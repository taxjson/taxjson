"""Low-round parsers2 fixes: taxjson-fill-crypto (the `taxjson fetch`
half moved to packages/taxjson-fetch with the fetcher).

S025-07 (FMV x |qty| pinned at quantities other than 1), S025-08
(today's open candle is not cached). No network: Yahoo and the cache
are patched. Synthetic data only.
"""
import io
import json
import sys
import unittest
from unittest.mock import patch

from tax_rules import rule


def _fill(txs, cache, prices=None):
    from taxjson.bin import fill_crypto_prices as fcp
    stdin = io.StringIO(json.dumps({"transactions": txs}))
    stdout = io.StringIO()
    stderr = io.StringIO()
    saved = {}
    calls = []

    def fake_price(sym, day):
        calls.append((sym, day))
        return (prices or {}).get((sym, day), 0.0)

    with patch.object(fcp, "load_cache", lambda: dict(cache)), \
            patch.object(fcp, "save_cache", lambda c: saved.update(c)), \
            patch.object(fcp, "get_crypto_price", fake_price), \
            patch.object(fcp.time, "sleep", lambda s: None), \
            patch.object(sys, "argv", ["taxjson-fill-crypto"]), \
            patch.object(sys, "stdin", stdin), \
            patch.object(sys, "stdout", stdout), \
            patch.object(sys, "stderr", stderr):
        fcp.main()
    return (json.loads(stdout.getvalue())["transactions"], saved, calls,
            stderr.getvalue())


def _row(**kw):
    base = {"action": "BUYSELL", "date": "2025-06-02", "time": "10:00:00",
            "symbol": "ETH", "quantity": 1.0, "price": 0.0,
            "net_amount": 0.0, "currency": "USD", "account": "wallet"}
    base.update(kw)
    return base


class TestFillValue(unittest.TestCase):
    """S025-07: the suite only ever filled quantity-1 rows, where FMV
    and FMV x quantity are the same number."""

    def test_net_is_fmv_times_quantity(self):
        out, _, _, _ = _fill(
            [_row(quantity=-0.5),
             _row(symbol="SOL", quantity=40.0),
             _row(action="DIVIDEND", quantity=0.01)],
            {"ETH-2025-06-02": 2500.0, "SOL-2025-06-02": 150.0})
        self.assertEqual([round(t["net_amount"], 2) for t in out],
                         [1250.0, 6000.0, 25.0])


class TestTodayNotCached(unittest.TestCase):
    """S025-08."""

    @rule("CA-INC-04")
    @rule("US-INC-02")
    def test_open_candle_used_but_not_cached(self):
        from taxjson.bin import fill_crypto_prices as fcp
        today = fcp._utc_today()
        out, saved, calls, err = _fill(
            [_row(action="DIVIDEND", symbol="SOL", quantity=2.0,
                  date=today)], {}, {("SOL", today): 100.0})
        self.assertEqual(out[0]["net_amount"], 200.0)
        self.assertNotIn(f"SOL-{today}", saved)
        self.assertIn("not cached", err)

    def test_past_day_is_cached(self):
        out, saved, _, _ = _fill([_row(symbol="SOL")], {},
                                 {("SOL", "2025-06-02"): 150.0})
        self.assertEqual(saved.get("SOL-2025-06-02"), 150.0)


if __name__ == "__main__":
    unittest.main()
