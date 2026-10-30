"""Fix round 2026-10-29 (round 7, wave D), group stats: trading
statistics and option reports (lib/trade_stats, lib/option_yield,
ccd-sum, winners / losers).

TJ-1052  one marked delivery of two assigned puts' shares went to one
         put only (TJ-0893's pairing put each leg in a `used` set): the
         other put's premium fell back to the trades near its option
         row (an unrelated purchase) or was never taken out. A strong
         leg is now shared up to its shares, as the engine's
         core._pair_assign_legs claims it.
TJ-1096  a split before a same-day missing-history option opening.
TJ-1087  a series' x1000 declared after option-yield's window.
TJ-1084  a bare ticker and a listing of an unknown market suffix.
TJ-1085  a mistyped dotted period (1.5y).
TJ-1097  a stock's OPENING line left out of the US FIFO lots.
TJ-1095  a US spin-off takes its part of the premium to the new shares.
TJ-1133  a US year view after an earlier year's deemed gain.
TJ-1086, TJ-1112  a grant-timing expiry counts in the window of its
         expiry, also from an earlier year's write, in ccd-sum, winners
         and losers.

Every fixture is synthetic: invented tickers (ZZ*, SYN*), account names
and amounts.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

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


def _project(td: str, country="canada", year=2025, extra="",
             accounts=("margin",)) -> Path:
    root = Path(td) / "p"
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{base}"\nsource_currencies = []\n' + extra
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    return root


def _run(root: Path) -> None:
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def _json(root: Path, *args: str) -> dict:
    r = _cli(root, *args, "--json")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(r.stdout)


def _cls(doc, cls):
    c = doc["classes"].get(cls) or {"trades": 0, "wins": 0, "losses": 0,
                                    "net_pl": 0.0}
    return (c["trades"], c["wins"], c["losses"], c["net_pl"])


# --------------------------------------------------------------------
# One delivery shared by two assigned puts (TJ-1052)
# --------------------------------------------------------------------

PUT = "ZZQQ250321P00010000.TO"       # strike 10


def _r(acct, d, sym, q, net, action="BUYSELL", rid=None, **kw):
    mult = 100 if sym == PUT else 1
    return dict(dict(date=d, time="10:00:00", account=acct, symbol=sym,
                     action=action, quantity=q, net_amount=net,
                     price=abs(net) / abs(q) / mult if net and q else 0,
                     currency="CAD", multiplier=mult,
                     **({"id": rid} if rid else {})), **kw)


class TestOneDeliveryOfTwoPuts(unittest.TestCase):
    """TJ-1052: two 1-contract puts at strike 10 (written for 100 and
    200) are assigned 2025-03-21; one marked 200-share delivery at 10.
    Both premiums come out of those 200 shares' sale (300)."""

    def _book(self, dlv_date, unrelated=False):
        rows = [_r("margin", "2025-01-02", PUT, -1, 100, rid="w1"),
                _r("margin", "2025-01-02", PUT, -1, 200, rid="w2",
                   time="11:00:00"),
                _r("margin", "2025-03-21", PUT, 1, 0, "ASSIGN", rid="a1",
                   time="16:00:00"),
                _r("margin", "2025-03-21", PUT, 1, 0, "ASSIGN", rid="a2",
                   time="16:01:00"),
                _r("margin", dlv_date, "ZZQQ.TO", 200, 2000, "ASSIGN",
                   rid="dlv", time="16:30:00"),
                _r("margin", "2025-06-02", "ZZQQ.TO", -200, 2000,
                   rid="sale")]
        if unrelated:
            rows += [_r("margin", "2025-03-21", "ZZQQ.TO", 50, 475,
                        rid="other_buy", time="10:00:00"),
                     _r("margin", "2025-03-24", "ZZQQ.TO", -50, 480,
                        rid="other_sale", time="09:31:00")]
        return rows

    def _both(self, book):
        from taxjson.lib import trade_stats as TS
        credits = TS.written_option_trades(
            book, lambda x: x["date"])["credits"]
        pooled = {k: round(v, 6) for k, v in TS.pooled_put_credits(
            {"margin": book}, credits).items()}
        fifo = {k: round(v, 6)
                for k, v in TS.credits_by_sale(book, credits).items()}
        return pooled, fifo

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_delivery_after_the_options_with_an_unrelated_trade(self):
        pooled, fifo = self._both(self._book("2025-03-24", unrelated=True))
        self.assertEqual(pooled, {("margin", "sale"): 300.0})
        self.assertEqual(fifo, {"sale": 300.0})

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_delivery_the_day_before_the_options(self):
        pooled, fifo = self._both(self._book("2025-03-20"))
        self.assertEqual(pooled, {("margin", "sale"): 300.0})
        self.assertEqual(fifo, {"sale": 300.0})

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {PUT} -1 CAD 1 100 0\n"
                f"BUYSELL 2025-01-02 11:00:00 {PUT} -1 CAD 2 200 0\n"
                f"ASSIGN 2025-03-21 16:00:00 {PUT} 1 CAD 0 0 0\n"
                f"ASSIGN 2025-03-21 16:01:00 {PUT} 1 CAD 0 0 0\n"
                "BUYSELL 2025-03-21 10:00:00 ZZQQ.TO 50 CAD 9.5 475 0\n"
                "BUYSELL 2025-03-24 09:31:00 ZZQQ.TO -50 CAD 9.6 480 0\n"
                "ASSIGN 2025-03-24 16:30:00 ZZQQ.TO 200 CAD 10 2000 0\n"
                "BUYSELL 2025-06-02 10:00:00 ZZQQ.TO -200 CAD 10 2000 0\n")
            _run(root)
            doc = _json(root, "stats", "2025")
        self.assertEqual(_cls(doc, "long-shares"), (2, 1, 0, 5.0))
        self.assertEqual(_cls(doc, "written-options")[3], 300.0)
        self.assertEqual(doc["total"]["net_pl"], 305.0)


