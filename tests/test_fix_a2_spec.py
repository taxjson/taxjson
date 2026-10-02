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
    if not isinstance(accounts, dict):
        accounts = {a: "taxable" for a in accounts}
    acc = "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items())
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, **settings) + acc)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _gains_one(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains on `book` (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
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


# ------------------------------------------------------------ income
def _rules(country="canada", **settings):
    from taxjson.lib.income_dating import IncomeRules
    return IncomeRules.from_settings(dict(settings, country=country))


class TestWhichCanadianIssuerIsATrust(unittest.TestCase):
    """A2-0810 / A2-1466: the trust test is every Canadian issuer but the
    corporate list; tax-logic says so and the January warning asks."""

    _ROC = {"action": "ADJUST", "type": "roc", "symbol": "ZZCO.TO",
            "date": "2026-01-06", "record_date": "2025-12-30",
            "net_amount": -100.0, "currency": "CAD",
            "description": "ZZCO CORP COMMON SHARES RETURN OF CAPITAL"}

    @rule("CA-INC-DATE-ISSUER")
    def test_corporation_roc_is_record_dated_until_listed(self):
        self.assertEqual(_rules().roc_date(self._ROC), "2025-12-30")
        listed = _rules(corporate_distributions=["ZZCO.TO"])
        self.assertEqual(listed.roc_date(self._ROC), "2026-01-06")
        w = _rules().warnings([self._ROC], year=2026)
        self.assertTrue(any("names a corporation" in x for x in w), w)
        self.assertIn("corporate_distributions",
                      _text("canada", "CA-INC-DATE-ISSUER"))

    @rule("CA-INC-DATE-ISSUER", "CA-INC-DATE-ROC-TRUST")
    def test_january_roc_warning_asks(self):
        row = dict(self._ROC, symbol="ZZB.TO", record_date="",
                   description="ZZB INC RETURN OF CAPITAL")
        w = _rules().warnings([row], year=2026)
        self.assertEqual(len(w), 1)
        self.assertIn("If ZZB.TO is a Canadian trust", w[0])
        self.assertIn("corporation", w[0])
        self.assertNotIn("this Canadian trust's", w[0])
        self.assertEqual(
            _rules(corporate_distributions=["ZZB.TO"]).warnings(
                [row], year=2026), [])


class TestIssuerCountryFromTheIsin(unittest.TestCase):
    """A2-1470: the ISIN country, when given, decides a Canadian issuer
    over the listing — as the text now says."""

    @rule("CA-INC-03")
    def test_pil_on_a_bermuda_issuer_listed_in_toronto(self):
        row = {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZP.UN.TO",
               "dealer_country": "CA", "issuer_country": "BM",
               "date": "2025-06-02"}
        self.assertFalse(_rules().pil_is_dividend(row))
        self.assertTrue(_rules().pil_is_dividend(
            dict(row, issuer_country="")))
        self.assertTrue(_rules().pil_is_dividend(
            dict(row, symbol="ZZP.US", issuer_country="CA")))
        self.assertIn("ISIN country", _text("canada", "CA-INC-03"))

    @rule("CA-INC-DATE-TRUST")
    def test_distribution_of_a_foreign_isin_keeps_the_pay_date(self):
        row = {"action": "DIVIDEND", "income_label": "distribution",
               "symbol": "ZZP.UN.TO", "date": "2026-01-15",
               "record_date": "2025-12-31", "issuer_country": "BM"}
        self.assertEqual(_rules().income_date(row), "2026-01-15")
        self.assertEqual(_rules().income_date(
            dict(row, issuer_country="")), "2025-12-31")
        self.assertIn("ISIN country", _text("canada", "CA-INC-DATE-TRUST"))


class TestPilOnATrustUnit(unittest.TestCase):
    """A2-1465: a payment in lieu on a Canadian ETF unit is deemed a
    dividend (the export cannot tell a unit from a share); tax-logic
    states it and the slip decides."""

    @rule("CA-INC-07")
    def test_etf_unit_pil_is_deemed(self):
        row = {"action": "DIVIDEND_IN_LIEU", "symbol": "ZZX.TO",
               "dealer_country": "CA", "date": "2025-06-02"}
        self.assertTrue(_rules().pil_is_dividend(row))
        self.assertIn("trust unit", _text("canada", "CA-INC-07"))


class TestIncomeDatingCitesKnownIds(unittest.TestCase):
    """A2-1468: income_dating's docstring cites tax-logic ids only."""

    @rule("CA-INC-03")
    def test_docstring_ids_exist(self):
        import re
        from taxjson.lib import income_dating
        ids = set(re.findall(r"\[((?:CA|US)-[A-Z0-9-]+)\]",
                             income_dating.__doc__))
        self.assertIn("CA-INC-03", ids)
        known = set(TL.catalog())
        self.assertEqual(ids - known, set())


# ------------------------------------------------------------ options
class TestCloseTimingClaims(unittest.TestCase):
    """A2-0826: one id per claim under close timing, each pinned on the
    engine (forcing grant timing fails both)."""

    _BOOK = [tx("BUYSELL", "2025-11-03", "ZZQ260116C00050000.US", -1, 300),
             tx("BUYSELL", "2026-01-05", "ZZQ260116C00050000.US", 1, 100)]

    def _year(self, year, timing="close"):
        r = _gains_one("canada", self._BOOK, year=year,
                       option_premium_timing=timing)
        return [t for t in r["transactions"] if t.get("symbol")]

    @rule("CA-OPT-05")
    def test_write_is_not_taxed_until_it_closes(self):
        self.assertEqual(self._year(2025), [])
        self.assertTrue(self._year(2025, timing="grant"))

    @rule("CA-OPT-10")
    def test_buy_back_realizes_premium_minus_cost(self):
        g = self._year(2026)
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0]["gain"], 200.0, places=2)
        self.assertIn("premium minus the cost",
                      _text("canada", "CA-OPT-10",
                            option_premium_timing="close"))


