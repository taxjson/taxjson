"""Re-audit-2 tests-tagging round (filing / estimate area): the estimate
callers' routing of each income kind, pinned through the code that
builds the estimator's inputs (every figure synthetic)."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _us(est, **kw):
    from taxjson.bin.taxjson_run import _tax_estimate_result
    cfg = {"settings": {"country": "usa", "year": 2025}}
    base = {"realized": 0.0, "st": 0.0, "lt": 0.0, "div_ca": 0.0,
            "div_foreign": 0.0, "pil": 0.0, "staking": 0.0}
    base.update(est)
    with contextlib.redirect_stderr(io.StringIO()):
        return _tax_estimate_result(cfg, base, other_income=60000.0,
                                    other_losses=0.0, province=None, **kw)


def _usa(**kw):
    from taxjson.lib.tax_estimate import estimate_usa
    args = dict(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                other_income=60000.0, other_losses=0.0, year=2025)
    args.update(kw)
    return estimate_usa(**args)["estimated_tax"]


class TestUsEstimateRouting(unittest.TestCase):
    """A2-0835, A2-1504 (US-RPT-07): payments in lieu and staking are
    ordinary income, gains with no term are short-term — as the caller
    builds the estimator's inputs, not only inside the estimator."""

    @rule("US-RPT-07")
    def test_payment_in_lieu_is_ordinary(self):
        got = _us({"pil": 10000.0})["estimated_tax"]
        self.assertEqual(got, _usa(pil=10000.0))
        self.assertNotEqual(got, _usa(qualified_div=10000.0))

    @rule("US-RPT-07")
    def test_staking_is_ordinary(self):
        got = _us({"staking": 10000.0})["estimated_tax"]
        self.assertEqual(got, _usa(pil=10000.0))
        self.assertNotEqual(got, _usa(qualified_div=10000.0))

    @rule("US-RPT-07")
    def test_gains_with_no_term_are_short_term(self):
        r = _us({"realized": 10000.0})
        self.assertEqual(r["st_input"], 10000.0)
        self.assertEqual(r["estimated_tax"], _usa(st=10000.0))
        self.assertGreater(r["estimated_tax"], _usa())


class TestCanadaEstimatePaymentInLieu(unittest.TestCase):
    """A2-0490 (CA-INC-03): a payment in lieu reaches the estimate as
    ordinary income, never as a foreign dividend with a 15% credit."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps({
            "summary": {"year": "2026"}, "transactions": [
                {"action": "DIVIDEND_IN_LIEU", "symbol": "KO.US",
                 "pil": 4000.0, "currency": "CAD"}]}))
        return root

    @rule("CA-INC-03", "CA-RPT-03")
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
