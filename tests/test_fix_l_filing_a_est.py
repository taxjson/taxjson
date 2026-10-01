"""Low-round fixes and pins, tax estimate (lib/tax_estimate.py):
S077-24 / S078-02 (US NIIT counts the capital loss deduction), S078-01
(US carryforward limited by taxable income), S077-18 (Canadian note
amounts pinned to the computed figures)."""
import unittest

from taxjson.lib.report_model import fmt_money as m
from taxjson.lib.tax_estimate import estimate_canada, estimate_usa
from tax_rules import rule


def us(**kw):
    base = dict(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                other_income=100000.0, other_losses=0.0, year=2025)
    base.update(kw)
    return estimate_usa(**base)


class TestUsNiitLoss(unittest.TestCase):

    @rule("US-EST-NIIT-LOSS")
    def test_loss_deduction_reduces_nii(self):
        # ST -10,000 (3,000 deductible), qualified 20,000, other 300,000:
        # NII 17,000 -> NIIT 646.00 (was 760.00).
        r = us(st=-10000.0, qualified_div=20000.0, other_income=300000.0)
        self.assertEqual((r["niit_base"], r["niit"]), (17000.0, 646.0))

    @rule("US-EST-NIIT-LOSS")
    def test_mixed_loss(self):
        r = us(st=-2000.0, lt=-500.0, qualified_div=50000.0,
               other_income=300000.0)
        self.assertEqual((r["niit_base"], r["niit"]), (47500.0, 1805.0))

    @rule("US-RPT-04")
    def test_gain_year_unchanged(self):
        r = us(lt=20000.0, other_income=190000.0)
        self.assertEqual(r["niit"], 380.0)


class TestUsCarryoverTaxableIncome(unittest.TestCase):

    @rule("US-EST-CARRY-TI")
    def test_worksheet_line_4(self):
        # Standard deduction 15,750 (2025). Other income 0 / 10,000:
        # taxable income + 3,000 <= 0 -> nothing used, 10,000 carried.
        for oi, used, carried in ((0.0, 0.0, 10000.0),
                                  (10000.0, 0.0, 10000.0),
                                  (16750.0, 1000.0, 9000.0),
                                  (60000.0, 3000.0, 7000.0)):
            r = us(st=-10000.0, other_income=oi)
            self.assertEqual(r["ordinary_offset"], 3000.0)
            self.assertEqual((r["offset_used_for_carryover"],
                              r["losses_unused"]), (used, carried), oi)


class TestCanadaNotePins(unittest.TestCase):
    """S077-18: each NOTE prints the figure the estimate computed."""

    def test_surtax_ohp_bpa_staking(self):
        r = estimate_canada(realized=200000.0, eligible_div=10000.0,
                            foreign_div=5000.0, pil=0.0,
                            other_income=150000.0, other_losses=0.0,
                            province="ON", staking=1234.56, year=2026)
        tw, tb = r["trace_with"], r["trace_base"]
        notes = " ".join(r["notes"])
        self.assertIn(f"{m(tw['prov_surtax'])} with investments, "
                      f"{m(tb['prov_surtax'])} on other income alone", notes)
        self.assertIn(f"Ontario Health Premium: {m(tw['prov_ohp'])} with "
                      f"investments, {m(tb['prov_ohp'])} on other income "
                      f"alone", notes)
        self.assertGreater(tw["prov_surtax"], 0.0)
        self.assertEqual(tw["prov_ohp"], 900.0)
        self.assertIn(f"Federal BPA phased down by net income: "
                      f"{m(tw['fed_bpa_amount'])} with investments, "
                      f"{m(tb['fed_bpa_amount'])} on other income alone",
                      notes)
        self.assertNotEqual(tw["fed_bpa_amount"], tb["fed_bpa_amount"])
        self.assertIn("Crypto staking rewards (1,234.56)", notes)

    def test_on_amt_note(self):
        r = estimate_canada(realized=1000000.0, eligible_div=0.0,
                            foreign_div=0.0, pil=0.0, other_income=0.0,
                            other_losses=0.0, province="ON", year=2026)
        amt = r["amt"]
        self.assertTrue(amt["binding"])
        notes = " ".join(r["notes"])
        self.assertIn(f"ON AMT = {amt['provincial_factor'] * 100:.2f}% of "
                      f"the federal excess "
                      f"({m(amt['provincial_amt_basic'])})", notes)
        self.assertAlmostEqual(amt["provincial_amt_basic"],
                               amt["excess_fed"] * amt["provincial_factor"],
                               places=1)


if __name__ == "__main__":
    unittest.main()
