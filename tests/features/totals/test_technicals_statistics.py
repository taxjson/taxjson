"""`tjs technicals`' numbers (lib/technicals) against hand-computed values,
and its daily history (lib/price_chain daily_history: the cache, the day,
the units, the switches). Pure functions and injected fetchers: no network.

All series are made up."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.lib import price_chain as PC
from taxjson.lib import technicals as T
from taxjson.lib.technicals import Bar


def _days(n, start="2024-01-01"):
    """`n` weekdays from `start`, as ISO dates."""
    from datetime import date, timedelta
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(closes, start="2024-01-01"):
    return [Bar(day=d, close=c) for d, c in zip(_days(len(closes), start),
                                                closes)]


def _from_returns(returns, first=100.0, days=None):
    closes = [first]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    days = days or _days(len(closes))
    return [Bar(day=d, close=c) for d, c in zip(days, closes)]


def _wave(n, k=0.0):
    """Returns that vary: a made-up oscillation."""
    return [0.01 * math.sin(i / 3.0 + k) + 0.002 * math.cos(i / 7.0)
            for i in range(n)]


class TestMovingAverageDistance(unittest.TestCase):
    def test_hand_computed(self):
        closes = [float(i) for i in range(1, 21)]          # 1 .. 20
        # SMA20 = 10.5, last 20: (20 / 10.5 - 1) x 100
        self.assertAlmostEqual(T.sma_distance(closes, 20),
                               (20 / 10.5 - 1) * 100)
        # SMA5 = 18: last 20 is 11.11% above it
        self.assertAlmostEqual(T.sma_distance(closes, 5), (20 / 18 - 1) * 100)
        self.assertAlmostEqual(T.sma_distance([10.0] * 50, 50), 0.0)

    def test_short_history_is_none(self):
        self.assertIsNone(T.sma_distance([1.0] * 19, 20))
        self.assertIsNone(T.sma_distance([], 20))


class TestRsi(unittest.TestCase):
    def test_wilder_by_hand(self):
        # changes +1 -1 +2 -1 +2, n = 2: seeds 0.5 / 0.5; then
        # (+2) 1.25 / 0.25, (-1) 0.625 / 0.625, (+2) 1.3125 / 0.3125:
        # RS 4.2, RSI 100 - 100 / 5.2
        closes = [10.0, 11.0, 10.0, 12.0, 11.0, 13.0]
        self.assertAlmostEqual(T.rsi(closes, 2), 100 - 100 / 5.2)

    def test_all_up_all_down_flat(self):
        up = [float(i) for i in range(1, 31)]
        self.assertEqual(T.rsi(up), 100.0)
        self.assertEqual(T.rsi(list(reversed(up))), 0.0)
        self.assertIsNone(T.rsi([5.0] * 30))

    def test_needs_n_plus_one_closes(self):
        self.assertIsNone(T.rsi([float(i) for i in range(14)]))
        self.assertEqual(T.rsi([float(i) for i in range(15)]), 100.0)


class TestBetaAlpha(unittest.TestCase):
    def test_benchmark_against_itself(self):
        b = _from_returns(_wave(T.YEAR))
        beta, alpha = T.beta_alpha(b, b)
        self.assertAlmostEqual(beta, 1.0, places=12)
        self.assertAlmostEqual(alpha, 0.0, places=9)

    def test_doubled_returns_have_beta_two(self):
        rb = _wave(T.YEAR)
        b = _from_returns(rb)
        a = _from_returns([2 * r for r in rb], first=30.0)
        beta, alpha = T.beta_alpha(a, b)
        self.assertAlmostEqual(beta, 2.0, places=12)
        self.assertAlmostEqual(alpha, 0.0, places=9)

    def test_alpha_is_the_mean_excess_a_year(self):
        rb = _wave(T.YEAR)
        a = _from_returns([r + 0.001 for r in rb])
        beta, alpha = T.beta_alpha(a, _from_returns(rb))
        self.assertAlmostEqual(beta, 1.0, places=12)
        self.assertAlmostEqual(alpha, 0.001 * T.YEAR * 100, places=9)

    def test_short_history_is_none(self):
        rb = _wave(T.YEAR - 1)
        b = _from_returns(rb)
        self.assertEqual(T.beta_alpha(b, b), (None, None))

    def test_a_flat_benchmark_is_none(self):
        flat = _bars([10.0] * (T.YEAR + 1))
        self.assertEqual(T.beta_alpha(_from_returns(_wave(T.YEAR)), flat),
                         (None, None))

    def test_misaligned_dates_pair_on_the_common_ones(self):
        a = [Bar("2024-01-01", 100.0), Bar("2024-01-02", 110.0),
             Bar("2024-01-03", 121.0), Bar("2024-01-05", 108.9)]
        b = [Bar("2024-01-01", 50.0), Bar("2024-01-03", 55.0),
             Bar("2024-01-04", 60.0), Bar("2024-01-05", 66.0)]
        pairs = T.paired_returns(a, b)
        # 01-01 -> 01-03 (a's 01-02 and b's 01-04 skipped), 01-03 -> 01-05
        self.assertEqual(len(pairs), 2)
        self.assertAlmostEqual(pairs[0][0], 0.21)
        self.assertAlmostEqual(pairs[0][1], 0.10)
        self.assertAlmostEqual(pairs[1][0], -0.10)
        self.assertAlmostEqual(pairs[1][1], 0.20)

    def test_a_holiday_on_one_market_still_gives_a_year(self):
        # The holding misses one benchmark session: a year of pairs needs
        # one more close of each.
        rb = _wave(T.YEAR + 1)
        b = _from_returns(rb)
        a = [x for i, x in enumerate(b) if i != 100]
        beta, _alpha = T.beta_alpha(a, b)
        self.assertAlmostEqual(beta, 1.0, places=12)
        self.assertEqual(T.beta_alpha(a[1:], b), (None, None))


class TestYearStatistics(unittest.TestCase):
    def test_range_drawdown_and_returns_by_hand(self):
        closes = [100.0] * (T.YEAR - 3) + [120.0, 90.0, 130.0, 117.0]
        self.assertEqual(len(closes), T.YEAR + 1)
        hi, lo = T.range_distances(closes)
        self.assertAlmostEqual(hi, (117 / 130 - 1) * 100)
        self.assertAlmostEqual(lo, (117 / 90 - 1) * 100)
        self.assertAlmostEqual(T.max_drawdown(closes), -25.0)
        self.assertAlmostEqual(T.period_return(closes, T.YEAR), 17.0)
        self.assertAlmostEqual(T.period_return(closes, 3), (117 / 120 - 1) * 100)

    def test_volatility_by_hand(self):
        # Returns alternating +1% and -1%: mean 0 (an even count), sample
        # sd sqrt(252 x 0.0001 / 251)
        closes = [100.0]
        for i in range(T.YEAR):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
        sd = math.sqrt(sum((r - sum(T.daily_returns(closes)) / T.YEAR) ** 2
                           for r in T.daily_returns(closes)) / (T.YEAR - 1))
        self.assertAlmostEqual(T.volatility(closes),
                               sd * math.sqrt(T.YEAR) * 100)
        self.assertAlmostEqual(T.volatility(closes),
                               math.sqrt(T.YEAR * 0.0001 / (T.YEAR - 1))
                               * math.sqrt(T.YEAR) * 100, places=6)
        self.assertAlmostEqual(T.volatility([5.0] * (T.YEAR + 1)), 0.0)

    def test_a_year_needs_253_closes(self):
        closes = [100.0 + i for i in range(T.YEAR)]
        self.assertEqual(T.range_distances(closes), (None, None))
        self.assertIsNone(T.volatility(closes))
        self.assertIsNone(T.max_drawdown(closes))
        self.assertIsNone(T.period_return(closes, T.YEAR))
        self.assertIsNotNone(T.period_return(closes + [1.0], T.YEAR))


class TestRangeAndVolume(unittest.TestCase):
    def test_atr_by_hand(self):
        bars = [Bar("2024-01-01", 10.0, 10.5, 9.5),
                Bar("2024-01-02", 11.0, 12.0, 9.0),     # TR max(3, 2, 1) = 3
                Bar("2024-01-03", 11.0, 11.5, 10.5),    # TR max(1, .5, .5) = 1
                Bar("2024-01-04", 12.0, 13.0, 11.0)]    # TR max(2, 2, 0) = 2
        # n = 2: seed (3 + 1) / 2 = 2, then (2 x 1 + 2) / 2 = 2: 2 / 12
        self.assertAlmostEqual(T.atr_pct(bars, 2), 2 / 12 * 100)
        # n = 3: the mean of the three true ranges; n = 4 needs a fifth bar
        self.assertAlmostEqual(T.atr_pct(bars, 3), 2 / 12 * 100)
        self.assertIsNone(T.atr_pct(bars, 4))
        # The run of bars with a high and low is the trailing one.
        self.assertIsNone(T.atr_pct([Bar("2023-12-29", 10.0)] + bars[1:], 3))

    def test_atr_without_a_high_and_low_is_none(self):
        self.assertIsNone(T.atr_pct(_bars([10.0 + i for i in range(30)])))

    def test_average_volume(self):
        bars = [Bar(d, 10.0, volume=float(i))
                for i, d in enumerate(_days(25))]
        self.assertAlmostEqual(T.avg_volume(bars), sum(range(5, 25)) / 20)
        self.assertIsNone(T.avg_volume(bars[:19]))
        bars[-1] = Bar(bars[-1].day, 10.0)
        self.assertIsNone(T.avg_volume(bars))


class TestEveryStatisticNeedsItsWindow(unittest.TestCase):
    """Each statistic is None one close short of MIN_SESSIONS and a number
    at it: never a figure from a shorter window."""

    @staticmethod
    def _series(n):
        closes = [b.close for b in _from_returns(_wave(n - 1))]
        return [Bar(d, c, c * 1.01, c * 0.99, 1000.0 + i)
                for i, (d, c) in enumerate(zip(_days(n), closes))]

    def test_each_window(self):
        for key, need in T.MIN_SESSIONS.items():
            with self.subTest(statistic=key):
                short = self._series(need - 1)
                enough = self._series(need)
                self.assertIsNone(T.series_stats(short, short)[key])
                self.assertIsNotNone(T.series_stats(enough, enough)[key])
                if key not in ("beta", "alpha_pct"):
                    self.assertIn(key, T.short_of(short))
                    self.assertNotIn(key, T.short_of(enough))

    def test_no_benchmark_no_beta(self):
        st = T.series_stats(self._series(300))
        self.assertIsNone(st["beta"])
        self.assertIsNone(st["alpha_pct"])
        self.assertEqual(set(st), set(T.MIN_SESSIONS))


class TestLongerThanTheWindows(unittest.TestCase):
    """Series longer than their windows: each statistic reads its own
    window at the series' end, never the start or the whole series."""

    def test_beta_reads_the_last_252_returns(self):
        # 320 returns: the first 68 of the holding three times the
        # benchmark's, the last 252 equal to them: beta 1, alpha 0 only
        # if the window is the last 252.
        rb = _wave(320)
        ra = [3 * r for r in rb[:68]] + rb[68:]
        a, b = _from_returns(ra), _from_returns(rb)
        beta, alpha = T.beta_alpha(a, b)
        self.assertAlmostEqual(beta, 1.0, places=12)
        self.assertAlmostEqual(alpha, 0.0, places=9)
        # The first 252 pairs would give another beta.
        first, _alpha = T.beta_alpha(a[:253], b[:253])
        self.assertGreater(first, 1.1)

    def test_range_drawdown_and_volatility_read_the_last_253_closes(self):
        # 60 wild closes (a peak of 300 and a trough of 20), then 253 calm
        # ones between 99 and 101.
        wild = [100.0, 300.0, 20.0] + [100.0 + (i % 2) * 50 for i in range(57)]
        calm = [100.0 + math.sin(i / 5.0) for i in range(253)]
        closes = wild + calm
        win = closes[-253:]
        hi, lo = T.range_distances(closes)
        self.assertAlmostEqual(hi, (win[-1] / max(win) - 1) * 100, places=12)
        self.assertAlmostEqual(lo, (win[-1] / min(win) - 1) * 100, places=12)
        self.assertGreater(hi, -3.0)            # not 300's -66%
        dd = T.max_drawdown(closes)
        peak, worst = win[0], 0.0
        for c in win:
            peak = max(peak, c)
            worst = min(worst, (c / peak - 1) * 100)
        self.assertAlmostEqual(dd, worst, places=12)
        self.assertGreater(dd, -3.0)            # not the wild part's -93%
        rets = [b / a - 1 for a, b in zip(win, win[1:])]
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))
        self.assertAlmostEqual(T.volatility(closes),
                               sd * math.sqrt(252) * 100, places=10)
        self.assertLess(T.volatility(closes), 30.0)
        # The 1Y return is from the close 252 sessions before the last.
        self.assertAlmostEqual(T.period_return(closes, 252),
                               (closes[-1] / closes[-253] - 1) * 100,
                               places=12)


