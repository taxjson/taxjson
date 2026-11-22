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


# ---------------------------------------------------------------- A2-0366
class TestSameExportInTwoAccounts(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestSameExportInTwoAccounts"""
    TT = ("BUYSELL 2024-02-05 09:31:00 MSFT.TO 50 CAD 40.00 -2001.00 1.00\n"
          "BUYSELL 2024-08-05 09:31:00 MSFT.TO -50 CAD 42.00 2099.00 1.00\n")

    def test_attention_and_strict_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("a1", "taxable"), ("a2", "taxable")],
                            {"a1": self.TT})
            (root / "inputs" / "a2").mkdir()
            (root / "inputs" / "a2" / "copy.tt").write_text(self.TT)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            out = r.stdout + r.stderr
            self.assertIn("ATTENTION", out)
            self.assertIn("a1/a1.tt", out)
            self.assertIn("a2/copy.tt", out)
            r = _cli(root, "run", "--strict", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
            self.assertIn("a2/copy.tt", r.stdout + r.stderr)

    def test_distinct_files_are_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("a1", "taxable"), ("a2", "taxable")],
                            {"a1": self.TT,
                             "a2": self.TT.replace("2024-02-05", "2024-02-06")})
            r = _cli(root, "run", "--strict", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("same export", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
