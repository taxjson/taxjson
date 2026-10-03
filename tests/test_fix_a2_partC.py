"""Regression pins for the re-audit-2 partition lists 05 and 06 (RBC
parser notes, web holdings pages, phantom_holdings, taxjson-brokerage,
reconcile-slips, export, explain, harvest).

Synthetic data only: fake account numbers (55500001 # pii-ok), no FX
fetch.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env():
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO_ROOT / "src")
    return e


def _module(mod, *args, cwd=None, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", mod, *args], cwd=cwd or REPO_ROOT,
        capture_output=True, text=True, env=_env(),
        input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)


# ------------------------------------------------- A2-0414 (crypto-sends)

class TestCryptoSendsRateLookback(unittest.TestCase):
    """A send is priced with a rate from its own day or the 5 days
    before (convert-currency's lookback), never an older one."""

    def _send(self, day):
        return {"symbol": "ETH", "date": day, "quantity": -1.0,
                "price": 3000.0, "currency": "USD", "exchange": "coinbase"}

    def _check(self, base, cur):
        from taxjson.lib import crypto_sends as cs
        rates = cs.Rates({cur: {"2025-01-02": (1.44, "boc")}}, base)
        send = self._send("2025-06-30")
        if base == "USD":
            send["currency"] = cur
        self.assertIsNone(cs.fair_value(send, rates, None),
                          "a six-month-old rate priced the send")
        near = self._send("2025-01-06")
        if base == "USD":
            near["currency"] = cur
        fv = cs.fair_value(near, rates, None)
        self.assertIsNotNone(fv)
        self.assertAlmostEqual(fv["price"], 3000.0 * 1.44, places=2)
        self.assertIn("latest before 2025-01-06", fv["source"])

    @rule("CA-FX-02")
    def test_canada_cad_base(self):
        self._check("CAD", "USD")

    @rule("US-FX-02")
    def test_usa_usd_base(self):
        self._check("USD", "CAD")

    @rule("CA-FX-02")
    @rule("US-FX-02")
    def test_usd_pool_rate_is_bounded_too(self):
        from taxjson.lib import crypto_sends as cs
        rates = cs.Rates({"USD": {"2025-01-02": (1.44, "boc")}}, "CAD")
        self.assertIsNone(rates.get("USD", "2025-01-08"))
        self.assertEqual(rates.get("USD", "2025-01-07")[1], "2025-01-02")


# --------------------------- A2-0419/0424/0429/0430/0438 (BOM taxjson.toml)

_US_TOML = """[settings]
country = "usa"
year = 2025

[accounts.margin]
type = "taxable"

[accounts.ira]
type = "sheltered"
"""


def _short_book():
    """A short sale traded 2025-12-31 that settles 2026-01-02 in the
    sheltered account (a position that goes negative)."""
    return {"transactions": [
        {"action": "BUYSELL", "date": "2025-12-31", "time": "10:00:00",
         "date_settle": "2026-01-02", "symbol": "QZQ.US",
         "quantity": -10, "price": 50.0, "net_amount": 500.0,
         "currency": "USD", "account": "ira", "fee": 0.0}]}


class TestProjectTomlBom(unittest.TestCase):

    def _project(self, tmp, text, bom=True):
        root = Path(tmp)
        (root / "work").mkdir()
        raw = text.encode("utf-8")
        (root / "taxjson.toml").write_bytes(
            (b"\xef\xbb\xbf" if bom else b"") + raw)
        base = root / "work" / "ira_base.json"
        base.write_text(json.dumps(_short_book()), encoding="utf-8")
        return base

    @rule("US-DATE-01")
    def test_bom_project_keeps_country_basis_and_types(self):
        from taxjson.lib import missing_history as ph
        with tempfile.TemporaryDirectory() as tmp:
            base = self._project(tmp, _US_TOML)
            self.assertEqual(ph.tax_date_near(base), "trade")
            self.assertEqual(ph.account_types_near(base),
                             {"margin": False, "ira": True})

    def test_unparseable_toml_stops_not_no_project(self):
        from taxjson.lib import missing_history as ph
        from taxjson.lib.cli_diag import InputReadError
        with tempfile.TemporaryDirectory() as tmp:
            base = self._project(tmp, "[settings\ncountry = 'usa'\n")
            with self.assertRaises(InputReadError) as cm:
                ph.account_types_near(base)
            self.assertIn("not valid TOML", str(cm.exception))

    def test_radar_config_accepts_bom(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as tmp:
            self._project(tmp, _US_TOML)
            cfg = R._radar_config(Path(tmp))
            self.assertEqual(cfg["settings"]["country"], "usa")

    def test_missing_history_cli_bom_us_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._project(tmp, _US_TOML)
            r = _module("taxjson.bin.taxjson_missing_history",
                        str(base), "--year", "2025")
            self.assertNotIn("no taxjson.toml beside", r.stderr)
            # Trade date 2025-12-31 (US): the row is in 2025, and the
            # sheltered account is flagged REG from the configured type.
            self.assertIn("REG", r.stdout)
            bad = Path(tmp) / "taxjson.toml"
            bad.write_text("[settings\n", encoding="utf-8")
            r = _module("taxjson.bin.taxjson_missing_history",
                        str(base), "--year", "2025")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("not valid TOML", r.stderr)
            self.assertNotIn("Traceback", r.stderr)


# ------------------------------------------------ reconcile-slips (06)

def _rs_sell(symbol="QZQ.US", date="2025-05-02", settle=None, qty=-100,
             proceeds=12000.0, cost=10000.0):
    return {"date": date, "date_settle": settle or date, "symbol": symbol,
            "qty": qty, "proceeds": proceeds, "cost": cost,
            "gain": proceeds - cost, "disallowed_amount": 0.0,
            "days_held": 100, "direction": "LONG", "commission": 0.0,
            "fee": 0.0, "account": "margin"}


def _reconcile(td, slip_text, entries, *argv):
    import contextlib
    from taxjson.bin.taxjson_reconcile_slips import main
    s = Path(td) / "slip.csv"
    s.write_text(slip_text, encoding="utf-8")
    g = Path(td) / "margin_gains.json"
    g.write_text(json.dumps({"transactions": entries, "summary": {}}))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main([str(s), "--gains", str(g), *argv])
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


_CA_ONLY = ("T5008", "Box 13", "boxes 20/21", "Bank of Canada",
            "blended ACB")


class TestReconcileSlipsCountry(unittest.TestCase):
    """A2-0423, A2-0744, A2-0747, A2-0753, A2-1292, A2-1294, A2-1295,
    A2-1331, A2-1337, A2-1348, A2-1349, A2-1350, A2-1351."""

    def test_country_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, err = _reconcile(
                td, "Symbol,Proceeds\nQZQ,12000\n", [_rs_sell()])
        self.assertEqual(code, 2)
        self.assertIn("--country", err)

    def test_wording_per_country(self):
        slip = "Symbol,Quantity,Proceeds,Cost\nQZQ,100,12000,9900\n"
        got = {}
        for c in ("canada", "usa"):
            with tempfile.TemporaryDirectory() as td:
                code, out, err = _reconcile(td, slip, [_rs_sell()],
                                            "--country", c)
            self.assertEqual(code, 0, err)
            got[c] = out
        for w in ("T5008 Box 13", "blended ACB"):
            self.assertIn(w, got["canada"])
        for w in _CA_ONLY:
            self.assertNotIn(w, got["usa"])
        self.assertIn("FIFO basis per account", got["usa"])

    def test_foreign_currency_refusal_per_country(self):
        got = {}
        for c, cur in (("canada", "USD"), ("usa", "CAD")):
            with tempfile.TemporaryDirectory() as td:
                code, out, err = _reconcile(
                    td, f"Symbol,Proceeds,Currency\nQZQ,12000,{cur}\n",
                    [_rs_sell()], "--country", c)
            self.assertEqual(code, 2)
            got[c] = err
        self.assertIn("Bank of Canada", got["canada"])
        self.assertIn("boxes 20/21", got["canada"])
        for w in _CA_ONLY:
            self.assertNotIn(w, got["usa"])
        self.assertIn("1099-B", got["usa"])
        self.assertIn("books are in USD", got["usa"])

    @rule("US-DATE-01")
    def test_usa_scopes_the_year_on_the_trade_date(self):
        # Traded 2024-12-31, settled 2025-01-02: on the 2024 1099-B.
        sale = _rs_sell(date="2024-12-31", settle="2025-01-02")
        with tempfile.TemporaryDirectory() as td:
            code, out, err = _reconcile(
                td, "Symbol,Proceeds\nQZQ,12000\n", [sale],
                "--country", "usa", "--year", "2024")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("MISSING_FROM_COMPUTED", out)

    @rule("CA-DATE-01")
    def test_canada_scopes_the_year_on_the_settle_date(self):
        sale = _rs_sell(date="2024-12-31", settle="2025-01-02")
        with tempfile.TemporaryDirectory() as td:
            code, out, err = _reconcile(
                td, "Symbol,Proceeds\nQZQ,12000\n", [sale],
                "--country", "canada", "--year", "2025")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("MISSING_FROM_COMPUTED", out)


# ------------------------------------------------ RBC parser notes (05)

def _rbc_rows():
    from test_rbc_parse_audit_2026_09 import HDR, row, RTS_EXP
    return HDR + "".join([
        row("December 31, 2025", "Distribution", "VDX", "VANGUARD X", "",
            "", "0", "CAD", "VANGUARD X 2025 NOTIONAL DISTRIBUTION "
            "ADJUSTMENT TO BOOK COST $2000.00"),
        row("December 31, 2025", "Distribution", "XYZ.UN", "XYZ TRUST", "",
            "", "0", "CAD", "RTC - XYZ TRUST RETURN OF CAPITAL ADJUSTMENT "
            "TO BOOK COST $30.00"),
        row("September 8, 2025", "Reorganization", "CSX.RT", "", "1", "",
            "0", "CAD", f"DIS - RTS CONSTELLO SOFTWARE INC {RTS_EXP} "
            f"{RTS_EXP} RTS DIST  ON       1 SHS REC 09/01/25 PAY "
            "09/08/25"),
        row("April 7, 2025", "Dividends", "RBF8411", "RBC INTL EQUITY O",
            "5", "", "", "USD", "DIV - Rbc International Equity Series O "
            "U$ (8411) As Of 04/07/25 Reinvest @ $20.00"),
        row("March 3, 2025", "Return of Capital", "ZZR", "ZZR TRUST", "",
            "", "0", "CAD", "ZZR TRUST RETURN OF CAPITAL ON 10 SHS"),
    ])


class TestRbcNotesFollowTheCountry(unittest.TestCase):
    """A2-0729, A2-0731, A2-0733, A2-0736, A2-1254, A2-1309, A2-1313,
    A2-1314, A2-1315, A2-1321, A2-1344, A2-1345, A2-1346, A2-1347: the
    same rows, the same booking; the notes' tax words are the project's
    country's (taxjson-brokerage --country, which `taxjson run` passes)."""

    _CA = ("ITA s.", "s.15(1)(c)", "T3", "T5", "ACB", "box 21", "box 42")

    def _brokerage(self, country):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "rbc.csv"
            f.write_text(_rbc_rows(), encoding="utf-8")
            argv = ["--brokerage", "rbc_direct", str(f)]
            if country:
                argv += ["--country", country]
            r = _module("taxjson.bin.taxjson_brokerage", *argv)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["transactions"], r.stderr

    @rule("CA-DIST-02")
    @rule("US-DIST-02")
    def test_dual_country_wording(self):
        ca_doc, ca = self._brokerage("canada")
        us_doc, us = self._brokerage("usa")
        neutral_doc, neutral = self._brokerage(None)
        # Booking is country-blind (parsers emit neutral facts).
        self.assertEqual(ca_doc, us_doc)
        self.assertEqual(ca_doc, neutral_doc)
        for w in ("s.15(1)(c)", "usually box 21", "raises its ACB",
                  "fund's T3", "T3/T5 slip"):
            self.assertIn(w, ca, w)
        for w in self._CA:
            self.assertNotIn(w, us, w)
            self.assertNotIn(w, neutral, w)
        for w in ("Form 1099-DIV", "raises its basis", "§305"):
            self.assertIn(w, us, w)

    def test_coverage_note_wording(self):
        from taxjson.lib.brokerages.rbc_direct import rbc_coverage_messages
        exports = [("rbc.csv", "2026-01-15", ["2025-03-03", "2025-12-30"],
                    set())]
        for country, has, lacks in (("canada", "2025 T3", None),
                                    ("usa", "Forms 1099-DIV", "T3"),
                                    (None, "tax slips", "T3")):
            msgs = " ".join(rbc_coverage_messages(
                exports, 2025, None, country=country))
            self.assertIn(has, msgs, country)
            if lacks:
                self.assertNotIn(lacks, msgs)
                self.assertNotIn("ACB", msgs)


# ---------------------------------- taxjson-brokerage stablecoins (05)

class TestBrokerageStablecoinDefault(unittest.TestCase):
    """A2-0742, A2-1238: without --country a USD stablecoin is property
    (the neutral answer, like --foreign-roc's cost reduction), with a
    note naming --country; canada keeps the cash model, usa property."""

    _CB = ("Transactions\n"
           "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
           "Price Currency,Price at Transaction,Subtotal,"
           "Total (inclusive of fees and/or spread),Fees and/or Spread,"
           "Notes\n"
           "a1,2025-06-01 12:00:00 UTC,Buy,USDC,1000,USD,$1.00,$1000.00,"
           "$1000.00,$0.00,Bought 1000 USDC\n")

    def _run(self, *flags):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "cb_2025.csv"
            f.write_text(self._CB, encoding="utf-8")
            r = _module("taxjson.bin.taxjson_brokerage", "--brokerage",
                        "coinbase", *flags, str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        txs = json.loads(r.stdout)["transactions"]
        return [t for t in txs if t.get("symbol") == "USDC"
                and t.get("action") == "BUYSELL"], r.stderr

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    def test_country_and_neutral_default(self):
        ca, ca_err = self._run("--country", "canada")
        us, _ = self._run("--country", "usa")
        none, none_err = self._run()
        self.assertEqual(ca, [])            # US-dollar cash
        self.assertEqual(len(us), 1)        # property
        self.assertEqual(none, us)          # neutral: property
        self.assertIn("no --country given", none_err)
        self.assertNotIn("no --country given", ca_err)

    def test_help_names_both_choices(self):
        r = _module("taxjson.bin.taxjson_brokerage", "--help")
        flat = " ".join(r.stdout.split())
        self.assertIn("stablecoins", flat)
        self.assertIn("--foreign-roc", flat)


# ------------------------------- holdings basis notes: web + export (05/06)

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAVE_WEB = True
except Exception:          # pragma: no cover - extra not installed
    _HAVE_WEB = False


def _web_project(tmp, country):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    sfx, cur = ((".US", "USD") if country == "usa" else (".TO", "CAD"))
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_base.json").write_text(json.dumps(
        {"transactions": [{"action": "BUYSELL", "date": "2026-06-01",
                           "date_settle": "2026-06-01",
                           "symbol": f"QZQ{sfx}", "quantity": 10,
                           "price": 10.0, "net_amount": 100.0,
                           "currency": cur, "account": "margin"}]}))
    (root / "reports" / "margin_holdings.toml").write_text(
        f'[[holding]]\nsymbol = "QZQ{sfx}"\nquantity = 10.0\n'
        f'total_cost = 100.0\ncost_per_share = 10.0\ncurrency = "{cur}"\n')
    return root, f"QZQ{sfx}"


_CA_BASIS = ("s.47", "ACB", "superficial", "pre-blend", "Canadian")


@unittest.skipUnless(_HAVE_WEB, "web extra not installed")
class TestWebHoldingsBasisNote(unittest.TestCase):
    """A2-0755, A2-1250, A2-1327, A2-1339, A2-1374, A2-1375, A2-1376,
    A2-1300, A2-1353 (web half)."""

    def _pages(self, country):
        from taxjson.web.app import create_app
        from taxjson.web.context import ProjectContext
        with tempfile.TemporaryDirectory() as tmp:
            root, sym = _web_project(tmp, country)
            c = TestClient(create_app(ProjectContext.load(root)),
                           base_url="http://127.0.0.1")
            with redirect_stderr(io.StringIO()):
                pages = [c.get("/holdings"),
                         c.get(f"/holdings/margin/{sym}"),
                         c.post("/whatif", data={
                             "account": "margin", "symbol": sym,
                             "qty": "5", "price": "8"})]
        for r in pages:
            self.assertEqual(r.status_code, 200, r.text[:500])
        return [r.text for r in pages]

    @rule("CA-ACB-01")
    @rule("US-BASIS-01")
    def test_dual_country_basis_notes(self):
        ca = self._pages("canada")
        us = self._pages("usa")
        self.assertIn("s.47 blended", ca[0])
        self.assertIn("s.47 blended", ca[1])
        self.assertIn("Canadian project", ca[2])
        for page in us:
            for w in _CA_BASIS:
                self.assertNotIn(w, page, w)
        self.assertIn("FIFO", us[0])
        self.assertIn("FIFO", us[1])
        self.assertIn("FIFO per account", us[2])


class TestHoldingsTomlBasisNote(unittest.TestCase):
    """A2-1248, A2-1289, A2-1290, A2-1291, A2-1328, A2-1336, A2-1300 /
    A2-1353 (export half)."""

    def _toml(self, *flags):
        gains = {"transactions": [], "inventory": [
            {"account": "margin", "currency": "USD", "symbol": "QZQ.US",
             "qty": 10, "total_cost": 100.0,
             "position_start_date": "2026-06-01"}]}
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps(gains))
            r = _module("taxjson.bin.taxjson_export", "--holdings-toml",
                        "--base-gains", str(g), *flags, str(g))
        self.assertEqual(r.returncode, 0, r.stderr)
        line = [ln for ln in r.stdout.splitlines()
                if ln.startswith("base_cost_basis")]
        self.assertEqual(len(line), 1, r.stdout)
        return line[0]

    @rule("CA-ACB-01")
    @rule("US-BASIS-01")
    def test_dual_country_wording(self):
        ca = self._toml("--country", "canada")
        us = self._toml("--country", "usa")
        neutral = self._toml()
        self.assertIn("s.47 blend", ca)
        self.assertIn("superficial-loss", ca)
        for text in (us, neutral):
            for w in ("s.47", "superficial", "ACB"):
                self.assertNotIn(w, text, w)
        self.assertIn("wash-sale", us)
        self.assertIn("§1091", us)


