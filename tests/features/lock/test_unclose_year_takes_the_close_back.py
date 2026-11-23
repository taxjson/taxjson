"""Backlog TJ-4974: `tjs unclose-year` takes a `tjs close-year` back.

The close wrote the freeze (the year's own copy of the shared exports and
tobase.map, taxjson.toml reading them), the reports as filed and the lock.
The unclose moves the lock and the reports into a backup folder of its own
(filed/<year>.unclosed-<stamp>/, with taxjson.toml as closed and a record,
unclosed.json), names the shared folders in taxjson.toml again and removes
the frozen copies that are the shared files' bytes. All or nothing: a stop
anywhere before the record is written leaves the closed year byte for byte
as it was.

Held here, each on a copy of `tjs init --demo`'s project (2024 run once
for the module, closed once, 2025 started from it once):

- the round trip: close then unclose leaves every file as before the
  close, the backup apart; close, unclose, close gives the first lock;
- a frozen copy that is not the shared file's bytes, or that the shared
  folder does not hold, is kept in the backup, never deleted, and the
  report says the year reads other inputs than it was filed from;
- a later year closed is a refusal; a later year started is named, and
  its `carryover`, `years`, `check-filed` and `checklist` say the truth;
- a lock of an older release (no `frozen` record), a single-folder
  project, a year never closed, a freeze half taken back by hand, an
  edit of taxjson.toml since the close;
- stopped at each of its calls by an exception or a Ctrl-C (and a Ctrl-C
  again at every write after it): nothing changed, or all of it.

Synthetic data only (the demo book).
"""
import contextlib
import errno
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
from taxjson.cli import unclose as cli_unclose
from taxjson.cli.main import main as tjs_main
from taxjson.lib import checklist as lib_checklist
from taxjson.lib import filed_locks
from taxjson.lib import write_step
from taxjson.lib.dispatch import python_module_argv
from taxjson.pipeline import run_state
import _paths

YEAR = "2024"
NEXT = "2025"
KINDS = ("RuntimeError", "OSError", "KeyboardInterrupt")
NOTE = "frozen by `tjs close-year`"

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


