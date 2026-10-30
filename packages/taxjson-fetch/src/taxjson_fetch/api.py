"""taxjson-fetch — the broker API clients behind `taxjson fetch`.

Part of the taxjson-fetch plugin (moved out of the taxjson core, which
holds no broker client and reads no broker credential).

Additive by design: each source downloads activity and writes a file
in a format an EXISTING parser already reads — no parser changes, no
new formats, and hand-exported CSVs keep working side by side.

Sources are declared on the account itself in taxjson.toml:

    [accounts.margin]
    type = "taxable"
    brokerage = "questrade"
    account = "12345678"           # Questrade account number

    [accounts.ibkr]
    type = "taxable"
    brokerage = "ibkr_flex"
    query_id = "123456"            # Flex query id (Activity, CSV,
                                   # "include section code and line
                                   # descriptor" ON)

Credentials never live in taxjson.toml:
  - Questrade: a refresh token, seeded from $QUESTRADE_REFRESH_TOKEN
    or --refresh-token. Questrade ROTATES the token on every use, so
    the current one is cached in ~/.questrade_token — shared
    machine-wide ($QUESTRADE_TOKEN_FILE overrides), because
    Questrade runs ONE rotating chain per API app
    (work/ is gitignored) and rewritten after each fetch.
  - IBKR Flex: $IBKR_FLEX_TOKEN (the Flex Web Service token).

Networking is stdlib urllib with explicit timeouts — no new runtime
dependencies. All HTTP goes through an injectable `http_get` so tests
run offline.
"""
import csv
import io
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

PROG = "taxjson-fetch"

_QT_LOGIN = "https://login.questrade.com/oauth2/token"
_FLEX_BASE = ("https://ndcdyn.interactivebrokers.com/AccountManagement/"
              "FlexWebService")
_TIMEOUT = 30
# Questrade caps the activities window at 31 days per request; stay
# comfortably under it whichever way they count the boundary days.
_QT_CHUNK_DAYS = 27


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects on credentialed requests: CPython forwards the
    Authorization header across 30x hops (only Content-* is stripped),
    so a redirect to another host or to http:// would leak the bearer
    (2026-09 security audit). Broker APIs never redirect in normal
    operation; a redirect is an anomaly worth failing loudly on."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Query strings stripped: a redirect that keeps the path and
        # query (an ordinary http->https or host-migration 30x) carries
        # the Flex `t=` token / the Questrade `refresh_token=`, and this
        # message reaches stderr (S031-14) — same redaction as every
        # other error path in this module.
        raise urllib.error.HTTPError(
            req.full_url.split('?')[0], code,
            f"refusing to follow a redirect to "
            f"{str(newurl).split('?')[0]!r} on a credentialed request",
            headers, fp)


_opener = urllib.request.build_opener(_NoRedirect())


def default_http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "taxjson"})
    with _opener.open(req, timeout=_TIMEOUT) as r:
        return r.read()


_DETAIL_MAX = 300
_BODY_READ_MAX = 65536


def scrub_detail(text: str, secrets: Any = ()) -> str:
    """Text from a broker or proxy (an HTTP reason or body, an XML
    ErrorMessage, a URLError) made safe to print: every value in
    `secrets` (the tokens and account numbers of the request, as typed
    and URL-quoted) becomes ***, then any URL's query string, any
    token=/"token": value, any Bearer credential, an /accounts/<n>/
    path segment and any account-shaped id (7+ digits, or a letter or
    two and 6+ digits: IB's U1234567, a Questrade number) are masked
    (GitHub #81 — the request path was masked but an echoed request URL
    or account id in the body was shown as it came)."""
    import re as _re
    text = str(text or "")
    forms = set()
    for v in secrets or ():
        v = str(v or "")
        if len(v) >= 4:
            forms.update({v, urllib.parse.quote(v, safe=""),
                          urllib.parse.quote_plus(v)})
    for f in sorted(forms, key=len, reverse=True):
        text = text.replace(f, "***")
    text = _re.sub(r"(?i)\b(https?://[^\s?#\"'<>]*)\?[^\s\"'<>]*",
                   r"\1?<query hidden>", text)
    text = _re.sub(r"(?i)(?<![A-Za-z0-9_])((?:refresh_|access_|id_)?token|"
                   r"t|password|secret|api_?key)=[^&\s\"'<>]+",
                   r"\1=***", text)
    text = _re.sub(r"(?i)(\"(?:(?:refresh_|access_|id_)?token|password|"
                   r"secret|api_?key)\"\s*:\s*\")[^\"]*\"", r'\1***"', text)
    text = _re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9\-._~+/]+=*",
                   r"\1 ***", text)
    text = _re.sub(r"(/accounts/)([^/?#\s\"'<>&]+)",
                   lambda m: m.group(1) + mask_account_number(m.group(2)),
                   text)
    return _re.sub(r"(?<![A-Za-z0-9])([A-Za-z]{1,2}\d{6,}|\d{7,})"
                   r"(?![A-Za-z0-9])",
                   lambda m: mask_account_number(m.group(1)), text)


def _http_error_detail(e: "urllib.error.HTTPError", secrets: Any = ()) -> str:
    """HTTP status plus the response BODY — Questrade and IB put the
    actual reason ({"code":..,"message":..} / an XML ErrorMessage) in
    the body, and swallowing it left only 'HTTP Error 400' to debug
    with. Reason and body go through scrub_detail BEFORE the cut, so a
    cut inside an echoed token cannot leave its head (GitHub #81)."""
    body = ""
    try:
        body = e.read(_BODY_READ_MAX).decode("utf-8", "replace").strip()
    except Exception:
        pass
    body = " ".join(scrub_detail(body, secrets).split())[:_DETAIL_MAX]
    reason = scrub_detail(e.reason, secrets)
    return f"HTTP {e.code} {reason}" + (f" — {body}" if body else "")


def _http_json(url: str, http_get: Callable[[str], bytes],
               secrets: Any = ()) -> Any:
    try:
        raw = http_get(url)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"request failed: {_http_error_detail(e, secrets)}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(
            f"request failed: {scrub_detail(e, secrets)}") from e
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError(f"non-JSON response from {url.split('?')[0]}: "
                           f"{e}") from e
    # Every caller reads named fields: a JSON array or scalar from a
    # broken upstream or proxy was an AttributeError traceback, not the
    # one-line error the fetch command catches (finding 82).
    if not isinstance(doc, dict):
        raise RuntimeError(f"unexpected response from {url.split('?')[0]}: "
                           f"a JSON {type(doc).__name__}, not an object")
    return doc


# ------------------------------------------------------------ Questrade

def qt_refresh(refresh_token: str,
               http_get: Callable[[str], bytes]) -> Dict[str, str]:
    """Exchange the refresh token. Returns {api_server, access_token,
    refresh_token} — the NEW refresh token MUST be persisted (Questrade
    rotates it; reusing the old one fails the next fetch)."""
    url = (f"{_QT_LOGIN}?grant_type=refresh_token&refresh_token="
           f"{urllib.parse.quote(refresh_token)}")
    doc = _http_json(url, http_get, (refresh_token,))
    for k in ("api_server", "access_token", "refresh_token"):
        if not doc.get(k):
            raise RuntimeError(f"Questrade token response missing {k!r}"
                               f" — is the refresh token valid?")
    # The tokens go into an Authorization header and the token file: a
    # CR/LF (or anything a bearer token cannot hold) in a tampered or
    # garbled response was a urllib ValueError whose traceback printed
    # the whole header, token included (security review L4). Refuse it
    # in one line that never shows the token.
    for k in ("access_token", "refresh_token"):
        if not _token_ok(str(doc[k])):
            raise RuntimeError(
                f"Questrade login returned an {k.replace('_', ' ')} with "
                f"characters a token cannot hold (whitespace or control "
                f"characters); refusing to use it")
    _api = str(doc["api_server"])
    if not _api.lower().startswith("https://"):
        raise RuntimeError(
            f"Questrade login returned a non-HTTPS api_server "
            f"({_api!r}); refusing to send the access token over it.")
    if not qt_api_server_ok(_api):
        raise RuntimeError(
            f"Questrade login returned an api_server outside "
            f"questrade.com ({_api!r}); refusing to send the access "
            f"token to it.")
    return {"api_server": _api,
            "access_token": str(doc["access_token"]),
            "refresh_token": str(doc["refresh_token"])}


