"""Re-audit-2 fixes: the Kraken parser (fix list crypto-03).

A2-0080 (price x vol vs cost), A2-0236 (ETH2/staked-code fold stated in
tax-logic), A2-0238/0251/0579/0580 (every fiat currency is cash),
A2-0245 (unknown trades type is UNBOOKED), A2-0246 (duplicate columns),
A2-0247/0248/1022 (line break in a cell), A2-0577 (KFEE fee credits),
A2-0578/1002 (fiat credits and zero-amount fee rows are not own cash),
A2-0581 (multi-coin dust sweep pairs for one value), A2-0582/1018 (a
fee in another coin is disposed of), A2-0583 (all-non-event file),
A2-1004/1020 (US: all five stablecoins at par), A2-1017 (earn
migration), A2-1019 (instant-trade leg signs). Synthetic data only.
"""
import io
import re
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule, rule_absent

from taxjson.lib.brokerages.kraken import KrakenBrokerage

_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")
_KL_H2 = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance,amountusd,feeusd,feecurrency\n")


def _parse(files, which, cash=True, want_extractor=False):
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        k = KrakenBrokerage()
        k.stablecoins_as_cash = cash
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = k.parse_file(Path(td) / which)
        if want_extractor:
            return txs, buf.getvalue(), k
        return txs, buf.getvalue()


def _ledger(rows, header=_KL_H):
    return header + "".join(r + "\n" for r in rows)


def _bs(txs):
    return [(t["symbol"], round(t["quantity"], 10)) for t in txs
            if t["action"] == "BUYSELL"]


class TestEveryFiatIsCash(unittest.TestCase):
    """A2-0238 / A2-0251 / A2-0579 / A2-0580: AUD, JPY, CHF (any fiat
    currency, the Coinbase set) are cash, not coins."""

    @rule("CA-CRYPTO-01")
    def test_trades_pair_quoted_in_aud_jpy_chf_is_a_fiat_purchase(self):
        for q in ("AUD", "JPY", "CHF"):
            with self.subTest(q=q):
                t = _KT_H + (f"T1,O1,XBT/{q},2025-06-02 16:00:00,buy,limit,"
                             f"90000,9000,10,0.1,,,\n")
                txs, _ = _parse({"kr_trades.csv": t}, "kr_trades.csv")
                self.assertEqual(len(txs), 1)
                self.assertEqual(txs[0]["symbol"], "BTC")
                self.assertEqual(txs[0]["currency"], q)
                self.assertAlmostEqual(txs[0]["net_amount"], 9010.0)

    def test_legacy_concatenated_pair_with_other_fiat(self):
        t = _KT_H + ("T1,O1,XXBTZJPY,2025-06-02 16:00:00,buy,limit,"
                     "9000000,900000,10,0.1,,,\n")
        txs, _ = _parse({"kr_trades.csv": t}, "kr_trades.csv")
        self.assertEqual([(x["symbol"], x["currency"]) for x in txs],
                         [("BTC", "JPY")])

    def test_ledger_funding_in_other_fiat_is_not_a_send(self):
        for a in ("ZAUD", "AUD", "ZJPY", "CHF", "ZCHF"):
            with self.subTest(a=a):
                led = _ledger([
                    f"L1,F1,2025-03-01 12:00:00,deposit,,currency,{a},"
                    f"spot,1000,0,1000",
                    f"L2,F2,2025-03-05 12:00:00,withdrawal,,currency,{a},"
                    f"spot,-500,5,495"])
                txs, err = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
                self.assertEqual(txs, [])
                self.assertNotIn("UNBOOKED", err)

    def test_ledger_instant_trade_aud_to_btc_is_a_purchase_in_aud(self):
        led = _ledger([
            "L1,R1,2025-03-01 12:00:00,spend,,currency,ZAUD,spot,-1500,"
            "15,0",
            "L2,R1,2025-03-01 12:00:00,receive,,currency,XXBT,spot,0.01,"
            "0,0.01"])
        txs, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertEqual(len(txs), 1)
        self.assertEqual((txs[0]["symbol"], txs[0]["currency"]),
                         ("BTC", "AUD"))
        self.assertAlmostEqual(txs[0]["net_amount"], 1515.0)

    def test_ledger_cad_to_aud_is_a_forex_non_event(self):
        led = _ledger([
            "L1,R1,2025-03-01 12:00:00,spend,,currency,ZCAD,spot,-10,0,0",
            "L2,R1,2025-03-01 12:00:00,receive,,currency,ZAUD,spot,11,0,"
            "11"])
        txs, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertEqual(txs, [])

    def test_aud_hold_reward_is_aud_cash_income(self):
        led = _ledger([
            "L1,R1,2025-03-01 12:00:00,dividend,,currency,AUD.HOLD,spot,"
            "2.5,0,2.5"])
        txs, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertEqual(len(txs), 1)
        self.assertEqual((txs[0]["action"], txs[0]["symbol"],
                          txs[0]["currency"]), ("DIVIDEND", "AUD", "AUD"))
        self.assertAlmostEqual(txs[0]["net_amount"], 2.5)

    def test_zec_is_not_mistaken_for_fiat(self):
        # Z-strip only when the rest is a fiat code: Zcash stays ZEC.
        from taxjson.lib.brokerages.kraken import _normalize_asset
        self.assertEqual(_normalize_asset("ZEC"), "ZEC")
        self.assertEqual(_normalize_asset("XZEC"), "ZEC")
        self.assertEqual(_normalize_asset("ZJPY"), "JPY")


