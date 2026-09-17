import unittest
import tempfile
import os
from pathlib import Path
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage

class TestSettlementDates(unittest.TestCase):
    def test_rbc_settlement_date(self):
        content = """\"Activity Export as of Jan 5, 2026\"
\"Account: 12345678 - Margin\"
\"Trades this month: 0\"
\"Date\",\"Activity\",\"Symbol\",\"Symbol Description\",\"Quantity\",\"Price\",\"Settlement Date\",\"Account\",\"Value\",\"Currency\",\"Description\"
\"December 29, 2025\",\"Buy\",\"AAPL\",\"APPLE\",\"-10\",\"100.00\",\"December 30, 2025\",\"12345678\",\"-1000\",\"USD\",\"BUY AAPL\"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
            
        try:
            parser = RbcBrokerage()
            txs = parser.parse_file(Path(fname))
            self.assertEqual(len(txs), 1)
            self.assertEqual(txs[0]['date_settle'], '2025-12-30')
        finally:
            os.remove(fname)

    def test_qt_settlement_date(self):
        content = """Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type
2026-03-23 12:00:00 AM,2026-03-24 12:00:00 AM,Buy,AAPL,APPLE INC,10.0,100.0,1000.0,0.0,-1000.0,USD,12345,Trades,Individual LIRA
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
            
        try:
            parser = QuestradeBrokerage()
            txs = parser.parse_file(Path(fname))
            self.assertEqual(len(txs), 1)
            self.assertEqual(txs[0]['date_settle'], '2026-03-24')
        finally:
            os.remove(fname)

if __name__ == '__main__':
    unittest.main()
