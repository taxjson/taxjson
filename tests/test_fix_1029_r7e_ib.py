"""Fix round 7 (wave E), group ib: Interactive Brokers overlap dedup,
execution numbering, split ratios and refunds
(backlog TJ-1164, TJ-1165, TJ-1166, TJ-1198, TJ-1244, TJ-1245, TJ-1246).
Synthetic data only; account ids are fake (pii-ok)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from test_fix_ibparse import CA_H, HEAD, TRADES_H, _ca, _parse_ib, _trade

_A, _B = 'U1234567', 'U9990002'
_NO_ACCT_H = TRADES_H.replace('Account,', '')
_ADJ_H = 'Commission Adjustments,Header,Currency,Date,Description,Amount\n'


def _period(text):
    return f'Statement,Data,Period,"{text}"\n'


def _info(acct):
    return ('Account Information,Header,Field Name,Field Value\n'
            f'Account Information,Data,Account,{acct}\n')


_Y25 = _period('January 1, 2025 - December 31, 2025')
_H1 = _period('January 1, 2025 - June 30, 2025')
_Y26 = _period('January 1, 2026 - December 31, 2026')


def _plain(when, qty, price, proceeds, comm=0, code='O'):
    """A Trades row of a statement with no Account column."""
    return (f'Trades,Data,Order,Stocks,CAD,SYN,"{when}",{qty},{price},0,'
            f'{proceeds},{comm},0,0,0,{code}\n')


class _Dir(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)

    def _brokerage(self, files, *extra):
        paths = []
        for n, t in files.items():
            p = self.dir / n
            p.write_text(t)
            paths.append(str(p))
        r = subprocess.run(
            python_module_argv('taxjson.bin.taxjson_brokerage',
                               ['--brokerage', 'ib', '--country', 'canada',
                                '--account', 'margin', '--account-type',
                                'taxable', '--strict', *extra, *paths]),
            capture_output=True, text=True,
            env=dict(os.environ, TAXJSON_WIDTH='0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)['transactions'], r.stderr

    def _project(self, files, year=2024, combined=False):
        (self.dir / 'inputs' / 'margin').mkdir(parents=True, exist_ok=True)
        (self.dir / 'taxjson.toml').write_text(
            f'[settings]\nyear = {year}\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n'
            + ('combined_broker_accounts = true\n' if combined else ''))
        for n, t in files.items():
            (self.dir / 'inputs' / 'margin' / n).write_text(t)
        env = dict(os.environ, TAXJSON_WIDTH='0', TAXJSON_OFFLINE='1')
        r = subprocess.run(python_module_argv(
            'taxjson.bin.taxjson_run',
            ['-C', str(self.dir), 'run', '--strict', '--no-input']),
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = subprocess.run(python_module_argv(
            'taxjson.bin.taxjson_run',
            ['-C', str(self.dir), 'sum', '--json']),
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        acct = json.loads(r.stdout)['filing']['accounts'][0]
        return (acct['proceeds'], acct['acb'], acct['gain'])


_FLEX_H = TRADES_H.rstrip('\n') + ',Settle Date Target\n'


def _stated(row, day):
    return row.rstrip('\n') + f',{day}\n'


class TestStatedSettleAcrossStatements(_Dir):
    """TJ-1164: one execution listed by a Flex with a stated settle date
    and by an Activity Statement without one is one row, on the stated
    date; TJ-0971's rule stays."""

    def _rows(self, sale='2024-12-10, 10:00:00', acct=_A, buy_acct=_A):
        return [_trade('SYN', '2024-06-03, 10:00:00', 30, 10, -300, cur='CAD',
                       acct=buy_acct),
                _trade('SYN', '2024-06-04, 10:00:00', 5, 10, -50, cur='CAD',
                       acct=buy_acct),
                _trade('SYN', sale, -10, 20, 200, cur='CAD', code='C',
                       acct=acct)]

    def _files(self, settle='2024-12-13', sale='2024-12-10, 10:00:00'):
        act = self._rows(sale)
        days = ('2024-06-04', '2024-06-05', settle)
        return {'act.csv': HEAD + TRADES_H + ''.join(act),
                'flex.csv': HEAD + _FLEX_H + ''.join(
                    _stated(r, d) for r, d in zip(act, days))}

    def test_overlap_books_one_sale_either_order(self):
        for order in (('act.csv', 'flex.csv'), ('flex.csv', 'act.csv')):
            with self.subTest(order=order):
                f = self._files()
                rows, err = self._brokerage({n: f[n] for n in order})
                sales = [t['date_settle'] for t in rows
                         if t.get('quantity', 0) < 0]
                self.assertEqual(sales, ['2024-12-13'] * 2, err)

    def test_run_counts_one_sale(self):
        self.assertEqual(self._project(self._files()), (200.0, 100.0, 100.0))

    def test_stated_date_in_next_year_moves_every_copy(self):
        files = self._files('2025-01-02', '2024-12-30, 10:00:00')
        self.assertEqual(self._project(files), (0.0, 0.0, 0.0))
        self.assertEqual(self._project(files, year=2025),
                         (200.0, 100.0, 100.0))

    def test_another_accounts_alike_fill_keeps_its_own_cycle(self):
        # A states a late settle; B's alike fill in a combined statement
        # has no stated date and stays on the standard cycle.
        a = _trade('SYN', '2024-12-10, 10:00:00', -10, 20, 200, cur='CAD',
                   code='C', acct=_A)
        b = _trade('SYN', '2024-12-10, 10:00:00', -10, 20, 200, cur='CAD',
                   code='C', acct=_B)
        rows, err = self._brokerage(
            {'flex.csv': HEAD + _FLEX_H + _stated(a, '2024-12-13'),
             'comb.csv': HEAD + TRADES_H + a + b},
            '--combined-broker-accounts')
        settles = sorted(t['date_settle'] for t in rows)
        self.assertEqual(settles, ['2024-12-11', '2024-12-13', '2024-12-13'],
                         err)

    def test_blank_stated_cell_is_no_statement(self):
        f = self._files('')
        rows, err = self._brokerage(f)
        self.assertEqual({t['date_settle'] for t in rows
                          if t['quantity'] < 0}, {'2024-12-11'}, err)

    def test_two_flexes_that_disagree_are_left_as_stated(self):
        a = _trade('SYN', '2024-12-10, 10:00:00', -10, 20, 200, cur='CAD',
                   code='C', acct=_A)
        rows, err = self._brokerage(
            {'f1.csv': HEAD + _FLEX_H + _stated(a, '2024-12-13'),
             'f2.csv': HEAD + _FLEX_H + _stated(a, '2024-12-12')})
        self.assertEqual(sorted(t['date_settle'] for t in rows),
                         ['2024-12-12', '2024-12-13'], err)


