"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _paths
from _support.tt_cash_project import _tt_project


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


class TestConfigCryptoFlag(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestConfigCryptoFlag"""
    def test_quoted_crypto_flag_is_refused(self):
        """S005-00: crypto = "false" read as truthy by every reader."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            t = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(t.replace(
                '[accounts.margin]\ntype = "taxable"\n',
                '[accounts.margin]\ntype = "taxable"\ncrypto = "false"\n'))
            for cmd in ("form-export", "estimate", "sum"):
                r = _cli(root, cmd)
                self.assertNotEqual(r.returncode, 0, cmd)
                self.assertIn("crypto must be true or false", r.stderr)
