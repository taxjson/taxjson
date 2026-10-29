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
"""
import contextlib
import dataclasses
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.generic import GenericBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.lib.dispatch import python_module_argv
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_WB_PRE = (",,,,,,,,\n"
           "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
           "Year / Année:,,,,,,,2025,\n"
           "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES "
           "TRANSACTIONS,\n")
_WB_H = ('"Currency\nDevise",Date,"Action Code\nCode d\'action",'
         '"Symbol\nSymbole","Security Description\nDescription des titres",'
         'Type Code of Securities Code de genre de titres,'
         '"Quantity of Securities Quantité\nde titres","Price\nPrix",'
         '"Proceeds of\nDisposition or Settlement Amount Produits de '
         'disposition"\n')
# Two newest-first downloads of one account, no row in common: March
# (a sale and a same-day rebuy after a buy) and June.
_WB_MARCH = (_WB_PRE + _WB_H
             + "CAD,05-03-2025,BUY,ZZW,ZZW CORP,SHS,10,20,(200)\n"
             "CAD,05-03-2025,SELL,ZZW,ZZW CORP,SHS,-10,30,300\n"
             "CAD,03-03-2025,BUY,ZZW,ZZW CORP,SHS,10,10,(100)\n")
_WB_JUNE = (_WB_PRE + _WB_H
            + "CAD,09-06-2025,SELL,ZZW,ZZW CORP,SHS,-10,40,400\n"
            "CAD,06-06-2025,BUY,ZZW,ZZW CORP,SHS,10,30,(300)\n")


def _webull(text, name="webull_2025.csv"):
    """The Webull parser's rows for `text`, alone in a folder of its
    own."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            return WebullBrokerage().parse_file(p)


def _ca_gain(rows):
    from taxjson.lib.core import CanadaTaxRules, TaxTransaction
    allowed = {f.name for f in dataclasses.fields(TaxTransaction)}
    txs = [TaxTransaction(**{k: v for k, v in r.items() if k in allowed})
           for r in rows]
    return CanadaTaxRules().compute_gains(
        txs, detect_wash_sales=False)["summary"]["total_gain"]


class TestWebullPastedDownloads(unittest.TestCase):
    """TJ-0376: one account's newest-first downloads pasted into one
    file were read as one block: the dates go both ways across the
    blocks, so neither was read bottom-up and a same-day sale and rebuy
    replayed rebuy first. Each download keeps its own order now."""

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_each_download_keeps_its_own_order(self):
        split = _webull(_WB_MARCH) + _webull(_WB_JUNE)
        together = _webull(_WB_MARCH + _WB_JUNE)
        self.assertEqual(
            [(t["date_settle"], t["quantity"]) for t in together],
            [(t["date_settle"], t["quantity"]) for t in split])
        march5 = [t["quantity"] for t in together
                  if t["date_settle"] == "2025-03-05"]
        self.assertEqual(march5, [-10.0, 10.0])     # the sale first
        self.assertAlmostEqual(_ca_gain(together), 350.0)
        self.assertAlmostEqual(_ca_gain(split), 350.0)

    @rule("CA-DATE-14")
    def test_one_date_download_follows_the_others(self):
        # A download whose rows are all at one date cannot show its
        # direction: it follows the file's other downloads.
        one_day = (_WB_PRE + _WB_H
                   + "CAD,12-06-2025,BUY,ZZW,ZZW CORP,SHS,5,30,(150)\n"
                   "CAD,12-06-2025,SELL,ZZW,ZZW CORP,SHS,-5,31,155\n")
        rows = _webull(_WB_MARCH + one_day)
        june12 = [t["quantity"] for t in rows
                  if t["date_settle"] == "2025-06-12"]
        self.assertEqual(june12, [-5.0, 5.0])


