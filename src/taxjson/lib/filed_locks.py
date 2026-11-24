"""A filed year's lock, read: the snapshot `tjs close-year` wrote to
filed/<year>.json (`read_lock`, `load_lock_json`, `project_locks`), what is
wrong with one (`lock_damage`, `lock_shape_problem`, `lock_country_problem`,
`missing_prior_lock`; a carryforwards block, `block_problem`; another year's
filed rows, `filed_rows_outside`), the per-account filing aggregates a lock holds and a
run recomputes (`aggregates_from_gains`, `income_as_summed`, `form_lines`),
and the notes other commands print from them (`partial_year_note`,
`locked_year_flags`, `lock_label`).

Writing a lock, recomputing a year and the drift report are the
`tjs close-year` / `tjs check-filed` command code in bin/taxjson_filed.py.
Moved from bin (PLAN step 8): lib reads a lock without importing the
command code.
"""
from taxjson.project.settings import as_settings
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Mapping, Any, Dict, List, Optional, Tuple

from taxjson.lib.futures_prefixes import canonical_futures_symbol
from taxjson.lib.tx_load import finite_num
from taxjson.lib.project_tables import _plausible_year
from taxjson.lib import cli_diag
from taxjson.model.result import GainRow, money_keys
from taxjson.model import money as M


PROG = "taxjson-filed"
# What aggregates_from_gains holds a row to before its views read it:
# the dates and every figure a GainRow reads (TJ-3034).
_VIEW_FIELDS = money_keys(GainRow) + ("date", "date_settle")
# The per-account and total figures a lock records as numbers.
_NUMERIC_KEYS = ("realized", "disallowed", "income", "dividend", "pil",
                 "proceeds", "st_gain", "lt_gain", "dispositions",
                 "tainted")
# The RETURN row's figures (close-year's _return_row_totals).
_RETURN_ROW_KEYS = ("realized", "disallowed")


# A locked figure: close-year writes plain finite numbers; anything else is
# a damaged or hand-edited lock.


def form_lines(entries: List[Dict[str, Any]], *, crypto: bool = False,
               year: Optional[int] = None,
               date_key: str = "date_settle") -> Dict[str, float]:
    """The amounts the export puts on the return for these disposition
    entries: Schedule 3 {proceeds/gain line code: amount} (Canadian
    gains; the 2024 form's Period 1 codes 10689/10690 and 10693/10694
    apart, A2-0166), or Form 8949 part totals {"I_proceeds": ..} (US
    gains carry a term), with, from 2025, the digital-asset boxes' own
    totals {"I_da_proceeds": ..} (boxes G-L, A2-0482). The lock records
    them so a change that moves amounts BETWEEN lines (13199 vs 15199 vs
    15200), or changes how proceeds are presented (outlays, short
    sales), is drift even when the total gain is unchanged (R1-205,
    R1-281)."""
    import contextlib
    import io
    from taxjson.lib import tax_forms as TF
    if not entries:
        return {}
    marked = TF.mark_crypto(entries) if crypto else entries
    with contextlib.redirect_stderr(io.StringIO()):
        if any(e.get("term") in ("SHORT_TERM", "LONG_TERM")
               for e in entries):
            rep = TF.build_8949(marked, year)
            out = {f"{part}_{k}": float(v)
                   for part in ("I", "II")
                   for k, v in (rep.get(f"part_{part}_totals") or {}).items()
                   if rep.get(f"part_{part}")}
            for g in rep.get("groups") or []:
                if g.get("digital_asset"):
                    for k, v in g["totals"].items():
                        out[f"{g['part']}_da_{k}"] = float(v)
            if rep.get("section_1256"):
                # §1256 contracts are off Form 8949 (Form 6781 by hand,
                # US-FUT-02): their net is locked on its own line.
                out["6781_gain"] = float(
                    rep["section_1256_totals"]["gain"])
            return out
        rep = TF.build_schedule3(marked, year, date_key)
    return {k.split("_", 1)[1]: float(v)
            for k, v in (rep.get("totals") or {}).items()
            if k not in ("proceeds_all", "gain_all")}


