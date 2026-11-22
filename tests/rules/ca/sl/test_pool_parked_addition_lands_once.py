"""A denied loss's addition that no purchase can take (CA-SL-19): it
waits on the symbol's pool while it holds no long position and is added,
once, to the long position the pool next opens. Where the position held
at day 30 begins is where it lands, however the passes of the solver
reach it; and added to that long position it lowers its later gain by
the loss denied.

PLAN step 13, commit 0: pins for the pool's operations no test held (the
2026-11-17 mutation pass). Synthetic data only.
"""
import unittest
from decimal import Decimal

from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import (
    open_with_parked, rename_parked_into, run_canada, sale, trade, trade_with_parked)

SYM = 'OLD.TO'


class TestTheAdditionLandsWhereTheDay30PositionBegins(unittest.TestCase):
    """200 shares (100 bought in January, 100 four days before the sale)
    sold at a loss of 2000 and none bought back to keep: 50 bought two
    days later are sold again within the week, and the 100 held at day
    30 are another account's, from before the data: an opening at an
    unknown cost (CA-ACB-11; in account c, which has no earlier row for
    it to replace, CA-OPEN-03). Half the loss is denied (100 held of the
    200 sold) and belongs to those 100 alone. Their own cost is not in
    the files: their sale's cost is the 1000 added, and it is listed as
    of unknown cost."""

    BOOK = [trade('2025-01-02', 100, 2000, sym=SYM),
            trade('2025-02-01', 100, 2000, sym=SYM),
            trade('2025-02-05', -200, 2000, sym=SYM),
            trade('2025-02-07', 50, 500, sym=SYM),
            trade('2025-02-10', -50, 500, sym=SYM),
            trade('2025-02-20', 100, 0, sym=SYM, account='c', action='OPENING_BALANCE'),
            trade('2025-06-02', -100, 1200, sym=SYM, account='c')]

    @rule(R.CA_SL_19, R.CA_SL_09, R.CA_ACB_11)
    def test_the_purchase_sold_out_before_day_30_takes_none_of_it(self):
        res, err = run_canada(self.BOOK)
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        loss = sale(res, '2025-02-05', SYM)
        self.assertEqual((round(loss['gain'], 2), round(loss['disallowed_amount'], 2)),
                         (-1000.0, 1000.0))
        # The 50 bought and sold in between: their own 500, no denial.
        between = sale(res, '2025-02-10', SYM)
        self.assertEqual((round(between['cost'], 2), round(between['gain'], 2),
                          round(between.get('disallowed_amount') or 0.0, 2)),
                         (500.0, 0.0, 0.0))
        self.assertNotIn('deferred_loss_recovered', between)
        # The shares held at day 30 carry the 1000, back on their sale.
        june = sale(res, '2025-06-02', SYM)
        self.assertEqual((round(june['cost'], 2), june['tainted']), (1000.0, True))
        self.assertAlmostEqual(june['deferred_loss_recovered'], 1000.0, 2)
        self.assertAlmostEqual(res['summary']['total_disallowed'], 1000.0, 2)


class TestTheWaitingAdditionLowersTheNextLongPositionsGain(unittest.TestCase):
    """The pool's own step, called on a pool as the walk holds it: the
    denied loss comes back, once, on the disposition of the long position
    the pool next opens, whose cost rises by it. A short takes none of
    it: a short is an obligation, not property held at day 30, and has no
    ACB to raise (CA-SL-07); the amount stays parked for the first row
    that leaves the pool long (the owner's ruling on backlog TJ-4258,
    2026-11-18; before it, a short's entry proceeds fell by it). No book
    reaches the short cases through these three (the pass lands an
    addition on a long holding, CA-SL-07 and CA-SL-09; backlog TJ-4242)."""

    @rule(R.CA_SL_19)
    def test_a_position_opened_on_the_flat_pool_takes_it(self):
        self.assertEqual(open_with_parked(500, 100), (Decimal(500), None))
        self.assertEqual(open_with_parked(500, -100), (Decimal(0), Decimal(500)))

    @rule(R.CA_SL_19)
    def test_a_trade_that_opens_the_flat_pool_takes_it_with_its_own_cost(self):
        # A purchase of 100 for 1500: 2000. A short sale of 100 for 1500:
        # entry proceeds of 1500, the 500 still parked.
        self.assertEqual(trade_with_parked(500, 100, 1500), (Decimal(2000), None))
        self.assertEqual(trade_with_parked(500, -100, 1500), (Decimal(1500), Decimal(500)))

    @rule(R.CA_SL_19, R.CA_ACB_RENAME)
    def test_a_rename_into_a_held_position_adds_it_there(self):
        # A long of 100 at 1500: 2000. A short of 100 for 1500: 1500, the
        # 500 still parked.
        self.assertEqual(rename_parked_into(500, 100, 1500), (Decimal(2000), None))
        self.assertEqual(rename_parked_into(500, -100, 1500), (Decimal(1500), Decimal(500)))

    @rule(R.CA_SL_19, R.CA_ACB_RENAME)
    def test_a_rename_into_a_flat_pool_leaves_it_waiting_there(self):
        # No position at the new ticker either: nothing takes it yet.
        self.assertEqual(rename_parked_into(500, 0, 0), (Decimal(0), Decimal(500)))


if __name__ == '__main__':
    unittest.main()
