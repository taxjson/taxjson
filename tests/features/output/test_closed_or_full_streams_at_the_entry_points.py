"""TJ-4831: a command or stand-alone tool started with stdout closed (`>&-`), or
with stdout or stderr a full device (`>/dev/full`, `2>/dev/full`), stops with no
Python traceback and never with the interpreter's own exit 120 (synthetic data
only: the demo project and the golden US book, made-up positions).

One guard, at the top of the process (lib/cli_diag `run_top_level`, which the
`tjs` entry point and every `taxjson-*` console script go through): a standard
stream closed at the start is /dev/null, as with `>/dev/null`
(`_closed_streams_to_devnull`), and one a full device refuses is guarded
(`_RoomGuard`), to the process's end (what the interpreter writes after the
command: a `SystemExit` text, an atexit note, its own flush). stdout's loss is
one `cannot write the output` line once the command has run, exit 2 (or the
command's own exit when that is not 0); stderr's is said nowhere (there is
nowhere to say it) and the command keeps its own exit.
"""
import contextlib
import errno
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cli_diag, out
from taxjson.lib.dispatch import python_module_argv
import _paths

_DEV_FULL = "/dev/full"         # every write: ENOSPC (Linux); no disk filled
_LOST = "cannot write the output: No space left on device (disk full?)"
# The widths stderr is written at: captured (0), and shown to a person
# (above 0: lib/markets' rolled-up notes are printed at exit then).
_WIDTHS = ("0", "100")

# (folder, program, arguments): each command TJ-4831 names, run in the demo's
# 2024 folder after `tjs run` ("books"), in a copy of it with the edits that
# give format-map, format and opening something to show ("edited"), or in the
# golden US book ("usa"). `tjs` is the CLI; any other program is the
# taxjson.bin module a `taxjson-*` console script runs (bin/_entry). Each
# writes to stdout.
COMMANDS = (
    ("books", "tjs", ("wash-sales", "--explain")),
    ("books", "tjs", ("audit",)),
    ("books", "tjs", ("form-export", "--json")),
    ("books", "tjs", ("t1135", "--json")),
    ("books", "taxjson_diff", ("work/margin_raw.json", "work/ib_raw.json")),
    ("books", "taxjson_explain", ("--country", "canada", "--color", "work/margin_base.json")),
    ("books", "taxjson_ticker_map", ("work/margin_raw.json",)),
    ("books", "fill_crypto_prices", ("work/crypto_mapped.json", "--project-root", ".")),
    ("books", "taxjson_merge", ("work/margin_raw_base.json", "work/ib_raw_base.json")),
    ("books", "taxjson_sort", ("--dedup", "work/crypto_merged.json")),
    ("books", "taxjson_split_gains", ("work/.blend_gains_wash.json", "--account", "margin",
                                      "--base", "work/margin_base.json",
                                      "--pool-base", "work/.blend_base.json")),
    ("books", "taxjson_brokerage", ("--brokerage", "ib", "../inputs/ib/ib_demo.csv")),
    ("books", "taxjson_merge2", ("work/margin_raw.json", "work/ib_raw.json")),
    ("books", "taxjson_t1135", ("--year", "2024", "work/margin_base.json")),
    ("books", "taxjson_sum_gains", ("work/margin_gains.json",)),
    ("edited", "tjs", ("format-map",)),
    ("edited", "tjs", ("opening", "margin", "pos.toml", "--dry-run")),
    ("edited", "tjs", ("format",)),
    ("usa", "tjs", ("form-export", "--form", "txf")),
)
# A made-up positions file for `tjs opening --dry-run`.
_POSITIONS = ('[meta]\nas_of = "2024-12-31"\n\n[[holding]]\nsymbol = "QZQ.US"\n'
              'quantity = 10\ncurrency = "USD"\ntotal_cost = 450.00\n')


def _env(width="0"):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH=width, NO_COLOR="1")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_paths.SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _argv(program, args):
    if program == "tjs":
        return python_module_argv("taxjson.bin.taxjson_run", args)
    return python_module_argv("taxjson.bin._entry", [program, *args])


def _run(program, args, cwd, *, stdout, stderr=subprocess.PIPE, width="0"):
    """Run `program` in `cwd` at `width` (TAXJSON_WIDTH): stdout "closed"
    (fd 1 not open at the start, as `>&-` leaves it), a file object, or
    PIPE; stderr likewise a file object or PIPE. -> CompletedProcess
    (text)."""
    argv = _argv(program, args)
    if stdout == "closed":
        argv, stdout = ["sh", "-c", 'exec "$@" >&-', "sh", *argv], None
    return subprocess.run(argv, cwd=cwd, env=_env(width), stdin=subprocess.DEVNULL,
                          stdout=stdout, stderr=stderr, text=True, timeout=600,
                          check=False)


def _shown(program, args):
    return " ".join(["tjs" if program == "tjs" else program, *args])


def _unclocked(text):
    """A --json document without the second it was made at."""
    doc = json.loads(text)
    doc.pop("generated_at", None)
    return doc


