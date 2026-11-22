"""A trade that takes the pool through zero, and a rename into a ticker
already held, as the ACB pool books them: the part past the holding is a
new position that begins on the trade's date (a sale past the shares
held is a short sale, CA-DISP-04) and carries its own share of what the
trade brought, and nothing of what the closed position held; a renamed
holding's acquisition date carries to the new ticker, and into a ticker
already held the merged holding shows the later of the two purchases
(CA-ACB-RENAME; the owner's ruling on backlog TJ-4241).

PLAN step 13, commit 0: pins for the pool's operations no test held (the
2026-11-17 mutation pass). Synthetic data only.
"""
import contextlib
import datetime
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import taxjson.bin.taxjson_harvest as HV
from tax_rules import rule
from taxjson.lib.pipeline import GainsRequest, run_gains
from taxjson.rules import ids as R
from _support.planning_harvest import _TODAY, _run as run_harvest
from _support.engine_ca_pool_books import (
    contract, holding, rename, return_of_capital, run_canada, sale, trade)

SYM = 'SYQ.TO'
CALL = 'SYQ251219C00010000.TO'


def denied_loss_of_one():
    """One share bought for 100000, sold for 50000 and bought back two
    days later for 50000: held at 100000, 50000 of it the denied loss."""
    return [trade('2025-01-06', 1, 100000), trade('2025-02-03', -1, 50000),
            trade('2025-02-05', 1, 50000)]


class TestTheUnitsPastZeroAreANewPosition(unittest.TestCase):

    @rule(R.CA_DISP_04)
    def test_a_sale_past_the_holding_opens_a_short_on_its_own_date(self):
        res, _ = run_canada([trade('2025-01-06', 100, 1000),
                             trade('2025-03-03', -150, 1800)])
        # 100 sold for 1200 against 1000; 50 short for the other 600.
        self.assertEqual(round(sale(res, '2025-03-03')['gain'], 2), 200.0)
        short = holding(res)
        self.assertEqual((short['qty'], round(short['total_cost'], 2),
                          short['position_start_date']),
                         (-50.0, -600.0, '2025-03-03'))

    @rule(R.CA_DISP_04, R.CA_SL_01)
    def test_a_purchase_past_a_short_is_acquired_on_its_own_dates(self):
        # 50 short for 600; 150 bought for 1500 on March 3, settling March
        # 5: 50 cover the short (500 against 600), the other 100 are a
        # holding acquired that day, on the trade date and, for the
        # 30-day window, on the settlement date.
        buy = trade('2025-03-03', 150, 1500)
        buy.date_settle = '2025-03-05'
        res, _ = run_canada([trade('2025-01-06', -50, 600), buy])
        self.assertEqual(round(sale(res, '2025-03-03')['gain'], 2), 100.0)
        held = holding(res)
        self.assertEqual((held['qty'], round(held['total_cost'], 2)), (100.0, 1000.0))
        self.assertEqual((held['position_start_date'], held['last_acq_date'],
                          held['last_acq_settle']),
                         ('2025-03-03', '2025-03-03', '2025-03-05'))

    @rule(R.CA_OPT_06, R.CA_SL_09, R.CA_DISP_04)
    def test_shares_past_a_cover_keep_their_share_of_an_exercised_calls_denied_loss(self):
        # A call bought for 300 is sold for 100 and bought back for 100
        # two days later: the 200 loss is denied and is in the call's
        # cost (300). Exercised at 10 while 50 shares are short (sold for
        # 700): the 100 shares cost 1000 + 300. 50 cover the short (650
        # against 700); the 50 kept cost 650, and half the call's denied
        # 200 is in them.
        res, _ = run_canada([
            contract('2025-01-06', 1, 300, CALL),
            contract('2025-02-03', -1, 100, CALL),
            contract('2025-02-05', 1, 100, CALL),
            trade('2025-03-20', -50, 700),
            contract('2025-04-01', -1, 0, CALL, action='ASSIGN'),
            trade('2025-04-01', 100, 1000)])
        cover = sale(res, '2025-04-01')
        self.assertEqual((cover['qty'], round(cover['gain'], 2)), (50.0, 50.0))
        kept = holding(res)
        self.assertEqual((kept['qty'], round(kept['total_cost'], 2),
                          kept['position_start_date']),
                         (50.0, 650.0, '2025-04-01'))
        self.assertAlmostEqual(kept['deferred_wash'], 100.0, 2)

    @rule(R.CA_RPT_16, R.CA_DISP_04)
    def test_a_short_opened_past_a_residue_reports_none_of_the_longs_cut_deferral(self):
        # One share at 100000, half of it a denied loss; a return of
        # capital of 80000 takes 30000 of the denied loss with it. All
        # but half a millionth of the share is sold; that residue still
        # costs a cent, so it is a holding. The next sale of 1 closes it
        # and is short by the rest. What the capital returned took of
        # the denied loss belongs to the long shares' sales: the cover
        # of the short, a new position, reports none of it.
        res, _ = run_canada(denied_loss_of_one() + [
            return_of_capital('2025-03-03', 80000),
            trade('2025-04-01', -0.9999995, 50000),
            trade('2025-04-10', -1, 60000),
            trade('2025-05-10', 1, 50000)])
        first = sale(res, '2025-04-01')
        self.assertAlmostEqual(first['deferred_loss_cut'], 29999.985, 3)
        cover = sale(res, '2025-05-10')
        self.assertEqual(cover['direction'], 'SHORT')
        self.assertNotIn('deferred_loss_cut', cover)
        self.assertNotIn('deferred_loss_recovered', cover)

    @rule(R.CA_ACB_11, R.CA_OPT_06, R.CA_DISP_04)
    def test_shares_past_a_cover_delivered_by_a_call_of_unknown_cost_have_no_known_cost(self):
        # A call held from before the data (its cost unknown) is exercised
        # at 10 while 50 shares are short: 50 of the 100 delivered cover
        # the short, the other 50 are a holding whose cost holds the
        # call's unknown premium. Their sale for 800 is not a gain of 300:
        # it is listed for manual reporting with the line the unknown cost
        # came by, and the year's total leaves it out.
        rows = [trade('2025-01-02', 1, 0, sym=CALL, action='OPENING_BALANCE'),
                trade('2025-03-20', -50, 700),
                contract('2025-04-01', -1, 0, CALL, action='ASSIGN'),
                trade('2025-04-01', 100, 1000),
                trade('2025-06-01', -50, 800)]
        res, _ = run_canada(rows)
        kept = sale(res, '2025-06-01')
        self.assertTrue(kept['tainted'])
        self.assertEqual([(s['symbol'], s['date'], s['quantity'])
                          for s in kept['unknown_cost_from']],
                         [(CALL, '2025-01-02', 1.0)])
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            run = run_gains(rows, [], [], req=GainsRequest(
                country='canada', taxable=True, year=2025))
        self.assertEqual([(m['date'], m['qty']) for m in run['manual_reporting_required']],
                         [('2025-04-01', 50.0), ('2025-06-01', 50.0)])
        self.assertEqual([r for r in run['transactions'] if r.get('qty')], [])
        self.assertAlmostEqual(run['summary']['total_gain'], 0.0, 2)


