"""Backlog fixes 2026-10-29, fix round 7 wave D, group `ib`: Interactive
Brokers, mostly regressions of this round's earlier fixes.

TJ-1041 (a same-day withdrawal walked before the timed Forex that funded
it), TJ-1042 / TJ-1043 (identical refunds of two accounts in combined
statements), TJ-1044 (a coded short open at the statement end read as a
holding before it opened), TJ-1120 (a JPY future on
CME dated in Tokyo time), TJ-1130 (the overlap step dropped a cancelled
trade's copy whichever account's row it was).
Synthetic data only; account ids are fake (pii-ok).
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.brokerages.ib_extractor import (IbBrokerage,
                                                 _ib_market_trade_date,
                                                 ib_cash_events)
from taxjson.lib.dispatch import python_module_argv
from tax_rules import rule
from test_fix_ibparse import HEAD, TRADES_H, _trade

_A, _B = 'U1234567', 'U9990002'
_ADJ_H = ('Commission Adjustments,Header,Currency,Account,Date,Description,'
          'Amount\n')
_DIV_H = 'Dividends,Header,Currency,Date,Description,Amount\n'
_OP_H = ('Open Positions,Header,DataDiscriminator,Asset Category,Currency,'
         'Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,'
         'Unrealized P/L,Code\n')
_DW_H = ('Deposits & Withdrawals,Header,Currency,Settle Date,Description,'
         'Amount\n')
_CASH_H = 'Cash Report,Header,Currency Summary,Currency,Total\n'


def _period(text):
    return f'Statement,Data,Period,"{text}"\n'


_Y2025 = _period('January 1, 2025 - December 31, 2025')


class _Tmp(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)

    def _write(self, text, name='ib.csv'):
        p = self.dir / name
        p.write_text(text)
        return p


class TestUntimedAgainstTheClock(_Tmp):
    """TJ-1041: an event with no clock is not ordered against a broker's
    clock as if it happened at 00:00: what it takes out comes after the
    day's timed events, what it brings in before them. TJ-0859's clock
    order of the conversions stays."""

    def _ledger(self, text, decls=(), opening='USD 0 0', rate=1.35,
                native=()):
        events = ib_cash_events(self._write(text))
        for e in events:
            e.update(label='margin', book='margin/ib', broker='ib',
                     file='ib.csv')
        lines = []
        for i, d in enumerate((f'CASHOPEN 2025-01-01 {opening}', *decls), 1):
            ln = CE.parse_line(d, f'cash.tt:{i}')
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
    def _cash(start, end):
        return (_CASH_H + f'Cash Report,Data,Starting Cash,USD,{start}\n'
                f'Cash Report,Data,Ending Cash,USD,{end}\n')

    def _withdrawal(self):
        return (HEAD + _Y2025 + TRADES_H
                + self._fx('2025-06-02, 10:00:00', 1000, 1.3, -1300)
                + _DW_H + 'Deposits & Withdrawals,Data,USD,2025-06-02,'
                'Disbursement Initiated by Someone,-1000\n'
                + self._cash(0, 0))

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
                + self._cash(0, 0))
        v2 = self._ledger(text, ['CASHMOVE 2025-06-02 USD 1000.00 spot'])
        self.assertFalse(v2.get('borrowed'))
        self.assertAlmostEqual(v2['net_gain'], 50.0, places=2)

    def test_placeholder_timed_fee_after_the_conversion(self):
        # IB's date-only rows carry the placeholder 09:30:00: a fee is
        # not an event of 09:30, before a 10:00 conversion.
        text = (HEAD + _Y2025 + TRADES_H
                + self._fx('2025-06-02, 10:00:00', 1000, 1.3, -1300)
                + self._cash(0, 995))
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


class _Brokerage(_Tmp):

    def _run(self, files, *extra):
        paths = [str(self._write(t, n)) for n, t in files.items()]
        r = subprocess.run(
            python_module_argv('taxjson.bin.taxjson_brokerage',
                               ['--brokerage', 'ib', '--country', 'canada',
                                '--account', 'margin', '--account-type',
                                'taxable', '--combined-broker-accounts',
                                '--strict', *extra, *paths]),
            capture_output=True, text=True,
            env=dict(os.environ, TAXJSON_WIDTH='0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)['transactions'], r.stderr


def _adj(acct, day, amount, desc):
    return f'Commission Adjustments,Data,CAD,{acct},{day},"{desc}",{amount}\n'


def _buy(acct, when='2025-01-06, 10:00:00', qty=10, comm=-1):
    return _trade('SYN', when, qty, 10, -10 * qty, comm, cur='CAD', acct=acct)


class TestIdenticalRefundsOfTwoAccounts(_Brokerage):
    """TJ-1042, TJ-1043: two accounts' refunds alike in currency, date,
    amount and wording are two refunds; TJ-0740's rule (a refund folds
    into its own account's trade) stays."""

    def test_refunds_for_trades_in_another_statement(self):
        # TJ-1042: each account's refund folds into its own trade.
        y25 = (HEAD + _Y2025 + TRADES_H
               + _buy(_A, '2025-12-15, 10:00:00')
               + _buy(_B, '2025-12-15, 10:00:00'))
        y26 = (HEAD + _period('January 1, 2026 - December 31, 2026') + _ADJ_H
               + _adj(_A, '2026-01-10', 1, 'Refund (SYN, 10, 2025-12-15)')
               + _adj(_B, '2026-01-10', 1, 'Refund (SYN, 10, 2025-12-15)'))
        rows, err = self._run({'ib_2025.csv': y25, 'ib_2026.csv': y26})
        self.assertEqual(sorted(t['net_amount'] for t in rows
                                if t['action'] == 'BUYSELL'), [100.0, 100.0],
                         err)
        self.assertNotIn('FEE', [t['action'] for t in rows])

    def test_a_refund_of_one_account_only_is_not_the_others(self):
        y25 = (HEAD + _Y2025 + TRADES_H
               + _buy(_A, '2025-12-15, 10:00:00')
               + _buy(_B, '2025-12-15, 10:00:00'))
        y26 = (HEAD + _period('January 1, 2026 - December 31, 2026') + _ADJ_H
               + _adj(_B, '2026-01-10', 1, 'Refund (SYN, 10, 2025-12-15)'))
        rows, err = self._run({'ib_2025.csv': y25, 'ib_2026.csv': y26})
        self.assertEqual(sorted(t['net_amount'] for t in rows
                                if t['action'] == 'BUYSELL'), [100.0, 101.0],
                         err)
        self.assertNotIn('FEE', [t['action'] for t in rows])

    def _overlap(self, refunds, order=('ib_h1.csv', 'ib_full.csv')):
        sells = ''.join(
            _trade('SYN', '2025-09-02, 10:00:00', -10, 20, 200, 0, code='C',
                   cur='CAD', acct=a) for a in (_A, _B))
        files = {
            'ib_h1.csv': (HEAD + _period('January 1, 2025 - June 30, 2025')
                          + TRADES_H + _buy(_A) + _buy(_B)),
            'ib_full.csv': HEAD + _Y2025 + TRADES_H + _buy(_A) + _buy(_B)
            + sells + _ADJ_H + refunds}
        return self._run({n: files[n] for n in order})

    def test_overlapping_copies_of_both_accounts_get_their_refund(self):
        # TJ-1043: both copies of each purchase end at net 100.
        refunds = (_adj(_A, '2025-02-03', 1, 'Refund (SYN, 10, 2025-01-06)')
                   + _adj(_B, '2025-02-03', 1, 'Refund (SYN, 10, 2025-01-06)'))
        for order in (('ib_h1.csv', 'ib_full.csv'),
                      ('ib_full.csv', 'ib_h1.csv')):
            with self.subTest(order=order):
                rows, err = self._overlap(refunds, order)
                buys = [t['net_amount'] for t in rows
                        if t['action'] == 'BUYSELL' and t['quantity'] > 0]
                self.assertEqual(buys, [100.0] * 4, err)

    def test_overlap_refund_of_one_account_only(self):
        refunds = _adj(_B, '2025-02-03', 1, 'Refund (SYN, 10, 2025-01-06)')
        rows, err = self._overlap(refunds)
        buys = sorted(t['net_amount'] for t in rows
                      if t['action'] == 'BUYSELL' and t['quantity'] > 0)
        self.assertEqual(buys, [100.0, 100.0, 101.0, 101.0], err)

    def test_a_copy_of_the_refund_in_a_statement_without_account_column(self):
        # One account's own statement (no Account column on its refund
        # row) and a combined one list one refund: folded once in each
        # copy of the trade.
        adj_h = ('Commission Adjustments,Header,Currency,Date,Description,'
                 'Amount\n')
        own = (HEAD + _Y2025
               + TRADES_H + _buy(_A) + adj_h
               + 'Commission Adjustments,Data,CAD,2025-02-03,'
                 '"Refund (SYN, 10, 2025-01-06)",1\n')
        combined = (HEAD + _Y2025 + TRADES_H + _buy(_A) + _buy(_B) + _ADJ_H
                    + _adj(_A, '2025-02-03', 1,
                           'Refund (SYN, 10, 2025-01-06)'))
        rows, err = self._run({'own.csv': own, 'combined.csv': combined})
        buys = sorted(t['net_amount'] for t in rows
                      if t['action'] == 'BUYSELL')
        self.assertEqual(buys, [100.0, 100.0, 101.0], err)
        self.assertNotIn('FEE', [t['action'] for t in rows])


