"""Low-round parsers2 fixes: taxjson fetch (Questrade window/dates) and
taxjson-fill-crypto.

R1-354 (a future project year is refused, not a silent trailing 90
days), S031-15 (--from with --days refused), S031-10 (a missing
settlementDate falls back to the posting date, not blank), S025-07
(FMV x |qty| pinned at quantities other than 1), S025-08 (today's open
candle is not cached). No network: Yahoo and the cache are patched.
Synthetic data only.
"""
import csv
import io
import json
import sys
import unittest
from datetime import date
from unittest.mock import patch

from taxjson.bin import taxjson_fetch as F
from tax_rules import rule


class TestQtWindow(unittest.TestCase):

    def test_future_year_refused(self):
        with self.assertRaises(ValueError) as cm:
            F.qt_window(None, None, today=date(2026, 9, 29), year=2027)
        self.assertIn("2027", str(cm.exception))

    def test_current_year_window(self):
        self.assertEqual(
            F.qt_window(None, None, today=date(2026, 9, 29), year=2026),
            (date(2025, 12, 1), date(2026, 9, 29)))

    def test_from_with_days_refused(self):
        with self.assertRaises(ValueError):
            F.qt_window(365, "2026-09-01", today=date(2026, 9, 29))

    def test_no_year_trailing_90_days(self):
        self.assertEqual(F.qt_window(None, None, today=date(2026, 9, 29)),
                         (date(2026, 7, 1), date(2026, 9, 29)))


class TestQtToCsvDates(unittest.TestCase):
    """S031-10."""

    def test_missing_settlement_uses_the_posting_date(self):
        text = F.qt_to_csv([{
            "tradeDate": "2025-12-30T00:00:00.000000-05:00",
            "transactionDate": "2025-12-31T00:00:00.000000-05:00",
            "settlementDate": "", "action": "Sell", "symbol": "XYZ.TO",
            "description": "XYZ CORP", "quantity": -10, "price": 5,
            "grossAmount": 50, "commission": -1, "netAmount": 49,
            "currency": "CAD", "type": "Trades"}], "55500001")  # pii-ok
        row = list(csv.DictReader(io.StringIO(text)))[0]
        self.assertTrue(row["Transaction Date"].startswith("2025-12-30"))
        self.assertTrue(row["Settlement Date"].startswith("2025-12-31"))


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