# ---------------------------------------------------- harvest (06)

class TestHarvestShelteredAdd(unittest.TestCase):
    """A2-1298 (US SH_ADD counts an IRA buy since sold) and A2-1299
    (the SH_ADD legend states each country's own rule)."""

    def _run(self, country, td, as_json=True):
        from datetime import date, timedelta
        from unittest import mock
        from taxjson.bin.taxjson_harvest import main as harvest_main
        today = date.today()
        iso = lambda d: (today + timedelta(days=d)).isoformat()
        sfx, cur = (".US", "USD") if country == "usa" else (".TO", "CAD")
        sym = f"QZL{sfx}"
        g = Path(td) / "margin_gains_wash.json"
        g.write_text(json.dumps({"summary": {"year": today.year},
                                 "transactions": [], "inventory": [
            {"symbol": sym, "qty": 100, "total_cost": 5000.0,
             "currency": cur, "last_acq_date": iso(-120),
             "position_start_date": iso(-120)}]}))
        # The sheltered account bought 10 twelve days ago and sold
        # them seven days ago: nothing held now.
        sh = Path(td) / "ira_gains.json"
        sh.write_text(json.dumps({"summary": {"year": today.year},
                                  "inventory": [], "transactions": [
            {"symbol": sym, "qty": 10.0, "date": iso(-7),
             "acquired_date": iso(-12), "direction": "LONG",
             "gain": 10.0, "proceeds": 410.0, "cost": 400.0,
             "account": "ira"}]}))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}), \
                redirect_stderr(err):
            import contextlib
            with contextlib.redirect_stdout(out):
                rc = harvest_main(
                    [str(g), "--no-ibkr", "--country", country,
                     "--base-currency", cur, "--sheltered", str(sh)]
                    + (["--json"] if as_json else []),
                    fetchers=[lambda rem: {s: (40.0, "yfinance")
                                           for s in rem}])
        self.assertEqual(rc, 0, err.getvalue())
        if not as_json:
            return out.getvalue(), iso(-12)
        row = json.loads(out.getvalue())["rows"][0]
        return row, iso(-12)

    @rule("US-WASH-11")
    def test_usa_sold_ira_buy_still_shows(self):
        with tempfile.TemporaryDirectory() as td:
            row, bought = self._run("usa", td)
        self.assertEqual(row["sheltered_last_add"], bought)
        self.assertEqual(row["sheltered_qty"], 0.0)

    @rule("CA-SL-02")
    @rule_absent("US-WASH-11", country="canada")
    def test_canada_sold_registered_buy_does_not_count(self):
        with tempfile.TemporaryDirectory() as td:
            row, _ = self._run("canada", td)
        self.assertIsNone(row["sheltered_last_add"])

    def test_legend_per_country(self):
        got = {}
        for c in ("canada", "usa"):
            with tempfile.TemporaryDirectory() as td:
                text, _ = self._run(c, td, as_json=False)
            got[c] = " ".join(text.split())
        self.assertIn("still holds 30 days after the sale", got["canada"])
        self.assertNotIn("IRA", got["canada"])
        self.assertIn("held or since sold", got["usa"])
        self.assertNotIn("registered", got["usa"])


