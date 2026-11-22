"""The tjs command audit of 2026-10-27, year-end records group: close-year,
handoff, carryover and check-filed agree with the run.

- TJ-0296 US `carryover` reads the filed locks before its gains run: a
  wash-sale basis add whose replacement was sold in a locked year is
  booked in the loss's year, as the pipeline books it (US-WASH-22);
- TJ-0323 US `carryover` names a crypto account's unknown-cost sales
  (warning, --json `excluded_unknown_cost` / `incomplete_years`) and
  counts its rows in `first_transaction_year`;
- TJ-0297 a US crypto account's close-year record (handoff.snapshot) is
  computed without the wash-sale rule, as the run computes it;
- TJ-0285 (TJ-0324) Canada: the record's Dec 31 `deferred` is what the
  sales before Dec 31 left of each superficial-loss addition;
- TJ-0298 close-year refuses a --filed-dispositions row of another year,
  and a replaced lock's kept row of another year, before any write;
- TJ-0274 (TJ-0336) a lock's `return_row` is checked for its shape like
  every other locked figure; check-filed's OK says it is not recomputed.

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).

Formerly: test_fix_1027_yearend
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))


def _env(**extra):
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _cli(root, *args):
    """`tjs -C ROOT ARGS` as a child of THIS tree's code."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *map(str, args)])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(), timeout=900)


# ------------------------------------------------------------- TJ-0298
class TestFiledDispositionsYear(unittest.TestCase):
    """TJ-0298, the reader: a row dated outside the closing year.

    Formerly: test_fix_1027_yearend.TestFiledDispositionsYear"""

    def _csv(self, text):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "filed.csv"
        p.write_text(text)
        return p

    def test_another_years_row_is_refused_by_line(self):
        from taxjson.lib import handoff
        p = self._csv("symbol,date,qty,proceeds,cost,gain\n"
                      "ZZQ.TO,2025-06-02,10,200,100,100\n"
                      "ZZR.TO,2024-06-03,10,100,400,-300\n")
        with self.assertRaises(ValueError) as cm:
            handoff.load_filed_dispositions(p, 2025)
        msg = str(cm.exception)
        self.assertIn("line 3 (ZZR.TO 2024-06-03)", msg)
        self.assertIn("outside 2025", msg)
        # Without a year (a reader that is not closing one): as before.
        self.assertEqual(len(handoff.load_filed_dispositions(p)), 2)

    def test_rows_outside(self):
        from taxjson.lib import filed_locks as FLK
        self.assertEqual(FLK.filed_rows_outside(
            [{"symbol": "ZZQ.TO", "date": "2025-06-02"},
             {"symbol": "ZZR.TO", "date": "2024-06-03"},
             {"symbol": "ZZS.TO", "date": ""}], 2025),
            ["ZZR.TO 2024-06-03", "ZZS.TO "])


# ----------------------------------------------- the commands, end to end
_CA_TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
_CA_TRADES = ("# made-up trades\n"
              "BUYSELL 2025-01-02 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
              "BUYSELL 2025-06-02 10:00:00 ZZQ.TO -10 CAD 20.00 200.00 0.00\n")


