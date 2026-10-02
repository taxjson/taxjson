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
import sys
from typing import Any, Callable

from taxjson.lib.cli_diag import guard_main, run_top_level


def private_umask() -> None:
    """Add 077 to the process umask (never loosens a stricter one):
    new files are 0600, new directories 0700."""
    os.umask(os.umask(0o077) | 0o077)


def __getattr__(name: str) -> Callable[[], Any]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise AttributeError(name)

    def run() -> Any:
        private_umask()
        main = importlib.import_module(f"taxjson.bin.{name}").main
        # An unreadable input path (missing, a directory, not UTF-8,
        # not JSON) is one `<prog>: error:` line with exit 2 for every
        # console script, never a traceback (audit S070-23 / S079-10).
        prog = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] \
            else f"taxjson-{name}"
        # Ctrl-C and a closed stdout pipe: one line / a quiet exit, not
        # a traceback (re-audit A2-1425, A2-0785).
        return run_top_level(prog, guard_main(prog)(main))

    run.__name__ = name
    return run
