"""Mutation pins for re-audit-2 test-gap findings (tests-pins-04/05/06):
RBC and Webull parser dates and pairing, the MXN settlement cutover, the
phantom-relevance split de-duplication and the config checks.

Each test fails when the finding's surviving mutant is applied. All data
is synthetic: fake tickers and a fake broker account id.

Formerly: test_fix_a2_pins2_rbc"""
import unittest
from taxjson.model.transaction import TaxTransaction


# -------------------------------------------------------------- phantoms
class TestPhantomRelevanceSplitDedup(unittest.TestCase):
    """Formerly: test_fix_a2_pins2_rbc.TestPhantomRelevanceSplitDedup"""

    def test_a2_0933_split_booked_by_two_brokers_counts_once(self):
        """S033-14: one account fed by two broker files that both book
        the same 2:1 split. Counted twice, the walk saw a 4:1 split, the
        2026 cover of the carried short vanished and --write-missing-history
        wrote nothing."""
        from taxjson.lib.missing_history import (MissingHistoryCandidate,
                                                  assess_tax_year_relevance)

        def tx(action, d, tm, qty, net=0.0, price=0.0):
            return TaxTransaction(action=action, date=d, time=tm,
                                  symbol='XYZ.US', quantity=qty,
                                  currency='USD', price=price,
                                  net_amount=net, account='margin',
                                  date_settle=d)
        rows = [tx('BUYSELL', '2025-01-06', '10:00:00', 100, -1000, 10),
                tx('SPLIT', '2025-06-11', '00:00:00', 2.0),
                tx('SPLIT', '2025-06-11', '20:25:00', 2.0),
                tx('BUYSELL', '2025-08-04', '10:00:00', -200, 1200, 6),
                tx('BUYSELL', '2025-08-05', '10:00:00', -200, 1200, 6),
                tx('BUYSELL', '2026-03-02', '10:00:00', 50, -350, 7)]
        cand = MissingHistoryCandidate(symbol='XYZ.US', account='margin',
                                currency='USD',
                                first_negative_date='2025-08-05',
                                peak_short=-200.0, end_position=-150.0,
                                disposition_count=1, registered=False)
        r = assess_tax_year_relevance(rows, [cand], 2026)
        self.assertTrue(r[0].affects_year)


if __name__ == "__main__":
    unittest.main()
