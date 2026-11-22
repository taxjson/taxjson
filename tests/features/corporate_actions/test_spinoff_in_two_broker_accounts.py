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

REPO = _paths.REPO
SRC = REPO / "src"


def _run(root, *args, env=None):
    e = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=300)


# ------------------------------------------------------------- A2-1535
class TestSpinoffInTwoBrokerAccounts(unittest.TestCase):
    """S029-11 twins: a spin-off held in two IB broker accounts of one
    taxjson account is ONE event (combine_broker_copies) at the elect
    re-extract (_reextract_pending_entry, an `elect --set` typed before
    any run) and in corp_views._current_events (`spinoffs`): FMV 900 +
    450, not one copy's 900 or 450.

    Formerly: test_fix_a2_pins3_g6b.TestSpinoffInTwoBrokerAccounts"""

    @rule(R.CA_CORP_07)
    def test_elect_before_run_and_spinoffs_see_the_combined_fmv(self):
        from _support.ib_corp_projects import (
            _IB_CA, _IB_HEAD, _IB_TRADES, _ib_buy, _ib_ca, _project,
        )
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        spin = ("PARNT(CA0000000777) Spinoff  1 for 5 (SPNCO, SPINCO CORP, "
                "CA0000000778)")

        def stmt(acct, q):
            return (_IB_HEAD.format(acct=acct) + _IB_TRADES
                    + _ib_buy("PARNT", q * 5, 60) + _IB_CA
                    + _ib_ca(spin, q, q * 45))
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {
                "ib_u1.csv": stmt("U5550001", 20),   # pii-ok (synthetic)
                "ib_u2.csv": stmt("U5550002", 10)})  # pii-ok (synthetic)
            eid = parse_ib_corporate_actions(
                root / "inputs" / "margin" / "ib_u1.csv",
                "margin")[0].event_id
            e = _run(root, "elect", "margin", "--set",
                     f"{eid}=taxable_deemed_dividend")
            self.assertEqual(e.returncode, 0, e.stderr[-2000:])
            man = json.loads((root / "inputs" / "margin" /
                              "manifest.json").read_text())
            self.assertIn("FMV 1350.00 CAD",
                          man["elections"][eid]["summary"])
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s = _run(root, "spinoffs", "--json")
            self.assertEqual(s.returncode, 0, s.stderr[-2000:])
        d = json.loads(s.stdout)
        rows = d.get("spinoffs", d if isinstance(d, list) else [])
        self.assertEqual([x["broker_fmv"] for x in rows], [1350.0])


if __name__ == "__main__":
    unittest.main()
