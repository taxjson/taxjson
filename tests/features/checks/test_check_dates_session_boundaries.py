"""Re-audit-2 test pins (lists tests-pins-07/08, fork g3): check-dates
session and range boundaries, edge-cases day-30 boundaries, the R1-28
assignment pairing, income dating (listings, split-share roots, the US
§852(b)(7) warning window) and the per-ticker income sums.

Each test kills a mutant that survived the whole suite in the second
audit (the docstrings name the A2 id and the line pinned). Every
fixture is SYNTHETIC: invented tickers (QZ*, XYZ, GHI), fake accounts.

Formerly: test_fix_a2_pins3_g3
"""
import json
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.check_dates import analyze as dates_analyze
from taxjson.lib.check_dates import check_trade_time


# ======================================================== check-dates
def _chk(cls, d, t=None, sym="X.US", cur="USD"):
    r = check_trade_time(cls, date.fromisoformat(d),
                         time.fromisoformat(t) if t else None, sym, cur)
    return r[1] if r else None


class TestCheckDatesOvernightBoundary(unittest.TestCase):
    """A2-0877, A2-1531, A2-1545 (overnight half): check_dates
    OVERNIGHT_OPEN = dtime(20, 0) and `t >= OVERNIGHT_OPEN`. 2025-06-08
    is a Sunday, 2025-06-06 a Friday.

    Formerly: test_fix_a2_pins3_g3.TestCheckDatesOvernightBoundary"""

    def test_sunday_overnight_opens_at_20_00_exactly(self):
        self.assertIsNone(_chk("us-equity", "2025-06-08", "20:00:00"))
        self.assertIsNone(_chk("us-equity", "2025-06-08", "20:30:00"))
        self.assertEqual(_chk("us-equity", "2025-06-08", "19:59:59"),
                         "weekend-trade")

    def test_friday_night_from_20_00_exactly(self):
        self.assertEqual(_chk("us-equity", "2025-06-06", "20:00:00"),
                         "friday-night-trade")
        self.assertEqual(_chk("us-equity", "2025-06-06", "20:30:00"),
                         "friday-night-trade")
        self.assertIsNone(_chk("us-equity", "2025-06-06", "19:59:59"))


class TestCheckDatesFuturesBoundaries(unittest.TestCase):
    """A2-1545 (futures half), A2-1566: FUTURES_OPEN = dtime(17, 0)
    with `t < FUTURES_OPEN` (Sunday), and the Friday close
    `t > dtime(18, 0)`.

    Formerly: test_fix_a2_pins3_g3.TestCheckDatesFuturesBoundaries"""

    def test_sunday_open_at_17_00(self):
        self.assertIsNone(_chk("futures", "2025-06-08", "17:00:00"))
        self.assertIsNone(_chk("futures", "2025-06-08", "17:30:00"))
        self.assertIsNone(_chk("futures", "2025-06-08", "18:30:00"))
        self.assertEqual(_chk("futures", "2025-06-08", "16:59:59"),
                         "futures-sunday-early")

    def test_friday_close_after_18_00(self):
        self.assertIsNone(_chk("futures", "2025-06-06", "18:00:00"))
        self.assertEqual(_chk("futures", "2025-06-06", "18:00:01"),
                         "futures-friday-late")
        self.assertEqual(_chk("futures", "2025-06-06", "18:30:00"),
                         "futures-friday-late")


class TestCheckDatesHolidayOvernight(unittest.TestCase):
    """A2-1546: check_dates `if not mc.is_trading_day(d, "USD") and not
    overnight` — an overnight fill on a market-holiday evening trades
    for the next session, so it is not a holiday trade.

    Formerly: test_fix_a2_pins3_g3.TestCheckDatesHolidayOvernight"""

    def test_christmas_evening_overnight_is_not_a_holiday_trade(self):
        # 2025-12-25 is a Thursday (NYSE closed); the overnight session
        # that evening trades for Friday 2025-12-26.
        self.assertIsNone(_chk("us-equity", "2025-12-25", "21:00:00"))
        self.assertIsNone(_chk("us-equity", "2025-12-25", "20:00:00"))
        self.assertEqual(_chk("us-equity", "2025-12-25", "10:00:00"),
                         "holiday-trade")

    def test_eve_of_good_friday_is_a_normal_overnight(self):
        # Thursday 2025-04-17, the evening before Good Friday.
        self.assertIsNone(_chk("us-equity", "2025-04-17", "21:00:00"))

    @rule(R.CA_DATE_SESSION)
    def test_ib_overnight_roll_skips_the_holiday(self):
        """The IB twin (ib_extractor._ib_next_trading_day's
        is_trading_day loop): a Thursday-night overnight fill before
        Good Friday trades on the Monday, a Christmas Eve one on Dec 26,
        and a Christmas-evening one on Dec 26 too."""
        from taxjson.lib.brokerages.ib_extractor import _ib_market_trade_date
        for (d, t), want in ((("2025-04-17", "21:00:00"), "2025-04-21"),
                             (("2025-12-24", "21:00:00"), "2025-12-26"),
                             (("2025-12-25", "21:00:00"), "2025-12-26")):
            got, tm, stamp = _ib_market_trade_date(d, t, "Stocks", "USD",
                                                   "US")
            self.assertEqual(got, want, (d, t))
            self.assertEqual(tm, "00:00:00")
            self.assertTrue(stamp)


def _dates_project(rows):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    work = root / "work"
    work.mkdir()
    (work / "m_ib.json").write_text(json.dumps(rows))
    (work / "m_sources.list").write_text("ib/a.csv\n")
    cfg = {"settings": {"year": 2025},
           "accounts": {"m": {"type": "taxable"}}}
    return td, root, cfg


def _dr(d, settle, sym="ABC.US"):
    return {"action": "BUYSELL", "date": d, "date_settle": settle,
            "time": "10:00:00", "symbol": sym, "quantity": 1,
            "price": 10.0, "currency": "USD", "description": ""}


class TestCheckDatesLagAndFloor(unittest.TestCase):
    """A2-1566: the settle-late lag (`lag_days > 7`) and the year floor
    (`td.year < EARLIEST_YEAR`, 1900 since backlog TJ-0820).

    Formerly: test_fix_a2_pins3_g3.TestCheckDatesLagAndFloor"""

    def _codes(self, rows):
        td, root, cfg = _dates_project(rows)
        with td:
            doc = dates_analyze(root, cfg, today=date(2026, 1, 1))
        return {(i["date"], i["code"]) for i in doc["issues"]}

    def test_settle_late_after_more_than_seven_days(self):
        # Tue 2025-06-10: 8 days -> settle-late (WARN), 7 days -> only a
        # cycle note.
        codes = self._codes([_dr("2025-06-10", "2025-06-18"),
                             _dr("2025-06-03", "2025-06-10")])
        self.assertIn(("2025-06-10", "settle-late"), codes)
        self.assertIn(("2025-06-03", "settle-cycle"), codes)
        self.assertNotIn(("2025-06-03", "settle-late"), codes)

    def test_dates_before_1900_are_out_of_range(self):
        codes = self._codes([_dr("1899-06-12", "1899-06-15"),
                             _dr("1900-06-12", "1900-06-15")])
        self.assertIn(("1899-06-12", "out-of-range"), codes)
        self.assertNotIn(("1900-06-12", "out-of-range"), codes)


if __name__ == "__main__":
    unittest.main()
