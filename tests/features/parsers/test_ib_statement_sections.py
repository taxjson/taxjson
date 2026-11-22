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
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
from _support.ib_statement_rows import (
    HEAD, TRADES_H, XFER_H, CA_H, _trade, _xfer, _ca, _parse_ib, _brokerage_cli, _period, FII_H,
    DIV_H, WHT_H, _fii_stock, _parse_account, _fii_opt,
)


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestSchemaTrades(unittest.TestCase):
    """Formerly: test_fix_ibparse.TestSchemaTrades"""
    def _v(self, **tx):
        from taxjson.lib.brokerages.schema import validate_transactions
        base = {'action': 'BUYSELL', 'date': '2025-06-02',
                'symbol': 'ZZZ250620C00050000.TO', 'currency': 'CAD'}
        base.update(tx)
        return validate_transactions([base])

    def test_sell_whose_commission_exceeds_gross_is_legal(self):
        # S017-00: closing an option at 0.01 nets negative proceeds;
        # the engine models it, the schema refused it (every run rc 1).
        errs, _ = self._v(quantity=-1, price=0.01, net_amount=-9.95,
                          fee=10.95)
        self.assertEqual(errs, [])

    def test_negative_buy_net_is_still_an_error(self):
        errs, _ = self._v(quantity=1, price=0.01, net_amount=-9.95)
        self.assertTrue(errs)

    def test_assign_stock_leg_notional_is_checked(self):
        # S017-02: a Webull assignment stock leg with Proceeds x10 off
        # (or blank) booked silently — ASSIGN skipped the check.
        for net in (1900.0, 0.0):
            errs, warns = self._v(action='ASSIGN', symbol='ABBV.US',
                                  currency='USD', quantity=100,
                                  price=190.0, net_amount=net)
            self.assertTrue(any('far from' in w for w in warns + errs),
                            net)
        errs, warns = self._v(action='ASSIGN', symbol='ABBV.US',
                              currency='USD', quantity=100, price=190.0,
                              net_amount=19000.0)
        self.assertEqual((errs, warns), ([], []))


class TestCommaNumbers(unittest.TestCase):
    """Formerly: test_fix_ibparse.TestCommaNumbers"""
    def test_leading_zero_group_is_not_a_thousands_separator(self):
        # S055-08: '0,125' is a decimal comma, never 125.
        from taxjson.lib.brokerages.base import (parse_strict_number,
                                                 check_comma_grouping)
        for bad in ('0,125', '00,125', '-0,500'):
            with self.assertRaises(BrokerageParseError, msg=bad):
                parse_strict_number(bad)
            with self.assertRaises(BrokerageParseError, msg=bad):
                check_comma_grouping(bad)
        self.assertEqual(parse_strict_number('1,250'), 1250.0)
        self.assertEqual(parse_strict_number('0.125'), 0.125)

    def test_sibling_helpers_refuse_leading_zero_group(self):
        from taxjson.lib.brokerages._crypto_common import strict_money
        with self.assertRaises(ValueError):
            strict_money('0,125')
        self.assertEqual(strict_money('1,250.5'), 1250.5)
        from taxjson.lib.brokerages.rbc_direct import rbc_number
        with self.assertRaises(Exception):
            rbc_number('0,125', path=Path('r.csv'), line=1, column='Price')
        from taxjson.bin.xlsx_to_csv import _GROUPED_NUMBER_RE
        self.assertIsNone(_GROUPED_NUMBER_RE.match('0,125'))

    def test_option_strike_with_thousands_separator(self):
        # R1-170: '5,000.00' was cut at the comma -> strike 5.  # pii-ok
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        q = QuestradeBrokerage()
        o = q.parse_option_from_description('CALL SPX 12/19/25 5,000.00')
        self.assertEqual(q.format_occ_symbol(o['right'], o['base'],
                                             o['expiry'], o['strike']),
                         'SPX251219C05000000')
        from taxjson.lib.brokerages.webull import WebullBrokerage
        w = WebullBrokerage()
        a = w.parse_option_from_description('CALL BKNG06/20/25 5,025')
        self.assertEqual(a['strike'], '5025')

    def test_ib_split_ratio_with_thousands_separator(self):
        # S058-19: 'Split 1 for 1,000' was read as 1 for 1.
        body = (HEAD + TRADES_H
                + _trade('QZX', '2025-01-10, 10:00:00', 5500, 1, -5500, -1)
                + CA_H
                + _ca('QZX(US9990000301) Split 1 for 1,000 (QZX, QZX CORP, '
                      'US9990000301)', -5494.5))
        _, txs, _ = _parse_ib(body)
        sp = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(sp), 1)
        self.assertAlmostEqual(sp[0]['quantity'], 0.001)


