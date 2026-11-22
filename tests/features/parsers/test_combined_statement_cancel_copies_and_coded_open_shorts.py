"""Backlog fixes 2026-10-29, fix round 7 wave D, group `ib`: Interactive
Brokers, mostly regressions of this round's earlier fixes.

TJ-1041 (a same-day withdrawal walked before the timed Forex that funded
it), TJ-1042 / TJ-1043 (identical refunds of two accounts in combined
statements), TJ-1044 (a coded short open at the statement end read as a
holding before it opened), TJ-1120 (a JPY future on
CME dated in Tokyo time), TJ-1130 (the overlap step dropped a cancelled
trade's copy whichever account's row it was).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_r7d_ib
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from _support.ib_statement_rows import HEAD, TRADES_H, _trade
from _support.ib_brokerage_base import _A, _B, _period, _Y2025, _Brokerage, _buy

_DIV_H = 'Dividends,Header,Currency,Date,Description,Amount\n'
_OP_H = ('Open Positions,Header,DataDiscriminator,Asset Category,Currency,'
         'Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,'
         'Unrealized P/L,Code\n')


class TestCancelledCopyByAccount(_Brokerage):
    """TJ-1130: an overlapping combined statement loses the cancelling
    account's copy of a cancelled trade, not the other account's.

    Formerly: test_fix_1029_r7d_ib.TestCancelledCopyByAccount"""

    def _ca(self, acct, comm=1):
        return _trade('SYN', '2025-03-03, 10:00:00', -10, 10, 100, comm,
                      code='Ca', cur='CAD', acct=acct)

    @staticmethod
    def _buys(rows):
        return sorted(t['net_amount'] for t in rows
                      if t['action'] == 'BUYSELL')

    def _both(self):
        # A's and B's fills differ in commission only, so the output
        # tells which account's row stayed; B's is listed last.
        return (_buy(_A, '2025-03-03, 10:00:00', comm=-1)
                + _buy(_B, '2025-03-03, 10:00:00', comm=-2))

    def test_the_cancelling_accounts_copy_is_dropped(self):
        old = (HEAD + _period('January 1, 2025 - June 30, 2025') + TRADES_H
               + self._both())
        new = HEAD + _Y2025 + TRADES_H + self._both() + self._ca(_A)
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        # B's row stays in both statements, A's in neither.
        self.assertEqual(self._buys(rows), [102.0, 102.0], err)
        self.assertIn('dropped from this overlapping', err)

    def test_each_account_cancels_its_own(self):
        old = (HEAD + _period('January 1, 2025 - June 30, 2025') + TRADES_H
               + self._both())
        new = (HEAD + _Y2025 + TRADES_H + self._both() + self._ca(_A)
               + self._ca(_B, 2))
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(self._buys(rows), [], err)

    def test_older_statement_holds_the_other_accounts_cancel(self):
        # The older statement already holds B's Ca; A's arrives later:
        # A's row in the older statement still goes.
        old = (HEAD + _period('January 1, 2025 - June 30, 2025') + TRADES_H
               + self._both() + self._ca(_B, 2))
        new = (HEAD + _Y2025 + TRADES_H + self._both() + self._ca(_B, 2)
               + self._ca(_A))
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(self._buys(rows), [], err)

    def test_statement_with_no_account_rows_still_drops_by_key(self):
        # One account, no Account column in the older statement.
        hdr = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
               'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
        row = ('Trades,Data,Order,Stocks,CAD,SYN,"2025-03-03, 10:00:00",10,'
               '10,0,-100,-1,0,0,0,O\n')
        old = HEAD + _period('January 1, 2025 - June 30, 2025') + hdr + row
        new = (HEAD + _Y2025 + TRADES_H
               + _buy(_A, '2025-03-03, 10:00:00', comm=-1) + self._ca(_A))
        rows, err = self._run({'old.csv': old, 'new.csv': new})
        self.assertEqual(self._buys(rows), [], err)


def _parse(text):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'ib.csv'
        p.write_text(text, encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = IbBrokerage().parse_file(p)
    return ([t['symbol'] for t in txs if t['action'] == 'ADJUST'],
            'more than one listing' in err.getvalue())


_ROC_USD = ('Dividends,Data,USD,2025-01-10,"SYN(CA9990000001) Return of '
            'Capital USD 1 per Share",10\n')


def _op(cur, qty):
    return (f'Open Positions,Data,Summary,Stocks,{cur},SYN,{qty},1,10,'
            f'{qty * 10},10,{qty * 10},0,\n')


def _lot(when, qty, proceeds, code, cur='USD'):
    return _trade('SYN', when, qty, 10, proceeds, 0, code=code, cur=cur,
                  acct=_A)


class TestOpenShortIsNoEarlierHolding(unittest.TestCase):
    """TJ-1044: a coded short still open at the statement end is in the
    Open Positions but opened inside the statement; TJ-0660's rule (a
    coded short opening asks for no earlier holding) stays.

    Formerly: test_fix_1029_r7d_ib.TestOpenShortIsNoEarlierHolding"""

    def _text(self, trades, cad):
        return (HEAD + TRADES_H + _lot('2025-01-02, 10:00:00', 10, -100, 'O')
                + trades + _DIV_H + _ROC_USD + _OP_H + _op('USD', 10)
                + _op('CAD', cad))

    def test_short_open_at_the_end(self):
        got = _parse(self._text(
            _lot('2025-02-03, 10:00:00', -10, 100, 'O', 'CAD'), -10))
        self.assertEqual(got, (['SYN.US'], False))

    def test_short_partly_covered_at_the_end(self):
        got = _parse(self._text(
            _lot('2025-02-03, 10:00:00', -10, 100, 'O', 'CAD')
            + _lot('2025-02-04, 10:00:00', 4, -40, 'C', 'CAD'), -6))
        self.assertEqual(got, (['SYN.US'], False))

    def test_a_real_opening_before_the_short_still_counts(self):
        # 5 held before the statement, sold (coded C), then a short of
        # 10 stays open: the Toronto line WAS held on the payment date.
        syms, amb = _parse(self._text(
            _lot('2025-02-03, 10:00:00', -5, 50, 'C', 'CAD')
            + _lot('2025-02-04, 10:00:00', -10, 100, 'O', 'CAD'), -10))
        self.assertTrue(amb)
        self.assertEqual(len(syms), 1)


if __name__ == '__main__':
    unittest.main()
