"""Command logs: what a `tjs` command showed, kept in its project's logs/
(docs/commands.md, "Command logs"; backlog TJ-5366).

A command run in a project (the folder -C names holds taxjson.toml; in a
multi-year project that is a year folder) writes
logs/<command>.log.<YYYYmmdd-HHMMSS> as it runs (`-2`, `-3` ... for a second
run in the same second) and, when it ends, copies it to logs/<command>.log,
the latest run (lib/safe_write: a link at the name is replaced, never
written through). Each holds the command line, taxjson's version and the
start time, then stdout and stderr in the order printed, then the exit code
and the duration. `[settings] log_keep` (default 20) is how many timestamped
logs of each command are kept; 0 writes none. Nothing is written for
`tjs help`, `--help`, `--version`, a usage error, a command outside a
project, `tjs redact` (its id map is shown only, never written) or a release
command; nor by a `tjs` another logged `tjs` starts (`tjs checklist --run`:
what it shows is in the parent's log).

The terminal output is not changed (`console`): the streams the process
prints to keep every byte, their file descriptors, TTY detection, width,
colours and buffering. A log that cannot be written is one warning on
stderr; the command and its exit code are as they would be.

- `console()`: cli/main.main's context, once per process at the top: a tee
  (`_Tee`) at the bottom of sys.stdout and sys.stderr, below lib/cli_diag's
  full-device guard and lib/out's settling proxy. It hands every call to the
  stream it wraps and copies the text written to the open log. It is
  transparent to lib/out.real_stream (`_tj_inner`), so whether a stream is
  the process's console reads as before. When the command ends it closes
  the log with the exit code, and the text the interpreter prints after it
  (a SystemExit message, a traceback) is added as printed.
- `begin(cmd, folder, commands)`: cli/main._main, once per command of a
  chain, after its arguments parse; the chain's next command closes the
  one before it (exit 0). A command another runs in this process (a nested
  `main`: `console(nested=True)` counts it) is part of the outer one's log.
- `child_streams(stdout, stderr)`: pipeline/dispatch.run_cmd's real child
  processes (an interactive election stage, TAXJSON_DISPATCH=subprocess):
  while a log is open, the standard streams the child would inherit are a
  pseudo-terminal where the real one is a terminal (its window size, output
  post-processing off: the child's own bytes) or a pipe where it is not, one
  for both when they are the same destination; a thread copies each chunk to
  the real stream and the log. stdin is the child's own (prompts read the
  terminal), and the child is in tjs's process group (Ctrl-C reaches it).
  The real stream's first refusal is the parent's when the child is done:
  a closed reader raises BrokenPipeError (exit 141), a full device is
  lib/cli_diag's guard's (`cannot write the output`, exit 2), as when the
  child wrote there itself.
- `ask(prompt)`: input(prompt), the question printed where input() prints
  it and given to the log, the answer added to the log only (the terminal
  echoes it).
- `moved(old, new)`: `tjs migrate --to-years` moved logs/ into the year: the
  open log follows.

A log never changes the command: any error writing it (an OSError, or any
other) stops the log, never the command, and is said in one warning.
"""
from __future__ import annotations

import contextlib
import errno
import os
import re
import signal
import stat
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import (Any, Callable, Dict, Iterator, List, Mapping, Optional,
                    Tuple, TypeVar)

__all__ = ["LOGS", "KEEP_DEFAULT", "HEADER", "UNLOGGED", "PARENT_ENV",
           "console", "begin", "child_streams", "log_keep", "log_names",
           "ask", "moved"]

# The folder in the project, and the keep when [settings] log_keep is unset.
LOGS = "logs"
KEEP_DEFAULT = 20
# A log's first bytes: how the pruning knows a file is one it wrote.
HEADER = "# taxjson log: "
# Commands that write no log: the help page; redact (its old -> new id map
# is shown here only, never written: bin/taxjson_redact); the release verbs
# (they read a git checkout, never a project).
UNLOGGED = frozenset({"help", "redact", "channels", "deploy", "promote",
                      "parser-check"})
