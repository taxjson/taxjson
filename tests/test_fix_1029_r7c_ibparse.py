"""Fix round 7 (wave C), group ibparse: the IB statement parser's
detail-level deduplication across accounts and currencies, distinct
execution ids, long and short split legs, Lot-only position reports, an
explicit settlement date, and a combined statement's cancellation of
another account's trade (backlog TJ-0965, TJ-0966, TJ-0967, TJ-0969,
TJ-0971, TJ-0982). Every fixture is synthetic: invented tickers and
ISINs, fake account ids."""
import unittest

from test_fix_ibparse import (CA_H, HEAD, TRADES_H, _brokerage_cli, _ca,
                              _parse_ib, _trade)
from _style import CapturedWidth
from tax_rules import rule


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_A, _B = 'U1234567', 'U7654321'  # pii-ok: synthetic ids


def _rows(out):
    return [(t['action'], t['quantity'], t['net_amount'])
            for t in (out or {}).get('transactions', [])]


class TestDetailLevelIdentity(unittest.TestCase):
    """TJ-0965: an execution-level Trade row is a duplicate only of an
    Order row of the same account and currency."""

    def _run(self, second):
        text = (HEAD + TRADES_H
                + _trade('SYN', '2025-06-02, 10:00:00', 10, 10, -100,
                         acct=_A, cur='CAD')
                + second)
        rc, out, err, _ = _brokerage_cli(
            {'ib.csv': text}, '--country', 'canada',
            '--combined-broker-accounts', '--strict')
        self.assertEqual(rc, 0, err)
        return _rows(out)

    def test_other_accounts_trade_row_is_kept(self):
        rows = self._run(_trade('SYN', '2025-06-02, 11:00:00', 10, 20,
                                -200, acct=_B, cur='CAD', disc='Trade'))
        self.assertEqual(rows, [('BUYSELL', 10.0, 100.0),
                                ('BUYSELL', 10.0, 200.0)])

    def test_other_currencys_trade_row_is_kept(self):
        text = (HEAD + TRADES_H
                + _trade('SYN', '2025-06-02, 10:00:00', 10, 10, -100,
                         acct=_A, cur='USD')
                + _trade('SYN', '2025-06-02, 11:00:00', 10, 20, -200,
                         acct=_A, cur='CAD', disc='Trade'))
        _p, txs, err = _parse_ib(text)
        self.assertEqual(sorted((t['currency'], t['quantity'])
                                for t in txs if t['action'] == 'BUYSELL'),
                         [('CAD', 10.0), ('USD', 10.0)], err)

    def test_same_fill_at_two_levels_is_booked_once(self):
        text = (HEAD + TRADES_H
                + _trade('SYN', '2025-06-02, 10:00:00', 10, 10, -100,
                         acct=_A, cur='CAD')
                + _trade('SYN', '2025-06-02, 10:00:00', 4, 10, -40,
                         acct=_A, cur='CAD', disc='Trade')
                + _trade('SYN', '2025-06-02, 10:00:00', 6, 10, -60,
                         acct=_A, cur='CAD', disc='Trade'))
        _p, txs, err = _parse_ib(text)
        self.assertEqual([t['quantity'] for t in txs
                          if t['action'] == 'BUYSELL'], [10.0], err)


class TestCancellationAccount(unittest.TestCase):
    """TJ-0982: a combined statement's cancellation of account A's buy
    never removes account B's same fill."""

    def _run(self, first, second):
        ca = _trade('SYN', '2025-06-02, 10:00:00', -10, 10, 100, comm=1,
                    acct=_A, cur='CAD', code='Ca')
        rc, out, err, _ = _brokerage_cli(
            {'ib.csv': HEAD + TRADES_H + first + second + ca},
            '--combined-broker-accounts', '--strict')
        self.assertEqual(rc, 0, err)
        return _rows(out)

    def test_either_order_keeps_b(self):
        a = _trade('SYN', '2025-06-02, 10:00:00', 10, 10, -100, comm=-1,
                   acct=_A, cur='CAD')
        b = _trade('SYN', '2025-06-02, 10:00:00', 10, 10, -100, comm=-2,
                   acct=_B, cur='CAD')
        for first, second in ((a, b), (b, a)):
            self.assertEqual(self._run(first, second),
                             [('BUYSELL', 10.0, 102.0)])

    def test_pair_cancellations_account_guard(self):
        from taxjson.lib.trade_cancel import (TRADE_CANCEL_TYPE,
                                              pair_cancellations)
        base = {'action': 'BUYSELL', 'symbol': 'SYN.TO', 'currency': 'CAD',
                'date': '2025-06-02', 'time': '10:00:00', 'price': 10.0,
                'account': 'IB'}
        a = dict(base, quantity=10.0, fee=1.0, net_amount=101.0)
        b = dict(base, quantity=10.0, fee=2.0, net_amount=102.0)
        ca = dict(base, quantity=-10.0, fee=-1.0, net_amount=99.0,
                  type=TRADE_CANCEL_TYPE)
        acct = {id(a): 'A', id(b): 'B', id(ca): 'A'}
        kept, pairs, unmatched = pair_cancellations(
            [a, b, ca], acct_of=lambda t: acct[id(t)])
        self.assertEqual(kept, [b])
        self.assertEqual(pairs, [(a, ca)])
        # Without the accounts the latest candidate is taken (as before).
        kept, _p, _u = pair_cancellations([a, b, ca])
        self.assertEqual(kept[0]['quantity'], 10.0)
        self.assertEqual(kept[0]['fee'], 1.0)


