"""QA F3 (external QA suite, synthetic data): a loss on one listing, the
other listing of the same root (an equal name) bought within 30 days: a
Warning naming TOBASE / DISTINCT, `ticker-map --suggest`, `scan`
XLIST-LOSS, the checklist's wash-reviewed step, `run --strict`
(lib/xlist_loss_radar; CA-XLIST-05 / US-XLIST-04)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import out

from _qa_project import console, gains, project, tj
from tax_rules import rule, rule_absent

# ------------------------------------------------------------------ F3

QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")
NAME = "ZZXCORP INTERNATIONAL INC COM"


def qt_row(day, settle, action, sym, qty, price, cur, name=NAME,
           acct="55500001", kind="Margin"):              # pii-ok
    gross = qty * price
    return (f"{day},{settle},{action},{sym},{name},{qty},{price:.2f},"
            f"{-gross:.2f},0,{-gross:.2f},{cur},Trades,{acct},{kind}\n")


F3_ROWS = (qt_row("2024-01-02", "2024-01-04", "Buy", "ZZX.TO", 100, 70, "CAD")
           + qt_row("2024-03-01", "2024-03-05", "Sell", "ZZX.TO", -100, 60,
                    "CAD")
           + qt_row("2024-03-11", "2024-03-13", "Buy", "ZZX", 100, 45, "USD"))


def f3_project(tmp, name, rows=F3_ROWS, country="canada", ticker_map=None,
               extra=None, extra_accounts=""):
    files = {"margin/questrade_2024.csv": QT_HEAD + rows}
    files.update(extra or {})
    return project(tmp, name, files, country=country, ticker_map=ticker_map,
                   extra_accounts=extra_accounts)


def zzx_loss(root):
    return [g for g in gains(root) if g.get("symbol") == "ZZX.TO"
            and g.get("direction") == "LONG"]


class TestF3CanadaRadar(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = f3_project(cls.tmp.name, "f3")
        cls.r = tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-XLIST-05")
    def test_warning_and_loss_still_allowed(self):
        loss, = zzx_loss(self.root)
        self.assertAlmostEqual(loss["gain"], -1000.0)
        text = self.r.stdout
        self.assertIn("Warning: possible superficial loss across listings: "
                      "ZZX.TO sold at a loss, ZZX.US bought within 30 days",
                      text)
        self.assertIn("\nTOBASE ZZX.US ZZX.TO\n", text)
        self.assertIn("\nDISTINCT ZZX.TO ZZX.US\n", text)
        self.assertEqual(out.lint(self.r.stdout), [])

    @rule("CA-XLIST-05")
    def test_suggest_offers_tobase(self):
        r = tj(self.root, "ticker-map", "--suggest", "--json")
        doc = json.loads(r.stdout)
        lines = [s["line"] for s in doc.get("suggestions") or doc.get(
            "offer") or []]
        self.assertIn("TOBASE ZZX.US ZZX.TO", lines, doc)

    @rule("CA-XLIST-05")
    def test_scan_finding(self):
        r = tj(self.root, "scan", check=False)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("XLIST-LOSS", r.stdout)
        self.assertIn("`TOBASE ZZX.US ZZX.TO`", r.stdout)

    @rule("CA-XLIST-05")
    def test_checklist_wash_reviewed_step(self):
        from datetime import date
        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        ctx = cl.Ctx(root=self.root, cfg=cfg, year=2024,
                     today=date(2025, 3, 1),
                     run_sub=lambda argv, timeout=900: (0, "", ""))
        res = cl.d_wash_reviewed(ctx)
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("1 possible superficial loss(s) across listings: "
                      "ZZX.TO/ZZX.US", res.detail)

    @rule("CA-XLIST-05")
    def test_strict_stops(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        r = tj(d, "run", "--no-input", "--strict", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--strict: 1 possible superficial loss across "
                      "listings", r.stderr)

    @rule("CA-XLIST-05", "CA-XLIST-01")
    def test_tobase_answers_it_and_denies_the_loss(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        (d / "ticker.map").write_text("TOBASE ZZX.US ZZX.TO\n")
        r = tj(d, "run", "--no-input", "--strict")
        self.assertNotIn("across listings", console(r))
        loss, = zzx_loss(d)
        self.assertAlmostEqual(loss["gain"], 0.0, places=2)
        self.assertEqual(tj(d, "scan", check=False).stdout.count(
            "XLIST-LOSS"), 0)

    @rule("CA-XLIST-05")
    def test_distinct_answers_it(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        (d / "ticker.map").write_text("DISTINCT ZZX.TO ZZX.US\n")
        r = tj(d, "run", "--no-input", "--strict")
        self.assertNotIn("across listings", console(r))
        loss, = zzx_loss(d)
        self.assertAlmostEqual(loss["gain"], -1000.0)
        # a stale state answered by the map since the run is not listed
        from taxjson.lib import xlist_loss_radar as XR
        self.assertEqual(XR.open_findings(d), [])


class TestF3Variants(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @rule("CA-XLIST-05")
    def test_loss_on_us_line_rebought_on_tsx(self):
        rows = (qt_row("2024-01-02", "2024-01-04", "Buy", "ZZX", 100, 50, "USD")
                + qt_row("2024-03-01", "2024-03-05", "Sell", "ZZX", -100, 40,
                         "USD")
                + qt_row("2024-03-11", "2024-03-13", "Buy", "ZZX.TO", 100, 55,
                         "CAD"))
        root = f3_project(self.tmp.name, "rev", rows)
        r = tj(root, "run", "--no-input")
        self.assertIn("ZZX.US sold at a loss, ZZX.TO bought within 30 days",
                      r.stdout)
        self.assertIn("\nTOBASE ZZX.US ZZX.TO\n", r.stdout)

    @rule("CA-XLIST-05")
    def test_names_that_differ_are_not_flagged(self):
        rows = (qt_row("2024-01-02", "2024-01-04", "Buy", "ZZX.TO", 100, 70,
                       "CAD")
                + qt_row("2024-03-01", "2024-03-05", "Sell", "ZZX.TO", -100,
                         60, "CAD")
                + qt_row("2024-03-11", "2024-03-13", "Buy", "ZZX", 100, 45,
                         "USD", name="ZZX OTHERCO HOLDINGS LTD"))
        root = f3_project(self.tmp.name, "diff", rows)
        r = tj(root, "run", "--no-input")
        self.assertNotIn("across listings", console(r))

    @rule("CA-XLIST-05")
    def test_purchase_in_a_registered_account_is_flagged(self):
        rows = (qt_row("2024-01-02", "2024-01-04", "Buy", "ZZX.TO", 100, 70,
                       "CAD")
                + qt_row("2024-03-01", "2024-03-05", "Sell", "ZZX.TO", -100,
                         60, "CAD"))
        tfsa = QT_HEAD + qt_row("2024-02-20", "2024-02-21", "Buy", "ZZX", 100,
                                45, "USD", acct="55500002",   # pii-ok
                                kind="TFSA")
        root = f3_project(self.tmp.name, "reg", rows,
                          extra={"tfsa/questrade_2024.csv": tfsa},
                          extra_accounts='[accounts.tfsa]\n'
                                         'type = "sheltered"\n')
        r = tj(root, "run", "--no-input")
        self.assertIn("Bought: ZZX.US 2024-02-20 (tfsa, registered)",
                      r.stdout)

    @rule("US-XLIST-04")
    def test_usa_wash_sale_across_listings(self):
        root = f3_project(self.tmp.name, "us", country="usa")
        r = tj(root, "run", "--no-input")
        self.assertIn("Warning: possible wash sale across listings: ZZX.TO "
                      "sold at a loss, ZZX.US bought within 30 days",
                      r.stdout)
        self.assertIn("\nTOBASE ZZX.TO ZZX.US\n", r.stdout)
        self.assertEqual(out.lint(r.stdout), [])

    @rule("CA-XLIST-05")
    @rule_absent("CA-XLIST-05", country="usa")
    @rule("US-XLIST-04")
    def test_still_held_at_day_30_is_canada_only(self):
        # The other listing is sold again before day 30: s.54 needs it
        # held at the end of day 30, §1091 does not.
        rows = F3_ROWS + qt_row("2024-03-20", "2024-03-21", "Sell", "ZZX",
                                -100, 46, "USD")
        res = {}
        for c in ("canada", "usa"):
            root = f3_project(self.tmp.name, c, rows, country=c)
            res[c] = tj(root, "run", "--no-input").stdout
        self.assertNotIn("across listings", res["canada"])
        self.assertIn("possible wash sale across listings", res["usa"])


if __name__ == "__main__":
    unittest.main()
