"""US engine branches the suite ran but did not check (TJ-1896, the
mutation triage of 2026-11-09, area us_misc): the lot rows, ADJUST rows,
dividend compensation paid on a short, the row walk and the finish. Each
test pins a figure (or, where a warning is the only output, the warning)
that a surviving mutant of the branch changes. Synthetic data only;
tickers (ZZQ, QZ*, ZQSH, ZRS, SPX options) and accounts ('b', 'c') are
made up."""
import contextlib
import io
import re
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.lib.row_types import SPINOFF_355_TYPE
from taxjson.lib.cost_timeline import inventory_on


def _engine(book, **kw):
    """The US engine over `book`, per-account basis, every lot at the end
    of 2025 (`lots_at`); stderr captured as `_stderr`."""
    kw.setdefault('lots_at', '2025-12-31')
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = USATaxRules().compute_gains(list(book), per_account_basis=True,
                                        **kw)
    r['_stderr'] = err.getvalue()
    return r


def _lots(res, account, symbol):
    return [lot for lot in res['lots_at']
            if lot['account'] == account and lot['symbol'] == symbol]


def _paid(date, sym, amount, **kw):
    """Dividend compensation `amount` (negative: paid) on a short in b."""
    return tx('DIVIDEND_IN_LIEU', date, sym, 100, amount,
              gross_amount=amount, type='dividend_in_lieu', account='b',
              **kw)


def _covers(res):
    return [t for t in res['transactions']
            if t.get('qty') and t.get('direction') == 'SHORT']


def _pil(res):
    [p] = [t for t in res['transactions']
           if t.get('action') == 'DIVIDEND_IN_LIEU']
    return p


class TestDistributionTakesALotSettlingOnTheRecordDate(unittest.TestCase):
    """`_us_walk_adjust`, a [[distributions]] row with a record date: a lot
    settled ON the record date is a holder's lot and takes its share
    (core: `str(l.get('date_settle') or l['date'])[:10] <= _rd`)."""

    @rule(R.US_DIST_01)
    def test_both_lots_share_the_return_of_capital(self):
        # Two lots of 10: one settled 2025-02-04, one bought 2025-02-28
        # and settled 2025-03-03, the record date. The 20 returned is
        # spread over all 20 shares held of record: 10 off each lot.
        book = [tx('BUYSELL', '2025-02-03', 'ZZQ', 10, 100.0, account='b',
                   settle='2025-02-04'),
                tx('BUYSELL', '2025-02-28', 'ZZQ', 10, 100.0, account='b',
                   settle='2025-03-03'),
                tx('ADJUST', '2025-03-20', 'ZZQ', 0, -20.0, account='b',
                   type='dist', record_date='2025-03-03')]
        res = _engine(book)
        self.assertEqual([(lot['date'], lot['basis'])
                          for lot in _lots(res, 'b', 'ZZQ')],
                         [('2025-02-03', 90.0), ('2025-02-28', 90.0)])
        self.assertNotIn('unapplied', res['_stderr'])


class TestZeroAdjustWithNoLotsIsNoGain(unittest.TestCase):
    """`_us_walk_adjust`, an ADJUST with no long lot: the warning's wording
    goes by the amount's sign, a return of capital (negative) a
    §301(c)(3) gain to report; a $0 row is no return of capital (core:
    `if _amt < 0`). The warning is the branch's only output."""

    @rule(R.US_ROC_03, R.US_ROC_04)
    def test_a_zero_row_names_no_taxable_gain(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', -10, 100.0, account='b'),
                tx('ADJUST', '2025-03-03', 'ZZQ', 0, 0.0, account='b')]
        res = _engine(book)
        self.assertIn('unapplied basis adjustment', res['_stderr'])
        self.assertNotIn('§301(c)(3)', res['_stderr'])
        self.assertNotIn('return of capital', res['_stderr'])

    @rule(R.US_ROC_03)
    def test_a_negative_row_does(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', -10, 100.0, account='b'),
                tx('ADJUST', '2025-03-03', 'ZZQ', 0, -5.0, account='b')]
        self.assertIn('§301(c)(3)', _engine(book)['_stderr'])


