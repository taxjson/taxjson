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
~5.5 years silently converted at the 1.35 default rate. `taxjson run`
passes --start: the earliest date written in the project's files, less
a few days (lib/rates_window) — a 2024 project no longer asks for 2000
onwards, nor Yahoo for the years before the Bank's series. A date the
rates still don't cover is a validation ERROR downstream.

CACHE: ~/.currency_price_cache.json. The legacy Yahoo entries keep their
`PAIR-YYYY-MM-DD` keys (forward-filled calendar dates); Bank of Canada
observations live under `_boc`, and `_coverage` records the date range
each source has been asked for (so a range with no data — Yahoo before
its history starts — is not re-requested every run). Only a complete
answer is recorded: an empty answer counts as "no data" only when the
source answers for the dates after it, and an answer cut off before the
range end records the dates it reached (the rest is asked again). Older taxjson
versions read the file unchanged. A series the Valet API definitively
reports as not found ({"message": "Series FX<CUR>CAD not found."}) is
marked `not_published` with the date it was seen and asked again after
NOT_PUBLISHED_RECHECK_DAYS; a bare HTTP 404 (maintenance page, proxy) is
a failed fetch and never marks anything.

TAXJSON_OFFLINE=1: no fetch at all; rows come from the cache. The stage
never fails for want of a rate — only the dates a transaction actually
needs matter, and those are checked at conversion time.
"""
from taxjson.lib.stage_msg import emit_line
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

from taxjson.lib.install_hint import extra_hint

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
SUSPECT_RECHECK_DAYS = 14           # re-ask an empty/truncated BoC answer
# An empty Bank answer for a range is the truth ("the series stopped",
# RUB since 2022) only when the series was already silent this long
# before the range. A shorter silence may itself be the product of an
# earlier degraded answer (re-audit A2-0393), so it is re-asked.
STOPPED_SERIES_DAYS = 45
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


class CacheProblem(str):
    """An entry of build_rates' `errors` that is a damaged cache value,
    not a failed download (printed with its own wording)."""


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
            with open(CACHE_FILE, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (ValueError, OSError):   # JSON or UTF-8 damage (S055-04)
            pass
    return {}


def save_cache(cache_data):
    # tmp + os.replace (repo standard): a kill mid-dump must not leave
    # a truncated cache that the next run silently discards.
    # A unique temp file renamed into place under a lock: two projects'
    # runs no longer share one `.part` name (re-audit A2-0233). Not
    # merged: this run may have dropped a damaged entry on purpose.
    from taxjson.lib.json_cache import save_json_cache
    save_json_cache(CACHE_FILE, cache_data, prog=PROG, indent=2)


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
        raise RuntimeError(f"yfinance is not installed ({extra_hint('fx')})")
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


def _uncover(cache: dict, key: str, d: str) -> None:
    """Remove date `d` from `key`'s coverage (asked again next fetch)."""
    if key not in (cache.get("_coverage") or {}):
        return
    out: List[List[str]] = []
    for lo, hi in _coverage(cache, key):
        if lo <= d <= hi:
            if lo < d:
                out.append([lo, _shift(d, -1)])
            if d < hi:
                out.append([_shift(d, 1), hi])
        else:
            out.append([lo, hi])
    cache["_coverage"][key] = out


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


def _drop_suspects(blk: dict, a: str, b: str) -> None:
    """Forget re-check markers inside [a, b] (just asked again)."""
    blk["suspect"] = [e for e in blk.get("suspect") or []
                      if not (a <= str(e[0]) and str(e[1]) <= b)]
    if not blk["suspect"]:
        blk.pop("suspect", None)