# Set while a log is open to this process's id: a `tjs` it starts (the
# checklist's `--run` commands, the subprocess dispatch) writes no log of
# its own when its parent is the one named.
PARENT_ENV = "TAXJSON_LOG_PARENT"
# The setting.
KEY = "log_keep"
# logs/.gitignore, written when logs/ is made: git ignores what is in it.
GITIGNORE = "# taxjson's command logs hold your figures: never committed.\n*\n"
_STAMP_RE = r"(\d{8}-\d{6})(?:-([1-9]\d*))?"
# The second line of a log: its start, with the zone's offset.
_STARTED_RE = re.compile(rb"\n# taxjson [^\n]*, started (\d{4}-\d\d-\d\d "
                         rb"\d\d:\d\d:\d\d [+-]\d{4})\n")
# A log's last line once its command ended.
_FOOTER_RE = re.compile(rb"\n# exit -?\d+ after \d+\.\d s\n\Z")
# A log with no footer this long after its last write: a command that was
# killed, not one still running.
_STALE_SECONDS = 24 * 3600

_T = TypeVar("_T")


def _try(fn: Callable[[], _T]) -> Tuple[Optional[_T], Optional[Exception]]:
    """(fn()'s value, None), or (None, the error it raised), whatever the
    error: writing a log stops the log, never the command, which goes on
    as it would with no log (one warning says it)."""
    try:
        return fn(), None
    except Exception as e:      # noqa: BLE001 (a log never stops the command)
        return None, e


def log_names(cmd: str) -> "re.Pattern[str]":
    """The timestamped log names of `cmd`: <cmd>.log.<stamp>[-N]."""
    return re.compile(rf"{re.escape(cmd)}\.log\.{_STAMP_RE}")


def log_keep(cfg_path: Path) -> int:
    """[settings] log_keep of the taxjson.toml at `cfg_path`: a whole
    number 0 or more, else (unset, not a number, a file that does not
    read) KEEP_DEFAULT. A bad value is lib/config_check's to name."""
    from taxjson.project.toml_io import read_toml
    try:
        doc = read_toml(cfg_path)
    except (OSError, ValueError):       # unreadable, not UTF-8, bad TOML
        return KEEP_DEFAULT
    st = doc.get("settings") if isinstance(doc, dict) else None
    v = st.get(KEY) if isinstance(st, dict) else None
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    return KEEP_DEFAULT


# ------------------------------------------------------------ the log

