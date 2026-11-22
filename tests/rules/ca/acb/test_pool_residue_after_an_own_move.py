"""What a move to another of the person's taxable accounts leaves in this
account's own view (CA-ACB-OWN-MOVE: the units leave at the pool's average
cost with no sale): the rest is a holding at its share of the cost when it
still costs half a cent or more, however few the units; only a rest under
a millionth of a share AND under half a cent is rounding residue
(CA-ACB-01, as after a sale: backlog TJ-0649, TJ-1062).

Backlog TJ-4244: a move that left less than a millionth of a share cleared
the units and stranded their cost; the sale of the rest then opened a
short and booked no gain. Synthetic data only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import holding, own_move, run_canada, sale, trade

ONE = 0.000001          # the share tolerance: a millionth of a share


class TestTheRestAMoveLeavesIsAHolding(unittest.TestCase):

    def _check(self, bought, rest_cost):
        # `bought` shares for 1000, a millionth moved out, the rest sold
        # for 700: its share of the 1000, no short, nothing left.
        res, err = run_canada([trade('2025-01-06', bought, 1000),
                               own_move('2025-04-01', -ONE, 'b', 'c'),
                               trade('2025-06-02', -(bought - ONE), 700)])
        self.assertNotIn('stranded', err)
        rec = sale(res, '2025-06-02')
        self.assertEqual((rec['direction'], round(rec['cost'], 2), round(rec['gain'], 2)),
                         ('LONG', rest_cost, round(700 - rest_cost, 2)))
        self.assertEqual([i for i in res['inventory'] if abs(i['qty']) > 1e-12], [])

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_01)
    def test_a_rest_of_exactly_the_tolerance(self):
        self._check(0.000002, 500.0)

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_01)
    def test_a_rest_under_the_tolerance_that_still_costs(self):
        self._check(0.0000015, 333.33)

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_01)
    def test_a_rest_over_the_tolerance(self):
        self._check(0.000003, 666.67)


class TestARestThatCostsNothingIsResidue(unittest.TestCase):

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_01)
    def test_the_account_starts_again_at_its_next_purchase(self):
        res, err = run_canada([trade('2025-01-06', 0.0000015, 0.006),
                               own_move('2025-04-01', -ONE, 'b', 'c'),
                               trade('2025-06-02', 10, 100)])
        self.assertNotIn('stranded', err)
        held = holding(res)
        self.assertEqual((held['qty'], round(held['total_cost'], 2),
                          held['position_start_date']),
                         (10.0, 100.0, '2025-06-02'))


class TestARestOfExactlyTheToleranceIsAPosition(unittest.TestCase):

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_01)
    def test_also_when_it_costs_under_half_a_cent(self):
        # Less than a millionth of a share is zero; a millionth is not.
        res, _ = run_canada([trade('2025-01-06', 0.000002, 0.006),
                             own_move('2025-04-01', -ONE, 'b', 'c'),
                             trade('2025-06-02', 10, 100)])
        held = holding(res)
        self.assertEqual((held['qty'], round(held['total_cost'], 3),
                          held['position_start_date']),
                         (10.000001, 100.003, '2025-01-06'))

    @rule(R.CA_ACB_01)
    def test_the_same_after_a_sale(self):
        res, _ = run_canada([trade('2025-01-06', 0.000002, 0.006),
                             trade('2025-04-01', -ONE, 0.003),
                             trade('2025-06-02', 10, 100)])
        held = holding(res)
        self.assertEqual((held['qty'], round(held['total_cost'], 3),
                          held['position_start_date']),
                         (10.000001, 100.003, '2025-01-06'))


if __name__ == '__main__':
    unittest.main()
