"""Backlog fixes 2026-10-29, fix round 7, group `ibcash`: IB cash.

TJ-0858 (Forex rows at the Trade detail level, a statement with no
Order rows, never reached the cash ledger), TJ-0859 (the cash reader
dropped a Forex row's clock, so the CSV order decided which of one
day's conversions came first), TJ-0743 (a cancelled Forex order and its
`Ca` reversal were two real conversions), TJ-0742 (a trade cancelled in
full dropped the commission IB kept on it).
Synthetic data only; account ids are fake (pii-ok).
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_fx_cash import apply_jurisdiction
from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.brokerages.ib_extractor import ib_cash_events
from taxjson.lib.core import TaxTransaction
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.trade_cancel import pair_cancellations
from test_fix_ibparse import HEAD, TRADES_H, _trade

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
        events = ib_cash_events(p)
        for e in events:
            e.update(label='margin', book='margin/ib', broker='ib',
                     file='ib.csv')
        lines = []
        if opening:
            ln = CE.parse_line(f'CASHOPEN 2025-01-01 {opening}', 'cash.tt:1')
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
    ledger."""

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
    their stated clocks, whatever the order of the rows."""

    def _text(self, reverse):
        sale = _trade('USD.CAD', '2025-06-02, 10:00:00', -500, 1.5, 750, 0,
                      cat='Forex', cur='CAD', acct=_ACCT)
        buy = _trade('USD.CAD', '2025-06-02, 15:00:00', 500, 1.4, -700, 0,
                     cat='Forex', cur='CAD', acct=_ACCT)
        body = buy + sale if reverse else sale + buy
        return (HEAD + _PERIOD + TRADES_H + body
                + _cash([('Starting Cash', 'USD', 1000),
                         ('Ending Cash', 'USD', 1000)]))

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
    """TJ-0743: a Forex order IB cancelled (`Ca`) moved no cash."""

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


class TestCancelRetainedFee(_Ledger):
    """TJ-0742: a trade IB cancelled in full whose commission it kept
    leaves a FEE row of it; a refunded one leaves nothing."""

    def _text(self, ca_comm):
        return (HEAD + _PERIOD + TRADES_H
                + _trade('SYN', '2025-01-06, 10:00:00', 10, 10, -100, -2,
                         acct=_ACCT)
                + _trade('SYN', '2025-01-06, 10:00:00', -10, 10, 100,
                         ca_comm, code='Ca', acct=_ACCT)
                + _cash([('Starting Cash', 'USD', 200),
                         ('Ending Cash', 'USD', 198 + ca_comm),
                         ('Trades (Sales)', 'USD', 100),
                         ('Trades (Purchase)', 'USD', -100),
                         ('Commissions', 'USD', -2 + ca_comm)]))

    def _rows(self, text):
        r = self._strict(self._write(text))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)['transactions']

    def test_retained_commission_is_a_fee(self):
        rows = self._rows(self._text(0))
        self.assertEqual([(t['action'], t['symbol'], t['net_amount'],
                           t['currency'], t['date']) for t in rows],
                         [('FEE', 'SYN.US', 2.0, 'USD', '2025-01-06')])
        # The cash ledger then matches the broker's 198.
        native = [dict(rows[0], account='margin', _book='margin/ib')]
        _, v2 = self._ledger(self._text(0), 'USD 200 250', rate=1.25,
                             native=native)
        self.assertEqual(v2['status'], 'computed', v2['problems'])

    def test_refunded_commission_leaves_nothing(self):
        self.assertEqual(self._rows(self._text(2)), [])

    def test_merge2_pairing_keeps_the_fee(self):
        orig = TaxTransaction(action='BUYSELL', date='2025-01-06',
                              time='10:00:00', symbol='SYN.US',
                              quantity=10, currency='USD', price=10,
                              fee=2, net_amount=102, account='margin')
        ca = TaxTransaction(action='BUYSELL', date='2025-01-06',
                            time='10:00:00', symbol='SYN.US', quantity=-10,
                            currency='USD', price=10, fee=0,
                            net_amount=100, account='margin',
                            type='trade_cancel')
        kept, pairs, unmatched = pair_cancellations([orig, ca])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(unmatched, [])
        self.assertEqual([(t.action, t.net_amount, t.quantity)
                          for t in kept], [('FEE', 2.0, 0.0)])
        refunded = TaxTransaction(**dict(ca.to_dict(), fee=-2))
        kept, _p, _u = pair_cancellations([orig, refunded])
        self.assertEqual(kept, [])

    def test_ca_in_the_later_statement_keeps_the_fee(self):
        # The original in 2025's statement, its Ca (commission kept) and
        # the rebook in 2026's, dated before its period.
        from test_fix_a2_ib import _booked, _stmt
        orig = _trade('QZQ', '2025-10-22, 10:00:00', 100, 20, -2000,
                      comm=-3)
        ca = _trade('QZQ', '2025-10-22, 10:00:00', -100, 20, 2000, comm=0,
                    code='Ca')
        rebook = _trade('QZQ', '2025-10-22, 10:00:00', 100, 20, -2000,
                        comm=-0.5)
        rows, err = _booked({
            'ib_2025.csv': _stmt('January 1, 2025', 'December 31, 2025',
                                 TRADES_H, orig),
            'ib_2026.csv': _stmt('January 1, 2026', 'March 31, 2026',
                                 TRADES_H, ca, rebook)})
        got = sorted((t['action'], t['quantity'], t['net_amount'])
                     for t in rows if t['symbol'] == 'QZQ.US')
        self.assertEqual(got, [('BUYSELL', 100.0, 2000.5),
                               ('FEE', 0.0, 3.0)], err)


_ADJ_H = 'Commission Adjustments,Header,Currency,Account,Date,Description,Amount\n'


def _adj(acct, day, amount, desc='Refund (SYN, 10, 2025-01-06)', cur='CAD'):
    return (f'Commission Adjustments,Data,{cur},{acct},{day},"{desc}",'
            f'{amount}\n')


class TestCombinedRefund(_Ledger):
    """TJ-0740: a combined statement's commission refund names its
    account; it is folded into that account's trade, not refused as
    matching both accounts' identical fills."""

    def _buys(self, files):
        paths = [str(self._write(t, n)) for n, t in files.items()]
        r = subprocess.run(
            python_module_argv('taxjson.bin.taxjson_brokerage',
                               ['--brokerage', 'ib', '--country', 'canada',
                                '--account', 'margin', '--account-type',
                                'taxable', '--combined-broker-accounts',
                                '--strict', *paths]),
            capture_output=True, text=True,
            env=dict(os.environ, TAXJSON_WIDTH='0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)['transactions']
        return sorted((t['action'], t['quantity'], t['net_amount'])
                      for t in rows if t['quantity'] >= 0), r.stderr

    def test_refund_folds_into_its_account_trade(self):
        a = _trade('SYN', '2025-01-06, 10:00:00', 10, 10, -100, -1,
                   cur='CAD', acct='U1234567')
        b = _trade('SYN', '2025-01-06, 10:00:00', 10, 10, -100, -1,
                   cur='CAD', acct='U9990002')
        refund = _ADJ_H + _adj('U1234567', '2025-02-03', 1)
        want = [('BUYSELL', 10.0, 100.0), ('BUYSELL', 10.0, 101.0)]
        for label, files in (
                ('combined', {'combined.csv': HEAD + TRADES_H + a + b
                              + refund}),
                ('separate', {'a.csv': HEAD + TRADES_H + a + refund,
                              'b.csv': HEAD + TRADES_H + b})):
            with self.subTest(label):
                buys, err = self._buys(files)
                self.assertEqual(buys, want, err)
                self.assertNotIn('matches 2 trade(s)', err)

    def test_refund_of_an_unnamed_account_still_refused_when_ambiguous(self):
        # No Account on the adjustment: both fills match, as before.
        a = _trade('SYN', '2025-01-06, 10:00:00', 10, 10, -100, -1,
                   cur='CAD', acct='U1234567')
        b = _trade('SYN', '2025-01-06, 10:00:00', 10, 10, -100, -1,
                   cur='CAD', acct='U9990002')
        refund = _ADJ_H + _adj('', '2025-02-03', 1)
        buys, err = self._buys({'combined.csv': HEAD + TRADES_H + a + b
                                + refund})
        self.assertIn('FEE', [x[0] for x in buys])
        self.assertIn('matches 2 trade(s)', err)


class TestRefundOrderOverlap(_Ledger):
    """TJ-0760: two overlapping statements of one account, each with
    another subset of a trade's refunds (the newer one's rows newest
    first): both copies end at the same cost, and dedup keeps one."""

    def test_refunds_in_any_order_reconcile_the_copies(self):
        head = (HEAD + 'Account Information,Header,Field Name,Field Value\n'
                'Account Information,Data,Account,U1234567\n')
        buy = _trade('SYN', '2025-01-06, 10:00:00', 10, 50, -500, -4,
                     acct=_ACCT, cur='CAD')
        sale = _trade('SYN', '2025-06-02, 10:00:00', -10, 60, 600, 0,
                      acct=_ACCT, cur='CAD', code='C')
        r1 = _adj(_ACCT, '2025-01-20', 1)
        r2 = _adj(_ACCT, '2025-02-10', 2)
        older = head + TRADES_H + buy + _ADJ_H + r1
        for order in ((r1, r2), (r2, r1)):
            newer = head + TRADES_H + buy + sale + _ADJ_H + ''.join(order)
            with self.subTest(first=order[0] is r1):
                paths = [str(self._write(older, 'ib0.csv')),
                         str(self._write(newer, 'ib1.csv'))]
                r = self._strict(*paths[:1], paths[1])
                self.assertEqual(r.returncode, 0, r.stderr)
                buys = [(t['fee'], t['net_amount'])
                        for t in json.loads(r.stdout)['transactions']
                        if t['action'] == 'BUYSELL' and t['quantity'] > 0]
                self.assertEqual(buys, [(1.0, 501.0), (1.0, 501.0)],
                                 r.stderr)


class TestRefundCashDate(_Ledger):
    """TJ-0748: a commission refund folded into a purchase's cost still
    moves its cash on its own date in the cash ledger: the purchase
    spent its full commission in January, the refund came back in
    February."""

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
        events = ib_cash_events(p)
        for e in events:
            e.update(label='margin', book='margin/ib', broker='ib',
                     file='ib.csv')
        ln = CE.parse_line('CASHOPEN 2025-01-01 USD 900 900', 'cash.tt:1')
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