class TestBlankReasons(unittest.TestCase):
    def test_the_252_pair_boundary(self):
        """252 paired returns give a beta; 251 do not, and say so."""
        for n, has in ((253, True), (252, False)):
            with self.subTest(closes=n):
                a = _from_returns(_wave(n - 1, 0.3))
                b = _from_returns(_wave(n - 1))
                st = T.series_stats(a, b)
                self.assertEqual(len(T.paired_returns(a, b)), n - 1)
                self.assertEqual(st["beta"] is not None, has)
                why = T.blank_reasons(a, st, b)
                self.assertEqual("beta" in why, not has)
                if not has:
                    self.assertEqual(why["beta"], T.TOO_FEW_PAIRS)
                    self.assertEqual(why["alpha_pct"], T.TOO_FEW_PAIRS)

    def test_the_252_pair_boundary_of_the_reason(self):
        """With a benchmark that does not move, BETA is blank at any length:
        at 252 paired returns it is the benchmark's fault, below that the
        history's."""
        for n, why in ((253, T.FLAT_BENCHMARK), (252, T.TOO_FEW_PAIRS)):
            with self.subTest(closes=n):
                a = _from_returns(_wave(n - 1, 0.3))
                b = [Bar(day=x.day, close=80.0) for x in a]
                st = T.series_stats(a, b)
                self.assertIsNone(st["beta"])
                got = T.blank_reasons(a, st, b)
                self.assertEqual((got["beta"], got["alpha_pct"]), (why, why))

    def test_each_reason(self):
        flat = [Bar(day=d, close=50.0) for d in _days(260)]
        st = T.series_stats(flat, flat)
        why = T.blank_reasons(flat, st, flat)
        self.assertEqual(why, {"rsi14": T.FLAT, "beta": T.FLAT_BENCHMARK,
                               "alpha_pct": T.FLAT_BENCHMARK,
                               "atr14_pct": T.NO_HIGH_LOW,
                               "avg_volume_20": T.NO_VOLUME})
        short = _bars([10.0 + i for i in range(30)])
        why = T.blank_reasons(short, T.series_stats(short), None)
        self.assertEqual(why["sma50_pct"], T.TOO_SHORT)
        self.assertEqual(why["beta"], T.NO_BENCHMARK)
        self.assertNotIn("sma20_pct", why)
        # Too short wins over what the data would allow.
        self.assertEqual(why["atr14_pct"], T.NO_HIGH_LOW)
        tiny = _bars([10.0] * 5)
        self.assertEqual(T.blank_reasons(tiny, T.series_stats(tiny))["rsi14"],
                         T.TOO_SHORT)


