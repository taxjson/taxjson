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


CA, US = ("canada", "TO", "CAD"), ("usa", "US", "USD")


def _loss_then_put(sfx: str, cur: str) -> str:
    """100 shares bought for 100 and sold for 80 (a loss of 20, denied);
    a put written for 20 delivers 100 at 1: an ACB of 100 less 20 plus
    20."""
    return (f"BUYSELL 2025-01-06 10:00:00 SYN.{sfx} 100 {cur} 1 100 0\n"
            f"BUYSELL 2025-01-10 10:00:00 SYN.{sfx} -100 {cur} .8 80 0\n"
            f"BUYSELL 2025-01-02 10:00:00 SYN250117P00001000.{sfx} -1 "
            f"{cur} .2 20 0\n"
            f"ASSIGN 2025-01-17 09:30:00 SYN250117P00001000.{sfx} 1 "
            f"{cur} 0 0 0\n"
            f"ASSIGN 2025-01-17 10:00:00 SYN.{sfx} 100 {cur} 1 100 0\n")


class TestDeniedLossCapWithAPremium(unittest.TestCase):
    """TJ-1217: `_loss_then_put`, a return of capital, a sale for 50. The
    total is the cash up to the 100 paid: what a return of capital pays
    beyond it stays out of the stock's P/L (the policy of TJ-1017)."""

    CASES = ((50, 0.0), (90, 40.0), (100, 50.0), (110, 50.0), (130, 50.0))

    def _go(self, c, roc: int, allowed: bool = False) -> float:
        country, sfx, cur = c
        rows = (_loss_then_put(sfx, cur)
                + f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -{roc} "
                "type=roc\n"
                f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -100 {cur} .5 50 0\n")
        if allowed:
            rows += f'ALLOWLOSS 2025-01-10 SYN.{sfx} reason="control"\n'
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": rows})
            return _pl(_json(root, "stats", "2025"))

    @rule("CA-RPT-16")
    def test_canada(self):
        for roc, total in self.CASES:
            with self.subTest(roc=roc):
                self.assertEqual(self._go(CA, roc), total)

    @rule("US-RPT-12")
    def test_usa(self):
        for roc, total in self.CASES:
            with self.subTest(roc=roc):
                self.assertEqual(self._go(US, roc), total)

    def _control(self, c):
        for roc, total in ((90, 40.0), (100, 50.0), (110, 50.0)):
            with self.subTest(roc=roc):
                self.assertEqual(self._go(c, roc, allowed=True), total)

    @rule("CA-RPT-16")
    def test_the_control_with_the_loss_allowed_canada(self):
        self._control(CA)

    @rule("US-RPT-12")
    def test_the_control_with_the_loss_allowed_usa(self):
        self._control(US)

    def _partial(self, c):
        # Half sold, then half: both carry their share.
        country, sfx, cur = c
        rows = (_loss_then_put(sfx, cur)
                + f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -90 "
                "type=roc\n"
                f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -50 {cur} .5 25 0\n"
                f"BUYSELL 2025-07-03 10:00:00 SYN.{sfx} -50 {cur} .5 25 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": rows})
            self.assertEqual(_pl(_json(root, "stats", "2025")), 40.0)

    @rule("CA-RPT-16")
    def test_a_partial_sale_canada(self):
        self._partial(CA)

    @rule("US-RPT-12")
    def test_a_partial_sale_usa(self):
        self._partial(US)

    def _purchase(self, c):
        # Another 100 at 1 after the return of capital of 90: all 200
        # sold for 100. Cash: -100 +80 +20 -100 +90 -100 +100 = -10.
        country, sfx, cur = c
        rows = (_loss_then_put(sfx, cur)
                + f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -90 "
                "type=roc\n"
                f"BUYSELL 2025-06-09 10:00:00 SYN.{sfx} 100 {cur} 1 100 0\n"
                f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -200 {cur} .5 100 "
                "0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": rows})
            self.assertEqual(_pl(_json(root, "stats", "2025")), -10.0)

    @rule("CA-RPT-16")
    def test_a_purchase_after_the_return_of_capital_canada(self):
        self._purchase(CA)

    @rule("US-RPT-12")
    def test_a_purchase_after_the_return_of_capital_usa(self):
        self._purchase(US)


