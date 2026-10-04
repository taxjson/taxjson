"""Re-audit-2 fixes, filing-reports list A (A2-...): taxjson-audit and
taxjson-explain recompute the books the way run_gains does (income
re-dating, option grant basis, US per-account FIFO, deemed gains,
phantom-basis manual rows, declared contract size), and the T1135 cost
walk follows the engine.

All data is synthetic (fake account ids, invented tickers).
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.core import TaxTransaction

ACCT = "55500001"  # pii-ok (synthetic)


def _row(action, date, sym, qty=0.0, net=0.0, *, settle=None,
         acct=ACCT, currency="CAD", price=None, **kw):
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action=action, date=date, time="10:00:00",
                          date_settle=settle or date, symbol=sym,
                          quantity=float(qty), price=float(price),
                          net_amount=float(net), account=acct,
                          currency=currency, **kw).to_dict()


def _quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    rc = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(*a, **kw)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class _Books:
    """base.json (the engine input) + the pipeline's saved gains file,
    written by the real run_gains with the given request."""

    def __init__(self, tmp: Path, rows, year, *, phantoms=None, **req):
        from taxjson.lib.core import load_transactions
        from taxjson.lib.pipeline import GainsRequest, run_gains
        self.tmp = tmp
        self.base = tmp / "base.json"
        self.base.write_text(json.dumps({"transactions": rows}),
                             encoding="utf-8")
        self.phantoms = None
        if phantoms is not None:
            self.phantoms = tmp / "missing_history.json"
            self.phantoms.write_text(json.dumps(phantoms), encoding="utf-8")
        greq = GainsRequest(year=year, taxable=True,
                            incomplete_history=self.phantoms, **req)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = run_gains(load_transactions(self.base), req=greq)
        self.results = res
        self.gains = tmp / "gains.json"
        self.gains.write_text(json.dumps(res, default=str),
                              encoding="utf-8")

    def audit(self, *extra):
        from taxjson.bin.taxjson_audit import main
        args = ["--base", str(self.base), "--check", str(self.gains),
                "--json", "--no-color"] + list(extra)
        if self.phantoms is not None:
            args += ["--incomplete-history", str(self.phantoms)]
        rc, out, err = _quiet(main, args)
        doc = json.loads(out) if out.strip() else {}
        return rc, doc, err


def _trust_roc_book():
    # 100 QZT.TO bought 2024 for 1,000, sold 2025-12-29 for 1,200; a
    # trust ROC of 100 with record date 2025-12-26 paid 2026-01-15.
    return [
        _row("BUYSELL", "2024-03-01", "QZT.TO", 100, 1000),
        _row("BUYSELL", "2025-12-29", "QZT.TO", -100, 1200),
        _row("ADJUST", "2026-01-15", "QZT.TO", 0, -100, type="roc",
             record_date="2025-12-26"),
    ]


class TestAuditRecomputesLikeRunGains(unittest.TestCase):

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_a2_0033_trust_roc_record_date_ties_out(self):
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), _trust_roc_book(), 2025, country="canada")
            booked = sum(g["gain"] for g in b.results["transactions"]
                         if g.get("qty"))
            self.assertAlmostEqual(booked, 300.0, places=2)
            rc, doc, err = b.audit("--country", "canada", "--year", "2025",
                                   "--check-year", "2025")
            self.assertEqual(rc, 0, err)
            self.assertFalse(doc["failed"])
            self.assertAlmostEqual(doc["total_gain"], 300.0, places=2)
            self.assertNotIn("EMPTY pool", err)

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_a2_0033_explain_traces_the_record_date_cost(self):
        from taxjson.bin import taxjson_explain
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "base.json"
            p.write_text(json.dumps({"transactions": _trust_roc_book()}),
                         encoding="utf-8")
            import sys
            argv = sys.argv
            sys.argv = ["taxjson-explain", "--country", "canada", "--list",
                        "--year", "2025", str(p)]
            try:
                rc, out, err = _quiet(taxjson_explain.main)
            finally:
                sys.argv = argv
            self.assertRegex(out, r"cost=\s+900\.0000.*gain=\s+\+300\.0000")

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_a2_0033_corporate_distribution_keeps_pay_date(self):
        # A listed corporation's ROC stays on its pay date — in the run
        # and in the audit (the wrapper forwards the setting).
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), _trust_roc_book(), 2025, country="canada",
                       corporate_distributions=("QZT.TO",))
            rc, doc, err = b.audit("--country", "canada", "--year", "2025",
                                   "--check-year", "2025",
                                   "--corporate-distribution", "QZT.TO")
            self.assertEqual(rc, 0, err)
            self.assertAlmostEqual(doc["total_gain"], 200.0, places=2)

    @rule("CA-OPT-01", "CA-OPT-02")
    def test_a2_0314_grant_since_on_the_trade_date_basis(self):
        # Trade-basis project, grant timing from 2025: a call written
        # 2024-12-31 (settles 2025-01-02) keeps close timing.
        rows = [
            _row("BUYSELL", "2024-12-31", "QZC250321C00050000.TO", -1, 300,
                 settle="2025-01-02"),
            _row("BUYSELL", "2025-01-15", "QZC250321C00050000.TO", 1, 100,
                 settle="2025-01-16"),
        ]
        with tempfile.TemporaryDirectory() as d:
            for year, want in ((2024, 0.0), (2025, 200.0)):
                b = _Books(Path(d), rows, year, country="canada",
                           tax_date="trade", option_premium_timing="grant",
                           option_grant_since=2025)
                rc, doc, err = b.audit(
                    "--country", "canada", "--tax-date", "trade",
                    "--year", str(year), "--check-year", str(year),
                    "--option-premium-timing", "grant",
                    "--option-grant-since", "2025")
                self.assertEqual(rc, 0, (year, err, doc.get(
                    "reconciliation_failures")))
                self.assertAlmostEqual(doc["total_gain"], want, places=2,
                                       msg=year)

    @rule("US-BASIS-01")
    def test_a2_0318_us_audit_is_per_account_by_default(self):
        m1, m2 = "U5550001", "U5550002"  # pii-ok (synthetic)
        rows = [
            _row("BUYSELL", "2025-01-02", "QZA.US", 100, 5000, acct=m2,
                 currency="USD"),
            _row("BUYSELL", "2025-01-03", "QZA.US", 100, 10000, acct=m1,
                 currency="USD"),
            _row("BUYSELL", "2025-03-03", "QZA.US", -100, 11000, acct=m1,
                 currency="USD"),
        ]
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), rows, 2025, country="usa")
            rc, doc, err = b.audit("--country", "usa", "--year", "2025",
                                   "--check-year", "2025")
            self.assertEqual(rc, 0, err)
            self.assertAlmostEqual(doc["total_gain"], 1000.0, places=2)

    @rule("CA-ACB-07")
    def test_a2_0316_deemed_gain_is_audited(self):
        rows = [
            _row("BUYSELL", "2025-01-02", "QZR.TO", 100, 1000),
            _row("BUYSELL", "2025-02-03", "QZR.TO", -100, 1200),
            _row("ADJUST", "2025-03-31", "QZR.TO", 0, -100, type="roc"),
        ]
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), rows, 2025, country="canada")
            rc, doc, err = b.audit("--country", "canada", "--year", "2025",
                                   "--check-year", "2025")
            self.assertEqual(rc, 0, err)
            self.assertAlmostEqual(doc["total_gain"], 300.0, places=2)
            deemed = [e for e in doc["events"] if e.get("deemed")]
            self.assertEqual(len(deemed), 1)
            self.assertTrue(deemed[0]["tie_out"]["ties"])

    @rule("CA-ACB-07")
    def test_a2_0316_tampered_deemed_gain_fails(self):
        rows = [
            _row("BUYSELL", "2025-01-02", "QZR.TO", 100, 1000),
            _row("BUYSELL", "2025-02-03", "QZR.TO", -100, 1200),
            _row("ADJUST", "2025-03-31", "QZR.TO", 0, -100, type="roc"),
        ]
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), rows, 2025, country="canada")
            doc = json.loads(b.gains.read_text())
            doc["transactions"] = [g for g in doc["transactions"]
                                   if not g.get("deemed")]
            b.gains.write_text(json.dumps(doc))
            rc, out, err = b.audit("--country", "canada", "--year", "2025",
                                   "--check-year", "2025")
            self.assertEqual(rc, 1)

    @rule("CA-ACB-11")
    def test_a2_1098_phantom_rows_tie_to_manual_reporting(self):
        rows = [_row("BUYSELL", "2025-01-10", "QZP.TO", -100, 3000)]
        ph = [{"symbol": "QZP.TO", "account": ACCT}]
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), rows, 2025, phantoms=ph, country="canada")
            self.assertTrue(b.results.get("manual_reporting_required"))
            rc, doc, err = b.audit("--country", "canada", "--year", "2025",
                                   "--check-year", "2025")
            self.assertEqual(rc, 0, (err, doc.get(
                "reconciliation_failures")))
            self.assertEqual(doc["total_gain"], 0.0)
            ev = doc["events"][0]
            self.assertTrue(ev["manual_reporting"])
            self.assertTrue(ev["tie_out"]["manual"])
            # Text mode says so instead of "stale books".
            from taxjson.bin.taxjson_audit import main
            rc, out, err = _quiet(main, [
                "--base", str(b.base), "--check", str(b.gains),
                "--incomplete-history", str(b.phantoms), "--no-color",
                "--country", "canada", "--year", "2025",
                "--check-year", "2025"])
            self.assertEqual(rc, 0)
            self.assertIn("MANUAL REPORTING REQUIRED", out)
            self.assertNotIn("MISSING", out + err)
            self.assertNotIn("stale or truncated", out + err)

    @rule("CA-ACB-11")
    def test_a2_1098_manual_row_missing_from_books_fails(self):
        rows = [_row("BUYSELL", "2025-01-10", "QZP.TO", -100, 3000)]
        ph = [{"symbol": "QZP.TO", "account": ACCT}]
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), rows, 2025, phantoms=ph, country="canada")
            doc = json.loads(b.gains.read_text())
            doc.pop("manual_reporting_required")
            b.gains.write_text(json.dumps(doc))
            rc, out, err = b.audit("--country", "canada", "--year", "2025",
                                   "--check-year", "2025")
            self.assertEqual(rc, 1)

    def test_a2_1099_per_share_uses_declared_size(self):
        from taxjson.bin.taxjson_audit import main
        sym = "QZM250620C00010000.US"
        rows = [_row("BUYSELL", "2025-01-02", sym, 1, 20, multiplier=10.0),
                _row("BUYSELL", "2025-02-03", sym, -1, 10, multiplier=10.0)]
        with tempfile.TemporaryDirectory() as d:
            b = _Books(Path(d), rows, 2025, country="canada")
            rc, out, err = _quiet(main, [
                "--base", str(b.base), "--check", str(b.gains),
                "--no-color", "--no-trace", "--country", "canada"])
            self.assertEqual(rc, 0, err)
            self.assertIn("1 contract × 10 sh @ 1.0000", out)
            self.assertIn("1 contract × 10 sh @ 2.0000", out)


class TestAsOfCutAfterRedating(unittest.TestCase):

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_a2_0315_as_of_cut_sees_the_record_date_roc(self):
        # `list --date` runs taxjson-gains --as-of: a trust ROC with a
        # record date before the cutoff and a January pay date lowers
        # the cost on the as-of date (already fixed by A2-0554; pinned).
        import subprocess
        import sys
        rows = [_row("BUYSELL", "2025-01-02", "QZT.TO", 100, 1000),
                _row("ADJUST", "2026-01-15", "QZT.TO", 0, -300, type="roc",
                     record_date="2025-12-20")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "base.json"
            p.write_text(json.dumps({"transactions": rows}),
                         encoding="utf-8")
            res = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--as-of", "2025-12-25",
                 "--no-wash", str(p)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            inv = json.loads(res.stdout)["inventory"]
            self.assertAlmostEqual(inv[0]["total_cost"], 700.0, places=2)


# ------------------------------------------------------------------ T1135

def _t(action="BUYSELL", date="2025-01-15", symbol="QZA.US", qty=0.0,
       net=0.0, settle=None, currency="CAD", acct=ACCT, **extra):
    d = {"action": action, "date": date, "date_settle": settle or date,
         "time": "09:30:00", "symbol": symbol, "quantity": qty,
         "net_amount": net, "symbol_new": "", "currency": currency,
         "account": acct}
    d.update(extra)
    return d


def _t1135(argv):
    from taxjson.bin.taxjson_t1135 import main
    return _quiet(main, argv)


class TestT1135CostWalk(unittest.TestCase):

    def walk(self, rows, year=2025, today=None):
        from taxjson.bin.taxjson_t1135 import walk_costs
        return walk_costs(rows, year, {}, "settle", today=today)

    @rule("CA-RPT-12")
    def test_a2_0034_empty_pool_roc_does_not_reach_the_rebuy(self):
        rows = [_t(date="2024-06-03", qty=1000, net=-100000.0),
                _t(date="2025-02-03", qty=-1000, net=100000.0),
                _t("ADJUST", "2025-03-31", qty=0, net=-8000.0, type="roc"),
                _t(date="2025-05-01", qty=1000, net=-105000.0)]
        w = self.walk(rows)
        self.assertEqual(w["max_total_cost"], 105000.0)
        self.assertEqual(w["per_symbol"]["QZA.US"]["year_end_cost"],
                         105000.0)

    @rule("CA-RPT-12")
    def test_a2_0115_roc_beyond_acb_leaves_cost_nil(self):
        rows = [_t(date="2025-01-03", qty=1000, net=-1000.0),
                _t("ADJUST", "2025-02-03", qty=0, net=-1500.0, type="roc"),
                _t(date="2025-03-03", qty=10050, net=-100500.0)]
        w = self.walk(rows)
        self.assertEqual(w["max_total_cost"], 100500.0)

    @rule("CA-RPT-12")
    def test_a2_0115_cli_verdict_flips_to_required(self):
        rows = [_t(date="2025-01-03", qty=1000, net=-1000.0),
                _t("ADJUST", "2025-02-03", qty=0, net=-1500.0, type="roc"),
                _t(date="2025-03-03", qty=10050, net=-100500.0)]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            rc, out, err = _t1135([str(p), "--year", "2025", "--json",
                                   "--year-wash-only"])
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["filing_required"])

    @rule("CA-RPT-12")
    def test_a2_0321_basis_increase_and_wash_still_apply(self):
        # A positive ADJUST (a notional distribution) on an empty pool
        # joins the next purchase (CA-ACB-13), as in the engine.
        rows = [_t(date="2025-01-03", qty=10, net=-1000.0),
                _t(date="2025-02-03", qty=-10, net=1100.0),
                _t("ADJUST", "2025-03-03", qty=0, net=50.0),
                _t(date="2025-04-03", qty=10, net=-1000.0)]
        w = self.walk(rows)
        self.assertEqual(w["per_symbol"]["QZA.US"]["year_end_cost"],
                         1050.0)

    @rule("CA-RPT-12")
    def test_a2_1106_class_root_call_premium_folds(self):
        rows = [_t(date="2025-01-03", symbol="BRKB250620C00050000.US",
                   qty=1, net=-500.0),
                _t("ASSIGN", "2025-06-20", symbol="BRKB250620C00050000.US",
                   qty=-1, net=0.0),
                _t("ASSIGN", "2025-06-20", symbol="BRK.B.US", qty=100,
                   net=-5000.0)]
        w = self.walk(rows)
        self.assertEqual(w["per_symbol"]["BRK.B.US"]["year_end_cost"],
                         5500.0)

    @rule("CA-RPT-12")
    def test_a2_0328_class_root_put_premium_folds(self):
        # A written RCI put assigned into RCI.B.TO... on a US listing
        # here so the walk reports it: premium lowers the shares' cost.
        rows = [_t(date="2025-01-03", symbol="QZB250620P00050000.US",
                   qty=-1, net=300.0),
                _t("ASSIGN", "2025-06-20", symbol="QZB250620P00050000.US",
                   qty=1, net=0.0),
                _t("ASSIGN", "2025-06-20", symbol="QZB.B.US", qty=100,
                   net=-5000.0)]
        w = self.walk(rows)
        self.assertEqual(w["per_symbol"]["QZB.B.US"]["year_end_cost"],
                         4700.0)

    def test_a2_1121_dec31_expiry_is_named(self):
        rows = [_t(date="2025-03-03", symbol="QZQ251231C00010000.US",
                   qty=1, net=-293.52),
                _t(date="2025-03-03", symbol="QZQ251219C00010000.US",
                   qty=1, net=-100.0)]
        w = self.walk(rows, today="2026-10-01")
        self.assertEqual(w["expired_options_held"],
                         ["QZQ251219C00010000.US", "QZQ251231C00010000.US"])
        # In an unfinished year a contract is not expired on its day.
        w = self.walk(rows, today="2025-12-19")
        self.assertEqual(w["expired_options_held"], [])


class TestT1135Report(unittest.TestCase):

    @rule("CA-RPT-02")
    def test_a2_0332_canadian_isin_on_us_listing_is_named(self):
        rows = [_t(date="2025-01-03", symbol="QZBT.US", qty=1500,
                   net=-202501.35),
                _t("DIVIDEND", "2025-03-03", symbol="QZBT.US", qty=0,
                   net=100.0, issuer_country="CA")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            rc, out, err = _t1135([str(p), "--year", "2025",
                                   "--year-wash-only"])
            self.assertEqual(rc, 0, err)
            self.assertIn("Canadian ISIN", err)
            self.assertIn("QZBT.US", out.split("Canadian ISIN")[-1])
            m = Path(d) / "ticker.map"
            m.write_text("T1135 QZBT.US CA\n")
            rc, out, err = _t1135([str(p), "--year", "2025", "--map",
                                   str(m), "--year-wash-only"])
            self.assertNotIn("Canadian ISIN", err + out)
            self.assertIn("no T1135 required", out)

    def test_a2_0660_refusal_does_not_advise_a_relabel(self):
        rows = [_t(date="2025-02-03", symbol="MSFT.US", qty=200,
                   net=-80000.0, currency="USD")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            rc, out, err = _t1135([str(p), "--year", "2025"])
            self.assertEqual(rc, 2)
            self.assertNotIn("--base-currency matching", err)
            rc, out, err = _t1135([str(p), "--year", "2025",
                                   "--base-currency", "USD"])
            self.assertEqual(rc, 2)
            self.assertNotIn("no T1135 required", out)

    @rule("CA-RPT-12")
    def test_a2_0661_s260_pil_counts_as_income(self):
        from taxjson.bin.taxjson_t1135 import join_income_gains
        doc = {"transactions": [
            {"action": "DIVIDEND_IN_LIEU", "date": "2025-03-03",
             "date_settle": "2025-03-03", "symbol": "SHOP.US",
             "dividend": 25.0, "pil": 0.0, "deemed_dividend": "ITA s.260"},
            {"action": "DIVIDEND_IN_LIEU", "date": "2025-04-03",
             "date_settle": "2025-04-03", "symbol": "SHOP.US",
             "pil": 5.0}]}
        with tempfile.TemporaryDirectory() as d:
            g = Path(d) / "margin_gains.json"
            g.write_text(json.dumps(doc))
            inc = join_income_gains([g], 2025)
        self.assertAlmostEqual(inc["SHOP.US"]["income"], 30.0)


if __name__ == "__main__":
    unittest.main()
