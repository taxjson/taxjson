"""TJ-1957: a call on the loss shares that an affiliated account holds as an
opening balance dated the day of a SPLIT on the underlying is a position
that existed when the day opened, in that day's opening (pre-split) units,
as `_sl_row_loss_units` already read a share row. The call-units helper read
it as a contract bought that day (post-split units) and backed half the
denial."""
import unittest

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.corporate_timeline import SplitTimeline
from tax_rules import rule
from taxjson.rules import ids as R
from _support.ca_engine_runs import _run, _split, _tt

C10 = 'SYQ251219C00010000.TO'
C5 = 'SYQ251219C00005000.TO'          # the series a 2:1 option SPLIT creates


def _call(action, date, symbol, qty, **kw):
    row = dict(action=action, date=date, time='10:00:00', symbol=symbol,
               quantity=qty, currency='CAD', account='rrsp', price=1.0,
               net_amount=100.0 * abs(qty))
    row.update(kw)
    return TaxTransaction(**row)


def _denial(loss_qty, ratio, held, trigger_qty=1):
    """The loss on `loss_qty` shares with a SPLIT of `ratio` on the day the
    `held` rows are dated; the trigger is a call the RRSP buys inside the
    window and sells again, so what it holds at day 30 is `held` alone.
    Returns (total_disallowed, disallowed_qty)."""
    taxable = _tt(f"""
        BUYSELL 2025-01-10 10:00:00 SYQ.TO {loss_qty} CAD 10 {10 * loss_qty}
        BUYSELL 2025-08-20 10:00:00 SYQ.TO -{loss_qty} CAD 6 {6 * loss_qty}
    """)
    taxable.append(_split('2025-08-25', 'SYQ.TO', 'SYQ.TO', ratio, 'margin'))
    rrsp = [_call('BUYSELL', '2025-08-21', C10, trigger_qty),
            _call('BUYSELL', '2025-08-22', C10, -trigger_qty)] + held
    res, _ = _run(taxable, sheltered_transactions=rrsp)
    (w,) = res['wash_sales']
    return res['summary']['total_disallowed'], w['disallowed_qty']


class TestOpeningBalanceCallOnSplitDay(unittest.TestCase):
    @rule(R.CA_SL_05)
    def test_an_opening_balance_call_is_in_the_days_opening_units(self):
        # One pre-split contract covers the 100 loss shares: the whole loss.
        held = [_call('OPENING_BALANCE', '2025-08-25', C10, 1)]
        self.assertEqual(_denial(100, 2.0, held), (400.0, 100.0))

    @rule(R.CA_SL_05)
    def test_a_call_bought_on_the_split_day_is_in_its_closing_units(self):
        # The boundary the share helper draws: a contract bought that day is
        # 100 post-split shares, 50 of the loss date's.
        held = [_call('BUYSELL', '2025-08-25', C10, 1)]
        self.assertEqual(_denial(100, 2.0, held), (200.0, 50.0))

    @rule(R.CA_SL_05)
    def test_a_consolidation_reads_the_opening_balance_the_same_way(self):
        # 1-for-2 on a 200-share loss: the opening contract is 100 old
        # shares (half the loss); one bought that day is 100 new, 200 old.
        held = [_call('OPENING_BALANCE', '2025-08-25', C10, 1)]
        self.assertEqual(_denial(200, 0.5, held), (400.0, 100.0))
        held = [_call('BUYSELL', '2025-08-25', C10, 1)]
        self.assertEqual(_denial(200, 0.5, held), (800.0, 200.0))

    @rule(R.CA_SL_05)
    def test_a_settle_lagged_call_pre_exists_its_settlement_day(self):
        # Bought before the split, settling on it: a pre-split contract.
        held = [_call('BUYSELL', '2025-08-22', C10, 1, date_settle='2025-08-25')]
        self.assertEqual(_denial(100, 2.0, held), (400.0, 100.0))

    @rule(R.CA_SL_05)
    def test_a_balance_in_the_series_the_days_split_created_is_post_split(self):
        # The broker restates the position in the new series: 2 C5
        # contracts are 200 post-split shares, 100 of the loss date's, as
        # the 1 C10 contract they replaced (TJ-2314); the trigger is 2.
        held = [_split('2025-08-25', C10, C5, 2.0, 'rrsp'),
                _call('OPENING_BALANCE', '2025-08-25', C5, 2)]
        self.assertEqual(_denial(200, 2.0, held, trigger_qty=2), (400.0, 100.0))
        held = [_call('OPENING_BALANCE', '2025-08-25', C10, 1)]
        self.assertEqual(_denial(200, 2.0, held, trigger_qty=2), (400.0, 100.0))

    @rule(R.CA_SL_05)
    def test_the_created_series_is_read_on_the_passs_date_basis(self):
        # The option SPLIT row traded 08-22 and settling 08-25 is placed on
        # the 25th, as the pass places every row; the balance restated that
        # day in the new series is post-split there too (TJ-2340).
        option_split = TaxTransaction(action='SPLIT', date='2025-08-22',
                                      date_settle='2025-08-25', time='09:30:00',
                                      symbol=C10, symbol_new=C5, quantity=2.0,
                                      account='rrsp', currency='CAD')
        held = [option_split, _call('OPENING_BALANCE', '2025-08-25', C5, 2)]
        self.assertEqual(_denial(200, 2.0, held, trigger_qty=2), (400.0, 100.0))


class TestCreatedOn(unittest.TestCase):
    """`SplitTimeline.created_on`: which symbol a SPLIT created on which day,
    on the timeline's date basis; a symbol two sources rename into is
    created on each day (TJ-2342)."""

    def _split_row(self, date, old, new, settle=None):
        return TaxTransaction(action='SPLIT', date=date, date_settle=settle or date,
                              time='09:30:00', symbol=old, symbol_new=new,
                              quantity=1.0, account='margin', currency='CAD')

    def test_each_creation_day_and_no_other(self):
        rows = [self._split_row('2025-03-03', 'SYA.TO', 'SYX.TO'),
                self._split_row('2025-05-05', 'SYB.TO', 'SYX.TO'),
                self._split_row('2025-06-06', 'SYX.TO', ''),          # a plain split
                self._split_row('2025-07-01', 'SYC.TO', 'SYY.TO', settle='2025-07-03')]
        tl = SplitTimeline.from_transactions(rows, date_of=lambda t: t.date_settle or t.date)
        self.assertTrue(tl.created_on('SYX.TO', '2025-03-03'))
        self.assertTrue(tl.created_on('SYX.TO', '2025-05-05'))
        self.assertFalse(tl.created_on('SYX.TO', '2025-06-06'))
        self.assertFalse(tl.created_on('SYA.TO', '2025-03-03'))
        self.assertTrue(tl.created_on('SYY.TO', '2025-07-03'))     # the settle basis
        self.assertFalse(tl.created_on('SYY.TO', '2025-07-01'))


if __name__ == '__main__':
    unittest.main()