def aggregates_from_gains(doc: Dict[str, Any],
                          account: Optional[str] = None, *,
                          crypto: bool = False,
                          year: Optional[int] = None,
                          rounded: bool = True) -> Dict[str, Any]:
    """The filing-relevant aggregate set from one gains document —
    optionally restricted to one account's entries (used when the
    document is a blended combined run). `crypto`: the account's
    dispositions go on the crypto-asset line. `rounded=False` returns
    the money totals unrounded (close-year sums the accounts and rounds
    ONCE — S031-20)."""
    if not isinstance(doc, dict):
        raise ValueError("not a gains document (expected a JSON object)")
    # The date the file was year-scoped on also places a 2024
    # disposition in Period 1 or 2 of the 2024 Schedule 3 (A2-0166).
    _basis = str(((doc.get("summary") or {}) if isinstance(
        doc.get("summary"), dict) else {}).get("tax_date_basis") or "")
    date_key = "date" if _basis == "trade" else "date_settle"
    entries: List[Dict[str, Any]] = []
    realized = disallowed = income = 0.0
    dividend = pil = 0.0
    proceeds = st_gain = lt_gain = 0.0
    dispositions = tainted = 0
    # Every row held to the gains-row contract first: a disposition
    # without its gain or units was skipped here in silence and left out
    # of the lock and the drift check (issue #17).
    # The rows read typed too, before a view reads them: lib/compare
    # reads the books with load_report_json, past the row checks (step
    # 6c(6c2)). This account's rows alone (TJ-2992, TJ-3023, TJ-3032),
    # a manual row's figures unread; the dates and every figure a view
    # reads (here and in form_lines' export builders), not a field no
    # view reads (TJ-3034).
    from taxjson.lib.json_input import (check_gains_rows, check_row_types,
                                        gains_row_kind)

    def _mine(r):
        return account is None or r.get("account") == account
    check_gains_rows(doc.get("transactions"), "gains document", only=_mine)
    check_row_types(doc.get("transactions"), "gains document",
                    only=lambda r: _mine(r) and gains_row_kind(r) != "manual",
                    fields=_VIEW_FIELDS)
    for e in doc.get("transactions", []):
        if account is not None and e.get("account") != account:
            continue
        kind = gains_row_kind(e)
        if kind == "manual":
            tainted += 1
            continue
        v = GainRow(e)
        if kind == "income":
            dividend += float(v.dividend or 0.0)
            pil += float(v.pil or 0.0)
            continue
        entries.append(e)
        gain = float(v.gain or 0.0)
        realized += gain
        disallowed += float(v.disallowed_amount
                            or e.get("disallowed") or 0.0)
        dispositions += 1
        proceeds += float(v.proceeds or 0.0)
        term = str(e.get("term") or "")
        if term == "SHORT_TERM":
            st_gain += gain
        elif term == "LONG_TERM":
            lt_gain += gain
    # Unknown-cost dispositions: the pipeline MOVES them to
    # manual_reporting_required (its 'tainted' key popped), so the
    # 'tainted' test above only sees hand-run files. Count both.
    for e in doc.get("manual_reporting_required") or []:
        if account is not None and e.get("account") != account:
            continue
        tainted += 1
    income = dividend + pil
    if not rounded:
        return {"realized": realized, "disallowed": disallowed,
                "income": income, "dividend": dividend, "pil": pil,
                "proceeds": proceeds, "st_gain": st_gain,
                "lt_gain": lt_gain}
    return {
        "realized": M.shown(realized, 2),
        "disallowed": M.shown(disallowed, 2),
        "dispositions": dispositions,
        "income": M.shown(income, 2),
        # A dividend and a payment in lieu go on different lines (a PIL
        # is not a taxable dividend: no gross-up, no dividend tax
        # credit), so a reclassification between them moves the filed
        # return even when their sum does not (S032-06).
        "dividend": M.shown(dividend, 2),
        "pil": M.shown(pil, 2),
        "tainted": tainted,
        # The ENGINE's net proceeds (sell-side costs netted, a short
        # cover's negated cost) — not a return line; the return's lines
        # are in form_lines (R1-281). A US ST<->LT term flip changes tax
        # owed with realized unchanged, so ST/LT are locked too.
        "proceeds": M.shown(proceeds, 2),
        "st_gain": M.shown(st_gain, 2),
        "lt_gain": M.shown(lt_gain, 2),
        "form_lines": {k: M.shown(v, 2) for k, v in
                       form_lines(entries, crypto=crypto, year=year,
                                  date_key=date_key).items()},
    }


def income_as_summed(lock: Dict[str, Any]) -> Optional[float]:
    """A lock's income as `tjs sum`'s taxable SUBTOTAL row adds it:
    each account's DIVIDEND and PIL to the cent, summed. The lock's
    `totals` round the accounts' unrounded sum once (what check-filed
    compares), a cent or so apart: close-year and `tjs years` show
    this (display only; backlog TJ-0212). None when the lock has no
    per-account figures."""
    accts = lock.get("accounts") if isinstance(lock, dict) else None
    if not isinstance(accts, dict) or not accts:
        return None
    total = 0.0
    for a in accts.values():
        if not isinstance(a, dict):
            return None
        parts = ([a.get("dividend"), a.get("pil")]
                 if "dividend" in a or "pil" in a else [a.get("income")])
        for v in parts:
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None
            total += M.shown(float(v), 2)
    return M.shown(total, 2) + 0.0


