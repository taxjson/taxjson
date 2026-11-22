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


class TestUsSignificantHolderStatements(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_03_citations.TestUsSignificantHolderStatements"""
    @rule(R.US_CORP_04, R.US_CORP_07)
    def test_tax_logic_names_the_regulations(self):
        from taxjson.lib.tax_logic import catalog
        c = catalog("usa")
        self.assertIn("Reg. §1.368-3", c["US-CORP-04"].text)
        self.assertIn("Reg. §1.355-5", c["US-CORP-07"].text)
        self.assertIn("significant holder", c["US-CORP-04"].text)
        self.assertIn("significant distributee", c["US-CORP-07"].text)


if __name__ == "__main__":
    unittest.main()
