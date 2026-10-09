"""What a new user cannot know and taxjson did not say (synthetic data):

1. Option grant timing (CA-OPT-11 / US-OPT-07): a contract written before
   `option_grant_timing_since` and closed in the project year is taxed at
   the close — right only if the write year's return did not report the
   premium. The run warns with the premium at stake, option-boundary
   asks, the checklist needs attention; a DONE mark or the
   setting lowered to the write year settles it. Never asked in a US
   project.
2. The cross-listing loss radar (CA-XLIST-05 / US-XLIST-04) judges the
   names of the loss's and the purchase's own rows, not every name the
   project gives a listing; names that differ only in voting-share
   wording are a "possible" pair — never a receipt, a class letter or two
   companies.
3. Per-broker export coverage (lib/export_coverage): a broker whose
   exports end before the year end (today in the running year) while it
   holds positions is a run Warning and the checklist's export-coverage
   step.
4. A sheltered account's corporate-action election is still asked, and
   says there is no tax in the account (its holdings still count for the
   superficial-loss rule).
"""
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path
from unittest import mock

from _qa_project import ENV, console, tj  # noqa: F401
from tax_rules import rule, rule_absent

from taxjson.lib import checklist as cl
from taxjson.lib import out
from taxjson.lib import export_coverage as EC
from taxjson.lib import option_boundary as OB
from taxjson.lib import xlist_loss_radar as XR
from taxjson.lib.core import TaxTransaction
from taxjson.lib.symbol_codes import exact_name
from taxjson.lib.tomlcompat import tomllib


def flat(text):
    return " ".join(text.split())


def toml(year, country="canada", since=None, extra=""):
    if country == "usa":
        head = ('[settings]\nlocal_timezone = "America/New_York"\n'
                f'year = {year}\ncountry = "usa"\nbase_currency = "USD"\n'
                'source_currencies = ["CAD"]\n')
    else:
        head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
                f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                f'option_grant_timing_since = {since or year}\n')
    return head + '[accounts.margin]\ntype = "taxable"\n' + extra


def make(tmp, name, year, files, **kw):
    root = Path(tmp) / name
    for rel, text in files.items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / "taxjson.toml").write_text(toml(year, **kw))
    return root


def cfg_of(root):
    return tomllib.loads((root / "taxjson.toml").read_text())


def ctx_of(root, year, today):
    return cl.Ctx(root=root, cfg=cfg_of(root), year=year, today=today,
                  run_sub=cl.default_run_sub(root))


# ------------------------------------------------------------ 1. options
OPT = "ZZQ261218C00050000.TO"
WRITE_BUYBACK = (f"BUYSELL 2025-12-15 10:00:00 {OPT} -1 CAD 4 400\n"
                 f"BUYSELL 2026-01-12 10:00:00 {OPT} 1 CAD 1 100\n")


def T(action, day, sym, q, net, settle=None):
    return TaxTransaction(action=action, date=day, date_settle=settle or day,
                          symbol=sym, quantity=q, price=abs(net / q) / 100
                          if q else 0.0, net_amount=net, currency="CAD",
                          account="margin")


