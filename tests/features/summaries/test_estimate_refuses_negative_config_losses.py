"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import io
import tempfile
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


class TestEstimateConfigGuard(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestEstimateConfigGuard"""
    def test_negative_config_losses_die(self):
        # The config path skipped the CLI's sign/finiteness guard —
        # a "-10,000 carryover" fabricated taxable gains silently.
        from argparse import Namespace
        from taxjson.cli.summaries import _estimate_inputs
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'taxjson.toml').write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.m]\ntype = "taxable"\n'
                '[estimate]\nother_losses = -10000\n')
            args = Namespace(other_income=None, other_losses=None)
            with self.assertRaises(SystemExit) as cm:
                with redirect_stderr(io.StringIO()):
                    _estimate_inputs(root, args)
        self.assertIn('non-negative', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
