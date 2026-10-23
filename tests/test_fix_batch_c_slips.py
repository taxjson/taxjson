"""Batch C regression pins (slips and misc): findings 42, 83, 98, 99 and
112 of the 2026-10 bug review (finding 82 is pinned in the fetch
plugin's own tests, packages/taxjson-fetch/tests/test_fetch_fix_batch_c.py).

Synthetic data only: made-up tickers (SAMP, ZZQ), issuers and broker
account hashes.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from taxjson.bin import taxjson_diff
from taxjson.lib import cra_slips as C
from taxjson.lib import slip_audit as SA
from taxjson.lib.project_tables import claimed_losses, table_problems


class TestClaimedYearTwice(unittest.TestCase):
    """42: `2023` and `02023` are two TOML keys but one tax year."""

    def test_two_spellings_of_one_year_are_a_problem(self):
        cfg = {"carryover": {"claimed": {"2023": 100, "02023": 200,
                                         "2024": 50}}}
        out, problems = claimed_losses(cfg)
        self.assertEqual(out, {2024: 50.0})      # neither 2023 kept
        self.assertEqual(len(problems), 1)
        self.assertIn("2023 is given twice", problems[0])
        self.assertIn("'02023'", problems[0])
        self.assertIn(problems[0], table_problems(cfg))

    def test_from_toml(self):
        try:
            import tomllib
        except ImportError:                     # Python < 3.11
            try:
                import tomli as tomllib
            except ImportError:
                self.skipTest("no TOML reader")
        cfg = tomllib.loads("[carryover]\nclaimed = { 2023 = 100, "
                            "02023 = 200 }\n")
        self.assertEqual(len(claimed_losses(cfg)[1]), 1)

    def test_one_spelling_unchanged(self):
        self.assertEqual(claimed_losses(
            {"carryover": {"claimed": {"2023": 100, "2024": 50}}}),
            ({2023: 100.0, 2024: 50.0}, []))


class TestDiffStructuredKey(unittest.TestCase):
    """83: `taxjson-diff --by` a list- or dict-valued field."""

    def test_unhashable_field_is_a_usable_key(self):
        a = {"action": "BUYSELL", "replacements": [{"id": 2}, {"id": 1}]}
        b = {"action": "BUYSELL", "replacements": [{"id": 2}, {"id": 1}]}
        c = {"action": "BUYSELL", "replacements": []}
        ka, kb, kc = (taxjson_diff.make_key(r, ["replacements"])
                      for r in (a, b, c))
        self.assertEqual(len({ka, kb, kc}), 2)   # hashable; equal values
        self.assertEqual(ka, kb)

    def test_cli_runs(self):
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        doc = {"transactions": [
            {"id": 1, "action": "BUYSELL", "symbol": "ZZQ",
             "wash_replacements": [{"date": "2025-01-02", "qty": 1}]}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            p.write_text(json.dumps(doc))
            argv = sys.argv
            sys.argv = ["taxjson-diff", "--by", "wash_replacements",
                        str(p), str(p)]
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    try:
                        rc = taxjson_diff.main()
                    except SystemExit as e:
                        rc = e.code
            finally:
                sys.argv = argv
        self.assertIn(rc, (0, None))


def _t5008_slip(cur, proceeds, cost):
    return SimpleNamespace(type="T5008", empty=False, account="margin",
                           code="SHS", currency=cur,
                           amounts={"proceeds": proceeds, "cost": cost})


class TestT5008NoRate(unittest.TestCase):
    """98: a foreign T5008 with no rate is not a slip total of 0.00."""

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
    """99: a weekly payer whose report and export dates drift 4+ days."""

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


def _group(h, amount):
    r = SA.BookRow(account="margin", id=h, action="INTEREST", symbol="",
                   root="", currency="CAD", native=amount, cad=amount,
                   pay_date="2025-03-03", tax_date="2025-03-03",
                   source=h + ".csv", source_hash=h, category="interest",
                   hashes=(h,))
    return C.Group("margin", (h,), "rbc_direct", (h + ".csv",), [r])


def _t5(amount, issuer="RBC DIRECT INVESTING INC."):
    return C.CraSlip("a.pdf", 2025, "T5", "original", issuer, "CAD",
                     {"13": amount})


class TestT5EqualBrokerAccounts(unittest.TestCase):
    """112: a T5 two broker accounts match equally is not placed."""

    def test_tie_left_unplaced(self):
        (pl,) = C.place([_t5(100)], [_group("a", 100), _group("b", 100)],
                        2025, {})
        self.assertIsNone(pl.group)
        self.assertIsNone(pl.account)
        self.assertEqual(pl.key, "")
        self.assertIn("ambiguous: 2", pl.how)
        self.assertIn("broker_account", pl.how)

    def test_clear_match_still_placed(self):
        (pl,) = C.place([_t5(100)], [_group("a", 100), _group("b", 40)],
                        2025, {})
        self.assertEqual((pl.account, pl.key), ("margin", "a"))
        self.assertIn("by its payments", pl.how)

    def test_two_slips_two_accounts_placed(self):
        pls = C.place([_t5(100), _t5(40)],
                      [_group("a", 100), _group("b", 40)], 2025, {})
        self.assertEqual([p.key for p in pls], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