_TOKEN_RE = None


def _token_ok(token: str) -> bool:
    """An OAuth token as a bearer credential may carry it (RFC 6750
    b64token: letters, digits, - . _ ~ + / and trailing =) — nothing
    that could split or extend an HTTP header."""
    global _TOKEN_RE
    if _TOKEN_RE is None:
        import re as _re
        _TOKEN_RE = _re.compile(r"[A-Za-z0-9\-._~+/]+=*")
    return bool(_TOKEN_RE.fullmatch(token or ""))


def qt_account_segment(number: Any) -> str:
    """The Questrade account number from taxjson.toml as an API path
    segment: ASCII digits only, then percent-quoted (security review
    L2) — '../' or '?x=' in the config must never reshape the request.
    A bad value raises RuntimeError naming it masked."""
    n = str(number or "")
    if not (n.isascii() and n.isdigit()):
        raise RuntimeError(
            f"the Questrade account number must be digits only (got "
            f"{mask_account_number(n)!s}) — fix `account` under the "
            f"account's [accounts.<name>] table in taxjson.toml")
    return urllib.parse.quote(n, safe="")


def qt_api_server_ok(url: str) -> bool:
    """The bearer token may only go to https://*.questrade.com (default
    port, no userinfo) — the login response names the server, so a
    tampered or spoofed response must not be able to redirect the
    token anywhere else."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return False
    host = (u.hostname or "").lower().rstrip(".")
    return (u.scheme.lower() == "https" and not u.username
            and not u.password and port in (None, 443)
            and (host == "questrade.com" or host.endswith(".questrade.com")))


def mask_account_number(number: Any) -> str:
    """A broker account number as it may appear in output: first 2
    characters + *** (the IB extractor's rule) — progress lines and
    API errors printed the full Questrade number (S046-17)."""
    n = str(number or "")
    return (n[:2] + "***") if n else "?"


def _masked_path(path: str) -> str:
    """An API path with its /accounts/<number>/ segment masked."""
    import re as _re
    return _re.sub(r"(/accounts/)([^/?]+)",
                   lambda m: m.group(1) + mask_account_number(m.group(2)),
                   path)


def _qt_get(api_server: str, access_token: str, path: str,
            http_get: Callable[[str], bytes]) -> Any:
    if not qt_api_server_ok(api_server):
        raise RuntimeError(
            f"refusing to send the Questrade access token to "
            f"{api_server!r} (not https://*.questrade.com)")
    if not _token_ok(access_token):
        raise RuntimeError("the Questrade access token holds characters "
                           "a token cannot hold; refusing to send it")
    url = api_server.rstrip("/") + path
    import re as _re
    # What an error message must never show: the bearer token and the
    # account number(s) of the path (GitHub #81).
    secrets = (access_token,
               *_re.findall(r"/accounts/([^/?]+)", path))
    def _authed(u: str) -> bytes:
        req = urllib.request.Request(
            u, headers={"Authorization": f"Bearer {access_token}",
                        "User-Agent": "taxjson"})
        with _opener.open(req, timeout=_TIMEOUT) as r:
            return r.read()
    # Tests inject http_get that ignores auth; production wraps urllib
    # with the bearer header.
    getter = _authed if http_get is default_http_get else http_get
    try:
        raw = getter(url)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{_masked_path(path.split('?')[0])}: "
                           f"{_http_error_detail(e, secrets)}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(
            f"request failed: {scrub_detail(e, secrets)}") from e
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError(f"non-JSON response from "
                           f"{_masked_path(path.split('?')[0])}: {e}") from e
    if not isinstance(doc, dict):
        raise RuntimeError(f"unexpected response from "
                           f"{_masked_path(path.split('?')[0])}: a JSON "
                           f"{type(doc).__name__}, not an object")
    return doc


def _eastern_dst(local: datetime) -> bool:
    """True when an America/Toronto wall-clock time is in daylight time
    (second Sunday of March 02:00 to first Sunday of November 02:00, the
    rule since 2007) — the fallback when no tz database is installed."""
    def _sunday(month: int, nth: int) -> datetime:
        d = datetime(local.year, month, 1)
        d += timedelta(days=(6 - d.weekday()) % 7 + 7 * (nth - 1))
        return d.replace(hour=2)
    return _sunday(3, 2) <= local < _sunday(11, 1)


def _toronto_stamp(day: date, hms: str) -> str:
    """ISO stamp of `hms` on `day` in America/Toronto local time, with
    that moment's own UTC offset (-04:00 in summer, -05:00 in winter).
    A fixed -05:00 asked from 01:00 EDT on a summer day, so a --from
    window could miss its first day's midnight-stamped activities
    (audit A2-0599)."""
    local = datetime.fromisoformat(f"{day.isoformat()}T{hms}")
    try:
        from zoneinfo import ZoneInfo
        return local.replace(tzinfo=ZoneInfo("America/Toronto")).isoformat()
    except Exception:                       # no tz database installed
        off = "-04:00" if _eastern_dst(local) else "-05:00"
        return f"{local.isoformat()}{off}"


def qt_activities(session: Dict[str, str], number: str,
                  start: date, end: date,
                  http_get: Callable[[str], bytes]) -> List[Dict[str, Any]]:
    """All activities for the account number over [start, end], fetched
    in <=31-day chunks (the API's hard window cap)."""
    from collections import Counter
    seg = qt_account_segment(number)
    out: List[Dict[str, Any]] = []
    # Per activity identity, the MOST copies any earlier chunk held.
    seen_prev: Counter = Counter()
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=_QT_CHUNK_DAYS), end)
        qs = urllib.parse.urlencode({
            "startTime": _toronto_stamp(cur, "00:00:00"),
            "endTime": _toronto_stamp(chunk_end, "23:59:59")})
        path = f"/v1/accounts/{seg}/activities?{qs}"
        doc = _qt_get(session["api_server"], session["access_token"],
                      path, http_get)
        # Boundary-day activities come back in BOTH adjacent chunks
        # (a trade on the boundary date settles inside the next
        # window, and the server matches it in each) — drop repeats
        # of rows an EARLIER chunk already returned. Within-chunk
        # duplicates are kept: identical same-chunk rows are genuine
        # split fills, exactly as the manual export shows them. So a
        # row is a repeat only up to the number of copies an earlier
        # chunk held: when the first chunk had ONE copy and the next
        # has TWO, the second copy is a fill the first chunk did not
        # see, kept — the way _merge_csv_text keeps the max count per
        # side (audit 2026-10-24 A21: a set dropped both copies).
        # Identity by VALUE: a window that spelled 10 as 10.0 made the
        # boundary activity a second trade (backlog TJ-0735).
        keys = [json.dumps(_qt_identity(act), sort_keys=True, default=str)
                for act in _qt_activity_list(doc, path, session, seg)]
        skipped: Counter = Counter()
        for act, k in zip(doc["activities"], keys):
            if skipped[k] < seen_prev[k]:
                skipped[k] += 1
                continue
            out.append(act)
        for k, n in Counter(keys).items():
            seen_prev[k] = max(seen_prev[k], n)
        cur = chunk_end + timedelta(days=1)
    return out