class TestCancelledCopyByAccount(_Brokerage):
    """TJ-1130: an overlapping combined statement loses the cancelling
    account's copy of a cancelled trade, not the other account's."""

    def _ca(self, acct, comm=1):
        return _trade('SYN', '2025-03-03, 10:00:00', -10, 10, 100, comm,
                      code='Ca', cur='CAD', acct=acct)

    @staticmethod
    def _buys(rows):
        return sorted(t['net_amount'] for t in rows
                      if t['action'] == 'BUYSELL')

    def _both(self):
        # A's and B's fills differ in commission only, so the output
        # tells which account's row stayed; B's is listed last.
        return (_buy(_A, '2025-03-03, 10:00:00', comm=-1)
                + _buy(_B, '2025-03-03, 10:00:00', comm=-2))

    def test_the_cancelling_accounts_copy_is_dropped(self):
        old = (HEAD + _period('January 1, 2025 - June 30, 2025') + TRADES_H
               + self._both())
        new = HEAD + _Y2025 + TRADES_H + self._both() + self._ca(_A)
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        # B's row stays in both statements, A's in neither.
        self.assertEqual(self._buys(rows), [102.0, 102.0], err)
        self.assertIn('dropped from this overlapping', err)

    def test_each_account_cancels_its_own(self):
        old = (HEAD + _period('January 1, 2025 - June 30, 2025') + TRADES_H
               + self._both())
        new = (HEAD + _Y2025 + TRADES_H + self._both() + self._ca(_A)
               + self._ca(_B, 2))
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(self._buys(rows), [], err)

    def test_older_statement_holds_the_other_accounts_cancel(self):
        # The older statement already holds B's Ca; A's arrives later:
        # A's row in the older statement still goes.
        old = (HEAD + _period('January 1, 2025 - June 30, 2025') + TRADES_H
               + self._both() + self._ca(_B, 2))
        new = (HEAD + _Y2025 + TRADES_H + self._both() + self._ca(_B, 2)
               + self._ca(_A))
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(self._buys(rows), [], err)

    def test_statement_with_no_account_rows_still_drops_by_key(self):
        # One account, no Account column in the older statement.
        hdr = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
               'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
        row = ('Trades,Data,Order,Stocks,CAD,SYN,"2025-03-03, 10:00:00",10,'
               '10,0,-100,-1,0,0,0,O\n')
        old = HEAD + _period('January 1, 2025 - June 30, 2025') + hdr + row
        new = (HEAD + _Y2025 + TRADES_H
               + _buy(_A, '2025-03-03, 10:00:00', comm=-1) + self._ca(_A))
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(self._buys(rows), [], err)


