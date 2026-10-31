"""Atomic, locked saves of the shared price/rate caches in $HOME.

The owner keeps several projects (one per tax year) on one $HOME, and
their runs share ~/.crypto_price_cache.json, ~/.currency_price_cache.json
and the price-chain cache. Each save used ONE fixed `<cache>.part` temp
name: two concurrent runs wrote into the same temp file, one rename
failed ("No such file or directory") and a reader could catch the live
cache half-written and silently treat it as empty (re-audit A2-0233).

Now every save writes its own temp file (mkstemp in the cache's folder)
and renames it into place under an exclusive lock on `<cache>.lock`;
with ``merge`` the entries already on disk are read under that lock and
kept unless this run has its own value for them, so one run never drops
the prices another run just saved.
"""
from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

def save_json_cache(path, data: Dict[str, Any], *, merge: bool = False,
                    update: Optional[Callable[[Any], Dict[str, Any]]] = None,
                    prog: str = "tjs", label: str = "",
                    **dump_kw) -> bool:
    """Write `data` (a dict) to `path` atomically; True when saved. A
    failure is a stderr warning (a cache write never stops a run).

    `update(current)`: called under the lock with what the file holds
    now (the parsed JSON; None when it is missing or unreadable), it
    returns the dict to write — a read-modify-write no other saver can
    interleave with (backlog TJ-0583: the FX cache wrote back the whole
    copy it had loaded, dropping another run's new series). `data` is
    then unused."""
    path = Path(path)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # safe_write.file_lock: owner-only, never through a symlink (a
        # link planted at `<cache>.lock` — work/.price_cache.json.lock in
        # a project — had its target created or appended to; 2026-10
        # security review, LOW b's sibling).
        from taxjson.lib.safe_write import file_lock
        with file_lock(str(path) + ".lock"):
            if merge or update is not None:
                try:
                    # utf-8-sig: the loaders accept a BOM; a file they
                    # read must not read as unreadable here.
                    cur = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    cur = None
                if update is not None:
                    data = update(cur)
                elif isinstance(cur, dict):
                    cur.update(data)
                    data = cur
            fd, tmp = tempfile.mkstemp(prefix=path.name + ".",
                                       suffix=".part", dir=str(path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, **dump_kw)
            os.replace(tmp, path)
            tmp = None
        return True
    except (OSError, ValueError, TypeError) as exc:
        emit_line(f"{prog}: warning: could not write {label}{path}: {exc}",
                  file=sys.stderr)
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
