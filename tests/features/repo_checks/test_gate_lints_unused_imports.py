"""Backlog fix round 5 of 2026-10-29, group `imports` (TJ-0593): the
gate's lint stage (scripts/ci.sh) and CI's lint job (tests.yml) refuse
an unused import (ruff F401), lint the same rules over the same folders,
scripts/ included.

The one ruff run here lints a planted file in a folder of its own.
`_LINT` reads a folder list in linear time (TJ-4960, CodeQL py/redos).

Formerly: test_fix_1029_r5_imports
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO
CI_SH = REPO / "scripts" / "ci.sh"
TESTS_YML = REPO / ".github" / "workflows" / "tests.yml"
# Each folder ends at its first `/` (no `/` inside the class): with one
# there, `a/b/c/` split many ways and a line that failed at its end
# backtracked exponentially (backlog TJ-4960, CodeQL py/redos).
_LINT = re.compile(r"ruff check --select (\S+) ((?:[\w.-]+/ ?)+)\s*$")


def _lint_cmd(path: Path, marker: str):
    """(rules, folders) of the one `ruff check --select` line holding
    `marker` in `path`."""
    hits = [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if marker in ln and "ruff check --select" in ln]
    if len(hits) != 1:
        raise AssertionError(f"{path.name}: {len(hits)} lint lines")
    m = _LINT.search(hits[0])
    if not m:
        raise AssertionError(f"{path.name}: unparsed: {hits[0]!r}")
    return set(m.group(1).split(",")), m.group(2).split()


def _ruff_ok() -> bool:
    r = subprocess.run(python_module_argv("ruff", ["--version"]),
                       capture_output=True, text=True, check=False)
    return r.returncode == 0


class TestGateLintsUnusedImports(unittest.TestCase):
    """TJ-0593: 199 unused imports piled up because the gate linted only
    E9, F63, F7 and F82."""

    def test_ci_sh_selects_f401_over_every_python_folder(self):
        rules, folders = _lint_cmd(CI_SH, "stage lint")
        self.assertIn("F401", rules)
        self.assertTrue({"E9", "F63", "F7", "F82"} <= rules, rules)
        self.assertEqual(sorted(folders),
                         ["packages/", "scripts/", "src/", "tests/"])

    def test_ci_lint_job_lints_as_the_gate_does(self):
        self.assertEqual(_lint_cmd(TESTS_YML, "- run:"),
                         _lint_cmd(CI_SH, "stage lint"))

    def test_the_pattern_fails_a_long_line_in_linear_time(self):
        # In a child with a time limit: an exponential search would hold
        # this process. 40 folders that fail at the end took the old
        # pattern hours; nested folders still read as before.
        self.assertEqual(_LINT.search("ruff check --select E9 a/b/ c.d/").groups(),
                         ("E9", "a/b/ c.d/"))
        line = "ruff check --select E9 " + "a/" * 40 + "!"
        try:
            r = subprocess.run(
                [sys.executable, "-I", "-c",
                 "import re, sys; print(re.compile(sys.argv[1]).search(sys.argv[2]))",
                 _LINT.pattern, line],
                capture_output=True, text=True, check=False, timeout=60)
        except subprocess.TimeoutExpired:
            self.fail("_LINT backtracks exponentially on a line that does not match")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "None"), r.stderr)

    @unittest.skipUnless(_ruff_ok(), "ruff is not installed here")
    def test_the_gate_selection_refuses_an_unused_import(self):
        rules, _folders = _lint_cmd(CI_SH, "stage lint")
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "planted.py"
            bad.write_text("import json\n\nVALUE = 1\n", encoding="utf-8")
            kept = Path(d) / "kept.py"
            kept.write_text("import json  # noqa: F401  (re-exported)\n",
                            encoding="utf-8")
            r = subprocess.run(
                python_module_argv("ruff", [
                    "check", "--no-cache", "--isolated",
                    "--output-format", "concise",
                    "--select", ",".join(sorted(rules)), bad, kept]),
                capture_output=True, text=True, check=False, cwd=d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("planted.py:1:8: F401", r.stdout)
        self.assertNotIn("kept.py", r.stdout)


if __name__ == "__main__":
    unittest.main()
