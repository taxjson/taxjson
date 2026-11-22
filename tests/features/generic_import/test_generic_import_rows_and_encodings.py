"""Re-audit-2 fixes: the generic column-mapped importer.

A2-0030 (cut-off last record), A2-0103/A2-1074/A2-0634 (futures_settle
= "next_day"), A2-0107/A2-0294/A2-0627 ('/' and '\\' futures), A2-0106
(a cash-in amount under a buy action), A2-0299 (a dangling sidecar
mapping), A2-0626/A2-1079 (commission rebates), A2-0628 (an option
expiring on Dec 31), A2-0629 (an exercise/assignment the mapping cannot
express), A2-1075 (a settle date far after the trade), A2-1076 (payment
in lieu), A2-1081 (non-string [defaults]/[formats] values), A2-1083 and
A2-1080 (UTF-16 exports), A2-1085 (the broker account of each row).
Synthetic data only (synthetic account ids, marked pii-ok).

Formerly: test_fix_a2_generic
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support.generic_parse import _BASE_COLS, _toml, _parse, _HDR


class TestCutOffLastRecord(unittest.TestCase):
    """A2-0030: a record cut short is refused, never completed from
    [defaults].

    Formerly: test_fix_a2_generic.TestCutOffLastRecord"""

    def test_record_with_fewer_cells_than_header_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,USD\n"
                   "2024-11-05,BUY,MSFT,5,4", _toml())
        msg = str(cm.exception)
        self.assertIn("fewer cells than the header", msg)
        self.assertIn("line 3", msg)

    def test_missing_currency_cell_not_filled_from_defaults(self):
        # Cut right after the amount: the currency cell is gone, and
        # [defaults] currency = CAD used to book a USD trade as MSFT.TO.
        with self.assertRaises(ValueError):
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95",
                   _toml())

    def test_cut_at_the_last_separator_refused(self):
        # '...,9.95,' with no line break after it: the final cell was
        # cut away, not left blank.
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,",
                   _toml())
        self.assertIn("cut", str(cm.exception))

    def test_cut_inside_the_currency_cell_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,US",
                   _toml())
        self.assertIn("currency", str(cm.exception))

    def test_blank_last_cell_with_a_line_break_still_uses_defaults(self):
        txs, _, _ = _parse(
            _HDR + "2024-11-04,BUY,XEI,50,20.00,-1009.95,9.95,\n", _toml())
        self.assertEqual(txs[0]["currency"], "CAD")
        self.assertEqual(txs[0]["symbol"], "XEI.TO")

    def test_complete_file_without_final_line_break_parses(self):
        txs, _, _ = _parse(
            _HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,USD", _toml())
        self.assertEqual(txs[0]["symbol"], "MSFT.US")


class TestBuyActionCashIn(unittest.TestCase):
    """A2-0106: under one action mapped to buy, a row whose amount is
    cash IN in a file whose buys carry negative (cash-out) amounts is a
    sale by the file's own convention.

    Formerly: test_fix_a2_generic.TestBuyActionCashIn"""

    def test_positive_amount_buy_in_a_cash_signed_file_refused(self):
        toml = _toml().replace('"BUY" = "buy"\n"SELL" = "sell"\n',
                               '"TRADE" = "buy"\n')
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2025-02-03,TRADE,XYZ,100,10,-1000.00,0,CAD\n"
                   "2025-06-03,TRADE,XYZ,100,20,2000.00,0,CAD\n", toml)
        self.assertIn("buy", str(cm.exception))
        self.assertIn("line 3", str(cm.exception))

    def test_unsigned_amount_file_still_accepted(self):
        txs, _, _ = _parse(_HDR + "2025-02-03,BUY,XYZ,100,10,1000.00,0,CAD\n"
                           "2025-06-03,BUY,XYZ,100,20,2000.00,0,CAD\n",
                           _toml())
        self.assertEqual([t["net_amount"] for t in txs], [1000.0, 2000.0])


class TestDanglingSidecar(unittest.TestCase):
    """A2-0299.

    Formerly: test_fix_a2_generic.TestDanglingSidecar"""

    def test_dangling_sidecar_refused_not_shared_mapping(self):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / "generic_a.csv"
            c.write_text(_HDR + "2025-01-02,BUY,XYZ,1,10,-10,0,CAD\n")
            (Path(td) / "generic.toml").write_text(_toml())
            os.symlink(Path(td) / "gone.toml",
                       c.with_name(c.name + ".toml"))
            with self.assertRaises(ValueError) as cm:
                GenericBrokerage().parse_file(c)
            self.assertIn("generic_a.csv.toml", str(cm.exception))
            from taxjson.lib.brokerages.generic import mapping_path
            with self.assertRaises(ValueError):
                mapping_path(c)


class TestRebates(unittest.TestCase):
    """A2-0626 / A2-1079: a credited commission lowers the cost and
    raises the proceeds, and is booked as a negative fee (Questrade's
    convention), never as a charge.

    Formerly: test_fix_a2_generic.TestRebates"""

    def test_amount_column_shows_the_rebate(self):
        txs, _, _ = _parse(
            _HDR + "2025-01-02,BUY,XYZ,100,10,-999.65,-0.35,USD\n"
            "2025-02-03,SELL,XYZ,-100,11,1100.35,-0.35,USD\n", _toml())
        self.assertAlmostEqual(txs[0]["net_amount"], 999.65)
        self.assertAlmostEqual(txs[0]["fee"], -0.35)
        self.assertAlmostEqual(txs[1]["net_amount"], 1100.35)
        self.assertAlmostEqual(txs[1]["fee"], -0.35)

    def test_amount_column_shows_a_charge(self):
        txs, _, _ = _parse(
            _HDR + "2025-01-02,BUY,XYZ,100,10,-1000.35,-0.35,USD\n",
            _toml())
        self.assertAlmostEqual(txs[0]["fee"], 0.35)

    def test_no_amount_column_explicit_charged_sign(self):
        toml = (_BASE_COLS + 'fee = "Fee"\n[formats]\nfee_sign = '
                '"charged"\n[actions]\n"BUY" = "buy"\n"SELL" = "sell"\n'
                '[defaults]\ncurrency = "USD"\n')
        txs, _, _ = _parse("Date,Type,Ticker,Qty,Price,Fee\n"
                           "2025-01-02,BUY,XYZ,100,10,-0.35\n"
                           "2025-02-03,SELL,XYZ,-100,11,-0.35\n", toml)
        self.assertAlmostEqual(txs[0]["net_amount"], 999.65)
        self.assertAlmostEqual(txs[1]["net_amount"], 1100.35)
        self.assertAlmostEqual(txs[0]["fee"], -0.35)

    def test_no_amount_column_default_keeps_magnitude_as_charge(self):
        toml = (_BASE_COLS + 'fee = "Fee"\n[actions]\n"BUY" = "buy"\n'
                '[defaults]\ncurrency = "USD"\n')
        txs, _, _ = _parse("Date,Type,Ticker,Qty,Price,Fee\n"
                           "2025-01-02,BUY,XYZ,100,10,0.35\n", toml)
        self.assertAlmostEqual(txs[0]["net_amount"], 1000.35)
        self.assertAlmostEqual(txs[0]["fee"], 0.35)

    def test_futures_gross_with_explicit_sign(self):
        toml = _toml(extra='[formats]\nfee_sign = "charged"\n')
        txs, _, _ = _parse(
            _HDR + "2025-01-02,SELL,F:ESZ5,-1,10,500.50,-0.50,USD\n", toml)
        self.assertAlmostEqual(txs[0]["gross_amount"], 500.0)
        self.assertAlmostEqual(txs[0]["fee"], -0.50)


class TestAssignmentShape(unittest.TestCase):
    """A2-0629: the mapping has no exercise/assignment target; a $0
    option close beside a stock trade at the strike is named on the
    console (ATTENTION) instead of booked silently as an expiry plus an
    unrelated trade.

    Formerly: test_fix_a2_generic.TestAssignmentShape"""

    def test_assignment_shape_is_attention(self):
        txs, err, _ = _parse(
            _HDR + "2025-02-07,SELL,QZV250815P00045000.TO,-1,2.00,200.00,"
            "0,CAD\n2025-08-15,BUY,QZV250815P00045000.TO,1,0,0,0,CAD\n"
            "2025-08-15,BUY,QZV.TO,100,45,-4500.00,0,CAD\n", _toml())
        self.assertEqual(len(txs), 3)
        self.assertIn("warning: ATTENTION:", err)
        self.assertIn("exercise/assignment", err)
        self.assertIn("QZV250815P00045000.TO", err)

    def test_no_attention_for_plain_expiry(self):
        _, err, _ = _parse(
            _HDR + "2025-02-07,SELL,QZV250815P00045000.TO,-1,2.00,200.00,"
            "0,CAD\n2025-08-15,BUY,QZV250815P00045000.TO,1,0,0,0,CAD\n",
            _toml())
        self.assertNotIn("ATTENTION", err)


class TestPaymentInLieu(unittest.TestCase):
    """A2-1076.

    Formerly: test_fix_a2_generic.TestPaymentInLieu"""

    def test_dividend_in_lieu_target(self):
        txs, _, _ = _parse(
            _HDR + "2025-03-03,PIL,XYZ,0,0,12.50,0,USD\n",
            _toml(extra_actions='"PIL" = "dividend_in_lieu"\n'))
        self.assertEqual(txs[0]["action"], "DIVIDEND_IN_LIEU")
        self.assertEqual(txs[0]["type"], "dividend_in_lieu")
        self.assertEqual(txs[0]["net_amount"], 12.5)
        self.assertEqual(txs[0]["description"], "PIL")


class TestValueTypes(unittest.TestCase):
    """A2-1081: [defaults] and [formats] values must be strings.

    Formerly: test_fix_a2_generic.TestValueTypes"""

    def _refused(self, toml):
        with self.assertRaises(ValueError) as cm:
            _parse("Date,Type,Qty,Price,Amount\n2025-01-02,BUY,1,10,-10\n",
                   toml)
        return str(cm.exception)

    def test_non_string_default_currency_refused(self):
        msg = self._refused(
            '[columns]\ndate="Date"\naction="Type"\nquantity="Qty"\n'
            'price="Price"\namount="Amount"\n[actions]\n"BUY"="buy"\n'
            '[defaults]\ncurrency=true\nsymbol="AAPL"\n')
        self.assertIn("[defaults].currency", msg)

    def test_non_string_default_symbol_refused(self):
        msg = self._refused(
            '[columns]\ndate="Date"\naction="Type"\nquantity="Qty"\n'
            'price="Price"\namount="Amount"\n[actions]\n"BUY"="buy"\n'
            '[defaults]\ncurrency="USD"\nsymbol=["AAPL"]\n')
        self.assertIn("[defaults].symbol", msg)

    def test_non_string_format_refused(self):
        msg = self._refused(
            '[columns]\ndate="Date"\naction="Type"\nquantity="Qty"\n'
            'price="Price"\namount="Amount"\n[formats]\nsettle=5\n'
            '[actions]\n"BUY"="buy"\n[defaults]\ncurrency="USD"\n'
            'symbol="AAPL"\n')
        self.assertIn("[formats].settle", msg)


class TestUtf16(unittest.TestCase):
    """A2-1083 (the importer) and A2-1080 (taxjson-generate-parser).

    Formerly: test_fix_a2_generic.TestUtf16"""

    _CSV = _HDR + "2025-01-02,BUY,XYZ,10,50.00,-500.00,0,CAD\n"

    def test_utf16_export_parses_like_utf8(self):
        a, _, _ = _parse(self._CSV, _toml())
        b, _, _ = _parse(None, _toml(), raw=self._CSV.encode("utf-16"))
        self.assertEqual(a, b)

    def test_cp1252_still_refused_naming_the_file(self):
        with self.assertRaises(ValueError) as cm:
            _parse(None, _toml(), raw=(self._CSV.replace("XYZ", "XÉZ")
                                       .encode("cp1252")))
        self.assertIn("generic_x.csv", str(cm.exception))

    def test_generate_parser_reads_a_utf16_sample(self):
        from taxjson.bin.taxjson_generate_parser import _read_sample
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.csv"
            p.write_bytes(self._CSV.encode("utf-16"))
            self.assertEqual(_read_sample(p, 1),
                             "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency")

    def test_generate_parser_names_a_legacy_file(self):
        env = dict(os.environ)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sample_x.csv"
            p.write_bytes("Date,Ticker\n2025-01-02,X\xc9Z\n".encode("cp1252"))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_generate_parser",
                 str(p), "-o", str(Path(td) / "out.py")],
                capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sample_x.csv", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestBrokerAccount(unittest.TestCase):
    """A2-1085 (parser side): the broker account of each row, from a
    mapped column or the mapping's [broker].account.

    Formerly: test_fix_a2_generic.TestBrokerAccount"""

    def test_account_column(self):
        toml = _toml(extra_cols='account = "Account"\n')
        txs, _, g = _parse(
            "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency,Account\n"
            "2025-01-02,BUY,XYZ,10,5,-50,0,CAD,55500001\n"  # pii-ok
            "2025-01-02,BUY,XYZ,10,5,-50,0,CAD,55500002\n",  # pii-ok
            toml)
        self.assertEqual([t["broker_account"] for t in txs],
                         ["55500001", "55500002"])  # pii-ok
        self.assertEqual(g.statement_accounts(),
                         {"55500001", "55500002"})  # pii-ok

    def test_broker_account_key(self):
        toml = _toml() + '[broker]\naccount = "55500001"\n'  # pii-ok
        txs, _, g = _parse(_HDR + "2025-01-02,BUY,XYZ,10,5,-50,0,CAD\n",
                           toml)
        self.assertEqual(txs[0]["broker_account"], "55500001")  # pii-ok
        self.assertEqual(g.statement_accounts(), {"55500001"})  # pii-ok

    def test_no_account_declared(self):
        txs, _, g = _parse(_HDR + "2025-01-02,BUY,XYZ,10,5,-50,0,CAD\n",
                           _toml())
        self.assertNotIn("broker_account", txs[0])
        self.assertEqual(g.statement_accounts(), set())

    def test_non_string_broker_account_refused(self):
        toml = _toml() + '[broker]\naccount = 55500001\n'  # pii-ok
        with self.assertRaises(ValueError):
            _parse(_HDR + "2025-01-02,BUY,XYZ,10,5,-50,0,CAD\n", toml)


if __name__ == "__main__":
    unittest.main()
