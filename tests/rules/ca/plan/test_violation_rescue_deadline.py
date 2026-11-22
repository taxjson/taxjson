"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _radar_text import radar_rows

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO_ROOT = _paths.REPO


class TestRadarViolationRescueDeadline(unittest.TestCase):
    """Deep-audit 2026-08 #8: the VIOLATION advisory printed a sell-by
    date of loss+31, one day past the engine's held-at-end boundary
    (loss+30). Following the advisory to the letter left the loss
    disallowed. This test closes the loop: the date the radar prints
    must actually rescue the loss when followed.

    Formerly: test_audit_2026_08_fixes.TestRadarViolationRescueDeadline"""

    BASE = [
        {"action": "BUYSELL", "date": "2025-01-15", "time": "09:30:00",
         "symbol": "XEI.TO", "quantity": 100.0, "net_amount": 1000.0,
         "currency": "CAD", "account": "m"},
        {"action": "BUYSELL", "date": "2025-02-02", "time": "09:30:00",
         "symbol": "XEI.TO", "quantity": -100.0, "net_amount": 800.0,
         "currency": "CAD", "account": "m"},
        {"action": "BUYSELL", "date": "2025-02-10", "time": "09:30:00",
         "symbol": "XEI.TO", "quantity": 100.0, "net_amount": 810.0,
         "currency": "CAD", "account": "m"},
    ]

    def _radar_violation_date(self):
        import json as _json
        import re as _re
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.json"
            t.write_text(_json.dumps({"transactions": self.BASE}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar", "--country", "canada",
                 "--taxable", str(t), "--date", "2025-02-20"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = _re.search(r"VIOLATION: Sell [\d.]+ shares \([^)]*\) by "
                       r"(\d{4}-\d{2}-\d{2})",
                       "\n".join(radar_rows(r.stdout)))
        self.assertIsNotNone(m, f"no VIOLATION advisory in:\n{r.stdout}")
        return m.group(1)

    def test_following_the_advisory_actually_rescues_the_loss(self):
        safe_date = self._radar_violation_date()
        # loss 2025-02-02 + 30 days = 2025-03-04 is the engine's SETTLE
        # boundary; the printed "sell by" is the last TRADE date that
        # settles inside it (T+1 → Mon 2025-03-03). Printing the settle
        # bound itself was the 2026-09 audit's one-lag-late defect.
        self.assertEqual(safe_date, "2025-03-03")
        # Follow the advisory: full exit TRADED on safe_date, settling
        # T+1 like a real broker row.
        from taxjson.lib.dates import settlement_date
        txs = [TaxTransaction(**t) for t in self.BASE]
        txs.append(TaxTransaction(
            action="BUYSELL", date=safe_date, time="09:30:00",
            date_settle=settlement_date(safe_date, "CAD"),
            symbol="XEI.TO", quantity=-100.0, net_amount=820.0,
            currency="CAD", account="m"))
        result = CanadaTaxRules().compute_gains(txs)
        self.assertAlmostEqual(
            result["summary"].get("total_disallowed", 0.0), 0.0,
            places=2,
            msg="selling on the radar's own printed deadline must "
                "rescue the loss — the +31 date left it disallowed")


if __name__ == '__main__':
    unittest.main()