# ------------------------------------------- income-rule refusal (06)

class TestIncomeRuleRefusalWording(unittest.TestCase):
    """A2-1306: the example follows the setting's country, and a CLI
    flag is not called a [settings] key."""

    def test_examples_and_sources(self):
        from taxjson.lib.income_dating import (
            IncomeRules, IncomeRulesError, parse_ric_entries)
        with self.assertRaises(IncomeRulesError) as cm:
            IncomeRules.from_settings({"country": "usa",
                                       "ric_january_dividends": "XYZ"})
        self.assertIn('["XYZ.US"]', str(cm.exception))
        self.assertNotIn(".TO", str(cm.exception))
        with self.assertRaises(IncomeRulesError) as cm:
            IncomeRules.from_settings({"country": "canada",
                                       "corporate_distributions": "XYZ"})
        self.assertIn('["XYZ.TO"]', str(cm.exception))
        with self.assertRaises(IncomeRulesError) as cm:
            parse_ric_entries(["XYZ 2024-02-01"],
                              key="--ric-january-dividend")
        self.assertTrue(str(cm.exception).startswith(
            "--ric-january-dividend:"), str(cm.exception))
        self.assertNotIn("[settings]", str(cm.exception))


# ------------------------------------------- ticker-map summary (06)

class TestTickerMapSummaryNoSuffixGuess(unittest.TestCase):
    """A2-1367: summary mode never 'maps' a .US listing to .TO."""

    def test_summary_keeps_listings(self):
        from taxjson.bin.taxjson_ticker_map import generate_summary
        from taxjson.lib.core import TaxTransaction
        txs = [TaxTransaction(action="BUYSELL", date="2026-01-05",
                              time="10:00:00", symbol=s, quantity=1,
                              price=1.0, net_amount=1.0, currency="USD",
                              account="brk")
               for s in ("QZQ.US", "U.19SEP25.26.P")]
        m = generate_summary(txs)["mappings"]
        self.assertEqual(m["QZQ.US"], "QZQ.US")
        self.assertEqual(m["U.19SEP25.26.P"], "U250919P00026000")


