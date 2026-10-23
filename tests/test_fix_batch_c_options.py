"""Batch C (options): the option statistics behind `tjs stats` /
`tjs win-rates` (lib/trade_stats.py) and `tjs option-yield`
(lib/option_yield.py). Planning views, never a filing figure.

2026-10 bug review findings 31, 32, 33, 34, 35, 36, 67 and 80.
Synthetic rows only."""
import unittest

from taxjson.lib import option_yield as OY
from taxjson.lib import trade_stats as TS

OPT = "Q260116C00050000.TO"
OPT2 = "R260116C00050000.TO"


def row(action, day, sym, q=0, net=0, time="09:30:00", **kw):
    r = dict(action=action, date=day, date_settle=day, time=time,
             symbol=sym, quantity=q, net_amount=net, currency="CAD",
             account="margin")
    r.update(kw)
    return r


def written(rows):
    return TS.written_option_trades(rows, lambda t: t["date"])


def lots(rows):
    return OY.contracts([("margin", t) for t in rows])


class LongSplitTest(unittest.TestCase):
    """#31: a plain split of a long option is applied once."""

    def test_plain_split_of_long_then_over_sell(self):
        rows = [row("BUYSELL", "2025-01-02", OPT, 1, 100),
                row("SPLIT", "2025-02-03", OPT, 2),
                row("BUYSELL", "2025-03-03", OPT, -3, 300),
                row("BUYSELL", "2025-04-01", OPT, 1, 50)]
        tr = written(rows)["trades"]
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["kind"], "bought back")
        self.assertAlmostEqual(tr[0]["pnl"], 50.0)


class CreditSplitRenameTest(unittest.TestCase):
    """#32 / #33: a put's assignment credit follows the underlying's
    split and rename."""

    CR = [dict(account="margin", underlying="AAA.TO", right="P",
               shares=10, amount=100, date="2025-01-02")]

    def test_split_scales_the_credit(self):
        rows = [row("SPLIT", "2025-02-03", "AAA.TO", 2),
                row("BUYSELL", "2025-03-03", "AAA.TO", -10, 300, id="sale")]
        got = TS.credits_by_sale(rows, [dict(c) for c in self.CR])
        self.assertEqual(set(got), {"sale"})
        self.assertAlmostEqual(got["sale"], 50.0)

    def test_rename_moves_the_credit(self):
        rows = [row("SPLIT", "2025-02-03", "AAA.TO", 1, symbol_new="BBB.TO"),
                row("BUYSELL", "2025-03-03", "BBB.TO", -10, 300, id="sale")]
        got = TS.credits_by_sale(rows, [dict(c) for c in self.CR])
        self.assertEqual(set(got), {"sale"})
        self.assertAlmostEqual(got["sale"], 100.0)

    def test_split_before_assignment_does_not_scale(self):
        cr = [dict(self.CR[0], date="2025-03-01")]
        rows = [row("SPLIT", "2025-02-03", "AAA.TO", 2),
                row("BUYSELL", "2025-03-03", "AAA.TO", -10, 300, id="sale")]
        got = TS.credits_by_sale(rows, cr)
        self.assertAlmostEqual(got["sale"], 100.0)


class NegativePremiumTest(unittest.TestCase):
    """#34: a write netting less than its commission is a loss."""

    ROWS = [row("BUYSELL", "2025-01-02", OPT, -1, -5, price=0.05, fee=10),
            row("BUYSELL", "2025-04-01", OPT, 1, 0, price=0)]

    def test_stats(self):
        tr = written([dict(r) for r in self.ROWS])["trades"]
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["kind"], "expired")
        self.assertAlmostEqual(tr[0]["pnl"], -5.0)

    def test_option_yield(self):
        got = lots([dict(r) for r in self.ROWS])
        self.assertAlmostEqual(got[0].cash_open, -5.0)
        self.assertAlmostEqual(got[0].closes[0].pnl, -5.0)

    def test_negative_buy_back_amount_still_paid(self):
        # The R03 convention holds: a buy-back's amount is a magnitude.
        rows = [row("BUYSELL", "2025-01-02", OPT, -1, 100),
                row("BUYSELL", "2025-02-03", OPT, 1, -40)]
        self.assertAlmostEqual(written(rows)["trades"][0]["pnl"], 60.0)
        self.assertAlmostEqual(lots(rows)[0].closes[0].pnl, 60.0)


class CallCreditLegTest(unittest.TestCase):
    """#35: a call's credit comes out of its own share leg, not an
    unrelated sale a few days earlier."""

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
        rows = [row("BUYSELL", "2025-02-01", "AAA.TO", -40, 200,
                    price=5, id="near")]
        self.assertEqual(TS.credits_by_sale(rows, self.credit()),
                         {"near": 40.0})

    def test_credits_carry_strike_and_delivered(self):
        sym = "AAA250321C00050000.TO"
        rows = [row("BUYSELL", "2025-01-02", sym, -2, 300),
                row("ASSIGN", "2025-02-03", sym, 2, 0)]
        cr = written(rows)["credits"]
        self.assertEqual([(c["strike"], c["delivered"]) for c in cr],
                         [(50.0, 200.0)])


