"""`taxjson redact` — account ids and identity out, row shapes intact.
All fixtures synthetic (fake ids)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_redact import redact_text

REPO_ROOT = Path(__file__).resolve().parent.parent

IB = (
    'Statement,Header,Field Name,Field Value\n'
    'Statement,Data,BrokerName,Interactive Brokers\n'
    'Account Information,Header,Field Name,Field Value\n'
    'Account Information,Data,Name,Jane Q Sample\n'
    'Account Information,Data,Account,U12345678\n'
    'Account Information,Data,Account Alias,jane-margin\n'
    'Account Information,Data,Base Currency,CAD\n'
    'Trades,Header,DataDiscriminator,Asset Category,Currency,Account,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n'
    'Trades,Data,Order,Stocks,USD,U12345678,MSFT,"2026-02-05, 09:31:00",50,400.00,0,-20000,-1,0,0,0,O\n'
    'Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,Cash Amount,Code\n'
    'Transfers,Data,Stocks,USD,MDA,2026-09-01,ATON,Out,--,5550001234,-383,--,"-11,064.87",0.00,0.00,\n'
    'Deposits & Withdrawals,Header,Currency,Account,Settle Date,Description,Amount\n'
    'Deposits & Withdrawals,Data,USD,U12345678,2026-03-01,Wire from jane@example.com ref U12345678,5000\n'
)
QT = (
    'Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type\n'
    '2026-09-02 12:00:00 AM,2026-09-02 12:00:00 AM,TFI,MDA,MDA SPACE LTD COM TRANSFER IN,383,0,0,0,0,USD,55500001,Trades,RRSP\n'
)
RBC = (
    '"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n'
    ',,,,,,,,,\n'
    '"Account: 55500002 - Margin"\n'  # pii-ok (synthetic)
    'Date,Activity Type,Symbol,Description,Quantity,Price,Amount\n'
    '2025-01-13,Buy,AMD,ADVANCED MICRO DEVICES INC COM,100,116.5351,-11660.46\n'
)


class TestRedact(unittest.TestCase):
    def test_ib_ids_identity_and_email(self):
        out, rep = redact_text(IB)
        self.assertNotIn("U12345678", out)
        self.assertNotIn("5550001234", out)
        self.assertNotIn("Jane Q Sample", out)
        self.assertNotIn("jane-margin", out)
        self.assertNotIn("jane@example.com", out)
        # Same-shape placeholders, consistent everywhere (3 rows carry
        # the IB id, incl. inside a description).
        ph = rep.accounts["U12345678"]
        self.assertRegex(ph, r"^U9990\d{4}$")
        self.assertEqual(out.count(ph), 4)
        self.assertRegex(rep.accounts["5550001234"], r"^9990\d{6}$")  # pii-ok
        self.assertEqual(rep.identity_rows, 2)
        self.assertEqual(rep.emails, 1)
        # Everything else byte-identical: quantities, prices, dates, symbols.
        self.assertIn('MSFT,"2026-02-05, 09:31:00",50,400.00,0,-20000,-1', out)
        self.assertIn('MDA,2026-09-01,ATON,Out,--,', out)
        self.assertEqual(len(out.splitlines()), len(IB.splitlines()))
        self.assertIn("deposits & withdrawals", rep.description_columns)

    def test_questrade_and_rbc_account_columns(self):
        out, rep = redact_text(QT)
        self.assertNotIn("55500001", out)
        self.assertRegex(rep.accounts["55500001"], r"^9990\d{4}$")  # pii-ok
        self.assertIn(",383,0,0,0,0,USD,", out)
        out, rep = redact_text(RBC)
        self.assertNotIn("55500002", out)
        self.assertIn('"Account: 9990', out)
        self.assertIn("100,116.5351,-11660.46", out)

    def test_extra_patterns_and_denylist(self):
        out, rep = redact_text("note: Jane Sample owns this\n", ["Jane Sample"])
        self.assertEqual(out, "note: REDACTED owns this\n")
        self.assertEqual(rep.patterns, 1)

    def test_cli_writes_beside_input_never_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "questrade_2026.csv"
            src.write_text(QT)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(src), "--no-denylist"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            dst = Path(tmp) / "questrade_2026.redacted.csv"
            self.assertTrue(dst.exists())
            self.assertEqual(src.read_text(), QT)          # untouched
            self.assertNotIn("55500001", dst.read_text())
            self.assertIn("8 digits -> 99900001", r.stdout)  # shape only, never the id
            self.assertNotIn("55500001", r.stdout)
            # A redacted copy is skipped, not re-redacted.
            r2 = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(dst), "--no-denylist"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertIn("already a redacted copy", r2.stderr)
            # A second run refuses to overwrite the copy unless --force.
            r2b = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(src), "--no-denylist"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertNotEqual(r2b.returncode, 0)
            self.assertIn("use --force", r2b.stderr)
            # --check writes nothing.
            r3 = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(src), "--check", "--out", tmp + "/x", "--no-denylist"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r3.returncode, 0, r3.stderr)
            self.assertFalse((Path(tmp) / "x").exists())



class TestRedactAuditFindings(unittest.TestCase):
    """Pins for the 2026-09-18 pre-release audit of the redactor."""

    def test_small_ids_and_decimals_never_rewrite_quantities(self):
        text = "Date,Acct,Symbol,Qty,Price\n2026-01-01,1,X,1,1.5\n2026-01-02,2,Y,2,12.25\n"
        out, rep = redact_text(text)
        self.assertEqual(out, text)                       # 1-digit "ids" are not ids
        out, rep = redact_text('"Account: 12345.67"\nQty\n12345\n')
        self.assertIn("12345.67", out)                    # decimal part is not an id
        self.assertIn("\n12345\n", out)
        out, rep = redact_text('"Account: 10000 - Margin"\nDate,Qty\n2026-01-01,10000\n')
        self.assertIn(",10000\n", out)                    # 5-digit free-text "id" too short
        out, rep = redact_text("Account #\n20260115\n")  # pii-ok (synthetic fixture)
        self.assertEqual(rep.accounts, {})                # date-like 8 digits skipped

    def test_more_id_and_identity_shapes(self):
        text = ('Account ID: 5550000301\nName,Jane Q Sample\nPrimary Owner: Jane Q Sample\n'  # pii-ok (synthetic fixture)
                'Wire ref U55512346_2025\nid DU55512347 and F55512348\n'
                '"Account: 555-00004-1 - Margin"\nAccount Number\nZ55512345\n5551-2345\n'  # pii-ok (synthetic fixture)
                '"Name: Sample, Jane",x\n')
        out, rep = redact_text(text)
        for gone in ("5550000301", "Jane", "U55512346", "DU55512347", "F55512348",  # pii-ok (synthetic fixture)
                     "555-00004-1", "Z55512345", "5551-2345"):
            self.assertNotIn(gone, out, gone)
        self.assertIn("U99900002_2025", out)              # `_` is a boundary
        self.assertIn('"Account: 999-00000-', out)        # shape kept
        self.assertIn('"Name: REDACTED",x', out)
        out, rep = redact_text("ClientAccountID,AccountAlias,Symbol\nU55512345,jane-margin,MSFT\n")  # pii-ok (synthetic fixture)
        self.assertEqual(out.splitlines()[1], "U99900001,REDACTED,MSFT")

    def test_bytes_it_promises_to_keep(self):
        out, rep = redact_text('"Account Information","Data","Country","Canada","x"\n')
        self.assertEqual(out, '"Account Information","Data","Country","REDACTED","x"\n')
        out, rep = redact_text("Account #,Qty\r\n55512345,5\r\n")  # pii-ok (synthetic fixture)
        self.assertTrue(out.endswith("\r\n"))              # CRLF preserved
        from taxjson.bin.taxjson_redact import decode_export
        t, enc, bom = decode_export("Account #,Qty\r\n55512345,5\r\n".encode("utf-16"))  # pii-ok (synthetic fixture)
        self.assertEqual(enc, "utf-16")
        self.assertIn("55512345", t)                      # decoded, so it WILL be redacted
        t, enc, bom = decode_export(b"\xef\xbb\xbfAccount #\n")
        self.assertEqual((enc, bom), ("utf-8", b"\xef\xbb\xbf"))
        t, enc, bom = decode_export(b"Qu\xe9bec\n")
        self.assertEqual(enc, "cp1252")

    def test_bad_pattern_is_a_note_not_a_traceback(self):
        out, rep = redact_text("x Jane (Smith)\n", ["Jane (", "JANE"])
        self.assertEqual(out, "x REDACTED (Smith)\n")      # case-insensitive
        self.assertEqual(len(rep.notes), 1)

    def test_id_in_file_name_is_replaced_and_report_masks(self):
        from taxjson.bin.taxjson_redact import redacted_name, Report
        self.assertEqual(redacted_name(Path("U55512345_20250101_20251231.csv"), {}),
                         "U99900001_20250101_20251231.redacted.csv")
        self.assertEqual(Report.masked("U55512345"), "U + 8 digits")  # pii-ok (synthetic fixture)
        self.assertEqual(Report.masked("55512"), "5 digits")

    def test_symlink_target_refused_and_dashdash_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.csv").write_text(QT)
            (d / "elsewhere.txt").write_text("keep")
            (d / "a.redacted.csv").symlink_to(d / "elsewhere.txt")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(d / "a.csv"), "--no-denylist", "--force"],
                cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("symlink", r.stderr)
            self.assertEqual((d / "elsewhere.txt").read_text(), "keep")
            (d / "--check").write_text(QT)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 "--no-denylist", "--", str(d / "--check")],
                cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((d / "--check.redacted").exists() or
                            any(x.name.startswith("--check") and "redacted" in x.name
                                for x in d.iterdir()))


if __name__ == "__main__":
    unittest.main()
