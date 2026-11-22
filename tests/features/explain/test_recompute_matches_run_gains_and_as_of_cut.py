"""Re-audit-2 fixes, filing-reports list A (A2-...): taxjson-audit and
taxjson-explain recompute the books the way run_gains does (income
re-dating, option grant basis, US per-account FIFO, deemed gains,
phantom-basis manual rows, declared contract size), and the T1135 cost
walk follows the engine.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_a
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _support.filing_report_books import ACCT, _row, _quiet, _Books


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
    """Formerly: test_fix_a2_filrep_a.TestAuditRecomputesLikeRunGains"""

    @rule(R.CA_INC_DATE_ROC_TRUST)
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

    @rule(R.CA_INC_DATE_ROC_TRUST)
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

    @rule(R.CA_INC_DATE_ROC_TRUST)
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

    @rule(R.CA_OPT_01, R.CA_OPT_02)
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

    @rule(R.US_BASIS_01)
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

    @rule(R.CA_ACB_07)
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

    @rule(R.CA_ACB_07)
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

    @rule(R.CA_ACB_11)
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

    @rule(R.CA_ACB_11)
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
    """Formerly: test_fix_a2_filrep_a.TestAsOfCutAfterRedating"""

    @rule(R.CA_INC_DATE_ROC_TRUST)
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


if __name__ == "__main__":
    unittest.main()