class RenameIntoOpenSeriesTest(unittest.TestCase):
    """#36: renaming a series into one with open writes keeps FIFO by
    write date."""

    ROWS = [row("BUYSELL", "2025-01-02", OPT, -1, 100),
            row("BUYSELL", "2025-01-15", OPT2, -1, 200),
            row("SPLIT", "2025-02-03", OPT, 1, symbol_new=OPT2),
            row("BUYSELL", "2025-03-03", OPT2, 1, 50)]

    def test_stats(self):
        tr = written([dict(r) for r in self.ROWS])["trades"]
        self.assertEqual([(t["written"], t["pnl"]) for t in tr],
                         [("2025-01-02", 50.0)])

    def test_option_yield(self):
        got = lots([dict(r) for r in self.ROWS])
        by_open = {l.open_date: l for l in got}
        self.assertEqual([c.pnl for c in by_open["2025-01-02"].closes],
                         [50.0])
        self.assertEqual(by_open["2025-01-15"].closes, [])
        self.assertAlmostEqual(by_open["2025-01-15"].units_open_on(
            "2025-03-03"), 1.0)


class ExerciseWindowTest(unittest.TestCase):
    """#67: exercised long options are counted in the window only."""

    SYM = "SAMP250620C00010000"

    def rows(self):
        return [dict(action=a, date=d, date_settle=d, symbol=self.SYM,
                     quantity=q, net_amount=n, account="margin")
                for a, d, q, n in [("BUYSELL", "2024-01-02", 1, 100),
                                   ("ASSIGN", "2024-06-03", -1, 0)]]

    def test_outside_the_window(self):
        r = TS.closed_trades({}, {"margin": self.rows()},
                             lambda d: d.startswith("2025"),
                             lambda t: t["date"])
        self.assertEqual(r["exercised_long_options"], 0)

    def test_inside_the_window(self):
        r = TS.closed_trades({}, {"margin": self.rows()},
                             lambda d: d.startswith("2024"),
                             lambda t: t["date"])
        self.assertEqual(r["exercised_long_options"], 1)

    def test_outside_the_books(self):
        r = TS.closed_trades({}, {"margin": self.rows()},
                             lambda d: True, lambda t: t["date"],
                             in_books=lambda t: t["date"] >= "2025")
        self.assertEqual(r["exercised_long_options"], 0)


class IntradaySplitTest(unittest.TestCase):
    """#80: a split stamped after the open on its own day re-denominates
    the lot once."""

    SYM = "SAMP250620C00010000.US"

    def opt(self, action, day, q, net, time):
        return ("margin", dict(action=action, date=day, time=time,
                               quantity=q, net_amount=net, symbol=self.SYM,
                               currency="USD", account="margin"))

    def test_split_after_the_open(self):
        got = OY.contracts([
            self.opt("BUYSELL", "2025-02-03", 1, 100, "09:00:00"),
            self.opt("SPLIT", "2025-02-03", 2, 0, "12:00:00"),
            self.opt("BUYSELL", "2025-02-03", -2, 120, "13:00:00")])
        self.assertAlmostEqual(sum(c.pnl for l in got for c in l.closes),
                               20.0)
        self.assertAlmostEqual(got[0].units_open_on("2025-02-03"), 0.0)
        self.assertAlmostEqual(got[0].units_on("2025-02-03"), 2.0)

    def test_close_before_a_same_day_split(self):
        got = OY.contracts([
            self.opt("BUYSELL", "2025-02-03", 2, 200, "09:00:00"),
            self.opt("BUYSELL", "2025-02-03", -1, 150, "10:00:00"),
            self.opt("SPLIT", "2025-02-03", 2, 0, "12:00:00"),
            self.opt("BUYSELL", "2025-02-03", -2, 120, "13:00:00")])
        lot = got[0]
        self.assertAlmostEqual(lot.units_on("2025-02-03"), 4.0)
        self.assertEqual([lot.close_units_on(c, "2025-02-03")
                          for c in lot.closes], [2.0, 2.0])
        self.assertAlmostEqual(lot.units_open_on("2025-02-03"), 0.0)
        self.assertAlmostEqual(sum(c.pnl for c in lot.closes), 70.0)

    def test_split_before_the_open_never_reaches_the_lot(self):
        got = OY.contracts([
            self.opt("SPLIT", "2025-02-03", 2, 0, "08:00:00"),
            self.opt("BUYSELL", "2025-02-03", 1, 100, "09:00:00")])
        self.assertAlmostEqual(got[0].units_on("2025-02-03"), 1.0)


if __name__ == "__main__":
    unittest.main()
