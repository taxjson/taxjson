"""Re-audit-2 fx-cash fixes (crypto-02 list). Synthetic data only.

A2-0235 / A2-0589 / A2-1012 / A2-1013 / A2-1015: a PYUSD/GUSD reward is
  US-dollar cash in a Canada book, like USDC (one shared stablecoin list).
A2-0079 / A2-0234: a Coinbase Advanced Trade on a crypto-quoted pair
  (ETH-BTC) moves no US dollars.
A2-0234 / A2-0576: a Kraken fee paid in a coin moves no US dollars.
A2-0244: same-settle rows are walked in trade-date order.
"""
import os
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_fx_cash import build_ledger
from tax_rules import rule, rule_absent


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

_RATES = {"2025-10-10": 1.40, "2025-10-13": 1.40, "2025-10-14": 1.40,
          "2026-01-10": 1.30, "2026-02-10": 1.40, "2026-03-10": 1.35}


def _rate_of(cur, d):
    return _RATES.get(d)


def _tx(action, date, cur, net, qty=0.0, **kw):
    return dict(action=action, date=date,
                date_settle=kw.pop("date_settle", date),
                time=kw.pop("time", "10:00:00"),
                symbol=kw.pop("symbol", "AAA.US"), quantity=qty,
                currency=cur, net_amount=net,
                account=kw.pop("account", "crypto"), **kw)


class TestStablecoinRewards(unittest.TestCase):

    @rule("CA-CRYPTO-02")
    def test_pyusd_and_gusd_rewards_enter_the_usd_pool(self):
        for coin in ("USDC", "PYUSD", "GUSD"):
            txs = [_tx("DIVIDEND", "2026-01-10", "USD", 1000.0,
                       symbol=coin, description="Staking Reward",
                       gross_amount=1000.0),
                   _tx("BUYSELL", "2026-02-10", "USD", 500.0, qty=1.0,
                       symbol="ETH")]
            doc = build_ledger(txs, "CAD", {}, 2026, rate_of=_rate_of)
            self.assertAlmostEqual(
                doc["per_currency"]["USD"]["acquired"], 1000.0, msg=coin)
            self.assertEqual(doc["overdrafts"], {}, msg=coin)
            self.assertAlmostEqual(doc["net_gain"], 50.0, msg=coin)

    @rule_absent("CA-CRYPTO-02", country="usa")
    @rule("US-FX-03")
    def test_us_book_has_no_usd_pool_for_a_stablecoin_reward(self):
        # US base is USD: a stablecoin reward is property, never foreign
        # cash in the §988 ledger.
        txs = [_tx("DIVIDEND", "2026-01-10", "USD", 1000.0, symbol="PYUSD",
                   description="Staking Reward", gross_amount=1000.0)]
        doc = build_ledger(txs, "USD", {}, 2026, rate_of=_rate_of,
                           country="usa")
        self.assertEqual(doc["pools"], {})


class TestCoinForCoinLegs(unittest.TestCase):

    _POOL = [_tx("BUYSELL", "2026-01-10", "USD", 60000.0, qty=-10,
                 symbol="SPY.US", account="margin")]

    @rule("CA-FX-07")
    def test_coinbase_crypto_pair_legs_move_no_dollars(self):
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        hdr = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
               "Price Currency,Price at Transaction,Subtotal,"
               "Total (inclusive of fees and/or spread),Fees and/or Spread,"
               "Notes\n")
        row = ("x1,2026-02-10 12:00:00 UTC,Advanced Trade Sell,ETH,-30,USD,"
               "$3300.00,-$99000.00,-$99000.00,$0.00,"
               "Sold 30 ETH for 1 BTC on ETH-BTC at 0.0333 BTC/ETH\n")
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "cb.csv"
            f.write_text(hdr + row)
            with contextlib.redirect_stderr(io.StringIO()):
                legs = CoinbaseBrokerage().parse_file(f)
        self.assertEqual(len(legs), 2)
        doc = build_ledger(self._POOL + legs, "CAD", {}, 2026,
                           rate_of=_rate_of)
        self.assertAlmostEqual(doc["net_gain"], 0.0)
        self.assertAlmostEqual(doc["pools"]["USD"]["units"], 60000.0)

    @rule("CA-FX-07")
    def test_kraken_coin_fee_moves_no_dollars(self):
        fee = _tx("BUYSELL", "2026-02-10", "USD", 75.0, qty=-0.5,
                  symbol="SOL",
                  description="Kraken withdrawal fee paid in SOL "
                              "(disposed at FMV)")
        doc = build_ledger([fee], "CAD", {}, 2026, rate_of=_rate_of)
        self.assertEqual(doc["pools"], {})
        self.assertEqual(doc["per_currency"], {})


class TestSameSettleOrder(unittest.TestCase):

    @rule("CA-FX-07")
    def test_earlier_trade_date_walks_first(self):
        # Columbus Day: a Friday sale and the Monday buy settle the same
        # Tuesday; the Monday buy has the EARLIER clock time.
        sale = _tx("BUYSELL", "2025-10-10", "USD", 1000.0, qty=-10,
                   date_settle="2025-10-14", time="15:00:00")
        buy = _tx("BUYSELL", "2025-10-13", "USD", 1000.0, qty=10,
                  date_settle="2025-10-14", time="09:45:00")
        doc = build_ledger([buy, sale], "CAD", {}, 2025, rate_of=_rate_of)
        self.assertEqual(doc["overdrafts"], {})
        self.assertEqual(doc["pools"], {})


if __name__ == "__main__":
    unittest.main()
