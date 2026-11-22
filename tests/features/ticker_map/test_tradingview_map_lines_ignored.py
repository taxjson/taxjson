"""The TradingView watchlist export is removed (owner decision).

- `taxjson-export --tradingview` / `--tv-map` are gone: asking for them
  is a clear error, not a silent fallback to another format.
- `tjs run` writes no reports/exports/*_TV.txt, and removes the ones
  an earlier run left behind (an outdated watchlist must not linger).
- A TRADINGVIEW line already in a ticker.map is not an error: it is
  ignored, with one NOTE per run asking to delete it.
- An old tv_exchange.map no longer stops the project (nothing in it is
  read any more); `tjs migrate` renames it to
  tv_exchange.map.migrated without converting it.
- --report / --holdings-toml are unchanged. (The Seeking Alpha and
  FastGraph exports were removed later: features.run.test_watchlist_exports_removed.)

Synthetic data only.

Formerly: test_fix_notv
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli
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


class TestTickerMapTradingViewLines(unittest.TestCase):
    """Formerly: test_fix_notv.TestTickerMapTradingViewLines"""

    def test_lines_are_ignored_not_problems(self):
        from taxjson.lib.map_file import _parse_map_file, map_file_problems
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


if __name__ == "__main__":
    unittest.main()
