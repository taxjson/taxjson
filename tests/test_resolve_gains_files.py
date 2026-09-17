"""resolve_gains_files — THE canonical gains-file discovery.

Pins the 2026-07b audit fix: `taxjson sum`/`list` read the PRE-wash gains
files while carryover/t1135/wash-sales preferred the wash-adjusted ones,
so totals disagreed across commands whenever a cross-account wash
disallowance fired. All six sites now resolve through
lib/report_model.resolve_gains_files and the reports name their basis.
"""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.lib.report_model import gains_basis_label, resolve_gains_files

PRE_WASH = {
    "summary": {"year": 2026, "total_gain": -1500.0},
    "transactions": [
        {"symbol": "AAPL.US", "date": "2026-05-02", "qty": -100,
         "currency": "CAD", "proceeds": 9000.0, "cost": 10500.0,
         "gain": -1500.0, "days_held": 200, "commission": 0.0, "fee": 0.0},
    ],
    "inventory": [{"symbol": "AAPL.US", "qty": 10, "total_cost": 1000.0,
                   "position_start_date": "2026-01-02"}],
    "wash_sales": [],
}

# Cross-account wash pass denied the loss: allowed gain 0.0.
POST_WASH = {
    "summary": {"year": 2026, "total_gain": 0.0},
    "transactions": [
        {"symbol": "AAPL.US", "date": "2026-05-02", "qty": -100,
         "currency": "CAD", "proceeds": 9000.0, "cost": 10500.0,
         "gain": 0.0, "raw_gain": -1500.0, "is_wash_sale": True,
         "days_held": 200, "commission": 0.0, "fee": 0.0},
    ],
    "inventory": [{"symbol": "AAPL.US", "qty": 10, "total_cost": 2500.0,
                   "position_start_date": "2026-01-02"}],
    "wash_sales": [{"symbol": "AAPL.US", "amount": 1500.0}],
}


def _work(td, wash=True):
    work = Path(td) / "work"
    work.mkdir(exist_ok=True)
    (work / "margin_gains.json").write_text(json.dumps(PRE_WASH))
    if wash:
        (work / "margin_gains_wash.json").write_text(json.dumps(POST_WASH))
    # Raw derivatives must never be picked up as accounts.
    (work / "margin_raw_gains.json").write_text(json.dumps(PRE_WASH))
    (work / "margin_raw_base_gains.json").write_text(json.dumps(PRE_WASH))
    return work


class TestResolveGainsFiles(unittest.TestCase):
    def test_prefers_wash_file(self):
        with tempfile.TemporaryDirectory() as td:
            files = resolve_gains_files(_work(td))
            self.assertEqual(list(files), ["margin"])
            self.assertEqual(files["margin"].name, "margin_gains_wash.json")
            self.assertEqual(gains_basis_label(files), "wash-adjusted")

    def test_falls_back_to_plain_file(self):
        with tempfile.TemporaryDirectory() as td:
            files = resolve_gains_files(_work(td, wash=False))
            self.assertEqual(files["margin"].name, "margin_gains.json")
            self.assertEqual(gains_basis_label(files), "pre-wash")

    def test_prefer_wash_false_means_pre_wash(self):
        with tempfile.TemporaryDirectory() as td:
            files = resolve_gains_files(_work(td), prefer_wash=False)
            self.assertEqual(files["margin"].name, "margin_gains.json")

    def test_raw_derivatives_never_become_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            files = resolve_gains_files(_work(td))
            self.assertNotIn("margin_raw", files)
            self.assertNotIn("margin_raw_base", files)

    def test_account_filter(self):
        with tempfile.TemporaryDirectory() as td:
            work = _work(td)
            (work / "crypto_gains.json").write_text(json.dumps(PRE_WASH))
            self.assertEqual(list(resolve_gains_files(work, "crypto")),
                             ["crypto"])
            self.assertEqual(resolve_gains_files(work, "nope"), {})

    def test_wash_only_account_still_found(self):
        # An account whose plain file was cleaned up but whose wash file
        # remains is still listed (prefer_wash path).
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"
            work.mkdir()
            (work / "m_gains_wash.json").write_text(json.dumps(POST_WASH))
            files = resolve_gains_files(work)
            self.assertEqual(files["m"].name, "m_gains_wash.json")


