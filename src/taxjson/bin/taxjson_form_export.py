#!/usr/bin/env python3
"""
taxjson_form_export.py

Render computed gains into filing-shaped artifacts:

  --form 8949       IRS Form 8949 rows (Part I short-term / Part II
                    long-term) with wash-sale code W adjustments, plus
                    Schedule D part totals. Needs a country=usa gains file
                    (entries carry ST/LT terms).

  --form txf        TurboTax-importable TXF (V042) built from the 8949
                    rows — one TD record per disposition with the
                    code-W wash amount as the trailing dollar field.
                    --box picks the 8949 checkbox pairing (A/D default);
                    --out writes the .txf file.

  --form schedule3  CRA Schedule 3 (section 3, publicly traded shares)
                    per-property rows: units, acquisition year, proceeds of
                    disposition, ACB, outlays, gain(loss) — with
                    superficial-loss denial notes — plus the line
                    13199/13200 totals.

Input is the pipeline's year-scoped `<account>_gains.json` (prefer the
wash-adjusted `<account>_gains_wash.json` — those are the allowed numbers a
return reports). Column conventions:

  8949:  (d) proceeds and (e) cost come straight from each disposition
         chunk; a wash sale gets code W in (f) and the disallowed amount as
         a positive adjustment in (g), so (h) = (d) - (e) + (g) equals the
         engine's allowed gain. Date acquired is derived as sale date minus
         days_held ("VARIOUS" never appears — chunks are per-lot already).
         Short sales report the cover date in both date columns, matching
         common 1099-B practice.

  Schedule 3: broker proceeds in taxjson are net of sell-side
         commission/fee, so the report re-splits them: proceeds column =
         net + outlays, outlays column = commission + fee, leaving the gain
         identical. Short-position rows show absolute amounts with a SHORT
         marker (gain is exact; the column split is presentational).

Tainted dispositions (phantom cost basis) are SKIPPED with a warning — they
are routed to `manual_reporting_required` by the pipeline and must be
resolved, not filed.

Usage:
    taxjson-form-export --form 8949 --year 2025 margin_gains.json [...]
    taxjson-form-export --form schedule3 --year 2025 --csv out.csv ...

Or through the project wrapper: `taxjson form-export` (form defaults from
the project's country).
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from taxjson.lib.report_model import load_report_json
from typing import Any, Dict, List, Optional, Tuple

_INCOME_ACTIONS = ("DIVIDEND", "DIVIDEND_IN_LIEU")
_EPS = 0.005


# Thin alias so existing importers (tests, taxjson_reconcile_slips)
# keep working; the implementation is the shared report-layer loader.
def load_json(path: Path) -> Any:
    return load_report_json(path)


def load_dispositions(paths: List[Path], year: Optional[int],
                      date_key: str) -> Tuple[List[Dict[str, Any]], int]:
    """Disposition entries (sells) from gains files; income rows and
    tainted rows are excluded. Returns (entries, tainted_skipped)."""
    entries: List[Dict[str, Any]] = []
    tainted_skipped = 0
    ystr = str(year) if year else None
    for p in paths:
        data = load_json(p)
        for e in data.get("transactions", []):
            if e.get("action") in _INCOME_ACTIONS:
                continue
            if "gain" not in e or "qty" not in e:
                continue
            date = e.get(date_key) or e.get("date") or ""
            if ystr and not date.startswith(ystr):
                continue
            if e.get("tainted"):
                tainted_skipped += 1
                continue
            entries.append(e)
    return entries, tainted_skipped


def _acquired_date(e: Dict[str, Any]) -> str:
    """The lot's ACTUAL purchase date when the engine emitted one
    (matches the broker's 1099-B date-acquired; the derived fallback
    below backdates wash-replacement lots by the §1223(3) tacking and
    won't reconcile). Fallback: sale date minus days_held; sale date
    when days_held is missing/zero (same-day round trip)."""
    if e.get("acquired_date"):
        return str(e["acquired_date"])
    date = e.get("date") or ""
    days = int(e.get("days_held") or 0)
    if not date or days <= 0:
        return date
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return date
    return (dt - timedelta(days=days)).strftime("%Y-%m-%d")


from taxjson.lib.report_model import fmt_qty as _qty_str  # noqa: E402


# ---------------------------------------------------------------- 8949

def build_8949(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    parts: Dict[str, List[Dict[str, Any]]] = {"I": [], "II": []}
    drift_warned = 0
    for e in entries:
        term = e.get("term")
        if term not in ("SHORT_TERM", "LONG_TERM"):
            raise SystemExit(
                "taxjson-form-export: entry for "
                f"{e.get('symbol')!r} on {e.get('date')!r} has no ST/LT "
                "term — Form 8949 needs a country=usa gains file "
                "(Canada has no term concept; use --form schedule3).")
        part = "I" if term == "SHORT_TERM" else "II"
        proceeds = float(e.get("proceeds") or 0.0)
        cost = float(e.get("cost") or 0.0)
        direction = e.get("direction") or "LONG"
        if direction == "SHORT":
            # Engine signed short convention (FUZZ #E): `cost` holds the
            # NEGATED opening short-sale proceeds and `proceeds` the
            # NEGATED buy-to-cover cost, so gain = proceeds - cost is
            # direction-free inside the engine. The FILED row wants the
            # real-world figures — (d) what the short sale brought in,
            # (e) what covering cost — so the fields un-negate AND swap.
            # (h) = (d) - (e) + (g) is preserved: the swap+negation
            # leaves the difference unchanged (which is also why the
            # drift check below never caught the raw rendering).
            proceeds, cost = -cost + 0.0, -proceeds + 0.0   # +0.0 kills -0.00 rendering
        adj = float(e.get("disallowed_amount") or 0.0)
        gain = float(e.get("gain") or 0.0)
        code = "W" if adj > _EPS else ""
        if adj < -_EPS:
            # A negative disallowance is not a shape this renderer can
            # file (code W adjustments are positive); rendering would
            # silently disagree with the engine's allowed gain.
            print(f"warning: {e.get('symbol')} {e.get('date')}: "
                  f"NEGATIVE disallowed_amount {adj:.2f} — row "
                  f"rendered without an adjustment; inspect before "
                  f"filing.", file=sys.stderr)
        # (h) must equal (d) - (e) + (g) with the RENDERED adjustment;
        # the engine's allowed gain is the authority — surface drift
        # instead of silently printing either.
        rendered_adj = adj if code else 0.0
        if abs((proceeds - cost + rendered_adj) - gain) > 0.02:
            drift_warned += 1
        sold = e.get("date") or ""
        acquired = _acquired_date(e) if direction != "SHORT" else sold
        if direction == "SHORT":
            sold = e.get("date") or ""
        desc = f"{_qty_str(abs(float(e.get('qty') or 0)))} {e.get('symbol')}"
        if e.get("is_option"):
            desc += " (option)"
        if direction == "SHORT":
            desc += " (short sale)"
        parts[part].append({
            "description": desc,
            "date_acquired": acquired,
            "date_sold": sold,
            "proceeds": round(proceeds, 2),
            "cost": round(cost, 2),
            "code": code,
            "adjustment": round(adj, 2) if code else 0.0,
            "gain": round(gain, 2),
            "account": e.get("account") or "",
        })
    if drift_warned:
        print(f"warning: {drift_warned} row(s) where (d)-(e)+(g) differs "
              f"from the engine's allowed gain by more than $0.02 — "
              f"inspect before filing.", file=sys.stderr)
    for rows in parts.values():
        rows.sort(key=lambda r: (r["date_sold"], r["description"]))

    def totals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        return {
            "proceeds": round(sum(r["proceeds"] for r in rows), 2),
            "cost": round(sum(r["cost"] for r in rows), 2),
            "adjustment": round(sum(r["adjustment"] for r in rows), 2),
            "gain": round(sum(r["gain"] for r in rows), 2),
        }
    return {
        "form": "8949",
        "part_I": parts["I"], "part_I_totals": totals(parts["I"]),
        "part_II": parts["II"], "part_II_totals": totals(parts["II"]),
    }


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
            _desc = " ".join(str(row['description']).split())
            _desc = _desc.replace("^", " ").encode(
                "ascii", "replace").decode("ascii")
            lines += ["TD", f"N{refnum}", "C1", "L1",
                      f"P{_desc}",
                      f"D{_txf_date(row['date_acquired'])}",
                      f"D{_txf_date(row['date_sold'])}",
                      f"${row['cost']:.2f}",
                      f"${row['proceeds']:.2f}"]
            if row.get("code") == "W" and row.get("adjustment"):
                lines.append(f"${row['adjustment']:.2f}")
            lines.append("^")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- schedule 3

def build_schedule3(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        symbol = e.get("symbol") or "?"
        rec = by_symbol.setdefault(symbol, {
            "symbol": symbol, "units": 0.0, "acq_year": None,
            "proceeds": 0.0, "acb": 0.0, "outlays": 0.0, "gain": 0.0,
            "denied": 0.0, "short": False,
        })
        qty = abs(float(e.get("qty") or 0.0))
        proceeds = float(e.get("proceeds") or 0.0)
        cost = float(e.get("cost") or 0.0)
        gain = float(e.get("gain") or 0.0)
        outlays = float(e.get("commission") or 0.0) + float(e.get("fee") or 0.0)
        direction = e.get("direction") or "LONG"
        if direction == "SHORT":
            # Engine signed short convention (FUZZ #E): `cost` is the
            # NEGATED opening short-sale proceeds and `proceeds` the
            # NEGATED buy-to-cover cost. Filing wants the real-world
            # mapping — disposition PROCEEDS = what the short sale
            # brought in, ACB = what covering cost — so the fields swap
            # as they un-negate. (abs() alone fixed the sign but kept
            # the swap: line 13199 was wrong and the row read
            # internally contradictory, proceeds − ACB ≠ gain.)
            rec["short"] = True
            rec["proceeds"] += abs(cost)
            rec["acb"] += abs(proceeds)
        else:
            # taxjson proceeds are net of sell-side costs; Schedule 3 wants
            # them split back out. gain = (net + outlays) - acb - outlays
            # stays identical.
            rec["proceeds"] += proceeds + outlays
            rec["acb"] += cost
            rec["outlays"] += outlays
        rec["units"] += qty
        rec["gain"] += gain
        rec["denied"] += float(e.get("disallowed_amount") or 0.0)
        rec["perm_denied"] = (rec.get("perm_denied", 0.0)
                              + float(e.get("permanently_disallowed")
                                      or 0.0))
        acq = _acquired_date(e)
        if acq:
            y = acq[:4]
            if rec["acq_year"] is None or y < rec["acq_year"]:
                rec["acq_year"] = y

    rows = []
    for symbol in sorted(by_symbol):
        r = by_symbol[symbol]
        notes = []
        _perm = float(r.get("perm_denied") or 0.0)
        _defer = r["denied"] - _perm
        if _defer > _EPS:
            notes.append(f"superficial loss {_defer:,.2f} denied "
                         f"(added to repurchased ACB)")
        if _perm > _EPS:
            # A registered-account acquisition denies for GOOD — there
            # is no ACB anywhere to bump; the old single note told
            # users to add it to a future rebuy's ACB (2026-09 audit).
            notes.append(f"superficial loss {_perm:,.2f} PERMANENTLY "
                         f"denied (registered-account acquisition — "
                         f"no ACB addition)")
        if r["short"]:
            notes.append("includes short position(s) — |amounts| shown")
        rows.append({
            "symbol": symbol,
            "units": round(r["units"], 4),
            "acq_year": r["acq_year"] or "",
            "proceeds": round(r["proceeds"], 2),
            "acb": round(r["acb"], 2),
            "outlays": round(r["outlays"], 2),
            "gain": round(r["gain"], 2),
            "notes": "; ".join(notes),
        })
    return {
        "form": "schedule3",
        "rows": rows,
        "totals": {
            # Line 13199: total proceeds; line 13200: total gain(loss).
            "proceeds_13199": round(sum(r["proceeds"] for r in rows), 2),
            "gain_13200": round(sum(r["gain"] for r in rows), 2),
        },
    }



def filing_totals(entries: List[Dict[str, Any]]) -> Dict[str, float]:
    """The three amounts a return's capital-gains entry asks for, summed
    over `entries` on the Schedule 3 convention (short sales as |amounts|,
    sell-side commissions split out as outlays), with the ACB chosen so
    that PROCEEDS − ACB − OUTLAYS equals the ALLOWED gain: a superficial
    loss the engine denied is folded into the ACB, exactly as it is in the
    per-account .sum report. `denied` reports how much that is."""
    rep = build_schedule3(entries)
    proceeds = round(sum(r["proceeds"] for r in rep["rows"]), 2)
    outlays = round(sum(r["outlays"] for r in rep["rows"]), 2)
    gain = round(sum(r["gain"] for r in rep["rows"]), 2)
    denied = round(sum(float(e.get("disallowed_amount") or 0.0)
                       for e in entries), 2)
    return {"proceeds": proceeds,
            "acb": round(proceeds - outlays - gain, 2),
            "outlays": outlays, "gain": gain, "denied": denied,
            "dispositions": len(entries)}

# ---------------------------------------------------------------- render

def _table(header: Tuple[str, ...], rows: List[Tuple[str, ...]],
           right: set) -> List[str]:
    if not rows:
        return ["  (no dispositions)"]
    widths = [max(len(header[i]), *(len(r[i]) for r in rows))
              for i in range(len(header))]
    def fmt(row: Tuple[str, ...]) -> str:
        return " | ".join(
            (row[i].rjust(widths[i]) if i in right else row[i].ljust(widths[i]))
            for i in range(len(row))).rstrip()
    return [fmt(header), "-+-".join("-" * w for w in widths)] + \
           [fmt(r) for r in rows]


def render_8949(rep: Dict[str, Any], year: Optional[int], cur: str) -> str:
    lines = [f"FORM 8949 — Sales and Other Dispositions of Capital Assets "
             f"(tax year {year or '?'}, amounts in {cur})",
             ""]
    for part, label in (("I", "PART I — SHORT-TERM"),
                        ("II", "PART II — LONG-TERM")):
        rows = rep[f"part_{part}"]
        t = rep[f"part_{part}_totals"]
        lines.append(label)
        header = ("(a) DESCRIPTION", "(b) ACQUIRED", "(c) SOLD",
                  "(d) PROCEEDS", "(e) COST", "(f)", "(g) ADJ",
                  "(h) GAIN(LOSS)")
        table = [(r["description"], r["date_acquired"], r["date_sold"],
                  f"{r['proceeds']:,.2f}", f"{r['cost']:,.2f}", r["code"],
                  f"{r['adjustment']:,.2f}" if r["code"] else "",
                  f"{r['gain']:,.2f}") for r in rows]
        lines += _table(header, table, right={3, 4, 6, 7})
        if rows:
            lines.append(f"  TOTALS (to Schedule D part {part}): proceeds "
                         f"{t['proceeds']:,.2f} | cost {t['cost']:,.2f} | "
                         f"adjustments {t['adjustment']:,.2f} | gain "
                         f"{t['gain']:,.2f}")
        lines.append("")
    lines.append("Notes:")
    lines.append("  - Code W rows are wash sales; column (g) is the "
                 "disallowed loss added back, so (h) is the allowed amount.")
    lines.append("  - Check the correct 8949 box (A/B/C, D/E/F) against "
                 "whether your broker reported basis on the 1099-B.")
    lines.append("  - Short sales show the cover date in both date columns.")
    lines.append("  - Not tax advice; reconcile against your 1099-B before "
                 "filing.")
    return "\n".join(lines)


def render_schedule3(rep: Dict[str, Any], year: Optional[int],
                     cur: str) -> str:
    lines = [f"SCHEDULE 3 — Capital Gains (section 3: publicly traded "
             f"shares) (tax year {year or '?'}, amounts in {cur})",
             ""]
    header = ("UNITS", "SYMBOL", "ACQ. YEAR", "PROCEEDS", "ACB",
              "OUTLAYS", "GAIN(LOSS)", "NOTES")
    table = [(f"{r['units']:,.4f}".rstrip("0").rstrip("."), r["symbol"],
              str(r["acq_year"]), f"{r['proceeds']:,.2f}",
              f"{r['acb']:,.2f}", f"{r['outlays']:,.2f}",
              f"{r['gain']:,.2f}", r["notes"]) for r in rep["rows"]]
    lines += _table(header, table, right={0, 3, 4, 5, 6})
    t = rep["totals"]
    lines.append("")
    lines.append(f"  Line 13199 (total proceeds): {t['proceeds_13199']:,.2f}")
    lines.append(f"  Line 13200 (total gain/loss): {t['gain_13200']:,.2f}")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - GAIN(LOSS) is the ALLOWED amount — superficial losses "
                 "are already denied and noted per row.")
    lines.append("  - Apply the inclusion rate on Schedule 3 itself; these "
                 "are 100% amounts.")
    lines.append("  - PROCEEDS re-adds sell-side commissions so OUTLAYS can "
                 "be shown separately; the gain is unchanged.")
    lines.append("  - Not tax advice; reconcile against your T5008 slips "
                 "before filing (see taxjson-reconcile-slips).")
    return "\n".join(lines)


def write_csv(rep: Dict[str, Any], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if rep["form"] == "8949":
            w.writerow(["part", "description", "date_acquired", "date_sold",
                        "proceeds", "cost", "code", "adjustment",
                        "gain", "account"])
            for part in ("I", "II"):
                for r in rep[f"part_{part}"]:
                    w.writerow([part, r["description"], r["date_acquired"],
                                r["date_sold"], r["proceeds"], r["cost"],
                                r["code"], r["adjustment"], r["gain"],
                                r["account"]])
        else:
            w.writerow(["units", "symbol", "acq_year", "proceeds", "acb",
                        "outlays", "gain", "notes"])
            for r in rep["rows"]:
                w.writerow([r["units"], r["symbol"], r["acq_year"],
                            r["proceeds"], r["acb"], r["outlays"],
                            r["gain"], r["notes"]])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render taxjson gains into IRS Form 8949 or CRA "
                    "Schedule 3 shaped output.")
    parser.add_argument("files", nargs="+", type=Path, metavar="FILE",
                        help="Year-scoped <account>_gains.json files "
                             "(prefer the wash-adjusted variants)")
    parser.add_argument("--form", required=True,
                        choices=["8949", "schedule3", "txf"])
    parser.add_argument("--box", default="A", choices=["A", "B", "C"],
                        help="TXF only: 8949 checkbox pairing — A/D "
                             "(basis on the 1099-B, the default for "
                             "covered securities), B/E (1099-B without "
                             "basis), C/F (no 1099-B)")
    parser.add_argument("--out", type=Path, default=None,
                        help="TXF only: write the .txf here instead of "
                             "stdout")
    parser.add_argument("--year", type=int, default=None,
                        help="Defensive year filter (pipeline gains files "
                             "are already year-scoped)")
    parser.add_argument("--base-currency", default="",
                        help="Currency label for the header")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Also write the rows as CSV to this path")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    args = parser.parse_args(argv)

    for p in args.files:
        if not p.exists():
            print(f"taxjson-form-export: no such file: {p}", file=sys.stderr)
            return 2

    # IRS attributes the year by TRADE date; CRA by SETTLEMENT date.
    date_key = "date" if args.form in ("8949", "txf") else "date_settle"
    entries, tainted = load_dispositions(args.files, args.year,
                                         date_key)
    if tainted:
        print(f"warning: skipped {tainted} tainted disposition(s) with "
              f"phantom cost basis — resolve via find-missing-history "
              f"(they are listed under manual_reporting_required), do not "
              f"file them from this report.", file=sys.stderr)

    if args.form == "txf":
        if args.csv or args.json:
            print("warning: --csv/--json have no effect with "
                  "--form txf (TXF is its own format) — ignored.",
                  file=sys.stderr)
        # TXF rides on the 8949 model — same rows, same code-W math.
        rep = build_8949(entries)
        doc = build_txf(rep, args.box)
        if args.out:
            tmp = args.out.with_name(args.out.name + ".part")
            try:
                tmp.write_text(doc, encoding="ascii")
                tmp.replace(args.out)
            except (OSError, UnicodeEncodeError) as e:
                tmp.unlink(missing_ok=True)
                sys.exit(f"taxjson-form-export: cannot write --out "
                         f"{args.out}: {e}")
            n = len(rep["part_I"]) + len(rep["part_II"])
            print(f"wrote {n} TXF record(s) (box {args.box}) to "
                  f"{args.out}", file=sys.stderr)
        else:
            sys.stdout.write(doc)
        return 0

    if args.form == "8949":
        rep = build_8949(entries)
        rep["currency"] = args.base_currency or "USD"
        text = render_8949(rep, args.year, rep["currency"])
    else:
        rep = build_schedule3(entries)
        rep["currency"] = args.base_currency or "CAD"
        text = render_schedule3(rep, args.year, rep["currency"])

    if args.csv:
        try:
            write_csv(rep, args.csv)
        except OSError as e:
            # --csv pointed at a directory (or unwritable path) used
            # to be a raw IsADirectoryError traceback that also lost
            # the report text (REVIEW #39). Print the report, THEN
            # fail with a clean message and nonzero exit.
            if not args.json:
                print(text)
            sys.exit(f"taxjson-form-export: cannot write --csv "
                     f"{args.csv}: {e}")
        print(f"wrote {args.csv}", file=sys.stderr)
    if args.json:
        json.dump(rep, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
