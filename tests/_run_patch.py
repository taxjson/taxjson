"""Patch a name of the run's code wherever it is bound.

`from x import y` gives every module its own binding, so patching one module's
copy leaves the others calling the real thing and a test can pass without its
fake ever running. patch_run replaces the name in every module of the run's code
that holds the same object, and fails when none does; and in every taxjson.lib
module holding that object, where a lib reader imports a helper the run's code
imports too (lib/in_kind's in_kind_unbooked, the checklist's transfers step:
backlog TJ-3526).

    with patch_run("run_to_file", fake): ...
    with patch_run("load_config", return_value=cfg) as soft: ...

Values `run_state` holds are one binding: `mock.patch.object(run_state, ...)`.
"""
import sys
from unittest import mock

_RUN_MODULE = "taxjson.bin.taxjson_run"
_PACKAGES = ("taxjson.cli.", "taxjson.pipeline.")
_LIB = "taxjson.lib."


def _holders(name):
    __import__(_RUN_MODULE)
    mods = [m for n, m in sorted(sys.modules.items())
            if m is not None and (n == _RUN_MODULE or n.startswith(_PACKAGES))]
    found = [m for m in mods if name in vars(m)]
    if not found:
        raise AttributeError(f"no module of the run's code binds {name!r}")
    # One object, held by the module defining it and the ones importing it. Two
    # modules' own names that happen to match: a fake would reach only some.
    if len({id(vars(m)[name]) for m in found}) > 1:
        raise AttributeError(f"modules bind {name!r} to different objects: "
                             + ", ".join(m.__name__ for m in found))
    obj = vars(found[0])[name]
    return found + [m for n, m in sorted(sys.modules.items())
                    if m is not None and n.startswith(_LIB) and m not in found
                    and vars(m).get(name) is obj]


class patch_run:
    """A context manager (or start/stop) like mock.patch.object: `new`, or a
    MagicMock built from the keyword arguments."""

    def __init__(self, name, new=mock.DEFAULT, **kwargs):
        self.name, self.new, self.kwargs = name, new, kwargs
        self._patches = []

    def start(self):
        new = mock.MagicMock(**self.kwargs) if self.new is mock.DEFAULT else self.new
        self._patches = [mock.patch.object(m, self.name, new)
                         for m in _holders(self.name)]
        for p in self._patches:
            p.start()
        return new

    def stop(self):
        for p in reversed(self._patches):
            p.stop()
        self._patches = []

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
