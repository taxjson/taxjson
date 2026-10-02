"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.
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
from unittest import mock

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, gains_both, settings_for, tx

from taxjson.lib import tax_logic as TL

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable


def _text(country, rid, **settings):
    """The rendered text of one rule under `settings`."""
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


def _project(td, country, files, *, accounts=("margin",), year=2025,
             **settings):
    root = Path(td) / country
    root.mkdir(parents=True, exist_ok=True)
    acc = "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, **settings) + acc)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _gains_one(country, book, **req):
    """One country's pipeline.run_gains on `book` (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), [], [],
                        req=GainsRequest(country=country, taxable=True,
                                         **dict(dict(year=2025), **req)))
    res["_stderr"] = err.getvalue()
    return res


def _gains(root, account="margin"):
    p = root / "work" / f"{account}_gains.json"
    return json.loads(p.read_text())


# ------------------------------------------------------------ dates
class TestLocalZoneInsideAProject(unittest.TestCase):
    """A2-0165: inside a project with no local_timezone the environment
    variable no longer re-dates crypto; the default zone tax-logic names
    is the one in force."""

    def _check(self, country, rid):
        from taxjson.bin import taxjson_run as run
        from taxjson.lib.brokerages._crypto_common import (
            DEFAULT_LOCAL_TZ, local_tz_name)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ,
                                {"TAXJSON_LOCAL_TZ": "Asia/Tokyo"}):
            root = _project(td, country, {})
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                run.load_config(root)
            self.assertEqual(local_tz_name(), DEFAULT_LOCAL_TZ)
            self.assertIn("ignored inside a project", err.getvalue())
            self.assertIn(DEFAULT_LOCAL_TZ, _text(country, rid))
            # The setting still wins.
            root2 = _project(Path(td) / "b", country, {},
                             local_timezone="America/Vancouver")
            run.load_config(root2)
            self.assertEqual(local_tz_name(), "America/Vancouver")

    @rule("CA-DATE-12")
    def test_canada(self):
        self._check("canada", "CA-DATE-12")

    @rule("US-DATE-11")
    def test_usa(self):
        self._check("usa", "US-DATE-11")


class TestExpiryDating(unittest.TestCase):
    """A2-0483: the 7-day re-dating window and the 0DTE settle clamp."""

    def _window(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        f = BaseBrokerage.option_expiry_booking_date
        self.assertEqual(f("2026-01-02", "12/31/25"), "2025-12-31")
        self.assertEqual(f("2026-01-07", "12/31/25"), "2025-12-31")
        self.assertEqual(f("2026-01-09", "12/31/25"), "2026-01-09")

    def _clamp(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        trade = {"action": "BUYSELL", "symbol": "ZZQ251231C00050000",
                 "account": "m", "date": "2025-12-31",
                 "date_settle": "2026-01-02"}
        other = dict(trade, symbol="ZZQ260116C00050000")
        exp = {"symbol": "ZZQ251231C00050000", "account": "m",
               "date": "2025-12-31", "date_settle": "2025-12-31"}
        BaseBrokerage.clamp_settlement_across([trade, other], [exp])
        self.assertEqual(trade["date_settle"], "2025-12-31")
        self.assertEqual(other["date_settle"], "2026-01-02")

    @rule("CA-DATE-15")
    def test_ca_window(self):
        self._window()

    @rule("US-DATE-14")
    def test_us_window(self):
        self._window()

    @rule("CA-DATE-16")
    def test_ca_clamp(self):
        self._clamp()

    @rule("US-DATE-15")
    def test_us_clamp(self):
        self._clamp()


class TestWebullPrintsTheSettleDate(unittest.TestCase):
    """A2-1478: Webull's Date column is the settle date; the trade date
    is walked back one cycle."""

    def _parse(self):
        from test_webull_formats import _H25, _PRE, _parse
        text = (_PRE.replace("2024", "2026") + _H25
                + 'USD,02-01-2026,SELL,ZZR,ZZR HOLDINGS INC CLASS A,SHS,'
                  '-10,50.00,,499.00\n'
                + 'USD,02-12-2025,BUY,ZZR,,,10,40.00,,"(401.00)"\n')
        return {t["quantity"]: t for t in _parse(text)}

    @rule("CA-DATE-17")
    def test_canada(self):
        rows = self._parse()
        self.assertEqual((rows[-10.0]["date"], rows[-10.0]["date_settle"]),
                         ("2025-12-31", "2026-01-02"))
        self.assertEqual(rows[10.0]["date"], "2025-12-01")

    @rule("US-DATE-16")
    def test_usa(self):
        rows = self._parse()
        self.assertEqual((rows[-10.0]["date"], rows[-10.0]["date_settle"]),
                         ("2025-12-31", "2026-01-02"))


class TestCrossFileTies(unittest.TestCase):
    """A2-0823: same-moment rows of one account from two files follow
    the files' name order."""

    _BUY = "BUYSELL 2025-02-03 10:00:00 JJJ.US 100 USD 10 1000 0\n"

    def _run(self, country, a, b, extra=""):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {
                "inputs/margin/0.tt": self._BUY + extra,
                "inputs/margin/a.tt": a, "inputs/margin/b.tt": b})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            return _gains(root)

    @rule("US-DATE-17")
    def test_usa_fifo_follows_file_names(self):
        sell = "BUYSELL 2025-06-02 11:00:00 JJJ.US -100 USD {p} {t} 0\n"
        lot2 = "BUYSELL 2025-03-03 10:00:00 JJJ.US 100 USD 20 2000 0\n"
        g = self._run("usa", sell.format(p=12, t=1200),
                      sell.format(p=30, t=3000), extra=lot2)
        by_p = {round(x["proceeds"]): x for x in g["transactions"]}
        # a.tt's sale (1200) takes the first lot (cost 1000).
        self.assertAlmostEqual(by_p[1200]["cost"], 1000.0, places=2)
        self.assertAlmostEqual(by_p[3000]["cost"], 2000.0, places=2)
        g2 = self._run("usa", sell.format(p=30, t=3000),
                       sell.format(p=12, t=1200), extra=lot2)
        by_p2 = {round(x["proceeds"]): x for x in g2["transactions"]}
        self.assertAlmostEqual(by_p2[3000]["cost"], 1000.0, places=2)

    @rule("CA-DATE-18")
    def test_canada_sale_before_rebuy_by_file_name(self):
        sell = "BUYSELL 2025-06-02 11:00:00 JJJ.US -100 USD 20 2000 0\n"
        buy = "BUYSELL 2025-06-02 11:00:00 JJJ.US 100 USD 30 3000 0\n"
        g = self._run("canada", sell, buy)
        self.assertEqual(len(g["transactions"]), 1)
        # The sale in a.tt is made from the shares held before b.tt's
        # buy: ACB 1000 (USD 10/share), not the blended 2000.
        rg = g["transactions"][0]
        self.assertLess(rg["cost"], rg["proceeds"] * 0.75)
        g2 = self._run("canada", buy, sell)
        rg2 = g2["transactions"][0]
        self.assertGreater(rg2["cost"], rg["cost"] * 1.5)


