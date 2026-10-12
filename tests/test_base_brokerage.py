"""Unit tests for BaseBrokerage helpers.

Each helper is exercised through a minimal concrete subclass since
BaseBrokerage.parse_file raises NotImplementedError. The 6 production
parsers already test these indirectly through their fixtures; these
tests pin the helpers' contracts in isolation so refactors don't
silently change behavior.
"""
import unittest

from taxjson.lib.brokerages.base import BaseBrokerage


class _TestBroker(BaseBrokerage):
    DEFAULT_ACCOUNT = "Test"

    def parse_file(self, path):
        return []


class TestParseOptionFromDescription(unittest.TestCase):
    def setUp(self):
        self.b = _TestBroker()

    def test_basic_call(self):
        result = self.b.parse_option_from_description("CALL AAPL 06/20/25 150.00")
        self.assertIsNotNone(result)
        self.assertEqual(result['right'], 'C')
        self.assertEqual(result['base'], 'AAPL')
        self.assertEqual(result['expiry'], '06/20/25')
        self.assertEqual(result['strike'], '150.00')

    def test_basic_put(self):
        result = self.b.parse_option_from_description("PUT TSLA 12/15/25 200.00")
        self.assertIsNotNone(result)
        self.assertEqual(result['right'], 'P')
        self.assertEqual(result['base'], 'TSLA')

    def test_rbc_exp_prefix(self):
        """RBC option-leg description: 'EXP - CALL .QQZ 08/22/25 31 QQZ HOLDINGS ...'"""
        result = self.b.parse_option_from_description("EXP - CALL .QQZ   08/22/25    31 QQZ HOLDINGS INC")
        self.assertIsNotNone(result)
        self.assertEqual(result['base'], 'QQZ')  # dot stripped

    def test_rbc_assignment_of_option_pattern(self):
        result = self.b.parse_option_from_description(
            "QZCN GLOBAL INC ASSIGNMENT OF OPTION CALL QZCN 08/22/25 142.50"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result['base'], 'QZCN')

    def test_no_match_returns_none(self):
        self.assertIsNone(self.b.parse_option_from_description(""))
        self.assertIsNone(self.b.parse_option_from_description("APPLE INC"))
        self.assertIsNone(self.b.parse_option_from_description(None))


class TestFormatOccSymbol(unittest.TestCase):
    def setUp(self):
        self.b = _TestBroker()

    def test_basic_call(self):
        self.assertEqual(
            self.b.format_occ_symbol('C', 'AAPL', '06/20/25', '150.00'),
            'AAPL250620C00150000'
        )

    def test_basic_put(self):
        self.assertEqual(
            self.b.format_occ_symbol('P', 'TSLA', '12/15/25', '200.50'),
            'TSLA251215P00200500'
        )

    def test_single_digit_month_day_padded(self):
        self.assertEqual(
            self.b.format_occ_symbol('C', 'NVDA', '3/5/24', '50'),
            'NVDA240305C00050000'
        )

    def test_fractional_strike(self):
        self.assertEqual(
            self.b.format_occ_symbol('C', 'SHOP', '01/17/25', '0.5'),
            'SHOP250117C00000500'
        )


class TestApplyCurrencySuffix(unittest.TestCase):
    def setUp(self):
        self.b = _TestBroker()

    def test_usd_appends_us(self):
        self.assertEqual(self.b.apply_currency_suffix('AAPL', 'USD'), 'AAPL.US')

    def test_cad_appends_to(self):
        self.assertEqual(self.b.apply_currency_suffix('SHOP', 'CAD'), 'SHOP.TO')

    def test_existing_suffix_stripped_first(self):
        self.assertEqual(self.b.apply_currency_suffix('SHOP.TO', 'CAD'), 'SHOP.TO')
        self.assertEqual(self.b.apply_currency_suffix('AAPL.US', 'CAD'), 'AAPL.TO')

    def test_spaces_become_dots(self):
        self.assertEqual(self.b.apply_currency_suffix('RY B', 'CAD'), 'RY.B.TO')

    def test_empty_symbol_unchanged(self):
        self.assertEqual(self.b.apply_currency_suffix('', 'USD'), '')

    def test_unknown_currency_echoes_code(self):
        # No fallback set on the test broker → use the currency code itself.
        self.assertEqual(self.b.apply_currency_suffix('SOMETHING', 'JPY'), 'SOMETHING.JPY')

    def test_subclass_can_override_fallback(self):
        class _USFallback(BaseBrokerage):
            CURRENCY_EXT_MAP = {'USD': 'US', 'CAD': 'TO'}
            CURRENCY_EXT_FALLBACK = 'US'
            def parse_file(self, path):
                return []
        self.assertEqual(_USFallback().apply_currency_suffix('FOO', 'JPY'), 'FOO.US')


