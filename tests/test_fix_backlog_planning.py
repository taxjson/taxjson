"""Backlog group "planning": harvest, div-estimate, US distributions,
the price fetch's messages and plot-tax (TJ-0021, TJ-0024, TJ-0027,
TJ-0039, TJ-0118, TJ-0138, TJ-0139, TJ-0140, TJ-0157, TJ-0161, TJ-0167,
TJ-0182). Synthetic data only; no network (yfinance is a stub)."""
import contextlib
import io
import json
import logging
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tax_rules import rule
from _style import CapturedWidth
from _style import project as style_project

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tx(d, q, sym="ZZQ.US", settle=None, **kw):
    return dict(date=d, date_settle=settle or d, time="10:00:00",
                account="margin", symbol=sym, action="BUYSELL",
                quantity=q, net_amount=0, currency="USD", **kw)


class TestDistributionStraddlingPurchase(unittest.TestCase):

    @rule("US-DIST-01")
    def test_tj0021_tj0027_a_straddling_purchase_is_no_miss(self):
        """TJ-0021 / TJ-0027: a purchase traded on the record date that
        settles after it is not the holder's and rightly takes none of
        the backdated adjustment: no ATTENTION. A purchase that settles
        by the record date is still named."""
        from taxjson.bin import taxjson_apply_distributions as dist
        rows = [_tx("2024-01-10", 100, settle="2024-01-11"),
                _tx("2024-06-10", -50, settle="2024-06-11"),
                _tx("2024-06-10", 30, settle="2024-06-11")]
        stamp = dist._stamp_date(rows, "ZZQ.US", "2024-06-10", "margin")
        self.assertEqual(stamp, "2024-06-09")
        self.assertIsNone(dist._stamp_problem(rows, "ZZQ.US", "2024-06-10",
                                              "margin", stamp))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _doc, n = dist.apply_distributions(
                {"transactions": [dict(r) for r in rows],
                 "metadata": {"target_currency": "USD"}},
                [("ZZQ.US", "2024-06-10", 0.5)], "margin", country="usa")
        self.assertEqual(n, 1)
        self.assertNotIn("ATTENTION", err.getvalue())
        # Settled by the record date: the stamp misses it, and says so.
        settled = rows + [_tx("2024-06-10", 10, settle="2024-06-10")]
        self.assertIn("bought 10 on 2024-06-10",
                      dist._stamp_problem(settled, "ZZQ.US", "2024-06-10",
                                          "margin", stamp))


# ------------------------------------------------------------------ harvest
_TODAY = date.today()


def _harvest(root: Path, inventory, *, country="canada", rates="",
             fetcher=None, extra=()):
    from taxjson.bin.taxjson_harvest import main
    gains = root / "margin_gains_wash.json"
    gains.write_text(json.dumps({"summary": {"year": _TODAY.year},
                                 "transactions": [],
                                 "inventory": inventory}))
    (root / "to_base.csv").write_text(rates)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main([str(gains), "--no-ibkr", "--country", country, *extra],
                  fetchers=[fetcher or (lambda rem: {})])
    return rc, out.getvalue(), err.getvalue()


