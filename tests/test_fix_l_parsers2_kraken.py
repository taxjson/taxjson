"""Low-round parsers2 fixes: Kraken.

R1-306 / S061-05 (the trades<->ledger join guard, every leg in both
directions), S061-03 (empty required field; each half of the foreign-fee
reward guard), S060-23 (instant SELL fiat fee), S061-00 (ambiguous
legacy pair refused), S061-04 (the .tt advice says to remove the row),
S061-15 (a fee on a wallet move is UNBOOKED), S061-16 (a fee in another
coin is disposed of), S061-17 (no coin fee in the money `fee` field),
S060-22 (the ledger's own USD values), S061-22 (fiat-only ignored note).
Synthetic data only.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule

from taxjson.lib.brokerages.kraken import KrakenBrokerage, _split_pair

_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")
_KL_H2 = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance,amountusd,feeusd,feecurrency\n")


def _parse(files, which, cash=True):
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        k = KrakenBrokerage()
        k.stablecoins_as_cash = cash
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = k.parse_file(Path(td) / which)
        return txs, buf.getvalue()


# A buy of 10 SOL for 2000 CAD, fee 5 CAD in the quote currency.
_TRADE = _KT_H + ("T1,O1,SOL/CAD,2025-06-02 16:00:00.1,buy,limit,200,2000,"
                  "5,10,,,\n")
_LEDGER = _KL_H + (
    "L1,T1,2025-06-02 16:00:00,trade,tradespot,currency,SOL,spot,10,0,10\n"
    "L2,T1,2025-06-02 16:00:00,trade,tradespot,currency,ZCAD,spot,-2000,"
    "5,0\n")


class TestJoinGuardBothWays(unittest.TestCase):
    """R1-306 / S061-05: each leg of the agreement check refuses a
    disagreement in either direction."""

    def _refused(self, ledger, needle):
        with self.assertRaises(ValueError) as cm:
            _parse({"kr_trades.csv": _TRADE, "kr_ledgers.csv": ledger},
                   "kr_trades.csv")
        self.assertIn(needle, str(cm.exception))

    def test_control_accepted(self):
        txs, _ = _parse({"kr_trades.csv": _TRADE, "kr_ledgers.csv": _LEDGER},
                        "kr_trades.csv")
        self.assertAlmostEqual(txs[0]["net_amount"], 2005.0)

    def test_ledger_coins_below_vol(self):
        self._refused(_LEDGER.replace("SOL,spot,10,", "SOL,spot,9,"),
                      "!= trades vol")

    def test_ledger_cost_below_and_above_trade_cost(self):
        for amt in ("-1900", "-2100"):
            with self.subTest(amt=amt):
                self._refused(_LEDGER.replace("ZCAD,spot,-2000,",
                                              f"ZCAD,spot,{amt},"),
                              "!= trades cost")

    def test_single_leg_reversed(self):
        self._refused(_LEDGER.replace("ZCAD,spot,-2000,", "ZCAD,spot,2000,"),
                      "direction disagrees")
        self._refused(_LEDGER.replace("SOL,spot,10,", "SOL,spot,-10,"),
                      "direction disagrees")

    def test_quote_fee_above_below_and_zero(self):
        for fee in ("6", "4", "0"):
            with self.subTest(fee=fee):
                self._refused(_LEDGER.replace("ZCAD,spot,-2000,5,",
                                              f"ZCAD,spot,-2000,{fee},"),
                              "but the ledger charged")


class TestRequiredFieldGuards(unittest.TestCase):
    """S061-03."""

    def test_empty_required_trades_field_refused(self):
        for col, row in (
                ("cost", "T1,O1,SOL/CAD,2025-06-02 16:00:00.1,buy,limit,200,"
                         ",5,10,,,\n"),
                ("vol", "T1,O1,SOL/CAD,2025-06-02 16:00:00.1,buy,limit,200,"
                        "2000,5,,,,\n")):
            with self.subTest(col=col):
                with self.assertRaises(ValueError) as cm:
                    _parse({"kr_trades.csv": _KT_H + row}, "kr_trades.csv")
                self.assertIn(f"empty '{col}'", str(cm.exception))

    def test_foreign_fee_reward_missing_either_usd_value(self):
        base = ("L1,R1,2026-03-01 00:00:00,earn,reward,currency,HYPE,"
                "earn,1.5,0.1,1.5,{usd},{fusd},DOT\n")
        for usd, fusd in (("", "0.50"), ("30.00", "")):
            with self.subTest(usd=usd, fusd=fusd):
                with self.assertRaises(ValueError) as cm:
                    _parse({"kr_ledgers.csv": _KL_H2 + base.format(
                        usd=usd, fusd=fusd)}, "kr_ledgers.csv")
                self.assertIn("no amountusd/feeusd", str(cm.exception))


class TestInstantSellFiatFee(unittest.TestCase):
    """S060-23: an instant SELL's fiat-leg fee comes off the proceeds."""

    def test_sell_net_is_quote_minus_fee(self):
        txs, _ = _parse({"kr_ledgers.csv": _KL_H + (
            "L1,R1,2025-01-15 10:05:00,spend,,currency,XETH,,-0.05,0,0\n"
            "L2,R1,2025-01-15 10:05:01,receive,,currency,ZUSD,,100,1.50,0\n")},
            "kr_ledgers.csv")
        t = [x for x in txs if x["description"] == "Instant Trade"][0]
        self.assertAlmostEqual(t["net_amount"], 98.50)
        self.assertAlmostEqual(t["quantity"], -0.05)


