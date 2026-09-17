"""Tests for the trace_format module.

The module emits all the user-facing trace text that taxjson-gains writes
to its --full-traces FILE and that taxjson-explain prints. Drift here means
silently broken human-readable output, so we lock in shape and content.
"""

import unittest

from taxjson.lib.trace_format import (
    align_pipe_lines,
    render_document_header,
    render_gain_block,
    render_summary_table,
)


class TestAlignPipeLines(unittest.TestCase):
    def test_aligns_two_pipe_rows(self):
        lines = [
            "# 2025-01-15 BUY | qty=10 | px=50",
            "# 2025-02-15 SELL | qty=100 | px=51",
        ]
        out = align_pipe_lines(lines)
        # The bars must be at the same column on both rows.
        bar_positions = [[i for i, c in enumerate(line) if c == '|'] for line in out]
        self.assertEqual(bar_positions[0], bar_positions[1])

    def test_passes_plain_lines_through(self):
        lines = [
            "# --- HEADER ---",
            "# 2025-01-15 BUY | qty=10",
            "# === RULE ===",
        ]
        out = align_pipe_lines(lines)
        self.assertEqual(out[0], "# --- HEADER ---")
        self.assertEqual(out[2], "# === RULE ===")

    def test_handles_empty_input(self):
        self.assertEqual(align_pipe_lines([]), [])

    def test_rows_with_different_column_counts(self):
        """A 3-col row and a 5-col row don't blow up; both render."""
        lines = [
            "# a | b | c",
            "# a | b | c | d | e",
        ]
        out = align_pipe_lines(lines)
        self.assertEqual(len(out), 2)
        # Both should still have their bars.
        self.assertGreater(out[0].count('|'), 0)
        self.assertGreater(out[1].count('|'), 0)


class TestRenderGainBlock(unittest.TestCase):
    def _basic_gain(self, **overrides):
        g = {
            'symbol': 'AAPL.US',
            'date': '2025-03-15',
            'qty': 100.0,
            'cost': 10000.0,
            'proceeds': 11000.0,
            'gain': 1000.0,
            'raw_gain': 1000.0,
            'disallowed_amount': 0.0,
            'days_held': 60,
            'account': 'Margin',
            'id': 'abc123def456',
            'direction': 'LONG',
            'term': 'SHORT_TERM',
            'trace': [
                "# --- FIFO CALCULATION TRACE: AAPL.US ---",
                "# 2025-01-15 BUY  100.0000 @ 100.0000 | Lot_Cost: 10000.00",
                "# 2025-03-15 SELL 100.0000 | Cost: 10000.00 | Proceeds: 11000.00 | Gain: 1000.00",
            ],
        }
        g.update(overrides)
        return g

    def test_block_has_rule_header_trace_rule(self):
        out = render_gain_block(self._basic_gain())
        # rule, line1, line2 (secondary), rule, ...trace..., rule
        self.assertTrue(out[0].startswith('# =='))
        self.assertTrue(out[-1].startswith('# =='))
        self.assertIn('AAPL.US', out[1])
        self.assertIn('2025-03-15', out[1])
        self.assertIn('+$1,000.00', out[1])

    def test_block_empty_when_no_trace(self):
        g = self._basic_gain(trace=[])
        self.assertEqual(render_gain_block(g), [])

    def test_secondary_line_has_days_account_id(self):
        out = render_gain_block(self._basic_gain())
        sec = out[2]
        self.assertIn('days_held=60', sec)
        self.assertIn('account=Margin', sec)
        self.assertIn('id=abc123def456', sec)

    def test_term_appears_in_header(self):
        out = render_gain_block(self._basic_gain(term='LONG_TERM'))
        self.assertTrue(any('[LONG_TERM]' in line for line in out[:3]))

    def test_wash_sale_header_shows_raw_and_disallowed(self):
        g = self._basic_gain(
            gain=0.0, raw_gain=-1000.0, disallowed_amount=1000.0,
        )
        out = render_gain_block(g)
        # Header should mention both raw loss and disallowance.
        self.assertTrue(any('-$1,000.00' in line for line in out[:3]))
        self.assertTrue(any('disallowed' in line.lower() for line in out[:3]))

    def test_us_long_wash_explanation_block(self):
        g = self._basic_gain(
            gain=0.0, raw_gain=-1000.0, disallowed_amount=1000.0,
            wash_replacements=[{
                'tx_id': 'rep1', 'date': '2025-03-25', 'qty_total': 100.0,
                'price': 100.0, 'account': 'Margin', 'match_qty': 100.0,
                'basis_bump': 1000.0, 'is_sheltered': False,
            }],
        )
        out = render_gain_block(g)
        joined = "\n".join(out)
        self.assertIn('WASH SALE (IRC §1091)', joined)
        self.assertIn('basis bump=+$1,000.00', joined)
        self.assertIn('§1223(3)', joined, msg="long-side §1223(3) note should appear")
        self.assertIn('BUY', joined)

    def test_us_short_wash_explanation_uses_proceeds_cut(self):
        g = self._basic_gain(
            direction='SHORT',
            cost=-1200.0, proceeds=-1000.0,
            gain=0.0, raw_gain=-200.0, disallowed_amount=200.0,
            wash_replacements=[{
                'tx_id': 'rep1', 'date': '2025-03-25', 'qty_total': -1.0,
                'price': 110.0, 'account': 'Margin', 'match_qty': 1.0,
                'proceeds_reduction': 200.0, 'is_sheltered': False,
            }],
        )
        out = render_gain_block(g)
        joined = "\n".join(out)
        self.assertIn('WASH SALE (IRC §1091 short)', joined)
        self.assertIn('proceeds cut=+$200.00', joined)
        self.assertIn('SELL', joined)
        # §1223(3) note is long-only; must NOT appear on a short wash.
        self.assertNotIn('§1223(3)', joined)

    def test_us_permanent_disallowance_header(self):
        g = self._basic_gain(
            gain=0.0, raw_gain=-1000.0,
            disallowed_amount=1000.0,
            permanently_disallowed=1000.0,
            wash_replacements=[{
                'tx_id': 'ira_rep', 'date': '2025-03-25', 'qty_total': 100.0,
                'price': 100.0, 'account': 'IRA', 'match_qty': 100.0,
                'basis_bump': 1000.0, 'is_sheltered': True,
            }],
        )
        out = render_gain_block(g)
        joined = "\n".join(out)
        self.assertIn('permanent disallowance', joined.lower())
        self.assertIn('Rev. Rul. 2008-5', joined)
        self.assertIn('[sheltered]', joined)

    def test_canada_wash_trigger_block(self):
        g = self._basic_gain(
            gain=0.0, raw_gain=-201.50, disallowed_amount=201.50,
            wash_trigger={
                'is_full_disallowance': True,
                'loss_raw_gain': -201.50,
                'disallowed_amount': 201.50,
                'trigger_tx_id': 'tx_rep',
                'trigger_date': '2025-07-20',
                'trigger_qty': 15.0,
                'trigger_price': 71.0,
                'trigger_account': 'Margin',
                'trigger_sheltered': False,
                'adjust_amount': 201.50,
                'adjust_date': '2025-07-20',
            },
        )
        out = render_gain_block(g)
        joined = "\n".join(out)
        self.assertIn('WASH SALE (CRA superficial loss)', joined)
        self.assertIn('triggered by 2025-07-20', joined)
        self.assertIn('+$201.50', joined)
        self.assertIn('ACB pool bumped', joined)


