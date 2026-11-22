"""A cover's disallowed loss comes off the replacement short sale's
proceeds (US-WASH-27, the owner's ruling on TJ-4153, question 5)."""

import unittest

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_rules import assert_rule, rules_of


class TestACoversDisallowedLossComesOffTheReplacementShort(unittest.TestCase):
    """US-WASH-27, the owner's ruling on TJ-4153 (question 5): §1091(e)
    disallows a cover's loss and states no adjustment; the books apply
    §1091(d) by analogy, so the loss comes off the replacement short
    sale's proceeds and returns when that short is covered."""

    @rule(R.US_WASH_05, R.US_WASH_27)
    def test_the_disallowed_loss_returns_on_the_replacement_shorts_cover(self):
        # 100 shorted for 1000 and covered 03-10 for 1300: a loss of 300.
        # 100 shorted again 03-20 for 1250: the 300 is disallowed and that
        # short's proceeds become 950. Covered on 06-02 for 1100, it shows
        # a loss of 150 where its own trades made 150: the 300 came back.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-03-03', symbol='X', quantity=-100.0,
                           net_amount=1000.0, currency='USD', id='short1'),
            TaxTransaction(action='BUYSELL', date='2025-03-10', symbol='X', quantity=100.0,
                           net_amount=1300.0, currency='USD', id='cover1'),
            TaxTransaction(action='BUYSELL', date='2025-03-20', symbol='X', quantity=-100.0,
                           net_amount=1250.0, currency='USD', id='short2'),
            TaxTransaction(action='BUYSELL', date='2025-06-02', symbol='X', quantity=100.0,
                           net_amount=1100.0, currency='USD', id='cover2'),
        ]
        result = USATaxRules().compute_gains(txs)
        covers = {g['id']: g for g in result['transactions'] if g.get('direction') == 'SHORT'}
        first, second = covers['cover1'], covers['cover2']
        self.assertAlmostEqual(first['disallowed_amount'], 300.0, places=2)
        self.assertAlmostEqual(first['gain'], 0.0, places=2)
        # (a cover's record: the cover's payment as negative proceeds, the
        # short's proceeds as a negative cost)
        self.assertAlmostEqual(second['cost'], -950.0, places=2)
        self.assertAlmostEqual(second['proceeds'], -1100.0, places=2)
        self.assertAlmostEqual(second['gain'], -150.0, places=2)
        self.assertAlmostEqual(first['gain'] + second['gain'], -150.0, places=2)
        # The cover's disallowance names US-WASH-05; the replacement
        # short's proceeds cut, US-WASH-27.
        assert_rule(self, result, 'cover1', R.US_WASH_05, record='cover1')
        assert_rule(self, result, 'cover1', R.US_WASH_27, record='short2')
        self.assertEqual(rules_of(result, 'cover2'), {})


if __name__ == '__main__':
    unittest.main()
