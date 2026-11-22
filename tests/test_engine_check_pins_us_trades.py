"""US engine branches of the buy/sell lot walk the suite runs but no test
checks (TJ-1896, the mutation triage of 2026-11-09): `_us_walk_buy`,
`_us_walk_sell`, `_us_allot_sold_shares`, `_us_apply_sold_replacement`,
`_us_carry_lots`, `_us_move_long_lots` and `_us_draw_long_lots`. Each test
pins a figure (a gain, a disallowed loss, a lot's basis or deferral, a
row kept or dropped) a mutant of the branch changes. Synthetic data only;
the tickers (ZZQ, QZRX, QZNW) and accounts ('b', 'c', 'ira') are made up."""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
from taxjson.engine.rows import closed_record, override_record, retest_record
from _support.engine_rules import assert_rule

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.row_types import LISTING_JOURNAL_TYPE, LOT_MOVE_TYPE
from taxjson.lib.corp_actions import CorporateAction, resolve_event

CALL = 'ZZQ250620C00005000.US'
PUT = 'ZZQ250620P00004500.US'


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
    """(qty, basis, deferred) of each lot held, FIFO order (a short's qty
    and basis negative)."""
    return [(round(lot['qty'], 6), round(lot['basis'], 6), lot['deferred'])
            for lot in res['lots_at']
            if lot['account'] == account and lot['symbol'] == symbol]


def _rows(res, account, symbol=None):
    return [t for t in res['transactions']
            if t['account'] == account and t.get('qty')
            and (symbol is None or t['symbol'] == symbol)]


def _sale(res, account, date):
    [row] = [t for t in _rows(res, account) if t['date'] == date]
    return row


def _washed(qty, cost, proceeds, rebuy, symbol='ZZQ.US'):
    """b buys `qty` for `cost` on 2025-01-02, sells them for `proceeds`
    on 2025-02-03 and buys `qty` back for `rebuy` on 2025-02-10: the
    rebuy carries the disallowed loss (holding period from 2025-01-09)."""
    return [tx('BUYSELL', '2025-01-02', symbol, qty, cost, account='b'),
            tx('BUYSELL', '2025-02-03', symbol, -qty, proceeds, account='b'),
            tx('BUYSELL', '2025-02-10', symbol, qty, rebuy, account='b')]


def _washed_call():
    """b buys the call for 500 on 2025-01-02, sells it for 200 on
    2025-02-03 and buys it back for 250 on 2025-02-10 (the identical
    option replaces the loss: its basis 550, 300 of it the add)."""
    return [tx('BUYSELL', '2025-01-02', CALL, 1, 500.0, account='b'),
            tx('BUYSELL', '2025-02-03', CALL, -1, 200.0, account='b'),
            tx('BUYSELL', '2025-02-10', CALL, 1, 250.0, account='b')]


def _exercise(date='2025-03-03'):
    """The call exercised: 100 ZZQ.US at the strike of 5.00."""
    return [tx('ASSIGN', date, CALL, -1, 0.0, account='b', time='09:30:00'),
            tx('ASSIGN', date, 'ZZQ.US', 100, 500.0, account='b',
               time='10:00:00')]


def _move(date, sym, qty, src, dst):
    desc = f"own-account move #1: {src} -> {dst} (test)"
    return [tx('TRANSFER', date, sym, -qty, 0.0, account=src,
               type=LOT_MOVE_TYPE, description=desc, time='00:00:00'),
            tx('TRANSFER', date, sym, qty, 0.0, account=dst,
               type=LOT_MOVE_TYPE, description=desc, time='00:00:00')]


def _journal(date, src_sym, dst_sym, qty, account):
    return [tx('TRANSFER', date, src_sym, -qty, 0.0, account=account,
               type=LISTING_JOURNAL_TYPE, journal_pair='j1'),
            tx('TRANSFER', date, dst_sym, qty, 0.0, account=account,
               type=LISTING_JOURNAL_TYPE, journal_pair='j1')]


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


# ------------------------------------------------------------ _us_walk_buy
class TestPartialCoverReleasesTheShortsWashAdd(unittest.TestCase):
    """`_us_walk_buy`, a partial cover of a short lot that carries a
    §1091(e) add: the covered chunk takes its share of the add (core:
    `(D(chunk_qty) / D(short_lot['qty'] + chunk_qty)) * _swd`) and the lot
    keeps the rest (`_swd - chunk_wd_d`). One cover row never reaches it
    (the disposition plan splits the lot at the cover first): the first
    of two same-second fills of one order does."""

    @rule(R.US_WASH_05)
    @rule(R.US_WASH_09)
    def test_two_fills_take_their_shares_of_the_add(self):
        # A 4-share short (400) covered for 440: a 40 loss. The re-short
        # of 10 at 105 two days later replaces it share for share: its
        # first 4 shares open at 420 - 40 = 380 with 40 of add. One order
        # covers 3 of them for 270, in fills of 2 (180) and 1 (90):
        # proceeds 190 and 95, gains 10 and 5, adds released 20 and 10;
        # the last share keeps 95 and 10.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', -4, 400.0, account='b'),
                tx('BUYSELL', '2025-03-10', 'ZZQ', 4, 440.0, account='b'),
                tx('BUYSELL', '2025-03-12', 'ZZQ', -10, 1050.0, account='b'),
                tx('BUYSELL', '2025-03-20', 'ZZQ', 2, 180.0, account='b',
                   time='11:00:00'),
                tx('BUYSELL', '2025-03-20', 'ZZQ', 1, 90.0, account='b',
                   time='11:00:00')]
        res = _engine(book)
        self.assertAlmostEqual(_sale(res, 'b', '2025-03-10')
                               ['disallowed_amount'], 40.0, places=6)
        self.assertEqual(
            [(r['qty'], round(r['gain'], 6),
              round(r['deferred_loss_recovered'], 6))
             for r in _rows(res, 'b') if r['date'] == '2025-03-20'],
            [(2.0, 10.0, 20.0), (1.0, 5.0, 10.0)])
        self.assertEqual(_lots(res, 'b', 'ZZQ'),
                         [(-1.0, -95.0, 10.0), (-6.0, -630.0, 0.0)])


