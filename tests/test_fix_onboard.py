"""Onboarding study fixes (docs/getting-started.md).

A stock dividend booked at $0 is fixed the documented way: a positive
ADJUST (a .tt line or a [[distributions]] entry) with the declared amount,
near the dividend date. The gains stage already treats that as the cost
(its ATTENTION goes quiet); find-missing-history must agree, or the
checklist's missing-history step stays open after the user fixed it.
"""
import unittest

from taxjson.lib.core import TaxTransaction
from taxjson.lib.missing_history import detect_zero_basis_acquisitions


def _tx(action, date, symbol, qty, net=0.0, price=0.0, account="margin",
        description=""):
    return TaxTransaction(action=action, date=date, time="09:30:00",
                          symbol=symbol, quantity=qty, net_amount=net,
                          account=account, currency="CAD", price=price,
                          description=description)


def _flagged(txs, year=2025):
    return {(r.symbol, r.account)
            for r in detect_zero_basis_acquisitions(txs, year)}


class TestZeroBasisCoveredByAdjust(unittest.TestCase):
    def _book(self, adjust=None):
        txs = [
            _tx("BUYSELL", "2024-02-06", "SAMPQ.TO", 100, 2004.95, 20.0),
            _tx("BUYSELL", "2024-09-16", "SAMPQ.TO", 10,
                description="SAMPLE Q CORP STK DIV ON 100 SHS"),
            _tx("BUYSELL", "2025-05-13", "SAMPQ.TO", -110, 2745.05, 25.0),
        ]
        if adjust:
            txs.append(adjust)
        return txs

    def test_uncovered_stock_dividend_is_flagged(self):
        self.assertIn(("SAMPQ.TO", "margin"), _flagged(self._book()))

    def test_adjust_on_the_dividend_date_covers_it(self):
        adj = _tx("ADJUST", "2024-09-16", "SAMPQ.TO", 0, 250.0)
        self.assertNotIn(("SAMPQ.TO", "margin"), _flagged(self._book(adj)))

    def test_adjust_in_another_account_does_not_cover_it(self):
        adj = _tx("ADJUST", "2024-09-16", "SAMPQ.TO", 0, 250.0,
                  account="other")
        self.assertIn(("SAMPQ.TO", "margin"), _flagged(self._book(adj)))

    def test_adjust_far_from_the_dividend_does_not_cover_it(self):
        # A return-of-capital style adjustment months later is not the
        # stock dividend's cost (same window as the gains-stage warning).
        adj = _tx("ADJUST", "2025-01-20", "SAMPQ.TO", 0, 250.0)
        self.assertIn(("SAMPQ.TO", "margin"), _flagged(self._book(adj)))

    def test_negative_adjust_does_not_cover_it(self):
        adj = _tx("ADJUST", "2024-09-16", "SAMPQ.TO", 0, -250.0)
        self.assertIn(("SAMPQ.TO", "margin"), _flagged(self._book(adj)))


if __name__ == "__main__":
    unittest.main()
