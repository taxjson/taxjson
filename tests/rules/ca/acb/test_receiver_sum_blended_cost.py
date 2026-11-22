"""Fix round 2026-10-29 (round 4), group views: derived views and
reports.

TJ-0279  <account>.sum asked for a `.tt OPENING cost=unknown` line
         (MANUAL REPORTING REQUIRED) for a sale of units moved in from
         another of your accounts: their cost is in the blended figures
         (<account>_wash.sum, `tjs sum`). They are a block of their
         own now, and its holdings table says "unknown", not 0.00.
TJ-0598  reports/<account>_holdings.toml: units moved in from another
         of your accounts take the cost the run has after the passes
         across accounts (`cost_source = "blended"`), where round 3
         left it unknown.
TJ-0629  `tjs stats` / `win-rates` counted a registered account's
         withdrawal in kind (a TRANSFER out, `transfers = true`, which
         the books book as a sale at its value) as a closed trade, with
         a win or a loss. It is no trade: its P/L is counted apart.
TJ-0634  The US option-replacement warning's covered_shares carried
         float noise (9.999999999999998) into the gains JSON: the loss's
         open part is a money ratio's, and quantities are saved
         unrounded. Share counts are now at the engine's share
         precision (six decimals).

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r4_views
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _flat(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------
# The per-account .sum and units moved in from your own account
# (TJ-0279)
# --------------------------------------------------------------------

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _qt(acct_no: str, cur: str, *rows) -> str:
    """A Questrade activity export: rows of (date, action, description,
    quantity, price, net amount, activity type)."""
    out = _QH
    for d, act, desc, q, px, net, kind in rows:
        out += (f"{d},{d},{act},ZZQ,{desc},{q},{px:.2f},{net:.2f},0.00,"
                f"{net:.2f},{cur},{kind},{acct_no},Margin\n")
    return out


def _moved_project(root: Path, country: str, *qb_rows) -> Path:
    """qa buys 100 ZZQ at 2.00 and moves them out on 2024-03-01; qb
    receives them the next day (a move of your own), then `qb_rows`."""
    from tax_rules.dual import settings_for
    cur = "CAD" if country == "canada" else "USD"
    accounts = {
        "qa": _qt("99900001", cur,
                  ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                   "Trades"),
                  ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                   0.0, 0.0, "Transfers")),
        "qb": _qt("99900002", cur,
                  ("2024-03-02", "TF6", "ZZQ CORP TRANSFER IN", 100, 0.0,
                   0.0, "Transfers"), *qb_rows)}
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + "".join(f'[accounts.{n}]\ntype = "taxable"\n' for n in accounts))
    for n, text in accounts.items():
        (root / "inputs" / n).mkdir(parents=True)
        (root / "inputs" / n / "questrade_2024.csv").write_text(text)
    return root


_SALE = ("2024-06-03", "Sell", "ZZQ CORP", -40, 3.0, 120.0, "Trades")


class TestOwnSumSaysTheCostIsBlended(unittest.TestCase):
    """TJ-0279: qb sells 40 of the 100 moved in from qa. Its own .sum
    asked for a `.tt OPENING cost=unknown` line and find-missing-history
    for them: they have no purchase missing, their cost is in the
    blended figures (qb_wash.sum, `tjs sum`).

    Formerly: test_fix_1029_r4_views.TestOwnSumSaysTheCostIsBlended"""

    def _check(self, country, why):
        cur = "CAD" if country == "canada" else "USD"
        sym = "ZZQ.TO" if country == "canada" else "ZZQ.US"
        with tempfile.TemporaryDirectory() as td:
            root = _moved_project(Path(td) / "p", country, _SALE)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            own = (root / "reports" / "qb.sum").read_text()
            flat = _flat(own)
            self.assertIn(
                f"MOVED IN FROM YOUR OWN ACCOUNT — 1 sale(s) of units "
                f"moved in from qa, proceeds 120.00 {cur}: NOT in the rows "
                f"or totals above, and nothing to report by hand from "
                f"here. This account's own books have no cost for them; "
                f"the figures you file have it: qb_wash.sum and `tjs "
                f"sum` ({why}).", flat)
            self.assertIn(f"{sym} 2024-06-03 40 120.00 qb", flat)
            for gone in ("MANUAL REPORTING REQUIRED", "OPENING cost=unknown",
                         "find-missing-history"):
                self.assertNotIn(gone, own)
            # Its holdings report: the 60 left at a cost unknown here,
            # never 0.00.
            self.assertIn(f"{sym} 60 unknown unknown {cur}", flat)
            self.assertIn("unknown: some of the units have no cost in "
                          "these books", flat)
            # The figures you file have the cost: 40 x 2.00.
            wash = json.loads((root / "work" / "qb_gains_wash.json")
                              .read_text())
            self.assertEqual([(t["qty"], round(t["cost"], 2))
                              for t in wash["transactions"]],
                             [(40.0, 80.0)])
            self.assertFalse(wash.get("manual_reporting_required"))

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada(self):
        self._check("canada", "one ACB across your taxable accounts, "
                              "CA-ACB-OWN-MOVE")

    @rule(R.US_BASIS_05)
    def test_usa(self):
        self._check("usa", "the lots' basis carried by the move, "
                           "US-BASIS-05")


if __name__ == "__main__":
    unittest.main()
