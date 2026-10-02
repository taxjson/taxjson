"""Regression tests for the medium-round crypto parser findings
(parsers2 area). All data is synthetic.

R1-108/R1-299  overlapping Kraken ledger exports broke the trades join
S013-09        repeated ledger rows doubled an instant trade
R1-107         unhandled balance-changing ledger types hid behind a
               "no gain effect" note; margin fills unflagged
S061-23        orphan instant-trade legs were warn-only
S061-12        short/long Kraken rows read missing cells as 0
R1-112         asset/pair codes were case-sensitive
S014-01/S061-08 bonded lock-period staking codes (DOT28.S) not folded
R1-110/S061-11 ETH2 <-> ETH swaps realized a gain
R1-111         Coinbase blank Price Currency cell defaulted to USD
R1-109         Coinbase header with spaces parsed to 0 rows
S056-06        an unterminated Coinbase quote swallowed later rows
S013-08        crypto-to-crypto swaps were valued twice
S025-09        a 0.0 Yahoo close left the row at $0 silently
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from tax_rules import rule

CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")
KR_TRADES_H = ("txid,ordertxid,pair,aclass,subclass,time,type,ordertype,"
               "price,cost,fee,vol,margin,misc,ledgers\n")
KR_LEDGER_H = ("txid,refid,time,type,subtype,aclass,subclass,asset,wallet,"
               "amount,fee,balance\n")
KR_LEDGER_USD_H = ("txid,refid,time,type,subtype,aclass,subclass,asset,"
                   "wallet,amount,fee,balance,amountusd\n")


def _cb_row(cid, ts, typ, asset, qty, ccy, price, sub, total, fee,
            notes=""):
    return (f'{cid},{ts},{typ},{asset},{qty},{ccy},"{price}","{sub}",'
            f'"{total}","{fee}","{notes}"\n')


def _parse_cb(text, header=CB_HEADER):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cb_2025.csv"
        p.write_text(header + text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = CoinbaseBrokerage().parse_file(p)
    return txs, err.getvalue()


def _parse_kr(files, target):
    """Write `files` {name: text} into one folder, parse `target`."""
    from taxjson.lib.brokerages.kraken import KrakenBrokerage
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = KrakenBrokerage().parse_file(Path(td) / target)
    return txs, err.getvalue()


def _bs(txs):
    return [t for t in txs if t["action"] == "BUYSELL"]


# A SOL/USD buy fill and its two ledger legs.
_TRADE = ("TB1,OB1,SOL/USD,forex,,2025-06-01 14:00:00.1,buy,limit,"
          "150.0,1500.0,3.0,10.0,0,,\n")
_LEG_SOL = ("LS1,TB1,2025-06-01 14:00:00.1,trade,,currency,,SOL,spot,"
            "10.0,0,10.0\n")
_LEG_USD = ("LU1,TB1,2025-06-01 14:00:00.1,trade,,currency,,USD,spot,"
            "-1500.0,3.0,500.0\n")


class TestKrakenLedgerOverlap(unittest.TestCase):
    """R1-108 / R1-299: two ledger exports that repeat the same rows."""

    def test_duplicate_ledger_copy_joins_cleanly(self):
        led = KR_LEDGER_H + _LEG_SOL + _LEG_USD
        txs, err = _parse_kr({"kr_trades_2025.csv": KR_TRADES_H + _TRADE,
                              "kr_ledgers_2025.csv": led,
                              "kr_ledgers_2025b.csv": led},
                             "kr_trades_2025.csv")
        self.assertEqual(len(_bs(txs)), 1)
        self.assertAlmostEqual(_bs(txs)[0]["quantity"], 10.0)

    def test_overlap_of_one_leg_joins_cleanly(self):
        txs, _ = _parse_kr({"kr_trades_2025.csv": KR_TRADES_H + _TRADE,
                            "kr_ledgers_2025.csv":
                                KR_LEDGER_H + _LEG_SOL + _LEG_USD,
                            "kr_ledgers_extra.csv": KR_LEDGER_H + _LEG_SOL},
                           "kr_trades_2025.csv")
        self.assertEqual(len(_bs(txs)), 1)

    def test_same_txid_different_content_raises(self):
        other = _LEG_SOL.replace("10.0,0,10.0", "9.0,0,9.0")
        with self.assertRaisesRegex(ValueError, "txid"):
            _parse_kr({"kr_trades_2025.csv": KR_TRADES_H + _TRADE,
                       "kr_ledgers_2025.csv":
                           KR_LEDGER_H + _LEG_SOL + _LEG_USD,
                       "kr_ledgers_b.csv": KR_LEDGER_H + other},
                      "kr_trades_2025.csv")


_INSTANT = (
    "LA1,RI1,2025-06-01 10:00:00,spend,,currency,,USD,spot,-1000.0,0,0\n"
    "LA2,RI1,2025-06-01 10:00:00,receive,,currency,,ETH,spot,0.4,0,0.4\n")


class TestKrakenRepeatedLedgerRows(unittest.TestCase):
    """S013-09: repeated rows inside one ledger file."""

    def test_repeated_rows_do_not_double_the_trade(self):
        txs, err = _parse_kr({"kr_ledgers_2025.csv":
                              KR_LEDGER_H + _INSTANT + _INSTANT},
                             "kr_ledgers_2025.csv")
        bs = _bs(txs)
        self.assertEqual(len(bs), 1)
        self.assertAlmostEqual(bs[0]["quantity"], 0.4)
        self.assertAlmostEqual(bs[0]["net_amount"], 1000.0)
        self.assertIn("repeated", err)

    def test_split_settlement_with_distinct_txids_still_sums(self):
        extra = ("LA3,RI1,2025-06-01 10:00:00,receive,,currency,,ETH,"
                 "spot,0.1,0,0.5\n")
        txs, _ = _parse_kr({"kr_ledgers_2025.csv":
                            KR_LEDGER_H + _INSTANT + extra},
                           "kr_ledgers_2025.csv")
        self.assertAlmostEqual(_bs(txs)[0]["quantity"], 0.5)

    def test_repeated_txid_with_different_content_raises(self):
        bad = _INSTANT.replace("0.4,0,0.4", "0.8,0,0.8").splitlines()[1]
        with self.assertRaisesRegex(ValueError, "txid"):
            _parse_kr({"kr_ledgers_2025.csv":
                       KR_LEDGER_H + _INSTANT + bad + "\n"},
                      "kr_ledgers_2025.csv")


class TestKrakenUnbookedIsLoud(unittest.TestCase):
    """R1-107 / S061-23."""

    def test_airdrop_is_unbooked_warning(self):
        led = KR_LEDGER_H + ("LX1,RX1,2025-06-01 10:00:00,airdrop,,"
                             "currency,,FLR,spot,1000.0,0,1000.0\n")
        _, err = _parse_kr({"kr_ledgers_2025.csv": led},
                           "kr_ledgers_2025.csv")
        self.assertIn("warning: UNBOOKED:", err)
        self.assertIn("airdrop", err)
        self.assertNotIn("don't affect gains", err)

    def test_conversion_is_unbooked_warning(self):
        led = KR_LEDGER_H + (
            "LX1,RX1,2025-06-01 10:00:00,conversion,,currency,,MATIC,"
            "spot,-200.0,0,0\n"
            "LX2,RX1,2025-06-01 10:00:00,conversion,,currency,,USD,spot,"
            "40.0,0,40\n")
        _, err = _parse_kr({"kr_ledgers_2025.csv": led},
                           "kr_ledgers_2025.csv")
        self.assertIn("warning: UNBOOKED:", err)
        self.assertIn("conversion", err)

    def test_fiat_transfer_stays_a_calm_note(self):
        led = KR_LEDGER_H + ("LX1,RX1,2025-06-01 10:00:00,transfer,,"
                             "currency,,CAD,spot,-50.0,0,0\n")
        _, err = _parse_kr({"kr_ledgers_2025.csv": led},
                           "kr_ledgers_2025.csv")
        self.assertNotIn("UNBOOKED", err)
        self.assertIn("transfer", err)

    def test_margin_fill_is_flagged(self):
        tr = KR_TRADES_H + ("TM1,OM1,ETH/USD,forex,,2025-06-01 14:00:00.1,"
                            "sell,limit,2000.0,2000.0,3.0,1.0,400.0,,\n")
        txs, err = _parse_kr({"kr_trades_2025.csv": tr},
                             "kr_trades_2025.csv")
        self.assertEqual(len(_bs(txs)), 1)
        self.assertIn("margin", err.lower())
        self.assertIn("warning", err)

    def test_orphan_spend_is_unbooked(self):
        led = KR_LEDGER_H + ("LO1,RO1,2025-05-20 10:00:00,spend,,currency,,"
                             "BTC,spot,-0.1,0,0\n")
        txs, err = _parse_kr({"kr_ledgers_2025.csv": led},
                             "kr_ledgers_2025.csv")
        self.assertEqual(_bs(txs), [])
        self.assertIn("warning: UNBOOKED:", err)
        self.assertIn("orphan spend", err)


class TestKrakenShortRows(unittest.TestCase):
    """S061-12."""

    def test_ledger_row_cut_after_amount_raises(self):
        led = KR_LEDGER_H + ("LR1,RR1,2025-06-01 10:00:00,earn,reward,"
                             "currency,,SOL,spot,0.2\n")
        with self.assertRaisesRegex(ValueError, "cells"):
            _parse_kr({"kr_ledgers_2025.csv": led}, "kr_ledgers_2025.csv")

    def test_trades_row_with_extra_cell_raises(self):
        tr = KR_TRADES_H + _TRADE.rstrip("\n") + ",EXTRA\n"
        with self.assertRaisesRegex(ValueError, "cells"):
            _parse_kr({"kr_trades_2025.csv": tr}, "kr_trades_2025.csv")


class TestSymbolCase(unittest.TestCase):
    """R1-112."""

    def test_lowercase_kraken_pair(self):
        tr = KR_TRADES_H + _TRADE.replace("SOL/USD", "sol/usd")
        txs, _ = _parse_kr({"kr_trades_2025.csv": tr}, "kr_trades_2025.csv")
        bs = _bs(txs)
        self.assertEqual([(t["symbol"], t["currency"]) for t in bs],
                         [("SOL", "USD")])
        self.assertAlmostEqual(bs[0]["net_amount"], 1503.0)

    def test_lowercase_ledger_asset(self):
        from taxjson.lib.brokerages.kraken import _normalize_asset
        self.assertEqual(_normalize_asset("dot.s"), "DOT")
        self.assertEqual(_normalize_asset("xxbt"), "BTC")

    def test_coinbase_mixed_case_asset_one_pool(self):
        txs, _ = _parse_cb(
            _cb_row("a1", "2025-01-02 10:00:00 UTC", "Buy", "sol", "1",
                    "CAD", "100", "100", "101", "1")
            + _cb_row("a2", "2025-03-02 10:00:00 UTC", "Sell", "SOL",
                      "-1", "CAD", "200", "200", "199", "1"))
        self.assertEqual({t["symbol"] for t in _bs(txs)}, {"SOL"})


class TestBondedStakingCodes(unittest.TestCase):
    """S014-01 / S061-08."""

    def test_lock_period_codes_fold(self):
        from taxjson.lib.brokerages.kraken import _normalize_asset
        for raw, want in (("DOT28.S", "DOT"), ("KSM07.S", "KSM"),
                          ("ATOM21.S", "ATOM"), ("SOL03.S", "SOL"),
                          ("MATIC04.S", "MATIC"), ("FLOW14.S", "FLOW"),
                          ("DOT.S", "DOT"), ("ETH2.S", "ETH")):
            self.assertEqual(_normalize_asset(raw), want, raw)

    def test_real_digit_coins_untouched(self):
        from taxjson.lib.brokerages.kraken import _normalize_asset
        for raw, want in (("0G", "0G"), ("1INCH", "1INCH"), ("C98", "C98"),
                          ("C98.S", "C98"), ("L3", "L3")):
            self.assertEqual(_normalize_asset(raw), want, raw)

    @rule("CA-INC-04")
    @rule("US-INC-02")
    def test_reward_books_to_bare_coin(self):
        led = KR_LEDGER_H + ("LS1,RS1,2025-06-01 10:00:00,staking,,"
                             "currency,,DOT28.S,spot,2.0,0,2.0\n")
        txs, _ = _parse_kr({"kr_ledgers_2025.csv": led},
                           "kr_ledgers_2025.csv")
        self.assertEqual({t["symbol"] for t in txs}, {"DOT"})


class TestEth2IsEth(unittest.TestCase):
    """R1-110 (Coinbase) / S061-11 (Kraken)."""

    def test_coinbase_eth2_staking_income_is_eth(self):
        txs, _ = _parse_cb(_cb_row("s1", "2025-01-02 10:00:00 UTC",
                                   "Staking Income", "ETH2", "0.01", "CAD",
                                   "4000", "40", "40", "0"))
        self.assertEqual({t["symbol"] for t in txs}, {"ETH"})

    def test_coinbase_convert_eth_to_eth2_is_a_non_event(self):
        txs, err = _parse_cb(
            _cb_row("b1", "2025-01-02 10:00:00 UTC", "Buy", "ETH", "1",
                    "CAD", "2000", "2000", "2000", "0")
            + _cb_row("c1", "2025-06-02 10:00:00 UTC", "Convert", "ETH",
                      "-1", "CAD", "4000", "4000", "4000", "0",
                      "Converted 1 ETH to 1 ETH2"))
        self.assertEqual(len(_bs(txs)), 1)          # the buy only
        self.assertIn("ETH", err)

    def test_coinbase_convert_eth_to_eth2_mismatch_raises(self):
        with self.assertRaisesRegex(ValueError, r"\.tt"):
            _parse_cb(_cb_row("c1", "2025-06-02 10:00:00 UTC", "Convert",
                              "ETH", "-1", "CAD", "4000", "4000", "4000",
                              "0", "Converted 1 ETH to 0.9 ETH2"))

    def test_kraken_eth2s_eth_trade_is_a_non_event(self):
        tr = KR_TRADES_H + ("TE1,OE1,ETH2.S/ETH,forex,,2025-06-02 10:00:00.1,"
                            "buy,limit,1.0,1.0,0,1.0,0,,\n")
        txs, _ = _parse_kr({"kr_trades_2025.csv": tr}, "kr_trades_2025.csv")
        self.assertEqual(_bs(txs), [])

    def test_kraken_ledger_eth_to_eth2s_is_a_non_event(self):
        led = KR_LEDGER_H + (
            "LW1,RW1,2025-06-02 10:00:00,spend,,currency,,XETH,spot,-1.0,"
            "0,1.0\n"
            "LW2,RW1,2025-06-02 10:00:00,receive,,currency,,ETH2.S,spot,"
            "1.0,0,1.0\n")
        txs, _ = _parse_kr({"kr_ledgers_2025.csv": led},
                           "kr_ledgers_2025.csv")
        self.assertEqual(_bs(txs), [])

    def test_kraken_ledger_eth_to_eth2s_mismatch_raises(self):
        led = KR_LEDGER_H + (
            "LW1,RW1,2025-06-02 10:00:00,spend,,currency,,XETH,spot,-1.0,"
            "0,1.0\n"
            "LW2,RW1,2025-06-02 10:00:00,receive,,currency,,ETH2.S,spot,"
            "0.5,0,1.0\n")
        with self.assertRaisesRegex(ValueError, r"\.tt"):
            _parse_kr({"kr_ledgers_2025.csv": led}, "kr_ledgers_2025.csv")


class TestCoinbaseLayout(unittest.TestCase):
    """R1-111 / R1-109 / S056-06."""

    def test_blank_price_currency_cell_raises(self):
        with self.assertRaisesRegex(ValueError, "Price Currency"):
            _parse_cb(_cb_row("b1", "2025-01-02 10:00:00 UTC", "Buy", "BTC",
                              "0.01", "", "100000", "1000", "1010", "10"))

    def test_absent_price_currency_column_stays_usd(self):
        hdr = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
               "Price at Transaction,Subtotal,"
               "Total (inclusive of fees and/or spread),Fees and/or Spread,"
               "Notes\n")
        txs, _ = _parse_cb('b1,2025-01-02 10:00:00 UTC,Buy,BTC,0.01,'
                           '"100000","1000","1010","10",""\n', header=hdr)
        self.assertEqual(_bs(txs)[0]["currency"], "USD")

    def test_header_with_spaces_parses(self):
        hdr = ", ".join(c.strip() for c in CB_HEADER.split(",")) + "\n"
        txs, _ = _parse_cb(_cb_row("b1", "2025-01-02 10:00:00 UTC", "Buy",
                                   "BTC", "0.01", "CAD", "100000", "1000",
                                   "1010", "10"), header=hdr)
        self.assertEqual(len(_bs(txs)), 1)

    def test_unterminated_quote_swallowing_rows_raises(self):
        rows = ('b1,2025-01-02 10:00:00 UTC,Buy,BTC,0.01,CAD,"100000",'
                '"1000","1010","10","Bought 0.01 BTC ""\n'
                + _cb_row("b2", "2025-01-03 10:00:00 UTC", "Buy", "ETH",
                          "0.5", "CAD", "4000", "2000", "2010", "10")
                + _cb_row("b3", "2025-01-04 10:00:00 UTC", "Buy", "SOL", "1",
                          "CAD", "200", "200", "201", "1", "a, b"))
        with self.assertRaisesRegex(ValueError, "line"):
            _parse_cb(rows)

    def test_row_wider_than_header_raises(self):
        row = _cb_row("b1", "2025-01-02 10:00:00 UTC", "Buy", "BTC", "0.01",
                      "CAD", "100000", "1000", "1010", "10").rstrip("\n")
        with self.assertRaisesRegex(ValueError, "cells"):
            _parse_cb(row + ",extra\n")


class TestSwapOneValue(unittest.TestCase):
    """S013-08."""

    def test_kraken_instant_swap_uses_amountusd_for_both_legs(self):
        led = KR_LEDGER_USD_H + (
            "LQ1,RQ1,2025-09-10 10:00:00,spend,,currency,,SOL,spot,-20.0,0,"
            "0,4349.15\n"
            "LQ2,RQ1,2025-09-10 10:00:00,receive,,currency,,ETH,spot,1.0,0,"
            "1.0,4349.15\n")
        txs, _ = _parse_kr({"kr_ledgers_2025.csv": led},
                           "kr_ledgers_2025.csv")
        bs = {t["symbol"]: t for t in _bs(txs)}
        self.assertAlmostEqual(bs["SOL"]["net_amount"], 4349.15)
        self.assertAlmostEqual(bs["ETH"]["net_amount"], 4349.15)
        self.assertAlmostEqual(bs["ETH"]["price"], 4349.15)
        self.assertAlmostEqual(bs["SOL"]["price"], 4349.15 / 20)

    def _fill(self, rows, prices):
        import taxjson.bin.fill_crypto_prices as fc
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.json"
            inp.write_text(json.dumps({"transactions": rows}))
            saved = (fc.CACHE_FILE, fc.get_crypto_price, sys.argv,
                     fc.time.sleep)
            fc.CACHE_FILE = str(Path(tmp) / "cache.json")
            fc.get_crypto_price = lambda s, d: prices.get(s, 0.0)
            fc.time.sleep = lambda s: None
            sys.argv = ["fill-crypto", str(inp)]
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    fc.main()
            finally:
                (fc.CACHE_FILE, fc.get_crypto_price, sys.argv,
                 fc.time.sleep) = saved
        txs = json.loads(out.getvalue())["transactions"]
        return {t["symbol"]: t for t in txs}, err.getvalue()

    @staticmethod
    def _leg(tid, sym, qty):
        return {"action": "BUYSELL", "date": "2025-09-10", "time": "10:00:00",
                "symbol": sym, "quantity": qty, "price": 0.0,
                "net_amount": 0.0, "currency": "USD", "id": tid}

    def test_fill_values_both_legs_from_the_received_coin(self):
        out, err = self._fill([self._leg("RQ1-sell", "SOL", -20.0),
                               self._leg("RQ1-buy", "ETH", 1.0)],
                              {"SOL": 223.987, "ETH": 4349.15})
        self.assertAlmostEqual(out["ETH"]["net_amount"], 4349.15)
        self.assertAlmostEqual(out["SOL"]["net_amount"], 4349.15)
        self.assertAlmostEqual(out["SOL"]["price"], 4349.15 / 20)
        self.assertIn("swap", err.lower())

    def test_fill_base_quote_legs(self):
        out, _ = self._fill([self._leg("T1-base", "SOL", -20.0),
                             self._leg("T1-quote", "ETH", 1.0)],
                            {"SOL": 223.987, "ETH": 4349.15})
        self.assertAlmostEqual(out["SOL"]["net_amount"], 4349.15)

    def test_fill_coinbase_quote_leg(self):
        out, _ = self._fill([self._leg("cb9", "ETH", 1.0),
                             self._leg("cb9-quote", "BTC", -0.05)],
                            {"BTC": 90000.0, "ETH": 4349.15})
        self.assertAlmostEqual(out["BTC"]["net_amount"], 4349.15)

    def test_fill_falls_back_to_the_spent_coin(self):
        out, _ = self._fill([self._leg("RQ1-sell", "SOL", -20.0),
                             self._leg("RQ1-buy", "ZZNEW", 1000.0)],
                            {"SOL": 200.0})
        self.assertAlmostEqual(out["ZZNEW"]["net_amount"], 4000.0)
        self.assertAlmostEqual(out["SOL"]["net_amount"], 4000.0)

    def test_rows_without_ids_price_individually(self):
        a, b = self._leg("x", "SOL", -20.0), self._leg("y", "ETH", 1.0)
        del a["id"], b["id"]
        out, _ = self._fill([a, b], {"SOL": 200.0, "ETH": 4349.15})
        self.assertAlmostEqual(out["SOL"]["net_amount"], 4000.0)
        self.assertAlmostEqual(out["ETH"]["net_amount"], 4349.15)


class TestZeroCloseIsLoud(unittest.TestCase):
    """S025-09: a 0.0 close is a failed lookup (already fixed by S000-00)."""

    def test_zero_close_warns(self):
        from taxjson.bin import fill_crypto_prices as fcp

        class _R:
            def read(self):
                return json.dumps({"chart": {"result": [{"indicators": {
                    "quote": [{"close": [0.0]}]}}]}}).encode()
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch("urllib.request.urlopen", return_value=_R()), \
                contextlib.redirect_stderr(err):
            p = fcp.get_crypto_price("SOL", "2025-03-02")
        self.assertEqual(p, 0.0)
        self.assertIn("failed to fetch crypto price", err.getvalue())


if __name__ == "__main__":
    unittest.main()
