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
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def tearDownModule():
    _WIDTH.stop()


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    _WIDTH.start()
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


# ------------------------------------------------------------- R1-26
def _sidecar(root: Path, acct: str, broker: str, rows):
    (root / "work" / f"{acct}_{broker}_transfers.json").write_text(
        json.dumps({"metadata": {"kind": "transfer_sidecar",
                                 "brokerage": broker},
                    "transactions": rows}))


def _send_fee_report(country: str, *, price_ccy: str):
    """A Coinbase Send of 1.0005 SOL that arrives on Kraken as 1.0 SOL,
    plus a USDC send that arrives 0.5 short, plus a Kraken withdrawal
    whose fee the ledger states (already booked by the parser)."""
    from taxjson.lib.crypto_sends import build_report
    d = tempfile.mkdtemp()
    root = Path(d)
    (root / "work").mkdir()
    _sidecar(root, "c", "coinbase", [
        {"action": "TRANSFER", "date": "2025-06-02", "time": "08:00:00",
         "symbol": "SOL", "quantity": -1.0005, "price": 200.0,
         "currency": price_ccy, "fee": 0.0, "description": "Send",
         "id": "cb1"},
        {"action": "TRANSFER", "date": "2025-06-03", "time": "09:00:00",
         "symbol": "USDC", "quantity": -100.5, "price": 1.0,
         "currency": "USD", "fee": 0.0, "description": "Send",
         "id": "cb2"},
        {"action": "TRANSFER", "date": "2025-06-05", "time": "09:00:00",
         "symbol": "ETH", "quantity": 0.5, "price": 0.0,
         "currency": "USD", "fee": 0.0, "description": "Receive",
         "id": "cb3"}])
    _sidecar(root, "c", "kraken", [
        {"action": "TRANSFER", "date": "2025-06-02", "time": "08:20:00",
         "symbol": "SOL", "quantity": 1.0, "currency": "USD", "fee": 0.0,
         "description": "deposit", "id": "kr1"},
        {"action": "TRANSFER", "date": "2025-06-03", "time": "09:20:00",
         "symbol": "USDC", "quantity": 100.0, "currency": "USD",
         "fee": 0.0, "description": "deposit", "id": "kr2"},
        # Kraken withdrawal: 0.5 ETH sent, 0.001 ETH fee booked by the
        # parser (named in the description) — never booked again here.
        {"action": "TRANSFER", "date": "2025-06-05", "time": "08:50:00",
         "symbol": "ETH", "quantity": -0.5, "currency": "USD", "fee": 0.0,
         "description": "withdrawal (fee 0.001 ETH)", "id": "kr3"}])
    cfg = {"settings": {"country": country,
                        "base_currency": "CAD" if country == "canada"
                        else "USD"},
           "accounts": {"c": {"crypto": True}}}
    rep = build_report(root, cfg, None, None, with_pool=False)
    return root, rep


