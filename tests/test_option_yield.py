"""`tjs option-yield`: an underlying's option trades over a window
as money, and the yield on the strike notional (CA-PLAN-07 /
US-PLAN-08). Planning numbers only. Synthetic books and projects only."""
import json
import tempfile
import unittest

from tax_rules import rule

from taxjson.lib import option_yield as OY
from _qa_project import project as qa_project, tj
from _style import assert_legend_first, assert_styled
from _style import project as style_project

_W = {"TAXJSON_WIDTH": "100"}
CALL = "ZZQ250418C00500000.US"      # strike 500, expiry 2025-04-18
PUT = "ZZQ250620P00450000.US"       # strike 450, expiry 2025-06-20
CALL2 = "ZZQ260220C00520000.US"   # expiry 2026-02-20


def _r(d, sym, qty, price, net=None, *, action="BUYSELL", acct="margin",
       cur="USD", **kw):
    r = {"action": action, "date": d, "time": "10:00:00", "symbol": sym,
         "quantity": float(qty), "price": float(price),
         "net_amount": (abs(qty) * price * 100.0 if net is None
                        else float(net)),
         "currency": cur, "multiplier": 100.0, "account": acct}
    r.update(kw)
    return (acct, r)


def _doc(rows, start="2025-01-01", end="2025-12-31", **kw):
    return OY.analyze(OY.contracts(rows), start=start, end=end,
                      scope="tax year 2025", **kw)


