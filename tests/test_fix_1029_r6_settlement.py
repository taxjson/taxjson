"""Backlog fix round 6 of 2026-10-29, group `settlement` (TJ-0705): a
US-dollar trade of a Canadian listing (a TSX fund's US-dollar unit) with
no printed settlement date settles on a day both Canadian and US
clearing settle. CDS settles US dollars only when US clearing does, so
a US-dollar trade the day before a US-only holiday (Juneteenth, the
Fourth of July, US Thanksgiving, Memorial Day, Martin Luther King Jr.
Day) settles a day later than a Canadian-dollar one: the TMX "Settlement
Schedule for 2026 Holidays" and the CSE 2025 Holiday Settlement
Schedule list those dates "for U.S. Dollar Traded Securities only".

The parsers keyed the fallback on the listing's market alone, so a
June 18, 2025 buy of a TSX unit in US dollars settled June 19
(Juneteenth) and its cost was converted at that day's rate.

Every fixture is synthetic: invented tickers, fake account ids and
made-up amounts and rates.
"""
import contextlib
import copy
import io
import unittest
from datetime import date
from decimal import Decimal

from tax_rules import rule

from test_fix_ibparse import HEAD, TRADES_H, _trade, _parse_ib
from test_fix_a2_ib_dates import FII_H, _stock_fii
from test_fix_l_ibparse import _book
from test_fix_def_rest import _qt_settle, _gen_settle, _rbc_settle

from taxjson.lib import market_calendar as mc
from taxjson.lib.dates import (last_trade_date_settling_by, market_of,
                               settlement_calendar, settlement_date,
                               settlement_lag_days)

CA_USD = mc.CA_USD


def _settle(trade, key):
    return settlement_date(trade, key)


