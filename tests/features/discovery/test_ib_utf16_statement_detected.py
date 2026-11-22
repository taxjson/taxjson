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

from _style import CapturedWidth
from _support.ib_statement_rows import HEAD, TRADES_H, _trade


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------ detection, schema, numbers
class TestDetectUtf16(unittest.TestCase):
    """R1-70: detection opened every file as utf-8-sig, so a UTF-16
    export the parsers read could not be routed.

    Formerly: test_fix_ibparse.TestDetectUtf16"""

    def test_utf16_ib_statement_is_detected(self):
        from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
        body = HEAD + TRADES_H + _trade('QZA', '2025-05-01, 09:30:00',
                                        10, 10, -100, -1)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'x.csv'
            p.write_bytes(body.encode('utf-16'))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(detect_brokerage(p), 'ib')


if __name__ == '__main__':
    unittest.main()