class TestSendNetworkFeeBooked(unittest.TestCase):
    """R1-26: the coins a send lost in transit paid the network fee — a
    disposition at fair value written to crypto_sends.tt."""

    @rule("CA-CRYPTO-06")
    def test_canada_books_the_shortfall_at_fair_value(self):
        from taxjson.lib import crypto_sends as cs
        root, rep = _send_fee_report("canada", price_ccy="CAD")
        adoc = rep["accounts"]["c"]
        fees = adoc["network_fees"]
        # The USDC shortfall is US-dollar cash in a Canada book; the
        # Kraken ETH withdrawal's fee is already booked by the parser.
        self.assertEqual([f["symbol"] for f in fees], ["SOL"])
        f = fees[0]
        self.assertAlmostEqual(f["quantity"], 0.0005)
        self.assertEqual(f["id"], "cb-20250602T080000-SOL-1.0005-fee")
        self.assertIn("Coinbase spot price", f["fair_value"]["source"])
        self.assertEqual(f["tt"], "BUYSELL 2025-06-02 08:00:00 SOL "
                                  "-0.0005 CAD 200 0.10 0")
        entries, unpriced = cs.tt_entries(adoc)
        self.assertEqual(unpriced, [])
        self.assertEqual([e["tt"] for e in entries], [f["tt"]])
        text = cs.render_tt("c", entries, "canada")
        self.assertIn(f["tt"], text)
        self.assertIn("network fee", text)
        # The generated file's ids include the fee: the checklist's
        # staleness check compares them.
        p = root / "crypto_sends.tt"
        p.write_text(text)
        self.assertEqual(cs.tt_ids(p), {f["id"]})

    @rule("US-CRYPTO-05")
    def test_us_books_the_shortfall_too_stablecoin_at_par(self):
        from taxjson.lib import crypto_sends as cs
        _root, rep = _send_fee_report("usa", price_ccy="USD")
        fees = {f["symbol"]: f for f in rep["accounts"]["c"]["network_fees"]}
        self.assertEqual(sorted(fees), ["SOL", "USDC"])
        self.assertEqual(fees["SOL"]["tt"], "BUYSELL 2025-06-02 08:00:00 "
                                            "SOL -0.0005 USD 200 0.10 0")
        # A US stablecoin is property: its shortfall is a sale at par.
        self.assertEqual(fees["USDC"]["tt"], "BUYSELL 2025-06-03 09:00:00 "
                                             "USDC -0.5 USD 1 0.50 0")
        entries, _ = cs.tt_entries(rep["accounts"]["c"])
        self.assertEqual(len(entries), 2)

    @rule("CA-CRYPTO-06")
    def test_unpriced_shortfall_is_reported_not_guessed(self):
        from taxjson.lib import crypto_sends as cs
        # A CAD project whose send is priced in USD with no USD rate on
        # file: no fair value, so nothing is written.
        _root, rep = _send_fee_report("canada", price_ccy="USD")
        entries, unpriced = cs.tt_entries(rep["accounts"]["c"])
        self.assertEqual(entries, [])
        self.assertEqual([e["symbol"] for e in unpriced], ["SOL"])


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

    # The parser's switch set by hand: not a country pair (A2-0830);
    # test_the_project_country_decides is.
    @rule("CA-CRYPTO-02")
    @rule("US-CRYPTO-02")
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

    # The parser's switch set by hand: not a country pair (A2-0830);
    # test_the_project_country_decides is.
    @rule("CA-CRYPTO-02")
    @rule("US-CRYPTO-02")
    def test_reward_is_dollar_income_in_canada_coin_in_us(self):
        ca = _kraken(_PYUSD_REWARD, "kr_ledgers", cash=True)
        self.assertEqual([(t["action"], t["price"], t["net_amount"])
                          for t in ca], [("DIVIDEND", 1.0, 10.0)])
        us = _kraken(_PYUSD_REWARD, "kr_ledgers", cash=False)
        self.assertEqual(sorted((t["action"], t["symbol"]) for t in us),
                         [("BUYSELL", "PYUSD"), ("DIVIDEND", "PYUSD")])

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    @rule("US-CRYPTO-02")
    def test_the_project_country_decides(self):
        # A2-0830: the two tests above set the parser's switch by hand;
        # here taxjson-brokerage sets it from --country.
        def parse(country):
            with tempfile.TemporaryDirectory() as td:
                f = Path(td) / "kr_trades.csv"
                f.write_text(_PYUSD_TRADES)
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                     "--brokerage", "kraken", "--country", country, str(f)],
                    capture_output=True, text=True,
                    stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            return sorted({t["symbol"] for t in
                           json.loads(r.stdout)["transactions"]
                           if t["action"] == "BUYSELL"})
        self.assertEqual(parse("canada"), ["ETH"])
        self.assertEqual(parse("usa"), ["ETH", "GUSD", "PYUSD"])

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
