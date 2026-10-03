"""Moved from the core's tests/test_fix_parsers.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Regression tests for the broker-parser audit findings R1-51, R1-63,
R1-66, R1-91, S010-05 and S014-07, plus the last comma-stripping number
parses. Every fixture is synthetic: invented tickers, fake account ids
marked pii-ok, invented prices.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import unittest
from tax_rules import rule


@rule("CA-ACB-04")
class TestCanadianListingIdentity(unittest.TestCase):
    """S010-05 / S014-07: one spelling per Canadian listing, whichever
    parser read it."""

    def test_generic_and_live_positions_agree(self):
        from taxjson_fetch.api import qt_position_symbol as q
        self.assertEqual(q('ABC.VN'), 'ABC.TO')
        self.assertEqual(q('CCC.CN'), 'CCC.TO')
        self.assertEqual(q('FTN.PRA.TO'), 'FTN.PR.A.TO')
        from taxjson.lib.brokerages.generic import GenericBrokerage
        g = GenericBrokerage()
        self.assertEqual(g._listing_symbol('ABC.V', 'USD'), 'ABC.TO')
        self.assertEqual(g._listing_symbol('CCC.CN', 'CAD'), 'CCC.TO')


if __name__ == "__main__":
    unittest.main()