class TestSettleTimelineDatesARowOnItsSettlement(unittest.TestCase):
    """`_us_walk_rows`, the cost timeline on a settlement basis: each row is
    dated its settlement date, so a purchase traded Dec 30 that settles in
    January is not held on Dec 31 (core: `str(tx.date_settle or
    _tl_d)[:10]`)."""

    @rule(R.US_RPT_13)
    def test_a_january_settlement_is_not_held_at_the_year_end(self):
        book = [tx('BUYSELL', '2025-06-02', 'ZZQ', 10, 100.0, account='b'),
                tx('BUYSELL', '2025-12-30', 'ZZQ', 5, 60.0, account='b',
                   settle='2026-01-02'),
                tx('BUYSELL', '2026-01-05', 'ZZQ', 1, 12.0, account='b')]
        res = _engine(book, cost_timeline='settle')
        [held] = inventory_on(res['cost_timeline'], '2025-12-31')
        self.assertEqual((held['qty'], held['total_cost']), (10.0, 100.0))
        [held] = inventory_on(res['cost_timeline'], '2026-01-02')
        self.assertEqual((held['qty'], held['total_cost']), (15.0, 160.0))


class TestSpinoffAdjustNotBookedWithItsSharesStillApplies(unittest.TestCase):
    """`_us_walk_rows`, the skip of a spin-off's parent ADJUST: only one the
    spin-off booked, or a $0 one, is skipped; a §355 ADJUST of real money
    the spin-off did not book still lowers the parent's basis (core:
    `abs(tx.net_amount or 0.0) < 0.005`)."""

    @rule(R.US_CORP_07)
    def test_the_parent_gives_up_the_allocated_basis(self):
        book = [tx('BUYSELL', '2025-02-03', 'ZZQ', 10, 100.0, account='b'),
                tx('ADJUST', '2025-03-20', 'ZZQ', 0, -20.0, account='b',
                   type=SPINOFF_355_TYPE)]
        [lot] = _lots(_engine(book), 'b', 'ZZQ')
        self.assertEqual((lot['qty'], lot['basis']), (10.0, 80.0))


class TestZeroUnitRowIsNotDust(unittest.TestCase):
    """`_us_walk_rows`, the rows under 1e-08 units: only a row with units
    (a trade of a sliver) is named as unbooked dust; a 0-unit, $0 row is
    no trade at all (core: `if tx.quantity and tx.action in ...`). The
    warning is the branch's only output."""

    @rule(R.US_CRYPTO_08)
    def test_no_dust_warning(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', 0, 0.0, account='b',
                   price=0.0)]
        res = _engine(book)
        self.assertNotIn('smaller than 1e-08 units', res['_stderr'])
        [lot] = _lots(res, 'b', 'ZZQ')
        self.assertEqual((lot['qty'], lot['basis']), (10.0, 100.0))


class TestFuturesFlagCarriesTheLoss(unittest.TestCase):
    """`_us_flag_futures_loss`, a §1256 contract's loss with a re-purchase
    in the window: never disallowed, flagged with the loss it would have
    been, summed over the lots the sale closed (core:
    `rec['loss_amount'] + loss_amt`)."""

    @rule(R.US_WASH_18)
    def test_the_flag_sums_both_lots_losses(self):
        sym = 'SPX260116C00100000'
        # Two contracts bought for 100 and 80, sold together for 100
        # (50 each): -50 and -30; one bought again two days later.
        book = [tx('BUYSELL', '2025-01-06', sym, 1, 100.0, account='b'),
                tx('BUYSELL', '2025-01-07', sym, 1, 80.0, account='b'),
                tx('BUYSELL', '2025-03-03', sym, -2, 100.0, account='b'),
                tx('BUYSELL', '2025-03-05', sym, 1, 100.0, account='b')]
        res = _engine(book)
        sale = [t for t in res['transactions'] if t.get('qty')]
        self.assertEqual([round(t['gain'], 6) for t in sale], [-50.0, -30.0])
        self.assertEqual([t['disallowed_amount'] for t in sale], [0.0, 0.0])
        [flag] = res['option_replacement_warnings']
        self.assertEqual(flag['rule'], 'futures_vs_loss')
        self.assertAlmostEqual(flag['loss_amount'], -80.0, places=6)


