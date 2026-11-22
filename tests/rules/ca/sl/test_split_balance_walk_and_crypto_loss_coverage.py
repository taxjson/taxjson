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

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
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


class TestMultiAccountSplitBalanceWalk(unittest.TestCase):
    """Audit 2026-08 #2: `_dedupe_corporate_splits` keeps one SPLIT row
    per corporate event (survivor account arbitrary), but the Canada
    engine's per-(account, alias) running-balance walk scaled only the
    survivor's account — every other account holding the symbol kept a
    pre-split balance, so a post-split sale there was misread as
    part short-opening and the rebuy as part short-covering, shrinking
    disallowed_qty.

    Formerly: test_audit_2026_08_fixes.TestMultiAccountSplitBalanceWalk"""

    def _fixture(self, two_accounts: bool):
        """Account Y: buy 100 @ 40, 4:1 split, sell 400 at a $600 loss,
        rebuy 400 within 30 days (held at window end → fully
        superficial). When `two_accounts`, account X also holds the
        symbol through the split and X's SPLIT row is the dedupe
        survivor (listed first)."""
        txs = []
        if two_accounts:
            txs += [
                TaxTransaction(action='BUYSELL', date='2025-01-05',
                               symbol='AAA.TO', quantity=100.0, price=40.0,
                               net_amount=4000.0, currency='CAD',
                               account='X'),
                # X's copy of the split row survives dedupe.
                TaxTransaction(action='SPLIT', date='2025-02-01',
                               symbol='AAA.TO', quantity=4.0,
                               currency='CAD', account='X'),
            ]
        txs += [
            TaxTransaction(action='BUYSELL', date='2025-01-06',
                           symbol='AAA.TO', quantity=100.0, price=40.0,
                           net_amount=4000.0, currency='CAD', account='Y'),
            # Y's copy — deduped away in the two-account fixture.
            TaxTransaction(action='SPLIT', date='2025-02-01',
                           symbol='AAA.TO', quantity=4.0, currency='CAD',
                           account='Y'),
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAA.TO', quantity=-400.0, price=8.5,
                           net_amount=3400.0, currency='CAD', account='Y'),
            TaxTransaction(action='BUYSELL', date='2025-02-20',
                           symbol='AAA.TO', quantity=400.0, price=8.5,
                           net_amount=3400.0, currency='CAD', account='Y'),
        ]
        return txs

    def test_disallowance_matches_single_account_control(self):
        control = CanadaTaxRules().compute_gains(
            self._fixture(two_accounts=False))
        multi = CanadaTaxRules().compute_gains(
            self._fixture(two_accounts=True))
        self.assertAlmostEqual(
            control['summary']['total_disallowed'], 600.0, places=2)
        self.assertAlmostEqual(
            multi['summary']['total_disallowed'], 600.0, places=2,
            msg="account Y's balance stayed in pre-split units because the "
                "dedupe-surviving SPLIT row was applied only in account X")


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

_CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
              "Price Currency,Price at Transaction,Subtotal,"
              "Total (inclusive of Fees and/or Spread),"
              "Fees and/or Spread,Notes\n")


class TestCanadaCryptoWashCoverage(unittest.TestCase):
    """Audit 2026-08 #14: the engine wash-checks Canadian crypto
    (s.54 covers any identical property), but the cross-account wash
    pass and wash-radar skipped crypto accounts on the US-only §1091
    rationale — losses were denied with zero advance warning from the
    advisory tooling.

    Formerly: test_audit_2026_08_fixes.TestCanadaCryptoWashCoverage"""

    def _project(self, tmp, country, base_currency, price_currency):
        root = Path(tmp)
        (root / "inputs" / "wallet").mkdir(parents=True)
        (root / "inputs" / "rrsp").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\ncountry = "{country}"\n'
            f'base_currency = "{base_currency}"\nsource_currencies = []\n'
            f'[accounts.wallet]\ntype = "taxable"\ncrypto = true\n'
            f'[accounts.rrsp]\ntype = "sheltered"\n')
        (root / "inputs" / "wallet" / "cb_wallet.csv").write_text(
            _CB_HEADER +
            f"CB-1,2024-01-18 16:24:11 UTC,Buy,BTC,0.05,{price_currency},"
            f"43000.00,2150.00,2150.00,0.00,Bought BTC\n"
            f"CB-2,2024-03-18 16:24:11 UTC,Sell,BTC,0.05,{price_currency},"
            f"40000.00,2000.00,2000.00,0.00,Sold BTC\n")
        # Priced in the base currency: with `source_currencies = []` the
        # pipeline's rates file is empty, and a currency absent from it
        # is now a fatal merge2 error (stage-tools audit) rather than a
        # silent 1.35 conversion.
        (root / "inputs" / "rrsp" / "questrade.csv").write_text(
            _QT_HEADER +
            "2024-02-03 09:30:00 AM,2024-02-04 12:00:00 AM,Buy,ZAG.TO,"
            f"BMO AGG BOND,50,14.00,700.00,0.00,-700.00,{base_currency},2,"
            "Trades,Individual\n")
        return root

    def test_canada_crypto_gets_wash_pass_and_radar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, "canada", "CAD", "CAD")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(
                (root / "work" / "wallet_gains_wash.json").exists(),
                "Canada crypto must get the cross-account wash pass")
            self.assertTrue(
                (root / "reports" / "wash_radar_wallet.rpt").exists(),
                "Canada crypto must get a wash radar — the engine "
                "denies superficial crypto losses, so the advisory "
                "tooling can't stay silent")

    def test_usa_crypto_still_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, "usa", "USD", "USD")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(
                (root / "work" / "wallet_gains_wash.json").exists())
            self.assertFalse(
                (root / "reports" / "wash_radar_wallet.rpt").exists())


if __name__ == '__main__':
    unittest.main()
