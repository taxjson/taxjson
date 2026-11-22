"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support.tt_fx_project import REPO_ROOT, _env
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ---------------------------------------------------------------- A2-0715
class TestCoveredCallNamesTheHeldClassShare(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestCoveredCallNamesTheHeldClassShare"""

    def test_ccd_groups_under_held_class_share(self):
        from taxjson.bin._option_gains_report import process_data
        doc = {"transactions": [{
                   "symbol": "QRL270618C00038000.TO", "direction": "SHORT",
                   "cost": -199.0, "proceeds": 0.0, "gain": 199.0,
                   "currency": "CAD", "date": "2026-02-17"}],
               "inventory": [{"symbol": "QRL.B.TO", "qty": 100.0}]}
        groups = {}
        process_data(doc, groups, direction="SHORT", calls_only=True)
        self.assertIn("QRL.B.TO", groups)
        self.assertNotIn("QRL.TO", groups)


# ------------------------------------------ A2-1214 / A2-1216 / A2-1217 / A2-1215
class TestExportInputs(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestExportInputs"""

    def test_dust_threshold_refuses_nan_inf_negative(self):
        for bad in ("nan", "inf", "-1"):
            with self.subTest(bad=bad):
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_export",
                     "--dust-threshold", bad, "--report", "x.json"],
                    cwd=REPO_ROOT, capture_output=True, text=True,
                    env=_env(Path("/nonexistent")), stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("--dust-threshold", r.stderr)

    def test_transfer_in_counts_toward_the_round(self):
        from taxjson.bin.taxjson_export import _load_trade_events
        for opener in ("TRANSFER", "OPENING_BALANCE"):
            with self.subTest(opener=opener):
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "raw.json"
                    p.write_text(json.dumps({"transactions": [
                        {"action": opener, "symbol": "XYZ.US",
                         "quantity": 100, "date": "2026-01-02"},
                        {"action": "BUYSELL", "symbol": "XYZ.US",
                         "quantity": 50, "price": 10, "date": "2026-02-02"},
                        {"action": "BUYSELL", "symbol": "XYZ.US",
                         "quantity": -50, "price": 11,
                         "date": "2026-03-02"}]}))
                    ev = _load_trade_events([p])
                    self.assertEqual([e["action"] for e in ev["XYZ.US"]],
                                     ["BUY", "SELL"])


if __name__ == "__main__":
    unittest.main()
