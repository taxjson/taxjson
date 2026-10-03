"""Medium-round fixes, filing-a / tax estimate (synthetic figures only).

R1-47   the Canada incremental estimate keeps its sign (a saving).
S077-16 federal foreign tax credit the federal tax cannot absorb goes
        to the provincial foreign tax credit (T2036).
R1-218  the unmodelled minimum-tax carryover (s.120.2) is disclosed.
S078-00 the assumptions line no longer points at views that do not
        show interest.
"""
import unittest

from taxjson.lib.tax_estimate import estimate_canada


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, year=2025, foreign_div=0.0,
                pil=0.0, other_income=0.0, other_losses=0.0, province="BC")
    args.update(kw)
    return estimate_canada(**args)


class NegativeIncrementKeepsSign(unittest.TestCase):
    """R1-47: eligible dividends at a low bracket save tax on the other
    income (the DTC exceeds the tax on the grossed-up amount)."""

    def test_saving_reported_negative(self):
        r = _ca(eligible_div=20000.0, other_income=32000.0)
        diff = round(r["tax_with"]["total"] - r["tax_base"]["total"], 2)
        self.assertLess(diff, 0.0)
        self.assertAlmostEqual(r["estimated_tax"], diff, places=2)
        self.assertAlmostEqual(r["estimated_tax_with_amt"],
                               round(diff + r["amt"]["topup"], 2), places=2)
        self.assertLess(r["avg_rate_pct"], 0.0)

    def test_positive_case_unchanged(self):
        r = _ca(realized=50000.0, other_income=60000.0)
        diff = round(r["tax_with"]["total"] - r["tax_base"]["total"], 2)
        self.assertGreater(diff, 0.0)
        self.assertAlmostEqual(r["estimated_tax"], diff, places=2)


class UnusedFederalFtcGoesToProvince(unittest.TestCase):
    """S077-16: 65,678.40 of foreign dividends, 15% withheld, no other
    income. Federal tax is 0; the part of the credit federal tax could
    not absorb reduces BC tax (T2036), limited to BC tax x F/NI."""

    def test_provincial_ftc(self):
        fd = 65678.40
        r = _ca(foreign_div=fd, actual_withheld=fd * 0.15)
        tw = r["trace_with"]
        fed_before_ftc = max(0.0, tw["fed_gross"] - tw["fed_bpa"]
                             - tw["fed_dtc"])
        unused = tw["fed_ftc"] - fed_before_ftc
        self.assertGreater(unused, 1000.0)
        self.assertAlmostEqual(r["tax_with"]["federal"], 0.0, places=2)
        prov_before = tw["prov_basic"] + tw["prov_surtax"] - tw["prov_dtc"]
        want_prov_ftc = min(unused, prov_before * min(1.0, fd / tw["net_income"]))
        self.assertAlmostEqual(tw["prov_ftc"], want_prov_ftc, places=2)
        self.assertAlmostEqual(r["tax_with"]["provincial"],
                               max(0.0, prov_before - want_prov_ftc), places=2)

    def test_limited_by_foreign_share_of_net_income(self):
        # Foreign income only part of net income: the provincial credit
        # is limited to provincial tax x F/NI.
        fd = 40000.0
        r = _ca(foreign_div=fd, other_income=5000.0,
                actual_withheld=fd * 0.15, province="ON")
        tw = r["trace_with"]
        prov_before = max(0.0, tw["prov_basic"] + tw["prov_surtax"]
                          - tw["prov_dtc"])
        cap = prov_before * fd / tw["net_income"]
        self.assertLessEqual(tw["prov_ftc"], cap + 0.01)

    def test_no_provincial_ftc_when_federal_absorbs_it(self):
        r = _ca(foreign_div=10000.0, other_income=100000.0,
                actual_withheld=1500.0)
        self.assertAlmostEqual(r["trace_with"]["prov_ftc"], 0.0, places=6)


class DisclosuresAndWording(unittest.TestCase):
    def test_amt_carryover_note_when_headroom(self):
        r = _ca(realized=20000.0, other_income=250000.0, year=2026,
                province="ON")
        self.assertFalse(r["amt"]["binding"])
        self.assertGreater(r["amt"]["headroom"], 0)
        joined = " ".join(r["notes"])
        self.assertIn("40427", joined)
        # The carryover is applied now when entered (CA-AMT-08); with
        # none entered the note says where to put it.
        self.assertIn("none is entered", joined)
        self.assertIn("amt_carryover.txt", joined)

    def test_assumptions_do_not_point_at_divs_fees_for_interest(self):
        r = _ca(realized=1000.0)
        self.assertNotIn("see divs/fees", r["assumptions"])
        self.assertIn("interest", r["assumptions"])


if __name__ == "__main__":
    unittest.main()
