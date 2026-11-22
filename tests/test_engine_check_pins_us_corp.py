"""US engine corporate actions: decisions the suite ran but did not check
(TJ-1896, the mutation triage of 2026-11-09). Each test runs one branch of
`_us_walk_stock_dividend`, `_us_walk_spinoff` or `_us_walk_reorg` on a book
where the branch's decision moves a figure (or, where no figure can move,
the line the run prints), and pins it. Synthetic data only; tickers (QZRX,
QZSP, QZNW) and accounts ('b', 'c') are made up."""
import dataclasses
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R

from taxjson.lib.row_types import LOT_MOVE_TYPE
from _support.us_corp_engine import _engine, _spin_rows, _boot_rows


def _lots(res, account, symbol):
    return [(lot['date'], round(lot['qty'], 9), round(lot['basis'], 6))
            for lot in res['lots_at']
            if lot['account'] == account and lot['symbol'] == symbol]


def _sales(res, date):
    return [t for t in res['transactions']
            if t['date'] == date and t.get('qty')]


def _stkdiv(date, qty, time='16:00:00', **kw):
    """A parser's stock-dividend row: `qty` new QZRX at $0 in 'b'."""
    return tx('BUYSELL', date, 'QZRX', qty, 0.0, account='b',
              type='stock_dividend', time=time, **kw)


def _move_legs(date, qty, src, dst):
    """An own-account move of `qty` QZRX from `src` to `dst`."""
    desc = f"own-account move #1: {src} -> {dst} (test)"
    return [tx('TRANSFER', date, 'QZRX', -qty, 0.0, account=src,
               type=LOT_MOVE_TYPE, description=desc),
            tx('TRANSFER', date, 'QZRX', qty, 0.0, account=dst,
               type=LOT_MOVE_TYPE, description=desc)]


# ============================================== _us_walk_stock_dividend


class TestStockDividendLotBoughtOnTheExDate(unittest.TestCase):
    """`_us_walk_stock_dividend`, the entitled lots: a lot bought ON the
    ex-dividend date is not entitled and keeps its own shares and basis
    (core: `(l.get('date') or '') < _ent_date`)."""

    @rule(R.US_STKDIV_01)
    def test_only_the_older_lot_grows(self):
        # 10 bought 2025-01-02 for 400 and 10 on the ex-date for 500; a
        # 1-for-10 stock dividend paid 2025-05-15 on the first 10: they
        # become 11 at 400; the ex-date lot stays 10 at 500.
        res = _engine([
            tx('BUYSELL', '2025-01-02', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-05-01', 'QZRX', 10, 500.0, account='b'),
            _stkdiv('2025-05-15', 1, ex_date='2025-05-01')])
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2025-01-02', 11.0, 400.0),
                          ('2025-05-01', 10.0, 500.0)])


