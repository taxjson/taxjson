"""Re-audit-2 conformance gaps: a mutant of each rule below survived the
whole suite in the re-audit's conformance pass. Each test here fails on
that mutant and passes on the real code. Synthetic data only.

Formerly: test_fix_a2_spec_gaps
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestUsTrustRocKeepsThePayDate(unittest.TestCase):
    """US-INC-DATE-ROC: a Canadian trust's return of capital with a
    printed record date lowers US basis on its PAY date (the record-date
    rule is Canada's, CA-INC-DATE-ROC-TRUST). The audit's mutant
    (USINCDATE-roc: roc_record_date's country gate removed) is
    equivalent: is_canadian_trust is Canada-only too.

    Formerly: test_fix_a2_spec_gaps.TestUsTrustRocKeepsThePayDate"""

    @rule(R.US_INC_DATE_ROC)
    def test_record_date_is_not_used(self):
        from taxjson.lib.income_dating import IncomeRules
        row = {"action": "ADJUST", "type": "roc", "symbol": "ZZT.UN.TO",
               "date": "2026-01-15", "record_date": "2025-12-31",
               "net_amount": -50.0, "currency": "CAD"}
        r = IncomeRules(country="usa")
        self.assertEqual(r.roc_date(row), "2026-01-15")
        self.assertEqual(r.row_date(row), "2026-01-15")


if __name__ == "__main__":
    unittest.main()
