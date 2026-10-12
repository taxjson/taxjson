"""Tests for description-keyed security overrides in taxjson-brokerage.

The brokerage parsers derive a ticker's exchange suffix from the row's
currency (USD -> .US, CAD -> .TO). That mislabels a security trading in
multiple currencies on one exchange: the TSX-listed Global X US Dollar
ETF's USD class becomes a fictional `DLR.US`, colliding with US-listed
Digital Realty Trust (NYSE: DLR). A `--security-overrides` file rewrites
the symbol by matching the description — the only field that tells the
two `DLR`s apart.
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_brokerage import (
    apply_security_override, load_security_overrides,
)

# (description-substring lowercased, currency, symbol)
_OVERRIDES = [("us dlr currency etf", "USD", "DLR.U.TO")]


class TestApplySecurityOverride(unittest.TestCase):
    def test_matching_description_and_currency_rewrites(self):
        tx = {'symbol': 'DLR.US', 'currency': 'USD',
              'description': 'GLOBAL X US DLR CURRENCY ETF UNIT CL A'}
        apply_security_override(tx, _OVERRIDES)
        self.assertEqual(tx['symbol'], 'DLR.U.TO')

    def test_non_matching_description_left_alone(self):
        # Digital Realty Trust — same ticker, different security.
        tx = {'symbol': 'DLR.US', 'currency': 'USD',
              'description': 'DIGITAL REALTY TRUST INC'}
        apply_security_override(tx, _OVERRIDES)
        self.assertEqual(tx['symbol'], 'DLR.US')

    def test_currency_must_match(self):
        # The CAD leg already parses to DLR.TO correctly; a USD-scoped
        # override must not touch it.
        tx = {'symbol': 'DLR.TO', 'currency': 'CAD',
              'description': 'GLOBAL X US DLR CURRENCY ETF UNIT CL A'}
        apply_security_override(tx, _OVERRIDES)
        self.assertEqual(tx['symbol'], 'DLR.TO')

    def test_wildcard_currency_matches_any(self):
        ov = [("us dlr currency etf", "*", "DLR.U.TO")]
        for cur in ("USD", "CAD"):
            tx = {'symbol': 'X', 'currency': cur,
                  'description': 'GLOBAL X US DLR CURRENCY ETF'}
            apply_security_override(tx, ov)
            self.assertEqual(tx['symbol'], 'DLR.U.TO')

    def test_empty_overrides_is_noop(self):
        tx = {'symbol': 'DLR.US', 'currency': 'USD', 'description': 'anything'}
        apply_security_override(tx, [])
        self.assertEqual(tx['symbol'], 'DLR.US')

    def test_first_match_wins(self):
        ov = [("currency etf", "USD", "FIRST"),
              ("us dlr currency etf", "USD", "SECOND")]
        tx = {'symbol': 'X', 'currency': 'USD',
              'description': 'GLOBAL X US DLR CURRENCY ETF'}
        apply_security_override(tx, ov)
        self.assertEqual(tx['symbol'], 'FIRST')


class TestLoadSecurityOverrides(unittest.TestCase):
    def test_parses_and_skips_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'ticker.map'
            f.write_text(
                "# a comment\n"
                "\n"
                "GLOBAL OLD.TO NEW.TO\n"
                "EXTRACT US DLR CURRENCY ETF | USD | DLR.U.TO\n"
                "  EXTRACT Some Fund | * | FUND.TO  \n"
            )
            overrides = load_security_overrides(f)
            self.assertEqual(overrides, [
                ("us dlr currency etf", "USD", "DLR.U.TO"),
                ("some fund", "*", "FUND.TO"),
            ])

    def test_malformed_line_is_an_error(self):
        # A skipped rule changes ACB pooling silently (audit S001-01):
        # the line is refused, named by its number.
        from taxjson.bin.taxjson_brokerage import SecurityOverrideError
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'ticker.map'
            # Missing the symbol field.
            f.write_text("EXTRACT US DLR CURRENCY ETF | USD\n"
                         "EXTRACT Good Fund | CAD | GF.TO\n")
            with self.assertRaises(SecurityOverrideError) as cm:
                load_security_overrides(f)
            self.assertIn('ticker.map:1:', str(cm.exception))
            # An old-format line (no keyword) is refused too.
            f.write_text("Good Fund | CAD | GF.TO\n")
            with self.assertRaises(SecurityOverrideError) as cm:
                load_security_overrides(f)
            self.assertIn('no ticker.map keyword', str(cm.exception))

if __name__ == '__main__':
    unittest.main()