class _Log:
    """One command's log: logs/<cmd>.log.<stamp>, written as it goes."""

    def __init__(self, folder: Path, cmd: str, name: str, f: Any,
                 keep: int, t0: float) -> None:
        self.folder, self.cmd, self.name, self.f = folder, cmd, name, f
        self.keep, self.t0 = keep, t0
        self.last = b"\n"
        # Why the log stopped (an OSError's text), said once at the end.
        self.failed: Optional[str] = None
        self.lock = threading.Lock()

    @classmethod
    def open(cls, root: Path, cmd: str, keep: int) -> "_Log":
        folder = (root / LOGS).absolute()     # (a chdir later: moved())
        if folder.is_symlink():
            # Never written through a link, inside the project or out of it
            # (work/: `tjs clean` would name the logs as files of yours).
            raise OSError(errno.EPERM, f"a link, to {os.readlink(folder)}")
        try:
            folder.mkdir(mode=0o700)
            # A project made before logs/ (its .gitignore does not name
            # it) never commits them either: they hold its figures.
            with contextlib.suppress(OSError):
                with open(folder / ".gitignore", "x",
                          encoding="utf-8") as g:
                    g.write(GITIGNORE)
        except FileExistsError:
            pass
        t0 = time.time()
        head = _encoded(_header(t0))
        base = f"{cmd}.log.{time.strftime('%Y%m%d-%H%M%S', time.localtime(t0))}"
        import functools
        opener = functools.partial(os.open, mode=0o600)
        for n in range(1, 1000):
            name = base if n == 1 else f"{base}-{n}"
            try:
                # "x": O_CREAT | O_EXCL, so nothing at the name (a file, a
                # symlink) is ever opened or followed.
                f = open(folder / name, "xb+", buffering=0, opener=opener)
                break
            except FileExistsError:
                continue
        else:
            raise OSError(errno.EEXIST, "no free log name")
        log = cls(folder, cmd, name, f, keep, t0)
        log.add(head)
        if log.failed:
            with contextlib.suppress(OSError, ValueError):
                f.close()
            with contextlib.suppress(OSError):
                os.unlink(folder / name)
            raise OSError(errno.EIO, log.failed)
        return log

    def add(self, data: bytes) -> None:
        if not data or self.failed:
            return
        with self.lock:
            try:
                self.f.write(data)
                self.last = data[-1:]
            except (OSError, ValueError) as e:
                self.failed = _reason(e)

    def add_text(self, text: Any, stream: Any) -> None:
        """`text` (a str, or what print() writes for it) as `stream` would
        encode it."""
        _v, e = _try(lambda: self.add(_as_bytes(text, stream)))
        if e is not None:
            self.fail(e)

    def fail(self, e: BaseException) -> None:
        """The log stops here (said once, at the end)."""
        if not self.failed:
            self.failed = _reason(e)

    def close(self, code: int, tail: str) -> Optional[str]:
        """The footer, <cmd>.log, the pruning: the warning to show, if any."""
        if tail:
            self.add_text(tail, sys.stderr)
        secs = time.time() - self.t0
        self.add((("" if self.last == b"\n" else "\n")
                  + f"# exit {code} after {secs:.1f} s\n").encode("utf-8"))
        data = b""
        if not self.failed:
            try:
                self.f.seek(0)
                data = self.f.read()
            except (OSError, ValueError) as e:
                self.failed = _reason(e)
        with contextlib.suppress(OSError, ValueError):
            self.f.close()
        if self.failed:
            return f"the log {LOGS}/{self.name} is incomplete ({self.failed})"
        from taxjson.lib.safe_write import write_atomic
        latest = f"{self.cmd}.log"
        _v, err = _try(lambda: write_atomic(self.folder / latest, data))
        if err is not None:
            return f"{LOGS}/{latest} not written ({_reason(err)})"
        with contextlib.suppress(Exception):
            self.prune()
        return None

    def prune(self) -> None:
        """The command's timestamped logs past `keep`, oldest start first:
        only a name log_names matches, a regular file of this user that
        starts with HEADER. Never this run's file, nor the log of a run
        still going: a file goes only when its run started before this one
        and ended (its footer), or when nothing was written to it for a day
        (a command that was killed). By the start the header gives, with
        its zone's offset (the hour an autumn clock change repeats sorts
        as it ran), the name's -N after it."""
        pat = log_names(self.cmd)
        mine = pat.fullmatch(self.name)
        own = (float(int(self.t0)), int(mine[2] or 1) if mine else 1)
        found: List[Tuple[Tuple[float, int], str, bool]] = []
        uid = os.getuid() if hasattr(os, "getuid") else None
        now = time.time()
        try:
            with os.scandir(self.folder) as it:
                names = [e.name for e in it]
        except OSError:
            return
        for name in names:
            m = pat.fullmatch(name)
            if m is None or name == self.name:
                continue
            p = self.folder / name
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or (
                    uid is not None and st.st_uid != uid):
                continue
            seen = _read_ends(p)
            if seen is None:
                continue
            started, ended = seen
            key = (started if started is not None else st.st_mtime,
                   int(m[2] or 1))
            gone = ((key < own and ended)
                    or now - st.st_mtime > _STALE_SECONDS)
            found.append((key, name, gone))
        found.sort(reverse=True)
        for _key, name, gone in found[max(self.keep - 1, 0):]:
            if gone:
                with contextlib.suppress(OSError):
                    os.unlink(self.folder / name)


