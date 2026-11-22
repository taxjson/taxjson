"""The parsers' toolbox (step 5a of the engine refactor): `mask_id`'s
contract as its seven callers rely on it (each one's fallback for an empty
value, whether it strips), the thousands-grouping rule the equity and the
crypto cells share, and the re-export surface the parser modules keep so no
call site moved."""
import unittest

from taxjson.lib.brokerages import _crypto_common, base
from taxjson.parsers.toolbox import errors, numbers
from taxjson.parsers.toolbox.masking import mask_id


class TestMaskId(unittest.TestCase):
    def test_the_rule(self):
        self.assertEqual(mask_id("U1234567"), "U1***")
        self.assertEqual(mask_id("ab"), "ab***")
        self.assertEqual(mask_id("a"), "a***")

    def test_an_empty_value_takes_the_callers_fallback(self):
        for raw in ("", None, "   "):
            self.assertEqual(mask_id(raw), "")
            self.assertEqual(mask_id(raw, empty="?"), "?")
            self.assertEqual(mask_id(raw, empty="(no Account column)"), "(no Account column)")
            self.assertEqual(mask_id(raw, empty="''"), "''")

    def test_whitespace_around_an_id_is_dropped_first(self):
        self.assertEqual(mask_id("  99900001 "), "99***")
        self.assertEqual(mask_id("   "), "")

    def test_the_kraken_parser_has_one_spelling(self):
        # Its messages print an empty id as `''` (the module's own mask,
        # since 5a's review also for the formerly inline spellings, TJ-2063).
        from taxjson.lib.brokerages import kraken
        import inspect
        self.assertEqual(kraken._mask(""), "''")
        self.assertEqual(kraken._mask("ABCDEF"), "AB***")
        self.assertNotIn("[:2]}***", inspect.getsource(kraken))

    def test_a_non_string_is_shown_as_text(self):
        self.assertEqual(mask_id(99900001), "99***")


class TestDescriptions(unittest.TestCase):
    def test_the_reinvest_marker_one_grammar(self):
        from taxjson.parsers.toolbox import descriptions as d
        for text, cur, price in (("DIV REINV@C$1,234.56", "C", "1,234.56"),
                                 ("REINV @ U$ 12.5", "U", "12.5"),
                                 ("REINV@CAD$12.34", "CAD", "12.34"),
                                 ("REINV@12.34", None, "12.34"),
                                 ("REINV@USD12.50", None, None)):
            mc = d.REINV_CUR_RE.search(text)
            self.assertEqual(mc.group(1) if mc else None, cur, text)
            mp = d.REINV_PRICE_RE.search(text)
            self.assertEqual(mp.group(1) if mp else None, price, text)
        self.assertEqual(d.REINV_CUR["CAD"], "CAD")

    def test_the_in_lieu_phrase_names_the_dividend(self):
        from taxjson.parsers.toolbox.descriptions import PIL_DESC_RE
        for text in ("CASH IN LIEU OF DIVIDEND", "Payment in Lieu of a Dividend",
                     "SUBST PAY IN LIEU OF DIV.", "IN LIEU OF DIV 100 SHS",
                     "PAYMENT IN LIEU OF DIVIDENDS"):
            self.assertTrue(PIL_DESC_RE.search(text), text)
        for text in ("Cash in Lieu of Divested Units", "IN LIEU OF DIVISION",
                     "CASH IN LIEU OF FRACTIONAL SHARES"):
            self.assertFalse(PIL_DESC_RE.search(text), text)


class TestNumbers(unittest.TestCase):
    def test_the_equity_and_the_crypto_cells_share_the_grouping_rule(self):
        self.assertEqual(numbers.parse_strict_number("1,234.5"), 1234.5)
        self.assertEqual(numbers.strict_money("1,234.5"), 1234.5)
        with self.assertRaises(errors.BrokerageParseError):
            numbers.parse_strict_number("1234,56")
        with self.assertRaises(ValueError):
            numbers.strict_money("1234,56")
        for bad in ("0,125", "12,34", "1,2345", "１,２３４.５"):
            with self.assertRaises(errors.BrokerageParseError):
                numbers.parse_strict_number(bad)
            with self.assertRaises(ValueError):
                numbers.strict_money(bad)

    def test_the_two_grammars_still_differ_where_they_did(self):
        # Blank: an error for a required equity cell, 0 for a crypto cell.
        with self.assertRaises(errors.BrokerageParseError):
            numbers.parse_strict_number("")
        self.assertEqual(numbers.strict_money(""), 0.0)
        self.assertEqual(numbers.parse_strict_number("", allow_blank=True, blank=0.0), 0.0)
        # Accounting parentheses are negative in both.
        self.assertEqual(numbers.parse_strict_number("(1,234.56)"), -1234.56)
        self.assertEqual(numbers.strict_money("(12.00)"), -12.0)

    def test_a_capture_beyond_float_range_is_refused(self):
        # TJ-2062: 400 digits overflow to inf; a book value or a split
        # ratio would carry it. Refused under the strict flag like a
        # decimal comma; None for an informational field.
        run = "9" * 400
        with self.assertRaises(errors.BrokerageParseError):
            numbers.desc_number(run, where="x.csv line 3", field="book value")
        self.assertIsNone(numbers.desc_number(run, strict=False))
        # A malformed capture (an optional group) is a refusal too (TJ-2359).
        with self.assertRaises(errors.BrokerageParseError):
            numbers.desc_number(".", field="book value")
        self.assertIsNone(numbers.desc_number("", strict=False))
        with self.assertRaises(errors.BrokerageParseError):
            numbers.parse_strict_number(run)
        with self.assertRaises(ValueError):
            numbers.strict_money(run)

    def test_the_parser_modules_re_export_what_they_held(self):
        self.assertIs(base.BrokerageParseError, errors.BrokerageParseError)
        self.assertIs(base.parse_strict_number, numbers.parse_strict_number)
        self.assertIs(base.desc_number, numbers.desc_number)
        self.assertIs(base.check_comma_grouping, numbers.check_comma_grouping)
        self.assertIs(_crypto_common.strict_money, numbers.strict_money)


if __name__ == "__main__":
    unittest.main()
