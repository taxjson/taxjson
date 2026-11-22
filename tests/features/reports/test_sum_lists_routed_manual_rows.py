"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import unittest
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _sell(symbol="AAPL.US", date="2025-05-02", qty=-100, proceeds=12000.0,
          cost=10000.0, direction="LONG", **extra):
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
         "disallowed_amount": 0.0, "days_held": 100,
         "direction": direction, "commission": 0.0, "fee": 0.0,
         "account": "margin"}
    e.update(extra)
    return e


if __name__ == "__main__":
    unittest.main()


class TestSumFileManualSection(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestSumFileManualSection"""
    def test_routed_rows_listed_in_the_sum(self):
        """R1-320: the .sum names phantom-basis sales it leaves out."""
        from taxjson.lib.gain_summary import summarize_gains
        from taxjson.bin.taxjson_sum_gains import format_report
        data = {"transactions": [_sell(symbol="DEF.TO", qty=-10,
                                       proceeds=150.0, cost=100.0)],
                "manual_reporting_required": [
                    {"symbol": "ABC.TO", "date": "2025-04-15",
                     "date_settle": "2025-04-16", "qty": -50,
                     "proceeds": 2000.0, "account": "margin"}],
                "summary": {"year": 2025}}
        text = format_report(summarize_gains(data), no_color=True)
        self.assertIn("MANUAL REPORTING REQUIRED", text)
        self.assertIn("ABC.TO", text)
