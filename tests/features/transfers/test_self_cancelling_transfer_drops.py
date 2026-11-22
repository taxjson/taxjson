"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import io
import unittest
from contextlib import redirect_stderr
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


class TestPairDropClustering(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestPairDropClustering"""
    def test_distant_events_never_cancel(self):
        # In-kind contribution OUT (Feb, a deemed disposition) and an
        # unrelated transfer IN (Dec) used to cancel silently,
        # bypassing the taxable hard error.
        from taxjson.lib.pipeline import (_handle_transfers,
                                          TransferValidationError)
        txs = [_t(date='2023-05-01', quantity=100, net_amount=5000.0,
                  account='margin'),
               _t(action='TRANSFER', date='2024-02-10', quantity=-100,
                  net_amount=8000.0, account='margin'),
               _t(action='TRANSFER', date='2024-12-05', quantity=100,
                  net_amount=2000.0, account='margin')]
        with self.assertRaises(TransferValidationError):
            with redirect_stderr(io.StringIO()):
                _handle_transfers(txs, [], taxable=True)

    def test_split_between_legs_blocks_the_drop(self):
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        txs = [_t(action='TRANSFER', date='2026-03-01', quantity=-100),
               TaxTransaction(action='SPLIT', date='2026-03-05',
                              symbol='Q.TO', quantity=2.0, account='A'),
               _t(action='TRANSFER', date='2026-03-10', quantity=100)]
        out, dropped = _drop_self_cancelling_transfers(txs)
        self.assertEqual(dropped, [])
        self.assertEqual(
            sum(1 for t in out if t.action == 'TRANSFER'), 2)

    def test_two_separate_moves_both_cancel(self):
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        txs = [_t(action='TRANSFER', date='2026-03-01', quantity=-50),
               _t(action='TRANSFER', date='2026-03-03', quantity=50),
               _t(action='TRANSFER', date='2026-07-01', quantity=-50),
               _t(action='TRANSFER', date='2026-07-02', quantity=50)]
        out, _ = _drop_self_cancelling_transfers(txs)
        self.assertEqual([t for t in out if t.action == 'TRANSFER'], [])


if __name__ == '__main__':
    unittest.main()
