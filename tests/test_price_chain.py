"""lib/price_chain: IBKR -> yfinance -> cache resolution, cache
write-back and age labeling
(yf_ticker.map ratio column, Src column, no hardcoded personal ratios)."""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib.price_chain import PriceQuote, fetch_prices


def tier(results):
    """A fake fetcher returning fixed {sym: (price, source)} for whatever
    it is asked about."""
    def _fetch(remaining):
        return {s: results[s] for s in remaining if s in results}
    return _fetch


class TestFetchPrices(unittest.TestCase):
    def test_tier_order_and_fallthrough(self):
        with tempfile.TemporaryDirectory() as td:
            quotes = fetch_prices(
                {"A.US": "A", "B.US": "B", "C.US": "C"},
                cache_path=Path(td) / "cache.json",
                fetchers=[tier({"A.US": (10.0, "ibkr")}),
                          tier({"A.US": (99.0, "yfinance"),   # must not win
                                "B.US": (20.0, "yfinance")})])
        self.assertEqual(quotes["A.US"].price, 10.0)
        self.assertEqual(quotes["A.US"].source, "ibkr")
        self.assertEqual(quotes["B.US"].source, "yfinance")
        self.assertNotIn("C.US", quotes)

    def test_fresh_hits_written_back_to_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "cache.json"
            fetch_prices({"A.US": "A"}, cache_path=cache_path,
                         fetchers=[tier({"A.US": (10.0, "ibkr")})])
            cached = json.loads(cache_path.read_text())
        self.assertEqual(cached["A.US"]["price"], 10.0)
        self.assertEqual(cached["A.US"]["source"], "ibkr")

    def test_cache_serves_misses_with_age_label(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "cache.json"
            asof = (date.today() - timedelta(days=3)).isoformat()
            cache_path.write_text(json.dumps(
                {"A.US": {"price": 12.5, "asof": asof, "source": "ibkr"}}))
            quotes = fetch_prices({"A.US": "A"}, cache_path=cache_path,
                                  fetchers=[tier({})])
        self.assertEqual(quotes["A.US"].price, 12.5)
        self.assertEqual(quotes["A.US"].source, "cache:3d")

    def test_stale_cache_warns_but_still_serves(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "cache.json"
            asof = (date.today() - timedelta(days=30)).isoformat()
            cache_path.write_text(json.dumps(
                {"A.US": {"price": 12.5, "asof": asof, "source": "ibkr"}}))
            err = io.StringIO()
            with redirect_stderr(err):
                quotes = fetch_prices({"A.US": "A"}, cache_path=cache_path,
                                      max_cache_age_days=7,
                                      fetchers=[tier({})])
        self.assertEqual(quotes["A.US"].source, "cache:30d")
        self.assertIn("older than 7d", err.getvalue())

    def test_failing_tier_is_skipped(self):
        def boom(remaining):
            raise RuntimeError("gateway down")
        with tempfile.TemporaryDirectory() as td:
            quotes = fetch_prices(
                {"A.US": "A"}, cache_path=Path(td) / "cache.json",
                fetchers=[boom, tier({"A.US": (7.0, "yfinance")})])
        self.assertEqual(quotes["A.US"].source, "yfinance")


class TestNanGuards(unittest.TestCase):
    def test_snapshot_tier_rejects_nan_close(self):
        # Yahoo returns NaN closes for halted/newly-delisted symbols;
        # unguarded, the NaN fossilized into the price cache and
        # surfaced as a bare NaN token in --json output.
        import sys
        import types
        import taxjson.lib.price_chain as pc

        class _Hist:
            empty = False

            def __init__(self):
                self.d = {"Close": types.SimpleNamespace(
                    iloc=[float("nan")])}

            def __getitem__(self, k):
                return self.d[k]

        class _Ticker:
            def __init__(self, sym):
                pass

            def history(self, **kw):
                return _Hist()
        fake_yf = types.SimpleNamespace(Ticker=_Ticker)
        sys.modules["yfinance"] = fake_yf
        try:
            out = pc._yfinance_fetcher({"AAA.TO": "AAA.TO"},
                                       verbose=False)
        finally:
            del sys.modules["yfinance"]
        self.assertEqual(out, {})

    def test_cache_write_refuses_nan_loudly(self):
        import io
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        from taxjson.lib.price_chain import _save_cache
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cache.json"
            buf = io.StringIO()
            with redirect_stderr(buf):
                _save_cache(p, {"AAA.TO": {"price": float("nan")}})
            self.assertFalse(p.exists(),
                             "a NaN must never fossilize into the "
                             "cache")
            self.assertIn("could not write price cache", buf.getvalue())


class TestOfflineSwitch(unittest.TestCase):
    """TAXJSON_OFFLINE covers the current-price chain too (harvest,
    watch --harvest, GUI harvest): live tiers skipped, cache served, a
    miss refused with a message naming the symbols — the same contract
    as the FX / crypto guards (SECURITY.md)."""

    def test_cache_served_and_miss_refused(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            cache.write_text(json.dumps({"A.US": {
                "price": 10.0, "asof": date.today().isoformat(),
                "source": "yfinance"}}))
            with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}):
                # Live tiers never consulted: no network module is
                # imported, the cache answers.
                quotes = fetch_prices({"A.US": "A"}, cache_path=cache)
                self.assertEqual(quotes["A.US"].price, 10.0)
                self.assertTrue(quotes["A.US"].source.startswith("cache"))
                with self.assertRaises(SystemExit) as cm:
                    fetch_prices({"A.US": "A", "B.US": "B"},
                                 cache_path=cache)
            msg = str(cm.exception)
            self.assertIn("TAXJSON_OFFLINE", msg)
            self.assertIn("B.US", msg)
            self.assertNotIn("A.US", msg)

    def test_injected_fetchers_unaffected(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}):
                quotes = fetch_prices(
                    {"A.US": "A"}, cache_path=Path(td) / "c.json",
                    fetchers=[tier({"A.US": (5.0, "test")})])
        self.assertEqual(quotes["A.US"].price, 5.0)


if __name__ == "__main__":
    unittest.main()
