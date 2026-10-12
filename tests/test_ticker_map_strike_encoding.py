"""Dotted-option → OCC strike encoding regression tests.

`map_ticker` converts dotted option strings like `ABC.17DEC27.4.02.P`
into OCC-formatted tickers like `ABC271217P00004020`. The strike portion
is `int(strike * 1000)` formatted with 8 digits.

The pre-fix implementation used `int(float(strike_str) * 1000)` which
truncated after a float multiply: e.g. `float("4.02") * 1000` returns
`4019.9999999996`, which `int()` floors to `4019` — one cent too low.
The bug fires on roughly half of all sub-$200 cent-precision strikes
(every value whose decimal expansion can't be represented exactly in
binary float).

The fix uses `int(Decimal(strike_str) * 1000)` to skip float entirely.
"""
import unittest

from taxjson.lib.ticker_map import map_ticker


class TestStrikeEncoding(unittest.TestCase):
    def test_known_float_truncation_strikes_encode_correctly(self):
        # Cherry-picked strikes that float-truncate one cent low:
        # 4.02, 8.03, 16.06, 32.12 all hit the IEEE-754 underflow.
        cases = [
            ('ABC.17DEC27.4.02.P', 'ABC271217P00004020'),
            ('ABC.17DEC27.8.03.P', 'ABC271217P00008030'),
            ('ABC.17DEC27.16.06.P', 'ABC271217P00016060'),
            ('ABC.17DEC27.32.12.P', 'ABC271217P00032120'),
            # Whole-cent strikes stayed correct under the bug — pin
            # them so a future "fix" doesn't break the easy cases.
            ('ABC.17DEC27.2.10.P', 'ABC271217P00002100'),
            ('ABC.17DEC27.100.07.P', 'ABC271217P00100070'),
        ]
        for dotted, expected_occ in cases:
            with self.subTest(dotted=dotted):
                self.assertEqual(map_ticker(dotted), expected_occ)

    def test_integer_strike_still_works(self):
        # No decimal point — Decimal(int_str) still parses cleanly.
        self.assertEqual(map_ticker('AAPL.16JAN26.150.C'),
                         'AAPL260116C00150000')

    def test_call_and_put_both_encode(self):
        self.assertEqual(map_ticker('ABC.17DEC27.4.02.C'),
                         'ABC271217C00004020')
        self.assertEqual(map_ticker('ABC.17DEC27.4.02.P'),
                         'ABC271217P00004020')


if __name__ == '__main__':
    unittest.main()
