"""Backlog group `errors` (2026-10-25): error messages, atomic writes and
the smaller commands (elect, fetch --list, slips, t1135, tax-logic).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account 99900001.

Formerly: test_fix_backlog_errors
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _demo(tmp):
    """`init --demo` (the years layout: exports shared in inputs/, a
    saved spin-off election); the 2024 year folder."""
    r = _tjs(None, "init", "--demo", str(Path(tmp) / "demo"))
    assert r.returncode == 0, r.stderr
    return Path(tmp) / "demo" / "2024"


_DEV_FULL = "/dev/full"       # every write: ENOSPC (Linux); no disk filled


def _fill_disk(f):
    """Make every later write through `f`'s descriptor fail as on a full
    disk: /dev/full put in its place (never a real disk filled)."""
    fd = os.open(_DEV_FULL, os.O_WRONLY)
    try:
        os.dup2(fd, f.fileno())
    finally:
        os.close(fd)


@unittest.skipUnless(os.path.exists(_DEV_FULL), "needs /dev/full")
class TestFullDisk(unittest.TestCase):
    """TJ-0106: on a full disk the message names the file and the fix,
    once; a failed stage keeps its "stopped at" line.

    Formerly: test_fix_backlog_errors.TestFullDisk"""

    def test_describe_names_the_output_and_the_fix(self):
        import errno
        from taxjson.lib.cli_diag import describe_input_error
        msg = describe_input_error(OSError(errno.EFBIG, "File too large"))
        self.assertEqual(msg, "cannot write the output: File too large "
                              "(disk full?) — free some space on the disk "
                              "and run again")
        e = OSError(errno.ENOSPC, "No space left on device")
        e.filename = "/zz/nowhere/work/x.json"
        self.assertTrue(describe_input_error(e).startswith(
            "cannot write /zz/nowhere/work/x.json: No space left on "
            "device (disk full?)"))

    def test_said_once_by_nested_guards(self):
        import contextlib
        import io
        from taxjson.lib.cli_diag import guard_main
        with tempfile.TemporaryDirectory() as td:
            with open(Path(td) / "out.json", "w") as out:
                def main():
                    print("x" * 64)
                    _fill_disk(out)
                err = io.StringIO()
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err), \
                        self.assertRaises(SystemExit) as cm:
                    guard_main("taxjson-zz")(guard_main("taxjson-zz")(main))()
                self.assertEqual(cm.exception.code, 2)
                try:
                    out.close()
                except OSError:
                    pass
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("cannot write the output: No space left on device "
                      "(disk full?)", lines[0])

    def test_atomic_open_keeps_the_block_error(self):
        import subprocess as sp
        from taxjson.lib.safe_write import atomic_open
        with tempfile.TemporaryDirectory() as td:
            final = Path(td) / "zz.json"
            final.write_text("old")
            with self.assertRaises(sp.CalledProcessError):
                with atomic_open(final) as f:
                    f.write("new" * 10)
                    _fill_disk(f)
                    raise sp.CalledProcessError(2, ["zz"])
            self.assertEqual(final.read_text(), "old")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["zz.json"])

    def test_atomic_open_names_the_file(self):
        from taxjson.lib.safe_write import atomic_open
        with tempfile.TemporaryDirectory() as td:
            final = Path(td) / "zz.json"
            final.write_text("old")
            with self.assertRaises(OSError) as cm:
                with atomic_open(final) as f:
                    f.write("new" * 10)
                    _fill_disk(f)
            self.assertEqual(cm.exception.filename, str(final))
            self.assertEqual(final.read_text(), "old")

    def _run_to_file(self, fake):
        import contextlib
        import io
        import subprocess as sp
        from unittest import mock
        from taxjson.pipeline import tools as pipeline_tools
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "work" / "margin_zz.json"
            out.parent.mkdir()
            out.write_text("old")
            err = io.StringIO()
            with mock.patch("taxjson.pipeline.dispatch.run_cmd", fake), \
                    contextlib.redirect_stderr(err), \
                    self.assertRaises(sp.CalledProcessError) as cm:
                pipeline_tools.run_to_file(["python", "-m", "taxjson.bin.taxjson_brokerage",
                                "--account", "margin"], out)
            self.assertEqual(out.read_text(), "old")
        return cm.exception, err.getvalue()

    def test_failed_stage_names_the_work_file(self):
        import subprocess as sp
        from taxjson.cli import main as cli_main
        from taxjson.pipeline import tools as pipeline_tools
        line = ("taxjson-brokerage: error: cannot write the output: File "
                "too large (disk full?) — free some space on the disk and "
                "run again\n")

        def fake(cmd, stdout=None, **kw):
            return sp.CompletedProcess(cmd, 2, stdout=None, stderr=line)
        e, shown = self._run_to_file(fake)
        self.assertEqual(shown.count("cannot write"), 1, shown)
        self.assertIn("cannot write work/margin_zz.json: File too large "
                      "(disk full?)", shown)
        why = cli_main._failure_headline(pipeline_tools._stage_text_shown(
            e.stderr, e.cmd, getattr(e, "out_path", None)))
        self.assertEqual(why, "cannot write work/margin_zz.json: File too "
                              "large (disk full?)")
        self.assertIn("for account margin", cli_main._stage_description(e.cmd))

    def test_the_runs_own_write_names_the_work_file(self):
        import subprocess as sp

        def fake(cmd, stdout=None, **kw):
            stdout.write("{}" * 10)
            _fill_disk(stdout)
            return sp.CompletedProcess(cmd, 0, stdout=None, stderr="")
        e, shown = self._run_to_file(fake)
        self.assertEqual(e.returncode, 2)
        self.assertIn("cannot write work/margin_zz.json: No space left on "
                      "device (disk full?)", e.stderr)
        self.assertIn("(disk full?) Free some space on the disk and run "
                      "again.", " ".join(shown.split()))


_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
_MINIMAL_TOML = '[settings]\ncountry = "canada"\nyear = 2024\n'


def _locked(path, mode):
    """chmod `path` to `mode`; the cleanup puts 0o755 / 0o644 back while
    it exists (TemporaryDirectory removes a read-only tree itself)."""
    path.chmod(mode)

    def _unlock():
        if path.exists():
            path.chmod(0o755 if path.is_dir() else 0o644)
    return _unlock


class TestWriteFailuresNameTheFile(unittest.TestCase):
    """TJ-0056: a write refused names the file asked for (never the temp
    or backup file beside it), the reason and the fix.

    Formerly: test_fix_backlog_errors.TestWriteFailuresNameTheFile"""

    def test_cannot_write_texts(self):
        import errno
        from taxjson.lib.cli_diag import cannot_write
        self.assertEqual(
            cannot_write("out/x.csv", OSError(errno.ENOSPC, "No space left "
                                              "on device")),
            ("cannot write out/x.csv: No space left on device (disk full?)",
             "Free some space on the disk."))
        self.assertEqual(
            cannot_write("x.csv", OSError(errno.EROFS, "Read-only file "
                                          "system"), alt=", or zz")[1],
            "The disk it is on is mounted read-only, or zz.")
        self.assertEqual(
            cannot_write("x.csv", OSError(errno.EIO, "I/O error")),
            ("cannot write x.csv: I/O error", ""))

    @unittest.skipIf(_ROOT, "root ignores permissions")
    def test_xlsx_to_csv_into_a_read_only_folder(self):
        import contextlib
        import io
        import sys
        import types
        from unittest import mock
        from taxjson.bin import xlsx_to_csv as X

        class _Frame:
            def to_csv(self, *a, **k):
                return "a,b\n1,2\n"
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "zz.xlsx").write_bytes(b"")
            ro = Path(td) / "rodir"
            ro.mkdir()
            self.addCleanup(_locked(ro, 0o555))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            err = io.StringIO()
            with mock.patch.object(X, "read_sheet",
                                   lambda f, s: _Frame()), \
                    mock.patch.object(X, "_apply_cells", lambda d, fn: d), \
                    mock.patch.dict(sys.modules,
                                    {"pandas": types.ModuleType("pandas")}), \
                    contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                X.convert_xlsx_to_csv("zz.xlsx", "rodir/x.csv")
            self.assertEqual(cm.exception.code, 2)
            text = " ".join(err.getvalue().split())
            self.assertIn("cannot write rodir/x.csv: Permission denied",
                          text)
            self.assertIn("The folder rodir/ is not writable: `chmod u+w "
                          "rodir`, or choose another -o.", text)
            self.assertNotIn(".part", text)
            self.assertEqual(list(ro.iterdir()), [])

    @unittest.skipIf(_ROOT, "root ignores permissions")
    def test_format_in_a_read_only_folder(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            (proj / "taxjson.toml").write_text(_MINIMAL_TOML)
            self.addCleanup(_locked(proj, 0o555))
            r = _tjs(None, "-C", "proj", "format", "--write", cwd=td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("cannot write proj/taxjson.toml: Permission "
                          "denied", r.stderr)
            self.assertIn("The folder proj/ is not writable: `chmod u+w "
                          "proj`. Nothing was changed.", r.stderr)
            self.assertNotIn(".bak", r.stderr)
            self.assertEqual((proj / "taxjson.toml").read_text(),
                             _MINIMAL_TOML)

    @unittest.skipIf(_ROOT, "root ignores permissions")
    def test_run_with_a_read_only_work_folder(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            work = year / "work"
            work.mkdir(exist_ok=True)
            self.addCleanup(_locked(work, 0o555))
            r = _tjs(None, "-C", "demo/2024", "run", cwd=td)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("cannot write to demo/2024/work/: Permission "
                          "denied", r.stderr)
            self.assertIn("`chmod u+w demo/2024/work` makes it writable. "
                          "Nothing was run.", r.stderr)


@unittest.skipIf(_ROOT, "root ignores permissions")
class TestReadOnlyUserFile(unittest.TestCase):
    """TJ-0107: a user file made read-only (a filed year's taxjson.toml)
    is refused, not replaced; a generated file is still replaced.

    Formerly: test_fix_backlog_errors.TestReadOnlyUserFile"""

    def test_format_refuses_a_read_only_toml(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "taxjson.toml"
            cfg.write_text(_MINIMAL_TOML)
            self.addCleanup(_locked(cfg, 0o444))
            r = _tjs(None, "format", "--write", cwd=td)
            self.assertEqual(r.returncode, 2, r.stderr)
            text = " ".join(r.stderr.split())
            self.assertIn("cannot write taxjson.toml: the file is "
                          "read-only", text)
            self.assertIn("`chmod u+w taxjson.toml` lets taxjson rewrite "
                          "it. Nothing was changed.", text)
            self.assertEqual(cfg.read_text(), _MINIMAL_TOML)
            # (and the refusal's own log: logs/, lib/command_log)
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["logs", "taxjson.toml"])

    def test_safe_write_user_files(self):
        from taxjson.lib.safe_write import (ReadOnlyFileError, write_atomic,
                                            write_user_file)
        with tempfile.TemporaryDirectory() as td:
            tm = Path(td) / "ticker.map"
            tm.write_text("old\n")
            self.addCleanup(_locked(tm, 0o444))
            with self.assertRaises(ReadOnlyFileError) as cm:
                write_user_file(tm, "new\n", td)
            self.assertEqual(cm.exception.filename, str(tm))
            with self.assertRaises(ReadOnlyFileError):
                write_atomic(tm, "new\n", keep_mode=True)
            self.assertEqual(tm.read_text(), "old\n")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["ticker.map"])
            # A generated file (no keep_mode) is replaced as before.
            gen = Path(td) / "zz.json"
            gen.write_text("old")
            self.addCleanup(_locked(gen, 0o444))
            write_atomic(gen, "new")
            self.assertEqual(gen.read_text(), "new")

    def test_guard_names_the_file_and_the_fix(self):
        from taxjson.lib.cli_diag import describe_input_error
        from taxjson.lib.safe_write import refuse_read_only
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "slips.toml"
            p.write_text("")
            self.addCleanup(_locked(p, 0o444))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            with self.assertRaises(OSError) as cm:
                refuse_read_only(p)
            self.assertEqual(describe_input_error(cm.exception),
                             "cannot write slips.toml: the file is "
                             "read-only — `chmod u+w slips.toml` lets "
                             "taxjson rewrite it")


if __name__ == "__main__":
    unittest.main()