def _tjs(folder: Path, *args: str):
    """`tjs -C FOLDER ARGS` in this process: (exit code or the SystemExit
    text, stdout, stderr, the exception that left `main` or None). The
    run lock is this call's own."""
    out, err = io.StringIO(), io.StringIO()
    code, escaped = 0, None
    held, run_state.run_lock = run_state.run_lock, None
    try:
        with mock.patch.object(sys, "argv", ["tjs", "-C", str(folder), *args]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                tjs_main()
            except SystemExit as e:
                code = e.code
            except (RuntimeError, OSError) as e:        # an injected one, raised on
                escaped = e
    finally:
        if run_state.run_lock is not None:
            run_state.run_lock.close()
        run_state.run_lock = held
    return code, out.getvalue(), err.getvalue(), escaped


def _unclose(year_dir: Path, *extra: str):
    return _tjs(year_dir, "unclose-year", *extra)


def _tree(top: Path, *, filed: bool = True) -> dict:
    """Every entry below `top` but each year's work/ (the run lock, the
    kept checklist verdicts): its kind, permission bits and bytes (a
    link: its text; a lock without its closed_at line). `filed` False:
    2024/filed/ left out too (the backup)."""
    seen = {}
    for p in sorted(top.rglob("*")):
        rel = p.relative_to(top).as_posix()
        parts = rel.split("/")
        if len(parts) > 1 and parts[1] == "work":
            continue
        if not filed and parts[:2] == [YEAR, "filed"]:
            continue
        mode = stat.S_IMODE(p.lstat().st_mode)
        if p.is_symlink():
            seen[rel] = ("link", mode, os.readlink(p))
        elif p.is_dir():
            seen[rel] = ("dir", mode, None)
        else:
            data = p.read_bytes()
            if rel.endswith(f"/filed/{parts[0]}.json"):
                data = _no_stamp(data)
            seen[rel] = ("file", mode, data)
    return seen


def _no_stamp(lock: bytes) -> bytes:
    return b"\n".join(ln for ln in lock.split(b"\n") if b'"closed_at"' not in ln)


def _changed(now: dict, was: dict) -> list:
    """The entries of two `_tree`s that differ, each named."""
    return sorted(k for k in set(now) | set(was) if now.get(k) != was.get(k))


def _backup(year_dir: Path) -> Path:
    """The one backup folder of the year's reopening."""
    got = sorted((year_dir / "filed").glob(f"{year_dir.name}.unclosed-*"))
    assert len(got) == 1, got
    return got[0]


def _in_unclose(frame=None) -> bool:
    f = frame or sys._getframe(1)
    while f is not None and f.f_code is not cli_unclose.cmd_unclose_year.__code__:
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


# The calls, each (name, object, attribute, keywords). Before the record
# is written nothing may be changed: the plan, the reports moved, the
# copy of taxjson.toml and its rewrite, the copies moved, the lock moved,
# the record itself.
BEFORE = (
    ("plan", cli_unclose, "_plan", {}),
    ("after the plan", cli_unclose, "_plan", {"after": True}),
    ("backup folder", Path, "mkdir", {"caller": "folder"}),
    ("reports moved", os, "rename", {"caller": "move"}),
    ("after the reports", os, "rename", {"caller": "move", "after": True}),
    ("toml copy", shutil, "copy2", {"caller": "_apply"}),
    ("toml rewrite", cli_unclose, "_write_atomic", {"caller": "_apply"}),
    ("after the rewrite", cli_unclose, "_write_atomic",
     {"caller": "_apply", "after": True}),
    ("third copy moved", os, "rename", {"caller": "move", "nth": 4}),
    ("lock moved", os, "rename", {"caller": "move", "nth": 13}),
    ("after the lock", os, "rename", {"caller": "move", "nth": 13, "after": True}),
    ("record built", cli_unclose, "_record", {}),
    ("record", cli_unclose, "_write_atomic", {"caller": "_apply", "nth": 2}),
)
# Once the record is written: everything is, and the year is reopened.
AFTER = (
    ("after the record", cli_unclose, "_write_atomic",
     {"caller": "_apply", "nth": 2, "after": True}),
    ("report", cli_unclose, "_report", {}),
    ("rows", cli_unclose, "_copies_row", {}),
)
# The points with something moved before them.
MOVED_BEFORE = BEFORE[BEFORE.index(next(p for p in BEFORE
                                        if p[0] == "after the reports")):]
# The calls that write or remove what the unclose puts back.
WRITES = ((Path, "unlink"), (Path, "rmdir"), (Path, "mkdir"), (os, "rename"),
          (shutil, "rmtree"), (shutil, "copy2"), (cli_unclose, "_write_atomic"))


@contextlib.contextmanager
def _ctrl_c_again(first: _Inject):
    """Once `first` has fired, a real Ctrl-C before every call in WRITES
    the unclose makes; yields the list of those sent."""
    sent = []

    def wrap(real):
        def again(*a, **kw):
            if first.fired and _in_unclose():
                sent.append(1)
                _ctrl_c()
            return real(*a, **kw)
        return again
    with contextlib.ExitStack() as st:
        for obj, attr in WRITES:
            st.enter_context(mock.patch.object(obj, attr, wrap(getattr(obj, attr))))
        yield sent


class _Projects(unittest.TestCase):
    """`base`: the demo, 2024 run; `closed`: the same, 2024 closed;
    `two`: closed, with 2025 started from it and run."""

    @classmethod
    def setUpClass(cls):
        # Python's own Ctrl-C handler, as at the top of `tjs`.
        cls._sigint = signal.signal(signal.SIGINT, signal.default_int_handler)
        cls._td = tempfile.TemporaryDirectory()
        tmp = Path(cls._td.name)
        cls.base = tmp / "base"
        r = _cli(tmp, "init", "--demo", str(cls.base))
        assert r.returncode == 0, r.stdout + r.stderr
        r = _cli(cls.base / YEAR, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.base_tree = _tree(cls.base)
        cls.closed = tmp / "closed"
        shutil.copytree(cls.base, cls.closed, symlinks=True)
        code, out, err, escaped = _tjs(cls.closed / YEAR, "close-year", "--yes")
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

    def assert_reopened(self, top: Path, res, *, as_base: bool = True) -> Path:
        """The year is open, what was filed is in the backup, and (with
        `as_base`) every other file is as before the close."""
        code, out, err, escaped = res
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"REOPENED {YEAR} — it is not filed any more", out)
        y = top / YEAR
        b = _backup(y)
        self.assertEqual(sorted(p.name for p in (y / "filed").iterdir()), [b.name])
        self.assertEqual(_no_stamp((b / f"{YEAR}.json").read_bytes()),
                         self.closed_tree[f"{YEAR}/filed/{YEAR}.json"][2])
        kept = {p.name: p.read_bytes() for p in (b / "reports").iterdir()}
        self.assertEqual(kept, {k.rsplit("/", 1)[1]: v[2]
                                for k, v in self.closed_tree.items()
                                if k.startswith(f"{YEAR}/filed/{YEAR}/reports/")})
        self.assertFalse((b / ".removed").exists())
        self.assertEqual(json.loads((b / "unclosed.json").read_text())["year"],
                         int(YEAR))
        self.assertNotIn(NOTE, (y / "taxjson.toml").read_text())
        # No reader takes the backup for a lock.
        self.assertEqual(filed_locks.list_snapshots(y), [])
        if as_base:
            self.assertEqual(_changed(_tree(top, filed=False),
                                      _tree(self.base, filed=False)), [])
        return b

    def assert_nothing_changed(self, top: Path, before: dict, res, kind: str):
        code, out, err, escaped = res
        if kind == "KeyboardInterrupt":
            self.assertEqual(code, 130, err)
            self.assertIn("unclose-year: error: interrupted", err)
        elif kind == "RuntimeError":
            # A bug: one Error, then its traceback.
            self.assertIsInstance(escaped, RuntimeError, (code, err))
            self.assertIn("tjs unclose-year: error: stopped before the year was "
                          "reopened: nothing was changed", err)
        elif isinstance(code, str):     # a refusal: its own words, exit 1
            self.assertIsNone(escaped, f"a traceback: {escaped!r}\n{err}")
            self.assertTrue(code.startswith("tjs unclose-year: error: "), code)
            self.assertIn("Nothing was changed.", code)
        else:                           # what cannot be read or written: exit 2
            self.assertIsNone(escaped, f"a traceback: {escaped!r}\n{err}")
            self.assertEqual(code, 2, err)
            self.assertIn("tjs unclose-year: error: ", err)
            self.assertIn("Nothing was changed", err)
        self.assertNotIn("REOPENED", out)
        self.assertNotIn("is reopened", err)
        self.assertEqual(_changed(_tree(top), before), [])


class UncloseYearRoundTrip(_Projects):

    def test_close_then_unclose_leaves_every_file_as_before_the_close(self):
        top = self._copy(self.closed)
        res = _unclose(top / YEAR)
        b = self.assert_reopened(top, res)
        out = res[1]
        self.assertEqual((b / "taxjson.toml").read_bytes(),
                         self.closed_tree[f"{YEAR}/taxjson.toml"][2])
        self.assertIn(f"Lock:              filed/{YEAR}.json -> filed/{b.name}/"
                      f"{YEAR}.json", out)
        self.assertIn('inputs_dir = "../inputs", tobase_map = "../tobase.map" set '
                      'again', out)
        self.assertIn("11 the same as the shared file(s): removed", out)
        self.assertIn(f"! {YEAR}'s filed figures are no longer recorded outside the "
                      f"backup — tjs years", out)
        self.assertNotIn("other inputs", out)
        self.assertEqual(res[2], "")
        self.assertFalse((top / YEAR / "work" / lib_checklist.RESULTS_FILE).exists())
        rec = json.loads((b / "unclosed.json").read_text())
        self.assertEqual(rec["settings_restored"],
                         {"inputs_dir": "../inputs", "tobase_map": "../tobase.map"})
        self.assertEqual(len(rec["copies_removed"]), 11)
        self.assertEqual(rec["copies_kept"],
                         {"differs": [], "only-copy": [], "unreadable": [],
                          "generated": []})
        # The books are the run's still: no input changed.
        code, out, err, escaped = _tjs(top / YEAR, "sum")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertNotIn("Run `tjs run` first", err)

    def test_close_unclose_close_gives_the_first_lock(self):
        top = self._copy(self.closed)
        self.assert_reopened(top, _unclose(top / YEAR))
        code, out, err, escaped = _tjs(top / YEAR, "close-year", "--yes")
        self.assertEqual((code, escaped), (0, None), err)
        now = _tree(top)
        backup = f"{YEAR}/filed/{_backup(top / YEAR).name}"
        self.assertEqual([k for k in _changed(now, self.closed_tree)
                          if not k.startswith(backup)], [])
        # And reopened again: a second backup, the first kept.
        res = _unclose(top / YEAR)
        self.assertEqual(res[0], 0, res[2])
        self.assertEqual(len(sorted((top / YEAR / "filed").glob("*.unclosed-*"))), 2)
        self.assertEqual(_changed(_tree(top, filed=False),
                                  _tree(self.base, filed=False)), [])

    def test_dry_run_changes_nothing(self):
        top = self._copy(self.closed)
        code, out, err, escaped = _unclose(top / YEAR, "--dry-run")
        self.assertEqual((code, escaped, err), (0, None, ""))
        self.assertIn(f"DRY RUN — reopening {YEAR} would change (nothing was "
                      f"changed):", out)
        # The folder's name carries the time the run starts: the pattern.
        self.assertIn(f"filed/{YEAR}.json -> filed/{YEAR}.unclosed-<date>-<time>/"
                      f"{YEAR}.json", out)
        self.assertNotRegex(out, r"unclosed-\d")
        self.assertIn("11 the same as the shared file(s): removed", out)
        self.assertIn("are then no longer recorded outside the backup", out)
        self.assertEqual(_changed(_tree(top), self.closed_tree), [])

    def test_details_says_each_point_in_full(self):
        top = self._copy(self.closed)
        code, out, err, escaped = _unclose(top / YEAR, "--details")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("unclosed.json lists every file", out)
        self.assertIn("`tjs close-year` locks the books as they are then", out)

    def test_this_years_commands_say_it_is_not_filed(self):
        top = self._copy(self.closed)
        y = top / YEAR
        code, out, err, escaped = _tjs(y, "checklist", "--run", "--only", "filed-lock")
        self.assertIn("locked", out)
        self.assert_reopened(top, _unclose(y))
        code, out, err, escaped = _tjs(y, "years")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertRegex(out, rf"(?m)^{YEAR}  open ")
        self.assertNotIn("filed", out.lower().replace("not filed", ""))
        code, out, err, escaped = _tjs(y, "check-filed")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("No filed/<year>.json snapshots", out)
        code, out, err, escaped = _tjs(y, "carryover")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertNotIn("close-year lock", out + err)
        # The verdict kept while it was closed is dropped, not shown.
        code, out, err, escaped = _tjs(y, "checklist")     # (exit 1: steps to do)
        self.assertIsNone(escaped, err)
        self.assertIn("Nothing checked yet", out)
        self.assertNotIn("locked; no drift", out)
        code, out, err, escaped = _tjs(y, "checklist", "--run", "--only", "filed-lock")
        self.assertIsNone(escaped, err)
        self.assertIn("no filed/<year>.json — `tjs close-year` after filing", out)
        for text in (out, err):
            self.assertNotIn("Traceback", text)


class UncloseYearDivergedInputs(_Projects):

    def test_a_copy_that_differs_or_is_the_only_one_is_kept(self):
        top = self._copy(self.closed)
        y, shared = top / YEAR, top / "inputs"
        # The shared export gained a row (the normal case).
        grown = shared / "ib" / "ib_demo.csv"
        grown.write_bytes(grown.read_bytes() + b"# a later download's row\n")
        # The frozen copy was edited (an amendment not filed).
        edited = y / "inputs" / "webull" / "webull_demo.csv"
        edited.write_bytes(edited.read_bytes() + b"# corrected in the copy\n")
        edited_bytes = edited.read_bytes()
        # A file added to the frozen copy only; a shared file gone since.
        only = y / "inputs" / "margin" / "added_demo.csv"
        only.write_bytes(b"synthetic,added,after,the,close\n")
        gone = shared / "tfsa" / "rbc_direct_demo.csv"
        gone_bytes = gone.read_bytes()
        gone.unlink()
        # A new export in the shared folder; the shared tobase.map edited.
        (shared / "questrade" / "questrade_2025_demo.csv").write_bytes(
            b"synthetic,new,export\n")
        tob = top / "tobase.map"
        frozen_tob = (y / "tobase.map").read_bytes()
        tob.write_bytes(tob.read_bytes() + b"# a line added since\n")
        shared_before = {p.relative_to(top).as_posix(): p.read_bytes()
                         for p in sorted(shared.rglob("*")) if p.is_file()}
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        b = _backup(y)
        self.assertEqual((b / "inputs" / "webull" / "webull_demo.csv").read_bytes(),
                         edited_bytes)
        self.assertEqual((b / "inputs" / "tfsa" / "rbc_direct_demo.csv").read_bytes(),
                         gone_bytes)
        self.assertTrue((b / "inputs" / "ib" / "ib_demo.csv").is_file())
        self.assertEqual((b / "inputs" / "margin" / "added_demo.csv").read_bytes(),
                         b"synthetic,added,after,the,close\n")
        self.assertEqual((b / "tobase.map").read_bytes(), frozen_tob)
        rec = json.loads((b / "unclosed.json").read_text())
        self.assertEqual(rec["copies_kept"], {
            "differs": ["inputs/ib/ib_demo.csv", "inputs/webull/webull_demo.csv",
                        "tobase.map"],
            "only-copy": ["inputs/margin/added_demo.csv",
                          "inputs/tfsa/rbc_direct_demo.csv"],
            "unreadable": [], "generated": []})
        self.assertEqual(len(rec["copies_removed"]), 7)
        self.assertEqual(rec["shared_files_not_read_when_filed"], 1)
        # The year's own copy is gone from the project, the shared files
        # are as they were, and taxjson.toml reads them.
        self.assertFalse((y / "inputs").exists())
        self.assertFalse((y / "tobase.map").exists())
        self.assertEqual({p.relative_to(top).as_posix(): p.read_bytes()
                          for p in sorted(shared.rglob("*")) if p.is_file()},
                         shared_before)
        self.assertEqual((y / "taxjson.toml").read_bytes(),
                         self.base_tree[f"{YEAR}/taxjson.toml"][2])
        self.assertIn("7 the same as the shared file(s): removed", out)
        self.assertIn("3 not the same as the shared file (inputs/ib/ib_demo.csv, "
                      "inputs/webull/webull_demo.csv, tobase.map): kept in the backup",
                      out)
        self.assertIn("2 not in ../inputs (inputs/margin/added_demo.csv, "
                      "inputs/tfsa/rbc_direct_demo.csv): kept in the backup", out)
        self.assertIn(f"! {YEAR} now reads other inputs than it was filed from "
                      f"(5 kept, 1 new): figures may differ — tjs run", out)
        for ln in out.splitlines():
            if ln.startswith("! "):
                self.assertLessEqual(len(ln), 100, ln)
        # The books are older than the inputs now, and the views say so.
        for cmd in (("sum",), ("t1135",), ("close-year", "--yes")):
            code, out, err, escaped = _tjs(y, *cmd)
            self.assertIsNone(escaped, (cmd, err))
            self.assertIsInstance(code, str, (cmd, out, err))     # a refusal, exit 1
            # (sum, t1135: "the books in work/ are older than the inputs";
            # close-year: "inputs changed since the last full run")
            self.assertIn("(changed: ../tobase.map (shared by every year), "
                          "inputs/ib/ib_demo.csv; added: "
                          "inputs/questrade/questrade_2025_demo.csv; removed: "
                          "inputs/tfsa/rbc_direct_demo.csv)", code, cmd)
            self.assertIn("`tjs run`", code, cmd)
        self.assertFalse((y / "filed" / f"{YEAR}.json").exists())

    def test_details_names_every_kept_file(self):
        top = self._copy(self.closed)
        y = top / YEAR
        for a in ("ib", "margin", "questrade", "webull"):
            for p in (y / "inputs" / a).glob("*.csv"):
                p.write_bytes(p.read_bytes() + b"# edited\n")
        code, out, err, escaped = _unclose(y, "--dry-run")
        self.assertIn("4 not the same as the shared file (inputs/ib/ib_demo.csv, "
                      "inputs/margin/ib_scenarios_demo.csv +2 more)", out)
        code, out, err, escaped = _unclose(y, "--details")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("inputs/webull/webull_demo.csv): kept in the backup", out)
        self.assertIn("4 file(s) of the frozen copy are kept in filed/", out)
        # A file that differs is compared, never copied over the shared one.
        self.assertIn("4 kept file(s) differ from the file of that name in ../inputs/ "
                      "(or it cannot be read): compare the two; the one in ../inputs/ "
                      "is what every year reads", out)
        self.assertNotIn("goes into", out)

    def test_details_says_where_a_file_only_in_the_copy_goes(self):
        top = self._copy(self.closed)
        y = top / YEAR
        (y / "inputs" / "margin" / "added_demo.csv").write_bytes(b"synthetic,added\n")
        code, out, err, escaped = _unclose(y, "--details")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("1 kept file(s) are not in ../inputs/: one that belongs to the "
                      "books goes into ../inputs/<account>/.", out)
        self.assertNotIn("differ from the file of that name", out)

    def test_a_shared_file_that_cannot_be_read_is_said_so(self):
        top = self._copy(self.closed)
        y = top / YEAR
        shared = top / "inputs" / "ib" / "ib_demo.csv"
        shared.chmod(0)
        self.addCleanup(shared.chmod, 0o600)
        code, out, err, escaped = _unclose(y, "--dry-run")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("1 whose shared file cannot be read (inputs/ib/ib_demo.csv): kept "
                      "in the backup", out)
        self.assertNotIn("not the same as the shared file", out)
        self.assertIn("(1 kept)", out)

    def test_a_copy_that_is_a_link_is_left_where_it_is(self):
        # Moved into the backup a relative link would lead nowhere.
        top = self._copy(self.closed)
        y = top / YEAR
        (y / "elsewhere.csv").write_bytes(b"synthetic,linked\n")
        link = y / "inputs" / "margin" / "linked_demo.csv"
        os.symlink("../../elsewhere.csv", link)
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.read_bytes(), b"synthetic,linked\n")
        self.assertIn("1 left where they are: a link, or reached through one "
                      "(inputs/margin/linked_demo.csv)", out)
        rec = json.loads((_backup(y) / "unclosed.json").read_text())
        self.assertEqual(rec["links_left"], ["inputs/margin/linked_demo.csv"])
        self.assertEqual(sorted(p.name for p in (y / "inputs").rglob("*")),
                         ["linked_demo.csv", "margin"])

    def test_a_tobase_map_that_is_a_link_to_another_file_is_refused(self):
        top = self._copy(self.closed)
        y = top / YEAR
        (y / "mine.map").write_bytes((y / "tobase.map").read_bytes())
        (y / "tobase.map").unlink()
        os.symlink("mine.map", y / "tobase.map")
        before = _tree(top)
        res = _unclose(y)
        self.assertIn("tobase.map is a link to mine.map, not the copy close-year froze",
                      str(res[0]))
        self.assert_nothing_changed(top, before, res, "refusal")

    def test_the_shared_folder_gone_is_a_refusal(self):
        for what, says in (("inputs", "../inputs/, the folder 2024 read its exports "
                                      "from, is gone"),
                           ("tobase.map", "../tobase.map, the tobase.map 2024 read "
                                          "before it was closed, is gone")):
            with self.subTest(gone=what):
                top = self._copy(self.closed)
                if what == "inputs":
                    shutil.rmtree(top / what)
                else:
                    (top / what).unlink()
                before = _tree(top)
                res = _unclose(top / YEAR)
                self.assertIn(says, str(res[0]))
                self.assertIn("is the only copy", str(res[0]))
                self.assert_nothing_changed(top, before, res, "refusal")

    def test_a_copy_that_is_a_link_to_the_shared_file_is_left(self):
        top = self._copy(self.closed)
        y = top / YEAR
        (y / "tobase.map").unlink()
        os.symlink("../tobase.map", y / "tobase.map")
        shared = (top / "tobase.map").read_bytes()
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertEqual((top / "tobase.map").read_bytes(), shared)
        self.assertTrue((y / "tobase.map").is_symlink())
        self.assertIn("1 left where they are: a link, or reached through one "
                      "(tobase.map)", out)


