"""Second-audit DEFERRED findings, the rest (A2-0590, A2-1014, A2-1091,
A2-1052/A2-1054, A2-1056). Every fixture is synthetic: invented tickers, fake
account ids and made-up amounts.

Formerly: test_fix_def_rest"""
import unittest
from _support.ib_statement_rows import TRADES_H, _trade


# ------------------------------------------ A2-1056: hints the map answers
import os


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# --------------------------- A2-1091: side-effect lines of an undone event
from _support.ib_statement_rows import CA_H, _ca
from _support.ib_statement_books import _stmt, _booked

SD = ('QZT(US9990000501) Stock Dividend US9990000501 1 for 10 '
      '(QZT, QZT CORP, US9990000501)')
CT = 'QZT(US9990000501) Merged(Acquisition) FOR USD 30.00 PER SHARE'


class TestCrossStatementCaUndoSilencesSideEffects(unittest.TestCase):
    """A2-1091: a stock dividend (or cash takeover) in the 2025 statement
    cancelled by a Ca in the 2026 one: the row was removed, but the
    2025 parse had already printed its stock-dividend ATTENTION /
    cash-takeover NOTE. Under taxjson-brokerage the lines wait for the
    cross-statement pass and an undone event's line is not printed.

    Formerly: test_fix_def_rest.TestCrossStatementCaUndoSilencesSideEffects"""

    BUY = TRADES_H + _trade('QZT', '2025-01-10, 10:00:00', 100, 30, -3000)

    def _files(self, desc, qty, proceeds=0, value=0):
        a = _stmt('January 1, 2025', 'December 31, 2025', self.BUY, CA_H,
                  _ca(desc, qty, proceeds=proceeds, value=value,
                      when='2025-12-30, 20:25:00'))
        b = _stmt('January 1, 2026', 'March 31, 2026', CA_H,
                  _ca(desc, -qty, proceeds=-proceeds, value=-value,
                      when='2026-01-05, 20:25:00', code='Ca'))
        return a, b

    def test_undone_stock_dividend_prints_no_attention(self):
        a, b = self._files(SD, 10, value=300)
        rows, err = _booked({'a.csv': a})
        self.assertIn('stock dividend of 10', err)        # control
        rows, err = _booked({'a.csv': a, 'b.csv': b})
        self.assertIn('is undone', err)
        self.assertNotIn('stock dividend of 10', err)
        # The Ca row the pass paired is not a skipped row either.
        self.assertNotIn('whose original is not in this statement', err)
        self.assertEqual(_position_of(rows, 'QZT.US'), 100.0)

    def test_undone_cash_takeover_prints_no_note(self):
        a, b = self._files(CT, -100, proceeds=3000)
        rows, err = _booked({'a.csv': a})
        self.assertIn('cash takeover booked as a sale', err)  # control
        rows, err = _booked({'a.csv': a, 'b.csv': b})
        self.assertIn('is undone', err)
        self.assertNotIn('cash takeover booked as a sale', err)

    def test_live_event_still_prints_once(self):
        a, _b = self._files(SD, 10, value=300)
        c = _stmt('January 1, 2026', 'March 31, 2026', self.BUY)
        rows, err = _booked({'a.csv': a, 'c.csv': c})
        self.assertEqual(err.count('stock dividend of 10'), 1, err)


def _position_of(rows, sym):
    return sum(t['quantity'] for t in rows
               if t['symbol'] == sym and t['action'] in ('BUYSELL',
                                                         'ASSIGN'))


if __name__ == "__main__":
    unittest.main()
