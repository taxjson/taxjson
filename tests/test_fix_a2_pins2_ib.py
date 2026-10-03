"""Re-audit-2 test pins for the IB parser (fix lists tests-pins-04/06):
behaviour that held but that no test failed on when it was reverted.
Each test names the finding and the mutant it kills. Every fixture is
synthetic: invented tickers, ISINs and account ids (pii-ok)."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, time
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import (_ib_market_trade_date,
                                                 _warn_coverage_gaps)
from taxjson.lib.check_dates import check_trade_time
from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE, pair_cancellations
from tax_rules import rule

from test_fix_ibparse import (HEAD, TRADES_H, XFER_H, CA_H, _trade, _xfer,
                              _ca, _parse_ib)
from test_fix_l_ibparse import (_PERIOD, _ACC_H, _DIV_H, _acc, _div, _pil,
                                _CIL, _split_desc, _ratios)
from test_fix_a2_ib_dates import FUT_H, FII_H, FUT_FII

ADJ_H = ('Commission Adjustments,Header,Currency,Date,Description,'
         'Amount,Code\n')
CASH_H = 'Cash Report,Header,Currency Summary,Currency,Total,\n'


def _adj(cur, date_, desc, amount):
    return f'Commission Adjustments,Data,{cur},{date_},"{desc}",{amount},\n'


# ------------------------------- A2-0508 / A2-0509 / A2-0876 / A2-0918
class TestOvernightSessionEdges(unittest.TestCase):
    """CA-/US-DATE-SESSION: a US stock fill stamped 20:00 ET or later
    (Sunday to Thursday) trades on the NEXT trading day. Pinned at the
    exact 20:00:00 edge (overnight orders queued before the session
    commonly fill at 20:00:00) and on the eve of NYSE holidays."""

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_fill_at_exactly_2000_is_next_day(self):
        # Mutants: _IB_OVERNIGHT_OPEN '20:00:01'; `>=` -> `>`.
        d, t, stamp = _ib_market_trade_date('2025-12-30', '20:00:00',
                                            'Stocks', 'USD', 'US')
        self.assertEqual((d, t, stamp),
                         ('2025-12-31', '00:00:00', '2025-12-30 20:00:00 ET'))
        self.assertEqual(_ib_market_trade_date(
            '2025-12-30', '19:59:59', 'Stocks', 'USD', 'US'),
            ('2025-12-30', '19:59:59', ''))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_dec30_2000_sale_settles_in_the_next_year(self):
        _, txs, _ = _parse_ib(HEAD + TRADES_H + _trade(
            'QZX', '2025-12-30, 20:00:00', -10, 10.0, 100.0, code='C'))
        t = txs[0]
        self.assertEqual((t['date'], t['date_settle']),
                         ('2025-12-31', '2026-01-02'))
        self.assertEqual(t['broker_time'], '2025-12-30 20:00:00 ET')

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_check_dates_reads_2000_as_the_overnight_session(self):
        # Mutant: check_dates `t >= OVERNIGHT_OPEN` -> `>`.
        def code(d, t):
            r = check_trade_time('us-equity', date.fromisoformat(d),
                                 time.fromisoformat(t), 'QZX.US', 'USD')
            return r[1] if r else None
        self.assertIsNone(code('2026-07-05', '20:00:00'))          # Sunday
        self.assertEqual(code('2026-07-05', '19:59:59'), 'weekend-trade')
        self.assertEqual(code('2026-07-10', '20:00:00'),           # Friday
                         'friday-night-trade')
        self.assertIsNone(code('2026-07-10', '19:59:59'))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_overnight_fill_on_a_holiday_eve_skips_the_holiday(self):
        # Mutant: the next-day loop skips weekends only.
        for when, want in ((('2025-12-24', '20:30:00'), '2025-12-26'),
                           (('2025-07-03', '20:15:00'), '2025-07-07'),
                           (('2025-11-26', '21:00:00'), '2025-11-28')):
            with self.subTest(when=when):
                d, t, _ = _ib_market_trade_date(*when, 'Stocks', 'USD',
                                                 'US')
                self.assertEqual((d, t), (want, '00:00:00'))


# ---------------------------------------------------------------- A2-0520
class TestStrictParseSiblingsOfS058_11(unittest.TestCase):
    """A decimal-comma number at a strict site is refused, never read
    100x (or 10x) too large: IB split ratio, IB Corporate Actions
    Quantity, IB Trades/Forex Quantity, Questrade TRANSFER BOOK VALUE."""

    def _ib_refused(self, text, field):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse_ib(text)
        self.assertIn(field, str(cm.exception))

    def test_ib_split_ratio_decimal_comma(self):
        for terms in ('1 for 2,5', '2,5 for 1'):
            with self.subTest(terms=terms):
                self._ib_refused(HEAD + CA_H + _ca(
                    f'QZX(US0000000QX1) Split {terms} (QZX, QZX CORP, '
                    f'US0000000QX1)', -60, when='2026-03-02, 20:25:00'),
                    'split ratio')

    def test_ib_corporate_action_quantity_decimal_comma(self):
        self._ib_refused(HEAD + CA_H + _ca(
            'QZX(US0000000QX1) Merged(Acquisition) FOR USD 12.00 PER '
            'SHARE (QZX, QZX CORP, US0000000QX1)', '"-1,5"', value=0,
            proceeds=18, when='2026-03-02, 20:25:00'), "Quantity '-1,5'")

    def test_ib_forex_quantity_decimal_comma(self):
        self._ib_refused(HEAD + TRADES_H + _trade(
            'USD.CAD', '2026-01-05, 10:00:00', '"1,5"', 1.35, -2.03,
            cat='Forex', cur='CAD'), "Quantity '1,5'")

    def test_questrade_transfer_book_value_decimal_comma(self):
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        csv_text = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2026-03-10 12:00:00 AM,2026-03-11 12:00:00 AM,Sell,QZR,'
            'QZR CORP WE ACTED AS AGENT,-3,64.73,194.20,0,194.20,USD,'
            '55500001,Trades,Individual LIRA\n'                  # pii-ok
            '2026-03-03 12:00:00 AM,2026-03-03 12:00:00 AM,TF6,Q999001,'
            '"QZR CORP OTHER BROKER 146.16 TRANSFER BOOK VALUE 173,64",3,'
            '0,0,0,0,USD,55500001,Transfers,Individual LIRA\n')  # pii-ok
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'qt.csv'
            p.write_text(csv_text, encoding='utf-8')
            with self.assertRaises(BrokerageParseError) as cm, \
                    redirect_stderr(io.StringIO()):
                QuestradeBrokerage().parse_file(p)
        self.assertIn("BOOK VALUE '173,64'", str(cm.exception))


# ------------------------------------------- A2-0895 (S058-11 accruals)
class TestAccrualCellsParsedStrictly(unittest.TestCase):

    def _parse(self, *rows):
        return _parse_ib(HEAD + _PERIOD + _ACC_H + ''.join(
            r for r in rows if r.startswith('Change'))
            + _DIV_H + ''.join(r for r in rows if r.startswith('Div')))

    def test_decimal_comma_gross_amount_is_skipped_not_scaled(self):
        _, txs, err = self._parse(_acc(
            'QZG', '2025-03-01', '2025-03-05', '2025-03-20', 800, '0.55',
            '"440,00"', 'Po'))
        self.assertIn("skipping malformed IB Change in Dividend Accruals "
                      "row (Gross Amount '440,00'", err)
        self.assertNotIn('44000', err)
        self.assertNotIn('44,000', err)

    def test_decimal_comma_gross_rate_is_not_the_pil_rate(self):
        # Lenient parse read 0,45 as 45: a PIL of 6 shares at 45.
        _, txs, _ = self._parse(
            _acc('QZO', '2025-03-01', '2025-03-05', '2025-03-20', 600,
                 '"0,45"', '270.00', 'Po'),
            _acc('QZO', '2025-03-20', '2025-03-05', '2025-03-20', 600,
                 '"0,45"', '-270.00', 'Re'),
            _div('QZO', 'US0000000QO1', '2025-03-20', 270, pil=True))
        self.assertEqual(_pil(txs), [('QZO.US', 600.0, 0.45)])


# ------------------------------------- A2-0888 m12 / A2-0890 / A2-1558
class TestAccrualMatching(unittest.TestCase):

    def _parse(self, *rows):
        return _parse_ib(HEAD + _PERIOD + _ACC_H + ''.join(
            r for r in rows if r.startswith('Change'))
            + _DIV_H + ''.join(r for r in rows if r.startswith('Div')))

    def test_pil_rate_is_the_po_rows_in_either_order(self):
        # S060-13 / G3-0: the Re row carries the rate in another
        # currency; only the Po row's rate is used (mutant `if True`).
        po = _acc('QZV', '2025-09-30', '2025-09-30', '2025-10-14', 9000,
                  0.0125, 112.5, 'Po')
        re_ = _acc('QZV', '2025-10-14', '2025-09-30', '2025-10-14', 9000,
                   0.01740125, -112.5, 'Re')
        pil = _div('QZV', 'US0000000QV1', '2025-10-14', 112.5, pil=True)
        for rows in ((po, re_, pil), (re_, po, pil)):
            with self.subTest(first=rows[0][:60]):
                _, txs, _ = self._parse(*rows)
                self.assertEqual(_pil(txs), [('QZV.US', 9000.0, 0.0125)])

    @rule("US-INC-DATE-RIC")
    def test_an_ex_date_after_the_pay_date_is_not_attached(self):
        # A garbled accrual whose ex date follows the pay date (mutant
        # `if _ex:`).
        _, txs, _ = self._parse(
            _acc('QZY', '2025-03-04', '2025-03-20', '2025-03-04', 100, 0.5,
                 50, 'Po'),
            _div('QZY', 'US0000000QY1', '2025-03-04', 50, rate=0.5))
        div = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(div), 1)
        self.assertNotIn('ex_date', div[0])
        _, txs, _ = self._parse(
            _acc('QZY', '2025-02-20', '2025-02-20', '2025-03-04', 100, 0.5,
                 50, 'Po'),
            _div('QZY', 'US0000000QY1', '2025-03-04', 50, rate=0.5))
        self.assertEqual([t.get('ex_date') for t in txs
                          if t['action'] == 'DIVIDEND'], ['2025-02-20'])

    def test_accrual_matches_a_posting_within_seven_days_only(self):
        # Mutant: `min(dists) > 7` -> `> 8`.
        acc = _acc('QZE', '2025-08-20', '2025-08-20', '2025-09-15', 42, 0.5,
                   21, 'Po')
        _, txs, _ = self._parse(acc, _div('QZE', 'US0000000QE1',
                                          '2025-09-22', 21, pil=True))
        self.assertEqual(_pil(txs), [('QZE.US', 42.0, 0.5)])
        _, txs, _ = self._parse(acc, _div('QZE', 'US0000000QE1',
                                          '2025-09-23', 21, pil=True))
        self.assertEqual(_pil(txs), [('QZE.US', 0.0, 0.0)])
        self.assertEqual([t.get('ex_date') for t in txs], [None])


# --------------------------------------- A2-0887 / A2-0888 / A2-1606
class TestCommissionRefundMatcher(unittest.TestCase):
    """CA-ACB-COMMREFUND / US-BASIS-COMMREFUND: a refund folds into the
    ONE trade it names (ticker, currency, quantity, trade date — the
    ET clock date of an overnight fill too); otherwise it stays a FEE
    row with a note."""

    @rule("CA-ACB-COMMREFUND")
    @rule("US-BASIS-COMMREFUND")
    def test_refund_naming_the_clock_date_of_an_overnight_fill(self):
        # Mon 21:00 ET trades Tue; IB's refund names Monday (mutant: the
        # broker_time date is ignored).
        _, txs, err = _parse_ib(
            HEAD + TRADES_H + _trade('QZK', '2025-03-03, 21:00:00', 100, 10,
                                     -1000, comm=-5)
            + ADJ_H + _adj('USD', '2025-03-10',
                           'Refund (QZK, 100, 2025-03-03)', 4))
        self.assertEqual([(t['action'], t['date'], t['net_amount'])
                          for t in txs],
                         [('BUYSELL', '2025-03-04', 1001.0)], err)

    @rule("CA-ACB-COMMREFUND")
    def test_refund_in_another_currency_is_not_folded(self):
        _, txs, err = _parse_ib(
            HEAD + TRADES_H + _trade('QZK', '2025-02-03, 10:00:00', 100, 10,
                                     -1000, comm=-5)
            + ADJ_H + _adj('CAD', '2025-02-10',
                           'Refund (QZK, 100, 2025-02-03)', 4))
        self.assertEqual(sorted((t['action'], t['net_amount'])
                                for t in txs),
                         [('BUYSELL', 1005.0), ('FEE', -4.0)], err)
        self.assertIn('kept as a FEE row', err)

    @rule("CA-ACB-COMMREFUND")
    def test_refund_naming_more_shares_than_the_trade_is_not_folded(self):
        _, txs, err = _parse_ib(
            HEAD + TRADES_H + _trade('QZK', '2025-02-03, 10:00:00', 100, 10,
                                     -1000, comm=-5)
            + ADJ_H + _adj('USD', '2025-02-10',
                           'Refund (QZK, 200, 2025-02-03)', 4))
        self.assertEqual(sorted((t['action'], t['net_amount'])
                                for t in txs),
                         [('BUYSELL', 1005.0), ('FEE', -4.0)], err)

    @rule("CA-ACB-COMMREFUND")
    def test_two_identical_candidates_keep_the_fee_row(self):
        _, txs, err = _parse_ib(
            HEAD + TRADES_H
            + _trade('QZK', '2025-02-03, 10:00:00', 100, 10, -1000, comm=-5)
            + _trade('QZK', '2025-02-03, 11:00:00', 100, 10, -1000, comm=-5)
            + ADJ_H + _adj('USD', '2025-02-10',
                           'Refund (QZK, 100, 2025-02-03)', 4))
        self.assertEqual(sorted((t['action'], t['net_amount'])
                                for t in txs),
                         [('BUYSELL', 1005.0), ('BUYSELL', 1005.0),
                          ('FEE', -4.0)], err)
        self.assertIn('matches 2 trade(s)', err)


# --------------------------------------------------- A2-0887 / A2-0888
class TestTradeRowMarkers(unittest.TestCase):

    def test_cancellation_leg_carries_no_open_close(self):
        # A Ca row's O/C says nothing about this account's position
        # (mutant M01 / m08: open_close kept on Ca rows).
        _, txs, _ = _parse_ib(HEAD + TRADES_H + _trade(
            'QZC', '2025-04-01, 10:00:00', -50, 20, 1000, code='Ca;O'))
        self.assertEqual([(t['quantity'], t.get('type'), t.get('open_close'))
                          for t in txs], [(-50.0, TRADE_CANCEL_TYPE, None)])

    def test_broker_basis_only_on_a_pure_closing_trade(self):
        # Mutant M02: IB's Basis copied onto opening trades too.
        row_o = ('Trades,Data,Order,Stocks,USD,U5550001,QZB,'  # pii-ok
                 '"2025-04-01, 10:00:00",10,20,0,-200,-1,-201,0,0,O\n')
        row_c = ('Trades,Data,Order,Stocks,USD,U5550001,QZB,'  # pii-ok
                 '"2025-05-01, 10:00:00",-10,25,0,250,-1,201,49,0,C\n')
        _, txs, _ = _parse_ib(HEAD + TRADES_H + row_o + row_c)
        self.assertEqual([t.get('broker_basis') for t in txs],
                         [None, '201.00 USD'])


# --------------------------------------------- A2-0887 M13 / M14 / M25
class TestCancellationAndCashInLieuWindows(unittest.TestCase):

    @rule("CA-ACB-04")
    def test_corporate_ca_waits_for_its_same_date_original(self):
        # R1-300: a rebook dated BEFORE the cancelled original and listed
        # before the Ca is never the row undone (mutant M13).
        d = 'QZS(US0000000AA1) Split 2 for 1 (QZS, QZS INC, US0000000AA1)'
        rows = (_ca(d, -100, when='2026-03-01, 20:25:00')
                + _ca(d, 200, when='2026-03-01, 20:25:00')
                + _ca(d, 100, when='2026-03-02, 20:25:00', code='Ca')
                + _ca(d, -200, when='2026-03-02, 20:25:00', code='Ca')
                + _ca(d, -100, when='2026-03-02, 20:25:00')
                + _ca(d, 200, when='2026-03-02, 20:25:00'))
        _, txs, err = _parse_ib(HEAD + CA_H + rows)
        self.assertEqual([(t['date'], round(t['quantity'], 6))
                          for t in txs if t['action'] == 'SPLIT'],
                         [('2026-03-01', 2.0)], err)

    def test_transfer_ca_never_consumes_a_later_dated_move(self):
        # A later real move listed BEFORE the Ca stays (mutant M14).
        _, txs, err = _parse_ib(HEAD + XFER_H
                                + _xfer('QZB', '2026-02-01', -50, -500)
                                + _xfer('QZB', '2026-01-05', 50, 500,
                                        code='Ca'))
        self.assertEqual(sorted((t['date'], t['quantity']) for t in txs
                                if t['action'] == 'TRANSFER'),
                         [('2026-01-05', 50.0), ('2026-02-01', -50.0)], err)
        self.assertIn('not in this statement', err)

    @rule("CA-ACB-04")
    def test_cash_in_lieu_joins_a_split_within_seven_days_only(self):
        # Mutant M25: the window widened to 70 days.
        legs = (_ca(_split_desc(1, 3), -2000, when='2026-10-02, 20:25:00')
                + _ca(_split_desc(1, 3), 666, when='2026-10-02, 20:25:00'))
        for when, joined in (('2026-10-09', True), ('2026-10-10', False)):
            with self.subTest(when=when):
                _, txs, err = _parse_ib(HEAD + CA_H + legs + _ca(
                    _CIL, -0.6667, proceeds=6, when=f'{when}, 20:25:00'))
                self.assertEqual(
                    _ratios(txs),
                    [('2026-10-02', 0.3333333 if joined else 0.333)])
                self.assertEqual('matched no split' in err, not joined)


# --------------------------------------------------------- A2-0887 M24
class TestFuturesOutsideTheTradesCashLine(unittest.TestCase):

    def test_futures_notional_is_not_reconciled_as_trades_cash(self):
        # Futures settle through Cash Settling MTM, never Trades (Sales +
        # Purchase); booking the notional there fails the Cash Report.
        fut = ('Trades,Data,Order,Futures,USD,U5550001,QZCLG6,'  # pii-ok
               '"2026-01-12, 10:00:00",1,60,0,-60000,-2.5,0,0,0,O\n')
        _, txs, err = _parse_ib(HEAD + FUT_H + fut + FII_H + FUT_FII
                                + CASH_H
                                + 'Cash Report,Data,Commissions,USD,-2.5,\n')
        self.assertEqual([(t['symbol'], t['quantity']) for t in txs],
                         [('F:QZCLG6.US', 1.0)], err)


# --------------------------------------------------------- A2-0887 M16
class TestCoverageCurrentYear(unittest.TestCase):

    def _warn(self, periods, today):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _warn_coverage_gaps(periods, today=today)
        return buf.getvalue()

    def test_statements_ending_mid_current_year_are_not_reported(self):
        per = [('ib.csv', date(2026, 1, 1), date(2026, 6, 30))]
        self.assertEqual(self._warn(per, date(2026, 10, 2)), '')
        # Control: the same gap in a finished year is reported.
        per = [('ib.csv', date(2025, 1, 1), date(2025, 6, 30))]
        self.assertIn('before the end of 2025',
                      self._warn(per, date(2026, 10, 2)))


# --------------------------------------------- A2-0887 M11 / M26 / M27
def _row(qty, *, time_='10:00:00', account='IB', fee=0.0, ca=False,
         sym='QZQ.US'):
    t = {'action': 'BUYSELL', 'symbol': sym, 'currency': 'USD',
         'date': '2025-10-22', 'time': time_, 'quantity': qty,
         'price': 20.0, 'fee': fee, 'account': account}
    if ca:
        t['type'] = TRADE_CANCEL_TYPE
    return t


class TestPairCancellations(unittest.TestCase):
    """lib/trade_cancel: among several candidate originals the one with
    the same time wins, else the latest one before the Ca; never one in
    another account."""

    def test_latest_original_before_the_ca_is_paired(self):
        a, b, c = _row(100, fee=1.0), _row(100, fee=2.0), _row(-100, ca=True)
        kept, pairs, unmatched = pair_cancellations([a, b, c])
        self.assertEqual((kept, unmatched), ([a], []))
        self.assertIs(pairs[0][0], b)

    def test_same_time_original_is_preferred(self):
        a = _row(100, time_='10:00:00', fee=1.0)
        b = _row(100, time_='11:00:00', fee=2.0)
        c = _row(-100, time_='10:00:00', ca=True)
        kept, pairs, _ = pair_cancellations([a, b, c])
        self.assertEqual(kept, [b])
        self.assertIs(pairs[0][0], a)

    def test_original_in_another_account_is_not_paired(self):
        a = _row(100, account='rrsp')
        c = _row(-100, account='margin', ca=True)
        kept, pairs, unmatched = pair_cancellations([a, c])
        self.assertEqual((kept, pairs, unmatched), ([a, c], [], [c]))


if __name__ == '__main__':
    unittest.main()


# ------------------------------------- A2-0886 / A2-1559 / A2-1560
class TestCancelAndRebookOfAnEarlierStatementsRow(unittest.TestCase):
    """IB cancels (Ca) a row of an EARLIER statement and rebooks it, in
    the later statement, with the original's date: the later statement
    holds the Ca and the rebook (both dated before its period), the
    earlier one the original. The Ca cancels the earlier statement's
    original; the rebook is booked. It used to pair with the rebook in
    its own statement, and the cross-statement pass then dropped the
    earlier original as an "overlapping copy": neither was booked."""

    def setUp(self):
        from test_fix_a2_ib import _stmt, _booked
        self._stmt, self._booked = _stmt, _booked

    def _y25(self, *sections):
        return self._stmt('January 1, 2025', 'December 31, 2025', *sections)

    def _y26(self, *sections):
        return self._stmt('January 1, 2026', 'March 31, 2026', *sections)

    @rule("CA-ACB-COMMREFUND")
    def test_trade_rebook_is_booked_with_its_new_commission(self):
        orig = _trade('QZQ', '2025-10-22, 10:00:00', 100, 20, -2000, comm=-9)
        ca = _trade('QZQ', '2025-10-22, 10:00:00', -100, 20, 2000, comm=9,
                    code='Ca')
        rebook = _trade('QZQ', '2025-10-22, 10:00:00', 100, 20, -2000,
                        comm=-0.5)
        sale = _trade('QZQ', '2026-02-02, 10:00:00', -100, 25, 2500,
                      comm=-1, code='C')
        for later in ((ca, rebook, sale), (rebook, ca, sale)):
            with self.subTest(order='ca first' if later[0] is ca
                              else 'rebook first'):
                rows, err = self._booked({
                    'ib_2025.csv': self._y25(TRADES_H, orig),
                    'ib_2026.csv': self._y26(TRADES_H, *later)})
                buys = [(t['date'], t['quantity'], t['fee'])
                        for t in rows if t['symbol'] == 'QZQ.US'
                        and t['quantity'] > 0]
                self.assertEqual(buys, [('2025-10-22', 100.0, 0.5)], err)
                self.assertFalse([t for t in rows
                                  if t.get('type') == TRADE_CANCEL_TYPE])

    @rule("CA-ACB-04")
    def test_cash_in_lieu_rebook_is_booked(self):
        buy = _trade('QZT', '2025-01-10, 10:00:00', 100.5, 20, -2010)
        when = '2025-11-05, 20:25:00'
        cil = ('QZT(US0000000QT1) Cash in Lieu of Fractional Shares (QZT, '
               'QZT CORP, US0000000QT1)')
        a = self._y25(TRADES_H, buy, CA_H, _ca(cil, -0.5, value=10,
                                               proceeds=10, when=when))
        ca = _ca(cil, 0.5, value=-10, proceeds=-10, when=when, code='Ca')
        rebook = _ca(cil, -0.5, value=12, proceeds=12, when=when)
        for later in ((ca, rebook), (rebook, ca)):
            with self.subTest(order='ca first' if later[0] is ca
                              else 'rebook first'):
                rows, err = self._booked({'ib_2025.csv': a,
                                          'ib_2026.csv': self._y26(
                                              CA_H, *later)})
                sales = [(t['date'], t['quantity'], t['net_amount'])
                         for t in rows if t['symbol'] == 'QZT.US'
                         and t['quantity'] < 0]
                self.assertEqual(sales, [('2025-11-05', -0.5, 12.0)], err)

    def test_transfer_rebook_is_booked(self):
        a = self._y25(XFER_H, _xfer('QZT', '2025-06-02', 100, 5000))
        ca = _xfer('QZT', '2025-06-02', -100, -5000, code='Ca')
        rebook = _xfer('QZT', '2025-06-02', 100, 5200)
        for later in ((ca, rebook), (rebook, ca)):
            with self.subTest(order='ca first' if later[0] is ca
                              else 'rebook first'):
                rows, err = self._booked(
                    {'ib_2025.csv': a,
                     'ib_2026.csv': self._y26(XFER_H, *later)},
                    '--transfers')
                legs = [(t['date'], t['quantity'], t['net_amount'])
                        for t in rows if t['action'] == 'TRANSFER']
                self.assertEqual(legs, [('2025-06-02', 100.0, 5200.0)], err)

    def test_lone_later_statement_keeps_todays_pairing(self):
        # No earlier statement: the Ca pairs with the row in its own
        # statement, as before.
        rows, err = self._booked({'ib_2026.csv': self._y26(
            TRADES_H,
            _trade('QZQ', '2025-10-22, 10:00:00', -100, 20, 2000, comm=9,
                   code='Ca'),
            _trade('QZQ', '2025-10-22, 10:00:00', 100, 20, -2000,
                   comm=-0.5))})
        self.assertFalse([t for t in rows if t['symbol'] == 'QZQ.US'], err)
