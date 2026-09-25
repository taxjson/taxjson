"""`taxjson sum` FOR THE RETURN block: proceeds / ACB / outlays / gain per
taxable account on the Schedule 3 convention, with PROCEEDS − ACB −
OUTLAYS equal to the allowed gain (denied superficial losses in the ACB)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_form_export import build_schedule3, filing_totals

REPO_ROOT = Path(__file__).resolve().parent.parent

ENTRIES = [
    # long sale, net proceeds 995 after a 5 commission, gain 195
    {"symbol": "A.TO", "qty": 10, "proceeds": 995.0, "cost": 800.0, "gain": 195.0,
     "commission": 5.0, "direction": "LONG", "date_settle": "2025-03-04"},
    # long loss of 100, 60 of it denied as superficial -> allowed -40
    {"symbol": "B.TO", "qty": 5, "proceeds": 400.0, "cost": 500.0, "gain": -40.0,
     "disallowed_amount": 60.0, "direction": "LONG", "date_settle": "2025-05-06"},
    # short: sold short for 300, covered for 250 (engine-signed: cost -300, proceeds -250)
    {"symbol": "C.US", "qty": 1, "proceeds": -250.0, "cost": -300.0, "gain": 50.0,
     "direction": "SHORT", "date_settle": "2025-07-08"},
]


class TestFilingTotals(unittest.TestCase):
    def test_identity_and_schedule3_agreement(self):
        t = filing_totals(ENTRIES)
        rep = build_schedule3(ENTRIES)
        self.assertEqual(t["proceeds"], rep["totals"]["proceeds_13199"])   # 1000 + 400 + 300
        self.assertEqual(t["gain"], rep["totals"]["gain_13200"])
        self.assertEqual((t["proceeds"], t["outlays"], t["gain"], t["denied"]), (1700.0, 5.0, 205.0, 60.0))
        self.assertAlmostEqual(t["proceeds"] - t["acb"] - t["outlays"], t["gain"], places=2)
        self.assertEqual(t["acb"], 1490.0)          # 800 + 500 - 60 denied + 250 cover


class TestSumCommand(unittest.TestCase):
    def test_block_and_json_for_taxable_accounts_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n[accounts.rrsp]\ntype = "sheltered"\n')
            (root / "work").mkdir()
            doc = {"summary": {"year": 2025}, "transactions": ENTRIES + [
                {"symbol": "Z.TO", "qty": 1, "proceeds": 9.0, "cost": 1.0, "gain": 8.0,
                 "direction": "LONG", "date_settle": "2024-12-30"}]}          # other year: excluded
            (root / "work" / "margin_gains.json").write_text(json.dumps(doc))
            (root / "work" / "rrsp_gains.json").write_text(json.dumps(doc))
            def cli(*a):
                return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root), "sum", *a],
                                      cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            r = cli("--json"); self.assertEqual(r.returncode, 0, r.stderr)
            f = json.loads(r.stdout)["filing"]
            self.assertEqual([a["account"] for a in f["accounts"]], ["margin"])
            self.assertEqual(f["totals"]["proceeds"], 1700.0)
            self.assertEqual(f["totals"]["acb"], 1490.0)
            t = cli(); self.assertEqual(t.returncode, 0, t.stderr)
            self.assertIn("FOR THE RETURN — taxable accounts, CAD", t.stdout)
            self.assertIn("1,490.00", t.stdout)


if __name__ == "__main__":
    unittest.main()
