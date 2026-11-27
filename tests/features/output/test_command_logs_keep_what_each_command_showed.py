"""Command logs (TJ-5366; docs/commands.md, "Command logs"): every command
run in a project keeps what it showed in logs/<command>.log.<time>, and the
latest run in logs/<command>.log, while the terminal's bytes, the exit code
and the books stay as they were.

Each test runs `tjs` as a person does (a child process, piped or on a
pseudo-terminal) in its own copy of one `tjs init --demo` project, built
and run once in a private temp folder; "with no tee" is the same `tjs` with
lib/command_log's console() a no-op (nothing captured: the bytes as before
the logs). Synthetic data only (the demo's).
"""
import contextlib
import io
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import _paths
from _tmpfiles import private_dir

from taxjson.lib.dispatch import python_module_argv

REPO = _paths.REPO
_TIMEOUT = 600
# The demo's top folder after `init --demo` and one `tjs run` (setUpModule).
_BUILT = {}
_HEAD = re.compile(r"# taxjson log: (?P<line>.*)\n"
                   r"# taxjson \S+, started \d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}\n")
_FOOT = re.compile(r"# exit (?P<code>-?\d+) after \d+\.\d s\n\Z")


def _env(**extra):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    for k in ("TAXJSON_LOG_PARENT", "TAXJSON_DISPATCH", "COLUMNS", "LINES"):
        env.pop(k, None)
    for k, v in extra.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


# `tjs` with lib/command_log's console() a no-op (no tee, no log) and its
# questions asked by input() itself, as before the logs.
_UNTEED = ("import contextlib, runpy\n"
           "from taxjson.lib import command_log\n"
           "command_log.console = lambda nested=False: contextlib.nullcontext()\n"
           "def _plain(prompt='', stream=None):\n"
           "    if stream is None:\n"
           "        return input(prompt)\n"
           "    print(prompt, end='', file=stream, flush=True)\n"
           "    return input()\n"
           "command_log.ask = _plain\n"
           "runpy.run_module('taxjson.bin.taxjson_run', run_name='__main__',"
           " alter_sys=True)\n")


def _argv(args, untee=False):
    args = [str(a) for a in args]
    if untee:
        return [sys.executable, "-P", "-c", _UNTEED, *args]
    return python_module_argv("taxjson.bin.taxjson_run", args)


def _tjs(*args, cwd=None, merged=False, untee=False, stdout=subprocess.PIPE,
         preexec_fn=None, **env):
    """`tjs ARGS` piped: (exit code, stdout, stderr) as bytes; `merged`:
    stderr into stdout, in the order printed (stderr is then b"");
    `untee`: with no tee (_UNTEED)."""
    r = subprocess.run(
        _argv(args, untee), stdin=subprocess.DEVNULL, stdout=stdout,
        stderr=(subprocess.STDOUT if merged else subprocess.PIPE),
        env=_env(**env), cwd=cwd, timeout=_TIMEOUT, preexec_fn=preexec_fn)
    return r.returncode, r.stdout or b"", r.stderr or b""


def _on_a_pty(*args, cols=100, untee=False, interrupt_after=None, **env):
    """`tjs ARGS` with stdout and stderr on one pseudo-terminal `cols`
    wide, no TAXJSON_WIDTH (the terminal's width): (exit code, the bytes
    the terminal received). `interrupt_after`: once those bytes are on
    the terminal, SIGINT to its process group (Ctrl-C)."""
    import fcntl
    import struct
    import termios
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))
    p = subprocess.Popen(
        _argv(args, untee), stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
        env=_env(TAXJSON_WIDTH=None, **env), start_new_session=True)
    os.close(slave)
    chunks, deadline = [], time.monotonic() + _TIMEOUT
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                p.kill()
                raise AssertionError(f"tjs {' '.join(map(str, args))}: timed out")
            if interrupt_after is not None and interrupt_after in b"".join(chunks):
                os.killpg(p.pid, signal.SIGINT)
                interrupt_after = None
            ready, _, _ = select.select([master], [], [], min(left, 5))
            if not ready:
                if p.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 65536)
            except OSError:             # EIO: every writer closed the terminal
                break
            if not data:
                break
            chunks.append(data)
    finally:
        os.close(master)
    return p.wait(timeout=_TIMEOUT), b"".join(chunks)


def setUpModule():
    top = Path(private_dir("taxjson-logs-built-")) / "demo"
    rc, out, err = _tjs("init", "--demo", top, cwd=top.parent)
    assert rc == 0, (out + err).decode(errors="replace")[-2000:]
    year = _year(top)
    rc, out, err = _tjs("-C", year, "run")
    assert rc == 0, (out + err).decode(errors="replace")[-2000:]
    _BUILT["top"] = top


def _year(top):
    return next(p for p in sorted(top.iterdir())
                if p.is_dir() and (p / "taxjson.toml").is_file())


