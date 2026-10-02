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
    'Transfers,Data,Stocks,USD,MDX,2026-05-20,ATON,Out,--,5550001234,-250,--,"-7,412.50",0.00,0.00,\n'
    'Deposits & Withdrawals,Header,Currency,Account,Settle Date,Description,Amount\n'
    'Deposits & Withdrawals,Data,USD,U12345678,2026-03-01,Wire from jane@example.com ref U12345678,5000\n'
)
QT = (
    'Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type\n'
    '2026-05-22 12:00:00 AM,2026-05-22 12:00:00 AM,TFI,MDX,MDX EXAMPLE LTD COM TRANSFER IN,250,0,0,0,0,USD,55500001,Trades,RRSP\n'
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
        self.assertIn('MDX,2026-05-20,ATON,Out,--,', out)
        self.assertEqual(len(out.splitlines()), len(IB.splitlines()))
        self.assertIn("deposits & withdrawals", rep.description_columns)

    def test_questrade_and_rbc_account_columns(self):
        out, rep = redact_text(QT)
        self.assertNotIn("55500001", out)
        self.assertRegex(rep.accounts["55500001"], r"^9990\d{4}$")  # pii-ok
        self.assertIn(",250,0,0,0,0,USD,", out)
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
            # --check writes nothing, and exits 1 when it FOUND ids.
            r3 = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact",
                 str(src), "--check", "--out", tmp + "/x", "--no-denylist"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r3.returncode, 1, r3.stderr)
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
                'Wire ref U55512346_2025\nid DU55512347 and F55512348\n'  # pii-ok (synthetic fixture)
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
        # Quoting kept; every value cell of an identity row is blanked
        # (a multi-cell address kept its city, A2-1391).
        self.assertEqual(out, '"Account Information","Data","Country","REDACTED","REDACTED"\n')
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
        self.assertEqual(redacted_name(Path("U55512345_20250101_20251231.csv"), {}),  # pii-ok (synthetic)
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



def _cli(*args, cwd=REPO_ROOT):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "redact", *args],
        cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)


# Contact-detail shapes are assembled at run time so the source itself
# carries no address/postal/SSN-shaped literal for PII scanners to flag.
_STREET1 = "123 " + "Maple Street, Unit 4"
_STREET2 = "42 " + "Example Crescent East"
_POSTAL1 = "M5V" + " 2T6"
_POSTAL2 = "K1A" + " 0B1"
_POSTAL3 = "H0H" + "0H0"
_NOT_POSTAL = "D1A" + " 1A1"          # D is outside the Canada Post alphabet
_SSN = "-".join(("555", "12", "3456"))