def refresh_boc(cache: dict, currency: str, start: str, end: str,
                today: str, fetch=None, notes: Optional[List[str]] = None
                ) -> Tuple[bool, List[str]]:
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
    # Degraded answers still inside their re-check window are asked
    # again (see below); older ones are accepted as the truth.
    suspect_seen: Dict[Tuple[str, str], str] = {}
    for ent in list(blk.get("suspect") or []):
        try:
            sa, sb, seen = (str(x) for x in ent)
        except (TypeError, ValueError):
            continue
        if _days(seen, today) > SUSPECT_RECHECK_DAYS:
            continue
        suspect_seen[(sa, sb)] = seen
        ra, rb = max(sa, lo), min(sb, end)
        if ra <= rb and not any(x <= ra and rb <= y for x, y in ranges):
            ranges.append((ra, rb))
    if suspect_seen:
        blk["suspect"] = [[a, b, v] for (a, b), v in suspect_seen.items()]
    else:
        blk.pop("suspect", None)
    ranges.sort()
    yesterday = _shift(today, -1)
    errors: List[str] = []
    asked: set = set()
    i = 0
    while i < len(ranges):
        a, b = ranges[i]
        i += 1
        if (a, b) in asked:
            continue
        asked.add((a, b))
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
        known_last = max(blk["obs"]) if blk["obs"] else None
        if got:
            # The series answers again. A gap longer than a holiday
            # between the cached observations and this answer, inside
            # a range already recorded as covered, was left by an
            # earlier empty or degraded answer (re-audit A2-0393): it is
            # asked again now. An empty re-ask becomes a suspect below.
            first = min(got)
            prev = _prior(sorted(blk["obs"]), _shift(first, -1))
            if prev and _days(prev, first) > MAX_FILL_DAYS + 1:
                ha, hb = max(_shift(prev, 1), lo), _shift(first, -1)
                if ha <= hb and not _missing_ranges(cov, ha, hb) \
                        and (ha, hb) not in asked:
                    ranges.append((ha, hb))
        blk["obs"].update(got)
        # Never mark today (or later) covered: today's rate is posted
        # late afternoon ET, so it is re-asked on the next run.
        hi = min(b, yesterday)
        if a > hi:
            continue
        # A 200 whose observations stop more than a weekend/holiday
        # short of the range end (none at all, or a truncated list) may
        # be a degraded answer rather than the truth (audit S055-03: it
        # was recorded as coverage for good, so a later healthy Bank
        # was never asked again and those dates kept a Yahoo close or a
        # stale forward-fill labelled 'boc'). The range still counts as
        # covered this run (a series that really stopped — RUB since
        # 2022 — keeps its Yahoo fallback), but its tail is re-asked on
        # every run for SUSPECT_RECHECK_DAYS, and a real answer fixes it.
        # A series already quiet before this range (its newest cached
        # observation is more than a week before `a`) has stopped: an
        # empty answer there is expected, not suspect.
        last = max(got) if got else _shift(a, -1)
        # Stopped: silent for weeks before this range and nothing cached
        # after it. A silence of a few weeks may be an earlier degraded
        # answer, and a series with observations after the range is
        # alive (re-audit A2-0393: a second empty answer used to read as
        # "stopped" and its dates stayed without a Bank rate for good).
        stopped = (known_last is not None and not got
                   and _days(known_last, a) > STOPPED_SERIES_DAYS
                   and not any(d > b for d in blk["obs"]))
        _drop_suspects(blk, a, hi)
        if _days(last, hi) > MAX_FILL_DAYS and not stopped:
            tail_a = max(a, _shift(last, 1))
            seen = min([v for (x, y), v in suspect_seen.items()
                        if x <= hi and tail_a <= y] or [today])
            blk.setdefault("suspect", []).append([tail_a, hi, seen])
            if notes is not None and seen == today:
                notes.append(
                    f"the Bank of Canada answered FX{currency}CAD "
                    f"{tail_a}..{hi} with no observations — those dates "
                    f"use the fallback this run and are asked again on "
                    f"each run for {SUSPECT_RECHECK_DAYS} days.")
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
        # The series is closed and every currency in NOON_SERIES runs to
        # April 2017, so an answer that stops more than a holiday short
        # of the range end was cut off: only the dates it reached are
        # recorded, and the rest is asked again (re-audit A2-0393 — the
        # whole range was recorded and never re-asked).
        last = max(got)
        if _days(last, b) > MAX_FILL_DAYS:
            _add_coverage(cache, key, a, last)
            errors.append(f"Bank of Canada noon {currency}CAD {a}..{b}: "
                          f"the answer stops at {last} (cut off?) — "
                          f"the rest is asked again on the next run")
            continue
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
        if not got and not _yahoo_history_starts_later(
                fetch, ticker, b, existing):
            # yfinance swallows a failed download (rate limit, network)
            # into an empty frame. Remember a range as "asked, no data"
            # only when the source, asked NOW, answers for the dates
            # after it (its history simply starts later); otherwise it
            # is a failure for this run, asked again next run (audit
            # S055-02; re-audit A2-0136 — later dates already in the
            # cache used to count as that proof, so a failed download
            # was remembered as "no data" for good).
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
            # An answer that stops more than a holiday short of the
            # range end was cut off: record only the dates it reached
            # and ask for the rest again (re-audit A2-0393). An answer
            # that STARTS late is the source's history starting later.
            if got and _days(max(got), hi) > MAX_FILL_DAYS:
                last = max(got)
                _add_coverage(cache, key, a, last)
                errors.append(f"Yahoo Finance {ticker} {a}..{b}: the "
                              f"answer stops at {last} (cut off?) — the "
                              f"rest is asked again on the next run")
                continue
            _add_coverage(cache, key, a, hi)
    return errors


