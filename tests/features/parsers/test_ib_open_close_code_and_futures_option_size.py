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
pii-ok.

Formerly: test_fix_ib_futures"""
import contextlib
import io
import unittest

from taxjson.model.transaction import TaxTransaction

from _support.ib_statement_rows import HEAD, TRADES_H, FII_H, _parse_ib, _brokerage_cli
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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

# Buy 150 (O), sell 60 (C;IA), sell 150 (C;O;P — closes 90, opens a 60
# short), buy 60 (C).
STOCK_ROWS = (
    _trade('QZX', '2025-09-15, 10:00:00', 150, 10, -1500, -1, 'O')
    + _trade('QZX', '2025-10-13, 10:00:00', -60, 12, 720, -1, 'C;IA',
             basis=-600.4)
    + _trade('QZX', '2025-10-20, 10:00:00', -150, 12, 1800, -1, 'C;O;P')
    + _trade('QZX', '2025-10-20, 10:02:00', 60, 11.9, -714, -1, 'C'))

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
    """Formerly: test_fix_ib_futures.TestIbOpenCloseCarried"""

    def test_parser_keeps_the_code_and_the_closing_basis(self):
        txs = _parsed(STOCK_ROWS + OPTION_ROWS)
        got = [(t['quantity'], t.get('open_close'), t.get('broker_basis'))
               for t in txs]
        self.assertEqual(got[:4], [(150, 'O', None), (-60, 'C', '600.40 CAD'),
                                   (-150, 'C;O', None), (60, 'C', None)])
        self.assertEqual(got[4], (-2, 'C', '450.00 CAD'))
        self.assertEqual(got[5][1], 'O')

    def test_cancellation_rows_carry_no_marker(self):
        txs = _parsed(
            _trade('QZX', '2025-09-15, 10:00:00', 150, 10, -1500, -1, 'O')
            + _trade('QZX', '2025-09-15, 10:00:00', -150, 10, 1500, 1,
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


# ------------------------------------------------------------- S026-22
FUTOPT_FII = (FII_H + 'Financial Instrument Information,Data,Options On '
              'Futures,QZCL JAN26 47 P,QZCL JAN26 47 P,990000071,,QZCL,'
              'NYMEX,1000,2025-12-16,2026-01,P,47,\n')


class TestFuturesOptionMultiplier(unittest.TestCase):
    """Formerly: test_fix_ib_futures.TestFuturesOptionMultiplier"""

    def test_brokerage_keeps_the_contract_size_on_derivatives_only(self):
        body = (HEAD + FUTOPT_FII
                + 'Financial Instrument Information,Data,Stocks,QZX,QZX '
                  'CORP,990000001,CA0000000001,,TSE,1,,,COMMON,,\n'
                + TRADES_H
                + _trade('QZCL JAN26 47 P', '2025-11-17, 10:00:00', 3, 0.71,
                         -2130, -6, cat='Options On Futures', cur='USD')
                + _trade('QZX', '2025-11-17, 10:00:00', 10, 10, -100, -1))
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
        sym = 'F:QZCL260116P00047000.US'
        res = {'inventory': [{'symbol': sym, 'qty': 3.0,
                              'total_cost': 2136.0, 'currency': 'USD'}]}
        annotate_inventory_multipliers(res, _book([
            {'action': 'BUYSELL', 'date': '2025-11-17', 'symbol': sym,
             'quantity': 3, 'net_amount': 2136, 'multiplier': 1000.0}]))
        self.assertEqual(res['inventory'][0]['multiplier'], 1000.0)
        b = dict(res['inventory'][0])
        self.assertEqual(contract_multiplier_of(sym, b), 1000.0)
        # Undeclared: unknown, never the equity 100.
        self.assertIsNone(contract_multiplier_of(sym, {}))
        self.assertEqual(contract_multiplier_of('QZQ251219C00030000.TO', {}),
                         100.0)
        txt = '\n'.join(render_report({sym: {**b, 'qty': 3.0,
                                             'total_cost': 2136.0}}))
        self.assertIn('0.7120', txt)          # per barrel, not per contract

    def test_tt_line_checked_at_the_declared_size(self):
        from taxjson.lib.tt_format import parse_tt_line
        sym = 'F:QZCL260116P00047000.US'

        def warn(line):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                tx = parse_tt_line(line, 'margin')
            return tx, err.getvalue()
        tx, err = warn(f'BUYSELL 2025-11-17 10:00:00 {sym} 3 USD 0.71 '
                       f'2136.00 6.00 x1000')
        self.assertEqual(tx['multiplier'], 1000.0)
        self.assertEqual(err, '')
        _, err = warn(f'BUYSELL 2025-11-17 10:00:00 {sym} 3 USD 0.71 '
                      f'216.00 6.00 x1000')
        self.assertIn('qty*price*1000', err)
        # Without a size a futures line is not compared (as before).
        _, err = warn(f'BUYSELL 2025-11-17 10:00:00 {sym} 3 USD 0.71 '
                      f'2136.00 6.00')
        self.assertEqual(err, '')
        with self.assertRaises(ValueError):
            parse_tt_line(f'BUYSELL 2025-11-17 10:00:00 {sym} 3 USD 0.71 '
                          f'2136.00 6.00 junk', 'margin')

    def test_tt_round_trip_keeps_the_size(self):
        from taxjson.lib.tt_format import tx_to_tt_line, parse_tt_line
        sym = 'F:QZCL260116P00047000.US'
        line = tx_to_tt_line({'action': 'BUYSELL', 'date': '2025-11-17',
                              'time': '10:00:00', 'symbol': sym,
                              'quantity': 3, 'currency': 'USD',
                              'price': 0.71, 'net_amount': 2136.0,
                              'fee': 6.0, 'multiplier': 1000.0})
        self.assertTrue(line.endswith(' x1000'), line)
        self.assertEqual(parse_tt_line(line, 'm')['multiplier'], 1000.0)
        eq = tx_to_tt_line({'action': 'BUYSELL', 'date': '2025-11-17',
                            'time': '10:00:00',
                            'symbol': 'QZQ251219C00030000.TO',
                            'quantity': 2, 'currency': 'CAD', 'price': 3,
                            'net_amount': 602.0, 'multiplier': 100.0})
        self.assertNotIn(' x', eq)

    def test_trace_fee_uses_the_declared_size(self):
        from taxjson.engine.ca.walk import _effective_fee_for_trace
        t = TaxTransaction(action='BUYSELL', date='2025-11-17',
                           symbol='F:QZCL260116P00047000.US', quantity=3,
                           price=0.71, net_amount=2136.0, multiplier=1000.0)
        # An engine internal: a Decimal in the default mode (TJ-3106).
        self.assertAlmostEqual(float(_effective_fee_for_trace(t)), 6.0)


if __name__ == '__main__':
    unittest.main()
