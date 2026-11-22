"""Write-then-rename that never writes through a planted symlink.

Every writer that stages a file under a fixed sibling name
(`<out>.part`, `<out>.migrate.part`, ...) and renames it into place goes
through here (pre-release security review M1). A plain
`open(tmp, "w")` follows a symlink planted at that predictable name and
overwrites whatever it points at — a file outside the project too.

- atomic_open / write_atomic stage each write in a temp file of its OWN:
  a new, uniquely named sibling `<out>.<random><suffix>` (an exclusive
  create in the final file's folder: O_CREAT | O_EXCL, which never
  follows a link at the name, mode 0600), owner-only from the first byte
  (tax data) and never shared. One fixed
  `<out><suffix>` name let two overlapping writers of one file (two
  processes, or a nested write) share it: the first rename published the
  other's unfinished data and the second failed (GitHub issue #9). A
  planted symlink or a stale temp at any name is never opened or reused.
  The temp's removal is in place before it exists, so an interrupt
  anywhere leaves none behind.
- open_new (a caller that names its own temp file) creates it FRESH: an
  existing entry at its name is unlinked first, then the exclusive
  create (O_CREAT | O_EXCL, 0o600) refuses anything that reappears
  there.
- Each new file is opened by its file object (_create), which owns the
  descriptor from the moment it exists: an exception closes it once.
- The data is flushed and fsync'd before the rename, so a crash never
  publishes a short file under the final name.
- The rename (os.replace) replaces the final directory ENTRY: when the
  final path is a symlink, the LINK is replaced by the new regular file
  and its target is never opened or changed. That is the policy for
  generated files (work/, reports/, exports). User-maintained files a
  person may symlink on purpose (ticker.map, taxjson.toml) are checked
  by their writer first (`link_outside`) and refused when the link
  leaves the project.
- Unique temps make each WRITE atomic, not a read-modify-write: the last
  rename wins. Writers that read a shared file, change it and write it
  back hold a lock around the whole step — the run lock
  (pipeline/run._acquire_run_lock, one `tjs run` per project), the
  checklist state lock (checklist._StateLock) and the shared price/rate
  caches' lock (json_cache.save_json_cache); file_lock is the general
  form (the fetch plugin's project and Questrade-token locks).
"""
from __future__ import annotations

import contextlib
import errno
import functools
import os
import secrets
import stat
from pathlib import Path
from typing import IO, Iterator, Optional, Union

# Names atomic_open tries before it gives up (each one is random).
_TEMP_TRIES = 100


def _create(path: Union[str, Path], *, binary: bool = False,
            encoding: str = "utf-8", newline: Optional[str] = None) -> IO:
    """Create `path` as a NEW owner-only file, open for writing: the "x"
    mode's O_CREAT | O_EXCL raises FileExistsError for anything at the
    name (a file, a symlink: never followed). The file object opens its
    own descriptor, so an exception anywhere closes it once. os.fdopen
    on a descriptor opened first did not: an interrupt raised as fdopen
    returned dropped the file object, which closed the descriptor, and
    the `except`'s os.close then raised EBADF in the interrupt's place
    ("Bad file descriptor": backlog TJ-4893)."""
    # The opener, owner-only, is a C callable (os.open looked up here),
    # so the descriptor goes from os.open to the file object with no
    # Python code between them: no point an interrupt can be raised at.
    opener = functools.partial(os.open, mode=0o600)
    if binary:
        return open(path, "xb", opener=opener)
    return open(path, "x", encoding=encoding, newline=newline,
                opener=opener)


def open_new(path: Union[str, Path], *, binary: bool = False,
             encoding: str = "utf-8",
             newline: Optional[str] = None) -> IO:
    """Open `path` for writing as a NEW owner-only file. Whatever sits at
    the name (a stale temp, a symlink — never followed) is unlinked
    first; a directory there raises the OSError unlink gives."""
    p = str(path)
    try:
        os.unlink(p)
    except FileNotFoundError:
        pass
    return _create(p, binary=binary, encoding=encoding, newline=newline)


def discard(path: Union[str, Path]) -> None:
    """Remove a temp file left by a failed write (a file or a link —
    never a directory someone put at the name)."""
    p = str(path)
    try:
        st = os.lstat(p)
    except OSError:
        return
    if stat.S_ISDIR(st.st_mode):
        return
    try:
        os.unlink(p)
    except OSError:
        pass


