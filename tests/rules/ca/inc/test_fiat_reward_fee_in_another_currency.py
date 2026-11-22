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


class TestKrakenFiatRewardOtherCurrencyFee(unittest.TestCase):
    """TJ-0378: a CAD reward of 10 (7.50 USD) with a 1 USD fee was
    booked as income of 6.5 CAD — the USD net under the reward's own
    currency. Its income stays in CAD, the fee converted at the rate the
    export's amountusd implies: 10 - 1 x 10 / 7.5 = 8.67 CAD.

    Formerly: test_fix_1029_parsers.TestKrakenFiatRewardOtherCurrencyFee"""

    @rule(R.CA_INC_STAKING)
    def test_cad_reward_usd_fee(self):
        rows = _kraken_reward("CAD", "7.5", "1", "1", "ZUSD", cash=True)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        self.assertEqual(div["currency"], "CAD")
        self.assertAlmostEqual(div["net_amount"], 10 - 10 / 7.5, places=6)
        self.assertAlmostEqual(div["gross_amount"], 10 - 10 / 7.5,
                               places=6)

    @rule(R.US_INC_STAKING)
    def test_eur_reward_coin_fee_us(self):
        # A fee in a coin: the same conversion, and the fee coins sold.
        rows = _kraken_reward("EUR", "11", "0.0005", "1", "XETH",
                              cash=False)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        self.assertEqual(div["currency"], "EUR")
        self.assertAlmostEqual(div["net_amount"], 10 * 10 / 11, places=6)
        sales = [r for r in rows if r["action"] == "BUYSELL"]
        self.assertEqual([(r["symbol"], r["quantity"]) for r in sales],
                         [("ETH", -0.0005)])

    @rule(R.CA_INC_STAKING)
    def test_usd_reward_unchanged(self):
        rows = _kraken_reward("ZUSD", "10", "0.0005", "1", "XETH",
                              cash=True)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        self.assertEqual(div["currency"], "USD")
        self.assertAlmostEqual(div["net_amount"], 9.0)

    def test_fiat_reward_with_no_usd_value_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _kraken_reward("CAD", "0", "1", "1", "ZUSD", cash=True)
        self.assertIn("amountusd 0", str(cm.exception))
        self.assertIn("kraken_ledgers.csv txid=", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