def _read_ends(p: Path) -> Optional[Tuple[Optional[float], bool]]:
    """(the start its header gives, as a time; whether it has its footer)
    of a log taxjson wrote, or None for a file that is not one (no HEADER
    first, unreadable, a link)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(p, flags)
    except OSError:
        return None
    try:
        head = os.read(fd, 4096)
        if not head.startswith(HEADER.encode("utf-8")):
            return None
        size = os.fstat(fd).st_size
        os.lseek(fd, max(size - 128, 0), os.SEEK_SET)
        tail = os.read(fd, 128)
    except OSError:
        return None
    finally:
        os.close(fd)
    started: Optional[float] = None
    m = _STARTED_RE.search(head)
    if m is not None:
        from datetime import datetime
        try:
            started = datetime.strptime(m[1].decode("ascii"),
                                        "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            started = None
    return started, _FOOTER_RE.search(tail) is not None


def _as_bytes(text: Any, stream: Any) -> bytes:
    enc = getattr(stream, "encoding", None) or "utf-8"
    errors = getattr(stream, "errors", None) or "strict"
    try:
        return str(text).encode(enc, errors)
    except (LookupError, UnicodeError):
        return _encoded(str(text))


def _encoded(text: str) -> bytes:
    """`text` as UTF-8, an undecodable byte of argv (surrogateescape) as
    the byte it was, anything else that cannot be encoded escaped."""
    try:
        return text.encode("utf-8", "surrogateescape")
    except UnicodeError:
        return text.encode("utf-8", "backslashreplace")


def _reason(e: BaseException) -> str:
    return (getattr(e, "strerror", None) or str(e) or type(e).__name__)


def _header(t0: float) -> str:
    import shlex
    prog = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if prog not in ("tjs", "taxjson"):
        prog = "tjs"
    from taxjson.lib.requires import running_version
    when = time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(t0))
    return (f"{HEADER}{shlex.join([prog, *sys.argv[1:]])}\n"
            f"# taxjson {running_version() or 'unknown'}, started {when}\n")


# ------------------------------------------------------------ the state

class _State:
    def __init__(self) -> None:
        self.active = False         # inside console()
        self.depth = 0              # nested `main`s inside it (console(nested=True))
        self.log: Optional[_Log] = None
        self.sigint_held = False    # a Ctrl-C held by a tee's copy (_held_sigint)


_S = _State()


def _warn(cmd: str, text: str) -> None:
    """The one `Warning:` line of a command: its log, never the command,
    failed."""
    from taxjson.lib.out import emit
    with contextlib.suppress(Exception):
        emit("warning", text, prog=f"tjs {cmd}")


def _rolled_notes() -> None:
    """lib/markets' rolled-up notes, which the process prints at exit,
    printed now: on an exit with no text nothing is printed between the
    two places, so the terminal's bytes are the same and the log has them."""
    mk = sys.modules.get("taxjson.lib.markets")
    flush = getattr(mk, "flush_notes", None)
    if callable(flush) and getattr(mk, "_ROLLED", None):
        with contextlib.suppress(Exception):
            flush()


def _finish(code: int, tail: str = "", *, last: bool = False) -> None:
    log = _S.log
    if log is None:
        return
    if last and not tail:
        _rolled_notes()
    _S.log = None
    if os.environ.get(PARENT_ENV) == str(os.getpid()):
        os.environ.pop(PARENT_ENV, None)
    warning, e = _try(lambda: log.close(code, tail))
    if e is not None:
        warning = f"the log {LOGS}/{log.name} is incomplete ({_reason(e)})"
    if warning:
        _warn(log.cmd, warning)


def _exit_of(code: Any) -> Tuple[int, str]:
    """(the exit code, the text the interpreter prints) of SystemExit(code)."""
    if code is None:
        return 0, ""
    if isinstance(code, int):
        return int(code), ""
    return 1, f"{code}\n"


def _canonical(cmd: str, commands: Optional[Mapping[str, Any]]) -> str:
    """The name an alias stands for: the first name of its subparser."""
    if not commands or cmd not in commands:
        return cmd
    sp = commands[cmd]
    return next((n for n, p in commands.items() if p is sp), cmd)


def _child_of_logged() -> bool:
    ppid = os.getppid() if hasattr(os, "getppid") else None
    return ppid is not None and os.environ.get(PARENT_ENV) == str(ppid)


def begin(cmd: Optional[str], folder: Any,
          commands: Optional[Mapping[str, Any]] = None) -> None:
    """Start the log of `cmd`, run in `folder` (a chain's next command
    closes the one before, exit 0). Writes nothing outside console(), for
    a nested command, an UNLOGGED one, a folder with no taxjson.toml, a
    log_keep of 0 or a logged `tjs`'s child (whose own children are its
    parent's too: it names itself in PARENT_ENV). A log it cannot start,
    for any reason: one warning, no log, the command as it would be."""
    if not _S.active or _S.depth:
        return
    _finish(0)
    name = _canonical(cmd or "", commands)
    log, e = _try(lambda: _start(name, folder))
    if e is not None:
        _warn(name, f"no log of this command: cannot write {LOGS}/ "
                    f"({_reason(e)})")
    _S.log = log


