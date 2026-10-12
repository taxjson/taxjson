"""Tests for taxjson-sum-income's handling of empty / interest-only inputs.

The original code only collected currencies from ticker_stats, so an
input with only INTEREST rows (no dividends) silently dropped the
report. Empty input produced no output at all. Both cases now render.
"""
import unittest

from taxjson.bin.taxjson_sum_income import format_report, summarize_income


class TestEmptyInput(unittest.TestCase):
    def test_empty_transactions_renders_zero_table(self):
        report = summarize_income([], target_year=2025)
        out = format_report(report)
        self.assertIn('INCOME SUMMARY', out)
        # Header should drop "(currency)" tag when there's no currency.
        self.assertNotIn('()', out)
        self.assertIn('TOTAL NET INCOME', out)
        self.assertIn('0.00', out)


class TestInterestOnlyInput(unittest.TestCase):
    def test_interest_only_renders_cash_interest_block(self):
        txs = [
            {'action': 'INTEREST', 'date': '2025-01-15', 'symbol': 'CASH',
             'currency': 'USD', 'net_amount': 25.50},
            {'action': 'INTEREST', 'date': '2025-06-20', 'symbol': 'CASH',
             'currency': 'USD', 'net_amount': 12.30},
        ]
        report = summarize_income(txs, target_year=2025)
        out = format_report(report)
        self.assertIn('INCOME SUMMARY — USD', out)
        self.assertIn('CASH INTEREST', out)
        self.assertIn('37.80', out)


class TestDividendAndInterestSameCurrency(unittest.TestCase):
    def test_both_collected_under_one_block(self):
        txs = [
            {'action': 'DIVIDEND', 'date': '2025-03-15', 'symbol': 'AAPL.US',
             'currency': 'USD', 'gross_amount': 100.00, 'type': 'dividend'},
            {'action': 'INTEREST', 'date': '2025-06-20', 'symbol': 'CASH',
             'currency': 'USD', 'net_amount': 15.00},
        ]
        report = summarize_income(txs, target_year=2025)
        out = format_report(report)
        # Both should appear under a single USD block.
        self.assertEqual(out.count('INCOME SUMMARY'), 1)
        self.assertIn('AAPL', out)
        self.assertIn('100.00', out)
        self.assertIn('15.00', out)


class TestMultiCurrency(unittest.TestCase):
    def test_separate_blocks_per_currency(self):
        txs = [
            {'action': 'DIVIDEND', 'date': '2025-03-15', 'symbol': 'AAPL.US',
             'currency': 'USD', 'gross_amount': 100.00, 'type': 'dividend'},
            {'action': 'INTEREST', 'date': '2025-06-20', 'symbol': 'CASH',
             'currency': 'CAD', 'net_amount': 30.00},
        ]
        report = summarize_income(txs, target_year=2025)
        out = format_report(report)
        self.assertIn('INCOME SUMMARY — USD', out)
        self.assertIn('INCOME SUMMARY — CAD', out)
        self.assertEqual(out.count('INCOME SUMMARY'), 2)




class TestOptionSymbolGroupsUnderUnderlying(unittest.TestCase):
    """Audit finding #9: sum_income's get_base_ticker was an identity
    function while sum_gains grouped options under the underlying — the
    two halves of one .sum bucketed differently. A PIL row on an OCC
    option symbol must now land under the underlying, matching the gains
    table."""

    def test_pil_on_occ_symbol(self):
        from taxjson.bin.taxjson_sum_income import (get_base_ticker,
                                                    summarize_income)
        self.assertEqual(get_base_ticker("AAPL260116C00150000.US"),
                         "AAPL.US")
        self.assertEqual(get_base_ticker("SHOP.TO"), "SHOP.TO")
        self.assertEqual(get_base_ticker(".DOL.TO"), "DOL.TO")
        res = summarize_income([{
            "action": "DIVIDEND_IN_LIEU", "symbol":
            "AAPL260116C00150000.US", "date": "2026-03-01",
            "currency": "USD", "net_amount": 12.0, "gross_amount": 12.0,
        }])
        self.assertIn("AAPL.US", res["ticker_stats"])
        self.assertNotIn("AAPL260116C00150000.US", res["ticker_stats"])


if __name__ == "__main__":
    unittest.main()
