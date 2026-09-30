#!/usr/bin/env python3
"""taxjson-to-base-curr — daily FX rates for one currency pair, printed in
the rates-file format taxjson-convert-currency / merge2 read.

SOURCES (per date, recorded in the output):
  * Target CAD: the Bank of Canada Valet daily average series
    FX<CUR>CAD is the PRIMARY source — CRA (Income Tax Folio S5-F4-C1)
    expects the Bank of Canada rate, or another reliable source used
    consistently. The Valet daily series begin 2017-01-03. For a date
    before 2017-03-01 the Folio (para 1.4) names the Bank's NOON rate:
    the legacy noon series (Valet, 2007-05-01..2017-04-28; source
    `boc-noon`) wins wherever its cache reaches.
    Yahoo Finance (<CUR>CAD=X) is only a FALLBACK: for dates the noon
    series does not reach (before 2007-05-01, or a failed noon fetch),
    for currencies the Bank does not publish, and for a
    published series that has stopped (no observation within a week,
    e.g. RUB since 2022). A Bank of Canada fetch that FAILS is never
    papered over with Yahoo — those dates are left without a rate, and
    the conversion stage turns any transaction on them into a
    validation error.
  * Any other target (a US project's USD base): Yahoo Finance, as
    before.

OUTPUT: one row per calendar date,
    YYYY-MM-DD 12:00:00 FROM TO RATE SOURCE
SOURCE is `boc`, `boc-noon` or `yahoo`. The sixth column is additive: every loader
reads the first five (load_exchange_rates ignores extra columns).

WEEKENDS / HOLIDAYS: a date with no published rate takes the rate of the
most recent PRIOR business day (the usual CRA practice; the pipeline has
always forward-filled this way, and convert-currency's lookback walks
backwards too). A fill never reaches further back than 7 days.

WINDOW: --start (default 2000-01-01) .. --end (default today). The
window used to be "today minus 2000 days", so any transaction older than
~5.5 years silently converted at the 1.35 default rate. The rates stage
runs before the inputs are parsed, so it cannot know the earliest
transaction date; the fixed early floor covers any realistic ACB
history, both sources are cached (one download each, ever), and a date
the rates still don't cover is now a validation ERROR downstream.

CACHE: ~/.currency_price_cache.json. The legacy Yahoo entries keep their
`PAIR-YYYY-MM-DD` keys (forward-filled calendar dates); Bank of Canada
observations live under `_boc`, and `_coverage` records the date range
each source has been asked for (so a range with no data — Yahoo before
its history starts — is not re-requested every run). Older taxjson
versions read the file unchanged. A series the Valet API definitively
reports as not found ({"message": "Series FX<CUR>CAD not found."}) is
marked `not_published` with the date it was seen and asked again after
NOT_PUBLISHED_RECHECK_DAYS; a bare HTTP 404 (maintenance page, proxy) is
a failed fetch and never marks anything.

TAXJSON_OFFLINE=1: no fetch at all; rows come from the cache. The stage
never fails for want of a rate — only the dates a transaction actually
needs matter, and those are checked at conversion time.
"""
import argparse
import bisect
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

try:
    import yfinance as yf
except ImportError:                     # pragma: no cover
    yf = None

PROG = "taxjson-to-base-curr"
CACHE_FILE = os.path.expanduser("~/.currency_price_cache.json")

BOC_START = "2017-01-03"            # first observation of the Valet FX series
# Folio S5-F4-C1 para 1.4: a date before March 1, 2017 uses the Bank of
# Canada NOON rate. Valet still serves the legacy noon series from
# 2007-05-01 (audit R1-146 — those dates used Yahoo closes).
NOON_START = "2007-05-01"
NOON_BEFORE = "2017-03-01"
NOON_URL = ("https://www.bankofcanada.ca/valet/observations/"
            "{series}/json?start_date={start}&end_date={end}")
