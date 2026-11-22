"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _paths
from _support.tt_cash_project import _TT_MARGIN, _TT_CASH, _tt_project


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()


class TestInstalmentsCaveats(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestInstalmentsCaveats"""
    def test_estimate_warnings_are_relayed(self):
        """S047-22: books built for 2025, project year bumped to 2026."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, instalments=True)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                t.replace("year = 2025", "year = 2026"))
            r = _cli(root, "instalments")
        # The estimate it builds on refuses books older than taxjson.toml
        # (TJ-4347; it warned they were built for another tax year): the
        # refusal is relayed.
        self.assertEqual(r.returncode, 1, r.stdout)
        said = " ".join(r.stderr.split())
        self.assertIn("could not compute the estimate it builds on", said)
        self.assertIn("older than the inputs (changed: taxjson.toml)", said)

    def test_vintage_note_for_an_old_year(self):
        """S043-16: a 2023 schedule says it used later tables."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, year=2023, instalments=True,
                        cash=_TT_CASH.replace("2025", "2023"))
            (root / "inputs" / "margin" / "m.tt").write_text(
                _TT_MARGIN.replace("2025", "2023"))
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            # The note in full: --details (Essentials first); the default
            # view flags it in a `! ` line.
            r = _cli(root, "instalments", "--details")
            b = _cli(root, "instalments")
            j = _cli(root, "instalments", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("predates the earliest built-in rate", r.stdout)
        self.assertIn("! Rate tables of another year used", b.stdout)
        self.assertTrue(json.loads(j.stdout)["vintage_notes"])