class TestSummaryUsesFilingBasis(unittest.TestCase):
    """`taxjson sum` must report the same basis as carryover/t1135 —
    the wash-adjusted numbers — and say so in the banner."""

    def _run(self, cmd, root, **kw):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cmd(argparse.Namespace(dir=str(root), **kw))
        return out.getvalue()

    def _project(self, td, wash=True):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
        _work(td, wash=wash)
        return root

    def test_sum_reports_wash_adjusted_numbers(self):
        from taxjson.bin.taxjson_run import cmd_summary
        with tempfile.TemporaryDirectory() as td:
            out = self._run(cmd_summary, self._project(td))
            self.assertIn("basis: wash-adjusted", out)
            # The denied loss is excluded: realized is 0.00, not -1,500.00.
            self.assertIn("0.00", out)
            self.assertNotIn("-1,500.00", out)

    def test_sum_labels_pre_wash_when_no_wash_files(self):
        from taxjson.bin.taxjson_run import cmd_summary
        with tempfile.TemporaryDirectory() as td:
            out = self._run(cmd_summary, self._project(td, wash=False))
            self.assertIn("basis: pre-wash", out)
            self.assertIn("-1,500.00", out)

    def test_list_prefers_wash_inventory_and_names_basis(self):
        from taxjson.bin.taxjson_run import cmd_positions
        with tempfile.TemporaryDirectory() as td:
            out = self._run(cmd_positions, self._project(td), account=None)
            self.assertIn("basis: wash-adjusted", out)
            self.assertIn("2,500.00", out)      # wash file's inventory cost


class TestSummaryGroupedTables(unittest.TestCase):
    """`taxjson sum` groups accounts into TAXABLE / SHELTERED /
    ALL ACCOUNTS tables when the config declares both types, and keeps
    the single-table layout when it doesn't."""

    def _run(self, root, **kw):
        from taxjson.bin.taxjson_run import cmd_summary
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cmd_summary(argparse.Namespace(dir=str(root), **kw))
        return out.getvalue()

    def _project(self, td, rrsp_type='sheltered'):
        root = Path(td)
        root.joinpath("taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            f'[accounts.rrsp]\ntype = "{rrsp_type}"\n')
        work = _work(td)
        (work / "rrsp_gains.json").write_text(json.dumps(PRE_WASH))
        return root

    def test_three_tables_with_subtotals(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td))
        self.assertIn("TAXABLE ACCOUNTS", out)
        self.assertIn("SHELTERED ACCOUNTS", out)
        self.assertIn("ALL ACCOUNTS", out)
        self.assertEqual(out.count("SUBTOTAL"), 2)
        self.assertEqual(out.count("\nTOTAL"), 1)
        # Each account appears once in its group table, once in ALL.
        self.assertEqual(out.count("margin"), 2)
        self.assertEqual(out.count("rrsp"), 3)   # +1: the NOTE line
        # margin ties wash-adjusted 0.00; rrsp only has pre-wash
        # -1,500.00 — the ALL total is their sum.
        self.assertIn("-1,500.00", out)

    def test_single_type_keeps_one_table(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td, rrsp_type='taxable'))
        self.assertNotIn("TAXABLE ACCOUNTS", out)
        self.assertNotIn("ALL ACCOUNTS", out)
        self.assertNotIn("SUBTOTAL", out)
        self.assertEqual(out.count("\nTOTAL"), 1)

    def test_single_account_keeps_one_table(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td), account="margin")
        self.assertNotIn("ALL ACCOUNTS", out)
        self.assertNotIn("SUBTOTAL", out)

    def test_json_carries_types_and_subtotals(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td), json=True)
        doc = json.loads(out)
        by_name = {r["account"]: r for r in doc["accounts"]}
        self.assertEqual(by_name["margin"]["type"], "taxable")
        self.assertEqual(by_name["rrsp"]["type"], "sheltered")
        self.assertEqual(set(doc["subtotals"]), {"taxable", "sheltered"})
        self.assertEqual(doc["subtotals"]["sheltered"]["realized"],
                         -1500.0)
        self.assertEqual(doc["subtotals"]["taxable"]["realized"], 0.0)

    def test_untyped_account_gets_its_own_group(self):
        # An account missing from taxjson.toml must not silently join
        # either bucket — it groups as UNTYPED so the gap is visible.
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            (root / "work" / "mystery_gains.json").write_text(
                json.dumps(PRE_WASH))
            out = self._run(root)
        self.assertIn("UNTYPED ACCOUNTS", out)
        self.assertIn("mystery", out)


