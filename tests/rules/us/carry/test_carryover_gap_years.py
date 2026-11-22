"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import unittest


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestUsaCarryoverGapYears(unittest.TestCase):
    """Audit 2026-08 #5: `build_usa_ledger` iterated only disposition
    years, so in a no-trade gap year neither the assumed $3,000
    ordinary-income offset nor a claimed_losses.txt entry was applied —
    the final carryover was overstated by up to $3k per gap year
    (the Canada ledger got this fix in REVIEW #23; USA did not).

    Formerly: test_audit_2026_08_fixes.TestUsaCarryoverGapYears"""

    def _nets(self):
        return {
            2020: {'net': -10000.0, 'st': -10000.0, 'lt': 0.0,
                   'dispositions': 1},
            2024: {'net': 1000.0, 'st': 1000.0, 'lt': 0.0,
                   'dispositions': 1},
        }

    def test_assumed_offset_applies_in_gap_years(self):
        from taxjson.bin.taxjson_carryover import build_usa_ledger
        ledger = build_usa_ledger(self._nets(), {})
        by_year = {r['year']: r for r in ledger['rows']}
        # 2020 absorbs 3k → 7k carry; 2021-2023 returns absorb 3k, 3k,
        # then the final 1k; 2024's gain sees no remaining carryover.
        self.assertAlmostEqual(by_year[2021]['ordinary_income_offset'],
                               3000.0, places=2)
        self.assertAlmostEqual(by_year[2022]['ordinary_income_offset'],
                               3000.0, places=2)
        self.assertAlmostEqual(by_year[2023]['ordinary_income_offset'],
                               1000.0, places=2)
        self.assertAlmostEqual(
            ledger['final_carryforward'], 0.0, places=2,
            msg="gap years 2021-2023 were skipped: the buggy final "
                "carryover was $3,000")

    def test_claimed_zero_in_gap_year_preserves_carry(self):
        from taxjson.bin.taxjson_carryover import build_usa_ledger
        ledger = build_usa_ledger(
            self._nets(), {2021: 0.0, 2022: 0.0, 2023: 0.0})
        by_year = {r['year']: r for r in ledger['rows']}
        # No returns filed 2021-2023: the full 7k carry survives into
        # 2024, nets against the 1k gain (→ -6k), 2024 assumes 3k.
        self.assertAlmostEqual(by_year[2023]['st_carryover'], 7000.0,
                               places=2)
        self.assertAlmostEqual(ledger['final_carryforward'], 3000.0,
                               places=2)


if __name__ == '__main__':
    unittest.main()
