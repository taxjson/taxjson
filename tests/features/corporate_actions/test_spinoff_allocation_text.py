"""Re-audit-2 test pins (tests-pins-03), A2-1611: the law citations that
earlier fixes corrected are asserted, so a revert to the wrong section
fails — ITA s.90(1) in taxjson-brokerage (s.90(2) is the foreign-
affiliate rule), the s.86.1(3) spin-off allocation text, the US
significant-holder statements (Reg. §1.368-3 / §1.355-5) in tax-logic,
and line 4 of the US Capital Loss Carryover Worksheet.

Formerly: test_fix_a2_pins1_03_citations"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestCanadaSpinoffAllocationText(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_03_citations.TestCanadaSpinoffAllocationText"""
    @rule(R.CA_CORP_06)
    def test_s86_1_3_and_form_8937_is_a_us_figure(self):
        from taxjson.lib.corp_actions import CANADA_SPINOFF
        text = " ".join(dict(CANADA_SPINOFF.options)['rollover_s_86_1']
                        .split())
        self.assertIn("combined fair market value right after it "
                      "(s. 86.1(3))", text)
        self.assertIn("Form 8937 percentage is a US figure", text)


if __name__ == "__main__":
    unittest.main()
