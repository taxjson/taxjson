"""Tests for `strip_json_comments` — the JSON-string-aware comment
stripper used by `load_transactions`. Earlier the loader did a naive
line-based strip (`if not line.strip().startswith('#')`); a `#` that
showed up at the start of a physical line inside a JSON value would
have corrupted the document. The new pass walks string literals first
so `#`-as-data is preserved while `#`-as-comment is dropped."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import load_transactions, strip_json_comments


class TestStripJsonComments(unittest.TestCase):
    def test_full_line_comment_removed(self):
        content = '{\n# header comment\n  "x": 1\n}'
        self.assertEqual(json.loads(strip_json_comments(content)), {"x": 1})

    def test_trailing_comment_removed(self):
        content = '{"x": 1}   # trailing\n'
        self.assertEqual(json.loads(strip_json_comments(content)), {"x": 1})

    def test_hash_inside_string_preserved(self):
        # The whole point: a `#` inside a JSON string value (e.g. a
        # broker's description "Settled #100 shares") must round-trip.
        content = '{"desc": "Settled #100 shares at #premium"}'
        out = json.loads(strip_json_comments(content))
        self.assertEqual(out["desc"], "Settled #100 shares at #premium")

    def test_load_transactions_preserves_hash_in_description(self):
        # Same case, but exercising the real `load_transactions` path.
        payload = {
            "transactions": [{
                "action": "BUYSELL", "date": "2025-01-15",
                "symbol": "AAPL.US", "quantity": 100,
                "price": 150.0, "net_amount": 15000.0,
                "currency": "USD", "account": "M",
                "description": "Lot #1 covered by #ASN",
            }],
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                          delete=False) as f:
            json.dump(payload, f)
            fname = f.name
        try:
            txs = load_transactions(Path(fname))
        finally:
            Path(fname).unlink()
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0].description, "Lot #1 covered by #ASN")


if __name__ == '__main__':
    unittest.main()
