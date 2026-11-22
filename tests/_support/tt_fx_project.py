"""Shared test helpers: a .tt project of taxable accounts with FX rates in work/ and in HOME's cache, and the CLI under that HOME.

Shared from: test_fix_a2_pipecmd
"""
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from _planted_rates import plant
import _paths


REPO_ROOT = _paths.REPO


def _rates(path: Path, src: str, dst: str, rate: str, home: Path):
    """The rates file, and the same rates in `home`'s currency cache (a
    run that rebuilds the file keeps them: backlog TJ-0635)."""
    d = date(2025, 1, 1)
    lines = []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 {src} {dst} {rate} boc")
        d += timedelta(days=1)
    plant(path, "\n".join(lines) + "\n", home)


def _env(home, **kw):
    e = {**os.environ, "HOME": str(home), "TAXJSON_OFFLINE": "1",
         "NO_COLOR": "1", "PYTHONPATH": str(REPO_ROOT / "src")}
    e.update(kw)
    return e


def _cli(root, home, *a):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(home))


def _tt_project(td, lines, *, country="canada", year=2026, accounts=None,
                extra_settings=""):
    """One taxable account `m` holding a.tt (or `accounts`: {name: (type,
    lines)}); FX rates for the project's foreign currency in work/."""
    root = Path(td) / f"proj-{country}"
    base, src = (("CAD", "USD") if country == "canada" else ("USD", "CAD"))
    accounts = accounts or {"m": ("taxable", lines)}
    cfg = (f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "{country}"\n'
           + ('province = "ON"\n' if country == "canada" else "")
           + f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n'
           + (f'option_grant_timing_since = {year}\n'
              if country == "canada" else "") + extra_settings)
    for name, (atype, ls) in accounts.items():
        cfg += f'\n[accounts.{name}]\ntype = "{atype}"\n'
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "a.tt").write_text("\n".join(ls) + "\n")
    (root / "taxjson.toml").write_text(cfg)
    home = Path(td) / "home"
    home.mkdir(exist_ok=True)
    _rates(root / "work" / "to_base.csv", src, base,
           "1.3500" if country == "canada" else "0.7400", home)
    return root, home
