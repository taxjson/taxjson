import unittest
import sys
import json
import tempfile
import os
from pathlib import Path
from datetime import datetime, timedelta

from taxjson.lib.core import TaxTransaction, CanadaTaxRules

class TestPortedTT(unittest.TestCase):
    def setUp(self):
        self.rules = CanadaTaxRules()

    def test_scenario_1_simple_wash_sale(self):
        """Scenario 1: Simple Buy-Sell-Repurchase (Taxable)"""
        # Buy 100 @ $100
        # Sell 100 @ $80 (Loss of $2000)
        # Repurchase 100 @ $85 within 30 days
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC.US', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC.US', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='ABC.US', quantity=100.0, net_amount=8500.0, currency='USD', id='b2'),
        ]
        result = self.rules.compute_gains(txs)
        
        self.assertEqual(len(result['wash_sales']), 1, "Should detect simple wash sale")
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 2000.0, 2, "Should disallow the full $2000 loss")

    def test_scenario_2_pre_acquisition(self):
        """Scenario 2: Pre-Acquisition (Substitution)"""
        # Original position: Buy 100 @ $100
        # Buy another 100 @ $90 (within 30 days before the loss)
        # Sell original 100 @ $80 (Loss)
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC.US', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='ABC.US', quantity=100.0, net_amount=9000.0, currency='USD', id='b2'),
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='ABC.US', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1'),
        ]
        result = self.rules.compute_gains(txs)
        
        self.assertEqual(len(result['wash_sales']), 1, "Should detect pre-acquisition wash sale")

    def test_scenario_3_partial_wash_sale(self):
        """Scenario 3: Partial Wash Sale"""
        # Buy 100 @ $100
        # Sell 100 @ $80 (Loss of $20/sh)
        # Repurchase only 50 @ $80
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC.US', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC.US', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='ABC.US', quantity=50.0, net_amount=4000.0, currency='USD', id='b2'),
        ]
        result = self.rules.compute_gains(txs)
        
        self.assertEqual(len(result['wash_sales']), 1)
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_qty'], 50.0, "Should only disallow 50 shares")
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 1000.0, 2, "Should only disallow half the loss ($1000)")

    def test_scenario_4_sheltered_trigger(self):
        """Scenario 4: Tax-Sheltered Trigger (Permanent Disallowance)"""
        # Sell @ Loss in Taxable
        taxable_txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC.US', quantity=100.0, net_amount=10000.0, currency='USD', account='TAXABLE', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC.US', quantity=-100.0, net_amount=8000.0, currency='USD', account='TAXABLE', id='s1'),
        ]
        # Buy in RRSP
        sheltered_txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='ABC.US', quantity=100.0, net_amount=8000.0, currency='USD', account='RRSP', id='b2'),
        ]
        
        result = self.rules.compute_gains(taxable_txs, sheltered_transactions=sheltered_txs)
        
        self.assertEqual(len(result['wash_sales']), 1, "Should detect wash sale from RRSP")
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 2000.0, 2)

    def test_scenario_5_short_position_wash_sale(self):
        """Scenario 5: Short Position Wash Sale"""
        # 1. Open Short: Sell 100 @ $100
        # 2. Close Short @ Loss (Buy to cover): Buy 100 @ $120 (Loss of $2000)
        # 3. Open another Short within 30 days: Sell 100 @ $130
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC.US', quantity=-100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC.US', quantity=100.0, net_amount=12000.0, currency='USD', id='s1'),
            TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='ABC.US', quantity=-100.0, net_amount=13000.0, currency='USD', id='b2'),
        ]
        result = self.rules.compute_gains(txs)
        # 2026-09 Canada audit: a new short is not an acquisition (ITA s.54),
        # so the cover loss stands; only a LONG rebuy held at day 30 denies.
        self.assertEqual(len(result['wash_sales']), 0, "a re-short must not trigger a superficial loss")
        txs[2] = TaxTransaction(action='BUYSELL', date='2025-01-20', symbol='ABC.US', quantity=100.0, net_amount=13000.0, currency='USD', id='b2')
        result = self.rules.compute_gains(txs)
        self.assertEqual(len(result['wash_sales']), 1, "a long rebuy held at day 30 denies the cover loss")
        self.assertAlmostEqual(result['wash_sales'][0]['disallowed_amount'], 2000.0, 2)

    def test_scenario_7_safe_disposition(self):
        """Scenario 7: Safe Disposition (Clearing the Window)"""
        # 1. Buy 100 @ $100
        # 2. Sell 100 @ $80 (Loss)
        # 3. Repurchase 100 @ $80 on 01-15
        # 4. Sell those new shares on 01-25 (Still within window of sale 1, but cleared before D+30)
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='ABC.US', quantity=100.0, net_amount=10000.0, currency='USD', id='b1'),
            TaxTransaction(action='BUYSELL', date='2025-01-10', symbol='ABC.US', quantity=-100.0, net_amount=8000.0, currency='USD', id='s1'),
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='ABC.US', quantity=100.0, net_amount=8000.0, currency='USD', id='b2'),
            TaxTransaction(action='BUYSELL', date='2025-01-25', symbol='ABC.US', quantity=-100.0, net_amount=8000.0, currency='USD', id='s2'),
        ]
        result = self.rules.compute_gains(txs)
        
        # In Scenario 7, if you exit the replacement position within the 30 day window and remain out, 
        # the loss from the first sale is realized.
        self.assertEqual(len(result['wash_sales']), 0, "Should NOT flag wash sale if replacement is sold before D+30 and not held")

    def test_split_corporate_action(self):
        """Ported from test_corp_actions.t: Test Split Corporate Action"""
        # Buy 10 @ 100
        # Split 10.0 (10-for-1)
        # Sell 50 @ 15
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-01', symbol='NVDA.US', quantity=10.0, net_amount=1000.0, currency='USD', id='b1'),
            TaxTransaction(action='SPLIT', date='2025-02-01', symbol='NVDA.US', quantity=10.0, id='sp1'),
            TaxTransaction(action='BUYSELL', date='2025-03-01', symbol='NVDA.US', quantity=-50.0, net_amount=750.0, currency='USD', id='s1'),
        ]
        result = self.rules.compute_gains(txs)
        
        # After split: 100 shares @ 1000 total cost = 10.0 avg cost
        # Sell 50: proceeds 750, cost 500, gain 250
        self.assertEqual(len(result['transactions']), 1)
        self.assertAlmostEqual(result['transactions'][0]['gain'], 250.0)
        
        inventory = {item['symbol']: item for item in result['inventory']}
        self.assertAlmostEqual(inventory['NVDA.US']['qty'], 50.0)
        self.assertAlmostEqual(inventory['NVDA.US']['total_cost'], 500.0)

    def test_currency_conversion_logic(self):
        """Test the underlying currency conversion logic used by taxjson_convert_currency.py"""
        from taxjson.lib.core import convert_currency
        from decimal import Decimal
        
        rates = {'USD': Decimal('1.35')}
        
        # USD to CAD
        self.assertAlmostEqual(convert_currency(100.0, 'USD', 'CAD', rates, 1.0), 135.0)
        # CAD to CAD
        self.assertAlmostEqual(convert_currency(100.0, 'CAD', 'CAD', rates, 1.35), 100.0)
        # Default rate fallback
        self.assertAlmostEqual(convert_currency(100.0, 'EUR', 'CAD', {}, 1.5), 150.0)

    def test_radar_script(self):
        """Test that taxjson_wash_radar.py runs without error and detects expected conditions."""
        import subprocess
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tax_file = Path(tmpdir) / "taxable.json"
            # Scenario: Buy recently (Locked)
            data = {
                "transactions": [
                    {
                        "action": "BUYSELL", "date": "2025-01-01", "time": "09:30:00", 
                        "symbol": "ABC", "quantity": 100.0, "currency": "USD", 
                        "price": 100.0, "net_amount": 10000.0
                    }
                ]
            }
            with open(tax_file, 'w') as f:
                json.dump(data, f)
            
            # Run radar with target date 2025-01-05 (4 days after buy)
            # Use the module path since it's installed
            cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar", "--taxable", str(tax_file), "--date", "2025-01-05"]
            env = os.environ.copy()
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            
            if result.returncode != 0:
                print(f"Radar test failed with return code {result.returncode}")
                print(f"STDOUT: {result.stdout}")
                print(f"STDERR: {result.stderr}")
            
            self.assertEqual(result.returncode, 0)
            self.assertIn("LOCKED", result.stdout)
            # A taxable-only recent buy names the account and the date, and
            # advises a full exit (no sheltered/registered buy in the window).
            self.assertIn("Recent buy in 'PORTFOLIO' on 2025-01-01", result.stdout)
            self.assertIn("EXITABLE", result.stdout)
        self.assertIn("FULL position", result.stdout)

if __name__ == '__main__':
    unittest.main()
