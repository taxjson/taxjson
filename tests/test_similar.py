"""ticker.map `SIMILAR A B [C ...]`: look-alike funds watched together
(lib/similar; tax-logic CA-SL-20 / US-WASH-26). A loss on one with
another bought in the window is a Warning of the run, a manual item of
the checklist, a caveat in buy-check / sell-check, the flagged listings
in rebuy and a block in wash-radar — never a denial: the funds stay two
securities."""
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import out

from _qa_project import console, gains, project, tj, toml
from tax_rules import rule

QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")


def qt_row(day, settle, action, sym, qty, price, cur, name,
           acct="55500001", kind="Margin"):                   # pii-ok
    gross = qty * price
    return (f"{day},{settle},{action},{sym},{name},{qty},{price:.2f},"
            f"{-gross:.2f},0,{-gross:.2f},{cur},Trades,{acct},{kind}\n")


MAP = "SIMILAR ZZSP.US ZZVO.US ZZVF.TO  # one index, three funds\n"


def rows(d):
    """The margin account: a loss on ZZSP.US, ZZVO.US bought 10 days
    later and held; `d(n)` is the day n days from the anchor."""
    return (qt_row(d(-70), d(-68), "Buy", "ZZSP", 100, 500, "USD",
                   "ZZ SP 500 ETF TRUST")
            + qt_row(d(-20), d(-18), "Sell", "ZZSP", -100, 450, "USD",
                     "ZZ SP 500 ETF TRUST")
            + qt_row(d(-10), d(-8), "Buy", "ZZVO", 80, 460, "USD",
                     "ZZ VANGUARD SP 500 ETF"))


def tfsa_rows(d):
    return qt_row(d(-5), d(-3), "Buy", "ZZVF.TO", 300, 120, "CAD",
                  "ZZ VANGUARD SP 500 INDEX ETF", acct="55500002",  # pii-ok
                  kind="TFSA")


def fixed(n):
    return (date(2024, 3, 21) + timedelta(days=n)).isoformat()


def make(tmp, name, d=fixed, country="canada", ticker_map=MAP, tfsa=True,
         year=None):
    files = {"margin/questrade_2024.csv": QT_HEAD + rows(d)}
    extra = ""
    if tfsa:
        files["tfsa/questrade_2024.csv"] = QT_HEAD + tfsa_rows(d)
        extra = '[accounts.tfsa]\ntype = "sheltered"\n'
    root = project(tmp, name, files, country=country, ticker_map=ticker_map,
                   extra_accounts=extra)
    if year is not None:
        (root / "taxjson.toml").write_text(
            toml(country, year=year, extra_accounts=extra))
    return root


def zzsp_loss(root):
    return [g for g in gains(root) if g.get("symbol") == "ZZSP.US"
            and g.get("direction") == "LONG"]