# --------------------------------------------------------------------
# A split before a same-day missing-history opening (TJ-1096)
# --------------------------------------------------------------------

class TestOpeningBeforeSameDaySplit(unittest.TestCase):
    """TJ-1096: `OPENING <split's day> <old series>`: the Canadian engine
    applies the opening, then the split (Phase.PRE_EXISTING, then SPLIT).
    TJ-0697's split-first order ran the split first, so the sale of the
    new series read as a write."""

    TT = ("SPLIT 2025-05-01 09:30:00 SYN251219C00001000.TO "
          "SYN251219C00000500.TO 2\n"
          "BUYSELL 2025-05-01 10:00:00 SYN251219C00000500.TO -2 CAD .02 4 0\n"
          "BUYSELL 2025-06-02 10:00:00 SYN251219C00000500.TO 2 CAD .03 6 0\n"
          "BUYSELL 2025-07-02 10:00:00 SYN251219C00000500.TO -2 CAD .05 10 0\n")

    def _project(self, td):
        root = _project(td)
        (root / "inputs" / "margin" / "trades.tt").write_text(self.TT)
        (root / "inputs" / "margin" / "missing_history.tt").write_text(
            "OPENING 2025-05-01 SYN251219C00001000.TO 1 cost=unknown "
            'reason="bought before the files"\n')
        return root

    @rule("CA-DATE-14", "CA-RPT-16")
    def test_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            _run(root)
            doc = _json(root, "stats", "2025")
        self.assertEqual(_cls(doc, "long-options")[0::3], (1, 4.0))
        self.assertEqual(_cls(doc, "written-options")[0], 0)
        self.assertEqual(doc["total"]["net_pl"], 4.0)

    @rule("CA-DATE-14", "CA-RPT-16")
    def test_a_noon_split_still_comes_before_the_morning_trade(self):
        # TJ-0697 stands: a buy-back of the new series stamped before
        # the split of its day closes the renamed write.
        from taxjson.lib import trade_stats as TS
        rows = [dict(date="2025-01-02", time="10:00:00", account="m",
                     symbol="SYN251219C00001000.TO", action="BUYSELL",
                     quantity=-1, net_amount=10, id="w"),
                dict(date="2025-05-01", time="12:00:00", account="m",
                     symbol="SYN251219C00001000.TO", action="SPLIT",
                     symbol_new="SYN251219C00000500.TO", quantity=2, id="s"),
                dict(date="2025-05-01", time="09:40:00", account="m",
                     symbol="SYN251219C00000500.TO", action="BUYSELL",
                     quantity=2, net_amount=4, id="b")]
        doc = TS.written_option_trades(rows, lambda x: x["date"],
                                       split_at_open=True)
        self.assertEqual([round(x["pnl"], 2) for x in doc["trades"]], [6.0])


