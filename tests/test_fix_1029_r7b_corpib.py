"""Fix round 7 (2026-10-29, wave B), group corpib: corporate actions
from IB exports and spin-off treatment (backlog TJ-0752, TJ-0753,
TJ-0754, TJ-0758, TJ-0841, TJ-0842, TJ-0658). Synthetic data only:
made-up tickers and ISINs, accounts U1234567 and U9990002."""
import contextlib
import copy
import csv
import dataclasses
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import corp_actions as CA
from taxjson.lib.core import TaxTransaction, coerce_transaction_row
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule

HEAD = ['Corporate Actions', 'Header', 'Asset Category', 'Currency',
        'Report Date', 'Date/Time', 'Description', 'Quantity', 'Proceeds',
        'Value', 'Realized P/L', 'Code']


def ca_row(day_time, desc, qty, value, cur='CAD', code=''):
    return ['Corporate Actions', 'Data', 'Stocks', cur, day_time[:10],
            day_time, desc, qty, 0, value, 0, code]


def statement(folder: Path, name: str, account: str, rows) -> Path:
    s = io.StringIO()
    csv.writer(s).writerows(
        [['Account Information', 'Header', 'Field Name', 'Field Value'],
         ['Account Information', 'Data', 'Account', account], HEAD] + rows)
    p = folder / name
    p.write_text(s.getvalue())
    return p


def events_of(files, context=True):
    """Every statement parsed with the others as context, the copies
    combined (as taxjson_corp_actions does)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
        evs = CA.combine_broker_copies(
            [e for f in files for e in CA.parse_ib_corporate_actions(
                f, 'margin', files if context else None)])
    return evs, err.getvalue()


def shape(evs):
    return sorted((e.action_type, e.source_symbol, e.target_symbol,
                   round(e.qty_disposed, 6), round(e.qty_received, 6))
                  for e in evs)


def tx(i, action, day, qty, amount, sym, **kw):
    return TaxTransaction(id=i, action=action, date=day, date_settle=day,
                          symbol=sym, quantity=qty, net_amount=amount,
                          currency='CAD', account='a', **kw)


def gains(book, year=2025):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
        g = run_gains(copy.deepcopy(book), req=GainsRequest(
            country='canada', year=year, taxable=True, phantom_hint=False))
    return g, err.getvalue()


def booked(rows):
    fields = {f.name for f in dataclasses.fields(TaxTransaction)}
    return [coerce_transaction_row(
        {k: v for k, v in dict(r, id=f'corp{i}').items() if k in fields},
        i, 'synthetic') for i, r in enumerate(rows)]


MERGER = 'ALPH(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2'


def merger_rows(when='2025-06-11, 20:25:00', cancel_on=None):
    rows = []
    for sym, isin, q, v in (('ALPH', 'CA0000000001', -100, -200),
                            ('BETA', 'CA0000000002', 50, 200)):
        desc = f'{MERGER} ({sym}, {sym} CORP, {isin})'
        rows.append(ca_row(when, desc, q, v))
        if cancel_on:
            rows.append(ca_row(cancel_on, desc, -q, -v, code='Ca'))
    return rows


class TestCancellationKeepsItsAccount(unittest.TestCase):
    """TJ-0752: a Ca row of one IB statement's account cancelled the
    same merger in another account's statement (neither has an Account
    column), and the live merger was never offered."""

    def test_other_accounts_cancellation_leaves_the_live_merger(self):
        with tempfile.TemporaryDirectory() as td:
            a = statement(Path(td), 'a.csv', 'U1234567', merger_rows())
            b = statement(Path(td), 'b.csv', 'U9990002', merger_rows(
                cancel_on='2025-06-12, 20:25:00'))
            evs, err = events_of([a, b])
        self.assertEqual(shape(evs),
                         [('merger', 'ALPH.TO', 'BETA.TO', 100.0, 50.0)],
                         err)
        self.assertEqual(evs[0].broker_account, 'U1234567')

    def test_own_cancellation_still_cancels(self):
        with tempfile.TemporaryDirectory() as td:
            b = statement(Path(td), 'b.csv', 'U9990002', merger_rows(
                cancel_on='2025-06-12, 20:25:00'))
            evs, err = events_of([b])
        self.assertEqual(evs, [], err)

    def test_cancellation_in_the_accounts_next_statement(self):
        # The original and its Ca in two yearly downloads of ONE account.
        desc = f'{MERGER} (BETA, BETA CORP, CA0000000002)'
        with tempfile.TemporaryDirectory() as td:
            y1 = statement(Path(td), 'y1.csv', 'U1234567',
                           merger_rows(when='2025-12-30, 20:25:00'))
            y2 = statement(Path(td), 'y2.csv', 'U1234567', [
                ca_row('2026-01-02, 20:25:00', desc, -50, -200, code='Ca'),
                ca_row('2026-01-02, 20:25:00',
                       f'{MERGER} (ALPH, ALPH CORP, CA0000000001)', 100,
                       200, code='Ca')])
            evs, err = events_of([y1, y2])
        self.assertEqual(evs, [], err)


SPLITUP = ('ALPH(CA0000000001) Merged(Acquisition) WITH BETAV 1 for 2, '
           'CA0000000002 1 for 2')


def splitup_rows():
    return [ca_row('2025-06-11, 20:25:00',
                   f'{SPLITUP} ({sym}, {sym} CORP, {isin})', q, v)
            for sym, isin, q, v in (('ALPH', 'CA0000000001', -100, -600),
                                    ('BETA', 'CA0000000002', 50, 400),
                                    ('GAMM', 'CA0000000003', 50, 200))]


class TestSplitUpAcrossAccounts(unittest.TestCase):
    """TJ-0753: one split-up held in two broker accounts made a phantom
    BETA -> BETA spin-off and split the successors' quantities."""

    def test_successors_are_added_across_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            a = statement(Path(td), 'a.csv', 'U1234567', splitup_rows())
            b = statement(Path(td), 'b.csv', 'U9990002', splitup_rows())
            evs, err = events_of([a, b])
            independent, _ = events_of([a, b], context=False)
        self.assertEqual(shape(evs), [
            ('merger', 'ALPH.TO', 'BETA.TO', 200.0, 100.0),
            ('spinoff', 'BETA.TO', 'GAMM.TO', 0.0, 100.0)], err)
        self.assertEqual(shape(evs), shape(independent))
        spin = next(e for e in evs if e.action_type == 'spinoff')
        rows = CA.resolve_event(spin, 'taxable_deemed_dividend', 'canada',
                                {})
        self.assertAlmostEqual(sum(r['net_amount'] for r in rows
                                   if r['action'] == 'DIVIDEND'), 400.0)

    def test_one_account_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            a = statement(Path(td), 'a.csv', 'U1234567', splitup_rows())
            evs, err = events_of([a])
        self.assertEqual(shape(evs), [
            ('merger', 'ALPH.TO', 'BETA.TO', 100.0, 50.0),
            ('spinoff', 'BETA.TO', 'GAMM.TO', 0.0, 50.0)], err)


