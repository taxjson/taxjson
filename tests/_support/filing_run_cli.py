"""Shared test helpers: `tjs` run on a project, as the filing commands' tests call it.

Shared from: test_filing_commands_on_partial_or_stale_books
"""
import subprocess
import sys

import _paths


REPO_ROOT = _paths.REPO


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)
