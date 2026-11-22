"""2026-09 CLI usability audit: wrong-year close-year lock, untyped
accounts, false "run first" warnings, exit codes, and argument hygiene.

Formerly: test_cli_audit_2026_09"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from taxjson.pipeline import config as pipeline_config
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



class TestUntypedAccountFatal(unittest.TestCase):    # B3
    """Formerly: test_cli_audit_2026_09.TestUntypedAccountFatal"""
    def test_missing_type_dies_listing_the_choices(self):
        with self.assertRaises(SystemExit) as cm:
            pipeline_config.validate_config({"settings": {"year": 2025,
                                            "country": "canada"},
                               "accounts": {"margin": {}}})
        self.assertIn("taxable | sheltered", str(cm.exception))


class TestInit(unittest.TestCase):                   # B8 + polish
    """Formerly: test_cli_audit_2026_09.TestInit"""
    def test_next_steps_put_dash_c_first_and_quote(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "my taxes"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "init",
                 str(target), "--country", "ca", "--year", "2025"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        # (one project for every year: the commands run in the year's
        # folder)
        self.assertIn(f"tjs -C '{target.resolve() / '2025'}' run",
                      r.stdout)

    def test_far_future_year_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "init",
                 td, "--country", "ca", "--year", "2099"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a plausible tax year", r.stderr)

    def test_force_backs_up_and_lists_orphans(self):
        with tempfile.TemporaryDirectory() as td:
            base = [sys.executable, "-m", "taxjson.bin.taxjson_run",
                    "init", "--single", td, "--year", "2025"]
            subprocess.run(base + ["--country", "ca"], cwd=REPO_ROOT,
                           capture_output=True, check=True)
            r = subprocess.run(base + ["--country", "us", "--force"],
                               cwd=REPO_ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            bak = (Path(td) / "taxjson.toml.bak").read_text()
            # (the Canadian file, whatever the `=` column's padding and
            # the values list at the end of the line)
            self.assertRegex(bak, r'(?m)^country += "canada"(?: +# .*)?$')
            self.assertIn("tfsa", r.stdout)
            self.assertIn("no [accounts.*] section", r.stdout)


if __name__ == "__main__":
    unittest.main()
