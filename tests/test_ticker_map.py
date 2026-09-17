import unittest
import tempfile
import os
import sys
from pathlib import Path
import subprocess
import json

class TestTickerMap(unittest.TestCase):
    def test_ticker_mapping(self):
        tx_content = {
            "transactions": [
                {"action": "BUYSELL", "date": "2025-01-01", "symbol": "AEM.US"},
                {"action": "BUYSELL", "date": "2025-01-01", "symbol": "AEM250321C00100000.US"},
                {"action": "BUYSELL", "date": "2025-01-01", "symbol": "AAPL.US"},
                {"action": "BUYSELL", "date": "2025-01-01", "symbol": "AAPL260116C00150000.US"}
            ]
        }
        
        map_content = "GLOBAL AEM.US AEM.TO\nGLOBAL AAPL.US AAPL.NEO\n"
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(json.dumps(tx_content))
            fname = f.name
            
        with tempfile.NamedTemporaryFile(mode='w', suffix='.map', delete=False) as fm:
            fr_name = fm.name
            fm.write(map_content)
            
        try:
            # Use the installed module path (not a loose script path)
            # so this test exercises the same surface as the
            # console-script entry point — protecting against
            # packaging regressions where a refactor breaks
            # `taxjson-ticker-map` while a raw-file invocation still
            # works.
            cmd = [sys.executable, "-m", "taxjson.bin.taxjson_ticker_map", fname, fr_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(result.stderr)
            self.assertEqual(result.returncode, 0)
            
            output = json.loads(result.stdout)
            txs = output['transactions']
            
            self.assertEqual(txs[0]['symbol'], "AEM.TO")
            self.assertEqual(txs[1]['symbol'], "AEM250321C00100000.TO")
            self.assertEqual(txs[2]['symbol'], "AAPL.NEO")
            self.assertEqual(txs[3]['symbol'], "AAPL260116C00150000.NEO")
        finally:
            os.remove(fname)
            os.remove(fr_name)

if __name__ == '__main__':
    unittest.main()
