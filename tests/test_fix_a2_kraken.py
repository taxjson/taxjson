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


if __name__ == "__main__":
    unittest.main()
