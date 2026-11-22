"""The 2026-10-29 fix round 3, group wash: the superficial-loss views
and the planning tools (backlog TJ-0600, TJ-0620, TJ-0599, TJ-0615).
Synthetic data only: made-up tickers (ZZR, OTH, SYN, ZZC, ZZQ) and
account numbers in throwaway folders.

Formerly: test_fix_1029_r3_wash"""
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar, _row


class TestRadarNamesTheTradeDate(unittest.TestCase):
    """TJ-0615: a Canadian buy traded on 2026-10-15 settles on 10-16.
    The radar's notes named the settlement date as the day it was
    bought ("SHELTERED 'tfsa' bought 2026-10-16"), while its
    `recent_buy` names the trade date the broker shows. The window and
    the clear date still count settlement dates.

    Formerly: test_fix_1029_r3_wash.TestRadarNamesTheTradeDate"""

    _TAX = [_row("2026-01-07", "ZZC.TO", 100, 10.0, settle="2026-01-08")]
    _BUY = _row("2026-10-15", "ZZC.TO", 20, 9.0, acct="tfsa",
                settle="2026-10-16")

    def _row_of(self, taxable, sheltered):
        return _radar("canada", taxable, sheltered,
                      as_of="2026-10-29")["ZZC.TO"]

    @rule(R.CA_PLAN_01)
    def test_tj0615_caution_names_the_trade_date(self):
        sold = _row("2026-10-22", "ZZC.TO", -20, 9.0, acct="tfsa",
                    settle="2026-10-23")
        r = self._row_of(self._TAX, [self._BUY, sold])
        self.assertEqual(r["category"], "CAUTION", r["advisory"])
        self.assertIn("SHELTERED 'tfsa' bought 2026-10-15 but holds none",
                      r["advisory"])
        self.assertNotIn("2026-10-16", r["advisory"])

    @rule(R.CA_PLAN_01)
    def test_tj0615_locked_and_clear_name_the_trade_date(self):
        r = self._row_of(self._TAX, [self._BUY])
        self.assertEqual(r["category"], "LOCKED", r["advisory"])
        self.assertIn("'tfsa' bought 2026-10-15 and still holds 20",
                      r["advisory"])
        # The clear date still counts from the settlement (10-16).
        self.assertEqual(r["clears_at"], "2026-11-13")
        r = self._row_of([_row("2026-01-07", "OTH.TO", 1, 1.0)],
                         [self._BUY])
        self.assertEqual(r["category"], "CLEAR", r["advisory"])
        self.assertIn("('tfsa' bought it 2026-10-15)", r["advisory"])
        self.assertEqual(r["sheltered_buy"],
                         {"account": "tfsa", "date": "2026-10-15"})

    @rule(R.CA_PLAN_01)
    def test_tj0615_a_taxable_buy_too(self):
        buy = _row("2026-10-15", "ZZC.TO", 10, 9.0, settle="2026-10-16")
        r = self._row_of(self._TAX + [buy], None)
        self.assertEqual(r["category"], "EXITABLE", r["advisory"])
        self.assertIn("Recent buy in 'margin' on 2026-10-15.",
                      r["advisory"])
        self.assertEqual(r["recent_buy"]["date"], "2026-10-15")


if __name__ == "__main__":
    unittest.main()
