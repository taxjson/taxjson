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

Formerly: test_fix_m_parsers2_crypto
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule
from taxjson.rules import ids as R
from _siblings import with_siblings
from _support.crypto_rows import KR_LEDGER_H, _cb_row, _parse_cb, _bs


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

KR_TRADES_H = ("txid,ordertxid,pair,aclass,subclass,time,type,ordertype,"
               "price,cost,fee,vol,margin,misc,ledgers\n")
KR_LEDGER_USD_H = ("txid,refid,time,type,subtype,aclass,subclass,asset,"
                   "wallet,amount,fee,balance,amountusd\n")


def _parse_kr(files, target):
    """Write `files` {name: text} into one folder, parse `target`."""
    from taxjson.lib.brokerages.kraken import KrakenBrokerage
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = with_siblings(KrakenBrokerage()).parse_file(
                Path(td) / target)
    return txs, err.getvalue()


@rule(R.CA_CRYPTO_01)
@rule(R.US_CRYPTO_01)
class TestBondedStakingCodes(unittest.TestCase):
    """S014-01 / S061-08.

    Formerly: test_fix_m_parsers2_crypto.TestBondedStakingCodes"""

    def test_lock_period_codes_fold(self):
        # Derived from the shape <COIN><dd>.S when the coin itself is in
        # the export (no list of lock periods; owner 2026-10-04). ETH2.S
        # is no bonded code: ETH2 folds into ETH only by a ticker.map
        # GLOBAL line (features.calendars.test_no_hard_coded_security_data_in_the_flow).
        from taxjson.lib.brokerages import kraken as K
        prev = K._KNOWN_ASSETS
        K._KNOWN_ASSETS = {"DOT", "KSM", "ATOM", "SOL", "MATIC", "FLOW",
                           "ETH"}
        try:
            for raw, want in (("DOT28.S", "DOT"), ("KSM07.S", "KSM"),
                              ("ATOM21.S", "ATOM"), ("SOL03.S", "SOL"),
                              ("MATIC04.S", "MATIC"), ("FLOW14.S", "FLOW"),
                              ("DOT60.S", "DOT"), ("DOT.S", "DOT"),
                              ("ETH2.S", "ETH2")):
                self.assertEqual(K._normalize_asset(raw), want, raw)
        finally:
            K._KNOWN_ASSETS = prev

    def test_real_digit_coins_untouched(self):
        from taxjson.lib.brokerages.kraken import _normalize_asset
        for raw, want in (("0G", "0G"), ("1INCH", "1INCH"), ("C98", "C98"),
                          ("C98.S", "C98"), ("L3", "L3")):
            self.assertEqual(_normalize_asset(raw), want, raw)

    @rule(R.CA_INC_04)
    @rule(R.US_INC_02)
    def test_reward_books_to_bare_coin(self):
        # The export holds DOT itself (the bonding move from spot), so
        # DOT28.S is DOT's bonded code.
        led = KR_LEDGER_H + (
            "LB1,RB1,2025-05-01 10:00:00,transfer,spottostaking,"
            "currency,,DOT,spot,-2.0,0,0\n"
            "LS1,RS1,2025-06-01 10:00:00,staking,,"
            "currency,,DOT28.S,spot,2.0,0,2.0\n")
        txs, _ = _parse_kr({"kr_ledgers_2025.csv": led},
                           "kr_ledgers_2025.csv")
        self.assertEqual({t["symbol"] for t in txs}, {"DOT"})


@rule(R.CA_CRYPTO_01)
@rule(R.US_CRYPTO_01)
class TestEth2IsEth(unittest.TestCase):
    """R1-110 (Coinbase) / S061-11 (Kraken). taxjson ships no staked-coin
    fold (owner, 2026-10-04): the project declares it with the ticker.map
    line `GLOBAL ETH2 ETH`, which the crypto parsers apply before they
    read a row.

    Formerly: test_fix_m_parsers2_crypto.TestEth2IsEth"""

    def setUp(self):
        from taxjson.lib import markets
        self._td = tempfile.TemporaryDirectory()
        tm = Path(self._td.name) / "ticker.map"
        tm.write_text("GLOBAL ETH2 ETH\n")
        markets.use_ticker_map(tm)

    def tearDown(self):
        from taxjson.lib import markets
        markets.use_ticker_map(None)
        self._td.cleanup()

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


@rule(R.CA_CRYPTO_01)
@rule(R.US_CRYPTO_01)
class TestSwapOneValue(unittest.TestCase):
    """S013-08.

    Formerly: test_fix_m_parsers2_crypto.TestSwapOneValue"""

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
        from taxjson.lib import crypto_prices
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.json"
            inp.write_text(json.dumps({"transactions": rows}))
            saved = (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv,
                     fc.time.sleep)
            crypto_prices.CACHE_FILE = str(Path(tmp) / "cache.json")
            crypto_prices.get_crypto_price = lambda s, d: prices.get(s, 0.0)
            fc.time.sleep = lambda s: None
            sys.argv = ["fill-crypto", str(inp)]
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    fc.main()
            finally:
                (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv,
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


if __name__ == "__main__":
    unittest.main()
