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


def _inc(**kw):
    r = {"action": "DIVIDEND", "symbol": "ZZF.US", "date": "2026-01-15",
         "currency": "USD", "net_amount": 10.0, "gross_amount": 10.0}
    r.update(kw)
    return r


# ====================================================== sum-income
class TestSumIncomeAccumulates(unittest.TestCase):
    """A2-1563: income_summary.summarize_income adds every row of a
    (ticker, currency) — the 'div', 'tax', 'pil' and type-'other'
    accumulators (`+=`), not only the last one.

    Formerly: test_fix_a2_pins3_g3.TestSumIncomeAccumulates"""

    def test_two_rows_per_column_add_up(self):
        from taxjson.lib.income_summary import summarize_income
        rows = [
            _inc(symbol="ZZD.US", date="2025-03-14", gross_amount=50.0,
                 net_amount=50.0),
            _inc(symbol="ZZD.US", date="2025-06-13", gross_amount=100.0,
                 net_amount=100.0),
            {"action": "TAX", "symbol": "ZZD.US", "date": "2025-03-14",
             "currency": "USD", "net_amount": 7.5},
            {"action": "TAX", "symbol": "ZZD.US", "date": "2025-06-13",
             "currency": "USD", "net_amount": 15.0},
            {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZD.US",
             "date": "2025-09-12", "currency": "USD", "gross_amount": 3.0,
             "net_amount": 3.0},
            {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZD.US",
             "date": "2025-12-12", "currency": "USD", "gross_amount": 4.0,
             "net_amount": 4.0},
            {"action": "OTHER", "type": "other", "symbol": "ZZO.US",
             "date": "2025-04-01", "currency": "USD", "net_amount": 10.0},
            {"action": "OTHER", "type": "other", "symbol": "ZZO.US",
             "date": "2025-05-01", "currency": "USD", "net_amount": 20.0},
        ]
        st = summarize_income(rows, 2025)["ticker_stats"]
        self.assertEqual(st["ZZD.US"]["USD"],
                         {"div": 150.0, "tax": 22.5, "pil": 7.0})
        self.assertEqual(st["ZZO.US"]["USD"]["div"], 30.0)


if __name__ == "__main__":
    unittest.main()
