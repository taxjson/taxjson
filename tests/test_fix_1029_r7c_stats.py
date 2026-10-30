"""Fix round 2026-10-29 (round 7, wave C), group stats: the assigned
option premium and the earlier denied loss in `stats` / `win-rates`
(lib/trade_stats), and the written options of `option-yield`
(lib/option_yield).

TJ-0994  a Canadian view of one account: the other account's short
         cover (a put's delivery) was read as shares into the pool, so
         its premium came out of the shown account's later sale.
TJ-0995  a US wheel (a put's shares sold by a written call): both
         premiums come out of the one sale (fixed by wave B's lots).
TJ-1003  option-yield: a series renamed into one of another contract
         size kept the old size in its notional.
TJ-1005  a US split stamped after the morning assignment on its day
         scales the premium's shares (fixed by wave B's lots).
TJ-1006  a return of capital beyond the ACB: the deemed gain took the
         premium the ACB held; the later sale still gave it up.
TJ-1007  a futures option assigned into the future: the future's gain
         holds the premium, also counted as the option's P/L.
TJ-1017  a return of capital beyond the ACB took the earlier denied
         loss's addition with it; the sale still added it back.
TJ-1019  a spin-off's share of the premium was read off an unrelated
         same-day return of capital of the same amount.

Every fixture is synthetic: invented tickers (SYN*), account names and
amounts.
"""
import json
import os
import re
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


def _write(td: str, country: str, trades: dict, tmap: str = "",
           files: dict = None) -> Path:
    """A project of `country` whose accounts (all taxable) hold the .tt
    lines of `trades` ({account: text}), not yet run."""
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
    return root


def _run(root: Path) -> Path:
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return root


def _project(td: str, country: str, trades: dict, tmap: str = "",
             files: dict = None) -> Path:
    return _run(_write(td, country, trades, tmap, files))


def _stats(root: Path, *args: str) -> dict:
    """{class: (trades, net P/L)} of the classes with a trade, and
    'total'."""
    r = _cli(root, "stats", "2025", *args, "--json")
    assert r.returncode == 0, r.stderr[-2000:]
    doc = json.loads(r.stdout)
    out = {c: (v["trades"], v["net_pl"]) for c, v in doc["classes"].items()
           if v["trades"]}
    out["total"] = (doc["total"]["trades"], doc["total"]["net_pl"])
    return out


def _gains(root: Path, acct: str = "margin") -> list:
    doc = json.loads((root / "work" / f"{acct}_gains_wash.json")
                     .read_text())
    return [t for t in doc["transactions"] if "gain" in t]