class TestStockDividendOnSharesSoldAfterTheExDate(unittest.TestCase):
    """`_us_walk_stock_dividend`, entitled shares sold between the ex-date
    and the pay date (TJ-0498): only the shares bought BEFORE the ex-date
    are entitled (core: `(e.get('acquired_date') or '') < _ent_date`), and
    a sale's shares are counted in the pay date's units from the SALE's
    date (core: `e.get('date') or ''`)."""

    @rule(R.US_STKDIV_05)
    def test_shares_bought_on_the_ex_date_keep_their_cost(self):
        # 10 bought 2025-01-02 for 400 and 10 on the 2025-05-01 ex-date
        # for 500, all 20 sold 2025-05-05; 1 new share paid 2025-05-15 on
        # the first 10 (ratio 1.1): that sale's cost falls to 400/1.1, the
        # new share carries 400/11 with the lot's date; the ex-date lot's
        # sale keeps its 500.
        res = _engine([
            tx('BUYSELL', '2025-01-02', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-05-01', 'QZRX', 10, 500.0, account='b'),
            tx('BUYSELL', '2025-05-05', 'QZRX', -20, 1100.0, account='b'),
            _stkdiv('2025-05-15', 1, ex_date='2025-05-01')])
        sales = sorted((s['acquired_date'], s['qty'], round(s['cost'], 6))
                       for s in _sales(res, '2025-05-05'))
        self.assertEqual(sales, [('2025-01-02', 10, round(400 / 1.1, 6)),
                                 ('2025-05-01', 10, 500.0)])
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2025-01-02', 1.0, round(400 / 11, 6))])

    @rule(R.US_STKDIV_05, R.US_CORP_01)
    def test_a_split_before_the_sale_is_not_counted_again(self):
        # 10 bought 2024-06-03 for 400, split 2:1 on 2025-02-03 (20
        # shares); all 20 sold 2025-05-05, after the 2025-05-01 ex-date; 2
        # new shares paid 2025-05-15 (ratio 22/20 = 1.1): the sale's cost
        # falls to 400/1.1, the new shares carry 400/11. The split is in
        # the sale's own units already.
        res = _engine([
            tx('BUYSELL', '2024-06-03', 'QZRX', 10, 400.0, account='b'),
            tx('SPLIT', '2025-02-03', 'QZRX', 2.0, 0.0, account='b'),
            tx('BUYSELL', '2025-05-05', 'QZRX', -20, 1100.0, account='b'),
            _stkdiv('2025-05-15', 2, ex_date='2025-05-01')])
        [sale] = _sales(res, '2025-05-05')
        self.assertAlmostEqual(sale['cost'], 400 / 1.1, places=6)
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2024-06-03', 2.0, round(400 / 11, 6))])


class TestStockDividendWithNoDateNamesRecentLots(unittest.TestCase):
    """`_us_walk_stock_dividend`, a row with no ex-dividend or record date:
    the basis is spread over every lot held, and a lot bought within the
    90 days before the pay date is named, when there is more than one lot
    (core: `(_day_gap(...) or 999) <= 90`, `len(_lots) > 1`)."""

    @rule(R.US_STKDIV_04)
    def test_a_lot_bought_90_days_before_is_named(self):
        # 2025-02-14 is 90 days before the 2025-05-15 pay date.
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-02-14', 'QZRX', 10, 500.0, account='b'),
            _stkdiv('2025-05-15', 2)])
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2024-01-02', 11.0, 400.0),
                          ('2025-02-14', 11.0, 500.0)])
        self.assertIn('including 10 share(s) bought on 2025-02-14, within '
                      '90 days before it', res['_stderr'])

    @rule(R.US_STKDIV_04)
    def test_a_lot_bought_on_the_pay_date_is_named(self):
        # 0 days before it (TJ-2705): `_day_gap` 0 read as no date at all.
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-05-15', 'QZRX', 10, 500.0, account='b',
               time='09:00:00'),
            _stkdiv('2025-05-15', 2)])
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2024-01-02', 11.0, 400.0),
                          ('2025-05-15', 11.0, 500.0)])
        self.assertIn('including 10 share(s) bought on 2025-05-15, within '
                      '90 days before it', res['_stderr'])

    @rule(R.US_STKDIV_04)
    def test_a_single_lot_is_not_named(self):
        # One lot: if it was not entitled, nothing held was.
        res = _engine([
            tx('BUYSELL', '2025-04-15', 'QZRX', 10, 500.0, account='b'),
            _stkdiv('2025-05-15', 1)])
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2025-04-15', 11.0, 500.0)])
        self.assertNotIn('within 90 days before it', res['_stderr'])


