"""GNU-style CLI diagnostics for taxjson bin tools.

Convention (AUDIT-2026-07-ui §1C): diagnostics go to stderr as
``<prog>: warning: ...`` / ``<prog>: error: ...`` / ``<prog>: note: ...``
with a lowercase severity word, where ``<prog>`` is the installed
console-script name (e.g. ``taxjson-merge``). Report content stays on
stdout. Exit codes: 0 = success (including "no data"), 1 = the tool's
finding (mismatch/violation/lint problem), 2 = usage/environment error.

These helpers cover only bin/ CLI diagnostics. Parser-layer warnings in
lib/brokerages/ keep their bare ``warning:`` shape — they feed the
.diag/DIAGNOSTICS machinery in taxjson_run.py, whose marker matcher
accepts both the bare and the prog-prefixed form.
"""

import sys


def warn(prog: str, msg: str) -> None:
    print(f"{prog}: warning: {msg}", file=sys.stderr)


def error(prog: str, msg: str) -> None:
    print(f"{prog}: error: {msg}", file=sys.stderr)


def note(prog: str, msg: str) -> None:
    print(f"{prog}: note: {msg}", file=sys.stderr)

