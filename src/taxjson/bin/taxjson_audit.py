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

from taxjson.lib.out import exit_text
from taxjson.lib.stage_msg import emit_line
import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.bin.taxjson_convert_currency import (LOOKBACK_DAYS,
                                                  default_rate_for,
                                                  positive_rate)
from taxjson.lib.core import (get_tax_rules, is_option_symbol,
                              load_transactions)
from taxjson.lib.country import (add_country_argument, canonical_country,
                                 refuse_foreign_flags)
from taxjson.lib.pipeline import (GainsRequest, apply_roc_record_dates,
                                  engine_options, loss_overrides_from_args,
                                  prepare_books)
from taxjson.lib.trace_format import render_report_trace

# Money agreement threshold for every cross-check in this tool: the
# books are kept to the cent, so half a cent of float drift is the
# honest line between "ties" and "differs".
TIE = 0.005 + 1e-9


# ---------------------------------------------------------------------------
# Loading / indexing
# ---------------------------------------------------------------------------

def _load_doc(path: Path) -> Dict[str, Any]:
    # Bare transaction lists are legal input to every other tool
    # (load_transactions accepts them) and are normalized; any other
    # shape — {"transactions": 5}, rows that are not objects — is a
    # one-line refusal, not a traceback (audit S042-18).
    from taxjson.lib.json_input import read_work_doc
    try:
        return read_work_doc(path)
    except (OSError, ValueError) as e:
        sys.exit(exit_text(f"taxjson-audit: cannot read {path}: {e}"))


# The source files the run writes itself rather than parses, by what
# their metadata says made them: named beside the rows traced to them
# (backlog TJ-0359).
_MADE_BY_SOURCE = {"taxjson-corp-actions": "corporate actions"}
_MADE_BY_KIND = {
    "transfer_book_value": "transfers in and in-kind moves, booked by "
                           "the run",
    "own_account_moves": "moves between your own accounts, booked by "
                         "the run",
}


def _source_label(path: Path, meta: Dict[str, Any]) -> str:
    """Human name for a parsed-source file: the original input files
    when the parser recorded them, else the work-file name; a file the
    run wrote (corporate actions, transfers in) says so."""
    # convert-tt records its .tt as `source_file` (S029-09: a
    # hand-entered row was labelled with the work file only); the
    # corp-actions stage its statements' shown names (TJ-0359).
    files = meta.get("input_files") or (
        [meta["source_file"]] if meta.get("source_file") else []) \
        or list(meta.get("statements") or [])
    names = ", ".join(Path(str(f)).name for f in files[:4])
    if len(files) > 4:
        names += f", +{len(files) - 4} more"
    what = (_MADE_BY_SOURCE.get(str(meta.get("source") or ""))
            or _MADE_BY_KIND.get(str(meta.get("kind") or "")) or "")
    if what:
        names = what + (f" from {names}" if names else "")
    return f"{path.name}" + (f" ({names})" if names else "")


# Shares per listed equity option contract (the per-share quote a
# statement prints; the engine's own 100x, core.py).
OPTION_MULTIPLIER = 100


def build_source_index(paths: List[Path]) -> Dict[str, List[Dict[str, Any]]]:
    """id -> [{label, row}] over every parsed-source file. A row
    deduplicated out of the books can legitimately appear in several
    files; all appearances are kept so the block can say so.

    A row its file gives no id (a corporate action's rows, a transfer
    in the run booked) is indexed under the id the books gave it: the
    merge computes it from the row's content (core.TaxTransaction.
    compute_id). It was skipped, so every such row read as one with no
    parsed row (backlog TJ-0359)."""
    from taxjson.lib.core import coerce_transaction_row
    index: Dict[str, List[Dict[str, Any]]] = {}
    for p in paths:
        doc = _load_doc(p)
        meta = doc.get("metadata") or {}
        label = _source_label(p, meta)
        # A converted .tt file's {id: line} (taxjson-convert-tt): the
        # line rides on the audit's copy of the row as `source_line`.
        lines = meta.get("source_lines")
        lines = lines if isinstance(lines, dict) else {}
        for i, row in enumerate(doc.get("transactions") or []):
            rid = row.get("id")
            if not rid:
                try:
                    rid = coerce_transaction_row(dict(row), i,
                                                 str(p.name)).id
                except Exception:                   # noqa: BLE001
                    rid = None      # a row the books could not load
            if rid:
                if lines.get(rid) is not None:
                    row = dict(row, source_line=lines[rid])
                index.setdefault(rid, []).append(
                    {"label": label, "row": row})
    return index


def source_hits(rid: str, source_index: Dict[str, List[Dict[str, Any]]],
                base_index: Dict[str, Dict[str, Any]]
                ) -> List[Dict[str, Any]]:
    """The parsed rows a book row came from, by its id. A row the dedup
    kept as a separate record although another file holds the same
    content (`<id>~<n>`, bin/taxjson_sort.plan_dedup) carries the plain
    id in its source file: the file the book row came from is picked."""
    hits = source_index.get(rid) or []
    if not hits and "~" in str(rid or ""):
        _b = base_index.get(rid) or {}
        _src = _b.get("source")
        # The source key keeps two files shown alike apart (A2-0159).
        _key = _b.get("source_key") or ""
        hits = [h for h in source_index.get(str(rid).split("~")[0]) or []
                if not _src or (h["row"].get("source") == _src
                                and (h["row"].get("source_key") or "")
                                == _key)]
    return hits


def money_lineage(rid: str, raw: Dict[str, Any], base_row: Dict[str, Any],
                  *, base_currency: str,
                  fx_history: Dict[str, Dict[str, Decimal]],
                  default_rate: Optional[Decimal],
                  futures_native: Optional[Dict[str, float]] = None,
                  filled_index: Optional[Dict[str, Dict[str, Any]]] = None,
                  field: str = "net_amount"
                  ) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """(fx, warnings, failures): a book row's net amount re-derived from
    the row the conversion read — the parsed broker row, or for a crypto
    book the fill stage's priced row (fill-crypto-prices values a swap
    and prices a coin-denominated fee: R1-271), the settled P/L for a
    futures fill (lib/futures.py) — times the rates file's rate when it
    is in another currency (the converter's own lookup, on the
    settlement date when there is one), and compared with the book's.

    A row already in the base currency is compared too: it was marked
    "native" with no amount compared, so a base book whose buy or sale
    amount differed from the broker's passed (backlog TJ-0333).

    `field`: the amount compared — the net amount, or a dividend's gross
    amount (row_money_field: the merge nets the withholding into a
    dividend's net amount, backlog TJ-0359)."""
    warnings: List[str] = []
    failures: List[str] = []
    pre = (filled_index or {}).get(rid)
    src = pre if pre is not None else raw
    raw_cur = str(raw.get("currency") or "").strip().upper()
    cur = str(src.get("currency") or "").strip().upper() or raw_cur
    nominal = float(src.get(field) or 0.0)
    priced_by_fill = pre is not None and (
        abs(nominal - float(raw.get(field) or 0.0)) > TIE
        or cur != raw_cur)
    book = float(base_row.get(field) or 0.0)
    native = not cur or cur == base_currency
    # The converter prices on the settlement date when there is one.
    rate_date = str(src.get("date_settle") or src.get("date")
                    or raw.get("date_settle") or raw.get("date") or "")
    fx: Dict[str, Any] = {"from": cur or base_currency, "to": base_currency}
    if native:
        rate: Optional[Decimal] = Decimal(1)
        fx["native"] = True
    else:
        rate, kind, src_date = rate_with_provenance(
            cur, rate_date, fx_history,
            # The converter's own fallback: an explicit --default-rate,
            # else none (no built-in rate).
            default_rate_for(cur, base_currency, default_rate))
        if rate is None:
            # No rate and no explicit --default-rate: the converter
            # stops on such a row, so these books cannot hold it.
            fx.update({"rate": None, "rate_kind": "missing",
                       "rate_date": rate_date})
            failures.append(
                f"FX: no {cur}->{base_currency} rate for {rate_date} "
                f"(nor in the {LOOKBACK_DAYS} days before) in the "
                f"rates file — the conversion stops on this row; "
                f"refresh the rates with `taxjson run`.")
            return fx, warnings, failures
        fx.update({"rate": float(rate), "rate_kind": kind,
                   "rate_date": rate_date, "rate_source_date": src_date})
        if kind == "default":
            warnings.append(
                f"FX fell back to the --default-rate {float(rate):g} "
                f"— no {cur} rate within {LOOKBACK_DAYS} days of "
                f"{rate_date} in the rates file.")
    notional = nominal
    settled = base_row.get("type") == "futures_settlement"
    if settled:
        # A futures fill converts its settled P/L, not the notional the
        # broker row carries (lib/futures.py).
        nominal = (futures_native or {}).get(rid, float("nan"))
    computed = nominal * float(rate)
    # Tolerance: the converter multiplied the same floats, so only
    # re-derivation slack (repr round-trip) is allowed.
    ties = abs(computed - book) <= max(TIE, abs(book) * 1e-9)
    fx.update({"priced_by_fill": priced_by_fill, "nominal_net": nominal,
               "computed_net": computed, "book_net": book, "ties": ties})
    if settled:
        fx["futures_settlement"] = True
        fx["notional"] = notional
    if not ties:
        _what = "" if field == "net_amount" else \
            f" ({field.replace('_', ' ')})"
        if native:
            failures.append(
                f"AMOUNT cross-check FAILED{_what}: the "
                f"{'priced' if pre is not None else 'parsed'} row "
                f"carries {_fmt(nominal)} {cur or base_currency} but the "
                f"base book row carries {_fmt(book)} {base_currency} — "
                f"no conversion applies to a {base_currency} row.")
        else:
            failures.append(
                f"FX cross-check FAILED{_what}: {_fmt(nominal)} {cur} x "
                f"{float(rate):g} = {_fmt(computed)} but the base "
                f"book row carries {_fmt(book)} {base_currency}.")
    return fx, warnings, failures


# The trades a pool check reads (pool_trade_check): the purchases and
# sales of a security, an option's assignment (CA-ACB-01 / US-BASIS-01).
POOL_TRADE_ACTIONS = ("BUYSELL", "ASSIGN")
# The loss rules' window after a sale (30 days, both countries: s.54
# "superficial loss", IRC §1091): a purchase in it shapes the sale's
# denied loss. One day more for a settle-date book.
_POOL_WINDOW_DAYS = 31


# A dividend's amount is its gross: the merge nets the withholding (its
# TAX row) into the dividend's net amount (taxjson_merge2.
# reconcile_dividend_tax), so the book's net is not the broker's.
_GROSS_ACTIONS = ("DIVIDEND", "DIVIDEND_IN_LIEU")
# A symbol no pool is kept for: cash rows (interest, fees, a corporate
# action's cash) on it never widen the pool checks' scope.
_NO_POOL_SYMBOLS = ("", "CASH")


def row_money_field(raw: Dict[str, Any]) -> str:
    """The amount of a row the audit compares with its parsed row: the
    net amount; a dividend's gross amount when its parsed row has one
    (_GROSS_ACTIONS; backlog TJ-0359)."""
    if str(raw.get("action") or "") in _GROSS_ACTIONS:
        try:
            if float(raw.get("gross_amount") or 0.0):
                return "gross_amount"
        except (TypeError, ValueError):
            pass
    return "net_amount"


