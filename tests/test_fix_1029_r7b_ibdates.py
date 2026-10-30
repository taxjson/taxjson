"""Fix round 7 (wave B), group ibdates: IB trade dates and listing
inference. Holiday Cboe GTH option sessions (TJ-0871), one clock for
Asia-Pacific fills (TJ-0872), Montreal evening futures (TJ-0878), CME's
24/7 crypto futures (TJ-0879), the listing replay's intraday order and
coded short openings (TJ-0659, TJ-0660). Every fixture is synthetic:
invented tickers and ISINs, fake account ids (pii-ok)."""
import unittest

from taxjson.lib.brokerages.ib_extractor import _ib_market_trade_date
from tax_rules import rule

from test_fix_ibparse import HEAD, TRADES_H, DIV_H, _trade, _parse_ib

FUT_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
         'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
         'Notional Value,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,'
         'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
         'Expiry,Delivery Month,Type,Strike,Code\n')
OPT = 'Equity and Index Options'


def _fut_fii(sym, under, exch, mult, conid='999000041'):
    return (f'Financial Instrument Information,Data,Futures,{sym},'
            f'{under} SEP26,{conid},,{under},{exch},{mult},2026-09-18,'
            f'2026-09,,,\n')


def _dates(txs):
    return [(t['date'], t['time'], t['date_settle']) for t in txs]


# ------------------------------------------------------------ TJ-0871
class TestGthHolidaySession(unittest.TestCase):
    """Cboe's holiday GTH session (Sunday January 18 20:15 ET through
    MLK Monday January 19, 2026) trades on Tuesday January 20."""

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_after_midnight_part_takes_the_next_trading_day(self):
        sym = 'SPXW 20JAN26 6000 C'
        for when in (('2026-01-18', '21:00:00'), ('2026-01-19', '02:00:00'),
                     ('2026-01-19', '09:00:00')):
            with self.subTest(when=when):
                d, t, stamp = _ib_market_trade_date(*when, OPT, 'USD', '',
                                                    symbol=sym)
                self.assertEqual((d, t), ('2026-01-20', '00:00:00'))
                self.assertEqual(stamp, f"{when[0]} {when[1]} ET")

    def test_root_without_an_evening_session_keeps_the_clock_date(self):
        self.assertEqual(
            _ib_market_trade_date('2026-01-19', '02:00:00', OPT, 'USD', '',
                                  symbol='QZX 20FEB26 50 C'),
            ('2026-01-19', '02:00:00', ''))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_open_then_close_stay_in_order_and_settle_wednesday(self):
        _, txs, _ = _parse_ib(
            HEAD + TRADES_H
            + _trade('SPXW 20JAN26 6000 C', '2026-01-18, 21:00:00', 1, 10.0,
                     -1000, cat=OPT)
            + _trade('SPXW 20JAN26 6000 C', '2026-01-19, 02:00:00', -1,
                     11.0, 1100, cat=OPT, code='C'))
        txs = sorted(txs, key=lambda t: (t['date'], t['time']))
        self.assertEqual([t['quantity'] for t in txs], [1.0, -1.0])
        self.assertEqual([(t['date'], t['date_settle']) for t in txs],
                         [('2026-01-20', '2026-01-21')] * 2)


# ------------------------------------------------------------ TJ-0872
class TestAsiaPacificOneClock(unittest.TestCase):
    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_same_local_date_fill_is_in_local_time_too(self):
        # 23:30 / 00:30 EDT are 12:30 / 13:30 JST on Tuesday June 3.
        self.assertEqual(
            _ib_market_trade_date('2025-06-02', '23:30:00', 'Stocks', 'JPY',
                                  'JPY')[:2], ('2025-06-03', '12:30:00'))
        d, t, stamp = _ib_market_trade_date('2025-06-03', '00:30:00',
                                             'Stocks', 'JPY', 'JPY')
        self.assertEqual((d, t), ('2025-06-03', '13:30:00'))
        self.assertEqual(stamp, '2025-06-03 00:30:00 ET')

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_buy_then_sell_keep_their_order(self):
        _, txs, _ = _parse_ib(
            HEAD + TRADES_H
            + _trade('QZJ', '2025-06-02, 23:30:00', 100, 10.0, -1000,
                     cur='JPY')
            + _trade('QZJ', '2025-06-03, 00:30:00', -100, 11.0, 1100,
                     cur='JPY', code='C'))
        self.assertEqual([(t['date'], t['time'], t['quantity'])
                          for t in sorted(txs, key=lambda t: (t['date'],
                                                              t['time']))],
                         [('2025-06-03', '12:30:00', 100.0),
                          ('2025-06-03', '13:30:00', -100.0)])


