"""`taxjson ccd-sum` — covered-call (short call) gains per underlying.

Pins: SHORT-call-only selection (long calls and puts excluded; the
direction fallback for older files without `direction`), per-underlying
aggregation, window filter, wash-preferred basis, --json, empty scope.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SHORT_CALL = "BNS260116C00082000.TO"       # sold 2 for 300, bought back 100
LONG_CALL = "AAPL260116C00150000.US"
SHORT_PUT = "BNS260116P00060000.TO"


def _runsub(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


def _project(tmp):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_gains.json").write_text(json.dumps({
        "summary": {"year": "2026"}, "transactions": [
            # SHORT call closed at a profit: premium 300, buyback 100.
            # Explicit-direction rows use the engines' signed cash-flow
            # convention (FUZZ #E): cost = -premium, proceeds = -buyback,
            # gain == proceeds - cost.
            {"symbol": SHORT_CALL, "date": "2026-03-01", "qty": -2,
             "proceeds": -100.0, "cost": -300.0, "gain": 200.0,
             "direction": "SHORT", "currency": "CAD", "days_held": 30},
            # Second close, same underlying, via the INFERENCE path
            # (no direction field; older files store shorts with the
            # legs swapped: gain == cost - proceeds).
            {"symbol": SHORT_CALL, "date": "2026-04-01", "qty": -1,
             "proceeds": 50.0, "cost": 150.0, "gain": 100.0,
             "currency": "CAD", "days_held": 10},
            # LONG call (direction fallback: gain == proceeds - cost).
            {"symbol": LONG_CALL, "date": "2026-03-05", "qty": 1,
             "proceeds": 500.0, "cost": 400.0, "gain": 100.0,
             "currency": "CAD", "days_held": 20},
            # Short PUT — a call-only report.
            {"symbol": SHORT_PUT, "date": "2026-03-06", "qty": -1,
             "proceeds": -20.0, "cost": -120.0, "gain": 100.0,
             "direction": "SHORT", "currency": "CAD", "days_held": 5},
            # Stock row: never counted.
            {"symbol": "BNS.TO", "date": "2026-03-07", "qty": 100,
             "proceeds": 8000.0, "cost": 7000.0, "gain": 1000.0,
             "currency": "CAD", "days_held": 60},
            # Outside a 30d-style window but inside the tax year.
            # Expiry: premium 80 kept, no buyback.
            {"symbol": SHORT_CALL, "date": "2026-01-05", "qty": -1,
             "proceeds": 0.0, "cost": -80.0, "gain": 80.0,
             "direction": "SHORT", "currency": "CAD", "days_held": 40},
        ]}))
    return root


class TestCcdSum(unittest.TestCase):
    def test_short_calls_only_aggregated_by_underlying(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(_project(tmp), "ccd-sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("COVERED-CALL GAINS", out)
        # 3 closes of the BNS short call: 200 + 100 + 80 = 380.
        self.assertRegex(out, r"BNS\.TO\s+3\s+4\s+530\.00\s+150\.00\s+380\.00")
        self.assertIn("TOTAL COVERED-CALL GAIN: 380.00 CAD", out)
        # Long call, short put, stock rows all excluded.
        self.assertNotIn("AAPL", out)
        self.assertNotIn("P00060000", out)
        self.assertNotIn("1,000.00", out)

    def test_window_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            # Year window (default) sees 380; a 2026-03+04-only slice
            # via explicit year token still sees all three; use a
            # month-scoped period by checking the January row drops
            # under "3m" only if today is past April — instead pin the
            # deterministic year filter: a bogus later year is empty.
            r = _runsub(root, "ccd-sum", "2027")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No covered-call", r.stdout)

    def test_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(_project(tmp), "ccd-sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["total_gain"], 380.0)
        row = doc["rows"][0]
        self.assertEqual(row["underlying"], "BNS.TO")
        self.assertEqual(row["contracts"], 3)
        self.assertEqual(row["gain"], 380.0)
        self.assertEqual(doc["currency"], "CAD")

    def test_prefers_wash_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "work" / "margin_gains_wash.json").write_text(
                json.dumps({"summary": {"year": "2026"},
                            "transactions": [
                    {"symbol": SHORT_CALL, "date": "2026-03-01",
                     "qty": -1, "proceeds": 0.0, "cost": -99.0,
                     "gain": 99.0, "direction": "SHORT",
                     "currency": "CAD", "days_held": 1}]}))
            r = _runsub(root, "ccd-sum", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual(doc["total_gain"], 99.0)      # wash file wins

    def test_missing_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(_project(tmp), "ccd-sum", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gains for account", r.stderr)


if __name__ == "__main__":
    unittest.main()
