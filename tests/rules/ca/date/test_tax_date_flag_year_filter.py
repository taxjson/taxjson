"""Regression tests for the post-review correctness fixes.

Each test exercises a specific finding from the code review so that future
refactors don't silently regress the behavior.

Formerly: test_review_fixes
"""

import sys
import unittest
from taxjson.model.transaction import TaxTransaction


class TestTaxDateBasis(unittest.TestCase):
    """The --tax-date flag selects trade vs settlement date for year filtering.

    Trades that close on Dec 30/31 and settle on Jan 1/2 are the canonical
    case where this matters. tt uses settle date; taxjson defaults to trade
    date but can be switched for cross-tool comparison.

    Formerly: test_review_fixes.TestTaxDateBasis
    """

    def _run_gains(self, txs, year, tax_date):
        import json
        import subprocess
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / 'in.json'
            input_path.write_text(json.dumps({
                'transactions': [
                    {k: v for k, v in t.__dict__.items()} for t in txs
                ]
            }))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_gains',
                 '--country', 'canada',
                 '--year', str(year),
                 '--tax-date', tax_date,
                 str(input_path)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

    def test_dec_close_settling_in_january(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-12-20', date_settle='2025-12-22',
                           symbol='ABC.US', quantity=100.0, currency='USD',
                           net_amount=10000.0, id='b1'),
            # Closing trade: trade Dec 31, settle Jan 1, 2026.
            TaxTransaction(action='BUYSELL', date='2025-12-31', date_settle='2026-01-01',
                           symbol='ABC.US', quantity=-100.0, currency='USD',
                           net_amount=11500.0, id='s1'),
        ]
        # Default (trade-date): close falls in 2025.
        r_trade = self._run_gains(txs, 2025, 'trade')
        self.assertEqual(len(r_trade['transactions']), 1)
        self.assertAlmostEqual(r_trade['summary']['total_gain'], 1500.0)
        self.assertEqual(r_trade['summary']['tax_date_basis'], 'trade')
        # Settle-date: close falls in 2026, so 2025 is empty.
        r_settle = self._run_gains(txs, 2025, 'settle')
        self.assertEqual(len(r_settle['transactions']), 0)
        self.assertAlmostEqual(r_settle['summary']['total_gain'], 0.0)
        self.assertEqual(r_settle['summary']['tax_date_basis'], 'settle')
        # And settle-date 2026 picks up the same gain.
        r_settle_2026 = self._run_gains(txs, 2026, 'settle')
        self.assertEqual(len(r_settle_2026['transactions']), 1)
        self.assertAlmostEqual(r_settle_2026['summary']['total_gain'], 1500.0)


if __name__ == '__main__':
    unittest.main()
