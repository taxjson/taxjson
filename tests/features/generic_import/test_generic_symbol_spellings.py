"""Regression tests for the medium-round `ibparse` audit findings
(IB statement parser, shared broker helpers, taxjson-brokerage, the
transaction schema and broker detection). Every fixture is synthetic:
invented tickers and ISINs, fake account ids marked pii-ok.

Formerly: test_fix_ibparse"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestGenericSymbolSpelling(unittest.TestCase):
    """S010-04: the generic importer only dotted spaces — BRK-B and
    BRK/B were separate pools from BRK.B, a padded OCC symbol became
    'XYZ...250321C...', and an option description was booked as a
    share.

    Formerly: test_fix_ibparse.TestGenericSymbolSpelling"""

    HDR = 'Date,Type,Ticker,Shares,Price,Amount,Commission,Currency\n'
    TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
            'quantity="Shares"\nprice="Price"\namount="Amount"\n'
            'fee="Commission"\ncurrency="Currency"\n'
            '[actions]\n"BUY"="buy"\n"SELL"="sell"\n')

    def _parse(self, rows):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / 'generic_t.csv'
            c.write_text(self.HDR + rows)
            c.with_name(c.name + '.toml').write_text(self.TOML)
            with contextlib.redirect_stderr(io.StringIO()):
                return GenericBrokerage().parse_file(c)

    def test_class_separators_fold_to_a_dot(self):
        txs = self._parse('2025-03-10,BUY,QZK-B,10,300,3000,0,USD\n'
                          '2025-03-11,BUY,QZK/B,10,300,3000,0,USD\n'
                          '2025-03-12,BUY,QZK B,10,300,3000,0,USD\n')
        self.assertEqual({t['symbol'] for t in txs}, {'QZK.B.US'})

    def test_padded_occ_symbol_is_compacted(self):
        txs = self._parse('2025-03-10,BUY,QZY   250321C00050000,1,2,200,0,'
                          'USD\n')
        self.assertEqual(txs[0]['symbol'], 'QZY250321C00050000.US')

    def test_option_description_is_refused(self):
        with self.assertRaises(BrokerageParseError):
            self._parse('2025-03-10,BUY,QZY 21MAR25 50 C,1,200,200,0,USD\n')


if __name__ == '__main__':
    unittest.main()