class TestSettleYearEndClosesASaleSettlingDec31(unittest.TestCase):
    """`_us_row_after`, a tax_date = "settle" year end: it ends before the
    first row that settles AFTER Dec 31; a sale traded Dec 29 that settles
    Dec 31 is in the year, its shares gone from the year-end lots (core:
    `str(_t.date_settle or _t.date or '')[:10] > day`)."""

    @rule(R.US_RPT_13)
    def test_the_sold_shares_are_not_held(self):
        book = [tx('BUYSELL', '2025-06-02', 'ZZQ', 10, 100.0, account='b'),
                tx('BUYSELL', '2025-12-29', 'ZZQ', -4, 48.0, account='b',
                   settle='2025-12-31'),
                tx('BUYSELL', '2026-01-05', 'ZZQ', 1, 12.0, account='b')]
        res = _engine(book, lots_at=None, year_end='2025-12-31',
                      year_end_basis='settle')
        [held] = res['inventory_year_end']
        self.assertEqual((held['qty'], held['total_cost']), (6.0, 60.0))


class TestCompensationOverASplitCountsPayDateShares(unittest.TestCase):
    """`_us_plan_paid_pil`, a short covered in two parts, a split between
    them: each cover's shares are counted in the pay date's units, so each
    half of the short carries half the payment (core:
    `_rep_units_factor(...) or 1.0`)."""

    @rule(R.US_INC_04, R.US_CORP_01)
    def test_each_cover_carries_half(self):
        # 100 short for 1,000; 20 paid on 2025-01-15; 50 covered for 450
        # before a 2:1 split, the other 50 (100 after it) for 450 after:
        # both within 45 days, 10 of the 20 in each cover's cost (§263(h)).
        book = [tx('BUYSELL', '2025-01-06', 'ZQSH', -100, 1000.0,
                   account='b'),
                _paid('2025-01-15', 'ZQSH', -20.0),
                tx('BUYSELL', '2025-01-17', 'ZQSH', 50, 450.0, account='b'),
                tx('SPLIT', '2025-01-20', 'ZQSH', 2, account='b'),
                tx('BUYSELL', '2025-01-21', 'ZQSH', 100, 450.0, account='b')]
        res = _engine(book)
        self.assertEqual(
            [(c['date'], c['qty'], round(c['gain'], 6),
              round(c['capitalized_263h'], 6)) for c in _covers(res)],
            [('2025-01-17', 50.0, 40.0, 10.0),
             ('2025-01-21', 100.0, 40.0, 10.0)])
        self.assertAlmostEqual(_pil(res)['pil'], 0.0, places=6)


