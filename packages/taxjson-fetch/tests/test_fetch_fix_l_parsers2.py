"""Moved from the core's tests/test_fix_l_parsers2_fetch.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Low-round parsers2 fixes: taxjson fetch (Questrade window/dates) and
taxjson-fill-crypto.

R1-354 (a future project year is refused, not a silent trailing 90
days), S031-15 (--from with --days refused), S031-10 (a missing
settlementDate falls back to the posting date, not blank), S025-07
(FMV x |qty| pinned at quantities other than 1), S025-08 (today's open
candle is not cached). No network: Yahoo and the cache are patched.
Synthetic data only.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import csv
import io
import unittest
from datetime import date
from taxjson_fetch import api as F


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


if __name__ == "__main__":
    unittest.main()
