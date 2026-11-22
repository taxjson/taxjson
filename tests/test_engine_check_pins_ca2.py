"""Canadian engine and transfer branches the suite executes but no test
checks (TJ-1896, the mutation triage's second batch, 2026-11-10): each
test runs one branch and pins a figure a mutant of its decision changes
(a gain, a basis, a record's date, a row kept or dropped). Synthetic data
only; tickers and accounts are made up."""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.row_types import (IN_KIND_CONTRIBUTION_TYPE, IN_KIND_DISPOSITION_TYPE,
                                   LOT_MOVE_TYPE, OPENING_TYPE)
from taxjson.lib.pipeline import (TransferValidationError,
                                  _drop_self_cancelling_transfers,
                                  _handle_transfers,
                                  _net_cross_account_transfers)
from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_rules import assert_rule, rules_of
from taxjson.engine.rows import in_kind_record


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res, err.getvalue()


def _tx(action, date, qty, net, *, sym='SYQ.TO', account='b', typ='',
        settle=None, time='10:00:00', price=None, lot_date=''):
    return TaxTransaction(action=action, date=date,
                          date_settle=date if settle is None else settle,
                          time=time, symbol=sym, quantity=qty,
                          price=(price if price is not None
                                 else abs(net / qty) if qty else 0.0),
                          net_amount=net, currency='CAD', account=account,
                          type=typ, lot_date=lot_date)


def _trade(date, qty, net, **kw):
    return _tx('BUYSELL', date, qty, net, **kw)


def _sales(res):
    """(date, gain, cost, denied) per sale record."""
    return [(r['date'], round(r['gain'], 2), round(r['cost'], 2),
             round(r.get('disallowed_amount') or 0.0, 2))
            for r in res['transactions'] if r.get('qty')]


# ------------------------------------------------------------ _walk_adjust

class TestRocLeavingTheAcbAtTheAddition(unittest.TestCase):
    """`_walk_adjust`, a return of capital on a pool that holds a
    superficial-loss addition: what it takes of the addition is the part
    STRICTLY over the ACB left (CA-RPT-16). Buy 100 for 1000, sell for
    500 (loss 500), rebuy 100 for 500: the loss is denied and added to the
    replacement's ACB (s.53(1)(f)): ACB 1000, of which 500 the addition.
    ROC 500 (s.53(2)(a)(ii)) leaves an ACB of 500, all of it the addition:
    nothing is cut. The sale for 900 gains 400 and recovers the 500."""

    @rule(R.CA_ACB_06, R.CA_SL_09, R.CA_RPT_16)
    def test_an_acb_equal_to_the_addition_cuts_nothing(self):
        res, _ = _run([_trade('2025-01-02', 100, -1000),
                       _trade('2025-02-03', -100, 500),
                       _trade('2025-02-05', 100, -500),
                       _tx('ADJUST', '2025-04-01', 0.0, -500, typ='roc'),
                       _trade('2025-07-02', -100, 900)])
        self.assertEqual(_sales(res), [('2025-02-03', 0.0, 1000.0, 500.0),
                                       ('2025-07-02', 400.0, 500.0, 0.0)])
        sale = res['transactions'][-1]
        self.assertAlmostEqual(sale['deferred_loss_recovered'], 500.0, 6)
        self.assertNotIn('deferred_loss_cut', sale)


