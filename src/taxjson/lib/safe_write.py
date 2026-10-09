"""Write-then-rename that never writes through a planted symlink.

Every writer that stages a file under a fixed sibling name
(`<out>.part`, `<out>.migrate.part`, ...) and renames it into place goes
through here (pre-release security review M1). A plain
`open(tmp, "w")` follows a symlink planted at that predictable name and
overwrites whatever it points at — a file outside the project too.

- atomic_open / write_atomic stage each write in a temp file of its OWN:
  a new, uniquely named sibling `<out>.<random><suffix>` (tempfile.mkstemp
  in the final file's folder: O_CREAT | O_EXCL | O_NOFOLLOW, mode 0600),
  owner-only from the first byte (tax data) and never shared. One fixed
  `<out><suffix>` name let two overlapping writers of one file (two
  processes, or a nested write) share it: the first rename published the
  other's unfinished data and the second failed (GitHub issue #9). A
  planted symlink or a stale temp at any name is never opened or reused.
- open_new (a caller that names its own temp file) creates it FRESH: an
  existing entry at its name is unlinked first, then
  os.open(O_CREAT | O_EXCL | O_NOFOLLOW, 0o600) refuses anything that
  reappears there.
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
  (taxjson_run._acquire_run_lock, one `taxjson run` per project), the
  checklist state lock (checklist._StateLock) and the shared price/rate
  caches' lock (json_cache.save_json_cache); file_lock is the general
  form (the fetch plugin's project and Questrade-token locks).
"""
from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path
from typing import IO, Iterator, Optional, Union

_FLAGS = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
          | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
          | getattr(os, "O_BINARY", 0))


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
    fd = os.open(p, _FLAGS, 0o600)
    try:
        if binary:
            return os.fdopen(fd, "wb")
        return os.fdopen(fd, "w", encoding=encoding, newline=newline)
    except BaseException:
        os.close(fd)
        raise


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
    data, and the last rename wins."""
    final = Path(final)
    fd, name = tempfile.mkstemp(prefix=final.name + ".", suffix=suffix,
                                dir=str(final.parent))
    tmp = Path(name)
    try:
        f = (os.fdopen(fd, "wb") if binary else
             os.fdopen(fd, "w", encoding=encoding, newline=newline))
    except BaseException:
        os.close(fd)
        discard(tmp)
        raise
    done = False
    try:
        with f:
            yield f
            f.flush()
            os.fsync(f.fileno())
        publish(tmp, final, keep_mode=keep_mode)
        done = True
    finally:
        if not done:
            discard(tmp)


def write_atomic(final: Union[str, Path], data: Union[str, bytes], *,
                 suffix: str = ".part", encoding: str = "utf-8",
                 newline: Optional[str] = None,
                 keep_mode: bool = False) -> None:
    """Write `data` (text or bytes) to `final` through atomic_open."""
    with atomic_open(final, binary=isinstance(data, bytes), suffix=suffix,
                     encoding=encoding, newline=newline,
                     keep_mode=keep_mode) as f:
        f.write(data)


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
    identical regular backup is reused. An entry at a .bak name that is
    not a regular file (a symlink, dangling or not) is taken, never
    written through; the copy is written fresh, owner-only, via
    write_atomic (pre-release security review M1)."""
    path = Path(path)
    data = path.read_bytes()
    base = path.name + ".bak"
    bak = path.with_name(base)
    n = 1
    while os.path.lexists(str(bak)) and not (
            bak.is_file() and not bak.is_symlink()
            and bak.read_bytes() == data):
        bak = path.with_name(f"{base}{n}")
        n += 1
    if not os.path.lexists(str(bak)):
        write_atomic(bak, data, keep_mode=False)
    return bak


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
    """Rewrite a file the user maintains (ticker.map, missing_history.json)
    the way migrate and format do: a symlink leaving `root` is refused
    (OutsideLinkError, nothing written); a link inside it is followed —
    the link stays and its target is replaced; the previous contents are
    kept by backup_copy (when `backup` and there were any); the new file
    keeps the replaced file's permission bits (keep_mode). Returns the
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
    bak = None
    if backup and path.is_file():
        bak = backup_copy(path)
    write_atomic(path, data, suffix=suffix, keep_mode=True)
    return bak

