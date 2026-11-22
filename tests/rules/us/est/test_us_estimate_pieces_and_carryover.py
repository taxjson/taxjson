"""Mutation pins for lib/tax_estimate.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round: a behaviour of `tjs estimate` that is
right but was never asserted. Expected figures are hand arithmetic from
the published charts, not re-derivations through the same code.

Formerly: test_fix_x_mut_tax_estimate
"""
import unittest

from taxjson.lib.tax_estimate import estimate_canada, estimate_usa
from tax_rules import rule
from taxjson.rules import ids as R


def _ca(**kw):
    base = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON",
                year=2025)
    base.update(kw)
    return estimate_canada(**base)


class TestUsaInvestmentIncome(unittest.TestCase):
    """Formerly: test_fix_x_mut_tax_estimate.TestUsaInvestmentIncome"""
    @rule(R.US_RPT_04)
    def test_investment_income_sums_every_component(self):
        # m205: st + lt + qualified + pil, each counted once.
        r = estimate_usa(st=1000.0, lt=2000.0, qualified_div=300.0,
                         pil=40.0, other_income=50000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["investment_income"], 3340.0)


class TestUsaPieces(unittest.TestCase):
    """Formerly: test_fix_x_mut_tax_estimate.TestUsaPieces"""
    @rule(R.US_RPT_04)
    def test_income_under_the_standard_deduction_is_untaxed(self):
        # m301: ordinary taxable income floors at 0, not 1.
        r = estimate_usa(st=0.0, lt=5000.0, qualified_div=0.0, pil=0.0,
                         other_income=10000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["tax_base"]["total"], 0.0)
        self.assertEqual(r["trace_base"]["ord_taxable"], 0.0)

    @rule(R.US_RPT_04)
    def test_unused_standard_deduction_reduces_preferential_income(self):
        # m306: 20,000 LT gain, no other income: 15,750 (2025) of
        # deduction is left for it, 4,250 is taxable.
        r = estimate_usa(st=0.0, lt=20000.0, qualified_div=0.0, pil=0.0,
                         other_income=0.0, other_losses=0.0, year=2025)
        self.assertEqual(r["trace_with"]["pref_taxable"], 4250.0)

    @rule(R.US_RPT_07)
    def test_payment_in_lieu_is_ordinary_income(self):
        # m318: 1,000 PIL on 80,000 of wages (taxable 64,250 -> 65,250,
        # all in the 22% band) = 220.
        r = estimate_usa(st=0.0, lt=0.0, qualified_div=0.0, pil=1000.0,
                         other_income=80000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["estimated_tax"], 220.0)

    @rule(R.US_RPT_04)
    def test_niit_base_counts_every_investment_component(self):
        # m321: MAGI far over 200,000 -> NIIT on all 3,340 = 126.92.
        r = estimate_usa(st=1000.0, lt=2000.0, qualified_div=300.0,
                         pil=40.0, other_income=300000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["niit_base"], 3340.0)
        self.assertEqual(r["niit"], 126.92)


