#!/usr/bin/env python3
"""
taxjson_form_export.py

Render computed gains into filing-shaped artifacts:

  --form 8949       IRS Form 8949 rows (Part I short-term / Part II
                    long-term) with wash-sale code W adjustments, plus
                    Schedule D part totals. Needs a country=usa gains file
                    (entries carry ST/LT terms). From tax year 2025 the
                    crypto accounts' rows (--crypto) are digital assets,
                    grouped on boxes G/H/I and J/K/L with their own
                    totals; the TXF leaves them out (no reference number).

  --form txf        TurboTax-importable TXF (V042) built from the 8949
                    rows — one TD record per disposition with the
                    code-W wash amount as the trailing dollar field.
                    --box picks the 8949 checkbox pairing (A/D default);
                    --out writes the .txf file.

  --form schedule3  CRA Schedule 3 per-property rows, routed by property
                    type to the Part 3 line they belong on (2025 form):
                    line 4 publicly traded shares / fund units
                    (13199/13200), line 6 options, futures and other
                    properties (15199/15300, per T4037), line 7
                    crypto-assets (15200/15301; before 2025 crypto went
                    on 15199/15300). The 2024 form splits each line by
                    period: Jan 1 - Jun 24 on 10689/10690 (shares) and
                    10693/10694 (other), the rest as above. Per row: units, acquisition year,
                    proceeds of disposition, ACB, outlays, gain(loss)
                    with superficial-loss notes; per-line totals.

Input is the pipeline's year-scoped `<account>_gains.json` (prefer the
wash-adjusted `<account>_gains_wash.json` — those are the allowed numbers a
return reports). Column conventions:

  8949:  (d) proceeds and (e) cost come straight from each disposition
         chunk; a wash sale gets code W in (f) and the disallowed amount as
         a positive adjustment in (g), so (h) = (d) - (e) + (g) equals the
         engine's allowed gain. Date acquired is derived as sale date minus
         days_held ("VARIOUS" never appears — chunks are per-lot already).
         Short sales report the cover date in both date columns, matching
         common 1099-B practice; one closed by delivering shares held (a
         move between your accounts) reports the date those were acquired.

  Schedule 3: broker proceeds in taxjson are net of sell-side
         commission/fee, so the report re-splits them: proceeds column =
         net + outlays, outlays column = commission + fee, leaving the gain
         identical. A short position's row shows what the short sale
         (or write) brought in as PROCEEDS and the cover as ACB, with a
         note (the engine's negated fields are un-negated AND swapped).
         A net rebate (negative commission/fee) stays netted in the
         proceeds: OUTLAYS is never negative.
         A written option's premium (grant timing) is shown GROSS, its
         write commission in outlays; a close-timing write's commission
         and a short sale's opening commission stay netted (the closing
         row does not carry them).
         Every row foots — proceeds − ACB − outlays = the allowed gain —
         so a denied superficial loss shows as an ACB REDUCED by the
         denial (the denied amount goes onto the replacement's ACB).
         Crypto is known by account: pass crypto books with --crypto.

Tainted dispositions (sales with no purchase in the files, cost unknown —
opened by a .tt OPENING cost=unknown line) have no computable gain: the
pipeline routes them to `manual_reporting_required`. They are left out of the
rows and totals above (which are the allowed, computed numbers), but NEVER
silently: every one is listed in a MANUAL REPORTING section of the report
(rows marked MANUAL in the CSV, `manual_reporting_required` in the JSON) and
named in a stderr warning with its proceeds — they must be reported by hand
once their cost is known.

  8949 / TXF: §1256 contracts (futures, options on futures, broad-
         based index options) are kept off Form 8949 and listed for
         Form 6781 by hand (tax-logic US-FUT-02 / US-OPT-04).

Usage:
    taxjson-form-export --form 8949 --country usa --year 2025 \
        margin_gains_wash.json [...]
    taxjson-form-export --form schedule3 --country canada --year 2025 \
        --csv out.csv margin_gains_wash.json [...]

Or through the project wrapper: `tjs form-export` (form defaults from
the project's country).
"""
from taxjson.lib.out import exit_text
import argparse
import csv
import re
import sys
from pathlib import Path
from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.exchange_rates import norm_currency
from taxjson.lib.json_input import gains_row_kind
from taxjson.model.result import GainRow
from taxjson.model import money as M
from typing import Any, Dict, List, Optional, Tuple
from taxjson.lib.tax_forms import (
    DIGITAL_ASSET_BOXES_FROM,
    PERIOD_YEAR,
    _infer_year,
    build_8949,
    build_schedule3,
    line_title,
    load_json,
    mark_crypto,
    schedule3_line,
)
from taxjson.lib import out as _out


_INCOME_ACTIONS = ("DIVIDEND", "DIVIDEND_IN_LIEU")


def load_manual_rows(paths: List[Path], year: Optional[int],
                     date_key: str) -> List[Dict[str, Any]]:
    """In-year unknown-cost dispositions: the pipeline's
    manual_reporting_required rows (its 'tainted' key is popped there,
    and gain/cost stripped) plus any hand-run file's rows still flagged
    'tainted' in transactions. Audit R1-199: keyed only on 'tainted',
    form-export never saw the pipeline's rows and left them out of the
    export without a word."""
    out: List[Dict[str, Any]] = []
    ystr = str(year) if year else None
    for p in paths:
        data = load_json(p)
        rows = list(data.get("manual_reporting_required") or [])
        rows += [e for e in data.get("transactions", [])
                 if e.get("tainted") and "qty" in e
                 and e.get("action") not in _INCOME_ACTIONS]
        for e in rows:
            date = e.get(date_key) or e.get("date") or ""
            if ystr and not str(date).startswith(ystr):
                continue
            out.append(e)
    return out


def load_dispositions(paths: List[Path], year: Optional[int],
                      date_key: str) -> Tuple[List[Dict[str, Any]], int]:
    """Disposition entries (sells) from gains files; income rows and
    tainted rows are excluded. Returns (entries, tainted_skipped) —
    tainted_skipped counts the in-year manual_reporting_required rows
    too (see load_manual_rows for the rows themselves)."""
    entries: List[Dict[str, Any]] = []
    tainted_skipped = 0
    ystr = str(year) if year else None
    for p in paths:
        # load_json holds every row to the gains-row contract (lib/
        # json_input.check_gains_rows): a disposition missing its gain
        # or units is refused there, never skipped here (issue #17).
        data = load_json(p)
        for e in data.get("transactions", []):
            kind = gains_row_kind(e)
            if kind == "income":
                continue
            date = e.get(date_key) or e.get("date") or ""
            if ystr and not date.startswith(ystr):
                continue
            if kind == "manual":
                tainted_skipped += 1
                continue
            entries.append(e)
        for e in data.get("manual_reporting_required") or []:
            date = e.get(date_key) or e.get("date") or ""
            if ystr and not str(date).startswith(ystr):
                continue
            tainted_skipped += 1
    return entries, tainted_skipped


def is_short_cover(r: Dict[str, Any]) -> bool:
    """An unknown-cost row that covers a short sale made before the
    data (the engine's direction SHORT): what covering cost is known,
    what the short sale brought in is not."""
    return (str(r.get("direction") or "").upper() == "SHORT"
            and not r.get("closed_by_delivery"))


def manual_figures(r: Dict[str, Any]) -> Tuple[Optional[float],
                                                Optional[float]]:
    """(proceeds, cover_cost) of an unknown-cost row as the filer
    reports it: a sale's proceeds (cover_cost None); for a short cover
    (is_short_cover), the cost to cover and proceeds None — the short
    sale's proceeds are the unknown figure. The engine's signed short
    convention keeps the cover cost, negated, in `proceeds` (build_8949
    swaps the two for a computed short); read as proceeds it put the
    cover cost in the field the filer is told to report (backlog
    TJ-0497). A sale keeps its sign: a disposal for nothing whose
    commission is the only cash carries net proceeds below zero, and
    abs() turned that debit into proceeds received (backlog TJ-0895)."""
    if r.get("closed_by_delivery"):
        # A short closed by shares moved in whose cost the books do not
        # have (the engine's `closed_by_delivery`): what the short sale
        # brought in is known (the engine's negated `cost`; the run's
        # `short_sale_proceeds` on a manual row), the cost of the
        # shares delivered is not (backlog TJ-3465).
        return abs(r.get("short_sale_proceeds")
                   or GainRow(r).cost or 0.0), None
    amt = float(GainRow(r).proceeds or 0.0)
    return (None, abs(amt)) if is_short_cover(r) else (amt, None)


