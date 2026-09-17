"""--json coverage across the query commands (the GUI's data layer).

Every command a GUI renders must emit parseable JSON on stdout — pure
JSON (banners/legends stay out; notes/warnings on stderr). One fixture
project exercises the whole family.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECENT = (date.today() - timedelta(days=3)).isoformat()


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


def _project(tmp):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\nprovince = "ON"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_raw.json").write_text(json.dumps(
        {"transactions": [
            {"action": "BUYSELL", "date": "2026-03-01", "time": "09:30:00",
             "symbol": "AAA.TO", "quantity": 100, "price": 10.0,
             "net_amount": 1000.0, "fee": 5.0, "currency": "CAD"},
            {"action": "BUYSELL", "date": RECENT, "time": "10:00:00",
             "symbol": "AAA.TO", "quantity": -40, "price": 12.0,
             "net_amount": 480.0, "commission": 3.0, "currency": "CAD"},
            {"action": "DIVIDEND", "date": "2026-05-01", "time": "09:30:00",
             "symbol": "AAA.TO", "quantity": 60, "price": 0.5,
             "gross_amount": 30.0, "net_amount": 30.0, "currency": "CAD"},
            {"action": "DIVIDEND_IN_LIEU", "date": "2026-05-15",
             "time": "09:30:00", "symbol": "AAA.TO", "quantity": 60,
             "price": 0.25, "gross_amount": 15.0, "net_amount": 15.0,
             "currency": "CAD"},
            {"action": "ADJUST", "date": "2026-05-20", "time": "09:30:00",
             "symbol": "AAA.TO", "net_amount": -25.0, "currency": "CAD",
             "type": "roc"}]}))
    (root / "work" / "margin_raw_gains.json").write_text(json.dumps(
        {"transactions": [
            {"date": "2026-04-01", "symbol": "AAA.TO", "qty": 40,
             "currency": "CAD", "proceeds": 480.0, "cost": 400.0,
             "gain": 80.0, "days_held": 31}]}))
    (root / "work" / "margin_gains.json").write_text(json.dumps(
        {"summary": {"year": "2026"}, "transactions": [
            {"symbol": "AAA.TO", "date": "2026-04-01", "qty": 40,
             "gain": 80.0, "cost": 400.0, "proceeds": 480.0,
             "currency": "CAD", "days_held": 31,
             "raw_gain": -100.0, "is_wash_sale": True,
             "disallowed_amount": 100.0},
            {"action": "DIVIDEND", "symbol": "AAA.TO", "dividend": 30.0,
             "currency": "CAD"}],
         "inventory": [
            {"symbol": "AAA.TO", "qty": 60, "total_cost": 600.0,
             "position_start_date": "2026-03-01",
             "last_acq_date": "2026-03-01"}]}))
    return root


class TestJsonOutputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = _project(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _json(self, *args):
        r = _run(self.root, *args, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"{args}: stdout is not pure JSON ({e}):\n"
                      f"{r.stdout[:400]}")

    def test_tx_views(self):
        ev = self._json("events", "1y", "margin")
        self.assertEqual(len(ev["rows"]), 5)
        self.assertEqual(ev["rows"][0]["account"], "margin")
        self.assertIn("dividend", ev["totals"])
        tr = self._json("trades", "1y", "margin")
        self.assertEqual(len(tr["rows"]), 2)
        self.assertEqual(tr["totals"]["buy"]["CAD"], 1000.0)
        dv = self._json("divs", "1y", "margin")
        self.assertEqual(len(dv["rows"]), 2)       # div + in-lieu
        self.assertEqual(len(self._json("dil", "1y", "margin")["rows"]), 1)
        self.assertEqual(len(self._json("roc", "1y", "margin")["rows"]), 1)

    def test_gains_and_fees(self):
        g = self._json("gains", "1y", "margin")
        self.assertEqual(g["rows"][0]["gain"], 80.0)
        self.assertEqual(g["totals"]["CAD"], 80.0)
        f = self._json("fees", "1y")
        self.assertEqual(len(f["rows"]), 2)
        self.assertEqual(f["totals"]["CAD"], 8.0)

    def test_roll_ups(self):
        ds = self._json("divs-sum")
        self.assertEqual(ds["rows"][0]["dividend"], 45.0)  # div + PIL
        dil = self._json("dil-sum")
        self.assertEqual(dil["rows"][0]["in_lieu"], 15.0)
        roc = self._json("roc-sum")
        self.assertEqual(roc["rows"][0]["capital_returned"], 25.0)
        ts = self._json("trades-sum")
        self.assertEqual(ts["rows"][0]["buys"], 1)
        self.assertEqual(ts["totals"]["CAD"]["fees"], 8.0)

    def test_empty_roll_up_is_empty_rows_not_prose(self):
        doc = self._json("divs-sum", "1d")
        self.assertEqual(doc["rows"], [])

    def test_sum_with_estimate(self):
        doc = self._json("sum")
        self.assertEqual(doc["accounts"][0]["account"], "margin")
        self.assertEqual(doc["totals"]["realized"], 80.0)
        self.assertNotIn("estimate", doc)
        doc2 = self._json("sum", "--other-income", "200000")
        self.assertEqual(doc2["estimate"]["country"], "canada")
        self.assertIn("estimated_tax", doc2["estimate"])

    def test_list_positions(self):
        doc = self._json("list")
        self.assertEqual(doc["rows"][0]["symbol"], "AAA.TO")
        self.assertEqual(doc["totals"]["book_cost"], 600.0)
        self.assertIn("basis", doc)

    def test_wash_sales(self):
        doc = self._json("wash-sales")
        self.assertEqual(len(doc["rows"]), 1)
        self.assertEqual(doc["totals"]["denied"], 100.0)
        self.assertEqual(doc["rows"][0]["account"], "margin")

    def test_leaps_empty_docs(self):
        self.assertEqual(self._json("leaps")["rows"], [])
        self.assertEqual(self._json("leaps-sum")["rows"], [])


if __name__ == "__main__":
    unittest.main()