def _ided(rows, col, ids):
    h = TRADES_H.rstrip('\n') + f',{col}\n'
    return h + ''.join(r.rstrip('\n') + f',{i}\n' for r, i in zip(rows, ids))


class TestExecutionIdColumns(_Dir):
    """TJ-1165: ids from different columns are different namespaces."""

    _R = [_trade('SYN', '2024-06-03, 10:00:00', 20, 10, -200, cur='CAD',
                 acct=_A, disc='Trade'),
          _trade('SYN', '2024-06-04, 10:00:00', -10, 20, 200, cur='CAD',
                 acct=_A, code='C', disc='Trade')]

    def test_two_columns_are_one_execution(self):
        got = self._project({
            'a.csv': HEAD + _ided(self._R, 'Trade ID', ('111', '222')),
            'b.csv': HEAD + _ided(self._R, 'IB Execution ID',
                                  ('0000e0d2.aa', '0000e0d2.bb'))})
        self.assertEqual(got, (200.0, 100.0, 100.0))

    def test_one_export_with_both_columns(self):
        both = (TRADES_H.rstrip('\n') + ',Trade ID,IB Execution ID\n'
                + ''.join(r.rstrip('\n') + f',{t},{x}\n' for r, t, x in zip(
                    self._R, ('111', '222'), ('e.aa', 'e.bb'))))
        got = self._project({
            'a.csv': HEAD + both,
            'b.csv': HEAD + _ided(self._R, 'Trade ID', ('111', '222'))})
        self.assertEqual(got, (200.0, 100.0, 100.0))

    def _sale(self, q=-10):
        return _trade('SYN', '2025-06-02, 10:00:00', q, 20, -q * 20, cur='CAD',
                      acct=_A, code='C', disc='Trade')

    def _descs(self, files):
        rows, err = self._brokerage(files)
        return sorted(t['description'] for t in rows), err

    def test_two_real_executions_stay_two_through_a_second_column(self):
        # a.csv lists the two alike fills by Trade ID, b.csv by both ids:
        # one number per execution, whichever column names it.
        s = self._sale()
        both = (TRADES_H.rstrip('\n') + ',Trade ID,IB Execution ID\n'
                + s.rstrip('\n') + ',111,e.bb\n'
                + s.rstrip('\n') + ',222,e.aa\n')
        got, err = self._descs({
            'a.csv': HEAD + _ided([s], 'Trade ID', ('111',)),
            'b.csv': HEAD + both})
        # 111 is e.bb, the second id in order: a.csv's copy has its mark.
        self.assertEqual(got, ['SYN', 'SYN [fill #2]', 'SYN [fill #2]'], err)

    def test_one_column_still_tells_two_fills_apart(self):
        s = self._sale()
        for col in ('Trade ID', 'IB Execution ID', 'TradeID', 'IBExecID',
                    'Execution ID'):
            with self.subTest(col=col):
                got, err = self._descs({
                    'a.csv': HEAD + _ided([s], col, ('1',)),
                    'b.csv': HEAD + _ided([s], col, ('2',))})
                self.assertEqual(got, ['SYN', 'SYN [fill #2]'], err)

    def test_spelling_of_one_column_is_one_namespace(self):
        s = self._sale()
        got, err = self._descs({
            'a.csv': HEAD + _ided([s], 'Trade ID', ('1',)),
            'b.csv': HEAD + _ided([s], 'TradeID', ('2',))})
        self.assertEqual(got, ['SYN', 'SYN [fill #2]'], err)


