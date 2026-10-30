"""Fix round 2026-10-29 (round 7), group optyield: option-yield, the
LEAPS views, ccd-sum and the report filters.

TJ-0692  `SYN.B` (a class share's bare ticker) matched no option or
         trade of SYN.B.US: the first dot came off, with the class.
TJ-0694  a futures option's x1000 stated on its close alone priced the
         open at the default 100 contract size.
TJ-0774  shares held partly at an unknown cost (an OPENING cost=unknown)
         gave a yield on the known part of their cost; list and shares
         showed that part as the whole.
TJ-0777  a closed unknown-cost long (an OPENING cost=unknown) made the
         LEAPS views read its sale as a write and a later real entry as
         its buy-back: the known LEAPS gain disappeared.
TJ-0778  ccd-sum filtered a grant-timing write's expiry by the write's
         date: an expiry inside a narrow window was not counted.
TJ-0784  option-yield of a tax year not yet begun reported today's
         closes under its label.
TJ-0785  win-rates refused a digit-leading listing (1234.HK) as an
         invalid period.
TJ-0790  an expiry carrying a fee (IB `C;Ep` with a commission) read
         as a buy-back.
TJ-0903  a written call renamed by a SPLIT and bought back in the new
         series counted an extra (inferred) expiry in ccd-sum.
TJ-0907  a series split reported by one blended Canadian account left
         another account's contracts in the old series: leaps /
         leaps-sum ACCOUNT lost the LEAPS gain, option-yield invented
         open contracts.
TJ-0909  an account named `123abc` was refused as an invalid period;
         one named `2025` was silently read as the year.
TJ-0910  option-yield did not find the class shares (SYN.B.TO) behind
         the option root SYNB.

Every fixture is synthetic: invented tickers (SYN*), account names and
amounts.
"""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import option_yield as OY
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _project(td: str, files, *, country="canada", year=2025,
             accounts=None, settings="") -> Path:
    """A project with each account's `.tt` book; `run` done."""
    root = Path(td) / "p"
    accounts = accounts or {"margin": "taxable"}
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    for a, text in files.items():
        name, text = text if isinstance(text, tuple) else ("trades.tt", text)
        (root / "inputs" / a / name).write_text(text)
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n{settings}\n'
        + "".join(f'[accounts."{a}"]\ntype = "{t}"\n'
                  for a, t in accounts.items()))
    (root / "ticker.map").write_text("")
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return root


def _json(root: Path, *args: str) -> dict:
    r = _cli(root, *args, "--json")
    assert r.returncode == 0, (args, r.stdout[-2000:], r.stderr[-2000:])
    return json.loads(r.stdout)


def _r(d, sym, qty, price, net, *, acct="margin", cur="CAD", **kw):
    r = {"action": "BUYSELL", "date": d, "time": "10:00:00", "symbol": sym,
         "quantity": float(qty), "price": float(price),
         "net_amount": float(net), "currency": cur, "account": acct}
    r.update(kw)
    return (acct, r)


# --------------------------------------------------------------------
# The underlying filter (TJ-0692)
# --------------------------------------------------------------------

class TestClassShareFilter(unittest.TestCase):
    """TJ-0692: `SYN.B` names the class share SYN.B.US; `SYN` never
    does (the first dot dropped the class)."""

    def test_bare_class_ticker(self):
        self.assertTrue(OY.underlying_matches("SYN.B.US", ["SYN.B"]))
        self.assertTrue(OY.underlying_matches("SYN.B.US", ["syn.b.us"]))
        self.assertFalse(OY.underlying_matches("SYN.B.US", ["SYN"]))
        self.assertFalse(OY.underlying_matches("SYN.US", ["SYN.B"]))
        # The market suffix alone still comes off.
        self.assertTrue(OY.underlying_matches("SYN.US", ["SYN"]))
        self.assertTrue(OY.underlying_matches("SYN.B.TO", ["SYN.B"]))

    def test_option_yield_selects_the_class_share(self):
        call = "SYNB251219C00002000.US"
        rows = [_r("2025-02-03", call, -1, 0.2, 20, cur="USD",
                   multiplier=100.0),
                _r("2025-03-03", call, 1, 0.1, 10, cur="USD",
                   multiplier=100.0)]
        lots = OY.contracts(rows)
        doc = OY.analyze(lots, start="2025-01-01", end="2025-12-31",
                         scope="tax year 2025", underlyings=["SYN.B"],
                         aliases={"SYNB.US": "SYN.B.US"})
        self.assertEqual([(r["underlying"], r["realized"])
                          for r in doc["rows"]], [("SYN.B.US", 10.0)])
        self.assertEqual(doc["unmatched_underlyings"], [])
        doc = OY.analyze(lots, start="2025-01-01", end="2025-12-31",
                         scope="tax year 2025", underlyings=["SYN"],
                         aliases={"SYNB.US": "SYN.B.US"})
        self.assertEqual(doc["rows"], [])


