"""Webull Trading Summary medium-round fixes (parsers2 area).

Synthetic data only; account ids are fake (pii-ok).
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.webull import WebullBrokerage

_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2025,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H24 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')
_H25 = _H24.replace('"Price\nPrix",', '"Price\nPrix",,')


def _parse(text, name="wb.csv"):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / name
        f.write_text(text, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = WebullBrokerage().parse_file(f)
        return tx, err.getvalue()


def _parse_folder(files, which):
    """Parse `which` from a folder holding every file in `files`."""
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = WebullBrokerage().parse_file(Path(td) / which)
        return tx, err.getvalue()


# A long call that expired worthless, then an ordinary limit buy of 100
# shares AT THE STRIKE four days later. {fee} picks the stock leg's
# commission: Webull's exercise/assignment signature is $1.00; ordinary
# stock trades carry the regular commission.
_LONGCALL = (_PRE + _H25 +
             'USD,20-03-2025,BUY,@XYZ,CALL XYZ03/21/25 50,OPC,1,1.00,,(100.99)\n'
             'USD,21-03-2025,SELL,,,,-1,0.00,,\n'
             'USD,25-03-2025,BUY,XYZ,XYZ CORP,SHS,100,50.00,,"({net})"\n')


@rule("CA-OPT-06")
@rule("US-OPT-02")
class TestAssignmentHeuristic(unittest.TestCase):
    """R1-15 / R1-94 / R1-175: an expiry + an unrelated trade at the
    strike must not silently become an exercise."""

    def test_ordinary_commission_trade_at_strike_stays_an_expiry(self):
        tx, err = _parse(_LONGCALL.format(net="5,002.99"))
        self.assertFalse([t for t in tx if t["action"] == "ASSIGN"])
        stock = [t for t in tx if t["symbol"] == "XYZ.US"][0]
        # Own (back-computed) trade date, not the option's date.
        self.assertEqual(stock["date"], "2025-03-24")
        self.assertIn("NOT inferred", err)
        self.assertIn("XYZ.US", err)

    def test_zero_commission_trade_at_strike_stays_an_expiry(self):
        tx, err = _parse(_LONGCALL.format(net="5,000.00"))
        self.assertFalse([t for t in tx if t["action"] == "ASSIGN"])
        self.assertIn("NOT inferred", err)

    def test_exercise_fee_signature_pairs_and_names_the_pairing(self):
        tx, err = _parse(_LONGCALL.format(net="5,001.00"))
        assign = [t for t in tx if t["action"] == "ASSIGN"]
        self.assertEqual(len(assign), 2)
        self.assertIn("inferred", err)
        self.assertIn("XYZ250321C00050000.US", err)


@rule("CA-OPT-06")
@rule("US-OPT-02")
class TestAssignmentPairingOrder(unittest.TestCase):
    """S066-12: the option CLOSEST to the stock leg wins, whatever the
    row order."""

    ROWS = (_PRE + _H25 +
            'USD,05-12-2025,SELL,@QZX,PUT QZX12/12/25 60,OPC,-1,1.00,,99.00\n'
            'USD,12-12-2025,BUY,,,,1,0.00,,\n'
            'USD,09-12-2025,SELL,@QZX,PUT QZX01/16/26 60,OPC,-1,2.50,,249.00\n'
            'USD,15-12-2025,BUY,,,,1,0.00,,\n'
            'USD,16-12-2025,BUY,QZX,QZX INC,SHS,100,60.00,,"(6,001.00)"\n')

    def test_expired_put_listed_first_does_not_claim_the_stock_leg(self):
        tx, _ = _parse(self.ROWS)
        closes = {t["symbol"]: t for t in tx if t["quantity"] > 0
                  and t["symbol"].startswith("QZX2")}
        self.assertEqual(closes["QZX251212P00060000.US"]["action"],
                         "BUYSELL")
        self.assertEqual(closes["QZX260116P00060000.US"]["action"],
                         "ASSIGN")
        stock = [t for t in tx if t["symbol"] == "QZX.US"][0]
        self.assertEqual(stock["action"], "ASSIGN")
        self.assertEqual(stock["date"], "2025-12-15")


@rule("CA-OPT-06")
@rule("US-OPT-02")
class TestAssignmentUnderlyingFromSymbolColumn(unittest.TestCase):
    """S066-02: pair by the row's own @Symbol, not the description
    root (an adjusted contract's root ZZS1 differs from the ticker)."""

    def test_adjusted_root_still_pairs(self):
        rows = (_PRE + _H25 +
                'USD,05-06-2025,SELL,@ZZS,PUT ZZS1 06/20/25 305,OPC,-1,8.00,,799.35\n'
                'USD,20-06-2025,BUY,,,,1,0.00,,\n'
                'USD,23-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n')
        tx, _ = _parse(rows)
        self.assertEqual(sorted(t["action"] for t in tx),
                         ["ASSIGN", "ASSIGN", "BUYSELL"])
        self.assertFalse(any("_under" in k for t in tx for k in t))


    def test_contract_code_in_symbol_column_falls_back_to_description(self):
        # Only `@ROOT` names the underlying; a contract code in the
        # Symbol column must not block the pairing.
        rows = (_PRE + _H25 +
                'USD,05-06-2025,SELL,ZZS250620P00305000,PUT ZZS06/20/25 305,OPC,-1,8.00,,799.35\n'
                'USD,20-06-2025,BUY,,,,1,0.00,,\n'
                'USD,23-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n')
        tx, _ = _parse(rows)
        self.assertEqual(sorted(t["action"] for t in tx),
                         ["ASSIGN", "ASSIGN", "BUYSELL"])


@rule("CA-OPT-06")
@rule("US-OPT-02")
class TestAssignmentAcrossFiles(unittest.TestCase):
    """S065-24: a Dec-31 assignment whose stock leg settles in January
    sits in the NEXT year's settlement-dated file."""

    F24 = (_PRE.replace("2025", "2024") + _H24 +
           'USD,20-12-2024,SELL,@QZW,PUT QZW12/31/24 50,OPC,-1,2.00,199.00\n'
           'USD,31-12-2024,BUY,,,,1,0.00,\n')
    F25 = (_PRE + _H25 +
           'USD,02-01-2025,BUY,QZW,QZW INC,SHS,100,50.00,,"(5,001.00)"\n')

    def test_both_files_see_the_pair(self):
        files = {"wb_2024.csv": self.F24, "wb_2025.csv": self.F25}
        tx24, _ = _parse_folder(files, "wb_2024.csv")
        tx25, _ = _parse_folder(files, "wb_2025.csv")
        close = [t for t in tx24 if t["quantity"] > 0][0]
        self.assertEqual(close["action"], "ASSIGN")
        self.assertEqual(len(tx25), 1)
        self.assertEqual(tx25[0]["action"], "ASSIGN")
        self.assertEqual(tx25[0]["date"], "2024-12-31")
        self.assertEqual(tx25[0]["date_settle"], "2025-01-02")

    def test_non_webull_sibling_is_ignored(self):
        files = {"wb_2025.csv": self.F25, "notes.csv": "a,b,c\n1,2,3\n"}
        tx25, _ = _parse_folder(files, "wb_2025.csv")
        self.assertEqual(tx25[0]["action"], "BUYSELL")


_TRADES = (_PRE + _H25 +
           'USD,10-03-2025,BUY,QQQX,QQQX CORP,SHS,100,50.00,,"(5,002.99)"\n'
           '{cur},12-05-2025,SELL,QQQX,QQQX CORP,SHS,-100,55.00,,"5,497.01"\n')


class TestTradeRowGate(unittest.TestCase):
    """R1-92 / R1-97: a trade row is booked or refused — never dropped
    because of its currency, date or action spelling."""

    def test_blank_currency_trade_row_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_TRADES.format(cur=""))
        self.assertIn("currency", str(cm.exception))

    def test_unknown_currency_trade_row_refused(self):
        with self.assertRaises(BrokerageParseError):
            _parse(_TRADES.format(cur="HKD"))

    def test_lowercase_currency_is_read(self):
        tx, _ = _parse(_TRADES.format(cur="usd"))
        self.assertEqual(len(tx), 2)
        self.assertEqual(tx[1]["currency"], "USD")

    def test_blank_date_trade_row_refused(self):
        text = _TRADES.format(cur="USD").replace("12-05-2025", "")
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(text)
        self.assertIn("date", str(cm.exception).lower())

    def test_mixed_case_action_is_booked(self):
        text = _TRADES.format(cur="USD").replace(",SELL,", ",Sell,")
        tx, _ = _parse(text)
        self.assertEqual([t["quantity"] for t in tx], [100.0, -100.0])


