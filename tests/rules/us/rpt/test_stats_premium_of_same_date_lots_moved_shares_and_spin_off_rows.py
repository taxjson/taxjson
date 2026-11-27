"""Fix round 2026-10-29 (round 7, wave E), group stats: trading
statistics and option reports (lib/trade_stats, lib/option_yield,
ccd-sum, winners), review output only.

TJ-1217  the denied-loss cap ignored an assigned put's premium in the
         same ACB.
TJ-1218  a US deemed gain on one lot took the premium of another lot of
         the same date.
TJ-1219  a return of capital reversed by a positive ADJUST left the
         denied-loss tally cut.
TJ-1220  a Canadian year view missed the deemed gain of an earlier
         year's return of capital on a nil ACB.
TJ-1221  a US view of one account missed a deemed gain booked in the
         other account before an own-account move.
TJ-1222  a Canadian year view missed a short cover of an earlier year.
TJ-1223  option-yield ignored a MULT line of 100 on a series renamed
         into it.
TJ-1254  a US spin-off's part of the premium when the parent's timeline
         row is not next to the new shares' row.
TJ-1255  a US year view after an earlier year's deemed gain of the
         account the shares moved to.
TJ-1256  a grant-timing series written in two years, one contract
         bought back: the same-year expiry.

Every fixture is synthetic: invented tickers (SYN*, ZZ*), account names
and amounts.

Formerly: test_fix_1029_r7e_stats
"""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _project(td: str, country: str, trades: dict, tmap: str = "",
             files: dict = None, year: int = 2025, extra: str = "",
             run: bool = True) -> Path:
    root = Path(td) / "p"
    cur = "CAD" if country == "canada" else "USD"
    for a in trades:
        (root / "inputs" / a).mkdir(parents=True)
        if trades[a]:
            (root / "inputs" / a / "trades.tt").write_text(trades[a])
    for rel, text in (files or {}).items():
        (root / rel).write_text(text)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n' + extra
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in trades))
    (root / "ticker.map").write_text(tmap)
    if run:
        _run(root)
    return root


def _run(root: Path) -> None:
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def _json(root: Path, *args: str) -> dict:
    r = _cli(root, *args, "--json")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(r.stdout)


def _pl(doc: dict, cls: str = "") -> float:
    if not cls:
        return doc["total"]["net_pl"]
    return (doc["classes"].get(cls) or {"net_pl": 0.0})["net_pl"]


def _ib_transfer(day: str, q: int, cur: str = "USD", sym: str = "SYN") -> str:
    """An IB activity statement with one ACATS share transfer row."""
    return ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers LLC\n"
            "Transfers,Header,Asset Category,Currency,Symbol,Date,Type,"
            "Direction,Xfer Company,Xfer Account,Qty,Xfer Price,"
            "Market Value,Realized P/L,Cash Amount,Code\n"
            f"Transfers,Data,Stocks,{cur},{sym},{day},ACATS,"
            f"{'Out' if q < 0 else 'In'},Other Broker,99900001,{q},0,{q},"
            "0,0,\n")


def _put(year: int, sfx: str, cur: str) -> str:
    """A put written for 20, assigned into 100 shares at 1 (cost 80)."""
    yy = str(year)[2:]
    return (f"BUYSELL {year}-01-02 10:00:00 SYN{yy}0117P00001000.{sfx} -1 "
            f"{cur} .2 20 0\n"
            f"ASSIGN {year}-01-17 09:30:00 SYN{yy}0117P00001000.{sfx} 1 "
            f"{cur} 0 0 0\n"
            f"ASSIGN {year}-01-17 10:00:00 SYN.{sfx} 100 {cur} 1 100 0\n")


class TestUsSameDateLots(unittest.TestCase):
    """TJ-1218: the put's lot (cost 80) and a purchase of the same date
    (cost 10), a return of capital across them: only a lot it passes the
    cost of gives up its premium.

    Formerly: test_fix_1029_r7e_stats.TestUsSameDateLots"""

    def _rows(self, day: str, roc: int = 100) -> str:
        return (_put(2025, "US", "USD")
                + f"BUYSELL {day} 11:00:00 SYN.US 100 USD .1 10 0\n"
                f"ADJUST 2025-06-02 10:00:00 SYN.US USD -{roc} type=roc\n"
                "BUYSELL 2025-07-02 10:00:00 SYN.US -100 USD .4 40 0\n"
                "BUYSELL 2025-07-03 10:00:00 SYN.US -100 USD .4 40 0\n")

    @rule(R.US_ROC_02, R.US_RPT_12)
    def test_same_date_as_the_next_day(self):
        # 50 each: the put's lot sells at -10, the other's at 40.
        for day in ("2025-01-17", "2025-01-18"):
            with self.subTest(day=day), tempfile.TemporaryDirectory() as td:
                root = _project(td, "usa", {"m": self._rows(day)})
                doc = _json(root, "stats", "2025")
                self.assertEqual(_pl(doc, "long-shares"), 30.0)
                self.assertEqual(_pl(doc), 50.0)

    @rule(R.US_ROC_02, R.US_RPT_12)
    def test_both_beyond_their_cost(self):
        # 100 each: the put's lot's 20 beyond its cost takes all its
        # premium, the other's 90 holds none. Both sales keep their 40.
        for day in ("2025-01-17", "2025-01-18"):
            with self.subTest(day=day), tempfile.TemporaryDirectory() as td:
                root = _project(td, "usa", {"m": self._rows(day, 200)})
                self.assertEqual(
                    _pl(_json(root, "stats", "2025"), "long-shares"), 80.0)


