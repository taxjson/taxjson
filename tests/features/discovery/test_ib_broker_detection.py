"""Regression tests for the low-round `ibparse` audit findings (IB
statement parser, shared broker helpers, taxjson-brokerage, broker
detection, the transaction schema) and the overnight-session
item. Every fixture is synthetic: invented tickers and ISINs, fake
account ids marked pii-ok.

Formerly: test_fix_l_ibparse"""
import unittest

from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------- detection and taxjson-brokerage
from pathlib import Path as _P
_EX = _paths.EXAMPLES


def _detect_text(text, encoding='utf-8', name='x.csv'):
    import tempfile
    from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
    with tempfile.TemporaryDirectory() as td:
        p = _P(td) / name
        p.write_bytes(text.encode(encoding) if isinstance(text, str)
                      else text)
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return detect_brokerage(p), err.getvalue()


class TestBrokerDetection(unittest.TestCase):
    """R1-57 / S029-14 / R1-90 / S059-17.

    Formerly: test_fix_l_ibparse.TestBrokerDetection"""

    def setUp(self):
        self.ib = (_EX / 'ib_demo.csv').read_text(encoding='utf-8')
        self.rbc = (_EX / 'rbc_direct_demo.csv').read_text(encoding='utf-8')

    def test_ib_variants_the_parser_reads(self):
        lines = self.ib.splitlines(keepends=True)
        trades_first = ''.join(l for l in lines
                               if not l.startswith(('Statement,',
                                                    'Account Information,')))
        no_broker = ''.join(l for l in lines if 'BrokerName' not in l)
        title_first = ''.join(
            [lines[0], 'Statement,Data,Title,Activity Statement\n']
            + lines[1:])
        for label, text in (('trades-first', trades_first),
                            ('no BrokerName', no_broker),
                            ('BrokerName on row 3', title_first)):
            with self.subTest(label):
                self.assertEqual(_detect_text(text)[0], 'ib')


    def test_rbc_header_after_a_partial_preamble_or_blank_line(self):
        no_brand = self.rbc.split('\n', 1)[1]
        spacer = '\n' + self.rbc[self.rbc.index('Date,Activity'):]
        for label, text in (('no brand line', no_brand),
                            ('blank first line', spacer)):
            with self.subTest(label):
                self.assertEqual(_detect_text(text)[0], 'rbc_direct')

    def test_utf16_rbc_export(self):
        self.assertEqual(_detect_text(self.rbc, 'utf-16')[0], 'rbc_direct')

    def test_cp1252_file_gets_one_line(self):
        got, err = _detect_text(self.ib.replace('Synthetic Demo',
                                                'SOCI\u00c9T\u00c9'),
                                'cp1252')
        self.assertIsNone(got)
        self.assertIn('not UTF-8 or UTF-16 text', err)
        self.assertNotIn('Traceback', err)


if __name__ == '__main__':
    unittest.main()
