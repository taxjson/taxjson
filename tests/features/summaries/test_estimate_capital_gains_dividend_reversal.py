"""Backlog fix round 7 (wave C) of 2026-10-29, group `forms`: form
export, carryover, the estimate and the per-account reports.

TJ-0993  the standalone form export refuses gains files of more than
         one tax year without --year.
TJ-1002  taxjson-carryover --claimed-year refuses an implausible year
         (2205), as the claimed file does.
TJ-1018  a Canadian partial claim (99.98 of 100.00 of losses in whole
         cents) leaves its 0.02 carried forward.
TJ-1020  the estimate takes a mapped capital-gains dividend and its
         reversal out of the dividends as one net amount.
TJ-1008  the long-option report (reports/leaps.rpt, taxjson-leaps-gains)
         counts an unknown-cost option sale the pipeline moved to
         manual_reporting_required.
TJ-1011  taxjson-sum-gains FILE groups an option on a held class share
         (SYN.B.TO) under that share, as stdin does.
TJ-1013  a per-account position reopened after a sale that settled
         across a split starts on its own buy's date.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).

Formerly: test_fix_1029_r7c_forms
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _env():
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _tjs(root, *args):
    """`tjs -C ROOT ARGS` in a child process (stdin closed, unwrapped)."""
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=_env(),
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, country="canada", year=2025, tt=None, settings="",
             accounts=("margin",), ticker_map=""):
    root = Path(root)
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text(ticker_map)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    if tt is not None:
        (root / "inputs" / accounts[0] / "trades.tt").write_text(tt)
    return root


def _run(test, root):
    r = _tjs(root, "run", "--no-input")
    test.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


class TestEstimateBox18Reversal(unittest.TestCase):
    """TJ-1020: a 100 SYN.TO dividend and a later -60 correction, all of
    it capital-gains dividends ([[capital_gains_dividends]] amount =
    "all"). The estimate floored each row's move: max(0, 40 - 100) = 0,
    then 0 + 60: 60 of eligible dividends no history had.

    Formerly: test_fix_1029_r7c_forms.TestEstimateBox18Reversal"""

    def _estimate(self, d, divs):
        p = _project(
            Path(d), tt=("BUYSELL 2025-01-02 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                         + divs),
            settings="option_grant_timing_since = 2025\n")
        with (p / "taxjson.toml").open("a") as f:
            f.write('\n[[capital_gains_dividends]]\nsymbol = "SYN.TO"\n'
                    'year = 2025\namount = "all"\n'
                    '[estimate]\nother_income = 90000\n')
        _run(self, p)
        r = _tjs(p, "estimate", "--province", "ON", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["estimate"]

    def test_reversal_matches_net_payment(self):
        with tempfile.TemporaryDirectory() as d:
            rev = self._estimate(
                Path(d) / "rev",
                "DIVIDEND 2025-06-02 10:00:00 SYN.TO 100 CAD 1 100\n"
                "DIVIDEND 2025-06-03 10:00:00 SYN.TO 100 CAD .6 -60\n")
            net = self._estimate(
                Path(d) / "net",
                "DIVIDEND 2025-06-02 10:00:00 SYN.TO 100 CAD .4 40\n")
        self.assertEqual(net["eligible_dividends"], 0.0)
        for key in ("eligible_dividends", "grossed_eligible",
                    "investment_income"):
            self.assertAlmostEqual(rev[key], net[key], places=2, msg=key)
        self.assertEqual(rev["tax_with"], net["tax_with"])


if __name__ == "__main__":
    unittest.main()