class TestRenderSummaryTable(unittest.TestCase):
    def test_basic_per_symbol_summary(self):
        gains = [
            {'symbol': 'AAPL.US', 'qty': 100, 'gain': 1000, 'disallowed_amount': 0},
            {'symbol': 'AAPL.US', 'qty': 50, 'gain': 500, 'disallowed_amount': 0},
            {'symbol': 'SHOP.TO', 'qty': 10, 'gain': -200, 'disallowed_amount': 200},
        ]
        out = render_summary_table(gains)
        joined = "\n".join(out)
        self.assertIn('AAPL.US', joined)
        self.assertIn('SHOP.TO', joined)
        # AAPL: 2 sales, total qty 150, total gain $1,500
        self.assertIn('+$1,500.00', joined)
        # SHOP: disallowed $200
        self.assertIn('$200.00', joined)
        # Always has a TOTAL row.
        self.assertIn('TOTAL', joined)

    def test_excludes_dividends(self):
        gains = [
            {'symbol': 'AAPL.US', 'qty': 100, 'gain': 1000},
            {'symbol': 'AAPL.US', 'action': 'DIVIDEND', 'dividend': 50},
        ]
        out = render_summary_table(gains)
        joined = "\n".join(out)
        # AAPL row appears once (the dividend is filtered out).
        self.assertEqual(joined.count('AAPL.US'), 1)

    def test_empty_gains_returns_empty_table(self):
        self.assertEqual(render_summary_table([]), [])


class TestRenderDocumentHeader(unittest.TestCase):
    def test_header_lists_all_metadata(self):
        out = render_document_header(
            input_path='margin.base.json', country='canada', year='2025',
            tax_date_basis='settle', disposition_count=42, wash_sale_count=3,
            total_gain=12345.67, total_disallowed=890.00,
        )
        joined = "\n".join(out)
        self.assertIn('taxjson-gains audit trace', joined)
        self.assertIn('margin.base.json', joined)
        self.assertIn('CANADA', joined)
        self.assertIn('2025 (settle basis)', joined)
        self.assertIn('42', joined)   # dispositions
        self.assertIn('3', joined)    # wash sales
        self.assertIn('+$12,345.67', joined)
        self.assertIn('$890.00', joined)

    def test_header_handles_all_years(self):
        out = render_document_header(
            input_path=None, country='usa', year=None,
            tax_date_basis='trade', disposition_count=0, wash_sale_count=0,
            total_gain=0.0, total_disallowed=0.0,
        )
        joined = "\n".join(out)
        self.assertIn('all years', joined)
        self.assertIn('<stdin>', joined)


if __name__ == '__main__':
    unittest.main()
