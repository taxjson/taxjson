"""The taxjson command-line tools.

`python -m taxjson.bin.<tool>` imports this package first, while
sys.argv[0] is still "-m" (Python sets it so while it locates the
module): the owner-only umask the console scripts get from
taxjson.bin._entry is set here for that form too, so every tool's
`__main__` creates its files 0600 / directories 0700 (2026-10 security
review LOW e). An ordinary import (a test, a library user) changes
nothing.
"""
import os as _os
import sys as _sys

if _sys.argv[:1] == ["-m"]:
    # The same rule as _entry.private_umask: never loosens a stricter one.
    _os.umask(_os.umask(0o077) | 0o077)
