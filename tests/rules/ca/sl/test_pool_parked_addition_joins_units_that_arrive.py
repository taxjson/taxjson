"""A denied loss's addition waiting on a flat pool (CA-SL-19) joins the
long position the pool next opens, whichever row opens it: units that
arrive with no purchase (a listing journal's, a move from another of the
person's accounts) as a purchase's do (backlog TJ-4256: they opened the
position without it, the sale's record said the loss was recovered, and
its cost lacked it). Arriving into a position already held, they find
nothing waiting and change nothing. Units that leave the pool short take
none of it: it waits on for the first row that leaves the pool long
(the owner's ruling on backlog TJ-4258, 2026-11-18).

Synthetic data only.
"""
import contextlib
import io
import unittest
from decimal import Decimal

from tax_rules import rule
from taxjson.engine.ca.pools import _warn_waiting_additions
from taxjson.lib.row_types import OPENING_TYPE
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import (
    flat_pool, journal, journal_short_into_parked, move_beyond_with_parked, own_move, run_canada,
    sale, trade)

SYM, US = 'OLD.TO', 'OLD.US'

# 200 bought for 4000 and sold for 2000; 50 bought back two days later and
# sold again within the week: no purchase keeps the loss's replacement, and
# the 100 held at day 30 arrive on 02-20 by no purchase. Half the loss
# (100 held of the 200 sold: 1000) is denied and waits on the flat pool.
PARKED = [trade('2025-01-02', 100, 2000, sym=SYM),
          trade('2025-02-01', 100, 2000, sym=SYM),
          trade('2025-02-05', -200, 2000, sym=SYM),
          trade('2025-02-07', 50, 500, sym=SYM),
          trade('2025-02-10', -50, 500, sym=SYM)]
JUNE = [trade('2025-06-02', -100, 1200, sym=SYM)]


def _figures(res, date='2025-06-02'):
    rec = sale(res, date, SYM)
    return (round(rec['cost'], 2), round(rec['gain'], 2), bool(rec.get('tainted')))


class TestUnitsThatArriveOnTheFlatPoolTakeWhatWaits(unittest.TestCase):

    @rule(R.CA_SL_19, R.CA_XLIST_01)
    def test_a_journal_from_another_listing(self):
        # 100 OLD.US bought for 700 are journaled to OLD.TO: they cost
        # 700 + the 1000 denied, and their sale for 1200 is a loss of 500.
        # The year's gains are the cash: 6400 paid, 4900 received.
        res, err = run_canada(PARKED + [trade('2025-01-05', 100, 700, sym=US)]
                              + journal('2025-02-20', 100, US, SYM) + JUNE)
        self.assertEqual(_figures(res), (1700.0, -500.0, False), err)
        self.assertEqual(res['summary']['total_gain'], -1500.0)
        self.assertEqual(res['summary']['total_disallowed'], 1000.0)

    @rule(R.CA_SL_19, R.CA_ACB_OWN_MOVE)
    def test_a_move_in_from_another_of_the_persons_accounts(self):
        # The 100 moved in cost nothing this book knows (unknown cost): the
        # sale's cost is the 1000 added, as an opening balance's is.
        res, err = run_canada(PARKED + [own_move('2025-02-20', 100, 'x', 'b', sym=SYM)]
                              + JUNE)
        self.assertEqual(_figures(res), (1000.0, 200.0, True), err)

    @rule(R.CA_SL_19, R.CA_XLIST_01)
    def test_a_journal_of_more_than_the_other_listing_holds(self):
        # 40 of the 100 journaled were bought (280); the other 60 have no
        # purchase in this book. The position they open takes the 1000 once.
        res, err = run_canada(PARKED + [trade('2025-01-05', 40, 280, sym=US)]
                              + journal('2025-02-20', 100, US, SYM) + JUNE)
        self.assertEqual(_figures(res), (1280.0, -80.0, True), err)


    @rule(R.CA_SL_19, R.CA_XLIST_01)
    def test_a_journal_of_units_the_other_listing_never_bought(self):
        # None of the 100 has a purchase in this book: unknown cost, and
        # the 1000 that waited.
        res, err = run_canada(PARKED + journal('2025-02-20', 100, US, SYM) + JUNE)
        self.assertEqual(_figures(res), (1000.0, 200.0, True), err)