# --------------------------------------------------------------------
# An expiry with a fee (TJ-0790)
# --------------------------------------------------------------------

class TestExpiryWithAFee(unittest.TestCase):
    """TJ-0790: a call written for 10, expiring at price 0 with a 2
    commission: expired, P/L 8 — not bought back."""
    CALL = "SYN251219C00001000.US"

    def _close(self, **kw):
        close = dict(dict(price=0.0, gross_amount=0.0, proceeds=0.0,
                          multiplier=100.0, cur="USD"), **kw)
        net = close.pop("net")
        rows = [_r("2025-02-03", self.CALL, -1, 0.1, 10, cur="USD",
                   multiplier=100.0),
                _r("2025-12-19", self.CALL, 1, close.pop("price"), net,
                   **close)]
        return OY.contracts(rows)[0].closes[0]

    def test_fee_on_an_expiry(self):
        c = self._close(net=2.0, fee=2.0)
        self.assertEqual((c.kind, c.pnl), (OY.EXPIRED, 8.0))
        c = self._close(net=0.0)
        self.assertEqual((c.kind, c.pnl), (OY.EXPIRED, 10.0))

    def test_money_beyond_the_fee_is_a_buy_back(self):
        # More money than the fee, or a price: a buy-back.
        c = self._close(net=5.0, fee=1.0)
        self.assertEqual(c.kind, OY.BOUGHT_BACK)
        c = self._close(net=5.0, gross_amount=5.0, fee=0.0)
        self.assertEqual(c.kind, OY.BOUGHT_BACK)
        c = self._close(price=0.05, net=5.0, fee=0.0)
        self.assertEqual(c.kind, OY.BOUGHT_BACK)


# --------------------------------------------------------------------
# A series' declared multiplier (TJ-0694)
# --------------------------------------------------------------------

class TestSeriesMultiplier(unittest.TestCase):
    """TJ-0694: two futures options written, one closed with x1000 on
    the close alone: the open is sized 1000 too, as when both rows say
    it."""
    OPT = "F:SYZ261218C00001000.US"

    def _lot(self, open_mult):
        kw = {"multiplier": open_mult} if open_mult else {}
        rows = [_r("2025-01-02", self.OPT, -2, 0.01, 20, cur="USD", **kw),
                _r("2025-01-17", self.OPT, 1, 0.005, 5, cur="USD",
                   multiplier=1000.0)]
        return OY.contracts(rows)[0]

    def test_close_declares_the_size(self):
        self.assertEqual(self._lot(None).size, 1000.0)
        self.assertEqual(self._lot(None).notional,
                         self._lot(1000.0).notional)
        # A row's own multiplier stays its own.
        self.assertEqual(self._lot(10.0).size, 10.0)


# --------------------------------------------------------------------
# Shares of unknown cost (TJ-0774)
# --------------------------------------------------------------------

class TestSharesOfUnknownCost(unittest.TestCase):
    """TJ-0774: 200 shares held, 100 of them opened cost=unknown, 100
    bought for 100; a written call earned 5. The P/L stays 5; the share
    capital is unknown, so there is no yield."""
    CALL = "SYN250620C00001000.TO"

    def _doc(self, unknown):
        rows = [_r("2025-02-03", self.CALL, -1, 0.1, 10, multiplier=100.0),
                _r("2025-03-03", self.CALL, 1, 0.05, 5, multiplier=100.0)]
        key = ("margin", "SYN.TO", "CAD")
        return OY.analyze(OY.contracts(rows), start="2025-01-01",
                          end="2025-12-31", scope="tax year 2025",
                          shares={key: (200.0, 100.0)},
                          unknown_shares={key} if unknown else ())

    def test_no_yield_on_a_partial_cost(self):
        r = self._doc(True)["rows"][0]
        self.assertEqual((r["realized"], r["yield"], r["annualized"],
                          r["yield_basis"], r["shares_cost_unknown"]),
                         (5.0, None, None, "shares", True))
        t = self._doc(True)["totals"]["CAD"]
        self.assertEqual((t["yield"], t["shares_cost_unknown"]),
                         (None, True))
        text = OY.render(self._doc(True)).text()
        self.assertIn("is not all known", text)
        # Control: all of the cost known.
        r = self._doc(False)["rows"][0]
        self.assertEqual((r["yield"], r["shares_cost_unknown"]),
                         (0.05, False))

    def test_cli_list_shares_and_option_yield(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"margin":
                "OPENING 2024-12-02 SYN.TO 100 cost=unknown\n"
                "BUYSELL 2025-01-02 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                f"BUYSELL 2025-02-03 10:00:00 {self.CALL} -1 CAD 0.1 10 0\n"
                f"BUYSELL 2025-03-03 10:00:00 {self.CALL} 1 CAD 0.05 5 0\n"})
            r = _json(root, "option-yield", "SYN", "2025")["rows"][0]
            self.assertEqual((r["realized"], r["yield"],
                              r["shares_cost_unknown"]), (5.0, None, True))
            lst = _json(root, "list")
            self.assertEqual([(x["symbol"], x["unknown_cost"])
                              for x in lst["rows"]], [("SYN.TO", True)])
            self.assertEqual(lst["totals"]["unknown_cost_positions"], 1)
            sh = _json(root, "shares")
            self.assertEqual([(x["symbol"], x["unknown_cost"])
                              for x in sh["rows"]], [("SYN.TO", True)])
            out = _cli(root, "list").stdout
            self.assertIn("100.00+?", out)
            self.assertIn("units of unknown cost", out)


