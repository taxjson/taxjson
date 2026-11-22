"""The `print` item of step 1 and its reviews (backlog TJ-1525, TJ-1527, TJ-1528,
TJ-1530 to TJ-1533, TJ-1536 to TJ-1545): a message through lib/out says all of
what it said before, one blank line after it, its later lines flush-left.
Synthetic names only.

Formerly: test_fix_step1_print"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from taxjson.project.settings import Settings
from taxjson.cli import books, common
from taxjson.lib import out
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.corp_actions import FILING_REQUIRED_ELECTIONS, Manifest
from taxjson.pipeline import messages, run, run_state
import _paths

SRC = _paths.REPO / "src"


class _Shown(unittest.TestCase):
    """Wrapping on, as a person sees it."""
    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "80"})
        p.start()
        self.addCleanup(p.stop)


class TestAWarningThatAReportIsIncomplete(_Shown):
    def _warned(self):
        err = io.StringIO()
        with mock.patch.object(out, "concise_on", return_value=True), \
                mock.patch.object(common, "load_config",
                                  return_value={"accounts": {"acct1": {}}}), \
                mock.patch.object(common, "_accounts_skipped_for_no_inputs",
                                  return_value=set()), \
                mock.patch.object(common, "_has_inputs", return_value=True), \
                contextlib.redirect_stderr(err):
            common._warn_accounts_without_books(Path("."), [], "wash-sales",
                                                "gains file")
        return " ".join(err.getvalue().split())

    def test_a_default_view_keeps_the_words(self):
        text = self._warned()
        self.assertIn("Warning: no gains file for account(s) acct1", text)
        self.assertIn("their rows are NOT in this report.", text)


class TestOneBlankLineAfterAMessage(_Shown):
    TWO = ["Warning: head", "detail"]

    def test_a_line_starting_with_a_newline_pays_the_debt(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), out.settling_streams():
            out.show(self.TWO, sys.stdout)
            print("\nNext:")
        self.assertEqual(buf.getvalue(), "Warning: head\ndetail\n\nNext:\n")

    def test_a_line_without_one_still_gets_it(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), out.settling_streams():
            out.show(self.TWO, sys.stdout)
            print("Next:")
        self.assertEqual(buf.getvalue(), "Warning: head\ndetail\n\nNext:\n")

    def test_an_entry_starting_with_its_own_blank_line_pays_the_debt(self):
        buf = io.StringIO()
        out.show(self.TWO, buf)
        out.show(["\nfoo"], buf)
        self.assertEqual(buf.getvalue(), "Warning: head\ndetail\n\nfoo\n")

    def test_one_item_of_two_lines_is_two_lines(self):
        buf = io.StringIO()
        out.show(["Warning: head\ndetail"], buf)
        out.show(["Info: next"], buf)
        self.assertEqual(buf.getvalue(), "Warning: head\ndetail\n\nInfo: next\n")

    def test_captured_bytes_are_as_they_were(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
            out.show(["a\nb"], buf)
            out.show(["c"], buf)
        self.assertEqual(buf.getvalue(), "a\nb\nc\n")


class TestAMessageKeptWhole(_Shown):
    TEXT = ("no base file for taxable account(s) acct1: and this headline goes on "
            "well past eighty columns, so a default view would cut it")

    def _emitted(self, **kw):
        err = io.StringIO()
        with mock.patch.object(out, "concise_on", return_value=True), \
                contextlib.redirect_stderr(err):
            out.warn(self.TEXT, details=["Run it first."], **kw)
        return " ".join(err.getvalue().split())

    def test_whole_keeps_the_text_and_the_details(self):
        text = self._emitted(whole=True)
        self.assertIn("so a default view would cut it", text)
        self.assertIn("Run it first.", text)

    def test_without_it_a_default_view_is_one_line(self):
        self.assertNotIn("Run it first.", self._emitted())

    def test_the_run_s_say_too(self):
        err = io.StringIO()
        with mock.patch.object(messages, "_run_concise", return_value=True), \
                contextlib.redirect_stderr(err):
            messages._say("warning", self.TEXT, "Run it first.", whole=True)
        self.assertIn("Run it first.", err.getvalue())


class TestALineOfAListing(_Shown):
    def _shown(self, text):
        buf = io.StringIO()
        out.show_listed(text, buf)
        return buf.getvalue()

    def test_a_labelled_one_is_a_message_flush_left(self):
        text = self._shown("note: " + "a long note that wraps " * 6)
        self.assertTrue(text.startswith("Info: "), text)
        self.assertFalse([ln for ln in text.splitlines() if ln[:1].isspace()], text)

    def test_a_row_stays_indented(self):
        self.assertEqual(self._shown("moved a.csv"), "  moved a.csv\n")

    def test_captured_keeps_its_indent(self):
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
            self.assertEqual(self._shown("note: x"), "  note: x\n")


class TestARelayedDetail(_Shown):
    def _relayed(self, text):
        err = io.StringIO()
        with mock.patch.object(run_state, "current_details", True), \
                contextlib.redirect_stderr(err), out.settling_streams():
            out.show(["Warning: the caller's", "its detail"], sys.stderr)
            common._relay_child_stderr(text)
        return err.getvalue()

    def test_with_no_headline_it_does_not_join_the_caller(self):
        self.assertIn("its detail\n\n", self._relayed("  a progress line\n"))

    def test_after_this_relay_s_headline_it_continues_it(self):
        text = self._relayed("taxjson-gains: warning: x\n  more\n")
        self.assertIn("x\n", text)
        self.assertNotIn("x\n\n", text)
        self.assertIn("more", text)


class TestARelayedMessageIsFlushLeft(_Shown):
    LONG = "taxjson-gains: warning: " + "a long warning that wraps " * 5 + "\n  the detail\n"

    def _relayed(self):
        err = io.StringIO()
        with mock.patch.object(run_state, "current_details", True), \
                contextlib.redirect_stderr(err):
            common._relay_child_stderr(self.LONG)
        return err.getvalue().splitlines()

    def test_shown(self):
        lines = self._relayed()
        self.assertGreater(len(lines), 2)
        self.assertEqual([ln for ln in lines if ln[:1].isspace()], [])

    def test_captured_keeps_the_detail_s_indent(self):
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
            self.assertIn("  the detail", self._relayed())


class TestFindMissingHistoryRelaysAChild(_Shown):
    def _relayed(self, text, details=False):
        err = io.StringIO()
        with mock.patch.object(run_state, "current_details", details), \
                contextlib.redirect_stderr(err):
            books._relay_gains_stderr(text, "/tmp/copy.csv", "a.csv")
        return err.getvalue()

    def test_the_label_first_and_the_file_named(self):
        self.assertEqual(self._relayed("taxjson-gains: warning: odd row\n"),
                         "Warning: taxjson-gains: odd row [a.csv]\n")

    def test_lines_about_the_temporary_copy_are_left_out(self):
        self.assertEqual(self._relayed("taxjson-gains: warning: /tmp/copy.csv\n"
                                       "\n"), "")

    def test_a_detail_only_with_details(self):
        text = "taxjson-gains: warning: odd row\n  the row\n"
        self.assertNotIn("the row", self._relayed(text))
        self.assertIn("the row", self._relayed(text, details=True))

    def test_nothing_is_nothing(self):
        self.assertEqual(self._relayed(None), "")

    def test_an_error_s_fix_shows_without_details(self):
        text = self._relayed("taxjson-gains: error: bad row\n  the fix: do this\n")
        self.assertIn("Error: taxjson-gains: bad row [a.csv]", text)
        self.assertIn("the fix: do this", text)

    def test_a_dropped_message_takes_its_details_with_it(self):
        text = self._relayed("taxjson-gains: warning: other\n"
                             "Wrote 1 to /tmp/copy.csv\n  its hint\n", details=True)
        self.assertEqual(text, "Warning: taxjson-gains: other [a.csv]\n")

    def test_a_detail_naming_the_copy_goes(self):
        text = self._relayed("taxjson-gains: warning: other\n  see /tmp/copy.csv\n",
                             details=True)
        self.assertNotIn("/tmp/copy.csv", text)

    def test_it_does_not_count_an_older_nested_command_as_said(self):
        with run_state.nested_command():
            run_state.current.ambiguous_warned.add("QZA")
        self.addCleanup(setattr, run_state, "_last_nested", None)
        self._relayed("taxjson-gains: warning: other\n")
        self.assertNotIn("QZA", run_state.current.ambiguous_warned)
        common._relay_child_stderr("")
        self.assertIn("QZA", run_state.current.ambiguous_warned)
        run_state.current.ambiguous_warned.discard("QZA")


class TestTheTemporaryFile(unittest.TestCase):
    def _suggest(self, returncode):
        seen = {}

        def child(cmd, **_kw):
            at = cmd.index("--suggest-missing-history") + 1
            seen["path"] = Path(cmd[at])
            seen["path"].write_text("[]", encoding="utf-8")
            return SimpleNamespace(stdout="", stderr="", returncode=returncode)
        args = SimpleNamespace(include_options=False, all_history=False)
        with mock.patch("taxjson.pipeline.dispatch.run_cmd", child), \
                mock.patch.object(books, "load_settings", return_value={}), \
                mock.patch.object(books, "_country", return_value="canada"), \
                mock.patch.object(books, "_tax_date", return_value="trade"), \
                mock.patch.object(books, "option_timing_flags", return_value=[]), \
                mock.patch.object(books, "income_dating_flags", return_value=[]):
            try:
                result = books._gains_suggestions(args, Path("."), Path("a.csv"),
                                                  2025, False, "--x")
            except SystemExit:
                result = None
        return seen["path"], result

    def test_it_is_removed_when_the_child_fails(self):
        path, result = self._suggest(2)
        self.assertIsNone(result)
        self.assertFalse(path.exists())

    def test_it_is_removed_when_the_child_succeeds(self):
        path, result = self._suggest(0)
        self.assertEqual(result, [])
        self.assertFalse(path.exists())


class TestAFilingReminderIsNeverLostInSilence(_Shown):
    def _reminded(self, error, concise=True):
        err = io.StringIO()
        with mock.patch.object(run, "_filing_reminder", side_effect=error), \
                mock.patch.object(messages, "_run_concise", return_value=concise), \
                contextlib.redirect_stderr(err):
            run._say_filing_reminder({}, {}, Path("."), Path("."), Path("."))
        return " ".join(err.getvalue().split())

    def test_a_bug_in_it_says_so_and_the_run_goes_on(self):
        text = self._reminded(NameError("a bug"))
        self.assertIn("could not check the elections for a FILING REQUIRED", text)
        self.assertIn("NameError: a bug", text)
        self.assertIn("tjs elect", text)

    def test_an_error_with_no_text_is_still_named(self):
        self.assertIn("AssertionError", self._reminded(AssertionError()))

    def test_a_closed_stderr_does_not_break_the_run(self):
        with mock.patch.object(run, "_filing_reminder",
                               side_effect=BrokenPipeError()), \
                mock.patch.object(run, "_say", side_effect=BrokenPipeError()):
            run._say_filing_reminder({}, {}, Path("."), Path("."), Path("."))

    def test_a_default_run_shows_the_obligation_whole(self):
        election, todo = next(iter(FILING_REQUIRED_ELECTIONS.items()))
        rec = SimpleNamespace(election=election, summary="2025 QZA rollover",
                              event_id="e1")
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(messages, "_run_concise", return_value=True), \
                mock.patch.object(Manifest, "load",
                                  return_value=SimpleNamespace(records={"e1": rec})), \
                contextlib.redirect_stderr(err):
            root = Path(t)
            with mock.patch.object(run, "_manifest_path_for",
                                   return_value=root / "taxjson.toml"):
                (root / "taxjson.toml").write_text("", encoding="utf-8")
                run._filing_reminder(
                    {"accounts": {"acct1": {"type": "taxable"}}},
                    Settings.from_table({"year": 2025}),
                    root, root, root)
        text = " ".join(err.getvalue().split())
        self.assertIn("FILING REQUIRED for 1 election(s)", text)
        self.assertIn("the paperwork", text)
        self.assertIn("acct1: 2025 QZA rollover", text)
        self.assertIn(" ".join(todo.split()), text)


class TestInitNotes(unittest.TestCase):
    def test_a_stray_folder_s_note_is_flush_left_with_one_blank_line(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "proj"
            (d / "inputs" / "oldacct").mkdir(parents=True)
            env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="120",
                       TAXJSON_OFFLINE="1")
            r = subprocess.run(
                [*python_module_argv("taxjson.bin.taxjson_run"), "init",
                 "--country", "canada", "--single", "--force", "--details", str(d)],
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        at = next(i for i, ln in enumerate(lines) if "oldacct" in ln)
        self.assertTrue(lines[at].startswith("Info: inputs/ has folder(s)"), lines[at])
        self.assertTrue(lines[at + 1].startswith("Add their sections")
                        or lines[at + 1].startswith("Re-add their sections"), lines[at + 1])
        self.assertNotIn("\n\n\n", r.stdout)


if __name__ == "__main__":
    unittest.main()