class TestStockDividendWithNoSharesHeld(unittest.TestCase):
    """`_us_walk_stock_dividend`, new shares with none held and no date on
    the row: booked as a $0 purchase, and the warning says whether the
    shares were sold before the pay date (a sale on or before the row's
    moment: core `_p.quantity < 0`, `(_p.date, _p.time or '') <=
    (tx.date, tx.time or '')`) or the history is missing."""

    SOLD = 'the shares were disposed of before the pay date'
    MISSING = 'add the missing purchase history'

    def _run(self, *rows):
        res = _engine(list(rows))
        return res, res['_stderr']

    @rule(R.US_STKDIV_03)
    def test_a_sale_at_the_same_moment_is_before_it(self):
        _res, err = self._run(
            tx('BUYSELL', '2025-01-02', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-05-15', 'QZRX', -10, 450.0, account='b',
               time='16:00:00'),
            _stkdiv('2025-05-15', 1))
        self.assertIn(self.SOLD, err)
        self.assertNotIn(self.MISSING, err)

    @rule(R.US_STKDIV_03)
    def test_a_sale_earlier_on_the_pay_date_is_before_it(self):
        res, err = self._run(
            tx('BUYSELL', '2025-01-02', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-05-15', 'QZRX', -10, 450.0, account='b',
               time='09:00:00'),
            _stkdiv('2025-05-15', 1))
        self.assertIn(self.SOLD, err)
        self.assertEqual(_lots(res, 'b', 'QZRX'), [('2025-05-15', 1.0, 0.0)])

    @rule(R.US_STKDIV_03)
    def test_a_sale_later_on_the_pay_date_is_not(self):
        # The history is missing; the new share is sold that afternoon.
        res, err = self._run(
            _stkdiv('2025-05-15', 1, time='09:00:00'),
            tx('BUYSELL', '2025-05-15', 'QZRX', -1, 45.0, account='b',
               time='16:00:00'))
        self.assertIn(self.MISSING, err)
        self.assertNotIn(self.SOLD, err)
        [sale] = _sales(res, '2025-05-15')
        self.assertAlmostEqual(sale['gain'], 45.0, places=6)

    @rule(R.US_STKDIV_03)
    def test_a_zero_quantity_row_is_not_a_sale(self):
        # A broker row of 0 shares (an explicit 0 is legal) sells nothing.
        res, err = self._run(
            tx('BUYSELL', '2025-03-03', 'QZRX', 0, 0.0, account='b'),
            _stkdiv('2025-05-15', 1))
        self.assertIn(self.MISSING, err)
        self.assertNotIn(self.SOLD, err)
        self.assertEqual(_lots(res, 'b', 'QZRX'), [('2025-05-15', 1.0, 0.0)])


# ===================================================== _us_walk_spinoff


class TestSpinoffLotBoughtOnTheRecordDate(unittest.TestCase):
    """`_us_walk_spinoff`, the late lots: a parent lot bought ON the record
    date is not entitled when the shares paid on agree with those held
    before it, and keeps its own basis (core: `(l.get('date') or '') >=
    _ent_d`)."""

    @rule(R.US_CORP_07)
    def test_only_the_older_lot_gives_up_basis(self):
        # 10 QZRX bought 2024-01-02 for 400 and 10 on the 2025-03-20
        # record date for 600; 5 QZSP paid on 10 shares, 100 allocated:
        # the old lot gives up 100 (a fraction of 0.25) to one block of 5
        # with its date; the record-date lot keeps its 600.
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-03-20', 'QZRX', 10, 600.0, account='b')]
            + _spin_rows(100.0, 5.0, record='2025-03-20'))
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2024-01-02', 10.0, 300.0),
                          ('2025-03-20', 10.0, 600.0)])
        self.assertEqual(_lots(res, 'b', 'QZSP'),
                         [('2024-01-02', 5.0, 100.0)])


class TestSpinoffDueBillSaleOfALateLot(unittest.TestCase):
    """`_us_walk_spinoff`, a due bill (the shares paid on are those held on
    the pay date): a sale between the dates is named as one of the shares
    held before the record date only when its shares were bought before
    it (core: `(e.get('acquired_date') or '') < _ent_d`). Reached when the
    older lot arrives after the sale, by an own-account move."""

    @rule(R.US_CORP_07, R.US_BASIS_05)
    def test_a_sale_of_shares_bought_on_the_record_date_is_not_named(self):
        # c holds 10 from 2024-01-02 (400). b buys 10 on the 2025-03-20
        # record date (600), sells them 2025-03-24, buys 10 on 2025-03-25
        # (700); c's 10 move to b on 2025-03-27. 10 QZSP paid on 20 shares
        # (those held on the pay date: a due bill), 110 allocated: each lot
        # gives up 10%.
        res = _engine(
            [tx('BUYSELL', '2024-01-02', 'QZRX', 10, 400.0, account='c'),
             tx('BUYSELL', '2025-03-20', 'QZRX', 10, 600.0, account='b'),
             tx('BUYSELL', '2025-03-24', 'QZRX', -10, 650.0, account='b'),
             tx('BUYSELL', '2025-03-25', 'QZRX', 10, 700.0, account='b')]
            + _move_legs('2025-03-27', 10, 'c', 'b')
            + _spin_rows(110.0, 10.0, record='2025-03-20'))
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2024-01-02', 10.0, 360.0),
                          ('2025-03-25', 10.0, 630.0)])
        self.assertEqual(_lots(res, 'b', 'QZSP'),
                         [('2024-01-02', 5.0, 40.0),
                          ('2025-03-25', 5.0, 70.0)])
        self.assertIn('came with a due bill and share the basis',
                      res['_stderr'])
        self.assertNotIn('were sold before it', res['_stderr'])


