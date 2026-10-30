"""Regression tests for fix round 7 (wave D), group shorts: a broker-marked
stock short beside a long of the same stock, in Canada, and a close of a
written option held in two accounts under close timing.

Synthetic data only; account labels and tickers are made up.
"""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from tax_rules import rule
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
    return res, err.getvalue()


def _row(account, date, qty, net, desc='', open_close='', sym='SYQ.TO'):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency='CAD', account=account,
                          description=desc, open_close=open_close)


def _gain_by_year(res):
    out = {}
    for r in res['transactions']:
        y = (r.get('date_settle') or r['date'])[:4]
        out[y] = round(out.get(y, 0.0) + r['gain'], 2)
    return out


def _held(res, key='inventory'):
    """{(account or '', symbol): (qty, cost)} of the gains file's rows."""
    return {(i.get('account', ''), i['symbol']):
            (round(i['qty'], 6), round(i['total_cost'], 2))
            for i in res[key] if abs(i['qty']) > 1e-9}


class TestCoverPastTheShort(unittest.TestCase):
    """TJ-1021: a buy larger than the short covers it and buys long with
    the rest; a later sale of the long is reported."""

    def _check(self, book):
        res, err = _run(book)
        self.assertNotIn('stranded', err)
        self.assertEqual(_gain_by_year(res), {'2025': 30.0})
        self.assertEqual(_held(res), {})

    @rule("CA-DISP-04", "CA-ACB-01")
    def test_ib_coded(self):
        self._check([_row('b', '2025-03-03', -10, 150, open_close='O'),
                     _row('b', '2025-03-10', 20, 280, open_close='C;O'),
                     _row('b', '2025-05-01', -10, 160, open_close='C')])

    @rule("CA-DISP-04", "CA-ACB-01")
    def test_rbc_marked_short_and_unmarked_buy(self):
        self._check([_row('b', '2025-03-03', -10, 150, 'SYQ INC SHORT. X'),
                     _row('b', '2025-03-10', 20, 280, 'SYQ INC X'),
                     _row('b', '2025-05-01', -10, 160, 'SYQ INC X')])

    @rule("CA-DISP-04")
    def test_the_long_part_is_held_with_its_own_cost(self):
        res, _ = _run([_row('b', '2025-03-03', -10, 150, open_close='O'),
                       _row('b', '2025-03-10', 20, 280, open_close='C;O')])
        self.assertEqual(_gain_by_year(res), {'2025': 10.0})
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 140.0)})

    @rule("CA-DISP-04")
    def test_the_commission_is_split_with_the_row(self):
        buy = TaxTransaction(
            action='BUYSELL', date='2025-03-10', date_settle='2025-03-10',
            time='10:00:00', symbol='SYQ.TO', quantity=20, price=14.0,
            net_amount=284.0, commission=4.0, currency='CAD', account='b',
            open_close='C;O')
        res, _ = _run([_row('b', '2025-03-03', -10, 150, open_close='O'), buy])
        (rec,) = res['transactions']
        self.assertAlmostEqual(rec['gain'], 150 - 142)
        self.assertAlmostEqual(rec['commission'], 2.0)
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 142.0)})

    @rule("CA-SL-02", "CA-DISP-04")
    def test_the_long_part_is_a_replacement_for_a_loss(self):
        # A loss on the pooled long, then the buy's long part held at day
        # 30: a superficial loss, as for any purchase in the window.
        res, _ = _run([
            _row('a', '2025-01-02', 10, 200),
            _row('a', '2025-03-03', -10, 100),
            _row('b', '2025-03-04', -5, 50, open_close='O'),
            _row('b', '2025-03-05', 15, 150, open_close='C;O')])
        self.assertAlmostEqual(res['summary']['total_disallowed'], 100.0)

    @rule("CA-DISP-04")
    def test_a_cover_the_size_of_the_short_stays_one_row(self):
        res, _ = _run([_row('b', '2025-03-03', -10, 150, open_close='O'),
                       _row('b', '2025-03-10', 10, 140, open_close='C')])
        self.assertEqual(_gain_by_year(res), {'2025': 10.0})
        self.assertEqual(_held(res), {})


