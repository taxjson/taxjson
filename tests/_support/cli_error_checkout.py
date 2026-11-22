"""Shared test helpers: the checkout the error-handling pins and their importers test (ERRB_REPO, else this one), its src/ first on sys.path.

Shared from: test_fix_a2_errb
"""
import os
import sys
from pathlib import Path

import _paths

REPO_ROOT = Path(os.environ.get("ERRB_REPO") or _paths.REPO)
# Before anything imports taxjson: a module run alone, in a worktree whose
# editable install points at another checkout, tests this one (TJ-4014).
sys.path.insert(0, str(REPO_ROOT / "src"))