def _start(name: str, folder: Any) -> Optional[_Log]:
    if _child_of_logged():
        # (its own children are its parent's too)
        os.environ[PARENT_ENV] = str(os.getpid())
        return None
    if not name or name in UNLOGGED:
        return None
    root = Path(folder or ".")
    cfg = root / "taxjson.toml"
    try:
        if not cfg.is_file():
            return None
    except OSError:
        return None
    keep = log_keep(cfg)
    if keep == 0:
        return None
    log = _Log.open(root, name, keep)
    os.environ[PARENT_ENV] = str(os.getpid())
    return log


def ask(prompt: str = "", stream: Any = None) -> str:
    """input(prompt) for a question a command asks, the same bytes where
    input() put them, and the command's log given the question and the
    answer typed (a terminal echoes that itself). Where input() reads a
    terminal (_input_tty) it prints the question straight to the process's
    stderr (stdout once readline is loaded), past every proxy; elsewhere
    through sys.stdout; `stream`: through that stream instead (ticker-map's
    --json: stderr). EOFError and KeyboardInterrupt as input() raises them.
    Never for a password."""
    for s in (sys.stderr, sys.stdout):          # (as input() flushes them)
        with contextlib.suppress(Exception):
            s.flush()
    out: Any
    if prompt and stream is not None:
        stream.write(prompt)
        stream.flush()
        out = stream
    elif prompt and _input_tty():
        out = (sys.__stdout__ if "readline" in sys.modules
               else sys.__stderr__)
        with contextlib.suppress(OSError, ValueError, AttributeError):
            out.write(prompt)
            out.flush()
        log = _S.log
        if log is not None:
            log.add_text(prompt, out)
    else:
        out = sys.stdout
        if prompt:
            out.write(prompt)           # (the tee gives it to the log)
    answer = input("")
    log = _S.log
    if log is not None:
        log.add_text(f"{answer}\n", out)
    return answer


def _input_tty() -> bool:
    """input()'s own test for reading a terminal: sys.stdin and sys.stdout
    are file descriptors 0 and 1, both terminals."""
    try:
        return (sys.stdin.fileno() == 0 and os.isatty(0)
                and sys.stdout.fileno() == 1 and os.isatty(1))
    except (AttributeError, ValueError, OSError):
        return False


def moved(old: Path, new: Path) -> None:
    """The folder `old` was renamed to `new` (`tjs migrate --to-years`
    moves logs/ into the year): the open log, there, follows it (its open
    file did already)."""
    log = _S.log
    if log is not None and Path(old).absolute() == log.folder:
        log.folder = Path(new).absolute()


# ------------------------------------------------------------ the tee

_MAIN = threading.main_thread()


def _held_sigint(signum: int, frame: Any) -> None:
    """SIGINT while a tee copies what the terminal took to the log: held
    until the copy is whole (_released_sigint)."""
    _S.sigint_held = True


def _released_sigint(prev: Any) -> None:
    """The handler `prev` back; a Ctrl-C held meanwhile goes to it now."""
    signal.signal(signal.SIGINT, prev if prev is not None else signal.SIG_DFL)
    if _S.sigint_held:
        _S.sigint_held = False
        if callable(prev):
            prev(signal.SIGINT, None)


class _Tee:
    """sys.stdout or sys.stderr at the bottom of the stack: every call is
    the wrapped stream's (same object, file descriptor, isatty, encoding,
    buffering); the text written is copied to the open log after the
    stream took it. `_tj_inner` is what lib/out.real_stream reads, so the
    tee is invisible to it."""

    def __init__(self, inner: Any) -> None:
        self._tj_inner = inner

    def write(self, s: Any) -> Any:
        texts = [s] if isinstance(s, str) and s else []
        main = threading.current_thread() is _MAIN
        try:
            n = self._tj_inner.write(s)
        except KeyboardInterrupt:
            # (the stream took the text, or holds it until the flush the
            # interrupt's own message makes: the log has it too)
            self._logged(texts)
            raise
        if not texts or _S.log is None:
            return n
        # A Ctrl-C between the stream taking the text and the log copying
        # it lost the copy: the log then ended unlike the terminal. It
        # waits for the copy, then goes to the handler that was in place
        # (Python's, or lib/write_step's). The handler is swapped inline,
        # with no Python call before it: one would take the Ctrl-C first.
        prev = None
        if main:
            try:
                prev = signal.signal(signal.SIGINT, _held_sigint)
            except (ValueError, TypeError, OSError):
                prev = None
        try:
            self._logged(texts)
        finally:
            if prev is not None:
                _released_sigint(prev)
        return n

    def writelines(self, lines: Any) -> None:
        lines = list(lines)
        texts = [x for x in lines if isinstance(x, str) and x]
        main = threading.current_thread() is _MAIN
        try:
            self._tj_inner.writelines(lines)
        except KeyboardInterrupt:
            self._logged(texts)
            raise
        if not texts or _S.log is None:
            return
        prev = None
        if main:
            try:
                prev = signal.signal(signal.SIGINT, _held_sigint)
            except (ValueError, TypeError, OSError):
                prev = None
        try:
            self._logged(texts)
        finally:
            if prev is not None:
                _released_sigint(prev)

    def _logged(self, texts: List[Any]) -> None:
        log = _S.log
        if log is not None:
            for x in texts:
                log.add_text(x, self._tj_inner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tj_inner, name)


