"""Re-audit-2 fixes, filing-reports list (helper D): the run pipeline
(crypto-sends stage, raw pass phantoms, blended .sum diagnostics, run
state banner), `sum`/`estimate` account handling and flag guards,
instalment dates, and the estimate's Canadian-issuer test and wording.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_d
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths
from _support.filing_run_record import record_full_run

REPO_ROOT = _paths.REPO


def _taxjson(root, *a, env=None):
    e = {**os.environ, "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
         "PYTHONPATH": str(REPO_ROOT / "src")}
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=e)


class TestInstalmentPrepayment(unittest.TestCase):
    """A2-0648: a December prepayment of next year's instalments is
    accepted when its row names the tax year (credited from Jan 1, as
    the interest model already does); an undesignated prior-year date
    still refuses (the year-rollover trap).

    Formerly: test_fix_a2_filrep_d.TestInstalmentPrepayment"""

    def _run(self, paid, year=2025):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = {year}\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[instalments]\nbasis = "prior_year"\n'
            'prior_year_net_tax = 20000\nprescribed_rate = 0.08\n'
            f'paid = [{paid}]\n')
        (root / "work").mkdir()
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": year}, "transactions": [],
             "inventory": [], "wash_sales": []}))
        record_full_run(root)
        return _taxjson(root, "instalments", "--json")

    @rule(R.CA_RPT_11)
    def test_designated_december_prepayment_counts_from_jan1(self):
        pre = self._run('{ date = "2024-12-20", amount = 5000, '
                        'tax_year = 2025 }')
        self.assertEqual(pre.returncode, 0, pre.stderr)
        jan = self._run('{ date = "2025-01-01", amount = 5000 }')
        self.assertEqual(jan.returncode, 0, jan.stderr)
        a, b = json.loads(pre.stdout), json.loads(jan.stdout)
        self.assertEqual(json.dumps(a.get("interest"), sort_keys=True),
                         json.dumps(b.get("interest"), sort_keys=True))
        self.assertIn("2024-12-20", pre.stdout)

    @rule(R.CA_RPT_11)
    def test_undesignated_prior_year_payment_still_refuses(self):
        r = self._run('{ date = "2024-12-20", amount = 5000 }')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outside tax year 2025", r.stderr)
        self.assertIn("tax_year = 2025", r.stderr)
        # A designation for another year is refused too.
        r = self._run('{ date = "2024-12-20", amount = 5000, '
                      'tax_year = 2024 }')
        self.assertNotEqual(r.returncode, 0)
        # Only a payment before Jan 1 of the year may be designated.
        r = self._run('{ date = "2026-06-01", amount = 5000, '
                      'tax_year = 2025 }')
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
