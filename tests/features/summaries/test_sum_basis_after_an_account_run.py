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


class TestSumBasisMatchesBanner(unittest.TestCase):
    """Audit 2026-08 #8: `tjs sum`'s fast path served
    work/<acct>_report.json on freshness alone. After `run --account X`
    (which skips the wash pass and rewrites the report from PRE-wash
    gains) sum printed pre-wash aggregates under a "wash-adjusted"
    banner, disagreeing with list/form-export/carryover which read the
    stale wash file. The report now records its basis and sum requires
    a match.

    Formerly: test_audit_2026_08_fixes.TestSumBasisMatchesBanner"""

    def test_sum_after_account_rerun_stays_on_wash_basis(self):
        import json as _json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for acct in ("margin", "rrsp"):
                (root / "inputs" / acct).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            margin_csv = root / "inputs" / "margin" / "questrade.csv"
            margin_csv.write_text(
                _QT_HEADER +
                "2025-01-10 09:30:00 AM,2025-01-11 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
                "Trades,Individual\n"
                "2025-03-10 09:30:00 AM,2025-03-11 12:00:00 AM,Sell,XEI.TO,"
                "ISHARES COMP,-100,5.00,500.00,0.00,500.00,CAD,1,"
                "Trades,Individual\n")
            # RRSP rebuy inside the ±30-day window, held → the margin
            # loss is (partly) superficial.
            (root / "inputs" / "rrsp" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-03-20 09:30:00 AM,2025-03-21 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,50,5.00,250.00,0.00,-250.00,CAD,2,"
                "Trades,Individual\n")
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            wash_file = root / "work" / "margin_gains_wash.json"
            self.assertTrue(wash_file.exists())
            self.assertTrue(
                _json.loads(wash_file.read_text()).get("wash_sales"),
                "fixture must actually trigger a cross-account wash")
            s1 = _run_cli(root, "sum", "--json")
            doc1 = _json.loads(s1.stdout)
            self.assertEqual(doc1["basis"], "wash-adjusted")

            # New profitable round-trip OUTSIDE the wash window, then a
            # per-account rerun (wash pass skipped).
            margin_csv.write_text(
                margin_csv.read_text() +
                "2025-05-01 09:30:00 AM,2025-05-02 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,10,10.00,100.00,0.00,-100.00,CAD,1,"
                "Trades,Individual\n"
                "2025-05-15 09:30:00 AM,2025-05-16 12:00:00 AM,Sell,XEI.TO,"
                "ISHARES COMP,-10,20.00,200.00,0.00,200.00,CAD,1,"
                "Trades,Individual\n")
            r2 = _run_cli(root, "run", "--account", "margin", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            s2 = _run_cli(root, "sum", "--json")
        # sum served the freshly-rebuilt PRE-wash report under a
        # wash-adjusted banner after `run --account`; then the wash-adjusted
        # report the account run left behind. Now it refuses books a full
        # run did not finish, as the filing commands do (TJ-4356, TJ-4347).
        self.assertEqual(s2.returncode, 1, s2.stdout)
        self.assertEqual(s2.stdout, "")
        self.assertIn("no full `tjs run` recorded", s2.stderr)


if __name__ == '__main__':
    unittest.main()