def _project():
    """A copy of the built demo, in a private folder of its own, with no
    logs/ yet: (its top folder, its year folder, the private folder)."""
    tmp = Path(private_dir("taxjson-logs-"))
    top = tmp / "demo"
    shutil.copytree(_BUILT["top"], top, symlinks=True)
    year = _year(top)
    shutil.rmtree(year / "logs", ignore_errors=True)
    return top, year, tmp


def _set_keep(year, n):
    cfg = year / "taxjson.toml"
    text = re.sub(r"(?m)^log_keep = .*\n", "", cfg.read_text(encoding="utf-8"))
    cfg.write_text(text.replace("[settings]\n", f"[settings]\nlog_keep = {n}\n", 1),
                   encoding="utf-8")


def _stamped(year, cmd):
    """logs/<cmd>.log.<time> names, sorted."""
    pat = re.compile(rf"{re.escape(cmd)}\.log\.\d{{8}}-\d{{6}}(?:-\d+)?\Z")
    d = year / "logs"
    return sorted(p.name for p in d.iterdir() if pat.match(p.name)) if d.is_dir() else []


def _body(tc, log_bytes):
    """A log's header line (the command line), its exit code and the text
    between."""
    text = log_bytes.decode("utf-8")
    head, foot = _HEAD.match(text), _FOOT.search(text)
    tc.assertIsNotNone(head, text[:300])
    tc.assertIsNotNone(foot, text[-300:])
    return head["line"], int(foot["code"]), text[head.end():foot.start()]


class TestBothFilesHeaderAndExitCode(unittest.TestCase):

    def test_a_command_writes_its_log_and_the_latest_copy(self):
        top, year, _ = _project()
        rc, out, _ = _tjs("-C", year, "sum", merged=True)
        self.assertEqual(rc, 0, out.decode()[-2000:])
        names = _stamped(year, "sum")
        self.assertEqual(len(names), 1, names)
        stamped = (year / "logs" / names[0]).read_bytes()
        self.assertEqual((year / "logs" / "sum.log").read_bytes(), stamped)
        line, code, body = _body(self, stamped)
        self.assertEqual(line, f"tjs -C {year} sum")
        self.assertEqual(code, 0)
        self.assertEqual(body, out.decode("utf-8"))
        # Owner-only, and never committed.
        self.assertEqual((year / "logs").stat().st_mode & 0o777, 0o700)
        self.assertEqual((year / "logs" / names[0]).stat().st_mode & 0o777, 0o600)
        self.assertIn("*", (year / "logs" / ".gitignore").read_text().splitlines())
        self.assertIn("/*/logs/", (top / ".gitignore").read_text().splitlines())

    def test_a_failed_command_logs_its_exit_code_and_message(self):
        _, year, _ = _project()
        for args in (("sum", "zzz"), ("sum", "./margin")):
            with self.subTest(args=args):
                rc, out, _ = _tjs("-C", year, *args, merged=True)
                self.assertNotEqual(rc, 0, out.decode()[-2000:])
                _, code, body = _body(self, (year / "logs" / "sum.log").read_bytes())
                self.assertEqual(code, rc)
                self.assertEqual(body, out.decode("utf-8"))

    def test_each_command_of_a_chain_has_its_own_log(self):
        _, year, _ = _project()
        rc, out, _ = _tjs("-C", year, "sum", "--", "stats", merged=True)
        self.assertEqual(rc, 0, out.decode()[-2000:])
        bodies = []
        for cmd in ("sum", "stats"):
            self.assertEqual(len(_stamped(year, cmd)), 1, cmd)
            _, code, body = _body(self, (year / "logs" / f"{cmd}.log").read_bytes())
            self.assertEqual(code, 0)
            bodies.append(body)
        self.assertEqual("".join(bodies), out.decode("utf-8"))

    def test_a_command_another_runs_is_in_its_log(self):
        # `tjs amt` builds on the estimate (a nested command): one log.
        _, year, _ = _project()
        rc, out, _ = _tjs("-C", year, "amt", merged=True)
        self.assertEqual(rc, 0, out.decode()[-2000:])
        self.assertEqual(sorted(p.name for p in (year / "logs").iterdir()
                                if not p.name.startswith(".")),
                         ["amt.log", *_stamped(year, "amt")])
        self.assertEqual(_body(self, (year / "logs" / "amt.log").read_bytes())[2],
                         out.decode("utf-8"))


