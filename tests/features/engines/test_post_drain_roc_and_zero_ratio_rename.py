"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestMediumSeverityDeepAuditBatch(unittest.TestCase):
    """Deep-audit 2026-08 medium fixes: post-drain ROC warning, ratio-0
    rename migration, short-side wash_deferred visibility, T1135
    tainted filter.

    Formerly: test_audit_2026_08_fixes.TestMediumSeverityDeepAuditBatch"""

    def test_post_drain_roc_adjust_warns(self):
        import io
        from contextlib import redirect_stderr
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='XRE.TO', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='CAD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-05',
                           symbol='XRE.TO', quantity=-100.0, price=11.0,
                           net_amount=1100.0, currency='CAD', account='M'),
            # ROC posted AFTER the full exit.
            TaxTransaction(action='ADJUST', date='2025-03-01',
                           symbol='XRE.TO', quantity=0.0,
                           net_amount=-60.0, currency='CAD', account='M',
                           type='roc'),
        ]
        buf = io.StringIO()
        with redirect_stderr(buf):
            CanadaTaxRules().compute_gains(txs)
        self.assertIn("EMPTY pool", buf.getvalue())

    def test_ratio_zero_rename_still_migrates(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='OLD.US', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='SPLIT', date='2025-02-01',
                           symbol='OLD.US', symbol_new='NEW.US',
                           quantity=0.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-01',
                           symbol='NEW.US', quantity=-100.0, price=12.0,
                           net_amount=1200.0, currency='USD', account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        closes = [g for g in result['transactions']
                  if g.get('raw_gain') is not None]
        self.assertEqual(len(closes), 1,
                         "the ratio-0 rename was dropped: no gain "
                         "realized, book forked into long+phantom short")
        self.assertAlmostEqual(closes[0]['raw_gain'], 200.0, places=2)
        self.assertFalse(result.get('inventory'),
                         "no residual position may survive the rename")

    def test_short_wash_deferred_visible_in_inventory(self):
        txs = [
            # Short-open A, cover at a loss; replacement short B stays
            # open with the deferral embedded.
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-01-20',
                           time='09:30:00', symbol='AAPL',
                           quantity=-100.0, price=11.0,
                           net_amount=1100.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=12.0,
                           net_amount=1200.0, currency='USD', account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        inv = next(r for r in result['inventory'] if r['qty'] < 0)
        self.assertGreater(
            inv.get('deferred_wash', 0.0), 0.0,
            "the short lot's embedded §1091 deferral was invisible "
            "(long-side lots already reported theirs)")

    def test_t1135_excludes_tainted_gains(self):
        import io
        import json as _json
        from contextlib import redirect_stderr
        from taxjson.bin.taxjson_t1135 import join_income_gains
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "g.json"
            p.write_text(_json.dumps({"transactions": [
                {"date": "2025-05-01", "symbol": "AAPL.US", "qty": -10,
                 "gain": 5000.0, "tainted": True},
                {"date": "2025-06-01", "symbol": "AAPL.US", "qty": -10,
                 "gain": 100.0},
            ]}))
            buf = io.StringIO()
            with redirect_stderr(buf):
                out = join_income_gains([p], 2025)
        self.assertAlmostEqual(out["AAPL.US"]["gain"], 100.0, places=2,
                               msg="the phantom-basis 5,000 leaked into "
                                   "the T1135 gain column")
        self.assertIn("EXCLUDED", buf.getvalue())


if __name__ == '__main__':
    unittest.main()
