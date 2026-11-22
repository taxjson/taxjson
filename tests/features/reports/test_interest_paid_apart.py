"""2026-10 workflow audit: the fixes the audit's confirmed findings asked
for (CHANGELOG "Fixed"): interest paid shown apart, a negative futures
price in a generic CSV, the audit over a pending election, the
checklist's hints, init's timezone, the single-folder year hints,
sanity with no holdings file, the headless election message, no empty
manifest.json, compare's exit codes (tests/test_compare.py).

Formerly: test_workflow_audit_fixes"""
import unittest


class TestInterestPaid(unittest.TestCase):
    """Formerly: test_workflow_audit_fixes.TestInterestPaid"""
    def test_paid_is_its_own_line_and_out_of_the_total(self):
        from taxjson.lib.income_summary import summarize_income
        from taxjson.bin.taxjson_sum_income import format_report
        txs = [{"action": "INTEREST", "date": "2025-01-15", "symbol": "CASH",
                "currency": "CAD", "net_amount": 25.50},
               {"action": "INTEREST", "date": "2025-02-15", "symbol": "CASH",
                "currency": "CAD", "net_amount": -140.25}]
        rep = summarize_income(txs, target_year=2025)
        self.assertEqual(rep["interest_totals"], {"CAD": 25.5})
        self.assertEqual(rep["interest_paid_totals"], {"CAD": 140.25})
        out = format_report(rep)
        lines = {ln.split("  ")[0].strip(): ln for ln in out.splitlines()}
        self.assertIn("25.50", lines["CASH INTEREST"])
        self.assertIn("140.25", lines["INTEREST PAID (not in total)"])
        self.assertIn("25.50", lines["TOTAL NET INCOME"])
        self.assertNotIn("-114.75", out)

    def test_no_paid_line_without_interest_charged(self):
        from taxjson.lib.income_summary import summarize_income
        from taxjson.bin.taxjson_sum_income import format_report
        rep = summarize_income([{"action": "INTEREST", "date": "2025-01-15",
                                 "symbol": "CASH", "currency": "USD",
                                 "net_amount": 10.0}], target_year=2025)
        self.assertNotIn("INTEREST PAID", format_report(rep))


if __name__ == "__main__":
    unittest.main()
