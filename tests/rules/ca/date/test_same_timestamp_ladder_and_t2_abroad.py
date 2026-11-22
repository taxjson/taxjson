"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


@rule(R.CA_DATE_14)
class TestCaPriorityLadderPinned(unittest.TestCase):
    """G1-7: every rung of the Canada same-timestamp ladder, through the
    function and through the ca_main sort.

    Formerly: test_fix_misc.TestCaPriorityLadderPinned"""

    def _tx(self, action, qty, symbol="ZZQ.US"):
        from taxjson.model.transaction import TaxTransaction
        return TaxTransaction(action=action, date="2025-06-10", time="10:00:00",
                              quantity=qty, symbol=symbol, currency="CAD")

    def test_each_rung(self):
        from taxjson.lib.corporate_timeline import CaPriority as P, _ca_priority
        opt = "ZZQ250620C00010000.US"
        cases = [
            (self._tx("OPENING_BALANCE", 100), P.OPENING_BALANCE),
            (self._tx("DISALLOW", 0), P.DISALLOW),
            (self._tx("ASSIGN", -1, opt), P.ASSIGN_OPTION),
            (self._tx("ASSIGN", 100), P.ASSIGN_STOCK_OR_SPLIT),
            (self._tx("SPLIT", 2.0), P.ASSIGN_STOCK_OR_SPLIT),
            (self._tx("ADJUST", 5), P.ADJUST),
            (self._tx("BUYSELL", 100), P.TRADE),
            (self._tx("BUYSELL", 0.5), P.TRADE),        # a fractional buy
            (self._tx("BUYSELL", 1, opt), P.TRADE),      # an option buy, not an ASSIGN
            (self._tx("BUYSELL", -100), P.TRADE),        # one rung (CA-DATE-14)
            (self._tx("BUYSELL", 0), P.OTHER),
        ]
        for t, want in cases:
            self.assertEqual(_ca_priority(t), want, (t.action, t.quantity, t.symbol))

    def test_same_timestamp_sort_order(self):
        from taxjson.lib.corporate_timeline import event_sort_key
        opt = "ZZQ250620C00010000.US"
        rows = [self._tx("BUYSELL", 0), self._tx("ADJUST", 5),
                self._tx("BUYSELL", -100), self._tx("BUYSELL", 100),
                self._tx("ASSIGN", 100), self._tx("ASSIGN", -1, opt),
                self._tx("DISALLOW", 0), self._tx("OPENING_BALANCE", 100)]
        got = [(t.action, t.quantity) for t in
               sorted(rows, key=lambda t: event_sort_key(t, profile="ca_main"))]
        self.assertEqual(got, [("OPENING_BALANCE", 100), ("DISALLOW", 0),
                               ("ASSIGN", -1), ("ASSIGN", 100),
                               # tied trades: input order (CA-DATE-14)
                               ("BUYSELL", -100), ("BUYSELL", 100),
                               ("ADJUST", 5), ("BUYSELL", 0)])


class TestNonNorthAmericanSettlement(unittest.TestCase):
    """G5-0: LSE (GBP) and ASX (AUD) shares settle T+2, not the US T+1.

    Formerly: test_fix_misc.TestNonNorthAmericanSettlement"""

    @rule(R.CA_DATE_04)
    @rule(R.US_DATE_04)
    def test_gbp_and_aud_settle_t2(self):
        from taxjson.lib.brokerages.ib_extractor import get_ib_settlement
        from taxjson.lib.dates import settlement_lag_days
        self.assertEqual(get_ib_settlement("2025-03-28", "Stocks", "GBP"), "2025-04-01")
        self.assertEqual(get_ib_settlement("2026-05-14", "Stocks", "AUD"), "2026-05-18")
        # A Dec-30 sale settles in the next tax year.
        self.assertEqual(get_ib_settlement("2026-12-30", "Stocks", "GBP")[:4], "2027")
        self.assertEqual(get_ib_settlement("2026-12-30", "Stocks", "AUD")[:4], "2027")
        # North America unchanged; the UK/EU move to T+1 on 2027-10-11.
        self.assertEqual(get_ib_settlement("2025-03-28", "Stocks", "USD"), "2025-03-31")
        self.assertEqual(get_ib_settlement("2025-03-28", "Stocks", "CAD"), "2025-03-31")
        self.assertEqual(settlement_lag_days("2027-10-08", "GBP"), 2)
        self.assertEqual(settlement_lag_days("2027-10-11", "GBP"), 1)
        self.assertEqual(settlement_lag_days("2027-10-11", "EUR"), 1)
        self.assertEqual(settlement_lag_days("2027-10-11", "AUD"), 2)


if __name__ == "__main__":
    unittest.main()