class TestARenamedHoldingsAcquisitionDateCarries(unittest.TestCase):
    """A rename into a ticker already held: the merged holding shows the
    LATER of the two last purchases, as one symbol bought twice does
    (CA-ACB-RENAME; the owner's ruling on backlog TJ-4241, 2026-11-18,
    which ended this pin's CHARACTERISATION of the earlier date). Only a
    side never bought gives way (a new ticker): onto a ticker bought and
    sold out, its purchase still shows when it is the later, as one
    ticker's does (backlog TJ-4413: it gave way whole, and the source's
    older date showed)."""

    def _days(self, *rows):
        res, _ = run_canada(list(rows) + [rename('2025-03-03', 'OLD.TO', 'NEW.TO'),
                                          trade('2025-06-02', -50, 700, sym='NEW.TO')])
        rec = sale(res, '2025-06-02', 'NEW.TO')
        return round(rec['gain'], 2), rec['days_held']

    @staticmethod
    def _since(y, m, d):
        return (datetime.date(2025, 6, 2) - datetime.date(y, m, d)).days

    @rule(R.CA_ACB_RENAME)
    def test_into_a_ticker_bought_later_the_later_purchase_shows(self):
        # OLD bought in January 2024 is renamed NEW, itself bought in
        # February 2025: the days held of a sale of NEW count from the
        # February purchase (119 days), not the 2024 one (511).
        self.assertEqual(self._days(trade('2024-01-08', 100, 1000, sym='OLD.TO'),
                                    trade('2025-02-03', 100, 1200, sym='NEW.TO')),
                         (150.0, self._since(2025, 2, 3)))

    @rule(R.CA_ACB_RENAME)
    def test_a_source_bought_later_brings_its_purchase(self):
        # The other way round: NEW held since 2024, OLD bought in February
        # 2025 and renamed into it: the February purchase shows.
        self.assertEqual(self._days(trade('2024-01-08', 100, 1200, sym='NEW.TO'),
                                    trade('2025-02-03', 100, 1000, sym='OLD.TO')),
                         (150.0, self._since(2025, 2, 3)))

    @rule(R.CA_ACB_RENAME)
    def test_onto_a_ticker_sold_out_its_later_purchase_shows(self):
        # NEW bought in February 2025 and sold before the rename holds
        # nothing, but its February purchase is the later of the two: the
        # days held count from it, as one ticker bought in January 2024 and
        # in February 2025 does (TJ-4413; it showed the January 2024 date).
        res, _ = run_canada([trade('2024-01-08', 100, 1000, sym='OLD.TO'),
                             trade('2025-02-03', 100, 1200, sym='NEW.TO'),
                             trade('2025-02-20', -100, 1300, sym='NEW.TO'),
                             rename('2025-03-03', 'OLD.TO', 'NEW.TO'),
                             trade('2025-06-02', -50, 700, sym='NEW.TO')])
        self.assertEqual(sale(res, '2025-06-02', 'NEW.TO')['days_held'],
                         self._since(2025, 2, 3))
        self.assertEqual(holding(res, 'NEW.TO')['last_acq_date'], '2025-02-03')

    @rule(R.CA_ACB_RENAME, R.CA_SL_19)
    def test_the_held_shares_renamed_onto_a_ticker_bought_and_sold_in_the_window(self):
        # CA-SL-19's first case: AAA bought 01-10 and sold out at a loss on
        # 01-20, BBB (bought 2024-11-06) renamed AAA on 01-25. The holding
        # shows AAA's 01-10 purchase, as one ticker bought on 2024-11-06
        # and on 01-10 would (it showed 2024-11-06).
        res, _ = run_canada([trade('2024-11-06', 100, 5000, sym='BBB.TO'),
                             trade('2025-01-10', 100, 10000, sym='AAA.TO'),
                             trade('2025-01-20', -100, 8000, sym='AAA.TO'),
                             rename('2025-01-25', 'BBB.TO', 'AAA.TO')],
                            year_end='2025-01-31')
        row = holding(res, 'AAA.TO')
        self.assertEqual((row['qty'], row['last_acq_date']), (100, '2025-01-10'))


