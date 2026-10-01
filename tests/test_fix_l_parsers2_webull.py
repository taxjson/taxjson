"""Low-round parsers2 fixes: Webull Trading Summary.

R1-100 (a sale's debit Proceeds kept its sign), S065-17 (Type Code
decides option vs shares), S065-22 (skip warning wording), S066-01
(s.49(3.1) for puts), S066-03 (long call exercised by a $0 SELL).
Synthetic data only; account ids are fake (pii-ok).
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.webull import WebullBrokerage

_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2025,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H25 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",,'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')


def _parse(rows):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(_PRE + _H25 + rows, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = WebullBrokerage().parse_file(f)
        return tx, err.getvalue()


class TestSaleDebitKeepsItsSign(unittest.TestCase):
    """R1-100: 'SELL ... -2 @ 0.00, (1.50)' is a close whose commission
    exceeded the gross — negative proceeds, not +1.50 received."""

    _BUY = 'USD,19-02-2025,BUY,@QQQ,CALL QQQ02/21/25 530,OPC,2,4.35,,(871.98)\n'

    def test_debit_on_a_sale_is_negative_proceeds(self):
        tx, _ = _parse(self._BUY +
                       'USD,21-02-2025,SELL,,,,-2,0.00,,(1.50)\n')
        sale = [t for t in tx if t["quantity"] < 0][0]
        self.assertAlmostEqual(sale["net_amount"], -1.50)
        self.assertAlmostEqual(sale["fee"], 1.50)

    def test_credit_on_a_sale_unchanged(self):
        tx, _ = _parse(self._BUY +
                       'USD,21-02-2025,SELL,,,,-2,0.10,,18.50\n')
        sale = [t for t in tx if t["quantity"] < 0][0]
        self.assertAlmostEqual(sale["net_amount"], 18.50)

    def test_zero_close_is_still_an_expiry(self):
        tx, _ = _parse(self._BUY + 'USD,21-02-2025,SELL,,,,-2,0.00,,\n')
        sale = [t for t in tx if t["quantity"] < 0][0]
        self.assertEqual(sale["net_amount"], 0.0)
        self.assertEqual(sale["time"], "16:00:00")

    def test_buy_with_a_credit_is_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse('USD,10-03-2025,BUY,ABC,ABC CORP,SHS,10,5.00,,51.00\n')
        self.assertIn("positive", str(cm.exception))


class TestTypeCodeDecides(unittest.TestCase):
    """S065-17."""

    def test_opc_row_with_unreadable_description_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse('USD,10-03-2025,BUY,@ABC,,OPC,1,2.00,,(200.99)\n')
        self.assertIn("OPC", str(cm.exception))

    def test_shs_row_with_contract_description_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse('USD,10-03-2025,BUY,ABC,CALL ABC06/20/25 50,SHS,1,'
                   '2.00,,(2.99)\n')
        self.assertIn("SHS", str(cm.exception))

    def test_continuation_rows_carry_the_type(self):
        tx, _ = _parse(
            'USD,10-03-2025,BUY,@ABC,CALL ABC06/20/25 50,OPC,1,2.00,,(200.99)\n'
            'USD,11-03-2025,BUY,,,,1,2.10,,(210.99)\n')
        self.assertEqual({t["symbol"] for t in tx},
                         {"ABC250620C00050000.US"})


class TestSkipWarningWording(unittest.TestCase):
    """S065-22: expiry and assignment ARE booked; only the other action
    codes are skipped."""

    def test_warning_names_only_the_skipped_codes(self):
        _, err = _parse(
            'USD,10-03-2025,BUY,ABC,ABC CORP,SHS,10,5.00,,(51.00)\n'
            'USD,12-03-2025,DIV,ABC,ABC CORP,SHS,,,,1.00\n')
        self.assertIn("1× DIV", err)
        self.assertNotIn("assignment/expiry are", err)
        self.assertNotIn("expiry are NOT booked", err)


class TestExerciseCitations(unittest.TestCase):
    """S066-01 / S066-03."""

    def test_long_call_exercise_pairs_and_cites_s49_3(self):
        # A long call closed by 'SELL ... -N' at $0 plus N*100 shares
        # bought at the strike with Webull's $1.00 charge: an exercise.
        tx, err = _parse(
            'USD,20-02-2025,BUY,@DLX,CALL DLX02/28/25 102,OPC,4,3.00,,(1203.99)\n'
            'USD,28-02-2025,SELL,,,,-4,0.00,,\n'
            'USD,04-03-2025,BUY,DLX,DLX CORP,SHS,400,102.00,,"(40,801.00)"\n')
        legs = [t for t in tx if t["action"] == "ASSIGN"]
        self.assertEqual(len(legs), 2, err)
        opt = [t for t in legs if "C00102000" in t["symbol"]][0]
        self.assertEqual(opt["quantity"], -4.0)
        stock = [t for t in legs if t["symbol"] == "DLX.US"][0]
        self.assertEqual(stock["quantity"], 400.0)
        self.assertIn("s.49(3))", err)

    def test_put_assignment_cites_s49_3_1(self):
        _, err = _parse(
            'USD,05-06-2025,SELL,@ZZS,PUT ZZS06/20/25 305,OPC,-1,8.00,,799.35\n'
            'USD,20-06-2025,BUY,,,,1,0.00,,\n'
            'USD,23-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n')
        self.assertIn("s.49(3.1)", err)


if __name__ == "__main__":
    unittest.main()
