"""New-user guidance (owner-approved gaps from the getting-started study).

1. A closing summary at the end of `taxjson run`: sales with no purchase
   not covered by missing_history.json, $0-cost positions (sold or still
   held), transfer-ins kept out with no cost, accounts with open
   positions and no holdings check, income on a security the books do
   not hold — each with its command; silent when clean; the counts in
   work/run_summary.json. The short-position NOTE of a taxable account
   is on the console.
2. `taxjson sum` warns about uncovered sales with no purchase; "tainted"
   is "unknown cost" in every message (JSON keys kept, aliases added).
3. A transfer-in from outside the books takes the broker's STATED book
   value as its cost (never a market value), with an ATTENTION that a
   covering .tt line silences (CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
4. Messages: the crypto time-zone refusal, the sanity mismatch hint, the
   stage-failure line, a stale missing_history.json entry, the inputs/
   README per broker.
5. A US scaffold fetches no CAD rates.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, gains_both, settings_for, tx


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _qrow(td, action, sym, desc, qty, price, gross, net, cur="CAD",
          act="Trades", acct="55500001"):                      # pii-ok
    return (f"{td},{td},{action},{sym},{desc},{qty},{price},{gross},0.00,"
            f"{net},{cur},{act},{acct},Margin\n")


def _project(tmp, *, country="canada", year=2025, accounts=None,
             files=None, extra_settings=""):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, source_currencies=[])
        + extra_settings
        + (accounts or '[accounts.margin]\ntype = "taxable"\n'))
    for rel, text in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


# A Canadian margin account with one of each first-run finding.
_CA_QT = _QH + (
    _qrow("2025-02-03", "Buy", "ABC.TO", "ABC CORP", 10, "20.00",
          "-200.00", "-200.00")
    + _qrow("2025-03-03", "TF6", "XYZ.TO",
            "XYZ CORP TRANSFER BOOK VALUE 5000.00", 100, "0.00", "0.00",
            "0.00", act="Transfers")
    + _qrow("2025-03-04", "TF6", "QRS.TO", "QRS CORP TRANSFER", 50, "0.00",
            "0.00", "0.00", act="Transfers")
    + _qrow("2025-04-01", "Sell", "XYZ.TO", "XYZ CORP", -100, "60.00",
            "6000.00", "6000.00")
    + _qrow("2025-05-01", "Sell", "SMA.TO", "SMA CORP", -20, "40.00",
            "800.00", "800.00"))
_CA_TT = ("BUYSELL  2025-01-10  09:30:00  ZRO.TO  5  CAD  0  0  0\n"
          "DIVIDEND 2025-06-30  09:30:00  DIV.TO  100  CAD  0.25  25.00\n")
_CLEAN_TT = ("BUYSELL  2025-01-10  09:30:00  ABC.TO  10  CAD  20  200  0\n"
             "BUYSELL  2025-03-10  09:30:00  ABC.TO  -10  CAD  25  250  0\n")


# ---------------------------------------------------------------- item 4

class TestMessages(unittest.TestCase):

    def test_crypto_timezone_refusal_offers_deleting_the_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            env_tz = os.environ.pop("TAXJSON_LOCAL_TZ", None)
            try:
                r = cli(root, "run", "--no-input")
            finally:
                if env_tz:
                    os.environ["TAXJSON_LOCAL_TZ"] = env_tz
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("local_timezone", r.stderr)
            self.assertIn("If you have no crypto, delete the "
                          "[accounts.crypto] section", r.stderr)

    def test_stage_failure_names_the_step_not_the_argv(self):
        from taxjson.bin.taxjson_run import _cmd, _stage_description
        d = _stage_description(
            _cmd("taxjson-brokerage")
            + ["--account", "margin", "--brokerage", "ib", "--strict",
               "--account-type", "taxable", "--rates", "/w/to_base.csv",
               "/p/inputs/margin/a.csv", "/p/inputs/margin/b.csv"])
        self.assertEqual(d, "reading the broker files for account margin "
                            "(a.csv, b.csv)")
        self.assertNotIn("-m", d)
        self.assertEqual(
            _stage_description(_cmd("taxjson-gains") + [
                "--country", "canada", "--year", "2025",
                "/w/margin_base.json"]),
            "computing the gains (margin_base.json)")

    def test_init_readme_says_what_to_download(self):
        from taxjson.lib.config_template import input_readme
        m = input_readme("canada", "margin")
        for want in ("Interactive Brokers", "Activity Statement",
                     "Questrade", "RBC Direct Investing", "Webull",
                     "ALL the history", "positions report", "T5008"):
            self.assertIn(want, m)
        c = input_readme("usa", "crypto")
        self.assertIn("Trades AND Ledgers", c)
        self.assertNotIn("Webull", c)
        r = input_readme("usa", "roth")
        self.assertIn("wash-sale rule", r)
        self.assertTrue(all(len(ln) <= 72 for ln in
                            (m + c + r).splitlines()))

    def test_init_writes_the_per_account_readme(self):
        import argparse
        from unittest import mock
        from taxjson.bin.taxjson_run import cmd_init
        with tempfile.TemporaryDirectory() as td:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch("taxjson.lib.config_template.system_timezone",
                               return_value="America/Toronto"):
                cmd_init(argparse.Namespace(path=td, dir=".", force=False,
                                            country="canada", year=2025))
            text = (Path(td) / "inputs" / "tfsa" / "README.txt").read_text()
            self.assertIn("superficial-loss rule", text)
            self.assertIn("Activity Statement", text)

    def test_sanity_mismatch_names_missing_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "work/margin_gains.json": json.dumps(
                    {"summary": {"year": "2025"}, "transactions": [],
                     "inventory": [{"symbol": "MIS.TO", "qty": 5,
                                    "total_cost": 1.0}]}),
                "h.toml": '[[holding]]\nsymbol = "MIS.TO"\nquantity = 20\n'})
            r = cli(root, "sanity", f"margin={root / 'h.toml'}")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("usually means missing history", r.stdout)
            self.assertIn("taxjson find-missing-history", r.stdout)


if __name__ == "__main__":
    unittest.main()
