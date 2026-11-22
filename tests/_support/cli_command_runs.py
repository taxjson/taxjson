"""Shared test helpers: a one-account project, the CLI and its gains file.

Shared from: test_fix_1029_r3_commands
"""
import os
import subprocess
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO = _paths.REPO

_CA = ('[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
       'source_currencies = []\n')
_TRADES = "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 100 100 0\n"


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _run_project(tmp: Path, trades: str = _TRADES, extra: str = "",
                 more_inputs=None) -> Path:
    """A Canada project of one taxable account, `margin` (and the
    accounts `extra` declares, whose .tt `more_inputs` gives), built by
    `tjs run`."""
    root = tmp / "p"
    (root / "inputs/margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        _CA + '[accounts.margin]\ntype = "taxable"\n' + extra)
    (root / "inputs/margin/trades.tt").write_text(trades)
    for name, text in (more_inputs or {}).items():
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "trades.tt").write_text(text)
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return root


def _gains_file(root: Path) -> Path:
    for name in ("margin_gains_wash.json", "margin_gains.json"):
        if (root / "work" / name).exists():
            return root / "work" / name
    raise AssertionError("no gains file")
