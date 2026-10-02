#!/usr/bin/env python3
"""
taxjson_ticker_map.py

Usage:
  Summary Mode (No map file):
    taxjson-ticker-map input.json > ticker_map.json
    Generates a mapping of all unique tickers in the input to their defaults.

  Transform Mode (With map file):
    taxjson-ticker-map input.json ticker.map > mapped_input.json
    Applies the mappings from ticker.map to all transactions and outputs a new JSON file.
    Options are mapped by applying the underlying mapping (e.g. AEM.US -> AEM.TO applies to AEM250101C00100000.US -> AEM250101C00100000.TO).
"""

import argparse
import json
import sys
import re
from collections import namedtuple
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.core import TaxTransaction
from taxjson.lib.ticker_map import map_ticker

# A ticker-map line is `KEYWORD from [to]`. One file, four rule types:
#   GLOBAL  from to   — a plain rename; applies in every stage (a
#                       security's true ticker, e.g. DFDV1 -> DFDV).
#   TOBASE  from to   — currency-equivalent consolidation; applies only
#                       when converting to base currency (the main
#                       pipeline). The raw holdings view keeps the two
#                       listings separate.
#   JOURNAL from to   — like TOBASE in the main pipeline, AND nets the
#                       two legs together in the holdings view (a
#                       Norbert's Gambit pair, e.g. DLR.U.TO / DLR.TO).
#   DELETE  from      — nuke that ticker's transactions (a pure artifact).
#   DISTINCT a b      — declares two look-alike listings are SEPARATE
#                       securities (a CDR vs its US underlying); changes
#                       no symbol, silences the scan's MAP-GAP nag.
#   RENAME  from to YYYY-MM-DD [late=fold|late=separate]
#                     — a ticker change on that date: a DATED event
#                       (lib/renames). Without a date it is GLOBAL.
_MAP_KEYWORDS = ("GLOBAL", "TOBASE", "JOURNAL", "DELETE", "DISTINCT",
                 "RENAME")

# Parsed map: glob/tobase/journal are {from: to}; delete is {symbol,...};
# distinct is a set of frozenset pairs the user declares are SEPARATE
# securities despite looking like one (CDRs vs their US underlying:
# UNH.TO is a fractional CAD-hedged receipt over UNH.US, not a listing
# equivalent — pooling their ACB would be wrong). DISTINCT changes no
# symbol; it silences the scan's MAP-GAP nagging for that pair and
# records the judgment in the map file where it belongs.
# dated: the dated RENAME lines (lib/renames.DatedRename), each a ticker
# change booked as an event on its date; undated_rename: the FROM symbols
# of undated RENAME lines (kept in glob — they mean exactly GLOBAL).
TickerMap = namedtuple("TickerMap",
                       ["glob", "tobase", "journal", "delete",
                        "distinct", "dated", "undated_rename"],
                       defaults=((), frozenset()))


