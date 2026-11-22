"""Re-audit-2 partition fixes, lists partition-07 / partition-08 (area
partD), data half: IB Asia-Pacific trade dates, the generic importer's
crypto advice and futures prefixes, Coinbase stablecoin quotes in
property mode, Kraken ledger de-pegs, the country-ownership tables'
completeness check and the US explain trace's per-account FIFO.

Every fixture is SYNTHETIC: fake account ids (55500001 / U5550001),  # pii-ok
invented tickers (QZ*, XYZ, ZZS).

Formerly: test_fix_a2_partd_data
"""
import os
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# ------------------------------------------------------- IB clock
class TestIbAsiaClock(unittest.TestCase):
    """A2-1302: every Asia-Pacific fill takes its exchange's date.

    Formerly: test_fix_a2_partd_data.TestIbAsiaClock"""

    @rule(R.CA_DATE_SESSION)
    @rule(R.US_DATE_SESSION)
    def test_jpy_hkd_cnh_evening_fills_move_to_the_local_day(self):
        from taxjson.lib.brokerages.ib_extractor import _ib_market_trade_date
        for cur in ("JPY", "HKD", "CNH", "SGD", "AUD", "NZD"):
            d, _t, stamp = _ib_market_trade_date(
                "2026-03-03", "21:45:00", "Stocks", cur, "")
            self.assertEqual(d, "2026-03-04", cur)
            self.assertTrue(stamp, cur)


if __name__ == "__main__":
    unittest.main()
