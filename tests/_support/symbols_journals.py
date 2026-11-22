"""Shared test helpers: journal legs, the cross-listings state and a project.

Shared from: test_journals_cmd
"""
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib import project_layout as _PL
from taxjson.lib.symbol_codes import exact_name


_TOML = ('[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
         'year = 2025\n[accounts.margin]\ntype = "taxable"\n'
         '[accounts.tfsa]\ntype = "tfsa"\n')


def _leg(sym, day, qty, name, *, account="margin", broker="rbc_direct",
         journal=True, ref=""):
    return XL.Leg(account, broker, sym, day, qty, name=exact_name(name),
                  raw_name=name, journal=broker if journal else "",
                  ref=ref)


def _state(legs, *, named=(), distinct=(), currency_journals=False):
    """work/cross_listings.state as the run writes it for `legs`."""
    names = {}
    for g in legs:
        names.setdefault(g.symbol, set()).add(g.name)
    shown = {g.name: g.raw_name for g in legs}
    refused = []
    res = XL.analyze(legs, names, shown, map_named=named,
                     map_distinct=distinct, base_currency="CAD",
                     currency_journals=currency_journals, refused=refused)
    res["refused"] = refused
    return XL.state_text(res)


def _project(td, state, tmap=None, effective=None):
    root = Path(td)
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_TOML)
    (root / "work" / XL.STATE).write_text(state)
    if tmap is not None:
        (root / "ticker.map").write_text(tmap)
    if effective is not None:
        (root / "work" / _PL.EFFECTIVE_MAP).write_text(effective)
    return root
