"""Shared test helpers: a writable copy of the style project, priced.

Shared from: test_style_group_c
"""
import datetime
import json
import shutil
import tempfile
from pathlib import Path

from _style import Project, project, quote_spelling


def _copy(country: str) -> Project:
    """A writable copy of the built style project (watch writes state,
    harvest needs every held symbol in the price cache)."""
    src = project(country)
    td = tempfile.mkdtemp(prefix="taxjson_style_c_")
    dst = Path(td) / country
    shutil.copytree(src.root, dst)
    return Project(dst, country)


def _price_every_holding(p: Project) -> None:
    """Add a synthetic cache price for every open position harvest will
    look up (the shared cache keys the country's own suffix only)."""
    today = datetime.date.today().isoformat()
    cache = p.root / "work" / ".price_cache.json"
    doc = json.loads(cache.read_text())
    for g in (p.root / "work").glob("*_gains*.json"):
        try:
            inv = json.loads(g.read_text()).get("inventory") or []
        except (OSError, ValueError, AttributeError):
            continue
        for h in inv:
            sym = str(h.get("symbol") or "")
            if sym and sym not in doc:
                cur = "CAD" if sym.endswith(".TO") else "USD"
                doc[sym] = {"price": 0.05 if len(sym) > 12 else 20.0,
                            "asof": today, "source": "yfinance",
                            "currency": cur,
                            "quote_symbol": quote_spelling(p.root, sym)}
    cache.write_text(json.dumps(doc))
