"""US engine branches the suite ran but did not check (TJ-1896, the
mutation triage's second batch, 2026-11-10): own-account moves and
listing journals, ADJUST rows, the buy's replacement blocks, the row walk,
the replacement scan, the move pairing, dividends and the finish. Each test
pins a figure (a lot's quantity or basis, a deferral, a gain, a disallowed
loss, a row's date; where a warning is the only output, the warning) that a
surviving mutant of the branch changes. Synthetic data only; the tickers
(ZZQ, QZRX, ZZQ.TO, ZZQ.US) and accounts ('b', 'c', 'ira') are made up."""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
from _support.engine_rules import assert_rule

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.lib.row_types import LISTING_JOURNAL_TYPE, LOT_MOVE_TYPE, OPENING_TYPE


def _engine(book, **kw):
    """The US engine over `book`, per-account basis unless given, every
    lot at the end of 2025 (`lots_at`); stderr captured as `_stderr`."""
    kw.setdefault('per_account_basis', True)
    kw.setdefault('lots_at', '2025-12-31')
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = USATaxRules().compute_gains(list(book), **kw)
    r['_stderr'] = err.getvalue()
    return r


def _lots(res, account, symbol):
    """(qty, basis, deferred) of each lot held, FIFO order."""
    return [(round(lot['qty'], 9), round(lot['basis'], 6), lot['deferred'])
            for lot in res['lots_at']
            if lot['account'] == account and lot['symbol'] == symbol]


def _rows(res, account, date=None):
    """(qty, cost, gain, disallowed) of each sale row of `account`."""
    return [(t['qty'], round(t['cost'], 6), round(t['gain'], 6),
             round(t['disallowed_amount'], 6))
            for t in res['transactions']
            if t['account'] == account and t.get('qty')
            and (date is None or t['date'] == date)]


def _deemed(res):
    return [t for t in res['transactions'] if t.get('deemed')]


def _move(date, sym, qty, src, dst, n=1, qty_in=None):
    """A custody move of `qty` from `src` to `dst` (the two legs the
    own-move stage writes); `qty_in`: an in leg of another size."""
    desc = f"own-account move #{n}: {src} -> {dst} (test)"
    return [tx('TRANSFER', date, sym, -qty, 0.0, account=src,
               type=LOT_MOVE_TYPE, description=desc, time='00:00:00'),
            tx('TRANSFER', date, sym, qty if qty_in is None else qty_in,
               0.0, account=dst, type=LOT_MOVE_TYPE, description=desc,
               time='00:00:00')]


def _journal(date, src_sym, dst_sym, qty, account, pair='j1'):
    """A listing journal of `qty` from `src_sym` to `dst_sym`."""
    return [tx('TRANSFER', date, src_sym, -qty, 0.0, account=account,
               type=LISTING_JOURNAL_TYPE, journal_pair=pair),
            tx('TRANSFER', date, dst_sym, qty, 0.0, account=account,
               type=LISTING_JOURNAL_TYPE, journal_pair=pair)]


def _two_lots():
    """b: 6 QZRX for 240 on 2023-01-10, 15 for 900 on 2024-03-01."""
    return [tx('BUYSELL', '2023-01-10', 'QZRX', 6, 240.0, account='b'),
            tx('BUYSELL', '2024-03-01', 'QZRX', 15, 900.0, account='b')]


def _washed():
    """b buys 10 ZZQ for 500 on 2025-01-02, sells them for 400 on
    2025-02-03 and buys 10 back for 400 on 2025-02-10: the rebuy carries
    the disallowed 100 (holding period from 2025-01-09)."""
    return [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 500.0, account='b'),
            tx('BUYSELL', '2025-02-03', 'ZZQ', -10, 400.0, account='b'),
            tx('BUYSELL', '2025-02-10', 'ZZQ', 10, 400.0, account='b')]


# ------------------------------------------------------ _us_move_long_lots
class TestOwnMoveOfAWholeLotLeavesNoEmptyLot(unittest.TestCase):
    """`_us_move_long_lots`, a move of exactly the sender's first lot: the
    lot goes whole (core: `if lot['qty'] <= left + run.epsilon`), not as
    a part that leaves an empty lot behind to date the sender's position
    and close as a row of its own."""

    @rule(R.US_BASIS_05)
    def test_the_sender_keeps_only_the_second_lot(self):
        book = _two_lots() + _move('2025-03-03', 'QZRX', 6, 'b', 'c')
        res = _engine(book)
        [inv] = [i for i in res['inventory'] if i.get('account') == 'b']
        self.assertEqual((inv['qty'], inv['position_start_date'],
                          inv['holding_start_date']),
                         (15.0, '2024-03-01', '2024-03-01'))
        self.assertEqual(_lots(res, 'c', 'QZRX'), [(6.0, 240.0, 0.0)])
        res = _engine(book + [tx('BUYSELL', '2025-05-01', 'QZRX', -15,
                                 1200.0, account='b')])
        self.assertEqual(_rows(res, 'b'), [(15.0, 900.0, 300.0, 0.0)])


# --------------------------------------------------------- _us_carry_lots
class TestJournalOfAWholeLotLeavesNoEmptyLot(unittest.TestCase):
    """`_us_carry_lots`, a listing journal of exactly the first lot: the
    lot goes whole (core: `if lot['qty'] <= left + run.epsilon`), not as
    a part that leaves an empty lot on the old listing to date it and
    close as a row of its own."""

    @rule(R.US_XLIST_01)
    def test_the_old_listing_keeps_only_the_second_lot(self):
        book = _two_lots() + _journal('2025-03-03', 'QZRX', 'QZRX.X', 6,
                                      'b')
        res = _engine(book)
        [inv] = [i for i in res['inventory'] if i['symbol'] == 'QZRX']
        self.assertEqual((inv['qty'], inv['position_start_date'],
                          inv['holding_start_date']),
                         (15.0, '2024-03-01', '2024-03-01'))
        res = _engine(book + [tx('BUYSELL', '2025-05-01', 'QZRX', -15,
                                 1200.0, account='b')])
        self.assertEqual(_rows(res, 'b'), [(15.0, 900.0, 300.0, 0.0)])