def _qt_identity(value: Any) -> Any:
    """`value` (an API activity) with every JSON number as a float, for
    comparing two windows' copies of one activity: 10 and 10.0 are the
    same quantity (backlog TJ-0735). An integer a float cannot hold
    exactly, a bool and anything else are kept as they are."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return float(value) + 0.0 if abs(value) < 2 ** 53 else value
    if isinstance(value, float):
        return value + 0.0 if math.isfinite(value) else value
    if isinstance(value, dict):
        return {k: _qt_identity(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_qt_identity(v) for v in value]
    return value


def _qt_activity_list(doc: Dict[str, Any], path: str,
                      session: Dict[str, str],
                      seg: str) -> List[Dict[str, Any]]:
    """The `activities` list of one chunk's response, or a RuntimeError
    naming the (masked) request. An object WITHOUT that list — an
    upstream error passed through at HTTP 200, a changed payload — used
    to read as a successful EMPTY window: nothing added, and with
    --trim-overlap the manual export's rows inside the window were
    deleted as if the download now held them (audit 2026-10-24 B01).
    `null` is refused too: only a real list is a window's activities."""
    return _qt_response_list(doc, "activities", path, session, seg,
                             "the window was not downloaded; nothing "
                             "written, nothing trimmed")


def _qt_response_list(doc: Dict[str, Any], key: str, path: str,
                      session: Dict[str, str], seg: str,
                      consequence: str) -> List[Dict[str, Any]]:
    """`doc[key]` when it is a list, else a RuntimeError naming the
    (masked) request, what `key` was instead, the response's keys and
    its scrubbed `code` / `message`, and `consequence` (what did not
    happen). Shared by the activities and the positions endpoints: a
    positions response without a `positions` list became an EMPTY
    live-holdings snapshot at exit 0 (audit 2026-10-24 B01 follow-up)."""
    items = doc.get(key)
    if isinstance(items, list):
        return items
    where = _masked_path(path.split("?")[0])
    secrets = (session.get("access_token", ""), seg)
    what = (f"'{key}' is a JSON {type(items).__name__}, not a list"
            if key in doc else f"no '{key}' list")
    detail = "; ".join(
        f"{k}: {scrub_detail(str(doc[k]), secrets)[:200]}"
        for k in ("code", "message")
        if isinstance(doc.get(k), (str, int)) and str(doc[k]).strip())
    keys = ", ".join(sorted(str(k) for k in doc)) or "none"
    raise RuntimeError(
        f"unexpected response from {where}: {what} (keys: {keys}"
        f"{'; ' + detail if detail else ''}) — {consequence}. Retry "
        f"later or check the proxy.")


def _qt_date(iso: str) -> str:
    """API ISO timestamp -> the CSV export's date format
    ('%Y-%m-%d %I:%M:%S %p', the shape the Questrade parser reads)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")


_QT_COLUMNS = ("Transaction Date", "Settlement Date", "Action", "Symbol",
               "Description", "Quantity", "Price", "Gross Amount",
               "Commission", "Net Amount", "Currency", "Account #",
               "Activity Type", "Account Type")


def _money_cell(v: Any) -> Any:
    """A money cell for qt_to_csv: blank when the API omitted it."""
    return "" if v is None or (isinstance(v, str) and not v.strip()) else v


def qt_to_csv(activities: List[Dict[str, Any]], number: str) -> str:
    """Render API activities as a Questrade activity-export CSV — the
    exact column set the existing parser reads. Rows sort by trade
    date ONLY, a stable sort: rows of one day keep the API's order.
    Questrade stamps every row at midnight, so that order is the only
    record of a same-day sale before its rebuy (CA-DATE-14 /
    US-DATE-13); sorting on (date, symbol, action) put every Buy ahead
    of the Sell and moved gain into a later year (audit A2-0084). The
    same download still renders the same bytes."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_QT_COLUMNS)
    def _key(a):
        return str(a.get("tradeDate") or a.get("transactionDate") or "")[:10]
    for a in sorted(activities, key=_key):
        w.writerow([
            # tradeDate FIRST: the export's "Transaction Date" column
            # is the TRADE date, and the API's field of that name is
            # the POSTING date (= settlement) — mapping transactionDate
            # shifted every trade 1-2 days, so nothing deduped against
            # manually-exported rows and the books double-counted.
            _qt_date(str(a.get("tradeDate")
                         or a.get("transactionDate") or "")),
            # transactionDate is the posting (= settlement) date: with
            # no settlementDate it IS the settle date. A blank cell
            # made the parser add a second settlement lag on top of it
            # — a Dec-30 trade posted Dec 31 settled in January
            # (S031-10).
            _qt_date(str(a.get("settlementDate")
                         or a.get("transactionDate") or "")),
            a.get("action") or "",
            a.get("symbol") or "",
            # Collapse whitespace runs: the API pads descriptions
            # ("INC  CASH DIV  ON     500 SHS") where the manual
            # export single-spaces them, and description is part of
            # the row's content-hash id — without this, a manually
            # exported copy of the same activity never dedups and the
            # books double-count.
            " ".join(str(a.get("description") or "").split()),
            a.get("quantity") or 0,
            a.get("price") or 0,
            # Money fields: a MISSING or null value is written BLANK so
            # the strict parser refuses the row ("required Net Amount is
            # blank"). Written as 0 it was indistinguishable from a real
            # $0 row, and a dividend with no netAmount was dropped as a
            # zero-net informational row at exit 0 (R1-348).
            _money_cell(a.get("grossAmount")),
            _money_cell(a.get("commission")),
            _money_cell(a.get("netAmount")),
            a.get("currency") or "",
            number,
            a.get("type") or "",
            "API",
        ])
    return buf.getvalue()


# The money fields qt_to_csv writes BLANK when the API omits them (the
# strict parser then refuses the row, R1-348), with their CSV columns.
_QT_MONEY_FIELDS = (("grossAmount", "Gross Amount"),
                    ("commission", "Commission"),
                    ("netAmount", "Net Amount"))


# The cells qt_to_csv writes as `a.get(key) or 0`: absent reads as 0, so
# only one present and no number is refused (TJ-1073).
_QT_COUNT_FIELDS = (("quantity", "Quantity"), ("price", "Price"))


def _qt_parser_refuses(activity: Dict[str, Any]) -> bool:
    """Whether the strict Questrade parser refuses the row qt_to_csv
    renders for `activity`. Which cells it reads depends on the row's
    kind (an FX conversion none, a dividend no Settlement Date), so it is
    asked (backlog TJ-1231). Whatever else stops it counts as a refusal;
    what it says on the way is not this run's to show."""
    import contextlib
    import tempfile
    from pathlib import Path
    from taxjson.lib.brokerages.questrade import QuestradeBrokerage
    try:
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            path = Path(td) / "activity.csv"
            path.write_text(qt_to_csv([activity], "0"), encoding="utf-8")
            QuestradeBrokerage().parse_file(path)
    except (Exception, SystemExit):
        return True
    return False


def _qt_cash_reader_loses(activity: Dict[str, Any]) -> bool:
    """Whether the row qt_to_csv renders for `activity` is a cash row the
    FX-on-cash ledger's reader (questrade_cash_events) refuses, or skips
    only because its Net Amount or currency is missing. parse_file leaves
    these rows out unread, so it cannot speak for them (backlog TJ-1291).
    A real zero amount has no cash to lose. The stand-in values only ask
    whether the row would be an event when whole."""
    import contextlib
    import tempfile
    from pathlib import Path
    from taxjson.lib.brokerages.questrade import questrade_cash_events

    def events(row: Dict[str, Any]) -> List[Dict[str, Any]]:
        path = Path(td) / "activity.csv"
        path.write_text(qt_to_csv([row], "0"), encoding="utf-8")
        return questrade_cash_events(path)
    try:
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            if events(activity):
                return False
            net = _finite(activity.get("netAmount"))
            return bool(events({
                **activity,
                "netAmount": 1 if net is None else net,
                "currency": str(activity.get("currency") or "").strip()
                or "CAD"}))
    except (Exception, SystemExit):
        return True


