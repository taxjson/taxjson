"""Pre-release review of `taxjson slip-audit` and `--import-cra`
(v0.24.2): each slip counts once (two slips in one PDF, a slip read
twice, an amended slip and its original, a typed T5 beside IB's report,
two copies of IB's report), an issuer is never placed at another
institution's broker, slips.toml carries a salted broker key, the
issuer is read from its own lines only, a USD-base IB report is
compared in CAD, and the small parser / message fixes.

Synthetic data only (tests/_cra_pdf.py pages; the fake IB ids of
test_slip_audit, a made-up holder name).
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cra_pdf as CRA
from _style import CapturedWidth
from tax_rules import rule
from test_slip_audit import (HOLDER, IB_ACCT, _home, _ib_project,
                             _ib_report, tj)
from test_slip_audit_cra import IB_NAME

_WIDTH = CapturedWidth()
HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None
HAVE_QPDF = shutil.which("qpdf") is not None
IB_ACCT2 = "U5550002"  # pii-ok (synthetic)


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _page(typ, issuer_lines, rows, status="original", year=2025):
    """A CRA page's pdftotext text: a browser header and the recipient's
    details above the slip line, then the box table."""
    head = (f"10/8/26, 9:00 AM        {year} {typ} Statement\n"
            f"  Signed in as ZED SYNTHETIC-HOLDER\n"
            f"  12        Recipient home              Somewhere\n")
    body = f"  {year} {typ} slip ({status}) from {issuer_lines[0]}\n"
    body += "".join(f"  {x}\n" for x in issuer_lines[1:])
    body += "\n    Box        Box name                  Box value\n    number\n"
    body += "".join(f"    {b}         {name}         {v}\n"
                    for b, name, v in rows)
    return head + body


T3_PAGE = _page("T3", ["ZZT SAMPLE INDEX ETF"], [
    ("21", "Capital gains", "2.00"), ("24", "Foreign business income",
                                      "7.00"),
    ("49", "Actual amount of eligible dividends", "30.00"),
    ("42", "Amounts resulting in cost base adjustment", "3.45")])
T5_PAGE = _page("T5", ["SAMPLE SPLIT CORP"], [
    ("24", "Actual amount of eligible dividends", "100.00"),
    ("18", "Capital gains dividends", "5.00"),
    ("15", "Foreign income", "40.00"), ("16", "Foreign tax paid", "6.00")])


class TestTwoSlipsInOneText(unittest.TestCase):
    """H1: a PDF of two slips (pages joined, a qpdf merge) is two
    slips, each read from its own lines."""

    @rule("CA-SLIP-04")
    def test_form_feed_pages(self):
        from taxjson.lib.cra_slips import CraSlipError, parse_slips, \
            parse_text
        got = parse_slips(T3_PAGE + "\f" + T5_PAGE, "both.pdf")
        self.assertEqual([(s.type, s.issuer, s.shown) for s in got],
                         [("T3", "ZZT SAMPLE INDEX ETF", "both.pdf #1"),
                          ("T5", "SAMPLE SPLIT CORP", "both.pdf #2")])
        t3, t5 = got
        self.assertEqual(t3.boxes, {"21": 2.0, "24": 7.0, "49": 30.0,
                                    "42": 3.45})
        # The T5's box 24 is its own (100), never the T3's (7); its boxes
        # 15/16/18 are kept.
        self.assertEqual(t5.boxes, {"24": 100.0, "18": 5.0, "15": 40.0,
                                    "16": 6.0})
        # No form feed: still cut at the second slip line.
        self.assertEqual(len(parse_slips(T3_PAGE + T5_PAGE, "x")), 2)
        with self.assertRaises(CraSlipError):
            parse_text(T3_PAGE + T5_PAGE, "x")

    def test_a_slip_read_twice_in_one_section_is_refused(self):
        from taxjson.lib.cra_slips import CraSlipError, parse_slips
        page = T5_PAGE + "    24         Actual amount         9.00\n"
        with self.assertRaises(CraSlipError) as cm:
            parse_slips(page, "x.pdf")
        self.assertIn("box 24 twice", str(cm.exception))

    def test_continuation_page_without_slip_line(self):
        from taxjson.lib.cra_slips import parse_slips
        two = T5_PAGE.replace("    15         Foreign income", "\f    "
                              "15         Foreign income")
        (s,) = parse_slips(two, "x.pdf")
        self.assertEqual(s.boxes["15"], 40.0)

    @unittest.skipUnless(HAVE_PDFTOTEXT and HAVE_QPDF,
                         "pdftotext / qpdf not installed")
    @rule("CA-SLIP-04")
    def test_qpdf_merged_pdf(self):
        from taxjson.lib.cra_slips import read_pdf
        with tempfile.TemporaryDirectory() as td:
            a = CRA.t3(Path(td) / "a.pdf", ["ZZT SAMPLE INDEX ETF"],
                       {"49": "30.00"}, other={"25": "6.55", "42": "3.45"})
            b = CRA.t5(Path(td) / "b.pdf", ["SAMPLE SPLIT CORP"],
                       {"24": "100.00", "18": "5.00"},
                       other={"15": "40.00", "16": "6.00"})
            m = Path(td) / "merged.pdf"
            subprocess.run(["qpdf", "--empty", "--pages", str(a), str(b),
                            "--", str(m)], check=True)
            t3, t5 = read_pdf(m)
        self.assertEqual((t3.type, t5.type), ("T3", "T5"))
        self.assertEqual({k: v for k, v in t3.boxes.items() if v},
                         {"49": 30.0, "25": 6.55, "42": 3.45})
        self.assertEqual({k: v for k, v in t5.boxes.items() if v},
                         {"24": 100.0, "18": 5.0, "15": 40.0, "16": 6.0})


class TestIssuerAndLayout(unittest.TestCase):
    """M5 and the parser lows: the issuer is the slip line's name and
    one more name-like line; box 27 wordings; '(Original)'; a French
    page; pdftotext's failure names a masked file."""

    def test_issuer_stops_at_recipient_lines(self):
        from taxjson.lib.cra_slips import parse_slips
        for extra in ("ZED SYNTHETIC-HOLDER", "123 SAMPLE STREET",
                      # A postal-code shape (made up; built so no
                      # scanner reads it as one).
                      "SOMEWHERE ON " + "Z9Z" + " 9" + "Z9",
                      "Recipient: ZED",
                      "Signed in as ZED"):
            text = _page("T5", ["SAMPLE TRUST COMPANY OF", "CANADA LTD.",
                                extra],
                         [("13", "Interest", "1.00")])
            (s,) = parse_slips(text, "x.pdf")
            self.assertEqual(s.issuer, "SAMPLE TRUST COMPANY OF CANADA LTD.")
            for bad in ("ZED", "123", "Z9Z", "SOMEWHERE"):
                self.assertNotIn(bad, s.issuer)

    def test_box27_wordings_and_status_case(self):
        from taxjson.lib.cra_slips import CraSlipError, parse_slips
        for word, want in (("USD", "USD"), ("U.S. dollars", "USD"),
                           ("US$", "USD"), ("CAD", "CAD"),
                           ("Canadian dollars", "CAD")):
            text = _page("T5", ["SAMPLE CORP"],
                         [("13", "Interest", "1.00"),
                          ("27", "Foreign currency", word)],
                         status="Original")
            (s,) = parse_slips(text, "x.pdf")
            self.assertEqual((s.currency, s.status), (want, "original"))
        with self.assertRaises(CraSlipError) as cm:
            parse_slips(_page("T5", ["SAMPLE CORP"],
                              [("13", "Interest", "1.00"),
                               ("27", "Foreign currency", "Zorkmids")]),
                        "x.pdf")
        self.assertIn("box 27", str(cm.exception))

    def test_french_page_and_unknown_status(self):
        from taxjson.lib.cra_slips import CraSlipError, parse_slips
        fr = ("  Feuillet T5 de 2025 (original) de SAMPLE CORP\n"
              "    13     Intérêts de source canadienne     1,00\n")
        with self.assertRaises(CraSlipError) as cm:
            parse_slips(fr, "x.pdf")
        self.assertIn("French", str(cm.exception))
        with self.assertRaises(CraSlipError) as cm:
            parse_slips(_page("T5", ["SAMPLE"], [("13", "I", "1.00")],
                              status="draft"), "x.pdf")
        self.assertIn("(draft)", str(cm.exception))

    def test_pdftotext_failure_masks_the_file_name(self):
        from taxjson.lib import cra_slips as CS
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / f"{IB_ACCT} T5.pdf"
            p.write_bytes(b"%PDF-1.4\n")
            with mock.patch.object(CS.shutil, "which",
                                   return_value=str(Path(td) / "nope")):
                with self.assertRaises(CS.CraSlipError) as cm:
                    CS.read_pdf(p)
        msg = str(cm.exception)
        self.assertIn("U5***", msg)
        self.assertNotIn(IB_ACCT, msg)
        self.assertNotIn(td, msg)

    def test_issuer_control_characters_are_escaped(self):
        from taxjson.lib.cra_slips import CraSlip, Placement, table
        from taxjson.lib.tomlcompat import tomllib
        s = CraSlip("x\ny.pdf", 2025, "T5", "original",
                    'EVIL "CO"\n[[slip]]\x00', boxes={"13": 1.0})
        t = table(Placement(s, account="margin"))
        doc = tomllib.loads(t)
        self.assertEqual(len(doc["slip"]), 1)
        self.assertEqual(doc["slip"][0]["issuer"], 'EVIL "CO"\n[[slip]]\x00')


