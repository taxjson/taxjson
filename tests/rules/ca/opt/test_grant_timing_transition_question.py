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

Formerly: test_fix_newuser_signals
"""
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import console, tj
from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import checklist as cl
from taxjson.lib import out
from taxjson.lib import option_boundary as OB
from taxjson.model.transaction import TaxTransaction
from _support.config_option_grant_projects import flat, toml, make, ctx_of, OPT, WRITE_BUYBACK


# ------------------------------------------------------------ 1. options
def T(action, day, sym, q, net, settle=None):
    return TaxTransaction(action=action, date=day, date_settle=settle or day,
                          symbol=sym, quantity=q, price=abs(net / q) / 100
                          if q else 0.0, net_amount=net, currency="CAD",
                          account="margin")


class TestTransitionQuestionRows(unittest.TestCase):
    """Formerly: test_fix_newuser_signals.TestTransitionQuestionRows"""
    BOOK = [T("BUYSELL", "2025-12-15", OPT, -1, 399.0, "2025-12-16"),
            T("BUYSELL", "2026-01-12", OPT, 1, 101.0, "2026-01-13")]

    @rule(R.CA_OPT_11)
    def test_closed_in_the_project_year_asks(self):
        row, = OB.straddling(self.BOOK, 2026, "grant", 2026, set())
        self.assertTrue(row["question"])
        self.assertFalse(row["attention"])
        self.assertIn("kept on close timing by option_grant_timing_since "
                      "= 2026", row["action"])
        self.assertIn("QUESTION: did your 2025 return report the 399.00 "
                      "premium", row["action"])
        self.assertIn("set option_grant_timing_since = 2025", row["action"])
        self.assertIn("`tjs checklist --done option-boundary`",
                      row["action"])

    @rule(R.CA_OPT_11)
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

    @rule(R.CA_OPT_11)
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
    written in Dec 2025 and bought back in Jan 2026.

    Formerly: test_fix_newuser_signals.TestTransitionQuestionProject"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "opt", 2026,
                        {"margin/book.tt": WRITE_BUYBACK})
        cls.r = tj(cls.root, "run", "--no-input", "--details")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule(R.CA_OPT_11)
    def test_run_warns_with_the_premium_and_the_question(self):
        text = flat(console(self.r))
        self.assertIn("Warning: 1 option contract you wrote in 2025 and "
                      "closed in 2026 is on transition close timing: "
                      "400.00 of premium is taxed in 2026", text)
        self.assertIn("Did your 2025 return report these premiums when the "
                      "contracts were written? If yes, set "
                      "option_grant_timing_since = 2025", text)
        self.assertIn("`tjs checklist --done option-boundary`", text)
        self.assertEqual(out.lint(self.r.stdout), [])

    def test_default_console_asks_in_one_line(self):
        # Essentials first: the default console's one line names the
        # premium and where the question is (the class's run: --details).
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        r = tj(d, "run", "--no-input")
        self.assertIn("Warning: 1 option contract(s) written in 2025: is "
                      "400.00 of premium taxed in 2026 right? — "
                      "`tjs checklist --run`\n", r.stdout)
        self.assertNotIn("Did your 2025 return", r.stdout)

    @rule(R.CA_OPT_11)
    def test_option_boundary_json_rows(self):
        doc = json.loads(tj(self.root, "option-boundary", "--json").stdout)
        q = [r for r in doc["rows"] if r.get("question")]
        self.assertEqual(len(q), 1)
        self.assertIn("QUESTION:", q[0]["action"])

    @rule(R.CA_OPT_11)
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
        text = flat(console(tj(d, "run", "--no-input", "--details")))
        self.assertNotIn("Warning: 1 option contract", text)
        self.assertIn("answered in checklist.json (option-boundary marked "
                      "done: 2025 filed on close timing)", text)

    @rule(R.CA_OPT_11)
    def test_the_setting_settles_it(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        (d / "taxjson.toml").write_text(toml(2026, since=2025))
        text = flat(console(tj(d, "run", "--no-input", "--details")))
        self.assertNotIn("transition close timing", text)
        res = cl.d_option_boundary(ctx_of(d, 2026, date(2026, 10, 1)))
        self.assertEqual(res.status, "done", res.detail)


if __name__ == "__main__":
    unittest.main()
