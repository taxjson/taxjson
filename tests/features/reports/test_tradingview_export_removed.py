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
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths

REPO = _paths.REPO
SRC = REPO / "src"


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
    """Formerly: test_fix_notv.TestExportRemoved"""

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
        from taxjson.pipeline import stages as pipeline_stages
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
                pipeline_stages._sweep_retired_exports(root / "reports")
            left = sorted(p.name for p in ex.iterdir())
        self.assertEqual(left, ["my_notes.txt"])
        self.assertIn("removed 4 old watchlist file(s)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