def _row_day(row: Dict[str, Any]) -> str:
    return min(str(row.get("date") or ""),
               str(row.get("date_settle") or row.get("date") or ""))


def pool_scope(events: List[Dict[str, Any]],
               base_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """What the pool checks read (pool_trade_check, pool_other_rows_
    check): the audited sales' ids (`own`), the wash-sale /
    superficial-loss replacements they name (`replacements`), the last
    day read (`until`: the loss rule's window after the last audited
    sale), and the securities (`symbols`): the audited ones, each under
    its earlier tickers (a rename's SPLIT row carries the pool to the
    new symbol) and with the other securities of a corporate action of
    theirs (rows sharing its corp_event_id: a spin-off's parent, a
    merger's old shares). The trades booked under an earlier ticker
    were not traced (backlog TJ-0359)."""
    from datetime import date as _date, timedelta as _td
    own = set()
    syms = set()
    reps = set()
    last = ""
    for e in events:
        own.add(str(e.get("id") or ""))
        syms.add(str(e.get("symbol") or ""))
        for r in e.get("replacements") or []:
            reps.add(str(r.get("id") or ""))
        for k in ("date", "date_settle"):
            last = max(last, str(e.get(k) or ""))
    try:
        until = (_date.fromisoformat(last[:10])
                 + _td(days=_POOL_WINDOW_DAYS)).isoformat()
    except ValueError:
        until = last
    changed = True
    while changed:
        changed = False
        corp = {str(r.get("corp_event_id")) for r in base_index.values()
                if r.get("corp_event_id")
                and str(r.get("symbol") or "") in syms}
        for row in base_index.values():
            sym = str(row.get("symbol") or "")
            if sym in syms or sym.upper() in _NO_POOL_SYMBOLS:
                continue
            new = str(row.get("symbol_new") or "")
            if ((row.get("action") == "SPLIT" and new in syms)
                    or (row.get("corp_event_id")
                        and str(row.get("corp_event_id")) in corp)):
                syms.add(sym)
                changed = True
    return {"own": own, "symbols": syms, "replacements": reps,
            "until": until}


def load_dated_renames(path: Optional[Path]
                       ) -> Dict[Tuple[str, str, str], List[str]]:
    """{(old, new, date): [where, ...]} of the ticker changes the run
    booked from a `.tt` RENAME line or a dated ticker.map line
    (work/dated_events.state, lib/dated_events): the declaration a
    rename's SPLIT row came from. Empty when the file is missing or
    unreadable (the row is still named, without its line)."""
    out: Dict[Tuple[str, str, str], List[str]] = {}
    if path is None:
        return out
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for ev in (doc.get("renames") if isinstance(doc, dict) else None) or []:
        if not isinstance(ev, dict):
            continue
        where = ev.get("where") or []
        if isinstance(where, str):
            where = [where]
        out[(str(ev.get("old") or "").upper(),
             str(ev.get("new") or "").upper(),
             str(ev.get("date") or ""))] = [str(w) for w in where]
    return out


def row_origin(row: Dict[str, Any], *,
               renames: Optional[Dict[Tuple[str, str, str],
                                      List[str]]] = None,
               filled_index: Optional[Dict[str, Dict[str, Any]]] = None
               ) -> str:
    """What made a book row that no parsed broker row (or `.tt` line)
    holds, in a few words: the declaration it came from where the row
    says (a [[distributions]] table, a dated RENAME line, an OPENING
    ... cost=unknown line, a corporate action), else the stage. Rows
    with no parsed row were counted with no word on what they were
    (backlog TJ-0359)."""
    from taxjson.lib import core as _core
    rid = str(row.get("id") or "")
    desc = str(row.get("description") or "").strip()
    typ = str(row.get("type") or "")
    sym = str(row.get("symbol") or "")
    if rid.startswith("DIST-"):
        what = desc.replace(" per [[distributions]]", "").strip()
        return ("a [[distributions]] table in taxjson.toml"
                + (f" ({what})" if what else ""))
    if row.get("corp_event_id"):
        el = str(row.get("corp_election") or "")
        return (f"corporate action {row['corp_event_id']}"
                + (f" ({el})" if el else "")
                + ": no row of the corp-actions file has its id — re-run "
                  "`taxjson run`")
    ev = str(row.get("event_source") or "")
    if ev:
        new = str(row.get("symbol_new") or "")
        where = (renames or {}).get((sym.upper(), new.upper(),
                                     str(row.get("date") or "")[:10]))
        if ev == "tt":
            w = ", ".join(where or []) or str(row.get("source") or "")
            return ("a .tt RENAME line" + (f" ({w})" if w else "")
                    + ": a ticker change, booked by the run")
        if ev == "map":
            w = ", ".join(where or [])
            return ("a dated RENAME line of ticker.map"
                    + (f" ({w})" if w else "")
                    + ": a ticker change, booked by the run")
        if ev == "ib-conid":
            return ("IB shows one contract id under two symbols: a "
                    "ticker change, booked by the run")
        return f"a dated event ({ev}), booked by the run"
    if row.get("action") == "OPENING_BALANCE" \
            and desc.startswith("MISSING-HISTORY"):
        m = re.match(r"MISSING-HISTORY opening \(([^)]*)\)", desc)
        return ("an OPENING ... cost=unknown line"
                + (f" ({m.group(1)})" if m else "")
                + ": units held before the data, cost unknown (missing "
                  "history)")
    if typ == _core.TRANSFER_BOOK_VALUE_TYPE:
        return ("a transfer in, booked by the run at the book value the "
                "broker states" + (f" ({row['source']})"
                                   if row.get("source") else ""))
    if typ in _core.IN_KIND_TYPES:
        return ("an in-kind move between a taxable and a registered "
                "account, booked by the run")
    if typ == _core.LOT_MOVE_TYPE:
        return "a move between your own accounts, booked by the run"
    if typ == _core.LISTING_JOURNAL_TYPE:
        return "a journal between two listings, booked by the run"
    if rid and rid in (filled_index or {}):
        return "a row of the crypto price stage (fill-crypto-prices)"
    if row.get("source"):
        return (f"a row of {row['source']} that no parsed file holds now "
                f"— stale work/ files: re-run `taxjson run`")
    return "made by the run (the row names no stage)"


def _pool_entry(rid: str, row: Dict[str, Any]) -> Dict[str, Any]:
    e = {"id": rid, "action": row.get("action"),
         "type": row.get("type") or "", "symbol": row.get("symbol"),
         "date": row.get("date"), "quantity": row.get("quantity")}
    new = str(row.get("symbol_new") or "")
    if new and new != str(row.get("symbol") or ""):
        e["symbol_new"] = new
    return e


def pool_trade_check(events: List[Dict[str, Any]],
                     base_index: Dict[str, Dict[str, Any]],
                     source_index: Dict[str, List[Dict[str, Any]]],
                     *, origin=None, **money: Any) -> Dict[str, Any]:
    """The audited sales' cost side, traced like the sales themselves:
    every trade of an audited security in the base book (its buys, its
    earlier sales, an assignment; under an earlier ticker too:
    pool_scope) up to the loss rule's window after the last audited
    sale, and each wash-sale / superficial-loss replacement the events
    name, re-derived from its parsed row (money_lineage) and compared
    with the book's amount. A buy whose amount or conversion differed
    from the broker's changed the cost the audit re-ran and the gain it
    tied, and passed: only the sales were traced (backlog TJ-0333).

    {"checked", "tied", "untraced" (a row with no parsed row: one the
    run made), "mismatched": [{id, symbol, date, quantity, failure}],
    "untraced_rows": [{id, action, type, symbol, date, quantity,
    origin}]: what made each row with no parsed row (`origin`, a
    callable row -> text, default row_origin; backlog TJ-0359)}."""
    origin = origin or row_origin
    scope = pool_scope(events, base_index)
    own, syms = scope["own"], scope["symbols"]
    reps, until = scope["replacements"], scope["until"]
    out: Dict[str, Any] = {"checked": 0, "tied": 0, "untraced": 0,
                           "mismatched": [], "untraced_rows": []}
    for rid, row in base_index.items():
        if rid in own:
            continue
        if rid not in reps:
            if (row.get("action") not in POOL_TRADE_ACTIONS
                    or str(row.get("symbol") or "") not in syms):
                continue
            if _row_day(row) > until:
                continue
        hits = source_hits(rid, source_index, base_index)
        if not hits:
            out["untraced"] += 1
            out["untraced_rows"].append(
                {**_pool_entry(rid, row), "origin": origin(row)})
            continue
        _fx, _w, fails = money_lineage(rid, hits[0]["row"], row, **money)
        out["checked"] += 1
        if fails:
            out["mismatched"].append({
                "id": rid, "symbol": row.get("symbol"),
                "date": row.get("date"),
                "quantity": row.get("quantity"), "failure": fails[0]})
        else:
            out["tied"] += 1
    return out


def pool_other_rows_check(events: List[Dict[str, Any]],
                          base_index: Dict[str, Dict[str, Any]],
                          source_index: Dict[str, List[Dict[str, Any]]],
                          *, engine_rows=(), origin=None,
                          **money: Any) -> Dict[str, Any]:
    """The audited securities' rows that are not trades — return of
    capital and the other cost adjustments, reinvested distributions,
    splits and ticker changes, corporate-action rows, transfers,
    openings, and the securities' dividends, withholding and fees —
    traced as their trades are (pool_trade_check, the same securities
    and window: pool_scope): each to its parsed broker row or `.tt`
    line, its amount re-derived (money_lineage on row_money_field) and
    compared with the book's; a row with no parsed row is named with
    what made it (`origin`, default row_origin). `engine_rows`: rows
    the computation adds to the books (an OPENING ... cost=unknown
    line's units, pipeline.prepare_books) — none has a parsed row. Only
    the trades were traced (backlog TJ-0359).

    {"checked", "tied", "untraced", "mismatched": [{id, action, symbol,
    date, quantity, failure}], "rows": [{id, action, type, symbol,
    date, quantity, [symbol_new], field, amount, ties (true / false /
    null: no parsed row), source (the parsed file) and line (a `.tt`
    line) | origin, [failure]}]}."""
    origin = origin or row_origin
    scope = pool_scope(events, base_index)
    own, syms = scope["own"], scope["symbols"]
    reps, until = scope["replacements"], scope["until"]
    out: Dict[str, Any] = {"checked": 0, "tied": 0, "untraced": 0,
                           "mismatched": [], "rows": []}
    rows = [(rid, row) for rid, row in base_index.items()
            if row.get("action") not in POOL_TRADE_ACTIONS]
    rows += [(str(r.get("id") or ""), r) for r in engine_rows
             if str(r.get("id") or "") not in base_index]
    for rid, row in rows:
        if rid in own or rid in reps:
            continue
        if str(row.get("symbol") or "") not in syms \
                or _row_day(row) > until:
            continue
        entry = _pool_entry(rid, row)
        hits = (source_hits(rid, source_index, base_index)
                if rid in base_index else [])
        if not hits:
            field = row_money_field(row)
            entry.update(field=field, amount=row.get(field), ties=None,
                         origin=origin(row))
            out["untraced"] += 1
            out["rows"].append(entry)
            continue
        raw = hits[0]["row"]
        field = row_money_field(raw)
        _fx, _w, fails = money_lineage(rid, raw, row, field=field, **money)
        entry.update(field=field, amount=row.get(field),
                     source=hits[0]["label"])
        if raw.get("source_line"):
            entry["line"] = raw.get("source_line")
        out["checked"] += 1
        if fails:
            entry.update(ties=False, failure=fails[0])
            out["mismatched"].append({
                "id": rid, "action": row.get("action"),
                "symbol": row.get("symbol"), "date": row.get("date"),
                "quantity": row.get("quantity"), "failure": fails[0]})
        else:
            entry["ties"] = True
            out["tied"] += 1
        out["rows"].append(entry)
    out["rows"].sort(key=lambda e: (str(e.get("date") or ""),
                                    str(e.get("symbol") or "")))
    return out


def futures_native_nets(source_index: Dict[str, List[Dict[str, Any]]],
                        base_index: Dict[str, Dict[str, Any]],
                        country: str) -> Dict[str, float]:
    """id -> native settled net of every futures fill the base book
    carries on the settlement basis, RE-DERIVED from the parsed broker
    rows (lib/futures.settle_futures over them, under the book's own
    symbols) — so the FX cross-check of a futures close compares the
    base row with the parsed rows, not with itself.

    Each account's fills are settled on their own, as the run settles
    each account's book in its conversion (taxjson_convert_currency.
    process_transactions): a blended audit's base file holds several
    accounts, and settling them as one book matched one account's close
    against another's opening (backlog TJ-0312)."""
    from taxjson.lib.futures import (FUTURES_SETTLEMENT, method_for,
                                     settle_futures_dicts)
    rows = []
    for rid, hits in source_index.items():
        b = base_index.get(rid)
        if not hits or not b or b.get("type") != FUTURES_SETTLEMENT:
            continue
        r = dict(hits[0]["row"])
        r["symbol"] = b.get("symbol")          # after ticker.map
        # The book the row was settled in: its account's.
        r["account"] = str(b.get("account") or r.get("account") or "")
        rows.append(r)
    try:
        settled = settle_futures_dicts(rows, method_for(country))
    except ValueError:
        return {}
    out: Dict[str, float] = {}
    for r in settled:
        rid = r.get("id")
        if rid and rid in base_index and rid not in out:
            out[rid] = float(r.get("net_amount") or 0.0)
    return out


def build_check_index(paths: List[Path]) -> Tuple[
        Dict[str, List[Dict[str, Any]]], List[str]]:
    """id -> [gain records] from the pipeline's saved gains files (the
    numbers the reports were rendered from). US FIFO can emit several
    lot records per sell id, hence the list."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    labels: List[str] = []
    seen: set = set()
    for p in paths:
        # A gains file named twice summed every id's records twice and
        # failed every tie-out as "the books changed" (S026-01).
        try:
            key = Path(p).resolve()
        except OSError:
            key = Path(p)
        if key in seen:
            emit_line(f"taxjson-audit: warning: --check {p} given more than "
                      f"once — read once.", file=sys.stderr)
            continue
        seen.add(key)
        doc = _load_doc(p)
        labels.append(p.name)
        for g in doc.get("transactions") or []:
            if not _is_disposition_record(g):
                continue
            # Same scope as the engine side (in_scope): income records
            # (PIL carries qty and gain 0.0) are not dispositions —
            # without this symmetric exclusion the reverse sweep
            # flagged every saved PIL row as "fabricated".
            if g.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            # A disposition with no id cannot be joined; it is kept under
            # NOID so the reverse sweep reports it instead of skipping
            # it (S026-02: a record without an id passed at exit 0).
            index.setdefault(g.get("id") or NOID, []).append(g)
    return index, labels


NOID = "\x00no-id"


def build_manual_index(paths: List[Path]) -> Dict[str, List[Dict[str, Any]]]:
    """id -> [rows] of the saved gains files' MANUAL REPORTING REQUIRED
    list (`manual_reporting_required`: dispositions drawn from a
    missing-history or transferred-in opening, reported by hand with no computed gain).
    The audit ties the engine's tainted re-run rows to this list; it
    used to call them MISSING — "stale or truncated saved books" — and
    exit 1 on a fresh run (re-audit A2-0640, A2-1098)."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    seen: set = set()
    for p in paths:
        try:
            key = Path(p).resolve()
        except OSError:
            key = Path(p)
        if key in seen:
            continue
        seen.add(key)
        for g in _load_doc(p).get("manual_reporting_required") or []:
            if isinstance(g, dict):
                index.setdefault(g.get("id") or NOID, []).append(g)
    return index


def _is_disposition_record(g: Dict[str, Any]) -> bool:
    """A gains row the audit traces: a disposition (it has a quantity)
    or a deemed gain with none — a return of capital on an empty pool
    or beyond the ACB (s.40(3), CA-ACB-07; US §301(c)(3)), which the
    return reports like a sale. The audit skipped those and printed
    "total 0.00 ✓" for a year whose return shows the gain (re-audit
    A2-0316)."""
    if "gain" not in g:
        return False
    return bool(g.get("qty")) or bool(g.get("deemed"))


def _kind(g: Dict[str, Any]) -> Tuple[str, bool, bool]:
    """What kind of record a gains row is: one sell id can carry a LONG
    close and a grant WRITE (a cross-zero option fill) — two events
    that must not be summed into one (S026-19)."""
    return (str(g.get("direction") or "LONG"), bool(g.get("grant")),
            bool(g.get("deemed")))


def _join_lot_traces(lots: List[Dict[str, Any]]):
    """One trace for a sale's lot records. Each lot's trace is the
    pool's running trace as that lot was sold, so a later lot's starts
    with the earlier one's: the last such trace is the whole history,
    referenced, not copied — the lots' traces laid end to end repeated
    the pool's history once per lot (backlog TJ-0334). Traces that do
    not continue each other are laid end to end."""
    traces = [l.get("trace") or [] for l in lots]
    traces = [t for t in traces if len(t)]
    if not traces:
        return []
    out = traces[0]
    for t in traces[1:]:
        n = len(out)
        if len(t) >= n and t[0] == out[0] and t[n - 1] == out[n - 1]:
            out = t                     # continues the one before
        else:
            out = list(out) + list(t)
    return out


def merge_lot_records(gains: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One event per (id, kind): US FIFO splits one sell into lot
    records, which are summed here. A sale whose lots differ in term is
    MIXED with the per-term gains in `term_split` (the first lot's term
    labelled the whole sale, S026-15); days_held is kept only when every
    lot agrees (else `days_held_range`).

    Unknown-cost lots (`tainted`: drawn from a missing-history or
    transferred-in opening) and known-cost lots of one sale are two
    events: the run files the known part's gain and lists the other
    under MANUAL REPORTING REQUIRED. Summed into one, the first lot's
    taint labelled the whole sale (backlog TJ-0310)."""
    by_key: Dict[Tuple, List[Dict[str, Any]]] = {}
    order: List[Tuple] = []
    for g in gains:
        k = ((g.get("id") or f"?{len(order)}",) + _kind(g)
             + (bool(g.get("tainted")),))
        if k not in by_key:
            order.append(k)
        by_key.setdefault(k, []).append(g)
    merged: List[Dict[str, Any]] = []
    for k in order:
        lots = by_key[k]
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
        agg["trace"] = _join_lot_traces(lots)
        agg["lots"] = len(lots)
        # The lot records themselves, for the tie-out's per-lot
        # comparison (tie_out_differences); never copied into an event.
        agg["_lots"] = lots
        terms: Dict[str, float] = {}
        for l in lots:
            t = l.get("term")
            if t:
                terms[t] = terms.get(t, 0.0) + float(l.get("gain") or 0)
        if len(terms) > 1:
            agg["term"] = "MIXED"
            agg["term_split"] = {t: round(v, 2)
                                 for t, v in sorted(terms.items())}
        days = {l.get("days_held") for l in lots}
        if len(days) > 1:
            agg["days_held"] = None
            _d = [int(d) for d in days if d is not None]
            agg["days_held_range"] = [min(_d), max(_d)] if _d else None
        merged.append(agg)
    return merged


# What the tie-out compares between the engine re-run and the saved
# gains file (backlog TJ-0332: only gain and disallowed were, so a saved
# file with another term, or proceeds and cost moved alike, tied): the
# amounts and the quantity summed over the sale's records, the fields
# naming the sale, and per lot (US: acquired date and term) its share.
TIE_MONEY_FIELDS = ("proceeds", "cost", "gain", "disallowed_amount",
                    "permanently_disallowed")
TIE_TEXT_FIELDS = ("symbol", "date", "date_settle", "account", "direction")
TIE_LOT_FIELDS = ("qty", "proceeds", "cost", "gain")
TIE_FIELDS = (TIE_MONEY_FIELDS + ("qty",) + TIE_TEXT_FIELDS
              + ("term", "acquired_date", "lots", "replacement_lot_ids"))
# An unknown-cost sale's MANUAL REPORTING REQUIRED row keeps no gain or
# cost (lib/pipeline.run_gains): its quantity, proceeds and names.
MANUAL_TIE_FIELDS = ("qty", "proceeds") + TIE_TEXT_FIELDS


def _qty_differs(a: float, b: float) -> bool:
    # Quantities are saved unrounded (lib/numeric._QTY_FIELDS): only
    # float summation slack.
    return abs(a - b) > 1e-9 * max(1.0, abs(a))


def _sum(recs: List[Dict[str, Any]], f: str) -> float:
    return sum(float(r.get(f) or 0.0) for r in recs)


def _text_differences(g: Dict[str, Any], recs: List[Dict[str, Any]],
                      fields) -> List[Dict[str, Any]]:
    out = []
    for f in fields:
        saved = sorted({str(r.get(f) or "") for r in recs})
        if saved != [str(g.get(f) or "")]:
            out.append({"field": f, "engine": g.get(f),
                        "saved": ", ".join(saved)})
    return out


def _lot_groups(recs: List[Dict[str, Any]]) -> Dict[Tuple[str, str],
                                                   Dict[str, float]]:
    groups: Dict[Tuple[str, str], Dict[str, float]] = {}
    for r in recs:
        k = (str(r.get("acquired_date") or ""), str(r.get("term") or ""))
        a = groups.setdefault(k, {f: 0.0 for f in TIE_LOT_FIELDS + ("n",)})
        for f in TIE_LOT_FIELDS:
            a[f] += float(r.get(f) or 0.0)
        a["n"] += 1
    return groups


def tie_out_differences(g: Dict[str, Any],
                        checks: List[Dict[str, Any]]
                        ) -> List[Dict[str, Any]]:
    """[{field, engine, saved[, lot]}] where the engine's records of one
    sale (`g`, its lots in `_lots`) and the saved gains file's differ
    (TIE_FIELDS). The saved file rounds each amount to 4 dp: 0.00005 a
    record is allowed beyond the half cent (S026-16)."""
    out: List[Dict[str, Any]] = []
    tol = TIE + 0.00005 * len(checks)
    for f in TIE_MONEY_FIELDS:
        e, s = float(g.get(f) or 0.0), _sum(checks, f)
        if abs(e - s) > tol:
            out.append({"field": f, "engine": e, "saved": s})
    e, s = float(g.get("qty") or 0.0), _sum(checks, "qty")
    if _qty_differs(e, s):
        out.append({"field": "qty", "engine": e, "saved": s})
    out += _text_differences(g, checks, TIE_TEXT_FIELDS)
    lots = g.get("_lots") or [g]
    eg, sg = _lot_groups(lots), _lot_groups(checks)
    if set(eg) != set(sg):
        for i, f in enumerate(("acquired_date", "term")):
            ek = sorted({k[i] for k in eg})
            sk = sorted({k[i] for k in sg})
            if ek != sk:
                out.append({"field": f, "engine": ", ".join(ek) or None,
                            "saved": ", ".join(sk) or None})
        if not any(d["field"] in ("acquired_date", "term") for d in out):
            out.append({"field": "lots",
                        "engine": "; ".join(" ".join(k).strip()
                                            for k in sorted(eg)),
                        "saved": "; ".join(" ".join(k).strip()
                                           for k in sorted(sg))})
    elif len(eg) > 1:
        # Each lot's share: the totals tie while one lot's cost moved
        # to another (Form 8949 reports each lot on its own row).
        for k in sorted(eg):
            _lt = TIE + 0.00005 * sg[k]["n"]
            for f in TIE_LOT_FIELDS:
                a, b = eg[k][f], sg[k][f]
                if (_qty_differs(a, b) if f == "qty"
                        else abs(a - b) > _lt):
                    out.append({"field": f, "engine": a, "saved": b,
                                "lot": {"acquired_date": k[0] or None,
                                        "term": k[1] or None}})
    er = sorted(str(x) for l in lots
                for x in (l.get("replacement_lot_ids") or []))
    sr = sorted(str(x) for c in checks
                for x in (c.get("replacement_lot_ids") or []))
    if er != sr:
        out.append({"field": "replacement_lot_ids",
                    "engine": ", ".join(x[:12] for x in er) or None,
                    "saved": ", ".join(x[:12] for x in sr) or None})
    return out


def manual_differences(g: Dict[str, Any], recs: List[Dict[str, Any]]
                       ) -> List[Dict[str, Any]]:
    """tie_out_differences for an unknown-cost sale and its MANUAL
    REPORTING REQUIRED rows (MANUAL_TIE_FIELDS)."""
    out: List[Dict[str, Any]] = []
    e, s = float(g.get("qty") or 0.0), _sum(recs, "qty")
    if _qty_differs(e, s):
        out.append({"field": "qty", "engine": e, "saved": s})
    e, s = float(g.get("proceeds") or 0.0), _sum(recs, "proceeds")
    if abs(e - s) > TIE + 0.00005 * len(recs):
        out.append({"field": "proceeds", "engine": e, "saved": s})
    return out + _text_differences(g, recs, TIE_TEXT_FIELDS)


def differences_text(diffs: List[Dict[str, Any]], limit: int = 4) -> str:
    """`proceeds 1,500.00 vs 1,520.00; term SHORT_TERM vs LONG_TERM`."""
    parts = []
    for d in diffs[:limit]:
        name = d["field"]
        if d.get("lot"):
            name += (f" (lot {d['lot'].get('acquired_date') or '?'}"
                     + (f" {d['lot']['term']}" if d["lot"].get("term")
                        else "") + ")")

        def v(x):
            if isinstance(x, float):
                return f"{x:g}" if d["field"] == "qty" else _fmt(x)
            return "none" if x in (None, "") else str(x)
        parts.append(f"{name} {v(d['engine'])} vs {v(d['saved'])}")
    if len(diffs) > limit:
        parts.append(f"+{len(diffs) - limit} more")
    return "; ".join(parts)


def rate_with_provenance(currency: str, date_str: str,
                         history: Dict[str, Dict[str, Decimal]],
                         default_rate: Decimal
                         ) -> Tuple[Decimal, str, Optional[str]]:
    """The converter's exact date-resolution rules (exact date, else
    walk back up to LOOKBACK_DAYS days, else an explicit --default-rate,
    else None — no rate), returning WHERE the rate came from as well:
    (rate, kind, source_date). kind is 'exact' | 'carried' | 'default'
    | 'missing'."""
    from datetime import datetime, timedelta
    curr_history = history.get(currency) or {}
    miss = "default" if default_rate is not None else "missing"
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return default_rate, miss, None
    for i in range(LOOKBACK_DAYS + 1):
        d = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
        if d in curr_history:
            return curr_history[d], ("exact" if i == 0 else "carried"), d
    return default_rate, miss, None


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
    if row.get("source_line"):
        # A .tt row's line (taxjson-convert-tt; backlog TJ-0359).
        line += f"  [line {row['source_line']}]"
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
                default_rate: Optional[Decimal], base_currency: str,
                tmap, replacement_lookup: Dict[str, Dict[str, Any]],
                checks_supplied: bool,
                futures_native: Optional[Dict[str, float]] = None,
                filled_index: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
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
        # The verb and the per-share note need what kind of record this
        # is: build_event dropped grant/deemed, so every grant-timing
        # WRITE printed as a COVER with proceeds 0.00 (S026-04).
        "grant": bool(g.get("grant")), "deemed": bool(g.get("deemed")),
        "is_option": bool(g.get("is_option")
                          or is_option_symbol(str(g.get("symbol") or ""))),
        "warnings": [], "failures": [],
    }
    for _k in ("lots", "term_split", "days_held_range", "note",
               # A .tt ALLOWLOSS filing position on this sale (CA-SL-18
               # / US-WASH-25): the trace showed "disallowed 0.00" and
               # nothing else (pre-release review M7).
               "loss_override"):
        if g.get(_k):
            ev[_k] = g[_k]

    # The book row the record stands on: its own id, but for a wash-sale
    # basis add booked in the loss's year (US-WASH-22: a deemed row
    # `<loss id>-wash-basis`, pipeline.place_retro_wash_adjustments),
    # the loss sale's row (backlog TJ-0313).
    from taxjson.lib.pipeline import RETRO_WASH_ID_SUFFIX
    retro_add = bool(g.get("deemed")) and gid.endswith(RETRO_WASH_ID_SUFFIX)
    rid = gid[:-len(RETRO_WASH_ID_SUFFIX)] if retro_add else gid

    # --- source rows (nominal) --------------------------------------
    hits = source_hits(rid, source_index, base_index)
    ev["sources"] = [{"file": h["label"], "row": h["row"]} for h in hits]
    if not hits:
        ev["warnings"].append(
            "no parsed source row found for this id — the disposition "
            "cannot be traced to a broker file (synthesized row, or "
            "stale work/ artifacts).")

    raw = hits[0]["row"] if hits else None

    # --- ticker mapping ----------------------------------------------
    base_row = base_index.get(rid)
    ev["base_row"] = base_row
    try:
        _mult = float((base_row or {}).get("multiplier") or 0.0)
    except (TypeError, ValueError):
        _mult = 0.0
    if _mult > 0:
        ev["multiplier"] = _mult
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

    # --- FX conversion and the amount --------------------------------
    # The book row's amount re-derived from the parsed row, converted or
    # not (money_lineage; a base-currency row was marked "native" with
    # no amount compared, backlog TJ-0333).
    fx: Optional[Dict[str, Any]] = None
    if raw is not None and base_row is not None and not retro_add:
        fx, _w, _f = money_lineage(
            rid, raw, base_row, base_currency=base_currency,
            fx_history=fx_history, default_rate=default_rate,
            futures_native=futures_native, filled_index=filled_index)
        ev["warnings"].extend(_w)
        ev["failures"].extend(_f)
    ev["fx"] = fx

    # --- wash replacements resolved to rows ---------------------------
    reps = []
    for rep_id in g.get("replacement_lot_ids") or []:
        rrow = replacement_lookup.get(rep_id)
        reps.append({"id": rep_id, "row": rrow})
    ev["replacements"] = reps

    # --- pipeline tie-out ---------------------------------------------
    # An unknown-cost event ties to MANUAL REPORTING REQUIRED (main),
    # never to the computed records its id shares when the sale also
    # drew known-cost lots (backlog TJ-0310).
    checks = [] if g.get("tainted") else (check_index.get(gid) or [])
    if len({_kind(c) for c in checks}) > 1:
        # One id, two kinds of record (a cross-zero fill's LONG close and
        # its grant WRITE): each event ties to its own kind (S026-19).
        checks = [c for c in checks if _kind(c) == _kind(g)]
    tie: Dict[str, Any] = {"records": len(checks)}
    if checks:
        # US FIFO can split one sell across lots; compare against the
        # per-id AGGREGATE, which is what the reports sum anyway.
        cg = sum(float(c.get("gain") or 0.0) for c in checks)
        cd = sum(float(c.get("disallowed_amount") or 0.0) for c in checks)
        # The engine re-run also yields one record per lot; the caller
        # aggregates before calling for multi-record ids, so g here is
        # already the aggregate view.
        # Every filing field, not the gain and disallowed amount alone
        # (TIE_FIELDS, backlog TJ-0332).
        diffs = tie_out_differences(g, checks)
        tie.update({"pipeline_gain": cg, "pipeline_disallowed": cd,
                    "mismatched": diffs,
                    "ties": not diffs})
        if diffs:
            ev["failures"].append(
                f"PIPELINE TIE-OUT FAILED: engine re-run vs saved gains "
                f"file: {differences_text(diffs)} — the books changed "
                f"since the last `taxjson run`, or the audit was "
                f"invoked with different inputs.")
    else:
        tie["ties"] = None
        if checks_supplied:
            ev["warnings"].append(
                "disposition not found in the pipeline gains file(s) — "
                "cannot tie out (books changed since the last run?).")
    ev["tie_out"] = tie

    # The engine's trace as is (a view of the pool's running trace,
    # trace_format.SharedTrace): a copy per event kept every sale's whole
    # history, which grew with the square of the sales (TJ-0334). The
    # JSON writer and the renderer read it as the list it stands for.
    ev["trace"] = g.get("trace") or []
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
    """Start a section: cyan label in a fixed gutter, first line after
    (wrapped at the house width under the gutter, as _cont)."""
    rest: List[str] = []
    _cont(rest, first)
    lead = f"  {' ' * _GUT}"
    head = rest[0][len(lead):] if rest else ""
    out.append(f"  {paint(label.ljust(_GUT), 'lbl')}{head}")
    out.extend(rest[1:])


_ANSI_WRAPPED = __import__("re").compile(
    r"\A((?:\x1b\[[0-9;]*m)+)([^\x1b]*)\x1b\[0m\Z")


def _cont(out, text: str):
    """Continuation line inside the current section, wrapped at the
    house width under the gutter (lib/out); a line painted as a whole is
    wrapped and painted line by line."""
    from taxjson.lib.out import wrap
    lead = f"  {' ' * _GUT}"
    m = _ANSI_WRAPPED.match(text)
    if m:
        for ln in wrap(m.group(2), None, lead, lead):
            out.append(lead + m.group(1) + ln[len(lead):] + "\x1b[0m")
        return
    if "\x1b" in text:
        out.append(lead + text)
        return
    out.extend(wrap(text, None, lead, lead))



def render_event(ev: Dict[str, Any], n: int, total: int,
                 country: str, show_trace: bool,
                 use_color: bool = False,
                 no_wash: bool = False) -> List[str]:
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
    # The row's own id, unmasked, on purpose (owner decision, audit
    # A2-1379): it is the handle `--id` takes. For most brokers it is a
    # content hash; a Kraken row's id is the exchange's ledger txid — an
    # exchange reference, never an account number or wallet address —
    # so it is shown as is, unlike the parser's masked messages.
    from taxjson.lib.out import width as _width
    _w = _width()
    _hid = f"   #{ev['id'][:12]}"
    if _w > 0 and len(head) + len(_hid) > _w:
        # Too long for one line: the id (the handle --id takes) under it.
        out.append(paint(head, "h"))
        out.append(paint(f"  {_hid.strip()}", "dim"))
    else:
        out.append(paint(head, "h") + paint(_hid, "dim"))
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
            if fx.get("futures_settlement"):
                _cont(out, paint(
                    f"futures: the settled P/L is booked; the notional "
                    f"{_fmt(fx.get('notional') or 0.0)} {fx['from']} "
                    f"never changes hands", "dim"))
            if "nominal_net" in fx:
                # The amount itself (backlog TJ-0333).
                _cont(out, f"{_fmt(fx['nominal_net'])} {fx['from']}"
                           f"  =  base book "
                           + (OK if fx["ties"] else BAD))
        elif fx.get("rate_kind") == "missing":
            _sec(out, paint, "FX",
                 f"{fx['from']}\u2192{fx['to']}  " + paint(
                     f"(NO RATE on file for {fx['rate_date']})", "dim"))
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
            if fx.get("futures_settlement"):
                _cont(out, paint(
                    f"futures: the settled P/L is converted at this "
                    f"closing leg's rate; the notional "
                    f"{_fmt(fx.get('notional') or 0.0)} {fx['from']} "
                    f"never changes hands", "dim"))
            _cont(out, f"{_fmt(fx['nominal_net'])} {fx['from']} "
                       f"\u00d7 {fx['rate']:.5f} = "
                       f"{_fmt(fx['computed_net'])} {fx['to']}"
                       f"  =  base book "
                       + (OK if fx["ties"] else BAD))

    # ---- disposition -------------------------------------------------
    # A US book run with --no-wash is a crypto account's: digital
    # assets are outside §1091 (US-WASH-13), so no wash-sale label
    # (re-audit A2-1267).
    rule = ("ACB pool — ITA s.47, blended across taxable accounts"
            if country not in ("us", "usa")
            else "FIFO lots — IRC; no wash-sale rule (digital assets "
                 "are outside \u00a71091)" if no_wash
            else "FIFO lots — IRC, wash sale \u00a71091")
    _sec(out, paint, "DISPOSITION", paint(rule, "dim"))
    if ev.get("deemed") and ev.get("note"):
        # What a deemed row is (a wash-sale basis add booked in the
        # loss's year, US-WASH-22): its source row is the loss sale.
        _cont(out, paint(str(ev["note"]), "warn"))

    def money(label, v, mark=""):
        _cont(out, f"{label:<15}{_fmt(v):>16}{mark}")

    is_opt = bool(ev.get("is_option")
                  or is_option_symbol(str(ev.get("symbol") or "")))

    def per_share(v):
        # (1,000 sh @ 12.3400) — the ACB/share (or sale price/share)
        # the reader wants to sanity-check against a statement. An
        # option is quoted per share: N contracts x 100 (S026-05).
        if qty and abs(qty) > 1e-9:
            if is_opt:
                n = abs(qty)
                # The row's declared contract size (a x10 mini, a
                # futures option's 1000) — the fixed 100 printed a
                # wrong per-share price (re-audit A2-1099).
                m = float(ev.get("multiplier") or 0) or OPTION_MULTIPLIER
                return paint(f"   ({n:,g} contract{'s' if n != 1 else ''}"
                             f" \u00d7 {m:g} sh @ "
                             f"{abs(v) / (n * m):,.4f})",
                             "dim")
            return paint(f"   ({abs(qty):,g} sh @ "
                         f"{abs(v) / abs(qty):,.4f})", "dim")
        return ""

    _pr = float(ev.get("proceeds") or 0)
    _cb = float(ev.get("cost") or 0)
    if ev.get("direction") == "SHORT":
        # Engine signed short convention: `cost` holds the NEGATED
        # short-sale proceeds, `proceeds` the NEGATED cover cost. Shown
        # as the return shows them (form-export): proceeds = what the
        # short sale (or the write) brought in, cost = the cover
        # (S026-08); gain = proceeds - cost is unchanged.
        _pr, _cb = -_cb + 0.0, -float(ev.get("proceeds") or 0) + 0.0
    money("proceeds", _pr, per_share(_pr))
    money("cost basis", _cb, per_share(_cb))
    if ev.get("direction") == "SHORT":
        _cont(out, paint("short: proceeds = the short sale / write, cost "
                         "= the buy-to-cover (as on the return)", "dim"))

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
        defer = dis - perm
        _us = country in ("us", "usa")
        dests = []
        if defer > TIE or perm <= TIE:
            # The deferred part: on the replacement's ACB. A split
            # denial named only the permanent part, so a deferred loss
            # read as lost for good (S026-10).
            _amt = f"{_fmt(defer)} " if perm > TIE else ""
            dests.append(
                f"{_amt}denied loss \u2192 replacement lot's ACB "
                f"(s.53(1)(f))" if not _us else
                f"{_amt}denied loss \u2192 replacement lot's basis; "
                f"holding period tacks (\u00a71223(3))")
        if perm > TIE:
            dests.append(
                f"{_fmt(perm)} denied loss PERMANENTLY lost: replacement "
                f"in a registered account or an affiliated person's "
                f"(s.53(1)(f))" if not _us else
                f"{_fmt(perm)} denied loss PERMANENTLY lost (replacement "
                f"in a sheltered account — Rev. Rul. 2008-5)")
        for dest in dests:
            _cont(out, paint(dest, "dim"))
        money("allowed gain", float(ev.get("gain") or 0))
    else:
        money("gain", float(ev.get("gain") or 0))
    if isinstance(ev.get("loss_override"), dict):
        from taxjson.lib.loss_overrides import note_text
        _sec(out, paint, "POSITION", paint(
            note_text(ev["loss_override"], country), "warn"))

    # ---- tie-out -----------------------------------------------------
    tie = ev.get("tie_out") or {}
    if ev.get("manual_reporting"):
        _sec(out, paint, "MANUAL", paint(
            "no purchase in your files (cost unknown) — the gain above "
            "is not computed on the return; report this sale by hand",
            "warn"))
    if tie.get("manual"):
        _sec(out, paint, "TIE-OUT",
             "saved gains file: listed under MANUAL REPORTING REQUIRED  "
             + (BAD if tie.get("ties") is False else OK))
    elif tie.get("ties") is not None:
        _sec(out, paint, "TIE-OUT",
             f"saved gains file: gain {_fmt(tie['pipeline_gain'])}, "
             f"disallowed {_fmt(tie['pipeline_disallowed'])}  "
             + (OK if tie["ties"] else BAD))

    from taxjson.lib.out import label as _label, wrap as _wrap
    for w in ev.get("warnings") or []:
        for _ln in _wrap(_label("warning", stream=sys.stdout) + str(w),
                         None, "  ", "    "):
            out.append(paint(_ln, "warn"))
    for f in ev.get("failures") or []:
        for _ln in _wrap(f"FAILED: {f}", None, "  ", "    "):
            out.append(paint(_ln, "bad"))

    if show_trace and ev.get("trace"):
        # The engine's pool history in the report layout (as `taxjson
        # wash-sales --explain`), base currency; the trace files keep
        # render_gain_block's '#' lines.
        out += render_report_trace({**ev, "trace": ev["trace"]})
    return out


# What the audit checks, and what it does not (backlog TJ-0332,
# TJ-0333): the footer's `not traced` line, the --json `scope` and
# docs/commands.md say it, so a clean audit is not read as proof of
# more than it compared.
SCOPE = {
    "replayed": ("the engine re-run on the base books (work/"
                 "<account>_base.json), already converted to the base "
                 "currency"),
    "tie_out": list(TIE_FIELDS),
    "manual_tie_out": list(MANUAL_TIE_FIELDS),
    "source_amounts": [
        "each audited sale's net amount against its parsed broker row "
        "(the fill stage's priced row for a crypto book, the settled "
        "P/L for a futures fill), times the rates file's rate when "
        "converted",
        "the same for the audited securities' other trades (buys, "
        "earlier sales, assignments) up to 31 days after the last "
        "audited sale, and for the wash-sale / superficial-loss "
        "replacements the sales name",
        "the same for those securities' rows that are not trades "
        "(return of capital and the other cost adjustments, reinvested "
        "distributions, splits and ticker changes, corporate-action "
        "rows, transfers, openings, their dividends, withholding and "
        "fees): a dividend's gross amount, any other row's net amount, "
        "each row with its parsed file and its .tt line",
        "the securities read: the audited ones, under their earlier "
        "tickers too (a rename), and the other securities of a "
        "corporate action of theirs"],
    "not_checked": [
        "rows with no parsed broker row (counted as `no parsed row`; "
        "what made each is said: a [[distributions]] table, a dated "
        "RENAME line, an OPENING ... cost=unknown line, a stage of the "
        "run): re-run, not traced",
        "a sheltered account's rows",
        "rows of the securities not audited, and rows on no security "
        "(interest, account fees)",
        "the parsed row's own reading of the broker's export (the "
        "parsers' tests cover it)",
        "a crypto row's price from the fill stage (its priced row is "
        "the start)",
        "days held and the descriptive fields (notes, wash windows)"],
}
NOT_TRACED_LINE = ("rows with no parsed row (what made each: --details) and "
                   "a sheltered account's rows: re-run only")
# How many rows with no parsed row --details names (--json lists each).
UNTRACED_SHOWN = 12
TIE_FIELDS_TEXT = ("each sale's quantity, proceeds, cost, gain, disallowed "
                   "and permanently disallowed amounts, symbol, dates, "
                   "account, direction and term, each lot's acquired date "
                   "and share, and the replacement lots")


def _untraced_text(e: Dict[str, Any]) -> str:
    """One row with no parsed row, for --details: its date, action,
    security and amount, and what made it (row_origin)."""
    action = str(e.get("action") or "?")
    try:
        qty = float(e.get("quantity") or 0.0)
    except (TypeError, ValueError):
        qty = 0.0
    if action in POOL_TRADE_ACTIONS:
        action = ("SELL" if qty < 0 else "BUY") + (
            "" if e.get("action") == "BUYSELL" else f" ({e['action']})")
    sym = str(e.get("symbol") or "?")
    if e.get("symbol_new"):
        sym += f" -> {e['symbol_new']}"
    bits = [str(e.get("date") or "?"), action]
    if qty and action not in ("SPLIT",):
        bits.append(f"{abs(qty):g}")
    bits.append(sym)
    try:
        amt = float(e.get("amount") or 0.0)
    except (TypeError, ValueError):
        amt = 0.0
    if amt:
        bits.append(_fmt(amt))
    return " ".join(bits) + f" — {e.get('origin') or row_origin({})}"


def untraced_rows(pool: Optional[Dict[str, Any]],
                  other: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The pool checks' rows with no parsed row, by date (--details)."""
    rows = list((pool or {}).get("untraced_rows") or []) + [
        r for r in (other or {}).get("rows") or [] if r.get("origin")]
    return sorted(rows, key=lambda r: (str(r.get("date") or ""),
                                       str(r.get("symbol") or "")))


def render_reconciliation(events: List[Dict[str, Any]],
                          check_labels: List[str],
                          checks_supplied: bool,
                          use_color: bool = False,
                          country: Optional[str] = None,
                          brief: bool = False,
                          pool: Optional[Dict[str, Any]] = None,
                          other: Optional[Dict[str, Any]] = None
                          ) -> List[str]:
    from taxjson.lib.out import wrap as _wrap
    W = 86
    paint = _mk_paint(use_color)
    OK = paint("\u2713", "ok")
    total_gain = sum(float(e.get("gain") or 0) for e in events
                     if not e.get("manual_reporting"))
    total_dis = sum(float(e.get("disallowed_amount") or 0) for e in events)
    tied = sum(1 for e in events if (e["tie_out"].get("ties") is True))
    untied = sum(1 for e in events if (e["tie_out"].get("ties") is False))
    manual = sum(1 for e in events if e["tie_out"].get("manual")
                 and e["tie_out"].get("ties") is not False)
    nocheck = sum(1 for e in events if e["tie_out"].get("ties") is None
                  and not e["tie_out"].get("out_of_scope")
                  and not e["tie_out"].get("manual"))
    outside = sum(1 for e in events if e["tie_out"].get("out_of_scope"))
    # A sale's amount against its parsed row: converted (FX) or not
    # (native, compared since backlog TJ-0333).
    fx_ok = sum(1 for e in events if e.get("fx") and e["fx"].get("ties"))
    fx_bad = sum(1 for e in events
                 if e.get("fx") and not e["fx"].get("ties"))
    conv = sum(1 for e in events if e.get("fx")
               and not e["fx"].get("native"))
    sourced = sum(1 for e in events if e.get("sources"))
    n = len(events)

    def mark(bad_count, all_ok):
        if bad_count:
            return paint("\u2717", "bad")
        return OK if all_ok else ""

    # The accounts tied out, not their file names.
    _accts = [re.sub(r"_gains(_wash)?\.json$", "", lb)
              for lb in check_labels]
    out = [paint("\u2550" * W, "dim"),
           paint("RECONCILIATION", "h")
           + (paint(f"   {', '.join(_accts)}", "dim")
              if check_labels else ""),
           paint("\u2500" * W, "dim"),
           f"  events audited     {n:>14,}",
           f"  total gain         {_fmt(total_gain):>14}  "
           + paint("(unrounded engine sum)", "dim"),
           f"  total disallowed   {_fmt(total_dis):>14}",
           f"  source rows        {sourced:>7,}/{n:,} traced to a "
           f"parsed broker row  " + mark(n - sourced, sourced == n),
           f"  sale amounts       {fx_ok:>7,} tied, {fx_bad:,} "
           f"mismatched ({conv:,} converted)  "
           + mark(fx_bad, fx_bad == 0)]
    if pool is not None:
        _bad = len(pool.get("mismatched") or [])
        out.append(f"  pool trades        {pool.get('tied', 0):>7,} tied, "
                   f"{_bad:,} mismatched, {pool.get('untraced', 0):,} "
                   f"with no parsed row  "
                   + mark(_bad, _bad == 0 and not pool.get("untraced")))
    if other is not None:
        # The rows that are not trades, traced the same way (TJ-0359).
        _bad = len(other.get("mismatched") or [])
        out.append(f"  other pool rows    {other.get('tied', 0):>7,} tied, "
                   f"{_bad:,} mismatched, {other.get('untraced', 0):,} "
                   f"with no parsed row  "
                   + mark(_bad, _bad == 0 and not other.get("untraced")))
    if checks_supplied:
        out.append(f"  pipeline tie-out   {tied:>7,} tied, {untied:,} "
                   f"MISMATCHED, {nocheck:,} not found  "
                   + mark(untied + nocheck,
                          untied == 0 and nocheck == 0))
        # (The checklist reads the tie-out line and the unknown-cost
        # count, each whole on its line: captured output is unwrapped.)
        if manual:
            out.extend(_wrap(f"{manual:,} unknown-cost "
                             f"disposition(s) tied to MANUAL REPORTING "
                             f"REQUIRED — reported by hand, not in the "
                             f"total", None, " " * 21, " " * 21))
        if outside:
            out.extend(_wrap(f"{outside:,} outside the "
                             f"saved books' tax year — not tied out (the "
                             f"work/ gains files hold one year)", None,
                             " " * 21, " " * 21))
    else:
        out.append("  pipeline tie-out   " + paint(
            "(no gains files supplied — engine re-run stands alone)",
            "dim"))
    # What is not checked, said where the ✓ marks are (TJ-0332/0333).
    out.extend(paint(ln, "dim") for ln in _wrap(
        f"not traced         {NOT_TRACED_LINE}", None, "  ", " " * 21))
    if not brief:
        # The rest of the scope and the rounding note: --details
        # (docs/output-style.md).
        out.extend(paint(ln, "dim") for ln in _wrap(
            f"The tie-out compares {TIE_FIELDS_TEXT} with the saved gains "
            f"file. Not checked: " + "; ".join(SCOPE["not_checked"][2:])
            + ".", None, "  ", "  "))
        out.extend(paint(ln, "dim")
                   for ln in _wrap(totals_note(country), None, "  ", "  "))
        # Each pool row with no parsed row, with what made it (backlog
        # TJ-0359): they were counted with no word on what they were.
        _un = untraced_rows(pool, other)
        if _un:
            out.append("  With no parsed row (re-run, not traced):")
            for r in _un[:UNTRACED_SHOWN]:
                out.extend(_wrap(_untraced_text(r), None, "    ",
                                 "      "))
            if len(_un) > UNTRACED_SHOWN:
                out.append(f"    ... {len(_un) - UNTRACED_SHOWN:,} more "
                           f"(--json: pool_trades.untraced_rows, "
                           f"pool_other_rows.rows)")
    out.append(paint("\u2550" * W, "dim"))
    return out


def _finite(v):
    """`v` with every non-finite float as None: --json is strict JSON (a
    futures fill the parsed rows cannot re-derive carried NaN)."""
    import math
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def manual_reporting_totals(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The unknown-cost dispositions (`manual_reporting`), which no gain
    total counts: how many, their quantity and their proceeds — what is
    reported by hand (the --json `manual_reporting`; `taxjson audit`
    merges its books' with the same function, backlog TJ-0302)."""
    man = [e for e in events if e.get("manual_reporting")]
    return {"events": len(man),
            "qty": round(sum(float(e.get("qty") or 0.0) for e in man), 6),
            "proceeds": round(sum(float(e.get("proceeds") or 0.0)
                                  for e in man), 2)}


# R1-267: the audit sums unrounded engine values; the return's form rows
# (form-export, `sum` FOR THE RETURN) round each row to the cent first.
# Each country's own form (re-audit A2-0737): Schedule 3 or Form 8949.
def totals_note(country: Optional[str]) -> str:
    from taxjson.lib.country import GAINS_FORM
    form = GAINS_FORM[_canon_or_none(country)]
    return (f"Totals are unrounded engine sums; {form} rows "
            f"(form-export, sum FOR THE RETURN) are rounded to the cent "
            f"first, so those totals can differ by a few cents.")


def _canon_or_none(country: Optional[str]) -> Optional[str]:
    try:
        return canonical_country(country)
    except Exception:                               # noqa: BLE001
        return None



# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Authoritative per-disposition audit: broker row -> "
                    "ticker map -> FX -> pool -> gain, every step "
                    "cross-checked. Exit 1 when any check fails.")
    add_country_argument(p)
    p.add_argument("--year", type=tax_year, help="Tax year filter.")
    p.add_argument("--tax-date", choices=["trade", "settle"], default=None)
    p.add_argument("--base", required=True,
                   help="Engine input books (mapped + converted) — the "
                        "same file the pipeline's gains stage consumed.")
    p.add_argument("--sheltered", action="append", default=[],
                   metavar="FILE",
                   help="sheltered_base.json for the wash/superficial-loss "
                        "context; repeatable, as in taxjson-gains.")
    p.add_argument("--affiliated")
    p.add_argument("--incomplete-history", metavar="DIR")
    # None -> the country default (GainsRequest): per-account FIFO on a
    # US book (US-BASIS-01), as taxjson-gains does — the standalone
    # audit pooled every account's lots (re-audit A2-0318).
    p.add_argument("--per-account-basis", action="store_true", default=None)
    from taxjson.lib.pipeline import add_loss_override_args
    add_loss_override_args(p)
    p.add_argument("--corporate-distribution", action="append",
                   default=None, metavar="SYMBOL",
                   help="Canada: as in taxjson-gains ([settings] "
                        "corporate_distributions) — the same income "
                        "dating the books used.")
    p.add_argument("--ric-january-dividend", action="append",
                   default=None, metavar="\"SYMBOL [YYYY-01-DD]\"",
                   help="USA: as in taxjson-gains ([settings] "
                        "ric_january_dividends).")
    p.add_argument("--cross-asset", action="store_true",
                   help=argparse.SUPPRESS)    # retired, ignored
    p.add_argument("--option-premium-timing", choices=["grant", "close"],
                   default=None, help="Canada only (default: close)")
    p.add_argument("--option-grant-since", type=tax_year, default=None)
    p.add_argument("--option-buyback-wash", action="store_true")
    p.add_argument("--transfers-as-acquisitions", action="store_true",
                   help="Count a sheltered account's unmatched TRANSFER rows as "
                        "acquisitions/disposals for the superficial-loss / wash-sale "
                        "window (strict; [settings] transfers_as_acquisitions = true). "
                        "Default: a custody move, held but never a purchase.")
    p.add_argument("--locked-record", action="append", default=None,
                   metavar="PATH",
                   help="USA: a filed year's lock file, repeatable, as in "
                        "taxjson-gains: a basis add its own row for that "
                        "sale already has stays in that sale (US-WASH-22).")
    p.add_argument("--locked-year", action="append", type=int,
                   default=None, metavar="YEAR",
                   help="USA: a filed (locked) tax year, repeatable, as "
                        "in taxjson-gains (`taxjson audit` passes every "
                        "filed/<year>.json): a wash-sale basis add to a "
                        "replacement sold in such a year is booked in the "
                        "loss's year (US-WASH-22).")
    p.add_argument("--no-wash", action="store_true",
                   help="Disable wash detection (US crypto: digital "
                        "assets are property, not securities — §1091 "
                        "does not reach them).")
    p.add_argument("--rates", help="FX history the pipeline converted "
                                   "with (work/to_base.csv).")
    p.add_argument("--base-currency",
                   help="Report currency (inferred from the base books "
                        "when omitted).")
    p.add_argument("--default-rate", type=positive_rate, default=None,
                   help="A rate of your own for a row with no rate in "
                        "--rates on its date or the days before it, as "
                        "the converter's --default-rate. Default: none — "
                        "there is no built-in rate, and such a row fails "
                        "its FX cross-check as a missing rate.")
    p.add_argument("--map", dest="ticker_map", help="ticker.map for "
                                                    "naming rename rules.")
    p.add_argument("--source", action="append", default=[],
                   help="Parsed-source JSON (repeatable) — the nominal-"
                        "currency rows dispositions are joined back to.")
    p.add_argument("--check", action="append", default=[],
                   help="Pipeline gains JSON (repeatable) to tie out "
                        "against.")
    p.add_argument("--check-year", type=tax_year, default=None,
                   help="The tax year the --check files hold (pipeline "
                        "gains files are year-scoped): dispositions of "
                        "other years are reported as not tied out "
                        "instead of MISSING.")
    p.add_argument("--filled", action="append", default=[],
                   help="Price-filled stage JSON (<acct>_filled.json, "
                        "repeatable): the nominal for rows the parse "
                        "left at 0 and the fill stage priced.")
    p.add_argument("--dated-events", metavar="PATH", default=None,
                   help="The run's work/dated_events.state: the .tt "
                        "RENAME line a ticker change's row came from "
                        "is named with it.")
    p.add_argument("--symbol", action="append", default=[],
                   help="Filter: symbol prefix (repeatable).")
    p.add_argument("--id", dest="gain_id", help="Filter: tx id prefix.")
    p.add_argument("--date", help="Filter: disposition date — matches "
                                  "the trade date OR the settlement date.")
    p.add_argument("--account", help="Filter: account name.")
    p.add_argument("--summary", action="store_true",
                   help="One line per event instead of full blocks.")
    # Wrapper plumbing (`taxjson audit` runs one invocation per book and
    # decides itself whether "nothing matched" is an error overall).
    p.add_argument("--no-match-rc", type=int, default=1,
                   help=argparse.SUPPRESS)
    p.add_argument("--no-trace", action="store_true",
                   help="Omit the engine pool traces.")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI color (auto-on for terminals; "
                        "NO_COLOR is honored).")
    p.add_argument("--json", action="store_true",
                   help="Emit the full audit as JSON.")
    p.add_argument("--brief", action="store_true",
                   help="Leave out the notes (`taxjson audit` passes it "
                        "unless --details; docs/output-style.md)")
    return p.parse_args(argv)


def unique_prefix_len(ids, minimum: int = 10) -> int:
    """Shortest prefix length (>= minimum) that keeps every id in `ids`
    distinct — the --summary column is meant to be pasted into --id,
    and a fixed 10 characters collided for ids sharing a stem."""
    ids = [str(i or "") for i in ids]
    n = minimum
    longest = max((len(i) for i in ids), default=minimum)
    while n < longest and len({i[:n] for i in ids}) < len(set(ids)):
        n += 1
    return n


def _norm_country(c: str) -> str:
    """Canonical canada|usa (lib/country); anything else is refused —
    it used to map every unrecognised value to canada (ENGINE-06)."""
    return canonical_country(c, what="--country")


@guard_main("taxjson-audit", value_errors=True)
def main(argv=None) -> int:
    args = parse_args(argv)
    country = _norm_country(args.country)
    refuse_foreign_flags(args, "taxjson-audit")
    # Close timing by default, while a Canada project uses grant timing:
    # say so, as taxjson-gains does (R1-177, A2-0677).
    from taxjson.bin.taxjson_gains import _timing_default_note
    _timing_default_note(args, "taxjson-audit")
    if args.tax_date is None:
        args.tax_date = "trade" if country == "usa" else "settle"

    base_path = Path(args.base)
    transactions = load_transactions(base_path)
    # Repeatable (A2-0194): a second --sheltered used to replace the
    # first silently.
    sheltered = [t for f in args.sheltered
                 for t in load_transactions(Path(f))]
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
    for _sf in args.sheltered:
        for row in (_load_doc(Path(_sf))
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
            emit_line(f"taxjson-audit: warning: could not read ticker.map "
                      f"({e}) — rename rules will not be named.",
                      file=sys.stderr)

    source_index = build_source_index([Path(s) for s in args.source])
    check_index, check_labels = build_check_index(
        [Path(c) for c in args.check])
    manual_index = build_manual_index([Path(c) for c in args.check])

    # Same load-side preprocessing as the pipeline and explain, so the
    # audit cannot contradict the .sum it justifies.
    transactions, sheltered, affiliated, _ = prepare_books(
        transactions, sheltered, affiliated, taxable=False,
        incomplete_history=(Path(args.incomplete_history)
                            if args.incomplete_history else None),
        phantom_hint=False, country=country,
        transfers_as_acquisitions=args.transfers_as_acquisitions)

    # The engine options and the income re-dating run_gains applies
    # (lib/pipeline: one builder): the trust ROC record date
    # (CA-INC-DATE-ROC-TRUST, A2-0033/A2-0315/A2-0327), the grant-timing
    # since-year on the tax-date basis (A2-0314/A2-0317) and US
    # per-account FIFO (A2-0318). Without them the re-run differed from
    # the books and the tie-out failed a correct project.
    req = GainsRequest(
        country=country, tax_date=args.tax_date,
        per_account_basis=args.per_account_basis,
        option_premium_timing=args.option_premium_timing or "close",
        option_grant_since=args.option_grant_since,
        option_buyback_loss_superficial=args.option_buyback_wash,
        corporate_distributions=tuple(args.corporate_distribution or ()),
        ric_january_dividends=tuple(args.ric_january_dividend or ()),
        loss_overrides=loss_overrides_from_args(args))
    apply_roc_record_dates(transactions, req)
    rules = get_tax_rules(country)
    # The engine traces only when something shows them: the blocks
    # (without --no-trace) and --json. --summary and --no-trace kept
    # every sale's whole pool history all the same (backlog TJ-0334).
    kwargs: Dict[str, Any] = dict(
        sheltered_transactions=sheltered,
        affiliated_transactions=affiliated,
        trace=not args.no_trace and (args.json or not args.summary),
        detect_wash_sales=not args.no_wash)
    kwargs.update(engine_options(req))
    from taxjson.lib.core import AmbiguousTransferDateError
    try:
        results = rules.compute_gains(transactions, **kwargs)
    except AmbiguousTransferDateError as e:
        sys.exit(exit_text(f"taxjson-audit: {e}"))
    if country == "usa":
        # run_gains' post-engine pass (US-WASH-22): a wash-sale basis add
        # to a replacement sold in a filed (locked) year is booked as its
        # own row in the loss's year. Without it the audit re-ran the
        # engine's raw rows, called the saved add row fabricated and
        # missed its loss (backlog TJ-0313). The run has said its notes
        # and ATTENTION lines already: not repeated here.
        from taxjson.lib.pipeline import place_retro_wash_adjustments
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            place_retro_wash_adjustments(
                results, args.tax_date, tuple(args.locked_year or ()),
                args.year, tuple(args.locked_record or ()))

    date_key = "date_settle" if args.tax_date == "settle" else "date"

    # The symbol filter also takes the broker's own ticker: every block
    # prints the source row (SELL 120 ABC.US) and its MAPPING, yet
    # `audit ABC.US` matched nothing because the books carry the
    # ticker.map target (ABC.TO) (S048-17).
    _want_syms = [str(w).upper() for w in (args.symbol or [])]
    if _want_syms and tmap is not None:
        for _attr in ("glob", "tobase", "journal"):
            for _src, _dst in (getattr(tmap, _attr, {}) or {}).items():
                if _dst and any(str(_src).upper().startswith(w)
                                for w in _want_syms):
                    _want_syms.append(str(_dst).upper())

    def in_scope(g) -> bool:
        if g.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU") \
                or not _is_disposition_record(g):
            return False
        return row_filter(g)

    def row_filter(g) -> bool:
        """The year and the row filters, on an engine record or a saved
        one alike: the reconciliation sweep scopes the saved gains files
        with the same test as the engine side (backlog TJ-0301)."""
        eff = g.get(date_key) or g.get("date") or ""
        if args.year and not eff.startswith(str(args.year)):
            return False
        # --date matches EITHER date: the display leads with the trade
        # date while the Canadian filing basis is the settle date, so
        # a date copied from the block used to match nothing.
        if args.date and args.date not in (g.get("date"),
                                           g.get("date_settle")):
            return False
        if _want_syms and not any(
                str(g.get("symbol") or "").upper().startswith(s)
                for s in _want_syms):
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
    # keep each lot's trace — but never fold a grant WRITE into the
    # LONG close that shares its id (S026-19).
    merged = merge_lot_records(gains)

    merged.sort(key=lambda g: (g.get(date_key) or g.get("date") or "",
                               g.get("symbol") or ""))

    futures_native = futures_native_nets(source_index, base_index, country)
    filled_index: Dict[str, Dict[str, Any]] = {}
    for fp in args.filled:
        for row in (_load_doc(Path(fp)).get("transactions") or []):
            if row.get("id"):
                filled_index[row["id"]] = row
    _default_rate = (None if args.default_rate is None
                     else Decimal(str(args.default_rate)))
    events = [build_event(g, base_index, source_index, check_index,
                          fx_history, _default_rate,
                          base_currency, tmap, replacement_lookup,
                          checks_supplied=bool(args.check),
                          futures_native=futures_native,
                          filled_index=filled_index)
              for g in merged]
    # The sales' cost side traced like the sales: the audited
    # securities' buys and earlier trades against their parsed rows
    # (backlog TJ-0333).
    _renames = load_dated_renames(Path(args.dated_events)
                                  if args.dated_events else None)

    def _origin(row):
        return row_origin(row, renames=_renames, filled_index=filled_index)
    _money = dict(base_currency=base_currency, fx_history=fx_history,
                  default_rate=_default_rate, futures_native=futures_native,
                  filled_index=filled_index)
    pool = pool_trade_check(events, base_index, source_index,
                            origin=_origin, **_money)
    # ...and the rows that are not trades (ROC, splits, corporate
    # actions, transfers, openings, the securities' income), with the
    # rows the computation adds (missing history's openings): backlog
    # TJ-0359.
    other = pool_other_rows_check(
        events, base_index, source_index, origin=_origin,
        engine_rows=[t.to_dict() for t in transactions
                     if t.id not in base_index], **_money)
    # Unknown-cost dispositions: the books list them under MANUAL
    # REPORTING REQUIRED with no gain; the re-run's figure is fabricated
    # (cost 0 on the synthetic opening). Tie them to that list, never to
    # the gains total (A2-0640, A2-1098).
    # Each event by its own lots' taint: a sale with known-cost and
    # unknown-cost lots is two events under one id (TJ-0310).
    _tainted_ids = {g.get("id") for g in merged if g.get("tainted")}
    for g, e in zip(merged, events):
        if not g.get("tainted"):
            continue
        e["manual_reporting"] = True
        e["warnings"] = [
            w for w in e.get("warnings") or []
            if not w.startswith("disposition not found in the "
                                "pipeline gains file")]
        if not args.check:
            continue
        if e["id"] in manual_index:
            # Its quantity, proceeds and names, as a computed sale's
            # fields are (MANUAL_TIE_FIELDS, backlog TJ-0332).
            _recs = manual_index[e["id"]]
            _diffs = manual_differences(g, _recs)
            e["tie_out"] = {"records": len(_recs),
                            "ties": False if _diffs else None,
                            "manual": True,
                            "mismatched": _diffs}
            if _diffs:
                e["failures"].append(
                    f"MANUAL REPORTING TIE-OUT FAILED: engine re-run vs "
                    f"the saved MANUAL REPORTING REQUIRED row: "
                    f"{differences_text(_diffs)} — the books changed "
                    f"since the last `taxjson run`.")
        else:
            e["tie_out"] = {"records": 0, "ties": None}
            e["warnings"].append(
                "unknown-cost disposition not found under MANUAL "
                "REPORTING REQUIRED in the pipeline gains file(s) — "
                "cannot tie out.")
    if args.check and args.check_year:
        # The saved gains files hold ONE tax year; `audit --year Y`
        # for another year (or --all-years) reported every other
        # year's disposition MISSING — "stale or truncated saved
        # books" on fresh books (R1-191).
        _cy = str(args.check_year)
        for e in events:
            eff = str(e.get(date_key) or e.get("date") or "")
            if e["tie_out"].get("ties") is None \
                    and not eff.startswith(_cy):
                e["tie_out"]["out_of_scope"] = True
                e["warnings"] = [
                    w for w in e.get("warnings") or []
                    if not w.startswith("disposition not found in the "
                                        "pipeline gains file")]

    # The tie-out must catch OMISSIONS and FABRICATIONS, not just
    # per-event drift (2026-09 audit: a check file missing a whole
    # disposition — or carrying an extra invented one — passed with
    # exit 0, which is exactly the stale/corrupted-book failure mode
    # this tool exists to catch). Sweep both directions, the saved side
    # scoped by the same year and row filters as the engine side
    # (row_filter): a symbol/date/id/account filter used to switch the
    # sweep off, so a saved sale missing from the books passed a
    # filtered audit at exit 0 (backlog TJ-0301).
    reconciliation_failures: List[str] = []
    if args.check:
        engine_ids = {e["id"] for e in events}

        def _chk_in_scope(c) -> bool:
            return row_filter(c)

        for gid, recs in sorted(check_index.items()):
            if gid in engine_ids:
                continue
            scoped = [c for c in recs if _chk_in_scope(c)]
            if not scoped:
                continue
            _g = sum(float(c.get("gain") or 0.0) for c in scoped)
            if gid == NOID:
                reconciliation_failures.append(
                    f"check file carries {len(scoped)} disposition(s) "
                    f"with NO id (first: {scoped[0].get('symbol')} "
                    f"{scoped[0].get('date')}; gain {_g:,.2f}) — they "
                    f"cannot be tied to the engine re-run: a hand-edited "
                    f"or corrupted gains file.")
                continue
            reconciliation_failures.append(
                f"check file carries disposition id {gid[:12]} "
                f"({scoped[0].get('symbol')} {scoped[0].get('date')}, "
                f"gain {_g:,.2f}) that the engine re-run did not "
                f"produce — fabricated or double-counted record.")
        for gid, recs in sorted(manual_index.items()):
            if gid in _tainted_ids:
                continue
            scoped = [c for c in recs if _chk_in_scope(c)]
            if scoped:
                reconciliation_failures.append(
                    f"check file lists {scoped[0].get('symbol')} "
                    f"{scoped[0].get('date')} (id {str(gid)[:12]}) under "
                    f"MANUAL REPORTING REQUIRED, but the engine re-run "
                    f"has no unknown-cost disposition with that id.")
        for e in events:
            if e["tie_out"].get("manual"):
                continue
            if e["tie_out"].get("ties") is None and args.check \
                    and not e["tie_out"].get("out_of_scope") \
                    and not e["tie_out"].get("manual"):
                reconciliation_failures.append(
                    f"engine disposition {e['id'][:12]} "
                    f"({e['symbol']} {e['date']}, gain "
                    f"{float(e.get('gain') or 0):,.2f}) is MISSING "
                    f"from the check file(s) — stale or truncated "
                    f"saved books.")
        eng_total = sum(float(e.get("gain") or 0) for e in events
                        if not e["tie_out"].get("out_of_scope")
                        and not e.get("manual_reporting"))
        _chk_rows = [c for recs in check_index.values() for c in recs
                     if _chk_in_scope(c)]
        chk_total = sum(float(c.get("gain") or 0.0) for c in _chk_rows)
        # The saved file rounds each row to 4 dp: a few thousand rows
        # drift past a fixed half-cent even when every row ties
        # (S026-16). Allow the rounding bound, 0.00005 per saved row.
        if abs(eng_total - chk_total) > TIE + 0.00005 * len(_chk_rows):
            reconciliation_failures.append(
                f"in-scope totals do not tie: engine "
                f"{eng_total:,.2f} vs check files {chk_total:,.2f}.")

    # A pool trade whose book amount is not its parsed row's (TJ-0333):
    # the first few named, the rest counted (--json lists each).
    _pool_bad = pool.get("mismatched") or []
    for m in _pool_bad[:5]:
        reconciliation_failures.append(
            f"pool trade {str(m['id'])[:12]} ({m.get('symbol')} "
            f"{m.get('date')}, quantity {float(m.get('quantity') or 0):g})"
            f": {m['failure']}")
    if len(_pool_bad) > 5:
        reconciliation_failures.append(
            f"{len(_pool_bad) - 5:,} more pool trades differ from their "
            f"parsed rows (--json lists each).")
    # ...and a row that is not a trade (TJ-0359), the same way.
    _other_bad = other.get("mismatched") or []
    for m in _other_bad[:5]:
        reconciliation_failures.append(
            f"pool row {str(m['id'])[:12]} ({m.get('action')} "
            f"{m.get('symbol')} {m.get('date')}): {m['failure']}")
    if len(_other_bad) > 5:
        reconciliation_failures.append(
            f"{len(_other_bad) - 5:,} more pool rows differ from their "
            f"parsed rows (--json lists each).")

    failed = any(e["failures"] for e in events) \
        or bool(reconciliation_failures)

    _filters = [f"{k} {v}" for k, v in (
        ("symbol", " ".join(args.symbol or [])), ("--id", args.gain_id),
        ("--date", args.date), ("--account", args.account)) if v]
    if _filters and not events and not reconciliation_failures:
        # An empty "0/0 ✓" reconciliation block read as a clean audit of
        # a typo (2026-09 CLI audit B15). A saved record the filter
        # matches with no engine event is a finding, not "no match".
        if args.no_match_rc == 1:
            print(f"taxjson-audit: no disposition matches "
                  f"{', '.join(_filters)}"
                  + (f" in {args.year}" if args.year else "")
                  + " — check the filter (`--summary` lists every "
                    "event).", file=sys.stderr)
        return args.no_match_rc
    if args.date and events:
        _by_trade = sum(1 for e in events if e.get("date") == args.date)
        emit_line(f"taxjson-audit: note: --date {args.date} matched "
                  f"{len(events)} disposition(s) — {_by_trade} by trade "
                  f"date, {len(events) - _by_trade} by settlement date "
                  f"only.", file=sys.stderr)

    if args.json:
        slim = []
        for e in events:
            d = dict(e)
            d.pop("base_row", None)
            d["trace"] = list(e.get("trace") or [])
            slim.append(d)
        json.dump(_finite({"base_currency": base_currency,
                   "country": country,
                   "events": slim,
                   "total_gain": round(sum(float(e.get("gain") or 0)
                                           for e in events
                                           if not e.get(
                                               "manual_reporting")), 2),
                   "total_disallowed": round(
                       sum(float(e.get("disallowed_amount") or 0)
                           for e in events), 2),
                   "manual_reporting": manual_reporting_totals(events),
                   "totals_note": totals_note(country),
                   "pool_trades": pool,
                   "pool_other_rows": other,
                   "scope": SCOPE,
                   "reconciliation_failures": reconciliation_failures,
                   "failed": failed}),
                  sys.stdout, indent=2, default=str, allow_nan=False)
        print()
        return 1 if failed else 0

    use_color = (not args.no_color and sys.stdout.isatty()
                 and not __import__("os").environ.get("NO_COLOR"))
    paint = _mk_paint(use_color)

    if args.summary:
        _idw = unique_prefix_len([e["id"] for e in events])
        for i, e in enumerate(events, 1):
            flags = ""
            if e["failures"]:
                flags += "  " + paint("FAILED", "bad")
            elif e["warnings"]:
                flags += "  " + paint("warn", "warn")
            if e.get("manual_reporting"):
                flags += "  " + paint("MANUAL", "warn")
            if float(e.get("disallowed_amount") or 0) > TIE:
                flags += "  " + paint(
                    f"WASH+{float(e['disallowed_amount']):.2f}",
                    "warn")
            if isinstance(e.get("loss_override"), dict):
                # A filing position against the rule (.tt ALLOWLOSS).
                flags += "  " + paint(
                    f"ALLOWLOSS(rule: "
                    f"{float(e['loss_override'].get('would_disallow') or 0):.2f})",
                    "warn")
            print(f"{paint((e['id'] or '')[:_idw].ljust(_idw), 'dim')}  "
                  f"{e['date']}  "
                  f"{e['symbol']:<22} qty={float(e['qty'] or 0):>12,.4f} "
                  f"gain={float(e['gain'] or 0):>+14,.2f}{flags}")
    else:
        # Each block printed as it is rendered: holding every block's
        # pool history until the end grew with the square of the sales.
        for i, e in enumerate(events, 1):
            for ln in render_event(e, i, len(events), country,
                                   show_trace=not args.no_trace,
                                   use_color=use_color,
                                   no_wash=args.no_wash):
                print(ln)
            print()

    for ln in render_reconciliation(events, check_labels,
                                    bool(args.check),
                                    use_color=use_color,
                                    country=country,
                                    brief=args.brief, pool=pool,
                                    other=other):
        print(ln)
    from taxjson.lib.out import wrap as _wrap
    for f in reconciliation_failures:
        for ln in _wrap(f"FAILED: {f}", None, "", "  "):
            print(ln)
    if failed:
        print(file=sys.stderr)
        for ln in _wrap("taxjson-audit: CHECKS FAILED — see FAILED lines "
                        "above. The usual cause is stale work/ artifacts; "
                        "re-run `taxjson run` and audit again.", None, "",
                        "  ", stream=sys.stderr):
            print(ln, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
