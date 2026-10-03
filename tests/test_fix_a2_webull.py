"""Re-audit-2 fixes: Webull Trading Summary parser (parsers-webull list).

Synthetic data only; account ids are fake (pii-ok).
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.webull import WebullBrokerage

from tax_rules import rule

_PRE = (",,,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2025,,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,,\n")
_H25 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",,'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')


def _write(td, name, rows, pre=_PRE, encoding="utf-8"):
    f = Path(td) / name
    f.write_bytes((pre + _H25 + rows).encode(encoding))
    return f


def _parse(rows, pre=_PRE, encoding="utf-8", name="wb.csv"):
    with tempfile.TemporaryDirectory() as td:
        f = _write(td, name, rows, pre, encoding)
        err = io.StringIO()
        ex = WebullBrokerage()
        with contextlib.redirect_stderr(err):
            tx = ex.parse_file(f)
        return tx, err.getvalue(), ex


def _parse_folder(files, which):
    with tempfile.TemporaryDirectory() as td:
        for name, (pre, rows) in files.items():
            _write(td, name, rows, pre)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = WebullBrokerage().parse_file(Path(td) / which)
        return tx, err.getvalue()


_BUY = 'CAD,12-12-2024,BUY,SHOP,SHOPIFY INC,SHS,30,95.00,,"(2,854.95)"\n'


class TestCutLastRow(unittest.TestCase):
    """A2-0028: a last row cut short is refused, never dropped."""

    def test_cut_points_in_the_first_cells_are_refused(self):
        full = 'CAD,13-12-2024,BUY,SHOP,SHOPIFY INC,SHS,30,95.00,,"(2,854.95)"'
        for n in (1, 3, 4, 8, 14, 15, 16, 20):
            with self.subTest(cut=full[:n]):
                with self.assertRaises(BrokerageParseError) as cm:
                    _parse(_BUY + full[:n] + "\n")
                self.assertIn("cut short", str(cm.exception))

    def test_complete_file_unchanged(self):
        tx, err, _ = _parse(_BUY)
        self.assertEqual(len(tx), 1)


class TestUtf16(unittest.TestCase):
    """A2-0101 / A2-1064 / A2-1066 / A2-1451: a UTF-16 export parses."""

    def test_webull_utf16_parses_like_utf8(self):
        a, _, _ = _parse(_BUY)
        b, _, _ = _parse(_BUY, encoding="utf-16")
        self.assertEqual(a, b)

    def test_kraken_and_coinbase_utf16(self):
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        root = Path(__file__).resolve().parent.parent / "examples"
        for cls, name in ((KrakenBrokerage, "kraken_demo.csv"),
                          (CoinbaseBrokerage, "coinbase_demo.csv")):
            src = root / name
            if not src.exists():
                continue
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                u8 = Path(td) / ("u8_" + name)
                u16 = Path(td) / ("u16_" + name)
                text = src.read_text(encoding="utf-8-sig")
                u8.write_text(text, encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()):
                    want = cls().parse_file(u8)
                u8.unlink()
                u16.write_bytes(text.encode("utf-16"))
                with contextlib.redirect_stderr(io.StringIO()):
                    got = cls().parse_file(u16)
                self.assertEqual(len(got), len(want))
                self.assertTrue(got)


    def test_ib_corporate_actions_reader_utf16(self):
        from taxjson.lib.corp_actions import _read_ib_corporate_actions
        src = Path(__file__).resolve().parent.parent / "examples" / "ib_demo.csv"
        text = src.read_text(encoding="utf-8-sig")
        with tempfile.TemporaryDirectory() as td:
            u16 = Path(td) / "ib.csv"
            u16.write_bytes(text.encode("utf-16"))
            got = _read_ib_corporate_actions(u16)
            u8 = Path(td) / "ib8.csv"
            u8.write_text(text, encoding="utf-8")
            want = _read_ib_corporate_actions(u8)
        self.assertEqual(repr(got).replace("ib8.csv", "ib.csv"),
                         repr(want).replace("ib8.csv", "ib.csv"))


class TestZeroMoneyRows(unittest.TestCase):
    """A2-0102 / A2-0284 / A2-0619: blank or zero Price and Proceeds are
    accepted only on an option close at expiry (or a paired
    assignment)."""

    def test_stock_sell_with_blank_price_and_proceeds_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse('CAD,10-03-2025,BUY,XYZ,XYZ CORP,SHS,10,120.00,,(1201.00)\n'
                   'CAD,21-03-2025,SELL,XYZ,XYZ CORP,SHS,-10,,,\n')
        self.assertIn("$0", str(cm.exception))

    def test_stock_buy_at_zero_refused(self):
        for proceeds in ("", "0.00"):
            with self.subTest(proceeds=proceeds):
                with self.assertRaises(BrokerageParseError):
                    _parse(f'USD,10-03-2025,BUY,ZZR,ZZR INC,SHS,40,0,,{proceeds}\n')

    def test_option_write_before_expiry_at_zero_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse('USD,05-06-2025,SELL,@QZA,CALL QZA06/20/25 50,OPC,-1,,,\n')
        self.assertIn("opens", str(cm.exception))

    def test_option_buy_at_zero_on_the_held_side_refused(self):
        with self.assertRaises(BrokerageParseError):
            _parse('USD,05-06-2025,BUY,@QZA,CALL QZA06/20/25 50,OPC,1,1.00,,(100.99)\n'
                   'USD,20-06-2025,BUY,,,,1,,,\n')

    def test_expiry_of_a_position_from_before_the_data_booked(self):
        tx, _, _ = _parse('USD,20-06-2025,SELL,@QZA,CALL QZA06/20/25 50,OPC,-1,,,\n')
        self.assertEqual(tx[0]["net_amount"], 0.0)

    def test_expiry_close_still_booked(self):
        tx, _, _ = _parse(
            'USD,05-06-2025,BUY,@QZA,CALL QZA06/20/25 50,OPC,1,1.00,,(100.99)\n'
            'USD,20-06-2025,SELL,,,,-1,0.00,,\n')
        self.assertEqual(len(tx), 2)

    def test_early_assignment_close_still_booked(self):
        tx, _, _ = _parse(
            'USD,05-06-2025,SELL,@ZZS,PUT ZZS06/20/25 305,OPC,-1,8.00,,799.35\n'
            'USD,13-06-2025,BUY,,,,1,0.00,,\n'
            'USD,16-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n')
        self.assertEqual(sum(t["action"] == "ASSIGN" for t in tx), 2)

    def test_unpaired_early_zero_close_warns(self):
        tx, err, _ = _parse(
            'USD,05-06-2025,SELL,@ZZS,PUT ZZS06/20/25 305,OPC,-1,8.00,,799.35\n'
            'USD,13-06-2025,BUY,,,,1,0.00,,\n')
        self.assertEqual(len(tx), 2)
        self.assertIn("before its expiry", err)


class TestUnbookedRows(unittest.TestCase):
    """A2-0285 / A2-0288 / A2-0289 / A2-1069: non-BUY/SELL rows that
    move shares or cash use the UNBOOKED channel."""

    def test_div_and_transfer_are_unbooked(self):
        _, err, _ = _parse(
            'USD,10-03-2025,BUY,ABC,ABC CORP,SHS,10,5.00,,(51.00)\n'
            'USD,12-03-2025,DIV,ABC,ABC CORP,SHS,,,,"1,250.00"\n'
            'USD,13-03-2025,TFI,MSFT,MICROSOFT CORP,SHS,50,,,\n')
        lines = [l for l in err.splitlines()
                 if l.startswith("warning: UNBOOKED:")]
        self.assertEqual(len(lines), 2, err)
        self.assertIn("DIV", lines[0])
        self.assertIn("1,250.00", lines[0])
        self.assertIn("TFI", lines[1])


class TestBrokerAccount(unittest.TestCase):
    """A2-0286 (parser side): each row names its broker account."""

    def test_rows_and_statement_accounts(self):
        tx, _, ex = _parse(_BUY)
        self.assertEqual(tx[0]["broker_account"], "55500001")  # pii-ok
        self.assertEqual(ex.statement_accounts(), {"55500001"})  # pii-ok

    def test_demo_style_preamble(self):
        acct = "55500002"  # pii-ok: synthetic id
        tx, _, ex = _parse(_BUY, pre=f"Account Number: {acct}\n\n")
        self.assertEqual(ex.statement_accounts(), {acct})

    def test_no_account_line(self):
        tx, _, ex = _parse(_BUY, pre="")
        self.assertEqual(ex.statement_accounts(), set())
        self.assertNotIn("broker_account", tx[0])


class TestUnparseableDate(unittest.TestCase):
    """A2-0618: a Date the parser cannot read is refused by file line."""

    def test_bad_dates_refused(self):
        for d in ("22/01/2025", "01-22-2025", "22-01-25", "31-02-2025"):
            with self.subTest(d=d):
                with self.assertRaises(BrokerageParseError) as cm:
                    _parse(f'USD,{d},BUY,ZZR,ZZR INC,SHS,40,48.41,,"(1,937.36)"\n')
                self.assertIn("line", str(cm.exception))

    def test_iso_date_still_read(self):
        tx, _, _ = _parse('USD,2025-01-22,BUY,ZZR,ZZR INC,SHS,40,48.41,,"(1,937.36)"\n')
        self.assertEqual(tx[0]["date_settle"], "2025-01-22")


class TestTickerChange(unittest.TestCase):
    """A2-0290 / A2-1067 / A2-1070."""

    def test_buy_first_shape_warns(self):
        _, err, _ = _parse(
            'USD,10-01-2025,BUY,QQOL,QQ HOLDINGS,SHS,500,10.00,,"(5,001.00)"\n'
            'USD,10-03-2025,BUY,QQNW,QQ HOLDINGS,SHS,10,10.00,,(101.00)\n'
            'USD,12-03-2025,SELL,QQNW,QQ HOLDINGS,SHS,-510,11.00,,"5,609.00"\n')
        # Renames are dated (A2-0197): the dated ticker.map line.
        self.assertIn("RENAME QQOL.US QQNW.US 2025-03-07", err)

    def test_rename_across_yearly_exports_warns_once(self):
        files = {
            "wb_2024.csv": (_PRE, 'USD,10-01-2024,BUY,QQOL,QQ HOLDINGS,SHS,500,10.00,,"(5,001.00)"\n'),
            "wb_2025.csv": (_PRE, 'USD,12-03-2025,SELL,QQNW,QQ HOLDINGS,SHS,-500,11.00,,"5,499.00"\n'),
        }
        _, err25 = _parse_folder(files, "wb_2025.csv")
        _, err24 = _parse_folder(files, "wb_2024.csv")
        self.assertIn("RENAME QQOL.US QQNW.US 2025-03-11", err25)
        self.assertNotIn("RENAME", err24)

    def test_other_broker_account_is_not_a_rename(self):
        other = _PRE.replace("55500001", "55500009")  # pii-ok
        files = {
            "wb_a.csv": (_PRE, 'USD,10-01-2024,BUY,QQOL,QQ HOLDINGS,SHS,500,10.00,,"(5,001.00)"\n'),
            "wb_b.csv": (other, 'USD,12-03-2025,SELL,QQNW,QQ HOLDINGS,SHS,-500,11.00,,"5,499.00"\n'),
        }
        _, err = _parse_folder(files, "wb_b.csv")
        self.assertNotIn("RENAME", err)


_PUT_ASSIGN = (
    'USD,05-06-2023,SELL,@ZZS,PUT ZZS06/16/23 180,OPC,-1,2.00,,199.35\n'
    'USD,16-06-2023,BUY,,,,1,0.00,,\n'
    'USD,20-06-2023,BUY,ZZS,ZZS INC,SHS,100,180.00,,"(18,001.00)"\n')


class TestAssignmentSettle(unittest.TestCase):
    """A2-1065 / A2-1071: an assignment's option leg takes its stock
    leg's settle date (CA-DATE-04 / US-DATE-04)."""

    @rule("CA-DATE-04")
    @rule("US-DATE-04")
    def test_option_leg_settles_with_the_stock_leg(self):
        tx, _, _ = _parse(_PUT_ASSIGN)
        legs = [t for t in tx if t["action"] == "ASSIGN"]
        self.assertEqual(len(legs), 2)
        opt = [t for t in legs if "P00180000" in t["symbol"]][0]
        stk = [t for t in legs if t["symbol"] == "ZZS.US"][0]
        self.assertEqual(opt["date_settle"], "2023-06-20")
        self.assertEqual(stk["date_settle"], "2023-06-20")
        self.assertEqual(opt["date"], "2023-06-16")


