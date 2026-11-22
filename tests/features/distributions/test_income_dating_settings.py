"""Mutation pins for lib/income_dating.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round. Rows are plain dicts (the module reads
dicts and TaxTransactions alike); all tickers are invented.

Formerly: test_fix_x_mut_income_dating
"""
import unittest

from taxjson.lib.income_dating import IncomeRules, rules_for
from taxjson.characterize.income_dating import IncomeRulesError, parse_ric_entries

CA = "canada"
US = "usa"


class TestSettingsValidation(unittest.TestCase):
    """Formerly: test_fix_x_mut_income_dating.TestSettingsValidation"""
    def test_symbol_lists_must_be_lists_of_symbols(self):
        # m1477 (a bare string is refused, not read as characters),
        # m1566 (blank and non-string entries are refused).
        for bad in ("ZZT.TO", [""], ["  "], [1], ("ok", 2)):
            with self.assertRaises(IncomeRulesError, msg=repr(bad)):
                IncomeRules.from_settings({"country": CA,
                                           "corporate_distributions": bad})

    def test_ric_entries(self):
        # m1513: an impossible January date is refused.
        self.assertEqual(parse_ric_entries(["zzf", "ZZF 2026-01-15"]),
                         (("ZZF", ""), ("ZZF", "2026-01-15")))
        for bad in (["ZZF 2026-01-32"], ["ZZF 2026-02-15"],
                    ["ZZF 2026-01-15 x"]):
            with self.assertRaises(IncomeRulesError, msg=bad):
                parse_ric_entries(bad)

    def test_rules_for_without_settings(self):
        # m1506.
        r = rules_for(US)
        self.assertEqual((r.country, r.ric_january_dividends), (US, ()))


if __name__ == "__main__":
    unittest.main()