# ------------------------------------------------------ _us_walk_lot_move
class TestMoveLegsThatDoNotPairMoveNothing(unittest.TestCase):
    """`_us_walk_lot_move`, a leg with no pair whose other account is in
    the book (the legs' quantities differ): not booked, with an
    ATTENTION line (core: `if _peer is None or _peer in _book_accounts`);
    only a book without the other account moves the lots out alone."""

    @rule(R.US_BASIS_05)
    def test_the_sender_keeps_its_lot_and_the_receiver_gets_none(self):
        book = ([tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b')]
                + _move('2025-03-03', 'ZZQ', 10, 'b', 'c', qty_in=8))
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ'), [(10.0, 100.0, 0.0)])
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [])
        self.assertIn('has no matching leg', res['_stderr'])


# --------------------------------------------------------- _us_walk_adjust
class TestReturnOfCapitalLastLotTakesTheRemainder(unittest.TestCase):
    """`_us_walk_adjust`, the pro-rata spread: every lot but the last takes
    amount x qty / open qty, the last the rest, so the lots give up
    exactly the amount (core: `if i == len(lots) - 1`). Two fractional
    lots whose quantities do not sum exactly in binary (0.1 + 0.2)."""

    @rule(R.US_ROC_01)
    def test_the_lots_keep_exactly_the_cost_less_the_distribution(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 0.1, 1.0, account='b'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', 0.2, 2.0, account='b'),
                tx('ADJUST', '2025-03-03', 'ZZQ', 0, -2.5, account='b')]
        lots = _engine(book)['lots_at']
        self.assertEqual(sum(lot['basis'] for lot in lots), 0.5)


class TestZeroAdjustLeavesTheWashAdd(unittest.TestCase):
    """`_us_walk_adjust`, the cap on a lot's §1091(d) add: only a basis
    INCREASE (share > 0, not a reversal) caps the add at what a return of
    capital left of the basis (core: `if (share > 0 and _lwd > 0`); a $0
    row raises nothing, so a later reversal (type=roc) still brings the
    whole add back."""

    @rule(R.US_ROC_01)
    @rule(R.US_WASH_09)
    def test_the_reversal_restores_the_whole_add(self):
        # The replacement lot: 10 at 400 + the 100 disallowed = 500. A
        # return of capital of 450 leaves 50 of basis (the add still
        # recorded as 100); a $0 row; the 450 reversed: 500 again, 100
        # of it the add, recovered by the sale.
        book = _washed() + [
            tx('ADJUST', '2025-03-03', 'ZZQ', 0, -450.0, account='b'),
            tx('ADJUST', '2025-03-10', 'ZZQ', 0, 0.0, account='b'),
            tx('ADJUST', '2025-03-17', 'ZZQ', 0, 450.0, account='b',
               type='roc')]
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ'), [(10.0, 500.0, 100.0)])
        res = _engine(book + [tx('BUYSELL', '2025-04-01', 'ZZQ', -10, 600.0,
                                 account='b')])
        [sale] = [t for t in res['transactions'] if t['date'] == '2025-04-01']
        self.assertAlmostEqual(sale['gain'], 100.0, places=6)
        self.assertAlmostEqual(sale['deferred_loss_recovered'], 100.0,
                               places=6)


class TestPooledExcessIsDatedByALotWithAnExcess(unittest.TestCase):
    """`_us_walk_adjust`, the §301(c)(3) excess: only a lot driven BELOW
    zero has one (core: `if lot['cost_basis'] < 0`); a lot whose basis
    the return of capital takes exactly to zero has none, so it does not
    date the pooled row of sub-cent excesses."""

    @rule(R.US_ROC_02)
    def test_the_pool_is_dated_by_the_first_lot_past_its_basis(self):
        # 10 a share returned on three 1-share lots: 10.00 (to zero),
        # 9.997 and 9.997 (0.003 past each): one pooled row of 0.006,
        # dated the 2025-01-03 lot.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 1, 10.0, account='b'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', 1, 9.997, account='b'),
                tx('BUYSELL', '2025-01-06', 'ZZQ', 1, 9.997, account='b'),
                tx('ADJUST', '2025-03-03', 'ZZQ', 0, -30.0, account='b')]
        [row] = _deemed(_engine(book))
        self.assertAlmostEqual(row['gain'], 0.006, places=9)
        self.assertEqual(row['acquired_date'], '2025-01-03')


class TestRejoinedLotPiecesPoolTheirExcess(unittest.TestCase):
    """`_us_walk_adjust`, the excess by lot (`deemed_lots`, TJ-1218): two
    pieces of one lot (split by a move out and back) add their excesses
    under the lot's id (core: `_xl.get(str(lot.get('id') or ''), 0.0) +
    float(excess)`)."""

    @rule(R.US_ROC_02)
    @rule(R.US_BASIS_05)
    def test_each_lot_of_the_day_has_its_whole_excess(self):
        # Two lots of 10 for 100 bought 2025-01-02; 5 of the first moved
        # to c and back. 15 a share returned on the 20: 5 past each
        # share's basis of 10, 50 a lot, one row of 100.
        book = ([tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b',
                    time='10:00:00'),
                 tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b',
                    time='11:00:00')]
                + _move('2025-02-03', 'ZZQ', 5, 'b', 'c', n=1)
                + _move('2025-02-04', 'ZZQ', 5, 'c', 'b', n=2)
                + [tx('ADJUST', '2025-03-03', 'ZZQ', 0, -300.0,
                      account='b')])
        [row] = _deemed(_engine(book))
        self.assertAlmostEqual(row['gain'], 100.0, places=6)
        self.assertEqual(row['deemed_lots'],
                         {book[0].id: 50.0, book[1].id: 50.0})


class TestOneLotsExcessNamesNoLots(unittest.TestCase):
    """`_us_walk_adjust`, the by-lot excess (`deemed_lots`) is kept only
    for a row of two or more lots of one date (core: `if
    (len(_date_ids...) < 2 or ... or not _by_lot or ...)`); a single
    lot's row has none."""

    @rule(R.US_ROC_02)
    def test_a_single_lot_row_has_no_deemed_lots(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b'),
                tx('ADJUST', '2025-03-03', 'ZZQ', 0, -150.0, account='b')]
        [row] = _deemed(_engine(book))
        self.assertAlmostEqual(row['gain'], 50.0, places=6)
        self.assertNotIn('deemed_lots', row)