class TestRun(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "sim")
        cls.r = tj(cls.root, "run", "--no-input", "--details", "--strict")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-SL-20")
    def test_warning_names_the_pair_and_the_line(self):
        text = self.r.stdout
        self.assertIn("Warning: similar fund in a loss's window: ZZSP.US "
                      "sold at a loss, ZZVO.US bought within 30 days", text)
        self.assertIn("Sold at a loss: ZZSP.US 2024-03-01 (margin). Bought: "
                      "ZZVO.US 2024-03-11 (margin). ticker.map:1 declares "
                      "them alike:", " ".join(text.split()))
        self.assertIn("`SIMILAR ZZSP.US ZZVO.US ZZVF.TO`", text)
        # The registered account's buy counts too.
        self.assertIn("ZZSP.US sold at a loss, ZZVF.TO bought within 30 "
                      "days", text)
        self.assertIn("Bought: ZZVF.TO 2024-03-16 (tfsa, registered)", text)
        self.assertIn("identical property", text)
        self.assertEqual(out.lint(self.r.stdout), [])

    @rule("CA-SL-20")
    def test_nothing_denied_and_strict_passes(self):
        loss, = zzsp_loss(self.root)
        self.assertLess(loss["gain"], -1000)
        self.assertAlmostEqual(loss["gain"], loss.get("raw_gain", loss["gain"]),
                               places=2)
        self.assertEqual(self.r.returncode, 0)

    @rule("CA-SL-20")
    def test_state_and_open_findings(self):
        from taxjson.lib import similar as SIM
        st = json.loads((self.root / "work" / SIM.STATE).read_text())
        self.assertEqual(st["format"], SIM.FORMAT)
        pairs = [(f["loss_symbol"], f["other_symbol"])
                 for f in SIM.open_findings(self.root)]
        self.assertEqual(pairs, [("ZZSP.US", "ZZVF.TO"), ("ZZSP.US", "ZZVO.US")])
        # A line deleted since the run answers its findings.
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        (d / "ticker.map").write_text("SIMILAR ZZSP.US ZZVF.TO\n")
        self.assertEqual([f["other_symbol"] for f in SIM.open_findings(d)],
                         ["ZZVF.TO"])

    @rule("CA-SL-20")
    def test_checklist_wash_reviewed_is_manual(self):
        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        ctx = cl.Ctx(root=self.root, cfg=cfg, year=2024,
                     today=date(2025, 3, 1),
                     run_sub=lambda argv, timeout=900: (0, "", ""))
        res = cl.d_wash_reviewed(ctx)
        self.assertEqual(res.status, "manual", res.detail)
        self.assertIn("2 loss(es) with a similar fund bought in the window "
                      "(ticker.map SIMILAR): ZZSP.US/ZZVF.TO, ZZSP.US/ZZVO.US",
                      res.detail)

    @rule("CA-SL-20")
    def test_concise_console_is_one_line(self):
        r = tj(self.root, "run", "--no-input")
        lines = [ln for ln in r.stdout.splitlines() if "similar fund" in ln]
        self.assertEqual(len(lines), 2, r.stdout)
        self.assertTrue(all(ln.endswith("`tjs run --details`")
                            for ln in lines), lines)

    @rule("CA-SL-20")
    def test_rebuy_names_the_flagged_listings(self):
        r = tj(self.root, "rebuy", "--date", "2024-03-21", "--details")
        self.assertIn("A buy of ZZVF.TO, ZZVO.US is flagged, not counted "
                      "(ticker.map SIMILAR", " ".join(r.stdout.split()))
        doc = json.loads(tj(self.root, "rebuy", "--date", "2024-03-21",
                            "--json").stdout)
        row, = [x for x in doc["rows"] if x["ticker"] == "ZZSP.US"]
        self.assertEqual(row["similar"], ["ZZVF.TO", "ZZVO.US"])
        self.assertEqual(row["also"], [])

    @rule("CA-SL-20")
    def test_wash_radar_lists_the_group(self):
        r = tj(self.root, "wash-radar", "--date", "2024-03-21")
        self.assertIn("\nSIMILAR\n  - ZZSP.US COOLING, ZZVF.TO CLEAR, "
                      "ZZVO.US EXITABLE\n", r.stdout)
        self.assertIn("two securities for the rule", r.stdout)
        r = tj(self.root, "wash-radar", "--date", "2024-03-21", "--details")
        self.assertIn("\nSIMILAR\n  - ZZSP.US COOLING", r.stdout)
        doc = json.loads(tj(self.root, "wash-radar", "--date", "2024-03-21",
                            "--json").stdout)
        self.assertEqual(doc["similar"], [{
            "members": ["ZZSP.US", "ZZVF.TO", "ZZVO.US"],
            "status": {"ZZSP.US": "COOLING", "ZZVF.TO": "CLEAR",
                       "ZZVO.US": "EXITABLE"}}])

    @rule("CA-SL-20")
    def test_without_the_line_nothing_is_said(self):
        root = make(self.tmp.name, "plain", ticker_map=None)
        r = tj(root, "run", "--no-input", "--details")
        self.assertNotIn("similar fund", console(r))
        r = tj(root, "wash-radar", "--date", "2024-03-21")
        self.assertNotIn("SIMILAR", r.stdout)


