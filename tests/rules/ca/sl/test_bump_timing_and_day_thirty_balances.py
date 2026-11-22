"""Low-round engine pins (fixl/engine-tests): end-to-end assertions on
engine output fields and boundaries that a mutant could change with
every other test still passing. Each class names the audit finding.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_engine_pins
"""
import contextlib
import copy
import io
import unittest
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule
from taxjson.rules import ids as R


def T(action, date, sym, qty, net, *, time='10:00:00', settle=None,
      cur='CAD', acct='a', price=None, **kw):
    """One synthetic row; `net` is the fee-inclusive money magnitude."""
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action=action, date=date, time=time,
                          date_settle=settle or date, symbol=sym,
                          quantity=float(qty), price=float(price),
                          net_amount=float(net), currency=cur,
                          account=acct, **kw)


def run(book, year, country='canada', sheltered=(), **kw):
    """pipeline.run_gains (what `tjs run` uses) on a taxable book."""
    req = GainsRequest(country=country, year=year, taxable=True, **kw)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)),
                        copy.deepcopy(list(sheltered)), [], req=req)
    return res


def sales(res):
    return [t for t in res['transactions'] if 'gain' in t]


class TestPreLossTriggerBumpTime(unittest.TestCase):
    """R1-305: the pre-loss-trigger ADJUST lands at loss + 1 s (23:59:59
    when the loss is at 23:59:59), so a later same-day sale of the pool
    averages the bump in and the loss itself never absorbs it.

    Formerly: test_fix_l_engine_pins.TestPreLossTriggerBumpTime"""

    def _book(self, loss_time, second_date):
        return [
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000),
            T('BUYSELL', '2025-02-01', 'XYZ.TO', 100, 1200),  # pre-loss rebuy
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000, time=loss_time),
            T('BUYSELL', second_date, 'XYZ.TO', -50, 1500, time='14:00:00'),
        ]

    def _check(self, res, adjust_time):
        # The bump is placed right after its loss row (_wash_after) and
        # its ADJUST line names loss + 1 s, wrapping to 23:59:59.
        cmds = [w['adjust_cmd'] for w in res['wash_sales']]
        self.assertEqual(cmds, [f'ADJUST 2025-02-03 {adjust_time} XYZ.TO '
                                f'CAD 300.0000'])
        loss, later = sorted(sales(res), key=lambda s: (s['date'],
                                                        s['proceeds']))
        self.assertEqual((loss['gain'], loss['disallowed_amount']),
                         (-300.0, 300.0))
        self.assertEqual((later['cost'], later['gain']), (950.0, 550.0))
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertEqual((inv['XYZ.TO']['qty'], inv['XYZ.TO']['total_cost']),
                         (50.0, 950.0))

    @rule(R.CA_SL_09)
    def test_later_same_day_sale_absorbs_the_bump(self):
        self._check(run(self._book('10:00:00', '2025-02-03'), 2025),
                    '10:00:01')

    @rule(R.CA_SL_09)
    def test_loss_at_235959_keeps_its_own_denial(self):
        self._check(run(self._book('23:59:59', '2025-02-04'), 2025),
                    '23:59:59')


@rule(R.CA_SL_02)
class TestWashWindowBalanceAtDayThirty(unittest.TestCase):
    """S069-18: wash_window.bal_at_end counts rows dated ON day +30.

    Formerly: test_fix_l_engine_pins.TestWashWindowBalanceAtDayThirty"""

    def _bal(self, book):
        res = run(book, 2025)
        return {s['date']: (s.get('wash_window') or {}).get('bal_at_end')
                for s in sales(res)}

    def test_sale_on_day_30_reduces_the_end_balance(self):
        bal = self._bal([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000),
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000),
            T('BUYSELL', '2025-02-10', 'XYZ.TO', 100, 1100),
            T('BUYSELL', '2025-03-05', 'XYZ.TO', -50, 600)])
        self.assertEqual(bal['2025-02-03'], 50.0)

    def test_buy_on_day_30_adds_to_the_end_balance(self):
        bal = self._bal([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000),
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000),
            T('BUYSELL', '2025-02-10', 'XYZ.TO', 60, 660),
            T('BUYSELL', '2025-03-05', 'XYZ.TO', 40, 440)])
        self.assertEqual(bal['2025-02-03'], 100.0)