# ------------------------------------------------ IB coverage / completeness
class TestIbStatementCoverage(unittest.TestCase):
    """R1-2 / R1-195: an IB statement that stops before Dec 31 of a
    finished year, or a gap between an account's statements, is
    reported (it used to parse silently; a Dec 28-31 sale vanished).

    Formerly: test_fix_ibparse.TestIbStatementCoverage"""

    BODY = TRADES_H + _trade('QZAC', '2024-06-03, 10:00:00', 100, 10,
                             -1000, -1)

    def test_statement_ending_early_is_flagged(self):
        ctx_err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'ib_2024.csv'
            p.write_text(HEAD + _period('January 1, 2024',
                                        'December 27, 2024') + self.BODY)
            with contextlib.redirect_stderr(ctx_err):
                IbBrokerage.prepare_files([p])
        self.assertIn('warning: ATTENTION:', ctx_err.getvalue())
        self.assertIn('2024-12-27', ctx_err.getvalue())

    def test_a_later_statement_closes_the_gap(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.csv'
            b = Path(td) / 'b.csv'
            a.write_text(HEAD + _period('January 1, 2024',
                                        'December 27, 2024') + self.BODY)
            b.write_text(HEAD + _period('December 28, 2024',
                                        'January 1, 2025'))
            with contextlib.redirect_stderr(err):
                IbBrokerage.prepare_files([a, b])
        self.assertNotIn('2024-12-27', err.getvalue())

    def test_gap_between_statements_is_flagged(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.csv'
            b = Path(td) / 'b.csv'
            a.write_text(HEAD + _period('January 1, 2023',
                                        'June 30, 2023'))
            b.write_text(HEAD + _period('August 1, 2023',
                                        'December 31, 2023'))
            with contextlib.redirect_stderr(err):
                IbBrokerage.prepare_files([a, b])
        self.assertIn('2023-07-01', err.getvalue())

    def test_full_year_and_mid_year_opening_are_quiet(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.csv'
            a.write_text(HEAD + _period('May 9, 2023', 'December 31, 2023'))
            with contextlib.redirect_stderr(err):
                IbBrokerage.prepare_files([a])
        self.assertNotIn('ATTENTION', err.getvalue())

    def test_run_echoes_attention_lines(self):
        from taxjson.pipeline import diagnostics as pipeline_diagnostics
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'margin_ib.json'
            out.with_name(out.name + '.diag').write_text(
                'note: calm\nwarning: ATTENTION: x.csv ends early\n')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pipeline_diagnostics.echo_parse_stats(out)
        self.assertIn('ATTENTION: x.csv ends early', buf.getvalue())
        self.assertNotIn('calm', buf.getvalue())


class TestIbNoCashReport(unittest.TestCase):
    """R1-53: without a Cash Report the reconciliation was skipped
    without a word.

    Formerly: test_fix_ibparse.TestIbNoCashReport"""

    def test_missing_cash_report_is_said(self):
        _, _, err = _parse_ib(HEAD + TRADES_H + _trade(
            'QZAC', '2025-06-03, 10:00:00', 100, 10, -1000, -1))
        self.assertIn('warning: ATTENTION:', err)
        self.assertIn('no Cash Report', err)


class TestIbUnhandledAssetClass(unittest.TestCase):
    """S060-08: a Bonds / Mutual Funds trade carrying money was only a
    counted skip (fatal only through the Cash Report).

    Formerly: test_fix_ibparse.TestIbUnhandledAssetClass"""

    def test_money_row_in_unknown_category_is_an_error(self):
        body = (HEAD + TRADES_H + _trade('QZBOND', '2025-03-01, 10:00:00',
                                         1000, 1, -1000, -1, cat='Bonds'))
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(body)
        self.assertIn("'Bonds'", str(cm.exception))


class TestIbOrderAndTradeRows(unittest.TestCase):
    """S060-10: every 'Trade' row after the first 'Order' row was
    skipped, for any symbol, and a Trade row listed BEFORE its Order row
    was booked twice.

    Formerly: test_fix_ibparse.TestIbOrderAndTradeRows"""

    def test_trade_only_fill_of_another_symbol_is_booked(self):
        body = (HEAD + TRADES_H
                + _trade('QZAA', '2025-03-12, 10:00:00', 100, 10, -1000, -1)
                + _trade('QZAA', '2025-03-12, 10:00:00', 100, 10, -1000,
                         -1, disc='Trade')
                + _trade('QZBB', '2025-03-13, 11:00:00', 50, 20, -1000, -1,
                         disc='Trade'))
        _, txs, _ = _parse_ib(body)
        self.assertEqual(sorted((t['symbol'], t['quantity']) for t in txs),
                         [('QZAA.US', 100.0), ('QZBB.US', 50.0)])

    def test_trade_row_before_its_order_row_is_not_doubled(self):
        body = (HEAD + TRADES_H
                + _trade('QZAA', '2025-03-12, 10:00:00', 100, 10, -1000, -1,
                         disc='Trade')
                + _trade('QZAA', '2025-03-12, 10:00:00', 100, 10, -1000, -1))
        _, txs, _ = _parse_ib(body)
        self.assertEqual([(t['symbol'], t['quantity']) for t in txs],
                         [('QZAA.US', 100.0)])

    def test_levels_that_disagree_are_an_error(self):
        body = (HEAD + TRADES_H
                + _trade('QZAA', '2025-03-12, 10:00:00', 100, 10, -1000, -1)
                + _trade('QZAA', '2025-03-12, 10:00:00', 60, 10, -600, -1,
                         disc='Trade'))
        with self.assertRaises(BrokerageParseError):
            _parse_ib(body)


class TestIbTransferWithoutQuantity(unittest.TestCase):
    """R1-55: a security transfer with a blank Qty was a calm non-event
    (the position change was lost).

    Formerly: test_fix_ibparse.TestIbTransferWithoutQuantity"""

    def test_blank_qty_on_a_stock_transfer_is_an_error(self):
        body = HEAD + XFER_H + _xfer('QZDD', '2025-06-11', '', 2400,
                                     typ='ATON', cur='CAD')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(body)
        self.assertIn('Qty', str(cm.exception))


# ------------------------------------------------ IB corporate actions
class TestIbOptionsExpirationsCategory(unittest.TestCase):
    """R1-56: the section's rows were all read as equity options, so a
    futures-option expiry became a phantom equity option.

    Formerly: test_fix_ibparse.TestIbOptionsExpirationsCategory"""

    def test_row_category_is_honoured(self):
        fii = (FII_H + 'Financial Instrument Information,Data,Options On '
               'Futures,QZCL FEB26 47 P,QZCL FEB26 47 P,990000071,,QZCL,'
               'NYMEX,1000,2026-01-14,2026-02,P,47,\n')
        body = (HEAD + TRADES_H
                + _trade('QZCL FEB26 47 P', '2025-11-03, 10:00:00', 1, 1.5,
                         -1500, -2, cat='Options On Futures')
                + 'Options Expirations,Header,Asset Category,Currency,'
                  'Symbol,Date/Time,Quantity,Code\n'
                + 'Options Expirations,Data,Options On Futures,USD,'
                  'QZCL FEB26 47 P,"2026-01-14, 16:20:00",-1,C;Ep\n'
                + fii)
        _, txs, _ = _parse_ib(body)
        self.assertEqual({t['symbol'] for t in txs},
                         {'F:QZCL260114P00047000.US'})


class TestIbCorporateActionsNotBooked(unittest.TestCase):
    """Rows neither this parser nor taxjson-corp-actions books are an
    UNBOOKED warning (console, fatal under run --strict), never an
    equity SPLIT on an invented symbol or a silent no-op.

    Formerly: test_fix_ibparse.TestIbCorporateActionsNotBooked"""

    def test_option_adjustment_row_is_not_an_equity_split(self):
        # S058-16
        body = (HEAD + CA_H + _ca(
            'QZNV 21JUN24 1200 C(QZNV 240621C01200000) Split 10 for 1 '
            '(QZNV 21JUN24 1200 C, QZNV CORP, US9990000401)', -1,
            cat='Equity and Index Options'))
        _, txs, err = _parse_ib(body)
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('Equity and Index Options', err)

    def test_stock_dividend_books_the_shares(self):
        # S058-24: the delivered shares were dropped (phantom short).
        body = (HEAD + CA_H + _ca(
            'QZSD(US9990000999) Stock Dividend US9990000999 1 for 20 '
            '(QZSD, QZSD CORP, US9990000999)', 5, value=100))
        _, txs, err = _parse_ib(body)
        self.assertEqual([(t['action'], t['symbol'], t['quantity'],
                           t['net_amount']) for t in txs],
                         [('BUYSELL', 'QZSD.US', 5.0, 0.0)])
        self.assertIn('stock dividend', err)
        self.assertIn('warning: ATTENTION:', err)

    def test_spinoff_on_a_short_parent_is_unbooked(self):
        # S058-22: the debit leg (qty < 0) was an "unhandled non-spinoff".
        body = (HEAD + CA_H + _ca(
            'QZPA(CA9990000001) Spinoff  1 for 5 (QZSP, SPINCO CORP, '
            'CA9990000002)', -20, value=-900, cur='CAD'))
        _, txs, err = _parse_ib(body)
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('short', err)

    def test_share_for_share_tender_is_unbooked(self):
        # S013-06: an allocation delivering ANOTHER security was a no-op.
        rows = (_ca('QZTG(CA9990000011) Tendered to 99999999 1 FOR 1 '
                    '(QZTG.TEN, QZTG CORP - TENDER, CA9990000011)', -1000,
                    cur='CAD')
                + _ca('QZTG.TEN(CA9990000011) Tendered to 99999999 1 FOR 1 '
                      '(QZTG.TEN, QZTG CORP - TENDER, CA9990000011)', 1000,
                      cur='CAD')
                + _ca('QZTG.TEN(99999999) Merged(Voluntary Offer Allocation)'
                      ' WITH CA8880000001 1 for 2 (QZAQ, ACQUIRER INC, '
                      'CA8880000001)', 500, value=25000, cur='CAD')
                + _ca('QZTG.TEN(99999999) Merged(Voluntary Offer Allocation)'
                      ' WITH CA8880000001 1 for 2 (QZTG.TEN, QZTG CORP - '
                      'TENDER, CA9990000011)', -1000, cur='CAD'))
        _, txs, err = _parse_ib(HEAD + CA_H + rows)
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)
        self.assertIn('QZAQ', err)

    def test_same_security_tender_round_trip_stays_a_no_op(self):
        rows = (_ca('QZAU(CA9990000021) Tendered to 99999998 1 FOR 1 '
                    '(QZAU.TEN, QZAU CORP - TENDER, CA9990000021)', -100,
                    cur='CAD')
                + _ca('QZAU.TEN(CA9990000021) Tendered to 99999998 1 FOR 1 '
                      '(QZAU.TEN, QZAU CORP - TENDER, CA9990000021)', 100,
                      cur='CAD')
                + _ca('QZAU.TEN(99999998) Merged(Voluntary Offer Allocation)'
                      ' WITH CA9990000021 1 for 1 (QZAU, QZAU CORP, '
                      'CA9990000021)', 100, cur='CAD')
                + _ca('QZAU.TEN(99999998) Merged(Voluntary Offer Allocation)'
                      ' WITH CA9990000021 1 for 1 (QZAU.TEN, QZAU CORP - '
                      'TENDER, CA9990000021)', -100, cur='CAD'))
        _, txs, err = _parse_ib(HEAD + CA_H + rows)
        self.assertEqual(txs, [])
        self.assertNotIn('UNBOOKED', err)
        self.assertIn('recognized no-op', err)


# ------------------------------------------------ IB security identity
class TestIbIncomeRebindNeedsTheSameIsin(unittest.TestCase):
    """S059-24 / S060-19: a dividend was rebound to whatever listing of
    the same ROOT was held — a US issuer's (US ISIN) onto an unrelated
    TSX listing of that root.

    Formerly: test_fix_ibparse.TestIbIncomeRebindNeedsTheSameIsin"""

    def _body(self, fii_isin):
        return (HEAD + TRADES_H
                + _trade('QZT', '2025-02-10, 10:00:00', 120, 30, -3600, -1,
                         cur='CAD')
                + DIV_H + 'Dividends,Data,USD,2025-05-01,QZT(US9990000701) '
                          'Cash Dividend USD 0.31 per Share (Ordinary '
                          'Dividend),6200\n'
                + WHT_H + 'Withholding Tax,Data,USD,2025-05-01,QZT('
                          'US9990000701) Cash Dividend USD 0.31 per Share '
                          '- US Tax,-930.00\n'
                + FII_H + _fii_stock('QZT', fii_isin, exch='TSE'))

    def test_other_issuer_keeps_its_isin_listing(self):
        _, txs, err = _parse_ib(self._body('CA9990000702'))
        inc = {(t['action'], t['symbol']) for t in txs
               if t['action'] in ('DIVIDEND', 'TAX')}
        self.assertEqual(inc, {('DIVIDEND', 'QZT.US'), ('TAX', 'QZT.US')})
        self.assertIn('ISIN', err)

    def test_same_issuer_is_still_rebound(self):
        _, txs, _ = _parse_ib(self._body('US9990000701'))
        inc = {(t['action'], t['symbol']) for t in txs
               if t['action'] in ('DIVIDEND', 'TAX')}
        self.assertEqual(inc, {('DIVIDEND', 'QZT.TO'), ('TAX', 'QZT.TO')})

    def test_no_temporary_keys_leak(self):
        _, txs, _ = _parse_ib(self._body('US9990000701'))
        self.assertFalse(any(k.startswith('_') for t in txs for k in t))


@rule(R.CA_INC_DATE_ROC)
@rule(R.US_ROC_01)
class TestIbIncomeRebindAcrossStatements(unittest.TestCase):
    """S060-00: a statement with only a ROC row (no trades, no Open
    Positions) kept the ISIN suffix: a gain on a phantom .TO listing.

    Formerly: test_fix_ibparse.TestIbIncomeRebindAcrossStatements"""

    def test_holding_from_the_other_statement_rebinds(self):
        a = (HEAD + TRADES_H
             + _trade('QZBT', '2025-03-03, 10:00:00', 1000, 3, -3000, -1))
        b = (HEAD + DIV_H
             + 'Dividends,Data,USD,2026-06-19,QZBT(CA9990000801) Cash '
               'Dividend USD 0.30 per Share (Return of Capital),300\n')
        txs, _ = _parse_account({'a.csv': a, 'b.csv': b})
        roc = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual([t['symbol'] for t in roc], ['QZBT.US'])


class TestIbUsdClassTsxUnits(unittest.TestCase):
    """S010-06: an IB USD trade of a TSX '.U' unit (ZSP.U) became
    ZSP.U.US — a fictional US security; RBC books DLR.U.TO.

    Formerly: test_fix_ibparse.TestIbUsdClassTsxUnits"""

    def test_u_unit_on_tsx_is_to(self):
        body = (HEAD + TRADES_H
                + _trade('QZSP.U', '2025-03-03, 10:00:00', 100, 50, -5000,
                         -1)
                + FII_H + _fii_stock('QZSP.U', 'CA9990000901', exch='TSE'))
        _, txs, _ = _parse_ib(body)
        self.assertEqual([t['symbol'] for t in txs], ['QZSP.U.TO'])

    def test_usd_trade_of_a_tsx_listed_ordinary_share_stays_us(self):
        body = (HEAD + TRADES_H
                + _trade('QZMD', '2025-03-03, 10:00:00', 100, 50, -5000, -1)
                + FII_H + _fii_stock('QZMD', 'CA9990000902', exch='TSE'))
        _, txs, _ = _parse_ib(body)
        self.assertEqual([t['symbol'] for t in txs], ['QZMD.US'])


class TestIbOptionRootAliases(unittest.TestCase):
    """Formerly: test_fix_ibparse.TestIbOptionRootAliases"""
    def test_rename_alias_canonical_is_the_underlying(self):
        # S059-11: a root rename (QZS -> QZXYZ); the shortest root won, so
        # the assigned put never met the delivered shares.
        body = (HEAD + TRADES_H
                + _trade('QZS 16JAN26 44 P', '2025-10-10, 10:00:00', -1, 4,
                         400, 0, cat='Equity and Index Options')
                + _trade('QZXYZ 16JAN26 44 P', '2026-01-16, 16:20:00', 1, 0,
                         0, 0, code='A;C', cat='Equity and Index Options')
                + _trade('QZXYZ', '2026-01-16, 16:20:00', 100, 44, -4400, 0,
                         code='A;O')
                + FII_H + _fii_opt('QZS   260116P00044000, QZXYZ 251219P'
                                   '00060000', 'QZXYZ 16JAN26 44 P',
                                   '990000011', 'QZXYZ'))
        _, txs, _ = _parse_ib(body)
        opts = {t['symbol'] for t in txs if 'P000' in t['symbol']}
        self.assertEqual(opts, {'QZXYZ260116P00044000.US'})

    def test_alias_learned_from_another_statement(self):
        # S059-15: the 2026 statement lists only the adjusted root.
        a = (HEAD + TRADES_H
             + _trade('QZD 16JAN26 44 P', '2025-10-10, 10:00:00', -1, 4, 400,
                      0, cat='Equity and Index Options')
             + FII_H + _fii_opt('QZD   260116P00044000, QZD1  251219P'
                                '00060000', 'QZD 16JAN26 44 P', '990000021',
                                'QZD'))
        b = (HEAD + TRADES_H
             + _trade('QZD1 16JAN26 44 P', '2025-11-10, 10:00:00', 1, 3,
                      -300, 0, code='C', cat='Equity and Index Options')
             + FII_H + _fii_opt('QZD1  260116P00044000', 'QZD1 16JAN26 44 P',
                                '990000021', 'QZD'))
        txs, _ = _parse_account({'a.csv': a, 'b.csv': b})
        self.assertEqual({t['symbol'] for t in txs},
                         {'QZD260116P00044000.US'})


class TestIbStockSymbolAliases(unittest.TestCase):
    """S059-13 / S060-17: one stock conid under two symbols split the
    position into two pools silently.

    Formerly: test_fix_ibparse.TestIbStockSymbolAliases"""

    def test_one_conid_two_symbols_in_a_statement_is_flagged(self):
        body = (HEAD + TRADES_H
                + _trade('QZOL', '2025-02-05, 10:00:00', 500, 30, -15000, -1)
                + _trade('QZNW', '2025-09-10, 10:00:00', -500, 40, 20000, -1)
                + FII_H + _fii_stock('QZOL, QZNW', 'US9990001001',
                                     conid='990000031'))
        _, err = _parse_account({"a.csv": body})
        self.assertIn('warning: ATTENTION:', err)
        self.assertIn('QZOL', err)
        self.assertIn('QZNW', err)

    def test_conid_renamed_between_statements_is_flagged(self):
        a = (HEAD + TRADES_H
             + _trade('QZOL', '2025-02-05, 10:00:00', 500, 30, -15000, -1)
             + FII_H + _fii_stock('QZOL', 'US9990001001', conid='990000031'))
        b = (HEAD + TRADES_H
             + _trade('QZNW', '2026-03-10, 10:00:00', -500, 40, 20000, -1)
             + FII_H + _fii_stock('QZNW', 'US9990001001', conid='990000031'))
        _, err = _parse_account({'a.csv': a, 'b.csv': b})
        self.assertIn('warning: ATTENTION:', err)
        # Booked as the dated event since renames are events (QZNW's
        # first row; one contract id, v0.24).
        self.assertIn('(QZOL.US -> QZNW.US on 2026-03-10)', err)


WB_HEAD = (',,,,,,,,,\n'
           '"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\n'
           'Symbole","Security Description\nDescription des titres",'
           'Type Code of Securities Code de genre de titres,"Quantity of '
           'Securities Quantité\nde titres","Price\nPrix",,Proceeds of '
           'Disposition or Settlement Amount Produits de disposition\n'
           ',,,,,,,,,\n')


class TestExpiryClampAcrossFiles(unittest.TestCase):
    """S055-22: a Dec-31 0DTE trade was clamped to its expiry only when
    the expiry row sat in the same yearly export.

    Formerly: test_fix_ibparse.TestExpiryClampAcrossFiles"""

    def test_expiry_in_the_other_export_still_clamps(self):
        a = WB_HEAD + 'USD,31-12-2025,SELL,,CALL QZQ12/31/25 511,,-4,,,\n'
        b = (WB_HEAD + 'USD,02-01-2026,BUY,,CALL QZQ12/31/25 511,,4,2.35,,'
                       '(943.96)\n')
        rc, out, err, _ = _brokerage_cli({'wb_2025.csv': a, 'wb_2026.csv': b},
                                         brokerage='webull')
        self.assertEqual(rc, 0, err)
        buy = next(t for t in out['transactions'] if t['quantity'] == 4)
        self.assertEqual(buy['date'], '2025-12-31')
        self.assertEqual(buy['date_settle'], '2025-12-31')


class TestIbCorporateActionCancelAcrossStatements(unittest.TestCase):
    """S059-04: a split booked in the 2025 statement and cancelled (Ca)
    and rebooked in the 2026 statement stayed applied (x3 then x2).

    Formerly: test_fix_ibparse.TestIbCorporateActionCancelAcrossStatements"""

    def test_cancel_in_the_next_statement_undoes_the_original(self):
        desc3 = ('QZX(US9990000301) Split 3 for 1 (QZX, QZX CORP, '
                 'US9990000301)')
        desc2 = ('QZX(US9990000301) Split 2 for 1 (QZX, QZX CORP, '
                 'US9990000301)')
        a = (HEAD + TRADES_H
             + _trade('QZX', '2025-06-02, 10:00:00', 100, 10, -1000, -1)
             + CA_H + _ca(desc3, 200, when='2025-12-30, 20:25:00'))
        b = (HEAD + CA_H
             + _ca(desc3, -200, when='2026-01-05, 20:25:00', code='Ca')
             + _ca(desc2, 100, when='2026-01-05, 20:25:00'))
        rc, out, err, _ = _brokerage_cli({'a.csv': a, 'b.csv': b})
        self.assertEqual(rc, 0, err)
        splits = [(t['date'], t['quantity']) for t in out['transactions']
                  if t['action'] == 'SPLIT']
        self.assertEqual(splits, [('2026-01-05', 2.0)])
        self.assertNotIn('nothing undone', err)


if __name__ == '__main__':
    unittest.main()
