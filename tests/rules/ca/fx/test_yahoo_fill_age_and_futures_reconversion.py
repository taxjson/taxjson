"""Fix round 7 (wave B), FX rates and `tjs audit`: a Yahoo rate is not
forward-filled twice (TJ-0843), a bad intraday quote does not poison
the rates file (TJ-0845), futures converted twice keep the composed
rate (TJ-0745); the audit needs no rate for a split that carries no
money (TJ-0765), traces a record-dated return of capital paid later
(TJ-0766) and finds a listing the run joined (TJ-0773).

Every fetcher is stubbed: no network. Synthetic books and rates only.

Formerly: test_fix_1029_r7b_ratesaudit"""
import tempfile
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from taxjson.bin import to_base_curr as T
from taxjson.lib import rate_cache
from taxjson.lib.exchange_rates import load_exchange_rates
from taxjson.bin.taxjson_convert_currency import get_rate_for_date, reset_fallback_tally
from tax_rules import rule
from taxjson.rules import ids as R


def _rates_file(td, rows, src="GBP", dst="USD"):
    p = Path(td) / "to_base.csv"
    p.write_text("".join(f"{r[0]} 12:00:00 {src} {dst} {r[1]} {r[2]}\n"
                         for r in rows), encoding="utf-8")
    return p


class TestYahooFillIsNotFilledAgain(unittest.TestCase):
    """TJ-0843: refresh_yahoo stores its forward-fill as calendar-date
    keys; resolve_rows used to fill again from the last fill (7 + 7 days)
    and the converter looked back 5 more: a 19-day-old rate.

    Formerly: test_fix_1029_r7b_ratesaudit.TestYahooFillIsNotFilledAgain"""

    OBS = {"2025-01-01": 1.25, "2025-01-22": 1.50}

    def setUp(self):
        reset_fallback_tally()
        self.addCleanup(reset_fallback_tally)

    def _fetch(self, _t, a, b):
        return {d: v for d, v in self.OBS.items() if a <= d <= b}

    def _rows(self, cache):
        return rate_cache.resolve_rows(cache, "GBP", "USD", "2025-01-01",
                              "2025-01-22", "2025-02-01")

    @rule(R.CA_FX_02)
    @rule(R.US_FX_02)
    def test_a_rate_is_never_older_than_fill_plus_lookback(self):
        cache = {}
        self.assertEqual(T.refresh_yahoo(
            cache, "GBPUSD", [("2025-01-01", "2025-01-22")], "2025-02-01",
            fetch=self._fetch), [])
        rows = self._rows(cache)
        dates = [d for d, _v, _s in rows]
        # 7 days of fill from January 1, nothing more, then January 22.
        self.assertEqual(dates[-2:], ["2025-01-08", "2025-01-22"])
        with tempfile.TemporaryDirectory() as td:
            hist = load_exchange_rates(_rates_file(td, rows), "USD")
        # January 13 is 12 days after the observation: still a rate.
        self.assertEqual(str(get_rate_for_date("GBP", "2025-01-13", hist,
                                               None)), "1.25")
        # January 14 and January 20 (19 days): no permissible rate.
        self.assertIsNone(get_rate_for_date("GBP", "2025-01-14", hist, None))
        self.assertIsNone(get_rate_for_date("GBP", "2025-01-20", hist, None))

    def test_a_second_fetch_does_not_fill_on_from_a_fill(self):
        # The range is asked in two parts: the second starts after the
        # first part's fill and must not seed a new fill from it.
        cache = {}
        T.refresh_yahoo(cache, "GBPUSD", [("2025-01-01", "2025-01-08")],
                        "2025-02-01", fetch=self._fetch)
        self.assertEqual(cache["GBPUSD-2025-01-08"], 1.25)
        T.refresh_yahoo(cache, "GBPUSD", [("2025-01-01", "2025-01-22")],
                        "2025-02-01", fetch=self._fetch)
        self.assertNotIn("GBPUSD-2025-01-09", cache)
        dates = [d for d, _v, _s in self._rows(cache)]
        self.assertNotIn("2025-01-09", dates)

    def test_a_weekend_after_a_genuine_observation_still_fills(self):
        # A cache holding observations only (weekdays) still carries
        # Friday's rate over the weekend, and a cached fill is used as is.
        cache = {"GBPUSD-2025-01-02": 1.2, "GBPUSD-2025-01-03": 1.3,
                 "GBPUSD-2025-01-06": 1.4,
                 "_coverage": {"yahoo:GBPUSD": [["2025-01-01",
                                                 "2025-01-10"]]}}
        got = {d: v for d, v, _s in rate_cache.resolve_rows(
            cache, "GBP", "USD", "2025-01-02", "2025-01-10", "2025-02-01")}
        self.assertEqual(got["2025-01-04"], "1.3")
        self.assertEqual(got["2025-01-05"], "1.3")
        self.assertEqual(got["2025-01-10"], "1.4")


