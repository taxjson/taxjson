"""Fix round 7 (wave B), group "coinbase": the Coinbase readers and the
crypto send / price helpers (backlog TJ-0865, TJ-0870, TJ-0916,
TJ-0747, TJ-0652, TJ-0663, TJ-0667, TJ-0671, TJ-0744).

All rows, ids and amounts are synthetic.

Formerly: test_fix_1029_r7b_coinbase
"""
import contextlib
import copy
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as V2
# The Coinbase parse builds its cash events (PLAN step 16 commit 8).
from _support.parser_cash import parsed_cash_events
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")
_BOOK = "coins/coinbase"


def _events(td, name, body):
    p = Path(td) / name
    p.write_text(_HDR + body)
    evs = parsed_cash_events("coinbase", p)
    for e in evs:
        e.update(label="coins", book=_BOOK, broker="coinbase", file=name)
    return p, evs


def _lines(*texts):
    out = []
    for i, t in enumerate(texts, 1):
        e = CL.parse_line(t, f"cash.tt:{i}")
        e.update(label="coins", book=_BOOK, file="cash.tt", book_problem="")
        out.append(e)
    return out


def _ledger(events, lines, rate, native=()):
    return V2.build(copy.deepcopy(list(native)),
                    {"events": copy.deepcopy(events),
                     "lines": copy.deepcopy(lines), "problems": [],
                     "stablecoins_as_cash": True},
                    "CAD", 2025, rate, country="canada")


class TestDistinctIdsAcrossFiles(unittest.TestCase):
    """TJ-0865: two exports holding distinct Coinbase IDs are two sets of
    executions, not one export's overlap; the same IDs in two files
    still count once.

    Formerly: test_fix_1029_r7b_coinbase.TestDistinctIdsAcrossFiles"""

    _BUY = (",2025-06-02 16:00:00 UTC,Buy,USDC,100,CAD,1,100,100,0,"
            "Bought 100 USDC for 100 CAD\n")
    _SELL = (",2025-06-04 16:00:00 UTC,Sell,USDC,100,CAD,3,300,300,0,"
             "Sold 100 USDC for 300 CAD\n")

    def _net(self, files):
        evs = []
        with tempfile.TemporaryDirectory() as td:
            for name, body in files.items():
                evs += _events(td, name, body)[1]
        kept = CE.dedup_across_files(evs)
        v = _ledger(kept, _lines("CASHOPEN 2025-01-01 USD 0 0",
                                 "CASHBAL 2025-12-31 USD 0"),
                    lambda c, d: 2.0)
        self.assertEqual(v["status"], "computed", v.get("problems"))
        return kept, v["net_gain"]

    @rule(R.CA_FX_07)
    def test_distinct_ids_in_two_files_are_two_round_trips(self):
        joined = {"all.csv": ("CB-A1" + self._BUY + "CB-B1" + self._SELL
                              + "CB-A2" + self._BUY + "CB-B2"
                              + self._SELL)}
        split = {"a.csv": "CB-A1" + self._BUY + "CB-B1" + self._SELL,
                 "b.csv": "CB-A2" + self._BUY + "CB-B2" + self._SELL}
        _k, g_joined = self._net(joined)
        kept, g_split = self._net(split)
        self.assertEqual(len(kept), 4)
        self.assertAlmostEqual(g_joined, 400.0, places=2)
        self.assertAlmostEqual(g_split, 400.0, places=2)

    def test_same_ids_in_two_files_count_once(self):
        same = {"a.csv": "CB-A1" + self._BUY + "CB-B1" + self._SELL,
                "b.csv": "CB-A1" + self._BUY + "CB-B1" + self._SELL}
        kept, g = self._net(same)
        self.assertEqual(len(kept), 2)
        self.assertAlmostEqual(g, 200.0, places=2)

    def test_an_id_less_copy_beside_an_id_export_counts_once(self):
        # An older export without IDs overlapping a newer one with them:
        # one event, as before.
        with tempfile.TemporaryDirectory() as td:
            evs = _events(td, "a.csv",
                          "CB-A1" + self._BUY + "CB-B1" + self._SELL)[1]
            p = Path(td) / "old.csv"
            p.write_text(
                "Timestamp,Transaction Type,Asset,Quantity Transacted,"
                "Price Currency,Price at Transaction,Subtotal,"
                "Total (inclusive of Fees and/or Spread),"
                "Fees and/or Spread,Notes\n"
                + self._BUY.lstrip(",") + self._SELL.lstrip(","))
            old = parsed_cash_events("coinbase", p)
            for e in old:
                e.update(label="coins", book=_BOOK, broker="coinbase",
                         file="old.csv")
        kept = CE.dedup_across_files(evs + old)
        self.assertEqual(len(kept), 2)

    def test_two_id_less_identical_rows_in_one_file_are_two(self):
        body = (self._BUY.lstrip(",") * 2)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "old.csv"
            p.write_text(
                "Timestamp,Transaction Type,Asset,Quantity Transacted,"
                "Price Currency,Price at Transaction,Subtotal,"
                "Total (inclusive of Fees and/or Spread),"
                "Fees and/or Spread,Notes\n" + body)
            evs = parsed_cash_events("coinbase", p)
        for e in evs:
            e.update(label="coins", book=_BOOK, file="old.csv")
        self.assertEqual(len(CE.dedup_across_files(evs)), 2)