class TestExecutionNumberingPerAccount(_Dir):
    """TJ-1198: ids of two broker accounts are not ranked together unless
    one statement lists both accounts' rows."""

    def _r(self, acct, when, q, price):
        return _trade('SYN', when, q, price, -q * price, cur='CAD', acct=acct,
                      code='C' if q < 0 else 'O')

    def test_reexport_of_one_account_still_dedups(self):
        a1 = self._r(_A, '2024-06-03, 10:00:00', 10, 10)
        b = [self._r(_B, '2024-06-03, 10:00:00', 10, 10),
             self._r(_B, '2024-06-04, 10:00:00', 5, 11),
             self._r(_B, '2024-09-03, 10:00:00', -15, 20)]
        got = self._project({
            'fa.csv': HEAD + _ided([a1], 'IB Execution ID', ('X',)),
            'fb.csv': HEAD + _ided(b, 'IB Execution ID', ('Y', 'Y2', 'Y3')),
            'fb2.csv': HEAD + TRADES_H + ''.join(b)}, combined=True)
        self.assertEqual(got, (300.0, 153.0, 147.0))

    def test_two_accounts_own_statements_each_keep_two_fills(self):
        a = self._r(_A, '2024-06-03, 10:00:00', 10, 10)
        b = self._r(_B, '2024-06-03, 10:00:00', 10, 10)
        sale = self._r(_B, '2024-09-03, 10:00:00', -40, 20)
        got = self._project({
            'a.csv': HEAD + _ided([a, a], 'IB Execution ID', ('X1', 'X2')),
            'b.csv': HEAD + _ided([b, b, sale], 'IB Execution ID',
                                  ('Y1', 'Y2', 'Y3'))}, combined=True)
        self.assertEqual(got, (800.0, 400.0, 400.0))

    def test_combined_statement_keeps_every_accounts_fills(self):
        # One statement holds both accounts' alike fills: every row a
        # number of its own, or the file's identical rows collapse.
        a = self._r(_A, '2024-06-03, 10:00:00', 10, 10)
        b = self._r(_B, '2024-06-03, 10:00:00', 10, 10)
        rows, err = self._brokerage(
            {'c.csv': HEAD + _ided([a, a, b, b], 'IB Execution ID',
                                   ('X1', 'X2', 'Y1', 'Y2'))},
            '--combined-broker-accounts')
        self.assertEqual(len({t['description'] for t in rows}), 4, err)

    def test_one_accounts_two_executions_across_statements_stay_two(self):
        a = self._r(_A, '2024-06-03, 10:00:00', 10, 10)
        rows, err = self._brokerage(
            {'a.csv': HEAD + _ided([a], 'IB Execution ID', ('X1',)),
             'b.csv': HEAD + _ided([a], 'IB Execution ID', ('X2',))})
        self.assertEqual(sorted(t['description'] for t in rows),
                         ['SYN', 'SYN [fill #2]'], err)

    def test_statement_naming_no_account_ranks_with_the_named(self):
        # The no-account statement may be either account's: as before,
        # its id and the named ones share one numbering.
        a = self._r(_A, '2024-06-03, 10:00:00', 10, 10)
        plain = _plain('2024-06-03, 10:00:00', 10, 10, -100)
        rows, err = self._brokerage(
            {'a.csv': HEAD + _ided([a], 'IB Execution ID', ('X1',)),
             'p.csv': HEAD + _NO_ACCT_H.rstrip('\n') + ',IB Execution ID\n'
             + plain.rstrip('\n') + ',X2\n'})
        self.assertEqual(sorted(t['description'] for t in rows),
                         ['SYN', 'SYN [fill #2]'], err)