class TestSpinoffFromAZeroBasisParent(unittest.TestCase):
    """`_us_walk_spinoff`, a parent whose basis a return of capital took:
    an allocation is capped at the zero basis, with an ATTENTION line, and
    the blocks follow the parent's blocks at $0 by quantity (core:
    `_pbasis > 0 and _alloc > 0`, `_pbasis <= 0 and _alloc > 0`,
    `_pbasis * _frac > 0`); a $0 allocation caps nothing and says so."""

    BOOK = [tx('BUYSELL', '2023-01-10', 'QZRX', 4, 200.0, account='b'),
            tx('BUYSELL', '2023-06-12', 'QZRX', 6, 300.0, account='b'),
            tx('ADJUST', '2024-06-03', 'QZRX', 0, -500.0, account='b',
               type='roc')]

    @rule(R.US_CORP_07, R.US_ROC_01)
    def test_an_allocation_is_capped_at_zero(self):
        res = _engine(self.BOOK + _spin_rows(100.0, 5.0))
        self.assertEqual(_lots(res, 'b', 'QZRX'),
                         [('2023-01-10', 4.0, 0.0), ('2023-06-12', 6.0, 0.0)])
        self.assertEqual(_lots(res, 'b', 'QZSP'),
                         [('2023-01-10', 2.0, 0.0), ('2023-06-12', 3.0, 0.0)])
        self.assertEqual(res['transactions'], [])
        self.assertIn('capped at the basis', res['_stderr'])

    @rule(R.US_CORP_07, R.US_ROC_01)
    def test_a_zero_allocation_is_not_capped(self):
        res = _engine(self.BOOK + _spin_rows(0.0, 5.0))
        self.assertEqual(_lots(res, 'b', 'QZSP'),
                         [('2023-01-10', 2.0, 0.0), ('2023-06-12', 3.0, 0.0)])
        self.assertNotIn('capped at the basis', res['_stderr'])


class TestSpinoffAllocatingTheWholeBasis(unittest.TestCase):
    """`_us_walk_spinoff`, an allocation equal to the parent's basis (a
    fraction of exactly 1): every dollar moves, nothing is over the basis,
    so nothing is capped (core: `if _frac > 1`)."""

    @rule(R.US_CORP_07)
    def test_the_whole_basis_moves_without_a_cap(self):
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 10, 400.0, account='b')]
            + _spin_rows(400.0, 5.0))
        self.assertEqual(_lots(res, 'b', 'QZRX'), [('2024-01-02', 10.0, 0.0)])
        self.assertEqual(_lots(res, 'b', 'QZSP'),
                         [('2024-01-02', 5.0, 400.0)])
        self.assertNotIn('capped at the basis', res['_stderr'])


class TestSpinoffBlocksCarryTheirShareOfTheWashAdd(unittest.TestCase):
    """`_us_walk_spinoff`, parent blocks of which one carries a §1091(d)
    add: each block of spun-off shares takes the fraction of its parent
    block's add (core: `_wd * _frac`), the parent keeps the rest."""

    @rule(R.US_CORP_07, R.US_WASH_09, R.US_WASH_10)
    def test_the_add_splits_by_the_fraction(self):
        # b's 10 bought 2025-01-02 for 500, sold 2025-02-03 for 400 and
        # rebought 2025-02-10 for 400: the replacement's basis is 500 (100
        # of it the disallowed loss). A second lot: 10 bought 2025-02-20
        # for 500. 250 allocated over 1,000 (a fraction of 0.25): the
        # replacement's block takes 125 with 25 of the add.
        res = _engine([
            tx('BUYSELL', '2025-01-02', 'QZRX', 10, 500.0, account='b'),
            tx('BUYSELL', '2025-02-03', 'QZRX', -10, 400.0, account='b'),
            tx('BUYSELL', '2025-02-10', 'QZRX', 10, 400.0, account='b'),
            tx('BUYSELL', '2025-02-20', 'QZRX', 10, 500.0, account='b')]
            + _spin_rows(250.0, 10.0))
        spun = [(lot['date'], lot['qty'], round(lot['basis'], 6),
                 lot['deferred'], lot['holding_start'])
                for lot in res['lots_at'] if lot['symbol'] == 'QZSP']
        self.assertEqual(spun, [('2025-02-10', 5.0, 125.0, 25.0,
                                 '2025-01-09'),
                                ('2025-02-20', 5.0, 125.0, 0.0,
                                 '2025-02-20')])
        par = [(lot['date'], round(lot['basis'], 6), lot['deferred'])
               for lot in res['lots_at'] if lot['symbol'] == 'QZRX']
        self.assertEqual(par, [('2025-02-10', 375.0, 75.0),
                               ('2025-02-20', 375.0, 0.0)])


