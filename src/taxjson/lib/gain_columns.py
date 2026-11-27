"""The money columns of a Canadian return's summary tables, one meaning
and one layout (backlog TJ-5336): `tjs sum`'s FOR THE RETURN (cli/
estimate `_sum_return_ca`), `tjs gains-cra`'s FOR THE RETURN and ALL
SALES (lib/gains_cra `render`) and `tjs gains-base`'s TOTALS (lib/
gains_base `render`). In each, in this order (KEYS, HEADS):

  PROCEEDS       Schedule 3's proceeds
  COST(ACB)      the ACB as filed: a denied loss already taken out
                 (lib/tax_forms `_foot_cells`), so it is reduced by it
  OUTLAYS        the outlays and expenses (the sale's commissions)
  TOTAL COST     COST(ACB) + OUTLAYS, added from the cells as shown
  GAIN           PROCEEDS − TOTAL COST: the gain filed, the allowed one
  DENIED (INFO)  every loss denied, added (`denied_all`, as `tjs
                 gains-cra --csv`'s Denied column adds it): the
                 superficial losses, deferred and for good (`denied`),
                 and an in-kind contribution's loss (CA-INKIND-03,
                 `denied_contribution`; TJ-5341): information only,
                 COST(ACB) is already reduced by them, nothing to add
                 or subtract; shown because a deferred one is added to
                 the replacement's ACB (one denied for good is not).

`table` lays one out at a width: its left block (SCHEDULE 3 LINE, or
the table's own columns: ACCOUNT TYPE SALES, TABLE ROWS) as wide as the
widest Schedule 3 line label `tjs sum` shows at that width (`label`, of
the `lines` given), the table's own block when wider, or `left_min`; its
first column padded; each money column right-aligned to the widest of
its heading, the table's cells, `tjs sum`'s (the `lines` and `total`
given) and the `refs` given, two spaces before it. DENIED (INFO)'s
heading is on two lines (DENIED, then (INFO)). So a money column ends at
the same offset in each report as far as the cells each one is given
reach: `tjs sum` knows only its own cells, so it lines up with the
others when no cell of theirs is wider than its (gains-base's account
rows, gains-cra's currency tables); within gains-cra, ALL SALES and FOR
THE RETURN are given each other's cells (`refs`, `left_min`) and always
line up. Too wide, as `tjs sum` has done since TJ-5333: the labels
shorten (and the left block takes its shortest form), then DENIED
(INFO) drops (never with `keep_denied`), then one record per row; no
other money column ever drops.

gains-cra's per-currency tables name their columns so too (PROCEEDS,
COST(ACB), OUTLAYS, GAIN, DENIED (INFO): no TOTAL COST), one name for
one figure in every table (TJ-5341's P3-10).

The legends' words are here too, each in forms by width (`fit_legend`):
`IDENTITY`, `FOOTING`, `sum_legend`, `denied_forms`.
"""
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

from taxjson.lib import out as _out
from taxjson.lib.report_fmt import fmt_money

HEADS = ("PROCEEDS", "COST(ACB)", "OUTLAYS", "TOTAL COST", "GAIN", "DENIED (INFO)")
# DENIED (INFO)'s key, `denied_all`: every kind of denied loss added (each
# record the tables lay out carries it; `denied` keeps the superficial
# losses alone, as --json always had it).
KEYS = ("proceeds", "acb", "outlays", "total_cost", "gain", "denied_all")
# Each heading as the tables stack it (top-aligned): DENIED (INFO) on two
# lines, so the column is no wider than its figures (TJ-5336 review).
_HEAD_LINES = tuple(("DENIED", "(INFO)") if h == "DENIED (INFO)" else (h,) for h in HEADS)
LINE_HEAD = "SCHEDULE 3 LINE"
RETURN_ROW = "RETURN"
GAP = "  "

# How every row and total foots, in these names (gains-cra's per-sale
# tables name their columns so too: TJ-5341's P3-10).
IDENTITY = "GAIN = PROCEEDS − COST(ACB) − OUTLAYS = PROCEEDS − TOTAL COST"
FOOTING = IDENTITY + ", the allowed gain."

# A legend line in forms by width (`fit_legend`): each form one or more
# lines, the longest first.
Forms = Sequence[Sequence[str]]


def denied_forms(star: str = "") -> Forms:
    """What DENIED (INFO) is, in one sense in every report: information
    only, COST(ACB) is already reduced by it, and why it is shown (a
    deferred one is added to the replacement's ACB; one denied for good
    is not: a superficial loss so denied, marked `star` where a report
    marks it, or an in-kind contribution's, whose own mark a report
    gives). One line, else two."""
    mark_long = f", {star}," if star else ""
    mark_short = f" ({star})" if star else ""
    return ((f"DENIED (INFO): information only — COST(ACB) is already reduced by it; a deferred "
             f"one is added to the replacement's ACB (one denied for good{mark_long} is not).",),
            ("DENIED (INFO): information only — COST(ACB) is already reduced by it.",
             f"A deferred one is added to the replacement's ACB; one denied for good{mark_short} "
             f"is not."))


