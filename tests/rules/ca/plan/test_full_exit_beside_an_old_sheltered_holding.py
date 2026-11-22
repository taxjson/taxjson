"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


class TestExitableWithShelteredHolding(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestExitableWithShelteredHolding"""
    def test_full_exit_advice_carries_the_denied_portion(self):
        # Taxable in-window buys + an OLD
        # sheltered holding. The sheltered shares were bought before
        # the window, so they are not substituted property: a FULL
        # taxable exit keeps the whole loss (the engine's per-holder
        # rule). This test used to pin "PERMANENTLY denied" (medium
        # audit R1-231); the old holding is now a forward caveat only.
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            tax = Path(td) / 'tax.json'
            shl = Path(td) / 'shl.json'
            tax.write_text(json.dumps({'transactions': [
                {'action': 'BUYSELL', 'date': '2026-01-06',
                 'time': '09:30:00', 'symbol': 'QFX.TO',
                 'quantity': 15, 'net_amount': 24300.0,
                 'currency': 'CAD', 'account': 'margin'},
                {'action': 'BUYSELL', 'date': '2026-01-21',
                 'time': '09:30:00', 'symbol': 'QFX.TO',
                 'quantity': 15, 'net_amount': 23850.0,
                 'currency': 'CAD', 'account': 'margin'}]}))
            shl.write_text(json.dumps({'transactions': [
                {'action': 'BUYSELL', 'date': '2025-10-15',
                 'time': '09:30:00', 'symbol': 'QFX.TO',
                 'quantity': 8, 'net_amount': 12800.0,
                 'currency': 'CAD', 'account': 'resp'}]}))
            r = subprocess.run(
                [sys.executable, '-m',
                 'taxjson.bin.taxjson_wash_radar', '--country', 'canada',
                 '--taxable', str(tax), '--sheltered', str(shl),
                 '--date', '2026-01-28'],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('EXITABLE', r.stdout)
        self.assertNotIn('PERMANENTLY denied', r.stdout)
        self.assertIn('loss is fine now', r.stdout)
        self.assertIn('bought before the window', r.stdout)


if __name__ == '__main__':
    unittest.main()
