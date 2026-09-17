"""Tests that the gain calculation produces the correct base-currency number
when the full pipeline (currency conversion → gains) is exercised.

The point is *not* just that the engine works with any currency label — it's
that converting raw USD trades to a CAD base (or vice versa) must produce
a gain that reflects the FX rate AT EACH transaction date, not the buy-day
rate applied to the USD gain.

Example: buy 100 AAPL @ $50 USD on 2025-01-15 (USDCAD = 1.30), sell @ $60 USD
on 2025-06-20 (USDCAD = 1.40).
    USD-base gain = (5999 - 5001) = $998 USD
    CAD-base gain = (5999 × 1.40) - (5001 × 1.30) = 8398.60 - 6501.30 = $1,897.30 CAD
The CAD number is what CRA wants on a Schedule 3 line for a Canadian resident.
$998 × any single rate would give the wrong answer.
"""

import unittest
from decimal import Decimal

from taxjson.bin.taxjson_convert_currency import process_transactions
from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


# FX rates calibrated so the swing between buy and sell dates is large enough
# that no single-rate shortcut would give the same answer.
#  - 2025-01-15: USDCAD = 1.30  (CADUSD ≈ 0.7692)
#  - 2025-06-20: USDCAD = 1.40  (CADUSD ≈ 0.7143)
RATE_HISTORY = {
    'USD': {
        '2025-01-15': Decimal('1.30'),
        '2025-06-20': Decimal('1.40'),
    },
    'CAD': {
        '2025-01-15': Decimal('0.769231'),  # 1/1.30
        '2025-06-20': Decimal('0.714286'),  # 1/1.40
    },
}
DEFAULT_RATE = Decimal('1.0')


def _convert(txs, target_curr):
    return process_transactions(txs, target_curr, RATE_HISTORY, DEFAULT_RATE)


class TestAmericanStockBaseCurrency(unittest.TestCase):
    """US stock bought and sold in USD, run through both bases."""

    def setUp(self):
        # Raw native-USD trades. 100 AAPL @ $50 buy, @ $60 sell, $1 fee each side.
        # Buy cost basis (net) = $5,001 USD; sell proceeds (net) = $5,999 USD.
        self.usd_txs = [
            TaxTransaction(
                action='BUYSELL', date='2025-01-15', date_settle='2025-01-15',
                symbol='AAPL.US', quantity=100.0, price=50.0,
                commission=1.00, net_amount=5001.00,
                currency='USD', account='Margin',
            ),
            TaxTransaction(
                action='BUYSELL', date='2025-06-20', date_settle='2025-06-20',
                symbol='AAPL.US', quantity=-100.0, price=60.0,
                commission=1.00, net_amount=5999.00,
                currency='USD', account='Margin',
            ),
        ]

    def test_usd_base_native(self):
        """Gain expressed in USD = 5999 - 5001 = $998."""
        rules = USATaxRules()
        txs = _convert(self.usd_txs, target_curr='USD')  # no-op
        result = rules.compute_gains(txs)

        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 998.00, places=2)
        self.assertEqual(gains[0]['currency'], 'USD')

    def test_cad_base_converted(self):
        """Gain in CAD reflects the FX swing — $1,897.30 CAD, not 998 × any rate.

        cost  = 5001 × 1.30 = 6501.30 CAD
        proc  = 5999 × 1.40 = 8398.60 CAD
        gain  = 8398.60 - 6501.30 = 1897.30 CAD
        """
        rules = CanadaTaxRules()
        txs = _convert(self.usd_txs, target_curr='CAD')
        result = rules.compute_gains(txs)

        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 1897.30, places=2)
        self.assertEqual(gains[0]['currency'], 'CAD')
        self.assertAlmostEqual(gains[0]['cost'], 6501.30, places=2)
        self.assertAlmostEqual(gains[0]['proceeds'], 8398.60, places=2)

    def test_cad_and_usd_gains_diverge(self):
        """The CAD-base gain must NOT equal the USD-base gain × any single rate.

        $998 USD × 1.30 = $1,297.40 (wrong — uses only the buy-day rate)
        $998 USD × 1.40 = $1,397.20 (wrong — uses only the sell-day rate)
        $998 USD × 1.35 = $1,347.30 (wrong — uses a mid-year rate)
        Correct answer = $1,897.30 because each leg is converted at ITS OWN date.
        """
        usd_result = USATaxRules().compute_gains(_convert(self.usd_txs, 'USD'))
        cad_result = CanadaTaxRules().compute_gains(_convert(self.usd_txs, 'CAD'))

        usd_gain = usd_result['summary']['total_gain']
        cad_gain = cad_result['summary']['total_gain']

        # The naive shortcuts should all be wrong.
        for naive_rate in (1.30, 1.35, 1.40):
            self.assertNotAlmostEqual(
                cad_gain, usd_gain * naive_rate, places=2,
                msg=f"CAD gain matches naive USD×{naive_rate} — FX-at-each-date logic is broken.",
            )
        # And the actual answer must be the per-date result.
        self.assertAlmostEqual(usd_gain, 998.00, places=2)
        self.assertAlmostEqual(cad_gain, 1897.30, places=2)


