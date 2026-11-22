"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.model.transaction import TaxTransaction
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


def _t(**kw):
    d = dict(action='BUYSELL', date='2026-01-05', time='09:30:00',
             symbol='Q.TO', quantity=100.0, currency='CAD',
             net_amount=1000.0, account='A')
    d.update(kw)
    return TaxTransaction(**d)


if __name__ == '__main__':
    unittest.main()


class TestFullAuditRound2Fixes(unittest.TestCase):
    """Pins for the 2026-09-04 full-audit fixes (reporting, pipeline,
    contracts). Engine pins live in test_engine_invariants.

    Formerly: test_audit_2026_09_fixes.TestFullAuditRound2Fixes"""

    def test_rbc_demo_detects_as_rbc(self):
        # "Account Number" preamble routed real RBC files to the
        # Webull parser -> silently empty books.
        from taxjson.bin.taxjson_detect_brokerage import (
            detect_brokerage)
        p = REPO_ROOT / "examples" / "rbc_direct_demo.csv"
        self.assertEqual(detect_brokerage(p), "rbc_direct")

    def test_webull_demo_still_webull(self):
        from taxjson.bin.taxjson_detect_brokerage import (
            detect_brokerage)
        p = REPO_ROOT / "examples" / "webull_demo.csv"
        self.assertEqual(detect_brokerage(p), "webull")

    def test_readme_adjust_example_parses(self):
        # The documented ROC line crashed the parser (wrong field
        # order in the docs). Whatever the README shows must parse.
        import re
        from taxjson.lib.tt_format import parse_tt_line
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"^\s*(ADJUST \S.*?)(?:#.*)?$", readme,
                      re.MULTILINE)
        self.assertIsNotNone(m, "README lost its ADJUST example")
        row = parse_tt_line(m.group(1).strip())
        self.assertEqual(row["action"], "ADJUST")
        self.assertAlmostEqual(row["net_amount"], -120.00, places=2)

    def test_reconcile_slips_short_swap(self):
        # Engine short convention needs the proceeds/cost SWAP; sign
        # stripping alone mismatched by exactly the gain.
        from taxjson.lib.slip_symbols import load_computed
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [
                {"symbol": "XYZ.TO", "date": "2026-02-10",
                 "date_settle": "2026-02-11", "qty": 100.0,
                 "gain": 980.0, "direction": "SHORT",
                 "proceeds": -4010.0, "cost": -4990.0}]}))
            rec = load_computed([g], 2026)["XYZ"]
        self.assertAlmostEqual(rec["proceeds_net"], 4990.0, places=2)
        self.assertAlmostEqual(rec["cost"], 4010.0, places=2)

    def test_reconcile_slips_sees_tainted_rows(self):
        from taxjson.lib.slip_symbols import load_computed
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({
                "transactions": [],
                "manual_reporting_required": [
                    {"symbol": "GHOST.TO", "date": "2026-03-01",
                     "date_settle": "2026-03-02", "qty": 10.0,
                     "gain": 0.0, "proceeds": 500.0, "cost": 0.0}]}))
            out = load_computed([g], 2026)
        self.assertIn("GHOST", out)
        self.assertEqual(out["GHOST"]["tainted_rows"], 1)

    def test_report_income_uses_base_book(self):
        from taxjson.lib.account_report import build_account_report
        gains_doc = {"transactions": [
            {"action": "DIVIDEND", "date": "2026-01-10",
             "symbol": "A.TO", "dividend": 100.0, "qty": 0}],
            "summary": {}}
        base_rows = [
            {"action": "DIVIDEND", "date": "2026-01-10",
             "symbol": "A.TO", "net_amount": 100.0,
             "gross_amount": 100.0, "currency": "CAD",
             "account": "m"},
            {"action": "TAX", "date": "2026-01-10", "symbol": "A.TO",
             "net_amount": 15.0, "currency": "CAD", "account": "m"}]
        rep = build_account_report(gains_doc, "m", basis="pre-wash",
                                   base_transactions=base_rows)
        inc = rep["income"]
        total = json.dumps(inc)
        self.assertIn("100", total,
                      f"income section still empty: {inc}")

    def test_filed_missing_history_read_from_root(self):
        src = (REPO_ROOT / "src/taxjson/bin/taxjson_filed.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn('cache / "missing_history.json"', src)
        self.assertNotIn('missing_history_path(cache)', src)
        self.assertIn('missing_history_arg(cache.parent)', src)

    def test_ambiguous_error_handled_by_explain(self):
        src = (REPO_ROOT / "src/taxjson/bin/taxjson_explain.py"
               ).read_text(encoding="utf-8")
        self.assertIn("AmbiguousTransferDateError", src)

    def test_cross_account_netting_refuses_near_taxable_sales(self):
        # contribution into rrsp + withdrawal from rrsp2 near a
        # taxable loss must SURVIVE netting (and then the engine's
        # guard forces a declaration).
        from taxjson.lib.pipeline import _net_cross_account_transfers
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-03-05', quantity=100, net_amount=8000.0),
                _t(action='TRANSFER', account='rrsp2',
                   date='2024-03-20', quantity=-100,
                   net_amount=8000.0)]
        main = [_t(account='margin', date='2024-03-10', quantity=-100,
                   net_amount=8000.0)]
        out = _net_cross_account_transfers(shel, main_transactions=main)
        self.assertEqual(len(out), 2, "must not net near a sale")
        out2 = _net_cross_account_transfers(shel, main_transactions=[])
        self.assertEqual(out2, [], "far from sales it still nets")
