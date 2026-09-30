"""Questrade and RBC Direct parser fixes from the 2026-09 audit, low
round (area rbcqt). Every fixture is SYNTHETIC: fake account ids
(55500001), invented tickers and codes.
"""
import unittest

from taxjson.lib.brokerages.base import (BrokerageParseError,
                                         _parse_div_qty_rate)
from tax_rules import rule
from test_fix_rbcqt import q, qdiv, qt_parse, rrow, rbc_parse, of


# ------------------------------------------- numbers in description text

class TestDescriptionNumbers(unittest.TestCase):
    """S062-13 / S064-19 / S016-01 / S062-20 / S064-21: a number inside a
    description is read with thousands commas only; a decimal comma is
    refused (money, share counts) or left unset (informational), never
    stripped into a 10x-100x value or cut at the comma."""

    def test_questrade_split_base_with_decimal_comma_is_refused(self):
        buy = q(td='2025-01-06', sym='QZS', desc='QZS CORP', qty='10',
                price='10', gross='-100', comm='0', net='-100', cur='CAD')
        split = q(td='2025-03-03', action='DIS', sym='QZS',
                  desc='QZS CORP STK SPLIT ON 1,5 SHS', qty='9', price='0',
                  gross='0', comm='0', net='0', cur='CAD', act='Dividends')
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(buy + split)
        self.assertIn('1,5', str(cm.exception))

    def test_questrade_split_base_with_thousands_comma_reads(self):
        buy = q(td='2025-01-06', sym='QZS', desc='QZS CORP', qty='1000',
                price='10', gross='-10000', comm='0', net='-10000', cur='CAD')
        split = q(td='2025-03-03', action='DIS', sym='QZS',
                  desc='QZS CORP STK SPLIT ON 1,000 SHS', qty='1000',
                  price='0', gross='0', comm='0', net='0', cur='CAD',
                  act='Dividends')
        txs, _, _ = qt_parse(buy + split)
        self.assertEqual(of(txs, action='SPLIT')[0]['quantity'], 2.0)

    def test_questrade_reinvestment_price_with_thousands_comma(self):
        rei = q(td='2025-03-03', action='REI', sym='QZR',
                desc='QZR FUND REINV@C$1,234.5678', qty='1', price='0',
                gross='0', comm='0', net='-1234.57', cur='CAD',
                act='Dividend reinvestment')
        txs, _, _ = qt_parse(rei)
        self.assertAlmostEqual(txs[0]['price'], 1234.5678)
        self.assertAlmostEqual(txs[0]['net_amount'], 1234.57)

    def test_dividend_share_count_with_decimal_comma_is_left_unset(self):
        self.assertEqual(_parse_div_qty_rate('CASH DIV ON 1,5 SHS', 1.5),
                         (0.0, 0.0))
        self.assertEqual(_parse_div_qty_rate('CASH DIV ON 1,000 SHS', 10)[0],
                         1000.0)

    def _roc(self, amount_text):
        return (rrow("March 3, 2025", "Buy", "QZE", "QZE FUND", "100", "10",
                     "-1009.95", "CAD", "QZE FUND")
                + rrow("June 2, 2025", "Return of Capital", "QZE", "QZE FUND",
                       "0", "", "0", "CAD", "QZE FUND RETURN OF CAPITAL "
                       f"ADJUSTMENT TO BOOK COST ${amount_text}"))

    def test_rbc_book_cost_with_decimal_comma_is_refused(self):
        for bad in ('1,16', '12,50', '5293,06'):
            with self.subTest(amount=bad):
                with self.assertRaises(BrokerageParseError) as cm:
                    rbc_parse(self._roc(bad))
                self.assertIn(bad, str(cm.exception))

    def test_rbc_book_cost_with_thousands_comma_reads(self):
        txs, _, _ = rbc_parse(self._roc('5,293.06'))
        self.assertEqual([t['net_amount'] for t in of(txs, action='ADJUST')],
                         [-5293.06])
        txs, _, _ = rbc_parse(self._roc('1.16'))
        self.assertEqual([t['net_amount'] for t in of(txs, action='ADJUST')],
                         [-1.16])

    def test_rbc_split_base_with_decimal_comma_is_refused(self):
        body = (rrow("March 3, 2025", "Buy", "QZS", "QZS CORP", "100", "10",
                     "-1009.95", "CAD", "QZS CORP")
                + rrow("June 2, 2025", "Reorganization", "QZS", "QZS CORP",
                       "-80", "", "0", "CAD",
                       "DIS - QZS CORP REVERSE SPLIT ON 100,0 SHS"))
        with self.assertRaises(BrokerageParseError):
            rbc_parse(body)

    def test_rbc_transfer_book_value_with_decimal_comma_is_refused(self):
        body = rrow("June 2, 2025", "Transfers", "QZT", "QZT CORP", "10", "",
                    "0", "CAD", "TFI - QZT CORP ACCOUNT TRANSFER BOOK VALUE "
                    "1234,56")
        with self.assertRaises(BrokerageParseError):
            rbc_parse(body)
        ok = rrow("June 2, 2025", "Transfers", "QZT", "QZT CORP", "10", "",
                  "0", "CAD", "TFI - QZT CORP ACCOUNT TRANSFER BOOK VALUE "
                  "1,234.56")
        txs, _, _ = rbc_parse(ok)
        self.assertEqual(txs[0]['book_value'], 1234.56)

    def test_rbc_reinvestment_price_with_thousands_comma(self):
        body = rrow("June 2, 2025", "Dividends", "QZR", "QZR FUND", "2", "",
                    "-2469.12", "CAD", "REI - QZR FUND REINV@C$1,234.56")
        txs, err, _ = rbc_parse(body)
        buy = of(txs, action='BUYSELL')[0]
        self.assertAlmostEqual(buy['price'], 1234.56)
        self.assertAlmostEqual(buy['gross_amount'], 2469.12)


