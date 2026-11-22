"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
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


# ---------------------------------------------------------------- A2-1221
class TestTaxableTransferWording(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestTaxableTransferWording"""

    def test_run_names_the_account_not_a_flag(self):
        from taxjson.lib.pipeline import (TransferValidationError,
                                          prepare_books)
        from tax_rules.dual import tx
        book = [tx("TRANSFER", "2025-03-03", "XYZ.TO", 100, 1000,
                   currency="CAD", account="margin")]
        with self.assertRaises(TransferValidationError) as cm:
            prepare_books(book, taxable=True)
        self.assertIn("taxable account margin", str(cm.exception))
        self.assertNotIn("--taxable was set", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
