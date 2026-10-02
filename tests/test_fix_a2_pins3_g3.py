"""Re-audit-2 test pins (lists tests-pins-07/08, fork g3): check-dates
session and range boundaries, edge-cases day-30 boundaries, the R1-28
assignment pairing, income dating (listings, split-share roots, the US
§852(b)(7) warning window) and the per-ticker income sums.

Each test kills a mutant that survived the whole suite in the second
audit (the docstrings name the A2 id and the line pinned). Every
fixture is SYNTHETIC: invented tickers (QZ*, XYZ, GHI), fake accounts.
"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from tax_rules import rule, rule_absent

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
    is a Sunday, 2025-06-06 a Friday."""

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
    `t > dtime(18, 0)`."""

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
    for the next session, so it is not a holiday trade."""

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

    @rule("CA-DATE-SESSION")
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
    """A2-1566: the settle-late lag (`lag_days > 7`) and the 1990 floor
    (`td.year < 1990`)."""

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

    def test_dates_before_1990_are_out_of_range(self):
        codes = self._codes([_dr("1989-06-12", "1989-06-15"),
                             _dr("1990-06-12", "1990-06-15")])
        self.assertIn(("1989-06-12", "out-of-range"), codes)
        self.assertNotIn(("1990-06-12", "out-of-range"), codes)


# ========================================================= edge-cases
from taxjson.lib.edge_cases import analyze as edge_analyze  # noqa: E402


def _etx(acct, action, d, settle, sym, qty, typ=""):
    return {"account": acct, "action": action, "date": d,
            "date_settle": settle, "time": "10:00:00", "symbol": sym,
            "quantity": qty, "net_amount": 0.0, "price": 0.0,
            "currency": "CAD", "type": typ, "id": f"{sym}-{d}-{qty}"}


def _egain(acct, d, settle, sym, qty, raw, denied=0.0):
    return {"account": acct, "date": d, "date_settle": settle,
            "symbol": sym, "qty": qty, "gain": raw + denied,
            "raw_gain": raw, "disallowed_amount": denied,
            "permanently_disallowed": 0.0, "proceeds": 1000.0,
            "cost": 1000.0 - raw, "id": f"g-{sym}-{d}"}


def _edge_project(rows, gains, country="canada"):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    work = root / "work"
    work.mkdir()
    (work / "margin_base.json").write_text(json.dumps(rows))
    (work / "margin_gains_wash.json").write_text(json.dumps(
        {"transactions": gains, "inventory": []}))
    cfg = {"settings": {"year": 2025, "country": country},
           "accounts": {"margin": {"type": "taxable"}}}
    return td, root, cfg


class TestEdgeCasesDay30(unittest.TestCase):
    """A2-0535: edge_cases.window_edges `inside=off <= WINDOW` (sale),
    `"inside": abs(off) <= WINDOW` (long call) and calls_in_windows
    `abs((d - ld).days) > WINDOW`: day 30 is inside, day 31 outside, for
    each item kind. Canada counts on settlement dates: the loss settles
    Tue 2025-03-04, so day 30 is 2025-04-03 and day 31 2025-04-04."""

    ROWS = [
        _etx("margin", "BUYSELL", "2025-01-10", "2025-01-13", "GHI.TO", 100),
        _etx("margin", "BUYSELL", "2025-03-03", "2025-03-04", "GHI.TO", -60),
        _etx("margin", "BUYSELL", "2025-03-05", "2025-03-06", "GHI.TO", 40),
        _etx("margin", "BUYSELL", "2025-04-02", "2025-04-03", "GHI.TO", -10),
        _etx("margin", "BUYSELL", "2025-04-03", "2025-04-04", "GHI.TO", -10),
        _etx("margin", "BUYSELL", "2025-04-02", "2025-04-03",
             "GHI260116C00010000.TO", 1),
        _etx("margin", "BUYSELL", "2025-04-03", "2025-04-04",
             "GHI260116C00012000.TO", 1),
    ]
    GAINS = [_egain("margin", "2025-03-03", "2025-03-04", "GHI.TO", 60,
                    -300.0, denied=100.0)]

    def _doc(self):
        td, root, cfg = _edge_project(self.ROWS, self.GAINS)
        with td:
            return edge_analyze(root, cfg)

    @rule("CA-RPT-07", "CA-SL-01", "CA-SL-02")
    def test_sale_and_long_call_day_30_inside_day_31_outside(self):
        doc = self._doc()
        (edge,) = [r for r in doc["window_edges"] if r["symbol"] == "GHI.TO"]
        got = {(i["kind"], i["day"]): i["inside"] for i in edge["items"]}
        self.assertEqual(got[("sale", 30)], True)
        self.assertEqual(got[("sale", 31)], False)
        self.assertEqual(got[("long call", 30)], True)
        self.assertEqual(got[("long call", 31)], False)

    @rule("CA-RPT-07", "CA-SL-01")
    def test_calls_in_windows_takes_day_30_not_day_31(self):
        doc = self._doc()
        (row,) = [r for r in doc["calls_in_windows"]
                  if r["symbol"] == "GHI.TO"]
        self.assertEqual([(i["day"], i["option"]) for i in row["items"]],
                         [(30, "GHI260116C00010000.TO")])


