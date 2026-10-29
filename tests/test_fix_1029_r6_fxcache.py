"""Backlog fix round 6 of 2026-10-29, group `fxcache` (TJ-0706): two
runs load one FX rate cache holding a damaged rate. The online run drops
it, downloads the day again and saves the repaired rate with its
coverage; the offline run drops it too (its cleanup) and saves as well.
Whichever saved second, the day's coverage used to be lost, so offline
runs had no row for the repaired rate and the next online run downloaded
it again. TJ-0583's delta save (tests/test_fix_1029_r2_caches.py
TestConcurrentFxRuns) stays as it was.

Synthetic rates only, in a cache file of each test's own; the two runs
are real `build_rates` calls in two threads, interleaved by a barrier and
an event (both load before either saves; the save order is the test's),
never by sleeps. No test touches the network: the online run's fetchers
are stubs."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from taxjson.bin import to_base_curr as T

MON, TUE, WED = "2025-03-03", "2025-03-04", "2025-03-05"

# A Bank USD/CAD cache whose Tuesday rate is damaged (not a number);
# `_boc` comes before `_coverage`, so a run's dropping of the rate is
# replayed before its coverage change.
BOC_DAMAGED = {
    "_boc": {"USDCAD": {"obs": {MON: "1.3500", TUE: "abc"}}},
    "_coverage": {"boc:USDCAD": [[MON, TUE]]},
}
# The same for a Yahoo pair (a non-CAD target): a rate that is not
# positive.
YAHOO_DAMAGED = {
    f"USDEUR-{MON}": 0.95,
    f"USDEUR-{TUE}": -1.0,
    "_coverage": {"yahoo:USDEUR": [[MON, TUE]]},
}


def _stub(rates):
    """A fetcher answering `rates` ({date: rate}) inside [a, b]."""
    return lambda _what, a, b: {d: v for d, v in rates.items()
                                if a <= d <= b}


def _online(frm="USD", to="CAD", boc=None, yahoo=None):
    return dict(frm=frm, to=to, fetch_boc_fn=_stub(boc or {}),
                fetch_yahoo_fn=_stub(yahoo or {}))


def _offline(frm="USD", to="CAD"):
    return dict(frm=frm, to=to, offline=True)


class _TwoRuns(unittest.TestCase):
    """A private FX cache file per test (to_base_curr.CACHE_FILE)."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.path = Path(td.name) / "fx.json"
        p = mock.patch.object(T, "CACHE_FILE", str(self.path))
        p.start()
        self.addCleanup(p.stop)

    def build(self, frm="USD", to="CAD", **kw):
        return T.build_rates(frm, to, MON, TUE, today=WED, **kw)

    def two_runs(self, first, second):
        """Run two `build_rates` (keyword dicts from _online/_offline) in
        two threads: both load the cache before either saves, and
        `second` saves after `first`. Returns their results."""
        both_loaded = threading.Barrier(2)
        first_saved = threading.Event()
        real_load, real_save = T.load_cache, T.save_cache
        who = threading.local()

        def load():
            v = real_load()
            both_loaded.wait(timeout=15)
            return v

        def save(v):
            if who.name == "second":
                first_saved.wait(timeout=15)
            real_save(v)
            if who.name == "first":
                first_saved.set()

        out = {}

        def one(name, kw):
            who.name = name
            kw = dict(kw)
            try:
                out[name] = self.build(kw.pop("frm"), kw.pop("to"), **kw)
            finally:
                if name == "first":
                    first_saved.set()

        with mock.patch.object(T, "load_cache", load), \
                mock.patch.object(T, "save_cache", save):
            ts = [threading.Thread(target=one, args=(n, kw))
                  for n, kw in (("first", first), ("second", second))]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=30)
        self.assertEqual(sorted(out), ["first", "second"])
        return out

    def doc(self):
        return json.loads(self.path.read_text())

    def offline_rows(self, frm="USD", to="CAD"):
        rows, _errors, _notes = self.build(frm, to, offline=True)
        return rows