class TestAnAdditionOnANegativeCostIsNoDeemedGain(unittest.TestCase):
    """`_walk_adjust`'s s.40(3) clause: a cost below nil on a long pool is
    a deemed gain only when a return of capital (an ADJUST of the books)
    drove it there, never when a superficial-loss addition (a WASH_
    ADJUST) lands on it. A futures contract bought at a negative price
    receives cash: its cost is negative (CA-FX-04, a negative price keeps
    its sign). Bought for 5000, sold for 4000 (loss 1000), bought again at
    -37 (receiving 3700) two days later and held: the loss is denied and
    added to the replacement's cost, -3700 + 1000 = -2700, no gain booked
    then. Its sale for 1000 gains 3700: 3700 in all, the cash's net."""

    @rule(R.CA_FX_04, R.CA_SL_09, R.CA_ACB_07)
    def test_the_addition_leaves_the_negative_cost(self):
        f = 'F:QZCLK25'
        res, _ = _run([_trade('2025-03-03', 1, 5000, sym=f),
                       _trade('2025-04-01', -1, 4000, sym=f),
                       _trade('2025-04-03', 1, -3700, sym=f, price=-37.0),
                       _trade('2025-06-02', -1, 1000, sym=f)])
        self.assertEqual(_sales(res), [('2025-04-01', 0.0, 5000.0, 1000.0),
                                       ('2025-06-02', 3700.0, -2700.0, 0.0)])
        self.assertEqual([r for r in res['transactions'] if not r.get('qty')],
                         [])


# ------------------------------------------------------------- _walk_trade

def _opening(date, qty, total, lot=''):
    return _tx('BUYSELL', date, qty, -total, typ=OPENING_TYPE,
               time='00:00:00', lot_date=lot)


class TestAnOpeningDatesItsPositionFromItsLot(unittest.TestCase):
    """`_walk_trade`, an opening balance (CA-OPEN-01: not a purchase): the
    position it opens starts on its lot date when the line gives one,
    else on the snapshot day; a sale from an opening-only pool counts its
    days held from that date. A 2024-12-31 snapshot of 100 SYQ.TO bought
    2019-03-04, 40 sold on 2025-03-03: 2191 days held (62 with no lot
    date), the 60 held starting 2019-03-04."""

    def _book(self, lot):
        res, _ = _run([_opening('2024-12-31', 100, 1000, lot),
                       _trade('2025-03-03', -40, 600)])
        sale, = res['transactions']
        held, = res['inventory']
        return (sale['days_held'], round(sale['gain'], 2),
                held['qty'], held['position_start_date'])

    @rule(R.CA_OPEN_01)
    def test_the_lot_date(self):
        self.assertEqual(self._book('2019-03-04'),
                         (2191, 200.0, 60.0, '2019-03-04'))

    @rule(R.CA_OPEN_01)
    def test_no_lot_date_is_the_snapshot_day(self):
        self.assertEqual(self._book(''), (62, 200.0, 60.0, '2024-12-31'))


class TestTheInKindNoteNamesItsPlan(unittest.TestCase):
    """`_walk_trade`, an in-kind move to a plan booked as a sale at fair
    market value (CA-INKIND-02): to a plan s.40(2)(g)(iv) names (RRSP,
    TFSA, ...) the loss is denied for good and the record's note says so;
    to one it does not name (an RESP) the loss is an ordinary one and the
    note says that. 100 bought for 1000, moved at a value of 700."""

    def _res(self, typ):
        res, _ = _run([_trade('2025-01-02', 100, -1000),
                       _trade('2025-03-03', -100, 700, typ=typ)])
        return res

    def _sale(self, typ):
        sale, = self._res(typ)['transactions']
        return (round(sale['gain'], 2), round(sale['denied_contribution'], 2),
                sale['note'])

    @rule(R.CA_INKIND_02, R.CA_INKIND_03)
    def test_a_named_plan(self):
        self.assertEqual(self._sale(IN_KIND_CONTRIBUTION_TYPE), (
            0.0, 300.0,
            'in-kind contribution to a registered plan: a sale at fair '
            'market value; the loss is denied (s.40(2)(g)(iv)), no ACB '
            'addition'))
        # The record names its rule (PLAN step 11).
        res = self._res(IN_KIND_CONTRIBUTION_TYPE)
        sale, = res['transactions']
        assert_rule(self, res, sale['id'], R.CA_INKIND_03, record=in_kind_record(sale['id']))

    @rule(R.CA_INKIND_02, R.CA_INKIND_03)
    def test_a_plan_the_rule_does_not_name(self):
        self.assertEqual(self._sale(IN_KIND_DISPOSITION_TYPE), (
            -300.0, 0.0,
            'in-kind contribution to a plan s.40(2)(g)(iv) does not name: '
            'a sale at fair market value'))
        # Its record names the sale at fair market value (PLAN step 11).
        res = self._res(IN_KIND_DISPOSITION_TYPE)
        sale, = res['transactions']
        self.assertEqual(rules_of(res, sale['id']), {in_kind_record(sale['id']): R.CA_INKIND_02})
        assert_rule(self, res, sale['id'], R.CA_INKIND_02, record=in_kind_record(sale['id']))

    @rule(R.CA_INKIND_02, R.CA_SL_01)
    def test_a_superficial_loss_on_the_move_keeps_both_records(self):
        # The same move to a plan the rule does not name, the shares bought
        # back a week later and held: the loss is superficial (denied onto
        # the rebuy), and the sale's two records keep their own rules.
        book = [_trade('2025-01-02', 100, -1000),
                _trade('2025-03-03', -100, 700, typ=IN_KIND_DISPOSITION_TYPE),
                _trade('2025-03-10', 100, -700)]
        res, err = _run(book)
        self.assertEqual(_sales(res), [('2025-03-03', 0.0, 1000.0, 300.0)], err)
        sale = book[1].id
        assert_rule(self, res, sale, R.CA_SL_01, record=sale)
        assert_rule(self, res, sale, R.CA_INKIND_02, record=in_kind_record(sale))
        assert_rule(self, res, sale, R.CA_SL_09, record=f"WASH_{sale}__{book[2].id}")