class TestUsOtherAccountsDeemedGain(unittest.TestCase):
    """TJ-1221, TJ-1255: a put's shares move between two accounts, with a
    return of capital beyond their cost in one of them.

    Formerly: test_fix_1029_r7e_stats.TestUsOtherAccountsDeemedGain"""

    @staticmethod
    def _files(out: str, in_: str) -> dict:
        return {"inputs/a/transfers.csv": _ib_transfer(out, -100),
                "inputs/b/transfers.csv": _ib_transfer(in_, 100)}

    @rule(R.US_ROC_02, R.US_RPT_12, R.US_BASIS_05)
    def test_same_year_view_of_the_receiving_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "a": _put(2025, "US", "USD")
                + "ADJUST 2025-01-20 10:00:00 SYN.US USD -100 type=roc\n",
                "b": "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD .4 40 0\n"},
                files=self._files("2025-02-03", "2025-02-04"))
            self.assertEqual(_pl(_json(root, "stats", "2025"),
                                 "long-shares"), 40.0)
            self.assertEqual(_pl(_json(root, "stats", "2025", "b"),
                                 "long-shares"), 40.0)

    @rule(R.US_RPT_12, R.US_BASIS_05)
    def test_no_return_of_capital_keeps_the_premium(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "a": _put(2025, "US", "USD"),
                "b": "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD .4 40 0\n"},
                files=self._files("2025-02-03", "2025-02-04"))
            self.assertEqual(_pl(_json(root, "stats", "2025", "b"),
                                 "long-shares"), -60.0)
            self.assertFalse(list((root / "work").glob("*_gains_all.json")))

    @rule(R.US_ROC_02, R.US_RPT_12)
    def test_earlier_year_roc_in_the_account_moved_to(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "a": _put(2024, "US", "USD"),
                "b": "ADJUST 2024-06-03 10:00:00 SYN.US USD -100 type=roc\n"
                     "BUYSELL 2025-07-02 10:00:00 SYN.US -100 USD .4 40 0\n"},
                files=self._files("2024-02-01", "2024-02-02"))
            self.assertEqual(_pl(_json(root, "stats", "all"),
                                 "long-shares"), 40.0)
            self.assertEqual(_pl(_json(root, "stats", "2025"),
                                 "long-shares"), 40.0)
            self.assertEqual(_pl(_json(root, "stats", "2025", "b"),
                                 "long-shares"), 40.0)

    @rule(R.US_RPT_12)
    def test_no_roc_builds_no_full_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "a": _put(2024, "US", "USD"),
                "b": "BUYSELL 2025-07-02 10:00:00 SYN.US -100 USD .4 40 0\n"},
                files=self._files("2024-02-01", "2024-02-02"))
            _json(root, "stats", "2025", "b")
            self.assertFalse(list((root / "work").glob("*_gains_all.json")))


SPIN = ("Statement,Header,Field Name,Field Value\n"
        "Statement,Data,BrokerName,Interactive Brokers\n"
        "Corporate Actions,Header,Asset Category,Currency,"
        "Report Date,Date/Time,Description,Quantity,Proceeds,"
        "Value,Realized P/L,Code\n")
SPIN_ROW = ("Corporate Actions,Data,Stocks,USD,2025-02-03,"
            '"2025-02-03, 10:00:00","ZZQQ(US0000000921) Spinoff 1 for 1 '
            '({c}, {c} INC, {isin})",100,0,100,0,\n')


def _elect_all(root: Path, accts, hint: str) -> None:
    """Elect tax-free each spin-off pending in `accts`, one run each."""
    for acct in accts:
        _cli(root, "run", "--no-input")
        pend = (root / "work" / "pending_elections.json").read_text()
        for cid in sorted(set(re.findall(
                r'"(\d{8}-zz[a-z]+-zz[a-z]+-[0-9a-f]+)"', pend))):
            r = _cli(root, "elections", acct, "--set", f"{cid}=tax_free_355",
                     "--hint", hint)
            assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    _run(root)


