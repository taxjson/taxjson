"""`taxjson plot-tax`: the tax the year's investment income generated
month by month, as an ASCII chart, against the year's estimate
(`taxjson estimate`). Planning numbers only, never a filing figure
(tax-logic CA-PLAN-08 / US-PLAN-09).

The command runs the estimator the way `taxjson estimate` does, once
per month on the taxable accounts' gains and income dated up to that
month's end; a month's tax is that run less the month before's (so a
month's figure depends on where the year's income stands: a loss
month is negative, a month that crosses a bracket costs more). The
running total through December is the year's estimate on the same
inputs; `taxjson estimate`'s own figure is the reference line (the two
differ only by what is dated to the year rather than to a month: a T5
box 18 reclassification, a US §1256 fold).

This module is pure: the command (taxjson_run.cmd_plot_tax) runs the
estimator and hands the monthly points in.
"""
from __future__ import annotations

import calendar
import math
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
               "Sep", "Oct", "Nov", "Dec")
# The chart: at most this many rows above the axis, cells this wide.
HEIGHT = 8
CELL_MAX, CELL_MIN = 6, 2
BAR, LEVEL = "#", "-"


def months_of_year(year: int) -> List[str]:
    return [f"{int(year)}-{m:02d}" for m in range(1, 13)]


def month_end(month: str) -> str:
    """The last day of `YYYY-MM`."""
    y, m = int(month[:4]), int(month[5:7])
    return f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def slice_doc(doc: Dict[str, Any], date_of, through: str) -> Dict[str, Any]:
    """A gains document's rows dated up to `through` (inclusive), the
    rest of the document as it is."""
    out = dict(doc)
    out["transactions"] = [t for t in (doc.get("transactions") or [])
                           if isinstance(t, dict)
                           and str(date_of(t) or "")[:10] <= through]
    return out


def build(points: Iterable[Dict[str, Any]], *, year: int, base: str,
          year_estimate: float, year_income: float,
          label: str, accounts: Iterable[str],
          with_amt: Optional[float] = None,
          other_income: float = 0.0) -> Dict[str, Any]:
    """The document. `points`: one per month in order, {month,
    cum_tax, cum_income} — the estimator run on the rows dated up to
    the month's end. `year_estimate` / `year_income`: `taxjson
    estimate`'s own figures for the year."""
    rows: List[Dict[str, Any]] = []
    prev_t = prev_i = 0.0
    for p in points:
        ct, ci = float(p["cum_tax"]), float(p["cum_income"])
        rows.append({"month": p["month"], "tax": round(ct - prev_t, 2),
                     "income": round(ci - prev_i, 2),
                     "cum_tax": round(ct, 2), "cum_income": round(ci, 2)})
        prev_t, prev_i = ct, ci
    through = round(prev_t, 2)
    gap = round(float(year_estimate) - through, 2)
    return {
        "schema_version": SCHEMA_VERSION, "planning_only": True,
        "tax_year": int(year), "currency": base.upper(), "label": label,
        "accounts": sorted(accounts), "other_income": round(other_income, 2),
        "rows": rows,
        "months_total": through,
        "year_estimate": round(float(year_estimate), 2),
        "year_income": round(float(year_income), 2),
        "year_estimate_with_amt": (None if with_amt is None
                                   else round(float(with_amt), 2)),
        # What is dated to the year rather than to a month.
        "year_adjustment": gap if abs(gap) > 0.005 else 0.0,
    }


# -------------------------------------------------------------- chart
def _nice_step(span: float, rows: int) -> float:
    """A 1 / 2 / 5 x 10^k step so that `span` fits in `rows` rows."""
    if span <= 0:
        return 1.0
    raw = span / rows
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw - 1e-9:
            return m * mag
    return 10 * mag


def _level(v: float, step: float) -> int:
    """The row a value lands on (half a step rounds away from zero)."""
    q = v / step
    return int(q + (0.5 if q >= 0 else -0.5))


