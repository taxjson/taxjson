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

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

try:                                    # POSIX; Windows saves unlocked
    import fcntl
except ImportError:                     # pragma: no cover
    fcntl = None


def save_json_cache(path, data: Dict[str, Any], *, merge: bool = False,
                    prog: str = "taxjson", label: str = "",
                    **dump_kw) -> bool:
    """Write `data` (a dict) to `path` atomically; True when saved. A
    failure is a stderr warning (a cache write never stops a run)."""
    path = Path(path)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path) + ".lock", "a") as lock:
            if fcntl is not None:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                except OSError:
                    pass
            if merge:
                try:
                    cur = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cur = None
                if isinstance(cur, dict):
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
        print(f"{prog}: warning: could not write {label}{path}: {exc}",
              file=sys.stderr)
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