class TestEdgeCasesStockDividend(unittest.TestCase):
    """A2-1547 (a real bug, fixed): a US stock dividend is not a
    purchase (US-STKDIV-01), so edge-cases no longer lists it as an
    in-window acquisition the engine ignores; Canada still lists it
    (CA-STKDIV-01: an acquisition at $0 that counts for s.54). Gate:
    country.stock_dividend_in_loss_window."""

    ROWS = [
        _etx("margin", "BUYSELL", "2025-01-02", "2025-01-03", "XYZ.US", 200),
        _etx("margin", "BUYSELL", "2025-05-20", "2025-05-21", "XYZ.US", -100),
        _etx("margin", "BUYSELL", "2025-06-18", "2025-06-18", "XYZ.US", 10,
             typ="stock_dividend"),
    ]
    GAINS = [_egain("margin", "2025-05-20", "2025-05-21", "XYZ.US", 100,
                    -1000.0)]

    def _items(self, country):
        td, root, cfg = _edge_project(self.ROWS, self.GAINS, country)
        with td:
            doc = edge_analyze(root, cfg)
        return [i for r in doc["window_edges"] for i in r["items"]]

    @rule("US-RPT-05", "US-STKDIV-01")
    @rule_absent("CA-STKDIV-01", country="usa")
    @rule("CA-STKDIV-01", "CA-RPT-07")
    def test_stock_dividend_window_item_by_country(self):
        self.assertEqual(self._items("usa"), [])
        ca = self._items("canada")
        self.assertEqual([(i["kind"], i["day"], i["inside"]) for i in ca],
                         [("acquisition", 28, True)])

    def test_country_table(self):
        from taxjson.lib.country import stock_dividend_in_loss_window
        self.assertTrue(stock_dividend_in_loss_window("canada"))
        self.assertFalse(stock_dividend_in_loss_window("usa"))


# ====================================================== income dating
from taxjson.lib.income_dating import IncomeRules  # noqa: E402

# Spelled out here, not read from the module: dropping an entry there
# must fail a test (re-audit A2-1562).
_CA_SUFFIXES = ("TO", "V", "CN", "NE", "VN")
_SPLIT_SHARE_ROOTS = ("BK", "DF", "DFN", "DGS", "ENS", "FFN", "FTN", "GDV",
                      "LBS", "LCS", "LFE", "PDV", "PIC", "PWI", "SBC", "SBN",
                      "WFS", "XMF", "XTD", "YCM")


def _inc(**kw):
    r = {"action": "DIVIDEND", "symbol": "ZZF.US", "date": "2026-01-15",
         "currency": "USD", "net_amount": 10.0, "gross_amount": 10.0}
    r.update(kw)
    return r


def _dist(symbol):
    return _inc(symbol=symbol, currency="CAD", income_label="distribution",
                record_date="2025-12-31")


class TestCanadaListingsAndSplitShares(unittest.TestCase):
    """A2-0924 (M13, 'V'), A2-1562: every Canadian listing suffix in
    CA_LISTING_SUFFIXES dates a trust's distribution by its record date,
    and every SPLIT_SHARE_ROOTS entry (a corporation) keeps the pay
    date."""

    @rule("CA-INC-DATE-TRUST")
    def test_every_canadian_listing_is_a_canadian_trust(self):
        rules = IncomeRules("canada")
        for sfx in _CA_SUFFIXES:
            with self.subTest(suffix=sfx):
                self.assertEqual(rules.income_date(_dist(f"ZZT.UN.{sfx}")),
                                 "2025-12-31")
        # A US listing is a foreign fund: the pay date.
        self.assertEqual(rules.income_date(_dist("ZZT.US")), "2026-01-15")

    @rule("CA-INC-DATE-TRUST", "CA-INC-DATE-DIV")
    def test_every_split_share_root_keeps_the_pay_date(self):
        rules = IncomeRules("canada")
        for root in _SPLIT_SHARE_ROOTS:
            for sym in (f"{root}.TO", f"{root}.PR.A.TO"):
                with self.subTest(symbol=sym):
                    self.assertEqual(rules.income_date(_dist(sym)),
                                     "2026-01-15")


