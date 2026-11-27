"""TJ-2735: an option series' written contracts are the writing account's
own position (CA-OPT-12), as a share short the broker marks is (TJ-0721):
a purchase in another account is a contract bought, never its closing
purchase, and a write never sells a contract another account holds. Bought
contracts stay one pool across the taxable accounts (CA-ACB-01), and the
superficial-loss rule reads every account (CA-SL-03).

Synthetic data only: made-up tickers and accounts.

Formerly: test_fix_2735_option_accounts
"""
import contextlib
import hashlib
import io
import json
import time
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.row_types import OPENING_TYPE
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
from _support.ca_edge_analysis import _edge, _t
import _move_fuzz


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


C = 'SYQ251219C00020000.TO'           # the entry's call
P = 'SYQ261218P00010000.TO'           # a put, for the assignment


def _tx(account, date, qty, net, sym=C, action='BUYSELL', **kw):
    """One contract row: `net` is the money (positive), `qty` signed."""
    return TaxTransaction(
        action=action, date=date, date_settle=date, time='10:00:00',
        symbol=sym, quantity=float(qty), currency='CAD',
        price=abs(net / qty) / 100 if qty and sym != 'SYQ.TO' else abs(net / (qty or 1)),
        net_amount=float(net), account=account, **kw)


def _mv(frm, to, date, qty, change=True, sym=C, broker=None, n=1):
    """The engine's legs for a move of `qty` contracts (negative: written
    ones) from account `frm` to `to`, as the pipeline builds them: the two
    TRANSFER rows paired by `transfer_in.arrivals` (its OwnMove, which
    records how the rows' broker signs a transfer row), the legs written by
    `pipeline.stages._move_legs`. `change`: the broker's rows are each
    position's change (a written contract leaving shows +1; IB's Transfers
    section), else signed by direction (out -1, in +1; Questrade, RBC, the
    generic importer, a .tt line): the pairing reads the negative row as
    the sender's, so with `change` a written move's legs name the receiver
    as the sender; the engine reads the sender by the convention the legs
    carry (backlog TJ-3224, TJ-3243). `broker`: the rows' brokerage id
    (default: one of that convention; 'other' leaves it unknown)."""
    from taxjson.lib import transfer_in as TI
    from taxjson.pipeline.stages import _move_legs
    out_q = -qty if change else -abs(qty)
    broker = broker or ('ib' if change else 'questrade')
    rows = [(a, broker, {'action': 'TRANSFER', 'symbol': sym, 'quantity': float(q),
                         'date': date, 'currency': 'CAD', 'description': 'transfer'})
            for a, q in ((frm, out_q), (to, -out_q))]
    moves = []
    TI.arrivals(rows, moves=moves)
    assert len(moves) == 1, moves
    m, = moves
    by_acct = _move_legs([{'symbol': m.key, 'qty': float(m.quantity), 'from': m.out_account,
                           'to': m.in_account, 'date': min(m.out_date, m.in_date),
                           'time': '00:00:00', 'symbol_out': m.out_symbol,
                           'symbol_in': m.in_symbol, 'currency_out': 'CAD',
                           'currency_in': 'CAD', 'signed_by': m.signed_by}], 'CAD')
    legs = [TaxTransaction(**d) for a in sorted(by_acct) for d in by_acct[a]]
    for t in legs:
        t.description = t.description.replace('#1:', f'#{n}:')
    return legs


