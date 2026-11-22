"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest

from taxjson.lib import tax_estimate as te
from tax_rules import rule
from taxjson.rules import ids as R


class _EstimateCase(unittest.TestCase):
    def tearDown(self):
        te.apply_vintage(2025)          # the import-time default


class TestCanadaAmtPinned(_EstimateCase):
    """G1-5: a binding AMT with every ATI term, to the cent.

    Formerly: test_fix_misc.TestCanadaAmtPinned"""

    @rule(R.CA_RPT_03)
    def test_binding_amt_exact(self):
        r = te.estimate_canada(year=2026, realized=600000, eligible_div=0,
                               foreign_div=20000, pil=10000, staking=5000,
                               other_income=0, other_losses=40000,
                               province="ON")
        a = r["amt"]
        # ATI = gains 600,000 - 50% x 40,000 losses + foreign 20,000
        #       + PIL 10,000 + staking 5,000.
        self.assertEqual(a["adjusted_income"], 615000.0)
        # (615,000 - 181,440) x 20.5% - BPA 14,829 x 14% x 50% - FTC 3,000
        fed_min = (615000 - 181440) * .205 - 14829 * .14 * .5 - 20000 * .15
        self.assertEqual(a["minimum_fed"], round(fed_min, 2))
        self.assertEqual(a["minimum_fed"], 84841.77)
        self.assertEqual(a["bpa_credit"], 1038.03)
        self.assertEqual(a["regular_fed"], 72849.99)
        self.assertEqual(a["excess_fed"], 11991.78)
        self.assertEqual(a["provincial_amt_basic"], 2953.58)
        self.assertEqual(a["provincial_amt_surtax"], 1654.0)
        self.assertEqual(a["topup"], 16599.36)
        self.assertTrue(a["binding"])
        self.assertEqual(r["taxable_gain"], 280000.0)
        self.assertEqual(r["investment_income"], 635000.0)
        self.assertEqual(r["estimated_tax"], 120952.22)
        self.assertEqual(r["estimated_tax_with_amt"], 137551.58)


if __name__ == "__main__":
    unittest.main()