def qt_unusable_activities(activities: List[Dict[str, Any]]) -> List[str]:
    """One line per activity qt_to_csv renders as a row the strict
    Questrade parser refuses: no trade or transaction date or one it
    cannot read (a settlement date too), no currency, a money field
    missing, null, blank or not a number, or a quantity or price that is
    no number, when the parser reads that cell on such a row
    (_qt_parser_refuses), or as a cash row the FX-on-cash ledger cannot
    use (_qt_cash_reader_loses). --trim-overlap retires a manual export's
    rows on the strength of the download, so it must not trust a download
    holding such a row: a null netAmount trimmed the manual purchase and
    left only a fetched row `taxjson run` refuses (backlog TJ-0868), and
    a null currency or an unreadable date did the same (TJ-1073)."""
    from taxjson.lib.brokerages.base import BaseBrokerage
    from taxjson.lib.brokerages.questrade import _DATE_FMTS

    def readable(raw: Any) -> bool:
        return BaseBrokerage.parse_date(_qt_date(str(raw)),
                                        *_DATE_FMTS) is not None
    out: List[str] = []
    for a in activities:
        if not isinstance(a, dict):
            out.append(f"an item that is a JSON {type(a).__name__}, not "
                       f"an activity")
            continue
        day = str(a.get("tradeDate") or a.get("transactionDate")
                  or "")[:10].strip()
        bad = [] if day else ["Transaction Date"]
        bad += [col for key, col in _QT_MONEY_FIELDS
                if _money_cell(a.get(key)) == ""
                or _finite(a.get(key)) is None]
        if not str(a.get("currency") or "").strip():
            bad.append("Currency")
        # A present cell the parser cannot read (the value is not shown:
        # it goes on to the console).
        unread = []
        trade = a.get("tradeDate") or a.get("transactionDate")
        if day and not readable(trade):
            unread.append("Transaction Date")
        settle = a.get("settlementDate") or a.get("transactionDate")
        if settle and str(settle).strip() and not readable(settle):
            unread.append("Settlement Date")
        unread += [col for key, col in _QT_COUNT_FIELDS
                   if a.get(key) and _finite(a.get(key)) is None]
        if (bad or unread) and (_qt_parser_refuses(a)
                                or _qt_cash_reader_loses(a)):
            what = " ".join(str(x) for x in (
                day or "undated", a.get("type") or "activity",
                a.get("symbol") or "") if x)
            said = ([f"no {', '.join(bad)}"] if bad else []) + (
                [f"unreadable {', '.join(unread)}"] if unread else [])
            out.append(f"{what} ({'; '.join(said)})")
    return out


def qt_positions(session: Dict[str, str], number: str,
                 http_get: Callable[[str], bytes]) -> List[Dict[str, Any]]:
    """Live open positions for the account — symbol, openQuantity,
    averageEntryPrice, currentPrice, currentMarketValue. A response
    without a `positions` list is refused (_qt_response_list): read as
    no positions, it wrote an EMPTY live-holdings snapshot at exit 0,
    and `taxjson sanity` then compared the books with nothing (audit
    2026-10-24 B01 follow-up). An explicit empty list is an empty
    account."""
    seg = qt_account_segment(number)
    path = f"/v1/accounts/{seg}/positions"
    doc = _qt_get(session["api_server"], session["access_token"],
                  path, http_get)
    items = _qt_response_list(doc, "positions", path, session, seg,
                              "no live-holdings snapshot was written")
    qt_check_positions(items)
    return items


def _symbol_id(position: Dict[str, Any]) -> Optional[int]:
    """A position's Questrade `symbolId` (a positive integer), else None."""
    sid = position.get("symbolId")
    if isinstance(sid, bool):
        return None
    if isinstance(sid, str) and sid.strip().isdigit():
        sid = int(sid.strip())
    return sid if isinstance(sid, int) and sid > 0 else None


def _declared_size(sym: str) -> Optional[float]:
    """The contract size a ticker.map `MULT` line declares for the
    option `sym` (the exact symbol, its root, then its underlying), the
    way core.equity_option_size reads it — ahead of any size the books
    or Questrade state. None when no line names it. The live snapshot
    read only the books' and the lookup's sizes, so a mini contract
    declared `MULT … 10` was costed at 100 shares (backlog TJ-0857)."""
    from taxjson.lib import markets
    from taxjson.lib.core import parse_option_underlying
    und = str(parse_option_underlying(sym) or "")
    root = und.split(".", 1)[0]
    size = _finite(markets.contract_size(sym, root)
                   or markets.contract_size(und))
    return size if size is not None and size > 0 else None


def qt_unbooked_option_ids(positions: List[Dict[str, Any]],
                           book_symbols=frozenset(),
                           closed_symbols=frozenset()) -> List[int]:
    """The symbolIds of the open option positions whose contract the
    books (`book_symbols`, upper case) do not hold under any suffix:
    the ones whose contract size only Questrade's symbol lookup can
    give (qt_contract_sizes; backlog TJ-0614). One they hold only closed
    (`closed_symbols`) too: the listing open now may be another one
    (backlog TJ-1235)."""
    from taxjson.lib.core import is_option_symbol
    book = frozenset(str(b).upper() for b in book_symbols)
    out: List[int] = []
    for p in positions:
        if not isinstance(p, dict) or not _open_qty(p):
            continue
        sym = qt_position_symbol(str(p.get("symbol") or ""), frozenset(),
                                 book)
        sid = _symbol_id(p)
        if is_option_symbol(sym) and (
                sym.upper() not in book
                or _qt_closed_listing(sym, book, closed_symbols)) \
                and sid is not None and _declared_size(sym) is None:
            out.append(sid)
    return out


def qt_contract_sizes(session: Dict[str, str], symbol_ids,
                      http_get: Callable[[str], bytes]
                      ) -> Dict[int, Optional[float]]:
    """The contract size Questrade's symbol lookup (GET
    /v1/symbols?ids=...) states for each option `symbol_ids` names:
    {symbolId: its one underlying's `multiplier`
    (optionContractDeliverables), or None when the deliverable is not
    one underlying's shares alone (cash in lieu, a second underlying:
    an adjusted contract, no size in shares)}. An id the response does
    not answer is left out (its size is not known). The positions
    payload carries no contract size, so a mini contract the books did
    not hold yet was costed at 100 shares (backlog TJ-0614). A
    malformed response is a RuntimeError.

    `multiplier` is the number of shares delivered, which is the
    contract size only for a contract no corporate action adjusted: a
    contract adjusted for a 1-for-10 reverse split delivers 10 shares
    but its premium is still paid on 100 (backlog TJ-0670). So a
    deliverable other than the standard 100 is the size only when the
    contract's root (`optionRoot`, else read from its `symbol`) is its
    underlying's symbol; an adjusted root (SYN1 on SYN), or none to
    compare, leaves it None: not known."""
    ids = sorted({int(i) for i in symbol_ids
                  if isinstance(i, int) and not isinstance(i, bool)
                  and i > 0})
    out: Dict[int, Optional[float]] = {}
    for k in range(0, len(ids), 100):
        chunk = ids[k:k + 100]
        path = "/v1/symbols?" + urllib.parse.urlencode(
            {"ids": ",".join(str(i) for i in chunk)})
        doc = _qt_get(session["api_server"], session["access_token"],
                      path, http_get)
        items = _qt_response_list(doc, "symbols", path, session, "",
                                  "no contract size was read")
        for it in items:
            if not isinstance(it, dict):
                continue
            sid = _symbol_id(it)
            if sid not in chunk:
                continue
            dlv = it.get("optionContractDeliverables")
            und = dlv.get("underlyings") if isinstance(dlv, dict) else None
            if not isinstance(und, list) or not und:
                continue                # not stated: not known
            cil = _finite(dlv.get("cashInLieu")) or 0.0
            mult = (_finite(und[0].get("multiplier"))
                    if isinstance(und[0], dict) else None)
            if (len(und) != 1 or cil or mult is None or mult <= 0
                    or (mult != 100
                        and not _qt_unadjusted(it, und[0]))):
                mult = None
            out[sid] = mult
    return out