class UncloseYearLaterYears(_Projects):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.two = Path(cls._td.name) / "two"
        shutil.copytree(cls.closed, cls.two, symlinks=True)
        r = _cli(cls.two / YEAR, "new-year", NEXT)
        assert r.returncode == 0, r.stdout + r.stderr
        r = _cli(cls.two / NEXT, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]

    def test_a_later_year_started_is_named_and_says_the_truth(self):
        top = self._copy(self.two)
        y, nxt = top / YEAR, top / NEXT
        code, out, err, escaped = _tjs(nxt, "checklist", "--run", "--only", "handoff")
        self.assertIn(f"{YEAR} carried forward once", out)
        nxt_before = {k: v for k, v in _tree(top).items() if k.startswith(NEXT)}
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"! {NEXT} carries in from {YEAR}, which has no lock now: its "
                      f"carry-in reads none — tjs -C ", out)
        self.assertEqual(json.loads((_backup(y) / "unclosed.json").read_text())
                         ["later_years_started"], [int(NEXT)])
        # Nothing of the next year but its kept verdicts is touched.
        self.assertEqual({k: v for k, v in _tree(top).items() if k.startswith(NEXT)},
                         nxt_before)
        said = {}
        for cmd in (("carryover",), ("years",), ("check-filed",), ("checklist",),
                    ("estimate",), ("checklist", "--run", "--only", "handoff")):
            code, out, err, escaped = _tjs(nxt, *cmd)
            self.assertIsNone(escaped, (cmd, err))
            self.assertNotIn("Traceback", out + err, cmd)
            said[" ".join(cmd)] = (code, out, err)
        code, out, err = said["carryover"]
        self.assertEqual(code, 0, err)
        self.assertIn(f"! Rows before {NEXT} are rebuilt, maybe partial", out)
        code, out, err = said["years"]
        self.assertEqual(code, 0, err)
        self.assertRegex(out, rf"(?m)^{YEAR}  open ")
        self.assertRegex(out, rf"(?m)^{NEXT}  open ")
        self.assertNotIn("frozen", out)
        code, out, err = said["check-filed"]
        self.assertEqual(code, 0, err)
        self.assertIn("No filed/<year>.json snapshots", out)
        code, out, err = said["checklist"]                  # (exit 1: steps to do)
        self.assertIn("Nothing checked yet", out)
        self.assertNotIn("carried forward once", out)
        code, out, err = said["estimate"]
        self.assertEqual(code, 0, err)
        self.assertIn(f"no close-year lock before {NEXT}", out)
        code, out, err = said["checklist --run --only handoff"]
        self.assertIn("[b]", out)
        self.assertIn("Run `tjs close-year` in the previous year's project", out)

    def test_a_later_year_closed_is_a_refusal(self):
        top = self._copy(self.two)
        code, out, err, escaped = _tjs(top / NEXT, "close-year", "--yes", "--force")
        self.assertEqual((code, escaped), (0, None), err)
        before = _tree(top)
        res = _unclose(top / YEAR)
        self.assertIn(f"tjs unclose-year: error: {NEXT} is closed too "
                      f"(../{NEXT}/filed/{NEXT}.json), on what {YEAR} carried into it",
                      str(res[0]))
        self.assertIn("Reopen the newest year first (`tjs -C ", str(res[0]))
        self.assert_nothing_changed(top, before, res, "refusal")
        res = _unclose(top / YEAR, "--dry-run")
        self.assertIn(f"{NEXT} is closed too", str(res[0]))
        # The newest first, then this one.
        code, out, err, escaped = _unclose(top / NEXT)
        self.assertEqual((code, escaped), (0, None), err)
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"! {NEXT} carries in from {YEAR}", out)

    def test_a_later_year_reached_through_a_link_is_seen(self):
        # `2025 -> y2025`: no year folder by its name, a link by the other.
        top = self._copy(self.two)
        (top / NEXT).rename(top / f"y{NEXT}")
        os.symlink(f"y{NEXT}", top / NEXT)
        code, out, err, escaped = _unclose(top / YEAR, "--dry-run")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertEqual(out.count(f"! {NEXT} carries in from {YEAR}"), 1, out)
        self.assertIn("— tjs -C ", out)
        (top / f"y{NEXT}" / "filed").mkdir()
        (top / f"y{NEXT}" / "filed" / f"{NEXT}.json").write_text("{}\n")
        before = _tree(top)
        res = _unclose(top / YEAR)
        self.assertIn(f"{NEXT} is closed too (../{NEXT}/filed/{NEXT}.json)", str(res[0]))
        self.assert_nothing_changed(top, before, res, "refusal")

    def test_a_later_year_in_a_folder_of_another_name_is_seen_by_its_record(self):
        # Not ../YYYY, and its `year` not read: its prior_year_record
        # names this lock.
        top = self._copy(self.two)
        other = top / "next-year"
        (top / NEXT).rename(other)
        cfg = other / "taxjson.toml"
        self.assertIn(f'"../{YEAR}/filed/{YEAR}.json"', cfg.read_text())
        code, out, err, escaped = _unclose(top / YEAR, "--dry-run")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"! {NEXT} carries in from {YEAR}", out)
        (other / "filed").mkdir()
        (other / "filed" / f"{NEXT}.json").write_text("{}\n")
        before = _tree(top)
        res = _unclose(top / YEAR)
        self.assertIn(f"{NEXT} is closed too (../next-year/filed/{NEXT}.json)",
                      str(res[0]))
        self.assert_nothing_changed(top, before, res, "refusal")

    def test_a_later_lock_in_the_same_folder_is_a_refusal(self):
        top = self._copy(self.closed)
        (top / YEAR / "filed" / f"{NEXT}.json").write_text("{}\n")
        before = _tree(top)
        res = _unclose(top / YEAR)
        self.assertIn(f"{NEXT} is closed too (filed/{NEXT}.json)", str(res[0]))
        self.assert_nothing_changed(top, before, res, "refusal")