class TestDeemedGainSettlesWithItsRow(unittest.TestCase):
    """`_us_walk_adjust`, the deemed row's settlement date is the ADJUST
    row's own (core: `'date_settle': tx.date_settle or tx.date`)."""

    @rule(R.US_ROC_02)
    def test_the_settlement_date_is_the_rows(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b'),
                tx('ADJUST', '2025-03-03', 'ZZQ', 0, -150.0, account='b',
                   settle='2025-03-05')]
        [row] = _deemed(_engine(book))
        self.assertEqual((row['date'], row['date_settle']),
                         ('2025-03-03', '2025-03-05'))


# ------------------------------------------------------------ _us_walk_buy
class TestRebuyASliverLargerIsOneReplacementBlock(unittest.TestCase):
    """`_us_walk_buy`, the replacement blocks of a buy matched before it
    is booked: a block within the engine's epsilon (1e-8 units) of the
    whole buy takes all of it (core: `if _left - _bq <= run.epsilon: _bq =
    _left`), so the sliver is not lost from the lot."""

    @rule(R.US_WASH_09)
    def test_the_lot_holds_every_share_bought(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 500.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'ZZQ', -10, 400.0, account='b'),
                tx('BUYSELL', '2025-02-10', 'ZZQ', 10.000000005, 400.0,
                   account='b')]
        res = _engine(book)
        [lot] = [lot for lot in res['lots_at'] if lot['symbol'] == 'ZZQ']
        self.assertEqual((lot['qty'], lot['basis'], lot['deferred']),
                         (10.000000005, 500.0, 100.0))


class TestJournalRestatesTheAddAtTheRate(unittest.TestCase):
    """`_us_carry_lots`, a listing journal into another currency's line:
    the lot's §1091(d) add is restated by the same factor as its basis
    (core: `lot['wash_deferred'] = (lot.get('wash_deferred', ...) * f)`),
    the in-leg's price (the source's currency over the receiver's)."""

    @rule(R.US_XLIST_01)
    @rule(R.US_WASH_09)
    def test_the_add_is_seven_tenths_like_the_basis(self):
        # The ZZQ.TO replacement lot: 10 at 400 + the 100 disallowed (in
        # CAD); journaled to ZZQ.US at 0.7: basis 350, 70 of it the add.
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ.TO', 10, 500.0,
                   account='b', currency='CAD'),
                tx('BUYSELL', '2025-02-03', 'ZZQ.TO', -10, 400.0,
                   account='b', currency='CAD'),
                tx('BUYSELL', '2025-02-10', 'ZZQ.TO', 10, 400.0,
                   account='b', currency='CAD'),
                tx('TRANSFER', '2025-03-03', 'ZZQ.TO', -10, 0.0, account='b',
                   currency='CAD', type=LISTING_JOURNAL_TYPE,
                   journal_pair='j1'),
                tx('TRANSFER', '2025-03-03', 'ZZQ.US', 10, 0.0, account='b',
                   currency='USD', price=0.7, type=LISTING_JOURNAL_TYPE,
                   journal_pair='j1')]
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ.TO'), [])
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(10.0, 350.0, 70.0)])


# -------------------------------------------------------- _us_book_journal
class TestJournalBesideAShortMovesTheLong(unittest.TestCase):
    """`_us_book_journal`, which way the lots go: a short's lots move back
    only when the out-leg's listing holds NO long (core: `short = a_long
    <= run.epsilon and b_short > run.epsilon`); with a long there the
    long's lots move, and cover first the short the in-leg's listing
    holds, as a purchase there would — the owner's ruling on backlog
    TJ-2738 (2026-11-11: net them; a broker's statement shows the net).
    The cover costs the delivered lots' basis (core: `_us_deliver_to_short`).
    Before the ruling the books held the long 10 beside the short 5."""

    @staticmethod
    def _book(qty=10, short=(5, 60.0)):
        return ([tx('BUYSELL', '2025-01-02', 'ZZQ.TO', 10, 100.0,
                    account='b'),
                 tx('BUYSELL', '2025-01-03', 'ZZQ.US', -short[0], short[1],
                    account='b')]
                + _journal('2025-03-03', 'ZZQ.TO', 'ZZQ.US', qty, 'b'))

    @rule(R.US_XLIST_01)
    def test_the_long_covers_the_short_and_the_rest_moves(self):
        # b: 10 ZZQ.TO for 100, short 5 ZZQ.US for 60; the 10 journaled
        # to ZZQ.US. The first 5 (basis 50) cover the short: a gain of 10
        # on the journal's date; the other 5 move with their basis and
        # date. ZZQ.TO holds nothing; no short is left.
        res = _engine(self._book())
        self.assertEqual(_lots(res, 'b', 'ZZQ.TO'), [])
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(5.0, 50.0, 0.0)])
        [long_lot] = [lot for lot in res['lots_at']
                      if lot['symbol'] == 'ZZQ.US']
        self.assertEqual(long_lot['date'], '2025-01-02')
        [cover] = [t for t in res['transactions'] if t.get('qty')]
        self.assertEqual(
            (cover['symbol'], cover['date'], cover['direction'],
             cover['qty'], cover['acquired_date'], cover['term']),
            ('ZZQ.US', '2025-03-03', 'SHORT', 5.0, '2025-01-03',
             'SHORT_TERM'))
        self.assertAlmostEqual(cover['cost'], -60.0, places=6)
        self.assertAlmostEqual(cover['proceeds'], -50.0, places=6)
        self.assertAlmostEqual(cover['gain'], 10.0, places=6)
        self.assertNotIn('conservation', res['_stderr'])

    @rule(R.US_XLIST_01)
    def test_a_smaller_journal_covers_part_of_the_short(self):
        # 4 of the 10 journaled into a short of 5 for 60: they cover 4
        # (proceeds 48, basis 40: a gain of 8); 1 stays short for 12 and
        # 6 stay in ZZQ.TO at 60.
        res = _engine(self._book(qty=4))
        self.assertEqual(_lots(res, 'b', 'ZZQ.TO'), [(6.0, 60.0, 0.0)])
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(-1.0, -12.0, 0.0)])
        self.assertEqual(_rows(res, 'b'), [(4.0, -48.0, 8.0, 0.0)])

    @rule(R.US_XLIST_01)
    def test_units_the_source_did_not_hold_cover_the_rest_of_the_short(self):
        # b holds 4 ZZQ.TO (40) and is short 5 ZZQ.US for 60; 10 are
        # journaled. The 4 cover 4 (a gain of 8); of the 6 with no
        # purchase in this book, 1 covers the rest of the short at an
        # unknown cost, as a purchase would (backlog TJ-3439), and 5 are
        # the long ZZQ.US holds, beside no short. ZZQ.TO is short the 6.
        book = ([tx('BUYSELL', '2025-01-02', 'ZZQ.TO', 4, 40.0, account='b'),
                 tx('BUYSELL', '2025-01-03', 'ZZQ.US', -5, 60.0, account='b')]
                + _journal('2025-03-03', 'ZZQ.TO', 'ZZQ.US', 10, 'b'))
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(5.0, 0.0, 0.0)])
        self.assertEqual(_lots(res, 'b', 'ZZQ.TO'), [(-6.0, 0.0, 0.0)])
        # (the source is short the units it did not hold: no share-count
        # warning, as for a move's: backlog TJ-3465)
        self.assertNotIn('conservation', res['_stderr'])
        self.assertEqual(
            [(t['qty'], round(t['cost'], 6), round(t['proceeds'], 6),
              bool(t.get('tainted')))
             for t in res['transactions'] if t.get('qty')],
            [(4.0, -48.0, -40.0, False), (1.0, -12.0, 0.0, True)])

    @rule(R.US_XLIST_01)
    @rule(R.US_WASH_05, R.US_WASH_27)
    def test_a_cover_at_a_loss_is_a_cover_for_the_wash_rule(self):
        # The short of 5 was sold for 30: the 5 delivered (basis 50)
        # close it at a loss of 20, and b shorts 5 more on 2025-03-10,
        # inside the window: the 20 is disallowed onto the new short.
        res = _engine(self._book(short=(5, 30.0)) + [
            tx('BUYSELL', '2025-03-04', 'ZZQ.US', -5, 55.0, account='b'),
            tx('BUYSELL', '2025-03-10', 'ZZQ.US', -5, 45.0, account='b')])
        [cover] = [t for t in res['transactions']
                   if t.get('direction') == 'SHORT']
        self.assertAlmostEqual(cover['raw_gain'], -20.0, places=6)
        self.assertAlmostEqual(cover['disallowed_amount'], 20.0, places=6)
        # The cover's disallowance names the short side's rule, the new
        # short's proceeds cut its own (PLAN step 11; the owner's ruling
        # on TJ-4153, question 5).
        [rep] = cover['wash_replacements']
        assert_rule(self, res, cover['id'], R.US_WASH_05, record=cover['id'])
        assert_rule(self, res, cover['id'], R.US_WASH_27, record=rep['tx_id'])