# Valet legacy noon series (group legacy_noon_rates) per currency.
NOON_SERIES = {
    "USD": "IEXE0101", "EUR": "EUROCAE01", "GBP": "IEXE1201",
    "AUD": "IEXE1601", "JPY": "IEXE0701", "CHF": "IEXE1101",
    "HKD": "IEXE1401", "NZD": "IEXE1901", "MXN": "IEXE2001",
    "RUB": "IEXE2101", "CNY": "IEXE2201", "PLN": "IEXE2401",
    "IDR": "IEXE2601", "BRL": "IEXE2801", "INR": "IEXE3001",
    "KRW": "IEXE3101", "MYR": "IEXE3201", "ZAR": "IEXE3401",
    "TWD": "IEXE3501", "THB": "IEXE3601", "SGD": "IEXE3701",
    "PEN": "IEXE5201", "TRY": "IEXE5802", "VND": "IEXE6503",
    "NOK": "IEXE0901", "SEK": "IEXE1001",
}
DEFAULT_START = "2000-01-01"
MAX_FILL_DAYS = 7                   # longest weekend/holiday forward-fill
TAIL_REFETCH_DAYS = 7               # re-ask BoC for the last week (late posts)
VALET_URL = ("https://www.bankofcanada.ca/valet/observations/"
             "{series}/json?start_date={start}&end_date={end}")
# Currencies with a Valet daily series FX<CUR>CAD (group FX_RATES_DAILY).
BOC_CURRENCIES = frozenset({
    "AUD", "BRL", "CHF", "CNY", "EUR", "GBP", "HKD", "IDR", "INR", "JPY",
    "KRW", "MXN", "MYR", "NOK", "NZD", "PEN", "PLN", "RUB", "SAR", "SEK",
    "SGD", "THB", "TRY", "TWD", "USD", "VND", "ZAR",
})