class TestUsStablecoinsAtPar(unittest.TestCase):
    """A2-1004 / A2-1020: in a US project all five USD stablecoins are
    property valued at 1.00 USD par, on every Kraken path."""

    @rule("US-CRYPTO-02")
    def test_rule_text_names_all_five_on_both_exchanges(self):
        from taxjson.lib.tax_logic import catalog
        text = catalog("usa")["US-CRYPTO-02"].text
        for c in ("USDC", "USDT", "DAI", "PYUSD", "GUSD"):
            self.assertIn(c, text)
        self.assertIn("Kraken and Coinbase alike", text)

    @rule("US-CRYPTO-02")
    def test_ledger_instant_swap_against_usdc_uses_par_not_amountusd(self):
        led = _ledger([
            "L1,R1,2025-03-01 12:00:00,spend,,currency,USDC,spot,-1000,0,0,"
            "880,,",
            "L2,R1,2025-03-01 12:00:00,receive,,currency,XETH,spot,0.4,0,"
            "0.4,880,,"], header=_KL_H2)
        txs, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv",
                        cash=False)
        self.assertEqual(sorted(_bs(txs)), [("ETH", 0.4), ("USDC", -1000.0)])
        for t in txs:
            self.assertAlmostEqual(t["net_amount"], 1000.0)

    @rule("US-CRYPTO-02")
    def test_pyusd_quote_and_reward_at_par(self):
        t = _KT_H + ("T1,O1,ETH/PYUSD,2025-06-02 16:00:00,buy,limit,"
                     "2500,1000,0,0.4,,,\n")
        txs, _ = _parse({"kr_trades.csv": t}, "kr_trades.csv", cash=False)
        self.assertEqual(sorted(_bs(txs)), [("ETH", 0.4), ("PYUSD", -1000.0)])
        for x in txs:
            self.assertAlmostEqual(x["net_amount"], 1000.0)
        led = _ledger([
            "L1,R1,2025-03-01 12:00:00,earn,reward,currency,GUSD,earn,5,0,"
            "5"])
        txs, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv",
                        cash=False)
        self.assertEqual([(x["action"], x["symbol"], x["net_amount"])
                          for x in txs],
                         [("DIVIDEND", "GUSD", 5.0), ("BUYSELL", "GUSD", 5.0)])

    @rule("US-CRYPTO-02")
    def test_fiat_base_against_pyusd_or_gusd_is_refused_not_forex(self):
        for q in ("USDC", "PYUSD", "GUSD"):
            with self.subTest(q=q):
                t = _KT_H + (f"T1,O1,EUR/{q},2025-06-02 16:00:00,buy,limit,"
                             f"1.08,108,0,100,,,\n")
                with self.assertRaises(ValueError) as cm:
                    _parse({"kr_trades.csv": t}, "kr_trades.csv",
                           cash=False)
                self.assertIn("prices dollars in a stablecoin",
                              str(cm.exception))

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    @rule("US-CRYPTO-02")
    @rule_absent("US-CRYPTO-02", country="canada")
    def test_pyusd_reward_cash_in_canada_property_in_the_us(self):
        led = _ledger([
            "L1,R1,2025-03-01 12:00:00,earn,reward,currency,PYUSD,earn,5,"
            "0,5"])
        ca, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv", cash=True)
        us, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv", cash=False)
        # Canada: US-dollar cash income, no coin acquired.
        self.assertEqual([(x["action"], x["currency"]) for x in ca],
                         [("DIVIDEND", "USD")])
        self.assertEqual([(x["action"], x["symbol"]) for x in us],
                         [("DIVIDEND", "PYUSD"), ("BUYSELL", "PYUSD")])