def lock_country_problem(lock: Dict[str, Any], settings: Dict[str, Any],
                         label: str, *, prior: bool = False
                         ) -> Optional[str]:
    """A refusal message when a filed-year lock (or a prior-year
    record) was closed under the OTHER country than this project's, else
    None. Every lock close-year writes records its country; recomputing
    a US-filed year under Canadian rules (or the reverse) reported every
    difference between the two laws as drift and advised amending or
    refreshing the lock — which would overwrite the filed record with
    the other country's numbers (partition COMMANDS-08). A lock without
    a country (hand-written, pre-country) is not judged. `prior`: the
    lock is read as the previous year's record (the next year's
    carryover, estimate, amt, handoff), so the advice is to point
    [settings] prior_year_record at this country's lock (2026-10-25
    audit year-end#1)."""
    from taxjson.lib.country import (CountryError, canonical_country,
                                     display_name, settings_country)
    rc = lock.get("country") if isinstance(lock, dict) else None
    if rc in (None, ""):
        return None
    here = settings_country(settings)
    try:
        rcc = canonical_country(rc, what=f"{label} country")
    except CountryError as e:
        return f"{e} — the lock cannot be checked against this project"
    if rcc == here:
        return None
    if prior:
        return (f"{label} was closed under country = \"{rcc}\" "
                f"({display_name(rcc)} law) but this project is country = "
                f"\"{here}\" ({display_name(here)}): the other country's "
                f"carry-forwards, positions and cost cannot open a "
                f"{display_name(here)} year, so the lock is not read. Point "
                f"[settings] prior_year_record at the previous year's lock "
                f"of this project's {display_name(here)} books (or, if this "
                f"project's country is wrong, set [settings] country = "
                f"\"{rcc}\").")
    return (f"{label} was closed under country = \"{rcc}\" "
            f"({display_name(rcc)} law) but this project is country = "
            f"\"{here}\" ({display_name(here)}): a return filed under one "
            f"country's rules cannot be checked against the other's, so "
            f"it is not recomputed. Set [settings] country = \"{rcc}\" to "
            f"check it; do NOT refresh it with `close-year --force` (that "
            f"would overwrite the filed {display_name(rcc)} record).")


def lock_shape_problem(lock: Any, year) -> Optional[str]:
    """Why a filed/<year>.json document is not a close-year lock — not
    a JSON object, a per-account table that is not one, totals that are
    not a table, a `year` other than the file's — else None. The
    readers that summarise a lock (`tjs years`) call a damaged one
    a problem instead of a filed year (issue #49); check-filed refuses
    the same shapes."""
    if not isinstance(lock, dict):
        what = ("null" if lock is None else "true/false"
                if isinstance(lock, bool) else "an array"
                if isinstance(lock, list) else "a string"
                if isinstance(lock, str) else "a number")
        return f"is not a close-year lock ({what}, not a JSON object)"
    accts = lock.get("accounts", {})
    if not isinstance(accts, dict) or not all(
            isinstance(v, dict) for v in accts.values()):
        return "is not a close-year lock (no per-account table)"
    if "totals" in lock and not isinstance(lock.get("totals"), dict):
        return "is not a close-year lock (totals is not a table)"
    # A figure that is not a finite number (NaN, Infinity, null, a quoted
    # number) is a damaged lock, never a match (finite_num).
    for acct, tbl in accts.items():
        for k in _NUMERIC_KEYS:
            if k in tbl and not finite_num(tbl[k]):
                return (f"records accounts.{acct}.{k} as {tbl[k]!r}, not a "
                        f"finite number — the lock is damaged")
        fl = tbl.get("form_lines")
        if isinstance(fl, dict):
            for code, v in fl.items():
                if not finite_num(v):
                    return (f"records accounts.{acct}.form_lines.{code} as "
                            f"{v!r}, not a finite number — the lock is "
                            f"damaged")
    for k, v in (lock.get("totals") or {}).items():
        if k in _NUMERIC_KEYS and not finite_num(v):
            return (f"records totals.{k} as {v!r}, not a finite number — "
                    f"the lock is damaged")
    # The RETURN row close-year recorded (`tjs sum`'s, each sale's
    # cents): not recomputed by the drift check (display only there),
    # but `tjs years` shows it and the next year's carryover takes
    # it as the filed figure (Canada), so a damaged one is a damaged
    # lock, never passed over in silence (backlog TJ-0274).
    if "return_row" in lock:
        rr = lock.get("return_row")
        if not isinstance(rr, dict):
            return ("records return_row as "
                    + ("null" if rr is None else type(rr).__name__)
                    + ", not a table — the lock is damaged")
        for k in _RETURN_ROW_KEYS:
            if k in rr and not finite_num(rr[k]):
                return (f"records return_row.{k} as {rr[k]!r}, not a "
                        f"finite number — the lock is damaged")
    # The year's slip capital gains close-year recorded (T5 box 18;
    # backlog TJ-0898): the next year's carryover adds it to the filed
    # figure.
    if "slip_gains" in lock and not finite_num(lock.get("slip_gains")):
        return (f"records slip_gains as {lock.get('slip_gains')!r}, not a "
                f"finite number — the lock is damaged")
    y = lock.get("year")
    if y is not None:
        try:
            ok = (not isinstance(y, bool) and int(y) == int(year))
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return f"says year {y!r}, not {year}"
    return None


