"""The edges of Canada's income dating (TJ-4222): the branches no test
held when PLAN step 14 moved the rules out of lib/income_dating.py, each
mutated in a copy first (a payment in lieu labelled a distribution, the
record date's 92-day limit, a date with a time, a bare symbol that spells
a Canadian venue, a trust ROC whose record date is its pay
date, which ADJUST rows are a return of capital, the withholding that
follows its dividend's date, an ATTENTION line's year filter, its
tail when no project year is given, the amount a trust's ROC shows and
a record date in the pay year).

Rows are plain dicts (the module reads dicts and TaxTransactions
alike); every ticker and account id is made up.
"""
import unittest

from taxjson.lib.income_dating import IncomeRules
from taxjson.characterize.income_dating import (is_canadian_listing, is_return_of_capital,
                                                listing_suffix)
from tax_rules import rule
from taxjson.rules import ids as R

CA = "canada"
ACCT = "99900001"


def _row(**kw):
    r = {"action": "DIVIDEND", "symbol": "ZZT.TO", "date": "2026-01-15",
         "account": ACCT, "currency": "CAD", "net_amount": 10.0,
         "gross_amount": 10.0}
    r.update(kw)
    return r


def _dist(**kw):
    return _row(income_label="distribution", **kw)


class TestTrustDistributionDate(unittest.TestCase):
    @rule(R.CA_INC_DATE_PIL)
    def test_a_payment_in_lieu_labelled_a_distribution_keeps_its_pay_date(self):
        # The broker labels the payment in lieu of a trust's
        # distribution as it labels the distribution: only the
        # DIVIDEND row is dated by its record date.
        r = IncomeRules(CA)
        dist = _dist(record_date="2025-12-30")
        self.assertEqual(r.income_date(dist), "2025-12-30")
        pil = dict(dist, action="DIVIDEND_IN_LIEU")
        self.assertEqual(r.trust_record_date(pil), "")
        self.assertEqual(r.income_date(pil), "2026-01-15")

    @rule(R.CA_INC_DATE_TRUST)
    def test_a_record_date_92_days_before_the_pay_date_is_not_used(self):
        # MAX_RECORD_LEAD_DAYS (92): 91 days before is a declaration, 92
        # is not (the pay date, and an ATTENTION line).
        r = IncomeRules(CA)
        self.assertEqual(r.income_date(_dist(record_date="2025-10-16")),
                         "2025-10-16")
        far = _dist(record_date="2025-10-15")
        self.assertEqual(r.income_date(far), "2026-01-15")
        w = r.warnings([far], 2025)
        self.assertEqual(len(w), 1)
        self.assertIn("record date 2025-10-15 is not within 92 days", w[0])

    @rule(R.CA_INC_DATE_TRUST)
    def test_a_date_with_a_time_is_read_by_its_day(self):
        r = IncomeRules(CA)
        self.assertEqual(r.income_date(_dist(record_date="2025-12-30T00:00:00")),
                         "2025-12-30")

    @rule(R.CA_INC_DATE_ISSUER)
    def test_a_bare_symbol_that_spells_a_venue_is_no_canadian_listing(self):
        # Only the part after a dot is a listing suffix.
        self.assertEqual(listing_suffix("ZZT.TO"), "TO")
        self.assertTrue(is_canadian_listing("ZZT.TO"))
        for sym in ("TO", "ZZT"):
            self.assertEqual(listing_suffix(sym), "", sym)
            self.assertFalse(is_canadian_listing(sym), sym)


class TestTrustReturnOfCapital(unittest.TestCase):
    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_a_record_date_on_the_pay_date_moves_nothing(self):
        # apply_trust_roc_record_dates moves the rows roc_record_date
        # names (their date and settle date): one whose record date is
        # its pay date is not among them.
        r = IncomeRules(CA)
        roc = _row(action="ADJUST", type="roc", net_amount=-5.0,
                   record_date="2026-01-15")
        self.assertEqual(r.roc_record_date(roc), "")
        self.assertEqual(r.roc_record_date(dict(roc, record_date="2026-01-14")),
                         "2026-01-14")

    def test_which_adjust_rows_are_a_return_of_capital(self):
        # A [[distributions]] row is one only below zero; a hand-entered
        # untyped ADJUST is one unless it is a deferral (WASH_, and the
        # handoff's HANDOFF_WASH_).
        adj = {"action": "ADJUST", "symbol": "ZZT.TO", "net_amount": -5.0}
        self.assertTrue(is_return_of_capital(dict(adj, type="dist", id="DIST-1")))
        self.assertFalse(is_return_of_capital(
            dict(adj, type="dist", id="DIST-1", net_amount=0.0)))
        self.assertTrue(is_return_of_capital(dict(adj, id="TT-1")))
        self.assertFalse(is_return_of_capital(dict(adj, id="WASH_1")))
        self.assertFalse(is_return_of_capital(dict(adj, id="HANDOFF_WASH_1")))