class TestFilteredAccountKeepsOtherCovers(unittest.TestCase):
    """TJ-0994: account b covers its short with a put's 100 shares
    (premium 20, in the cover's gain); account a then buys 100 at 100
    and sells at 110. `stats a` / `win-rates a`: a's one trade is the
    10 win the all-account view counts, not a 10 loss."""

    @rule("CA-RPT-16")
    def test_view_of_one_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {
                "a": ("BUYSELL 2025-03-03 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                      "BUYSELL 2025-04-03 10:00:00 SYN.TO -100 CAD 1.1 110 "
                      "0\n"),
                "b": ("BUYSELL 2025-01-02 10:00:00 SYN.TO -100 CAD 1 100 0\n"
                      "BUYSELL 2025-01-03 10:00:00 SYN250117P00001000.TO -1 "
                      "CAD .2 20 0\n"
                      "ASSIGN 2025-01-17 10:00:00 SYN250117P00001000.TO 1 "
                      "CAD 0 0 0\n"
                      "ASSIGN 2025-01-17 11:00:00 SYN.TO 100 CAD 1 100 0\n")})
            everyone = _stats(root)
            self.assertEqual(everyone["long-shares"], (1, 10.0))
            self.assertEqual(_stats(root, "a"),
                             {"long-shares": (1, 10.0), "total": (1, 10.0)})
            r = _cli(root, "win-rates", "2025", "a", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = json.loads(r.stdout)
            self.assertEqual(doc["total"]["net_pl"], 10.0)
            self.assertEqual(doc["total"]["wins"], 1)


class TestWheelBothPremiums(unittest.TestCase):
    """TJ-0995 (fixed before, wave B's lots): a put written for 20
    delivers 100 shares at 1; a call written for 10 takes them at 1.2.
    Cash and the gains: 50 = shares 20 + options 30, in both countries."""

    @rule("US-RPT-12")
    def test_usa(self):
        self._wheel("usa", "US", "USD")

    @rule("CA-RPT-16")
    def test_canada(self):
        self._wheel("canada", "TO", "CAD")

    def _wheel(self, country: str, sfx: str, cur: str):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": (
                f"BUYSELL 2025-01-02 10:00:00 SYN250117P00001000.{sfx} "
                f"-1 {cur} .2 20 0\n"
                f"ASSIGN 2025-01-17 10:00:00 SYN250117P00001000.{sfx} 1 "
                f"{cur} 0 0 0\n"
                f"ASSIGN 2025-01-17 11:00:00 SYN.{sfx} 100 {cur} 1 100 "
                "0\n"
                f"BUYSELL 2025-03-03 10:00:00 SYN250321C00001200.{sfx} "
                f"-1 {cur} .1 10 0\n"
                f"ASSIGN 2025-03-21 10:00:00 SYN250321C00001200.{sfx} 1 "
                f"{cur} 0 0 0\n"
                f"ASSIGN 2025-03-21 11:00:00 SYN.{sfx} -100 {cur} 1.2 "
                "120 0\n")})
            st = _stats(root)
            self.assertEqual(st["long-shares"], (1, 20.0))
            self.assertEqual(st["written-options"], (2, 30.0))
            self.assertEqual(st["total"], (3, 50.0))


class TestSameDaySplitAfterAssignment(unittest.TestCase):
    """TJ-1005 (fixed before, wave B's lots): a US put (20) assigned at
    09:30 into 100 shares at 1, a 2:1 split stamped noon that day, half
    the 200 shares sold for 60: shares 10, option 20, as with the split
    on the next day."""

    @rule("US-RPT-12")
    def test_split_later_that_day(self):
        for day in ("2025-01-17", "2025-01-18"):
            with self.subTest(split=day), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, "usa", {"margin": (
                    "BUYSELL 2025-01-02 10:00:00 SYN250117P00001000.US -1 "
                    "USD .2 20 0\n"
                    "ASSIGN 2025-01-17 09:30:00 SYN250117P00001000.US 1 USD "
                    "0 0 0\n"
                    "ASSIGN 2025-01-17 10:00:00 SYN.US 100 USD 1 100 0\n"
                    f"SPLIT {day} 12:00:00 SYN.US SYN.US 2\n"
                    "BUYSELL 2025-01-21 10:00:00 SYN.US -100 USD .6 60 0\n")})
                st = _stats(root)
                self.assertEqual(st["long-shares"], (1, 10.0))
                self.assertEqual(st["total"], (2, 30.0))


