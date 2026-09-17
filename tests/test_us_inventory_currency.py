"""Tests for currency on US inventory emission.

Mirrors the Canadian engine's per-symbol currency tracking: every
inventory entry carries a 'currency' field so taxjson-export --report
can show it for US workflows too. A currency mismatch on the same
symbol is rejected with a clear error.
"""
import unittest

from taxjson.lib.core import TaxTransaction, USATaxRules


def _tx(action, date, symbol, qty, price=0.0, net=0.0, currency='USD', account='Margin'):
    return TaxTransaction(
        action=action, date=date, symbol=symbol, quantity=qty,
        price=price, net_amount=net or abs(qty * price),
        currency=currency, account=account,
    )


class TestUSInventoryCurrency(unittest.TestCase):
    def test_inventory_carries_currency(self):
        txs = [
            _tx('BUYSELL', '2024-01-15', 'AAPL', 100, 150.0, 15009, currency='USD'),
        ]
        result = USATaxRules().compute_gains(txs, detect_wash_sales=False)
        self.assertEqual(len(result['inventory']), 1)
        self.assertEqual(result['inventory'][0]['symbol'], 'AAPL')
        self.assertEqual(result['inventory'][0]['currency'], 'USD')

    def test_short_inventory_carries_currency(self):
        txs = [
            _tx('BUYSELL', '2024-06-15', 'NVDA', -50, 1000.0, 49998, currency='USD'),
        ]
        result = USATaxRules().compute_gains(txs, detect_wash_sales=False)
        short_entries = [i for i in result['inventory'] if i['qty'] < 0]
        self.assertEqual(len(short_entries), 1)
        self.assertEqual(short_entries[0]['currency'], 'USD')

    def test_multiple_symbols_different_currencies(self):
        """A US-domiciled user with foreign holdings still works."""
        txs = [
            _tx('BUYSELL', '2024-01-15', 'AAPL', 100, 150.0, 15009, currency='USD'),
            _tx('BUYSELL', '2024-02-10', 'SHOP', 50, 100.0, 5005, currency='CAD'),
        ]
        result = USATaxRules().compute_gains(txs, detect_wash_sales=False)
        by_sym = {i['symbol']: i['currency'] for i in result['inventory']}
        self.assertEqual(by_sym, {'AAPL': 'USD', 'SHOP': 'CAD'})

    def test_currency_mismatch_raises(self):
        """Same symbol arriving in two currencies should error — same guard
        the Canadian engine has."""
        txs = [
            _tx('BUYSELL', '2024-01-15', 'AAPL', 100, 150.0, 15009, currency='USD'),
            _tx('BUYSELL', '2024-03-20', 'AAPL', -50, 180.0, 8991, currency='CAD'),
        ]
        with self.assertRaises(ValueError) as cm:
            USATaxRules().compute_gains(txs, detect_wash_sales=False)
        msg = str(cm.exception)
        self.assertIn('Currency mismatch', msg)
        self.assertIn('AAPL', msg)
        self.assertIn('taxjson_convert_currency', msg)

    def test_missing_currency_tolerated(self):
        """If a transaction has no currency (legacy data), the inventory
        emits '' rather than crashing — taxjson-export auto-hides the column."""
        txs = [
            _tx('BUYSELL', '2024-01-15', 'AAPL', 100, 150.0, 15009, currency=''),
        ]
        result = USATaxRules().compute_gains(txs, detect_wash_sales=False)
        self.assertEqual(result['inventory'][0]['currency'], '')

    def test_dividend_currency_does_not_pollute_symbol(self):
        """A DIVIDEND transaction's currency shouldn't be used to determine
        the symbol's pool currency (per the existing skip rule)."""
        txs = [
            _tx('BUYSELL', '2024-01-15', 'AAPL', 100, 150.0, 15009, currency='USD'),
            # Implausible: a dividend in CAD on a USD-denominated AAPL.
            # The dividend row is skipped from currency-tracking by design.
            _tx('DIVIDEND', '2024-03-15', 'AAPL', 0, 0, 25, currency='CAD'),
        ]
        result = USATaxRules().compute_gains(txs, detect_wash_sales=False)
        self.assertEqual(result['inventory'][0]['currency'], 'USD')


if __name__ == '__main__':
    unittest.main()
