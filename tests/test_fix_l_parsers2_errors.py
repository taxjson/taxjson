"""Low-round parsers2 fixes: refusals are one line naming the file.

R1-132 / S065-20 (plain ValueError refusals printed a traceback),
S024-03 / S053-06 (a non-UTF-8 export or project text file printed a
traceback or never named the file), S053-01 (lint-crosslistings).
Synthetic data only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.cli_diag import InputReadError

_LATIN1 = "# café\n".encode("latin-1")


def _cli(module, *args, cwd=None):
    return subprocess.run([sys.executable, "-m", module, *map(str, args)],
                          capture_output=True, text=True, cwd=cwd,
                          stdin=subprocess.DEVNULL)


class TestBrokerageRefusalsAreOneLine(unittest.TestCase):

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def _brokerage(self, broker, path):
        return _cli("taxjson.bin.taxjson_brokerage", "--brokerage", broker,
                    path)

    def test_cp1252_kraken_ledger_names_the_file(self):
        p = self.td / "kr_ledgers.csv"
        p.write_bytes(
            b"txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"
            b"L1,R1,2025-01-01 00:00:00,deposit,,currency,ZCAD,100,0,"
            b"100 caf\xe9\n")
        r = self._brokerage("kraken", p)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("kr_ledgers.csv: not UTF-8", r.stderr)

    def test_cp1252_coinbase_names_the_file(self):
        p = self.td / "cb.csv"
        p.write_bytes(
            b"Timestamp,Transaction Type,Asset,Quantity Transacted,"
            b"Price Currency,Price at Transaction,Subtotal,"
            b"Total (inclusive of fees and/or spread),Fees and/or Spread,"
            b"Notes\n2025-01-01 00:00:00 UTC,Buy,BTC,1,USD,100,100,101,1,"
            b"caf\xe9\n")
        r = self._brokerage("coinbase", p)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("cb.csv: not UTF-8", r.stderr)

    def test_webull_layout_refusal_is_one_line(self):
        p = self.td / "wb_x.csv"
        p.write_text("Currency,Date,Action Code,Symbol,Security Description,"
                     "Quantity,Price\nUSD,01-02-2025,BUY,ABC,ABC INC,1,2\n")
        r = self._brokerage("webull", p)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("taxjson-brokerage: error: ", r.stderr)
        self.assertIn("unrecognised Webull export layout", r.stderr)

    def test_generic_importer_refusal_is_one_line(self):
        p = self.td / "generic_a.csv"
        p.write_text("Date,Type,Ticker,Qty,Price,Amount\n"
                     "2025-01-02,BUY,XYZ,10,10.00,999.00\n")
        (self.td / "generic_a.csv.toml").write_text(
            '[columns]\ndate = "Date"\naction = "Type"\nsymbol = "Ticker"\n'
            'quantity = "Qty"\nprice = "Price"\namount = "Amount"\n'
            '[actions]\n"BUY" = "buy"\n[defaults]\ncurrency = "CAD"\n')
        r = self._brokerage("generic", p)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("taxjson-brokerage: error: ", r.stderr)
        self.assertIn("generic importer: generic_a.csv line 2", r.stderr)

    def test_generic_importer_missing_mapping_is_one_line(self):
        p = self.td / "generic_b.csv"
        p.write_text("Date,Type\n2025-01-02,BUY\n")
        r = self._brokerage("generic", p)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.count("\n"), 1, r.stderr)


class TestProjectTextFilesNameThemselves(unittest.TestCase):
    """S053-06: a latin-1 comment in a project text file."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def _bad(self, name):
        p = self.td / name
        p.write_bytes(_LATIN1)
        return p

    def test_loaders_raise_input_read_error_naming_the_file(self):
        from taxjson.bin.taxjson_apply_distributions import load_rows
        from taxjson.bin.taxjson_brokerage import load_security_overrides
        from taxjson.bin.taxjson_carryover import load_claimed
        from taxjson.bin.taxjson_t1135 import load_overrides
        from taxjson.bin.taxjson_ticker_map import load_map_file
        # [[distributions]] are read from taxjson.toml and the EXTRACT
        # overrides from ticker.map (once distributions.map and
        # ticker_extraction_overrides.txt).
        for fn, name in ((load_rows, "taxjson.toml"),
                         (load_security_overrides, "ticker.map"),
                         (load_claimed, "claimed_losses.txt"),
                         (load_overrides, "t1135.map"),
                         (load_map_file, "ticker.map")):
            with self.subTest(name=name):
                with self.assertRaises(InputReadError) as cm:
                    fn(self._bad(name))
                self.assertIn(name, str(cm.exception))
                self.assertIn("not UTF-8", str(cm.exception))

    def test_ticker_map_tool_one_line(self):
        books = self.td / "b.json"
        books.write_text(json.dumps({"transactions": []}))
        r = _cli("taxjson.bin.taxjson_ticker_map", books,
                 self._bad("ticker.map"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("ticker.map: not UTF-8 text", r.stderr)

    def test_run_names_a_non_utf8_ticker_map(self):
        root = self.td / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\noption_grant_timing_since = 2025\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "inputs" / "margin" / "x.tt").write_text(
            "BUYSELL 2025-01-02 09:30:00 A.TO 1 CAD 1 1 0\n")
        (root / "ticker.map").write_bytes(_LATIN1)
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "run", "--no-input"], capture_output=True,
            text=True, env=dict(os.environ, TAXJSON_OFFLINE="1"),
            stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("ticker.map: not UTF-8 text", r.stderr)

    def test_run_says_not_utf8_instead_of_rename(self):
        # A cp1252 re-save of a broker export: content detection cannot
        # read it, and "rename it" was the wrong advice.
        root = self.td / "q"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\noption_grant_timing_since = 2025\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "inputs" / "margin" / "activity.csv").write_bytes(
            b"Transaction Date,Settlement Date,Action,Symbol,Description\n"
            b"2025-01-02,2025-01-03,Buy,XYZ,soci\xe9t\xe9\n")
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "run", "--no-input"], capture_output=True,
            text=True, env=dict(os.environ, TAXJSON_OFFLINE="1"),
            stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("activity.csv: not UTF-8 text", r.stderr)
        self.assertNotIn("Rename to start with", r.stderr)


class TestLintCrosslistingsBadRow(unittest.TestCase):
    """S053-01: a non-numeric quantity is one line, exit 2."""

    def test_non_numeric_quantity(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.json"
            p.write_text(json.dumps({"transactions": [{
                "action": "BUYSELL", "date": "2025-01-02",
                "time": "09:30:00", "date_settle": "2025-01-02",
                "symbol": "AAA.TO", "quantity": "ten", "currency": "CAD",
                "price": 1, "net_amount": 10, "account": "x"}]}))
            r = _cli("taxjson.bin.taxjson_lint_crosslistings", "--taxable",
                     p)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("non-numeric quantity", r.stderr)


if __name__ == "__main__":
    unittest.main()
