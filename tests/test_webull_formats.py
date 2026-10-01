"""Webull Trading Summary: BOTH export layouts, resolved by header label.

2024 layout: 9 columns, Proceeds in column 8.
2025 layout: 10 columns, an empty column 8, Proceeds in column 9, the
header repeated on every page, continuation rows with blank symbol and
description.
A position-based read of one layout on the other put the purchase amount
in the wrong field (a filed return booked 15 Webull 2024 purchases at $0
cost). These tests pin the AMOUNTS of every row shape, the loud failure
on an unknown layout, and assignment/exercise detection.

Synthetic data only; account ids are fake (pii-ok).
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.webull import WebullBrokerage

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
       'USD,02-12-2024,BUY,@ZZQ,CALL ZZQ01/17/25 50,OPC,2,3.10,"(621.97)"\n'
       'USD,18-12-2024,BUY,,,,2,2.20,(441.98)\n'
       'USD,,,,,,,,\n'
       'USD,31-12-2024,BUY,ZZR,ZZR HOLDINGS INC CLASS A,SHS,140,38.49,"(5,388.60)"\n')
F25 = (_PRE.replace("2024", "2025") + _H25 +
       'USD,22-01-2025,SELL,ZZR,ZZR HOLDINGS INC CLASS A,SHS,-40,48.41,,"1,933.36"\n'
       'USD,03-02-2025,SELL,,,,-100,52.40,,"5,236.86"\n' + _H25 +
       'USD,12-06-2025,BUY,@ZZS,PUT ZZS06/20/25 305,OPC,1,0.00,,\n'
       'USD,05-06-2025,SELL,,,,-1,8.00,,799.35\n'
       'USD,13-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n')


def _parse(text):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(text, encoding="utf-8")
        return WebullBrokerage().parse_file(f)


class TestBothLayouts(unittest.TestCase):
    def test_2024_layout_amounts(self):
        tx = _parse(F24)
        self.assertEqual(len(tx), 3)
        opt = [t for t in tx if t["symbol"].startswith("ZZQ250117C")]
        self.assertEqual([t["quantity"] for t in opt], [2.0, 2.0])
        # The purchase amount is the NET, never 0 and never the fee.
        self.assertEqual([t["net_amount"] for t in opt], [621.97, 441.98])
        self.assertTrue(all(0 < t["fee"] < 5 for t in opt))
        stk = [t for t in tx if t["symbol"] == "ZZR.US"][0]
        self.assertEqual((stk["quantity"], stk["net_amount"]), (140.0, 5388.60))
        self.assertEqual(stk["date_settle"], "2024-12-31")

    def test_2025_layout_amounts_across_a_repeated_page_header(self):
        tx = _parse(F25)
        sells = [t for t in tx if t["symbol"] == "ZZR.US"]
        self.assertEqual([t["quantity"] for t in sells], [-40.0, -100.0])
        self.assertEqual([t["net_amount"] for t in sells], [1933.36, 5236.86])

    def test_unknown_layout_fails_loudly(self):
        bad = F24.replace("Proceeds of", "Montant")
        with self.assertRaises(ValueError) as cm:
            _parse(bad)
        self.assertIn("unrecognised Webull export layout", str(cm.exception))


class TestAssignmentDetection(unittest.TestCase):
    def test_short_put_assigned_is_two_assign_legs(self):
        tx = _parse(F25)
        opt = [t for t in tx if t["symbol"].startswith("ZZS250620P")]
        close = [t for t in opt if t["quantity"] > 0][0]
        stock = [t for t in tx if t["symbol"] == "ZZS.US"][0]
        self.assertEqual(close["action"], "ASSIGN")
        self.assertEqual(stock["action"], "ASSIGN")
        self.assertEqual(stock["date"], close["date"])
        self.assertGreater(stock["time"], close["time"])

    def test_zero_price_close_without_a_stock_leg_stays_an_expiry(self):
        tx = _parse(F25.replace('USD,13-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n', ""))
        close = [t for t in tx if t["symbol"].startswith("ZZS250620P") and t["quantity"] > 0][0]
        self.assertEqual(close["action"], "BUYSELL")

    def test_stock_at_a_different_price_is_not_an_assignment(self):
        tx = _parse(F25.replace("100,305.00,,", "100,300.00,,"))
        self.assertFalse([t for t in tx if t["action"] == "ASSIGN"])


if __name__ == "__main__":
    unittest.main()
