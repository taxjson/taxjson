"""Re-audit 2, FX rates (to_base_curr): a transient failure is never cached
as permanent coverage (A2-0136, A2-0393), a damaged cached Bank of Canada
value is named instead of copied into the rates file (A2-1212), and the
stated rate-gap tolerance matches the code (A2-0706).

Every fetcher is stubbed — no network."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from taxjson.bin import to_base_curr as T
from taxjson.bin.taxjson_convert_currency import (
    get_rate_for_date, load_exchange_rates, reset_fallback_tally,
)
from tax_rules import rule


def _weekdays(a, b):
    out, d = [], a
    while d <= b:
        if date.fromisoformat(d).weekday() < 5:
            out.append(d)
        d = T._shift(d, 1)
    return out


class _Cache(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        p = mock.patch.object(T, "CACHE_FILE",
                              str(Path(self.td.name) / "fx.json"))
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def rec(self, fn):
        def f(c, a, b):
            self.calls.append((a, b))
            return fn(c, a, b)
        return f


class TestYahooFailedDownload(_Cache):
    """A2-0136: later data already cached is no proof that an empty
    answer is the source's truth."""

    def test_failed_download_with_later_data_cached_is_asked_again(self):
        cache = {}
        T.refresh_yahoo(cache, "USDCAD", [("2020-01-01", "2020-12-31")],
                        "2026-09-28",
                        fetch=lambda t, a, b: {d: 1.34 for d in
                                               _weekdays(a, b)})
        errs = T.refresh_yahoo(cache, "USDCAD",
                               [("2015-01-01", "2015-12-31")],
                               "2026-09-28", fetch=lambda t, a, b: {})
        self.assertTrue(errs)
        self.assertEqual(T._yahoo_coverage(cache, "USDCAD"),
                         [["2020-01-01", "2020-12-31"]])
        healthy = self.rec(lambda t, a, b: {d: 1.28 for d in
                                            _weekdays(a, b)})
        self.assertEqual(T.refresh_yahoo(
            cache, "USDCAD", [("2015-01-01", "2015-12-31")], "2026-09-28",
            fetch=healthy), [])
        self.assertIn(("2015-01-01", "2015-12-31"), self.calls)
        self.assertEqual(cache["USDCAD-2015-06-15"], 1.28)

    def test_range_before_history_is_remembered_when_source_answers_later(
            self):
        # The source works (it answers for the dates after the range) but
        # has nothing in it: its history starts later.
        cache = {}

        def hist(t, a, b):
            return {d: 1.3 for d in _weekdays(max(a, "2003-12-01"), b)}
        T.refresh_yahoo(cache, "USDCAD", [("2015-01-01", "2015-12-31")],
                        "2026-09-28", fetch=hist)
        errs = T.refresh_yahoo(cache, "USDCAD",
                               [("2000-01-01", "2000-12-31")],
                               "2026-09-28", fetch=hist)
        self.assertEqual(errs, [])
        self.assertIn(["2000-01-01", "2000-12-31"],
                      T._yahoo_coverage(cache, "USDCAD"))