class TestSummaryTotalColumn(unittest.TestCase):
    """TOTAL = REALIZED + DIVIDEND (the d7fdc1a column) — was untested."""

    def test_total_column_arithmetic(self):
        from taxjson.bin.taxjson_run import cmd_summary
        gains = {
            "summary": {"year": 2026, "total_gain": 100.0},
            "transactions": [
                {"symbol": "AAA.TO", "date": "2026-04-01", "qty": -10,
                 "currency": "CAD", "proceeds": 600.0, "cost": 500.0,
                 "gain": 100.0, "days_held": 30, "commission": 0.0,
                 "fee": 0.0},
                {"action": "DIVIDEND", "symbol": "AAA.TO",
                 "date": "2026-03-01", "currency": "CAD", "dividend": 50.0,
                 "gain": 0.0, "qty": 0, "gross_amount": 50.0,
                 "net_amount": 50.0, "type": "dividend"},
            ],
            "wash_sales": [],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            work = root / "work"
            work.mkdir()
            (work / "margin_gains.json").write_text(json.dumps(gains))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                cmd_summary(argparse.Namespace(dir=str(root)))
        text = out.getvalue()
        row = next(ln for ln in text.splitlines()
                   if ln.strip().startswith("margin"))
        # ACCOUNT STOCK OPTION REALIZED DIVIDEND PIL FEES TOTAL
        cols = row.split()
        self.assertEqual(cols[1], "100.00")     # STOCK
        self.assertEqual(cols[3], "100.00")     # REALIZED
        self.assertEqual(cols[4], "50.00")      # DIVIDEND
        self.assertEqual(cols[-1], "150.00")    # TOTAL = REALIZED + DIVIDEND




class TestListNegativeFilter(unittest.TestCase):
    """`taxjson list --negative` — only qty < 0 rows (shorts, or missed
    corporate actions in accounts that can't short, like the real
    XTD.TO stock-dividend case)."""

    def _run(self, root, **kw):
        from taxjson.bin.taxjson_run import cmd_positions
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cmd_positions(argparse.Namespace(dir=str(root), **kw))
        return out.getvalue()

    def _project(self, td, negatives=True):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.resp]\ntype = "sheltered"\n')
        work = root / "work"
        work.mkdir()
        inv = [{"symbol": "AAA.TO", "qty": 100, "total_cost": 1000.0,
                "position_start_date": "2026-01-02"}]
        if negatives:
            inv.append({"symbol": "XTD.TO", "qty": -208,
                        "total_cost": -1800.0,
                        "position_start_date": "2026-08-19"})
        (work / "resp_gains.json").write_text(json.dumps(
            {"summary": {"year": 2026, "total_gain": 0.0},
             "transactions": [], "inventory": inv, "wash_sales": []}))
        return root

    def test_negative_shows_only_short_rows(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td), account=None, negative=True)
        self.assertIn("NEGATIVE POSITIONS", out)
        self.assertIn("XTD.TO", out)
        self.assertNotIn("AAA.TO", out)
        self.assertIn("1 position(s)", out)

    def test_default_list_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td), account=None)
        self.assertIn("OPEN POSITIONS", out)
        self.assertIn("AAA.TO", out)
        self.assertIn("XTD.TO", out)

    def test_no_negatives_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td, negatives=False),
                            account=None, negative=True)
        self.assertIn("No negative positions", out)

    def test_json_carries_filter_marker(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td), account=None,
                            negative=True, json=True)
        doc = json.loads(out)
        self.assertEqual(doc.get("filter"), "negative")
        self.assertEqual([r["symbol"] for r in doc["rows"]], ["XTD.TO"])
        self.assertEqual(doc["totals"]["positions"], 1)


if __name__ == "__main__":
    unittest.main()