# ======================================================= _us_walk_reorg


class TestExchangeRecognizesTheGainUpToTheCash(unittest.TestCase):
    """`_us_walk_reorg`, the old shares' leg: one block per lot, its row's
    cost is the cash less the gain recognized (core: `_bb - _rec`), and a
    leg the books hold in full names no missing shares (core: `_q - _got >
    run.epsilon`)."""

    @rule(R.US_CORP_05)
    def test_the_rows_cost_is_the_cash_less_the_gain(self):
        # 50 QZRX bought 2024-01-02 for 2,500 go for 25 QZNW worth 2,300
        # and 300 cash: realized 2,600 - 2,500 = 100, recognized 100 (less
        # than the cash). The row: proceeds 300, cost 200, gain 100; QZNW
        # basis 2,500 - 300 + 100 = 2,300 (§358), the lot's date.
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 50, 2500.0, account='b')]
            + _boot_rows(50, 25, 300.0, 92.0))
        [row] = _sales(res, '2025-06-20')
        self.assertEqual((row['qty'], row['term'], row['tainted']),
                         (50, 'LONG_TERM', False))
        self.assertAlmostEqual(row['proceeds'], 300.0, places=6)
        self.assertAlmostEqual(row['cost'], 200.0, places=6)
        self.assertAlmostEqual(row['gain'], 100.0, places=6)
        self.assertEqual(_lots(res, 'b', 'QZNW'),
                         [('2024-01-02', 25.0, 2300.0)])
        self.assertNotIn('have no basis in the books', res['_stderr'])


class TestExchangeOfSharesTheBooksDoNotHold(unittest.TestCase):
    """`_us_walk_reorg`, a leg giving up more shares than the books hold:
    the shares missing are one block at zero basis, tainted, for manual
    reporting (core: `'qty': _q - _got`)."""

    @rule(R.US_CORP_05)
    def test_the_missing_shares_are_a_zero_basis_block(self):
        # The books hold 30 (1,500) of the 50 given for 25 QZNW worth
        # 2,600 and 300 cash. The 30 (3/5): realized 1,740 - 1,500 = 240,
        # recognized their 180 of the cash. The 20 missing (2/5): realized
        # 1,160 on no basis, recognized their 120.
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 30, 1500.0, account='b')]
            + _boot_rows(50, 25, 300.0, 104.0))
        rows = sorted((r['qty'], round(r['gain'], 6), r['tainted'])
                      for r in _sales(res, '2025-06-20'))
        self.assertEqual(rows, [(20, 120.0, True), (30, 180.0, False)])
        self.assertEqual(_lots(res, 'b', 'QZNW'),
                         [('2024-01-02', 15.0, 1500.0),
                          ('2025-06-20', 10.0, 0.0)])
        self.assertIn('have no basis in the books', res['_stderr'])


class TestExchangeWithNoCash(unittest.TestCase):
    """`_us_walk_reorg`, a §356 pair whose cash is 0 (elected with
    cash_boot=0): a block with no cash recognizes nothing and books no row
    (core: `if _bb <= 0: continue`); the basis carries over whole."""

    @rule(R.US_CORP_05)
    def test_no_row_and_the_basis_carried(self):
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 50, 2500.0, account='b')]
            + _boot_rows(50, 25, 0.0, 104.0))
        self.assertEqual(res['transactions'], [])
        self.assertEqual(_lots(res, 'b', 'QZNW'),
                         [('2024-01-02', 25.0, 2500.0)])


