"""`taxjson winners` — ranked per-ticker realized gains."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


def _project(tmp):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')

    def d(sym, gain, date="2026-03-01"):
        return {"symbol": sym, "date": date, "qty": 10,
                "proceeds": 1000.0 + gain, "cost": 1000.0, "gain": gain,
                "currency": "CAD", "days_held": 30}
    (root / "work" / "margin_gains.json").write_text(json.dumps({
        "summary": {"year": "2026"}, "transactions": [
            d("WIN.TO", 5000.0), d("MID.TO", 100.0),
            d("LOSE.US", -3000.0),
            # option under WIN.TO groups with it
            d("WIN.TO260116C00010000", 250.0),
            # dividend must not count
            {"action": "DIVIDEND", "symbol": "WIN.TO",
             "dividend": 99999.0, "currency": "CAD"},
            # outside a 2027 window
            d("OLD.TO", 42.0, date="2025-06-01")]}))
    return root


class TestWinners(unittest.TestCase):
    def test_ranked_with_option_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "winners")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("WINNERS & LOSERS", out)
        # WIN.TO first (5,250 incl. its option), LOSE.US last.
        self.assertLess(out.index("WIN.TO"), out.index("MID.TO"))
        self.assertLess(out.index("MID.TO"), out.index("LOSE.US"))
        self.assertRegex(out, r"WIN\.TO\s+2\s+.*5,250\.00")
        self.assertIn("-3,000.00", out)
        self.assertNotIn("99,999", out)             # dividends excluded
        self.assertNotIn("OLD.TO", out)             # window (tax year)

    def test_top_truncates_middle(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "winners", "--top", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WIN.TO", r.stdout)
        self.assertIn("LOSE.US", r.stdout)
        self.assertNotIn("MID.TO", r.stdout)        # hidden middle
        s = r.stdout
        self.assertRegex(s, r"\.\.\.\s+1\s")

    def test_json_all_rows_ranked(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "winners", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual([x["ticker"] for x in doc["rows"]],
                         ["WIN.TO", "MID.TO", "LOSE.US"])
        self.assertEqual(doc["rows"][0]["gain"], 5250.0)
        self.assertEqual(doc["total_gain"], 2350.0)

    def test_empty_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "winners", "2027")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No realized dispositions", r.stdout)


if __name__ == "__main__":
    unittest.main()
