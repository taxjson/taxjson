"""`taxjson-gains --as-of` + `taxjson list --date`: the books as they
stood on a date (full ACB/deferred fidelity; pre-wash, pre-map)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(d, q, p, sym="XYZ.TO"):
    return {"action": "BUYSELL", "date": d, "date_settle": d,
            "time": "09:30:00", "symbol": sym, "quantity": q, "price": p,
            "net_amount": abs(q) * p, "currency": "CAD",
            "account": "margin"}


TXS = [_tx("2025-01-06", 100, 20.0), _tx("2025-03-01", -60, 25.0),
       _tx("2025-05-01", 40, 30.0)]


def _project(tmp):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": TXS}))
    return root


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestAsOfShelteredAndDerivatives(unittest.TestCase):
    """Regression (real-data report): sheltered accounts hold TRANSFER
    rows (legal there) — --taxable must not be passed for them; and
    work/ derivative files (lira_raw_base.json) must never masquerade
    as accounts."""

    def test_sheltered_transfers_and_raw_derivative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.lira]\ntype = "sheltered"\n')
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": TXS}))
            lira = [{"action": "TRANSFER", "date": "2025-01-10",
                     "date_settle": "2025-01-10", "time": "09:30:00",
                     "symbol": "AAA.TO", "quantity": 50, "price": 10.0,
                     "net_amount": 500.0, "currency": "CAD",
                     "account": "lira"}]
            (root / "work" / "lira_base.json").write_text(
                json.dumps({"transactions": lira}))
            # A derivative that must NOT be treated as an account.
            (root / "work" / "lira_raw_base.json").write_text(
                json.dumps({"transactions": lira}))
            r = _run(root, "list", "--date", "2025-06-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("as-of compute failed", r.stderr)
        self.assertNotIn("lira_raw", r.stdout)
        self.assertRegex(r.stdout, r"lira\s+AAA\.TO\s+50")
        self.assertRegex(r.stdout, r"margin\s+XYZ\.TO\s+80")


class TestAsOf(unittest.TestCase):
    def test_gains_as_of_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": TXS}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2025",
                 "--as-of", "2025-02-01", "--taxable", str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            inv = json.loads(r.stdout)["inventory"]
        self.assertEqual(inv[0]["qty"], 100)
        self.assertAlmostEqual(inv[0]["total_cost"], 2000.0, places=2)

    def test_list_date_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            feb = _run(root, "list", "--date", "2025-02-01")
            apr = _run(root, "list", "--date", "2025-04-01", "--json")
        self.assertEqual(feb.returncode, 0, feb.stderr)
        self.assertIn("as of 2025-02-01", feb.stdout)
        self.assertRegex(feb.stdout, r"XYZ\.TO\s+100\s+2,000\.00")
        doc = json.loads(apr.stdout)
        row = doc["rows"][0]
        self.assertEqual(row["qty"], 40)          # 100 - 60 sold
        self.assertAlmostEqual(row["cost"], 800.0)  # ACB avg 20/sh
        self.assertIn("as of 2025-04-01", doc["basis"])

    def test_bad_date_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_project(tmp), "list", "--date", "banana")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("YYYY-MM-DD", r.stderr)


if __name__ == "__main__":
    unittest.main()