class TestReplacementShortSplitKeepsItsCarriedAdd(unittest.TestCase):
    """`_us_walk_buy`, a short-cover loss matched to part of a replacement
    short that already carries an add (a written call's, rolled in by
    its assignment): the unmatched rest keeps (1 - fraction) of it and
    the matched part the fraction (core: `_swd0 * (1 - _sfr)`,
    `_swd0 - _srem['wash_deferred']`)."""

    @rule(R.US_WASH_03)
    @rule(R.US_WASH_05)
    @rule(R.US_WASH_09)
    def test_the_assigned_shorts_add_splits_by_shares(self):
        # The written call (300) bought back for 600: a 300 loss; the
        # call written again for 400 replaces it (its premium 100, 300 of
        # add). Assigned: 100 ZZQ.US sold short at 5.00, proceeds 600,
        # 300 of add. The older 40-share short (180) covered for 240 a
        # week later: a 60 loss, replaced by 40 of the 100: they keep
        # 240 - 60 = 180 and 120 + 60 of add; the other 60 keep 360 and
        # 180 of add.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ.US', -40, 180.0,
                   account='b'),
                tx('BUYSELL', '2025-01-02', CALL, -1, 300.0, account='b'),
                tx('BUYSELL', '2025-02-03', CALL, 1, 600.0, account='b'),
                tx('BUYSELL', '2025-02-10', CALL, -1, 400.0, account='b'),
                tx('ASSIGN', '2025-03-03', CALL, 1, 0.0, account='b',
                   time='09:30:00'),
                tx('ASSIGN', '2025-03-03', 'ZZQ.US', -100, 500.0,
                   account='b', time='10:00:00'),
                tx('BUYSELL', '2025-03-10', 'ZZQ.US', 40, 240.0,
                   account='b')]
        res = _engine(book)
        self.assertAlmostEqual(_sale(res, 'b', '2025-03-10')
                               ['disallowed_amount'], 60.0, places=6)
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'),
                         [(-40.0, -180.0, 180.0), (-60.0, -360.0, 180.0)])


class TestExercisedReplacementCallsAddInTheUnmatchedShares(unittest.TestCase):
    """`_us_walk_buy`, a purchase matched before its own buy (the loss
    came first) that carries an exercised call's add: the unmatched
    remainder takes its share of that add (core: `_odw * D(_left) /
    D(qty_remaining)`)."""

    @rule(R.US_WASH_09)
    @rule(R.US_WASH_10)
    def test_the_remainder_keeps_its_share_of_the_calls_add(self):
        # 40 ZZQ.US (240) sold for 180 on 2025-02-24: a 60 loss. The
        # replacement call (basis 550, 300 of add) exercised on
        # 2025-03-03 into 100 shares at 5.00: basis 1,050, add 300. 40 of
        # them replace the loss: 420 + 60, add 120 + 60, held from
        # 2024-06-10; the other 60: 630 with 180 of the call's add, held
        # from the exercise.
        book = ([tx('BUYSELL', '2024-06-03', 'ZZQ.US', 40, 240.0,
                    account='b'),
                 tx('BUYSELL', '2025-02-24', 'ZZQ.US', -40, 180.0,
                    account='b')]
                + _washed_call() + _exercise())
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'),
                         [(40.0, 480.0, 180.0), (60.0, 630.0, 180.0)])
        self.assertEqual([lot['holding_start'] for lot in res['lots_at']
                          if lot['symbol'] == 'ZZQ.US'],
                         ['2024-06-10', '2025-03-03'])


class TestUnmatchedRemainderSettlesWithItsPurchase(unittest.TestCase):
    """`_us_walk_buy`, the unmatched remainder of a purchase matched before
    its own buy: the lot settles on the purchase's settlement date (core:
    `'date_settle': tx.date_settle or tx.date`), so a distribution whose
    record date falls before it does not reach it."""

    @rule(R.US_DIST_01)
    def test_a_record_date_before_settlement_skips_the_remainder(self):
        # 10 sold at a 100 loss on 2025-02-03; 5 bought on 2025-02-07
        # (settled that day) and 20 on 2025-02-10 (settling 2025-02-12)
        # replace 5 shares each. A distribution of 30 with record date
        # 2025-02-11 belongs to the 5 settled: 250 - 30 = 220; the
        # 2025-02-10 blocks keep 250 and 600.
        book = [tx('BUYSELL', '2024-06-03', 'ZZQ.US', 10, 500.0,
                   account='b'),
                tx('BUYSELL', '2025-02-03', 'ZZQ.US', -10, 400.0,
                   account='b'),
                tx('BUYSELL', '2025-02-07', 'ZZQ.US', 5, 200.0, account='b'),
                tx('BUYSELL', '2025-02-10', 'ZZQ.US', 20, 800.0,
                   account='b', settle='2025-02-12'),
                tx('ADJUST', '2025-02-20', 'ZZQ.US', 0, -30.0, account='b',
                   type='dist', record_date='2025-02-11')]
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'),
                         [(5.0, 220.0, 50.0), (5.0, 250.0, 50.0),
                          (15.0, 600.0, 0.0)])


# ----------------------------------------------------------- _us_walk_sell
class TestLotResidueIsTheLotLessTheSale(unittest.TestCase):
    """`_us_walk_sell`, a lot at most 1e-08 units over the sale: the
    residue is folded into the sale and reported as the lot less the
    units sold (core: `lot['qty'] - qty_remaining`)."""

    @rule(R.US_CRYPTO_08)
    def test_the_residue_reported_is_five_billionths(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ.US', 10.000000005, 500.0,
                   account='b'),
                tx('BUYSELL', '2025-03-03', 'ZZQ.US', -10, 600.0,
                   account='b')]
        res = _engine(book)
        self.assertAlmostEqual(_sale(res, 'b', '2025-03-03')['cost'], 500.0,
                               places=6)
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [])
        self.assertIn("ZZQ.US (1 time(s), +5e-09 units)", res['_stderr'])


class TestPartialSaleReleasesTheLotsWashAdd(unittest.TestCase):
    """`_us_walk_sell`, a partial sale of a replacement lot: the sold
    chunk takes its share of the add and the lot keeps the rest (core:
    `lot['wash_deferred'] = _wd - chunk_wd_d`). As on the short side, the
    first of two same-second fills reaches it (the plan splits the lot at
    the order's 4 units)."""

    @rule(R.US_WASH_09)
    def test_two_fills_of_four_take_thirty_and_ten(self):
        # The replacement's 10 shares: 500 with 100 of add. One order
        # sells 4 for 200, in fills of 3 and 1: costs 150 and 50, adds
        # recovered 30 and 10; the 6 kept hold 300 and 60.
        res = _engine(_washed(10, 500.0, 400.0, 400.0)
                      + [tx('BUYSELL', '2025-03-03', 'ZZQ.US', -3, 150.0,
                            account='b', time='11:00:00'),
                         tx('BUYSELL', '2025-03-03', 'ZZQ.US', -1, 50.0,
                            account='b', time='11:00:00')])
        self.assertEqual(
            [(r['qty'], round(r['cost'], 6),
              round(r['deferred_loss_recovered'], 6))
             for r in _rows(res, 'b') if r['date'] == '2025-03-03'],
            [(3.0, 150.0, 30.0), (1.0, 50.0, 10.0)])
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(6.0, 300.0, 60.0)])