class TestLegacyPairs(unittest.TestCase):
    """S061-00."""

    def test_stablecoin_suffix_pair_refused(self):
        for pair in ("ETHPYUSD", "XRPRLUSD", "BTCFDUSD", "SOLGUSD"):
            with self.subTest(pair=pair):
                with self.assertRaises(ValueError) as cm:
                    _split_pair(pair)
                self.assertIn("ambiguous", str(cm.exception))

    def test_ordinary_legacy_pairs_still_split(self):
        self.assertEqual(_split_pair("ADAUSD"), ("ADA", "USD"))
        self.assertEqual(_split_pair("XBTUSD"), ("XBT", "USD"))
        self.assertEqual(_split_pair("SOLUSDT"), ("SOL", "USDT"))
        self.assertEqual(_split_pair("ETH/PYUSD"), ("ETH", "PYUSD"))


class TestRefusalAdvice(unittest.TestCase):
    """S061-04: entering the .tt alone never unblocked the run."""

    def test_message_says_to_remove_the_row(self):
        with self.assertRaises(ValueError) as cm:
            _parse({"kr_ledgers.csv": _KL_H2 + (
                "L1,R1,2026-03-01 00:00:00,spend,,currency,XETH,,-0.05,1,0,"
                "100,1,ZUSD\n"
                "L2,R1,2026-03-01 00:00:01,receive,,currency,SOL,,1,0,1,"
                "100,,\n")}, "kr_ledgers.csv")
        self.assertIn("remove the row from the export", str(cm.exception))


