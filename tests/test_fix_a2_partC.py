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
        from taxjson.lib import phantom_holdings as ph
        with tempfile.TemporaryDirectory() as tmp:
            base = self._project(tmp, _US_TOML)
            self.assertEqual(ph.tax_date_near(base), "trade")
            self.assertEqual(ph.account_types_near(base),
                             {"margin": False, "ira": True})

    def test_unparseable_toml_stops_not_no_project(self):
        from taxjson.lib import phantom_holdings as ph
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


if __name__ == "__main__":
    unittest.main()
