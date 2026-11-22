"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import unittest
from taxjson.rules import ids as R


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# t1135
# ---------------------------------------------------------------------------

from unittest import mock  # noqa: E402

from tax_rules import rule, rule  # noqa: E402


class TestInstalmentBoundaries(unittest.TestCase):
    """R1-314: the $3,000 and $25 instalment thresholds at the tie.

    Formerly: test_fix_l_filing_b.TestInstalmentBoundaries"""

    @rule(R.CA_RPT_11)
    def test_net_tax_threshold_is_strict(self):
        from datetime import date
        from taxjson.bin.taxjson_instalments import build
        kw = dict(year=2026, basis="current_year", payments=[],
                  annual_rate=0.07, as_of=date(2026, 3, 1))
        self.assertFalse(build(current_net_tax=3000.00, prior_net_tax=5000.0,
                               **kw)["required_at_all"])
        self.assertTrue(build(current_net_tax=3000.01, prior_net_tax=5000.0,
                              **kw)["required_at_all"])
        both = dict(kw, current_net_tax=9000.0)
        self.assertEqual(build(prior_net_tax=3000.00,
                               second_prior_net_tax=3000.00,
                               **both)["prior_year_test"], "not_met")
        self.assertEqual(build(prior_net_tax=3000.01,
                               second_prior_net_tax=3000.00,
                               **both)["prior_year_test"], "met")
        self.assertEqual(build(prior_net_tax=1000.00,
                               second_prior_net_tax=3000.01,
                               **both)["prior_year_test"], "met")

    @rule(R.CA_RPT_11)
    def test_interest_charged_only_above_25(self):
        from datetime import date
        from taxjson.bin import taxjson_instalments as I
        for charge, net in ((25.00, 0.0), (25.01, 25.01)):
            with self.subTest(charge=charge), \
                    mock.patch.object(I, "_accrue",
                                      return_value=(charge, 0.0)):
                got = I.interest_and_penalty(required=[], payments=[],
                                             annual_rate=0.07,
                                             end=date(2027, 4, 30))
                self.assertEqual(got["net_interest"], net)
