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

Formerly: test_fix_1029_parsers
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


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


class TestWebullTwoAccountsInOneFile(unittest.TestCase):
    """TJ-0237: two accounts' downloads pasted into one file booked the
    second account's trades under the first account's number. The run
    stops, naming the line, with a message to split the file.

    Formerly: test_fix_1029_parsers.TestWebullTwoAccountsInOneFile"""

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
    refuses one.

    Formerly: test_fix_1029_parsers.TestCoinbaseNegativeReward"""

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
    two stay two, while one export saved twice is still read once.

    Formerly: test_fix_1029_parsers.TestCoinbaseCustodyIds"""

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


if __name__ == "__main__":
    unittest.main()
