"""Re-audit-2 tests-pins (pins3, group g6b): fixes in taxjson_run.py's
reports, guards, wording gates and settings that held but that no test
failed on when reverted. Each TestCase names the A2 finding and the code
it pins; every test was checked to fail with that code reverted.

All data is synthetic (invented tickers, round amounts).

Formerly: test_fix_a2_pins3_g6b
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
import _paths
from _support.filing_run_record import record_full_run


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO = _paths.REPO
SRC = REPO / "src"


def _run(root, *args, env=None):
    e = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=300)


def _doc(path, txs, **extra):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"transactions": txs}
    d.update(extra)
    path.write_text(json.dumps(d))
    return path


def _gain(symbol, cur, *, gain=-1.0, disallowed=0.0, term=None, **kw):
    e = {"symbol": symbol, "qty": -100.0, "proceeds": 999.0,
         "cost": 999.0 - gain + disallowed, "gain": gain,
         "direction": "LONG", "date": "2025-05-02",
         "date_settle": "2025-05-02", "commission": 0.0, "fee": 0.0,
         "disallowed_amount": disallowed, "permanently_disallowed": 0.0,
         "days_held": 30, "currency": cur, "account": "margin"}
    if term:
        e["term"] = term
    e.update(kw)
    return e


# ----------------------------------------------- A2-0934, A2-1586, A2-1587
def _t(action, date, sym, qty=0.0, net=0.0, cur="CAD", **kw):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": sym, "quantity": qty,
         "price": abs(net / qty) if qty else 0.0, "net_amount": net,
         "currency": cur}
    d.update(kw)
    return d


# ------------------------------------------------------------- A2-1610
class TestOtherYearBanner(unittest.TestCase):
    """S067-12 twins: t1135 and form-export say when the work/ books were
    built for another tax year (_warn_artifact_year), as sum does.

    Formerly: test_fix_a2_pins3_g6b.TestOtherYearBanner"""

    def test_t1135_and_form_export_banner(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            _doc(root / "work" / "margin_base.json", [
                _t("BUYSELL", "2025-02-03", "AAA.US", 10, -1000.0),
                _t("BUYSELL", "2025-05-03", "AAA.US", -10, 1200.0)])
            _doc(root / "work" / "margin_gains.json",
                 [_gain("AAA.US", "CAD", gain=200.0)],
                 summary={"year": "2025", "tax_date_basis": "settle"})
            record_full_run(root)  # books a full run built (TJ-4309)
            for cmd in ("t1135", "form-export"):
                with self.subTest(cmd=cmd):
                    r = _run(root, cmd)
                    self.assertIn("built for another tax year: margin "
                                  "(2025)", r.stderr,
                                  (r.stdout + r.stderr)[-1500:])


if __name__ == "__main__":
    unittest.main()