def publish(tmp: Union[str, Path], final: Union[str, Path], *,
            keep_mode: bool = False) -> None:
    """Rename the finished `tmp` over `final`. A symlink at `final` is
    replaced itself (rename never follows it). keep_mode: the new file
    takes the permission bits of a REGULAR file it replaces (a user's
    ticker.map); otherwise it stays owner-only."""
    if keep_mode:
        try:
            st = os.lstat(str(final))
        except OSError:
            st = None
        if st is not None and stat.S_ISREG(st.st_mode):
            try:
                os.chmod(str(tmp), stat.S_IMODE(st.st_mode))
            except OSError:
                pass
    os.replace(str(tmp), str(final))


class ReadOnlyFileError(PermissionError):
    """A user file to rewrite that this user may not write: refused,
    nothing written (refuse_read_only)."""


# The reason a ReadOnlyFileError gives, and its fix.
READ_ONLY = "the file is read-only"
READ_ONLY_FIX = "`chmod u+w {shown}` lets taxjson rewrite it"


def refuse_read_only(path: Union[str, Path]) -> None:
    """Raise ReadOnlyFileError when `path` is a regular file this user
    may not write: one made read-only (`chmod a-w`) to keep it as it
    is — a filed year's taxjson.toml. The rename that replaces a file
    ignores the file's own mode, and keep_mode put the 0444 back, so it
    was rewritten without a word (backlog TJ-0107); an editor asks
    first. A missing file, a symlink (its writer resolves it first) and
    a file this user can write pass."""
    p = str(path)
    try:
        st = os.lstat(p)
    except OSError:
        return
    if stat.S_ISREG(st.st_mode) and not os.access(p, os.W_OK):
        # The chmod names the file from where the command was run: the
        # bare file name was wrong with -C from elsewhere (backlog
        # TJ-0219).
        raise ReadOnlyFileError(
            errno.EACCES,
            f"{READ_ONLY} ({READ_ONLY_FIX.format(shown=_shown_here(p))})",
            p)


def _shown_here(path: Union[str, Path]) -> str:
    """`path` relative to the folder the command was run in when it is
    inside it, else absolute."""
    try:
        p = Path(path).resolve()
        here = Path.cwd().resolve()
    except (OSError, RuntimeError):
        return str(path)
    try:
        return str(p.relative_to(here))
    except ValueError:
        return str(p)


def temp_name(final: Union[str, Path], suffix: str = ".part") -> Path:
    final = Path(final)
    return final.with_name(final.name + suffix)


@contextlib.contextmanager
def atomic_open(final: Union[str, Path], *, binary: bool = False,
                suffix: str = ".part", encoding: str = "utf-8",
                newline: Optional[str] = None,
                keep_mode: bool = False) -> Iterator[IO]:
    """`with atomic_open(out) as f: f.write(...)` — written to a new
    owner-only temp file of this writer's own beside `out`
    (`<out>.<random><suffix>`), fsync'd, then renamed over `out` when the
    block ends cleanly. On an exception the temp is removed and `out`
    keeps its previous contents. Overlapping writers of one `out` never
    see each other's temp file: each publishes only its own complete
    data, and the last rename wins. keep_mode (a file the user keeps:
    see publish) also refuses to replace one this user may not write
    (refuse_read_only: ReadOnlyFileError, nothing written)."""
    final = Path(final)
    if keep_mode:
        refuse_read_only(final)
    # The temp's removal is in place before the temp exists: its name is
    # chosen first and it is created inside the `try`. tempfile.mkstemp
    # returned a file the `try` did not cover yet, so an interrupt there
    # left `<out>.<random>.part` behind (backlog TJ-4893).
    made: Optional[Path] = None         # the temp, removed unless renamed
    f: Optional[IO] = None
    done = False
    try:
        for _ in range(_TEMP_TRIES):
            tmp = final.with_name(
                f"{final.name}.{secrets.token_hex(4)}{suffix}")
            made = tmp
            try:
                f = _create(tmp, binary=binary, encoding=encoding,
                            newline=newline)
                break
            except FileExistsError:
                made = None             # not this writer's: never removed
        else:
            raise FileExistsError(errno.EEXIST,
                                  "no free temporary file name", str(final))
        try:
            yield f
        except BaseException as e:
            _name_full_disk(e, final)
            raise
        try:
            with f:
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            _name_full_disk(e, final)
            raise
        publish(tmp, final, keep_mode=keep_mode)
        done = True
    finally:
        if not done:
            # The block failed (or the temp's open, its rename): its
            # exception is the error. Closing the temp can fail too (a
            # full disk, a file-size limit) — the temp is removed, so
            # that second error must not replace the first: it hid a
            # failed stage behind a bare "[Errno 27] File too large"
            # (backlog TJ-0106). A closed file closes again as a no-op.
            if f is not None:
                with contextlib.suppress(OSError):
                    f.close()
            if made is not None:
                discard(made)


