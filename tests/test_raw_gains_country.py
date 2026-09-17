"""The wrapper's raw-holdings stage now passes `--country country` to
`taxjson-gains`. Without it, `taxjson-gains` defaults to Canada and a
US user's `_holdings.toml` ends up with averaged ACB cost basis
instead of FIFO-remaining lots.

This test pins the engine-level difference between the two countries
on a multi-lot scenario — confirming there IS a divergence that the
`--country` plumbing protects against. The wrapper-plumbing side is
covered by the live invocation in `taxjson_run.py:stage_account`."""
import unittest

from taxjson.lib.core import (
    CanadaTaxRules, USATaxRules, TaxTransaction, get_tax_rules,
)


class TestEngineInventoryDiffersByCountry(unittest.TestCase):
    """If both engines produced the same per-symbol inventory total,
    the `--country` flag wouldn't matter for the holdings handoff —
    but they DON'T, so it does."""

    def _two_lots_then_partial_sell(self):
        # 100 @ $10 + 100 @ $20 = 200 shares at $3000 total.
        # Sell 100 @ $50.
        #   Canada ACB: avg cost $15 → SELL closes 100 at $1500 →
        #     remaining pool: 100 shares, total_cost $1500.
        #   US FIFO: SELL closes lot 1 ($1000) → remaining: lot 2
        #     intact, 100 shares, total_cost $2000.
        # Different inventory total_cost → why the --country flag
        # matters for the raw-holdings handoff.
        return [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-15',
                           symbol='AAPL', quantity=100.0, price=20.0,
                           net_amount=2000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-06-15',
                           symbol='AAPL', quantity=-100.0, price=50.0,
                           net_amount=5000.0, currency='USD', account='M'),
        ]

    def test_canada_acb_averages_remaining_basis(self):
        result = CanadaTaxRules().compute_gains(self._two_lots_then_partial_sell())
        aapl = next(h for h in result['inventory'] if h['symbol'] == 'AAPL')
        self.assertEqual(aapl['qty'], 100.0)
        # Avg $15/share × 100 remaining = $1500.
        self.assertAlmostEqual(aapl['total_cost'], 1500.0, places=2)

    def test_usa_fifo_preserves_remaining_lot_cost(self):
        result = USATaxRules().compute_gains(self._two_lots_then_partial_sell())
        aapl = next(h for h in result['inventory'] if h['symbol'] == 'AAPL')
        self.assertEqual(aapl['qty'], 100.0)
        # Lot 1 was closed; lot 2 remains intact at $20 × 100 = $2000.
        self.assertAlmostEqual(aapl['total_cost'], 2000.0, places=2)

    def test_country_selector_returns_distinct_engines(self):
        ca = get_tax_rules('ca')
        us = get_tax_rules('us')
        self.assertIsInstance(ca, CanadaTaxRules)
        self.assertIsInstance(us, USATaxRules)


if __name__ == '__main__':
    unittest.main()
