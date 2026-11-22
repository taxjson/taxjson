"""Fixes of 2026-10-29, round 7 (wave C), group taxtext: the standalone
pipeline tools. The validator survives an impossible trade date beside
a settlement date (backlog TJ-0987), reads the qty alias (TJ-0977) and
checks multiplier and market_value (TJ-0979); the .tt conversion reads
the qty alias too (TJ-0977), writes an untimed row's clock (TJ-0983),
reads an income line's per-share price (TJ-0984) and keeps a broker
trade cancellation (TJ-0985); currency conversion prices income on its
pay date (TJ-0974) and names a selected rate's own source (TJ-0986);
sort and conversion keep the per-file broker accounts (TJ-0976). One
test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented tickers (SYN*), invented account
ids, small amounts.

Formerly: test_fix_1029_r7c_taxtext
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_validate as V
from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


def _cli(module, args, cwd=None, stdin=None):
    """Run a taxjson module as the pipeline does: (rc, stdout, stderr)."""
    env = dict(os.environ, TAXJSON_WIDTH="0", TAXJSON_OFFLINE="1",
               PYTHONPATH=str(REPO_ROOT / "src"))
    c = subprocess.run(python_module_argv(f"taxjson.bin.{module}",
                                          [str(a) for a in args]),
                       capture_output=True, text=True, env=env, cwd=cwd,
                       input=stdin)
    return c.returncode, c.stdout, c.stderr


def _buy(**kw):
    row = {"action": "BUYSELL", "date": "2025-02-28",
           "date_settle": "2025-03-03", "time": "10:00:00",
           "symbol": "SYN.TO", "quantity": 10.0, "price": 10.0,
           "net_amount": 100.0, "fee": 0.0, "currency": "CAD",
           "account": "margin"}
    row.update(kw)
    return row


# ================================ TJ-0987 an impossible trade date
class TestTJ0987ImpossibleTradeDateBesideSettlement(unittest.TestCase):
    """TJ-0987: a purchase dated 2025-02-30 with settlement 2025-03-03
    crashed taxjson-validate with a ValueError traceback (the
    settlement-lag check parsed the date again): no report, and the
    next file was never validated.

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0987ImpossibleTradeDateBesideSettlement"""

    def test_reported_with_its_row_and_no_traceback(self):
        issues, _w = V.validate_transactions(
            [_buy(date="2025-02-30")], "book.json")
        found = [m for msgs in issues.values() for m in msgs]
        self.assertTrue(any("Impossible date: '2025-02-30'" in m
                            for m in found), found)

    def test_the_next_file_is_still_validated(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "a.json"
            good = Path(d) / "b.json"
            bad.write_text(json.dumps([_buy(date="2025-02-30")]))
            good.write_text(json.dumps([_buy()]))
            rc, out, err = _cli("taxjson_validate", [bad, good])
        self.assertEqual(rc, 1, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("Impossible date: '2025-02-30'", out)
        self.assertIn("OK: " + str(good), out)

    def test_a_real_day_keeps_the_settlement_checks(self):
        issues, _w = V.validate_transactions([_buy()], "book.json")
        self.assertEqual(dict(issues), {})
        issues, _w = V.validate_transactions(
            [_buy(date_settle="2025-02-27")], "book.json")
        self.assertTrue(any("before the trade date" in m
                            for msgs in issues.values() for m in msgs))


# ===================================================== TJ-0977 qty alias
def _qty_rows():
    buy = _buy(date="2025-01-02", date_settle="2025-01-03")
    sell = _buy(date="2025-06-02", date_settle="2025-06-03", price=20.0,
                net_amount=200.0)
    buy["qty"] = buy.pop("quantity")
    sell["qty"] = -sell.pop("quantity")
    return [buy, sell]


class TestTJ0977QtyAlias(unittest.TestCase):
    """TJ-0977: `qty`, the loaders' alias of `quantity`, read as 0 in
    taxjson-validate ("Quantity is 0") and taxjson-convert-tt (every
    line written with quantity 0).

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0977QtyAlias"""

    def test_validate_accepts_the_alias(self):
        issues, _w = V.validate_transactions(_qty_rows(), "book.json")
        self.assertEqual(dict(issues), {})

    def test_validate_still_refuses_a_zero_or_missing_quantity(self):
        rows = _qty_rows()
        rows[0]["qty"] = 0
        del rows[1]["qty"]
        issues, _w = V.validate_transactions(rows, "book.json")
        found = [m for msgs in issues.values() for m in msgs]
        self.assertTrue(any("Quantity is 0" in m for m in found), found)
        self.assertTrue(any("Missing 'quantity'" in m for m in found),
                        found)

    def test_convert_tt_writes_the_quantity(self):
        from taxjson.lib import tt_format as CT
        lines = [CT.tx_to_tt_line(r) for r in _qty_rows()]
        self.assertEqual(lines[0].split()[4], "10.00000000")
        self.assertEqual(lines[1].split()[4], "-10.00000000")
        for line in lines:
            row = CT.parse_tt_line(line, "margin")
            self.assertEqual(abs(row["quantity"]), 10.0)


# ============================ TJ-0979 multiplier and market_value types
class TestTJ0979ValidatorChecksEveryNumericField(unittest.TestCase):
    """TJ-0979: taxjson-validate said "Validation passed" for a row with
    multiplier="not-a-number" or market_value=true, which the shared
    loader (taxjson-sort --strict) refuses.

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0979ValidatorChecksEveryNumericField"""

    def _errors(self, **kw):
        issues, _w = V.validate_transactions([_buy(**kw)], "book.json")
        return [m for msgs in issues.values() for m in msgs]

    def test_malformed_values_are_errors(self):
        self.assertTrue(any("Non-numeric multiplier" in m for m in
                            self._errors(multiplier="not-a-number")))
        self.assertTrue(any("Non-numeric market_value" in m for m in
                            self._errors(market_value=True)))
        self.assertTrue(any("Null multiplier" in m for m in
                            self._errors(multiplier=None)))

    def test_the_loader_refuses_the_same_rows(self):
        from taxjson.lib.tx_load import coerce_transaction_row
        for kw in ({"multiplier": "not-a-number"}, {"market_value": True}):
            with self.assertRaises(ValueError):
                coerce_transaction_row(_buy(**kw), 0, "book.json")

    def test_numbers_and_absence_pass(self):
        self.assertEqual(self._errors(multiplier=100, market_value=95.5),
                         [])
        self.assertEqual(self._errors(), [])


# =========================== TJ-0976 per-file accounts through a stage
class TestTJ0976PassThroughKeepsSourceAccounts(unittest.TestCase):
    """TJ-0976: two consolidated statements of disjoint broker accounts,
    each with one identical CAD 100 purchase: `sort --dedup` keeps both,
    but a plain sort (or an identity CAD-to-CAD conversion) first wrote
    no metadata.source_accounts, and the next dedup kept one.

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0976PassThroughKeepsSourceAccounts"""

    def _doc(self):
        rows = []
        for src in ("one.csv", "two.csv"):
            row = _buy(date="2025-01-02", date_settle="2025-01-03")
            row["source"] = src
            rows.append(row)
        return {"transactions": rows, "metadata": {"source_accounts": {
            "one.csv": ["aaaa000001", "aaaa000002"],
            "two.csv": ["bbbb000001", "bbbb000002"]}}}

    def _dedup_after(self, module, extra):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            mid = Path(d) / "mid.json"
            book.write_text(json.dumps(self._doc()))
            rc, out, err = _cli(module, [book, *extra])
            self.assertEqual(rc, 0, err)
            meta = json.loads(out)["metadata"]
            self.assertEqual(meta["source_accounts"],
                             self._doc()["metadata"]["source_accounts"])
            mid.write_text(out)
            rc, out, err = _cli("taxjson_sort", [mid, "--dedup", "--strict"])
            self.assertEqual(rc, 0, err)
        return json.loads(out)["transactions"]

    def test_direct_dedup_keeps_both(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            book.write_text(json.dumps(self._doc()))
            rc, out, err = _cli("taxjson_sort", [book, "--dedup", "--strict"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(json.loads(out)["transactions"]), 2)

    def test_a_plain_sort_keeps_the_accounts(self):
        self.assertEqual(len(self._dedup_after("taxjson_sort",
                                               ["--strict"])), 2)

    def test_an_identity_conversion_keeps_the_accounts(self):
        self.assertEqual(len(self._dedup_after("taxjson_convert_currency",
                                               ["--to", "CAD"])), 2)

    def test_stdin_keeps_them_too(self):
        rc, out, err = _cli("taxjson_sort", [],
                            stdin=json.dumps(self._doc()))
        self.assertEqual(rc, 0, err)
        self.assertIn("one.csv", json.loads(out)["metadata"][
            "source_accounts"])
        rc, out, err = _cli("taxjson_convert_currency", ["--to", "CAD"],
                            stdin=json.dumps(self._doc()))
        self.assertEqual(rc, 0, err)
        self.assertIn("two.csv", json.loads(out)["metadata"][
            "source_accounts"])

    def test_no_accounts_no_metadata_block(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            book.write_text(json.dumps([_buy()]))
            rc, out, err = _cli("taxjson_sort", [book])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("metadata", json.loads(out))


if __name__ == "__main__":
    unittest.main()