class TestHarvest(unittest.TestCase):

    def test_tj0024_no_rate_names_the_position_once_and_in_json(self):
        """TJ-0024: a quote in a currency the rates file lacks: one
        warning naming the symbol and the fix that works (the run
        fetches source_currencies only), and --json lists it."""
        inv = [{"symbol": "ZZL.TO", "qty": 10, "total_cost": 100.0,
                "position_start_date": "2024-01-02"}]

        def gbp(rem):
            return {s: (5.0, "fake", "GBP") for s in rem}
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = _harvest(Path(td), inv, fetcher=gbp)
            self.assertEqual(rc, 0)
            self.assertEqual(err.count("no GBP->CAD rate"), 1, err)
            self.assertIn("ZZL.TO omitted: no GBP->CAD rate", err)
            self.assertIn('add "GBP" to source_currencies', err)
            self.assertNotIn("to refresh rates", err)
            rc, out, err = _harvest(Path(td), inv, fetcher=gbp,
                                    extra=["--json"])
        doc = json.loads(out)
        self.assertEqual(doc["rows"], [])
        self.assertEqual(doc["omitted"], [{"account": "margin",
                                           "symbol": "ZZL.TO",
                                           "reason": "no GBP->CAD rate"}])

    def test_tj0167_an_excluded_option_is_said(self):
        """TJ-0167: a book holding only an option, without --options:
        a note says it was left out, not just 'No open positions.'"""
        inv = [{"symbol": "ZZK250117P00045000.US", "qty": -1,
                "total_cost": -199.0}]
        with tempfile.TemporaryDirectory() as td:
            rc, out, err = _harvest(Path(td), inv)
        self.assertEqual(rc, 0)
        self.assertIn("No open positions.", out)
        self.assertIn("1 option position(s) excluded (pass --options",
                      err)
        self.assertIn("ZZK250117P00045000.US", err)

    @rule("US-WASH-10")
    def test_tj0138_lt_in_counts_the_tacked_holding_period(self):
        """TJ-0138: a wash-sale replacement's LT_IN counts from its
        holding period (the loss shares' days added), which the US
        engine's inventory now carries as holding_start_date."""
        from taxjson.lib.core import TaxTransaction, USATaxRules
        rows = [dict(_tx("2026-03-02", 100, sym="ZZW.US"), price=20,
                     net_amount=-2000),
                dict(_tx("2026-09-01", -100, sym="ZZW.US"), price=10,
                     net_amount=1000),
                dict(_tx("2026-09-15", 60, sym="ZZW.US"), price=11,
                     net_amount=-660)]
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            res = USATaxRules().compute_gains(
                [TaxTransaction(**r) for r in rows], detect_wash_sales=True)
        inv = [i for i in res["inventory"] if i["symbol"] == "ZZW.US"]
        self.assertEqual(len(inv), 1)
        # 183 days held (03-02 .. 09-01), tacked onto the 09-15 buy.
        self.assertEqual(inv[0]["position_start_date"], "2026-09-15")
        self.assertEqual(inv[0]["holding_start_date"], "2026-03-16")
        from taxjson.bin.taxjson_harvest import _days_to_long_term
        want = _days_to_long_term("2026-03-16")
        self.assertLess(want, _days_to_long_term("2026-09-15"))
        with tempfile.TemporaryDirectory() as td:
            rc, out, _err = _harvest(
                Path(td), [dict(inv[0], account="margin")], country="usa",
                fetcher=lambda rem: {s: (11.94, "fake", "USD")
                                     for s in rem},
                extra=["--json"])
        self.assertEqual(rc, 0)
        row = json.loads(out)["rows"][0]
        self.assertEqual(row["days_to_long_term"], want)


# ------------------------------------------------------- the yfinance tier
class _Frame:
    empty = True


