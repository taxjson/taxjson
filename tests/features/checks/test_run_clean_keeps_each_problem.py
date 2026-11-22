"""Backlog group `runcon`: the run's console output, run-state messages
and closing list.

TJ-0034  a misspelled local_timezone stopped the run with "has no
         local_timezone" and never named the typo (the checklist said
         the same); a first project's configure step offered `tjs
         migrate` and `tjs align --from <year-1>`, both failing.
TJ-0095  an in-kind contribution that could not be valued was NOT
         booked, yet `tjs sum` had no `!` line and the checklist marked
         the transfers step done.
TJ-0109  one "inputs changed (changed: ...; added: ...)" problem was
         split at "; " into two run-state problems.
TJ-0130  crypto only: `tjs gains` said "run `tjs run` first" after a
         successful run, and exited 1.
TJ-0133  in the years layout the run named the year folder's inputs/
         instead of ../inputs/ in its skip and nothing-computed lines.
TJ-0154  a full run after `run --account` opened with a stale-wash
         warning per account telling the user to run `tjs run`.
TJ-0155  empty project: `tjs sum` said "Run `tjs run` first" right
         after that run said nothing was computed.
TJ-0162  crypto-only use of the scaffold warned about
         option_grant_timing_since.
TJ-0163  the run's console showed "taxjson-apply-distributions: applied
         2 adjustment(s) to margin_base.json.stage."
TJ-0165  (the part this group owns) an option the broker never coded
         CLOSING is warned about once per run.
TJ-0177  "Info: holdings changed since the last run (N):" was a
         multi-line message in the run's default view.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_backlog_runcon
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str, width: str = "0"
         ) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH=width)
    env.pop("TAXJSON_LOCAL_TZ", None)
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


def _project(root: Path, accounts: str, **settings) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        settings_for("canada", **dict({"year": 2024,
                                       "source_currencies": []},
                                      **settings))
        + accounts, encoding="utf-8")
    os.chmod(root, 0o700)
    return root


class TestRunStateProblemsWhole(unittest.TestCase):
    """TJ-0109: one inputs-changed problem is one problem.

    Formerly: test_fix_backlog_runcon.TestRunStateProblemsWhole"""

    def test_detail_with_semicolons_stays_one_problem(self):
        from taxjson.cli import common as cli_common
        from taxjson.lib import checklist as CL
        one = ("inputs changed since the last full run (changed: "
               "inputs/margin/a.tt; added: inputs/margin/b.tt)")
        res = CL.Result("run-clean", "attention",
                        "; ".join([one, "pending elections"]),
                        problems=[one, "pending elections"])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(CL, "d_run_clean", return_value=res):
            got = cli_common._run_state_problems(Path(tmp), {"settings": {
                "year": 2024}})
        self.assertEqual(got, [one, "pending elections"])

    def test_d_run_clean_keeps_each_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(Path(tmp) / "p",
                            '[accounts.margin]\ntype = "taxable"\n',
                            source_currencies=["USD"])
            m = root / "inputs" / "margin"
            m.mkdir(parents=True)
            (m / "a.tt").write_text(
                "BUYSELL 2024-02-01 09:30:00 ZZA.US 10 USD 5.00 50.00 0\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (m / "a.tt").write_text(
                "BUYSELL 2024-02-01 09:30:00 ZZA.US 12 USD 5.00 60.00 0\n")
            (m / "b.tt").write_text(
                "BUYSELL 2024-02-02 09:30:00 ZZC.US 10 USD 5.00 50.00 0\n")
            # (`tjs sum` refuses such books, TJ-4347: the problems the
            # review commands' banner counts.)
            from taxjson.cli import common as cli_common
            from taxjson.pipeline.config import load_config
            probs = cli_common._run_state_problems(root, load_config(root))
            self.assertEqual(len(probs), 1, probs)
            self.assertIn("changed: inputs/margin/a.tt", probs[0])
            self.assertIn("added: inputs/margin/b.tt)", probs[0])
            r = _cli(root, "divs-sum", width="120")
            self.assertIn("(1 problem)", r.stderr.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
