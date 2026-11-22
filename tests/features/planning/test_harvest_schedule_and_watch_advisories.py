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


class TestHarvestScheduleSemantics(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestHarvestScheduleSemantics"""
    def test_violation_loss_is_claimable_now(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        rows = [{'verdict': 'LOSS', 'unrealized': -1000.0,
                 'radar': {'category': 'VIOLATION',
                           'clears_at': '2099-01-01'}}]
        out = _recovery_schedule(rows)
        self.assertAlmostEqual(out['now'], 1000.0)


class TestWatchAdvisoryDiff(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestWatchAdvisoryDiff"""
    def test_same_category_advisory_change_is_news(self):
        from taxjson.bin.taxjson_watch import diff_radar
        prev = {'X.TO': {'category': 'VIOLATION',
                         'advisory': 'Sell 100 by 2026-09-20.',
                         'clears_at': '2026-09-20'}}
        cur = {'X.TO': {'category': 'VIOLATION',
                        'advisory': 'Sell 200 by 2026-09-20.',
                        'clears_at': '2026-09-20'}}
        changes = diff_radar(prev, cur)
        self.assertEqual([c['kind'] for c in changes],
                         ['advisory_changed'])


if __name__ == '__main__':
    unittest.main()
