"""Dual-country harness: run ONE synthetic book under both countries.

Every partition test (a rule that must fire in its country and must not
fire in the other) uses these helpers, so the two runs can only differ
by the country:

    from tax_rules import rule, rule_absent
    from tax_rules.dual import gains_both, tx

    @rule("CA-SL-02")
    @rule_absent("CA-SL-02", country="usa")
    def test_still_held(self):
        book = [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 1000), ...]
        r = gains_both(book, year=2025)
        r["canada"]["summary"]["total_disallowed"]  # the Canadian result
        r["usa"]["summary"]["total_disallowed"]     # the same book, US

- ``tx(action, date, symbol, qty, net, ...)``: one TaxTransaction with
  the settle date given explicitly (``settle=``; default = trade date).
- ``gains_both(book, ...)`` -> {"canada": result, "usa": result}: the full
  `run_gains` (the same code `taxjson run` uses) under each country, on
  deep copies of the book. Keyword arguments go to GainsRequest; per-
  country extras go in ``canada={...}`` / ``usa={...}``.
- ``projects_both(tmp, ...)`` -> {"canada": Path, "usa": Path}: two
  project folders with identical inputs/work files and the country's own
  [settings] (country, base currency); ``cli_both(projects, *args)`` runs
  `taxjson -C <project> args...` in each and returns the
  CompletedProcess per country.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from taxjson.lib.core import TaxTransaction
from taxjson.lib.country import CANADA, COUNTRIES, HOME_CURRENCY, USA

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"


def tx(action: str, date: str, symbol: str, qty: float = 0.0,
       net: float = 0.0, *, settle: Optional[str] = None,
       price: Optional[float] = None, account: str = "margin",
       currency: str = "USD", time: str = "10:00:00",
       **kw: Any) -> TaxTransaction:
    """One synthetic row: `net` is the money (a BUYSELL's |qty| x price;
    an ADJUST's amount, negative for a return of capital); `price`
    defaults to |net / qty|; `settle` defaults to the trade date."""
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action=action, date=date, time=time,
                          date_settle=settle or date, symbol=symbol,
                          quantity=float(qty), price=float(price),
                          net_amount=float(net), currency=currency,
                          account=account, **kw)


def gains_both(book: Iterable[TaxTransaction], *, sheltered=(),
               affiliated=(), taxable: bool = True,
               canada: Optional[Dict[str, Any]] = None,
               usa: Optional[Dict[str, Any]] = None,
               quiet: bool = True, **req: Any) -> Dict[str, dict]:
    """The same book through pipeline.run_gains under each country."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    book = list(book)
    extra = {CANADA: canada or {}, USA: usa or {}}
    out: Dict[str, dict] = {}
    for c in COUNTRIES:
        r = GainsRequest(country=c, taxable=taxable,
                         **dict(req, **extra[c]))
        err = io.StringIO()
        ctx = contextlib.redirect_stderr(err) if quiet else \
            contextlib.nullcontext()
        with ctx:
            res = run_gains(copy.deepcopy(book), copy.deepcopy(list(sheltered)),
                            copy.deepcopy(list(affiliated)), req=r)
        res["_stderr"] = err.getvalue()
        out[c] = res
    return out


def settings_for(country: str, **extra: Any) -> str:
    """The [settings] table of a project of `country` (TOML text)."""
    lines = [f'country = "{country}"',
             f'base_currency = "{HOME_CURRENCY[country]}"']
    for k, v in extra.items():
        lines.append(f"{k} = {json.dumps(v)}")
    return "[settings]\n" + "\n".join(lines) + "\n"


def projects_both(tmp, *, year: int = 2025,
                  accounts: str = '[accounts.margin]\ntype = "taxable"\n',
                  files: Optional[Dict[str, str]] = None,
                  canada: Optional[Dict[str, Any]] = None,
                  usa: Optional[Dict[str, Any]] = None,
                  tail: str = "") -> Dict[str, Path]:
    """Two projects under `tmp` with the same accounts and files (paths
    relative to the project root, e.g. "work/margin_gains.json") and each
    country's own settings. `canada=`/`usa=` add per-country settings;
    `tail` is appended to both tomls (extra tables)."""
    extra = {CANADA: canada or {}, USA: usa or {}}
    out: Dict[str, Path] = {}
    for c in COUNTRIES:
        root = Path(tmp) / c
        root.mkdir(parents=True, exist_ok=True)
        (root / "taxjson.toml").write_text(
            settings_for(c, year=year, **extra[c]) + accounts + tail)
        for rel, text in (files or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        out[c] = root
    return out


def cli(root: Path, *args: str, timeout: int = 180
        ) -> subprocess.CompletedProcess:
    """`taxjson -C root args...` from this worktree's sources."""
    import os
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=timeout)


def cli_both(projects: Dict[str, Path], *args: str
             ) -> Dict[str, subprocess.CompletedProcess]:
    return {c: cli(p, *args) for c, p in projects.items()}
