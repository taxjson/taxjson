"""`load_transactions` refuses to silently drop malformed rows.

The historical behavior was to `continue` on any row that couldn't be
coerced to a dict via the mapping protocol. That's dangerous in a
context where one missing buy/sell silently invalidates the entire
ACB pool downstream — the user would see a wrong gain number with no
trace back to a malformed-row warning. The current behavior raises so
the failure is visible at ingest time, not at filing time."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import load_transactions


class TestLoadTransactionsStrict(unittest.TestCase):
    def test_non_dict_entry_raises(self):
        # A bare string in the transactions array would previously be
        # silently dropped — the dict() coercion fails, the loader
        # continued.
        payload = {
            "transactions": [
                {"action": "BUYSELL", "date": "2025-01-15",
                 "symbol": "AAPL.US", "quantity": 100,
                 "currency": "USD"},
                "this row is malformed",
            ],
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                          delete=False) as f:
            json.dump(payload, f)
            fname = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                load_transactions(Path(fname))
            msg = str(cm.exception)
            self.assertIn('index 1', msg)
            self.assertIn('malformed', msg)
        finally:
            Path(fname).unlink()

    def test_integer_entry_raises(self):
        payload = {"transactions": [42]}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                          delete=False) as f:
            json.dump(payload, f)
            fname = f.name
        try:
            with self.assertRaises(ValueError):
                load_transactions(Path(fname))
        finally:
            Path(fname).unlink()

    def test_clean_dict_entries_still_parse(self):
        # Sanity: the strict check doesn't break normal input.
        payload = {
            "transactions": [
                {"action": "BUYSELL", "date": "2025-01-15",
                 "symbol": "AAPL.US", "quantity": 100,
                 "price": 150.0, "net_amount": 15000.0,
                 "currency": "USD", "account": "M"},
                {"action": "BUYSELL", "date": "2025-02-15",
                 "symbol": "AAPL.US", "quantity": -100,
                 "price": 200.0, "net_amount": 20000.0,
                 "currency": "USD", "account": "M"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                          delete=False) as f:
            json.dump(payload, f)
            fname = f.name
        try:
            txs = load_transactions(Path(fname))
            self.assertEqual(len(txs), 2)
            self.assertEqual(txs[0].action, 'BUYSELL')
        finally:
            Path(fname).unlink()


if __name__ == '__main__':
    unittest.main()