class TestSoldAndHeldReplacementsMatchInTimeOrder(unittest.TestCase):
    """`_us_walk_sell`, a loss with both a held replacement and one another
    taxable account sold before the loss: the candidates are sorted by
    date, then time of day, before the account kind (core: the `_ck` key,
    `r['tx'].time or ''`)."""

    @rule(R.US_WASH_20)
    @rule(R.US_WASH_11)
    @rule(R.US_WASH_22)
    def test_the_ira_purchase_at_0930_beats_c_at_1500(self):
        # b sells 5 at a 50 loss on 2025-02-03. On 2025-01-20 the IRA
        # bought 5 at 09:30 and c bought 5 at 15:00 (sold by c on
        # 2025-01-27): the IRA's purchase was first, so the 50 is
        # disallowed for good (Rev. Rul. 2008-5) and c's sale keeps its
        # gain of 15.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 500.0, account='b'),
                tx('BUYSELL', '2025-01-20', 'ZZQ', 5, 225.0, account='c',
                   time='15:00:00'),
                tx('BUYSELL', '2025-01-27', 'ZZQ', -5, 240.0, account='c'),
                tx('BUYSELL', '2025-02-03', 'ZZQ', -5, 200.0, account='b')]
        ira = [tx('BUYSELL', '2025-01-20', 'ZZQ', 5, 225.0, account='ira',
                  time='09:30:00')]
        res = _engine(book, sheltered_transactions=ira)
        loss = _sale(res, 'b', '2025-02-03')
        self.assertAlmostEqual(loss['disallowed_amount'], 50.0, places=6)
        self.assertAlmostEqual(loss['permanently_disallowed'], 50.0,
                               places=6)
        sold = _sale(res, 'c', '2025-01-27')
        self.assertAlmostEqual(sold['cost'], 225.0, places=6)
        self.assertAlmostEqual(sold['gain'], 15.0, places=6)


class TestSoldReplacementAcrossASplit(unittest.TestCase):
    """`_us_walk_sell` and `_us_apply_sold_replacement`, a replacement
    another account bought before a split and sold after it, before the
    loss: its capacity (in the purchase's units) is matched in the loss
    date's units (core: `_us_rep_units_factor(...) or 1.0`,
    `_sch['avail'] * _uf`), and the add lands on the sale's units of it
    (`q_sale = match_qty / uf * _ufs`, `_ufs = ... or 1.0`)."""

    @rule(R.US_WASH_22)
    @rule(R.US_WASH_02)
    def test_ten_post_split_shares_replace_ten(self):
        # b: 10 for 1,000, split to 20, sold for 800 on 2025-02-03: a 200
        # loss, 10 a share. c bought 5 for 450 on 2025-01-20 (10 after
        # the split) and sold them for 460 on 2025-01-28: those 10
        # replace 10 of b's 20 loss shares: 100 disallowed, added to c's
        # sale (one row: cost 550, gain -90) with b's 32 days tacked.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
                tx('BUYSELL', '2025-01-20', 'ZZQ', 5, 450.0, account='c'),
                tx('SPLIT', '2025-01-25', 'ZZQ', 2),
                tx('BUYSELL', '2025-01-28', 'ZZQ', -10, 460.0, account='c'),
                tx('BUYSELL', '2025-02-03', 'ZZQ', -20, 800.0, account='b')]
        res = _engine(book)
        loss = _sale(res, 'b', '2025-02-03')
        self.assertAlmostEqual(loss['disallowed_amount'], 100.0, places=6)
        self.assertAlmostEqual(loss['gain'], -100.0, places=6)
        self.assertEqual(
            [(r['qty'], round(r['cost'], 6), round(r['gain'], 6),
              r['days_held']) for r in _rows(res, 'c')],
            [(10.0, 550.0, -90.0, 40)])


class TestCallReplacementLotSplitKeepsItsCarriedAdd(unittest.TestCase):
    """`_us_walk_sell`, a loss matched to part of a replacement lot that
    already carries an add (an exercised replacement call's): the
    unmatched rest keeps (1 - fraction) of it and the matched part the
    fraction (core: `_rem['wash_deferred'] = ... * (1 - _frac)`,
    `_tack_lot['wash_deferred'] = ... * _frac`)."""

    @rule(R.US_WASH_03)
    @rule(R.US_WASH_09)
    def test_the_exercised_lots_add_splits_by_shares(self):
        # The exercised call's 100 shares: basis 1,050, add 300. A week
        # later the older 40 (240) sold for 180: a 60 loss, replaced by
        # 40 of the 100: they hold 420 + 60 and 120 + 60 of add; the
        # other 60 hold 630 and 180.
        book = ([tx('BUYSELL', '2024-06-03', 'ZZQ.US', 40, 240.0,
                    account='b')]
                + _washed_call() + _exercise()
                + [tx('BUYSELL', '2025-03-10', 'ZZQ.US', -40, 180.0,
                      account='b')])
        res = _engine(book)
        self.assertAlmostEqual(_sale(res, 'b', '2025-03-10')
                               ['disallowed_amount'], 60.0, places=6)
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'),
                         [(40.0, 480.0, 180.0), (60.0, 630.0, 180.0)])


