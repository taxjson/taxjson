"""Backlog fix round 2026-10-29 (round 2), group `income`: dividend
forecasts (`tjs div-estimate`), dividend corrections and refunded
withholding, tips, slip-audit and roc-sum (T3 return of capital) and
the final-gains required-field contract.

TJ-0547  div-estimate: a dated rename carries the dividend history to
         the new symbol.
TJ-0548  div-estimate: withholding posted in another currency than its
         dividend is converted at the pay date's rate before the rate is
         read.
TJ-0549  div-estimate: a dividend paid after a split on shares counted
         on a record date before it is read in today's shares.
TJ-0552  div-estimate: a correction's refunded withholding moves with
         the gross it reverses.
TJ-0558  div-estimate: one broker account's short charge in a combined
         account is an expense, not a reversal of its own dividend,
         beside another broker account's larger long.
TJ-0429  slip-audit: the suggested .tt lines name the folder the year
         reads (../inputs/<account>/ in the shared-exports layout).
TJ-0420  slip-audit: a typed T3's box 42 is spread over the year's
         distributions when units were sold between them, not all put
         after the sale.
TJ-0427  roc-sum: a hand-typed .tt ADJUST type=roc line is a manual row.
TJ-0562  tips: TFSA-US-DIV follows the issuer's domicile, not the US
         listing alone.
TJ-0495  a gains document's income row without its amount or date is
         refused, an explicit 0 kept.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN.*, ZZ*), account names (margin), broker account
ids (99900001), amounts under 1,000. Planning numbers (CA-PLAN-06 /
US-PLAN-07) unless a test says otherwise.

Formerly: test_fix_1029_r2_income
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from tax_rules import rule
from taxjson.rules import ids as R
import _paths

REPO = _paths.REPO


def _tjs(root, *args):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               NO_COLOR="1")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args),
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=600)


class TestTipsIssuerDomicile(unittest.TestCase):
    """TJ-0562: a TFSA holds SYN.US, a Canadian issuer listed only in the
    US (no Canadian listing in the books), declared `T1135 SYN.US CAN`.
    tips said "US dividend payer inside a TFSA: the 15% US withholding
    is unrecoverable here". US withholding follows the issuer's
    domicile, not its listing.

    Formerly: test_fix_1029_r2_income.TestTipsIssuerDomicile"""

    def _tips(self, td, ticker_map="", issuer_country=""):
        root = Path(td) / "p"
        (root / "work").mkdir(parents=True)
        (root / "reports").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.tfsa]\ntype = "sheltered"\n'
            'plan = "tfsa"\n')
        (root / "reports" / "tfsa_holdings.toml").write_text(
            'schema_version = "1.2"\n[[holding]]\nsymbol = "SYN.US"\n'
            'quantity = 100.0\ncurrency = "USD"\ntotal_cost = 100.0\n'
            'cost_per_share = 1.0\n')
        row = {"action": "DIVIDEND", "date": "2025-03-01",
               "symbol": "SYN.US", "quantity": 100, "gross_amount": 10.0,
               "net_amount": 10.0, "currency": "USD"}
        if issuer_country:
            row["issuer_country"] = issuer_country
        (root / "work" / "tfsa_raw.json").write_text(
            json.dumps({"transactions": [row]}))
        (root / "ticker.map").write_text(ticker_map)
        r = _tjs(root, "tips", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return [f for f in json.loads(r.stdout)["findings"]
                if f["check"] == "TFSA-US-DIV"]

    @rule(R.CA_SCAN_01)
    def test_a_declared_canadian_issuer_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._tips(td, "T1135 SYN.US CAN\n"), [])
        with tempfile.TemporaryDirectory() as td:
            # The export's ISIN says Canada (IB).
            self.assertEqual(self._tips(td, issuer_country="CA"), [])
        with tempfile.TemporaryDirectory() as td:
            # Another foreign country: no US withholding claimed either.
            self.assertEqual(self._tips(td, "T1135 SYN.US IRL\n"), [])

    @rule(R.CA_SCAN_01)
    def test_a_declared_us_issuer_is_flagged_outright(self):
        with tempfile.TemporaryDirectory() as td:
            (f,) = self._tips(td, "T1135 SYN.US USA\n")
        self.assertIn("US dividend payer inside a TFSA: the 15% US "
                      "withholding is unrecoverable", f["message"])
        self.assertNotIn("if its issuer", f["message"])

    @rule(R.CA_SCAN_01)
    def test_no_domicile_on_record_is_a_condition(self):
        with tempfile.TemporaryDirectory() as td:
            (f,) = self._tips(td)
        self.assertIn("if its issuer is US-domiciled", f["message"])
        self.assertIn("T1135 SYN.US COUNTRY", f["message"])


if __name__ == "__main__":
    unittest.main()
