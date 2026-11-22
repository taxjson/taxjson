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
from _support.engine_rules import assert_rule, rules_of


class TestC7USWashSaleBasisAdjustment(unittest.TestCase):
    """C7: disallowed loss must transfer to replacement-lot basis (IRC §1091).

    Formerly: test_review_fixes.TestC7USWashSaleBasisAdjustment"""

    @rule(R.US_WASH_01, R.US_WASH_09)
    def test_full_basis_transfer_then_sell_replacement(self):
        rules = USATaxRules()
        # Buy 100 @ 100, Sell 100 @ 80 (loss 2000), Buy 100 @ 85 within 30 days,
        # later Sell 100 @ 110.
        # Without wash adjustment: sell #1 = -2000, sell #2 = +2500. Net = +500.
        # With wash adjustment: sell #1 disallowed (gain 0), basis on lot 2
        # bumped from 8500 to 10500. sell #2 = 11000-10500 = +500. Net = +500.
        # The TOTAL is unchanged but the DISTRIBUTION differs and the eventual
        # taxable amount must reflect the basis bump.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1_loss'),
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X', quantity=100.0, net_amount=8500.0, currency='USD', id='b2_replacement'),
            TaxTransaction(action='BUYSELL', date='2025-06-01', symbol='X', quantity=-100.0, net_amount=11000.0, currency='USD', id='s2'),
        ]
        result = rules.compute_gains(txs)
        sells = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND' and g.get('direction') == 'LONG']
        # Loss sale: loss disallowed, gain reported as 0
        loss_sale = next(g for g in sells if g['id'] == 's1_loss')
        self.assertTrue(loss_sale['is_wash_sale'])
        self.assertAlmostEqual(loss_sale['gain'], 0.0, places=2)
        self.assertAlmostEqual(loss_sale['disallowed_amount'], 2000.0, places=2)
        # Replacement sale: cost basis bumped from 8500 to 10500,
        # so gain = 11000 - 10500 = 500 (NOT 11000 - 8500 = 2500).
        replacement_sale = next(g for g in sells if g['id'] == 's2')
        self.assertAlmostEqual(replacement_sale['cost'], 10500.0, places=2)
        self.assertAlmostEqual(replacement_sale['gain'], 500.0, places=2)
        # And the total taxable across both = 0 + 500 = 500
        self.assertAlmostEqual(sum(g['gain'] for g in sells), 500.0, places=2)
        # Each record names its rule (PLAN step 11): the sale's
        # disallowance and the replacement's basis add.
        assert_rule(self, result, 's1_loss', R.US_WASH_01, record='s1_loss')
        assert_rule(self, result, 's1_loss', R.US_WASH_09, record='b2_replacement')
        self.assertEqual(rules_of(result, 's2'), {})     # a gain: no record

    def test_partial_replacement_partial_disallowance(self):
        rules = USATaxRules()
        # Sell 100 at a loss, but only buy back 30 within 30 days.
        # 30 shares' worth of loss is disallowed; 70 shares' worth remains real.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1_loss'),
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X', quantity=30.0, net_amount=2400.0, currency='USD', id='b2_partial'),
        ]
        result = rules.compute_gains(txs)
        loss_sale = next(g for g in result['transactions'] if g.get('id') == 's1_loss')
        # Loss per share = 20. 30 shares disallowed = 600. 70 shares allowed = -1400.
        self.assertAlmostEqual(loss_sale['disallowed_amount'], 600.0, places=2)
        self.assertAlmostEqual(loss_sale['gain'], -1400.0, places=2)
        self.assertAlmostEqual(loss_sale['raw_gain'], -2000.0, places=2)

    @rule(R.US_WASH_11)
    def test_sheltered_replacement_permanent_disallowance(self):
        """Rev. Rul. 2008-5: replacement in IRA → loss is permanently denied."""
        rules = USATaxRules()
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='X', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1_loss'),
        ]
        # Replacement is in the sheltered (IRA) account
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='X', quantity=100.0, net_amount=8500.0, currency='USD', id='ira_buy'),
        ]
        result = rules.compute_gains(taxable, sheltered_transactions=sheltered)
        loss_sale = next(g for g in result['transactions'] if g.get('id') == 's1_loss')
        self.assertTrue(loss_sale['is_wash_sale'])
        self.assertAlmostEqual(loss_sale['disallowed_amount'], 2000.0, places=2)
        self.assertAlmostEqual(loss_sale['permanently_disallowed'], 2000.0, places=2)
        # Allowed gain on the loss sell is 0 (disallowed disappears entirely).
        self.assertAlmostEqual(loss_sale['gain'], 0.0, places=2)
        assert_rule(self, result, 's1_loss', R.US_WASH_01, record='s1_loss')
        assert_rule(self, result, 's1_loss', R.US_WASH_11, record='ira_buy')


if __name__ == '__main__':
    unittest.main()
