"""Re-audit-2 test pins (lists tests-pins-07/08, fork g3): check-dates
session and range boundaries, edge-cases day-30 boundaries, the R1-28
assignment pairing, income dating (listings, split-share roots, the US
§852(b)(7) warning window) and the per-ticker income sums.

Each test kills a mutant that survived the whole suite in the second
audit (the docstrings name the A2 id and the line pinned). Every
fixture is SYNTHETIC: invented tickers (QZ*, XYZ, GHI), fake accounts.

Formerly: test_fix_a2_pins3_g3
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


# ====================================================== income dating
from taxjson.lib.income_dating import IncomeRules  # noqa: E402

# Spelled out here, not read from the module: dropping an entry there
# must fail a test (re-audit A2-1562).
_CA_SUFFIXES = ("TO", "V", "CN", "NE", "VN")
_SPLIT_SHARE_ROOTS = ("BK", "DF", "DFN", "DGS", "ENS", "FFN", "FTN", "GDV",
                      "LBS", "LCS", "LFE", "PDV", "PIC", "PWI", "SBC", "SBN",
                      "WFS", "XMF", "XTD", "YCM")


def _inc(**kw):
    r = {"action": "DIVIDEND", "symbol": "ZZF.US", "date": "2026-01-15",
         "currency": "USD", "net_amount": 10.0, "gross_amount": 10.0}
    r.update(kw)
    return r


def _dist(symbol):
    return _inc(symbol=symbol, currency="CAD", income_label="distribution",
                record_date="2025-12-31")


class TestCanadaListingsAndSplitShares(unittest.TestCase):
    """A2-0924 (M13, 'V'), A2-1562: every Canadian listing suffix in
    CA_LISTING_SUFFIXES dates a trust's distribution by its record date,
    and every SPLIT_SHARE_ROOTS entry (a corporation) keeps the pay
    date.

    Formerly: test_fix_a2_pins3_g3.TestCanadaListingsAndSplitShares"""

    @rule(R.CA_INC_DATE_TRUST)
    def test_every_canadian_listing_is_a_canadian_trust(self):
        rules = IncomeRules("canada")
        for sfx in _CA_SUFFIXES:
            with self.subTest(suffix=sfx):
                self.assertEqual(rules.income_date(_dist(f"ZZT.UN.{sfx}")),
                                 "2025-12-31")
        # A US listing is a foreign fund: the pay date.
        self.assertEqual(rules.income_date(_dist("ZZT.US")), "2026-01-15")

    @rule(R.CA_INC_DATE_TRUST, R.CA_INC_DATE_DIV)
    def test_every_split_share_root_keeps_the_pay_date(self):
        rules = IncomeRules("canada")
        for root in _SPLIT_SHARE_ROOTS:
            for sym in (f"{root}.TO", f"{root}.PR.A.TO"):
                with self.subTest(symbol=sym):
                    self.assertEqual(rules.income_date(_dist(sym)),
                                     "2026-01-15")


class TestCanadaJanuaryRocWarning(unittest.TestCase):
    """A2-0924 (M08): the January-ROC warning fires only when the export
    has NO record date (`and not _iso(_get(r, "record_date"))`).

    Formerly: test_fix_a2_pins3_g3.TestCanadaJanuaryRocWarning"""

    def _roc(self, **kw):
        r = {"action": "ADJUST", "type": "roc", "symbol": "ZZT.UN.TO",
             "date": "2026-01-15", "currency": "CAD", "net_amount": -12.0}
        r.update(kw)
        return r

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_no_warning_when_the_record_date_is_printed(self):
        rules = IncomeRules("canada")
        bare = rules.warnings([self._roc()], year=2026)
        self.assertTrue(any("with no record date" in w for w in bare), bare)
        dated = rules.warnings([self._roc(record_date="2026-01-05")],
                               year=2026)
        self.assertFalse(any("with no record date" in w for w in dated),
                         dated)


if __name__ == "__main__":
    unittest.main()
