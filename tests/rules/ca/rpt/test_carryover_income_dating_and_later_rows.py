"""Re-audit-2 filing-lock fixes, carryover side: the income-dating
settings in carryover's (and t1135's) full-history engine pass, the
filed-year locks it reads (unreadable, non-finite, reached through
prior_year_record, seeding the rebuilt prior years), rows after the
project year, box-18 slip gains, the base-currency check and the
standalone defaults.

Formerly: test_fix_a2_locks_co"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as R
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


def _row(i, date, sym, qty, net, action="BUYSELL", **kw):
    r = {"id": i, "action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": sym, "quantity": qty,
         "price": abs(net / qty) if qty else 0.0, "net_amount": net,
         "currency": kw.pop("currency", "CAD"), "account": "margin"}
    r.update(kw)
    return r


def _project(root, year, rows, extra="", country="canada", cur="CAD"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": rows}))
    return root


def _carryover(root, *extra):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "carryover", *extra], cwd=REPO_ROOT, capture_output=True,
        text=True, stdin=subprocess.DEVNULL, timeout=300)


def _rows_by_year(stdout):
    return {r["year"]: r for r in json.loads(stdout)["rows"]}


# 100 ABC.TO for 1,000; a ROC of 500 recorded 2025-12-31, paid
# 2026-01-15; 50 sold 2026-01-06 for 450 (between record and pay date).
ROC_BOOK = [
    _row("b1", "2025-06-02", "ABC.TO", 100, -1000.0),
    _row("r1", "2026-01-15", "ABC.TO", 0, -500.0, action="ADJUST",
         type="roc", record_date="2025-12-31",
         description="ABC CORP RETURN OF CAPITAL REC 12/31/25 PAY 01/15/26"),
    _row("s1", "2026-01-06", "ABC.TO", -50, 450.0),
]


class TestCarryoverIncomeDating(unittest.TestCase):
    """A2-0123 / A2-0339 / A2-0341 / A2-1141 / A2-0337 / A2-0340: the
    ledger's engine pass ignored [settings] corporate_distributions.

    Formerly: test_fix_a2_locks_co.TestCarryoverIncomeDating"""

    @rule(R.CA_INC_DATE_ROC)
    def test_listed_corporation_roc_on_pay_date(self):
        with tempfile.TemporaryDirectory() as td:
            listed = _project(Path(td) / "listed", 2026, ROC_BOOK,
                              'corporate_distributions = ["ABC.TO"]\n')
            r = _carryover(listed, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            # ROC paid after the sale: the 50 cost 500, sold for 450.
            self.assertAlmostEqual(
                _rows_by_year(r.stdout)[2026]["net_gain"], -50.0, places=2)

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_unlisted_trust_roc_on_record_date(self):
        with tempfile.TemporaryDirectory() as td:
            trust = _project(Path(td) / "trust", 2026, ROC_BOOK)
            r = _carryover(trust, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertAlmostEqual(
                _rows_by_year(r.stdout)[2026]["net_gain"], 200.0, places=2)

    def test_standalone_accepts_corporate_distribution(self):
        from taxjson.bin import taxjson_carryover as C
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": ROC_BOOK}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = C.main([str(b), "--country", "canada", "--json",
                             "--corporate-distribution", "ABC.TO"])
            self.assertEqual(rc, 0)
            self.assertAlmostEqual(
                _rows_by_year(out.getvalue())[2026]["net_gain"], -50.0,
                places=2)

    def test_t1135_full_history_pass_gets_the_income_rules(self):
        from taxjson.bin import taxjson_t1135 as T
        seen = []

        def fake(txs, sh, aff, req):
            seen.append(req)
            return {"wash_sales": []}
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": ROC_BOOK}))
            with mock.patch("taxjson.lib.pipeline.run_gains", fake):
                T.full_history_wash_sales(
                    [b], income_rules={"corporate_distributions":
                                       ("ABC.TO",)})
        self.assertEqual(tuple(seen[0].corporate_distributions),
                         ("ABC.TO",))


class TestCarryoverAfterProjectYear(unittest.TestCase):
    """Formerly: test_fix_a2_locks_co.TestCarryoverAfterProjectYear"""
    @rule(R.CA_RPT_10)
    def test_rows_after_the_project_year_are_partial(self):
        # A2-0665: a January trade after the project year produced a T1A
        # suggestion and the "carryforward after" balance.
        book = [_row("b1", "2024-02-01", "XYZ.TO", 200, -20000.0),
                _row("s1", "2024-03-03", "XYZ.TO", -100, 18000.0),
                _row("s2", "2025-01-06", "XYZ.TO", -100, 6000.0)]
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2024, book)
            r = _carryover(p, "--json")
            doc = json.loads(r.stdout)
            rows = {x["year"]: x for x in doc["rows"]}
            self.assertTrue(rows[2025]["after_project_year"])
            self.assertEqual(rows[2025]["carryback_candidates"], [])
            self.assertAlmostEqual(doc["final_carryforward"], 0.0)
            t = _carryover(p)
            self.assertNotIn("carried back to 2024", t.stdout)
            self.assertIn("carryforward after 2024", t.stdout)
            self.assertIn("2025", t.stdout)
            self.assertIn("partial", t.stdout)


if __name__ == "__main__":
    unittest.main()