def _rows(n, start="2024-01-01", close=10.0):
    return [[d, close + i * 0.1, close + i * 0.1 + 0.5,
             close + i * 0.1 - 0.5, 1000 + i]
            for i, d in enumerate(_days(n, start))]


class TestDailyHistory(unittest.TestCase):
    """lib/price_chain daily_history with an injected live tier."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cache = Path(self.td.name) / "work" / ".history_cache.json"
        self.cache.parent.mkdir()
        self.calls = []

    def tearDown(self):
        self.td.cleanup()

    def tier(self, answers):
        def _fetch(pairs, start, end):
            self.calls.append((dict(pairs), start, end))
            return {s: answers[s] for s in pairs if s in answers}
        return _fetch

    def test_fetched_written_back_and_reused_the_same_day(self):
        f = self.tier({"ZZQ.TO": (_rows(30), "CAD")})
        got, missing = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                        cache_path=self.cache, fetchers=[f],
                                        today="2025-01-10")
        self.assertEqual(missing, {})
        h = got["ZZQ.TO"]
        self.assertEqual((h.currency, h.fetched, len(h.bars)),
                         ("CAD", "2025-01-10", 30))
        self.assertEqual(self.calls[0][1:], ("2023-12-07", "2025-01-10"))
        entry = json.loads(self.cache.read_text())["ZZQ.TO|ZZQ.TO"]
        self.assertEqual(entry["adjusted"], PC.HISTORY_ADJUSTED)
        self.assertEqual(entry["fetched"], "2025-01-10")
        # The same day: the cache answers, the tier is not asked.
        got2, _ = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                   cache_path=self.cache, fetchers=[f],
                                   today="2025-01-10")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(got2["ZZQ.TO"].source, PC.HISTORY_CACHE_SOURCE)
        self.assertEqual([b.close for b in got2["ZZQ.TO"].bars],
                         [b.close for b in h.bars])

    def test_a_later_day_asks_again_and_falls_back_to_the_cache(self):
        PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                         fetchers=[self.tier({"ZZQ.TO": _rows(30)})],
                         today="2025-01-10")
        got, missing = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                        cache_path=self.cache,
                                        fetchers=[self.tier({})],
                                        today="2025-01-20")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(missing, {})
        self.assertEqual(got["ZZQ.TO"].source, PC.HISTORY_CACHE_SOURCE)
        self.assertEqual(got["ZZQ.TO"].fetched, "2025-01-10")

    def test_offline_reads_the_cache_only(self):
        PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                         fetchers=[self.tier({"ZZQ.TO": _rows(30)})],
                         today="2025-01-10")
        got, missing = PC.daily_history(
            {"ZZQ.TO": "ZZQ.TO", "ZZW.TO": "ZZW.TO"}, cache_path=self.cache,
            fetchers=[self.tier({"ZZW.TO": _rows(30)})], offline=True,
            today="2025-01-20")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(list(got), ["ZZQ.TO"])
        self.assertEqual(missing, {"ZZW.TO": PC.HISTORY_OFFLINE})

    def test_taxjson_offline_without_injected_tiers(self):
        with mock.patch.dict("os.environ", {"TAXJSON_OFFLINE": "1"}), \
                mock.patch.object(PC, "_yf_history_fetcher",
                                  side_effect=AssertionError("asked")):
            got, missing = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                            cache_path=self.cache,
                                            today="2025-01-20")
        self.assertEqual((got, missing), ({}, {"ZZQ.TO": PC.HISTORY_OFFLINE}))

    def test_no_fx_extra(self):
        with mock.patch.dict("os.environ", {"TAXJSON_OFFLINE": "0"}), \
                mock.patch.dict(sys.modules, {"yfinance": None}), \
                mock.patch.object(PC, "_yf_history_fetcher",
                                  side_effect=AssertionError("asked")):
            self.assertFalse(PC.history_tier_installed())
            got, missing = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                            cache_path=self.cache,
                                            today="2025-01-20")
        self.assertEqual((got, missing), ({}, {"ZZQ.TO": PC.HISTORY_NO_EXTRA}))

    def test_a_session_dated_today_and_bad_rows_are_left_out(self):
        rows = _rows(5, start="2025-01-06") + [
            ["2025-01-13", 99.0, 99.0, 99.0, 1],       # today
            ["2025-01-0x", 10.0, 10.0, 10.0, 1],       # not a date
            ["2025-01-02", float("nan"), 1, 1, 1],     # no close
            ["2025-01-03", True, 1, 1, 1],             # a bool
            ["2025-01-01", 10.0, "x", -1, -5]]         # no high, low, volume
        got, _ = PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                                  fetchers=[self.tier({"ZZQ.TO": rows})],
                                  today="2025-01-13")
        bars = got["ZZQ.TO"].bars
        self.assertEqual([b.day for b in bars],
                         ["2025-01-01"] + _days(5, "2025-01-06"))
        self.assertEqual((bars[0].high, bars[0].low, bars[0].volume),
                         (None, None, None))

    def test_pence_are_pounds(self):
        got, _ = PC.daily_history(
            {"ZZQ.L": "ZZQ.L"}, cache_path=self.cache,
            fetchers=[self.tier({"ZZQ.L": ([["2025-01-06", 125.0, 130.0,
                                              120.0, 10]], "GBp")})],
            today="2025-01-10")
        h = got["ZZQ.L"]
        self.assertEqual(h.currency, "GBP")
        self.assertEqual((h.bars[0].close, h.bars[0].high), (1.25, 1.30))

    def test_a_pence_market_without_a_unit_is_not_used(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            got, missing = PC.daily_history(
                {"ZZQ.L": "ZZQ.L"}, cache_path=self.cache,
                fetchers=[self.tier({"ZZQ.L": [["2025-01-06", 125.0]]})],
                today="2025-01-10")
        self.assertEqual(got, {})
        self.assertEqual(missing, {"ZZQ.L": PC.HISTORY_NONE})
        self.assertIn("did not say its unit", err.getvalue())

    def test_a_cache_entry_of_another_adjustment_or_spelling_is_a_miss(self):
        PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                         fetchers=[self.tier({"ZZQ.TO": _rows(30)})],
                         today="2025-01-10")
        doc = json.loads(self.cache.read_text())
        doc["ZZQ.TO|ZZQ.TO"]["adjusted"] = "splits"
        self.cache.write_text(json.dumps(doc))
        got, missing = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                        cache_path=self.cache, offline=True,
                                        today="2025-01-10")
        self.assertEqual(missing, {"ZZQ.TO": PC.HISTORY_OFFLINE})
        doc["ZZQ.TO|ZZQ.TO"]["adjusted"] = PC.HISTORY_ADJUSTED
        doc["ZZQ.TO|ZZQ.TO"]["fetched"] = "2099-01-01"
        self.cache.write_text(json.dumps(doc))
        got, missing = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                        cache_path=self.cache, offline=True,
                                        today="2025-01-10")
        self.assertEqual(got, {})

    def test_another_quote_spelling_is_not_reused(self):
        PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                         fetchers=[self.tier({"ZZQ.TO": _rows(30)})],
                         today="2025-01-10")
        # A QUOTE line now spells it ZZQ.NE: the entry under ZZQ.TO's
        # spelling is another listing's.
        got, missing = PC.daily_history({"ZZQ.TO": "ZZQ.NE"},
                                        cache_path=self.cache, offline=True,
                                        today="2025-01-10")
        self.assertEqual((got, missing), ({}, {"ZZQ.TO": PC.HISTORY_OFFLINE}))
        # Nor an entry under the right key that names another spelling.
        doc = json.loads(self.cache.read_text())
        doc["ZZQ.TO|ZZQ.NE"] = dict(doc["ZZQ.TO|ZZQ.TO"])
        self.cache.write_text(json.dumps(doc))
        got, _ = PC.daily_history({"ZZQ.TO": "ZZQ.NE"}, cache_path=self.cache,
                                  offline=True, today="2025-01-10")
        self.assertEqual(got, {})

    def test_a_fetch_date_not_in_iso_form_is_a_miss(self):
        PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                         fetchers=[self.tier({"ZZQ.TO": _rows(30)})],
                         today="2025-01-10")
        doc = json.loads(self.cache.read_text())
        for bad in ("20250110", "2025-W02-5", "2025-01-10T00:00:00"):
            with self.subTest(fetched=bad):
                doc["ZZQ.TO|ZZQ.TO"]["fetched"] = bad
                self.cache.write_text(json.dumps(doc))
                got, missing = PC.daily_history(
                    {"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache, offline=True,
                    today="2025-01-10")
                self.assertEqual(got, {})

    def test_why_a_cached_series_was_served(self):
        PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                         fetchers=[self.tier({"ZZQ.TO": _rows(30)})],
                         today="2025-01-10")
        cases = ((dict(fetchers=[self.tier({})], today="2025-01-10"),
                  PC.HISTORY_TODAY),
                 (dict(fetchers=[self.tier({})], today="2025-01-20"),
                  PC.HISTORY_NONE),
                 (dict(offline=True, today="2025-01-20"), PC.HISTORY_OFFLINE))
        for kw, reason in cases:
            with self.subTest(reason=reason):
                got, _ = PC.daily_history({"ZZQ.TO": "ZZQ.TO"},
                                          cache_path=self.cache, **kw)
                self.assertEqual(got["ZZQ.TO"].cache_reason, reason)
        got, _ = PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                                  fetchers=[self.tier({"ZZQ.TO": _rows(31)})],
                                  today="2025-01-21")
        self.assertIsNone(got["ZZQ.TO"].cache_reason)

    def test_a_tier_that_raises_is_one_line(self):
        PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                         fetchers=[self.tier({"ZZQ.TO": _rows(30)})],
                         today="2025-01-10")

        def _boom(pairs, start, end):
            raise ValueError("bad frame")
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            got, missing = PC.daily_history(
                {"ZZQ.TO": "ZZQ.TO", "ZZW.TO": "ZZW.TO"},
                cache_path=self.cache, fetchers=[_boom, self.tier(
                    {"ZZW.TO": _rows(30)})], today="2025-01-20")
        # The next tier is still asked; the cache serves the rest.
        self.assertEqual(got["ZZQ.TO"].cache_reason, PC.HISTORY_NONE)
        self.assertIsNone(got["ZZW.TO"].cache_reason)
        self.assertEqual(missing, {})
        lines = err.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn("daily-history tier failed (ValueError: bad frame)",
                      lines[0])

        def _interrupt(pairs, start, end):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            PC.daily_history({"ZZQ.TO": "ZZQ.TO"}, cache_path=self.cache,
                             fetchers=[_interrupt], today="2025-01-20")

    def test_the_live_bar_reader(self):
        # Yahoo's frame as yfinance returns it: a stand-in with the
        # columns and the index the reader reads.
        class _Col(list):
            @property
            def iloc(self):
                return self

        class _Frame:
            def __init__(self, cols, index):
                self.cols, self.index = cols, index

            def __contains__(self, k):
                return k in self.cols

            def __getitem__(self, k):
                return _Col(self.cols[k])

        frame = _Frame({"Close": [125.0, float("nan")], "High": [130.0, 1.0],
                        "Low": [120.0, 1.0], "Volume": [0, 7]},
                       ["2025-01-06 00:00:00-05:00", "2025-01-07 00:00:00"])
        self.assertEqual(PC._yf_bar_rows(frame, 100.0),
                         [["2025-01-06", 1.25, 1.30, 1.20, 0.0],
                          ["2025-01-07", None, 0.01, 0.01, 7.0]])
        self.assertEqual(PC._yf_bar_rows(_Frame({}, []), 1.0), [])
        self.assertEqual(PC._yf_bar_rows(None, 1.0), [])


if __name__ == "__main__":
    unittest.main()