class TestRoundedTextRatio(unittest.TestCase):
    """TJ-1166: one book's legs give the delivered ratio even when the
    text ratio is rounded; TJ-0967's mix keeps the declared ratio."""

    DESC = 'SYN(US9990000301) Split 1.05 for 1 (SYN, SYN CORP, US9990000301)'

    def _ratio(self, legs, desc=None):
        text = (HEAD + TRADES_H
                + _trade('SYN', '2025-01-02, 10:00:00', 10000, 10, -100000,
                         acct=_A, cur='CAD')
                + CA_H + ''.join(_ca(desc or self.DESC, q, cur='CAD')
                                 for q in legs))
        _p, txs, err = _parse_ib(text)
        splits = [t['quantity'] for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(splits), 1, err)
        return splits[0]

    def test_one_long_book(self):
        self.assertAlmostEqual(self._ratio([-10000, 10526]), 1.0526)
        self.assertAlmostEqual(self._ratio([10526, -10000]), 1.0526)

    def test_one_short_book(self):
        self.assertAlmostEqual(self._ratio([10000, -10526]), 1.0526)

    def test_a_consolidation(self):
        d = 'SYN(US9990000301) Split 1 for 1.05 (SYN, SYN CORP, US9990000301)'
        self.assertAlmostEqual(self._ratio([-10526, 10000], d), 0.95003,
                               places=4)

    def test_two_long_books_of_one_direction(self):
        self.assertAlmostEqual(
            self._ratio([-5000, 5263, -3000, 3158]), 8421 / 8000)

    def test_two_short_books(self):
        self.assertAlmostEqual(
            self._ratio([5000, -5263, 3000, -3158]), 8421 / 8000)

    def test_rounded_long_and_short_mix_keeps_the_text(self):
        for legs in ([-10000, 10526, 500, -526],
                     [-1000, 1052, 1000, -1052],
                     [-1000, 1052, 990, -1041]):
            with self.subTest(legs=legs):
                self.assertEqual(self._ratio(legs), 1.05)

    def test_outside_the_five_percent_bound_keeps_the_text(self):
        self.assertEqual(self._ratio([-10000, 11100]), 1.05)


class TestRunWithRoundedRatio(_Dir):
    """TJ-1166 through a whole run: no short, the delivered shares sold."""

    def test_sale_of_the_delivered_shares(self):
        desc = ('SYN(US9990000301) Split 1.05 for 1 (SYN, SYN CORP, '
                'US9990000301)')
        got = self._project({'s.csv': (
            HEAD + TRADES_H
            + _trade('SYN', '2025-01-02, 10:00:00', 10000, 10, -100000,
                     acct=_A, cur='CAD')
            + _trade('SYN', '2025-06-02, 10:00:00', -10526, 20, 210520,
                     acct=_A, cur='CAD', code='C')
            + CA_H + _ca(desc, -10000, cur='CAD')
            + _ca(desc, 10526, cur='CAD'))}, year=2025)
        self.assertEqual(got, (210520.0, 100000.0, 110520.0))


def _head(acct=None, a='January 1, 2025', b='December 31, 2025'):
    return (HEAD + _period(f'{a} - {b}')
            + (_info(acct) if acct else ''))


