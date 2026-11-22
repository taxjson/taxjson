"""Regression tests for the post-review correctness fixes.

Each test exercises a specific finding from the code review so that future
refactors don't silently regress the behavior.

Formerly: test_review_fixes
"""

import unittest

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


class TestUSHoldingPeriod(unittest.TestCase):
    """ST/LT classification: 'more than one year' via anniversary, not 365 days.

    Formerly: test_review_fixes.TestUSHoldingPeriod"""

    @rule(R.US_HOLD_01)
    def test_one_year_exact_is_short_term(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-03-15', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            # Sold exactly one year later → still ST per IRS (more than one year required)
            TaxTransaction(action='BUYSELL', date='2025-03-15', symbol='X', quantity=-100.0, net_amount=11000.0, currency='USD', id='s1'),
        ]
        result = rules.compute_gains(txs)
        sale = next(g for g in result['transactions'] if g.get('id') == 's1')
        self.assertEqual(sale['term'], 'SHORT_TERM')

    def test_one_year_plus_one_day_is_long_term(self):
        rules = USATaxRules()
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-03-15', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-03-16', symbol='X', quantity=-100.0, net_amount=11000.0, currency='USD', id='s1'),
        ]
        result = rules.compute_gains(txs)
        sale = next(g for g in result['transactions'] if g.get('id') == 's1')
        self.assertEqual(sale['term'], 'LONG_TERM')


if __name__ == '__main__':
    unittest.main()
