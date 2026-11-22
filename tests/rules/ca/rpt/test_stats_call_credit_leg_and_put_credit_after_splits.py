"""Batch C (options): the option statistics behind `tjs stats` /
`tjs win-rates` (lib/trade_stats.py) and `tjs option-yield`
(lib/option_yield.py). Planning views, never a filing figure.

2026-10 bug review findings 31, 32, 33, 34, 35, 36, 67 and 80.
Synthetic rows only.

Formerly: test_fix_batch_c_options"""
import unittest

from taxjson.lib import trade_stats as TS


def row(action, day, sym, q=0, net=0, time="09:30:00", **kw):
    r = dict(action=action, date=day, date_settle=day, time=time,
             symbol=sym, quantity=q, net_amount=net, currency="CAD",
             account="margin")
    r.update(kw)
    return r


def written(rows):
    return TS.written_option_trades(rows, lambda t: t["date"])


class CreditSplitRenameTest(unittest.TestCase):
    """#32 / #33: a put's assignment credit follows the underlying's
    split and rename. The put's delivery leg is in the book: a credit
    whose leg the engine never paired stays with the option (2026-10-24
    audit A44, A49).

    Formerly: test_fix_batch_c_options.CreditSplitRenameTest"""

    CR = [dict(account="margin", underlying="AAA.TO", right="P",
               shares=10, amount=100, date="2025-01-02")]
    LEG = row("ASSIGN", "2025-01-02", "AAA.TO", 10, 500, id="delivery")

    def test_split_scales_the_credit(self):
        rows = [dict(self.LEG), row("SPLIT", "2025-02-03", "AAA.TO", 2),
                row("BUYSELL", "2025-03-03", "AAA.TO", -10, 300, id="sale")]
        got = TS.credits_by_sale(rows, [dict(c) for c in self.CR])
        self.assertEqual(set(got), {"sale"})
        self.assertAlmostEqual(got["sale"], 50.0)

    def test_rename_moves_the_credit(self):
        rows = [dict(self.LEG),
                row("SPLIT", "2025-02-03", "AAA.TO", 1, symbol_new="BBB.TO"),
                row("BUYSELL", "2025-03-03", "BBB.TO", -10, 300, id="sale")]
        got = TS.credits_by_sale(rows, [dict(c) for c in self.CR])
        self.assertEqual(set(got), {"sale"})
        self.assertAlmostEqual(got["sale"], 100.0)

    def test_split_before_assignment_does_not_scale(self):
        cr = [dict(self.CR[0], date="2025-03-01")]
        rows = [row("SPLIT", "2025-02-03", "AAA.TO", 2),
                dict(self.LEG, date="2025-03-01", date_settle="2025-03-01"),
                row("BUYSELL", "2025-03-03", "AAA.TO", -10, 300, id="sale")]
        got = TS.credits_by_sale(rows, cr)
        self.assertAlmostEqual(got["sale"], 100.0)


class CallCreditLegTest(unittest.TestCase):
    """#35: a call's credit comes out of its own share leg, not an
    unrelated sale a few days earlier.

    Formerly: test_fix_batch_c_options.CallCreditLegTest"""

    def credit(self, **kw):
        cr = dict(account="margin", underlying="AAA.TO", right="C",
                  shares=100, amount=100, date="2025-02-03", strike=50.0,
                  delivered=100)
        cr.update(kw)
        return [cr]

    def test_marked_leg_wins_over_earlier_sale(self):
        rows = [row("BUYSELL", "2025-01-31", "AAA.TO", -100, 200,
                    price=2, id="ordinary"),
                row("ASSIGN", "2025-02-03", "AAA.TO", -100, 5000,
                    price=50, id="assigned")]
        self.assertEqual(TS.credits_by_sale(rows, self.credit()),
                         {"assigned": 100.0})

    def test_earlier_leg_at_strike_and_sized(self):
        rows = [row("BUYSELL", "2025-01-31", "AAA.TO", -100, 5000,
                    price=50, id="leg"),
                row("BUYSELL", "2025-02-04", "AAA.TO", -100, 6000,
                    price=60, id="later")]
        self.assertEqual(TS.credits_by_sale(rows, self.credit()),
                         {"leg": 100.0})

    def test_no_identified_leg_keeps_the_window(self):
        # An unmarked sale of another size and price two days BEFORE
        # the option row is no leg: the engine's ledger drains an
        # unpaired premium only into a sale from the option row on,
        # within 7 days, else leaves it with the option (2026-10-24
        # audit A44).
        rows = [row("BUYSELL", "2025-02-01", "AAA.TO", -40, 200,
                    price=5, id="near")]
        self.assertEqual(TS.credits_by_sale(rows, self.credit()), {})
        # The same sale on the option's day is the nearest trade in the
        # window and, as the last leg coming, takes the whole premium,
        # as the engine folds it.
        rows = [row("BUYSELL", "2025-02-03", "AAA.TO", -40, 200,
                    price=5, id="near")]
        self.assertEqual(TS.credits_by_sale(rows, self.credit()),
                         {"near": 100.0})
        # Beyond the window: the premium stays with the option.
        rows = [row("BUYSELL", "2025-02-11", "AAA.TO", -100, 5000,
                    price=50, id="late")]
        self.assertEqual(TS.credits_by_sale(rows, self.credit()), {})

    def test_credits_carry_strike_and_delivered(self):
        sym = "AAA250321C00050000.TO"
        rows = [row("BUYSELL", "2025-01-02", sym, -2, 300),
                row("ASSIGN", "2025-02-03", sym, 2, 0),
                # The share leg: without a stock line of AAA.TO in the
                # account the engine's resolver reads the assignment as
                # cash-settled, and there is no credit (audit A44).
                row("ASSIGN", "2025-02-03", "AAA.TO", -200, 10000,
                    price=50, id="leg")]
        cr = written(rows)["credits"]
        self.assertEqual([(c["strike"], c["delivered"]) for c in cr],
                         [(50.0, 200.0)])
        self.assertEqual(written(rows[:2])["credits"], [])
        self.assertTrue(written(rows[:2])["trades"][0]["cash_settled"])


if __name__ == "__main__":
    unittest.main()