# ------------------------------------------- the one check of a lock
#
# Every reader of a close-year lock — check-filed and the run's filed-
# year check, `tjs years`, the next year's carryover, estimate, amt,
# close-year and handoff — applies the same check (read_lock), so a
# lock one of them refuses is never taken as sound by another: the
# carryover ledger dropped a lock the estimate refused and printed a
# 0.00 carryforward, and took a lock closed under the other country's
# law as the filed figure 0.00 (2026-10-25 audit robustness#1,
# year-end#1, year-end#6).

class LockProblem(ValueError):
    """A close-year lock no reader may use: unreadable, damaged, or
    closed under the other country's law. The message names the file
    and the fix."""


LOCK_FIX = ("restore the lock from git or re-close that year (`tjs "
            "close-year --force` in its project)")


def _unique_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """json.loads object_pairs_hook: a key written twice in one object
    is refused — json.loads keeps the second in silence (a balance
    written twice, 2026-10-25 audit year-end#3)."""
    out: Dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"the key {k!r} is written twice in one "
                             f"object")
        out[k] = v
    return out


# The lists of rows a lock holds, each row naming its security in `symbol`,
# and the {group: {symbol: ...}} tables (backlog TJ-5150).
_LOCK_ROW_LISTS = ("dispositions", "filed_dispositions", "settle_next_year",
                   "boundary_rows")
_LOCK_SYMBOL_TABLES = ("year_end", "year_end_lots", "year_end_shorts")


def _lock_symbol(sym: str) -> str:
    """A lock's symbol as the books spell it; one the books would refuse
    (`//QZM26.US`: FuturesSymbolError) as written, since a lock is a
    record and the year's .tt line is refused, naming it."""
    try:
        return canonical_futures_symbol(sym)
    except ValueError:
        return sym


def _merge_held(a: Any, b: Any) -> Any:
    """One security's two entries under one spelling: a position's or a
    short's figures summed ({qty, acb, deferred}), two lists of lots in
    one list by acquisition date; None when they are not of that shape
    (the record's validator then names the field)."""
    if isinstance(a, list) and isinstance(b, list):
        if all(isinstance(x, dict) for x in a + b):
            return sorted(a + b, key=lambda x: str(x.get("date") or ""))
        return None
    if isinstance(a, dict) and isinstance(b, dict) and all(
            finite_num(v) for v in list(a.values()) + list(b.values())):
        return {k: a.get(k, 0.0) + b.get(k, 0.0) for k in {**a, **b}}
    return None


def fold_lock_symbols(doc: Any) -> Any:
    """`doc` (a lock read from filed/<year>.json) with every symbol it holds
    as the books spell it now: a future's `/` or `\\` prefix `F:`
    (lib/futures_prefixes canonical_futures_symbol). A year closed by an
    older taxjson keeps a .tt line's `/QZM26.US`; read as written, the
    next year's `tjs handoff` found it held under two spellings (backlog
    TJ-5150). A security held under both spellings is one: its figures
    summed, its lots joined. Anything not of the shape close-year writes is
    left for the validators."""
    if not isinstance(doc, dict):
        return doc
    for key in _LOCK_ROW_LISTS:
        rows = doc.get(key)
        for r in rows if isinstance(rows, list) else ():
            if isinstance(r, dict) and isinstance(r.get("symbol"), str):
                r["symbol"] = _lock_symbol(r["symbol"])
    for key in _LOCK_SYMBOL_TABLES:
        table = doc.get(key)
        for g, held in (table.items() if isinstance(table, dict) else ()):
            if not isinstance(held, dict):
                continue
            out: Dict[str, Any] = {}
            for sym, v in held.items():
                k = _lock_symbol(sym)
                out[k] = v if k not in out else _merge_held(out[k], v)
                if out[k] is None:
                    break
            else:
                table[g] = out
    return doc


def load_lock_json(path: Path) -> Any:
    """The JSON document of a lock file (UTF-8, a BOM tolerated). A key
    written twice in one object raises ValueError, as do bad JSON and
    text that is not UTF-8 (UnicodeDecodeError); OSError propagates. Its
    symbols are read as the books spell them (fold_lock_symbols)."""
    return fold_lock_symbols(json.loads(
        Path(path).read_text(encoding="utf-8-sig"),
        object_pairs_hook=_unique_keys))


def file_year(path: Path) -> Optional[int]:
    """The year a lock's file name gives (filed/<year>.json), else
    None (a prior_year_record may be named anything)."""
    try:
        return int(Path(path).stem)
    except ValueError:
        return None