class TestAGrantTimedNilCloseKeepsItsSettleDate(unittest.TestCase):
    """`_walk_trade`, a grant-timed write closed for nothing (CA-OPT-01:
    its premium was the write year's gain): no record, but a nil close the
    reconcile-slips view counts, dated as the close: its settle date (the
    year a settle-basis project files it in, CA-DATE-01), its trade date
    when the row has none. Written 2025-02-03 for 400, closed at 0 on
    2025-12-31 settling 2026-01-02."""

    C = 'SYQ260116C00020000.TO'

    def _nil(self, settle):
        res, _ = _run([
            TaxTransaction(action='BUYSELL', date='2025-02-03',
                           date_settle='2025-02-03', time='10:00:00',
                           symbol=self.C, quantity=-1, price=4.0,
                           net_amount=400, currency='CAD', account='b'),
            TaxTransaction(action='BUYSELL', date='2025-12-31',
                           date_settle=settle, time='10:00:00',
                           symbol=self.C, quantity=1, price=0.0,
                           net_amount=0.0, currency='CAD', account='b')],
            option_premium_timing='grant', option_grant_since=2025)
        self.assertEqual([(r['date'], r['gain']) for r in res['transactions']],
                         [('2025-02-03', 400.0)])
        nil, = res['grant_nil_closes']
        return nil['date'], nil['date_settle'], nil['grant_closed']

    @rule(R.CA_OPT_01, R.CA_DATE_01)
    def test_the_settle_date(self):
        self.assertEqual(self._nil('2026-01-02'), (
            '2025-12-31', '2026-01-02',
            {'2025': {'units': 1.0, 'premium': 400.0}}))

    @rule(R.CA_OPT_01, R.CA_DATE_01)
    def test_no_settle_date_is_the_trade_date(self):
        self.assertEqual(self._nil('')[:2], ('2025-12-31', '2025-12-31'))


# --------------------------------------------- _sl_mk_adjust, _sl_allocate

