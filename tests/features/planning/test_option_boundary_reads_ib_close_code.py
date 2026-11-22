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
import unittest
from datetime import date

from taxjson.model.transaction import TaxTransaction

from _support.ib_statement_rows import HEAD, TRADES_H, FII_H, _parse_ib
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


class TestOptionBoundaryReadsTheCode(unittest.TestCase):
    """Formerly: test_fix_ib_futures.TestOptionBoundaryReadsTheCode"""

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


if __name__ == '__main__':
    unittest.main()