def _run_err(txs, **kw):
    """`_run` with the run's stderr."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res, err.getvalue()


def _leg_of(legs, account):
    return next(t for t in legs if t.account == account)


def _split(sym, new, date='2025-03-01', ratio=1.0, account='b'):
    return TaxTransaction(action='SPLIT', date=date, date_settle=date,
                          time='00:00:00', symbol=sym, symbol_new=new,
                          quantity=ratio, price=0.0, net_amount=0.0,
                          currency='CAD', account=account)


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res


def _gains(res):
    """(date, account, qty, gain, disallowed) of each disposition, in order."""
    return [(r['date'], r['account'], r['qty'], round(r['gain'], 2),
             round(r.get('disallowed_amount') or 0.0, 2))
            for r in res['transactions']]


def _net(res, year='2025'):
    return round(sum(r['gain'] for r in res['transactions']
                     if (r.get('date_settle') or r['date'])[:4] == year), 2)


def _inv(res):
    """(symbol, account or '', qty, total_cost) of each open position."""
    return sorted((i['symbol'], i.get('account') or '', round(i['qty'], 6),
                   round(i['total_cost'], 2))
                  for i in res['inventory'] if abs(i['qty']) > 1e-9)


class TestTheEntrysBook(unittest.TestCase):
    """The backlog entry's book: account a buys, sells at a loss, buys again
    and sells; account b writes twice and buys one back."""

    BOOK = [
        _tx('a', '2025-01-06', 1, 500),
        _tx('a', '2025-01-20', -1, 200),
        _tx('b', '2025-01-25', -1, 250),
        _tx('a', '2025-02-03', 1, 220),
        _tx('b', '2025-03-03', -1, 300),
        _tx('a', '2025-03-04', -1, 400),
        _tx('b', '2025-03-10', 1, 100),
    ]

    @rule(R.CA_OPT_12, R.CA_SL_01, R.CA_SL_02, R.CA_SL_09, R.CA_OPT_06,
          R.CA_OPT_10, R.CA_SL_07)
    def test_each_accounts_position_is_its_own(self):
        res = _run(self.BOOK)
        # a's -300 is superficial: the 02-03 contract is held at day 30 (b's
        # write never replaces, and a's purchase closed nothing of b's);
        # the 300 raises that contract's ACB to 520, so the 03-04 sale is
        # -120. b's buy-back takes its own two contracts' average, 275.
        self.assertEqual(_gains(res), [
            ('2025-01-20', 'a', 1.0, 0.0, 300.0),
            ('2025-03-04', 'a', 1.0, -120.0, 0.0),
            ('2025-03-10', 'b', 1.0, 175.0, 0.0)])
        sale = res['transactions'][1]
        self.assertEqual((round(sale['cost'], 2), round(sale['proceeds'], 2)),
                         (520.0, 400.0))
        self.assertEqual(_net(res), 55.0)
        # b is still short one contract, at its account's average premium.
        self.assertEqual(_inv(res), [(C, '', -1.0, -275.0)])

    @rule(R.CA_OPT_12, R.CA_OPT_06)
    def test_the_old_netting_is_gone(self):
        # Before TJ-2735 one pool netted the accounts: a's 02-03 purchase
        # closed b's write (+30 in a), a's -300 was allowed, a's 03-04
        # sale was a write and b's buy-back closed its 300 write (+200):
        # net -70, one contract short at -400. The figures now: three
        # dispositions (none on 02-03), net +55, b short one at 275.
        res = _run(self.BOOK)
        self.assertEqual([(g[0], g[1], g[3]) for g in _gains(res)],
                         [('2025-01-20', 'a', 0.0), ('2025-03-04', 'a', -120.0),
                          ('2025-03-10', 'b', 175.0)])
        self.assertEqual(_net(res), 55.0)
        self.assertEqual(_inv(res), [(C, '', -1.0, -275.0)])


class TestSameAccountWriteAndBuyBack(unittest.TestCase):

    @rule(R.CA_OPT_12, R.CA_OPT_10, R.CA_OPT_05)
    def test_the_accounts_own_buy_back_closes_its_write(self):
        res = _run([_tx('a', '2025-01-06', -1, 250),
                    _tx('a', '2025-02-03', 1, 100)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, 150.0, 0.0)])
        self.assertEqual(_inv(res), [])

    @rule(R.CA_OPT_12, R.CA_OPT_04)
    def test_a_bought_contract_sold_in_the_same_account(self):
        res = _run([_tx('a', '2025-01-06', 1, 500),
                    _tx('a', '2025-02-03', -1, 600)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, 100.0, 0.0)])
        self.assertEqual(_inv(res), [])


class TestTwoAccountsBothWriting(unittest.TestCase):

    @rule(R.CA_OPT_12, R.CA_OPT_06, R.CA_OPT_10)
    def test_each_buy_back_closes_its_own_accounts_write(self):
        book = [_tx('a', '2025-01-06', -1, 250),
                _tx('b', '2025-01-10', -1, 400),
                _tx('b', '2025-02-03', 1, 100)]
        res = _run(book)
        self.assertEqual(_gains(res), [('2025-02-03', 'b', 1.0, 300.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', -1.0, -250.0)])
        res = _run(book + [_tx('a', '2025-02-10', 1, 50)])
        self.assertEqual(_gains(res), [('2025-02-03', 'b', 1.0, 300.0, 0.0),
                                       ('2025-02-10', 'a', 1.0, 200.0, 0.0)])
        self.assertEqual(_inv(res), [])


class TestALongInOneAccountBesideAWriteInAnother(unittest.TestCase):

    @rule(R.CA_OPT_12, R.CA_ACB_01)
    def test_two_positions_not_their_net(self):
        res = _run([_tx('a', '2025-01-06', 1, 500),
                    _tx('b', '2025-01-10', -1, 250)])
        self.assertEqual(_gains(res), [])
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'b', -1.0, -250.0)])
        short, = [i for i in res['inventory'] if i.get('account') == 'b']
        self.assertTrue(short.get('short_pool'))

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_each_account_closes_its_own(self):
        res = _run([_tx('a', '2025-01-06', 1, 500),
                    _tx('b', '2025-01-10', -1, 250),
                    _tx('a', '2025-02-03', -1, 600),
                    _tx('b', '2025-02-10', 1, 100)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, 100.0, 0.0),
                                       ('2025-02-10', 'b', 1.0, 150.0, 0.0)])
        self.assertEqual(_inv(res), [])

    @rule(R.CA_OPT_12, R.CA_OPT_06)
    def test_each_writers_row_is_at_its_own_premium(self):
        res = _run([_tx('a', '2025-01-06', 1, 500),
                    _tx('b', '2025-01-10', -2, 200),
                    _tx('c', '2025-01-12', -1, 900)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'b', -2.0, -200.0),
                                     (C, 'c', -1.0, -900.0)])


class TestBoughtContractsPoolAcrossAccounts(unittest.TestCase):
    """Bought contracts are property: one average-cost pool across the
    taxable accounts, as the engine pools shares (s.47)."""

    @rule(R.CA_ACB_01, R.CA_OPT_12)
    def test_a_sale_takes_the_pooled_average(self):
        res = _run([_tx('a', '2025-01-06', 1, 500),
                    _tx('c', '2025-01-10', 1, 300),
                    _tx('a', '2025-02-03', -1, 600)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, 200.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 400.0)])


class TestACrossingRowIsTwoParts(unittest.TestCase):

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_purchase_past_the_accounts_write_buys_long_with_the_rest(self):
        res = _run([_tx('b', '2025-01-06', -1, 250),
                    _tx('b', '2025-02-03', 3, 300)])
        self.assertEqual(_gains(res), [('2025-02-03', 'b', 1.0, 150.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 2.0, 200.0)])

    @rule(R.CA_OPT_12)
    def test_a_sale_past_the_accounts_bought_contracts_writes_the_rest(self):
        res = _run([_tx('a', '2025-01-06', 1, 500),
                    _tx('a', '2025-02-03', -2, 800)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, -100.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])


class TestGrantTimingKeepsTheOtherAccountsWrite(unittest.TestCase):

    @rule(R.CA_OPT_01, R.CA_OPT_12)
    def test_a_purchase_in_another_account_is_no_buy_back(self):
        res = _run([_tx('b', '2025-01-06', -1, 250),
                    _tx('a', '2025-01-10', 1, 220)],
                   option_premium_timing='grant', option_grant_since=2025)
        # b's premium is 2025's gain at the write; a's contract is bought,
        # not b's closing purchase (which would have been a loss of 220).
        self.assertEqual(_gains(res), [('2025-01-06', 'b', 1.0, 250.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 220.0), (C, 'b', -1.0, -250.0)])


class TestTheSuperficialLossRuleReadsEveryAccount(unittest.TestCase):
    """The 30-day test still looks at every account of the person: a
    contract bought in another account within the window and held at day
    30 denies the loss."""

    LOSS = [_tx('a', '2025-01-06', 1, 500), _tx('a', '2025-01-20', -1, 200)]

    @rule(R.CA_SL_03, R.CA_SL_09, R.CA_OPT_12, R.CA_ACB_01)
    def test_a_taxable_accounts_purchase_takes_the_addition(self):
        res = _run(self.LOSS + [_tx('c', '2025-02-03', 1, 220)])
        self.assertEqual(_gains(res), [('2025-01-20', 'a', 1.0, 0.0, 300.0)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 520.0)])

    @rule(R.CA_SL_03, R.CA_SL_09)
    def test_a_registered_accounts_purchase_denies_for_good(self):
        res = _run(self.LOSS, sheltered_transactions=[
            _tx('tfsa', '2025-02-03', 1, 220)])
        self.assertEqual(_gains(res), [('2025-01-20', 'a', 1.0, 0.0, 300.0)])
        self.assertEqual(round(res['transactions'][0]['permanently_disallowed'], 2), 300.0)
        self.assertEqual(_inv(res), [])

    @rule(R.CA_SL_07, R.CA_OPT_12)
    def test_another_accounts_write_never_replaces(self):
        res = _run(self.LOSS + [_tx('b', '2025-02-03', -1, 250)])
        self.assertEqual(_gains(res), [('2025-01-20', 'a', 1.0, -300.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', -1.0, -250.0)])


class TestAssignmentBesideAnotherAccountsBoughtContract(unittest.TestCase):

    @rule(R.CA_OPT_06, R.CA_OPT_12)
    def test_the_assignment_retracts_its_own_accounts_write(self):
        shares = TaxTransaction(
            action='BUYSELL', date='2025-01-21', date_settle='2025-01-21',
            time='10:00:00', symbol='SYQ.TO', quantity=100.0, price=10.0,
            net_amount=1000.0, currency='CAD', account='b')
        res = _run([_tx('b', '2025-01-06', -1, 300, sym=P),
                    _tx('a', '2025-01-10', 1, 100, sym=P),
                    _tx('b', '2025-01-21', 1, 0, sym=P, action='ASSIGN'),
                    shares])
        # b's premium folds into b's shares (1000 - 300); a's contract is
        # still held, at its own cost.
        self.assertEqual(_gains(res), [])
        self.assertEqual(_inv(res), [('SYQ.TO', '', 100.0, 700.0), (P, '', 1.0, 100.0)])


class TestAMoveOfWrittenContracts(unittest.TestCase):
    """TJ-3192, TJ-3224: a move of written contracts between two of the
    person's accounts carries them: the receiving account's buy-back, expiry
    or assignment closes them, at the premium they were written for. The
    legs are the pipeline's own, in both of the brokers' sign conventions:
    the move's kind is read from the accounts' positions (the sender is
    short the series), never from the legs' signs."""

    def _legs(self, change, date='2025-01-15', qty=-1, sym=C):
        return _mv('b', 'c', date, qty, change=change, sym=sym)

    def _both(self, fn):
        for change in (True, False):
            with self.subTest(rows='position change' if change else 'by direction'):
                fn(self._legs(change))

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_the_receiving_accounts_buy_back_closes_them(self):
        def check(legs):
            res = _run([_tx('b', '2025-01-06', -1, 250), *legs,
                        _tx('c', '2025-02-03', 1, 100)])
            self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
            self.assertEqual(_inv(res), [])
        self._both(check)

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_the_receiving_accounts_expiry_closes_them(self):
        def check(legs):
            res = _run([_tx('b', '2025-01-06', -1, 250), *legs,
                        _tx('c', '2025-12-19', 1, 0)])
            self.assertEqual(_gains(res), [('2025-12-19', 'c', 1.0, 250.0, 0.0)])
            self.assertEqual(_inv(res), [])
        self._both(check)

    @rule(R.CA_OPT_12, R.CA_OPT_06)
    def test_the_receiving_accounts_assignment_folds_the_moved_premium(self):
        shares = TaxTransaction(
            action='BUYSELL', date='2025-01-21', date_settle='2025-01-21',
            time='10:00:00', symbol='SYQ.TO', quantity=100.0, price=10.0,
            net_amount=1000.0, currency='CAD', account='c')
        for change in (True, False):
            with self.subTest(change=change):
                res = _run([_tx('b', '2025-01-06', -2, 500, sym=P),
                            *_mv('b', 'c', '2025-01-15', -1, change=change, sym=P),
                            _tx('c', '2025-01-21', 1, 0, sym=P, action='ASSIGN'), shares])
                # One of b's two contracts is c's: its 250 folds into c's
                # shares; b keeps the other at its premium.
                self.assertEqual(_gains(res), [])
                self.assertEqual(_inv(res), [('SYQ.TO', '', 100.0, 750.0),
                                             (P, '', -1.0, -250.0)])

    @rule(R.CA_OPT_12)
    def test_the_senders_next_sale_is_a_write(self):
        # The moved contract never counts as a bought one of b's.
        def check(legs):
            res = _run([_tx('b', '2025-01-06', -1, 250), *legs,
                        _tx('c', '2025-02-03', 1, 100), _tx('b', '2025-03-01', -1, 400)])
            self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
            self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])
        self._both(check)

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_the_receiving_accounts_rows_of_the_moves_day_find_the_contract(self):
        # TJ-3225: the move takes effect before the receiver's rows of the
        # day: a buy-back, an expiry or an assignment that day closes it.
        shares = TaxTransaction(
            action='BUYSELL', date='2025-01-15', date_settle='2025-01-15',
            time='10:00:01', symbol='SYQ.TO', quantity=-100.0, price=20.0,
            net_amount=2000.0, currency='CAD', account='c')
        held = TaxTransaction(
            action='BUYSELL', date='2024-06-01', date_settle='2024-06-01',
            time='10:00:00', symbol='SYQ.TO', quantity=100.0, price=15.0,
            net_amount=1500.0, currency='CAD', account='c')
        for change in (True, False):
            legs = self._legs(change)
            with self.subTest(change=change, close='buy-back'):
                res = _run([_tx('b', '2025-01-06', -1, 250), *legs,
                            _tx('c', '2025-01-15', 1, 100)])
                self.assertEqual(_gains(res), [('2025-01-15', 'c', 1.0, 150.0, 0.0)])
                self.assertEqual(_inv(res), [])
            with self.subTest(change=change, close='expiry'):
                legs_x = self._legs(change, date='2025-12-19')
                res = _run([_tx('b', '2025-01-06', -1, 250), *legs_x,
                            _tx('c', '2025-12-19', 1, 0)])
                self.assertEqual(_gains(res), [('2025-12-19', 'c', 1.0, 250.0, 0.0)])
            with self.subTest(change=change, close='assignment'):
                res = _run([held, _tx('b', '2025-01-06', -1, 250), *legs,
                            _tx('c', '2025-01-15', 1, 0, action='ASSIGN'), shares])
                self.assertEqual(_gains(res), [('2025-01-15', 'c', 100.0, 750.0, 0.0)])
                self.assertEqual(_inv(res), [])

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_chain_of_moves_on_one_day(self):
        # TJ-3225: b to c and c to d the same day, in either order of the
        # pairing, then d buys the contract back.
        for change in (True, False):
            bc = _mv('b', 'c', '2025-01-15', -1, change=change, n=1)
            cd = _mv('c', 'd', '2025-01-15', -1, change=change, n=2)
            for order, legs in (('b-c first', bc + cd), ('c-d first', cd + bc)):
                with self.subTest(change=change, order=order):
                    res = _run([_tx('b', '2025-01-06', -1, 250), *legs,
                                _tx('d', '2025-02-03', 1, 100)])
                    self.assertEqual(_gains(res), [('2025-02-03', 'd', 1.0, 150.0, 0.0)])
                    self.assertEqual(_inv(res), [])

    @rule(R.CA_OPT_12)
    def test_a_move_after_the_last_row_reaches_the_inventory_and_the_year_end(self):
        # TJ-3226: the moved contract is c's in the inventory beside a's
        # bought one, and in the year-end snapshot when the buy-back is
        # next year's.
        for change in (True, False):
            with self.subTest(change=change):
                book = [_tx('a', '2025-01-02', 1, 500), _tx('b', '2025-01-06', -1, 250),
                        *_mv('b', 'c', '2025-12-20', -1, change=change)]
                res = _run(book, year_end='2025-12-31')
                self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'c', -1.0, -250.0)])
                ye = sorted((i.get('account') or '', i['qty'], round(i['total_cost'], 2))
                            for i in res['inventory_year_end'] if abs(i['qty']) > 1e-9)
                self.assertEqual(ye, [('', 1.0, 500.0), ('c', -1.0, -250.0)])
                res = _run(book + [_tx('c', '2026-01-10', 1, 100)], year_end='2025-12-31')
                self.assertEqual(_gains(res), [('2026-01-10', 'c', 1.0, 150.0, 0.0)])
                ye = sorted((i.get('account') or '', i['qty'])
                            for i in res['inventory_year_end'] if abs(i['qty']) > 1e-9)
                self.assertEqual(ye, [('', 1.0), ('c', -1.0)])

    @rule(R.CA_OPT_12, R.CA_ACB_OWN_MOVE)
    def test_each_accounts_own_view(self):
        # b's book: its contract left with the move, no close, no gain, in
        # either convention (b is short the series: its leg is a written
        # contract leaving, whatever its sign).
        for change in (True, False):
            with self.subTest(change=change, view='b'):
                legs = self._legs(change)
                res = _run([_tx('b', '2025-01-06', -1, 250), _leg_of(legs, 'b'),
                            _tx('b', '2025-03-01', -1, 400)])
                self.assertEqual(_gains(res), [])
                self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])
        # c's book holds no position to read the leg by: a negative leg
        # (the position's change) is a written contract arriving at a
        # premium unknown here, so its buy-back is a close of unknown
        # cost, set apart.
        res = _run([_leg_of(self._legs(True), 'c'), _tx('c', '2025-02-03', 1, 100)])
        row, = res['transactions']
        self.assertEqual((row['date'], row['account'], row['qty'], row['tainted']),
                         ('2025-02-03', 'c', 1.0, True))
        self.assertEqual(_inv(res), [])