class TestALossOnAnAssignedLegIsItsOwnSale(unittest.TestCase):
    """`_sl_mk_adjust` and `_sl_allocate`, a superficial loss on a row the
    sale grouping (lib/sale_groups disposition_groups) does not list: the
    marked ASSIGN stock leg of a written call's assignment, its own sale
    and its own last fill (`_disp_last`, TJ-4119). 100 bought for 5000,
    100 more for 4100 on 02-20 (the replacement, held at day 30), a call
    written for 100 and assigned on 03-03: the 100 shares go at 40 with
    the premium, proceeds 4100 (CA-OPT-06), against an ACB of 4550. The
    450 loss is denied (100 sold, acquired and held: CA-SL-08) and added
    to the shares left, placed right after the leg (the replacement was
    bought before the sale), an ACB of 5000 (CA-SL-09): their sale for
    4600 loses 400."""

    C = 'SYQ250321C00040000.TO'

    @rule(R.CA_SL_08, R.CA_SL_09, R.CA_OPT_06)
    def test_the_addition_follows_the_assigned_leg(self):
        book = [_trade('2025-01-02', 100, -5000),
                _trade('2025-02-20', 100, -4100),
                _trade('2025-02-24', -1, 100, sym=self.C, price=1.0),
                _tx('ASSIGN', '2025-03-03', 1, 0, sym=self.C,
                    time='16:00:00'),
                _tx('ASSIGN', '2025-03-03', -100, 4000, time='16:00:00'),
                _trade('2025-06-02', -100, 4600)]
        res, err = _run(book)
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        # The first read (the addition's `after`) right on the first pass:
        # a wrong row there is moved by a later pass, which only the
        # iteration count shows.
        self.assertEqual(res['summary']['wash_solver_iterations'], 2)
        self.assertEqual(_sales(res), [('2025-03-03', 0.0, 4550.0, 450.0),
                                       ('2025-06-02', -400.0, 5000.0, 0.0)])
        (wash,) = res['wash_sales']
        self.assertEqual(wash['loss_tx_id'], book[4].id)
        self.assertEqual([(a['after'], a['time'], a['amount'])
                          for a in wash['adjusts']],
                         [(book[4].id, '16:00:01', 450.0)])


# ------------------------------------------------- CanadaTaxRules._finish

class TestIncomeRecordsKeepTheirSettleDate(unittest.TestCase):
    """`CanadaTaxRules._finish`, the income records: a dividend's and a
    payment in lieu's record carries the row's settle date, its pay date
    when the row has none (the year of income is the pay date's,
    CA-INC-DATE-DIV / CA-INC-DATE-PIL)."""

    @rule(R.CA_INC_DATE_DIV, R.CA_INC_DATE_PIL)
    def test_dividend_and_payment_in_lieu(self):
        res, _ = _run([
            _trade('2025-01-02', 100, -1000),
            _tx('DIVIDEND', '2025-03-31', 0.0, 25.0, settle='2025-04-02'),
            _tx('DIVIDEND', '2025-06-30', 0.0, 30.0, settle=''),
            _tx('DIVIDEND_IN_LIEU', '2025-09-30', 0.0, -12.0,
                settle='2025-10-01'),
            _tx('DIVIDEND_IN_LIEU', '2025-12-31', 0.0, -13.0, settle='')])
        self.assertEqual(
            [(r['action'], r['date'], r['date_settle'],
              r.get('dividend', r.get('pil')))
             for r in res['transactions']],
            [('DIVIDEND', '2025-03-31', '2025-04-02', 25.0),
             ('DIVIDEND', '2025-06-30', '2025-06-30', 30.0),
             ('DIVIDEND_IN_LIEU', '2025-09-30', '2025-10-01', -12.0),
             ('DIVIDEND_IN_LIEU', '2025-12-31', '2025-12-31', -13.0)])


# ----------------------------------------------------------- _ca_tl_after

class TestASplitNamedForItselfIsNoRename(unittest.TestCase):
    """`_ca_tl_after`, a taxable SPLIT whose symbol_new is its own symbol
    (IB's spelling of a plain split, corporate_timeline.
    normalize_symbol_new): the cost timeline's row of the split carries no
    rename (`renamed_to` empty), as a split with no symbol_new does: 100
    bought for 1000, split 2-for-1 (CA-CORP-01: the ACB unchanged)."""

    @rule(R.CA_CORP_01, R.CA_RPT_12)
    def test_the_split_row_names_no_new_symbol(self):
        for new in ('SYQ.TO', ''):
            with self.subTest(symbol_new=new):
                res, _ = _run([
                    _trade('2025-01-02', 100, -1000),
                    TaxTransaction(action='SPLIT', date='2025-03-03',
                                   date_settle='2025-03-03',
                                   time='09:00:00', symbol='SYQ.TO',
                                   symbol_new=new, quantity=2.0,
                                   net_amount=0.0, currency='CAD',
                                   account='b')],
                    cost_timeline='settle')
                tl = res['cost_timeline']
                f = {k: tl['fields'].index(k) for k in
                     ('date', 'symbol', 'qty', 'total_cost', 'renamed_to')}
                self.assertEqual(
                    [tuple(r[f[k]] for k in f) for r in tl['rows']],
                    [('2025-01-02', 'SYQ.TO', 100.0, 1000.0, ''),
                     ('2025-03-03', 'SYQ.TO', 200.0, 1000.0, '')])