class UncloseYearOtherStates(_Projects):

    def test_a_year_never_closed_is_one_line(self):
        top = self._copy(self.base)
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertEqual(code, f"tjs unclose-year: error: {YEAR} is not closed (no "
                               f"filed/{YEAR}.json): there is nothing to reopen")
        self.assertEqual(_changed(_tree(top), self.base_tree), [])
        self.assertFalse((top / YEAR / "filed").exists())

    def test_another_year_than_the_projects_is_refused(self):
        top = self._copy(self.closed)
        res = _unclose(top / YEAR, "--year", "2023")
        self.assertIn("this project's tax year is 2024, not 2023", str(res[0]))
        self.assert_nothing_changed(top, self.closed_tree, res, "refusal")

    def test_a_lock_of_an_older_release_with_no_frozen_record(self):
        # The freeze is read from taxjson.toml's note, not the lock.
        top = self._copy(self.closed)
        lock = top / YEAR / "filed" / f"{YEAR}.json"
        doc = json.loads(lock.read_text())
        del doc["frozen"]
        lock.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertEqual(_changed(_tree(top, filed=False),
                                  _tree(self.base, filed=False)), [])
        self.assertIn("11 the same as the shared file(s): removed", out)

    def test_a_lock_that_froze_nothing_only_the_lock_and_reports_go(self):
        # An older release's lock on a year that reads the shared
        # exports, or a lock taken before the year ended (`partial`).
        for partial in (False, True):
            with self.subTest(partial=partial):
                top = self._copy(self.base)
                y = top / YEAR
                shutil.copytree(self.closed / YEAR / "filed", y / "filed")
                lock = y / "filed" / f"{YEAR}.json"
                doc = json.loads(lock.read_text())
                if partial:
                    doc["frozen"] = {"partial": True, "inputs_dir": None,
                                     "tobase_map": None, "copied": 0,
                                     "inputs": doc["frozen"]["inputs"]}
                else:
                    del doc["frozen"]
                lock.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
                code, out, err, escaped = _unclose(y)
                self.assertEqual((code, escaped), (0, None), err)
                self.assertEqual(_changed(_tree(top, filed=False),
                                          _tree(self.base, filed=False)), [])
                self.assertNotIn("Settings:", out)
                self.assertNotIn("Frozen copies:", out)
                self.assertFalse((_backup(y) / "taxjson.toml").exists())

    def test_the_note_deleted_by_hand_the_setting_comes_from_the_lock(self):
        top = self._copy(self.closed)
        cfg = top / YEAR / "taxjson.toml"
        cfg.write_text("\n".join(
            ln for ln in cfg.read_text().split("\n")
            if NOTE not in ln and not ln.startswith(("# inputs_dir", "# tobase_map"))))
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn('inputs_dir = "../inputs" (from the lock\'s record: its line '
                      'under the note was gone)', out)
        from taxjson.lib import project_layout as PL
        st = PL.parse_config_text(cfg.read_text())["settings"]
        self.assertEqual((st["inputs_dir"], st["tobase_map"]),
                         ("../inputs", "../tobase.map"))
        self.assertFalse((top / YEAR / "inputs").exists())
        self.assertFalse((top / YEAR / "tobase.map").exists())

    def test_a_setting_named_again_by_hand_is_left(self):
        # The manual way of older docs, half done: inputs_dir set again
        # under the note, the copies and the lock still there.
        top = self._copy(self.closed)
        cfg = top / YEAR / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            '# inputs_dir                        = "../inputs"',
            '# inputs_dir                        = "../inputs"\n'
            'inputs_dir = "../inputs"'))
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("Set by hand:       inputs_dir already named in taxjson.toml: "
                      "left as it is", out)
        text = cfg.read_text()
        self.assertNotIn(NOTE, text)
        # (the commented line was not restored: it stays, as every line
        # but the note does)
        self.assertIn('\n# inputs_dir                        = "../inputs"\n'
                      'inputs_dir = "../inputs"\n', text)
        self.assertIn('\ntobase_map                        = "../tobase.map"\n', text)
        self.assertFalse((top / YEAR / "inputs").exists())

    def _named_again(self, top: Path, *keys: str) -> Path:
        """`keys` set again by hand under their commented lines."""
        cfg = top / YEAR / "taxjson.toml"
        text = cfg.read_text()
        for k in keys:
            line = next(ln for ln in text.split("\n") if ln.startswith(f"# {k} "))
            text = text.replace(line, f"{line}\n{line[2:]}")
        cfg.write_text(text)
        return cfg

    def test_tobase_map_named_again_by_hand_is_left_and_its_copy_goes(self):
        # Every other command refuses this state ("two tobase.map files
        # for this year"): this one takes it back.
        for keys in (("tobase_map",), ("inputs_dir", "tobase_map")):
            with self.subTest(keys=keys):
                top = self._copy(self.closed)
                y = top / YEAR
                cfg = self._named_again(top, *keys)
                res = _tjs(y, "sum")
                self.assertIn("two tobase.map files for this year", f"{res[0]}{res[2]}")
                code, out, err, escaped = _unclose(y)
                self.assertEqual((code, escaped), (0, None), err)
                self.assertIn(f"Set by hand:       {', '.join(keys)} already named in "
                              f"taxjson.toml: left as it is", out)
                self.assertIn("11 the same as the shared file(s): removed", out)
                text = cfg.read_text()
                self.assertNotIn(NOTE, text)
                from taxjson.lib import project_layout as PL
                st = PL.parse_config_text(text)["settings"]
                self.assertEqual((st["inputs_dir"], st["tobase_map"]),
                                 ("../inputs", "../tobase.map"))
                self.assertFalse((y / "tobase.map").exists())
                self.assertFalse((y / "inputs").exists())
                code, out, err, escaped = _tjs(y, "years")
                self.assertEqual((code, escaped), (0, None), err)

    def test_tobase_map_named_by_hand_with_a_copy_that_differs(self):
        top = self._copy(self.closed)
        y = top / YEAR
        self._named_again(top, "tobase_map")
        (y / "tobase.map").write_bytes((y / "tobase.map").read_bytes() + b"# mine\n")
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("1 not the same as the shared file (tobase.map): kept in the "
                      "backup", out)
        self.assertTrue((_backup(y) / "tobase.map").read_bytes().endswith(b"# mine\n"))

    def test_two_tobase_maps_with_no_record_of_a_freeze_is_refused(self):
        # No `frozen` record: nothing says the year's tobase.map is the
        # close's copy, so it is not taken away, and the two stay refused.
        top = self._copy(self.closed)
        y = top / YEAR
        self._named_again(top, "tobase_map")
        lock = y / "filed" / f"{YEAR}.json"
        doc = json.loads(lock.read_text())
        del doc["frozen"]
        lock.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        before = _tree(top)
        res = _unclose(y)
        self.assertIn("tjs unclose-year: error: two tobase.map files for this year",
                      str(res[0]))
        self.assert_nothing_changed(top, before, res, "refusal")

    def test_another_config_error_is_still_refused(self):
        # The one refusal left to this command hides no other.
        top = self._copy(self.closed)
        cfg = self._named_again(top, "tobase_map")
        cfg.write_text(cfg.read_text().replace(
            'tax_date                          = "settle"',
            'tax_date                          = "sometime"'))
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertIsNone(escaped, err)
        self.assertNotEqual(code, 0)
        self.assertIn("tax_date", f"{code}{err}")
        self.assertEqual(_changed(_tree(top, filed=True),
                                  {**self.closed_tree,
                                   f"{YEAR}/taxjson.toml": _tree(top)[f"{YEAR}/taxjson.toml"]}),
                         [])

    def test_a_run_in_progress_is_a_refusal(self):
        import fcntl
        top = self._copy(self.closed)
        before = _tree(top)
        with open(top / YEAR / "work" / ".run.lock", "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            res = _unclose(top / YEAR)
        self.assertIn("is in progress in this project", str(res[0]))
        self.assertIn("Nothing was written.", str(res[0]))
        self.assertEqual(_changed(_tree(top), before), [])
        self.assert_reopened(top, _unclose(top / YEAR))

    def test_the_copies_deleted_by_hand(self):
        top = self._copy(self.closed)
        shutil.rmtree(top / YEAR / "inputs")
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("1 the same as the shared file(s): removed", out)    # tobase.map
        self.assertNotIn("other inputs", out)
        self.assertEqual(_changed(_tree(top, filed=False),
                                  _tree(self.base, filed=False)), [])

    def test_the_lock_deleted_by_hand_the_freeze_is_taken_back(self):
        # `years` says "lock missing: restore it or run close-year again";
        # the third way out is to reopen the year.
        top = self._copy(self.closed)
        y = top / YEAR
        (y / "filed" / f"{YEAR}.json").unlink()
        code, out, err, escaped = _tjs(y, "years")
        self.assertIn("lock missing", out)
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"Lock:              filed/{YEAR}.json was gone already: the "
                      f"filed figures are not in the backup", out)
        self.assertIn(f"! {YEAR}'s lock was gone already: its filed figures are "
                      f"recorded nowhere here — tjs years", out)
        self.assertNotIn("outside the backup", out)
        self.assertTrue((_backup(y) / "reports").is_dir())
        self.assertEqual(_changed(_tree(top, filed=False),
                                  _tree(self.base, filed=False)), [])
        code, out, err, escaped = _tjs(y, "years")
        self.assertNotIn("lock missing", out)
        self.assertRegex(out, rf"(?m)^{YEAR}  open ")

    def test_a_lock_that_cannot_be_read_is_kept_as_it_is(self):
        top = self._copy(self.closed)
        y = top / YEAR
        (y / "filed" / f"{YEAR}.json").write_text("{ not json\n")
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("(it cannot be read: kept as it is)", out)
        self.assertEqual((_backup(y) / f"{YEAR}.json").read_text(), "{ not json\n")
        self.assertEqual(_changed(_tree(top, filed=False),
                                  _tree(self.base, filed=False)), [])

    def test_an_edit_of_taxjson_toml_since_the_close_is_kept(self):
        top = self._copy(self.closed)
        cfg = top / YEAR / "taxjson.toml"
        edit = "\n## my own note, added after the close\n[estimate]\nother_income = 100\n"
        was = cfg.read_text()
        self.assertNotIn("\n[estimate]\n", was)
        cfg.write_text(was + edit)
        cfg.chmod(0o640)
        code, out, err, escaped = _unclose(top / YEAR)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertEqual(cfg.read_bytes(),
                         self.base_tree[f"{YEAR}/taxjson.toml"][2] + edit.encode())
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o640)
        self.assertEqual((_backup(top / YEAR) / "taxjson.toml").read_text(), was + edit)

    def test_a_read_only_taxjson_toml_is_refused(self):
        top = self._copy(self.closed)
        cfg = top / YEAR / "taxjson.toml"
        cfg.chmod(0o400)
        before = _tree(top)
        res = _unclose(top / YEAR)
        self.assertEqual(res[0], 2, res[2])
        self.assertIn("taxjson.toml", res[2])
        self.assert_nothing_changed(top, before, res, "OSError")

    def test_filed_that_cannot_be_written_is_refused(self):
        top = self._copy(self.closed)
        d = top / YEAR / "filed"
        d.chmod(0o500)
        self.addCleanup(d.chmod, 0o700)
        before = _tree(top)
        res = _unclose(top / YEAR)
        self.assertIn("the backup of what was filed cannot be written: filed/ cannot "
                      "be written (Permission denied)", res[2])
        self.assert_nothing_changed(top, before, res, "OSError")


