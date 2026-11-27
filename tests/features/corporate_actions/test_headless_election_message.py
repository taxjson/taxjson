"""2026-10 workflow audit: the fixes the audit's confirmed findings asked
for (CHANGELOG "Fixed"): interest paid shown apart, a negative futures
price in a generic CSV, the audit over a pending election, the
checklist's hints, init's timezone, the single-folder year hints,
sanity with no holdings file, the headless election message, no empty
elections.json, compare's exit codes (tests/test_compare.py).

Formerly: test_workflow_audit_fixes"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _support.ib_corp import _SPIN, _ca, _ib_files
import _paths

REPO_ROOT = _paths.REPO


class TestHeadlessElectionMessage(unittest.TestCase):
    """Formerly: test_workflow_audit_fixes.TestHeadlessElectionMessage"""
    def _run(self, tmp, *flags):
        a, b = _ib_files(tmp, [_ca(_SPIN, 20, 400)], [])
        pend = Path(tmp) / "pending.json"
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
             "--country", "canada", "--brokerage", "ib",
             "--elections", str(Path(tmp) / "elections.json"),
             *flags, "--pending-json", str(pend), str(a), str(b)],
            cwd=REPO_ROOT, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=120)

    def test_says_which_kept_it_from_asking(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("need an election — not asked: --no-input", r.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("need an election — not asked: no terminal to "
                          "answer on", r.stderr)


if __name__ == "__main__":
    unittest.main()
