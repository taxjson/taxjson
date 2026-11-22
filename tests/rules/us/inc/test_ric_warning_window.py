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


def _inc(**kw):
    r = {"action": "DIVIDEND", "symbol": "ZZF.US", "date": "2026-01-15",
         "currency": "USD", "net_amount": 10.0, "gross_amount": 10.0}
    r.update(kw)
    return r


class TestUsRicWarningWindow(unittest.TestCase):
    """A2-0889, A2-0924 (M09), A2-1561, A2-1562: income_dating.warnings
    `d[:4] == str(prev) and d[5:7] in ("10", "11", "12")` and
    `pay[5:7] != "01"` — a January payment with an ex or record date in
    October-December of the PRIOR year is warned about; September, a
    February payment and a two-years-earlier date are not.

    Formerly: test_fix_a2_pins3_g3.TestUsRicWarningWindow"""

    def _warned(self, **kw):
        return bool(IncomeRules("usa").warnings([_inc(**kw)], year=2026))

    @rule(R.US_INC_DATE_RIC)
    def test_october_to_december_ex_dates_warn(self):
        for ex in ("2025-10-01", "2025-10-31", "2025-11-14", "2025-12-15"):
            with self.subTest(ex=ex):
                self.assertTrue(self._warned(ex_date=ex))
        # The record date counts too.
        self.assertTrue(self._warned(record_date="2025-10-03"))

    @rule(R.US_INC_DATE_RIC)
    def test_outside_the_window_is_quiet(self):
        self.assertFalse(self._warned(ex_date="2025-09-30"))
        self.assertFalse(self._warned(record_date="2025-09-30"))
        # Two years before the pay date: not the prior year.
        self.assertFalse(self._warned(ex_date="2024-11-14"))
        # A February payment is not a January dividend.
        self.assertFalse(self._warned(date="2026-02-13",
                                      ex_date="2025-12-15"))
        self.assertFalse(self._warned())


if __name__ == "__main__":
    unittest.main()
