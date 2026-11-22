"""Workflow audit 2026-10-22, batch 2: what the views claim
(~/workflow-audit-2026-10-22.md is the maintainer's report).

- Item 5: a sale of more shares than held, the engine closing part of it
  against a long, is NOT in the totals for the units it left short.
- Item 10: the checklist flags option_grant_timing_since only where
  option-boundary (and the run) warn: the books hold a written option.
- Item 12: an empty crypto (or other) account left from the scaffold:
  option-boundary and the checklist name the empty folder, never "run
  `tjs run`" (the run skips it, so the advice looped).
- Item 15: outside a project every command says there is none and how
  to start one or where to `cd`.

Every fixture is SYNTHETIC: account 99900001, made-up tickers ZZ*.

Formerly: test_fix_audit_1022_b
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _paths

REPO = _paths.REPO


def _tjs(root, *args, cwd=None):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    argv = [sys.executable, "-m", "taxjson.bin.taxjson_run"]
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _single(tmp, keep=("margin", "crypto")):
    """`init --single` (Canada, 2025), its accounts cut to `keep`."""
    root = Path(tmp) / "proj"
    r = _tjs(None, "init", "--single", "--country", "canada", "--year",
             "2025", str(root))
    assert r.returncode == 0, r.stderr
    cfg = root / "taxjson.toml"
    text = cfg.read_text()
    for name in ("margin", "tfsa", "rrsp", "crypto"):
        if name in keep:
            continue
        text = re.sub(r"\n\[accounts\.%s\]\n(?:[^\n\[]+\n)*" % name, "\n",
                      text)
        d = root / "inputs" / name
        if d.is_dir():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
    cfg.write_text(text)
    return root


class TestOutsideAProject(unittest.TestCase):
    """Item 15: every project command, in a folder that is none.

    Formerly: test_fix_audit_1022_b.TestOutsideAProject"""

    CMDS = ("sanity", "find-missing-history", "wash-sales", "list",
            "events", "fees", "fees-sum", "fx-cash", "divs-sum", "stats",
            "amt", "option-boundary", "run", "sum", "elect")

    def test_says_no_project_and_how_to_start_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            for cmd in self.CMDS:
                with self.subTest(cmd=cmd):
                    r = _tjs(tmp, cmd)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("not a taxjson project", r.stderr)
                    self.assertIn("init --country", r.stderr)
                    self.assertNotIn("`tjs run` first", r.stderr)
                    self.assertNotIn("country is missing", r.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_inside_a_project_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _single(tmp, keep=("margin",))
            r = _tjs(None, "sanity", cwd=root / "inputs" / "margin")
            self.assertIn("not a taxjson project", r.stderr)
            self.assertIn("`cd ../..`", r.stderr)
            self.assertIn("-C ../.. sanity", r.stderr)

    def test_the_checklist_still_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _tjs(tmp, "checklist", "--run")
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
