"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import tempfile
import unittest

from _support.tt_fx_project import _cli, _tt_project
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ---------------------------------------------------------------- A2-0404
class TestScanIsNotCleanOverMissingBooks(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestScanIsNotCleanOverMissingBooks"""

    def test_missing_holdings_or_raw_book(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"]),
                "tfsa": ("sheltered", [
                    "BUYSELL 2026-01-05 10:00:00 ABC.TO 10 CAD 10 -100 0"])})
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, home, "tips")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for f in (root / "reports" / "tfsa_holdings.toml",
                      root / "work" / "tfsa_raw.json"):
                with self.subTest(f=f.name):
                    data = f.read_bytes()
                    f.unlink()
                    r = _cli(root, home, "tips")
                    self.assertEqual(r.returncode, 2, r.stdout)
                    self.assertNotIn("No tips —", r.stdout)
                    self.assertIn("tfsa", r.stderr)
                    f.write_bytes(data)


if __name__ == "__main__":
    unittest.main()
