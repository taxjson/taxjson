"""Decisions of 2026-10-26, group `cents`: the figures the return files
(each sale's cents) shown where the engine's unrounded totals were.

- TJ-0208 `tjs carryover`'s NET GAIN(LOSS) for the project year is
  the RETURN row of `tjs sum`, so its carryforward balance is the one
  close-year's lock and the estimate carry forward; a prior year's lock
  gives the RETURN row close-year recorded;
- TJ-0209 the default `sum` view says in one line that SUBTOTAL (TOTAL in
  the one-table layout) adds the account rows and RETURN each sale's
  cents, when the two differ;
- TJ-0212 close-year's CLOSED block and `years --json` show income as
  `sum`'s taxable SUBTOTAL adds it (each account's cents);
- TJ-0226 `estimate --json` carries `losses_carried_back` and
  `losses_carried_forward`; `losses_unused` stays the figure before
  the carry-back.

The project: two taxable accounts in a years layout (inputs/ beside
2023/). margin: a 100.00 loss and one unit sold of each of three 3-unit
lots (each row rounds its loss up by a third of a cent); cash: a 10.00
gain, to the cent. RETURN -93.99, the engine's total -94.00. Each
account a dividend of 10.004 (each account 10.00, the lock's total
20.01); [carryover] claimed carries 50.00 of the loss back to 2022. Synthetic data only (made-up tickers, amounts
under a thousand).

Formerly: test_decisions_2026_10_26_cents
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))

_MARGIN = """# made-up trades
BUYSELL 2023-02-01 09:30:00 ZZQB.TO 50 CAD 10.00 -500.00 0.00
BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD 8.00 400.00 0.00
BUYSELL 2023-03-01 09:30:00 ZZQR.TO 3 CAD 3.3333 -10.00 0.00
BUYSELL 2023-03-01 09:30:00 ZZQT.TO 3 CAD 3.3333 -10.00 0.00
BUYSELL 2023-03-01 09:30:00 ZZQU.TO 3 CAD 3.3333 -10.00 0.00
BUYSELL 2023-04-03 09:30:00 ZZQR.TO -1 CAD 2.00 2.00 0.00
BUYSELL 2023-05-01 09:30:00 ZZQT.TO -1 CAD 2.00 2.00 0.00
BUYSELL 2023-06-01 09:30:00 ZZQU.TO -1 CAD 2.00 2.00 0.00
DIVIDEND 2023-06-15 16:00:00 ZZQB.TO 50 CAD 0.20008 10.004
"""
_CASH = """# made-up trades
BUYSELL 2023-01-10 09:30:00 ZZQK.TO 10 CAD 10.00 -100.00 0.00
DIVIDEND 2023-06-15 16:00:00 ZZQK.TO 10 CAD 1.0004 10.004
BUYSELL 2023-09-01 09:30:00 ZZQK.TO -5 CAD 12.00 60.00 0.00
"""
_TOML = ('[settings]\nyear = 2023\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         'tax_date = "settle"\nprovince = "ON"\n'
         'inputs_dir = "../inputs"\n\n'
         '[accounts.margin]\ntype = "taxable"\n\n'
         '[accounts.cash]\ntype = "taxable"\n\n'
         '[estimate]\nother_income = 50000\n\n'
         '[carryover]\nclaimed = { 2022 = 50.00 }\n')


def _env(**extra):
    """This checkout's code, offline, captured width unless `extra`
    says otherwise."""
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _cli(root, *args, **env):
    """`tjs -C ROOT ARGS` as a child of THIS tree's code."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *map(str, args)])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(**env), timeout=900)


class _Project(unittest.TestCase):
    """The project, run once for the class; a test that writes works on
    its own copy (`self.copy()`)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "base"
        for acct, text in (("margin", _MARGIN), ("cash", _CASH)):
            (top / "inputs" / acct).mkdir(parents=True)
            (top / "inputs" / acct / "trades.tt").write_text(text)
        (top / "2023").mkdir()
        (top / "2023" / "taxjson.toml").write_text(_TOML)
        r = _cli(top / "2023", "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.top, cls.y = top, top / "2023"

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r

    def copy(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        top = Path(t.name) / "p"
        shutil.copytree(self.top, top, symlinks=True)
        return top, top / "2023"

    def sum_json(self, y=None):
        return json.loads(self.ok(_cli(y or self.y, "sum", "--json")).stdout)


class TestCloseYearIncome(_Project):
    """Formerly: test_decisions_2026_10_26_cents.TestCloseYearIncome"""
    def test_tj0212_closed_block_and_years_show_the_subtotal(self):
        """TJ-0212: close-year showed the lock's income (20.01, the
        accounts' unrounded sum rounded once) where `sum`'s DIVIDEND
        total read 20.00; it and `years --json` show 20.00 now, and the
        lock's totals, which check-filed compares, are unchanged."""
        top, y = self.copy()
        r = self.ok(_cli(y, "close-year", "--yes"))
        line = next(ln for ln in r.stdout.splitlines()
                    if ln.strip().startswith("Income:"))
        self.assertTrue(line.rstrip().endswith("20.00"), line)
        lock = json.loads((y / "filed" / "2023.json").read_text())
        self.assertEqual(lock["totals"]["income"], 20.01)
        yrs = json.loads(self.ok(_cli(top, "years", "--json")).stdout)
        y23 = next(e for e in yrs["years"] if e["year"] == 2023)
        self.assertEqual(y23["totals"]["income"], 20.0)
        d = self.ok(_cli(y, "close-year", "--yes", "--force", "--details"))
        self.assertIn("the lock's total the drift check compares is 20.01",
                      " ".join(d.stdout.split()))


if __name__ == "__main__":
    unittest.main()
