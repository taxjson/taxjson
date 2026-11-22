"""GitHub issues #9 (overlapping atomic writers) and #10 (`run --fast`,
deleted since, ignored a change to the shipped package data: the
records name it now). (#7, the pre-push binary
scan, is pinned in test_check_pii.py.) Backlog TJ-4893: an atomic
write interrupted at any point keeps the interrupt and leaves no temp
file. Synthetic data only.

Formerly: test_fix_issues7910"""
import dis
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from taxjson.lib.project_layout import _GAINS_ALL_SUFFIX
from taxjson.lib.records import record_path
from taxjson.pipeline import run_state
import _paths
REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))


# ------------------------------------------------------------------ #9

class TestOverlappingAtomicWriters(unittest.TestCase):
    """Two atomic_open writers of one final path each own a private temp
    file until their own rename: neither publishes the other's
    unfinished data, and neither fails when the other renames first."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "work" / "m_base.json"
        self.out.parent.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _temps(self):
        return sorted(p.name for p in self.out.parent.iterdir()
                      if p.name != self.out.name)

    def test_interleaved_writers(self):
        from taxjson.lib.safe_write import atomic_open
        a_cm, b_cm = atomic_open(self.out), atomic_open(self.out)
        a = a_cm.__enter__()
        a.write("writer A, complete\n")
        b = b_cm.__enter__()
        b.write("writer B, part 1\n")
        b.flush()
        self.assertEqual(len(self._temps()), 2, self._temps())
        a_cm.__exit__(None, None, None)
        self.assertEqual(self.out.read_text(), "writer A, complete\n")
        b.write("writer B, part 2\n")
        b.flush()
        # B's unfinished data never shows under the final name.
        self.assertEqual(self.out.read_text(), "writer A, complete\n")
        b_cm.__exit__(None, None, None)
        self.assertEqual(self.out.read_text(),
                         "writer B, part 1\nwriter B, part 2\n")
        self.assertEqual(self._temps(), [])

    def test_nested_writers(self):
        from taxjson.lib.safe_write import atomic_open
        with atomic_open(self.out) as outer:
            outer.write("outer\n")
            with atomic_open(self.out) as inner:
                inner.write("inner\n")
            self.assertEqual(self.out.read_text(), "inner\n")
            outer.write("outer, more\n")
        self.assertEqual(self.out.read_text(), "outer\nouter, more\n")
        self.assertEqual(self._temps(), [])

    def test_failed_inner_writer_leaves_the_outer_alone(self):
        from taxjson.lib.safe_write import atomic_open
        with atomic_open(self.out, binary=True) as outer:
            outer.write(b"outer\n")
            with self.assertRaises(RuntimeError):
                with atomic_open(self.out, binary=True) as inner:
                    inner.write(b"half")
                    raise RuntimeError("boom")
        self.assertEqual(self.out.read_bytes(), b"outer\n")
        self.assertEqual(self._temps(), [])

    def test_temp_is_an_owner_only_sibling_with_the_suffix(self):
        from taxjson.lib.safe_write import atomic_open
        with atomic_open(self.out, suffix=".migrate.part") as f:
            f.write("x")
            (name,) = self._temps()
            self.assertTrue(name.startswith("m_base.json."), name)
            self.assertTrue(name.endswith(".migrate.part"), name)
            mode = stat.S_IMODE(os.lstat(self.out.parent / name).st_mode)
            self.assertEqual(mode & 0o077, 0, oct(mode))
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode) & 0o077, 0)

    def test_keep_mode_still_takes_the_replaced_files_bits(self):
        from taxjson.lib.safe_write import write_atomic
        self.out.write_text("old\n")
        os.chmod(self.out, 0o644)
        write_atomic(self.out, "new\n", keep_mode=True)
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o644)
        self.assertEqual(self.out.read_text(), "new\n")

    def test_concurrent_processes_publish_whole_files_only(self):
        # Two processes rewriting one file many times: every read sees
        # one writer's complete text, and no save fails.
        script = (
            "import sys\n"
            "from taxjson.lib.safe_write import atomic_open\n"
            "out, tag = sys.argv[1], sys.argv[2]\n"
            "for i in range(150):\n"
            "    with atomic_open(out) as f:\n"
            "        for _ in range(50):\n"
            "            f.write(tag * 40 + '\\n')\n"
            "            f.flush()\n")
        env = dict(os.environ, PYTHONPATH=str(SRC))
        procs = [subprocess.Popen([sys.executable, "-c", script,
                                   str(self.out), tag], env=env,
                                  stderr=subprocess.PIPE, text=True)
                 for tag in ("a", "b")]
        whole = {(t * 40 + "\n") * 50 for t in ("a", "b")}
        torn = 0
        while any(p.poll() is None for p in procs):
            try:
                text = self.out.read_text()
            except FileNotFoundError:
                continue
            if text not in whole:
                torn += 1
        errs = [p.communicate()[1] for p in procs]
        self.assertEqual(errs, ["", ""])
        self.assertEqual([p.returncode for p in procs], [0, 0])
        self.assertEqual(torn, 0)
        self.assertEqual(self._temps(), [])


# ------------------------------------------------------------- TJ-4893

_STDLIB = os.path.dirname(os.__file__) + os.sep


def _traced(code) -> bool:
    """safe_write's own code and the standard library's it calls (the
    `with` statement's own contextlib frames left out)."""
    from taxjson.lib import safe_write
    name = code.co_filename
    if name == safe_write.__file__:
        return True
    return (name.startswith(_STDLIB) and "site-packages" not in name
            and os.path.basename(name) != "contextlib.py")


def _interrupted_at_each_step(call):
    """Run `call()` once to its end, then once for each bytecode
    instruction it runs in `_traced` code, a KeyboardInterrupt raised
    there (a trace hook: every point a Ctrl-C can be raised at, and
    more; a NOP, the line a `try:` stands on, raises nothing, and no
    handler covers it), then once more to its end. Yields, after each
    run, what it raised (the type and text) or None. (The first run
    is there because a function's first traced call sends no opcode
    events.)"""
    nop = dis.opmap["NOP"]
    n = 0
    while True:
        steps = [0]

        def step(frame, event, arg):
            if (event == "opcode"
                    and frame.f_code.co_code[frame.f_lasti] != nop):
                steps[0] += 1
                if steps[0] == n:
                    raise KeyboardInterrupt
            return step

        def enter(frame, event, arg):
            if not _traced(frame.f_code):
                return None
            frame.f_trace_opcodes = True
            return step

        raised = None
        with warnings.catch_warnings():
            # (a file object an interrupt dropped is closed as it goes)
            warnings.simplefilter("ignore", ResourceWarning)
            sys.settrace(enter)
            try:
                call()
            except (KeyboardInterrupt, OSError) as e:
                raised = (type(e), str(e))
            finally:
                sys.settrace(None)
        yield raised
        if steps[0] < n:                # ran to its end
            return
        n += 1


def _open_fds():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:                     # no /proc: not counted
        return None


class TestInterruptedAsTheTempOpens(unittest.TestCase):
    """Backlog TJ-4893: a write interrupted at any point in atomic_open
    (as its temp file is made, as the file object takes the descriptor,
    as it is renamed) keeps the interrupt and leaves no temp file and no
    open descriptor: the old file or the new one, whole. An interrupt as
    os.fdopen returned closed the descriptor twice, and the second
    close's EBADF replaced it ("Bad file descriptor": a close-year
    refusal); one as tempfile.mkstemp returned left the temp file
    `<out>.<random>.part`. open_new and backup_copy, the other new
    files, keep the interrupt too."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "work"
        self.dir.mkdir()
        self.out = self.dir / "m_base.json"
        self.out.write_bytes(b"old\n")

    def _sweep(self, data, mode=None, **kw):
        from taxjson.lib.safe_write import write_atomic
        new = data if isinstance(data, bytes) else data.encode()
        fds, runs = _open_fds(), 0
        for raised in _interrupted_at_each_step(
                lambda: write_atomic(self.out, data, **kw)):
            runs += 1
            kind = raised[0] if raised else None
            self.assertIn(kind, (None, KeyboardInterrupt), f"run {runs}: {raised}")
            self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                             [self.out.name], f"run {runs}: a temp file left")
            self.assertIn(self.out.read_bytes(),
                          (b"old\n", new) if raised else (new,), f"run {runs}")
            if mode is not None:
                self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), mode)
            self.out.write_bytes(b"old\n")
        self.assertGreater(runs, 50)
        self.assertEqual(_open_fds(), fds, "a descriptor left open")

    def test_a_write_interrupted_at_any_point(self):
        self._sweep("new\n")

    def test_a_kept_files_rewrite_interrupted_at_any_point(self):
        os.chmod(self.out, 0o644)
        self._sweep(b"new\n", mode=0o644, keep_mode=True)

    def test_open_new_and_backup_copy_keep_the_interrupt(self):
        from taxjson.lib.safe_write import backup_copy, open_new

        def new_file():
            with open_new(self.dir / "x.part") as f:
                f.write("x")
        for call in (new_file, lambda: backup_copy(self.out)):
            fds, runs = _open_fds(), 0
            for raised in _interrupted_at_each_step(call):
                runs += 1
                kind = raised[0] if raised else None
                self.assertIn(kind, (None, KeyboardInterrupt), f"run {runs}: {raised}")
                for p in self.dir.iterdir():
                    if p != self.out:
                        p.unlink()
            self.assertGreater(runs, 20)
            self.assertEqual(_open_fds(), fds, "a descriptor left open")


# ------------------------------------------------------------------ #10

def _mock_package(root: Path, markets: str) -> Path:
    pkg = root / "taxjson"
    (pkg / "data").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "data" / "markets.toml").write_text(markets)
    return pkg


class TestPackageDataInvalidatesTheRecords(unittest.TestCase):
    """The records name the package's code (the rates rebuild what other
    code fetched; a run and a view build everything every time: TJ-4307);
    the shipped data the code reads (data/markets.toml: venues, index-option
    roots, stablecoins, ...) is part of it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        from taxjson.pipeline import freshness
        self.freshness = freshness
        self.pkg = _mock_package(
            self.tmp, '[lists]\nindex_option_roots = ["SYNTH"]\n')
        self._file = mock.patch("taxjson.__file__",
                                str(self.pkg / "__init__.py"))
        self._file.start()
        self._saved_fp = run_state.package_fingerprint
        run_state.package_fingerprint = None

    def tearDown(self):
        self._file.stop()
        run_state.package_fingerprint = self._saved_fp
        self._tmp.cleanup()

    def test_fingerprint_changes_with_markets_toml(self):
        before = self.freshness._package_fingerprint()
        data = self.pkg / "data" / "markets.toml"
        st = data.stat()
        data.write_text("[lists]\nindex_option_roots = []\n")
        os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns))   # cp -p
        self.assertNotEqual(self.freshness._package_fingerprint(), before)

    def test_fingerprint_sees_a_new_or_removed_data_file(self):
        before = self.freshness._package_fingerprint()
        extra = self.pkg / "data" / "extra.csv"
        extra.write_text("a,b\n")
        added = self.freshness._package_fingerprint()
        self.assertNotEqual(added, before)
        extra.unlink()
        self.assertEqual(self.freshness._package_fingerprint(), before)

    def test_caches_and_editor_litter_do_not_count(self):
        before = self.freshness._package_fingerprint()
        (self.pkg / "__pycache__").mkdir()
        (self.pkg / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
        (self.pkg / "data" / ".DS_Store").write_bytes(b"\0")
        (self.pkg / "data" / "markets.toml~").write_text("old")
        (self.pkg / "data" / ".markets.toml.swp").write_bytes(b"\0")
        (self.pkg / "README.md").write_text("docs\n")
        self.assertEqual(self.freshness._package_fingerprint(), before)

_CONFIG = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""

_TT = ("BUYSELL 2025-01-06 10:00:00 ABC.TO 100 CAD 10 -1000 0\n"
       "BUYSELL 2025-03-03 10:00:00 ABC.TO -40 CAD 12 480 0\n")


class TestAViewRebuildsAfterMarketsChange(unittest.TestCase):
    """Pipeline: a view builds its full-history file every time (no record's
    word: TJ-4307), and the record it writes for the sweep names the code:
    a copy of the package whose data/markets.toml changes (its mtime kept,
    as cp -p or an rsync -a upgrade leaves it) is another code."""

    def test_the_full_history_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            lib = td / "lib"
            shutil.copytree(SRC / "taxjson", lib / "taxjson",
                            ignore=shutil.ignore_patterns("__pycache__"))
            root = td / "proj"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(_CONFIG)
            (root / "inputs" / "margin" / "a.tt").write_text(_TT)
            env = dict(os.environ, PYTHONPATH=str(lib),
                       TAXJSON_OFFLINE="1", HOME=str(td / "home"),
                       NO_COLOR="1")
            (td / "home").mkdir()

            def tjs(*a):
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                     str(root), *a], cwd=str(td),
                    capture_output=True, text=True, env=env,
                    stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                return r.stdout

            tjs("run", "--no-input")
            tjs("stats", "--all-history")
            built = root / "work" / f"margin{_GAINS_ALL_SUFFIX}"
            first = built.stat().st_mtime_ns - 10 ** 9
            os.utime(built, ns=(first, first))

            def code():
                return json.loads(record_path(built).read_text(encoding="utf-8"))["code"]
            before = code()
            tjs("stats", "--all-history")
            self.assertGreater(built.stat().st_mtime_ns, first, "built again, nothing changed")
            self.assertEqual(code(), before)
            data = lib / "taxjson" / "data" / "markets.toml"
            st = data.stat()
            with open(data, "a", encoding="utf-8") as f:
                f.write("\n# a market-data-only update\n")
            os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns))
            tjs("stats", "--all-history")
            self.assertNotEqual(code(), before, "the record names the code it was built by")

if __name__ == "__main__":
    unittest.main()
