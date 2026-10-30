"""Fix round 2026-10-29 (round 7, wave B), group statsus: the assigned
option premium in `stats` / `win-rates` (lib/trade_stats) and the
written options of `option-yield` (lib/option_yield), US and
cross-cutting cases.

TJ-0770  a US stock dividend grew the shares the put's premium was
         spread over, but the statistics still drew it from 100 shares:
         a sale of half the shares lost the whole premium.
TJ-0771  a US sale that FIFO took from an older lot lost the premium of
         the assigned lot still held.
TJ-0787  a written call assigned with no shares held (or too few) opens
         a short: its premium stayed on the leg, and the later cover
         counted it a second time.
TJ-0788  a US put's delivered shares moved to another of your accounts
         (an own-account move): the receiving account's sale kept the
         premium, counted twice.
TJ-0789  a ticker.map MULT line did not size an assignment in the
         statistics (nor any contract in option-yield): a mini option's
         premium was spread over 100 shares a contract.
TJ-0697  a split stamped at noon replayed after a morning buy-back of
         the new series, in a Canadian project: the buy-back read as a
         new long. The Canadian engine takes a split at the day's open.

Every fixture is synthetic: invented tickers (SYN*), account names and
amounts.
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

US_PUT = "SYN250117P00001000.US"       # strike 1, Jan 17
US_CALL = "SYN250117C00001000.US"
CA_CALL = "SYN250117C00001000.TO"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _project(td: str, country: str, trades: dict, tmap: str = "",
             files: dict = None) -> Path:
    """A project of `country` whose accounts (all taxable) hold the .tt
    lines of `trades` ({account: text})."""
    root = Path(td) / "p"
    cur = "CAD" if country == "canada" else "USD"
    for a in trades:
        (root / "inputs" / a).mkdir(parents=True)
        if trades[a]:
            (root / "inputs" / a / "trades.tt").write_text(trades[a])
    for rel, text in (files or {}).items():
        (root / rel).write_text(text)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in trades))
    (root / "ticker.map").write_text(tmap)
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return root


def _stats(root: Path, *args: str) -> dict:
    r = _cli(root, "stats", "2025", *args, "--json")
    assert r.returncode == 0, r.stderr[-2000:]
    doc = json.loads(r.stdout)
    out = {c: (v["trades"], v["net_pl"]) for c, v in doc["classes"].items()
           if v["trades"]}
    out["total"] = (doc["total"]["trades"], doc["total"]["net_pl"])
    return out


def _total_gain(root: Path, acct: str = "margin") -> float:
    doc = json.loads((root / "work" / f"{acct}_gains_wash.json")
                     .read_text())
    return round(sum(t["gain"] for t in doc["transactions"]
                     if "gain" in t), 6)


# A put written for 10, assigned Jan 17: 100 shares at 1 (cost 90).
PUT_ASSIGNED = (f"BUYSELL 2025-01-02 10:00:00 {US_PUT} -1 USD 0.1 10 0\n"
                f"ASSIGN 2025-01-17 10:00:00 {US_PUT} 1 USD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 SYN.US 100 USD 1 100 0\n")


class TestStockDividendSpreadsThePremium(unittest.TestCase):
    """TJ-0770: a typed stock dividend doubles the delivered shares (the
    basis of 90 spread over 200, §307); 100 are sold for 50 at cost 45.
    The sale breaks even after the premium's half: stock 0, option 10,
    as with the same 2:1 split."""

    @rule("US-STKDIV-01", "US-OPT-05")
    def test_stock_dividend_as_split(self):
        for event in (
                "SPLIT 2025-02-03 09:30:00 SYN.US SYN.US 2\n",
                "BUYSELL 2025-02-03 09:30:00 SYN.US 100 USD 0 0 0 "
                "type=stock_dividend record=2025-01-31 ex=2025-01-31\n"):
            with self.subTest(event=event.split()[0]), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, "usa", {"margin": (
                    PUT_ASSIGNED + event
                    + "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD 0.5 50 "
                      "0\n")})
                # (the assigned option has no gains row of its own)
                self.assertEqual(_total_gain(root), 5.0)
                self.assertEqual(_stats(root),
                                 {"long-shares": (1, 0.0),
                                  "written-options": (1, 10.0),
                                  "total": (2, 10.0)})


class TestFifoOlderLotKeepsItsGain(unittest.TestCase):
    """TJ-0771: 100 shares bought in December at 1, then a put delivers
    100 more at cost 90. The March sale of 100 at 1 takes the December
    lot (FIFO): it breaks even, and the premium stays in the lot still
    held."""

    @rule("US-BASIS-01", "US-OPT-05")
    def test_sale_of_the_older_lot(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {"margin": (
                "BUYSELL 2024-12-02 10:00:00 SYN.US 100 USD 1 100 0\n"
                + PUT_ASSIGNED
                + "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD 1 100 0\n")})
            self.assertEqual(_total_gain(root), 0.0)
            self.assertEqual(_stats(root), {"long-shares": (1, 0.0),
                                            "written-options": (1, 10.0),
                                            "total": (2, 10.0)})

    @rule("US-BASIS-01", "US-OPT-05")
    def test_second_sale_takes_the_assigned_lot(self):
        # A second sale of 100 at 1 takes the assigned lot (cost 90):
        # its gain of 10 is the premium, already counted on the option.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {"margin": (
                "BUYSELL 2024-12-02 10:00:00 SYN.US 100 USD 1 100 0\n"
                + PUT_ASSIGNED
                + "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD 1 100 0\n"
                "BUYSELL 2025-03-04 10:00:00 SYN.US -100 USD 1 100 0\n")})
            self.assertEqual(_stats(root), {"long-shares": (2, 0.0),
                                            "written-options": (1, 10.0),
                                            "total": (3, 10.0)})


def _call_rows(sfx: str, cur: str, held: int) -> str:
    """A call written for 10 and assigned Jan 17 (100 shares delivered
    at 1) with `held` shares held; what the delivery left short is
    covered at 1 in March."""
    sym = f"SYN250117C00001000.{sfx}"
    r = (f"BUYSELL 2025-01-02 09:00:00 SYN.{sfx} {held} {cur} 1 {held} 0\n"
         if held else "")
    r += (f"BUYSELL 2025-01-02 10:00:00 {sym} -1 {cur} 0.1 10 0\n"
          f"ASSIGN 2025-01-17 10:00:00 {sym} 1 {cur} 0 0 0\n"
          f"ASSIGN 2025-01-17 10:00:00 SYN.{sfx} -100 {cur} 1 100 0\n")
    if held < 100:
        r += (f"BUYSELL 2025-03-03 10:00:00 SYN.{sfx} {100 - held} {cur} 1 "
              f"{100 - held} 0\n")
    return r


class TestCallOpeningAShortCanada(unittest.TestCase):
    """TJ-0787 (Canada): the call's delivery opens a short (all of it,
    or the half the 50 shares held did not cover); the premium is in
    that short's proceeds, so its cover's gain holds it: the cover is a
    break-even, and the premium counts once, on the option."""

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_no_shares_held(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": _call_rows(
                "TO", "CAD", 0)})
            self.assertEqual(_total_gain(root), 10.0)
            self.assertEqual(_stats(root), {"short-shares": (1, 0.0),
                                            "written-options": (1, 10.0),
                                            "total": (2, 10.0)})

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_half_the_shares_held(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": _call_rows(
                "TO", "CAD", 50)})
            self.assertEqual(_stats(root), {"long-shares": (1, 0.0),
                                            "short-shares": (1, 0.0),
                                            "written-options": (1, 10.0),
                                            "total": (3, 10.0)})


class TestCallOpeningAShortUS(unittest.TestCase):
    """TJ-0787 (US): the same, under the US engine."""

    @rule("US-RPT-12", "US-OPT-05")
    def test_no_shares_held(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {"margin": _call_rows(
                "US", "USD", 0)})
            self.assertEqual(_total_gain(root), 10.0)
            self.assertEqual(_stats(root), {"short-shares": (1, 0.0),
                                            "written-options": (1, 10.0),
                                            "total": (2, 10.0)})


def _ib_transfer(cur: str, day: str, q: int) -> str:
    """An IB activity statement with one ACATS share transfer row."""
    return ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers LLC\n"
            "Transfers,Header,Asset Category,Currency,Symbol,Date,Type,"
            "Direction,Xfer Company,Xfer Account,Qty,Xfer Price,"
            "Market Value,Realized P/L,Cash Amount,Code\n"
            f"Transfers,Data,Stocks,{cur},SYN,{day},ACATS,"
            f"{'Out' if q < 0 else 'In'},Other Broker,99900001,{q},0,{q},"
            "0,0,\n")


class TestPutPremiumMovesWithTheShares(unittest.TestCase):
    """TJ-0788: account a's put delivers 100 shares (basis 90); they move
    to account b (an own-account move, IB ACATS rows out and in), and b
    sells them for 100. The premium travels with the lot: stock 0,
    option 10, as with no move."""

    @rule("US-BASIS-05", "US-OPT-05")
    def test_own_account_move(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(
                td, "usa",
                {"a": PUT_ASSIGNED,
                 "b": "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD 1 100 "
                      "0\n"},
                files={"inputs/a/transfers.csv":
                       _ib_transfer("USD", "2025-02-03", -100),
                       "inputs/b/transfers.csv":
                       _ib_transfer("USD", "2025-02-04", 100)})
            self.assertEqual(_total_gain(root, "b"), 10.0)
            self.assertEqual(_stats(root), {"long-shares": (1, 0.0),
                                            "written-options": (1, 10.0),
                                            "total": (2, 10.0)})
            # Account b alone: its sale still carries a's premium (the
            # other taxable accounts' books are read for the move).
            self.assertEqual(_stats(root, "b"), {"long-shares": (1, 0.0),
                                                 "total": (1, 0.0)})


class TestMultSizesTheAssignment(unittest.TestCase):
    """TJ-0789: ticker.map says `MULT SYN 10`. Two mini puts written for
    20 (the write row says x10, the assignment row does not) deliver 10
    shares on Jan 17 and 10 on Jan 21; the first 10 are sold at the
    strike. The option made 20 and the sale breaks even."""

    PUT = "SYN250117P00010000.TO"

    def _rows(self, stamp: str) -> str:
        return (f"BUYSELL 2025-01-02 10:00:00 {self.PUT} -2 CAD 1 20 0 x10\n"
                f"ASSIGN 2025-01-17 10:00:00 {self.PUT} 2 CAD 0 0 0{stamp}\n"
                "ASSIGN 2025-01-17 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-01-20 10:00:00 SYN.TO -10 CAD 10 100 0\n"
                "ASSIGN 2025-01-21 10:00:00 SYN.TO 10 CAD 10 100 0\n")

    @rule("CA-RPT-16", "CA-OPT-08")
    def test_assignment_row_without_its_size(self):
        for stamp in ("", " x10"):
            with self.subTest(stamp=stamp), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, "canada", {"margin": self._rows(stamp)},
                                tmap="MULT SYN 10\n")
                self.assertEqual(_stats(root), {"long-shares": (1, 0.0),
                                                "written-options": (1, 20.0),
                                                "total": (2, 20.0)})

    @rule("CA-PLAN-07")
    def test_option_yield_reads_the_map(self):
        # A write with no size on its rows: the map's 10 shares a
        # contract, not 100, are the notional at risk.
        put = "SYN251219P00010000.TO"
        got = {}
        for stamp, tmap in (("", "MULT SYN 10\n"), (" x10", "")):
            with tempfile.TemporaryDirectory() as td:
                root = _project(td, "canada", {"margin": (
                    f"BUYSELL 2025-01-02 10:00:00 {put} -1 CAD 1 10 0{stamp}\n"
                    f"BUYSELL 2025-12-19 16:00:00 {put} 1 CAD 0 0 0{stamp}\n"
                )}, tmap=tmap)
                r = _cli(root, "option-yield", "2025", "--json")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                got[tmap] = [p["avg_notional"]
                             for p in json.loads(r.stdout)["parts"]]
        self.assertEqual(got["MULT SYN 10\n"], got[""])


class TestNoonSplitTakesEffectAtTheOpen(unittest.TestCase):
    """TJ-0697: a put written for 10; a 2:1 split on May 1 renames it to
    the half-strike series; one new contract is bought back at 10:00 for
    2, the other expires. Stamped 09:30 or 12:00, the split comes first
    in a Canadian project: a buy-back of 3 and an expiry of 5."""

    def _rows(self, clock: str) -> str:
        return ("BUYSELL 2025-01-02 10:00:00 SYN251219P00001000.TO -1 CAD "
                "0.1 10 0\n"
                f"SPLIT 2025-05-01 {clock} SYN251219P00001000.TO "
                "SYN251219P00000500.TO 2\n"
                "BUYSELL 2025-05-01 10:00:00 SYN251219P00000500.TO 1 CAD "
                "0.02 2 0\n"
                "BUYSELL 2025-12-19 16:00:00 SYN251219P00000500.TO 1 CAD "
                "0 0 0\n")

    @rule("CA-DATE-14", "CA-RPT-16")
    def test_stats_and_option_yield(self):
        for clock in ("09:30:00", "12:00:00"):
            with self.subTest(clock=clock), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, "canada", {"margin": self._rows(clock)})
                self.assertEqual(_total_gain(root), 8.0)
                self.assertEqual(_stats(root), {"written-options": (2, 8.0),
                                                "total": (2, 8.0)})
                r = _cli(root, "option-yield", "2025", "--json")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                self.assertEqual(
                    [(c["how"], c["pnl"]) for c in
                     json.loads(r.stdout)["closed"]],
                    [("bought back", 3.0), ("expired", 5.0)])


class TestShareCreditsUnit(unittest.TestCase):
    """The lot replay alone (lib/trade_stats share_credits)."""

    def _row(self, d, q, net, rid="", sym="SYN.US", action="BUYSELL",
             acct="margin", **kw):
        return dict(dict(date=d, time="10:00:00", account=acct, symbol=sym,
                         action=action, quantity=q, net_amount=net,
                         price=abs(net / q) if q else 0, id=rid), **kw)

    def test_fifo_and_average_cost_of_a_call_short(self):
        # 50 held; a plain sale of 75 closes them and opens a short of
        # 25; the call's leg sells 75 more, all short (the gains rows'
        # word), its premium of 30 with them. A cover of 25, then one of
        # 75: first in, first out the first covers the plain short and
        # the second carries the premium; in one average-cost pool each
        # cover carries its share of it.
        from taxjson.lib import trade_stats as TS
        rows = [self._row("2025-01-02", 50, 50, "buy"),
                self._row("2025-01-10", -75, 75, "s0"),
                self._row("2025-01-17", -75, 75, "leg", action="ASSIGN"),
                self._row("2025-03-03", 25, 25, "c1"),
                self._row("2025-03-04", 75, 75, "c2")]
        cr = [dict(account="margin", underlying="SYN.US", right="C",
                   shares=75, amount=30, date="2025-01-17", strike=1,
                   delivered=75, option=0)]
        longs = {"margin": {"s0": 50}}
        covers = {"margin": {"c1": 25, "c2": 75}}
        fifo = TS.share_credits({"margin": rows}, cr, covers, longs,
                                lambda a, r: True, fifo=True)
        self.assertEqual({k: round(v, 6) for k, v in fifo.items()},
                         {("margin", "c2"): 30.0})
        avg = TS.share_credits({"margin": rows}, cr, covers, longs,
                               lambda a, r: True, fifo=False)
        self.assertEqual({k: round(v, 6) for k, v in avg.items()},
                         {("margin", "c1"): 7.5, ("margin", "c2"): 22.5})

    def test_without_gains_rows_a_call_leg_keeps_its_premium(self):
        # A book alone (no gains rows to say what a sale closed): the
        # leg is read as a sale of shares held, as before.
        from taxjson.lib import trade_stats as TS
        rows = [self._row("2025-01-17", -100, 100, "leg", action="ASSIGN")]
        cr = [dict(account="margin", underlying="SYN.US", right="C",
                   shares=100, amount=10, date="2025-01-17", strike=1,
                   delivered=100, option=0)]
        self.assertEqual(TS.credits_by_sale(rows, cr), {"leg": 10.0})


if __name__ == "__main__":
    unittest.main()