def _no_crash(test, r, what):
    for mark in ("Traceback", "Exception ignored", "AttributeError"):
        test.assertNotIn(mark, r.stderr or "", what)
    test.assertNotEqual(r.returncode, 120, what)


@unittest.skipUnless(os.name == "posix" and os.path.exists(_DEV_FULL),
                     "needs a POSIX shell and /dev/full")
class TestCommandsWithAClosedOrFullStream(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        r = _run("tjs", ("init", "--demo", str(td / "demo")), td, stdout=subprocess.PIPE)
        assert r.returncode == 0, r.stderr
        books = td / "demo" / "2024"
        r = _run("tjs", ("run", "--no-input"), books, stdout=subprocess.PIPE)
        assert r.returncode == 0, r.stderr[-2000:]
        # The edits: a ticker.map rule out of its group, taxjson.toml's year
        # out of its column, a positions file.
        shutil.copytree(td / "demo", td / "edited")
        edited = td / "edited" / "2024"
        with open(edited / "ticker.map", "a", encoding="utf-8") as f:
            f.write("DELETE QZQZ.US\n")
        toml = edited / "taxjson.toml"
        text, n = re.subn(r"^year  +=", "year =", toml.read_text(encoding="utf-8"),
                          flags=re.M)
        assert n == 1, "the demo's year is in its column"
        toml.write_text(text, encoding="utf-8")
        (edited / "pos.toml").write_text(_POSITIONS, encoding="utf-8")
        # The books older than an input (a comment added to ticker.map):
        # `tjs sum` refuses, exit 1 (lib/out.fail: a SystemExit with its
        # text, which the interpreter prints).
        shutil.copytree(td / "demo", td / "stale")
        stale = td / "stale" / "2024"
        with open(stale / "ticker.map", "a", encoding="utf-8") as f:
            f.write("# a comment\n")
        folders = {"books": books, "edited": edited, "stale": stale}
        # The golden US book; the golden Canada book, whose wash radar
        # rolls an option-contract note up for a person, printed at exit
        # (lib/markets.flush_notes).
        for name, golden in (("usa", "style-usa"), ("canada", "style-canada")):
            folders[name] = td / name
            shutil.copytree(_paths.TESTS / "golden" / golden / "project", folders[name])
            for p in (folders[name], *folders[name].rglob("*")):
                p.chmod(0o700 if p.is_dir() else 0o600)
            r = _run("tjs", ("run", "--no-input"), folders[name], stdout=subprocess.PIPE)
            assert r.returncode == 0, r.stderr[-2000:]
        cls.folders = folders

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_each_runs_with_stdout_closed(self):
        # As with `>/dev/null`: the command runs, exit 0, no traceback (it
        # was an AttributeError on sys.stdout, None, in each of them).
        for folder, program, args in COMMANDS:
            what = _shown(program, args)
            with self.subTest(what):
                r = _run(program, args, self.folders[folder], stdout="closed")
                _no_crash(self, r, what)
                self.assertEqual(r.returncode, 0, f"{what}:\n{r.stderr[-2000:]}")

    def test_each_into_a_full_device_says_so_in_one_line(self):
        for folder, program, args in COMMANDS:
            what = _shown(program, args)
            with self.subTest(what), open(_DEV_FULL, "w", encoding="utf-8") as full:
                r = _run(program, args, self.folders[folder], stdout=full)
                _no_crash(self, r, what)
                self.assertEqual(r.returncode, 2, f"{what}:\n{r.stderr[-2000:]}")
                said = [ln for ln in r.stderr.splitlines() if "No space left" in ln]
                self.assertEqual(len(said), 1, f"{what}:\n{r.stderr[-2000:]}")
                self.assertIn(_LOST, said[0])

    def test_a_run_into_a_full_device_builds_the_books_and_says_so(self):
        # It stopped with a traceback at its first step line and exit 120;
        # now the run completes, the lost output is its error (exit 2) and
        # the books are current.
        with tempfile.TemporaryDirectory() as td:
            shutil.copytree(self.folders["books"].parent, Path(td) / "demo")
            year = Path(td) / "demo" / "2024"
            with open(_DEV_FULL, "w", encoding="utf-8") as full:
                r = _run("tjs", ("run", "--no-input"), year, stdout=full)
            _no_crash(self, r, "tjs run")
            self.assertEqual(r.returncode, 2, r.stderr[-2000:])
            self.assertTrue(r.stderr.rstrip().splitlines()[-1].endswith(
                _LOST + " — free some space on the disk and run again"), r.stderr[-2000:])
            r = _run("tjs", ("sum",), year, stdout=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_stderr_into_a_full_device_keeps_the_exit(self):
        # Exit 120 at the interpreter's own flush; now the exit the command
        # gives with stderr writable, and its stdout: detect-brokerage 0;
        # harvest offline, the demo's symbols priced nowhere: 1; `tjs sum`
        # over stale books, 1 (its error is a SystemExit's text, printed
        # by the interpreter once the command is over); the wash radar's
        # JSON, 0, its rolled-up note printed at exit at a width above 0.
        cases = (("books", "taxjson_detect_brokerage", ("../inputs/ib/ib_demo.csv",), 0),
                 ("books", "tjs", ("harvest",), 1),
                 ("stale", "tjs", ("sum",), 1),
                 ("canada", "tjs", ("wash-radar", "--date", "2024-11-25", "--json"), 0))
        for (folder, program, args, code), width in ((c, w) for c in cases for w in _WIDTHS):
            what = f"{_shown(program, args)} (width {width})"
            with self.subTest(what):
                ref = _run(program, args, self.folders[folder], stdout=subprocess.PIPE,
                           width=width)
                self.assertTrue(ref.stderr.strip(), what)
                self.assertEqual(ref.returncode, code, f"{what}:\n{ref.stderr[-2000:]}")
                with open(_DEV_FULL, "w", encoding="utf-8") as full:
                    r = _run(program, args, self.folders[folder], stdout=subprocess.PIPE,
                             stderr=full, width=width)
                self.assertNotEqual(r.returncode, 120, what)
                if "--json" in args:
                    # The whole document, its clock aside.
                    self.assertEqual((r.returncode, _unclocked(r.stdout)),
                                     (ref.returncode, _unclocked(ref.stdout)), what)
                else:
                    self.assertEqual((r.returncode, r.stdout), (ref.returncode, ref.stdout),
                                     what)


class TestTheGuard(unittest.TestCase):
    """lib/cli_diag `_RoomGuard`, and `run_top_level` over it, in this
    process."""

    def _lost(self, main):
        """Run `main` at the top (run_top_level) with stdout on a full
        device. -> (its result or SystemExit code, the stderr lines)."""
        if not os.path.exists(_DEV_FULL):
            self.skipTest("needs /dev/full")
        err = io.StringIO()
        with open(_DEV_FULL, "w", encoding="utf-8") as full, \
                contextlib.redirect_stdout(full), contextlib.redirect_stderr(err):
            try:
                r = cli_diag.run_top_level("taxjson-zz", main)
            except SystemExit as e:
                r = ("exit", e.code)
            self.assertIs(sys.stdout, full)     # a stream not the process's: put back
        return r, [ln for ln in err.getvalue().splitlines() if ln.strip()]

    def test_lost_output_is_said_once_and_a_failure_keeps_its_exit(self):
        # Ended well: exit 2, the loss its error. A finding or a failure
        # of its own (returned, or sys.exit): kept, the loss said too.
        def ends(how):
            def main():
                print("a line of output\n" * 200)
                return how()
            return main

        def stop(code):
            raise SystemExit(code)
        for how, want in ((lambda: 0, ("exit", 2)), (lambda: None, ("exit", 2)),
                          (lambda: stop(0), ("exit", 2)), (lambda: 1, 1),
                          (lambda: stop(1), ("exit", 1)), (lambda: stop(3), ("exit", 3))):
            with self.subTest(want=want):
                r, lines = self._lost(ends(how))
                self.assertEqual(r, want)
                self.assertEqual(len(lines), 1, lines)
                # (labelled for a person at a width above 0)
                self.assertRegex(lines[0], r"^(taxjson-zz: error|Error): ")
                self.assertIn(_LOST, lines[0])

    def test_a_refused_write_goes_to_devnull_and_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            with open(Path(td) / "out.txt", "w", encoding="utf-8") as f:
                g = cli_diag._RoomGuard(f)
                g.write("kept\n")
                g.flush()
                self.assertIsNone(g.lost)
                if not os.path.exists(_DEV_FULL):
                    self.skipTest("needs /dev/full")
                fd = os.open(_DEV_FULL, os.O_WRONLY)
                os.dup2(fd, f.fileno())
                os.close(fd)
                self.assertEqual(g.write("x" * 64), 64)
                g.flush()                       # no OSError
                self.assertEqual(g.lost.errno, errno.ENOSPC)
                g.writelines(["more\n"])
                g.flush()
                # The descriptor is /dev/null's now: the interpreter's own
                # flush at exit, and a child given it, write there.
                self.assertEqual(os.fstat(f.fileno()).st_rdev, os.stat(os.devnull).st_rdev)
            self.assertEqual((Path(td) / "out.txt").read_text(encoding="utf-8"), "kept\n")

    def test_another_error_is_raised_as_before(self):
        class Gone(io.StringIO):
            def write(self, s):
                raise BrokenPipeError(errno.EPIPE, "Broken pipe")
        g = cli_diag._RoomGuard(Gone())
        with self.assertRaises(BrokenPipeError):
            g.write("x")
        self.assertIsNone(g.lost)

    def test_the_real_stream_is_beneath_both_proxies(self):
        # lib/out's settling proxy over the guard: the checks that ask
        # whether a stream is the process's own (concise_on, stage_msg)
        # still find it.
        buf = io.StringIO()
        proxied = out._Settling(cli_diag._RoomGuard(buf))
        self.assertIs(out.real_stream(proxied), buf)
        self.assertIs(out.real_stream(buf), buf)


if __name__ == "__main__":
    unittest.main()