# --------------------------------------------------------------------
# A series' x1000 declared after the window (TJ-1087)
# --------------------------------------------------------------------

class TestSizeDeclaredAfterTheWindow(unittest.TestCase):
    """TJ-1087: the declared size is the series', whichever row states
    it, also a row after the window's end."""

    SYM = "F:SYZ271217C00001000.US"

    def _yield(self, tt):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa", year=2026)
            (root / "inputs" / "margin" / "trades.tt").write_text(tt)
            _run(root)
            return _json(root, "option-yield", "2025")["rows"]

    def test_declared_on_the_later_row(self):
        late = self._yield(
            f"BUYSELL 2025-01-02 10:00:00 {self.SYM} -2 USD .01 20 0\n"
            f"BUYSELL 2025-01-17 10:00:00 {self.SYM} 1 USD .005 5 0\n"
            f"BUYSELL 2026-01-15 10:00:00 {self.SYM} 1 USD .005 5 0 x1000\n")
        every = self._yield(
            f"BUYSELL 2025-01-02 10:00:00 {self.SYM} -2 USD .01 20 0 x1000\n"
            f"BUYSELL 2025-01-17 10:00:00 {self.SYM} 1 USD .005 5 0 x1000\n"
            f"BUYSELL 2026-01-15 10:00:00 {self.SYM} 1 USD .005 5 0 x1000\n")
        self.assertEqual(late, every)
        self.assertAlmostEqual(late[0]["avg_notional"], 1038.36, places=2)


# --------------------------------------------------------------------
# A bare ticker and a listing of an unknown suffix (TJ-1084)
# --------------------------------------------------------------------

class TestBareTickerUnknownSuffix(unittest.TestCase):
    """TJ-1084: `SYNX` names SYNX.DE again; TJ-0692 stands: `SYN` never
    names the class share SYN.B.US or a unit SYN.UN."""

    def test_library(self):
        from taxjson.lib.option_yield import underlying_matches as m
        self.assertTrue(m("SYNX.DE", ["SYNX"]))
        self.assertTrue(m("SYNH.HK", ["SYNH"]))
        self.assertTrue(m("1234.HK", ["1234"]))
        self.assertTrue(m("SYN.B.DE", ["SYN.B"]))
        self.assertTrue(m("SYNX.DE", ["SYNX.DE"]))
        self.assertFalse(m("SYN.B.DE", ["SYN"]))
        self.assertFalse(m("SYNX.DE", ["SYNX.HK"]))
        # TJ-0692's own
        self.assertTrue(m("SYN.B.US", ["SYN.B"]))
        self.assertTrue(m("SYN.US", ["SYN"]))
        self.assertFalse(m("SYN.B.US", ["SYN"]))
        self.assertFalse(m("SYN.US", ["SYN.B"]))
        # A class or a unit is the security's own, on no known market too
        self.assertFalse(m("SYN.B", ["SYN"]))
        self.assertFalse(m("SYN.UN", ["SYN"]))
        self.assertFalse(m("SYN.UN.TO", ["SYN"]))
        self.assertTrue(m("SYN.UN.TO", ["SYN.UN"]))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                "BUYSELL 2025-02-03 10:00:00 SYNX.DE 10 CAD 10 100 0\n"
                "BUYSELL 2025-03-03 10:00:00 SYNX.DE -10 CAD 12 120 0\n")
            _run(root)
            doc = _json(root, "win-rates", "SYNX")
        self.assertEqual(doc["unmatched_symbols"], [])
        self.assertEqual([r["key"] for r in doc["rows"]], ["SYNX.DE"])