class TestAShortThatArrivesTakesNoneOfIt(unittest.TestCase):
    """What waits belongs to the long position held at day 30: a short is
    an obligation, not property held, and has no ACB to raise (CA-SL-07),
    so units that leave the pool short take none of it and it stays
    parked (as `test_pool_parked_addition_lands_once` pins for a short
    sale; before the owner's ruling on TJ-4258 a short's entry proceeds
    fell by it). The pool's own operations are called."""

    @rule(R.CA_SL_19, R.CA_SL_07)
    def test_units_out_past_the_holding(self):
        self.assertEqual(move_beyond_with_parked(500, -100), (Decimal(0), Decimal(500)))
        self.assertEqual(move_beyond_with_parked(500, 100), (Decimal(500), None))

    @rule(R.CA_SL_19, R.CA_XLIST_01)
    def test_a_short_journaled_from_another_listing(self):
        # A short of 100 opened for 700: entry proceeds of 700 here, the
        # 500 still parked.
        self.assertEqual(journal_short_into_parked(500, 100, 700),
                         (-100.0, Decimal(700), Decimal(500)))

    @rule(R.CA_SL_19)
    def test_the_row_that_leaves_the_short_pool_long_takes_it(self):
        # Short 100 by units out, then 120 in: the 20 held take the 500;
        # 80 in leave the pool short 20 and take none.
        pool = flat_pool(500)
        pool.move_beyond(-100.0, '2025-03-03', restart=True)
        pool.move_in(120.0, '2025-03-04')
        self.assertEqual((pool.qty, pool.total_cost, pool.pending_wash), (20.0, Decimal(500), 0))
        pool = flat_pool(500)
        pool.move_beyond(-100.0, '2025-03-03', restart=True)
        pool.move_in(80.0, '2025-03-04')
        self.assertEqual((pool.qty, pool.total_cost, pool.pending_wash),
                         (-20.0, Decimal(0), Decimal(500)))
        # What waits is tallied, in no cost: a cover releases none of it.
        self.assertEqual((pool.deferred_wash, pool.denied_in_cost), (Decimal(500), Decimal(0)))


class TestAJournalOutOfTheFlatPoolLeavesWhatWaitsParked(unittest.TestCase):
    """Backlog TJ-4258's book: 1000 waits on the flat OLD.TO pool (the book
    above); a journal of 30 out to OLD.US with none held (02-12) leaves the
    pool short 30 at an unknown cost; an opening position of 130 for 500
    (02-20) covers the 30 and leaves 100, held at day 30 and sold on
    06-02 for 1200. The cover loses its own 115.38 and no more; the 1000
    joins the 100 units the row leaves long and comes back on their sale.
    Before the ruling the short took the 1000 and the cover lost 1115.38,
    fifteen days after the denial, in an unknown-cost record."""

    @staticmethod
    def _book(opening):
        return PARKED + journal('2025-02-12', 30, SYM, US) + [opening] + JUNE

    @rule(R.CA_SL_19, R.CA_SL_07, R.CA_OPEN_01)
    def test_the_cover_loses_its_own_and_the_long_position_takes_the_wait(self):
        res, err = run_canada(self._book(
            trade('2025-02-20', 130, 500, sym=SYM, type=OPENING_TYPE)))
        cover = sale(res, '2025-02-20', SYM)
        self.assertEqual((cover['qty'], round(cover['gain'], 2)), (30.0, -115.38), err)
        self.assertNotIn('deferred_loss_recovered', cover)
        self.assertEqual(_figures(res), (1384.62, -184.62, True))
        self.assertAlmostEqual(sale(res, '2025-06-02', SYM)['deferred_loss_recovered'], 1000.0, 2)
        self.assertEqual(res['summary']['total_gain'], -1300.0)
        self.assertEqual(res['summary']['total_disallowed'], 1000.0)
        self.assertNotIn('still waits', err)

    @rule(R.CA_SL_19, R.CA_ACB_11)
    def test_an_opening_at_an_unknown_cost_takes_it_too(self):
        # The 130 at an unknown cost (an OPENING cost=unknown line) bring
        # the pool from short 30 to long 100: the 100 cost the 1000 that
        # waited, and their sale for 1200 is a gain of 200 (before: a
        # cost of -1000, the short's, and a gain of 2200).
        res, err = run_canada(self._book(
            trade('2025-02-20', 130, 0, sym=SYM, action='OPENING_BALANCE')))
        self.assertEqual(_figures(res), (1000.0, 200.0, True), err)


