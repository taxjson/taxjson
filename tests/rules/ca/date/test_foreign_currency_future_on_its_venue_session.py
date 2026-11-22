"""Backlog fixes 2026-10-29, fix round 7 wave D, group `ib`: Interactive
Brokers, mostly regressions of this round's earlier fixes.

TJ-1041 (a same-day withdrawal walked before the timed Forex that funded
it), TJ-1042 / TJ-1043 (identical refunds of two accounts in combined
statements), TJ-1044 (a coded short open at the statement end read as a
holding before it opened), TJ-1120 (a JPY future on
CME dated in Tokyo time), TJ-1130 (the overlap step dropped a cancelled
trade's copy whichever account's row it was).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_r7d_ib
"""
import unittest
from taxjson.lib.brokerages.ib_extractor import _ib_market_trade_date
from tax_rules import rule
from taxjson.rules import ids as R


class TestForeignCurrencyFutureOnItsVenue(unittest.TestCase):
    """TJ-1120: a future on a venue with session data is dated by that
    venue's session, whatever currency it is quoted in.

    Formerly: test_fix_1029_r7d_ib.TestForeignCurrencyFutureOnItsVenue"""

    def test_jpy_future_on_cme_is_not_on_tokyo_time(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '10:00:00', 'Futures', 'JPY',
                                  '', symbol='QZNU6', exch='CME'),
            ('2026-06-08', '10:00:00', ''))

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_jpy_future_on_cme_follows_the_evening_session(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '19:00:00', 'Futures', 'JPY',
                                  '', symbol='QZNU6', exch='CME'),
            ('2026-06-09', '00:00:00', '2026-06-08 19:00:00 ET'))

    def test_future_option_too(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '10:00:00',
                                  'Options On Futures', 'AUD', '',
                                  symbol='QZNU6 C', exch='CME'),
            ('2026-06-08', '10:00:00', ''))

    def test_a_venue_the_data_does_not_name_stays_on_local_time(self):
        for exch in ('OSE.JPN', ''):
            with self.subTest(exch=exch):
                self.assertEqual(
                    _ib_market_trade_date('2026-06-08', '10:00:00',
                                          'Futures', 'JPY', '',
                                          symbol='QZNU6', exch=exch)[:2],
                    ('2026-06-08', '23:00:00'))

    def test_stocks_stay_on_local_time(self):
        self.assertEqual(
            _ib_market_trade_date('2026-06-08', '10:00:00', 'Stocks', 'JPY',
                                  'T', symbol='QZN', exch='CME')[:2],
            ('2026-06-08', '23:00:00'))


if __name__ == '__main__':
    unittest.main()
