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
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tax_rules.dual import settings_for
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _project(root: Path, accounts: str, **settings) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        settings_for("canada", **dict({"year": 2024,
                                       "source_currencies": []},
                                      **settings))
        + accounts, encoding="utf-8")
    os.chmod(root, 0o700)
    return root


class TestStaleWashBeforeAFullRun(unittest.TestCase):
    """TJ-0154: the full run's `prev` snapshot reads the books it is
    about to rebuild without warning that they are stale.

    Formerly: test_fix_backlog_runcon.TestStaleWashBeforeAFullRun"""

    def _project(self, tmp: str) -> Path:
        root = _project(Path(tmp) / "p", '[accounts.margin]\n'
                        'type = "taxable"\n')
        (root / "work").mkdir()
        (root / "reports").mkdir()
        (root / "reports" / "margin.sum").write_text("SUMMARY\n")
        (root / "work" / "margin_gains_wash.json").write_text(
            json.dumps({"transactions": []}))
        return root

    def test_rotate_previous_is_quiet(self):
        from taxjson.lib import compare, report_model
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            err = io.StringIO()
            with mock.patch.object(report_model, "stale_wash_inputs",
                                   return_value=["margin_base.json"]), \
                    redirect_stderr(err):
                report_model._STALE_SAID.clear()
                self.assertTrue(compare.rotate_previous(
                    root, {"accounts": {"margin": {"type": "taxable"}}}))
            self.assertNotIn("was built before", err.getvalue())

    def test_other_readers_still_warn(self):
        from taxjson.lib import report_model
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            err = io.StringIO()
            with mock.patch.object(report_model, "stale_wash_inputs",
                                   return_value=["margin_base.json"]), \
                    redirect_stderr(err):
                report_model._STALE_SAID.clear()
                report_model.resolve_gains_files(root / "work")
            self.assertIn("margin_gains_wash.json was built before "
                          "margin_base.json changed", err.getvalue())


if __name__ == "__main__":
    unittest.main()
