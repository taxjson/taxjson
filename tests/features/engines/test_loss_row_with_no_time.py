"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import io
import unittest
from contextlib import redirect_stderr

from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _t(**kw):
    d = dict(action='BUYSELL', date='2026-01-05', time='09:30:00',
             symbol='Q.TO', quantity=100.0, currency='CAD',
             net_amount=1000.0, account='A')
    d.update(kw)
    return TaxTransaction(**d)


class TestEngineEmptyTimeNoCrash(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestEngineEmptyTimeNoCrash"""
    def test_loss_row_with_empty_time(self):
        txs = [_t(date='2026-01-05', quantity=100),
               _t(date='2026-02-01', quantity=100, net_amount=900.0),
               _t(date='2026-02-10', quantity=-100, net_amount=800.0,
                  time='')]
        with redirect_stderr(io.StringIO()):
            r = get_tax_rules('canada').compute_gains(txs)
        # Used to raise ValueError out of _sl_mk_adjust; now the wash
        # sale computes.
        self.assertEqual(len(r.get('wash_sales') or []), 1)


if __name__ == '__main__':
    unittest.main()
