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


# ---------------------------------------------- A2-0010 (R1-126 regression)
class TestTtUsdSplitRuns(unittest.TestCase):
    """A .tt SPLIT is stamped CAD whatever the listing (kept so row ids
    stay stable). The native gains pass then refused a USD pool's split
    or rename with a currency mismatch and `run` exited 1.

    Formerly: test_fix_a2_pipecmd.TestTtUsdSplitRuns"""

    def _check(self, country, lines, held_sym, held_qty):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, lines, country=country)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertNotIn("Currency mismatch", r.stderr)
            self.assertNotIn("raw holdings skipped", r.stderr)
            hold = (root / "reports" / "m_holdings.toml").read_text()
            self.assertIn(f'symbol = "{held_sym}"', hold)
            self.assertIn(f"quantity = {held_qty}", hold)
            self.assertIn('currency = "USD"', hold)

    def test_usd_split_canada(self):
        self._check("canada", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ.US 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ.US OLDQ.US 2.0",
            "BUYSELL 2026-08-03 10:00:00 OLDQ.US -5 USD 15 75 0"],
            "OLDQ.US", "15.0")

    def test_usd_rename_canada(self):
        self._check("canada", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ.US 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ.US NEWQ.US 1.0",
            "BUYSELL 2026-08-03 10:00:00 NEWQ.US -5 USD 15 75 0"],
            "NEWQ.US", "5.0")

    def test_usd_split_usa(self):
        self._check("usa", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ OLDQ 2.0",
            "BUYSELL 2026-08-03 10:00:00 OLDQ -5 USD 15 75 0"],
            "OLDQ", "15.0")


if __name__ == "__main__":
    unittest.main()