class TestUsDollarCanadianCalendar(unittest.TestCase):
    """The calendar key of a US-dollar trade on a Canadian market."""

    @rule("CA-DATE-05")
    def test_the_key_comes_from_the_trade_currency_not_the_ticker(self):
        self.assertEqual(CA_USD, "CAD+USD")
        self.assertEqual(settlement_calendar("QZX.U.TO", "USD"), CA_USD)
        # The listing's trade currency decides, not a ".U" in the ticker.
        self.assertEqual(settlement_calendar("QZX.TO", "USD"), CA_USD)
        self.assertEqual(settlement_calendar("QZX.V", "usd"), CA_USD)
        self.assertEqual(settlement_calendar("QZX.U.TO", "CAD"), "CAD")
        self.assertEqual(settlement_calendar("QZX.TO", "CAD"), "CAD")
        self.assertEqual(settlement_calendar("QZX.TO", None), "CAD")
        # A US stock settled in Canadian dollars, other markets: as before.
        self.assertEqual(settlement_calendar("QZA.US", "CAD"), "USD")
        self.assertEqual(settlement_calendar("QZL.L", "USD"), "GBP")
        self.assertEqual(settlement_calendar("QZE", "EUR"), "EUR")
        self.assertEqual(settlement_calendar("QZM", "USD"), "USD")
        # The listing's market itself is unchanged.
        self.assertEqual(market_of("QZX.U.TO", "USD"), "CAD")

    @rule("CA-DATE-05")
    def test_a_day_settles_when_both_clearing_houses_settle(self):
        juneteenth = date(2025, 6, 19)
        self.assertTrue(mc.is_settlement_day(juneteenth, "CAD"))
        self.assertFalse(mc.is_settlement_day(juneteenth, "USD"))
        self.assertFalse(mc.is_settlement_day(juneteenth, CA_USD))
        canada_day = date(2025, 7, 1)
        self.assertFalse(mc.is_settlement_day(canada_day, CA_USD))
        self.assertTrue(mc.is_settlement_day(date(2025, 6, 20), CA_USD))
        # The TSX trades on a US holiday: its trading days are the TSX's.
        self.assertTrue(mc.is_trading_day(juneteenth, CA_USD))
        self.assertFalse(mc.is_trading_day(canada_day, CA_USD))
        self.assertEqual(mc.market_for(CA_USD), "CA")

    @rule("CA-DATE-04")
    def test_the_cycle_is_the_canadian_markets(self):
        # T+1 from 2024-05-27 in Canada (the US moved on the 28th).
        self.assertEqual(settlement_lag_days("2024-05-27", CA_USD), 1)
        self.assertEqual(settlement_lag_days("2024-05-24", CA_USD), 2)
        self.assertEqual(settlement_lag_days("2017-09-01", CA_USD), 3)

    @rule("CA-DATE-05")
    def test_tmx_2026_us_dollar_settlement_schedule(self):
        # TMX, "Settlement Schedule for 2026 Holidays": the US holidays,
        # "for U.S. Dollar Traded Securities only".
        for trade, usd, cad in (
                ("2026-01-16", "2026-01-20", "2026-01-19"),  # MLK Day
                ("2026-05-22", "2026-05-26", "2026-05-25"),  # Memorial Day
                ("2026-06-18", "2026-06-22", "2026-06-19"),  # Juneteenth
                ("2026-07-02", "2026-07-06", "2026-07-03"),  # July 4 (obs.)
                ("2026-11-25", "2026-11-27", "2026-11-26"),  # US Thanksgiving
        ):
            with self.subTest(trade=trade):
                self.assertEqual(_settle(trade, CA_USD), usd)
                self.assertEqual(_settle(trade, "CAD"), cad)
        # The Canadian holidays hold for both (the same schedule).
        for trade, both in (
                ("2026-06-30", "2026-07-02"),    # Canada Day
                ("2026-09-29", "2026-10-01"),    # Truth and Reconciliation
                ("2026-10-09", "2026-10-13"),    # Thanksgiving
                ("2026-11-10", "2026-11-12"),    # Remembrance Day
                ("2026-12-24", "2026-12-29"),    # Christmas, Boxing Day
                ("2026-12-31", "2027-01-04"),    # New Year's Day
        ):
            with self.subTest(trade=trade):
                self.assertEqual(_settle(trade, CA_USD), both)
                self.assertEqual(_settle(trade, "CAD"), both)

    @rule("CA-DATE-05")
    def test_cse_2025_schedule_trades_on_the_us_holiday_itself(self):
        # CSE 2025 Holiday Settlement Schedule: a T+1 USD trade made on
        # the US holiday settles the next day both settle.
        for trade, usd in (("2025-01-20", "2025-01-21"),
                           ("2025-05-26", "2025-05-27"),
                           ("2025-06-18", "2025-06-20"),
                           ("2025-06-19", "2025-06-20"),
                           ("2025-07-03", "2025-07-07"),
                           ("2025-07-04", "2025-07-07"),
                           ("2025-11-26", "2025-11-28"),
                           ("2025-11-27", "2025-11-28")):
            with self.subTest(trade=trade):
                self.assertEqual(_settle(trade, CA_USD), usd)

    @rule("CA-DATE-05")
    def test_walk_back_to_the_last_trade_settling_by_a_date(self):
        # A sale made June 18, 2025 settles June 20: the last US-dollar
        # trade settling by June 19 is the 17th (the 18th in CAD).
        self.assertEqual(last_trade_date_settling_by("2025-06-19", CA_USD),
                         "2025-06-17")
        self.assertEqual(last_trade_date_settling_by("2025-06-19", "CAD"),
                         "2025-06-18")
        self.assertEqual(
            mc.sub_settlement_days("2025-06-20", 1, CA_USD).isoformat(),
            "2025-06-19")


