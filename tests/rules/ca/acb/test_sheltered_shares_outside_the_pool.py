"""Regression tests for the post-review correctness fixes.

Each test exercises a specific finding from the code review so that future
refactors don't silently regress the behavior.

Formerly: test_review_fixes
"""

import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction


class TestC5ShelteredPoolSemantics(unittest.TestCase):
    """C5: sheltered shares must not enter the taxable ACB pool's qty.

    Before the fix, sheltered shares inflated pool['qty'] which diluted the
    average cost (total_cost / pool_qty) used to compute the cost basis on
    a taxable sell.

    Formerly: test_review_fixes.TestC5ShelteredPoolSemantics
    """

    def test_sheltered_shares_do_not_dilute_taxable_acb(self):
        rules = CanadaTaxRules()
        # 50 taxable shares at $100 each.
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=50.0, net_amount=5000.0, currency='CAD', id='taxable_buy'),
            TaxTransaction(action='BUYSELL', date='2025-02-01', symbol='X', quantity=-30.0, net_amount=3300.0, currency='CAD', id='taxable_sell'),
        ]
        # 100 sheltered shares (RRSP) at $100 each. These should NOT contribute
        # to the taxable ACB pool's average-cost denominator.
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=100.0, net_amount=10000.0, currency='CAD', id='shelt_buy'),
        ]
        result = rules.compute_gains(taxable, sheltered_transactions=sheltered)

        sells = [g for g in result['transactions'] if g.get('id') == 'taxable_sell']
        self.assertEqual(len(sells), 1)
        # ACB per share is $100 (taxable cost basis) regardless of sheltered.
        # Selling 30 shares at $110 → cost 3000, proceeds 3300, gain $300.
        self.assertAlmostEqual(sells[0]['cost'], 3000.0, places=2)
        self.assertAlmostEqual(sells[0]['gain'], 300.0, places=2)


if __name__ == '__main__':
    unittest.main()
