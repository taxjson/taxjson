"""The shared FX rate cache in $HOME (~/.currency_price_cache.json:
load_cache) and the rate each date resolves to from it (resolve_rows: the most
recent prior observation within MAX_FILL_DAYS, under the source rules
bin/to_base_curr.py's docstring gives). bin/to_base_curr.py fills the cache
and prints the rates file; lib/slip_audit reads a year's average. A test
isolates the cache by patching CACHE_FILE here: the loader reads it from
this module.
"""
import bisect
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple


CACHE_FILE = os.path.expanduser("~/.currency_price_cache.json")


BOC_START = "2017-01-03"            # first observation of the Valet FX series


NOON_BEFORE = "2017-03-01"


MAX_FILL_DAYS = 7                   # longest weekend/holiday forward-fill


# Currencies with a Valet daily series FX<CUR>CAD (group FX_RATES_DAILY).
BOC_CURRENCIES = frozenset({
    "AUD", "BRL", "CHF", "CNY", "EUR", "GBP", "HKD", "IDR", "INR", "JPY",
    "KRW", "MXN", "MYR", "NOK", "NZD", "PEN", "PLN", "RUB", "SAR", "SEK",
    "SGD", "THB", "TRY", "TWD", "USD", "VND", "ZAR",
})


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_date(s) -> bool:
    """`s` is a real calendar date written YYYY-MM-DD. The shape alone
    is not enough: '2025-02-30' matched _DATE_RE, and the date arithmetic
    below then raised ValueError ("day is out of range for month") out
    of the cache repair and the command (backlog TJ-0584). THE check for
    every date read from outside: the cache here, and an answer and
    --start/--end in bin/to_base_curr."""
    if not (isinstance(s, str) and _DATE_RE.match(s)):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return False
    return True


# ------------------------------------------------------------ cache I/O

class LoadedCache(dict):
    """The rate cache as load_cache read it. `loaded_text` keeps what
    the file held then, so save_cache can write back only what this run
    changed (TJ-0583)."""
    loaded_text = "{}"


def load_cache():
    # OSError too: an unreadable cache degrades to a refetch.
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8-sig') as f:
                text = f.read()
            data = json.loads(text)
            if isinstance(data, dict):
                out = LoadedCache(data)
                out.loaded_text = text
                return out
        except (ValueError, OSError):   # JSON or UTF-8 damage (S055-04)
            pass
    return LoadedCache()


# ------------------------------------------------------------ date helpers

def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _s(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _shift(s: str, days: int) -> str:
    return _s(_d(s) + timedelta(days=days))


def _days(a: str, b: str) -> int:
    return (_d(b) - _d(a)).days


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
                   and all(_is_date(x) for x in iv)
                   and iv[0] <= iv[1]])


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
        if k.startswith(pre) and _is_date(k[len(pre):]):
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


def _prior(sorted_dates: List[str], d: str) -> Optional[str]:
    i = bisect.bisect_right(sorted_dates, d)
    return sorted_dates[i - 1] if i else None


def _yahoo_run_start(obs: Dict[str, float], d: str) -> str:
    """The earliest date the Yahoo rate cached for `d` may have been
    observed. The cache stores refresh_yahoo's forward-fills as plain
    calendar-date keys, so a fill cannot be told from an observation:
    the start of the unbroken run of days carrying the same rate is the
    oldest it can be (a rate that really repeated only shortens a fill).
    Walks back no further than MAX_FILL_DAYS + 1 days (backlog TJ-0843)."""
    v = obs[d]
    first = d
    for _ in range(MAX_FILL_DAYS + 1):
        q = _shift(first, -1)
        if obs.get(q) != v:
            break
        first = q
    return first


def resolve_rows(cache: dict, from_curr: str, to_curr: str, start: str,
                 end: str, today: str, with_obs: bool = False
                 ) -> List[Tuple[str, ...]]:
    """[(date, rate_text, source)] for every calendar date in
    [start, end] that has a rate under the source rules (module
    docstring). A date gets the most recent prior observation within
    MAX_FILL_DAYS, and only when the source's requested range covers
    it (today: covers yesterday) — an offline cache that ends a month
    ago does not smear its last rate over the month. `with_obs`: a
    fourth item, the date the rate was published (the Bank's; Yahoo's
    cache keeps no publication date: the date itself) — backlog
    TJ-0462."""
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
        obs = d
        # Before March 2017 the CRA names the Bank's NOON rate (Folio
        # S5-F4-C1 para 1.4): it wins over the daily average (Jan-Feb
        # 2017) and over Yahoo wherever the cached noon series reaches.
        if d < NOON_BEFORE and noon_dates and _reaches(noon_cov, d, today):
            p = _prior(noon_dates, d)
            if p and _days(p, d) <= MAX_FILL_DAYS:
                rows.append((d, noon_obs[p], "boc-noon")
                            + ((p,) if with_obs else ()))
                d = _shift(d, 1)
                continue
        boc_era = use_boc and d >= BOC_START
        boc_reach = boc_era and _reaches(boc_cov, d, today)
        if boc_reach:
            p = _prior(boc_dates, d)
            if p and _days(p, d) <= MAX_FILL_DAYS:
                val, src, obs = boc_obs[p], "boc", p
        # Yahoo inside the BoC era only where the Bank's REQUESTED range
        # has no recent observation (a stopped series) — never as a
        # stand-in for a Bank fetch that did not happen.
        if val is None and (not boc_era or boc_reach or withdrawn) \
                and _reaches(y_cov, d, today):
            # A cached day is used as it is (refresh_yahoo already
            # filled it, within MAX_FILL_DAYS of its observation); a
            # day with none is filled from the latest one before only
            # within MAX_FILL_DAYS of the observation that one may
            # carry, never of the fill itself — filling again from a
            # fill let a rate reach 19 days into a converter that
            # promises 12 (backlog TJ-0843).
            p = d if d in y_obs else _prior(y_dates, d)
            if p and (p == d or _days(_yahoo_run_start(y_obs, p), d)
                      <= MAX_FILL_DAYS):
                # %.6g keeps significant digits for small-magnitude
                # rates (JPY→CAD ~0.00947 printed %.4f lost 0.5%).
                val, src = f"{y_obs[p]:.6g}", "yahoo"
        if val is not None:
            rows.append((d, val, src) + ((obs,) if with_obs else ()))
        d = _shift(d, 1)
    return rows