class TestTheTerminalSeesTheSameBytes(unittest.TestCase):
    """Logs on and logs off (`log_keep = 0`) print the same bytes."""

    def test_piped(self):
        _, year, _ = _project()
        _set_keep(year, 0)
        off = _tjs("-C", year, "sum")
        self.assertFalse((year / "logs").exists())
        _set_keep(year, 20)
        on = _tjs("-C", year, "sum")
        self.assertEqual(on, off)
        self.assertEqual(len(_stamped(year, "sum")), 1)
        self.assertEqual(on, _tjs("-C", year, "sum", untee=True))
        self.assertEqual(len(_stamped(year, "sum")), 1)
        # A message of more than one line and the exit text after it.
        bad = ("-C", year, "sum", "zzz")
        self.assertEqual(_tjs(*bad), _tjs(*bad, untee=True))

    def test_on_a_terminal(self):
        _, year, _ = _project()
        _set_keep(year, 0)
        off = _on_a_pty("-C", year, "sum")
        _set_keep(year, 20)
        on = _on_a_pty("-C", year, "sum")
        self.assertEqual(on, off)
        self.assertEqual(on, _on_a_pty("-C", year, "sum", untee=True))
        self.assertEqual(on[0], 0, on[1].decode(errors="replace")[-2000:])
        # The log holds what was printed; the terminal's own \r is not in it.
        body = _body(self, (year / "logs" / "sum.log").read_bytes())[2]
        self.assertNotIn("\r", body)
        self.assertEqual(body, on[1].decode("utf-8").replace("\r\n", "\n"))
        bad = ("-C", year, "sum", "zzz")
        self.assertEqual(_on_a_pty(*bad), _on_a_pty(*bad, untee=True))


class TestAChildProcessIsInTheLog(unittest.TestCase):
    """TAXJSON_DISPATCH=subprocess: `tjs fees-sum` and `tjs
    find-missing-history` run their tool as a child process, which prints
    to the terminal itself."""

    def test_piped(self):
        _, year, _ = _project()
        inproc = _tjs("-C", year, "fees-sum", merged=True)
        rc, out, _ = _tjs("-C", year, "fees-sum", merged=True,
                          TAXJSON_DISPATCH="subprocess")
        self.assertEqual((rc, out), inproc[:2])
        self.assertTrue(out.strip())
        _, code, body = _body(self, (year / "logs" / "fees-sum.log").read_bytes())
        self.assertEqual((code, body), (rc, out.decode("utf-8")))

    def test_as_with_no_tee(self):
        _, year, _ = _project()
        args = ("-C", year, "find-missing-history")
        for run in (_tjs, _on_a_pty):
            with self.subTest(run=run.__name__):
                self.assertEqual(run(*args, TAXJSON_DISPATCH="subprocess"),
                                 run(*args, untee=True, TAXJSON_DISPATCH="subprocess"))

    @unittest.skipUnless(os.path.exists("/dev/full"), "no /dev/full")
    def test_a_full_device_is_said_and_exits_2(self):
        # The child's output, copied to a stdout with no room: the
        # command's `cannot write the output` error and exit 2, as when
        # the child wrote there itself.
        _, year, _ = _project()
        args = ("-C", year, "find-missing-history")
        for untee in (True, False):
            with self.subTest(untee=untee), open("/dev/full", "wb") as full:
                rc, _, err = _tjs(*args, untee=untee, stdout=full,
                                  TAXJSON_DISPATCH="subprocess")
                self.assertEqual(rc, 2, err.decode()[-2000:])
                self.assertIn(b"error: cannot write the output: No space left on device", err)
        _, code, body = _body(self, (year / "logs" / "find-missing-history.log").read_bytes())
        self.assertEqual(code, 2)
        self.assertIn("error: cannot write the output: No space left", body)

    def test_a_closed_reader_exits_141(self):
        _, year, _ = _project()
        for dispatch in ("subprocess", None):
            for untee in (True, False):
                with self.subTest(dispatch=dispatch, untee=untee):
                    r, w = os.pipe()
                    os.close(r)
                    try:
                        rc, _, err = _tjs("-C", year, "fees-sum", untee=untee, stdout=w,
                                          TAXJSON_DISPATCH=dispatch)
                    finally:
                        os.close(w)
                    self.assertEqual(rc, 141, err.decode()[-2000:])
        self.assertEqual(_body(self, (year / "logs" / "fees-sum.log").read_bytes())[1], 141)

    def test_a_tjs_a_child_starts_writes_no_log(self):
        # A logged tjs's child (here: this test's) names itself for its
        # own children: `tjs amt`'s estimate, run as a child, writes none.
        _, year, _ = _project()
        rc, _, err = _tjs("-C", year, "amt", TAXJSON_DISPATCH="subprocess",
                          TAXJSON_LOG_PARENT=str(os.getpid()))
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertFalse((year / "logs").exists())

    def test_on_a_terminal_the_child_sees_one(self):
        # 60 columns: the child wraps its lines at the terminal's width,
        # as it does writing to the terminal itself (logs off).
        _, year, _ = _project()
        args = ("-C", year, "find-missing-history")
        _set_keep(year, 0)
        off = _on_a_pty(*args, cols=60, TAXJSON_DISPATCH="subprocess")
        _set_keep(year, 20)
        on = _on_a_pty(*args, cols=60, TAXJSON_DISPATCH="subprocess")
        self.assertEqual(on, off)
        body = _body(self, (year / "logs" / "find-missing-history.log").read_bytes())[2]
        self.assertEqual(body, on[1].decode("utf-8").replace("\r\n", "\n"))
        # The same as the tool in this process prints; not what it prints
        # piped (unwrapped).
        self.assertEqual(on, _on_a_pty(*args, cols=60))
        piped = _tjs(*args, merged=True)[1]
        self.assertNotEqual(on[1].replace(b"\r\n", b"\n"), piped)