def _noisy_yfinance(records):
    """A stand-in yfinance whose every lookup misses the way the real
    one does: a raw line on its logger, and (older releases) printed."""
    mod = types.ModuleType("yfinance")

    class Ticker:
        history_metadata = {}

        def __init__(self, sym):
            self.sym = sym

        def history(self, *a, **kw):
            logging.getLogger("yfinance").error(
                'HTTP Error 404: {"quoteSummary":{"error":{"code":'
                '"Not Found"}}}')
            print(f"${self.sym}: possibly delisted; no timezone found",
                  file=sys.stderr)
            print(f"${self.sym}: No data found, symbol may be delisted")
            return _Frame()
    mod.Ticker = Ticker

    class _Rec(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    return mod, _Rec()


class TestYfinanceQuiet(unittest.TestCase):

    def _quiet(self, call):
        records = []
        mod, handler = _noisy_yfinance(records)
        lg = logging.getLogger("yfinance")
        lg.addHandler(handler)
        out, err = io.StringIO(), io.StringIO()
        try:
            with patch.dict(sys.modules, {"yfinance": mod}), \
                    contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                result = call()
        finally:
            lg.removeHandler(handler)
        return result, out.getvalue(), err.getvalue(), records

    def test_tj0161_tj0182_harvest_prints_only_the_labelled_warning(self):
        """TJ-0161 / TJ-0182: harvest's yfinance tier: no raw library
        line, only 'no price from any tier for: ...'."""
        from taxjson.bin.taxjson_harvest import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gains = root / "margin_gains_wash.json"
            gains.write_text(json.dumps({"inventory": [
                {"symbol": "ZZQ.TO", "qty": 10, "total_cost": 100.0}]}))
            (root / "to_base.csv").write_text("")
            with patch.dict("os.environ", {"TAXJSON_OFFLINE": "0"}):
                rc, out, err, records = self._quiet(lambda: main(
                    [str(gains), "--no-ibkr", "--country", "canada"]))
        self.assertEqual(rc, 0)
        self.assertEqual(records, [])
        self.assertNotIn("delisted", err + out)
        self.assertNotIn("HTTP Error", err + out)
        self.assertIn("no price from any tier for: ZZQ.TO", err)

    def test_tj0118_the_close_lookup_is_quiet(self):
        """TJ-0118: the in-kind close lookup (price_chain.close_on, the
        Yahoo history tier) prints no raw yfinance line."""
        from taxjson.lib import price_chain as PC
        with tempfile.TemporaryDirectory() as td:
            got, out, err, records = self._quiet(lambda: PC.close_on(
                "ZZB.TO", "ZZB.TO", "2024-05-01",
                cache_path=Path(td) / ".close_cache.json", offline=False))
        self.assertIsNone(got)
        self.assertEqual((out, err, records), ("", "", []))


# --------------------------------------------------------------- plot-tax
def _plot_doc(label, adjustment=0.0):
    from taxjson.lib import tax_plot as TP
    pts = [{"month": m, "cum_tax": 10.0, "cum_income": 100.0}
           for m in TP.months_of_year(2026)]
    return TP.build(pts, year=2026, base="USD",
                    year_estimate=10.0 + adjustment, year_income=100.0,
                    label=label, accounts=["margin"])


class TestPlotTax(unittest.TestCase):

    def test_tj0139_the_footer_names_only_the_countrys_commands(self):
        """TJ-0139: a US project's footer does not send the user to
        `tjs instalments` (Canada-only, US-CTRY-02), and the year line
        names the country's own reclassification."""
        from taxjson.lib import tax_plot as TP
        us = TP.render(_plot_doc("usa", 5.0)).text()
        self.assertIn("tjs estimate", us)
        self.assertNotIn("instalments", us)
        self.assertIn("§1256", us)
        self.assertNotIn("T5", us)
        ca = TP.render(_plot_doc("canada/ON", 5.0)).text()
        self.assertIn("the instalments it calls for: tjs instalments", ca)
        self.assertIn("T5 box 18", ca)
        self.assertNotIn("§1256", ca)


# ----------------------------------------------------------- div-estimate
class TestDivEstimate(unittest.TestCase):

    def test_tj0140_the_dating_rule_is_the_projects(self):
        """TJ-0140: HOW IT IS ESTIMATED names the project's own dating
        rule: a US project's RIC January dividend, never a Canadian
        trust's record date."""
        from taxjson.lib import div_estimate as DE
        doc = DE.estimate([], DE.payments([]), as_of="2026-07-01",
                          months=12, tax_year=2026, base="USD",
                          fx=lambda c: None)
        us = DE.render(doc, details=True, country="usa").text()
        self.assertIn("ric_january_dividends", us)
        self.assertNotIn("Canadian", us)
        ca = DE.render(doc, details=True, country="canada").text()
        self.assertIn("a Canadian trust's: its record date", ca)
        self.assertNotIn("ric_january_dividends", ca)

    def test_tj0039_a_zero_window_is_refused(self):
        """TJ-0039: '0m' / '0y' are refused (exit 2) as --months 0 is,
        not read as one month; a few days still count as one."""
        from taxjson.lib.div_estimate import parse_window
        self.assertEqual(parse_window("0m"), 0)
        self.assertEqual(parse_window("0y"), 0)
        self.assertEqual(parse_window("10d"), 1)
        p = style_project("canada")
        for tok in ("0m", "0y"):
            r = p.run("div-estimate", tok)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(f"'{tok}' is a zero-length window", r.stderr)
        r = p.run("div-estimate", "--months", "0")
        self.assertEqual(r.returncode, 2, r.stderr)


# -------------------------------------------------------------- exit codes
class TestUsageExitCodes(unittest.TestCase):

    def test_tj0157_one_exit_code_for_one_usage_error(self):
        """TJ-0157: a malformed date or period, or an account the
        project does not have, exits 2 in every command (it was 1 in
        some, 2 in others)."""
        p = style_project("canada")
        for argv in (("wash-radar", "--date", "2026-02-30"),
                     ("list", "--date", "bad"),
                     ("list", "--date", "2026-02-30"),
                     ("events", "1x"), ("trades", "1x"),
                     ("winners", "1x"), ("stats", "1x"),
                     ("wash-radar", "nosuch"), ("list", "nosuch"),
                     ("winners", "2026", "nosuch"),
                     ("events", "2026", "nosuch"),
                     ("sum", "nosuch")):
            with self.subTest(argv=argv):
                r = p.run(*argv)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("Error: ", r.stderr)


if __name__ == "__main__":
    unittest.main()