class TestCanadianStockBaseCurrency(unittest.TestCase):
    """Canadian stock (SHOP.TO) bought and sold in CAD, run through both bases."""

    def setUp(self):
        # Raw native-CAD trades: 100 SHOP @ $50 CAD buy, @ $60 CAD sell, $1 fee each side.
        self.cad_txs = [
            TaxTransaction(
                action='BUYSELL', date='2025-01-15', date_settle='2025-01-15',
                symbol='SHOP.TO', quantity=100.0, price=50.0,
                commission=1.00, net_amount=5001.00,
                currency='CAD', account='Margin',
            ),
            TaxTransaction(
                action='BUYSELL', date='2025-06-20', date_settle='2025-06-20',
                symbol='SHOP.TO', quantity=-100.0, price=60.0,
                commission=1.00, net_amount=5999.00,
                currency='CAD', account='Margin',
            ),
        ]

    def test_cad_base_native(self):
        """Gain in native CAD = $998."""
        rules = CanadaTaxRules()
        txs = _convert(self.cad_txs, target_curr='CAD')  # no-op
        result = rules.compute_gains(txs)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertAlmostEqual(gains[0]['gain'], 998.00, places=2)
        self.assertEqual(gains[0]['currency'], 'CAD')

    def test_usd_base_converted(self):
        """Gain in USD reflects FX swing.

        cost  = 5001 × 0.769231 = 3,847.6927 USD
        proc  = 5999 × 0.714286 = 4,285.7689 USD
        gain  = 4,285.7689 - 3,847.6927 = 438.0762 USD
        """
        rules = USATaxRules()
        txs = _convert(self.cad_txs, target_curr='USD')
        result = rules.compute_gains(txs)

        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        self.assertAlmostEqual(gains[0]['gain'], 438.08, places=2)
        self.assertEqual(gains[0]['currency'], 'USD')

    def test_cad_and_usd_gains_diverge(self):
        """Same swing-direction check on a CAD-native stock converted to USD."""
        cad_result = CanadaTaxRules().compute_gains(_convert(self.cad_txs, 'CAD'))
        usd_result = USATaxRules().compute_gains(_convert(self.cad_txs, 'USD'))
        cad_gain = cad_result['summary']['total_gain']
        usd_gain = usd_result['summary']['total_gain']

        for naive_rate in (0.769231, 0.714286, 0.74):
            self.assertNotAlmostEqual(
                usd_gain, cad_gain * naive_rate, places=2,
                msg=f"USD gain matches naive CAD×{naive_rate} — per-date FX logic is broken.",
            )