# --------------------------------------------------------------------
# A mistyped dotted period (TJ-1085)
# --------------------------------------------------------------------

class TestDottedPeriodRefused(unittest.TestCase):
    """TJ-1085: `1.5y` is a mistyped period (exit 2); TJ-0785 stands:
    `1234.HK` is a listing."""

    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                "BUYSELL 2025-02-03 10:00:00 1234.HK 10 CAD 10 100 0\n"
                "BUYSELL 2025-03-03 10:00:00 1234.HK -10 CAD 12 120 0\n")
            _run(root)
            for cmd in ("win-rates", "option-yield"):
                for tok in ("1.5y", "2.5m", "1.5"):
                    r = _cli(root, cmd, tok)
                    self.assertEqual(r.returncode, 2, (cmd, tok, r.stdout))
                    self.assertIn(f"invalid time period '{tok}'",
                                  " ".join((r.stdout + r.stderr).split()))
            doc = _json(root, "win-rates", "1234.HK")
        self.assertEqual([r["key"] for r in doc["rows"]], ["1234.HK"])



# --------------------------------------------------------------------
# An opening of unknown cost in the US lots (TJ-1097)
# --------------------------------------------------------------------

class TestUsOpeningLot(unittest.TestCase):
    """TJ-1097: an older lot opened by an OPENING line is sold first
    (FIFO); the second sale takes the delivered lot and its premium.
    TJ-0771's control, a purchase for the older lot, gives the same."""

    PUT = "SYN250117P00001000.US"
    TT = (f"BUYSELL 2025-01-02 10:00:00 {PUT} -1 USD .1 10 0\n"
          f"ASSIGN 2025-01-17 10:00:00 {PUT} 1 USD 0 0 0\n"
          "ASSIGN 2025-01-17 10:00:00 SYN.US 100 USD 1 100 0\n"
          "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD 1 100 0\n"
          "BUYSELL 2025-03-04 10:00:00 SYN.US -100 USD 1 100 0\n")

    def _stats(self, tt, mh=None):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa")
            (root / "inputs" / "margin" / "trades.tt").write_text(tt)
            if mh:
                (root / "inputs" / "margin" / "missing_history.tt"
                 ).write_text(mh)
            _run(root)
            return _json(root, "stats", "2025")

    @rule("US-BASIS-01", "US-OPT-05")
    def test_opening(self):
        doc = self._stats(self.TT, 'OPENING 2024-12-31 SYN.US 100 cost=unknown '
                          'reason="bought before the files"\n')
        self.assertEqual(_cls(doc, "long-shares")[0::3], (1, 0.0))
        self.assertEqual(_cls(doc, "written-options")[0::3], (1, 10.0))
        self.assertEqual(doc["total"]["net_pl"], 10.0)
        self.assertEqual(doc["tainted_skipped"], 1)

    @rule("US-BASIS-01", "US-OPT-05")
    def test_the_control(self):
        doc = self._stats("BUYSELL 2024-12-02 10:00:00 SYN.US 100 USD 1 100 0\n"
                          + self.TT)
        self.assertEqual(_cls(doc, "long-shares")[0], 2)
        self.assertEqual(doc["total"]["net_pl"], 10.0)

    @rule("US-BASIS-01", "US-OPT-05")
    def test_library_opening_is_a_lot_with_no_premium(self):
        from taxjson.lib import trade_stats as TS
        rows = [dict(date="2024-12-31", time="00:00:00", account="m",
                     symbol="SYN.US", action="OPENING_BALANCE", quantity=100,
                     id="o"),
                dict(date="2025-03-03", time="10:00:00", account="m",
                     symbol="SYN.US", action="BUYSELL", quantity=-100,
                     net_amount=100, price=1, id="s1"),
                dict(date="2025-03-04", time="10:00:00", account="m",
                     symbol="SYN.US", action="BUYSELL", quantity=-100,
                     net_amount=100, price=1, id="s2"),
                dict(date="2025-01-17", time="10:00:00", account="m",
                     symbol="SYN.US", action="ASSIGN", quantity=100,
                     net_amount=100, price=1, id="d")]
        cred = [dict(account="m", underlying="SYN.US", right="P", shares=100.0,
                     amount=10.0, option=0, date="2025-01-17", strike=1.0,
                     delivered=100.0)]
        self.assertEqual(TS.credits_by_sale(rows, cred), {"s2": 10.0})