class TestTruncatedAnswersAreAskedAgain(_Cache):
    """A2-0393: only a complete answer marks its dates covered."""

    def test_boc_second_empty_answer_is_not_a_stopped_series(self):
        healthy = lambda c, a, b: {d: "1.3700" for d in _weekdays(a, b)}
        empty = lambda c, a, b: {}
        cache = {}
        T.refresh_boc(cache, "USD", "2026-01-02", "2026-07-31",
                      "2026-08-01", fetch=healthy, notes=[])
        for today, fn in (("2026-09-01", empty), ("2026-09-20", empty)):
            notes = []
            T.refresh_boc(cache, "USD", "2026-01-02", T._shift(today, -1),
                          today, fetch=fn, notes=notes)
        # The 09-20 empty answer is re-checked and said, not taken as
        # "the series stopped".
        self.assertTrue(cache["_boc"]["USDCAD"].get("suspect"))
        self.assertTrue(notes)
        T.refresh_boc(cache, "USD", "2026-01-02", "2026-09-21",
                      "2026-09-22", fetch=healthy, notes=[])
        obs = cache["_boc"]["USDCAD"]["obs"]
        self.assertEqual([d for d in _weekdays("2026-08-01", "2026-09-21")
                          if d not in obs], [])
        rows = T.resolve_rows(cache, "USD", "CAD", "2026-09-01",
                              "2026-09-11", "2026-09-22")
        self.assertEqual(len(rows), 11)
        self.assertTrue(all(s == "boc" for _d, _v, s in rows))

    def test_boc_hole_left_by_an_expired_suspect_is_refilled(self):
        # A degraded answer accepted after its re-check window: the next
        # answer that has data re-asks the hole before it.
        healthy = lambda c, a, b: {d: "1.3700" for d in _weekdays(a, b)}
        cache = {}
        T.refresh_boc(cache, "USD", "2026-01-02", "2026-07-31",
                      "2026-08-01", fetch=healthy, notes=[])
        T.refresh_boc(cache, "USD", "2026-01-02", "2026-08-31",
                      "2026-09-01", fetch=lambda c, a, b: {}, notes=[])
        cache["_boc"]["USDCAD"].pop("suspect", None)        # expired
        T.refresh_boc(cache, "USD", "2026-01-02", "2026-09-21",
                      "2026-09-22", fetch=self.rec(healthy), notes=[])
        obs = cache["_boc"]["USDCAD"]["obs"]
        self.assertIn("2026-08-14", obs)
        self.assertTrue(any(a <= "2026-08-14" <= b for a, b in self.calls))

    def test_boc_really_stopped_series_is_not_suspect(self):
        # RUB-style: silent for years before the range, nothing after it.
        cache = {}
        T.refresh_boc(cache, "RUB", "2021-01-04", "2022-02-28",
                      "2022-03-01",
                      fetch=lambda c, a, b: {d: "0.017" for d in
                                             _weekdays(a, b)}, notes=[])
        notes = []
        T.refresh_boc(cache, "RUB", "2021-01-04", "2026-09-27",
                      "2026-09-28", fetch=lambda c, a, b: {}, notes=notes)
        notes = []
        T.refresh_boc(cache, "RUB", "2021-01-04", "2026-10-01",
                      "2026-10-02", fetch=lambda c, a, b: {}, notes=notes)
        self.assertEqual(notes, [])

    def test_noon_truncated_answer_covers_only_what_it_reached(self):
        trunc = lambda c, a, b: {d: "1.3000" for d in
                                 _weekdays(a, min(b, "2015-03-31"))}
        full = self.rec(lambda c, a, b: {d: "1.3000" for d in
                                         _weekdays(a, b)})
        cache = {}
        errs = T.refresh_boc_noon(cache, "USD", "2015-01-02", "2015-12-31",
                                  "2026-10-01", fetch=trunc)
        self.assertTrue(errs and "2015-03-31" in errs[0])
        self.assertEqual(T._coverage(cache, "boc_noon:USDCAD"),
                         [["2015-01-02", "2015-03-31"]])
        self.assertEqual(T.refresh_boc_noon(
            cache, "USD", "2015-01-02", "2015-12-31", "2026-10-01",
            fetch=full), [])
        self.assertEqual(self.calls, [("2015-04-01", "2015-12-31")])
        self.assertIn("2015-11-02", cache["_boc_noon"]["USDCAD"]["obs"])

    def test_yahoo_truncated_answer_covers_only_what_it_reached(self):
        trunc = lambda t, a, b: {d: 1.30 for d in
                                 _weekdays(a, min(b, "2016-03-31"))}
        full = self.rec(lambda t, a, b: {d: 1.30 for d in _weekdays(a, b)})
        cache = {}
        errs = T.refresh_yahoo(cache, "USDCAD", [("2016-01-04", "2016-12-30")],
                               "2026-10-01", fetch=trunc)
        self.assertTrue(errs)
        self.assertEqual(T._yahoo_coverage(cache, "USDCAD"),
                         [["2016-01-04", "2016-03-31"]])
        T.refresh_yahoo(cache, "USDCAD", [("2016-01-04", "2016-12-30")],
                        "2026-10-01", fetch=full)
        self.assertEqual(self.calls, [("2016-04-01", "2016-12-30")])
        self.assertIn("USDCAD-2016-11-02", cache)

    def test_yahoo_late_start_is_the_history_not_a_cut(self):
        hist = lambda t, a, b: {d: 1.3 for d in
                                _weekdays(max(a, "2003-12-01"), b)}
        cache = {}
        self.assertEqual(T.refresh_yahoo(
            cache, "USDCAD", [("2000-01-01", "2016-12-30")], "2026-10-01",
            fetch=hist), [])
        self.assertEqual(T._yahoo_coverage(cache, "USDCAD"),
                         [["2000-01-01", "2016-12-30"]])