class TestDeniedLossCapVariants(unittest.TestCase):
    """TJ-1217: the same total through a year end, two accounts of one
    pool and a rename; and what the sale row reports."""

    def _year_end(self, c):
        country, sfx, cur = c
        rows = (_loss_then_put(sfx, cur)
                + f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -100 "
                "type=roc\n"
                f"BUYSELL 2026-02-02 10:00:00 SYN.{sfx} -100 {cur} .5 50 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": rows}, year=2026)
            self.assertEqual(_pl(_json(root, "stats", "2026"),
                                 "long-shares"), 50.0 + 0.0)

    @rule("CA-RPT-16")
    def test_year_end_canada(self):
        self._year_end(CA)

    @rule("US-RPT-12")
    def test_year_end_usa(self):
        self._year_end(US)

    @rule("CA-RPT-16")
    def test_two_accounts_of_one_pool(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {
                "a": _loss_then_put("TO", "CAD")
                + "ADJUST 2025-06-02 10:00:00 SYN.TO CAD -100 type=roc\n",
                "b": "BUYSELL 2025-07-02 10:00:00 SYN.TO -100 CAD .5 50 0\n"})
            self.assertEqual(_pl(_json(root, "stats", "2025")), 50.0)
            self.assertEqual(_pl(_json(root, "stats", "2025", "b"),
                                 "long-shares"), 50.0)

    @rule("CA-RPT-16")
    def test_a_rename_and_a_split(self):
        # The pool renamed 2:1 after the return of capital: the same.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": (
                _loss_then_put("TO", "CAD")
                + "ADJUST 2025-06-02 10:00:00 SYN.TO CAD -100 type=roc\n"
                "SPLIT 2025-06-15 09:30:00 SYN.TO SYQ.TO 2\n"
                "BUYSELL 2025-07-02 10:00:00 SYQ.TO -200 CAD .25 50 0\n")})
            self.assertEqual(_pl(_json(root, "stats", "2025")), 50.0)

    @rule("US-RPT-12")
    def test_a_split_usa(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {"margin": (
                _loss_then_put("US", "USD")
                + "ADJUST 2025-06-02 10:00:00 SYN.US USD -100 type=roc\n"
                "SPLIT 2025-06-15 09:30:00 SYN.US SYN.US 2\n"
                "BUYSELL 2025-07-02 10:00:00 SYN.US -200 USD .25 50 0\n")})
            self.assertEqual(_pl(_json(root, "stats", "2025")), 50.0)

    def _no_premium(self, c):
        # No premium: the row reports the cut and stats does not use it
        # (TJ-1017's own figures stand).
        country, sfx, cur = c
        rows = (f"BUYSELL 2025-01-02 10:00:00 SYN.{sfx} 10 {cur} 10 100 0\n"
                f"BUYSELL 2025-02-03 10:00:00 SYN.{sfx} -10 {cur} 8 80 0\n"
                f"BUYSELL 2025-02-04 10:00:00 SYN.{sfx} 10 {cur} 10 100 0\n"
                f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -110 type=roc\n"
                f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -10 {cur} 5 50 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": rows})
            doc = json.loads((root / "work" / "margin_gains_wash.json")
                             .read_text())
            sale, = [t for t in doc["transactions"]
                     if t["date"] == "2025-07-02"]
            self.assertAlmostEqual(sale["deferred_loss_recovered"], 10.0, 6)
            self.assertAlmostEqual(sale["deferred_loss_cut"], 10.0, 6)
            self.assertEqual(_pl(_json(root, "stats", "2025")), 30.0)
            inv = _json(root, "stats", "2025")
            self.assertEqual(inv["total"]["trades"], 2)

    @rule("CA-RPT-16")
    def test_no_premium_canada(self):
        self._no_premium(CA)

    @rule("US-RPT-12")
    def test_no_premium_usa(self):
        self._no_premium(US)

    def _inventory(self, c):
        # The holding's tally, shown before a sale, is what the ACB holds.
        country, sfx, cur = c
        rows = (f"BUYSELL 2025-01-02 10:00:00 SYN.{sfx} 10 {cur} 10 100 0\n"
                f"BUYSELL 2025-02-03 10:00:00 SYN.{sfx} -10 {cur} 8 80 0\n"
                f"BUYSELL 2025-02-04 10:00:00 SYN.{sfx} 10 {cur} 10 100 0\n"
                f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -110 type=roc\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": rows})
            doc = json.loads((root / "work" / "margin_gains_wash.json")
                             .read_text())
            held, = [h for h in doc["inventory"]
                     if h["symbol"] == f"SYN.{sfx}"]
            self.assertAlmostEqual(held["total_cost"], 10.0, 6)
            self.assertAlmostEqual(held["deferred_wash"], 10.0, 3)

    @rule("CA-RPT-16")
    def test_inventory_canada(self):
        self._inventory(CA)

    @rule("US-RPT-12")
    def test_inventory_usa(self):
        self._inventory(US)


class TestRocReversed(unittest.TestCase):
    """TJ-1219: a denied loss of 20 in an ACB of 120; a return of capital
    of 110; then a positive ADJUST. Reversed, the ACB holds the whole
    addition again and the total equals cash (-100 + 80 - 100 + 50)."""

    def _total(self, c, back: str) -> float:
        country, sfx, cur = c
        rows = (f"BUYSELL 2025-01-02 10:00:00 SYN.{sfx} 10 {cur} 10 100 0\n"
                f"BUYSELL 2025-02-03 10:00:00 SYN.{sfx} -10 {cur} 8 80 0\n"
                f"BUYSELL 2025-02-04 10:00:00 SYN.{sfx} 10 {cur} 10 100 0\n"
                f"ADJUST 2025-06-02 10:00:00 SYN.{sfx} {cur} -110 type=roc\n"
                + back.format(sfx=sfx, cur=cur)
                + f"BUYSELL 2025-07-02 10:00:00 SYN.{sfx} -10 {cur} 5 50 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"margin": rows})
            return _pl(_json(root, "stats", "2025"))

    REV = "ADJUST 2025-06-20 10:00:00 SYN.{sfx} {cur} 110 type=roc\n"
    # 60 of the 110 reversed: 50 stands, an ACB of 70 with the whole 20
    # in it: cash -70, plus the 50 the return of capital paid.
    PART = "ADJUST 2025-06-20 10:00:00 SYN.{sfx} {cur} 60 type=roc\n"
    # A reinvested distribution adds 50 of new cost: the ACB is 60 and
    # holds 10 of the addition; the sale for 50: -10 + 10, with the
    # loss's -20.
    NEW = "ADJUST 2025-06-20 10:00:00 SYN.{sfx} {cur} 50 type=dist\n"

    @rule("CA-RPT-16")
    def test_reversed_canada(self):
        self.assertEqual(self._total(CA, self.REV), -70.0)

    @rule("US-RPT-12")
    def test_reversed_usa(self):
        self.assertEqual(self._total(US, self.REV), -70.0)

    @rule("CA-RPT-16")
    def test_part_reversed_canada(self):
        self.assertEqual(self._total(CA, self.PART), -20.0)

    @rule("US-RPT-12")
    def test_part_reversed_usa(self):
        self.assertEqual(self._total(US, self.PART), -20.0)

    @rule("CA-RPT-16")
    def test_new_cost_brings_nothing_back_canada(self):
        self.assertEqual(self._total(CA, self.NEW), -20.0)

    @rule("US-RPT-12")
    def test_new_cost_brings_nothing_back_usa(self):
        self.assertEqual(self._total(US, self.NEW), -20.0)


class TestUsSameDateLots(unittest.TestCase):
    """TJ-1218: the put's lot (cost 80) and a purchase of the same date
    (cost 10), a return of capital across them: only a lot it passes the
    cost of gives up its premium."""

    def _rows(self, day: str, roc: int = 100) -> str:
        return (_put(2025, "US", "USD")
                + f"BUYSELL {day} 11:00:00 SYN.US 100 USD .1 10 0\n"
                f"ADJUST 2025-06-02 10:00:00 SYN.US USD -{roc} type=roc\n"
                "BUYSELL 2025-07-02 10:00:00 SYN.US -100 USD .4 40 0\n"
                "BUYSELL 2025-07-03 10:00:00 SYN.US -100 USD .4 40 0\n")

    @rule("US-ROC-02", "US-RPT-12")
    def test_same_date_as_the_next_day(self):
        # 50 each: the put's lot sells at -10, the other's at 40.
        for day in ("2025-01-17", "2025-01-18"):
            with self.subTest(day=day), tempfile.TemporaryDirectory() as td:
                root = _project(td, "usa", {"m": self._rows(day)})
                doc = _json(root, "stats", "2025")
                self.assertEqual(_pl(doc, "long-shares"), 30.0)
                self.assertEqual(_pl(doc), 50.0)

    @rule("US-ROC-02", "US-RPT-12")
    def test_both_beyond_their_cost(self):
        # 100 each: the put's lot's 20 beyond its cost takes all its
        # premium, the other's 90 holds none. Both sales keep their 40.
        for day in ("2025-01-17", "2025-01-18"):
            with self.subTest(day=day), tempfile.TemporaryDirectory() as td:
                root = _project(td, "usa", {"m": self._rows(day, 200)})
                self.assertEqual(
                    _pl(_json(root, "stats", "2025"), "long-shares"), 80.0)


class TestCaEarlierYearRocOnNilAcb(unittest.TestCase):
    """TJ-1220: 2024 returns of capital reach and pass the ACB of 80; a
    second on a nil ACB is a deemed gain too: `stats 2025` keeps the
    sale's whole gain as `stats all` does."""

    def _go(self, rocs) -> tuple:
        rows = _put(2024, "TO", "CAD")
        for i, r in enumerate(rocs):
            rows += (f"ADJUST 2024-{6 + i:02d}-03 10:00:00 SYN.TO CAD -{r} "
                     "type=roc\n")
        rows += "BUYSELL 2025-07-02 10:00:00 SYN.TO -100 CAD .4 40 0\n"
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"m": rows})
            return (_pl(_json(root, "stats", "2025"), "long-shares"),
                    _pl(_json(root, "stats", "all"), "long-shares"))

    @rule("CA-RPT-16")
    def test_nil_then_more(self):
        self.assertEqual(self._go([80, 20]), (40.0, 40.0))
        self.assertEqual(self._go([90, 30]), (40.0, 40.0))
        # 10 of the 20 released: the sale's 40 less the other 10.
        self.assertEqual(self._go([80, 5, 5]), (30.0, 30.0))

    @rule("CA-RPT-16")
    def test_two_accounts(self):
        # The return of capital on nil in one account of the pool, the
        # sale in the other.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {
                "a": _put(2024, "TO", "CAD")
                + "ADJUST 2024-06-03 10:00:00 SYN.TO CAD -80 type=roc\n"
                "ADJUST 2024-07-03 10:00:00 SYN.TO CAD -20 type=roc\n",
                "b": "BUYSELL 2025-07-02 10:00:00 SYN.TO -100 CAD .4 40 0\n"})
            for args in (("2025",), ("2025", "b"), ("all",)):
                with self.subTest(view=args):
                    self.assertEqual(
                        _pl(_json(root, "stats", *args), "long-shares"), 40.0)

    @rule("CA-RPT-16")
    def test_a_short_stays_a_short(self):
        # A positive compensation payment on a short pool is no return
        # of capital on nil: nothing to release, the put's premium stays
        # with its later shares.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"m": (
                "BUYSELL 2024-01-03 10:00:00 SYN.TO -10 CAD 1 10 0\n"
                "ADJUST 2024-01-05 10:00:00 SYN.TO CAD -1 type=roc\n"
                "BUYSELL 2024-01-08 10:00:00 SYN.TO 10 CAD 1 10 0\n"
                + _put(2024, "TO", "CAD").replace("2024-01-17", "2024-02-16")
                + "BUYSELL 2025-07-02 10:00:00 SYN.TO -100 CAD .4 40 0\n")})
            self.assertEqual(
                _pl(_json(root, "stats", "2025"), "long-shares"), -60.0)

    @rule("CA-RPT-16")
    def test_up_to_the_acb_keeps_the_premium(self):
        self.assertEqual(self._go([80]), (20.0, 20.0))


