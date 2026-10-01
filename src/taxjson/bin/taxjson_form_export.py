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

  --form schedule3  CRA Schedule 3 per-property rows, routed by property
                    type to the Part 3 line they belong on (2025 form):
                    line 4 publicly traded shares / fund units
                    (13199/13200), line 6 options, futures and other
                    properties (15199/15300, per T4037), line 7
                    crypto-assets (15200/15301; before 2025 crypto went
                    on 15199/15300). Per row: units, acquisition year,
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
         common 1099-B practice.

  Schedule 3: broker proceeds in taxjson are net of sell-side
         commission/fee, so the report re-splits them: proceeds column =
         net + outlays, outlays column = commission + fee, leaving the gain
         identical. Short-position rows show absolute amounts with a SHORT
         marker (gain is exact; the column split is presentational).
         Every row foots — proceeds − ACB − outlays = the allowed gain —
         so a denied superficial loss shows as an ACB REDUCED by the
         denial (the denied amount goes onto the replacement's ACB).
         Crypto is known by account: pass crypto books with --crypto.

Tainted dispositions (phantom cost basis) have no computable gain: the
pipeline routes them to `manual_reporting_required`. They are left out of the
rows and totals above (which are the allowed, computed numbers), but NEVER
silently: every one is listed in a MANUAL REPORTING section of the report
(rows marked MANUAL in the CSV, `manual_reporting_required` in the JSON) and
named in a stderr warning with its proceeds — they must be reported by hand
once their cost is known.

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
from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.core import is_option_symbol
from taxjson.lib.futures import is_plain_future
from typing import Any, Dict, List, Optional, Tuple

_INCOME_ACTIONS = ("DIVIDEND", "DIVIDEND_IN_LIEU")
_EPS = 0.005


# Thin alias so existing importers (tests, taxjson_reconcile_slips)
# keep working; the implementation is the shared report-layer loader.
def load_json(path: Path) -> Any:
    # The shared input contract (lib/json_input): the message names the
    # file (R1-277), a bare array is a transaction list and any other
    # non-object is refused instead of an AttributeError traceback
    # (audit S079-11). InputFileError is a ValueError.
    from taxjson.lib.json_input import read_json_doc, require_gains_doc
    # A document with no 'transactions' list, or a pre-gains stage file
    # (work/<acct>_base.json), rendered a $0 Schedule 3 at exit 0
    # (S033-01).
    return require_gains_doc(read_json_doc(path), path)


def load_manual_rows(paths: List[Path], year: Optional[int],
                     date_key: str) -> List[Dict[str, Any]]:
    """In-year phantom-basis dispositions: the pipeline's
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
        for e in data.get("manual_reporting_required") or []:
            date = e.get(date_key) or e.get("date") or ""
            if ystr and not str(date).startswith(ystr):
                continue
            tainted_skipped += 1
    return entries, tainted_skipped


def manual_section(rows: List[Dict[str, Any]], cur: str) -> List[str]:
    """The MANUAL REPORTING block every text report ends with when
    phantom-basis dispositions exist in the year."""
    if not rows:
        return []
    total = sum(abs(float(r.get("proceeds") or 0.0)) for r in rows)
    lines = [f"MANUAL REPORTING REQUIRED — {len(rows)} disposition(s) "
             f"with unknown cost (phantoms.json), proceeds "
             f"{total:,.2f} {cur}: NOT in the rows or totals above. "
             f"Report each by hand once its cost is known "
             f"(`taxjson find-missing-history`)."]
    table = [(str(r.get("symbol") or ""), str(r.get("date") or ""),
              _qty_str(abs(float(r.get("qty") or 0.0))),
              f"{abs(float(r.get('proceeds') or 0.0)):,.2f}",
              str(r.get("account") or ""))
             for r in rows]
    lines += _table(("SYMBOL", "DATE", "UNITS", "PROCEEDS", "ACCOUNT"),
                    table, right={2, 3})
    lines.append("")
    return lines


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
        if e.get("deemed"):
            # §301(c)(3): no shares were sold (US-ROC-02).
            desc = (f"{e.get('symbol')} nondividend distribution in excess "
                    f"of basis (§301(c)(3))")
        if e.get("is_option"):
            desc += " (option)"
        if direction == "SHORT":
            desc += " (short sale)"
        # Every row FOOTS: (h) = (d) - (e) + (g) on the rounded cells,
        # (d) and (e) as the 1099-B reports them. Rounding the four
        # independently left rows (and the part totals, and the TXF
        # that carries only d/e/g) off by cents (S032-16); the engine
        # drift check above still compares the unrounded gain.
        _d, _e = round(proceeds, 2) + 0.0, round(cost, 2) + 0.0
        _g = round(adj, 2) if code else 0.0
        parts[part].append({
            "description": desc,
            "date_acquired": acquired,
            "date_sold": sold,
            "proceeds": _d,
            "cost": _e,
            "code": code,
            "adjustment": _g,
            "gain": round(_d - _e + _g, 2) + 0.0,
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

# Schedule 3 routes each disposition by PROPERTY TYPE (2025 form, Part 3;
# T4037 "Capital Gains", Chapter 2 "Completing Schedule 3", lines 4/6/7):
#   line 4  publicly traded shares, mutual fund units, ...   13199 / 13200
#   line 6  bonds, debentures, promissory notes and other     15199 / 15300
#           similar properties — T4037: "Other properties
#           include bad debts, foreign currencies and options"
#   line 7  crypto-assets (new for 2025)                      15200 / 15301
# For 2024 and earlier returns crypto-assets were reported with the
# other properties on 15199 / 15300 (there was no separate line).
_FUTURES_PREFIXES = ("F:", "/", "\\")
_LINE_ORDER = ("shares", "other", "crypto")


def schedule3_line(key: str, year: Optional[int]) -> Dict[str, str]:
    """The Schedule 3 line a property class lands on for `year`.
    `key` is shares | other | crypto; crypto folds into `other` before
    2025 (the separate crypto-assets line starts with the 2025 form)."""
    new_form = year is None or int(year) >= 2025
    if key == "crypto" and not new_form:
        key = "other"
    if key == "shares":
        return {"key": "shares", "line": "4" if new_form else "",
                "label": "Publicly traded shares, mutual fund units, "
                         "deferral of eligible small business "
                         "corporation shares, and other shares",
                "short": "shares & fund units",
                "proceeds_code": "13199", "gain_code": "13200"}
    if key == "crypto":
        return {"key": "crypto", "line": "7",
                "label": "Crypto-assets", "short": "crypto-assets",
                "proceeds_code": "15200", "gain_code": "15301"}
    return {"key": "other", "line": "6" if new_form else "",
            "label": ("Bonds, debentures, promissory notes, and other "
                      "similar properties (options, futures, foreign "
                      "currency)" if new_form else
                      "Bonds, debentures, promissory notes, crypto-"
                      "assets, and other similar properties (options, "
                      "futures, foreign currency)"),
            "short": ("options & other properties" if new_form else
                      "options, crypto & other properties"),
            "proceeds_code": "15199", "gain_code": "15300"}


def line_title(spec: Dict[str, str]) -> str:
    """'Part 3, line 4 (13199/13200)' — or, for pre-2025 forms whose
    line numbering this tool does not pin, just the line codes."""
    codes = f"lines {spec['proceeds_code']}/{spec['gain_code']}"
    return (f"Part 3, line {spec['line']} ({codes})" if spec["line"]
            else codes)


def property_class(e: Dict[str, Any]) -> str:
    """shares | option | futures | crypto for one disposition. Crypto
    comes from the account (`crypto = true` books, flagged `_crypto` by
    the loader); futures carry the F:/ prefix the parsers give them
    (options on futures too); options are OCC-style symbols."""
    if e.get("_crypto"):
        return "crypto"
    sym = str(e.get("symbol") or "")
    if sym.startswith(_FUTURES_PREFIXES):
        return "futures"
    if e.get("is_option") or is_option_symbol(sym):
        return "option"
    return "shares"


def _line_key(pclass: str) -> str:
    return {"shares": "shares", "crypto": "crypto"}.get(pclass, "other")


def mark_crypto(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of `entries` flagged as crypto-asset dispositions (the
    gains rows themselves carry no asset class; the account does)."""
    return [{**e, "_crypto": True} for e in entries]


def _infer_year(entries: List[Dict[str, Any]]) -> Optional[int]:
    ys = [str(e.get("date_settle") or e.get("date") or "")[:4]
          for e in entries]
    ys = [int(y) for y in ys if y.isdigit()]
    return max(ys) if ys else None


def build_schedule3(entries: List[Dict[str, Any]],
                    year: Optional[int] = None) -> Dict[str, Any]:
    """Per-property rows grouped by Schedule 3 line. Every row FOOTS:
    PROCEEDS − ACB − OUTLAYS = GAIN (the allowed gain), so a denied
    superficial loss shows as an ACB reduced by the denial — the denied
    amount is what gets added to the replacement property's ACB."""
    if year is None:
        year = _infer_year(entries)
    recs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in entries:
        symbol = e.get("symbol") or "?"
        pclass = property_class(e)
        lkey = schedule3_line(_line_key(pclass), year)["key"]
        rec = recs.setdefault((lkey, symbol), {
            "symbol": symbol, "units": 0.0, "acq_year": None,
            "proceeds": 0.0, "outlays": 0.0, "gain": 0.0,
            "denied": 0.0, "perm_denied": 0.0, "short": False,
            "classes": set(), "n": 0, "grant_units": 0.0,
            "short_close_units": 0.0,
        })
        rec["classes"].add(pclass)
        rec["n"] += 1
        qty = abs(float(e.get("qty") or 0.0))
        proceeds = float(e.get("proceeds") or 0.0)
        cost = float(e.get("cost") or 0.0)
        gain = float(e.get("gain") or 0.0)
        outlays = float(e.get("commission") or 0.0) + float(e.get("fee") or 0.0)
        direction = e.get("direction") or "LONG"
        if pclass == "futures" and is_plain_future(symbol):
            # A futures contract is booked on its settled P/L
            # (lib/futures.py): nothing is paid to open one, so the
            # notional is neither proceeds nor ACB. Each closed contract
            # shows the T5008 shape — a gain as PROCEEDS, a loss as ACB
            # (the P/L before any superficial-loss denial; the footing
            # ACB below absorbs a denial the usual way). Commissions are
            # inside the P/L, so no separate outlays.
            pl = (float(e["raw_gain"]) if e.get("raw_gain") is not None
                  else gain)
            rec["proceeds"] += max(pl, 0.0)
        elif direction == "SHORT":
            # Engine signed short convention (FUZZ #E): `cost` is the
            # NEGATED opening short-sale proceeds and `proceeds` the
            # NEGATED buy-to-cover cost. Filing wants the real-world
            # mapping — disposition PROCEEDS = what the short sale
            # brought in, ACB = what covering cost — so the fields swap
            # as they un-negate (the ACB itself is derived below).
            rec["short"] = True
            _sold = -cost           # what the short sale brought in (net)
            if _sold >= 0:
                rec["proceeds"] += _sold
            else:
                # A write for a net DEBIT (commission above the premium):
                # abs() turned the debit into proceeds and invented an ACB
                # of twice it (S032-19). Nothing was received: the debit
                # is an outlay, the ACB (the cover) is what it cost.
                rec["outlays"] += -_sold
        else:
            # taxjson proceeds are net of sell-side costs; Schedule 3 wants
            # them split back out. gain = (net + outlays) - acb - outlays
            # stays identical.
            rec["proceeds"] += proceeds + outlays
            rec["outlays"] += outlays
        # Grant timing books a written option twice — the WRITE and its
        # buy-back — while the contracts were disposed of once: count
        # them once (S003-04, S033-02; as reconcile-slips, R1-18). A
        # write still open (or closed next year) counts at the write.
        if e.get("grant"):
            rec["grant_units"] += qty
        else:
            if direction == "SHORT":
                rec["short_close_units"] += qty
            rec["units"] += qty
        rec["gain"] += gain
        rec["denied"] += float(e.get("disallowed_amount") or 0.0)
        rec["perm_denied"] += float(e.get("permanently_disallowed") or 0.0)
        acq = _acquired_date(e)
        if acq:
            y = acq[:4]
            if rec["acq_year"] is None or y < rec["acq_year"]:
                rec["acq_year"] = y

    rows: List[Dict[str, Any]] = []
    for (lkey, symbol) in sorted(recs, key=lambda k: (
            _LINE_ORDER.index(k[0]), k[1])):
        r = recs[(lkey, symbol)]
        r["units"] += max(0.0, r["grant_units"] - r["short_close_units"])
        spec = schedule3_line(lkey, year)
        notes = []
        _perm = r["perm_denied"]
        _defer = r["denied"] - _perm
        if _defer > _EPS:
            notes.append(f"superficial loss {_defer:,.2f} denied: ACB "
                         f"shown reduced by it; add it to the ACB of "
                         f"the replacement property")
        if _perm > _EPS:
            # A registered-account acquisition denies for GOOD — there
            # is no ACB anywhere to bump; the old single note told
            # users to add it to a future rebuy's ACB (2026-09 audit).
            # An affiliated person's purchase is permanent for THIS
            # return too, but that person adds it to their own ACB
            # (s.53(1)(f)) — the note no longer says nobody does
            # (S033-03).
            notes.append(f"superficial loss {_perm:,.2f} PERMANENTLY "
                         f"denied (replacement in a registered account "
                         f"or bought by an affiliated person — no ACB "
                         f"addition on this return; an affiliated "
                         f"person adds it to their own ACB)")
        if "futures" in r["classes"]:
            notes.append("futures / option on futures — reported with "
                         "the other properties (options, T4037)"
                         + ("; futures: settled P/L (gain as proceeds, "
                            "loss as ACB), not the notional"
                            if is_plain_future(symbol) else ""))
        if r["short"]:
            notes.append("includes short position(s) — PROCEEDS is the "
                         "short sale or write, ACB the cover")
        # `+ 0.0` turns a rounded -0.0 into 0.0 (no "-0.00" cells).
        proceeds = round(r["proceeds"], 2) + 0.0
        outlays = round(r["outlays"], 2) + 0.0
        gain = round(r["gain"], 2) + 0.0
        pclass = sorted(r["classes"])[0] if len(r["classes"]) == 1 \
            else "mixed"
        rows.append({
            "symbol": symbol,
            "line": spec["line"], "line_key": spec["key"],
            "proceeds_line": spec["proceeds_code"],
            "gain_line": spec["gain_code"],
            "property": pclass,
            # Full precision (a crypto unit count is not money): 4 dp
            # showed a 0.00003 BTC sale as 0 units (S032-21).
            "units": round(r["units"], 8),
            "acq_year": r["acq_year"] or "",
            "proceeds": proceeds,
            # The footing ACB: proceeds − outlays − allowed gain. For a
            # plain sale this IS the ACB; a superficial-loss row shows
            # it reduced by the denied amount.
            "acb": round(proceeds - outlays - gain, 2) + 0.0,
            "outlays": outlays,
            "gain": gain,
            "denied": round(r["denied"], 2),
            "dispositions": r["n"],
            "notes": "; ".join(notes),
        })

    lines: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    for lkey in _LINE_ORDER:
        lrows = [r for r in rows if r["line_key"] == lkey]
        if not lrows:
            continue
        spec = schedule3_line(lkey, year)
        agg = {k: round(sum(r[k] for r in lrows), 2) + 0.0
               for k in ("proceeds", "acb", "outlays", "gain", "denied")}
        lines.append({**spec, "title": line_title(spec), **agg,
                      "dispositions": sum(r["dispositions"] for r in lrows),
                      "rows": len(lrows)})
        totals[f"proceeds_{spec['proceeds_code']}"] = agg["proceeds"]
        totals[f"gain_{spec['gain_code']}"] = agg["gain"]
    # Line 4's pair is always present (0.00 when nothing was sold), so a
    # consumer keyed on 13199/13200 never KeyErrors.
    totals.setdefault("proceeds_13199", 0.0)
    totals.setdefault("gain_13200", 0.0)
    totals["proceeds_all"] = round(sum(r["proceeds"] for r in rows), 2)
    totals["gain_all"] = round(sum(r["gain"] for r in rows), 2)
    # The same rows' gain summed BEFORE the per-row rounding: what the
    # engine (and the .sum) totals. The rows must add up to the line, so
    # the form rounds per row; a checker comparing with the engine uses
    # this instead of a tolerance that has to grow with the row count
    # (R1-210: 100 rows of 100.004 export 10,000.00 against 10,000.40).
    return {"form": "schedule3", "year": year, "rows": rows,
            "lines": lines, "totals": totals,
            "gain_unrounded": sum(r["gain"] for r in recs.values())}


def filing_lines(entries: List[Dict[str, Any]],
                 year: Optional[int] = None) -> List[Dict[str, Any]]:
    """One dict per Schedule 3 line that has dispositions: the line
    number / codes / label plus PROCEEDS, ACB, OUTLAYS, GAIN, DENIED and
    the disposition count — the rows of `taxjson sum`'s FOR THE RETURN
    block, identical to form-export's line totals by construction."""
    return build_schedule3(entries, year)["lines"]


def filing_totals(entries: List[Dict[str, Any]],
                  year: Optional[int] = None) -> Dict[str, float]:
    """The amounts a return's capital-gains entry asks for, summed over
    `entries` (all Schedule 3 lines) on the Schedule 3 convention (short
    sales as |amounts|, sell-side commissions split out as outlays), with
    the ACB chosen so that PROCEEDS − ACB − OUTLAYS equals the ALLOWED
    gain: a denied superficial loss REDUCES the ACB shown here (the
    denied amount is added to the replacement property's ACB instead).
    `denied` reports how much that is."""
    rep = build_schedule3(entries, year)
    agg = {k: round(sum(r[k] for r in rep["rows"]), 2)
           for k in ("proceeds", "outlays", "gain", "denied")}
    return {"proceeds": agg["proceeds"],
            "acb": round(agg["proceeds"] - agg["outlays"] - agg["gain"], 2),
            "outlays": agg["outlays"], "gain": agg["gain"],
            "denied": agg["denied"],
            "dispositions": len(entries)}


def filing_parts_8949(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Form 8949's own part totals — (d) proceeds, (e) cost, (g)
    adjustment, (h) gain — for `taxjson sum`'s US FOR THE RETURN block,
    so it shows exactly what the 8949 export carries to Schedule D."""
    rep = build_8949(entries)
    out = []
    for part, label, sched_d in (("I", "Part I — short-term", "Schedule D Part I"),
                                 ("II", "Part II — long-term", "Schedule D Part II")):
        rows = rep[f"part_{part}"]
        if not rows:
            continue
        t = rep[f"part_{part}_totals"]
        out.append({"part": part, "label": label, "schedule_d": sched_d,
                    "proceeds": t["proceeds"], "cost": t["cost"],
                    "adjustment": t["adjustment"], "gain": t["gain"],
                    "dispositions": len(rows)})
    return out

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
    lines += manual_section(rep.get("manual_reporting_required") or [],
                            cur)
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
    year = year or rep.get("year")
    lines = [f"SCHEDULE 3 — Capital Gains (or Losses) (tax year "
             f"{year or '?'}, amounts in {cur})",
             ""]
    header = ("UNITS", "SYMBOL", "ACQ. YEAR", "PROCEEDS", "ACB",
              "OUTLAYS", "GAIN(LOSS)", "NOTES")
    by_line = rep.get("lines") or []
    if not by_line:
        spec = schedule3_line("shares", year)
        lines.append(f"{line_title(spec).upper()} — {spec['label']}")
        lines += _table(header, [], right=set())
        lines.append("")
    for ln in by_line:
        lines.append(f"{ln['title'].upper()} — {ln['label']}")
        table = [(f"{r['units']:,.8f}".rstrip("0").rstrip(".") or "0",
                  r["symbol"],
                  str(r["acq_year"]), f"{r['proceeds']:,.2f}",
                  f"{r['acb']:,.2f}", f"{r['outlays']:,.2f}",
                  f"{r['gain']:,.2f}", r["notes"])
                 for r in rep["rows"] if r["line_key"] == ln["key"]]
        lines += _table(header, table, right={0, 3, 4, 5, 6})
        lines.append(f"  Line {ln['proceeds_code']} (proceeds of "
                     f"disposition): {ln['proceeds']:,.2f}")
        lines.append(f"  Line {ln['gain_code']} (gain/loss): "
                     f"{ln['gain']:,.2f}")
        lines.append("")
    lines += manual_section(rep.get("manual_reporting_required") or [],
                            cur)
    lines.append("Notes:")
    lines.append("  - Each disposition is on the line for its property "
                 "type: shares and fund units on 13199/13200; options, "
                 "futures and other properties on 15199/15300 (T4037); "
                 # The same routing the rows use (S032-23): a second,
                 # separate year test could contradict them.
                 + ("crypto-assets on 15200/15301."
                    if schedule3_line("crypto", year)["key"] == "crypto"
                    else "crypto-assets with the other properties "
                         "(15199/15300) for this year."))
    lines.append("  - GAIN(LOSS) is the ALLOWED amount. Every row foots: "
                 "PROCEEDS − ACB − OUTLAYS = GAIN(LOSS); where a "
                 "superficial loss was denied the ACB shown is reduced "
                 "by the denied amount, which is added to the ACB of "
                 "the replacement property instead (noted per row).")
    lines.append("  - Apply the inclusion rate on Schedule 3 itself; these "
                 "are 100% amounts.")
    lines.append("  - PROCEEDS re-adds sell-side commissions so OUTLAYS can "
                 "be shown separately; the gain is unchanged.")
    lines.append("  - FX gains on foreign cash (s.39(1.1), `taxjson "
                 "fx-cash`) are not in these rows; T4037 puts them on "
                 "line 15300.")
    lines.append("  - Capital gains paid out by funds and trusts are not "
                 "in these rows either: T3 box 21 goes on line 17600 and "
                 "T5/T5013 box 18 on line 17400. Enter them from the "
                 "slips; the books carry those distributions as "
                 "dividends, so line 19700 is these rows plus the slip "
                 "lines.")
    lines.append("  - Not tax advice; reconcile against your T5008 slips "
                 "before filing (see taxjson-reconcile-slips).")
    return "\n".join(lines)


def write_csv(rep: Dict[str, Any], path: Path) -> None:
    """Write through `<path>.part` and replace, as the TXF --out does: a
    failed write left a truncated CSV in place of the good one
    (S032-24)."""
    tmp = path.with_name(path.name + ".part")
    try:
        _write_csv(rep, tmp)
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _write_csv(rep: Dict[str, Any], path: Path) -> None:
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
            # Phantom-basis dispositions: cost unknown — flagged rows,
            # blank cost/gain, never mistaken for a computed row.
            for m in rep.get("manual_reporting_required") or []:
                w.writerow(["MANUAL",
                            f"{_qty_str(abs(float(m.get('qty') or 0.0)))}"
                            f" {m.get('symbol') or ''}", "",
                            m.get("date") or "",
                            round(abs(float(m.get("proceeds") or 0.0)), 2),
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
                w.writerow(["MANUAL", "", "", "",
                            abs(float(m.get("qty") or 0.0)),
                            m.get("symbol") or "", "",
                            round(abs(float(m.get("proceeds") or 0.0)), 2),
                            "", "", "", "",
                            "cost unknown (phantoms.json) - report by "
                            "hand"])


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
                        help="Schedule 3: a gains file whose dispositions "
                             "are crypto-assets (repeatable; line 7 — "
                             "15200/15301 — from 2025, 15199/15300 "
                             "before). The `taxjson form-export` wrapper "
                             "passes the crypto = true accounts here.")
    parser.add_argument("--form", required=True,
                        choices=["8949", "schedule3", "txf"])
    from taxjson.lib.country import add_country_argument
    add_country_argument(parser, help="Whose return the rows are for "
                         "(required): canada (--form schedule3) | usa "
                         "(--form 8949 / txf). The `taxjson form-export` "
                         "wrapper passes the project's country.")
    parser.add_argument("--box", default="A", choices=["A", "B", "C"],
                        help="TXF only: 8949 checkbox pairing — A/D "
                             "(basis on the 1099-B, the default for "
                             "covered securities), B/E (1099-B without "
                             "basis), C/F (no 1099-B)")
    parser.add_argument("--out", type=Path, default=None,
                        help="TXF only: write the .txf here instead of "
                             "stdout")
    parser.add_argument("--year", type=tax_year, default=None,
                        help="Defensive year filter (pipeline gains files "
                             "are already year-scoped)")
    parser.add_argument("--base-currency", default="",
                        help="Currency label for the header")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Also write the rows as CSV to this path")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    parser.add_argument("--date-basis", choices=("settle", "trade"),
                        default=None,
                        help="Date the gains files were year-scoped on "
                             "(the wrapper passes the project's "
                             "tax_date). Default: the files' own "
                             "summary.tax_date_basis, else the form's "
                             "convention (8949/txf trade, Schedule 3 "
                             "settle).")
    args = parser.parse_args(argv)

    # The same ownership table `taxjson` dispatch enforces: Schedule 3
    # is Canada's, Form 8949 / TXF the US's (partition COMMANDS-04; the
    # standalone was not gated).
    from taxjson.lib.country import command_country_problem
    _cp = command_country_problem("form-export", args.country,
                                  variant=args.form)
    if _cp:
        print(f"taxjson-form-export: {_cp}", file=sys.stderr)
        return 2

    for p in args.files + args.crypto:
        if not p.exists():
            print(f"taxjson-form-export: no such file: {p}", file=sys.stderr)
            return 2

    try:
        return _main(args)
    except (OSError, ValueError) as e:
        # A truncated gains file crashed with a raw JSONDecodeError
        # traceback (R1-277).
        print(f"taxjson-form-export: could not read the gains files: "
              f"{e} — re-run `taxjson run` to rebuild them.",
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
                         f"them with one `taxjson run`")
    return seen.pop() if seen else None


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
        for e in load_json(p).get("transactions") or []:
            c = str(e.get("currency") or "").upper()
            if c and c != base and e.get("action") not in _INCOME_ACTIONS:
                other[c] = other.get(c, 0) + 1
        if other:
            got = ", ".join(f"{n} {c}" for c, n in sorted(other.items()))
            print(f"taxjson-form-export: {p}: dispositions in another "
                  f"currency than {base} ({got} row(s)) — pass the "
                  f"converted <account>_gains(_wash).json, not the native "
                  f"*_raw_gains.json (`taxjson form-export` does)",
                  file=sys.stderr)
            raise SystemExit(2)


def _main(args) -> int:
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
    _check_currency(list(args.files) + list(args.crypto),
                    args.base_currency or _home_ccy(args))
    entries, tainted = load_dispositions(
        [p for p in args.files if p.resolve() not in _crypto],
        args.year, date_key)
    if args.crypto:
        c_entries, c_tainted = load_dispositions(
            sorted(_crypto), args.year, date_key)
        entries += mark_crypto(c_entries)
        tainted += c_tainted
    if args.form == "schedule3" and any(
            e.get("term") in ("SHORT_TERM", "LONG_TERM") for e in entries):
        # Short/long-term terms exist only in the US engine's output:
        # Schedule 3 from US-computed gains (FIFO, §1091) would put US
        # numbers on a Canadian return.
        raise SystemExit(
            "taxjson-form-export: these gains files carry short/long-term "
            "terms — they were computed by the US engine; Schedule 3 needs "
            "a country=canada gains file (re-run `taxjson run` in the "
            "Canadian project).")
    manual = load_manual_rows(list(args.files) + list(args.crypto),
                              args.year, date_key)
    manual_proceeds = round(sum(abs(float(m.get("proceeds") or 0.0))
                                for m in manual), 2)
    if tainted or manual:
        _names = ", ".join(
            f"{m.get('symbol')} {m.get('date')} "
            f"({abs(float(m.get('proceeds') or 0.0)):,.2f})"
            for m in manual[:8])
        _more = f" (+{len(manual) - 8} more)" if len(manual) > 8 else ""
        print(f"warning: {max(tainted, len(manual))} tainted "
              f"disposition(s) with phantom cost basis are NOT in the "
              f"{'TXF' if args.form == 'txf' else 'form'} rows or totals "
              f"— proceeds {manual_proceeds:,.2f}: {_names}{_more}. "
              f"Report them by hand once their cost is known "
              f"(`taxjson find-missing-history`); they are listed in the "
              f"MANUAL REPORTING section.", file=sys.stderr)

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
    else:
        rep = build_schedule3(entries, args.year)
        rep["currency"] = args.base_currency or "CAD"
    rep["manual_reporting_required"] = manual
    rep["manual_proceeds"] = manual_proceeds
    if args.form == "8949":
        text = render_8949(rep, args.year, rep["currency"])
    else:
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
