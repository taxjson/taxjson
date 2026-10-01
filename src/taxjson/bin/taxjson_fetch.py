"""taxjson fetch — pull broker activity straight into inputs/.

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


def _http_error_detail(e: "urllib.error.HTTPError") -> str:
    """HTTP status plus the response BODY — Questrade and IB put the
    actual reason ({"code":..,"message":..} / an XML ErrorMessage) in
    the body, and swallowing it left only 'HTTP Error 400' to debug
    with."""
    body = ""
    try:
        body = e.read().decode("utf-8", "replace").strip()[:300]
    except Exception:
        pass
    return f"HTTP {e.code} {e.reason}" + (f" — {body}" if body else "")


def _http_json(url: str, http_get: Callable[[str], bytes]) -> Any:
    try:
        raw = http_get(url)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"request failed: {_http_error_detail(e)}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"request failed: {e}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError(f"non-JSON response from {url.split('?')[0]}: "
                           f"{e}") from e


# ------------------------------------------------------------ Questrade

def qt_refresh(refresh_token: str,
               http_get: Callable[[str], bytes]) -> Dict[str, str]:
    """Exchange the refresh token. Returns {api_server, access_token,
    refresh_token} — the NEW refresh token MUST be persisted (Questrade
    rotates it; reusing the old one fails the next fetch)."""
    url = (f"{_QT_LOGIN}?grant_type=refresh_token&refresh_token="
           f"{urllib.parse.quote(refresh_token)}")
    doc = _http_json(url, http_get)
    for k in ("api_server", "access_token", "refresh_token"):
        if not doc.get(k):
            raise RuntimeError(f"Questrade token response missing {k!r}"
                               f" — is the refresh token valid?")
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


def _qt_get(api_server: str, access_token: str, path: str,
            http_get: Callable[[str], bytes]) -> Any:
    if not qt_api_server_ok(api_server):
        raise RuntimeError(
            f"refusing to send the Questrade access token to "
            f"{api_server!r} (not https://*.questrade.com)")
    url = api_server.rstrip("/") + path
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
        raise RuntimeError(f"{path.split('?')[0]}: "
                           f"{_http_error_detail(e)}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"request failed: {e}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError(f"non-JSON response from {path}: {e}") from e


def qt_activities(session: Dict[str, str], number: str,
                  start: date, end: date,
                  http_get: Callable[[str], bytes]) -> List[Dict[str, Any]]:
    """All activities for the account number over [start, end], fetched
    in <=31-day chunks (the API's hard window cap)."""
    out: List[Dict[str, Any]] = []
    seen_prev: set = set()
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=_QT_CHUNK_DAYS), end)
        qs = urllib.parse.urlencode({
            "startTime": f"{cur.isoformat()}T00:00:00-05:00",
            "endTime": f"{chunk_end.isoformat()}T23:59:59-05:00"})
        doc = _qt_get(session["api_server"], session["access_token"],
                      f"/v1/accounts/{number}/activities?{qs}", http_get)
        # Boundary-day activities come back in BOTH adjacent chunks
        # (a trade on the boundary date settles inside the next
        # window, and the server matches it in each) — drop repeats
        # of rows an EARLIER chunk already returned. Within-chunk
        # duplicates are kept: identical same-chunk rows are genuine
        # split fills, exactly as the manual export shows them.
        chunk_keys = []
        for act in doc.get("activities") or []:
            k = json.dumps(act, sort_keys=True, default=str)
            if k in seen_prev:
                continue
            chunk_keys.append(k)
            out.append(act)
        seen_prev.update(chunk_keys)
        cur = chunk_end + timedelta(days=1)
    return out


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
    exact column set the existing parser reads. Rows sort by
    transaction date so re-fetches are byte-stable."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_QT_COLUMNS)
    def _key(a):
        return (str(a.get("tradeDate") or a.get("transactionDate") or ""),
                str(a.get("symbol") or ""), str(a.get("action") or ""))
    for a in sorted(activities, key=_key):
        w.writerow([
            # tradeDate FIRST: the export's "Transaction Date" column
            # is the TRADE date, and the API's field of that name is
            # the POSTING date (= settlement) — mapping transactionDate
            # shifted every trade 1-2 days, so nothing deduped against
            # manually-exported rows and the books double-counted.
            _qt_date(str(a.get("tradeDate")
                         or a.get("transactionDate") or "")),
            _qt_date(str(a.get("settlementDate") or "")),
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


def qt_positions(session: Dict[str, str], number: str,
                 http_get: Callable[[str], bytes]) -> List[Dict[str, Any]]:
    """Live open positions for the account — symbol, openQuantity,
    averageEntryPrice, currentPrice, currentMarketValue."""
    doc = _qt_get(session["api_server"], session["access_token"],
                  f"/v1/accounts/{number}/positions", http_get)
    return doc.get("positions") or []


_QT_OPT_RE = None  # compiled lazily below

_MONTHS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
           "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
           "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}


def qt_position_symbol(sym: str, to_roots=frozenset(),
                       book_symbols=frozenset()) -> str:
    """Questrade position symbol -> taxjson convention. Equities:
    Canadian venues ('.TO', '.VN' TSX Venture, '.CN', '.NE') all map
    to '.TO' with a dotted preferred series, as the parsers do; bare
    symbols are US listings ('.US'), and a trailing CLASS letter
    ('BRK.B') is not an exchange — it takes '.US' too. Options ('BMO20Jan26C88.00')
    become OCC ('BMO260120C00088000.TO') — suffixed .TO when the
    root's equity also appears in this payload as a .TO listing
    (Montréal-listed options on Canadian names), else .US.
    When the account's BOOKS already hold the exact contract
    (`book_symbols`), their suffix wins — the parsers suffix a contract
    by its listing currency, and a same-root .TO equity (a CDR such as
    AMZN.TO) must not turn a US option Montreal-listed (S031-12).
    Unrecognized shapes pass through untouched so a mismatch stays
    VISIBLE in the sanity diff instead of being mangled."""
    import re as _re
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    global _QT_OPT_RE
    if _QT_OPT_RE is None:
        _QT_OPT_RE = _re.compile(
            r"^([A-Z][A-Z0-9]*)(\d{2})([A-Z][a-z]{2})(\d{2})"
            r"([CP])(\d+(?:\.\d+)?)$")
    sym = (sym or "").strip()
    m = _QT_OPT_RE.match(sym)
    if m:
        root, dd, mon, yy, right, strike = m.groups()
        mm = _MONTHS.get(mon)
        if mm:
            occ = (f"{root}{yy}{mm}{dd}{right}"
                   f"{int(round(float(strike) * 1000)):08d}")
            if f"{occ}.TO" in book_symbols:
                return f"{occ}.TO"
            if f"{occ}.US" in book_symbols:
                return f"{occ}.US"
            return occ + (".TO" if root in to_roots else ".US")
    if "." not in sym:
        return f"{sym}.US"
    base, _, ext = sym.rpartition(".")
    if ext.upper() in ("TO", "V", "VN", "CN", "NE"):
        # One spelling per Canadian listing, the parsers' own
        # (base.canonical_ca_listing): ABC.VN / CCC.CN -> ABC.TO /
        # CCC.TO, FTN.PRA.TO -> FTN.PR.A.TO. A different spelling here
        # than in the parsed books is a phantom verify mismatch.
        from taxjson.lib.brokerages.base import canonical_ca_listing
        return canonical_ca_listing(sym, "CAD") or sym
    if ext.upper() not in KNOWN_SUFFIXES:
        # A class share, not an exchange: 'BRK.B' is the US listing
        # BRK.B.US. Passing it through unsuffixed made every live
        # class-share position look like a phantom mismatch in the
        # verify diff.
        return f"{sym}.US"
    return sym


def positions_to_holdings_toml(positions: List[Dict[str, Any]],
                               account: str, number: str,
                               generated_at: str,
                               extra_to_roots=frozenset(),
                               book_symbols=frozenset()) -> str:
    """Render live positions as the portoml-style holdings TOML that
    `taxjson sanity` reads ([[holding]] symbol/quantity; extra fields
    are informational)."""
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
    from taxjson.lib.core import is_option_symbol, parse_option_underlying
    book_to_roots = set()
    for b in book_symbols:
        if b.endswith(".TO") and is_option_symbol(b):
            try:
                book_to_roots.add(
                    str(parse_option_underlying(b)).rsplit(".", 1)[0])
            except Exception:
                pass
    to_roots = frozenset(
        str(p.get("symbol") or "").rsplit(".", 1)[0]
        for p in positions
        if str(p.get("symbol") or "").endswith(".TO")) | frozenset(
            extra_to_roots) | frozenset(book_to_roots)
    q = toml_str
    lines = ["# Live Questrade holdings snapshot — taxjson fetch/verify.",
             "# Regenerated on every fetch; do not hand-edit.",
             "[meta]",
             f"account = {q(account)}",
             f"broker_account = {q(number)}",
             'source = "questrade-api"',
             f"generated_at = {q(generated_at)}",
             f"holdings_count = "
             f"{sum(1 for p in positions if p.get('openQuantity'))}"]
    for p2 in sorted(positions,
                     key=lambda x: str(x.get("symbol") or "")):
        qty = float(p2.get("openQuantity") or 0.0)
        if abs(qty) < 1e-12:
            continue
        sym = qt_position_symbol(str(p2.get("symbol") or ""), to_roots,
                                 book_symbols)
        lines += ["", "[[holding]]",
                  f"symbol = {q(sym)}",
                  f"quantity = {qty!r}"]
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
    """Write a fetched statement / snapshot atomically (.part + rename)
    as 0600 inside a 0700 directory. mkdir(mode=) does not tighten a
    directory that already exists, so the chmod is explicit; the .part
    is created fresh (O_EXCL, never through a symlink)."""
    import os
    from pathlib import Path as _P
    path = _P(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".part")
    tmp.unlink(missing_ok=True)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
                f"Flex request failed: {_http_error_detail(e)}") from e
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Flex request failed: {e}") from e

    qs = urllib.parse.urlencode({"t": token, "q": query_id, "v": "3"})
    first = _get(f"{_FLEX_BASE}/SendRequest?{qs}").decode(
        "utf-8", "replace")
    code = _flex_field(first, "ReferenceCode")
    if not code:
        err = _flex_field(first, "ErrorMessage") or first[:200]
        raise RuntimeError(f"Flex SendRequest failed: {err}")
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
            err = _flex_field(head, "ErrorMessage") or head[:200]
            raise RuntimeError(f"Flex GetStatement failed: {err}")
        return raw


def _flex_field(xml_text: str, tag: str) -> Optional[str]:
    lo = xml_text.find(f"<{tag}>")
    hi = xml_text.find(f"</{tag}>")
    if lo < 0 or hi < 0:
        return None
    return xml_text[lo + len(tag) + 2:hi].strip()


def flex_statement_count(text: str) -> int:
    """Number of per-account statements in a Flex download — a
    multi-account query concatenates one 'Statement,Data' block per
    account, and blending two accounts' books into one inputs folder
    must be refused, not parsed."""
    import re as _re
    return len(_re.findall(r'^"?Statement"?,"?Data"?,', text,
                           flags=_re.M))


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
    back to a trailing 90 days when no year is known."""
    end = today or date.today()
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
    return end - timedelta(days=90), end


if __name__ == "__main__":                       # pragma: no cover
    sys.exit("taxjson-fetch is not a standalone tool — use "
             "`taxjson fetch` (configured via `brokerage` + "
             "`account`/`query_id` under [accounts.<name>] in "
             "taxjson.toml).")
