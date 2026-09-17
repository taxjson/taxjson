#!/usr/bin/env python3
"""
taxjson_reconcile_slips.py

Reconcile the broker's official tax slips — CRA T5008 or IRS 1099-B — against
taxjson's computed dispositions, BEFORE filing. CRA/IRS receive copies of
these slips and machine-match returns against them; this report finds every
divergence and makes you decide, per symbol, whether it's a tool-side problem
(dropped rows, missing statement months) or a legitimate difference to
document (per-broker box-20 cost vs blended ACB, broker lot method vs FIFO,
per-account vs cross-account wash adjustments).

Slip input is a CSV with one row per disposition (or per symbol, already
aggregated) — export it from your broker or hand-build it from the slips.
Headers are matched loosely, so common broker/T5008 spellings work as-is:

    symbol:    symbol | ticker | security | sym
    quantity:  quantity | qty | shares | number of shares | box 16
    proceeds:  proceeds | proceeds of disposition | gross proceeds | box 21
    cost:      cost | cost or other basis | book value | acb | box 20
               (optional — omit the column to skip basis comparison)

Comparison is per symbol with market suffixes stripped (slip `AAPL` matches
computed `AAPL.US`). taxjson proceeds are net of sell-side commissions;
slips are usually gross — the tool compares against gross first and falls
back to net, telling you which one matched. Amounts must be in the same
currency as the project's base currency; the tool cannot convert slips.

Exit codes: 0 = everything reconciled; 1 = at least one mismatch or missing
symbol; 2 = usage error.

Usage:
    taxjson-reconcile-slips t5008.csv --gains margin_gains.json [--gains ...]
        [--year 2025] [--tolerance 1.00] [--json]

Or through the project wrapper: `taxjson reconcile-slips t5008.csv`.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.bin.taxjson_form_export import load_json

_SUFFIX_RE = re.compile(r"\.(US|TO|AX|L|V|CN|NE)$", re.IGNORECASE)

_HEADER_SYNONYMS = {
    "symbol": ("symbol", "ticker", "security symbol", "security", "sym"),
    "quantity": ("quantity", "qty", "shares", "number of shares", "box 16",
                 "quantity of securities"),
    "proceeds": ("proceeds of disposition", "gross proceeds", "proceeds",
                 "box 21", "proceeds of disposition or settlement amount"),
    "cost": ("cost or other basis", "cost basis", "book value",
             "cost/book value", "adjusted cost base", "acb", "box 20",
             "cost"),
}


def norm_symbol(sym: str) -> str:
    return _SUFFIX_RE.sub("", (sym or "").strip().upper().lstrip("."))


def _clean_amount(raw: str) -> Optional[float]:
    s = (raw or "").strip().replace(",", "").replace("$", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _map_headers(fieldnames: List[str]) -> Dict[str, str]:
    """Map our canonical keys to the CSV's actual column names. Longest
    synonym wins so 'proceeds of disposition' beats bare 'proceeds'."""
    mapping: Dict[str, str] = {}
    lowered = {f.strip().lower(): f for f in fieldnames if f}
    for key, synonyms in _HEADER_SYNONYMS.items():
        for syn in synonyms:
            for low, orig in lowered.items():
                if syn == low or syn in low:
                    mapping.setdefault(key, orig)
                    break
            if key in mapping:
                break
    return mapping


def load_slip(path: Path) -> Dict[str, Dict[str, Any]]:
    """Aggregate the slip CSV per normalized symbol:
    {SYM: {qty, proceeds, cost (or None), rows}}."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        # Québec-broker T5008 exports (Desjardins, NBDB, RBC French)
        # are commonly cp1252/latin-1 — the utf-8-only open crashed
        # with a raw UnicodeDecodeError traceback (REVIEW #22).
        text = path.read_text(encoding="cp1252")
    import io
    with io.StringIO(text, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"taxjson-reconcile-slips: {path} is empty")
        cols = _map_headers(list(reader.fieldnames))
        for required in ("symbol", "proceeds"):
            if required not in cols:
                raise SystemExit(
                    f"taxjson-reconcile-slips: {path} has no recognizable "
                    f"{required!r} column (headers: "
                    f"{', '.join(reader.fieldnames)}). See --help for "
                    f"accepted spellings.")
        out: Dict[str, Dict[str, Any]] = {}
        dropped = 0
        for row in reader:
            sym = norm_symbol(row.get(cols["symbol"], ""))
            if not sym:
                continue
            proceeds = _clean_amount(row.get(cols["proceeds"], ""))
            if proceeds is None:
                # A slip row the tool cannot read is a row it cannot
                # RECONCILE — silently dropping it certified a
                # disagreeing slip as fully reconciled at exit 0
                # (REVIEW #21).
                dropped += 1
                print(f"taxjson-reconcile-slips: warning: {path.name}: "
                      f"unreadable proceeds for {sym} "
                      f"({row.get(cols['proceeds'], '')!r}) — row NOT "
                      f"reconciled; fix the slip CSV cell.",
                      file=sys.stderr)
                continue
            rec = out.setdefault(sym, {"qty": 0.0, "proceeds": 0.0,
                                       "cost": None, "rows": 0})
            rec["rows"] += 1
            rec["proceeds"] += proceeds
            if "quantity" in cols:
                q = _clean_amount(row.get(cols["quantity"], ""))
                if q is not None:
                    rec["qty"] += abs(q)
            if "cost" in cols:
                c = _clean_amount(row.get(cols["cost"], ""))
                if c is not None:
                    rec["cost"] = (rec["cost"] or 0.0) + c
        if dropped:
            out["__dropped_rows__"] = dropped   # consumed (popped) in main
        return out


