"""Interactive Brokers rows the owner ruled on (2026-11-19; backlog
TJ-4569):

- (1) a Corporate Actions split carrying cash: refused (the cash was
  dropped with no word);
- (2) a share leg coded A (an assignment) at T. Price 0 and Proceeds 0:
  refused (it was booked as an ASSIGN at $0 cost, with no word: the
  zero-cost ATTENTION exempts A and Ex rows);
- (3) a cash takeover whose Proceeds is 0: booked at the description's
  rate, as before, and said: a Warning naming the row and the amount
  assumed;
- (4) a section under a Header row naming no columns: refused (its rows
  were read as blank, the instrument list lost, with no word);
- (5) KEPT and pinned: the same buy in two overlapping statements with
  different commissions is two trades (a refund in one of them folds
  into that one's buy only).

All data synthetic (made-up tickers QZA, QZAS, QZM, SYN; account ids
U1234567 and the like).
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.out import END_OF_RUN_MARK
from _support.ib_statement_rows import CA_H, HEAD, TRADES_H, _ca, _parse_ib, _trade
from _support.ib_account_ids import _A

_SPLIT = 'QZA(US9990000091) Split 2 for 1 (QZA, QZA CORP, US9990000091)'
_TAKEOVER = ('QZM(US9990000092) Merged(Acquisition) FOR USD 30.00 PER SHARE '
             '(QZM, QZM CORP, US9990000092)')
_FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,Description,'
          'Conid,Security ID,Listing Exch,Multiplier,Type,Code\n')


def _buy(sym, qty=100, price=10, comm=-1, when='2025-01-10, 10:00:00'):
    return _trade(sym, when, qty, price, -qty * price, comm=comm)


def _refused(test, text):
    with test.assertRaises(BrokerageParseError) as cm:
        _parse_ib(text)
    return str(cm.exception)


class TestASplitCarryingCash(unittest.TestCase):

    def test_refused(self):
        msg = _refused(self, HEAD + TRADES_H + _buy('QZA') + CA_H
                       + _ca(_SPLIT, 100, proceeds=50))
        self.assertTrue(msg.startswith(
            "ib.csv line 7 (Corporate Actions): the split carries cash (Proceeds 50.00 "
            "USD; "), msg)
        # The fix names the lines to write (review round 1: IB's wording
        # of a cash in lieu is unverified, so a real row may stop here).
        self.assertIn("refusing to guess what it is. Set this row's Proceeds to 0 (the "
                      "split at no cash), and write the cash as a line of its own in a "
                      ".tt file", msg)
        self.assertIn("`BUYSELL 2025-03-02 20:25:00 QZA.US -FRACTION USD PRICE 50.00` "
                      "(PRICE is the cash, 50.00, divided by the FRACTION)", msg)
        self.assertIn("`DIVIDEND 2025-03-02 20:25:00 QZA.US SHARES USD 0 50.00` "
                      "(SHARES: the shares it was paid on)", msg)

    def test_a_charge_is_offered_no_sale(self):
        # Review round 2: Proceeds below 0 was offered as a sale at +cash.
        msg = _refused(self, HEAD + TRADES_H + _buy('QZA') + CA_H
                       + _ca(_SPLIT, 100, proceeds=-10))
        self.assertTrue(msg.startswith(
            "ib.csv line 7 (Corporate Actions): the split carries a charge (Proceeds "
            "-10.00 USD; "), msg)
        self.assertIn("A split moves shares, not cash: the statement must explain what "
                      "the 10.00 USD paid for (no sale, no dividend). Set this row's "
                      "Proceeds to 0", msg)
        for word in ("BUYSELL", "DIVIDEND ", "FRACTION"):
            self.assertNotIn(word, msg)

    def test_with_no_cash_the_split_is_booked(self):
        _p, txs, _err = _parse_ib(HEAD + TRADES_H + _buy('QZA') + CA_H + _ca(_SPLIT, 100))
        self.assertEqual([(t['action'], t['quantity']) for t in txs if t['action'] == 'SPLIT'],
                         [('SPLIT', 2.0)])


class TestAnAssignmentShareLegAtZero(unittest.TestCase):

    def test_refused(self):
        msg = _refused(self, HEAD + TRADES_H
                       + _trade('QZAS', '2025-03-21, 16:20:00', 100, 0, 0, code='A;O'))
        self.assertTrue(msg.startswith(
            "ib.csv line 5 (Trades): QZAS.US: a share leg of +100 coded A;O (an "
            "assignment) at T. Price 0 and Proceeds 0 — an assignment's share leg is at "
            "the strike"), msg)
        self.assertIn("Fill the T. Price and Proceeds from the statement", msg)

    def test_a_share_leg_at_the_strike_is_booked(self):
        _p, txs, _err = _parse_ib(HEAD + TRADES_H + _trade(
            'QZAS', '2025-03-21, 16:20:00', 100, 30, -3000, code='A;O'))
        self.assertEqual([(t['symbol'], t['quantity'], t['net_amount']) for t in txs],
                         [('QZAS.US', 100.0, 3000.0)])


class TestACashTakeoverWithProceeds0(unittest.TestCase):

    def _takeover(self, proceeds):
        _p, txs, err = _parse_ib(HEAD + TRADES_H + _buy('QZM') + CA_H
                                 + _ca(_TAKEOVER, -100, proceeds=proceeds))
        return [t['net_amount'] for t in txs if t['quantity'] < 0], err

    def test_booked_at_the_descriptions_rate_and_said(self):
        sold, err = self._takeover(0)
        self.assertEqual(sold, [3000.0])
        # Marked for `tjs run`'s end-of-run list (lib/first_run.parser_warnings).
        self.assertIn("warning: ATTENTION: " + END_OF_RUN_MARK
                      + "ib.csv line 7 (Corporate Actions): QZM cash "
                      "takeover: Proceeds is 0; booked at the description's 30.00 x 100 = "
                      "3000.00 USD; check it against the cash received.", err)

    def test_stated_proceeds_are_booked_with_no_word(self):
        sold, err = self._takeover(2995)
        self.assertEqual(sold, [2995.0])
        self.assertNotIn("Proceeds is 0", err)


class TestAHeaderNamingNoColumns(unittest.TestCase):

    def test_refused(self):
        head = HEAD + TRADES_H + _buy('QZA') + _FII_H
        msg = _refused(self, head + 'Financial Instrument Information,Header\n'
                       + 'Financial Instrument Information,Data,Stocks,QZA,QZA CORP,'
                         '99900011,US9990000091,NASDAQ,1,COMMON,\n')
        # The header row's line is named (review round 1).
        line = head.count('\n') + 1
        self.assertEqual(msg, f"ib.csv line {line}: the header row of section "
                              "'Financial Instrument Information' names no columns; the "
                              "section cannot be read. Download the statement again.")

    def test_trailing_empty_cells_name_no_columns_either(self):
        self.assertIn("the header row of section 'Trades' names no columns",
                      _refused(self, HEAD + 'Trades,Header,,\n' + _buy('QZA')))


_ADJ_H = 'Commission Adjustments,Header,Currency,Date,Description,Amount\n'


class TestOneBuyInTwoStatementsWithTwoCommissions(unittest.TestCase):
    """(5): two rows that differ in a money cell are two trades."""

    def test_both_buys_are_kept(self):
        def buy(comm):
            return _trade('SYN', '2025-03-03, 10:00:00', 10, 10, -100, comm, cur='CAD',
                          acct=_A)
        files = {
            'a.csv': (HEAD + TRADES_H + buy(-1) + _ADJ_H
                      + 'Commission Adjustments,Data,CAD,2025-04-03,'
                        '"Refund (SYN, 10, 2025-03-03)",0.5\n'),
            'b.csv': HEAD + TRADES_H + buy(-2),
        }
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for name, text in files.items():
                (Path(td) / name).write_text(text)
                paths.append(str(Path(td) / name))
            r = subprocess.run(
                python_module_argv('taxjson.bin.taxjson_brokerage',
                                   ['--brokerage', 'ib', '--country', 'canada',
                                    '--account', 'margin', '--account-type', 'taxable',
                                    *paths]),
                capture_output=True, text=True, env=dict(os.environ, TAXJSON_WIDTH='0'))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)['transactions']
        self.assertEqual(sorted(t['fee'] for t in rows if t['action'] == 'BUYSELL'),
                         [0.5, 2.0], r.stderr)


if __name__ == "__main__":
    unittest.main()
