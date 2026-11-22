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


class TestCarrybackCapacityKeysOnTargetYear(unittest.TestCase):
    """Deep-audit 2026-08: a T1A carryback recorded via --claimed under
    its TARGET year still had that year's carryback capacity intact, so
    the ledger re-suggested the same carryback — following the report
    filed a duplicate T1A.

    Formerly: test_audit_2026_08_fixes.TestCarrybackCapacityKeysOnTargetYear"""

    def test_recorded_carryback_not_resuggested(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        nets = {
            2022: {"net": 5000.0, "dispositions": 1},
            2025: {"net": -8000.0, "dispositions": 1},
        }
        ledger = build_canada_ledger(nets, {2022: 5000.0})
        row_2025 = next(r for r in ledger["rows"] if r["year"] == 2025)
        cb_to_2022 = [c for c in row_2025["carryback_candidates"]
                      if c["year"] == 2022]
        self.assertEqual(
            cb_to_2022, [],
            "the 2022 gains are already sheltered by the recorded "
            "claim — suggesting another 5,000 carryback duplicates "
            "the filed T1A")
        self.assertAlmostEqual(ledger["final_carryforward"], 3000.0,
                               places=2)


if __name__ == '__main__':
    unittest.main()
