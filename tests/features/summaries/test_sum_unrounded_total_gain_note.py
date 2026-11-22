"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths
from _support.filing_run_record import record_full_run
from _support.filing_bin_reports import _gains, _g

REPO = _paths.REPO


if __name__ == "__main__":
    unittest.main()


class TestSumRoundingNote(unittest.TestCase):
    """R1-166: FOR THE RETURN (rows rounded to the cent) disagreed with
    the gains files' unrounded total by cents without a word.

    Formerly: test_fix_l_filing_b.TestSumRoundingNote"""

    def test_return_row_names_the_unrounded_total(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _gains(root / "work", [
                _g(symbol=f"S{i}.TO", proceeds=1001.004, cost=1000.0,
                   gain=1.004) for i in range(3)])
            record_full_run(root)
            env = {**os.environ, "TAXJSON_OFFLINE": "1",
                   "PYTHONPATH": str(REPO / "src")}
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "sum", "--details"], capture_output=True,
                text=True,
                env=env, timeout=300)
            j = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "sum", "--json"], capture_output=True,
                text=True, env=env, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("unrounded total gain is 3.01", r.stdout)
        doc = json.loads(j.stdout)
        self.assertEqual(doc["filing"]["totals"]["gain"], 3.0)
        self.assertEqual(doc["filing"]["engine_gain_unrounded"], 3.01)
