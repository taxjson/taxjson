"""A synthetic HOME for every test process: no developer files, no network.

The suite used to pass on the maintainer's machine and fail on every
GitHub runner: `tjs run` reads its exchange rates from
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
and `_hermetic` sorts first), so the switch is made before a test module
imports taxjson (lib/rate_cache and lib/crypto_prices read HOME at
import, and `CacheHome` runs that under this HOME whatever HOME the
environment holds: TJ-3889). The helpers that build projects (_style,
_qa_project, tax_rules), _planted_rates and the shared helpers' package
(_support) import it too, and a test module that imports lib/rate_cache
or lib/crypto_prices (or taxjson.bin's cache fillers, which load them)
imports it before them: a module run alone (`python -m unittest test_x`)
that loads one of those gets it first (test_test_layout checks it:
TJ-3857). A module that imports none of these runs alone with the HOME
it was given (TJ-3858): run one module as `python -m unittest _hermetic
test_x`, as the parallel runner does. Idempotent: a process that already
has one (TAXJSON_TEST_HOME, inherited from the test that started it)
keeps it. The setup imports nothing of taxjson.
"""
import atexit
import datetime
import json
import os
import shutil
import sys
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
# The sets a HOME can be made with (`home_env`). fx4: four places that
# do not end in zeros, so a converted figure carries six places and the
# gains file's 4-place cut can make a half cent that the engine's value
# is not (tests/_views_ab.py, step 6h). SYNTHETIC_RATES's two places
# never can.
RATE_SETS = {
    "default": SYNTHETIC_RATES,
    "fx4": {"USDCAD": "1.3457", "EURCAD": "1.4983", "GBPCAD": "1.7123",
            "CADUSD": "0.7431", "EURUSD": "1.1087", "GBPUSD": "1.2641"},
}
BOC_START = "2017-01-03"       # lib/rate_cache.BOC_START
FIRST = "2000-01-01"           # to_base_curr.DEFAULT_START


def _weekdays(lo: datetime.date, hi: datetime.date):
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            yield d.isoformat()
        d += datetime.timedelta(days=1)


def rate_cache(today=None, rates=None) -> dict:
    """The synthetic ~/.currency_price_cache.json: every pair of `rates`
    (SYNTHETIC_RATES by default) on every weekday FIRST..today+7, as
    observations the cache's coverage says were asked for. CAD targets:
    Bank of Canada rows from BOC_START, Yahoo rows before; other
    targets: Yahoo rows throughout."""
    rates = SYNTHETIC_RATES if rates is None else rates
    today = today or datetime.date.today()
    lo = datetime.date.fromisoformat(FIRST)
    hi = today + datetime.timedelta(days=7)
    boc_lo = datetime.date.fromisoformat(BOC_START)
    cache = {"_coverage": {}, "_boc": {}}
    for pair, rate in rates.items():
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


def home_env(home: str, rates: str = "default") -> dict:
    """Make `home` a synthetic HOME whose rate cache holds the set `rates`
    (RATE_SETS): the environment entries that point a process at it."""
    if rates not in RATE_SETS:
        raise ValueError(f"rates {rates!r}: one of {', '.join(RATE_SETS)}")
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, ".currency_price_cache.json"), "w",
              encoding="utf-8") as f:
        json.dump(rate_cache(rates=RATE_SETS[rates]), f)
    env = home_vars(home)
    for k in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        os.makedirs(env[k], exist_ok=True)
    return env


def home_vars(home: str) -> dict:
    """The environment entries that point a process at the synthetic HOME
    `home` (made by `home_env`), writing nothing: HOME, the XDG folders
    below it, MARK."""
    env = {"HOME": home, MARK: home,
           "XDG_CACHE_HOME": os.path.join(home, ".cache"),
           "XDG_CONFIG_HOME": os.path.join(home, ".config"),
           "XDG_DATA_HOME": os.path.join(home, ".local", "share")}
    if os.name == "nt":
        env["USERPROFILE"] = home
    return env


