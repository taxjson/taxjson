"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import io
import os
from _tmpfiles import private_tmpfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestIbCancellations(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestIbCancellations"""
    def _parse(self, text):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with private_tmpfile('w', suffix='.csv',
                             delete=False) as f:
            f.write(text)
            name = f.name
        try:
            with redirect_stderr(io.StringIO()):
                return IbBrokerage().parse_file(Path(name))
        finally:
            os.unlink(name)

    def test_cancelled_trade_pair_nets_to_zero(self):
        txs = self._parse(
            'Trades,Header,Asset Category,Currency,Symbol,Date/Time,'
            'Quantity,T. Price,Proceeds,Comm/Fee,Code\n'
            'Trades,Data,Stocks,USD,MSFT,"2026-01-05, 09:30:00",'
            '100,10,-1000,-1,O\n'
            'Trades,Data,Stocks,USD,MSFT,"2026-01-06, 09:30:00",'
            '-100,10,1000,1,Ca\n')
        buys = [t for t in txs if t['action'] == 'BUYSELL']
        self.assertEqual(len(buys), 2)
        # Original cost == cancellation proceeds: the phantom round
        # trip nets exactly zero (was: a 2x-commission phantom loss).
        self.assertAlmostEqual(buys[0]['net_amount'],
                               buys[1]['net_amount'], places=6)

    def test_order_and_trade_levels_emit_once(self):
        txs = self._parse(
            'Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee,Code\n'
            'Trades,Data,Order,Stocks,USD,MSFT,"2026-01-05, 09:30:00",'
            '-50,400,20000,-1,\n'
            'Trades,Data,Trade,Stocks,USD,MSFT,"2026-01-05, 09:30:00",'
            '-50,400,20000,-1,\n'
            'Trades,Data,ClosedLot,Stocks,USD,MSFT,'
            '"2025-06-01, 09:30:00",50,300,,,\n')
        self.assertEqual(
            sum(1 for t in txs if t['action'] == 'BUYSELL'), 1)

    def test_restated_spinoff_emits_once(self):
        # Spin-offs are an election, booked by taxjson-corp-actions (the
        # parser leaves the rows alone); the restatement still yields
        # ONE event of 10 shares at IB's value.
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        text = (
            'Corporate Actions,Header,Asset Category,Currency,'
            'Report Date,Date/Time,Description,Quantity,Proceeds,'
            'Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,USD,2025-06-02,'
            '"2025-06-01, 20:25:00","PARN(US0000000401) Spinoff  1 for '
            '10 (SPNC, SPINCO CORP, US0000000402)",10,0,250.0,0,\n'
            'Corporate Actions,Data,Stocks,USD,2025-06-03,'
            '"2025-06-01, 20:25:00","PARN(US0000000401) Spinoff  1 for '
            '10 (SPNC, SPINCO CORP, US0000000402)",-10,0,-250.0,0,Ca\n'
            'Corporate Actions,Data,Stocks,USD,2025-06-03,'
            '"2025-06-01, 20:25:00","PARN(US0000000401) Spinoff  1 for '
            '10 (SPNC, SPINCO CORP, US0000000402)",10,0,250.0,0,\n')
        txs = self._parse(text)
        self.assertEqual(txs, [])
        with private_tmpfile('w', suffix='.csv',
                             delete=False) as f:
            f.write(text)
        try:
            evs = parse_ib_corporate_actions(Path(f.name), 'margin')
        finally:
            os.unlink(f.name)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].action_type, 'spinoff')
        self.assertAlmostEqual(evs[0].qty_received, 10.0)
        self.assertAlmostEqual(evs[0].target_fmv, 250.0)


class TestVentureSuffix(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestVentureSuffix"""
    def test_vn_normalizes_to_v(self):
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        b = QuestradeBrokerage()
        # Canonical Canadian spelling is ROOT.TO (audit S010-05): the
        # IB/RBC/Webull parsers cannot name the venue.
        self.assertEqual(b.apply_currency_suffix('ABC.VN', 'CAD'),
                         'ABC.TO')
        self.assertEqual(b.apply_currency_suffix('SHOP.TO', 'CAD'),
                         'SHOP.TO')


if __name__ == '__main__':
    unittest.main()
