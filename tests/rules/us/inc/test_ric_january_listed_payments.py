"""Mutation pins for lib/income_dating.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round. Rows are plain dicts (the module reads
dicts and TaxTransactions alike); all tickers are invented.

Formerly: test_fix_x_mut_income_dating
"""
import unittest

from taxjson.lib.income_dating import IncomeRules
from tax_rules import rule
from taxjson.rules import ids as R

US = "usa"


def _row(**kw):
    r = {"action": "DIVIDEND", "symbol": "ZZT.TO", "date": "2026-01-15",
         "currency": "CAD", "net_amount": 10.0, "gross_amount": 10.0}
    r.update(kw)
    return r


class TestUsRicJanuary(unittest.TestCase):
    """Formerly: test_fix_x_mut_income_dating.TestUsRicJanuary"""
    @rule(R.US_INC_DATE_RIC)
    def test_listed_payments_only(self):
        # m1502 (a February payment of a listed fund stays), m1569 (a
        # dated entry moves only that pay date).
        r = IncomeRules(US, ric_january_dividends=(("ZZF", ""),
                                                   ("ZZG", "2026-01-15")))
        self.assertEqual(r.income_date(_row(symbol="ZZF",
                                            date="2026-01-20")),
                         "2025-12-31")
        self.assertEqual(r.income_date(_row(symbol="ZZF",
                                            date="2026-02-20")),
                         "2026-02-20")
        self.assertEqual(r.income_date(_row(symbol="ZZG",
                                            date="2026-01-15")),
                         "2025-12-31")
        self.assertEqual(r.income_date(_row(symbol="ZZG",
                                            date="2026-01-20")),
                         "2026-01-20")
        self.assertEqual(r.income_date(_row(symbol="ZZH",
                                            date="2026-01-15")),
                         "2026-01-15")


if __name__ == "__main__":
    unittest.main()