def _tee_in(stream: Any) -> Optional[_Tee]:
    """The tee in `stream`'s chain of proxies (`_tj_inner`), if any: a
    command run after another in this process (a test's) reuses it."""
    for _ in range(32):
        if isinstance(stream, _Tee):
            return stream
        stream = getattr(stream, "__dict__", {}).get("_tj_inner")
        if stream is None:
            return None
    return None


@contextlib.contextmanager
def console(nested: bool = False) -> Iterator[None]:
    """The top of the process (cli/main.main): the tees on sys.stdout and
    sys.stderr while the command runs; its log closed at the end with the
    exit code and the text the interpreter prints after it. A tee left
    under a proxy that outlives the command (lib/cli_diag's guard, kept for
    the writes at exit) stays, passing everything through. `nested`: a
    `main` inside the command (amt's estimate): counted, so what it runs is
    the outer command's log."""
    if nested:
        _S.depth += 1
        try:
            yield
        finally:
            _S.depth -= 1
        return
    owned = []
    for name in ("stdout", "stderr"):
        s = getattr(sys, name)
        if s is None or _tee_in(s) is not None:
            continue
        tee = _Tee(s)
        setattr(sys, name, tee)
        owned.append((name, s, tee))
    _S.active, _S.depth = True, 0
    code, tail = 0, ""
    try:
        yield
    except SystemExit as e:
        code, tail = _exit_of(e.code)
        raise
    except BaseException as e:
        code, tail = 1, "".join(traceback.format_exception(e))
        raise
    finally:
        _S.active = False
        try:
            _finish(code, tail, last=True)
        finally:
            for name, s, tee in owned:
                if getattr(sys, name) is tee:
                    setattr(sys, name, s)


# ------------------------------------------------------------ children

class _Channel:
    """One stream a child writes to: a pty or a pipe, and the thread that
    copies what arrives to the real file descriptor and the log. `error`:
    the real stream's first refusal, after which nothing more is shown
    (the log goes on), the parent's own when the child is done (_said)."""

    def __init__(self, real: int, read: int, write: int, pty: bool,
                 log: _Log) -> None:
        self.real, self.read, self.write, self.pty = real, read, write, pty
        self.log = log
        self.error: Optional[OSError] = None
        self.thread = threading.Thread(target=self._copy, daemon=True,
                                       name="taxjson-log-copy")

    def _copy(self) -> None:
        while True:
            try:
                data = os.read(self.read, 65536)
            except InterruptedError:
                continue
            except OSError:
                break                   # a pty whose slave closed: EIO
            if not data:
                break
            if self.error is None:
                try:
                    _write_all(self.real, data)
                except OSError as e:
                    self.error = e
                    if isinstance(e, BrokenPipeError) and not self.pty:
                        # The reader went away: the child gets EPIPE, as
                        # it would have writing there itself.
                        with contextlib.suppress(OSError):
                            os.close(self.read)
                        self.read = -1
                        self.log.add(data)
                        return
            self.log.add(data)

    def winsize(self) -> None:
        if self.pty:
            _copy_winsize(self.real, self.write)

    def close_write(self) -> None:
        if self.write >= 0:
            with contextlib.suppress(OSError):
                os.close(self.write)
            self.write = -1

    def close(self) -> None:
        """Once the child is done and the write end closed: the copier
        drains what is left and stops. One a grandchild still writes
        through goes on copying (a daemon thread), its end left open."""
        if self.thread.ident is not None:
            self.thread.join(timeout=_JOIN_SECONDS)
            if self.thread.is_alive():
                return
        if self.read >= 0:
            with contextlib.suppress(OSError):
                os.close(self.read)
            self.read = -1