class TestPriceCandleMatchesTheDay(unittest.TestCase):
    """TJ-0744: fill-crypto takes the close of the asked asset, in USD,
    on the asked UTC day, never the first positive close of whatever
    the provider answered.

    Formerly: test_fix_1029_r7b_coinbase.TestPriceCandleMatchesTheDay"""

    @staticmethod
    def _stamp(day):
        import calendar
        import time
        return calendar.timegm(time.strptime(day, "%Y-%m-%d"))

    def _price(self, meta, days, closes, with_stamps=True):
        import json
        from unittest import mock
        from taxjson.lib import crypto_prices
        res = {"meta": meta, "indicators": {"quote": [{"close": closes}]}}
        if with_stamps:
            res["timestamp"] = [self._stamp(d) for d in days]
        body = json.dumps({"chart": {"result": [res], "error": None}})

        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch.object(crypto_prices.urllib.request, "urlopen",
                                  lambda req, timeout=15: io.BytesIO(body.encode())), \
                contextlib.redirect_stderr(err):
            return crypto_prices.get_crypto_price("SYN", "2025-03-03"), err.getvalue()

    _USD = {"symbol": "SYN-USD", "currency": "USD"}

    def test_the_asked_days_close_not_the_first(self):
        p, err = self._price(self._USD, ["2025-03-02", "2025-03-03"],
                             [10, 20])
        self.assertEqual(p, 20.0)
        self.assertEqual(err, "")

    def test_wrong_day_only_is_unpriced(self):
        p, err = self._price(self._USD, ["2025-03-02"], [10])
        self.assertEqual(p, 0.0)
        self.assertIn("no usable close for SYN-USD on 2025-03-03", err)

    def test_other_currency_or_symbol_is_unpriced(self):
        p, err = self._price({"symbol": "SYN-EUR", "currency": "EUR"},
                             ["2025-03-03"], [20])
        self.assertEqual(p, 0.0)
        self.assertIn("SYN-EUR", err)
        p, err = self._price({"symbol": "SYN-USD", "currency": "EUR"},
                             ["2025-03-03"], [20])
        self.assertEqual(p, 0.0)
        self.assertIn("in EUR, not USD", err)

    def test_boolean_close_is_no_price(self):
        p, _err = self._price(self._USD, ["2025-03-03"], [True])
        self.assertEqual(p, 0.0)

    def test_a_single_candle_without_timestamps_still_reads(self):
        p, _err = self._price({}, [], [None, 20], with_stamps=False)
        self.assertEqual(p, 20.0)
        p, _err = self._price({}, [], [10, 20], with_stamps=False)
        self.assertEqual(p, 0.0)

    def test_a_refused_answer_is_not_cached(self):
        import json
        from unittest import mock
        from _support.crypto_fill_runner import run
        from taxjson.lib import crypto_prices
        row = {"action": "BUYSELL", "date": "2025-03-03", "symbol": "SYN",
               "quantity": 2.0, "price": 0.0, "net_amount": 0.0,
               "currency": "USD"}
        body = json.dumps({"chart": {"result": [{
            "meta": self._USD, "timestamp": [self._stamp("2025-03-02")],
            "indicators": {"quote": [{"close": [10]}]}}]}}).encode()
        real = crypto_prices.get_crypto_price
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch.object(crypto_prices.urllib.request, "urlopen",
                                  lambda req, timeout=15: io.BytesIO(body)):
            txs, cache, _err, _calls, _rc = run(td, [row], real)
        self.assertNotIn("SYN-2025-03-03", cache)
        self.assertEqual(txs[0]["net_amount"], 0.0)


if __name__ == "__main__":
    unittest.main()
