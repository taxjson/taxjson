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

from taxjson.lib.tx_load import coerce_transaction_row, load_transactions
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


class TestD1NullNumerics(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD1NullNumerics"""
    def test_loader_refuses_null_numeric_naming_the_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', [{**ROW, 'net_amount': None}])
            with self.assertRaises(ValueError) as cm:
                load_transactions(p)
        self.assertIn('net_amount', str(cm.exception))
        self.assertIn('index 0', str(cm.exception))

    def test_validate_flags_null_numeric(self):
        from taxjson.bin.taxjson_validate import validate_transactions
        issues, _ = validate_transactions([{**ROW, 'net_amount': None}])
        msgs = [m for ms in issues.values() for m in ms]
        self.assertTrue(any('Null net_amount' in m for m in msgs), msgs)

    def test_gains_cli_reports_cleanly_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', [{**ROW, 'net_amount': None}])
            r = _run('taxjson.bin.taxjson_gains', '--country', 'canada',
                     str(p))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('taxjson-gains: error', r.stderr)
        self.assertIn('net_amount', r.stderr)


class TestD2TypeFunnel(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD2TypeFunnel"""
    def test_numeric_string_coerced_to_float(self):
        tx = coerce_transaction_row({**ROW, 'quantity': '10',
                                     'price': '1.5'}, 0, 't')
        self.assertIsInstance(tx.quantity, float)
        self.assertEqual(tx.quantity, 10.0)
        self.assertEqual(tx.price, 1.5)

    def test_non_numeric_string_refused(self):
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row({**ROW, 'gross_amount': 'abc'}, 3, 't')
        self.assertIn('gross_amount', str(cm.exception))
        self.assertIn('index 3', str(cm.exception))

    def test_bool_and_list_numerics_refused(self):
        for bad in (True, [1], {}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                coerce_transaction_row({**ROW, 'quantity': bad}, 0, 't')

    def test_non_string_symbol_and_account_refused(self):
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row({**ROW, 'symbol': 0}, 0, 't')
        self.assertIn('symbol', str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row({**ROW, 'account': []}, 0, 't')
        self.assertIn('account', str(cm.exception))

    def test_null_optional_string_falls_back_to_default(self):
        tx = coerce_transaction_row({**ROW, 'time': None,
                                     'description': None}, 0, 't')
        self.assertEqual(tx.time, '09:30:00')
        self.assertEqual(tx.description, '')

    def test_null_or_missing_required_string_refused(self):
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row({**ROW, 'date': None}, 0, 't')
        self.assertIn('date', str(cm.exception))
        row = dict(ROW)
        del row['action']
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row(row, 0, 't')
        self.assertIn('action', str(cm.exception))

    def test_string_quantity_survives_missing_history_walk(self):
        # phantom_holdings does `s['running'] += tx.quantity`; a str
        # quantity crashed it with a TypeError.
        from taxjson.lib.missing_history import detect_missing_history
        txs = [coerce_transaction_row({**ROW, 'quantity': '-10'}, 0, 't')]
        cands = detect_missing_history(txs)   # must not raise
        self.assertEqual(len(cands), 1)


if __name__ == '__main__':
    unittest.main()
