"""Shared test helpers: transfers to and from registered plans.

Shared from: test_fix_1029_r7_transfers
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

from tax_rules.dual import settings_for

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root: Path, accounts: dict, files: dict, *, year: int = 2025,
             country: str = "canada", tail: str = "") -> Path:
    """A project: `accounts` {name: account table body}, `files`
    {path relative to the project: text}."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, source_currencies=[])
        + "".join(f"[accounts.{n}]\n{body}\n" for n, body in accounts.items())
        + tail)
    (root / "ticker.map").write_text("")
    for n in accounts:
        (root / "inputs" / n).mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _run(test: unittest.TestCase, root: Path) -> subprocess.CompletedProcess:
    r = _cli(root, "run", "--no-input")
    test.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


def _totals(root: Path) -> dict:
    r = _cli(root, "form-export", "--json")
    return json.loads(r.stdout)["totals"]


def _held(root: Path) -> list:
    held = json.loads(_cli(root, "list", "--json").stdout)
    return sorted((r["account"], r["symbol"], r["qty"])
                  for r in held["rows"])


_TAXABLE = 'type = "taxable"'


_TFSA = 'type = "sheltered"\nplan = "tfsa"'


def _gains_with_context(sheltered):
    """Canada, 2025: 100 SYN.TO bought in 2024 for 1000 and sold on
    January 20, 2025 for 600 (a loss of 400), with the registered
    accounts' rows `sheltered` (a list of TaxTransaction)."""
    import contextlib
    import copy
    import io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    from tax_rules.dual import tx
    book = [tx("BUYSELL", "2024-01-08", "SYN.TO", 100, 1000,
               currency="CAD", account="a", id="old"),
            tx("BUYSELL", "2025-01-20", "SYN.TO", -100, 600,
               currency="CAD", account="a", id="loss")]
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return run_gains(copy.deepcopy(book), copy.deepcopy(sheltered), [],
                         GainsRequest(country="canada", year=2025,
                                      taxable=True, phantom_hint=False))
