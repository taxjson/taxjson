"""Re-audit-2 tests-tagging round (filing / estimate area): the estimate
callers' routing of each income kind, pinned through the code that
builds the estimator's inputs (every figure synthetic).

Formerly: test_fix_a2_tagging_b"""
import contextlib
import io
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
from taxjson.project.settings import Settings


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _us(est, **kw):
    from taxjson.cli.summaries import _tax_estimate_result
    cfg = {"settings": Settings.from_table({"country": "usa", "year": 2025})}
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
    builds the estimator's inputs, not only inside the estimator.

    Formerly: test_fix_a2_tagging_b.TestUsEstimateRouting"""

    @rule(R.US_RPT_07)
    def test_payment_in_lieu_is_ordinary(self):
        got = _us({"pil": 10000.0})["estimated_tax"]
        self.assertEqual(got, _usa(pil=10000.0))
        self.assertNotEqual(got, _usa(qualified_div=10000.0))

    @rule(R.US_RPT_07)
    def test_staking_is_ordinary(self):
        got = _us({"staking": 10000.0})["estimated_tax"]
        self.assertEqual(got, _usa(pil=10000.0))
        self.assertNotEqual(got, _usa(qualified_div=10000.0))

    @rule(R.US_RPT_07)
    def test_gains_with_no_term_are_short_term(self):
        r = _us({"realized": 10000.0})
        self.assertEqual(r["st_input"], 10000.0)
        self.assertEqual(r["estimated_tax"], _usa(st=10000.0))
        self.assertGreater(r["estimated_tax"], _usa())


if __name__ == "__main__":
    unittest.main()
