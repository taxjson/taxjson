"""Backlog fixes 2026-10-29, fix round 7 wave D, group `ib`: Interactive
Brokers, mostly regressions of this round's earlier fixes.

TJ-1041 (a same-day withdrawal walked before the timed Forex that funded
it), TJ-1042 / TJ-1043 (identical refunds of two accounts in combined
statements), TJ-1044 (a coded short open at the statement end read as a
holding before it opened), TJ-1120 (a JPY future on
CME dated in Tokyo time), TJ-1130 (the overlap step dropped a cancelled
trade's copy whichever account's row it was).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_r7d_ib
"""
import unittest

from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as V2
from _support.parser_cash import parsed_cash_events
from _support.ib_statement_rows import HEAD, TRADES_H, _trade
from _support.ib_brokerage_base import _A, _Y2025, _Tmp

_DW_H = ('Deposits & Withdrawals,Header,Currency,Settle Date,Description,'
         'Amount\n')
_CASH_H = 'Cash Report,Header,Currency Summary,Currency,Total\n'
# The Cash Report's trades of 1,000 US dollars bought for 1,300 Canadian.
_BOUGHT = (('CAD', -1300), ('USD', 1000))


class TestUntimedAgainstTheClock(_Tmp):
    """TJ-1041: an event with no clock is not ordered against a broker's
    clock as if it happened at 00:00: what it takes out comes after the
    day's timed events, what it brings in before them. TJ-0859's clock
    order of the conversions stays.

    Formerly: test_fix_1029_r7d_ib.TestUntimedAgainstTheClock"""

    def _ledger(self, text, decls=(), opening='USD 0 0', rate=1.35,
                native=()):
        events = parsed_cash_events('ib', self._write(text))
        for e in events:
            e.update(label='margin', book='margin/ib', broker='ib',
                     file='ib.csv')
        lines = []
        for i, d in enumerate((f'CASHOPEN 2025-01-01 {opening}', *decls), 1):
            ln = CL.parse_line(d, f'cash.tt:{i}')
            ln.update(label='margin', book='margin/ib', file='cash.tt',
                      book_problem='')
            lines.append(ln)
        cash = {'events': events, 'lines': lines, 'problems': [],
                'stablecoins_as_cash': True}
        return V2.build(list(native), cash, 'CAD', 2025, lambda c, d: rate,
                        country='canada')

    @staticmethod
    def _fx(when, qty, price, proceeds):
        return _trade('USD.CAD', when, qty, price, proceeds, 0, cat='Forex',
                      cur='CAD', acct=_A)

    @staticmethod
    def _cash(start, end, trades=()):
        # The conversions' cash too, per currency: the events are the
        # parse's (PLAN step 16 commit 10), which reconciles its rows with
        # IB's own Cash Report.
        return (_CASH_H + f'Cash Report,Data,Starting Cash,USD,{start}\n'
                f'Cash Report,Data,Ending Cash,USD,{end}\n'
                + ''.join(f'Cash Report,Data,Trades (Sales),{c},{v}\n'
                          for c, v in trades))

    def _withdrawal(self):
        return (HEAD + _Y2025 + TRADES_H
                + self._fx('2025-06-02, 10:00:00', 1000, 1.3, -1300)
                + _DW_H + 'Deposits & Withdrawals,Data,USD,2025-06-02,'
                'Disbursement Initiated by Someone,-1000\n'
                + self._cash(0, 0, _BOUGHT))

    def test_withdrawal_after_the_conversion_that_funds_it(self):
        v2 = self._ledger(self._withdrawal(),
                          ['CASHMOVE 2025-06-02 USD -1000.00 kept'])
        self.assertEqual(v2['status'], 'computed')
        self.assertAlmostEqual(v2['net_gain'], 0.0, places=2)
        self.assertFalse(v2.get('borrowed'))

    def test_spot_withdrawal_is_the_disposal_only(self):
        v2 = self._ledger(self._withdrawal(),
                          ['CASHMOVE 2025-06-02 USD -1000.00 spot'])
        self.assertAlmostEqual(v2['net_gain'], 50.0, places=2)
        self.assertFalse(v2.get('borrowed'))

    def test_deposit_still_before_the_conversion_it_funds(self):
        text = (HEAD + _Y2025 + TRADES_H
                + self._fx('2025-06-02, 10:00:00', -1000, 1.4, 1400)
                + _DW_H + 'Deposits & Withdrawals,Data,USD,2025-06-02,'
                'Electronic Fund Transfer,1000\n'
                + self._cash(0, 0, (('CAD', 1400), ('USD', -1000))))
        v2 = self._ledger(text, ['CASHMOVE 2025-06-02 USD 1000.00 spot'])
        self.assertFalse(v2.get('borrowed'))
        self.assertAlmostEqual(v2['net_gain'], 50.0, places=2)

    def test_placeholder_timed_fee_after_the_conversion(self):
        # IB's date-only rows carry the placeholder 09:30:00: a fee is
        # not an event of 09:30, before a 10:00 conversion.
        text = (HEAD + _Y2025 + TRADES_H
                + self._fx('2025-06-02, 10:00:00', 1000, 1.3, -1300)
                + self._cash(0, 995, _BOUGHT))
        fee = {'action': 'FEE', 'date': '2025-06-02', 'time': '09:30:00',
               'date_settle': '2025-06-02', 'symbol': '', 'quantity': 0.0,
               'price': 0.0, 'currency': 'USD', 'net_amount': 5.0,
               'gross_amount': 5.0, 'fee': 0.0, 'account': 'margin',
               'source': 'ib.csv', '_book': 'margin/ib'}
        v2 = self._ledger(text, native=[fee])
        self.assertFalse(v2.get('borrowed'), v2.get('borrowed'))
        self.assertEqual(v2['status'], 'computed')

    def test_a_day_with_no_clock_keeps_its_row_order(self):
        # Nothing timed that day: the rows' order, as ever.
        text = (HEAD + _Y2025 + _DW_H
                + 'Deposits & Withdrawals,Data,USD,2025-06-02,'
                  'Disbursement Initiated by Someone,-1000\n'
                + 'Deposits & Withdrawals,Data,USD,2025-06-02,'
                  'Electronic Fund Transfer,1000\n'
                + self._cash(0, 0))
        v2 = self._ledger(text, ['CASHMOVE 2025-06-02 USD -1000.00 spot',
                                 'CASHMOVE 2025-06-02 USD 1000.00 spot'])
        self.assertTrue(v2.get('borrowed'))


if __name__ == '__main__':
    unittest.main()