def _parse_map_file(file_path: Path):
    """(TickerMap, problems, notes). `problems` are the lines that
    could not be parsed — each rule on them is DROPPED — or that
    contradict another line (one FROM with two targets, a rename
    cycle, a DISTINCT pair the renames join), as
    `<file>:<lineno>: <message>: '<line>'`; `notes` are harmless
    no-op lines (a DISTINCT that pairs a symbol with itself).

    Symbols are upper-cased (the keyword always was): a lower-case
    rule used to match nothing in the pipeline while `taxjson scan`,
    which upper-cases, called it live (S009-05). A BOM and inline
    `# notes` are stripped (R1-139)."""
    glob: Dict[str, str] = {}
    tobase: Dict[str, str] = {}
    journal: Dict[str, str] = {}
    delete = set()
    distinct = set()
    problems: List[str] = []
    notes: List[str] = []
    buckets = {"GLOBAL": glob, "TOBASE": tobase, "JOURNAL": journal,
               "RENAME": glob}
    dated: list = []
    undated_rename = set()
    # from -> (target, where, line) of its first rename rule
    first_rule: Dict[str, tuple] = {}
    distinct_where: Dict[frozenset, tuple] = {}
    from io import StringIO
    from taxjson.lib.cli_diag import read_text_utf8
    # A non-UTF-8 map is a one-line error naming it (S053-06).
    with StringIO(read_text_utf8(file_path)) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            where = f"{file_path.name}:{lineno}"
            parts = line.split()
            kw = parts[0].upper()
            syms = [p.upper() for p in parts[1:]]
            if kw not in _MAP_KEYWORDS:
                problems.append(
                    f"{where}: line has no GLOBAL/TOBASE/JOURNAL/DELETE/"
                    f"DISTINCT/RENAME keyword: {line!r}")
                continue
            if kw == "RENAME" and len(syms) > 2:
                # RENAME OLD NEW YYYY-MM-DD [late=fold|late=separate]
                from taxjson.lib.renames import (DatedRename,
                                                 parse_rename_tail)
                try:
                    _d, _late = parse_rename_tail(parts[3:])
                except ValueError as e:
                    problems.append(
                        f"{where}: RENAME line needs `from to "
                        f"YYYY-MM-DD [late=fold|late=separate]` — {e}: "
                        f"{line!r}")
                    continue
                if syms[0] == syms[1]:
                    notes.append(f"{where}: RENAME renames a symbol to "
                                 f"itself (no effect): {line!r}")
                    continue
                dated.append(DatedRename(syms[0], syms[1], _d, _late,
                                         where, line))
                continue
            want = 1 if kw == "DELETE" else 2
            if len(syms) > want:
                problems.append(
                    f"{where}: {kw} line has extra token(s) "
                    f"{parts[1 + want:]} — notes go after `#`: {line!r}")
                continue
            if kw == "DELETE":
                if syms:
                    delete.add(syms[0])
                else:
                    problems.append(f"{where}: DELETE line needs a "
                                    f"symbol: {line!r}")
            elif kw == "DISTINCT":
                if len(syms) == 2:
                    if syms[0] == syms[1]:
                        notes.append(f"{where}: DISTINCT pairs a symbol "
                                     f"with itself (no effect): {line!r}")
                        continue
                    pair = frozenset(syms)
                    distinct.add(pair)
                    distinct_where.setdefault(pair, (where, line))
                else:
                    problems.append(f"{where}: DISTINCT line needs "
                                    f"`a b`: {line!r}")
            else:
                if len(syms) == 2:
                    frm, to = syms
                    if frm == to:
                        notes.append(f"{where}: {kw} renames a symbol to "
                                     f"itself (no effect): {line!r}")
                        continue
                    prev = first_rule.get(frm)
                    if prev is not None and prev[0] != to:
                        problems.append(
                            f"{where}: {frm} is renamed to {to} here but "
                            f"to {prev[0]} at {prev[1]} ({prev[2]!r}) — "
                            f"one symbol can have only one target: "
                            f"{line!r}")
                        continue
                    first_rule.setdefault(frm, (to, where, line))
                    buckets[kw][frm] = to
                    if kw == "RENAME":
                        undated_rename.add(frm)
                else:
                    problems.append(f"{where}: {kw} line needs `from to` "
                                    f"(two symbols separated by a space): "
                                    f"{line!r}")
    # A dated rename next to an undated rule for the same symbol, or two
    # dated renames of one symbol close together, contradict each other.
    _seen_dated: Dict[str, list] = {}
    for dr in dated:
        if dr.old in glob:
            to, w, ln = first_rule[dr.old]
            problems.append(
                f"{dr.where}: RENAME {dr.old} {dr.new} {dr.date} is "
                f"dated but {dr.old} is also renamed (undated) to {to} at "
                f"{w} ({ln!r}) — an undated rule applies to every row "
                f"at any date; keep one of the two: {dr.line!r}")
        for prev in _seen_dated.get(dr.old, []):
            from taxjson.lib.renames import WINDOW_DAYS, _days
            gap = _days(prev.date, dr.date)
            if gap is not None and gap <= WINDOW_DAYS:
                problems.append(
                    f"{dr.where}: RENAME {dr.old} on {dr.date} repeats "
                    f"the rename of {dr.old} on {prev.date} at "
                    f"{prev.where} — one event, one line: {dr.line!r}")
        _seen_dated.setdefault(dr.old, []).append(dr)
    tmap = TickerMap(glob, tobase, journal, delete, distinct,
                     tuple(dated), frozenset(undated_rename))
    for to_base in (False, True):
        raw = raw_renames(tmap, to_base)
        for frm in sorted(raw):
            cyc = _chain_cycle(frm, raw)
            if cyc and frm == min(cyc):
                wheres = ", ".join(first_rule[s][1] for s in cyc
                                   if s in first_rule)
                msg = (f"rename cycle {' -> '.join(cyc + [cyc[0]])} "
                       f"({wheres}) — a chain of renames must end at "
                       f"one symbol")
                if not any(msg in p for p in problems):
                    problems.append(f"{file_path.name}: {msg}")
    if not any("rename cycle" in p for p in problems):
        for to_base, view in ((False, "GLOBAL"), (True, "base-currency")):
            ren = merge_renames(tmap, to_base)
            for pair in sorted(distinct, key=sorted):
                a, b = sorted(pair)
                if map_symbol(a, ren) == map_symbol(b, ren):
                    w, ln = distinct_where[pair]
                    via = ", ".join(first_rule[s][1] for s in (a, b)
                                    if s in first_rule)
                    msg = (f"{w}: DISTINCT {a} {b} ({ln!r}) contradicts "
                           f"the rename rule(s) at {via}, which pool "
                           f"both into {map_symbol(a, ren)} ({view} "
                           f"view) — delete one of the two statements")
                    if msg not in problems:
                        problems.append(msg)
    return tmap, problems, notes


