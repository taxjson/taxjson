"""Re-audit-2 tests-tagging round (filing / estimate area): the estimate
callers' routing of each income kind, pinned through the code that
builds the estimator's inputs (every figure synthetic).

Formerly: test_fix_a2_tagging_b"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
import _paths
from _support.filing_run_record import record_full_run


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


class TestCanadaEstimatePaymentInLieu(unittest.TestCase):
    """A2-0490 (CA-INC-03): a payment in lieu reaches the estimate as
    ordinary income, never as a foreign dividend with a 15% credit.

    Formerly: test_fix_a2_tagging_b.TestCanadaEstimatePaymentInLieu"""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps({
            "summary": {"year": "2026"}, "transactions": [
                {"action": "DIVIDEND_IN_LIEU", "date": "2026-03-02",
                 "symbol": "KO.US",
                 "pil": 4000.0, "currency": "CAD"}]}))
        record_full_run(root)
        return root

    @rule(R.CA_INC_03, R.CA_RPT_03)
    def test_pil_is_ordinary_income_in_the_estimate(self):
        from taxjson.lib.tax_estimate import estimate_canada
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(self._project(tmp)), "sum", "--json",
                 "--other-income", "80000"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)["estimate"]["estimated_tax"]
        common = dict(realized=0.0, eligible_div=0.0, year=2026,
                      other_income=80000.0, other_losses=0.0,
                      province="ON")
        want = estimate_canada(foreign_div=0.0, pil=4000.0, **common)
        self.assertEqual(got, want["estimated_tax"])
        wrong = estimate_canada(foreign_div=4000.0, pil=0.0, **common)
        self.assertNotEqual(got, wrong["estimated_tax"])



if __name__ == "__main__":
    unittest.main()
