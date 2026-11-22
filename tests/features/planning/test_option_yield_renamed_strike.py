"""Batch D (options / crypto sends / ALLOWLOSS): `tjs option-yield`
(lib/option_yield.py), the fair value of a crypto send
(lib/crypto_sends.py) and the sales an ALLOWLOSS line names
(lib/loss_overrides.py).

2026-10 bug review findings 37, 47, 81 and 88. Synthetic rows only.

Formerly: test_fix_batch_d_options"""
import unittest
from taxjson.lib import option_yield as OY

SAMP = "SAMP250620C00010000.US"
SAMP8 = "SAMP250620C00008000.US"
Q50 = "Q260116C00050000.TO"
Q40 = "Q260116C00040000.TO"


def row(action, day, sym, q=0, net=0, time="09:30:00", cur="USD", **kw):
    r = dict(action=action, date=day, date_settle=day, time=time,
             symbol=sym, quantity=q, net_amount=net, currency=cur,
             account="margin")
    r.update(kw)
    return r


def lots(rows):
    return OY.contracts([("margin", t) for t in rows])


def year(rows, start="2025-01-01", end="2025-12-31"):
    return OY.analyze(lots(rows), start=start, end=end, scope="all")


class RenamedStrikeTest(unittest.TestCase):
    """#81: a lot renamed into another strike's series reads that
    series' strike (and its notional), not the opened one.

    Formerly: test_fix_batch_d_options.RenamedStrikeTest"""

    def test_direct_rename_takes_the_new_strike(self):
        lot, = lots([row("BUYSELL", "2025-02-03", SAMP, 1, 100),
                     row("SPLIT", "2025-02-04", SAMP, 1, symbol_new=SAMP8)])
        self.assertEqual(lot.symbol_on("2025-02-04"), SAMP8)
        self.assertAlmostEqual(lot.strike_on("2025-02-04"), 8.0)
        self.assertAlmostEqual(lot.strike_on("2025-02-03"), 10.0)

    def test_plain_split_keeps_strike_over_factor(self):
        lot, = lots([row("BUYSELL", "2025-02-03", SAMP, 1, 100),
                     row("SPLIT", "2025-02-04", SAMP, 2)])
        self.assertAlmostEqual(lot.strike_on("2025-02-04"), 5.0)
        self.assertAlmostEqual(lot.notional_on("2025-02-04"), 1000.0)

    def test_rbc_option_adjustment_split(self):
        # The shape rbc_direct._emit_option_adjust books for a strike
        # adjustment RBC describes as another contract: SPLIT old ->
        # new series, factor 1.
        adj = dict(action="SPLIT", date="2025-03-03", time="10:00:00",
                   date_settle="2025-03-03", symbol=Q50, symbol_new=Q40,
                   quantity=1.0, currency="CAD", price=0.0, net_amount=0.0,
                   account="margin",
                   description="RBC option adjustment QA1->QA2: synthetic")
        rows = [row("BUYSELL", "2025-01-02", Q50, -1, 100, cur="CAD"), adj]
        doc = year(rows)
        o, = doc["open"]
        self.assertEqual(o["symbol"], Q40)
        self.assertAlmostEqual(o["strike"], 40.0)
        # Naked: the yield is over the strike notional — at 50 for the
        # 60 days before the adjustment (2025-01-02 to 2025-03-03), at
        # 40 for the 304 days after it to the window's end (the whole
        # stretch at 40 revalued the earlier exposure: 2026-10-24 audit
        # A05).
        r, = doc["rows"]
        self.assertEqual(r["yield_basis"], "notional")
        self.assertAlmostEqual(r["avg_notional"],
                               round((50 * 100 * 60 + 40 * 100 * 304) / 365,
                                     2))


if __name__ == "__main__":
    unittest.main()