class TestCsvShape(unittest.TestCase):
    """A2-0246 / A2-0247 / A2-0248 / A2-1022."""

    _TR = _KT_H + (
        'T1,O1,SOL/USD,2025-06-01 16:00:00,buy,limit,100,100,0,1,,x,LA\n'
        'T2,O2,SOL/USD,2025-06-02 16:00:00,buy,limit,100,200,0,2,,y,LB\n'
        'T3,O3,SOL/USD,2025-06-03 16:00:00,buy,limit,100,300,0,3,,z,LC\n')
    _LE = _ledger([
        "L1,R1,2025-06-01 12:00:00,staking,,currency,SOL.S,spot,0.1,0,0.1",
        "L2,R2,2025-06-02 12:00:00,staking,,currency,SOL.S,spot,0.2,0,0.3",
        "L3,R3,2025-06-03 12:00:00,staking,,currency,SOL.S,spot,0.3,0,0.6"])

    def test_control(self):
        txs, _ = _parse({"kr_trades.csv": self._TR}, "kr_trades.csv")
        self.assertEqual(len(txs), 3)
        txs, _ = _parse({"kr_ledgers.csv": self._LE}, "kr_ledgers.csv")
        self.assertEqual(len(txs), 6)

    def test_stray_quote_closing_later_is_refused_naming_its_line(self):
        bad = self._TR.replace(",x,LA", ',"x,LA').replace(",z,LC", ',z",LC')
        with self.assertRaises(ValueError) as cm:
            _parse({"kr_trades.csv": bad}, "kr_trades.csv")
        self.assertIn("line 2", str(cm.exception))
        self.assertIn("line break", str(cm.exception))
        bad = self._LE.replace(",spot,0.1,0,0.1", ',spot,0.1,0,"0.1').replace(
            ",spot,0.3,0,0.6", ',spot,0.3,0,0.6"')
        with self.assertRaises(ValueError) as cm:
            _parse({"kr_ledgers.csv": bad}, "kr_ledgers.csv")
        self.assertIn("line 2", str(cm.exception))

    def test_stray_quote_in_a_sibling_ledger_is_refused(self):
        t = _KT_H + ("T1,O1,SOL/CAD,2025-06-02 16:00:00.1,buy,limit,200,"
                     "2000,5,10,,,\n")
        bad = self._LE.replace(",spot,0.1,0,0.1", ',spot,0.1,0,"0.1')
        with self.assertRaises(ValueError) as cm:
            _parse({"kr_trades.csv": t, "kr_ledgers.csv": bad},
                   "kr_trades.csv")
        self.assertIn("kr_ledgers.csv line 2", str(cm.exception))

    def test_unterminated_quote_names_the_opening_line(self):
        bad = self._LE.replace(",spot,0.1,0,0.1", ',spot,0.1,0,"0.1')
        with self.assertRaises(ValueError) as cm:
            _parse({"kr_ledgers.csv": bad}, "kr_ledgers.csv")
        msg = str(cm.exception)
        self.assertIn("line 2", msg)
        self.assertIn("quote", msg)
        self.assertNotIn("truncated", msg)

    def test_duplicate_column_is_refused(self):
        led = _ledger([
            "L1,F1,2025-03-01 12:00:00,withdrawal,,currency,XXBT,spot,-0.1,"
            "0.0002,0,0"], header=_KL_H.replace("balance\n", "balance,fee\n"))
        with self.assertRaises(ValueError) as cm:
            _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIn("fee", str(cm.exception))
        self.assertIn("twice", str(cm.exception))
        t = _KT_H.replace("ledgers\n", "ledgers,Cost\n") + (
            "T1,O1,SOL/USD,2025-06-01 16:00:00,buy,limit,100,100,0,1,,,,0\n")
        with self.assertRaises(ValueError):
            _parse({"kr_trades.csv": t}, "kr_trades.csv")


