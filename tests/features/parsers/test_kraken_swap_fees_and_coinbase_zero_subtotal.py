"""Fixes of 2026-10-30, round 7, wave E, group cash: T1135 cash in
transit, export coverage, Kraken swap fees, Coinbase's blank Total and
repeated hour across exports, and the crypto-sends migration on read-only
inputs. One test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented books and accounts, made-up tickers
and ids, small amounts. Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7e_cash
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _support.cb_price_fill import _HDR as _CB_HDR
from _support.cb_price_fill import _brokerage, _fill
from taxjson.lib.dispatch import python_module_argv


# ============================================== TJ-1159 Kraken's trades
# export: an unpriced swap keeps its fee
class TestTJ1159KrakenTradesSwapFee(unittest.TestCase):
    """TJ-1159: the crypto-to-crypto branch of the trades parser folded
    the fee into the leg quantities and said nothing of it, so the filler
    valued the swap from the received units alone.

    Formerly: test_fix_1029_r7e_cash.TestTJ1159KrakenTradesSwapFee"""

    CACHE1 = {"SYN-2025-06-02": 30, "ZZQ-2025-06-02": 100}
    CACHE2 = {"SYN-2025-06-02": 10, "ZZQ-2025-06-02": 5}
    T_HDR = "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
    L_HDR = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance"

    def _legs(self, td, name, trade, ledger, cache):
        d = Path(td) / name
        d.mkdir()
        (d / "trades.csv").write_text(self.T_HDR + trade)
        if ledger:
            (d / "ledgers.csv").write_text(ledger)
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", "kraken", "--country", "canada",
                                str(d / "trades.csv")]),
            capture_output=True, text=True,
            env=dict(os.environ, TAXJSON_WIDTH="0",
                     TAXJSON_LOCAL_TZ="America/Toronto"))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = d / "parsed.json"
        out.write_text(r.stdout)
        return {x["symbol"]: x for x in _fill(out, cache)}

    def _ledger(self, rows, usd=False):
        hdr = self.L_HDR + (",amountusd" if usd else "") + "\n"
        return hdr + "".join(
            f"{i},T1,2025-06-02 14:00:00,trade,,currency,{a},{q},{f},0"
            + (f",{u}" if usd else "") + "\n" for i, a, q, f, u in rows)

    def _both(self, legs, want):
        for sym, leg in legs.items():
            self.assertAlmostEqual(leg["net_amount"], want, msg=sym)

    def test_a_buy_with_no_ledger_counts_its_quote_fee(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", "T1,O1,SYN/ZZQ,2025-06-02 14:00:00.0000,"
                              "buy,market,0.3,3,0.03,10,0,,\n", "", self.CACHE1)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], -3.03)
        self._both(legs, 303.0)

    def test_a_sell_with_no_ledger_is_net_of_its_fee(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", "T1,O1,SYN/ZZQ,2025-06-02 14:00:00.0000,"
                              "sell,market,0.3,3,0.03,10,0,,\n", "", self.CACHE1)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], 2.97)
        self._both(legs, 297.0)

    def test_no_fee_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", "T1,O1,SYN/ZZQ,2025-06-02 14:00:00.0000,"
                              "buy,market,0.3,3,0,10,0,,\n", "", self.CACHE1)
        self._both(legs, 300.0)
        self.assertNotIn("swap_fee", legs["SYN"])

    BUY2 = "T1,O1,ZZQ/SYN,2025-06-02 14:00:00.0000,buy,market,0.5,10,0,20,0,,L4 L5\n"

    def test_a_joined_fee_in_the_received_coin(self):
        rows = [("L4", "SYN", -10, 0, 100), ("L5", "ZZQ", 20, 0.2, 100)]
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", self.BUY2, self._ledger(rows),
                              self.CACHE2)
            valued = self._legs(td, "b", self.BUY2,
                                self._ledger(rows, usd=True), self.CACHE2)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], 19.8)
        self._both(legs, 100.0)
        self._both(valued, 100.0)

    def test_a_joined_fee_in_the_spent_coin_is_as_with_amountusd(self):
        # A sell's fee in the coin it spends (the base coin).
        sell = ("T1,O1,ZZQ/SYN,2025-06-02 14:00:00.0000,sell,market,0.5,10,0,"
                "20,0,,L4 L5\n")
        rows = [("L4", "ZZQ", -20, 0.2, 100), ("L5", "SYN", 10, 0, 100)]
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", sell, self._ledger(rows), self.CACHE2)
            valued = self._legs(td, "b", sell, self._ledger(rows, usd=True),
                                self.CACHE2)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], -20.2)
        self._both(legs, 100.0)
        self._both(valued, 100.0)

    def test_a_joined_sell_with_its_fee_in_the_received_coin(self):
        sell = ("T1,O1,ZZQ/SYN,2025-06-02 14:00:00.0000,sell,market,0.5,10,"
                "0.1,20,0,,L4 L5\n")
        rows = [("L4", "ZZQ", -20, 0, 100), ("L5", "SYN", 10, 0.1, 100)]
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", sell, self._ledger(rows), self.CACHE2)
            valued = self._legs(td, "b", sell, self._ledger(rows, usd=True),
                                self.CACHE2)
        self.assertAlmostEqual(legs["SYN"]["quantity"], 9.9)
        self._both(legs, 100.0)
        self._both(valued, 100.0)


# ============================================== TJ-1188 a stated Subtotal 0
class TestTJ1188BlankTotalWithSubtotalZero(unittest.TestCase):
    """TJ-1188: a stated Subtotal of 0 was read as a blank cell: the row
    was booked at quantity x price, while the same row with its Total
    filled in is refused.

    Formerly: test_fix_1029_r7e_cash.TestTJ1188BlankTotalWithSubtotalZero"""

    def _parse(self, td, name, price, subtotal, total):
        row = (f"A1,2025-06-02 14:00:00 UTC,Buy,SYN,10,CAD,{price},{subtotal},"
               f"{total},0,Bought\n")
        return _brokerage(td, name, "coinbase", _CB_HDR + row)

    def test_refused_either_way(self):
        with tempfile.TemporaryDirectory() as td:
            for name, total in (("blank", ""), ("given", "100")):
                rc, _path, err = self._parse(td, name, 10, 0, total)
                self.assertNotEqual(rc, 0, name)
                self.assertIn("Subtotal 0.00 does not fit", err, name)

    def test_a_blank_subtotal_still_derives(self):
        with tempfile.TemporaryDirectory() as td:
            rc, path, err = self._parse(td, "blank", 10, "", "")
            self.assertEqual(rc, 0, err)
            row = json.loads(path.read_text())["transactions"][0]
            self.assertAlmostEqual(row["net_amount"], 100.0)

    def test_nothing_to_derive_from_is_the_old_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            rc, _path, err = self._parse(td, "zero", "", 0, "")
            self.assertNotEqual(rc, 0)
            self.assertIn("the Total cell is blank", err)


if __name__ == "__main__":
    unittest.main()