class TestPruning(unittest.TestCase):

    def test_the_newest_log_keep_logs_stay(self):
        _, year, tmp = _project()
        _set_keep(year, 3)
        logs = year / "logs"
        logs.mkdir(mode=0o700)
        header = "# taxjson log: tjs sum\n# taxjson 0.0.0, started earlier\n"
        ended = header + "# exit 0 after 0.1 s\n"
        (logs / "sum.log.20000101-000000").write_text(ended)     # taxjson's, the oldest
        # (its header gives no start, so its file's time orders it: made
        # now, it sorted after a run of the same second on a fast machine)
        os.utime(logs / "sum.log.20000101-000000", (946684800, 946684800))
        (logs / "sum.log.20000101-000001").write_text("my notes\n")
        (logs / "fees-sum.log.20000101-000000").write_text(header)
        (logs / "sum.log.notes").write_text(header)
        outside = tmp / "kept.txt"
        outside.write_text(header)
        (logs / "sum.log.19990101-000000").symlink_to(outside)
        runs = []
        for _ in range(4):
            before = set(_stamped(year, "sum"))
            rc, out, err = _tjs("-C", year, "sum")
            self.assertEqual(rc, 0, err.decode()[-2000:])
            new = set(_stamped(year, "sum")) - before
            self.assertEqual(len(new), 1, new)
            runs += new
        self.assertEqual(
            set(_stamped(year, "sum")),
            {*runs[-3:], "sum.log.20000101-000001", "sum.log.19990101-000000"})
        self.assertEqual((logs / "sum.log").read_bytes(), (logs / runs[-1]).read_bytes())
        self.assertTrue((logs / "fees-sum.log.20000101-000000").is_file())
        self.assertTrue((logs / "sum.log.notes").is_file())
        self.assertEqual(outside.read_text(), header)

    def test_a_run_still_going_keeps_its_log(self):
        # By the start each header gives, never a log with no last line
        # (a run still going) unless nothing was written to it for a day.
        _, year, _ = _project()
        _set_keep(year, 1)
        logs = year / "logs"
        logs.mkdir(mode=0o700)

        def log(name, started, ended, days_old=0):
            f = logs / name
            f.write_text(f"# taxjson log: tjs sum\n# taxjson 0.0.0, started {started}\n"
                         "==> a line\n" + ("# exit 0 after 0.1 s\n" if ended else ""))
            if days_old:
                then = time.time() - days_old * 86400
                os.utime(f, (then, then))
        log("sum.log.20000101-000000", "2000-01-01 00:00:00 +0000", True)
        log("sum.log.20000101-000001", "2000-01-01 00:00:01 +0000", False)
        log("sum.log.20000101-000002", "2000-01-01 00:00:02 +0000", False, days_old=2)
        # (named later than it started: the hour an autumn clock change repeats)
        log("sum.log.20991231-000000", "2000-01-01 01:00:00 -0100", True)
        log("sum.log.20000101-000003", "2099-12-31 00:00:00 +0000", True)
        rc, _, err = _tjs("-C", year, "sum")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        ours = (logs / "sum.log").read_bytes()
        mine = [n for n in _stamped(year, "sum") if (logs / n).read_bytes() == ours]
        self.assertEqual(len(mine), 1)
        self.assertEqual(set(_stamped(year, "sum")),
                         {*mine, "sum.log.20000101-000001", "sum.log.20000101-000003"})

    def test_two_runs_at_once_keep_both_logs(self):
        # The first waits on a full pipe, its log open, while the second
        # runs to its end with log_keep = 1.
        _, year, _ = _project()
        _set_keep(year, 1)
        first = subprocess.Popen(_argv(("-C", year, "tax-logic")), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env=_env())
        try:
            deadline = time.monotonic() + 60
            while not _stamped(year, "tax-logic") and time.monotonic() < deadline:
                time.sleep(0.1)
            started = _stamped(year, "tax-logic")
            self.assertEqual(len(started), 1)
            rc, _, err = _tjs("-C", year, "tax-logic")
            self.assertEqual(rc, 0, err.decode()[-2000:])
            self.assertIsNone(first.poll(), "the first run was to wait on its pipe")
            both = _stamped(year, "tax-logic")
            self.assertEqual(len(both), 2)
            self.assertIn(started[0], both)
        finally:
            out, _ = first.communicate(timeout=_TIMEOUT)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(_stamped(year, "tax-logic"), both)
        line, code, body = _body(self, (year / "logs" / started[0]).read_bytes())
        self.assertEqual((code, body), (0, out.decode("utf-8")))

    def test_a_second_run_in_the_same_second(self):
        _, year, _ = _project()
        frozen = dict(TAXJSON_TEST_CLOCK_AT=str(int(time.time())),
                      PYTHONPATH=os.pathsep.join([str(REPO / "tests" / "_clockshift"),
                                                  str(REPO / "src")]))
        for _ in range(2):
            rc, _, err = _tjs("-C", year, "sum", **frozen)
            self.assertEqual(rc, 0, err.decode()[-2000:])
        first, second = _stamped(year, "sum")
        self.assertEqual(second, first + "-2")

    def test_log_keep_0_writes_and_deletes_nothing(self):
        _, year, _ = _project()
        _set_keep(year, 0)
        rc, _, err = _tjs("-C", year, "sum")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertFalse((year / "logs").exists())
        (year / "logs").mkdir()
        old = year / "logs" / "sum.log.20000101-000000"
        old.write_text("# taxjson log: tjs sum\n")
        _tjs("-C", year, "sum")
        self.assertEqual(sorted(p.name for p in (year / "logs").iterdir()), [old.name])


