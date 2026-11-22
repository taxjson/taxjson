"""Re-audit-2 partition fixes, lists partition-07 / partition-08 (area
partD), data half: IB Asia-Pacific trade dates, the generic importer's
crypto advice and futures prefixes, Coinbase stablecoin quotes in
property mode, Kraken ledger de-pegs, the country-ownership tables'
completeness check and the US explain trace's per-account FIFO.

Every fixture is SYNTHETIC: fake account ids (55500001 / U5550001),  # pii-ok
invented tickers (QZ*, XYZ, ZZS).

Formerly: test_fix_a2_partd_data
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# --------------------------------------------------------------- Coinbase
_CB_H = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
         "Price Currency,Price at Transaction,Subtotal,Total (inclusive of "
         "fees and/or spread),Fees and/or Spread,Notes,ID\n")


def _cb(rows, cash):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cb.csv"
        p.write_text(_CB_H + rows, encoding="utf-8")
        par = CoinbaseBrokerage()
        par.stablecoins_as_cash = cash
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return par.parse_file(p), err.getvalue()


class TestCoinbaseStablecoinQuote(unittest.TestCase):
    """A2-0730: in property mode (a US project) a fill priced in a
    stablecoin with no 'on BASE-QUOTE' pair is not folded to USD.

    Formerly: test_fix_a2_partd_data.TestCoinbaseStablecoinQuote"""

    @rule(R.US_CRYPTO_02)
    @rule_absent(R.US_CRYPTO_02, country="canada")
    @rule(R.CA_CRYPTO_02)
    def test_property_mode_refuses_rather_than_drop_the_leg(self):
        row = ("2025-03-03T15:00:00Z,Advanced Trade Buy,ETH,0.5,PYUSD,2000,"
               "1000,1000,0,Bought 0.5 ETH for 1000 PYUSD,cbid-1\n")
        with self.assertRaises(ValueError) as cm:
            _cb(row, cash=False)
        self.assertIn("PYUSD", str(cm.exception))
        # Canada's cash model: PYUSD is US-dollar cash, one ETH buy.
        txs, _ = _cb(row, cash=True)
        self.assertEqual([(t["symbol"], t["quantity"], t["currency"])
                          for t in txs], [("ETH", 0.5, "USD")])

    @rule(R.CA_CRYPTO_02)
    @rule(R.US_CRYPTO_02)
    def test_pyusd_and_gusd_cash_in_canada_property_in_the_us(self):
        """A2-1490 / A2-1507: on Coinbase as on Kraken, PYUSD and GUSD
        are US-dollar cash in the Canadian model (buying one books
        nothing; a coin bought with one is a USD purchase) and property
        in the US model (buying one is a purchase; spending one a sale)."""
        for coin in ("PYUSD", "GUSD"):
            buy = (f"2025-03-03T15:00:00Z,Buy,{coin},100,USD,1.00,100,100,"
                   f"0,Bought 100 {coin} for 100 USD,cbid-3\n")
            eth = (f"2025-03-03T15:00:00Z,Advanced Trade Buy,ETH,0.5,{coin},"
                   f"2000,1000,1000,0,Bought 0.5 ETH for 1000 {coin} on "
                   f"ETH-{coin},cbid-4\n")
            with self.subTest(coin=coin, model="cash"):
                self.assertEqual(_cb(buy, cash=True)[0], [])
                self.assertEqual(
                    [(t["symbol"], t["quantity"], t["currency"])
                     for t in _cb(eth, cash=True)[0]],
                    [("ETH", 0.5, "USD")])
            with self.subTest(coin=coin, model="property"):
                self.assertEqual(
                    [(t["symbol"], t["quantity"])
                     for t in _cb(buy, cash=False)[0]], [(coin, 100.0)])
                self.assertEqual(
                    sorted((t["symbol"], t["quantity"])
                           for t in _cb(eth, cash=False)[0]),
                    [("ETH", 0.5), (coin, -1000.0)])

    @rule(R.CA_DATE_07)
    @rule(R.CA_DATE_12)
    @rule(R.US_DATE_07)
    @rule(R.US_DATE_11)
    def test_fills_are_dated_locally_and_settle_on_the_trade_date(self):
        """Coinbase stamps UTC: a fill is dated in local time
        (America/Toronto here) and settles on that date — a 03:30 UTC
        New Year's Day sale is a Dec-31 disposition."""
        rows = ("2025-12-31T23:30:00Z,Buy,BTC,0.01,USD,100000,1000,1010,10,"
                "Bought 0.01 BTC for 1010 USD,cbid-5\n"
                "2026-01-01T03:30:00Z,Sell,BTC,0.005,USD,100000,500,495,5,"
                "Sold 0.005 BTC for 495 USD,cbid-6\n"
                "2026-01-01T04:30:00Z,Advanced Trade Sell,BTC,0.001,USD,"
                "100000,100,99,1,Sold 0.001 BTC for 99 USD on BTC-USD,"
                "cbid-7\n")
        from unittest import mock
        with mock.patch.dict(os.environ,
                             {"TAXJSON_LOCAL_TZ": "America/Toronto"}):
            txs, _ = _cb(rows, cash=True)
        self.assertEqual(
            [(t["quantity"], t["date"], t["time"], t["date_settle"])
             for t in txs],
            [(0.01, "2025-12-31", "18:30:00", "2025-12-31"),
             (-0.005, "2025-12-31", "22:30:00", "2025-12-31"),
             (-0.001, "2025-12-31", "23:30:00", "2025-12-31")])

    def test_property_mode_with_pair_books_both_legs(self):
        row = ("2025-03-03T15:00:00Z,Advanced Trade Buy,ETH,0.5,PYUSD,2000,"
               "1000,1000,0,Bought 0.5 ETH for 1000 PYUSD on ETH-PYUSD,"
               "cbid-2\n")
        txs, _ = _cb(row, cash=False)
        self.assertEqual(sorted((t["symbol"], t["quantity"]) for t in txs),
                         [("ETH", 0.5), ("PYUSD", -1000.0)])


# ----------------------------------------------------------------- Kraken
_KR_H = ('"txid","refid","time","type","subtype","aclass","asset","wallet",'
         '"amount","fee","balance"\n')


class TestKrakenLedgerDepeg(unittest.TestCase):
    """A2-1284: a ledger instant trade USDC -> USD off the peg warns.

    Formerly: test_fix_a2_partd_data.TestKrakenLedgerDepeg"""

    @rule(R.CA_CRYPTO_02)
    def test_ledger_instant_trade_off_peg_warns(self):
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        rows = ('"LA1","RX1","2025-03-03 15:00:00","spend","","currency",'
                '"USDC","spot / main",-1000,0,0\n'
                '"LA2","RX1","2025-03-03 15:00:00","receive","","currency",'
                '"ZUSD","spot / main",900,0,900\n')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kraken_ledgers.csv"
            p.write_text(_KR_H + rows, encoding="utf-8")
            par = KrakenBrokerage()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                txs = par.parse_file(p)
        self.assertEqual(txs, [])
        self.assertIn("USDC traded at 0.9000 USD", err.getvalue())


if __name__ == "__main__":
    unittest.main()