# --------------------------------------------------------------------
# A US spin-off of the delivered shares (TJ-1095)
# --------------------------------------------------------------------

SPIN = ("Statement,Header,Field Name,Field Value\n"
        "Statement,Data,BrokerName,Interactive Brokers\n"
        "Corporate Actions,Header,Asset Category,Currency,"
        "Report Date,Date/Time,Description,Quantity,Proceeds,"
        "Value,Realized P/L,Code\n"
        "Corporate Actions,Data,Stocks,USD,2025-02-03,"
        '"2025-02-03, 10:00:00","ZZQQ(US0000000921) Spinoff 1 for 1 '
        '(ZZKK, ZZKK INC, US0000000922)",{qty},0,{qty},0,\n')


def _elect_spin(root: Path, hint: str) -> None:
    import re
    r = _cli(root, "run", "--no-input")
    pend = (root / "work" / "pending_elections.json").read_text()
    m = re.search(r'"(\d{8}-zzqq-zzkk-[0-9a-f]+)"', pend)
    assert m, r.stdout[-2000:] + r.stderr[-2000:]
    r = _cli(root, "elect", "margin", "--set", f"{m[1]}=tax_free_355",
             "--hint", hint)
    assert r.returncode == 0, r.stderr[-2000:]
    _run(root)