class TestBrokerOfIssuer(unittest.TestCase):
    """M3: another institution is never another broker's (DIRECT,
    INVESTING, BANK ... say nothing)."""

    def test_names(self):
        from taxjson.lib.cra_slips import broker_of_issuer as b
        self.assertEqual(b(" ".join(IB_NAME)), "ib")
        self.assertEqual(b("RBC DIRECT INVESTING INC./RBC PLACEMENTS EN "
                           "DIRECT INC."), "rbc_direct")
        self.assertEqual(b("QUESTRADE, INC."), "questrade")
        for other in ("TD DIRECT INVESTING", "QTRADE DIRECT INVESTING",
                      "NATIONAL BANK DIRECT BROKERAGE", "RBC ROYAL BANK",
                      "RBC GLOBAL ASSET MANAGEMENT INC.",
                      "SAMPLE TRUST COMPANY"):
            self.assertIsNone(b(other), other)


def _row(h, sym, amt, cat="ca_div", src="ib.csv"):
    from taxjson.lib.slip_audit import BookRow
    return BookRow(account="margin", id=f"{h}{sym}{amt}", action="DIVIDEND",
                   symbol=sym, root=sym.split(".")[0], currency="CAD",
                   native=amt, cad=amt, pay_date="2025-03-03",
                   tax_date="2025-03-03", source=src, source_hash=h,
                   category=cat, hashes=(h,))


