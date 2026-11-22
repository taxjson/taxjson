"""Regression tests for the post-review correctness fixes.

Each test exercises a specific finding from the code review so that future
refactors don't silently regress the behavior.

Formerly: test_review_fixes
"""

import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction


class TestDividendGrossReporting(unittest.TestCase):
    """Dividends should be reported gross of foreign withholding tax.

    Brokerage adapters (e.g. RBC) populate gross_amount with the pre-withhold
    figure and emit a separate TAX record for the withholding. The gains
    engine should pass the gross figure through to the dividend total so it
    matches T5/T3/1099-DIV box 1a; the TAX record continues to feed the
    foreign tax credit independently.

    Formerly: test_review_fixes.TestDividendGrossReporting
    """

    def test_canada_dividend_uses_gross_when_present(self):
        rules = CanadaTaxRules()
        # 100 USD dividend, 15 USD withheld → net 85, gross 100.
        txs = [
            TaxTransaction(action='DIVIDEND', date='2025-03-31', symbol='AAPL.US',
                           currency='USD', net_amount=85.0, gross_amount=100.0,
                           type='dividend', id='div1'),
            TaxTransaction(action='TAX', date='2025-03-31', symbol='AAPL.US',
                           currency='USD', net_amount=15.0, type='tax', id='tax1'),
        ]
        result = rules.compute_gains(txs)
        div_entries = [t for t in result['transactions'] if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(div_entries), 1)
        self.assertAlmostEqual(div_entries[0]['dividend'], 100.0,
                               msg="dividend total must use gross_amount, not net_amount")
        self.assertAlmostEqual(result['by_ticker']['AAPL.US']['total_div'], 100.0)

    def test_canada_dividend_falls_back_to_net_when_gross_missing(self):
        """For older data lacking gross_amount, net_amount is used as-is."""
        rules = CanadaTaxRules()
        txs = [
            TaxTransaction(action='DIVIDEND', date='2025-03-31', symbol='TD.TO',
                           currency='CAD', net_amount=42.0,  # gross_amount defaults to 0
                           type='dividend', id='div1'),
        ]
        result = rules.compute_gains(txs)
        div_entries = [t for t in result['transactions'] if t.get('action') == 'DIVIDEND']
        self.assertAlmostEqual(div_entries[0]['dividend'], 42.0)

    def test_usa_dividend_uses_gross_when_present(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='DIVIDEND', date='2025-03-31', symbol='AAPL',
                           currency='USD', net_amount=85.0, gross_amount=100.0,
                           type='dividend', id='div1'),
        ]
        result = rules.compute_gains(txs)
        div_entries = [t for t in result['transactions'] if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(div_entries), 1)
        self.assertAlmostEqual(div_entries[0]['dividend'], 100.0)


if __name__ == '__main__':
    unittest.main()