class TestUsSpinOff(unittest.TestCase):
    """TJ-1095: a put written for 20 delivers 100 ZZQQ.US at 2 (basis
    180); a §355 spin-off moves 90 to 100 ZZKK.US. The parent's sale
    (cost 90, gain 10) carries 10 of the premium, the child's the other
    10."""

    PUT = "ZZQQ250321P00002000.US"

    def _doc(self, extra="", year=2025, qty=100, alloc=90, window="2025"):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa", year=year)
            inp = root / "inputs" / "margin"
            (inp / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {self.PUT} -1 USD .2 20 0\n"
                f"ASSIGN 2025-01-17 10:00:00 {self.PUT} 1 USD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 ZZQQ.US 100 USD 2 200 0\n"
                + extra)
            (inp / "spin.csv").write_text(SPIN.format(qty=qty))
            _elect_spin(root, f"allocated_acb={alloc}")
            return _json(root, "stats", window)

    @rule("US-RPT-12", "US-CORP-07")
    def test_parent_sale(self):
        doc = self._doc(
            "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0\n")
        self.assertEqual(_cls(doc, "long-shares")[0::3], (1, 0.0))
        self.assertEqual(_cls(doc, "written-options")[0::3], (1, 20.0))
        self.assertEqual(doc["total"]["net_pl"], 20.0)

    @rule("US-RPT-12", "US-CORP-07")
    def test_child_sale_carries_its_part(self):
        # The child's cost is 90, sold for 100: gain 10, its part of the
        # premium; the parent's the same.
        doc = self._doc(
            "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0\n"
            "BUYSELL 2025-03-04 10:00:00 ZZKK.US -100 USD 1 100 0\n")
        # (2 trades, no win, no loss: at the parent's -10 and the child's
        # +10 the sum was right and both trades wrong)
        self.assertEqual(_cls(doc, "long-shares"), (2, 0, 0, 0.0))
        self.assertEqual(doc["total"]["net_pl"], 20.0)

    @rule("US-RPT-12", "US-CORP-07")
    def test_a_year_view_after_the_spin_off(self):
        doc = self._doc(
            "BUYSELL 2026-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0\n",
            year=2026, window="2026")
        self.assertEqual(_cls(doc, "long-shares")[0::3], (1, 0.0))

    @rule("US-RPT-12", "US-CORP-07")
    def test_two_accounts_and_an_opening_of_the_other(self):
        # The blended pass's timeline; the other account's opening and
        # sale of the same shares are its own lots.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa", accounts=("margin", "other"))
            inp = root / "inputs" / "margin"
            (inp / "trades.tt").write_text(
                f"BUYSELL 2025-01-02 10:00:00 {self.PUT} -1 USD .2 20 0\n"
                f"ASSIGN 2025-01-17 10:00:00 {self.PUT} 1 USD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 ZZQQ.US 100 USD 2 200 0\n"
                "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0\n")
            (inp / "spin.csv").write_text(SPIN.format(qty=100))
            oth = root / "inputs" / "other"
            (oth / "trades.tt").write_text(
                "BUYSELL 2025-04-01 10:00:00 ZZQQ.US -10 USD 3 30 0\n")
            (oth / "missing_history.tt").write_text(
                "OPENING 2024-12-31 ZZQQ.US 10 cost=unknown "
                'reason="bought before the files"\n')
            _elect_spin(root, "allocated_acb=90")
            doc = _json(root, "stats", "2025")
        self.assertEqual(_cls(doc, "long-shares"), (1, 0, 0, 0.0))
        self.assertEqual(doc["total"]["net_pl"], 20.0)
        self.assertEqual(doc["tainted_skipped"], 1)

    @rule("US-RPT-12", "US-CORP-07")
    def test_blocks_follow_fifo(self):
        # An older lot (100 at 1, no premium) and the delivered lot
        # (basis 180): 140 of 280 moves, half of each lot's basis and
        # premium. The child's first sale takes the older lot's block,
        # no premium; its second the delivered lot's, 10.
        from taxjson.lib import trade_stats as TS
        tl = {"version": 1,
              "fields": ["seq", "date", "account", "symbol", "qty",
                         "total_cost", "deferred_wash", "unknown_cost",
                         "currency", "position_start_date", "last_acq_date",
                         "renamed_to"],
              "rows": [
                  [1, "2025-01-17", "m", "ZZQQ.US", 200.0, 280.0, 0.0, False,
                   "USD", "2024-12-02", "2025-01-17", ""],
                  [2, "2025-02-03", "m", "ZZKK.US", 200.0, 140.0, 0.0, False,
                   "USD", "2024-12-02", "2025-01-17", ""],
                  [3, "2025-02-03", "m", "ZZQQ.US", 200.0, 140.0, 0.0, False,
                   "USD", "2024-12-02", "2025-01-17", ""]]}
        def row(i, d, sym, q, net, act="BUYSELL", **kw):
            return dict(dict(id=i, date=d, time="10:00:00", account="m",
                             symbol=sym, action=act, quantity=q,
                             net_amount=net, price=abs(net / q) if q else 0),
                        **kw)
        rows = [row("old", "2024-12-02", "ZZQQ.US", 100, 100),
                row("d", "2025-01-17", "ZZQQ.US", 100, 200, "ASSIGN"),
                row("c", "2025-02-03", "ZZKK.US", 200, 140, type="spinoff_355",
                    corp_event_id="ev"),
                row("a", "2025-02-03", "ZZQQ.US", 0, -140, "ADJUST",
                    type="spinoff_355", corp_event_id="ev"),
                row("c1", "2025-03-03", "ZZKK.US", -100, 100),
                row("c2", "2025-03-04", "ZZKK.US", -100, 100),
                row("p1", "2025-03-05", "ZZQQ.US", -100, 100),
                row("p2", "2025-03-06", "ZZQQ.US", -100, 100)]
        cred = [dict(account="m", underlying="ZZQQ.US", right="P",
                     shares=100.0, amount=20.0, option=0, date="2025-01-17",
                     strike=2.0, delivered=100.0)]
        got = {k[1]: round(v, 6) for k, v in TS.share_credits(
            {"m": rows}, cred, timelines={"m": tl}).items()}
        self.assertEqual(got, {"c2": 10.0, "p2": 10.0})
        # Without a timeline: all of it stays on the parent.
        got = {k[1]: round(v, 6) for k, v in TS.share_credits(
            {"m": rows}, cred, timelines={}).items()}
        self.assertEqual(got, {"p2": 20.0})
        # And a lot bought on or after the ex-date keeps its own basis.
        late = [row("l", "2025-02-01", "ZZQQ.US", 100, 300)]
        tl2 = dict(tl, rows=[
            [1, "2025-02-01", "m", "ZZQQ.US", 300.0, 580.0, 0.0, False,
             "USD", "2024-12-02", "2025-02-01", ""],
            [2, "2025-02-03", "m", "ZZKK.US", 200.0, 140.0, 0.0, False,
             "USD", "2024-12-02", "2025-01-17", ""],
            [3, "2025-02-03", "m", "ZZQQ.US", 300.0, 440.0, 0.0, False,
             "USD", "2024-12-02", "2025-02-01", ""]])
        rows2 = [dict(r, ex_date="2025-02-01") if r["id"] == "c" else r
                 for r in rows] + late
        got = {k[1]: round(v, 6) for k, v in TS.share_credits(
            {"m": rows2}, cred, timelines={"m": tl2}).items()}
        self.assertEqual(got, {"c2": 10.0, "p2": 10.0})


