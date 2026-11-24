"""Whether a T1A carry-back's split rests on sales the carryover ledger left out.

One rule for what reads the ledger: the estimate, `close-year`'s stop, handoff's
note and the checklist's carryover step (backlog TJ-1453, TJ-1455, TJ-1469). It
reads a `tjs carryover --json` document, and takes a damaged one: the checklist
reads the child's output as it comes, and `cli.common._ledger_doc` refuses it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from taxjson.model import money as M

# What a Canadian ledger's nets leave out of a year's return (the ledger's scope
# note; tax-logic CA-CARRY-04): a balance carried in can't be set against the
# nets of a rebuilt year to say what its return carried back (backlog TJ-4886).
NOT_IN_NETS = ("t3_box_21", "t5_box_18_unnamed", "fx_on_cash")
NOT_IN_NETS_TEXT = (
    "T3 box 21 capital gains, T5 box 18 gains not named in "
    "[[capital_gains_dividends]] and the FX gain or loss on foreign cash "
    "(line 15300)")
GAIN_NOT_IN_NETS_TEXT = (
    "T3 box 21, T5 box 18 not named in [[capital_gains_dividends]], an FX "
    "gain on foreign cash")
LOSS_NOT_IN_NETS_TEXT = "an FX loss on foreign cash"

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
    (backlog TJ-1574; bin/taxjson_carryover `_canada_ledger`). `by_books_nets`,
    when a balance the person gave follows those years: what it would say on
    the books' nets alone (`_by_books_text`; backlog TJ-4886). `books_to`:
    {year: {earlier year: amount}} for each of `years` whose carry-back, as the
    books read, could have gone to more than one [carryover] claimed year: the
    split the ledger proposes, the oldest first (backlog TJ-4882)."""
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
            and all(_is_amount(v) or _is_split(v) for v in answered.values())
            and _is_splits(ct.get("books_to", {}))):
        return None
    for side in ("if_none", "if_all"):
        fig = ct.get(side)
        if not (isinstance(fig, dict) and _is_amount(fig.get("carried_back"))
                and _is_amount(fig.get("carryforward"))):
            return None
    return ct