# ------------------------------------------------------------ reports
class TestT1135Scope(unittest.TestCase):
    """A2-1473 (scope) and A2-0821 (the $250,000 Part A / Part B split)."""

    def _rep(self, net):
        from taxjson.bin.taxjson_t1135 import build_report
        from test_t1135 import tx as t1135_tx
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "base.json"
            base.write_text(json.dumps({"transactions": [
                t1135_tx(date="2025-01-10", qty=100, net=net)]}))
            return build_report([base], [], 2025, {}, "CAD")

    @rule("CA-RPT-13")
    def test_verdict_is_on_these_books(self):
        rep = self._rep(50000.0)
        self.assertFalse(rep["filing_required"])
        self.assertEqual(rep["scope"], "books_only")
        self.assertIn("outside them", rep["scope_note"])
        self.assertIn("these books", _text("canada", "CA-RPT-13"))

    @rule("CA-RPT-14")
    def test_simplified_below_250k(self):
        self.assertTrue(self._rep(240000.0)["simplified_method_available"])
        self.assertFalse(self._rep(250000.0)["simplified_method_available"])


class TestUsShelteredOutOfTheTotals(unittest.TestCase):
    """A2-0822: US tax-logic states that sheltered (IRA) accounts are
    kept out of Form 8949 and the totals."""

    @rule("US-BASIS-07")
    def test_ira_sale_is_not_on_form_8949(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", {
                "inputs/margin/m.tt":
                    "BUYSELL 2024-02-01 10:00:00 XYZ.US 10 USD 100 1000 0\n"
                    "BUYSELL 2025-06-02 10:00:00 XYZ.US -10 USD 200 2000 0\n",
                "inputs/ira/i.tt":
                    "BUYSELL 2024-02-01 10:00:00 QQZ.US 10 USD 100 1000 0\n"
                    "BUYSELL 2025-06-02 10:00:00 QQZ.US -10 USD 300 3000 0\n"},
                accounts={"margin": "taxable", "ira": "sheltered"})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            f = cli(root, "form-export", "--form", "8949")
            self.assertEqual(f.returncode, 0, f.stderr[-2000:])
        self.assertIn("XYZ.US", f.stdout)
        self.assertNotIn("QQZ.US", f.stdout)
        self.assertIn("sheltered", _text("usa", "US-BASIS-07"))


# ------------------------------------------------------------ planning
class TestRadarIraReplacementIsPermanent(unittest.TestCase):
    """A2-1474: US-PLAN-01 carves out the IRA replacement the radar
    already reports as lost for good."""

    @rule("US-PLAN-01")
    def test_ira_rebuy_is_lost_for_good(self):
        from test_fix_a2_planning import _row, _rows
        book = [_row("2025-01-10", "XYZ.US", 100, 5000.0, rid="b1",
                     currency="USD"),
                _row("2025-03-03", "XYZ.US", -100, 4000.0, rid="s1",
                     currency="USD")]
        ira = [_row("2025-03-10", "XYZ.US", 100, 4000.0, account="ira",
                    rid="i1", currency="USD")]
        row = _rows(book, "2025-03-20", sheltered=ira,
                    country="usa")["XYZ.US"]
        self.assertIn("WASHED", row["advisory"])
        self.assertIn("lost for good", row["advisory"])
        self.assertIn("IRA", _text("usa", "US-PLAN-01"))


