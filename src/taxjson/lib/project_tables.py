"""Hand-entered year data kept in taxjson.toml: the readers and checks.

Four inputs that used to be project-root text files live in the
project's taxjson.toml (`tjs migrate` moves an old project's files):

    [estimate]
    amt_carryover = { 2023 = 1200.50 }     # Canada: minimum tax carryover
                                           #   by year of origin (T691)
    [carryover]
    claimed = { 2023 = 4000.00 }           # net capital losses applied on
                                           #   each filed return (both
                                           #   countries; `tjs carryover`)
    [[capital_gains_dividends]]            # Canada: T5 box 18
    symbol = "ABD.TO"
    year = 2025                            # or: date = 2025-06-16
    amount = "all"                         # or the box 18 amount: 1.25
    # account = "margin"                   # optional

    [[distributions]]                      # non-cash fund distributions
    symbol = "ABC.TO"
    record_date = 2025-12-29
    per_share = 0.2500                     # base currency; negative = ROC

lib/config_check's `table_problems(cfg)` (in settings_problems) is the
one check every config reader applies: types, dates, duplicates and the
account an entry names. Country ownership is lib/country CONFIG_COUNTRY
([capital_gains_dividends] and [estimate] amt_carryover are Canadian).
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.futures_prefixes import canonical_futures_symbol


DIST_TABLE = "distributions"
CGD_TABLE = "capital_gains_dividends"
CARRY_TABLE = "carryover"
CLAIMED_KEY = "claimed"
# The keys a [carryover] table and a [[distributions]] entry may carry
# (claimed_losses and distribution_rows name any other; lib/config_template
# lists them).
CARRYOVER_KEYS = ("claimed",)
# ...and the key that answers `tjs carryover` where it says it can't tell
# (Canada): accepted, documented in docs/settings.md, but no part of the
# template `tjs init` writes; it is refused where the ledger can tell.
STATED_BACK_KEY = "carried_back"
CARRYOVER_ANSWER_KEYS = (STATED_BACK_KEY,)
DISTRIBUTION_KEYS = ("symbol", "record_date", "per_share")

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
        # As the books spell it (a future's `/` or `\` prefix is `F:`:
        # lib/futures_prefixes, backlog TJ-5150); one that names no
        # contract is refused, naming the entry.
        try:
            key = (canonical_futures_symbol(sym.strip().upper()), date)
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
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

def _carry_table(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """(the [carryover] table, its problems as a table)."""
    tbl = cfg.get(CARRY_TABLE)
    if tbl is None:
        return {}, []
    where = f"[{CARRY_TABLE}]"
    if not isinstance(tbl, dict):
        return {}, [f"{where} must be a table (a [{CARRY_TABLE}] section), "
                    f"got `{CARRY_TABLE} = {tbl!r}`"]
    return tbl, [f"{where}: unknown key {k!r} (only `{CLAIMED_KEY} = "
                 f"{{ 2023 = 4000.00 }}`)" for k in tbl
                 if k not in CARRYOVER_KEYS + CARRYOVER_ANSWER_KEYS]


def _by_year(v: Any, where: str, key: str) -> Tuple[Dict[int, float], List[str]]:
    """({year: amount}, problems) of a `KEY = { YEAR = AMOUNT }` value."""
    if v is None:
        return {}, []
    if not isinstance(v, dict):
        return {}, [
            f"{where} must be a table by tax year, e.g. `{key} = "
            f"{{ 2023 = 4000.00, 2024 = 1500 }}`, got {v!r}"]
    problems: List[str] = []
    out: Dict[int, float] = {}
    spelled: Dict[int, str] = {}
    for k, a in v.items():
        try:
            y = int(str(k).strip())
        except ValueError:
            problems.append(f"{where}: {k!r} is not a tax year")
            continue
        if not _plausible_year(y):
            problems.append(f"{where}: {y} is not a plausible tax year")
            continue
        if y in spelled:
            # `2023` and `02023` are two TOML keys but one tax year:
            # never keep one of two contradictory claims silently.
            problems.append(f"{where}: {y} is given twice ({spelled[y]!r} "
                            f"and {str(k)!r}); keep one line for {y}")
            out.pop(y, None)
            continue
        spelled[y] = str(k)
        amt = _number(a)
        if amt is None or amt < 0:
            problems.append(f"{where}: {y} must be an amount >= 0 (a "
                            f"number, unquoted), got {a!r}")
            continue
        out[y] = amt
    return out, problems


def claimed_losses(cfg: Dict[str, Any]) -> Tuple[Dict[int, float], List[str]]:
    """({year: amount}, problems) of [carryover] claimed — the net
    capital losses applied on each filed return."""
    tbl, problems = _carry_table(cfg)
    out, more = _by_year(tbl.get(CLAIMED_KEY),
                         f"[{CARRY_TABLE}] {CLAIMED_KEY}", CLAIMED_KEY)
    return out, problems + more


def _back_targets(year: int, v: Dict[Any, Any], line: str
                  ) -> Tuple[Dict[int, float], List[str]]:
    """({target year: amount}, problems) of one by-target entry of [carryover]
    carried_back, `YEAR = { TARGET = AMOUNT }`: the years the loss of `year`
    went back to on its T1A, each one of the 3 before it, the amounts numbers
    of 0 or more. `line`: the entry as a problem names it."""
    out: Dict[int, float] = {}
    problems: List[str] = []
    if not v:
        return {}, [f"{line} names no year: give each year the loss went back to "
                    f"and its amount, e.g. `{year} = {{ {year - 1} = 40.00 }}`, or "
                    f"`{year} = 0` for none"]
    for k, a in v.items():
        try:
            t = int(str(k).strip())
        except ValueError:
            problems.append(f"{line}: {k!r} is not a tax year")
            continue
        amt = _number(a)
        if not year - 3 <= t < year:
            problems.append(f"{line}: {t} is not one of the 3 years before {year} "
                            f"({year - 3} to {year - 1}): a net capital loss "
                            f"carries back 3 years (T1A)")
        elif t in out:
            problems.append(f"{line}: {t} is given twice; keep one amount for {t}")
        elif amt is None or amt < 0:
            problems.append(f"{line}: {t} must be an amount >= 0 (a number, "
                            f"unquoted), got {a!r}")
        else:
            out[t] = amt
    return ({}, problems) if problems else (out, [])


def carried_back_entries(cfg: Dict[str, Any]) -> Tuple[
        Dict[int, float], Dict[int, Dict[int, float]], List[str]]:
    """({year: total}, {year: {target year: amount}}, problems) of [carryover]
    carried_back (Canada): the net capital loss each named year's return
    carried back (T1A), the person's answer where `tjs carryover` says it
    can't tell (backlog TJ-4880). `YEAR = AMOUNT` is a total, met the oldest
    [carryover] claimed year first; `YEAR = { TARGET = AMOUNT }` says which
    years it went to, and its total is their sum (backlog TJ-4882). A target
    needs a `claimed` amount to meet, at least what is carried back to it.
    The table's own problems are `claimed_losses`'s."""
    where = f"[{CARRY_TABLE}] {STATED_BACK_KEY}"
    tbl = _carry_table(cfg)[0]
    v = tbl.get(STATED_BACK_KEY)
    if not isinstance(v, dict):
        totals, problems = _by_year(v, where, STATED_BACK_KEY)
        return totals, {}, problems
    # The years, read as `claimed`'s are (a tax year, once), then each amount.
    years, problems = _by_year({k: 0 for k in v}, where, STATED_BACK_KEY)
    claimed = _by_year(tbl.get(CLAIMED_KEY), "", CLAIMED_KEY)[0]
    totals: Dict[int, float] = {}
    to: Dict[int, Dict[int, float]] = {}
    for k, a in v.items():
        y = int(str(k).strip()) if str(k).strip().lstrip("-").isdigit() else None
        if y not in years:
            continue
        if not isinstance(a, dict):
            amt = _number(a)
            if amt is None or amt < 0:
                problems.append(f"{where}: {y} must be an amount >= 0 (a "
                                f"number, unquoted), got {a!r}")
            else:
                totals[y] = amt
            continue
        line = (f"{where}: {y} = {{ "
                + ", ".join(f"{t} = {x!r}" for t, x in a.items()) + " }")
        got, bad = _back_targets(y, a, line)
        for t, amt in sorted(got.items()):
            have = claimed.get(t, 0.0)
            if amt > have + 0.005:
                bad.append(
                    f"{line}: {amt:,.2f} carried back to {t}, but "
                    f"[{CARRY_TABLE}] {CLAIMED_KEY} has "
                    + ("no amount" if have <= 0.005 else f"{have:,.2f}")
                    + f" under {t}: a carry-back is part of the losses applied "
                      f"on the {t} return; correct the year or the amount, or "
                      f"`{CLAIMED_KEY}`")
        problems += bad
        if not bad:
            to[y] = got
            totals[y] = math.fsum(got.values())
    return totals, to, problems


def carried_back_stated(cfg: Dict[str, Any]) -> Tuple[Dict[int, float], List[str]]:
    """({year: amount}, problems) of [carryover] carried_back: each year's
    total, a by-target entry's being the sum of its amounts
    (`carried_back_entries`)."""
    totals, _to, problems = carried_back_entries(cfg)
    return totals, problems


# ------------------------------------------------------------ all checks

def claims_back(cfg: Dict[str, Any], year: Any) -> bool:
    """True when [carryover] claimed has an entry under one of the 3
    years before `year`: a T1A carry-back of `year`'s loss may be
    recorded, which only the carryover ledger can tell."""
    try:
        y = int(year)
        claimed = claimed_losses(cfg)[0]
    except (TypeError, ValueError, SystemExit):
        return False
    return any(y - 3 <= int(c) < y and float(a) > 0.005
               for c, a in claimed.items())
