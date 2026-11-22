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
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.model.transaction import TaxTransaction

from _support.ib_statement_rows import HEAD, TRADES_H, FII_H, _parse_ib
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


REPO_ROOT = _paths.REPO


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


class TestPhantomsReadTheCode(unittest.TestCase):
    """Formerly: test_fix_ib_futures.TestPhantomsReadTheCode"""

    def test_ib_coded_short_is_not_missing_history(self):
        from taxjson.lib.missing_history import detect_missing_history
        book = _book(_parsed(STOCK_ROWS))
        self.assertEqual(detect_missing_history(book), [])
        c, = detect_missing_history(book, include_broker_shorts=True)
        self.assertTrue(c.broker_marked_short)
        self.assertEqual(c.short_marker, 'IB code O')
        self.assertEqual(c.peak_short, -60)

    def test_without_codes_the_same_rows_are_still_flagged(self):
        # The sign-only walk is unchanged for exports with no marker.
        from taxjson.lib.missing_history import detect_missing_history
        rows = _parsed(STOCK_ROWS)
        for r in rows:
            r.pop('open_close', None)
        c, = detect_missing_history(_book(rows))
        self.assertFalse(c.broker_marked_short)

    def test_closing_option_sale_is_flagged_by_default(self):
        from taxjson.lib.missing_history import detect_missing_history
        got = detect_missing_history(_book(_parsed(OPTION_ROWS)))
        self.assertEqual([c.symbol for c in got], ['QZQ251219C00030000.TO'])
        self.assertTrue(got[0].broker_says_closing)
        self.assertEqual(got[0].broker_basis, '450.00 CAD')

    def test_c_o_sale_with_no_long_held_is_missing_history(self):
        # IB says the fill closed a long first: with none in the data,
        # the closed part was bought before it — the O does not excuse it.
        from taxjson.lib.missing_history import detect_missing_history
        c, = detect_missing_history(_book(_parsed(
            _trade('QZX', '2025-10-20, 10:00:00', -150, 12, 1800, -1,
                   'C;O'))))
        self.assertTrue(c.broker_says_closing)
        self.assertFalse(c.broker_marked_short)

    def test_order_code_on_every_fill_is_read_per_order(self):
        # IB stamps the ORDER's code on each fill: long 1, an order to
        # sell 2 in two fills both coded C;O (the first closed the long,
        # the second opened the short), then a buy back (C). A real
        # short, not missing history.
        from taxjson.lib.missing_history import detect_missing_history
        fut = (FII_H + 'Financial Instrument Information,Data,Futures,'
               'QZBM6,QZB JUN26,990000201,,QZB,CME,0.1,2026-06-26,'
               '2026-06,,,\n')
        _, txs, _ = _parse_ib(HEAD + fut + TRADES_H
                              + _trade('QZBM6', '2026-06-08, 10:00:00', 1,
                                       1000, -100, -1, 'O', cat='Futures',
                                       cur='USD')
                              + _trade('QZBM6', '2026-06-23, 07:41:12', -1,
                                       990, 99, -1, 'C;O', cat='Futures',
                                       cur='USD')
                              + _trade('QZBM6', '2026-06-23, 07:41:12', -1,
                                       990, 99, -1, 'C;O', cat='Futures',
                                       cur='USD')
                              + _trade('QZBM6', '2026-06-23, 07:41:52', 1,
                                       991, -99.1, -1, 'C', cat='Futures',
                                       cur='USD'))
        for t in txs:
            t['account'] = 'margin'
        book = _book(txs)
        self.assertEqual(detect_missing_history(book), [])
        c, = detect_missing_history(book, include_options=True,
                             include_broker_shorts=True)
        self.assertTrue(c.broker_marked_short)
        self.assertFalse(c.broker_says_closing)

    def test_suggestions_name_the_code(self):
        from taxjson.lib.missing_history import (detect_missing_history,
                                                  format_suggestions)
        e, = json.loads(format_suggestions(
            detect_missing_history(_book(_parsed(OPTION_ROWS)))))
        self.assertIn('IB code C', e['_note'])
        self.assertIn('450.00 CAD', e['_note'])


class TestFindMissingHistoryCli(unittest.TestCase):
    """Formerly: test_fix_ib_futures.TestFindMissingHistoryCli"""

    def _run(self, rows):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'margin_base.json'
            p.write_text(json.dumps({'transactions': rows}))
            env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / 'src'))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_missing_history',
                 # each row's why: --details (docs/output-style.md)
                 '--year', '2025', '--details', str(p)],
                capture_output=True, text=True, env=env)
        return r.stdout

    def test_short_listed_apart_and_closing_sale_affects_the_year(self):
        out = self._run(_parsed(STOCK_ROWS + OPTION_ROWS))
        self.assertIn('BROKER-MARKED SHORT SALES', out)
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


if __name__ == '__main__':
    unittest.main()
