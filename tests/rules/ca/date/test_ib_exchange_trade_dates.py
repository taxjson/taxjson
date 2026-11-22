"""Regression tests for the low-round `ibparse` audit findings (IB
statement parser, shared broker helpers, taxjson-brokerage, broker
detection, the transaction schema) and the overnight-session
item. Every fixture is synthetic: invented tickers and ISINs, fake
account ids marked pii-ok.

Formerly: test_fix_l_ibparse"""
import unittest

from taxjson.lib import country as C
from tax_rules import rule
from tax_rules.dual import gains_both
from taxjson.rules import ids as R

from _support.ib_statement_rows import _trade, _parse_ib, _brokerage_cli
from _style import CapturedWidth
from _support.ib_income import _book, _trades


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------- OVERNIGHT / G5-1
class TestIbExchangeTradeDate(unittest.TestCase):
    """IB stamps US Eastern clock time. An overnight-session US fill
    trades on the next trading day; an ASX fill on the Sydney date.

    Formerly: test_fix_l_ibparse.TestIbExchangeTradeDate"""

    def _one(self, when, sym='QZN', cur='USD', qty=10, price=10.0):
        _, txs, _ = _parse_ib(_trades(
            _trade(sym, when, qty, price, -qty * price, cur=cur)))
        self.assertEqual(len(txs), 1)
        return txs[0]

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_christmas_night_fill_trades_next_day(self):
        t = self._one('2025-12-25, 20:52:37')
        self.assertEqual(t['date'], '2025-12-26')
        self.assertEqual(t['time'], '00:00:00')
        # T+1 from Friday Dec 26: Monday Dec 29 (was Dec 26).
        self.assertEqual(t['date_settle'], '2025-12-29')
        self.assertEqual(t['broker_time'], '2025-12-25 20:52:37 ET')

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_sunday_night_fill_trades_monday(self):
        t = self._one('2026-07-05, 21:00:00')         # a Sunday
        self.assertEqual((t['date'], t['date_settle']),
                         ('2026-07-06', '2026-07-07'))

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_regular_and_after_midnight_fills_are_unchanged(self):
        for when, d in (('2026-07-08, 15:30:00', '2026-07-08'),
                        ('2026-07-08, 19:59:59', '2026-07-08'),
                        ('2026-07-09, 00:30:00', '2026-07-09')):
            with self.subTest(when=when):
                t = self._one(when)
                self.assertEqual(t['date'], d)
                self.assertNotIn('broker_time', t)

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_friday_night_fill_is_left_for_check_dates(self):
        # There is no Friday-night session: the row stays as stamped and
        # check-dates reports it.
        from datetime import date, time
        from taxjson.lib.check_dates import check_trade_time
        t = self._one('2026-07-10, 21:00:00')
        self.assertEqual((t['date'], t['time']), ('2026-07-10', '21:00:00'))
        hit = check_trade_time('us-equity', date(2026, 7, 10), time(21),
                               t['symbol'], 'USD')
        self.assertEqual(hit[1], 'friday-night-trade')

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_tsx_and_option_rows_are_not_shifted(self):
        t = self._one('2026-07-08, 21:00:00', cur='CAD')
        self.assertEqual(t['date'], '2026-07-08')

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_asx_fill_is_dated_in_sydney(self):
        # 18:47 EST on Tue Feb 17 is 10:47 AEDT on Wed Feb 18.
        t = self._one('2026-02-17, 18:47:51', sym='QZA', cur='AUD')
        self.assertEqual(t['symbol'], 'QZA.AX')
        self.assertEqual((t['date'], t['time']), ('2026-02-18', '10:47:51'))
        self.assertEqual(t['date_settle'], '2026-02-20')      # T+2
        self.assertEqual(t['broker_time'], '2026-02-17 18:47:51 ET')

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_dec30_overnight_sale_settles_in_january(self):
        # Wed Dec 30 2026, 20:30 ET: trade date Thu Dec 31, T+1 is Jan 1
        # (a holiday), then a weekend: settles Mon 2027-01-04.
        _, txs, _ = _parse_ib(_trades(
            _trade('QZN', '2026-06-01, 10:00:00', 10, 10.0, -100.0),
            _trade('QZN', '2026-12-30, 20:30:00', -10, 15.0, 150.0,
                   code='C')))
        sale = txs[1]
        self.assertEqual((sale['date'], sale['date_settle']),
                         ('2026-12-31', '2027-01-04'))
        book = _book(txs)
        r26 = gains_both(book, year=2026)
        r27 = gains_both(book, year=2027)
        # Canada (settle date): a 2027 disposition.
        self.assertAlmostEqual(r26[C.CANADA]['summary']['total_gain'], 0.0)
        self.assertAlmostEqual(r27[C.CANADA]['summary']['total_gain'], 50.0)
        # US (trade date): 2026, traded Dec 31.
        self.assertAlmostEqual(r26[C.USA]['summary']['total_gain'], 50.0)
        self.assertEqual([t['date'] for t in r26[C.USA]['transactions']],
                         ['2026-12-31'])

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_overnight_buy_sorts_before_the_days_regular_sale(self):
        # Thu 22:00 buy (trade date Fri) sold Fri 10:00: a long round
        # trip in both engines, not a short sale covered that night.
        _, txs, _ = _parse_ib(_trades(
            _trade('QZN', '2026-07-09, 22:00:00', 10, 10.0, -100.0),
            _trade('QZN', '2026-07-10, 10:00:00', -10, 12.0, 120.0,
                   code='C')))
        r = gains_both(_book(txs), year=2026)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                self.assertEqual([t.get('direction')
                                  for t in r[c]['transactions']], ['LONG'])
                self.assertAlmostEqual(r[c]['summary']['total_gain'], 20.0)

    def test_broker_time_survives_normalization(self):
        rc, out, err, _ = _brokerage_cli({'ib.csv': _trades(
            _trade('QZN', '2025-12-25, 20:52:37', 10, 10.0, -100.0))})
        self.assertEqual(rc, 0, err)
        t = out['transactions'][0]
        self.assertEqual(t['broker_time'], '2025-12-25 20:52:37 ET')
        self.assertNotIn('unknown field', err)


class TestSettleToTradeAtTheCutover(unittest.TestCase):
    """S056-01: the T+2 settles ON the cutover day round-trip.

    Formerly: test_fix_l_ibparse.TestSettleToTradeAtTheCutover"""

    def test_cutover_days(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.lib.dates import settlement_date
        b = BaseBrokerage()
        for settle, cur in (('2024-05-27', 'CAD'), ('2024-05-28', 'USD')):
            with self.subTest(cur=cur):
                t = b.trade_date_from_settlement(settle, cur, False,
                                                 '%Y-%m-%d')
                self.assertEqual(t, '2024-05-23')
                self.assertEqual(settlement_date(t, cur), settle)


if __name__ == '__main__':
    unittest.main()
