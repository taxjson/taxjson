"""Re-audit-2 conformance gaps: a mutant of each rule below survived the
whole suite in the re-audit's conformance pass. Each test here fails on
that mutant and passes on the real code. Synthetic data only.

Formerly: test_fix_a2_spec_gaps
"""
import contextlib
import io
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.project.settings import Settings


class TestUsEstimateInputs(unittest.TestCase):
    """US-RPT-07 (mutants USRPT07-pil, USRPT07-unterm): `tjs
    estimate` taxes a payment in lieu as ordinary income (never
    qualified) and folds gains with no term into short-term.

    Formerly: test_fix_a2_spec_gaps.TestUsEstimateInputs"""

    CFG = {"settings": Settings.from_table({"country": "usa", "year": 2025})}

    def _est(self, **est):
        from taxjson.cli.summaries import _tax_estimate_result
        base = {"realized": 0.0, "st": 0.0, "lt": 0.0, "div_ca": 0.0,
                "div_foreign": 0.0, "pil": 0.0}
        base.update(est)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = _tax_estimate_result(self.CFG, base, other_income=80000.0,
                                     other_losses=0.0, province=None)
        return r, err.getvalue()

    @rule(R.US_RPT_07)
    def test_payment_in_lieu_is_ordinary(self):
        # 1,000 PIL on 80,000 of other income: 22% band = 220 (as
        # qualified it would be taxed at 15% = 150).
        r, _ = self._est(pil=1000.0)
        self.assertEqual(r["estimated_tax"], 220.0)

    @rule(R.US_RPT_07)
    def test_gain_with_no_term_is_short_term(self):
        r, err = self._est(realized=1000.0)
        self.assertEqual(r["st_input"], 1000.0)
        self.assertEqual(r["estimated_tax"], 220.0)
        self.assertIn("SHORT-TERM", err)


if __name__ == "__main__":
    unittest.main()
