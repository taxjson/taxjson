"""Low-round parsers2 fixes: the .tt converter and its consumers.

R1-133 (BOM, atomic json->tt, no silent CAD), S028-24 (currency case),
S029-01 (a commission-over-gross sale entered as 0 is not a typo),
S029-03 (the total check on SELL lines), S029-09 (audit names the .tt).
Synthetic data only.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_convert_tt import (parse_tt_line, tt_to_json,
                                            tx_to_tt_line)


def _parse(line):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        tx = parse_tt_line(line, account_name="m", source="h.tt:1")
    return tx, err.getvalue()


class TestTotalCheck(unittest.TestCase):

    def test_sell_typo_warns(self):
        # S029-03: the check was only ever pinned on BUY lines.
        _, err = _parse("BUYSELL 2025-01-05 09:31:00 ABC.US -100 USD 50.00 "
                        "499.50 5.00")
        self.assertIn("differs from qty*price", err)

    def test_correct_sell_silent(self):
        _, err = _parse("BUYSELL 2025-01-05 09:31:00 ABC.US -100 USD 50.00 "
                        "4995.00 5.00")
        self.assertEqual(err, "")

    def test_commission_over_gross_entered_as_zero_is_silent(self):
        # S029-01: the README says to enter 0 when the commission
        # exceeds the proceeds; the check compared 0 with -8.95.
        _, err = _parse("BUYSELL 2025-06-16 09:30:00 ZZZ250620C00050000.US "
                        "-1 USD 0.01 0 9.95")
        self.assertEqual(err, "")


class TestCurrencyAndEncoding(unittest.TestCase):

    def test_currency_is_upper_cased(self):
        # S028-24: 'usd' beside 'USD' blocked the holdings export as
        # "mixed currencies".
        for line in ("BUYSELL 2025-01-02 09:30:00 XYZ.US 100 usd 10 1000 0",
                     "DIVIDEND 2025-01-02 09:30:00 XYZ.US 100 usd 0 5",
                     "INTEREST 2025-01-02 09:30:00 usd 5",
                     "ADJUST 2025-01-02 09:30:00 XYZ.US usd -5"):
            with self.subTest(line=line):
                tx, _ = _parse(line)
                self.assertEqual(tx["currency"], "USD")

    def test_bom_file_converts(self):
        # R1-133: a BOM read as '﻿BUYSELL' -> "unknown action".
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bom.tt"
            p.write_bytes(b"\xef\xbb\xbfBUYSELL 2025-01-02 09:30:00 AAA.TO "
                          b"10 CAD 1 10 0\n")
            doc = tt_to_json(p, "m")
        self.assertEqual(len(doc["transactions"]), 1)

    def test_missing_currency_refused(self):
        with self.assertRaises(ValueError) as cm:
            tx_to_tt_line({"action": "DIVIDEND", "date": "2025-02-14",
                           "time": "09:30:00", "symbol": "AAPL.US",
                           "net_amount": 25.0})
        self.assertIn("no currency", str(cm.exception))


class TestJsonToTtIsAtomic(unittest.TestCase):
    """R1-133: a row failing half-way left a partial .tt and destroyed
    the old one."""

    def test_failure_keeps_the_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in.json"
            src.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2025-01-02",
                 "time": "09:30:00", "symbol": "AAA.TO", "quantity": 10,
                 "currency": "CAD", "price": 1, "net_amount": 10},
                {"action": "BUYSELL", "date": "2025-01-03",
                 "time": "09:30:00", "symbol": "AAA.TO", "quantity": "ten",
                 "currency": "CAD", "price": 1, "net_amount": 10}]}))
            out = Path(d) / "out.tt"
            out.write_text("PREVIOUS\n")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_convert_tt",
                 str(src), str(out), "--date-basis", "settle"],
                capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(out.read_text(), "PREVIOUS\n")
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["in.json", "out.tt"])


class TestAuditNamesTheTtFile(unittest.TestCase):
    """S029-09: convert-tt records `source_file`; the audit read only
    `input_files`."""

    def test_source_label_falls_back_to_source_file(self):
        from taxjson.bin.taxjson_audit import _source_label
        label = _source_label(Path("margin_hand.json"),
                              {"source_file": "/p/inputs/margin/hand.tt"})
        self.assertEqual(label, "margin_hand.json (hand.tt)")


if __name__ == "__main__":
    unittest.main()