def _fake_report(h, eligible):
    from taxjson.lib.ib_dividends import Component, Payment, Report
    pay = Payment("ZZQ", "CA", "CAD", "2025-03-03", "2025-02-20", False,
                  components=[Component("T5", "eligible", "T5: Eligible",
                                        eligible, eligible, 0.0, 0.0)])
    return Report(path=Path(f"{h}.csv"), account_hash=h,
                  account_masked="U5***", base_currency="CAD",
                  payments=[pay])


class TestPlaceTwoAccountLabel(unittest.TestCase):
    """The key of a slip in a label whose IB statement holds two
    accounts: the account whose dividends report matches it; else the
    one account with no report; never a guess."""

    def _group(self):
        from taxjson.lib.cra_slips import Group
        h1, h2 = "1" * 10, "2" * 10
        return h1, h2, Group("margin", (h1, h2), "ib", ("ib.csv",),
                             [_row(h1, "ZZQ.TO", 600.0)])

    @rule("CA-SLIP-04")
    def test_report_match_picks_the_account(self):
        from taxjson.lib.cra_slips import CraSlip, place
        h1, h2, g = self._group()
        ib = " ".join(IB_NAME)
        s1 = CraSlip("a.pdf", 2025, "T5", "original", ib,
                     boxes={"24": 400.0})
        s2 = CraSlip("b.pdf", 2025, "T5", "original", ib,
                     boxes={"24": 200.0})
        reps = [_fake_report(h1, 200.0), _fake_report(h2, 400.0)]
        got = {p.slip.shown: p.key for p in place([s1, s2], [g], 2025, {},
                                                  reps)}
        self.assertEqual(got, {"a.pdf": h2, "b.pdf": h1})
        # One report, a slip it does not match: the account with none.
        (p,) = place([s1], [g], 2025, {}, [_fake_report(h2, 200.0)])
        self.assertEqual(p.key, h1)
        # Two reports, neither matches: not placed.
        (p,) = place([CraSlip("c.pdf", 2025, "T5", "original", ib,
                              boxes={"24": 999.0})], [g], 2025, {}, reps)
        self.assertIsNone(p.account)
        self.assertIn("broker_account", p.how)

    def test_usd_slip_with_no_rate_is_not_placed(self):
        from taxjson.lib.cra_slips import CraSlip, place
        _h1, _h2, g = self._group()
        s = CraSlip("u.pdf", 2025, "T5", "original", " ".join(IB_NAME),
                    currency="USD", boxes={"24": 100.0})
        (p,) = place([s], [g], 2025, {"USD": None})
        self.assertIsNone(p.account)
        self.assertIn("no USD rate", p.how)