class TestBadCachedRate(_Cache):
    """A2-1212: a non-numeric cached Bank rate is named, never written."""

    def _seed(self, value):
        Path(T.CACHE_FILE).write_text(json.dumps({
            "_boc": {"USDCAD": {"obs": {
                "2024-01-02": "1.3316", "2024-01-03": value,
                "2024-01-04": "1.3320", "2024-01-05": "1.3330"}}},
            "_coverage": {"boc:USDCAD": [["2024-01-01", "2024-01-05"]]}}))

    def test_bad_value_is_named_and_not_emitted_offline(self):
        for value in ("abc", "1,3316", [1], "nan", "0", "-1.3"):
            with self.subTest(value=value):
                self._seed(value)
                rows, errors, _ = T.build_rates(
                    "USD", "CAD", "2024-01-02", "2024-01-05",
                    today="2024-01-06", offline=True)
                vals = {d: v for d, v, _s in rows}
                self.assertNotIn(str(value), vals.values())
                self.assertNotIn("2024-01-03", vals)    # asked again online
                self.assertEqual(vals["2024-01-04"], "1.3320")
                self.assertTrue(errors)
                self.assertIsInstance(errors[0], T.CacheProblem)
                self.assertIn(T.CACHE_FILE, errors[0])
                self.assertIn("2024-01-03", errors[0])

    def test_bad_value_is_asked_again_online(self):
        self._seed("abc")
        calls = []

        def boc(c, a, b):
            calls.append((a, b))
            return {d: "1.3400" for d in _weekdays(a, b)}
        rows, errors, _ = T.build_rates(
            "USD", "CAD", "2024-01-02", "2024-01-05", today="2024-01-06",
            fetch_boc_fn=boc)
        self.assertTrue(any(a <= "2024-01-03" <= b for a, b in calls))
        self.assertEqual({d: v for d, v, _s in rows}["2024-01-03"],
                         "1.3400")
        self.assertTrue(any(isinstance(e, T.CacheProblem) for e in errors))

    def test_main_prints_the_cache_problem_not_a_download_failure(self):
        self._seed("abc")
        err, out = io.StringIO(), io.StringIO()
        with mock.patch("taxjson.lib.offline.offline_enabled",
                        return_value=True), \
                redirect_stderr(err), redirect_stdout(out):
            T.main(["USD", "CAD", "--start", "2024-01-02",
                    "--end", "2024-01-05"])
        self.assertNotIn(" abc ", out.getvalue())
        self.assertIn("2024-01-03", err.getvalue())
        self.assertIn(T.CACHE_FILE, err.getvalue())
        self.assertNotIn("download failed", err.getvalue())


class TestRateGapTolerance(unittest.TestCase):
    """A2-0706: tax-logic states the gap the converter really accepts."""

    def setUp(self):
        reset_fallback_tally()
        self.addCleanup(reset_fallback_tally)

    def _rates(self, td):
        # Bank observations on 03-03 and 03-20 only; to_base_curr carries
        # 03-03 forward MAX_FILL_DAYS days (through 03-10).
        cache = {"_boc": {"USDCAD": {"obs": {"2025-03-03": "1.4400",
                                             "2025-03-20": "1.4300"}}},
                 "_coverage": {"boc:USDCAD": [["2025-03-01",
                                               "2025-03-31"]]}}
        rows = T.resolve_rows(cache, "USD", "CAD", "2025-03-01",
                              "2025-03-31", "2025-04-30")
        p = Path(td) / "to_base.csv"
        p.write_text("".join(f"{d} 12:00:00 USD CAD {v} {s}\n"
                             for d, v, s in rows))
        return load_exchange_rates(p, "CAD")

    @rule("CA-FX-02")
    def test_canada_rate_gap_is_fill_plus_lookback(self):
        self._check()

    @rule("US-FX-02")
    def test_usa_rate_gap_is_fill_plus_lookback(self):
        self._check()

    def _check(self):
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as td:
            hist = self._rates(td)
        fill, look = T.MAX_FILL_DAYS, 5
        last_ok = T._shift("2025-03-03", fill + look)
        self.assertEqual(get_rate_for_date("USD", last_ok, hist,
                                           Decimal("9")), Decimal("1.4400"))
        self.assertEqual(get_rate_for_date(
            "USD", T._shift(last_ok, 1), hist, Decimal("9")), Decimal("9"))
        from taxjson.lib import tax_logic
        for rid in ("CA-FX-02", "US-FX-02"):
            text = tax_logic.catalog()[rid].text
            self.assertIn(f"{fill} days", text)
            self.assertIn(f"{fill + look} days", text)


if __name__ == "__main__":
    unittest.main()
