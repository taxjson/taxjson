"""`taxjson option-boundary` — written options across a year boundary and
the amendment instruction (ITA s.49(1)-(4))."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_boundary import straddling

REPO_ROOT = Path(__file__).resolve().parent.parent
OPT = "Q260116C00050000.TO"


def T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD", "account": "margin", **kw})


class TestStraddling(unittest.TestCase):
    BOOK = [T(date="2025-12-15", date_settle="2025-12-16", symbol=OPT, quantity=-3, price=4, net_amount=1197.0),
            T(date="2026-01-10", date_settle="2026-01-12", symbol=OPT, quantity=1, price=1, net_amount=101.0),
            T(date="2026-01-16", date_settle="2026-01-16", symbol=OPT, quantity=1, price=0, net_amount=0.0),
            TaxTransaction(action="ASSIGN", date="2026-01-16", date_settle="2026-01-16", symbol=OPT, quantity=1, price=0, net_amount=0.0, currency="CAD", account="margin"),
            T(date="2025-03-01", date_settle="2025-03-03", symbol="Z260116C00010000.TO", quantity=-1, price=2, net_amount=199.0),
            T(date="2025-04-01", date_settle="2025-04-02", symbol="Z260116C00010000.TO", quantity=1, price=1, net_amount=101.0)]

    def test_rows_and_instructions_under_grant_timing(self):
        rows = straddling(self.BOOK, 2025, "grant", 2025, filed_years={2025})
        kinds = {(r["close_kind"], r["units"]) for r in rows}
        self.assertEqual(kinds, {("buy-back", 1.0), ("expiry", 1.0), ("assignment", 1.0)})
        self.assertTrue(all(r["symbol"] == OPT for r in rows))          # same-year Z round trip excluded
        by = {r["close_kind"]: r for r in rows}
        self.assertTrue(by["buy-back"]["action"].startswith("no amendment"))
        self.assertTrue(by["expiry"]["action"].startswith("no amendment"))
        self.assertTrue(by["assignment"]["action"].startswith("T1-ADJ 2025"))   # 2025 filed, assigned in 2026
        self.assertIn("399.00", by["assignment"]["action"])
        rows = straddling(self.BOOK, 2025, "grant", 2025, filed_years=set())
        self.assertTrue({r["close_kind"]: r for r in rows}["assignment"]["action"].startswith("if 2025 was filed"))

    def test_close_timing_and_transition_wording(self):
        rows = straddling(self.BOOK, 2025, "close", None)
        self.assertTrue(all(r["timing"] == "close" for r in rows))
        self.assertIn("enable grant timing with option_grant_timing_since = 2025", {r["close_kind"]: r for r in rows}["buy-back"]["action"])
        rows = straddling(self.BOOK, 2026, "grant", 2026)                # written 2025 < since 2026 -> transition
        self.assertIn("kept on close timing by option_grant_timing_since = 2026", {r["close_kind"]: r for r in rows}["buy-back"]["action"])

    def test_open_at_year_end(self):
        book = [T(date="2025-12-15", date_settle="2025-12-16", symbol=OPT, quantity=-2, price=4, net_amount=798.0)]
        rows = straddling(book, 2025, "grant", 2025)
        self.assertEqual([(r["close_kind"], r["units"], r["premium"]) for r in rows], [("open", 2.0, 798.0)])
        self.assertIn("T1-ADJ 2025", rows[0]["action"])


class TestCommand(unittest.TestCase):
    def test_cli_reads_taxable_books_and_filed_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text('[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n[accounts.rrsp]\ntype = "sheltered"\n')
            (root / "work").mkdir(); (root / "filed").mkdir()
            (root / "filed" / "2025.json").write_text("{}")
            rows = [{"action": "BUYSELL", "date": "2025-12-15", "time": "09:30:00", "date_settle": "2025-12-16", "symbol": OPT, "quantity": -1, "price": 4.0, "net_amount": 399.0, "currency": "CAD", "account": "margin"},
                    {"action": "ASSIGN", "date": "2026-01-16", "time": "09:30:00", "date_settle": "2026-01-16", "symbol": OPT, "quantity": 1, "price": 0.0, "net_amount": 0.0, "currency": "CAD", "account": "margin"}]
            (root / "work" / "margin_base.json").write_text(json.dumps({"transactions": rows}))
            (root / "work" / "rrsp_base.json").write_text(json.dumps({"transactions": rows}))   # sheltered: ignored
            def cli(*a):
                return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root), "option-boundary", *a],
                                      cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            r = cli("--json"); self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual((doc["timing"], doc["since"], doc["filed_years"]), ("grant", 2025, [2025]))
            self.assertEqual(len(doc["rows"]), 1)
            self.assertTrue(doc["rows"][0]["action"].startswith("T1-ADJ 2025"))
            t = cli(); self.assertEqual(t.returncode, 0, t.stderr)
            self.assertIn("1 item(s) require an amended return", t.stdout)
            (root / "taxjson.toml").write_text('[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\noption_premium_timing = "close"\n[accounts.margin]\ntype = "taxable"\n')
            t = cli(); self.assertIn("premium timing: close", t.stdout)
            self.assertIn("No amended return is required", t.stdout)


if __name__ == "__main__":
    unittest.main()
