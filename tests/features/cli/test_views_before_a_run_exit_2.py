"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene.

Formerly: test_cli_audit_2026_09"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _paths

REPO_ROOT = _paths.REPO


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()



def _tj(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


_TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         '[accounts.margin]\ntype = "taxable"\n'
         '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')


class TestNoFalseCleanBeforeRun(unittest.TestCase):  # B22
    """Formerly: test_cli_audit_2026_09.TestNoFalseCleanBeforeRun"""
    def _root(self, td):
        root = Path(td)
        (root / "taxjson.toml").write_text(_TOML.format(year=2024))
        (root / "work").mkdir()
        return root

    def test_tips_without_holdings_exits_2(self):
        # `tips` (scan before) stops: it cannot read the project (exit
        # 2, an input error; a tip itself never fails the command).
        with tempfile.TemporaryDirectory() as td:
            r = _tj(self._root(td), "tips")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Run `tjs run`", r.stderr)
        self.assertNotIn("No tips", r.stdout)

    def test_leaps_without_books_exits_2(self):
        # A missing input: exit 2, as every view (backlog TJ-0644).
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            for sub in ("leaps", "leaps-sum"):
                r = _tj(root, sub)
                self.assertEqual(r.returncode, 2, sub)
                self.assertIn(f"tjs {sub}: error: no gains files", r.stderr)


if __name__ == "__main__":
    unittest.main()
