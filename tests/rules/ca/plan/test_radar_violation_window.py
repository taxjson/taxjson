"""Regression tests for the 2026-07 audit tier-1 fixes:

1. run_to_file: a failed stage must not leave a truncated output file that the
   mtime cache then treats as fresh (previous good output must survive).
2. `tjs fees` counts standalone FEE-action rows (amount in net_amount).
3. IB dividend/withholding reversal rows keep their sign and net out instead
   of being abs()'d into extra income/tax.
4. Wash radar: 30-day windows are DST-immune (UTC-noon epochs) — day 30
   across the November fall-back is still inside the window.
5. Wash radar: the VIOLATION (substituted-property) test uses the CRA window
   around the LOSS SALE, not the last 30 days from today; fully-exited losses
   route to COOLING.
6. Engines apply one corporate SPLIT once even when merged multi-account
   input carries one SPLIT row per account.

Formerly: test_audit_tier1_fixes
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _radar_text import radar_rows
import _paths

REPO_ROOT = _paths.REPO


def _radar(taxable_txs, date, extra_env=None):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "t.json"
        f.write_text(json.dumps({"transactions": taxable_txs}))
        env = dict(os.environ)
        env["TZ"] = "America/Toronto"      # DST-observing zone
        if extra_env:
            env.update(extra_env)
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar", "--country", "canada",
             "--taxable", str(f), "--date", date],
            cwd=REPO_ROOT, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout


def _tx(date, sym, qty, net, account="margin"):
    return {"action": "BUYSELL", "date": date, "time": "09:30:00",
            "symbol": sym, "quantity": qty, "price": abs(net / qty) if qty else 0,
            "net_amount": abs(net), "currency": "CAD", "account": account}


class TestWashRadarViolationWindow(unittest.TestCase):
    """Formerly: test_audit_tier1_fixes.TestWashRadarViolationWindow"""
    def test_violation_persists_as_today_advances(self):
        # Substitute lot bought 4 days BEFORE the loss sale and still held:
        # inside [sale−30, sale+30] forever, so the advisory must stay
        # VIOLATION (sell-by deadline) even once the buy is >30 days before
        # "today" — pre-fix it silently downgraded to BLOCKED.
        txs = [_tx("2024-01-05", "VIO.TO", 100, 10000.0),
               _tx("2026-06-01", "VIO.TO", 10, 1000.0),      # trigger buy
               _tx("2026-06-05", "VIO.TO", -50, 2000.0)]     # loss sale
        out_early = _radar(txs, "2026-06-15")
        out_late = _radar(txs, "2026-07-03")                  # buy >30d ago
        for out in (out_early, out_late):
            line = next(l for l in radar_rows(out) if l.startswith("VIO.TO"))
            self.assertIn("VIOLATION", line, out)

    def test_no_trigger_held_position_is_blocked(self):
        # Loss with shares still held but NO acquisition in the CRA window:
        # loss is allowed so far — BLOCKED (don't re-buy), not VIOLATION.
        txs = [_tx("2024-01-05", "BLK.TO", 100, 10000.0),
               _tx("2026-06-05", "BLK.TO", -50, 2000.0)]      # loss, 50 left
        out = _radar(txs, "2026-06-15")
        line = next(l for l in radar_rows(out) if l.startswith("BLK.TO"))
        self.assertIn("BLOCKED", line)

    def test_fully_exited_loss_is_cooling(self):
        # Sold out completely at a loss, nothing held → COOLING (the category
        # was previously unreachable dead code).
        txs = [_tx("2026-05-01", "COO.TO", 100, 2000.0),
               _tx("2026-06-05", "COO.TO", -100, 1000.0)]
        out = _radar(txs, "2026-06-15")
        line = next(l for l in radar_rows(out) if l.startswith("COO.TO"))
        self.assertIn("COOLING", line)


if __name__ == "__main__":
    unittest.main()
