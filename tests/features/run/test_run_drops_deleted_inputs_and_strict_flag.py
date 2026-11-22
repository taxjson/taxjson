"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

It was features/run/test_fast_run_deletions_and_strict_flag until
`--fast` was retired: its `run --fast` cases are a plain run's now
(every run rebuilds everything), and its no-change `--fast` and
`--strict --fast` cache-hit cases went with the flag (no run reuses a
cached stage).

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

_CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
              "Price Currency,Price at Transaction,Subtotal,"
              "Total (inclusive of Fees and/or Spread),"
              "Fees and/or Spread,Notes\n")


class TestFastSeesTtAndGroupDeletions(unittest.TestCase):
    """Audit 2026-08 #6: the FUZZ #J sources manifest listed only CSVs,
    so under `run --fast` (retired since) (a) deleting a .tt file left
    its transactions in the cached books, and (b) on the crypto path —
    whose merge had no manifest dep — deleting the only CSV group did
    the same when a .tt kept the account alive.

    Formerly: test_audit_2026_08_fixes.TestFastSeesTtAndGroupDeletions"""

    def test_deleted_tt_drops_from_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
                "Trades,Individual\n")
            tt = root / "inputs" / "margin" / "start.tt"
            tt.write_text(
                "BUYSELL 2025-03-01 09:30:00 TTONLY.TO 10 CAD 5.0 -50.0\n")
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            base1 = (root / "work" / "margin_base.json").read_text()
            self.assertIn("TTONLY.TO", base1)
            tt.unlink()                          # delete the .tt
            r2 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            base2 = (root / "work" / "margin_base.json").read_text()
        self.assertNotIn(
            "TTONLY.TO", base2,
            msg=".tt files were missing from the sources manifest — the "
                "deleted opening balances survived the run")

    def test_deleted_crypto_group_drops_from_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "wallet").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.wallet]\ntype = "taxable"\ncrypto = true\n')
            cb = root / "inputs" / "wallet" / "cb_wallet.csv"
            cb.write_text(
                _CB_HEADER +
                "CB-T-0001,2024-01-18 16:24:11 UTC,Buy,BTC,0.05000000,CAD,"
                "43000.00,2150.00,2167.20,17.20,Bought BTC\n")
            # The .tt keeps the account alive once the CSV group is gone.
            (root / "inputs" / "wallet" / "start.tt").write_text(
                "BUYSELL 2024-02-01 09:30:00 ETH-CAD 1.0 CAD 3000.0 "
                "-3000.0\n")
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            base1 = (root / "work" / "wallet_base.json").read_text()
            self.assertIn("BTC", base1)
            cb.unlink()                          # delete the whole group
            r2 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            base2 = (root / "work" / "wallet_base.json").read_text()
        self.assertNotIn(
            "BTC", base2,
            msg="the crypto merge had no sources-manifest dep — the "
                "deleted broker group's trades survived the run")
        self.assertIn("ETH-CAD", base2)


class TestStrictRunFlag(unittest.TestCase):
    """KNOWN_ISSUES "per-account validation is non-fatal": a CI/cron
    `tjs run` could publish a wrong .sum at exit 0. `--strict`
    promotes per-account validation ERRORs to fatal.

    Formerly: test_audit_2026_08_fixes.TestStrictRunFlag"""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            _QT_HEADER +
            "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
            "ISHARES COMP,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
            "Trades,Individual\n")
        # Zero-quantity BUYSELL: a validation ERROR the gains engine
        # tolerates (it skips sub-epsilon rows) — so the default run
        # publishes reports at exit 0 with only a DIAGNOSTICS banner.
        (root / "inputs" / "margin" / "start.tt").write_text(
            "BUYSELL 2025-03-01 09:30:00 ZERO.TO 0 CAD 5.0 0.0\n")
        return root

    def test_strict_promotes_validation_errors_to_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r_default = _run_cli(root, "run", "--no-input")
            r_strict = _run_cli(root, "run", "--strict", "--no-input")
        self.assertEqual(r_default.returncode, 0,
                         "default stays warn-only: " + r_default.stderr)
        self.assertNotEqual(r_strict.returncode, 0)
        self.assertIn("--strict", r_strict.stderr)
        self.assertIn("validation ERROR", r_strict.stderr)


if __name__ == '__main__':
    unittest.main()
