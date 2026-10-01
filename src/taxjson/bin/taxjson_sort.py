#!/usr/bin/env python3
"""
taxjson_sort.py

Sort transactions by date/time and optionally deduplicate.

Usage:
    python -m taxjson.bin.taxjson_sort input.json [--dedup]

The script reads transactions from JSON, sorts them chronologically, and writes
the result to stdout. Optionally deduplicates by transaction ID or synthetic UID.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.core import TaxTransaction, load_transactions

PROG = "taxjson-sort"


def generate_uid(tx: TaxTransaction) -> str:
    """Generate a unique ID for a transaction."""
    if getattr(tx, "id", None):
        return tx.id
    
    parts = [
        getattr(tx, "brokerage", "unknown"),
        getattr(tx, "account", "default"),
        getattr(tx, "symbol", "unknown"),
        getattr(tx, "date", "unknown"),
        getattr(tx, "time", "00:00:00"),
    ]
    return "_".join(str(p) for p in parts)


def validate_transactions(transactions: List[TaxTransaction]) -> Tuple[List[TaxTransaction], List[str]]:
    """Validate with the SHARED schema validator
    (lib/brokerages/schema.validate_transactions) and return
    `(transactions, error_messages)` — every row, always.

    The private validator this replaced required a symbol on EVERY
    action and a currency of exactly 3 characters, then the non-strict
    CLI path silently DROPPED each failing row: a Coinbase row priced
    in 'USDC', or a TAX/INTEREST row without a symbol, vanished from
    the crypto pipeline (which runs `taxjson-sort --dedup` with no
    --strict) — and the "Validation error:" stderr line did not match
    the pipeline's diag-marker format, so the .sum never showed the
    drop (stage-tools audit). Rows are never dropped now; the caller
    decides between warning (default) and abort (--strict)."""
    from taxjson.lib.brokerages.schema import (
        validate_transactions as _schema_validate,
    )
    errors, _warnings = _schema_validate([tx.to_dict() for tx in transactions])
    return list(transactions), errors


def sort_transactions(transactions: List[TaxTransaction]) -> List[TaxTransaction]:
    """Sort transactions by date and time."""
    def sort_key(tx):
        date_str = getattr(tx, "date", "9999-99-99")
        time_str = getattr(tx, "time", "00:00:00")
        return (date_str, time_str)
    
    return sorted(transactions, key=sort_key)


# --------------------------------------------------------------- dedup
#
# The one definition of "duplicate row", shared by the books
# (taxjson-sort --dedup, taxjson-merge2 --dedup) and the fee report
# (taxjson-fees / fees-sum), so the two can never disagree (audit
# R1-296, S031-02).
#
# Rows carry their input file in `source` (taxjson-brokerage, convert-
# tt). Two rows with one content id are:
#   * from ONE file: collapsed (a parser marks a genuine repeated fill
#     "[fill #N]", which changes the id, so a same-id pair inside one
#     file is the file repeating itself);
#   * from statements of DIFFERENT broker accounts (the parse records
#     each file's account set, hashed, in metadata.source_accounts):
#     separate trades, both booked;
#   * from two hand-kept .tt files: separate records, both booked, with
#     an ATTENTION line (delete one if it is one trade typed twice);
#   * from two exports that overlap as copies (within the dates both
#     files cover, one file's rows are a subset of the other's, and
#     they share at least two rows): the same row exported twice, kept
#     once;
#   * otherwise (a .tt line equal to an exported row, or two exports
#     whose overlap is too thin to confirm, or that disagree inside it):
#     AMBIGUOUS — kept once, as the re-export reading, with an ATTENTION
#     line naming both files and the row.
# Rows without a `source` (hand-made JSON, generated corp-action rows)
# keep the old id-only rule.

TT_SUFFIX = ".tt"
# ATTENTION lines show this many example rows per file pair.
_DEDUP_EXAMPLES = 3


def _row_get(tx, key, default=None):
    if isinstance(tx, dict):
        return tx.get(key, default)
    return getattr(tx, key, default)


def _row_uid(tx):
    """The dedup identity: the row's id (a dict row without one is never
    collapsed — None)."""
    if isinstance(tx, dict):
        return tx.get("id") or None
    return generate_uid(tx)


def _row_brief(tx) -> str:
    qty = _row_get(tx, "quantity", 0) or 0
    try:
        qty = f"{float(qty):g}"
    except (TypeError, ValueError):
        pass
    return (f"{_row_get(tx, 'date', '?')} {_row_get(tx, 'action', '?')} "
            f"{_row_get(tx, 'symbol', '') or '-'} {qty}")


class DedupPlan:
    """What dedup does to a row list: `keep`/`drop` (indices, input
    order), `relabel` (index -> new id for a row kept although another
    kept row has its id: `<id>~<n>`, the engine's convention), and the
    `attention` / `notes` lines to print."""

    def __init__(self):
        self.keep: List[int] = []
        self.drop: List[int] = []
        self.relabel: Dict[int, str] = {}
        self.attention: List[str] = []
        self.notes: List[str] = []


def plan_dedup(rows, source_accounts: Optional[Dict[str, Any]] = None
               ) -> DedupPlan:
    """Decide, for every row (TaxTransaction or dict), whether it is a
    duplicate. See the block comment above for the rules."""
    source_accounts = source_accounts or {}
    plan = DedupPlan()
    uids = [_row_uid(t) for t in rows]
    srcs = [str(_row_get(t, "source", "") or "") for t in rows]

    groups: Dict[Any, List[int]] = {}
    for i, uid in enumerate(uids):
        if uid is not None:
            groups.setdefault(uid, []).append(i)

    # Per-source (date, uid) index, built only when two sources collide.
    _by_src: Dict[str, Dict[str, Set[Any]]] = {}

    def src_index(src):
        if not _by_src:
            for j, s in enumerate(srcs):
                if s and uids[j] is not None:
                    d = str(_row_get(rows[j], "date", "") or "")
                    _by_src.setdefault(s, {}).setdefault(d, set()).add(
                        uids[j])
        return _by_src.get(src, {})

    verdicts: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def verdict(a: str, b: str) -> Tuple[str, str]:
        key = (a, b) if a <= b else (b, a)
        if key in verdicts:
            return verdicts[key]
        why = ""
        ta = a.lower().endswith(TT_SUFFIX)
        tb = b.lower().endswith(TT_SUFFIX)
        acc_a = set(source_accounts.get(a) or ())
        acc_b = set(source_accounts.get(b) or ())
        if not a or not b:
            v = "copy"
        elif ta and tb:
            v = "distinct-ambiguous"
        elif acc_a and acc_b and not acc_a & acc_b:
            v = "distinct"
        elif ta or tb:
            v = "copy-ambiguous"
            why = "a hand-kept .tt line equals an exported row"
        else:
            ia, ib = src_index(a), src_index(b)
            lo = max(min(ia), min(ib))
            hi = min(max(ia), max(ib))
            in_a = set().union(*(u for d, u in ia.items() if lo <= d <= hi))
            in_b = set().union(*(u for d, u in ib.items() if lo <= d <= hi))
            shared = in_a & in_b
            if (in_a <= in_b or in_b <= in_a) and len(shared) >= 2:
                v = "copy"
            elif len(shared) < 2:
                v = "copy-ambiguous"
                why = (f"the dates both files cover ({lo}..{hi}) hold "
                       f"only this one common row — too little overlap "
                       f"to confirm a re-export")
            else:
                v = "copy-ambiguous"
                why = (f"the files disagree on the dates both cover "
                       f"({lo}..{hi}): each has rows the other lacks")
        verdicts[key] = (v, why)
        return v, why

    # (a, b, verdict) -> example rows
    events: Dict[Tuple[str, str, str], List[int]] = {}
    keep_set: Set[int] = set()
    for uid, idx in groups.items():
        kept: List[int] = []
        for i in idx:
            if not kept:
                kept.append(i)
                continue
            s = srcs[i]
            if any(srcs[k] == s for k in kept):
                plan.drop.append(i)
                continue
            found = [(k, verdict(srcs[k], s)) for k in kept]
            copies = [(k, v) for k, v in found
                      if v[0] in ("copy", "copy-ambiguous")]
            if copies:
                k, (v, _why) = copies[0]
                plan.drop.append(i)
                if v == "copy-ambiguous":
                    events.setdefault((srcs[k], s, v), []).append(i)
                continue
            kept.append(i)
            plan.relabel[i] = f"{uid}~{len(kept)}"
            k, (v, _why) = found[0]
            events.setdefault((srcs[k], s, v), []).append(i)
        keep_set.update(kept)
    for i, uid in enumerate(uids):
        if uid is None or i in keep_set:
            plan.keep.append(i)
    plan.drop.sort()

    for (a, b, v), ex in events.items():
        sample = "; ".join(_row_brief(rows[i])
                           for i in ex[:_DEDUP_EXAMPLES])
        more = (f"; +{len(ex) - _DEDUP_EXAMPLES} more"
                if len(ex) > _DEDUP_EXAMPLES else "")
        n = len(ex)
        if v == "copy-ambiguous":
            plan.attention.append(
                f"dedup: {a} and {b} both hold {n} identical row(s) "
                f"({sample}{more}) — {verdicts[(a, b) if a <= b else (b, a)][1]}. "
                f"Booked ONCE (read as the same row exported twice). If "
                f"they are separate trades, book the missing one as a "
                f".tt line.")
        elif v == "distinct-ambiguous":
            plan.attention.append(
                f"dedup: {a} and {b} both hold {n} identical line(s) "
                f"({sample}{more}). Hand-kept .tt files are separate "
                f"records, so each line is booked. If it is one trade "
                f"entered in both files, delete one line.")
        else:
            plan.notes.append(
                f"dedup: {a} and {b} are statements of different broker "
                f"accounts; {n} identical row(s) in them ({sample}{more}) "
                f"are booked separately.")
    return plan


def deduplicate(transactions: List[TaxTransaction], return_dropped: bool = False,
                *, source_accounts: Optional[Dict[str, Any]] = None,
                report=None):
    """Remove duplicate transactions (rules: `plan_dedup`).

    With `return_dropped=True` returns `(kept, dropped)` so callers can
    report which rows collapsed (useful when --dedup removes more rows
    than expected and the user needs to audit which broker/account/date
    combinations are colliding). The default-False signature preserves
    backwards compatibility with the bare `deduplicate(txs)` callers.
    `report`, when given, is a file the ATTENTION/note lines go to.
    A row kept although another kept row shares its id (separate
    records) gets the id `<id>~<n>`.
    """
    plan = plan_dedup(transactions, source_accounts)
    for i, new_id in plan.relabel.items():
        transactions[i].id = new_id
    kept = [transactions[i] for i in plan.keep]
    dropped = [transactions[i] for i in plan.drop]
    if report is not None:
        for line in plan.attention:
            print(f"warning: ATTENTION: {line}", file=report)
        for line in plan.notes:
            print(f"note: {line}", file=report)

    if return_dropped:
        return kept, dropped
    return kept


def source_accounts_of(metadata_blocks) -> Dict[str, List[str]]:
    """Merge the `source_accounts` maps (input file -> hashed broker
    account ids) of parsed-source metadata blocks."""
    out: Dict[str, List[str]] = {}
    for meta in metadata_blocks:
        if isinstance(meta, dict):
            for f, accts in (meta.get("source_accounts") or {}).items():
                out.setdefault(str(f), [])
                out[str(f)] = sorted(set(out[str(f)]) | set(accts or ()))
    return out


@guard_main("taxjson-sort")
def main():
    parser = argparse.ArgumentParser(description="Sort and optionally deduplicate transactions")
    parser.add_argument("input", nargs="?", help="Input JSON file (default: stdin)")
    parser.add_argument("--dedup", action="store_true", help="Remove duplicate transactions")
    parser.add_argument("--strict", action="store_true", help="Fail on validation errors")
    parser.add_argument("--no-validation", action="store_true", help="Skip validation")
    args = parser.parse_args()

    # Load input through the shared loader funnel (file: core.load_
    # transactions; stdin: the pipeline's stdin loader) — `#` comments,
    # qty alias, type guards; a malformed row is a clean error.
    try:
        if args.input:
            transactions = load_transactions(Path(args.input))
        else:
            from taxjson.lib.pipeline import load_stdin_transactions
            transactions = load_stdin_transactions()
    except ValueError as e:
        print(f"{PROG}: error: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate transactions (unless disabled). NEVER drops a row:
    # without --strict each schema error is a `<prog>: warning:` line
    # (the pipeline's diag-marker format, so it lands in the .sum) and
    # the row flows on; with --strict they are errors and we abort.
    if not args.no_validation:
        transactions, errors = validate_transactions(transactions)
        if errors:
            level = 'error' if args.strict else 'warning'
            for err in errors:
                print(f"{PROG}: {level}: validation: {err}", file=sys.stderr)
            if args.strict:
                print(f"{PROG}: error: aborting due to {len(errors)} "
                      f"validation error(s) (--strict)", file=sys.stderr)
                sys.exit(1)
            print(f"{PROG}: warning: {len(errors)} validation error(s) "
                  f"above; every row was KEPT (no row is dropped by "
                  f"validation) — fix the input or run with --strict "
                  f"to abort.", file=sys.stderr)
    
    # Sort first, then dedup. Dedup keeps the *first* occurrence of each
    # UID — if it ran before sort, the survivor depended on input-file
    # ordering rather than chronology. Re-running over a re-ordered
    # concatenation of the same data could then keep a different row.
    # `taxjson-merge2` has always done sort→dedup; this brings the bare
    # `taxjson-sort --dedup` into the same order.
    transactions = sort_transactions(transactions)

    if args.dedup:
        transactions = deduplicate(transactions, report=sys.stderr)

    # Sort is a passthrough — preserve full input precision so downstream
    # calculations don't see truncated values.
    output_data = {"transactions": [tx.to_dict() for tx in transactions]}

    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()  # Add final newline


if __name__ == "__main__":
    main()