# ------------------------------- pipeline._drop_self_cancelling_transfers

def _xfer(date, qty, *, sym='SYQ.TO', account='rrsp', time='10:00:00',
          typ=''):
    return _tx('TRANSFER', date, qty, 0.0, sym=sym, account=account,
               time=time, typ=typ)


def _kept(rows):
    return sorted((t.account, t.date, t.time, t.quantity) for t in rows
                  if t.action == 'TRANSFER')


class TestOwnBookRunsFollowTheClock(unittest.TestCase):
    """`_drop_self_cancelling_transfers`, a registered account's own book
    (`trades_block=False`, TJ-0360): a segment that does not net whole is
    judged by its zero-net runs, the rows taken in (date, time) order,
    whatever order the export lists them in. One day: 100 out at 09:00,
    back at 10:00, 30 out at 11:00, listed in-leg first: the out-and-back
    nets (a custody move, CA-SL-16), the 30 out stays."""

    @rule(R.CA_SL_16)
    def test_the_out_and_back_nets(self):
        rows = [_xfer('2025-06-10', 100, time='10:00:00'),
                _xfer('2025-06-10', -30, time='11:00:00'),
                _xfer('2025-06-10', -100, time='09:00:00')]
        out, dropped = _drop_self_cancelling_transfers(rows,
                                                       trades_block=False)
        self.assertEqual(_kept(out), [('rrsp', '2025-06-10', '11:00:00', -30)])
        self.assertEqual(dropped, [('SYQ.TO', 'rrsp', 2)])


# --------------------------------- pipeline._net_cross_account_transfers

def _net(rows):
    moved = []
    out = _net_cross_account_transfers(rows, netted_out=moved,
                                       near_trade_guard=False)
    return _kept(out), _kept(moved)


class TestCrossAccountSegmentsFollowTheCalendar(unittest.TestCase):
    """`_net_cross_account_transfers`' segments: the symbol's rows in
    (date, time) order, a new segment where two rows are more than 35 days
    apart. A move r1 -> r2 in June nets (CA-SL-16); a TFSA contribution in
    January, at a later time of its day, is a segment of its own and
    stays."""

    @rule(R.CA_SL_16)
    def test_a_far_earlier_row_stays_apart(self):
        kept, moved = _net([_xfer('2025-01-02', 40, account='tfsa',
                                  time='11:00:00'),
                            _xfer('2025-06-10', -100, account='r1'),
                            _xfer('2025-06-12', 100, account='r2',
                                  time='12:00:00')])
        self.assertEqual(kept, [('tfsa', '2025-01-02', '11:00:00', 40)])
        self.assertEqual(moved, [('r1', '2025-06-10', '10:00:00', -100),
                                 ('r2', '2025-06-12', '12:00:00', 100)])


class TestCrossAccountMovesOnOneDayPairByTime(unittest.TestCase):
    """`_net_cross_account_transfers`' `_moves`, a segment that does not
    net whole: an out leg pairs with an in leg of the same units in
    another account, the closest dates first; on one day the earlier row
    first, in clock order whatever the export's. r1's 100 out at 10:00
    pairs with r3's 100 in at 11:00 (listed after r2's at 12:00); r2's
    stays."""

    @rule(R.CA_SL_16)
    def test_the_earlier_in_leg_is_the_move(self):
        kept, moved = _net([_xfer('2025-06-10', -100, account='r1'),
                            _xfer('2025-06-10', 100, account='r2',
                                  time='12:00:00'),
                            _xfer('2025-06-10', 100, account='r3',
                                  time='11:00:00')])
        self.assertEqual(kept, [('r2', '2025-06-10', '12:00:00', 100)])
        self.assertEqual(moved, [('r1', '2025-06-10', '10:00:00', -100),
                                 ('r3', '2025-06-10', '11:00:00', 100)])


