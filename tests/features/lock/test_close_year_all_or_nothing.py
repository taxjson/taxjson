"""Backlog TJ-4627: `tjs close-year` is all or nothing.

Its three writes (the freeze of the inputs: the copies in the year's own
inputs/ and taxjson.toml rewritten to read them; the reports as filed,
filed/<year>/reports/; the lock, filed/<year>.json) are one step, the lock
written last as the commit point. An exception or an interrupt at any of
close-year's calls either leaves all three written, the year closed (and,
when the report of the close stops, a warning that says so), or none of
them: the project byte for byte as it was, modes included, and a message
that says nothing was written. An input that cannot be read keeps its
line and exit 2; any other error (a bug) is one Error, then its traceback,
as before. It used to stop after the lock with the inputs frozen and no
reports as filed, the year half filed; a failed copy of the reports was a
warning after the lock.

Each case runs `tjs close-year --yes` in-process on its own copy of
`tjs init --demo`'s 2024 folder, run once for the class (`tjs run
--no-input`), with one call made to raise RuntimeError, OSError or
KeyboardInterrupt, in its place or right after it returns, or with real
SIGINTs: one at a call and one more at every write after it, or three
3 ms apart at a random time in the writes. A Ctrl-C pressed again stopped
the putting back halfway, the year half filed (review of TJ-4627: 63 of
200 closes). `--force` over a filed year that stops before the lock keeps
the old lock and reports. A Ctrl-C as a write's temp file opens stops the
close the same way, never "Bad file descriptor" (backlog TJ-4893). A file
or folder it cannot read or write is exit 2; a Ctrl-C some code swallowed
still stops it, and one as it ends says the year is closed (TJ-4896).

Synthetic data only (the demo book).
"""
import contextlib
import errno
import io
import json
import os
import random
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
from taxjson.bin import taxjson_filed
from taxjson.cli import file as cli_file
from taxjson.cli.main import main as tjs_main
from taxjson.lib import checklist as lib_checklist
from taxjson.lib import compare as lib_compare
from taxjson.lib import safe_write
from taxjson.lib import write_step
from taxjson.lib.cli_diag import InputContentError
from taxjson.lib.dispatch import python_module_argv
from taxjson.pipeline import run_state
import _paths

YEAR = "2024"
KINDS = ("RuntimeError", "OSError", "KeyboardInterrupt")
# Closes with three real SIGINTs at a random time in the writes.
TRIALS = 24


# This module's own file, from a code object of it (the layout test
# allows no `__file__`): an injected call's frames here are skipped.
_THIS_FILE = (lambda: None).__code__.co_filename


def _exc(kind: str) -> BaseException:
    if kind == "OSError":
        return OSError(errno.EIO, "injected I/O error")
    if kind == "KeyboardInterrupt":
        return KeyboardInterrupt()
    return RuntimeError("injected")