class TestPremiumAfterCutIsAWrittenCallsOnly(unittest.TestCase):
    """`_us_walk_sell`, a sale whose §1091 add a return of capital cut: the
    `premium_after_cut` note is a written call's premium only (core:
    `if option_adj_sell < 0 and not _call_said`); a plain sale or a long
    put's exercise has none."""

    def _book(self):
        # The replacement lot: 100 shares, basis 400 + 100 add; a return
        # of capital of 450 leaves 50, so 50 of the add is cut at sale.
        return (_washed(100, 500.0, 400.0, 400.0)
                + [tx('ADJUST', '2025-03-03', 'ZZQ.US', 0, -450.0,
                      account='b', type='roc')])

    @rule(R.US_ROC_01)
    @rule(R.US_WASH_09)
    def test_a_plain_sale_has_no_premium_note(self):
        res = _engine(self._book() + [tx('BUYSELL', '2025-04-01', 'ZZQ.US',
                                         -100, 450.0, account='b')])
        sale = _sale(res, 'b', '2025-04-01')
        self.assertAlmostEqual(sale['gain'], 400.0, places=6)
        self.assertAlmostEqual(sale['deferred_loss_cut'], 50.0, places=6)
        self.assertAlmostEqual(sale['deferred_loss_recovered'], 50.0,
                               places=6)
        self.assertNotIn('premium_after_cut', sale)

    @rule(R.US_ROC_01)
    @rule(R.US_WASH_09)
    def test_a_long_puts_cost_is_no_premium_note(self):
        # The put (200) exercised: the shares sold at 4.50 for 450, less
        # the put's cost: 250 realized, a gain of 200.
        res = _engine(self._book() + [
            tx('BUYSELL', '2025-03-05', PUT, 1, 200.0, account='b'),
            tx('ASSIGN', '2025-04-01', PUT, -1, 0.0, account='b',
               time='09:30:00'),
            tx('ASSIGN', '2025-04-01', 'ZZQ.US', -100, 450.0, account='b',
               time='10:00:00')])
        sale = _sale(res, 'b', '2025-04-01')
        self.assertAlmostEqual(sale['proceeds'], 250.0, places=6)
        self.assertAlmostEqual(sale['gain'], 200.0, places=6)
        self.assertAlmostEqual(sale['deferred_loss_cut'], 50.0, places=6)
        self.assertNotIn('premium_after_cut', sale)


# --------------------------------------------------- _us_allot_sold_shares
class TestEntitledSaleGivesItsNewSharesTheirPartOfTheAdd(unittest.TestCase):
    """`_us_allot_sold_shares`, entitled shares of a replacement lot sold
    between the ex date and the pay date: the sale gives 1 - 1/ratio of
    its cost AND of its §1091 add to its new shares (core: `wd = D(e.get(
    'deferred_loss_recovered') or 0.0) * (1 - keep)`), its gain and raw
    gain rise by the cost moved (`e['raw_gain'] + float(amt)`), and the
    new lot keeps the sold lot's purchase date (`e.get('acquired_date')
    or div_tx.date`)."""

    @rule(R.US_STKDIV_05)
    def test_the_new_shares_take_a_eleventh_of_cost_and_add(self):
        # 200 bought for 1,000 sold at a 200 loss; the 200 bought back
        # for 800 on 2025-01-20 carry it (basis 1,000, add 200, held from
        # 2024-12-12). 100 of them sold for 600 on 2025-06-10, after the
        # ex date (2025-06-02) of a 20-share dividend paid 2025-06-16:
        # ratio 1.1, so the sale gives 500/11 of cost and 100/11 of add
        # to its 10 new shares.
        book = [tx('BUYSELL', '2024-12-02', 'ZZQ', 200, 1000.0, account='b'),
                tx('BUYSELL', '2025-01-10', 'ZZQ', -200, 800.0, account='b'),
                tx('BUYSELL', '2025-01-20', 'ZZQ', 200, 800.0, account='b'),
                tx('BUYSELL', '2025-06-10', 'ZZQ', -100, 600.0, account='b'),
                tx('BUYSELL', '2025-06-16', 'ZZQ', 20, 0.0, account='b',
                   type='stock_dividend', ex_date='2025-06-02')]
        res = _engine(book)
        sale = _sale(res, 'b', '2025-06-10')
        self.assertAlmostEqual(sale['cost'], 5000.0 / 11, places=6)
        self.assertAlmostEqual(sale['gain'], 600.0 - 5000.0 / 11, places=6)
        self.assertAlmostEqual(sale['raw_gain'], 600.0 - 5000.0 / 11,
                               places=6)
        self.assertAlmostEqual(sale['deferred_loss_recovered'],
                               1000.0 / 11, places=6)
        new = [lot for lot in res['lots_at'] if lot['qty'] < 50]
        self.assertEqual([(lot['date'], lot['holding_start'])
                          for lot in new], [('2025-01-20', '2024-12-12')])
        self.assertAlmostEqual(new[0]['qty'], 10.0, places=6)
        self.assertAlmostEqual(new[0]['basis'], 500.0 / 11, places=6)
        self.assertAlmostEqual(new[0]['deferred'], 9.0909, places=4)


class TestWashedEntitledSaleIsLeftForAManualMove(unittest.TestCase):
    """`_us_allot_sold_shares`, an entitled sale the wash-sale rule
    touched: it is left as booked, its new shares at $0, the amount named
    for a manual move (core: `(e.get('disallowed_amount') or 0.0) >
    run.epsilon`)."""

    @rule(R.US_STKDIV_05)
    def test_the_washed_sale_keeps_its_cost(self):
        # 100 of 200 (1,000) sold for 400 on 2025-06-10, after the ex
        # date, and replaced on 2025-06-12: the 100 loss is disallowed,
        # so the sale keeps its 500 and its 15 new shares are at $0.
        book = [tx('BUYSELL', '2024-01-02', 'ZZQ', 200, 1000.0, account='b'),
                tx('BUYSELL', '2025-06-10', 'ZZQ', -100, 400.0, account='b'),
                tx('BUYSELL', '2025-06-12', 'ZZQ', 100, 400.0, account='b'),
                tx('BUYSELL', '2025-06-16', 'ZZQ', 30, 0.0, account='b',
                   type='stock_dividend', ex_date='2025-06-02')]
        res = _engine(book)
        sale = _sale(res, 'b', '2025-06-10')
        self.assertAlmostEqual(sale['cost'], 500.0, places=6)
        self.assertAlmostEqual(sale['gain'], 0.0, places=6)
        self.assertAlmostEqual(sale['disallowed_amount'], 100.0, places=6)
        self.assertEqual([(round(q, 6), b) for q, b, _d in
                          _lots(res, 'b', 'ZZQ')],
                         [(115.0, 500.0), (15.0, 0.0), (100.0, 500.0)])
        self.assertIn("move 65.22 of that sale's cost", res['_stderr'])


# -------------------------------------------- _us_carry_lots / _us_move_long_lots
class TestListingJournalSplitsTheLotsWashAdd(unittest.TestCase):
    """`_us_carry_lots`, a listing journal of part of a replacement lot:
    the moved part takes its share of the add and the lot keeps the rest
    (core: `part['wash_deferred'] = _wd * frac`, `_wd -
    part['wash_deferred']`)."""

    @rule(R.US_XLIST_01)
    @rule(R.US_WASH_09)
    def test_four_of_ten_journaled_take_forty(self):
        res = _engine(_washed(10, 500.0, 400.0, 400.0)
                      + _journal('2025-03-03', 'ZZQ.US', 'ZZQX.US', 4, 'b'))
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(6.0, 300.0, 60.0)])
        self.assertEqual(_lots(res, 'b', 'ZZQX.US'), [(4.0, 200.0, 40.0)])