def environ(**extra: str) -> dict:
    """The environment as the setup left it (`ENVIRON`), with EXTRA set:
    the base of a subprocess environment a helper builds at its import.
    Not `os.environ` there: a test that sets a variable and is the first
    to import the helper would leave it in every later run (TJ-3951)."""
    return dict(ENVIRON, **extra)


def install() -> str:
    """Point this process (and every subprocess it starts) at a private
    synthetic HOME, offline. Returns the folder."""
    home = os.environ.get(MARK)
    if home and os.path.isdir(home) and os.environ.get("HOME") == home:
        return home
    home = tempfile.mkdtemp(prefix="taxjson-home-")
    atexit.register(shutil.rmtree, home, True)
    os.environ.update(home_env(home))
    os.environ["TAXJSON_OFFLINE"] = "1"
    # Unwrapped, full-length messages, as scripts/ci.sh runs the suite:
    # a GitHub runner sets no TAXJSON_WIDTH, and at a terminal width the
    # essentials-first output cuts long messages to one line. A test of
    # the cut view sets its own width (tests/_style.CapturedWidth).
    os.environ.setdefault("TAXJSON_WIDTH", "0")
    os.environ[MARK] = home
    return home


class _UnderHome:
    """Another loader, whose module code runs with the environment pointing
    at the synthetic HOME `home`. Each key is then put back unless it no
    longer holds what this import set (another thread's import of the
    other cache, or the test, changed it since: TJ-3915). The swap is the
    process's: a thread that reads HOME while the module code runs sees the
    synthetic one."""

    def __init__(self, loader, home: str):
        self._loader, self._home = loader, home

    def __getattr__(self, name):        # get_source, get_resource_reader, ...
        if name.startswith("__") or name in ("_loader", "_home"):
            raise AttributeError(name)  # a copy or pickle (TJ-3917)
        return getattr(self._loader, name)

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        env = home_vars(self._home)
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            self._loader.exec_module(module)
        finally:
            for k, v in saved.items():
                if os.environ.get(k) != env[k]:
                    continue
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class CacheHome:
    """The product's two caches name their file from HOME at import
    (lib/rate_cache, lib/crypto_prices: `CACHE_FILE`). This finder runs
    their module code under the setup's HOME, whatever HOME the environment
    holds by then, so a test that patches HOME and is the first to
    import one cannot leave it naming the test's own folder for the tests
    after it (TJ-3889). It imports nothing itself: taxjson still comes from
    the path a test module put first (TJ-3891). Every import of the two
    names in this process is served so, a reload too: a test of how
    `CACHE_FILE` follows HOME runs a process without this setup. A patch
    of `os.path.expanduser` itself is not covered. The names are the
    product modules that read HOME at import (tests/test_test_layout.py
    checks them: TJ-3916)."""

    NAMES = ("taxjson.lib.rate_cache", "taxjson.lib.crypto_prices")

    def __init__(self, home: str):
        self.home = home

    def find_spec(self, name, path=None, target=None):
        if name not in self.NAMES:
            return None
        for finder in sys.meta_path:
            find = getattr(finder, "find_spec", None)
            if finder is self or find is None:
                continue
            spec = find(name, path, target)
            if spec is not None:
                break
        else:
            return None
        if spec.loader is not None and hasattr(spec.loader, "exec_module"):
            spec.loader = _UnderHome(spec.loader, self.home)
        return spec


# The folder, kept here: a test may patch the environment, record and all.
HOME = install()
# The environment as the setup leaves it (`environ`).
ENVIRON = dict(os.environ)
# One finder, with the newest setup's HOME (a second copy of this module,
# imported without the record, made a new one: TJ-3920).
for _finder in sys.meta_path:
    if type(_finder).__name__ == "CacheHome":
        _finder.home = HOME
        break
else:
    sys.meta_path.insert(0, CacheHome(HOME))
