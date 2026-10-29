"""The 2026-10-29 fix round 2, caches: the price, close and FX-rate
caches, `tjs init`'s half-written project and `tjs opening`'s
replacement of a user file (backlog TJ-0575, TJ-0576, TJ-0577,
TJ-0578, TJ-0583, TJ-0584, TJ-0568, TJ-0586). Synthetic data only:
made-up tickers (SYN.US, SYN.TO, SYN.L) in throwaway folders; no test
touches the network (the live tiers are stubbed)."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from taxjson.lib import price_chain as PC
from _style import CapturedWidth

# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _iso(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _tier(results):
    def _fetch(remaining):
        return {s: results[s] for s in remaining if s in results}
    return _fetch


# ------------------------------------------- TJ-0575: close cache unit

class TestCloseCacheUnit(unittest.TestCase):
    """TJ-0575: a close cache entry naming a unit taxjson cannot read
    ('USd') was served with the listing's currency (125 USD); the live
    tier refuses the same unit. Now it is a miss: fetched again online,
    OfflineCloseMissing offline."""

    KEY = "SYN.US|SYN@2025-03-03"

    def _cache(self, td, currency):
        cache = Path(td) / "close.json"
        cache.write_text(json.dumps({self.KEY: {
            "price": 125, "day": "2025-03-03", "currency": currency,
            "unit": "provider"}}))
        return cache

    def test_bad_unit_offline_is_a_miss(self):
        with tempfile.TemporaryDirectory() as td:
            cache = self._cache(td, "USd")
            err = io.StringIO()
            with redirect_stderr(err), \
                    self.assertRaises(PC.OfflineCloseMissing):
                PC.close_on("SYN.US", "SYN", "2025-03-03",
                            cache_path=cache, offline=True)
        self.assertIn("USd", err.getvalue())
        self.assertIn("close.json", err.getvalue())

    def test_bad_unit_is_fetched_again(self):
        with tempfile.TemporaryDirectory() as td:
            cache = self._cache(td, "USd")
            with redirect_stderr(io.StringIO()):
                got = PC.close_on(
                    "SYN.US", "SYN", "2025-03-03", cache_path=cache,
                    fetchers=[lambda pairs, s, e: {
                        "SYN.US": ({"2025-03-03": 1.25}, "USD")}])
            doc = json.loads(cache.read_text())
        self.assertEqual((got.price, got.currency, got.source),
                         (1.25, "USD", "yfinance"))
        self.assertEqual(doc[self.KEY]["currency"], "USD")

    def test_good_unit_still_served(self):
        with tempfile.TemporaryDirectory() as td:
            cache = self._cache(td, "usd")
            got = PC.close_on("SYN.US", "SYN", "2025-03-03",
                              cache_path=cache, offline=True)
        self.assertEqual((got.price, got.currency, got.source),
                         (125.0, "USD", "cache"))


# ------------------------------------------ TJ-0576: a quote from 2099

class TestFutureCachedQuote(unittest.TestCase):
    """TJ-0576: an entry dated 2099 was served as current
    ('cache:-26362d', no warning). A date after today (one day of clock
    skew allowed) is a miss, said naming the cache file."""

    def _fetch(self, td, asof):
        cache = Path(td) / "prices.json"
        cache.write_text(json.dumps({"SYN.US": {
            "price": 15, "asof": asof, "currency": "USD",
            "quote_symbol": "SYN"}}))
        err = io.StringIO()
        with redirect_stderr(err):
            q = PC.fetch_prices({"SYN.US": "SYN"}, cache_path=cache,
                                fetchers=[_tier({})])
        return q, err.getvalue()

    def test_far_future_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            q, err = self._fetch(td, "2099-01-01")
        self.assertNotIn("SYN.US", q)
        self.assertIn("dated 2099-01-01, after today", err)
        self.assertIn("prices.json", err)

    def test_offline_far_future_entry_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "prices.json"
            cache.write_text(json.dumps({"SYN.US": {
                "price": 15, "asof": _iso(30), "currency": "USD",
                "quote_symbol": "SYN"}}))
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                PC.fetch_prices({"SYN.US": "SYN"}, cache_path=cache,
                                offline=True)

    def test_tomorrow_is_clock_skew_and_served_as_today(self):
        with tempfile.TemporaryDirectory() as td:
            q, err = self._fetch(td, _iso(1))
        self.assertEqual(q["SYN.US"].price, 15.0)
        self.assertEqual(q["SYN.US"].source, "cache:0d")
        self.assertNotIn("after today", err)

    def test_past_entry_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            q, _err = self._fetch(td, _iso(-2))
        self.assertEqual(q["SYN.US"].source, "cache:2d")


# ---------------------------------------- TJ-0577: JSON true is not 1

class TestBooleanPrice(unittest.TestCase):
    """TJ-0577: float(True) is 1.0, so a tier or a cache entry that
    said `true` was a price of 1, cached. Both paths now refuse a bool
    (the next tier is asked; the entry is a miss)."""

    def test_live_true_is_refused_and_next_tier_asked(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "prices.json"
            err = io.StringIO()
            with redirect_stderr(err):
                q = PC.fetch_prices(
                    {"SYN.US": "SYN"}, cache_path=cache,
                    fetchers=[_tier({"SYN.US": (True, "synthetic", "USD")}),
                              _tier({"SYN.US": (7.5, "second", "USD")})])
            doc = json.loads(cache.read_text())
        self.assertEqual((q["SYN.US"].price, q["SYN.US"].source),
                         (7.5, "second"))
        self.assertEqual(doc["SYN.US"]["price"], 7.5)
        self.assertIn("True is not a usable price", err.getvalue())

    def test_live_true_alone_prices_nothing_and_caches_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "prices.json"
            with redirect_stderr(io.StringIO()):
                q = PC.fetch_prices(
                    {"SYN.US": "SYN"}, cache_path=cache,
                    fetchers=[_tier({"SYN.US": (True, "synthetic")})])
            self.assertFalse(cache.exists())
        self.assertNotIn("SYN.US", q)

    def test_cached_true_is_a_miss(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "prices.json"
            cache.write_text(json.dumps({"SYN.US": {
                "price": True, "asof": _iso(0), "currency": "USD",
                "quote_symbol": "SYN"}}))
            err = io.StringIO()
            with redirect_stderr(err):
                q = PC.fetch_prices({"SYN.US": "SYN"}, cache_path=cache,
                                    fetchers=[_tier({})])
        self.assertNotIn("SYN.US", q)
        self.assertIn("no usable price (True)", err.getvalue())

    def test_history_true_close_is_not_a_price(self):
        with tempfile.TemporaryDirectory() as td:
            got = PC.close_on(
                "SYN.US", "SYN", "2025-03-03",
                cache_path=Path(td) / "close.json",
                fetchers=[lambda pairs, s, e: {
                    "SYN.US": ({"2025-03-03": True}, "USD")}])
        self.assertIsNone(got)

    def test_shared_rule(self):
        for bad in (True, False, None, "x", float("nan"), float("inf"),
                    0, -1, [1]):
            self.assertIsNone(PC._money(bad), bad)
        self.assertEqual(PC._money("2.5"), 2.5)
        self.assertEqual(PC._money(3), 3.0)


# ------------------------------- TJ-0578: IBKR's quote keeps its listing

class TestIbkrNativeCurrency(unittest.TestCase):
    """TJ-0578: QUOTE SYN.US SYN.TO remaps the Yahoo spelling; the IBKR
    tier still prices SYN.US's own (USD) listing, but its quote carried
    no currency and harvest read SYN.TO's CAD off the remap: 10 USD
    valued as 10 CAD. The IBKR quote now carries its contract's
    currency."""

    def _chain(self, cache, pairs, ibkr):
        with mock.patch.object(PC, "_ibkr_fetcher", return_value=ibkr), \
                mock.patch.object(PC, "_yfinance_fetcher",
                                  return_value={}), \
                redirect_stderr(io.StringIO()):
            return PC.fetch_prices(pairs, cache_path=cache, offline=False)

    def test_quote_carries_the_native_currency(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "prices.json"
            q = self._chain(cache, {"SYN.US": "SYN.TO"}, {"SYN.US": 10.0})
            rec = json.loads(cache.read_text())["SYN.US"]
        self.assertEqual((q["SYN.US"].price, q["SYN.US"].currency),
                         (10.0, "USD"))
        self.assertEqual((rec["currency"], rec["quote_symbol"]),
                         ("USD", "SYN"))

    def test_pence_market_native_quote_is_refused(self):
        # A native LSE line remapped to a US spelling: IB's figure does
        # not say pence or pounds, so it is not taken (it used to be,
        # with the remap's USD).
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "prices.json"
            q = self._chain(cache, {"SYN.L": "SYN"}, {"SYN.L": 125.0})
        self.assertNotIn("SYN.L", q)

    def test_harvest_values_the_native_usd_quote(self):
        from taxjson.bin.taxjson_harvest import main as harvest_main
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "ticker.map").write_text("QUOTE SYN.US SYN.TO\n")
            g = p / "margin_gains_wash.json"
            g.write_text(json.dumps({
                "summary": {"year": date.today().year},
                "transactions": [],
                "inventory": [{"symbol": "SYN.US", "qty": 10,
                               "total_cost": 100.0,
                               "position_start_date": _iso(-400)}]}))
            rates = p / "to_base.csv"
            rates.write_text(f"{_iso(-1)} 12:00:00 USD CAD 1.35\n"
                             f"{_iso(0)} 12:00:00 USD CAD 1.35\n")
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(PC, "_ibkr_fetcher",
                                   return_value={"SYN.US": 10.0}), \
                    mock.patch.object(PC, "_yfinance_fetcher",
                                      return_value={}), \
                    mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                    redirect_stdout(out), redirect_stderr(err):
                rc = harvest_main([str(g), "--country", "canada",
                                   "--rates", str(rates), "--json"])
        self.assertEqual(rc, 0, err.getvalue())
        row = json.loads(out.getvalue())["rows"][0]
        self.assertEqual(row["price_currency"], "USD")
        self.assertAlmostEqual(row["fx_rate"], 1.35)
        self.assertAlmostEqual(row["value"], 135.0)
        self.assertAlmostEqual(row["unrealized"], 35.0)


# --------------------------------- TJ-0584: a day no calendar has

class _FxCache(unittest.TestCase):
    """A private FX cache file per test (to_base_curr.CACHE_FILE)."""

    def setUp(self):
        from taxjson.bin import to_base_curr as T
        self.T = T
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.path = Path(td.name) / "fx.json"
        p = mock.patch.object(T, "CACHE_FILE", str(self.path))
        p.start()
        self.addCleanup(p.stop)

    def offline(self, doc, frm="USD", to="CAD"):
        self.path.write_text(json.dumps(doc))
        return self.T.build_rates(frm, to, "2025-03-03", "2025-03-03",
                                  today="2025-03-04", offline=True)


class TestImpossibleFxDates(_FxCache):
    """TJ-0584: '2025-02-30' has the YYYY-MM-DD shape, so a cached
    observation, a coverage endpoint or --start under it reached the
    date arithmetic and raised "day is out of range for month". Each is
    now a damaged cache record (dropped, said naming the file) or a
    usage error."""

    def test_observation_under_an_impossible_day(self):
        rows, errors, _ = self.offline({
            "_boc": {"USDCAD": {"obs": {"2025-02-30": "1.30",
                                        "2025-02-28": "1.35"}}},
            "_coverage": {"boc:USDCAD": [["2025-02-28", "2025-03-03"]]}})
        self.assertEqual(rows, [("2025-03-03", "1.35", "boc")])
        bad = [e for e in errors if "2025-02-30" in e]
        self.assertEqual(len(bad), 1, errors)
        self.assertIn(str(self.path), bad[0])
        doc = json.loads(self.path.read_text())
        self.assertNotIn("2025-02-30", doc["_boc"]["USDCAD"]["obs"])

    def test_coverage_endpoint_on_an_impossible_day(self):
        rows, errors, _ = self.offline({
            "_boc": {"USDCAD": {"obs": {"2025-02-28": "1.35"}}},
            "_coverage": {"boc:USDCAD": [["2025-02-28", "2025-02-30"],
                                         ["2025-03-01", "2025-03-03"]]}})
        self.assertEqual(rows, [])
        self.assertTrue(any("_coverage['boc:USDCAD']" in e
                            and "damaged" in e for e in errors), errors)

    def test_legacy_yahoo_key_on_an_impossible_day(self):
        rows, errors, _ = self.offline(
            {"USDEUR-2025-02-30": 0.9, "USDEUR-2025-03-03": 0.92},
            frm="USD", to="EUR")
        self.assertEqual(rows, [("2025-03-03", "0.92", "yahoo")])
        self.assertTrue(any("USDEUR-2025-02-30" in e for e in errors),
                        errors)

    def test_damaged_markers_are_not_dates_to_count_from(self):
        self.path.write_text(json.dumps({"_boc": {"USDCAD": {
            "obs": {"2025-02-28": "1.35"},
            "not_published": True, "not_published_checked": "2025-02-30",
            "suspect": [["2025-02-30", "2025-03-01", "2025-02-31"]]}}}))
        asked = []

        def boc(cur, a, b):
            asked.append((a, b))
            return {"2025-03-03": "1.36"}
        rows, errors, _ = self.T.build_rates(
            "USD", "CAD", "2025-03-03", "2025-03-03", today="2025-03-04",
            fetch_boc_fn=boc, fetch_yahoo_fn=lambda *_: {})
        self.assertEqual(rows, [("2025-03-03", "1.36", "boc")])
        self.assertTrue(asked)          # the undated marker is re-probed
        blk = json.loads(self.path.read_text())["_boc"]["USDCAD"]
        self.assertNotIn("not_published", blk)
        self.assertNotIn("2025-02-30", json.dumps(blk.get("suspect")))

    def test_cli_start_on_an_impossible_day_is_a_usage_error(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}), \
                redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.T.main(["USD", "CAD", "--start", "2025-02-30",
                         "--end", "2025-03-03"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--start must be a date written YYYY-MM-DD, got "
                      "'2025-02-30'", err.getvalue())


# --------------------------- TJ-0583: two runs, one FX cache, both kept

class TestConcurrentFxRuns(_FxCache):
    """TJ-0583: two projects' runs load one FX cache, fetch USD/CAD and
    EUR/CAD at once and save in turn; the later save wrote back its whole
    stale copy, so the cache kept EUR only and an offline USD run had no
    rate. Each run now writes its changes into the file as it is under
    the lock."""

    def _two_runs(self, first, second, *, start="2025-03-03",
                  end="2025-03-03", rates=None):
        """Run `first` and `second` (currencies) in two threads: both
        load before either saves, and `second` saves after `first`."""
        import threading
        T = self.T
        both_loaded = threading.Barrier(2)
        first_saved = threading.Event()
        real_load, real_save = T.load_cache, T.save_cache
        who = threading.local()
        rates = rates or {"USD": "1.35", "EUR": "1.55", "GBP": "1.75"}

        def load():
            v = real_load()
            both_loaded.wait(timeout=15)
            return v

        def save(v):
            if who.cur == second:
                first_saved.wait(timeout=15)
            real_save(v)
            if who.cur == first:
                first_saved.set()

        def one(cur):
            who.cur = cur
            try:
                return T.build_rates(
                    cur, "CAD", start, end, today="2025-03-04",
                    fetch_boc_fn=lambda _c, a, b: {
                        d: rates[cur] for d in ("2025-03-03",)
                        if a <= d <= b},
                    fetch_yahoo_fn=lambda *_: {})
            finally:
                if cur == first:
                    first_saved.set()
        out = {}
        with mock.patch.object(T, "load_cache", load), \
                mock.patch.object(T, "save_cache", save):
            ts = [threading.Thread(target=lambda c=c: out.update({c: one(c)}))
                  for c in (first, second)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=30)
        return out

    def test_two_series_fetched_at_once_are_both_kept(self):
        out = self._two_runs("USD", "EUR")
        self.assertEqual(out["USD"][0], [("2025-03-03", "1.35", "boc")])
        self.assertEqual(out["EUR"][0], [("2025-03-03", "1.55", "boc")])
        doc = json.loads(self.path.read_text())
        self.assertEqual(sorted(doc["_boc"]), ["EURCAD", "USDCAD"])
        self.assertEqual(sorted(doc["_coverage"]),
                         ["boc:EURCAD", "boc:USDCAD"])
        for cur, rate in (("USD", "1.35"), ("EUR", "1.55")):
            rows, _e, _n = self.T.build_rates(
                cur, "CAD", "2025-03-03", "2025-03-03",
                today="2025-03-04", offline=True)
            self.assertEqual(rows, [("2025-03-03", rate, "boc")], cur)

    def test_a_dropped_damaged_entry_stays_dropped(self):
        # Both runs load a cache with a damaged USD observation; the USD
        # run drops it (and fetches the date again), the EUR run leaves
        # it alone. The later EUR save must not bring it back.
        self.path.write_text(json.dumps({"_boc": {"USDCAD": {
            "obs": {"2025-03-03": "abc"}}}}))
        self._two_runs("USD", "EUR")
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc["_boc"]["USDCAD"]["obs"],
                         {"2025-03-03": "1.35"})
        self.assertEqual(doc["_boc"]["EURCAD"]["obs"],
                         {"2025-03-03": "1.55"})

    def test_a_deletion_keeps_another_runs_newer_value(self):
        # The entry was damaged when this run loaded the cache and the
        # run drops it — but by then another writer has put a real rate
        # there: that rate is kept, not deleted with the damage.
        T = self.T
        self.path.write_text(json.dumps({"_boc": {"EURCAD": {
            "obs": {"2025-02-28": "abc"}}}}))
        cache = T.load_cache()
        errors = T._drop_bad_obs(cache, "EURCAD", False)
        self.assertEqual(len(errors), 1)
        self.path.write_text(json.dumps({"_boc": {"EURCAD": {
            "obs": {"2025-02-28": "1.54"}}}}))
        T.save_cache(cache)
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc["_boc"]["EURCAD"]["obs"],
                         {"2025-02-28": "1.54"})

    def test_two_ranges_of_one_series_are_both_covered(self):
        T = self.T
        self.path.write_text("{}")
        a, b = T.load_cache(), T.load_cache()
        a.setdefault("_coverage", {})["boc:USDCAD"] = [
            ["2025-01-01", "2025-01-31"]]
        b.setdefault("_coverage", {})["boc:USDCAD"] = [
            ["2025-03-01", "2025-03-31"]]
        T.save_cache(a)
        T.save_cache(b)
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc["_coverage"]["boc:USDCAD"],
                         [["2025-01-01", "2025-01-31"],
                          ["2025-03-01", "2025-03-31"]])

    def test_a_plain_dict_still_replaces_the_file(self):
        self.path.write_text(json.dumps({"USDEUR-2025-03-03": 0.9}))
        self.T.save_cache({"USDEUR-2025-03-04": 0.91})
        self.assertEqual(json.loads(self.path.read_text()),
                         {"USDEUR-2025-03-04": 0.91})


# ------------------------------------ TJ-0568: init's half-written project

def _tree(top):
    return sorted(p.relative_to(top).as_posix() for p in top.rglob("*"))


class TestInitLeavesNoHalfProject(unittest.TestCase):
    """TJ-0568: `init --year 2025` with a FILE at 2025/holdings wrote
    taxjson.toml, ticker.map, tobase.map and .gitignore, then stopped;
    the retry refused ("taxjson.toml already exists"). The holdings
    folder is now checked with the others before anything is written,
    and a write that fails later removes what a new project's init made
    so far."""

    def _init(self, top):
        from _qa_project import tj
        return tj(top, "init", "--country", "canada", "--year", "2025",
                  check=False)

    def test_a_file_at_holdings_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "2025").mkdir()
            (top / "2025" / "holdings").write_text("not a folder\n")
            r = self._init(top)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("cannot create 2025/holdings/: it exists and is "
                          "not a directory", r.stderr)
            self.assertIn("Nothing was written", r.stderr)
            self.assertEqual(_tree(top), ["2025", "2025/holdings"])
            (top / "2025" / "holdings").unlink()
            r = self._init(top)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((top / "2025" / "taxjson.toml").is_file())
            self.assertTrue((top / "2025" / "holdings").is_dir())

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0,
                     "a read-only folder does not stop root")
    def test_a_failed_write_removes_what_a_new_init_made(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "inputs").mkdir()
            (top / "inputs").chmod(0o500)
            try:
                r = self._init(top)
            finally:
                (top / "inputs").chmod(0o700)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("cannot write inputs/", r.stderr)
            self.assertIn("Nothing was kept", r.stderr)
            self.assertEqual(_tree(top), ["inputs"])
            r = self._init(top)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((top / "2025" / "taxjson.toml").is_file())


# --------------------------- TJ-0586: `opening --force` is a user-file write

OLD_OPENING = "OPENING 2024-12-31 SYN.TO 10 CAD 100\n"


class TestOpeningReplacesLikeAUserFile(unittest.TestCase):
    """TJ-0586: `opening ACCOUNT FILE --force` replaced the .tt like a
    generated file: a read-only file was rewritten (mode 0600), a link
    inside the project was replaced by a file (its target unchanged),
    and a link leaving the project likewise. It now goes through
    write_user_file, as every other writer of a user's file."""

    def _case(self, td, kind):
        from _qa_project import tj
        top = Path(td)
        p = top / "project"
        (p / "inputs" / "margin").mkdir(parents=True)
        (p / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\noption_grant_timing_since = 2025\n'
            '[accounts.margin]\ntype = "taxable"\n')
        snap = p / "snapshot.toml"
        snap.write_text('[meta]\nas_of = "2024-12-31"\n[[holding]]\n'
                        'symbol = "SYN.TO"\nquantity = 10\ncurrency = "CAD"'
                        '\ntotal_cost = 120\n')
        dest = p / "inputs" / "margin" / "opening_2024-12-31.tt"
        target = dest
        if kind == "readonly":
            dest.write_text(OLD_OPENING)
            dest.chmod(0o444)
        else:
            target = (p if kind == "inside" else top) / "saved-opening.txt"
            target.write_text(OLD_OPENING)
            dest.symlink_to(target)
        r = tj(p, "opening", "margin", str(snap), "--force", check=False)
        return r, dest, target

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0,
                     "a read-only file does not stop root")
    def test_read_only_file_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r, dest, _t = self._case(td, "readonly")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("the file is read-only", " ".join(r.stderr.split()))
            self.assertEqual(dest.read_text(), OLD_OPENING)
            self.assertEqual(dest.stat().st_mode & 0o777, 0o444)
            dest.chmod(0o644)

    def test_link_inside_the_project_keeps_pointing_at_its_target(self):
        with tempfile.TemporaryDirectory() as td:
            r, dest, target = self._case(td, "inside")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(dest.is_symlink())
            self.assertIn("OPENING 2024-12-31 SYN.TO 10 CAD 120.00",
                          target.read_text())
            bak = target.with_name(target.name + ".bak")
            self.assertEqual(bak.read_text(), OLD_OPENING)

    def test_link_leaving_the_project_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r, dest, target = self._case(td, "outside")
            self.assertEqual(r.returncode, 2, r.stderr)
            # Width 120: where the temp path's length breaks the line.
            self.assertIn("outside the project", " ".join(r.stderr.split()))
            self.assertTrue(dest.is_symlink())
            self.assertEqual(target.read_text(), OLD_OPENING)


if __name__ == "__main__":
    unittest.main()
