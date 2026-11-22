"""Batch C regression pins (slips and misc): findings 42, 83, 98, 99 and
112 of the 2026-10 bug review (finding 82 is pinned in the fetch
plugin's own tests, packages/taxjson-fetch/tests/test_fetch_fix_batch_c.py).

Synthetic data only: made-up tickers (SAMP, ZZQ), issuers and broker
account hashes.

Formerly: test_fix_batch_c_slips
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from taxjson.lib import slip_audit as SA


def _t5008_slip(cur, proceeds, cost):
    return SimpleNamespace(type="T5008", empty=False, account="margin",
                           code="SHS", currency=cur,
                           amounts={"proceeds": proceeds, "cost": cost})


class TestT5008NoRate(unittest.TestCase):
    """98: a foreign T5008 with no rate is not a slip total of 0.00.

    Formerly: test_fix_batch_c_slips.TestT5008NoRate"""

    def test_no_rate_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            rows = SA._t5008(Path(d), [_t5008_slip("USD", 100.0, 80.0)],
                             2025, 1.0, {"USD": None})
        (row,) = rows
        self.assertIsNone(row["slip_proceeds"])
        self.assertIsNone(row["slip_cost"])
        self.assertEqual(row["no_rate"], ["USD"])
        rep = {"year": 2025, "tolerance": 1.0, "sources": [],
               "annual_average": {}, "accounts": [],
               "coverage": {"no_slip": [], "slips_without_books": []},
               "t5008": rows, "issues": [], "problems": [], "notes": [],
               "suggestions": {"capital_gains_dividends": [],
                               "tt_lines": [], "notes": []}}
        text = "\n".join(SA.render(rep, width_=120))
        self.assertIn("no USD rate for 2025", text)
        line = next(ln for ln in text.splitlines()
                    if ln.lstrip().startswith("margin"))
        self.assertNotIn("0.00*", line)
        self.assertIn("—", line)

    def test_with_rate_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            (row,) = SA._t5008(Path(d), [_t5008_slip("USD", 100.0, 80.0)],
                               2025, 1.0, {"USD": 1.25})
        self.assertEqual((row["slip_proceeds"], row["slip_cost"]),
                         (125.0, 100.0))
        self.assertNotIn("no_rate", row)


def _book(day, amount, root="SAMP"):
    return SA.BookRow(account="margin", id=f"r{day}", action="DIVIDEND",
                      symbol=root, root=root, currency="USD", native=amount,
                      cad=amount, pay_date=day, tax_date=day,
                      source="b.csv", source_hash="h1", category="foreign")


def _pay(day, gross, symbol="SAMP"):
    return SimpleNamespace(pay_date=day, symbol=symbol, pil=False,
                           gross=gross, currency="USD", components=[])


class TestInOrderPaymentMatch(unittest.TestCase):
    """99: a weekly payer whose report and export dates drift 4+ days.

    Formerly: test_fix_batch_c_slips.TestInOrderPaymentMatch"""

    def _match(self, pays, rows):
        slip = SimpleNamespace(source="ib", payments=pays)
        issues = []
        out = SA._match_payments("margin", "report", [slip], rows, 2025,
                                 issues)
        return out, issues

    def test_drifted_weekly_payments_all_match(self):
        out, issues = self._match(
            [_pay("2025-02-08", 10.0), _pay("2025-02-15", 10.0)],
            [_book("2025-02-02", 10.0), _book("2025-02-09", 10.0)])
        self.assertEqual(out["matched"], 2)
        self.assertEqual(out["missing_from_books"], [])
        self.assertEqual(out["missing_from_slip"], [])
        self.assertEqual(issues, [])

    def test_isolated_payment_takes_nearest(self):
        out, _ = self._match(
            [_pay("2025-03-10", 10.0)],
            [_book("2025-03-05", 9.0), _book("2025-03-11", 10.0)])
        self.assertEqual(out["matched"], 1)
        self.assertEqual(out["amount_differs"], [])
        self.assertEqual([e["date"] for e in out["missing_from_slip"]],
                         ["2025-03-05"])

    def test_pair_in_order(self):
        self.assertEqual(SA._pair_in_order(
            ["2025-02-08", "2025-02-15"], ["2025-02-02", "2025-02-09"]),
            [(0, 0), (1, 1)])
        self.assertEqual(SA._pair_in_order(["2025-02-08"], []), [])


if __name__ == "__main__":
    unittest.main()
