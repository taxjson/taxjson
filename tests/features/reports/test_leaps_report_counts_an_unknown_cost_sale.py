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


class TestOptionReportCountsManualRows(unittest.TestCase):
    """TJ-1008: a long call held before the data at an unknown cost
    (OPENING cost=unknown), sold for 30. reports/leaps.rpt and
    taxjson-leaps-gains printed TOTAL 0.00 with no note: the sale is in
    the gains file's manual_reporting_required, which they never read.

    Formerly: test_fix_1029_r7c_forms.TestOptionReportCountsManualRows"""

    def test_report_and_standalone_count_it(self):
        opt = "SYN260116C00001000.TO"
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "p",
                tt=(f"OPENING 2024-12-31 {opt} 1 cost=unknown\n"
                    f"BUYSELL 2025-01-17 10:00:00 {opt} -1 CAD .3 30 0\n"),
                settings="option_grant_timing_since = 2025\n")
            _run(self, p)
            g = p / "work" / "margin_gains.json"
            doc = json.loads(g.read_text())
            self.assertEqual([m["symbol"] for m in
                              doc["manual_reporting_required"]], [opt])
            rpt = (p / "reports" / "leaps.rpt").read_text()
            self.assertIn("NOTE: 1 disposition(s) with an unknown cost",
                          rpt)
            r = _module("taxjson.bin.taxjson_leaps_gains", g)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("NOTE: 1 disposition(s) with an unknown cost",
                          r.stdout)
            # The same document on stdin, and the short-call report:
            # a long call's sale is not a covered call's.
            r = _module("taxjson.bin.taxjson_leaps_gains",
                        stdin=g.read_text())
            self.assertIn("NOTE: 1 disposition(s)", r.stdout)
            r = _module("taxjson.bin.taxjson_ccd_gains", g)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("unknown cost", r.stdout)

    def test_unit_inline_and_moved_row_counted_once(self):
        from taxjson.bin._option_gains_report import process_data
        row = {"id": "x1", "symbol": "SYN260116C00001000.TO", "qty": 1.0,
               "proceeds": 30.0, "direction": "LONG", "currency": "CAD",
               "date": "2025-01-17"}
        groups = {}
        process_data({"transactions": [dict(row, tainted=True, gain=0.0,
                                            cost=0.0)],
                      "manual_reporting_required": [row]}, groups,
                     direction="LONG", calls_only=False)
        self.assertEqual(groups.get("_tainted"), 1)


if __name__ == "__main__":
    unittest.main()