# ------------------------------------------------------------ corporate
class TestMergerCash(unittest.TestCase):
    """A2-1475 / A2-0824 (corporate half) / A2-1477: cash in a merger and
    cash in lieu of a fraction, stated per country."""

    CASH = ('TGT(CA0000000555) Merged(Acquisition) FOR CAD 30.00 PER '
            'SHARE (TGT, TARGET CO, CA0000000555)')
    BOTH = ('OLDC(CA0000000801) Merged(Acquisition) WITH CA0000000802 1 '
            'for 2 AND CAD 5.00 ({t}, {n}, {i})')

    def _ib(self, *rows, cur="CAD"):
        from test_fix_corp import _IB_CA, _IB_HEAD, _IB_TRADES
        head = _IB_HEAD.format(acct='U5550001')  # pii-ok: synthetic
        return (head.replace("Base Currency,CAD", f"Base Currency,{cur}")
                + _IB_TRADES + rows[0] + _IB_CA + "".join(rows[1:]))

    def _run(self, country, body):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"inputs/margin/ib_2026.csv": body},
                            year=2026)
            r = cli(root, "run", "--no-input")
            out = {"rc": r.returncode, "err": r.stderr + r.stdout}
            g = root / "work" / "margin_gains_wash.json"
            if g.exists():
                out["gains"] = json.loads(g.read_text())
            pe = root / "work" / "pending_elections.json"
            if pe.exists():
                out["pending"] = json.loads(pe.read_text())[
                    "accounts"]["margin"]["pending"]
            return out

    def _cash_takeover(self, country):
        from test_fix_corp import _ib_buy, _ib_ca
        cur, sym = (("CAD", "TGT.TO") if country == "canada"
                    else ("USD", "TGT.US"))
        desc = self.CASH.replace("CAD", cur).replace("CA0000000555",
                                                     "US0000000555")
        r = self._run(country, self._ib(
            _ib_buy('TGT', 100, 20, cur=cur),
            _ib_ca(desc, -100, -3000, proceeds=3000,
                   when='2026-06-01, 20:25:00', cur=cur), cur=cur))
        self.assertEqual(r["rc"], 0, r["err"][-2000:])
        sales = [t for t in r["gains"]["transactions"]
                 if t.get("symbol") == sym and t.get("qty")]
        self.assertEqual(len(sales), 1)
        self.assertAlmostEqual(sales[0]["proceeds"], 3000.0, places=2)
        self.assertAlmostEqual(sales[0]["gain"], 1000.0, places=2)
        self.assertEqual([i for i in r["gains"]["inventory"]
                          if abs(i["qty"]) > 1e-9], [])

    def _stock_and_cash(self, country):
        from test_fix_corp import _ib_buy, _ib_ca
        cur = "CAD" if country == "canada" else "USD"
        both = self.BOTH.replace("CAD", cur).replace("CA00", "US00")
        r = self._run(country, self._ib(
            _ib_buy('OLDC', 100, 20, cur=cur),
            _ib_ca(both.format(t='NEWC', n='NEWC CORP',
                               i='US0000000802'), 50, 2000, cur=cur),
            _ib_ca(both.format(t='OLDC', n='OLDC CORP',
                               i='US0000000801'), -100, -2500,
                   proceeds=500, cur=cur), cur=cur))
        self.assertEqual(r["rc"], 3, r["err"][-2000:])
        self.assertEqual(len(r["pending"]), 1)
        self.assertIn("UNSUPPORTED", r["pending"][0]["summary"])

    @rule("CA-CORP-09")
    def test_ca_cash_takeover_is_a_sale(self):
        self._cash_takeover("canada")
        self.assertIn("wholly in cash", _text("canada", "CA-CORP-09"))

    @rule("US-CORP-10")
    def test_us_cash_takeover_is_a_sale(self):
        self._cash_takeover("usa")

    @rule("CA-CORP-10")
    def test_ca_stock_and_cash_stops(self):
        self._stock_and_cash("canada")
        self.assertNotIn("not modelled", _text("canada", "CA-CORP-05"))

    @rule("US-CORP-11")
    def test_us_stock_and_cash_stops(self):
        self._stock_and_cash("usa")

    _CIL = ("BUYSELL 2023-02-01 10:00:00 QZS.US 10 USD 20 200 0\n"
            "BUYSELL 2025-03-03 10:00:00 QZS.US 10 USD 40 400 0\n")

    def _cil_book(self):
        # Questrade's CIL pair: the fraction at $0, then sold for the cash.
        return [tx("BUYSELL", "2023-02-01", "QZS.US", 10, 200),
                tx("BUYSELL", "2025-03-03", "QZS.US", 10, 400),
                tx("BUYSELL", "2025-06-30", "QZS.US", 0.4, 0.0,
                   time="09:30:00"),
                tx("BUYSELL", "2025-06-30", "QZS.US", -0.4, 3.10,
                   time="09:30:01")]

    @rule("CA-CORP-05")
    def test_ca_cil_is_a_sale_on_the_pool(self):
        r = _gains_one("canada", self._cil_book())
        g = [t for t in r["transactions"] if t.get("qty")]
        self.assertEqual(len(g), 1)
        # Average cost of the pool with the $0 fraction: 600 / 20.4.
        self.assertAlmostEqual(g[0]["cost"], 0.4 * 600 / 20.4, places=2)

    @rule("US-CORP-09")
    def test_us_cil_takes_the_oldest_lot(self):
        r = _gains_one("usa", self._cil_book())
        g = [t for t in r["transactions"] if t.get("qty")]
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0]["cost"], 8.0, places=2)
        self.assertEqual(g[0]["term"], "LONG_TERM")
        self.assertIn("oldest lot", _text("usa", "US-CORP-09"))