def not_in_nets(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A Canadian ledger's `carried_in_not_in_nets`: for each balance the
    person gave that their [carryover] carried_back entries don't leave on the
    books' nets, {into, carried_in, leaves, leaves_least, years, and
    gain_not_in_nets or loss_not_in_nets; carried_in_lock when the balance is
    the opening that year's lock took from [estimate] other_losses, and
    answered_in_lock when the answers are a lock's record, not this
    project's entries}. [] with none; ValueError for an
    entry that is not whole: what reads the ledger says so, never less than
    the ledger did (backlog TJ-4886)."""
    entries = doc.get("carried_in_not_in_nets") if isinstance(doc, dict) else None
    if entries is None:
        return []
    ok = isinstance(entries, list) and all(
        isinstance(h, dict) and _is_year(h.get("into"))
        and isinstance(h.get("years"), list) and h["years"]
        and all(_is_year(y) for y in h["years"])
        and all(_is_amount(h.get(k)) for k in ("carried_in", "leaves", "leaves_least"))
        and (_is_amount(h.get("gain_not_in_nets"))
             != _is_amount(h.get("loss_not_in_nets")))
        for h in entries)
    if not ok:
        raise ValueError(f"`tjs carryover --json` gave carried_in_not_in_nets "
                         f"{entries!r}")
    return entries


def not_in_nets_text(h: Dict[str, Any]) -> str:
    """One `not_in_nets` entry as a sentence. Both figures are the person's,
    from filed forms, and both are used as given: the sentence says what must
    then be true of those returns, and what to check."""
    gain = _is_amount(h.get("gain_not_in_nets"))
    amount = h["gain_not_in_nets"] if gain else h["loss_not_in_nets"]
    one = len(h["years"]) == 1
    answers = (f"the answers {h['answered_in_lock']}'s close-year lock recorded "
               f"(carry_back_stated)" if _is_year(h.get("answered_in_lock"))
               else "your [carryover] carried_back entries")
    balance = (f"{h['carried_in_lock']}'s lock opened with {h['carried_in']:,.2f} "
               f"(its [estimate] other_losses)" if _is_year(h.get("carried_in_lock"))
               else f"you carried in {h['carried_in']:,.2f} ([estimate] other_losses)")
    return (
        f"{answers} leave {h['leaves']:,.2f} "
        f"carried into {h['into']} on these books' nets"
        + (f" ({h['leaves_least']:,.2f} if the gains between took all they "
           f"could)" if h["leaves"] - h["leaves_least"] > 0.005 else "")
        + f"; {balance}, "
        f"{amount:,.2f} {'less' if gain else 'more'}. Both hold only if the "
        f"{'return' if one else 'returns'} of {years_text(h['years'])} had "
        f"{amount:,.2f}"
        + (" or more of capital gains" if gain else " of capital losses")
        + " that are not in these nets ("
        + (GAIN_NOT_IN_NETS_TEXT if gain else LOSS_NOT_IN_NETS_TEXT)
        + "): check " + ("those answers" if _is_year(h.get("answered_in_lock"))
                         else "the entries") + " and that balance against "
        + ("that return" if one else "those returns"))


def _is_split(v: Any) -> bool:
    """A carry-back by the year it went to: {year: amount}."""
    return isinstance(v, dict) and all(
        str(t).isdigit() and _is_amount(a) for t, a in v.items())


def _is_splits(v: Any) -> bool:
    return isinstance(v, dict) and all(
        str(y).isdigit() and _is_split(to) for y, to in v.items())


def split_cant_tell(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A Canadian ledger's `carry_back_split_cant_tell`, None with none:
    {years, unchecked, readings, answered, more}. `years` are years before the
    project year whose carry-back could have gone to more than one [carryover]
    claimed year (or, those in `unchecked`, may not have happened at all:
    rebuilt years no lock checks), with no record of the years it went to: no
    by-target [carryover] carried_back entry and no lock's `carried_back_to`.
    `readings`: one for each set of figures the project year can have, the
    ledger's own order first: {to: {year: {earlier year: amount}} (what each
    year in question carried back there; {} for nothing), carried_back,
    carryforward, carried_back_to (the project year's own split)}. They
    differ from one another or from the ledger's rows, so those are working,
    not an answer. `more`: there are readings beyond those listed. `answered`:
    the entries in use (bin/taxjson_carryover `_Readings._orders`; backlog
    TJ-4882). ValueError for an entry that is not whole: what reads the ledger
    must not take a figure the ledger said it can't tell."""
    ct = doc.get("carry_back_split_cant_tell") if isinstance(doc, dict) else None
    if ct is None:
        return None
    readings = ct.get("readings") if isinstance(ct, dict) else None
    years = ct.get("years") if isinstance(ct, dict) else None
    ok = (isinstance(years, list) and years and all(_is_year(y) for y in years)
          and isinstance(readings, list) and readings
          and all(isinstance(r, dict) and _is_splits(r.get("to")) and r["to"]
                  and _is_amount(r.get("carried_back"))
                  and _is_amount(r.get("carryforward"))
                  and _is_split(r.get("carried_back_to", {})) for r in readings)
          and isinstance(ct.get("unchecked", []), list)
          and isinstance(ct.get("answered", {}), dict)
          and all(_is_amount(v) or _is_split(v)
                  for v in ct.get("answered", {}).values()))
    if not ok:
        raise ValueError(f"`tjs carryover --json` gave carry_back_split_cant_tell "
                         f"{ct!r}")
    return ct


def entry_text(v: Any) -> str:
    """One year's [carryover] carried_back value as taxjson.toml takes it: a
    total, or `{ YEAR = AMOUNT }` by the year it went to (0 for none)."""
    if isinstance(v, dict):
        named = sorted((int(t), a) for t, a in v.items() if a > 0.005)
        return ("{ " + ", ".join(f"{t} = {a:.2f}" for t, a in named) + " }"
                if named else "0")
    return f"{v:.2f}"


def split_text(to: Any) -> str:
    """A carry-back by the year it went to, for a sentence: "2020 40.00, 2021
    20.00"; "" with none."""
    named = sorted((int(t), a) for t, a in (to or {}).items() if a > 0.005)
    return ", ".join(f"{t} {a:,.2f}" for t, a in named)


def trim_to(to: Any, amount: float) -> Dict[str, float]:
    """A carry-back by the year it went to, cut to `amount` in all: the
    latest year gives back first, as the ledger's own give-back does."""
    out = {str(t): float(a) for t, a in sorted((to or {}).items(),
                                               key=lambda kv: int(kv[0]))}
    over = sum(out.values()) - amount
    for t in sorted(out, key=int, reverse=True):
        give = min(out[t], max(0.0, over))
        out[t], over = M.shown(out[t] - give, 2), over - give
    return {t: a for t, a in out.items() if a > 0.005}


def went_to(to: Any) -> str:
    """A carry-back by the year it went to, to follow its amount: " to 2021",
    " to 2020 (40.00) and 2021 (20.00)"; "" with none."""
    named = sorted((int(t), a) for t, a in (to or {}).items() if a > 0.005)
    if len(named) < 2:
        return "".join(f" to {t}" for t, _a in named)
    parts = [f"{t} ({a:,.2f})" for t, a in named]
    return " to " + ", ".join(parts[:-1]) + " and " + parts[-1]


def _split_line(ct: Dict[str, Any], reading: Dict[str, Any]) -> str:
    """The exact taxjson.toml line for one reading, the entries in use kept."""
    entries = {str(y): entry_text(v) for y, v in ct.get("answered", {}).items()}
    entries.update({str(y): entry_text(to) for y, to in reading["to"].items()})
    return ("`carried_back = { "
            + ", ".join(f"{y} = {v}" for y, v in sorted(entries.items())) + " }`")


def reading_when(ct: Dict[str, Any], r: Dict[str, Any]) -> str:
    """One reading of a `split_cant_tell` entry, to follow "if": what each
    year in question carried back, and to which years."""
    parts = []
    for y, to in sorted(r["to"].items()):
        total = sum(a for a in to.values() if a > 0.005)
        parts.append(f"{y} carried back {total:,.2f}{went_to(to)}" if total > 0.005
                     else f"{y} carried nothing back")
    return (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1
            else "".join(parts))


def _split_cant_tell_text(ct: Dict[str, Any]) -> str:
    """`split_cant_tell` in one line: what is not on record, and the line for
    each reading with the figures it gives."""
    years = years_text(ct["years"])
    parts = [
        f"{_split_line(ct, r)} if {reading_when(ct, r)} (this year then carries "
        f"back {r['carried_back']:,.2f}{went_to(r.get('carried_back_to'))} and "
        f"{r['carryforward']:,.2f} forward)" for r in ct["readings"]]
    one = len(ct["years"]) == 1
    return (f"these books can't tell which [carryover] claimed years the "
            f"carry-back of {years} went to, with more than one in reach"
            + (", or whether it was made" if ct.get("unchecked") else "")
            + f": a T1A names them, and no lock or [carryover] carried_back entry "
            f"records them. What this year carries back, and to which years, "
            f"rests on it. From the T1A of {years}, under [carryover] in "
            f"taxjson.toml add "
            + "; or ".join(parts)
            + f"; or the amounts {'its' if one else 'their'} T1A did carry back, "
              f"under the years it names"
            + (" (there are more readings than these)" if ct.get("more") else ""))


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
SPLIT_WAYS = (
    "In taxjson.toml, `[carryover] carried_back = { YEAR = { TARGET = AMOUNT } "
    "}`: the net capital loss that year's return carried back on its T1A, by "
    "the year it went to (one of the 3 years before it with a `claimed` "
    "amount; several are `{ 2020 = 10.00, 2021 = 30.00 }`). A plain `YEAR = "
    "AMOUNT` is a total and does not say the years. `tjs close-year` records "
    "the answer, and the next years' projects read it there.")


def cant_tell_act(doc: Dict[str, Any]) -> str:
    """The default view's one line."""
    ct = cant_tell(doc)
    if not ct:
        return (f"can't tell which claimed years "
                f"{years_text((split_cant_tell(doc) or {'years': []})['years'])} "
                f"carried back to (T1A): set [carryover] carried_back by year")
    return (f"can't tell what {years_text(ct['years'])} carried back (T1A): "
            f"set [carryover] carried_back")


def _answer_line(ct: Dict[str, Any], books: bool) -> str:
    """The exact taxjson.toml line for one reading, the entries in use kept."""
    entries = {str(y): entry_text(v) for y, v in ct.get("answered", {}).items()}
    for y in ct["years"]:
        amount = ct["books"][str(y)] if books else float(
            (ct.get("least") or {}).get(str(y)) or 0.0)
        # By the year it went to, where another split was open (TJ-4882).
        to = (ct.get("books_to") or {}).get(str(y)) if books else None
        entries[str(y)] = (entry_text(to) if to and amount > 0.005 else
                           f"{amount:.2f}" if amount > 0.005 else "0")
    return ("`carried_back = { "
            + ", ".join(f"{y} = {v}" for y, v in sorted(entries.items())) + " }`")


def _by_books_text(ct: Dict[str, Any]) -> str:
    """What a balance the person gave would say of the years asked about, on
    the books' nets, and why the ledger doesn't take it: the nets leave out
    capital gains and losses those returns can hold, so the figure is a hint
    for the answer, never the answer (backlog TJ-4886). "" for an entry with
    none, or one that is not whole."""
    hints = ct.get("by_books_nets")
    out: List[str] = []
    for h in hints if isinstance(hints, list) else []:
        if not (isinstance(h, dict) and isinstance(h.get("years"), list)
                and h["years"] and all(_is_year(y) for y in h["years"])
                and _is_year(h.get("into")) and all(
                    _is_amount(h.get(k)) for k in (
                        "carried_in", "books_leave", "at_least", "at_most"))):
            return ""
        years = years_text(h["years"])
        low, high = h["at_least"], h["at_most"]
        said = (f"{low:,.2f}" if high - low <= 0.005 else
                f"at least {low:,.2f} and at most {high:,.2f}")
        out.append(
            f"On these books' nets alone, the {h['carried_in']:,.2f} you carried "
            f"into {h['into']} ([estimate] other_losses) would mean {years} "
            f"carried back {said}"
            + (" in all" if len(h["years"]) > 1 else "")
            + f" (the nets leave {h['books_leave']:,.2f} with nothing carried "
            f"back). Not in those nets: {NOT_IN_NETS_TEXT}. One of them on the "
            f"{'return' if len(h['years']) == 1 else 'returns'} of {years} "
            f"changes that figure, so the ledger does not take it as the "
            f"answer")
    return ". ".join(out)


def cant_tell_text(doc: Dict[str, Any]) -> str:
    """`cant_tell` (else `split_cant_tell`) in one line, with the lines that
    answer it; "" when the ledger can tell. ValueError for a
    `carry_back_split_cant_tell` that is not whole."""
    ct = cant_tell(doc)
    if not ct:
        split = split_cant_tell(doc)
        return _split_cant_tell_text(split) if split else ""
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
            f"{'lock' if one else 'locks'} in filed/"
            + (f". Where the line names the years the loss went back to, it is "
               f"the oldest claimed year first: write the years and amounts "
               f"{'its' if one else 'their'} T1A names"
               if ct.get("books_to") else "")
            + (". " + _by_books_text(ct) if _by_books_text(ct) else ""))