def journal_rows(day, qty, value):
    desc = 'BETA.CAD(10000001) Merged(Acquisition) WITH BETA 1 for 1'
    when = f'{day}, 20:25:00'
    return [ca_row(when, f'{desc} (BETA.CAD, SYNTHETIC CORP, US0000000002)',
                   -qty, -value),
            ca_row(when, f'{desc} (BETA, SYNTHETIC CORP, US0000000002)',
                   qty, value * 0.8, cur='USD')]


def cad_merger_rows(day, qty):
    desc = 'ALPH(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 2'
    when = f'{day}, 20:25:00'
    return [ca_row(when, f'{desc} (ALPH, SYNTHETIC CORP, CA0000000001)',
                   -qty, -200),
            ca_row(when, f'{desc} (BETA.CAD, SYNTHETIC CORP, US0000000002)',
                   qty / 2, 200)]


class TestJournalBeforeMergerStaysApart(unittest.TestCase):
    """TJ-0754: a listing journal months BEFORE a merger into the same
    issuer was folded into the merger, and its own event deleted."""

    def _events(self, rows):
        with tempfile.TemporaryDirectory() as td:
            f = statement(Path(td), 'a.csv', 'U1234567', rows)
            return events_of([f])

    def test_earlier_journal_is_its_own_event(self):
        evs, err = self._events(cad_merger_rows('2025-06-11', 100)
                                + journal_rows('2025-01-10', 10, 40))
        self.assertEqual(shape(evs), [
            ('merger', 'ALPH.TO', 'BETA.CAD.TO', 100.0, 50.0),
            ('merger', 'BETA.CAD.TO', 'BETA.US', 10.0, 10.0)], err)

    def test_journal_of_other_shares_is_its_own_event(self):
        # After the merger, but of 10 shares where it delivered 50.
        evs, err = self._events(cad_merger_rows('2025-06-11', 100)
                                + journal_rows('2025-06-12', 10, 40))
        self.assertEqual(len(evs), 2, err)

    def test_following_journal_still_collapses(self):
        evs, err = self._events(cad_merger_rows('2025-06-11', 100)
                                + journal_rows('2025-06-12', 50, 200))
        self.assertEqual(shape(evs), [
            ('merger', 'ALPH.TO', 'BETA.US', 100.0, 50.0)], err)