class TestALogThatCannotBeWritten(unittest.TestCase):
    """One warning on stderr; stdout and the exit code as with no log."""

    _WARNING = b"no log of this command: cannot write logs/ ("

    def _same_but_one_warning(self, year, args, spoil, warning=_WARNING):
        off = _tjs("-C", year, *args, untee=True)
        spoil()
        on = _tjs("-C", year, *args)
        self.assertEqual(on[:2], off[:2])
        lines = on[2].splitlines(keepends=True)
        hits = [i for i, x in enumerate(lines) if warning in x]
        self.assertEqual(len(hits), 1, on[2].decode()[-2000:])
        del lines[hits[0]]
        self.assertEqual(b"".join(lines), off[2])
        return on[2]

    def test_logs_is_a_file(self):
        _, year, _ = _project()
        for args in (("sum",), ("sum", "zzz")):
            with self.subTest(args=args):
                self._same_but_one_warning(
                    year, args, lambda: (year / "logs").write_text("a file\n"))
                self.assertEqual((year / "logs").read_text(), "a file\n")
                (year / "logs").unlink()

    @unittest.skipIf(os.geteuid() == 0, "root writes a read-only folder")
    def test_logs_is_read_only(self):
        _, year, _ = _project()
        (year / "logs").mkdir(mode=0o500)
        try:
            err = self._same_but_one_warning(year, ("sum",), lambda: None)
            self.assertIn(b"(Permission denied)", err)
            self.assertEqual(list((year / "logs").iterdir()), [])
        finally:
            (year / "logs").chmod(0o700)

    def test_logs_is_a_link(self):
        # Out of the project or into it (work/: `tjs clean` would list the
        # logs as files of yours there).
        _, year, tmp = _project()
        elsewhere = tmp / "elsewhere"
        elsewhere.mkdir()
        for target in (elsewhere, year / "work"):
            with self.subTest(target=target.name):
                before = sorted(target.iterdir())
                err = self._same_but_one_warning(
                    year, ("sum",), lambda: (year / "logs").symlink_to(target))
                self.assertIn(b"(a link, to ", err)
                self.assertEqual(sorted(target.iterdir()), before)
                (year / "logs").unlink()

    def test_an_argument_that_is_not_utf8(self):
        # The header names the command line as typed, its bytes kept.
        _, year, tmp = _project()
        odd = tmp / os.fsdecode(b"d\xffmo")
        year.parent.rename(odd)
        year = odd / year.name
        for args in (("years",), ("sum", os.fsdecode(b"ab\xffc"))):
            with self.subTest(args=args):
                shutil.rmtree(year / "logs", ignore_errors=True)
                untee = _tjs("-C", year, *args, untee=True)
                self.assertEqual(_tjs("-C", year, *args), untee)
                head = (year / "logs" / f"{args[0]}.log").read_bytes().split(b"\n", 1)[0]
                self.assertIn(b"d\xffmo", head)

    def test_the_latest_copy_not_written(self):
        _, year, _ = _project()
        err = self._same_but_one_warning(
            year, ("sum",), lambda: (year / "logs" / "sum.log").mkdir(parents=True),
            warning=b"warning: logs/sum.log not written (")
        self.assertIn(b"(Is a directory)", err)
        self.assertEqual(_body(self, (year / "logs" / _stamped(year, "sum")[0])
                               .read_bytes())[1], 0)

    def test_a_log_cut_short(self):
        # A file-size limit stops the log after its first bytes: said once,
        # at the end; the command as with no tee.
        import resource
        _, year, _ = _project()

        def small():
            resource.setrlimit(resource.RLIMIT_FSIZE, (400, 400))
        untee = _tjs("-C", year, "sum", untee=True, preexec_fn=small)
        on = _tjs("-C", year, "sum", preexec_fn=small)
        self.assertEqual(on[:2], untee[:2])
        self.assertTrue(len(on[1]) > 400)
        warned = [x for x in on[2].splitlines() if b"is incomplete (" in x]
        self.assertEqual(len(warned), 1, on[2].decode()[-2000:])
        self.assertIn(f"the log logs/{_stamped(year, 'sum')[0]} is incomplete".encode(),
                      warned[0])
        self.assertEqual(on[2].replace(warned[0] + b"\n", b""), untee[2])
        self.assertFalse((year / "logs" / "sum.log").exists())


