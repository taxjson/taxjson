"""Fix round 7 (wave C), group "crypto": saved crypto-send prices keep
their currency, the US gift advice, Coinbase fees and blank cells
during price filling, Kraken's unpriced swap fee (backlog TJ-0927,
TJ-0928, TJ-0981, TJ-0988, TJ-0992, TJ-0991).

All rows, ids and amounts are synthetic.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from _qa_project import tj

from taxjson.bin import fill_crypto_prices as FC
from taxjson.lib.core import load_transactions
from taxjson.lib.dispatch import python_module_argv


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")


def _project(td, rows, country="canada", year=2025):
    root = Path(td) / "p"
    (root / "inputs" / "coin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    src = '[]' if country == "canada" else '["CAD"]'
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{base}"\n'
        f'source_currencies = {src}\n'
        '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
    (root / "ticker.map").write_text("")
    (root / "inputs" / "coin" / "coinbase.csv").write_text(_HDR + rows)
    return root


class TestSendPriceKeepsCurrency(unittest.TestCase):
    """TJ-0927: a --price saved in a Canadian (CAD) project and reused
    after the project's base became USD is converted at the CAD/USD rate,
    never read as USD."""

    ROWS = ("BUY111,2025-01-02 17:00:00 UTC,Buy,SOL,1,CAD,50,50,50,0,"
            "Bought one coin\n"
            "SEND111,2025-03-03 17:00:00 UTC,Send,SOL,1,CAD,100,100,100,0,"
            "Sent one coin\n")

    def test_saved_price_converts_to_the_new_base(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, self.ROWS)
            tj(root, "run", "--no-input")
            doc = json.loads(tj(root, "crypto-sends", "--json").stdout)
            sid = doc["accounts"]["coin"]["sends"][0]["id"]
            tj(root, "crypto-sends", "coin", "--set", f"{sid}=payment",
               "--price", "100")
            man = json.loads((root / "inputs/coin/sends.json").read_text())
            self.assertEqual(man["sends"][sid]["price_currency"], "CAD")
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text()
                           .replace('country = "canada"', 'country = "usa"')
                           .replace('base_currency = "CAD"',
                                    'base_currency = "USD"')
                           .replace("source_currencies = []",
                                    'source_currencies = ["CAD"]'))
            (root / "work").mkdir(exist_ok=True)
            (root / "work" / "to_base.csv").write_text("".join(
                f"{d} 12:00:00 CAD USD 0.5 synthetic\n"
                for d in ("2025-01-02", "2025-03-03",
                          str(date.today()))))
            doc = json.loads(tj(root, "crypto-sends", "--json").stdout)
            fv = doc["accounts"]["coin"]["sends"][0]["fair_value"]
            self.assertEqual(fv["currency"], "USD")
            self.assertAlmostEqual(fv["value"], 50.0)
            self.assertIn("100 CAD", fv["source"])
            tj(root, "run", "--no-input")
            tt = (root / "inputs/coin/crypto_sends.tt").read_text()
            self.assertIn("SOL -1 USD 50 50.00", tt)

    def test_a_price_in_the_base_is_unchanged(self):
        from taxjson.lib import crypto_sends as CS
        rates = CS.Rates({"USD": {"2025-03-03": (1.25, "synthetic")}},
                         "CAD")
        send = {"symbol": "SOL", "date": "2025-03-03", "quantity": -2.0,
                "exchange": "coinbase", "price": 0.0, "currency": "CAD"}
        fv = CS.fair_value(send, rates, None, 40.0, manual_currency="CAD")
        self.assertAlmostEqual(fv["value"], 80.0)
        fv = CS.fair_value(send, rates, None, 40.0, manual_currency="USD")
        self.assertAlmostEqual(fv["value"], 100.0)
        # No rate for the saved currency: unpriced, never read as CAD.
        self.assertIsNone(CS.fair_value(send, rates, None, 40.0,
                                        manual_currency="EUR"))
        # An older decision without its currency: read as the base.
        fv = CS.fair_value(send, rates, None, 40.0)
        self.assertAlmostEqual(fv["value"], 80.0)


def _brokerage(td, name, broker, text):
    """`taxjson-brokerage --strict` on one synthetic file: (rc, the
    rows' JSON path, stderr)."""
    d = Path(td) / name
    d.mkdir()
    src = d / ("coinbase.csv" if broker == "coinbase" else "ledgers.csv")
    src.write_text(text)
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_brokerage",
                           ["--brokerage", broker, "--country", "canada",
                            "--strict", str(src)]),
        capture_output=True, text=True,
        env=dict(os.environ, TAXJSON_WIDTH="0",
                 TAXJSON_LOCAL_TZ="America/Toronto"))
    out = d / "parsed.json"
    out.write_text(r.stdout)
    return r.returncode, out, r.stderr