class TestOptionYieldRenamedSeriesSize(unittest.TestCase):
    """TJ-1003: a call written on SYN at strike 3 (100 shares: notional
    300) is renamed by a ratio-1 split into SYN1 at strike 2, a series
    the ticker.map sizes at 150 (notional 300 still). Its yield is the
    unrenamed control's: average notional 148.77, 12.0994% a year."""

    def _yield(self, rows: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": rows},
                            tmap="MULT SYN1 150\n")
            r = _cli(root, "option-yield", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            part, = json.loads(r.stdout)["parts"]
            return {k: part[k] for k in ("avg_notional", "annualized",
                                         "realized")}

    def test_renamed_into_a_larger_contract(self):
        renamed = self._yield(
            "BUYSELL 2025-01-02 10:00:00 SYN251219C00003000.TO -1 CAD .3 "
            "30 0\n"
            "SPLIT 2025-04-01 09:30:00 SYN251219C00003000.TO "
            "SYN1251219C00002000.TO 1\n"
            "BUYSELL 2025-07-02 10:00:00 SYN1251219C00002000.TO 1 CAD .08 "
            "12 0\n")
        control = self._yield(
            "BUYSELL 2025-01-02 10:00:00 SYN251219C00003000.TO -1 CAD .3 "
            "30 0\n"
            "BUYSELL 2025-07-02 10:00:00 SYN251219C00003000.TO 1 CAD .12 "
            "12 0\n")
        self.assertEqual(control, {"avg_notional": 148.77,
                                   "annualized": 0.120994,
                                   "realized": 18.0})
        self.assertEqual(renamed, control)

    def test_split_keeps_the_size(self):
        """A plain 2:1 split of the series (no rename): the size stays,
        the notional with it."""
        split = self._yield(
            "BUYSELL 2025-01-02 10:00:00 SYN251219C00003000.TO -1 CAD .3 "
            "30 0\n"
            "SPLIT 2025-04-01 09:30:00 SYN251219C00003000.TO "
            "SYN251219C00003000.TO 2\n"
            "BUYSELL 2025-07-02 10:00:00 SYN251219C00003000.TO 2 CAD .06 "
            "12 0\n")
        self.assertEqual(split["avg_notional"], 148.77)


# A put written for 20, assigned into 100 shares at 1 (ACB 80).
def _put_assigned(sfx: str, cur: str) -> str:
    return (f"BUYSELL 2025-01-02 10:00:00 SYN250117P00001000.{sfx} -1 "
            f"{cur} .2 20 0\n"
            f"ASSIGN 2025-01-17 09:30:00 SYN250117P00001000.{sfx} 1 {cur} "
            "0 0 0\n"
            f"ASSIGN 2025-01-17 10:00:00 SYN.{sfx} 100 {cur} 1 100 0\n")


class TestReturnOfCapitalTakesThePremium(unittest.TestCase):
    """TJ-1006: after the put above, a return of capital of 100 (20
    beyond the ACB: a deemed gain, no trade) and a sale for 40. The
    premium was in the ACB the deemed gain took: the sale's 40 is the
    stock's own, the option's 20 apart, 60 in all — as cash has it. A
    return of capital of 50 leaves it in the ACB: shares -10."""

    @rule("CA-RPT-16")
    def test_canada(self):
        self._roc("canada", "TO", "CAD")

    @rule("US-RPT-12")
    def test_usa(self):
        self._roc("usa", "US", "USD")

    def _roc(self, country: str, sfx: str, cur: str):
        for roc, shares in ((100, 40.0), (90, 30.0), (50, -10.0)):
            with self.subTest(country=country, roc=roc), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, country, {"margin": (
                    _put_assigned(sfx, cur)
                    + f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} "
                      f"-{roc} type=roc\n"
                    + f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -100 "
                      f"{cur} .4 40 0\n")})
                st = _stats(root)
                self.assertEqual(st["long-shares"], (1, shares))
                self.assertEqual(st["written-options"], (1, 20.0))

    @rule("CA-RPT-16")
    def test_pool_sold_in_two(self):
        """Canada: the pool sold in two halves after the deemed gain:
        neither gives up the premium."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": (
                _put_assigned("TO", "CAD")
                + "ADJUST 2025-06-02 10:00:00 SYN.TO CAD -100 type=roc\n"
                + "BUYSELL 2025-07-02 10:00:00 SYN.TO -50 CAD .4 20 0\n"
                + "BUYSELL 2025-07-03 10:00:00 SYN.TO -50 CAD .4 20 0\n")})
            st = _stats(root)
            self.assertEqual(st["long-shares"], (2, 40.0))
            self.assertEqual(st["total"], (3, 60.0))

    @rule("CA-RPT-16")
    def test_deemed_gain_of_an_earlier_year(self):
        """Canada: the delivery and the return of capital in 2024, the
        sale in 2025: the year's gains hold no deemed row, the run's
        cost timeline has the nil ACB."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": (
                "BUYSELL 2024-01-02 10:00:00 SYN240119P00001000.TO -1 CAD "
                ".2 20 0\n"
                "ASSIGN 2024-01-19 09:30:00 SYN240119P00001000.TO 1 CAD 0 0 "
                "0\n"
                "ASSIGN 2024-01-19 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                "ADJUST 2024-06-03 10:00:00 SYN.TO CAD -100 type=roc\n"
                "BUYSELL 2025-07-02 10:00:00 SYN.TO -100 CAD .4 40 0\n")})
            self.assertEqual(_stats(root), {"long-shares": (1, 40.0),
                                            "total": (1, 40.0)})


