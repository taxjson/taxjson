"""Backlog fixes 2026-10-29, group `parsers`: the broker parsers.

TJ-0376 (one account's Webull downloads pasted into one file lost each
download's row order), TJ-0237 (two accounts' Webull downloads pasted
into one file were booked under the first account), TJ-0378 (a Kraken
fiat reward with its fee in another currency booked the USD net as its
own currency), TJ-0379 (a US Kraken stablecoin reward's separately paid
fee came off the coins' cost), TJ-0383 (a negative Coinbase reward was
booked as positive income), TJ-0391 (Coinbase Send/Receive rows lost
their IDs, and two exports' distinct sends folded into one), TJ-0382
(generic [actions] keys alike but for case silently replaced one
another).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_parsers
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.kraken import KrakenBrokerage
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_KR_LEDGER = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
              "balance,amountusd,feeusd,feecurrency\n")


def _kraken_reward(asset, amountusd, fee, feeusd, feecurrency, cash):
    """The Kraken parser's rows for one Earn reward of 10 `asset`."""
    body = _KR_LEDGER + (f"LSYN1,RSYN1,2025-01-15 15:00:00,earn,reward,"
                         f"currency,{asset},earn,10,{fee},10,{amountusd},"
                         f"{feeusd},{feecurrency}\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "kraken_ledgers.csv"
        p.write_text(body, encoding="utf-8")
        k = KrakenBrokerage()
        k.stablecoins_as_cash = cash
        with contextlib.redirect_stderr(io.StringIO()):
            return k.parse_file(p)


class TestKrakenStablecoinRewardOtherCurrencyFee(unittest.TestCase):
    """TJ-0379: in a US (property-mode) book a 10 USDC reward with a
    1 USD fee paid apart was acquired at 9 (0.90 a coin), so selling the
    10 coins at par later made a gain of 1. The coins cost their full
    par value; only the income is net of the fee.

    Formerly: test_fix_1029_parsers.TestKrakenStablecoinRewardOtherCurrencyFee"""

    @rule(R.US_INC_STAKING)
    @rule(R.US_CRYPTO_02)
    def test_usdc_reward_usd_fee_keeps_par_basis(self):
        rows = _kraken_reward("USDC", "10", "1", "1", "ZUSD", cash=False)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        (buy,) = [r for r in rows if r["action"] == "BUYSELL"]
        self.assertAlmostEqual(div["net_amount"], 9.0)
        self.assertAlmostEqual(buy["quantity"], 10.0)
        self.assertAlmostEqual(buy["net_amount"], 10.0)
        self.assertAlmostEqual(buy["price"], 1.0)

    @rule(R.US_INC_STAKING)
    def test_usdc_reward_same_coin_fee_still_net(self):
        # A commission withheld in the coins is netted from units,
        # income and basis alike (unchanged).
        rows = _kraken_reward("USDC", "10", "1", "1", "USDC", cash=False)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        (buy,) = [r for r in rows if r["action"] == "BUYSELL"]
        self.assertAlmostEqual(div["net_amount"], 9.0)
        self.assertAlmostEqual(buy["quantity"], 9.0)
        self.assertAlmostEqual(buy["net_amount"], 9.0)


if __name__ == "__main__":
    unittest.main()
