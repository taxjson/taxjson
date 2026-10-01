"""Mutation pins for lib/tax_estimate.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round: a behaviour of `taxjson estimate` that is
right but was never asserted. Expected figures are hand arithmetic from
the published charts, not re-derivations through the same code.
"""
import unittest

from taxjson.lib import tax_estimate as TE
from taxjson.lib.tax_estimate import (bracket_slices, estimate_canada,
                                      estimate_usa, ontario_health_premium,
                                      stacked_slices)
from tax_rules import rule


def _ca(**kw):
    base = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON",
                year=2025)
    base.update(kw)
    return estimate_canada(**base)


class TestOntarioHealthPremiumChart(unittest.TestCase):
    """ON428 line 89 (unindexed): 6% over 20,000 to 300; 6% over 36,000
    to 450; 25% over 48,000 to 600; 25% over 72,000 to 750; 25% over
    200,000 to 900. One point on each phase-in and each plateau pins
    every floor, start, rate and cap of the table."""

    @rule("CA-RPT-03")
    def test_chart(self):
        for income, want in ((0, 0.0), (20000, 0.0), (22000, 120.0),
                             (24999, 299.94), (30000, 300.0),
                             (36000, 300.0), (37000, 360.0),
                             (38499, 449.94), (40000, 450.0),
                             (48000, 450.0), (48400, 550.0),
                             (60000, 600.0), (72000, 600.0),
                             (72400, 700.0), (100000, 750.0),
                             (200000, 750.0), (200400, 850.0),
                             (300000, 900.0)):
            self.assertAlmostEqual(ontario_health_premium(income), want,
                                   places=6, msg=income)


class TestTraceSlices(unittest.TestCase):
    """The --verbose trace's bracket slices (taxjson_run prints them)."""
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


class TestCanadaNotes(unittest.TestCase):
    """What the printed estimate says it included (CA-RPT-03)."""

    @rule("CA-RPT-03")
    def test_non_binding_amt_prints_no_provincial_amt_note(self):
        # m179 / m180: a non-binding AMT names no provincial AMT and no
        # ASSUMED factor (2026 ON's factor is assumed); it does print
        # the carryover headroom note.
        r = _ca(year=2026, other_income=200000.0, realized=1000.0)
        self.assertFalse(r["amt"]["binding"])
        text = "\n".join(r["notes"])
        self.assertNotIn("AMT =", text)
        self.assertNotIn("ASSUMED", text)
        self.assertIn("7 preceding years", text)
        # m700 / m708: the surtax tiers print as percentages.
        self.assertIn("ON surtax (20% over 5,818 + 36% over 7,446 of "
                      "basic ON tax)", text)

    @rule("CA-RPT-03")
    def test_binding_amt_prints_its_notes_but_no_headroom_note(self):
        # m182: a binding AMT has no carryover headroom to mention.
        r = _ca(year=2026, realized=1000000.0)
        self.assertTrue(r["amt"]["binding"])
        text = "\n".join(r["notes"])
        self.assertIn("ON AMT = 24.63% of the federal excess", text)
        self.assertIn("ASSUMED unchanged", text)
        self.assertNotIn("7 preceding years", text)


class TestUsaInvestmentIncome(unittest.TestCase):
    @rule("US-RPT-04")
    def test_investment_income_sums_every_component(self):
        # m205: st + lt + qualified + pil, each counted once.
        r = estimate_usa(st=1000.0, lt=2000.0, qualified_div=300.0,
                         pil=40.0, other_income=50000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["investment_income"], 3340.0)