class TestParsersSettleUsDollarUnits(unittest.TestCase):
    """The blank-settle fallback of every parser (CA-DATE-05: one rule)."""

    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_ib_juneteenth_buy_settles_the_next_day_both_settle(self):
        _, txs, err = _parse_ib(
            HEAD + FII_H + _stock_fii('QZX.U', 'TSE') + TRADES_H
            + _trade('QZX.U', '2025-06-18, 10:00:00', 10, 10, -100,
                     acct='U1234567'))
        self.assertEqual([(t['symbol'], t['currency'], t['date_settle'])
                          for t in txs],
                         [('QZX.U.TO', 'USD', '2025-06-20')], err)

    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_ib_cad_line_keeps_the_canadian_calendar(self):
        _, txs, err = _parse_ib(
            HEAD + FII_H + _stock_fii('QZX', 'TSE') + TRADES_H
            + _trade('QZX', '2025-06-18, 10:00:00', 10, 10, -100,
                     cur='CAD', acct='U1234567'))
        self.assertEqual([(t['symbol'], t['date_settle']) for t in txs],
                         [('QZX.TO', '2025-06-19')], err)

    @rule("CA-DATE-05")
    def test_questrade_rbc_and_generic_agree(self):
        self.assertEqual(_qt_settle("2025-06-18", "QZU.U.TO", "USD"),
                         ("QZU.U.TO", "2025-06-20"))
        self.assertEqual(_gen_settle("2025-06-18", "QZU.U.TO", "USD"),
                         ("QZU.U.TO", "2025-06-20"))
        from unittest import mock
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        with mock.patch.object(RbcBrokerage, "security_overrides",
                               [("qz u.s. dlr currency etf", "USD",
                                 "QZD.U.TO")]):
            self.assertEqual(
                _rbc_settle("June 18, 2025", "QZD.U",
                            "QZ U.S. DLR CURRENCY ETF", "USD"),
                ("QZD.U.TO", "2025-06-20"))
        # The Canadian-dollar line: June 19, as before.
        self.assertEqual(_gen_settle("2025-06-18", "QZU.TO", "CAD"),
                         ("QZU.TO", "2025-06-19"))

    @rule("CA-DATE-03")
    def test_a_printed_settlement_date_wins(self):
        from test_fix_rbcqt import q, qt_parse
        txs, err, _ = qt_parse(q(td="2025-06-18", sd="2025-06-19",
                                 sym="QZU.U.TO", desc="QZU FUND",
                                 qty="10", price="10", gross="-100",
                                 comm="0", net="-100", cur="USD"))
        self.assertEqual([t['date_settle'] for t in txs], ['2025-06-19'],
                         err)


class TestUsDollarUnitGain(unittest.TestCase):
    """The audit's case: the cost is converted on the settlement date."""

    @rule("CA-DATE-05")
    def test_cost_converts_on_the_real_settlement_date(self):
        from taxjson.bin.taxjson_convert_currency import convert_transaction
        from taxjson.lib.pipeline import GainsRequest, run_gains
        _, rows, err = _parse_ib(
            HEAD + FII_H + _stock_fii('QZX.U', 'TSE') + TRADES_H
            + _trade('QZX.U', '2025-06-18, 10:00:00', 10, 10, -100,
                     acct='U1234567')
            + _trade('QZX.U', '2025-06-23, 10:00:00', -10, 11, 110,
                     code='C', acct='U1234567'))
        self.assertEqual([r['date_settle'] for r in rows],
                         ['2025-06-20', '2025-06-24'], err)
        rates = {'USD': {'2025-06-19': Decimal('1.30'),
                         '2025-06-20': Decimal('1.40'),
                         '2025-06-24': Decimal('1.40')}}
        book = [convert_transaction(t, 'CAD', rates, None)
                for t in _book(rows)]
        with contextlib.redirect_stderr(io.StringIO()):
            res = run_gains(copy.deepcopy(book), [], [],
                            req=GainsRequest(country="canada", year=2025))
        self.assertAlmostEqual(res['summary']['total_gain'], 14.0)


class TestCheckDatesUsDollarUnit(unittest.TestCase):
    """`tjs check-dates` reads the same calendar."""

    @rule("CA-DATE-05")
    def test_tt_settle_horizon_and_cycle(self):
        from taxjson.lib import check_dates as cd
        self.assertEqual(
            cd._tt_settle_horizon(date(2025, 6, 18), "ca-equity",
                                  "QZX.U.TO", "USD"), date(2025, 6, 20))
        self.assertEqual(
            cd._tt_settle_horizon(date(2025, 6, 18), "ca-equity",
                                  "QZX.TO", "CAD"), date(2025, 6, 19))


if __name__ == "__main__":
    unittest.main()
