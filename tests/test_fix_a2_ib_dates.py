"""Re-audit-2 IB trade-date findings: clock-time validation (A2-0082,
A2-0260, A2-0607), the exchange trade date of fills outside the US
regular session (A2-0252, A2-0253, A2-1027, A2-0254, A2-0596, A2-0608,
A2-0255, A2-0597, A2-0595) and the settlement market of a listing
quoted in a foreign currency (A2-0595, A2-0081). Every fixture is
synthetic: invented tickers, fake account ids (pii-ok)."""
import unittest
from datetime import date, time

from taxjson.lib import country as C
from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import (_ib_market_trade_date,
                                                 _ib_split_datetime)
from tax_rules import rule
from tax_rules.dual import gains_both

from test_fix_ibparse import HEAD, TRADES_H, _trade, _parse_ib
from test_fix_l_ibparse import _book

FUT_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
         'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
         'Notional Value,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,'
         'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
         'Expiry,Delivery Month,Type,Strike,Code\n')
FUT_FII = ('Financial Instrument Information,Data,Futures,QZCLG6,'
           'QZCL FEB26,999000011,,QZCL,NYMEX,"1,000",2026-01-20,2026-02,'
           ',,\n')


def _stock_fii(sym, exch, conid='999000031'):
    return (f'Financial Instrument Information,Data,Stocks,"{sym}",{sym} '
            f'FUND,{conid},,,{exch},1,,,ETF,,\n')


# ------------------------------------------- A2-0082 / A2-0260 / A2-0607
class TestIbClockTime(unittest.TestCase):
    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_unpadded_hour_is_padded_before_any_compare(self):
        self.assertEqual(_ib_split_datetime('2025-12-31, 9:45:00', 'r'),
                         ('2025-12-31', '09:45:00'))
        self.assertEqual(_ib_split_datetime('2025-12-31, 9:45', 'r'),
                         ('2025-12-31', '09:45:00'))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_unpadded_morning_sale_stays_on_dec31(self):
        _, txs, _ = _parse_ib(HEAD + TRADES_H + _trade(
            'QZX', '2025-12-31, 9:45:00', -10, 10.0, 100.0, code='C'))
        t = txs[0]
        self.assertEqual((t['date'], t['time']), ('2025-12-31', '09:45:00'))
        self.assertEqual(t['date_settle'], '2026-01-02')
        self.assertNotIn('broker_time', t)

    def test_impossible_clock_times_are_refused(self):
        for bad in ('2025-12-31, 29:00:00', '2025-12-31, 12:75:00',
                    '2025-12-31, 12:00:60', '2025-12-31, 24:00:00'):
            with self.subTest(bad=bad):
                with self.assertRaises(BrokerageParseError) as cm:
                    _ib_split_datetime(bad, 'ib.csv row 7')
                self.assertIn('ib.csv row 7', str(cm.exception))
                self.assertIn('clock time', str(cm.exception))

    def test_impossible_time_refused_end_to_end(self):
        for cur in ('USD', 'AUD'):
            with self.subTest(cur=cur):
                with self.assertRaises(BrokerageParseError):
                    _parse_ib(HEAD + TRADES_H + _trade(
                        'QZX', '2025-12-31, 29:00:00', -10, 10.0, 100.0,
                        code='C', cur=cur))


# ------------------------------ A2-0253 / A2-1027 / A2-0252 / A2-0595
class TestIbVenueLocalDate(unittest.TestCase):
    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_asx_option_and_warrant_take_the_sydney_date(self):
        for cat in ('Equity and Index Options', 'Warrants', 'Stocks'):
            with self.subTest(cat=cat):
                d, t, stamp = _ib_market_trade_date(
                    '2026-12-30', '18:30:00', cat, 'AUD', 'AX')
                self.assertEqual((d, t), ('2026-12-31', '10:30:00'))
                self.assertEqual(stamp, '2026-12-30 18:30:00 ET')

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_asia_pacific_fills_take_their_local_date(self):
        # 21:00 EST on Mon Dec 29 is Tue Dec 30 in Asia.
        for cur, want in (('HKD', ('2025-12-30', '10:00:00')),
                          ('JPY', ('2025-12-30', '11:00:00')),
                          ('SGD', ('2025-12-30', '10:00:00')),
                          ('NZD', ('2025-12-30', '15:00:00'))):
            with self.subTest(cur=cur):
                d, t, stamp = _ib_market_trade_date(
                    '2025-12-29', '21:00:00', 'Stocks', cur, cur)
                self.assertEqual((d, t), want)
                self.assertTrue(stamp)

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_hkd_sale_on_dec30_local_settles_in_january(self):
        _, txs, _ = _parse_ib(HEAD + TRADES_H + _trade(
            'QZH', '2025-12-29, 21:00:00', -10, 10.0, 100.0, code='C',
            cur='HKD'))
        t = txs[0]
        self.assertEqual(t['date'], '2025-12-30')
        self.assertEqual(t['date_settle'], '2026-01-01')   # 2 business days