def _fmt_level(v: float, step: float = 1.0) -> str:
    """An axis label at the chart step's precision (cents on a
    cent-sized step: twelve months of 0.04 are not twelve rows of 0)."""
    if step >= 1:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def chart(rows: List[Dict[str, Any]], estimate: float,
          width: int = 120, base: str = "") -> List[str]:
    """The chart lines: its key, then a bar (`#`) per month for the tax
    its income generated, the year's estimate spread evenly (its
    twelfth) as a dashed level; a negative month hangs below the
    axis."""
    n = len(rows) or 1
    lw = 11                                     # "  1,200,000 |"
    width = width or 120
    tag = f" estimate / {n}"
    cell = max(CELL_MIN, min(CELL_MAX, (width - lw - 1 - len(tag)) // n))
    if lw + 1 + cell * n + len(tag) > width:
        # Too narrow for the tag beside the level: the key names it.
        cell = max(CELL_MIN, min(CELL_MAX, (width - lw - 1) // n))
        tag = ""
    bw = max(1, cell - 2)                       # bar width
    taxes = [float(r["tax"]) for r in rows]
    share = float(estimate) / n
    hi = max([0.0, share] + taxes)
    lo = min([0.0, share] + taxes)
    step = _nice_step(max(hi, -lo), HEIGHT)
    bars = [_level(v, step) for v in taxes]
    est_lv = _level(share, step)
    top = max(1, max(bars), est_lv)
    bottom = max(0, -min(bars), -est_lv)
    keys = (f"{base:>{lw - 2}} |  {BAR} = the month's tax   {LEVEL} {LEVEL} "
            f"{LEVEL} = the year's estimate / {n}",
            f"{base:>{lw - 2}} |  {BAR} month's tax   {LEVEL} {LEVEL} "
            f"{LEVEL} estimate / {n}",
            f"{base:>{lw - 2}} |  {BAR} tax  {LEVEL} {LEVEL} est/{n}")
    lines: List[str] = [next((k for k in keys if len(k) <= width),
                             keys[-1])]
    for lv in range(top, -bottom - 1, -1):
        if lv == 0:
            axis = f"{'0':>{lw - 2}} +" + LEVEL * (cell * n)
            if est_lv == 0:
                axis += tag
            lines.append(axis.rstrip())
            continue
        cells: List[List[str]] = []
        for i in range(n):
            c = [" "] * cell
            b = bars[i]
            if (lv > 0 and 0 < lv <= b) or (lv < 0 and b <= lv < 0):
                for k in range(bw):
                    c[1 + k] = BAR
            cells.append(c)
        row = "".join("".join(c) for c in cells)
        if lv == est_lv:
            row = "".join(LEVEL if (ch == " " and j % 2 == 0) else ch
                          for j, ch in enumerate(row))
            row += tag
        lines.append((f"{_fmt_level(lv * step, step):>{lw - 2}} |"
                      + row).rstrip())
    # Month names; their numbers, then initials, as the cells narrow.
    names = "".join((MONTH_NAMES[int(r['month'][5:7]) - 1] if cell >= 4
                     else r['month'][5:7] if cell >= 3
                     else MONTH_NAMES[int(r['month'][5:7]) - 1][:1]
                     ).center(cell) for r in rows)
    lines.append(" " * lw + names.rstrip())
    return lines


def render(doc: Dict[str, Any], width: int = 120, cmd: str = "tjs plot-tax"):
    """The text view (lib/out.Doc): the legend, the chart, the monthly
    table with the running total, the year's figures and what to
    check."""
    from taxjson.lib import out
    from taxjson.lib.report_model import fmt_money
    base = doc["currency"]
    yr = doc["tax_year"]
    d = out.Doc(f"TAX BY MONTH — planning numbers, tax year {yr} "
                f"({doc['label']}, {base})")
    rows = doc["rows"]
    flat = (all(abs(r["tax"]) < 0.005 for r in rows)
            and abs(doc["year_estimate"]) < 0.005)
    if flat:
        # Nothing to chart: the legend says so and the table follows.
        d.para(f"Planning numbers, not filing: no tax on the year's "
               f"investment income ({fmt_money(doc['year_income'])} "
               f"{base}), nothing to chart.")
    else:
        d.para(f"Planning numbers, not filing: the tax on each month's "
               f"investment income, as the year's estimate (tjs estimate) "
               f"stands through that month.")
    if not rows:
        d.para("No month to plot.")
        return d
    if not flat:
        for ln in chart(rows, doc["year_estimate"], width, base):
            d.line(ln)
    d.blank()
    body = [[r["month"], fmt_money(r["income"]), fmt_money(r["tax"]),
             fmt_money(r["cum_tax"])] for r in rows]
    d.table(["MONTH", "INCOME", "TAX", "RUNNING"], body,
            aligns=["<", ">", ">", ">"],
            foot=[["YEAR", fmt_money(doc["year_income"]),
                   fmt_money(doc["year_estimate"]),
                   fmt_money(doc["year_estimate"])]])
    d.blank()
    kv = [("Estimated tax for the year",
           f"{fmt_money(doc['year_estimate'])} {base} on "
           f"{fmt_money(doc['year_income'])} of investment income"
           + (f", on top of {fmt_money(doc['other_income'])} other income"
              if doc.get("other_income") else ""))]
    wa = doc.get("year_estimate_with_amt")
    if wa is not None and abs(wa - doc["year_estimate"]) > 0.005:
        kv.append(("With the minimum tax", f"{fmt_money(wa)} {base}"))
    d.kv(kv)
    # The project's country, from the label `build` gives the document
    # ("canada/ON", "usa"): the footer names only what that country's
    # law and commands have (backlog TJ-0139: a US project was sent to
    # `tjs instalments`, which refuses it, US-CTRY-02).
    from taxjson.lib.country import command_country_problem, is_usa
    country = str(doc.get("label") or "").split("/")[0]
    try:
        us = is_usa(country)
        instalments = command_country_problem("instalments",
                                              country) is None
    except ValueError:
        us, instalments = None, False
    acts = []
    if doc.get("year_adjustment"):
        what = {True: "a §1256 reclassification",
                False: "a T5 box 18 capital gains dividend"}.get(
                    us, "a T5 box 18 or §1256 reclassification")
        acts.append(out.act(
            f"{fmt_money(doc['year_adjustment'])} of the year's estimate "
            f"is dated to the year, not a month ({what}): the months add "
            f"up to {fmt_money(doc['months_total'])}",
            "tjs estimate --details"))
    if acts:
        d.blank()
        for a in acts:
            d.item(a[len(out.ACT):], bullet=out.ACT)
    d.para("The year's estimate line by line: tjs estimate"
           + ("; the instalments it calls for: tjs instalments."
              if instalments else "."))
    return d