class TestUsOtherAccountsDeemedGain(unittest.TestCase):
    """TJ-1221, TJ-1255: a put's shares move between two accounts, with a
    return of capital beyond their cost in one of them."""

    @staticmethod
    def _files(out: str, in_: str) -> dict:
        return {"inputs/a/transfers.csv": _ib_transfer(out, -100),
                "inputs/b/transfers.csv": _ib_transfer(in_, 100)}

    @rule("US-ROC-02", "US-RPT-12", "US-BASIS-05")
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

    @rule("US-RPT-12", "US-BASIS-05")
    def test_no_return_of_capital_keeps_the_premium(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "a": _put(2025, "US", "USD"),
                "b": "BUYSELL 2025-03-03 10:00:00 SYN.US -100 USD .4 40 0\n"},
                files=self._files("2025-02-03", "2025-02-04"))
            self.assertEqual(_pl(_json(root, "stats", "2025", "b"),
                                 "long-shares"), -60.0)
            self.assertFalse(list((root / "work").glob("*_gains_all.json")))

    @rule("US-ROC-02", "US-RPT-12")
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

    @rule("US-RPT-12")
    def test_no_roc_builds_no_full_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "a": _put(2024, "US", "USD"),
                "b": "BUYSELL 2025-07-02 10:00:00 SYN.US -100 USD .4 40 0\n"},
                files=self._files("2024-02-01", "2024-02-02"))
            _json(root, "stats", "2025", "b")
            self.assertFalse(list((root / "work").glob("*_gains_all.json")))


