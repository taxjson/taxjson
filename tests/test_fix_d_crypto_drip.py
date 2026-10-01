"""Owner-decision fixes (crypto / DRIP area):

* R1-26: a send whose matched arrival is short (a Coinbase Send carries
  its network fee inside the sent quantity) books the shortfall as a
  disposition at fair value, in both countries — the way the Kraken
  withdrawal fee paid in a coin is booked.
* S060-24: in a Canada project PYUSD and GUSD are US-dollar cash on
  Kraken too (as on Coinbase, like USDC/USDT/DAI); a US project keeps
  every stablecoin as property.
* S065-12: a notional mismatch on a row whose parser does not declare a
  contract multiplier (a DRIP priced in another currency, a 10x typo)
  is echoed to the console as ATTENTION, not left in the .sum only.

Synthetic data only.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


# ----------------------------------------------------------- S060-24
_KR_TRADES_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
                "margin,misc,ledgers\n")
_KR_LEDGER_H = ('"txid","refid","time","type","subtype","aclass","asset",'
                '"wallet","amount","fee","balance"\n')


def _kraken(text: str, prefix: str, cash: bool):
    from taxjson.lib.brokerages.kraken import KrakenBrokerage

    class K(KrakenBrokerage):
        stablecoins_as_cash = cash
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{prefix}.csv"
        p.write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return K().parse_file(p)


_PYUSD_TRADES = _KR_TRADES_H + (
    "T1,O1,PYUSD/USD,2025-03-01 12:00:00.1,buy,limit,1.00,1000,0,1000,,,\n"
    "T2,O2,ETH/PYUSD,2025-03-02 12:00:00.1,buy,limit,2000,1000,0,0.5,,,\n"
    "T3,O3,GUSD/USD,2025-03-03 12:00:00.1,buy,limit,1.00,50,0,50,,,\n")
_PYUSD_REWARD = _KR_LEDGER_H + (
    '"L1","R1","2025-04-01 10:00:00","earn","reward","currency",'
    '"PYUSD","earn","10","0","10"\n')


class TestKrakenPyusdGusd(unittest.TestCase):

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    def test_canada_cash_us_property(self):
        ca = _kraken(_PYUSD_TRADES, "kr_trades", cash=True)
        # Canada: no PYUSD/GUSD position at all; ETH bought for dollars.
        self.assertEqual([t for t in ca if t["symbol"] in ("PYUSD", "GUSD")],
                         [])
        eth = [t for t in ca if t["symbol"] == "ETH"]
        self.assertEqual(len(eth), 1)
        self.assertAlmostEqual(eth[0]["net_amount"], 1000.0)
        self.assertEqual(eth[0]["currency"], "USD")
        # US: unchanged — PYUSD and GUSD are coins (property).
        us = _kraken(_PYUSD_TRADES, "kr_trades", cash=False)
        self.assertEqual(sorted({t["symbol"] for t in us
                                 if t["action"] == "BUYSELL"}),
                         ["ETH", "GUSD", "PYUSD"])

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    def test_reward_is_dollar_income_in_canada_coin_in_us(self):
        ca = _kraken(_PYUSD_REWARD, "kr_ledgers", cash=True)
        self.assertEqual([(t["action"], t["price"], t["net_amount"])
                          for t in ca], [("DIVIDEND", 1.0, 10.0)])
        us = _kraken(_PYUSD_REWARD, "kr_ledgers", cash=False)
        self.assertEqual(sorted((t["action"], t["symbol"]) for t in us),
                         [("BUYSELL", "PYUSD"), ("DIVIDEND", "PYUSD")])

    @rule("CA-CRYPTO-08")
    def test_kraken_pyusd_send_is_stablecoin_cash(self):
        from taxjson.lib import crypto_sends as cs
        for ex in ("kraken", "coinbase"):
            for sym in ("PYUSD", "GUSD", "USDC"):
                self.assertTrue(cs.is_cash_stablecoin(sym, ex), (sym, ex))
        self.assertFalse(cs.is_cash_stablecoin("ETH", "kraken"))



if __name__ == "__main__":
    unittest.main()
