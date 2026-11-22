"""Planning low round: report and diagnostic tools.

  R1-211 / S051-01 / S031-04  sum-gains, `sum`, fees-sum and divs-sum
          labels: the non-option column is not 'STOCK', crypto staking
          is not a dividend
  S051-00 the .sum bottom line is GAIN + DIV + PIL, labelled so
  S051-02 sum-gains FILE keeps wash_solver_iterations (and routed rows)
  R1-313  sum-gains TOTAL row pinned; summary.total_gain leaves income out
  S028-07 ccd-sum CLOSES/QTY do not count grant-timing write records
  R1-239 / S028-08 / S028-11  ccd-gains / leaps-gains per-unit columns,
          break-even legacy rows, per-currency totals
  S028-09 / S035-13 / S051-11 / S079-11  unreadable or wrong-shape
          inputs: a one-line refusal, never a traceback or a partial
          report at exit 0; a bare-array book is accepted
  R1-265 / S029-15 / S029-18 / G7-1  taxjson-diff and taxjson-extractors
  S035-07 / S035-08  taxjson-missing-history --account and registered
          accounts
  S051-13 / S051-14  sum-income help text; income rows with no amount
  S077-14 report.json wash total from 'disallowed_amount'

All data is synthetic.

Formerly: test_fixl_planning_reports
"""
import json
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
from _support.view_report_tools import _tool, _row


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)

    def write(self, name, doc):
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(doc, (bytes, str)):
            p.write_bytes(doc if isinstance(doc, bytes) else doc.encode())
        else:
            p.write_text(json.dumps(doc))
        return p


# -------------------------------------------- input contract (many tools)
class TestToolInputContract(_Tmp):
    """Each tool refuses an unreadable / non-UTF-8 / wrong-shape input in
    one line (no traceback); a bare-array book is accepted.

    Formerly: test_fixl_planning_reports.TestToolInputContract"""

    def _cases(self):
        bad = {"trunc": self.write("trunc.json", '{"transactions": ['),
               "latin1": self.write("latin1.json", b'{"a": "caf\xe9"}'),
               "int": self.write("int.json", "5")}
        good_map = self.write("taxjson.toml", "")
        slip = self.write("slip.csv", "symbol,quantity,proceeds\n")

        def argv(module, f):
            return {
                "taxjson_lint_crosslistings": ["--taxable", f],
                "taxjson_fees": [f],
                "taxjson_apply_distributions": [f, "--config", good_map],
                "taxjson_form_export": ["--form", "schedule3",
                                        "--country", "canada", f],
                "taxjson_split_gains": [f, "--account", "margin"],
                "taxjson_sum_gains": [f],
                "taxjson_sum_income": [f],
                "taxjson_leaps_gains": [f],
                "taxjson_ccd_gains": [f],
                "taxjson_reconcile_slips": [slip, "--gains", f,
                                            "--country", "canada"],
                "taxjson_explain": ["--country", "canada", f],
                "taxjson_ticker_map": [f],
                "taxjson_carryover": ["--country", "canada", f],
                "taxjson_diff": [f, f],
            }[module]
        return bad, argv

    MODULES = ("taxjson_lint_crosslistings", "taxjson_fees",
               "taxjson_apply_distributions", "taxjson_form_export",
               "taxjson_split_gains", "taxjson_sum_gains",
               "taxjson_sum_income", "taxjson_leaps_gains",
               "taxjson_ccd_gains", "taxjson_reconcile_slips",
               "taxjson_explain", "taxjson_ticker_map", "taxjson_carryover",
               "taxjson_diff")

    def test_bad_inputs_are_one_line_refusals(self):
        bad, argv = self._cases()
        for mod in self.MODULES:
            for kind, f in bad.items():
                with self.subTest(mod=mod, kind=kind):
                    r = _tool(mod, *argv(mod, f))
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertNotEqual(r.returncode, 0, r.stdout[-500:])
                    self.assertIn(f.name, r.stderr)

    def test_wrong_shape_rows_never_traceback(self):
        _, argv = self._cases()
        f = self.write("shape.json", '[{"date": 5}]')
        for mod in self.MODULES:
            with self.subTest(mod=mod):
                r = _tool(mod, *argv(mod, f))
                self.assertNotIn("Traceback", r.stderr)

    def test_bare_array_book_is_accepted(self):
        book = self.write("bare.json", [
            _row("2025-01-06", "ENB.TO", 10, -500.0),
            _row("2025-03-03", "ENB.TO", -10, 600.0, commission=9.99)])
        # The gains-report tools read gains rows: a bare array of them is
        # accepted, while a stage book (rows without a gain) is refused
        # since S033-01 (it summed to 0.00 at exit 0).
        gains = self.write("bare_gains.json", [
            {"date": "2025-03-03", "date_settle": "2025-03-04",
             "symbol": "ENB.TO", "qty": -10, "proceeds": 590.01,
             "cost": 500.0, "gain": 90.01, "direction": "LONG",
             "commission": 9.99, "fee": 0.0, "currency": "CAD",
             "account": "margin"}])
        for mod, args in (("taxjson_lint_crosslistings",
                           ["--taxable", book]),
                          ("taxjson_sum_gains", [gains]),
                          ("taxjson_ccd_gains", [gains]),
                          ("taxjson_leaps_gains", [gains])):
            with self.subTest(mod=mod):
                r = _tool(mod, *args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_fees_sum_unreadable_cache_file_is_not_skipped(self):
        cache = self.tmp / "work"
        self.write("work/margin_qt.json", {
            "metadata": {"source_brokerage": "questrade"},
            "transactions": [_row("2025-03-03", "ENB.TO", 10, -500.0,
                                  commission=9.95)]})
        self.write("work/margin_ib.json", '{"metadata": {"source_')
        r = _tool("taxjson_fees", "--cache", cache)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("margin_ib.json", r.stderr)
        self.assertNotIn("TOTAL", r.stdout)

    def test_fees_sum_labels(self):
        self.write("work/c.json", {
            "metadata": {"source_brokerage": "questrade"},
            "transactions": [_row("2025-03-03", "ENB.TO", 10, -500.0,
                                  commission=9.95)]})
        r = _tool("taxjson_fees", "--cache", self.tmp / "work")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("non-option", r.stdout)
        self.assertNotIn(" stocks ", r.stdout)
        self.assertIn("$/UNIT", r.stdout)


if __name__ == "__main__":
    unittest.main()
