"""Re-audit-2 fixes, filing-reports list (helper D): the run pipeline
(crypto-sends stage, raw pass phantoms, blended .sum diagnostics, run
state banner), `sum`/`estimate` account handling and flag guards,
instalment dates, and the estimate's Canadian-issuer test and wording.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_d
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths

REPO_ROOT = _paths.REPO


def _taxjson(root, *a, env=None):
    e = {**os.environ, "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
         "PYTHONPATH": str(REPO_ROOT / "src")}
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=e)


QT_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
          "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
          "Account #,Activity Type,Account Type\n")


def _qt_row(date, action, sym, qty, price, net, desc="D", cur="CAD"):
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},55500001,Trades,Individual\n")  # pii-ok


def _qt_project(root, accounts, year=2025, country="canada", extra=""):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cur = "CAD" if country == "canada" else "USD"
    cfg = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
           f'base_currency = "{cur}"\nsource_currencies = []\n' + extra)
    for name, body in accounts.items():
        typ = "taxable"
        if isinstance(body, tuple):
            typ, body = body
        cfg += f'[accounts.{name}]\ntype = "{typ}"\n'
        d = root / "inputs" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "questrade.csv").write_text(QT_HDR + body)
    (root / "taxjson.toml").write_text(cfg)
    return root


class TestBlendedWashSumDiagnostics(unittest.TestCase):
    """A2-0654, A2-1117: the filing-basis <acct>_wash.sum of a blended
    account does not carry the isolated per-account pass's s.40(3)
    notes (the blended s.47 pool booked no deemed gain); the per-account
    <acct>.sum baseline keeps them.

    Formerly: test_fix_a2_filrep_d.TestBlendedWashSumDiagnostics"""

    ROC = "ISHARES XEI RETURN OF CAPITAL ON 10 SHS"

    def _run(self, accounts):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _qt_project(Path(td.name) / "p", accounts)
        r = _taxjson(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        rep = root / "reports"
        return ((rep / "acctA.sum").read_text(),
                (rep / "acctA_wash.sum").read_text())

    @rule(R.CA_ACB_07)
    def test_a2_0654_roc_beyond_isolated_acb(self):
        pre, wash = self._run({
            "acctA": _qt_row("2025-01-10", "Buy", "XEI.TO", 10, 10.0, -100.0)
            + _qt_row("2025-03-31", "DIV", "XEI.TO", 0, 0, 150.0,
                      desc=self.ROC),
            "acctB": _qt_row("2025-02-10", "Buy", "XEI.TO", 100, 20.0,
                             -2000.0)})
        self.assertIn("s.40(3)", pre)
        self.assertNotIn("s.40(3)", wash)

    @rule(R.CA_ACB_07)
    def test_a2_1117_roc_on_empty_account_pool(self):
        pre, wash = self._run({
            "acctA": _qt_row("2025-01-10", "Buy", "XEI.TO", 10, 10.0, -100.0)
            + _qt_row("2025-02-10", "Sell", "XEI.TO", -10, 12.0, 120.0)
            + _qt_row("2025-03-31", "DIV", "XEI.TO", 0, 0, 5.0,
                      desc=self.ROC),
            "acctB": _qt_row("2025-01-15", "Buy", "XEI.TO", 100, 20.0,
                             -2000.0)})
        self.assertIn("EMPTY pool", pre)
        self.assertNotIn("EMPTY pool", wash)
        self.assertNotIn("s.40(3)", wash)


if __name__ == "__main__":
    unittest.main()