class TestWithholdingFollowsItsDividend(unittest.TestCase):
    @rule(R.CA_INC_DATE_TRUST)
    def test_the_tax_takes_the_date_only_when_that_days_payouts_agree(self):
        r = IncomeRules(CA)
        tax = _row(action="TAX", net_amount=1.5, gross_amount=1.5)
        dist = _dist(record_date="2025-12-30")
        self.assertEqual(r.withholding_dates([dist, tax]), {id(tax): "2025-12-30"})
        # A row typed tax is withholding whatever its action says.
        typed = {k: v for k, v in tax.items() if k != "action"}
        typed["type"] = "tax"
        self.assertEqual(r.withholding_dates([dist, typed]),
                         {id(typed): "2025-12-30"})
        # A payment in lieu that day keeps its pay date: the day's
        # payouts are dated differently, so the tax keeps its own.
        pil = _row(action="DIVIDEND_IN_LIEU", net_amount=4.0, gross_amount=4.0)
        self.assertEqual(r.withholding_dates([dist, pil, tax]), {})
        # Two distributions dated by two record dates: the same.
        other = _dist(record_date="2025-12-29", net_amount=3.0, gross_amount=3.0)
        self.assertEqual(r.withholding_dates([dist, other, tax]), {})


class TestAttentionLines(unittest.TestCase):
    @rule(R.CA_INC_DATE_TRUST)
    def test_an_implausible_record_date_is_said_in_its_years_only(self):
        r = IncomeRules(CA)
        far = _dist(date="2025-06-30", record_date="2024-01-02")
        for year in (2024, 2025, None):
            self.assertEqual(len(r.warnings([far], year)), 1, year)
        self.assertEqual(r.warnings([far], 2023), [])
        self.assertEqual(r.warnings([far], 2026), [])

    @rule(R.CA_INC_DATE_TRUST)
    def test_a_record_date_in_the_pay_year_says_nothing(self):
        r = IncomeRules(CA)
        dist = _dist(date="2025-06-30", record_date="2025-06-20")
        self.assertEqual(r.income_date(dist), "2025-06-20")
        self.assertEqual(r.warnings([dist], 2025), [])
        self.assertEqual(r.warnings([dist]), [])

    @rule(R.CA_INC_DATE_TRUST)
    def test_without_a_project_year_the_pay_years_tail(self):
        r = IncomeRules(CA)
        dist = _dist(record_date="2025-12-30")
        w = r.warnings([dist])
        self.assertEqual(len(w), 1)
        self.assertIn("it is NOT in 2026's numbers: make sure the 2025 return "
                      "carries it", w[0])
        self.assertEqual(r.warnings([dist], 2026), w)
        self.assertIn("it is counted in 2025 here; the 2026 project leaves it "
                      "out", r.warnings([dist], 2025)[0])

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_a_trust_rocs_line_shows_the_amount_it_lowers_the_acb_by(self):
        # An ADJUST's net_amount is the cost change (negative for a
        # ROC): the line shows what the ACB drops by.
        r = IncomeRules(CA)
        roc = {"action": "ADJUST", "type": "roc", "symbol": "ZZT.TO",
               "date": "2026-01-15", "record_date": "2025-12-30",
               "account": ACCT, "currency": "CAD", "net_amount": -5.0}
        w = r.warnings([roc], 2025)
        self.assertEqual(len(w), 1)
        self.assertIn("ZZT.TO: return of capital 5.00 CAD paid 2026-01-15 with "
                      "record date 2025-12-30 lowers the ACB in 2025", w[0])
        far = dict(roc, record_date="2025-10-15")
        self.assertIn("used for this 5.00 CAD return of capital",
                      r.warnings([far], 2025)[0])


if __name__ == "__main__":
    unittest.main()