class TestOwnMoveSplitsTheLotsWashAdd(unittest.TestCase):
    """`_us_move_long_lots`, a move of part of a replacement lot to another
    of your own accounts: the moved part takes its share of the add and
    the lot keeps the rest (core: `part['wash_deferred'] = _wd * frac`,
    `_wd - part['wash_deferred']`)."""

    @rule(R.US_BASIS_05)
    @rule(R.US_WASH_09)
    def test_four_of_ten_moved_take_forty(self):
        res = _engine(_washed(10, 500.0, 400.0, 400.0)
                      + _move('2025-03-03', 'ZZQ.US', 4, 'b', 'c'))
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(6.0, 300.0, 60.0)])
        self.assertEqual(_lots(res, 'c', 'ZZQ.US'), [(4.0, 200.0, 40.0)])


# ----------------------------------------------------- _us_draw_long_lots
class TestExchangeOfAWholeLotLeavesNoEmptyLot(unittest.TestCase):
    """`_us_draw_long_lots`, a §356 exchange giving up exactly the first
    lot: the lot leaves the pool whole (core: `if lot['qty'] <= left +
    run.epsilon`), not as an empty lot that still dates the position and
    closes as a row of its own."""

    @rule(R.US_CORP_05)
    def test_the_position_starts_with_the_lot_left(self):
        book = ([tx('BUYSELL', '2023-01-10', 'QZRX', 6, 240.0, account='b'),
                 tx('BUYSELL', '2024-03-01', 'QZRX', 15, 900.0, account='b')]
                + _boot_rows('2025-04-01', 6, 3, 60.0, 30.0)
                + [tx('BUYSELL', '2025-05-01', 'QZRX', -15, 1200.0,
                      account='b')])
        res = _engine(book[:-1])
        [inv] = [i for i in res['inventory'] if i['symbol'] == 'QZRX']
        self.assertEqual((inv['qty'], inv['position_start_date'],
                          inv['holding_start_date']),
                         (15.0, '2024-03-01', '2024-03-01'))
        res = _engine(book)
        self.assertEqual(
            [(t['date'], t['qty'], round(t['gain'], 6))
             for t in res['transactions'] if t['symbol'] == 'QZRX'
             and t['date'] == '2025-05-01'],
            [('2025-05-01', 15.0, 300.0)])


# ------------------------------------------------- _us_walk_buy, the cover
class TestAnotherAccountsCoveredShortStillReplaces(unittest.TestCase):
    """`_us_walk_buy`, a cover's loss against a short sale another taxable
    account made in the window and covered before it: §1091(e) applies
    §1091 to short sales, with no still-held test either (the owner's
    ruling on backlog TJ-2708, 2026-11-11: US-WASH-22 reaches short
    sales). The disallowed loss comes off the proceeds of that covered
    short, so its cover's gain falls by it (core:
    `_us_record_left_shares(..., short=True)`, `_short_rep_of`). Before
    the ruling c's 200 loss was allowed."""

    BOOK = [tx('BUYSELL', '2024-12-02', 'ZZQ', -10, 1000.0, account='c'),
            tx('BUYSELL', '2025-01-02', 'ZZQ', -10, 1000.0, account='b'),
            tx('BUYSELL', '2025-01-08', 'ZZQ', 10, 900.0, account='b'),
            tx('BUYSELL', '2025-01-10', 'ZZQ', 10, 1200.0, account='c')]

    @rule(R.US_WASH_22, R.US_WASH_05)
    def test_the_loss_is_disallowed_onto_the_other_accounts_cover(self):
        # c short 10 for 1000, covered for 1200 on 2025-01-10: a 200
        # loss. b shorted 10 on 2025-01-02 and covered for 900 on 01-08
        # (a 100 gain): the 200 comes off b's 1000, b's cover a 100 loss.
        res = _engine(self.BOOK)
        loss = _sale(res, 'c', '2025-01-10')
        self.assertAlmostEqual(loss['raw_gain'], -200.0, places=6)
        self.assertAlmostEqual(loss['disallowed_amount'], 200.0, places=6)
        self.assertAlmostEqual(loss['gain'], 0.0, places=6)
        [rep] = loss['wash_replacements']
        self.assertEqual((rep['account'], rep['date'], rep['match_qty'],
                          rep['proceeds_reduction'],
                          rep['covered_before_loss']),
                         ('b', '2025-01-02', 10.0, 200.0, '2025-01-08'))
        cover = _sale(res, 'b', '2025-01-08')
        self.assertEqual((cover['direction'], cover['term']),
                         ('SHORT', 'SHORT_TERM'))
        self.assertAlmostEqual(cover['cost'], -800.0, places=6)
        self.assertAlmostEqual(cover['gain'], -100.0, places=6)
        self.assertEqual(
            [(a['amount'], a['qty'], a['loss_account'], a['term'])
             for a in cover['wash_basis_added']],
            [(200.0, 10.0, 'c', 'SHORT_TERM')])
        # b's cover is now a loss of 100, tested itself (backlog
        # TJ-3428): no short was entered in its own 61 days but the one
        # it closed, so it stays allowed, with no line.
        self.assertAlmostEqual(cover['disallowed_amount'], 0.0, places=9)
        self.assertNotIn('by hand', res['_stderr'])

    @rule(R.US_WASH_22)
    def test_half_the_loss_splits_the_other_accounts_cover(self):
        # c covers 5 of its 10 at the same price: 100 of loss matches 5
        # of b's 10 covered shares, whose row is split at the match.
        res = _engine(self.BOOK[:3] + [
            tx('BUYSELL', '2025-01-10', 'ZZQ', 5, 600.0, account='c')])
        self.assertAlmostEqual(
            _sale(res, 'c', '2025-01-10')['disallowed_amount'], 100.0,
            places=6)
        self.assertEqual(
            sorted((t['qty'], round(t['gain'], 6)) for t in _rows(res, 'b')),
            [(5.0, -50.0), (5.0, 50.0)])

    @rule(R.US_WASH_22, R.US_WASH_20)
    def test_the_itself_disallowed_line_waits_for_its_turn(self):
        # c shorts 10 (01-02); a shorts 20 (01-05); b shorts 10 (01-08)
        # and covers at a loss (01-10), disallowed against c's open
        # short. c covers its 10 at a loss of 300 on 01-20: a's short,
        # made first and still open, takes all of it, so b's covered
        # short is never reached and no line names it (backlog TJ-3437).
        # With a's short out of the book b's turn comes with the loss
        # unmatched, and the line is said.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', -10, 1000.0, account='c'),
                tx('BUYSELL', '2025-01-05', 'ZZQ', -20, 2000.0, account='a'),
                tx('BUYSELL', '2025-01-08', 'ZZQ', -10, 1000.0, account='b'),
                tx('BUYSELL', '2025-01-10', 'ZZQ', 10, 1100.0, account='b'),
                tx('BUYSELL', '2025-01-20', 'ZZQ', 10, 1200.0, account='c')]
        line = ('sold short 2025-01-08 in b and covered 2025-01-10 at a '
                'loss that was itself disallowed')
        res = _engine(book)
        self.assertAlmostEqual(
            _sale(res, 'c', '2025-01-20')['disallowed_amount'], 300.0,
            places=6)
        self.assertNotIn(line, res['_stderr'])
        res = _engine(book[:1] + book[2:])
        self.assertAlmostEqual(
            _sale(res, 'c', '2025-01-20')['disallowed_amount'], 0.0,
            places=6)
        self.assertEqual(res['_stderr'].count(line), 1)

    @rule(R.US_WASH_22, R.US_WASH_20)
    def test_the_long_sides_line_waits_for_its_turn_too(self):
        # The long mirror: b's purchase sold at a disallowed loss before
        # c's loss (its own 10 of 2024-12-20 carry b's 100) is named only
        # when a's earlier purchase, still held, does not take the whole
        # loss first.
        book = [tx('BUYSELL', '2024-12-20', 'ZZQ', 10, 1200.0, account='c'),
                tx('BUYSELL', '2025-01-05', 'ZZQ', 20, 2000.0, account='a'),
                tx('BUYSELL', '2025-01-08', 'ZZQ', 10, 1000.0, account='b'),
                tx('BUYSELL', '2025-01-10', 'ZZQ', -10, 900.0, account='b'),
                tx('BUYSELL', '2025-01-20', 'ZZQ', -10, 900.0, account='c')]
        line = ('bought 2025-01-08 in b and sold 2025-01-10 at a loss that '
                'was itself disallowed')
        self.assertNotIn(line, _engine(book)['_stderr'])
        self.assertEqual(
            _engine(book[:1] + book[2:])['_stderr'].count(line), 1)

    @rule(R.US_WASH_21)
    def test_the_same_accounts_covered_short_does_not(self):
        # Every row in b: the short covered before the loss no longer
        # washes it (US-WASH-21 is unchanged).
        res = _engine([tx(t.action, t.date, t.symbol, t.quantity,
                          t.net_amount, account='b') for t in self.BOOK])
        loss = _sale(res, 'b', '2025-01-10')
        self.assertAlmostEqual(loss['disallowed_amount'], 0.0, places=9)
        self.assertAlmostEqual(loss['gain'], -200.0, places=6)



