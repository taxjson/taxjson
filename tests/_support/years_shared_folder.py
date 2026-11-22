"""Shared test helpers: a folder of exports every year shares.

Shared from: test_fix_backlog_years
"""
import json
from pathlib import Path


_NOTE = ("## frozen by `tjs close-year`: this year reads its own copy "
         "of the exports it was filed from")


def _lock(year, *, closed_at=None, frozen=True, realized=100.0):
    doc = {"schema_version": 2, "year": year, "country": "canada",
           "basis": "wash-adjusted",
           "closed_at": closed_at or f"{year + 1}-03-01T12:00:00",
           "date_basis": "settle", "year_end": {}, "dispositions": [],
           "accounts": {"margin": {"realized": realized, "disallowed": 0.0,
                                   "dispositions": 1, "income": 0.0}},
           "totals": {"realized": realized, "disallowed": 0.0,
                      "dispositions": 1, "income": 0.0}}
    if frozen:
        doc["frozen"] = {"inputs_dir": "../inputs",
                         "tobase_map": "../tobase.map", "copied": 0,
                         "inputs": {}}
    return doc


def _years(top: Path, *, filed=True, newest_extra=""):
    """A folder of exports shared by every year: 2024 closed with its
    inputs frozen (the note, its own tobase.map and inputs/, the lock)
    — or, `filed` False, open and reading the shared folders — and
    2025 open, `newest_extra` added to its [settings]."""
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "tobase.map").write_text("# shared\n")
    y4 = top / "2024"
    (y4 / "inputs" / "margin").mkdir(parents=True)
    head = ('[settings]\nyear = {y}\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n')
    tail = '\n[accounts.margin]\ntype = "taxable"\n'
    if filed:
        (y4 / "taxjson.toml").write_text(
            head.format(y=2024) + f'{_NOTE}\n# inputs_dir = "../inputs"\n'
            f'# tobase_map = "../tobase.map"\n' + tail)
        (y4 / "tobase.map").write_text("# shared\n")
        (y4 / "filed").mkdir()
        (y4 / "filed" / "2024.json").write_text(json.dumps(_lock(2024)))
    else:
        (y4 / "taxjson.toml").write_text(
            head.format(y=2024) + 'inputs_dir = "../inputs"\n'
            'tobase_map = "../tobase.map"\n' + tail)
    y5 = top / "2025"
    y5.mkdir()
    (y5 / "taxjson.toml").write_text(
        head.format(y=2025) + 'inputs_dir = "../inputs"\n'
        'tobase_map = "../tobase.map"\n'
        'prior_year_record = "../2024/filed/2024.json"\n'
        + newest_extra + tail)
    return y4, y5