# ------------------------------- standalone timing defaults (A2-1361)

class TestStandaloneTimingDefaults(unittest.TestCase):

    def _t1135(self, *flags):
        import contextlib
        from taxjson.bin.taxjson_t1135 import main as t1135_main
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({
                "metadata": {"target_currency": "CAD"},
                "transactions": [{
                    "date": "2025-01-05", "date_settle": "2025-01-06",
                    "symbol": "XYZ.TO", "action": "BUY", "quantity": 10,
                    "price": 10.0, "net_amount": -100.0,
                    "currency": "CAD", "fees": 0.0}]}))
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    redirect_stderr(err):
                rc = t1135_main(["--year", "2025", *flags, str(b)])
        self.assertEqual(rc, 0, err.getvalue())
        return err.getvalue()

    def test_t1135_says_it_defaults_to_close_timing(self):
        self.assertIn("--option-premium-timing not given", self._t1135())
        self.assertNotIn("not given", self._t1135(
            "--option-premium-timing", "grant",
            "--option-grant-since", "2025"))

    def test_gen_phantoms_passes_the_project_timing(self):
        root = None
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\noption_grant_timing_since = 2025\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [{
                    "action": "BUYSELL", "date": "2025-03-03",
                    "date_settle": "2025-03-04", "symbol": "QZQ.TO",
                    "quantity": -10, "price": 10.0, "net_amount": 100.0,
                    "currency": "CAD", "account": "margin"}]}))
            r = _module("taxjson.bin.taxjson_run", "-C", str(root),
                        "find-missing-history", "--write-missing-history",
                        str(root / "phantoms.new.json"))
        self.assertNotIn("--option-premium-timing not given", r.stderr,
                         r.stdout + r.stderr)