class TestWashSaleAcrossBases(unittest.TestCase):
    """Wash-sale detection must still fire after currency conversion."""

    def _wash_round_trip(self, native_currency, base_currency):
        """Buy → loss-sell → repurchase within 30 days, converted to a base."""
        # Native-currency txs; same calendar pattern as test_canada_wash_sale_basic.
        native_txs = [
            TaxTransaction(
                action='BUYSELL', date='2025-01-15', date_settle='2025-01-15',
                symbol='SHOP.TO' if native_currency == 'CAD' else 'AAPL.US',
                quantity=100.0, net_amount=10000.0, currency=native_currency, account='M',
            ),
            TaxTransaction(
                action='BUYSELL', date='2025-06-20', date_settle='2025-06-20',
                symbol='SHOP.TO' if native_currency == 'CAD' else 'AAPL.US',
                quantity=-100.0, net_amount=8000.0, currency=native_currency, account='M',
            ),
            # +30 days from sell would be 2025-07-20; we pick 2025-06-25 (5 days later).
            TaxTransaction(
                action='BUYSELL', date='2025-06-25', date_settle='2025-06-25',
                symbol='SHOP.TO' if native_currency == 'CAD' else 'AAPL.US',
                quantity=100.0, net_amount=8500.0, currency=native_currency, account='M',
            ),
        ]
        # Add a rate for 2025-06-25 so the conversion stays deterministic.
        history = {
            'USD': {**RATE_HISTORY['USD'], '2025-06-25': Decimal('1.41')},
            'CAD': {**RATE_HISTORY['CAD'], '2025-06-25': Decimal('0.70922')},
        }
        converted = process_transactions(native_txs, base_currency, history, DEFAULT_RATE)
        result = CanadaTaxRules().compute_gains(converted)

        # Wash sale fires regardless of base; disallowed_amount equals the
        # converted loss on the middle (sell) leg.
        self.assertEqual(len(result['wash_sales']), 1)
        loss_gain = next(g for g in result['transactions']
                         if g.get('action') != 'DIVIDEND' and g.get('is_wash_sale'))
        self.assertEqual(loss_gain['currency'], base_currency)
        self.assertGreater(loss_gain['disallowed_amount'], 0)

    def test_native_cad_to_cad_base(self):
        self._wash_round_trip('CAD', 'CAD')

    def test_native_cad_to_usd_base(self):
        self._wash_round_trip('CAD', 'USD')

    def test_native_usd_to_cad_base(self):
        self._wash_round_trip('USD', 'CAD')

    def test_native_usd_to_usd_base(self):
        self._wash_round_trip('USD', 'USD')


class TestNoWashFlagAcrossBases(unittest.TestCase):
    """--no-wash leaves raw losses intact regardless of base currency."""

    def test_no_wash_cad_base(self):
        native_txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', date_settle='2025-01-15',
                           symbol='AAPL.US', quantity=100.0, net_amount=10000.0,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-06-20', date_settle='2025-06-20',
                           symbol='AAPL.US', quantity=-100.0, net_amount=8000.0,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-06-25', date_settle='2025-06-25',
                           symbol='AAPL.US', quantity=100.0, net_amount=8500.0,
                           currency='USD', account='M'),
        ]
        history = {'USD': {**RATE_HISTORY['USD'], '2025-06-25': Decimal('1.41')}}
        converted = process_transactions(native_txs, 'CAD', history, DEFAULT_RATE)
        result = CanadaTaxRules().compute_gains(converted, detect_wash_sales=False)

        self.assertEqual(len(result['wash_sales']), 0)
        gains = [g for g in result['transactions'] if g.get('action') != 'DIVIDEND']
        loss = next(g for g in gains if g['gain'] < 0)
        self.assertEqual(loss['currency'], 'CAD')
        # Loss is in CAD: (8000 × 1.40) - (10000 × 1.30) = 11200 - 13000 = -1800 CAD.
        self.assertAlmostEqual(loss['gain'], -1800.0, places=2)
        self.assertAlmostEqual(loss['disallowed_amount'], 0.0)


if __name__ == '__main__':
    unittest.main()
