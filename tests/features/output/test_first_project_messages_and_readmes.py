"""New-user guidance (owner-approved gaps from the getting-started study).

1. A closing summary at the end of `tjs run`: sales with no purchase
   not covered by missing history, $0-cost positions (sold or still
   held), transfer-ins kept out with no cost, accounts with open
   positions and no holdings check, income on a security the books do
   not hold — each with its command; silent when clean; the counts in
   reports/run_summary.json. The short-position NOTE of a taxable account
   is on the console.
2. `tjs sum` warns about uncovered sales with no purchase; "tainted"
   is "unknown cost" in every message (JSON keys kept, aliases added).
3. A transfer-in from outside the books takes the broker's STATED book
   value as its cost (never a market value), with an ATTENTION that a
   covering .tt line silences (CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
4. Messages: the crypto time-zone refusal, the sanity mismatch hint, the
   stage-failure line, a stale missing-history line, the inputs/
   README per broker.
5. A US scaffold fetches no CAD rates.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_newuser_guidance
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli, settings_for, tx
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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


_CLEAN_TT = ("BUYSELL  2025-01-10  09:30:00  ABC.TO  10  CAD  20  200  0\n"
             "BUYSELL  2025-03-10  09:30:00  ABC.TO  -10  CAD  25  250  0\n")


# ---------------------------------------------------------------- item 4

class TestMessages(unittest.TestCase):
    """Formerly: test_fix_newuser_guidance.TestMessages"""

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
        from taxjson.cli.main import _stage_description
        from taxjson.pipeline.tools import _cmd
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

    def test_stale_missing_history_entry_is_reported(self):
        from taxjson.lib.missing_history import (
            report_missing_history_log, stale_missing_history_entries,
            synthesize_openings)
        rows = [tx("BUYSELL", "2024-01-02", "CMP.TO", 10, 100,
                   currency="CAD"),
                tx("BUYSELL", "2025-02-10", "CMP.TO", -10, 120,
                   currency="CAD")]
        st = stale_missing_history_entries(rows, {("CMP.TO", "margin")},
                                           complete=True)
        self.assertEqual([(e.symbol, e.reason) for e in st],
                         [("CMP.TO", "complete")])
        _ops, log = synthesize_openings(rows, {("CMP.TO", "margin")})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            report_missing_history_log([log], {"margin"})
        self.assertIn("warning: ATTENTION: missing_history.tt lists "
                      "CMP.TO / margin, but its rows never go short any "
                      "more — the purchase is in the books now",
                      err.getvalue())

    def test_several_stale_entries_are_one_line(self):
        from taxjson.lib.missing_history import (
            report_missing_history_log, synthesize_openings)
        rows = [tx("BUYSELL", "2024-01-02", s, 10, 100, currency="CAD")
                for s in ("CMA.TO", "CMB.TO")]
        _ops, log = synthesize_openings(
            rows, {("CMA.TO", "margin"), ("CMB.TO", "margin")})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            report_missing_history_log([log], {"margin"})
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("lists 2 entries whose rows never go short any more "
                      "(CMA.TO / margin, CMB.TO / margin)", lines[0])

    def test_stale_entry_listed_by_find_missing_history_and_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "inputs/margin/start.tt": _CLEAN_TT,
                "inputs/margin/missing_history.tt":
                    "OPENING 2025-01-09 ABC.TO 10 cost=unknown\n"})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            f = cli(root, "find-missing-history")
            self.assertIn("STALE in missing_history.tt", f.stdout)
            self.assertIn("ABC.TO margin", f.stdout)

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
        from taxjson.cli.setup import cmd_init
        with tempfile.TemporaryDirectory() as td:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch("taxjson.lib.timezones.system_timezone",
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
            # Fewer shares in the books and none short: purchases from
            # before the data, which find-missing-history cannot see —
            # an opening balance (backlog TJ-0423).
            self.assertIn("bought before your data", r.stdout)
            # (the `! ` line names the command: docs/output-style.md)
            self.assertIn("— tjs opening margin FILE", r.stdout)


if __name__ == "__main__":
    unittest.main()