class TestAMovesSenderByTheRowsSignConvention(unittest.TestCase):
    """TJ-3243, TJ-3244: the move's sender is read by how the rows' broker
    signs a transfer row (carried on the legs), then its kind from the
    sender's position; where the convention is not known, the positions
    decide when one reading fits, and a Warning names the reading taken
    when more than one fits."""

    def _both_short(self, change):
        return [_tx('b', '2025-01-06', -1, 250), _tx('c', '2025-01-10', -1, 300),
                *_mv('b', 'c', '2025-01-15', -1, change=change),
                _tx('c', '2025-02-03', 2, 200), _tx('b', '2025-03-01', -1, 400)]

    @rule(R.CA_OPT_12, R.CA_OPT_06)
    def test_both_accounts_short_the_convention_decides(self):
        for change in (True, False):
            with self.subTest(change=change):
                res, err = _run_err(self._both_short(change))
                # c's buy-back of 2 closes its own 300 and the moved 250
                # (their average, 275 each); b's next write is a write.
                self.assertEqual(_gains(res), [('2025-02-03', 'c', 2.0, 350.0, 0.0)])
                self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])
                self.assertNotIn('warning', err)

    @rule(R.CA_OPT_12, R.CA_OPT_06)
    def test_a_chain_through_an_account_already_short(self):
        # The contract b wrote travels b to c to d: d's buy-back closes it
        # (250), c's its own (300), in either convention and pairing order.
        for change in (True, False):
            bc = _mv('b', 'c', '2025-01-15', -1, change=change, n=1)
            cd = _mv('c', 'd', '2025-01-15', -1, change=change, n=2)
            for order, legs in (('b-c first', bc + cd), ('c-d first', cd + bc)):
                with self.subTest(change=change, order=order):
                    res = _run([_tx('b', '2025-01-06', -1, 250), _tx('c', '2025-01-10', -1, 300),
                                *legs, _tx('d', '2025-02-03', 1, 100),
                                _tx('c', '2025-02-04', 1, 120)])
                    self.assertEqual(_gains(res), [('2025-02-03', 'd', 1.0, 150.0, 0.0),
                                                   ('2025-02-04', 'c', 1.0, 180.0, 0.0)])

    def _bought_into_a_short(self, change, broker=None):
        return [_tx('a', '2025-01-06', 1, 500), _tx('c', '2025-01-10', -1, 250),
                *_mv('a', 'c', '2025-01-15', 1, change=change, broker=broker),
                _tx('c', '2025-02-03', 1, 100), _tx('a', '2025-03-01', -1, 600)]

    @rule(R.CA_OPT_12, R.CA_ACB_01)
    def test_a_bought_contract_moved_into_an_account_short_the_series(self):
        # a's bought contract arrives in c beside c's written one: c's
        # purchase closes its write, a's later sale is a write of its own.
        # Rows signed by direction say a sends; rows that are the
        # positions' changes read both ways (a's bought contract leaving,
        # or c's written one): the bought reading, with the Warning.
        for change in (True, False):
            with self.subTest(change=change):
                res, err = _run_err(self._bought_into_a_short(change))
                self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
                self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'a', -1.0, -600.0)])
                self.assertEqual(err.count('warning: ATTENTION: own-account move'),
                                 1 if change else 0)

    @rule(R.CA_OPT_12)
    def test_an_unknown_convention_warns_when_both_readings_fit(self):
        res, err = _run_err(self._bought_into_a_short(True, broker='other'))
        self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'a', -1.0, -600.0)])
        self.assertEqual(err.count('warning: ATTENTION: own-account move'), 1)
        # The first line (what a run shows) carries the series, the date,
        # the accounts and the reading booked; the continuation the rest.
        self.assertIn(
            f"warning: ATTENTION: own-account move: {C} 2025-01-15 a->c: booked as 1 bought "
            "leaving a\n    It also reads as c's written contracts leaving; if the move "
            "was otherwise, both accounts' figures for the series are off.", err)
        head = next(ln for ln in err.splitlines() if ln.startswith('warning: ATTENTION: own-account'))
        self.assertLessEqual(len(head), 120)

    @rule(R.CA_OPT_12)
    def test_an_unknown_convention_reads_the_positions_when_one_fits(self):
        # Only b is short: the move is b's written contract whatever the sign.
        for change in (True, False):
            with self.subTest(change=change):
                res, err = _run_err([_tx('b', '2025-01-06', -1, 250),
                                     *_mv('b', 'c', '2025-01-15', -1, change=change, broker='other'),
                                     _tx('c', '2025-02-03', 1, 100)])
                self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
                self.assertNotIn('warning', err)


class TestAWrittenMoveTakesEffectWhenTheSenderHoldsIt(unittest.TestCase):
    """TJ-3242, TJ-3246: a written move takes effect at the first moment of
    its day at which the sender holds what it moves: the start of the day
    when it held the contract overnight, right after its own write when it
    wrote it that day; the sender's position leaves at that moment too."""

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_contract_written_and_moved_the_same_day(self):
        for change in (True, False):
            legs = _mv('b', 'c', '2025-01-15', -1, change=change)
            with self.subTest(change=change, close='later'):
                res = _run([_tx('b', '2025-01-15', -1, 250), *legs,
                            _tx('c', '2025-02-03', 1, 100), _tx('b', '2025-03-01', -1, 400)])
                self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
                self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])
            with self.subTest(change=change, close='the same day'):
                late = TaxTransaction(
                    action='BUYSELL', date='2025-01-15', date_settle='2025-01-15',
                    time='15:00:00', symbol=C, quantity=1.0, price=1.0, net_amount=100.0,
                    currency='CAD', account='c')
                res = _run([_tx('b', '2025-01-15', -1, 250), *legs, late])
                self.assertEqual(_gains(res), [('2025-01-15', 'c', 1.0, 150.0, 0.0)])
                self.assertEqual(_inv(res), [])
            with self.subTest(change=change, view='b'):
                res = _run([_tx('b', '2025-01-15', -1, 250), _leg_of(legs, 'b'),
                            _tx('b', '2025-03-01', -1, 400)])
                self.assertEqual(_gains(res), [])
                self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_the_senders_purchase_on_the_moves_day_is_its_own(self):
        # b held the contract overnight: the move takes it at the start of
        # the day, so b's purchase that day is a bought contract, and c's
        # purchase later closes the moved one; c's sale after that writes.
        for change in (True, False):
            with self.subTest(change=change):
                res = _run([_tx('b', '2025-01-06', -1, 250), *_mv('b', 'c', '2025-01-15', -1, change=change),
                            _tx('b', '2025-01-15', 1, 100), _tx('c', '2025-02-03', 1, 90),
                            _tx('c', '2025-03-01', -1, 500)])
                self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 160.0, 0.0)])
                self.assertEqual(_inv(res), [(C, '', 1.0, 100.0), (C, 'c', -1.0, -500.0)])


