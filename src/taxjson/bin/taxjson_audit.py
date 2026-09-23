#!/usr/bin/env python3
"""
taxjson_audit.py

The authoritative justification of every capital-gain figure: one block
per taxable disposition tracing the number from the parsed broker row
to the filed gain, with every transformation shown and cross-checked.

For each disposition in scope the block shows:

  SOURCE ROW(S)     the parsed broker row(s) the disposition came
                    from, joined by the content-hash transaction id —
                    nominal currency, original ticker, source file.
  TICKER MAPPING    what renamed the symbol (ticker.map rule, corp
                    action) — or "unchanged".
  FX CONVERSION     the exact rate the pipeline applied (same file,
                    same date-resolution rules, provenance named:
                    exact date / carried forward / default), with
                    nominal × rate recomputed and compared against
                    the base-book row to the cent.
  DISPOSITION       proceeds, cost basis, raw gain — the engine's own
                    numbers from a trace=True re-run on the same
                    books the pipeline used.
  WASH / SUPERFICIAL LOSS
                    the denied amount, the replacement lot(s) resolved
                    to their rows, and where the denied loss went.
  PIPELINE TIE-OUT  the same disposition looked up by id in the
                    pipeline's saved gains file(s); gain and
                    disallowed must agree to the cent.
  POOL TRACE        the full ACB/FIFO calculation trace feeding the
                    disposition (tt-style, from the engine).

A RECONCILIATION footer totals the events and counts every
cross-check. Exit 1 when any tie-out or FX cross-check fails — the
audit's whole claim is that these numbers agree, so a mismatch is a
finding, not a formatting problem.

This tool re-DERIVES and cross-checks; it never writes books. The
project-level front door is `taxjson audit`, which resolves every
path below from the project.
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.core import get_tax_rules, load_transactions
from taxjson.lib.pipeline import prepare_books
from taxjson.lib.trace_format import render_gain_block

# Money agreement threshold for every cross-check in this tool: the
# books are kept to the cent, so half a cent of float drift is the
# honest line between "ties" and "differs".
TIE = 0.005 + 1e-9


# ---------------------------------------------------------------------------
# Loading / indexing
# ---------------------------------------------------------------------------

def _load_doc(path: Path) -> Dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"taxjson-audit: cannot read {path}: {e}")
    if isinstance(doc, list):
        # Bare transaction lists are legal input to every other tool
        # (load_transactions accepts them) — normalize instead of
        # AttributeError-ing on .get (2026-09 audit).
        return {"transactions": doc}
    return doc


def _source_label(path: Path, meta: Dict[str, Any]) -> str:
    """Human name for a parsed-source file: the original input files
    when the parser recorded them, else the work-file name."""
    files = meta.get("input_files") or []
    names = ", ".join(Path(f).name for f in files[:4])
    if len(files) > 4:
        names += f", +{len(files) - 4} more"
    return f"{path.name}" + (f" ({names})" if names else "")


def build_source_index(paths: List[Path]) -> Dict[str, List[Dict[str, Any]]]:
    """id -> [{label, row}] over every parsed-source file. A row
    deduplicated out of the books can legitimately appear in several
    files; all appearances are kept so the block can say so."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    for p in paths:
        doc = _load_doc(p)
        label = _source_label(p, doc.get("metadata") or {})
        for row in doc.get("transactions") or []:
            rid = row.get("id")
            if rid:
                index.setdefault(rid, []).append(
                    {"label": label, "row": row})
    return index