LINE_IN = '# inputs_dir = "../inputs"'
LINE_TOB = '# tobase_map = "../tobase.map"'
NOTE_1 = ("## frozen by `tjs close-year`: this year reads its own copy of the "
          "exports it was filed from")
NOTE_2 = "## frozen by `tjs close-year` too"
BOTH = {"inputs_dir": "../inputs", "tobase_map": "../tobase.map"}


def _toml(*lines: str) -> str:
    return "\n".join(("[settings]", "year = 2024", *lines, "country = \"canada\"", ""))


class UncloseYearOnlyTheClosesLines(unittest.TestCase):
    """`_thawed`: under a note, only what the close wrote is restored
    (review of TJ-4974, P1): the lock's `frozen` record says which keys
    and values; a commented line of the person's own is never activated,
    changed or removed."""

    def thaw(self, text, record, live=()):
        new, back, left = cli_unclose._thawed(text, list(live), record)
        return new, back, [k for k, _ln in left]

    def test_the_three_forms_the_freeze_writes(self):
        both = _toml('inputs_dir = "../inputs"', 'tobase_map = "../tobase.map"')
        for name, text, record, want in (
                ("one note over two adjacent keys",
                 _toml(NOTE_1, LINE_IN, LINE_TOB), BOTH, both),
                ("a note each",
                 _toml(NOTE_1, LINE_IN, "## a comment", NOTE_2, LINE_TOB), BOTH,
                 _toml('inputs_dir = "../inputs"', "## a comment",
                       'tobase_map = "../tobase.map"')),
                ("only tobase.map frozen",
                 _toml(NOTE_1, LINE_TOB), {"inputs_dir": None,
                                           "tobase_map": "../tobase.map"},
                 _toml('tobase_map = "../tobase.map"'))):
            with self.subTest(form=name):
                new, back, left = self.thaw(text, record)
                self.assertEqual(new, want)
                self.assertEqual(back, {k: v for k, v in record.items() if v})
                self.assertEqual(left, [])

    def test_a_commented_line_of_the_persons_own_under_the_note_stays(self):
        # Repro A: the close froze inputs_dir only (the record says so);
        # the commented tobase_map under it was there before.
        text = _toml(NOTE_1, LINE_IN, LINE_TOB)
        new, back, left = self.thaw(text, {"inputs_dir": "../inputs",
                                           "tobase_map": None})
        self.assertEqual(new, _toml('inputs_dir = "../inputs"', LINE_TOB))
        self.assertEqual((back, left), ({"inputs_dir": "../inputs"}, ["tobase_map"]))

    def test_a_commented_alternative_is_not_taken_for_the_setting(self):
        # Repro B: `# tobase_map = "../old.map"` stood above the real line.
        old = '# tobase_map = "../old.map"'
        text = _toml(NOTE_1, LINE_IN, old, NOTE_2, LINE_TOB)
        new, back, left = self.thaw(text, BOTH)
        self.assertEqual(new, _toml('inputs_dir = "../inputs"', old,
                                    'tobase_map = "../tobase.map"'))
        self.assertEqual(back, BOTH)
        # The same with no second note (the value is not the record's).
        new, back, left = self.thaw(_toml(NOTE_1, LINE_IN, old), BOTH)
        self.assertEqual(new, _toml('inputs_dir = "../inputs"', old))
        self.assertEqual((back, left), ({"inputs_dir": "../inputs"}, ["tobase_map"]))
        # And for inputs_dir.
        alt = '# inputs_dir = "../elsewhere"'
        new, back, left = self.thaw(_toml(NOTE_1, alt), BOTH)
        self.assertEqual(new, _toml(alt))
        self.assertEqual((back, left), ({}, ["inputs_dir"]))

    def test_lines_the_freeze_never_writes_stay(self):
        for extra in ('#tobase_map = "../tobase.map"',           # no space
                      "# tobase_map is the shared one",           # prose
                      '# holdings_dir = "../holdings"',           # another key
                      '# inputs_dir = "../inputs"'):              # the same key twice
            with self.subTest(line=extra):
                text = _toml(NOTE_1, LINE_IN, extra)
                new, back, left = self.thaw(text, BOTH)
                self.assertEqual(new, _toml('inputs_dir = "../inputs"', extra))
                self.assertEqual(back, {"inputs_dir": "../inputs"})
        # A commented line after both keys is the person's.
        text = _toml(NOTE_1, LINE_IN, LINE_TOB, '# tobase_map = "../third.map"')
        new, back, left = self.thaw(text, BOTH)
        self.assertEqual(new, _toml('inputs_dir = "../inputs"',
                                    'tobase_map = "../tobase.map"',
                                    '# tobase_map = "../third.map"'))

    def test_with_no_record_only_the_line_directly_under_each_note(self):
        text = _toml(NOTE_1, LINE_IN, LINE_TOB)
        new, back, left = self.thaw(text, None)
        self.assertEqual(new, _toml('inputs_dir = "../inputs"', LINE_TOB))
        self.assertEqual(back, {"inputs_dir": "../inputs"})
        text = _toml(NOTE_1, LINE_IN, "", NOTE_2, LINE_TOB)
        new, back, left = self.thaw(text, None)
        self.assertEqual(new, _toml('inputs_dir = "../inputs"', "",
                                    'tobase_map = "../tobase.map"'))
        self.assertEqual(back, BOTH)

    def test_a_key_named_again_by_hand_keeps_its_commented_line(self):
        text = _toml(NOTE_1, LINE_IN, 'inputs_dir = "../inputs"')
        new, back, left = self.thaw(text, BOTH, live=["inputs_dir"])
        self.assertEqual(new, _toml(LINE_IN, 'inputs_dir = "../inputs"'))
        self.assertEqual((back, left), ({}, ["inputs_dir"]))

    def test_quoted_and_dotted_keys(self):
        from taxjson.lib import project_layout as PL
        for line in ('# "inputs_dir" = "../inputs"', "# 'inputs_dir' = '../inputs'",
                     '#   inputs_dir   =   "../inputs"  # as it was'):
            with self.subTest(line=line):
                new, back, left = self.thaw(_toml(NOTE_1, line), BOTH)
                self.assertEqual(new, _toml(line[2:]))
                self.assertEqual(back, {"inputs_dir": "../inputs"})
                self.assertEqual(PL.parse_config_text(new)["settings"]["inputs_dir"],
                                 "../inputs")
        dotted = "\n".join(("settings.year = 2024", NOTE_1,
                            '# settings.inputs_dir = "../inputs"', ""))
        new, back, left = self.thaw(dotted, BOTH)
        self.assertEqual(new, 'settings.year = 2024\nsettings.inputs_dir = "../inputs"\n')
        self.assertEqual(back, {"inputs_dir": "../inputs"})
        # Another value than the record's, in any spelling: not restored.
        for line in ('# "inputs_dir" = "../other"', '# settings.inputs_dir = "../other"'):
            new, back, left = self.thaw(_toml(NOTE_1, line), BOTH)
            self.assertEqual((new, back), (_toml(line), {}))