# ------------------------------------------------------------ crypto
class TestCoinbaseEth2IsEth(unittest.TestCase):
    """A2-0479 / A2-0815: Coinbase's ETH -> ETH2 convert is a relabel;
    tax-logic says so for each country."""

    def _check(self, country, rid):
        from test_fix_m_parsers2_crypto import _bs, _cb_row, _parse_cb
        self.assertIn("Coinbase's ETH2", _text(country, rid))
        txs, err = _parse_cb(
            _cb_row("b1", "2025-01-02 10:00:00 UTC", "Buy", "ETH", "1",
                    "CAD", "2000", "2000", "2000", "0")
            + _cb_row("c1", "2025-06-02 10:00:00 UTC", "Convert", "ETH",
                      "-1", "CAD", "4000", "4000", "4000", "0",
                      "Converted 1 ETH to 1 ETH2"))
        self.assertEqual([(t["symbol"], t["quantity"]) for t in _bs(txs)],
                         [("ETH", 1.0)])
        self.assertIn("same property ETH", err)

    @rule("CA-CRYPTO-10")
    def test_canada(self):
        self._check("canada", "CA-CRYPTO-10")

    @rule("US-CRYPTO-06")
    def test_usa(self):
        self._check("usa", "US-CRYPTO-06")


class TestKrakenDustSweepSplit(unittest.TestCase):
    """A2-1469: a multi-coin dust sweep's receipt is split by amountusd,
    equally without it."""

    def _sales(self, rows, header, cash):
        from test_fix_a2_kraken import _ledger, _parse
        txs, err = _parse({"kr_ledgers.csv": _ledger(rows, header)},
                          "kr_ledgers.csv", cash=cash)
        return ({t["symbol"]: round(t["net_amount"], 6) for t in txs
                 if t["action"] == "BUYSELL"}, err)

    def _check(self, cash):
        from test_fix_a2_kraken import _KL_H, _KL_H2
        legs = ["L1,RS1,2025-03-01 12:00:00,spend,,currency,ADA,spot,-1,0,0",
                "L2,RS1,2025-03-01 12:00:00,spend,,currency,AVAX,spot,-0.01,"
                "0,0",
                "L3,RS1,2025-03-01 12:00:00,spend,,currency,BNB,spot,-0.001,"
                "0,0",
                "L4,RS1,2025-03-01 12:00:00,receive,,currency,ZUSD,spot,1.80,"
                "0,1.80"]
        eq, err = self._sales(legs, _KL_H, cash)
        self.assertEqual(eq, {"ADA": 0.6, "AVAX": 0.6, "BNB": 0.6})
        self.assertIn("equal shares", err)
        usd = [legs[0] + ",0.90,,", legs[1] + ",0.60,,", legs[2] + ",0.30,,",
               legs[3] + ",1.80,,"]
        by, err = self._sales(usd, _KL_H2, cash)
        self.assertEqual(by, {"ADA": 0.9, "AVAX": 0.6, "BNB": 0.3})
        self.assertIn("amountusd", err)

    @rule("CA-CRYPTO-11")
    def test_canada(self):
        self._check(cash=True)

    @rule("US-CRYPTO-07")
    def test_usa(self):
        self._check(cash=False)