# --------------------------------------------------------------------
# A class share's option root (TJ-0910)
# --------------------------------------------------------------------

class TestClassShareOptionRoot(unittest.TestCase):
    """TJ-0910: 100 SYN.B.TO shares at cost 100 and a SYNB call written
    and bought back for a profit of 10: the yield is 10% on the shares,
    as with the ordinary SYN.TO control."""

    def test_option_root_names_the_class_share(self):
        with tempfile.TemporaryDirectory() as td:
            opt = "SYNB251219C00002000.TO"
            root = _project(td, {"margin":
                "BUYSELL 2025-01-02 10:00:00 SYN.B.TO 100 CAD 1 100 0\n"
                f"BUYSELL 2025-06-02 10:00:00 {opt} -1 CAD .2 20 0\n"
                f"BUYSELL 2025-07-02 10:00:00 {opt} 1 CAD .1 10 0\n"})
            doc = _json(root, "option-yield")
            r = doc["rows"][0]
            self.assertEqual((r["underlying"], r["shares_held"],
                              r["shares_cost"], r["yield_basis"],
                              r["yield"]),
                             ("SYN.B.TO", 100.0, 100.0, "shares", 0.1))
            # The option root still selects it.
            self.assertEqual(len(_json(root, "option-yield",
                                       "SYNB")["rows"]), 1)


# --------------------------------------------------------------------
# A split reported by one account of the blended pool (TJ-0907)
# --------------------------------------------------------------------

class TestSplitInAnotherAccount(unittest.TestCase):
    """TJ-0907: accounts a and b each buy one long-dated call for 20;
    only a reports its 2-for-1 split and rename; b sells its two
    adjusted contracts for 40. b's gain is 20, no contract left."""
    OLD = "SYN270917C00002000.TO"
    NEW = "SYN270917C00001000.TO"

    def test_pool_split_moves_every_account(self):
        split = ("a", {"action": "SPLIT", "date": "2025-04-01",
                       "time": "09:30:00", "symbol": self.OLD,
                       "symbol_new": self.NEW, "quantity": 2.0,
                       "account": "a"})
        rows = [_r("2025-01-03", self.OLD, 1, 0.2, 20, acct="a",
                   multiplier=100.0),
                _r("2025-01-03", self.OLD, 1, 0.2, 20, acct="b",
                   multiplier=100.0),
                split,
                _r("2025-06-03", self.NEW, -2, 0.2, 40, acct="b",
                   multiplier=100.0)]
        lots = OY.contracts(rows, pool_of=lambda a: "pool")
        b = [lot for lot in lots if lot.account == "b"]
        self.assertEqual([(lot.side, [(c.kind, c.units, c.pnl)
                                      for c in lot.closes])
                          for lot in b],
                         [(OY.BOUGHT, [(OY.SOLD, 2.0, 20.0)])])
        # Each account its own pool (a US project): b's sale writes.
        b = [lot for lot in OY.contracts(rows) if lot.account == "b"]
        self.assertEqual(len(b), 2)

    def test_leaps_and_option_yield_of_one_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {
                "a": f"BUYSELL 2025-01-03 10:00:00 {self.OLD} 1 CAD 0.2 20 0\n"
                     f"SPLIT 2025-04-01 09:30:00 {self.OLD} {self.NEW} 2\n",
                "b": f"BUYSELL 2025-01-03 10:00:00 {self.OLD} 1 CAD 0.2 20 0\n"
                     f"BUYSELL 2025-06-03 10:00:00 {self.NEW} -2 CAD 0.2 40 0\n"},
                accounts={"a": "taxable", "b": "taxable"})
            for cmd in ("leaps", "leaps-sum"):
                self.assertEqual(_json(root, cmd, "b")["total_gain"], 20.0,
                                 cmd)
            y = _json(root, "option-yield", "b")
            self.assertEqual([(c["symbol"], c["how"], c["pnl"])
                              for c in y["closed"]],
                             [(self.NEW, "sold", 20.0)])
            self.assertEqual(y["open"], [])