def _chain_cycle(frm: str, raw: Dict[str, str]):
    """The cycle (list of symbols) reached by following renames from
    `frm`, or None when the chain ends."""
    seen: List[str] = []
    cur = frm
    while cur in raw:
        if cur in seen:
            return seen[seen.index(cur):]
        seen.append(cur)
        cur = raw[cur]
    return None


def raw_renames(tmap: "TickerMap", to_base: bool) -> Dict[str, str]:
    """The stage's rename rules as written (one hop each)."""
    renames = dict(tmap.glob)
    if to_base:
        renames.update(tmap.tobase)
        renames.update(tmap.journal)
    return renames


def map_file_problems(file_path: Path) -> List[str]:
    """The ticker-map lines that cannot be parsed (their rules would be
    dropped), each as `<file>:<lineno>: <message>`. `taxjson run`
    refuses a map with any: a dropped TOBASE/GLOBAL/JOURNAL rule
    silently changes ACB pools and the Schedule 3 gain (S009-03)."""
    return _parse_map_file(file_path)[1]


def load_map_file(file_path: Path) -> "TickerMap":
    """Parse a keyword-prefixed ticker-map file into a TickerMap. A
    malformed line is skipped with a warning naming its line number
    (`taxjson run` refuses such a map up front — map_file_problems)."""
    tmap, problems, notes = _parse_map_file(file_path)
    for msg in problems:
        print(f"warning: ticker.map problem: {msg} (`taxjson run` refuses "
              f"this map)", file=sys.stderr)
    for msg in notes:
        print(f"warning: {msg} — skipping", file=sys.stderr)
    return tmap


