"""A broker's journal between two listings of one security (CA-XLIST-01:
one ACB pool, identical property) as the pool books it in a book that
keeps the listings apart: the journaled units take their share of the
ACB to the other listing, the denied loss in it included; the listing
they all left starts again at its next purchase. The listing they join
shows the later of the two listings' purchases and opening lots, as one
listing holding both does (the owner's ruling on backlog TJ-4241).

PLAN step 13, commit 0: pins for the pool's operations no test held (the
2026-11-17 mutation pass). Synthetic data only.
"""
import datetime
import unittest

from tax_rules import rule
from taxjson.lib.row_types import OPENING_TYPE
from taxjson.rules import ids as R
from _support.engine_ca_pool_books import (
    denied_loss, holding, journal, own_move, return_of_capital, run_canada, sale, trade)

TO, US = 'SYQ.TO', 'SYQ.US'


class TestJournaledUnitsTakeTheirShareOfTheAcb(unittest.TestCase):

    @rule(R.CA_XLIST_01, R.CA_SL_09)
    def test_the_denied_loss_goes_with_the_units_and_is_counted_once(self):
        # 100 held at 1000, 500 of it a denied loss; 40 are journaled.
        res, _ = run_canada(denied_loss(TO) + journal('2025-04-01', 40, TO, US))
        stay, gone = holding(res, TO), holding(res, US)
        self.assertEqual((stay['qty'], round(stay['total_cost'], 2)), (60.0, 600.0))
        self.assertEqual((gone['qty'], round(gone['total_cost'], 2)), (40.0, 400.0))
        self.assertAlmostEqual(stay['deferred_wash'], 300.0, 2)
        self.assertAlmostEqual(gone['deferred_wash'], 200.0, 2)


class TestTheListingLeftStartsAgain(unittest.TestCase):

    @rule(R.CA_XLIST_01)
    def test_a_purchase_after_every_unit_was_journaled_begins_a_position(self):
        res, _ = run_canada([trade('2025-01-06', 100, 1000, sym=TO)]
                            + journal('2025-04-01', 100, TO, US)
                            + [trade('2025-06-02', 50, 600, sym=TO)])
        # The journaled shares keep the day they were bought; the 50
        # bought in June are a new position of the first listing.
        self.assertEqual(holding(res, US)['position_start_date'], '2025-01-06')
        self.assertEqual(holding(res, TO)['position_start_date'], '2025-06-02')

    @rule(R.CA_XLIST_01, R.CA_ACB_11, R.CA_ACB_OWN_MOVE)
    def test_an_unknown_openings_cause_goes_with_its_units(self):
        # 30 held from before the data (cost unknown) are all journaled
        # to the other listing; 50 then arrive on the first from account
        # x. The first listing's sale is put down to the move alone; the
        # other listing's sale names the opening line its units came by.
        res, _ = run_canada([trade('2025-01-02', 30, 0, sym=TO, action='OPENING_BALANCE')]
                            + journal('2025-01-10', 30, TO, US)
                            + [own_move('2025-02-03', 50, 'x', 'b', sym=TO),
                               trade('2025-06-02', -50, 600, sym=TO),
                               trade('2025-06-03', -30, 600, sym=US)])
        here = sale(res, '2025-06-02', TO)
        self.assertEqual((here['tainted'], here.get('own_move_from')), (True, ['x']))
        self.assertNotIn('unknown_cost_from', here)
        there = sale(res, '2025-06-03', US)
        self.assertTrue(there['tainted'])
        self.assertNotIn('own_move_from', there)
        self.assertEqual([(s['symbol'], s['date'], s['quantity'])
                          for s in there['unknown_cost_from']],
                         [(TO, '2025-01-02', 30.0)])


class TestAJournalBetweenListingsInTwoCurrencies(unittest.TestCase):
    """A book that keeps each listing in its own currency: the journaled
    units' cost is restated at the rate on the in-leg (CA-XLIST-03), and
    so is everything in it."""

    @rule(R.CA_XLIST_03, R.CA_XLIST_01, R.CA_RPT_16)
    def test_the_cost_and_the_denied_loss_in_it_are_restated_at_the_rate(self):
        # The US listing: 100 at 1000 USD, 500 of it a denied loss; a
        # return of capital of 800 leaves 200 (all denied loss) and takes
        # 300 of the denied loss. 40 are journaled to the Canadian listing
        # at 1.25: 80 USD of cost, 80 of denied loss and 120 taken, times
        # 1.25. The 60 left keep theirs in USD.
        res, _ = run_canada(
            denied_loss(US, currency='USD')
            + [return_of_capital('2025-03-03', 800, sym=US, currency='USD')]
            + journal('2025-04-01', 40, US, TO, currencies=('USD', 'CAD'), rate=1.25)
            + [trade('2025-06-02', -40, 800, sym=TO),
               trade('2025-06-03', -60, 600, sym=US, currency='USD')])
        there, here = sale(res, '2025-06-02', TO), sale(res, '2025-06-03', US)
        self.assertEqual((round(there['cost'], 2), round(there['deferred_loss_recovered'], 2),
                          round(there['deferred_loss_cut'], 2)), (100.0, 100.0, 150.0))
        self.assertEqual((round(here['cost'], 2), round(here['deferred_loss_recovered'], 2),
                          round(here['deferred_loss_cut'], 2)), (120.0, 120.0, 180.0))

    @rule(R.CA_XLIST_03, R.CA_ACB_11)
    def test_with_no_rate_on_file_the_cost_is_unknown_there(self):
        res, _ = run_canada(
            [trade('2025-01-06', 50, 500, sym=US, currency='USD')]
            + journal('2025-02-03', 50, US, TO, currencies=('USD', 'CAD'), rate=0.0))
        held = holding(res, TO)
        self.assertEqual((held['qty'], round(held['total_cost'], 2), held.get('unknown_cost')),
                         (50.0, 0.0, True))


