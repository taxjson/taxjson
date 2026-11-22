"""US engine branches the suite did not reach (TJ-1896, the mutation triage
of 2026-11-09): each test runs one branch of `USATaxRules` no other test
executed and pins a figure a mutant of it changes. Synthetic data only;
tickers (QZ*, ZZQ) and accounts ('b', 'c') are made up."""
import contextlib
import io
import re
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
from _support.engine_rules import assert_rule

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.corp_actions import CorporateAction, resolve_event


def _engine(book, **kw):
    """The US engine over `book`, per-account basis, every lot at the end
    of 2025 (`lots_at`); stderr captured as `_stderr`."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = USATaxRules().compute_gains(list(book), per_account_basis=True,
                                        lots_at='2025-12-31', **kw)
    r['_stderr'] = err.getvalue()
    return r


def _lots(res, account, symbol):
    return [lot for lot in res['lots_at']
            if lot['account'] == account and lot['symbol'] == symbol]


def _sale(res, account, date):
    [row] = [t for t in res['transactions']
             if t['account'] == account and t['date'] == date
             and t.get('qty')]
    return row


def _spin_rows(alloc, qty_received, account='b'):
    """A §355 spin-off of QZSP from QZRX on 2025-04-01 (the resolver's
    BUYSELL + parent ADJUST pair)."""
    ev = CorporateAction(
        date='2025-04-01', time='09:30:00', action_type='spinoff',
        source_symbol='QZRX', source_isin='', target_symbol='QZSP',
        target_isin='', ratio_new=1, ratio_old=2, qty_disposed=0.0,
        qty_received=qty_received, fmv=0.0, currency='USD',
        target_currency='USD', account=account, event_id='ev-qzsp')
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'tax_free_355', country='usa',
                             hints={'allocated_acb': alloc})
    return [TaxTransaction(**r) for r in rows]


def _boot_rows(date, give, get, boot, fmv_per_share, account='b'):
    """A §356 exchange of `give` QZRX for `get` QZNW plus `boot` cash."""
    ev = CorporateAction(
        date=date, time='09:30:00', action_type='merger',
        source_symbol='QZRX', source_isin='', target_symbol='QZNW',
        target_isin='', ratio_new=1, ratio_old=2, qty_disposed=give,
        qty_received=get, fmv=0.0, currency='USD', target_currency='USD',
        account=account, event_id='ev-qznw')
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'reorg_368_boot', country='usa',
                             hints={'cash_boot': boot,
                                    'fmv_per_share': fmv_per_share})
    return [TaxTransaction(**r) for r in rows]


def _washed_lot(qty, cost, loss_proceeds, rebuy, symbol='QZRX'):
    """b buys `qty` for `cost` on 2025-01-02, sells them for
    `loss_proceeds` on 2025-02-03 and buys `qty` back for `rebuy` on
    2025-02-10: the rebuy is the replacement, its basis rebuy + the
    disallowed loss, its holding period from 2025-01-09 (32 days tacked)."""
    return [tx('BUYSELL', '2025-01-02', symbol, qty, cost, account='b'),
            tx('BUYSELL', '2025-02-03', symbol, -qty, loss_proceeds,
               account='b'),
            tx('BUYSELL', '2025-02-10', symbol, qty, rebuy, account='b')]


class TestPooledDeemedGainIsDatedItsEarliestLot(unittest.TestCase):
    """`_us_walk_adjust`, the pool of §301(c)(3) excesses under half a cent:
    two lots with the same holding-period start (one bought that day, a
    wash replacement whose tacked start is that day) pool into one row,
    dated the first of them; a later lot with an equal start does not
    move it (core: `if _acq < _pool[1]`)."""

    @rule(R.US_ROC_02)
    def test_tie_keeps_the_first_lot(self):
        # ZZQ 1 @10 on 2023-06-01, sold @9 on 2024-02-01 (245 days held),
        # rebought @9 on 2024-02-10: the replacement's basis is 10 and its
        # holding period starts 2023-06-10, the day the other lot was
        # bought. A return of capital of 20.006 over the 2 shares takes
        # each 0.003 past its basis of 10: 0.006 of gain, one pooled row.
        book = [tx('BUYSELL', '2023-06-01', 'ZZQ', 1, 10.0, account='b'),
                tx('BUYSELL', '2023-06-10', 'ZZQ', 1, 10.0, account='b'),
                tx('BUYSELL', '2024-02-01', 'ZZQ', -1, 9.0, account='b'),
                tx('BUYSELL', '2024-02-10', 'ZZQ', 1, 9.0, account='b'),
                tx('ADJUST', '2024-06-03', 'ZZQ', 0, -20.006, account='b',
                   type='roc')]
        res = _engine(book)
        self.assertEqual([lot['holding_start'] for lot in _lots(res, 'b', 'ZZQ')],
                         ['2023-06-10', '2023-06-10'])
        [deemed] = [t for t in res['transactions'] if t.get('deemed')]
        self.assertAlmostEqual(deemed['gain'], 0.006, places=9)
        self.assertEqual(deemed['term'], 'SHORT_TERM')
        self.assertEqual(deemed['acquired_date'], '2023-06-10')
        assert_rule(self, res, book[4].id, R.US_ROC_02, record=book[4].id)   # PLAN step 11


class TestSpinoffSplitsTheParentLotsWashAdd(unittest.TestCase):
    """`_us_walk_spinoff`, a parent lot that carries a §1091(d) add: the
    lot keeps (1 - fraction) of the add and the spun-off block takes the
    fraction (core: `_wd * (1 - _frac)`)."""

    @rule(R.US_CORP_07)
    def test_wash_add_follows_the_basis_fraction(self):
        # The replacement QZRX lot: 10 shares, basis 400 + 100 disallowed
        # = 500. The spin-off moves 100 of it (a fraction of 0.2) to 5
        # QZSP: the parent keeps 400 with 80 of the add, the block 100
        # with 20.
        res = _engine(_washed_lot(10, 500.0, 400.0, 400.0)
                      + _spin_rows(100.0, 5.0))
        [par] = _lots(res, 'b', 'QZRX')
        [spn] = _lots(res, 'b', 'QZSP')
        self.assertAlmostEqual(par['basis'], 400.0, places=6)
        self.assertAlmostEqual(par['deferred'], 80.0, places=4)
        self.assertAlmostEqual(spn['basis'], 100.0, places=6)
        self.assertAlmostEqual(spn['deferred'], 20.0, places=4)
        self.assertEqual(spn['holding_start'], '2025-01-09')


class TestZeroSpinoffFollowsEveryParentBlock(unittest.TestCase):
    """`_us_walk_spinoff`, a $0 allocation over two parent lots: no basis
    moves (fraction 0), and every block but the last takes its share of
    the new shares' (zero) total by quantity (TJ-0723). The quantity-share
    line cannot tell `* qty` from `/ qty` (the total is always 0 here: the
    resolver emits the pair from one amount and a hint is never negative),
    so this pins the blocks, not that mutant."""

    @rule(R.US_CORP_07)
    def test_blocks_at_zero_with_the_parents_dates(self):
        book = [tx('BUYSELL', '2023-01-10', 'QZRX', 5, 200.0, account='b'),
                tx('BUYSELL', '2024-03-01', 'QZRX', 15, 900.0, account='b')
                ] + _spin_rows(0.0, 10.0)
        res = _engine(book)
        self.assertEqual(
            [(lot['date'], lot['qty'], lot['basis']) for lot in _lots(res, 'b', 'QZSP')],
            [('2023-01-10', 2.5, 0.0), ('2024-03-01', 7.5, 0.0)])
        self.assertEqual(
            [lot['basis'] for lot in _lots(res, 'b', 'QZRX')], [200.0, 900.0])


class TestShortResidueFoldedIntoTheCover(unittest.TestCase):
    """`_us_walk_buy`, a buy-to-close that leaves a short residue of at
    most 1e-08 units: the residue is folded into the cover and named with
    its size (core: `short_lot['qty'] - qty_remaining`)."""

    @rule(R.US_CRYPTO_08)
    def test_residue_is_named_with_its_units(self):
        res = _engine([
            tx('BUYSELL', '2025-01-02', 'QZC', -1.000000005, 3000.0,
               account='b'),
            tx('BUYSELL', '2025-03-03', 'QZC', 1, 2000.0, account='b')])
        self.assertEqual(res['inventory'], [])
        [cover] = res['transactions']
        self.assertEqual(cover['direction'], 'SHORT')
        self.assertAlmostEqual(cover['qty'], 1.000000005, places=12)
        m = re.search(r'lot residue of at most 1e-08 units.*?'
                      r'QZC \(1 time\(s\), ([-+][0-9.e+-]+) units\)',
                      res['_stderr'])
        self.assertIsNotNone(m, res['_stderr'])
        self.assertAlmostEqual(float(m.group(1)), 5e-09, delta=1e-10)


class TestPendingMatchOvershootFoldsIntoTheLastBlock(unittest.TestCase):
    """`_us_walk_buy`, a replacement matched before its own buy with more
    matches than the shares it opens: a match left over once the opened
    shares are spent adds its §1091(d) amount to the last block (core:
    `_b0 + _pb`). Reached when the pre-pass's capacity exceeds the walk's
    opened shares: here b's folded 5e-09 residue (US-CRYPTO-08) leaves the
    pre-pass a short of 9.999999995, the walk one of 10, so the buy of 25
    opens 15 against a capacity of 15.000000005, and c's third loss
    matches the 1.3e-08 left."""

    @rule(R.US_WASH_09)
    def test_every_disallowed_amount_lands_in_a_basis(self):
        book = [
            tx('BUYSELL', '2024-06-03', 'QZC', 1.000000005, 100.0,
               account='b'),
            tx('BUYSELL', '2024-07-01', 'QZC', -1, 100.0, account='b'),
            tx('BUYSELL', '2025-02-03', 'QZC', -10, 1000.0, account='b'),
            tx('BUYSELL', '2024-01-02', 'QZC', 20, 40000.0, account='c'),
            tx('BUYSELL', '2025-03-03', 'QZC', -10, 1000.0, account='c'),
            tx('BUYSELL', '2025-03-04', 'QZC', -4.999999992, 499.9999992,
               account='c'),
            tx('BUYSELL', '2025-03-05', 'QZC', -1, 100.0, account='c'),
            tx('BUYSELL', '2025-03-10', 'QZC', 25, 2500.0, account='b')]
        res = _engine(book)
        c_rows = [t for t in res['transactions'] if t['account'] == 'c']
        self.assertEqual(len(c_rows), 3)
        # The third loss matched a sliver: the leftover match.
        self.assertGreater(c_rows[2]['disallowed_amount'], 1e-6)
        b_lots = _lots(res, 'b', 'QZC')
        self.assertEqual([lot['qty'] for lot in b_lots], [10.0, 5.0])
        # 15 of the 25 bought open long: 1,500 of the cost, plus every
        # loss c's sales were denied (§1091(d)), nothing lost or doubled.
        denied = sum(t['disallowed_amount'] for t in c_rows)
        self.assertAlmostEqual(sum(lot['basis'] for lot in b_lots),
                               1500.0 + denied, places=7)


class TestStockDividendOnASoldReplacement(unittest.TestCase):
    """`_us_allot_sold_shares`, an entitled lot that carried a §1091(d) add
    sold before the pay date: the sale gives up 1 - 1/ratio of its cost to
    the new share, and as much of the add, which the new share carries
    (core: `D(e['deferred_loss_recovered']) - wd`)."""

    @rule(R.US_STKDIV_05)
    def test_the_add_moves_with_the_cost(self):
        # The replacement ZZQ lot: 10 shares, basis 400 + 100 disallowed.
        # Sold for 600 on 2025-05-05, after the 2025-05-01 ex-date of a
        # 1-for-10 stock dividend paid 2025-05-15 (ratio 1.1): the sale
        # keeps 500/1.1 of the cost and 100/1.1 of the add; the new share
        # takes 500/11 and 100/11.
        book = _washed_lot(10, 500.0, 400.0, 400.0, symbol='ZZQ') + [
            tx('BUYSELL', '2025-05-05', 'ZZQ', -10, 600.0, account='b'),
            tx('BUYSELL', '2025-05-15', 'ZZQ', 1, 0.0, account='b',
               type='stock_dividend', ex_date='2025-05-01')]
        res = _engine(book)
        sale = _sale(res, 'b', '2025-05-05')
        self.assertAlmostEqual(sale['cost'], 500.0 / 1.1, places=6)
        self.assertAlmostEqual(sale['gain'], 600.0 - 500.0 / 1.1, places=6)
        self.assertAlmostEqual(sale['deferred_loss_recovered'], 100.0 / 1.1,
                               places=6)
        [new] = _lots(res, 'b', 'ZZQ')
        self.assertAlmostEqual(new['qty'], 1.0, places=9)
        self.assertAlmostEqual(new['basis'], 500.0 / 11, places=6)
        self.assertAlmostEqual(new['deferred'], round(100.0 / 11, 4),
                               places=4)
        self.assertEqual((new['date'], new['holding_start']),
                         ('2025-02-10', '2025-01-09'))


class TestExchangeOfPartOfALot(unittest.TestCase):
    """`_us_draw_long_lots`, a §356 exchange that gives up part of a lot:
    the part drawn takes its share of the lot's basis and of its §1091(d)
    add, and the lot keeps the rest (core: `lot['cost_basis'] * frac`,
    `_wd * frac`, `_wd - part['wash_deferred']`)."""

    @rule(R.US_CORP_05)
    def test_part_and_rest_split_basis_and_add(self):
        # The replacement QZRX lot: 60 shares, basis 2,400 + 600 = 3,000.
        # 50 of them (5/6: basis 2,500, add 500) go for 25 QZNW worth
        # 2,600 and 300 cash: realized 2,900 - 2,500 = 400, recognized
        # 300 (the cash); QZNW basis 2,500 - 300 + 300 = 2,500.
        book = (_washed_lot(60, 3000.0, 2400.0, 2400.0)
                + _boot_rows('2025-06-20', 50, 25, 300.0, 104.0))
        res = _engine(book)
        ex = _sale(res, 'b', '2025-06-20')
        self.assertEqual((ex['qty'], ex['acquired_date'], ex['term']),
                         (50, '2025-02-10', 'SHORT_TERM'))
        self.assertAlmostEqual(ex['proceeds'], 300.0, places=6)
        self.assertAlmostEqual(ex['gain'], 300.0, places=6)
        assert_rule(self, res, ex['id'], R.US_CORP_05, record=ex['id'])     # PLAN step 11
        [rest] = _lots(res, 'b', 'QZRX')
        self.assertAlmostEqual(rest['qty'], 10.0, places=9)
        self.assertAlmostEqual(rest['basis'], 500.0, places=6)
        self.assertAlmostEqual(rest['deferred'], 100.0, places=4)
        [new] = _lots(res, 'b', 'QZNW')
        self.assertAlmostEqual(new['qty'], 25.0, places=9)
        self.assertAlmostEqual(new['basis'], 2500.0, places=6)
        self.assertAlmostEqual(new['deferred'], 500.0, places=4)
        self.assertEqual(new['holding_start'], '2025-01-09')


class TestExchangeLeavesASpentReplacementSpent(unittest.TestCase):
    """`_us_draw_long_lots`, the replacement capacity of a lot drawn in
    part: the drawn shares' capacity goes, never below zero (core:
    `r['remaining_qty'] - left / ...`). A replacement whose every share
    already replaced an earlier loss replaces no later one."""

    @rule(R.US_WASH_02, R.US_CORP_05)
    def test_a_later_loss_finds_no_capacity(self):
        # b's 60 replacement shares (bought 2025-02-10) replaced all 60 of
        # its 2025-02-03 loss; 50 go in the 2025-02-20 exchange. c's loss
        # on 2025-03-03, inside the rebuy's window, is allowed in full.
        book = ([tx('BUYSELL', '2024-11-01', 'QZRX', 40, 2000.0,
                    account='c')]
                + _washed_lot(60, 3000.0, 2400.0, 2400.0)
                + _boot_rows('2025-02-20', 50, 25, 300.0, 104.0)
                + [tx('BUYSELL', '2025-03-03', 'QZRX', -40, 1600.0,
                      account='c')])
        res = _engine(book)
        loss = _sale(res, 'c', '2025-03-03')
        self.assertAlmostEqual(loss['gain'], -400.0, places=6)
        self.assertAlmostEqual(loss['disallowed_amount'], 0.0, places=9)


class TestExchangeAfterASplitCutsCapacityInTheBuysUnits(unittest.TestCase):
    """`_us_draw_long_lots`, the same capacity cut through a split between
    the replacement's buy and the exchange: the drawn (post-split) shares
    are converted to the buy's own units (core: `_cuf or 1.0`), and the
    free shares given up stay acquired replacements (US-WASH-06,
    US-WASH-22; the owner's ruling on backlog TJ-2700, 2026-11-11: count
    them): a loss they match adds its §1091(d) amount to the new shares
    they became (core: `_us_record_left_shares`, `_rep_chunk`)."""

    @rule(R.US_WASH_02, R.US_CORP_01)
    def test_the_unmatched_shares_still_replace(self):
        # b buys 60 on 2025-02-10; 20 replace its 2025-02-03 loss, 40 are
        # free. A 2:1 split makes them 40 + 80. The exchange gives up 80:
        # the 40 matched and 40 of the 80 free, leaving 40 free (20 in the
        # buy's units). c's 30-share loss on 2025-03-03 is replaced in
        # full: 30 x (50 - 40) = 300 disallowed.
        book = [tx('BUYSELL', '2024-11-01', 'QZRX', 30, 3000.0, account='c'),
                tx('BUYSELL', '2025-01-02', 'QZRX', 20, 1000.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'QZRX', -20, 800.0, account='b'),
                tx('BUYSELL', '2025-02-10', 'QZRX', 60, 2400.0, account='b'),
                tx('SPLIT', '2025-02-14', 'QZRX', 2.0, 0.0, account='b'),
                tx('SPLIT', '2025-02-14', 'QZRX', 2.0, 0.0, account='c')
                ] + _boot_rows('2025-02-20', 80, 40, 300.0, 52.0) + [
                tx('BUYSELL', '2025-03-03', 'QZRX', -30, 1200.0, account='c')]
        res = _engine(book)
        loss = _sale(res, 'c', '2025-03-03')
        self.assertAlmostEqual(loss['disallowed_amount'], 300.0, places=6)
        self.assertAlmostEqual(loss['gain'], 0.0, places=6)
        # The 300 sits on 30 of b's 40 free shares, with c's holding
        # period (122 days before 2025-02-10).
        rep = [lot for lot in _lots(res, 'b', 'QZRX') if lot['deferred']]
        self.assertEqual([(lot['qty'], lot['basis'], lot['holding_start'])
                          for lot in rep], [(30.0, 900.0, '2024-10-11')])

    def _book(self):
        return [tx('BUYSELL', '2024-11-01', 'QZRX', 30, 3000.0, account='c'),
                tx('BUYSELL', '2025-01-02', 'QZRX', 20, 1000.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'QZRX', -20, 800.0, account='b'),
                tx('BUYSELL', '2025-02-10', 'QZRX', 60, 2400.0, account='b'),
                tx('SPLIT', '2025-02-14', 'QZRX', 2.0, 0.0, account='b'),
                tx('SPLIT', '2025-02-14', 'QZRX', 2.0, 0.0, account='c')
                ] + _boot_rows('2025-02-20', 80, 40, 300.0, 52.0)

    @rule(R.US_WASH_06, R.US_WASH_22, R.US_CORP_01)
    def test_the_shares_given_up_in_the_exchange_replace_too(self):
        # The same book, c selling 60 (backlog TJ-2700; before the ruling
        # 400 was disallowed): the 40 free shares b still holds replace
        # 40, and 20 of the 40 free shares b gave up on 2025-02-20 the
        # other 20: 60 x 10 = 600 disallowed, c's loss 0. Those 40 became
        # the second block of 20 QZNW (basis 800), on 150 of boot gain.
        # The matched 20 old shares had basis 400 and got 595, 75 of it
        # boot: 75 recognized. With the 200 their basis is 600, a loss
        # of 5, so nothing is recognized (§356(a)(1)): the exchange's row
        # is split, the matched half's 75 gone (backlog TJ-3427), and
        # their 10 QZNW take 600 - 75 + 0 = 525 (§358) and c's holding
        # period (§1223(1): 2025-02-10 less the 122 days c held); the
        # other half stays as it was.
        res = _engine(self._book() + [
            tx('BUYSELL', '2025-03-03', 'QZRX', -60, 2400.0, account='c')])
        loss = _sale(res, 'c', '2025-03-03')
        self.assertAlmostEqual(loss['disallowed_amount'], 600.0, places=6)
        self.assertAlmostEqual(loss['gain'], 0.0, places=6)
        self.assertEqual(
            [(r['date'], r['match_qty'], r['basis_bump'],
              r.get('sold_before_loss')) for r in loss['wash_replacements']],
            [('2025-02-10', 40.0, 400.0, None),
             ('2025-02-10', 20.0, 200.0, '2025-02-20')])
        self.assertEqual(
            [(lot['qty'], lot['basis'], lot['deferred'], lot['holding_start'])
             for lot in _lots(res, 'b', 'QZNW')],
            [(20.0, 1000.0, 200.0, '2025-01-09'),
             (10.0, 525.0, 125.0, '2024-10-11'),
             (10.0, 400.0, 0.0, '2025-02-10')])
        self.assertNotIn('by hand', res['_stderr'])
        # The exchange's rows: the first block's 150, then the second
        # block's matched 20 old shares at 0 and the other 20 at 75.
        boot = [t for t in res['transactions']
                if t['account'] == 'b' and t['date'] == '2025-02-20']
        self.assertEqual(
            [(t['qty'], t['proceeds'], t['gain'],
              [a['amount'] for a in t.get('wash_basis_added') or []])
             for t in boot],
            [(40.0, 150.0, 150.0, []), (20.0, 75.0, 0.0, [75.0]),
             (20.0, 75.0, 75.0, [])])

    @rule(R.US_WASH_22, R.US_CORP_01)
    def test_a_boot_gain_the_add_leaves_whole_is_not_restated(self):
        # c's loss is 1 a share (60 sold for 2940): the matched 20 old
        # shares take 20, their realized gain 195 - 20 = 175 still above
        # the 75 of boot: nothing restated, the 20 on the 10 QZNW.
        res = _engine(self._book() + [
            tx('BUYSELL', '2025-03-03', 'QZRX', -60, 2940.0, account='c')])
        self.assertEqual(
            [(lot['qty'], lot['basis'], lot['deferred'])
             for lot in _lots(res, 'b', 'QZNW')],
            [(20.0, 1000.0, 200.0), (10.0, 420.0, 20.0), (10.0, 400.0, 0.0)])
        self.assertEqual(
            [(t['qty'], t['gain']) for t in res['transactions']
             if t['account'] == 'b' and t['date'] == '2025-02-20'],
            [(40.0, 150.0), (40.0, 150.0)])

    @rule(R.US_WASH_22, R.US_CORP_01, R.US_HOLD_01)
    def test_a_boot_gain_cut_in_part_reads_long_term_in_its_dates_too(self):
        # b's 40 QZRX (400) go for 20 QZNW and 150 of cash; c's loss of
        # 540 on 30 held since 2023-11-01 matches 30 of them: basis 300 +
        # 540, consideration 892.50 (112.50 of it cash), so 52.50 of the
        # 112.50 is still recognized, long-term with c's 488 days tacked
        # on. The row's date acquired is that tacked start, as its term
        # and days say (backlog TJ-3458: it kept the purchase date,
        # 2025-02-10, beside 498 days held).
        book = [tx('BUYSELL', '2023-11-01', 'QZRX', 30, 3000.0, account='c'),
                tx('BUYSELL', '2025-02-10', 'QZRX', 40, 400.0, account='b')
                ] + _boot_rows('2025-02-20', 40, 20, 150.0, 52.0) + [
                tx('BUYSELL', '2025-03-03', 'QZRX', -30, 2460.0, account='c')]
        res = _engine(book)
        self.assertEqual(
            [(t['qty'], t['gain'], t['term'], t['acquired_date'],
              t['days_held'])
             for t in res['transactions']
             if t['account'] == 'b' and t['date'] == '2025-02-20'],
            [(30.0, 52.5, 'LONG_TERM', '2023-10-11', 498),
             (10.0, 37.5, 'SHORT_TERM', '2025-02-10', 10)])

    @rule(R.US_WASH_22)
    def test_new_shares_sold_before_the_loss_are_named_not_matched(self):
        # b sells the 40 QZNW on 2025-02-25: no lot is left to take the
        # add for the shares given up, so c's loss matches the 40 held
        # shares only (400) and the run names the rest for a manual check.
        res = _engine(self._book() + [
            tx('BUYSELL', '2025-02-25', 'QZNW', -40, 2200.0, account='b'),
            tx('BUYSELL', '2025-03-03', 'QZRX', -60, 2400.0, account='c')])
        loss = _sale(res, 'c', '2025-03-03')
        self.assertAlmostEqual(loss['disallowed_amount'], 400.0, places=6)
        self.assertAlmostEqual(loss['gain'], -200.0, places=6)
        # The note names the shares and the amount left allowed
        # (backlog TJ-3433).
        self.assertEqual(len(re.findall(
            r'left on 2025-02-20 by an exchange or an exercise, and '
            r'shares it became were sold before the loss: up to 20 of the '
            r"loss's shares \(200\.00 of it\) are not matched",
            res['_stderr'])), 1)


if __name__ == '__main__':
    unittest.main()
