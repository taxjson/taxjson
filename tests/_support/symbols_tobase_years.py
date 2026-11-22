"""Shared test helpers: a multi-year project and its tobase.map.

Shared from: test_fix_shared_tobase
"""
from pathlib import Path


def _toml(year, country="canada", extra=""):
    return (f'[settings]\ncountry = "{country}"\nyear = {year}\n'
            f'inputs_dir = "../inputs"\n{extra}')


def _years(td, years=(2024, 2025), tobase=None, shared=False):
    """A multi-year project: one folder per year, the exports shared;
    `tobase` the text of each year's own tobase.map (or, `shared`, of
    one at the top every year reads)."""
    top = Path(td) / "taxes"
    (top / "inputs").mkdir(parents=True)
    for y in years:
        d = top / str(y)
        d.mkdir()
        (d / "taxjson.toml").write_text(_toml(
            y, extra='tobase_map = "../tobase.map"\n' if shared else ""))
        (d / "ticker.map").write_text("")
        if tobase is not None and not shared:
            (d / "tobase.map").write_text(tobase)
    if tobase is not None and shared:
        (top / "tobase.map").write_text(tobase)
    return top
