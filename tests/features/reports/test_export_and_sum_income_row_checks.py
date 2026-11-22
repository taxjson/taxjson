"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, export,
some parsers and taxjson-sum-income.

Formerly: test_fix_a2_errA_d"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO = _paths.REPO
ENV = _hermetic.environ(TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
                        NO_COLOR="1", TAXJSON_WIDTH="0")    # captured output


def tool(module, *args, cwd=None, env=None):
    return subprocess.run([sys.executable, "-c",
                           "import sys; from taxjson.bin._entry import "
                           f"{module} as m; sys.exit(m())", *args],
                          capture_output=True, text=True, cwd=cwd,
                          env=env or ENV, stdin=subprocess.DEVNULL)


def no_tb(tc, r):
    tc.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
                else:
                    p.chmod(0o644)
            except OSError:
                pass
        self._td.cleanup()


# --------------------------------------------------------------- export
def export(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_export",
                           *args], capture_output=True, text=True, cwd=cwd,
                          env=ENV, stdin=subprocess.DEVNULL)


class TestExport(_Tmp):
    """A2-0806, A2-1410, A2-1442, A2-1441. (A2-1443, the tv-map BOM, went
    with the TradingView export.)

    Formerly: test_fix_a2_errA_d.TestExport"""

    def test_gains_rows_wrong_types_refused(self):
        """A2-0793 (export part)."""
        g = self.root / "margin_gains_wash.json"
        for inv in ({"symbol": "x", "qty": "x", "total_cost": "x",
                     "currency": "x"},
                    {"symbol": 5, "qty": 1, "total_cost": 1}):
            g.write_text(json.dumps({"transactions": [], "inventory": [inv]}))
            for mode in ("--report", "--holdings-toml"):
                r = export(mode, str(g))
                no_tb(self, r)
                self.assertEqual(r.returncode, 2, (mode, r.stderr))
                self.assertIn("margin_gains_wash.json", r.stderr)

    def test_holdings_toml_bad_quantity_refused(self):
        h = self.root / "h.toml"
        for bad in ('quantity = "abc"\ntotal_cost = 10.0',
                    'quantity = 5\ntotal_cost = "x"'):
            h.write_text('[meta]\nschema_version = 1\n[[holding]]\n'
                         'symbol = "XEI.TO"\n' + bad +
                         '\ncurrency = "CAD"\n')
            for mode in ("--report", "--holdings-toml"):
                r = export(mode, str(h))
                no_tb(self, r)
                self.assertEqual(r.returncode, 2, (mode, r.stderr))
                self.assertIn("[[holding]] 1 (XEI.TO)", r.stderr)


# ----------------------------------------------------------- sum-income
class TestSumIncomeRows(_Tmp):
    """A2-1448: rows go through the gains loader's checks.

    Formerly: test_fix_a2_errA_d.TestSumIncomeRows"""

    def test_bad_rows_one_line_exit_2(self):
        good = {"action": "DIVIDEND", "date": "2024-02-15", "time":
                "09:30:00", "symbol": "QZRY.TO", "quantity": 0.0,
                "currency": "CAD", "net_amount": 24.69, "type": "dividend",
                "account": "margin", "id": "d1"}
        for field, val in (("date", "2024-02-30"),
                           ("net_amount", float("nan")),
                           ("net_amount", float("inf")),
                           ("net_amount", "abc")):
            row = dict(good, **{field: val})
            f = self.root / "b.json"
            f.write_text(json.dumps({"transactions": [row]}))
            r = tool("taxjson_sum_income", "--year", "2024",
                     "--country", "canada", str(f))
            no_tb(self, r)
            self.assertEqual(r.returncode, 2, (field, val, r.stderr,
                                               r.stdout))
            self.assertIn("b.json", r.stderr)
        f.write_text(json.dumps({"transactions": [good]}))
        r = tool("taxjson_sum_income", "--year", "2024", "--country",
                 "canada", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("24.69", r.stdout)


if __name__ == "__main__":
    unittest.main()
