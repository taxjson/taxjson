"""A row the parser warns UNBOOKED about is the reported class: counted,
and not listed again in the skip summary as unclassified (TJ-2377,
TJ-2378, TJ-2400, TJ-2401). Questrade's warning helper answers the
row's Outcome itself; RBC's classify and build answer it beside the
warning."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage

_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,"
            "Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type")
_QT_BUY = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,SAMPLF,SAMPLF CORP,10,9.00,"
           "-90.00,-4.95,-94.95,CAD,12345,Trades,Individual")
_RBC_HEAD = "Date,Activity,Symbol,Description,Quantity,Price,Settlement Date,Currency,Value,Amount"
_RBC_BUY = "01/12/2024,Buy,SAMPLF,SAMPLF CORP - Buy,10,9.00,01/15/2024,CAD,-90.00,-94.95"


def _parse(parser, head, *rows):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "export.csv"
        f.write_text("\n".join((head,) + rows) + "\n", encoding="utf-8")
        with redirect_stderr(io.StringIO()) as err:
            parser.parse_file(f)
    return parser.skip_classes(), err.getvalue()


class TestQuestrade(unittest.TestCase):
    def test_an_unknown_action_that_moves_shares(self):
        (_known, reported, rest), err = _parse(QuestradeBrokerage(), _QT_HEAD, _QT_BUY, (
            "2025-04-01 12:00:00 AM,2025-04-02 12:00:00 AM,ZZQ,SAMPLF,SAMPLF CORP,5,0.00,"
            "0.00,0.00,0.00,CAD,12345,Trades,Individual"))
        self.assertIn("UNBOOKED", err)
        self.assertEqual(reported, {"action ZZQ": 1})
        self.assertEqual(rest, {})

    def test_an_unknown_action_that_moves_nothing_stays_unclassified(self):
        (_known, reported, rest), _err = _parse(QuestradeBrokerage(), _QT_HEAD, _QT_BUY, (
            "2025-04-01 12:00:00 AM,2025-04-02 12:00:00 AM,ZZQ,SAMPLF,SAMPLF CORP,0,0.00,"
            "0.00,0.00,0.00,CAD,12345,Trades,Individual"))
        self.assertEqual((reported, rest), ({}, {"action ZZQ": 1}))


class TestRbc(unittest.TestCase):
    def test_an_unclassified_row_that_moves_shares(self):
        (_known, reported, rest), err = _parse(RbcBrokerage(), _RBC_HEAD, _RBC_BUY,
                                               "07/15/2024,Fancy New Thing,SAMPLF,SOMETHING NEW,5,"
                                               "0.00,07/15/2024,CAD,0.00,0.00")
        self.assertIn("UNBOOKED", err)
        self.assertEqual(reported, {"activity Fancy New Thing": 1})
        self.assertEqual(rest, {})

    def test_a_split_row_without_a_quantity(self):
        (_known, reported, _rest), err = _parse(RbcBrokerage(), _RBC_HEAD, _RBC_BUY,
                                                "04/15/2024,Reorganization,SAMPLF,DIS - SAMPLF CORP "
                                                "STK SPLIT ON 100 SHS,0,,04/15/2024,CAD,0,0")
        self.assertIn("stock-split row", err)
        self.assertEqual(reported, {"stock-split row not understood (see warning)": 1})


if __name__ == "__main__":
    unittest.main()
