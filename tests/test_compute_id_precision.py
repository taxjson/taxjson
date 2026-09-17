"""Tests for `TaxTransaction.compute_id()` precision.

The hash used to format floats with `f"{x:.8f}"`, which truncated
quantities below the 8-decimal level — two distinct sub-satoshi crypto
amounts collided onto the same ID and got silently deduplicated. The
fix uses `repr(float(x))`, which produces the shortest decimal string
that uniquely round-trips back to the same IEEE-754 bit pattern."""
import unittest

from taxjson.lib.core import TaxTransaction


class TestComputeIdPrecision(unittest.TestCase):
    def test_distinguishes_sub_satoshi_quantities(self):
        # ETH wei lives at 18 decimals; pick two values that round to
        # the same 8-decimal string but are distinct float64 values.
        a = TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='ETH', currency='USD',
                           quantity=0.000000001, price=3000.0,
                           net_amount=0.000003)
        b = TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='ETH', currency='USD',
                           quantity=0.000000002, price=3000.0,
                           net_amount=0.000006)
        self.assertNotEqual(a.id, b.id,
                            "Sub-8-decimal qty differences must yield "
                            "distinct dedup IDs (was a fixed-:.8f "
                            "rounding collision)")

    def test_distinguishes_sub_eighth_prices(self):
        # Same qty, slightly different price → must hash differently.
        a = TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='BTC', currency='USD',
                           quantity=0.1, price=60000.123456789,
                           net_amount=6000.0123456789)
        b = TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='BTC', currency='USD',
                           quantity=0.1, price=60000.123456788,
                           net_amount=6000.0123456788)
        self.assertNotEqual(a.id, b.id)

    def test_id_stable_for_identical_floats(self):
        # The ID must remain deterministic — repr is stable for the
        # same IEEE-754 value across runs and platforms.
        a = TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', currency='USD',
                           quantity=100.0, price=150.25,
                           net_amount=15025.0)
        b = TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', currency='USD',
                           quantity=100.0, price=150.25,
                           net_amount=15025.0)
        self.assertEqual(a.id, b.id)


if __name__ == '__main__':
    unittest.main()