class TestAmbiguousHeader(unittest.TestCase):
    """R1-98: a second label containing a field's needle is refused."""

    def test_extra_proceeds_column_refused(self):
        text = (_PRE + _H24.replace('"Proceeds of',
                                    '"Gross Proceeds",' + '"Proceeds of') +
                'USD,10-03-2025,BUY,QQQX,QQQX CORP,SHS,100,50.00,9999.99,'
                '"(5,002.99)"\n')
        with self.assertRaises(ValueError) as cm:
            _parse(text)
        self.assertIn("ambiguous", str(cm.exception))

    def test_price_currency_column_refused(self):
        text = (_PRE + _H24.replace('"Price\nPrix"',
                                    '"Price Currency","Price\nPrix"') +
                'USD,10-03-2025,BUY,QQQX,QQQX CORP,SHS,100,USD,50.00,'
                '"(5,002.99)"\n')
        with self.assertRaises(ValueError):
            _parse(text)


class TestMalformedRows(unittest.TestCase):
    def test_row_cut_after_quantity_refused(self):
        # S017-09 (already refused on main by the R1-91 width check).
        text = (_PRE + _H25 +
                'USD,21-03-2025,SELL,NVDA,NVIDIA CORP,STK,6\n')
        with self.assertRaises(BrokerageParseError):
            _parse(text)

    def test_unescaped_quote_swallowing_rows_refused(self):
        # S066-04: a Description with an unclosed quote swallows the
        # rest of the row and the following rows.
        text = (_PRE + _H24 +
                'USD,10-03-2025,SELL,ABC,"ABC CORP "",SHS,-100,10.00,999.00\n'
                'USD,10-03-2025,SELL,DEF,"DEF CORP",SHS,-50,20.00,999.00\n'
                'USD,11-03-2025,BUY,GHI,"GHI, INC",SHS,10,5.00,(50.99)\n')
        with self.assertRaises(BrokerageParseError):
            _parse(text)