class TestLedgerFees(unittest.TestCase):

    def test_wallet_move_fee_is_unbooked(self):
        # S061-15
        _, err = _parse({"kr_ledgers.csv": _KL_H + (
            "L1,R1,2026-03-01 00:00:00,earn,allocation,currency,DOT,spot,"
            "-50,0.25,0\n"
            "L2,R1,2026-03-01 00:00:00,earn,allocation,currency,DOT,earn,"
            "50,0,50\n")}, "kr_ledgers.csv")
        self.assertIn("warning: UNBOOKED", err)
        self.assertIn("earn/allocation fee", err)

    @rule("CA-CRYPTO-03")
    def test_fee_in_another_coin_is_disposed_of(self):
        # S061-16: 2000 ARB withdrawn, the 0.01 fee charged in ETH.
        txs, _ = _parse({"kr_ledgers.csv": _KL_H2 + (
            "L1,F1,2026-03-01 00:00:00,withdrawal,,currency,ARB,spot,"
            "-2000,0.01,0,,30.00,XETH\n")}, "kr_ledgers.csv")
        fee = [t for t in txs if t["action"] == "BUYSELL"]
        self.assertEqual(len(fee), 1)
        self.assertEqual(fee[0]["symbol"], "ETH")
        self.assertAlmostEqual(fee[0]["quantity"], -0.01)
        # S060-22: valued from the ledger's feeusd, not a daily close.
        self.assertAlmostEqual(fee[0]["net_amount"], 30.0)
        tr = [t for t in txs if t["action"] == "TRANSFER"][0]
        self.assertEqual(tr["fee"], 0.0)
        self.assertIn("(fee 0.01 ETH)", tr["description"])

    @rule("US-CRYPTO-03")
    def test_fee_in_another_coin_is_disposed_of_us(self):
        txs, _ = _parse({"kr_ledgers.csv": _KL_H2 + (
            "L1,F1,2026-03-01 00:00:00,withdrawal,,currency,ARB,spot,"
            "-2000,0.01,0,,30.00,XETH\n")}, "kr_ledgers.csv", cash=False)
        fee = [t for t in txs if t["action"] == "BUYSELL"]
        self.assertEqual([t["symbol"] for t in fee], ["ETH"])

    def test_same_coin_fee_not_in_the_money_fee_field(self):
        # S061-17: 25 XRP of fee was labelled 25 USD.
        txs, _ = _parse({"kr_ledgers.csv": _KL_H + (
            "L1,F1,2025-03-03 00:00:00,withdrawal,,currency,XXRP,spot,"
            "-500,25,0\n")}, "kr_ledgers.csv")
        tr = [t for t in txs if t["action"] == "TRANSFER"][0]
        self.assertEqual(tr["fee"], 0.0)
        self.assertIn("(fee 25 XRP)", tr["description"])
        fee = [t for t in txs if t["action"] == "BUYSELL"][0]
        self.assertAlmostEqual(fee["quantity"], -25.0)

    def test_crypto_sends_still_reads_the_coin_fee(self):
        import json
        from taxjson.lib.crypto_sends import load_transfer_rows
        txs, _ = _parse({"kr_ledgers.csv": _KL_H + (
            "L1,F1,2025-03-03 00:00:00,withdrawal,,currency,XXRP,spot,"
            "-500,25,0\n")}, "kr_ledgers.csv")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "c_kraken_transfers.json").write_text(json.dumps({
                "metadata": {"kind": "transfer_sidecar",
                             "brokerage": "kraken"},
                "transactions": [t for t in txs
                                 if t["action"] == "TRANSFER"]}))
            rows = load_transfer_rows(Path(d), ["c"])
        self.assertEqual(rows[0]["fee"], 25.0)
        self.assertEqual(rows[0]["kind"], "withdrawal")

    def test_ignored_note_has_no_cost_basis_advice(self):
        # S061-22: only fiat funding reaches the note.
        _, err = _parse({"kr_ledgers.csv": _KL_H + (
            "L1,F1,2025-03-03 00:00:00,deposit,,currency,ZCAD,spot,"
            "500,0,500\n")}, "kr_ledgers.csv")
        self.assertIn("deposit x1", err)
        self.assertNotIn("cost basis must come from", err)


class TestTradesSwapValue(unittest.TestCase):
    """S060-22: a trades-CSV crypto/crypto fill joined to a ledger that
    carries amountusd is valued once, from it."""

    def test_both_legs_carry_the_ledger_value(self):
        trades = _KT_H + ("T1,O1,SOL/ETH,2026-06-02 16:00:00.1,buy,limit,"
                          "0.05,0.5,0,10,,,\n")
        ledger = _KL_H2 + (
            "L1,T1,2026-06-02 16:00:00,trade,tradespot,currency,SOL,spot,"
            "10,0,10,1600,0,SOL\n"
            "L2,T1,2026-06-02 16:00:00,trade,tradespot,currency,XETH,spot,"
            "-0.5,0,0,1600,0,XETH\n")
        txs, _ = _parse({"kr_trades.csv": trades, "kr_ledgers.csv": ledger},
                        "kr_trades.csv")
        self.assertEqual(len(txs), 2)
        self.assertEqual({t["net_amount"] for t in txs}, {1600.0})


if __name__ == "__main__":
    unittest.main()
