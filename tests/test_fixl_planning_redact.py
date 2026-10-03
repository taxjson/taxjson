"""Planning low round: `taxjson redact` findings.

Redact
  R1-352  a line the csv module cannot split still has its account id
          collected; a file-name id keeps the content's placeholder
  S036-14 a US 'City, ST ZIP' preamble line is blanked
  S036-15 the street regex never swallows the next CSV field
  S036-18 one invocation never gives two accounts (or two files) the same
          pseudonym / output name
  S036-23 `broker_account = "..."` / `account = "..."` keys are ids
  S036-24 / S037-01  an unreadable input or --out is one line, and the
          rest of the batch is still redacted
  S037-03 address columns of a columnar AccountInformation section
  S037-08 Phone:/SIN:/Payee:/Beneficiary: label cells redact the next cell

All data is synthetic.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Split so the source carries no address/postal literal for PII scanners.
_BAKER = "221 " + "Baker St"
_MAIN = "123 " + "Main St"
_ELM = "12 " + "Elm Street"
_POST = "S6H" + " 1A1"


# =================================================================== redact
class TestRedactLow(unittest.TestCase):
    def _rt(self, text):
        from taxjson.bin.taxjson_redact import redact_text
        return redact_text(text)

    def test_unsplittable_line_still_collects_its_id(self):   # R1-352 (a)
        text = ("Date,Account #,Description,Amount\n"
                "2025-01-02,55511112,short,1.00\n"
                "2025-01-03,55522224," + "x" * 131082 + ",2.00\n")
        out, rep = self._rt(text)
        self.assertNotIn("55522224", out)
        self.assertEqual(len(rep.accounts), 2)
        self.assertEqual(rep.unreplaced, {})

    def test_file_name_keeps_the_content_placeholder(self):   # R1-352 (b)
        from taxjson.bin.taxjson_redact import redacted_name, redact_text
        _, rep = redact_text("Trades,Header,Account\nTrades,Data,U55512345\n")  # pii-ok
        ph = rep.accounts["U55512345"]  # pii-ok
        for name in ("U55512345_2025.csv", "U55512345.2025.dividends.csv"):  # pii-ok
            got = redacted_name(Path(name), rep.accounts)
            self.assertIn(ph, got)
            self.assertNotIn("U55512345", got)  # pii-ok
        # A name-only 8-digit id gets its own placeholder, not a renumber
        # of the placeholder digits.
        got = redacted_name(Path("U55512345_55599999.csv"), rep.accounts)  # pii-ok (synthetic)
        self.assertIn(ph, got)
        self.assertNotIn("55599999", got)

    def test_us_city_state_zip_preamble(self):                # S036-14
        text = ("Jane Sample\n" + _MAIN + "\nSpringfield, IL 62704\n"
                "Date,Symbol,Quantity,Amount\n2025-01-02,AAPL,10,-1500\n")
        out, _ = self._rt(text)
        self.assertNotIn("Springfield", out)
        self.assertNotIn("62704", out)

    def test_street_does_not_swallow_the_next_field(self):    # S036-15
        for line in ("AccountInformation,Data,U55500001,Main," + _BAKER + ","  # pii-ok
                     "Unit 5,Moose Jaw\n",
                     "2025-01-02," + _ELM + ",Apt 3,100.00\n",
                     "2025-01-02,Unit 5," + _BAKER + ",100.00\n"):
            out, _ = self._rt("a,b,c,d\n" + line)
            self.assertEqual(out.splitlines()[1].count(","),
                             line.count(","), (line, out))
            self.assertNotIn("Baker", out)
            self.assertNotIn("Elm Street", out)
        # Inside ONE field the unit is still part of the address.
        out, _ = self._rt('a,b\n2025-01-02,"' + _BAKER + ', Unit 5"\n')
        self.assertNotIn("Unit 5", out)

    def test_broker_account_key(self):                        # S036-23
        text = ('[meta]\nbroker = "questrade"\n'
                'broker_account = "55576543"\n'  # pii-ok
                '[[holding]]\naccount = "55576544"\nsymbol = "XIU.TO"\n'  # pii-ok
                '{"account_id": "55576545", "note": "x"}\n')  # pii-ok
        out, rep = self._rt(text)
        for v in ("55576543", "55576544", "55576545"):
            self.assertNotIn(v, out)
        self.assertEqual(len(rep.accounts), 3)
        # A plain name value is not an id.
        out, rep = self._rt('account = "margin"\n')
        self.assertIn('"margin"', out)
        self.assertEqual(rep.accounts, {})

    def test_flex_account_information_address_columns(self):  # S037-03
        hdr = ("ClientAccountID,AccountAlias,Name,AccountType,Street,Street2,"
               "City,State,Country,PostalCode,PrimaryEmail,Currency\n")
        row = ("U55500001,margin-1,Zelda Quixote,Individual," + _BAKER + ","  # pii-ok
               "Unit 5,Moose Jaw,SK,Canada," + _POST + ",z@example.com,CAD\n")
        for text in ("AccountInformation,Header," + hdr
                     + "AccountInformation,Data," + row, hdr + row):
            out, _ = self._rt(text)
            data_line = out.splitlines()[1]
            for v in ("Zelda", "Unit 5", "Moose Jaw", ",SK,", "Canada",
                      "margin-1"):
                self.assertNotIn(v, data_line, (v, data_line))
            self.assertIn("CAD", data_line)
            self.assertEqual(data_line.count(","),
                             text.splitlines()[1].count(","))

    def test_label_cell_contact_values(self):                 # S037-08
        for line, val in (("Phone:,4165550123", "4165550123"),
                          ("SIN:,046454286", "046454286"),  # pii-ok (CRA sample SIN)
                          ("Payee:,Zelda Quixote", "Zelda"),
                          ("Beneficiary:,Zelda Quixote", "Zelda")):
            out, rep = self._rt(line + "\n")
            self.assertNotIn(val, out, line)
            self.assertTrue(rep.found_anything(), line)


class TestRedactBatch(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_redact",
             "--no-denylist", *args], capture_output=True, text=True,
            cwd=REPO_ROOT)

    def test_two_accounts_two_names_two_pseudonyms(self):     # S036-18
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            a = t / "U5551111_2025.csv"   # pii-ok (synthetic)
            b = t / "U5552222_2025.csv"   # pii-ok (synthetic)
            a.write_text("Trades,Header,Account,Qty\n"
                         "Trades,Data,U5551111,1\n")   # pii-ok
            b.write_text("Trades,Header,Account,Qty\n"
                         "Trades,Data,U5552222,2\n")   # pii-ok
            r = self._run("--force", "--out", str(t / "out"), str(a), str(b))
            self.assertEqual(r.returncode, 0, r.stderr)
            outs = sorted((t / "out").iterdir())
            self.assertEqual(len(outs), 2, outs)
            texts = [p.read_text() for p in outs]
            ids = [ln.split(",")[2] for tx in texts
                   for ln in tx.splitlines()[1:]]
            self.assertEqual(len(set(ids)), 2, ids)

    def test_same_basename_is_not_overwritten(self):          # S036-18
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for n in ("a", "b"):
                (t / n).mkdir()
                (t / n / "activity.csv").write_text(f"Date,Qty\n2025,{n}\n")
            r = self._run("--force", "--out", str(t / "out"),
                          str(t / "a" / "activity.csv"),
                          str(t / "b" / "activity.csv"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("already written", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(len(list((t / "out").iterdir())), 1)

    @unittest.skipIf(os.geteuid() == 0, "root reads mode-000 files")
    def test_unreadable_input_does_not_stop_the_batch(self):  # S037-01
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bad = t / "a_unreadable.csv"
            bad.write_text("Date,Qty\n2025,1\n")
            ok = t / "b_ok.csv"
            ok.write_text("Date,Qty\n2025,2\n")
            bad.chmod(0)
            try:
                r = self._run("--out", str(t / "out"), str(bad), str(ok))
            finally:
                bad.chmod(0o600)
            self.assertEqual(r.returncode, 2)       # A2-0164
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("a_unreadable.csv", r.stderr)
            self.assertTrue((t / "out" / "b_ok.redacted.csv").exists())

    @unittest.skipIf(os.geteuid() == 0, "root writes anywhere")
    def test_unwritable_out_is_one_line(self):                # S036-24
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            src = t / "a.csv"
            src.write_text("Date,Qty\n2025,1\n")
            ro = t / "ro"
            ro.mkdir()
            ro.chmod(0o555)
            try:
                r = self._run("--out", str(ro / "x"), str(src))
            finally:
                ro.chmod(0o755)
            self.assertEqual(r.returncode, 2)       # A2-0164
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("taxjson redact:", r.stderr)


if __name__ == "__main__":
    unittest.main()
