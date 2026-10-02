"""Re-audit-2 test pins (tests-pins-04/05/06, reports group): labels and
accumulators that held at the audit but that no test failed on when
reverted. Each test names the finding and the mutant it kills.

  A2-0880 / A2-1585  fees-sum's per-brokerage 'non-option' line, its
                     $/UNIT header and 'Non-option vs option fees:'
                     heading; `taxjson sum`'s NON-OPT column; the tx
                     views' 'TOTAL STAKING (crypto, ordinary income):'
  A2-1555            fx-cash's per-currency ACQUIRED column sums
  A2-0533            `taxjson sum`'s summarize_gains / summarize_income
                     accumulators (2+ rows per ticker and term, 2
                     withholding rows, an 'other' row, all-zero rows)
  A2-0861 / A2-0906 / A2-0907
                     run_gains: the Canadian stock-dividend line and the
                     Canadian option summary keys stay out of a US book;
                     no note for a plain purchase; fees after a non-trade
                     row; by_ticker after a tainted row and with two
                     dividends; a manual-reporting row has no gain/cost
  A2-0865 / A2-0866 / A2-1532
                     checklist: a stale wash file makes audit and
                     form-export 'attention'; the t5-t3 step names the
                     TAXABLE line; the US slip and margin-interest words

All data is synthetic.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
COUNTRIES = ("canada", "usa")


def _tool(module, *args):
    env = dict(os.environ, PYTHONPATH=str(SRC), NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", f"taxjson.bin.{module}", *map(str, args)],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=180)


# ------------------------------------------------------------ fees-sum
class TestFeesSumLabels(unittest.TestCase):
    """A2-0880: the converting branch's per-brokerage line says
    'non-option' (S031-04); A2-1585: its heading and the $/UNIT column
    header (the legend's '$/UNIT is ...' does not count)."""

    def test_converting_labels(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"
            work.mkdir()
            (work / "c.json").write_text(json.dumps({
                "metadata": {"source_brokerage": "questrade"},
                "transactions": [dict(
                    action="BUYSELL", date="2025-03-03",
                    date_settle="2025-03-04", time="10:00:00",
                    symbol="ENB.TO", quantity=10, price=50.0,
                    net_amount=-500.0, currency="CAD", account="margin",
                    commission=9.95)]}))
            rates = Path(td) / "to_base.csv"
            rates.write_text("2025-03-03 12:00:00 USD CAD 1.40000\n")
            r = _tool("taxjson_fees", "--cache", work, "--to", "CAD",
                      "--rates", rates)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        header = next(ln for ln in lines if ln.startswith("BROKERAGE"))
        self.assertIn("$/UNIT", header)
        self.assertNotIn("$/SHARE", header)
        self.assertIn("Non-option vs option fees:", lines)
        row = next(ln for ln in lines
                   if ln.strip().startswith("questrade")
                   and "options" in ln)
        self.assertRegex(row, r"questrade\s+non-option 9\.95\s+options 0\.00")


# --------------------------------------------------- run views and `sum`
def _cli(root, *args):
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               NO_COLOR="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=300)


class TestRunReportLabels(unittest.TestCase):
    """A2-1585: `taxjson sum`'s NON-OPT column header and the staking
    footer of the transaction views."""

    def test_staking_footer_names_ordinary_income(self):
        from test_fix_l_runcore_a import TestReportLabelsAndTotals
        with tempfile.TemporaryDirectory() as tmp:
            root = TestReportLabelsAndTotals._views_project(None, tmp)
            ev = _cli(root, "events")
        self.assertEqual(ev.returncode, 0, ev.stderr)
        self.assertRegex(ev.stdout, r"TOTAL STAKING \(crypto, ordinary "
                                    r"income\):\s+7\.00 CAD")

    def test_sum_header_says_non_opt(self):
        tt = ("BUYSELL 2025-01-10 10:00:00 XYZ.TO 100 CAD 10.00 -1000.00 0.00\n"
              "BUYSELL 2025-06-10 10:00:00 XYZ.TO -100 CAD 12.00 1200.00 0.00\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "margin.tt").write_text(tt)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s = _cli(root, "sum")
        self.assertEqual(s.returncode, 0, s.stderr[-2000:])
        header = next(ln for ln in s.stdout.splitlines()
                      if ln.split()[:1] == ["ACCOUNT"])
        self.assertEqual(header.split()[:3], ["ACCOUNT", "NON-OPT", "OPTION"])


# ------------------------------------------------------------- fx-cash
class TestFxCashAcquired(unittest.TestCase):
    """A2-1555: `stat["acquired"] += flow` — two acquisitions of US
    dollars in the year both count."""

    @rule("CA-FX-07")
    def test_two_acquisitions_sum(self):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        from test_fix_a2_fx_cash import _rate_of, _tx
        txs = [_tx("DIVIDEND", "2026-01-10", "USD", 1000.0,
                   gross_amount=1000.0),
               _tx("BUYSELL", "2026-02-10", "USD", 500.0, qty=-5.0),
               _tx("BUYSELL", "2026-03-10", "USD", -300.0, qty=3.0)]
        doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
        usd = doc["per_currency"]["USD"]
        self.assertAlmostEqual(usd["acquired"], 1500.0)
        self.assertAlmostEqual(usd["disposed"], 300.0)


# --------------------------------------------------- summarize_gains
def _g(symbol, gain, cost, proceeds, days, term, d, **kw):
    return dict(symbol=symbol, currency="USD", proceeds=proceeds, cost=cost,
                gain=gain, days_held=days, term=term, date=d,
                action="BUYSELL", **kw)


class TestSumGainsAccumulators(unittest.TestCase):
    """A2-0533 F02 (pil), F05/F06 (st/lt), F07 (trades), F08 (hold days),
    F09 (all-zero rows skipped), F10 (option fees), F12 (TOTAL ST)."""

    ROWS = [_g("AAA.US", 100, 1000, 1100, 10, "SHORT_TERM", "2025-03-03"),
            _g("AAA.US", 50, 1000, 1050, 20, "SHORT_TERM", "2025-04-03"),
            _g("AAA.US", 200, 1000, 1200, 400, "LONG_TERM", "2025-05-03"),
            _g("AAA.US", 300, 1000, 1300, 500, "LONG_TERM", "2025-06-03"),
            _g("BBB.US", -100, 1000, 900, 6, "SHORT_TERM", "2025-03-05"),
            _g("BBB.US", 0, 0, 0, 4, "SHORT_TERM", "2025-03-06"),
            _g("AAA250620C00050000.US", 40, 100, 140, 30, "SHORT_TERM",
               "2025-05-20", commission=1.25),
            _g("AAA250620C00055000.US", 20, 100, 120, 30, "SHORT_TERM",
               "2025-05-21", commission=0.75),
            _g("ZZZ.US", 0, 0, 0, 1, "SHORT_TERM", "2025-07-01"),
            dict(symbol="CCC.US", currency="USD",
                 action="DIVIDEND_IN_LIEU", pil=12.5, date="2025-03-01"),
            dict(symbol="CCC.US", currency="USD",
                 action="DIVIDEND_IN_LIEU", pil=7.5, date="2025-06-01")]

    def _sum(self):
        from taxjson.bin.taxjson_sum_gains import summarize_gains
        return summarize_gains({"transactions": [dict(r) for r in self.ROWS],
                                "summary": {"year": 2025}})

    @rule("US-HOLD-01")
    def test_per_ticker_and_term_sums(self):
        s = self._sum()
        a = s["ticker_stats"]["AAA.US"]["USD"]
        # Options count under their underlying: 100 + 50 + 40 + 20.
        self.assertAlmostEqual(a["st_gain"], 210.0)
        self.assertAlmostEqual(a["lt_gain"], 500.0)
        self.assertAlmostEqual(s["ticker_stats"]["CCC.US"]["USD"]["pil"],
                               20.0)
        self.assertAlmostEqual(s["option_fees"]["USD"], 2.0)

    @rule("US-HOLD-01")
    def test_report_totals(self):
        from taxjson.bin.taxjson_sum_gains import format_report
        rep = format_report(self._sum(), no_color=True)
        lines = rep.splitlines()
        # The all-zero ZZZ.US row is skipped; AAA's ST/LT (its options
        # included) are summed.
        self.assertFalse(any(ln.startswith("ZZZ.US") for ln in lines), rep)
        aaa = next(ln for ln in lines if ln.startswith("AAA.US "))
        self.assertIn("[ST:   210.00 LT:   500.00]", aaa)
        total = next(ln for ln in lines if ln.startswith("TOTAL"))
        # TOTAL ST = 210 (AAA and its options) - 100 + 0 (BBB) = 110.
        self.assertIn("ST:   110.00", total)
        self.assertIn("LT:   500.00", total)
        # Avg days over the 8 trades: (10+20+400+500+6+4+30+30)/8 = 125.
        self.assertRegex(total, r"\s125\.0\s")


# -------------------------------------------------- summarize_income
class TestSumIncomeAccumulators(unittest.TestCase):
    """A2-0533 F14 (pil), F15 (withholding), F17 ('other' income), F18
    (an all-zero ticker is skipped)."""

    ROWS = [dict(action="DIVIDEND", symbol="AAA.US", currency="USD",
                 gross_amount=100.0, date="2025-03-15"),
            dict(action="TAX", symbol="AAA.US", currency="USD",
                 net_amount=15.0, date="2025-03-15"),
            dict(action="DIVIDEND", symbol="AAA.US", currency="USD",
                 gross_amount=100.0, date="2025-06-15"),
            dict(action="TAX", symbol="AAA.US", currency="USD",
                 net_amount=15.0, date="2025-06-15"),
            dict(action="DIVIDEND_IN_LIEU", symbol="AAA.US",
                 currency="USD", gross_amount=4.0, date="2025-09-15"),
            dict(action="DIVIDEND_IN_LIEU", symbol="AAA.US",
                 currency="USD", gross_amount=6.0, date="2025-12-15"),
            dict(action="OTHER_INCOME", type="other", symbol="BBB.US",
                 currency="USD", net_amount=9.0, date="2025-05-01"),
            dict(action="DIVIDEND", symbol="ZZZ.US", currency="USD",
                 gross_amount=10.0, date="2025-04-01"),
            dict(action="DIVIDEND", symbol="ZZZ.US", currency="USD",
                 gross_amount=-10.0, date="2025-04-02")]

    @rule("US-INC-03")
    def test_sums_and_skip(self):
        from taxjson.bin.taxjson_sum_income import (format_report,
                                                    summarize_income)
        s = summarize_income([dict(r) for r in self.ROWS], 2025)
        a = s["ticker_stats"]["AAA.US"]["USD"]
        self.assertAlmostEqual(a["div"], 200.0)
        self.assertAlmostEqual(a["tax"], 30.0)
        self.assertAlmostEqual(a["pil"], 10.0)
        self.assertAlmostEqual(s["ticker_stats"]["BBB.US"]["USD"]["div"],
                               9.0)
        rep = format_report(s)
        self.assertFalse(any(ln.startswith("ZZZ.US")
                             for ln in rep.splitlines()), rep)
        self.assertTrue(any(ln.startswith("BBB.US")
                            for ln in rep.splitlines()), rep)


# ------------------------------------------------------------ run_gains
def _book(sym, cur):
    from taxjson.lib.core import STOCK_DIVIDEND, TaxTransaction

    def t(i, action, d, s, q, net, **kw):
        return TaxTransaction(
            id=i, action=action, date=d, date_settle=d, time="10:00:00",
            symbol=s, quantity=q, price=abs(net / q) if q else 0.0,
            net_amount=net, currency=cur, account="m", **kw)
    ph = "PHM" + sym[3:]
    return [t("xs", "BUYSELL", "2025-01-03", ph, -10, 500.0),
            t("d1", "DIVIDEND", "2025-02-01", sym, 0, 25.0),
            t("b0", "BUYSELL", "2025-02-02", sym, 10, -100.0),
            t("b", "BUYSELL", "2025-02-03", sym, 100, -1000.0,
              commission=9.95),
            t("sd", "BUYSELL", "2025-03-03", sym, 5, 0.0,
              type=STOCK_DIVIDEND),
            t("d2", "DIVIDEND", "2025-04-01", sym, 0, 50.0),
            t("s", "BUYSELL", "2025-05-05", sym, -50, 600.0,
              commission=4.99)]


def _run_gains(country):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    sym, cur = (("ZZR.TO", "CAD") if country == "canada"
                else ("ZZR.US", "USD"))
    with tempfile.TemporaryDirectory() as td:
        ph = Path(td) / "phantoms.json"
        ph.write_text(json.dumps([{"symbol": "PHM" + sym[3:],
                                   "account": "m"}]))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = run_gains(_book(sym, cur), (), (), GainsRequest(
                country=country, year=2025, taxable=True,
                incomplete_history=ph))
    return sym, cur, r, err.getvalue()


class TestRunGainsPins(unittest.TestCase):

    @rule("CA-STKDIV-01")
    @rule_absent("CA-STKDIV-01", country="usa")
    @rule("US-STKDIV-01")
    def test_stock_dividend_line_and_option_keys_by_country(self):
        # A2-0861: the Canadian ATTENTION line and option summary keys
        # (pipeline `if req.country == 'canada'` gates) never reach a US
        # book; A2-0906 / A2-0907 m1592: no line for a plain purchase.
        _s, _c, ca, ca_err = _run_gains("canada")
        _s, _c, us, us_err = _run_gains("usa")
        lines = [ln for ln in ca_err.splitlines() if "stock dividend" in ln]
        self.assertEqual(len(lines), 1, ca_err)
        self.assertIn("ZZR.TO: stock dividend of 5 share(s) on 2025-03-03",
                      lines[0])
        self.assertIn("in Canada it is a dividend", lines[0])
        self.assertNotIn("in Canada", us_err)
        self.assertNotIn("declared amount", us_err)
        self.assertIn("§305(a)", us_err)
        keys = ("option_premium_timing", "option_grant_since",
                "option_buyback_loss_superficial")
        for k in keys:
            self.assertIn(k, ca["summary"])
            self.assertNotIn(k, us["summary"])

    @rule("CA-ACB-02")
    @rule("US-BASIS-01")
    def test_fees_by_ticker_and_manual_rows(self):
        # A2-0907: fees after a non-trade row (m1580), by_ticker after a
        # tainted row (m1607) and with two dividends (m1609), and a
        # manual-reporting row without gain or cost (m1660 / m1661).
        for country in COUNTRIES:
            sym, cur, r, _err = _run_gains(country)
            fees = r["summary"]["total_fees_by_currency"]
            self.assertAlmostEqual(fees[cur]["total"], 14.94, msg=country)
            self.assertAlmostEqual(fees[cur]["stocks"], 14.94, msg=country)
            self.assertIn(sym, r["by_ticker"], country)
            self.assertAlmostEqual(r["by_ticker"][sym]["total_div"], 75.0,
                                   msg=country)
            self.assertNotIn("PHM" + sym[3:], r["by_ticker"], country)
            manual = r["manual_reporting_required"]
            self.assertEqual([m["symbol"] for m in manual],
                             ["PHM" + sym[3:]], country)
            for k in ("gain", "cost"):
                self.assertNotIn(k, manual[0], (country, k))


# ------------------------------------------------------------ checklist
def _cl_ctx(root, country="canada", table=None, accounts=None):
    from taxjson.lib import checklist as cl
    cfg = {"settings": {"year": 2025, "country": country,
                        "base_currency": "USD" if country == "usa"
                        else "CAD"},
           "accounts": accounts or {"margin": {"type": "taxable"}}}
    table = table or {}
    return cl.Ctx(root=root, cfg=cfg, year=2025, today=date(2026, 3, 1),
                  run_sub=lambda argv, timeout=900:
                  table.get(argv[0], (0, "", "")))


class TestChecklistPins(unittest.TestCase):

    @rule("CA-SL-09")
    def test_stale_wash_file_is_attention_for_audit_and_form_export(self):
        # A2-0865: _books_state's `stale_wash_inputs` guard.
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            doc = json.dumps({"summary": {"year": 2025}, "transactions": []})
            (work / "margin_gains_wash.json").write_text(doc)
            plain = work / "margin_gains.json"
            plain.write_text(doc)
            later = time.time() + 10
            os.utime(plain, (later, later))
            ctx = _cl_ctx(root, table={"audit": (
                0, "pipeline tie-out 2 tied, 0 MISMATCHED, 0 not found",
                "")})
            for fn in (cl.d_audit, cl.d_form_export):
                r = fn(ctx)
                self.assertEqual(r.status, "attention", (fn.__name__, r))
                self.assertIn("older than their inputs", r.detail)

    def test_t5_t3_names_the_taxable_line(self):
        # A2-0866 (S068-14).
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            r = cl.DETECTORS["t5-t3"](_cl_ctx(Path(td)))
        self.assertIn("TAXABLE line of `taxjson divs-sum`", r.detail)

    @rule("US-RPT-09")
    def test_us_slip_and_margin_interest_words(self):
        # A2-1532: d_t5008's slip name and d_fees's investment interest.
        from taxjson.lib import checklist as cl
        out = {}
        with tempfile.TemporaryDirectory() as td:
            for c in COUNTRIES:
                root = Path(td) / c
                root.mkdir()
                ctx = _cl_ctx(root, country=c)
                out[c] = (cl.d_t5008(ctx).detail, cl.d_fees(ctx).detail)
        self.assertIn("put the broker 1099-B CSVs", out["usa"][0])
        self.assertNotIn("T5008", out["usa"][0])
        self.assertIn("investment interest (Form 4952)", out["usa"][1])
        self.assertNotIn("22100", out["usa"][1])
        self.assertIn("put the broker T5008 CSVs", out["canada"][0])
        self.assertIn("line 22100", out["canada"][1])


if __name__ == "__main__":
    unittest.main()