class TestCanadaFloorsAndForeignCredit(unittest.TestCase):
    @rule("CA-RPT-03")
    def test_deductions_above_income_floor_income_at_zero(self):
        # Net and taxable income never go below zero (m246 ti floor,
        # m332 net floor, m267 AMT adjusted-income floor) — and foreign
        # dividends on a zero net income do not divide by zero (m262,
        # m335: the provincial FTC share needs net > 0).
        r = _ca(foreign_div=1000.0, deductions=10000.0)
        self.assertEqual(r["trace_with"]["ti"], 0.0)
        self.assertEqual(r["trace_with"]["net_income"], 0.0)
        self.assertEqual(r["trace_with"]["prov_ftc"], 0.0)
        self.assertEqual(r["trace_base"]["ti"], 0.0)
        self.assertEqual(r["amt"]["adjusted_income"], 0.0)

    @rule("CA-RPT-05")
    def test_provincial_ftc_share_is_capped_at_one(self):
        # Foreign income 60,000 with 40,000 deducted: taxable and net
        # income 20,000, so foreign income / net income = 3 — the T2036
        # share is capped at 100% (m337). ON basic tax = (20,000 -
        # 12,747) x 5.05% = 366.28; the unused federal credit
        # (9,000 - (20,000 x 14.5% - 2,338.71) = 8,438.71) covers it all.
        r = _ca(foreign_div=60000.0, deductions=40000.0)
        t = r["trace_with"]
        self.assertAlmostEqual(t["fed_ftc_unused"], 8438.705, places=3)
        self.assertAlmostEqual(t["prov_basic"], 366.2765, places=4)
        self.assertAlmostEqual(t["prov_ftc"], 366.2765, places=4)
        self.assertAlmostEqual(r["tax_with"]["provincial"], 0.0, places=2)


class TestAmtFiguresInCents(unittest.TestCase):
    """The AMT block's money figures are rounded to cents
    (m346-m359 round(.., 2) -> 3)."""
    KEYS = ("adjusted_income", "exemption", "bpa_credit", "minimum_fed",
            "regular_fed", "excess_fed", "provincial_amt_basic",
            "provincial_amt_surtax", "provincial_amt", "topup",
            "carryforward", "headroom")

    def _cents(self, amt):
        for k in self.KEYS:
            self.assertEqual(round(amt[k], 2), amt[k], k)

    @rule("CA-RPT-03")
    def test_binding(self):
        r = _ca(realized=1234567.891, eligible_div=333.33,
                foreign_div=777.77, pil=11.11, other_income=12345.67)
        self.assertTrue(r["amt"]["binding"])
        self.assertEqual(r["amt"]["bpa_credit"], 1054.0)  # 14,538 x 14.5% / 2
        self._cents(r["amt"])

    @rule("CA-RPT-03")
    def test_not_binding(self):
        r = _ca(realized=1001.117, other_income=200000.0)
        self.assertFalse(r["amt"]["binding"])
        self._cents(r["amt"])


class TestUsaPieces(unittest.TestCase):
    @rule("US-RPT-04")
    def test_income_under_the_standard_deduction_is_untaxed(self):
        # m301: ordinary taxable income floors at 0, not 1.
        r = estimate_usa(st=0.0, lt=5000.0, qualified_div=0.0, pil=0.0,
                         other_income=10000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["tax_base"]["total"], 0.0)
        self.assertEqual(r["trace_base"]["ord_taxable"], 0.0)

    @rule("US-RPT-04")
    def test_unused_standard_deduction_reduces_preferential_income(self):
        # m306: 20,000 LT gain, no other income: 15,750 (2025) of
        # deduction is left for it, 4,250 is taxable.
        r = estimate_usa(st=0.0, lt=20000.0, qualified_div=0.0, pil=0.0,
                         other_income=0.0, other_losses=0.0, year=2025)
        self.assertEqual(r["trace_with"]["pref_taxable"], 4250.0)

    @rule("US-RPT-07")
    def test_payment_in_lieu_is_ordinary_income(self):
        # m318: 1,000 PIL on 80,000 of wages (taxable 64,250 -> 65,250,
        # all in the 22% band) = 220.
        r = estimate_usa(st=0.0, lt=0.0, qualified_div=0.0, pil=1000.0,
                         other_income=80000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["estimated_tax"], 220.0)

    @rule("US-RPT-04")
    def test_niit_base_counts_every_investment_component(self):
        # m321: MAGI far over 200,000 -> NIIT on all 3,340 = 126.92.
        r = estimate_usa(st=1000.0, lt=2000.0, qualified_div=300.0,
                         pil=40.0, other_income=300000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["niit_base"], 3340.0)
        self.assertEqual(r["niit"], 126.92)


