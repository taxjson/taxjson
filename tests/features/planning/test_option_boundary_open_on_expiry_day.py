"""Re-audit-2 fixes, filing-reports list (helper B): option-boundary,
reconcile-slips, Schedule 3 option units, the .sum per-asset block and
the fees report.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_b
"""
import unittest
from datetime import date

from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def T(action="BUYSELL", **kw):
    base = {"action": action, "currency": "CAD", "account": "margin",
            "time": "10:00:00"}
    base.update(kw)
    base.setdefault("date_settle", base.get("date"))
    return TaxTransaction(**base)


class TestOptionBoundaryExpiryDay(unittest.TestCase):
    """A2-1112: on its own expiry day a contract is still open (the
    broker posts the expiry row afterwards), as the run's own
    expired-options warning already treats it.

    Formerly: test_fix_a2_filrep_b.TestOptionBoundaryExpiryDay"""

    S = "Q261001C00050000.TO"

    def _book(self):
        return [T(date="2026-09-01", symbol=self.S, quantity=-1, price=4,
                  net_amount=399.0),
                T(date="2026-09-02", symbol="Z261001C00010000.TO",
                  quantity=1, price=1, net_amount=101.0)]

    @rule(R.CA_OPT_07)
    def test_a2_1112_expiry_day_is_open(self):
        from taxjson.lib.option_boundary import expired_open, straddling
        today = date(2026, 10, 1)
        self.assertEqual(expired_open(self._book(), 2026, today=today), [])
        rows = straddling(self._book(), 2026, "grant", 2026, today=today)
        self.assertEqual([r["close_kind"] for r in rows], ["open"])

    @rule(R.CA_OPT_07)
    def test_a2_1112_day_after_expiry_is_missing_row(self):
        from taxjson.lib.option_boundary import expired_open, straddling
        today = date(2026, 10, 2)
        self.assertEqual(len(expired_open(self._book(), 2026,
                                          today=today)), 2)
        rows = straddling(self._book(), 2026, "grant", 2026, today=today)
        self.assertEqual([r["close_kind"] for r in rows], ["expired?"])

    @rule(R.CA_OPT_07)
    def test_a2_1112_dec31_expiry_of_a_past_year_still_flagged(self):
        from taxjson.lib.option_boundary import expired_open
        book = [T(date="2025-09-01", symbol="Q251231C00050000.TO",
                  quantity=-1, price=4, net_amount=399.0)]
        rows = expired_open(book, 2025, today=date(2026, 3, 1))
        self.assertEqual([r["symbol"] for r in rows],
                         ["Q251231C00050000.TO"])


if __name__ == "__main__":
    unittest.main()
