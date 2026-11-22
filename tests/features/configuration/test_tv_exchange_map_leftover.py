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


def _project(root: Path) -> Path:
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT + "\n".join(r.format(a=ACCT) for r in _ROWS) + "\n")
    (root / "taxjson.toml").write_text(_TOML)
    return root


class TestTvExchangeMapLeftover(unittest.TestCase):
    """Formerly: test_fix_notv.TestTvExchangeMapLeftover"""

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
        self.assertIn("tjs migrate", err)

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