def _qt_both_listings(sym: str, book_symbols) -> bool:
    """Whether the option `sym` (taxjson spelling) is a contract the
    books hold under BOTH listings: Montreal (.TO) and US (.US)."""
    base, dot, _ext = str(sym).upper().rpartition(".")
    return bool(dot) and (f"{base}.TO" in book_symbols
                          and f"{base}.US" in book_symbols)


def _qt_unbooked_listing(sym: str, book_symbols) -> bool:
    """Whether the books hold the option `sym` under neither listing."""
    base, dot, _ext = str(sym).upper().rpartition(".")
    return bool(dot) and (f"{base}.TO" not in book_symbols
                          and f"{base}.US" not in book_symbols)


def _qt_closed_listing(sym: str, book_symbols, closed_symbols) -> bool:
    """Whether the books hold the option `sym` under one listing only,
    and closed (`closed_symbols`): that says nothing about the listing
    open now (backlog TJ-1235)."""
    base, dot, _ext = str(sym).upper().rpartition(".")
    held = {f"{base}.TO", f"{base}.US"} & set(book_symbols)
    return bool(dot) and len(held) == 1 and held <= set(closed_symbols)


def _qt_option_root(sym: str) -> str:
    """The root of the option `sym` (taxjson spelling), '' when it reads
    as none."""
    from taxjson.lib.core import parse_option_underlying
    try:
        return str(parse_option_underlying(sym) or "").rsplit(".", 1)[0]
    except Exception:
        return ""


def _qt_book_to_roots(book_symbols) -> set:
    """The roots of the .TO options the books (upper case) ever held,
    open or closed."""
    from taxjson.lib.core import is_option_symbol
    return {r for r in (_qt_option_root(b) for b in book_symbols
                        if b.endswith(".TO") and is_option_symbol(b)) if r}


def _qt_payload_to_roots(positions: List[Dict[str, Any]]) -> frozenset:
    """The roots of the .TO listings in the live payload."""
    return frozenset(
        str(p.get("symbol") or "").rsplit(".", 1)[0]
        for p in positions
        if isinstance(p, dict) and str(p.get("symbol") or "").endswith(".TO"))


def qt_ambiguous_option_ids(positions: List[Dict[str, Any]],
                            book_symbols=frozenset(),
                            closed_symbols=frozenset()) -> List[int]:
    """The symbolIds of the open option positions whose listing only
    Questrade's symbol lookup can settle (qt_option_currencies): a
    contract the books (`book_symbols`, upper case) hold under both
    listings, .TO and .US — a series traded and closed on one listing
    while the other is held (backlog TJ-0880) — and a contract they hold
    under neither, of a root a .TO listing teaches as Montreal-listed: a
    root trades on both exchanges, and one closed Montreal option or a
    CDR says nothing about this contract (backlog TJ-1074, TJ-1108); and
    a contract they hold on one listing only closed (`closed_symbols`,
    backlog TJ-1235)."""
    from taxjson.lib.core import is_option_symbol
    book = frozenset(str(b).upper() for b in book_symbols)
    taught = _qt_book_to_roots(book) | _qt_payload_to_roots(positions)
    out: List[int] = []
    for p in positions:
        if not isinstance(p, dict) or not _open_qty(p):
            continue
        sym = qt_position_symbol(str(p.get("symbol") or ""), frozenset(),
                                 frozenset())
        sid = _symbol_id(p)
        if not is_option_symbol(sym) or sid is None:
            continue
        if _qt_both_listings(sym, book) or (
                _qt_unbooked_listing(sym, book)
                and _qt_option_root(sym) in taught) or _qt_closed_listing(
                    sym, book, closed_symbols):
            out.append(sid)
    return out


def qt_option_currencies(session: Dict[str, str], symbol_ids,
                         http_get: Callable[[str], bytes]) -> Dict[int, str]:
    """The currency Questrade's symbol lookup (GET /v1/symbols?ids=...)
    states for each of `symbol_ids`: {symbolId: 'CAD' or 'USD'} — a
    Montreal-listed option trades in CAD, a US-listed one in USD. An id
    the response does not answer, or answers with another currency, is
    left out (not known). A malformed response is a RuntimeError."""
    ids = sorted({int(i) for i in symbol_ids
                  if isinstance(i, int) and not isinstance(i, bool)
                  and i > 0})
    out: Dict[int, str] = {}
    for k in range(0, len(ids), 100):
        chunk = ids[k:k + 100]
        path = "/v1/symbols?" + urllib.parse.urlencode(
            {"ids": ",".join(str(i) for i in chunk)})
        doc = _qt_get(session["api_server"], session["access_token"],
                      path, http_get)
        items = _qt_response_list(doc, "symbols", path, session, "",
                                  "no listing was read")
        for it in items:
            if not isinstance(it, dict):
                continue
            sid = _symbol_id(it)
            cur = str(it.get("currency") or "").strip().upper()
            if sid in chunk and cur in ("CAD", "USD"):
                out[sid] = cur
    return out


def _qt_unadjusted(item: Dict[str, Any], underlying: Dict[str, Any]) -> bool:
    """True when symbol-lookup `item` (an option) is a contract no
    corporate action adjusted: its root (`optionRoot`, else the root of
    its Questrade `symbol`) is its one `underlying`'s symbol, a listing
    suffix aside. An adjusted contract takes a root of its own (SYN1 on
    SYN). False when either is not stated (qt_contract_sizes)."""
    from taxjson.lib.markets import canadian_suffixes
    root = item.get("optionRoot")
    if not isinstance(root, str) or not root.strip():
        m = _qt_opt_re().match(str(item.get("symbol") or "").strip())
        root = m.group(1) if m else ""
    name = underlying.get("underlyingSymbol")
    if not isinstance(name, str):
        return False
    name = name.strip().upper()
    base, dot, ext = name.rpartition(".")
    if dot and (ext in canadian_suffixes() or ext == "US"):
        name = base
    root = root.strip().upper()
    return bool(root) and root == name


def _finite(value: Any) -> Optional[float]:
    """`value` as a finite float, else None (a bool, None, text that is
    no number, NaN, an infinity)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _open_qty(position: Dict[str, Any]) -> float:
    """A checked position's openQuantity; 0.0 for a closed one."""
    qty = _finite(position.get("openQuantity")) or 0.0
    return qty if abs(qty) >= 1e-12 else 0.0


