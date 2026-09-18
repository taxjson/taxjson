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
            self.assertIn("55****01 ->", r.stdout)           # masked, never the id
            self.assertNotIn("55500001", r.stdout)
            # A redacted copy is skipped, not re-redacted.
            r2 = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(dst), "--no-denylist"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertIn("already a redacted copy", r2.stderr)
            # --check writes nothing.
            r3 = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(src), "--check", "--out", tmp + "/x", "--no-denylist"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r3.returncode, 0, r3.stderr)
            self.assertFalse((Path(tmp) / "x").exists())


if __name__ == "__main__":
    unittest.main()