class UncloseYearOnlyTheClosesLinesEndToEnd(_Projects):

    def _closed_from(self, edit, run: bool = False) -> tuple:
        """A copy of the run project with taxjson.toml edited by `edit`
        (text -> text), closed: (its folder, the tree before the close)."""
        top = self._copy(self.base)
        cfg = top / YEAR / "taxjson.toml"
        cfg.write_text(edit(cfg.read_text(), top))
        if run:
            r = _cli(top / YEAR, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        before = _tree(top)
        code, out, err, escaped = _tjs(top / YEAR, "close-year", "--yes")
        self.assertEqual((code, escaped), (0, None), err)
        return top, before

    def test_a_year_with_a_tobase_map_of_its_own_keeps_it(self):
        # Repro A: inputs shared, the year's own tobase.map (the shared
        # one and a pair), `# tobase_map = ...` commented under inputs_dir.
        def edit(text, top):
            out = []
            for ln in text.split("\n"):
                if ln.startswith("tobase_map"):
                    continue
                out.append(ln)
                if ln.startswith("inputs_dir"):
                    out.append(LINE_TOB)
            (top / YEAR / "tobase.map").write_bytes(
                (top / "tobase.map").read_bytes() + b"# a pair of this year's own\n")
            return "\n".join(out)
        top, before = self._closed_from(edit, run=True)
        y = top / YEAR
        lock = json.loads((y / "filed" / f"{YEAR}.json").read_text())
        self.assertIsNone(lock["frozen"]["tobase_map"])
        self.assertIn(f"{NOTE_1}\n# inputs_dir                        = \"../inputs\"\n"
                      f"{LINE_TOB}\n", (y / "taxjson.toml").read_text())
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertEqual(_changed(_tree(top, filed=False),
                                  {k: v for k, v in before.items()}), [])
        self.assertIn('inputs_dir = "../inputs" set again', out)
        self.assertNotIn("tobase_map", out)
        self.assertNotIn("tobase.map", out)
        self.assertIn("10 the same as the shared file(s): removed", out)
        self.assertTrue((y / "tobase.map").read_bytes().endswith(b"own\n"))
        self.assertFalse((_backup(y) / "tobase.map").exists())

    def test_a_commented_alternative_above_the_setting_stays_a_comment(self):
        # Repro B.
        def edit(text, top):
            return text.replace("\ntobase_map ", '\n# tobase_map = "../old.map"'
                                                 "\ntobase_map ", 1)
        top, before = self._closed_from(edit)
        y = top / YEAR
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertEqual(_changed(_tree(top, filed=False), before), [])
        self.assertIn('tobase_map = "../tobase.map" set again', out)
        self.assertNotIn("old.map", out)

    def test_adjacent_keys_and_a_comment_of_the_persons_after_them(self):
        def edit(text, top):
            lines = [ln for ln in text.split("\n") if not ln.startswith("tobase_map")]
            i = next(n for n, ln in enumerate(lines) if ln.startswith("inputs_dir"))
            lines[i + 1:i + 1] = ['tobase_map = "../tobase.map"',
                                  '# tobase_map = "../spare.map"']
            return "\n".join(lines)
        top, before = self._closed_from(edit)
        y = top / YEAR
        self.assertEqual((y / "taxjson.toml").read_text().count(NOTE), 1)
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertEqual(_changed(_tree(top, filed=False), before), [])

    def test_a_quoted_key_comes_back_from_its_own_line(self):
        def edit(text, top):
            return text.replace("\ninputs_dir ", '\n"inputs_dir" ', 1)
        for record in (True, False):
            with self.subTest(record=record):
                top, before = self._closed_from(edit)
                y = top / YEAR
                if not record:
                    lock = y / "filed" / f"{YEAR}.json"
                    doc = json.loads(lock.read_text())
                    del doc["frozen"]
                    lock.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
                code, out, err, escaped = _unclose(y)
                self.assertEqual((code, escaped), (0, None), err)
                self.assertNotIn("was gone", out)
                self.assertNotIn("Not restored", out)
                self.assertEqual(_changed(_tree(top, filed=False), before), [])

    def test_a_line_edited_under_the_note_is_left_and_the_record_is_used(self):
        top = self._copy(self.closed)
        y = top / YEAR
        cfg = y / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace(
            '# inputs_dir                        = "../inputs"',
            '# inputs_dir                        = "../elsewhere"'))
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn('inputs_dir = "../inputs" (from the lock\'s record: the '
                      'commented line under the note is not the one close-year wrote, '
                      'and stays)', out)
        self.assertNotIn("was gone", out)
        text = cfg.read_text()
        self.assertIn('\n# inputs_dir                        = "../elsewhere"\n', text)
        from taxjson.lib import project_layout as PL
        self.assertEqual(PL.parse_config_text(text)["settings"]["inputs_dir"],
                         "../inputs")
        self.assertFalse((y / "inputs").exists())

    def test_with_no_record_a_second_line_under_the_note_is_left_and_said(self):
        # The lock deleted by hand, the two keys adjacent under one note:
        # nothing says which lines the close wrote, so only the first.
        def edit(text, top):
            lines = [ln for ln in text.split("\n") if not ln.startswith("tobase_map")]
            i = next(n for n, ln in enumerate(lines) if ln.startswith("inputs_dir"))
            lines.insert(i + 1, 'tobase_map = "../tobase.map"')
            return "\n".join(lines)
        top, before = self._closed_from(edit)
        y = top / YEAR
        (y / "filed" / f"{YEAR}.json").unlink()
        tob = (y / "tobase.map").read_bytes()
        code, out, err, escaped = _unclose(y)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn('Not restored:      `# tobase_map = "../tobase.map"` stays '
                      'commented (nothing records that close-year wrote it): if it did, '
                      'un-comment it by hand and move the year\'s own tobase.map away',
                      out)
        self.assertIn('\ninputs_dir                        = "../inputs"\n'
                      '# tobase_map = "../tobase.map"\n', (y / "taxjson.toml").read_text())
        self.assertEqual((y / "tobase.map").read_bytes(), tob)
        self.assertFalse((y / "inputs").exists())
        code, out, err, escaped = _tjs(y, "years")
        self.assertEqual((code, escaped), (0, None), err)


