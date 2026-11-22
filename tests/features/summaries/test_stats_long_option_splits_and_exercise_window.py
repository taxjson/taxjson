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
    """#31: a plain split of a long option is applied once.

    Formerly: test_fix_batch_c_options.LongSplitTest"""

    def test_plain_split_of_long_then_over_sell(self):
        rows = [row("BUYSELL", "2025-01-02", OPT, 1, 100),
                row("SPLIT", "2025-02-03", OPT, 2),
                row("BUYSELL", "2025-03-03", OPT, -3, 300),
                row("BUYSELL", "2025-04-01", OPT, 1, 50)]
        tr = written(rows)["trades"]
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["kind"], "bought back")
        self.assertAlmostEqual(tr[0]["pnl"], 50.0)


class RenameIntoOpenSeriesTest(unittest.TestCase):
    """#36: renaming a series into one with open writes keeps FIFO by
    write date.

    Formerly: test_fix_batch_c_options.RenameIntoOpenSeriesTest"""

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
    """#67: exercised long options are counted in the window only.

    Formerly: test_fix_batch_c_options.ExerciseWindowTest"""

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


if __name__ == "__main__":
    unittest.main()
