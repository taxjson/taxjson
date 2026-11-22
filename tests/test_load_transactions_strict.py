"""`load_transactions` refuses to silently drop malformed rows.

The historical behavior was to `continue` on any row that couldn't be
coerced to a dict via the mapping protocol. That's dangerous in a
context where one missing buy/sell silently invalidates the entire
ACB pool downstream — the user would see a wrong gain number with no
trace back to a malformed-row warning. The current behavior raises so
the failure is visible at ingest time, not at filing time."""
import contextlib
import io
import json
from _tmpfiles import private_tmpfile
import unittest
from decimal import Decimal
from pathlib import Path

from taxjson.engine.by_country import get_tax_rules
from taxjson.lib.tx_load import coerce_transaction_row, load_transactions


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
        with private_tmpfile(mode='w', suffix='.json',
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
        with private_tmpfile(mode='w', suffix='.json',
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
        with private_tmpfile(mode='w', suffix='.json',
                                          delete=False) as f:
            json.dump(payload, f)
            fname = f.name
        try:
            txs = load_transactions(Path(fname))
            self.assertEqual(len(txs), 2)
            self.assertEqual(txs[0].action, 'BUYSELL')
        finally:
            Path(fname).unlink()


class TestADecimalNumberIsAFloat(unittest.TestCase):
    """A row's numbers stay float, an int where whole (`compute_id`
    hashes their repr; the engines read them exactly): a Decimal one is
    turned into its float, and a split inside its trade's settle lag
    re-denominates a Decimal price (TJ-3312)."""

    BUY = {"action": "BUYSELL", "date": "2025-06-09", "time": "10:30:00",
           "date_settle": "2025-06-11", "symbol": "ZZQ.TO", "quantity": 10,
           "price": Decimal("10"), "net_amount": -100.0, "currency": "CAD",
           "account": "a"}
    SPLIT = {"action": "SPLIT", "date": "2025-06-10", "date_settle": "2025-06-10",
             "time": "20:25:00", "symbol": "ZZQ.TO", "quantity": 2.0,
             "net_amount": 0.0, "currency": "CAD", "account": "a"}

    def test_the_row_holds_floats(self):
        t = coerce_transaction_row(dict(self.BUY), 0, "t")
        self.assertIs(type(t.price), int)
        self.assertEqual(t.price, 10)
        t = coerce_transaction_row({**self.BUY, "price": Decimal("2.50"),
                                    "net_amount": Decimal("-25.0")}, 0, "t")
        self.assertEqual((t.price, t.net_amount), (2.5, -25.0))
        self.assertIs(type(t.net_amount), float)
        t = coerce_transaction_row({**self.BUY, "price": Decimal("-0")}, 0, "t")
        self.assertEqual(repr(t.price), "-0.0")          # TJ-3319
        for bad, said in ((Decimal("NaN"), "nan"), (Decimal("sNaN"), "nan"),
                          (Decimal("1e400"), "inf"), (Decimal("Infinity"), "inf"),
                          (Decimal("-Infinity"), "-inf"),  # TJ-3317
                          (Decimal("1" + "0" * 400), "inf")):  # TJ-3318
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                coerce_transaction_row({**self.BUY, "price": bad}, 0, "t")
            self.assertIn(f"non-finite price={said} ", str(cm.exception))

    def test_a_split_in_the_settle_lag(self):
        rows = [coerce_transaction_row(r, i, "t") for i, r in enumerate((self.BUY, self.SPLIT))]
        with contextlib.redirect_stderr(io.StringIO()):
            res = get_tax_rules("canada").compute_gains(rows)
        self.assertEqual([(p["qty"], p["total_cost"]) for p in res["inventory"]], [(20.0, 100.0)])


if __name__ == '__main__':
    unittest.main()