class TestFuturesOptionAssigned(unittest.TestCase):
    """TJ-1007: a futures put written for 20 is assigned into the future
    at 100 and the future sold at 110. The engines book one futures gain
    of 30 (the premium folded in): the future's own move is 10, the
    option's 20 — 30 in all, as when the option expires instead."""

    @rule("US-RPT-12")
    def test_usa(self):
        self._future("usa", "USD")

    @rule("CA-RPT-16")
    def test_canada(self):
        self._future("canada", "CAD")

    def _future(self, country: str, cur: str):
        for assigned in (True, False):
            close = "ASSIGN" if assigned else "BUYSELL"
            with self.subTest(country=country, assigned=assigned), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, country, {"margin": (
                    "BUYSELL 2025-01-02 10:00:00 F:SYN250117P00001000.US "
                    f"-1 {cur} .2 20 0\n"
                    f"{close} 2025-01-17 09:30:00 "
                    f"F:SYN250117P00001000.US 1 {cur} 0 0 0\n"
                    f"BUYSELL 2025-01-17 10:00:00 F:SYNG5.US 1 {cur} 1 "
                    "100 0\n"
                    f"BUYSELL 2025-01-21 10:00:00 F:SYNG5.US -1 {cur} 1.1 "
                    "110 0\n")}, tmap="MULT F:SYN 100\n")
                st = _stats(root)
                self.assertEqual(st["futures"], (1, 10.0))
                self.assertEqual(st["written-options"], (1, 20.0))
                self.assertEqual(st["total"], (2, 30.0))


