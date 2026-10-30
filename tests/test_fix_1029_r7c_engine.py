"""Regression tests for fix round 7 (wave C), group engine: the engines
(lib/core.py, lib/corp_actions.py, lib/futures.py).

TJ-0947 an option held at an unknown cost (an OPENING cost=unknown),
        exercised: its shares were booked at the strike alone, a
        definite gain; their cost is unknown too.
TJ-0948 a taxable spin-off's fraction paid in cash was left out of the
        dividend, and the broker's $0 fraction diluted the new shares'
        cost.
TJ-0949 two broker accounts' exports in one folder: a short future
        opened in one closed the long held in the other.
TJ-0951 a US §355 spin-off allocated basis from lots bought after its
        record date.
TJ-0952 buying back a warrant written short was named as a
        superficial-loss replacement.
TJ-0953 a US short sale covered at a loss in December and delivered in
        January was the earlier year's loss.
TJ-0954 a warrant exercise with no shares per warrant stated, delivered
        in several rows: the first row took all the warrants' cost.
TJ-0956 a US return of capital beyond many small lots' basis: each
        lot's sub-cent excess was left as negative basis.

Every fixture is synthetic: invented tickers (SYN*), account labels
and amounts.
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import gains_both, tx


def _gains(book, country, year=2025):
    """pipeline.run_gains under one country (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return run_gains(list(book), [], [], req=GainsRequest(
            country=country, taxable=True, year=year,
            phantom_hint=False))


# ---------------------------------------------------------------- TJ-0947
class TestUnknownCostOptionExercise(unittest.TestCase):
    """One long SYN call held before the data (cost unknown), exercised
    into 100 SYN at 10 (1000) and the shares sold for 1200. With the
    call's purchase in the book (100), the sale's cost is 1100 (gain
    100). Without it, the cost is unknown: the sale is reported by
    hand, never a definite gain of 200."""

    OPT = 'SYN250620C00010000.US'

    def _book(self, known):
        first = (tx('BUYSELL', '2024-12-20', self.OPT, 1, 100) if known
                 else tx('OPENING_BALANCE', '2024-12-31', self.OPT, 1, 0,
                         time='00:00:00'))
        return [first,
                tx('ASSIGN', '2025-06-10', self.OPT, -1, 0),
                tx('ASSIGN', '2025-06-10', 'SYN.US', 100, 1000,
                   time='10:01:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -100, 1200)]

    @rule("CA-ACB-11")
    @rule("US-BASIS-04")
    def test_unknown_cost_call_makes_its_shares_unknown_cost(self):
        for c, res in gains_both(self._book(False), year=2025).items():
            with self.subTest(country=c):
                self.assertEqual(
                    [t for t in res['transactions']
                     if t['symbol'] == 'SYN.US'], [])
                manual = res.get('manual_reporting_required') or []
                self.assertEqual([(m['symbol'], m['qty']) for m in manual],
                                 [('SYN.US', 100.0)])

    def test_known_cost_call_still_carries_its_premium(self):
        for c, res in gains_both(self._book(True), year=2025).items():
            with self.subTest(country=c):
                sale = [t for t in res['transactions']
                        if t['symbol'] == 'SYN.US']
                self.assertEqual(len(sale), 1)
                self.assertAlmostEqual(sale[0]['cost'], 1100.0, places=4)
                self.assertAlmostEqual(sale[0]['gain'], 100.0, places=4)
                self.assertFalse(res.get('manual_reporting_required'))

    def test_unknown_cost_written_put_assigned(self):
        """A written put held before the data (premium unknown),
        assigned: the 100 shares bought at the strike carry it."""
        put = 'SYN250620P00010000.US'
        book = [tx('OPENING_BALANCE', '2024-12-31', put, -1, 0,
                   time='00:00:00'),
                tx('ASSIGN', '2025-06-10', put, 1, 0),
                tx('ASSIGN', '2025-06-10', 'SYN.US', 100, 1000,
                   time='10:01:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -100, 1200)]
        for c, res in gains_both(book, year=2025).items():
            with self.subTest(country=c):
                manual = res.get('manual_reporting_required') or []
                self.assertEqual([(m['symbol'], m['qty']) for m in manual],
                                 [('SYN.US', 100.0)])

    def test_unknown_cost_long_put_exercised(self):
        """A long put held before the data, exercised against 100
        known shares: the sale's proceeds carry its unknown cost."""
        put = 'SYN250620P00012000.US'
        book = [tx('BUYSELL', '2025-01-06', 'SYN.US', 100, 1000),
                tx('OPENING_BALANCE', '2024-12-31', put, 1, 0,
                   time='00:00:00'),
                tx('ASSIGN', '2025-06-10', put, -1, 0),
                tx('ASSIGN', '2025-06-10', 'SYN.US', -100, 1200,
                   time='10:01:00')]
        for c, res in gains_both(book, year=2025).items():
            with self.subTest(country=c):
                self.assertEqual(
                    [t for t in res['transactions']
                     if t['symbol'] == 'SYN.US'], [])
                manual = res.get('manual_reporting_required') or []
                self.assertEqual([(m['symbol'], m['qty']) for m in manual],
                                 [('SYN.US', 100.0)])


# ---------------------------------------------------------------- TJ-0948
class TestTaxableSpinoffFraction(unittest.TestCase):
    """A §301 spin-off delivering 10 SYNS and paying 10 for 0.5 more
    (fmv_per_share 20): the distribution is 210, the 10.5 shares cost
    210 and the fraction is sold for the cash (Canada's
    taxable_deemed_dividend: test_fix_batch_d_spinoff)."""

    @rule("US-CORP-06")
    def test_us_301_counts_and_sells_the_fraction(self):
        from taxjson.lib.corp_actions import CorporateAction, resolve_event
        ev = CorporateAction(
            date='2025-06-16', time='09:30:00', action_type='spinoff',
            source_symbol='SYNP.US', source_isin='SYNP.US',
            target_symbol='SYNS.US', target_isin='SYNS.US',
            ratio_new=10, ratio_old=105, qty_disposed=0.0,
            qty_received=10.0, fmv=0.0, currency='USD',
            target_currency='USD', account='margin', cash_in_lieu=10.0,
            cash_in_lieu_currency='USD', fraction_in_lieu=0.5,
            cash_in_lieu_date='2025-06-19')
        with contextlib.redirect_stderr(io.StringIO()):
            rows = resolve_event(ev, 'taxable_distribution_301', 'usa',
                                 {'fmv_per_share': 20.0})
        self.assertEqual(
            [(r['action'], r.get('quantity'), r['net_amount'])
             for r in rows],
            [('DIVIDEND', 0.0, 210.0), ('BUYSELL', 10.5, 210.0),
             ('BUYSELL', -0.5, 10.0)])
        self.assertEqual(rows[2]['date'], '2025-06-19')


# ---------------------------------------------------------------- TJ-0949
def _fut(i, date, qty, net, src, account='a', sym='F:SYNZ6.US'):
    from taxjson.lib.core import TaxTransaction
    return TaxTransaction(id=i, action='BUYSELL', date=date,
                          date_settle=date, time='10:00:00', symbol=sym,
                          quantity=qty, net_amount=net, currency='CAD',
                          account=account, source_account=src)


class TestFuturesBrokerAccountsInOneFolder(unittest.TestCase):
    """Two broker accounts' exports in one taxable folder: a long bought
    at 100 in one, a short opened at 120 in the other (2025); the long
    sold at 150 and the short covered at 110 in 2026. Nothing closes in
    2025; 2026 realizes 50 + 10 = 60, as with a folder per account."""

    def _book(self, src_b='bbbbbbbbbb'):
        return [_fut('l1', '2025-01-06', 1, 100, 'aaaaaaaaaa'),
                _fut('s1', '2025-02-03', -1, 120, src_b),
                _fut('l2', '2026-01-05', -1, 150, 'aaaaaaaaaa'),
                _fut('s2', '2026-02-02', 1, 110, src_b)]

    def _parts(self, book):
        from taxjson.lib.futures import settle_futures
        rows, _ = settle_futures(book, 'average')
        return [(r.date, r.quantity, round(r.net_amount, 6),
                 r.settle_native.split()[0]) for r in rows]

    def test_settlement_keeps_each_broker_accounts_position(self):
        self.assertEqual(self._parts(self._book()), [
            ('2025-01-06', 1.0, 0.0, 'LO'),
            ('2025-02-03', -1.0, 0.0, 'SO'),
            ('2026-01-05', -1.0, 50.0, 'LC'),
            ('2026-02-02', 1.0, 10.0, 'SC')])

    def test_a_row_naming_no_broker_account_keeps_one_book(self):
        """A .tt line (no broker account) beside an export: one book,
        as before."""
        self.assertEqual(self._parts(self._book(src_b=''))[1],
                         ('2025-02-03', -1.0, 20.0, 'LC'))

    @rule("CA-FX-04")
    def test_canada_gains_by_year(self):
        from taxjson.lib.futures import settle_futures
        rows, _ = settle_futures(self._book(), 'average')
        for year, want in ((2025, 0.0), (2026, 60.0)):
            with self.subTest(year=year):
                from taxjson.lib.pipeline import GainsRequest, run_gains
                with contextlib.redirect_stderr(io.StringIO()):
                    r = run_gains(list(rows), [], [], GainsRequest(
                        country='canada', taxable=True, year=year,
                        phantom_hint=False))
                self.assertAlmostEqual(r['summary']['total_gain'], want,
                                       places=4)

    @rule("CA-FX-04")
    def test_canada_longs_of_two_broker_accounts_share_one_average(self):
        """s.47: longs at 100 and 140 in the two broker accounts of one
        folder are one average (120); a sale of one at 150 gains 30."""
        from taxjson.lib.futures import pool_across_accounts, settle_futures
        book = [_fut('l1', '2025-01-06', 1, 100, 'aaaaaaaaaa'),
                _fut('l2', '2025-01-07', 1, 140, 'bbbbbbbbbb'),
                _fut('x', '2025-03-03', -1, 150, 'aaaaaaaaaa')]
        rows, _ = settle_futures(book, 'average')
        pooled, _notes = pool_across_accounts(rows)
        close = [r for r in pooled if r.id == 'x']
        self.assertEqual(len(close), 1)
        self.assertAlmostEqual(close[0].net_amount, 30.0, places=6)


# ---------------------------------------------------------------- TJ-0951
class TestUs355EntitledLots(unittest.TestCase):
    """100 SYNP bought 2024-01-08 for 100 and 100 more 2025-06-10 for
    300; a §355 spin-off with record date 2025-06-02 pays 10 SYNS on
    2025-06-16, allocated 10. Only the old lot is entitled: it keeps 90
    and the 10 SYNS cost 10 with its 2024 date; the June lot keeps 300.
    Selling the old 100 for 200 and the 10 SYNS for 100: 200, all long
    term."""

    def _rows(self, record_date):
        from taxjson.lib.core import coerce_transaction_row
        from taxjson.lib.corp_actions import CorporateAction, resolve_event
        ev = CorporateAction(
            date='2025-06-16', time='09:30:00', action_type='spinoff',
            source_symbol='SYNP.US', source_isin='SYNP.US',
            target_symbol='SYNS.US', target_isin='SYNS.US',
            ratio_new=10, ratio_old=100, qty_disposed=0.0,
            qty_received=10.0, fmv=0.0, currency='USD',
            target_currency='USD', account='margin',
            record_date=record_date)
        with contextlib.redirect_stderr(io.StringIO()):
            rows = resolve_event(ev, 'tax_free_355', 'usa',
                                 {'allocated_acb': 10.0})
        return [coerce_transaction_row(r, i, 'spin')
                for i, r in enumerate(rows)]

    def _run(self, record_date):
        book = [tx('BUYSELL', '2024-01-08', 'SYNP.US', 100, 100),
                tx('BUYSELL', '2025-06-10', 'SYNP.US', 100, 300),
                *self._rows(record_date),
                tx('BUYSELL', '2025-07-02', 'SYNP.US', -100, 200),
                tx('BUYSELL', '2025-07-03', 'SYNS.US', -10, 100)]
        return _gains(book, 'usa')

    @rule("US-CORP-07")
    def test_only_lots_before_the_record_date_share_basis(self):
        res = self._run('2025-06-02')
        sales = res['transactions']
        self.assertAlmostEqual(sum(t['gain'] for t in sales), 200.0,
                               places=4)
        self.assertEqual({t['term'] for t in sales}, {'LONG_TERM'})
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertAlmostEqual(inv['SYNP.US']['total_cost'], 300.0,
                               places=4)

    def test_no_record_date_keeps_every_lot_held(self):
        """With no record date stated, the lots held on the pay date
        share it, as before."""
        res = self._run('')
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertAlmostEqual(inv['SYNP.US']['total_cost'], 292.5,
                               places=4)


# ---------------------------------------------------------------- TJ-0952
class TestWarrantShortCoverIsNoAcquisition(unittest.TestCase):
    """SYN.TO bought for 1000 and sold 2025-06-02 for 900 (a 100 loss).
    A warrant SYN.WT.TO written short in January and bought back June 5
    acquires no right: no right_vs_share_loss flag. A warrant bought to
    open on June 5 is still named."""

    def _flags(self, short_first):
        book = [tx('BUYSELL', '2025-01-06', 'SYN.TO', 100, 1000,
                   currency='CAD'),
                tx('BUYSELL', '2025-06-02', 'SYN.TO', -100, 900,
                   currency='CAD')]
        if short_first:
            book.append(tx('BUYSELL', '2025-01-10', 'SYN.WT.TO', -1, 2,
                           currency='CAD'))
        book.append(tx('BUYSELL', '2025-06-05', 'SYN.WT.TO', 1, 1,
                       currency='CAD'))
        res = _gains(book, 'canada')
        return [w for w in res.get('option_replacement_warnings') or []
                if w['rule'] == 'right_vs_share_loss']

    @rule("CA-SL-14")
    def test_cover_of_a_written_warrant_is_not_flagged(self):
        self.assertEqual(self._flags(True), [])

    @rule("CA-SL-14")
    def test_warrant_bought_to_open_is_still_flagged(self):
        flags = self._flags(False)
        self.assertEqual([(w['option_symbol'], w['option_qty'])
                          for w in flags], [('SYN.WT.TO', 1.0)])


# ---------------------------------------------------------------- TJ-0953
class TestUsShortLossDeliveredInJanuary(unittest.TestCase):
    """10 SYN sold short 2025-01-06 for 100 and covered for 120 on
    2025-12-31, delivered 2026-01-02: the 20 loss is 2026's (Rev. Rul.
    2002-44, Situation 1). A cover on 2025-12-30 settling 2025-12-31 is
    2025's; a cover at a gain keeps its trade date (§1259)."""

    def _years(self, day, settle, cover=120):
        book = [tx('BUYSELL', '2025-01-06', 'SYN.US', -10, 100),
                tx('BUYSELL', day, 'SYN.US', 10, cover, settle=settle)]
        out = {}
        for y in (2025, 2026):
            res = _gains(book, 'usa', year=y)
            out[y] = [(t['date'], round(t['gain'], 4))
                      for t in res['transactions']]
        return out

    @rule("US-DATE-01")
    def test_loss_is_the_delivery_years(self):
        self.assertEqual(self._years('2025-12-31', '2026-01-02'),
                         {2025: [], 2026: [('2026-01-02', -20.0)]})

    def test_loss_settled_in_the_same_year(self):
        self.assertEqual(self._years('2025-12-30', '2025-12-31'),
                         {2025: [('2025-12-30', -20.0)], 2026: []})

    def test_gain_keeps_its_trade_date(self):
        self.assertEqual(self._years('2025-12-31', '2026-01-02', cover=80),
                         {2025: [('2025-12-31', 20.0)], 2026: []})


# ---------------------------------------------------------------- TJ-0954
class TestUnsizedWarrantExerciseInParts(unittest.TestCase):
    """100 SYN.WT bought for 100, exercised into 100 SYN at 10 delivered
    as two same-day legs of 50, with no shares per warrant stated; 50
    sold a month later for 600. Each leg carries 500 + 50: the sale's
    cost is 550 (gain 50), as with `x1` stated."""

    def _sale(self, country, size):
        ex = tx('ASSIGN', '2025-06-02', 'SYN.WT.US', -100, 0,
                exercise_of='SYN.US', **({'multiplier': size}
                                         if size else {}))
        book = [tx('BUYSELL', '2024-01-08', 'SYN.WT.US', 100, 100), ex,
                tx('BUYSELL', '2025-06-02', 'SYN.US', 50, 500,
                   time='10:01:00'),
                tx('BUYSELL', '2025-06-02', 'SYN.US', 50, 500,
                   time='10:02:00'),
                tx('BUYSELL', '2025-07-02', 'SYN.US', -50, 600)]
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            res = run_gains(list(book), [], [], req=GainsRequest(
                country=country, taxable=True, year=2025,
                phantom_hint=False))
        sales = [t for t in res['transactions'] if t['symbol'] == 'SYN.US']
        return sales, err.getvalue()

    @rule("US-OPT-06")
    def test_us_cost_is_shared_by_the_deliveries(self):
        for size in (0, 1):
            with self.subTest(size=size):
                sales, err = self._sale('usa', size)
                self.assertEqual(len(sales), 1)
                self.assertAlmostEqual(sales[0]['cost'], 550.0, places=4)
                self.assertEqual('states no shares per warrant' in err,
                                 not size, err[-600:])

    @rule("CA-OPT-09")
    def test_canada_cost_is_shared_by_the_deliveries(self):
        sales, _err = self._sale('canada', 0)
        self.assertEqual(len(sales), 1)
        self.assertAlmostEqual(sales[0]['cost'], 550.0, places=4)


# ---------------------------------------------------------------- TJ-0956
class TestUsRocExcessAcrossSmallLots(unittest.TestCase):
    """500 one-share SYN lots bought 2024-01-08 for 0.50 each (basis
    250), then a 252 return of capital on 2025-06-02: 2 of long-term
    gain in 2025 and zero basis, as for one 500-share lot."""

    def _run(self, lots):
        each = 500 // lots
        book = [tx('BUYSELL', '2024-01-08', 'SYN.US', each, 0.5 * each,
                   time=f'10:{i // 60:02d}:{i % 60:02d}')
                for i in range(lots)]
        book.append(tx('ADJUST', '2025-06-02', 'SYN.US', 0, -252))
        return _gains(book, 'usa', year=2025)

    @rule("US-ROC-02")
    def test_fragmented_lots_book_the_excess(self):
        for lots in (1, 500):
            with self.subTest(lots=lots):
                res = self._run(lots)
                rows = [t for t in res['transactions'] if t.get('deemed')]
                self.assertEqual(len(rows), 1)
                self.assertAlmostEqual(rows[0]['gain'], 2.0, places=6)
                self.assertEqual(rows[0]['term'], 'LONG_TERM')
                self.assertEqual(rows[0]['acquired_date'], '2024-01-08')


if __name__ == '__main__':
    unittest.main()