def _fut(account, date, qty, net):
    from taxjson.model.transaction import TaxTransaction
    return TaxTransaction(
        action="BUYSELL", date=date, date_settle=date, time="10:00:00",
        symbol="F:SYNZ6.US", quantity=float(qty), currency="USD",
        price=abs(net / qty), net_amount=float(net),
        gross_amount=float(abs(net)), account=account)


class TestFuturesReconversionComposesTheRate(unittest.TestCase):
    """TJ-0745: converting settled futures books again (USD to EUR at
    0.8, then EUR to CAD at 1.5) kept the first rate in settle_native,
    so the Canadian cross-account pool priced a close at 0.8.

    Formerly: test_fix_1029_r7b_ratesaudit.TestFuturesReconversionComposesTheRate"""

    DATES = ("2025-01-06", "2025-02-03", "2025-06-02", "2026-02-02")

    def _convert(self, rows, src, to, rate):
        from decimal import Decimal
        from taxjson.bin.taxjson_convert_currency import process_transactions
        reset_fallback_tally()
        self.addCleanup(reset_fallback_tally)
        hist = {src: {d: Decimal(rate) for d in self.DATES}}
        return process_transactions(rows, to, hist, None, country="canada")

    def _gain_2025(self, chain):
        from taxjson.engine.by_country import get_tax_rules
        books = [[_fut("acct_a", "2025-01-06", 1, 100),
                  _fut("acct_a", "2025-06-02", -1, 300)],
                 [_fut("acct_b", "2025-02-03", 1, 200),
                  _fut("acct_b", "2026-02-02", -1, 300)]]
        rows = []
        for book in books:
            for src, to, rate in chain:
                book = self._convert(book, src, to, rate)
            rows += book
        res = get_tax_rules("canada").compute_gains(rows)
        return round(sum(g["gain"] for g in res["transactions"]
                         if g.get("qty") and "gain" in g
                         and str(g.get("date", ""))[:4] == "2025"), 6), rows

    @rule(R.CA_FX_04)
    def test_two_step_conversion_pools_like_a_direct_one(self):
        from decimal import Decimal
        from taxjson.model.evidence import parse_settle_native
        direct, _ = self._gain_2025([("USD", "CAD", "1.2")])
        via, rows = self._gain_2025([("USD", "EUR", "0.8"),
                                     ("EUR", "CAD", "1.5")])
        # (300 - 150) x 1.2: the pooled close at the USD->CAD rate.
        self.assertEqual(direct, 180.0)
        self.assertEqual(via, direct)
        legs = [parse_settle_native(r.settle_native) for r in rows]
        self.assertTrue(all(leg.rate == Decimal("1.2") for leg in legs))

    def test_a_conversion_into_its_own_currency_keeps_the_rate(self):
        from decimal import Decimal
        from taxjson.model.evidence import parse_settle_native
        rows = self._convert([_fut("acct_a", "2025-01-06", 1, 100),
                              _fut("acct_a", "2025-06-02", -1, 300)],
                             "USD", "CAD", "1.2")
        again = self._convert(rows, "USD", "CAD", "1.2")
        self.assertEqual([parse_settle_native(r.settle_native).rate
                          for r in again], [Decimal("1.2")] * 2)


if __name__ == "__main__":
    unittest.main()