# `tjs sum`'s legend over FOR THE RETURN (`sum_legend`): one line at the
# concise width (its budget, docs/output-style.md, has room for one), two
# where a wider terminal has the room.
_SUM_ONE = FOOTING[:-1] + "; " + denied_forms()[0][0]
_SUM_SHORT = "GAIN = PROCEEDS − TOTAL COST; " + denied_forms()[1][0]
_SUM_CONCISE = 100


def sum_legend(width: int, denied: bool = True) -> List[str]:
    """`tjs sum`'s legend lines over FOR THE RETURN: unwrapped (width 0)
    or where it fits, the footing and DENIED (INFO)'s sense on one line;
    wider than the concise width, its short form and why DENIED (INFO)
    is shown on a second line; else the short form alone (wrapped by the
    caller where even that is too wide). Without DENIED (INFO) (`denied`
    False: dropped for the width) the footing alone."""
    if not denied:
        return [FOOTING]
    if width <= 0 or len(_SUM_ONE) <= width:
        return [_SUM_ONE]
    if width > _SUM_CONCISE:
        return [_SUM_SHORT, denied_forms()[1][1]]
    return [_SUM_SHORT]


def fit_legend(forms: Forms, width: int) -> Sequence[str]:
    """The first of `forms` whose every line is within `width` (0: the
    first), else the last: the caller wraps a line still too wide."""
    for form in forms:
        if width <= 0 or all(len(t) <= width for t in form):
            return form
    return forms[-1]


def legend(d: "_out.Doc", forms: Forms, width: int) -> None:
    """`fit_legend`'s lines into `d`: each a legend line (verbatim), or a
    wrapped paragraph where even the shortest form is too wide."""
    for text in fit_legend(forms, width):
        if width <= 0 or len(text) <= width:
            d.legend(text)
        else:
            d.para(text)


_SHORT = {"shares & fund units": "shares & units",
          "options & other properties": "options & other",
          "options, crypto & other properties": "options, crypto & other",
          "crypto-assets": "crypto"}


def label(r: Mapping[str, Any], limit: Optional[int] = None) -> str:
    """A Schedule 3 line's label, its one name in every report: its line
    number, words, period mark and line codes ("shares & fund units
    (P2) (13199/13200)"; `r` a lib/tax_forms schedule3_line spec or a
    line of build_schedule3). With `limit`, the longest form within it:
    the words, the short words (`_SHORT`), the short words cut after a
    whole word with an ellipsis ("options, crypto…"), then the period
    mark and the codes alone ("(P2) 13199/13200", `limit` 0), returned
    even over the limit: the codes are what a filer types, always
    whole."""
    short = str(r["short"])
    words, pmark = short, ""
    if short.endswith(")") and " (P" in short:
        words, pmark = short.rsplit(" (P", 1)[0], " (P" + short.rsplit(" (P", 1)[1]
    pre = f"Line {r['line']} " if r["line"] else ""
    codes = f"{r['proceeds_code']}/{r['gain_code']}"
    tail = f"{pmark} ({codes})"
    sw = _SHORT.get(words, words).split(" ")
    forms = [words, " ".join(sw)] + [" ".join(sw[:k]).rstrip(" ,&") + "…"
                                     for k in range(len(sw) - 1, 0, -1)]
    for form in forms:
        if limit is None or len(pre) + len(form) + len(tail) <= limit:
            return f"{pre}{form}{tail}"
    return f"{pmark.strip()} {codes}".strip()


def figures(rec: Mapping[str, Any]) -> List[str]:
    """A record's money cells (KEYS), as every report shows money."""
    return [fmt_money(rec[k]) for k in KEYS]


def block_width(heads: Sequence[str], cells: Sequence[Sequence[str]]) -> int:
    """A left block's width: each column as wide as its heading or its
    widest cell, GAP between them (what `table` lays out)."""
    return (sum(max([len(h)] + [len(r[j]) for r in cells]) for j, h in enumerate(heads))
            + len(GAP) * (len(heads) - 1))


# The left block's forms for the labels shown, longest first: each the
# body's left cells and the foot's.
Left = Callable[[List[str]], Sequence[Tuple[Sequence[Sequence[str]], Sequence[Sequence[str]]]]]