class TestOutputsInCents(unittest.TestCase):
    """Every money figure the estimate returns is rounded to cents
    (round(.., 2) -> 3 mutants on the result dicts)."""

    def _cents(self, d, keys):
        for k in keys:
            self.assertEqual(round(d[k], 2), d[k], k)

    @rule("CA-RPT-03")
    def test_canada(self):
        r = _ca(realized=12345.678, other_losses=2345.671,
                eligible_div=101.013, foreign_div=55.557, pil=7.777,
                staking=3.333, other_income=45678.901,
                deductions=1234.567, carrying_charges=89.017)
        self._cents(r, ("taxable_gain", "losses_applied", "losses_unused",
                        "grossed_eligible", "staking", "deductions",
                        "carrying_charges", "ftc_assumed", "estimated_tax",
                        "investment_income", "estimated_tax_with_amt"))
        self.assertEqual(r["staking"], 3.33)
        # m552: the average rate is in hundredths of a percent too.
        self.assertEqual(round(r["avg_rate_pct"], 2), r["avg_rate_pct"])
        self.assertNotEqual(round(r["avg_rate_pct"], 1), r["avg_rate_pct"])
        self.assertEqual(r["deductions"], 1234.57)

    @rule("CA-RPT-03")
    def test_canada_binding_provincial_amt(self):
        r = _ca(realized=987654.321, other_income=1234.567)
        self.assertTrue(r["amt"]["binding"])
        for k in ("provincial_amt", "provincial_amt_basic",
                  "provincial_amt_surtax", "topup"):
            self.assertEqual(round(r["amt"][k], 2), r["amt"][k], k)
        self.assertAlmostEqual(
            r["amt"]["provincial_amt"],
            r["amt"]["provincial_amt_basic"]
            + r["amt"]["provincial_amt_surtax"], delta=0.011)

    @rule("US-RPT-04")
    def test_usa(self):
        r = estimate_usa(st=-1234.567, lt=5678.913, qualified_div=11.111,
                         pil=2.229, other_income=45678.901,
                         other_losses=987.654, year=2025)
        self._cents(r, ("st_net", "lt_net", "ordinary_offset",
                        "offset_used_for_carryover", "losses_unused",
                        "niit", "niit_base", "magi", "estimated_tax",
                        "investment_income"))
        self.assertNotEqual(r["lt_net"], 0.0)

    @rule("US-EST-CARRY-TI")
    def test_usa_loss_year(self):
        r = estimate_usa(st=-1234.567, lt=-77.777, qualified_div=0.0,
                         pil=0.0, other_income=50000.123,
                         other_losses=987.654, year=2025)
        self._cents(r, ("st_net", "lt_net", "ordinary_offset",
                        "offset_used_for_carryover", "losses_unused"))
        self.assertEqual(r["st_net"], 0.0)
        self.assertEqual(r["ordinary_offset"], 2300.0)    # 987.654 + 1,312.344
        self.assertEqual(r["offset_used_for_carryover"], 2300.0)
        r = estimate_usa(st=1234.567, lt=10.0, qualified_div=0.0,
                         pil=0.0, other_income=50000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["st_net"], 1234.57)


class TestCanadaLossesApplied(unittest.TestCase):
    @rule("CA-RPT-03")
    def test_no_carryforward_applies_nothing(self):
        # m372: no --other-losses -> nothing applied, not 1.00.
        r = _ca(realized=1000.0, other_income=50000.0)
        self.assertEqual(r["losses_applied"], 0.0)
        self.assertEqual(r["losses_unused"], 0.0)

    @rule("CA-RPT-03")
    def test_loss_year_applies_no_carryforward(self):
        # m373: a net LOSS year uses none of the carryforward (s.111(1)(b)
        # caps it at the year's gains); it all carries on.
        r = _ca(realized=-500.0, other_losses=1000.0,
                other_income=50000.0)
        self.assertEqual(r["losses_applied"], 0.0)
        self.assertEqual(r["losses_unused"], 1500.0)
        r = _ca(realized=-500.004, other_losses=1000.0,
                other_income=50000.0)
        self.assertEqual(r["losses_unused"], 1500.0)     # in cents
        self.assertEqual(r["taxable_gain"], 0.0)


