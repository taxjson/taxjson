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
         common 1099-B practice.

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
listed in missing_history.json) have no computable gain: the
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

Or through the project wrapper: `taxjson form-export` (form defaults from
the project's country).
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.bin.taxjson_convert_currency import norm_currency
from taxjson.lib.core import is_option_symbol
from taxjson.lib.futures import is_plain_future, section_1256_kind
from taxjson.lib.numeric import round_half_up
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
    unknown-cost dispositions (no purchase in the files) exist in the
    year."""
    if not rows:
        return []
    total = sum(abs(float(r.get("proceeds") or 0.0)) for r in rows)
    lines = [f"MANUAL REPORTING REQUIRED — {len(rows)} sale(s) with no "
             f"purchase in your files (unknown cost, missing_history.json), "
             f"proceeds "
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

def _cents(x: float) -> float:
    """One money cell: half-up to the cent on the stored value (lib/
    numeric.round_half_up — the project's presentation rounding). The
    binary round() put a half-cent denial on (g) as 530.42 while the
    allowed gain elsewhere read -530.42 (A2-1105). `+ 0.0` kills -0.00."""
    return round_half_up(float(x or 0.0), 2) + 0.0


# Form 8949 checkboxes by part: securities on A/B/C (short-term) and
# D/E/F (long-term); from tax year 2025 digital assets on their own
# G/H/I and J/K/L (2025 Instructions for Form 8949: "Use box G, H, or I
# to report short-term digital asset transactions. Do not use box C";
# A2-0482, A2-0829). Digital assets are the dispositions of `crypto =
# true` accounts (flagged `_crypto` by mark_crypto).
DIGITAL_ASSET_BOXES_FROM = 2025
_BOXES = {("I", False): "A/B/C", ("II", False): "D/E/F",
          ("I", True): "G/H/I", ("II", True): "J/K/L"}


def build_8949(entries: List[Dict[str, Any]],
               year: Optional[int] = None) -> Dict[str, Any]:
    """Form 8949 rows by part. `year` (default: the latest year among
    the rows) decides whether `_crypto` rows go on the digital-asset
    boxes (2025 and later) or with the securities."""
    if year is None:
        year = _infer_year(entries)
    da_boxes = year is not None and int(year) >= DIGITAL_ASSET_BOXES_FROM
    parts: Dict[str, List[Dict[str, Any]]] = {"I": [], "II": []}
    sec1256: List[Dict[str, Any]] = []
    drift_warned = 0
    for e in entries:
        term = e.get("term")
        if term not in ("SHORT_TERM", "LONG_TERM"):
            raise SystemExit(
                "taxjson-form-export: entry for "
                f"{e.get('symbol')!r} on {e.get('date')!r} has no ST/LT "
                "term — Form 8949 needs a country=usa gains file "
                "(Canada has no term concept; use --form schedule3).")
        _kind = section_1256_kind(str(e.get("symbol") or ""))
        if _kind:
            # A §1256 contract (a future, an option on one, a broad-
            # based index option) is reported on Form 6781 — 60/40,
            # marked to market at year end — never on Form 8949, and
            # neither is modelled (tax-logic US-FUT-02 / US-OPT-04). It
            # went on Part I as a covered short-term sale, a futures
            # loss as NEGATIVE proceeds (A2-0118, A2-0322, A2-0323): a
            # user following tax-logic reported it twice. Kept off the
            # parts and their totals, listed for Form 6781 by hand.
            sec1256.append({
                "description": str(e.get("symbol") or ""),
                "kind": _kind,
                "date_acquired": (_acquired_date(e)
                                  if (e.get("direction") or "LONG")
                                  != "SHORT" else e.get("date") or ""),
                "date_sold": e.get("date") or "",
                "gain": _cents(e.get("gain")),
                "account": e.get("account") or "",
            })
            continue
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
            from taxjson.lib.out import warn as _warn
            _warn(f"{e.get('symbol')} {e.get('date')}: NEGATIVE "
                  f"disallowed_amount {adj:.2f} — row rendered without an "
                  f"adjustment", details=["Inspect it before filing."])
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
            # §301(c)(3): no shares were sold (US-ROC-02); a row says
            # its own kind when it is another (US-WASH-22).
            desc = e.get("deemed_desc") or (
                f"{e.get('symbol')} nondividend distribution in excess "
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
        _d, _e = _cents(proceeds), _cents(cost)
        _g = _cents(adj) if code else 0.0
        _da = bool(da_boxes and e.get("_crypto"))
        parts[part].append({
            "boxes": _BOXES[(part, _da)],
            "digital_asset": _da,
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
        from taxjson.lib.out import warn as _warn
        _warn(f"{drift_warned} row(s) where (d)-(e)+(g) differs from the "
              f"engine's allowed gain by more than $0.02",
              details=["Inspect them before filing."])
    for rows in list(parts.values()):
        rows.sort(key=lambda r: (r["digital_asset"], r["date_sold"],
                                 r["description"]))
    sec1256.sort(key=lambda r: (r["date_sold"], r["description"]))

    def totals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        return {
            "proceeds": round(sum(r["proceeds"] for r in rows), 2),
            "cost": round(sum(r["cost"] for r in rows), 2),
            "adjustment": round(sum(r["adjustment"] for r in rows), 2),
            "gain": round(sum(r["gain"] for r in rows), 2),
        }
    groups = []
    for part in ("I", "II"):
        for _da in (False, True):
            g_rows = [r for r in parts[part] if r["digital_asset"] == _da]
            if g_rows:
                groups.append({"part": part, "boxes": _BOXES[(part, _da)],
                               "digital_asset": _da,
                               "rows": len(g_rows),
                               "totals": totals(g_rows)})
    return {
        "form": "8949", "year": year,
        # Per checkbox group (a 2025+ digital-asset group apart): each
        # group is its own Form 8949 page(s) with its own totals.
        "groups": groups,
        "part_I": parts["I"], "part_I_totals": totals(parts["I"]),
        "part_II": parts["II"], "part_II_totals": totals(parts["II"]),
        # The engine's unrounded sums over the same rows (the export
        # rounds per row, as filed — A2-1108's note compares them).
        "gain_unrounded": sum(float(e.get("gain") or 0.0)
                              for e in entries
                              if not section_1256_kind(
                                  str(e.get("symbol") or ""))),
        "adjustment_unrounded": sum(
            float(e.get("disallowed_amount") or 0.0) for e in entries
            if float(e.get("disallowed_amount") or 0.0) > _EPS
            and not section_1256_kind(str(e.get("symbol") or ""))),
        "section_1256": sec1256,
        "section_1256_totals": {
            "gain": round(sum(r["gain"] for r in sec1256), 2) + 0.0,
            "dispositions": len(sec1256)},
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
# The 2024 form (5000-S3 E (24)) splits Part 3 by the date of the
# disposition (A2-0166): Period 1, January 1 to June 24, 2024, on
# 10689 / 10690 (shares) and 10693 / 10694 (bonds, crypto-assets and
# other properties), slip lines 17399 / 17599; Period 2, June 25 to
# December 31, on 13199 / 13200 and 15199 / 15300, slips 17400 / 17600.
_FUTURES_PREFIXES = ("F:", "/", "\\")
_LINE_ORDER = ("shares_p1", "other_p1", "shares", "other", "crypto")
PERIOD_YEAR = 2024
PERIOD_1_END = "2024-06-24"
_PERIOD_TEXT = {1: "Period 1 (January 1 to June 24, 2024)",
                2: "Period 2 (June 25 to December 31, 2024)"}


def schedule3_period(e: Dict[str, Any], year: Optional[int],
                     date_key: str = "date_settle") -> Optional[int]:
    """1 or 2 for a disposition on the 2024 form (its date on the
    gains files' tax-date basis: settle by default, CA-DATE-01); None
    for every other year (one period)."""
    if year is None or int(year) != PERIOD_YEAR:
        return None
    d = str(e.get(date_key) or e.get("date_settle") or e.get("date") or "")
    return 1 if d[:10] <= PERIOD_1_END else 2


def schedule3_line(key: str, year: Optional[int],
                   period: Optional[int] = None) -> Dict[str, str]:
    """The Schedule 3 line a property class lands on for `year`.
    `key` is shares | other | crypto (or a period-1 key, shares_p1 |
    other_p1); crypto folds into `other` before 2025 (the separate
    crypto-assets line starts with the 2025 form). `period` (2024 only,
    schedule3_period) picks the 2024 form's Period 1 or Period 2
    codes."""
    new_form = year is None or int(year) >= 2025
    if key.endswith("_p1"):
        key, period = key[:-3], 1
    if key == "crypto" and not new_form:
        key = "other"
    split = (not new_form and int(year) == PERIOD_YEAR
             and period in (1, 2))
    p1 = split and period == 1
    tag = f" — {_PERIOD_TEXT[period]}" if split else ""
    if key == "shares":
        return {"key": "shares_p1" if p1 else "shares",
                "line": "4" if new_form else "",
                "label": "Publicly traded shares, mutual fund units, "
                         "deferral of eligible small business "
                         "corporation shares, and other shares" + tag,
                "short": "shares & fund units"
                         + (f" (P{period})" if split else ""),
                "period": period if split else None,
                "proceeds_code": "10689" if p1 else "13199",
                "gain_code": "10690" if p1 else "13200"}
    if key == "crypto":
        return {"key": "crypto", "line": "7",
                "label": "Crypto-assets", "short": "crypto-assets",
                "period": None,
                "proceeds_code": "15200", "gain_code": "15301"}
    return {"key": "other_p1" if p1 else "other",
            "line": "6" if new_form else "",
            "label": ("Bonds, debentures, promissory notes, and other "
                      "similar properties (options, futures, foreign "
                      "currency)" if new_form else
                      "Bonds, debentures, promissory notes, crypto-"
                      "assets, and other similar properties (options, "
                      "futures, foreign currency)") + tag,
            "short": ("options & other properties" if new_form else
                      "options, crypto & other properties")
                     + (f" (P{period})" if split else ""),
            "period": period if split else None,
            "proceeds_code": "10693" if p1 else "15199",
            "gain_code": "10694" if p1 else "15300"}


def line_title(spec: Dict[str, str]) -> str:
    """'Part 3, line 4 (13199/13200)' — or, for pre-2025 forms whose
    line numbering this tool does not pin, just the line codes."""
    codes = f"lines {spec['proceeds_code']}/{spec['gain_code']}"
    if spec.get("period"):
        return f"{_PERIOD_TEXT[spec['period']]}, {codes}"
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


def _foot_cells(proceeds_u: float, outlays_u: float,
                gain_u: float) -> Tuple[float, float, float, float]:
    """(proceeds, acb, outlays, gain) cents for one Schedule 3 row that
    FOOT exactly: PROCEEDS − ACB − OUTLAYS = GAIN. Each cell is rounded
    half-up on its own value (the .sum's convention — binary round()
    put a 1,469.575 ACB at 1,469.57 here and 1,469.58 there, A2-1104);
    the ACB is the residual. When the separately rounded cells leave the
    residual a cent below zero (a premium-only write: 27.672 / 13.836 /
    13.836 showed ACB -0.01, A2-0649), the ACB is 0.00 and the cent is
    absorbed by OUTLAYS (else by the gain) — no cell goes negative."""
    proceeds, outlays, gain = (_cents(proceeds_u), _cents(outlays_u),
                               _cents(gain_u))
    acb = round(proceeds - outlays - gain, 2) + 0.0
    acb_u = proceeds_u - outlays_u - gain_u
    if acb < 0 and acb_u > -_EPS:
        acb = 0.0
        if proceeds - gain >= 0:
            outlays = round(proceeds - gain, 2) + 0.0
        else:
            gain = round(proceeds - outlays, 2) + 0.0
    return proceeds, acb, outlays, gain
def grant_buyback_units(e: Dict[str, Any], year: Optional[int]) -> float:
    """Units of a (non-grant) SHORT close row that buy back a grant-
    timing WRITE of the same year `year`: those contracts were disposed
    of once (the write), so Schedule 3 and reconcile-slips count them
    with the write, not again at the buy-back (R1-18, S003-04). A buy-
    back of an EARLIER year's write, or of a close-timing (pre-since)
    write, is a disposition of its own and counts (A2-0320, A2-0650,
    A2-0651). The engine names the write years a close consumed in
    `grant_closed`; a gains file written before that field nets every
    short close (the old count)."""
    gc = e.get("grant_closed")
    if isinstance(gc, dict):
        return sum(abs(float((v or {}).get("units") or 0.0))
                   for y, v in gc.items()
                   if year is None or str(y) == str(year))
    return abs(float(e.get("qty") or 0.0))


def build_schedule3(entries: List[Dict[str, Any]],
                    year: Optional[int] = None,
                    date_key: str = "date_settle") -> Dict[str, Any]:
    """Per-property rows grouped by Schedule 3 line. Every row FOOTS:
    PROCEEDS − ACB − OUTLAYS = GAIN (the allowed gain), so a denied
    superficial loss shows as an ACB reduced by the denial — the denied
    amount is what gets added to the replacement property's ACB.
    `date_key`: the date the gains files were scoped on (date_settle,
    or date under tax_date = "trade"); on the 2024 form it also picks
    the Period 1 / Period 2 line codes (A2-0166), so a symbol sold in
    both periods is two rows."""
    if year is None:
        year = _infer_year(entries)
    recs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in entries:
        symbol = e.get("symbol") or "?"
        pclass = property_class(e)
        lkey = schedule3_line(_line_key(pclass), year,
                              schedule3_period(e, year, date_key))["key"]
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
        if outlays < 0:
            # A net REBATE (IB, Questrade: a negative commission/fee)
            # is not an outlay or expense: it stays netted in the
            # proceeds, so the OUTLAYS cell is never negative (A2-0653,
            # A2-1053; tax software can reject a negative outlay). The
            # gain is unchanged.
            outlays = 0.0
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
            if e.get("grant") and _sold + outlays >= 0:
                # A WRITE under grant timing (s.49(1)): the row's
                # commission/fee is the write's own, netted into the
                # premium. Show the premium GROSS with the commission
                # as an outlay, as for a sale (audit R1-40, CA-DISP-06;
                # gain unchanged). A buy-back row's fee is a cost of
                # acquiring the cover and stays in the ACB.
                rec["proceeds"] += _sold + outlays
                rec["outlays"] += outlays
            elif _sold >= 0:
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
                rec["short_close_units"] += grant_buyback_units(e, year)
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
        spec = schedule3_line(lkey, year, 2)
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
        proceeds, acb, outlays, gain = _foot_cells(
            r["proceeds"], r["outlays"], r["gain"])
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
            "acb": acb,
            "outlays": outlays,
            "gain": gain,
            "denied": _cents(r["denied"]),
            "dispositions": r["n"],
            "notes": "; ".join(notes),
        })

    lines: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    for lkey in _LINE_ORDER:
        lrows = [r for r in rows if r["line_key"] == lkey]
        if not lrows:
            continue
        spec = schedule3_line(lkey, year, 2)
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
            "gain_unrounded": sum(r["gain"] for r in recs.values()),
            "denied_unrounded": sum(r["denied"] for r in recs.values())}


def filing_lines(entries: List[Dict[str, Any]],
                 year: Optional[int] = None,
                 date_key: str = "date_settle") -> List[Dict[str, Any]]:
    """One dict per Schedule 3 line that has dispositions: the line
    number / codes / label plus PROCEEDS, ACB, OUTLAYS, GAIN, DENIED and
    the disposition count — the rows of `taxjson sum`'s FOR THE RETURN
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
    agg = {k: round(sum(r[k] for r in rep["rows"]), 2) + 0.0
           for k in ("proceeds", "acb", "outlays", "gain", "denied")}
    return {"proceeds": agg["proceeds"],
            "acb": agg["acb"],
            "outlays": agg["outlays"], "gain": agg["gain"],
            "denied": agg["denied"],
            "dispositions": len(entries)}


def filing_parts_8949(entries: List[Dict[str, Any]],
                      year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Form 8949's own totals per part and checkbox group — (d)
    proceeds, (e) cost, (g) adjustment, (h) gain — for `taxjson sum`'s
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
    `taxjson sum`'s US FOR THE RETURN note (A2-0324)."""
    return dict(build_8949(entries)["section_1256_totals"])


SEC1256_NOTE = ("§1256 contracts (futures, options on futures, broad-"
                "based index options such as SPX) are NOT on Form 8949: "
                "report them on Form 6781 by hand — the net is split 60% "
                "long-term / 40% short-term, and contracts still open on "
                "Dec 31 are marked to market (neither is modelled; tax-"
                "logic US-FUT-02, US-OPT-04).")


def section_1256_lines(rep: Dict[str, Any], cur: str) -> List[str]:
    """The FORM 6781 block of the 8949 text report (empty when the
    year has no §1256 contract)."""
    rows = rep.get("section_1256") or []
    if not rows:
        return []
    t = rep.get("section_1256_totals") or {}
    lines = [f"FORM 6781 BY HAND — {len(rows)} §1256 contract "
             f"disposition(s)"]
    lines += _para(f"Net {t.get('gain', 0.0):,.2f} {cur}: NOT in the Form "
                   f"8949 rows or totals above.")
    table = [(r["description"], r["kind"], r["date_acquired"],
              r["date_sold"], f"{r['gain']:,.2f}") for r in rows]
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
           right: set) -> List[str]:
    """A table fitted to the house width (lib/out.fit_table): too wide,
    one record per row headed by its first column."""
    from taxjson.lib.out import fit_table
    if not rows:
        return ["  (no dispositions)"]
    aligns = [">" if i in right else "<" for i in range(len(header))]
    return fit_table(header, rows, aligns=aligns)


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
    vals = [f"{v:,.2f}" for _, v in pairs]
    vw = max(len(v) for v in vals)
    return [f"{indent}{k + ':':<{lw}}  {v:>{vw}}"
            for (k, _), v in zip(pairs, vals)]


def _manual_console(rows: List[Dict[str, Any]], cur: str) -> List[str]:
    """manual_section() in the console layout."""
    if not rows:
        return []
    total = sum(abs(float(r.get("proceeds") or 0.0)) for r in rows)
    lines = [f"MANUAL REPORTING REQUIRED — {len(rows)} sale(s) with no "
             f"purchase in your files"]
    lines += _para(f"Unknown cost (missing_history.json), proceeds "
                   f"{total:,.2f} {cur}: NOT in the rows or totals above. "
                   f"Report each by hand once its cost is known "
                   f"(`taxjson find-missing-history`).")
    table = [(str(r.get("symbol") or ""), str(r.get("date") or ""),
              _qty_str(abs(float(r.get("qty") or 0.0))),
              f"{abs(float(r.get('proceeds') or 0.0)):,.2f}",
              str(r.get("account") or ""))
             for r in rows]
    lines += _table(("SYMBOL", "DATE", "UNITS", "PROCEEDS", "ACCOUNT"),
                    table, right={2, 3})
    lines.append("")
    return lines


def _rounding_note(rep: Dict[str, Any]) -> List[str]:
    """The note `taxjson sum` prints beside FOR THE RETURN (R1-166),
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
        gap = round(float(shown) - float(raw), 2)
        if abs(gap) >= 0.005:
            out += _item(f"Rows are rounded to the cent, as filed: the "
                         f"gains files' unrounded total {label} is "
                         f"{float(raw):,.2f} ({gap:+,.2f} on the totals "
                         f"above).")
    return out


def _year_note(rep: Dict[str, Any]) -> List[str]:
    """The year-not-ended sentence (the --json field keeps its NOTE:)."""
    t = str(rep.get("year_not_ended") or "")
    if t.startswith("NOTE: t"):
        t = "T" + t[len("NOTE: t"):]
    return _para(t) if t else []


def render_8949(rep: Dict[str, Any], year: Optional[int], cur: str) -> str:
    lines = [f"FORM 8949 — Sales and Other Dispositions of Capital Assets, "
             f"tax year {year or '?'}, {cur}"]
    lines += _year_note(rep)
    lines.append("")
    header = ("(a) DESCRIPTION", "(b) ACQUIRED", "(c) SOLD",
              "(d) PROCEEDS", "(e) COST", "(f)", "(g) ADJ",
              "(h) GAIN(LOSS)")

    def _rows_table(rows):
        return _table(header, [
            (r["description"], r["date_acquired"], r["date_sold"],
             f"{r['proceeds']:,.2f}", f"{r['cost']:,.2f}", r["code"],
             f"{r['adjustment']:,.2f}" if r["code"] else "",
             f"{r['gain']:,.2f}") for r in rows], right={3, 4, 6, 7})

    def _totals(label: str, t: Dict[str, float]) -> List[str]:
        return [f"  {label}"] + _amounts(
            [("proceeds", t['proceeds']), ("cost", t['cost']),
             ("adjustments", t['adjustment']), ("gain", t['gain'])],
            "    ")

    has_da = any(r.get("digital_asset") for p in ("I", "II")
                 for r in rep[f"part_{p}"])
    for part, label in (("I", "PART I — SHORT-TERM"),
                        ("II", "PART II — LONG-TERM")):
        rows = rep[f"part_{part}"]
        t = rep[f"part_{part}_totals"]
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
        lines.append("")
    lines += section_1256_lines(rep, cur)
    lines += _manual_console(rep.get("manual_reporting_required") or [],
                             cur)
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
    lines += _rounding_note(rep)
    lines += _item("Not tax advice; reconcile against your 1099-B before "
                   "filing.")
    return "\n".join(lines)


def render_schedule3(rep: Dict[str, Any], year: Optional[int],
                     cur: str) -> str:
    year = year or rep.get("year")
    lines = [f"SCHEDULE 3 — Capital Gains (or Losses), tax year "
             f"{year or '?'}, {cur}"]
    lines += _year_note(rep)
    lines.append("")
    header = ("UNITS", "SYMBOL", "ACQ. YEAR", "PROCEEDS", "ACB",
              "OUTLAYS", "GAIN(LOSS)")
    by_line = rep.get("lines") or []
    if not by_line:
        spec = schedule3_line("shares", year)
        lines.append(line_title(spec).upper())
        lines += _para(spec['label'])
        lines += _table(header, [], right=set())
        lines.append("")
    for ln in by_line:
        lines.append(ln['title'].upper())
        lines += _para(ln['label'])
        sel = [r for r in rep["rows"] if r["line_key"] == ln["key"]]
        table = [(f"{r['units']:,.8f}".rstrip("0").rstrip(".") or "0",
                  r["symbol"],
                  str(r["acq_year"]), f"{r['proceeds']:,.2f}",
                  f"{r['acb']:,.2f}", f"{r['outlays']:,.2f}",
                  f"{r['gain']:,.2f}")
                 for r in sel]
        lines += _table(header, table, right={0, 3, 4, 5, 6})
        lines += _amounts(
            [(f"Line {ln['proceeds_code']} (proceeds of disposition)",
              ln['proceeds']),
             (f"Line {ln['gain_code']} (gain/loss)", ln['gain'])], "  ")
        lines += _row_notes([(r["symbol"], r["notes"]) for r in sel])
        lines.append("")
    lines += _manual_console(rep.get("manual_reporting_required") or [],
                             cur)
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
    lines += _item("Apply the inclusion rate on Schedule 3 itself; these "
                   "are 100% amounts.")
    lines += _item("PROCEEDS re-adds sell-side commissions so OUTLAYS can "
                   "be shown separately (a commission rebate stays netted "
                   "in PROCEEDS); the gain is unchanged.")
    lines += _rounding_note(rep)
    lines += _item("FX gains on foreign cash (s.39(1.1), `taxjson "
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
                   "before filing (`taxjson reconcile-slips`).")
    return "\n".join(lines)


def write_csv(rep: Dict[str, Any], path: Path) -> None:
    """Write through `<path>.part` and replace, as the TXF --out does: a
    failed write left a truncated CSV in place of the good one
    (S032-24)."""
    from taxjson.lib.safe_write import discard, publish, temp_name
    tmp = temp_name(path)
    try:
        _write_csv(rep, tmp)
        publish(tmp, path)
    except BaseException:
        discard(tmp)
        raise


def _write_csv(rep: Dict[str, Any], path: Path) -> None:
    # A fresh owner-only file, never through a symlink at `path`
    # (lib/safe_write; security review M1).
    from taxjson.lib.safe_write import open_new
    with open_new(path, newline="") as f:
        _rows_csv(rep, f)
        f.flush()
        os.fsync(f.fileno())


def _rows_csv(rep: Dict[str, Any], f) -> None:
    w = csv.writer(f)
    if rep["form"] == "8949":
        w.writerow(["part", "description", "date_acquired", "date_sold",
                    "proceeds", "cost", "code", "adjustment",
                    "gain", "account", "boxes"])
        for part in ("I", "II"):
            for r in rep[f"part_{part}"]:
                w.writerow([part, r["description"], r["date_acquired"],
                            r["date_sold"], r["proceeds"], r["cost"],
                            r["code"], r["adjustment"], r["gain"],
                            r["account"], r.get("boxes", "")])
        # §1256 contracts: Form 6781 by hand, never an 8949 row.
        for r in rep.get("section_1256") or []:
            w.writerow(["6781", f"{r['description']} ({r['kind']})",
                        r["date_acquired"], r["date_sold"], "", "",
                        "", "", r["gain"], r["account"]])
        # Unknown-cost dispositions: flagged rows,
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
                        "no purchase in your files, cost unknown "
                        "(missing_history.json) - report by hand"])


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
                             "The `taxjson form-export` wrapper passes "
                             "the crypto = true accounts here.")
    parser.add_argument("--form", required=True,
                        choices=["8949", "schedule3", "txf"])
    from taxjson.lib.country import add_country_argument
    add_country_argument(parser, help="Whose return the rows are for "
                         "(required): canada (--form schedule3) | usa "
                         "(--form 8949 / txf). The `taxjson form-export` "
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
                             "are already year-scoped)")
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
        blank = 0
        for e in load_json(p).get("transactions") or []:
            if e.get("action") in _INCOME_ACTIONS:
                continue
            c = str(e.get("currency") or "").upper()
            if c and c != base:
                other[c] = other.get(c, 0) + 1
            elif not c and "gain" in e and "qty" in e:
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
    _home = _home_ccy(args)
    if args.base_currency and args.base_currency.strip().upper() != _home:
        # --base-currency was compared with the rows only, so passing
        # the rows' own currency exported a Canadian Schedule 3 in USD
        # (or a Form 8949 in CAD) at exit 0 (A2-0652, A2-1118). The
        # return is filed in the country's currency (tax-logic
        # CA-CTRY-03; Form 8949 in USD).
        print(f"taxjson-form-export: --base-currency "
              f"{args.base_currency} does not match the {args.country} "
              f"return's currency {_home} — the form is filed in {_home}: "
              f"pass the converted <account>_gains(_wash).json "
              f"(`taxjson run` converts them; `taxjson form-export` "
              f"passes them).", file=sys.stderr)
        raise SystemExit(2)
    _check_currency(list(args.files) + list(args.crypto), _home)
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
    # Each file once: the wrapper passes a crypto account's gains file
    # positionally AND as --crypto, which listed every crypto unknown-
    # cost disposition twice under MANUAL REPORTING (A2-0113).
    _seen_manual: set = set()
    _manual_paths = []
    for _p in list(args.files) + list(args.crypto):
        if _p.resolve() not in _seen_manual:
            _seen_manual.add(_p.resolve())
            _manual_paths.append(_p)
    manual = load_manual_rows(_manual_paths, args.year, date_key)
    manual_proceeds = round(sum(abs(float(m.get("proceeds") or 0.0))
                                for m in manual), 2)
    if tainted or manual:
        _names = ", ".join(
            f"{m.get('symbol')} {m.get('date')} "
            f"({abs(float(m.get('proceeds') or 0.0)):,.2f})"
            for m in manual[:8])
        _more = f" (+{len(manual) - 8} more)" if len(manual) > 8 else ""
        from taxjson.lib.out import warn as _warn
        _warn(f"{max(tainted, len(manual))} disposition(s) with an "
              f"unknown cost (no purchase in your files) are NOT in the "
              f"{'TXF' if args.form == 'txf' else 'form'} rows or totals "
              f"— proceeds {manual_proceeds:,.2f}",
              details=[f"{_names}{_more}.",
                       "Report them by hand once their cost is known "
                       "(`taxjson find-missing-history`); they are listed "
                       "in the MANUAL REPORTING section."])

    rep_8949 = (build_8949(entries, args.year)
                if args.form in ("8949", "txf") else None)
    if rep_8949 is not None:
        _s1256 = rep_8949["section_1256_totals"]
        if _s1256["dispositions"]:
            from taxjson.lib.out import warn as _warn
            _warn(f"{_s1256['dispositions']} §1256 contract "
                  f"disposition(s) (net {_s1256['gain']:,.2f}) are NOT in "
                  f"the {'TXF records' if args.form == 'txf' else 'Form 8949 rows'}",
                  details=[SEC1256_NOTE
                           + (" `--form 8949` lists them."
                              if args.form == "txf" else "")])
    if args.form == "txf":
        from taxjson.lib.out import warn as _warn
        if args.csv or args.json:
            _warn("--csv/--json have no effect with --form txf (TXF is its "
                  "own format) — ignored")
        # TXF rides on the 8949 model — same rows, same code-W math.
        rep = rep_8949
        _ynote = year_not_ended_text(args.year or _infer_year(entries))
        if _ynote:
            _warn(_ynote)
        doc = build_txf(rep, args.box)
        _da_rows = [r for p in ("I", "II") for r in rep[f"part_{p}"]
                    if r.get("digital_asset")]
        if _da_rows:
            _warn(f"{len(_da_rows)} digital-asset disposition(s) "
                  f"(proceeds {sum(r['proceeds'] for r in _da_rows):,.2f}) "
                  f"are NOT in the TXF",
                  details=["From 2025 they go on Form 8949 boxes G-L, "
                           "which have no TXF reference number taxjson "
                           "knows — enter them by hand (`--form 8949` "
                           "lists them by box)."])
        if args.out:
            from taxjson.lib.safe_write import write_atomic
            try:
                write_atomic(args.out, doc, encoding="ascii")
            except (OSError, UnicodeEncodeError) as e:
                sys.exit(f"taxjson-form-export: cannot write --out "
                         f"{args.out}: {e}")
            n = sum(1 for p in ("I", "II") for r in rep[f"part_{p}"]
                    if not r.get("digital_asset"))
            print(f"wrote {n} TXF record(s) (box {args.box}) to "
                  f"{args.out}", file=sys.stderr)
        else:
            sys.stdout.write(doc)
        return 0

    if args.form == "8949":
        rep = rep_8949
        rep["currency"] = _home
    else:
        rep = build_schedule3(entries, args.year, date_key)
        rep["currency"] = _home
    rep["manual_reporting_required"] = manual
    rep["manual_proceeds"] = manual_proceeds
    _ynote = year_not_ended_note(args.year or rep.get("year")
                                 or _infer_year(entries))
    if _ynote:
        rep["year_not_ended"] = _ynote
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
