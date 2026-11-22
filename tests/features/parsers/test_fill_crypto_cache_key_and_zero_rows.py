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
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _write(tmp, name, rows, text=None):
    p = Path(tmp) / name
    p.write_text(text if text is not None
                 else json.dumps({'transactions': rows}), encoding='utf-8')
    return p


class TestD8FillCrypto(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD8FillCrypto"""
    def _run(self, rows, tmp, fetch):
        import taxjson.bin.fill_crypto_prices as fc
        from taxjson.lib import crypto_prices
        cache = Path(tmp) / 'cache.json'
        inp = _write(tmp, 'in.json', rows)
        saved = (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv,
                 dict(crypto_prices.PROJECT_CRYPTO_IDS), fc.time.sleep)
        crypto_prices.CACHE_FILE = str(cache)
        crypto_prices.get_crypto_price = fetch
        fc.time.sleep = lambda s: None
        # ZZQ's Yahoo id comes from the project's ticker.map CRYPTO
        # line (there is no built-in coin id table).
        (Path(tmp) / 'ticker.map').write_text('CRYPTO ZZQ ZZQ12345\n')
        sys.argv = ['fill-crypto', '--project-root', str(tmp), str(inp)]
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                fc.main()
        finally:
            (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv,
             overrides, fc.time.sleep) = saved
            crypto_prices.PROJECT_CRYPTO_IDS.clear()
            crypto_prices.PROJECT_CRYPTO_IDS.update(overrides)
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


if __name__ == '__main__':
    unittest.main()
