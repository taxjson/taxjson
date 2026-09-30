"""Regression tests for the generic importer / .tt / clean_number
audit findings (R1-93, R1-117..R1-123, R1-185). All data synthetic."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


def _parse(csv_text, toml_text):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_t.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(toml_text)
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = GenericBrokerage().parse_file(c)
        return txs, buf.getvalue()


_HDR = "Date,Type,Ticker,Shares,Price,Amount,Commission,Currency\n"
_TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
         'quantity="Shares"\nprice="Price"\namount="Amount"\n'
         'fee="Commission"\ncurrency="Currency"\n'
         '[actions]\n"BUY"="buy"\n"SELL"="sell"\n"DIV"="dividend"\n'
         '"WHT"="tax"\n"INT"="interest"\n')


def _tt(line, name="hand.tt"):
    from taxjson.bin.taxjson_convert_tt import tt_to_json
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(line)
        buf = io.StringIO()
        with redirect_stderr(buf):
            return tt_to_json(p, "margin")["transactions"], buf.getvalue()


# ---------------------------------------------------------------- R1-93
class TestCleanNumberDecimalComma(unittest.TestCase):
    def test_decimal_comma_refused(self):
        from taxjson.lib.brokerages.base import (BaseBrokerage,
                                                 BrokerageParseError)
        for raw in ("0,95", "470,02", "(114,96)", "1,5", "1.234,56",
                    "12,3456", "$0,95", "-4,95"):
            with self.subTest(raw=raw):
                with self.assertRaises(BrokerageParseError) as cm:
                    BaseBrokerage.clean_number(raw)
                self.assertIn(raw, str(cm.exception))

    def test_thousands_separator_still_works(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        self.assertEqual(BaseBrokerage.clean_number("1,234.56"), 1234.56)
        self.assertEqual(BaseBrokerage.clean_number("(1,352.97)"), -1352.97)
        self.assertEqual(BaseBrokerage.clean_number("1,000"), 1000.0)
        self.assertEqual(BaseBrokerage.clean_number("$1,234,567.8"),
                         1234567.8)
        self.assertEqual(BaseBrokerage.clean_number(""), 0.0)


# --------------------------------------------------------------- R1-118
class TestGenericDecimalComma(unittest.TestCase):
    def test_dividend_decimal_comma_refused(self):
        csv = _HDR + '2025-03-03,DIV,XYZ,0,0,"12,50",0,CAD\n'
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _TOML)
        msg = str(cm.exception)
        self.assertIn("12,50", msg)
        self.assertIn("line 2", msg)

    def test_trade_decimal_comma_refused(self):
        for cells in ('100,"45,50","-4550,00",0',
                      '"1,5",10.00,-15.00,0'):
            with self.subTest(cells=cells):
                csv = _HDR + f"2025-03-03,BUY,XYZ,{cells},CAD\n"
                with self.assertRaises(ValueError):
                    _parse(csv, _TOML)

    def test_thousands_separator_ok(self):
        csv = _HDR + '2025-03-03,BUY,XYZ,"1,000",10.00,"-10,000.00",0,CAD\n'
        txs, _ = _parse(csv, _TOML)
        self.assertEqual(txs[0]["quantity"], 1000.0)
        self.assertAlmostEqual(txs[0]["net_amount"], 10000.0)


class TestTtDecimalComma(unittest.TestCase):
    def test_decimal_comma_tokens_refused_with_file_line(self):
        for line in ("ADJUST 2025-03-03 09:30:00 XYZ.TO CAD -48,24\n",
                     "DIVIDEND 2025-03-03 09:30:00 XYZ.TO 0 CAD 0 12,50\n",
                     "INTEREST 2025-03-03 09:30:00 CAD 3,21\n",
                     "FEE 2025-03-03 09:30:00 CAD -4,95\n",
                     "SPLIT 2025-03-03 09:30:00 XYZ.TO XYZ.TO 1,5\n",
                     "BUYSELL 2025-03-03 09:30:00 XYZ.TO 10 CAD 45,50 455,00\n",
                     "ACQUIRED 2025-03-03 09:30:00 XYZ.TO 1,5 CAD 10 15 "
                     "ARRIVED 2025-04-01\n"):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as cm:
                    _tt("# header\n" + line)
                self.assertIn("hand.tt:2", str(cm.exception))

    def test_thousands_separator_ok(self):
        txs, _ = _tt("BUYSELL 2025-03-03 09:30:00 XYZ.TO 1,000 CAD 10 "
                     "10,000.00 0\n")
        self.assertEqual(txs[0]["quantity"], 1000.0)
        self.assertEqual(txs[0]["net_amount"], 10000.0)


# --------------------------------------------------------------- R1-117
class TestTtNegativeSellTotal(unittest.TestCase):
    def test_negative_sell_total_refused(self):
        with self.assertRaises(ValueError) as cm:
            _tt("BUYSELL 2025-02-03 09:30:00 XYZ.TO 100 CAD 10 1000\n"
                "BUYSELL 2025-06-03 09:30:00 XYZ.TO -100 CAD 20 -2000\n")
        msg = str(cm.exception)
        self.assertIn("hand.tt:2", msg)
        self.assertIn("-2000", msg)

    def test_negative_assign_sell_total_refused(self):
        with self.assertRaises(ValueError):
            _tt("ASSIGN 2025-06-03 09:30:00 XYZ.TO -100 CAD 20 -2000\n")

    def test_positive_sell_total_ok(self):
        txs, _ = _tt("BUYSELL 2025-06-03 09:30:00 XYZ.TO -100 CAD 20 2000\n")
        self.assertEqual(txs[0]["net_amount"], 2000.0)

    def test_negative_buy_total_is_unambiguous(self):
        # A buy's documented total (qty x price + fee) is never negative:
        # a cash-signed buy keeps being read as its magnitude.
        txs, _ = _tt("BUYSELL 2025-03-01 09:30:00 XYZ.TO 10 CAD 5.0 -50.0\n")
        self.assertEqual(txs[0]["quantity"], 10.0)


# --------------------------------------------------------------- R1-119
class TestGenericMappingStrict(unittest.TestCase):
    _CSV = _HDR + "2025-03-03,DIV,XYZ,0,0,125.00,0,CAD\n"

    def _err(self, toml, csv=None):
        with self.assertRaises(ValueError) as cm:
            _parse(csv or self._CSV, toml)
        return str(cm.exception)

    def test_unknown_columns_key_suggests(self):
        msg = self._err(_TOML.replace('amount="Amount"', 'ammount="Amount"'))
        self.assertIn("ammount", msg)
        self.assertIn("amount", msg.replace("ammount", ""))
        msg = self._err(_TOML.replace('fee="Commission"',
                                      'commission="Commission"'))
        self.assertIn("commission", msg)
        self.assertIn("fee", msg)

    def test_unknown_section_suggests(self):
        msg = self._err(_TOML + '[format]\ntax_sign = "withheld"\n')
        self.assertIn("[format]", msg)
        self.assertIn("formats", msg)

    def test_unknown_formats_and_defaults_keys(self):
        msg = self._err(_TOML + '[formats]\ntax_sgn = "withheld"\n')
        self.assertIn("tax_sgn", msg)
        self.assertIn("tax_sign", msg)
        msg = self._err(_TOML + '[defaults]\ncurency = "CAD"\n')
        self.assertIn("curency", msg)

    def test_income_target_needs_amount_mapping(self):
        toml = _TOML.replace('amount="Amount"\n', '')
        msg = self._err(toml)
        self.assertIn("amount", msg)
        self.assertIn("dividend", msg)

    def test_blank_income_amount_refused(self):
        csv = _HDR + "2025-03-03,DIV,XYZ,0,0,,0,CAD\n"
        msg = self._err(_TOML, csv)
        self.assertIn("line 2", msg)
        self.assertIn("amount", msg)

    def test_trade_targets_need_quantity(self):
        toml = _TOML.replace('quantity="Shares"\n', '')
        self.assertIn("quantity", self._err(toml))


# --------------------------------------------------------------- R1-120
class TestGenericTradeNeedsPriceOrAmount(unittest.TestCase):
    def test_blank_price_and_amount_refused(self):
        for act in ("BUY", "SELL"):
            with self.subTest(act=act):
                csv = _HDR + f"2025-03-03,{act},XYZ,100,,,4.95,CAD\n"
                with self.assertRaises(ValueError) as cm:
                    _parse(csv, _TOML)
                self.assertIn("line 2", str(cm.exception))

    def test_zero_cost_stock_buy_refused(self):
        csv = _HDR + "2025-03-03,BUY,XYZ,100,0,0,0,CAD\n"
        with self.assertRaises(ValueError):
            _parse(csv, _TOML)

    def test_option_expiry_zero_close_allowed(self):
        csv = (_HDR + "2025-03-03,SELL,XYZ250321C00050000,1,0,0,0,USD\n"
                      "2025-03-03,BUY,XYZ250321P00040000,1,0,0,0,USD\n")
        txs, _ = _parse(csv, _TOML)
        self.assertEqual([t["net_amount"] for t in txs], [0.0, 0.0])

    def test_blank_quantity_refused(self):
        csv = _HDR + "2025-03-03,BUY,XYZ,,10.00,-1000.00,0,CAD\n"
        with self.assertRaises(ValueError):
            _parse(csv, _TOML)


# --------------------------------------------------------------- R1-121
class TestGenericQuantitySign(unittest.TestCase):
    _CSV = (_HDR + "2025-02-03,TRADE,XYZ,100,10,-1000.00,0,CAD\n"
                   "2025-06-03,TRADE,XYZ,-100,20,2000.00,0,CAD\n")

    def test_single_action_mapped_to_buy_refused(self):
        toml = _TOML.replace('"BUY"="buy"', '"TRADE"="buy"')
        with self.assertRaises(ValueError) as cm:
            _parse(self._CSV, toml)
        self.assertIn("line 3", str(cm.exception))

    def test_single_action_mapped_to_sell_refused(self):
        toml = _TOML.replace('"SELL"="sell"', '"TRADE"="sell"')
        with self.assertRaises(ValueError) as cm:
            _parse(self._CSV, toml)
        self.assertIn("line 2", str(cm.exception))

    def test_sell_with_positive_qty_in_signed_file_refused(self):
        csv = (_HDR + "2025-02-03,BUY,XYZ,100,10,,0,CAD\n"
                      "2025-03-03,SELL,XYZ,-50,20,,0,CAD\n"
                      "2025-06-03,SELL,XYZ,50,20,,0,CAD\n")
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _TOML)
        self.assertIn("line 4", str(cm.exception))

    def test_consistent_signed_and_unsigned_files_ok(self):
        signed = (_HDR + "2025-02-03,BUY,XYZ,100,10,-1000.00,0,CAD\n"
                         "2025-06-03,SELL,XYZ,-100,20,2000.00,0,CAD\n")
        unsigned = (_HDR + "2025-02-03,BUY,XYZ,100,10,1000.00,0,CAD\n"
                           "2025-06-03,SELL,XYZ,100,20,2000.00,0,CAD\n")
        for csv in (signed, unsigned):
            txs, _ = _parse(csv, _TOML)
            self.assertEqual([t["quantity"] for t in txs], [100.0, -100.0])


# --------------------------------------------------------------- R1-122
class TestGenericExplicitSuffix(unittest.TestCase):
    def test_explicit_known_suffix_kept(self):
        cases = [("DLR.U.TO", "USD", "DLR.U.TO"),
                 ("SHOP.TO", "USD", "SHOP.TO"),
                 ("AAPL.US", "CAD", "AAPL.US"),
                 ("ABC.V", "USD", "ABC.V"),
                 ("ABC.VN", "CAD", "ABC.V"),
                 ("XEI", "CAD", "XEI.TO"),
                 ("DLR.U", "USD", "DLR.U.US"),
                 ("BRK B", "USD", "BRK.B.US")]
        for sym, cur, want in cases:
            with self.subTest(sym=sym, cur=cur):
                csv = _HDR + f"2025-03-03,BUY,{sym},10,10,,0,{cur}\n"
                txs, _ = _parse(csv, _TOML)
                self.assertEqual(txs[0]["symbol"], want)


# ------------------------------------------------------ R1-123 / R1-185
class TestGenericSettlement(unittest.TestCase):
    def test_computed_settle_is_holiday_aware(self):
        csv = (_HDR + "2025-06-02,BUY,XEI,100,10,,0,CAD\n"
                      "2025-12-31,SELL,XEI,100,20,,0,CAD\n"
                      "2025-12-31,SELL,SPY,1,20,,0,USD\n"
                      "2023-12-28,BUY,XEI,1,10,,0,CAD\n"
                      "2025-12-31,BUY,XYZ260116C00050000,1,1,,0,USD\n"
                      "2025-12-31,DIV,XEI,0,0,5.00,0,CAD\n")
        txs, _ = _parse(csv, _TOML)
        got = [(t["symbol"], t["date"], t["date_settle"]) for t in txs]
        self.assertEqual(got, [
            ("XEI.TO", "2025-06-02", "2025-06-03"),
            ("XEI.TO", "2025-12-31", "2026-01-02"),
            ("SPY.US", "2025-12-31", "2026-01-02"),
            ("XEI.TO", "2023-12-28", "2024-01-02"),     # T+2 era
            ("XYZ260116C00050000.US", "2025-12-31", "2026-01-02"),
            ("XEI.TO", "2025-12-31", "2025-12-31"),     # income: no cycle
        ])

    def test_settle_column_honoured(self):
        csv = ("Date,Settle,Type,Ticker,Shares,Price,Currency\n"
               "2025-12-31,2026-01-05,SELL,XEI,100,20,CAD\n"
               "2025-12-30,,SELL,XEI,100,20,CAD\n")
        toml = ('[columns]\ndate="Date"\nsettle="Settle"\naction="Type"\n'
                'symbol="Ticker"\nquantity="Shares"\nprice="Price"\n'
                'currency="Currency"\n[actions]\n"SELL"="sell"\n')
        txs, _ = _parse(csv, toml)
        self.assertEqual([t["date_settle"] for t in txs],
                         ["2026-01-05", "2025-12-31"])

    def test_settle_before_trade_refused(self):
        csv = ("Date,Settle,Type,Ticker,Shares,Price,Currency\n"
               "2025-12-31,2025-12-30,SELL,XEI,100,20,CAD\n")
        toml = ('[columns]\ndate="Date"\nsettle="Settle"\naction="Type"\n'
                'symbol="Ticker"\nquantity="Shares"\nprice="Price"\n'
                'currency="Currency"\n[actions]\n"SELL"="sell"\n')
        with self.assertRaises(ValueError) as cm:
            _parse(csv, toml)
        self.assertIn("line 2", str(cm.exception))

    def test_settle_on_trade_date_option(self):
        csv = _HDR + "2025-12-31,SELL,BTC,1,20,,0,CAD\n"
        txs, _ = _parse(csv, _TOML + "[options]\nsettle_on_trade_date = true\n")
        self.assertEqual(txs[0]["date_settle"], "2025-12-31")


if __name__ == "__main__":
    unittest.main()
