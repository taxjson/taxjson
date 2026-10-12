"""Hand-entered year data kept in taxjson.toml: the readers and checks.

Four inputs that used to be project-root text files live in the
project's taxjson.toml (`taxjson migrate` moves an old project's files):

    [estimate]
    amt_carryover = { 2023 = 1200.50 }     # Canada: minimum tax carryover
                                           #   by year of origin (T691)
    [carryover]
    claimed = { 2023 = 4000.00 }           # net capital losses applied on
                                           #   each filed return (both
                                           #   countries; `taxjson carryover`)
    [[capital_gains_dividends]]            # Canada: T5 box 18
    symbol = "ABD.TO"
    year = 2025                            # or: date = 2025-06-16
    amount = "all"                         # or the box 18 amount: 1.25
    # account = "margin"                   # optional

    [[distributions]]                      # non-cash fund distributions
    symbol = "ABC.TO"
    record_date = 2025-12-29
    per_share = 0.2500                     # base currency; negative = ROC

`table_problems(cfg)` is the one check every config reader applies
(lib/config_check.settings_problems): types, dates, duplicates and the
account an entry names. Country ownership is lib/country CONFIG_COUNTRY
([capital_gains_dividends] and [estimate] amt_carryover are Canadian).
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.config_check import CARRYOVER_KEYS, DISTRIBUTION_KEYS

DIST_TABLE = "distributions"
CGD_TABLE = "capital_gains_dividends"
CARRY_TABLE = "carryover"
CLAIMED_KEY = "claimed"

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ProjectTableError(ValueError):
    """A taxjson.toml data table the readers cannot apply."""


def _number(v: Any) -> Optional[float]:
    """A finite TOML number (not a boolean), else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def iso_date(v: Any) -> Optional[str]:
    """A TOML date (2025-12-29) or a "YYYY-MM-DD" string that is a real
    date, as ISO text; None otherwise (a date-time is not a date)."""
    if isinstance(v, _dt.datetime):
        return None
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, str) and _DATE_RE.fullmatch(v.strip()):
        try:
            return _dt.date.fromisoformat(v.strip()).isoformat()
        except ValueError:
            return None
    return None


def _plausible_year(y: int) -> bool:
    return 1900 <= y <= _dt.date.today().year + 1


def _array_of_tables(cfg: Dict[str, Any], table: str
                     ) -> Tuple[List[Dict[str, Any]], List[str]]:
    v = cfg.get(table)
    if v is None:
        return [], []
    if not isinstance(v, list) or not all(isinstance(e, dict) for e in v):
        return [], [f"[[{table}]] must be an array of tables (one "
                    f"[[{table}]] section per entry), got "
                    f"`{table} = {v!r}`"]
    return v, []


# ------------------------------------------------------------ distributions

def distribution_rows(cfg: Dict[str, Any]
                      ) -> Tuple[List[Tuple[str, str, float]], List[str],
                                 List[str]]:
    """([(SYMBOL, record date, per share)], problems, warnings) of the
    [[distributions]] entries, in file order. A repeated symbol and date
    is kept (two components of one distribution may be two entries) and
    warned about: the amounts ADD."""
    entries, problems = _array_of_tables(cfg, DIST_TABLE)
    rows: List[Tuple[str, str, float]] = []
    warnings: List[str] = []
    seen: Dict[Tuple[str, str], int] = {}
    for n, e in enumerate(entries, 1):
        where = f"[[{DIST_TABLE}]] #{n}"
        extra = sorted(set(e) - set(DISTRIBUTION_KEYS))
        sym = e.get("symbol")
        date = iso_date(e.get("record_date"))
        amt = _number(e.get("per_share"))
        bad = []
        if extra:
            bad.append(f"unknown key(s) {', '.join(extra)} (an entry has "
                       f"symbol, record_date, per_share)")
        if not isinstance(sym, str) or not sym.strip() \
                or len(sym.split()) != 1:
            bad.append(f"symbol must be the books' symbol as a string, "
                       f"e.g. \"XAW.TO\" (got {sym!r})")
        if date is None:
            bad.append(f"record_date must be a date such as 2025-12-29 "
                       f"(got {e.get('record_date')!r})")
        if amt is None:
            bad.append(f"per_share must be a number in the base currency "
                       f"(negative = return of capital), got "
                       f"{e.get('per_share')!r}")
        if bad:
            problems.append(f"{where}: " + "; ".join(bad))
            continue
        key = (sym.strip().upper(), date)
        if key in seen:
            warnings.append(
                f"{where}: {key[0]} {date} repeats entry #{seen[key]} — "
                f"both amounts are applied (they ADD). If this entry is a "
                f"correction or a pasted copy, delete the other one.")
        else:
            seen[key] = n
        rows.append((key[0], date, amt))
    return rows, problems, warnings


# ------------------------------------------------------------ claimed

def claimed_losses(cfg: Dict[str, Any]) -> Tuple[Dict[int, float], List[str]]:
    """({year: amount}, problems) of [carryover] claimed — the net
    capital losses applied on each filed return."""
    tbl = cfg.get(CARRY_TABLE)
    if tbl is None:
        return {}, []
    where = f"[{CARRY_TABLE}]"
    if not isinstance(tbl, dict):
        return {}, [f"{where} must be a table (a [{CARRY_TABLE}] section), "
                    f"got `{CARRY_TABLE} = {tbl!r}`"]
    problems = [f"{where}: unknown key {k!r} (only `{CLAIMED_KEY} = "
                f"{{ 2023 = 4000.00 }}`)" for k in tbl
                if k not in CARRYOVER_KEYS]
    v = tbl.get(CLAIMED_KEY)
    if v is None:
        return {}, problems
    where = f"[{CARRY_TABLE}] {CLAIMED_KEY}"
    if not isinstance(v, dict):
        return {}, problems + [
            f"{where} must be a table by tax year, e.g. `{CLAIMED_KEY} = "
            f"{{ 2023 = 4000.00, 2024 = 1500 }}`, got {v!r}"]
    out: Dict[int, float] = {}
    for k, a in v.items():
        try:
            y = int(str(k).strip())
        except ValueError:
            problems.append(f"{where}: {k!r} is not a tax year")
            continue
        if not _plausible_year(y):
            problems.append(f"{where}: {y} is not a plausible tax year")
            continue
        amt = _number(a)
        if amt is None or amt < 0:
            problems.append(f"{where}: {y} must be an amount >= 0 (a "
                            f"number, unquoted), got {a!r}")
            continue
        out[y] = amt
    return out, problems


# ------------------------------------------------------------ all checks

def table_problems(cfg: Dict[str, Any]) -> List[str]:
    """Every problem of the four data tables (types, dates, duplicates,
    the account a box-18 entry names). Country ownership is checked by
    lib/country with the rest of the config."""
    out: List[str] = []
    out += distribution_rows(cfg)[1]
    out += claimed_losses(cfg)[1]
    from taxjson.lib.cg_dividends import entries_from_config
    try:
        entries_from_config(cfg)
    except ValueError as e:
        out.append(str(e))
    est = cfg.get("estimate")
    if isinstance(est, dict) and "amt_carryover" in est:
        from taxjson.lib.carryforward import amt_from_config
        try:
            amt_from_config(est["amt_carryover"])
        except ValueError as e:
            out.append(str(e))
    return out