class TestCancelInOneAccountsOwnStatement(_Dir):
    """TJ-1244: a Ca in A's own statement (no Account column, Account
    Information names A) drops A's row of a combined statement, not B's."""

    def _files(self):
        comb = (HEAD + _H1 + TRADES_H
                + _trade('SYN', '2025-03-03, 10:00:00', 10, 10, -100, -1,
                         cur='CAD', acct=_A)
                + _trade('SYN', '2025-03-03, 10:00:00', 10, 10, -100, -2,
                         cur='CAD', acct=_B))
        own = (HEAD + _Y25 + _info(_A) + _NO_ACCT_H
               + _plain('2025-03-03, 10:00:00', 10, 10, -100, -1)
               + _plain('2025-03-03, 10:00:00', -10, 10, 100, 1, 'Ca'))
        return comb, own

    def test_either_order(self):
        comb, own = self._files()
        for files in ({'comb.csv': comb, 'own.csv': own},
                      {'own.csv': own, 'comb.csv': comb}):
            with self.subTest(order=list(files)):
                rows, err = self._brokerage(files,
                                            '--combined-broker-accounts')
                self.assertEqual([t['net_amount'] for t in rows
                                  if t['action'] == 'BUYSELL'], [102.0], err)

    def test_b_has_its_own_statement_with_the_cancel(self):
        comb, _own = self._files()
        own = (HEAD + _Y25 + _info(_B) + _NO_ACCT_H
               + _plain('2025-03-03, 10:00:00', 10, 10, -100, -2)
               + _plain('2025-03-03, 10:00:00', -10, 10, 100, 2, 'Ca'))
        rows, err = self._brokerage({'comb.csv': comb, 'own.csv': own},
                                    '--combined-broker-accounts')
        self.assertEqual([t['net_amount'] for t in rows
                          if t['action'] == 'BUYSELL'], [101.0], err)

    def test_one_account_overlap_still_drops(self):
        old = (HEAD + _H1 + _info(_A) + _NO_ACCT_H
               + _plain('2025-03-03, 10:00:00', 10, 10, -100, -1))
        _c, new = self._files()
        rows, err = self._brokerage({'old.csv': old, 'new.csv': new})
        self.assertEqual([t for t in rows if t['action'] == 'BUYSELL'], [],
                         err)

    def test_held_cancel_of_an_earlier_statement(self):
        # The Ca is dated before its statement's period: it cancels A's
        # row of the earlier combined statement, not B's.
        comb = (HEAD + _period('January 1, 2024 - December 31, 2024')
                + TRADES_H
                + _trade('SYN', '2024-03-04, 10:00:00', 10, 10, -100, -1,
                         cur='CAD', acct=_A)
                + _trade('SYN', '2024-03-04, 10:00:00', 10, 10, -100, -2,
                         cur='CAD', acct=_B))
        own = (HEAD + _Y25 + _info(_A) + _NO_ACCT_H
               + _plain('2024-03-04, 10:00:00', -10, 10, 100, 1, 'Ca'))
        rows, err = self._brokerage({'comb.csv': comb, 'own.csv': own},
                                    '--combined-broker-accounts')
        self.assertEqual([t['net_amount'] for t in rows
                          if t['action'] == 'BUYSELL'], [102.0], err)


def _refund(day, trade_day='2025-03-03', amount=1):
    return (f'Commission Adjustments,Data,CAD,{day},'
            f'"Refund (SYN, 10, {trade_day})",{amount}\n')


