"""Re-audit-2 fixes, filing-reports list (helper D): the run pipeline
(crypto-sends stage, raw pass phantoms, blended .sum diagnostics, run
state banner), `sum`/`estimate` account handling and flag guards,
instalment dates, and the estimate's Canadian-issuer test and wording.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_d
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths
from _support.filing_run_record import record_full_run

REPO_ROOT = _paths.REPO


def _taxjson(root, *a, env=None):
    e = {**os.environ, "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
         "PYTHONPATH": str(REPO_ROOT / "src")}
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=e)


def _estimate_project(tmp, rows, base_rows=None, *, country="canada"):
    """A project with a hand-written gains file (and base book) for one
    taxable account; `tjs estimate --json` reads them."""
    root = Path(tmp)
    (root / "work").mkdir()
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n'
        + ('province = "ON"\n' if country == "canada" else "")
        + '[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_gains.json").write_text(json.dumps(
        {"summary": {"year": 2025}, "transactions": rows,
         "inventory": [], "wash_sales": []}))
    if base_rows is not None:
        (root / "work" / "margin_base.json").write_text(json.dumps(
            {"transactions": base_rows}))
    record_full_run(root)       # (the estimate reads books a full run recorded)
    return root


class TestUsEstimateSection1256(unittest.TestCase):
    """A2-1124: the US estimate says when §1256 P/L (futures, index
    options) is in its short-term figure — taxed as short-term, the
    60/40 split not modelled — and its Assumes line says so.

    Formerly: test_fix_a2_filrep_d.TestUsEstimateSection1256"""

    def _rows(self, sym, gain):
        return {"date": "2025-03-03", "date_settle": "2025-03-04",
                "symbol": sym, "qty": -1, "currency": "USD",
                "proceeds": 1000.0 + gain, "cost": 1000.0, "gain": gain,
                "days_held": 10, "term": "SHORT_TERM", "account": "margin",
                "action": "BUYSELL"}

    @rule(R.US_FUT_02)
    def test_futures_pl_named_in_us_estimate(self):
        rows = [self._rows("F:ESH5", 15000.0),
                self._rows("AAA.US", 500.0)]
        with tempfile.TemporaryDirectory() as td:
            root = _estimate_project(td, rows, country="usa")
            j = _taxjson(root, "estimate", "--other-income", "100000",
                         "--json")
            self.assertEqual(j.returncode, 0, j.stderr[-2000:])
            est = json.loads(j.stdout)["estimate"]
            self.assertAlmostEqual(est["section_1256_gain"], 15000.0,
                                   places=2)
            self.assertTrue(any("60/40" in n for n in est["notes"]),
                            est["notes"])
            # The estimate's notes: --details (Essentials first).
            t = _taxjson(root, "estimate", "--other-income", "100000",
                         "--details")
            self.assertEqual(t.returncode, 0, t.stderr[-2000:])
            self.assertIn("§1256", t.stdout)
            self.assertIn("60/40", t.stdout)

    @rule(R.US_FUT_02)
    def test_no_section_1256_no_note(self):
        with tempfile.TemporaryDirectory() as td:
            root = _estimate_project(td, [self._rows("AAA.US", 500.0)],
                                     country="usa")
            j = _taxjson(root, "estimate", "--other-income", "100000",
                         "--json")
            self.assertEqual(j.returncode, 0, j.stderr[-2000:])
            est = json.loads(j.stdout)["estimate"]
            self.assertEqual(est.get("section_1256_gain", 0.0), 0.0)
            self.assertFalse(any("60/40" in n
                                 for n in est.get("notes") or []))


if __name__ == "__main__":
    unittest.main()
