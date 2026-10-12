"""A synthetic HOME for every test process: no developer files, no network.

The suite used to pass on the maintainer's machine and fail on every
GitHub runner: `taxjson run` reads its exchange rates from
~/.currency_price_cache.json, and the maintainer's real cache (filled by
years of online runs) answered every date a test project needed, while
a fresh runner had no cache, no [fx] extra and a live Bank of Canada
call. A test now never sees the developer's HOME:

  * HOME (and XDG_CACHE_HOME / XDG_CONFIG_HOME / XDG_DATA_HOME, and
    USERPROFILE on Windows) point at a private folder made for this
    process and removed when it exits; a `taxjson` subprocess a test
    starts inherits it.
  * That folder holds a SYNTHETIC rate cache (rate_cache below): one
    constant made-up rate per currency pair, every weekday from
    2000-01-01 to a week past today, recorded as the Bank of Canada /
    Yahoo coverage taxjson-to-base-curr reads. No rate in it is real.
  * TAXJSON_WIDTH=0 unless set (full messages, as the gate runs them).
  * TAXJSON_OFFLINE=1, so no stage downloads anything: rates, prices
    and crypto prices come from the caches only. A test of an online
    path injects its fetcher (or clears the variable itself).

`python -m unittest discover -s tests` imports this package before any
test module (discovery imports the packages it finds, in sorted order,
and `_hermetic` sorts first), so the switch is made before a test
module imports taxjson (to_base_curr reads HOME at import). The helpers
that build projects (_style, _qa_project, tax_rules) import it too, so
a single module run alone (`python -m unittest test_x`) gets it as
well. Idempotent: a process that already has one (TAXJSON_TEST_HOME,
inherited from the test that started it) keeps it.
"""
import atexit
import datetime
import json
import os
import shutil
import tempfile

MARK = "TAXJSON_TEST_HOME"

# Synthetic constant rates (made up; units of the second currency per
# unit of the first). The Bank of Canada pairs are the ones a CAD base
# reads, the Yahoo pairs the ones a USD base reads (and a CAD base
# before the Bank's series starts).
SYNTHETIC_RATES = {
    "USDCAD": "1.3500", "EURCAD": "1.5000", "GBPCAD": "1.7000",
    "CADUSD": "0.7407", "EURUSD": "1.1100", "GBPUSD": "1.2600",
}
BOC_START = "2017-01-03"       # to_base_curr.BOC_START
FIRST = "2000-01-01"           # to_base_curr.DEFAULT_START


def _weekdays(lo: datetime.date, hi: datetime.date):
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            yield d.isoformat()
        d += datetime.timedelta(days=1)


def rate_cache(today=None) -> dict:
    """The synthetic ~/.currency_price_cache.json: every pair of
    SYNTHETIC_RATES on every weekday FIRST..today+7, as observations
    the cache's coverage says were asked for. CAD targets: Bank of
    Canada rows from BOC_START, Yahoo rows before; other targets:
    Yahoo rows throughout."""
    today = today or datetime.date.today()
    lo = datetime.date.fromisoformat(FIRST)
    hi = today + datetime.timedelta(days=7)
    boc_lo = datetime.date.fromisoformat(BOC_START)
    cache = {"_coverage": {}, "_boc": {}}
    for pair, rate in SYNTHETIC_RATES.items():
        if pair.endswith("CAD"):
            cache["_boc"][pair] = {"obs": {d: rate for d in
                                           _weekdays(boc_lo, hi)}}
            cache["_coverage"][f"boc:{pair}"] = [[BOC_START, hi.isoformat()]]
            y_hi = boc_lo - datetime.timedelta(days=1)
        else:
            y_hi = hi
        for d in _weekdays(lo, y_hi):
            cache[f"{pair}-{d}"] = float(rate)
        cache["_coverage"][f"yahoo:{pair}"] = [[FIRST, y_hi.isoformat()]]
    return cache


def install() -> str:
    """Point this process (and every subprocess it starts) at a private
    synthetic HOME, offline. Returns the folder."""
    home = os.environ.get(MARK)
    if home and os.path.isdir(home) and os.environ.get("HOME") == home:
        return home
    home = tempfile.mkdtemp(prefix="taxjson-home-")
    atexit.register(shutil.rmtree, home, True)
    with open(os.path.join(home, ".currency_price_cache.json"), "w",
              encoding="utf-8") as f:
        json.dump(rate_cache(), f)
    xdg = {"XDG_CACHE_HOME": os.path.join(home, ".cache"),
           "XDG_CONFIG_HOME": os.path.join(home, ".config"),
           "XDG_DATA_HOME": os.path.join(home, ".local", "share")}
    for d in xdg.values():
        os.makedirs(d, exist_ok=True)
    os.environ.update(xdg)
    os.environ["HOME"] = home
    if os.name == "nt":
        os.environ["USERPROFILE"] = home
    os.environ["TAXJSON_OFFLINE"] = "1"
    # Unwrapped, full-length messages, as scripts/ci.sh runs the suite:
    # a GitHub runner sets no TAXJSON_WIDTH, and at a terminal width the
    # essentials-first output cuts long messages to one line. A test of
    # the cut view sets its own width (tests/_style.CapturedWidth).
    os.environ.setdefault("TAXJSON_WIDTH", "0")
    os.environ[MARK] = home
    return home


install()
