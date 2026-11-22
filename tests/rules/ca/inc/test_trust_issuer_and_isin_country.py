"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import os
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import tax_logic as TL


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _text(country, rid, **settings):
    """The rendered text of one rule under `settings`."""
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


# ------------------------------------------------------------ income
def _rules(country="canada", **settings):
    from taxjson.lib.income_dating import IncomeRules
    return IncomeRules.from_settings(dict(settings, country=country))


class TestWhichCanadianIssuerIsATrust(unittest.TestCase):
    """A2-0810 / A2-1466: the trust test is every Canadian issuer but the
    corporate list; tax-logic says so and the January warning asks.

    Formerly: test_fix_a2_spec.TestWhichCanadianIssuerIsATrust"""

    _ROC = {"action": "ADJUST", "type": "roc", "symbol": "ZZCO.TO",
            "date": "2026-01-06", "record_date": "2025-12-30",
            "net_amount": -100.0, "currency": "CAD",
            "description": "ZZCO CORP COMMON SHARES RETURN OF CAPITAL"}

    @rule(R.CA_INC_DATE_ISSUER)
    def test_corporation_roc_is_record_dated_until_listed(self):
        self.assertEqual(_rules().roc_date(self._ROC), "2025-12-30")
        listed = _rules(corporate_distributions=["ZZCO.TO"])
        self.assertEqual(listed.roc_date(self._ROC), "2026-01-06")
        w = _rules().warnings([self._ROC], year=2026)
        self.assertTrue(any("names a corporation" in x for x in w), w)
        self.assertIn("corporate_distributions",
                      _text("canada", "CA-INC-DATE-ISSUER"))

    @rule(R.CA_INC_DATE_ISSUER, R.CA_INC_DATE_ROC_TRUST)
    def test_january_roc_warning_asks(self):
        row = dict(self._ROC, symbol="ZZB.TO", record_date="",
                   description="ZZB INC RETURN OF CAPITAL")
        w = _rules().warnings([row], year=2026)
        self.assertEqual(len(w), 1)
        self.assertIn("If ZZB.TO is a Canadian trust", w[0])
        self.assertIn("corporation", w[0])
        self.assertNotIn("this Canadian trust's", w[0])
        self.assertEqual(
            _rules(corporate_distributions=["ZZB.TO"]).warnings(
                [row], year=2026), [])


class TestIssuerCountryFromTheIsin(unittest.TestCase):
    """A2-1470: the ISIN country, when given, decides a Canadian issuer
    over the listing — as the text now says.

    Formerly: test_fix_a2_spec.TestIssuerCountryFromTheIsin"""

    @rule(R.CA_INC_03)
    def test_pil_on_a_bermuda_issuer_listed_in_toronto(self):
        row = {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZP.UN.TO",
               "dealer_country": "CA", "issuer_country": "BM",
               "date": "2025-06-02"}
        self.assertFalse(_rules().pil_is_dividend(row))
        self.assertTrue(_rules().pil_is_dividend(
            dict(row, issuer_country="")))
        self.assertTrue(_rules().pil_is_dividend(
            dict(row, symbol="ZZP.US", issuer_country="CA")))
        self.assertIn("ISIN country", _text("canada", "CA-INC-03"))

    @rule(R.CA_INC_DATE_TRUST)
    def test_distribution_of_a_foreign_isin_keeps_the_pay_date(self):
        row = {"action": "DIVIDEND", "income_label": "distribution",
               "symbol": "ZZP.UN.TO", "date": "2026-01-15",
               "record_date": "2025-12-31", "issuer_country": "BM"}
        self.assertEqual(_rules().income_date(row), "2026-01-15")
        self.assertEqual(_rules().income_date(
            dict(row, issuer_country="")), "2025-12-31")
        self.assertIn("ISIN country", _text("canada", "CA-INC-DATE-TRUST"))


class TestPilOnATrustUnit(unittest.TestCase):
    """A2-1465: a payment in lieu on a Canadian ETF unit whose payouts
    the books never call distributions (IB) cannot be told from a share
    and is deemed a dividend; tax-logic states it and the slip decides
    (a unit the books show to be a trust's:
    rules.ca.inc.test_payment_in_lieu_on_trust_units).

    Formerly: test_fix_a2_spec.TestPilOnATrustUnit"""

    @rule(R.CA_INC_07)
    def test_etf_unit_pil_is_deemed(self):
        row = {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZX.TO",
               "dealer_country": "CA", "date": "2025-06-02"}
        self.assertTrue(_rules().pil_is_dividend(row))
        self.assertIn("trust's unit", _text("canada", "CA-INC-07"))


if __name__ == "__main__":
    unittest.main()
