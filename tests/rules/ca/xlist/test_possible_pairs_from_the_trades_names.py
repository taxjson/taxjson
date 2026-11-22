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
from unittest import mock

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import xlist_loss_radar as XR
from taxjson.lib import xlist_state as XST
from taxjson.lib.symbol_codes import exact_name
from _support.config_option_grant_projects import flat, make, QT_HEAD, qt_row


# -------------------------------------------------------- 2. xlist radar
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
    """Formerly: test_fix_newuser_signals.TestRadarJudgesTheTradesOwnNames"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @rule(R.CA_XLIST_05)
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
        text = flat(tj(root, "run", "--no-input", "--details").stdout)
        self.assertIn("possible superficial loss across listings: ZZC.US "
                      "sold at a loss, ZZC.TO bought within 30 days", text)
        self.assertIn("The listings are both named 'ZZCELL INC'", text)
        self.assertIn("TOBASE ZZC.US ZZC.TO", text)

    @rule(R.CA_XLIST_05)
    def test_share_wording_only_is_a_possible_pair(self):
        margin = (qt_row("2025-11-03", "2025-11-04", "Buy", "ZZC", 100, 50,
                         "USD", "ZZCELL INC COM")
                  + qt_row("2026-01-06", "2026-01-07", "Sell", "ZZC", -100,
                           40, "USD", "ZZCELL INC COM"))
        rrsp = rrsp_row("2026-01-29", "2026-01-30", "ZZC.TO", 100, 55, "CAD",
                        "ZZCELL INC SUBORD VTG SHS")
        root = radar_project(self.tmp.name, "wording", margin, rrsp)
        text = flat(tj(root, "run", "--no-input", "--details").stdout)
        self.assertIn("possible superficial loss across listings: ZZC.US "
                      "sold at a loss, ZZC.TO bought within 30 days", text)
        self.assertIn("named 'ZZCELL INC COM' and 'ZZCELL INC SUBORD VTG "
                      "SHS' — the same company; the names differ only in "
                      "share wording", text)
        st = XST.read_state(root / "work")
        self.assertEqual(st[0]["names"], ["ZZCELL INC COM",
                                          "ZZCELL INC SUBORD VTG SHS"])
        r = tj(root, "ticker-map", "--suggest", check=False)
        self.assertIn("differ only in share wording", flat(r.stdout))

    @rule(R.US_XLIST_04)
    def test_share_wording_only_usa(self):
        margin = (qt_row("2025-11-03", "2025-11-04", "Buy", "ZZC", 100, 50,
                         "USD", "ZZCELL INC COM")
                  + qt_row("2026-01-06", "2026-01-07", "Sell", "ZZC", -100,
                           40, "USD", "ZZCELL INC COM"))
        rrsp = rrsp_row("2026-01-29", "2026-01-30", "ZZC.TO", 100, 55, "CAD",
                        "ZZCELL INC SUBORD VTG SHS")
        root = radar_project(self.tmp.name, "wording_us", margin, rrsp,
                             country="usa")
        text = flat(tj(root, "run", "--no-input", "--details").stdout)
        self.assertIn("possible wash sale across listings: ZZC.US sold at "
                      "a loss, ZZC.TO bought within 30 days", text)

    @rule(R.CA_XLIST_05)
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

    @rule(R.CA_XLIST_05)
    def test_what_the_scan_shows_apart_is_never_possible(self):
        """The "possible" rule reuses tips' test of two listings the
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

    @rule(R.CA_XLIST_05)
    def test_different_companies_still_dropped_in_a_run(self):
        margin = (qt_row("2025-11-03", "2025-11-04", "Buy", "ZZC", 100, 50,
                         "USD", "ZZCELL INC COM")
                  + qt_row("2026-01-06", "2026-01-07", "Sell", "ZZC", -100,
                           40, "USD", "ZZCELL INC COM"))
        rrsp = rrsp_row("2026-01-29", "2026-01-30", "ZZC.TO", 100, 55, "CAD",
                        "QQOTHERCO HOLDINGS SUBORD VTG SHS")
        root = radar_project(self.tmp.name, "diff", margin, rrsp)
        self.assertNotIn("across listings",
                         flat(tj(root, "run", "--no-input", "--details").stdout))


if __name__ == "__main__":
    unittest.main()