# --------------------------------------------------------------------
# A closed unknown-cost long before a real LEAPS entry (TJ-0777)
# --------------------------------------------------------------------

class TestLeapsAfterAnUnknownCostLong(unittest.TestCase):
    """TJ-0777: one long call opened cost=unknown and sold; then a new
    one bought (more than 9 months to expiry) for 10 and sold for 20:
    the LEAPS gain is 10."""

    def test_known_entry_after_unknown_history(self):
        sym = "SYN270115C00001000.TO"
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"margin":
                f"OPENING 2025-01-02 {sym} 1 cost=unknown\n"
                f"BUYSELL 2025-01-02 10:00:00 {sym} -1 CAD 0.1 10 0\n"
                f"BUYSELL 2025-02-03 10:00:00 {sym} 1 CAD 0.1 10 0\n"
                f"BUYSELL 2025-03-03 10:00:00 {sym} -1 CAD 0.2 20 0\n"})
            for cmd in ("leaps", "leaps-sum"):
                self.assertEqual(_json(root, cmd)["total_gain"], 10.0, cmd)


# --------------------------------------------------------------------
# ccd-sum: an expiry in a narrow window (TJ-0778), a renamed write
# bought back (TJ-0903)
# --------------------------------------------------------------------

def _occ(d: date) -> str:
    return f"SYN{d:%y%m%d}C00001000.TO"


class TestCcdGrantExpiry(unittest.TestCase):

    def test_expiry_in_window_write_before_it(self):
        """TJ-0778: a call written about four months ago expired three
        weeks ago (grant timing): `ccd-sum 2m` counts its close, with
        no premium in that window."""
        today = date.today()
        wrote, expired = today - timedelta(days=120), today - timedelta(
            days=21)
        sym = _occ(expired)
        with tempfile.TemporaryDirectory() as td:
            root = _project(
                td, {"margin":
                     f"BUYSELL {wrote} 10:00:00 {sym} -1 CAD 0.1 10 0\n"
                     f"BUYSELL {expired} 10:00:00 {sym} 1 CAD 0 0 0\n"},
                year=wrote.year,
                settings=('option_premium_timing = "grant"\n'
                          f"option_grant_timing_since = {wrote.year}\n"))
            doc = _json(root, "ccd-sum", "2m")
            self.assertEqual([(r["contracts"], r["qty"], r["gain"])
                              for r in doc["rows"]], [(1, 1.0, 0.0)])
            self.assertEqual(doc["total_gain"], 0.0)
            out = _cli(root, "ccd-sum", "2m").stdout
            self.assertNotIn("No covered-call", out)

    def test_renamed_write_bought_back(self):
        """TJ-0903: written for 10, split 2-for-1 into the new series,
        both bought back for 4: one close of two contracts, gain 6."""
        old, new = "SYN250919C00002000.TO", "SYN250919C00001000.TO"
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"margin":
                f"BUYSELL 2025-01-03 10:00:00 {old} -1 CAD .1 10 0\n"
                f"SPLIT 2025-04-01 09:30:00 {old} {new} 2\n"
                f"BUYSELL 2025-05-02 10:00:00 {new} 2 CAD .02 4 0\n"},
                settings="option_grant_timing_since = 2025\n")
            doc = _json(root, "ccd-sum")
            self.assertEqual([(r["contracts"], r["qty"], r["gain"])
                              for r in doc["rows"]], [(1, 2.0, 6.0)])

    def test_helper_follows_the_rename(self):
        from taxjson.lib.report_model import (grant_write_closes,
                                              grant_write_expiries)
        old, new = "SYN250919C00002000.TO", "SYN250919C00001000.TO"
        txs = [{"symbol": old, "date": "2025-01-03", "qty": 1.0,
                "grant": True, "direction": "SHORT"},
               {"symbol": new, "date": "2025-05-02", "qty": 2.0,
                "direction": "SHORT"}]
        split = [{"action": "SPLIT", "symbol": old, "symbol_new": new,
                  "date": "2025-04-01", "quantity": 2.0}]
        self.assertEqual(grant_write_closes(txs, [], 2025, split), {})
        # Bought back one of the two: the other expired, in the new
        # series' units, on its expiry date.
        txs[1]["qty"] = 1.0
        self.assertEqual(grant_write_expiries(txs, [], 2025, split),
                         {0: (1.0, "2025-09-19")})
        # Without the split: the old series' write reads as expired.
        self.assertEqual(grant_write_closes(txs, [], 2025), {0: 1.0})