def manual_totals(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    """(proceeds, cover cost) over unknown-cost rows (manual_figures)."""
    proceeds = cover = 0.0
    for r in rows:
        p, c = manual_figures(r)
        proceeds += p or 0.0
        cover += c or 0.0
    return proceeds, cover


def _manual_counts(rows: List[Dict[str, Any]], cur: str) -> str:
    """What the unknown-cost rows are, with their known totals: `N
    sale(s) with no purchase in your files ..., proceeds X CUR` and,
    for short covers, `M short sale(s) covered with no short sale in
    your files, cover cost Y CUR, their proceeds unknown`."""
    longs = [r for r in rows if not is_short_cover(r)]
    shorts = [r for r in rows if is_short_cover(r)]
    proceeds, cover = manual_totals(rows)
    parts = []
    if longs:
        parts.append(f"{len(longs)} sale(s) with no purchase in your "
                     f"files (unknown cost, .tt OPENING cost=unknown), "
                     f"proceeds {M.shown(proceeds, 2):,.2f} {cur}")
    if shorts:
        parts.append(f"{len(shorts)} short sale(s) covered with no short "
                     f"sale in your files, cover cost {M.shown(cover, 2):,.2f} {cur}, "
                     f"their proceeds unknown")
    return "; ".join(parts)


def _manual_table(rows: List[Dict[str, Any]]) -> List[str]:
    """The MANUAL REPORTING table: a COVER COST column, and `unknown`
    proceeds, once a short cover is among the rows."""
    shorts = any(is_short_cover(r) for r in rows)
    table = []
    for r in rows:
        p, c = manual_figures(r)
        cells = [str(r.get("symbol") or ""), str(r.get("date") or ""),
                 _qty_str(abs(float(GainRow(r).quantity or 0.0))),
                 "unknown" if p is None else f"{M.shown(p, 2):,.2f}"]
        if shorts:
            cells.append("unknown" if r.get("closed_by_delivery") else
                         "" if c is None else f"{M.shown(c, 2):,.2f}")
        cells.append(str(r.get("account") or ""))
        table.append(tuple(cells))
    if shorts:
        return _table(("SYMBOL", "DATE", "UNITS", "PROCEEDS", "COVER COST",
                       "ACCOUNT"), table, right={2, 3, 4})
    return _table(("SYMBOL", "DATE", "UNITS", "PROCEEDS", "ACCOUNT"),
                  table, right={2, 3})


def manual_section(rows: List[Dict[str, Any]], cur: str,
                   country: Optional[str] = None) -> List[str]:
    """The MANUAL REPORTING block every text report ends with when
    unknown-cost dispositions (no purchase in the files) exist in the
    year. A sale of units moved in from another of your accounts
    (`own_move_from`: one account's own books, its `<account>.sum`) has
    no purchase missing: its cost is in the blended figures you file,
    said in a block of its own (own_move_section, backlog TJ-0279)."""
    # A report of no other row in the year: its rows' own currency.
    cur = cur or ", ".join(sorted({str(r.get("currency") or "")
                                   for r in rows} - {""}))
    moved = [r for r in rows if r.get("own_move_from")]
    rows = [r for r in rows if not r.get("own_move_from")]
    lines: List[str] = []
    if rows:
        lines = [f"MANUAL REPORTING REQUIRED — "
                 f"{_manual_counts(rows, cur)}: "
                 f"NOT in the rows or totals above. "
                 f"Report each by hand once its cost is known "
                 f"(`tjs find-missing-history`)."]
        lines += _manual_table(rows)
        lines.append("")
    return lines + own_move_section(moved, cur, country)


def own_move_section(rows: List[Dict[str, Any]], cur: str,
                     country: Optional[str] = None) -> List[str]:
    """The sales of units moved in from another of your taxable
    accounts that one account's own books hold at no cost (backlog
    TJ-0279): not in its rows or totals, and nothing to report by hand
    from it: the blended pass, the figures you file
    (`<account>_wash.sum`, `tjs sum`), has their cost."""
    if not rows:
        return []
    proceeds, _cover = manual_totals(rows)
    froms = sorted({a or "another of your accounts"
                    for r in rows for a in r.get("own_move_from") or []})
    accts = sorted({str(r.get("account") or "") for r in rows} - {""})
    wash = (", ".join(f"{a}_wash.sum" for a in accts)
            or "<account>_wash.sum")
    from taxjson.lib.country import basis_pooled_across_accounts
    why = ("" if not country else
           " (one ACB across your taxable accounts, CA-ACB-OWN-MOVE)"
           if basis_pooled_across_accounts(country) else
           " (the lots' basis carried by the move, US-BASIS-05)")
    lines = [f"MOVED IN FROM YOUR OWN ACCOUNT — {len(rows)} sale(s) of "
             f"units moved in from {', '.join(froms)}, proceeds "
             f"{M.shown(proceeds, 2):,.2f} {cur}: NOT in the rows or totals above, "
             f"and nothing to report by hand from here. This account's "
             f"own books have no cost for them; the figures you file "
             f"have it: {wash} and `tjs sum`{why}."]
    lines += _manual_table(rows)
    lines.append("")
    return lines


from taxjson.lib.report_fmt import fmt_qty as _qty_str  # noqa: E402


FILING_POSITION_NOTE_8949 = (
    "A row with a filing position (your .tt ALLOWLOSS line, US-WASH-25) "
    "is filed as your position: no code W in (f), nothing in (g), so (h) "
    "claims the loss. When the broker's 1099-B reports a wash-sale loss "
    "disallowed (box 1g) for that sale, the row differs from it: enter "
    "the proceeds and basis as the 1099-B reports them and follow the "
    "Form 8949 instructions for a 1099-B amount you believe is incorrect "
    "(the code and the (g) adjustment that bring the row to your "
    "figure). taxjson does not pick that code: check the instructions "
    "or a tax professional, and keep the reason with your records.")
FILING_POSITION_NOTE_S3 = (
    "A row with a filing position (your .tt ALLOWLOSS line, CA-SL-18) "
    "claims a loss the superficial-loss rule would deny: GAIN(LOSS) "
    "includes it, the ACB is not reduced by it and no replacement's ACB "
    "is raised for it. It is your position, not the rule's test: be "
    "ready to support it (CRA guidance, a tax professional); delete the "
    "line to apply the rule.")


# ---------------------------------------------------------------- txf

# TXF (Tax Exchange Format) V042 reference numbers for Form 8949
# security sales, by part and checkbox: Part I (short-term) boxes
# A/B/C -> 711/712/713, Part II (long-term) boxes D/E/F -> 714/715/716.
# Box A/D = basis reported on the 1099-B (covered securities, the
# common case and the default); C/F = not on a 1099-B at all.
_TXF_REFNUM = {
    ("I", "A"): 711, ("I", "B"): 712, ("I", "C"): 713,
    ("II", "A"): 714, ("II", "B"): 715, ("II", "C"): 716,
}


def _txf_date(iso: str) -> str:
    """MM/DD/YYYY (TXF's date shape) from ISO; blank stays blank."""
    if not iso or len(iso) != 10:
        return iso or ""
    return f"{iso[5:7]}/{iso[8:10]}/{iso[0:4]}"


def build_txf(rep_8949: Dict[str, Any], box: str) -> str:
    """A TurboTax-importable TXF V042 document from the 8949 report
    model (which already carries real-world short-sale columns and
    code-W adjustments). One TD record per row:

        TD / N<refnum> / C1 / L1 / P<desc> / D<acquired> / D<sold> /
        $<cost> / $<proceeds> [/ $<wash-sale disallowed>] / ^

    The wash-sale amount rides as the optional trailing dollar field —
    TurboTax applies it as the code-W adjustment, reproducing the 8949
    row exactly.
    """
    from datetime import date as _date
    lines = ["V042", "Ataxjson", f"D{_txf_date(_date.today().isoformat())}",
             "^"]
    for part in ("I", "II"):
        refnum = _TXF_REFNUM[(part, box)]
        for row in rep_8949[f"part_{part}"]:
            if row.get("digital_asset"):
                # Boxes G-L have no TXF reference number taxjson knows:
                # left out, and _main says so (A2-0482).
                continue
            _desc = " ".join(str(row['description']).split())
            _desc = _desc.replace("^", " ").encode(
                "ascii", "replace").decode("ascii")
            lines += ["TD", f"N{refnum}", "C1", "L1",
                      f"P{_desc}",
                      f"D{_txf_date(row['date_acquired'])}",
                      f"D{_txf_date(row['date_sold'])}",
                      f"${M.shown(row['cost'], 2):.2f}",
                      f"${M.shown(row['proceeds'], 2):.2f}"]
            if row.get("code") == "W" and row.get("adjustment"):
                lines.append(f"${M.shown(row['adjustment'], 2):.2f}")
            lines.append("^")
    return "\n".join(lines) + "\n"


def filing_lines(entries: List[Dict[str, Any]],
                 year: Optional[int] = None,
                 date_key: str = "date_settle") -> List[Dict[str, Any]]:
    """One dict per Schedule 3 line that has dispositions: the line
    number / codes / label plus PROCEEDS, ACB, OUTLAYS, GAIN, DENIED and
    the disposition count — the rows of `tjs sum`'s FOR THE RETURN
    block, identical to form-export's line totals by construction."""
    return build_schedule3(entries, year, date_key)["lines"]


def filing_totals(entries: List[Dict[str, Any]],
                  year: Optional[int] = None,
                  date_key: str = "date_settle") -> Dict[str, float]:
    """The amounts a return's capital-gains entry asks for, summed over
    `entries` (all Schedule 3 lines) on the Schedule 3 convention (a
    short sale's proceeds as PROCEEDS and its cover as ACB, sell-side
    commissions split out as outlays, a rebate kept netted), with
    the ACB chosen so that PROCEEDS − ACB − OUTLAYS equals the ALLOWED
    gain: a denied superficial loss REDUCES the ACB shown here (the
    denied amount is added to the replacement property's ACB instead).
    `denied` reports how much that is."""
    rep = build_schedule3(entries, year, date_key)
    # Each column is the sum of its row cells, as the line totals are:
    # re-deriving ACB as a residual of the sums was a second residual
    # site that showed ACB -0.01 for a zero-ACB sale (A2-1107).
    agg = {k: M.shown(sum(r[k] for r in rep["rows"]), 2) + 0.0
           for k in ("proceeds", "acb", "outlays", "gain", "denied",
                     "denied_contribution")}
    return {"proceeds": agg["proceeds"],
            "acb": agg["acb"],
            "outlays": agg["outlays"], "gain": agg["gain"],
            "denied": agg["denied"],
            # An in-kind contribution's loss, nil by s.40(2)(g)(iv) — not
            # in `denied` (CA-INKIND-03).
            "denied_contribution": agg["denied_contribution"],
            "dispositions": len(entries)}


def filing_parts_8949(entries: List[Dict[str, Any]],
                      year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Form 8949's own totals per part and checkbox group — (d)
    proceeds, (e) cost, (g) adjustment, (h) gain — for `tjs sum`'s
    US FOR THE RETURN block, so it shows exactly what the 8949 export
    carries to Schedule D. From 2025 the digital-asset boxes (G/H/I,
    J/K/L) are rows of their own (A2-0482)."""
    rep = build_8949(entries, year)
    has_da = any(g["digital_asset"] for g in rep["groups"])
    names = {"I": ("Part I — short-term", "Schedule D Part I"),
             "II": ("Part II — long-term", "Schedule D Part II")}
    out = []
    for g in rep["groups"]:
        label, sched_d = names[g["part"]]
        if has_da:
            label += (" digital assets" if g["digital_asset"] else "") \
                + f" (box {g['boxes']})"
        t = g["totals"]
        out.append({"part": g["part"], "boxes": g["boxes"],
                    "digital_asset": g["digital_asset"],
                    "label": label, "schedule_d": sched_d,
                    "proceeds": t["proceeds"], "cost": t["cost"],
                    "adjustment": t["adjustment"], "gain": t["gain"],
                    "dispositions": g["rows"]})
    return out


def filing_6781(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The §1256 contracts `build_8949` keeps off Form 8949 (tax-logic
    US-FUT-02 / US-OPT-04): {"dispositions": n, "gain": net P/L} for
    `tjs sum`'s US FOR THE RETURN note (A2-0324)."""
    return dict(build_8949(entries)["section_1256_totals"])


SEC1256_NOTE = ("§1256 contracts (futures, options on futures, broad-"
                "based index options such as SPX) are NOT on Form 8949: "
                "report them on Form 6781 by hand — the net is split 60% "
                "long-term / 40% short-term (as taxjson's estimate and "
                "carryover split it), and contracts still open on Dec 31 "
                "are marked to market (not modelled; tax-logic US-FUT-02, "
                "US-FUT-03, US-OPT-04).")


def section_1256_lines(rep: Dict[str, Any], cur: str) -> List[str]:
    """The FORM 6781 block of the 8949 text report (empty when the
    year has no §1256 contract)."""
    rows = rep.get("section_1256") or []
    if not rows:
        return []
    t = rep.get("section_1256_totals") or {}
    lines = [f"FORM 6781 BY HAND — {len(rows)} §1256 contract "
             f"disposition(s)"]
    lines += _para(f"Net {M.shown(t.get('gain', 0.0), 2):,.2f} {cur}: NOT in the Form "
                   f"8949 rows or totals above.")
    table = [(r["description"], r["kind"], r["date_acquired"],
              r["date_sold"], f"{M.shown(r['gain'], 2):,.2f}") for r in rows]
    lines += _table(("CONTRACT", "KIND", "ACQUIRED", "CLOSED",
                     "GAIN(LOSS)"), table, right={4})
    lines += _item(SEC1256_NOTE)
    lines.append("")
    return lines

def year_not_ended_text(year: Any, today: Optional[Any] = None) -> str:
    """'' once `year` has ended; else a sentence saying the filing
    figures are year-to-date (A2-1103: Schedule 3, Form 8949 and the
    sum FOR THE RETURN block presented an unfinished year as a complete
    return, while t1135 and close-year say so) — the console wording."""
    from datetime import date as _date
    try:
        y = int(year)
    except (TypeError, ValueError):
        return ""
    today = today or _date.today()
    if today > _date(y, 12, 31):
        return ""
    return (f"Tax year {y} has not ended (today {today.isoformat()}) "
            f"— these are YEAR-TO-DATE figures, not a complete return; "
            f"re-run after Dec 31.")


def year_not_ended_note(year: Any, today: Optional[Any] = None) -> str:
    """year_not_ended_text as the `--json` report's `year_not_ended`
    field carries it (`NOTE: tax year ...`, unchanged for readers)."""
    t = year_not_ended_text(year, today)
    return ("NOTE: t" + t[1:]) if t else ""


# ---------------------------------------------------------------- render
# The console view only (docs/output-style.md): `--csv`, `--json` and the
# TXF are the exports and keep their bytes. manual_section() above is
# shared with the account .sum reports and keeps its layout.

def _table(header: Tuple[str, ...], rows: List[Tuple[str, ...]],
           right: set, key=0) -> List[str]:
    """A table fitted to the house width (lib/out.fit_table): too wide,
    one record per row headed by its `key` column(s)."""
    from taxjson.lib.out import fit_table
    if not rows:
        return ["  (no dispositions)"]
    aligns = [">" if i in right else "<" for i in range(len(header))]
    return fit_table(header, rows, aligns=aligns, key=key)


def _para(text: str, indent: str = "",
          hang: Optional[str] = None) -> List[str]:
    from taxjson.lib.out import wrap
    return wrap(text, None, indent, indent if hang is None else hang)


def _item(text: str, indent: str = "") -> List[str]:
    return _para(text, indent + "- ", indent + "  ")


def _row_notes(rows: List[Tuple[str, str]]) -> List[str]:
    """`- SYMBOL, SYMBOL: note` under a table, one item per distinct
    note (the prose column made the rows run off the width)."""
    by_note: Dict[str, List[str]] = {}
    for sym, note in rows:
        if note:
            syms = by_note.setdefault(note, [])
            if sym not in syms:
                syms.append(sym)
    out: List[str] = []
    for note, syms in by_note.items():
        out += _item(f"{', '.join(syms)}: {note}")
    return out


def _amounts(pairs: List[Tuple[str, float]], indent: str) -> List[str]:
    """`label:  amount` lines, labels padded and amounts right-aligned."""
    if not pairs:
        return []
    lw = max(len(k) for k, _ in pairs) + 1
    vals = [f"{M.shown(v, 2):,.2f}" for _, v in pairs]
    vw = max(len(v) for v in vals)
    return [f"{indent}{k + ':':<{lw}}  {v:>{vw}}"
            for (k, _), v in zip(pairs, vals)]


def _manual_console(rows: List[Dict[str, Any]], cur: str,
                    details: bool = True) -> List[str]:
    """manual_section() in the console layout."""
    if not rows:
        return []
    shorts = sum(1 for r in rows if is_short_cover(r))
    longs = len(rows) - shorts
    head = []
    if longs:
        head.append(f"{longs} sale(s) with no purchase in your files")
    if shorts:
        head.append(f"{shorts} short sale(s) covered with no short sale "
                    f"in your files")
    lines = [f"MANUAL REPORTING REQUIRED — {'; '.join(head)}"]
    if details:
        proceeds, cover = manual_totals(rows)
        known = []
        if longs:
            known.append(f"Unknown cost (.tt OPENING cost=unknown), "
                         f"proceeds {M.shown(proceeds, 2):,.2f} {cur}")
        if shorts:
            known.append(f"{'short' if longs else 'Short'} sales covered "
                         f"for {M.shown(cover, 2):,.2f} {cur}, their proceeds unknown")
        lines += _para(f"{'; '.join(known)}: NOT in the rows or totals "
                       f"above. Report each by hand once its cost is known "
                       f"(`tjs find-missing-history`).")
    else:
        from taxjson.lib.out import act
        lines.append(act("Not in the rows or totals above: report each "
                         "by hand", "tjs find-missing-history"))
    lines += _manual_table(rows)
    lines.append("")
    return lines


def _rounding_note(rep: Dict[str, Any]) -> List[str]:
    """The note `tjs sum` prints beside FOR THE RETURN (R1-166),
    for the export too (A2-1108): rows are rounded to the cent as
    filed, so the totals can differ from the gains files' unrounded
    sums by a few cents."""
    out = []
    if rep.get("form") == "8949":
        pairs = [("gain", sum(rep[f"part_{p}_totals"]["gain"]
                              for p in ("I", "II")),
                  rep.get("gain_unrounded")),
                 ("adjustment (g)", sum(rep[f"part_{p}_totals"]["adjustment"]
                                        for p in ("I", "II")),
                  rep.get("adjustment_unrounded"))]
    else:
        pairs = [("gain", (rep.get("totals") or {}).get("gain_all", 0.0),
                  rep.get("gain_unrounded")),
                 ("denied", sum(r.get("denied", 0.0)
                                for r in rep.get("rows") or []),
                  rep.get("denied_unrounded"))]
    for label, shown, raw in pairs:
        if raw is None:
            continue
        gap = M.shown(float(shown) - float(raw), 2)
        if abs(gap) >= 0.005:
            out += _item(f"Rows are rounded to the cent, as filed: the "
                         f"gains files' unrounded total {label} is "
                         f"{M.shown(float(raw), 2):,.2f} ({gap:+,.2f} on the totals "
                         f"above).")
    return out


def _year_note(rep: Dict[str, Any]) -> List[str]:
    """The year-not-ended sentence (the --json field keeps its NOTE:)."""
    t = str(rep.get("year_not_ended") or "")
    if t.startswith("NOTE: t"):
        t = "T" + t[len("NOTE: t"):]
    return _para(t) if t else []


def _not_in_rows(rep: Dict[str, Any]) -> List[str]:
    """One `! ` line each for what the books leave out of these rows and
    the gains files cannot tell (the wrapper counts them, as `tjs
    sum` does): an in-kind move with a registered account the run could
    not value, a sale with no purchase in the files (backlog TJ-0251)."""
    from taxjson.lib.out import act
    miss = rep.get("not_in_rows") or {}
    out: List[str] = []
    n = int(miss.get("in_kind_unbooked") or 0)
    if n:
        out.append(act(f"{n} in-kind move{'s' if n != 1 else ''} with a "
                       f"registered account NOT booked: NOT in these rows",
                       "tjs transfers"))
    n = int(miss.get("no_purchase") or 0)
    if n:
        out.append(act(f"{n} sale{'s' if n != 1 else ''} with no purchase "
                       f"in your files NOT in these rows",
                       "tjs find-missing-history"))
    n = int(miss.get("unbacked_cover") or 0)
    if n:
        out.append(act(f"{n} cover{'s' if n != 1 else ''} of a short opened "
                       f"before your files booked as a new long, NOT in "
                       f"these rows", "tjs find-missing-history"))
    return out


def render_8949(rep: Dict[str, Any], year: Optional[int], cur: str,
                details: bool = True) -> str:
    """The Form 8949 console report. `details` (--details): the notes
    and each row's filing-position note; the default view (docs/output-
    style.md, Essentials first) is the legend, the rows and totals, and
    one line per thing to act on."""
    lines = [f"FORM 8949 — Sales and Other Dispositions of Capital Assets, "
             f"tax year {year or '?'}, {cur}"]
    lines += _year_note(rep)
    if not details:
        lines.append("Each sale: (a) description, (b) acquired, (c) sold, "
                     "then its amounts; (h) = (d) − (e) + (g).")
    lines.append("")
    header = ("(a) DESCRIPTION", "(b) ACQUIRED", "(c) SOLD",
              "(d) PROCEEDS", "(e) COST", "(f)", "(g) ADJ",
              "(h) GAIN(LOSS)")

    def _rows_table(rows):
        # Too wide: one record per sale headed by (a)-(c) (the legend
        # names them in the default view).
        return _table(header, [
            (r["description"], r["date_acquired"], r["date_sold"],
             f"{M.shown(r['proceeds'], 2):,.2f}", f"{M.shown(r['cost'], 2):,.2f}", r["code"],
             f"{M.shown(r['adjustment'], 2):,.2f}" if r["code"] else "",
             f"{M.shown(r['gain'], 2):,.2f}") for r in rows], right={3, 4, 6, 7},
            key=0 if details else (0, 1, 2))

    def _totals(label: str, t: Dict[str, float]) -> List[str]:
        head = f"  {label}"
        if not details and label.startswith("TOTALS (to Schedule D part"):
            # A heading in the default view: TOTALS — SCHEDULE D PART I.
            head = ("TOTALS — SCHEDULE D PART "
                    + label.split()[-1].rstrip("):"))
        return [head] + _amounts(
            [("proceeds", t['proceeds']), ("cost", t['cost']),
             ("adjustments", t['adjustment']), ("gain", t['gain'])],
            "    ")

    has_da = any(r.get("digital_asset") for p in ("I", "II")
                 for r in rep[f"part_{p}"])
    for part, label in (("I", "PART I — SHORT-TERM"),
                        ("II", "PART II — LONG-TERM")):
        rows = rep[f"part_{part}"]
        t = rep[f"part_{part}_totals"]
        if not rows and not details:
            lines.append(f"{label}: no dispositions")
            lines.append("")
            continue
        lines.append(label)
        if has_da:
            # One table per checkbox group: securities, then the 2025+
            # digital-asset boxes (A2-0482).
            for g in rep.get("groups") or []:
                if g["part"] != part:
                    continue
                boxes = g["boxes"].replace("/", ", ", 1).replace(
                    "/", " OR ")
                lines.append(f"BOX {boxes} — "
                             + ("digital assets (Form 1099-DA)"
                                if g["digital_asset"] else
                                "securities (Form 1099-B)"))
                lines += _rows_table([r for r in rows
                                      if r["digital_asset"]
                                      == g["digital_asset"]])
                lines += _totals(f"Box {g['boxes']} totals:", g["totals"])
        else:
            lines += _rows_table(rows)
        if rows:
            lines += _totals(f"TOTALS (to Schedule D part {part}):", t)
        for r in rows:
            if r.get("filing_position") and details:
                lines += _item(f"{r['description']} sold {r['date_sold']}: "
                               f"{r['filing_position']['note']}", "  ")
        lines.append("")
    lines += section_1256_lines(rep, cur)
    lines += _manual_console(rep.get("manual_reporting_required") or [],
                             cur, details)
    _miss = _not_in_rows(rep)
    if _miss and details:
        lines += _miss + [""]
    if not details:
        from taxjson.lib.out import act
        lines += _miss
        _cmd = "tjs form-export --details"
        _fp = sum(1 for p in ("I", "II") for r in rep[f"part_{p}"]
                  if r.get("filing_position"))
        if _fp:
            lines.append(act(f"{_fp} row(s) carry a filing position "
                             f"(.tt ALLOWLOSS)", _cmd))
        lines.append(f"Code W: wash sale, (g) the loss added back; check "
                     f"the 8949 box for each part — {_cmd}")
        return "\n".join(ln for i, ln in enumerate(lines)
                         if ln or i + 1 < len(lines))
    lines.append("NOTES")
    lines += _item("Code W rows are wash sales; column (g) is the "
                   "disallowed loss added back, so (h) is the allowed "
                   "amount.")
    _y = year or rep.get("year")
    if _y is not None and int(_y) >= DIGITAL_ASSET_BOXES_FROM:
        lines += _item("Check the correct 8949 box: securities A/B/C "
                       "(short-term) or D/E/F (long-term) by whether the "
                       "1099-B reported basis; digital assets (crypto "
                       "accounts) G/H/I or J/K/L by whether a Form 1099-DA "
                       "reported them and their basis — never A-F.")
    else:
        lines += _item("Check the correct 8949 box (A/B/C, D/E/F) "
                       "against whether your broker reported basis on the "
                       "1099-B.")
    lines += _item("Short sales show the cover date in both date columns.")
    if any(r.get("filing_position") for p in ("I", "II")
           for r in rep[f"part_{p}"]):
        lines += _item(FILING_POSITION_NOTE_8949)
    lines += _rounding_note(rep)
    lines += _item("Not tax advice; reconcile against your 1099-B before "
                   "filing.")
    return "\n".join(lines)


def render_schedule3(rep: Dict[str, Any], year: Optional[int],
                     cur: str, details: bool = True) -> str:
    """The Schedule 3 console report. `details` (--details): each line's
    form label, the rows' notes and the NOTES; the default view (docs/
    output-style.md, Essentials first) is the legend, the rows and line
    totals, and one line per thing to act on or not to miss."""
    year = year or rep.get("year")
    lines = [f"SCHEDULE 3 — Capital Gains (or Losses), tax year "
             f"{year or '?'}, {cur}"]
    lines += _year_note(rep)
    if not details:
        lines.append("GAIN(LOSS) = PROCEEDS − ACB − OUTLAYS, the allowed "
                     "amount; 100% amounts (the form applies the rate).")
    lines.append("")
    header = ("UNITS", "SYMBOL", "ACQ. YEAR", "PROCEEDS", "ACB",
              "OUTLAYS", "GAIN(LOSS)")
    by_line = rep.get("lines") or []
    if not by_line:
        spec = schedule3_line("shares", year)
        lines.append(line_title(spec).upper())
        if details:
            lines += _para(spec['label'])
        lines += _table(header, [], right=set())
        lines.append("")
    for ln in by_line:
        # The default view's heading: the period, then its lines.
        lines.append(ln['title'].upper() if details else
                     ln['title'].replace(", lines ", " — lines ").upper())
        if details:
            lines += _para(ln['label'])
        sel = [r for r in rep["rows"] if r["line_key"] == ln["key"]]
        table = [(f"{r['units']:,.8f}".rstrip("0").rstrip(".") or "0",
                  r["symbol"],
                  str(r["acq_year"]), f"{M.shown(r['proceeds'], 2):,.2f}",
                  f"{M.shown(r['acb'], 2):,.2f}", f"{M.shown(r['outlays'], 2):,.2f}",
                  f"{M.shown(r['gain'], 2):,.2f}")
                 for r in sel]
        lines += _table(header, table, right={0, 3, 4, 5, 6})
        lines += _amounts(
            [(f"Line {ln['proceeds_code']} (proceeds of disposition)",
              ln['proceeds']),
             (f"Line {ln['gain_code']} (gain/loss)", ln['gain'])], "  ")
        if details:
            lines += _row_notes([(r["symbol"], r["notes"]) for r in sel])
        lines.append("")
    lines += _manual_console(rep.get("manual_reporting_required") or [],
                             cur, details)
    _miss = _not_in_rows(rep)
    if _miss and details:
        lines += _miss + [""]
    if not details:
        from taxjson.lib.out import act
        lines += _miss
        _cmd = "tjs form-export --details"
        _rows = rep.get("rows") or []
        _fp = sum(1 for r in _rows if r.get("filing_positions"))
        if _fp:
            lines.append(act(f"{_fp} row(s) carry a filing position "
                             f"(.tt ALLOWLOSS)", _cmd))
        _den = [r for r in _rows if (r.get("denied") or 0) > 0.005]
        if _den:
            lines.append(f"{len(_den)} row(s) with a denied superficial "
                         f"loss: the ACB shown is reduced by it — "
                         f"tjs wash-sales")
        # An in-kind contribution's loss is nil for good (s.40(2)(g)
        # (iv), CA-INKIND-03), never a superficial loss: `tjs
        # wash-sales` lists none of it (backlog TJ-0443).
        _ikd = [r for r in _rows
                if (r.get("denied_contribution") or 0) > 0.005]
        if _ikd:
            lines.append(f"{len(_ikd)} row(s) with a loss denied for good "
                         f"on an in-kind contribution (s.40(2)(g)(iv)) — "
                         f"{_cmd}")
        lines.append("Not in the rows: FX on cash (15300), slip capital "
                     "gains (17400, 17600) — " + _cmd)
        return "\n".join(lines).rstrip("\n")
    lines.append("NOTES")
    if year is not None and int(year) == PERIOD_YEAR:
        # The 2024 form's two periods (A2-0166).
        lines += _item("The 2024 Schedule 3 splits each line by the date "
                       "of the disposition: Period 1 (January 1 to June "
                       "24, 2024) — shares and fund units on 10689/10690, "
                       "options, futures, crypto-assets and other "
                       "properties on 10693/10694; Period 2 (June 25 to "
                       "December 31) — 13199/13200 and 15199/15300. A "
                       "security sold in both periods has a row in each.")
    else:
        lines += _item("Each disposition is on the line for its property "
                       "type: shares and fund units on 13199/13200; "
                       "options, futures and other properties on "
                       "15199/15300 (T4037); "
                       # The same routing the rows use (S032-23): a
                       # second, separate year test could contradict them.
                       + ("crypto-assets on 15200/15301."
                          if schedule3_line("crypto", year)["key"]
                          == "crypto"
                          else "crypto-assets with the other properties "
                               "(15199/15300) for this year."))
    lines += _item("GAIN(LOSS) is the ALLOWED amount. Every row foots: "
                   "PROCEEDS − ACB − OUTLAYS = GAIN(LOSS); where a "
                   "superficial loss was denied the ACB shown is reduced "
                   "by the denied amount, which is added to the ACB of "
                   "the replacement property instead — except a denial "
                   "caused by a registered-account or affiliated-person "
                   "acquisition, which is permanent for this return with "
                   "no ACB addition here (an affiliated person adds it to "
                   "their own ACB, s.53(1)(f); noted per row).")
    if any(r.get("filing_positions") for r in rep.get("rows") or []):
        lines += _item(FILING_POSITION_NOTE_S3)
    lines += _item("Apply the inclusion rate on Schedule 3 itself; these "
                   "are 100% amounts.")
    lines += _item("PROCEEDS re-adds sell-side commissions so OUTLAYS can "
                   "be shown separately (a commission rebate, and a short "
                   "sale's opening commission, stay netted in PROCEEDS); "
                   "the gain is unchanged.")
    lines += _rounding_note(rep)
    lines += _item("FX gains on foreign cash (s.39(1.1), `tjs "
                   "fx-cash`) are not in these rows; T4037 puts them on "
                   "line 15300.")
    lines += _item("Capital gains paid out by funds and trusts are not "
                   "in these rows either: T3 box 21 goes on line 17600 "
                   "and T5/T5013 box 18 on line 17400"
                   + (" (for 2024, the slips' Period 1 amounts on 17599 "
                      "and 17399, Period 2 on 17600 and 17400)"
                      if year is not None and int(year) == PERIOD_YEAR
                      else "")
                   + ". Enter them from the "
                   "slips; the books carry those distributions as "
                   "dividends, so line 19700 is these rows plus the slip "
                   "lines.")
    lines += _item("Not tax advice; reconcile against your T5008 slips "
                   "before filing (`tjs reconcile-slips`).")
    return "\n".join(lines)


def write_csv(rep: Dict[str, Any], path: Path) -> None:
    """Write through a new owner-only temp file of this writer's own
    beside `path` and replace (lib/safe_write.atomic_open): a failed
    write left a truncated CSV in place of the good one (S032-24), and
    the fixed `<path>.part` temp it used made a leftover or concurrent
    one fail the write, or be deleted (the issue #9 pattern)."""
    from taxjson.lib.safe_write import atomic_open
    with atomic_open(path, newline="") as f:
        _rows_csv(rep, f)


_write_csv = write_csv      # the old name, kept for its importers


# A text cell a spreadsheet would read as a formula: `=`, `+`, `-`, `@`,
# a tab or a carriage return first. A description, symbol, account or
# note comes from the user's exports and files, so such a cell gets a
# leading `'` (shown as text, never evaluated); a number — a negative
# gain included — is written as is (2026-10 security review LOW a).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def spreadsheet_cell(v: Any) -> Any:
    """`v` safe to open in a spreadsheet (see _FORMULA_START)."""
    if (isinstance(v, str) and v.startswith(_FORMULA_START)
            and not _PLAIN_NUMBER.fullmatch(v)):
        return "'" + v
    return v


class _SafeCsvWriter:
    """csv.writer whose every row goes through spreadsheet_cell."""

    def __init__(self, f) -> None:
        self._w = csv.writer(f)

    def writerow(self, row) -> None:
        self._w.writerow([spreadsheet_cell(v) for v in row])


def _rows_csv(rep: Dict[str, Any], f) -> None:
    w = _SafeCsvWriter(f)
    if rep["form"] == "8949":
        w.writerow(["part", "description", "date_acquired", "date_sold",
                    "proceeds", "cost", "code", "adjustment",
                    "gain", "account", "boxes", "note"])
        for part in ("I", "II"):
            for r in rep[f"part_{part}"]:
                w.writerow([part, r["description"], r["date_acquired"],
                            r["date_sold"], r["proceeds"], r["cost"],
                            r["code"], r["adjustment"], r["gain"],
                            r["account"], r.get("boxes", ""),
                            (r.get("filing_position") or {}).get("note",
                                                                 "")])
        # §1256 contracts: Form 6781 by hand, never an 8949 row.
        for r in rep.get("section_1256") or []:
            w.writerow(["6781", f"{r['description']} ({r['kind']})",
                        r["date_acquired"], r["date_sold"], "", "",
                        "", "", r["gain"], r["account"]])
        # Unknown-cost dispositions: flagged rows,
        # blank cost/gain, never mistaken for a computed row.
        for m in rep.get("manual_reporting_required") or []:
            _p, _c = manual_figures(m)
            _desc = (f"{_qty_str(abs(float(GainRow(m).quantity or 0.0)))}"
                     f" {m.get('symbol') or ''}")
            if _c is not None:
                # A short covered with its short sale before the data:
                # (e) what covering cost; (d) the short sale's proceeds
                # unknown, left blank (backlog TJ-0497).
                w.writerow(["MANUAL", f"{_desc} (short sale)", "",
                            m.get("date") or "", "", M.shown(_c, 2), "",
                            "", "", m.get("account") or "", "",
                            "short sale before your files: its proceeds "
                            "unknown - report by hand"])
                continue
            w.writerow(["MANUAL", _desc, "",
                        m.get("date") or "",
                        M.shown(_p, 2),
                        "", "", "", "", m.get("account") or ""])
    else:
        w.writerow(["line", "proceeds_line", "gain_line", "property",
                    "units", "symbol", "acq_year", "proceeds", "acb",
                    "outlays", "gain", "denied", "notes"])
        for r in rep["rows"]:
            w.writerow([r["line"], r["proceeds_line"], r["gain_line"],
                        r["property"], r["units"], r["symbol"],
                        r["acq_year"], r["proceeds"], r["acb"],
                        r["outlays"], r["gain"], r["denied"],
                        r["notes"]])
        for m in rep.get("manual_reporting_required") or []:
            _p, _c = manual_figures(m)
            if _c is not None:
                # A short covered with its short sale before the data:
                # the cover cost is the ACB, the proceeds (what the
                # short sale brought in) unknown (backlog TJ-0497).
                w.writerow(["MANUAL", "", "", "",
                            abs(float(GainRow(m).quantity or 0.0)),
                            m.get("symbol") or "", "", "", M.shown(_c, 2),
                            "", "", "",
                            "short sale before your files, its proceeds "
                            "unknown; acb is the cover cost - report by "
                            "hand"])
                continue
            w.writerow(["MANUAL", "", "", "",
                        abs(float(GainRow(m).quantity or 0.0)),
                        m.get("symbol") or "", "",
                        M.shown(_p, 2),
                        "", "", "", "",
                        "no purchase in your files, cost unknown "
                        "(.tt OPENING cost=unknown) - report by hand"])


@guard_main("taxjson-form-export")
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render taxjson gains into IRS Form 8949 or CRA "
                    "Schedule 3 shaped output.")
    parser.add_argument("files", nargs="+", type=Path, metavar="FILE",
                        help="Year-scoped <account>_gains.json files "
                             "(prefer the wash-adjusted variants)")
    parser.add_argument("--crypto", action="append", type=Path,
                        default=[], metavar="FILE",
                        help="A gains file whose dispositions are "
                             "crypto-assets (repeatable). Schedule 3: "
                             "line 7 — 15200/15301 — from 2025, "
                             "15199/15300 before. Form 8949: from 2025 "
                             "the digital-asset boxes G/H/I and J/K/L. "
                             "The `tjs form-export` wrapper passes "
                             "the crypto = true accounts here.")
    parser.add_argument("--form", required=True,
                        choices=["8949", "schedule3", "txf"])
    from taxjson.lib.country import add_country_argument
    add_country_argument(parser, help="Whose return the rows are for "
                         "(required): canada (--form schedule3) | usa "
                         "(--form 8949 / txf). The `tjs form-export` "
                         "wrapper passes the project's country.")
    parser.add_argument("--box", default="A", choices=["A", "B", "C"],
                        help="TXF only: 8949 checkbox pairing for "
                             "securities — A/D (basis on the 1099-B, the "
                             "default for covered securities), B/E "
                             "(1099-B without basis), C/F (no 1099-B). "
                             "From 2025 digital assets (--crypto) belong "
                             "on boxes G-L, which TXF cannot carry: they "
                             "are left out with a warning")
    parser.add_argument("--out", type=Path, default=None,
                        help="TXF only: write the .txf here instead of "
                             "stdout")
    parser.add_argument("--year", type=tax_year, default=None,
                        help="Defensive year filter (pipeline gains files "
                             "are already year-scoped) and the form's tax "
                             "year (default: the year the gains files "
                             "record, else the latest row's)")
    parser.add_argument("--base-currency", default="",
                        type=norm_currency,
                        help="The amounts' currency. Must be the return's "
                             "own currency (CAD for Schedule 3, USD for "
                             "8949/txf) — refused otherwise, as are rows in "
                             "any other currency; not just a header label")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Also write the rows as CSV to this path")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    # The wrapper's run-state problems (cli/common._run_state_problems),
    # carried into --json's readiness record.
    parser.add_argument("--run-state-problem", action="append",
                        default=[], help=argparse.SUPPRESS)
    # ...and what the books leave out of the rows that the gains files
    # cannot tell, counted as `tjs sum` counts them (backlog TJ-0251).
    parser.add_argument("--in-kind-unbooked", type=int, default=0,
                        help=argparse.SUPPRESS)
    parser.add_argument("--no-purchase", type=int, default=0,
                        help=argparse.SUPPRESS)
    parser.add_argument("--unbacked-cover", type=int, default=0,
                        help=argparse.SUPPRESS)
    parser.add_argument("--date-basis", choices=("settle", "trade"),
                        default=None,
                        help="Date the gains files were year-scoped on "
                             "(the wrapper passes the project's "
                             "tax_date). Default: the files' own "
                             "summary.tax_date_basis, else the form's "
                             "convention (8949/txf trade, Schedule 3 "
                             "settle).")
    parser.add_argument("--brief", action="store_true",
                        help="The essentials only (what `tjs form-export` prints "
                             "without --details): the notes and each "
                             "message's detail are left out")
    parser.add_argument("--ticker-map", dest="ticker_map", metavar="FILE",
                        default=None,
                        help="The project's ticker.map: its market lines "
                             "(a SECFUT root stays on Form 8949, an "
                             "INDEXOPT root is on Form 6781). `tjs "
                             "form-export` reads the project's; without "
                             "this option the TAXJSON_TICKER_MAP variable, "
                             "else none")
    args = parser.parse_args(argv)
    if args.form == "txf" and args.json:
        # TXF is its own format: --json printed the TXF text, not JSON,
        # under a warning (the --json contract: one JSON object or a
        # refusal). A usage error, before any file is read.
        _out.error("--json does not apply to --form txf (TXF is its own "
                   "format, not JSON)", prog="taxjson-form-export",
                   details=["Drop --json to print the TXF (or --out FILE "
                            "to write it); `--form 8949 --json` is the "
                            "same rows as JSON."])
        return 2
    if args.ticker_map:
        # Before any row is classified (backlog TJ-1029).
        if not Path(args.ticker_map).is_file():
            sys.exit(exit_text(f"taxjson-form-export: no such file: "
                               f"--ticker-map {args.ticker_map}"))
        from taxjson.lib.markets import use_ticker_map
        use_ticker_map(args.ticker_map)

    # The same ownership table `taxjson` dispatch enforces: Schedule 3
    # is Canada's, Form 8949 / TXF the US's (partition COMMANDS-04; the
    # standalone was not gated).
    from taxjson.lib.country import command_country_problem
    _cp = command_country_problem("form-export", args.country,
                                  variant=args.form)
    if _cp:
        _out.put(f"taxjson-form-export: {_cp}", file=sys.stderr)
        return 2

    for p in args.files + args.crypto:
        if not p.exists():
            _out.put(f"taxjson-form-export: no such file: {p}", file=sys.stderr)
            return 2

    try:
        return _main(args)
    except (OSError, ValueError) as e:
        # A truncated gains file crashed with a raw JSONDecodeError
        # traceback (R1-277); the advice once (TJ-2968).
        from taxjson.lib.json_input import rerun_advice
        print("taxjson-form-export: could not read the gains files: "
              + rerun_advice(str(e), " — re-run `tjs run` to rebuild them."),
              file=sys.stderr)
        return 2


def _files_date_basis(paths: List[Path]) -> Optional[str]:
    """The tax_date basis the gains files record (summary.
    tax_date_basis); None when none records one. Files that disagree
    are refused."""
    seen = set()
    for p in paths:
        b = ((load_json(p) or {}).get("summary") or {}).get(
            "tax_date_basis")
        if b in ("settle", "trade"):
            seen.add(b)
    if len(seen) > 1:
        raise ValueError(f"the gains files were built on different date "
                         f"bases ({', '.join(sorted(seen))}) — rebuild "
                         f"them with one `tjs run`")
    return seen.pop() if seen else None


def _files_year(paths: List[Path]) -> Optional[int]:
    """The tax year the gains files record (summary.year), when every
    file that records one names the same year; else None (backlog
    TJ-0901: the form's year is the files' own, not one guessed from
    the rows' dates)."""
    seen = set()
    for p in paths:
        y = str(((load_json(p) or {}).get("summary") or {}).get("year")
                or "")
        if len(y) == 4 and y.isdigit():
            seen.add(int(y))
    return seen.pop() if len(seen) == 1 else None


def _source_years(paths: List[Path], date_key: str) -> Dict[int, List[str]]:
    """{tax year: [file, ...]}: each gains file's year — the one it
    records (summary.year), else the years its disposition rows fall in
    on `date_key`. Without --year, files of more than one year are
    refused: a 2024 and a 2025 file merged into one return labelled
    2025, with the 2024 sale in it (backlog TJ-0993)."""
    out: Dict[int, List[str]] = {}
    for p in paths:
        data = load_json(p) or {}
        y = str((data.get("summary") or {}).get("year") or "")
        if len(y) == 4 and y.isdigit():
            years = {int(y)}
        else:
            rows = [e for e in data.get("transactions") or []
                    if gains_row_kind(e) != "income"]
            rows += list(data.get("manual_reporting_required") or [])
            years = set()
            for e in rows:
                d = str(e.get(date_key) or e.get("date") or "")[:4]
                if d.isdigit():
                    years.add(int(d))
        for yr in years:
            out.setdefault(yr, []).append(str(p))
    return out


def _home_ccy(args) -> str:
    from taxjson.lib.country import home_currency
    return home_currency(args.country) if args.country else (
        "USD" if args.form in ("8949", "txf") else "CAD")


def _check_currency(paths: List[Path], base: str) -> None:
    """Refuse gains rows in another currency than the export's: the
    native work/<acct>_raw_gains.json sits beside the converted file
    and summed USD, CAD and GBP under a CAD label (S032-13)."""
    base = (base or "").upper()
    for p in paths:
        other: Dict[str, int] = {}
        blank = 0
        doc = load_json(p)
        # The unknown-cost rows too: they are listed with the return's
        # currency, so a USD row in a CAD return showed its proceeds as
        # CAD (backlog TJ-0496).
        for e, manual in ([(e, False) for e in doc.get("transactions")
                           or []]
                          + [(e, True) for e in
                             doc.get("manual_reporting_required") or []]):
            if not isinstance(e, dict) or (
                    e.get("action") in _INCOME_ACTIONS):
                continue
            c = str(e.get("currency") or "").upper()
            if c and c != base:
                other[c] = other.get(c, 0) + 1
            elif not c and "qty" in e and (manual or "gain" in e):
                blank += 1
        if blank:
            # A disposition with no currency cannot be verified as in
            # the return's currency (A2-0652): said, not skipped.
            from taxjson.lib.out import warn as _warn
            _warn(f"{p}: {blank} disposition(s) carry no currency — "
                  f"cannot verify they are in {base}",
                  details=["The pipeline's converted gains files always "
                           "carry it."])
        if other:
            got = ", ".join(f"{n} {c}" for c, n in sorted(other.items()))
            _out.put(f"taxjson-form-export: {p}: dispositions in another "
                     f"currency than {base} ({got} row(s)) — pass the "
                     f"converted <account>_gains(_wash).json, not the native "
                     f"*_raw_gains.json (`tjs form-export` does)",
                     file=sys.stderr)
            raise SystemExit(2)


def _unique_paths(paths: List[Path]) -> Tuple[List[Path], List[Path]]:
    """(each file once, in order; the repeats left out): one file named
    twice — explicit paths and a shell glob that overlap — summed its
    dispositions twice into the form (backlog TJ-0905)."""
    seen: set = set()
    once: List[Path] = []
    again: List[Path] = []
    for p in paths:
        r = Path(p).resolve()
        if r in seen:
            again.append(p)
            continue
        seen.add(r)
        once.append(p)
    return once, again


def _main(args) -> int:
    args.files, _again = _unique_paths(list(args.files))
    if _again:
        from taxjson.lib.out import note as _note
        _note(f"{', '.join(str(p) for p in _again)}: given more than "
              f"once — each file is read once")
    # Rows are picked by the date the gains files were scoped on: an
    # explicit tax_date different from the country default dropped a
    # year-end sale from the export (R1-200). Without a recorded basis,
    # IRS attributes the year by TRADE date and CRA by SETTLEMENT date.
    basis = args.date_basis or _files_date_basis(
        list(args.files) + list(args.crypto))
    if basis:
        date_key = "date" if basis == "trade" else "date_settle"
    else:
        date_key = "date" if args.form in ("8949", "txf") else "date_settle"
    _crypto = {p.resolve() for p in args.crypto}
    _home = _home_ccy(args)
    if args.base_currency and args.base_currency.strip().upper() != _home:
        # --base-currency was compared with the rows only, so passing
        # the rows' own currency exported a Canadian Schedule 3 in USD
        # (or a Form 8949 in CAD) at exit 0 (A2-0652, A2-1118). The
        # return is filed in the country's currency (tax-logic
        # CA-CTRY-03; Form 8949 in USD).
        _out.put(f"taxjson-form-export: --base-currency "
                 f"{args.base_currency} does not match the {args.country} "
                 f"return's currency {_home} — the form is filed in {_home}: "
                 f"pass the converted <account>_gains(_wash).json "
                 f"(`tjs run` converts them; `tjs form-export` "
                 f"passes them).", file=sys.stderr)
        raise SystemExit(2)
    _check_currency(list(args.files) + list(args.crypto), _home)
    if args.year is None:
        _years = _source_years(
            list(_unique_paths(list(args.files) + list(args.crypto))[0]),
            date_key)
        if len(_years) > 1:
            _named = "; ".join(f"{y}: {', '.join(fs)}"
                               for y, fs in sorted(_years.items()))
            _out.put(f"taxjson-form-export: the gains files are for more "
                     f"than one tax year ({_named}) — a return is one year: "
                     f"pass --year YEAR, or only that year's files "
                     f"(`tjs form-export` passes the project's year).",
                     file=sys.stderr)
            raise SystemExit(2)
    entries, tainted = load_dispositions(
        [p for p in args.files if p.resolve() not in _crypto],
        args.year, date_key)
    if args.crypto:
        c_entries, c_tainted = load_dispositions(
            sorted(_crypto), args.year, date_key)
        entries += mark_crypto(c_entries)
        tainted += c_tainted
    # The form's tax year: --year, else the year the gains files
    # record, else the latest row's year on the files' date basis (a
    # December trade settling in January was the next year's Form 8949;
    # backlog TJ-0901).
    form_year = args.year
    if form_year is None:
        form_year = (_files_year(list(args.files) + list(args.crypto))
                     or _infer_year(entries, date_key))
    if args.form == "schedule3" and any(
            e.get("term") in ("SHORT_TERM", "LONG_TERM") for e in entries):
        # Short/long-term terms exist only in the US engine's output:
        # Schedule 3 from US-computed gains (FIFO, §1091) would put US
        # numbers on a Canadian return.
        raise SystemExit(
            exit_text("taxjson-form-export: these gains files carry short/long-term "
                      "terms — they were computed by the US engine; Schedule 3 needs "
                      "a country=canada gains file (re-run `tjs run` in the "
                      "Canadian project)."))
    manual, manual_proceeds, manual_cover_cost = _export_manual(args, date_key, tainted)

    rep_8949 = (build_8949(entries, form_year)
                if args.form in ("8949", "txf") else None)
    if rep_8949 is not None:
        _s1256 = rep_8949["section_1256_totals"]
        if _s1256["dispositions"]:
            from taxjson.lib.out import warn as _warn
            _warn(f"{_s1256['dispositions']} §1256 contract "
                  f"disposition(s) (net {M.shown(_s1256['gain'], 2):,.2f}) are NOT in "
                  f"the {'TXF records' if args.form == 'txf' else 'Form 8949 rows'}",
                  details=[SEC1256_NOTE
                           + (" `--form 8949` lists them."
                              if args.form == "txf" else "")])
    if args.form == "txf":
        _export_txf(args, rep_8949, form_year)
        return 0

    if args.form == "8949":
        rep = rep_8949
        rep["currency"] = _home
    else:
        rep = build_schedule3(entries, form_year, date_key)
        rep["currency"] = _home
    rep["manual_reporting_required"] = manual
    rep["manual_proceeds"] = manual_proceeds
    rep["manual_cover_cost"] = manual_cover_cost
    _ynote = year_not_ended_note(form_year or rep.get("year"))
    if _ynote:
        rep["year_not_ended"] = _ynote
    if not args.json and (args.in_kind_unbooked or args.no_purchase
                          or args.unbacked_cover):
        # The text view's `! ` lines (--json: the readiness record).
        rep["not_in_rows"] = {"in_kind_unbooked": args.in_kind_unbooked,
                              "no_purchase": args.no_purchase,
                              "unbacked_cover": args.unbacked_cover}
    if args.form == "8949":
        text = render_8949(rep, form_year, rep["currency"],
                           not args.brief)
    else:
        text = render_schedule3(rep, form_year, rep["currency"],
                                not args.brief)

    if args.csv:
        try:
            write_csv(rep, args.csv)
        except OSError as e:
            # --csv pointed at a directory (or unwritable path) used
            # to be a raw IsADirectoryError traceback that also lost
            # the report text (REVIEW #39). Print the report, THEN
            # fail with a clean message and nonzero exit.
            if not args.json:
                # A live exact Doc: each line printed when it is added, as the
                # print() it replaces printed it.
                d = _out.Doc(exact=True, width_=0, live=True)
                d.line(text)
            sys.exit(exit_text(f"taxjson-form-export: cannot write --csv "
                               f"{args.csv}: {e}"))
        _out.put(f"wrote {args.csv}", file=sys.stderr)
    _export_report(args, rep, tainted, manual, text)
    return 0


def _export_manual(args: argparse.Namespace, date_key: str,
                   tainted: int) -> Tuple[List[Dict[str, Any]], Any, Any]:
    """form-export: the dispositions of unknown cost (MANUAL REPORTING),
    each file read once, their proceeds and cover cost, and the
    warning that they are not in the form's rows."""
    # Each file once: the wrapper passes a crypto account's gains file
    # positionally AND as --crypto, which listed every crypto unknown-
    # cost disposition twice under MANUAL REPORTING (A2-0113).
    _seen_manual: set = set()
    _manual_paths = []
    for _p in list(args.files) + list(args.crypto):
        if _p.resolve() not in _seen_manual:
            _seen_manual.add(_p.resolve())
            _manual_paths.append(_p)
    # A short covered with its short sale before the data carries the
    # cover cost (negated) in the engine's `proceeds`: it is named as
    # the cover cost, its proceeds unknown, never summed into the
    # proceeds (backlog TJ-0497).
    manual = [dict(m, cover_cost=M.shown(manual_figures(m)[1], 2),
                   short_sale_proceeds=None) if is_short_cover(m) else m
              for m in load_manual_rows(_manual_paths, args.year,
                                        date_key)]
    _mp, _mc = manual_totals(manual)
    manual_proceeds = M.shown(_mp, 2)
    manual_cover_cost = M.shown(_mc, 2)
    if tainted or manual:
        def _named(m):
            _p, _c = manual_figures(m)
            return (f"{m.get('symbol')} {m.get('date')} "
                    + (f"({M.shown(_p, 2):,.2f})" if _c is None
                       else f"(short covered for {M.shown(_c, 2):,.2f}, its proceeds "
                            f"unknown)"))
        _names = ", ".join(_named(m) for m in manual[:8])
        _more = f" (+{len(manual) - 8} more)" if len(manual) > 8 else ""
        _shorts = sum(1 for m in manual if is_short_cover(m))
        _known = ([f"proceeds {manual_proceeds:,.2f}"]
                  if len(manual) > _shorts or not manual else [])
        if _shorts:
            _known.append(f"cover cost {manual_cover_cost:,.2f} of "
                          f"{_shorts} short sale(s) whose proceeds are "
                          f"unknown")
        from taxjson.lib.out import warn as _warn
        _warn(f"{max(tainted, len(manual))} disposition(s) with an "
              f"unknown cost (no purchase in your files) are NOT in the "
              f"{'TXF' if args.form == 'txf' else 'form'} rows or totals "
              f"— {'; '.join(_known)}",
              details=[f"{_names}{_more}.",
                       "Report them by hand once their cost is known "
                       "(`tjs find-missing-history`); they are listed "
                       "in the MANUAL REPORTING section."])
    return manual, manual_proceeds, manual_cover_cost


def _export_txf(args: argparse.Namespace, rep_8949: Optional[Dict[str, Any]],
                form_year: Any) -> None:
    """form-export: `--form txf`, its warnings, and the TXF document
    written to --out or printed."""
    from taxjson.lib.out import warn as _warn
    if args.csv:
        # (--json is refused before: main.)
        _warn("--csv has no effect with --form txf (TXF is its "
              "own format) — ignored")
    # TXF rides on the 8949 model — same rows, same code-W math.
    rep = rep_8949
    _ynote = year_not_ended_text(form_year)
    if _ynote:
        _warn(_ynote)
    doc = build_txf(rep, args.box)
    _da_rows = [r for p in ("I", "II") for r in rep[f"part_{p}"]
                if r.get("digital_asset")]
    if _da_rows:
        _warn(f"{len(_da_rows)} digital-asset disposition(s) "
              f"(proceeds {M.shown(sum(r['proceeds'] for r in _da_rows), 2):,.2f}) "
              f"are NOT in the TXF",
              details=["From 2025 they go on Form 8949 boxes G-L, "
                       "which have no TXF reference number taxjson "
                       "knows — enter them by hand (`--form 8949` "
                       "lists them by box)."])
    _fp_rows = [r for p in ("I", "II") for r in rep[f"part_{p}"]
                if r.get("filing_position")
                and not r.get("digital_asset")]
    if _fp_rows:
        # TXF has no note field: the record carries the claimed loss
        # (no wash-sale amount) and says nothing (pre-release M7).
        _warn(f"{len(_fp_rows)} TXF record(s) carry a filing position "
              f"(.tt ALLOWLOSS): imported with no wash-sale amount, "
              f"the loss claimed",
              details=[f"- {r['description']} sold {r['date_sold']}: "
                       f"{r['filing_position']['note']}"
                       for r in _fp_rows]
              + [FILING_POSITION_NOTE_8949])
    if args.out:
        from taxjson.lib.safe_write import write_atomic
        try:
            write_atomic(args.out, doc, encoding="ascii")
        except (OSError, UnicodeEncodeError) as e:
            sys.exit(exit_text(f"taxjson-form-export: cannot write --out "
                               f"{args.out}: {e}"))
        n = sum(1 for p in ("I", "II") for r in rep[f"part_{p}"]
                if not r.get("digital_asset"))
        _out.put(f"wrote {n} TXF record(s) (box {args.box}) to "
                 f"{args.out}", file=sys.stderr)
    else:
        sys.stdout.write(doc)


def _export_report(args: argparse.Namespace, rep: Optional[Dict[str, Any]], tainted: int,
                   manual: List[Dict[str, Any]], text: str) -> None:
    """form-export: the report printed, or with --json the document with
    its readiness record."""
    # A live exact Doc: each line printed when it is added, as the
    # print() it replaces printed it.
    d = _out.Doc(exact=True, width_=0, live=True)
    if args.json:
        # Whether these rows are the whole figure (lib/readiness): the
        # run state, the unknown-cost sales left out, the engine's
        # conservation warnings (2026-10 critique).
        from taxjson.lib import readiness as _RD
        if isinstance(rep, dict):
            rep["readiness"] = _RD.build(
                run_state=args.run_state_problem,
                unknown_cost=max(tainted, len(manual)),
                no_purchase=args.no_purchase,
                unbacked_cover=args.unbacked_cover,
                conservation=_RD.conservation_lines(
                    list(args.files) + list(args.crypto)),
                in_kind_unbooked=args.in_kind_unbooked,
                unconsumed_premium=_RD.unconsumed_premium_lines(
                    list(args.files) + list(args.crypto)),
                unsettled_compensation=_RD.unsettled_compensation_lines(
                    list(args.files) + list(args.crypto)))
        # A NaN figure is refused, never written (issue #8).
        from taxjson.lib.json_contract import versioned
        from taxjson.lib.json_input import dump_filing_json
        dump_filing_json(versioned(rep, "form-export"), sys.stdout)
        d.blank()
    else:
        d.line(text)


if __name__ == "__main__":
    sys.exit(main())
