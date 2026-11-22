"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestCryptoTickerMap(unittest.TestCase):
    """KNOWN_ISSUES "SYMBOL_OVERRIDES is hardcoded": the user-editable
    ticker.map CRYPTO lines (once crypto_ticker.map) are the ONLY Yahoo
    coin ids — the built-in table was removed.

    Formerly: test_audit_2026_08_fixes.TestCryptoTickerMap"""

    def test_map_file_is_the_only_source(self):
        from taxjson.lib.crypto_prices import load_symbol_overrides
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ticker.map").write_text(
                "# my colliding coins\n"
                "CRYPTO QZP QZP55503   # collision with the other QZP\n"
                "CRYPTO QZT  QZT55504-USD   # Yahoo's full pair\n"
                "CRYPTO garbage line without two fields extra\n")
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                merged = load_symbol_overrides([tmp])
        # Only the map's lines; a pasted `-USD` suffix is dropped (the
        # lookups append it).
        self.assertEqual(merged, {"QZP": "QZP55503", "QZT": "QZT55504"})
        self.assertIn("CRYPTO needs", buf.getvalue())   # malformed warned


class TestTickerMapSymbolNew(unittest.TestCase):
    """Deep-audit 2026-08 #10: the ticker map never remapped
    symbol_new, so a merger SPLIT renamed the source pool into an
    ORPHAN identity while the acquirer's trades mapped elsewhere —
    phantom short plus stranded ACB.

    Formerly: test_audit_2026_08_fixes.TestTickerMapSymbolNew"""

    @rule(R.CA_ACB_04)
    @rule(R.US_BASIS_06)
    def test_split_target_follows_the_mapping(self):
        from taxjson.bin.taxjson_ticker_map import apply_mapping
        tx = TaxTransaction(action='SPLIT', date='2025-06-01',
                            symbol='HES.US', symbol_new='CVX.US',
                            quantity=1.0, currency='USD', account='M')
        apply_mapping(tx, {'CVX.US': 'CVX.TO', 'HES.US': 'HES.TO'})
        self.assertEqual(tx.symbol, 'HES.TO')
        self.assertEqual(
            tx.symbol_new, 'CVX.TO',
            "symbol_new kept the unmapped identity: the renamed pool "
            "orphans while CVX.TO sales find no basis")

    def test_drop_removes_renames_targeting_dropped_ticker(self):
        import io
        from contextlib import redirect_stderr
        from taxjson.bin.taxjson_ticker_map import apply_drops
        txs = [
            TaxTransaction(action='SPLIT', date='2025-06-01',
                           symbol='KEEP.TO', symbol_new='GONE.TO',
                           quantity=1.0, currency='CAD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-06-02',
                           symbol='KEEP.TO', quantity=10.0, price=1.0,
                           net_amount=10.0, currency='CAD', account='M'),
        ]
        with redirect_stderr(io.StringIO()):
            kept = apply_drops(txs, {'GONE.TO'})
        self.assertEqual([t.action for t in kept], ['BUYSELL'])


if __name__ == '__main__':
    unittest.main()