def merge_renames(tmap: "TickerMap", to_base: bool) -> Dict[str, str]:
    """Build the symbol-rename dict for a merge stage. GLOBAL renames
    always apply; TOBASE and JOURNAL renames apply only when converting
    to base currency (`to_base`) — pre-gains they'd otherwise create a
    mixed-currency ACB pool, which only the conversion stage resolves.

    Chains resolve to their fixed point: `GLOBAL OLD.US NEW.US` plus
    `TOBASE NEW.US NEW.TO` sends OLD.US to NEW.TO. A one-hop lookup left
    OLD.US's lots in NEW.US — a split pool and a phantom short (R1-139).
    A cycle raises (`taxjson run` refuses such a map up front)."""
    raw = raw_renames(tmap, to_base)
    out: Dict[str, str] = {}
    for frm in raw:
        cyc = _chain_cycle(frm, raw)
        if cyc:
            raise ValueError(
                f"ticker.map: rename cycle "
                f"{' -> '.join(cyc + [cyc[0]])} — a chain of renames "
                f"must end at one symbol")
        cur = raw[frm]
        while cur in raw:
            cur = raw[cur]
        out[frm] = cur
    return out


def apply_drops(transactions: List[TaxTransaction], drops) -> List[TaxTransaction]:
    """Remove transactions whose symbol is in `drops`.

    For each dropped ticker, prints a NOTE — count, net quantity, net
    amount — so the deletion is auditable rather than silent. If a
    dropped ticker's net quantity looks like a real position
    (|net| >= 1 share) a loud warning is printed, since DELETE discards
    its cost basis. Returns the kept transactions."""
    if not drops:
        return transactions

    def _field(tx, name):
        v = tx.get(name) if isinstance(tx, dict) else getattr(tx, name, 0)
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    kept: List[TaxTransaction] = []
    removed: Dict[str, list] = {}
    for tx in transactions:
        sym = tx.get('symbol') if isinstance(tx, dict) else getattr(tx, 'symbol', None)
        new_sym = (tx.get('symbol_new') if isinstance(tx, dict)
                   else getattr(tx, 'symbol_new', None)) or None
        if sym in drops:
            removed.setdefault(sym, []).append(tx)
        elif new_sym and new_sym in drops:
            # A SPLIT-rename TARGETING a dropped ticker would rename a
            # live pool into the dropped identity (whose trades are
            # gone) — the rename row goes with the ticker.
            removed.setdefault(new_sym, []).append(tx)
        else:
            kept.append(tx)

    def _cash(tx):
        """Signed cash of one row: a trade's net_amount is a magnitude
        with the direction in the quantity sign (a buy pays it, a sale
        receives it); income rows carry signed cash; TAX and FEE are
        positive = paid. Summing the raw net_amount added a buy's cost
        to a sale's proceeds (audit S052-23)."""
        act = str((tx.get('action') if isinstance(tx, dict)
                   else getattr(tx, 'action', '')) or '').upper()
        net = _field(tx, 'net_amount')
        if act in ('BUYSELL', 'ASSIGN'):
            return -net if _field(tx, 'quantity') > 0 else net
        if act in ('TAX', 'FEE'):
            return -net
        return net

    for sym in sorted(removed):
        rows = removed[sym]
        net_qty = sum(_field(t, 'quantity') for t in rows)
        net_cash = sum(_cash(t) for t in rows)
        print(
            f"NOTE: ticker-map DELETE removed {len(rows)} {sym} row(s) "
            f"(net qty {net_qty:.4f}, net cash {net_cash:+.2f}).",
            file=sys.stderr,
        )
        if abs(net_qty) >= 1.0:
            print(
                f"warning: dropped ticker {sym} has a net quantity of "
                f"{net_qty:.4f} — that looks like a real position, and "
                f"DELETE discards its cost basis. Remove the `DELETE "
                f"{sym}` line from ticker.map if "
                f"{sym} is not a pure artifact.",
                file=sys.stderr,
            )
    return kept

def generate_summary(transactions: List[TaxTransaction]) -> Dict[str, Any]:
    unique_tickers = set()
    for tx in transactions:
        if tx.symbol:
            unique_tickers.add(tx.symbol)
    
    mappings = {}
    for ticker in sorted(unique_tickers):
        mapped = map_ticker(ticker, "CAD")
        mappings[ticker] = mapped
        
    return {
        "mappings": mappings,
        "total_unique_tickers": len(mappings),
        "generated_at": datetime.now().strftime("%Y-%m-%d")
    }

