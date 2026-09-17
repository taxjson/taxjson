"""Tests for taxjson-explain (CLI gain-trace tool).

The tool: reads transactions JSON, runs compute_gains with trace=True, and
prints filtered, formatted trace blocks for one or more gains. These tests
exercise the filter logic (gain_matches), summary formatter (fmt_summary),
and the rendering hook so refactors don't quietly change output shape.
"""

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from taxjson.bin.taxjson_explain import (
    fmt_summary,
    gain_matches,
    is_disposition_line,
    main as explain_main,
)


def _args(**overrides):
    """Build a default arg namespace matching what argparse would produce."""
    base = dict(
        country='canada', sheltered=None, symbol=None, date=None,
        gain_id=None, year=None, tax_date='trade', list=False,
        wash_sales=False, color=False, no_align=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestGainMatches(unittest.TestCase):
    def _gain(self, **kw):
        g = {'action': 'BUYSELL', 'symbol': 'AAPL.US', 'date': '2025-03-15',
             'date_settle': '2025-03-17', 'id': 'abc123def456',
             'is_wash_sale': False, 'gain': 100.0}
        g.update(kw)
        return g

    def test_skips_dividends(self):
        self.assertFalse(gain_matches(self._gain(action='DIVIDEND'), _args()))

    def test_symbol_prefix_match(self):
        self.assertTrue(gain_matches(self._gain(), _args(symbol='AAPL')))
        self.assertTrue(gain_matches(self._gain(), _args(symbol='AAPL.US')))
        self.assertFalse(gain_matches(self._gain(), _args(symbol='MSFT')))

    def test_date_exact_match_uses_trade_date_by_default(self):
        self.assertTrue(gain_matches(self._gain(), _args(date='2025-03-15')))
        self.assertFalse(gain_matches(self._gain(), _args(date='2025-03-17')))

    def test_date_filter_with_settle_basis(self):
        """--tax-date settle switches the date filter to date_settle."""
        self.assertTrue(gain_matches(
            self._gain(), _args(date='2025-03-17', tax_date='settle'),
        ))
        self.assertFalse(gain_matches(
            self._gain(), _args(date='2025-03-15', tax_date='settle'),
        ))

    def test_id_prefix_match(self):
        self.assertTrue(gain_matches(self._gain(), _args(gain_id='abc')))
        self.assertTrue(gain_matches(self._gain(), _args(gain_id='abc123def456')))
        self.assertFalse(gain_matches(self._gain(), _args(gain_id='xyz')))

    def test_year_filter(self):
        self.assertTrue(gain_matches(self._gain(), _args(year=2025)))
        self.assertFalse(gain_matches(self._gain(), _args(year=2024)))

    def test_wash_sales_filter(self):
        """--wash-sales filters down to gains where is_wash_sale is True."""
        self.assertFalse(gain_matches(self._gain(), _args(wash_sales=True)))
        self.assertTrue(gain_matches(
            self._gain(is_wash_sale=True), _args(wash_sales=True),
        ))


class TestFmtSummary(unittest.TestCase):
    def test_summary_contains_key_fields(self):
        g = {
            'id': 'abc123def456789', 'date': '2025-03-15', 'symbol': 'AAPL.US',
            'qty': 100.0, 'cost': 10000.0, 'proceeds': 11000.0, 'gain': 1000.0,
            'disallowed_amount': 0.0, 'direction': 'LONG',
        }
        out = fmt_summary(g)
        self.assertIn('AAPL.US', out)
        self.assertIn('2025-03-15', out)
        self.assertIn('LONG', out)
        # Truncated id (10 chars).
        self.assertIn('abc123def4', out)

    def test_summary_marks_wash_sales(self):
        g = {
            'id': 'x' * 16, 'date': '2025-01-01', 'symbol': 'X',
            'qty': 10, 'cost': 1000, 'proceeds': 800, 'gain': 0,
            'disallowed_amount': 200, 'direction': 'LONG',
        }
        out = fmt_summary(g)
        self.assertIn('WASH+200.00', out)

    def test_summary_marks_term_for_us(self):
        g = {
            'id': 'x' * 16, 'date': '2025-01-01', 'symbol': 'X',
            'qty': 10, 'cost': 1000, 'proceeds': 1100, 'gain': 100,
            'disallowed_amount': 0, 'direction': 'LONG', 'term': 'LONG_TERM',
        }
        out = fmt_summary(g)
        self.assertIn('[LONG_TERM]', out)


class TestIsDispositionLine(unittest.TestCase):
    def test_canada_gain_line(self):
        self.assertTrue(is_disposition_line(
            "# 2025-03-15 BUYSELL -100 @ 60 | Proceeds: 6000 | Cost_Basis: 5000 | Gain: 1000"
        ))

    def test_usa_sell_close_line(self):
        self.assertTrue(is_disposition_line(
            "# 2025-03-15 SELL-CLOSE 100 | Cost: 5000 | Proceeds: 6000 | RawGain: 1000 | AllowedGain: 1000"
        ))

    def test_non_disposition_line(self):
        self.assertFalse(is_disposition_line(
            "# 2025-01-15 BUY 100.0000 @ 50.0000 | Lot_Cost: 5001"
        ))


class TestExplainEndToEnd(unittest.TestCase):
    """Invoke main() with a small fixture and verify the printed trace
    block contains the expected substrings."""

    def _write_input(self, transactions):
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump({'transactions': transactions}, fh)
        fh.close()
        return fh.name

    def test_prints_trace_for_matching_gain(self):
        path = self._write_input([
            {'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'AAPL.US',
             'quantity': 100, 'price': 100.0, 'net_amount': -10000.00,
             'currency': 'USD', 'account': 'Margin'},
            {'action': 'BUYSELL', 'date': '2025-06-15', 'symbol': 'AAPL.US',
             'quantity': -100, 'price': 110.0, 'net_amount': 11000.00,
             'currency': 'USD', 'account': 'Margin'},
        ])
        captured = StringIO()
        with patch.object(sys, 'argv', ['taxjson-explain', '--country', 'usa',
                                         '--symbol', 'AAPL', path]):
            with patch.object(sys, 'stdout', captured):
                explain_main()
        out = captured.getvalue()
        self.assertIn('AAPL.US', out)
        self.assertIn('FIFO CALCULATION TRACE', out)
        # Header line shows the trade
        self.assertIn('2025-06-15', out)

    def test_list_mode_shows_one_line_per_gain(self):
        path = self._write_input([
            {'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'X',
             'quantity': 100, 'price': 100.0, 'net_amount': -10000.0,
             'currency': 'USD', 'account': 'M'},
            {'action': 'BUYSELL', 'date': '2025-06-15', 'symbol': 'X',
             'quantity': -100, 'price': 110.0, 'net_amount': 11000.0,
             'currency': 'USD', 'account': 'M'},
        ])
        captured = StringIO()
        with patch.object(sys, 'argv', ['taxjson-explain', '--country', 'usa',
                                         '--list', path]):
            with patch.object(sys, 'stdout', captured):
                explain_main()
        out = captured.getvalue()
        # The summary line carries the symbol and a direction tag
        self.assertIn('X', out)
        self.assertIn('LONG', out)
        # NOT the full trace box
        self.assertNotIn('FIFO CALCULATION TRACE', out)


if __name__ == '__main__':
    unittest.main()