def _fill(path, cache):
    """fill-crypto's _fill on a parsed file with a fixed cache and no
    network: the filled rows."""
    out = io.StringIO()
    with patch.object(FC, "load_cache", return_value=dict(cache)), \
            patch.object(FC, "save_cache"), \
            patch.object(FC, "get_crypto_price",
                         side_effect=AssertionError("network")), \
            redirect_stdout(out), redirect_stderr(io.StringIO()):
        FC._fill(Namespace(input=str(path)))
    return json.loads(out.getvalue())["transactions"]


class TestCoinbaseQuoteCoinFee(unittest.TestCase):
    """TJ-0981: an Advanced Trade buy on a crypto-quoted pair keeps its
    fee (in the quote coin) through price filling: both legs carry the
    received coin's value plus the fee, as a fiat-priced row's Total."""

    CACHE = {"SYN-2025-06-02": 30, "ZZQ-2025-06-02": 100}

    def _legs(self, td, name, numbers, verb, spent):
        row = (f"SYN-SWAP-1,2025-06-02 14:00:00 UTC,Advanced Trade "
               f"{'Sell' if verb == 'Sold' else 'Buy'},SYN,10,{numbers},"
               f"{verb} 10 SYN for {spent} ZZQ on SYN-ZZQ\n")
        rc, path, err = _brokerage(td, name, "coinbase", _HDR + row)
        self.assertEqual(rc, 0, err)
        return {r["symbol"]: r for r in _fill(path, self.CACHE)}

    def test_buy_fee_in_the_quote_coin_is_capitalised(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "fee", "ZZQ,0.3,3,3.03,0.03", "Bought",
                              "3.03")
            self.assertAlmostEqual(legs["SYN"]["net_amount"], 303.0)
            self.assertAlmostEqual(legs["ZZQ"]["net_amount"], 303.0)
            self.assertAlmostEqual(legs["ZZQ"]["quantity"], -3.03)
            legs = self._legs(td, "nofee", "ZZQ,0.3,3,3,0", "Bought", "3")
            self.assertAlmostEqual(legs["SYN"]["net_amount"], 300.0)
            self.assertNotIn("swap_fee", legs["SYN"])

    def test_sell_is_net_of_its_fee_as_before(self):
        # A sell's Notes amount is net of the fee, as its fiat Total:
        # the received units' value is the swap's value.
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "sell", "ZZQ,0.3,3,2.97,0.03", "Sold",
                              "2.97")
            self.assertAlmostEqual(legs["ZZQ"]["net_amount"], 297.0)
            self.assertAlmostEqual(legs["SYN"]["net_amount"], 297.0)

    def test_swap_fee_factor(self):
        from taxjson.lib.core import TaxTransaction as T
        recv = T(action="BUYSELL", date="2025-06-02", symbol="SYN",
                 quantity=10, swap_fee="0.5 received")
        spent = T(action="BUYSELL", date="2025-06-02", symbol="ZZQ",
                  quantity=-3)
        self.assertAlmostEqual(FC._swap_fee_factor(recv, spent), 1.05)
        recv.swap_fee = "1 spent"
        self.assertAlmostEqual(FC._swap_fee_factor(recv, spent), 1.5)
        for bad in ("", "3 spent", "x spent", "1 ZZQ", "-1 received",
                    "nan received", "1 received extra"):
            recv.swap_fee = bad
            self.assertEqual(FC._swap_fee_factor(recv, spent), 1.0, bad)
        # The field survives a write and a read (an evidence field).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rows.json"
            recv.swap_fee = "0.5 received"
            p.write_text(json.dumps({"transactions": [recv.to_dict()]}))
            self.assertEqual(load_transactions(str(p))[0].swap_fee,
                             "0.5 received")


class TestCoinbaseBlankTotalChecksSubtotal(unittest.TestCase):
    """TJ-0992: a Buy/Sell row with a blank Total has its Subtotal
    checked against |Quantity| x Price, as a row with the Total filled
    in is: a contradiction is refused, not booked."""

    def _parse(self, td, name, subtotal, total):
        row = (f"SYN-ROW-1,2025-06-02 14:00:00 UTC,Buy,SYN,10,CAD,10,"
               f"{subtotal},{total},0,Bought 10 SYN\n")
        return _brokerage(td, name, "coinbase", _HDR + row)

    def test_contradiction_is_refused_with_or_without_the_total(self):
        with tempfile.TemporaryDirectory() as td:
            for name, total in (("blank", ""), ("given", "50")):
                rc, _path, err = self._parse(td, name, 50, total)
                self.assertNotEqual(rc, 0, name)
                self.assertIn("Subtotal 50.00 does not fit", err, name)
            rc, path, err = self._parse(td, "fits", 100, "")
            self.assertEqual(rc, 0, err)
            row = json.loads(path.read_text())["transactions"][0]
            self.assertAlmostEqual(row["net_amount"], 100.0)