class TestAnInLegIsOneMoveOnly(unittest.TestCase):
    """`_net_cross_account_transfers`' `_moves`: each leg is in one move
    at most. r1 and r4 each move 100 out (06-10, 06-11), r2 receives 100
    (06-12): r4's leg, the closer one, is the move; r1's 100 out stays
    (a withdrawal, booked as one)."""

    @rule(R.CA_SL_16)
    def test_the_farther_out_leg_stays(self):
        kept, moved = _net([_xfer('2025-06-10', -100, account='r1'),
                            _xfer('2025-06-11', -100, account='r4'),
                            _xfer('2025-06-12', 100, account='r2')])
        self.assertEqual(kept, [('r1', '2025-06-10', '10:00:00', -100)])
        self.assertEqual(moved, [('r2', '2025-06-12', '10:00:00', 100),
                                 ('r4', '2025-06-11', '10:00:00', -100)])


class TestASegmentWithinTheToleranceNetsWhole(unittest.TestCase):
    """`_net_cross_account_transfers`: a segment whose legs sum to zero
    within the share tolerance (1e-9, the tolerance itself included, as
    `_nets` reads it) nets whole, a leg of dust with it; only a segment
    past it is split into moves. r1 -> r2 of 100 and r3's 1e-9 units in
    (a coin's last unit): all three net."""

    @rule(R.CA_SL_16)
    def test_the_dust_leg_nets_with_the_move(self):
        kept, moved = _net([_xfer('2025-06-10', -100, account='r1'),
                            _xfer('2025-06-11', 100, account='r2'),
                            _xfer('2025-06-12', 1e-9, account='r3')])
        self.assertEqual(kept, [])
        self.assertEqual(len(moved), 3)


# ------------------------------------------- pipeline._handle_transfers

class TestLotMovesSetAsideKeepTheOtherTransfers(unittest.TestCase):
    """`_handle_transfers`: the legs of a move between two of your own
    accounts (LOT_MOVE_TYPE) are set aside and handed back unchanged; the
    book's other TRANSFER rows are still judged: in a taxable book a
    transfer in stops the run (CA-ACB-10)."""

    @rule(R.CA_ACB_10, R.CA_ACB_OWN_MOVE)
    def test_a_plain_transfer_beside_a_lot_move_still_stops_the_run(self):
        rows = [_trade('2025-03-01', 10, -100.0, account='b'),
                _xfer('2025-03-03', -10, account='b', typ=LOT_MOVE_TYPE),
                _xfer('2025-03-03', 10, account='c', typ=LOT_MOVE_TYPE),
                _xfer('2025-03-05', 100, sym='ZZQ.TO', account='a')]
        with self.assertRaises(TransferValidationError) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                _handle_transfers(rows, [], taxable=True)
        self.assertTrue(str(cm.exception).startswith('taxable account a: '),
                        str(cm.exception))

    @rule(R.CA_ACB_OWN_MOVE)
    def test_the_lot_moves_alone_pass_through(self):
        rows = [_trade('2025-03-01', 10, -100.0, account='b'),
                _xfer('2025-03-03', -10, account='b', typ=LOT_MOVE_TYPE),
                _xfer('2025-03-03', 10, account='c', typ=LOT_MOVE_TYPE)]
        with contextlib.redirect_stderr(io.StringIO()):
            out, _sh = _handle_transfers(rows, [], taxable=True)
        self.assertEqual(sorted((t.action, t.account, t.quantity)
                                for t in out),
                         [('BUYSELL', 'b', 10), ('TRANSFER', 'b', -10),
                          ('TRANSFER', 'c', 10)])


if __name__ == '__main__':
    unittest.main()
