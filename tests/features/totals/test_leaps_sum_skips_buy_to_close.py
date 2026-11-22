"""Regressions for FUZZ-2026-07 confirmed findings (minimal repros).

Formerly: test_fuzz_fixes"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO


class TestFuzzL_LeapsBuyToCloseNotEntry(unittest.TestCase):
    """#L: a short call's buy-to-close (positive qty against a short
    position) was classified as a LEAPS entry buy — the same
    covered-call gain appeared in BOTH ccd-sum and leaps-sum.

    Formerly: test_fuzz_fixes.TestFuzzL_LeapsBuyToCloseNotEntry"""

    SHORT_CALL = "QBN270115C00083000.TO"      # >3 months to expiry
    LONG_CALL = "QAP270115C00145000.US"

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_raw.json").write_text(json.dumps(
            {"transactions": [
                # covered call: sell to open, buy to CLOSE (qty>0 but
                # against a short — NOT a LEAPS entry)
                {"action": "BUYSELL", "date": "2025-02-01",
                 "time": "09:30:00", "symbol": self.SHORT_CALL,
                 "quantity": -2, "price": 1.5, "net_amount": 300.0,
                 "currency": "CAD"},
                {"action": "BUYSELL", "date": "2025-03-01",
                 "time": "09:30:00", "symbol": self.SHORT_CALL,
                 "quantity": 2, "price": 0.5, "net_amount": 100.0,
                 "currency": "CAD"},
                # true long LEAPS entry (control)
                {"action": "BUYSELL", "date": "2025-02-05",
                 "time": "09:30:00", "symbol": self.LONG_CALL,
                 "quantity": 1, "price": 4.0, "net_amount": 400.0,
                 "currency": "CAD"}]}))
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": "2025"}, "transactions": [
                {"symbol": self.SHORT_CALL, "date": "2025-03-01",
                 "qty": -2, "proceeds": 300.0, "cost": 100.0,
                 "gain": 200.0, "direction": "SHORT",
                 "currency": "CAD", "days_held": 28},
                {"symbol": self.LONG_CALL, "date": "2025-06-01",
                 "qty": 1, "proceeds": 500.0, "cost": 400.0,
                 "gain": 100.0, "currency": "CAD", "days_held": 116}],
             "inventory": []}))
        return root

    def test_short_call_close_not_in_leaps_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "leaps-sum", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        contracts = [row.get("contract") for row in doc["rows"]]
        self.assertIn(self.LONG_CALL, contracts)          # control
        self.assertNotIn(self.SHORT_CALL, contracts)      # was leaking


if __name__ == "__main__":
    unittest.main()
