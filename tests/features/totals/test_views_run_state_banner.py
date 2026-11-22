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


# ------------------------------------------- A2-0400 / A2-0694 / A2-0405
class TestViewsSayTheBooksAreNotClean(unittest.TestCase):
    """wash-sales, list and winners carry the run-state banner (per-account
    books after `run --account`), and wash-sales the other-year banner.

    Formerly: test_fix_a2_pipecmd.TestViewsSayTheBooksAreNotClean"""

    def _two_taxable(self, td):
        return _tt_project(td, [], accounts={
            "margin": ("taxable", [
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 50 -5000 0",
                "BUYSELL 2026-06-10 10:00:00 XYZ.TO -100 CAD 40 4000 0"]),
            "cash": ("taxable", [
                "BUYSELL 2026-06-12 10:00:00 XYZ.TO 100 CAD 41 -4100 0"])})

    def test_per_account_books_banner(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._two_taxable(td)
            for a in ("margin", "cash"):
                r = _cli(root, home, "run", "--no-input", "--account", a)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            for cmd in ("wash-sales", "list", "winners"):
                with self.subTest(cmd=cmd):
                    r = _cli(root, home, cmd)
                    self.assertIn("not the clean result", r.stderr,
                                  (r.stdout + r.stderr)[-1000:])

    def test_wash_sales_other_year_banner(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._two_taxable(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace("year = 2026",
                                                   "year = 2025"))
            r = _cli(root, home, "wash-sales")
            self.assertIn("built for another tax year", r.stderr,
                          (r.stdout + r.stderr)[-1000:])


if __name__ == "__main__":
    unittest.main()