# ------------------------------------------------------------ US wash
class TestUsSameMomentReplacementOrder(unittest.TestCase):
    """A2-0485: same-moment replacements go taxable first, then the
    IRA, then by row order (accounts in taxjson.toml order) — never by
    the account's label."""

    def _book(self, first, second):
        return [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000,
                   account=first),
                tx("BUYSELL", "2025-03-03", "XYZ.US", -100, 4000,
                   account=first),
                tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                   account=first, time="11:00:00"),
                tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                   account=second, time="11:00:00")]

    @staticmethod
    def _cost(res):
        return {i["account"]: round(i["total_cost"], 2)
                for i in res["inventory"]}

    @rule("US-WASH-20")
    def test_row_order_not_label(self):
        for first, second in (("z_first", "a_second"),
                              ("a_first", "z_second")):
            c = self._cost(_gains_one("usa", self._book(first, second)))
            self.assertEqual((c[first], c[second]), (5000.0, 4000.0))

    @rule("US-WASH-20")
    def test_taxable_before_the_ira(self):
        book = self._book("margin", "margin")[:3]
        ira = [tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                  account="ira", time="11:00:00")]
        # Same moment as the taxable rebuy: the taxable lot takes it.
        r = _gains_one("usa", book, sheltered=ira)
        loss = [t for t in r["transactions"] if t.get("qty")][0]
        self.assertAlmostEqual(loss["disallowed_amount"], 1000.0, places=2)
        self.assertAlmostEqual(loss.get("permanently_disallowed") or 0.0,
                               0.0, places=2)
        self.assertIn("taxable accounts first", _text("usa", "US-WASH-20"))


class TestUsStockDividendWithNothingHeld(unittest.TestCase):
    """A2-1486: the $0-lot fallback is stated; it never washes a loss."""

    @rule("US-STKDIV-03")
    def test_zero_lot_is_warned_and_not_a_replacement(self):
        from taxjson.lib.core import STOCK_DIVIDEND
        r = _gains_one("usa", [
            tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000),
            tx("BUYSELL", "2025-03-03", "XYZ.US", -100, 4000),
            tx("BUYSELL", "2025-03-10", "XYZ.US", 5, 0,
               type=STOCK_DIVIDEND)])
        sale = [t for t in r["transactions"] if t.get("qty")][0]
        self.assertAlmostEqual(sale["gain"], -1000.0, places=2)
        self.assertAlmostEqual(sale["disallowed_amount"], 0.0, places=2)
        self.assertEqual([(i["qty"], i["total_cost"])
                          for i in r["inventory"]], [(5.0, 0.0)])
        self.assertIn("warning: XYZ.US: stock dividend", r["_stderr"])
        self.assertIn("$0 lot", _text("usa", "US-STKDIV-03"))


if __name__ == "__main__":
    unittest.main()