class TestALinkAtTheLatestLog(unittest.TestCase):

    def test_the_link_is_replaced_never_written_through(self):
        _, year, tmp = _project()
        (year / "logs").mkdir(mode=0o700)
        target = tmp / "target.txt"
        target.write_text("mine\n")
        (year / "logs" / "sum.log").symlink_to(target)
        rc, _, err = _tjs("-C", year, "sum")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertEqual(target.read_text(), "mine\n")
        latest = year / "logs" / "sum.log"
        self.assertFalse(latest.is_symlink())
        self.assertEqual(latest.read_bytes(),
                         (year / "logs" / _stamped(year, "sum")[0]).read_bytes())


class TestCtrlC(unittest.TestCase):

    def test_the_log_ends_as_the_terminal_with_exit_130(self):
        _, year, _ = _project()
        rc, shown = _on_a_pty("-C", year, "run", interrupt_after=b"==> ")
        self.assertEqual(rc, 130, shown.decode(errors="replace")[-2000:])
        text = shown.decode("utf-8").replace("\r\n", "\n")
        _, code, body = _body(self, (year / "logs" / "run.log").read_bytes())
        self.assertEqual((code, body), (130, text))
        self.assertIn("Error: interrupted", body[-300:])
        self.assertFalse(body.endswith("\n\n"), body[-300:])



class _Copied:
    """A log that records each text it copies; `on_copy` runs in the
    middle of a copy (a Ctrl-C arriving then)."""

    def __init__(self, on_copy=None):
        self.got, self.on_copy = [], on_copy

    def add_text(self, text, stream):
        if self.on_copy is not None:
            self.on_copy()
        self.got.append(text)


class _Taking(io.StringIO):
    """A stream whose write is interrupted once it took the text."""

    def write(self, s):
        n = super().write(s)
        os.kill(os.getpid(), signal.SIGINT)
        return n


class TestCtrlCWhileTheLogCopies(unittest.TestCase):
    """A Ctrl-C between the terminal taking a text and the log copying
    it: the copy is whole, then the Ctrl-C goes on (the gate saw the log
    end one blank line short of the terminal)."""

    def setUp(self):
        from taxjson.lib import command_log
        self.cl = command_log
        self.addCleanup(setattr, command_log._S, "log", command_log._S.log)
        self.addCleanup(setattr, command_log._S, "sigint_held", False)
        self.addCleanup(signal.signal, signal.SIGINT, signal.getsignal(signal.SIGINT))
        signal.signal(signal.SIGINT, signal.default_int_handler)

    def _kill(self):
        os.kill(os.getpid(), signal.SIGINT)

    def test_during_the_copy(self):
        self.cl._S.log = log = _Copied(on_copy=self._kill)
        inner = io.StringIO()
        with self.assertRaises(KeyboardInterrupt):
            self.cl._Tee(inner).write("==> Checking\n")
        self.assertEqual(inner.getvalue(), "==> Checking\n")
        self.assertEqual(log.got, ["==> Checking\n"])
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_during_writelines_copy(self):
        self.cl._S.log = log = _Copied(on_copy=self._kill)
        with self.assertRaises(KeyboardInterrupt):
            self.cl._Tee(io.StringIO()).writelines(["a\n", "b\n"])
        self.assertEqual(log.got, ["a\n", "b\n"])

    def test_in_the_streams_write(self):
        self.cl._S.log = log = _Copied()
        with self.assertRaises(KeyboardInterrupt):
            self.cl._Tee(_Taking()).write("Error: x\n")
        self.assertEqual(log.got, ["Error: x\n"])

    def test_the_handler_in_place_gets_it(self):
        # (lib/write_step's own handler: called once, after the copy)
        calls = []
        signal.signal(signal.SIGINT, lambda n, f: calls.append(len(log.got)))
        self.cl._S.log = log = _Copied(on_copy=self._kill)
        self.cl._Tee(io.StringIO()).write("x\n")
        self.assertEqual(calls, [1])
        self.assertEqual(log.got, ["x\n"])
        self.assertFalse(self.cl._S.sigint_held)

    def test_no_log_no_swap(self):
        self.cl._S.log = None
        with mock.patch.object(signal, "signal") as swap:
            self.cl._Tee(io.StringIO()).write("x\n")
        swap.assert_not_called()