class TestUnbookedRows(unittest.TestCase):
    """A2-0245 / A2-0578 / A2-1002 / A2-1017 / A2-0583."""

    def test_trades_row_with_blank_or_unknown_type_is_unbooked(self):
        for typ in ("", "settle"):
            with self.subTest(typ=typ):
                t = _KT_H + (f"T1,O1,SOL/USD,2025-06-01 16:00:00,{typ},"
                             f"limit,65000,3250,0,0.05,,,\n")
                _txs, err = _parse({"kr_trades.csv": t}, "kr_trades.csv")
                self.assertIn("warning: UNBOOKED", err)

    def test_fiat_credit_or_adjustment_is_unbooked(self):
        for typ, a in (("credit", "ZUSD"), ("adjustment", "ZCAD")):
            with self.subTest(typ=typ):
                led = _ledger([f"L1,R1,2025-03-01 12:00:00,{typ},,currency,"
                               f"{a},spot,50,0,50"])
                txs, err = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
                self.assertEqual(txs, [])
                self.assertIn("warning: UNBOOKED", err)
                self.assertNotIn("moving your own cash", err)

    def test_fiat_deposit_still_ignored_quietly(self):
        led = _ledger(["L1,F1,2025-03-01 12:00:00,deposit,,currency,ZUSD,"
                       "spot,50,0,50"])
        txs, err = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertEqual(txs, [])
        self.assertNotIn("UNBOOKED", err)

    def test_zero_amount_coin_row_with_a_fee_is_unbooked(self):
        for typ in ("adjustment", "transfer"):
            with self.subTest(typ=typ):
                led = _ledger([f"L1,R1,2025-03-01 12:00:00,{typ},,currency,"
                               f"XETH,spot,0,0.001,0.5"])
                txs, err = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv")
                self.assertIn("warning: UNBOOKED", err)
                self.assertNotIn("moving your own cash", err)

    def test_earn_migration_is_a_wallet_move(self):
        led = _ledger([
            "L1,R1,2025-03-01 12:00:00,earn,migration,currency,DOT,spot,"
            "-100,0,0",
            "L2,R1,2025-03-01 12:00:00,earn,migration,currency,DOT.S,earn,"
            "100,0,100"])
        txs, err, k = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv",
                             want_extractor=True)
        self.assertEqual(txs, [])
        self.assertNotIn("UNBOOKED", err)
        self.assertTrue(getattr(k, "zero_tx_reason", None))

    def test_file_of_only_non_events_reports_a_reason(self):
        for rows in (
                ["L1,R1,2025-03-01 12:00:00,spend,,currency,XETH,spot,-1,0,"
                 "1", "L2,R1,2025-03-01 12:00:00,receive,,currency,ETH2.S,"
                 "spot,1,0,1"],
                ["L1,F1,2025-03-01 12:00:00,deposit,,currency,ZUSD,spot,50,"
                 "0,50"]):
            with self.subTest(rows=rows[0]):
                txs, _err, k = _parse({"kr_ledgers.csv": _ledger(rows)},
                                      "kr_ledgers.csv", want_extractor=True)
                self.assertEqual(txs, [])
                self.assertTrue(getattr(k, "zero_tx_reason", None))

    def test_unbooked_file_gets_no_zero_tx_reason(self):
        led = _ledger(["L1,R1,2025-03-01 12:00:00,credit,,currency,ZUSD,"
                       "spot,50,0,50"])
        _txs, _err, k = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv",
                               want_extractor=True)
        self.assertFalse(getattr(k, "zero_tx_reason", None))


class TestFeeInAnotherCoin(unittest.TestCase):
    """A2-0582 / A2-1018: the fee coins left the account — a sale."""

    @rule("CA-CRYPTO-03")
    def test_reward_fee_in_another_coin_is_disposed_of(self):
        led = _ledger([
            "L1,R1,2026-03-01 12:00:00,earn,reward,currency,DOT,earn,10,"
            "0.001,10,50,3,XETH"], header=_KL_H2)
        for cash in (True, False):
            with self.subTest(cash=cash):
                txs, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv",
                                cash=cash)
                fee = [t for t in txs if t["symbol"] == "ETH"]
                self.assertEqual(len(fee), 1)
                self.assertAlmostEqual(fee[0]["quantity"], -0.001)
                self.assertAlmostEqual(fee[0]["net_amount"], 3.0)
                div = [t for t in txs if t["action"] == "DIVIDEND"][0]
                self.assertAlmostEqual(div["net_amount"], 47.0)

    @rule("US-CRYPTO-03")
    def test_fiat_withdrawal_fee_in_a_coin_is_disposed_of(self):
        led = _ledger([
            "L1,F1,2026-03-01 12:00:00,withdrawal,,currency,ZUSD,spot,-500,"
            "0.001,0,500,3,ETH"], header=_KL_H2)
        for cash in (True, False):
            with self.subTest(cash=cash):
                txs, _ = _parse({"kr_ledgers.csv": led}, "kr_ledgers.csv",
                                cash=cash)
                self.assertEqual(_bs(txs), [("ETH", -0.001)])
                self.assertAlmostEqual(txs[0]["net_amount"], 3.0)


if __name__ == "__main__":
    unittest.main()