class TestMoveIntoAShortAccountClosesTheShort(unittest.TestCase):
    """`_us_walk_lot_move`, a long moved into an account short the
    security: the moved shares close the short first, on the move's date
    at their own basis — a cover row in the tax books — and only the rest
    move as lots (the owner's ruling on backlog TJ-3424, 2026-11-13: net
    them; it replaces TJ-3436's interim Warning). Every run names the
    cover. The cover's term follows the lot delivered: long-term only
    when it was held more than one year on the day of the short sale
    (§1233(b), §1233(d); Reg. §1.1233-1(a)(3)) (core:
    `_us_deliver_to_short`, `_us_delivered_long`)."""

    @staticmethod
    def _book(moved=10, short=5, bought='2025-01-02', sold_for=60.0):
        return ([tx('BUYSELL', bought, 'ZZQ', 10, 100.0, account='b'),
                 tx('BUYSELL', '2025-01-03', 'ZZQ', -short, sold_for,
                    account='c')]
                + _move('2025-03-03', 'ZZQ', moved, 'b', 'c'))

    @staticmethod
    def _covers(res):
        return [(t['account'], t['date'], t['direction'], t['qty'],
                 round(t['cost'], 6), round(t['proceeds'], 6),
                 round(t['gain'], 6), t['term'])
                for t in res['transactions'] if t.get('qty')]

    @rule(R.US_BASIS_05)
    def test_a_move_larger_than_the_short_covers_it_and_the_rest_moves(self):
        # b: 10 ZZQ for 100; c short 5 for 60; the 10 moved to c. The
        # first 5 (basis 50) close the short: a gain of 10 on the move's
        # date, short-term (the lot was bought the day before the short
        # sale: §1233(b)(1)); the other 5 arrive with their basis and
        # date. Before the ruling c held 10 long beside 5 short.
        res = _engine(self._book())
        self.assertEqual(self._covers(res), [
            ('c', '2025-03-03', 'SHORT', 5.0, -60.0, -50.0, 10.0,
             'SHORT_TERM')])
        self.assertEqual(_lots(res, 'b', 'ZZQ'), [])
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(5.0, 50.0, 0.0)])
        [lot] = [x for x in res['lots_at'] if x['symbol'] == 'ZZQ']
        self.assertEqual(lot['date'], '2025-01-02')
        # The line: what closed the short and what moved, the term, the
        # figure as booked at the move (a later wash-sale add can change
        # the row: backlog TJ-3463), and that the term read the
        # delivered lot only (TJ-3449).
        self.assertEqual(res['_stderr'].count(
            'ZZQ: 10 moved from b to c on 2025-03-03, where c was short 5: '
            '5 of them close the short that day at their own basis (a '
            'cover on Form 8949, short-term: a gain of 10.00, as booked at '
            'the move, before any later wash-sale add), and the other 5 '
            'move as lots. The term reads the lot delivered only (§1233): '
            'your other holdings of the security were not read for it. A '
            'constructive sale while the account held both (§1259) is not '
            'modelled.'), 1)
        self.assertNotIn('conservation', res['_stderr'])
        # The row says how the short was closed, and its date acquired
        # is the delivered lot's (Form 8949 column (b): TJ-3451); a
        # short-term cover's days held are 0 (§1233(b)(2)).
        [row] = [t for t in res['transactions'] if t.get('qty')]
        self.assertEqual((row['closed_by_delivery'], row['acquired_date'],
                          row['days_held']), (True, '2025-01-02', 0))

    @rule(R.US_BASIS_05)
    def test_a_move_equal_to_the_short_leaves_nothing(self):
        res = _engine(self._book(short=10, sold_for=120.0))
        self.assertEqual(self._covers(res), [
            ('c', '2025-03-03', 'SHORT', 10.0, -120.0, -100.0, 20.0,
             'SHORT_TERM')])
        self.assertEqual(_lots(res, 'b', 'ZZQ') + _lots(res, 'c', 'ZZQ'), [])

    @rule(R.US_BASIS_05)
    def test_a_move_smaller_than_the_short_covers_part_of_it(self):
        # 4 of b's 10 moved into a short of 5 for 60: they cover 4
        # (proceeds 48, basis 40); 1 stays short for 12, 6 stay in b.
        res = _engine(self._book(moved=4))
        self.assertEqual(self._covers(res), [
            ('c', '2025-03-03', 'SHORT', 4.0, -48.0, -40.0, 8.0,
             'SHORT_TERM')])
        self.assertEqual(_lots(res, 'b', 'ZZQ'), [(6.0, 60.0, 0.0)])
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(-1.0, -12.0, 0.0)])

    @rule(R.US_BASIS_05, R.US_HOLD_01)
    def test_a_lot_held_over_a_year_at_the_short_sale_is_long_term(self):
        # The lot bought 2023-06-01 was held more than a year when c
        # sold short on 2025-01-03: the cover's gain is long-term (the
        # delivered property's holding period, Reg. §1.1233-1(a)(3)), and
        # so is a loss (§1233(d)). Bought 2024-06-03 (seven months
        # before the short sale, nine by the move) both are short-term.
        for bought, sold_for, term in (
                ('2023-06-01', 60.0, 'LONG_TERM'),
                ('2023-06-01', 30.0, 'LONG_TERM'),
                ('2024-06-03', 60.0, 'SHORT_TERM'),
                ('2024-06-03', 30.0, 'SHORT_TERM')):
            with self.subTest(bought=bought, sold_for=sold_for):
                res = _engine(self._book(bought=bought, sold_for=sold_for))
                [cover] = self._covers(res)
                self.assertEqual(cover[6:], (sold_for - 50.0, term))
                # The dates read as the term does (backlog TJ-3451): the
                # delivered lot's purchase date, and its days to the
                # close when long-term (2023-06-01 to 2025-03-03).
                [row] = [t for t in res['transactions'] if t.get('qty')]
                self.assertEqual(
                    (row['acquired_date'], row['days_held']),
                    (bought, 641 if term == 'LONG_TERM' else 0))
                self.assertIn(f"a cover on Form 8949, "
                              f"{'long' if term == 'LONG_TERM' else 'short'}"
                              f"-term: ", res['_stderr'])

    @rule(R.US_BASIS_05, R.US_WASH_05)
    def test_a_cover_at_a_loss_is_tested_for_a_wash_sale(self):
        # The short of 5 was sold for 30: the 5 delivered (basis 50)
        # close it at a loss of 20. c sells the 5 that arrived (flat) and
        # shorts 5 more on 2025-03-10, inside the window: the 20 is
        # disallowed onto the new short, and the Warning says a gain or
        # loss of 0.
        res = _engine(self._book(sold_for=30.0) + [
            tx('BUYSELL', '2025-03-04', 'ZZQ', -5, 55.0, account='c'),
            tx('BUYSELL', '2025-03-10', 'ZZQ', -5, 45.0, account='c')])
        [cover] = [t for t in res['transactions']
                   if t.get('direction') == 'SHORT']
        self.assertAlmostEqual(cover['raw_gain'], -20.0, places=6)
        self.assertAlmostEqual(cover['disallowed_amount'], 20.0, places=6)
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(-5.0, -25.0, 20.0)])
        self.assertIn('a loss of 20.00, 20.00 of it disallowed as a wash '
                      'sale, as booked at the move', res['_stderr'])

    @rule(R.US_BASIS_05)
    def test_units_the_sender_did_not_hold_cover_the_rest_of_the_short(self):
        # b holds 3 of the 10 moved and c is short 5: the 3 close 3, and
        # 2 of the 7 with no purchase in the books close the other 2 at
        # an unknown cost, as a journal's do (backlog TJ-3456); nothing
        # moves as lots, and the line says so. b is short those 2 (they
        # left it), so the share count holds and no conservation line
        # is printed; the line's gain is the known-cost row's 6, not 6
        # + the unknown-cost row's 24 (backlog TJ-3465).
        res = _engine([tx('BUYSELL', '2025-01-02', 'ZZQ', 3, 30.0,
                          account='b'),
                       tx('BUYSELL', '2025-01-03', 'ZZQ', -5, 60.0,
                          account='c')]
                      + _move('2025-03-03', 'ZZQ', 10, 'b', 'c'))
        self.assertEqual([lot[0] for lot in _lots(res, 'b', 'ZZQ')], [-2.0])
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [])
        self.assertEqual(
            [(t['qty'], round(t['cost'], 6), round(t['proceeds'], 6),
              bool(t.get('tainted')))
             for t in res['transactions'] if t.get('qty')],
            [(3.0, -36.0, -30.0, False), (2.0, -24.0, 0.0, True)])
        self.assertNotIn('conservation', res['_stderr'])
        self.assertIn('5 of them close the short that day at their own '
                      'basis (2 of them at an unknown cost, for manual '
                      'reporting) (a cover on Form 8949, short-term: a gain '
                      'of 6.00, as booked at the move', res['_stderr'])
        self.assertIn('and none move as lots.', res['_stderr'])
        # ... and that the sender is the one booked short (TJ-3474).
        self.assertIn("2 of them closed c's short at an unknown cost, and b "
                      "is booked short by them. The other 5 reach no "
                      "account: c's sales of them read as a short.",
                      res['_stderr'])

    @rule(R.US_BASIS_05)
    def test_the_line_says_a_gain_row_and_a_loss_row_each(self):
        # Lots of 5 at 10 and 5 at 14 delivered into a short of 10 at 11:
        # a gain of 5 and a loss of 15, the loss disallowed against f's
        # short of 02-20: each said, not "a loss of 10.00, 15.00 of it
        # disallowed" (backlog TJ-3469).
        book = [tx('BUYSELL', '2024-12-20', 'ZZQ', 5, 50.0, account='a'),
                tx('BUYSELL', '2025-01-02', 'ZZQ', 5, 70.0, account='a'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', -10, 110.0, account='d'),
                tx('BUYSELL', '2025-02-20', 'ZZQ', -5, 50.0, account='f')
                ] + _move('2025-03-03', 'ZZQ', 10, 'a', 'd')
        res = _engine(book)
        self.assertIn('a gain of 5.00 and a loss of 15.00, 15.00 of it '
                      'disallowed as a wash sale, as booked at the move',
                      res['_stderr'])

    @rule(R.US_BASIS_05, R.US_WASH_22)
    def test_an_earlier_row_split_while_the_cover_books_is_not_the_cover(self):
        # The cover's own wash match splits an earlier row (d's covered
        # short of 2024, the replacement of the cover's loss), whose
        # head lands before the cover in the list: the cover's rows are
        # found by identity (backlog TJ-3459: by index, d's 2024 row was
        # taken for the cover and took a's 250).
        book = [tx('BUYSELL', '2024-11-01', 'ZZQ', 10, 2000.0, account='a'),
                tx('BUYSELL', '2024-11-04', 'ZZQ', -5, 30.0, account='c'),
                tx('BUYSELL', '2024-12-16', 'ZZQ', -10, 1000.0, account='d'),
                tx('BUYSELL', '2024-12-18', 'ZZQ', 10, 950.0, account='d'),
                tx('BUYSELL', '2024-12-20', 'ZZQ', 10, 100.0, account='b')
                ] + _move('2025-01-06', 'ZZQ', 10, 'b', 'c') + [
                tx('BUYSELL', '2025-01-10', 'ZZQ', -10, 1500.0, account='a')]
        res = _engine(book)
        by_year = {}
        for t in res['transactions']:
            if t.get('qty'):
                by_year[t['date'][:4]] = round(
                    by_year.get(t['date'][:4], 0.0) + t['gain'], 6)
        self.assertEqual(by_year, {'2024': 30.0, '2025': -250.0})
        self.assertIn('a loss of 20.00, 20.00 of it disallowed',
                      res['_stderr'])

    @rule(R.US_BASIS_05, R.US_WASH_05)
    def test_a_covers_loss_from_a_long_add_is_tested_against_shorts(self):
        # a's 500 loss matches b's 10 bought 02-15: 5 held by c, 5
        # delivered to c's short, whose cover (a gain of 10) takes 250
        # and is a loss of 240. It is a cover's loss: tested against
        # short sales (§1091(e)), of which there are none, so e's
        # purchase of 03-20 takes nothing (backlog TJ-3460: tested on
        # the long side, the 240 went onto e's lot).
        book = [tx('BUYSELL', '2024-12-02', 'ZZQ', 10, 2000.0, account='a'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', -5, 60.0, account='c'),
                tx('BUYSELL', '2025-02-15', 'ZZQ', 10, 100.0, account='b')
                ] + _move('2025-03-03', 'ZZQ', 10, 'b', 'c') + [
                tx('BUYSELL', '2025-03-10', 'ZZQ', -10, 1500.0, account='a'),
                tx('BUYSELL', '2025-03-20', 'ZZQ', 10, 1000.0, account='e')]
        res = _engine(book)
        [cover] = [t for t in res['transactions']
                   if t.get('direction') == 'SHORT']
        self.assertEqual((round(cover['gain'], 6),
                          round(cover['disallowed_amount'], 6)),
                         (-240.0, 0.0))
        self.assertEqual(_lots(res, 'e', 'ZZQ'), [(10.0, 1000.0, 0.0)])

    @rule(R.US_BASIS_05, R.US_WASH_22)
    def test_delivered_replacement_shares_still_replace(self):
        # b's 10 bought on 2025-02-20 are inside the window of a's loss
        # of 2025-03-10 (10 bought for 200, sold for 150). 5 of them were
        # delivered to c's short on 2025-03-03: they count as acquired
        # replacements that left (TJ-2700's ruling), 25 of a's loss added
        # to the cover's cost on its row; the 5 that moved to c take the
        # other 25 as held shares.
        book = [tx('BUYSELL', '2024-12-02', 'ZZQ', 10, 200.0, account='a'),
                tx('BUYSELL', '2025-01-03', 'ZZQ', -5, 60.0, account='c'),
                tx('BUYSELL', '2025-02-20', 'ZZQ', 10, 100.0, account='b')
                ] + _move('2025-03-03', 'ZZQ', 10, 'b', 'c') + [
                tx('BUYSELL', '2025-03-10', 'ZZQ', -10, 150.0, account='a')]
        res = _engine(book)
        [loss] = [t for t in res['transactions'] if t['account'] == 'a']
        self.assertAlmostEqual(loss['disallowed_amount'], 50.0, places=6)
        [cover] = [t for t in res['transactions']
                   if t.get('direction') == 'SHORT']
        self.assertEqual((round(cover['proceeds'], 6), round(cover['gain'], 6),
                          cover['term'],
                          [a['amount'] for a in cover['wash_basis_added']]),
                         (-50.0, -15.0, 'SHORT_TERM', [25.0]))
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(5.0, 75.0, 25.0)])

    @rule(R.US_BASIS_05)
    def test_a_move_into_an_account_with_no_short_says_nothing(self):
        book = ([tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 100.0, account='b')]
                + _move('2025-03-03', 'ZZQ', 10, 'b', 'c'))
        res = _engine(book)
        self.assertNotIn('close the short', res['_stderr'])
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(10.0, 100.0, 0.0)])


