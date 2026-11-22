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
import tempfile
import unittest
from datetime import date

from _qa_project import console, tj
from tax_rules import rule, rule_absent
from taxjson.rules import ids as R

from taxjson.lib import checklist as cl
from taxjson.lib import option_boundary as OB
from _support.config_option_grant_projects import flat, make, cfg_of, ctx_of, WRITE_BUYBACK


class TestTransitionQuestionUSA(unittest.TestCase):
    """Formerly: test_fix_newuser_signals.TestTransitionQuestionUSA"""
    @rule_absent(R.CA_OPT_11, country="usa")
    @rule(R.US_OPT_07)
    def test_never_asked_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "us", 2026,
                        {"margin/book.tt": WRITE_BUYBACK.replace(
                            ".TO", ".US").replace("CAD", "USD")},
                        country="usa")
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertNotIn("transition close timing", text)
            self.assertEqual(OB.project_question_rows(root, cfg_of(root)),
                             [])
            res, = cl.evaluate(ctx_of(root, 2026, date(2026, 10, 1)),
                               only=["option-boundary"])
            self.assertFalse(res.question)
            self.assertNotIn("did your 2025 return", res.detail)


if __name__ == "__main__":
    unittest.main()
