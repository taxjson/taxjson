"""Settlement calendars (lib/market_calendar) and the settle dates the
parsers compute from them: IB (no settle column), Webull (settle date
only, trade date back-computed) and the shared helpers.

Holiday lists are checked against the exchanges' published schedules;
the Canadian bank-only settlement holidays (Remembrance Day, Truth and
Reconciliation) against Questrade/RBC printed settle dates and the Bank
of Canada's closures."""
import unittest
from datetime import date

from taxjson.lib import market_calendar as mc
from taxjson.lib.dates import (last_trade_date_settling_by, settlement_date,
                               settlement_lag_days)
from taxjson.lib.brokerages.base import BaseBrokerage
from taxjson.lib.brokerages.ib_extractor import IbBrokerage, get_ib_settlement

from test_parser_audit_2026_09b import (IB_TRADES_HDR, _ib_fii_futures,
                                        _ib_trade, _parse)
from tax_rules import rule


def _iso(ds):
    return sorted(d.isoformat() for d in ds)


class TestHolidayLists(unittest.TestCase):
    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_nyse_2025_2026(self):
        self.assertEqual(_iso(mc.nyse_holidays(2025)), [
            '2025-01-01', '2025-01-20', '2025-02-17', '2025-04-18',
            '2025-05-26', '2025-06-19', '2025-07-04', '2025-09-01',
            '2025-11-27', '2025-12-25'])
        self.assertEqual(_iso(mc.nyse_holidays(2026)), [
            '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03',
            '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07',
            '2026-11-26', '2026-12-25'])

    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_tsx_2025_2026(self):
        self.assertEqual(_iso(mc.tsx_holidays(2025)), [
            '2025-01-01', '2025-02-17', '2025-04-18', '2025-05-19',
            '2025-07-01', '2025-08-04', '2025-09-01', '2025-10-13',
            '2025-12-25', '2025-12-26'])
        self.assertEqual(_iso(mc.tsx_holidays(2026)), [
            '2026-01-01', '2026-02-16', '2026-04-03', '2026-05-18',
            '2026-07-01', '2026-08-03', '2026-09-07', '2026-10-12',
            '2026-12-25', '2026-12-28'])

    def test_observance_rules(self):
        # NYSE does not move a Saturday New Year's Day to Friday Dec 31.
        self.assertTrue(mc.is_trading_day('2021-12-31', 'USD'))
        # Juneteenth is an NYSE holiday only from 2022.
        self.assertTrue(mc.is_trading_day('2021-06-18', 'USD'))
        self.assertFalse(mc.is_trading_day('2022-06-20', 'USD'))
        # TSX: Christmas on a Saturday closes Mon 27 and Tue 28.
        self.assertIn(date(2021, 12, 27), mc.tsx_holidays(2021))
        self.assertIn(date(2021, 12, 28), mc.tsx_holidays(2021))
        # ...on a Sunday closes Mon 26 and Tue 27.
        self.assertIn(date(2022, 12, 26), mc.tsx_holidays(2022))
        self.assertIn(date(2022, 12, 27), mc.tsx_holidays(2022))
        # Canada Day on a Saturday moves to Monday.
        self.assertIn(date(2023, 7, 3), mc.tsx_holidays(2023))
        # New Year's Day on a Saturday moves to Monday on the TSX.
        self.assertIn(date(2022, 1, 3), mc.tsx_holidays(2022))

    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_us_bank_holidays_trade_but_do_not_settle(self):
        for d in ('2025-10-13', '2025-11-11'):    # Columbus, Veterans
            self.assertTrue(mc.is_trading_day(d, 'USD'), d)
            self.assertFalse(mc.is_settlement_day(d, 'USD'), d)
        # July 4 2026 is a Saturday: NYSE closes Friday July 3 (no
        # settlement) although the Federal Reserve is open.
        self.assertFalse(mc.is_settlement_day('2026-07-03', 'USD'))

    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_canadian_bank_holidays_trade_but_do_not_settle(self):
        for d in ('2025-11-11', '2023-11-13', '2025-09-30', '2023-10-02'):
            self.assertTrue(mc.is_trading_day(d, 'CAD'), d)
            self.assertFalse(mc.is_settlement_day(d, 'CAD'), d)
        # Truth and Reconciliation only from 2021.
        self.assertTrue(mc.is_settlement_day('2020-09-30', 'CAD'))

    def test_one_off_closure_still_settles(self):
        # 2025-01-09 (President Carter): NYSE closed, DTC settled.
        self.assertTrue(mc.is_settlement_day('2025-01-09', 'USD'))

    def test_other_currencies_weekends_only(self):
        self.assertTrue(mc.is_settlement_day('2025-12-25', 'GBP'))
        self.assertFalse(mc.is_settlement_day('2025-12-27', 'GBP'))