# --------------------------------------------------------------------
# A tax year not yet begun (TJ-0784)
# --------------------------------------------------------------------

class TestFutureYear(unittest.TestCase):
    """TJ-0784: a call written and bought back today; `option-yield
    NEXT_YEAR` holds nothing (it reported today's close)."""

    def test_next_year_is_empty(self):
        today = date.today()
        sym = f"SYN{today.year + 1 - 2000:02d}0618C00001000.TO"
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"margin":
                f"BUYSELL {today} 10:00:00 {sym} -1 CAD 0.1 10 0\n"
                f"BUYSELL {today} 11:00:00 {sym} 1 CAD 0.05 5 0\n"},
                year=today.year)
            nxt = today.year + 1
            doc = _json(root, "option-yield", str(nxt))
            self.assertEqual((doc["start"], doc["end"], doc["closed"],
                              doc["rows"]),
                             (f"{nxt}-01-01", f"{nxt}-12-31", [], []))
            r = _cli(root, "option-yield", str(nxt))
            self.assertIn("has not begun", r.stderr)
            # Control: this year has the trade.
            doc = _json(root, "option-yield", str(today.year))
            self.assertEqual(len(doc["closed"]), 1)


# --------------------------------------------------------------------
# Digit-leading symbols and account names (TJ-0785, TJ-0909)
# --------------------------------------------------------------------

class TestDigitLeadingPositionals(unittest.TestCase):
    """TJ-0785: `win-rates 1234.HK` filters on that listing. TJ-0909:
    an account named `123abc` is selectable alone; one named `2025` is
    the account beside a period (or --all-history), and alone the
    period, said in a warning."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _project(cls._td.name, {
            "123abc": "BUYSELL 2025-02-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                      "BUYSELL 2025-03-03 10:00:00 SYN.TO -10 CAD 12 120 0\n",
            "2025": "BUYSELL 2025-02-03 10:00:00 SYM.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2025-03-03 10:00:00 SYM.TO -10 CAD 11 110 0\n",
            "margin": "BUYSELL 2025-02-03 10:00:00 1234.HK 10 CAD 10 100 0\n"
                      "BUYSELL 2025-03-03 10:00:00 1234.HK -10 CAD 14 140 0\n"},
            accounts={"123abc": "taxable", "2025": "taxable",
                      "margin": "taxable"})

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _net(self, *args):
        return _json(self.root, *args)["total"]["net_pl"]

    def test_digit_leading_listing(self):
        for args in (("1234.HK",), ("1234.HK", "2025")):
            doc = _json(self.root, "win-rates", *args)
            self.assertEqual([r["key"] for r in doc["rows"]], ["1234.HK"],
                             args)
            self.assertEqual(doc["unmatched_symbols"], [])
        # A malformed period is still refused.
        r = _cli(self.root, "win-rates", "30x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid time period", r.stderr)

    def test_digit_leading_account(self):
        for cmd in ("stats", "trades", "fees", "leaps"):
            r = _cli(self.root, cmd, "123abc")
            self.assertEqual(r.returncode, 0, (cmd, r.stderr))
        self.assertEqual(_json(self.root, "stats", "123abc")["accounts"],
                         ["123abc"])
        self.assertEqual(self._net("win-rates", "123abc"), 20.0)
        doc = _json(self.root, "option-yield", "123abc")
        self.assertEqual(doc["rows"], [])

    def test_year_shaped_account(self):
        self.assertEqual(self._net("win-rates", "all", "2025"), 10.0)
        self.assertEqual(self._net("win-rates", "2025", "--all-history"),
                         10.0)
        self.assertEqual(self._net("win-rates", "2025", "2025"), 10.0)
        # Alone it is the period: every account, with a warning.
        r = _cli(self.root, "win-rates", "2025", "--json")
        self.assertEqual(json.loads(r.stdout)["total"]["net_pl"], 70.0)
        self.assertIn("both a period and an account name", r.stderr)
        r = _cli(self.root, "stats", "2025")
        self.assertIn("both a period and an account name", r.stderr)


if __name__ == "__main__":
    unittest.main()
