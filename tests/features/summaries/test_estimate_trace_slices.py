"""Mutation pins for lib/tax_estimate.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round: a behaviour of `tjs estimate` that is
right but was never asserted. Expected figures are hand arithmetic from
the published charts, not re-derivations through the same code.

Formerly: test_fix_x_mut_tax_estimate
"""
import unittest

from taxjson.lib.tax_estimate import bracket_slices, stacked_slices


class TestTraceSlices(unittest.TestCase):
    """The --verbose trace's bracket slices (taxjson_run prints them).

    Formerly: test_fix_x_mut_tax_estimate.TestTraceSlices"""
    B = [(10000.0, .10), (20000.0, .20), (float("inf"), .30)]

    def test_income_on_a_threshold_has_no_empty_slice(self):
        # m243: `income <= lower` stops at a threshold — no zero-width
        # (20000, 20000) slice at the next rate.
        self.assertEqual(bracket_slices(20000.0, self.B),
                         [(0.0, 10000.0, .10, 1000.0),
                          (10000.0, 20000.0, .20, 2000.0)])

    def test_stacked_slices_start_at_zero_and_skip_empty_bands(self):
        # m244 (lower starts at 0), m245 (`hi > lo`: a stack ending on a
        # threshold adds no zero-width slice).
        self.assertEqual(stacked_slices(0.0, 100.0, self.B),
                         [(0.0, 100.0, .10, 10.0)])
        self.assertEqual(stacked_slices(5000.0, 5000.0, self.B),
                         [(5000.0, 10000.0, .10, 500.0)])


if __name__ == "__main__":
    unittest.main()
