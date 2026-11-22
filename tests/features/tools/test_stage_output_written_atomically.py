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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestRunToFileAtomic(unittest.TestCase):
    """Formerly: test_audit_tier1_fixes.TestRunToFileAtomic"""
    def test_failed_stage_preserves_previous_output(self):
        from taxjson.pipeline.tools import run_to_file
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stage.json"
            out.write_text('{"good": true}')
            # A command that writes a partial payload then fails.
            cmd = [sys.executable, "-c",
                   "import sys; sys.stdout.write('{\"transactions\": ['); sys.exit(1)"]
            with self.assertRaises(subprocess.CalledProcessError):
                run_to_file(cmd, out, capture_diag=False)
            # The old good output survives; no .part litter.
            self.assertEqual(out.read_text(), '{"good": true}')
            self.assertFalse((Path(tmp) / "stage.json.part").exists())

    def test_failed_stage_leaves_no_first_time_output(self):
        from taxjson.pipeline.tools import run_to_file
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stage.json"
            cmd = [sys.executable, "-c",
                   "import sys; sys.stdout.write('partial'); sys.exit(1)"]
            with self.assertRaises(subprocess.CalledProcessError):
                run_to_file(cmd, out, capture_diag=False)
            self.assertFalse(out.exists(),
                             "a failed first run must not leave a fresh-mtime "
                             "partial file for needs_rebuild to trust")

    def test_success_writes_output(self):
        from taxjson.pipeline.tools import run_to_file
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stage.json"
            run_to_file([sys.executable, "-c", "print('{}')"], out,
                        capture_diag=False)
            self.assertEqual(out.read_text().strip(), "{}")


if __name__ == "__main__":
    unittest.main()