def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs ARGS` as a child of this tree's code, offline, width 0."""
    env = _hermetic.environ(TAXJSON_WIDTH="0", NO_COLOR="1", TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = str(_paths.SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run", list(args)),
                          cwd=cwd, capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=900)


def _close(year_dir: Path, *extra: str, alarm: float = 0.0):
    """`tjs -C YEAR_DIR close-year --yes EXTRA` in this process: (exit
    code or the SystemExit text, stdout, stderr, the exception that left
    `main` or None). The run lock is this call's own. `alarm`: SIGALRM
    that many seconds after the start, then every 3 ms until stopped."""
    out, err = io.StringIO(), io.StringIO()
    code, escaped = 0, None
    held, run_state.run_lock = run_state.run_lock, None
    argv = ["tjs", "-C", str(year_dir), "close-year", "--yes", *extra]
    try:
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                if alarm:
                    signal.setitimer(signal.ITIMER_REAL, alarm, 0.003)
                try:
                    tjs_main()
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
            except SystemExit as e:
                code = e.code
            except (RuntimeError, OSError) as e:        # an injected one, raised on
                escaped = e
    finally:
        if run_state.run_lock is not None:
            run_state.run_lock.close()
        run_state.run_lock = held
    return code, out.getvalue(), err.getvalue(), escaped


def _tree(top: Path) -> dict:
    """Every entry below `top` but the year's work/ (the run lock): its
    kind, permission bits and bytes (a link: its text; the lock without
    its closed_at line)."""
    seen = {}
    for p in sorted(top.rglob("*")):
        rel = p.relative_to(top).as_posix()
        if rel == f"{YEAR}/work" or rel.startswith(f"{YEAR}/work/"):
            continue
        mode = stat.S_IMODE(p.lstat().st_mode)
        if p.is_symlink():
            seen[rel] = ("link", mode, os.readlink(p))
        elif p.is_dir():
            seen[rel] = ("dir", mode, None)
        else:
            data = p.read_bytes()
            if rel == f"{YEAR}/filed/{YEAR}.json":
                data = b"\n".join(ln for ln in data.split(b"\n")
                                  if b'"closed_at"' not in ln)
            seen[rel] = ("file", mode, data)
    return seen


def _changed(now: dict, was: dict) -> list:
    """The entries of two `_tree`s that differ, each named (a diff of
    the bytes themselves is too slow to show)."""
    return sorted(k for k in set(now) | set(was) if now.get(k) != was.get(k))


def _in_close(frame=None) -> bool:
    """cmd_close_year is running (on the stack from `frame` up)."""
    f = frame or sys._getframe(1)
    while f is not None and f.f_code is not cli_file.cmd_close_year.__code__:
        f = f.f_back
    return f is not None


def _ctrl_c() -> None:
    """A real Ctrl-C: SIGINT to this process (its handler runs here)."""
    os.kill(os.getpid(), signal.SIGINT)


class _Inject:
    """`obj.attr` raising `exc` at its `nth` call from a function named
    `caller` (any caller when None; this file's wrappers skipped): in the
    call's place (`after` False) or once it has returned; `exc` "SIGINT":
    a real Ctrl-C there instead. `fired` says it did."""

    def __init__(self, obj, attr, exc, *, after=False, caller=None, nth=1):
        self.obj, self.attr, self.exc = obj, attr, exc
        self.after, self.caller, self.nth = after, caller, nth
        self.calls, self.fired = 0, False
        self.real = getattr(obj, attr)
        inject = self

        def fake(*a, **kw):
            f = sys._getframe(1)
            while f.f_code.co_filename == _THIS_FILE:
                f = f.f_back
            if not inject.fired and inject.caller in (None, f.f_code.co_name):
                inject.calls += 1
                if inject.calls == inject.nth:
                    inject.fired = True
                    if inject.after:
                        inject.real(*a, **kw)
                    if inject.exc != "SIGINT":
                        raise inject.exc
                    _ctrl_c()
            return inject.real(*a, **kw)
        self.fake = fake

    def patch(self):
        return mock.patch.object(self.obj, self.attr, self.fake)


# The calls, each (name, object, attribute, keywords). Before the lock is
# written: nothing may be left. The checks first, then the freeze, the
# reports as filed and the lock itself.
BEFORE = (
    ("refusals", cli_file, "_close_year_refusals", {}),
    ("checks", cli_file, "_close_year_checks", {}),
    ("extra", cli_file, "_close_year_extra", {}),
    ("after extra", cli_file, "_close_year_extra", {"after": True}),
    ("freeze", cli_file, "_frozen_with_books", {}),
    ("second copy", shutil, "copy2", {"caller": "_freeze_inputs", "nth": 2}),
    ("toml rewrite", cli_file, "_write_atomic", {"caller": "_freeze_inputs"}),
    ("hashes after the rewrite", lib_checklist, "frozen_files",
     {"caller": "_freeze_inputs"}),
    ("after the freeze", cli_file, "_freeze_inputs", {"after": True}),
    ("book hashes", lib_checklist, "book_hashes", {"caller": "_frozen_with_books"}),
    ("after freeze and hashes", cli_file, "_frozen_with_books", {"after": True}),
    ("return row", cli_file, "_return_row_totals", {}),
    ("box 18", cli_file, "_box18_by_year", {}),
    ("reports", cli_file, "_keep_filed_reports", {}),
    ("reports read", lib_compare, "report_files", {"caller": "_keep_filed_reports"}),
    ("third report", cli_file, "_write_atomic", {"caller": "_keep_filed_reports", "nth": 3}),
    ("reports renamed", Path, "rename", {"caller": "_keep_filed_reports"}),
    ("after the reports", cli_file, "_keep_filed_reports", {"after": True}),
    ("lock", taxjson_filed, "write_snapshot", {}),
)
# Once the lock is written: everything is, and the year is closed.
AFTER = (
    ("after the lock", taxjson_filed, "write_snapshot", {"after": True}),
    ("report", cli_file, "_close_year_report", {}),
    ("frozen line", cli_file, "_frozen_summary", {}),
    ("carryforwards", cli_file, "_carryforwards_summary", {}),
)
# --force over a filed year, before its lock is replaced: the old reports
# moved aside (the first rename), the new ones into place (the second).
FORCE_BEFORE = (
    ("old reports aside", Path, "rename", {"caller": "_keep_filed_reports"}),
    ("new reports in", Path, "rename", {"caller": "_keep_filed_reports", "nth": 2}),
    ("after the reports", cli_file, "_keep_filed_reports", {"after": True}),
    ("lock", taxjson_filed, "write_snapshot", {}),
)
# The points with something written before them.
WRITTEN_BEFORE = BEFORE[BEFORE.index(next(p for p in BEFORE if p[0] == "second copy")):]
# The calls that write or remove what the close puts back.
WRITES = ((Path, "unlink"), (Path, "rmdir"), (Path, "rename"), (Path, "mkdir"),
          (shutil, "rmtree"), (shutil, "copy2"), (cli_file, "_write_atomic"))


@contextlib.contextmanager
def _ctrl_c_again(first: _Inject):
    """Once `first` has fired, a real Ctrl-C before every call in WRITES
    close-year makes; yields the list of those sent."""
    sent = []

    def wrap(real):
        def again(*a, **kw):
            if first.fired and _in_close():
                sent.append(1)
                _ctrl_c()
            return real(*a, **kw)
        return again
    with contextlib.ExitStack() as st:
        for obj, attr in WRITES:
            st.enter_context(mock.patch.object(obj, attr, wrap(getattr(obj, attr))))
        yield sent


@contextlib.contextmanager
def _ctrl_c_as_a_temp_opens(nth: int):
    """A real Ctrl-C as the `nth` temp file safe_write.atomic_open makes
    in close-year opens: `open` has made its file object, nothing is
    written to it yet (SIGINT is held until `open` returns, so its
    handler runs as the object is handed back). Yields the names of the
    files written that way, in turn (nth 0: none stopped)."""
    real = io.open
    opening = safe_write.atomic_open.__wrapped__.__code__
    opened = []

    def fake(*a, **kw):
        f = sys._getframe(1)
        while f is not None and f.f_code is not opening:
            f = f.f_back
        if f is None or not _in_close():
            return real(*a, **kw)
        opened.append(Path(f.f_locals["final"]).name)
        if len(opened) != nth:
            return real(*a, **kw)
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        try:
            _ctrl_c()
            return real(*a, **kw)
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
    with mock.patch.object(io, "open", fake), mock.patch("builtins.open", fake):
        yield opened


class CloseYearAllOrNothing(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Python's own Ctrl-C handler, as at the top of `tjs` (a process
        # started in the background may ignore SIGINT).
        cls._sigint = signal.signal(signal.SIGINT, signal.default_int_handler)
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "demo"
        r = _cli(Path(cls._td.name), "init", "--demo", str(top))
        assert r.returncode == 0, r.stdout + r.stderr
        r = _cli(top / YEAR, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.base = top
        # The same year closed once, for --force.
        cls.closed = Path(cls._td.name) / "closed"
        shutil.copytree(top, cls.closed, symlinks=True)
        code, out, err, escaped = _close(cls.closed / YEAR)
        assert code == 0 and escaped is None, (code, out, err, escaped)
        cls.closed_tree = _tree(cls.closed)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()
        signal.signal(signal.SIGINT, cls._sigint)

    def _copy(self, src: Path) -> Path:
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        top = Path(t.name) / "p"
        shutil.copytree(src, top, symlinks=True)
        return top

    def _run(self, src: Path, point, kind, *extra: str):
        """A close of a copy of `src` stopped at `point` by `kind` (a
        KINDS name, an exception, "SIGINT")."""
        _name, obj, attr, kw = point
        top = self._copy(src)
        before = _tree(top)
        inj = _Inject(obj, attr, _exc(kind) if kind in KINDS else kind, **kw)
        with inj.patch():
            res = _close(top / YEAR, *extra)
        self.assertTrue(inj.fired, f"{attr} was not called as the case says")
        return top, before, res

    def assert_nothing_written(self, top, before, res, kind):
        code, out, err, escaped = res
        if kind == "KeyboardInterrupt":
            self.assertEqual(code, 130, err)
            self.assertIn("close-year: error: interrupted", err)
        elif kind == "RuntimeError":
            # A bug: one Error, then its traceback, as before.
            self.assertIsInstance(escaped, RuntimeError, (code, err))
            self.assertIn("tjs close-year: error: stopped before the lock was "
                          "written: nothing was written", err)
            self.assertNotIn("This is a bug", err)
        elif isinstance(code, str):     # a refusal: its own words, exit 1
            self.assertIsNone(escaped, f"a traceback: {escaped!r}\n{err}")
            self.assertTrue(code.startswith("tjs close-year: error: "), code)
            self.assertIn("Nothing was written.", code)
        else:                           # what cannot be read or written: exit 2
            self.assertIsNone(escaped, f"a traceback: {escaped!r}\n{err}")
            self.assertEqual(code, 2, err)
            self.assertIn("tjs close-year: error: ", err)
            self.assertIn("Nothing was written", err)
        self.assertNotIn("is closed", err)
        self.assertEqual(_changed(_tree(top), before), [])

    def assert_all_written(self, top):
        y = top / YEAR
        lock = json.loads((y / "filed" / f"{YEAR}.json").read_text())
        self.assertEqual(lock["year"], int(YEAR))
        self.assertIn("frozen by `tjs close-year`", (y / "taxjson.toml").read_text())
        self.assertTrue(any((y / "inputs" / "margin").iterdir()))
        rep = {p.name: p.read_bytes() for p in (y / "reports").iterdir()
               if p.is_file() and not p.name.startswith(".")}
        kept = {p.name: p.read_bytes()
                for p in (y / "filed" / YEAR / "reports").iterdir()}
        self.assertTrue(rep)
        self.assertEqual(kept, rep)

    def test_a_close_writes_all_three(self):
        top = self._copy(self.base)
        code, out, err, escaped = _close(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"CLOSED {YEAR}", out)
        self.assertIn(f"reports as filed: filed/{YEAR}/reports/", out)
        self.assert_all_written(top)
        self.assertEqual(sorted(p.name for p in (top / YEAR / "filed" / YEAR).iterdir()),
                         ["reports"])

    def test_stopped_before_the_lock_nothing_is_written(self):
        for point in BEFORE:
            for kind in KINDS:
                with self.subTest(point=point[0], kind=kind):
                    top, before, res = self._run(self.base, point, kind)
                    self.assert_nothing_written(top, before, res, kind)
                    self.assertFalse((top / YEAR / "filed").exists())

    def test_stopped_after_the_lock_the_year_is_closed(self):
        for point in AFTER:
            for kind in KINDS:
                with self.subTest(point=point[0], kind=kind):
                    top, _before, (code, _o, err, escaped) = self._run(
                        self.base, point, kind)
                    self.assert_all_written(top)
                    self.assertIn(f"warning: {YEAR} is closed: filed/{YEAR}.json is "
                                  f"written, but the report of the close stopped", err)
                    if kind == "KeyboardInterrupt":
                        self.assertEqual(code, 130, err)
                    elif kind == "OSError":     # the house's one line, exit 2
                        self.assertEqual(code, 2, err)
                        self.assertIn("error: [Errno 5] injected I/O error", err)
                    else:                       # raised on, as it was
                        self.assertIsInstance(escaped, RuntimeError, err)

    def test_force_stopped_before_the_lock_keeps_the_old_reports(self):
        for point in FORCE_BEFORE:
            for kind in KINDS:
                with self.subTest(point=point[0], kind=kind):
                    top, before, res = self._run(self.closed, point, kind, "--force")
                    self.assert_nothing_written(top, before, res, kind)

    def test_force_replaces_the_reports_and_leaves_no_holding_folder(self):
        top = self._copy(self.closed)
        old = top / YEAR / "filed" / YEAR / "reports" / "fees.rpt"
        old.write_bytes(old.read_bytes() + b"# a line of the old reports\n")
        code, out, err, escaped = _close(top / YEAR, "--force")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"CLOSED {YEAR}", out)
        self.assert_all_written(top)
        self.assertNotIn(b"a line of the old reports", old.read_bytes())
        self.assertEqual(sorted(p.name for p in (top / YEAR / "filed" / YEAR).iterdir()),
                         ["reports"])

    def test_unreadable_reports_are_refused_before_the_lock(self):
        # A copy that cannot be made was a warning after the lock: the
        # year closed with nothing for `tjs compare filed` to diff.
        top = self._copy(self.base)
        before = _tree(top)
        bad = top / YEAR / "reports" / "fees.rpt"
        mode = stat.S_IMODE(bad.stat().st_mode)
        bad.chmod(0)
        try:
            code, out, err, escaped = _close(top / YEAR)
        finally:
            bad.chmod(mode)
        self.assertIsNone(escaped, err)
        self.assertEqual(code, 2, err)          # an input that cannot be read
        self.assertIn("tjs close-year: error: the reports as filed cannot be kept: "
                      "reports/fees.rpt cannot be read", err)
        self.assertIn("Nothing was written.", err)
        self.assertEqual(_changed(_tree(top), before), [])

    def test_an_input_error_keeps_its_line_and_exit_2(self):
        # As every command says them (lib/cli_diag guard_main), nothing
        # written added: it was "close-year stopped before the lock was
        # written: RuntimeError: ... This is a bug in taxjson", exit 1.
        cases = (
            (lambda: RuntimeError("Symlink loop from '/nowhere/loop'"),
             "cannot read input: Symlink loop from '/nowhere/loop'"),
            (lambda: InputContentError("ticker.map: a made-up refusal"),
             "ticker.map: a made-up refusal"),
            (lambda: FileNotFoundError(errno.ENOENT, "No such file or directory",
                                       "/nowhere/x.csv"),
             "no such file: /nowhere/x.csv"))
        for make, line in cases:
            for point in (BEFORE[4], BEFORE[-1]):       # the freeze, the lock
                with self.subTest(line=line, point=point[0]):
                    top, before, (code, _o, err, escaped) = self._run(
                        self.base, point, make())
                    self.assertIsNone(escaped, err)
                    self.assertEqual(code, 2, err)
                    self.assertIn(f"tjs close-year: error: {line}\n"
                                  f"  Nothing was written", err)
                    self.assertEqual(_changed(_tree(top), before), [])

    def test_ctrl_c_again_while_it_puts_back_waits(self):
        # A real Ctrl-C at a call, then one more at every write and
        # removal after it: the second stopped the putting back.
        for src, points, extra in ((self.base, WRITTEN_BEFORE, ()),
                                   (self.closed, FORCE_BEFORE, ("--force",))):
            for name, obj, attr, kw in points:
                with self.subTest(point=name, force=bool(extra)):
                    top = self._copy(src)
                    before = _tree(top)
                    first = _Inject(obj, attr, "SIGINT", **kw)
                    with first.patch(), _ctrl_c_again(first) as again:
                        res = _close(top / YEAR, *extra)
                    self.assertTrue(first.fired, f"{attr} was not called")
                    self.assertTrue(again, "no write after the first Ctrl-C")
                    self.assert_nothing_written(top, before, res, "KeyboardInterrupt")

    def test_ctrl_c_pressed_three_times_never_half_files(self):
        # Three real SIGINTs 3 ms apart at a random time in the writes
        # (from the freeze to the lock, timed on a close first): each
        # close is written in full, the year closed, or not at all.
        top = self._copy(self.base)
        marks = {}

        def timed(obj, attr):
            real = getattr(obj, attr)

            def at(*a, **kw):
                marks.setdefault(attr, time.monotonic() - marks["start"])
                return real(*a, **kw)
            return mock.patch.object(obj, attr, at)
        marks["start"] = time.monotonic()
        with timed(cli_file, "_frozen_with_books"), timed(cli_file, "_close_year_report"):
            code, _o, err, _e = _close(top / YEAR)
        self.assertEqual(code, 0, err)
        lo, hi = marks["_frozen_with_books"] - 0.004, marks["_close_year_report"] + 0.002
        sent = []

        def alarm(signum, frame):
            if len(sent) >= 3 or not _in_close(frame):
                signal.setitimer(signal.ITIMER_REAL, 0)
                return
            sent.append(1)
            _ctrl_c()
        old = signal.signal(signal.SIGALRM, alarm)
        self.addCleanup(signal.signal, signal.SIGALRM, old)
        rnd = random.Random(4627)
        seen = {"closed": 0, "nothing": 0}
        for i in range(TRIALS):
            at = max(0.0005, rnd.uniform(lo, hi))
            with self.subTest(trial=i, at=round(at, 4)):
                top = self._copy(self.base)
                before = _tree(top)
                sent.clear()
                code, _o, err, escaped = _close(top / YEAR, alarm=at)
                self.assertIsNone(escaped, err)
                if (top / YEAR / "filed" / f"{YEAR}.json").exists():
                    seen["closed"] += 1
                    self.assertEqual(_changed(_tree(top), self.closed_tree), [],
                                     (sent, code, err))
                    self.assertIn(code, (0, 130), err)
                    if code:
                        self.assertIn(f"{YEAR} is closed", err)
                else:
                    seen["nothing"] += 1
                    self.assertEqual(_changed(_tree(top), before), [], (sent, code, err))
                    self.assertEqual(code, 130, err)
        self.assertTrue(seen["nothing"], seen)      # the writes were hit

    def test_ctrl_c_as_a_temp_file_opens_stops_it_never_bad_file_descriptor(self):
        # Backlog TJ-4893: a Ctrl-C as a write's temp file opened closed
        # its descriptor twice, and the second close's "Bad file
        # descriptor" took the interrupt's place (a refusal, exit 2), or
        # the temp file stayed. The close's first write (the books'
        # figures), the freeze's taxjson.toml, the first report as filed
        # and the lock: stopped, nothing written, no temp file left.
        top = self._copy(self.base)
        with _ctrl_c_as_a_temp_opens(0) as opened:
            code, _o, err, escaped = _close(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        names = list(opened)
        self.assertEqual(names[-1], f"{YEAR}.json")     # the lock, last
        toml = names.index("taxjson.toml") + 1
        for nth in sorted({1, toml, toml + 1, len(names)}):
            with self.subTest(nth=nth, file=names[nth - 1]):
                top = self._copy(self.base)
                before = _tree(top)
                with _ctrl_c_as_a_temp_opens(nth) as opened:
                    res = _close(top / YEAR)
                self.assertGreaterEqual(len(opened), nth)
                self.assertNotIn("Bad file descriptor", res[2])
                self.assertEqual(sorted(top.rglob("*.part")), [])
                self.assert_nothing_written(top, before, res, "KeyboardInterrupt")


    def test_files_it_cannot_read_or_write_exit_2(self):
        # Backlog TJ-4896: what close-year cannot read or write is exit
        # 2, as docs/commands.md's exit-code table says (these were 1, a
        # finding's code): the lock's folder, a copy, taxjson.toml's
        # rewrite, the lock itself, a book it locks. Nothing written.
        top = self._copy(self.base)
        (top / YEAR / "filed").write_text("synthetic: a file where the folder goes\n")
        before = _tree(top)
        res = _close(top / YEAR)
        self.assert_nothing_written(top, before, res, "OSError")
        self.assertIn(f"cannot write filed/{YEAR}.json: filed exists and is not a "
                      f"folder", res[2])
        for point, says in (
                (("second copy", shutil, "copy2", {"caller": "_freeze_inputs", "nth": 2}),
                 "cannot copy "),
                (("toml rewrite", cli_file, "_write_atomic", {"caller": "_freeze_inputs"}),
                 "cannot rewrite taxjson.toml: "),
                (("lock write", safe_write, "write_atomic", {"caller": "write_snapshot"}),
                 f"cannot write filed/{YEAR}.json: "),
                (("book read", cli_file, "_read_work_doc", {"caller": "_close_year"}),
                 "could not read work/")):
            with self.subTest(point=point[0]):
                top, before, res = self._run(self.base, point, "OSError")
                self.assert_nothing_written(top, before, res, "OSError")
                self.assertIn(says, res[2])

    def test_a_swallowed_ctrl_c_still_stops_it(self):
        # Backlog TJ-4896: a Ctrl-C whose KeyboardInterrupt some code
        # catches, then one more: the close went on and locked the year,
        # every later Ctrl-C held. Before the lock it stops there,
        # nothing written; after it, the year is closed and said so.
        def swallowing(obj, attr):
            real = getattr(obj, attr)

            def call(*a, **kw):
                for _ in range(2):
                    try:
                        _ctrl_c()
                    except KeyboardInterrupt:
                        pass            # caught on its way: the close goes on
                return real(*a, **kw)
            return mock.patch.object(obj, attr, call)
        for obj, attr, locked in ((cli_file, "_return_row_totals", False),
                                  (cli_file, "_frozen_summary", True)):
            with self.subTest(at=attr):
                top = self._copy(self.base)
                before = _tree(top)
                with swallowing(obj, attr):
                    code, _o, err, escaped = _close(top / YEAR)
                self.assertIsNone(escaped, err)
                self.assertEqual(code, 130, err)
                if locked:
                    self.assertIn(f"{YEAR} is closed", err)
                    self.assertEqual(_changed(_tree(top), self.closed_tree), [])
                else:
                    self.assertNotIn("is closed", err)
                    self.assertEqual(_changed(_tree(top), before), [])

    def test_ctrl_c_as_it_ends_says_the_year_is_closed(self):
        # Backlog TJ-4896: a Ctrl-C as close-year lets go of Ctrl-C, its
        # last step, the lock written: "interrupted" only (exit 130), as
        # if nothing were filed.
        top = self._copy(self.base)
        inj = _Inject(write_step.WriteStep, "release", "SIGINT")
        with inj.patch():
            code, _o, err, escaped = _close(top / YEAR)
        self.assertTrue(inj.fired)
        self.assertIsNone(escaped, err)
        self.assertEqual(code, 130, err)
        self.assertIn(f"{YEAR} is closed", err)
        self.assertEqual(_changed(_tree(top), self.closed_tree), [])


if __name__ == "__main__":
    unittest.main()
