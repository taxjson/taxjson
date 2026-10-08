"""`taxjson slip-audit --import-cra`: T5 / T3 slip PDFs downloaded from
CRA My Account read into slips.toml and placed in an account by the
books (tax-logic CA-SLIP-04; lib/cra_slips).

The PDFs are synthetic (tests/_cra_pdf.py writes CRA's page layout as a
one-page PDF); the recipient's name, a box-shaped address line and a
SIN line are on each page and must never be read. Tests that need
pdftotext skip without it; the text parser is tested on its own.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _cra_pdf as CRA
from _style import CapturedWidth
from tax_rules import rule
from test_slip_audit import (HOLDER, IB_ACCT, _home, _ib_project, tj)

_WIDTH = CapturedWidth()
HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None
IB_NAME = ["INTERACTIVE BROKERS CANADA INC. INTERACTIVE",
           "COURTAGE CANADA INC."]


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_TEXT = """10/8/26, 9:00 AM                           2025 T5 Statement of Investment Income
  Signed in as ZED SYNTHETIC-HOLDER
  12        Recipient home                          Somewhere
  2025 T5 slip (amended) from SAMPLE TRUST COMPANY OF
  CANADA LTD.

    Box                   Box name                               Box value
    number
                          Actual amount of dividends other than
    10                                                              1,000.25
                          eligible dividends
    13                    Interest from Canadian sources              12.34
    23                    Recipient type                         Individual
    27                    Foreign currency                              USD
  Other information
    Box number            Box name                               Box value
    15                    Foreign income                               40.00
    16                    Foreign tax paid                              6.00
    99                    Something new                                 1.00
"""


class TestParse(unittest.TestCase):

    @rule("CA-SLIP-04")
    def test_text_layout(self):
        from taxjson.lib.cra_slips import parse_text
        s = parse_text(_TEXT, "x.pdf")
        self.assertEqual((s.year, s.type, s.status, s.currency),
                         (2025, "T5", "amended", "USD"))
        self.assertEqual(s.issuer, "SAMPLE TRUST COMPANY OF CANADA LTD.")
        self.assertEqual(s.boxes, {"10": 1000.25, "13": 12.34,
                                   "15": 40.0, "16": 6.0})
        # The name, the address (box-12 shaped) never get in.
        self.assertNotIn("SYNTHETIC", repr(s).replace("SAMPLE", ""))
        self.assertNotIn("Somewhere", repr(s))
        self.assertNotIn("12", s.boxes)

    def test_not_a_slip(self):
        from taxjson.lib.cra_slips import CraSlipError, parse_text
        for text in ("2025 RRSP contribution receipt\nAmount  100.00\n",
                     "2025 T5 slip (original) from X\n  27   Foreign "
                     "currency    CAD\n"):
            with self.assertRaises(CraSlipError):
                parse_text(text, "x.pdf")

    def test_issuer_to_broker(self):
        from taxjson.lib.cra_slips import broker_of_issuer
        self.assertEqual(broker_of_issuer(" ".join(IB_NAME)), "ib")
        self.assertEqual(broker_of_issuer(
            "RBC DIRECT INVESTING INC./RBC PLACEMENTS EN DIRECT INC."),
            "rbc_direct")
        self.assertIsNone(broker_of_issuer("SAMPLE TRUST COMPANY"))

    @unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext is not installed")
    def test_pdf(self):
        from taxjson.lib.cra_slips import read_pdf
        with tempfile.TemporaryDirectory() as td:
            p = CRA.t5(Path(td) / "t5.pdf", IB_NAME,
                       {"24": "1,494.50", "18": "20.50"},
                       other={"15": "48.15", "16": "7.94"})
            s = read_pdf(p)
            q = CRA.t3(Path(td) / "t3.pdf", ["ZZT SAMPLE INDEX ETF"],
                       {"49": "30.00"}, other={"25": "6.55", "42": "3.45"})
            t = read_pdf(q)
        self.assertEqual(s.issuer, " ".join(IB_NAME))
        self.assertEqual({k: v for k, v in s.boxes.items() if v},
                         {"24": 1494.5, "18": 20.5, "15": 48.15,
                          "16": 7.94})
        self.assertEqual({k: v for k, v in t.boxes.items() if v},
                         {"49": 30.0, "25": 6.55, "42": 3.45})
        for x in (s, t):
            self.assertNotIn(HOLDER, repr(x))
            self.assertNotIn("Somewhere", repr(x))


class TestPlace(unittest.TestCase):
    """One broker's slips shared out among its broker accounts by the
    books' payments; a T3 to its fund by amount."""

    @rule("CA-SLIP-04")
    def test_share_out(self):
        from taxjson.lib.cra_slips import CraSlip, Group, place
        from taxjson.lib.slip_audit import BookRow

        def row(h, sym, amt, cat="ca_div", src="a.csv"):
            return BookRow(account="margin", id=f"{h}{sym}{amt}",
                           action="DIVIDEND", symbol=sym,
                           root=sym.split(".")[0], currency="CAD",
                           native=amt, cad=amt, pay_date="2025-03-03",
                           tax_date="2025-03-03", source=src,
                           source_hash=h, category=cat, hashes=(h,))
        ga = Group("margin", ("a" * 10,), "rbc_direct", ("a.csv",),
                   [row("a" * 10, "ZZA.TO", 600.0),
                    row("a" * 10, "ZZF.TO", 90.0)])
        gb = Group("margin", ("b" * 10,), "rbc_direct", ("b.csv",),
                   [row("b" * 10, "ZZB.TO", 100.0, src="b.csv"),
                    row("b" * 10, "ZZC.TO", 50.0, src="b.csv")])
        rbc = "RBC DIRECT INVESTING INC."
        slips = [CraSlip("1.pdf", 2025, "T5", "original", rbc,
                         boxes={"24": 100.0}),
                 CraSlip("2.pdf", 2025, "T5", "original", rbc,
                         boxes={"24": 600.0}),
                 CraSlip("3.pdf", 2025, "T5", "original", rbc,
                         boxes={"24": 50.0}),
                 CraSlip("4.pdf", 2025, "T3", "original", "ZZF FUND",
                         boxes={"49": 80.0, "42": 10.0}),
                 CraSlip("5.pdf", 2025, "T5", "original", "SAMPLE BANK",
                         boxes={"13": 1.0})]
        got = {p.slip.shown: (p.group.sources if p.group else None,
                              p.security)
               for p in place(slips, [ga, gb], 2025, {})}
        self.assertEqual(got, {"1.pdf": (("b.csv",), ""),
                               "2.pdf": (("a.csv",), ""),
                               "3.pdf": (("b.csv",), ""),
                               "4.pdf": (("a.csv",), "ZZF.TO"),
                               "5.pdf": (None, "")})


@unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext is not installed")
class TestImportEndToEnd(unittest.TestCase):
    """IB's CRA slips imported beside IB's dividends report: placed in
    the IB account (its key), the report keeps only its payments (no
    double count), a bank's T5 is listed unplaced, a receipt skipped;
    --write appends once."""

    @rule("CA-SLIP-04", "CA-SLIP-01")
    def test_import_write_audit(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            cra = tmp / "cra"
            cra.mkdir()
            CRA.t5(cra / "2025 T5 IBKR.pdf", IB_NAME,
                   {"24": "494.50", "18": "20.50", "13": "12.34"},
                   other={"15": "48.15", "16": "7.94"})
            CRA.t3(cra / "2025 T3 ZZT.pdf", ["ZZT SAMPLE INDEX ETF"],
                   {"49": "30.00"}, other={"25": "6.55", "42": "3.45"})
            CRA.t5(cra / "2025 T5 Bank.pdf", ["SAMPLE SAVINGS BANK"],
                   {"13": "1.24"})
            CRA.write(cra / "2025 RRSP contribution receipt.pdf",
                      [(40, 700, "2025 RRSP contribution receipt"),
                       (40, 680, f"Signed in as {HOLDER}")])
            r = tj(root, home, "slip-audit", "--import-cra", str(cra))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Would add to inputs/slips/slips.toml", r.stdout)
            self.assertIn("2025 T5 Bank.pdf: no broker in the books",
                          r.stdout)
            self.assertIn("skipped 2025 RRSP contribution receipt.pdf",
                          r.stdout)
            self.assertNotIn(HOLDER, r.stdout + r.stderr)
            self.assertNotIn("Somewhere", r.stdout)
            self.assertFalse((root / "inputs" / "slips" / "slips.toml")
                             .exists())
            r = tj(root, home, "slip-audit", "--import-cra", str(cra),
                   "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            from taxjson.bin.taxjson_brokerage import hash_broker_account
            from taxjson.lib.tomlcompat import tomllib
            doc = tomllib.loads((root / "inputs" / "slips" / "slips.toml")
                                .read_text())
            got = {(s["type"], s.get("security"), s["broker_key"])
                   for s in doc["slip"]}
            key = hash_broker_account(IB_ACCT)
            self.assertEqual(got, {("T5", None, key), ("T3", "ZZT.TO", key)})
            # Again: nothing twice.
            r = tj(root, home, "slip-audit", "--import-cra", str(cra),
                   "--write")
            self.assertIn("already in slips.toml", r.stdout)
            self.assertIn("Nothing to add.", r.stdout)
            self.assertEqual(len(tomllib.loads(
                (root / "inputs" / "slips" / "slips.toml").read_text())
                ["slip"]), 2)

            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (acc,) = rep["accounts"]
            (g,) = acc["groups"]
            lines = {ln["category"]: ln for ln in g["buckets"][0]["lines"]}
            # The CRA boxes, once (IB's report: payments only).
            self.assertEqual(lines["ca_div"]["slip"], 524.5)
            self.assertEqual(lines["roc"]["slip"], 3.45)
            self.assertEqual(lines["interest"]["slip"], 12.34)
            self.assertEqual(lines["interest"]["status"], "ok")
            self.assertEqual(g["payments"]["matched"], 5)
            self.assertTrue(any("payments only" in s
                                for s in g["buckets"][0]["slips"]))
            # The same findings and suggestions as from the report.
            self.assertEqual(lines["cg_div"]["status"], "differs")
            self.assertEqual(
                [e["symbol"] for e in
                 rep["suggestions"]["capital_gains_dividends"]],
                ["ZZQ.TO"])
            (tt,) = rep["suggestions"]["tt_lines"]
            self.assertEqual(tt["lines"], [
                "ADJUST 2025-09-30 16:00:00 ZZT.TO CAD -3.45 type=roc",
                "DIVIDEND 2025-09-30 16:00:00 ZZT.TO 0 CAD 0 -3.45"])

    def test_write_needs_import(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _ib_project(tmp)
            home = _home(tmp)
            tj(root, home, "run", "--no-input")
            r = tj(root, home, "slip-audit", "--write")
            self.assertEqual(r.returncode, 2)
            self.assertIn("--import-cra", r.stderr)


if __name__ == "__main__":
    unittest.main()