class TestUsSpinOffRowsNotAdjacent(unittest.TestCase):
    """TJ-1254: the premium's part of a US spin-off, read from the
    parent's own drop on its day.

    Formerly: test_fix_1029_r7e_stats.TestUsSpinOffRowsNotAdjacent"""

    PUT = "ZZQQ250321P00002000.US"
    HEAD = (f"BUYSELL 2025-01-02 10:00:00 {PUT} -1 USD .2 20 0\n"
            f"ASSIGN 2025-01-17 10:00:00 {PUT} 1 USD 0 0 0\n"
            "ASSIGN 2025-01-17 10:00:00 ZZQQ.US 100 USD 2 200 0\n")
    ONE = SPIN + SPIN_ROW.format(c="ZZKK", isin="US0000000922")

    def _by_symbol(self, root: Path) -> dict:
        doc = _json(root, "win-rates", "2025", "--by", "symbol")
        return {g["key"]: g["net_pl"] for g in doc["rows"]}

    @rule(R.US_RPT_12, R.US_CORP_07)
    def test_two_accounts_each_report_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "margin": self.HEAD
                + "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0\n"
                "BUYSELL 2025-03-04 10:00:00 ZZKK.US -100 USD 1 100 0\n",
                "other": "BUYSELL 2024-12-02 10:00:00 ZZQQ.US 100 USD 2 200 "
                "0\n"
                "BUYSELL 2025-04-03 10:00:00 ZZQQ.US -100 USD 2 200 0\n"
                "BUYSELL 2025-04-04 10:00:00 ZZKK.US -100 USD 1 100 0\n"},
                files={"inputs/margin/spin.csv": self.ONE,
                       "inputs/other/spin.csv": self.ONE}, run=False)
            _elect_all(root, ("margin", "other"), "allocated_acb=90")
            ls = _json(root, "stats", "2025")["classes"]["long-shares"]
            self.assertEqual((ls["trades"], ls["wins"], ls["losses"],
                              ls["net_pl"]), (4, 2, 0, 100.0))

    @rule(R.US_RPT_12, R.US_CORP_07)
    def test_a_later_return_of_capital_of_the_same_amount(self):
        # 90 in the spin-off, then a return of capital of 90 that day.
        # The drop at the new shares' row is the spin-off's: half the
        # premium goes.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {"margin": self.HEAD
                + "ADJUST 2025-02-03 11:00:00 ZZQQ.US USD -90 type=roc\n"
                "BUYSELL 2025-03-04 10:00:00 ZZKK.US -100 USD 1 100 0\n"},
                files={"inputs/margin/spin.csv": self.ONE}, run=False)
            _elect_all(root, ("margin",), "allocated_acb=90")
            self.assertAlmostEqual(self._by_symbol(root)["ZZKK.US"], 0.0,
                                   places=1)

    @rule(R.US_RPT_12, R.US_CORP_07)
    def test_two_children(self):
        with tempfile.TemporaryDirectory() as td:
            spin = (self.ONE.replace("ZZKK", "ZZKA")
                    + SPIN_ROW.format(c="ZZKB", isin="US0000000923"))
            root = _project(td, "usa", {"margin": self.HEAD
                + "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0\n"
                "BUYSELL 2025-03-04 10:00:00 ZZKA.US -100 USD 1 100 0\n"
                "BUYSELL 2025-03-05 10:00:00 ZZKB.US -100 USD 1 100 0\n"},
                files={"inputs/margin/spin.csv": spin}, run=False)
            _elect_all(root, ("margin",), "allocated_acb=60")
            got = self._by_symbol(root)
            self.assertAlmostEqual(got["ZZKA.US"], 33.33, places=1)
            self.assertAlmostEqual(got["ZZKB.US"], 33.33, places=1)

    @rule(R.US_RPT_12, R.US_CORP_07)
    def test_a_same_day_purchase_and_return_of_capital(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {"margin": self.HEAD
                + "BUYSELL 2025-02-03 09:00:00 ZZKK.US 10 USD 1 10 0\n"
                "ADJUST 2025-02-03 09:01:00 ZZQQ.US USD -30 type=roc\n"
                "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0\n"
                "BUYSELL 2025-03-04 10:00:00 ZZKK.US -100 USD 1 100 0\n"},
                files={"inputs/margin/spin.csv": self.ONE}, run=False)
            _elect_all(root, ("margin",), "allocated_acb=90")
            self.assertAlmostEqual(self._by_symbol(root)["ZZQQ.US"], 52.0,
                                   places=1)


if __name__ == "__main__":
    unittest.main()
