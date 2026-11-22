"""Low-round engine pins (fixl/engine-tests): end-to-end assertions on
engine output fields and boundaries that a mutant could change with
every other test still passing. Each class names the audit finding.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_engine_pins
"""
import unittest

from taxjson.engine.us.terms import held_more_than_one_year
from tax_rules import rule
from taxjson.rules import ids as R


class TestUsDecemberMonthEndHoldingPeriod(unittest.TestCase):
    """R1-315: the Dec-31 acquisition branch of held_more_than_one_year
    (Pub 550 / Rev. Rul. 66-7) had no test.

    Formerly: test_fix_l_engine_pins.TestUsDecemberMonthEndHoldingPeriod"""

    @rule(R.US_HOLD_01)
    def test_dec31_acquisition(self):
        h = held_more_than_one_year
        self.assertFalse(h('2023-12-31', '2024-06-01'))
        self.assertFalse(h('2023-12-31', '2024-12-31'))
        self.assertTrue(h('2023-12-31', '2025-01-01'))
        self.assertTrue(h('2023-12-31', '2025-01-31'))
        self.assertTrue(h('2024-11-30', '2025-12-01'))
        self.assertFalse(h('2024-11-30', '2025-11-30'))


if __name__ == '__main__':
    unittest.main()
