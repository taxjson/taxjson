"""The edges of the US January fund dividends (TJ-4222): the branches no
test held when PLAN step 14 moved the rules out of lib/income_dating.py,
each mutated in a copy first (a dotted ric_january_dividends entry, the
note's tail when no project year is given, and the flags the list is
forwarded to the tools as).

Rows are plain dicts; every ticker is made up.
"""
import unittest

from taxjson.lib.income_dating import IncomeRules
from taxjson.characterize.income_dating import IncomeRulesError, parse_ric_entries
from tax_rules import rule
from taxjson.rules import ids as R

US = "usa"


def _div(symbol, **kw):
    r = {"action": "DIVIDEND", "symbol": symbol, "date": "2026-01-15",
         "account": "U1234567", "currency": "USD", "net_amount": 20.0,
         "gross_amount": 20.0}
    r.update(kw)
    return r


class TestRicJanuaryEntries(unittest.TestCase):
    @rule(R.US_INC_DATE_RIC)
    def test_a_dotted_entry_names_one_listing_only(self):
        # A bare root covers its fund's US listing (ZZF -> ZZF.US); an
        # entry with a dot is a listing, matched exactly: ZZP.PR is not
        # the preferred series ZZP.PR.US.
        bare = IncomeRules(US, ric_january_dividends=(("ZZF", ""),))
        self.assertEqual(bare.income_date(_div("ZZF.US")), "2025-12-31")
        dotted = IncomeRules(US, ric_january_dividends=(("ZZP.PR", ""),))
        self.assertEqual(dotted.income_date(_div("ZZP.PR")), "2025-12-31")
        self.assertEqual(dotted.income_date(_div("ZZP.PR.US")), "2026-01-15")

    @rule(R.US_INC_DATE_RIC)
    def test_without_a_project_year_the_pay_years_tail(self):
        r = IncomeRules(US, ric_january_dividends=(("ZZF", ""),))
        div = _div("ZZF")
        w = r.warnings([div])
        self.assertEqual(len(w), 1)
        self.assertIn("it is NOT in 2026's numbers: make sure the 2025 return "
                      "carries it", w[0])
        self.assertEqual(r.warnings([div], 2026), w)
        self.assertIn("it is counted in 2025 here; the 2026 project leaves it "
                      "out", r.warnings([div], 2025)[0])


class TestRicJanuaryFlags(unittest.TestCase):
    @rule(R.US_INC_DATE_RIC)
    def test_the_flags_carry_each_entry_whole(self):
        # `tjs run` forwards the list to the tools as flags
        # (income_dating_flags): a dated entry keeps its date.
        r = IncomeRules(US, ric_january_dividends=(("ZZF", ""),
                                                   ("ZZG", "2026-01-15")))
        flags = r.cli_flags()
        self.assertEqual(flags, ["--ric-january-dividend", "ZZF",
                                 "--ric-january-dividend", "ZZG 2026-01-15"])
        self.assertEqual(parse_ric_entries(flags[1::2],
                                           key="--ric-january-dividend"),
                         r.ric_january_dividends)

    def test_a_blank_flag_is_refused_with_a_us_example(self):
        with self.assertRaises(IncomeRulesError) as cm:
            parse_ric_entries([" "], key="--ric-january-dividend")
        self.assertIn('--ric-january-dividend must be a list of symbols, e.g. '
                      '["SAMPLE.US"]', str(cm.exception))


if __name__ == "__main__":
    unittest.main()
