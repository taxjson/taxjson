"""Regression tests for the 2026-07 audit tier-1 fixes:

1. run_to_file: a failed stage must not leave a truncated output file that the
   mtime cache then treats as fresh (previous good output must survive).
2. `tjs fees` counts standalone FEE-action rows (amount in net_amount).
3. IB dividend/withholding reversal rows keep their sign and net out instead
   of being abs()'d into extra income/tax.
4. Wash radar: 30-day windows are DST-immune (UTC-noon epochs) — day 30
   across the November fall-back is still inside the window.
5. Wash radar: the VIOLATION (substituted-property) test uses the CRA window
   around the LOSS SALE, not the last 30 days from today; fully-exited losses
   route to COOLING.
6. Engines apply one corporate SPLIT once even when merged multi-account
   input carries one SPLIT row per account.

Formerly: test_audit_tier1_fixes
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths

REPO_ROOT = _paths.REPO


def _runsub(root, sub, *args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         sub, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=e)


class TestFeesIncludesFeeRows(unittest.TestCase):
    """Formerly: test_audit_tier1_fixes.TestFeesIncludesFeeRows"""
    def test_fee_action_rows_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_raw.json").write_text(json.dumps({
                "transactions": [
                    {"action": "BUYSELL", "date": "2026-03-01",
                     "time": "09:30:00", "symbol": "AAA.TO", "quantity": 100,
                     "price": 10.0, "net_amount": 1009.99, "fee": 9.99,
                     "currency": "CAD"},
                    # Standalone fee (e.g. IB market-data): amount lives in
                    # net_amount, POSITIVE = charged (the parsers'
                    # convention; R1-124, R1-54 — this test used to pin the
                    # inverted sign).
                    {"action": "FEE", "date": "2026-03-05",
                     "time": "09:30:00", "symbol": "CASH", "quantity": 0.0,
                     "net_amount": 25.00, "currency": "CAD"}]}))
            r = _runsub(root, "fees")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FEE", r.stdout)
        self.assertIn("TOTAL FEES: 34.99 CAD", r.stdout)


if __name__ == "__main__":
    unittest.main()