# --------------------------------------------------------------------
# The US year view after an earlier year's deemed gain (TJ-1133)
# --------------------------------------------------------------------

class TestUsYearViewAfterEarlierDeemedGain(unittest.TestCase):
    """TJ-1133: a return of capital beyond the basis in 2024 took the
    premium the basis held; the 2025 sale keeps its whole gain in the
    year view as in `stats all`."""

    PUT = "SYN240119P00001000.US"

    @rule("US-ROC-02", "US-RPT-12")
    def test_year_view_equals_all(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa")
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2024-01-02 10:00:00 {self.PUT} -1 USD .2 20 0\n"
                f"ASSIGN 2024-01-19 09:30:00 {self.PUT} 1 USD 0 0 0\n"
                "ASSIGN 2024-01-19 10:00:00 SYN.US 100 USD 1 100 0\n"
                "ADJUST 2024-06-03 10:00:00 SYN.US USD -100 type=roc\n"
                "BUYSELL 2025-07-02 10:00:00 SYN.US -100 USD .4 40 0\n")
            _run(root)
            year = _json(root, "stats", "2025")
            whole = _json(root, "stats", "all")
        self.assertEqual(_cls(whole, "long-shares")[3], 40.0)
        self.assertEqual(_cls(year, "long-shares")[3], 40.0)

    @rule("US-RPT-12")
    def test_a_later_years_roc_changes_nothing(self):
        # (a return of capital after the view's year builds no file and
        # the premium stays with the sale of the year)
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country="usa", year=2024)
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL 2024-01-02 10:00:00 {self.PUT} -1 USD .2 20 0\n"
                f"ASSIGN 2024-01-19 09:30:00 {self.PUT} 1 USD 0 0 0\n"
                "ASSIGN 2024-01-19 10:00:00 SYN.US 100 USD 1 100 0\n"
                "BUYSELL 2024-07-02 10:00:00 SYN.US -50 USD 1 50 0\n"
                "ADJUST 2025-06-03 10:00:00 SYN.US USD -100 type=roc\n")
            _run(root)
            doc = _json(root, "stats", "2024")
            self.assertFalse((root / "work" / "margin_gains_all.json").exists())
        self.assertEqual(_cls(doc, "long-shares")[3], 0.0)


# --------------------------------------------------------------------
# A grant-timing expiry of an earlier year's write (TJ-1086, TJ-1112)
# --------------------------------------------------------------------

