"""Bug-review batch C (income): dividend estimation, income dating and
the year hand-off (findings 38, 49, 65, 76, 77, 92 of the 2026-10
review).

All data is synthetic (made-up tickers and accounts).

Formerly: test_fix_batch_c_income
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _qa_project import tj, toml


def _div(d, sym, qty, per, acct="margin", cur="CAD", **kw):
    r = {"action": "DIVIDEND", "date": d, "date_settle": d, "symbol": sym,
         "quantity": qty, "gross_amount": qty * per,
         "net_amount": qty * per, "currency": cur, "account": acct}
    r.update(kw)
    return r


class TestDivEstimateWindow(unittest.TestCase):
    """Formerly: test_fix_batch_c_income.TestDivEstimateWindow"""

    @rule(R.CA_PLAN_06)
    def test_49_positional_window_has_the_months_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "work").mkdir(parents=True)
            (root / "taxjson.toml").write_text(toml(year=2025))
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [_div("2025-01-02", "ZZD.TO", 10, 1.0)]}))
            (root / "work" / "margin_raw_gains.json").write_text(
                json.dumps({"inventory": [{"symbol": "ZZD.TO", "qty": 10,
                                           "total_cost": 100,
                                           "currency": "CAD"}]}))
            for period in ("100000y", "11y", "121m"):
                with self.subTest(period=period):
                    r = tj(root, "div-estimate", period, "--json",
                           check=False)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertIn("longer than 120 months", r.stderr)
            r = tj(root, "div-estimate", "10y", "--date", "2025-06-01",
                   "--json")
            self.assertEqual(json.loads(r.stdout)["months"], 120)


if __name__ == "__main__":
    unittest.main()