def load_computed(gains_paths: List[Path],
                  year: Optional[int],
                  date_basis: str = "settle") -> Dict[str, Dict[str, Any]]:
    """Aggregate computed dispositions per normalized symbol:
    {SYM: {qty, proceeds_net, proceeds_gross, cost, rows, tainted_rows}}.
    Tainted rows are INCLUDED in the counts here (the broker's slip will
    include those sales too) but flagged so a basis mismatch on a tainted
    symbol reads as expected, not alarming."""
    out: Dict[str, Dict[str, Any]] = {}
    ystr = str(year) if year else None
    for p in gains_paths:
        data = load_json(p)
        # Tainted (phantom-basis) dispositions live in
        # manual_reporting_required, NOT transactions — the pipeline
        # strips them there before writing the gains file, which made
        # every tainted_rows counter in this tool a permanent no-op
        # and a real broker-reported sale show as
        # MISSING_FROM_COMPUTED with the wrong diagnosis (2026-09
        # audit). Fold them back in, flagged.
        _manual = [dict(e, tainted=True)
                   for e in data.get("manual_reporting_required", [])
                   if e.get("qty")]
        for e in list(data.get("transactions", [])) + _manual:
            if e.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            if "gain" not in e or "qty" not in e:
                continue
            # Year-scope on the same basis the gains files (and the
            # broker's slip) use: the IRS recognizes on TRADE date, so a
            # 1099-B includes a Dec-31 sale settling in January — the
            # unconditional settle-first read excluded it and produced
            # spurious MISMATCH rows against the tool's own form-export.
            # CRA times dispositions on SETTLEMENT date (the default).
            if date_basis == "trade":
                date = e.get("date") or e.get("date_settle") or ""
            else:
                date = e.get("date_settle") or e.get("date") or ""
            if ystr and not date.startswith(ystr):
                continue
            sym = norm_symbol(e.get("symbol") or "")
            if not sym:
                continue
            rec = out.setdefault(sym, {"qty": 0.0, "proceeds_net": 0.0,
                                       "proceeds_gross": 0.0, "cost": 0.0,
                                       "rows": 0, "tainted_rows": 0})
            proceeds = float(e.get("proceeds") or 0.0)
            cost = float(e.get("cost") or 0.0)
            outlays = float(e.get("commission") or 0.0) + \
                float(e.get("fee") or 0.0)
            if (e.get("direction") or "LONG") == "SHORT":
                # Engine short convention: `cost` holds the (negated)
                # short-sale proceeds and `proceeds` the (negated)
                # cover cost — the SWAP form-export performs. Merely
                # stripping signs compared the slip's proceeds against
                # the COVER COST, a false MISMATCH equal to the gain
                # (2026-09 audit).
                proceeds, cost = abs(cost), abs(proceeds)
                outlays = 0.0
            rec["qty"] += abs(float(e.get("qty") or 0.0))
            rec["proceeds_net"] += proceeds
            rec["proceeds_gross"] += proceeds + outlays
            rec["cost"] += cost
            rec["rows"] += 1
            if e.get("tainted"):
                rec["tainted_rows"] += 1
    return out


