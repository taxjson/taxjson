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
TJ-0130  crypto only: `tjs gains` said "run `taxjson run` first" after a
         successful run, and exited 1.
TJ-0133  in the years layout the run named the year folder's inputs/
         instead of ../inputs/ in its skip and nothing-computed lines.
TJ-0154  a full run after `run --account` opened with a stale-wash
         warning per account telling the user to run `taxjson run`.
TJ-0155  empty project: `tjs sum` said "Run `taxjson run` first" right
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
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
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


class TestTimezoneTypo(unittest.TestCase):
    """TJ-0034."""

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
        from taxjson.lib.brokerages._crypto_common import timezone_typo
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


def _in_kind_project(root: Path) -> Path:
    """margin buys 100 ZZB and moves 50 to the RRSP on 2024-05-01; the
    rows state no value and ticker.map's QUOTE ratio makes the close no
    value either: the contribution is NOT booked."""
    _project(root, '[accounts.margin]\ntype = "taxable"\n'
                   '[accounts.rrsp]\ntype = "sheltered"\n')
    (root / "ticker.map").write_text("QUOTE ZZB.TO ZZBX.TO 2\n")
    for a in ("margin", "rrsp"):
        (root / "inputs" / a).mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2024.csv").write_text(
        _QH + "2024-01-10,2024-01-11,Buy,ZZB,ZZB CORP,100,10.00,-1000.00,"
        "-4.95,-1004.95,CAD,Trades,99900001,Margin\n"
        "2024-05-01,2024-05-01,TF6,ZZB,ZZB CORP TFER TO 99900002,-50,0.00,"
        "0.00,0.00,0.00,CAD,Transfers,99900001,Margin\n")
    (root / "inputs" / "rrsp" / "questrade_2024.csv").write_text(
        _QH + "2024-05-01,2024-05-01,TF6,ZZB,ZZB CORP TRANSFER BOOK VALUE "
        "502.48,50,0.00,0.00,0.00,0.00,CAD,Transfers,99900002,RRSP\n")
    return root