def bare_rename_target(frm: str, to: str) -> bool:
    """True when a rename takes a listed symbol (a known market suffix:
    RY.TO, XYZ.US) to a bare one (RY). A bare symbol is read as crypto
    or as an unknown listing: a Canadian eligible dividend on RY became
    a foreign dividend with an assumed foreign tax credit (audit
    A2-0304). Options, futures and cash are not judged."""
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    from taxjson.lib.core import is_option_symbol
    frm, to = str(frm or ''), str(to or '')
    if (not frm or not to or '.' in to or to.upper() == 'CASH'
            or is_option_symbol(frm) or is_option_symbol(to)
            or frm.startswith(('F:', '/', '\\'))
            or to.startswith(('F:', '/', '\\'))):
        return False
    return '.' in frm and frm.rsplit('.', 1)[1].upper() in KNOWN_SUFFIXES


def bare_target_warnings(symbols, mapping: Dict[str, str]) -> List[str]:
    """One ATTENTION text per rename rule that takes a listed symbol the
    book holds to a bare symbol (bare_rename_target)."""
    held = set(symbols)
    return [f"ticker.map: {frm} -> {to}: the target has no market suffix "
            f"— a bare symbol is read as crypto / an unknown listing (a "
            f"Canadian dividend on it is counted as foreign). Write the "
            f"listing ({to}.TO, {to}.US)."
            for frm, to in sorted(mapping.items())
            if frm in held and bare_rename_target(frm, to)]


def map_symbol(symbol: str, mapping: Dict[str, str]) -> str:
    """The rename a mapping implies for ONE symbol — exact match first,
    else options map through their UNDERLYING (AEM.US -> AEM.TO also
    renames AEM260116C00150000.US -> ...TO). Pure-string twin of
    apply_mapping so non-transaction consumers (taxjson sanity) apply
    the same consolidation the pipeline does."""
    if symbol in mapping:
        return mapping[symbol]
    # Option check (e.g. AEM260116C00150000.US or F:CL251220P00053000.US)
    match = re.match(r'^((?:F:)?[A-Z0-9\.]+?)(\d{6}[CP]\d+)\.(\S+)$',
                     symbol, re.IGNORECASE)
    if match:
        underlying, contract, ext = match.groups()
        lookup = f"{underlying}.{ext}"
        if lookup in mapping:
            target_full = mapping[lookup]
            if '.' in target_full:
                target_underlying, target_ext = target_full.rsplit('.', 1)
            else:
                target_underlying, target_ext = target_full, ext
            return f"{target_underlying}{contract}.{target_ext}"
    return symbol


def guard_option_listing_collisions(symbols, mapping: Dict[str, str],
                                    prog: str = "taxjson-ticker-map"
                                    ) -> Dict[str, str]:
    """`mapping` plus an identity rule for every OPTION the underlying
    rule would move onto a contract code the book already carries
    under that code natively (R1-16). `TOBASE BCE.US BCE.TO` renames
    BCE270115C00025000.US (a USD-strike OCC contract) to ...TO — when
    the book also trades the Montreal BCE270115C00025000.TO (a
    CAD-strike CDCC contract), the rename pooled two different
    properties' ACB (not identical under s.47/s.54) and changed the
    reported gain without a word. The US contract now keeps its own
    symbol, and each one is named on stderr. An exact rule for the
    option symbol itself still wins (it is the user's explicit
    choice)."""
    natives = set(s for s in symbols if s)
    out = dict(mapping)
    for sym in sorted(natives):
        if sym in mapping:
            continue                    # explicit rule for this symbol
        target = map_symbol(sym, mapping)
        if target != sym and target in natives:
            out[sym] = sym
            print(f"{prog}: warning: the underlying rule would rename option "
                  f"{sym} to {target}, a different listed contract this book "
                  f"also trades (strike currency and clearing house differ, so "
                  f"they are not identical property) — kept separate as {sym}. "
                  f"Add an exact rule for {sym} in ticker.map if they really "
                  f"are one contract.", file=sys.stderr)
    return out