class TestVariants(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @rule("CA-SL-20")
    def test_sold_out_by_day_30_is_not_flagged(self):
        d = fixed
        files = {"margin/questrade_2024.csv": QT_HEAD + rows(d) + qt_row(
            d(-2), d(0), "Sell", "ZZVO", -80, 470, "USD",
            "ZZ VANGUARD SP 500 ETF")}
        root = project(self.tmp.name, "out", files, ticker_map=MAP)
        r = tj(root, "run", "--no-input", "--details")
        self.assertNotIn("similar fund", console(r))

    @rule("CA-SL-20")
    def test_a_buy_outside_the_window_is_not_flagged(self):
        d = fixed
        files = {"margin/questrade_2024.csv": QT_HEAD + qt_row(
            d(-70), d(-68), "Buy", "ZZSP", 100, 500, "USD", "ZZ SP 500 ETF")
            + qt_row(d(-20), d(-18), "Sell", "ZZSP", -100, 450, "USD",
                     "ZZ SP 500 ETF")
            + qt_row(d(20), d(22), "Buy", "ZZVO", 80, 460, "USD",
                     "ZZ VANGUARD SP 500 ETF")}
        root = project(self.tmp.name, "late", files, ticker_map=MAP)
        r = tj(root, "run", "--no-input", "--details")
        self.assertNotIn("similar fund", console(r))

    @rule("US-WASH-26")
    def test_usa_wash_sale_wording(self):
        root = make(self.tmp.name, "us", country="usa", tfsa=False)
        r = tj(root, "run", "--no-input", "--details")
        self.assertIn("Warning: similar fund in a loss's window: ZZSP.US "
                      "sold at a loss, ZZVO.US bought within 30 days",
                      r.stdout)
        self.assertIn("substantially identical", r.stdout)
        self.assertNotIn("superficial", r.stdout)
        loss, = [g for g in gains(root) if g.get("symbol") == "ZZSP.US"]
        self.assertLess(loss["gain"], -1000)
        self.assertFalse(loss.get("disallowed_amount"))

    def test_malformed_lines_are_refused(self):
        root = make(self.tmp.name, "bad", ticker_map="SIMILAR ZZSP ZZVO.US\n",
                    tfsa=False)
        r = tj(root, "run", "--no-input", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SIMILAR symbol 'ZZSP' must be a share listing with "
                      "its suffix", console(r))
        root = make(self.tmp.name, "one", ticker_map="SIMILAR ZZSP.US\n",
                    tfsa=False)
        r = tj(root, "run", "--no-input", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SIMILAR needs `SIMILAR A B [C ...]`", console(r))

    def test_parser_groups_and_format_map(self):
        from taxjson.lib import ticker_map_format as F
        from taxjson.lib.ticker_map import (side_rules_from_text,
                                            similar_groups, similar_index)
        text = ("SIMILAR ZZSP.US ZZVO.US\nSIMILAR zzvo.us ZZVF.TO\n"
                "SIMILAR ZZA.US ZZB.TO\n")
        side = side_rules_from_text(text)
        self.assertEqual(side.problems, [])
        g = similar_groups(side)
        self.assertEqual(g, [["ZZA.US", "ZZB.TO"],
                             ["ZZSP.US", "ZZVF.TO", "ZZVO.US"]])
        self.assertEqual(similar_index(g)["ZZVF.TO"], ["ZZSP.US", "ZZVO.US"])
        r = F.format_map(text)
        self.assertEqual(F.meaning(r.text), F.meaning(text))
        self.assertIn("SIMILAR A B [C ...]", r.text)
        self.assertIn("SIMILAR ZZSP.US ZZVO.US\n", r.text)
        # The members are symbols the user wrote a line for.
        from taxjson.bin.taxjson_ticker_map import _parse_map_text, named_symbols
        tmap = _parse_map_text(text)[0]
        self.assertIn("ZZVF.TO", named_symbols(tmap, lookups=True))
        self.assertNotIn("ZZVF.TO", named_symbols(tmap))


class TestPlanning(unittest.TestCase):
    """buy-check and sell-check answer as of today: the trades are dated
    from today."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        today = date.today()
        cls.root = make(cls.tmp.name, "live",
                        d=lambda n: (today + timedelta(days=n)).isoformat(),
                        year=today.year)
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-SL-20")
    def test_buy_check_caveat_on_a_similar_loss(self):
        r = tj(self.root, "buy-check", "ZZVO.US", "ZZVF.TO", check=False)
        self.assertEqual(r.returncode, 0, console(r))
        text = " ".join(r.stdout.split())
        self.assertIn("ZZVO.US: SAFE*", text)
        self.assertIn("ZZVF.TO: SAFE*", text)
        self.assertIn("ZZSP.US: COOLING — a loss sold within the past 30 "
                      "days; ticker.map calls it similar to ZZVO.US "
                      "(SIMILAR). The books keep them two securities, so "
                      "buying ZZVO.US denies nothing; it would if you treat "
                      "them as identical property (s.54).", text)
        doc = json.loads(tj(self.root, "buy-check", "ZZVF.TO",
                            "--json").stdout)
        self.assertEqual(doc["results"][0]["verdict"], "SAFE*")
        self.assertTrue(any("similar to ZZVF.TO" in ln
                            for ln in doc["results"][0]["detail"]))

    @rule("CA-SL-20")
    def test_sell_check_caveat_on_a_similar_buy(self):
        r = tj(self.root, "sell-check", "ZZSP.US", check=False)
        self.assertEqual(r.returncode, 0, console(r))
        text = " ".join(r.stdout.split())
        self.assertIn("ZZSP.US: SAFE*", text)
        self.assertIn("ZZVO.US: EXITABLE — bought within the past 30 days; "
                      "ticker.map calls it similar to ZZSP.US (SIMILAR). "
                      "The books keep them two securities, so a loss on "
                      "ZZSP.US stays allowed; it would not if you treat "
                      "them as identical property (s.54).", text)

    @rule("CA-SL-20")
    def test_the_similar_fund_itself_is_still_blocked(self):
        # The caveat never softens the real verdict on the loss's own
        # fund.
        r = tj(self.root, "buy-check", "ZZSP.US", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ZZSP.US: UNSAFE", r.stdout)
        self.assertNotIn("SIMILAR", r.stdout)


if __name__ == "__main__":
    unittest.main()


class TestBookedMembers(unittest.TestCase):
    """A member a TOBASE line moves is read under its booked symbol in
    every view (2026-10 workflow audit)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "tb",
                        ticker_map=MAP + "TOBASE ZZVO.US ZZVO.TO\n")
        cls.r = tj(cls.root, "run", "--no-input", "--details")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-SL-20")
    def test_run_checklist_and_radar_agree(self):
        from taxjson.lib import similar as SIM
        self.assertIn("ZZSP.US sold at a loss, ZZVO.TO bought within 30 days",
                      self.r.stdout)
        pairs = [(f["loss_symbol"], f["other_symbol"])
                 for f in SIM.open_findings(self.root)]
        self.assertEqual(pairs, [("ZZSP.US", "ZZVF.TO"), ("ZZSP.US", "ZZVO.TO")])
        self.assertEqual(SIM.booked_groups(self.root),
                         [["ZZSP.US", "ZZVF.TO", "ZZVO.TO"]])
        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        ctx = cl.Ctx(root=self.root, cfg=cfg, year=2024,
                     today=date(2025, 3, 1),
                     run_sub=lambda argv, timeout=900: (0, "", ""))
        res = cl.d_wash_reviewed(ctx)
        self.assertIn("2 loss(es) with a similar fund", res.detail)
        doc = json.loads(tj(self.root, "wash-radar", "--date", "2024-03-21",
                            "--json").stdout)
        self.assertEqual(doc["similar"][0]["members"],
                         ["ZZSP.US", "ZZVF.TO", "ZZVO.TO"])
        self.assertEqual(doc["similar"][0]["status"]["ZZVO.TO"], "EXITABLE")