class TestSaleCodedCO(unittest.TestCase):
    """TJ-1023: a sale coded C;O closes the account's own long; past it,
    it opens a short and leaves another account's shares alone."""

    BOOK = [('a', '2024-03-01', 10, 100, ''),
            ('b', '2025-11-03', 5, 60, 'O'),
            ('b', '2025-12-01', -15, 225, 'C;O'),
            ('b', '2026-01-20', 10, 140, 'C')]

    @rule("CA-DISP-04", "CA-ACB-01")
    def test_crossing_sale_matches_its_two_coded_parts(self):
        res, _ = _run([_row(a, d, q, n, open_close=c)
                       for a, d, q, n, c in self.BOOK],
                      year_end='2025-12-31')
        self.assertEqual(_gain_by_year(res), {'2025': 21.67, '2026': 10.0})
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 106.67)})
        # The control the item names: the same sale as 5 coded C, 10 coded O.
        book = [_row(a, d, q, n, open_close=c)
                for a, d, q, n, c in self.BOOK if c != 'C;O']
        book += [_row('b', '2025-12-01', -5, 75, open_close='C'),
                 _row('b', '2025-12-01', -10, 150, open_close='O')]
        ctl, _ = _run(book)
        self.assertEqual(_gain_by_year(ctl), _gain_by_year(res))
        self.assertEqual(_held(ctl), _held(res))

    @rule("CA-DISP-04")
    def test_coded_co_with_no_long_of_its_own_is_all_short(self):
        res, _ = _run([_row('a', '2024-03-01', 10, 100),
                       _row('b', '2025-12-01', -10, 150, open_close='C;O'),
                       _row('b', '2026-01-20', 10, 140, open_close='C')])
        self.assertEqual(_gain_by_year(res), {'2026': 10.0})
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 100.0)})

    @rule("CA-DISP-04")
    def test_one_account_gains_as_before(self):
        res, _ = _run([_row('b', '2025-11-03', 5, 60),
                       _row('b', '2025-12-01', -15, 225, open_close='C;O'),
                       _row('b', '2026-01-20', 10, 140, open_close='C')])
        self.assertEqual(_gain_by_year(res), {'2025': 15.0, '2026': 10.0})
        self.assertEqual(_held(res), {})

    @rule("CA-DISP-04")
    def test_shares_held_by_an_opening_snapshot_count_as_its_long(self):
        from taxjson.lib.core import OPENING_TYPE
        snap = TaxTransaction(
            action='BUYSELL', date='2025-01-02', date_settle='2025-01-02',
            time='00:00:00', symbol='SYQ.TO', quantity=5, price=12.0,
            net_amount=60.0, currency='CAD', account='b', type=OPENING_TYPE)
        res, _ = _run([snap,
                       _row('b', '2025-12-01', -15, 225, open_close='C;O'),
                       _row('b', '2026-01-20', 10, 140, open_close='C')])
        self.assertEqual(_gain_by_year(res), {'2025': 15.0, '2026': 10.0})


def _rbc(account_no, rows):
    return ('Account Activity Detail Report\n'
            f'"Account Number: {account_no}"\n'
            '"Date Range: January 1, 2024 - December 31, 2025"\n\n'
            'Date,Activity,Symbol,Description,Quantity,Price,'
            'Settlement Date,Currency,Value,Amount\n' + rows)