class TestMatching(unittest.TestCase):
    def test_written_call_expires(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-04-18", CALL, 1, 0.0, 0.0)]
        lots = OY.contracts(rows)
        self.assertEqual(len(lots), 1)
        lot = lots[0]
        self.assertEqual((lot.side, lot.units, lot.cash_open, lot.strike,
                          lot.expiry, lot.underlying),
                         (OY.WRITTEN, 1.0, 400.0, 500.0, "2025-04-18",
                          "ZZQ.US"))
        self.assertEqual([(c.kind, c.units, c.cash, c.pnl)
                          for c in lot.closes],
                         [(OY.EXPIRED, 1.0, 0.0, 400.0)])
        self.assertEqual(lot.notional, 50000.0)

    def test_written_put_bought_back_at_a_loss(self):
        rows = [_r("2025-03-01", PUT, -2, 3.0),
                _r("2025-03-20", PUT, 2, 5.0)]
        lot = OY.contracts(rows)[0]
        self.assertEqual(lot.cash_open, 600.0)
        c = lot.closes[0]
        self.assertEqual((c.kind, c.units, c.cash, c.pnl),
                         (OY.BOUGHT_BACK, 2.0, -1000.0, -400.0))

    def test_tt_books_negative_buy_back_reads_the_same(self):
        # A .tt book writes money out as a negative amount (A2-1111).
        rows = [_r("2025-03-01", PUT, -2, 3.0),
                _r("2025-03-20", PUT, 2, 5.0, -1000.0)]
        self.assertEqual(OY.contracts(rows)[0].closes[0].pnl, -400.0)

    def test_bought_call_sold_exercised_and_expired(self):
        rows = [_r("2025-02-01", CALL, 3, 2.0),
                _r("2025-02-15", CALL, -1, 3.5),
                _r("2025-03-01", CALL, -1, 0.0, 0.0, action="ASSIGN"),
                _r("2025-04-18", CALL, -1, 0.0, 0.0)]
        lot = OY.contracts(rows)[0]
        self.assertEqual((lot.side, lot.cash_open), (OY.BOUGHT, -600.0))
        self.assertEqual([(c.kind, c.pnl) for c in lot.closes],
                         [(OY.SOLD, 150.0), (OY.EXERCISED, -200.0),
                          (OY.EXPIRED, -200.0)])

    def test_assignment_of_a_written_contract(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-04-18", CALL, 1, 0.0, 0.0, action="ASSIGN")]
        c = OY.contracts(rows)[0].closes[0]
        self.assertEqual((c.kind, c.pnl), (OY.ASSIGNED, 400.0))

    def test_fifo_per_account_and_series(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-03-05", CALL, -1, 5.0),
                _r("2025-03-01", CALL, -1, 1.0, acct="tfsa"),
                _r("2025-03-10", CALL, 1, 2.0),
                _r("2025-03-11", CALL, 1, 2.0, acct="tfsa")]
        lots = OY.contracts(rows)
        self.assertEqual([(l.account, l.cash_open,
                           [c.pnl for c in l.closes]) for l in lots],
                         [("margin", 400.0, [200.0]), ("tfsa", 100.0, [-100.0]),
                          ("margin", 500.0, [])])

    def test_a_trade_that_closes_and_opens(self):
        # Short 1, buy 3: 1 closes, 2 open long.
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-03-10", CALL, 3, 2.0)]
        lots = OY.contracts(rows)
        self.assertEqual([(l.side, l.units, l.cash_open) for l in lots],
                         [(OY.WRITTEN, 1.0, 400.0), (OY.BOUGHT, 2.0, -400.0)])
        self.assertEqual(lots[0].closes[0].cash, -200.0)

    def test_split_renames_and_redenominates(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                ("margin", {"action": "SPLIT", "date": "2025-03-15",
                            "time": "00:00:00", "symbol": CALL,
                            "symbol_new": "ZZR250418C00250000.US",
                            "quantity": 2.0, "account": "margin"}),
                _r("2025-04-18", "ZZR250418C00250000.US", 2, 0.0, 0.0)]
        lot = OY.contracts(rows)[0]
        # As opened, and as it stood after the split (a later split
        # never rewrites an earlier window: audit R10).
        self.assertEqual((lot.symbol, lot.units, lot.strike, lot.notional),
                         (CALL, 1.0, 500.0, 50000.0))
        e = "2025-04-18"
        self.assertEqual((lot.symbol_on(e), lot.units_on(e), lot.strike_on(e),
                          lot.underlying_on(e)),
                         ("ZZR250418C00250000.US", 2.0, 250.0, "ZZR.US"))
        self.assertEqual((lot.symbol_on("2025-03-14"),
                          lot.units_on("2025-03-14")), (CALL, 1.0))
        self.assertEqual(lot.closes[0].pnl, 400.0)
        # The renamed series answers to its new underlying.
        doc = _doc(rows, underlyings=["ZZR"])
        self.assertEqual([(r["underlying"], r["realized"])
                          for r in doc["rows"]], [("ZZR.US", 400.0)])
        self.assertEqual(doc["unmatched_underlyings"], [])

    def test_opening_before_the_data_is_unknown(self):
        rows = [("margin", {"action": "OPENING_BALANCE", "date": "2025-01-01",
                            "symbol": CALL, "quantity": -1.0,
                            "account": "margin", "currency": "USD"}),
                _r("2025-02-01", CALL, 1, 1.0)]
        lot = OY.contracts(rows)[0]
        self.assertTrue(lot.unknown_cost)
        self.assertEqual((lot.side, lot.closes[0].cash), (OY.WRITTEN, -100.0))

    def test_underlying_matching(self):
        self.assertTrue(OY.underlying_matches("ZZQ.US", ["zzq"]))
        self.assertTrue(OY.underlying_matches("ZZQ.US", ["ZZQ.US"]))
        self.assertFalse(OY.underlying_matches("ZZQ.US", ["ZZQ.TO"]))
        self.assertFalse(OY.underlying_matches("ZZQX.US", ["ZZQ"]))


class TestAnalyze(unittest.TestCase):
    @rule("CA-PLAN-07")
    def test_window_money_and_yield(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-04-18", CALL, 1, 0.0, 0.0)]
        doc = _doc(rows)
        r = doc["rows"][0]
        self.assertEqual((r["underlying"], r["currency"]), ("ZZQ.US", "USD"))
        self.assertEqual((r["written_contracts"], r["received"], r["paid"],
                          r["realized"], r["closed_contracts"], r["wins"],
                          r["open_contracts"], r["cash_flow"]),
                         (1.0, 400.0, 0.0, 400.0, 1.0, 1.0, 0.0, 400.0))
        self.assertEqual(r["outcomes"], {OY.EXPIRED: 1.0})
        # 48 days open of a 365-day window on a 50,000 notional.
        self.assertEqual(doc["days"], 365)
        # No shares held: the yield is on the strike notional.
        self.assertEqual(r["yield_basis"], "notional")
        self.assertAlmostEqual(r["avg_notional"], 50000.0 * 48 / 365, 2)
        self.assertAlmostEqual(r["yield"], 400.0 / r["avg_notional"], 5)
        self.assertAlmostEqual(r["annualized"], r["yield"], 6)
        self.assertEqual(doc["totals"]["USD"]["realized"], 400.0)

    @rule("CA-PLAN-07")
    def test_yield_on_the_shares_held(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-04-18", CALL, 1, 0.0, 0.0),
                _r("2025-03-01", CALL, -1, 1.0, acct="tfsa")]
        shares = {("margin", "ZZQ.US"): (550.0, 220000.0),
                  ("tfsa", "ZZQ.US"): (100.0, 40000.0),
                  ("margin", "ZZX.US"): (10.0, 100.0)}
        doc = _doc(rows, start="2025-01-01", end="2025-06-30", shares=shares)
        r = doc["rows"][0]
        self.assertEqual((r["yield_basis"], r["shares_held"], r["shares_cost"]),
                         ("shares", 650.0, 260000.0))
        self.assertAlmostEqual(r["yield"], 400.0 / 260000.0, 5)
        self.assertAlmostEqual(r["annualized"], r["yield"] * 365 / 181, 5)
        by = {(p["account"]): p for p in doc["parts"]}
        self.assertEqual(by["tfsa"]["shares_held"], 100.0)
        self.assertAlmostEqual(by["margin"]["yield"], 400.0 / 220000.0, 5)
        t = doc["totals"]["USD"]
        self.assertEqual((t["shares_held"], t["shares_cost"]),
                         (650.0, 260000.0))
        # The account filter keeps that account's shares only.
        one = _doc(rows, accounts=["tfsa"], shares=shares)
        self.assertEqual(one["rows"][0]["shares_held"], 100.0)

    @rule("US-PLAN-08")
    def test_contract_opened_before_the_window_closes_in_it(self):
        rows = [_r("2024-12-01", CALL, -1, 4.0),
                _r("2025-01-20", CALL, 1, 1.0),
                _r("2025-12-20", CALL2, -1, 2.0)]
        doc = _doc(rows)
        r = doc["rows"][0]
        # The December write is not a write of the window; its close is.
        self.assertEqual((r["written_contracts"], r["received"], r["paid"],
                          r["realized"], r["cash_flow"]),
                         (1.0, 200.0, 100.0, 300.0, 100.0))
        # The write of Dec 20 is still open at the window's end.
        self.assertEqual((r["open_contracts"], r["open_cash"]), (1.0, 200.0))
        # Notional days: 19 days of the old contract + 12 of the new.
        self.assertAlmostEqual(r["avg_notional"],
                               (50000.0 * 19 + 52000.0 * 12) / 365, 2)
        self.assertEqual(doc["closed"][0]["how"], OY.BOUGHT_BACK)
        self.assertEqual(doc["closed"][0]["days_open"], 50)

    def test_filters(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-03-01", "ZZX250418P00010000.US", -1, 1.0,
                   acct="tfsa"),
                _r("2025-03-01", "ZZX250418P00010000.US", 1, 0.5,
                   acct="tfsa")]
        doc = _doc(rows, underlyings=["zzq"])
        self.assertEqual([r["underlying"] for r in doc["rows"]], ["ZZQ.US"])
        doc = _doc(rows, accounts=["tfsa"], groups={"tfsa": "sheltered"})
        self.assertEqual([r["underlying"] for r in doc["rows"]], ["ZZX.US"])
        self.assertEqual(doc["parts"][0]["group"], "sheltered")
        doc = _doc(rows, underlyings=["ZZX", "NOPE"])
        self.assertEqual(doc["unmatched_underlyings"], ["NOPE"])
        # Nothing of the window (the ZZQ call, never closed, is still
        # open in 2026: a window before any trade is empty).
        doc = _doc(rows, start="2024-01-01", end="2024-12-31")
        self.assertEqual(doc["rows"], [])
        doc = _doc(rows, start="2026-01-01", end="2026-12-31")
        self.assertEqual([(r["underlying"], r["open_contracts"])
                          for r in doc["rows"]], [("ZZQ.US", 1.0)])

    def test_unknown_premium_is_flagged_not_counted(self):
        rows = [("margin", {"action": "OPENING_BALANCE", "date": "2024-06-01",
                            "symbol": CALL, "quantity": -1.0,
                            "account": "margin", "currency": "USD"}),
                _r("2025-02-01", CALL, 1, 1.0)]
        doc = _doc(rows)
        r = doc["rows"][0]
        # No premium, no P/L: not a 100 loss (audit R06).
        self.assertEqual((r["unknown_cost"], r["unknown_closes"],
                          r["realized"], r["win_rate"], r["yield"]),
                         (1, 1.0, None, None, None))
        self.assertEqual((r["paid"], r["cash_flow"]), (100.0, -100.0))
        self.assertIsNone(doc["closed"][0]["premium"])
        self.assertIsNone(doc["totals"]["USD"]["realized"])

    def test_render_closed_then_open_then_summary(self):
        rows = [_r("2025-03-01", CALL, -1, 4.0),
                _r("2025-04-18", CALL, 1, 0.0, 0.0),
                _r("2025-12-20", CALL2, -1, 2.0),
                _r("2025-05-01", PUT, -1, 3.0)]      # expired, no row
        doc = _doc(rows, shares={("margin", "ZZQ.US"): (550.0, 220000.0)})
        self.assertEqual([o["symbol"] for o in doc["open"]], [PUT, CALL2])
        self.assertTrue(doc["open"][0]["expired"])
        text = OY.render(doc).text()
        self.assertIn("OPTION YIELD — planning numbers, tax year 2025",
                      text)
        i_c = text.index("CLOSED      SERIES")
        i_o = text.index("OPEN AT 2025-12-31")
        i_s = text.index("SUMMARY")
        self.assertLess(i_c, i_o)
        self.assertLess(i_o, i_s)
        self.assertIn("expired", text)
        self.assertIn("2025-06-20 (past)", text)
        self.assertIn("1 open contract(s) past expiry", text)
        self.assertIn("on the 220,000.00 cost of 550 share(s) held", text)
        self.assertNotIn("HOW IT IS COMPUTED", text)
        self.assertNotIn("--details", text)
        empty = OY.render(_doc(rows, underlyings=["NOPE"])).text()
        self.assertIn("NOPE: no option on it in the books", empty)


class TestOptionYieldCli(unittest.TestCase):
    @rule("CA-PLAN-07")
    def test_project_end_to_end(self):
        tt = ["BUYSELL 2025-01-02 10:00:00 ZZQ.US 100 USD 480.00 48000",
              "BUYSELL 2025-03-03 10:00:00 ZZQ250418C00500000.US -1 USD "
              "4.00 400",
              "BUYSELL 2025-04-18 16:00:00 ZZQ250418C00500000.US 1 USD "
              "0 0",
              "BUYSELL 2025-05-01 10:00:00 ZZQ250620P00450000.US -1 USD "
              "3.00 300",
              "BUYSELL 2025-05-20 10:00:00 ZZQ250620P00450000.US 1 USD "
              "1.00 100",
              "BUYSELL 2025-06-02 10:00:00 ZZX250919C00020000.US 2 USD "
              "0.50 100"]
        with tempfile.TemporaryDirectory() as tmp:
            root = qa_project(tmp, "p", {"margin/m.tt": "\n".join(tt) + "\n"})
            cfg = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                cfg.replace("year = 2024", "year = 2025").replace(
                    "option_grant_timing_since = 2024",
                    "option_grant_timing_since = 2025"))
            tj(root, "run", "--no-input")
            doc = json.loads(tj(root, "option-yield", "zzq", "2025",
                                "--json").stdout)
            self.assertEqual([r["underlying"] for r in doc["rows"]],
                             ["ZZQ.US"])
            r = doc["rows"][0]
            self.assertEqual((r["written_contracts"], r["received"],
                              r["paid"], r["realized"], r["wins"],
                              r["win_rate"]),
                             (2.0, 700.0, 100.0, 600.0, 2.0, 1.0))
            # The 100 ZZQ shares held (cost 48,000) are the capital.
            self.assertEqual((r["yield_basis"], r["shares_held"],
                              r["shares_cost"]), ("shares", 100.0, 48000.0))
            self.assertAlmostEqual(r["yield"], 600.0 / 48000.0, 6)
            self.assertEqual(r["outcomes"], {"bought back": 1.0,
                                             "expired": 1.0})
            self.assertEqual(doc["unmatched_underlyings"], [])
            # No filter: every underlying; the bought call is open.
            every = json.loads(tj(root, "option-yield", "2025",
                                  "--json").stdout)
            self.assertEqual([r["underlying"] for r in every["rows"]],
                             ["ZZQ.US", "ZZX.US"])
            self.assertEqual(every["rows"][1]["open_contracts"], 2.0)
            # Positionals in any order; the account filter.
            one = json.loads(tj(root, "option-yield", "margin", "ZZQ",
                                "2025", "--json").stdout)
            self.assertEqual(one["totals"]["USD"]["realized"], 600.0)
            text = tj(root, "option-yield", "ZZQ", "2025").stdout
            self.assertIn("CLOSED      SERIES", text)
            self.assertIn("OPEN AT 2025-12-31", text)
            self.assertIn("WIN%", text)
            self.assertIn("(2 at a profit: win rate 100%)", text)
            # Nothing closed: no rate.
            self.assertIsNone(every["rows"][1]["win_rate"])
            bad = tj(root, "option-yield", "2025-13", check=False)
            self.assertNotEqual(bad.returncode, 0)


class TestOptionYieldStyle(unittest.TestCase):
    def test_style_both_countries(self):
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                r = style_project(c).run("option-yield", "all", **_W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_legend_first(self, r.stdout,
                                    "Planning numbers, not filing")
                assert_styled(self, r.stdout)


if __name__ == "__main__":
    unittest.main()
