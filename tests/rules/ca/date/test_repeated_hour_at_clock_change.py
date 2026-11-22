"""Fix round 7 (wave B), group "coinbase": the Coinbase readers and the
crypto send / price helpers (backlog TJ-0865, TJ-0870, TJ-0916,
TJ-0747, TJ-0652, TJ-0663, TJ-0667, TJ-0671, TJ-0744).

All rows, ids and amounts are synthetic.

Formerly: test_fix_1029_r7b_coinbase
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")


class TestRepeatedHourKeepsOrder(unittest.TestCase):
    """TJ-0747: a sale at 05:50 UTC and a rebuy at 06:10 UTC on the day
    Toronto's clocks fall back (01:50 EDT, then 01:10 EST) stay in that
    order: the sale realizes its gain as on any other day.

    Formerly: test_fix_1029_r7b_coinbase.TestRepeatedHourKeepsOrder"""

    def _gain(self, day):
        import dataclasses
        from taxjson.model.transaction import TaxTransaction as T
        from taxjson.lib.pipeline import GainsRequest, run_gains
        body = ("C1,2025-01-06 15:00:00 UTC,Buy,ZZE,10,CAD,5,50,50,0,"
                "Bought 10 ZZE\n"
                f"C2,{day} 05:50:00 UTC,Sell,ZZE,10,CAD,10,100,100,0,"
                "Sold 10 ZZE\n"
                f"C3,{day} 06:10:00 UTC,Buy,ZZE,10,CAD,10.01,100.1,100.1,"
                "0,Bought 10 ZZE\n")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.csv"
            p.write_text(_HDR + body)
            with contextlib.redirect_stderr(io.StringIO()):
                rows = CoinbaseBrokerage().parse_file(p)
        fields = {f.name for f in dataclasses.fields(T)}
        txs = [T(**{k: v for k, v in r.items() if k in fields})
               for r in rows]
        with contextlib.redirect_stderr(io.StringIO()):
            res = run_gains(txs, req=GainsRequest(
                country="canada", year=2025, taxable=True,
                phantom_hint=False))
        times = {r["id"]: r["time"] for r in rows}
        return res["summary"]["total_gain"], times

    @rule(R.CA_DATE_12)
    def test_sale_before_rebuy_across_the_clock_change(self):
        g_normal, _t = self._gain("2025-11-01")
        g_fold, times = self._gain("2025-11-02")
        self.assertAlmostEqual(g_normal, 50.0, places=2)
        self.assertAlmostEqual(g_fold, 50.0, places=2)
        self.assertLess(times["C2"], times["C3"])

    def test_times_around_the_repeated_hour(self):
        from datetime import datetime
        from taxjson.lib import timezones as TZ
        got = [TZ.utc_to_local(datetime(2025, 11, 2, h, m),
                               "America/Toronto").strftime("%H:%M:%S")
               for h, m in ((4, 59), (5, 0), (5, 50), (6, 0), (6, 10),
                            (6, 59), (7, 0))]
        self.assertEqual(got, ["00:59:00", "01:00:00", "01:25:00",
                               "01:30:00", "01:35:00", "01:59:30",
                               "02:00:00"])
        self.assertEqual(got, sorted(got))
        # Any other hour is its wall-clock time.
        self.assertEqual(TZ.utc_to_local(datetime(2025, 7, 1, 3, 0),
                                         "America/Toronto"),
                         datetime(2025, 6, 30, 23, 0))

    def test_the_no_tz_database_fallback_agrees(self):
        from datetime import datetime, timedelta
        from taxjson.lib import timezones as TZ
        for y in (2006, 2025):
            for h in range(3, 9):
                for m in (0, 1, 29, 30, 59):
                    d = (datetime(y, 10, 29) if y < 2007
                         else datetime(y, 11, 2)) + timedelta(hours=h,
                                                              minutes=m)
                    self.assertEqual(TZ._eastern_local(d),
                                     TZ.utc_to_local(d, "America/Toronto"),
                                     d)


if __name__ == "__main__":
    unittest.main()
