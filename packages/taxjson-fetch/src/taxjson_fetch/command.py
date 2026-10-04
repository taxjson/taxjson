"""`taxjson fetch` for Questrade and IBKR Flex — the command half.

Moved out of the core (taxjson.bin.taxjson_run) into the taxjson-fetch
plugin: the Questrade refresh-token file, the union-merge of a re-fetched
window into questrade_<year>.csv, the overlap check and --trim-overlap
for manually exported siblings, the Flex download's span and lost-dates
guard, and live positions (--positions). The HTTP clients and the CSV /
TOML writers are in taxjson_fetch.api; the core's `taxjson fetch`
dispatcher calls run() through taxjson_fetch.plugin.
"""
import re
import shutil
import sys
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson_fetch import api as F


def _die(msg: str) -> None:
    sys.exit(f"taxjson fetch: {msg}")


def _questrade_token_file(cache: Path) -> Path:
    """Where the Questrade refresh token lives, read AND written:
    $QUESTRADE_TOKEN_FILE > ~/.questrade_token — a shared, tool-neutral
    file that other Questrade API tools may use as well, serving every
    taxjson project on the machine.

    Questrade issues ONE rotating chain per API app: every exchange kills
    the previous token, so the token belongs to the APP, not to a single
    tool or project. One shared file means taxjson and any other tool on
    the same app can never rotate each other's copy dead. (`cache` is unused since the
    per-project work/.questrade_refresh_token fallback was removed
    pre-1.0; the parameter stays so call sites read uniformly.)"""
    import os as _os
    del cache
    env = _os.environ.get("QUESTRADE_TOKEN_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path("~/.questrade_token").expanduser()


def _questrade_token_write(tok_cache: Path, token: str) -> None:
    """Persist the ROTATED token atomically (tmp + rename), mode 600 — a
    later failure must not lose it, since the old one is already dead."""
    import os as _os
    tok_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = tok_cache.with_name(tok_cache.name + ".part")
    # 0600 from the first byte, and a FRESH file: a stale .part (or a
    # symlink planted there) is unlinked, then O_EXCL|O_NOFOLLOW refuses
    # to follow or reuse anything that reappears before the open.
    tmp.unlink(missing_ok=True)
    fd = _os.open(str(tmp), _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL
                  | getattr(_os, "O_NOFOLLOW", 0), 0o600)
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(tok_cache)
    try:
        _os.chmod(tok_cache, 0o600)          # belt: pre-existing target
    except OSError:
        pass


def _qt_auth_hint(token: str, tok_cache: Path, *,
                  explicit: bool = False) -> str:
    """Recovery advice for a failed Questrade refresh. The cached
    rotating token wins over $QUESTRADE_REFRESH_TOKEN, so a fresh token
    exported over a dead cache failed the same way with no word about
    the env var being skipped (R1-353). Never prints a token."""
    import os as _os
    env = _os.environ.get("QUESTRADE_REFRESH_TOKEN", "").strip()
    cached = (tok_cache.read_text(encoding="utf-8").strip()
              if tok_cache.exists() else "")
    if not explicit and cached and token == cached:
        msg = (f" — the token used is the cached chain in {tok_cache}"
               + (", NOT $QUESTRADE_REFRESH_TOKEN (the cache wins)"
                  if env and env != cached else "")
               + ". If that chain is dead, start a new one: "
                 "`taxjson fetch --refresh-token <new token>` (or "
                 f"delete {tok_cache.name} and set "
                 "$QUESTRADE_REFRESH_TOKEN).")
        return msg
    return (" — generate a new refresh token in Questrade's API centre "
            "and pass it once with `taxjson fetch --refresh-token`.")


def _qt_live_holdings(root: Path, cache: Path, cfg: Dict[str, Any],
                      wanted: List[str], http, say) -> Dict[str, Path]:
    """Fetch live Questrade positions for each fetch-enabled account in
    `wanted` and write work/<account>_live_holdings.toml (the
    [[holding]] file `taxjson sanity` reads). Returns
    {account: toml_path}. Shares the rotated-token session flow with
    the activity fetch."""
    import os as _os
    from datetime import datetime as _dt
    fetch_cfg = _fetch_sources(cfg)
    out: Dict[str, Path] = {}
    qt_session = None
    for a in wanted:
        fc = fetch_cfg.get(a) or {}
        if fc.get("source") != "questrade":
            continue
        number = fc.get("number") or ""
        if not number:
            continue
        try:
            F.qt_account_segment(number)          # before any login (L2)
        except RuntimeError as e:
            _die(f"[accounts.{a}]: {e}")
        if qt_session is None:
            tok_cache = _questrade_token_file(cache)
            token = ((tok_cache.read_text(encoding="utf-8").strip()
                      if tok_cache.exists() else "")
                     or _os.environ.get("QUESTRADE_REFRESH_TOKEN",
                                        "").strip())
            if not token:
                _die("no Questrade refresh token — run `taxjson "
                     "fetch --refresh-token ...` once first.")
            try:
                qt_session = F.qt_refresh(token, http)
            except RuntimeError as e:
                _die(f"Questrade auth failed: {e}"
                     + _qt_auth_hint(token, tok_cache))
            _questrade_token_write(tok_cache,
                                   qt_session["refresh_token"])
        try:
            positions = F.qt_positions(qt_session, number, http)
        except RuntimeError as e:
            _die(f"{a}: {e}")
        # The account's BOOK symbols decide a live option's suffix when
        # the books hold that exact contract, and the books' .TO
        # OPTIONS teach Montreal roots (a cash-secured put has no
        # equity leg in the live payload — 2026-09 audit). A .TO
        # EQUITY in the books no longer does: a CDR (AMZN.TO) made the
        # account's US AMZN option .TO live vs .US in the books, a
        # false verify mismatch every run (S031-12).
        _book_syms = set()
        try:
            import json as _json
            _bp = cache / f"{a}_base.json"
            if _bp.exists():
                for _r in (_json.loads(_bp.read_text(encoding="utf-8"))
                           .get("transactions") or []):
                    _sym = str(_r.get("symbol") or "").upper()
                    if _sym:
                        _book_syms.add(_sym)
        except Exception:
            pass
        text = F.positions_to_holdings_toml(
            positions, a, number,
            _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            book_symbols=_book_syms)
        toml_path = cache / f"{a}_live_holdings.toml"
        F.write_private(toml_path, text)
        n = sum(1 for pz in positions if pz.get("openQuantity"))
        say(f"  {a}: {n} live position(s) -> {toml_path.name}")
        out[a] = toml_path
    return out


def _merge_csv_text(existing: str, new: str) -> Tuple[str, int]:
    """Union-merge two same-header CSVs on LOGICAL rows (csv module,
    not text lines — a quoted field may contain newlines, and a
    line-based merge interleaved the fragments of multi-line rows into
    silently-wrong data). Duplicates keep the MAX count seen per side:
    byte-identical rows can be physically distinct split fills (the
    parser's disambiguate_split_fills exists for exactly this), so a
    plain set-union would delete real trades on an overlap re-fetch.
    Returns (merged_text, new_row_count). Refuses on a header
    mismatch — a format change must not silently corrupt the file."""
    import csv as _csv
    import io as _io
    from collections import Counter

    def _rows(text):
        return [tuple(r) for r in _csv.reader(_io.StringIO(text))
                if any(f.strip() for f in r)]
    # A BOM (an Excel re-save of the fetch file) is not part of the
    # header: 'header mismatch' failed every later fetch (A2-1445).
    ex_rows = _rows(existing.removeprefix("\ufeff"))
    new_rows = _rows(new.removeprefix("\ufeff"))
    if not new_rows:
        return existing, 0
    if not ex_rows:
        return new, max(0, len(new_rows) - 1)
    if ex_rows[0] != new_rows[0]:
        raise ValueError("header mismatch between the existing fetch "
                         "file and the new download")
    ex_c, new_c = Counter(ex_rows[1:]), Counter(new_rows[1:])
    added = sum((new_c - ex_c).values())
    # ORDER (audit A2-0598): rows of one moment keep the export's order
    # (CA-DATE-14 / US-DATE-13) and Questrade stamps every row at
    # midnight, so the row sequence is data. The new download is the
    # API's own order for what it covers; an existing-only row (outside
    # the window, a restated copy, an extra split fill) is slotted in
    # after the row it followed in the existing file. Then a STABLE
    # sort on the trade date keeps the file chronological (the parser
    # reads a newest-first file bottom-up). Sorting the row tuples put
    # every same-day Buy ahead of its Sell.
    merged = list(new_rows[1:])
    matched = [False] * len(merged)
    anchor = -1
    for row in ex_rows[1:]:
        hit = next((i for i in range(anchor + 1, len(merged))
                    if not matched[i] and merged[i] == row), None)
        if hit is not None:
            matched[hit] = True
            anchor = hit
            continue
        earlier = next((i for i in range(0, anchor + 1)
                        if not matched[i] and merged[i] == row), None)
        if earlier is not None:          # same row, other place: a dup
            matched[earlier] = True
            continue
        anchor += 1                      # existing-only: keep it
        merged.insert(anchor, row)
        matched.insert(anchor, True)
    col = _qt_date_col(list(ex_rows[0]))

    def _day(r):
        try:
            return datetime.strptime(r[col][:10], "%Y-%m-%d").strftime(
                "%Y-%m-%d")
        except (ValueError, IndexError):
            return None
    days = [_day(r) for r in merged]
    if all(days):
        merged = [r for _d, r in sorted(zip(days, merged),
                                        key=lambda p: p[0])]
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(ex_rows[0])
    for row in merged:
        w.writerow(row)
    return buf.getvalue(), added


def _fetch_sources(cfg: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """{account: {source, number, query_id}} from the `brokerage` +
    `account`/`query_id` keys under [accounts.<name>]."""
    out: Dict[str, Dict[str, str]] = {}
    for a, ac in (cfg.get("accounts") or {}).items():
        b = str((ac or {}).get("brokerage") or "").strip()
        if b:
            out[a] = {"source": b,
                      "number": str((ac or {}).get("account")
                                    or "").strip(),
                      "query_id": str((ac or {}).get("query_id")
                                      or "").strip()}
    return out


def _qt_restatement_suspects(existing_csv: str,
                             new_csv: str) -> List[str]:
    """Rows in the EXISTING fetch file that look like stale copies of a
    broker-restated activity: same (date, symbol, action-ish prefix) as
    a fetched row but different content. The union-merge deliberately
    keeps both (it cannot tell a restatement from a split fill), so a
    corrected dividend/commission would double-count silently — this
    names the suspects so the user can delete the stale copy."""
    import csv as _csv
    import io as _io

    def _rows(text):
        try:
            rows = list(_csv.reader(_io.StringIO(text)))
        except Exception:
            return [], []
        return (rows[0] if rows else []), [r for r in rows[1:] if r]

    hdr, ex_rows = _rows(existing_csv)
    _hdr2, new_rows = _rows(new_csv)
    if not ex_rows or not new_rows:
        return []

    def _key(r):
        # (trade date, symbol, activity type) — colidx by Questrade
        # header names, falling back to positions 0/3/12.
        def _col(name, default):
            try:
                return r[hdr.index(name)] if name in hdr else r[default]
            except (ValueError, IndexError):
                return ""
        return (_col("Transaction Date", 0)[:10],
                _col("Symbol", 3).strip().upper(),
                _col("Activity Type", 12).strip())

    new_by_key: Dict[tuple, set] = {}
    for r in new_rows:
        new_by_key.setdefault(_key(r), set()).add(tuple(r))
    out: List[str] = []
    for r in ex_rows:
        k = _key(r)
        if not k[1]:
            continue
        peers = new_by_key.get(k)
        if peers and tuple(r) not in peers:
            out.append(f"{k[0]} {k[1]} ({k[2] or 'row'})")
    # De-dup while keeping order.
    seen: set = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def _qt_window_overlap(acct_dir: Path, out: Path,
                       start_iso: str,
                       end_iso: str) -> List[Tuple[Path, int]]:
    """Sibling Questrade CSVs with rows dated inside the fetched
    window. The two sources round price/gross differently, so their
    copies of the same trade hash to DIFFERENT ids and never dedup —
    coexisting coverage double-counts the books."""
    hits: List[Tuple[Path, int]] = []
    import csv as _csv
    import io as _io
    from taxjson.bin.taxjson_run import detect_broker, input_files
    # input_files, not glob("*.csv"): `run` reads QT_MANUAL.CSV too, so
    # a case-sensitive glob let an overlapping upper-case export double
    # the books with no warning (S046-14).
    for sib in input_files(acct_dir, ".csv"):
        if sib == out:
            continue
        if re.fullmatch(r"questrade_\d{4}\.csv", sib.name):
            # A prior tax year's OWN fetch file: API-written rows are
            # formatted identically, hash to the same ids, and DO
            # dedup — warning about it (every fetch, after a year
            # rollover) was a false alarm whose advice would trim an
            # API file needlessly.
            continue
        try:
            if detect_broker(sib) != "questrade":
                continue
            # Decoded like detection and the parser (UTF-16, UTF-8 BOM):
            # read as UTF-8 a UTF-16 export showed no rows, and the
            # double-count warning was lost (audit A2-0256 / A2-1040).
            rows = list(_csv.reader(_io.StringIO(_qt_sibling_text(sib))))
        except Exception:
            continue
        col = _qt_date_col(rows[0] if rows else [])
        n = sum(1 for r in rows[1:] if len(r) > col and _row_date_in_window(
            r[col], start_iso, end_iso))
        if n:
            hits.append((sib, n))
    return hits


def _qt_sibling_text(path: Path) -> str:
    """A Questrade CSV's text, decoded the way detection and the parser
    read it (base.decode_broker_text: UTF-16 by its BOM, else UTF-8
    with an optional BOM)."""
    from taxjson.lib.brokerages.base import decode_broker_text
    return decode_broker_text(path.read_bytes(), path.name)


def _qt_date_col(header: List[str]) -> int:
    """Index of Questrade's "Transaction Date" column, by header name.
    The window is a TRADE-date window (the API's own); reading r[0]
    trimmed by Settlement Date when a manual export had that column
    first, deleting a trade the fetched file does not hold (R1-74)."""
    for i, h in enumerate(header):
        if h.strip().lstrip("\ufeff").lower() == "transaction date":
            return i
    return 0


def _row_date_in_window(first_field: str, start_iso: str,
                        end_iso: str) -> bool:
    """True only for rows whose first field carries a REAL date INSIDE
    the fetched window (both ends) — a lexicographic compare counted
    (and trimmed!) disclaimer/stray-header rows, and an open end
    counted (and --trim-overlap DELETED!) sibling rows dated months
    AFTER the window, i.e. real trades the fetched file does not own
    (2026-09 audit)."""
    try:
        datetime.strptime(first_field[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return start_iso <= first_field[:10] <= end_iso


def _qt_trim_file(path: Path, start_iso: str, end_iso: str) -> int:
    """Rewrite a Questrade CSV keeping only rows dated OUTSIDE the
    fetch window (the fetched file owns the window — and nothing
    else). The original is kept as <name>.bak. Returns the number of
    rows removed; unparseable rows are kept (safe side)."""
    import csv as _csv
    import io as _io
    # Decoded like the parser (A2-1040): a UTF-16 export read as UTF-8
    # matched no row. The trimmed copy is written as UTF-8 (the parser
    # reads both); the original bytes stay in the .bak.
    rows = list(_csv.reader(_io.StringIO(_qt_sibling_text(path))))
    for i, r in enumerate(rows, 1):
        if any("\n" in c or "\r" in c for c in r):
            # An unbalanced quote swallows the following lines into one
            # record; judged by its first date, the swallowed
            # out-of-window trades were deleted with it and the count
            # said 1 (S046-16). Never rewrite such a file.
            raise ValueError(f"{path.name}: record {i} spans several "
                             f"lines (an unbalanced quote?) — not "
                             f"trimmed; fix the file by hand")
    col = _qt_date_col(rows[0] if rows else [])
    keep = [rows[0]] + [r for r in rows[1:]
                        if not (len(r) > col and _row_date_in_window(
                            r[col], start_iso, end_iso))]
    removed = len(rows) - len(keep)
    if removed:
        # Never clobber an existing backup: a second --trim-overlap
        # run would replace the FULL original with the already-trimmed
        # copy, silently destroying the only copy of the trimmed rows.
        bak = path.with_name(path.name + ".bak")
        n = 2
        while bak.exists():
            bak = path.with_name(f"{path.name}.bak{n}")
            n += 1
        # Copy, then write the trimmed file atomically and private
        # (0600) like every fetched file — replace-then-write_text left
        # a 0664 file, and no file at all if the write failed (R1-74).
        shutil.copy2(path, bak)
        buf = _io.StringIO()
        _csv.writer(buf, lineterminator="\n").writerows(keep)
        F.write_private(path, buf.getvalue())
    return removed


# A cell that IS a date or date-time ("2025-03-05", "20250305",
# "2025-03-05, 10:00:00", "20250305;100000"), never digits inside a
# number: "-12.20180315" read as 2018-03-15 and widened the download's
# span so the replace guard stayed silent (audit A2-0083).
_FLEX_DATE_RE = re.compile(r"^\s*(20\d{2})-?(0[1-9]|1[0-2])-?"
                           r"(0[1-9]|[12]\d|3[01])"
                           r"(?:[,; T]\s*\d{1,2}:?\d{2}(?::?\d{2})?)?\s*$")
# Sections whose rows are not activity (statement metadata, summaries,
# performance figures): never dated from.
_FLEX_SKIP_SECTIONS = ("Statement", "Account Information")
# Activity sections whose dates bound what a download covers when its
# Statement section names no Period.
_FLEX_SPAN_SECTIONS = ("Trades", "Dividends", "Transfers")


def _flex_rows(text: str):
    """(section, date-cells) for each Data row of an IB section,Header/
    Data CSV. With a section Header, only its date columns (a header
    holding 'Date' — 'Date/Time', 'Date', 'Settle Date', ...) count;
    without one, every cell that is a whole date counts."""
    import csv as _csv
    import io as _io
    headers: Dict[str, List[str]] = {}
    for row in _csv.reader(_io.StringIO(text)):
        if len(row) < 3:
            continue
        sec, kind = row[0].lstrip("\ufeff").strip(), row[1].strip()
        if kind == "Header":
            headers[sec] = [h.strip() for h in row[2:]]
            continue
        if kind != "Data" or sec in _FLEX_SKIP_SECTIONS:
            continue
        cells = row[2:]
        hdr = headers.get(sec)
        if hdr:
            cells = [c for h, c in zip(hdr, cells)
                     if "date" in h.lower() and "ex date" not in h.lower()
                     and "pay date" not in h.lower()]
        out = []
        for c in cells:
            m = _FLEX_DATE_RE.match(c)
            if m:
                out.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
        yield sec, out


def _flex_dates(text: str) -> List[str]:
    """Sorted ISO activity dates of an IB statement's data rows (the
    Statement section — generation time, period — is skipped; with a
    section Header only its date columns are read)."""
    return sorted({d for _s, ds in _flex_rows(text) for d in ds})


def _flex_span(text: str) -> Optional[Tuple[str, str]]:
    """(first, last) ISO day a download covers: its Statement Period
    when it names one, else the span of its Trades / Dividends /
    Transfers dates (a late withholding-tax adjustment dated in an
    earlier year does not stretch it). None when nothing is dated."""
    import csv as _csv
    import io as _io
    from taxjson.lib.brokerages.ib_extractor import _ib_period
    for row in _csv.reader(_io.StringIO(text)):
        if (len(row) >= 4 and row[0].lstrip("\ufeff").strip() == "Statement"
                and row[1].strip() == "Data"
                and row[2].strip() == "Period"):
            span = _ib_period(row[3])
            if span:
                return span[0].isoformat(), span[1].isoformat()
    days = sorted({d for s, ds in _flex_rows(text)
                   if s in _FLEX_SPAN_SECTIONS for d in ds})
    if not days:
        days = _flex_dates(text)
    return (days[0], days[-1]) if days else None


def _flex_lost_dates(existing: str, new: str, year: Any) -> List[str]:
    """Dates of `year` the existing ib_flex.csv covers but the new
    download's span (_flex_span) does not: overwriting would delete
    those rows. A Flex query set to 'Year to date' re-fetched in January
    replaced a whole year of activity at exit 0 (S007-00)."""
    if not year or not existing:
        return []
    old = [d for d in _flex_dates(existing) if d[:4] == str(year)]
    got = _flex_span(new)
    if not got:
        return old
    return [d for d in old if not got[0] <= d <= got[1]]


def run(request) -> Dict[str, Any]:
    """Download every account in request.accounts (each declares
    `brokerage = "questrade"` or `"ibkr_flex"`) into inputs/<account>/:
    questrade_<year>.csv through the Questrade REST API, ib_flex.csv
    through the IBKR Flex Web Service — files the core's existing
    parsers read; hand-exported CSVs keep working side by side.
    Returns {account: result} for `taxjson fetch --json`. Moved from
    the core's cmd_fetch, which is now the plugin dispatcher."""
    args = request.args
    root = request.root
    cache = request.work
    cfg = request.config
    fetch_cfg = _fetch_sources(cfg)
    wanted = list(request.accounts)
    say = request.say
    json_mode = bool(request.json)
    results: Dict[str, Any] = {}

    if getattr(args, "days", None) is not None and args.days <= 0:
        sys.exit(f"taxjson fetch: --days must be positive, got "
                 f"{args.days}")
    if getattr(args, "year", None) is not None:
        if getattr(args, "from_date", None) or getattr(args, "days",
                                                       None):
            sys.exit("taxjson fetch: --year picks the whole past "
                     "year's window and file — combine it with "
                     "--from/--days and the file name would lie about "
                     "its contents. Use one or the other.")
        from datetime import date as _d
        if not 2000 <= args.year <= _d.today().year:
            sys.exit(f"taxjson fetch: --year {args.year} is outside "
                     f"2000..{_d.today().year}.")
    if getattr(args, "from_date", None):
        from datetime import date as _date_cls
        try:
            # Same parser qt_window uses — strptime accepted unpadded
            # dates that fromisoformat then crashed on.
            _f = _date_cls.fromisoformat(args.from_date)
        except ValueError:
            sys.exit(f"taxjson fetch: --from {args.from_date!r} is not "
                     f"a valid YYYY-MM-DD date")
        if _f > _date_cls.today():
            sys.exit(f"taxjson fetch: --from {args.from_date} is in "
                     f"the future — nothing to fetch.")
    http = F.default_http_get
    qt_session = None
    import os as _os
    for a in wanted:
        fc = fetch_cfg[a]
        source = fc["source"]
        acct_dir = root / "inputs" / a
        if source == "questrade":
            number = fc["number"]
            if not number:
                sys.exit(f"taxjson fetch: [accounts.{a}] needs "
                         f"`account` (the Questrade account number).")
            try:
                # digits only, checked before the token is spent (L2)
                F.qt_account_segment(number)
            except RuntimeError as e:
                sys.exit(f"taxjson fetch: [accounts.{a}]: {e}")
            if qt_session is None:
                tok_cache = _questrade_token_file(cache)
                token = (getattr(args, "refresh_token", None)
                         or (tok_cache.read_text(encoding="utf-8")
                             .strip() if tok_cache.exists() else "")
                         or _os.environ.get("QUESTRADE_REFRESH_TOKEN",
                                            "").strip())
                if not token:
                    sys.exit("taxjson fetch: no Questrade refresh "
                             "token — pass --refresh-token once (or "
                             "set $QUESTRADE_REFRESH_TOKEN); the "
                             "rotating chain then lives in "
                             f"{tok_cache}.")
                try:
                    qt_session = F.qt_refresh(token, http)
                except RuntimeError as e:
                    sys.exit(f"taxjson fetch: Questrade auth failed: "
                             f"{e}" + _qt_auth_hint(
                                 token, tok_cache,
                                 explicit=bool(getattr(
                                     args, "refresh_token", None))))
                # Persist the ROTATED token immediately — a later
                # failure must not lose it (the old one is now dead).
                _questrade_token_write(tok_cache,
                                       qt_session["refresh_token"])
            _fetch_year = (getattr(args, "year", None)
                           or cfg.get("settings", {}).get("year"))
            try:
                start, end = F.qt_window(getattr(args, "days", None),
                                         getattr(args, "from_date", None),
                                         year=_fetch_year)
            except ValueError as e:
                sys.exit(f"taxjson fetch: {e}")
            if _fetch_year and (getattr(args, "days", None)
                                or getattr(args, "from_date", None)):
                from datetime import date as _dd
                _y = int(_fetch_year)
                if (start < _dd(_y - 1, 12, 1)
                        or end > _dd(_y + 1, 1, 31)):
                    # R1-354: the file is named for the project year
                    # whatever the window; the parser dates each row
                    # itself, so this is only a label — but say so.
                    say(f"  note: the window {start} -> {end} reaches "
                        f"outside tax year {_y}'s (Dec 1 {_y - 1} .. Jan "
                        f"31 {_y + 1}); those rows go into "
                        f"questrade_{_y}.csv too (each row is still "
                        f"dated by its own trade/settle date).")
            say(f"fetch {a}: questrade #{F.mask_account_number(number)} "
                f"{start} -> {end}")
            try:
                acts = F.qt_activities(qt_session, number, start, end,
                                       http)
            except RuntimeError as e:
                sys.exit(f"taxjson fetch: {a}: {e}")
            new_csv = F.qt_to_csv(acts, number)
            by_type = F.activity_type_counts(acts)
            if by_type:
                say("  types: " + ", ".join(
                    f"{t} {n}" for t, n in by_type.items()))
            out = acct_dir / (f"questrade_{_fetch_year}.csv"
                              if _fetch_year else "questrade_api.csv")
            try:
                existing = (out.read_text(encoding="utf-8")
                            if out.exists() else "")
                merged, added = _merge_csv_text(existing, new_csv)
            except ValueError as e:
                sys.exit(f"taxjson fetch: {a}: {e} — move the old "
                         f"{out.name} aside and re-fetch.")
            overlap_now = _qt_window_overlap(acct_dir, out,
                                             start.isoformat(),
                                             end.isoformat())
            results[a] = {
                "source": "questrade", "file": out.name,
                "window": [start.isoformat(), end.isoformat()],
                "downloaded": len(acts), "added": added,
                "by_type": by_type,
                "overlaps": [{"file": sib.name, "rows": n}
                             for sib, n in overlap_now],
            }
            if getattr(args, "dry_run", False):
                say(f"  would add {added} row(s) to {out.name} "
                    f"({len(acts)} activities downloaded)")
                for sib, n in overlap_now:
                    say(f"  note: {sib.name} has {n} row(s) inside "
                        f"the window (see --trim-overlap)")
                continue
            F.write_private(out, merged)
            say(f"  {out.name}: +{added} new row(s) "
                f"({len(acts)} downloaded)")
            _restated = _qt_restatement_suspects(existing, new_csv)
            for _line in _restated[:5]:
                say(f"  note: possible broker RESTATEMENT — an "
                    f"existing row matches a fetched row on "
                    f"(date, symbol, action) but differs elsewhere; "
                    f"if Questrade corrected it, delete the stale "
                    f"copy: {_line}")
            if len(_restated) > 5:
                say(f"  note: (+{len(_restated) - 5} more possible "
                    f"restatements)")
            overlap = _qt_window_overlap(acct_dir, out,
                                         start.isoformat(),
                                         end.isoformat())
            if overlap and getattr(args, "trim_overlap", False):
                results[a]["trimmed"] = []
                for sib, n in overlap:
                    try:
                        cut = _qt_trim_file(sib, start.isoformat(),
                                            end.isoformat())
                    except ValueError as e:
                        print(f"taxjson fetch: WARNING: {a}/{e}",
                              file=sys.stderr)
                        results[a]["trimmed"].append(
                            {"file": sib.name, "rows": 0,
                             "refused": str(e)})
                        continue
                    results[a]["trimmed"].append(
                        {"file": sib.name, "rows": cut})
                    say(f"  {sib.name}: trimmed {cut} row(s) inside "
                        f"the fetched window (original kept as "
                        f"{sib.name}.bak)")
            elif overlap:
                for sib, n in overlap:
                    print(f"taxjson fetch: WARNING: {a}/{sib.name} has "
                          f"{n} row(s) dated on/after {start} — the "
                          f"same trades exported manually round "
                          f"price/gross differently than the API, so "
                          f"they will NOT dedup against {out.name} and "
                          f"the books will double-count. Re-run with "
                          f"--trim-overlap (keeps a .bak), or remove "
                          f"the overlapping rows yourself.",
                          file=sys.stderr)
        elif source == "ibkr_flex":
            query_id = fc["query_id"]
            if not query_id:
                sys.exit(f"taxjson fetch: [accounts.{a}] needs "
                         f"`query_id` (the Flex query id).")
            token = (getattr(args, "flex_token", None)
                     or _os.environ.get("IBKR_FLEX_TOKEN", "").strip())
            if not token:
                sys.exit("taxjson fetch: no IBKR Flex token — set "
                         "$IBKR_FLEX_TOKEN (or pass --flex-token).")
            say(f"fetch {a}: ibkr flex query {query_id}")
            try:
                raw = F.flex_fetch(token, query_id, http)
            except RuntimeError as e:
                sys.exit(f"taxjson fetch: {a}: {e}")
            # Decoded like detection and the parser (UTF-16, a UTF-8
            # BOM dropped): with the BOM kept as U+FEFF the section
            # test refused a download `run` reads (A2-0799, R1-57).
            from taxjson.lib.brokerages.base import (
                BrokerageParseError as _BPE, decode_broker_text as _dbt)
            try:
                text = _dbt(raw, "the Flex download")
            except _BPE:
                text = raw.decode("utf-8-sig", "replace")
            out = acct_dir / "ib_flex.csv"
            _nstmt = F.flex_statement_count(text)
            if _nstmt > 1:
                sys.exit(f"taxjson fetch: {a}: the Flex download "
                         f"contains {_nstmt} account statements — a "
                         f"multi-account query would blend books that "
                         f"must stay separate. Scope the Flex query "
                         f"to ONE account (one query per taxjson "
                         f"account; the token is shared).")
            if not F.looks_like_ib_statement(text):
                bad = out.with_suffix(".csv.unrecognized")
                saved = ""
                if not getattr(args, "dry_run", False):
                    F.write_private(bad, text)
                    saved = f" — saved to {bad.name}"
                sys.exit(f"taxjson fetch: {a}: the Flex download is "
                         f"not in the section,Header/Data CSV shape "
                         f"the IB parser reads{saved}. "
                         f"In the Flex query settings choose format "
                         f"CSV and enable 'include section code and "
                         f"line descriptor', then re-fetch.")
            results[a] = {"source": "ibkr_flex", "file": out.name,
                          "lines": len(text.splitlines())}
            _pyear = cfg.get("settings", {}).get("year")
            _existing = (out.read_text(encoding="utf-8", errors="replace")
                         if out.exists() else "")
            _lost = _flex_lost_dates(_existing, text, _pyear)
            _got = _flex_span(text)
            _span = f"{_got[0]}..{_got[-1]}" if _got else "no dated rows"
            if _lost:
                _new = out.with_name(out.name + ".new")
                if not getattr(args, "dry_run", False):
                    F.write_private(_new, text)
                sys.exit(f"taxjson fetch: {a}: the Flex download covers "
                         f"{_span}, but {out.name} holds {len(_lost)} "
                         f"{_pyear} activity date(s) outside it "
                         f"({_lost[0]}..{_lost[-1]}) — replacing it "
                         f"would delete that activity from the books. "
                         + ("" if getattr(args, "dry_run", False) else
                            f"The download was saved as {_new.name} "
                            f"(not read by `taxjson run`). ")
                         + f"Set the Flex query's period to cover "
                         f"{_pyear}, or move {out.name} aside to accept "
                         f"the new file.")
            if getattr(args, "dry_run", False):
                say(f"  would write {out.name} "
                    f"({len(text.splitlines())} lines, {_span})")
                continue
            if _existing and _existing != text:
                # Never lose the previous statement: numbered backups,
                # like --trim-overlap's (not read by `taxjson run`).
                bak = out.with_name(out.name + ".bak")
                n = 2
                while bak.exists():
                    bak = out.with_name(f"{out.name}.bak{n}")
                    n += 1
                F.write_private(bak, _existing)
            # Atomic like the Questrade path: a crash mid-write must
            # not leave a truncated statement for the next run.
            F.write_private(out, text)
            say(f"  {out.name}: {len(text.splitlines())} lines, {_span} "
                f"(overwritten — a Flex query re-covers its whole "
                f"configured period"
                + (f"; previous copy kept as {bak.name}"
                   if _existing and _existing != text else "") + ")")
            if (_pyear and _got and int(_pyear) < date_cls.today().year
                    and (_got[0] > f"{_pyear}-01-10"
                         or _got[-1] < f"{_pyear}-12-20")):
                print(f"taxjson fetch: WARNING: {a}: the Flex download "
                      f"covers {_span}, not the whole tax year "
                      f"{_pyear} — set the query's period to the full "
                      f"year (Jan 1 to Dec 31, plus January for "
                      f"year-end settlements).", file=sys.stderr)
        else:
            sys.exit(f"taxjson fetch: [accounts.{a}] brokerage must be "
                     f"'questrade' or 'ibkr_flex', got {source!r}.")
    if getattr(args, "positions", False) \
            and not getattr(args, "dry_run", False):
        live = _qt_live_holdings(root, cache, cfg, wanted, http, say)
        for a, pth in live.items():
            results.setdefault(a, {})["live_holdings"] = pth.name
        if live and not json_mode:
            files_str = " ".join(str(pv) for pv in live.values())
            print(f"cross-check after rebuilding: taxjson run sanity "
                  f"{' '.join(live)} {files_str}")
    return results
