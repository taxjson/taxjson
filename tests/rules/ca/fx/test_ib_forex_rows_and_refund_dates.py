"""Backlog fixes 2026-10-29, fix round 7, group `ibcash`: IB cash.

TJ-0858 (Forex rows at the Trade detail level, a statement with no
Order rows, never reached the cash ledger), TJ-0859 (the cash reader
dropped a Forex row's clock, so the CSV order decided which of one
day's conversions came first), TJ-0743 (a cancelled Forex order and its
`Ca` reversal were two real conversions), TJ-0742 (a trade cancelled in
full dropped the commission IB kept on it).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_r7_ibcash
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_fx_cash import apply_jurisdiction
from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.dispatch import python_module_argv
from _support.ib_statement_rows import HEAD, TRADES_H, _trade
from _support.parser_cash import parsed_cash_events

_ACCT = 'U1234567'
_PERIOD = 'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
_CASH_H = 'Cash Report,Header,Currency Summary,Currency,Total\n'


def _cash(lines):
    return _CASH_H + ''.join(f'Cash Report,Data,{a},{c},{v}\n'
                             for a, c, v in lines)


class _Ledger(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)

    def _write(self, text, name='ib.csv'):
        p = self.dir / name
        p.write_text(text)
        return p

    def _ledger(self, text, opening=None, rate=1.5, native=()):
        p = self._write(text)
        events = parsed_cash_events('ib', p)
        for e in events:
            e.update(label='margin', book='margin/ib', broker='ib',
                     file='ib.csv')
        lines = []
        if opening:
            ln = CL.parse_line(f'CASHOPEN 2025-01-01 {opening}', 'cash.tt:1')
            ln.update(label='margin', book='margin/ib', file='cash.tt',
                      book_problem='')
            lines.append(ln)
        cash = {'events': events, 'lines': lines, 'problems': [],
                'stablecoins_as_cash': True}
        return events, V2.build(list(native), cash, 'CAD', 2025,
                                lambda c, d: rate, country='canada')

    def _strict(self, path, *extra):
        return subprocess.run(
            python_module_argv('taxjson.bin.taxjson_brokerage',
                               ['--brokerage', 'ib', '--country', 'canada',
                                '--strict', *extra, str(path)]),
            capture_output=True, text=True,
            env=dict(os.environ, TAXJSON_WIDTH='0'))


class TestForexTradeLevel(_Ledger):
    """TJ-0858: a statement whose Forex fills are at the Trade detail
    level only reconciled in the parser but moved no cash in the
    ledger.

    Formerly: test_fix_1029_r7_ibcash.TestForexTradeLevel"""

    def _text(self, disc):
        fx = (_trade('USD.CAD', '2025-06-02, 10:00:00', 1000, 1, -1000, 0,
                     cat='Forex', cur='CAD', acct=_ACCT, disc=disc)
              + _trade('USD.CAD', '2025-06-03, 10:00:00', -1000, 1.5, 1500,
                       0, cat='Forex', cur='CAD', acct=_ACCT, disc=disc))
        return (HEAD + _PERIOD + TRADES_H + fx
                + _cash([('Starting Cash', 'USD', 0),
                         ('Ending Cash', 'USD', 0),
                         ('Trades (Sales)', 'CAD', 1500),
                         ('Trades (Purchase)', 'CAD', -1000)]))

    def test_trade_level_forex_is_read_like_order_level(self):
        for disc in ('Order', 'Trade'):
            with self.subTest(disc=disc):
                events, v2 = self._ledger(self._text(disc), 'USD 0 0')
                self.assertEqual(
                    sum(e['kind'] == 'FXCONV' for e in events), 2)
                self.assertEqual(v2['status'], 'computed')
                self.assertAlmostEqual(v2['net_gain'], 500.0, places=2)
                self.assertAlmostEqual(
                    apply_jurisdiction(v2['net_gain'], 'canada')
                    ['reportable'], 300.0, places=2)

    def test_order_level_present_trade_rows_not_doubled(self):
        # Both levels of one fill: the Order row only (the parser's
        # rule — the Trade row is the same execution).
        fx = (_trade('USD.CAD', '2025-06-02, 10:00:00', 1000, 1, -1000, 0,
                     cat='Forex', cur='CAD', acct=_ACCT)
              + _trade('USD.CAD', '2025-06-02, 10:00:00', 1000, 1, -1000, 0,
                       cat='Forex', cur='CAD', acct=_ACCT, disc='Trade'))
        events, _ = self._ledger(HEAD + _PERIOD + TRADES_H + fx)
        self.assertEqual(sum(e['kind'] == 'FXCONV' for e in events), 1)


class TestForexClock(_Ledger):
    """TJ-0859: one day's two conversions are walked in the order of
    their stated clocks, whatever the order of the rows.

    Formerly: test_fix_1029_r7_ibcash.TestForexClock"""

    def _text(self, reverse):
        sale = _trade('USD.CAD', '2025-06-02, 10:00:00', -500, 1.5, 750, 0,
                      cat='Forex', cur='CAD', acct=_ACCT)
        buy = _trade('USD.CAD', '2025-06-02, 15:00:00', 500, 1.4, -700, 0,
                     cat='Forex', cur='CAD', acct=_ACCT)
        body = buy + sale if reverse else sale + buy
        return (HEAD + _PERIOD + TRADES_H + body
                + _cash([('Starting Cash', 'USD', 1000),
                         ('Ending Cash', 'USD', 1000),
                         ('Trades (Sales)', 'CAD', 750),
                         ('Trades (Purchase)', 'CAD', -700)]))

    def test_clock_not_row_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                events, v2 = self._ledger(self._text(reverse),
                                          'USD 1000 1000')
                convs = [e for e in events if e['kind'] == 'FXCONV']
                self.assertEqual(sorted(e.get('time') for e in convs),
                                 ['10:00:00', '15:00:00'])
                self.assertEqual(v2['status'], 'computed')
                self.assertAlmostEqual(v2['net_gain'], 250.0, places=2)
                self.assertAlmostEqual(
                    apply_jurisdiction(v2['net_gain'], 'canada')
                    ['reportable'], 50.0, places=2)
                pool = v2['pools_year_end']['USD']
                self.assertAlmostEqual(pool['units'], 1000.0, places=2)
                self.assertAlmostEqual(pool['acb'], 1200.0, places=2)


class TestForexCancel(_Ledger):
    """TJ-0743: a Forex order IB cancelled (`Ca`) moved no cash.

    Formerly: test_fix_1029_r7_ibcash.TestForexCancel"""

    def test_cancelled_forex_order_is_no_conversion(self):
        fx = (_trade('USD.CAD', '2025-06-02, 10:00:00', 1000, 1.5, -1500,
                     0, cat='Forex', cur='CAD', acct=_ACCT)
              + _trade('USD.CAD', '2025-06-02, 10:00:00', -1000, 1.5, 1500,
                       0, code='Ca', cat='Forex', cur='CAD', acct=_ACCT))
        text = (HEAD + _PERIOD + TRADES_H + fx
                + _cash([('Starting Cash', 'USD', 1000),
                         ('Ending Cash', 'USD', 1000)]))
        events, v2 = self._ledger(text, 'USD 1000 1000')
        self.assertEqual([e for e in events if e['kind'] == 'FXCONV'], [])
        self.assertEqual(v2['status'], 'computed')
        self.assertAlmostEqual(v2['net_gain'], 0.0, places=2)
        self.assertAlmostEqual(v2['pools_year_end']['USD']['acb'], 1000.0,
                               places=2)

    def test_cancel_keeps_the_rebooked_conversion(self):
        # Cancelled and rebooked at another price: the rebooking stays.
        fx = (_trade('USD.CAD', '2025-06-02, 10:00:00', 1000, 1.5, -1500,
                     0, cat='Forex', cur='CAD', acct=_ACCT)
              + _trade('USD.CAD', '2025-06-02, 10:00:00', -1000, 1.5, 1500,
                       0, code='Ca', cat='Forex', cur='CAD', acct=_ACCT)
              + _trade('USD.CAD', '2025-06-02, 10:00:00', 1000, 1.4, -1400,
                       0, cat='Forex', cur='CAD', acct=_ACCT))
        events, _ = self._ledger(HEAD + _PERIOD + TRADES_H + fx)
        convs = [e for e in events if e['kind'] == 'FXCONV']
        self.assertEqual(len(convs), 1)
        self.assertAlmostEqual(convs[0]['from_amt'], 1400.0)

    def test_cancel_with_retained_commission_keeps_the_fee(self):
        # IB kept the commission of the cancelled order: a FLOW stays.
        fx = (_trade('USD.CAD', '2025-06-02, 10:00:00', 1000, 1.5, -1500,
                     -2, cat='Forex', cur='CAD', acct=_ACCT)
              + _trade('USD.CAD', '2025-06-02, 10:00:00', -1000, 1.5, 1500,
                       0, code='Ca', cat='Forex', cur='CAD', acct=_ACCT))
        events, _ = self._ledger(HEAD + _PERIOD + TRADES_H + fx)
        self.assertEqual([e for e in events if e['kind'] == 'FXCONV'], [])
        flows = [e for e in events if e['kind'] == 'FLOW']
        self.assertEqual(len(flows), 1)
        self.assertAlmostEqual(flows[0]['amount'], -2.0)
        self.assertEqual(flows[0]['currency'], 'CAD')
        self.assertNotIn('_fee_of', flows[0])

    # Which order a `Ca` row reverses (lib/trade_cancel's rule): the same
    # price, then the cancellation's own clock, then the latest before it,
    # else the first after it (PLAN step 16 commit 10's pins).
    @staticmethod
    def _fx(when, qty, price, proceeds, code='O', comm=0):
        return _trade('USD.CAD', when, qty, price, proceeds, comm, code=code,
                      cat='Forex', cur='CAD', acct=_ACCT)

    def _left(self, *fills, extra=()):
        events, _ = self._ledger(
            HEAD + _PERIOD + TRADES_H + ''.join(fills)
            + _cash([('Starting Cash', 'USD', 1000), ('Ending Cash', 'USD', 2000),
                     ('Trades (Purchase)', 'CAD', -1500),
                     ('Trades (Purchase)', 'USD', 1000), *extra]))
        return [(e['kind'], e.get('from_amt', e.get('amount')), e.get('time'),
                 e['where'], '_fee_of' in e)
                for e in events if e['kind'] in ('FXCONV', 'FLOW')]

    def test_the_price_decides_which_order_is_reversed(self):
        # The rebooking at 1.4 sits between: the order at the Ca's price goes.
        got = self._left(self._fx('2025-06-02, 10:00:00', 1000, 1.5, -1500),
                         self._fx('2025-06-02, 10:00:00', 1000, 1.4, -1400),
                         self._fx('2025-06-02, 10:00:00', -1000, 1.5, 1500, code='Ca'),
                         extra=[('Trades (Sales)', 'CAD', 100)])
        self.assertEqual(got, [('FXCONV', 1400.0, '10:00:00', 'ib.csv:7', False)])

    def test_the_cancellations_clock_decides_before_the_row_order(self):
        got = self._left(self._fx('2025-06-02, 10:00:00', 1000, 1.5, -1500),
                         self._fx('2025-06-02, 11:00:00', 1000, 1.5, -1500),
                         self._fx('2025-06-02, 10:00:00', -1000, 1.5, 1500, code='Ca'))
        self.assertEqual(got, [('FXCONV', 1500.0, '11:00:00', 'ib.csv:7', False)])

    def test_the_latest_order_before_it_else_the_first_after_it(self):
        a = self._fx('2025-06-02, 10:00:00', 1000, 1.5, -1500)
        b = self._fx('2025-06-02, 10:00:00', 1000, 1.5, -1500, comm=-3)
        ca = self._fx('2025-06-02, 10:00:00', -1000, 1.5, 1500, code='Ca')
        fee = [('Commissions', 'CAD', -3)]
        # Both before it: the later one (b, its commission kept) goes.
        self.assertEqual(self._left(a, b, ca, extra=fee),
                         [('FXCONV', 1500.0, '10:00:00', 'ib.csv:6', False),
                          ('FLOW', -3.0, '10:00:00', 'ib.csv:8', False)])
        # Both after it: the first one (a) goes; b's commission is its own.
        self.assertEqual(self._left(ca, a, b, extra=fee),
                         [('FXCONV', 1500.0, '10:00:00', 'ib.csv:8', False),
                          ('FLOW', -3.0, '10:00:00', 'ib.csv:8', True)])

    def test_a_cancellation_whose_order_is_not_in_the_statement_is_said(self):
        events, _ = self._ledger(
            HEAD + _PERIOD + TRADES_H
            + self._fx('2025-06-02, 10:00:00', -1000, 1.5, 1500, code='Ca')
            + _cash([('Starting Cash', 'USD', 1000), ('Ending Cash', 'USD', 0),
                     ('Trades (Sales)', 'CAD', 1500), ('Trades (Sales)', 'USD', -1000)]))
        self.assertEqual(
            [(e['kind'], e.get('from_ccy'), e.get('text')) for e in events][:2],
            [('FXCONV', 'USD', None),
             ('NOTE', None, 'ib.csv:6: IB cancelled (Ca) a USD.CAD conversion of '
                            '1000 on 2025-06-02 whose original is not in this '
                            'statement — kept as a reversing conversion')])


_ADJ_H = 'Commission Adjustments,Header,Currency,Account,Date,Description,Amount\n'


def _adj(acct, day, amount, desc='Refund (SYN, 10, 2025-01-06)', cur='CAD'):
    return (f'Commission Adjustments,Data,{cur},{acct},{day},"{desc}",'
            f'{amount}\n')


class TestRefundCashDate(_Ledger):
    """TJ-0748: a commission refund folded into a purchase's cost still
    moves its cash on its own date in the cash ledger: the purchase
    spent its full commission in January, the refund came back in
    February.

    Formerly: test_fix_1029_r7_ibcash.TestRefundCashDate"""

    def test_refund_cash_walked_on_its_date(self):
        text = (HEAD + _PERIOD + TRADES_H
                + _trade('SYN', '2025-01-06, 10:00:00', 10, 50, -500, -4,
                         acct=_ACCT)
                + _ADJ_H + _adj(_ACCT, '2025-02-05', 4, cur='USD')
                + _cash([('Starting Cash', 'USD', 900),
                         ('Ending Cash', 'USD', 400),
                         ('Trades (Purchase)', 'USD', -500),
                         ('Commissions', 'USD', 0)]))
        r = self._strict(self._write(text))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)['transactions']
        # The security's cost is net of the refund (CA-ACB-COMMREFUND).
        self.assertEqual([(t['action'], t['net_amount'], t['fee'],
                           t.get('commission_refunds')) for t in rows],
                         [('BUYSELL', 500.0, 0.0, '2025-02-05 4.0')])
        for t in rows:
            t.update(account='margin', _book='margin/ib')
        p = self._write(text)
        events = parsed_cash_events('ib', p)
        for e in events:
            e.update(label='margin', book='margin/ib', broker='ib',
                     file='ib.csv')
        ln = CL.parse_line('CASHOPEN 2025-01-01 USD 900 900', 'cash.tt:1')
        ln.update(label='margin', book='margin/ib', file='cash.tt',
                  book_problem='')
        v2 = V2.build(rows, {'events': events, 'lines': [ln],
                             'problems': [], 'stablecoins_as_cash': True},
                      'CAD', 2025,
                      lambda c, d: 2.0 if d >= '2025-02-05' else 1.5,
                      country='canada')
        self.assertEqual(v2['status'], 'computed', v2['problems'])
        self.assertAlmostEqual(v2['net_gain'], 252.0, places=2)
        self.assertAlmostEqual(
            apply_jurisdiction(v2['net_gain'], 'canada')['reportable'],
            52.0, places=2)
        self.assertAlmostEqual(v2['pools_year_end']['USD']['acb'], 404.0,
                               places=2)

    def test_refunds_listed_in_date_order(self):
        # Two refunds, the newer listed first: one canonical evidence
        # string, so overlapping copies read alike.
        text = (HEAD + _PERIOD + TRADES_H
                + _trade('SYN', '2025-01-06, 10:00:00', 10, 50, -500, -4,
                         acct=_ACCT, cur='CAD')
                + _ADJ_H + _adj(_ACCT, '2025-02-10', 2)
                + _adj(_ACCT, '2025-01-20', 1))
        r = self._strict(self._write(text))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)['transactions']
        self.assertEqual([t.get('commission_refunds') for t in rows],
                         ['2025-01-20 1.0; 2025-02-10 2.0'])


if __name__ == '__main__':
    unittest.main()