class TestGrantExpiryWindows(unittest.TestCase):
    """TJ-1086: a call written in December 2025 expires in January 2026:
    the tax year 2026 counts the close, with no premium; 2025 the
    premium, with no close; `all` both. TJ-1112: winners and losers
    count it the same way."""

    CALL = "SYN260116C00001000.TO"
    GRANT = 'option_premium_timing = "grant"\noption_grant_timing_since = 2025\n'

    def _root(self, td, tt, year=2026):
        root = _project(td, year=year, extra=self.GRANT)
        (root / "inputs" / "margin" / "trades.tt").write_text(tt)
        _run(root)
        return root

    def _tt(self, close=True):
        return ("BUYSELL 2025-11-03 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                f"BUYSELL 2025-12-01 10:00:00 {self.CALL} -1 CAD .1 10 0\n"
                + (f"BUYSELL 2026-01-16 10:00:00 {self.CALL} 1 CAD 0 0 0\n"
                   if close else ""))

    @staticmethod
    def _ccd(root, *args):
        rows = _json(root, "ccd-sum", *args)["rows"]
        return [(r["contracts"], r["qty"], r["proceeds"]) for r in rows]

    @staticmethod
    def _ranked(root, *args):
        rows = _json(root, "winners", *args)["rows"]
        return [(r["ticker"], r["closes"], r["gain"]) for r in rows]

    @rule("CA-OPT-01")
    def test_ccd_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, self._tt())
            self.assertEqual(self._ccd(root), [(1, 1.0, 0.0)])
            self.assertEqual(self._ccd(root, "2025"), [(0, 0.0, 10.0)])
            self.assertEqual(self._ccd(root, "all"), [(1, 1.0, 10.0)])

    @rule("CA-OPT-01")
    def test_winners(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, self._tt())
            self.assertEqual(self._ranked(root), [("SYN.TO", 1, 0.0)])
            self.assertEqual(self._ranked(root, "2025"),
                             [("SYN.TO", 0, 10.0)])
            self.assertEqual(self._ranked(root, "all"),
                             [("SYN.TO", 1, 10.0)])

    @rule("CA-OPT-01")
    def test_a_buy_back_is_not_also_expired(self):
        tt = ("BUYSELL 2025-11-03 10:00:00 SYN.TO 100 CAD 1 100 0\n"
              f"BUYSELL 2025-12-01 10:00:00 {self.CALL} -1 CAD .1 10 0\n"
              f"BUYSELL 2026-01-10 10:00:00 {self.CALL} 1 CAD .05 5 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, tt)
            self.assertEqual([r[0] for r in self._ccd(root)], [1])
            self.assertEqual(self._ranked(root)[0][1], 1)

    @rule("CA-OPT-01")
    def test_a_year_with_no_earlier_write_builds_no_full_history(self):
        tt = (f"BUYSELL 2026-01-02 10:00:00 {self.CALL} -1 CAD .1 10 0\n"
              f"BUYSELL 2026-01-16 10:00:00 {self.CALL} 1 CAD 0 0 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, tt)
            self.assertEqual(self._ccd(root), [(1, 1.0, 10.0)])
            self.assertFalse((root / "work" / "margin_gains_all.json").exists())

    @rule("CA-OPT-01")
    def test_a_narrow_window_holding_the_expiry_only(self):
        # TJ-1112's own shape: written 120 days ago, expired 21 days ago.
        from datetime import date, timedelta
        today = date.today()
        wrote = today - timedelta(days=120)
        exp = today - timedelta(days=21)
        call = f"SYN{exp:%y%m%d}C00001000.TO"
        tt = (f"BUYSELL {wrote - timedelta(days=30)} 10:00:00 SYN.TO 100 CAD "
              "1 100 0\n"
              f"BUYSELL {wrote} 10:00:00 {call} -1 CAD .1 10 0\n"
              f"BUYSELL {exp} 10:00:00 {call} 1 CAD 0 0 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, year=today.year, extra=(
                'option_premium_timing = "grant"\n'
                f"option_grant_timing_since = {wrote.year}\n"))
            (root / "inputs" / "margin" / "trades.tt").write_text(tt)
            _run(root)
            self.assertEqual(self._ranked(root, "2m"), [("SYN.TO", 1, 0.0)])


if __name__ == "__main__":
    unittest.main()
