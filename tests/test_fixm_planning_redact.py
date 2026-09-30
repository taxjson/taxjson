"""`taxjson redact` medium-round fixes (audit R1-340, R1-341, R1-345,
S036-16, S037-00, S037-02, S037-05). All data synthetic."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_redact import main, redact_text, redacted_name

NAME = "Zelda Quixote"


def _run(*argv, env=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_redact", *argv],
        capture_output=True, text=True, env=env)


class HolderNameShapes(unittest.TestCase):
    """R1-340: Coinbase User row, IB Flex Account Name, flat Name column."""

    def test_coinbase_flex_and_flat_name(self):
        for text in (
                "Transactions\nUser,Zelda Quixote,12345678-1234-1234-1234-123456789abc\n"
                "Timestamp,Transaction Type,Asset,Quantity Transacted\n2025-01-01,Buy,BTC,0.1\n",
                "Account,Header,AccountNumber,AccountAlias,Name,BaseCurrency\n"
                "Account,Data,U24681357,Options,Zelda Quixote,CAD\n",
                "Account #,Name,Symbol,Quantity\n55512345,Zelda Quixote,XYZ.TO,100\n"):  # pii-ok
            out, rep = redact_text(text)
            self.assertNotIn("Zelda", out)
            self.assertGreaterEqual(rep.identity_rows, 1)


class LabelValueCells(unittest.TestCase):
    """R1-341: a 'Label:' cell with its value in a later cell."""

    WB = ("Account Number / Numéro de compte:,,,,,,,,555123456,\n"  # pii-ok
          "Date,Symbol,Side,Qty,Price\n2025-01-01,XYZ,Buy,1,2\n")

    def test_webull_account_number_cell(self):
        out, rep = redact_text(self.WB)
        self.assertNotIn("555123456", out)  # pii-ok
        self.assertEqual(len(rep.accounts), 1)
        self.assertTrue(rep.found_anything())

    def test_plain_label_cell(self):
        out, rep = redact_text("Account Number:,555123456\nx\n")  # pii-ok
        self.assertNotIn("555123456", out)  # pii-ok
        self.assertEqual(len(rep.accounts), 1)

    def test_name_label_cell(self):
        for label in ("Name:", "Name / Nom:", "Client:"):
            out, rep = redact_text(f"{label},{NAME}\nDate,Symbol,Qty\n")
            self.assertNotIn("Zelda", out, label)
            self.assertIn(f"{label},REDACTED", out)

    def test_check_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "wb.csv"
            p.write_text("Account Number:,555123456\n", encoding="utf-8")  # pii-ok
            self.assertEqual(main([str(p), "--check", "--no-denylist"]), 1)


class IdColumnLabels(unittest.TestCase):
    """S037-00: client / plan / portfolio / acct-number columns."""

    def test_other_id_labels(self):
        text = ("Date,Client Number,Plan Number,Portfolio Number,Acct Number,Account Num,Symbol,Quantity\n"
                "2025-03-01,51234567,62234567,73234567,84234567,95234567,XYZ.TO,100\n")  # pii-ok
        out, rep = redact_text(text)
        for v in ("51234567", "62234567", "73234567", "84234567", "95234567"):  # pii-ok
            self.assertNotIn(v, out)
        self.assertEqual(len(rep.accounts), 5)
        self.assertIn("XYZ.TO,100", out)

    def test_account_type_is_not_an_id_column(self):
        out, rep = redact_text("Date,Account Type,Quantity\n2025-03-01,12345678,100\n")
        self.assertIn("12345678", out)


class PersonColumns(unittest.TestCase):
    """S037-02: annuitant / subscriber / beneficiary / holder-name columns,
    each cell replaced in its OWN position."""

    def test_plan_parties(self):
        text = ("Date,Account #,Account Type,Annuitant,Subscriber,Beneficiary,"
                "Account Holder Name,Customer Name,Symbol\n"
                "2025-01-01,55512345,RESP,,Zelda Quixote,Milo Quixote,Zelda Quixote,"  # pii-ok
                "Zelda Quixote,XYZ.TO\n")
        out, rep = redact_text(text)
        self.assertNotIn("Quixote", out)
        row = out.splitlines()[1].split(",")
        self.assertEqual(row[3:8], ["", "REDACTED", "REDACTED", "REDACTED", "REDACTED"])
        self.assertEqual(row[8], "XYZ.TO")

    def test_repeated_value_replaced_in_place(self):
        # The same text in a non-identity column earlier on the line
        # must stay; only the identity cell changes.
        text = "Symbol,Account #,Owner\nABC,55512345,ABC\n"  # pii-ok
        out, _ = redact_text(text)
        self.assertEqual(out.splitlines()[1], "ABC,99900001,REDACTED")


if __name__ == "__main__":
    unittest.main()
