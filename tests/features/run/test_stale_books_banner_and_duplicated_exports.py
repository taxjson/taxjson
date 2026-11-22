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


class TestStaleBooksBannerOnReviewCommands(unittest.TestCase):
    """TJ-0557: a run over a buy for 10 and a sale for 20, then the
    purchase edited to 15 without a re-run. `list` warned that the
    inputs changed; `audit --json` still reported a gain of 10 with an
    empty stderr and exit 0, and journals, renames and cash said
    nothing either. Every review command that reads the saved books
    now prints the run-state banner (stderr, so --json stays valid).
    `tjs cash` reads the run's cash events, so it refuses instead (PLAN
    step 16 commit 6; the owner's rule: no chance of stale data).

    Formerly: test_fix_1029_r2_review.TestStaleBooksBannerOnReviewCommands"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p", files={
            "margin/trades.tt":
                "BUYSELL 2025-01-06 10:00:00 SYN.TO 1 CAD 10 -10 0\n"
                "BUYSELL 2025-03-03 10:00:00 SYN.TO -1 CAD 20 20 0\n"})
        r = _tjs(cls.p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        cls.clean = {c: _tjs(cls.p, c, "--json")
                     for c in ("audit", "journals", "renames", "cash", "list")}
        (cls.p / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2025-01-06 10:00:00 SYN.TO 1 CAD 15 -15 0\n"
            "BUYSELL 2025-03-03 10:00:00 SYN.TO -1 CAD 20 20 0\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_each_review_command_warns_after_an_input_change(self):
        for cmd in ("audit", "journals", "renames", "list"):
            with self.subTest(cmd=cmd):
                self.assertNotIn("not the clean result",
                                 self.clean[cmd]
                                 .stderr)
                r = _tjs(self.p, cmd, "--json")
                self.assertIn("these books are not the clean result",
                              r.stderr)
                json.loads(r.stdout)

    def test_cash_refuses_after_an_input_change(self):
        self.assertEqual(self.clean["cash"].returncode, 0, self.clean["cash"].stderr)
        r = _tjs(self.p, "cash", "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("the books in work/ are older than the inputs (changed: "
                      "inputs/margin/trades.tt)", r.stderr)
        self.assertIn("Run `tjs run` first.", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_slip_audit_and_reconcile_slips_warn(self):
        r = _tjs(self.p, "slip-audit")
        self.assertIn("these books are not the clean result", r.stderr)
        r = _tjs(self.p, "slip-audit", "--template")
        self.assertNotIn("not the clean result", r.stderr)
        slip = self.p / "t5008.csv"
        slip.write_text("date,symbol,quantity,proceeds\n")
        r = _tjs(self.p, "reconcile-slips", str(slip))
        self.assertIn("these books are not the clean result", r.stderr)


class TestOneExportInTwoAccounts(unittest.TestCase):
    """TJ-0478: one Questrade export copied into a second taxable
    account's folder. The run warned that the same export sits in two
    accounts (every row booked in both), but its closing list left it
    out, `tjs sum` showed no banner over the inflated totals and the
    checklist ticked run-clean done. The run now records what it found;
    run-clean is attention with a Next line that fixes it, every report
    banner carries it, and the closing list names it.

    Formerly: test_fix_1029_r2_review.TestOneExportInTwoAccounts"""

    CSV = (_QT_HEAD
           + "2025-01-06,2025-01-07,Buy,SYN.TO,SYNTHETIC,10,10,-100,0,"
             "-100,CAD,Trades,99900001,Margin\n"
           + "2025-03-03,2025-03-04,Sell,SYN.TO,SYNTHETIC,-10,15,150,0,"
             "150,CAD,Trades,99900001,Margin\n")

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p",
                         accounts={"margin": "taxable", "other": "taxable"},
                         files={"margin/q.csv": cls.CSV,
                                "other/copy.csv": cls.CSV})
        cls.first = _tjs(cls.p, "run", "--no-input")
        assert cls.first.returncode == 0, cls.first.stdout + cls.first.stderr

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_the_run_warns_and_its_closing_list_names_it(self):
        out = self.first.stdout + self.first.stderr
        self.assertIn("the same export file sits in two accounts", out)
        close = self.first.stdout[
            self.first.stdout.index("Before you trust these numbers"):]
        self.assertIn("booked in two accounts", close)
        self.assertIn("margin/q.csv", close)

    def test_run_clean_is_attention_and_its_next_line_fixes_it(self):
        r = _tjs(self.p, "checklist", "--run", "--only", "run-clean",
                 "--json")
        doc = json.loads(r.stdout)
        step = next(s for s in doc["steps"] if s["id"] == "run-clean")
        self.assertEqual(step["status"], "attention", step)
        self.assertIn("margin/q.csv", step["detail"])
        self.assertIn("other/copy.csv", step["detail"])
        r = _tjs(self.p, "checklist")
        nxt = [ln for ln in r.stdout.splitlines()
               if ln.startswith("Next")]
        self.assertTrue(nxt, r.stdout)
        self.assertIn("one inputs/<account>/ folder", nxt[0])

    def test_sum_carries_the_run_state_banner(self):
        r = _tjs(self.p, "sum")
        self.assertIn("these books are not the clean result", r.stderr)
        r = _tjs(self.p, "sum", "--details")
        self.assertIn("booked twice: keep each export in one", r.stderr)

    def test_moving_the_copy_away_and_running_again_clears_it(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p",
                         accounts={"margin": "taxable", "other": "taxable"},
                         files={"margin/q.csv": self.CSV,
                                "other/copy.csv": self.CSV})
            self.assertEqual(_tjs(p, "run", "--no-input").returncode, 0)
            (p / "inputs" / "other" / "copy.csv").unlink()
            (p / "inputs" / "other" / "x.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 10 -10 0\n")
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("booked in two accounts", r.stdout)
            r = _tjs(p, "checklist", "--run", "--only", "run-clean",
                     "--json")
            step = next(s for s in json.loads(r.stdout)["steps"]
                        if s["id"] == "run-clean")
            self.assertEqual(step["status"], "done", step)


@unittest.skipIf(os.name == "nt", "POSIX permissions")
class TestNoPermissionWarningUnderAnOwnerOnlyFolder(unittest.TestCase):
    """TJ-0426: `mkdir inputs/slips` (where the docs put the slips) made
    the year's inputs/ 0755 under a 0700 year folder, and every run
    warned that other users could read it. Nobody else passes through
    the 0700 folder above it: no warning, until that folder lets them.

    Formerly: test_fix_1029_r2_review.TestNoPermissionWarningUnderAnOwnerOnlyFolder"""

    def test_quiet_under_0700_warned_once_reachable(self):
        with tempfile.TemporaryDirectory() as td:
            w = Path(td) / "w"
            r = _tjs(None, "init", "--country", "canada", "--year",
                     "2024", str(w))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            year = w / "2024"
            self.assertTrue((year / "taxjson.toml").is_file())
            self.assertEqual(year.stat().st_mode & 0o077, 0)
            slips = year / "inputs" / "slips"
            slips.mkdir(parents=True)
            for d in (year / "inputs", slips):
                d.chmod(0o755)
            r = _tjs(year, "run", "--no-input")
            self.assertNotIn("other users", r.stderr + r.stdout)
            # The year folder others can pass through: inputs/ is theirs
            # to read, and said once.
            year.chmod(0o711)
            w.chmod(0o711)
            r = _tjs(year, "run", "--no-input")
            self.assertEqual(r.stderr.count("can be read by other users"),
                             1, r.stderr)
            self.assertIn("inputs/ can be read by other users", r.stderr)
            # The folder holding the year folders shut again: quiet.
            w.chmod(0o700)
            r = _tjs(year, "run", "--no-input")
            self.assertNotIn("other users", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