def _settle_csv(field, sale_settle, cat='Stocks', sym='SYN'):
    header = TRADES_H.rstrip('\n') + ',' + field + '\n'
    buy = _trade(sym, '2024-01-02, 10:00:00', 10, 10, -100, cur='CAD',
                 acct=_A, cat=cat).rstrip('\n') + ',2024-01-04\n'
    sale = _trade(sym, '2024-12-30, 10:00:00', -10, 20, 200, cur='CAD',
                  acct=_A, code='C', cat=cat).rstrip('\n') + (
                      ',' + sale_settle + '\n')
    return HEAD + header + buy + sale


class TestStatedSettleDate(unittest.TestCase):
    """TJ-0971: an IB trade row's own settle date (Flex Settle Date
    Target) is its settle date; the standard cycle is the fallback."""

    def _settles(self, text):
        _p, txs, err = _parse_ib(text)
        return [(t['date'], t['date_settle']) for t in txs
                if t['action'] == 'BUYSELL'], err

    @rule("CA-DATE-06")
    def test_stated_settle_moves_the_sale_to_next_year(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.pipeline import GainsRequest, run_gains
        import contextlib
        import dataclasses
        import io
        for field in ('Settle Date Target', 'SettleDateTarget',
                      'Settle Date'):
            rows, err = self._settles(_settle_csv(field, '2025-01-02'))
            self.assertEqual(rows, [('2024-01-02', '2024-01-04'),
                                    ('2024-12-30', '2025-01-02')],
                             field + err)
        _p, txs, _e = _parse_ib(_settle_csv('SettleDateTarget',
                                            '20250102'))
        names = {f.name for f in dataclasses.fields(TaxTransaction)}
        book = [TaxTransaction(**{k: v for k, v in t.items() if k in names})
                for t in txs]
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            g = run_gains(book, req=GainsRequest(
                country='canada', year=2024, taxable=True,
                phantom_hint=False))
        # The sale settles in 2025: no 2024 disposition.
        self.assertEqual(round(g['summary']['total_gain'], 2), 0.0)

    @rule("US-DATE-06")
    def test_blank_cell_falls_back_and_bad_cells_are_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        rows, _e = self._settles(_settle_csv('Settle Date Target', ''))
        self.assertEqual(rows[1], ('2024-12-30', '2024-12-31'))
        for bad, what in (('2024-12-27', 'before the trade date'),
                          ('2025-03-01', 'more than 31 days'),
                          ('soon', 'is not a date')):
            with self.assertRaises(BrokerageParseError) as cm:
                _parse_ib(_settle_csv('Settle Date Target', bad))
            self.assertIn(what, str(cm.exception))

    def test_futures_keep_the_trade_date(self):
        from test_fix_ibparse import FII_H
        text = (_settle_csv('Settle Date Target', '2024-12-31',
                            cat='Futures', sym='SYNZ5')
                + FII_H + 'Financial Instrument Information,Data,Futures,'
                'SYNZ5,SYN MAR25,990000081,,SYN,CME,1,2025-03-21,2025-03,,,'
                '\n')
        rows, err = self._settles(text)
        self.assertEqual(rows[1], ('2024-12-30', '2024-12-30'), err)

class TestSplitLegsOfTwoBooks(unittest.TestCase):
    """TJ-0967: a combined statement's long and short legs of one split
    keep the declared ratio."""

    DESC = 'SYN(US9990000301) Split 21 for 20 (SYN, SYN CORP, US9990000301)'

    def _ratio(self, legs):
        text = (HEAD + TRADES_H
                + _trade('SYN', '2025-01-02, 10:00:00', 100, 10, -1000,
                         acct=_A, cur='CAD')
                + _trade('SYN', '2025-01-02, 11:00:00', -50, 10, 500,
                         acct=_B, cur='CAD')
                + CA_H + ''.join(_ca(self.DESC, q, cur='CAD')
                                 for q in legs))
        _p, txs, err = _parse_ib(text)
        splits = [t['quantity'] for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(splits), 1, err)
        return splits[0]

    def test_long_and_short_legs(self):
        self.assertEqual(self._ratio([-100, 105, 50, -52.5]), 1.05)
        self.assertEqual(self._ratio([50, -100, 105, -52.5]), 1.05)

    def test_one_book_still_snaps(self):
        self.assertEqual(self._ratio([-100, 105]), 1.05)
        self.assertEqual(self._ratio([50, -52.5]), 1.05)
        # IB's 4-dp result: the ratio the legs deliver.
        self.assertAlmostEqual(self._ratio([-100, 105.0001]), 1.050001)
        # Two long books each floored to whole shares (no cash in lieu
        # row): the shares IB delivered, as before.
        self.assertEqual(self._ratio([-10, 10, -10, 10]), 1.0)

    def test_floored_long_and_short_legs_keep_the_text_ratio(self):
        self.assertEqual(self._ratio([-100, 105, 51, -53]), 1.05)


class TestLotOnlyPositions(unittest.TestCase):
    """TJ-0969: an IB Open Positions section of Lot rows with no Summary
    row is refused, never read as no positions."""

    def _read(self, body):
        import tempfile
        from pathlib import Path
        from test_positions_reports import _IB_HEAD, _OP_H
        from taxjson.lib.positions_reports import read_positions
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'hold.csv'
            f.write_text(_IB_HEAD + _OP_H + body, encoding='utf-8')
            return read_positions(f)

    def test_lot_only_is_refused(self):
        from taxjson.lib.positions_reports import PositionsReportError
        with self.assertRaises(PositionsReportError) as cm:
            self._read('Open Positions,Data,Lot,Stocks,CAD,SYN,10,1,10,'
                       '100,20,200,100,\n')
        self.assertIn('SYN CAD have no Summary row', str(cm.exception))

    def test_lot_with_its_summary_reads(self):
        rep = self._read('Open Positions,Data,Summary,Stocks,CAD,SYN,10,1,'
                         '10,100,20,200,100,\n'
                         'Open Positions,Data,Lot,Stocks,CAD,SYN,10,1,10,'
                         '100,20,200,100,\n')
        self.assertEqual([(r.raw_symbol, r.quantity) for r in rep.rows],
                         [('SYN', 10.0)])

    def test_empty_lot_is_no_position(self):
        rep = self._read('Open Positions,Data,Lot,Stocks,CAD,SYN,0,1,10,'
                         '0,20,0,0,\n')
        self.assertEqual(rep.rows, [])


_IDS_H = TRADES_H.rstrip('\n') + ',Trade ID,IB Execution ID\n'


def _fill(q, price, day, ident):
    return _trade('SYN', day, q, price, -q * price, acct=_A, cur='CAD',
                  code='C' if q < 0 else 'O',
                  disc='Trade').rstrip('\n') + f',{ident},{ident}\n'


_OPEN = _fill(20, 10, '2025-01-02, 10:00:00', 'SYN-OPEN')
_SALE_A = _fill(-10, 20, '2025-06-02, 10:00:00', 'SYN-SALE-A')
_SALE_B = _fill(-10, 20, '2025-06-02, 10:00:00', 'SYN-SALE-B')


class TestExecutionIds(unittest.TestCase):
    """TJ-0966: two fills alike in every booked field with different IB
    execution ids are two trades, in one statement or split over two;
    one execution exported twice stays one."""

    def _sales(self, *bodies):
        files = {f'ib_{n}.csv': HEAD + b for n, b in enumerate(bodies)}
        rc, out, err, _ = _brokerage_cli(files, '--strict')
        self.assertEqual(rc, 0, err)
        return sorted(t['description'] for t in out['transactions']
                      if t['quantity'] < 0)

    def test_split_over_two_statements_stay_two(self):
        self.assertEqual(self._sales(_IDS_H + _OPEN + _SALE_A,
                                     _IDS_H + _SALE_B),
                         ['SYN', 'SYN [fill #2]'])

    def test_one_statement_numbers_alike(self):
        self.assertEqual(self._sales(_IDS_H + _OPEN + _SALE_B + _SALE_A),
                         ['SYN', 'SYN [fill #2]'])

    def test_same_execution_twice_is_one_row(self):
        self.assertEqual(self._sales(_IDS_H + _OPEN + _SALE_A,
                                     _IDS_H + _SALE_A), ['SYN', 'SYN'])

    def test_full_run_books_both_sales(self):
        import json
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        from taxjson.lib.dispatch import python_module_argv
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / 'inputs' / 'margin').mkdir(parents=True)
            (d / 'taxjson.toml').write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (d / 'inputs' / 'margin' / 'ib_0.csv').write_text(
                HEAD + _IDS_H + _OPEN + _SALE_A)
            (d / 'inputs' / 'margin' / 'ib_1.csv').write_text(
                HEAD + _IDS_H + _SALE_B)
            env = dict(os.environ, TAXJSON_WIDTH='0', TAXJSON_OFFLINE='1')
            r = subprocess.run(python_module_argv(
                'taxjson.bin.taxjson_run',
                ['-C', str(d), 'run', '--strict', '--no-input']),
                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run(python_module_argv(
                'taxjson.bin.taxjson_run', ['-C', str(d), 'sum', '--json']),
                capture_output=True, text=True, env=env)
            acct = json.loads(r.stdout)['filing']['accounts'][0]
        self.assertEqual((acct['proceeds'], acct['acb'], acct['gain']),
                         (400.0, 200.0, 200.0))


if __name__ == '__main__':
    unittest.main()
