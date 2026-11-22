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
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from taxjson.model.transaction import TaxTransaction
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


class TestD6CurrencyNormalization(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD6CurrencyNormalization"""
    def test_target_currency_with_case_or_whitespace_is_left_alone(self):
        from taxjson.bin.taxjson_convert_currency import (
            convert_transaction, reset_fallback_tally,
            _DEFAULT_RATE_FALLBACKS)
        hist = {'USD': {'2025-01-15': Decimal('1.40')}}
        reset_fallback_tally()
        for cur in ('cad ', 'Cad', ' CAD'):
            tx = TaxTransaction(**{**ROW, 'currency': cur, 'symbol': 'A.TO'})
            cv = convert_transaction(tx, 'CAD', hist, Decimal('1.35'))
            self.assertEqual(cv.net_amount, 150.0, cur)
            self.assertEqual(cv.currency, 'CAD', cur)
        self.assertEqual(_DEFAULT_RATE_FALLBACKS, {})

    def test_lowercase_source_currency_uses_its_rates(self):
        from taxjson.bin.taxjson_convert_currency import convert_transaction
        hist = {'USD': {'2025-01-15': Decimal('1.40')}}
        tx = TaxTransaction(**{**ROW, 'currency': 'usd'})
        cv = convert_transaction(tx, 'cad', hist, Decimal('1.35'))
        self.assertAlmostEqual(cv.net_amount, 210.0)
        self.assertEqual(cv.currency, 'CAD')

    def test_uncovered_currency_fatal_unless_default_rate_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(tmp, 'a.json', [{**ROW, 'currency': 'EUR'}])
            rates = Path(tmp) / 'r.csv'
            rates.write_text('2025-01-15 12:00:00 USD CAD 1.40\n')
            r = _run('taxjson.bin.taxjson_convert_currency', str(p),
                     '--to', 'CAD', '--rates', str(rates))
            self.assertNotEqual(r.returncode, 0)
            # No built-in rate (owner, 2026-10-04): the row's pair and
            # date are named.
            self.assertIn('EUR->CAD on', r.stderr)
            self.assertIn('no rates for currency', r.stderr)
            r2 = _run('taxjson.bin.taxjson_convert_currency', str(p),
                      '--to', 'CAD', '--rates', str(rates),
                      '--default-rate', '1.5')
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertAlmostEqual(
                json.loads(r2.stdout)['transactions'][0]['net_amount'], 225.0)
            m = _run('taxjson.bin.taxjson_merge2', '--to', 'CAD',
                     '--rates', str(rates), str(p))
            self.assertNotEqual(m.returncode, 0)
            self.assertIn('EUR->CAD on', m.stderr)


class TestD7RatesFileParser(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD7RatesFileParser"""
    def _load(self, text, **kw):
        from taxjson.lib.exchange_rates import load_exchange_rates
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'r.csv'
            p.write_text(text)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                hist = load_exchange_rates(p, target_curr='CAD', **kw)
        return hist, err.getvalue()

    def test_malformed_lines_counted_and_reported(self):
        hist, err = self._load(
            "2025-01-02 12:00:00 USD CAD 1.44\n"
            "2025-01-06 USD CAD 1.46\n"              # no TIME column
            "2025-01-07,12:00:00,USD,CAD,1.48\n"    # comma-separated
            "2025/01/12 12:00:00 USD CAD 1.50\n"    # slash date
            "# a comment line with five tokens\n"
            "\n")
        self.assertEqual(hist, {'USD': {'2025-01-02': Decimal('1.44')}})
        self.assertIn('skipped 3 malformed FX rate line(s)', err)

    def test_currency_columns_uppercased(self):
        hist, _ = self._load("2025-01-13 12:00:00 usd cad 1.51\n")
        self.assertEqual(hist, {'USD': {'2025-01-13': Decimal('1.51')}})

    def test_non_finite_or_non_positive_rate_is_fatal(self):
        for bad in ('NaN', 'Infinity', '-1.5', '0'):
            with self.assertRaises(ValueError, msg=bad) as cm:
                self._load(f"2025-01-08 12:00:00 USD CAD {bad}\n")
            self.assertIn('line 1', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
