"""Bug-review batch C (income): dividend estimation, income dating and
the year hand-off (findings 38, 49, 65, 76, 77, 92 of the 2026-10
review).

All data is synthetic (made-up tickers and accounts).

Formerly: test_fix_batch_c_income
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestRocWarningAccount(unittest.TestCase):
    """Formerly: test_fix_batch_c_income.TestRocWarningAccount"""

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_65_pair_in_another_account_or_currency_keeps_warning(self):
        from taxjson.lib.income_dating import rules_for
        r = rules_for("canada")
        roc = {"action": "ADJUST", "type": "roc", "date": "2025-01-10",
               "symbol": "ZZT.UN.TO", "account": "margin",
               "currency": "CAD", "net_amount": -10.0}

        def pair(acct, cur):
            return [{"action": "ADJUST", "date": d, "symbol": "ZZT.UN.TO",
                     "account": acct, "currency": cur, "net_amount": n}
                    for d, n in (("2024-12-31", -10.0),
                                 ("2025-01-10", 10.0))]
        self.assertEqual(len(r.warnings([roc], 2025)), 1)
        self.assertEqual(len(r.warnings([roc] + pair("other", "CAD"),
                                        2025)), 1)
        self.assertEqual(len(r.warnings([roc] + pair("margin", "USD"),
                                        2025)), 1)
        self.assertEqual(r.warnings([roc] + pair("margin", "CAD"), 2025),
                         [])
        # A pair row naming no account still counts (as before).
        self.assertEqual(r.warnings([roc] + pair("", "CAD"), 2025), [])


if __name__ == "__main__":
    unittest.main()
