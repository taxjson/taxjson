"""`taxjson shares` — combined quantity per symbol across accounts, from
the canonical per-account inventories (post ticker.map)."""
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
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _gains(root, acct, inv):
    (root / "work").mkdir(exist_ok=True)
    (root / "work" / f"{acct}_gains.json").write_text(json.dumps(
        {"summary": {"year": "2026"}, "transactions": [],
         "inventory": [{"symbol": s, "qty": q, "total_cost": c}
                       for s, (q, c) in inv.items()]}))


class TestShares(unittest.TestCase):
    def _project(self, tmp):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')
        _gains(root, "margin", {"XIU.TO": (100, 3000.0),
                                "ANET.US": (-50, -5000.0),   # short
                                "AEM280121C00155000.TO": (2, 400.0),
                                "GONE.TO": (0, 0.0)})
        _gains(root, "rrsp", {"XIU.TO": (40, 1300.0),
                              "ANET.US": (75, 8000.0)})
        return root

    def test_combined_per_symbol_with_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run(root, "shares", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            doc = json.loads(r.stdout)
            rows = {d["symbol"]: d for d in doc["rows"]}
            # Options excluded, zero positions dropped, shorts netted.
            self.assertEqual(set(rows), {"XIU.TO", "ANET.US"})
            self.assertEqual(rows["XIU.TO"]["qty"], 140)
            self.assertEqual(rows["XIU.TO"]["cost"], 4300.0)
            self.assertEqual(rows["XIU.TO"]["accounts"],
                             {"margin": 100, "rrsp": 40})
            self.assertEqual(rows["ANET.US"]["qty"], 25)
            self.assertEqual(rows["ANET.US"]["accounts"],
                             {"margin": -50, "rrsp": 75})
            self.assertFalse(doc["options_included"])
            t = _run(root, "shares")
            self.assertEqual(t.returncode, 0, t.stderr)
            self.assertIn("SYMBOL", t.stdout)
            self.assertIn("ACCOUNTS", t.stdout)
            self.assertIn("margin -50, rrsp 75", t.stdout)
            self.assertIn("2 symbol(s)", t.stdout)

    def test_options_and_scope_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run(root, "shares", "--options", "--json")
            rows = {d["symbol"] for d in json.loads(r.stdout)["rows"]}
            self.assertIn("AEM280121C00155000.TO", rows)
            r = _run(root, "shares", "--sheltered", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual(doc["scope"], "sheltered")
            self.assertEqual({d["symbol"]: d["qty"] for d in doc["rows"]},
                             {"XIU.TO": 40, "ANET.US": 75})
            r = _run(root, "shares", "--taxable", "--sort", "qty", "--json")
            self.assertEqual([d["symbol"] for d in json.loads(r.stdout)["rows"]],
                             ["XIU.TO", "ANET.US"])   # 100 before |-50|
            r = _run(root, "shares", "--taxable", "--sheltered")
            self.assertNotEqual(r.returncode, 0)

    def test_empty_scope_and_odd_config_are_clear_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.rrsp]\ntype = "sheltered"\n')
            _gains(root, "rrsp", {"XIU.TO": (40, 1300.0)})
            r = _run(root, "shares", "--taxable")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no taxable account", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            # A non-table [accounts] entry must not traceback.
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts]\nrrsp = "x"\n')
            r = _run(root, "shares", "--sheltered", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            # JSON quantities are rounded, not float noise.
            _gains(root, "rrsp", {"F.US": (0.1, 1.0)})
            _gains(root, "margin", {"F.US": (0.2, 1.0)})
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.rrsp]\ntype = "sheltered"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            doc = json.loads(_run(root, "shares", "--json").stdout)
            self.assertEqual(doc["rows"][0]["qty"], 0.3)

    def test_no_gains_files_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            r = _run(root, "shares")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("taxjson run", r.stderr)


if __name__ == "__main__":
    unittest.main()