class TestSettlementDates(unittest.TestCase):
    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_holiday_inside_the_lag(self):
        cases = [('2025-04-17', 'USD', '2025-04-21'),   # Good Friday
                 ('2025-01-17', 'USD', '2025-01-21'),   # MLK
                 ('2025-08-29', 'USD', '2025-09-02'),   # Labor Day
                 ('2025-12-24', 'CAD', '2025-12-29'),   # Christmas/Boxing
                 ('2025-12-31', 'USD', '2026-01-02'),   # New Year
                 ('2025-11-10', 'CAD', '2025-11-12'),   # Remembrance Day
                 ('2025-10-10', 'USD', '2025-10-14'),   # Columbus Day
                 ('2025-10-13', 'USD', '2025-10-14')]   # traded on it
        for trade, cur, want in cases:
            self.assertEqual(settlement_date(trade, cur), want,
                             (trade, cur))

    @rule("CA-DATE-04")
    @rule("US-DATE-04")
    def test_settlement_eras(self):
        self.assertEqual(settlement_lag_days('2017-09-01', 'USD'), 3)
        self.assertEqual(settlement_lag_days('2017-09-05', 'USD'), 2)
        self.assertEqual(settlement_lag_days('2024-05-27', 'CAD'), 1)
        self.assertEqual(settlement_lag_days('2024-05-27', 'USD'), 2)
        # T+3 across Labor Day 2017.
        self.assertEqual(settlement_date('2017-09-01', 'USD'), '2017-09-07')
        # Options settle T+1 in every era and market (A2-0486, A2-1510).
        for trade, cur in (('2016-12-28', 'USD'), ('2017-09-01', 'CAD'),
                           ('2020-03-02', 'USD'), ('2025-03-04', 'GBP'),
                           ('2016-12-28', 'AUD')):
            self.assertEqual(settlement_lag_days(trade, cur, is_option=True),
                             1, (trade, cur))
        # A Friday option trade settles Monday; the stock trade T+3.
        self.assertEqual(settlement_date('2017-09-01', 'USD', is_option=True),
                         '2017-09-05')

    def test_rescue_deadline_walks_back_over_a_holiday(self):
        # Must settle by Canada Day 2026: a 06-30 CAD trade settles
        # 07-02, so the last safe trade date is 06-29.
        self.assertEqual(last_trade_date_settling_by('2026-07-01', 'CAD'),
                         '2026-06-29')
        self.assertEqual(last_trade_date_settling_by('2026-07-06', 'USD'),
                         '2026-07-02')

    def test_webull_trade_date_back_computed_over_holiday(self):
        b = BaseBrokerage.__new__(BaseBrokerage)
        # Settled Monday after Good Friday (T+1): traded Thursday.
        self.assertEqual(b.trade_date_from_settlement(
            '2025-04-21', 'USD', False, '%Y-%m-%d'), '2025-04-17')
        # Settled the Tuesday after Columbus Day: the latest trading day
        # settling then is the holiday Monday itself.
        self.assertEqual(b.trade_date_from_settlement(
            '2025-10-14', 'USD', True, '%Y-%m-%d'), '2025-10-13')


class TestIbSettlement(unittest.TestCase):
    OPT = 'Equity and Index Options'

    def test_stock_holiday_and_era(self):
        self.assertEqual(get_ib_settlement('2025-04-17', 'Stocks', 'USD'),
                         '2025-04-21')
        self.assertEqual(get_ib_settlement('2024-05-24', 'Stocks', 'USD'),
                         '2024-05-29')     # two days over Memorial Day
        self.assertEqual(get_ib_settlement('2025-12-24', 'Stocks', 'CAD'),
                         '2025-12-29')

    @rule("CA-DATE-09")
    @rule("US-DATE-09")
    def test_futures_default_trade_date(self):
        for cat in ('Futures', 'Options On Futures'):
            self.assertEqual(get_ib_settlement('2025-12-31', cat, 'USD'),
                             '2025-12-31')
            self.assertEqual(get_ib_settlement('2025-12-31', cat, 'USD',
                                               futures_settle='next_day'),
                             '2026-01-02')

    @rule("CA-DATE-10")
    @rule("US-DATE-12")
    def test_parser_uses_the_setting(self):
        body = (IB_TRADES_HDR
                + _ib_trade('Futures', 'QZFH6', '2025-12-31, 10:00:00',
                            1, 5000, -500, -2, 'O')
                + _ib_fii_futures('QZFH6', '0.1'))
        txs, _ = _parse(IbBrokerage(), body)
        self.assertEqual(txs[0]['date_settle'], '2025-12-31')
        nxt = IbBrokerage()
        nxt.futures_settle = 'next_day'
        txs, _ = _parse(nxt, body)
        self.assertEqual(txs[0]['date_settle'], '2026-01-02')


class TestFuturesSettleConfig(unittest.TestCase):
    def test_validation(self):
        from taxjson.bin import taxjson_run as tr
        cfg = {'settings': {'year': 2025, 'futures_settle': 'next_day', 'country': 'canada'},
               'accounts': {'m': {'type': 'taxable'}}}
        warnings = tr.validate_config(cfg)
        self.assertFalse(any('futures_settle' in w for w in warnings))
        cfg['settings']['futures_settle'] = 'settle'
        with self.assertRaises(SystemExit):
            tr.validate_config(cfg)

    def test_init_template_documents_it_with_its_default(self):
        from taxjson.lib.config_template import render_init
        for country in ('canada', 'usa'):
            text = render_init(country, 2025)[0]
            self.assertRegex(text, r'(?m)^# futures_settle\s+= "trade"\s+# '
                                   r'trade \| next_day')


if __name__ == '__main__':
    unittest.main()