class TestTheSnapshotsAndTheMoves(unittest.TestCase):
    """TJ-3241, TJ-3247: a move dated after the year end is not in the
    year-end snapshot; the last timeline step runs after the moves."""

    @rule(R.CA_OPT_12)
    def test_a_move_after_the_year_end_stays_out_of_the_snapshot(self):
        for change in (True, False):
            with self.subTest(change=change):
                res = _run([_tx('a', '2025-01-02', 1, 500), _tx('b', '2025-01-06', -1, 250),
                            *_mv('b', 'c', '2026-01-05', -1, change=change),
                            _tx('c', '2026-01-10', 1, 100)], year_end='2025-12-31')
                self.assertEqual(_gains(res), [('2026-01-10', 'c', 1.0, 150.0, 0.0)])
                ye = sorted((i.get('account') or '', i['qty'], round(i['total_cost'], 2))
                            for i in res['inventory_year_end'] if abs(i['qty']) > 1e-9)
                self.assertEqual(ye, [('', 1.0, 500.0), ('b', -1.0, -250.0)])

    @rule(R.CA_OPT_12)
    def test_the_timelines_last_step_sees_a_trailing_move(self):
        for change in (True, False):
            with self.subTest(change=change):
                res = _run([_tx('a', '2025-01-02', 1, 500), _tx('b', '2025-01-06', -1, 250),
                            *_mv('b', 'c', '2025-12-20', -1, change=change)],
                           year_end='2025-12-31', cost_timeline='settle')
                self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'c', -1.0, -250.0)])
                rows = res['cost_timeline']['shorts']['rows']
                self.assertEqual((rows[-1][2], rows[-1][4]), ('c', -1.0))


class TestTheEdgeCaseReportReadsAMovesLegsAsAPair(unittest.TestCase):
    """TJ-3245: `tjs edge-cases` reads a move's two legs together, as the
    engine does: a written contract moved into c, bought back by c inside
    a loss's window, is c's close, not an acquisition held at day 30."""

    @rule(R.CA_SL_02, R.CA_OPT_12)
    def test_the_receivers_buy_back_is_no_replacement(self):
        for change in (True, False):
            with self.subTest(change=change):
                a = [_t('a', 'BUYSELL', '2024-12-20', C, 1, -500.0, price=5.0),
                     _t('a', 'BUYSELL', '2025-01-05', C, -1, 200.0, price=2.0)]
                b = [_t('b', 'BUYSELL', '2025-01-10', C, -1, 250.0, price=2.5),
                     _leg_of(_mv('b', 'c', '2025-01-20', -1, change=change), 'b')]
                c = [_leg_of(_mv('b', 'c', '2025-01-20', -1, change=change), 'c'),
                     _t('c', 'BUYSELL', '2025-02-03', C, 1, -100.0, price=1.0)]
                doc, edge, _text = _edge({'a': a, 'b': b, 'c': c})
                self.assertEqual([(t['date'], t['account'], round(t['gain'], 2))
                                  for t in doc['transactions']],
                                 [('2025-01-05', 'a', -300.0), ('2025-02-03', 'c', 150.0)])
                w, = edge['window_edges']
                self.assertEqual((w['loss_date'], w['held_at_day30'], w['verdict']),
                                 ('2025-01-05', 0.0, 'allowed'))


class TestADaysMovesResolveByFittingReadings(unittest.TestCase):
    """TJ-3256, TJ-3260, TJ-3261: a day's moves are read on the positions
    as they stand, a sure reading first and kept while its units fit, the
    unread ones read again as the day's moves apply; the fallback only for
    a move no reading fits, with the Warning; the pick among readings that
    fit."""

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_chain_on_a_day_with_no_rows_whatever_the_accounts_names(self):
        # TJ-3256: b writes 2; on a day with no rows the contract travels
        # b to d to a; each account's close is its own. The accounts'
        # names permuted, so the pairing's order never carries it.
        for names in (('b', 'd', 'a'), ('a', 'b', 'c'), ('c', 'b', 'a'), ('d', 'a', 'b')):
            w, mid, last = names
            for change in (True, False):
                legs = (_mv(w, mid, '2025-11-07', -1, change=change, n=1)
                        + _mv(mid, last, '2025-11-07', -1, change=change, n=2))
                with self.subTest(names=names, change=change):
                    res, err = _run_err([_tx(w, '2025-10-25', -2, 930), *legs,
                                         _tx(last, '2025-11-27', 1, 534), _tx(w, '2025-12-28', 1, 345),
                                         _tx(mid, '2026-01-13', 1, 223)])
                    self.assertEqual(_gains(res), [('2025-11-27', last, 1.0, -69.0, 0.0),
                                                   ('2025-12-28', w, 1.0, 120.0, 0.0)])
                    self.assertEqual(_inv(res), [(C, '', 1.0, 223.0)])
                    self.assertNotIn('warning', err)

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_move_sure_at_the_days_start_keeps_its_reading(self):
        # TJ-3260: c wrote 2, b bought 2; on one day c moves a written
        # contract to b and b a bought one to d: b's move stays a bought
        # one after c's lands, under either convention.
        for change in (True, False):
            with self.subTest(change=change):
                cb = _mv('c', 'b', '2025-06-10', -1, change=change, n=1)
                bd = _mv('b', 'd', '2025-06-10', 1, change=change, n=2)
                res, err = _run_err([_tx('c', '2025-01-10', -2, 600), _tx('b', '2025-02-05', 2, 1000),
                                     *cb, *bd, _tx('d', '2025-07-01', -1, 700),
                                     _tx('b', '2025-07-02', 1, 100), _tx('c', '2025-07-03', 1, 120)])
                if change:
                    # rows that are the positions' changes read c's move both
                    # ways (b holds bought contracts, c written ones): the
                    # bought reading, b's contract to c, with the Warning; b's
                    # purchase then buys.
                    self.assertEqual(_gains(res), [('2025-07-01', 'd', 1.0, 200.0, 0.0),
                                                   ('2025-07-03', 'c', 1.0, 180.0, 0.0)])
                    self.assertEqual(err.count('warning: ATTENTION'), 1)
                else:
                    self.assertEqual(_gains(res), [('2025-07-01', 'd', 1.0, 200.0, 0.0),
                                                   ('2025-07-02', 'b', 1.0, 200.0, 0.0),
                                                   ('2025-07-03', 'c', 1.0, 180.0, 0.0)])
                    self.assertNotIn('warning', err)

    @rule(R.CA_OPT_12)
    def test_a_sender_holding_both_kinds_is_read_by_the_units(self):
        # c wrote 1 and holds 2 bought contracts moved in: its move of 2 to
        # d is of bought contracts (the units do not fit its short).
        for change in (True, False):
            with self.subTest(change=change):
                res, err = _run_err([_tx('c', '2025-01-06', -1, 300), _tx('a', '2025-01-07', 2, 1000),
                                     *_mv('a', 'c', '2025-01-10', 2, change=change, n=1),
                                     *_mv('c', 'd', '2025-01-15', 2, change=change, n=2),
                                     _tx('d', '2025-02-03', -2, 1300), _tx('c', '2025-02-04', 1, 50)])
                self.assertEqual(_gains(res), [('2025-02-03', 'd', 2.0, 300.0, 0.0),
                                               ('2025-02-04', 'c', 1.0, 250.0, 0.0)])
                self.assertEqual(_inv(res), [])
                self.assertNotIn('warning', err)

    @rule(R.CA_OPT_12)
    def test_the_pick_is_a_reading_that_fits(self):
        # TJ-3261: c and b each wrote 2, c also holds a bought contract; a
        # move of 2 between c and b with the convention not known reads as
        # c's written contracts or b's: the pick is one of those (c's, the
        # rows' sign's), never c's bought contract it does not hold enough of.
        res, err = _run_err([_tx('c', '2025-01-06', -2, 500), _tx('b', '2025-01-07', -2, 600),
                             _tx('a', '2025-01-08', 1, 300),
                             *_mv('a', 'c', '2025-01-10', 1, change=False, broker='other', n=1),
                             *_mv('c', 'b', '2025-01-15', -2, change=False, broker='other', n=2),
                             _tx('b', '2025-02-03', 2, 200), _tx('c', '2025-02-04', 2, 180)])
        self.assertIn("2025-01-15 c->b: booked as 2 written leaving c", err)
        self.assertIn("It also reads as b's written contracts leaving;", err)
        # b's buy-back of 2 closes two of its four written (average 275).
        self.assertEqual(_gains(res), [('2025-02-03', 'b', 2.0, 350.0, 0.0)])


class TestTheMovesDayReadsTradesByTheirDate(unittest.TestCase):
    """TJ-3258: a move reads the sender's position with its trades dated
    on or before the move's date, whatever their settlement date."""

    def _write(self, account, date, qty, net, settle):
        t = _tx(account, date, qty, net)
        t.date_settle = settle
        return t

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_contract_written_and_moved_the_day_it_is_written_settling_later(self):
        for change in (True, False):
            legs = _mv('b', 'c', '2025-01-15', -1, change=change)
            with self.subTest(change=change, view='blended'):
                res = _run([self._write('b', '2025-01-15', -1, 250, '2025-01-16'), *legs,
                            _tx('c', '2025-02-03', 1, 100), _tx('b', '2025-03-01', -1, 400)])
                self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
                self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])
            with self.subTest(change=change, view='b'):
                res, err = _run_err([self._write('b', '2025-01-15', -1, 250, '2025-01-16'),
                                     _leg_of(legs, 'b'), _tx('b', '2025-03-01', -1, 400)])
                self.assertEqual(_gains(res), [])
                self.assertEqual(_inv(res), [(C, '', -1.0, -400.0)])
                self.assertNotIn('conservation', err)


