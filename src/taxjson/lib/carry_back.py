"""Whether a T1A carry-back's split rests on sales the carryover ledger left out.

One rule for what reads the ledger: the estimate, `close-year`'s stop, handoff's
note and the checklist's carryover step (backlog TJ-1453, TJ-1455, TJ-1469). It
reads a `tjs carryover --json` document, and takes a damaged one: the checklist
reads the child's output as it comes, and `cli.common._ledger_doc` refuses it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# How the checklist's carryover step opens when a split rests on left-out sales.
# `close-year` reads it to tell this item, answered yes, from the step's others.
SPLIT_ATTENTION = "[carryover] claimed folds on nets where"
_YEARS_SHOWN = 5


def _is_year(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_amount(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = doc.get("rows")
    return [x for x in rows if isinstance(x, dict) and _is_year(x.get("year"))
            ] if isinstance(rows, list) else []


def ledger_left_out(doc: Dict[str, Any], year: int) -> Optional[Dict[str, Any]]:
    """{years, sales, and `undated` when so} of the sales with an unknown cost
    whose results can still move `year`'s figures in this ledger, None when no
    such sale. A later year's cannot, and neither can one at or before the last
    locked year before `year`: the balance restarts from that lock's. A sale
    with no year cannot be cleared by either (backlog TJ-1453, TJ-1455)."""
    rows = _rows(doc)
    locked = max((x["year"] for x in rows
                  if x.get("balance_from_lock") and x["year"] < year),
                 default=None)

    def counts(y: int) -> bool:
        return y <= year and (locked is None or y > locked)

    listed = doc.get("incomplete_years")
    years = sorted(
        {y for y in (listed if isinstance(listed, list) else [])
         if _is_year(y) and counts(y)}
        | {x["year"] for x in rows if x.get("incomplete") and counts(x["year"])})
    excluded = doc.get("excluded_unknown_cost")
    sales = [e for e in (excluded if isinstance(excluded, list) else [])
             if isinstance(e, dict)
             and (not _is_year(e.get("year")) or counts(e["year"]))]
    undated = any(not _is_year(e.get("year")) for e in sales)
    if not years and not undated:
        return None
    return {"years": years, "sales": sales,
            **({"undated": True} if undated else {})}


def split_left_out(doc: Dict[str, Any], year: int) -> Optional[Dict[str, Any]]:
    """`ledger_left_out`, when there is a split for those sales to move: the
    ledger's row for `year` shows a loss or a carry-back. A carried-in balance
    alone is no split, but the row can carry back with no loss this year, when
    the lock before it recorded none (backlog TJ-1454). A row on a filed return
    is spared the year's own sales (`filed_left_out`; TJ-1490)."""
    left = ledger_left_out(doc, year)
    if left is None:
        return None
    for x in _rows(doc):
        if x["year"] != year:
            continue
        # A figure of another kind is no split; NaN compares False.
        if (_is_amount(x.get("carried_back")) and x["carried_back"] > 0.005) \
                or (_is_amount(x.get("net_gain")) and x["net_gain"] < -0.005):
            return (filed_left_out(left, year)
                    if x.get("net_source") == "filed_return" else left)
    return None


def filed_left_out(left: Optional[Dict[str, Any]], year: int
                   ) -> Optional[Dict[str, Any]]:
    """A `split_left_out` result for a year whose net is the filed return's:
    that figure already holds the year's own sales, so a cost found later
    cannot move it, as with a locked year. An earlier year's sale and one with
    no year still can (backlog TJ-1474)."""
    if not left:
        return None
    years = [y for y in left.get("years") or [] if y != year]
    sales = [s for s in left.get("sales") or [] if s.get("year") != year]
    if not years and not left.get("undated"):
        return None
    return {**left, "years": years, "sales": sales}


def left_out_text(left: Dict[str, Any]) -> str:
    """The years of a `ledger_left_out` result (or of a lock's record of one)
    for a sentence, short enough for a line of its own."""
    years = left.get("years")
    years = [str(y) for y in years] if isinstance(years, list) else []
    if len(years) > _YEARS_SHOWN:
        years[_YEARS_SHOWN:] = [f"and {len(years) - _YEARS_SHOWN} more"]
    if left.get("undated"):
        years.append("undated")
    return ", ".join(years)