class TestRoundTwoPins(unittest.TestCase):
    """Formerly: test_fix_x_mut_tax_estimate.TestRoundTwoPins"""
    @rule(R.CA_RPT_03)
    def test_amt_headroom_is_regular_minus_minimum(self):
        # m532 / m533: headroom = regular federal tax - minimum tax,
        # floored at 0 (0.00 when the AMT binds).
        r = _ca(year=2025, other_income=200000.0, realized=1000.0)
        a = r["amt"]
        self.assertGreater(a["minimum_fed"], 0.0)
        self.assertAlmostEqual(a["headroom"],
                               a["regular_fed"] - a["minimum_fed"],
                               delta=0.011)
        self.assertEqual(_ca(realized=1000000.0)["amt"]["headroom"], 0.0)

    @rule(R.CA_RPT_03)
    def test_full_bpa_prints_no_phase_down_note(self):
        # m535 / m536: income under the 29% bracket keeps the full BPA,
        # so no phase-down note.
        r = _ca(realized=1000.0, other_income=50000.0)
        self.assertNotIn("phased down", "\n".join(r["notes"]))

    @rule(R.CA_RPT_03)
    def test_assumed_factor_note_prints_the_percentage(self):
        # m572: the factor prints as a percentage (24.63%).
        r = _ca(year=2026, realized=1000000.0)
        self.assertIn("ON minimum-tax factor (24.63%) is ASSUMED",
                      "\n".join(r["notes"]))

    @rule(R.CA_RPT_05)
    def test_zero_actual_withholding_credits_nothing(self):
        # m540: books whose TAX rows withheld nothing get no FTC.
        r = _ca(foreign_div=1000.0, other_income=50000.0,
                actual_withheld=0.0)
        self.assertEqual(r["ftc_assumed"], 0.0)

    @rule(R.US_RPT_07, R.US_EST_CARRY_TERM)
    def test_carryover_nets_short_term_then_long_term(self):
        # m549: 1,500 of carryover: 1,000 absorbs the ST gain, the
        # remaining 500 reduces LT 5,000 -> 4,500.
        r = estimate_usa(st=1000.0, lt=5000.0, qualified_div=0.0, pil=0.0,
                         other_income=60000.0, other_losses=1500.0,
                         year=2025)
        self.assertEqual((r["st_net"], r["lt_net"]), (0.0, 4500.0))
        self.assertEqual(r["ordinary_offset"], 0.0)

    @rule(R.US_RPT_07, R.US_EST_CARRY_TERM)
    def test_carryover_left_after_lt_offsets_ordinary(self):
        # m551: LT 1,000 absorbs 1,000 of 1,500; the 500 left offsets
        # ordinary income (under the 3,000 cap) and is used up.
        r = estimate_usa(st=0.0, lt=1000.0, qualified_div=0.0, pil=0.0,
                         other_income=60000.0, other_losses=1500.0,
                         year=2025)
        self.assertEqual(r["lt_net"], 0.0)
        self.assertEqual(r["ordinary_offset"], 500.0)
        self.assertEqual(r["losses_unused"], 0.0)

    @rule(R.US_EST_NIIT_LOSS)
    def test_no_niit_on_a_net_loss(self):
        # m556 / m578: negative net investment income -> no NIIT.
        r = estimate_usa(st=-5000.0, lt=0.0, qualified_div=0.0, pil=0.0,
                         other_income=300000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual((r["niit"], r["niit_base"]), (0.0, 0.0))

    @rule(R.US_RPT_04)
    def test_no_niit_under_the_magi_threshold(self):
        # m579 / m580: MAGI 101,000 < 200,000 -> no NIIT. 1,000 LT at
        # 15% (taxable ordinary 84,250 > 48,350) = 150 = 15.00%.
        r = estimate_usa(st=0.0, lt=1000.0, qualified_div=0.0, pil=0.0,
                         other_income=100000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual((r["niit"], r["niit_base"]), (0.0, 0.0))
        self.assertEqual(r["estimated_tax"], 150.0)
        self.assertEqual(r["avg_rate_pct"], 15.0)

    @rule(R.US_RPT_04)
    def test_usa_figures_in_cents_and_average_rate(self):
        # m559, m408-m410, m562/m563/m581/m582: every figure in cents;
        # the average rate is estimated tax / investment income x 100.
        r = estimate_usa(st=1234.567, lt=2345.678, qualified_div=12.345,
                         pil=1.111, other_income=250000.123,
                         other_losses=0.0, year=2025)
        for d in (r["tax_with"], r["tax_base"]):
            for k in ("ordinary", "preferential", "total"):
                self.assertEqual(round(d[k], 2), d[k], k)
        for k in ("niit", "niit_base", "losses_unused"):
            self.assertEqual(round(r[k], 2), r[k], k)
        self.assertGreater(r["niit"], 0.0)
        self.assertEqual(r["niit_base"], 3593.7)        # 3,593.701  # pii-ok
        self.assertEqual(r["avg_rate_pct"],
                         round(r["estimated_tax"] / 3593.701 * 100, 2))
        self.assertEqual(r["avg_rate_pct"], 24.65)

    @rule(R.US_RPT_04)
    def test_no_investment_income_has_no_average_rate(self):
        # m414: no division by zero.
        r = estimate_usa(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                         other_income=1000.0, other_losses=0.0, year=2025)
        self.assertIsNone(r["avg_rate_pct"])

    @rule(R.US_RPT_07)
    def test_losses_unused_in_cents(self):
        r = estimate_usa(st=-9876.543, lt=0.0, qualified_div=0.0,
                         pil=0.0, other_income=50000.0,
                         other_losses=0.0, year=2025)
        self.assertEqual(r["losses_unused"], 6876.54)


if __name__ == "__main__":
    unittest.main()
