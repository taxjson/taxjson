import unittest
import tempfile
import os
import sys
from pathlib import Path
import json

from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage

class TestCryptoParsers(unittest.TestCase):
    def test_kraken_trades(self):
        content = """txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\nT1,O1,BTC/USD,2025-01-15 10:00:00.1234,buy,limit,60000,6000,1,0.1,,,"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
            
        try:
            parser = KrakenBrokerage()
            txs = parser.parse_file(Path(fname))
            self.assertEqual(len(txs), 1)
            self.assertEqual(txs[0]['symbol'], 'BTC')
            self.assertEqual(txs[0]['currency'], 'USD')
            self.assertEqual(txs[0]['quantity'], 0.1)
            self.assertEqual(txs[0]['net_amount'], 6001.0)
        finally:
            os.remove(fname)

    def test_kraken_ledgers(self):
        content = """txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\nL1,,2025-01-15 10:00:00,earn,reward,currency,ADA,,100,0,1000\nL2,REF1,2025-01-15 10:05:00,spend,,currency,ZUSD,,-100,1,0\nL3,REF1,2025-01-15 10:05:00,receive,,currency,XXBT,,0.001,0,0"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
            
        try:
            parser = KrakenBrokerage()
            txs = parser.parse_file(Path(fname))
            self.assertEqual(len(txs), 3) # 1 dividend, 1 0-cost earn buy, 1 instant trade
            
            # Find the reward
            divs = [t for t in txs if t['action'] == 'DIVIDEND']
            self.assertEqual(len(divs), 1)
            self.assertEqual(divs[0]['symbol'], 'ADA')
            
            # Find the trade
            trade = next(t for t in txs if t.get('description', '') == 'Instant Trade')
            self.assertEqual(trade['symbol'], 'BTC')
            self.assertEqual(trade['currency'], 'USD')
            self.assertEqual(trade['quantity'], 0.001)
            # Fee-inclusive net (engine convention): Kraken debits the
            # $1 fee on top of the $100 spend, so the buy's true cost
            # is $101 — the bare-100 pin understated basis by the fee
            # (2026-08 deep audit).
            self.assertEqual(trade['net_amount'], 101.0)
            self.assertEqual(trade['fee'], 1.0)
        finally:
            os.remove(fname)

if __name__ == '__main__':
    unittest.main()
