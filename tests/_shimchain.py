"""The chain loader the taxjson test shims share (tests/_clockshift,
tests/_opentrace: each a sitecustomize.py on PYTHONPATH).

Python runs the first sitecustomize on sys.path only. `load_next(here)` runs
the next one (the system's: Ubuntu ships one; another taxjson shim) before
the shim's own code, and records each folder it has run in, so two shims on
the path load each other and the system's once, in either order, without
recursing (TJ-2772). One copy for both (TJ-2790). A shim loads this file by
its path: the tests folder is not on a child's sys.path."""
import importlib.util
import os
import sys


def load_next(here: str) -> None:
    chain = sys.__dict__.setdefault("_taxjson_sitecustomize_chain", set())
    chain.add(os.path.dirname(os.path.abspath(here)))
    for p in sys.path:
        folder = os.path.abspath(p or ".")
        cand = os.path.join(folder, "sitecustomize.py")
        if folder not in chain and os.path.isfile(cand):
            chain.add(folder)
            spec = importlib.util.spec_from_file_location(f"_sitecustomize_{len(chain)}", cand)
            try:
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
            except (ImportError, OSError):   # the system's hook is not the tests'
                pass
            return
