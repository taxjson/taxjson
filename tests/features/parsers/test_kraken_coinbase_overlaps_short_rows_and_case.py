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
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from _siblings import with_siblings
from _support.crypto_rows import CB_HEADER, KR_LEDGER_H, _cb_row, _parse_cb, _bs


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

KR_TRADES_H = ("txid,ordertxid,pair,aclass,subclass,time,type,ordertype,"
               "price,cost,fee,vol,margin,misc,ledgers\n")


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


# A SOL/USD buy fill and its two ledger legs.
_TRADE = ("TB1,OB1,SOL/USD,forex,,2025-06-01 14:00:00.1,buy,limit,"
          "150.0,1500.0,3.0,10.0,0,,\n")
_LEG_SOL = ("LS1,TB1,2025-06-01 14:00:00.1,trade,,currency,,SOL,spot,"
            "10.0,0,10.0\n")
_LEG_USD = ("LU1,TB1,2025-06-01 14:00:00.1,trade,,currency,,USD,spot,"
            "-1500.0,3.0,500.0\n")


class TestKrakenLedgerOverlap(unittest.TestCase):
    """R1-108 / R1-299: two ledger exports that repeat the same rows.

    Formerly: test_fix_m_parsers2_crypto.TestKrakenLedgerOverlap"""

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
    """S013-09: repeated rows inside one ledger file.

    Formerly: test_fix_m_parsers2_crypto.TestKrakenRepeatedLedgerRows"""

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
    """R1-107 / S061-23.

    Formerly: test_fix_m_parsers2_crypto.TestKrakenUnbookedIsLoud"""

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
    """S061-12.

    Formerly: test_fix_m_parsers2_crypto.TestKrakenShortRows"""

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
    """R1-112.

    Formerly: test_fix_m_parsers2_crypto.TestSymbolCase"""

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


class TestCoinbaseLayout(unittest.TestCase):
    """R1-111 / R1-109 / S056-06.

    Formerly: test_fix_m_parsers2_crypto.TestCoinbaseLayout"""

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


class TestZeroCloseIsLoud(unittest.TestCase):
    """S025-09: a 0.0 close is a failed lookup (already fixed by S000-00).

    Formerly: test_fix_m_parsers2_crypto.TestZeroCloseIsLoud"""

    def test_zero_close_warns(self):
        from taxjson.lib import crypto_prices as fcp

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