def qt_check_positions(positions: Any) -> None:
    """Every item of a Questrade positions response is a position with a
    symbol and a finite openQuantity (an explicit 0 is a closed
    position), and its averageEntryPrice / currentMarketValue, when
    given, are finite numbers. Else a RuntimeError: an item without
    openQuantity was read as 0 and skipped, and the live-holdings
    snapshot of an account holding 10 shares was replaced by an EMPTY
    one at exit 0 (backlog TJ-0513)."""
    if not isinstance(positions, list):
        raise RuntimeError("the positions response is not a list — no "
                           "live-holdings snapshot was written")
    for i, p in enumerate(positions, 1):
        if not isinstance(p, dict):
            bad = f"is a JSON {type(p).__name__}, not a position"
        else:
            sym = p.get("symbol")
            bad = ""
            if not isinstance(sym, str) or not sym.strip():
                bad = f"has no symbol ({sym!r})"
            elif "openQuantity" not in p:
                bad = f"({sym}) has no openQuantity"
            elif _finite(p.get("openQuantity")) is None:
                bad = (f"({sym}) openQuantity {p.get('openQuantity')!r} "
                       f"is not a finite number")
            else:
                for key in ("averageEntryPrice", "currentMarketValue"):
                    if p.get(key) is not None \
                            and _finite(p.get(key)) is None:
                        bad = (f"({sym}) {key} {p.get(key)!r} is not a "
                               f"finite number")
                        break
        if bad:
            raise RuntimeError(
                f"Questrade positions item {i} {bad} — a malformed "
                f"response; no live-holdings snapshot was written (the "
                f"previous one is kept). Retry later.")


_QT_OPT_RE = None  # compiled lazily (_qt_opt_re)


def _qt_opt_re():
    """A Questrade option symbol (SYN20Jan26C88.00): root, day, month,
    year, right, strike."""
    global _QT_OPT_RE
    if _QT_OPT_RE is None:
        import re as _re
        # The root may carry a class share's letter(s) (SYN.B, REI.UN),
        # as the broker parsers' option roots do (backlog TJ-0515).
        _QT_OPT_RE = _re.compile(
            r"^([A-Z][A-Z0-9]*(?:\.[A-Z]{1,2})?)(\d{2})([A-Z][a-z]{2})"
            r"(\d{2})([CP])(\d+(?:\.\d+)?)$")
    return _QT_OPT_RE


_MONTHS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
           "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
           "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}


def qt_position_symbol(sym: str, to_roots=frozenset(),
                       book_symbols=frozenset()) -> str:
    """Questrade position symbol -> taxjson convention. Equities:
    Canadian venues ('.TO', '.VN' TSX Venture, '.CN', '.NE') all map
    to '.TO' with a dotted preferred series, as the parsers do; bare
    symbols are US listings ('.US'), and a trailing CLASS letter
    ('SAMPLC.B') is not an exchange — it takes '.US' too. Options ('SAMPLA20Jan26C88.00')
    become OCC ('SAMPLA260120C00088000.TO') — suffixed .TO when the
    root's equity also appears in this payload as a .TO listing
    (Montréal-listed options on Canadian names), else .US.
    When the account's BOOKS already hold the exact contract
    (`book_symbols`), their suffix wins — the parsers suffix a contract
    by its listing currency, and a same-root .TO equity (a CDR such as
    SAMPLB.TO) must not turn a US option Montreal-listed (S031-12).
    Unrecognized shapes pass through untouched so a mismatch stays
    VISIBLE in the sanity diff instead of being mangled."""
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    sym = (sym or "").strip()
    m = _qt_opt_re().match(sym)
    if m:
        root, dd, mon, yy, right, strike = m.groups()
        mm = _MONTHS.get(mon)
        if mm:
            from taxjson.lib.brokerages.base import (BrokerageParseError,
                                                     encode_occ_strike)
            try:
                date(2000 + int(yy), int(mm), int(dd))
                occ_strike = encode_occ_strike(strike)
            except (ValueError, BrokerageParseError):
                # No real contract (February 30, a strike finer than
                # 0.001): kept as sent, a VISIBLE mismatch.
                return sym
            occ = f"{root}{yy}{mm}{dd}{right}{occ_strike}"
            if f"{occ}.TO" in book_symbols:
                return f"{occ}.TO"
            if f"{occ}.US" in book_symbols:
                return f"{occ}.US"
            return occ + (".TO" if root in to_roots else ".US")
    if "." not in sym:
        return f"{sym}.US"
    base, _, ext = sym.rpartition(".")
    from taxjson.lib.markets import canadian_suffixes
    if ext.upper() in canadian_suffixes():
        # One spelling per Canadian listing, the parsers' own
        # (base.canonical_ca_listing): ABC.VN / CCC.CN -> ABC.TO /
        # CCC.TO, FTN.PRA.TO -> FTN.PR.A.TO. A different spelling here
        # than in the parsed books is a phantom verify mismatch.
        from taxjson.lib.brokerages.base import canonical_ca_listing
        return canonical_ca_listing(sym, "CAD") or sym
    if ext.upper() not in KNOWN_SUFFIXES:
        # A class share, not an exchange: 'SAMPLC.B' is the US listing
        # SAMPLC.B.US. Passing it through unsuffixed made every live
        # class-share position look like a phantom mismatch in the
        # verify diff.
        return f"{sym}.US"
    return sym