class TestCompensationFallsOnAShortOpenedOnThePayDate(unittest.TestCase):
    """`_us_plan_paid_pil`, which shares owe the payment: those short over
    the ex-dividend date the row states (core: `opened < ex and (closed
    is None or ex <= closed)`) — a short sold on or after the ex-date
    owes none (the owner's ruling on backlog TJ-2709, 2026-11-11: the
    ex-date where taxjson knows it, else the pay date with a note). With
    no ex-date every share short on the pay date shares it, a short sold
    that day included (core: `opened <= pd_`), and the run says so."""

    NOTE = 'states no ex-dividend date'

    @staticmethod
    def _book(**facts):
        # 50 short on 2025-01-06 and 50 more on 2025-01-15, the pay date
        # of 20; the first 50 covered on 2025-01-21, within 45 days.
        return [tx('BUYSELL', '2025-01-06', 'ZQSH', -50, 500.0, account='b'),
                tx('BUYSELL', '2025-01-15', 'ZQSH', -50, 500.0, account='b',
                   time='09:00:00'),
                _paid('2025-01-15', 'ZQSH', -20.0, **facts),
                tx('BUYSELL', '2025-01-21', 'ZQSH', 50, 450.0, account='b'),
                tx('BUYSELL', '2025-06-02', 'ZZOTHER', 1, 10.0, account='b')]

    @rule(R.US_INC_04)
    def test_with_an_ex_date_the_short_sold_after_it_owes_nothing(self):
        # Ex-date 2025-01-10: only the first 50 were short over it, so
        # the whole 20 is their cover's cost (before the ruling: 10, and
        # 10 paid compensation on the short sold on the pay date).
        res = _engine(self._book(ex_date='2025-01-10'))
        [cover] = _covers(res)
        self.assertAlmostEqual(cover['capitalized_263h'], 20.0, places=6)
        self.assertAlmostEqual(cover['gain'], 30.0, places=6)
        self.assertAlmostEqual(_pil(res)['pil'], 0.0, places=6)
        self.assertNotIn(self.NOTE, res['_stderr'])

    @rule(R.US_INC_04)
    def test_a_short_sold_on_the_ex_date_owes_nothing(self):
        # Ex-date 2025-01-06, the first short's own day: no share was
        # short before it, so the payment is no cover's cost. The account
        # was short on the pay date: it stays compensation paid on a
        # short (no income reduction), all 20 of it.
        res = _engine(self._book(ex_date='2025-01-06'))
        [cover] = _covers(res)
        self.assertNotIn('capitalized_263h', cover)
        self.assertAlmostEqual(cover['gain'], 50.0, places=6)
        self.assertAlmostEqual(_pil(res)['pil'], -20.0, places=6)
        self.assertIs(_pil(res).get('paid_on_short'), True)
        # The row contradicts itself: said once (backlog TJ-3431).
        self.assertEqual(res['_stderr'].count(
            'states the ex-dividend date 2025-01-06, but no short of b '
            'was open over it: kept as compensation paid on a short, no '
            "cover's cost; check the row"), 1)
        self.assertNotIn(self.NOTE, res['_stderr'])

    @rule(R.US_INC_04)
    def test_an_ex_date_after_the_pay_date_is_ignored_and_said(self):
        # The pay date decides, as with no ex-date, and the run says the
        # date was ignored and why, not that the row states none
        # (backlog TJ-3431).
        res = _engine(self._book(ex_date='2025-01-25'))
        [cover] = _covers(res)
        self.assertAlmostEqual(cover['capitalized_263h'], 10.0, places=6)
        self.assertEqual(res['_stderr'].count(
            'states the ex-dividend date 2025-01-25, after the pay date: '
            'ignored'), 1)
        self.assertNotIn(self.NOTE, res['_stderr'])

    @rule(R.US_INC_04)
    def test_a_standing_short_gets_one_line_a_run_not_a_note_a_payment(self):
        # A short sold on 2024-06-03 and covered on 2025-06-02: no short
        # sold or covered in the 60 days before either payment, so an
        # ex-date could not change the split. One line with the count,
        # no note per payment (backlog TJ-3432).
        book = [tx('BUYSELL', '2024-06-03', 'ZQSH', -50, 500.0, account='b'),
                _paid('2025-01-15', 'ZQSH', -20.0),
                _paid('2025-04-15', 'ZQSH', -20.0),
                tx('BUYSELL', '2025-06-02', 'ZQSH', 50, 450.0, account='b')]
        res = _engine(book)
        self.assertNotIn(self.NOTE, res['_stderr'])
        self.assertEqual(res['_stderr'].count(
            '2 payment(s) of dividend compensation on a short state no '
            'ex-dividend date'), 1)

    @rule(R.US_INC_04)
    def test_a_short_covered_before_the_ex_date_owes_nothing(self):
        # The first 50 covered on 2025-01-08, before the 2025-01-10
        # ex-date, and no other short before it: nothing capitalized.
        book = self._book(ex_date='2025-01-10')
        book[3] = tx('BUYSELL', '2025-01-08', 'ZQSH', 50, 450.0, account='b')
        res = _engine(book)
        self.assertNotIn('capitalized_263h', _covers(res)[0])

    @rule(R.US_INC_04)
    def test_a_short_covered_between_the_ex_date_and_the_pay_date_owes(self):
        # The first 50 covered on 2025-01-13: short over the 2025-01-10
        # ex-date, closed before the pay date (which the pay-date key
        # missed). The whole 20 is their cover's cost.
        book = self._book(ex_date='2025-01-10')
        book[3] = tx('BUYSELL', '2025-01-13', 'ZQSH', 50, 450.0, account='b')
        book.sort(key=lambda t: (t.date, t.time or ''))
        res = _engine(book)
        [cover] = _covers(res)
        self.assertAlmostEqual(cover['capitalized_263h'], 20.0, places=6)
        self.assertAlmostEqual(cover['gain'], 30.0, places=6)

    @rule(R.US_INC_04)
    def test_with_no_ex_date_the_covered_half_carries_half(self):
        # No ex-date: the pay date decides, and both 50s were short on
        # it. 10 is the covered 50's cover cost; the other 10 is on the
        # short still open after its 45th day: paid compensation. The
        # run names the payment once.
        res = _engine(self._book())
        [cover] = _covers(res)
        self.assertAlmostEqual(cover['capitalized_263h'], 10.0, places=6)
        self.assertAlmostEqual(cover['gain'], 40.0, places=6)
        self.assertAlmostEqual(_pil(res)['pil'], -10.0, places=6)
        self.assertEqual(res['_stderr'].count(self.NOTE), 1)