class TestTickerChangeWarning(unittest.TestCase):
    """S066-10: two symbols sharing one Security Description, the
    second opening with a sale — a likely ticker change."""

    def test_rename_candidate_warns(self):
        text = (_PRE + _H25 +
                'USD,10-03-2025,BUY,QQOL,QQ HOLDINGS CORP,SHS,500,10.00,,"(5,002.99)"\n'
                'USD,12-06-2025,SELL,QQNW,QQ HOLDINGS CORP,SHS,-500,20.00,,"9,997.01"\n')
        tx, err = _parse(text)
        self.assertEqual(len(tx), 2)
        self.assertIn("ticker.map", err)
        self.assertIn("QQOL.US", err)
        self.assertIn("QQNW.US", err)

    def test_distinct_descriptions_are_quiet(self):
        text = (_PRE + _H25 +
                'USD,10-03-2025,BUY,QQOL,QQ HOLDINGS CORP,SHS,500,10.00,,"(5,002.99)"\n'
                'USD,12-06-2025,SELL,QQOL,QQ HOLDINGS CORP,SHS,-500,20.00,,"9,997.01"\n')
        _tx, err = _parse(text)
        self.assertNotIn("ticker.map", err)


if __name__ == "__main__":
    unittest.main()


class TestTradeMoneyIdentity(unittest.TestCase):
    """S023-19 (Webull half): a Proceeds cell that does not fit
    |qty| x price (+/- a commission) is refused, as Questrade, IB and
    RBC refuse it — a shifted or mislabelled column used to book with
    only a schema warning."""

    def test_proceeds_off_by_ten_refused(self):
        text = (_PRE + _H25 +
                'USD,10-03-2025,BUY,AAPL,APPLE INC,SHS,100,185.00,,"(1,850.00)"\n')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(text)
        self.assertIn("does not fit", str(cm.exception))

    def test_sale_netting_more_than_its_gross_refused(self):
        text = (_PRE + _H25 +
                'USD,10-03-2025,SELL,AAPL,APPLE INC,SHS,-100,185.00,,"19,500.00"\n')
        with self.assertRaises(BrokerageParseError):
            _parse(text)

    def test_normal_commissions_accepted(self):
        text = (_PRE + _H25 +
                'USD,10-03-2025,BUY,AAPL,APPLE INC,SHS,100,185.00,,"(18,503.99)"\n'
                'USD,11-03-2025,BUY,@ZZQ,CALL ZZQ01/17/26 50,OPC,2,3.10,,"(621.97)"\n')
        tx, _ = _parse(text)
        self.assertEqual(len(tx), 2)
