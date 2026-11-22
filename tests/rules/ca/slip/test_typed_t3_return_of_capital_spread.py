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


_T3R = (_Q_HEADER
        + "2024-01-10,2024-01-12,Buy,ZZE.TO,ZZE SAMPLE ETF UNITS,200,25.00,"
          "-5000.00,-4.95,-5004.95,CAD,Trades,99900090,Margin\n"
        + "".join(
            f"{p},{p},DIV,ZZE.TO,ZZE SAMPLE ETF DIST ON {n} SHS REC {r} PAY "
            f"{p[5:7]}/{p[8:]}/24,0,0,{a},0,{a},CAD,Dividends,99900090,"
            f"Margin\n"
            for p, r, n, a in (("2024-03-28", "03/22/24", 200, "60.00"),
                               ("2024-06-27", "06/21/24", 200, "60.00"),
                               ("2024-09-27", "09/20/24", 200, "60.00")))
        + "2024-11-15,2024-11-18,Sell,ZZE.TO,ZZE SAMPLE ETF UNITS,-100,27.00,"
          "2700.00,-4.95,2695.05,CAD,Trades,99900090,Margin\n"
        + "2024-12-30,2024-12-30,DIV,ZZE.TO,ZZE SAMPLE ETF DIST ON 100 SHS "
          "REC 12/27/24 PAY 12/30/24,0,0,90.00,0,90.00,CAD,Dividends,"
          "99900090,Margin\n")
_T3R_SLIPS = ('year = 2024\n\n[[slip]]\ntype = "T3"\n'
              'issuer = "ZZE Sample Funds"\naccount = "margin"\n'
              'currency = "CAD"\nsecurity = "ZZE.TO"\n'
              'boxes = { 26 = 150.00, 21 = 40.00, 42 = 80.00 }\n')


class TestTypedT3ReturnOfCapitalAfterASale(unittest.TestCase):
    """TJ-0420: 200 ZZE.TO units, distributions of 60 in March, June and
    September, 100 units sold November 15, a distribution of 90 on
    December 30; the typed T3 returns 80 of capital (box 42). All 80 was
    suggested on December 30, after the sale: the units sold kept an ACB
    the return of capital should have lowered (gain 192.58). Spread in
    proportion to the distributions (17.78, 17.78, 17.78, 26.66), 53.34
    lowers the ACB before the sale: the gain is 2700.00 - 2475.80 -
    4.95 = 219.25 and the 100 units kept end at 2449.14. TJ-0427: the
    applied .tt lines are manual rows in roc-sum, not the broker's.

    Formerly: test_fix_1029_r2_income.TestTypedT3ReturnOfCapitalAfterASale"""

    def _run(self, root, *args):
        r = _tjs(root, *args)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r

    @rule(R.CA_SLIP_03, R.CA_INC_DATE_ROC_TRUST)
    def test_spread_over_the_distributions_and_manual_in_roc_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", _T3R, year=2024,
                            name="questrade_2024.csv")
            (root / "inputs" / "slips").mkdir()
            (root / "inputs" / "slips" / "slips.toml").write_text(_T3R_SLIPS)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(self._run(root, "slip-audit", "--json").stdout)
            (tt,) = doc["suggestions"]["tt_lines"]
            adj = [ln for ln in tt["lines"] if ln.startswith("ADJUST")]
            self.assertEqual(adj, [
                "ADJUST 2024-03-28 16:00:00 ZZE.TO CAD -17.78 type=roc "
                "record=2024-03-22",
                "ADJUST 2024-06-27 16:00:00 ZZE.TO CAD -17.78 type=roc "
                "record=2024-06-21",
                "ADJUST 2024-09-27 16:00:00 ZZE.TO CAD -17.78 type=roc "
                "record=2024-09-20",
                "ADJUST 2024-12-30 16:00:00 ZZE.TO CAD -26.66 type=roc "
                "record=2024-12-27"])
            self.assertTrue(any("sold on 2024-11-15" in n
                                and "fund's breakdown" in n
                                for n in doc["suggestions"]["notes"]),
                            doc["suggestions"]["notes"])
            (root / tt["file"]).write_text("\n".join(tt["lines"]) + "\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(self._run(root, "slip-audit", "--json").stdout)
            self.assertEqual(doc["suggestions"]["tt_lines"], [])
            self.assertEqual(doc["issues"], [])
            fe = json.loads(self._run(root, "form-export", "--json").stdout)
            (row,) = [x for x in fe["rows"] if x["symbol"] == "ZZE.TO"]
            self.assertAlmostEqual(row["acb"], 2475.80, places=2)
            self.assertAlmostEqual(row["gain"], 219.25, places=2)
            # TJ-0427: the four typed lines are manual, not the broker's.
            rs = json.loads(self._run(root, "roc-sum", "--json").stdout)
            (z,) = rs["rows"]
            self.assertEqual((z["roc_rows"], z["manual_rows"],
                              z["dist_rows"]), (0, 4, 0))
            self.assertAlmostEqual(z["capital_returned"], 80.0)

    @rule(R.CA_SLIP_03)
    def test_no_sale_between_keeps_one_line_on_the_last(self):
        """The control: with the sale after the last distribution's
        record date, one line on the last distribution, as before."""
        csv = _T3R.replace("2024-11-15,2024-11-18,Sell", "SKIP").splitlines()
        csv = "\n".join(ln for ln in csv if not ln.startswith("SKIP")) + "\n"
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", csv, year=2024,
                            name="questrade_2024.csv")
            (root / "inputs" / "slips").mkdir()
            (root / "inputs" / "slips" / "slips.toml").write_text(_T3R_SLIPS)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(self._run(root, "slip-audit", "--json").stdout)
            (tt,) = doc["suggestions"]["tt_lines"]
            self.assertEqual(
                [ln for ln in tt["lines"] if ln.startswith("ADJUST")],
                ["ADJUST 2024-12-30 16:00:00 ZZE.TO CAD -80.00 type=roc "
                 "record=2024-12-27"])


if __name__ == "__main__":
    unittest.main()
