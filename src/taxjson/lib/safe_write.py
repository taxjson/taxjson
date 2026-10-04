"""Write-then-rename that never writes through a planted symlink.

Every writer that stages a file under a fixed sibling name
(`<out>.part`, `<out>.migrate.part`, ...) and renames it into place goes
through here (pre-release security review M1). A plain
`open(tmp, "w")` follows a symlink planted at that predictable name and
overwrites whatever it points at — a file outside the project too.

- The temp file is created FRESH: an existing entry at its name (a
  stale temp from a killed run, or a planted symlink) is unlinked first,
  then os.open(O_CREAT | O_EXCL | O_NOFOLLOW, 0o600) refuses anything
  that reappears there. Owner-only from the first byte (tax data).
- The data is flushed and fsync'd before the rename, so a crash never
  publishes a short file under the final name.
- The rename (os.replace) replaces the final directory ENTRY: when the
  final path is a symlink, the LINK is replaced by the new regular file
  and its target is never opened or changed. That is the policy for
  generated files (work/, reports/, exports). User-maintained files a
  person may symlink on purpose (ticker.map, taxjson.toml) are checked
  by their writer first (`link_outside`) and refused when the link
  leaves the project.
"""
from __future__ import annotations

import contextlib
import os
import stat
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
    """`with atomic_open(out) as f: f.write(...)` — written to
    `<out><suffix>` (created fresh, see open_new), fsync'd, then renamed
    over `out` when the block ends cleanly. On an exception the temp is
    removed and `out` keeps its previous contents."""
    final = Path(final)
    tmp = temp_name(final, suffix)
    f = open_new(tmp, binary=binary, encoding=encoding, newline=newline)
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
