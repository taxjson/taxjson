"""The 2026-10-27 command audit, report figures.

TJ-0299  plot-tax credits the foreign tax the books record, as
         `estimate` does (it assumed 15% on every run); a month's run
         credits what was recorded by its end.
TJ-0327  stats / win-rates: a US sale that takes several FIFO lots is
         one closed trade, not one per lot.
TJ-0328  fees: a fee under half a cent is in the total (three 0.004
         commissions were "no fees").
TJ-0329  30d / mtd / ytd windows end today: a row dated later is not in
         them (`all` and a year keep it).

Synthetic books only: made-up tickers (ZZQ.US, ZZQ.TO), account names
(margin), amounts under 1,000.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tax_rules import rule

from taxjson.bin import taxjson_fees as FEES
from taxjson.bin import taxjson_run as R
from taxjson.lib import trade_stats as TS
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`taxjson -C root args...` from this tree's sources (width 0)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)
    if r.returncode != 0:
        raise AssertionError(f"{args}: rc {r.returncode}\n"
                             f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return r


def _project(tmp: str, country: str, year: int, lines) -> Path:
    """One taxable account `margin` holding one .tt book."""
    root = Path(tmp) / "p"
    base = "USD" if country == "usa" else "CAD"
    prov = 'province = "ON"\n' if country == "canada" else ""
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\n'
        f'year = {year}\ncountry = "{country}"\n'
        f'base_currency = "{base}"\nsource_currencies = []\n{prov}'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin" / "book.tt").write_text(
        "\n".join(lines) + "\n")
    return root


def _pinned(day: date):
    """`today` is `day` for the views (taxjson_run's date_cls)."""
    class _Today(date):
        @classmethod
        def today(cls):
            return day
    return mock.patch.object(R, "date_cls", _Today)


def _inproc(root: Path, *args: str):
    """`taxjson -C root ARGS` in this process: (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["taxjson", "-C", str(root), *args]), \
            mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1",
                                         "TAXJSON_WIDTH": "0"}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            R._main()
            code = 0
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------- TJ-0299
class TestPlotTaxWithholding(unittest.TestCase):
    """plot-tax runs the estimator on the withholding `estimate` reads:
    the base books' TAX rows, a month's run those dated by its end."""

    FLAGS = ("--other-income", "60000")

    def _both(self, lines):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "canada", 2025, lines)
            _cli(root, "run", "--no-input")
            est = json.loads(_cli(root, "estimate", *self.FLAGS,
                                  "--json").stdout)["estimate"]
            plot = json.loads(_cli(root, "plot-tax", *self.FLAGS,
                                   "--json").stdout)
        return est, plot

    @rule("CA-PLAN-08")
    def test_recorded_withholding_is_the_estimates(self):
        # 10 withheld on a 200 foreign dividend (5%): the estimate
        # credits 10; the plot assumed 15% (30) and showed 20 less tax.
        est, plot = self._both([
            "BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0",
            "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 20 200",
            "TAX 2025-06-02 10:00:00 ZZQ.US 10 CAD 1 10"])
        self.assertEqual(est["ftc_assumed"], 10.0)
        self.assertEqual(plot["year_estimate"], est["estimated_tax"])
        self.assertEqual(plot["months_total"], est["estimated_tax"])
        self.assertEqual(plot["year_adjustment"], 0.0)
        rows = {r["month"]: r for r in plot["rows"]}
        self.assertEqual(rows["2025-06"]["tax"], est["estimated_tax"])
        self.assertEqual(rows["2025-12"]["cum_tax"], est["estimated_tax"])

    @rule("CA-PLAN-08")
    def test_a_month_credits_what_was_withheld_by_its_end(self):
        # The TAX row posts a month after the dividend: June carries the
        # dividend's tax with no credit, July the 10 withheld.
        est, plot = self._both([
            "BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 CAD 10 100 0",
            "DIVIDEND 2025-06-02 10:00:00 ZZQ.US 10 CAD 20 200",
            "TAX 2025-07-02 10:00:00 ZZQ.US 10 CAD 1 10"])
        rows = {r["month"]: r for r in plot["rows"]}
        self.assertEqual(rows["2025-07"]["tax"], -10.0)
        self.assertEqual(rows["2025-06"]["cum_tax"],
                         round(est["estimated_tax"] + 10.0, 2))
        self.assertEqual(plot["months_total"], est["estimated_tax"])
        self.assertEqual(plot["year_estimate"], est["estimated_tax"])

    @rule("CA-PLAN-08")
    def test_withholding_totals(self):
        rows = {"margin": [("2025-06-02", 10.0), ("2025-07-02", 5.0)],
                "rbc": None}
        fba = {"margin": 200.0, "rbc": 100.0}
        # An account with no TAX row in the year: the 15% assumption on
        # its foreign dividends; the other: what it recorded.
        self.assertEqual(R._withholding_total(rows, fba), 30.0)
        self.assertEqual(R._withholding_total(rows, fba, "2025-06-30"),
                         25.0)
        # Before the first TAX row: nothing recorded yet, no assumption.
        self.assertEqual(R._withholding_total(rows, fba, "2025-05-31"),
                         15.0)
        # A refund that nets the year to 0 credits nothing.
        self.assertEqual(R._withholding_total(
            {"margin": [("2025-06-02", 10.0), ("2025-08-01", -10.0)]},
            {"margin": 200.0}), 0.0)
        # No TAX row anywhere: no recorded figure (the estimator assumes).
        self.assertIsNone(R._withholding_total({"margin": None},
                                               {"margin": 200.0}))


# --------------------------------------------------------------- TJ-0327
def _lot(tid, gain, qty=1, **kw):
    """One US gains row: a lot of the sale `tid`."""
    row = dict(id=tid, date="2025-03-06", symbol="ZZQ.US", account="margin",
               qty=qty, gain=gain, raw_gain=gain)
    row.update(kw)
    return row


class TestFifoLotsAreOneTrade(unittest.TestCase):

    def _closed(self, rows, manual=()):
        doc = {"transactions": list(rows),
               "manual_reporting_required": list(manual)}
        return TS.closed_trades({"margin": doc}, {"margin": []},
                                lambda d: True, lambda t: t["date"])

    @rule("US-RPT-12")
    def test_one_sale_of_two_lots_is_one_trade(self):
        res = self._closed([_lot("s1", 15.0, term="LONG_TERM"),
                            _lot("s1", -5.0, term="SHORT_TERM")])
        self.assertEqual([(t["symbol"], t["pnl"]) for t in res["trades"]],
                         [("ZZQ.US", 10.0)])
        st = TS.group(res["trades"], "symbol")["total"]
        self.assertEqual((st["trades"], st["wins"], st["losses"],
                          st["net_pl"], st["win_rate"]),
                         (1, 1, 0, 10.0, 1.0))

    @rule("US-RPT-12")
    def test_partial_closes_stay_trades_of_their_own(self):
        res = self._closed([_lot("s1", 15.0), _lot("s1", -5.0),
                            _lot("s2", -2.0, date="2025-04-01"),
                            _lot("s3", 6.0, date="2025-05-01")])
        self.assertEqual([t["pnl"] for t in res["trades"]],
                         [10.0, -2.0, 6.0])
        # A row with no id is a trade of its own.
        res = self._closed([_lot("", 4.0), _lot("", -1.0)])
        self.assertEqual([t["pnl"] for t in res["trades"]], [4.0, -1.0])
        # The same id in another class (an option) is another trade.
        res = self._closed([_lot("s1", 3.0),
                            _lot("s1", 2.0, symbol="ZZQ250620C00010000.US")])
        self.assertEqual(sorted(t["cls"] for t in res["trades"]),
                         ["long-options", "long-shares"])

    @rule("US-RPT-12")
    def test_denied_and_unknown_cost_count_dispositions(self):
        res = self._closed([_lot("s1", 0.0, raw_gain=-4.0,
                                 disallowed_amount=4.0),
                            _lot("s1", 0.0, raw_gain=-6.0,
                                 disallowed_amount=6.0)],
                           manual=[_lot("s9", 0.0, tainted=True),
                                   _lot("s9", 0.0, tainted=True)])
        self.assertEqual(res["denied_total"], 10.0)
        self.assertEqual(res["denied_count"], 1)
        self.assertEqual(res["tainted_skipped"], 1)
        self.assertEqual([t["pnl"] for t in res["trades"]], [-10.0])
        # A sale with an unknown-cost lot is left out whole, its known
        # lot too: the unknown lot could turn its P/L (backlog TJ-0545).
        res = self._closed([_lot("s1", 7.0), _lot("s1", 0.0, tainted=True)])
        self.assertEqual(res["trades"], [])
        self.assertEqual(res["tainted_skipped"], 1)

    @rule("US-RPT-12")
    def test_cli_mixed_profit_and_term_lots(self):
        # One sale takes a long-term winning lot and a short-term losing
        # one; two later sales close the third lot in parts.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "usa", 2025, [
                "BUYSELL 2023-01-05 10:00:00 ZZQ.US 1 USD 10 10 0",
                "BUYSELL 2024-11-01 10:00:00 ZZQ.US 1 USD 30 30 0",
                "BUYSELL 2024-12-02 10:00:00 ZZQ.US 2 USD 20 40 0",
                "BUYSELL 2025-03-06 10:00:00 ZZQ.US -2 USD 25 50 0",
                "BUYSELL 2025-04-01 10:00:00 ZZQ.US -1 USD 18 18 0",
                "BUYSELL 2025-05-01 10:00:00 ZZQ.US -1 USD 26 26 0"])
            _cli(root, "run", "--no-input")
            gains = json.loads((root / "work" / "margin_gains.json")
                               .read_text())["transactions"]
            # The engine books the first sale as two lots.
            self.assertEqual(sorted(t["term"] for t in gains
                                    if t.get("date") == "2025-03-06"),
                             ["LONG_TERM", "SHORT_TERM"])
            stats = json.loads(_cli(root, "stats", "--json").stdout)
            wr = json.loads(_cli(root, "win-rates", "--json").stdout)
        for total in (stats["total"], wr["total"]):
            self.assertEqual((total["trades"], total["wins"],
                              total["losses"], total["net_pl"],
                              total["largest_win"], total["largest_loss"]),
                             (3, 2, 1, 14.0, 10.0, -2.0))


# --------------------------------------------------------------- TJ-0328
def _books(tmp: str, txs, year=2025) -> Path:
    """A project whose work/ holds one native book (margin_raw.json)."""
    root = Path(tmp) / "p"
    (root / "work").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\ncountry = "canada"\nyear = {year}\n'
        'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_raw.json").write_text(
        json.dumps({"transactions": txs}))
    return root


def _buy(d, commission, sym="ZZQ.TO", **kw):
    row = dict(action="BUYSELL", date=d, time="10:00:00", symbol=sym,
               quantity=1, price=10, net_amount=10 + commission,
               commission=commission, currency="CAD", account="margin")
    row.update(kw)
    return row


class TestFeesSubCent(unittest.TestCase):

    def test_sub_cent_commissions_are_in_the_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _books(tmp, [_buy(f"2025-01-0{i}", 0.004)
                                for i in (6, 7, 8)])
            rc, out, err = _inproc(root, "fees", "--json")
            self.assertEqual(rc, 0, err)
            doc = json.loads(out)
            self.assertEqual(doc["rows"], [])
            self.assertEqual(doc["totals"], {"CAD": 0.01})
            self.assertEqual(doc["unlisted_small_fees"], 3)
            rc, out, err = _inproc(root, "fees")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("No fees", out)
        self.assertIn("3 fee(s) (3 under half a cent, not listed); "
                      "TOTAL FEES: 0.01 CAD", out)

    def test_listed_fees_and_rebates_sum_unrounded(self):
        rebate = dict(action="FEE", date="2025-02-01", time="10:00:00",
                      currency="CAD", net_amount=-0.002, account="margin")
        with tempfile.TemporaryDirectory() as tmp:
            root = _books(tmp, [_buy("2025-01-06", 1.004),
                                _buy("2025-01-07", 0.004),
                                _buy("2025-01-08", 0.004), rebate,
                                _buy("2025-01-09", 0.0)])
            rc, out, err = _inproc(root, "fees", "--json")
            self.assertEqual(rc, 0, err)
            doc = json.loads(out)
            # 1.004 + 0.004 + 0.004 - 0.002 = 1.010; the listed row is
            # the 1.004 one; the zero-fee trade is no fee.
            self.assertEqual([r["fee"] for r in doc["rows"]], [1.0])
            self.assertEqual(doc["totals"], {"CAD": 1.01})
            self.assertEqual(doc["unlisted_small_fees"], 3)
            rc, out, err = _inproc(root, "fees")
        self.assertIn("4 fee(s) (3 under half a cent, not listed); "
                      "TOTAL FEES: 1.01 CAD", out)
        self.assertIn("1.00", out)

    def test_no_fee_at_all_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _books(tmp, [_buy("2025-01-06", 0.0)])
            rc, out, err = _inproc(root, "fees")
            self.assertIn("No fees incurred in tax year 2025.", out)
            rc, out, err = _inproc(root, "fees", "--json")
        self.assertEqual(json.loads(out)["totals"], {})


# --------------------------------------------------------------- TJ-0329
class TestWindowsEndToday(unittest.TestCase):

    def test_period_keep_closed_through_today(self):
        root = Path("/nonexistent-project")
        cases = [
            # today, token, kept, not kept
            (date(2026, 10, 27), "mtd", ["2026-10-01", "2026-10-27"],
             ["2026-09-30", "2026-10-28", "2026-11-01"]),
            (date(2026, 10, 27), "1d", ["2026-10-26", "2026-10-27"],
             ["2026-10-25", "2026-10-28"]),
            (date(2026, 12, 31), "ytd", ["2026-01-01", "2026-12-31"],
             ["2025-12-31", "2027-01-01"]),
            (date(2027, 1, 1), "mtd", ["2027-01-01"],
             ["2026-12-31", "2027-01-02"]),
            (date(2027, 1, 1), "ytd", ["2027-01-01"],
             ["2026-12-31", "2027-01-02"]),
            (date(2027, 1, 1), "30d", ["2026-12-02", "2027-01-01"],
             ["2026-12-01", "2027-01-02"]),
        ]
        for today, tok, kept, dropped in cases:
            with self.subTest(today=today, tok=tok), _pinned(today):
                keep, _label = R._period_keep(tok, root)
                for d in kept:
                    self.assertTrue(keep(d), d)
                for d in dropped:
                    self.assertFalse(keep(d), d)
        # `all` and a year keep a later row.
        with _pinned(date(2026, 10, 27)):
            self.assertTrue(R._period_keep("all", root)[0]("2026-11-01"))
            self.assertTrue(R._period_keep("2026", root)[0]("2026-11-01"))
            self.assertIsNone(R._period_end("all"))
            self.assertEqual(R._period_end("ytd"), "2026-10-27")

    def test_fees_sum_aggregate_until(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "margin_tt.json"
            p.write_text(json.dumps({
                "metadata": {"source_brokerage": "tt"},
                "transactions": [_buy("2026-10-26", 1.0, id="a"),
                                 _buy("2026-11-01", 1.0, id="b")]}))
            kw = dict(year=None, to_curr=None, history={},
                      default_rate=None, by_account=False)
            _b, grand, info = FEES.aggregate([p], since="2026-10-01",
                                             until="2026-10-27", **kw)
            self.assertEqual(info["rows"], 1)
            _b, grand, info = FEES.aggregate([p], since="2026-10-01", **kw)
            self.assertEqual(info["rows"], 2)


class TestWindowViewsEndToday(unittest.TestCase):
    """The views over real books (`tjs run`), today pinned to the day
    after a sale and five days before a sale entered ahead."""

    TODAY = date(2026, 10, 27)
    DATES = ("2026-10-20", "2026-10-26", "2026-11-01")

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _project(cls._tmp.name, "canada", 2026, [
            "BUYSELL 2026-10-20 10:00:00 ZZQ.TO 2 CAD 10 20 0",
            "BUYSELL 2026-10-26 10:00:00 ZZQ.TO -1 CAD 12 11 1",
            "BUYSELL 2026-11-01 10:00:00 ZZQ.TO -1 CAD 12 11 1"])
        _cli(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _json(self, *args):
        with _pinned(self.TODAY):
            rc, out, err = _inproc(self.root, *args, "--json")
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def _dates(self, doc):
        """The book's dates found anywhere in a JSON document."""
        found = set()

        def walk(v):
            if isinstance(v, dict):
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, str) and v[:10] in self.DATES:
                found.add(v[:10])
        walk(doc)
        return found

    def test_row_listings(self):
        for cmd in ("events", "trades"):
            for tok, want in (("1d", {"2026-10-26"}),
                              ("mtd", {"2026-10-20", "2026-10-26"}),
                              ("ytd", {"2026-10-20", "2026-10-26"}),
                              ("30d", {"2026-10-20", "2026-10-26"}),
                              ("all", set(self.DATES)),
                              ("2026", set(self.DATES))):
                with self.subTest(cmd=cmd, tok=tok):
                    self.assertEqual(self._dates(self._json(cmd, tok)),
                                     want)
        for tok in ("1d", "mtd", "ytd", "30d"):
            with self.subTest(cmd="fees", tok=tok):
                self.assertEqual(self._dates(self._json("fees", tok)),
                                 {"2026-10-26"})
        self.assertEqual(self._dates(self._json("fees", "all")),
                         {"2026-10-26", "2026-11-01"})

    def test_closed_trade_views(self):
        for tok in ("mtd", "ytd", "30d"):
            with self.subTest(tok=tok):
                self.assertEqual(self._json("stats", tok)["total"]["trades"],
                                 1)
                self.assertEqual(
                    self._json("win-rates", tok)["total"]["trades"], 1)
                self.assertEqual(self._dates(self._json("gains", tok)),
                                 {"2026-10-26"})
        # The tax year (the default) and `all` keep the later sale.
        self.assertEqual(self._json("stats")["total"]["trades"], 2)
        self.assertEqual(self._json("stats", "all")["total"]["trades"], 2)

    def test_fee_totals(self):
        self.assertEqual(self._json("fees", "mtd")["totals"], {"CAD": 1.0})
        self.assertEqual(self._json("fees", "2026")["totals"],
                         {"CAD": 2.0})
        fs = self._json("fees-sum", "mtd")
        self.assertEqual((fs["meta"]["since"], fs["meta"]["until"]),
                         ("2026-10-01", "2026-10-27"))
        self.assertEqual(fs["meta"]["fee_rows"], 1)
        self.assertEqual(self._json("fees-sum", "all")["meta"]["fee_rows"],
                         2)

    def test_trades_sum(self):
        for tok in ("mtd", "ytd", "30d"):
            with self.subTest(tok=tok):
                doc = self._json("trades-sum", tok)
                self.assertEqual([(r["buys"], r["sells"], r["fees"])
                                  for r in doc["rows"]], [(1, 1, 1.0)])
        doc = self._json("trades-sum", "all")
        self.assertEqual([(r["buys"], r["sells"], r["fees"])
                          for r in doc["rows"]], [(1, 2, 2.0)])


if __name__ == "__main__":
    unittest.main()
