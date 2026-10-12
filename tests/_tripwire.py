"""Coverage tripwires for engine branches (A2-1596).

A branch the suite must execute carries a marker comment on its first
body line in the source, e.g. ``x += 1  # cov: a2-1596-grant-loss-ref``.
``Tripwire(path, *markers)`` traces only the frames of that one file
(sys.settrace; coverage.py is not a dependency), and ``missed()`` lists
the markers whose line never ran. Lines are found by the marker text,
so the wire survives line drift; a marker that is missing or appears
twice fails loudly.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Dict, List


def marker_line(path: str, marker: str) -> int:
    tag = f"# cov: {marker}"
    hits = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if line.rstrip().endswith(tag):
                hits.append(n)
    if len(hits) != 1:
        raise AssertionError(
            f"tripwire marker {tag!r} found {len(hits)} times in {path}")
    return hits[0]


class Tripwire:
    def __init__(self, path: str, *markers: str):
        self.path = os.path.realpath(path)
        self.lines: Dict[str, int] = {m: marker_line(self.path, m)
                                      for m in markers}
        self.hit: set = set()
        self._seen: Dict[str, bool] = {}
        self._old = None

    def _local(self, frame, event, arg):
        if event == "line":
            self.hit.add(frame.f_lineno)
        return self._local

    def _global(self, frame, event, arg):
        fn = frame.f_code.co_filename
        ok = self._seen.get(fn)
        if ok is None:
            ok = self._seen[fn] = os.path.realpath(fn) == self.path
        return self._local if ok else None

    def __enter__(self):
        self._old = sys.gettrace()
        threading.settrace(self._global)
        sys.settrace(self._global)
        return self

    def __exit__(self, *exc):
        sys.settrace(self._old)
        threading.settrace(self._old)
        return False

    def missed(self) -> List[str]:
        return [m for m, n in self.lines.items() if n not in self.hit]

    def ran(self, marker: str) -> bool:
        return self.lines[marker] in self.hit