# ---------------------------------- A2-0254 / A2-0596 / A2-0608 (CME)
class TestIbFuturesEveningSession(unittest.TestCase):
    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_evening_fill_trades_on_the_next_cme_day(self):
        for when, want in ((('2026-07-05', '19:00:00'), '2026-07-06'),  # Sun
                           (('2025-12-25', '19:00:00'), '2025-12-26'),  # Xmas
                           (('2025-12-30', '19:30:00'), '2025-12-31'),
                           (('2025-12-31', '18:30:00'), '2026-01-02'),
                           (('2025-10-26', '18:20:00'), '2025-10-27')):
            for cat in ('Futures', 'Options On Futures'):
                with self.subTest(when=when, cat=cat):
                    d, t, stamp = _ib_market_trade_date(
                        *when, cat, 'USD', '')
                    self.assertEqual((d, t), (want, '00:00:00'))
                    self.assertEqual(stamp, f"{when[0]} {when[1]} ET")

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_day_session_and_friday_evening_are_unchanged(self):
        for when in (('2025-12-30', '10:00:00'), ('2025-12-30', '17:59:59'),
                     ('2026-07-10', '19:00:00'),            # Friday
                     ('2026-07-05', '12:00:00')):           # Sunday early
            with self.subTest(when=when):
                self.assertEqual(
                    _ib_market_trade_date(*when, 'Futures', 'USD', ''),
                    (when[0], when[1], ''))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_fill_on_an_exchange_holiday_takes_the_next_day(self):
        # MLK day 2026-01-19: the Globex session's trade date is Tuesday.
        self.assertEqual(
            _ib_market_trade_date('2026-01-19', '10:00:00', 'Futures',
                                  'USD', '')[:2],
            ('2026-01-20', '00:00:00'))

    @rule("CA-DATE-10")
    @rule("US-DATE-12")
    def test_dec30_evening_close_settles_in_january_under_next_day(self):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        body = (HEAD + FUT_H
                + _trade('QZCLG6', '2025-12-01, 10:00:00', 1, 60.0, -60000,
                         -2.25, cat='Futures')
                + _trade('QZCLG6', '2025-12-30, 19:30:00', -1, 62.0, 62000,
                         -2.25, cat='Futures', code='C')
                + FII_H + FUT_FII)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'ib.csv'
            p.write_text(body, encoding='utf-8')
            parser = IbBrokerage()
            parser.futures_settle = 'next_day'
            with contextlib.redirect_stderr(io.StringIO()):
                txs = parser.parse_file(p)
        close = txs[1]
        self.assertEqual((close['date'], close['date_settle']),
                         ('2025-12-31', '2026-01-02'))
        self.assertEqual(close['broker_time'], '2025-12-30 19:30:00 ET')

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_sunday_evening_round_trip_is_dated_monday_in_both(self):
        _, txs, _ = _parse_ib(
            HEAD + FUT_H
            + _trade('QZCLG6', '2025-11-30, 19:05:13', 1, 60.0, -60000,
                     -2.25, cat='Futures')
            + _trade('QZCLG6', '2025-11-30, 21:10:27', -1, 61.0, 61000,
                     -2.25, cat='Futures', code='C')
            + FII_H + FUT_FII)
        self.assertEqual([t['date'] for t in txs],
                         ['2025-12-01', '2025-12-01'])
        r = gains_both(_book(txs), year=2025)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                self.assertEqual([t['date'] for t in r[c]['transactions']],
                                 ['2025-12-01'])

    def test_non_usd_futures_keep_the_clock_date(self):
        self.assertEqual(
            _ib_market_trade_date('2025-12-30', '19:30:00', 'Futures',
                                  'EUR', ''),
            ('2025-12-30', '19:30:00', ''))


