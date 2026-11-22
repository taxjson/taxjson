"""Backlog fix round 2 of 2026-10-29, group `review`: the review and
filing-output commands (audit --year, form-export, the wash review, the
stale-book warning, check-tt, the checklist's run-clean, sanity's
pointer, the inputs/ permission warning).

TJ-0550  audit --year refuses a lock closed under the other country
         before applying its date basis or option timing.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*, SYN*), account names (margin, tfsa), amounts
under 1,000, broker account numbers like 99900001.

Formerly: test_fix_1029_r2_review
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, *, country="canada", year=2025, accounts=None,
             files=None, settings=""):
    """A project at `root`: taxjson.toml, an empty ticker.map and one
    inputs/<account>/ per account; `files` maps "account/name" to its
    text."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    accounts = accounts or {"margin": "taxable"}
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items()))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        acct, _, fn = name.partition("/")
        (root / "inputs" / acct / fn).write_text(text)
    return root


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type\n")


class TestAuditRefusesAForeignLock(unittest.TestCase):
    """TJ-0550: a Canadian 2026 project whose filed/2025.json was
    closed under country = "usa" with date_basis = "trade". `audit
    --year 2025` adopted the US lock's trade date basis (a sale traded
    Dec 31 2025, settled Jan 2 2026 moved into 2025: gain 0 became 50)
    and exited 0. It now refuses the lock, as check-filed does, before
    applying any of its settings.

    Formerly: test_fix_1029_r2_review.TestAuditRefusesAForeignLock"""

    def test_a_us_lock_in_a_canadian_project_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", year=2026, files={
                "margin/q.csv": _QT_HEAD
                + "2025-01-06,2025-01-07,Buy,SYN.TO,SYNTHETIC,10,10,-100,"
                  "0,-100,CAD,Trades,99900001,Margin\n"
                + "2025-12-31,2026-01-02,Sell,SYN.TO,SYNTHETIC,-10,15,150,"
                  "0,150,CAD,Trades,99900001,Margin\n"})
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            ctl = _tjs(p, "audit", "--year", "2025", "--json")
            self.assertEqual(ctl.returncode, 0, ctl.stderr)
            self.assertEqual(json.loads(ctl.stdout)["total_gain"], 0.0)
            (p / "filed").mkdir()
            (p / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "usa", "date_basis": "trade",
                 "accounts": {"margin": {}}}))
            r = _tjs(p, "audit", "--year", "2025", "--json")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("filed/2025.json", r.stderr)
            self.assertIn('country = "usa"', r.stderr)
            self.assertNotIn("trade date basis its lock recorded",
                             r.stderr)
            self.assertNotIn('"total_gain": 50', r.stdout)

    def test_a_lock_of_this_country_still_sets_the_date_basis(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", year=2026, files={
                "margin/q.csv": _QT_HEAD
                + "2025-01-06,2025-01-07,Buy,SYN.TO,SYNTHETIC,10,10,-100,"
                  "0,-100,CAD,Trades,99900001,Margin\n"
                + "2025-12-31,2026-01-02,Sell,SYN.TO,SYNTHETIC,-10,15,150,"
                  "0,150,CAD,Trades,99900001,Margin\n"},
                settings='tax_date = "settle"\n')
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            (p / "filed").mkdir()
            (p / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada", "date_basis": "trade",
                 "accounts": {"margin": {}}}))
            r = _tjs(p, "audit", "--year", "2025", "--json")
            self.assertEqual(json.loads(r.stdout)["total_gain"], 50.0,
                             r.stderr)
            self.assertIn("2025 is locked", r.stderr)


class TestWashReviewShowsUnknownCostWarnings(unittest.TestCase):
    """TJ-0551: a US sale of 2 SYN.US draws a lot bought for 10 (sold
    for 8: a loss of 2) and a unit with no purchase in the files. The
    engine warns that the wash-sale rule may apply and must be checked
    by hand (the gains file's superficial_loss_warnings), but `tjs
    wash-sales` listed no flag and the checklist marked wash-reviewed
    done ("no wash sales"). Both now carry the engine's warning.

    Formerly: test_fix_1029_r2_review.TestWashReviewShowsUnknownCostWarnings"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p", country="usa", files={
            "margin/trades.tt":
                'OPENING 2024-12-31 SYN.US 1 cost=unknown '
                'reason="synthetic missing lot"\n'
                "BUYSELL 2025-01-06 10:00:00 SYN.US 1 USD 10 10 0\n"
                "BUYSELL 2025-03-03 10:00:00 SYN.US -2 USD 8 16 0\n"})
        r = _tjs(cls.p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        g = json.loads((cls.p / "work" / "margin_gains_wash.json")
                       .read_text())
        assert g.get("superficial_loss_warnings"), g

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_wash_sales_json_lists_the_manual_check(self):
        r = _tjs(self.p, "wash-sales", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        flags = json.loads(r.stdout)["manual_check_flags"]
        self.assertEqual(len(flags), 1, flags)
        self.assertIn("SYN.US", flags[0])
        self.assertIn("verify manually", flags[0])

    def test_wash_sales_text_names_the_manual_check(self):
        r = _tjs(self.p, "wash-sales")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MANUAL CHECK: 1 warn-only flag(s)", r.stdout)
        r = _tjs(self.p, "wash-sales", "--details")
        self.assertIn("unknown-cost disposition", r.stdout)

    def test_the_checklist_keeps_wash_reviewed_open(self):
        r = _tjs(self.p, "checklist", "--run", "--only", "wash-reviewed",
                 "--json")
        step = next(s for s in json.loads(r.stdout)["steps"]
                    if s["id"] == "wash-reviewed")
        self.assertEqual(step["status"], "manual", step)
        self.assertIn("SYN.US", step["detail"])
        self.assertNotIn("no wash sales", step["detail"])


if __name__ == "__main__":
    unittest.main()
