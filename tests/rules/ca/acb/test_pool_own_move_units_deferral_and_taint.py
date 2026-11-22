"""One account's own view of a move between two of the person's taxable
accounts (CA-ACB-OWN-MOVE), as the ACB pool books it: the units leave at
the pool's average cost with no sale, taking their share of everything
the ACB holds (a denied loss's s.53(1)(f) addition, and what a return of
capital took of it); a pool the move empties starts again from nothing;
units that arrive have no cost in this book, and a sale of them names the
account they came from.

PLAN step 13, commit 0: pins for the pool's operations no test held (the
2026-11-17 mutation pass). Synthetic data only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import (
    denied_loss, holding, own_move, return_of_capital, run_canada, sale, trade)


class TestUnitsMovedOutTakeTheirShareOfTheAcb(unittest.TestCase):
    """100 units held at 1000, 500 of it a denied loss; 40 move out."""

    @rule(R.CA_ACB_OWN_MOVE, R.CA_SL_09)
    def test_the_denied_loss_leaves_in_the_units_share(self):
        res, _ = run_canada(denied_loss() + [own_move('2025-04-01', -40, 'b', 'c')])
        left = holding(res)
        # 40 of 100 units take 400 of the 1000 and 200 of the 500 denied.
        self.assertEqual((left['qty'], round(left['total_cost'], 2)), (60.0, 600.0))
        self.assertAlmostEqual(left['deferred_wash'], 300.0, 2)

    @rule(R.CA_ACB_OWN_MOVE, R.CA_RPT_16)
    def test_what_a_return_of_capital_took_of_it_leaves_in_that_share_too(self):
        # A return of capital of 800 leaves an ACB of 200: of the 500
        # denied, 200 are still in the ACB and 300 were taken with the
        # capital returned. 40 units move out; the sale of the other 60
        # reports 60% of each (180 taken, 120 recovered), not all 300.
        res, _ = run_canada(denied_loss() + [
            return_of_capital('2025-03-03', 800),
            own_move('2025-04-01', -40, 'b', 'c'),
            trade('2025-06-02', -60, 600)])
        rec = sale(res, '2025-06-02')
        self.assertEqual((round(rec['cost'], 2), round(rec['gain'], 2)), (120.0, 480.0))
        self.assertAlmostEqual(rec['deferred_loss_cut'], 180.0, 2)
        self.assertAlmostEqual(rec['deferred_loss_recovered'], 120.0, 2)


class TestAPoolAMoveEmptiesStartsAgain(unittest.TestCase):

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_11)
    def test_units_bought_after_the_moved_ones_left_have_a_known_cost(self):
        # 100 arrive from account c (no cost in this book) and all 100
        # leave again: nothing of unknown cost is held, so the 100 bought
        # afterwards for 1000 are sold at their own cost.
        res, _ = run_canada([own_move('2025-01-06', 100, 'c', 'b'),
                             own_move('2025-02-03', -100, 'b', 'c', n=2),
                             trade('2025-03-03', 100, 1000),
                             trade('2025-06-02', -40, 480)])
        rec = sale(res, '2025-06-02')
        self.assertEqual((round(rec['cost'], 2), round(rec['gain'], 2), rec['tainted']),
                         (400.0, 80.0, False))
        self.assertNotIn('own_move_from', rec)
        self.assertFalse(holding(res).get('unknown_cost'))

    @rule(R.CA_ACB_OWN_MOVE)
    def test_the_position_bought_afterwards_begins_at_its_purchase(self):
        res, _ = run_canada([trade('2025-01-06', 100, 1000),
                             own_move('2025-02-03', -100, 'b', 'c'),
                             trade('2025-06-02', 100, 1100)])
        held = holding(res)
        self.assertEqual((held['qty'], round(held['total_cost'], 2),
                          held['position_start_date']),
                         (100.0, 1100.0, '2025-06-02'))


class TestUnitsThatArriveOrLeaveWithNoPurchaseHere(unittest.TestCase):
    """Units moved in have no cost in this account's own view, and units
    moved out past what it holds have no purchase in it (missing history
    in the sender): either way the position they make begins on the day of
    the move and is of unknown cost (CA-ACB-OWN-MOVE)."""

    @rule(R.CA_ACB_OWN_MOVE)
    def test_units_moved_in_begin_a_position_of_unknown_cost(self):
        res, _ = run_canada([own_move('2025-02-03', 50, 'x', 'b')])
        held = holding(res)
        self.assertEqual((held['qty'], round(held['total_cost'], 2),
                          held['position_start_date'], held.get('unknown_cost')),
                         (50.0, 0.0, '2025-02-03', True))

    @rule(R.CA_ACB_OWN_MOVE, R.CA_OPT_12)
    def test_written_contracts_moved_in_begin_a_short_of_unknown_premium(self):
        put = 'SYQ251219P00010000.TO'
        res, _ = run_canada([own_move('2025-02-03', -1, 'c', 'b', sym=put)])
        held = holding(res, put)
        self.assertEqual((held['qty'], held['position_start_date'],
                          held.get('unknown_cost')),
                         (-1.0, '2025-02-03', True))

    @rule(R.CA_ACB_OWN_MOVE)
    def test_units_moved_out_with_none_held_leave_a_short_of_unknown_cost(self):
        res, _ = run_canada([own_move('2025-02-03', -30, 'b', 'c')])
        short = holding(res)
        self.assertEqual((short['qty'], short['position_start_date'],
                          short.get('unknown_cost')),
                         (-30.0, '2025-02-03', True))
        # A purchase that then closes it is not a loss of what it paid:
        # it is of unknown cost, set apart.
        res, _ = run_canada([own_move('2025-02-03', -30, 'b', 'c'),
                             trade('2025-03-03', 30, 300)])
        self.assertTrue(sale(res, '2025-03-03')['tainted'])


class TestASaleOfMovedInUnitsNamesTheirAccount(unittest.TestCase):

    @rule(R.CA_ACB_OWN_MOVE)
    def test_units_sold_out_no_longer_name_the_account_they_came_from(self):
        # 50 from account x are sold; then 50 arrive from account y: the
        # sale of those names y alone.
        res, _ = run_canada([own_move('2025-01-06', 50, 'x', 'b'),
                             trade('2025-02-03', -50, 500),
                             own_move('2025-03-03', 50, 'y', 'b', n=2),
                             trade('2025-06-02', -50, 600)])
        self.assertEqual(sale(res, '2025-02-03')['own_move_from'], ['x'])
        self.assertEqual(sale(res, '2025-06-02')['own_move_from'], ['y'])

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_11)
    def test_units_from_before_the_data_are_not_put_down_to_the_move(self):
        # 50 held from before the data (their cost unknown, CA-ACB-11)
        # and 50 moved in from account x: the sale's unknown cost is not
        # the move's alone, so it names no account.
        res, _ = run_canada([trade('2025-01-06', 50, 0, action='OPENING_BALANCE'),
                             own_move('2025-02-03', 50, 'x', 'b'),
                             trade('2025-06-02', -100, 1200)])
        rec = sale(res, '2025-06-02')
        self.assertTrue(rec['tainted'])
        self.assertNotIn('own_move_from', rec)

    @rule(R.CA_ACB_OWN_MOVE, R.CA_ACB_11)
    def test_units_moved_out_take_an_unknown_openings_cause_with_them(self):
        # 30 held from before the data (cost unknown) all move out; 50
        # then arrive from account x. The sale of those 50 is put down to
        # the move from x alone, and names no opening line: the opening's
        # units are gone.
        res, _ = run_canada([trade('2025-01-02', 30, 0, action='OPENING_BALANCE'),
                             own_move('2025-01-10', -30, 'b', 'c'),
                             own_move('2025-02-03', 50, 'x', 'b', n=2),
                             trade('2025-06-02', -50, 600)])
        rec = sale(res, '2025-06-02')
        self.assertTrue(rec['tainted'])
        self.assertEqual(rec.get('own_move_from'), ['x'])
        self.assertNotIn('unknown_cost_from', rec)


if __name__ == '__main__':
    unittest.main()