# ------------------------------------------------ A2-0255 (Cboe GTH)
class TestIbIndexOptionGth(unittest.TestCase):
    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_gth_fill_trades_on_the_next_day(self):
        for sym in ('SPXW 20JUN25 5900 C', 'SPX 20JUN25 5900 C',
                    'XSP 20JUN25 590 C', 'VIX 18JUN25 20 C'):
            with self.subTest(sym=sym):
                self.assertEqual(
                    _ib_market_trade_date('2025-06-01', '20:30:00',
                                          'Equity and Index Options', 'USD',
                                          '', symbol=sym)[:2],
                    ('2025-06-02', '00:00:00'))
        self.assertEqual(
            _ib_market_trade_date('2025-12-30', '21:00:00',
                                  'Equity and Index Options', 'USD', '',
                                  symbol='SPXW 31DEC25 5900 C')[:2],
            ('2025-12-31', '00:00:00'))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_equity_options_and_regular_hours_are_unchanged(self):
        for sym, when in (('QZX 20JUN25 50 C', '21:00:00'),
                          ('SPXW 20JUN25 5900 C', '16:10:00'),
                          ('SPXW 20JUN25 5900 C', '20:14:59')):
            with self.subTest(sym=sym, when=when):
                self.assertEqual(
                    _ib_market_trade_date('2025-06-03', when,
                                          'Equity and Index Options', 'USD',
                                          '', symbol=sym),
                    ('2025-06-03', when, ''))

    def test_check_dates_does_not_error_on_a_sunday_gth_fill(self):
        from taxjson.lib.check_dates import check_trade_time
        hit = check_trade_time('option', date(2025, 6, 1), time(20, 30),
                               'SPXW250620C05900000.US', 'USD')
        self.assertTrue(hit is None or hit[0] != 'ERROR', hit)
        hit = check_trade_time('option', date(2025, 6, 1), time(20, 30),
                               'QZX250620C00050000.US', 'USD')
        self.assertEqual(hit[0], 'ERROR')


# ----------------------------------------------------------- A2-0597
class TestIbOvernightAfterMidnightOnHoliday(unittest.TestCase):
    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_after_midnight_fill_on_a_holiday_takes_the_next_day(self):
        # Thanksgiving 2025-11-27: the Wed-night session trades Fri 11-28.
        for when in (('2025-11-26', '23:59:00'), ('2025-11-27', '00:30:00'),
                     ('2025-11-27', '03:30:00')):
            with self.subTest(when=when):
                self.assertEqual(
                    _ib_market_trade_date(*when, 'Stocks', 'USD', 'US')[:2],
                    ('2025-11-28', '00:00:00'))
        self.assertEqual(
            _ib_market_trade_date('2026-01-01', '00:30:00', 'Stocks', 'USD',
                                  'US')[:2], ('2026-01-02', '00:00:00'))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_after_midnight_on_a_trading_day_is_unchanged(self):
        self.assertEqual(
            _ib_market_trade_date('2025-11-26', '00:30:00', 'Stocks', 'USD',
                                  'US'),
            ('2025-11-26', '00:30:00', ''))


# ------------------------------------- A2-0595 (TSX unit) / A2-0081 (LSE)
class TestIbListingSettlementMarket(unittest.TestCase):
    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_usd_tsx_unit_settles_on_the_canadian_calendar(self):
        _, txs, _ = _parse_ib(HEAD + FII_H + _stock_fii('QZX.U', 'TSE')
                              + TRADES_H + _trade(
                                  'QZX.U', '2025-06-30, 10:00:00', 10, 10.0,
                                  -100.0))
        t = txs[0]
        self.assertEqual(t['symbol'], 'QZX.U.TO')
        self.assertEqual(t['date_settle'], '2025-07-02')    # not Canada Day

    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_usd_lse_etf_is_an_lse_listing_on_the_uk_cycle(self):
        _, txs, _ = _parse_ib(
            HEAD + FII_H + _stock_fii('QZSPX', 'LSEETF') + TRADES_H
            + _trade('QZSPX', '2025-06-02, 10:00:00', 10, 500.0, -5000.0)
            + _trade('QZSPX', '2025-12-30, 10:00:00', -10, 600.0, 6000.0,
                     code='C'))
        self.assertEqual([t['symbol'] for t in txs], ['QZSPX.L', 'QZSPX.L'])
        self.assertEqual(txs[1]['date_settle'], '2026-01-01')  # T+2
        book = _book(txs)
        r25 = gains_both(book, year=2025)
        # Canada (settle date): a 2026 disposition; US (trade date): 2025.
        self.assertEqual(r25[C.CANADA]['transactions'], [])
        self.assertEqual(len(r25[C.USA]['transactions']), 1)

    def test_usd_nyse_interlisting_keeps_us(self):
        _, txs, _ = _parse_ib(HEAD + FII_H + _stock_fii('QZM', 'NYSE') + TRADES_H
                              + _trade('QZM', '2025-06-02, 10:00:00', 10,
                                       10.0, -100.0))
        self.assertEqual(txs[0]['symbol'], 'QZM.US')
        self.assertEqual(txs[0]['date_settle'], '2025-06-03')


if __name__ == '__main__':
    unittest.main()