class TestRedactIdentifierShapes(unittest.TestCase):
    """2026-09 security audit: identifier shapes the redactor missed
    while the README promised it strips account numbers and names.
    Every value below is synthetic."""

    def test_names_in_free_text(self):
        ib = ('Deposits & Withdrawals,Header,Currency,Account,Settle Date,Description,Amount\n'
              'Deposits & Withdrawals,Data,CAD,U55512345,2026-03-01,'  # pii-ok (synthetic fixture)
              'Disbursement Initiated by Jane Q Sample,-5000\n'
              'Deposits & Withdrawals,Data,CAD,U55512345,2026-03-02,'  # pii-ok (synthetic fixture)
              'Wire from Jonas Example,5000\n')
        out, rep = redact_text(ib)
        self.assertNotIn("Jane", out)
        self.assertNotIn("Jonas", out)
        self.assertIn("Disbursement Initiated by REDACTED,-5000", out)
        self.assertEqual(rep.names, 2)
        rbc = ('"Account: 55500002 - Margin, Jane Sample"\n'  # pii-ok (synthetic fixture)
               'Date,Activity Type,Symbol,Description,Quantity,Price,Amount\n'
               '2025-01-14,Transfer,,CONTRIBUTION Mrs. Jane Sample,0,0,100\n')
        out, rep = redact_text(rbc)
        self.assertNotIn("Jane", out)
        self.assertIn('"Account: 9990', out)
        self.assertIn("Margin, REDACTED\"", out)
        self.assertIn("CONTRIBUTION Mrs. REDACTED,0,0,100", out)

    def test_webull_preamble_name_address_and_alnum_account(self):
        text = ('Webull Securities Statement\n'
                'JANE SAMPLE\n'
                + _STREET1 + '\n'
                'Toronto, ON ' + _POSTAL1 + '\n'
                'Phone: 416-555-0123\n'
                'Account Number: 5MV07654\n'
                '"Currency","Date","Action Code","Symbol","Security Description","Quantity","Price"\n'
                'USD,2025-01-02,BUY,AAPL,APPLE INC,10,150.00\n')
        out, rep = redact_text(text)
        for gone in ("JANE", "Maple", "M5V", "Toronto", "416", "5MV07654"):
            self.assertNotIn(gone, out, gone)
        self.assertIn("Webull Securities Statement\n", out)     # a title is not a name
        self.assertIn("USD,2025-01-02,BUY,AAPL,APPLE INC,10,150.00\n", out)
        self.assertRegex(rep.accounts["5MV07654"], r"^9MV9900\d$")
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))

    def test_account_numbers_without_the_word_account(self):
        text = ('Date,Activity Type,Symbol,Description,Quantity,Price,Amount\n'
                '2025-01-13,Transfer,,TRANSFER FROM 55511111,0,0,100\n'
                '2025-01-14,Transfer,,TRANSFER TO ACCT 55522222,0,0,100\n'
                '2025-01-15,Transfer,,bank acct 0555-1234567,0,0,100\n')
        out, rep = redact_text(text)
        for gone in ("55511111", "55522222", "0555-1234567"):
            self.assertNotIn(gone, out, gone)
        self.assertIn("TRANSFER FROM 9990", out)
        self.assertIn("bank acct 9990-", out)                    # shape kept

    def test_ib_flex_acctalias_column(self):
        text = ('"ClientAccountID","AcctAlias","Symbol","Quantity"\n'
                '"U55512345","janes-rrsp","MSFT","10"\n')  # pii-ok (synthetic fixture)
        out, rep = redact_text(text)
        self.assertNotIn("janes-rrsp", out)
        self.assertIn('"U99900001","REDACTED","MSFT","10"', out)

    def test_contact_details(self):
        text = ('Date,Description,Amount\n'
                '2025-01-01,call (604) 555-0199 or +1 416 555 0123,0\n'
                '2025-01-02,mail ' + _POSTAL2 + ' and ' + _POSTAL3 + ',0\n'
                '2025-01-03,ship to ' + _STREET2 + ',0\n'
                '2025-01-04,SIN 046 454 286 SSN ' + _SSN + ',0\n'  # pii-ok (CRA sample SIN)
                '2025-01-05,SIN: 046454286,0\n')  # pii-ok (CRA sample SIN)
        out, rep = redact_text(text)
        for gone in ("604", "416", _POSTAL2, _POSTAL3, "Example Crescent",
                     "046 454 286", "046454286", _SSN):  # pii-ok (CRA sample SIN)
            self.assertNotIn(gone, out, gone)
        self.assertEqual((rep.phones, rep.postal_codes, rep.addresses, rep.sins),
                         (2, 2, 1, 3))

    def test_contact_lookalikes_are_kept(self):
        keep = ('Date,Description,Quantity,Price\n'
                # Canada Post never uses D/F/I/O/Q/U; hyphenated tokens,
                # non-Luhn 3-3-3 groups, dates and share counts stay.
                '2025-01-01,' + _NOT_POSTAL + ' ABC-' + _POSTAL3 + '-X ref 123 456 789,5,1.5\n'
                '2025-01-02,CASH DIV ON 500 SHS REC 09/26/25 PAY 10/07/25,0,0\n'
                '2025-01-03,account opened 2025-01-15 qty 1234567.5,0,0\n'
                '2025-01-04,"XYZ(US0000000101) Split 1 for 10 (XYZ, XYZ CORP, US0000000101)",9,0\n')
        out, rep = redact_text(keep)
        self.assertEqual(out, keep)
        self.assertFalse(rep.found_anything())

    def test_wallets_and_tx_ids_get_stable_distinct_pseudonyms(self):
        kraken = ('"txid","refid","time","type","subtype","aclass","asset","amount","fee","balance"\n'
                  '"LAAAA1-BBBB2-CCCC34","TXYZ12-A2B3C-DEF456","2024-01-01 00:00:00","trade","","currency","XXBT","0.1","0","0.1"\n'
                  '"LAAAA2-BBBB2-CCCC35","TXYZ12-A2B3C-DEF456","2024-01-01 00:00:00","trade","","currency","ZUSD","-4000","0","100"\n')
        out, rep = redact_text(kraken)
        rows = out.splitlines()[1:]
        self.assertNotIn("LAAAA1", out)
        self.assertNotIn("TXYZ12", out)
        tx1, ref1 = rows[0].split(",")[:2]
        tx2, ref2 = rows[1].split(",")[:2]
        self.assertNotEqual(tx1, tx2)                            # distinct stay distinct
        self.assertEqual(ref1, ref2)                             # shared stays shared
        self.assertRegex(tx1, r'^"\d{6}-\d{5}-\d{6}"$')          # same shape
        text = ('Timestamp,Notes\n'
                '2024-01-01,Sent 0.1 BTC to bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq\n'
                '2024-01-02,Sent 0.1 BTC to 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2\n'
                '2024-01-03,Sent 1 ETH to 0x52908400098527886E0F7030069857D2E4169EE7\n'
                '2024-01-04,Again to bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq\n'
                '2024-01-05,Coinbase id 5f9c1a2b3c4d5e6f7a8b9c0d\n')
        out, rep = redact_text(text)
        for gone in ("bc1qar0", "1BvBMSEY", "0x5290", "5f9c1a2b"):
            self.assertNotIn(gone, out, gone)
        self.assertEqual(len(rep.wallets), 3)
        lines = out.splitlines()
        self.assertEqual(lines[1].split()[-1], lines[4].split()[-1])
        self.assertTrue(lines[3].split()[-1].startswith("0x"))
        self.assertEqual(len(lines[3].split()[-1]), 42)

    def test_review_list_names_lines_it_could_not_classify(self):
        text = ('Date,Description,Amount\n'
                '2025-01-01,Gift for Jonas Example,0\n'
                '2025-01-02,reference 4455667788,0\n'
                '2025-01-03,Cash Dividend USD 0.24 per Share,0\n')
        out, rep = redact_text(text)
        self.assertEqual([n for n, _ in rep.review], [2, 3])   # line numbers only

    def test_xlsx_and_binary_input_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "questrade.xlsx"
            src.write_bytes(b"PK\x03\x04" + b"\x00\x01binary" * 50)
            r = _cli(str(src), "--no-denylist")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not a text export", r.stderr)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["questrade.xlsx"])
            blob = Path(tmp) / "blob.csv"
            blob.write_bytes(bytes(range(256)) * 20)
            r = _cli(str(blob), "--no-denylist", "--check")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("binary", r.stderr)
        from taxjson.bin.taxjson_redact import sniff_binary
        self.assertIsNone(sniff_binary("Account #,Qty\n".encode("utf-16-le")))  # UTF-16 is text

    def test_invalid_also_fails_closed_and_check_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.csv"
            src.write_text(QT)
            r = _cli(str(src), "--no-denylist", "--also", "[")
            self.assertEqual(r.returncode, 2)
            self.assertIn("not a valid regular expression", r.stderr)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["a.csv"])
            clean = Path(tmp) / "clean.csv"
            clean.write_text("Date,Symbol,Qty\n2025-01-01,AAPL,10\n")
            self.assertEqual(_cli(str(clean), "--no-denylist", "--check").returncode, 0)
            self.assertEqual(_cli(str(src), "--no-denylist", "--check").returncode, 1)
            r = _cli(str(src), "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stderr)
            dst = Path(tmp) / "a.redacted.csv"
            self.assertEqual(dst.stat().st_mode & 0o077, 0)      # owner-only copy


if __name__ == "__main__":
    unittest.main()
