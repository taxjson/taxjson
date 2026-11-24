"""Webull: every Type Code is read as its kind before it is checked
against the Security Description (backlog TJ-2388, TJ-5022).

The guard that keeps an option row from being booked as shares (and a
share row as an option) knew two codes, OPC and SHS. An OPP row whose
description is not a readable contract was one share of the underlying;
an STK or EQ row whose description read as a contract was that option.
The codes the Trading Summaries use are now read as their kinds (OPC,
OPP: an option; SHS, STK, EQ: shares), and a trade row with any other
code is refused, naming the line and the code.

Every security is made up (QZRX); the account number is synthetic.
"""
import contextlib
import io
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.webull import WebullBrokerage
from _tmpfiles import private_dir

HEAD = ("Account Number: 99900099\n"
        "Currency,Date,Action Code,Symbol,Security Description,Type Code,"
        "Quantity,Price,Proceeds\n")
CONTRACT = "QZRX270219C00045000.US"


def _row(symbol, desc, code, qty, price, proceeds, action="BUY"):
    return (f'USD,15-01-2025,{action},{symbol},{desc},{code},{qty},{price},'
            f'"{proceeds}"\n')


def _shares(code, desc="QZRX CORP"):
    return _row("QZRX", desc, code, 10, "12.00", "(121.00)")


def _option(code, desc="CALL QZRX02/19/27 45", symbol="@QZRX"):
    return _row(symbol, desc, code, 1, "2.50", "(251.00)")


def _parse(*rows):
    path = Path(private_dir()) / "webull_2025.csv"
    path.write_text(HEAD + "".join(rows), encoding="utf-8")
    sink = io.StringIO()
    with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
        return WebullBrokerage().parse_file(path)


def _facts(txs):
    return [(t["symbol"], t["quantity"], t["net_amount"]) for t in txs]


def _refusal(case, *rows):
    with case.assertRaises(BrokerageParseError) as cm:
        _parse(*rows)
    return str(cm.exception)


class TestAnOptionCodeNeedsAReadableContract(unittest.TestCase):

    def test_an_opp_row_with_an_unreadable_description_is_refused(self):
        # Base: one share of QZRX.US at a cost of 3.50.
        text = _refusal(self, _row("@QZRX", "QZRX PUT FEB 45", "OPP", 1,
                                   "2.50", "(3.50)"))
        self.assertIn("webull_2025.csv line 3:", text)
        self.assertIn("Type Code OPP (an option)", text)
        self.assertIn("is not a readable contract", text)

    def test_the_code_is_read_in_any_case(self):
        text = _refusal(self, _row("@QZRX", "QZRX PUT FEB 45", "opp", 1,
                                   "2.50", "(3.50)"))
        self.assertIn("Type Code OPP (an option)", text)

    def test_opc_is_refused_as_before(self):
        text = _refusal(self, _row("@QZRX", "QZRX CALL FEB 45", "OPC", 1,
                                   "2.50", "(3.50)"))
        self.assertIn("Type Code OPC (an option)", text)

    def test_an_opp_row_with_a_contract_is_that_option(self):
        self.assertEqual(
            _facts(_parse(_option("OPP", desc="PUT QZRX02/19/27 45"))),
            [("QZRX270219P00045000.US", 1.0, 251.0)])


class TestAShareCodeRefusesAContractDescription(unittest.TestCase):

    def test_stk_and_eq_rows_reading_as_a_contract_are_refused(self):
        # Base: each booked as the option QZRX270219C00045000.US.
        for code in ("STK", "EQ"):
            with self.subTest(code=code):
                text = _refusal(self, _option(code, symbol="QZRX"))
                self.assertIn("webull_2025.csv line 3:", text)
                self.assertIn(f"Type Code {code} (shares)", text)
                self.assertIn("reads as an option contract", text)

    def test_shs_is_refused_as_before(self):
        text = _refusal(self, _option("SHS", symbol="QZRX"))
        self.assertIn("Type Code SHS (shares)", text)


class TestACodeThatIsNoneOfThem(unittest.TestCase):

    def test_it_is_refused_naming_the_line_and_the_code(self):
        # Base: 10 shares of QZRX.US.
        text = _refusal(self, _shares("STK"), _shares("XYZ"))
        self.assertIn("webull_2025.csv line 4:", text)
        self.assertIn("Type Code 'XYZ'", text)
        self.assertIn("options: OPC, OPP; shares: EQ, SHS, STK", text)
        # The remedy that always works first, then the report, then
        # the typo.
        self.assertLess(text.index("enter the trade as a .tt BUYSELL line"),
                        text.index("please report the code 'XYZ'"))
        self.assertLess(text.index("please report the code 'XYZ'"),
                        text.index("if the cell is a typo"))

    def test_with_a_contract_description_too(self):
        text = _refusal(self, _option("XYZ"))
        self.assertIn("Type Code 'XYZ'", text)


class TestWhatDoesNotChange(unittest.TestCase):

    def test_share_rows_under_every_share_code(self):
        for code in ("SHS", "STK", "EQ", "eq"):
            with self.subTest(code=code):
                self.assertEqual(_facts(_parse(_shares(code))),
                                 [("QZRX.US", 10.0, 121.0)])

    def test_option_rows_under_opc_whichever_right(self):
        self.assertEqual(_facts(_parse(_option("OPC"))),
                         [(CONTRACT, 1.0, 251.0)])
        self.assertEqual(
            _facts(_parse(_option("OPC", desc="PUT QZRX02/19/27 45"))),
            [("QZRX270219P00045000.US", 1.0, 251.0)])

    def test_a_blank_code_is_read_by_the_description(self):
        self.assertEqual(_facts(_parse(_shares(""))),
                         [("QZRX.US", 10.0, 121.0)])
        self.assertEqual(_facts(_parse(_option(""))),
                         [(CONTRACT, 1.0, 251.0)])

    def test_a_row_that_is_no_trade_is_not_asked_for_its_code(self):
        # A DIV row carries a code of its own; it is reported unbooked,
        # as before, and the trade beside it is read.
        txs = _parse(_shares("STK"),
                     'USD,12-03-2025,DIV,QZRX,QZRX CORP,DIV,,,1.20\n')
        self.assertEqual(_facts(txs), [("QZRX.US", 10.0, 121.0)])


if __name__ == "__main__":
    unittest.main()
