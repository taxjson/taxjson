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
                "Account,Data,U24681357,Options,Zelda Quixote,CAD\n",  # pii-ok
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


class Denylist(unittest.TestCase):
    """R1-345: digit loosening, the file name, a missing configured file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.deny = self.d / "deny"
        self.deny.write_text("# synthetic\n11223344\nQwertyfoo\n", encoding="utf-8")
        self.env = {**os.environ, "TAXJSON_PII_DENYLIST": str(self.deny)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_spaced_and_dashed_digits(self):
        src = self.d / "misc.csv"
        src.write_text("a,b\nWire 1122 3344 in,1\nJournal 1122-3344,2\nPlain 11223344,3\n",
                       encoding="utf-8")
        r = _run(str(src), "--out", str(self.d / "out"), env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = (self.d / "out" / "misc.redacted.csv").read_text(encoding="utf-8")
        self.assertNotIn("3344", out)

    def test_check_catches_spaced_digits(self):
        src = self.d / "one.csv"
        src.write_text("a,b\nref 1122 3344,1\n", encoding="utf-8")
        self.assertEqual(_run(str(src), "--check", env=self.env).returncode, 1)

    def test_file_name_denylisted_word(self):
        src = self.d / "qwertyfoo_stmt.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8")
        r = _run(str(src), "--out", str(self.d / "out"), env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = [p.name for p in (self.d / "out").iterdir()]
        self.assertEqual(len(names), 1)
        self.assertNotIn("qwertyfoo", names[0].lower())
        self.assertNotIn("qwertyfoo", r.stdout.lower())
        self.assertEqual(redacted_name(Path("a_1234.csv"), {}), "a_1234.redacted.csv")

    def test_missing_configured_denylist_fails_closed(self):
        src = self.d / "word.csv"
        src.write_text("a,b\nQwertyfoo,1\n", encoding="utf-8")
        env = {**self.env, "TAXJSON_PII_DENYLIST": str(self.d / "typo")}
        r = _run(str(src), "--check", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("TAXJSON_PII_DENYLIST", r.stderr)
        self.assertEqual(_run(str(src), "--check", "--no-denylist", env=env).returncode, 0)


class FrenchStreet(unittest.TestCase):
    """S036-16: '1234 rue Saint-Denis' in a statement preamble."""

    def test_preamble_and_free_text(self):
        out, rep = redact_text("JANE SAMPLE\n1234 rue Saint-Denis\n"
                               "Date,Symbol,Side,Qty,Price\n2025-01-01,XYZ,Buy,1,2\n")
        self.assertNotIn("Saint-Denis", out)
        out, rep = redact_text("Date,Description,Amount\n"
                               "2025-01-01,Cheque to 55 boulevard de la Concorde,10\n")
        self.assertNotIn("Concorde", out)
        self.assertGreaterEqual(rep.addresses, 1)

    def test_no_false_positive_on_quantities(self):
        text = "Date,Description,Qty\n2025-01-01,Bought 100 Route Inc shares,100\n"
        out, _ = redact_text(text)
        self.assertIn("100", out)


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


class NameShapes(unittest.TestCase):
    """S037-05: accented names, statement-vocabulary names, UPPER case."""

    def test_accented_names(self):
        out, rep = redact_text("Josée Tremblay\nDate,Description,Amount,Symbol\n"
                               "2025-01-01,E-TRANSFER FROM JOSÉE TREMBLAY,100,\n")
        self.assertNotIn("Tremblay".upper(), out.upper())

    def test_vocabulary_word_names_go_to_review(self):
        text = ("Bill Sample\nJane Price\nDate,Description,Amount,Symbol\n"
                "2025-01-02,E-TRANSFER FROM BILL SAMPLE,100,\n"
                "2025-01-03,Wire from Bill Sample,100,\n")
        out, rep = redact_text(text)
        flagged = {n for n, _ in rep.review}
        self.assertTrue({1, 2, 4, 5} <= flagged, rep.review)

    def test_all_vocabulary_phrase_not_flagged(self):
        out, rep = redact_text("Date,Description,Amount\n"
                               "2025-01-02,Transfer to Margin Account,100\n")
        self.assertEqual(rep.review, [])
        self.assertIn("Margin Account", out)

    def test_flat_description_note(self):
        _, rep = redact_text("Date,Description,Amount,Symbol\n2025-01-01,x,1,\n")
        self.assertTrue(rep.description_columns)


if __name__ == "__main__":
    unittest.main()