def lock_damage(lock: Any, year: Any, label: str) -> Optional[str]:
    """Why a lock document is damaged, as a sentence naming it
    (`label`), else None: its shape (lock_shape_problem — an object, a
    per-account table, finite figures, a `year` field that is `year`,
    the file's) and its `carryforwards` block
    (block_problem — each present section with its closing
    balance, numbers, one entry per year, no negative balance), and its
    `fx_cash_v2` close (fx_cash_v2.close_problem). `year` None: the
    year field is not judged."""
    if year is None and isinstance(lock, dict):
        year = lock.get("year")
    prob = lock_shape_problem(lock, year)
    if prob:
        return f"{label} {prob}"
    if isinstance(lock, dict) and lock.get("carryforwards") is not None:
        prob = block_problem(lock["carryforwards"])
        if prob:
            return f"{label}: {prob}"
    if isinstance(lock, dict) and lock.get("fx_cash_v2") is not None:
        # The FX-on-cash ledger v2's close, the next year's opening
        # pool: the ledger refuses a damaged one, so the lock is damaged
        # (backlog TJ-0068).
        from taxjson.lib.fx_cash_v2 import close_problem
        prob = close_problem(lock["fx_cash_v2"], year)
        if prob:
            return f"{label}: {prob}"
    return None


REFILE_DISPOSITIONS = ("tjs close-year --force --filed-dispositions "
                       "<CSV>")


def filed_rows_problem(lock: Any, year: Any, label: str) -> Optional[str]:
    """Why the loss a lock carried cannot be read, when its
    `filed_dispositions` (the sales a return prepared with another tool
    reported) hold a row dated outside the lock's year: a sentence
    naming the lock, the rows and the re-close; else None. close-year
    refuses to write such a lock since TJ-0298, but one an older
    release wrote counted the other year's sales in its filed_totals
    and, in Canada, in the net capital loss it carried forward: the
    next year's estimate, carryover and handoff stop until that year is
    re-closed (backlog TJ-0347). `year` None: the lock's own field."""
    if not isinstance(lock, dict) or not isinstance(
            lock.get("filed_dispositions"), list):
        return None
    if year is None:
        year = lock.get("year")
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    other = filed_rows_outside(lock["filed_dispositions"], y)
    if not other:
        return None
    shown = ", ".join(other[:5]) + (f" and {len(other) - 5} more"
                                    if len(other) > 5 else "")
    return (f"{label} records {len(other)} filed disposition(s) dated "
            f"outside {y} ({shown}): its filed totals and the loss it "
            f"carried forward count another year's sales (a lock an "
            f"older taxjson wrote), so it is not read — re-close {y} in "
            f"its project with `{REFILE_DISPOSITIONS}`, the CSV holding "
            f"the sales the {y} return reported ({y}'s rows only)")


def missing_lock_problem(path: Path, label: Optional[str] = None
                         ) -> Optional[str]:
    """When `path` is a year project's filed/<year>.json that is gone
    while that project shows the year was closed: its taxjson.toml still
    carries close-year's freeze note (project_layout._freeze_noted),
    or it still holds the reports close-year kept as filed
    (filed/<year>/reports/: a year closed as a single folder, then moved
    by `migrate --to-years`, has no freeze note) — a sentence naming it
    (`label`, default the path) and the fix; else None: a year never
    closed has no lock to miss. The next year read such a lock as no
    record at all: no carried loss, no foreign-cash pool (backlog
    TJ-0272)."""
    import os
    p = Path(path)
    y = file_year(p)
    if y is None or p.parent.name != "filed" or os.path.lexists(p):
        return None
    from taxjson.lib import project_layout as _PL
    if _PL._freeze_noted(p.parent.parent):
        why = (f"the {y} project's taxjson.toml still carries close-year's "
               f"freeze note")
    elif _filed_reports_kept(p.parent / str(y) / "reports", y):
        why = (f"the {y} project holds the reports close-year kept as "
               f"filed (filed/{y}/reports/)")
    else:
        return None
    return (f"{label or p}: the {y} close-year lock is missing, while "
            f"{why} (the year was closed) — restore filed/{y}.json from "
            f"git or a backup, or run `tjs close-year` in the {y} "
            f"project again; until then what {y} carried forward is "
            f"unknown")


def _filed_reports_kept(d: Path, year: int) -> bool:
    """`d` (a filed/<year>/reports/ folder) holds a report close-year
    kept after the year ended: the year was closed
    (project_layout.filed_reports_kept)."""
    from taxjson.lib import project_layout as _PL
    return _PL.filed_reports_kept(d, year)


def missing_prior_lock(root: Path, settings: Mapping[str, Any]
                       ) -> Optional[Tuple[int, Path, str]]:
    """(year, path, text) of the lock [settings] prior_year_record
    names when it is missing_lock_problem's case; else None (no
    setting, a file there, a year never closed)."""
    settings = as_settings(settings)
    try:
        p = prior_record_setting(root, settings)
    except PriorRecordError:
        return None
    if p is None:
        return None
    text = missing_lock_problem(
        p, f"[settings] prior_year_record "
           f"({settings.prior_year_record})")
    if text is None:
        return None
    return int(Path(p).stem), Path(p), text