class TestTheYearEndSeesOnlyTheYearsMoves(unittest.TestCase):
    """TJ-3257: a move dated after the year end that is the book's last
    event stays out of the year-end snapshot."""

    @rule(R.CA_OPT_12)
    def test_a_trailing_move_after_the_year_end(self):
        for change in (True, False):
            with self.subTest(change=change):
                res = _run([_tx('a', '2025-01-02', 1, 500), _tx('b', '2025-04-20', -2, 1128),
                            *_mv('b', 'c', '2026-01-13', -1, change=change)], year_end='2025-12-31')
                ye = sorted((i.get('account') or '', i['qty']) for i in res['inventory_year_end']
                            if abs(i['qty']) > 1e-9)
                self.assertEqual(ye, [('', 1.0), ('b', -2.0)])
                self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'b', -1.0, -564.0),
                                             (C, 'c', -1.0, -564.0)])


class TestTheReceiverNeverHoldsALongThePoolDoesNot(unittest.TestCase):
    """TJ-3262: a bought move of an option series leaves the sender at the
    move's reading, so its sale that day past what it still holds writes,
    and the receiver's contract stays in the pool."""

    @rule(R.CA_OPT_12, R.CA_ACB_01)
    def test_the_senders_same_day_sale_past_its_contracts_writes(self):
        # fuzz seed 253's shape: c bought 2, moves 1 to a and sells 2 that
        # day; a's later sale is of its own contract, not a write.
        for change in (True, False):
            with self.subTest(change=change):
                res = _run([_tx('c', '2025-02-03', 2, 1082), *_mv('c', 'a', '2025-03-17', 1, change=change),
                            _tx('c', '2025-03-17', -2, 810), _tx('a', '2025-10-09', -1, 495)])
                self.assertEqual(_gains(res), [('2025-03-17', 'c', 1.0, -136.0, 0.0),
                                               ('2025-10-09', 'a', 1.0, -46.0, 0.0)])
                self.assertEqual(_inv(res), [(C, '', -1.0, -405.0)])


class TestTheReceiversOwnViewByTheConvention(unittest.TestCase):
    """TJ-3263: the receiving account's own view reads its lone leg by the
    move's convention: an account already short the series that receives
    another written contract (the positions' changes: a negative leg)
    holds two, and its buy-back of two closes them."""

    @rule(R.CA_OPT_12, R.CA_ACB_OWN_MOVE)
    def test_an_arriving_written_contract_in_an_account_already_short(self):
        legs = _mv('b', 'c', '2025-01-15', -1, change=True)
        res = _run([_tx('c', '2025-01-06', -1, 300), _leg_of(legs, 'c'), _tx('c', '2025-02-03', 2, 200)])
        row, = res['transactions']
        self.assertEqual((row['date'], row['qty'], row['tainted']), ('2025-02-03', 2.0, True))
        self.assertEqual(_inv(res), [])


class TestMoveFuzz(unittest.TestCase):
    """TJ-3264, TJ-3272: a seeded subset of the review's invariant fuzz
    (tests/_move_fuzz.py): no crash; each account's router position is its
    rows' and moves'; no short lot without a router short and none the other
    way; the Warning once per move that reads more than one way on the
    positions standing when it is read, in every convention, whatever it
    settles on; one answer in both conventions where the reading is sure;
    the US engine's figures unchanged."""

    SEEDS = range(1, 1001)
    # The US engine's figures on the generator's books (seeds 1-60), the
    # same digest on the tree before TJ-2735's fifth round (cb3165b8).
    US_DIGEST = 'd8df1f38586cf06f8a4a77b1be6060a7f3104a0600592673c3a8ac3e91a90487'

    def test_invariants_in_every_convention(self):
        cls, orig = _move_fuzz.patch_keep()
        self.addCleanup(setattr, cls, 'keep', orig)
        t0 = time.time()
        figs = {}
        for conv in ('direction', 'change', 'unknown', 'unknownc'):
            for seed in self.SEEDS:
                book = _move_fuzz.gen_book(seed, conv)
                r = _move_fuzz.run_one(book, conv)
                figs[(conv, seed)] = (r['fig'], book['n_amb'] > 0)
                with self.subTest(conv=conv, seed=seed):
                    # The Warning once per move read more than one way, and
                    # for no other; the positions the model's, unless an
                    # ambiguous move's booking is the engine's order's.
                    self.assertEqual(len(r['warn']), book['n_amb'])
                    if 'amb-unsettled' not in book['flags']:
                        self.assertEqual(r['problems'], [])
        # (d): the same book under both conventions, where the reading is sure
        same = 0
        for seed in self.SEEDS:
            a, b = _move_fuzz.gen_book(seed, 'direction'), _move_fuzz.gen_book(seed, 'change')
            if a['moves'] != b['moves'] or figs[('direction', seed)][1] or figs[('change', seed)][1]:
                continue
            if [(r.account, r.date, r.quantity) for r in a['rows']] != [(r.account, r.date, r.quantity) for r in b['rows']]:
                continue
            same += 1
            with self.subTest(seed=seed, check='direction vs change'):
                self.assertEqual(figs[('direction', seed)][0], figs[('change', seed)][0])
        self.assertGreater(same, len(self.SEEDS) // 2)
        self.assertLess(time.time() - t0, 30.0)

    def test_the_us_engine_is_unchanged(self):
        us = hashlib.sha256()
        for seed in range(1, 61):
            book = _move_fuzz.gen_book(seed, 'direction')
            us.update(json.dumps(_move_fuzz.run_one(book, 'direction', us=True)['fig']).encode())
        self.assertEqual(us.hexdigest(), self.US_DIGEST)


class TestEachPoolHasItsOwnTrace(unittest.TestCase):
    """TJ-3228: a series bought in one account and written in another shows
    two ACB traces, the bought contracts' and the written contracts' own."""

    @rule(R.CA_OPT_12)
    def test_the_entrys_book_traces(self):
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res = CanadaTaxRules().compute_gains(TestTheEntrysBook.BOOK, trace=True)
        traces = {(r['date'], r['account']): [str(x) for x in r['trace']]
                  for r in res['transactions']}
        sale = traces[('2025-03-04', 'a')]
        self.assertEqual(sale[0], f"# --- ACB CALCULATION TRACE: {C} ---")
        self.assertFalse([x for x in sale if 'Pool_Qty:    -1.0000' in x
                          or 'Pool_Qty:    -2.0000' in x], sale)
        self.assertTrue(any('Pool_ACB:   520.0000' in x for x in sale), sale)
        buy_back = traces[('2025-03-10', 'b')]
        self.assertEqual(buy_back[0], f"# --- ACB CALCULATION TRACE: {C} (written) ---")
        self.assertEqual([x[2:12] for x in buy_back[1:]],
                         ['2025-01-25', '2025-03-03', '2025-03-10'])

    @rule(R.CA_CORP_02, R.CA_OPT_12)
    def test_a_renames_line_goes_to_each_pools_trace(self):
        # TJ-3248: the written pool's trace keeps its history across the
        # rename, with the rename's line, as the bought pool's does.
        C2 = 'SYR251219C00010000.TO'
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res = CanadaTaxRules().compute_gains(
                [_tx('a', '2025-01-06', 1, 500), _tx('b', '2025-01-10', -1, 250),
                 _split(C, C2), _tx('b', '2025-03-10', 1, 100, sym=C2),
                 _tx('a', '2025-03-11', -1, 600, sym=C2)], trace=True)
        traces = {r['account']: [str(x) for x in r['trace']] for r in res['transactions']}
        for acct, head in (('a', f"# --- ACB CALCULATION TRACE: {C2} ---"),
                           ('b', f"# --- ACB CALCULATION TRACE: {C2} (written) ---")):
            with self.subTest(acct=acct):
                self.assertEqual(traces[acct][0], head)
                self.assertTrue(any('SPLIT-RENAME' in x for x in traces[acct]), traces[acct])


class TestARenameIntoAnExistingShortPool(unittest.TestCase):
    """TJ-3193: the short pool's rename merge keeps what the long pool's
    keeps: the taint, the position's start, the currency check."""

    C2 = 'SYR251219C00010000.TO'

    @rule(R.CA_CORP_02, R.CA_OPT_12)
    def test_the_taint_and_the_start_date_ride_the_rename(self):
        book = [_tx('b', '2025-01-01', -1, 0, action='OPENING_BALANCE'),
                _tx('b', '2025-01-07', -1, 300, sym=self.C2), _split(C, self.C2)]
        res = _run(book + [_tx('b', '2025-04-01', 2, 100, sym=self.C2)])
        row, = res['transactions']
        # One of the two premiums is unknown: the close says so.
        self.assertEqual((round(row['gain'], 2), row['tainted']), (200.0, True))
        res = _run(book)
        short, = [i for i in res['inventory'] if abs(i['qty']) > 1e-9]
        self.assertEqual((short['symbol'], short['qty'], short['position_start_date'],
                          short.get('unknown_cost')),
                         (self.C2, -2.0, '2025-01-01', True))

    @rule(R.CA_CORP_02)
    def test_a_currency_mix_is_refused(self):
        usd = TaxTransaction(
            action='BUYSELL', date='2025-01-07', date_settle='2025-01-07',
            time='10:00:00', symbol=self.C2, quantity=-1.0, price=3.0,
            net_amount=300.0, currency='USD', account='b')
        with self.assertRaises(ValueError) as cm:
            _run([_tx('b', '2025-01-06', -1, 250), usd, _split(C, self.C2)])
        self.assertIn('currency mismatch', str(cm.exception))


class TestGrantTimingRowsBesideALong(unittest.TestCase):
    """TJ-3194: under grant timing each writer's row beside another
    account's bought contract carries its own cost and recognised premium."""

    @rule(R.CA_OPT_01, R.CA_OPT_12)
    def test_each_writers_row_carries_its_own_recognised_premium(self):
        res = _run([_tx('a', '2025-01-06', 1, 500), _tx('b', '2025-01-10', -1, 250),
                    _tx('c', '2025-01-12', -1, 900)],
                   option_premium_timing='grant', option_grant_since=2025)
        self.assertEqual(_gains(res), [('2025-01-10', 'b', 1.0, 250.0, 0.0),
                                       ('2025-01-12', 'c', 1.0, 900.0, 0.0)])
        rows = {i.get('account') or '': (round(i['qty'], 6), round(i['total_cost'], 2),
                                         i.get('recognised_premium'))
                for i in res['inventory'] if abs(i['qty']) > 1e-9}
        self.assertEqual(rows, {'': (1.0, 500.0, None), 'b': (-1.0, -250.0, 250.0),
                                'c': (-1.0, -900.0, 900.0)})


class TestAnOpeningWrittenPositionBesideALong(unittest.TestCase):
    """TJ-3196: an opening position of written contracts (a .tt OPENING
    line, an OPENING_BALANCE row) is a short of its own in its account,
    never netted against the account's bought contracts."""

    @rule(R.CA_OPT_12, R.CA_OPEN_01)
    def test_a_tt_opening_short_beside_a_bought_contract(self):
        book = [_tx('a', '2025-01-06', 1, 500),
                _tx('a', '2025-01-10', -1, 300, type=OPENING_TYPE)]
        res = _run(book)
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0), (C, 'a', -1.0, -300.0)])
        res = _run(book + [_tx('a', '2025-02-03', 1, 100)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, 200.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0)])

    @rule(R.CA_OPT_12, R.CA_OPEN_01)
    def test_an_unknown_premium_opening_short_beside_a_bought_contract(self):
        res = _run([_tx('a', '2025-01-06', 1, 500),
                    _tx('a', '2025-01-10', -1, 0, action='OPENING_BALANCE'),
                    _tx('a', '2025-02-03', 1, 100)])
        row, = res['transactions']
        self.assertEqual((row['date'], row['qty'], row['tainted']), ('2025-02-03', 1.0, True))
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0)])


