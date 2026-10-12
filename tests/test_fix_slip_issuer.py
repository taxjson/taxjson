"""`taxjson slip-audit --import-cra`: a T5 whose issuer is a broker's
carrying dealer, not the broker (Webull Canada's slips are issued by CI
Investment Services). The issuer names live in a shipped data file
(src/taxjson/data/slip_issuers.toml), never in code; an alias places a
slip only in a broker account the books hold, and only on its own words
("CI DIRECT INVESTING" is another CI business). A broker account whose
export has no income rows (Webull's parser books trades only) is still
that broker's account. Tax-logic CA-SLIP-04. All data synthetic.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _cra_pdf as CRA
from _style import CapturedWidth
from tax_rules import rule
from test_slip_audit import HOLDER, _home, _ib_project, tj

_WIDTH = CapturedWidth()
HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None
CI_ISSUER = ["CI INVESTMENT SERVICES INC./CI SERVICES D'INVESTISSEMENT INC"]
WB_ACCT = "55500099"  # pii-ok (synthetic)

_WEBULL = f"""Webull Securities (Canada) Ltd.
Synthetic Statement
Account Number: {WB_ACCT}
Date Range: January 1 2025 - December 31 2025

"Currency","Date","Action Code","Symbol","Security Description","Type Code","Quantity","Price","Proceeds"
USD,15-01-2025,BUY,@ZZW,ZZW SAMPLE CORP,EQ,10,50.00,"(500.00)"
"""


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestIssuerAliases(unittest.TestCase):

    @rule("CA-SLIP-04")
    def test_carrying_dealer_is_webull(self):
        from taxjson.lib.cra_slips import broker_of_issuer as b
        self.assertEqual(b(CI_ISSUER[0]), "webull")
        # Either half alone, any case and spacing, the legal form left out.
        self.assertEqual(b("CI Investment  Services Inc."), "webull")
        self.assertEqual(b("CI SERVICES D'INVESTISSEMENT INC"), "webull")
        self.assertEqual(b("CI INVESTMENT SERVICES"), "webull")
        # The broker's own name still does.
        self.assertEqual(b("WEBULL SECURITIES (CANADA) LTD."), "webull")

    @rule("CA-SLIP-04")
    def test_other_ci_businesses_and_other_institutions_never(self):
        from taxjson.lib.cra_slips import broker_of_issuer as b
        for other in ("CI DIRECT INVESTING", "CI DIRECT INVESTING INC.",
                      "CI FINANCIAL CORP.", "CI GLOBAL ASSET MANAGEMENT",
                      "CI INVESTMENTS INC.", "CI ASSANTE WEALTH MANAGEMENT",
                      "SAMPLE INVESTMENT SERVICES INC.",
                      "ZZCI INVESTMENT SERVICES INC.",
                      "CI SAMPLE INVESTMENT SERVICES",
                      # The words split across the two halves.
                      "SAMPLE CI INC./INVESTMENT SERVICES INC",
                      "TD DIRECT INVESTING", "QTRADE DIRECT INVESTING",
                      "NATIONAL BANK DIRECT BROKERAGE", "RBC ROYAL BANK",
                      "RBC GLOBAL ASSET MANAGEMENT INC.",
                      "SAMPLE TRUST COMPANY"):
            self.assertIsNone(b(other), other)

    def test_display_names_still_cover_the_brokers(self):
        from taxjson.lib.cra_slips import broker_of_issuer as b
        self.assertEqual(b("INTERACTIVE BROKERS CANADA INC. INTERACTIVE "
                           "COURTAGE CANADA"), "ib")
        self.assertEqual(b("RBC DIRECT INVESTING INC./RBC PLACEMENTS EN "
                           "DIRECT INC."), "rbc_direct")
        self.assertEqual(b("QUESTRADE, INC."), "questrade")

    def test_the_shipped_table_is_valid(self):
        from taxjson.lib import cra_slips as CS
        al = CS.issuer_aliases()
        self.assertIn("webull", {a.broker for a in al})
        for a in al:
            self.assertTrue(a.source, a)

    def test_a_bad_table_is_refused(self):
        from taxjson.lib import cra_slips as CS
        bad = [
            {"alias": [{"broker": "webull",
                        "issuer": ["INVESTMENT SERVICES INC."],
                        "source": "x"}]},           # nothing distinctive
            {"alias": [{"broker": "nosuch", "issuer": ["ZZQ DEALER"],
                        "source": "x"}]},           # no such broker
            {"alias": [{"broker": "webull", "issuer": ["ZZQ DEALER"]}]},
            {"alias": [{"broker": "webull", "issuer": "ZZQ DEALER",
                        "source": "x", "extra": 1}]},
            {"aliases": []},
        ]
        for doc in bad:
            with self.assertRaises(CS.CraSlipError, msg=doc):
                CS.parse_issuer_aliases(doc, "slip_issuers.toml")


def _webull_beside_ib(tmp: Path) -> Path:
    root = _ib_project(tmp)
    (root / "inputs" / "margin" / "wb_2025.csv").write_text(_WEBULL)
    return root


@unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext is not installed")
class TestImportWebull(unittest.TestCase):

    @rule("CA-SLIP-04")
    def test_ci_slip_placed_in_the_webull_account(self):
        from taxjson.bin.taxjson_brokerage import hash_broker_account
        from taxjson.lib import slip_audit as SA
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _webull_beside_ib(tmp)
            r = tj(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr)
            cra = tmp / "cra"
            cra.mkdir()
            CRA.t5(cra / "2025 T5 Webull USD.pdf", CI_ISSUER,
                   {"24": "0.00"}, currency="USD",
                   other={"15": "12.00", "16": "1.80"})
            CRA.t5(cra / "2025 T5 CI Direct.pdf", ["CI DIRECT INVESTING"],
                   {"13": "3.21"})
            r = tj(root, home, "slip-audit", "--import-cra", str(cra),
                   "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            got = {s["file"]: s for s in json.loads(r.stdout)["slips"]}
            wb = got["2025 T5 Webull USD.pdf"]
            self.assertEqual(wb["account"], "margin", wb["how"])
            self.assertEqual(wb["broker_account"], ["wb_2025.csv"])
            self.assertIn("webull", wb["how"])
            ci = got["2025 T5 CI Direct.pdf"]
            self.assertIsNone(ci["account"])
            self.assertIn("no broker in the books", ci["how"])
            r = tj(root, home, "slip-audit", "--import-cra",
                   str(cra / "2025 T5 Webull USD.pdf"), "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn(HOLDER, r.stdout + r.stderr)
            doc = tomllib.loads((root / "inputs" / "slips" / "slips.toml")
                                .read_text())
            (s,) = doc["slip"]
            self.assertEqual(s["broker_key"], SA.broker_key(
                SA.key_salt(root), hash_broker_account(WB_ACCT)))
            # The audit reads the key back: the Webull account, compared
            # with its books (none: the parser books trades only).
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            self.assertFalse([p for p in rep["problems"]
                              if "broker_key" in p], rep["problems"])
            masked = [g["broker_account"] for a in rep["accounts"]
                      for g in a["groups"]]
            self.assertIn("wb_2025.csv", masked)

    @rule("CA-SLIP-04")
    def test_no_webull_in_the_books_not_placed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            cra = tmp / "cra"
            cra.mkdir()
            CRA.t5(cra / "2025 T5 Webull.pdf", CI_ISSUER, {"24": "10.00"})
            r = tj(root, home, "slip-audit", "--import-cra", str(cra),
                   "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            (s,) = json.loads(r.stdout)["slips"]
            self.assertIsNone(s["account"])
            self.assertIn("Webull", s["how"])


class TestPlaceEmptyGroups(unittest.TestCase):
    """A broker account with no income rows in the books: a slip of its
    broker goes there; with two such accounts it cannot be told."""

    def _slip(self, name="1.pdf"):
        from taxjson.lib.cra_slips import CraSlip
        return CraSlip(name, 2025, "T5", "original", CI_ISSUER[0],
                       boxes={"15": 12.0})

    def test_one_account(self):
        from taxjson.lib.cra_slips import Group, place
        g = Group("margin", ("a" * 10,), "webull", ("wb.csv",))
        (pl,) = place([self._slip()], [g], 2025, {})
        self.assertEqual((pl.account, pl.key), ("margin", "a" * 10))

    def test_an_account_with_payments_comes_first(self):
        from taxjson.lib.cra_slips import CraSlip, Group, place
        from taxjson.lib.slip_audit import BookRow
        row = BookRow(account="margin", id="r1", action="DIVIDEND",
                      symbol="ZZA.TO", root="ZZA", currency="CAD",
                      native=50.0, cad=50.0, pay_date="2025-03-03",
                      tax_date="2025-03-03", source="rbc.csv",
                      source_hash="a" * 10, category="ca_div",
                      hashes=("a" * 10,))
        gs = [Group("margin", ("a" * 10,), "rbc_direct", ("rbc.csv",),
                    [row]),
              Group("margin", ("b" * 10,), "rbc_direct", ("old.csv",))]
        s = CraSlip("1.pdf", 2025, "T5", "original",
                    "RBC DIRECT INVESTING INC.", boxes={"24": 900.0})
        (pl,) = place([s], gs, 2025, {})
        self.assertEqual((pl.key, pl.how),
                         ("a" * 10, "the rbc_direct broker account"))

    def test_two_accounts_not_guessed(self):
        from taxjson.lib.cra_slips import Group, place
        gs = [Group("margin", ("a" * 10,), "webull", ("wb.csv",)),
              Group("margin", ("b" * 10,), "webull", ("wb2.csv",))]
        (pl,) = place([self._slip()], gs, 2025, {})
        self.assertIsNone(pl.account)
        self.assertIn("cannot be told", pl.how)
        # Naming the account puts it there, with no broker account.
        (pl,) = place([self._slip()], gs, 2025, {}, account="margin")
        self.assertEqual((pl.account, pl.group, pl.key), ("margin", None,
                                                          ""))


if __name__ == "__main__":
    unittest.main()
