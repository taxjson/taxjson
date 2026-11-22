"""Shared test helpers: a one-account project with planted rates, and the environment the checkout's taxjson commands run in there.

Shared from: test_fix_a2_errb
"""
import os
from datetime import date, timedelta
from pathlib import Path
from _support.cli_error_checkout import REPO_ROOT
from _planted_rates import plant


def _env(home, **kw):
    e = {**os.environ, "HOME": str(home), "TAXJSON_OFFLINE": "1",
         "NO_COLOR": "1", "PYTHONPATH": str(REPO_ROOT / "src")}
    e.update(kw)
    return e


def _rates(path: Path, home: Path):
    """The rates file, and the same rates in `home`'s currency cache: a
    run that rebuilds the file keeps them (backlog TJ-0635)."""
    d, lines = date(2024, 1, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += timedelta(days=1)
    plant(path, "\n".join(lines) + "\n", home)


def _make_project(root: Path, *, extra_accounts=""):
    """A project at `root`, run under the HOME beside it (root.parent /
    "home", as _Built and features.output.test_closed_standard_streams run it)."""
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "inputs" / "m" / "a.tt").write_text(
        "BUYSELL 2025-01-06 10:00:00 ABC.TO 100 CAD 10 -1000 0\n"
        "BUYSELL 2025-03-03 10:00:00 ABC.TO -40 CAD 12 480 0\n")
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        'option_grant_timing_since = 2025\n\n'
        '[accounts.m]\ntype = "taxable"\n' + extra_accounts)
    _rates(root / "work" / "to_base.csv", root.parent / "home")