class TestTransitionQuestionRows(unittest.TestCase):
    BOOK = [T("BUYSELL", "2025-12-15", OPT, -1, 399.0, "2025-12-16"),
            T("BUYSELL", "2026-01-12", OPT, 1, 101.0, "2026-01-13")]

    @rule("CA-OPT-11")
    def test_closed_in_the_project_year_asks(self):
        row, = OB.straddling(self.BOOK, 2026, "grant", 2026, set())
        self.assertTrue(row["question"])
        self.assertFalse(row["attention"])
        self.assertIn("kept on close timing by option_grant_timing_since "
                      "= 2026", row["action"])
        self.assertIn("QUESTION: did your 2025 return report the 399.00 "
                      "premium", row["action"])
        self.assertIn("set option_grant_timing_since = 2025", row["action"])
        self.assertIn("`taxjson checklist --done option-boundary`",
                      row["action"])

    @rule("CA-OPT-11")
    def test_settled_or_not_this_years(self):
        # The setting lowered to the write year: grant timing, no question.
        row, = OB.straddling(self.BOOK, 2026, "grant", 2025, set())
        self.assertFalse(row["question"])
        # A lock that records close timing for 2025: the transition is
        # known to be right.
        row, = OB.straddling(self.BOOK, 2026, "grant", 2026, set(),
                             filed_timing={2025: {
                                 "option_premium_timing": "close"}})
        self.assertFalse(row["question"])
        # A locked 2025 with no timing record: the ATTENTION, not a
        # question.
        row, = OB.straddling(self.BOOK, 2026, "grant", 2026, {2025})
        self.assertTrue(row["attention"])
        self.assertFalse(row["question"])
        # Closed in another year than the project's: nothing at stake.
        rows = OB.straddling(self.BOOK, 2027, "grant", 2027, set())
        self.assertFalse(any(r["question"] for r in rows))
        # Close timing chosen for every year: no transition.
        row, = OB.straddling(self.BOOK, 2026, "close", None, set())
        self.assertFalse(row["question"])

    @rule("CA-OPT-11")
    def test_assignment_is_not_asked(self):
        book = [T("BUYSELL", "2025-12-15", OPT, -1, 399.0),
                TaxTransaction(action="ASSIGN", date="2026-01-16",
                               date_settle="2026-01-16", symbol=OPT,
                               quantity=1, currency="CAD", account="margin"),
                TaxTransaction(action="ASSIGN", date="2026-01-16",
                               date_settle="2026-01-19", symbol="ZZQ.TO",
                               quantity=-100, price=50, net_amount=5000.0,
                               currency="CAD", account="margin")]
        rows = OB.straddling(book, 2026, "grant", 2026, set())
        self.assertEqual([r["close_kind"] for r in rows], ["assignment"])
        self.assertFalse(rows[0]["question"])