class TestCaEarlierYearCover(unittest.TestCase):
    """TJ-1222: b's short of 2024 is covered by a put's delivery in 2024,
    the premium in that cover's gain; a's later round trip is a win of
    10 in the year views as in `stats all`."""

    A = ("BUYSELL 2025-03-03 10:00:00 SYN.TO 100 CAD 1 100 0\n"
         "BUYSELL 2025-04-03 10:00:00 SYN.TO -100 CAD 1.1 110 0\n")
    B = ("BUYSELL 2024-01-02 10:00:00 SYN.TO -100 CAD 1 100 0\n"
         "BUYSELL 2024-01-03 10:00:00 SYN240119P00001000.TO -1 CAD .2 20 0\n"
         "ASSIGN 2024-01-19 09:30:00 SYN240119P00001000.TO 1 CAD 0 0 0\n"
         "ASSIGN 2024-01-19 10:00:00 SYN.TO 100 CAD 1 100 0\n")

    @rule("CA-RPT-16")
    def test_two_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"a": self.A, "b": self.B})
            for args in (("2025", "a"), ("2025",), ("all", "a")):
                with self.subTest(view=args):
                    self.assertEqual(
                        _pl(_json(root, "stats", *args), "long-shares"), 10.0)

    @rule("CA-RPT-16")
    def test_one_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"a": self.B + self.A})
            self.assertEqual(
                _pl(_json(root, "stats", "2025"), "long-shares"), 10.0)

    @rule("CA-RPT-16")
    def test_a_delivery_that_covered_nothing_gives_its_premium_up(self):
        # No short: the put's shares are the pool's, the 2025 sale takes
        # the premium out: 110 - 80 - 20.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"a": (
                _put(2024, "TO", "CAD")
                + "BUYSELL 2025-04-03 10:00:00 SYN.TO -100 CAD 1.1 110 0\n")})
            self.assertEqual(
                _pl(_json(root, "stats", "2025"), "long-shares"), 10.0)
            self.assertFalse(list((root / "work").glob("*_gains_all.json")))

    @rule("CA-RPT-16")
    def test_part_of_the_delivery_covered(self):
        # A short of 40: 60 delivered shares stay in the pool with 12 of
        # the premium; sold for 66: cost 48, gain 18, the shares' P/L 6.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"a": (
                "BUYSELL 2024-01-02 10:00:00 SYN.TO -40 CAD 1 40 0\n"
                "BUYSELL 2024-01-03 10:00:00 SYN240119P00001000.TO -1 CAD .2 "
                "20 0\n"
                "ASSIGN 2024-01-19 09:30:00 SYN240119P00001000.TO 1 CAD 0 0 "
                "0\n"
                "ASSIGN 2024-01-19 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-04-03 10:00:00 SYN.TO -60 CAD 1.1 66 0\n")})
            self.assertEqual(
                _pl(_json(root, "stats", "2025"), "long-shares"), 6.0)


