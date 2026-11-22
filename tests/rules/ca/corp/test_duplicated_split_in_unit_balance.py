"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import unittest
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestBalanceOnSplitDedup(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestBalanceOnSplitDedup"""
    def test_duplicated_broker_split_applies_once(self):
        from taxjson.lib.unit_balance import balance_on
        txs = [{'action': 'BUYSELL', 'date': '2026-01-05',
                'symbol': 'A.TO', 'quantity': 100.0, 'account': 'm'},
               {'action': 'SPLIT', 'date': '2026-02-01',
                'symbol': 'A.TO', 'quantity': 2.0, 'account': 'm',
                'id': 'broker1'},
               {'action': 'SPLIT', 'date': '2026-02-01',
                'symbol': 'A.TO', 'quantity': 2.0, 'account': 'm',
                'id': 'broker2'}]
        self.assertAlmostEqual(
            balance_on(txs, 'A.TO', '2026-03-01'), 200.0)


if __name__ == '__main__':
    unittest.main()