class TestTheEdgeCaseReportReadsOptionsPerAccount(unittest.TestCase):
    """TJ-3195: `tjs edge-cases` reads an option series per account as the
    engine does, with no share short mark anywhere in the book."""

    @rule(R.CA_SL_02, R.CA_OPT_12)
    def test_a_contract_bought_at_day_29_is_held_at_day_30(self):
        a = [_t('a', 'BUYSELL', '2024-12-20', C, 1, -500.0, price=5.0),
             _t('a', 'BUYSELL', '2025-01-05', C, -1, 200.0, price=2.0),
             _t('a', 'BUYSELL', '2025-02-03', C, 1, -220.0, price=2.2),
             _t('a', 'BUYSELL', '2025-03-20', C, -1, 400.0, price=4.0)]
        b = [_t('b', 'BUYSELL', '2025-01-10', C, -1, 250.0, price=2.5),
             _t('b', 'BUYSELL', '2025-03-03', C, -1, 300.0, price=3.0),
             _t('b', 'BUYSELL', '2025-03-25', C, 1, -100.0, price=1.0)]
        doc, edge, _text = _edge({'a': a, 'b': b})
        loss = next(t for t in doc['transactions'] if t['date'] == '2025-01-05')
        self.assertEqual(round(loss['disallowed_amount'], 2), 300.0)
        w, = edge['window_edges']
        # b's write and buy-back are b's own position: a's contract is
        # held at day 30, as the engine found.
        self.assertEqual((w['loss_date'], w['held_at_day30']), ('2025-01-05', 1.0))
        self.assertTrue(w['verdict'].startswith('denied'), w['verdict'])


S = 'SYQ.TO'                          # the underlying


def _settled(account, date, qty, net, settle, sym=C, **kw):
    t = _tx(account, date, qty, net, sym=sym, **kw)
    t.date_settle = settle
    return t


def _move_warnings(err):
    return [ln for ln in err.splitlines() if ln.startswith('warning: ATTENTION: own-account move')]


class TestTheFallbackIsABoughtMoveFromTheNegativeRowsAccount(unittest.TestCase):
    """TJ-3269: a move no reading fits by the end of its day is booked as
    bought contracts leaving the account whose row is negative, in every
    convention (a written contract no account holds was booked with
    direction rows, a short the pool had no lot for), with the Warning."""

    CONVS = (('direction', False, None), ('change', True, None), ('unknown', False, 'other'))

    @rule(R.CA_OPT_12)
    def test_a_bought_move_from_an_account_holding_nothing(self):
        # J2: b holds nothing on 01-10, buys on 01-15; c sells 02-03: c's
        # sale takes the pooled contract (+100) whatever the convention.
        for name, change, broker in self.CONVS:
            with self.subTest(conv=name):
                res, err = _run_err([*_mv('b', 'c', '2025-01-10', 1, change=change, broker=broker),
                                     _tx('b', '2025-01-15', 1, 500), _tx('c', '2025-02-03', -1, 600)])
                self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 100.0, 0.0)])
                self.assertEqual(_inv(res), [])
                self.assertEqual(_move_warnings(err),
                                 [f"warning: ATTENTION: own-account move: {C} 2025-01-10: "
                                  "none held; booked as 1 bought leaving b"])
                self.assertIn("    The move: 1 bought, b -> c. No account holds what it moves "
                              "(a write or purchase is missing from the files: tjs "
                              "find-missing-history); booked as bought contracts leaving the "
                              "account whose row is negative, so both accounts' figures for "
                              "the series are off.", err)

    @rule(R.CA_OPT_12)
    def test_a_written_move_from_an_account_holding_nothing(self):
        # J1: the rows say b moves a written contract it does not hold; b
        # writes on 01-15, c buys on 02-03. Rows signed by direction (or
        # not known) have b's row negative: a bought contract leaving b,
        # so c's purchase is a contract bought and b's write stays open.
        # IB's rows have c's row negative: a bought contract leaving c.
        for name, change, broker in self.CONVS:
            with self.subTest(conv=name):
                res, err = _run_err([*_mv('b', 'c', '2025-01-10', -1, change=change, broker=broker),
                                     _tx('b', '2025-01-15', -1, 250), _tx('c', '2025-02-03', 1, 100)])
                neg = 'c' if change else 'b'
                self.assertEqual(_move_warnings(err),
                                 [f"warning: ATTENTION: own-account move: {C} 2025-01-10: "
                                  f"none held; booked as 1 bought leaving {neg}"])
                if change:
                    self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
                    self.assertEqual(_inv(res), [])
                else:
                    self.assertEqual(_gains(res), [])
                    self.assertEqual(_inv(res), [(C, '', 1.0, 100.0), (C, 'b', -1.0, -250.0)])


class TestTheMoveWarningFitsTheWidth(unittest.TestCase):
    """TJ-3270: the Warning's shown line, at width 120 with a 24-character
    series and two 12-character accounts, always carries the series, the
    date and the reading booked; the accounts' names give way first (the
    receiver's to the continuation, then the sender's cut)."""

    SER = 'SYQXYZ251219C00020000.TO'
    A, B = 'margin_abcde', 'margin_fghij'

    def _head(self, err):
        from taxjson.lib.out import relabel
        line, = _move_warnings(err)
        self.assertLessEqual(len(line), 120)
        shown = relabel(line, source=False)
        self.assertLessEqual(len(shown), 120)
        self.assertTrue(shown.startswith('Warning: Move between your accounts: '), shown)
        return line, shown

    @rule(R.CA_OPT_12)
    def test_the_ambiguous_form(self):
        # a's bought contract moved into b, which wrote one, with the
        # convention not known: both readings fit.
        res, err = _run_err([_tx(self.A, '2025-01-06', 1, 500, sym=self.SER),
                             _tx(self.B, '2025-01-10', -1, 250, sym=self.SER),
                             *_mv(self.A, self.B, '2025-01-15', 1, change=False, broker='other', sym=self.SER),
                             _tx(self.B, '2025-02-03', 1, 100, sym=self.SER)])
        self.assertEqual(_gains(res), [('2025-02-03', self.B, 1.0, 150.0, 0.0)])
        line, _shown = self._head(err)
        self.assertEqual(line, f"warning: ATTENTION: own-account move: {self.SER} 2025-01-15: "
                               f"booked as 1 bought leaving {self.A}")
        self.assertIn(f"    The move: 1 bought, {self.A} -> {self.B}. It also reads as "
                      f"{self.B}'s written contracts leaving; if the move was otherwise, "
                      "both accounts' figures for the series are off.", err)

    @rule(R.CA_OPT_12)
    def test_the_fallback_form(self):
        res, err = _run_err([*_mv(self.A, self.B, '2025-01-10', 1, change=False, sym=self.SER),
                             _tx(self.A, '2025-01-15', 1, 500, sym=self.SER),
                             _tx(self.B, '2025-02-03', -1, 600, sym=self.SER)])
        self.assertEqual(_gains(res), [('2025-02-03', self.B, 1.0, 100.0, 0.0)])
        line, _shown = self._head(err)
        # The sender's name is cut to fit (the continuation has it whole).
        self.assertEqual(line, f"warning: ATTENTION: own-account move: {self.SER} 2025-01-10: "
                               "none held; booked as 1 bought leaving margi..")
        self.assertIn(f"    The move: 1 bought, {self.A} -> {self.B}. No account holds", err)

    @rule(R.CA_OPT_12)
    def test_short_names_keep_the_whole_form(self):
        res, err = _run_err([_tx('a', '2025-01-06', 1, 500), _tx('c', '2025-01-10', -1, 250),
                             *_mv('a', 'c', '2025-01-15', 1, change=False, broker='other'),
                             _tx('c', '2025-02-03', 1, 100)])
        self.assertEqual(_gains(res), [('2025-02-03', 'c', 1.0, 150.0, 0.0)])
        line, _shown = self._head(err)
        self.assertEqual(line, f"warning: ATTENTION: own-account move: {C} 2025-01-15 a->c: "
                               "booked as 1 bought leaving a")
        self.assertIn("leaving a\n    It also reads as c's written contracts leaving; if the "
                      "move was otherwise, both accounts' figures for the series are off.", err)