def reconcile(slip: Dict[str, Dict[str, Any]],
              computed: Dict[str, Dict[str, Any]],
              tolerance: float) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for sym in sorted(set(slip) | set(computed)):
        s, c = slip.get(sym), computed.get(sym)
        if s is None:
            rows.append({"symbol": sym, "status": "MISSING_FROM_SLIP",
                         "detail": f"computed {c['rows']} disposition(s), "
                                   f"proceeds {c['proceeds_gross']:,.2f} — "
                                   f"no slip row (missing slip, or a "
                                   f"non-slip disposition like an option "
                                   f"expiry or corp action)"})
            continue
        if c is None:
            rows.append({"symbol": sym, "status": "MISSING_FROM_COMPUTED",
                         "detail": f"slip has {s['rows']} row(s), proceeds "
                                   f"{s['proceeds']:,.2f} — nothing "
                                   f"computed (dropped CSV rows or a "
                                   f"missing statement?)"})
            continue

        problems: List[str] = []
        notes: List[str] = []
        d_gross = s["proceeds"] - c["proceeds_gross"]
        d_net = s["proceeds"] - c["proceeds_net"]
        if abs(d_gross) <= tolerance:
            pass
        elif abs(d_net) <= tolerance:
            notes.append("matches NET proceeds (slip appears net of "
                         "commissions)")
        else:
            closer = d_gross if abs(d_gross) <= abs(d_net) else d_net
            problems.append(f"proceeds off by {closer:+,.2f} "
                            f"(slip {s['proceeds']:,.2f} vs computed "
                            f"gross {c['proceeds_gross']:,.2f} / net "
                            f"{c['proceeds_net']:,.2f})")
        if s["qty"] > 0 and abs(s["qty"] - c["qty"]) > 1e-4:
            problems.append(f"quantity off by {s['qty'] - c['qty']:+,.4f} "
                            f"(slip {s['qty']:,.4f} vs computed "
                            f"{c['qty']:,.4f})")
        if s["cost"] is not None:
            d_cost = s["cost"] - c["cost"]
            if abs(d_cost) > tolerance:
                notes.append(f"slip cost differs by {d_cost:+,.2f} — often "
                             f"legitimate (per-broker book value vs blended "
                             f"ACB / lot method); document the reason")
        if c["tainted_rows"]:
            notes.append(f"{c['tainted_rows']} tainted disposition(s) with "
                         f"phantom basis included")
        status = "MISMATCH" if problems else "OK"
        rows.append({"symbol": sym, "status": status,
                     "detail": "; ".join(problems + notes)})
    mismatches = [r for r in rows if r["status"] != "OK"]
    return {"rows": rows,
            "counts": {
                "ok": sum(1 for r in rows if r["status"] == "OK"),
                "mismatch": sum(1 for r in rows
                                if r["status"] == "MISMATCH"),
                "missing_from_computed": sum(
                    1 for r in rows
                    if r["status"] == "MISSING_FROM_COMPUTED"),
                "missing_from_slip": sum(
                    1 for r in rows if r["status"] == "MISSING_FROM_SLIP"),
            },
            "clean": not mismatches}


def render(rep: Dict[str, Any], tolerance: float) -> str:
    lines = ["SLIP RECONCILIATION — computed dispositions vs broker tax "
             "slips", ""]
    if not rep["rows"]:
        lines.append("Nothing to reconcile (no symbols on either side).")
        return "\n".join(lines)
    width = max(len(r["symbol"]) for r in rep["rows"])
    swidth = max(len(r["status"]) for r in rep["rows"])
    for r in rep["rows"]:
        lines.append(f"{r['symbol'].ljust(width)}  "
                     f"{r['status'].ljust(swidth)}  {r['detail']}".rstrip())
    c = rep["counts"]
    lines.append("")
    lines.append(f"{c['ok']} OK, {c['mismatch']} mismatch, "
                 f"{c['missing_from_computed']} missing from computed, "
                 f"{c['missing_from_slip']} missing from slip "
                 f"(tolerance ±{tolerance:,.2f}).")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - MISSING_FROM_SLIP is often benign: option expiries, "
                 "corp-action dispositions, and crypto don't get T5008/"
                 "1099-B rows.")
    lines.append("  - Slip cost (T5008 box 20) is per-broker book value; a "
                 "difference from blended ACB is expected when you hold the "
                 "security at more than one broker — document it, don't "
                 "'fix' it.")
    lines.append("  - Amounts are compared in the project base currency; "
                 "slips in another currency will not reconcile.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile T5008 / 1099-B slip CSVs against taxjson's "
                    "computed dispositions.")
    parser.add_argument("slip_csv", type=Path, help="Slip CSV (see --help "
                        "header docs for accepted column spellings)")
    parser.add_argument("--gains", action="append", type=Path, default=[],
                        required=True,
                        help="Year-scoped <account>_gains.json (repeatable; "
                             "prefer the wash-adjusted variants)")
    parser.add_argument("--year", type=int, default=None,
                        help="Defensive year filter")
    parser.add_argument("--date-basis", choices=("settle", "trade"),
                        default="settle",
                        help="Which date the --year filter scopes on: "
                             "'settle' (CRA/T5008, the default) or "
                             "'trade' (IRS/1099-B). The `taxjson "
                             "reconcile-slips` wrapper passes the "
                             "project's convention automatically.")
    parser.add_argument("--tolerance", type=float, default=1.00,
                        help="Absolute per-symbol amount tolerance "
                             "(default: 1.00)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    args = parser.parse_args(argv)

    if not args.slip_csv.exists():
        print(f"taxjson-reconcile-slips: no such file: {args.slip_csv}",
              file=sys.stderr)
        return 2
    for p in args.gains:
        if not p.exists():
            print(f"taxjson-reconcile-slips: no such file: {p}",
                  file=sys.stderr)
            return 2

    slip = load_slip(args.slip_csv)
    dropped_rows = int(slip.pop("__dropped_rows__", 0) or 0)
    computed = load_computed(args.gains, args.year, args.date_basis)
    rep = reconcile(slip, computed, args.tolerance)
    if dropped_rows:
        # Unreadable rows mean the slip was NOT fully reconciled —
        # exit 0 here certified agreement the tool never checked
        # (REVIEW #21).
        rep["clean"] = False
        rep["unreadable_rows"] = dropped_rows
    if args.json:
        json.dump(rep, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(render(rep, args.tolerance))
        if dropped_rows:
            print(f"\nNOT RECONCILED: {dropped_rows} slip row(s) had "
                  f"unreadable proceeds (see warnings above).")
    return 0 if rep["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