# ------------------------------------------------------------ TJ-0878
class TestMontrealEveningFutures(unittest.TestCase):
    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_evening_fill_takes_the_next_canadian_trading_day(self):
        for when, want in ((('2026-06-08', '20:30:00'), '2026-06-09'),
                           # Friday evening: Monday's session
                           (('2026-06-05', '20:30:00'), '2026-06-08'),
                           # Victoria Day 2026-05-18 (TSX closed)
                           (('2026-05-15', '20:30:00'), '2026-05-19'),
                           (('2026-05-18', '10:00:00'), '2026-05-19')):
            with self.subTest(when=when):
                d, t, stamp = _ib_market_trade_date(
                    *when, 'Futures', 'CAD', '', symbol='QZGU6',
                    exch='CDE')
                self.assertEqual((d, t), (want, '00:00:00'))
                self.assertEqual(stamp, f"{when[0]} {when[1]} ET")

    def test_day_session_and_unknown_venues_keep_the_clock_date(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '16:00:00', 'Futures', 'CAD',
                                  '', symbol='QZGU6', exch='CDE'),
            ('2026-06-08', '16:00:00', ''))
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '20:30:00', 'Futures', 'CAD',
                                  '', symbol='QZGU6', exch=''),
            ('2026-06-08', '20:30:00', ''))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_parse_dates_both_fills_tuesday(self):
        _, txs, _ = _parse_ib(
            HEAD + FII_H + _fut_fii('QZGU6', 'QZG', 'CDE', 1000) + FUT_H
            + _trade('QZGU6', '2026-06-08, 20:30:00', 1, 100.0, -100000,
                     cat='Futures', cur='CAD')
            + _trade('QZGU6', '2026-06-09, 10:00:00', -1, 101.0, 101000,
                     cat='Futures', cur='CAD', code='C'))
        self.assertEqual(_dates(txs),
                         [('2026-06-09', '00:00:00', '2026-06-09'),
                          ('2026-06-09', '10:00:00', '2026-06-09')])
        self.assertEqual(txs[0]['broker_time'], '2026-06-08 20:30:00 ET')