class TestPlanImport(unittest.TestCase):
    """H2 at the planner: duplicates, amended against original."""

    def _pl(self, shown, status, boxes, issuer="ZZT SAMPLE INDEX ETF"):
        from taxjson.lib.cra_slips import CraSlip, Placement
        s = CraSlip(shown, 2025, "T3", status, issuer, boxes=boxes)
        return (Placement(s, account="margin", security="ZZT.TO"), "k" * 10)

    @rule("CA-SLIP-04")
    def test_duplicates_and_amended(self):
        from taxjson.lib.cra_slips import (CraSlip, Existing, drop_duplicates,
                                           plan_import)
        a = CraSlip("t3.pdf", 2025, "T3", "original", "ZZT FUND",
                    boxes={"42": 3.45})
        b = CraSlip("t3 (1).pdf", 2025, "T3", "original", "ZZT FUND",
                    boxes={"42": 3.45})
        kept, notes = drop_duplicates([a, b])
        self.assertEqual(kept, [a])
        self.assertIn("the same slip as t3.pdf", notes[0])
        # Original + amended in one import: the amended only.
        plan = plan_import([self._pl("o.pdf", "original", {"42": 3.45}),
                            self._pl("a.pdf", "amended", {"42": 5.45})], [])
        self.assertEqual([p.slip.shown for p, _k in plan.add], ["a.pdf"])
        ex = Existing(index=2, type="T3", account="margin", security="ZZT",
                      issuer="ZZT SAMPLE INDEX ETF", status="original",
                      currency="CAD", boxes={"42": 3.45},
                      keys=frozenset({"k" * 10}), imported=True)
        # The same slip again: nothing.
        plan = plan_import([self._pl("o.pdf", "original", {"42": 3.45})],
                           [ex])
        self.assertEqual((plan.add, plan.replace), ([], []))
        self.assertIn("already in slips.toml", plan.notes[0])
        # The amended slip later: it replaces table #2.
        plan = plan_import([self._pl("a.pdf", "amended", {"42": 5.45})],
                           [ex])
        self.assertEqual([(e.index, p.slip.shown) for e, p, _k in
                          plan.replace], [(2, "a.pdf")])
        # The original after its amended slip: not imported.
        amd = Existing(**{**ex.__dict__, "status": "amended",
                          "boxes": {"42": 5.45}})
        plan = plan_import([self._pl("o.pdf", "original", {"42": 3.45})],
                           [amd])
        self.assertEqual(plan.add, [])
        self.assertIn("holds its amended slip", plan.notes[0])
        # Two originals it may amend: neither.
        ex3 = Existing(**{**ex.__dict__, "index": 3, "boxes": {"42": 1.0}})
        plan = plan_import([self._pl("a.pdf", "amended", {"42": 5.45})],
                           [ex, ex3])
        self.assertEqual((plan.add, plan.replace), ([], []))
        self.assertIn("several slips it may amend", plan.notes[0])


def _toml(root):
    from taxjson.lib.tomlcompat import tomllib
    return tomllib.loads((root / "inputs" / "slips" / "slips.toml")
                         .read_text())


@unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext is not installed")
class TestImportCountsOnce(unittest.TestCase):
    """H2 end to end: a re-download, a file and its folder, an amended
    slip in the same import and later — the T3's return of capital is
    counted once, the amended figure."""

    @rule("CA-SLIP-04")
    def test_redownload_folder_and_amended(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            cra = tmp / "cra"
            cra.mkdir()
            t3 = CRA.t3(cra / "t3.pdf", ["ZZT SAMPLE INDEX ETF"],
                        {"49": "30.00"}, other={"25": "6.55", "42": "3.45"})
            shutil.copy(t3, cra / "t3 (1).pdf")
            r = tj(root, home, "slip-audit", "--import-cra", str(cra),
                   str(t3), "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("the same slip as t3", r.stdout)
            self.assertEqual(len(_toml(root)["slip"]), 1)
            self.assertEqual(_toml(root)["slip"][0]["status"], "original")
            # The amended slip: it replaces the original (commented out,
            # the old file kept).
            amd = tmp / "amd"
            amd.mkdir()
            CRA.t3(amd / "t3.pdf", ["ZZT SAMPLE INDEX ETF"],
                   {"49": "28.00"}, other={"25": "6.55", "42": "5.45"},
                   status="amended")
            r = tj(root, home, "slip-audit", "--import-cra", str(amd),
                   "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("replaced by the amended slip", r.stdout)
            doc = _toml(root)
            self.assertEqual([(s["status"], s["boxes"]["42"])
                              for s in doc["slip"]], [("amended", 5.45)])
            text = (root / "inputs" / "slips" / "slips.toml").read_text()
            self.assertIn("# Replaced by the amended slip t3.pdf", text)
            self.assertTrue((root / "inputs" / "slips" / "slips.toml.bak")
                            .is_file())
            # The original again: not imported.
            r = tj(root, home, "slip-audit", "--import-cra", str(t3),
                   "--write")
            self.assertIn("holds its amended slip", r.stdout)
            self.assertEqual(len(_toml(root)["slip"]), 1)
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (g,) = rep["accounts"][0]["groups"]
            ln = {x["category"]: x for x in g["buckets"][0]["lines"]}
            self.assertEqual(ln["roc"]["slip"], 5.45)

    @rule("CA-SLIP-04")
    def test_original_and_amended_in_one_import_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            tj(root, home, "run", "--no-input")
            cra = tmp / "cra"
            cra.mkdir()
            CRA.t3(cra / "a original.pdf", ["ZZT SAMPLE INDEX ETF"],
                   {"49": "30.00"}, other={"25": "6.55", "42": "3.45"})
            # A name carrying the IB id: masked in `source` and the output.
            CRA.t3(cra / f"{IB_ACCT} b amended.pdf", ["ZZT SAMPLE INDEX ETF"],
                   {"49": "28.00"}, other={"25": "6.55", "42": "5.45"},
                   status="amended")
            r = tj(root, home, "slip-audit", "--import-cra", str(cra),
                   "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["add"], ["U5*** b amended.pdf"])
            self.assertIn('source = "cra:U5*** b amended.pdf"', doc["tables"])
            self.assertNotIn(IB_ACCT, r.stdout)
            self.assertTrue(any("replaced by the amended slip" in n
                                for n in doc["replaced_in_import"]))
            self.assertFalse(doc["written"])
            self.assertIn('status = "amended"', doc["tables"])
            self.assertNotIn(HOLDER, r.stdout)

    @unittest.skipUnless(HAVE_QPDF, "qpdf is not installed")
    def test_merged_pdf_imports_two_tables(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            tj(root, home, "run", "--no-input")
            a = CRA.t5(tmp / "a.pdf", IB_NAME,
                       {"24": "494.50", "18": "20.50"},
                       other={"15": "48.15", "16": "7.94"})
            b = CRA.t3(tmp / "b.pdf", ["ZZT SAMPLE INDEX ETF"],
                       {"49": "30.00"}, other={"25": "6.55", "42": "3.45"})
            m = tmp / "merged.pdf"
            subprocess.run(["qpdf", "--empty", "--pages", str(a), str(b),
                            "--", str(m)], check=True)
            r = tj(root, home, "slip-audit", "--import-cra", str(m),
                   "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            got = {(s["type"], s.get("security"), s["source"])
                   for s in _toml(root)["slip"]}
            self.assertEqual(got, {("T5", None, "cra:merged.pdf #1"),
                                   ("T3", "ZZT.TO", "cra:merged.pdf #2")})


class TestKeysAndRedact(unittest.TestCase):
    """M4: slips.toml's broker_key is salted (the salt in work/), a key
    of another salt is said, a legacy unsalted key still works and is
    said, and `taxjson redact` replaces the key."""

    def _slips(self, root, key):
        (root / "inputs" / "slips" / "slips.toml").write_text(
            f'[[slip]]\ntype = "T5"\nissuer = "IB"\naccount = "margin"\n'
            f'broker_key = "{key}"\nboxes = {{ 24 = 494.50, 18 = 20.50, '
            f'15 = 48.15, 16 = 7.94 }}\n')

    def test_salted_legacy_and_foreign_keys(self):
        from taxjson.bin.taxjson_brokerage import hash_broker_account
        from taxjson.lib import slip_audit as SA
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            tj(root, home, "run", "--no-input")
            salt = SA.key_salt(root, create=True)
            self.assertRegex(salt, r"^[0-9a-f]{32}$")
            self.assertEqual(SA.key_salt(root), salt)
            raw = hash_broker_account(IB_ACCT)
            key = SA.broker_key(salt, raw)
            self.assertNotEqual(key, raw)
            self._slips(root, key)
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (g,) = rep["accounts"][0]["groups"]
            ln = {x["category"]: x for x in g["buckets"][0]["lines"]}
            # T5 24 494.50 and the report's T3 49 30.00.
            self.assertEqual(ln["ca_div"]["slip"], 524.5)
            self.assertFalse([p for p in rep["problems"]
                              if "broker_key" in p])
            self._slips(root, raw)
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            self.assertTrue(any("books' own hash" in p
                                for p in rep["problems"]))
            self._slips(root, "0a1b2c3d4e")
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            self.assertTrue(any("is no broker account in the books" in p
                                for p in rep["problems"]))

    def test_redact_replaces_broker_key(self):
        from taxjson.bin.taxjson_redact import redact_text
        text = ('[[slip]]\ntype = "T5"\nbroker_key = "37b9df9118"   # '
                'ib.csv\n[[slip]]\nbroker_key = "37b9df9118"\n'
                '[[slip]]\nbroker_key = "aaaaaaaaaa"\n'
                '# broker_key = "bbbbbbbbbb"   # a replaced table\n')
        out, rep = redact_text(text)
        self.assertNotIn("37b9df9118", out)
        self.assertNotIn("aaaaaaaaaa", out)
        self.assertEqual(out.count('broker_key = "0000000001"'), 2)
        self.assertIn('broker_key = "0000000002"', out)
        self.assertNotIn("bbbbbbbbbb", out)
        self.assertTrue(rep.found_anything())


class TestReportBesideTypedSlip(unittest.TestCase):
    """M1, M2: a typed T5 for the IB account beside IB's report, two
    copies of the report, --template with a report."""

    @rule("CA-SLIP-04", "CA-SLIP-01")
    def test_typed_t5_and_template(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            tj(root, home, "run", "--no-input")
            sf = root / "inputs" / "slips" / "slips.toml"
            sf.write_text(
                f'[[slip]]\ntype = "T5"\nissuer = "IB"\naccount = "margin"\n'
                f'broker_account = "{IB_ACCT}"\nboxes = {{ 24 = 494.50, '
                f'18 = 20.50, 15 = 48.15, 16 = 7.94, 13 = 12.34 }}\n')
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (g,) = rep["accounts"][0]["groups"]
            ln = {x["category"]: x for x in g["buckets"][0]["lines"]}
            # Once, not the typed slip plus the report (T5 24 494.50 and
            # the report's T3 49 30.00; T5 15 and the T3's 25).
            self.assertEqual(ln["ca_div"]["slip"], 524.5)
            self.assertEqual(ln["foreign"]["slip"], 54.7)
            self.assertEqual(g["payments"]["matched"], 5)
            # A T5 typed for its interest only: compared beside the
            # report (which has none).
            sf.write_text(
                f'[[slip]]\ntype = "T5"\nissuer = "IB"\naccount = "margin"\n'
                f'broker_account = "{IB_ACCT}"\nboxes = {{ 13 = 12.34 }}\n')
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (g,) = rep["accounts"][0]["groups"]
            ln = {x["category"]: x for x in g["buckets"][0]["lines"]}
            self.assertEqual((ln["ca_div"]["slip"], ln["interest"]["slip"],
                              ln["interest"]["status"]), (524.5, 12.34, "ok"))
            sf.unlink()
            r = tj(root, home, "slip-audit", "--template")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn('\n[[slip]]\ntype = "T5"', r.stdout)
            self.assertIn("IB's dividends report of U5***", r.stdout)
            self.assertNotIn(IB_ACCT, r.stdout)

    def test_two_copies_of_the_report(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            tj(root, home, "run", "--no-input")
            d = root / "inputs" / "slips"
            src = d / f"{IB_ACCT}.2025.dividends.csv"
            shutil.copy(src, d / f"{IB_ACCT}.2025.dividends (1).csv")
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (g,) = rep["accounts"][0]["groups"]
            ln = {x["category"]: x for x in g["buckets"][0]["lines"]}
            self.assertEqual(ln["ca_div"]["slip"], 524.5)
            self.assertTrue(any("the same IB dividends report" in p
                                for p in rep["problems"]))
            (d / f"{IB_ACCT}.2025.dividends (1).csv").write_text(
                _ib_report().replace("Holding Period,30,30,21.6,",
                                     "Holding Period,31,31,22.3,"))
            r = tj(root, home, "slip-audit")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("two different IB dividends reports", r.stderr)
            self.assertNotIn(IB_ACCT, r.stderr)

    def test_report_of_two_accounts_is_refused(self):
        from taxjson.lib.ib_dividends import IBReportError, read_report
        two = _ib_report().replace(
            f"Account,Data,{IB_ACCT},,{HOLDER},CAD,\n",
            f"Account,Data,{IB_ACCT},,{HOLDER},CAD,\n"
            f"Account,Data,{IB_ACCT2},,{HOLDER},CAD,\n")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.csv"
            p.write_text(two)
            with self.assertRaises(IBReportError) as cm:
                read_report(p)
        self.assertIn("one dividends report per account", str(cm.exception))
        self.assertNotIn(IB_ACCT2, str(cm.exception))


class TestUsdBaseReport(unittest.TestCase):
    """M6: IB's report of a USD-base account is compared in CAD, each
    payment at the Bank of Canada rate of its pay date (1.40 before
    July, 1.36 after, in the seeded cache)."""

    @rule("CA-SLIP-02")
    def test_converted_at_daily_rates(self):
        usd = _ib_report().replace(f"{HOLDER},CAD,", f"{HOLDER},USD,")
        # GrossInBase now in USD: the CAD payments' base amounts change.
        usd = usd.replace(",500,500,360,", ",500,360,360,")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp, report=usd)
            tj(root, home, "run", "--no-input")
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (g,) = rep["accounts"][0]["groups"]
            (b,) = g["buckets"]
            self.assertEqual(b["currency"], "CAD")
            ln = {x["category"]: x for x in b["lines"]}
            # NQZ 10 USD (May, 1.40) + QQZ 25 USD (August, 1.36), and
            # the T3's 6.55 CAD.
            self.assertEqual(ln["foreign"]["slip"], 54.55)
            self.assertEqual(ln["ca_div"]["slip"], 524.5)
            self.assertTrue(any("converted to CAD at the Bank of Canada"
                                in n and "average rate" in n
                                for n in rep["suggestions"]["notes"]))
            (src,) = [s for s in rep["sources"]
                      if s["kind"] == "ib-dividends"]
            self.assertEqual(src["converted_from"], "USD")
            # No rate in the cache: not compared, said.
            (home / ".currency_price_cache.json").write_text("{}")
            r = tj(root, home, "slip-audit")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("no Bank of Canada USD rate", r.stderr)


class TestMisplacedReport(unittest.TestCase):

    def test_report_in_an_account_folder(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            src = root / "inputs" / "slips" / f"{IB_ACCT}.2025.dividends.csv"
            shutil.move(src, root / "inputs" / "margin" / src.name)
            r = tj(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is IB's dividends report", r.stdout + r.stderr)
            self.assertIn("move it to inputs/slips/", r.stdout + r.stderr)
            self.assertNotIn(IB_ACCT, r.stdout + r.stderr)


class TestChecklistStep(unittest.TestCase):

    def test_t5_t3_step_names_nr4_and_import(self):
        from taxjson.lib.checklist import STEPS
        (step,) = [s for s in STEPS if s[0] == "t5-t3"]
        self.assertIn("NR4", step[2])
        self.assertIn("--import-cra", step[4])


if __name__ == "__main__":
    unittest.main()
