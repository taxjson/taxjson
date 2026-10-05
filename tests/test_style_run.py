"""`taxjson run`, `init`, `format`, `migrate` and `fetch` in the house
output style (docs/output-style.md), and the stage messages they show:
lib/stage_msg (captured text unchanged, wrapped for a person at display
time), the run's "before you trust these numbers" summary.

Layout is pinned with out.lint at width 100 on the synthetic style
projects (tests/_style.py); the captured forms (.diag, width 0) are
pinned byte for byte. Every fixture is synthetic."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _style import Project, assert_styled, env, project

from taxjson.lib import out

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Width(unittest.TestCase):
    """Each test sets TAXJSON_WIDTH itself (the suite runs with 0)."""

    def setUp(self):
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("TAXJSON_WIDTH", None)


def _flat(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------- lib/stage_msg
class TestStageMessages(_Width):
    LONG = ("warning: ATTENTION: x.csv: the statement has no Cash Report "
            "— parsed money is NOT reconciled against the broker's own "
            "totals. Include the Cash Report section in the export.")

    def test_say_keeps_the_captured_text(self):
        from taxjson.lib.stage_msg import say
        err = io.StringIO()
        with out.unwrapped(), contextlib.redirect_stderr(err):
            say("note", "head", ["detail " * 30], legacy="NOTE: one line")
        self.assertEqual(err.getvalue(), "NOTE: one line\n")

    def test_say_for_a_person_is_headline_and_details(self):
        from taxjson.lib.stage_msg import say
        err = io.StringIO()
        # (as the process's own stderr: a buffer a caller reads gets the
        # captured form, test_a_redirected_stream_is_captured)
        with contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "__stderr__", err):
            say("note", "3 rows rewritten", ["detail " * 30],
                legacy="NOTE: one line")
        lines = err.getvalue().splitlines()
        self.assertEqual(lines[0], "note: 3 rows rewritten")
        self.assertTrue(all(ln.startswith("  ") for ln in lines[1:]))
        self.assertEqual(out.lint(err.getvalue()), [])

    def test_a_redirected_stream_is_captured(self):
        # An in-process caller reading a stage's stderr line by line
        # (t1135, wash-radar) gets the lines it always got.
        from taxjson.lib.stage_msg import emit_line, say
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            emit_line(self.LONG)
            say("note", "head", ["detail"], legacy="NOTE: one line")
        self.assertEqual(err.getvalue(), self.LONG + "\nNOTE: one line\n")

    def test_emit_line_is_identity_when_captured(self):
        from taxjson.lib.stage_msg import emit_line
        err = io.StringIO()
        with out.unwrapped(), contextlib.redirect_stderr(err):
            emit_line(self.LONG)
            emit_line("NOTE: kept as captured")
        self.assertEqual(err.getvalue(),
                         self.LONG + "\nNOTE: kept as captured\n")

    def test_console_lines_keep_the_marker_first_line(self):
        from taxjson.lib.stage_msg import console_lines
        lines = console_lines(self.LONG, "  ", width_=100)
        self.assertEqual(lines[0], "  warning: ATTENTION: x.csv: the "
                                   "statement has no Cash Report")
        self.assertTrue(lines[1].startswith("    Parsed money is NOT"))
        self.assertEqual(out.lint("\n".join(lines)), [])
        self.assertEqual(_flat(" ".join(lines)).replace("Parsed", "parsed"),
                         _flat(self.LONG.replace(" —", "")))
        # Nothing wraps (captured, or TAXJSON_WIDTH=0): the one line.
        self.assertEqual(console_lines(self.LONG, "  ", width_=0),
                         ["  " + self.LONG])
        # A retired prefix the captured text keeps is shown lower case.
        self.assertEqual(console_lines("NOTE: x", "", width_=100),
                         ["note: x"])


# ------------------------------------------- the run's closing summary
class TestFirstRunSummaryCount(_Width):
    def _doc(self, n):
        return {"year": 2024, "unchecked_accounts": [
            {"account": f"acct{i}", "positions": i + 1} for i in range(n)]}

    def test_every_counted_account_is_named(self):
        # The line said "5 account(s)" and named four (display cut).
        from taxjson.lib.first_run import render
        text = _flat("\n".join(render(self._doc(5), width_=100)))
        self.assertIn("5 account(s) with open positions", text)
        for i in range(5):
            self.assertIn(f"acct{i} ({i + 1})", text)
        self.assertNotIn("more", text)

    def test_a_long_list_says_how_many_more(self):
        from taxjson.lib.first_run import render
        text = _flat("\n".join(render(self._doc(9), width_=100)))
        self.assertIn("9 account(s)", text)
        self.assertIn("acct5 (6) +3 more", text)
        self.assertNotIn("acct6", text)

    def test_layout(self):
        from taxjson.lib.first_run import render
        lines = render(self._doc(9), width_=100)
        self.assertTrue(lines[0].startswith("==> before you trust"))
        self.assertTrue(lines[1].startswith("  - 9 account(s)"))
        self.assertEqual(out.lint("\n".join(lines)), [])
        # Unwrapped: one line per finding.
        self.assertEqual(len(render(self._doc(9), width_=0)), 3)


# --------------------------------------------------- the commands, live
class TestRunStyle(unittest.TestCase):
    """A full run on a copy of the synthetic projects, piped (width 100)."""

    def _copy(self, country, pending=False):
        src = project(country, pending=pending)
        tmp = tempfile.mkdtemp(prefix="taxjson_style_run_")
        self.addCleanup(shutil.rmtree, tmp, True)
        dst = Path(tmp) / country
        shutil.copytree(src.root, dst)
        return Project(dst, country)

    def test_run_console(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = self._copy(country)
                r = p.run("run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_styled(self, r.stdout)
                assert_styled(self, r.stderr)
                # Files the run wrote are named relative to the project.
                self.assertIn("  wrote reports/margin.sum", r.stdout)
                self.assertNotIn(str(p.root), r.stdout)
                self.assertIn("\nDone. Reports in reports/\n", r.stdout)
                # An ATTENTION line keeps its marker first line.
                self.assertRegex(r.stdout, r"\n  warning: ATTENTION: \S")

    def test_pending_run(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = self._copy(country, pending=True)
                r = p.run("run", "--no-input")
                self.assertEqual(r.returncode, 3, r.stderr)
                assert_styled(self, r.stdout)
                assert_styled(self, r.stderr, allow=("taxjson elect ",))

    def test_captured_stage_text_is_unchanged(self):
        # The .diag keeps the one-line ATTENTION text the console wraps.
        p = self._copy("canada")
        r = p.run("run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        diag = (p.root / "work" / "margin_ib.json.diag").read_text()
        long = [ln for ln in diag.splitlines()
                if ln.startswith("warning: ATTENTION:") and len(ln) > 100]
        self.assertTrue(long, diag)
        self.assertIn(long[0].split(" — ")[0], r.stdout)

    def test_init(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as tmp:
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "init",
                     "--country", country, "--year", "2024",
                     str(Path(tmp) / "p")], capture_output=True, text=True,
                    env=env(), cwd=tmp, stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_styled(self, r.stdout, allow=("taxjson -C ",))
                assert_styled(self, r.stderr)
                self.assertIn("  3. Run:\n       taxjson -C ", r.stdout)
                if country == "usa":
                    self.assertTrue(r.stderr.lstrip().startswith(
                        "taxjson init: note: the US engine is "
                        "EXPERIMENTAL"), r.stderr)

    def test_fetch_list(self):
        r = project("canada").run("fetch", "--list")
        if r.returncode == 2:
            # No fetcher plugin installed here: one error, one message.
            self.assertIn("taxjson fetch: error: no fetcher is installed",
                          r.stderr)
            assert_styled(self, r.stderr, allow=("--with-fetch",))
            return
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_styled(self, r.stdout)


if __name__ == "__main__":
    unittest.main()