class TestRepairAndCleanupAtOnce(_TwoRuns):
    """TJ-0706: the offline run's cleanup and the online run's repair of
    one damaged rate, saved in either order, leave the repaired rate
    covered, as the online run alone would have."""

    REPAIRED = [(MON, "1.3500", "boc"), (TUE, "1.4000", "boc")]

    def _both_orders(self, base, online, offline, check):
        """Run the repair and the cleanup on `base`, each order in turn,
        then `check()` the cache they leave."""
        for order in ("online first", "offline first"):
            with self.subTest(order):
                self.path.write_text(json.dumps(base))
                runs = (online, offline) if order == "online first" \
                    else (offline, online)
                out = self.two_runs(*runs)
                cleanup = out["second" if order == "online first"
                              else "first"]
                self.assertTrue(any("not a positive number" in e
                                    for e in cleanup[1]), cleanup[1])
                check()

    def test_the_repaired_bank_rate_keeps_its_coverage(self):
        def check():
            doc = self.doc()
            self.assertEqual(doc["_boc"]["USDCAD"]["obs"],
                             {MON: "1.3500", TUE: "1.4000"})
            self.assertEqual(doc["_coverage"]["boc:USDCAD"], [[MON, TUE]])
            self.assertEqual(self.offline_rows(), self.REPAIRED)
        self._both_orders(BOC_DAMAGED,
                          _online(boc={MON: "1.3500", TUE: "1.4000"}),
                          _offline(), check)

    def test_a_day_the_repair_found_no_rate_for_stays_asked(self):
        # The Bank answers nothing for the re-asked Tuesday (a closed
        # day): the online run records it as asked, and the day carries
        # Monday's rate. The cleanup does not take that back.
        def check():
            doc = self.doc()
            self.assertEqual(doc["_boc"]["USDCAD"]["obs"], {MON: "1.3500"})
            self.assertEqual(doc["_coverage"]["boc:USDCAD"], [[MON, TUE]])
            self.assertEqual(self.offline_rows(),
                             [(MON, "1.3500", "boc"), (TUE, "1.3500", "boc")])
        self._both_orders(BOC_DAMAGED, _online(boc={MON: "1.3500"}),
                          _offline(), check)

    def test_the_repaired_yahoo_rate_keeps_its_coverage(self):
        def check():
            doc = self.doc()
            self.assertEqual(doc[f"USDEUR-{TUE}"], 0.96)
            self.assertEqual(doc["_coverage"]["yahoo:USDEUR"], [[MON, TUE]])
            self.assertEqual(self.offline_rows("USD", "EUR"),
                             [(MON, "0.95", "yahoo"), (TUE, "0.96", "yahoo")])
        self._both_orders(YAHOO_DAMAGED,
                          _online("USD", "EUR", yahoo={MON: 0.95, TUE: 0.96}),
                          _offline("USD", "EUR"), check)


class TestNoonSeriesRepair(_TwoRuns):
    """TJ-0706 for the Bank's legacy noon series (`boc_noon:`), in
    explicit steps: both runs load, each drops the damaged rate, the
    online one asks the Bank again, then they save in either order."""

    A, B = "2016-03-01", "2016-03-02"

    def test_the_repaired_noon_rate_keeps_its_coverage(self):
        A, B = self.A, self.B
        base = {"_boc_noon": {"USDCAD": {"obs": {A: "1.3500", B: "abc"}}},
                "_coverage": {"boc_noon:USDCAD": [[A, B]]}}
        for online_first in (True, False):
            with self.subTest(online_first=online_first):
                self.path.write_text(json.dumps(base))
                online, offline = T.load_cache(), T.load_cache()
                self.assertEqual(len(T._drop_bad_obs(offline, "USDCAD",
                                                     True)), 1)
                self.assertEqual(len(T._drop_bad_obs(online, "USDCAD",
                                                     False)), 1)
                self.assertEqual(T.refresh_boc_noon(
                    online, "USD", A, B, "2016-03-03",
                    fetch=_stub({A: "1.3500", B: "1.4000"})), [])
                for cache in ((online, offline) if online_first
                              else (offline, online)):
                    T.save_cache(cache)
                doc = self.doc()
                self.assertEqual(doc["_boc_noon"]["USDCAD"]["obs"],
                                 {A: "1.3500", B: "1.4000"})
                self.assertEqual(doc["_coverage"]["boc_noon:USDCAD"],
                                 [[A, B]])
                rows, _e, _n = T.build_rates("USD", "CAD", A, B,
                                             today="2016-03-03",
                                             offline=True)
                self.assertEqual(rows, [(A, "1.3500", "boc-noon"),
                                        (B, "1.4000", "boc-noon")])


class TestCleanupStillUncovers(_TwoRuns):
    """What TJ-0583 and the cleanup did stays: a damaged rate nobody
    repaired is dropped with its coverage (asked again online), also
    when another run saved in between."""

    def _assert_uncovered(self):
        doc = self.doc()
        self.assertEqual(doc["_boc"]["USDCAD"]["obs"], {MON: "1.3500"})
        self.assertEqual(doc["_coverage"]["boc:USDCAD"], [[MON, MON]])
        self.assertEqual(self.offline_rows(), [(MON, "1.3500", "boc")])

    def test_a_lone_cleanup_uncovers_the_damaged_day(self):
        self.path.write_text(json.dumps(BOC_DAMAGED))
        rows, errors, _notes = self.build(offline=True)
        self.assertEqual(rows, [(MON, "1.3500", "boc")])
        self.assertEqual(len(errors), 1, errors)
        self._assert_uncovered()

    def test_a_cleanup_saved_after_another_series_uncovers_it(self):
        self.path.write_text(json.dumps(BOC_DAMAGED))
        self.two_runs(_online("EUR", boc={MON: "1.5500", TUE: "1.5600"}),
                      _offline())
        self._assert_uncovered()
        self.assertEqual(self.doc()["_coverage"]["boc:EURCAD"], [[MON, TUE]])
        self.assertEqual(self.offline_rows("EUR"),
                         [(MON, "1.5500", "boc"), (TUE, "1.5600", "boc")])

    def test_two_cleanups_at_once_uncover_it(self):
        self.path.write_text(json.dumps(BOC_DAMAGED))
        self.two_runs(_offline(), _offline())
        self._assert_uncovered()


if __name__ == "__main__":
    unittest.main()