def build_check_index(paths: List[Path]) -> Tuple[
        Dict[str, List[Dict[str, Any]]], List[str]]:
    """id -> [gain records] from the pipeline's saved gains files (the
    numbers the reports were rendered from). US FIFO can emit several
    lot records per sell id, hence the list."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    labels: List[str] = []
    for p in paths:
        doc = _load_doc(p)
        labels.append(p.name)
        for g in doc.get("transactions") or []:
            if not g.get("qty") or "gain" not in g:
                continue
            # Same scope as the engine side (in_scope): income records
            # (PIL carries qty and gain 0.0) are not dispositions —
            # without this symmetric exclusion the reverse sweep
            # flagged every saved PIL row as "fabricated".
            if g.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            gid = g.get("id")
            if gid:
                index.setdefault(gid, []).append(g)
    return index, labels


def rate_with_provenance(currency: str, date_str: str,
                         history: Dict[str, Dict[str, Decimal]],
                         default_rate: Decimal
                         ) -> Tuple[Decimal, str, Optional[str]]:
    """The converter's exact date-resolution rules (exact date, else
    walk back up to 5 days, else the default rate), returning WHERE
    the rate came from as well: (rate, kind, source_date). kind is
    'exact' | 'carried' | 'default'."""
    from datetime import datetime, timedelta
    curr_history = history.get(currency) or {}
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return default_rate, "default", None
    for i in range(6):
        d = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
        if d in curr_history:
            return curr_history[d], ("exact" if i == 0 else "carried"), d
    return default_rate, "default", None


# ---------------------------------------------------------------------------
# Per-event assembly
# ---------------------------------------------------------------------------

def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def _row_line(row: Dict[str, Any]) -> str:
    """One-line rendering of a parsed transaction row, nominal values.
    BUYSELL is shown as the verb a reader scans for (BUY/SELL)."""
    qty = float(row.get("quantity") or 0.0)
    action = str(row.get("action") or "?")
    if action == "BUYSELL":
        action = "SELL" if qty < 0 else "BUY"
    bits = [str(row.get("date") or "?"), action,
            f"{abs(qty):g}" if qty else "",
            str(row.get("symbol") or "?")]
    price = float(row.get("price") or 0.0)
    if price:
        bits.append(f"@ {price:,.2f}")
    cur = row.get("currency") or "?"
    net = float(row.get("net_amount") or 0.0)
    fee = float(row.get("fee") or 0.0) + float(row.get("commission") or 0.0)
    line = " ".join(b for b in bits if b) + f" — net {_fmt(net)} {cur}"
    if fee:
        line += f", fee {fee:,.2f}"
    if row.get("corp_event_id"):
        line += f"  [corp event {row['corp_event_id']}]"
    return line


def _map_note(raw_sym: str, base_sym: str, tmap) -> str:
    """Name what renamed the symbol, when a ticker.map is on hand."""
    if raw_sym == base_sym:
        return "unchanged"
    if tmap is not None:
        for attr, rule in (("glob", "GLOBAL"), ("tobase", "TOBASE"),
                           ("journal", "JOURNAL")):
            table = getattr(tmap, attr, {}) or {}
            for src, dst in table.items():
                if src.upper() == raw_sym.upper() \
                        and dst.upper() == base_sym.upper():
                    return f"{raw_sym} -> {base_sym}  (ticker.map {rule})"
    return f"{raw_sym} -> {base_sym}"


def build_event(g: Dict[str, Any], base_index: Dict[str, Dict[str, Any]],
                source_index: Dict[str, List[Dict[str, Any]]],
                check_index: Dict[str, List[Dict[str, Any]]],
                fx_history: Dict[str, Dict[str, Decimal]],
                default_rate: Decimal, base_currency: str,
                tmap, replacement_lookup: Dict[str, Dict[str, Any]],
                checks_supplied: bool) -> Dict[str, Any]:
    """Assemble the full provenance record for one engine disposition."""
    gid = g.get("id") or ""
    ev: Dict[str, Any] = {
        "id": gid, "symbol": g.get("symbol"), "date": g.get("date"),
        "date_settle": g.get("date_settle"), "account": g.get("account"),
        "qty": g.get("qty"), "direction": g.get("direction"),
        "proceeds": g.get("proceeds"), "cost": g.get("cost"),
        "gain": g.get("gain"), "raw_gain": g.get("raw_gain"),
        "disallowed_amount": g.get("disallowed_amount"),
        "permanently_disallowed": g.get("permanently_disallowed"),
        "is_wash_sale": bool(g.get("is_wash_sale")),
        "term": g.get("term"), "days_held": g.get("days_held"),
        "warnings": [], "failures": [],
    }

    # --- source rows (nominal) --------------------------------------
    hits = source_index.get(gid) or []
    ev["sources"] = [{"file": h["label"], "row": h["row"]} for h in hits]
    if not hits:
        ev["warnings"].append(
            "no parsed source row found for this id — the disposition "
            "cannot be traced to a broker file (synthesized row, or "
            "stale work/ artifacts).")

    raw = hits[0]["row"] if hits else None

    # --- ticker mapping ----------------------------------------------
    base_row = base_index.get(gid)
    ev["base_row"] = base_row
    if raw is not None and base_row is not None:
        ev["mapping"] = _map_note(str(raw.get("symbol") or ""),
                                  str(base_row.get("symbol") or ""), tmap)
    elif base_row is None:
        ev["mapping"] = None
        ev["warnings"].append(
            "id not present in the engine's base books — trace and "
            "book row cannot be shown (should not happen).")
    else:
        ev["mapping"] = None

    # --- FX conversion ------------------------------------------------
    fx: Optional[Dict[str, Any]] = None
    if raw is not None and base_row is not None:
        cur = str(raw.get("currency") or "").upper()
        if cur and cur != base_currency:
            # The converter prices on the settlement date when there is
            # one — mirror it exactly.
            rate_date = str(raw.get("date_settle") or raw.get("date") or "")
            rate, kind, src_date = rate_with_provenance(
                cur, rate_date, fx_history, default_rate)
            nominal = float(raw.get("net_amount") or 0.0)
            book = float(base_row.get("net_amount") or 0.0)
            computed = nominal * float(rate)
            # Tolerance: the converter multiplied the same floats, so
            # only re-derivation slack (repr round-trip) is allowed.
            ties = abs(computed - book) <= max(TIE, abs(book) * 1e-9)
            fx = {"from": cur, "to": base_currency,
                  "rate": float(rate), "rate_kind": kind,
                  "rate_date": rate_date, "rate_source_date": src_date,
                  "nominal_net": nominal, "computed_net": computed,
                  "book_net": book, "ties": ties}
            if kind == "default":
                ev["warnings"].append(
                    f"FX fell back to the default rate {float(rate):g} "
                    f"— no {cur} rate within 5 days of {rate_date} in "
                    f"the rates file.")
            if not ties:
                ev["failures"].append(
                    f"FX cross-check FAILED: {_fmt(nominal)} {cur} x "
                    f"{float(rate):g} = {_fmt(computed)} but the base "
                    f"book row carries {_fmt(book)} {base_currency}.")
        else:
            fx = {"from": cur or base_currency, "to": base_currency,
                  "native": True}
    ev["fx"] = fx

    # --- wash replacements resolved to rows ---------------------------
    reps = []
    for rid in g.get("replacement_lot_ids") or []:
        rrow = replacement_lookup.get(rid)
        reps.append({"id": rid, "row": rrow})
    ev["replacements"] = reps

    # --- pipeline tie-out ---------------------------------------------
    checks = check_index.get(gid) or []
    tie: Dict[str, Any] = {"records": len(checks)}
    if checks:
        # US FIFO can split one sell across lots; compare against the
        # per-id AGGREGATE, which is what the reports sum anyway.
        cg = sum(float(c.get("gain") or 0.0) for c in checks)
        cd = sum(float(c.get("disallowed_amount") or 0.0) for c in checks)
        # The engine re-run also yields one record per lot; the caller
        # aggregates before calling for multi-record ids, so g here is
        # already the aggregate view.
        eg = float(g.get("gain") or 0.0)
        ed = float(g.get("disallowed_amount") or 0.0)
        tie.update({"pipeline_gain": cg, "pipeline_disallowed": cd,
                    "ties": abs(cg - eg) <= TIE and abs(cd - ed) <= TIE})
        if not tie["ties"]:
            ev["failures"].append(
                f"PIPELINE TIE-OUT FAILED: engine re-run gain "
                f"{_fmt(eg)} / disallowed {_fmt(ed)} vs saved gains "
                f"file {_fmt(cg)} / {_fmt(cd)} — the books changed "
                f"since the last `taxjson run`, or the audit was "
                f"invoked with different inputs.")
    else:
        tie["ties"] = None
        if checks_supplied:
            ev["warnings"].append(
                "disposition not found in the pipeline gains file(s) — "
                "cannot tie out (books changed since the last run?).")
    ev["tie_out"] = tie

    ev["trace"] = list(g.get("trace") or [])
    return ev


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# ANSI palette — used only when main() decides color is on (tty, no
# NO_COLOR, no --no-color; same gate as taxjson-sum-gains).
_C = {"h": "\033[1m", "dim": "\033[2m", "lbl": "\033[36m",
      "ok": "\033[32m", "bad": "\033[1;31m", "warn": "\033[33m",
      "reset": "\033[0m"}


def _mk_paint(use_color: bool):
    def paint(text: str, *keys: str) -> str:
        if not use_color or not keys:
            return text
        return "".join(_C[k] for k in keys) + text + _C["reset"]
    return paint


_GUT = 13          # section-label gutter width


def _sec(out, paint, label: str, first: str = ""):
    """Start a section: cyan label in a fixed gutter, first line after."""
    out.append(f"  {paint(label.ljust(_GUT), 'lbl')}{first}")


def _cont(out, text: str):
    """Continuation line inside the current section."""
    out.append(f"  {' ' * _GUT}{text}")



def render_event(ev: Dict[str, Any], n: int, total: int,
                 country: str, show_trace: bool,
                 use_color: bool = False) -> List[str]:
    W = 86
    paint = _mk_paint(use_color)
    OK = paint("\u2713", "ok")
    BAD = paint("\u2717 MISMATCH", "bad")
    out: List[str] = []

    qty = float(ev.get("qty") or 0.0)
    verb = ("WRITE" if ev.get("grant") else "DEEMED GAIN" if ev.get("deemed")
            else "COVER" if (ev.get("direction") == "SHORT") else "SELL")
    settle = (f" (settle {ev['date_settle']})"
              if ev.get("date_settle")
              and ev["date_settle"] != ev["date"] else "")
    head = (f"EVENT {n}/{total}   {verb} {qty:g} {ev['symbol']}   "
            f"{ev['date']}{settle}   {ev.get('account') or '?'}"
            + (" (short)" if ev.get("direction") == "SHORT" else ""))
    out.append(paint("\u2550" * W, "dim"))
    out.append(paint(head, "h")
               + paint(f"   #{ev['id'][:12]}", "dim"))
    out.append(paint("\u2500" * W, "dim"))

    # ---- source ------------------------------------------------------
    if ev["sources"]:
        seen = []
        for src in ev["sources"]:
            if src["file"] in seen:
                continue
            seen.append(src["file"])
            if len(seen) == 1:
                _sec(out, paint, "SOURCE", paint(src["file"], "dim"))
            else:
                _cont(out, paint(src["file"], "dim"))
            _cont(out, _row_line(src["row"]))
        if len(seen) > 1:
            _cont(out, paint(f"(row appears in {len(seen)} source "
                             f"files — deduplicated to one book "
                             f"entry)", "dim"))
    else:
        _sec(out, paint, "SOURCE",
             paint("(none found — see WARNINGS)", "warn"))

    # ---- mapping -----------------------------------------------------
    if ev.get("mapping") and ev["mapping"] != "unchanged":
        _sec(out, paint, "MAPPING",
             ev["mapping"].replace("->", "\u2192"))

    # ---- fx ----------------------------------------------------------
    fx = ev.get("fx")
    if fx:
        if fx.get("native"):
            _sec(out, paint, "CURRENCY",
                 paint(f"native {fx['to']} — no conversion", "dim"))
        else:
            kind = {"exact": f"exact rate for {fx['rate_date']}",
                    "carried": f"carried forward from "
                               f"{fx['rate_source_date']} to "
                               f"{fx['rate_date']}",
                    "default": f"DEFAULT RATE — no rate on file near "
                               f"{fx['rate_date']}"}[fx["rate_kind"]]
            _sec(out, paint, "FX",
                 f"{fx['from']}\u2192{fx['to']} @ {fx['rate']:.5f}  "
                 + paint(f"({kind})", "dim"))
            _cont(out, f"{_fmt(fx['nominal_net'])} {fx['from']} "
                       f"\u00d7 {fx['rate']:.5f} = "
                       f"{_fmt(fx['computed_net'])} {fx['to']}"
                       f"  =  base book "
                       + (OK if fx["ties"] else BAD))

    # ---- disposition -------------------------------------------------
    rule = ("ACB pool — ITA s.47, blended across taxable accounts"
            if country not in ("us", "usa")
            else "FIFO lots — IRC, wash sale \u00a71091")
    _sec(out, paint, "DISPOSITION", paint(rule, "dim"))

    def money(label, v, mark=""):
        _cont(out, f"{label:<15}{_fmt(v):>16}{mark}")

    def per_share(v):
        # (1,000 sh @ 41.0500) — the ACB/share (or sale price/share)
        # the reader wants to sanity-check against a statement.
        if qty and abs(qty) > 1e-9:
            return paint(f"   ({abs(qty):,g} sh @ "
                         f"{abs(v) / abs(qty):,.4f})", "dim")
        return ""

    _pr = float(ev.get("proceeds") or 0)
    _cb = float(ev.get("cost") or 0)
    money("proceeds", _pr, per_share(_pr))
    money("cost basis", _cb, per_share(_cb))

    # ---- wash / superficial loss ------------------------------------
    dis = float(ev.get("disallowed_amount") or 0.0)
    perm = float(ev.get("permanently_disallowed") or 0.0)
    if ev.get("is_wash_sale") or dis > TIE:
        title = ("superficial loss — s.40(2)(g)"
                 if country not in ("us", "usa")
                 else "wash sale — \u00a71091")
        raw_g = float(ev.get("raw_gain")
                      if ev.get("raw_gain") is not None
                      else ev.get("gain") or 0)
        money("raw gain", raw_g)
        _sec(out, paint, "WASH", paint(title, "warn"))
        money("denied", dis,
              paint(f"   (PERMANENT {_fmt(perm)})", "warn")
              if perm > TIE else "")
        for r in ev.get("replacements") or []:
            row = r.get("row")
            if row:
                _cont(out, f"replacement {_row_line(row)} "
                           + paint(f"({r['id'][:8]})", "dim"))
            else:
                _cont(out, f"replacement {r['id'][:12]} "
                           + paint("(row not in supplied books)",
                                   "warn"))
        if perm > TIE:
            dest = ("denied loss PERMANENTLY lost to the "
                    "registered-account replacement (no ACB bump "
                    "outside a taxable account)"
                    if country not in ("us", "usa") else
                    "denied loss PERMANENTLY lost (replacement in a "
                    "sheltered account — Rev. Rul. 2008-5)")
        else:
            dest = ("denied loss \u2192 replacement lot's ACB "
                    "(s.53(1)(f))" if country not in ("us", "usa")
                    else "denied loss \u2192 replacement lot's basis; "
                         "holding period tacks (\u00a71223(3))")
        import textwrap as _tw
        for _ln in _tw.wrap(dest, width=W - _GUT - 2):
            _cont(out, paint(_ln, "dim"))
        money("allowed gain", float(ev.get("gain") or 0))
    else:
        money("gain", float(ev.get("gain") or 0))

    # ---- tie-out -----------------------------------------------------
    tie = ev.get("tie_out") or {}
    if tie.get("ties") is not None:
        _sec(out, paint, "TIE-OUT",
             f"saved gains file: gain {_fmt(tie['pipeline_gain'])}, "
             f"disallowed {_fmt(tie['pipeline_disallowed'])}  "
             + (OK if tie["ties"] else BAD))

    import textwrap as _tw2
    for w in ev.get("warnings") or []:
        for _k, _ln in enumerate(_tw2.wrap(f"WARNING: {w}",
                                           width=W - 2,
                                           subsequent_indent="  ")):
            out.append("  " + paint(_ln, "warn"))
    for f in ev.get("failures") or []:
        for _ln in _tw2.wrap(f"FAILED: {f}", width=W - 2,
                             subsequent_indent="  "):
            out.append("  " + paint(_ln, "bad"))

    if show_trace and ev.get("trace"):
        _sec(out, paint, "TRACE",
             paint("engine pool history, base currency", "dim"))
        for ln in render_gain_block({**ev, "trace": ev["trace"]},
                                    align=True):
            out.append(paint(ln, "dim") if ln.startswith("# =")
                       else ln)
    return out


def render_reconciliation(events: List[Dict[str, Any]],
                          check_labels: List[str],
                          checks_supplied: bool,
                          use_color: bool = False) -> List[str]:
    W = 86
    paint = _mk_paint(use_color)
    OK = paint("\u2713", "ok")
    total_gain = sum(float(e.get("gain") or 0) for e in events)
    total_dis = sum(float(e.get("disallowed_amount") or 0) for e in events)
    tied = sum(1 for e in events if (e["tie_out"].get("ties") is True))
    untied = sum(1 for e in events if (e["tie_out"].get("ties") is False))
    nocheck = sum(1 for e in events if e["tie_out"].get("ties") is None)
    fx_ok = sum(1 for e in events
                if e.get("fx") and not e["fx"].get("native")
                and e["fx"].get("ties"))
    fx_bad = sum(1 for e in events
                 if e.get("fx") and not e["fx"].get("native")
                 and not e["fx"].get("ties"))
    native = sum(1 for e in events if e.get("fx")
                 and e["fx"].get("native"))
    sourced = sum(1 for e in events if e.get("sources"))
    n = len(events)

    def mark(bad_count, all_ok):
        if bad_count:
            return paint("\u2717", "bad")
        return OK if all_ok else ""

    out = [paint("\u2550" * W, "dim"),
           paint("RECONCILIATION", "h")
           + (paint(f"   {', '.join(check_labels)}", "dim")
              if check_labels else ""),
           paint("\u2500" * W, "dim"),
           f"  events audited     {n:>14,}",
           f"  total gain         {_fmt(total_gain):>14}",
           f"  total disallowed   {_fmt(total_dis):>14}",
           f"  source rows        {sourced:>7,}/{n:,} traced to a "
           f"parsed broker row  " + mark(n - sourced, sourced == n),
           f"  fx cross-check     {fx_ok:>7,} tied, {fx_bad:,} "
           f"mismatched, {native:,} native  "
           + mark(fx_bad, fx_bad == 0)]
    if checks_supplied:
        out.append(f"  pipeline tie-out   {tied:>7,} tied, {untied:,} "
                   f"MISMATCHED, {nocheck:,} not found  "
                   + mark(untied + nocheck,
                          untied == 0 and nocheck == 0))
    else:
        out.append("  pipeline tie-out   " + paint(
            "(no gains files supplied — engine re-run stands alone)",
            "dim"))
    out.append(paint("\u2550" * W, "dim"))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Authoritative per-disposition audit: broker row -> "
                    "ticker map -> FX -> pool -> gain, every step "
                    "cross-checked. Exit 1 when any check fails.")
    p.add_argument("--country", default="canada")
    p.add_argument("--year", type=int, help="Tax year filter.")
    p.add_argument("--tax-date", choices=["trade", "settle"], default=None)
    p.add_argument("--base", required=True,
                   help="Engine input books (mapped + converted) — the "
                        "same file the pipeline's gains stage consumed.")
    p.add_argument("--sheltered", help="sheltered_base.json for the "
                                       "wash/superficial-loss context.")
    p.add_argument("--affiliated")
    p.add_argument("--incomplete-history", metavar="FILE")
    p.add_argument("--per-account-basis", action="store_true")
    p.add_argument("--cross-asset", action="store_true")
    p.add_argument("--option-premium-timing", choices=["grant", "close"], default="close")
    p.add_argument("--option-grant-since", type=int, default=None)
    p.add_argument("--option-buyback-wash", action="store_true")
    p.add_argument("--no-wash", action="store_true",
                   help="Disable wash detection (US crypto: digital "
                        "assets are property, not securities — §1091 "
                        "does not reach them).")
    p.add_argument("--rates", help="FX history the pipeline converted "
                                   "with (work/to_base.csv).")
    p.add_argument("--base-currency",
                   help="Report currency (inferred from the base books "
                        "when omitted).")
    p.add_argument("--default-rate", type=float, default=1.35)
    p.add_argument("--map", dest="ticker_map", help="ticker.map for "
                                                    "naming rename rules.")
    p.add_argument("--source", action="append", default=[],
                   help="Parsed-source JSON (repeatable) — the nominal-"
                        "currency rows dispositions are joined back to.")
    p.add_argument("--check", action="append", default=[],
                   help="Pipeline gains JSON (repeatable) to tie out "
                        "against.")
    p.add_argument("--symbol", action="append", default=[],
                   help="Filter: symbol prefix (repeatable).")
    p.add_argument("--id", dest="gain_id", help="Filter: tx id prefix.")
    p.add_argument("--date", help="Filter: disposition date.")
    p.add_argument("--account", help="Filter: account name.")
    p.add_argument("--summary", action="store_true",
                   help="One line per event instead of full blocks.")
    p.add_argument("--no-trace", action="store_true",
                   help="Omit the engine pool traces.")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI color (auto-on for terminals; "
                        "NO_COLOR is honored).")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def _norm_country(c: str) -> str:
    c = (c or "").strip().lower()
    return "usa" if c in ("us", "usa") else "canada"


def main(argv=None) -> int:
    args = parse_args(argv)
    country = _norm_country(args.country)
    if args.tax_date is None:
        args.tax_date = "trade" if country == "usa" else "settle"

    base_path = Path(args.base)
    transactions = load_transactions(base_path)
    sheltered = (load_transactions(Path(args.sheltered))
                 if args.sheltered else [])
    affiliated = (load_transactions(Path(args.affiliated))
                  if args.affiliated else [])

    # Raw dict twins of the engine input, indexed by id (the engine
    # consumes TaxTransaction objects; the audit wants the row dicts).
    base_doc = _load_doc(base_path)
    base_index: Dict[str, Dict[str, Any]] = {}
    for row in base_doc.get("transactions") or []:
        rid = row.get("id")
        if rid and rid not in base_index:
            base_index[rid] = row
    replacement_lookup = dict(base_index)
    if args.sheltered:
        for row in (_load_doc(Path(args.sheltered))
                    .get("transactions") or []):
            rid = row.get("id")
            if rid and rid not in replacement_lookup:
                replacement_lookup[rid] = row

    base_currency = (args.base_currency
                     or next((str(r.get("currency"))
                              for r in base_doc.get("transactions") or []
                              if r.get("currency")), "CAD")).upper()

    fx_history: Dict[str, Dict[str, Decimal]] = {}
    if args.rates:
        from taxjson.bin.taxjson_convert_currency import load_exchange_rates
        fx_history = load_exchange_rates(Path(args.rates), base_currency)

    tmap = None
    if args.ticker_map:
        try:
            from taxjson.bin.taxjson_ticker_map import load_map_file
            tmap = load_map_file(Path(args.ticker_map))
        except Exception as e:
            print(f"taxjson-audit: warning: could not read ticker.map "
                  f"({e}) — rename rules will not be named.",
                  file=sys.stderr)

    source_index = build_source_index([Path(s) for s in args.source])
    check_index, check_labels = build_check_index(
        [Path(c) for c in args.check])

    # Same load-side preprocessing as the pipeline and explain, so the
    # audit cannot contradict the .sum it justifies.
    transactions, sheltered, affiliated, _ = prepare_books(
        transactions, sheltered, affiliated, taxable=False,
        incomplete_history=(Path(args.incomplete_history)
                            if args.incomplete_history else None),
        phantom_hint=False)

    rules = get_tax_rules(country)
    kwargs: Dict[str, Any] = dict(
        sheltered_transactions=sheltered,
        affiliated_transactions=affiliated,
        cross_asset=args.cross_asset, trace=True,
        detect_wash_sales=not args.no_wash)
    if country == "usa":
        kwargs["per_account_basis"] = args.per_account_basis
    else:
        kwargs["option_premium_timing"] = args.option_premium_timing
        kwargs["option_grant_since"] = args.option_grant_since
        kwargs["option_buyback_loss_superficial"] = args.option_buyback_wash
    from taxjson.lib.core import AmbiguousTransferDateError
    try:
        results = rules.compute_gains(transactions, **kwargs)
    except AmbiguousTransferDateError as e:
        sys.exit(f"taxjson-audit: {e}")

    date_key = "date_settle" if args.tax_date == "settle" else "date"

    def in_scope(g) -> bool:
        if g.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU") \
                or not g.get("qty") or "gain" not in g:
            return False
        eff = g.get(date_key) or g.get("date") or ""
        if args.year and not eff.startswith(str(args.year)):
            return False
        if args.date and eff != args.date:
            return False
        if args.symbol and not any(
                str(g.get("symbol") or "").upper().startswith(s.upper())
                for s in args.symbol):
            return False
        if args.gain_id and not (g.get("id") or "").startswith(
                args.gain_id):
            return False
        if args.account and (g.get("account") or "") != args.account:
            return False
        return True

    gains = [g for g in results["transactions"] if in_scope(g)]

    # US FIFO: one sell id can produce several lot records. The audit
    # block is per taxable EVENT (the sell); aggregate the lots and
    # keep each lot's trace.
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for g in gains:
        gid = g.get("id") or f"?{len(order)}"
        if gid not in by_id:
            order.append(gid)
        by_id.setdefault(gid, []).append(g)

    merged: List[Dict[str, Any]] = []
    for gid in order:
        lots = by_id[gid]
        if len(lots) == 1:
            merged.append(lots[0])
            continue
        agg = dict(lots[0])
        agg["qty"] = sum(float(l.get("qty") or 0) for l in lots)
        for f in ("gain", "raw_gain", "proceeds", "cost",
                  "disallowed_amount", "permanently_disallowed"):
            agg[f] = sum(float(l.get(f) or 0) for l in lots)
        agg["is_wash_sale"] = any(l.get("is_wash_sale") for l in lots)
        agg["replacement_lot_ids"] = [
            r for l in lots for r in (l.get("replacement_lot_ids") or [])]
        agg["trace"] = [ln for l in lots for ln in (l.get("trace") or [])]
        agg["lots"] = len(lots)
        merged.append(agg)

    merged.sort(key=lambda g: (g.get(date_key) or g.get("date") or "",
                               g.get("symbol") or ""))

    events = [build_event(g, base_index, source_index, check_index,
                          fx_history, Decimal(str(args.default_rate)),
                          base_currency, tmap, replacement_lookup,
                          checks_supplied=bool(args.check))
              for g in merged]

    # The tie-out must catch OMISSIONS and FABRICATIONS, not just
    # per-event drift (2026-09 audit: a check file missing a whole
    # disposition — or carrying an extra invented one — passed with
    # exit 0, which is exactly the stale/corrupted-book failure mode
    # this tool exists to catch). Sweep both directions when the
    # filters would not explain the difference: symbol/date/id/account
    # filters legitimately scope the engine side down, so the sweep
    # runs only when NO row filter is active.
    reconciliation_failures: List[str] = []
    if args.check and not (args.symbol or args.gain_id or args.date
                           or args.account):
        engine_ids = {e["id"] for e in events}

        def _chk_in_scope(c) -> bool:
            eff = (c.get(date_key) or c.get("date") or "")
            return not args.year or eff.startswith(str(args.year))

        for gid, recs in sorted(check_index.items()):
            if gid in engine_ids:
                continue
            scoped = [c for c in recs if _chk_in_scope(c)]
            if not scoped:
                continue
            _g = sum(float(c.get("gain") or 0.0) for c in scoped)
            reconciliation_failures.append(
                f"check file carries disposition id {gid[:12]} "
                f"({scoped[0].get('symbol')} {scoped[0].get('date')}, "
                f"gain {_g:,.2f}) that the engine re-run did not "
                f"produce — fabricated or double-counted record.")
        for e in events:
            if e["tie_out"].get("ties") is None and args.check:
                reconciliation_failures.append(
                    f"engine disposition {e['id'][:12]} "
                    f"({e['symbol']} {e['date']}, gain "
                    f"{float(e.get('gain') or 0):,.2f}) is MISSING "
                    f"from the check file(s) — stale or truncated "
                    f"saved books.")
        eng_total = sum(float(e.get("gain") or 0) for e in events)
        chk_total = sum(float(c.get("gain") or 0.0)
                        for recs in check_index.values()
                        for c in recs if _chk_in_scope(c))
        if abs(eng_total - chk_total) > TIE:
            reconciliation_failures.append(
                f"in-scope totals do not tie: engine "
                f"{eng_total:,.2f} vs check files {chk_total:,.2f}.")

    failed = any(e["failures"] for e in events) \
        or bool(reconciliation_failures)

    if args.json:
        slim = []
        for e in events:
            d = dict(e)
            d.pop("base_row", None)
            slim.append(d)
        json.dump({"base_currency": base_currency, "country": country,
                   "events": slim,
                   "total_gain": round(sum(float(e.get("gain") or 0)
                                           for e in events), 2),
                   "total_disallowed": round(
                       sum(float(e.get("disallowed_amount") or 0)
                           for e in events), 2),
                   "reconciliation_failures": reconciliation_failures,
                   "failed": failed},
                  sys.stdout, indent=2, default=str)
        print()
        return 1 if failed else 0

    use_color = (not args.no_color and sys.stdout.isatty()
                 and not __import__("os").environ.get("NO_COLOR"))
    paint = _mk_paint(use_color)

    if args.summary:
        for i, e in enumerate(events, 1):
            flags = ""
            if e["failures"]:
                flags += "  " + paint("FAILED", "bad")
            elif e["warnings"]:
                flags += "  " + paint("warn", "warn")
            if float(e.get("disallowed_amount") or 0) > TIE:
                flags += "  " + paint(
                    f"WASH+{float(e['disallowed_amount']):.2f}",
                    "warn")
            print(f"{paint((e['id'] or '')[:10], 'dim')}  "
                  f"{e['date']}  "
                  f"{e['symbol']:<22} qty={float(e['qty'] or 0):>12,.4f} "
                  f"gain={float(e['gain'] or 0):>+14,.2f}{flags}")
    else:
        lines: List[str] = []
        for i, e in enumerate(events, 1):
            lines += render_event(e, i, len(events), country,
                                  show_trace=not args.no_trace,
                                  use_color=use_color)
            lines.append("")
        for ln in lines:
            print(ln)

    for ln in render_reconciliation(events, check_labels,
                                    bool(args.check),
                                    use_color=use_color):
        print(ln)
    for f in reconciliation_failures:
        print(f"FAILED: {f}")
    if failed:
        print("\ntaxjson-audit: CHECKS FAILED — see FAILED lines above. "
              "The usual cause is stale work/ artifacts; re-run "
              "`taxjson run` and audit again.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
