"""Shared test helpers: the checklist's CLI and its checks' stub results.

Shared from: test_checklist_run
"""
import subprocess

from taxjson.lib.dispatch import python_module_argv

import _paths


REPO_ROOT = _paths.REPO

# Every slow sub-command answers cleanly, so the verdicts are stable
# between a serial and a parallel run.
OK = {sub: (0, "ok\n", "") for sub in (
    "sanity", "find-missing-history", "elections", "audit", "option-boundary",
    "handoff", "reconcile-slips", "form-export", "t1135", "carryover",
    "fx-cash", "amt", "check-filed", "check-dates", "sum")}


def _cli(root, *a):
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), "checklist", *a])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env={"TAXJSON_WIDTH": "0", "NO_COLOR": "1",
                               "PATH": "/usr/bin:/bin", "HOME": str(root)})