def lock_split(ncl: Dict[str, Any], back: float, to: Any, how: Any,
               stated_to: Any) -> str:
    """Record the years the lock's carry-back goes to (`carried_back_to`; with
    `carried_back_split` "oldest_first" when they are the ledger's proposal and
    another split was open) and the earlier years' carry-backs whose years the
    person gave (`carry_back_stated_to`): the next years' ledgers read both,
    so which [carryover] claimed year a carry-back went to is asked once
    (backlog TJ-4882). Returns why it can't, "" when done: figures that are no
    amounts by year, or years that don't add up to the carry-back. With no
    years given at all (`to` None: an estimate that names none) the lock
    records the total alone, and is read as a lock written before this was: a
    total met the oldest year first, asked about where that matters. The years
    are footed to the carry-back, the oldest taking the cents between the
    ledger's rounded parts and its rounded total."""
    from taxjson.lib.tx_load import finite_num

    def by_year(v: Any) -> bool:
        return isinstance(v, dict) and all(
            str(t).isdigit() and finite_num(a) and a >= 0 for t, a in v.items())

    if stated_to is not None and not (isinstance(stated_to, dict) and all(
            str(y).isdigit() and by_year(v) for y, v in stated_to.items())):
        return f"the estimate gave carry_back_stated_to {stated_to!r}"
    if stated_to:
        ncl["carry_back_stated_to"] = stated_to
    if back <= 0.005 or to is None:
        return ""
    if not by_year(to) or how not in (None, "entry", "oldest_first"):
        return (f"the estimate gave no years for the {M.shown(back, 2):,.2f} "
                f"carried back (losses_carried_back_to {to!r}, carry_back_split "
                f"{how!r})")
    split = trim_to(to, back)
    said = M.shown(sum(to.values()), 2)
    if not split or M.cents_apart(sum(split.values()), back) > len(split) or (
            how == "entry" and M.cents_apart(said, back) > len(split)):
        return (f"the lock carries back {M.shown(back, 2):,.2f}, and the years "
                f"given for it add up to {said:,.2f} ({split_text(to) or 'none'})"
                + (": correct the entry for this year in [carryover] "
                   "carried_back" if how == "entry" else ""))
    first = min(split, key=int)
    split[first] = M.shown(split[first] + M.shown(back, 2) - sum(split.values()), 2)
    ncl["carried_back_to"] = split
    if how == "oldest_first":
        ncl["carried_back_split"] = how
    return ""


def by_year_said(ncl: Dict[str, Any]) -> str:
    """The years the lock's carry-back goes to, where there is more than one
    ("" otherwise: the lock has the year, `carried_back_to`)."""
    to: Any = ncl.get("carried_back_to")
    return (f"; carry-back by year: {split_text(to)}"
            if isinstance(to, dict) and len(to) > 1 else "")


def split_proposed_said(ncl: Dict[str, Any], year: int) -> str:
    """close-year's note when the years the lock's carry-back goes to are the
    ledger's proposal, another split open (`carried_back_split`), "" otherwise:
    the T1A names the years, so the person files these or says their own
    (backlog TJ-4882)."""
    to: Any = ncl.get("carried_back_to")
    if ncl.get("carried_back_split") != "oldest_first" or not isinstance(to, dict):
        return ""
    return (f"the T1A carry-back by year is `tjs carryover`'s proposal, the "
            f"oldest [carryover] claimed year first: {split_text(to)}. The lock "
            f"records it (carried_back_to), and the next years' ledgers read it. "
            f"If the T1A names other years, say so under [carryover] in "
            f"taxjson.toml, `carried_back = {{ {year} = {{ YEAR = AMOUNT }} }}`, "
            f"and close {year} again (--force).")
