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

from taxjson.lib.cli_diag import console_prog, guard_main, tolerant_stdout


def private_umask() -> None:
    """Add 077 to the process umask (never loosens a stricter one):
    new files are 0600, new directories 0700."""
    os.umask(os.umask(0o077) | 0o077)


def __getattr__(name: str) -> Callable[[], Any]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise AttributeError(name)

    def run() -> Any:
        private_umask()
        # A report's '—' under an ASCII locale is replaced, not a
        # UnicodeEncodeError (re-audit A2-1427).
        tolerant_stdout()
        main = importlib.import_module(f"taxjson.bin.{name}").main
        # An unreadable input path (missing, a directory, not UTF-8,
        # not JSON) is one `<prog>: error:` line with exit 2 for every
        # console script, never a traceback (audit S070-23 / S079-10);
        # a closed pipe is a quiet exit (A2-1426).
        prog = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] \
            else console_prog(name)
        return guard_main(prog)(main)()

    run.__name__ = name
    return run


if __name__ == "__main__":
    # `python -m taxjson.bin._entry <module> ARGS...`: the subprocess form
    # lib/dispatch uses, so a tool run out of process gets the same
    # umask, one-line errors and pipe handling as its console script
    # (`python -m taxjson.bin.<module>` skipped all three — A2-0161).
    if len(sys.argv) < 2:
        sys.exit("usage: python -m taxjson.bin._entry <module> [ARGS...]")
    _mod = sys.argv[1]
    sys.argv = [console_prog(_mod)] + sys.argv[2:]
    sys.exit(__getattr__(_mod)())     # as the console script does
