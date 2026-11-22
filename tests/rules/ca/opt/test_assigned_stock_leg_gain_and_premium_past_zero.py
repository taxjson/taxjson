"""Regression tests for the post-review correctness fixes.

Each test exercises a specific finding from the code review so that future
refactors don't silently regress the behavior.

Formerly: test_review_fixes
"""

import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction


class TestAssignmentEdgeCases(unittest.TestCase):
    """ASSIGN-action handling edge cases discovered during tt comparison.

    Formerly: test_review_fixes.TestAssignmentEdgeCases"""

    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_stock_leg_assign_emits_gain(self):
        """Stock leg of an option assignment recorded as ASSIGN must still
        produce a realized gain entry. tt_gains.pl previously rolled the gain
        into pending_adjustments and orphaned it; taxjson must not regress."""
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='AAPL.US',
                           quantity=100.0, currency='USD', net_amount=15000.0, id='b'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL250620C00200000.US',
                           quantity=-1.0, currency='USD', net_amount=500.0, id='sto'),
            TaxTransaction(action='ASSIGN', date='2025-06-20',
                           symbol='AAPL250620C00200000.US',
                           quantity=1.0, currency='USD', net_amount=0.0, id='opt_assign'),
            # Stock-leg recorded as ASSIGN (taxjson IB extractor convention).
            TaxTransaction(action='ASSIGN', date='2025-06-20', time='16:00:01',
                           symbol='AAPL.US',
                           quantity=-100.0, currency='USD', net_amount=20000.0, id='stk_assign'),
        ]
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND' and g.get('symbol') == 'AAPL.US']
        self.assertEqual(len(sells), 1, "stock-leg ASSIGN must emit a gain entry")
        # Cost = 15000, proceeds = 20000 + 500 (rolled premium) = 20500, gain = 5500.
        self.assertAlmostEqual(sells[0]['cost'], 15000.0, places=2)
        self.assertAlmostEqual(sells[0]['proceeds'], 20500.0, places=2)
        self.assertAlmostEqual(sells[0]['gain'], 5500.0, places=2)
        # No option-leg gain entry should appear.
        opt_entries = [g for g in result['transactions']
                       if g.get('symbol', '').startswith('AAPL250620C')]
        self.assertEqual(len(opt_entries), 0)

    def test_cross_zero_assign_apportions_premium_to_leftover(self):
        """If a stock ASSIGN crosses inventory zero (e.g. assigned on more
        shares than held), the rolled-in premium must be apportioned to the
        new opposite-direction position too — not just consumed by the close.

        Hold 50 stock @ $100, sell 1 call (premium $500), assigned on the
        full 100-share contract (over-sells by 50 shares).
        After close: realize 50 closed shares + premium share apportioned to
        them. After leftover open: 50-share short carrying the remaining
        premium share into its cost basis.
        """
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='AAPL.US',
                           quantity=50.0, currency='USD', net_amount=5000.0, id='b'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL250620C00120000.US',
                           quantity=-1.0, currency='USD', net_amount=500.0, id='sto'),
            TaxTransaction(action='ASSIGN', date='2025-06-20',
                           symbol='AAPL250620C00120000.US',
                           quantity=1.0, currency='USD', net_amount=0.0, id='opt_assign'),
            # 100-share assignment crosses zero (held 50, sells 100 → -50 leftover).
            TaxTransaction(action='ASSIGN', date='2025-06-20', time='16:00:01',
                           symbol='AAPL.US',
                           quantity=-100.0, currency='USD', net_amount=12000.0, id='stk_assign'),
        ]
        result = self.rules.compute_gains(txs)
        sells = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND' and g.get('symbol') == 'AAPL.US']
        self.assertEqual(len(sells), 1)
        # Closing 50 shares: cost=5000 (full lot), proceeds = 6000 + 250
        # (half of the $500 premium apportioned) = 6250, gain = 1250.
        self.assertAlmostEqual(sells[0]['cost'], 5000.0, places=2)
        self.assertAlmostEqual(sells[0]['proceeds'], 6250.0, places=2)
        self.assertAlmostEqual(sells[0]['gain'], 1250.0, places=2)
        # Leftover -50-share short carries the other half of the premium.
        # On a short open, the premium adds to the proceeds-equivalent cost
        # basis (raising the breakeven cover price): half stock proceeds +
        # half premium = 6000 + 250 = 6250. The remaining premium dollars
        # will be realized when the short is later covered.
        inv = {i['symbol']: i for i in result['inventory']}
        self.assertAlmostEqual(inv['AAPL.US']['qty'], -50.0)
        # NEGATIVE per the repo-wide short-inventory convention (proceeds
        # credited; FUZZ #21 aligned the Canada engine's emission with
        # the USA engine and the holdings export): the 6250 basis is a
        # credit, and cost_per_share = total_cost/qty = +125/share.
        self.assertAlmostEqual(inv['AAPL.US']['total_cost'], -6250.0, places=2)


if __name__ == '__main__':
    unittest.main()
