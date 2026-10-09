"""Removed commands leave no trace a user could take for a live command.

`quick-start` (replaced by `taxjson checklist`) and `scan` (replaced by
`taxjson tips` and `taxjson ticker-map --suggest`) are named only in
CHANGELOG.md, which records their removal. The code, the docs, the
README, AGENTS.md, KNOWN_ISSUES.md and packages/ name neither: the
patterns below match the commands, not the other scans the project runs
(the PII scan, a broker's listing scan, the CA-SCAN rule ids)."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The places a user reads (CHANGELOG.md sits at the root, outside these).
ROOTS = ("src", "docs", "packages")
FILES = ("README.md", "AGENTS.md", "KNOWN_ISSUES.md")
TEXT = {".py", ".md", ".toml", ".txt", ".html", ".cfg", ".ini", ".json",
        ".sh", ".svg", ".yml", ".yaml", ".rst", ".tt", ".map"}
SKIP_DIRS = {"__pycache__", ".git", "build", "dist", ".eggs"}

REMOVED = re.compile(
    r"quick[-_ ]?start"                       # the command, its module
    r"|\b(?:tjs|taxjson)\s+scan\b"            # a scan command line
    r"|`scan`"                                # the command named alone
    r"|\bscan\s+command\b|\bscan's\b"         # prose about the command
    r"|--(?:done|skip|only|undo)[ =]scan\b",  # its former checklist id
    re.IGNORECASE)


def _files():
    for name in FILES:
        p = REPO / name
        if p.is_file():
            yield p
    for root in ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in TEXT:
                continue
            rel = p.relative_to(REPO).parts
            if SKIP_DIRS & set(rel) or any(
                    part.endswith(".egg-info") for part in rel):
                continue
            yield p


class TestRemovedCommandsLeaveNoTrace(unittest.TestCase):

    def test_no_file_names_quick_start_or_the_scan_command(self):
        hits = []
        for p in _files():
            text = p.read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                m = REMOVED.search(line)
                if m:
                    hits.append(f"{p.relative_to(REPO)}:{n}: {m.group(0)!r}")
        self.assertEqual(hits, [], "a removed command is named — only "
                                   "CHANGELOG.md records its removal")

    def test_the_pattern_matches_the_commands_only(self):
        for named in ("taxjson quick-start", "tjs quick-start",
                      "lib/quick_start.py", "the Quickstart guide",
                      "run `tjs scan`", "taxjson scan --json",
                      "`scan` was renamed", "the scan command",
                      "scan's MAP-GAP", "checklist --done scan"):
            self.assertRegex(named, REMOVED, named)
        for other in ("the PII scan", "CA-SCAN-01", "scan_questrade",
                      "SIBLING_SCAN_LIMIT", "the folder scan, one line",
                      "a pre-scan over the rows", "hard-to-scan lines",
                      '{"scan": "tips"}', "taxjson tips"):
            self.assertNotRegex(other, REMOVED, other)

    def test_the_changelog_records_both_removals(self):
        # Under Unreleased now, under its version heading once released.
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"`quick-start` command is removed; "
                               r"`taxjson checklist` replaces it")
        self.assertRegex(text, r"`scan` command is removed; `taxjson "
                               r"tips` replaces it")


if __name__ == "__main__":
    unittest.main()
