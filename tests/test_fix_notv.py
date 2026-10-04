"""The TradingView watchlist export is removed (owner decision).

- `taxjson-export --tradingview` / `--tv-map` are gone: asking for them
  is a clear error, not a silent fallback to another format.
- `taxjson run` writes no reports/exports/*_TV.txt, and removes the ones
  an earlier run left behind (an outdated watchlist must not linger).
- A TRADINGVIEW line already in a ticker.map is not an error: it is
  ignored, with one NOTE per run asking to delete it.
- An old tv_exchange.map no longer stops the project (nothing in it is
  read any more); `taxjson migrate` renames it to
  tv_exchange.map.migrated without converting it.
- --report / --holdings-toml are unchanged. (The Seeking Alpha and
  FastGraph exports were removed later: test_fix_noexports.)

Synthetic data only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli

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

_NOTE = "TradingView export removed"


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


class TestExportRemoved(unittest.TestCase):

    def test_tradingview_flags_are_a_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = _gains(Path(td))
            for args in (("--tradingview", g),
                         ("--report", "--tv-map", Path(td) / "m", g),
                         ("--tv-map=x", "--holdings-toml", g)):
                with self.subTest(args=args):
                    r = _export(*args)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("TradingView", r.stderr)
                    self.assertIn("removed", r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertEqual(r.stdout, "")

    def test_run_sweeps_stale_tradingview_files(self):
        from taxjson.bin import taxjson_run
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ex = root / "reports" / "exports"
            ex.mkdir(parents=True)
            for n in ("AAll_TV.txt", "ALong_TV.txt",
                      "AOptionsShort_TV.txt", "AOptionsLong_TV.txt"):
                (ex / n).write_text("TSX:OLD\n")
            (ex / "my_notes.txt").write_text("mine\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                taxjson_run._sweep_retired_exports(root / "reports")
            left = sorted(p.name for p in ex.iterdir())
        self.assertEqual(left, ["my_notes.txt"])
        self.assertIn("removed 4 old watchlist file(s)", out.getvalue())


class TestTickerMapTradingViewLines(unittest.TestCase):

    def test_lines_are_ignored_not_problems(self):
        from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                    map_file_problems)
        from taxjson.lib.ticker_map import SIDE_KEYWORDS, read_side_rules
        from taxjson.lib.price_chain import load_yf_map
        self.assertNotIn("TRADINGVIEW", SIDE_KEYWORDS)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            # Well-formed, short and lower-case lines alike are ignored.
            p.write_text("TRADINGVIEW ABC.TO NEO  # old\n"
                         "QUOTE ABC.TO ABC-B.TO\n"
                         "tradingview XYZ.US\n"
                         "GLOBAL FB.US META.US\n")
            self.assertEqual(map_file_problems(p), [])
            _tm, problems, notes = _parse_map_file(p)
            self.assertEqual((problems, notes), ([], []))
            side = read_side_rules(p)
            self.assertEqual(side.problems, [])
            self.assertEqual(side.retired, ["ticker.map:1", "ticker.map:3"])
            self.assertEqual(load_yf_map([td]),
                             {"ABC.TO": ("ABC-B.TO", 1.0)})
            # Only TRADINGVIEW lines: nothing to look up.
            p.write_text("TRADINGVIEW ABC.TO NEO\n")
            self.assertTrue(read_side_rules(p).empty())

    def test_run_notes_once_and_writes_no_watchlist(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            (root / "ticker.map").write_text(
                "TRADINGVIEW XAW.TO TSX\nTRADINGVIEW ZZZ.US NYSE\n")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            ex = (root / "reports" / "exports").exists()
        err = r.stdout + r.stderr
        self.assertEqual(err.count(_NOTE), 1, err[-3000:])
        self.assertIn("ticker.map:1", err)
        self.assertIn("ticker.map:2", err)
        self.assertIn("delete these lines", err)
        self.assertFalse(ex)

    def test_run_without_tradingview_lines_says_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertNotIn(_NOTE, r.stdout + r.stderr)
        self.assertNotIn("tv_exchange.map", r.stdout + r.stderr)


class TestTvExchangeMapLeftover(unittest.TestCase):

    def test_leftover_file_does_not_stop_the_project(self):
        from taxjson.lib.price_chain import load_yf_map
        from taxjson.lib.migrate import legacy_files
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            (root / "tv_exchange.map").write_text("XAW.TO TSX\n")
            self.assertEqual(legacy_files(root), [])
            self.assertEqual(load_yf_map([root]), {})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r2 = cli(root, "sum", "--json")
            self.assertEqual(r2.returncode, 0, r2.stderr[-3000:])
        err = r.stdout + r.stderr
        self.assertEqual(err.count("tv_exchange.map"), 1, err[-3000:])
        self.assertIn("taxjson migrate", err)

    def test_migrate_renames_without_converting(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            (root / "ticker.map").write_text("GLOBAL FB.US META.US\n")
            (root / "tv_exchange.map").write_text("XAW.TO TSX\n")
            (root / "yf_ticker.map").write_text("ABC.TO ABC-B.TO\n")
            dry = cli(root, "migrate", "--dry-run")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertTrue((root / "tv_exchange.map").exists())
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            tm = (root / "ticker.map").read_text()
            names = sorted(p.name for p in root.iterdir())
        for out in (dry.stdout, r.stdout):
            self.assertIn("tv_exchange.map", out)
            self.assertIn("no longer used", out)
        self.assertIn("would move tv_exchange.map -> "
                      "tv_exchange.map.migrated", dry.stdout)
        self.assertIn("tv_exchange.map.migrated", names)
        self.assertNotIn("tv_exchange.map", names)
        self.assertNotIn("TRADINGVIEW", tm)
        self.assertIn("QUOTE ABC.TO ABC-B.TO", tm)

    def test_migrate_with_only_the_tradingview_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p")
            (root / "tv_exchange.map").write_text("XAW.TO TSX\n")
            r = cli(root, "migrate")
            names = sorted(p.name for p in root.iterdir())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tv_exchange.map.migrated", names)
        self.assertNotIn("ticker.map", names)


if __name__ == "__main__":
    unittest.main()