SPIN = 'ALPH(CA0000000001) Spinoff 1 for 2 (SPNA, SPINCO CORP, CA0000000002)'


class TestSpinoffDeliveriesAdded(unittest.TestCase):
    """TJ-0758: two live deliveries of one spin-off at two times of the
    day were read as two statements' copies and one was dropped."""

    def test_two_deliveries_are_one_event(self):
        with tempfile.TemporaryDirectory() as td:
            f = statement(Path(td), 'a.csv', 'U1234567', [
                ca_row('2025-06-11, 10:00:00', SPIN, 10, 100),
                ca_row('2025-06-11, 11:00:00', SPIN, 5, 50)])
            evs, err = events_of([f])
        self.assertEqual(len(evs), 1, err)
        self.assertNotIn('disagree', err)
        ev = evs[0]
        self.assertAlmostEqual(ev.qty_received, 15.0)
        self.assertAlmostEqual(ev.fmv, 150.0)
        self.assertEqual(ev.time, '10:00:00')
        rows = CA.resolve_event(ev, 'taxable_deemed_dividend', 'canada', {})
        self.assertAlmostEqual(sum(r['net_amount'] for r in rows
                                   if r['action'] == 'DIVIDEND'), 150.0)

    def test_overlapping_statements_still_count_once(self):
        rows = [ca_row('2025-06-11, 10:00:00', SPIN, 10, 100),
                ca_row('2025-06-11, 11:00:00', SPIN, 5, 50)]
        with tempfile.TemporaryDirectory() as td:
            a = statement(Path(td), 'a.csv', 'U1234567', rows)
            b = statement(Path(td), 'b.csv', 'U1234567', rows)
            evs, err = events_of([a, b])
        self.assertEqual([round(e.qty_received, 6) for e in evs], [15.0],
                         err)


def s861_spinoff(qty, day='2025-06-02'):
    return CA.CorporateAction(
        date=day, time='10:00:00', action_type='spinoff',
        source_symbol='PARENT.US', source_isin='US0000000001',
        target_symbol='CHILD.US', target_isin='US0000000002',
        ratio_new=qty, ratio_old=10, qty_disposed=0, qty_received=qty,
        fmv=500, target_fmv=500, currency='CAD', target_currency='CAD',
        account='a', fractional_delivery=True)


def quiet_resolve(ev, election, hints):
    with contextlib.redirect_stderr(io.StringIO()):
        return CA.resolve_event(ev, election, 'canada', hints)


class TestDeliveredFractionKept(unittest.TestCase):
    """TJ-0841: a delivered 0.005 share (all of the spin-off, under the
    dust threshold) vanished with its allocated cost."""

    @rule("CA-CORP-06")
    def test_sub_dust_delivery_keeps_its_allocation(self):
        rows = quiet_resolve(s861_spinoff(0.005), 'rollover_s_86_1',
                             {'allocated_acb_cad': 500})
        buy = next(r for r in rows if r['action'] == 'BUYSELL')
        adj = next(r for r in rows if r['action'] == 'ADJUST')
        self.assertAlmostEqual(buy['quantity'], 0.005)
        self.assertAlmostEqual(buy['net_amount'], 500.0)
        self.assertAlmostEqual(adj['net_amount'], -500.0)
        book = ([tx('p', 'BUYSELL', '2025-01-02', 10, 1000, 'PARENT.US')]
                + booked(rows)
                + [tx('c', 'BUYSELL', '2025-09-02', -0.005, 600, 'CHILD.US'),
                   tx('q', 'BUYSELL', '2025-10-02', -10, 1000, 'PARENT.US')])
        g, log = gains(book)
        self.assertAlmostEqual(g['summary']['total_gain'], 600.0, places=2,
                               msg=log)

    def test_dust_on_whole_shares_still_snaps(self):
        ev = s861_spinoff(100.0026)
        q, _v, frac = CA._snap_received(ev, 100.0026, 500.0)
        self.assertEqual(q, 100.0)
        self.assertAlmostEqual(frac, 0.0026)

    def test_whole_share_broker_still_pays_the_fraction(self):
        ev = dataclasses.replace(s861_spinoff(0.005),
                                 fractional_delivery=False)
        q, _v, frac = CA._snap_received(ev, 0.005, 500.0)
        self.assertEqual(q, 0.0)
        self.assertAlmostEqual(frac, 0.005)


