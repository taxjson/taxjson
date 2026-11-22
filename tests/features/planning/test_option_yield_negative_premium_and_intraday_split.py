"""Batch C (options): the option statistics behind `tjs stats` /
`tjs win-rates` (lib/trade_stats.py) and `tjs option-yield`
(lib/option_yield.py). Planning views, never a filing figure.

2026-10 bug review findings 31, 32, 33, 34, 35, 36, 67 and 80.
Synthetic rows only.

Formerly: test_fix_batch_c_options"""
import unittest

from taxjson.lib import option_yield as OY
from taxjson.lib import trade_stats as TS

OPT = "Q260116C00050000.TO"


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


class NegativePremiumTest(unittest.TestCase):
    """#34: a write netting less than its commission is a loss.

    Formerly: test_fix_batch_c_options.NegativePremiumTest"""

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


class IntradaySplitTest(unittest.TestCase):
    """#80: a split stamped after the open on its own day re-denominates
    the lot once.

    Formerly: test_fix_batch_c_options.IntradaySplitTest"""

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