class TestReturnOfCapitalTakesTheDeferredLoss(unittest.TestCase):
    """TJ-1017: 10 shares bought for 100, sold for 80, bought back for
    100 the next day (a denied loss of 20 added to the new ACB: 120);
    a return of capital of 130 (10 beyond the ACB) and a sale for 50.
    The ACB that held the denied loss is gone: the sale is 50, the loss
    -20, 30 in all — the same with the loss allowed (ALLOWLOSS). A
    return of capital of 50 leaves the addition in the ACB: -20."""

    @rule("CA-RPT-16")
    def test_canada(self):
        self._roc("canada", "TO", "CAD")

    @rule("US-RPT-12")
    def test_usa(self):
        self._roc("usa", "US", "USD")

    def _roc(self, country: str, sfx: str, cur: str):
        for roc, allowed, net in ((130, False, 30.0), (130, True, 30.0),
                                  (50, False, -20.0), (50, True, -20.0)):
            rows = (f"BUYSELL 2025-01-02 10:00:00 SYN.{sfx} 10 {cur} 10 "
                    "100 0\n"
                    f"BUYSELL 2025-02-03 10:00:00 SYN.{sfx} -10 {cur} 8 "
                    "80 0\n"
                    f"BUYSELL 2025-02-04 10:00:00 SYN.{sfx} 10 {cur} 10 "
                    "100 0\n"
                    f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -{roc} "
                    "type=roc\n"
                    f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -10 {cur} 5 "
                    "50 0\n")
            if allowed:
                rows += (f"ALLOWLOSS 2025-02-03 SYN.{sfx} "
                         'reason="synthetic control"\n')
            with self.subTest(country=country, roc=roc,
                              allowed=allowed), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, country, {"margin": rows})
                self.assertEqual(_stats(root)["total"], (2, net))

    @rule("CA-RPT-16")
    def test_tally_capped_canada(self):
        self._capped("canada", "TO", "CAD")

    @rule("US-RPT-12")
    def test_tally_capped_usa(self):
        self._capped("usa", "US", "USD")

    def _capped(self, country: str, sfx: str, cur: str):
        """The ACB's deferred-loss tally never exceeds the ACB: a
        return of capital of 110 leaves an ACB of 10, all of it the
        denied loss; the sale's economic P/L is its proceeds."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": (
                f"BUYSELL 2025-01-02 10:00:00 SYN.{sfx} 10 {cur} 10 100 "
                "0\n"
                f"BUYSELL 2025-02-03 10:00:00 SYN.{sfx} -10 {cur} 8 80 "
                "0\n"
                f"BUYSELL 2025-02-04 10:00:00 SYN.{sfx} 10 {cur} 10 100 "
                "0\n"
                f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -110 "
                "type=roc\n"
                f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -10 {cur} 5 50 "
                "0\n")})
            sale, = [t for t in _gains(root)
                     if t["date"] == "2025-07-02"]
            self.assertAlmostEqual(sale["gain"], 40.0, places=6)
            self.assertAlmostEqual(
                sale.get("deferred_loss_recovered") or 0.0, 10.0,
                places=6)


_SPIN_CSV = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    "Corporate Actions,Header,Asset Category,Currency,Report Date,"
    "Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n"
    'Corporate Actions,Data,Stocks,CAD,2025-02-03,"2025-02-03, 10:00:00",'
    '"SYN(US0000000921) Spinoff 1 for 1 (CHLD, CHILD INC, US0000000922)",'
    "100,0,100,0,\n")


class TestSpinOffBesideSameDayRoc(unittest.TestCase):
    """TJ-1019: a put (20) delivers 100 shares at 2 (ACB 180); a return
    of capital of 60 at 08:00 (ACB 120), then at 10:00 a spin-off the
    election moves 60 of the 120 to the child. The parent sold for 100
    (cost 60, gain 40) carries half the premium: 30, the option 20 — as
    with the return of capital a day earlier."""

    @rule("CA-RPT-16")
    def test_same_day_roc(self):
        for roc_day in ("2025-02-03", "2025-01-31"):
            with self.subTest(roc=roc_day), \
                    tempfile.TemporaryDirectory() as td:
                root = _write(td, "canada", {"margin": (
                    "BUYSELL 2025-01-02 10:00:00 SYN250117P00002000.TO -1 "
                    "CAD .2 20 0\n"
                    "ASSIGN 2025-01-17 10:00:00 SYN250117P00002000.TO 1 CAD "
                    "0 0 0\n"
                    "ASSIGN 2025-01-17 10:00:00 SYN.TO 100 CAD 2 200 0\n"
                    f"ADJUST {roc_day} 08:00:00 SYN.TO CAD -60 type=roc\n"
                    "BUYSELL 2025-03-03 10:00:00 SYN.TO -100 CAD 1 100 0\n")},
                    files={"inputs/margin/spin.csv": _SPIN_CSV})
                first = _cli(root, "run", "--no-input")
                m = re.search(r"--set (\S+?)=<", first.stdout + first.stderr)
                self.assertIsNotNone(m, first.stdout[-2000:])
                r = _cli(root, "elect", "margin", "--set",
                         m[1] + "=rollover_s_86_1",
                         "--hint", "allocated_acb_cad=60")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                _run(root)
                sale, = [t for t in _gains(root) if t["date"] == "2025-03-03"]
                self.assertAlmostEqual(sale["gain"], 40.0, places=6)
                st = _stats(root)
                self.assertEqual(st["long-shares"], (1, 30.0))
                self.assertEqual(st["total"], (2, 50.0))


if __name__ == "__main__":
    unittest.main()
