"""Shared test helpers: a one-folder Canadian project with a .tt book and a holdings snapshot, and the CLI, for tjs migrate.

Shared from: test_fix_1029_r7_migrate
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO_ROOT = _paths.REPO

CFG = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
       'country = "canada"\nbase_currency = "CAD"\n'
       'source_currencies = []\n')
TT = ("BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
      "BUYSELL 2025-06-02 10:00:00 SYN.TO -10 CAD 15 150 0\n")
SNAP = ('[meta]\ngenerated_at = 2025-12-31T21:00:00Z\n[[holding]]\n'
        'symbol = "SYN.TO"\nquantity = 10\ncurrency = "CAD"\n'
        'total_cost = 100\n')


def tjs(root, *args):
    """`tjs -C root <args>`, captured at width 0, offline."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               NO_COLOR="1",
               PYTHONPATH=str(REPO_ROOT / "src") + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=root, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


def project(p: Path, year=2025, accounts=("margin",), extra=""):
    """A single-folder Canadian project in `p` with taxable accounts
    (each with an inputs/<name>/ folder) and an empty ticker.map."""
    p.mkdir(parents=True, exist_ok=True)
    (p / "taxjson.toml").write_text(
        CFG.format(year=year) + extra + "".join(
            f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    for a in accounts:
        (p / "inputs" / a).mkdir(parents=True)
    (p / "ticker.map").write_text("")


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.top = Path(self._td.name).resolve()

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        return r