def positions_to_holdings_toml(positions: List[Dict[str, Any]],
                               account: str, number: str,
                               generated_at: str,
                               extra_to_roots=frozenset(),
                               book_symbols=frozenset(),
                               book_multipliers=None,
                               live_sizes=None,
                               assumed: Optional[List[str]] = None,
                               live_currencies=None,
                               ambiguous: Optional[List[str]] = None,
                               ambiguous_unbooked: Optional[List[str]] = None,
                               closed_symbols=frozenset(),
                               ambiguous_closed: Optional[List[str]] = None
                               ) -> str:
    """Render live positions as the holdings TOML that
    `taxjson sanity` reads ([[holding]] symbol/quantity; extra fields
    are informational). The positions are checked first
    (qt_check_positions: a RuntimeError, nothing rendered).
    A ticker.map `MULT` line for an option is its contract size, ahead
    of every other (_declared_size; backlog TJ-0857).
    `book_multipliers` ({book symbol: contract size, or None when the
    books disagree}): an option the books hold under that exact symbol
    gets its contract size (`multiplier`), or
    `contract_multiplier_unknown = true` — the average price is per
    share, and a mini contract's cost was read at the standard 100
    (backlog TJ-0514). An option the books do not hold (opened since
    the last run) takes the size Questrade's symbol lookup states
    (`live_sizes`, qt_contract_sizes: {symbolId: size, or None for a
    deliverable that is not shares alone: unknown}); with none, the
    standard 100 is ASSUMED, said in a comment of the holding and
    listed in `assumed` for the caller to say (backlog TJ-0614).
    An option the books hold under BOTH listings (.TO and .US) takes
    the listing of the currency Questrade's symbol lookup states
    (`live_currencies`, qt_option_currencies: {symbolId: 'CAD' or
    'USD'}); with none it is written with no currency, said in a
    comment and listed in `ambiguous` — the books' history cannot say
    which listing is open, and a closed Canadian contract made the open
    US one .TO, costed in CAD (backlog TJ-0880). An option the books hold
    under neither listing takes that currency's listing too (TJ-1074,
    TJ-1108); with none, on a root only the books' .TO options teach, it
    keeps the .TO spelling with no currency, said in a comment and listed
    in `ambiguous_unbooked`. So does one they hold on one listing only
    closed (`closed_symbols`): with no currency stated it keeps the books'
    spelling with no currency, listed in `ambiguous_closed` (TJ-1235)."""
    qt_check_positions(positions)
    live_sizes = dict(live_sizes or {})
    live_currencies = dict(live_currencies or {})
    book_multipliers = {str(k).upper(): v
                        for k, v in (book_multipliers or {}).items()}
    # Roots whose options list in Canada (Montreal): learned from .TO
    # EQUITY positions in this payload, and from Questrade's own option
    # symbols when the option's underlying root also appears as a .TO
    # position anywhere. A cash-secured put with no equity leg in the
    # SAME payload previously suffixed .US live vs .TO in the books —
    # a phantom verify mismatch every run (2026-09 audit). The best
    # remaining signal for the no-equity-anywhere case is the option
    # description; Questrade names Montreal contracts on the TSX line.
    # `book_symbols` (the account's parsed book, upper-case): an exact
    # contract there decides its suffix (qt_position_symbol), and only
    # its .TO OPTIONS teach Montreal roots — a .TO equity in the books
    # may be a CDR over a US name.
    book_symbols = frozenset(str(b).upper() for b in book_symbols)
    closed_symbols = frozenset(str(b).upper() for b in closed_symbols)
    from taxjson.lib.core import is_option_symbol
    book_to_roots = _qt_book_to_roots(book_symbols)
    live_to_roots = (_qt_payload_to_roots(positions)
                     | frozenset(extra_to_roots))
    to_roots = live_to_roots | frozenset(book_to_roots)
    q = toml_str
    lines = ["# Live Questrade holdings snapshot — taxjson fetch/verify.",
             "# Regenerated on every fetch; do not hand-edit.",
             "[meta]",
             f"account = {q(account)}",
             f"broker_account = {q(number)}",
             'source = "questrade-api"',
             f"generated_at = {q(generated_at)}",
             f"holdings_count = "
             f"{sum(1 for p in positions if _open_qty(p))}"]
    for p2 in sorted(positions,
                     key=lambda x: str(x.get("symbol") or "")):
        qty = _open_qty(p2)
        if not qty:
            continue
        sym = qt_position_symbol(str(p2.get("symbol") or ""), to_roots,
                                 book_symbols)
        venue_unknown = False
        unbooked_unknown = False
        closed_unknown = False
        closed_listing = _qt_closed_listing(sym, book_symbols, closed_symbols)
        if is_option_symbol(sym) and (
                _qt_both_listings(sym, book_symbols)
                or _qt_unbooked_listing(sym, book_symbols)
                or closed_listing):
            # backlog TJ-0880, TJ-1074: the listing Questrade says.
            listed = live_currencies.get(_symbol_id(p2))
            base = sym.rpartition(".")[0]
            if listed == "CAD":
                sym = f"{base}.TO"
            elif listed == "USD":
                sym = f"{base}.US"
            elif _qt_both_listings(sym, book_symbols):
                venue_unknown = True
            elif closed_listing:
                venue_unknown = closed_unknown = True
            elif (sym.endswith(".TO")
                  and _qt_option_root(sym) not in live_to_roots):
                # Only the books' other .TO options say Montreal.
                venue_unknown = unbooked_unknown = True
        lines += ["", "[[holding]]",
                  f"symbol = {q(sym)}",
                  f"quantity = {qty!r}"]
        if closed_unknown:
            lines.append("# listing AMBIGUOUS: the books hold this contract "
                         "only closed, and Questrade's symbol lookup did not "
                         "say which listing is open: no currency written")
            if ambiguous_closed is not None:
                ambiguous_closed.append(sym)
        elif unbooked_unknown:
            lines.append("# listing AMBIGUOUS: this contract is not in the "
                         "books yet, the books hold other options of its "
                         "root Montreal-listed (.TO), and Questrade's symbol "
                         "lookup did not say which it is: no currency "
                         "written")
            if ambiguous_unbooked is not None:
                ambiguous_unbooked.append(sym)
        elif venue_unknown:
            lines.append("# listing AMBIGUOUS: the books hold this contract "
                         "both Montreal-listed (.TO) and US-listed (.US), "
                         "and Questrade's symbol lookup did not say which "
                         "is open: no currency written")
            if ambiguous is not None:
                ambiguous.append(sym)
        declared = _declared_size(sym) if is_option_symbol(sym) else None
        if declared is not None:
            # A ticker.map MULT line wins, as in the books (TJ-0857).
            lines.append(f"multiplier = {float(declared)!r}")
        elif is_option_symbol(sym) and sym.upper() in book_multipliers:
            mult = book_multipliers[sym.upper()]
            lines.append("contract_multiplier_unknown = true"
                         if mult is None else
                         f"multiplier = {float(mult)!r}")
        elif is_option_symbol(sym) and sym.upper() not in book_symbols:
            # Not in the books yet: Questrade's own size, else the
            # standard one ASSUMED and said (backlog TJ-0614).
            sid = _symbol_id(p2)
            if sid is not None and sid in live_sizes:
                mult = live_sizes[sid]
                lines.append("contract_multiplier_unknown = true"
                             if mult is None else
                             f"multiplier = {float(mult)!r}")
            else:
                lines.append("# contract size ASSUMED: 100 shares (not in "
                             "the books yet, and Questrade's symbol "
                             "lookup gave none)")
                if assumed is not None:
                    assumed.append(sym)
        # The currency of the average price, from the listing's suffix
        # (Questrade's payload carries none): without it `taxjson
        # opening` skipped every holding ("no currency for its cost")
        # and sanity compared a USD average with CAD books (2026-10
        # audit #43). A spelling that does not say is left out.
        from taxjson.lib.price_chain import quote_currency
        cur = None if venue_unknown else quote_currency(sym)
        if cur:
            lines.append(f"currency = {q(cur)}")
        aep = p2.get("averageEntryPrice")
        if aep is not None:
            lines.append(f"average_entry_price = {float(aep)!r}")
        cmv = p2.get("currentMarketValue")
        if cmv is not None:
            lines.append(f"market_value = {float(cmv)!r}")
    return "\n".join(lines) + "\n"


def toml_str(value: Any) -> str:
    """A TOML basic string: quotes, backslashes and every control
    character (incl. DEL) escaped — a broker-supplied symbol holding a
    quote and a newline must not be able to inject keys or tables."""
    out = []
    for ch in str(value):
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def write_private(path: Any, text: str) -> None:
    """Write a fetched statement / snapshot atomically as 0600 inside a
    0700 directory. mkdir(mode=) does not tighten a directory that
    already exists, so the chmod is explicit. The text is staged in a
    temp file of this write's OWN (core safe_write.write_atomic: a new
    owner-only `<name>.<random>.part`, never through a symlink) and
    renamed over `path`: two overlapping writers each publish only
    their own complete file. One fixed `<name>.part` let a second
    writer replace the first one's temp, so the first rename published
    the second's unfinished file (GitHub issue #13)."""
    import os
    from pathlib import Path as _P
    from taxjson.lib.safe_write import write_atomic
    path = _P(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    write_atomic(path, text, newline="")


def activity_type_counts(activities: List[Dict[str, Any]]) -> Dict[str, int]:
    """{activity type: count} for the download summary — a glance at
    'Trades 14, Dividends 6, FXT 2' catches a mis-scoped window or a
    filtered export faster than any total."""
    out: Dict[str, int] = {}
    for a in activities:
        t = str(a.get("type") or "?").strip() or "?"
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


# ------------------------------------------------------------ IBKR Flex

def flex_fetch(token: str, query_id: str,
               http_get: Callable[[str], bytes],
               sleep: Callable[[float], None] = time.sleep,
               max_wait: int = 120) -> bytes:
    """Two-step Flex Web Service: SendRequest -> reference code, then
    poll GetStatement until the report is generated."""
    def _get(url: str) -> bytes:
        # Same wrapping as the Questrade path: a DNS/proxy/HTTP
        # failure must be a clean RuntimeError (cmd_fetch catches
        # those), not a raw urllib traceback.
        try:
            return http_get(url)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Flex request failed: {_http_error_detail(e, (token,))}"
            ) from e
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(
                f"Flex request failed: {scrub_detail(e, (token,))}") from e

    qs = urllib.parse.urlencode({"t": token, "q": query_id, "v": "3"})
    first = _get(f"{_FLEX_BASE}/SendRequest?{qs}").decode(
        "utf-8", "replace")
    code = _flex_field(first, "ReferenceCode")
    if not code:
        err = _flex_field(first, "ErrorMessage") or first
        raise RuntimeError(f"Flex SendRequest failed: "
                           f"{_flex_err(err, token)}")
    qs2 = urllib.parse.urlencode({"t": token, "q": code, "v": "3"})
    waited = 0
    while True:
        raw = _get(f"{_FLEX_BASE}/GetStatement?{qs2}")
        head = raw[:2000].decode("utf-8", "replace")
        if "<FlexStatementResponse" in head and "ErrorCode" in head:
            # 1019 = statement generation in progress — poll.
            in_progress = ("<ErrorCode>1019</ErrorCode>" in head
                           or ">1019<" in head)
            if in_progress and waited < max_wait:
                sleep(5)
                waited += 5
                continue
            err = _flex_field(head, "ErrorMessage") or head
            raise RuntimeError(f"Flex GetStatement failed: "
                               f"{_flex_err(err, token)}")
        return raw


