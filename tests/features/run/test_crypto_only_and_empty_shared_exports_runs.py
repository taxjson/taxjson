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
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.project.settings import Settings
from taxjson.lib.dispatch import python_module_argv
from taxjson.pipeline import run_state
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
EXAMPLES = REPO_ROOT / "examples"
_WIDTH = CapturedWidth()

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


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


class TestCryptoOnly(unittest.TestCase):
    """TJ-0130, TJ-0162: the scaffold's accounts, only crypto has files.

    Formerly: test_fix_backlog_runcon.TestCryptoOnly"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _project(Path(cls._tmp.name) / "p",
                            '[accounts.margin]\ntype = "taxable"\n'
                            '[accounts.tfsa]\ntype = "sheltered"\n'
                            '[accounts.crypto]\ntype = "taxable"\n'
                            'crypto = true\n', source_currencies=["USD"])
        for a in ("margin", "tfsa", "crypto"):
            (cls.root / "inputs" / a).mkdir(parents=True)
        for f in ("kraken_demo.csv", "coinbase_demo.csv"):
            (cls.root / "inputs" / "crypto" / f).write_bytes(
                (EXAMPLES / f).read_bytes())
        cls.res = _cli(cls.root, "run", "--no-input", width="120")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_run_ok(self):
        self.assertEqual(self.res.returncode, 0,
                         self.res.stdout + self.res.stderr)
        self.assertIn("skipping account 'margin'",
                      self.res.stdout + self.res.stderr)

    def test_no_option_timing_warning(self):
        """TJ-0162: the empty margin account has no books."""
        self.assertNotIn("option_grant_timing_since",
                         self.res.stdout + self.res.stderr)

    def test_gains_stops_after_the_crypto_note(self):
        """TJ-0130."""
        r = _cli(self.root, "gains", width="120")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("crypto account 'crypto' is not shown", r.stderr)
        self.assertNotIn("run `tjs run` first", r.stdout + r.stderr)
        self.assertNotIn("Error:", r.stderr)

    def test_grant_since_warning_needs_books(self):
        """TJ-0162: no taxable equity book at all is not maybe-options."""
        from taxjson.pipeline.run import _grant_since_warning
        st = Settings.from_table({"country": "canada", "year": 2024})
        accts = {"margin": {"type": "taxable"}}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_grant_since_warning(st, Path(tmp), accts))
        # Without a project to read, the setting alone decides.
        self.assertIsNotNone(_grant_since_warning(st, accounts=accts))


class TestSharedExportsFolder(unittest.TestCase):
    """TJ-0133 and TJ-0155: the years layout, nothing in ../inputs/.

    Formerly: test_fix_backlog_runcon.TestSharedExportsFolder"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        top = Path(cls._tmp.name) / "taxes"
        r = _cli(Path(cls._tmp.name), "init", "--country", "canada",
                 "--year", "2025", str(top))
        assert r.returncode == 0, r.stdout + r.stderr
        os.chmod(top, 0o700)
        cls.top, cls.root = top, top / "2025"
        # A download saved in the year folder's own inputs/: not read.
        (cls.root / "inputs" / "margin").mkdir(parents=True)
        (cls.root / "inputs" / "margin" / "questrade_demo.csv").write_bytes(
            (EXAMPLES / "questrade_demo.csv").read_bytes())
        cls.res = _cli(cls.root, "run")
        cls.shown = _cli(cls.root, "run", width="120")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_skip_names_the_shared_folder(self):
        text = self.res.stdout + self.res.stderr
        self.assertEqual(self.res.returncode, 0, text)
        self.assertIn("no CSVs or .tt files in ../inputs/margin;", text)
        self.assertNotIn("no CSVs or .tt files in inputs/", text)

    def test_nothing_computed_names_the_shared_folder(self):
        cap = " ".join((self.res.stdout + self.res.stderr).split())
        self.assertIn("into ../inputs/<account>/ and re-run", cap)
        shown = self.shown.stdout + self.shown.stderr
        self.assertIn("put each broker's export in ../inputs/<account>/",
                      shown)

    def test_sum_says_the_run_had_no_input(self):
        """TJ-0155."""
        for cmd in ("sum", "list", "gains", "option-boundary"):
            r = _cli(self.root, cmd)
            self.assertEqual(r.returncode, 1, cmd)
            text = " ".join(r.stderr.split())
            self.assertIn("the last run had no input: ../inputs/margin, "
                          "../inputs/tfsa, ../inputs/rrsp +1 more are empty",
                          text, cmd)
            # The fix is the export, not another run (the older
            # option-boundary contract, features.planning.test_option_boundary_empty_taxable_folder).
            self.assertNotIn("`tjs run`", text, cmd)

    def test_single_folder_names_the_empty_folder(self):
        """TJ-0155, one account in a single-folder project."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(Path(tmp) / "p",
                            '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin").mkdir(parents=True)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "sum", width="120")
            self.assertEqual(r.returncode, 1)
            self.assertIn("Error: the last run had no input: inputs/margin "
                          "is empty, so it made no books", r.stderr)
            self.assertNotIn("`tjs run`", r.stderr)

    def test_an_export_added_since_asks_for_the_run(self):
        """TJ-0155: once a folder has a file, the run is the fix."""
        with tempfile.TemporaryDirectory() as tmp:
            r = _cli(Path(tmp), "init", "--country", "canada", "--year",
                     "2025", str(Path(tmp) / "t"))
            self.assertEqual(r.returncode, 0, r.stderr)
            root = Path(tmp) / "t" / "2025"
            self.assertEqual(_cli(root, "run").returncode, 0)
            (Path(tmp) / "t" / "inputs" / "margin" / "q.csv").write_bytes(
                (EXAMPLES / "questrade_demo.csv").read_bytes())
            r = _cli(root, "sum")
            self.assertEqual(r.returncode, 2)  # a missing input (backlog TJ-0644)
            self.assertNotIn("had no input", r.stderr)
            self.assertIn("Run `tjs run` first", r.stderr)


class TestDistributionsLine(unittest.TestCase):
    """TJ-0163.

    Formerly: test_fix_backlog_runcon.TestDistributionsLine"""

    def test_no_program_name_or_stage_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                Path(tmp) / "p", '[accounts.margin]\ntype = "taxable"\n\n'
                '[[distributions]]\nsymbol = "ZZT.TO"\n'
                'record_date = 2024-12-27\nper_share = 0.50\n\n'
                '[[distributions]]\nsymbol = "ZZT.TO"\n'
                'record_date = 2024-06-03\nper_share = -0.10\n')
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "questrade_2024.csv").write_text(
                _QH + "2024-02-01,2024-02-05,Buy,ZZT,ZZT TRUST UNITS,100,"
                "20.00,-2000.00,0.00,-2000.00,CAD,Trades,99900001,Margin\n")
            r = _cli(root, "run", "--no-input", width="120")
            text = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, text)
            self.assertNotIn("taxjson-apply-distributions:", text)
            self.assertNotIn(".stage", text)
            self.assertIn("Info: 2 [[distributions]] adjustment(s) applied "
                          "to margin's books", text)


class TestNeverCodedClosingOnce(unittest.TestCase):
    """TJ-0165 (warning count): said once per run, naming its file.

    Formerly: test_fix_backlog_runcon.TestNeverCodedClosingOnce"""

    def test_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(Path(tmp) / "p",
                            '[accounts.margin]\ntype = "taxable"\n',
                            source_currencies=["USD"])
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "hand.tt").write_text(
                'OPENING 2023-12-29 ZZL250117C00050000.US 2 cost=unknown '
                'reason="held before the exports"\n'
                "BUYSELL 2024-03-01 10:00:00 ZZL250117C00050000.US -2 USD "
                "3.00 598.70 1.30\n")
            for width in ("0", "120"):
                r = _cli(root, "run", "--no-input", width=width)
                text = r.stdout + r.stderr
                self.assertEqual(r.returncode, 0, text)
                self.assertEqual(text.count("never coded"), 1, text)
                self.assertIn("inputs/margin/hand.tt:1 lists "
                              "ZZL250117C00050000.US", text)


class TestHoldingsDiffOneLine(unittest.TestCase):
    """TJ-0177.

    Formerly: test_fix_backlog_runcon.TestHoldingsDiffOneLine"""

    def _shown(self, details: bool) -> str:
        from taxjson.pipeline import diagnostics as pipeline_diagnostics
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "120"}), \
                mock.patch.object(run_state, "current_cmd", "run"), \
                mock.patch.object(run_state, "current_details", details), \
                mock.patch.object(run_state, "current", run_state.RunState()), \
                redirect_stdout(buf):
            pipeline_diagnostics.print_holdings_diff(
                {"ZZG.US": (-20.0, 0.0), "ZZK.US": (3.0, 30.0)},
                {"ZZY.US": (5.0, 50.0), "ZZK.US": (4.0, 40.0)},
                account="margin")
        return buf.getvalue()

    def test_default_view_is_one_line(self):
        out = self._shown(details=False)
        self.assertEqual(out.splitlines(), [
            ("Info: margin: 3 holdings changed since the last run "
             "(ZZG.US, ZZK.US, ZZY.US)")])

    def test_details_lists_each(self):
        out = self._shown(details=True)
        lines = out.splitlines()
        self.assertEqual(lines[0],
                         "Info: holdings changed since the last run (3):")
        self.assertIn("- ZZG.US: was -20 (closed)", lines)
        self.assertIn("+ ZZY.US: 5 (new position)", lines)


if __name__ == "__main__":
    unittest.main()