class TestInKindNotBooked(unittest.TestCase):
    """TJ-0095."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _in_kind_project(Path(cls._tmp.name) / "p")
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "NOT booked" in r.stdout + r.stderr, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_sum_has_an_act_line(self):
        r = _cli(self.root, "sum", width="120")
        self.assertEqual(r.returncode, 0, r.stderr)
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertTrue(any("in-kind move" in a and "NOT booked" in a
                            and "ZZB.TO" in a for a in acts), r.stdout)
        self.assertTrue(all(len(a) <= 100 for a in acts), acts)

    def test_sum_details_names_the_inkind_line(self):
        r = _cli(self.root, "sum", "--details", width="120")
        text = " ".join((r.stdout + r.stderr).split())
        self.assertIn("INKIND 2024-05-01 ZZB.TO -50 CAD <price per share>",
                      text)

    def test_sum_json_readiness(self):
        r = _cli(self.root, "sum", "--json")
        doc = json.loads(r.stdout)
        kinds = [i["kind"] for i in doc["readiness"]["issues"]]
        self.assertIn("in-kind-not-booked", kinds)
        self.assertFalse(doc["readiness"]["complete"])

    def test_checklist_transfers_needs_attention(self):
        r = _cli(self.root, "checklist", "--run", "--only", "transfers",
                 "--json")
        doc = json.loads(r.stdout)
        step = next(s for s in doc["steps"] if s["id"] == "transfers")
        self.assertEqual(step["effective"], "attention", step)
        self.assertIn("in-kind", step["detail"])

    def test_form_export_json_readiness(self):
        """TJ-0251 (TJ-0375): form-export --json carries the same
        readiness record as sum --json."""
        r = _cli(self.root, "form-export", "--json")
        doc = json.loads(r.stdout)
        kinds = [i["kind"] for i in doc["readiness"]["issues"]]
        self.assertIn("in-kind-not-booked", kinds)
        self.assertFalse(doc["readiness"]["complete"])
        s = json.loads(_cli(self.root, "sum", "--json").stdout)
        self.assertEqual(sorted(kinds), sorted(
            i["kind"] for i in s["readiness"]["issues"]))

    def test_form_export_text_has_an_act_line(self):
        """TJ-0251: and a `! ` line in its text view."""
        r = _cli(self.root, "form-export", width="120")
        acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
        self.assertTrue(any("in-kind move" in a and "NOT booked" in a
                            and "tjs transfers" in a for a in acts),
                        r.stdout)
        r = _cli(self.root, "form-export", "--details", width="120")
        self.assertIn("NOT in these rows", r.stdout)

    def test_readiness_build(self):
        from taxjson.lib import readiness as RD
        self.assertTrue(RD.build()["complete"])
        rec = RD.build(in_kind_unbooked=2)
        self.assertFalse(rec["complete"])
        self.assertEqual(rec["issues"][0]["count"], 2)


class TestRunStateProblemsWhole(unittest.TestCase):
    """TJ-0109: one inputs-changed problem is one problem."""

    def test_detail_with_semicolons_stays_one_problem(self):
        from taxjson.bin import taxjson_run as TR
        from taxjson.lib import checklist as CL
        one = ("inputs changed since the last full run (changed: "
               "inputs/margin/a.tt; added: inputs/margin/b.tt)")
        res = CL.Result("run-clean", "attention",
                        "; ".join([one, "pending elections"]),
                        problems=[one, "pending elections"])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(CL, "d_run_clean", return_value=res):
            got = TR._run_state_problems(Path(tmp), {"settings": {
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
            r = _cli(root, "sum", "--json")
            doc = json.loads(r.stdout)
            probs = doc["run_state_problems"]
            self.assertEqual(len(probs), 1, probs)
            self.assertIn("changed: inputs/margin/a.tt", probs[0])
            self.assertIn("added: inputs/margin/b.tt)", probs[0])
            runstate = [i for i in doc["readiness"]["issues"]
                        if i["kind"] == "run-state"]
            self.assertEqual(len(runstate), 1, runstate)
            r = _cli(root, "sum", width="120")
            self.assertIn("(1 problem)", r.stderr.splitlines()[0])


class TestCryptoOnly(unittest.TestCase):
    """TJ-0130, TJ-0162: the scaffold's accounts, only crypto has files."""

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
        self.assertNotIn("run `taxjson run` first", r.stdout + r.stderr)
        self.assertNotIn("Error:", r.stderr)

    def test_grant_since_warning_needs_books(self):
        """TJ-0162: no taxable equity book at all is not maybe-options."""
        from taxjson.bin.taxjson_run import _grant_since_warning
        st = {"country": "canada", "year": 2024}
        accts = {"margin": {"type": "taxable"}}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_grant_since_warning(st, Path(tmp), accts))
        # Without a project to read, the setting alone decides.
        self.assertIsNotNone(_grant_since_warning(st, accounts=accts))


class TestSharedExportsFolder(unittest.TestCase):
    """TJ-0133 and TJ-0155: the years layout, nothing in ../inputs/."""

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
            # option-boundary contract, test_fix_audit_1022_b).
            self.assertNotIn("`taxjson run`", text, cmd)

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
            self.assertNotIn("`taxjson run`", r.stderr)

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
            self.assertIn("Run `taxjson run` first", r.stderr)


class TestStaleWashBeforeAFullRun(unittest.TestCase):
    """TJ-0154: the full run's `prev` snapshot reads the books it is
    about to rebuild without warning that they are stale."""

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


class TestDistributionsLine(unittest.TestCase):
    """TJ-0163."""

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
    """TJ-0165 (warning count): said once per run, naming its file."""

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
    """TJ-0177."""

    def _shown(self, details: bool) -> str:
        from taxjson.bin import taxjson_run as TR
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "120"}), \
                mock.patch.object(TR, "_CURRENT_CMD", "run"), \
                mock.patch.object(TR, "_CURRENT_DETAILS", details), \
                mock.patch.object(TR, "_DETAILS_HIDDEN", [False]), \
                redirect_stdout(buf):
            TR.print_holdings_diff(
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
