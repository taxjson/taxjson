"""Shared test helpers: a Canada or USA context for the filing views.

Shared from: test_fix_a2_partA
"""
from __future__ import annotations
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl
from taxjson.lib.country import USA


def _ctx(root: Path, country: str, accounts: dict, year: int = 2025):
    cfg = {"settings": {"country": country, "year": year,
                        "base_currency": "USD" if country == USA else "CAD"},
           "accounts": accounts}
    return cl.Ctx(root=root, cfg=cfg, year=year, today=date(2026, 6, 1),
                  run_sub=lambda *a, **k: (0, "", ""))