class TestInventoryKeepsThePositionsApart(unittest.TestCase):
    """TJ-1058, TJ-1103, TJ-1028: a short beside a long is two rows, each
    at its own cost; the short's is its account's own."""

    BOOK = [('a', '2024-03-01', 10, 100, ''),
            ('b', '2025-12-01', -10, 150, 'O')]
    WANT = {('', 'SYQ.TO'): (10.0, 100.0), ('b', 'SYQ.TO'): (-10.0, -150.0)}

    def _res(self, **kw):
        return _run([_row(a, d, q, n, open_close=c)
                     for a, d, q, n, c in self.BOOK], **kw)

    @rule("CA-DISP-04", "CA-ACB-01")
    def test_inventory_and_year_end(self):
        res, err = self._res(year_end='2025-12-31')
        self.assertEqual(_held(res), self.WANT)
        self.assertEqual(_held(res, 'inventory_year_end'), self.WANT)
        self.assertNotIn('stranded', err)
        self.assertNotIn('conservation', err)

    @rule("CA-DISP-04")
    def test_year_end_is_the_short_of_that_day(self):
        res, _ = _run([_row('a', '2024-03-01', 10, 100),
                       _row('b', '2025-12-01', -10, 150, open_close='O'),
                       _row('b', '2026-01-20', 10, 140, open_close='C')],
                      year_end='2025-12-31')
        self.assertEqual(_held(res, 'inventory_year_end'), self.WANT)
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 100.0)})

    @rule("CA-DISP-04")
    def test_two_shorts_each_keep_their_share(self):
        res, _ = _run([_row('a', '2024-03-01', 10, 100),
                       _row('b', '2025-12-01', -10, 150, open_close='O'),
                       _row('c', '2025-12-02', -30, 150, open_close='O')])
        held = _held(res)
        self.assertEqual(held[('b', 'SYQ.TO')], (-10.0, -75.0))
        self.assertEqual(held[('c', 'SYQ.TO')], (-30.0, -225.0))

    @rule("CA-DISP-04")
    def test_a_short_alone_stays_the_symbols_row(self):
        res, _ = _run([_row('b', '2025-12-01', -10, 150, open_close='O')])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (-10.0, -150.0)})
        (row,) = res['inventory']
        self.assertNotIn('short_pool', row)

    @rule("CA-DISP-04")
    def test_split_for_account_and_its_sum(self):
        from taxjson.bin.taxjson_split_gains import _split_inventory
        res, _ = self._res()
        book_a = [{'symbol': 'SYQ.TO', 'action': 'BUYSELL', 'quantity': 10,
                   'date': '2024-03-01', 'date_settle': '2024-03-01'}]
        book_b = [{'symbol': 'SYQ.TO', 'action': 'BUYSELL', 'quantity': -10,
                   'date': '2025-12-01', 'date_settle': '2025-12-01'}]
        a = _split_inventory(res['inventory'], 'a', book_a, {}, '2025-12-31')
        b = _split_inventory(res['inventory'], 'b', book_b, {}, '2025-12-31')
        self.assertEqual([(r['qty'], r['total_cost']) for r in a],
                         [(10.0, 100.0)])
        self.assertEqual([(r['qty'], r['total_cost']) for r in b],
                         [(-10.0, -150.0)])

    @rule("CA-DISP-04")
    def test_an_account_long_and_short_has_both_rows(self):
        from taxjson.bin.taxjson_split_gains import _split_inventory
        res, _ = _run([_row('b', '2025-01-02', 10, 100),
                       _row('b', '2025-12-01', -4, 60, open_close='O')])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 100.0),
                                      ('b', 'SYQ.TO'): (-4.0, -60.0)})
        book = [{'symbol': 'SYQ.TO', 'action': 'BUYSELL', 'quantity': q,
                 'date': d, 'date_settle': d}
                for q, d in ((10, '2025-01-02'), (-4, '2025-12-01'))]
        rows = _split_inventory(res['inventory'], 'b', book, {}, '2025-12-31')
        self.assertEqual(sorted((r['qty'], r['total_cost']) for r in rows),
                         [(-4.0, -60.0), (10.0, 100.0)])


class TestFuturesShortBesideALong(unittest.TestCase):
    """TJ-1103: the same for a future: a long contract and another
    account's short are not their net."""

    @rule("CA-FX-04", "CA-ACB-01")
    def test_the_long_contract_is_held(self):
        from test_fix_1029_r7_coreca import _fut, _settled
        res, _ = _run(_settled([_fut('old', '2025-01-06', 1, 100),
                                _fut('loss', '2025-03-03', -1, 50),
                                _fut('rep', '2025-03-05', 1, 100),
                                _fut('short', '2025-04-01', -1, 100,
                                     account='b')]))
        held = _held(res)
        self.assertEqual(held[('', 'F:SYQH6.US')][0], 1.0)
        self.assertAlmostEqual(held[('', 'F:SYQH6.US')][1], 50.0)
        self.assertEqual(held[('b', 'F:SYQH6.US')][0], -1.0)


