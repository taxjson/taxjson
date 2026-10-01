"""Mutation pins for lib/income_dating.py (audit G1-0).

Each test kills mutants that survived the whole suite in the
2026-09-30 mutation round. Rows are plain dicts (the module reads
dicts and TaxTransactions alike); all tickers are invented.
"""
import unittest

from taxjson.lib.income_dating import (IncomeRules, IncomeRulesError,
                                       parse_ric_entries, rules_for)
from tax_rules import rule

CA = "canada"
US = "usa"


def _row(**kw):
    r = {"action": "DIVIDEND", "symbol": "ZZT.TO", "date": "2026-01-15",
         "currency": "CAD", "net_amount": 10.0, "gross_amount": 10.0}
    r.update(kw)
    return r


class TestSettingsValidation(unittest.TestCase):
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


class TestCanadaRecordDates(unittest.TestCase):
    @rule("CA-INC-DATE-TRUST")
    def test_distribution_record_date_after_pay_is_ignored(self):
        # m1493: a record date AFTER the pay date is nonsense -> pay date.
        r = IncomeRules(CA)
        row = _row(income_label="distribution", record_date="2026-01-20")
        self.assertEqual(r.income_date(row), "2026-01-15")
        row["record_date"] = "2025-12-31"
        self.assertEqual(r.income_date(row), "2025-12-31")

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_only_a_roc_adjust_moves_to_its_record_date(self):
        # m1495 (an ADJUST of another type keeps its date), m1496 (a
        # record date after the pay date is ignored), m1505 (row_date
        # of an ADJUST is the ROC date).
        r = IncomeRules(CA)
        roc = _row(action="ADJUST", type="roc", record_date="2025-12-31",
                   net_amount=-5.0)
        self.assertEqual(r.roc_date(roc), "2025-12-31")
        self.assertEqual(r.row_date(roc), "2025-12-31")
        other = dict(roc, type="acb")
        self.assertEqual(r.roc_date(other), "2026-01-15")
        self.assertEqual(r.row_date(other), "2026-01-15")
        late = dict(roc, record_date="2026-01-20")
        self.assertEqual(r.roc_date(late), "2026-01-15")


class TestUsRicJanuary(unittest.TestCase):
    @rule("US-INC-DATE-RIC")
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


class TestWarnings(unittest.TestCase):
    @rule("CA-INC-DATE-ROC-TRUST")
    def test_canada_january_trust_roc_without_record_date(self):
        # m1538/m1539 (January only), m1584/m1591/m1541 (the year and
        # the next), m1563 (only an ADJUST roc row), m1595/m1597 (the
        # amount is the row's).
        r = IncomeRules(CA)
        roc = dict(_row(action="ADJUST", type="roc", net_amount=-5.0))
        w = r.warnings([roc], 2025)
        self.assertEqual(len(w), 1)
        self.assertIn("ZZT.TO: return of capital 5.00 CAD paid 2026-01-15",
                      w[0])
        self.assertIn("drops on 2025-12-31", w[0])
        self.assertEqual(r.warnings([roc], 2026), w)           # year itself
        self.assertEqual(r.warnings([roc], 2024), [])
        self.assertEqual(r.warnings([roc], 2027), [])
        self.assertEqual(r.warnings([dict(roc, date="2026-03-15")], 2025),
                         [])
        self.assertEqual(r.warnings([_row(record_date=""),
                                     _row(symbol="ZZU", date="2026-01-15")],
                                    2025), [])
        zero = r.warnings([dict(roc, net_amount=0.0)], 2025)
        self.assertNotIn("1.00", zero[0])
        bare = dict(roc)
        del bare["net_amount"]                  # a dict row without it
        self.assertNotIn("1.00", r.warnings([bare], 2025)[0])

    @rule("US-INC-DATE-RIC")
    def test_us_january_dividend_with_a_q4_record_date(self):
        # m1546 (a Canadian issuer is not a RIC candidate), m1550/m1579
        # /m1588/m1593 (the amount shown is the gross, else the net).
        r = IncomeRules(US)
        div = _row(symbol="ZZF", currency="USD", gross_amount=100.0,
                   net_amount=85.0, record_date="2025-12-20")
        w = r.warnings([div], 2025)
        self.assertEqual(len(w), 1)
        self.assertIn("ZZF: dividend 100.00 USD paid 2026-01-15 with an "
                      "ex/record date of 2025-12-20", w[0])
        w = r.warnings([dict(div, gross_amount=0.0)], 2025)
        self.assertIn("dividend 85.00 USD", w[0])
        self.assertEqual(r.warnings([dict(div, symbol="ZZT.TO")], 2025), [])
        # A dict row without the gross takes the net; without either, 0.
        bare = dict(div)
        del bare["gross_amount"]
        self.assertIn("dividend 85.00 USD", r.warnings([bare], 2025)[0])
        del bare["net_amount"]
        self.assertIn("dividend 0.00 USD", r.warnings([bare], 2025)[0])


if __name__ == "__main__":
    unittest.main()
