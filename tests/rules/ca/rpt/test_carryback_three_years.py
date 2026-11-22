"""Re-audit-2 conformance gaps: a mutant of each rule below survived the
whole suite in the re-audit's conformance pass. Each test here fails on
that mutant and passes on the real code. Synthetic data only.

Formerly: test_fix_a2_spec_gaps
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestCarrybackReachesThreeYears(unittest.TestCase):
    """CA-RPT-10, mutant RPT10-carryback (carry back 2 years).

    Formerly: test_fix_a2_spec_gaps.TestCarrybackReachesThreeYears"""

    @rule(R.CA_RPT_10)
    def test_third_prior_year_is_a_candidate(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        nets = {2020: {"net": 9000.0, "st": 0, "lt": 0, "dispositions": 1},
                2023: {"net": -1000.0, "st": 0, "lt": 0,
                       "dispositions": 1}}
        rows = {r["year"]: r for r in build_canada_ledger(nets, {})["rows"]}
        self.assertEqual(rows[2023]["carryback_candidates"],
                         [{"year": 2020, "amount": 1000.0}])


if __name__ == "__main__":
    unittest.main()