class TestTransitionQuestionProject(unittest.TestCase):
    """A 2026 project (since = 2026, the template's default) with a call
    written in Dec 2025 and bought back in Jan 2026."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "opt", 2026,
                        {"margin/book.tt": WRITE_BUYBACK})
        cls.r = tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-OPT-11")
    def test_run_warns_with_the_premium_and_the_question(self):
        text = flat(console(self.r))
        self.assertIn("Warning: 1 option contract you wrote in 2025 and "
                      "closed in 2026 is on transition close timing: "
                      "400.00 of premium is taxed in 2026", text)
        self.assertIn("Did your 2025 return report these premiums when the "
                      "contracts were written? If yes, set "
                      "option_grant_timing_since = 2025", text)
        self.assertIn("`taxjson checklist --done option-boundary`", text)
        self.assertEqual(out.lint(self.r.stdout), [])

    @rule("CA-OPT-11")
    def test_option_boundary_json_rows(self):
        doc = json.loads(tj(self.root, "option-boundary", "--json").stdout)
        q = [r for r in doc["rows"] if r.get("question")]
        self.assertEqual(len(q), 1)
        self.assertIn("QUESTION:", q[0]["action"])

    @rule("CA-OPT-11")
    def test_checklist_until_marked(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        res = cl.d_option_boundary(ctx_of(d, 2026, date(2026, 10, 1)))
        self.assertEqual(res.status, "attention")
        self.assertTrue(res.question)
        self.assertIn("did your 2025 return report these premiums", res.detail)
        self.assertIn("400.00 of premium taxed in 2026", res.detail)
        res, = cl.evaluate(ctx_of(d, 2026, date(2026, 10, 1)),
                           only=["option-boundary"])
        self.assertEqual(res.effective, "attention")
        # The answer: a DONE mark settles it everywhere.
        tj(d, "checklist", "--done", "option-boundary", "--note",
           "2025 filed on close timing", check=False)
        res, = cl.evaluate(ctx_of(d, 2026, date(2026, 10, 1)),
                           only=["option-boundary"])
        self.assertEqual(res.effective, "done")
        self.assertIn("did your 2025 return", res.finding)
        text = flat(console(tj(d, "run", "--no-input")))
        self.assertNotIn("Warning: 1 option contract", text)
        self.assertIn("answered in checklist.json (option-boundary marked "
                      "done: 2025 filed on close timing)", text)

    @rule("CA-OPT-11")
    def test_the_setting_settles_it(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        (d / "taxjson.toml").write_text(toml(2026, since=2025))
        text = flat(console(tj(d, "run", "--no-input")))
        self.assertNotIn("transition close timing", text)
        res = cl.d_option_boundary(ctx_of(d, 2026, date(2026, 10, 1)))
        self.assertEqual(res.status, "done", res.detail)


class TestTransitionQuestionUSA(unittest.TestCase):
    @rule_absent("CA-OPT-11", country="usa")
    @rule("US-OPT-07")
    def test_never_asked_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "us", 2026,
                        {"margin/book.tt": WRITE_BUYBACK.replace(
                            ".TO", ".US").replace("CAD", "USD")},
                        country="usa")
            text = flat(console(tj(root, "run", "--no-input")))
            self.assertNotIn("transition close timing", text)
            self.assertEqual(OB.project_question_rows(root, cfg_of(root)),
                             [])
            res, = cl.evaluate(ctx_of(root, 2026, date(2026, 10, 1)),
                               only=["option-boundary"])
            self.assertFalse(res.question)
            self.assertNotIn("did your 2025 return", res.detail)


# -------------------------------------------------------- 2. xlist radar
QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")


def qt_row(day, settle, action, sym, qty, price, cur, name,
           acct="55500001", kind="Margin"):              # pii-ok
    gross = qty * price
    return (f"{day},{settle},{action},{sym},{name},{qty},{price:.2f},"
            f"{-gross:.2f},0,{-gross:.2f},{cur},Trades,{acct},{kind}\n")


SHELTERED = '[accounts.rrsp]\ntype = "sheltered"\n'


def radar_project(tmp, name, margin_rows, rrsp_rows="", country="canada"):
    files = {"margin/questrade_2026.csv": QT_HEAD + margin_rows}
    if rrsp_rows:
        files["rrsp/questrade_2026.csv"] = QT_HEAD + rrsp_rows
    return make(tmp, name, 2026, files, country=country,
                extra=SHELTERED if rrsp_rows else "")


def rrsp_row(day, settle, sym, qty, price, cur, name):
    return qt_row(day, settle, "Buy", sym, qty, price, cur, name,
                  acct="55500002", kind="RRSP")              # pii-ok


class TestRadarJudgesTheTradesOwnNames(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @rule("CA-XLIST-05")
    def test_another_accounts_wording_does_not_hide_the_pair(self):
        """The owner's case in synthetic form: the loss and the purchase
        are named alike by their own broker rows; a registered account's
        rows name the TSX line with its voting wording. The project-wide
        names used to drop the pair."""
        nm = "ZZCELL INC"
        margin = (qt_row("2025-11-03", "2025-11-04", "Buy", "ZZC", 100, 50,
                         "USD", nm)
                  + qt_row("2026-01-06", "2026-01-07", "Sell", "ZZC", -100,
                           40, "USD", nm)
                  + qt_row("2026-01-29", "2026-01-30", "Buy", "ZZC.TO", 100,
                           55, "CAD", nm))
        rrsp = rrsp_row("2025-06-02", "2025-06-03", "ZZC.TO", 50, 50, "CAD",
                        "ZZCELL INC SUBORD VTG SHS")
        root = radar_project(self.tmp.name, "own", margin, rrsp)
        text = flat(tj(root, "run", "--no-input").stdout)
        self.assertIn("possible superficial loss across listings: ZZC.US "
                      "sold at a loss, ZZC.TO bought within 30 days", text)
        self.assertIn("The listings are both named 'ZZCELL INC'", text)
        self.assertIn("TOBASE ZZC.US ZZC.TO", text)

    @rule("CA-XLIST-05")
    def test_share_wording_only_is_a_possible_pair(self):
        margin = (qt_row("2025-11-03", "2025-11-04", "Buy", "ZZC", 100, 50,
                         "USD", "ZZCELL INC COM")
                  + qt_row("2026-01-06", "2026-01-07", "Sell", "ZZC", -100,
                           40, "USD", "ZZCELL INC COM"))
        rrsp = rrsp_row("2026-01-29", "2026-01-30", "ZZC.TO", 100, 55, "CAD",
                        "ZZCELL INC SUBORD VTG SHS")
        root = radar_project(self.tmp.name, "wording", margin, rrsp)
        text = flat(tj(root, "run", "--no-input").stdout)
        self.assertIn("possible superficial loss across listings: ZZC.US "
                      "sold at a loss, ZZC.TO bought within 30 days", text)
        self.assertIn("named 'ZZCELL INC COM' and 'ZZCELL INC SUBORD VTG "
                      "SHS' — the same company; the names differ only in "
                      "share wording", text)
        st = XR.read_state(root / "work")
        self.assertEqual(st[0]["names"], ["ZZCELL INC COM",
                                          "ZZCELL INC SUBORD VTG SHS"])
        r = tj(root, "scan", check=False)
        self.assertIn("differ only in share wording", flat(r.stdout))

    @rule("US-XLIST-04")
    def test_share_wording_only_usa(self):
        margin = (qt_row("2025-11-03", "2025-11-04", "Buy", "ZZC", 100, 50,
                         "USD", "ZZCELL INC COM")
                  + qt_row("2026-01-06", "2026-01-07", "Sell", "ZZC", -100,
                           40, "USD", "ZZCELL INC COM"))
        rrsp = rrsp_row("2026-01-29", "2026-01-30", "ZZC.TO", 100, 55, "CAD",
                        "ZZCELL INC SUBORD VTG SHS")
        root = radar_project(self.tmp.name, "wording_us", margin, rrsp,
                             country="usa")
        text = flat(tj(root, "run", "--no-input").stdout)
        self.assertIn("possible wash sale across listings: ZZC.US sold at "
                      "a loss, ZZC.TO bought within 30 days", text)

    @rule("CA-XLIST-05")
    def test_never_a_receipt_a_class_or_another_company(self):
        def k(*names):
            return {exact_name(n) for n in names}
        com = k("ZZCELL INC COM")
        sv = k("ZZCELL INC SUBORD VTG SHS")
        self.assertIsNotNone(XR._wording_only(com, sv, com, sv, "ZZC.US",
                                              "ZZC.TO"))
        # A depositary receipt is its own security.
        cdr = k("ZZCELL INC CDR")
        self.assertIsNone(XR._wording_only(com, cdr, com, cdr, "ZZC.US",
                                           "ZZC.TO"))
        # Two voting classes named somewhere: two securities.
        mv = k("ZZCELL INC MULTIPLE VTG SHS")
        self.assertIsNone(XR._wording_only(com, sv, com, sv | mv, "ZZC.US",
                                           "ZZC.TO"))
        # A class letter anywhere: an issuer with classes.
        cla = k("ZZCELL INC CL A SUBORD VTG")
        self.assertIsNone(XR._wording_only(com, cla, com, cla, "ZZC.US",
                                           "ZZC.TO"))
        # Another designator (preferred) is not wording.
        pfd = k("ZZCELL INC PFD")
        self.assertIsNone(XR._wording_only(com, pfd, com, pfd, "ZZC.US",
                                           "ZZC.TO"))
        # Two companies.
        other = k("QQOTHERCO HOLDINGS SUBORD VTG SHS")
        self.assertIsNone(XR._wording_only(com, other, com, other, "ZZC.US",
                                           "ZZC.TO"))

    @rule("CA-XLIST-05")
    def test_what_the_scan_shows_apart_is_never_possible(self):
        """The "possible" rule reuses the scan's test of two listings the
        exports show apart (cross_listings.shown_apart)."""
        from taxjson.lib import cross_listings as XL
        com = {exact_name("ZZCELL INC COM")}
        sv = {exact_name("ZZCELL INC SUBORD VTG SHS")}
        with mock.patch.object(XL, "shown_apart",
                               return_value="ZZC.TO is a receipt") as m:
            self.assertIsNone(XR._wording_only(com, sv, com, sv, "ZZC.US",
                                               "ZZC.TO"))
        m.assert_called_once()
        self.assertEqual(XL.shown_apart("ZZC.US", "ZZC.TO",
                                        {"ZZC.US": com, "ZZC.TO": sv}), "")

    @rule("CA-XLIST-05")
    def test_different_companies_still_dropped_in_a_run(self):
        margin = (qt_row("2025-11-03", "2025-11-04", "Buy", "ZZC", 100, 50,
                         "USD", "ZZCELL INC COM")
                  + qt_row("2026-01-06", "2026-01-07", "Sell", "ZZC", -100,
                           40, "USD", "ZZCELL INC COM"))
        rrsp = rrsp_row("2026-01-29", "2026-01-30", "ZZC.TO", 100, 55, "CAD",
                        "QQOTHERCO HOLDINGS SUBORD VTG SHS")
        root = radar_project(self.tmp.name, "diff", margin, rrsp)
        self.assertNotIn("across listings",
                         flat(tj(root, "run", "--no-input").stdout))


# ------------------------------------------------------- 3. export coverage
WB_HEAD = ('"Currency","Date","Action Code","Symbol","Security Description",'
           '"Type Code","Quantity","Price","Proceeds"\n')


def webull(end, rows):
    return ("Webull Securities (Canada) Ltd.\nSynthetic Demo Statement\n"
            "Account Number: 12345678\n"
            f"Date Range: January 1 2025 - {end}\n\n" + WB_HEAD + rows)


WB_OPEN_CALLS = (
    'USD,15-01-2025,BUY,@ZZAA,ZZAA HOLDINGS INC,EQ,100,85.00,"(8,500.00)"\n'
    'USD,20-03-2025,SELL,@ZZAA,ZZAA HOLDINGS INC,EQ,100,90.00,"8,995.05"\n'
    'USD,22-04-2025,BUY,@ZZBB,CALL ZZBB01/16/26 45,OPC,14,1.55,'
    '"(2,184.75)"\n')
WB_ALL_CLOSED = (
    'USD,15-01-2025,BUY,@ZZAA,ZZAA HOLDINGS INC,EQ,100,85.00,"(8,500.00)"\n'
    'USD,20-03-2025,SELL,@ZZAA,ZZAA HOLDINGS INC,EQ,100,90.00,"8,995.05"\n')


class TestExportCoverage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "wb", 2025, {
            "margin/webull_2025.csv": webull("September 30 2025",
                                             WB_OPEN_CALLS)})
        cls.r = tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_run_warns_for_the_short_broker(self):
        text = flat(console(self.r))
        self.assertIn("Warning: Webull exports for margin end 2025-09-30 "
                      "with open positions (ZZBB260116C00045000.US 14); "
                      "download the rest of 2025", text)
        self.assertIn("The export's date range ends 2025-09-30.", text)
        self.assertEqual(out.lint(self.r.stdout), [])

    def test_checklist(self):
        res = cl.d_export_coverage(ctx_of(self.root, 2025, date(2026, 3, 1)))
        self.assertEqual(res.status, "attention")
        # A question even for the export's own end (v0.24.1 review: the
        # user may know the broker had no later activity); a DONE mark
        # answers this (account, broker, end) only.
        self.assertTrue(res.question)
        self.assertEqual(res.answers, ["margin|webull|2025-09-30"])
        self.assertIn("Webull exports for margin end 2025-09-30", res.detail)
        res, = cl.evaluate(ctx_of(self.root, 2025, date(2026, 3, 1)),
                           only=["export-coverage"])
        self.assertEqual(res.effective, "attention")
        self.assertIn("download the rest of the year", res.detail)

    def test_no_nag_when_every_position_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "closed", 2025, {
                "margin/webull_2025.csv": webull("September 30 2025",
                                                 WB_ALL_CLOSED)})
            text = flat(console(tj(root, "run", "--no-input")))
            self.assertNotIn("exports for margin end", text)
            self.assertEqual(EC.find_gaps(root, cfg_of(root),
                                          today=date(2026, 3, 1)), [])

    def test_full_year_export_is_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "full", 2025, {
                "margin/webull_2025.csv": webull("December 31 2025",
                                                 WB_OPEN_CALLS)})
            tj(root, "run", "--no-input")
            self.assertEqual(EC.find_gaps(root, cfg_of(root),
                                          today=date(2026, 3, 1)), [])

    def test_last_row_end_is_a_question_a_mark_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = (qt_row("2025-02-03", "2025-02-04", "Buy", "ZZD.TO", 100,
                           20, "CAD", "ZZDELTA CORP")
                    + qt_row("2025-06-02", "2025-06-03", "Buy", "ZZE.TO", 10,
                             20, "CAD", "ZZECHO CORP"))
            root = make(tmp, "qt", 2025, {"margin/questrade_2025.csv":
                                          QT_HEAD + rows})
            text = flat(console(tj(root, "run", "--no-input")))
            self.assertIn("Questrade exports for margin end 2025-06-02 with "
                          "open positions (ZZD.TO 100, ZZE.TO 10)", text)
            self.assertIn("The last row is dated 2025-06-02; the export "
                          "itself names no end date.", text)
            ctx = ctx_of(root, 2025, date(2026, 3, 1))
            res = cl.d_export_coverage(ctx)
            self.assertTrue(res.question)
            tj(root, "checklist", "--done", "export-coverage", check=False)
            res, = cl.evaluate(ctx, only=["export-coverage"])
            self.assertEqual(res.effective, "done")
            text = flat(console(tj(root, "run", "--no-input")))
            self.assertNotIn("Warning: Questrade exports", text)
            self.assertIn("marked done in checklist.json (export-coverage)",
                          text)

    def _year_summary_project(self, tmp, tt):
        """A 2026 project whose Webull file is the 2025 trading summary
        (no 2026 rows) holding 14 calls and 10 ZZCC shares at its end;
        the account's own .tt books what happened in 2026."""
        summary = ("Account Number / Numero de compte:,,,,,,,,12345678,\n"
                   "Year / Annee:,,,,,,,,2025,\n"
                   "Report / Rapport:,,,,,,,,TRADING SUMMARY,\n\n"
                   + WB_HEAD
                   + 'USD,22-04-2025,BUY,@ZZBB,CALL ZZBB01/15/27 45,OPC,14,'
                     '1.55,"(2,184.75)"\n'
                   + 'USD,23-04-2025,BUY,@ZZCC,ZZCC HOLDINGS INC,EQ,10,'
                     '20.00,"(200.00)"\n')
        files = {"margin/webull_2025.csv": summary}
        if tt:
            files["margin/wb_2026_manual.tt"] = tt
        return make(tmp, "ys", 2026, files)

    def test_later_tt_closes_make_it_an_info(self):
        """Positions open at the export's end that the account's own .tt
        lines close later are not missing: an Info, no Warning, the
        checklist step done."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._year_summary_project(
                tmp, "BUYSELL 2026-06-15 10:00:00 ZZBB270115C00045000.US "
                     "-14 USD 2 2800\n"
                     "BUYSELL 2026-06-16 10:00:00 ZZCC.US -10 USD 25 250\n")
            text = flat(console(tj(root, "run", "--no-input")))
            self.assertNotIn("Warning: Webull exports", text)
            self.assertIn("Info: Webull exports for margin end 2025-12-31; "
                          "the positions open at the export end "
                          "(ZZBB270115C00045000.US, ZZCC.US) were closed by "
                          ".tt lines — no export needed", text)
            g, = EC.find_gaps(root, cfg_of(root), today=date(2026, 10, 7))
            self.assertTrue(g.info)
            res = cl.d_export_coverage(ctx_of(root, 2026, date(2026, 10, 7)))
            self.assertEqual(res.status, "done", res.detail)

    def test_tt_closes_after_the_gap_do_not_cover_it(self):
        """A finished year: the export ends 2025-09-30 and the calls are
        closed by a .tt line dated 2026 — outside the gap (Oct–Dec 2025),
        so a missing 2025 sale would still be missing: a Warning."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "after", 2025, {
                "margin/webull_2025.csv": webull("September 30 2025",
                                                 WB_OPEN_CALLS),
                "margin/wb_2026_manual.tt":
                    "BUYSELL 2026-01-05 10:00:00 ZZBB260116C00045000.US "
                    "-14 USD 2 2800\n"})
            text = flat(console(tj(root, "run", "--no-input")))
            self.assertIn("Warning: Webull exports for margin end 2025-09-30 "
                          "with open positions (ZZBB260116C00045000.US 14); "
                          "download the rest of 2025", text)
            self.assertNotIn("were closed by .tt lines", text)
            g, = EC.find_gaps(root, cfg_of(root), today=date(2026, 3, 1))
            self.assertFalse(g.info)

    def test_only_positions_still_open_are_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._year_summary_project(
                tmp, "BUYSELL 2026-06-15 10:00:00 ZZBB270115C00045000.US "
                     "-14 USD 2 2800\n")
            text = flat(console(tj(root, "run", "--no-input")))
            self.assertIn("Warning: Webull exports for margin end 2025-12-31 "
                          "with open positions (ZZCC.US 10); download the "
                          "rest of 2026", text)
            self.assertIn("The trading summary covers 2025 only.", text)
            self.assertIn("Closed by later .tt lines, not listed: "
                          "ZZBB270115C00045000.US.", text)

    def test_closed_later(self):
        held = [("ZZG.TO", 20.0), ("ZZH.TO", 5.0), ("ZZI.TO", 3.0)]
        later = [
            {"action": "BUYSELL", "date": "2026-02-01", "symbol": "ZZG.TO",
             "quantity": -20, "source": "fix.tt"},
            {"action": "TRANSFER", "date": "2026-02-01", "symbol": "ZZH.TO",
             "quantity": -5, "source": "U_2026.csv"},
            {"action": "BUYSELL", "date": "2026-02-01", "symbol": "ZZI.TO",
             "quantity": -1, "source": "fix.tt"}]
        self.assertEqual(EC.closed_later(held, later),
                         ([("ZZI.TO", 2.0)], ["ZZG.TO"], ["ZZH.TO"]))

    def test_running_year_compares_with_today(self):
        """The year still running: an IB statement ending more than
        GRACE_DAYS before today is short; one ending last week is not."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "inputs" / "rrsp").mkdir(parents=True)
            (root / "inputs" / "rrsp" / "U_2026.csv").write_text(
                "Statement,Header,Field Name,Field Value\n"
                'Statement,Data,Period,"January 1, 2026 - September 4, '
                '2026"\n')
            (root / "work" / "rrsp_ib.json").write_text(json.dumps({
                "metadata": {"input_files": ["/x/U_2026.csv"]},
                "transactions": []}))
            (root / "work" / "rrsp_base.json").write_text(json.dumps({
                "transactions": [{"action": "BUYSELL", "date": "2026-03-02",
                                  "symbol": "ZZF.US", "quantity": 10,
                                  "source": "U_2026.csv"}]}))
            cfg = {"settings": {"year": 2026},
                   "accounts": {"rrsp": {"type": "sheltered"}}}
            g, = EC.find_gaps(root, cfg, today=date(2026, 10, 7))
            self.assertEqual((g.broker, g.end, g.how, g.current_year),
                             ("ib", "2026-09-04", "statement", True))
            self.assertEqual(g.positions, [("ZZF.US", 10.0)])
            self.assertEqual(EC.find_gaps(root, cfg,
                                          today=date(2026, 9, 10)), [])

    def test_open_positions(self):
        rows = [
            {"action": "BUYSELL", "date": "2025-01-02", "symbol": "ZZG.TO",
             "quantity": 10},
            {"action": "SPLIT", "date": "2025-02-02", "symbol": "ZZG.TO",
             "quantity": 2},
            {"action": "BUYSELL", "date": "2025-03-02", "symbol": "ZZH.TO",
             "quantity": -5},               # a sale with no purchase
            {"action": "BUYSELL", "date": "2025-03-02",
             "symbol": "ZZI250321C00010000.US", "quantity": 1},
            {"action": "BUYSELL", "date": "2025-03-02",
             "symbol": "ZZI251219P00010000.US", "quantity": -2},
            {"action": "TRANSFER", "date": "2025-04-02", "symbol": "ZZJ.TO",
             "quantity": 3},
            {"action": "TRANSFER", "date": "2025-05-02", "symbol": "ZZJ.TO",
             "quantity": -3},
        ]
        self.assertEqual(EC.open_positions(rows, date(2025, 6, 30)),
                         [("ZZG.TO", 20.0),
                          ("ZZI251219P00010000.US", -2.0)])


# ------------------------------------------- 4. sheltered-account elections
class TestShelteredElection(unittest.TestCase):

    def _event(self):
        from taxjson.lib.corp_actions import CorporateAction
        return CorporateAction(
            date="2025-05-01", time="00:00:00", action_type="spinoff",
            source_symbol="ZZK.TO", source_isin="", target_symbol="ZZL.TO",
            target_isin="", ratio_new=1, ratio_old=10, qty_disposed=0,
            qty_received=10, fmv=0.0, currency="CAD",
            target_currency="CAD", account="rrsp", event_id="ev1")

    def test_prompt_says_holdings_only(self):
        from taxjson.bin import taxjson_corp_actions as CA
        err = io.StringIO()
        with mock.patch("builtins.input", side_effect=["1", "0", "y", ""]), \
                redirect_stderr(err):
            CA._prompt_election(self._event(), "canada", sheltered=True)
        self.assertIn("Sheltered account: this election sets the "
                      "holdings' cost in the books — no tax in this "
                      "account; its holdings still count for the "
                      "superficial-loss rule",
                      flat(err.getvalue()))
        err = io.StringIO()
        with mock.patch("builtins.input", side_effect=["1", "0", "y", ""]), \
                redirect_stderr(err):
            CA._prompt_election(self._event(), "canada")
        self.assertNotIn("Sheltered account", err.getvalue())

    def test_pending_list_says_holdings_only(self):
        from taxjson.bin import taxjson_corp_actions as CA
        doc = CA._pending_doc([self._event()], Path("manifest.json"),
                              "canada", sheltered=True)
        self.assertTrue(doc["pending"][0]["sheltered"])
        self.assertFalse(CA._pending_doc([self._event()],
                                         Path("manifest.json"),
                                         "canada")["pending"][0]["sheltered"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                toml(2025, extra='[accounts.rrsp]\ntype = "sheltered"\n'))
            (root / "work").mkdir()
            (root / "inputs" / "rrsp").mkdir(parents=True)
            (root / "work" / "pending_elections.json").write_text(
                json.dumps({"schema_version": 1,
                            "accounts": {"rrsp": doc}}))
            r = tj(root, "elect", "--pending", check=False)
            self.assertIn("Sheltered account: this election sets the "
                          "holdings' cost in the books — no tax in this "
                          "account; its holdings still count for the "
                          "superficial-loss rule",
                          flat(r.stdout))


if __name__ == "__main__":
    unittest.main()
