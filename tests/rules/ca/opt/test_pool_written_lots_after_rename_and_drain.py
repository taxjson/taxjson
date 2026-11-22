"""The written contracts of an option series as the pool keeps them, lot
by lot in the order they were written (CA-OPT-06: the contract assigned
is the account's earliest; a close takes the average premium of its own
account's open contracts), across a rename of the series and after the
position is closed out.

PLAN step 13, commit 0: pins for the pool's operations no test held (the
2026-11-17 mutation pass). Synthetic data only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import (
    contract, holding, own_move, rename, run_canada, sale, trade)

OLD = 'SYQ251219P00010000.TO'
NEW = 'SYR251219P00010000.TO'


class TestARenamedSeriesKeepsItsWritesInOrder(unittest.TestCase):
    """A put written for 250 in December 2024 under the old ticker, one
    written for 300 in January 2025 under the new one, the series renamed
    in February: two contracts of one series, the 2024 one the earlier."""

    BOOK = [contract('2024-12-02', -1, 250, OLD),
            contract('2025-01-07', -1, 300, NEW),
            rename('2025-02-03', OLD, NEW)]

    @rule(R.CA_OPT_06, R.CA_OPT_01, R.CA_CORP_02)
    def test_an_assignment_takes_the_earliest_write(self):
        res, _ = run_canada(
            self.BOOK + [contract('2025-03-03', 1, 0, NEW, action='ASSIGN'),
                         trade('2025-03-03', 100, 1000, sym='SYR.TO')],
            option_premium_timing='grant')
        # The 2024 premium folds into the shares (s.49(3.1)): 1000 - 250.
        shares = holding(res, 'SYR.TO')
        self.assertEqual((shares['qty'], round(shares['total_cost'], 2)), (100.0, 750.0))
        # The 2025 contract stays written, its premium recognised.
        left = holding(res, NEW)
        self.assertEqual((left['qty'], left.get('recognised_premium')), (-1.0, 300.0))

    @rule(R.CA_OPT_01, R.CA_OPT_03, R.CA_CORP_02)
    def test_a_buy_back_closes_the_earliest_write(self):
        res, _ = run_canada(self.BOOK + [contract('2025-03-03', 1, 100, NEW)],
                            option_premium_timing='grant')
        closed = sale(res, '2025-03-03', NEW)['grant_closed']
        self.assertEqual({y: (v['units'], round(v['premium'], 2)) for y, v in closed.items()},
                         {'2024': (1.0, 250.0)})


class TestASplitScalesAWrittenLotWhereverItIsKept(unittest.TestCase):
    """A sale past the contracts the books show held writes the rest
    (CA-OPT-12). When the account's own count and the pooled count differ
    (contracts moved between the person's accounts beyond what the files
    show), the written part is booked on the series' pool of bought
    contracts, not on its pool of written ones, and its lot is kept there.
    A split (CA-CORP-01: the quantity scales, the cost does not) halves
    that lot's premium per contract like any other. Under grant timing
    (CA-OPT-01) the premium was a gain when written, so a buy-back is a
    loss of what it cost (CA-OPT-03) and the contracts left carry their
    own share of the recognised premium."""

    CALL = 'SYQ251219C00010000.TO'
    GRANT = {'option_premium_timing': 'grant'}

    @rule(R.CA_CORP_01, R.CA_OPT_01, R.CA_OPT_03)
    def test_one_accounts_own_view(self):
        # 1 bought; 2 moved out (one more than the files show) and 2
        # moved back in; 3 sold for 900: the first closes what is held,
        # the other two are written for 300 each (600 recognised). A 2:1
        # split makes them 4 at 150; 2 are bought back for 100.
        C = self.CALL
        res, _ = run_canada([
            contract('2025-01-06', 1, 300, C),
            own_move('2025-01-10', -2, 'b', 'c', sym=C),
            own_move('2025-01-12', 2, 'c', 'b', sym=C, n=2),
            contract('2025-01-15', -3, 900, C),
            rename('2025-02-03', C, C, 2.0),
            contract('2025-03-03', 2, 100, C)], **self.GRANT)
        back = sale(res, '2025-03-03', C)
        self.assertEqual(round(back['gain'], 2), -100.0)
        self.assertEqual({y: (v['units'], round(v['premium'], 2))
                          for y, v in back['grant_closed'].items()},
                         {'2025': (2.0, 300.0)})
        # Two contracts left: half the 600, not all of it.
        left = holding(res, C)
        self.assertEqual((left['qty'], round(left['total_cost'], 2),
                          left['recognised_premium']), (-2.0, -300.0, 300.0))

    @rule(R.CA_CORP_01, R.CA_OPT_01, R.CA_OPT_03)
    def test_two_accounts_in_one_book(self):
        # b and c each buy 1 for 300; b moves 2 to c (it holds 1); c
        # sells 3 for 900: the two pooled contracts go for 600 against
        # 600, the third is written for 300. A 2:1 split makes it 2 at
        # 150; c buys both back for 100: a loss of 100, nothing left.
        C = self.CALL
        res, _ = run_canada([
            contract('2025-01-06', 1, 300, C, account='b'),
            contract('2025-01-07', 1, 300, C, account='c'),
            own_move('2025-01-10', -2, 'b', 'c', sym=C, account='b'),
            own_move('2025-01-10', 2, 'b', 'c', sym=C, account='c'),
            contract('2025-01-15', -3, 900, C, account='c'),
            rename('2025-02-03', C, C, 2.0, account='c'),
            contract('2025-03-03', 2, 100, C, account='c')], **self.GRANT)
        back = sale(res, '2025-03-03', C)
        self.assertEqual((back['qty'], round(back['gain'], 2)), (2.0, -100.0))
        self.assertEqual({y: (v['units'], round(v['premium'], 2))
                          for y, v in back['grant_closed'].items()},
                         {'2025': (2.0, 300.0)})
        self.assertEqual([i for i in res['inventory'] if abs(i['qty']) > 1e-9], [])
        self.assertAlmostEqual(res['summary']['total_gain'], 200.0, 2)


class TestAClosedOutPositionLeavesNoLot(unittest.TestCase):

    @rule(R.CA_OPT_06, R.CA_OPT_10)
    def test_the_next_write_closes_at_its_own_accounts_premium(self):
        # A contract written for 10000 is bought back but for a rounding
        # residue of the quantity (under the share tolerance: the position
        # is closed). The account then writes one for 50 and another
        # account one for 70; its buy-back for 10 closes its own contract:
        # 50 - 10, to the cent, nothing of the contract closed before.
        res, _ = run_canada([contract('2025-01-06', -1, 10000, OLD),
                             contract('2025-02-03', 0.9999998, 10, OLD),
                             contract('2025-03-03', -1, 50, OLD),
                             contract('2025-03-04', -1, 70, OLD, account='c'),
                             contract('2025-04-01', 1, 10, OLD)])
        self.assertAlmostEqual(sale(res, '2025-04-01', OLD)['gain'], 40.0, delta=0.01)
        left = holding(res, OLD)
        self.assertEqual(left['qty'], -1.0)
        self.assertAlmostEqual(left['total_cost'], -70.0, delta=0.01)


class TestWrittenContractsMovedOutLeaveNothingBehind(unittest.TestCase):
    """A written contract moved to another of the person's accounts is that
    account's obligation from then on (CA-OPT-12): this account's own view
    holds none of it, and what it writes next is a new position."""

    @rule(R.CA_OPT_12, R.CA_ACB_OWN_MOVE)
    def test_the_next_write_begins_a_position(self):
        res, _ = run_canada([contract('2025-01-06', -1, 250, OLD),
                             own_move('2025-02-03', -1, 'b', 'c', sym=OLD),
                             contract('2025-03-03', -1, 300, OLD)])
        left = holding(res, OLD)
        self.assertEqual((left['qty'], round(left['total_cost'], 2),
                          left['position_start_date']),
                         (-1.0, -300.0, '2025-03-03'))

    @rule(R.CA_OPT_12, R.CA_ACB_OWN_MOVE, R.CA_OPT_01)
    def test_the_contracts_left_keep_their_own_premium_and_no_more(self):
        # Two written for 500 under grant timing (500 recognised), one
        # moved out: the account is short one contract whose premium, and
        # recognised premium, is 250.
        res, _ = run_canada([contract('2025-01-06', -2, 500, OLD),
                             own_move('2025-02-03', -1, 'b', 'c', sym=OLD)],
                            option_premium_timing='grant')
        left = holding(res, OLD)
        self.assertEqual((left['qty'], round(left['total_cost'], 2),
                          left['recognised_premium']), (-1.0, -250.0, 250.0))

    @rule(R.CA_OPT_12, R.CA_ACB_OWN_MOVE, R.CA_OPT_10)
    def test_a_contract_that_arrived_and_left_does_not_mark_the_next(self):
        # A written contract arrives from account c (its premium unknown
        # in this book) and leaves again; the account then writes one for
        # 300 and buys it back for 100: 200, at a premium the book knows.
        res, _ = run_canada([own_move('2025-01-06', -1, 'c', 'b', sym=OLD),
                             own_move('2025-02-03', -1, 'b', 'c', sym=OLD, n=2),
                             contract('2025-03-03', -1, 300, OLD),
                             contract('2025-04-03', 1, 100, OLD)])
        back = sale(res, '2025-04-03', OLD)
        self.assertEqual((round(back['gain'], 2), back['tainted']), (200.0, False))
        self.assertNotIn('own_move_from', back)


if __name__ == '__main__':
    unittest.main()
