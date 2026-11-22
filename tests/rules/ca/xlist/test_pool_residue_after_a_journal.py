"""What a listing journal leaves on the listing the units left (CA-XLIST-01:
the two listings are one ACB pool, the journaled units take their share of
it): the rest is a holding at its share of the cost when it still costs
half a cent or more, however few the units; only a rest under a millionth
of a share AND under half a cent is rounding residue (CA-ACB-01, as after
a sale: backlog TJ-0649, TJ-1062).

Backlog TJ-4244: a journal that left exactly a millionth of a share, or
less, cleared the units and stranded their cost ("pool is EMPTY but
carries ... of stranded basis"); the sale of the rest then opened a short
and booked no gain. Synthetic data only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import holding, journal, run_canada, sale, trade

TO, US = 'SYQ.TO', 'SYQ.US'
ONE = 0.000001          # the share tolerance: a millionth of a share


def _journaled(bought, cost=1000):
    """`bought` shares for `cost`, a millionth of a share journaled to the
    other listing, then the rest and the journaled share sold for 700 each."""
    rest = bought - ONE
    res, err = run_canada([trade('2025-01-06', bought, cost, sym=TO)]
                          + journal('2025-04-01', ONE, TO, US)
                          + [trade('2025-06-02', -rest, 700, sym=TO),
                             trade('2025-06-03', -ONE, 700, sym=US)])
    return res, err


def _cost_gain(res, date, sym):
    rec = sale(res, date, sym)
    return rec['direction'], round(rec['cost'], 2), round(rec['gain'], 2)


class TestTheRestAJournalLeavesIsAHolding(unittest.TestCase):

    def _check(self, bought, rest_cost, moved_cost):
        res, err = _journaled(bought)
        self.assertNotIn('stranded', err)
        self.assertEqual(_cost_gain(res, '2025-06-02', TO),
                         ('LONG', rest_cost, round(700 - rest_cost, 2)))
        self.assertEqual(_cost_gain(res, '2025-06-03', US),
                         ('LONG', moved_cost, round(700 - moved_cost, 2)))
        self.assertEqual([i for i in res['inventory'] if abs(i['qty']) > 1e-12], [])
        self.assertAlmostEqual(res['summary']['total_gain'], 400.0, 2)

    @rule(R.CA_XLIST_01, R.CA_ACB_01)
    def test_a_rest_of_exactly_the_tolerance(self):
        # Two millionths for 1000, one journaled: each listing holds one
        # at 500.
        self._check(0.000002, 500.0, 500.0)

    @rule(R.CA_XLIST_01, R.CA_ACB_01)
    def test_a_rest_under_the_tolerance_that_still_costs(self):
        # One and a half millionths for 1000: two thirds of the cost go
        # with the millionth journaled, the half millionth left costs
        # 333.33.
        self._check(0.0000015, 333.33, 666.67)

    @rule(R.CA_XLIST_01, R.CA_ACB_01)
    def test_a_rest_over_the_tolerance(self):
        self._check(0.000003, 666.67, 333.33)


class TestARestThatCostsNothingIsResidue(unittest.TestCase):

    @rule(R.CA_XLIST_01, R.CA_ACB_01)
    def test_the_listing_starts_again_at_its_next_purchase(self):
        # One and a half millionths for under a cent: the half millionth
        # left costs a fifth of a cent. It is rounding residue: the
        # listing holds nothing, and the 10 bought in June are a new
        # position at their own cost.
        res, err = run_canada([trade('2025-01-06', 0.0000015, 0.006, sym=TO)]
                              + journal('2025-04-01', ONE, TO, US)
                              + [trade('2025-06-02', 10, 100, sym=TO)])
        self.assertNotIn('stranded', err)
        held = holding(res, TO)
        self.assertEqual((held['qty'], round(held['total_cost'], 2),
                          held['position_start_date']),
                         (10.0, 100.0, '2025-06-02'))


class TestARestOfExactlyTheToleranceIsAPosition(unittest.TestCase):

    @rule(R.CA_XLIST_01, R.CA_ACB_01)
    def test_also_when_it_costs_under_half_a_cent(self):
        # Two millionths for under a cent, one journaled: the millionth
        # left costs three tenths of a cent. Less than a millionth of a
        # share is zero; a millionth is not: the listing still holds it,
        # and the 10 bought in June join that position.
        res, _ = run_canada([trade('2025-01-06', 0.000002, 0.006, sym=TO)]
                            + journal('2025-04-01', ONE, TO, US)
                            + [trade('2025-06-02', 10, 100, sym=TO)])
        held = holding(res, TO)
        self.assertEqual((held['qty'], round(held['total_cost'], 3),
                          held['position_start_date']),
                         (10.000001, 100.003, '2025-01-06'))


if __name__ == '__main__':
    unittest.main()
