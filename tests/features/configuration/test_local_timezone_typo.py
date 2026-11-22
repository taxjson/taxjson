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


class TestTimezoneTypo(unittest.TestCase):
    """TJ-0034.

    Formerly: test_fix_backlog_runcon.TestTimezoneTypo"""

    def _typo_project(self, tmp: str) -> Path:
        root = _project(Path(tmp) / "p",
                        '[accounts.margin]\ntype = "taxable"\n'
                        '[accounts.crypto]\ntype = "taxable"\n'
                        'crypto = true\n', local_timezone=None,
                        local_timezoen="America/Toronto")
        (root / "inputs" / "margin").mkdir(parents=True)
        return root

    def test_run_names_the_misspelled_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._typo_project(tmp)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            text = " ".join((r.stdout + r.stderr).split())
            self.assertIn("has no local_timezone", text)
            self.assertIn("'local_timezoen'", text)

    def test_checklist_configure_names_it_and_offers_no_failing_command(
            self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._typo_project(tmp)
            r = _cli(root, "checklist", "--run", "--only", "configure",
                     width="120")
            text = " ".join(r.stdout.split())
            self.assertIn("has no local_timezone (it has 'local_timezoen'",
                          text)
            # A first project: no old map files, no other year folder.
            self.assertNotIn("tjs migrate", r.stdout)
            self.assertNotIn("tjs align --from", r.stdout)

    def test_typo_helper(self):
        from taxjson.lib.timezones import timezone_typo
        self.assertEqual(timezone_typo({"local_timezoen": "x"}),
                         "local_timezoen")
        self.assertEqual(timezone_typo({"Local_Timezone": "x"}),
                         "Local_Timezone")
        self.assertIsNone(timezone_typo({"local_timezone": "x"}))
        self.assertIsNone(timezone_typo({"year": 2024}))

    def test_configure_commands_follow_the_project(self):
        from taxjson.lib import checklist as CL
        it = next(i for i in CL.items(2024, "canada") if i.id == "configure")
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp)
            y24 = top / "2024"
            y24.mkdir()
            (y24 / "taxjson.toml").write_text("[settings]\nyear = 2024\n")
            got = [c.command for c in CL._project_cmds(it, y24, 2024).cmds]
            self.assertEqual(got, [])
            # Another year's project beside it: align from that year.
            (top / "2022").mkdir()
            (top / "2022" / "taxjson.toml").write_text(
                "[settings]\nyear = 2022\n")
            got = [c.command for c in CL._project_cmds(it, y24, 2024).cmds]
            self.assertEqual(got, ["tjs align --from 2022"])
            # An old map file: migrate is offered.
            (y24 / "yf_ticker.map").write_text("")
            got = [c.command for c in CL._project_cmds(it, y24, 2024).cmds]
            self.assertIn("tjs migrate", got)
        # Outside a project (the guide), and any other item: unchanged.
        self.assertIs(CL._project_cmds(it, None, 2024), it)


if __name__ == "__main__":
    unittest.main()