class TestRoundTwoPins(unittest.TestCase):
    @rule("CA-RPT-03")
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

    @rule("CA-RPT-03")
    def test_full_bpa_prints_no_phase_down_note(self):
        # m535 / m536: income under the 29% bracket keeps the full BPA,
        # so no phase-down note.
        r = _ca(realized=1000.0, other_income=50000.0)
        self.assertNotIn("phased down", "\n".join(r["notes"]))

    @rule("CA-RPT-03")
    def test_assumed_factor_note_prints_the_percentage(self):
        # m572: the factor prints as a percentage (24.63%).
        r = _ca(year=2026, realized=1000000.0)
        self.assertIn("ON minimum-tax factor (24.63%) is ASSUMED",
                      "\n".join(r["notes"]))

    @rule("CA-RPT-05")
    def test_zero_actual_withholding_credits_nothing(self):
        # m540: books whose TAX rows withheld nothing get no FTC.
        r = _ca(foreign_div=1000.0, other_income=50000.0,
                actual_withheld=0.0)
        self.assertEqual(r["ftc_assumed"], 0.0)

    @rule("US-RPT-07")
    def test_carryover_nets_short_term_then_long_term(self):
        # m549: 1,500 of carryover: 1,000 absorbs the ST gain, the
        # remaining 500 reduces LT 5,000 -> 4,500.
        r = estimate_usa(st=1000.0, lt=5000.0, qualified_div=0.0, pil=0.0,
                         other_income=60000.0, other_losses=1500.0,
                         year=2025)
        self.assertEqual((r["st_net"], r["lt_net"]), (0.0, 4500.0))
        self.assertEqual(r["ordinary_offset"], 0.0)

    @rule("US-RPT-07")
    def test_carryover_left_after_lt_offsets_ordinary(self):
        # m551: LT 1,000 absorbs 1,000 of 1,500; the 500 left offsets
        # ordinary income (under the 3,000 cap) and is used up.
        r = estimate_usa(st=0.0, lt=1000.0, qualified_div=0.0, pil=0.0,
                         other_income=60000.0, other_losses=1500.0,
                         year=2025)
        self.assertEqual(r["lt_net"], 0.0)
        self.assertEqual(r["ordinary_offset"], 500.0)
        self.assertEqual(r["losses_unused"], 0.0)

    @rule("US-EST-NIIT-LOSS")
    def test_no_niit_on_a_net_loss(self):
        # m556 / m578: negative net investment income -> no NIIT.
        r = estimate_usa(st=-5000.0, lt=0.0, qualified_div=0.0, pil=0.0,
                         other_income=300000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual((r["niit"], r["niit_base"]), (0.0, 0.0))

    @rule("US-RPT-04")
    def test_no_niit_under_the_magi_threshold(self):
        # m579 / m580: MAGI 101,000 < 200,000 -> no NIIT. 1,000 LT at
        # 15% (taxable ordinary 84,250 > 48,350) = 150 = 15.00%.
        r = estimate_usa(st=0.0, lt=1000.0, qualified_div=0.0, pil=0.0,
                         other_income=100000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual((r["niit"], r["niit_base"]), (0.0, 0.0))
        self.assertEqual(r["estimated_tax"], 150.0)
        self.assertEqual(r["avg_rate_pct"], 15.0)

    @rule("US-RPT-04")
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
        self.assertEqual(r["niit_base"], 3593.7)        # 3,593.701
        self.assertEqual(r["avg_rate_pct"],
                         round(r["estimated_tax"] / 3593.701 * 100, 2))
        self.assertEqual(r["avg_rate_pct"], 24.65)

    @rule("US-RPT-04")
    def test_no_investment_income_has_no_average_rate(self):
        # m414: no division by zero.
        r = estimate_usa(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                         other_income=1000.0, other_losses=0.0, year=2025)
        self.assertIsNone(r["avg_rate_pct"])

    @rule("US-RPT-07")
    def test_losses_unused_in_cents(self):
        r = estimate_usa(st=-9876.543, lt=0.0, qualified_div=0.0,
                         pil=0.0, other_income=50000.0,
                         other_losses=0.0, year=2025)
        self.assertEqual(r["losses_unused"], 6876.54)


class TestCapitalGainsDividends(unittest.TestCase):
    @rule("CA-INC-06")
    def test_taxed_as_a_capital_gain_with_the_realized_gains(self):
        # 1,000.004 of T5 box 18 joins realized gains: 50% inclusion,
        # shown back in cents; 0 by default.
        r = _ca(realized=200.0, other_income=50000.0,
                capital_gains_dividends=1000.004)
        self.assertEqual(r["taxable_gain"], 600.0)
        self.assertEqual(r["capital_gains_dividends"], 1000.0)
        self.assertEqual(r["grossed_eligible"], 0.0)
        d = _ca(realized=200.0, other_income=50000.0)
        self.assertEqual((d["taxable_gain"], d["capital_gains_dividends"]),
                         (100.0, 0.0))
        r = _ca(realized=200.0, other_income=50000.0,
                capital_gains_dividends=None)
        self.assertEqual(r["taxable_gain"], 100.0)


INF = float("inf")
# The published figures each vintage must carry (CRA indexation
# releases, ON/BC/AB rate cards, IRS Rev. Proc. 2023-34 / 2025-32 +
# OBBBA; sources in lib/tax_estimate.py). A change to a table must be a
# deliberate re-verification: update it here in the same commit.
PUBLISHED = {
    "2024": {
        "CA_FED_BRACKETS": [(55867, .15), (111733, .205), (173205, .26),
                            (246752, .29), (INF, .33)],
        "CA_FED_BPA": 15705.0, "CA_FED_BPA_MIN": 14156.0,
        "ON": ([(51446, .0505), (102894, .0915), (150000, .1116),
                (220000, .1216), (INF, .1316)], 12399.0, .10,
               [(5554, .20), (7108, .36)], .2463),
        "BC": ([(47937, .0506), (95875, .077), (110076, .105),
                (133664, .1229), (181232, .147), (252752, .168),
                (INF, .205)], 12580.0, .12, [], .337),
        "AB": ([(148269, .10), (177922, .12), (237230, .13),
                (355845, .14), (INF, .15)], 21885.0, .0812, [], .35),
        "US_STD_DEDUCTION": 14600.0,
        "US_ORD_BRACKETS": [(11600, .10), (47150, .12), (100525, .22),
                            (191950, .24), (243725, .32), (609350, .35),
                            (INF, .37)],
        "US_LTCG_BRACKETS": [(47025, .00), (518900, .15), (INF, .20)],
    },
    "2025": {
        "CA_FED_BRACKETS": [(57375, .145), (114750, .205), (177882, .26),
                            (253414, .29), (INF, .33)],
        "CA_FED_BPA": 16129.0, "CA_FED_BPA_MIN": 14538.0,
        "ON": ([(52886, .0505), (105775, .0915), (150000, .1116),
                (220000, .1216), (INF, .1316)], 12747.0, .10,
               [(5710, .20), (7307, .36)], .2463),
        "BC": ([(49279, .0506), (98560, .077), (113158, .105),
                (137407, .1229), (186306, .147), (259829, .168),
                (INF, .205)], 12932.0, .12, [], .349),
        "AB": ([(60000, .08), (151234, .10), (181481, .12),
                (241974, .13), (362961, .14), (INF, .15)], 22323.0,
               .0812, [], .35),
        "US_STD_DEDUCTION": 15750.0,
        "US_ORD_BRACKETS": [(11925, .10), (48475, .12), (103350, .22),
                            (197300, .24), (250525, .32), (626350, .35),
                            (INF, .37)],
        "US_LTCG_BRACKETS": [(48350, .00), (533400, .15), (INF, .20)],
    },
    "2026": {
        "CA_FED_BRACKETS": [(58523, .14), (117045, .205), (181440, .26),
                            (258482, .29), (INF, .33)],
        "CA_FED_BPA": 16452.0, "CA_FED_BPA_MIN": 14829.0,
        "ON": ([(53891, .0505), (107785, .0915), (150000, .1116),
                (220000, .1216), (INF, .1316)], 12989.0, .10,
               [(5818, .20), (7446, .36)], .2463),
        "BC": ([(50363, .056), (100728, .077), (115648, .105),
                (140430, .1229), (190405, .147), (265545, .168),
                (INF, .205)], 13216.0, .12, [], .400),
        "AB": ([(61200, .08), (154259, .10), (185111, .12),
                (246813, .13), (370220, .14), (INF, .15)], 22769.0,
               .0812, [], .35),
        "US_STD_DEDUCTION": 16100.0,
        "US_ORD_BRACKETS": [(12400, .10), (50400, .12), (105700, .22),
                            (201775, .24), (256225, .32), (640600, .35),
                            (INF, .37)],
        "US_LTCG_BRACKETS": [(49450, .00), (545500, .15), (INF, .20)],
    },
}


class TestPublishedTables(unittest.TestCase):
    """Every figure of every vintage (the const mutants on _VINTAGES:
    a threshold off by one dollar moves an estimate by cents, so no
    behavioural test can see most of them — the table itself is the
    spec)."""

    @rule("CA-RPT-03")
    def test_canada_tables(self):
        self.assertEqual(sorted(TE._VINTAGES), sorted(PUBLISHED))
        for y, want in PUBLISHED.items():
            v = TE._VINTAGES[y]
            self.assertEqual(v["CA_FED_BRACKETS"], want["CA_FED_BRACKETS"], y)
            self.assertEqual(v["CA_FED_BPA"], want["CA_FED_BPA"], y)
            self.assertEqual(v["CA_FED_BPA_MIN"], want["CA_FED_BPA_MIN"], y)
            self.assertEqual(sorted(v["CA_PROVINCES"]), ["AB", "BC", "ON"])
            for prov in ("ON", "BC", "AB"):
                br, bpa, dtc, surtax, amt = want[prov]
                got = v["CA_PROVINCES"][prov]
                self.assertEqual(got["brackets"], br, (y, prov))
                self.assertEqual(got["bpa"], bpa, (y, prov))
                self.assertEqual(got["dtc_eligible"], dtc, (y, prov))
                self.assertEqual(got["surtax"], surtax, (y, prov))
                self.assertEqual(got["amt_factor"], amt, (y, prov))
                self.assertEqual(bool(got.get("health_premium")),
                                 prov == "ON", (y, prov))
        self.assertEqual(
            {(y, p) for y in TE._VINTAGES
             for p, d in TE._VINTAGES[y]["CA_PROVINCES"].items()
             if d.get("amt_factor_assumed")},
            {("2026", "ON"), ("2026", "AB")})

    @rule("CA-RPT-04")
    def test_canada_constants(self):
        self.assertEqual(TE.CA_ELIGIBLE_GROSSUP, 1.38)
        self.assertEqual(TE.CA_FED_DTC_ELIGIBLE, .150198)
        self.assertEqual(TE.CA_FOREIGN_WITHHOLDING, .15)
        self.assertEqual(TE.CA_INCLUSION, .50)
        self.assertEqual((TE.CA_AMT_RATE, TE.CA_AMT_CREDIT_ALLOWANCE,
                          TE.CA_AMT_LOSS_ALLOWANCE,
                          TE.CA_AMT_CARRYING_CHARGE_ALLOWANCE),
                         (.205, .50, .50, .50))

    @rule("US-RPT-04")
    def test_usa_tables(self):
        for y, want in PUBLISHED.items():
            v = TE._VINTAGES[y]
            for k in ("US_STD_DEDUCTION", "US_ORD_BRACKETS",
                      "US_LTCG_BRACKETS"):
                self.assertEqual(v[k], want[k], (y, k))
        self.assertEqual((TE.US_NIIT_RATE, TE.US_NIIT_MAGI_THRESHOLD,
                          TE.US_ORDINARY_LOSS_CAP), (.038, 200000.0, 3000.0))


if __name__ == "__main__":
    unittest.main()
