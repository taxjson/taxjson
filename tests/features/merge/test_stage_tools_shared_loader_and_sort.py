"""Regression pins for the stage-tools audit (merge2 / sort / validate /
ticker-map / convert-currency / fill-crypto / convert-tt).

One class per defect, numbered as in the audit:

  1. null numerics crashed every loader with a bare `float(None)`
     traceback while `taxjson-validate` said OK.
  2. wrong-typed but validate-clean rows (`"quantity": "10"`,
     `"symbol": 0`) crashed downstream tools; coerce_transaction_row is
     now the single type funnel.
  3. `taxjson-validate` itself crashed on wrong JSON types.
  4. merge2 / convert-currency bypassed the shared loader (no `#`
     comment stripping, no qty alias) and merge2 emitted an EMPTY merge
     at exit 0 on a commented input.
  5. ticker-map DELETE vs GLOBAL order differed between merge2 and the
     standalone tool.
  6. currency codes compared case/whitespace-sensitively, and a
     currency entirely absent from --rates converted at the implicit
     default with only a warning.
  7. rates-file parser skipped malformed lines silently and accepted
     NaN / negative / zero rates.
  8. fill-crypto cached on the raw symbol (stale after a ticker remap)
     and priced qty=0 rows at one unit of FMV.
  9. `taxjson-sort --dedup` (non-strict) silently DROPPED rows failing
     a private validator, with a stderr line the pipeline's diag
     marker regex ignored.
 10. `.tt` files had no split-fill disambiguation, so two identical
     hand-entered lines collapsed under --dedup.

Formerly: test_stage_tools_audit
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO

ROW = {
    'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
    'symbol': 'AAPL.US', 'quantity': 10, 'price': 15.0,
    'net_amount': 150.0, 'currency': 'USD', 'account': 'Margin',
}


def _run(module, *args, stdin=None):
    return subprocess.run(
        [sys.executable, '-m', module, *args], cwd=REPO_ROOT,
        capture_output=True, text=True, input=stdin,
        env={**os.environ, 'TAXJSON_OFFLINE': '1'},
    )


def _write(tmp, name, rows, text=None):
    p = Path(tmp) / name
    p.write_text(text if text is not None
                 else json.dumps({'transactions': rows}), encoding='utf-8')
    return p


class TestD3ValidateSurvivesWrongTypes(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD3ValidateSurvivesWrongTypes"""
    def test_wrong_types_reported_not_raised(self):
        from taxjson.bin.taxjson_validate import validate_transactions
        rows = [
            {**ROW, 'action': ['BUYSELL']},
            {**ROW, 'date': 20250115},
            {**ROW, 'time': 93000},
            {**ROW, 'symbol': 1.5},
            {**ROW, 'currency': 840},
            {**ROW, 'id': 7},
            'not a row',
        ]
        issues, _ = validate_transactions(rows)
        msgs = "\n".join(m for ms in issues.values() for m in ms)
        for fld in ('action', 'date', 'time', 'symbol', 'currency', 'id'):
            self.assertIn(f"Field '{fld}' must be a string", msgs)
        self.assertIn('not a JSON object', msgs)

    def test_string_numeric_is_an_error(self):
        from taxjson.bin.taxjson_validate import validate_transactions
        issues, _ = validate_transactions([{**ROW, 'quantity': '10'}])
        msgs = [m for ms in issues.values() for m in ms]
        self.assertTrue(any('quantity is a string' in m for m in msgs), msgs)


