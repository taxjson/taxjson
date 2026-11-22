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
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from _qa_project import tj
from _support.config_option_grant_projects import flat, toml


# ------------------------------------------- 4. sheltered-account elections
class TestShelteredElection(unittest.TestCase):
    """Formerly: test_fix_newuser_signals.TestShelteredElection"""

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