# --------------------------------- explain on a merged book (06)

class TestExplainMergedBookBasis(unittest.TestCase):
    """A2-0155, A2-0421, A2-0441, A2-0444 (fixed on main by eed1786;
    pinned here): `taxjson-explain` on the merged taxable book traces
    the sale the way the books do — FIFO per account in the US, one
    s.47 pool in Canada — never FIFO pooled across accounts."""

    def _list(self, country):
        rows = [
            {"action": "BUYSELL", "date": "2024-01-02", "time": "10:00:00",
             "date_settle": "2024-01-02", "symbol": "QZQ.US",
             "quantity": 100, "price": 10.0, "net_amount": 1000.0,
             "currency": "USD", "account": "a1", "fee": 0.0},
            {"action": "BUYSELL", "date": "2024-06-03", "time": "10:00:00",
             "date_settle": "2024-06-03", "symbol": "QZQ.US",
             "quantity": 100, "price": 20.0, "net_amount": 2000.0,
             "currency": "USD", "account": "a2", "fee": 0.0},
            {"action": "BUYSELL", "date": "2025-03-03", "time": "10:00:00",
             "date_settle": "2025-03-03", "symbol": "QZQ.US",
             "quantity": -50, "price": 15.0, "net_amount": 750.0,
             "currency": "USD", "account": "a2", "fee": 0.0},
        ]
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "merged.json"
            f.write_text(json.dumps({"transactions": rows}))
            timing = (["--option-premium-timing", "close"]
                      if country == "canada" else [])
            r = _module("taxjson.bin.taxjson_explain", "--country", country,
                        *timing, "--list", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        line = [ln for ln in r.stdout.splitlines() if "QZQ.US" in ln]
        self.assertEqual(len(line), 1, r.stdout)
        return line[0]

    @rule("US-BASIS-01")
    def test_usa_fifo_per_account(self):
        line = self._list("usa")
        self.assertIn("cost=   1000.0000", line)
        self.assertIn("gain=   -250.0000", line)

    @rule("CA-ACB-01")
    def test_canada_one_pool(self):
        line = self._list("canada")
        self.assertIn("cost=    750.0000", line)


if __name__ == "__main__":
    unittest.main()
