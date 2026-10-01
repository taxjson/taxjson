"""Owner-decision fixes (crypto / DRIP area):

* R1-26: a send whose matched arrival is short (a Coinbase Send carries
  its network fee inside the sent quantity) books the shortfall as a
  disposition at fair value, in both countries — the way the Kraken
  withdrawal fee paid in a coin is booked.
* S060-24: in a Canada project PYUSD and GUSD are US-dollar cash on
  Kraken too (as on Coinbase, like USDC/USDT/DAI); a US project keeps
  every stablecoin as property.
* S065-12: a notional mismatch on a row whose parser does not declare a
  contract multiplier (a DRIP priced in another currency, a 10x typo)
  is echoed to the console as ATTENTION, not left in the .sum only.

Synthetic data only.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


# ----------------------------------------------------------- S060-24
_KR_TRADES_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
                "margin,misc,ledgers\n")
_KR_LEDGER_H = ('"txid","refid","time","type","subtype","aclass","asset",'
                '"wallet","amount","fee","balance"\n')


def _kraken(text: str, prefix: str, cash: bool):
    from taxjson.lib.brokerages.kraken import KrakenBrokerage

    class K(KrakenBrokerage):
        stablecoins_as_cash = cash
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{prefix}.csv"
        p.write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return K().parse_file(p)


_PYUSD_TRADES = _KR_TRADES_H + (
    "T1,O1,PYUSD/USD,2025-03-01 12:00:00.1,buy,limit,1.00,1000,0,1000,,,\n"
    "T2,O2,ETH/PYUSD,2025-03-02 12:00:00.1,buy,limit,2000,1000,0,0.5,,,\n"
    "T3,O3,GUSD/USD,2025-03-03 12:00:00.1,buy,limit,1.00,50,0,50,,,\n")
_PYUSD_REWARD = _KR_LEDGER_H + (
    '"L1","R1","2025-04-01 10:00:00","earn","reward","currency",'
    '"PYUSD","earn","10","0","10"\n')


class TestKrakenPyusdGusd(unittest.TestCase):

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    def test_canada_cash_us_property(self):
        ca = _kraken(_PYUSD_TRADES, "kr_trades", cash=True)
        # Canada: no PYUSD/GUSD position at all; ETH bought for dollars.
        self.assertEqual([t for t in ca if t["symbol"] in ("PYUSD", "GUSD")],
                         [])
        eth = [t for t in ca if t["symbol"] == "ETH"]
        self.assertEqual(len(eth), 1)
        self.assertAlmostEqual(eth[0]["net_amount"], 1000.0)
        self.assertEqual(eth[0]["currency"], "USD")
        # US: unchanged — PYUSD and GUSD are coins (property).
        us = _kraken(_PYUSD_TRADES, "kr_trades", cash=False)
        self.assertEqual(sorted({t["symbol"] for t in us
                                 if t["action"] == "BUYSELL"}),
                         ["ETH", "GUSD", "PYUSD"])

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    def test_reward_is_dollar_income_in_canada_coin_in_us(self):
        ca = _kraken(_PYUSD_REWARD, "kr_ledgers", cash=True)
        self.assertEqual([(t["action"], t["price"], t["net_amount"])
                          for t in ca], [("DIVIDEND", 1.0, 10.0)])
        us = _kraken(_PYUSD_REWARD, "kr_ledgers", cash=False)
        self.assertEqual(sorted((t["action"], t["symbol"]) for t in us),
                         [("BUYSELL", "PYUSD"), ("DIVIDEND", "PYUSD")])

    @rule("CA-CRYPTO-08")
    def test_kraken_pyusd_send_is_stablecoin_cash(self):
        from taxjson.lib import crypto_sends as cs
        for ex in ("kraken", "coinbase"):
            for sym in ("PYUSD", "GUSD", "USDC"):
                self.assertTrue(cs.is_cash_stablecoin(sym, ex), (sym, ex))
        self.assertFalse(cs.is_cash_stablecoin("ETH", "kraken"))


# ----------------------------------------------------------- S065-12
_QT_H = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
         'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
         'Account #,Activity Type,Account Type\n')


class TestNotionalMismatchIsAttention(unittest.TestCase):
    """S065-12: a DRIP priced in US dollars on a CAD row (REINV@U$) is a
    notional mismatch the parser cannot resolve — echoed as ATTENTION."""

    def test_schema_tags_undeclared_mismatch_only(self):
        from taxjson.lib.brokerages.schema import (ATTENTION_TAG,
                                                   validate_transactions)
        row = {"action": "BUYSELL", "date": "2025-06-02", "symbol": "ZZQ.TO",
               "currency": "CAD", "quantity": 10, "price": 7.0,
               "net_amount": 105.0}
        errs, warns = validate_transactions([row])
        self.assertEqual(errs, [])
        self.assertEqual(len(warns), 1)
        self.assertTrue(warns[0].startswith(ATTENTION_TAG), warns[0])
        # A declared multiplier keeps its ERROR.
        errs, warns = validate_transactions([dict(row, multiplier=1)])
        self.assertEqual(len(errs), 1)
        self.assertFalse(errs[0].startswith(ATTENTION_TAG))

    def test_drip_in_another_currency_reaches_the_console(self):
        from taxjson.bin.taxjson_run import echo_parse_stats
        csv = _QT_H + (
            "2026-05-11 12:00:00 AM,2026-05-11 12:00:00 AM,REI,ZZQ.TO,"
            "ZZQ CORP REINV@U$7.00000 REC 04/30/26 PAY 05/11/26,11,0,0,0,"
            "-105.00,CAD,55500001,Dividend reinvestment,Individual margin\n")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "questrade_activity.csv"
            src.write_text(csv)
            out = Path(td) / "qt.json"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "questrade", "--strict", str(src)],
                capture_output=True, text=True,
                env=dict(os.environ, PYTHONPATH=str(SRC)))
            self.assertEqual(r.returncode, 0, r.stderr)
            out.write_text(r.stdout)
            self.assertIn("warning: ATTENTION: schema:", r.stderr)
            self.assertIn("ZZQ.TO", r.stderr)
            (Path(td) / "qt.json.diag").write_text(r.stderr)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                echo_parse_stats(out)
            self.assertIn("ATTENTION: schema:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