class TestAMoveReadsTheSendersTradesDatedBeforeIt(unittest.TestCase):
    """TJ-3271: a move reads the sender's position after every trade dated
    before the move's date, whatever its settlement, for a reading that
    fits too: b's purchase dated 01-14 settling 01-15 covers b's write and
    buys one before the 01-15 move takes the bought contract."""

    CONVS = (('direction', False, None), ('change', True, None), ('unknown', False, 'other'))

    def _book(self, change, broker):
        legs = _mv('b', 'c', '2025-01-15', 1, change=change, broker=broker)
        return [_tx('b', '2025-01-02', -1, 250), _settled('b', '2025-01-14', 2, 200, '2025-01-15'),
                *legs, _tx('c', '2025-02-03', -1, 600)], legs

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_purchase_settling_on_the_moves_day_is_read_first(self):
        for name, change, broker in self.CONVS:
            rows, legs = self._book(change, broker)
            with self.subTest(conv=name, view='blended'):
                res, err = _run_err(rows)
                self.assertEqual(_gains(res), [('2025-01-14', 'b', 1.0, 150.0, 0.0),
                                               ('2025-02-03', 'c', 1.0, 500.0, 0.0)])
                self.assertEqual(_inv(res), [])
                self.assertNotIn('warning', err)
            with self.subTest(conv=name, view='b'):
                res, err = _run_err([t for t in rows if t.account == 'b' and t.action == 'BUYSELL']
                                    + [_leg_of(legs, 'b')])
                self.assertEqual(_gains(res), [('2025-01-14', 'b', 1.0, 150.0, 0.0)])
                self.assertEqual(_inv(res), [])
                self.assertNotIn('warning', err)

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_the_control_settling_the_same_day(self):
        res = _run([_tx('b', '2025-01-02', -1, 250), _tx('b', '2025-01-14', 2, 200),
                    *_mv('b', 'c', '2025-01-15', 1, change=False), _tx('c', '2025-02-03', -1, 600)])
        self.assertEqual(_gains(res), [('2025-01-14', 'b', 1.0, 150.0, 0.0),
                                       ('2025-02-03', 'c', 1.0, 500.0, 0.0)])


class TestAnAmbiguousMoveSettledByTheDaysOtherMovesIsNamed(unittest.TestCase):
    """TJ-3272: a move that reads more than one way on the positions it is
    read on is named by the Warning even when the day's other moves leave
    it one reading. U1: a and b each wrote 1; on 02-10 b moves one written
    contract to a and one to c and writes one that day."""

    def _book(self, change, broker):
        return [_tx('a', '2025-01-06', -1, 300), _tx('b', '2025-01-07', -1, 280),
                *_mv('b', 'a', '2025-02-10', -1, change=change, broker=broker, n=1),
                *_mv('b', 'c', '2025-02-10', -1, change=change, broker=broker, n=2),
                _tx('b', '2025-02-10', -1, 400), _tx('c', '2025-03-01', 1, 100),
                _tx('a', '2025-03-02', 2, 100)]

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_a_known_convention_reads_both_moves_as_bs(self):
        for change in (False, True):
            with self.subTest(change=change):
                res, err = _run_err(self._book(change, None))
                self.assertEqual(_gains(res), [('2025-03-01', 'c', 1.0, 300.0, 0.0),
                                               ('2025-03-02', 'a', 2.0, 480.0, 0.0)])
                self.assertEqual(_inv(res), [])
                self.assertNotIn('warning', err)

    @rule(R.CA_OPT_12, R.CA_OPT_10)
    def test_an_unknown_convention_names_the_move_it_settled(self):
        # b's move to c is sure (c holds nothing); the move between b and
        # a reads as b's written contract or a's: once b's contract went
        # to c, only a's reading fits, and the Warning says so.
        res, err = _run_err(self._book(False, 'other'))
        self.assertEqual(_gains(res), [('2025-03-01', 'c', 1.0, 180.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 2.0, 100.0), (C, 'b', -2.0, -700.0)])
        self.assertEqual(_move_warnings(err),
                         [f"warning: ATTENTION: own-account move: {C} 2025-02-10 a->b: "
                          "booked as 1 written leaving a"])
        self.assertIn("leaving a\n    It also reads as b's written contracts leaving; if the "
                      "move was otherwise, both accounts' figures for the series are off.", err)

    def test_the_fuzz_model_expects_the_warning_there(self):
        # The model reads the move between b and a as ambiguous on the
        # positions standing at the day's start (a and b each short 1) and
        # counts its Warning, whatever the engine settles on.
        m = _move_fuzz.Model()
        m.row('a', C, -1)
        m.row('b', C, -1)
        n, p = _move_fuzz.legs_np('unknown', 'written', 'b', 'a')
        self.assertEqual(_move_fuzz.mirror('unknown', n, p, 1, m.L, m.S, C),
                         ('written', 'b', 'a', 'ambiguous'))
        n, p = _move_fuzz.legs_np('unknown', 'written', 'b', 'c')
        self.assertEqual(_move_fuzz.mirror('unknown', n, p, 1, m.L, m.S, C), ('written', 'b', 'c', ''))


