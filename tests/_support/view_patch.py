"""Shared test helpers: the patch that puts a given raw reader in place of model/result's view classes, and no view built meanwhile.

Shared from: test_model_result_export
"""
import collections
import contextlib
import sys
from unittest import mock


@contextlib.contextmanager
def raw_views(modules, names, raw):
    """`raw` (RawRow) in place of each view class `names` in `modules`, and
    no view built meanwhile: a view built is read by a module the patch
    missed, which then reads it on both sides of a comparison (code moved
    out of a patched module did, backlog TJ-3522). The failure names the
    module that built it."""
    built = collections.Counter()
    with contextlib.ExitStack() as stack:
        for n in names:
            init = getattr(modules[0], n).__init__

            def counting(self, *args, _init=init, _n=n, **kwargs):
                built[(_n, sys._getframe(1).f_globals.get("__name__"))] += 1
                _init(self, *args, **kwargs)
            stack.enter_context(mock.patch.object(getattr(modules[0], n), "__init__", counting))
        for mod in modules:
            for n in names:
                stack.enter_context(mock.patch.object(mod, n, raw))
        yield
    if built:
        raise AssertionError("the raw run built views (view, module): "
                             + ", ".join(f"{k} x{v}" for k, v in sorted(built.items())))
