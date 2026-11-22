"""Regression tests for the medium-round `ibparse` audit findings
(IB statement parser, shared broker helpers, taxjson-brokerage, the
transaction schema and broker detection). Every fixture is synthetic:
invented tickers and ISINs, fake account ids marked pii-ok.

Formerly: test_fix_ibparse"""
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestUsProjectReturnOfCapital(unittest.TestCase):
    """S013-01: a US project booked a US issuer's return of capital as
    a dividend under Canada's s.90(1) default. Partition INPUTS-02 /
    SPEC-08: an explicit foreign_return_of_capital = "dividend" was then
    still honoured in a US project (and this test pinned it); the key is
    Canada-only now — refused by every config reader — and the resolver
    returns "acb" for a US project whatever the table says.

    Formerly: test_fix_ibparse.TestUsProjectReturnOfCapital"""

    @rule(R.CA_ACB_08)
    def test_default_follows_the_country(self):
        from taxjson.pipeline.stages import ib_foreign_roc_mode
        self.assertEqual(ib_foreign_roc_mode({'country': 'usa'}), 'acb')
        self.assertEqual(ib_foreign_roc_mode({'country': 'canada'}),
                         'dividend')
        self.assertEqual(ib_foreign_roc_mode(
            {'country': 'usa', 'foreign_return_of_capital': 'dividend'}),
            'acb')


if __name__ == '__main__':
    unittest.main()