def _asking(args, answer, mode, untee=False):
    """`tjs ARGS` reading a terminal (stdin a pseudo-terminal, 100 columns),
    `answer` typed once its question is shown: (exit code, the terminal's
    bytes, stdout, stderr). `mode`: "piped" (stdout and stderr pipes),
    "stderr piped" (stdout the terminal) or "terminal" (all three)."""
    import fcntl
    import struct
    import termios
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    p = subprocess.Popen(
        _argv(args, untee), stdin=slave,
        stdout=subprocess.PIPE if mode == "piped" else slave,
        stderr=slave if mode == "terminal" else subprocess.PIPE,
        env=_env(TAXJSON_WIDTH=None), start_new_session=True)
    os.close(slave)
    out_fd, err_fd = (f.fileno() if f is not None else None for f in (p.stdout, p.stderr))
    got = {fd: b"" for fd in (master, out_fd, err_fd) if fd is not None}
    live, typed, deadline = set(got), False, time.monotonic() + _TIMEOUT
    try:
        while live:
            if time.monotonic() > deadline:
                p.kill()
                raise AssertionError(f"tjs {' '.join(map(str, args))}: timed out")
            if not typed and any(b"  > " in v for v in got.values()):
                os.write(master, answer.encode() + b"\n")
                typed = True
            ready, _, _ = select.select(list(live), [], [], 1)
            for fd in ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:         # EIO: every writer closed the terminal
                    data = b""
                if data:
                    got[fd] += data
                else:
                    live.discard(fd)
            if not ready and p.poll() is not None and typed:
                break
    finally:
        os.close(master)
        for f in (p.stdout, p.stderr):
            if f is not None:
                f.close()
    return (p.wait(timeout=_TIMEOUT), got[master], got.get(out_fd, b""),
            got.get(err_fd, b""))


def _clockless(result):
    """`result` (an exit code, then bytes) with the checklist's paragraph
    on when its verdicts were made ("Checked DATE HH:MM (the oldest
    verdict ...) ... re-checks.", wrapped as the width wraps it) masked:
    two runs a minute apart differ there, and only there."""
    return tuple(re.sub(rb"Checked \d{4}-\d{2}-\d{2} .*?re-checks\.",
                        b"Checked WHEN.", x, flags=re.S)
                 if isinstance(x, bytes) else x for x in result)

class TestAQuestionAndItsAnswer(unittest.TestCase):
    """lib/command_log.ask, as `tjs close-year`, `checklist --walk`, `align
    --write` and `ticker-map --write` ask: the question where input() put
    it, the answer in the log only (a terminal shows what is typed itself)."""

    def test_the_question_goes_where_input_put_it(self):
        # At a terminal input() printed it on stderr, past every proxy;
        # with stdout piped, on stdout.
        _, year, _ = _project()
        args = ("-C", year, "checklist", "--walk")
        for mode in ("piped", "stderr piped", "terminal"):
            with self.subTest(mode=mode):
                untee = _asking(args, "q", mode, untee=True)
                on = _asking(args, "q", mode)
                # (the checklist says when its verdicts were made: two
                # runs can straddle a minute)
                self.assertEqual(_clockless(on), _clockless(untee))
                self.assertIn(b"  > ", on[1] + on[2] + on[3])
                log = (year / "logs" / "checklist.log").read_text(encoding="utf-8")
                self.assertIn("\n  > q\n", log)

    def test_both_are_in_the_log(self):
        from taxjson.lib import command_log
        _, year, _ = _project()
        shown = io.StringIO()
        with mock.patch.dict(os.environ), \
                mock.patch("builtins.input", return_value="y") as typed, \
                contextlib.redirect_stdout(shown), \
                contextlib.redirect_stderr(io.StringIO()):
            os.environ.pop(command_log.PARENT_ENV, None)
            with command_log.console():
                command_log.begin("align", year)
                answer = command_log.ask("add to ticker.map: X? [y/N/q] ")
        self.assertEqual(answer, "y")
        typed.assert_called_once_with("")
        self.assertEqual(shown.getvalue(), "add to ticker.map: X? [y/N/q] ")
        self.assertEqual(_body(self, (year / "logs" / "align.log").read_bytes())[2],
                         "add to ticker.map: X? [y/N/q] y\n")


class TestTheStreamsBeneath(unittest.TestCase):
    """What the tee needs of lib/out and lib/cli_diag."""

    def test_settle_writes_through_the_stream_it_is_given(self):
        from taxjson.lib import out

        class Proxy:
            def __init__(self, inner):
                self._tj_inner, self.seen = inner, []

            def write(self, s):
                self.seen.append(s)
                return self._tj_inner.write(s)
        inner = io.StringIO()
        proxy = Proxy(inner)
        out._owe(proxy)
        out.settle(proxy)
        self.assertEqual((proxy.seen, inner.getvalue()), (["\n"], "\n"))

    def test_the_guard_stays_on_a_tee_over_the_process_stream(self):
        from taxjson.lib import cli_diag, command_log
        saved = sys.stdout, sys.stderr
        tees = command_log._Tee(sys.__stdout__), command_log._Tee(sys.__stderr__)
        guards = mock.patch.dict(cli_diag._GUARDS, clear=True)
        guards.start()
        try:
            sys.stdout, sys.stderr = tees
            with cli_diag._guarded_streams():
                pass
            self.assertIsInstance(sys.stdout, cli_diag._RoomGuard)
            self.assertIs(sys.stdout._tj_inner, tees[0])
        finally:
            sys.stdout, sys.stderr = saved
            guards.stop()


