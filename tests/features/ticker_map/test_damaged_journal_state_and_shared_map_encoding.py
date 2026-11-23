"""Audit 2026-10-24, group "reports-state": the recorded wash inputs
and a new account (D01), the T1135 cost walk and an assignment that
covers a short (D02), a damaged work/cross_listings.state read as "no
pending journals" (D05), an incomplete work/<acct>_report.json served
by `sum` (D07), and `migrate` sharing a tobase.map that is not UTF-8
text (A42). One test per finding, each the audit's probe.

Every fixture is synthetic: made-up tickers (ZZQ.US), account names
(a, b, c, margin, tfsa), no ids, no amounts of 1,000 or more with cents.

Formerly: test_fix_audit_1024_reports
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs -C root args...` from this worktree's sources (the
    audit's CLI probes; python_module_argv, never `-m` from the cwd)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=180)


_CFG = {"settings": {"year": 2026, "country": "canada"},
        "accounts": {"margin": {"type": "taxable"}}}


def _project(root: Path) -> None:
    """A minimal Canadian project whose work/ holds one empty book (the
    probes' was empty; a work/ with no book reads as never run)."""
    (root / "work").mkdir()
    (root / "work" / "margin_raw.json").write_text('{"transactions": []}')
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        '[accounts.margin]\ntype = "taxable"\n')


class TestD05DamagedJournalState(unittest.TestCase):
    """Formerly: test_fix_audit_1024_reports.TestD05DamagedJournalState"""

    def test_d05_damaged_state_blocks_the_journals_and_the_step(self):
        """D05: Unreadable journal state yields no pending journals and
        a completed checklist item. A work/cross_listings.state holding
        broken JSON passed the existence checks and read as empty record
        lists: `journals --pending --json` exited 0 with pending 0 and
        checklist.d_journals said done. The report now refuses a state
        file it cannot read, naming why, and the step is blocked."""
        from taxjson.lib import checklist
        from taxjson.lib import cross_listings as XL
        from taxjson.lib.journals import JournalsError, report
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            state = root / "work" / XL.STATE
            ctx = SimpleNamespace(root=root, cache=root / "work", cfg=_CFG)
            state.write_text("{broken")
            run = _cli(root, "journals", "--pending", "--json")
            self.assertNotEqual(run.returncode, 0, run.stdout)
            self.assertEqual(run.stdout, "")
            self.assertIn(f"work/{XL.STATE} is not JSON", run.stderr)
            self.assertIn("run `tjs run` first", run.stderr)
            with self.assertRaises(JournalsError) as cm:
                report(root, _CFG)
            self.assertIn("is not JSON", str(cm.exception))
            res = checklist.d_journals(ctx)
            self.assertEqual(res.status, "blocked")
            self.assertIn(f"work/{XL.STATE} is not JSON", res.detail)
            self.assertIn("run `tjs run`", res.detail)
            # Another version's record (or a hand-edited one): the same.
            state.write_text(json.dumps({"format": "cross_listings/0",
                                         "joined": []}))
            res = checklist.d_journals(ctx)
            self.assertEqual(res.status, "blocked")
            self.assertIn("not 'cross_listings/1'", res.detail)
            self.assertEqual(XL.state_problem(state).split(" (")[0],
                             "has format 'cross_listings/0', not "
                             "'cross_listings/1'")
            # The run's own record with nothing in it reads as before:
            # nothing pending, the step done.
            state.write_text(json.dumps({"format": XL.FORMAT}))
            self.assertIsNone(XL.state_problem(state))
            run = _cli(root, "journals", "--pending", "--json")
            self.assertEqual(run.returncode, 0, run.stderr[-1000:])
            self.assertEqual(json.loads(run.stdout)["pending"], 0)
            self.assertEqual(checklist.d_journals(ctx).status, "done")


class TestA42SharedTobaseNotUtf8(unittest.TestCase):
    """Formerly: test_fix_audit_1024_reports.TestA42SharedTobaseNotUtf8"""

    def test_a42_an_unreadable_winning_map_is_refused(self):
        """A42: Sharing accepts an invalid UTF-8 winning map and
        propagates it to every year. The newest year's tobase.map, not
        UTF-8 text, was compared as an empty map and proposed as the
        shared file with no problem recorded; identical copies were not
        looked at. plan_shared now records the problem for the winner,
        identical or not, and `migrate` writes nothing."""
        from taxjson.lib import tobase_map as TB
        from _support.symbols_tobase_years import _years
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase="")
            bad = top / "2025" / "tobase.map"
            bad.write_bytes(bytes([255]))
            plan = TB.plan_shared(top)
            self.assertEqual(len(plan.problems), 1, plan.problems)
            self.assertIn("2025/tobase.map is not UTF-8 text (byte 0xff "
                          "at offset 0)", plan.problems[0])
            self.assertFalse(plan.identical)
            r = _cli(top, "migrate")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("2025/tobase.map is not UTF-8 text", r.stderr)
            self.assertIn("Nothing was changed", r.stderr)
            self.assertFalse((top / "tobase.map").exists())
            self.assertEqual(bad.read_bytes(), bytes([255]))
            self.assertEqual((top / "2024" / "tobase.map").read_text(), "")
            # Every copy the same unreadable bytes: no fast path past
            # the check.
            (top / "2024" / "tobase.map").write_bytes(bytes([255]))
            plan = TB.plan_shared(top)
            self.assertTrue(plan.identical)
            self.assertEqual(len(plan.problems), 1, plan.problems)
            self.assertIn("is not UTF-8 text", plan.problems[0])
            r = _cli(top, "migrate")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertFalse((top / "tobase.map").exists())
            # Readable copies share as before.
            for y in ("2024", "2025"):
                (top / y / "tobase.map").write_text("")
            plan = TB.plan_shared(top)
            self.assertEqual(plan.problems, [])
            self.assertTrue(plan.identical)


if __name__ == "__main__":
    unittest.main()
