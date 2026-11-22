"""Batch D (options / crypto sends / ALLOWLOSS): `tjs option-yield`
(lib/option_yield.py), the fair value of a crypto send
(lib/crypto_sends.py) and the sales an ALLOWLOSS line names
(lib/loss_overrides.py).

2026-10 bug review findings 37, 47, 81 and 88. Synthetic rows only.

Formerly: test_fix_batch_d_options"""
import unittest
from types import SimpleNamespace

from taxjson.lib import loss_overrides as LO


class AllowLossWholeSaleTest(unittest.TestCase):
    """#88: a line names a whole sale or none, its units over every
    fill of the sale, whichever day each settles.

    Formerly: test_fix_batch_d_options.AllowLossWholeSaleTest"""

    ROWS = [SimpleNamespace(id=str(i), account="margin", symbol="SAMP.US",
                            action="BUYSELL", date="2024-12-30",
                            date_settle=s, quantity=-1)
            for i, s in enumerate(["2024-12-31", "2025-01-02"])]

    def plan(self, day, qty):
        ov = dict(account="margin", symbol="SAMP.US", date=day, qty=qty)
        return LO.plan([ov], self.ROWS)

    def test_one_fill_settle_date_names_no_sale(self):
        p = self.plan("2024-12-31", 1.0)
        self.assertEqual(p.groups[0], [])
        self.assertEqual(p.fill_of[0], [2.0])
        self.assertEqual(p.row_of, {})

    def test_whole_sale_on_one_fill_settle_date_names_none(self):
        p = self.plan("2024-12-31", 2.0)
        self.assertEqual(p.groups[0], [])

    def test_settle_date_of_some_fills_names_none_without_units(self):
        p = self.plan("2024-12-31", None)
        self.assertEqual(p.groups[0], [])
        self.assertEqual(p.split_settle[0], [("2024-12-30", 2.0)])
        self.assertEqual(p.row_of, {})

    def test_trade_date_without_units_names_the_whole_sale(self):
        p = self.plan("2024-12-30", None)
        self.assertEqual([[t.id for t in s] for s in p.groups[0]],
                         [["0", "1"]])
        self.assertEqual(p.split_settle[0], [])

    def test_message_gives_the_sale_trade_date(self):
        it = {"where": "margin/x.tt:3", "line": "ALLOWLOSS ...",
              "account": "margin", "date": "2024-12-31",
              "symbol": "SAMP.US", "qty": None, "status": "unmatched",
              "sales": [], "fill_of": [],
              "split_settle": [["2024-12-30", 2.0]]}
        (msg,) = LO.problems([it])
        self.assertIn("only some fills of the 2-unit sale", msg)
        self.assertIn("date the line 2024-12-30", msg)

    def test_trade_date_whole_sale_matches(self):
        p = self.plan("2024-12-30", 2.0)
        self.assertEqual([[t.id for t in s] for s in p.groups[0]],
                         [["0", "1"]])


if __name__ == "__main__":
    unittest.main()
