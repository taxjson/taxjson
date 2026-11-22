"""Re-audit-2 tests-tagging round (filing / estimate area): the estimate
callers' routing of each income kind, pinned through the code that
builds the estimator's inputs (every figure synthetic).

Formerly: test_fix_a2_tagging_b"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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



class TestSafeToSellByCountry(unittest.TestCase):
    """A2-0852: safe-to-sell (a wash-radar view) on a registered / IRA
    buy that was sold again before today: Canada's still-held test frees
    it (CA-PLAN-01), the US denies the loss for good (US-PLAN-01,
    US-WASH-11).

    Formerly: test_fix_a2_tagging_b.TestSafeToSellByCountry"""

    def _run(self, country):
        def row(d, q, acct):
            return {"action": "BUYSELL", "date": d, "date_settle": d,
                    "time": "10:00:00", "symbol": "ABC.US", "quantity": q,
                    "price": 20.0, "net_amount": abs(q) * 20.0,
                    "currency": "USD", "account": acct}
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "margin_base.json"
            t.write_text(json.dumps({"transactions": [
                row("2026-01-05", 100, "margin")]}))
            sp = Path(tmp) / "sheltered_base.json"
            sp.write_text(json.dumps({"transactions": [
                row("2026-09-18", 50, "plan"),
                row("2026-09-22", -50, "plan")]}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_safe_to_sell",
                 "--country", country, "--taxable", str(t),
                 "--sheltered", str(sp), "--date", "2026-09-29"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    @rule(R.CA_PLAN_01, R.CA_PLAN_04)
    @rule(R.US_PLAN_01, R.US_PLAN_04, R.US_WASH_11)
    def test_sold_registered_buy(self):
        ca, us = self._run("canada"), self._run("usa")
        row = next(ln for ln in ca.splitlines() if "ABC.US" in ln)
        self.assertIn("SAFE*", row)
        self.assertIn("holds none of it now", ca)
        self.assertIn("s.251.1", ca)
        self.assertIn("permanently denied", us)
        self.assertIn("even if the IRA has sold", us)
        self.assertIn("IRS Pub. 550", us)
        self.assertNotIn("superficial", us)


if __name__ == "__main__":
    unittest.main()
