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


def _project(root, rows, settings="", country="canada", year=2025,
             name="trades.tt"):
    """A one-account project whose books are `rows` (a .tt file, or
    another export named `name`)."""
    root = Path(root)
    (root / "inputs" / "margin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{base}"\n'
        f'source_currencies = []\n{settings}\n'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text("")
    (root / "inputs" / "margin" / name).write_text(rows)
    return root


_Q_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
             "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
             "Activity Type,Account #,Account Type\n")


class TestSlipAuditSuggestedFile(unittest.TestCase):
    """TJ-0429: in an open year of the default layout (one folder of
    exports for every year, `inputs_dir = "../inputs"`), slip-audit said
    to add the ROC line to inputs/margin/slip-audit.tt, a folder that
    year never reads: followed literally it changed nothing. It names
    ../inputs/margin/slip-audit.tt, the folder the year reads, and says
    every year reading it reads the line.

    Formerly: test_fix_1029_r2_income.TestSlipAuditSuggestedFile"""

    def test_the_shared_folder_is_named_and_the_line_applies(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "fd"
            r = _tjs(None, "init", "--country", "canada", "--year",
                     "2025", str(top))
            self.assertEqual(r.returncode, 0, r.stderr)
            (top / "inputs" / "margin" / "q.csv").write_text(
                _Q_HEADER
                + "2025-01-10,2025-01-13,Buy,ZZE.TO,ZZE SAMPLE TRUST UNITS,"
                  "100,10.00,-1000.00,0.00,-1000.00,CAD,Trades,99900001,"
                  "Margin\n"
                + "2025-06-27,2025-06-27,DIS,ZZE.TO,ZZE SAMPLE TRUST UNITS "
                  "DIST ON 100 SHS,0,0,20.00,0,20.00,CAD,Dividends,"
                  "99900001,Margin\n")
            year = top / "2025"
            (year / "inputs" / "slips").mkdir(parents=True)
            (year / "inputs" / "slips" / "slips.toml").write_text(
                'year = 2025\n\n[[slip]]\ntype = "T3"\n'
                'issuer = "ZZE SAMPLE TRUST"\naccount = "margin"\n'
                'security = "ZZE.TO"\nboxes = { 42 = 20.00 }\n')
            r = _tjs(year, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(year, "slip-audit", "--json")
            (tt,) = json.loads(r.stdout)["suggestions"]["tt_lines"]
            self.assertEqual(tt["file"], "../inputs/margin/slip-audit.tt")
            self.assertTrue(tt["shared"])
            r = _tjs(year, "slip-audit")
            self.assertIn("Add to ../inputs/margin/slip-audit.tt", r.stdout)
            self.assertIn("each year reading it reads the line", r.stdout)
            # Followed literally, from the year folder, it brings the
            # books to the slip.
            (year / tt["file"]).write_text("\n".join(tt["lines"]) + "\n")
            r = _tjs(year, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(year, "slip-audit", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual(doc["suggestions"]["tt_lines"], [])
            line = [ln for a in doc["accounts"] for g in a["groups"]
                    for b in g["buckets"] for ln in b["lines"]
                    if ln["category"] == "roc"]
            self.assertEqual([ln["status"] for ln in line], ["ok"], line)

    def test_a_project_of_its_own_names_its_own_folder(self):
        from taxjson.lib.slip_audit import suggest_tt_file
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "")
            self.assertEqual(suggest_tt_file(root, "margin"),
                             {"file": "inputs/margin/slip-audit.tt",
                              "shared": False})


if __name__ == "__main__":
    unittest.main()