class TestSplitAssignmentShapes(unittest.TestCase):
    """A2-0617: a $0 close whose stock leg is split differently is named
    as a candidate, never silently booked as an expiry."""

    def test_two_contracts_vs_two_100_share_rows(self):
        tx, err, _ = _parse(
            'USD,05-06-2025,SELL,@ZZS,PUT ZZS06/20/25 305,OPC,-2,8.00,,"1,598.70"\n'
            'USD,20-06-2025,BUY,,,,2,0.00,,\n'
            'USD,23-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n'
            'USD,23-06-2025,BUY,,,,100,305.00,,"(30,501.00)"\n')
        self.assertIn("quantities differ", err)

    def test_two_one_contract_closes_vs_one_200_share_row(self):
        tx, err, _ = _parse(
            'USD,05-06-2025,SELL,@ZZS,PUT ZZS06/20/25 305,OPC,-2,8.00,,"1,598.70"\n'
            'USD,20-06-2025,BUY,,,,1,0.00,,\n'
            'USD,20-06-2025,BUY,,,,1,0.00,,\n'
            'USD,23-06-2025,BUY,ZZS,ZZS INC,SHS,200,305.00,,"(61,001.00)"\n')
        self.assertIn("quantities differ", err)


class TestNewestFirst(unittest.TestCase):
    """A2-1068: a newest-first export is read bottom-up (CA-DATE-14 /
    US-DATE-13)."""

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_same_day_rows_replayed_in_time_order(self):
        tx, _, _ = _parse(
            'USD,20-03-2025,BUY,ABC,ABC CORP,SHS,4,10.00,,(41.00)\n'
            'USD,20-03-2025,SELL,,,,-4,11.00,,43.00\n'
            'USD,15-01-2025,BUY,,,,10,9.00,,(91.00)\n')
        same_day = [t["quantity"] for t in tx
                    if t["date_settle"] == "2025-03-20"]
        self.assertEqual(same_day, [-4.0, 4.0])

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_oldest_first_kept(self):
        tx, _, _ = _parse(
            'USD,15-01-2025,BUY,ABC,ABC CORP,SHS,10,9.00,,(91.00)\n'
            'USD,20-03-2025,SELL,,,,-4,11.00,,43.00\n'
            'USD,20-03-2025,BUY,,,,4,10.00,,(41.00)\n')
        same_day = [t["quantity"] for t in tx
                    if t["date_settle"] == "2025-03-20"]
        self.assertEqual(same_day, [-4.0, 4.0])


if __name__ == "__main__":
    unittest.main()
