"""Bug-review batch C (income): dividend estimation, income dating and
the year hand-off (findings 38, 49, 65, 76, 77, 92 of the 2026-10
review).

All data is synthetic (made-up tickers and accounts).

Formerly: test_fix_batch_c_income
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R


class TestHandoffBoundary(unittest.TestCase):
    """Formerly: test_fix_batch_c_income.TestHandoffBoundary"""
    CFG = {"settings": {"country": "canada"},
           "accounts": {"a": {"type": "taxable"},
                        "b": {"type": "taxable"}}}

    @staticmethod
    def _rec(acct, d):
        return {"account": acct, "action": "DIVIDEND", "date": d,
                "tax_date": d, "symbol": "ZZH.US", "quantity": 0,
                "net": 10}

    def _issues(self, cache, rows_b, rec_rows):
        from taxjson.lib import handoff as H
        (cache / "b_base.json").write_text(json.dumps(
            {"transactions": rows_b}))
        return H._boundary_issues(cache, self.CFG,
                                  {"year": 2024, "boundary_rows": rec_rows},
                                  "settle", set())

    @rule(R.CA_RPT_08)
    def test_76_another_accounts_row_is_not_this_one(self):
        """The closed record has account a's Dec 31 dividend; this
        project's account b has an identical one the closed books never
        had: it is in neither return."""
        row = {"action": "DIVIDEND", "date": "2024-12-31",
               "date_settle": "2024-12-31", "symbol": "ZZH.US",
               "quantity": 0, "net_amount": 10, "account": "b"}
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            out = self._issues(cache, [row], [self._rec("a", "2024-12-31")])
            self.assertEqual(len(out), 1, out)
            self.assertIn("neither return", out[0]["why"])
            # The same row in the record under b: matched, no issue.
            self.assertEqual(
                self._issues(cache, [row], [self._rec("b", "2024-12-31")]),
                [])
            # An older record's rows name no account: matched as before.
            old = self._rec("a", "2024-12-31")
            del old["account"]
            self.assertEqual(self._issues(cache, [row], [old]), [])

    @rule(R.CA_RPT_08)
    def test_77_one_row_here_answers_one_record_row(self):
        """The closed project's inputs held two identical early-January
        dividends; this project has one: one is in neither return."""
        row = {"action": "DIVIDEND", "date": "2025-01-03",
               "date_settle": "2025-01-03", "symbol": "ZZH.US",
               "quantity": 0, "net_amount": 10, "account": "b"}
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            rec = self._rec("b", "2025-01-03")
            out = self._issues(cache, [row], [rec, dict(rec)])
            self.assertEqual(len(out), 1, out)
            self.assertIn("neither return", out[0]["why"])
            self.assertEqual(
                self._issues(cache, [row, dict(row)], [rec, dict(rec)]), [])
            self.assertEqual(len(self._issues(cache, [], [rec, dict(rec)])),
                             2)


if __name__ == "__main__":
    unittest.main()