class _CaProject(unittest.TestCase):
    """A Canadian 2025 project, run once; each test works on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name) / "base"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(_CA_TRADES)
        (root / "taxjson.toml").write_text(_CA_TOML)
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.base = root

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name) / "p"
        shutil.copytree(self.base, root, symlinks=True)
        return root

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r


@rule(R.CA_CARRY_01)
class TestCloseYearFiledDispositionsYear(_CaProject):
    """TJ-0298: close-year stored a 2024 row of the CSV in the 2025 lock,
    filed_totals.gain -200.00 and a 200.00 net capital loss carried
    into 2026, although the 2025 return's gain was 100.00.

    Formerly: test_fix_1027_yearend.TestCloseYearFiledDispositionsYear"""

    def test_another_years_row_is_refused_before_any_write(self):
        root = self.copy()
        csv = root / "filed.csv"
        csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                       "ZZQ.TO,2025-06-02,10,200,100,100\n"
                       "ZZR.TO,2024-06-03,10,100,400,-300\n")
        toml = (root / "taxjson.toml").read_bytes()
        r = _cli(root, "close-year", "--yes", "--filed-dispositions", csv)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("line 3 (ZZR.TO 2024-06-03)", err)
        self.assertIn("Nothing was written.", err)
        self.assertFalse((root / "filed" / "2025.json").exists())
        self.assertEqual((root / "taxjson.toml").read_bytes(), toml)

    def test_the_years_own_rows_are_kept(self):
        root = self.copy()
        csv = root / "filed.csv"
        csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                       "ZZQ.TO,2025-06-02,10,200,100,100\n")
        self.ok(_cli(root, "close-year", "--yes", "--filed-dispositions",
                     csv))
        lock = json.loads((root / "filed" / "2025.json").read_text())
        self.assertEqual(lock["filed_totals"]["gain"], 100.0)
        self.assertEqual(
            lock["carryforwards"]["net_capital_loss"]["closing"], 0.0)

    def test_force_refuses_a_kept_row_of_another_year(self):
        """--force keeps the replaced lock's filed dispositions: one of
        another year is refused, naming it, and nothing is rewritten."""
        root = self.copy()
        csv = root / "filed.csv"
        csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                       "ZZQ.TO,2025-06-02,10,200,100,100\n")
        self.ok(_cli(root, "close-year", "--yes", "--filed-dispositions",
                     csv))
        lp = root / "filed" / "2025.json"
        lock = json.loads(lp.read_text())
        lock["filed_dispositions"].append(
            {"account": "", "symbol": "ZZR.TO", "date": "2024-06-03",
             "qty": 10.0, "proceeds": 100.0, "cost": 400.0,
             "gain": -300.0})
        lp.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        before = lp.read_bytes()
        r = _cli(root, "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("ZZR.TO 2024-06-03", err)
        self.assertIn("--filed-dispositions", err)
        self.assertEqual(lp.read_bytes(), before)


# ------------------------------------------------------------- TJ-0274
_LOCK = {"year": 2025, "country": "canada",
         "totals": {"realized": 100.0, "dispositions": 1},
         "return_row": {"realized": 100.0, "disallowed": 0.0},
         "accounts": {"margin": {"realized": 100.0, "dispositions": 1}}}


class TestReturnRowShape(unittest.TestCase):
    """TJ-0274 / TJ-0336: a malformed return_row passed check-filed, and
    `years` and the next year's carryover fell back in silence.

    Formerly: test_fix_1027_yearend.TestReturnRowShape"""

    def test_shape_problem(self):
        from taxjson.lib.filed_locks import lock_shape_problem
        self.assertIsNone(lock_shape_problem(_LOCK, 2025))
        no_row = {k: v for k, v in _LOCK.items() if k != "return_row"}
        self.assertIsNone(lock_shape_problem(no_row, 2025))
        bad = dict(_LOCK, return_row={"realized": "broken"})
        self.assertIn("return_row.realized as 'broken'",
                      lock_shape_problem(bad, 2025))
        for v in (float("nan"), True, None):
            bad = dict(_LOCK, return_row={"realized": 1.0, "disallowed": v})
            self.assertIn("return_row.disallowed",
                          lock_shape_problem(bad, 2025), v)
        for v in ([1.0], "100", None):
            self.assertIn("return_row as",
                          lock_shape_problem(dict(_LOCK, return_row=v),
                                             2025), v)

    def test_every_reader_refuses_it(self):
        from taxjson.lib import filed_locks
        from taxjson.bin.taxjson_carryover import LockUnreadable, lock_figure
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "2025.json"
            p.write_text(json.dumps(dict(_LOCK, return_row={
                "realized": "broken", "disallowed": 0.0})))
            with self.assertRaises(filed_locks.LockProblem):
                filed_locks.read_lock(p, {"country": "canada"})
            with self.assertRaises(LockUnreadable):
                lock_figure(p, "canada")

    def test_ok_scope_names_it(self):
        from taxjson.bin.taxjson_filed import NOT_LOCKED
        self.assertIn("RETURN row (return_row) is not recomputed",
                      NOT_LOCKED)


class TestCheckFiledReturnRow(_CaProject):
    """TJ-0274 / TJ-0336 through check-filed.

    Formerly: test_fix_1027_yearend.TestCheckFiledReturnRow"""

    def test_malformed_return_row_is_lock_damage(self):
        root = self.copy()
        self.ok(_cli(root, "close-year", "--yes"))
        ok = self.ok(_cli(root, "check-filed")).stdout
        self.assertIn("RETURN row (return_row) is not recomputed",
                      " ".join(ok.split()))
        lp = root / "filed" / "2025.json"
        lock = json.loads(lp.read_text())
        self.assertIn("return_row", lock)
        lock["return_row"]["realized"] = "broken"
        lp.write_text(json.dumps(lock))
        r = _cli(root, "check-filed")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("return_row.realized", " ".join(r.stderr.split()))


if __name__ == "__main__":
    unittest.main()
