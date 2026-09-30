"""Regression tests for the 2026-09 audit's crypto findings.

R1-102  Coinbase Advanced Trade on a crypto-quoted pair (ETH-BTC) dropped
        the quote-coin disposition.
R1-103  Coinbase Buy/Sell with a blank Total booked $0 cost / proceeds.
R1-104  Kraken ledger trades missing from the trades export were dropped
        with the same note a complete run prints.
R1-105  fill-crypto price fetch failure left price 0 ($0 income / cost)
S000-00 and the run exited 0 with validation OK; a null/empty Yahoo close
        was quieter still (no warning at all).
R1-106  A project-root crypto_ticker.map was ignored unless cwd was the
        project root (and a map in cwd leaked into other projects).

All data is synthetic.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent

CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")

KR_TRADES_H = ("txid,ordertxid,pair,aclass,subclass,time,type,ordertype,"
               "price,cost,fee,vol,margin,misc,ledgers\n")
KR_LEDGER_H = "txid,refid,time,type,subtype,aclass,subclass,asset,wallet," \
              "amount,fee,balance\n"


def _cb_row(cid, ts, typ, asset, qty, ccy, price, sub, total, fee, notes=""):
    return (f'{cid},{ts},{typ},{asset},{qty},{ccy},"{price}","{sub}",'
            f'"{total}","{fee}","{notes}"\n')


def _parse_cb(text):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cb_2026.csv"
        p.write_text(CB_HEADER + text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = CoinbaseBrokerage().parse_file(p)
    return txs, err.getvalue()


def _bs(txs):
    return [t for t in txs if t["action"] == "BUYSELL"]


def _run_cli(root, *args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=str(cwd or REPO_ROOT), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=env)


def _project(td, name="crypto", source_currencies=("USD",)):
    """A 2026 crypto-only CA project. With USD configured, a fresh
    to_base.csv is pre-seeded (flat 1.40) so no FX download happens."""
    root = Path(td) / "proj"
    (root / "inputs" / name).mkdir(parents=True)
    (root / "work").mkdir()
    sc = ", ".join(f'"{c}"' for c in source_currencies)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\nprovince = "ON"\n'
        f'base_currency = "CAD"\nsource_currencies = [{sc}]\n'
        f'[accounts.{name}]\ntype = "taxable"\ncrypto = true\n')
    if source_currencies:
        d, lines = date(2025, 12, 1), []
        while d <= date.today():
            lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.40 yahoo\n")
            d += timedelta(days=1)
        (root / "work" / "to_base.csv").write_text("".join(lines))
    return root


def _env(home, **extra):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("TAXJSON_OFFLINE", None)
    env.update(extra)
    return env


# ---------------------------------------------------------------- R1-102
class TestCoinbaseCryptoQuotedAdvancedTrade(unittest.TestCase):
    BUY = _cb_row("a2", "2026-03-02 15:00:00 UTC", "Advanced Trade Buy",
                  "ETH", "3", "CAD", "$4000.00", "$12000.00", "$12030.00",
                  "$30.00",
                  "Bought 3 ETH for 0.1 BTC on ETH-BTC at 0.0333 BTC/ETH")

    def test_buy_books_the_quote_coin_disposition(self):
        txs, _ = _parse_cb(self.BUY)
        legs = {t["symbol"]: t for t in _bs(txs)}
        self.assertEqual(set(legs), {"ETH", "BTC"})
        self.assertAlmostEqual(legs["ETH"]["quantity"], 3.0)
        self.assertAlmostEqual(legs["ETH"]["net_amount"], 12030.0)
        self.assertAlmostEqual(legs["BTC"]["quantity"], -0.1)
        # The 0.1 BTC spent (fee included) is worth the row's Total; the
        # fee lands once, capitalised into the ETH leg.
        self.assertAlmostEqual(legs["BTC"]["net_amount"], 12030.0)
        self.assertEqual(legs["BTC"]["currency"], "CAD")
        self.assertEqual(legs["BTC"]["id"], "a2-quote")
        self.assertEqual(legs["ETH"]["id"], "a2")

    def test_sell_books_the_quote_coin_acquisition(self):
        txs, _ = _parse_cb(_cb_row(
            "a3", "2026-03-03 15:00:00 UTC", "Advanced Trade Sell", "ETH",
            "-3", "CAD", "$4000.00", "-$12000.00", "-$11970.00", "$30.00",
            "Sold 3 ETH for 0.0997 BTC on ETH-BTC at 0.0333 BTC/ETH"))
        legs = {t["symbol"]: t for t in _bs(txs)}
        self.assertAlmostEqual(legs["ETH"]["quantity"], -3.0)
        self.assertAlmostEqual(legs["ETH"]["net_amount"], 11970.0)
        self.assertAlmostEqual(legs["BTC"]["quantity"], 0.0997)
        self.assertAlmostEqual(legs["BTC"]["net_amount"], 11970.0)

    def test_crypto_price_currency_ships_two_legs_for_fill(self):
        # Price Currency = BTC (audit case C21): no fiat value on the row,
        # so both legs go to taxjson-fill-crypto at price 0 (as Kraken's
        # crypto/crypto path does) instead of booking ETH "in BTC".
        txs, _ = _parse_cb(_cb_row(
            "a4", "2025-06-30 18:32:27 UTC", "Advanced Trade Buy", "ETH",
            "1", "BTC", "0.025", "0.025", "0.02505", "0.00005",
            "Bought 1 ETH for 0.02505 BTC on ETH-BTC"))
        legs = {t["symbol"]: t for t in _bs(txs)}
        self.assertEqual(set(legs), {"ETH", "BTC"})
        for leg in legs.values():
            self.assertEqual(leg["currency"], "USD")
            self.assertEqual(leg["price"], 0.0)
            self.assertEqual(leg["net_amount"], 0.0)
        self.assertAlmostEqual(legs["BTC"]["quantity"], -0.02505)

    def test_usdc_quote_unchanged(self):
        txs, _ = _parse_cb(_cb_row(
            "a5", "2025-06-30 18:32:27 UTC", "Advanced Trade Buy", "ETH",
            "1", "CAD", "$3400", "$3400", "$3405", "$5",
            "Bought 1 ETH for 2490 USDC on ETH-USDC at 2485 USDC/ETH"))
        self.assertEqual([(t["symbol"], t["net_amount"]) for t in _bs(txs)],
                         [("ETH", 3405.0)])

    def test_unreadable_crypto_pair_notes_refuse(self):
        with self.assertRaises(ValueError) as cm:
            _parse_cb(_cb_row(
                "a6", "2026-03-02 15:00:00 UTC", "Advanced Trade Buy", "ETH",
                "3", "CAD", "$4000", "$12000", "$12030", "$30",
                "Filled on ETH-BTC"))
        self.assertIn("ETH-BTC", str(cm.exception))

    def test_asset_not_the_pair_base_refuses(self):
        with self.assertRaises(ValueError):
            _parse_cb(_cb_row(
                "a7", "2026-03-02 15:00:00 UTC", "Advanced Trade Buy", "SOL",
                "3", "CAD", "$4000", "$12000", "$12030", "$30",
                "Bought 3 ETH for 0.1 BTC on ETH-BTC"))

    def test_stablecoin_base_on_crypto_quote_refuses(self):
        with self.assertRaises(ValueError):
            _parse_cb(_cb_row(
                "a8", "2026-03-02 15:00:00 UTC", "Advanced Trade Buy",
                "USDC", "100", "CAD", "$1.37", "$137", "$137.20", "$0.20",
                "Bought 100 USDC for 0.0014 BTC on USDC-BTC"))

    def test_end_to_end_btc_disposition_is_realized(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, source_currencies=())
            (root / "inputs" / "crypto" / "cb_2026.csv").write_text(
                CB_HEADER
                + _cb_row("a1", "2026-02-02 15:00:00 UTC", "Buy", "BTC",
                          "0.1", "CAD", "$100000", "$10000", "$10100",
                          "$100")
                + self.BUY)
            r = _run_cli(root, "run", "--no-input",
                         env=_env(Path(td), TAXJSON_OFFLINE="1"))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            gains = json.loads(
                (root / "work" / "crypto_gains.json").read_text())
        rows = [t for t in gains["transactions"] if t.get("symbol") == "BTC"]
        self.assertEqual(len(rows), 1, gains["transactions"])
        self.assertAlmostEqual(rows[0]["gain"], 1930.0, places=2)


# ---------------------------------------------------------------- R1-103
class TestCoinbaseBlankTotal(unittest.TestCase):
    def test_blank_total_buy_derived_from_subtotal_plus_fee(self):
        txs, err = _parse_cb(_cb_row(
            "b1", "2026-02-02 15:00:00 UTC", "Buy", "BTC", "0.1", "CAD",
            "$100000", "$10000", "", "$100"))
        self.assertAlmostEqual(_bs(txs)[0]["net_amount"], 10100.0)
        self.assertIn("blank Total", err)

    def test_blank_total_sell_derived_from_subtotal_minus_fee(self):
        txs, _ = _parse_cb(_cb_row(
            "b2", "2026-02-03 15:00:00 UTC", "Sell", "BTC", "-0.1", "CAD",
            "$110000", "$11000", "", "$100"))
        self.assertAlmostEqual(_bs(txs)[0]["net_amount"], 10900.0)

    def test_blank_total_and_subtotal_uses_qty_times_price(self):
        txs, _ = _parse_cb(_cb_row(
            "b3", "2026-02-02 15:00:00 UTC", "Advanced Trade Buy", "BTC",
            "0.1", "CAD", "$100000", "", "", "$100"))
        self.assertAlmostEqual(_bs(txs)[0]["net_amount"], 10100.0)

    def test_nothing_to_derive_from_refuses_naming_the_row(self):
        with self.assertRaises(ValueError) as cm:
            _parse_cb(_cb_row(
                "b4", "2026-02-02 15:00:00 UTC", "Buy", "BTC", "0.1", "CAD",
                "", "", "", "$100"))
        self.assertIn("2026-02-02 15:00:00 UTC", str(cm.exception))
        self.assertIn("Total", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