class TestOptionYieldMultOfOneHundred(unittest.TestCase):
    """TJ-1223: a contract of 150 renamed into a series whose ticker.map
    MULT line says 100."""

    ROWS = ("BUYSELL 2025-01-02 10:00:00 SYN1251219C00002000.TO -1 CAD .2 "
            "30 0\n"
            "SPLIT 2025-04-01 09:30:00 SYN1251219C00002000.TO "
            "SYN251219C00003000.TO 1\n"
            "BUYSELL 2025-07-02 10:00:00 SYN251219C00003000.TO 1 CAD .12 12 "
            "0\n")

    def _part(self, tmap: str, rows: str = None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": rows or self.ROWS},
                            tmap=tmap)
            part, = _json(root, "option-yield")["parts"]
            return {k: part[k] for k in ("avg_notional", "annualized")}

    def test_mult_line_of_100(self):
        self.assertEqual(self._part("MULT SYN1 150\nMULT SYN 100\n"),
                         {"avg_notional": 148.77, "annualized": 0.120994})

    def test_stated_on_a_row(self):
        self.assertEqual(
            self._part("MULT SYN1 150\n",
                       self.ROWS.replace("12 0\n", "12 0 x100\n")),
            {"avg_notional": 148.77, "annualized": 0.120994})

    def test_stated_nowhere_keeps_the_opened_size(self):
        self.assertEqual(self._part("MULT SYN1 150\n")["avg_notional"],
                         186.58)


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
            r = _cli(root, "elect", acct, "--set", f"{cid}=tax_free_355",
                     "--hint", hint)
            assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    _run(root)


