"""How taxjson starts a child Python process: `python_module_argv`, the
argv that runs `python -m <module>` without the current directory on
sys.path (backlog TJ-0632; every taxjson child Python process is built
here). Running a tool command in process, the dispatch of
`python -m taxjson.bin.<tool>` commands, is pipeline/dispatch.py's
(`run_cmd`): it imports a bin tool by name, which lib may not.
"""

import sys
from typing import List, Sequence


def python_module_argv(module: str, args: Sequence[str] = ()) -> List[str]:
    """The argv that runs `python -m <module> ARGS` in a child process
    WITHOUT the current directory on sys.path. A plain `python -m`
    imports from the cwd first, so a json.py or csv.py planted in a
    project folder ran inside `tjs run`'s child processes (2026-10
    security review H1). `-P` keeps it off. Every taxjson child Python
    process is launched through here."""
    return [sys.executable, "-P", "-m", module, *map(str, args)]
