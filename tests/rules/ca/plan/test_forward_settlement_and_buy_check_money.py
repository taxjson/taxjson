"""The 2026-10-29 fix round, planning commands: buy-check, sell-check,
rebuy, wash-radar, harvest and wash-sales (backlog TJ-0350, TJ-0351,
TJ-0352, TJ-0243, TJ-0245, TJ-0247, TJ-0244, TJ-0246, TJ-0258,
TJ-0259, TJ-0413, TJ-0157). Synthetic data only: made-up tickers
(SYN.TO, ONE.TO, TWO.TO, ZZU.US) in throwaway projects.

Formerly: test_fix_1029_planning"""
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar, _rebuy, _row


class TestForwardViewSettlesTheSale(unittest.TestCase):
    """TJ-0243 (the audit's case TJ-0416): an RRSP bought SYN.TO on
    Wednesday 2026-10-14, settled Thursday 10-15. On Friday 11-13 a
    taxable loss sale settles Monday 11-16, 32 days after the buy: the
    loss stands (the engine allows it). The radar measured the sale from
    the Friday, said LOCKED, "selling before 2026-11-15" (a Sunday:
    10-15 + 31 on the calendar).

    Formerly: test_fix_1029_planning.TestForwardViewSettlesTheSale"""

    _BOOK = [_row("2026-01-05", "SYN.TO", 10, 10)]
    _RRSP = [_row("2026-10-14", "SYN.TO", 10, 5, acct="rrsp",
                  settle="2026-10-15")]

    @rule(R.CA_PLAN_01)
    def test_tj0243_locked_clears_on_the_first_trading_day(self):
        row = _radar("canada", self._BOOK, self._RRSP,
                     as_of="2026-11-12")["SYN.TO"]
        self.assertEqual(row["category"], "LOCKED", row["advisory"])
        # Friday's sale settles Monday 11-16, past 10-15 + 30.
        self.assertEqual(row["clears_at"], "2026-11-13")
        self.assertIn("at a loss before 2026-11-13", row["advisory"])

    @rule(R.CA_PLAN_01)
    def test_tj0243_a_friday_sale_settles_monday(self):
        for d in ("2026-11-13", "2026-11-14", "2026-11-16"):
            with self.subTest(as_of=d):
                row = _radar("canada", self._BOOK, self._RRSP,
                             as_of=d)["SYN.TO"]
                self.assertNotEqual(row["category"], "LOCKED",
                                    row["advisory"])

    @rule(R.CA_PLAN_01)
    def test_tj0243_exitable_and_a_weekend_end(self):
        # A taxable buy settled 10-15: EXITABLE until Thursday 11-12.
        tax = self._BOOK + [_row("2026-10-14", "SYN.TO", 5, 5,
                                 settle="2026-10-15")]
        row = _radar("canada", tax, as_of="2026-11-12")["SYN.TO"]
        self.assertEqual(row["category"], "EXITABLE", row["advisory"])
        self.assertEqual(row["clears_at"], "2026-11-13")
        row = _radar("canada", tax, as_of="2026-11-13")["SYN.TO"]
        self.assertEqual(row["category"], "CLEAR", row["advisory"])
        # A buy settled Wednesday 10-21: + 30 is Friday 11-20, the
        # trade that settles then is Thursday's; Friday is clear (it
        # was Saturday 11-21).
        rrsp = [_row("2026-10-20", "SYN.TO", 10, 10, acct="rrsp",
                     settle="2026-10-21")]
        row = _radar("canada", self._BOOK, rrsp,
                     as_of="2026-10-20")["SYN.TO"]
        self.assertEqual((row["category"], row["clears_at"]),
                         ("LOCKED", "2026-11-20"))

    @rule(R.US_PLAN_01)
    def test_tj0243_us_clears_on_a_trading_day(self):
        # An IRA bought SYN.US on Thursday 2026-10-15: + 31 is Sunday
        # 11-15; the first trading day from it is Monday 11-16.
        book = [_row("2026-01-05", "SYN.US", 10, 10, currency="USD")]
        ira = [_row("2026-10-15", "SYN.US", 10, 5, acct="ira",
                    currency="USD")]
        row = _radar("usa", book, ira, as_of="2026-11-13")["SYN.US"]
        self.assertEqual((row["category"], row["clears_at"]),
                         ("LOCKED", "2026-11-16"))
        row = _radar("usa", book, ira, as_of="2026-11-16")["SYN.US"]
        self.assertNotEqual(row["category"], "LOCKED", row["advisory"])


