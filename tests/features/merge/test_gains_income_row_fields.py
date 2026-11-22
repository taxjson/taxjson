"""Backlog fix round 2026-10-29 (round 2), group `income`: dividend
forecasts (`tjs div-estimate`), dividend corrections and refunded
withholding, tips, slip-audit and roc-sum (T3 return of capital) and
the final-gains required-field contract.

TJ-0547  div-estimate: a dated rename carries the dividend history to
         the new symbol.
TJ-0548  div-estimate: withholding posted in another currency than its
         dividend is converted at the pay date's rate before the rate is
         read.
TJ-0549  div-estimate: a dividend paid after a split on shares counted
         on a record date before it is read in today's shares.
TJ-0552  div-estimate: a correction's refunded withholding moves with
         the gross it reverses.
TJ-0558  div-estimate: one broker account's short charge in a combined
         account is an expense, not a reversal of its own dividend,
         beside another broker account's larger long.
TJ-0429  slip-audit: the suggested .tt lines name the folder the year
         reads (../inputs/<account>/ in the shared-exports layout).
TJ-0420  slip-audit: a typed T3's box 42 is spread over the year's
         distributions when units were sold between them, not all put
         after the sale.
TJ-0427  roc-sum: a hand-typed .tt ADJUST type=roc line is a manual row.
TJ-0562  tips: TFSA-US-DIV follows the issuer's domicile, not the US
         listing alone.
TJ-0495  a gains document's income row without its amount or date is
         refused, an explicit 0 kept.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN.*, ZZ*), account names (margin), broker account
ids (99900001), amounts under 1,000. Planning numbers (CA-PLAN-06 /
US-PLAN-07) unless a test says otherwise.

Formerly: test_fix_1029_r2_income
"""
import json
import tempfile
import unittest
from pathlib import Path


class TestGainsIncomeRowContract(unittest.TestCase):
    """TJ-0495: a final gains document's DIVIDEND without its amount, or
    with no date, passed require_gains_doc; T1135 joined the first as
    income 0 and dropped the second, with no warning.

    Formerly: test_fix_1029_r2_income.TestGainsIncomeRowContract"""

    def _doc(self, row):
        return {"transactions": [dict(row, symbol="SYN.US",
                                      currency="CAD", gain=0.0)]}

    def test_an_income_row_without_its_amount_or_date_is_refused(self):
        from taxjson.lib.json_input import InputFileError, require_gains_doc
        for row, field in (
                ({"action": "DIVIDEND", "date": "2025-06-01"}, "dividend"),
                ({"action": "DIVIDEND", "dividend": 100.0}, "date"),
                ({"action": "DIVIDEND", "dividend": None,
                  "date": "2025-06-01"}, "dividend"),
                ({"action": "DIVIDEND_IN_LIEU", "date": "2025-06-01"},
                 "pil or dividend"),
                ({"action": "DIVIDEND_IN_LIEU", "pil": 5.0, "date": ""},
                 "date")):
            with self.subTest(row=row):
                with self.assertRaises(InputFileError) as cm:
                    require_gains_doc(self._doc(row), "g.json")
                msg = str(cm.exception)
                self.assertIn('g.json: "transactions" row 0 (SYN.US', msg)
                self.assertIn(f"an income row ({row['action']}) without "
                              f"{field}", msg)

    def test_an_explicit_zero_and_settle_date_are_kept(self):
        from taxjson.lib.json_input import require_gains_doc
        for row in ({"action": "DIVIDEND", "dividend": 0,
                     "date": "2025-06-01"},
                    {"action": "DIVIDEND", "dividend": 10.0,
                     "date_settle": "2025-06-02"},
                    {"action": "DIVIDEND_IN_LIEU", "pil": 5.0,
                     "date": "2025-06-01"},
                    # a s.260 deemed dividend: the run moves pil there.
                    {"action": "DIVIDEND_IN_LIEU", "dividend": 5.0,
                     "pil": 0.0, "date": "2025-06-01"}):
            with self.subTest(row=row):
                doc = self._doc(row)
                self.assertIs(require_gains_doc(doc, "g.json"), doc)

    def test_t1135_refuses_rather_than_joining_zero(self):
        from taxjson.bin.taxjson_t1135 import (UnreadableGains,
                                               join_income_gains)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_gains.json"
            p.write_text(json.dumps(self._doc(
                {"action": "DIVIDEND", "date": "2025-06-01"})))
            with self.assertRaises(UnreadableGains) as cm:
                join_income_gains([p], 2025, {})
            self.assertIn("without dividend", str(cm.exception))
            p.write_text(json.dumps(self._doc(
                {"action": "DIVIDEND", "dividend": 0.0,
                 "date": "2025-06-01"})))
            self.assertEqual(join_income_gains([p], 2025, {}),
                             {"SYN.US": {"income": 0.0, "gain": 0.0}})


if __name__ == "__main__":
    unittest.main()