def table(left_heads: Sequence[str], left: Left, body: Sequence[Mapping[str, Any]],
          foot: Sequence[Mapping[str, Any]], *, width: int,
          lines: Sequence[Mapping[str, Any]] = (), total: Optional[Mapping[str, Any]] = None,
          refs: Sequence[Mapping[str, Any]] = (), left_min: int = 0,
          keep_denied: bool = False, aligns: Optional[Sequence[str]] = None
          ) -> Tuple[List[str], str]:
    """A summary table at `width` (0: unwrapped) and its layout: "table"
    every column; "no-denied" DENIED (INFO) left out (the caller says so
    in a `! ` line); "records" one record per row, every column. `body`
    and `foot` are the rows' figures (KEYS), `left(labels)` their left
    cells (`left_heads`, `aligns`) for the Schedule 3 labels shown;
    `lines` and `total` are `tjs sum`'s figures (its lines and RETURN
    row), the reference every table's layout is made from; `refs` more
    figures that size the money columns and are not shown, `left_min`
    the narrowest left block (another table's, to line up with it)."""
    w = width
    every = list(range(len(HEADS)))
    bm, fm = [figures(r) for r in body], [figures(r) for r in foot]
    ref = [figures(r) for r in [*lines, *([total] if total is not None else []), *refs]]
    mw = [max([len(t) for t in _HEAD_LINES[i]] + [len(c[i]) for c in bm + fm + ref])
          for i in every]
    aligns = list(aligns or ["<"] * len(left_heads))
    cell = _out.printable if w > 0 else str     # shown to a person: escaped

    def money_w(cols: List[int]) -> int:
        return sum(len(GAP) + mw[i] for i in cols)

    def block(labels: List[str], room: Optional[int]) -> Tuple[Any, ...]:
        # The first form within sum's label column (no lines: within the
        # room the width leaves, else the first), padded to it.
        ref_w = max([len(LINE_HEAD)] + [len(x) for x in labels]) if lines else 0
        target = ref_w if lines else room
        for bl, fl in left(labels):
            bl = [[cell(c) for c in r] for r in bl]
            fl = [[cell(c) for c in r] for r in fl]
            lw = [max([len(h)] + [len(r[j]) for r in bl + fl]) for j, h in enumerate(left_heads)]
            own = sum(lw) + len(GAP) * (len(lw) - 1)
            if target is None or own <= target:
                break
        return bl, fl, lw, max(own, ref_w, left_min)

    def render(cols: List[int], bl: Any, fl: Any, lw: List[int], wide: int) -> List[str]:
        lw = [lw[0] + wide - (sum(lw) + len(GAP) * (len(lw) - 1))] + lw[1:]

        def row(cells: Sequence[str], money: Sequence[str]) -> str:
            text = GAP.join(f"{c:{a}{n}}" for c, a, n in zip(cells, aligns, lw))
            return (text + "".join(f"{GAP}{money[i]:>{mw[i]}}" for i in cols)).rstrip()
        depth = max(len(_HEAD_LINES[i]) for i in cols)
        heads = [row(list(left_heads) if k == 0 else [""] * len(left_heads),
                     [(_HEAD_LINES[i] + ("",) * depth)[k] for i in every])
                 for k in range(depth)]
        rule = "-" * len(heads[0])
        out = heads + [rule] + [row(c, m) for c, m in zip(bl, bm)]
        return out + ([rule] + [row(c, m) for c, m in zip(fl, fm)] if fm else [])

    full = [label(ln) for ln in lines]
    codes = [label(ln, 0) for ln in lines]
    tries = [(every, full)]
    if w > 0:
        # The labels shorten, all to the codes alone once one is; DENIED
        # (INFO) goes only after that, the labels kept at the codes.
        short = [label(ln, w - money_w(every)) for ln in lines]
        tries.append((every, codes if set(short) & set(codes) else short))
        if not keep_denied:
            tries.append((every[:-1], codes))
    for cols, labels in tries:
        bl, fl, lw, wide = block(labels, None if w <= 0 else w - money_w(cols))
        if w <= 0 or wide + money_w(cols) <= w:
            return render(cols, bl, fl, lw, wide), ("table" if cols == every else "no-denied")
    bl, fl = left([label(ln, w) for ln in lines])[0]
    rows = ([[cell(x) for x in c] + m for c, m in zip(bl, bm)]
            + [[cell(x) for x in c] + m for c, m in zip(fl, fm)])
    return _out.records(list(left_heads) + list(HEADS), rows, w), "records"


def return_table(lines: Sequence[Mapping[str, Any]], total: Mapping[str, Any], *, width: int,
                 keep_denied: bool = False, total_label: str = RETURN_ROW,
                 refs: Sequence[Mapping[str, Any]] = (), left_min: int = 0
                 ) -> Tuple[List[str], str]:
    """`table` of Schedule 3's lines and their total (`tjs sum`'s RETURN
    row, named as sum names it), each line labelled as sum labels it;
    `refs` and `left_min` as `table`'s (gains-cra: ALL SALES')."""
    return table([LINE_HEAD], lambda labels: [([[x] for x in labels], [[total_label]])],
                 lines, [total], width=width, lines=lines, total=total, refs=refs,
                 left_min=left_min, keep_denied=keep_denied)