class TestUsDust(unittest.TestCase):
    """A2-0808 / A2-0818 / A2-0820 / A2-1471 / A2-1484 / A2-1485: the
    US engine's 1e-08 zero is stated and every case is named; Canada
    keeps any amount of a coin (CA-CRYPTO-09)."""

    def _book(self, *rows):
        return [tx("BUYSELL", d, s, q, n, currency="CAD", account="crypto")
                for d, s, q, n in rows]

    @rule("US-CRYPTO-08")
    @rule_absent("US-CRYPTO-08", country="canada")
    def test_dust_row_residue_and_excess(self):
        r = gains_both(self._book(("2025-01-02", "ETH", 5e-9, 2),
                                  ("2025-03-03", "ETH", -5e-9, 3)),
                       year=2025)
        self.assertEqual(r["usa"]["transactions"], [])
        self.assertIn("smaller than 1e-08", r["usa"]["_stderr"])
        self.assertAlmostEqual(r["canada"]["transactions"][0]["gain"], 1.0)

        r = gains_both(self._book(("2025-01-02", "ETH", 1.000000005, 2000),
                                  ("2025-03-03", "ETH", -1, 3000)),
                       year=2025)
        self.assertEqual(r["usa"]["inventory"], [])
        self.assertIn("lot residue of at most 1e-08",
                      r["usa"]["_stderr"])
        self.assertEqual(len(r["canada"]["inventory"]), 1)
        self.assertNotIn("1e-08", r["canada"]["_stderr"])

        r = gains_both(self._book(("2025-01-02", "BTC", 1, 2000),
                                  ("2025-03-03", "BTC", -1.000000009, 3000)),
                       year=2025)
        self.assertEqual(r["usa"]["inventory"], [])
        self.assertIn("exceeded the units held", r["usa"]["_stderr"])
        self.assertLess(r["canada"]["inventory"][0]["qty"], 0)
        self.assertIn("1e-08", _text("usa", "US-CRYPTO-08"))

    @rule("US-CRYPTO-08")
    def test_float_noise_is_not_named(self):
        r = _gains_one("usa", self._book(("2025-01-02", "ETH", 0.1, 200),
                                         ("2025-01-03", "ETH", 0.2, 400),
                                         ("2025-03-03", "ETH", -0.3, 900)))
        self.assertEqual(r["inventory"], [])
        self.assertNotIn("1e-08", r["_stderr"])


if __name__ == "__main__":
    unittest.main()