def years_text(years: List[int]) -> str:
    names = [str(y) for y in years]
    return (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1
            else "".join(names))


def cant_tell(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A Canadian ledger's `carry_back_cant_tell`, None when it can tell (or
    the entry is no such thing): {years, books, answered, if_none, if_all}.
    `years` are rebuilt years before the project year that could have carried
    a loss back to a [carryover] claimed amount, with no lock to say what their
    return did; `books` and `least`: {year: the most and the least it carried
    back, by the books and any balance the person gave}; `answered`: the
    [carryover] carried_back entries in use; `if_none` and `if_all` are the
    project year's carried_back and the carryforward its lock would record
    (with `loss_left`, the loss the estimate has to split) at the least and
    at the most. They differ, so the ledger's rows are working, not an answer
    (backlog TJ-1574; bin/taxjson_carryover `_canada_ledger`)."""
    ct = doc.get("carry_back_cant_tell") if isinstance(doc, dict) else None
    if not isinstance(ct, dict):
        return None
    years, books = ct.get("years"), ct.get("books")
    if not (isinstance(years, list) and years and all(_is_year(y) for y in years)
            and isinstance(books, dict)
            and all(_is_amount(books.get(str(y))) for y in years)):
        return None
    answered = ct.get("answered", {})
    if not (isinstance(answered, dict)
            and all(_is_amount(v) for v in answered.values())):
        return None
    for side in ("if_none", "if_all"):
        fig = ct.get(side)
        if not (isinstance(fig, dict) and _is_amount(fig.get("carried_back"))
                and _is_amount(fig.get("carryforward"))):
            return None
    return ct


# The ways out, for the views with room for them.
CANT_TELL_WAYS = (
    "Any one of these settles it. In taxjson.toml, `[carryover] carried_back = "
    "{ YEAR = AMOUNT }`: the net capital loss that year's return carried back "
    "on its T1A, 0 for none (any amount up to the claims it could meet). Or "
    "that year's close-year lock in this project's filed/. Or, if the lock is "
    "lost, its folder made again (its year, prior_year_record and the shared "
    "inputs), `tjs run` and `tjs close-year` there. If the return carried "
    "back a loss from an export missing here, adding the export may settle it "
    "too.")


def cant_tell_act(doc: Dict[str, Any]) -> str:
    """The default view's one line."""
    ct = cant_tell(doc) or {"years": []}
    return (f"can't tell what {years_text(ct['years'])} carried back (T1A): "
            f"set [carryover] carried_back")


def _answer_line(ct: Dict[str, Any], books: bool) -> str:
    """The exact taxjson.toml line for one reading, the entries in use kept."""
    entries = {str(y): f"{v:.2f}" for y, v in ct.get("answered", {}).items()}
    for y in ct["years"]:
        amount = ct["books"][str(y)] if books else float(
            (ct.get("least") or {}).get(str(y)) or 0.0)
        entries[str(y)] = f"{amount:.2f}" if amount > 0.005 else "0"
    return ("`carried_back = { "
            + ", ".join(f"{y} = {v}" for y, v in sorted(entries.items())) + " }`")


def cant_tell_text(doc: Dict[str, Any]) -> str:
    """`cant_tell` in one line, with the lines that answer it; "" when the
    ledger can tell."""
    ct = cant_tell(doc)
    if not ct:
        return ""
    years = years_text(ct["years"])
    one = len(ct["years"]) == 1
    # The figure that differs: the carry-back, else the balance carried out.
    key, verb = ("carried_back", "back") if abs(
        ct["if_all"]["carried_back"] - ct["if_none"]["carried_back"]) > 0.005 else (
        "carryforward", "forward")
    return (f"these books can't tell whether the "
            f"{'return' if one else 'returns'} of {years} carried a loss back "
            f"(T1A) to the [carryover] claimed amounts, or this year's does. "
            f"Under [carryover] in taxjson.toml add {_answer_line(ct, True)} "
            f"if {'it' if one else 'they'} did, as the books read (this year "
            f"then carries {verb} {ct['if_all'][key]:,.2f}), or "
            f"{_answer_line(ct, False)} if not (then "
            f"{ct['if_none'][key]:,.2f}); or put "
            f"{'its' if one else 'their'} close-year "
            f"{'lock' if one else 'locks'} in filed/")