class TestUnsettledCompensationEndsOnTheFortyFifthDay(unittest.TestCase):
    """`_us_plan_paid_pil`, a short still open at the last row: its part is
    unsettled only while its 45th day is after the last row; one whose
    45th day IS the last row stayed open past it, its part paid
    compensation (core: `gap < 45`); the part is the payment's share by
    quantity (core: `paid * q / total`). The warning is the only output."""

    MARK = 'dividend compensation not settled'

    @staticmethod
    def _unsettled(res):
        return re.findall(r'ZRS: ([0-9.,]+) of the dividend compensation '
                          r'paid on 2024-12-20 is on a short sale of '
                          r'(\d{4}-\d\d-\d\d)', res['_stderr'])

    @rule(R.US_INC_04)
    def test_only_the_later_short_is_unsettled(self):
        # 30 short on 2024-12-16 (45th day: 2025-01-30, the last row) and
        # 10 on 2024-12-18 (45th day: 2025-02-01); 15 paid on 2024-12-20,
        # 3.75 of it on the 10.
        book = [tx('BUYSELL', '2024-12-16', 'ZRS', -30, 2999.0, account='b'),
                tx('BUYSELL', '2024-12-18', 'ZRS', -10, 999.0, account='b'),
                _paid('2024-12-20', 'ZRS', -15.0),
                tx('BUYSELL', '2025-01-30', 'ZZOTHER', 1, 10.0, account='b')]
        res = _engine(book)
        self.assertEqual(self._unsettled(res), [('3.75', '2024-12-18')])
        self.assertIn('Covered by 2025-02-01', res['_stderr'])
        self.assertAlmostEqual(_pil(res)['pil'], -15.0, places=6)

    @rule(R.US_INC_04)
    def test_a_day_earlier_both_are(self):
        book = [tx('BUYSELL', '2024-12-16', 'ZRS', -30, 2999.0, account='b'),
                tx('BUYSELL', '2024-12-18', 'ZRS', -10, 999.0, account='b'),
                _paid('2024-12-20', 'ZRS', -15.0),
                tx('BUYSELL', '2025-01-29', 'ZZOTHER', 1, 10.0, account='b')]
        self.assertEqual(self._unsettled(_engine(book)),
                         [('11.25', '2024-12-16'), ('3.75', '2024-12-18')])


class TestCoverOnTheFortyFifthDayTakesThePayment(unittest.TestCase):
    """`_us_plan_paid_pil`, a cover on the 45th day after the short sale:
    closed "on or before the 45th day" (§263(h)(1)), the payment is its
    cost; a day later it is not (core: `gap > 45`)."""

    def _book(self, cover_date):
        return [tx('BUYSELL', '2025-01-06', 'ZQSH', -100, 1000.0,
                   account='b'),
                _paid('2025-01-15', 'ZQSH', -20.0),
                tx('BUYSELL', cover_date, 'ZQSH', 100, 900.0, account='b')]

    @rule(R.US_INC_04)
    def test_on_the_45th_day(self):
        res = _engine(self._book('2025-02-20'))
        [cover] = _covers(res)
        self.assertAlmostEqual(cover['capitalized_263h'], 20.0, places=6)
        self.assertAlmostEqual(cover['gain'], 80.0, places=6)
        self.assertAlmostEqual(_pil(res)['pil'], 0.0, places=6)

    @rule(R.US_INC_04)
    def test_on_the_46th_day(self):
        res = _engine(self._book('2025-02-21'))
        [cover] = _covers(res)
        self.assertNotIn('capitalized_263h', cover)
        self.assertAlmostEqual(cover['gain'], 100.0, places=6)
        self.assertAlmostEqual(_pil(res)['pil'], -20.0, places=6)