# ------------------------------------------- _us_retest_row (TJ-3428)
class TestALossAnAddMakesIsTestedItself(unittest.TestCase):
    """`_us_retest_row`: a wash add (US-WASH-22) can leave the earlier
    sale or cover it lands on at a loss. That loss is a loss of that
    row's own date and account and is tested under §1091 itself, against
    what is left of the replacements in its own 61 days, in the order
    acquired (US-WASH-20) — the owner's decision on backlog TJ-3428,
    2026-11-13 (it was never tested: an add turned a deferred loss into
    an allowed one; the interim named it for a manual check). It is
    tested when the add is made: replacement units a loss the walk
    already matched took stay with that loss."""

    SHORT = [tx('BUYSELL', '2024-12-02', 'ZZQ', -10, 1000.0, account='c'),
             tx('BUYSELL', '2025-01-02', 'ZZQ', -10, 1000.0, account='b'),
             tx('BUYSELL', '2025-01-08', 'ZZQ', 10, 900.0, account='b'),
             tx('BUYSELL', '2025-01-10', 'ZZQ', 10, 1200.0, account='c')]
    LONG = [tx('BUYSELL', '2024-12-02', 'ZZQ', 10, 1200.0, account='c'),
            tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
            tx('BUYSELL', '2025-01-08', 'ZZQ', -10, 1100.0, account='b'),
            tx('BUYSELL', '2025-01-10', 'ZZQ', -10, 1000.0, account='c')]

    @staticmethod
    def _row(res, account, date):
        t = _sale(res, account, date)
        return (round(t['raw_gain'], 6), round(t['disallowed_amount'], 6),
                round(t['gain'], 6))

    @rule(R.US_WASH_22, R.US_WASH_05, R.US_WASH_27)
    def test_the_covers_new_loss_is_deferred_onto_a_later_short(self):
        # c's 200 loss (01-10) comes off the proceeds of b's short covered
        # 01-08, a gain of 100 that is now a loss of 100. A short entered
        # 7 days after that cover, in c or in b, replaces it: the 100 is
        # disallowed on b's cover and deferred on the open short (before:
        # b's cover -100 allowed, the open short carrying nothing; before
        # TJ-2708 the open short carried c's 200).
        for acct in ('c', 'b'):
            with self.subTest(short_in=acct):
                res = _engine(self.SHORT + [
                    tx('BUYSELL', '2025-01-15', 'ZZQ', -10, 1100.0,
                       account=acct)])
                self.assertEqual(self._row(res, 'c', '2025-01-10'),
                                 (-200.0, 200.0, 0.0))
                self.assertEqual(self._row(res, 'b', '2025-01-08'),
                                 (-100.0, 100.0, 0.0))
                self.assertEqual(_lots(res, acct, 'ZZQ'),
                                 [(-10.0, -1000.0, 100.0)])
                [rep] = _sale(res, 'b', '2025-01-08')['wash_replacements']
                self.assertEqual(
                    (rep['date'], rep['account'], rep['match_qty'],
                     rep['proceeds_reduction'], rep['retest_of']),
                    ('2025-01-15', acct, 10.0, 100.0,
                     _sale(res, 'c', '2025-01-10')['id']))
                # The retested cover's disallowance names US-WASH-22, where
                # the retest is stated; the open short's proceeds cut,
                # US-WASH-27 (the owner's ruling on TJ-4153, questions 4, 5).
                b_cover = _sale(res, 'b', '2025-01-08')['id']
                assert_rule(self, res, b_cover, R.US_WASH_22, record=b_cover)
                assert_rule(self, res, b_cover, R.US_WASH_27, record=rep['tx_id'])
                self.assertEqual(
                    sorted((w['date'], w['direction'], w['disallowed_amount'])
                           for w in res['wash_sales']),
                    [('2025-01-08', 'SHORT', 100.0),
                     ('2025-01-10', 'SHORT', 200.0)])
                self.assertNotIn('by hand', res['_stderr'])

    @rule(R.US_WASH_25, R.US_WASH_22)
    def test_a_retested_row_a_filing_position_names_keeps_both_records(self):
        # b's 01-08 loss of 100 is allowed by an ALLOWLOSS line (c's 01-15
        # purchase would have disallowed it); c's 01-10 loss then lands 200
        # on that row (b's 01-02 purchase, sold before it), and the retest,
        # which reads no filing position, disallows 200 onto the rest of
        # c's purchase. The row's two records keep their own rules
        # (backlog TJ-4146: the retest's replaced the override's); the
        # retest's names US-WASH-22, where the retest is stated (US-WASH-01
        # until the owner's ruling on TJ-4153, question 4).
        book = [tx('BUYSELL', '2024-12-02', 'ZZQ', 10, 1200.0, account='c'),
                tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
                tx('BUYSELL', '2025-01-08', 'ZZQ', -10, 900.0, account='b'),
                tx('BUYSELL', '2025-01-10', 'ZZQ', -10, 1000.0, account='c'),
                tx('BUYSELL', '2025-01-15', 'ZZQ', 20, 2100.0, account='c')]
        ov = {"account": "b", "date": "2025-01-08", "symbol": "ZZQ", "qty": None,
              "reason": "r", "where": "inputs/b/x.tt:1",
              "line": 'ALLOWLOSS 2025-01-08 ZZQ reason="r"'}
        res = _engine(book, loss_overrides=(ov,))
        self.assertEqual(self._row(res, 'b', '2025-01-08'), (-300.0, 200.0, -100.0))
        b_sale, c_sale = book[2].id, book[3].id
        assert_rule(self, res, b_sale, R.US_WASH_25, record=override_record(b_sale))
        assert_rule(self, res, b_sale, R.US_WASH_22, record=b_sale)
        assert_rule(self, res, b_sale, R.US_WASH_09, record=book[4].id)
        assert_rule(self, res, c_sale, R.US_WASH_22, record=closed_record(book[1].id))

    @rule(R.US_WASH_01, R.US_WASH_22, R.US_WASH_09)
    def test_a_row_with_its_own_disallowance_and_a_retest_keeps_both_records(self):
        # b sells two lots on 01-08: the 2024 lot at a loss of 200, replaced
        # by b's 01-20 purchase (its own §1091(a) disallowance), and the
        # 01-02 lot at a gain of 100. c's 01-10 loss of 200 lands on that
        # gain (b's 01-02 purchase, sold before it): a loss of 100, retested
        # and replaced by the rest of the 01-20 purchase. The row's own
        # disallowance names US-WASH-01 and the retest's, kept apart,
        # US-WASH-22 (backlog TJ-4412: the retest's overwrote the own).
        book = [tx('BUYSELL', '2024-12-02', 'ZZQ', 10, 1200.0, account='c'),
                tx('BUYSELL', '2024-10-01', 'ZZQ', 10, 1300.0, account='b'),
                tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
                tx('BUYSELL', '2025-01-08', 'ZZQ', -20, 2200.0, account='b'),
                tx('BUYSELL', '2025-01-10', 'ZZQ', -10, 1000.0, account='c'),
                tx('BUYSELL', '2025-01-20', 'ZZQ', 20, 2000.0, account='b')]
        res = _engine(book)
        b_sale, c_sale = book[3].id, book[4].id
        self.assertEqual(
            sorted((round(t['raw_gain'], 6), round(t['disallowed_amount'], 6),
                    round(t['gain'], 6)) for t in _rows(res, 'b')
                   if t['date'] == '2025-01-08'),
            [(-200.0, 200.0, 0.0), (-100.0, 100.0, 0.0)])
        self.assertEqual(
            sorted((w['loss_tx_id'], w['disallowed_amount'], w.get('retest_of'))
                   for w in res['wash_sales'] if w['loss_tx_id'] == b_sale),
            [(b_sale, 100.0, c_sale), (b_sale, 200.0, None)])
        assert_rule(self, res, b_sale, R.US_WASH_01, record=b_sale)
        assert_rule(self, res, b_sale, R.US_WASH_22, record=retest_record(b_sale))
        assert_rule(self, res, b_sale, R.US_WASH_09, record=book[5].id)
        assert_rule(self, res, c_sale, R.US_WASH_22, record=closed_record(book[2].id))

    @rule(R.US_WASH_22, R.US_WASH_09)
    def test_a_purchase_partly_sold_before_the_loss_keeps_both_records(self):
        # b buys 100 inside the window of a's loss and sells 50 of them
        # before it: the loss takes the 50 still held (a basis add) and
        # the 50 sold (an add to b's sale), one purchase, two records.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 100, 5000.0, account='a'),
                tx('BUYSELL', '2025-03-03', 'ZZQ', 100, 3000.0, account='b'),
                tx('BUYSELL', '2025-03-05', 'ZZQ', -50, 1600.0, account='b'),
                tx('BUYSELL', '2025-03-10', 'ZZQ', -100, 3000.0, account='a')]
        res = _engine(book)
        loss = _sale(res, 'a', '2025-03-10')
        self.assertEqual(sorted((w['match_qty'], 'sold_before_loss' in w)
                                for w in loss['wash_replacements']),
                         [(50.0, False), (50.0, True)])
        assert_rule(self, res, loss['id'], R.US_WASH_09, record=book[1].id)
        assert_rule(self, res, loss['id'], R.US_WASH_22, record=closed_record(book[1].id))

    @rule(R.US_WASH_22, R.US_WASH_09)
    def test_the_sales_new_loss_is_deferred_onto_a_later_purchase(self):
        # The long mirror: b's sale at a gain of 100 takes c's 200 and is
        # a loss of 100; c's purchase of 01-15 replaces it: its 10 carry
        # the 100 (basis 1150).
        res = _engine(self.LONG + [
            tx('BUYSELL', '2025-01-15', 'ZZQ', 10, 1050.0, account='c')])
        self.assertEqual(self._row(res, 'b', '2025-01-08'),
                         (-100.0, 100.0, 0.0))
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(10.0, 1150.0, 100.0)])

    @rule(R.US_WASH_22)
    def test_with_no_replacement_the_new_loss_stays_allowed(self):
        # No purchase in the 61 days of b's sale but the one it sold: the
        # 100 stays an allowed loss, and nothing is said. A sale the add
        # leaves at a gain is not tested at all.
        res = _engine(self.LONG)
        self.assertEqual(self._row(res, 'b', '2025-01-08'),
                         (-100.0, 0.0, -100.0))
        # One line a run: the count, and the order the rows were tested
        # in, an interim (backlog TJ-3453, TJ-3444).
        self.assertEqual(res['_stderr'].count(
            "note: 1 earlier sale(s) or cover(s) (ZZQ in b) that a wash "
            "sale's disallowed loss turned into a loss were tested for a "
            "wash sale of their own, 0 with a part disallowed. Each was "
            "tested after the losses already matched, not in the order of "
            "the sales (Reg. §1.1091-1)"), 1)
        self.assertEqual(res['_stderr'].count('\n'), 1)
        res = _engine(self.LONG[:2] + [
            tx('BUYSELL', '2025-01-08', 'ZZQ', -10, 1300.0, account='b'),
            self.LONG[3],
            tx('BUYSELL', '2025-01-15', 'ZZQ', 10, 1050.0, account='c')])
        self.assertEqual(self._row(res, 'b', '2025-01-08'),
                         (100.0, 0.0, 100.0))
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(10.0, 1050.0, 0.0)])
        self.assertEqual(res['_stderr'], '')

    @rule(R.US_WASH_22, R.US_WASH_20)
    def test_a_cascade_of_two(self):
        # b's new loss of 100 (01-08) is replaced by a's purchase of
        # 01-09, sold that day at a gain of 50 before c's loss: the 100
        # goes to a's sale, now a loss of 50, which is tested in turn
        # and deferred onto d's purchase of 01-20.
        res = _engine(self.LONG[:3] + [
            tx('BUYSELL', '2025-01-09', 'ZZQ', 10, 1000.0, account='a',
               time='10:00:00'),
            tx('BUYSELL', '2025-01-09', 'ZZQ', -10, 1050.0, account='a',
               time='15:00:00'),
            self.LONG[3],
            tx('BUYSELL', '2025-01-20', 'ZZQ', 10, 1000.0, account='d')])
        self.assertEqual(self._row(res, 'c', '2025-01-10'),
                         (-200.0, 200.0, 0.0))
        self.assertEqual(self._row(res, 'b', '2025-01-08'),
                         (-100.0, 100.0, 0.0))
        self.assertEqual(self._row(res, 'a', '2025-01-09'),
                         (-50.0, 50.0, 0.0))
        self.assertEqual(_lots(res, 'd', 'ZZQ'), [(10.0, 1050.0, 50.0)])
        # The rows tested are listed as such; the ones with a part
        # disallowed are named apart only when not all (backlog TJ-3477).
        self.assertIn("note: 2 earlier sale(s) or cover(s) (ZZQ in a, ZZQ "
                      "in b) that a wash sale's disallowed loss turned into "
                      "a loss were tested for a wash sale of their own, 2 "
                      "with a part disallowed. Each", res['_stderr'])
        self.assertNotIn('warning', res['_stderr'])

    @rule(R.US_WASH_22)
    def test_the_figures_do_not_follow_how_the_sales_lots_split(self):
        # c sells 20 at a loss of 400, held as one lot or as two of 10:
        # the rows the sale's matches turned into losses are tested once
        # the whole sale is booked, so c's loss takes b's sold 10 and d's
        # held 10 either way and b's new loss of 100 finds nothing left
        # (backlog TJ-3462: tested lot by lot, with two lots b's loss
        # took d's units before c's second lot could).
        def book(lots):
            return lots + self.LONG[1:3] + [
                tx('BUYSELL', '2025-01-09', 'ZZQ', 10, 1000.0, account='d'),
                tx('BUYSELL', '2025-01-10', 'ZZQ', -20, 2000.0, account='c')]
        one = _engine(book([tx('BUYSELL', '2024-12-02', 'ZZQ', 20, 2400.0,
                               account='c')]))
        two = _engine(book([tx('BUYSELL', '2024-12-02', 'ZZQ', 10, 1200.0,
                               account='c'),
                            tx('BUYSELL', '2024-12-03', 'ZZQ', 10, 1200.0,
                               account='c')]))
        for res in (one, two):
            self.assertAlmostEqual(
                sum(t['gain'] for t in res['transactions'] if t.get('qty')),
                -100.0, places=6)
            self.assertEqual(self._row(res, 'b', '2025-01-08'),
                             (-100.0, 0.0, -100.0))
            self.assertEqual([lot[1:] for lot in _lots(res, 'd', 'ZZQ')],
                             [(1200.0, 200.0)])

    @rule(R.US_WASH_20)
    def test_units_a_later_loss_already_took_stay_with_it(self):
        # The order of the losses. b's sale of 01-08 is a gain of 100; d
        # buys 10 on 01-09 and holds them; e sells 20 (bought 2024-11-01
        # for 2600) on 01-09 at a loss of 600. In the order acquired
        # (US-WASH-20) e's loss takes b's sold 10 (300 onto b's sale,
        # now a loss of 200) and d's held 10 (300 deferred). b's loss
        # exists only then: d's units are spent, on a loss dated after
        # b's, and they stay with it — b's 200 is allowed. With 20
        # bought by d, e takes 10 and b's new loss the other 10.
        def book(d_buys):
            return ([tx('BUYSELL', '2024-11-01', 'ZZQ', 20, 2600.0,
                        account='e')] + self.LONG[1:3] + [
                    tx('BUYSELL', '2025-01-09', 'ZZQ', d_buys,
                       100.0 * d_buys, account='d', time='10:00:00'),
                    tx('BUYSELL', '2025-01-09', 'ZZQ', -20, 2000.0,
                       account='e', time='15:00:00')])
        res = _engine(book(10))
        self.assertEqual(self._row(res, 'e', '2025-01-09'),
                         (-600.0, 600.0, 0.0))
        self.assertEqual(self._row(res, 'b', '2025-01-08'),
                         (-200.0, 0.0, -200.0))
        self.assertEqual(_lots(res, 'd', 'ZZQ'), [(10.0, 1300.0, 300.0)])
        res = _engine(book(20))
        self.assertEqual(self._row(res, 'b', '2025-01-08'),
                         (-200.0, 200.0, 0.0))
        self.assertEqual(_lots(res, 'd', 'ZZQ'),
                         [(10.0, 1300.0, 300.0), (10.0, 1200.0, 200.0)])


if __name__ == '__main__':
    unittest.main()