_CCY_RE = re.compile(r"^[A-Z]{3}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SeriesNotFound(Exception):
    """The Bank of Canada does not publish this series — raised only on
    the Valet API's own definitive answer (a JSON 404 whose message is
    "Series FX<CUR>CAD not found."), never on a bare HTTP 404."""


# A definitive "series not found" is remembered with the date it was
# seen and re-asked after this many days (R1-145): a series can come
# back, and a marker that never expires turned one bad answer into a
# machine-wide, permanent switch to Yahoo.
NOT_PUBLISHED_RECHECK_DAYS = 7


def _valet_not_found(exc: "urllib.error.HTTPError", series: str) -> bool:
    """True when an HTTP 404 is the Valet API's definitive answer for
    `series` ({"message": "Series FXUSDCAD not found."}). A maintenance
    page, proxy or CDN 404 has no such body: it is a failed fetch."""
    try:
        body = exc.read()
        doc = json.loads(body.decode("utf-8", "replace")) if body else None
    except (OSError, ValueError, AttributeError):
        return False
    msg = doc.get("message") if isinstance(doc, dict) else None
    if not isinstance(msg, str):
        return False
    return re.fullmatch(rf"\s*Series\s+{re.escape(series)}\s+not\s+"
                        rf"found\.?\s*", msg, re.IGNORECASE) is not None


# ------------------------------------------------------------ cache I/O

def load_cache():
    # OSError too: an unreadable cache degrades to a refetch.
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(cache_data):
    # tmp + os.replace (repo standard): a kill mid-dump must not leave
    # a truncated cache that the next run silently discards.
    tmp = CACHE_FILE + ".part"
    try:
        with open(tmp, 'w') as f:
            json.dump(cache_data, f, indent=2)
        os.replace(tmp, CACHE_FILE)
    except OSError as exc:
        print(f"{PROG}: warning: could not write {CACHE_FILE}: {exc}",
              file=sys.stderr)


# ------------------------------------------------------------ date helpers

def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _s(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _shift(s: str, days: int) -> str:
    return _s(_d(s) + timedelta(days=days))


def _days(a: str, b: str) -> int:
    return (_d(b) - _d(a)).days


# ------------------------------------------------------------ fetchers
# Both return REAL observations only: {YYYY-MM-DD: rate}. Module-level
# so tests replace them (the suite never touches the network).

def fetch_boc(currency: str, start: str, end: str) -> Dict[str, str]:
    """Bank of Canada Valet daily series FX<currency>CAD, start..end
    inclusive. Values are kept as the Bank's own decimal strings so the
    published rate reaches the rates file verbatim. Raises
    SeriesNotFound only on the Valet API's definitive "Series ... not
    found." 404; any other HTTP error (an HTML 404 from a maintenance
    page or proxy included) propagates as a failed fetch."""
    series = f"FX{currency}CAD"
    url = VALET_URL.format(series=series, start=start, end=end)
    req = urllib.request.Request(url, headers={"User-Agent": "taxjson"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            doc = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and _valet_not_found(exc, series):
            raise SeriesNotFound(series) from exc
        raise
    out: Dict[str, str] = {}
    for ob in doc.get("observations") or []:
        d = ob.get("d")
        v = (ob.get(series) or {}).get("v")
        if not (isinstance(d, str) and _DATE_RE.match(d)):
            continue
        try:
            if v is None or not float(v) > 0:
                continue
        except (TypeError, ValueError):
            continue
        out[d] = str(v).strip()
    return out


def fetch_boc_noon(currency: str, start: str, end: str) -> Dict[str, str]:
    """The Bank of Canada legacy NOON rate for `currency` (Valet
    series NOON_SERIES[currency]), start..end inclusive; {} for a
    currency without one."""
    series = NOON_SERIES.get(currency)
    if not series:
        return {}
    url = NOON_URL.format(series=series, start=start, end=end)
    req = urllib.request.Request(url, headers={"User-Agent": "taxjson"})
    with urllib.request.urlopen(req, timeout=30) as res:
        doc = json.loads(res.read().decode("utf-8"))
    out: Dict[str, str] = {}
    for ob in doc.get("observations") or []:
        d = ob.get("d")
        v = (ob.get(series) or {}).get("v")
        if not (isinstance(d, str) and _DATE_RE.match(d)):
            continue
        try:
            if v is None or not float(v) > 0:
                continue
        except (TypeError, ValueError):
            continue
        out[d] = str(v).strip()
    return out


def fetch_yahoo(ticker: str, start: str, end: str) -> Dict[str, float]:
    """Yahoo Finance daily closes for `ticker`, start..end inclusive."""
    if yf is None:
        raise RuntimeError("yfinance is not installed (pip install "
                           "'taxjson[fx]')")
    data = yf.download(ticker, start=start, end=_shift(end, 1),
                       progress=False)
    out: Dict[str, float] = {}
    if data is None or getattr(data, "empty", True) \
            or "Close" not in data.columns:
        return out
    close = data["Close"]
    if hasattr(close, "columns"):          # MultiIndex → one-column frame
        close = (close[ticker] if ticker in close.columns
                 else close.iloc[:, 0])
    for idx, val in close.items():
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")) or f <= 0:
            continue
        out[idx.strftime("%Y-%m-%d")] = f
    return out


# ------------------------------------------------------------ coverage
# Per source and pair, the date ranges already REQUESTED, as a sorted
# list of disjoint [lo, hi] intervals. A requested range with no data
# (Yahoo before its history starts) is not re-requested every run.

def _merge(intervals: List[List[str]]) -> List[List[str]]:
    out: List[List[str]] = []
    for lo, hi in sorted(intervals):
        if out and lo <= _shift(out[-1][1], 1):
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def _coverage(cache: dict, key: str) -> List[List[str]]:
    cov = (cache.get("_coverage") or {}).get(key)
    if not isinstance(cov, list):
        return []
    return _merge([list(iv) for iv in cov
                   if isinstance(iv, list) and len(iv) == 2
                   and all(isinstance(x, str) and _DATE_RE.match(x)
                           for x in iv) and iv[0] <= iv[1]])


def _add_coverage(cache: dict, key: str, lo: str, hi: str) -> None:
    cov = _coverage(cache, key) + [[lo, hi]]
    cache.setdefault("_coverage", {})[key] = _merge(cov)


def _missing_ranges(cov: List[List[str]], start: str,
                    end: str) -> List[Tuple[str, str]]:
    """Sub-ranges of [start, end] outside every covered interval."""
    out: List[Tuple[str, str]] = []
    cur = start
    for lo, hi in cov:
        if cur > end:
            break
        if hi < cur:
            continue
        if lo > cur:
            out.append((cur, min(end, _shift(lo, -1))))
        cur = max(cur, _shift(hi, 1))
    if cur <= end:
        out.append((cur, end))
    return out


def _reaches(cov: List[List[str]], d: str, today: str) -> bool:
    """`d` is inside a covered interval — or it is TODAY and yesterday
    is covered (today's rate is not posted until late afternoon ET, so
    today's row carries the last business day's rate, as it always
    has)."""
    if any(lo <= d <= hi for lo, hi in cov):
        return True
    return d == today and any(lo <= _shift(d, -1) <= hi for lo, hi in cov)


def _yahoo_obs(cache: dict, pair: str) -> Dict[str, float]:
    pre = f"{pair}-"
    out = {}
    for k, v in cache.items():
        if k.startswith(pre) and _DATE_RE.match(k[len(pre):]):
            try:
                out[k[len(pre):]] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def _yahoo_coverage(cache: dict, pair: str) -> List[List[str]]:
    key = f"yahoo:{pair}"
    if key in (cache.get("_coverage") or {}):
        return _coverage(cache, key)
    # Legacy cache (pre-`_coverage`): the old fetcher forward-filled
    # every calendar date of each fetched window, so contiguous runs of
    # keys are exactly the ranges it had asked for.
    return _merge([[k, k] for k in _yahoo_obs(cache, pair)])


def _boc_block(cache: dict, currency: str) -> dict:
    blk = cache.setdefault("_boc", {}).setdefault(f"{currency}CAD", {})
    if not isinstance(blk.get("obs"), dict):
        blk["obs"] = {}
    return blk


# ------------------------------------------------------------ refresh

def _not_published_fresh(blk: dict, today: str) -> bool:
    """A "series not found" marker still inside its re-check window. A
    marker without a date (written by taxjson before R1-145, possibly
    from a transient 404) is never fresh: it is re-probed."""
    if not blk.get("not_published"):
        return False
    checked = blk.get("not_published_checked")
    if not (isinstance(checked, str) and _DATE_RE.match(checked)):
        return False
    return 0 <= _days(checked, today) < NOT_PUBLISHED_RECHECK_DAYS


def refresh_boc(cache: dict, currency: str, start: str, end: str,
                today: str, fetch=None) -> Tuple[bool, List[str]]:
    """Fill the BoC cache for [max(start, BOC_START), end]. Returns
    (published, errors): published=False when the Bank answered that it
    has no such series (the caller falls back to Yahoo for the dates
    the cached Bank observations do not cover).

    Only the Valet API's definitive "not found" sets the marker, and it
    carries the date it was seen: after NOT_PUBLISHED_RECHECK_DAYS the
    series is asked again, and a successful answer clears it. A failed
    fetch (network, 5xx, a non-Valet 404) is an error for this run only
    — its dates stay unrated, never papered over with Yahoo."""
    fetch = fetch or fetch_boc
    blk = _boc_block(cache, currency)
    if _not_published_fresh(blk, today):
        return False, []
    had_marker = bool(blk.get("not_published"))
    dated_marker = had_marker and isinstance(
        blk.get("not_published_checked"), str)
    lo = max(start, BOC_START)
    if lo > end:
        return (not had_marker), []
    key = f"boc:{currency}CAD"
    cov = _coverage(cache, key)
    ranges = _missing_ranges(cov, lo, end)
    if had_marker and not ranges:
        # Re-probe a stale marker even when the cache covers the window:
        # one small request for the last covered week.
        ranges = [(max(lo, _shift(end, -TAIL_REFETCH_DAYS)), end)]
    if cov and ranges and ranges[-1][1] == end and end > cov[-1][1]:
        # Re-ask for the trailing week: the Bank posts by 16:30 ET, so
        # a day "covered" from another time zone may have been empty.
        ranges[-1] = (max(lo, _shift(cov[-1][1], -TAIL_REFETCH_DAYS)), end)
    yesterday = _shift(today, -1)
    errors: List[str] = []
    for a, b in ranges:
        try:
            got = fetch(currency, a, b)
        except SeriesNotFound:
            blk["not_published"] = True
            blk["not_published_checked"] = today
            return False, []
        except Exception as exc:          # network, HTTP 5xx, bad JSON
            errors.append(f"Bank of Canada FX{currency}CAD {a}..{b}: {exc}")
            if dated_marker:
                # A re-probe of a DEFINITIVE marker that could not get an
                # answer: keep honouring it this run (the Bank said so
                # last time) and ask again next run.
                return False, errors
            continue
        if had_marker:
            blk.pop("not_published", None)
            blk.pop("not_published_checked", None)
            had_marker = dated_marker = False
        blk["obs"].update(got)
        # Never mark today (or later) covered: today's rate is posted
        # late afternoon ET, so it is re-asked on the next run.
        hi = min(b, yesterday)
        if a <= hi:
            _add_coverage(cache, key, a, hi)
    return True, errors


def refresh_boc_noon(cache: dict, currency: str, start: str, end: str,
                     today: str, fetch=None) -> List[str]:
    """Fill the legacy noon-rate cache for [max(start, NOON_START),
    min(end, NOON_BEFORE - 1)]. The series is closed (it ends in April
    2017), so a covered range is never re-asked; a failed fetch is an
    error for this run (those dates fall back to Yahoo as before)."""
    if currency not in NOON_SERIES:
        return []
    fetch = fetch or globals()["fetch_boc_noon"]
    lo, hi = max(start, NOON_START), min(end, _shift(NOON_BEFORE, -1))
    if lo > hi:
        return []
    key = f"boc_noon:{currency}CAD"
    blk = cache.setdefault("_boc_noon", {}).setdefault(f"{currency}CAD", {})
    if not isinstance(blk.get("obs"), dict):
        blk["obs"] = {}
    errors: List[str] = []
    for a, b in _missing_ranges(_coverage(cache, key), lo, hi):
        try:
            got = fetch(currency, a, b)
        except Exception as exc:          # network, HTTP error, bad JSON
            errors.append(f"Bank of Canada noon {currency}CAD {a}..{b}: "
                          f"{exc}")
            continue
        if not got:
            continue                      # nothing to cache; asked again
        blk["obs"].update(got)
        _add_coverage(cache, key, a, b)
    return errors


def refresh_yahoo(cache: dict, pair: str, ranges: List[Tuple[str, str]],
                  today: str, fetch=None) -> List[str]:
    """Fill the legacy Yahoo cache keys for each needed range not yet
    requested. Stored forward-filled per calendar date (the historical
    cache layout), today excluded (a partial intraday bar)."""
    fetch = fetch or fetch_yahoo
    ticker = f"{pair}=X"
    yesterday = _shift(today, -1)
    errors: List[str] = []
    cov = _yahoo_coverage(cache, pair)
    todo: List[Tuple[str, str]] = []
    for a, b in ranges:
        todo += _missing_ranges(cov, a, b)
    for a, b in sorted(set(todo)):
        try:
            got = fetch(ticker, a, b)
        except Exception as exc:
            errors.append(f"Yahoo Finance {ticker} {a}..{b}: {exc}")
            continue
        existing = _yahoo_obs(cache, pair)
        if not got and not any(d > b for d in existing):
            # yfinance swallows a failed download (rate limit, network)
            # into an empty frame. Remember a range as "asked, no data"
            # only when the source is known to have data AFTER it (its
            # history simply starts later); otherwise it is a failure
            # for this run, asked again next run (audit S055-02 — one
            # hiccup used to leave the range unrated forever).
            errors.append(f"Yahoo Finance {ticker} {a}..{b}: no data "
                          f"returned (download failed?)")
            continue
        # Seed the forward-fill from the last cached value before `a`.
        prev = _prior(sorted(existing), _shift(a, -1))
        cur = existing[prev] if prev and _days(prev, a) <= MAX_FILL_DAYS \
            else None
        last_obs = prev if cur is not None else None
        d = a
        hi = min(b, yesterday)
        while d <= hi:
            if d in got:
                cur, last_obs = got[d], d
            if cur is not None and _days(last_obs, d) <= MAX_FILL_DAYS:
                cache[f"{pair}-{d}"] = cur
            d = _shift(d, 1)
        if a <= hi:
            # Record legacy coverage first so it is not lost when the
            # explicit `_coverage` entry is created.
            key = f"yahoo:{pair}"
            if key not in (cache.get("_coverage") or {}):
                cache.setdefault("_coverage", {})[key] = cov
            _add_coverage(cache, key, a, hi)
    return errors


# ------------------------------------------------------------ emission

def _prior(sorted_dates: List[str], d: str) -> Optional[str]:
    i = bisect.bisect_right(sorted_dates, d)
    return sorted_dates[i - 1] if i else None


def resolve_rows(cache: dict, from_curr: str, to_curr: str, start: str,
                 end: str, today: str) -> List[Tuple[str, str, str]]:
    """[(date, rate_text, source)] for every calendar date in
    [start, end] that has a rate under the source rules (module
    docstring). A date gets the most recent prior observation within
    MAX_FILL_DAYS, and only when the source's requested range covers
    it (today: covers yesterday) — an offline cache that ends a month
    ago does not smear its last rate over the month."""
    pair = f"{from_curr}{to_curr}"
    use_boc = to_curr == "CAD" and from_curr in BOC_CURRENCIES
    boc_obs: Dict[str, str] = {}
    boc_cov: List[List[str]] = []
    # The Bank answered "series not found": its CACHED observations are
    # still real Bank rates and keep their source; Yahoo fills only the
    # dates they do not reach (R1-145 — the marker used to switch every
    # date of the pair to Yahoo, cached Bank rates included).
    withdrawn = False
    if use_boc:
        blk = (cache.get("_boc") or {}).get(pair) or {}
        withdrawn = bool(blk.get("not_published"))
        boc_obs = blk.get("obs") or {}
        boc_cov = _coverage(cache, f"boc:{pair}")
    boc_dates = sorted(boc_obs)
    noon_obs: Dict[str, str] = {}
    noon_cov: List[List[str]] = []
    if to_curr == "CAD":
        noon_obs = ((cache.get("_boc_noon") or {}).get(pair) or {}).get(
            "obs") or {}
        noon_cov = _coverage(cache, f"boc_noon:{pair}")
    noon_dates = sorted(noon_obs)
    y_obs = _yahoo_obs(cache, pair)
    y_dates = sorted(y_obs)
    y_cov = _yahoo_coverage(cache, pair)

    rows: List[Tuple[str, str, str]] = []
    d = start
    last = min(end, today)
    while d <= last:
        val = src = None
        # Before March 2017 the CRA names the Bank's NOON rate (Folio
        # S5-F4-C1 para 1.4): it wins over the daily average (Jan-Feb
        # 2017) and over Yahoo wherever the cached noon series reaches.
        if d < NOON_BEFORE and noon_dates and _reaches(noon_cov, d, today):
            p = _prior(noon_dates, d)
            if p and _days(p, d) <= MAX_FILL_DAYS:
                rows.append((d, noon_obs[p], "boc-noon"))
                d = _shift(d, 1)
                continue
        boc_era = use_boc and d >= BOC_START
        boc_reach = boc_era and _reaches(boc_cov, d, today)
        if boc_reach:
            p = _prior(boc_dates, d)
            if p and _days(p, d) <= MAX_FILL_DAYS:
                val, src = boc_obs[p], "boc"
        # Yahoo inside the BoC era only where the Bank's REQUESTED range
        # has no recent observation (a stopped series) — never as a
        # stand-in for a Bank fetch that did not happen.
        if val is None and (not boc_era or boc_reach or withdrawn) \
                and _reaches(y_cov, d, today):
            p = _prior(y_dates, d)
            if p and _days(p, d) <= MAX_FILL_DAYS:
                # %.6g keeps significant digits for small-magnitude
                # rates (JPY→CAD ~0.00947 printed %.4f lost 0.5%).
                val, src = f"{y_obs[p]:.6g}", "yahoo"
        if val is not None:
            rows.append((d, val, src))
        d = _shift(d, 1)
    return rows


def _yahoo_needed(cache: dict, from_curr: str, start: str, end: str,
                  boc_published: bool) -> List[Tuple[str, str]]:
    """Date ranges the Yahoo fallback must cover for a CAD target."""
    if not boc_published:
        return [(start, end)]
    need: List[Tuple[str, str]] = []
    if start < BOC_START:
        need.append((start, min(end, _shift(BOC_START, -1))))
    pair = f"{from_curr}CAD"
    obs = sorted(((cache.get("_boc") or {}).get(pair) or {}).get("obs")
                 or {})
    for lo, hi in _coverage(cache, f"boc:{pair}"):
        lo, hi = max(lo, start, BOC_START), min(hi, end)
        gap: Optional[List[str]] = None
        d = lo
        while d <= hi:
            p = _prior(obs, d)
            if not p or _days(p, d) > MAX_FILL_DAYS:
                if gap is None:
                    gap = [d, d]
                gap[1] = d
            elif gap is not None:
                need.append((gap[0], gap[1]))
                gap = None
            d = _shift(d, 1)
        if gap is not None:
            need.append((gap[0], gap[1]))
    return need


def summarize(rows: List[Tuple[str, str, str]]) -> str:
    """'Bank of Canada Valet for N dates, Yahoo fallback for M (a..b)'."""
    counts: Dict[str, int] = {}
    span: Dict[str, List[str]] = {}
    for d, _v, src in rows:
        counts[src] = counts.get(src, 0) + 1
        sp = span.setdefault(src, [d, d])
        sp[1] = d
    parts = [f"Bank of Canada Valet for {counts.get('boc', 0)} dates",
             f"Yahoo fallback for {counts.get('yahoo', 0)}"]
    if counts.get("boc-noon"):
        parts.insert(1, f"Bank of Canada noon rate (before 2017-03) for "
                        f"{counts['boc-noon']}")
    if counts.get("yahoo"):
        parts[-1] += f" ({span['yahoo'][0]}..{span['yahoo'][1]})"
    return ", ".join(parts)


def _not_published_note(cache: dict, from_curr: str,
                        to_curr: str) -> Optional[str]:
    """Say which source a "series not found" pair uses, and why."""
    if to_curr != "CAD" or from_curr not in BOC_CURRENCIES:
        return None
    blk = (cache.get("_boc") or {}).get(f"{from_curr}CAD") or {}
    if not blk.get("not_published"):
        return None
    when = blk.get("not_published_checked")
    seen = (f"on {when}; asked again {NOT_PUBLISHED_RECHECK_DAYS} days "
            f"later" if when else "by an older taxjson, undated; asked "
            "again on the next online run")
    return (f"the Bank of Canada publishes no {from_curr}/CAD series (the "
            f"Valet API answered \"Series FX{from_curr}CAD not found\" "
            f"{seen}) — dates its cached observations cover keep the "
            f"Bank's rate, the rest use Yahoo Finance {from_curr}CAD=X.")


def build_rates(from_curr: str, to_curr: str, start: str, end: str, *,
                today: Optional[str] = None, offline: bool = False,
                fetch_boc_fn: Optional[Callable] = None,
                fetch_yahoo_fn: Optional[Callable] = None,
                fetch_noon_fn: Optional[Callable] = None,
                ) -> Tuple[List[Tuple[str, str, str]], List[str], List[str]]:
    """Refresh the cache as needed and resolve the rows. Returns
    (rows, errors, notes)."""
    today = today or _s(date.today())
    cache = load_cache()
    before = json.dumps(cache, sort_keys=True)
    errors: List[str] = []
    notes: List[str] = []
    pair = f"{from_curr}{to_curr}"
    if offline:
        notes.append("TAXJSON_OFFLINE is set — using cached rates only "
                     "(no download); a transaction whose date has no "
                     "cached rate fails at the conversion stage.")
        n = _not_published_note(cache, from_curr, to_curr)
        if n:
            notes.append(n)
    elif to_curr == "CAD":
        if fetch_noon_fn is None and fetch_boc_fn is not None:
            # An injected Bank fetcher (tests) without a noon one: no
            # noon source — never a live request behind a stub.
            fetch_noon_fn = lambda _c, _a, _b: {}          # noqa: E731
        errors += refresh_boc_noon(cache, from_curr, start, end, today,
                                   fetch_noon_fn)
        published = from_curr in BOC_CURRENCIES
        if published:
            published, errs = refresh_boc(cache, from_curr, start, end,
                                          today, fetch_boc_fn)
            errors += errs
        if not published:
            notes.append(_not_published_note(cache, from_curr, to_curr)
                         or f"the Bank of Canada publishes no "
                            f"{from_curr}/CAD series — using Yahoo "
                            f"Finance for {pair}.")
        need = _yahoo_needed(cache, from_curr, start, end, published)
        if need and yf is None and fetch_yahoo_fn is None:
            notes.append(f"yfinance is not installed, so there is no "
                         f"Yahoo fallback for {pair} "
                         f"{need[0][0]}..{need[-1][1]} (install "
                         f"'taxjson[fx]' if you have transactions then).")
        elif need:
            errors += refresh_yahoo(cache, pair, need, today, fetch_yahoo_fn)
    else:
        errors += refresh_yahoo(cache, pair, [(start, end)], today,
                                fetch_yahoo_fn)
    if json.dumps(cache, sort_keys=True) != before:
        save_cache(cache)
    return resolve_rows(cache, from_curr, to_curr, start, end, today), \
        errors, notes


def _spot_row(from_curr: str, to_curr: str) -> Optional[str]:
    """Non-CAD targets only (historical behaviour): an intraday spot
    row after the daily rows. convert-currency keeps the FIRST row per
    date, so it only matters when today has no daily row."""
    if yf is None:
        return None
    try:
        recent = yf.Ticker(f"{from_curr}{to_curr}=X").history(
            period="1d", interval="1m")
        if not recent.empty:
            now = datetime.now()
            return (f"{now:%Y-%m-%d} {now:%H:%M:%S} {from_curr} {to_curr} "
                    f"{float(recent['Close'].iloc[-1]):.6g} yahoo")
    except Exception:
        pass
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Print daily FX rates for a currency pair in the rates-file "
            "format read by taxjson-convert-currency "
            "(`YYYY-MM-DD HH:MM:SS FROM TO RATE SOURCE` per line). For a "
            "CAD target the Bank of Canada Valet daily rate is the "
            "primary source (from 2017-01-03; its legacy noon rate before "
            "2017-03-01, back to 2007-05-01); Yahoo Finance fills earlier "
            "dates and currencies the Bank does not publish. Other "
            "targets use Yahoo Finance. Weekends and holidays take the "
            "most recent prior business-day rate. Fetched values are "
            "cached in ~/.currency_price_cache.json. TAXJSON_OFFLINE=1 "
            "serves the cache only."),
    )
    parser.add_argument("from_currency", nargs="?", default="USD",
                        help="ISO source currency (default: USD).")
    parser.add_argument("to_currency", nargs="?", default="CAD",
                        help="ISO target currency (default: CAD).")
    parser.add_argument("--start", default=DEFAULT_START,
                        help=f"First date to emit (default: {DEFAULT_START}"
                             f" — earlier than any realistic ACB history).")
    parser.add_argument("--end", default=None,
                        help="Last date to emit (default: today).")
    args = parser.parse_args(argv)
    from_curr = args.from_currency.strip().upper()
    to_curr = args.to_currency.strip().upper()
    for c in (from_curr, to_curr):
        if not _CCY_RE.match(c):
            parser.error(f"not an ISO currency code: {c!r}")
    today = _s(date.today())
    end = args.end or today
    for label, val in (("--start", args.start), ("--end", end)):
        if not _DATE_RE.match(val):
            parser.error(f"{label} must be YYYY-MM-DD, got {val!r}")
    if to_curr != "CAD" and yf is None:
        print(f"{PROG} needs the [fx] extra for a {to_curr} target "
              f"(Yahoo Finance): pip install 'taxjson[fx]'",
              file=sys.stderr)
        return 1
    if from_curr == to_curr:
        return 0

    from taxjson.lib.offline import offline_enabled
    offline = offline_enabled()
    rows, errors, notes = build_rates(from_curr, to_curr, args.start, end,
                                      today=today, offline=offline)
    for d, val, src in rows:
        print(f"{d} 12:00:00 {from_curr} {to_curr} {val} {src}")
    if to_curr != "CAD" and not offline:
        spot = _spot_row(from_curr, to_curr)
        if spot:
            print(spot)
    for n in notes:
        print(f"{PROG}: note: {n}", file=sys.stderr)
    for e in errors:
        print(f"{PROG}: warning: download failed — {e}; dates it would "
              f"have covered have no rate this run.", file=sys.stderr)
    print(f"{PROG}: note: FX {from_curr}→{to_curr}: {summarize(rows)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
