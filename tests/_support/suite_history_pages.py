"""Shared test helpers: the history pages and the renamed commands.

docs/troubleshooting.md and docs/upgrading.md keep each entry in the
words it was written with: one fixed in a release before a command was
renamed names the command as that release printed it.

Shared from: test_knowledge_pack, test_elections_command_and_file_renamed
"""
import re

# The release that renamed each old command name (cli/parser.RENAMED_COMMANDS).
RENAMED_IN = {"elect": (3, 0, 0)}
HISTORY = ("docs/troubleshooting.md", "docs/upgrading.md")
_VER = r"v(\d+)\.(\d+)\.(\d+)"


def history_releases(path, lines):
    """Per line of a history page, the release its entry was fixed in: a
    troubleshooting entry's Fixed in, an upgrading line's `### vX.Y.Z`
    section or its own "fixed in vX.Y.Z"; None anywhere else."""
    out = [None] * len(lines)
    if path not in HISTORY:
        return out
    if path.endswith("troubleshooting.md"):
        starts = [i for i, ln in enumerate(lines) if ln.startswith("### ")]
        for a, b in zip(starts, starts[1:] + [len(lines)]):
            fixed = next((m for ln in lines[a:b] for m in [re.match(
                r"^- \*\*Fixed in:\*\*.*?`" + _VER + "`", ln)] if m), None)
            for i in range(a, b):
                out[i] = tuple(map(int, fixed.groups())) if fixed else None
        return out
    rel = None
    for i, ln in enumerate(lines):
        m = re.match(r"^### " + _VER + r"\s*$", ln)
        if m or ln.startswith(("## ", "### ")):
            rel = tuple(map(int, m.groups())) if m else None
        own = re.search(r"\bfixed in " + _VER + r"\b", ln)
        out[i] = tuple(map(int, own.groups())) if own else rel
    return out


def renamed_ok(name, release):
    """An old command `name` may stand in a line of an entry fixed in
    `release` (history_releases): one before its rename."""
    old = RENAMED_IN.get(name)
    return bool(old) and release is not None and release < old
