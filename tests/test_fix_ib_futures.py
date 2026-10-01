"""Deferred-round fixes, area ib-futures (2026-10 audit follow-up):

* S013-00 / S058-02 / S060-12 — IB's Trades open/close Code (O / C /
  C;O) is carried on the transaction (`open_close`), so the missing-
  history checks tell a broker-declared short (O) from a sale of a
  position bought before the data (C).
* S026-22 — a futures option keeps its declared contract size
  (`multiplier`) through the books: holdings export, list, .tt check,
  what-if.
* S063-22 — an RBC export's "as of" timestamp is a coverage bound,
  checked against the tax year (parse ATTENTION, checklist
  inputs-frozen) and against RBC's spring posting of back-dated Dec-31
  book-cost adjustments.

Every fixture is synthetic: invented tickers, fake account ids marked
pii-ok."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib.core import TaxTransaction

from test_fix_ibparse import (HEAD, TRADES_H, FII_H, _parse_ib,
                              _brokerage_cli)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _trade(sym, when, qty, price, proceeds, comm=0, code='O', basis=0,
           cat='Stocks', cur='CAD', acct='U5550001'):  # pii-ok
    return (f'Trades,Data,Order,{cat},{cur},{acct},{sym},"{when}",{qty},'
            f'{price},0,{proceeds},{comm},{basis},0,0,{code}\n')


OPT_FII = (FII_H
           + 'Financial Instrument Information,Data,Equity and Index Options,'
             'QZQ   251219C00030000,QZQ 19DEC25 30 C,990000101,,QZQ,CDE,100,'
             '2025-12-19,2025-12,C,30,\n'
           + 'Financial Instrument Information,Data,Equity and Index Options,'
             'QZQ   251219C00035000,QZQ 19DEC25 35 C,990000102,,QZQ,CDE,100,'
             '2025-12-19,2025-12,C,35,\n')

# The owner's AMZN shape on a fake ticker: buy 100 (O), sell 40 (C;IA),
# sell 100 (C;O;P — closes 60, opens a 40 short), buy 40 (C).
STOCK_ROWS = (
    _trade('QZX', '2025-10-02, 10:00:00', 100, 10, -1000, -1, 'O')
    + _trade('QZX', '2025-10-29, 10:00:00', -40, 12, 480, -1, 'C;IA',
             basis=-400.4)
    + _trade('QZX', '2025-11-03, 10:00:00', -100, 12, 1200, -1, 'C;O;P')
    + _trade('QZX', '2025-11-03, 10:02:00', 40, 11.9, -476, -1, 'C'))

# A long call bought before the data, sold to close (C, IB Basis -450),
# beside a genuine write (O) that expires (C;Ep).
OPTION_ROWS = (
    _trade('QZQ 19DEC25 30 C', '2025-04-01, 10:00:00', -2, 3, 600, -2, 'C',
           basis=-450, cat='Equity and Index Options')
    + _trade('QZQ 19DEC25 35 C', '2025-04-01, 10:05:00', -2, 1, 200, -2, 'O',
             basis=-198, cat='Equity and Index Options')
    + _trade('QZQ 19DEC25 35 C', '2025-12-19, 16:20:00', 2, 0, 0, 0, 'C;Ep',
             cat='Equity and Index Options'))


def _book(rows):
    keys = TaxTransaction.__dataclass_fields__
    return [TaxTransaction(**{k: v for k, v in r.items() if k in keys})
            for r in rows]


def _parsed(body):
    _, txs, err = _parse_ib(HEAD + OPT_FII + TRADES_H + body)
    for t in txs:
        t['account'] = 'margin'
    return txs


# ------------------------------------------------- S013-00/S058-02/S060-12
class TestIbOpenCloseCarried(unittest.TestCase):

    def test_parser_keeps_the_code_and_the_closing_basis(self):
        txs = _parsed(STOCK_ROWS + OPTION_ROWS)
        got = [(t['quantity'], t.get('open_close'), t.get('broker_basis'))
               for t in txs]
        self.assertEqual(got[:4], [(100, 'O', None), (-40, 'C', '400.40 CAD'),
                                   (-100, 'C;O', None), (40, 'C', None)])
        self.assertEqual(got[4], (-2, 'C', '450.00 CAD'))
        self.assertEqual(got[5][1], 'O')

    def test_cancellation_rows_carry_no_marker(self):
        txs = _parsed(
            _trade('QZX', '2025-10-02, 10:00:00', 100, 10, -1000, -1, 'O')
            + _trade('QZX', '2025-10-02, 10:00:00', -100, 10, 1000, 1,
                     'Ca;O'))
        self.assertFalse(any(t.get('open_close') for t in txs
                             if t.get('type')))

    def test_evidence_fields_stay_out_of_the_id_and_empty_dicts(self):
        a = TaxTransaction(action='BUYSELL', date='2025-01-02', symbol='X.TO',
                           quantity=-1, net_amount=10)
        b = TaxTransaction(action='BUYSELL', date='2025-01-02', symbol='X.TO',
                           quantity=-1, net_amount=10, open_close='C;O',
                           broker_basis='9.00 CAD', multiplier=100.0)
        self.assertEqual(a.id, b.id)
        self.assertNotIn('open_close', a.to_dict())
        self.assertNotIn('multiplier', a.to_dict())
        self.assertEqual(b.to_dict()['open_close'], 'C;O')
        self.assertEqual(b.to_dict()['multiplier'], 100.0)


class TestPhantomsReadTheCode(unittest.TestCase):

    def test_ib_coded_short_is_not_missing_history(self):
        from taxjson.lib.phantom_holdings import detect_phantoms
        book = _book(_parsed(STOCK_ROWS))
        self.assertEqual(detect_phantoms(book), [])
        c, = detect_phantoms(book, include_broker_shorts=True)
        self.assertTrue(c.broker_marked_short)
        self.assertEqual(c.short_marker, 'IB code O')
        self.assertEqual(c.peak_short, -40)

    def test_without_codes_the_same_rows_are_still_flagged(self):
        # The sign-only walk is unchanged for exports with no marker.
        from taxjson.lib.phantom_holdings import detect_phantoms
        rows = _parsed(STOCK_ROWS)
        for r in rows:
            r.pop('open_close', None)
        c, = detect_phantoms(_book(rows))
        self.assertFalse(c.broker_marked_short)

    def test_closing_option_sale_is_flagged_by_default(self):
        from taxjson.lib.phantom_holdings import detect_phantoms
        got = detect_phantoms(_book(_parsed(OPTION_ROWS)))
        self.assertEqual([c.symbol for c in got], ['QZQ251219C00030000.TO'])
        self.assertTrue(got[0].broker_says_closing)
        self.assertEqual(got[0].broker_basis, '450.00 CAD')

    def test_c_o_sale_with_no_long_held_is_missing_history(self):
        # IB says the fill closed a long first: with none in the data,
        # the closed part was bought before it — the O does not excuse it.
        from taxjson.lib.phantom_holdings import detect_phantoms
        c, = detect_phantoms(_book(_parsed(
            _trade('QZX', '2025-11-03, 10:00:00', -100, 12, 1200, -1,
                   'C;O'))))
        self.assertTrue(c.broker_says_closing)
        self.assertFalse(c.broker_marked_short)

    def test_order_code_on_every_fill_is_read_per_order(self):
        # IB stamps the ORDER's code on each fill: long 1, an order to
        # sell 2 in two fills both coded C;O (the first closed the long,
        # the second opened the short), then a buy back (C). A real
        # short, not missing history (the owner's MBTK6 rows).
        from taxjson.lib.phantom_holdings import detect_phantoms
        fut = (FII_H + 'Financial Instrument Information,Data,Futures,'
               'QZBK6,QZB MAY26,990000201,,QZB,CME,0.1,2026-05-29,'
               '2026-05,,,\n')
        _, txs, _ = _parse_ib(HEAD + fut + TRADES_H
                              + _trade('QZBK6', '2026-05-14, 10:00:00', 1,
                                       1000, -100, -1, 'O', cat='Futures',
                                       cur='USD')
                              + _trade('QZBK6', '2026-05-29, 06:15:26', -1,
                                       990, 99, -1, 'C;O', cat='Futures',
                                       cur='USD')
                              + _trade('QZBK6', '2026-05-29, 06:15:26', -1,
                                       990, 99, -1, 'C;O', cat='Futures',
                                       cur='USD')
                              + _trade('QZBK6', '2026-05-29, 06:15:54', 1,
                                       991, -99.1, -1, 'C', cat='Futures',
                                       cur='USD'))
        for t in txs:
            t['account'] = 'margin'
        book = _book(txs)
        self.assertEqual(detect_phantoms(book), [])
        c, = detect_phantoms(book, include_options=True,
                             include_broker_shorts=True)
        self.assertTrue(c.broker_marked_short)
        self.assertFalse(c.broker_says_closing)

    def test_suggestions_name_the_code(self):
        from taxjson.lib.phantom_holdings import (detect_phantoms,
                                                  format_suggestions)
        e, = json.loads(format_suggestions(
            detect_phantoms(_book(_parsed(OPTION_ROWS)))))
        self.assertIn('IB code C', e['_note'])
        self.assertIn('450.00 CAD', e['_note'])


class TestFindMissingHistoryCli(unittest.TestCase):

    def _run(self, rows):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'margin_base.json'
            p.write_text(json.dumps({'transactions': rows}))
            env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / 'src'))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_missing_history',
                 '--year', '2025', str(p)],
                capture_output=True, text=True, env=env)
        return r.stdout

    def test_short_listed_apart_and_closing_sale_affects_the_year(self):
        out = self._run(_parsed(STOCK_ROWS + OPTION_ROWS))
        self.assertIn('Broker-marked short sales', out)
        self.assertIn('codes the sale O (opening)', out)
        affects = out.split('AFFECTS 2025', 1)[1]
        self.assertNotIn('QZX', affects)
        self.assertIn('QZQ251219C00030000.TO', affects)
        self.assertIn('broker says closing (IB code C)', affects)
        self.assertIn('IB Basis 450.00 CAD', affects)
        # The checklist counts table rows only: the option, not the short.
        from taxjson.lib import checklist as cl

        class _Ctx:
            year = 2025

            def sub(self, *a):
                return 0, out, ''
        r = cl.d_missing_history(_Ctx())
        self.assertEqual(r.status, 'attention')
        self.assertIn('1 position(s)', r.detail)
        self.assertIn('QZQ251219C00030000.TO', r.detail)


class TestOptionBoundaryReadsTheCode(unittest.TestCase):

    def test_closing_sale_is_not_a_write(self):
        from taxjson.lib.option_boundary import straddling, expired_open
        book = _book(_parsed(OPTION_ROWS))
        rows = straddling(book, 2025, 'grant', 2025, today=date(2026, 3, 1))
        r, = rows
        self.assertEqual(r['close_kind'], 'broker-closing')
        self.assertIn('broker says closing (IB code C', r['where'])
        self.assertNotIn('expiry', r['action'])
        self.assertTrue(r['attention'])
        x, = expired_open(book, 2025, today=date(2026, 3, 1))
        self.assertTrue(x['broker_closing'])

    def test_genuine_write_unchanged(self):
        from taxjson.lib.option_boundary import straddling
        book = _book(_parsed(
            _trade('QZQ 19DEC25 35 C', '2025-04-01, 10:05:00', -2, 1, 200,
                   -2, 'O', cat='Equity and Index Options')))
        r, = straddling(book, 2025, 'grant', 2025, today=date(2026, 3, 1))
        self.assertEqual(r['close_kind'], 'expired?')
        self.assertIn('import the expiry', r['action'])


# ------------------------------------------------------------- S026-22
FUTOPT_FII = (FII_H + 'Financial Instrument Information,Data,Options On '
              'Futures,QZCL JAN26 52 P,QZCL JAN26 52 P,990000071,,QZCL,'
              'NYMEX,1000,2025-12-16,2026-01,P,52,\n')


class TestFuturesOptionMultiplier(unittest.TestCase):

    def test_brokerage_keeps_the_contract_size_on_derivatives_only(self):
        body = (HEAD + FUTOPT_FII
                + 'Financial Instrument Information,Data,Stocks,QZX,QZX '
                  'CORP,990000001,CA0000000001,,TSE,1,,,COMMON,,\n'
                + TRADES_H
                + _trade('QZCL JAN26 52 P', '2025-11-03, 10:00:00', 2, 0.83,
                         -1660, -4, cat='Options On Futures', cur='USD')
                + _trade('QZX', '2025-11-03, 10:00:00', 10, 10, -100, -1))
        rc, doc, err, _ = _brokerage_cli({'ib.csv': body})
        self.assertEqual(rc, 0, err)
        by = {t['symbol']: t for t in doc['transactions']}
        fut = next(t for s, t in by.items() if s.startswith('F:'))
        self.assertEqual(fut['multiplier'], 1000.0)
        self.assertNotIn('multiplier', by['QZX.TO'])

    def test_inventory_export_and_list_use_the_declared_size(self):
        from taxjson.lib.pipeline import annotate_inventory_multipliers
        from taxjson.bin.taxjson_export import (contract_multiplier_of,
                                                render_report)
        sym = 'F:QZCL260116P00052000.US'
        res = {'inventory': [{'symbol': sym, 'qty': 2.0,
                              'total_cost': 1664.0, 'currency': 'USD'}]}
        annotate_inventory_multipliers(res, _book([
            {'action': 'BUYSELL', 'date': '2025-11-03', 'symbol': sym,
             'quantity': 2, 'net_amount': 1664, 'multiplier': 1000.0}]))
        self.assertEqual(res['inventory'][0]['multiplier'], 1000.0)
        b = dict(res['inventory'][0])
        self.assertEqual(contract_multiplier_of(sym, b), 1000.0)
        # Undeclared: unknown, never the equity 100.
        self.assertIsNone(contract_multiplier_of(sym, {}))
        self.assertEqual(contract_multiplier_of('QZQ251219C00030000.TO', {}),
                         100.0)
        txt = '\n'.join(render_report({sym: {**b, 'qty': 2.0,
                                             'total_cost': 1664.0}}))
        self.assertIn('0.8320', txt)          # per barrel, not per contract

    def test_tt_line_checked_at_the_declared_size(self):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        sym = 'F:QZCL260116P00052000.US'

        def warn(line):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                tx = parse_tt_line(line, 'margin')
            return tx, err.getvalue()
        tx, err = warn(f'BUYSELL 2025-11-03 10:00:00 {sym} 2 USD 0.83 '
                       f'1664.00 4.00 x1000')
        self.assertEqual(tx['multiplier'], 1000.0)
        self.assertEqual(err, '')
        _, err = warn(f'BUYSELL 2025-11-03 10:00:00 {sym} 2 USD 0.83 '
                      f'166.40 4.00 x1000')
        self.assertIn('qty*price*1000', err)
        # Without a size a futures line is not compared (as before).
        _, err = warn(f'BUYSELL 2025-11-03 10:00:00 {sym} 2 USD 0.83 '
                      f'1664.00 4.00')
        self.assertEqual(err, '')
        with self.assertRaises(ValueError):
            parse_tt_line(f'BUYSELL 2025-11-03 10:00:00 {sym} 2 USD 0.83 '
                          f'1664.00 4.00 junk', 'margin')

    def test_tt_round_trip_keeps_the_size(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line, parse_tt_line
        sym = 'F:QZCL260116P00052000.US'
        line = tx_to_tt_line({'action': 'BUYSELL', 'date': '2025-11-03',
                              'time': '10:00:00', 'symbol': sym,
                              'quantity': 2, 'currency': 'USD',
                              'price': 0.83, 'net_amount': 1664.0,
                              'fee': 4.0, 'multiplier': 1000.0})
        self.assertTrue(line.endswith(' x1000'), line)
        self.assertEqual(parse_tt_line(line, 'm')['multiplier'], 1000.0)
        eq = tx_to_tt_line({'action': 'BUYSELL', 'date': '2025-11-03',
                            'time': '10:00:00',
                            'symbol': 'QZQ251219C00030000.TO',
                            'quantity': 2, 'currency': 'CAD', 'price': 3,
                            'net_amount': 602.0, 'multiplier': 100.0})
        self.assertNotIn(' x', eq)

    def test_trace_fee_uses_the_declared_size(self):
        from taxjson.lib.core import _effective_fee_for_trace
        t = TaxTransaction(action='BUYSELL', date='2025-11-03',
                           symbol='F:QZCL260116P00052000.US', quantity=2,
                           price=0.83, net_amount=1664.0, multiplier=1000.0)
        self.assertAlmostEqual(_effective_fee_for_trace(t), 4.0)



if __name__ == '__main__':
    unittest.main()