# --------------------------------------------------------- _us_scan_events
class TestMovedSharesLeaveTheSendersPosition(unittest.TestCase):
    """`_us_scan_events`, a custody move in the replacement scan: each leg
    moves its account's running position (core: `net_qty_state[_nk] =
    (net_qty_state.get(_nk, 0.0) + ev.quantity)`, under `abs(ev.quantity
    or 0.0) >= epsilon`), so the sender's next sale opens a short and the
    buy that covers it acquires nothing (no replacement)."""

    @rule(R.US_BASIS_05)
    @rule(R.US_WASH_01)
    def test_the_cover_does_not_wash_the_receivers_loss(self):
        # b moves its 10 to c on 2025-02-03, shorts 10 for 900 and covers
        # them for 850 (a 50 gain); c sells the 10 moved for 800 on
        # 2025-02-06: a 200 loss with no purchase in its window.
        book = ([tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b')]
                + _move('2025-02-03', 'ZZQ', 10, 'b', 'c')
                + [tx('BUYSELL', '2025-02-04', 'ZZQ', -10, 900.0,
                      account='b'),
                   tx('BUYSELL', '2025-02-05', 'ZZQ', 10, 850.0, account='b'),
                   tx('BUYSELL', '2025-02-06', 'ZZQ', -10, 800.0,
                      account='c')])
        res = _engine(book)
        self.assertEqual(_rows(res, 'c'), [(10.0, 1000.0, -200.0, 0.0)])
        self.assertEqual(_rows(res, 'b'), [(10.0, -900.0, 50.0, 0.0)])


class TestJournaledSharesLeaveTheOldListingsPosition(unittest.TestCase):
    """`_us_scan_events`, a listing journal in the replacement scan: each
    leg moves its listing's running position (core: `net_qty_state[_nk]
    = (net_qty_state.get(_nk, 0.0) + ev.quantity)`, under `abs(ev.quantity
    or 0.0) >= epsilon`), so a sale of the old listing after it opens a
    short and the buy that covers it acquires nothing."""

    @rule(R.US_XLIST_01)
    @rule(R.US_WASH_01)
    def test_the_cover_does_not_wash_another_accounts_loss(self):
        # b journals its 10 ZZQ.TO to ZZQ.US, shorts 10 ZZQ.TO for 900
        # and covers them for 850; c's ZZQ.TO (bought 2024) sold for 800
        # on 2025-02-06: a 200 loss with no purchase in its window.
        book = ([tx('BUYSELL', '2024-06-03', 'ZZQ.TO', 10, 1000.0,
                    account='c'),
                 tx('BUYSELL', '2025-01-02', 'ZZQ.TO', 10, 1000.0,
                    account='b')]
                + _journal('2025-02-03', 'ZZQ.TO', 'ZZQ.US', 10, 'b')
                + [tx('BUYSELL', '2025-02-04', 'ZZQ.TO', -10, 900.0,
                      account='b'),
                   tx('BUYSELL', '2025-02-05', 'ZZQ.TO', 10, 850.0,
                      account='b'),
                   tx('BUYSELL', '2025-02-06', 'ZZQ.TO', -10, 800.0,
                      account='c')])
        res = _engine(book)
        self.assertEqual(_rows(res, 'c'), [(10.0, 1000.0, -200.0, 0.0)])
        self.assertEqual(_lots(res, 'b', 'ZZQ.US'), [(10.0, 1000.0, 0.0)])


class TestIraHoldingFromAnOpeningBalanceBuysAReplacement(unittest.TestCase):
    """`_us_scan_events`, a context (IRA) book's shares that were not
    purchased (an opening balance): they raise the IRA's own running
    balance (core: `other_qty_state[_ok] = (other_qty_state.get(_ok, 0.0)
    + ev.quantity)`), so its next purchase opens long and replaces."""

    @rule(R.US_WASH_11)
    @rule(R.US_OPEN_01)
    def test_the_iras_purchase_disallows_the_loss_for_good(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
                tx('BUYSELL', '2025-02-03', 'ZZQ', -10, 800.0, account='b')]
        ira = [tx('BUYSELL', '2025-01-15', 'ZZQ', 10, 900.0, account='ira',
                  type=OPENING_TYPE, lot_date='2024-01-02'),
               tx('BUYSELL', '2025-02-10', 'ZZQ', 10, 800.0, account='ira')]
        res = _engine(book, sheltered_transactions=ira)
        [sale] = [t for t in res['transactions'] if t.get('qty')]
        self.assertAlmostEqual(sale['disallowed_amount'], 200.0, places=6)
        self.assertAlmostEqual(sale['permanently_disallowed'], 200.0,
                               places=6)


# ---------------------------------------------------- _us_dividend_entries
class TestDividendSettlesWithItsRow(unittest.TestCase):
    """`_us_dividend_entries`, a dividend's entry keeps the row's own
    settlement date (core: `'date_settle': tx.date_settle or tx.date`)."""

    @rule(R.US_INC_DATE_DIV)
    def test_the_settlement_date_is_the_rows(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
                tx('DIVIDEND', '2025-03-03', 'ZZQ', 10, 5.0, account='b',
                   settle='2025-03-04', gross_amount=5.0)]
        [div] = [t for t in _engine(book)['transactions']
                 if t.get('action') == 'DIVIDEND']
        self.assertEqual((div['date'], div['date_settle'], div['dividend']),
                         ('2025-03-03', '2025-03-04', 5.0))


# ---------------------------------------------------- USATaxRules._prepare
class TestMoveLegsWithinTheToleranceArePaired(unittest.TestCase):
    """`USATaxRules._prepare`, the pairing of a move's legs: quantities
    equal within 1e-9 x the quantity (core: `<= 1e-9 * max(1.0,
    abs(_i.quantity))`) pair, so 100 shares and a leg of 100.000000005
    move the lots."""

    @rule(R.US_BASIS_05)
    def test_the_receiver_holds_the_lot(self):
        book = ([tx('BUYSELL', '2025-01-02', 'ZZQ', 100, 1000.0, account='b')]
                + _move('2025-03-03', 'ZZQ', 100.000000005, 'b', 'c',
                        qty_in=100.0))
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ'), [])
        self.assertEqual(_lots(res, 'c', 'ZZQ'), [(100.0, 1000.0, 0.0)])
        self.assertNotIn('no matching leg', res['_stderr'])


# ----------------------------------------------------- USATaxRules._finish
class TestPlainTransferIsOutOfTheShareCount(unittest.TestCase):
    """`USATaxRules._finish`, the share-conservation replay: of the
    TRANSFER rows only an own move's legs count (core: `(t.action ==
    'TRANSFER' and t.type == LOT_MOVE_TYPE)`); a plain TRANSFER, which
    the engine does not book, is no mismatch. The warning is the
    branch's only output."""

    @rule(R.US_BASIS_05)
    def test_no_conservation_warning(self):
        book = [tx('BUYSELL', '2025-01-02', 'ZZQ', 10, 1000.0, account='b'),
                tx('TRANSFER', '2025-02-03', 'ZZQ', 5, 0.0, account='b')]
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ'), [(10.0, 1000.0, 0.0)])
        self.assertNotIn('conservation', res['_stderr'])



# ----------------------------------------------------------- _us_walk_sell
class TestPartlyExercisedReplacementWarrantsStillReplace(unittest.TestCase):
    """`_us_walk_sell`, a partial exercise of a replacement lot (an
    exercise runs no draw plan, so the walk's partial branch spends the
    capacity): the replacement record gives up the exercised units in its
    own purchase's units (core: `chunk_qty / (_cuf or 1.0)`), so across a
    2:1 split 30 exercised are 15 of the 20 bought; the 10 still held and
    the 30 exercised both replace another account's later loss — the
    owner's ruling on backlog TJ-2737 with TJ-2700 (2026-11-11: count
    them, §1091 has no still-held test, US-WASH-06 / US-WASH-22) — the
    exercised units' §1091(d) add on the shares they delivered (core:
    `_us_record_left_shares`, `last_chunks`). The floor of 35 this test
    pinned before the ruling is 70 exactly."""

    @rule(R.US_WASH_02)
    @rule(R.US_WASH_06, R.US_WASH_22)
    @rule(R.US_OPT_06)
    def test_the_held_ten_and_the_exercised_thirty_disallow_all_of_cs_loss(self):
        # c's 10 ZZQ.WT (20 after the split) carry 150 (b's 50 loss of
        # 2025-02-03 washed into them) and sell for 80 on 2025-02-20: a 70
        # loss. b bought 20 on 2025-02-10 (40 after the split) and
        # exercised 30 on 2025-02-14: the 10 b still holds replace 10 of
        # c's 20 and 10 of the 30 exercised the other 10, so 70 is
        # disallowed: 35 on b's held warrants and 35 in the basis of 10 of
        # the 30 ZZQ the exercise delivered (300 paid + 75 of warrant
        # cost: 125 for 10, plus 35), split from the other 20.
        w = 'ZZQ.WT'
        book = [tx('BUYSELL', '2025-01-02', w, 10, 100.0, account='b'),
                tx('BUYSELL', '2025-01-06', w, 10, 100.0, account='c'),
                tx('BUYSELL', '2025-02-03', w, -10, 50.0, account='b'),
                tx('BUYSELL', '2025-02-10', w, 20, 100.0, account='b'),
                tx('SPLIT', '2025-02-12', w, 2),
                tx('ASSIGN', '2025-02-14', w, -30, 0.0, account='b',
                   exercise_of='ZZQ', time='09:30:00'),
                tx('BUYSELL', '2025-02-14', 'ZZQ', 30, 300.0, account='b',
                   time='09:31:00'),
                tx('BUYSELL', '2025-02-20', w, -20, 80.0, account='c')]
        res = _engine(book)
        [loss] = [t for t in res['transactions']
                  if t['account'] == 'c' and t.get('qty')]
        self.assertAlmostEqual(loss['proceeds'] - loss['cost'], -70.0,
                               places=6)
        self.assertAlmostEqual(loss['disallowed_amount'], 70.0, places=6)
        self.assertAlmostEqual(loss['gain'], 0.0, places=6)
        self.assertEqual(_lots(res, 'b', w), [(10.0, 60.0, 35.0)])
        self.assertEqual(_lots(res, 'b', 'ZZQ'), [(10.0, 160.0, 35.0),
                                                  (20.0, 250.0, 0.0)])
        [held, left] = sorted(loss['wash_replacements'],
                              key=lambda r: r.get('sold_before_loss') or '')
        self.assertEqual((held['match_qty'], held['basis_bump']), (10.0, 35.0))
        self.assertEqual((left['sold_before_loss'], left['match_qty'],
                          left['basis_bump']), ('2025-02-14', 10.0, 35.0))

    @rule(R.US_WASH_22, R.US_OPT_06)
    def test_warrants_that_were_no_replacement_take_none_of_the_add(self):
        # b holds 10 ZZQ.WT from 2024-06-03 (50), outside every window,
        # and buys 10 more on 2025-02-10 (100); all 20 exercised into 20
        # ZZQ for 200. c's 70 loss matches the 10 bought in its window:
        # their 10 shares (100 + 75) take the 70; the 10 from the 2024
        # warrants stay at 175 (backlog TJ-3438: one lot of 20 at 420
        # carried the add on every share).
        w = 'ZZQ.WT'
        book = [tx('BUYSELL', '2024-06-03', w, 10, 50.0, account='b'),
                tx('BUYSELL', '2025-01-06', w, 10, 150.0, account='c'),
                tx('BUYSELL', '2025-02-10', w, 10, 100.0, account='b'),
                tx('ASSIGN', '2025-02-14', w, -20, 0.0, account='b',
                   exercise_of='ZZQ', time='09:30:00'),
                tx('BUYSELL', '2025-02-14', 'ZZQ', 20, 200.0, account='b',
                   time='09:31:00'),
                tx('BUYSELL', '2025-02-20', w, -10, 80.0, account='c')]
        res = _engine(book)
        self.assertEqual(sorted(_lots(res, 'b', 'ZZQ')),
                         [(10.0, 175.0, 0.0), (10.0, 245.0, 70.0)])

    @rule(R.US_WASH_22, R.US_WASH_09)
    @rule(R.US_OPT_06)
    def test_the_add_is_on_the_shares_the_matched_warrants_became(self):
        # b's 30 warrants of 2025-02-10: 10 carry b's own 50 (its loss of
        # 02-03), 20 are free; all 30 exercised into 30 ZZQ for 300. c's
        # 60 loss on 10 matches 10 of the free 20. Share for share
        # (backlog TJ-3430): the washed 10 became 10 ZZQ at 100 + 100
        # with their 50; the free 20 became 20 at 100 + 200, and 10 of
        # those take the 60. (One lot split by the chunk's fraction gave
        # 15 at 310 and 15 at 250.)
        w = 'ZZQ.WT'
        book = [tx('BUYSELL', '2024-12-02', w, 10, 100.0, account='c'),
                tx('BUYSELL', '2025-01-02', w, 10, 100.0, account='b'),
                tx('BUYSELL', '2025-02-03', w, -10, 50.0, account='b'),
                tx('BUYSELL', '2025-02-10', w, 30, 150.0, account='b'),
                tx('ASSIGN', '2025-02-14', w, -30, 0.0, account='b',
                   exercise_of='ZZQ', time='09:30:00'),
                tx('BUYSELL', '2025-02-14', 'ZZQ', 30, 300.0, account='b',
                   time='09:31:00'),
                tx('BUYSELL', '2025-02-20', w, -10, 40.0, account='c')]
        res = _engine(book)
        self.assertEqual(_lots(res, 'b', 'ZZQ'),
                         [(10.0, 200.0, 50.0), (10.0, 210.0, 60.0),
                          (10.0, 150.0, 0.0)])


if __name__ == '__main__':
    unittest.main()