class TestLotRowsCarryUnitsAndTheTackedStart(unittest.TestCase):
    """`_us_lot_rows`, a long lot's row: its units, and the holding period
    a wash sale tacked on (core: `sign * l['qty']`,
    `l.get('effective_acq_date') or l['date']`)."""

    @rule(R.US_WASH_09, R.US_WASH_10)
    def test_the_replacement_lot(self):
        # 10 bought for 500 on 2025-01-02, sold for 400 on 2025-02-03 (32
        # days held), 10 bought back for 400 on 2025-02-10: basis 500, the
        # 100 denied in it, held from 2025-01-09.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 500.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'ZZQ', -10, 400.0, account='b'),
                tx('BUYSELL', '2025-02-10', 'ZZQ', 10, 400.0, account='b')]
        self.assertEqual(_lots(_engine(book), 'b', 'ZZQ'), [{
            'account': 'b', 'symbol': 'ZZQ', 'date': '2025-02-10',
            'holding_start': '2025-01-09', 'qty': 10.0, 'basis': 500.0,
            'deferred': 100.0}])


class TestStockDividendGrowsTheScannedPosition(unittest.TestCase):
    """`_us_scan_events`, a stock dividend on shares held: no purchase, but
    the position the replacement scan tracks grows by it, so a later
    sale of every share closes the long and a rebuy opens one again
    (core: `prev + ev.quantity`)."""

    @rule(R.US_STKDIV_01, R.US_WASH_01)
    def test_the_rebuy_replaces_the_loss(self):
        # 10 for 1,000; 10 more as a stock dividend (basis 50 a share);
        # all 20 sold for 800 (a loss of 200) and 20 bought back for 800
        # a week later: 200 denied, the new lot's basis 1,000, held from
        # 2025-01-09 (60 days tacked, §1223(5) then §1223(3)).
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'ZZQ', 10, 0.0, account='b',
                   type='stock_dividend', ex_date='2025-01-27'),
                tx('BUYSELL', '2025-03-03', 'ZZQ', -20, 800.0, account='b'),
                tx('BUYSELL', '2025-03-10', 'ZZQ', 20, 800.0, account='b')]
        res = _engine(book)
        [sale] = [t for t in res['transactions'] if t.get('qty')]
        self.assertAlmostEqual(sale['raw_gain'], -200.0, places=6)
        self.assertAlmostEqual(sale['disallowed_amount'], 200.0, places=6)
        [lot] = _lots(res, 'b', 'ZZQ')
        self.assertEqual((lot['qty'], lot['basis'], lot['holding_start']),
                         (20.0, 1000.0, '2025-01-09'))


class TestPaymentInLieuKeepsItsSettleDate(unittest.TestCase):
    """`_us_dividend_entries`, a payment-in-lieu entry: it carries the
    row's settlement date, its trade date only when it has none (core:
    `tx.date_settle or tx.date`)."""

    @rule(R.US_INC_01)
    def test_the_entry_dates(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 100, 1000.0, account='b'),
                tx('DIVIDEND_IN_LIEU', '2025-03-14', 'ZZQ', 100, 12.0,
                   gross_amount=12.0, type='dividend_in_lieu', account='b',
                   settle='2025-03-17')]
        p = _pil(_engine(book))
        self.assertEqual((p['date'], p['date_settle'], p['pil']),
                         ('2025-03-14', '2025-03-17', 12.0))


class TestOneCentOfDustMoneyIsAConservationGap(unittest.TestCase):
    """`USATaxRules._finish`, the rows under 1e-08 units the engine does
    not book: one carrying a cent or more is named on the conservation
    channel (core: `v >= 0.01`). The warning is the only output."""

    @rule(R.US_CRYPTO_08)
    def test_a_cent_is_named(self):
        book = [tx('BUYSELL', '2025-01-02', 'QZC', 1, 3000.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'QZC', 5e-09, 0.01, account='b')]
        res = _engine(book)
        self.assertIn('smaller than 1e-08 units', res['_stderr'])
        self.assertRegex(res['_stderr'],
                         r'warning: conservation: rows under 1e-08 units '
                         r'carrying money .*: QZC 0\.01 ')

    @rule(R.US_CRYPTO_08)
    def test_less_than_a_cent_is_not(self):
        book = [tx('BUYSELL', '2025-01-02', 'QZC', 1, 3000.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'QZC', 5e-09, 0.009,
                   account='b')]
        res = _engine(book)
        self.assertIn('smaller than 1e-08 units', res['_stderr'])
        self.assertNotIn('conservation', res['_stderr'])


if __name__ == '__main__':
    unittest.main()