class TestASpinOffTakesNoneOfWhatWaits(unittest.TestCase):
    """A spin-off's parent gives up a share of its ACB and the same share
    of the denied losses that ACB holds (TJ-0062, `give_up`); an addition
    parked on the pool is in no ACB, so none of it goes to the new shares:
    it waits on for the parent's long position (TJ-4258; backlog TJ-4414).
    """

    @rule(R.CA_SL_19, R.CA_CORP_06)
    def test_a_short_parent_holding_a_parked_amount(self):
        parent = flat_pool(500)
        parent.move_beyond(-100.0, '2025-02-12', restart=True)
        parent.tally_denied(Decimal(300))       # in the short's proceeds
        moved, cut = parent.give_up(Decimal('0.2'))
        self.assertEqual((moved, cut), (Decimal(60), Decimal(0)))
        self.assertEqual((parent.deferred_wash, parent.denied_in_cost,
                          parent.pending_wash),
                         (Decimal(740), Decimal(240), Decimal(500)))


class TestAnAmountStillWaitingWhenTheBooksEndIsSaid(unittest.TestCase):
    """No book's day-30 position leaves an amount parked at the end (the
    denial presumes a long position held then, and whatever row opens it
    takes the amount); a pool left short or flat with it is said, so a
    denied loss is never lost in silence (the owner's ruling on TJ-4258)."""

    @staticmethod
    def _said(pools):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _warn_waiting_additions(pools)
        return err.getvalue()

    @rule(R.CA_SL_19)
    def test_a_pool_left_short_with_it(self):
        short = flat_pool(1000)
        short.move_beyond(-30.0, '2025-02-12', restart=True)
        flat = flat_pool('0.004')          # under half a cent: not said
        self.assertEqual(
            self._said({SYM: short, US: flat, 'ALIAS.TO': short}),
            "warning: OLD.TO: a denied superficial loss of 1000.00 still waits "
            "for a long position at the end of the books: no ACB holds it\n")

    @rule(R.CA_SL_19)
    def test_nothing_is_said_once_a_long_position_took_it(self):
        pool = flat_pool(1000)
        pool.move_in(100.0, '2025-02-20')
        self.assertEqual(self._said({SYM: pool}), '')


class TestUnitsThatArriveInAPositionHeldChangeNothingElse(unittest.TestCase):
    """Nothing waits on a pool that holds a position: the arriving units
    bring their own cost, or none, and no more (the figures before
    TJ-4256's fix)."""

    HELD = [trade('2025-01-02', 100, 2000, sym=SYM)]
    SOLD = [trade('2025-06-02', -200, 2400, sym=SYM)]

    @rule(R.CA_XLIST_01, R.CA_ACB_01)
    def test_a_journal_into_a_held_position(self):
        res, err = run_canada(self.HELD + [trade('2025-01-05', 100, 700, sym=US)]
                              + journal('2025-02-20', 100, US, SYM) + self.SOLD)
        self.assertEqual(_figures(res), (2700.0, -300.0, False), err)

    @rule(R.CA_ACB_OWN_MOVE)
    def test_a_move_into_a_held_position(self):
        res, err = run_canada(self.HELD + [own_move('2025-02-20', 100, 'x', 'b', sym=SYM)]
                              + self.SOLD)
        self.assertEqual(_figures(res), (2000.0, 400.0, True), err)

    @rule(R.CA_SL_19, R.CA_XLIST_01)
    def test_a_journal_after_a_purchase_took_what_waited(self):
        # A purchase of 10 for 100 opens the position on 02-15 (110 are
        # then held at day 30: 1100 is denied) and takes what waits; the
        # journal five days later adds its 700 and no more: 100 + 1100 + 700.
        res, err = run_canada(PARKED + [trade('2025-02-15', 10, 100, sym=SYM),
                                        trade('2025-01-05', 100, 700, sym=US)]
                              + journal('2025-02-20', 100, US, SYM)
                              + [trade('2025-06-02', -110, 1320, sym=SYM)])
        self.assertEqual(_figures(res), (1900.0, -580.0, False), err)


if __name__ == '__main__':
    unittest.main()
