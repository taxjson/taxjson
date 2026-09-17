"""taxjson_safe_to_sell — the taxable-only 30-day lock audit.

2026-09 audit: the walk skipped every action but BUYSELL, so an ASSIGN
(option assignment/exercise — an acquisition to the engine and the
radar alike) neither opened a lock nor added to the taxable inventory.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(action, date, symbol, qty, net):
    return {"action": action, "date": date, "time": "09:30:00",
            "symbol": symbol, "quantity": qty, "net_amount": net,
            "currency": "CAD", "account": "margin"}


def _run(taxable, date):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.json"
        t.write_text(json.dumps({"transactions": taxable}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_safe_to_sell",
             "--taxable", str(t), "--date", date],
            cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


class TestAssignIsAnAcquisition(unittest.TestCase):
    def test_assigned_put_opens_the_lock(self):
        rows = [_tx("BUYSELL", "2026-01-05", "ASG.TO", 100, 5000.0),
                _tx("ASSIGN", "2026-09-01", "ASG.TO", 100, 4000.0)]
        out = _run(rows, "2026-09-10")
        line = next(ln for ln in out.splitlines() if ln.startswith("ASG.TO"))
        self.assertIn("200.0000", line)          # inventory counts it
        self.assertIn("LOCKED", line)
        self.assertIn("Last Buy 2026-09-01", line)

    def test_plain_old_position_stays_safe(self):
        rows = [_tx("BUYSELL", "2026-01-05", "ASG.TO", 100, 5000.0)]
        out = _run(rows, "2026-09-10")
        line = next(ln for ln in out.splitlines() if ln.startswith("ASG.TO"))
        self.assertIn("SAFE", line)


if __name__ == "__main__":
    unittest.main()