def _yahoo_history_starts_later(fetch, ticker: str, b: str,
                                existing: Dict[str, float]) -> bool:
    """After an empty answer for a range ending `b`: whether the source
    has data after it RIGHT NOW (one probe up to the first cached
    observation after `b`). A working source with nothing in the range
    means its history starts later; a probe that also comes back empty
    (or fails) means the download failed."""
    later = sorted(d for d in existing if d > b)
    if not later:
        return False
    try:
        probe = fetch(ticker, _shift(b, 1),
                      max(later[0], _shift(b, MAX_FILL_DAYS)))
    except Exception:
        return False
    return bool(probe)


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


def _good_rate(v) -> bool:
    """A cached rate is a positive finite number (a string or a float;
    never a bool, list or text such as "1,3316")."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f > 0 and f != float("inf")      # NaN fails `f > 0`


def _valid_coverage(v) -> bool:
    """A `_coverage` entry is a list of [lo, hi] YYYY-MM-DD pairs."""
    return isinstance(v, list) and all(
        isinstance(iv, list) and len(iv) == 2
        and all(isinstance(x, str) and _DATE_RE.match(x) for x in iv)
        and iv[0] <= iv[1] for iv in v)


def _drop_bad_shapes(cache: dict, offline: bool) -> List[str]:
    """Remove the parts of the cache whose SHAPE is damaged — a
    `_coverage`, `_boc` or `_boc_noon` that is not an object, a pair's
    block or its `obs` that is not an object, a coverage entry that is
    not a list of [lo, hi] date pairs — with one CacheProblem each,
    naming the cache file. What is dropped is asked for again online;
    offline its dates have no row. A damaged block used to be an
    AttributeError traceback in the FX stage, and a damaged coverage
    entry silently discarded the whole cached Bank series (re-audit
    A2-0474 / A2-0800 / A2-1446)."""
    out: List[str] = []
    then = ("its dates have no rate this run (TAXJSON_OFFLINE) and are "
            "asked for again online" if offline
            else "its dates are asked for again")

    def bad(what: str, v) -> None:
        out.append(CacheProblem(
            f"{CACHE_FILE}: {what} is damaged ({str(v)[:40]!r}) — "
            f"dropped; {then}."))

    cov = cache.get("_coverage")
    if cov is not None and not isinstance(cov, dict):
        bad("_coverage", cov)
        del cache["_coverage"]
    for key in list((cache.get("_coverage") or {})):
        if not _valid_coverage(cache["_coverage"][key]):
            bad(f"_coverage[{key!r}]", cache["_coverage"][key])
            del cache["_coverage"][key]
    for top, cov_pre in (("_boc", "boc"), ("_boc_noon", "boc_noon")):
        blocks = cache.get(top)
        if blocks is None:
            continue
        if not isinstance(blocks, dict):
            bad(top, blocks)
            del cache[top]
            for key in [k for k in (cache.get("_coverage") or {})
                        if k.startswith(f"{cov_pre}:")]:
                del cache["_coverage"][key]
            continue
        for pair in list(blocks):
            blk = blocks[pair]
            if isinstance(blk, dict) and (blk.get("obs") is None
                                          or isinstance(blk["obs"], dict)):
                continue
            bad(f"{top}[{pair!r}]", blk)
            del blocks[pair]
            (cache.get("_coverage") or {}).pop(f"{cov_pre}:{pair}", None)
    return out


def _drop_bad_obs(cache: dict, pair: str, offline: bool) -> List[str]:
    """Remove cached observations for `pair` that are not a positive
    finite number, and the coverage of their dates (an online run asks
    the source again). One CacheProblem per bad value, naming the cache
    file and the date (re-audit A2-1212 — a value such as "abc" was
    copied verbatim into the rates file, and the run then blamed the
    config: "no rates at all for USD")."""
    out: List[str] = []
    then = ("that date has no row this run (TAXJSON_OFFLINE) and is asked "
            "for again online" if offline else "that date is asked for again")
    blocks = (("_boc", f"boc:{pair}", "Bank of Canada"),
              ("_boc_noon", f"boc_noon:{pair}", "Bank of Canada noon"))
    for top, key, label in blocks:
        blk = (cache.get(top) or {}).get(pair)
        obs = blk.get("obs") if isinstance(blk, dict) else None
        if not isinstance(obs, dict):
            continue
        for d in sorted(obs):
            if _good_rate(obs[d]):
                continue
            out.append(CacheProblem(
                f"{CACHE_FILE} holds a {label} {pair} rate for {d} that "
                f"is not a positive number ({obs[d]!r}) — dropped; "
                f"{then}."))
            del obs[d]
            if _DATE_RE.match(str(d)):
                _uncover(cache, key, d)
    pre = f"{pair}-"
    for k in sorted(k for k in cache if isinstance(k, str)
                    and k.startswith(pre) and _DATE_RE.match(k[len(pre):])):
        if _good_rate(cache[k]):
            continue
        d = k[len(pre):]
        out.append(CacheProblem(
            f"{CACHE_FILE} holds a Yahoo {pair} rate for {d} that is not "
            f"a positive number ({cache[k]!r}) — dropped; {then}."))
        if f"yahoo:{pair}" not in (cache.get("_coverage") or {}):
            cache.setdefault("_coverage", {})[f"yahoo:{pair}"] = \
                _yahoo_coverage(cache, pair)
        del cache[k]
        _uncover(cache, f"yahoo:{pair}", d)
    return out


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
    errors += _drop_bad_shapes(cache, offline)
    errors += _drop_bad_obs(cache, pair, offline)
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
                                          today, fetch_boc_fn, notes=notes)
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
                         f"{need[0][0]}..{need[-1][1]} (if you have "
                         f"transactions then, {extra_hint('fx')}).")
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
    from taxjson.lib.offline import offline_enabled
    offline = offline_enabled()
    # Offline the rows come from the cache only, so the missing Yahoo
    # library is no reason to stop (it used to: a USD-base project
    # without the [fx] extra failed even with every rate cached).
    if to_curr != "CAD" and yf is None and not offline:
        emit_line(f"{PROG} needs the [fx] extra for a {to_curr} target "
              f"(Yahoo Finance): {extra_hint('fx')}")
        return 1
    if from_curr == to_curr:
        return 0

    rows, errors, notes = build_rates(from_curr, to_curr, args.start, end,
                                      today=today, offline=offline)
    for d, val, src in rows:
        print(f"{d} 12:00:00 {from_curr} {to_curr} {val} {src}")
    if to_curr != "CAD" and not offline:
        spot = _spot_row(from_curr, to_curr)
        if spot:
            print(spot)
    for n in notes:
        emit_line(f"{PROG}: note: {n}")
    for e in errors:
        if isinstance(e, CacheProblem):
            emit_line(f"{PROG}: warning: {e}")
            continue
        emit_line(f"{PROG}: warning: download failed — {e}; dates it would "
              f"have covered have no rate this run.")
    emit_line(f"{PROG}: note: FX {from_curr}→{to_curr}: {summarize(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
