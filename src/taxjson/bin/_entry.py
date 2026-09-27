"""Console-script trampolines: `taxjson-<tool>` → `<module>.main()` with
an owner-only umask first.

Every taxjson tool reads or writes tax data (statements, books, reports,
form exports). `taxjson` itself sets the umask in its own main(); the
stand-alone `taxjson-*` entry points route through here so a direct
invocation gets the same guarantee without each module having to
remember it. pyproject.toml maps `taxjson-foo = "taxjson.bin._entry:mod"`
and `from taxjson.bin._entry import mod` resolves via the module-level
__getattr__ below (PEP 562) to a wrapper around taxjson.bin.mod:main.
"""
from __future__ import annotations

import importlib
import os
import re
from typing import Any, Callable


def private_umask() -> None:
    """Add 077 to the process umask (never loosens a stricter one):
    new files are 0600, new directories 0700."""
    os.umask(os.umask(0o077) | 0o077)


def __getattr__(name: str) -> Callable[[], Any]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise AttributeError(name)

    def run() -> Any:
        private_umask()
        return importlib.import_module(f"taxjson.bin.{name}").main()

    run.__name__ = name
    return run