class TestAnAssignmentPastTheAccountsWrittenContracts(unittest.TestCase):
    """TJ-3273, TJ-3303, TJ-3302: an assignment closes only the account's
    own written contracts (CA-OPT-06): the contracts past them close
    nothing and open nothing (when it owns none too), their shares go at
    the strike with no premium, and a Warning names the assignment (the
    files hold more assignments than writes); an expiry row (a no-money
    purchase) past the written contracts and an exercise past the bought
    ones alike."""

    def _warnings(self, err):
        return [ln for ln in err.splitlines() if 'more assignments than writes' in ln]

    def _book(self):
        return [_tx('b', '2024-06-01', 300, 4500, sym=S), _tx('b', '2025-01-06', -1, 250),
                _tx('c', '2025-01-07', -2, 600),
                _tx('b', '2025-02-03', 3, 0, action='ASSIGN'),
                _settled('b', '2025-02-03', -300, 6000, '2025-02-03', sym=S),
                _tx('c', '2025-03-01', 2, 200)]

    @rule(R.CA_OPT_06, R.CA_OPT_12)
    def test_the_other_accounts_contracts_stay_open_for_its_own_close(self):
        res, err = _run_err(self._book())
        # b's 300 shares: 6000 at the strike plus its own 250, not c's 600;
        # c's buy-back closes c's two (+400).
        self.assertEqual(_gains(res), [('2025-02-03', 'b', 300.0, 1750.0, 0.0),
                                       ('2025-03-01', 'c', 2.0, 400.0, 0.0)])
        self.assertEqual(_inv(res), [])
        self.assertNotIn('conservation', err)
        lines = [ln for ln in err.splitlines() if 'more assignments than writes' in ln]
        self.assertEqual(lines, [f"warning: ATTENTION: more assignments than writes: {C} "
                                 "2025-02-03 b: 3 assigned, 1 written"])
        self.assertIn("1 written\n    The 2 contract(s) past the account's written ones close "
                      "nothing: their shares go at the strike with no premium. A write is "
                      "missing from the files (tjs find-missing-history), or the assignment "
                      "is another account's.", err)
        from taxjson.lib.out import relabel
        shown = relabel(lines[0], source=False)
        self.assertTrue(shown.startswith('Warning: More assignments than writes: '), shown)
        self.assertLessEqual(len(shown), 120)

    @rule(R.CA_OPT_06, R.CA_OPT_12)
    def test_an_expiry_past_the_accounts_written_contracts(self):
        # OA5: b's expiry row of 3 (a purchase with no money, as the row
        # check reads an expiry) closes b's one (+250); the rest closes
        # nothing and opens nothing (a purchase at 0 of two contracts no
        # one bought was a long that replaced a loss and diluted the pool);
        # c's buy-back closes c's own two.
        res, err = _run_err([_tx('b', '2025-01-06', -1, 250), _tx('c', '2025-01-07', -2, 600),
                             _tx('b', '2025-12-19', 3, 0), _tx('c', '2025-12-19', 2, 200)])
        self.assertEqual(_gains(res), [('2025-12-19', 'b', 1.0, 250.0, 0.0),
                                       ('2025-12-19', 'c', 2.0, 400.0, 0.0)])
        self.assertEqual(_inv(res), [])
        self.assertNotIn('conservation', err)
        self.assertEqual(self._warnings(err), [
            f"warning: ATTENTION: more assignments than writes: {C} 2025-12-19 b: 3 expired, 1 written"])
        self.assertIn("1 written\n    The 2 contract(s) past the account's written ones close "
                      "nothing. A write is missing from the files (tjs find-missing-history), "
                      "or the expiry is another account's.", err)

    @rule(R.CA_OPT_06, R.CA_OPT_12, R.CA_SL_07)
    def test_an_assignment_in_an_account_that_wrote_nothing(self):
        # TJ-3303 (PH1): c wrote 2; b, which never wrote, is assigned 2 and
        # sells 200 shares at the strike; a's loss on its bought contracts
        # is allowed (the assigned contracts were a long bought at 0 that
        # replaced it), and c's buy-back closes c's own two.
        res, err = _run_err([_tx('a', '2024-12-01', 2, 1000), _tx('b', '2024-06-01', 200, 3000, sym=S),
                             _tx('c', '2025-01-07', -2, 600), _tx('b', '2025-02-03', 2, 0, action='ASSIGN'),
                             _settled('b', '2025-02-03', -200, 4000, '2025-02-03', sym=S),
                             _tx('a', '2025-02-10', -1, 200), _tx('c', '2025-03-01', 2, 200)])
        self.assertEqual(_gains(res), [('2025-02-03', 'b', 200.0, 1000.0, 0.0),
                                       ('2025-02-10', 'a', 1.0, -300.0, 0.0),
                                       ('2025-03-01', 'c', 2.0, 400.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0)])
        self.assertNotIn('conservation', err)
        self.assertEqual(self._warnings(err), [
            f"warning: ATTENTION: more assignments than writes: {C} 2025-02-03 b: 2 assigned, 0 written"])

    @rule(R.CA_OPT_06, R.CA_OPT_12)
    def test_two_assignment_rows_on_one_day_past_one_write(self):
        # OA2: b wrote 1; assigned 1 then 2 the same day: the first closes
        # b's contract (its 250 in the 100 shares' proceeds), the second
        # closes nothing; no long is left.
        second = [_tx('b', '2025-02-03', 2, 0, action='ASSIGN'),
                  _settled('b', '2025-02-03', -200, 4000, '2025-02-03', sym=S)]
        second[0].time, second[1].time = '11:00:00', '11:00:01'
        res, err = _run_err([_tx('b', '2024-06-01', 300, 4500, sym=S), _tx('b', '2025-01-06', -1, 250),
                             _tx('c', '2025-01-07', -2, 600),
                             _tx('b', '2025-02-03', 1, 0, action='ASSIGN'),
                             _settled('b', '2025-02-03', -100, 2000, '2025-02-03', sym=S),
                             *second, _tx('c', '2025-03-01', 2, 200)])
        self.assertEqual(_gains(res), [('2025-02-03', 'b', 100.0, 750.0, 0.0),
                                       ('2025-02-03', 'b', 200.0, 1000.0, 0.0),
                                       ('2025-03-01', 'c', 2.0, 400.0, 0.0)])
        self.assertEqual(_inv(res), [])
        self.assertEqual(self._warnings(err), [
            f"warning: ATTENTION: more assignments than writes: {C} 2025-02-03 b: 2 assigned, 0 written"])

    @rule(R.CA_OPT_06, R.CA_OPT_12, R.CA_ACB_01)
    def test_an_exercise_past_the_accounts_bought_contracts(self):
        # TJ-3302 (EX1): b bought 1, c bought 2; b exercises 3, buying 300
        # shares at the strike: only b's contract's premium joins the
        # shares' cost, c's two stay in the pool for c's own sale.
        book = [_tx('b', '2025-01-06', 1, 100), _tx('c', '2025-01-07', 2, 200),
                _tx('b', '2025-02-03', -3, 0, action='ASSIGN'),
                _settled('b', '2025-02-03', 300, 6000, '2025-02-03', sym=S),
                _tx('c', '2025-03-01', -2, 300), _tx('b', '2025-04-01', -300, 7500, sym=S)]
        res, err = _run_err(book)
        self.assertEqual(_gains(res), [('2025-03-01', 'c', 2.0, 100.0, 0.0),
                                       ('2025-04-01', 'b', 300.0, 1400.0, 0.0)])
        self.assertEqual(_inv(res), [])
        self.assertNotIn('conservation', err)
        self.assertEqual(self._warnings(err), [
            f"warning: ATTENTION: more assignments than writes: {C} 2025-02-03 b: 3 exercised, 1 bought"])
        self.assertIn("1 bought\n    The 2 contract(s) past the account's bought ones close "
                      "nothing: their shares go at the strike with no premium. A purchase is "
                      "missing from the files (tjs find-missing-history), or the exercise is "
                      "another account's.", err)
        # The control: b exercises its one contract.
        res, err = _run_err([_tx('b', '2025-01-06', 1, 100), _tx('c', '2025-01-07', 2, 200),
                             _tx('b', '2025-02-03', -1, 0, action='ASSIGN'),
                             _settled('b', '2025-02-03', 100, 2000, '2025-02-03', sym=S),
                             _tx('c', '2025-03-01', -2, 300), _tx('b', '2025-04-01', -100, 2500, sym=S)])
        self.assertEqual(_gains(res), [('2025-03-01', 'c', 2.0, 100.0, 0.0),
                                       ('2025-04-01', 'b', 100.0, 400.0, 0.0)])
        self.assertEqual(self._warnings(err), [])


class TestABuyBackInAnotherAccountNeverReplaces(unittest.TestCase):
    """TJ-3227 (the owner's ruling of 2026-11-12): a buy-back of a written
    option acquires no property, so it never replaces a loss on bought
    contracts, in any account (CA-SL-07); the cover of a share short sale
    replaces only by the shares it buys past the short, as before."""

    LOSS = [_tx('a', '2024-12-01', 2, 1000), _tx('a', '2025-02-03', -1, 200)]

    @rule(R.CA_SL_07, R.CA_OPT_12)
    def test_another_accounts_buy_back_leaves_the_loss_allowed(self):
        res = _run(self.LOSS + [_tx('b', '2025-02-05', -1, 250), _tx('b', '2025-02-20', 1, 100)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, -300.0, 0.0),
                                       ('2025-02-20', 'b', 1.0, 150.0, 0.0)])
        self.assertEqual(_inv(res), [(C, '', 1.0, 500.0)])

    @rule(R.CA_SL_07, R.CA_OPT_12)
    def test_across_a_rename_the_buy_back_is_no_candidate(self):
        # TJ-3304 (RN4): outside a rename the class balances already left a
        # buy-back out; b wrote C before the rename C to C2 and buys back C2
        # after it: a's loss on C2 needs `_SLIndexes.build`'s filter.
        C2 = 'SYR251219C00020000.TO'
        res = _run([_tx('a', '2024-12-01', 2, 1000), _tx('b', '2025-01-05', -1, 250),
                    _split(C, C2, '2025-01-15'), _tx('a', '2025-02-03', -1, 200, sym=C2),
                    _tx('b', '2025-02-20', 1, 100, sym=C2)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, -300.0, 0.0),
                                       ('2025-02-20', 'b', 1.0, 150.0, 0.0)])
        self.assertEqual(_inv(res), [(C2, '', 1.0, 500.0)])

    @rule(R.CA_SL_07, R.CA_SL_09)
    def test_another_accounts_purchase_still_denies(self):
        res = _run(self.LOSS + [_tx('b', '2025-02-20', 1, 220)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 1.0, 0.0, 300.0)])
        self.assertEqual(_inv(res), [(C, '', 2.0, 1020.0)])

    @rule(R.CA_SL_07)
    def test_a_share_shorts_cover_replaces_by_the_shares_past_the_short(self):
        loss = [_tx('a', '2024-12-01', 100, 1500, sym=S), _tx('a', '2025-02-03', -100, 1200, sym=S)]
        res = _run(loss + [_tx('b', '2025-02-05', -100, 1300, sym=S, description='SELL SHORT.'),
                           _tx('b', '2025-02-20', 100, 1100, sym=S, description='COVER SHORT.')])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 100.0, -300.0, 0.0),
                                       ('2025-02-20', 'b', 100.0, 200.0, 0.0)])
        # A purchase with no cover mark past the short (a source with no
        # convention): it covers the short and buys 50, which replace.
        res = _run(loss + [_tx('b', '2025-02-05', -100, 1300, sym=S, description='SELL SHORT.'),
                           _tx('b', '2025-02-20', 150, 1650, sym=S)])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 100.0, -150.0, 150.0),
                                       ('2025-02-20', 'b', 100.0, 200.0, 0.0)])
        self.assertEqual(_inv(res), [(S, '', 50.0, 700.0)])
        # The same purchase marked COVER SHORT.: a row the broker marks
        # CLOSE opens no position, so its 50 past the short closed a short
        # sold before the files (reported by hand) and replace nothing
        # (CA-ACB-11; the owner's ruling on backlog TJ-5349).
        res = _run(loss + [_tx('b', '2025-02-05', -100, 1300, sym=S, description='SELL SHORT.'),
                           _tx('b', '2025-02-20', 150, 1650, sym=S, description='COVER SHORT.')])
        self.assertEqual(_gains(res), [('2025-02-03', 'a', 100.0, -300.0, 0.0),
                                       ('2025-02-20', 'b', 100.0, 200.0, 0.0),
                                       ('2025-02-20', 'b', 50.0, -550.0, 0.0)])
        self.assertTrue(res['transactions'][-1].get('tainted'))
        self.assertEqual(_inv(res), [])


if __name__ == '__main__':
    unittest.main()