class TestUsSpinOffRowsNotAdjacent(unittest.TestCase):
    """TJ-1254: the premium's part of a US spin-off, read from the
    parent's own drop on its day."""

    PUT = "ZZQQ250321P00002000.US"
    HEAD = (f"BUYSELL 2025-01-02 10:00:00 {PUT} -1 USD .2 20 0\n"
            f"ASSIGN 2025-01-17 10:00:00 {PUT} 1 USD 0 0 0\n"
            "ASSIGN 2025-01-17 10:00:00 ZZQQ.US 100 USD 2 200 0\n")
    ONE = SPIN + SPIN_ROW.format(c="ZZKK", isin="US0000000922")

    def _by_symbol(self, root: Path) -> dict:
        doc = _json(root, "win-rates", "2025", "--by", "symbol")
        return {g["key"]: g["net_pl"] for g in doc["rows"]}

    @rule("US-RPT-12", "US-CORP-07")
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

    @rule("US-RPT-12", "US-CORP-07")
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

    @rule("US-RPT-12", "US-CORP-07")
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

    @rule("US-RPT-12", "US-CORP-07")
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


class TestGrantExpiryWrittenInTwoYears(unittest.TestCase):
    """TJ-1256: one series written in 2025 and 2026, one contract bought
    back in 2026, the other closed by the expiry in 2026: two closes in
    the 2026 views, the gain as it is."""

    OPT = "SYN260320C00001000.TO"
    TOML = ('option_premium_timing = "grant"\n'
            'option_grant_timing_since = 2025\n')
    HEAD = ("BUYSELL 2025-11-03 10:00:00 SYN.TO 200 CAD 1 200 0\n"
            f"BUYSELL 2025-12-01 10:00:00 {OPT} -1 CAD 0.1 10 0\n"
            f"BUYSELL 2026-01-05 10:00:00 {OPT} -1 CAD 0.1 10 0\n")
    END = f"BUYSELL 2026-03-20 10:00:00 {OPT} 1 CAD 0 0 0\n"
    BACK = f"BUYSELL 2026-02-02 10:00:00 {OPT} 1 CAD 0.05 5 0\n"
    BACK2 = f"BUYSELL 2026-02-03 10:00:00 {OPT} 1 CAD 0.05 5 0\n"

    def _views(self, rows: str):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": rows}, year=2026,
                            extra=self.TOML)
            return (_json(root, "ccd-sum"), _json(root, "ccd-sum", "all"),
                    _json(root, "winners"), _json(root, "winners", "all"))

    @staticmethod
    def _ccd(doc) -> tuple:
        return (sum(r["contracts"] for r in doc["rows"]),
                sum(r["qty"] for r in doc["rows"]), doc["total_gain"])

    @staticmethod
    def _closes(doc) -> int:
        return sum(r["closes"] for r in doc["rows"])

    def test_one_bought_back(self):
        ccd, ccd_all, win, win_all = self._views(
            self.HEAD + self.BACK + self.END)
        self.assertEqual(self._ccd(ccd_all)[:2], (2, 2.0))
        self.assertEqual(self._ccd(ccd), (2, 2.0, 5.0))
        self.assertEqual(self._closes(win_all), 2)
        self.assertEqual(self._closes(win), 2)

    def test_one_closed_at_nil_one_still_open(self):
        # The row at nil closes one; the books hold the other: no more
        # than `all` says.
        ccd, ccd_all, win, win_all = self._views(self.HEAD + self.END)
        self.assertEqual(self._ccd(ccd)[0], self._ccd(ccd_all)[0])
        self.assertEqual(self._closes(win), self._closes(win_all))
        self.assertEqual(self._ccd(ccd)[0], 1)

    def test_both_bought_back(self):
        ccd, _all, win, _wall = self._views(
            self.HEAD + self.BACK + self.BACK2 + self.END)
        self.assertEqual(self._ccd(ccd)[0], 2)
        self.assertEqual(self._closes(win), 2)


if __name__ == "__main__":
    unittest.main()
