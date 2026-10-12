"""Medium-round regression tests: generic importer + .tt converter
(parsers2 group). All data synthetic."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tax_rules import rule


def _parse(csv_text, toml_text, return_parser=False):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_t.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(toml_text)
        buf = io.StringIO()
        p = GenericBrokerage()
        with redirect_stderr(buf):
            txs = p.parse_file(c)
        if return_parser:
            return txs, buf.getvalue(), p
        return txs, buf.getvalue()


_HDR = "Date,Type,Ticker,Shares,Price,Amount,Commission,Currency\n"
_TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
         'quantity="Shares"\nprice="Price"\namount="Amount"\n'
         'fee="Commission"\ncurrency="Currency"\n'
         '[actions]\n"BUY"="buy"\n"SELL"="sell"\n"DIV"="dividend"\n'
         '"FEE"="fee"\n"JNL"="skip"\n')


def _tt(text, name="hand.tt"):
    from taxjson.bin.taxjson_convert_tt import tt_to_json
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text)
        buf = io.StringIO()
        with redirect_stderr(buf):
            return tt_to_json(p, "margin")["transactions"], buf.getvalue()


# ------------------------------------------------------------ R1-128
class TestGenericSymbolCase(unittest.TestCase):
    def test_mixed_case_symbols_share_one_pool(self):
        csv = (_HDR + "2025-01-15,BUY,xyz,100,10,-1000,0,CAD\n"
               "2025-06-20,SELL,XYZ,-100,20,2000,0,CAD\n")
        txs, _ = _parse(csv, _TOML)
        self.assertEqual({t["symbol"] for t in txs}, {"XYZ.TO"})

    def test_explicit_suffix_kept_and_uppercased(self):
        csv = _HDR + "2025-01-15,BUY,dlr.u.to,100,10,-1000,0,USD\n"
        txs, _ = _parse(csv, _TOML)
        self.assertEqual(txs[0]["symbol"], "DLR.U.TO")


# ------------------------------------------------------------ R1-129
class TestGenericUnmappedAction(unittest.TestCase):
    def test_unmapped_row_with_quantity_is_unbooked(self):
        csv = (_HDR + "2025-01-15,BUY,XYZ,100,10,-1000,0,CAD\n"
               "2025-03-15,REINVEST,XYZ,5,10,-50,0,CAD\n")
        txs, err, p = _parse(csv, _TOML, return_parser=True)
        self.assertEqual(len(txs), 1)
        self.assertIn("warning: UNBOOKED:", err)
        self.assertIn("REINVEST", err)
        self.assertEqual(len(p.lint_findings), 1)

    def test_unmapped_row_without_money_is_only_a_note(self):
        csv = (_HDR + "2025-01-15,BUY,XYZ,100,10,-1000,0,CAD\n"
               "2025-03-15,MEMO,XYZ,0,0,0,0,CAD\n")
        _txs, err, p = _parse(csv, _TOML, return_parser=True)
        self.assertNotIn("UNBOOKED", err)
        self.assertFalse(getattr(p, "lint_findings", None))

    def test_mapped_skip_is_not_unbooked(self):
        csv = (_HDR + "2025-01-15,BUY,XYZ,100,10,-1000,0,CAD\n"
               "2025-03-15,JNL,XYZ,5,0,-50,0,CAD\n")
        _txs, err, p = _parse(csv, _TOML, return_parser=True)
        self.assertNotIn("UNBOOKED", err)
        self.assertFalse(getattr(p, "lint_findings", None))


# ------------------------------------------------------------ R1-124
class TestGenericFeeSign(unittest.TestCase):
    def test_cash_signed_fee_becomes_positive_charged(self):
        csv = _HDR + "2025-03-03,FEE,,0,0,-5.00,0,USD\n"
        txs, _ = _parse(csv, _TOML)
        self.assertEqual(txs[0]["action"], "FEE")
        self.assertEqual(txs[0]["net_amount"], 5.0)

    def test_fee_rebate_stays_negative(self):
        csv = _HDR + "2025-03-03,FEE,,0,0,2.00,0,USD\n"
        txs, _ = _parse(csv, _TOML)
        self.assertEqual(txs[0]["net_amount"], -2.0)

    def test_fee_sign_charged_option(self):
        toml = _TOML.replace("[actions]",
                             '[formats]\nfee_sign="charged"\n[actions]')
        csv = _HDR + "2025-03-03,FEE,,0,0,5.00,0,USD\n"
        txs, _ = _parse(csv, toml)
        self.assertEqual(txs[0]["net_amount"], 5.0)


# ------------------------------------------------------------ S011-03
_SWAP_TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
              'quantity="Shares"\namount="Commission"\nfee="Net Amount"\n'
              'currency="Currency"\n[actions]\n"BUY"="buy"\n"SELL"="sell"\n')


class TestGenericCrossCheckWithoutPrice(unittest.TestCase):
    def test_swapped_fee_and_amount_refused_without_price(self):
        csv = ("Date,Type,Ticker,Shares,Commission,Net Amount,Currency\n"
               "2025-01-15,BUY,XYZ,100,4.95,1004.95,CAD\n")
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _SWAP_TOML)
        self.assertIn("fee", str(cm.exception))

    def test_blank_price_cell_still_cross_checked(self):
        toml = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
                'quantity="Shares"\nprice="Price"\namount="Commission"\n'
                'fee="Net Amount"\ncurrency="Currency"\n'
                '[actions]\n"BUY"="buy"\n')
        csv = ("Date,Type,Ticker,Shares,Price,Commission,Net Amount,"
               "Currency\n2025-01-15,BUY,XYZ,100,,4.95,1004.95,CAD\n")
        with self.assertRaises(ValueError):
            _parse(csv, toml)

    def test_plausible_row_without_price_accepted(self):
        toml = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
                'quantity="Shares"\namount="Net"\nfee="Comm"\n'
                'currency="Currency"\n[actions]\n"BUY"="buy"\n')
        csv = ("Date,Type,Ticker,Shares,Net,Comm,Currency\n"
               "2025-01-15,BUY,XYZ,100,-1004.95,4.95,CAD\n")
        txs, _ = _parse(csv, toml)
        self.assertEqual(txs[0]["net_amount"], 1004.95)


# ------------------------------------------------------------ S056-23
_NOFEE_TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
               'quantity="Shares"\nprice="Price"\namount="Net Amount"\n'
               'currency="Currency"\n[actions]\n"BUY"="buy"\n"SELL"="sell"\n')
_NOFEE_HDR = "Date,Type,Ticker,Shares,Price,Net Amount,Currency\n"


class TestGenericInferredFee(unittest.TestCase):
    def test_small_buy_with_commission_accepted_and_fee_inferred(self):
        csv = _NOFEE_HDR + "2025-01-15,BUY,ZZQ,10,20.00,-209.99,CAD\n"
        txs, _ = _parse(csv, _NOFEE_TOML)
        self.assertAlmostEqual(txs[0]["net_amount"], 209.99)
        self.assertAlmostEqual(txs[0]["fee"], 9.99)

    def test_inferred_fee_share_check_applies(self):
        csv = _NOFEE_HDR + "2025-01-15,BUY,ZZQ,10,20.00,-229.99,CAD\n"
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _NOFEE_TOML)
        self.assertIn("allow_large_fees", str(cm.exception))
        toml = _NOFEE_TOML + "[options]\nallow_large_fees=true\n"
        txs, _ = _parse(csv, toml)
        self.assertAlmostEqual(txs[0]["fee"], 29.99)

    def test_large_buy_and_sell_infer_fee(self):
        csv = (_NOFEE_HDR + "2025-01-15,BUY,ZZQ,1000,20.00,-20009.99,CAD\n"
               "2025-02-15,SELL,ZZQ,-1000,25.00,24990.01,CAD\n")
        txs, _ = _parse(csv, _NOFEE_TOML)
        self.assertAlmostEqual(txs[0]["fee"], 9.99)
        self.assertAlmostEqual(txs[1]["fee"], 9.99)
        self.assertAlmostEqual(txs[1]["net_amount"], 24990.01)

    def test_amount_below_gross_on_buy_still_refused(self):
        csv = _NOFEE_HDR + "2025-01-15,BUY,ZZQ,1000,20.00,-15000.00,CAD\n"
        with self.assertRaises(ValueError):
            _parse(csv, _NOFEE_TOML)


# ------------------------------------------------------------ S012-01
class TestGenericFutures(unittest.TestCase):
    def test_future_without_amount_refused(self):
        toml = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
                'quantity="Shares"\nprice="Price"\nfee="Commission"\n'
                'currency="Currency"\n[actions]\n"BUY"="buy"\n')
        csv = ("Date,Type,Ticker,Shares,Price,Commission,Currency\n"
               "2025-01-15,BUY,/CLZ5,1,80.57,2.50,USD\n")
        with self.assertRaises(ValueError) as cm:
            _parse(csv, toml)
        self.assertIn("contract size", str(cm.exception))

    def test_future_with_amount_books_the_amount(self):
        csv = (_HDR + "2025-01-15,BUY,/CLZ5,1,80.57,-80572.50,2.50,USD\n"
               "2025-02-15,BUY,F:CL251216P00047000,1,1.24,-1241.00,1.00,"
               "USD\n")
        txs, _ = _parse(csv, _TOML)
        self.assertEqual(txs[0]["net_amount"], 80572.50)
        self.assertEqual(txs[1]["net_amount"], 1241.00)


# ------------------------------------------------------------ S057-08
class TestGenericSwallowedRow(unittest.TestCase):
    def test_unescaped_quote_refused(self):
        toml = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
                'quantity="Shares"\nprice="Price"\namount="Amount"\n'
                'currency="Currency"\n[actions]\n"BUY"="buy"\n')
        csv = ("Date,Type,Ticker,Description,Shares,Price,Amount,Currency\n"
               '2025-01-02,BUY,ABC,"ABC CORP "",100,10,-1000,CAD\n'
               '2025-01-03,BUY,DEF,"DEF CORP CL A",50,20,-1000,CAD\n'
               "2025-01-04,BUY,GHI,GHI INC,10,5,-50,CAD\n")
        with self.assertRaises(ValueError) as cm:
            _parse(csv, toml)
        self.assertIn("line", str(cm.exception))

    def test_row_wider_than_header_refused(self):
        csv = _HDR + "2025-01-15,BUY,XYZ,100,10,-1000,0,CAD,extra\n"
        with self.assertRaises(ValueError):
            _parse(csv, _TOML)


# ------------------------------------------------------ S001-06/S028-19
class TestTtSymbolCanonical(unittest.TestCase):
    def test_lowercase_symbol_uppercased(self):
        txs, _ = _tt("BUYSELL 2023-01-10 09:30:00 aapl.us 100 USD 130.00 "
                     "13000.00 0\n"
                     "SPLIT 2023-02-10 09:30:00 abc.to xyz.to 2\n")
        self.assertEqual(txs[0]["symbol"], "AAPL.US")
        self.assertEqual(txs[1]["symbol"], "ABC.TO")
        self.assertEqual(txs[1]["symbol_new"], "XYZ.TO")

    def test_unknown_suffix_warns_naming_line(self):
        for sym in ("XYZ.TSX", "XYZ.CA"):
            with self.subTest(sym=sym):
                _txs, err = _tt(f"BUYSELL 2025-02-03 09:30:00 {sym} 100 CAD "
                                f"10 1000\n")
                self.assertIn("hand.tt:1", err)
                self.assertIn("suffix", err)

    def test_bare_crypto_and_options_do_not_warn(self):
        _txs, err = _tt(
            "BUYSELL 2025-02-03 09:30:00 BTC 0.1 USD 50000 5000\n"
            "BUYSELL 2025-02-03 09:30:00 AAPL250117C00150000.US 1 USD 2 200\n")
        self.assertNotIn("suffix", err)


# ------------------------------------------------------------ S029-00
class TestTtFuturesTotalWarning(unittest.TestCase):
    def test_futures_totals_not_called_typos(self):
        _txs, err = _tt(
            "BUYSELL 2025-01-15 09:30:00 /CLZ5 1 USD 80.57 80572.50 2.50\n"
            "BUYSELL 2025-01-15 09:30:00 F:CL251216P00047000.US 1 USD 1.24 "
            "1241.00 1.00\n")
        self.assertNotIn("differs", err)

    def test_equity_typo_still_warns(self):
        _txs, err = _tt("BUYSELL 2025-01-15 09:30:00 XYZ.US 10 USD 10 "
                        "1000\n")
        self.assertIn("differs", err)


# ------------------------------------------------------------ S029-07
class TestTtAcquiredQuantityComma(unittest.TestCase):
    def test_decimal_comma_quantity_refused(self):
        with self.assertRaises(ValueError):
            _tt("ACQUIRED 2020-01-02 09:30:00 XYZ.TO 1,5 CAD 45.50 68.25 "
                "ARRIVED 2025-04-22\n")


# ------------------------------------------------------------ S029-08
@rule("CA-ACB-10")
class TestTtAcquiredIdenticalLots(unittest.TestCase):
    def test_identical_lots_keep_both_arrival_legs(self):
        txs, _ = _tt(
            "ACQUIRED 2020-01-02 09:30:00 XYZ.TO 100 CAD 10.00 1000.00 "
            "ARRIVED 2025-04-22\n"
            "ACQUIRED 2021-05-03 09:30:00 XYZ.TO 100 CAD 10.00 1000.00 "
            "ARRIVED 2025-04-22\n")
        buys = [t for t in txs if t["action"] == "BUYSELL"]
        xfers = [t for t in txs if t["action"] == "TRANSFER"]
        self.assertEqual(len(buys), 2)
        self.assertEqual(sum(t["quantity"] for t in xfers), -200)
        # No two rows may share an id (sort --dedup keys on it).
        ids = [t["id"] for t in txs]
        self.assertEqual(len(ids), len(set(ids)))
        from taxjson.lib.pipeline import MANUAL_TRANSFER_DECLARATION
        for t in xfers:
            self.assertEqual(t.get("description"),
                             MANUAL_TRANSFER_DECLARATION)

    def test_single_acquired_unchanged(self):
        txs, _ = _tt("ACQUIRED 2020-01-02 09:30:00 XYZ.TO 100 CAD 10.00 "
                     "1000.00 ARRIVED 2025-04-22\n")
        x = [t for t in txs if t["action"] == "TRANSFER"]
        hand, _ = _tt("TRANSFER 2025-04-22 09:30:00 XYZ.TO -100 CAD 10.00 "
                      "1000.00 DECLARED\n")
        self.assertEqual(x[0]["id"], hand[0]["id"])


# ------------------------------------------------------------ R1-126
class TestTtUsdSplitHoldings(unittest.TestCase):
    def test_usd_split_is_not_a_mixed_currency_pool(self):
        from taxjson.bin.taxjson_run import _raw_mixed_currency_symbols
        txs, _ = _tt("BUYSELL 2025-01-15 09:30:00 XYZ.US 100 USD 10 1000\n"
                     "SPLIT 2025-03-01 09:30:00 XYZ.US XYZ.US 2\n"
                     "BUYSELL 2025-06-15 09:30:00 XYZ.US -50 USD 20 1000\n")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m_raw.json"
            p.write_text(json.dumps({"transactions": txs}))
            self.assertEqual(_raw_mixed_currency_symbols(p), [])


# ------------------------------------------------------ R1-130/S002-03
class TestTtEmitterDates(unittest.TestCase):
    _SELL = {"action": "BUYSELL", "date": "2025-12-31", "time": "10:00:00",
             "date_settle": "2026-01-02", "symbol": "QZA.TO",
             "quantity": -100.0, "currency": "CAD", "price": 12.0,
             "net_amount": 1199.0, "commission": 1.0}

    def test_settle_date_emitted_by_default(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line
        line = tx_to_tt_line(self._SELL)
        self.assertTrue(line.startswith("BUYSELL 2026-01-02 "), line)
        self.assertTrue(line.endswith(" 1.00000"), line)   # commission

    def test_trade_date_basis(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line
        line = tx_to_tt_line(self._SELL, date_basis="trade")
        self.assertTrue(line.startswith("BUYSELL 2025-12-31 "), line)

    def test_round_trip_keeps_the_settle_year(self):
        from taxjson.bin.taxjson_convert_tt import (json_to_tt_lines,
                                                    parse_tt_line)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "b.json"
            fut = {"action": "BUYSELL", "date": "2025-03-03",
                   "time": "10:00:00", "date_settle": "2025-03-03",
                   "symbol": "F:CLZ5.US", "quantity": 1.0,
                   "currency": "USD", "price": 70.0, "net_amount": 70002.0,
                   "fee": 2.0, "multiplier": 1000}
            p.write_text(json.dumps({"transactions": [self._SELL, fut]}))
            buf = io.StringIO()
            with redirect_stderr(buf):
                lines = list(json_to_tt_lines(p))
        err = buf.getvalue()
        back = parse_tt_line(lines[0])
        self.assertEqual(back["date_settle"], "2026-01-02")
        self.assertIn("1 row(s)", err)          # trade != settle note
        # The futures row's contract size now rides along as `x1000`
        # (S026-22) instead of the "cannot hold" warning.
        self.assertTrue(lines[1].endswith(" x1000"), lines[1])
        self.assertEqual(parse_tt_line(lines[1])["multiplier"], 1000.0)
        self.assertNotIn("multiplier", err)

    @rule("CA-DATE-01")
    def test_events_view_single_account_uses_settle_date(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        self.assertTrue(_tx_display_line(self._SELL, settle=True)
                        .startswith("BUYSELL 2026-01-02 "))
        self.assertTrue(_tx_display_line(self._SELL)
                        .startswith("BUYSELL 2025-12-31 "))


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------ S057-02
class TestGenericNegativeSellNet(unittest.TestCase):
    """A sell whose commission exceeds its gross nets negative; the
    schema accepts a negative SELL net (S017-00) and the engine deducts
    it, so the importer must neither refuse nor clamp it (S057-02)."""

    _OPT = "ZZQ250321C00050000"
    _BUY = f"2025-01-15,BUY,{_OPT},1,2.00,-209.95,9.95,CAD\n"

    def _toml(self):
        return _TOML + "[options]\nallow_large_fees=true\n"

    def test_amount_mapped_negative_sell_net_booked(self):
        for qty in ("-1", "1"):
            csv = (_HDR + self._BUY +
                   f"2025-03-01,SELL,{self._OPT},{qty},0.01,-8.95,9.95,CAD\n")
            txs, _ = _parse(csv, self._toml())
            self.assertAlmostEqual(txs[1]["net_amount"], -8.95)
            self.assertEqual(txs[1]["quantity"], -1.0)

    def test_derived_negative_sell_net_not_clamped(self):
        toml = (self._toml().replace('amount="Amount"\n', '')
                .replace('"DIV"="dividend"\n', '').replace('"FEE"="fee"\n', ''))
        csv = (_HDR + f"2025-01-15,BUY,{self._OPT},1,2.00,,9.95,CAD\n"
               f"2025-03-01,SELL,{self._OPT},-1,0.01,,9.95,CAD\n")
        txs, err = _parse(csv, toml)
        self.assertAlmostEqual(txs[1]["net_amount"], -8.95)
        self.assertNotIn("clamped", err)

    def test_negative_amount_not_matching_fee_still_refused(self):
        csv = (_HDR + self._BUY +
               f"2025-03-01,SELL,{self._OPT},-1,0.01,-500.00,9.95,CAD\n")
        with self.assertRaises(ValueError):
            _parse(csv, self._toml())
