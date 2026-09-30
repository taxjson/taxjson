"""Regression tests for the low-round engine-area audit fixes (fixl/engine).

Synthetic data only; account labels are fake.
"""
import contextlib
import io
import json
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from taxjson.bin import to_base_curr as T
from taxjson.lib import price_chain as PC
from tax_rules import rule


def _weekdays(a, b):
    out, d = [], a
    while d <= b:
        if date.fromisoformat(d).weekday() < 5:
            out.append(d)
        d = T._shift(d, 1)
    return out


class TestFxCashRateAge(unittest.TestCase):
    """R1-151: fx-cash priced an event with the newest rate on file
    however old it was; the converter refuses anything past 5 days."""

    def test_latest_rate_bounded(self):
        hist = {"USD": {"2024-01-02": 1.32}}
        self.assertEqual(PC.latest_rate(hist, "USD", "2025-11-03"),
                         (1.32, "2024-01-02"))            # unbounded
        self.assertEqual(PC.latest_rate(hist, "USD", "2025-11-03",
                                        max_age_days=7), (None, None))
        self.assertEqual(PC.latest_rate(hist, "USD", "2024-01-05",
                                        max_age_days=7),
                         (1.32, "2024-01-02"))

    @rule("CA-FX-02")
    def test_fx_cash_counts_a_stale_rate_unrated(self):
        self._stale("canada")

    @rule("US-FX-02")
    def test_fx_cash_counts_a_stale_rate_unrated_us(self):
        self._stale("usa")

    def _stale(self, country):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        rows = [
            {"action": "BUYSELL", "date": "2024-01-02", "symbol": "XYZ.US",
             "quantity": -10, "net_amount": 1000.0, "currency": "USD"},
            {"action": "BUYSELL", "date": "2025-11-03", "symbol": "XYZ.US",
             "quantity": 10, "net_amount": 1000.0, "currency": "USD"},
        ]
        base, cur = ("CAD", "USD") if country == "canada" else ("USD", "CAD")
        for r in rows:
            r["currency"] = cur
        doc = build_ledger(rows, base, {cur: {"2024-01-02": 1.32}},
                           2025, country=country)
        self.assertEqual(doc["unrated"], {cur: 1})
        # Within the 5-day lookback the rate is used.
        rows[1]["date"] = "2024-01-05"
        doc = build_ledger(rows, base, {cur: {"2024-01-02": 1.32}},
                           2024, country=country)
        self.assertEqual(doc["unrated"], {})


class TestPriceCacheValues(unittest.TestCase):
    """R1-156 / R1-244: a cached price that is missing, null, 0,
    negative, NaN, Inf or text was served as a quote (0.0 -> a -100%
    LOSS in harvest; text crashed). It is a cache miss now."""

    def _fetch(self, rec):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".price_cache.json"
            p.write_text(json.dumps({"AAA.US": rec}, allow_nan=True))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                q = PC.fetch_prices({"AAA.US": "AAA"}, cache_path=p,
                                    fetchers=[])
            return q, err.getvalue()

    def test_bad_prices_are_misses(self):
        today = date.today().isoformat()
        for bad in (None, 0, -5, "n/a", float("nan"), float("inf")):
            rec = {"asof": today, "price": bad}
            q, err = self._fetch(rec)
            self.assertNotIn("AAA.US", q, bad)
            self.assertIn("no usable price", err)
        q, _ = self._fetch({"asof": today})              # key missing
        self.assertNotIn("AAA.US", q)

    def test_good_price_served(self):
        q, _ = self._fetch({"asof": date.today().isoformat(),
                            "price": 18.5})
        self.assertEqual(q["AAA.US"].price, 18.5)


class TestCacheLoadersSurviveNonUtf8(unittest.TestCase):
    """S055-04: a non-UTF-8 byte in a cache raised UnicodeDecodeError
    instead of degrading to a refetch."""

    def test_all_three_loaders(self):
        from taxjson.bin import fill_crypto_prices as F
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "c.json"
            bad.write_bytes(b'{"USDCAD-2025-01-02": "\xe9"}')
            self.assertEqual(PC._load_cache(bad), {})
            with mock.patch.object(T, "CACHE_FILE", str(bad)):
                self.assertEqual(T.load_cache(), {})
            with mock.patch.object(F, "CACHE_FILE", str(bad)):
                self.assertEqual(F.load_cache(), {})


class TestYfMapLoader(unittest.TestCase):
    """S076-24: lower-case keys were never matched. S077-07: a line
    with no target was dropped silently."""

    def test_case_and_short_line(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "yf_ticker.map").write_text(
                "oldco.to newco.to\nABC.TO\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                m = PC.load_yf_map([td])
        self.assertEqual(m, {"OLDCO.TO": ("newco.to", 1.0)})
        self.assertIn("'ABC.TO'", err.getvalue())
        self.assertIn("line ignored", err.getvalue())


class TestBocDegradedAnswer(unittest.TestCase):
    """S055-03: an HTTP 200 with no (or truncated) observations was
    recorded as coverage for good, so the dates kept a Yahoo close or a
    stale forward-filled 'boc' rate even after the Bank recovered."""

    TODAY = "2026-09-29"

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = mock.patch.object(T, "CACHE_FILE", str(Path(td.name) / "fx.json"))
        p.start()
        self.addCleanup(p.stop)
        self.empty = True
        self.calls = []

    def boc(self, cur, a, b):
        self.calls.append((a, b))
        if self.empty:
            return {}
        return {d: "1.3500" for d in _weekdays(a, b) if d < self.TODAY}

    def yahoo(self, ticker, a, b):
        return {d: 1.2 for d in _weekdays(a, b) if d < self.TODAY}

    def build(self, today=None):
        return T.build_rates("USD", "CAD", "2024-01-01", "2024-12-31",
                             today=today or self.TODAY, fetch_boc_fn=self.boc,
                             fetch_yahoo_fn=self.yahoo)

    def test_cold_empty_answer_is_rechecked(self):
        rows, _errors, notes = self.build()
        src = {d: s for d, _v, s in rows}
        self.assertEqual(src["2024-06-03"], "yahoo")      # fallback now
        self.assertTrue(any("no observations" in n for n in notes))
        # The Bank recovers: the next run asks again and its rates win.
        self.empty = False
        self.calls.clear()
        rows, _errors, _notes = self.build(today="2026-09-30")
        self.assertTrue(self.calls)
        src = {d: (v, s) for d, v, s in rows}
        self.assertEqual(src["2024-06-03"], ("1.3500", "boc"))

    def test_recheck_window_expires(self):
        self.build()
        self.calls.clear()
        later = T._shift(self.TODAY, T.SUSPECT_RECHECK_DAYS + 1)
        self.build(today=later)
        self.assertEqual(self.calls, [])      # accepted as the truth

    def test_healthy_answer_leaves_no_marker(self):
        self.empty = False
        _rows, _e, notes = self.build()
        self.assertFalse(any("no observations" in n for n in notes))
        cache = json.loads(Path(T.CACHE_FILE).read_text())
        self.assertNotIn("suspect", cache["_boc"]["USDCAD"])


if __name__ == "__main__":
    unittest.main()
