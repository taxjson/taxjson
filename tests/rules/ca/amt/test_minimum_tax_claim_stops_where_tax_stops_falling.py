"""Backlog fix round 7 (wave D) of 2026-10-29, group `edgeplan`: edge
cases, option-boundary, the estimate and the carryover.

TJ-1046  option-boundary's already-amended test left out the same-day
         buy-back in the lock's figure for a series and day, so a lock
         still reporting the assigned premium read as amended.
TJ-1067  under grant timing a write across Dec 31 is an option write in
         both project years, not an "option sale" in the new one.
TJ-1068  the expiry review names the share leg the engine's premium
         ledger used, also one in the opposite direction.
TJ-1113  the window review's units held on day 30 are the holders' the
         engine tests, not every account's.
TJ-1030  a minimum tax carryover is claimed only while the claim lowers
         tax (or the origin would lapse anyway).
TJ-1075  a damaged prior-year lock (its year field is not its file's)
         fails `tjs carryover` as damaged, not with --filed-lock advice.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*, LOSS*), account names (margin), small
amounts (or round test figures).

Formerly: test_fix_1029_r7d_edgeplan
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import amt_report as AR
from taxjson.lib import carryforward as CF
from taxjson.lib import filed_locks as FLK
from taxjson.lib import tax_estimate as TE


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, foreign_div=60000.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON", year=2025,
                actual_withheld=9000.0, amt_carryover={2022: 5000.0},
                amt_carryover_source="x")
    args.update(kw)
    return TE.estimate_canada(**args)


class TestCarryoverStopsWhereTaxStopsFalling(unittest.TestCase):
    """TJ-1030: s.120.2(1) allows the amount claimed, up to its limit.
    Keeps TJ-0719 (test_carryover_recovery_limit): the limit stays the before-FTC
    one, and an origin in its last usable year is claimed up to it.

    Formerly: test_fix_1029_r7d_edgeplan.TestCarryoverStopsWhereTaxStopsFalling"""

    @rule(R.CA_AMT_04, R.CA_AMT_07)
    def test_federal_only_claims_nothing_and_keeps_it_open(self):
        c = _ca(province=None)["amt"]["carryover"]
        self.assertEqual(c["limit"], 6518.8)
        self.assertEqual(c["recovered_federal"], 0.0)
        self.assertEqual(c["recovered"], 0.0)
        self.assertEqual(c["closing_by_year"], {"2022": 5000.0})
        self.assertEqual(c["closing"], 5000.0)
        self.assertEqual(c["claim_kept"], 5000.0)

    @rule(R.CA_AMT_04, R.CA_AMT_07)
    def test_ontario_claims_what_the_provincial_share_saves(self):
        full = _ca()["amt"]["carryover"]
        self.assertAlmostEqual(full["recovered"], 196.75, places=2)
        # Each dollar takes a dollar off the federal credit, which T2036
        # then gives back, on top of the province's 24.63% share: 196.75 /
        # 1.2463 is the claim where tax stops falling.
        self.assertEqual(full["recovered_federal"], 157.87)
        self.assertEqual(full["claim_kept"], 4842.13)
        self.assertEqual(full["closing_by_year"], {"2022": 4842.13})
        # A larger carryover saves no more; a smaller one saves less.
        small = _ca(amt_carryover={2022: 800.0})["amt"]["carryover"]
        self.assertEqual(small["recovered"], full["recovered"])
        self.assertEqual(small["recovered_federal"], 157.87)
        less = _ca(amt_carryover={2022: 150.0})["amt"]["carryover"]
        self.assertEqual(less["recovered_federal"], 150.0)
        self.assertLess(less["recovered"], full["recovered"] - 5.0)
        self.assertNotIn("claim_kept", less)

    @rule(R.CA_AMT_04)
    def test_a_lapsing_origin_is_still_claimed_up_to_the_limit(self):
        c = _ca(province=None, amt_carryover={2018: 10000.0})["amt"][
            "carryover"]
        self.assertEqual(c["recovered_by_year"], {"2018": 6518.8})
        self.assertEqual(c["lapsing_by_year"], {"2018": 3481.2})

    @rule(R.CA_AMT_04)
    def test_a_lapsing_origin_does_not_spend_a_later_one(self):
        c = _ca(province=None, amt_carryover={2018: 1000.0, 2022: 5000.0})[
            "amt"]["carryover"]
        self.assertEqual(c["recovered_by_year"], {"2018": 1000.0})
        self.assertEqual(c["closing_by_year"], {"2022": 5000.0})

    @rule(R.CA_AMT_04)
    def test_no_foreign_credit_claim_is_the_whole_limit(self):
        r = _ca(foreign_div=0.0, actual_withheld=0.0, other_income=100000.0,
                amt_carryover={2024: 900.5})
        c = r["amt"]["carryover"]
        self.assertEqual(c["recovered_federal"], 900.5)
        self.assertNotIn("claim_kept", c)

    @rule(R.CA_AMT_04)
    def test_said_in_the_notes_and_the_report(self):
        r = _ca(province=None)
        self.assertTrue(any("lowers no tax" in n for n in r["notes"]),
                        r["notes"])
        r = _ca()
        self.assertTrue(any("lowers no tax" in n for n in r["notes"]),
                        r["notes"])
        text = "\n".join(AR.render(AR.build(r, 2025, "CAD"), details=True))
        self.assertIn("lowers no tax", text)

    @rule(R.CA_AMT_07, R.CA_CARRY_03)
    def test_the_lock_keeps_the_carryover_open(self):
        r = _ca(province=None)
        blk = CF.record_block("canada", r, {"other_income_entered": True,
                                            "other_income_value": 0.0})
        mt = blk["minimum_tax"]
        self.assertEqual(mt["recovered_by_year"], {})
        self.assertEqual(mt["closing_by_year"], {"2022": 5000.0})
        self.assertEqual(mt["closing"], 5000.0)
        self.assertIsNone(FLK.block_problem(blk))


if __name__ == "__main__":
    unittest.main()