def apply_mapping(tx: TaxTransaction, mapping: Dict[str, str]) -> TaxTransaction:
    mapped_symbol = map_symbol(tx.symbol, mapping)
    if mapped_symbol != tx.symbol:
        tx.symbol = mapped_symbol
    # A SPLIT-rename's TARGET must consolidate under the same identity
    # as the trades it renames INTO. Mapping only tx.symbol left e.g.
    # `HES.US -> symbol_new=CVX.US` pointing at an orphan CVX.US pool
    # while the later CVX trades mapped to CVX.TO — the engine renamed
    # the basis into a pool no sale ever draws from (phantom short +
    # stranded ACB).
    new_sym = getattr(tx, 'symbol_new', '') or ''
    if new_sym:
        mapped_new = map_symbol(new_sym, mapping)
        if mapped_new != new_sym:
            tx.symbol_new = mapped_new
    return tx

@guard_main("taxjson-ticker-map")
def main():
    parser = argparse.ArgumentParser(description="Map tickers in a tax.json file")
    parser.add_argument("input", help="Input JSON file with transactions")
    parser.add_argument("map_file", nargs="?", help="Optional map file (ticker.map). If provided, outputs updated JSON.")
    parser.add_argument("--map", dest="map_flag", default=None,
                        help="Same as the positional map file (for "
                             "callers that prefer a flag).")
    parser.add_argument("--global-only", action="store_true",
                        help="Apply only GLOBAL renames and DELETEs — "
                             "the crypto pipeline's subset (TOBASE/"
                             "JOURNAL assume exchange-suffixed "
                             "cross-listings).")
    args = parser.parse_args()
    if args.map_flag and args.map_file:
        # Two map sources: the positional one used to win silently
        # (audit S053-00).
        print("taxjson-ticker-map: error: give the map once — either "
              "the positional MAP_FILE or --map, not both",
              file=sys.stderr)
        sys.exit(2)
    if args.map_flag:
        args.map_file = args.map_flag

    from taxjson.lib.json_input import load_transactions_or_exit
    transactions = load_transactions_or_exit("taxjson-ticker-map",
                                             args.input)

    if not args.map_file:
        # Summary Mode
        output_data = generate_summary(transactions)
        json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        # Transform Mode — standalone tool applies the full map
        # (GLOBAL + TOBASE + JOURNAL renames, plus DELETE).
        tmap = load_map_file(Path(args.map_file))
        mapping = merge_renames(tmap,
                                to_base=not args.global_only)
        # DELETE first, then rename: a DELETE line names the broker's
        # RAW symbol. taxjson-merge2 applies the same order so one map
        # file means one thing on the equity and crypto paths.
        transactions = apply_drops(transactions, tmap.delete)
        # Dated RENAME lines are events (lib/renames), booked on the raw
        # symbols like taxjson-merge2 does.
        from taxjson.lib.renames import RenameConflict, apply_dated_renames
        try:
            transactions = apply_dated_renames(transactions, tmap.dated)
        except RenameConflict as e:
            print(f"taxjson-ticker-map: error: {e}", file=sys.stderr)
            sys.exit(1)
        mapping = guard_option_listing_collisions(
            [t.symbol for t in transactions], mapping)
        for _w in bare_target_warnings([t.symbol for t in transactions],
                                       mapping):
            print(f"warning: ATTENTION: {_w}", file=sys.stderr)
        updated_transactions = []

        for tx in transactions:
            # We copy to avoid mutating the original if we cared, but we're just outputting
            updated_tx = apply_mapping(tx, mapping)
            updated_transactions.append(updated_tx.to_dict())

        output_data = {
            "transactions": updated_transactions,
            "metadata": {
                "source_file": args.input,
                "map_file": args.map_file,
                "mapped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
        print()

if __name__ == "__main__":
    main()