def _name_full_disk(exc: BaseException, final: Path) -> None:
    """A write a full disk (or a quota, a file-size limit) refused raises
    an OSError that names no file: it is given the final file's name, so
    its message says which file could not be written (backlog TJ-0106).
    Any other exception is left as it is."""
    if (isinstance(exc, OSError) and not exc.filename
            and exc.errno in (errno.ENOSPC, errno.EFBIG,
                              getattr(errno, "EDQUOT", errno.ENOSPC))):
        exc.filename = str(final)


def write_atomic(final: Union[str, Path], data: Union[str, bytes], *,
                 suffix: str = ".part", encoding: str = "utf-8",
                 newline: Optional[str] = None,
                 keep_mode: bool = False) -> None:
    """Write `data` (text or bytes) to `final` through atomic_open."""
    with atomic_open(final, binary=isinstance(data, bytes), suffix=suffix,
                     encoding=encoding, newline=newline,
                     keep_mode=keep_mode) as f:
        f.write(data)


def holds(final: Union[str, Path], data: Union[str, bytes], *,
          encoding: str = "utf-8") -> bool:
    """True when `final` is a regular file (not a link) whose bytes are
    `data` (text encoded with `encoding`): write_changed's test, for a
    writer that words its own errors (pipeline/tools `_write_work_stamp`).
    Text a write translated the newlines of (Windows) never matches, so it
    is written again."""
    raw = data if isinstance(data, bytes) else data.encode(encoding)
    return _same_regular_file(Path(final), raw)


def write_changed(final: Union[str, Path], data: Union[str, bytes], *,
                  encoding: str = "utf-8") -> bool:
    """write_atomic, unless `final` is a regular file holding `data`
    already: a run that changed nothing writes nothing (the stage table's
    rule, for the run's own bookkeeping). True when it wrote."""
    if holds(final, data, encoding=encoding):
        return False
    write_atomic(final, data, encoding=encoding)
    return True


def link_outside(path: Union[str, Path],
                 root: Union[str, Path]) -> Optional[str]:
    """The link text when `path` is a symlink whose target resolves
    outside `root` (or cannot be resolved); None otherwise."""
    p = Path(path)
    if not p.is_symlink():
        return None
    try:
        target = os.readlink(str(p))
    except OSError:
        target = "?"
    try:
        real = p.resolve(strict=True)
        real.relative_to(Path(root).resolve())
    except (OSError, RuntimeError, ValueError):
        return target
    return None


def backup_copy(path: Union[str, Path]) -> Path:
    """Keep the current contents of `path` beside it before it is
    replaced, and return the backup's path: `<name>.bak`, or the next
    free `<name>.bakN` — an earlier backup is never overwritten (a second
    --force used to replace the only copy of the first version), and an
    identical regular backup is reused. Each name is taken with an
    exclusive create (O_CREAT | O_EXCL, owner-only: _create), so
    choosing the name and reserving it are one step: two overlapping
    callers both found `<name>.bak` free and one version was lost
    (audit 2026-10-24 D06). An entry at a .bak name that is not a
    regular file (a symlink, dangling or not) makes that create fail
    and is never written through (pre-release security review M1). The
    copy is complete and fsync'd when this returns; one a crash left
    short is never reused (its contents differ) and never overwritten."""
    path = Path(path)
    data = path.read_bytes()
    base = path.name + ".bak"
    n = 0
    while True:
        bak = path.with_name(base if n == 0 else f"{base}{n}")
        n += 1
        try:
            f = _create(bak, binary=True)
        except FileExistsError:
            if _same_regular_file(bak, data):
                return bak
            continue
        try:
            _fill(f, data)
        except BaseException:
            discard(bak)
            raise
        return bak