def _parse(text):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'ib.csv'
        p.write_text(text, encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = IbBrokerage().parse_file(p)
    return ([t['symbol'] for t in txs if t['action'] == 'ADJUST'],
            'more than one listing' in err.getvalue())


_ROC_USD = ('Dividends,Data,USD,2025-01-10,"SYN(CA9990000001) Return of '
            'Capital USD 1 per Share",10\n')


def _op(cur, qty):
    return (f'Open Positions,Data,Summary,Stocks,{cur},SYN,{qty},1,10,'
            f'{qty * 10},10,{qty * 10},0,\n')


def _lot(when, qty, proceeds, code, cur='USD'):
    return _trade('SYN', when, qty, 10, proceeds, 0, code=code, cur=cur,
                  acct=_A)


class TestOpenShortIsNoEarlierHolding(unittest.TestCase):
    """TJ-1044: a coded short still open at the statement end is in the
    Open Positions but opened inside the statement; TJ-0660's rule (a
    coded short opening asks for no earlier holding) stays."""

    def _text(self, trades, cad):
        return (HEAD + TRADES_H + _lot('2025-01-02, 10:00:00', 10, -100, 'O')
                + trades + _DIV_H + _ROC_USD + _OP_H + _op('USD', 10)
                + _op('CAD', cad))

    def test_short_open_at_the_end(self):
        got = _parse(self._text(
            _lot('2025-02-03, 10:00:00', -10, 100, 'O', 'CAD'), -10))
        self.assertEqual(got, (['SYN.US'], False))

    def test_short_partly_covered_at_the_end(self):
        got = _parse(self._text(
            _lot('2025-02-03, 10:00:00', -10, 100, 'O', 'CAD')
            + _lot('2025-02-04, 10:00:00', 4, -40, 'C', 'CAD'), -6))
        self.assertEqual(got, (['SYN.US'], False))

    def test_a_real_opening_before_the_short_still_counts(self):
        # 5 held before the statement, sold (coded C), then a short of
        # 10 stays open: the Toronto line WAS held on the payment date.
        syms, amb = _parse(self._text(
            _lot('2025-02-03, 10:00:00', -5, 50, 'C', 'CAD')
            + _lot('2025-02-04, 10:00:00', -10, 100, 'O', 'CAD'), -10))
        self.assertTrue(amb)
        self.assertEqual(len(syms), 1)


class TestForeignCurrencyFutureOnItsVenue(unittest.TestCase):
    """TJ-1120: a future on a venue with session data is dated by that
    venue's session, whatever currency it is quoted in."""

    def test_jpy_future_on_cme_is_not_on_tokyo_time(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '10:00:00', 'Futures', 'JPY',
                                  '', symbol='QZNU6', exch='CME'),
            ('2026-06-08', '10:00:00', ''))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_jpy_future_on_cme_follows_the_evening_session(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '19:00:00', 'Futures', 'JPY',
                                  '', symbol='QZNU6', exch='CME'),
            ('2026-06-09', '00:00:00', '2026-06-08 19:00:00 ET'))

    def test_future_option_too(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '10:00:00',
                                  'Options On Futures', 'AUD', '',
                                  symbol='QZNU6 C', exch='CME'),
            ('2026-06-08', '10:00:00', ''))

    def test_a_venue_the_data_does_not_name_stays_on_local_time(self):
        for exch in ('OSE.JPN', ''):
            with self.subTest(exch=exch):
                self.assertEqual(
                    _ib_market_trade_date('2026-06-08', '10:00:00',
                                          'Futures', 'JPY', '',
                                          symbol='QZNU6', exch=exch)[:2],
                    ('2026-06-08', '23:00:00'))

    def test_stocks_stay_on_local_time(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '10:00:00', 'Stocks', 'JPY',
                                  'T', symbol='QZN', exch='CME')[:2],
            ('2026-06-08', '23:00:00'))


if __name__ == '__main__':
    unittest.main()
