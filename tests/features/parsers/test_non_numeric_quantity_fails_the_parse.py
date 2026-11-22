"""Regressions for FUZZ-2026-07 confirmed findings (minimal repros).

Formerly: test_fuzz_fixes"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO


class TestFuzzM_ValidationErrorsSurface(unittest.TestCase):
    """#M: merge2 --validate's hard ERRORs lived only in the raw .diag
    — the DIAGNOSTICS banner regex dropped the `validation:` header and
    the console said nothing (validation theater). Now: the header (and
    its indented TX lines) reaches the .sum banner AND the run prints a
    loud console pointer.

    Formerly: test_fuzz_fixes.TestFuzzM_ValidationErrorsSurface"""

    def test_bad_quantity_row_fails_the_parse(self):
        # RE-PREMISED (2026-09 parse hardening): 'NOTANUM' used to read
        # as quantity 0 and surface only as a validation banner in the
        # .sum while the run exited 0. A required numeric cell that is
        # not a number now FAILS the parse (and the run), naming the
        # line and the cell.
        qt = ("Transaction Date,Settlement Date,Action,Symbol,"
              "Description,Quantity,Price,Gross Amount,Commission,"
              "Net Amount,Currency,Account #,Activity Type,Account Type\n"
              "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
              "ISHARES,NOTANUM,10.00,1000.00,9.95,-1009.95,CAD,12345,"
              "Trades,Individual\n"
              "2025-02-15 09:30:00 AM,2025-02-16 12:00:00 AM,Buy,XEI.TO,"
              "ISHARES,100,10.00,1000.00,9.95,-1009.95,CAD,12345,"
              "Trades,Individual\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(qt)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("Quantity 'NOTANUM'", r.stderr)
        self.assertIn("questrade.csv line 2", r.stderr)


if __name__ == "__main__":
    unittest.main()
