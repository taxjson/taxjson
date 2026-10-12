"""The Seeking Alpha and FastGraph watchlist exports are removed (owner
decision; the TradingView one went first, see test_fix_notv).

- `taxjson-export --seekingalpha` / `--fastgraph` are gone: asking for
  either is a clear exit-2 error naming the removal. The tool now needs
  one of its remaining modes, `--report` or `--holdings-toml` (no mode
  used to print a bare ticker list, the watchlist path).
- `taxjson run` has no exports stage (no "==> exports"); a full run
  removes the *_SA.csv / *_FG.csv / *_TV.txt files an earlier run left in
  reports/exports/ and the folder once it is empty, in one line.
- `--report`, `--holdings-toml` and the holdings TOMLs `taxjson run`
  writes are unchanged.

Synthetic data only.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
ACCT = "55500001"  # pii-ok

_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type\n")
_ROWS = [
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XAW.TO,XAW ETF,"
    "100,10.00,-1000.00,0.00,-1000.00,CAD,{a},Trades,Individual margin",
    "2025-11-20 09:30:00 AM,2025-11-21 12:00:00 AM,Sell,XAW.TO,XAW ETF,"
    "-40,12.00,480.00,0.00,480.00,CAD,{a},Trades,Individual margin",
]
_TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\nprovince = "ON"\n\n'
         '[accounts.margin]\ntype = "taxable"\n')

_OLD_EXPORTS = (
    "AAll_SA.csv", "ALongUSD_SA.csv", "AOptionsShortCAD_SA.csv",
    "AAll_FG.csv", "AOptionsLong_FG.csv", "AAll_TV.txt", "ALong_TV.txt")


def _project(root: Path) -> Path:
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT + "\n".join(r.format(a=ACCT) for r in _ROWS) + "\n")
    (root / "taxjson.toml").write_text(_TOML)
    return root


def _export(*args, cwd=REPO):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_export",
         *map(str, args)], cwd=cwd, capture_output=True, text=True,
        env=env, stdin=subprocess.DEVNULL)


def _gains(d: Path) -> Path:
    g = d / "margin_gains.json"
    g.write_text(json.dumps({"inventory": [
        {"symbol": "ABC.TO", "qty": 10, "total_cost": 100.0,
         "currency": "CAD"},
        {"symbol": "XYZ.US", "qty": 5, "total_cost": 50.0,
         "currency": "USD"}]}))
    return g


def _stale(ex: Path) -> None:
    ex.mkdir(parents=True, exist_ok=True)
    for n in _OLD_EXPORTS:
        (ex / n).write_text("OLD:CA, OLD\n")


class TestExportFlagsRemoved(unittest.TestCase):

    def test_watchlist_flags_are_a_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(Path(td))
            for args, name in ((("--seekingalpha", g), "Seeking Alpha"),
                               (("--fastgraph", g), "FastGraph"),
                               (("--fastgraph", "--short", g), "FastGraph"),
                               (("--report", "--seekingalpha", g),
                                "Seeking Alpha")):
                with self.subTest(args=args):
                    r = _export(*args)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn(name, r.stderr)
                    self.assertIn("removed", r.stderr)
                    self.assertIn("--report", r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertEqual(r.stdout, "")

    def test_a_mode_is_required(self):
        """No mode printed a bare ticker list (the watchlist path); it is
        now refused, naming the two modes left."""
        with tempfile.TemporaryDirectory() as td:
            r = _export(_gains(Path(td)))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--report", r.stderr)
        self.assertIn("--holdings-toml", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_report_and_holdings_toml_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(Path(td))
            rep = _export("--report", g)
            usd = _export("--report", "--no-cad", g)
            toml = _export("--holdings-toml", "--account-name", "margin", g)
        self.assertEqual(rep.returncode, 0, rep.stderr)
        self.assertIn("ABC.TO", rep.stdout)
        self.assertIn("XYZ.US", rep.stdout)
        # The filters still apply to the report.
        self.assertEqual(usd.returncode, 0, usd.stderr)
        self.assertIn("XYZ.US", usd.stdout)
        self.assertNotIn("ABC.TO", usd.stdout)
        self.assertEqual(toml.returncode, 0, toml.stderr)
        self.assertIn('symbol = "ABC.TO"', toml.stdout)
        self.assertIn('symbol = "XYZ.US"', toml.stdout)

    def test_platform_formatter_is_gone(self):
        from taxjson.bin import taxjson_export
        from taxjson.lib import ticker_map
        self.assertFalse(hasattr(ticker_map, "format_ticker_for_platform"))
        self.assertFalse(hasattr(taxjson_export, "process_data_platform"))


class TestRunHasNoExportsStage(unittest.TestCase):

    def test_no_matrix_and_no_stage(self):
        from taxjson.bin import taxjson_run
        self.assertFalse(hasattr(taxjson_run, "_EXPORT_MATRIX"))
        self.assertFalse(hasattr(taxjson_run, "stage_exports"))

    def test_sweep_removes_old_files_and_the_empty_folder(self):
        from taxjson.bin import taxjson_run
        with tempfile.TemporaryDirectory() as td:
            reports = Path(td) / "reports"
            _stale(reports / "exports")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                taxjson_run._sweep_retired_exports(reports)
            self.assertFalse((reports / "exports").exists())
            self.assertTrue(reports.is_dir())
            # Nothing to sweep: silent.
            out2 = io.StringIO()
            with contextlib.redirect_stdout(out2):
                taxjson_run._sweep_retired_exports(reports)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertIn("reports/exports", lines[0])
        self.assertIn(str(len(_OLD_EXPORTS)), lines[0])
        self.assertIn("removed", lines[0])
        self.assertEqual(out2.getvalue(), "")

    def test_sweep_keeps_a_users_own_files(self):
        from taxjson.bin import taxjson_run
        with tempfile.TemporaryDirectory() as td:
            reports = Path(td) / "reports"
            ex = reports / "exports"
            _stale(ex)
            (ex / "my_notes.txt").write_text("mine\n")
            (ex / "AAll_SA.csv.bak").write_text("mine too\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                taxjson_run._sweep_retired_exports(reports)
            left = sorted(p.name for p in ex.iterdir())
        self.assertEqual(left, ["AAll_SA.csv.bak", "my_notes.txt"])
        self.assertEqual(len(out.getvalue().splitlines()), 1, out.getvalue())

    def test_full_run_sweeps_and_prints_no_exports_stage(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            _stale(root / "reports" / "exports")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            gone = not (root / "reports" / "exports").exists()
            holdings = (root / "reports" / "margin_holdings.toml").exists()
            # A second run has nothing left to sweep.
            r2 = cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr[-3000:])
            again = (root / "reports" / "exports").exists()
        out = r.stdout + r.stderr
        self.assertTrue(gone, out[-3000:])
        self.assertTrue(holdings)
        self.assertFalse(again)
        self.assertNotIn("==> exports", out)
        self.assertEqual(out.count("reports/exports"), 1, out[-3000:])
        self.assertNotIn("reports/exports", r2.stdout + r2.stderr)

    def test_single_account_run_names_no_exports(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            r = cli(root, "run", "--no-input", "--account", "margin")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertIn("single-account run", r.stderr)
        self.assertNotIn("exports", r.stderr)


if __name__ == "__main__":
    unittest.main()
