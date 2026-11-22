"""The 2026-10 filing-amount audit: five defects that changed exported
filing figures (F1-F5), each with the neighbouring control that passed.

All data is synthetic (made-up tickers, whole-dollar amounts).

Formerly: test_fix_filing_audit_2026_10
"""
import contextlib
import io
import unittest

from taxjson.lib.tax_forms import build_schedule3
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def compute(book, country, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        result = run_gains(book, req=GainsRequest(country=country,
                                                  taxable=True, **kw))
    return result, err.getvalue()


def crypto_book(q=0.0000009):
    return [tx('BUYSELL', d, 'QZCOIN', sign * q, amount, currency='CAD',
               id=ident)
            for d, sign, amount, ident in [
                ('2024-01-02', 1, .18, 'buy'), ('2025-01-02', -1, .09, 'loss'),
                ('2025-01-10', 1, .09, 'rebuy'), ('2025-03-03', -1, .18, 'sale')]]


def _inventory(res, symbol):
    return [r for r in res['inventory'] if r['symbol'] == symbol]


class TestF2CanadaMicroCryptoDeniedLoss(unittest.TestCase):
    """F2: a sub-micro coin position (0.0000009 QZCOIN) read as FLAT by
    the 1e-6 share epsilon when the denied loss's ADJUST landed: the
    deferral was parked in pending_wash and the closing sale never
    recovered it — Schedule 3 showed the denied 0.09 as a gain.

    Formerly: test_fix_filing_audit_2026_10.TestF2CanadaMicroCryptoDeniedLoss"""

    @rule(R.CA_CRYPTO_09, R.CA_SL_09)
    def test_replacement_carries_the_denied_loss(self):
        r, _ = compute(crypto_book(), 'canada', year=2025, spot_crypto=True)
        f = build_schedule3(r['transactions'], year=2025)
        self.assertEqual(f['totals']['gain_all'], 0.0)
        self.assertFalse([i for i in _inventory(r, 'QZCOIN')
                          if abs(i['qty']) > 1e-15], "fully sold")

    def test_control_whole_coin(self):
        r, _ = compute(crypto_book(1), 'canada', year=2025, spot_crypto=True)
        self.assertEqual(build_schedule3(r['transactions'], 2025)
                         ['totals']['gain_all'], 0)


if __name__ == '__main__':
    unittest.main()