def _flex_err(text: str, token: str) -> str:
    """A Flex error text (an ErrorMessage, else the start of the body)
    scrubbed of the token and account ids, then cut (GitHub #81)."""
    return " ".join(scrub_detail(text, (token,)).split())[:200]


def _flex_field(xml_text: str, tag: str) -> Optional[str]:
    lo = xml_text.find(f"<{tag}>")
    hi = xml_text.find(f"</{tag}>")
    if lo < 0 or hi < 0:
        return None
    return xml_text[lo + len(tag) + 2:hi].strip()


def flex_statement_count(text: str) -> int:
    """Number of per-account statements in a Flex download: its runs
    of `Statement` section rows — the section's Header and the Data
    rows under it (BrokerName, Title, Period, WhenGenerated, ...). A
    multi-account query concatenates one statement per account, each
    with a Statement section of its own, and blending two accounts'
    books into one inputs folder must be refused, not parsed. Counting
    the 'Statement,Data' ROWS read one statement's metadata rows as
    that many accounts and refused every real download (audit
    2026-10-24 B02). A Header row always opens a new statement; a Data
    row only when no Statement row came right before it."""
    import csv as _csv
    import io as _io
    count = 0
    in_statement = False
    for row in _csv.reader(_io.StringIO(text)):
        if len(row) < 2:
            continue                    # a blank line splits nothing
        sec, kind = row[0].lstrip("﻿").strip(), row[1].strip()
        if sec == "Statement" and kind == "Header":
            count += 1
            in_statement = True
        elif sec == "Statement" and kind == "Data":
            if not in_statement:
                count += 1
            in_statement = True
        else:
            in_statement = False
    return count


# Section columns that name the broker account a row belongs to (the
# Activity Statement's `Account`, a Flex query's `ClientAccountID`).
_FLEX_ACCOUNT_COLUMNS = ("account", "clientaccountid")


def flex_account_ids(text: str) -> List[str]:
    """The broker accounts a Flex download names, sorted: every
    non-blank cell of an `Account` / `ClientAccountID` column of a
    section's Data rows (a `Total` row aside), the Account Information
    section's `Account` (its first word: "U1234567 (Custom)") and its
    `Accounts Included` list — the cells the IB parser books rows by.
    A combined statement has ONE Statement section and rows of two
    accounts, so flex_statement_count said 1 and two accounts' books
    were written into one taxjson account (backlog TJ-0867)."""
    import csv as _csv
    import io as _io
    headers: Dict[str, List[str]] = {}
    out = set()
    for row in _csv.reader(_io.StringIO(text)):
        if len(row) < 3:
            continue
        sec, kind = row[0].lstrip("﻿").strip(), row[1].strip()
        if kind == "Header":
            headers[sec] = [h.strip().lower() for h in row[2:]]
            continue
        if kind != "Data":
            continue
        cells = dict(zip(headers.get(sec) or [], row[2:]))
        if sec == "Account Information":
            name = (cells.get("field name") or "").strip()
            value = (cells.get("field value") or "").strip()
            if name == "Account" and value:
                out.add(value.split()[0].upper())
            elif name == "Accounts Included":
                out.update(a.strip().split()[0].upper()
                           for a in value.split(",") if a.strip())
            continue
        for col in _FLEX_ACCOUNT_COLUMNS:
            v = (cells.get(col) or "").strip()
            if v and "total" not in v.lower():
                out.add(v.split()[0].upper())
    return sorted(out)


def looks_like_ib_statement(text: str) -> bool:
    """The section,Header/Data CSV shape ib_extractor parses. A Flex
    query produces it with 'include section code and line descriptor'
    enabled; without it the download is a different format the parser
    would refuse. The SAME test broker detection routes on, so a
    download fetch accepts is one `taxjson run` can read (audit R1-57:
    a 'Trades,Header' anywhere in the first 4 KB used to pass here and
    then stop the run with "cannot detect broker")."""
    from taxjson.bin.taxjson_detect_brokerage import looks_like_ib_text
    return looks_like_ib_text(text)


# ------------------------------------------------------------ window

def qt_window(days: Optional[int], since: Optional[str],
              today: Optional[date] = None,
              year: Optional[int] = None) -> Tuple[date, date]:
    """[start, end] for a Questrade fetch. Explicit --from/--days win;
    the default covers the WHOLE tax-year window every time — from
    Dec 1 of the year BEFORE the config tax `year` through Jan 31 of
    the year after (capped at today): the superficial-loss window of
    a year-boundary disposition (30 days either side) and a
    late-December trade settling in January both fall inside it. Re-fetching the full window keeps the file equivalent to a
    manual YTD export and self-heals late-posted or corrected rows
    anywhere in the year; the union-merge dedups the overlap. Falls
    back to a trailing 90 days when no year is known. Raises ValueError
    for --from with --days (one would be ignored silently, S031-15) and
    for a tax year that has not started (it used to fall back to the
    trailing 90 days of the PREVIOUS year, written under the new year's
    file name — R1-354)."""
    end = today or date.today()
    if since and days:
        raise ValueError("--from and --days both pick the start of the "
                         "window — give one")
    if since:
        return date.fromisoformat(since), end
    if days:
        return end - timedelta(days=days), end
    if year:
        # The window is the TAX YEAR plus the superficial-loss margins:
        # Dec 1 of the prior year through Jan 31 of the next — capped
        # at today. The 30-day window around a disposition settling
        # early in January reaches back into December, and one settling
        # Dec 31 reaches Jan 30: a Jan 16-30 (or Dec 1-14) repurchase in
        # a fetch-only account used to fall outside the old Dec 15 ..
        # Jan 15 window, so a permanently denied loss was allowed
        # (S002-05/S031-13). December trades settling in January are
        # covered too. For the current year the cap means "through
        # today"; for a past year (--year backfill) it keeps the file
        # year-scoped.
        start = date(int(year) - 1, 12, 1)
        end = min(end, date(int(year) + 1, 1, 31))
        if start <= end:
            return start, end
        raise ValueError(
            f"the project's tax year {year} window starts {start}, after "
            f"today — nothing of {year} to fetch yet (pass --from or "
            f"--days to fetch a recent window explicitly)")
    return end - timedelta(days=90), end


if __name__ == "__main__":                       # pragma: no cover
    sys.exit("taxjson_fetch.api is not a standalone tool — use "
             "`taxjson fetch` (configured via `brokerage` + "
             "`account`/`query_id` under [accounts.<name>] in "
             "taxjson.toml).")