class TestCanadaJanuaryRocWarning(unittest.TestCase):
    """A2-0924 (M08): the January-ROC warning fires only when the export
    has NO record date (`and not _iso(_get(r, "record_date"))`)."""

    def _roc(self, **kw):
        r = {"action": "ADJUST", "type": "roc", "symbol": "ZZT.UN.TO",
             "date": "2026-01-15", "currency": "CAD", "net_amount": -12.0}
        r.update(kw)
        return r

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_no_warning_when_the_record_date_is_printed(self):
        rules = IncomeRules("canada")
        bare = rules.warnings([self._roc()], year=2026)
        self.assertTrue(any("with no record date" in w for w in bare), bare)
        dated = rules.warnings([self._roc(record_date="2026-01-05")],
                               year=2026)
        self.assertFalse(any("with no record date" in w for w in dated),
                         dated)


class TestUsRicWarningWindow(unittest.TestCase):
    """A2-0889, A2-0924 (M09), A2-1561, A2-1562: income_dating.warnings
    `d[:4] == str(prev) and d[5:7] in ("10", "11", "12")` and
    `pay[5:7] != "01"` — a January payment with an ex or record date in
    October-December of the PRIOR year is warned about; September, a
    February payment and a two-years-earlier date are not."""

    def _warned(self, **kw):
        return bool(IncomeRules("usa").warnings([_inc(**kw)], year=2026))

    @rule("US-INC-DATE-RIC")
    def test_october_to_december_ex_dates_warn(self):
        for ex in ("2025-10-01", "2025-10-31", "2025-11-14", "2025-12-15"):
            with self.subTest(ex=ex):
                self.assertTrue(self._warned(ex_date=ex))
        # The record date counts too.
        self.assertTrue(self._warned(record_date="2025-10-03"))

    @rule("US-INC-DATE-RIC")
    def test_outside_the_window_is_quiet(self):
        self.assertFalse(self._warned(ex_date="2025-09-30"))
        self.assertFalse(self._warned(record_date="2025-09-30"))
        # Two years before the pay date: not the prior year.
        self.assertFalse(self._warned(ex_date="2024-11-14"))
        # A February payment is not a January dividend.
        self.assertFalse(self._warned(date="2026-02-13",
                                      ex_date="2025-12-15"))
        self.assertFalse(self._warned())


# ====================================================== sum-income
class TestSumIncomeAccumulates(unittest.TestCase):
    """A2-1563: taxjson_sum_income.summarize_income adds every row of a
    (ticker, currency) — the 'div', 'tax', 'pil' and type-'other'
    accumulators (`+=`), not only the last one."""

    def test_two_rows_per_column_add_up(self):
        from taxjson.bin.taxjson_sum_income import summarize_income
        rows = [
            _inc(symbol="ZZD.US", date="2025-03-14", gross_amount=50.0,
                 net_amount=50.0),
            _inc(symbol="ZZD.US", date="2025-06-13", gross_amount=100.0,
                 net_amount=100.0),
            {"action": "TAX", "symbol": "ZZD.US", "date": "2025-03-14",
             "currency": "USD", "net_amount": 7.5},
            {"action": "TAX", "symbol": "ZZD.US", "date": "2025-06-13",
             "currency": "USD", "net_amount": 15.0},
            {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZD.US",
             "date": "2025-09-12", "currency": "USD", "gross_amount": 3.0,
             "net_amount": 3.0},
            {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZD.US",
             "date": "2025-12-12", "currency": "USD", "gross_amount": 4.0,
             "net_amount": 4.0},
            {"action": "OTHER", "type": "other", "symbol": "ZZO.US",
             "date": "2025-04-01", "currency": "USD", "net_amount": 10.0},
            {"action": "OTHER", "type": "other", "symbol": "ZZO.US",
             "date": "2025-05-01", "currency": "USD", "net_amount": 20.0},
        ]
        st = summarize_income(rows, 2025)["ticker_stats"]
        self.assertEqual(st["ZZD.US"]["USD"],
                         {"div": 150.0, "tax": 22.5, "pil": 7.0})
        self.assertEqual(st["ZZO.US"]["USD"]["div"], 30.0)


if __name__ == "__main__":
    unittest.main()
