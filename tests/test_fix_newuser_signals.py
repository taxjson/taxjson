"""What a new user cannot know and taxjson did not say (synthetic data):

1. Option grant timing (CA-OPT-11 / US-OPT-07): a contract written before
   `option_grant_timing_since` and closed in the project year is taxed at
   the close — right only if the write year's return did not report the
   premium. The run warns with the premium at stake, option-boundary
   asks, the checklist and quick-start need attention; a DONE mark or the
   setting lowered to the write year settles it. Never asked in a US
   project.
2. The cross-listing loss radar (CA-XLIST-05 / US-XLIST-04) judges the
   names of the loss's and the purchase's own rows, not every name the
   project gives a listing; names that differ only in voting-share
   wording are a "possible" pair — never a receipt, a class letter or two
   companies.
3. Per-broker export coverage (lib/export_coverage): a broker whose
   exports end before the year end (today in the running year) while it
   holds positions is a run Warning, the checklist's export-coverage
   step and quick-start's inputs step.
4. A sheltered account's corporate-action election is still asked, and
   says it affects the holdings only.
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
from taxjson.lib import option_boundary as OB
from taxjson.lib import quick_start as QS
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



if __name__ == "__main__":
    unittest.main()
