"""Workflow audit 2026-10-23, item 1: a missing_history.tt OPENING line
whose purchase is in the books now.

A `.tt` `OPENING ... cost=unknown` line opens its units as written,
never sized from the rows (CA-ACB-11). Once the purchase is found and
added (an older export, a `.tt` BUYSELL), the position never goes short
without the line, yet the line still opens its units at an unknown
cost: the sale stays out of the totals and the books hold units the
broker may not. Before the fix only `find-missing-history` said so (and
called the line one that "does nothing"); the run was silent and the
checklist's missing-history step was done. Now every run says it once,
`find-missing-history` says the run still applies it, and the checklist
step needs attention. Deleting the line puts the sale back in the
totals.

Every fixture is synthetic: invented tickers, amounts and dates.
"""
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, settings_for
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# The purchase, found and added as a .tt line, and the sale it backs.
_FOUND_TT = ("BUYSELL  2022-04-01  09:30:00  SAMPQ.TO  20  CAD  30  600  0\n"
             "BUYSELL  2025-03-10  09:30:00  SAMPQ.TO  -20  CAD  40  800  0\n")
# The line find-missing-history wrote before the purchase was found.
_OPENING = "OPENING 2025-01-09 SAMPQ.TO 20 cost=unknown\n"
_SAID = ("missing_history.tt:1 lists SAMPQ.TO / margin, but its rows never "
         "go short any more")


def _project(tmp, *, opening=True):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        settings_for("canada", year=2025, source_currencies=[])
        + '[accounts.margin]\ntype = "taxable"\n')
    acct = root / "inputs" / "margin"
    acct.mkdir(parents=True)
    (acct / "found.tt").write_text(_FOUND_TT)
    if opening:
        (acct / "missing_history.tt").write_text(_OPENING)
    return root


class TestStaleTtOpening(unittest.TestCase):

    @rule("CA-ACB-11")
    def test_run_says_it_once_and_the_line_still_applies(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            # Said on the run's console, once (the gains stage; the
            # option-boundary check's own opening pass is quiet).
            self.assertEqual(r.stdout.count(_SAID), 1, r.stdout[-3000:])
            # (captured: the stage's own line; a terminal shows it as
            # `Warning: inputs/margin/missing_history.tt:1 lists ...`)
            self.assertIn("ATTENTION: inputs/margin/" + _SAID, r.stdout)
            # The line is applied as written: the sale draws on unknown-
            # cost units and stays out of the totals until it is deleted.
            s = cli(root, "sum")
            self.assertIn("1 disposition(s) with an unknown cost",
                          s.stdout + s.stderr)
            self.assertNotIn("200.00", s.stdout)
            d = cli(root, "run", "--no-input", "--details")
            self.assertIn("still opens its 20 units at an unknown cost",
                          d.stdout)

    def test_find_missing_history_and_checklist_say_the_run_applies_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            f = cli(root, "find-missing-history")
            self.assertIn("entries the run still applies: 1", f.stdout)
            self.assertIn("STALE in missing_history.tt", f.stdout)
            self.assertIn("SAMPQ.TO margin", f.stdout)
            self.assertNotIn("does nothing", f.stdout)
            c = cli(root, "checklist", "--only", "missing-history")
            self.assertIn("[!]", c.stdout.replace("[>]", "[!]"))
            self.assertIn("entry still opens its units", c.stdout)
            self.assertNotIn("[x] 10. missing-history", c.stdout)

    def test_without_the_line_the_sale_is_in_the_totals(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, opening=False)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("never go short", r.stdout)
            s = cli(root, "sum")
            self.assertNotIn("unknown cost", s.stdout + s.stderr)
            self.assertIn("200.00", s.stdout)
            c = cli(root, "checklist", "--only", "missing-history")
            self.assertIn("[x] 10. missing-history", c.stdout)


if __name__ == "__main__":
    unittest.main()
