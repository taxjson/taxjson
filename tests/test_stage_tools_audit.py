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

from taxjson.lib.core import (
    TaxTransaction, coerce_transaction_row, load_transactions,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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
            r = _run('taxjson.bin.taxjson_gains', str(p))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('taxjson-gains: error', r.stderr)
        self.assertIn('net_amount', r.stderr)


class TestD2TypeFunnel(unittest.TestCase):
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

    def test_string_quantity_survives_phantom_walk(self):
        # phantom_holdings does `s['running'] += tx.quantity`; a str
        # quantity crashed it with a TypeError.
        from taxjson.lib.phantom_holdings import detect_phantoms
        txs = [coerce_transaction_row({**ROW, 'quantity': '-10'}, 0, 't')]
        cands = detect_phantoms(txs)   # must not raise
        self.assertEqual(len(cands), 1)


class TestD3ValidateSurvivesWrongTypes(unittest.TestCase):
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
    in BOTH merge2 and the standalone tool."""

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


class TestD6CurrencyNormalization(unittest.TestCase):
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
            self.assertIn('no rates at all for EUR', r.stderr)
            r2 = _run('taxjson.bin.taxjson_convert_currency', str(p),
                      '--to', 'CAD', '--rates', str(rates),
                      '--default-rate', '1.5')
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertAlmostEqual(
                json.loads(r2.stdout)['transactions'][0]['net_amount'], 225.0)
            m = _run('taxjson.bin.taxjson_merge2', '--to', 'CAD',
                     '--rates', str(rates), str(p))
            self.assertNotEqual(m.returncode, 0)
            self.assertIn('no rates at all for EUR', m.stderr)


class TestD7RatesFileParser(unittest.TestCase):
    def _load(self, text, **kw):
        from taxjson.bin.taxjson_convert_currency import load_exchange_rates
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


class TestD8FillCrypto(unittest.TestCase):
    def _run(self, rows, tmp, fetch):
        import taxjson.bin.fill_crypto_prices as fc
        cache = Path(tmp) / 'cache.json'
        inp = _write(tmp, 'in.json', rows)
        saved = (fc.CACHE_FILE, fc.get_crypto_price, sys.argv,
                 dict(fc.SYMBOL_OVERRIDES), fc.time.sleep)
        fc.CACHE_FILE = str(cache)
        fc.get_crypto_price = fetch
        fc.time.sleep = lambda s: None
        fc.SYMBOL_OVERRIDES['ZZQ'] = 'ZZQ12345'
        sys.argv = ['fill-crypto', str(inp)]
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                fc.main()
        finally:
            (fc.CACHE_FILE, fc.get_crypto_price, sys.argv,
             overrides, fc.time.sleep) = saved
            fc.SYMBOL_OVERRIDES.clear()
            fc.SYMBOL_OVERRIDES.update(overrides)
        return json.loads(out.getvalue())['transactions'], cache, err.getvalue()

    def test_cache_keyed_on_resolved_yahoo_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{'action': 'BUYSELL', 'date': '2026-01-02', 'symbol': 'ZZQ',
                     'quantity': 1.0, 'price': 0.0, 'net_amount': 0.0,
                     'currency': 'USD'}]
            out, cache, _ = self._run(rows, tmp, lambda s, d: 400.0)
            keys = list(json.loads(cache.read_text()))
            self.assertEqual(keys, ['ZZQ12345-2026-01-02'])
            self.assertEqual(out[0]['price'], 400.0)

    def test_qty_zero_row_left_untouched_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{'action': 'BUYSELL', 'date': '2026-01-02', 'symbol': 'ZZQ',
                     'quantity': 0.0, 'price': 0.0, 'net_amount': 0.0,
                     'currency': 'USD'}]
            out, cache, err = self._run(rows, tmp, lambda s, d: 400.0)
            self.assertEqual(out[0]['price'], 0.0)
            self.assertEqual(out[0]['net_amount'], 0.0)
            self.assertFalse(cache.exists())
            self.assertIn('taxjson-fill-crypto: warning:', err)
            self.assertIn('quantity 0', err)


class TestD9SortNeverDrops(unittest.TestCase):
    ROWS = [
        {**ROW, 'currency': 'USDC', 'symbol': 'ETH'},       # 4-char currency
        {'action': 'TAX', 'date': '2025-01-16', 'currency': 'USD',
         'net_amount': 1.0},                                 # no symbol
        {**ROW, 'date': '2025-01-17', 'currency': ''},       # schema error
    ]

    def test_non_strict_keeps_every_row_and_marks_diag(self):
        from taxjson.bin.taxjson_run import _DIAG_MARKER_RE
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


class TestD10TtSplitFills(unittest.TestCase):
    LINE = "BUYSELL 2025-01-15 09:30:00 AAPL.US 100 USD 150.00 15000.00 5.00"

    def test_identical_tt_lines_get_distinct_ids(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json, parse_tt_line
        from taxjson.bin.taxjson_sort import deduplicate
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.tt'
            p.write_text(self.LINE + "\n" + self.LINE + "\n")
            txs = tt_to_json(p, 'Margin')['transactions']
        self.assertEqual(len(txs), 2)
        self.assertNotEqual(txs[0]['id'], txs[1]['id'])
        # First keeps its stable id; second carries the fill marker and
        # its id agrees with a JSON-native load of the same content.
        self.assertEqual(txs[0]['id'], parse_tt_line(self.LINE, 'Margin')['id'])
        self.assertIn('[fill #2]', txs[1]['description'])
        self.assertEqual(txs[1]['id'], TaxTransaction(
            **{k: v for k, v in txs[1].items() if k != 'id'}).id)
        self.assertEqual(len(deduplicate([TaxTransaction(**t) for t in txs])), 2)

    def test_declared_transfers_are_not_marked(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        from taxjson.lib.pipeline import MANUAL_TRANSFER_DECLARATION
        line = "TRANSFER 2025-01-20 09:30:00 AAPL.US -100 USD 150.00 15000.00 DECLARED"
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.tt'
            p.write_text(line + "\n" + line + "\n")
            txs = tt_to_json(p, 'Margin')['transactions']
        for t in txs:
            self.assertEqual(t['description'], MANUAL_TRANSFER_DECLARATION)


if __name__ == '__main__':
    unittest.main()
