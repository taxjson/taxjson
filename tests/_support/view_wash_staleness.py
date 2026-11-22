"""Shared test helpers: a book's modification time set against the wash-adjusted gains'.

Shared from: test_new_account_makes_blend_stale
"""
import os
from pathlib import Path


def _older(p: Path, than: Path, seconds: float) -> None:
    t = than.stat().st_mtime + seconds
    os.utime(p, (t, t))