class TestNoLog(unittest.TestCase):

    def test_redact(self):
        _, year, _ = _project()
        rc, _, err = _tjs("-C", year, "redact")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertFalse((year / "logs").exists())

    def test_outside_a_project(self):
        tmp = Path(private_dir("taxjson-logs-none-"))
        rc, _, _ = _tjs("sum", cwd=tmp)
        self.assertNotEqual(rc, 0)
        rc, _, _ = _tjs("-C", tmp, "sum")
        self.assertNotEqual(rc, 0)
        self.assertEqual(list(tmp.iterdir()), [])

    def test_help_version_usage_errors_and_the_years_folder(self):
        top, year, _ = _project()
        for args in (("--help",), ("--version",), ("help",), ("sum", "--help"),
                     ("sum", "--bogus"), ("fx-cash", "--ledger", "v9")):
            with self.subTest(args=args):
                _tjs("-C", year, *args)
                self.assertFalse((year / "logs").exists())
        rc, _, err = _tjs("-C", top, "years")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertFalse((top / "logs").exists())

    def test_a_tjs_a_logged_tjs_starts(self):
        # Its parent's log holds what it shows (`tjs checklist --run`).
        _, year, _ = _project()
        rc, _, err = _tjs("-C", year, "sum", TAXJSON_LOG_PARENT=str(os.getpid()))
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertFalse((year / "logs").exists())


class TestTheBooksStayCurrent(unittest.TestCase):

    def test_logs_and_log_keep_make_no_run_stale(self):
        _, year, _ = _project()
        rc, out, _ = _tjs("-C", year, "run", merged=True)
        self.assertEqual(rc, 0, out.decode()[-2000:])
        self.assertEqual(_body(self, (year / "logs" / "run.log").read_bytes())[2],
                         out.decode("utf-8"))
        _set_keep(year, 5)
        # `tjs sum` refuses books older than their inputs.
        rc, _, err = _tjs("-C", year, "sum")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertNotIn(b"older than the inputs", err)
        # The check itself: an input that moves a figure is seen.
        cfg = year / "taxjson.toml"
        cfg.write_text(re.sub(r'(?m)^tax_date\s*=.*$', 'tax_date = "trade"',
                              cfg.read_text(encoding="utf-8")), encoding="utf-8")
        rc, _, err = _tjs("-C", year, "sum")
        self.assertEqual(rc, 1)
        self.assertIn(b"older than the inputs", err)

    def test_clean_keeps_logs(self):
        _, year, _ = _project()
        _tjs("-C", year, "sum")
        kept = _stamped(year, "sum")
        rc, _, err = _tjs("-C", year, "clean")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        self.assertEqual(_stamped(year, "sum"), kept)
        self.assertEqual(len(_stamped(year, "clean")), 1)

    def test_migrate_to_years_moves_them_into_the_year(self):
        from _support.years_multi_projects import single
        root = single("canada", 2024)
        rc, _, err = _tjs("-C", root, "sanity")
        self.assertTrue((root / "logs" / "sanity.log").is_file(), err.decode()[-2000:])
        rc, out, _ = _tjs("-C", root, "migrate", "--to-years", merged=True)
        self.assertEqual(rc, 0, out.decode()[-2000:])
        self.assertFalse((root / "logs").exists())
        logs = root / "2024" / "logs"
        self.assertTrue((logs / "sanity.log").is_file())
        self.assertEqual(len(_stamped(root / "2024", "migrate")), 1)
        _, code, body = _body(self, (logs / "migrate.log").read_bytes())
        self.assertEqual((code, body), (0, out.decode("utf-8")))
        self.assertNotIn("Left at the top", body)

    def test_a_snapshot_leaves_the_logs_out(self):
        top, year, _ = _project()
        _tjs("-C", year, "sum")
        rc, _, err = _tjs("-C", year, "snapshot")
        self.assertEqual(rc, 0, err.decode()[-2000:])
        copies = list((top / "snapshot").iterdir())
        self.assertEqual(len(copies), 1, copies)
        copy = copies[0] / year.name
        self.assertTrue((copy / "reports").is_dir())
        self.assertFalse((copy / "logs").exists())
        self.assertTrue((year / "logs" / "snapshot.log").is_file())


if __name__ == "__main__":
    unittest.main()