class TestRefundInOneAccountsOwnStatement(_Dir):
    """TJ-1245: a refund in A's own statement (no Account column) is A's."""

    def _comb(self, first=_A, period=_Y25):
        second = _B if first == _A else _A
        return (HEAD + period + TRADES_H
                + _trade('SYN', '2025-03-03, 10:00:00', 10, 10, -100, -1,
                         cur='CAD', acct=first)
                + _trade('SYN', '2025-03-03, 10:00:00', 10, 10, -100, -1,
                         cur='CAD', acct=second))

    def test_refund_a_year_later_folds_into_one_account(self):
        own = (HEAD + _Y26 + _info(_A) + _ADJ_H + _refund('2026-01-10'))
        rows, err = self._brokerage({'comb.csv': self._comb(), 'own.csv': own},
                                    '--combined-broker-accounts')
        self.assertEqual(sorted(t['net_amount'] for t in rows
                                if t['action'] == 'BUYSELL'), [100.0, 101.0],
                         err)
        self.assertNotIn('FEE', [t['action'] for t in rows])

    def test_refund_with_its_trade_reaches_only_that_accounts_copy(self):
        own = (HEAD + _Y25 + _info(_A) + _NO_ACCT_H
               + _plain('2025-03-03, 10:00:00', 10, 10, -100, -1)
               + _ADJ_H + _refund('2025-04-03'))
        rows, err = self._brokerage(
            {'own.csv': own, 'comb.csv': self._comb(first=_B, period=_H1)},
            '--combined-broker-accounts')
        self.assertEqual(sorted(t['net_amount'] for t in rows
                                if t['action'] == 'BUYSELL'),
                         [100.0, 100.0, 101.0], err)

    def test_statement_naming_no_account_matches_as_before(self):
        # No Account Information: the refund cannot be placed, and two
        # keys remain: kept as a FEE row, never guessed. (Its trade in
        # one account only still takes it.)
        own = HEAD + _Y26 + _ADJ_H + _refund('2026-01-10')
        one = (HEAD + _Y25 + TRADES_H
               + _trade('SYN', '2025-03-03, 10:00:00', 10, 10, -100, -1,
                        cur='CAD', acct=_A))
        rows, err = self._brokerage({'one.csv': one, 'own.csv': own})
        self.assertEqual([t['net_amount'] for t in rows
                          if t['action'] == 'BUYSELL'], [100.0], err)


class TestTwoIdenticalRefunds(_Dir):
    """TJ-1246: two refund rows alike in one statement are two refunds;
    one refund listed by two statements stays one."""

    def _trade(self, comm=-3):
        return _plain('2025-03-03, 10:00:00', 10, 10, -100, comm)

    def _head(self, period):
        return HEAD + period + _info(_A)

    def test_two_rows_a_year_later(self):
        y25 = self._head(_Y25) + _NO_ACCT_H + self._trade()
        y26 = (self._head(_Y26) + _ADJ_H + _refund('2026-01-10') * 2)
        rows, err = self._brokerage({'y25.csv': y25, 'y26.csv': y26})
        self.assertEqual([t['net_amount'] for t in rows], [101.0], err)
        self.assertEqual(rows[0]['commission_refunds'],
                         '2026-01-10 1.0; 2026-01-10 1.0', err)

    def test_overlapping_copy_takes_both(self):
        h1 = self._head(_H1) + _NO_ACCT_H + self._trade()
        y25 = (self._head(_Y25) + _NO_ACCT_H + self._trade() + _ADJ_H
               + _refund('2025-04-03') * 2)
        for files in ({'h1.csv': h1, 'y25.csv': y25},
                      {'y25.csv': y25, 'h1.csv': h1}):
            with self.subTest(order=list(files)):
                rows, err = self._brokerage(files)
                self.assertEqual([t['net_amount'] for t in rows],
                                 [101.0, 101.0], err)

    def test_one_refund_in_two_statements_stays_one(self):
        y25 = self._head(_Y25) + _NO_ACCT_H + self._trade()
        y26a = self._head(_Y26) + _ADJ_H + _refund('2026-01-10')
        y26b = self._head(_Y26) + _ADJ_H + _refund('2026-01-10')
        rows, err = self._brokerage({'y25.csv': y25, 'a.csv': y26a,
                                     'b.csv': y26b})
        self.assertEqual([t['net_amount'] for t in rows], [102.0], err)
        self.assertNotIn('FEE', [t['action'] for t in rows])

    def test_a_statement_with_one_and_a_statement_with_two(self):
        y25 = self._head(_Y25) + _NO_ACCT_H + self._trade()
        one = self._head(_Y26) + _ADJ_H + _refund('2026-01-10')
        two = self._head(_Y26) + _ADJ_H + _refund('2026-01-10') * 2
        rows, err = self._brokerage({'y25.csv': y25, 'one.csv': one,
                                     'two.csv': two})
        self.assertEqual([t['net_amount'] for t in rows], [101.0], err)

    def test_two_rows_with_no_trade_stay_two_fee_rows(self):
        y26 = self._head(_Y26) + _ADJ_H + _refund('2026-01-10') * 2
        y25 = self._head(_Y25) + _NO_ACCT_H + _plain(
            '2025-03-04, 10:00:00', 10, 10, -100, -3)
        rows, err = self._brokerage({'y25.csv': y25, 'y26.csv': y26})
        self.assertEqual(sorted(t['net_amount'] for t in rows), [-1.0, -1.0,
                                                                 103.0], err)


if __name__ == '__main__':
    unittest.main()