class TestD4SharedLoaderInStageTools(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD4SharedLoaderInStageTools"""
    COMMENTED = ('# hand-edited\n{"transactions": [\n'
                 '  {"action": "BUYSELL", "date": "2025-01-15", '
                 '"symbol": "AAPL.US", "qty": 10, "price": 15.0, '
                 '"net_amount": 150.0, "currency": "USD"}  # trailing\n]}\n')

    def test_merge2_reads_comments_and_qty_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'c.json', None, text=self.COMMENTED)
            r = _run('taxjson.bin.taxjson_merge2', str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)['transactions']
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['quantity'], 10)

    def test_merge2_unreadable_input_is_fatal_without_require_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = _write(tmp, 'g.json', [ROW])
            bad = _write(tmp, 'b.json', None, text='{ not json')
            r = _run('taxjson.bin.taxjson_merge2', str(good), str(bad))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), '')
        self.assertIn('refusing to emit', r.stderr)

    def test_merge2_malformed_row_is_fatal_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', [ROW, {**ROW, 'quantity': None}])
            r = _run('taxjson.bin.taxjson_merge2', str(p))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), '')
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('index 1', r.stderr)
        self.assertIn('quantity', r.stderr)

    def test_merge2_all_inputs_missing_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run('taxjson.bin.taxjson_merge2', str(Path(tmp) / 'x.json'))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), '')

    def test_convert_currency_reads_comments_and_qty_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'c.json', None, text=self.COMMENTED)
            r = _run('taxjson.bin.taxjson_convert_currency', str(p),
                     '--to', 'USD')
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)['transactions']
        self.assertEqual(out[0]['quantity'], 10)


class TestD5TickerMapOrder(unittest.TestCase):
    """DELETE names the broker's RAW symbol: drop first, then rename —
    in BOTH merge2 and the standalone tool.

    Formerly: test_stage_tools_audit.TestD5TickerMapOrder"""

    ROWS = [
        {**ROW, 'symbol': 'FOO.US', 'quantity': 1},
        {**ROW, 'symbol': 'BAR.US', 'quantity': 2},
    ]

    def _both(self, map_text):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', self.ROWS)
            m = Path(tmp) / 't.map'
            m.write_text(map_text)
            r1 = _run('taxjson.bin.taxjson_merge2', '--map', str(m), str(p))
            r2 = _run('taxjson.bin.taxjson_ticker_map', str(p), str(m))
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        key = lambda r: sorted((t['symbol'], t['quantity'])
                               for t in json.loads(r.stdout)['transactions'])
        return key(r1), key(r2)

    def test_delete_raw_symbol_drops_it_before_rename(self):
        m2, sa = self._both("GLOBAL FOO.US BAR.US\nDELETE FOO.US\n")
        self.assertEqual(m2, [('BAR.US', 2)])
        self.assertEqual(sa, [('BAR.US', 2)])

    def test_delete_post_rename_name_only_hits_the_raw_row(self):
        m2, sa = self._both("GLOBAL FOO.US BAR.US\nDELETE BAR.US\n")
        self.assertEqual(m2, [('BAR.US', 1)])   # FOO renamed; raw BAR gone
        self.assertEqual(sa, [('BAR.US', 1)])


class TestD9SortNeverDrops(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD9SortNeverDrops"""
    ROWS = [
        {**ROW, 'currency': 'USDC', 'symbol': 'ETH'},       # 4-char currency
        {'action': 'TAX', 'date': '2025-01-16', 'currency': 'USD',
         'net_amount': 1.0},                                 # no symbol
        {**ROW, 'date': '2025-01-17', 'currency': ''},       # schema error
    ]

    def test_non_strict_keeps_every_row_and_marks_diag(self):
        from taxjson.pipeline.diagnostics import _DIAG_MARKER_RE
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', self.ROWS)
            r = _run('taxjson.bin.taxjson_sort', '--dedup', str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)['transactions']), 3)
        lines = [l for l in r.stderr.splitlines() if l.strip()]
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(_DIAG_MARKER_RE.match(line.strip()), line)
        self.assertIn("missing required field 'currency'", r.stderr)

    def test_strict_aborts_on_schema_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', self.ROWS)
            r = _run('taxjson.bin.taxjson_sort', '--dedup', '--strict', str(p))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('taxjson-sort: error:', r.stderr)

    def test_clean_rows_with_usdc_or_no_symbol_are_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', self.ROWS[:2])
            r = _run('taxjson.bin.taxjson_sort', '--dedup', str(p))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), '')
        self.assertEqual(len(json.loads(r.stdout)['transactions']), 2)


if __name__ == '__main__':
    unittest.main()
