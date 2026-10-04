"""Webull Trading Summary: BOTH export layouts, resolved by header label.

2024 layout: 9 columns, Proceeds in column 8.
2025 layout: 10 columns, an empty column 8, Proceeds in column 9, the
header repeated on every page, continuation rows with blank symbol and
description.
A position-based read of one layout on the other put the purchase amount
in the wrong field (purchases were booked at $0 cost). These tests pin the AMOUNTS of every row shape, the loud failure
on an unknown layout, and assignment/exercise detection.

Synthetic data only; account ids are fake (pii-ok).
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.webull import WebullBrokerage


def _wb():
    """Webull with a $1.00 exercise/assignment charge configured
    (`[accounts.X] exercise_fee = 1.00`): these fixtures carry that
    charge, and nothing is inferred without the setting (B11)."""
    p = WebullBrokerage()
    p.exercise_fee = 1.00
    return p

_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2024,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H24 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')
_H25 = _H24.replace('"Price\nPrix",', '"Price\nPrix",,')

F24 = (_PRE + _H24 +
       'USD,09-12-2024,BUY,@ZZQ,CALL ZZQ03/21/25 45,OPC,3,1.80,"(541.97)"\n'
       'USD,16-12-2024,BUY,,,,3,1.35,(406.98)\n'
       'USD,,,,,,,,\n'
       'USD,31-12-2024,BUY,ZZR,ZZR HOLDINGS INC CLASS A,SHS,160,28.25,"(4,520.00)"\n')
F25 = (_PRE.replace("2024", "2025") + _H25 +
       'USD,27-01-2025,SELL,ZZR,ZZR HOLDINGS INC CLASS A,SHS,-60,35.50,,"2,127.64"\n'
       'USD,10-02-2025,SELL,,,,-100,38.00,,"3,797.12"\n' + _H25 +
       'USD,10-07-2025,BUY,@ZZS,PUT ZZS07/18/25 240,OPC,1,0.00,,\n'
       'USD,03-07-2025,SELL,,,,-1,6.20,,619.35\n'
       'USD,11-07-2025,BUY,ZZS,ZZS INC,SHS,100,240.00,,"(24,001.00)"\n')


def _parse(text):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(text, encoding="utf-8")
        return _wb().parse_file(f)


class TestBothLayouts(unittest.TestCase):
    def test_2024_layout_amounts(self):
        tx = _parse(F24)
        self.assertEqual(len(tx), 3)
        opt = [t for t in tx if t["symbol"].startswith("ZZQ250321C")]
        self.assertEqual([t["quantity"] for t in opt], [3.0, 3.0])
        # The purchase amount is the NET, never 0 and never the fee.
        self.assertEqual([t["net_amount"] for t in opt], [541.97, 406.98])
        self.assertTrue(all(0 < t["fee"] < 5 for t in opt))
        stk = [t for t in tx if t["symbol"] == "ZZR.US"][0]
        self.assertEqual((stk["quantity"], stk["net_amount"]), (160.0, 4520.00))
        self.assertEqual(stk["date_settle"], "2024-12-31")

    def test_2025_layout_amounts_across_a_repeated_page_header(self):
        tx = _parse(F25)
        sells = [t for t in tx if t["symbol"] == "ZZR.US"]
        self.assertEqual([t["quantity"] for t in sells], [-60.0, -100.0])
        self.assertEqual([t["net_amount"] for t in sells], [2127.64, 3797.12])

    def test_unknown_layout_fails_loudly(self):
        bad = F24.replace("Proceeds of", "Montant")
        with self.assertRaises(ValueError) as cm:
            _parse(bad)
        self.assertIn("unrecognised Webull export layout", str(cm.exception))


class TestAssignmentDetection(unittest.TestCase):
    def test_short_put_assigned_is_two_assign_legs(self):
        tx = _parse(F25)
        opt = [t for t in tx if t["symbol"].startswith("ZZS250718P")]
        close = [t for t in opt if t["quantity"] > 0][0]
        stock = [t for t in tx if t["symbol"] == "ZZS.US"][0]
        self.assertEqual(close["action"], "ASSIGN")
        self.assertEqual(stock["action"], "ASSIGN")
        self.assertEqual(stock["date"], close["date"])
        self.assertGreater(stock["time"], close["time"])

    def test_zero_price_close_without_a_stock_leg_stays_an_expiry(self):
        tx = _parse(F25.replace('USD,11-07-2025,BUY,ZZS,ZZS INC,SHS,100,240.00,,"(24,001.00)"\n', ""))
        close = [t for t in tx if t["symbol"].startswith("ZZS250718P") and t["quantity"] > 0][0]
        self.assertEqual(close["action"], "BUYSELL")

    def test_stock_at_a_different_price_is_not_an_assignment(self):
        tx = _parse(F25.replace("100,240.00,,", "100,235.00,,"))
        self.assertFalse([t for t in tx if t["action"] == "ASSIGN"])


if __name__ == "__main__":
    unittest.main()
