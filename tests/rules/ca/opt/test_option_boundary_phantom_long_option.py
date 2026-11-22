"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import unittest
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


if __name__ == "__main__":
    unittest.main()


class TestOptionBoundaryPhantoms(unittest.TestCase):
    """Formerly: test_fix_filing_b.TestOptionBoundaryPhantoms"""
    def test_phantom_long_option_sold_is_not_a_write(self):
        """S044-09: a phantom-backed long option sold to close."""
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.option_boundary import write_lots
        from taxjson.lib.missing_history import synthesize_openings
        opt = "ZZZ250919C00050000.TO"
        sale = TaxTransaction(action="BUYSELL", date="2025-06-10",
                              date_settle="2025-06-11", time="10:00:00",
                              symbol=opt, quantity=-2, currency="CAD",
                              price=4.5, net_amount=900.0,
                              account="margin", id="s1")
        self.assertEqual(len(write_lots([sale])), 1)     # no phantom
        txs, _log = synthesize_openings([sale], {(opt, "margin")})
        self.assertEqual(write_lots(txs), [])