# ------------------------------------------------------------ TJ-0879
class TestCmeContinuousSession(unittest.TestCase):
    """CME moved its crypto futures to 24/7 trading from 16:00 CT
    (17:00 ET) on Friday May 29, 2026: trades from Friday evening
    through Sunday evening, and on holidays, take the next business
    day's trade date. No other CME future trades then."""

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_weekend_and_after_close_fills_take_the_next_day(self):
        for when, want in ((('2026-06-05', '17:30:00'), '2026-06-08'),
                           (('2026-06-06', '10:00:00'), '2026-06-08'),
                           (('2026-06-07', '12:00:00'), '2026-06-08'),
                           (('2026-06-07', '19:00:00'), '2026-06-08'),
                           (('2026-06-08', '17:30:00'), '2026-06-09'),
                           (('2026-05-29', '17:30:00'), '2026-06-01'),
                           # Independence Day (observed Fri July 3)
                           (('2026-07-03', '12:00:00'), '2026-07-06')):
            for cat in ('Futures', 'Options On Futures'):
                with self.subTest(when=when, cat=cat):
                    d, t, stamp = _ib_market_trade_date(
                        *when, cat, 'USD', '', symbol='QZBU6', exch='CME')
                    self.assertEqual((d, t), (want, '00:00:00'))
                    self.assertEqual(stamp, f"{when[0]} {when[1]} ET")

    def test_regular_hours_and_before_the_launch_are_unchanged(self):
        for when in (('2026-06-08', '10:00:00'), ('2026-06-05', '16:59:59'),
                     ('2026-06-08', '17:00:00'),
                     ('2026-05-29', '16:30:00'),    # before 17:00 ET
                     ('2026-05-22', '19:00:00'),    # a Friday before it
                     ('2026-05-23', '10:00:00')):   # a Saturday before it
            with self.subTest(when=when):
                self.assertEqual(
                    _ib_market_trade_date(*when, 'Futures', 'USD', '',
                                          symbol='QZBU6', exch='CME'),
                    (when[0], when[1], ''))

    def test_other_venues_keep_the_weekday_session(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-05', '19:00:00', 'Futures', 'USD',
                                  '', symbol='QZCLU6', exch='NYMEX'),
            ('2026-06-05', '19:00:00', ''))

    @rule("CA-DATE-SESSION")
    @rule("US-DATE-SESSION")
    def test_parse_dates_weekend_fills_monday(self):
        _, txs, _ = _parse_ib(
            HEAD + FII_H + _fut_fii('QZBU6', 'QZB', 'CME', 0.1) + FUT_H
            + _trade('QZBU6', '2026-06-05, 17:30:00', 1, 100.0, -10,
                     cat='Futures')
            + _trade('QZBU6', '2026-06-06, 10:00:00', 1, 101.0, -10.1,
                     cat='Futures')
            + _trade('QZBU6', '2026-06-08, 10:00:00', -2, 102.0, 20.4,
                     cat='Futures', code='C'))
        self.assertEqual([t['date'] for t in txs], ['2026-06-08'] * 3)
        self.assertEqual([t['date_settle'] for t in txs], ['2026-06-08'] * 3)


# ------------------------------------------------- TJ-0659 / TJ-0660
_ROC = ('Dividends,Data,USD,2025-01-10,"SYN(CA9990000001) Return of '
        'Capital USD 1 per Share",10\n')


def _listing_case(*later):
    return (HEAD + TRADES_H
            + _trade('SYN', '2025-01-02, 10:00:00', 10, 10, -100)
            + ''.join(later) + DIV_H + _ROC)


def _roc_symbols(txs):
    return [t['symbol'] for t in txs if t['action'] == 'ADJUST']


class TestListingReplayOrder(unittest.TestCase):
    def test_intraday_round_trip_is_replayed_in_clock_order(self):
        _, txs, _ = _parse_ib(_listing_case(
            _trade('SYN', '2025-02-03, 10:00:00', 10, 10, -100, cur='CAD',
                   code='O'),
            _trade('SYN', '2025-02-03, 11:00:00', -10, 10, 100, cur='CAD',
                   code='C')))
        self.assertEqual(_roc_symbols(txs), ['SYN.US'])

    def test_coded_short_opening_is_not_an_earlier_holding(self):
        _, txs, _ = _parse_ib(_listing_case(
            _trade('SYN', '2025-02-03, 10:00:00', -10, 10, 100, cur='CAD',
                   code='O'),
            _trade('SYN', '2025-02-04, 11:00:00', 10, 10, -100, cur='CAD',
                   code='C')))
        self.assertEqual(_roc_symbols(txs), ['SYN.US'])

    def test_partly_covered_short_is_not_an_earlier_holding(self):
        _, txs, _ = _parse_ib(_listing_case(
            _trade('SYN', '2025-02-03, 10:00:00', -10, 10, 100, cur='CAD',
                   code='O'),
            _trade('SYN', '2025-02-04, 11:00:00', 4, 10, -40, cur='CAD',
                   code='C')))
        self.assertEqual(_roc_symbols(txs), ['SYN.US'])

    def test_uncoded_sale_still_implies_an_earlier_holding(self):
        # A sale coded C (closing) with no buy before it: units held
        # before the statement, so both listings are held on Jan 10 and
        # the ROC keeps its ISIN listing (ambiguous, noted).
        _, txs, err = _parse_ib(_listing_case(
            _trade('SYN', '2025-02-03, 10:00:00', -10, 10, 100, cur='CAD',
                   code='C')))
        self.assertEqual(_roc_symbols(txs), ['SYN.TO'])
        self.assertIn('more than one listing', err)


if __name__ == '__main__':
    unittest.main()