def read_lock(path: Path, settings: Any = None, *,
              label: Optional[str] = None,
              prior: bool = False,
              carried: bool = False) -> Dict[str, Any]:
    """Read a close-year lock and apply the one check every reader of a
    lock uses: readable JSON with no key written twice, lock_damage (the
    year field against the file's name), and — `settings` given — the
    country it was closed under (lock_country_problem). Returns the
    document; raises LockProblem naming the file (`label`, default the
    path) and the fix. `prior`: the lock is read as the previous year's
    record (estimate, amt, carryover), so a country mismatch is advised
    as a prior_year_record naming the wrong project's lock
    (lock_country_problem). `carried`: the lock is read for what it
    carried into the next year (its filed totals, the loss it carried
    forward): one whose filed dispositions hold another year's rows is
    refused too (filed_rows_problem, backlog TJ-0347)."""
    p = Path(path)
    label = label or str(p)
    try:
        doc = load_lock_json(p)
    except OSError as e:
        # A filed year's deleted lock says so (backlog TJ-0272).
        raise LockProblem(
            missing_lock_problem(p, label)
            or f"cannot read the close-year lock {label} "
               f"({e.strerror or e})") from None
    except UnicodeDecodeError as e:
        raise LockProblem(f"{label}: not UTF-8 text ({e.reason} at byte "
                          f"{e.start}) — {LOCK_FIX}") from None
    except ValueError as e:
        raise LockProblem(f"{label}: not valid JSON ({e}) — "
                          f"{LOCK_FIX}") from None
    damage = lock_damage(doc, file_year(p), label)
    if damage:
        raise LockProblem(f"{damage} — {LOCK_FIX}")
    if settings is not None:
        cp = lock_country_problem(doc, settings, label, prior=prior)
        if cp:
            raise LockProblem(cp)
    if carried:
        fr = filed_rows_problem(doc, file_year(p), label)
        if fr:
            raise LockProblem(fr)
    return doc


def partial_year_note(lock: Dict[str, Any], year) -> Optional[str]:
    """When the lock was taken on or before Dec 31 of its own year
    (`close-year --force` on an open year): a sentence saying it is a
    snapshot, not a filed return — every lock consumer says so instead
    of certifying it (S045-23, A2-0679, A2-1164). None otherwise, and
    for a lock without a readable closed_at."""
    if not isinstance(lock, dict):
        return None
    at = str(lock.get("closed_at") or "")[:10]
    try:
        y = int(lock.get("year") or year)
    except (TypeError, ValueError):
        return None
    if len(at) != 10 or at > f"{y}-12-31":
        return None
    return (f"the {y} lock was taken on {at}, before the year ended "
            f"(close-year --force) — a snapshot, not a filed return; "
            f"close the year again after filing")


def _canonical_country(settings: Dict[str, Any]) -> str:
    """lib/country.settings_country: missing / unknown raises."""
    from taxjson.lib.country import settings_country
    return settings_country(settings)


class PriorRecordError(ValueError):
    """[settings] prior_year_record is not a path string."""


def prior_record_setting(root: Path, settings: Any
                         ) -> Optional[Path]:
    """The lock [settings] prior_year_record names (per-year project
    layout: "../2025/filed/2025.json"), resolved against the project
    root; None when the key is unset. A non-string value raises
    PriorRecordError with the same text `tjs run` refuses it with —
    handoff read 5 as the path <root>/5 (A2-1135)."""
    settings = as_settings(settings)
    configured = settings.prior_year_record
    if configured is None or configured == "":
        return None
    if not isinstance(configured, str):
        raise PriorRecordError(
            f"[settings] prior_year_record must be a path string "
            f"(got {configured!r}).")
    p = Path(configured).expanduser()
    return p if p.is_absolute() else (Path(root) / p)


