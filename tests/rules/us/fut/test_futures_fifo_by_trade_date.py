"""Batch D (the FX-on-cash ledger v2, the Kraken cash reader, futures):
2026-10 audit findings 71, 72, 78, 86, 91, 102, 103. All data
synthetic.

Formerly: test_fix_batch_d_cash"""
import unittest


class TestUsFuturesTradeDateFifo(unittest.TestCase):
    """#102: US FIFO consumed futures lots in settlement order (a later
    trade with an earlier settle date was the "first in").

    Formerly: test_fix_batch_d_cash.TestUsFuturesTradeDateFifo"""

    def test_fifo_by_trade_date(self):
        from taxjson.engine.us.taxrules import USATaxRules
        from taxjson.model.transaction import TaxTransaction as T
        from taxjson.lib.futures import settle_futures
        rows = [T(action="BUYSELL", date=d, date_settle=sd,
                  time="09:30:00", symbol="F:SAMP", quantity=q,
                  currency="USD", net_amount=n, price=n, account="margin")
                for d, sd, q, n in [("2025-01-02", "2025-01-06", 1, 100),
                                    ("2025-01-03", "2025-01-03", 1, 200),
                                    ("2025-01-04", "2025-01-04", -1, 300)]]
        out, _ = settle_futures(rows, "fifo")
        self.assertEqual([t.date_settle for t in out],
                         ["2025-01-06", "2025-01-03", "2025-01-04"])
        self.assertEqual(out[2].net_amount, 200.0)
        r = USATaxRules().compute_gains(out, detect_wash_sales=False)
        self.assertEqual(r["summary"]["total_gain"], 200.0)


if __name__ == "__main__":
    unittest.main()