class TestSignedQuantity(unittest.TestCase):
    def test_buy_returns_positive(self):
        self.assertEqual(BaseBrokerage.signed_quantity(100, action_is_sell=False), 100)
        self.assertEqual(BaseBrokerage.signed_quantity(-100, action_is_sell=False), 100)

    def test_sell_returns_negative(self):
        self.assertEqual(BaseBrokerage.signed_quantity(100, action_is_sell=True), -100)
        self.assertEqual(BaseBrokerage.signed_quantity(-100, action_is_sell=True), -100)


class TestCleanNumber(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(BaseBrokerage.clean_number('100'), 100.0)
        self.assertEqual(BaseBrokerage.clean_number('100.50'), 100.50)

    def test_commas_stripped(self):
        self.assertEqual(BaseBrokerage.clean_number('1,000.50'), 1000.50)

    def test_currency_symbols_stripped(self):
        self.assertEqual(BaseBrokerage.clean_number('$100'), 100.0)
        self.assertEqual(BaseBrokerage.clean_number('€50.50'), 50.50)
        self.assertEqual(BaseBrokerage.clean_number('£25'), 25.0)

    def test_parens_are_negative(self):
        """RE-PREMISED (2026-09 parse hardening): accounting parentheses
        are a NEGATIVE amount. The old magnitude reading flipped the
        sign of every parenthesized cell whose parser did not abs() it;
        Webull (the one real user of parens, "(1,234.56)" on buys) now
        takes abs() itself, so its output is unchanged."""
        self.assertEqual(BaseBrokerage.clean_number('(1,000.50)'), -1000.50)
        self.assertEqual(BaseBrokerage.clean_number('\u22125'), -5.0)

    def test_empty_returns_default(self):
        self.assertEqual(BaseBrokerage.clean_number(''), 0.0)
        self.assertEqual(BaseBrokerage.clean_number(None), 0.0)
        self.assertEqual(BaseBrokerage.clean_number('', default=-1.0), -1.0)

    def test_garbage_returns_default_with_warning(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(BaseBrokerage.clean_number('not a number'), 0.0)
        self.assertIn("not a number", err.getvalue())


class TestParseStrictNumber(unittest.TestCase):
    """REQUIRED money/quantity cells: nothing ambiguous reads as 0."""

    def test_accepted_forms(self):
        from taxjson.lib.brokerages.base import parse_strict_number as p
        for raw, want in (('1234.5', 1234.5), ('-1,234.50', -1234.5),
                          ('+3', 3.0), ('.5', 0.5), ('1e-05', 1e-05),
                          ('$-12.00', -12.0), ('-$12.00', -12.0),
                          ('(1,234.56)', -1234.56), ('\u221212.5', -12.5),
                          (' 10,000 ', 10000.0), ('0', 0.0)):
            with self.subTest(raw=raw):
                self.assertEqual(p(raw), want)

    def test_refused_forms(self):
        from taxjson.lib.brokerages.base import (BrokerageParseError,
                                                 parse_strict_number as p)
        for raw in ('1234,56', '12,34', '1,2345', '1 000', '5-', 'N/A',
                    '', None, '(-5)', '--', '.', '1,000e3'):
            with self.subTest(raw=raw):
                with self.assertRaises(BrokerageParseError):
                    p(raw, field='Amount', where='x line 3')

    def test_blank_allowed_only_when_asked(self):
        from taxjson.lib.brokerages.base import parse_strict_number as p
        self.assertEqual(p('', allow_blank=True, blank=0.0), 0.0)

    def test_require_columns_names_section_and_columns(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaises(BrokerageParseError) as cm:
            BaseBrokerage.require_columns({'A': 0}, ('A', 'B', ('C', 'D')),
                                          section='Trades')
        msg = str(cm.exception)
        self.assertIn("'Trades'", msg)
        self.assertIn("'B'", msg)
        self.assertIn("'C' or 'D'", msg)


class TestBackComputeFee(unittest.TestCase):
    def setUp(self):
        self.b = _TestBroker()

    def test_basic_equity_fee(self):
        # 100 shares @ $150 = $15000 theoretical. Net $15010 → $10 fee.
        self.assertAlmostEqual(
            self.b.back_compute_fee(100, 150.0, 15010, is_option=False),
            10.0, places=2,
        )

    def test_option_uses_100_multiplier(self):
        # 10 contracts @ $1.40 × 100 = $1400 theoretical. Net $1409.87 → $9.87 fee.
        self.assertAlmostEqual(
            self.b.back_compute_fee(10, 1.40, 1409.87, is_option=True),
            9.87, places=2,
        )

    def test_subcent_residual_returns_zero(self):
        # Theoretical 15000.000, net 15000.001 → 0.001 residual < 0.005.
        self.assertEqual(
            self.b.back_compute_fee(100, 150.0, 15000.001, is_option=False),
            0.0,
        )

    def test_above_25pct_returns_zero(self):
        """Sanity guard: residual > 25% of net is a parsing artifact, not a fee."""
        self.assertEqual(
            self.b.back_compute_fee(1, 100.0, 50.0, is_option=False),
            0.0,
        )


class TestParseDate(unittest.TestCase):
    def test_first_format_wins(self):
        dt = BaseBrokerage.parse_date('2025-01-15', '%Y-%m-%d', '%m/%d/%Y')
        self.assertEqual(dt.year, 2025)
        self.assertEqual(dt.month, 1)

    def test_second_format_fallback(self):
        dt = BaseBrokerage.parse_date('01/15/2025', '%Y-%m-%d', '%m/%d/%Y')
        self.assertEqual(dt.year, 2025)

    def test_no_match_returns_none(self):
        self.assertIsNone(BaseBrokerage.parse_date('not a date', '%Y-%m-%d'))
        self.assertIsNone(BaseBrokerage.parse_date('', '%Y-%m-%d'))


class TestSettlementDateT1(unittest.TestCase):
    def setUp(self):
        self.b = _TestBroker()

    def test_basic_weekday(self):
        # Tuesday → Wednesday (T+1).
        self.assertEqual(
            self.b.settlement_date_t1('2025-01-14', '%Y-%m-%d'),
            '2025-01-15',
        )

    def test_skips_weekend(self):
        # Friday → Monday (skip Sat/Sun).
        self.assertEqual(
            self.b.settlement_date_t1('2025-01-10', '%Y-%m-%d'),
            '2025-01-13',
        )

    def test_skips_holiday(self):
        # Friday before MLK Day → Tuesday (US); the TSX is open that
        # Monday, so a CAD trade settles Monday.
        self.assertEqual(
            self.b.settlement_date_t1('2025-01-17', '%Y-%m-%d'),
            '2025-01-21',
        )
        self.assertEqual(
            self.b.settlement_date_t1('2025-01-17', '%Y-%m-%d',
                                      currency='CAD'),
            '2025-01-20',
        )

    def test_unparseable_returns_input(self):
        # Defensive: don't crash on malformed dates.
        self.assertEqual(
            self.b.settlement_date_t1('garbage', '%Y-%m-%d'),
            'garbage',
        )


if __name__ == '__main__':
    unittest.main()
