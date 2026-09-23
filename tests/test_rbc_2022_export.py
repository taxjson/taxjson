"""The 2022-vintage RBC Direct export (plain `Date,Activity,...` header,
Date as 1/6/2022 and Settlement Date as 10-Jan-22) parses with its own
settlement dates. Before the `%d-%b-%y` format landed, every 2022 settle
date silently fell back to the trade date.

Synthetic account id 55500001 — pii-ok.
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage

_CSV = """Date,Activity,Symbol,Symbol Description,Quantity,Price,Settlement Date,Account,Value,Currency,Description
3/15/2022,Dividends,PEY,PEYTO EXPLORATION AND DEVELOPMENT CORP,,,15-Mar-22,55500001,66,CAD,DIV - PEYTO EXPLORATION AND DEVELOPMENT CORP CASH DIV  ON     600 SHS REC 02/28/22 PAY 03/15/22
1/6/2022,Sell,VT,VANGUARD TOTAL WORLD STOCK ETF,-950,106.054,10-Jan-22,55500001,100740.83,USD,VANGUARD TOTAL WORLD STOCK ETF UNSOLICITED CA
12/30/2022,Buy,XEI,ISHARES S&P/TSX COMPOSITE HIGH DIV,100,24.10,4-Jan-23,55500001,-2419.95,CAD,ISHARES S&P/TSX COMPOSITE HIGH DIV
"""


class TestRbc2022Export(unittest.TestCase):
    def test_settlement_dates_parse(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "activity_rbc_2022.csv"
            f.write_text(_CSV)
            rows = RbcBrokerage().parse_file(f)
        by_sym = {r["symbol"].split(".")[0]: r for r in rows
                  if r.get("action") == "BUYSELL"}
        self.assertEqual(by_sym["VT"]["date"], "2022-01-06")
        self.assertEqual(by_sym["VT"]["date_settle"], "2022-01-10")
        # A year-straddling settle: trade 2022, settles 2023.
        self.assertEqual(by_sym["XEI"]["date"], "2022-12-30")
        self.assertEqual(by_sym["XEI"]["date_settle"], "2023-01-04")

    def test_exploration_dividend_is_not_a_trade(self):
        # "EXP" inside EXPLORATION used to make the dividend row trade-like
        # (a 0-quantity BUYSELL that failed validation and lost the income).
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "activity_rbc_2022.csv"
            f.write_text(_CSV)
            rows = RbcBrokerage().parse_file(f)
        pey = [r for r in rows if r["symbol"].startswith("PEY")]
        self.assertEqual([r["action"] for r in pey], ["DIVIDEND"])
        self.assertNotIn("BUYSELL", {r["action"] for r in pey})


if __name__ == "__main__":
    unittest.main()