class TestAJournalOfMoreThanTheListingHolds(unittest.TestCase):

    @rule(R.CA_XLIST_01, R.CA_ACB_11)
    def test_the_units_past_the_holding_are_of_unknown_cost_on_both_listings(self):
        # 50 bought for 500, 80 journaled: the 50 go with their cost; the
        # other 30 have no purchase in this book, so the first listing is
        # short by them from the journal's day and the other holds 80, 30
        # of them at no known cost.
        res, _ = run_canada([trade('2025-01-06', 50, 500, sym=TO)]
                            + journal('2025-02-03', 80, TO, US))
        left, got = holding(res, TO), holding(res, US)
        self.assertEqual((left['qty'], left['position_start_date'], left.get('unknown_cost')),
                         (-30.0, '2025-02-03', True))
        self.assertEqual((got['qty'], round(got['total_cost'], 2),
                          got['position_start_date'], got.get('unknown_cost')),
                         (80.0, 500.0, '2025-01-06', True))

    @rule(R.CA_XLIST_01, R.CA_ACB_11)
    def test_units_journaled_into_a_short_leave_a_position_of_unknown_cost(self):
        # 50 long on one listing, 20 short on the other: the journal nets
        # them on the second, which the engine does not cost: its sale is
        # set apart.
        res, _ = run_canada([trade('2025-01-06', 50, 500, sym=TO),
                             trade('2025-01-07', -20, 300, sym=US)]
                            + journal('2025-02-03', 50, TO, US)
                            + [trade('2025-04-01', -30, 400, sym=US)])
        self.assertTrue(sale(res, '2025-04-01', US)['tainted'])
        self.assertEqual([i for i in res['inventory'] if abs(i['qty']) > 1e-9], [])


class TestTheDatesAJournaledPoolShows(unittest.TestCase):
    """A journal into a listing already held: the pool shows the LATER of
    the two listings' last purchases and of their opening lots, as one
    listing holding both purchases does (CA-XLIST-01: one pool; CA-OPEN-01
    for the lots). The owner's ruling on backlog TJ-4241 (2026-11-18)
    ended this class's CHARACTERISATION of the earlier date. Only a
    listing never bought gives way to the units journaled in; one bought
    and sold out keeps its purchase when it is the later (backlog
    TJ-4413)."""

    def _days(self, first, second):
        res, _ = run_canada([first, second] + journal('2025-04-01', 100, TO, US)
                            + [trade('2025-06-02', -50, 700, sym=US)])
        return sale(res, '2025-06-02', US)['days_held']

    @staticmethod
    def _opening(sym, lot):
        return trade('2025-01-02', 100, 1000, sym=sym, type=OPENING_TYPE, lot_date=lot)

    @rule(R.CA_XLIST_01, R.CA_OPEN_01)
    def test_two_opening_lots_show_the_later_lot_date(self):
        since = (datetime.date(2025, 6, 2) - datetime.date(2022, 3, 3)).days
        self.assertEqual(self._days(self._opening(TO, '2020-05-05'),
                                    self._opening(US, '2022-03-03')), since)
        self.assertEqual(self._days(self._opening(TO, '2022-03-03'),
                                    self._opening(US, '2020-05-05')), since)

    @rule(R.CA_XLIST_01)
    def test_two_purchases_show_the_later_purchase(self):
        # Bought in January and in March, whichever listing is journaled
        # into the other: the days held count from March.
        since = (datetime.date(2025, 6, 2) - datetime.date(2025, 3, 3)).days
        def jan(sym):
            return trade('2025-01-06', 100, 1000, sym=sym)

        def mar(sym):
            return trade('2025-03-03', 100, 1200, sym=sym)

        self.assertEqual(self._days(jan(TO), mar(US)), since)
        self.assertEqual(self._days(mar(TO), jan(US)), since)

    @rule(R.CA_XLIST_01, R.CA_SL_01)
    def test_a_listing_that_held_none_shows_the_journaled_shares_own_dates(self):
        # Bought January 6, settled January 8, all journaled: the other
        # listing shows the trade date and, for the 30-day window, the
        # settlement date (CA-SL-01).
        buy = trade('2025-01-06', 50, 500, sym=TO)
        buy.date_settle = '2025-01-08'
        res, _ = run_canada([buy] + journal('2025-02-03', 50, TO, US))
        held = holding(res, US)
        self.assertEqual((held['position_start_date'], held['last_acq_date'],
                          held['last_acq_settle']),
                         ('2025-01-06', '2025-01-06', '2025-01-08'))

    @rule(R.CA_XLIST_01)
    def test_a_listing_sold_out_keeps_its_later_purchase(self):
        # The other listing was bought in March and sold before the
        # journal: its March purchase is the later of the two and still
        # shows, as one listing bought in January and March does (TJ-4413;
        # the January shares' date showed).
        res, _ = run_canada([trade('2025-01-06', 100, 1000, sym=TO),
                             trade('2025-03-03', 100, 1200, sym=US),
                             trade('2025-03-10', -100, 1250, sym=US)]
                            + journal('2025-04-01', 100, TO, US)
                            + [trade('2025-06-02', -50, 700, sym=US)])
        self.assertEqual(sale(res, '2025-06-02', US)['days_held'],
                         (datetime.date(2025, 6, 2) - datetime.date(2025, 3, 3)).days)


if __name__ == '__main__':
    unittest.main()