class TestCoinbaseZeroNetSaleKept(unittest.TestCase):
    """TJ-0988: a sale whose fee took the whole Subtotal (Total 0) keeps
    its stated 0 proceeds when its Price cell is blank: the price and
    the gross come from the Subtotal, and fill-crypto leaves the row."""

    def _filled(self, td, name, price):
        row = (f"SYN-DUST-1,2025-06-02 14:00:00 UTC,Sell,SYN,1,CAD,"
               f"{price},1,0,1,Sold 1 SYN for 0 CAD\n")
        rc, path, err = _brokerage(td, name, "coinbase", _HDR + row)
        self.assertEqual(rc, 0, err)
        return _fill(path, {"SYN-2025-06-02": 1})[0]

    def test_blank_price_keeps_zero_proceeds(self):
        with tempfile.TemporaryDirectory() as td:
            for name, price in (("blank", ""), ("given", "1")):
                row = self._filled(td, name, price)
                self.assertEqual(row["currency"], "CAD", name)
                self.assertAlmostEqual(row["net_amount"], 0.0, msg=name)
                self.assertAlmostEqual(row["gross_amount"], 1.0, msg=name)
                self.assertAlmostEqual(row["price"], 1.0, msg=name)
                self.assertAlmostEqual(row["fee"], 1.0, msg=name)


class TestKrakenReceiveFeeSwap(unittest.TestCase):
    """TJ-0991: an unpriced Kraken ledger swap with a receive-side fee is
    valued at the gross received units, as its gross amountusd values
    it: 10 SYN for 20 ZZQ less a 0.2 ZZQ fee, ZZQ at 5, is 100 on both
    legs, not the 99 of the 19.8 that arrived."""

    CACHE = {"SYN-2025-06-02": 10, "ZZQ-2025-06-02": 5}
    HDR = ["txid", "refid", "time", "type", "subtype", "aclass", "asset",
           "amount", "fee", "balance"]

    def _legs(self, td, name, recv_fee, spend_fee=0.0, usd=False):
        hdr = self.HDR + (["amountusd", "feeusd"] if usd else [])
        rows = [["SYN-SPEND", "SYN-SWAP", "2025-06-02 14:00:00", "spend",
                 "", "currency", "SYN", -10, spend_fee, 0]
                + ([100, 0] if usd else []),
                ["SYN-RECEIVE", "SYN-SWAP", "2025-06-02 14:00:00",
                 "receive", "", "currency", "ZZQ", 20, recv_fee,
                 20 - recv_fee] + ([100, 1] if usd else [])]
        text = "".join(",".join(str(c) for c in r) + "\n"
                       for r in [hdr, *rows])
        rc, path, err = _brokerage(td, name, "kraken", text)
        self.assertEqual(rc, 0, err)
        return {r["symbol"]: r for r in _fill(path, self.CACHE)}

    def test_receive_fee_is_valued_with_the_received_units(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "unpriced", 0.2)
            self.assertAlmostEqual(legs["ZZQ"]["quantity"], 19.8)
            self.assertAlmostEqual(legs["ZZQ"]["net_amount"], 100.0)
            self.assertAlmostEqual(legs["SYN"]["net_amount"], 100.0)
            valued = self._legs(td, "valued", 0.2, usd=True)
            self.assertAlmostEqual(valued["ZZQ"]["net_amount"], 100.0)
            self.assertAlmostEqual(valued["SYN"]["net_amount"], 100.0)
            legs = self._legs(td, "nofee", 0.0)
            self.assertAlmostEqual(legs["ZZQ"]["net_amount"], 100.0)
            self.assertNotIn("swap_fee", legs["ZZQ"])

    def test_spend_fee_keeps_the_received_value(self):
        # amountusd's received value leaves a spend-side fee out: the
        # unpriced path agrees.
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "spendfee", 0.0, spend_fee=0.1)
            self.assertAlmostEqual(legs["SYN"]["quantity"], -10.1)
            self.assertAlmostEqual(legs["ZZQ"]["net_amount"], 100.0)
            self.assertAlmostEqual(legs["SYN"]["net_amount"], 100.0)


if __name__ == "__main__":
    unittest.main()
