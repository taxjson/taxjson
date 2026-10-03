"""Coinbase staking income is the SUBTOTAL (value received), not the
Total that adds back Coinbase's staking commission."""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from tax_rules import rule

CSV = ("Transactions\n"
       "User,Test User,00000000-0000-0000-0000-000000000000\n"
       "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,"
       "Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
       "aaaaaaaaaaaaaaaaaaaaaaaa,2025-07-16 08:21:37 UTC,Staking Income,ZZC,0.2,CAD,$200.00,$40.00,$60.00,$20.00,\n")


@rule("CA-INC-04")
class TestStakingCommission(unittest.TestCase):
    def test_income_and_cost_are_the_subtotal(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "cb_2025.csv"
            f.write_text(CSV)
            tx = CoinbaseBrokerage().parse_file(f)
        div = [t for t in tx if t["action"] == "DIVIDEND"][0]
        buy = [t for t in tx if t["action"] == "BUYSELL"][0]
        self.assertAlmostEqual(div["net_amount"], 40.00)   # not 60.00
        self.assertAlmostEqual(buy["net_amount"], 40.00)
        self.assertAlmostEqual(buy["quantity"], 0.2)


if __name__ == "__main__":
    unittest.main()
