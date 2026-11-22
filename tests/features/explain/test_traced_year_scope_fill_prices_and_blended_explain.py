"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _paths
from _support.tt_cash_project import _toml, _TT_CASH, _tt_project


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()


class TestAuditYearScope(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestAuditYearScope"""
    def test_other_year_is_not_called_stale(self):
        """R1-191: the saved gains files hold one year."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, year=2026, cash=_TT_CASH + (
                "BUYSELL 2026-01-10 10:00:00 YYY.TO 10 CAD 30.00 -300.00 "
                "0.00\nBUYSELL 2026-02-10 10:00:00 YYY.TO -10 CAD 31.00 "
                "310.00 0.00\n"))
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            for extra in (["--year", "2025"], ["--all-years"], []):
                r = _cli(root, "audit", "--summary", *extra)
                self.assertEqual(r.returncode, 0,
                                 (extra, r.stdout[-1500:], r.stderr[-800:]))
                self.assertNotIn("stale or truncated", r.stdout + r.stderr)

    def test_many_rounded_rows_still_tie(self):
        """S026-16: 4-dp rounding of hundreds of rows is not a failure."""
        rows = []
        for i in range(400):
            sym = f"Q{i:03d}.TO"
            rows.append(f"BUYSELL 2025-02-03 10:00:00 {sym} 3 CAD 3.3333 "
                        f"-10.00 0.00\n")
            rows.append(f"BUYSELL 2025-05-05 10:00:00 {sym} -1 CAD 5.00 "
                        f"5.00 0.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _tt_project(root, cash="".join(rows))
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "audit", "--summary")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:])
        self.assertNotIn("do not tie", r.stdout + r.stderr)


class TestAuditPricedByFill(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestAuditPricedByFill"""
    def test_fill_priced_row_ties(self):
        """R1-271: a coin fee parsed at 0.00 and priced by the fill
        stage ties against the book when the filled row is supplied."""
        from decimal import Decimal
        from taxjson.bin import taxjson_audit as A
        gid = "fee1"
        g = {"id": gid, "symbol": "QTK", "date": "2026-05-18",
             "date_settle": "2026-05-18", "qty": 0.003, "gain": 0.09,
             "account": "crypto"}
        raw = {"id": gid, "currency": "USD", "net_amount": 0.0,
               "date": "2026-05-18"}
        base = {"id": gid, "net_amount": 0.6042, "currency": "CAD"}
        filled = {"id": gid, "net_amount": 0.4410, "currency": "USD"}
        fx = {"USD": {"2026-05-18": Decimal("1.3700")}}
        kw = dict(check_index={}, fx_history=fx,
                  default_rate=Decimal("1.35"), base_currency="CAD",
                  tmap=None, replacement_lookup={}, checks_supplied=False)
        src = {gid: [{"label": "kraken", "row": raw}]}
        bad = A.build_event(g, {gid: base}, src, **kw)
        good = A.build_event(g, {gid: base}, src, filled_index={gid: filled},
                             **kw)
        self.assertFalse(bad["fx"]["ties"])
        self.assertTrue(good["fx"]["ties"], good["fx"])
        self.assertTrue(good["fx"]["priced_by_fill"])


class TestWashExplainBlended(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestWashExplainBlended"""
    def test_explain_traces_the_blended_pool(self):
        """S046-09: margin 100@20 + margin2 100@10, margin2 sells @12
        and rebuys — the blended ACB is 15, so a 300 loss is denied."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _toml(root, 2025, "source_currencies = []\n"
                              "option_grant_timing_since = 2025\n",
                  [("margin", "taxable", ""), ("margin2", "taxable", "")])
            for a, tt in (("margin", "BUYSELL 2025-02-03 10:00:00 QRS.TO "
                                     "100 CAD 20.00 -2000.00 0.00\n"),
                          ("margin2", "BUYSELL 2025-02-03 10:00:00 QRS.TO "
                                      "100 CAD 10.00 -1000.00 0.00\n"
                                      "BUYSELL 2025-09-02 10:00:00 QRS.TO "
                                      "-100 CAD 12.00 1200.00 0.00\n"
                                      "BUYSELL 2025-09-08 10:00:00 QRS.TO "
                                      "100 CAD 12.00 -1200.00 0.00\n")):
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / "x.tt").write_text(tt)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "wash-sales", "--explain")
        # The report layout (docs/output-style.md).
        self.assertIn("denied 300.00)", r.stdout)
        self.assertNotIn("no matching gains", r.stdout + r.stderr)