class TestWebullTwoAccountsInOneFile(unittest.TestCase):
    """TJ-0237: two accounts' downloads pasted into one file booked the
    second account's trades under the first account's number. The run
    stops, naming the line, with a message to split the file."""

    def test_second_account_is_refused(self):
        second = _WB_JUNE.replace("55500001", "99900002")  # pii-ok: synthetic
        with self.assertRaises(BrokerageParseError) as cm:
            _webull(_WB_MARCH + second)
        msg = str(cm.exception)
        self.assertIn("webull_2025.csv line 17:", msg)
        self.assertIn("99***", msg)
        self.assertIn("55***", msg)
        self.assertIn("Save each account's download as its own file", msg)
        self.assertNotIn("99900002", msg)  # pii-ok: synthetic id
        self.assertNotIn("55500001", msg)  # pii-ok: synthetic id

    def test_same_account_twice_is_read(self):
        rows = _webull(_WB_MARCH + _WB_JUNE)
        self.assertEqual({t.get("broker_account") for t in rows},
                         {"55500001"})  # pii-ok: synthetic id


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
    export's amountusd implies: 10 - 1 x 10 / 7.5 = 8.67 CAD."""

    @rule("CA-INC-STAKING")
    def test_cad_reward_usd_fee(self):
        rows = _kraken_reward("CAD", "7.5", "1", "1", "ZUSD", cash=True)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        self.assertEqual(div["currency"], "CAD")
        self.assertAlmostEqual(div["net_amount"], 10 - 10 / 7.5, places=6)
        self.assertAlmostEqual(div["gross_amount"], 10 - 10 / 7.5,
                               places=6)

    @rule("US-INC-STAKING")
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

    @rule("CA-INC-STAKING")
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


class TestKrakenStablecoinRewardOtherCurrencyFee(unittest.TestCase):
    """TJ-0379: in a US (property-mode) book a 10 USDC reward with a
    1 USD fee paid apart was acquired at 9 (0.90 a coin), so selling the
    10 coins at par later made a gain of 1. The coins cost their full
    par value; only the income is net of the fee."""

    @rule("US-INC-STAKING")
    @rule("US-CRYPTO-02")
    def test_usdc_reward_usd_fee_keeps_par_basis(self):
        rows = _kraken_reward("USDC", "10", "1", "1", "ZUSD", cash=False)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        (buy,) = [r for r in rows if r["action"] == "BUYSELL"]
        self.assertAlmostEqual(div["net_amount"], 9.0)
        self.assertAlmostEqual(buy["quantity"], 10.0)
        self.assertAlmostEqual(buy["net_amount"], 10.0)
        self.assertAlmostEqual(buy["price"], 1.0)

    @rule("US-INC-STAKING")
    def test_usdc_reward_same_coin_fee_still_net(self):
        # A commission withheld in the coins is netted from units,
        # income and basis alike (unchanged).
        rows = _kraken_reward("USDC", "10", "1", "1", "USDC", cash=False)
        (div,) = [r for r in rows if r["action"] == "DIVIDEND"]
        (buy,) = [r for r in rows if r["action"] == "BUYSELL"]
        self.assertAlmostEqual(div["net_amount"], 9.0)
        self.assertAlmostEqual(buy["quantity"], 9.0)
        self.assertAlmostEqual(buy["net_amount"], 9.0)


_CB_HEAD = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
            "Price Currency,Price at Transaction,Subtotal,"
            "Fees and/or Spread,Total (inclusive of fees and/or spread)\n")