# ------------------------------------------- settle date before the trade

class TestSettleBeforeTrade(unittest.TestCase):
    """R1-75 / S065-05: a printed settlement date earlier than the trade
    date moved the disposition into the prior tax year silently."""

    @rule("CA-DATE-03")
    @rule("US-DATE-04")
    def test_questrade_settle_before_trade_is_refused(self):
        bad = q(td='2025-01-03', sd='2024-12-30', action='Sell', qty='-10',
                price='50', gross='500', net='495.05')
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(bad)
        self.assertIn('2024-12-30', str(cm.exception))
        txs, _, _ = qt_parse(q(td='2025-01-03', sd='2025-01-06'))
        self.assertEqual(txs[0]['date_settle'], '2025-01-06')

    @rule("CA-DATE-03")
    @rule("US-DATE-04")
    def test_rbc_settle_before_trade_is_refused(self):
        buy = rrow("June 13, 2024", "Buy", "QZB", "QZB CORP", "100", "10",
                   "-1009.95", "CAD", "QZB CORP", settle="June 14, 2024")
        sell = rrow("January 13, 2025", "Sell", "QZB", "QZB CORP", "-100",
                    "6", "590.05", "CAD", "QZB CORP",
                    settle="December 30, 2024")
        with self.assertRaises(BrokerageParseError) as cm:
            rbc_parse(buy + sell)
        self.assertIn('2024-12-30', str(cm.exception))

    def test_questrade_blank_settle_option_is_t_plus_1(self):
        """R1-75's other half (fixed in the medium round, R1-194)."""
        opt = q(td='2023-12-28', sd='', sym='QZA23Dec29C10.00',
                desc='CALL QZA 12/29/23 10 QZA CORP', qty='1', price='1',
                gross='-100', comm='-1', net='-101')
        txs, _, _ = qt_parse(opt)
        self.assertEqual(txs[0]['date_settle'], '2023-12-29')


# --------------------------------------------------- Questrade row shapes

class TestQtRowShapesLow(unittest.TestCase):

    def test_adr_custody_fee_real_wording_binds_the_ticker(self):
        """R1-77: Questrade's real text is '500 SHARES XPEV'."""
        fee = q(td='2025-09-16', action='FCH', sym='',
                desc='ADR CUSTODY FEE 500 SHARES QZPV RECORD DATE 8/1/25',
                qty='0', price='0', gross='0', comm='0', net='-10.00',
                act='Fees and rebates')
        txs, _, _ = qt_parse(fee)
        self.assertEqual([(t['action'], t['symbol'], t['net_amount'])
                          for t in txs], [('FEE', 'QZPV.US', 10.0)])

    def test_a_swallowed_row_is_refused(self):
        """S062-07: an unescaped quote swallows the next row and shifts
        its cells into this row's money columns."""
        good = q(sym='QZA', desc='QZA CORP')
        broken = good.replace('QZA CORP', '"QZA CORP ""', 1)
        nxt = q(sym='QZD', desc='"QZD CORP"', qty='50', price='20',
                gross='-1000', comm='0', net='-1000')
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(broken + nxt + q(sym='QZG', desc='QZG CORP'))
        self.assertIn('line', str(cm.exception))

    def test_extra_cells_are_refused(self):
        body = q(sym='QZA', desc='QZA CORP').rstrip('\n') + ',EXTRA\n'
        with self.assertRaises(BrokerageParseError):
            qt_parse(body)

    def test_cash_dividend_mentioning_a_split_stays_a_dividend(self):
        """S063-07."""
        div = qdiv('QZA', 'QZA CORP CASH DIV ON 100 SHS POST STOCK SPLIT',
                   '25.00', cur='CAD')
        txs, err, _ = qt_parse(div)
        self.assertEqual([(t['action'], t['net_amount']) for t in txs],
                         [('DIVIDEND', 25.0)], err)

    def test_real_split_row_still_splits(self):
        buy = q(td='2025-01-06', sym='QZS', desc='QZS CORP', qty='10',
                price='10', gross='-100', comm='0', net='-100', cur='CAD')
        split = q(td='2025-03-03', action='DIS', sym='QZS',
                  desc='QZS CORP STK SPLIT ON 10 SHS', qty='10', price='0',
                  gross='0', comm='0', net='0', cur='CAD', act='Dividends')
        txs, _, _ = qt_parse(buy + split)
        self.assertEqual(of(txs, action='SPLIT')[0]['quantity'], 2.0)

    def test_reinvestment_with_units_and_no_cash_is_refused(self):
        """S063-06: it was skipped as 'no shares/cost' and the units
        were lost (phantom short at the next sale)."""
        rei = q(td='2025-03-03', action='REI', sym='QZR',
                desc='QZR FUND REINV@C$10.00000', qty='5', price='0',
                gross='0', comm='0', net='0', cur='CAD',
                act='Dividend reinvestment')
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(rei)
        self.assertIn('no cash', str(cm.exception))

    def test_internal_code_warning_says_it_is_moot_once_mapped(self):
        """S063-10: the parse-time warning cannot see ticker.map."""
        xfer = q(td='2026-02-02', action='TF6', sym='R223608',
                 desc='QZF HOLDINGS TRANSFER BOOK VALUE 1000.00', qty='10',
                 price='0', gross='0', comm='0', net='0', cur='CAD',
                 act='Transfers')
        _, err, _ = qt_parse(xfer, taxable=False)
        self.assertIn("Unless ticker.map already maps R223608.TO", err)


if __name__ == '__main__':
    unittest.main()
