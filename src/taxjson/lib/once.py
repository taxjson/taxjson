"""What a command has already warned about, for the warnings `lib` says once.

A warning said once per process was lost when a command another one runs in this
process said it first and its stderr was dropped: the mark stayed, the words did
not. The sets live here so that `pipeline/run_state.py`, which `lib` can't import,
can scope all of them: a nested command starts knowing what its caller said, and
what it says itself is forgotten when it returns, unless its caller shows it
(`adopt_nested`). So a warning is said once in a chain of commands, until the next
`tjs run`. A tool whose stderr becomes a work/ file's .diag says its warnings as
it would in a process of its own (`own_process`), and each `tjs` command a
process runs from the top (`cli/main.main`, not nested) starts afresh.
"""
import contextlib
from typing import Any, Iterator, List, Set

_SETS: List[Set[Any]] = []
# What the last nested command said, in `_SETS`'s order, for `adopt_nested`.
_last_nested: List[Set[Any]] = []


def said_set() -> Set[Any]:
    """A module's set of what it has said once."""
    said: Set[Any] = set()
    _SETS.append(said)
    return said


def forget_all() -> None:
    """A new run says its warnings again."""
    for said in _SETS:
        said.clear()


@contextlib.contextmanager
def nested() -> Iterator[None]:
    """A nested command's marks last until it returns. In place: each module
    holds its set by name. A set first made inside (its module was imported
    there) starts empty after."""
    global _last_nested
    saved = [set(said) for said in _SETS]
    try:
        yield
    finally:
        _last_nested = [set(said) for said in _SETS]
        for i, said in enumerate(_SETS):
            said.clear()
            if i < len(saved):
                said.update(saved[i])


@contextlib.contextmanager
def own_process() -> Iterator[None]:
    """A tool run as a process of its own (pipeline/dispatch `run_cmd`, its
    stderr captured for a work/ file's .diag): it starts knowing nothing, as the
    same tool run out of process does, so its .diag holds every line it says, and
    what it says is forgotten when it returns (the words went to the file, not to
    a person). In place, as `nested`; the last nested command's marks
    (`adopt_nested`) are left as they were."""
    saved = [set(said) for said in _SETS]
    for said in _SETS:
        said.clear()
    try:
        yield
    finally:
        for i, said in enumerate(_SETS):
            said.clear()
            if i < len(saved):
                said.update(saved[i])


def adopt_nested() -> None:
    """The caller showed the last nested command's stderr: what it said is said."""
    global _last_nested
    for said, nested_said in zip(_SETS, _last_nested):
        said.update(nested_said)
    _last_nested = []
