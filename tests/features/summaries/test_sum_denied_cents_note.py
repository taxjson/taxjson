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

from tax_rules import rule
from tax_rules.dual import projects_both
from taxjson.rules import ids as R
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


# ------------------------------------------------------------- A2-0912
class TestSumDeniedRoundingNote(unittest.TestCase):
    """A2-0912 (R1-166 second half): `sum`'s FOR THE RETURN rounds each
    row to the cent; the gain gap was labelled but the DENIED column
    (US: the code-W adjustment) differed from the gains files' unrounded
    total with no note. Both countries' notes are pinned (the US one was
    asserted by no test: cli/estimate.py cmd_summary, `if abs(_round_gap)`
    in the Form 8949 branch).

    Formerly: test_fix_a2_pins3_g6b.TestSumDeniedRoundingNote"""

    def _proj(self, td, country):
        cur = "CAD" if country == "canada" else "USD"
        sfx = ".TO" if country == "canada" else ".US"
        term = None if country == "canada" else "SHORT_TERM"
        p = projects_both(td)[country]
        # Three rows, each with 1.004 denied: rounded rows 3.00, engine
        # 3.012 -> 3.01.
        _doc(p / "work" / "margin_gains.json",
             [_gain(f"S{i}{sfx}", cur, disallowed=1.004, term=term)
              for i in range(3)])
        record_full_run(p)          # (sum reads books a full run recorded: TJ-4347)
        return p

    @rule(R.CA_DISP_08)
    def test_canada_denied_gap_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td, "canada")
            r = _run(p, "sum", "--details")
            j = _run(p, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        # DENIED (INFO)'s figure, every kind (here the superficial losses
        # alone), against the gains files' (TJ-5450).
        self.assertIn("DENIED (INFO)'s unrounded total in the gains files (every loss "
                      "denied) is 3.01 (-0.01 on the RETURN row)", r.stdout)
        f = json.loads(j.stdout)["filing"]
        self.assertEqual(f["totals"]["denied"], 3.0)
        self.assertEqual(f["engine_denied_unrounded"], 3.01)

    @rule(R.US_RPT_09)
    def test_usa_adjustment_gap_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td, "usa")
            r = _run(p, "sum", "--details")
            j = _run(p, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FORM 8949", r.stdout)
        self.assertIn("unrounded total adjustment (g) is 3.01 (-0.01 on "
                      "the RETURN row)", r.stdout)
        f = json.loads(j.stdout)["filing"]
        self.assertEqual(f["totals"]["adjustment"], 3.0)
        self.assertEqual(f["engine_denied_unrounded"], 3.01)

    @rule(R.US_RPT_09)
    def test_usa_gain_gap_is_named(self):
        """The US branch's gain note (unpinned before)."""
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td)["usa"]
            _doc(p / "work" / "margin_gains.json",
                 [_gain(f"S{i}.US", "USD", gain=1.004, term="SHORT_TERM")
                  for i in range(3)])
            record_full_run(p)
            r = _run(p, "sum", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FORM 8949", r.stdout)
        self.assertIn("unrounded total gain is 3.01 (-0.01 on the RETURN "
                      "row)", r.stdout)


if __name__ == "__main__":
    unittest.main()
