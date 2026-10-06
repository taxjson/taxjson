#!/usr/bin/env python3
"""
taxjson_sort.py

Sort transactions by date/time and optionally deduplicate.

Usage:
    python -m taxjson.bin.taxjson_sort input.json [--dedup]

The script reads transactions from JSON, sorts them chronologically, and writes
the result to stdout. Optionally deduplicates by transaction ID or synthetic UID.
"""

from taxjson.lib.stage_msg import emit_line
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from taxjson.lib.brokerages.base import source_identity
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
    # One identity per input file: the shown (masked) name plus, when
    # the name was masked, its key — two files whose names differ only
    # in an account-number token are two sources (audit A2-0159). The
    # messages name the shown name only.
    srcs = [source_identity(_row_get(t, "source", ""),
                            _row_get(t, "source_key", "")) for t in rows]
    raccts = [str(_row_get(t, "source_account", "") or "") for t in rows]
    shown = {s: str(_row_get(t, "source", "") or "")
             for s, t in zip(srcs, rows)}

    def is_tt(s: str) -> bool:
        return shown.get(s, s).lower().endswith(TT_SUFFIX)

    def accts_of(i: int) -> Set[str]:
        """The broker accounts row i can belong to: its own (stamped per
        row by taxjson-brokerage), else its file's set."""
        if raccts[i]:
            return {raccts[i]}
        return set(source_accounts.get(srcs[i]) or ())

    groups: Dict[Any, List[int]] = {}
    for i, uid in enumerate(uids):
        if uid is not None:
            groups.setdefault(uid, []).append(i)

    # Per-source (date, uid) index and (source, uid) -> first row, built
    # only when two sources collide.
    _by_src: Dict[str, Dict[str, Set[Any]]] = {}
    _first: Dict[Tuple[str, Any], int] = {}

    def src_index(src):
        if not _by_src:
            for j, s in enumerate(srcs):
                if s and uids[j] is not None:
                    d = str(_row_get(rows[j], "date", "") or "")
                    _by_src.setdefault(s, {}).setdefault(d, set()).add(
                        uids[j])
                    _first.setdefault((s, uids[j]), j)
        return _by_src.get(src, {})

    def only_in(src, uids_only) -> str:
        ex = sorted(_row_brief(rows[_first[(src, u)]]) for u in uids_only
                    if (src, u) in _first)
        more = (f"; +{len(ex) - _DEDUP_EXAMPLES} more"
                if len(ex) > _DEDUP_EXAMPLES else "")
        return "; ".join(ex[:_DEDUP_EXAMPLES]) + more

    verdicts: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def verdict(a: str, b: str) -> Tuple[str, str]:
        key = (a, b) if a <= b else (b, a)
        if key in verdicts:
            return verdicts[key]
        why = ""
        ta, tb = is_tt(a), is_tt(b)
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
            elif len(shared) < 2 and not (in_a - in_b and in_b - in_a):
                v = "copy-ambiguous"
                why = (f"the dates both files cover ({lo}..{hi}) hold "
                       f"only this one common row — too little overlap "
                       f"to confirm a re-export")
            else:
                # Each file has rows the other lacks on the dates both
                # cover: name them — a newer statement of one account
                # that RESTATED a row (a commission refund folded into
                # the trade, a Ca-cancelled trade) leaves the older
                # version booked beside it (audit A2-0108).
                x, y = sorted((a, b))
                ix, iy = (in_a, in_b) if x == a else (in_b, in_a)
                v = "copy-ambiguous"
                why = (f"the files disagree on the dates both cover "
                       f"({lo}..{hi}): only in {shown.get(x, x)}: "
                       f"{only_in(x, ix - iy)}; only in {shown.get(y, y)}: "
                       f"{only_in(y, iy - ix)} — those rows are ALL "
                       f"booked. If one file is a newer statement of the "
                       f"same account that restated a row (a commission "
                       f"refund folded in, a cancelled trade), keep only "
                       f"the newer file")
        verdicts[key] = (v, why)
        return v, why

    def row_verdict(k: int, i: int) -> Tuple[str, str]:
        """Rows of two DIFFERENT broker accounts are never one row
        (audit A2-0008): checked per row before the file-level rule."""
        ak, ai = accts_of(k), accts_of(i)
        if ak and ai and not ak & ai and not is_tt(srcs[k]) \
                and not is_tt(srcs[i]):
            return ("distinct", "")
        if srcs[k] == srcs[i]:
            return ("copy", "")
        return verdict(srcs[k], srcs[i])

    # (a, b, verdict) -> example rows
    events: Dict[Tuple[str, str, str], List[int]] = {}
    keep_set: Set[int] = set()
    for uid, idx in groups.items():
        # One canonical order, so the kept set never depends on the
        # order the files were listed in (audit A2-0105, A2-0624):
        # exported rows first (by file name), hand-kept .tt lines last.
        order = sorted(idx, key=lambda i: (is_tt(srcs[i]), srcs[i], i))
        kept: List[int] = []        # exported (or sourceless) rows
        tt_kept: List[int] = []     # .tt lines booked as their own record
        claimed: Dict[int, int] = {}   # exported row -> .tt line it absorbed

        def keep(i: int, against: Optional[int], v: str) -> None:
            n = len(kept) + len(tt_kept)
            if n:
                plan.relabel[i] = f"{uid}~{n + 1}"
            if against is not None and srcs[against] != srcs[i]:
                events.setdefault((srcs[against], srcs[i], v), []).append(i)

        for i in order:
            s = srcs[i]
            if not is_tt(s):
                found = [(k, row_verdict(k, i)) for k in kept]
                copies = [(k, v) for k, v in found
                          if v[0] in ("copy", "copy-ambiguous")]
                if copies:
                    k, (v, _why) = copies[0]
                    plan.drop.append(i)
                    if v == "copy-ambiguous":
                        events.setdefault((srcs[k], s, v), []).append(i)
                    continue
                keep(i, found[0][0] if found else None,
                     found[0][1][0] if found else "")
                kept.append(i)
                continue
            # A hand-kept .tt line: the same file repeating itself is
            # one line; otherwise it stands for ONE exported row (a .tt
            # line equal to an exported row is that row) — each exported
            # row absorbs at most one .tt line, and .tt lines left over
            # are records of their own (two .tt files are two records).
            if any(srcs[k] == s for k in tt_kept) or \
                    any(srcs[t] == s for t in claimed.values()):
                plan.drop.append(i)
                continue
            free = [k for k in kept if k not in claimed]
            if free:
                k = free[0]
                claimed[k] = i
                plan.drop.append(i)
                if srcs[k]:     # a row without a source: the id rule
                    verdict(srcs[k], s)
                    events.setdefault((srcs[k], s, "copy-ambiguous"),
                                      []).append(i)
                continue
            other = (tt_kept[0] if tt_kept
                     else next(iter(claimed.values()), None))
            if other is not None:
                verdict(srcs[other], s)
            keep(i, other, "distinct-ambiguous")
            tt_kept.append(i)
        keep_set.update(kept)
        keep_set.update(tt_kept)
    for i, uid in enumerate(uids):
        if uid is None or i in keep_set:
            plan.keep.append(i)
    plan.drop.sort()

    for (ka, kb, v), ex in events.items():
        a, b = shown.get(ka, ka), shown.get(kb, kb)
        if a == b:      # two files shown alike (masked account tokens)
            a, b = f"{a} (one file)", f"{b} (another file)"
        sample = "; ".join(_row_brief(rows[i])
                           for i in ex[:_DEDUP_EXAMPLES])
        more = (f"; +{len(ex) - _DEDUP_EXAMPLES} more"
                if len(ex) > _DEDUP_EXAMPLES else "")
        n = len(ex)
        if v == "copy-ambiguous":
            plan.attention.append(
                f"dedup: {a} and {b} both hold {n} identical row(s) "
                f"({sample}{more}) — {verdicts[(ka, kb) if ka <= kb else (kb, ka)][1]}. "
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
    plan.attention.extend(_tt_near_duplicates(
        rows, [shown.get(x, x) for x in srcs], plan.keep,
        lambda x: x.lower().endswith(TT_SUFFIX)))
    return plan


def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _tt_near_duplicates(rows, srcs, keep, is_tt) -> List[str]:
    """A hand-kept .tt line that repeats an exported row never has the
    row's id (the export's description, settle date and gross differ),
    so the id rule above never sees the pair and BOTH are booked. Name
    each kept .tt line whose action, symbol, currency, quantity and
    money equal a kept exported row's on the same trade or settle date
    (audit A2-0295) — warn only: the user decides."""
    exports: Dict[Tuple[str, str, str, float], List[int]] = {}
    tts: List[Tuple[int, Tuple[str, str, str, float]]] = []
    for i in keep:
        s = srcs[i]
        if not s:
            continue
        key = (str(_row_get(rows[i], "action", "") or "").upper(),
               str(_row_get(rows[i], "symbol", "") or "").upper(),
               str(_row_get(rows[i], "currency", "") or "").upper(),
               round(_num(_row_get(rows[i], "quantity", 0)), 6))
        if is_tt(s):
            tts.append((i, key))
        else:
            exports.setdefault(key, []).append(i)
    out: List[str] = []
    if not exports:
        return out
    for i, key in tts:
        t = rows[i]
        t_dates = {str(_row_get(t, "date", "") or ""),
                   str(_row_get(t, "date_settle", "") or "")} - {""}
        t_net = _num(_row_get(t, "net_amount", 0))
        for j in exports.get(key, ()):
            e = rows[j]
            e_dates = {str(_row_get(e, "date", "") or ""),
                       str(_row_get(e, "date_settle", "") or "")} - {""}
            e_net = _num(_row_get(e, "net_amount", 0))
            if not t_dates & e_dates:
                continue
            if abs(t_net - e_net) > max(0.01, 0.001 * abs(e_net)):
                continue
            out.append(
                f"dedup: {srcs[i]} line ({_row_brief(t)}, "
                f"{t_net:,.2f}) repeats the exported row in {srcs[j]} "
                f"({_row_brief(e)}, {e_net:,.2f}) — same symbol, "
                f"quantity and money on the same trade/settle date, but "
                f"not the same id, so BOTH are booked. If the .tt line "
                f"is that trade, delete it.")
            break
    return out


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
            emit_line(f"warning: ATTENTION: {line}", file=report)
        for line in plan.notes:
            emit_line(f"note: {line}", file=report)

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


def _doc_source_accounts(text: str) -> Dict[str, List[str]]:
    """metadata.source_accounts of a stage document and of every source
    it merged (metadata.sources[].original_metadata); {} when absent or
    unreadable (the rows' own source_account still applies)."""
    from taxjson.lib.core import strip_json_comments
    try:
        doc = json.loads(strip_json_comments(text))
    except ValueError:
        return {}
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    if not isinstance(meta, dict):
        return {}
    blocks = [meta] + [s.get("original_metadata")
                       for s in (meta.get("sources") or ())
                       if isinstance(s, dict)]
    return source_accounts_of(blocks)


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
    meta_accounts: Dict[str, List[str]] = {}
    try:
        if args.input:
            transactions = load_transactions(Path(args.input))
            if args.dedup:
                meta_accounts = _doc_source_accounts(
                    Path(args.input).read_text(encoding="utf-8"))
        else:
            import io
            from taxjson.lib.pipeline import load_stdin_transactions
            from taxjson.lib.cli_diag import read_stdin_utf8
            _stdin = read_stdin_utf8()      # UTF-8 whatever the locale
            transactions = load_stdin_transactions(io.StringIO(_stdin))
            if args.dedup:
                meta_accounts = _doc_source_accounts(_stdin)
    except ValueError as e:
        emit_line(f"{PROG}: error: {e}")
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
                emit_line(f"{PROG}: {level}: validation: {err}")
            if args.strict:
                emit_line(f"{PROG}: error: aborting due to {len(errors)} "
                          f"validation error(s) (--strict)")
                sys.exit(1)
            emit_line(f"{PROG}: warning: {len(errors)} validation error(s) "
                      f"above; every row was KEPT (no row is dropped by "
                      f"validation) — fix the input or run with --strict "
                      f"to abort.")
    
    # Sort first, then dedup. Dedup keeps the *first* occurrence of each
    # UID — if it ran before sort, the survivor depended on input-file
    # ordering rather than chronology. Re-running over a re-ordered
    # concatenation of the same data could then keep a different row.
    # `taxjson-merge2` has always done sort→dedup; this brings the bare
    # `taxjson-sort --dedup` into the same order.
    transactions = sort_transactions(transactions)

    if args.dedup:
        # The per-file broker accounts the parse recorded (kept by
        # taxjson-merge under metadata.sources[].original_metadata):
        # the same rule merge2 and the fee report apply (A2-0297).
        transactions = deduplicate(transactions, report=sys.stderr,
                                   source_accounts=meta_accounts)

    # Sort is a passthrough — preserve full input precision so downstream
    # calculations don't see truncated values.
    output_data = {"transactions": [tx.to_dict() for tx in transactions]}

    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()  # Add final newline


if __name__ == "__main__":
    main()
