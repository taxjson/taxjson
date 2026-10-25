"""Batch D (options / crypto sends / ALLOWLOSS): `tjs option-yield`
(lib/option_yield.py), the fair value of a crypto send
(lib/crypto_sends.py) and the sales an ALLOWLOSS line names
(lib/loss_overrides.py).

2026-10 bug review findings 37, 47, 81 and 88. Synthetic rows only."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from taxjson.bin.taxjson_convert_tt import parse_tt_line
from taxjson.lib import crypto_sends as CS
from taxjson.lib import loss_overrides as LO
from taxjson.lib import option_yield as OY
from taxjson.lib import out

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


class CloseInAnotherCurrencyTest(unittest.TestCase):
    """#37: a close whose currency is not the open's leaves the lot's
    money unknown (never subtracted across currencies) and names the
    row to check."""

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


class RenamedStrikeTest(unittest.TestCase):
    """#81: a lot renamed into another strike's series reads that
    series' strike (and its notional), not the opened one."""

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


class CryptoSendFullPrecisionTest(unittest.TestCase):
    """#47: a send's fair value and .tt proceeds come from the
    full-precision price; six digits only for showing it."""

    SEND = dict(symbol="SYNCOIN", date="2025-01-02", time="09:30:00",
                quantity=-100, price=123.456789, currency="CAD",
                exchange="coinbase")

    def test_value_and_line(self):
        fv = CS.fair_value(self.SEND, CS.Rates({}, "CAD"), None)
        self.assertAlmostEqual(fv["value"], 12345.68)
        line = CS.tt_line(self.SEND, fv, "CAD")
        self.assertEqual(line, "BUYSELL 2025-01-02 09:30:00 SYNCOIN -100 "
                               "CAD 123.457 12345.68 0")
        # The .tt reader's typo check (shown price x units, 1%) takes it.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = parse_tt_line(line, "coinbase", "crypto_sends.tt:5")
        self.assertNotIn("differs from", err.getvalue())
        self.assertAlmostEqual(tx["net_amount"], 12345.68)

    def test_line_written_before_is_kept(self):
        fv = CS.fair_value(self.SEND, CS.Rates({}, "CAD"), None)
        new = CS.tt_line(self.SEND, fv, "CAD")
        old = new.replace("12345.68", "12345.70")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / CS.TT_NAME
            p.write_text(CS.GENERATED_MARK + "\n" + old + "\n",
                         encoding="utf-8")
            e = {"id": "cb-1", "tt": new}
            CS.keep_written_lines([e], p)
            self.assertEqual(e["tt"], old)
            # A changed price is a changed line: rewritten.
            other = {"id": "cb-2", "tt": new.replace("123.457", "120")}
            want = other["tt"]
            CS.keep_written_lines([other], p)
            self.assertEqual(other["tt"], want)


class AllowLossWholeSaleTest(unittest.TestCase):
    """#88: a line names a whole sale or none, its units over every
    fill of the sale, whichever day each settles."""

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