class TestRenameOnWindowEndPoolBacking(unittest.TestCase):
    """S069-19: the end-of-window pool backing is keyed after every row of
    the window's last day, so a rename on that day routes the bump to
    the renamed pool, not to the old symbol's partial sale.

    Formerly: test_fix_l_engine_pins.TestRenameOnWindowEndPoolBacking"""

    @rule(R.CA_SL_09)
    def test_rename_on_window_end_with_partial_sale(self):
        book = [
            T('BUYSELL', '2025-01-06', 'OLD.TO', 100, 2000, time='09:30:00',
              id='a0'),
            T('BUYSELL', '2025-02-03', 'OLD.TO', -100, 1000, id='a1'),
            T('BUYSELL', '2025-02-10', 'OLD.TO', 100, 1100, id='a2'),
            T('BUYSELL', '2025-02-20', 'OLD.TO', -60, 720, id='a3'),
            T('SPLIT', '2025-03-05', 'OLD.TO', 1.0, 0, time='09:30:00',
              price=0, symbol_new='NEW.TO', id='sp'),
            T('BUYSELL', '2025-03-05', 'NEW.TO', 50, 600, time='11:00:00',
              acct='b', id='a4'),
        ]
        res = run(book, 2025)
        self.assertEqual(res['summary']['total_gain'], -100.0)
        self.assertEqual(res['summary']['total_disallowed'], 1380.0)
        by = {s['date']: s for s in sales(res)}
        self.assertEqual((by['2025-02-20']['cost'], by['2025-02-20']['gain']),
                         (1200.0, 0.0))
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertEqual((inv['NEW.TO']['qty'], inv['NEW.TO']['total_cost']),
                         (90.0, 1880.0))


class TestCanadaBalanceWalkClockOrder(unittest.TestCase):
    """S074-11: the per-account balance walk orders same-day rows by clock
    time like the ACB pass, even when the export lists them otherwise.

    Formerly: test_fix_l_engine_pins.TestCanadaBalanceWalkClockOrder"""

    @rule(R.CA_SL_08)
    def test_buy_then_sell_same_day_other_account(self):
        res = run([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000, time='09:30:00',
              id='t1'),
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000, id='t2'),
            # Listed sell-first; the clock says buy 10:00, sell 14:00.
            T('BUYSELL', '2025-02-10', 'XYZ.TO', -100, 1150, time='14:00:00',
              acct='b', id='a_sell'),
            T('BUYSELL', '2025-02-10', 'XYZ.TO', 100, 1100, time='10:00:00',
              acct='b', id='z_buy'),
            T('BUYSELL', '2025-02-11', 'XYZ.TO', 50, 550, time='09:30:00',
              acct='b', id='t5')], 2025)
        # 50 of the 100 units denied (500): the 02-10 shares are sold the
        # same day, so the addition goes to the 50 bought 02-11, the
        # shares held at day 30 (TJ-2711); the 02-10 round trip is its
        # own +50 (it carried the 500 and showed a loss of 450, half of
        # it denied again, before).
        self.assertEqual(res['summary']['total_gain'], -450.0)
        self.assertEqual(res['summary']['total_disallowed'], 500.0)
        by = {s['date']: s for s in sales(res)}
        self.assertEqual(by['2025-02-10']['cost'], 1100.0)
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertEqual((inv['XYZ.TO']['qty'], inv['XYZ.TO']['total_cost']),
                         (50.0, 1050.0))
        # The wash window's running balance walks the same clock order
        # (the ACB pass no longer depends on this walk; its display does).
        # The 02-10 purchase, sold out the same day, is a candidate and
        # no replacement; the 02-11 purchase is the trigger (TJ-2761).
        win = {(t['date'], t['role']): t['running_bal'] for t in
               by['2025-02-03']['wash_window']['transactions']}
        self.assertEqual(win[('2025-02-10', 'candidate')], 100.0)
        self.assertEqual(win[('2025-02-10', 'other_sell')], 0.0)
        self.assertEqual(win[('2025-02-11', 'trigger')], 50.0)


if __name__ == '__main__':
    unittest.main()