class UncloseYearSingleFolder(unittest.TestCase):
    """A project in one folder, its own inputs/ and tobase.map: the close
    froze nothing, so the lock and the reports as filed are all there is
    to take back."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        tmp = Path(cls._td.name)
        r = _cli(tmp, "init", "--demo", str(tmp / "demo"))
        assert r.returncode == 0, r.stdout + r.stderr
        cls.solo = tmp / "solo"
        shutil.copytree(tmp / "demo" / YEAR, cls.solo)
        shutil.copytree(tmp / "demo" / "inputs", cls.solo / "inputs")
        shutil.copy2(tmp / "demo" / "tobase.map", cls.solo / "tobase.map")
        cfg = cls.solo / "taxjson.toml"
        cfg.write_text("\n".join(
            ln for ln in cfg.read_text().split("\n")
            if not ln.startswith(("inputs_dir", "tobase_map", "exports_dir"))))
        r = _cli(cls.solo, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.before = _tree(tmp)
        r = _cli(cls.solo, "close-year", "--yes")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_only_the_lock_and_the_reports_as_filed_go(self):
        tmp = Path(self._td.name)
        self.assertNotIn(NOTE, (self.solo / "taxjson.toml").read_text())
        code, out, err, escaped = _unclose(self.solo)
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn(f"REOPENED {YEAR} — it is not filed any more", out)
        self.assertNotIn("Settings:", out)
        self.assertNotIn("Frozen copies:", out)
        self.assertNotIn("other inputs", out)
        b = sorted((self.solo / "filed").iterdir())
        self.assertEqual(len(b), 1)
        self.assertEqual(sorted(p.name for p in b[0].iterdir()),
                         [f"{YEAR}.json", "reports", "unclosed.json"])
        now = {k: v for k, v in _tree(tmp).items()
               if not k.startswith("solo/filed")}
        self.assertEqual(_changed(now, self.before), [])
        code, out, err, escaped = _tjs(self.solo, "check-filed")
        self.assertEqual((code, escaped), (0, None), err)
        self.assertIn("No filed/<year>.json snapshots", out)


class UncloseYearAllOrNothing(_Projects):

    def _run(self, point, kind):
        """An unclose of a copy of the closed project stopped at `point`
        by `kind` (a KINDS name, "SIGINT")."""
        _name, obj, attr, kw = point
        top = self._copy(self.closed)
        before = _tree(top)
        inj = _Inject(obj, attr, _exc(kind) if kind in KINDS else kind, **kw)
        with inj.patch():
            res = _unclose(top / YEAR)
        self.assertTrue(inj.fired, f"{attr} was not called as the case says")
        return top, before, res

    def test_the_points_are_where_they_say(self):
        # The lock is the unclose's 13th move (the reports, 11 copies).
        top = self._copy(self.closed)
        moves = []
        real = os.rename

        def spy(src, dst):
            if sys._getframe(1).f_code.co_name == "move":
                moves.append(Path(src).name)
            return real(src, dst)
        with mock.patch.object(os, "rename", spy):
            self.assert_reopened(top, _unclose(top / YEAR))
        self.assertEqual((moves[0], len(moves), moves[-1]),
                         ("reports", 13, f"{YEAR}.json"))

    def test_stopped_before_the_record_nothing_is_changed(self):
        for point in BEFORE:
            for kind in KINDS:
                with self.subTest(point=point[0], kind=kind):
                    top, before, res = self._run(point, kind)
                    self.assert_nothing_changed(top, before, res, kind)

    def test_stopped_after_the_record_the_year_is_reopened(self):
        for point in AFTER:
            for kind in KINDS:
                with self.subTest(point=point[0], kind=kind):
                    top, _before, (code, out, err, escaped) = self._run(point, kind)
                    self.assertIn("warning: the year is reopened: filed/", err)
                    self.assertIn("holds what was filed, but the report stopped", err)
                    self.assertEqual(_changed(_tree(top, filed=False),
                                              _tree(self.base, filed=False)), [])
                    b = _backup(top / YEAR)
                    self.assertTrue((b / f"{YEAR}.json").is_file())
                    self.assertFalse((b / ".removed").exists())
                    if kind == "KeyboardInterrupt":
                        self.assertEqual(code, 130, err)
                    elif kind == "OSError":     # the house's one line, exit 2
                        self.assertEqual(code, 2, err)
                    else:                       # raised on, as it was
                        self.assertIsInstance(escaped, RuntimeError, err)

    def test_ctrl_c_again_while_it_puts_back_waits(self):
        # A real Ctrl-C at a call, then one more at every write and
        # removal after it.
        for name, obj, attr, kw in MOVED_BEFORE:
            with self.subTest(point=name):
                top = self._copy(self.closed)
                before = _tree(top)
                first = _Inject(obj, attr, "SIGINT", **kw)
                with first.patch(), _ctrl_c_again(first) as again:
                    res = _unclose(top / YEAR)
                self.assertTrue(first.fired, f"{attr} was not called")
                self.assertTrue(again, "no write after the first Ctrl-C")
                self.assert_nothing_changed(top, before, res, "KeyboardInterrupt")

    def test_verdicts_that_cannot_be_dropped_are_a_warning(self):
        top = self._copy(self.closed)
        with mock.patch.object(lib_checklist, "forget_results", lambda root: False):
            res = _unclose(top / YEAR)
        self.assert_reopened(top, res)
        self.assertIn("warning: the checklist verdicts kept in work/ could not be "
                      "dropped", res[2])

    def test_ctrl_c_as_it_ends_says_the_year_is_reopened(self):
        top = self._copy(self.closed)
        inj = _Inject(write_step.WriteStep, "release", "SIGINT")
        with inj.patch():
            code, _o, err, escaped = _unclose(top / YEAR)
        self.assertTrue(inj.fired)
        self.assertIsNone(escaped, err)
        self.assertEqual(code, 130, err)
        self.assertIn("the year is reopened", err)
        self.assertEqual(_changed(_tree(top, filed=False),
                                  _tree(self.base, filed=False)), [])

    def test_a_move_that_fails_is_exit_2_and_put_back(self):
        top, before, res = self._run(
            ("fifth move", os, "rename", {"caller": "move", "nth": 5}), "OSError")
        self.assertEqual(res[0], 2, res[2])
        self.assertIn("tjs unclose-year: error: cannot move inputs/", res[2])
        self.assert_nothing_changed(top, before, res, "OSError")


if __name__ == "__main__":
    unittest.main()