class TestShelteredBuyMadeToday(unittest.TestCase):
    """TJ-0245 (the audit's case TJ-0415): as of 2026-10-20 an RRSP
    bought SYN.TO today, settling 10-21, and no taxable account holds
    it. The row said only "CLEAR: Held only in SHELTERED accounts",
    `sheltered_buy` null, while the settled-day case names the buy.

    Formerly: test_fix_1029_planning.TestShelteredBuyMadeToday"""

    @rule(R.CA_PLAN_01)
    def test_tj0245_the_buy_made_today_is_named(self):
        rrsp = [_row("2026-10-20", "SYN.TO", 10, 10, acct="rrsp",
                     settle="2026-10-21")]
        other = [_row("2026-01-05", "OTHER.TO", 10, 10)]
        row = _radar("canada", other, rrsp, as_of="2026-10-20")["SYN.TO"]
        self.assertEqual(row["category"], "CLEAR", row["advisory"])
        self.assertEqual(row["sheltered_buy"]["account"], "rrsp")
        self.assertIn("that purchase can make a taxable loss",
                      row["advisory"])


class TestBuyCheckMoney(unittest.TestCase):
    """TJ-0244: buy-check's per-unit share of a recent loss printed
    "about $10.00 of it per unit" while the radar's advisory beside it
    said "10.00 CAD of it for each unit" (TJ-0121).

    Formerly: test_fix_1029_planning.TestBuyCheckMoney"""

    def _per_unit(self, usa):
        from _support.planning_radar import _check
        cur, sym = ("USD", "ZZA.US") if usa else ("CAD", "ZZA.TO")
        book = [_row("2026-06-01", sym, 200, 20.0, currency=cur),
                _row("2026-10-14", sym, -100, 10.0, currency=cur)]
        radar = _radar("usa" if usa else "canada", book, as_of="2026-10-25")
        self.assertEqual(radar[sym]["category"], "BLOCKED",
                         radar[sym]["advisory"])
        out, code = _check("cmd_buy_check", radar, [sym], usa=usa)
        self.assertEqual(code, 1, out)
        return " ".join(out.split())

    @rule(R.CA_PLAN_01)
    def test_tj0244_canada(self):
        out = self._per_unit(False)
        self.assertIn("about 10.00 CAD of it per unit", out)
        self.assertNotIn("$", out)

    @rule(R.US_PLAN_01)
    def test_tj0244_usa(self):
        out = self._per_unit(True)
        self.assertIn("about 10.00 USD of it per unit", out)
        self.assertNotIn("$", out)


class TestRebuyKeepsAClosedFinalDenial(unittest.TestCase):
    """TJ-0247: the TJ-0079 book (ZZJ.TO losses on 06-01 and 06-09, 30
    bought back 06-12) as of Friday 2026-07-03: the 06-01 loss is denied
    for good (its rescue date 06-30 passed) and its re-entry date 07-02
    has passed too. rebuy showed only the 06-09 loss, and "sell it by
    07-09 to keep the loss", the final denial gone without a word.

    Formerly: test_fix_1029_planning.TestRebuyKeepsAClosedFinalDenial"""

    _BOOK = [_row("2026-01-08", "ZZJ.TO", 100, 20.0),
             _row("2026-06-01", "ZZJ.TO", -50, 15.0, settle="2026-06-02"),
             _row("2026-06-09", "ZZJ.TO", -50, 14.0, settle="2026-06-10"),
             _row("2026-06-12", "ZZJ.TO", 30, 14.0, settle="2026-06-15")]

    @rule(R.CA_PLAN_05)
    def test_tj0247_the_final_denial_is_named(self):
        from taxjson.lib import rebuy as RB
        radar = _radar("canada", self._BOOK, as_of="2026-07-03")
        doc = _rebuy(radar, "2026-07-03")
        row, = doc["rows"]
        self.assertEqual((row["status"], row["sold_on"]),
                         ("rebought", "2026-06-09"))
        self.assertEqual(row["final"], ["2026-06-01"])
        self.assertEqual([l["trade_date"] for l in row["denied_closed"]],
                         ["2026-06-01"])
        acts = " ".join(RB._acts(doc, "canada"))
        self.assertIn("sell it by 2026-07-09 to keep the 2026-06-09 loss",
                      acts)
        self.assertIn("1 loss(es) already denied (ZZJ.TO)", acts)
        text = " ".join(RB._row_detail(row, False).split())
        self.assertIn("Also 2026-06-01 margin 50 units, loss 250.00, 30 "
                      "units denied for good (the last trade date to "
                      "rescue it, 2026-06-30, has passed); past its "
                      "re-entry date, 2026-07-02, nothing changes it.",
                      text)


if __name__ == "__main__":
    unittest.main()
