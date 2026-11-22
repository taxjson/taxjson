"""Regression tests for fix round 7 (wave E), group shorts: a marked short
beside an own move's shares, a written option's premium in the holdings views
under close timing, the run's no-total note and holdings report after a split
one account reports, and edge-cases' HELD DAY 30 for a short holder.

Synthetic data only; account labels and tickers are made up.

Formerly: test_fix_1029_r7e_shorts
"""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.row_types import LOT_MOVE_TYPE
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res


def _row(account, date, qty, net, open_close='', sym='SYQ.TO', settle=None):
    return TaxTransaction(action='BUYSELL', date=date,
                          date_settle=settle or date,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency='CAD', account=account,
                          open_close=open_close)


def _move(date, units, src, dst, sym='SYQ.TO', time=''):
    desc = f'own-account move #1: {src} -> {dst} (test)'
    return [TaxTransaction(action='TRANSFER', type=LOT_MOVE_TYPE, date=date,
                           date_settle=date, time=time, symbol=sym,
                           quantity=sign * units, currency='CAD',
                           account=acct, description=desc)
            for sign, acct in ((-1, src), (1, dst))]


def _held(res):
    return {(i.get('account', ''), i['symbol']):
            (round(i['qty'], 6), round(i['total_cost'], 2))
            for i in res['inventory'] if abs(i['qty']) > 1e-9}


def _gains(res):
    return [(r['date'], round(r['gain'], 2)) for r in res['transactions']]


class TestOwnMoveCountsAsTheAccountsLong(unittest.TestCase):
    """TJ-1180: a sale coded C;O closes the account's own long, moved-in
    shares included, and opens a short only past it; moved-out shares are
    no longer the sender's long.

    Formerly: test_fix_1029_r7e_shorts.TestOwnMoveCountsAsTheAccountsLong"""

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_moved_in_units_are_closed_first(self):
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -15, 225, 'C;O'),
                      _row('b', '2025-06-02', 5, -60, 'C')])
        self.assertEqual(_gains(res), [('2025-03-03', 50.0),
                                       ('2025-06-02', 15.0)])
        self.assertEqual(_held(res), {})

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_same_as_the_control_with_no_move(self):
        control = _run([_row('b', '2025-01-02', 10, -100),
                        _row('b', '2025-03-03', -15, 225, 'C;O'),
                        _row('b', '2025-06-02', 5, -60, 'C')])
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -15, 225, 'C;O'),
                      _row('b', '2025-06-02', 5, -60, 'C')])
        self.assertEqual(res['summary']['total_gain'],
                         control['summary']['total_gain'])

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_part_held_part_moved_in(self):
        res = _run([_row('a', '2025-01-02', 10, -100),
                    _row('b', '2025-01-02', 5, -50)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -20, 300, 'C;O')])
        self.assertEqual(_gains(res), [('2025-03-03', 75.0)])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (-5.0, -75.0)})

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_moved_out_units_are_no_long_of_the_sender(self):
        res = _run([_row('a', '2025-01-02', 15, -150)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('a', '2025-03-03', -10, 150, 'C;O')])
        self.assertEqual(_gains(res), [('2025-03-03', 25.0)])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 100.0),
                                      ('a', 'SYQ.TO'): (-5.0, -75.0)})

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_a_sale_the_day_the_units_arrive(self):
        """Whatever clock the leg has, and a sale settling that day."""
        for time in ('', '23:00:00'):
            res = _run([_row('a', '2025-01-02', 10, -100)]
                       + _move('2025-02-03', 10, 'a', 'b', time=time)
                       + [_row('b', '2025-01-30', -10, 150, 'C;O',
                               settle='2025-02-03')])
            self.assertEqual(_gains(res), [('2025-01-30', 50.0)], time)
            self.assertEqual(_held(res), {}, time)

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_a_split_after_the_move_scales_the_moved_units(self):
        split = TaxTransaction(action='SPLIT', date='2025-02-10',
                               date_settle='2025-02-10', time='00:00:01',
                               symbol='SYQ.TO', quantity=2.0,
                               currency='CAD', account='a')
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b') + [split]
                   + [_row('b', '2025-03-03', -20, 300, 'C;O')])
        self.assertEqual(_gains(res), [('2025-03-03', 200.0)])
        self.assertEqual(_held(res), {})

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_a_rename_after_the_move_keeps_the_moved_units(self):
        ren = TaxTransaction(action='SPLIT', date='2025-02-10',
                             date_settle='2025-02-10', time='00:00:01',
                             symbol='SYQ.TO', symbol_new='SYR.TO',
                             quantity=1.0, currency='CAD', account='a')
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b') + [ren]
                   + [_row('b', '2025-03-03', -10, 150, 'C;O', sym='SYR.TO')])
        self.assertEqual(_gains(res), [('2025-03-03', 50.0)])
        self.assertEqual(_held(res), {})

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_a_move_out_of_units_the_books_never_had(self):
        """The sender's later buy is its own long: never a short because
        of an earlier move the router could not back."""
        res = _run([_row('c', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('a', '2025-03-03', 10, -120),
                      _row('a', '2025-04-03', -10, 150, 'C;O')])
        self.assertEqual(len(_gains(res)), 1)
        self.assertEqual(sum(q for q, _ in _held(res).values()), 10.0)
        self.assertNotIn(('a', 'SYQ.TO'), _held(res))

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_a_coded_open_past_a_move_stays_short(self):
        """Coded O alone: a short, whatever long the account received."""
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -5, 75, 'O')])
        self.assertEqual(_gains(res), [])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 100.0),
                                      ('b', 'SYQ.TO'): (-5.0, -75.0)})

    @rule(R.CA_DISP_04, R.CA_ACB_OWN_MOVE)
    def test_an_unmarked_buy_goes_long_beside_moved_in_units(self):
        """b short 5 then 10 moved in: its long is held, so an unmarked buy
        is a long, not a cover (the router's rule for any long)."""
        res = _run([_row('a', '2025-01-02', 10, -100),
                    _row('b', '2025-01-10', -5, 75, 'O')]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', 5, -60)])
        self.assertEqual(_gains(res), [])
        self.assertEqual(_held(res)[('b', 'SYQ.TO')], (-5.0, -75.0))

    @rule(R.CA_ACB_OWN_MOVE)
    def test_a_book_with_no_marked_short_is_untouched(self):
        plain = _run([_row('a', '2025-01-02', 10, -100),
                      _row('b', '2025-03-03', -10, 150)])
        moved = _run([_row('a', '2025-01-02', 10, -100)]
                     + _move('2025-02-03', 10, 'a', 'b')
                     + [_row('b', '2025-03-03', -10, 150)])
        self.assertEqual(_gains(plain), _gains(moved))
        self.assertEqual(_held(plain), _held(moved))


if __name__ == '__main__':
    unittest.main()
