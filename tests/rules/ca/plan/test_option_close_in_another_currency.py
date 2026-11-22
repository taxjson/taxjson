"""Batch D (options / crypto sends / ALLOWLOSS): `tjs option-yield`
(lib/option_yield.py), the fair value of a crypto send
(lib/crypto_sends.py) and the sales an ALLOWLOSS line names
(lib/loss_overrides.py).

2026-10 bug review findings 37, 47, 81 and 88. Synthetic rows only.

Formerly: test_fix_batch_d_options"""
import unittest
from taxjson.lib import option_yield as OY
from taxjson.lib import out

SAMP = "SAMP250620C00010000.US"


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


class CloseInAnotherCurrencyTest(unittest.TestCase):
    """#37: a close whose currency is not the open's leaves the lot's
    money unknown (never subtracted across currencies) and names the
    row to check.

    Formerly: test_fix_batch_d_options.CloseInAnotherCurrencyTest"""

    ROWS = [row("BUYSELL", "2025-01-02", SAMP, -1, 100),
            row("BUYSELL", "2025-02-03", SAMP, 1, 50, cur="CAD")]

    def test_lot_money_unknown(self):
        lot, = lots(self.ROWS)
        self.assertEqual(lot.currency, "USD")
        self.assertTrue(lot.unknown_cost)
        self.assertEqual(lot.closes[0].currency, "CAD")

    def test_report_blanks_and_names_the_row(self):
        doc = year(self.ROWS)
        c, = doc["closed"]
        self.assertIsNone(c["premium"])
        self.assertIsNone(c["pnl"])
        self.assertIsNone(c["close_cash"])
        t = doc["totals"]["USD"]
        self.assertIsNone(t["realized"])
        # The CAD buy-back is not taken from the USD premium.
        self.assertAlmostEqual(t["cash_flow"], 100.0)
        self.assertEqual(t["unknown_cost"], 0)      # not missing history
        text = OY.render(doc).text()
        acts = out.act_lines(text)
        self.assertTrue(any("not in the open's currency" in a
                            and "SAMP.US 2025-02-03 CAD" in a
                            and "check that row's currency" in a
                            for a in acts), acts)
        self.assertFalse(any("before the data" in a for a in acts), acts)
        self.assertTrue(all(len(a) <= 100 for a in acts), acts)

    def test_same_currency_close_is_known(self):
        rows = [dict(r, currency="USD") for r in self.ROWS]
        lot, = lots(rows)
        self.assertFalse(lot.unknown_cost)
        self.assertAlmostEqual(year(rows)["totals"]["USD"]["realized"], 50.0)


if __name__ == "__main__":
    unittest.main()
