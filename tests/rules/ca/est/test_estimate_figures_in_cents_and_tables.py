"""Mutation pins for lib/tax_estimate.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round: a behaviour of `tjs estimate` that is
right but was never asserted. Expected figures are hand arithmetic from
the published charts, not re-derivations through the same code.

Formerly: test_fix_x_mut_tax_estimate
"""
import unittest

from taxjson.lib import tax_estimate as TE
from taxjson.lib.tax_estimate import estimate_canada, estimate_usa, ontario_health_premium
from tax_rules import rule
from taxjson.rules import ids as R


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
    every floor, start, rate and cap of the table.

    Formerly: test_fix_x_mut_tax_estimate.TestOntarioHealthPremiumChart"""

    @rule(R.CA_RPT_03)
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


class TestCanadaNotes(unittest.TestCase):
    """What the printed estimate says it included (CA-RPT-03).

    Formerly: test_fix_x_mut_tax_estimate.TestCanadaNotes"""

    @rule(R.CA_RPT_03)
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

    @rule(R.CA_RPT_03)
    def test_binding_amt_prints_its_notes_but_no_headroom_note(self):
        # m182: a binding AMT has no carryover headroom to mention.
        r = _ca(year=2026, realized=1000000.0)
        self.assertTrue(r["amt"]["binding"])
        text = "\n".join(r["notes"])
        self.assertIn("ON AMT = 24.63% of the federal excess", text)
        self.assertIn("ASSUMED unchanged", text)
        self.assertNotIn("7 preceding years", text)


class TestCanadaFloorsAndForeignCredit(unittest.TestCase):
    """Formerly: test_fix_x_mut_tax_estimate.TestCanadaFloorsAndForeignCredit"""
    @rule(R.CA_RPT_03)
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

    @rule(R.CA_RPT_05)
    def test_provincial_ftc_share_is_capped_at_one(self):
        # Foreign income 60,000 with 40,000 deducted: taxable and net
        # income 20,000, so foreign income / net income = 3 — the T2036
        # share is capped at 100% (m337). ON basic tax = (20,000 -
        # 12,747) x 5.05% = 366.28; the unused federal credit
        # (9,000 - (20,000 x 14.5% - 2,338.71) = 8,438.71) covers it all.  # pii-ok
        r = _ca(foreign_div=60000.0, deductions=40000.0)
        t = r["trace_with"]
        self.assertAlmostEqual(t["fed_ftc_unused"], 8438.705, places=3)
        self.assertAlmostEqual(t["prov_basic"], 366.2765, places=4)
        self.assertAlmostEqual(t["prov_ftc"], 366.2765, places=4)
        self.assertAlmostEqual(r["tax_with"]["provincial"], 0.0, places=2)


class TestAmtFiguresInCents(unittest.TestCase):
    """The AMT block's money figures are rounded to cents
    (m346-m359 round(.., 2) -> 3).

    Formerly: test_fix_x_mut_tax_estimate.TestAmtFiguresInCents"""
    KEYS = ("adjusted_income", "exemption", "bpa_credit", "minimum_fed",
            "regular_fed", "excess_fed", "provincial_amt_basic",
            "provincial_amt_surtax", "provincial_amt", "topup",
            "carryforward", "headroom")

    def _cents(self, amt):
        for k in self.KEYS:
            self.assertEqual(round(amt[k], 2), amt[k], k)

    @rule(R.CA_RPT_03)
    def test_binding(self):
        r = _ca(realized=1234567.891, eligible_div=333.33,
                foreign_div=777.77, pil=11.11, other_income=12345.67)
        self.assertTrue(r["amt"]["binding"])
        self.assertEqual(r["amt"]["bpa_credit"], 1054.0)  # 14,538 x 14.5% / 2
        self._cents(r["amt"])

    @rule(R.CA_RPT_03)
    def test_not_binding(self):
        r = _ca(realized=1001.117, other_income=200000.0)
        self.assertFalse(r["amt"]["binding"])
        self._cents(r["amt"])


class TestOutputsInCents(unittest.TestCase):
    """Every money figure the estimate returns is rounded to cents
    (round(.., 2) -> 3 mutants on the result dicts).

    Formerly: test_fix_x_mut_tax_estimate.TestOutputsInCents"""

    def _cents(self, d, keys):
        for k in keys:
            self.assertEqual(round(d[k], 2), d[k], k)

    @rule(R.CA_RPT_03)
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

    @rule(R.CA_RPT_03)
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

    @rule(R.US_RPT_04)
    def test_usa(self):
        r = estimate_usa(st=-1234.567, lt=5678.913, qualified_div=11.111,
                         pil=2.229, other_income=45678.901,
                         other_losses=987.654, year=2025)
        self._cents(r, ("st_net", "lt_net", "ordinary_offset",
                        "offset_used_for_carryover", "losses_unused",
                        "niit", "niit_base", "magi", "estimated_tax",
                        "investment_income"))
        self.assertNotEqual(r["lt_net"], 0.0)

    @rule(R.US_EST_CARRY_TI)
    def test_usa_loss_year(self):
        r = estimate_usa(st=-1234.567, lt=-77.777, qualified_div=0.0,
                         pil=0.0, other_income=50000.123,
                         other_losses=987.654, year=2025)
        self._cents(r, ("st_net", "lt_net", "ordinary_offset",
                        "offset_used_for_carryover", "losses_unused"))
        self.assertEqual(r["st_net"], 0.0)
        self.assertEqual(r["ordinary_offset"], 2300.0)    # 987.654 + 1,312.344  # pii-ok
        self.assertEqual(r["offset_used_for_carryover"], 2300.0)
        r = estimate_usa(st=1234.567, lt=10.0, qualified_div=0.0,
                         pil=0.0, other_income=50000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(r["st_net"], 1234.57)


class TestCanadaLossesApplied(unittest.TestCase):
    """Formerly: test_fix_x_mut_tax_estimate.TestCanadaLossesApplied"""
    @rule(R.CA_RPT_03)
    def test_no_carryforward_applies_nothing(self):
        # m372: no --other-losses -> nothing applied, not 1.00.
        r = _ca(realized=1000.0, other_income=50000.0)
        self.assertEqual(r["losses_applied"], 0.0)
        self.assertEqual(r["losses_unused"], 0.0)

    @rule(R.CA_RPT_03)
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


class TestCapitalGainsDividends(unittest.TestCase):
    """Formerly: test_fix_x_mut_tax_estimate.TestCapitalGainsDividends"""
    @rule(R.CA_INC_06)
    def test_taxed_as_a_capital_gain_with_the_realized_gains(self):
        # 1,000.004 of T5 box 18 joins realized gains: 50% inclusion,  # pii-ok
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
    spec).

    Formerly: test_fix_x_mut_tax_estimate.TestPublishedTables"""

    @rule(R.CA_RPT_03)
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

    @rule(R.CA_RPT_04)
    def test_canada_constants(self):
        self.assertEqual(TE.CA_ELIGIBLE_GROSSUP, 1.38)
        self.assertEqual(TE.CA_FED_DTC_ELIGIBLE, .150198)
        self.assertEqual(TE.CA_FOREIGN_WITHHOLDING, .15)
        self.assertEqual(TE.CA_INCLUSION, .50)
        self.assertEqual((TE.CA_AMT_RATE, TE.CA_AMT_CREDIT_ALLOWANCE,
                          TE.CA_AMT_LOSS_ALLOWANCE,
                          TE.CA_AMT_CARRYING_CHARGE_ALLOWANCE),
                         (.205, .50, .50, .50))

    @rule(R.US_RPT_04)
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