class TestListAndShares(unittest.TestCase):
    """TJ-1058, TJ-1104, TJ-1028 from the command line: `list`, `list
    --date`, `shares` and `harvest`."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from _qa_project import project, tj
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls._tmp.name, 'p', {
            'a/rbc.csv': _rbc('99900001',
                              '03/01/2024,Buy,SYQ.TO,SYQ INC UNSOLICITED,'
                              '15,10.00,03/04/2024,CAD,-150.00,-150.00\n'),
            'b/rbc.csv': _rbc('99900002',
                              '12/01/2024,Sell,SYQ.TO,SYQ INC SHORT. '
                              'UNSOLICITED,10,15.00,12/03/2024,CAD,'
                              '150.00,150.00\n'),
        })
        (cls.root / 'taxjson.toml').write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = ["USD"]\n'
            '[accounts.a]\ntype = "taxable"\n'
            '[accounts.b]\ntype = "taxable"\n')
        cls.tj = staticmethod(lambda *a, **k: tj(cls.root, *a, **k))
        cls.run_out = cls.tj('run')

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _list(self, *args):
        import json
        rows = json.loads(self.tj('list', '--json', *args).stdout)['rows']
        return {(r['account'], r['qty']): r['cost'] for r in rows}

    WANT = {('a', 15.0): 150.0, ('b', -10.0): -150.0}

    @rule("CA-DISP-04")
    def test_list(self):
        self.assertEqual(self._list(), self.WANT)

    @rule("CA-DISP-04")
    def test_list_date(self):
        self.assertEqual(self._list('--date', '2024-12-31'), self.WANT)

    @rule("CA-DISP-04")
    def test_list_date_before_the_short(self):
        self.assertEqual(self._list('--date', '2024-11-29'),
                         {('a', 15.0): 150.0})

    @rule("CA-DISP-04")
    def test_no_stranded_basis_warning(self):
        diag = ''.join(p.read_text()
                       for p in (self.root / 'work').glob('*.diag'))
        self.assertNotIn('stranded', diag)

    @rule("CA-DISP-04")
    def test_shares_nets_the_cost_never_adds_it(self):
        import json
        (row,) = json.loads(self.tj('shares', '--json').stdout)['rows']
        self.assertEqual((row['qty'], row['cost']), (5.0, 0.0))
        self.assertEqual(row['accounts'], {'a': 15.0, 'b': -10.0})

    @rule("CA-DISP-04")
    def test_harvest_values_each_at_its_own_cost(self):
        import datetime
        import json
        from _style import quote_spelling
        (self.root / 'work' / '.price_cache.json').write_text(json.dumps({
            'SYQ.TO': {'price': 12.0, 'source': 'yfinance', 'currency': 'CAD',
                       'asof': datetime.date.today().isoformat(),
                       'quote_symbol': quote_spelling(self.root,
                                                      'SYQ.TO')}}))
        doc = json.loads(self.tj('harvest', '--json', check=False).stdout)
        rows = {r['account']: r for r in doc['rows']}
        self.assertAlmostEqual(rows['a']['unrealized'], 30.0, places=2)
        self.assertAlmostEqual(rows['b']['unrealized'], 30.0, places=2)


class TestT1135CompensationOnAMarkedShort(unittest.TestCase):
    """TJ-1053: a payment in lieu in the short's account is no reduction
    of the income the long in another account earned."""

    @staticmethod
    def _rows(pil, pil_account='b'):
        def row(account, date, sym, qty, net, action='BUYSELL', oc=''):
            return TaxTransaction(
                action=action, date=date, date_settle=date, time='10:00:00',
                symbol=sym, quantity=qty, price=abs(net / qty) if qty else 0,
                net_amount=net, currency='CAD', account=account,
                open_close=oc)
        return [
            row('a', '2025-01-02', 'OTHER.US', 1, 100001.0),
            row('a', '2025-01-02', 'SYN.US', 100, 1000.0),
            row('a', '2025-02-03', 'SYN.US', 0, 100.0, 'DIVIDEND'),
            row('b', '2025-05-01', 'SYN.US', -100, 1500.0, oc='O'),
            row(pil_account, '2025-06-02', 'SYN.US', 0, pil,
                'DIVIDEND_IN_LIEU'),
            row('b', '2025-09-02', 'SYN.US', 100, 1400.0, oc='C')]

    def _income(self, rows):
        import copy
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        from taxjson.bin import taxjson_t1135 as T
        from taxjson.lib.pipeline import GainsRequest, run_gains
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(T, '_DETAILS', True):
            base = []
            for acct in ('a', 'b'):
                p = Path(td) / f'{acct}_base.json'
                p.write_text(json.dumps({'transactions': [
                    r.to_dict() for r in rows if r.account == acct]}))
                base.append(p)
            g = Path(td) / 'blend_gains.json'
            g.write_text(json.dumps(run_gains(
                copy.deepcopy(rows), [], [], GainsRequest(
                    country='canada', taxable=True, year=2025,
                    phantom_hint=False))))
            rep = T.build_report(base, [g], 2025, {}, 'CAD',
                                 today='2026-10-29')
        return next(r for r in rep['properties']
                    if r['symbol'] == 'SYN.US')['income']

    @rule("CA-RPT-14")
    def test_the_shorts_compensation_is_left_out(self):
        self.assertEqual(self._income(self._rows(-30.0)), 100.0)
        self.assertEqual(self._income(self._rows(0.0)), 100.0)

    @rule("CA-RPT-14")
    def test_a_reversal_in_the_long_account_still_nets(self):
        self.assertEqual(self._income(self._rows(-30.0, 'a')), 70.0)


class TestCloseTimingKeepsEachAccountsPremium(unittest.TestCase):
    """TJ-1105: under close timing a close takes its own account's
    average premium, never another account's."""

    OPT = 'SYQ261218P00010000.US'

    def _write(self, account, date, net, qty=-1):
        return TaxTransaction(
            action='BUYSELL', date=date, date_settle=date, time='10:00:00',
            symbol=self.OPT, quantity=qty, price=abs(net / qty) / 100,
            net_amount=net, currency='CAD', account=account)

    def _assign(self, account='b'):
        return [
            TaxTransaction(action='ASSIGN', date='2025-01-21',
                           date_settle='2025-01-21', time='10:00:00',
                           symbol=self.OPT, quantity=1, currency='CAD',
                           account=account),
            _row(account, '2025-01-21', 100, 1000, sym='SYQ.US')]

    def _inv(self, res):
        return {i['symbol']: (i['qty'], round(i['total_cost'], 2))
                for i in res['inventory'] if abs(i['qty']) > 1e-9}

    @rule("CA-OPT-02", "CA-OPT-06")
    def test_assignment_folds_its_own_contracts_premium(self):
        res, _ = _run([self._write('a', '2024-12-02', 100),
                       self._write('b', '2025-01-06', 300)] + self._assign())
        self.assertEqual(self._inv(res), {'SYQ.US': (100.0, 700.0),
                                          self.OPT: (-1.0, -100.0)})
        self.assertEqual(res['transactions'], [])

    @rule("CA-OPT-02")
    def test_buy_back_gains_its_own_contracts_premium(self):
        res, _ = _run([self._write('a', '2024-12-02', 100),
                       self._write('b', '2025-01-06', 300),
                       self._write('b', '2025-02-03', 50, qty=1)])
        self.assertEqual(_gain_by_year(res), {'2025': 250.0})
        self.assertEqual(self._inv(res), {self.OPT: (-1.0, -100.0)})

    @rule("CA-OPT-02")
    def test_one_account_still_averages(self):
        res, _ = _run([self._write('b', '2024-12-02', 100),
                       self._write('b', '2025-01-06', 300),
                       self._write('b', '2025-02-03', 50, qty=1)])
        self.assertEqual(_gain_by_year(res), {'2025': 150.0})
        self.assertEqual(self._inv(res), {self.OPT: (-1.0, -200.0)})

    @rule("CA-OPT-02")
    def test_an_accounts_average_survives_a_close(self):
        # b's two writes average 200; one closed, the other still carries
        # 200 when a's own is open beside it.
        res, _ = _run([self._write('b', '2024-12-02', 100),
                       self._write('b', '2025-01-06', 300),
                       self._write('b', '2025-02-03', 50, qty=1),
                       self._write('a', '2025-02-10', 40),
                       self._write('b', '2025-03-03', 50, qty=1)])
        self.assertEqual(_gain_by_year(res), {'2025': 300.0})
        self.assertEqual(self._inv(res), {self.OPT: (-1.0, -40.0)})


if __name__ == '__main__':
    unittest.main()
