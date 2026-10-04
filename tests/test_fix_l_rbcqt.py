"""Questrade and RBC Direct parser fixes from the 2026-09 audit, low
round (area rbcqt). Every fixture is SYNTHETIC: fake account ids
(55500001), invented tickers and codes.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import (BrokerageParseError,
                                         _parse_div_qty_rate)
from tax_rules import rule
from test_fix_rbcqt import q, qdiv, qt_parse, rrow, rbc_parse, of, RH

REPO = Path(__file__).resolve().parent.parent


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
        """R1-77: Questrade's wording is '<N> SHARES <TICKER>'."""
        fee = q(td='2025-10-14', action='FCH', sym='',
                desc='ADR CUSTODY FEE 400 SHARES QZPV RECORD DATE 9/2/25',
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
        xfer = q(td='2026-02-02', action='TF6', sym='X000002',
                 desc='QZF HOLDINGS TRANSFER BOOK VALUE 1000.00', qty='10',
                 price='0', gross='0', comm='0', net='0', cur='CAD',
                 act='Transfers')
        _, err, _ = qt_parse(xfer, taxable=False)
        self.assertIn("Unless ticker.map already maps X000002.TO", err)


# ------------------------------------------------------------- RBC rows

class TestRbcRowsLow(unittest.TestCase):

    def test_holdings_export_is_named(self):
        """R1-332: a clean refusal that names the report type."""
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        body = ('Holdings Export as of Jun 20, 2025\n'
                '"Symbol","Quantity","Market Value"\n"QZH","10","100.00"\n')
        with self.assertRaises(RbcFormatError) as cm:
            rbc_parse(body, raw=True)
        self.assertIn('Holdings export', str(cm.exception))

    def test_split_with_blank_quantity_names_the_quantity(self):
        """S063-20: the warning blamed a missing 'ON N SHS' base."""
        body = (rrow("March 3, 2025", "Buy", "QZQ", "QZQ CORP", "300", "10",
                     "-3009.95", "CAD", "QZQ CORP")
                + rrow("June 13, 2025", "Reorganization", "QZQ", "QZQ CORP",
                       "", "", "0", "CAD",
                       "DIS - QZQ CORP STK SPLIT ON 300 SHS"))
        txs, err, _ = rbc_parse(body)
        self.assertEqual(of(txs, action='SPLIT'), [])
        self.assertIn('UNBOOKED', err)
        self.assertIn('no Quantity', err)
        self.assertNotIn("without a usable 'ON N SHS'", err)

    def test_spinoff_note_is_country_neutral(self):
        """S064-20: the note cited ITA s.86.1 and told a non-Canadian
        project to add a .tt row the corp-actions stage already books."""
        body = (rrow("March 3, 2025", "Buy", "QZN", "QZN CORP", "100", "10",
                     "-1009.95", "USD", "QZN CORP")
                + rrow("June 2, 2025", "Reorganization", "QZV", "QZV CORP",
                       "30", "", "0", "USD", "DIS - QZN CORP SPINOFF ON 100 "
                       "SHS FROM SEC# 123 QZN CORP"))
        _, err, _ = rbc_parse(body)
        self.assertIn('spin-off', err)
        self.assertNotIn('s.86.1', err)
        self.assertNotIn('canada', err)
        self.assertIn('do not also enter', err)

    def test_option_transfer_takes_the_contract_symbol(self):
        """S065-02: an in-kind option transfer kept the RBC code."""
        body = (rrow("March 3, 2025", "Buy", "8QZQQQ1",
                     "CALL .QZT 01/15/27 21 QZT CORP", "2", "1.40", "-291.95",
                     "USD", "CALL .QZT 01/15/27 21 QZT CORP")
                + rrow("June 2, 2025", "Transfers", "8QZQQQ1",
                       "CALL .QZT 01/15/27 21 QZT CORP", "-2", "", "0", "USD",
                       "TFO - CALL .QZT 01/15/27 21 ACCOUNT TRANSFER"))
        txs, err, _ = rbc_parse(body)
        syms = {t['action']: t['symbol'] for t in txs}
        self.assertEqual(syms['TRANSFER'], syms['BUYSELL'])
        self.assertEqual(syms['TRANSFER'], 'QZT270115C00021000.US')
        self.assertEqual(of(txs, action='TRANSFER')[0]['multiplier'], 100.0)
        self.assertNotIn('internal code', err)


# ------------------------------------------- coverage pins (tests only)

def rbc_cli(body, *extra):
    """taxjson-brokerage --brokerage rbc_direct on one file: (rc, err)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'rbc.csv'
        p.write_text(RH + body, encoding='utf-8')
        r = subprocess.run(
            [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
             '--brokerage', 'rbc_direct', '--account', 'margin', *extra,
             str(p)], capture_output=True, text=True,
            env={**os.environ, 'PYTHONPATH': str(REPO / 'src')})
    return r.returncode, r.stderr


REV_SPLIT = (
    rrow("November 20, 2024", "Reorganization", "QZE", "QZE MINI TR ETF",
         "70", "", "0", "USD", "REV - QZE MINI TR ETF AS OF 11/20/24")
    + rrow("November 20, 2024", "Reorganization", "G012345", "", "-700", "",
           "0", "USD", "REV - QZE MINI TR ETF SHARES REV SPLIT TO QZE MINI "
           "TR ETF; 1 FOR 10")
    + rrow("November 1, 2024", "Buy", "QZE", "QZE MINI TR ETF", "700", "3",
           "-2109.95", "USD", "QZE MINI TR ETF UNSOLICITED DA"))


class TestRbcLint(unittest.TestCase):
    """R1-316: --lint row accounting on a clean reorganization statement,
    with and without a skipped (non-event) row."""

    def test_clean_reorg_statement_is_lint_clean(self):
        rc, err = rbc_cli(REV_SPLIT, '--lint')
        self.assertEqual(rc, 0, err)
        self.assertIn('rows=3 consumed=3 skipped=0 unaccounted=0', err)
        self.assertNotIn('internal code', err)

    def test_skipped_rows_are_accounted(self):
        cash = rrow("November 25, 2024", "Deposits & Contributions", "", "",
                    "", "", "500.00", "USD", "CONTRIBUTION")
        rc, err = rbc_cli(cash + REV_SPLIT, '--lint')
        self.assertEqual(rc, 0, err)
        self.assertIn('rows=4 consumed=3 skipped=1 unaccounted=0', err)


class TestRbcGuardsBothSides(unittest.TestCase):
    """S064-01 / S064-18 / S064-06 / S064-02: refusals and warnings
    pinned on both operands and both signs."""

    def test_no_currency_with_shares_only_or_cash_only_is_refused(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        for qty, value in (("100", "0"), ("", "-250.00")):
            with self.subTest(qty=qty, value=value):
                with self.assertRaises(RbcFormatError):
                    rbc_parse(rrow("June 2, 2025", "Buy", "QZX", "QZX CORP",
                                   qty, "", value, "", "QZX CORP"))

    def test_income_row_with_either_sign_of_quantity_is_refused(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        for qty in ("5", "-5"):
            with self.subTest(qty=qty):
                with self.assertRaises(RbcFormatError):
                    rbc_parse(rrow("June 2, 2025", "Dividends", "QZX",
                                   "QZX CORP", qty, "", "12.00", "CAD",
                                   "DIV - QZX CORP CASH DIV ON 100 SHS"))

    def test_reinvestment_without_units_or_without_cash_is_refused(self):
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        for qty, value in (("2", "0"), ("0", "-5.20")):
            with self.subTest(qty=qty, value=value):
                with self.assertRaises(RbcFormatError):
                    rbc_parse(rrow("June 2, 2025", "Dividends", "QZX",
                                   "QZX CORP", qty, "", value, "CAD",
                                   "REI - QZX CORP REINV@C$2.60"))

    def test_unclassified_rows_that_remove_shares_or_debit_cash_warn(self):
        for qty, value in (("10", "0"), ("-10", "0"), ("", "-40.00")):
            with self.subTest(qty=qty, value=value):
                txs, err, pars = rbc_parse(rrow(
                    "June 2, 2025", "Mystery", "QZX", "QZX CORP", qty, "",
                    value, "CAD", "QZX CORP SOMETHING NEW"))
                self.assertEqual(txs, [])
                self.assertEqual(err.count('UNCLASSIFIED row'), 1, err)
                self.assertEqual(len(pars[0].lint_findings), 1)

    def test_transfer_out_with_rbc_negative_quantity(self):
        """RBC exports TFO/TFR quantities negative (fixtures had them
        positive only)."""
        txs, _, _ = rbc_parse(rrow(
            "June 2, 2025", "Transfers", "QZX", "QZX CORP", "-300", "", "0",
            "CAD", "TFO - QZX CORP ACCOUNT TRANSFER BOOK VALUE 3000.00"))
        self.assertEqual([(t['action'], t['quantity']) for t in txs],
                         [('TRANSFER', -300.0)])

    def test_rejoined_row_count_is_reported(self):
        body = (RH.rstrip('\n') + '\n'
                + rrow("June 2, 2025", "Buy", "QZX", "QZX CORP", "10", "5",
                       "-59.95", "CAD", "QZX CORP").rstrip('\n')[:-1]
                + ' BAL",31,498-\n')
        body = body.replace('"QZX CORP BAL",31,498-',
                            '"QZX CORP BAL   31",498-')
        txs, err, _ = rbc_parse(body, raw=True)
        self.assertEqual(len(txs), 1)
        self.assertIn('1 row(s) had an unquoted comma inside the '
                      'Description', err)


class TestExpiryAndAssignmentOrder(unittest.TestCase):
    """S062-23 / S063-14 / S064-03: intra-day stamps the engine orders
    by."""

    @rule("CA-DATE-08")
    @rule("US-DATE-08")
    def test_questrade_same_day_write_then_expiry(self):
        write = q(td='2025-03-21', sd='2025-03-24', action='Sell', sym='',
                  desc='CALL QZA 03/21/25 10 QZA CORP', qty='-1',
                  price='2.95', gross='295', comm='-4.95', net='290.05')
        write = write.replace('2025-03-21 12:00:00 AM',
                              '2025-03-21 10:15:00 AM', 1)
        exp = q(td='2025-03-24', sd='', action='EXP', sym='',
                desc='CALL QZA 03/21/25 10 QZA CORP OPTION EXPIRATION - '
                     'EXPIRED', qty='1', price='0', gross='0', comm='0',
                net='0')
        txs, _, _ = qt_parse(write + exp)
        w, e = sorted(txs, key=lambda t: t['quantity'])
        self.assertEqual((w['date'], w['time']), ('2025-03-21', '10:15:00'))
        self.assertEqual((e['date'], e['time']), ('2025-03-21', '16:00:00'))

    @rule("CA-DATE-08")
    @rule("US-DATE-08")
    def test_rbc_same_day_write_then_expiry(self):
        body = (rrow("March 24, 2025", "Reorganization", "8QZQQQ2", "", "1",
                     "", "0", "USD", "EXP - CALL .QZA 03/21/25 10 QZA CORP "
                     "OPTION EXPIRATION - EXPIRED", settle="")
                + rrow("March 21, 2025", "Sell", "8QZQQQ2",
                       "CALL .QZA 03/21/25 10 QZA CORP", "-1", "1.90",
                       "178.80", "USD", "CALL .QZA 03/21/25 10 QZA CORP",
                       settle="March 24, 2025"))
        txs, _, _ = rbc_parse(body)
        w, e = sorted(txs, key=lambda t: t['quantity'])
        self.assertEqual(e['date'], w['date'])
        self.assertEqual(e['time'], '16:00:00')
        self.assertLess(w['time'], e['time'])

    def test_rbc_assignment_group_keeps_the_stock_legs_slot(self):
        """Newest first: the ASN row, a same-day buy, then the stock
        leg. The group takes the stock leg's (earliest) slot, so the buy
        is pooled AFTER the assigned sale."""
        body = (rrow("June 13, 2025", "Other", "8QZQQQ3", "", "1", "", "0",
                     "CAD", "ASN - CALL .QZA 06/13/25 142.50 QZA CORP",
                     settle="June 17, 2025")
                + rrow("June 13, 2025", "Buy", "QZA", "QZA CORP", "100",
                       "145", "-14509.95", "CAD", "QZA CORP UNSOLICITED",
                       settle="June 17, 2025")
                + rrow("June 13, 2025", "Sell", "QZA", "QZA CORP", "-100",
                       "142.50", "14240.05", "CAD", "QZA CORP ASSIGNMENT "
                       "OF OPTION AS OF 06/13/25", settle="June 17, 2025"))
        txs, err, _ = rbc_parse(body)
        asn = of(txs, action='ASSIGN')
        buy = [t for t in txs if t['action'] == 'BUYSELL'
               and t['quantity'] > 0]
        sale = [t for t in txs if t['symbol'] == 'QZA.TO'
                and t['quantity'] < 0]
        self.assertEqual(len(buy), 1, err)
        self.assertEqual(len(sale), 1, err)
        self.assertTrue(asn, err)
        self.assertEqual(asn[0]['time'], sale[0]['time'])
        self.assertLess(sale[0]['time'], buy[0]['time'])


class TestQtMoneyIdentity(unittest.TestCase):
    """S063-05: Net differing from Gross by LESS than the Commission
    (net == gross with a commission: a Net column mapped onto Gross) is
    refused too."""

    def test_gap_smaller_than_the_commission_is_refused(self):
        for net in ('-1004', '-1000'):
            with self.subTest(net=net):
                with self.assertRaises(BrokerageParseError):
                    qt_parse(q(qty='100', price='10', gross='-1000',
                               comm='-9.95', net=net))
        txs, _, _ = qt_parse(q(qty='100', price='10', gross='-1000',
                               comm='-9.95', net='-1009.95'))
        self.assertAlmostEqual(txs[0]['net_amount'], 1009.95)


if __name__ == '__main__':
    unittest.main()