class TestForeignParentSpinoffIncome(unittest.TestCase):
    """TJ-0658: a foreign parent's taxable spin-off into Canadian shares
    booked its dividend on the Canadian shares: a Canadian eligible
    dividend, and no T1135 income for the parent."""

    @rule("CA-CORP-07")
    def test_dividend_is_the_parents(self):
        ev = CA.CorporateAction(
            date='2025-06-02', time='10:00:00', action_type='spinoff',
            source_symbol='SYN.US', source_isin='US0000000002',
            target_symbol='NEW.TO', target_isin='CA0000000001',
            ratio_new=1, ratio_old=2, qty_disposed=0, qty_received=10,
            fmv=200, currency='CAD', target_currency='CAD', account='a')
        rows = quiet_resolve(ev, 'taxable_deemed_dividend',
                             {'fmv_per_share': 20})
        div = next(r for r in rows if r['action'] == 'DIVIDEND')
        buy = next(r for r in rows if r['action'] == 'BUYSELL')
        self.assertEqual(div['symbol'], 'SYN.US')
        self.assertEqual(div['issuer_country'], 'US')
        self.assertAlmostEqual(div['net_amount'], 200.0)
        self.assertEqual(buy['symbol'], 'NEW.TO')
        self.assertAlmostEqual(buy['net_amount'], 200.0)
        from taxjson.lib.income_dating import is_canadian_issuer
        self.assertFalse(is_canadian_issuer(div))

    def test_unknown_parent_falls_back_to_the_new_shares(self):
        ev = CA.CorporateAction(
            date='2025-06-02', time='10:00:00', action_type='spinoff',
            source_symbol='(unknown parent)', source_isin='',
            target_symbol='NEW.TO', target_isin='CA0000000001',
            ratio_new=1, ratio_old=2, qty_disposed=0, qty_received=10,
            fmv=200, currency='CAD', target_currency='CAD', account='a')
        rows = quiet_resolve(ev, 'taxable_deemed_dividend',
                             {'fmv_per_share': 20})
        div = next(r for r in rows if r['action'] == 'DIVIDEND')
        self.assertEqual(div['symbol'], 'NEW.TO')
        self.assertNotIn('issuer_country', div)


class TestS861AfterTheParentWasSold(unittest.TestCase):
    """TJ-0842: an s.86.1 spin-off distributed after the parent was sold
    reduced the cost of nothing and booked a return-of-capital gain on
    the distribution date; it reduces the cost of the sale."""

    def _book(self, sale_amount=1000):
        rows = quiet_resolve(s861_spinoff(1, day='2025-01-02'),
                             'rollover_s_86_1', {'allocated_acb_cad': 500})
        return ([tx('buy', 'BUYSELL', '2024-01-02', 10, 1000, 'PARENT.US'),
                 tx('sale', 'BUYSELL', '2024-12-31', -10, sale_amount,
                    'PARENT.US')] + booked(rows))

    @rule("CA-CORP-14")
    def test_reduction_goes_to_the_prior_sale(self):
        book = self._book()
        g24, log = gains(book, 2024)
        self.assertAlmostEqual(g24['summary']['total_gain'], 500.0,
                               msg=log)
        sale = next(r for r in g24['transactions']
                    if r['symbol'] == 'PARENT.US')
        self.assertAlmostEqual(sale['cost'], 500.0)
        self.assertIn('s.86.1 spin-off on 2025-01-02 reduces the cost',
                      log)
        g25, log25 = gains(book, 2025)
        self.assertAlmostEqual(g25['summary']['total_gain'], 0.0,
                               msg=log25)
        self.assertNotIn('EMPTY pool', log25)
        # The child keeps its allocated cost.
        child = [p for p in g25['inventory'] if p['symbol'] == 'CHILD.US']
        self.assertAlmostEqual(child[0]['total_cost'], 500.0)

    @rule("CA-CORP-14")
    def test_a_loss_sale_keeps_the_gain_on_the_distribution(self):
        # Sold at a loss: the solver has read the loss, so the reduction
        # stays a gain on the distribution date, with a warning.
        book = self._book(sale_amount=900)
        g24, _ = gains(book, 2024)
        self.assertAlmostEqual(g24['summary']['total_gain'], -100.0)
        g25, log = gains(book, 2025)
        self.assertAlmostEqual(g25['summary']['total_gain'], 500.0)
        self.assertIn('cannot move the reduction to that sale', log)

    def test_a_plain_roc_on_an_empty_pool_is_unchanged(self):
        book = [tx('buy', 'BUYSELL', '2024-01-02', 10, 1000, 'PARENT.US'),
                tx('sale', 'BUYSELL', '2024-12-31', -10, 1000, 'PARENT.US'),
                tx('roc', 'ADJUST', '2025-01-02', 0, -500, 'PARENT.US')]
        g24, _ = gains(book, 2024)
        self.assertAlmostEqual(g24['summary']['total_gain'], 0.0)
        g25, log = gains(book, 2025)
        self.assertAlmostEqual(g25['summary']['total_gain'], 500.0)
        self.assertIn('EMPTY pool', log)


if __name__ == '__main__':
    unittest.main()
