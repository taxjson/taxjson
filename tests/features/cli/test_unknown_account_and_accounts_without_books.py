"""Re-audit-2 planning lists: the views / pipeline part.

  A2-0128  run --account <sheltered> with a sibling's book deleted refuses
           (sheltered_base.json kept) instead of rebuilding without it
  A2-0366  the same export file in two accounts is ATTENTION; --strict stops
  A2-0380 / A2-0381  every command that reads work/ books prints the
           stale-books run-state banner (stderr; --json stays valid)
  A2-0684 / A2-1167  an account-taking command refuses an account that is
           not in [accounts]; harvest names a symbol filter that matches
           nothing
  A2-1182  all-accounts views name a configured account with inputs but
           no books
  A2-0685  reports/<acct>_holdings.toml applies missing_history.json openings
  A2-0693  grant timing: an expired written call counts as a close in
           ccd-sum, winners and the .sum TRADES line
  A2-1190  wrappers relay the child's error line, not a fixed prefix

All data is synthetic.

Formerly: test_fix_a2_planning_views
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


def _cli(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _config(year, accounts, extra=""):
    t = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
         f'base_currency = "CAD"\nsource_currencies = []\n{extra}')
    for n, ty in accounts:
        t += f'[accounts.{n}]\ntype = "{ty}"\n'
    return t


def _project(tmp, accounts, tts, year=2024, extra=""):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_config(year, accounts, extra))
    for acct, text in tts.items():
        d = root / "inputs" / acct
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{acct}.tt").write_text(text)
    return root


# ------------------------------------------------------- A2-0684 / A2-1167
class TestUnknownAccountRefused(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestUnknownAccountRefused"""
    MARGIN = ("BUYSELL 2024-06-10 10:00:00 XYZ.TO 200 CAD 10.00 -2000.00 0.00\n"
              "BUYSELL 2024-09-10 10:00:00 XYZ.TO -100 CAD 12.00 1200.00 0.00\n")

    def test_mistyped_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable")],
                            {"margin": self.MARGIN})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for c in ("edge-cases", "spinoffs", "splits", "check-dates",
                      "winners", "leaps", "leaps-sum"):
                r = _cli(root, c, "margn")
                self.assertNotEqual(r.returncode, 0, c)
                self.assertIn("margn", r.stderr, c)
                self.assertIn("check the name", r.stderr, c)
                self.assertNotIn("run `tjs run` first", r.stderr, c)
                self.assertNotIn("Traceback", r.stderr, c)
            ok = _cli(root, "winners", "margin")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            # harvest takes symbols: one that matches no open position
            # is named, and nothing matching at all is an error.
            h = _cli(root, "harvest", "--no-ibkr", "margn")
            self.assertNotEqual(h.returncode, 0, h.stdout)
            self.assertIn("margn", h.stdout + h.stderr)
            self.assertIn("symbol", h.stdout + h.stderr)


# ---------------------------------------------------------------- A2-1182
class TestViewsNameAccountsWithoutBooks(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestViewsNameAccountsWithoutBooks"""
    AAA = ("BUYSELL 2024-06-10 10:00:00 AAA.TO 10 CAD 10.00 -100.00 0.00\n"
           "BUYSELL 2024-09-10 10:00:00 AAA.TO -5 CAD 12.00 60.00 0.00\n")
    BBB = ("BUYSELL 2024-06-10 10:00:00 BBB.TO 10 CAD 10.00 -100.00 0.00\n"
           "BUYSELL 2024-09-10 10:00:00 BBB.TO -10 CAD 12.00 120.00 0.00\n"
           "BUYSELL 2024-09-11 10:00:00 CCC.TO 10 CAD 12.00 -120.00 0.00\n")

    def test_missing_books_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("aaa", "taxable"), ("bbb", "taxable")],
                            {"aaa": self.AAA, "bbb": self.BBB})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for f in (root / "work").glob("bbb*"):
                f.unlink()
            for c in ("trades", "divs", "events", "roc", "gains", "shares",
                      "fees-sum", "check-dates"):
                r = _cli(root, c)
                self.assertIn("bbb", r.stderr, c)
                self.assertIn("NOT in this report", r.stderr, c)


if __name__ == "__main__":
    unittest.main()
