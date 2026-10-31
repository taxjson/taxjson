"""`tjs winners` / `tjs losers` — per-ticker realized gains,
one side each (one implementation, _ranked_view)."""

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
    def test_winners_only_with_option_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "winners")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertTrue(out.startswith("WINNERS — tax year 2026"))
        # WIN.TO first (5,250 incl. its option), then MID.TO; no loser.
        self.assertLess(out.index("WIN.TO"), out.index("MID.TO"))
        self.assertNotIn("LOSE.US", out.split("WINNERS TOTAL")[0])
        self.assertNotIn("-3,000.00", out)
        self.assertRegex(out, r"WIN\.TO\s+2\s+.*5,250\.00")
        self.assertRegex(out, r"WINNERS TOTAL:\s+5,350\.00 CAD \(all 2 shown\)")
        self.assertRegex(out, r"TOTAL REALIZED GAIN:\s+2,350\.00 CAD")
        self.assertIn("tjs losers", out)
        self.assertNotIn("99,999", out)             # dividends excluded
        self.assertNotIn("OLD.TO", out)             # window (tax year)

    def test_losers_only_worst_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "losers")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertTrue(out.startswith("LOSERS — tax year 2026"))
        self.assertIn("LOSE.US", out)
        self.assertNotIn("WIN.TO", out)
        self.assertNotIn("MID.TO", out)
        self.assertRegex(out, r"LOSERS TOTAL:\s+-3,000\.00 CAD \(1 shown\)")
        self.assertIn("tjs winners", out)

    def test_losers_ranked_worst_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            doc = json.loads((root / "work" / "margin_gains.json")
                             .read_text())
            doc["transactions"].append({
                "symbol": "BAD.TO", "date": "2026-04-01", "qty": 1,
                "proceeds": 1.0, "cost": 9001.0, "gain": -9000.0,
                "currency": "CAD", "days_held": 3})
            (root / "work" / "margin_gains.json").write_text(
                json.dumps(doc))
            r = _run(root, "losers", "--top", "1")
        self.assertIn("BAD.TO", r.stdout)
        self.assertNotIn("LOSE.US", r.stdout)
        self.assertIn("(1 of 2 shown)", r.stdout)

    def test_top_cuts_the_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "winners", "--top", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WIN.TO", r.stdout)
        self.assertNotIn("MID.TO", r.stdout.split("WINNERS TOTAL")[0])
        self.assertNotIn("LOSE.US", r.stdout)
        self.assertIn("(1 of 2 shown)", r.stdout)

    def test_json_all_rows_ranked_unchanged(self):
        """winners --json still carries every ticker, best first, with
        the overall totals (backward compatible); the side's own totals
        are new keys."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            doc = json.loads(_run(root, "winners", "--json").stdout)
            los = json.loads(_run(root, "losers", "--json").stdout)
        self.assertEqual([x["ticker"] for x in doc["rows"]],
                         ["WIN.TO", "MID.TO", "LOSE.US"])
        self.assertEqual(doc["rows"][0]["gain"], 5250.0)
        self.assertEqual(doc["total_gain"], 2350.0)
        self.assertEqual(doc["side"], "winners")
        self.assertEqual(doc["side_tickers"], ["WIN.TO", "MID.TO"])
        self.assertEqual(doc["side_total_gain"], 5350.0)
        self.assertEqual([x["ticker"] for x in los["rows"]],
                         ["LOSE.US", "MID.TO", "WIN.TO"])
        self.assertEqual(los["side_tickers"], ["LOSE.US"])
        self.assertEqual(los["side_total_gain"], -3000.0)
        self.assertEqual(los["side_total_gain_taxable"], -3000.0)
        self.assertEqual(los["total_gain"], 2350.0)

    def test_empty_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            for cmd in ("winners", "losers"):
                r = _run(root, cmd, "2027")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("No realized dispositions", r.stdout)

    def test_empty_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            doc = json.loads((root / "work" / "margin_gains.json")
                             .read_text())
            doc["transactions"] = [t for t in doc["transactions"]
                                   if t.get("symbol") != "LOSE.US"]
            (root / "work" / "margin_gains.json").write_text(
                json.dumps(doc))
            r = _run(root, "losers")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No realized losers in tax year 2026", r.stdout)

    def test_sheltered_split_scoped_to_the_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            cfg = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                cfg + '[accounts.rrsp]\ntype = "sheltered"\n')
            (root / "work" / "rrsp_gains.json").write_text(json.dumps({
                "summary": {"year": "2026"}, "transactions": [
                    {"symbol": "SHL.TO", "date": "2026-03-01", "qty": 1,
                     "proceeds": 100.0, "cost": 500.0, "gain": -400.0,
                     "currency": "CAD", "days_held": 3}]}))
            w = _run(root, "winners").stdout
            lo = _run(root, "losers").stdout
        self.assertNotIn("SHELTERED", w)          # no sheltered winner
        self.assertIn("TAXABLE: -3,000.00 CAD", lo)
        self.assertIn("SHELTERED (rrsp", lo)
        self.assertIn("-400.00", lo)

    def test_budget(self):
        from _style import assert_concise
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            for cmd in ("winners", "losers"):
                r = _run(root, cmd)
                assert_concise(self, r.stdout, r.stderr)


if __name__ == "__main__":
    unittest.main()