class TestExchangeRowKeepsItsSettleDate(unittest.TestCase):
    """`_us_walk_reorg`, the block's row: dated the leg's trade date and
    carrying its settle date when it has one (core: `tx.date_settle or
    tx.date`)."""

    @rule(R.US_CORP_05)
    def test_the_settle_date_is_the_legs(self):
        sell, buy = _boot_rows(50, 25, 300.0, 104.0)
        res = _engine([
            tx('BUYSELL', '2024-01-02', 'QZRX', 50, 2500.0, account='b'),
            dataclasses.replace(sell, date_settle='2025-06-23'), buy])
        [row] = _sales(res, '2025-06-20')
        self.assertEqual(row['date_settle'], '2025-06-23')


class TestExchangeDroppingAFraction(unittest.TestCase):
    """`_us_walk_reorg`, the new shares' leg: its value over the old leg's
    value of the new shares (whole shares only here) scales each block's
    carried basis and §1091(d) add (core: `_nb * _r`, `_wd * _r`)."""

    @rule(R.US_CORP_05, R.US_WASH_09, R.US_WASH_10)
    def test_the_whole_shares_take_their_share_of_basis_and_add(self):
        # b's 50 bought 2025-01-02 for 2,500, sold 2025-02-03 for 2,000
        # and rebought 2025-02-10 for 2,000: basis 2,500, 500 of it the
        # disallowed loss. They go on 2025-06-20 for 25.5 QZNW worth 2,652
        # and 300 cash: realized 452, recognized 300; carried basis 2,500.
        # The half share is dropped (its cash is a sale of its own): the
        # 25 whole shares carry 25/25.5 of the basis and of the add.
        res = _engine([
            tx('BUYSELL', '2025-01-02', 'QZRX', 50, 2500.0, account='b'),
            tx('BUYSELL', '2025-02-03', 'QZRX', -50, 2000.0, account='b'),
            tx('BUYSELL', '2025-02-10', 'QZRX', 50, 2000.0, account='b')]
            + _boot_rows(50, 25.5, 300.0, 104.0))
        [new] = [lot for lot in res['lots_at'] if lot['symbol'] == 'QZNW']
        self.assertAlmostEqual(new['qty'], 25.0, places=9)
        self.assertAlmostEqual(new['basis'], 2500.0 * 25 / 25.5, places=6)
        self.assertAlmostEqual(new['deferred'], round(500.0 * 25 / 25.5, 4),
                               places=4)
        self.assertEqual(new['holding_start'], '2025-01-09')


class TestExchangeLegsEnteredByHand(unittest.TestCase):
    """`_us_walk_reorg`, the scale between the legs when one side states no
    value for the new shares (two reorg_356 rows written by hand, as a JSON
    book can): the blocks' basis is carried unscaled (core: `if
    _src_value > 0 and _val > 0 else Decimal(1)`)."""

    def _legs(self, sell_net, buy_net):
        common = dict(account='b', type='reorg_356', corp_event_id='ev-qznw')
        return [tx('BUYSELL', '2024-01-02', 'QZRX', 50, 2500.0, account='b'),
                tx('BUYSELL', '2025-06-20', 'QZRX', -50, sell_net,
                   gross_amount=300.0, time='09:30:00', **common),
                tx('BUYSELL', '2025-06-20', 'QZNW', 25, buy_net,
                   time='09:30:01', **common)]

    @rule(R.US_CORP_05)
    def test_a_new_shares_row_at_zero_carries_the_basis(self):
        # Realized 2,900 - 2,500 = 400, recognized the 300 cash: carried
        # 2,500 - 300 + 300 = 2,500, whatever the new row's amount.
        res = _engine(self._legs(2900.0, 0.0))
        self.assertEqual(_lots(res, 'b', 'QZNW'),
                         [('2024-01-02', 25.0, 2500.0)])

    @rule(R.US_CORP_05)
    def test_an_old_leg_of_only_the_cash_carries_the_basis(self):
        # The amount realized stated as the cash alone: 300 - 2,500 is a
        # loss, recognized 0 (§356(c)); carried 2,500 - 300 = 2,200.
        res = _engine(self._legs(300.0, 2600.0))
        [row] = _sales(res, '2025-06-20')
        self.assertAlmostEqual(row['gain'], 0.0, places=6)
        self.assertEqual(_lots(res, 'b', 'QZNW'),
                         [('2024-01-02', 25.0, 2200.0)])


if __name__ == '__main__':
    unittest.main()
