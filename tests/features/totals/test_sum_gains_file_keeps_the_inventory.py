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


def _module(mod, *args, cwd=None, stdin=None):
    """A standalone tool (`taxjson-form-export` ...) in a child process."""
    return subprocess.run(python_module_argv(mod) + [str(a) for a in args],
                          capture_output=True, text=True, env=_env(),
                          cwd=cwd, input=stdin,
                          stdin=None if stdin is not None
                          else subprocess.DEVNULL, timeout=600)


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


class TestSumGainsFileKeepsInventory(unittest.TestCase):
    """TJ-1011: SYN.B.TO held since 2024, a SYN call written and bought
    back in 2025 for a gain of 10. taxjson-sum-gains on the gains file
    grouped it under a phantom SYN.TO (the merge dropped the inventory
    the class-share alias reads); on stdin, under SYN.B.TO.

    Formerly: test_fix_1029_r7c_forms.TestSumGainsFileKeepsInventory"""

    def test_file_and_stdin_agree(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "p",
                tt=("BUYSELL 2024-01-02 10:00:00 SYN.B.TO 100 CAD 1 100 0\n"
                    "BUYSELL 2025-01-02 10:00:00 SYN251219C00002000.TO -1 "
                    "CAD .2 20 0\n"
                    "BUYSELL 2025-02-03 10:00:00 SYN251219C00002000.TO 1 "
                    "CAD .1 10 0\n"),
                settings="option_grant_timing_since = 2025\n")
            _run(self, p)
            g = p / "work" / "margin_gains.json"
            by_file = _module("taxjson.bin.taxjson_sum_gains", "--json", g)
            self.assertEqual(by_file.returncode, 0, by_file.stderr)
            by_stdin = _module("taxjson.bin.taxjson_sum_gains", "--json",
                               stdin=g.read_text())
            self.assertEqual(by_stdin.returncode, 0, by_stdin.stderr)
            f = json.loads(by_file.stdout)["ticker_stats"]
            s = json.loads(by_stdin.stdout)["ticker_stats"]
            self.assertIn("SYN.B.TO", s)
            self.assertEqual(sorted(f), sorted(s))
            self.assertNotIn("SYN.TO", f)
            sums = [x for x in p.rglob("margin*.sum")]
            self.assertTrue(sums)
            for x in sums:
                text = x.read_text()
                self.assertIn("SYN.B.TO", text, x.name)
                self.assertNotRegex(text, r"(?m)^\s*SYN\.TO\b", x.name)


if __name__ == "__main__":
    unittest.main()