def _lock_year(path: Path) -> Optional[int]:
    """The tax year a lock file records: its "year" key, else its
    <year>.json name. None when neither says."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("year") is not None:
            return int(doc["year"])
    except (OSError, ValueError, TypeError):
        pass
    try:
        return int(Path(path).stem)
    except ValueError:
        return None


def project_locks(root: Path, settings: Any
                  ) -> List[Tuple[int, Path, str]]:
    """Every filed-year lock this project answers to, as (year, path,
    where): the project's own filed/<year>.json ("local"), plus the lock
    [settings] prior_year_record names ("prior_year_record") when no
    local lock covers its year. In the documented per-year layout last
    year's lock lives only in the previous project; readers that looked
    only under <project>/filed/ (option-boundary, audit --year,
    carryover, the grant-since hint) said "no filed-year locks" and gave
    advice the lock contradicted (A2-0036, A2-0335, A2-0338, A2-0664).
    A configured record that does not exist is left out (handoff and the
    checklist report it); a non-string setting raises
    PriorRecordError."""
    settings = as_settings(settings)
    out: List[Tuple[int, Path, str]] = [
        (y, p, "local") for y, p in list_snapshots(root)]
    pr = prior_record_setting(root, settings)
    if pr is not None and pr.is_file():
        try:
            same = any(pr.resolve() == p.resolve() for _y, p, _w in out)
        except OSError:
            same = False
        y = _lock_year(pr)
        if y is None:
            try:
                y = int(settings.year) - 1
            except (TypeError, ValueError):
                y = None
        if (y is not None and not same
                and y not in {yy for yy, _p, _w in out}):
            out.append((y, pr, "prior_year_record"))
    out.sort(key=lambda t: t[0])
    return out


def record_of_label(lock: Path, label: Any) -> Optional[Tuple[int, Path]]:
    """(year, path) of the close-year record a carry-forward's `opening_source`
    names: a path relative to the lock's project (the folder above filed/) or
    absolute, then " (the YYYY close-year record)". None for any other label
    ([estimate] other_losses, none)."""
    import re as _re
    m = _re.fullmatch(r'(.+?) \(the (\d{4}) close-year record\)',
                      str(label or "").strip())
    if not m:
        return None
    p = Path(m.group(1))
    if not p.is_absolute():
        p = Path(os.path.normpath(Path(lock).resolve().parent.parent / p))
    return int(m.group(2)), p


def records_behind(locks: List[Tuple[int, Path, str]], before: int,
                   usable: Any) -> List[Tuple[int, Path, str]]:
    """`locks` (project_locks) with the records behind them, all the way back:
    the lock each one's net capital loss opening was taken from
    ("opening_source"), for the years before `before` that no lock covers yet.
    In year folders a project names only last year's lock, and the row before
    it was rebuilt: what that return carried back (T1A) is in its own lock, so
    the carryover ledger reads it and doesn't have to guess (backlog TJ-1574).
    `usable(path, year)`: the ledger's own reading of a lock (it refuses one it
    can't read, and a file this project never named must not stop it): a record
    that is gone, damaged, of another year or closed under the other country's
    law is left out, with those behind it, and the year stays rebuilt."""
    out = list(locks)
    have = {y for y, _p, _w in out}
    queue = [p for _y, p, _w in out]
    while queue:
        lock = queue.pop()
        try:
            label = load_lock_json(lock)[
                "carryforwards"]["net_capital_loss"]["opening_source"]
        except (OSError, ValueError, TypeError, KeyError):
            continue
        rec = record_of_label(lock, label)
        if (rec is None or rec[0] in have or rec[0] >= before
                or not usable(rec[1], rec[0])):
            continue
        have.add(rec[0])
        out.append((rec[0], rec[1], "opening_source"))
        queue.append(rec[1])
    out.sort(key=lambda t: t[0])
    return out


def locked_year_flags(root: Path, settings: Any) -> List[str]:
    """`--locked-year Y --locked-record PATH` for every filed-year lock
    of a US project (the gains engine then books a wash-sale basis add
    that reaches a sale in a filed year in the loss's year instead,
    US-WASH-22, unless the lock's own row for that sale has it: backlog
    TJ-0463). Canada has no such flag ([] — lib/country.FLAG_COUNTRY)."""
    if _canonical_country(settings) != "usa":
        return []
    try:
        locks = project_locks(root, settings)
    except ValueError:
        return []
    out: List[str] = []
    for y, p, _w in locks:
        out += ["--locked-year", str(y), "--locked-record",
                os.path.abspath(p)]
    return out


def lock_label(root: Path, path: Path) -> str:
    """A short name for a lock in messages: filed/<year>.json for the
    project's own, else the path as configured."""
    try:
        return str(Path(path).relative_to(Path(root)))
    except ValueError:
        return str(path)


def list_snapshots(root: Path) -> List[Tuple[int, Path]]:
    d = Path(root) / "filed"
    out = []
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                out.append((int(p.stem), p))
            except ValueError:
                cli_diag.warn(PROG, f"ignoring non-year file {p.name}")
    return out


# What a present section must carry: the balance carried OUT of the
# year, which the next year's estimate reads (resolve_losses,
# _from_lock). A section with an opening and no closing passed as
# well-formed, and the next year read the absent closing as zero: a loss
# carry-forward silently reset (2026-10 audit B39).
_BLOCK_CLOSING = {"net_capital_loss": ("closing",),
                  "capital_loss": ("st_closing", "lt_closing"),
                  "minimum_tax": ("closing_by_year",)}