def _same_regular_file(path: Path, data: bytes) -> bool:
    """True when `path` is a regular file (not a link) holding `data`."""
    try:
        if not stat.S_ISREG(os.lstat(str(path)).st_mode):
            return False
        return path.read_bytes() == data
    except OSError:
        return False


def _fill(f: IO, data: bytes) -> None:
    """Write `data` to the just-created file `f`, fsync it and close it."""
    with f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


class LockLinkError(OSError):
    """A symlink at a lock file's name that file_lock cannot replace."""


@contextlib.contextmanager
def file_lock(lock_path: Union[str, Path], *,
              on_wait=None) -> Iterator[None]:
    """`with file_lock(p):` — hold an exclusive advisory lock (POSIX
    flock) on the lock file `p` for the block, so a read-modify-write
    (read a file, change it, write it back) or a read-refresh-save of a
    rotating token cannot interleave with another process's or thread's
    (unique temp files make each WRITE atomic, not the whole step). The
    lock file holds no data; it is created owner-only and never through
    a symlink (O_NOFOLLOW). A symlink at the lock's name is replaced by
    a lock file of its own (the link removed, never its target): it used
    to make the open fail and the block run unlocked, so a planted or
    leftover link silently turned the lock off. When the link cannot be
    replaced, LockLinkError is raised instead of running unlocked.
    `on_wait()` is called once when another holder makes this one wait.
    Where flock is unavailable (Windows, a file system without locks) or
    the lock file cannot be opened for another reason, the block runs
    unlocked, as before."""
    try:
        import fcntl
    except ImportError:                             # pragma: no cover
        fcntl = None
    fd = None
    if fcntl is not None:
        flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        for attempt in (1, 2):
            try:
                fd = os.open(str(lock_path), flags, 0o600)
                break
            except OSError:
                fd = None
                if not os.path.islink(str(lock_path)):
                    break               # not a link: run unlocked
                if attempt == 2:
                    raise LockLinkError(
                        f"{Path(lock_path).name} is a symlink and could "
                        f"not be replaced by a lock file — remove it "
                        f"({lock_path}) and try again")
                try:
                    os.unlink(str(lock_path))
                except FileNotFoundError:
                    pass
                except OSError as e:
                    raise LockLinkError(
                        f"{Path(lock_path).name} is a symlink and could "
                        f"not be removed ({e.strerror or e}) — remove it "
                        f"({lock_path}) and try again") from e
    if fd is not None:
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if on_wait is not None:
                    on_wait()
                fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            os.close(fd)                            # no locking here
            fd = None
        except BaseException:
            os.close(fd)
            raise
    try:
        yield
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


class OutsideLinkError(ValueError):
    """A user file to rewrite is a symlink leaving the project."""


def write_user_file(path: Union[str, Path], data: Union[str, bytes],
                    root: Union[str, Path], *, suffix: str = ".part",
                    backup: bool = True) -> Optional[Path]:
    """Rewrite a file the user maintains (ticker.map, a .tt file)
    the way migrate and format do: a symlink leaving `root` is refused
    (OutsideLinkError, nothing written); a link inside it is followed —
    the link stays and its target is replaced; the previous contents are
    kept by backup_copy (when `backup` and there were any); the new file
    keeps the replaced file's permission bits (keep_mode); a file this
    user may not write is refused (ReadOnlyFileError). Returns the
    backup's path, or None."""
    path = Path(path)
    target = link_outside(path, root)
    if target is not None:
        raise OutsideLinkError(
            f"{path.name} is a symlink to {target}, outside the project — "
            f"never written through: replace the link with a copy, or "
            f"run in the folder that holds the real file")
    if path.is_symlink():
        path = path.resolve()
    # A file made read-only is refused before a backup is taken
    # (ReadOnlyFileError, nothing written; backlog TJ-0107).
    refuse_read_only(path)
    bak = None
    if backup and path.is_file():
        bak = backup_copy(path)
    write_atomic(path, data, suffix=suffix, keep_mode=True)
    return bak

