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


# --------------------------------------------------------- taxjson-diff
class TestDiff(_Tmp):
    """Formerly: test_fixl_planning_reports.TestDiff"""
    def test_unknown_by_field_is_refused(self):
        a = self.write("a.json", [_row("2025-01-06", "AAA.TO", 1, -1.0)])
        r = _tool("taxjson_diff", "--by", "symbl", a, a)
        self.assertEqual(r.returncode, 2)
        self.assertIn("symbl", r.stderr)

    def test_reordered_same_key_rows_are_unchanged(self):
        rows = [_row("2025-01-22", "CASH", 0, -17.73, action="INTEREST"),
                _row("2025-01-22", "CASH", 0, -81.29, action="INTEREST")]
        a = self.write("a.json", rows)
        b = self.write("b.json", list(reversed(rows)))
        r = _tool("taxjson_diff", "--summary", a, b)
        self.assertIn("0 added | 0 removed | 0 modified | 2 unchanged",
                      r.stdout)

    def test_sub_micro_quantity_change_is_seen(self):
        a = self.write("a.json", [_row("2025-01-06", "BTC", 9.4e-7, 0.0,
                                       action="BUYSELL")])
        b = self.write("b.json", [_row("2025-01-06", "BTC", 5.6e-7, 0.0,
                                       action="BUYSELL")])
        r = _tool("taxjson_diff", "--summary", a, b)
        self.assertNotIn("0 added | 0 removed | 0 modified | 1 unchanged",
                         r.stdout)

    def test_output_does_not_depend_on_hash_seed(self):
        old = [_row("2025-01-06", s, 10, -100.0) for s in
               ("CCC", "DDD", "EEE", "FFF", "AAA", "BBB")]
        old += [_row("2025-02-03", "ZZZ.TO", q, -q * 1.0) for q in
                range(1, 6)]
        new = old[:6] + [_row("2025-02-03", "ZZZ.TO", q, -q * 1.0)
                         for q in range(3, 8)]
        a, b = self.write("a.json", old), self.write("b.json", new)
        outs = {_tool("taxjson_diff", "--show-unchanged", "--limit", "2",
                      a, b, seed=s).stdout for s in range(5)}
        self.assertEqual(len(outs), 1)

    def test_extractors_parses_argv(self):
        r = _tool("taxjson_extractors", "--bogus-flag")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(_tool("taxjson_extractors").returncode, 0)


if __name__ == "__main__":
    unittest.main()
