"""Shared test helpers: taxjson-export and its holdings.

Shared from: test_fix_1029_r7e_fetchforms
"""
import os
import subprocess
import tempfile
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import tomllib
import _paths


REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _env():
    return dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="0",
                TAXJSON_OFFLINE="1")


def _module(mod, *args, stdin=None):
    return subprocess.run(
        python_module_argv(mod) + [str(a) for a in args],
        capture_output=True, text=True, env=_env(), input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL, timeout=300)


def _export(files, *args):
    """taxjson-export with `files` ({name: text}) in a folder of their
    own; `args` name them as {name}. (CompletedProcess, parsed snapshot
    or None)."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, text in files.items():
            (root / name).write_text(text)
        argv = [str(root / a[1:-1]) if a.startswith("{") else a
                for a in args]
        r = _module("taxjson.bin.taxjson_export", *argv)
    return r, (tomllib.loads(r.stdout) if r.returncode == 0 else None)


def _toml(*holdings, account="acct1"):
    return (f'schema_version = "1.2"\n[meta]\naccount = "{account}"\n'
            f'generated_at = 2026-01-02T15:00:00Z\n'
            f'holdings_count = {len(holdings)}\n'
            + "".join(f"[[holding]]\n{h}" for h in holdings))


_BLENDED = ('symbol = "ZZQ.TO"\naccount = "acct1"\nquantity = 4.0\n'
            'currency = "CAD"\ntotal_cost = 80.0\ncost_per_share = 20.0\n'
            'cost_source = "blended"\nbase_currency = "CAD"\n'
            'base_total_cost = 80.0\nposition_start_date = 2024-03-01\n')
_NIL = ('symbol = "ZZQ.TO"\naccount = "acct2"\nquantity = 0.0\n'
        'currency = "CAD"\ntotal_cost = 0.0\n')
