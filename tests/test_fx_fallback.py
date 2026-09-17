import unittest
import tempfile
import os
import sys
from pathlib import Path
import subprocess
import json

class TestFxFallbackLogic(unittest.TestCase):
    def run_converter(self, tx_content, rates_content):
        # Create rates file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as fr:
            fr.write(rates_content)
            rates_name = fr.name
            
        # Create input json
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(json.dumps({"transactions": tx_content}))
            fname = f.name
        
        try:
            cmd = [sys.executable, "-m", "taxjson.bin.taxjson_convert_currency", fname, "--to", "CAD", "--rates", rates_name]
            result = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(Path(__file__).parent.parent)))
            if result.returncode != 0:
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)
            return json.loads(result.stdout)
        finally:
            os.remove(fname)
            os.remove(rates_name)

    def test_fee_cad_no_conversion(self):
        rates = "2025-01-03 16:00:00 USD CAD 1.3500\n"
        txs = [{"action": "FEE", "date": "2025-01-04", "symbol": "CASH", "currency": "CAD", "net_amount": 5.50}]
        res = self.run_converter(txs, rates)
        self.assertEqual(res['transactions'][0]['net_amount'], 5.50)

    def test_fee_usd_converted(self):
        rates = "2025-01-04 16:00:00 USD CAD 1.3500\n"
        txs = [{"action": "FEE", "date": "2025-01-04", "symbol": "CASH", "currency": "USD", "net_amount": 5.00}]
        res = self.run_converter(txs, rates)
        self.assertAlmostEqual(res['transactions'][0]['net_amount'], 6.75)

    def test_fee_weekend_fallback(self):
        # Rate available Friday 01-03, trade on Saturday 01-04
        rates = "2025-01-03 16:00:00 USD CAD 1.3500\n"
        txs = [{"action": "FEE", "date": "2025-01-04", "symbol": "CASH", "currency": "USD", "net_amount": 10.00}]
        res = self.run_converter(txs, rates)
        self.assertAlmostEqual(res['transactions'][0]['net_amount'], 13.50)

    def test_partial_conversion_failure_leaves_row_unchanged(self):
        """Regression: when one field fails to convert, the old loop
        wrote earlier fields already-converted while later fields
        stayed in source currency — producing mixed-currency rows.
        Fix is stage-then-commit: all fields convert into a scratch
        dict, only committed if every conversion succeeded."""
        from decimal import Decimal
        from taxjson.lib.core import TaxTransaction
        from taxjson.bin.taxjson_convert_currency import convert_transaction
        tx = TaxTransaction(
            action='BUYSELL', date='2025-01-15', symbol='AAPL.US',
            quantity=100.0, price=150.0, net_amount=15000.0,
            currency='USD', account='Margin',
        )
        # Corrupt `fee` so its conversion raises mid-loop. The old code
        # would still relabel currency=CAD and emit a partially-
        # converted row; the staged version leaves everything alone.
        tx.fee = 'not-a-number'
        history = {'USD': {'2025-01-15': Decimal('1.35')}}
        converted = convert_transaction(tx, 'CAD', history, Decimal('1.35'))
        self.assertEqual(converted.currency, 'USD',
                         "Partial-failure must NOT relabel currency")
        self.assertAlmostEqual(converted.price, 150.0, places=4)
        self.assertAlmostEqual(converted.net_amount, 15000.0, places=4)

if __name__ == '__main__':
    unittest.main()
