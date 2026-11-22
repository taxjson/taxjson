"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene.

Formerly: test_cli_audit_2026_09"""

import json
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


class TestRunEmptyProject(unittest.TestCase):        # B21
    """Formerly: test_cli_audit_2026_09.TestRunEmptyProject"""
    def test_strict_empty_project_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_TOML.format(year=2024))
            (root / "inputs" / "margin").mkdir(parents=True)
            r = _tj(root, "run", "--strict")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("no account had any input", r.stderr)
            self.assertNotIn("Done.", r.stdout)
            r = _tj(root, "run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no account had any input", r.stderr)
            doc = json.loads((root / "work" / "skipped_accounts.json")
                             .read_text())
            self.assertEqual(doc["accounts"], ["crypto", "margin"])

    def test_nonexistent_dir_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tj(Path(td) / "nope", "sum")
        # exit 2: a usage error, not a finding (re-audit A2-0164)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such directory", r.stderr)

    def test_typo_warning_precedes_missing_year(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                _TOML.format(year=2024).replace("year = 2024",
                                                "yeer = 2024"))
            r = _tj(root, "run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did you mean 'year'", r.stderr)
        self.assertLess(r.stderr.index("did you mean"),
                        r.stderr.index("missing [settings] year"))


if __name__ == "__main__":
    unittest.main()
