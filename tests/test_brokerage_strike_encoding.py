"""Broker-side OCC strike encoding regression tests.

`ticker_map.py` already fixed `int(float(strike) * 1000)` (which floors a
cent-precision strike one cent low — `float("4.02") * 1000` == 4019.999…),
but the broker parsers still used the buggy float pattern in
`BaseBrokerage.format_occ_symbol` and at four sites in `ib_extractor.py`.
Both now route through the shared `encode_occ_strike` helper.
"""
import unittest

from taxjson.lib.brokerages.base import BaseBrokerage, encode_occ_strike


class TestEncodeOccStrike(unittest.TestCase):
    def test_float_truncating_strikes_encode_correctly(self):
        # These all underflow under int(float(s) * 1000): 4019, 2009, 16059.
        self.assertEqual(encode_occ_strike('4.02'), '00004020')
        self.assertEqual(encode_occ_strike('2.01'), '00002010')
        self.assertEqual(encode_occ_strike('16.06'), '00016060')

    def test_whole_and_half_dollar_strikes(self):
        self.assertEqual(encode_occ_strike('55'), '00055000')
        self.assertEqual(encode_occ_strike('197.50'), '00197500')
        self.assertEqual(encode_occ_strike('82'), '00082000')

    def test_accepts_number_and_whitespace(self):
        self.assertEqual(encode_occ_strike(4.02), '00004020')
        self.assertEqual(encode_occ_strike(' 4.02 '), '00004020')


class TestFormatOccSymbol(unittest.TestCase):
    def test_cent_precision_strike_not_one_cent_low(self):
        sym = BaseBrokerage().format_occ_symbol('P', 'U', '09/19/25', '4.02')
        self.assertEqual(sym, 'U250919P00004020')

    def test_call_whole_strike(self):
        sym = BaseBrokerage().format_occ_symbol('C', 'AAPL', '01/16/26', '150')
        self.assertEqual(sym, 'AAPL260116C00150000')


if __name__ == '__main__':
    unittest.main()