# How long the end of a child's call waits for its copier to drain.
_JOIN_SECONDS = 5


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except InterruptedError:
            continue
        view = view[n:]


def _copy_winsize(real: int, slave: int) -> None:
    try:
        import fcntl
        import termios
        size = fcntl.ioctl(real, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
    except (ImportError, OSError, AttributeError):
        pass


def _channel(real: int, log: _Log) -> Optional[_Channel]:
    """A pty for a terminal (None where there is none: left inherited), a
    pipe for anything else."""
    if os.isatty(real):
        try:
            import termios
            master, slave = os.openpty()
        except (ImportError, OSError, AttributeError):
            return None
        try:
            attrs = termios.tcgetattr(slave)
            attrs[1] &= ~termios.OPOST
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except (OSError, termios.error):
            pass
        _copy_winsize(real, slave)
        return _Channel(real, master, slave, True, log)
    r, w = os.pipe()
    return _Channel(real, r, w, False, log)


@contextlib.contextmanager
def child_streams(stdout: bool = False, stderr: bool = False
                  ) -> Iterator[Dict[str, int]]:
    """subprocess's `stdout` / `stderr` for a child that would inherit fd 1
    / fd 2 (the ones asked for), while a log is open: {} otherwise, and
    for a stream that is closed or a terminal with no pty here (or any
    error setting them up: the child inherits them, the log is
    incomplete). Once the child is done, a refusal of the real stream is
    this process's own (_said)."""
    log = _S.log
    if log is None or not (stdout or stderr):
        yield {}
        return
    channels: List[_Channel] = []
    kwargs: Dict[str, int] = {}
    old: Any = None

    def setup() -> None:
        nonlocal old
        groups: Dict[Tuple[int, int], List[int]] = {}
        for fd, on in ((1, stdout), (2, stderr)):
            if not on:
                continue
            try:
                st = os.fstat(fd)
            except OSError:
                continue
            groups.setdefault((st.st_dev, st.st_ino), []).append(fd)
        for fds in groups.values():
            ch = _channel(fds[0], log)
            if ch is None:
                continue
            channels.append(ch)
            for fd in fds:
                kwargs["stdout" if fd == 1 else "stderr"] = ch.write
        if any(ch.pty for ch in channels):
            old = _follow_winch(channels)
        for ch in channels:
            ch.thread.start()
    _v, e = _try(setup)
    if e is not None:
        log.fail(e)
        _closed(channels, old)
        channels, old = [], None
        kwargs.clear()
    try:
        yield kwargs
    finally:
        _closed(channels, old)
    _said(channels)


def _closed(channels: List[_Channel], old: Any) -> None:
    for ch in channels:
        ch.close_write()
    for ch in channels:
        ch.close()
    if old is not None:
        import signal
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGWINCH, old)


def _said(channels: List[_Channel]) -> None:
    """The first refusal of a real stream a child's output was copied to,
    as if this process had written there (the child would have, with no
    log): a closed reader raises BrokenPipeError (lib/cli_diag: exit 141);
    a full device is the guard's (`cannot write the output`, exit 2); any
    other error is raised."""
    for ch in channels:
        e = ch.error
        if e is None:
            continue
        if isinstance(e, BrokenPipeError):
            raise e
        from taxjson.lib.cli_diag import stream_refused
        if not stream_refused("stdout" if ch.real == 1 else "stderr", e):
            raise e


def _follow_winch(channels: List[_Channel]) -> Any:
    """The terminal's new size on each pty when the window changes (the
    main thread only): the previous handler, to put back."""
    import signal
    if (not hasattr(signal, "SIGWINCH")
            or threading.current_thread() is not threading.main_thread()):
        return None
    prev = signal.getsignal(signal.SIGWINCH)

    def on_winch(signum: int, frame: Any) -> None:
        for ch in channels:
            ch.winsize()
        if callable(prev):
            prev(signum, frame)
    try:
        signal.signal(signal.SIGWINCH, on_winch)
    except (ValueError, OSError):
        return None
    return prev if prev is not None else signal.SIG_DFL
