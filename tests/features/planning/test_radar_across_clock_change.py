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


class TestWashRadarDst(unittest.TestCase):
    """Formerly: test_audit_tier1_fixes.TestWashRadarDst"""
    def test_day30_across_fallback_still_in_window(self):
        # Loss 2026-10-04; 2026-11-03 is day 30 and the window spans the
        # Nov 1 DST fall-back. Pre-fix the local-epoch math aged it out a day
        # early and the row vanished. A coin settles on its trade date, so
        # a buy on day 30 is still inside the window.
        txs = [_tx("2026-09-01", "DSTC", 100, 1000.0),
               _tx("2026-10-04", "DSTC", -100, 500.0)]       # loss, fully out
        out = _radar(txs, "2026-11-03")
        line = next((l for l in radar_rows(out) if l.startswith("DSTC")), "")
        self.assertTrue(line, "day-30 loss must still be reported:\n" + out)
        self.assertIn("COOLING", line)

    def test_share_loss_across_fallback_until_reentry(self):
        # The same loss on a T+1 listing: a buy on day 29 (Mon 11-02)
        # settles on day 30, inside the window; one on day 30 settles
        # after it, so the loss stops blocking that day (backlog TJ-0186,
        # CA-PLAN-05).
        txs = [_tx("2026-09-01", "DST.TO", 100, 1000.0),
               _tx("2026-10-04", "DST.TO", -100, 500.0)]     # loss, fully out
        out = _radar(txs, "2026-11-02")
        line = next((l for l in radar_rows(out) if l.startswith("DST.TO")), "")
        self.assertTrue(line, "day-29 loss must still be reported:\n" + out)
        self.assertIn("COOLING", line)
        out = _radar(txs, "2026-11-03")
        line = next((l for l in radar_rows(out) if l.startswith("DST.TO")), "")
        self.assertNotIn("COOLING", line)

    def test_locked_buy_day30_across_fallback_not_clear(self):
        # Buy 2026-10-04 still held when a loss sale settles on day 30
        # (2026-11-03, the Nov 1 fall-back between): a partial loss sale
        # is still superficial — EXITABLE, not CLEAR. A sale is measured
        # from its own settlement (T+1, backlog TJ-0243): Monday 11-02's
        # settles on day 30; Tuesday 11-03's on day 31, clear.
        txs = [_tx("2026-10-04", "LCK.TO", 100, 1000.0)]
        out = _radar(txs, "2026-11-02")
        line = next(l for l in radar_rows(out) if l.startswith("LCK.TO"))
        self.assertIn("EXITABLE", line)
        self.assertNotIn("CLEAR:", line)
        out = _radar(txs, "2026-11-03")
        line = next(l for l in radar_rows(out) if l.startswith("LCK.TO"))
        self.assertIn("CLEAR:", line)


if __name__ == "__main__":
    unittest.main()
