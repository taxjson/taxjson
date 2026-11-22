"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


# ------------------------------------------------------------- TJ-0418

KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
          "margin,misc,ledgers\n")
# ZZC: bought 1.0, sold 0.9999999 (a sale of "all" leaving 1e-07 units:
# rounding residue). ZZD: 2.5 units still held.
KR = (KR_HDR
      + "TXA1,OA1,ZZC/CAD,2025-01-15 10:00:00.1234,buy,limit,40,"
        "40,0,1.0,,,\n"
      + "TXA2,OA2,ZZD/CAD,2025-02-15 10:00:00.1234,buy,limit,10,"
        "25,0,2.5,,,\n"
      + "TXA3,OA3,ZZC/CAD,2025-03-15 10:00:00.1234,sell,limit,50,"
        "49.999995,0,0.9999999,,,\n")


def _kraken_project(td):
    root = Path(td)
    (root / "inputs" / "kr1").mkdir(parents=True)
    (root / "inputs" / "kr1" / "kr_trades.csv").write_text(KR)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.kr1]\ntype = "taxable"\ncrypto = true\n')
    return root


# ------------------------------------------------------------- TJ-0414

def _tjs(*args, cwd=REPO_ROOT):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(args)),
        cwd=cwd, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


# ------------------------------------------------------------- TJ-0253

class TestUndetectedFileInSharedInputs(unittest.TestCase):
    """TJ-0253: a file no rule routes, in exports shared by every year,
    was named inputs/margin/x.csv from the year folder, where it is
    ../inputs/margin/x.csv.

    Formerly: test_fix_1029_messages.TestUndetectedFileInSharedInputs"""

    UNKNOWN = "zz_col_a,zz_col_b,zz_col_c\n1,2,3\n"

    def test_year_folder_names_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "demo"
            r = _tjs("init", "--demo", str(demo))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (demo / "inputs" / "margin" / "zz_unknown.csv").write_text(
                self.UNKNOWN)
            r = _tjs("-C", str(demo / "2024"), "run", "--account",
                     "margin", "--no-input")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("cannot detect broker for "
                      "../inputs/margin/zz_unknown.csv.", r.stdout + r.stderr)

    def test_single_folder_project_names_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = _kraken_project(td)
            (root / "inputs" / "kr1" / "zz_unknown.csv").write_text(
                self.UNKNOWN)
            r = _tjs("-C", str(root), "run", "--no-input")
        self.assertIn("cannot detect broker for "
                      "inputs/kr1/zz_unknown.csv.", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
