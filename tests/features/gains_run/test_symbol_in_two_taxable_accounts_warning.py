"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO_ROOT = _paths.REPO


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


class TestCrossTaxableOverlapWarning(unittest.TestCase):
    """KNOWN_ISSUES multi-account gap: the per-account fan-out
    silently computed wrong Canada ACB (and skipped US cross-taxable
    wash checks) when a symbol traded in two taxable accounts. The run
    now warns loudly, naming the symbols and the workaround.

    Formerly: test_audit_2026_08_fixes.TestCrossTaxableOverlapWarning"""

    def _project(self, tmp, second_symbol):
        root = Path(tmp)
        for acct in ("rbc", "ib2"):
            (root / "inputs" / acct).mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.rbc]\ntype = "taxable"\n'
            '[accounts.ib2]\ntype = "taxable"\n')
        row = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,{sym},"
               "DESC,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,"
               "Individual\n")
        (root / "inputs" / "rbc" / "questrade.csv").write_text(
            _QT_HEADER + row.format(sym="XEI.TO"))
        (root / "inputs" / "ib2" / "questrade.csv").write_text(
            _QT_HEADER + row.format(sym=second_symbol))
        return root

    def test_shared_symbol_across_taxable_accounts_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, second_symbol="XEI.TO")
            r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Since the blended pass landed, the message is an informational
        # note (the canonical wash artifacts carry blended figures) —
        # not a wrong-numbers warning.
        self.assertIn("more than one TAXABLE account", r.stderr)
        self.assertIn("XEI.TO", r.stderr)
        self.assertIn("blended", r.stderr)

    def test_disjoint_symbols_stay_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, second_symbol="ZAG.TO")
            r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("more than one TAXABLE account", r.stderr)


if __name__ == '__main__':
    unittest.main()