class _Today(datetime.date):
    @classmethod
    def today(cls):
        return cls(_TODAY.year, _TODAY.month, _TODAY.day)


class TestThePlanningWindowCountsFromTheLaterPurchase(unittest.TestCase):
    """`tjs harvest`'s TX_ADD, the last purchase its 30-day window counts
    from (CA-PLAN-01), of a holding a rename merged: shares held since
    long ago renamed into a ticker bought 28 days ago show the purchase
    28 days ago, inside the window. The earlier date showed them clear of
    it (backlog TJ-4241)."""

    @staticmethod
    def _day(n):
        return (_TODAY - datetime.timedelta(days=n)).isoformat()

    def _harvest(self, rows):
        res, _ = run_canada(rows)
        with tempfile.TemporaryDirectory() as td:
            gains = Path(td) / 'margin_gains_wash.json'
            gains.write_text(json.dumps({'summary': {'year': _TODAY.year},
                                         'transactions': [],
                                         'inventory': res['inventory']}))
            with mock.patch.object(HV, 'date', _Today):
                rc, out, _ = run_harvest([str(gains), '--no-ibkr',
                                          '--country', 'canada', '--json'])
        self.assertEqual(rc, 0)
        (row,) = [r for r in json.loads(out)['rows'] if r['symbol'] == 'AAA.TO']
        return row

    @rule(R.CA_ACB_RENAME, R.CA_PLAN_01)
    def test_a_holding_bought_into_28_days_ago_is_inside_the_window(self):
        day = self._day
        row = self._harvest([trade(day(500), 100, 1240, sym='OLD.TO'),
                             trade(day(28), 100, 1240, sym='AAA.TO'),
                             rename(day(10), 'OLD.TO', 'AAA.TO')])
        self.assertEqual((row['qty'], row['verdict']), (200, 'LOSS'))
        self.assertEqual((row['taxable_last_add'], row['taxable_last_add_days']),
                         (day(28), -28))

    @rule(R.CA_ACB_RENAME, R.CA_PLAN_01)
    def test_a_ticker_bought_and_sold_out_28_days_ago_is_inside_the_window(self):
        # The target bought 28 days ago and sold out before the rename: its
        # purchase still starts the window, as one ticker's would (TJ-4413:
        # the shares held since long ago read as clear of it).
        day = self._day
        row = self._harvest([trade(day(500), 100, 1240, sym='OLD.TO'),
                             trade(day(28), 100, 1240, sym='AAA.TO'),
                             trade(day(20), -100, 1100, sym='AAA.TO'),
                             rename(day(10), 'OLD.TO', 'AAA.TO')])
        self.assertEqual(row['qty'], 100)
        self.assertEqual((row['taxable_last_add'], row['taxable_last_add_days']),
                         (day(28), -28))


if __name__ == '__main__':
    unittest.main()
