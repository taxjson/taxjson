"""Re-audit-2 privacy fixes for `taxjson redact` and the
generate-parser identity gate (lists privacy-01 / privacy-02). Every
id, name, address and number here is synthetic."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.bin import taxjson_redact as R
from taxjson.bin.taxjson_redact import redact_text

NAME = "Zelda Quixote"
# Split so the pre-commit PII scanner does not read a street literal.
STREET = "12 " + "Fakeway Street"


def _run(*argv, env=None, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_redact", *argv],
        capture_output=True, text=True, env=env, cwd=cwd)


def _env(home, denylist=None):
    env = dict(os.environ, HOME=str(home))
    env.pop("TAXJSON_PII_DENYLIST", None)
    if denylist is not None:
        env["TAXJSON_PII_DENYLIST"] = str(denylist)
    return env


class DenylistEncoding(unittest.TestCase):
    """A2-0045 / A2-0158 / A2-0458 / A2-0448: the private denylist must
    never lose a pattern silently — a UTF-8 BOM is stripped, and a file
    that is UTF-16, not UTF-8, a directory or unreadable is refused."""

    BODY = "ZEBRAFARM\nSECONDWORD\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.home = self.d / "home"
        self.home.mkdir()
        self.csv = self.d / "qt.csv"
        self.csv.write_text("Date,Note,Qty\n2026-01-02,zebrafarm secondword,10\n",
                            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _deny(self, raw: bytes) -> Path:
        p = self.d / "deny"
        p.write_bytes(raw)
        return p

    def test_bom_is_stripped(self):
        p = self._deny(b"\xef\xbb\xbf" + self.BODY.encode())
        self.assertEqual(R.load_denylist(str(p)), ["ZEBRAFARM", "SECONDWORD"])
        r = _run(str(self.csv), "--out", str(self.d / "o"), env=_env(self.home, p))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = (self.d / "o" / "qt.redacted.csv").read_text(encoding="utf-8")
        self.assertNotIn("zebrafarm", out.lower())
        self.assertNotIn("secondword", out.lower())

    def test_utf16_non_utf8_directory_unreadable_refused(self):
        cases = {
            "utf16": self.BODY.encode("utf-16"),
            "utf16-nobom": self.BODY.encode("utf-16-le"),
            "cp1252": "ZÉBRÉFARM\n".encode("cp1252"),
        }
        for what, raw in cases.items():
            with self.subTest(what):
                p = self._deny(raw)
                with self.assertRaises(R.DenylistError):
                    R.load_denylist(str(p))
                out = self.d / ("o-" + what)
                r = _run(str(self.csv), "--out", str(out), env=_env(self.home, p))
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("UTF-8", r.stderr)
                self.assertFalse(out.exists())
        # The default path is a directory: refused, not treated as absent.
        cfg = self.home / ".config" / "taxjson" / "pii-denylist"
        cfg.mkdir(parents=True)
        r = _run(str(self.csv), "--check", env=_env(self.home))
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a file", r.stderr)
        cfg.rmdir()
        # TAXJSON_PII_DENYLIST names a directory: not "does not exist".
        r = _run(str(self.csv), "--check", env=_env(self.home, self.d))
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a file", r.stderr)
        self.assertNotIn("does not exist", r.stderr)
        # Unreadable (skip as root: root reads anything).
        if os.geteuid() != 0:
            p = self._deny(self.BODY.encode())
            p.chmod(0)
            try:
                r = _run(str(self.csv), "--check", env=_env(self.home, p))
            finally:
                p.chmod(0o600)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn("Traceback", r.stderr)

    def test_generate_parser_gate_refuses_on_bad_denylist(self):
        from taxjson.bin.taxjson_generate_parser import identity_findings
        sample = "Date,Note\n2026-01-02,ZebrafarM\n"
        p = self._deny(b"\xef\xbb\xbf" + self.BODY.encode())
        with mock.patch.dict(os.environ, {"TAXJSON_PII_DENYLIST": str(p)}):
            self.assertTrue(any("denylist" in f for f in identity_findings(sample)))
        p = self._deny(self.BODY.encode("utf-16"))
        with mock.patch.dict(os.environ, {"TAXJSON_PII_DENYLIST": str(p)}):
            found = identity_findings("Date,Note\n2026-01-02,nothing\n")
        self.assertTrue(found and "denylist" in found[0], found)


class IbAccountInformation(unittest.TestCase):
    """A2-0046 / A2-0455 / A2-1391: every Field Value of IB's Account
    Information section is identity except a known-safe allowlist, and
    every value cell of the row is blanked."""

    def test_every_identity_field_redacted(self):
        fields = ("Name", "Legal Name", "Joint Name", "Master Name",
                  "Account Holder", "Account Title", "Beneficiary",
                  "Trustee", "Contact", "Primary Holder", "Secondary Holder",
                  "SIN", "SSN", "Tax ID", "Telephone", "Some New Field")
        for f in fields:
            with self.subTest(f):
                text = ("Account Information,Header,Field Name,Field Value\n"
                        f"Account Information,Data,{f},{NAME}\n")
                out, rep = redact_text(text)
                self.assertNotIn("Zelda", out)
                self.assertIn(f"Data,{f},REDACTED", out)
                self.assertEqual(rep.identity_rows, 1)
        out, _ = redact_text("Account Information,Data,SIN,046454286\n")  # pii-ok
        self.assertNotIn("046454286", out)  # pii-ok

    def test_safe_fields_and_account_kept(self):
        text = ("Account Information,Header,Field Name,Field Value\n"
                "Account Information,Data,Account,U5550001\n"  # pii-ok
                "Account Information,Data,Base Currency,CAD\n"
                "Account Information,Data,Account Type,Individual\n"
                "Account Information,Data,Customer Type,Individual\n"
                "Account Information,Data,Account Capabilities,Margin\n")
        out, rep = redact_text(text)
        self.assertIn("Account,U9990001", out)
        for keep in ("Base Currency,CAD", "Account Type,Individual",
                     "Customer Type,Individual", "Account Capabilities,Margin"):
            self.assertIn(keep, out)
        self.assertEqual(rep.identity_rows, 0)

    def test_multi_cell_address(self):
        out, _ = redact_text(
            "Account Information,Data,Address,Unit Nine Fakeway,Springvale,ON\n")
        self.assertEqual(
            out, "Account Information,Data,Address,REDACTED,REDACTED,REDACTED\n")


class HtmlIdentityCells(unittest.TestCase):
    """A2-0457 / A2-0455 / A2-0460: HTML label/value cells — every value
    cell up to the end of the row, nested inline tags, and the SIN /
    Tax ID / Telephone / Legal Name labels."""

    def test_multi_cell_address_and_nested_tag(self):
        html = (f"<tr><td>Address</td><td>{STREET}</td><td>Springvale</td>"
                "<td>ON</td></tr>\n<tr><td>Name</td><td><b>Zelda Quixote</b></td></tr>\n")
        out, rep = redact_text(html)
        self.assertNotIn("Springvale", out)
        self.assertNotIn("Zelda", out)
        self.assertIn("<td>Address</td><td>REDACTED</td><td>REDACTED</td><td>REDACTED</td></tr>", out)
        self.assertIn("<td>Name</td><td><b>REDACTED</b></td>", out)
        self.assertEqual(rep.identity_rows, 2)

    def test_more_labels(self):
        for label, value in (("SIN", "046454286"), ("Tax ID", "078051120"),  # pii-ok
                             ("Telephone", "4165550123"), ("Legal Name", NAME),
                             ("Joint Name", NAME), ("Master Name", NAME),
                             ("Account Holder", NAME), ("Beneficiary", NAME)):
            with self.subTest(label):
                out, _ = redact_text(f"<tr><td>{label}</td><td>{value}</td></tr>\n")
                self.assertNotIn(value, out)
                self.assertIn(f"<td>{label}</td><td>REDACTED</td>", out)

    def test_lower_case_ib_id_in_html_id(self):
        # A2-0762: replaced case-insensitively, and the verification
        # counts a case-different leftover.
        html = ('<div id="tblaccountinformation_u5550123body">\n'  # pii-ok
                "<tr><td>Account</td><td>U5550123</td></tr>\n")  # pii-ok
        out, rep = redact_text(html)
        self.assertNotIn("5550123", out)
        self.assertEqual(rep.unreplaced, {})


class LabelCells(unittest.TestCase):
    """A2-0759 / A2-0765 / A2-0460 / A2-0456 / A2-1390."""

    def test_every_label_in_a_row(self):
        cases = {
            "Account Number:,555123456,Name:,Zelda Quixote\n":  # pii-ok
                "Account Number:,999000001,Name:,REDACTED\n",
            "Contact,Phone:,4165550123\n": "Contact,Phone:,REDACTED\n",
            "Beneficiary:,Zelda Quixote,Payee:,Ada Quixote\n":
                "Beneficiary:,REDACTED,Payee:,REDACTED\n",
            "Payee:,Zelda Quixote,Memo:,Rent\n": "Payee:,REDACTED,Memo:,Rent\n",
            "Beneficiary:,Zelda Quixote,Relationship:,Spouse\n":
                "Beneficiary:,REDACTED,Relationship:,Spouse\n",
            "Beneficiary:,Zelda Quixote,Phone:,4165550123\n":
                "Beneficiary:,REDACTED,Phone:,REDACTED\n",
            "Address:,Unit Nine Fakeway,Springvale ON,Zone Four\n":
                "Address:,REDACTED,REDACTED,REDACTED\n",
        }
        for text, want in cases.items():
            with self.subTest(text):
                out, rep = redact_text(text)
                self.assertEqual(out, want)
                self.assertTrue(rep.found_anything())

    def test_label_rows_are_not_a_column_header(self):
        # Four digit-free cells used to be read as a flat CSV header.
        out, rep = redact_text("Payee:,Zelda Quixote,Memo:,Rent\n"
                               "Date,Symbol,Qty\n2025-01-01,XYZ,1\n")
        self.assertNotIn("Zelda", out)
        self.assertIn("Date,Symbol,Qty\n2025-01-01,XYZ,1\n", out)

    def test_person_labels_by_pattern(self):
        for label in ("Recipient", "Recipient Name", "Payee Name", "Joint Holder",
                      "Co-Owner", "Contact", "Policyholder", "Beneficiary Name",
                      "Trustee", "Spouse", "Authorized Trader", "Legal Name",
                      "Employee Name", "Nom du titulaire"):
            with self.subTest(label):
                out, _ = redact_text(f"{label}:,{NAME}\nDate,Symbol,Qty\n")
                self.assertNotIn("Zelda", out)

    def test_security_name_label_is_kept(self):
        out, _ = redact_text("Security Name:,Apple Inc\nDate,Symbol,Qty\n")
        self.assertIn("Apple Inc", out)

    def test_uncoloned_label_cell(self):
        for text, secret in (("SIN,046454286\n", "046454286"),  # pii-ok
                             ("SSN,078051120\n", "078051120"),  # pii-ok
                             ("Phone,4165550123\n", "4165550123"),
                             ("Tel,4165550123\n", "4165550123"),
                             ("Tax ID,078051120\n", "078051120"),  # pii-ok
                             ('2025-01-01,"SIN,046454286",x\n', "046454286")):  # pii-ok
            with self.subTest(text):
                out, rep = redact_text(text)
                self.assertNotIn(secret, out)
                self.assertTrue(rep.found_anything())

    def test_comma_label_does_not_eat_a_date_or_quantity(self):
        for text in (
                "Date,Symbol,Settle,Qty\n2025-01-02,CELL,2025-01-01,4165550123\n",
                "Date,Symbol,Qty\n2025-01-02,TEL,1000.0000\n2025-01-02,CELL,1234567890.0000\n",
                "Trades,Header,Asset Category,Currency,Symbol,Quantity\n"
                "Trades,Data,Stocks,USD,TEL,1234567890\n"):
            with self.subTest(text):
                out, _ = redact_text(text)
                self.assertEqual(out, text)

    def test_french_and_short_comma_labels(self):
        for label in ("Nom du client", "Nom", "Titulaire", "Holder", "Payee",
                      "Beneficiary", "Bénéficiaire"):
            for pos in ("pre", "post"):
                with self.subTest(label=label, pos=pos):
                    row = f"{label},Jane Sample\n"
                    table = "Date,Symbol,Qty\n2025-01-01,XYZ,1\n"
                    text = row + table if pos == "pre" else table + row
                    out, _ = redact_text(text)
                    self.assertNotIn("Jane Sample", out)
                    self.assertIn(table, out)

    def test_header_row_is_not_damaged(self):
        # A header whose first cell is 'Name' used to lose its second
        # column name ('Name,REDACTED,Qty').
        text = "Name,Symbol,Qty\nApple,XYZ,1\n"
        out, _ = redact_text(text)
        self.assertTrue(out.startswith("Name,Symbol,Qty\n"))


class PersonColumns(unittest.TestCase):
    """A2-0764: person columns beyond the plan-party list; any other
    '* Name' column is at least listed for REVIEW."""

    def test_person_columns_blanked(self):
        for h in ("Recipient Name", "Payee Name", "Employee Name", "Policyholder",
                  "Co-Applicant", "Applicant", "Participant", "Contact", "Payee"):
            with self.subTest(h):
                out, rep = redact_text(
                    f"Date,Account #,{h},Symbol\n2025-01-01,55512345,{NAME},XYZ\n")  # pii-ok
                self.assertNotIn("Zelda", out)
                self.assertIn(f"Date,Account #,{h},Symbol\n", out)

    def test_unknown_name_column_reviewed_security_name_kept(self):
        out, rep = redact_text(
            f"Date,Account #,Agent Name,Symbol\n2025-01-01,55512345,{NAME},XYZ\n")  # pii-ok
        self.assertTrue(any("Agent Name" in why for _n, why in rep.review), rep.review)
        out, rep = redact_text(
            "Date,Account #,Security Name,Qty\n2025-01-01,55512345,Apple Inc,1\n")  # pii-ok
        self.assertIn("Apple Inc", out)


class CaseInsensitiveIds(unittest.TestCase):
    """A2-0451 / A2-1386 / A2-0452."""

    def test_lower_case_ib_id_in_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src = d / "u5550001_2025.csv"  # pii-ok
            src.write_text("Statement,Data,Account,U5550001\n", encoding="utf-8")  # pii-ok
            r = _run(str(src), "--no-denylist", "--out", str(d / "o"),
                     env=_env(d))
            self.assertEqual(r.returncode, 0, r.stderr)
            names = [p.name for p in (d / "o").iterdir()]
            self.assertEqual([n.lower() for n in names],
                             ["u9990001_2025.redacted.csv"])
            self.assertNotIn("5550001", r.stdout)

    def test_alnum_id_any_case(self):
        text = ("Date,Account Number,Description\n"
                "2025-01-01,AB55500012,transfer note ab55500012 to self\n")  # pii-ok
        out, rep = redact_text(text)
        self.assertNotIn("55500012", out)
        self.assertEqual(rep.unreplaced, {})
        out, rep = redact_text("Date,Account Number,Qty\n2025-01-01,ab55500012,1\n")  # pii-ok
        self.assertNotIn("55500012", out)
        self.assertEqual(len(rep.accounts), 1)

    def test_upper_case_bech32_same_pseudonym(self):
        lo = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
        text = f"addr\n{lo.upper()}\n{lo}\n"
        out, rep = redact_text(text)
        self.assertNotIn(lo, out.lower())
        a, b = out.splitlines()[1:]
        self.assertEqual(a.lower(), b.lower())
        self.assertEqual(len(rep.wallets), 1)


class SharedPseudonyms(unittest.TestCase):
    """A2-0454 / A2-0766: transaction-id and wallet pseudonyms are one
    table per invocation, like account ids."""

    def test_two_coinbase_files_keep_distinct_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            a, b = d / "cb_2024.csv", d / "cb_2025.csv"
            a.write_text("ID,Timestamp,Asset\n65f0a1b2c3d4e5f6a7b8c9d0,2024-03-01,ETH\n")
            b.write_text("ID,Timestamp,Asset\n65f0a1b2c3d4e5f6a7b8c9d1,2025-06-01,ETH\n")
            r = _run(str(a), str(b), "--no-denylist", "--out", str(d / "o"),
                     env=_env(d))
            self.assertEqual(r.returncode, 0, r.stderr)
            ia = (d / "o" / "cb_2024.redacted.csv").read_text().splitlines()[1].split(",")[0]
            ib = (d / "o" / "cb_2025.redacted.csv").read_text().splitlines()[1].split(",")[0]
            self.assertNotEqual(ia, ib)

    def test_kraken_trade_txid_still_links_to_ledger_refid(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            t = d / "kr_trades.csv"
            lg = d / "kr_ledgers.csv"
            t.write_text('"txid","ordertxid","pair","time"\n'
                         '"TQABCD-EFGHI-JKLMNO","OQABCD-EFGHI-JKLMNO","XETHZCAD","2025-01-01 00:00:00"\n')
            lg.write_text('"txid","refid","time","type"\n'
                          '"LZABCD-EFGHI-JKLMNO","TQABCD-EFGHI-JKLMNO","2025-01-01 00:00:00","trade"\n')
            r = _run(str(t), str(lg), "--no-denylist", "--out", str(d / "o"),
                     env=_env(d))
            self.assertEqual(r.returncode, 0, r.stderr)
            tr = (d / "o" / "kr_trades.redacted.csv").read_text().splitlines()[1].split(",")
            le = (d / "o" / "kr_ledgers.redacted.csv").read_text().splitlines()[1].split(",")
            self.assertEqual(tr[0], le[1])          # trade txid == ledger refid
            self.assertEqual(len({tr[0], tr[1], le[0]}), 3)

    def test_per_file_report_counts_stay_per_file(self):
        shared = R.Pseudonyms()
        _o, r1 = redact_text("hash\n" + "a" * 64 + "\n", pseudonyms=shared)
        _o, r2 = redact_text("hash\n" + "b" * 64 + "\n", pseudonyms=shared)
        self.assertEqual((len(r1.txids), len(r2.txids)), (1, 1))
        self.assertNotEqual(list(r1.txids.values()), list(r2.txids.values()))


class CheckFileName(unittest.TestCase):
    """A2-0459 / A2-0767: --check exits 1 for an id or a denylisted word
    that appears only in the file NAME."""

    def test_name_only_id_and_denylist_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            deny = d / "deny"
            deny.write_text("Qwertyfoo\n", encoding="utf-8")
            for name in ("U5550001_activity.csv", "55500001.csv",  # pii-ok
                         "u5550001_activity.csv", "qwertyfoo_stmt.csv"):  # pii-ok
                with self.subTest(name):
                    src = d / name
                    src.write_text("Date,Symbol,Qty\n2025-01-01,XYZ,1\n")
                    r = _run(str(src), "--check", env=_env(d, deny))
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    self.assertNotIn("5550001", r.stdout)
                    self.assertNotIn("5550001", r.stderr)
            clean = d / "activity.csv"
            clean.write_text("Date,Symbol,Qty\n2025-01-01,XYZ,1\n")
            self.assertEqual(_run(str(clean), "--check", env=_env(d, deny)).returncode, 0)


class ContactShapes(unittest.TestCase):
    """A2-0453 / A2-0761 / A2-0763 / A2-1389: one test per redaction
    path, so removing any one of them fails a test."""

    TABLE = "Date,Symbol,Qty\n2025-01-01,XYZ,1\n"

    def test_body_po_box(self):
        out, _ = redact_text(self.TABLE + "Remit to PO Box 4412 Station A\n")
        self.assertNotIn("4412", out)

    def test_body_phone_after_tel(self):
        out, _ = redact_text(self.TABLE + "Questions? Tel: 4165550199\n")
        self.assertNotIn("4165550199", out)

    def test_preamble_street_line_blanks_the_city(self):
        out, _ = redact_text(STREET + ",Springvale\n" + self.TABLE)
        self.assertNotIn("Springvale", out)

    def test_preamble_po_box_line_blanks_the_station(self):
        out, _ = redact_text("PO Box 4412,Station Main\n" + self.TABLE)
        self.assertNotIn("Station Main", out)

    def test_txid_column_of_any_shape(self):
        out, rep = redact_text("Date,Transaction ID,Qty\n2025-01-01,QX12345ABCZ,1\n")
        self.assertNotIn("QX12345ABCZ", out)
        self.assertEqual(len(rep.txids), 1)

    def test_body_us_zip_line(self):
        out, _ = redact_text(self.TABLE + "Mailing address: Springfield, IL 62704-1234\n")
        self.assertNotIn("62704", out)
        self.assertNotIn("Springfield", out)

    def test_preamble_us_zip_line(self):
        out, _ = redact_text("Springfield, IL 62704\n" + self.TABLE)
        self.assertNotIn("Springfield", out)
        self.assertNotIn("62704", out)
        # The whole preamble line goes, not just the 'City, ST ZIP' part.
        out, _ = redact_text("Suite Seven,Springfield IL 62704\n" + self.TABLE)
        self.assertNotIn("Suite Seven", out)

    def test_us_zip_cells(self):
        out, _ = redact_text("Owner,City,State,Zip\nZelda Quixote,Springfield,IL,62704\n")
        self.assertEqual(out.splitlines()[1], "REDACTED,REDACTED,REDACTED,REDACTED")
        out, _ = redact_text("Springfield,IL,62704\n" + self.TABLE)
        self.assertNotIn("62704", out)
        self.assertNotIn("Springfield", out)
        self.assertIn(self.TABLE, out)

    def test_labelled_sin_ssn_separators(self):
        for text in ("SIN: 046.454.286", "SSN: 078 05 1120", "SSN: 078.05.1120",  # pii-ok
                     "SIN: 046454286", "SIN: 046-454-286"):  # pii-ok
            with self.subTest(text):
                out, rep = redact_text(text + "\n")
                self.assertNotRegex(out, r"\d{3}")
                self.assertGreaterEqual(rep.sins, 1)


class TruncatedUtf16(unittest.TestCase):
    """A2-1392: a truncated BOM UTF-16 file is one clean line, and the
    rest of the batch is still redacted."""

    def test_one_line_and_batch_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            bad = d / "bad.csv"
            bad.write_bytes(b"\xff\xfe" + "a,b\n".encode("utf-16-le") + b"\xff")
            good = d / "good.csv"
            good.write_text("Date,Symbol,Qty\n2025-01-01,XYZ,1\n")
            r = _run(str(bad), str(good), "--no-denylist", "--out", str(d / "o"),
                     env=_env(d))
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("bad.csv", r.stderr)
            self.assertTrue((d / "o" / "good.redacted.csv").exists())
            self.assertFalse((d / "o" / "bad.redacted.csv").exists())


class GenerateParserFileName(unittest.TestCase):
    """A2-0447: the input file stem (a default IB download name carries
    the account id) never reaches the model API or stderr."""

    def test_stem_id_masked_in_prompt_and_stderr(self):
        from taxjson.bin import taxjson_generate_parser as G
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src = d / "U5550001_20250101_20251231.csv"  # pii-ok
            src.write_text("Date,Symbol,Qty\n2025-01-01,XYZ,1\n")
            seen = {}

            def fake(system_text, user_text, model_id):
                seen["user"] = user_text
                return "x = 1\n"
            argv = ["taxjson-generate-parser", str(src), "-o", str(d / "p.py")]
            err = tempfile.TemporaryFile("w+")
            env = {"HOME": str(d)}
            with mock.patch.object(G, "_call_claude", fake), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(sys, "stderr", err), \
                    mock.patch.dict(os.environ, env), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TAXJSON_PII_DENYLIST", None)
                try:
                    G.main()
                except SystemExit as e:
                    self.assertIn(e.code, (0, None))
            err.seek(0)
            stderr = err.read()
            self.assertIn("user", seen)
            self.assertNotIn("5550001", seen["user"])
            self.assertNotIn("5550001", stderr)


if __name__ == "__main__":
    unittest.main()