# The balances close-year writes as 0 or more (record_block; the T1A
# carry-back it takes out): a negative one is a hand edit. The next
# year read a negative closing as a loss that adds gains, and dropped a
# negative minimum-tax amount in silence (2026-10-25 audit year-end#3).
_BLOCK_BALANCES = {"net_capital_loss": ("closing", "carried_back"),
                   "capital_loss": ("st_closing", "lt_closing",
                                    "deduction_used"),
                   "minimum_tax": ("closing",)}
_BY_YEAR_KEYS = ("opening_by_year", "closing_by_year",
                 "recovered_by_year", "expired_by_year")


def _by_year_problem(where: str, d: Any) -> Optional[str]:
    """What is wrong with a lock's {year of origin: amount} table, or
    None: the checks [estimate] amt_carryover gets (amt_from_config) —
    a year key, one entry per year (`2024` and `02024` are one year:
    the second replaced the first in silence), a plausible year, an
    amount that is a number of 0 or more (2026-10-25 audit year-end#3;
    close-year writes str(year) keys and positive amounts)."""
    if not isinstance(d, dict):
        return f"{where} is not {{year: amount}}"
    spelled: Dict[int, str] = {}
    for k, a in d.items():
        ks = str(k).strip()
        if not ks.isdigit() or not finite_num(a):
            return f"{where} is not {{year: amount}}"
        y = int(ks)
        if y in spelled:
            return (f"{where} gives {y} twice ({spelled[y]!r} and "
                    f"{str(k)!r}) — one entry per year of origin")
        spelled[y] = str(k)
        if not _plausible_year(y):
            return f"{where}: {ks} is not a plausible year of origin"
        if float(a) < 0:
            return (f"{where}: {y} is {a!r}, a negative amount (close-year "
                    f"writes 0 or more)")
    return None


def block_problem(block: Any) -> Optional[str]:
    """None when a lock's `carryforwards` block has the shape close-year
    writes; else what is wrong (lock_damage — the one
    check every reader of a lock applies — and handoff use it). A
    present section without its closing balance (_BLOCK_CLOSING) is a
    problem, never a zero; so is a negative balance (_BLOCK_BALANCES), a
    by-year table with a year twice or a negative amount
    (_by_year_problem), and a minimum tax closing that is not the sum
    of its closing_by_year."""
    if not isinstance(block, dict):
        return "carryforwards is not an object"
    for sec, keys in (("net_capital_loss", ("opening", "created",
                                            "applied", "closing",
                                            "carried_back")),
                      ("capital_loss", ("st_opening", "lt_opening",
                                        "st_closing", "lt_closing",
                                        "deduction_used",
                                        "deduction_available",
                                        "section_1256_gain",
                                        "section_1256_short_term",
                                        "section_1256_long_term")),
                      ("minimum_tax", ("created", "closing",
                                       "recovered_federal"))):
        s = block.get(sec)
        if s is None:
            continue
        if not isinstance(s, dict):
            return f"carryforwards.{sec} is not an object"
        for k in keys:
            if k in s and not finite_num(s[k]):
                return f"carryforwards.{sec}.{k} is not a number"
        for k in _BLOCK_CLOSING[sec]:
            if k not in s:
                return (f"carryforwards.{sec} has no {k} — the balance "
                        f"carried out of the year is missing (a truncated "
                        f"block)")
        for k in _BLOCK_BALANCES[sec]:
            if k in s and float(s[k]) < 0:
                return (f"carryforwards.{sec}.{k} is {s[k]!r}, a negative "
                        f"balance (close-year writes 0 or more)")
        for k in _BY_YEAR_KEYS:
            if s.get(k) is None:
                continue
            prob = _by_year_problem(f"carryforwards.{sec}.{k}", s[k])
            if prob:
                return prob
        if sec == "minimum_tax" and "closing" in s:
            # close-year writes closing as the rounded sum of
            # closing_by_year (tax_estimate): an amount edited in one
            # and not the other moved the carryover the next year
            # recovers with no word.
            parts = s["closing_by_year"]
            total = sum(float(a) for a in parts.values())
            if abs(total - float(s["closing"])) > 0.01:
                return (f"carryforwards.minimum_tax.closing is "
                        f"{M.shown(float(s['closing']), 2):,.2f} but its "
                        f"closing_by_year adds up to {M.shown(total, 2):,.2f}")
    return None


def _d(s: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def filed_rows_outside(rows: List[Dict[str, Any]], year: int
                       ) -> List[str]:
    """`SYMBOL DATE` of each filed disposition whose date (the date the
    return used) is not in `year`: a lock records ONE return, so another
    year's row summed into its filed_totals and carried that year's gain
    or loss into the next one (backlog TJ-0298). A row with no readable
    date is named too."""
    out = []
    for r in rows or []:
        r = r if isinstance(r, dict) else {}
        d = _d(r.get("date"))
        if d is None or d.year != int(year):
            out.append(f"{r.get('symbol')} {r.get('date')}")
    return out