def _coinbase(body, name="coinbase.csv"):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(_CB_HEAD + body, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            return CoinbaseBrokerage().parse_file(p)


class TestCoinbaseNegativeReward(unittest.TestCase):
    """TJ-0383: a Staking Income row of -2 ETH (Subtotal -20) was booked
    as income of +20 and 2 coins bought: every amount was taken as a
    magnitude. A negative reward is refused, naming the row, as Kraken
    refuses one."""

    def test_negative_reward_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _coinbase("SYN1,2025-01-15 10:00:00 UTC,Staking Income,ZZE,"
                      "-2,CAD,10,-20,0,-20\n")
        msg = str(cm.exception)
        self.assertIn("coinbase.csv line 2", msg)
        self.assertIn("negative Quantity Transacted -2, Subtotal -20, "
                      "Total -20", msg)
        self.assertIn("reward reversal is not modeled", msg)

    def test_negative_subtotal_alone_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _coinbase("SYN1,2025-01-15 10:00:00 UTC,Staking Income,ZZE,"
                      "2,CAD,10,-20,0,20\n")
        self.assertIn("negative Subtotal -20", str(cm.exception))

    def test_positive_reward_still_booked(self):
        rows = _coinbase("SYN1,2025-01-15 10:00:00 UTC,Staking Income,ZZE,"
                         "2,CAD,10,20,0,20\n")
        self.assertEqual(sorted((r["action"], r["quantity"], r["net_amount"])
                                for r in rows),
                         [("BUYSELL", 2.0, 20.0), ("DIVIDEND", 2.0, 20.0)])


class TestCoinbaseCustodyIds(unittest.TestCase):
    """TJ-0391: a Coinbase Send/Receive row's TRANSFER carried no ID,
    so two exports' distinct sends alike in every other cell (IDs SYNA
    and SYNB, the same second and quantity) were read as one download's
    overlap: the custody sidecar kept one. The ID is kept now, so the
    two stay two, while one export saved twice is still read once."""

    _H = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
          "Price Currency,Price at Transaction,Fees and/or Spread,"
          "Total (inclusive of fees and/or spread)\n")

    def _send(self, cid):
        return (self._H + f"{cid},2025-07-01 12:00:00 UTC,Send,ZZE,1,CAD,"
                f"100,0,100\n")

    def test_transfer_rows_carry_the_id(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "coinbase_a.csv"
            p.write_text(self._send("SYNA") + "SYNR,2025-07-02 12:00:00 "
                         "UTC,Receive,ZZE,2,CAD,100,0,200\n")
            with contextlib.redirect_stderr(io.StringIO()):
                rows = CoinbaseBrokerage().parse_file(p)
        self.assertEqual([(r["action"], r["quantity"], r.get("id"))
                          for r in rows],
                         [("TRANSFER", -1.0, "SYNA-xfer"),
                          ("TRANSFER", 2.0, "SYNR-xfer")])

    def _sidecar(self, files):
        with tempfile.TemporaryDirectory() as td:
            for name, text in files.items():
                (Path(td) / name).write_text(text)
            side = Path(td) / "sidecar.json"
            env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
            r = subprocess.run(
                python_module_argv(
                    "taxjson.bin.taxjson_brokerage",
                    ["--brokerage", "coinbase", "--country", "canada",
                     "--account", "crypto", "--transfers-out", str(side),
                     *[str(Path(td) / n) for n in files]]),
                cwd=REPO_ROOT, capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(side.read_text())["transactions"]

    def test_two_exports_distinct_sends_stay_two(self):
        rows = self._sidecar({"coinbase_a.csv": self._send("SYNA"),
                              "coinbase_b.csv": self._send("SYNB")})
        self.assertEqual(sorted(r.get("id") for r in rows),
                         ["SYNA-xfer", "SYNB-xfer"])

    def test_one_export_saved_twice_is_read_once(self):
        rows = self._sidecar({"coinbase_a.csv": self._send("SYNA"),
                              "coinbase_a2.csv": self._send("SYNA")})
        self.assertEqual([r.get("id") for r in rows], ["SYNA-xfer"])


_GEN_TOML = """\
[columns]
date = "Date"
action = "Transaction type"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"

[formats]
date = "%m/%d/%Y"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"""
_GEN_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
            "01/15/2025,BUY,ZZG,10,10.00,100.00,CAD\n")


def _generic(csv_text, toml_text):
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_test.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(toml_text)
        with contextlib.redirect_stderr(io.StringIO()):
            return GenericBrokerage().parse_file(c)


class TestGenericActionKeysIgnoreCase(unittest.TestCase):
    """TJ-0382: the importer matches a row's action ignoring case, so
    [actions] "BUY" = "buy" and "Buy" = "sell" are one label with two
    meanings: the last one silently won and a purchase of 10 was booked
    as a sale. Such a mapping is refused; keys alike but for case or
    spaces that agree still read."""

    def test_contradictory_keys_are_refused(self):
        with self.assertRaises(ValueError) as cm:
            _generic(_GEN_CSV, _GEN_TOML + '"Buy" = "sell"\n')
        msg = str(cm.exception)
        self.assertIn("generic_test.csv.toml", msg)
        self.assertIn("'BUY'", msg)
        self.assertIn("'Buy'", msg)
        self.assertIn("ignoring case", msg)

    def test_keys_with_spaces_are_one_label(self):
        with self.assertRaises(ValueError):
            _generic(_GEN_CSV, _GEN_TOML + '" buy " = "sell"\n')

    def test_agreeing_duplicates_and_padded_keys_read(self):
        toml_text = _GEN_TOML.replace('"BUY" = "buy"',
                                      '" Buy " = "buy"\n"BUY" = "buy"')
        rows = _generic(_GEN_CSV, toml_text)
        self.assertEqual([(r["action"], r["quantity"]) for r in rows],
                         [("BUYSELL", 10.0)])


if __name__ == "__main__":
    unittest.main()
