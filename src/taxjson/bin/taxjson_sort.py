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
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from taxjson.lib.brokerages.base import source_identity
from taxjson.lib.cli_diag import InputContentError, guard_main
from taxjson.lib import out as _out
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.tx_load import load_transactions
from taxjson.model import money as M

PROG = "taxjson-sort"


def generate_uid(tx: TaxTransaction) -> str:
    """The row's dedup identity: its id, else (no id, or a blank one) its
    content id (`compute_id`: action, quantity and money too). The
    account/symbol/date/time fallback made two different trades of one
    moment one (backlog TJ-5041); it is kept for a row with no
    compute_id."""
    _id = getattr(tx, "id", None)
    if _id and str(_id).strip():
        return _id
    if callable(getattr(tx, "compute_id", None)):
        return tx.compute_id()
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
#     "[fill #N]", which changes the id, and a parser that keeps the
#     broker's own id refuses one id on two DIFFERENT rows —
#     brokerages/_crypto_common.RowIds, audit 2026-10-25 robustness#4 —
#     so a same-id pair inside one file is the file repeating itself);
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
# keep the old id-only rule: the id carries the corporate event and a
# rename's destination (model/transaction `event_identity`), so two
# events' rows are never one id, and the copies of one event still are.
# Two renames of one holding on one day (or within the split-date
# window) to DIFFERENT symbols are no copies and cannot both be right:
# `rename_conflicts`, refused by `deduplicate` (backlog TJ-4985), or by
# taxjson-merge2 after its ticker.map mapping (TJ-5283).

TT_SUFFIX = ".tt"
# ATTENTION lines show this many example rows per file pair.
_DEDUP_EXAMPLES = 3


def _row_get(tx: Any, key: str, default: Any = None) -> Any:
    if isinstance(tx, dict):
        return tx.get(key, default)
    return getattr(tx, key, default)


def _row_uid(tx):
    """The dedup identity: the row's id (a dict row without one, or with
    a blank one, is never collapsed — None)."""
    if isinstance(tx, dict):
        _id = tx.get("id")
        return _id if _id and str(_id).strip() else None
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
    kept row has its id: `<id>~<n>`, the engine's convention), `fill`
    (index -> income facts a dropped copy adds to the kept row), and
    the `attention` / `notes` lines to print."""

    def __init__(self):
        self.keep: List[int] = []
        self.drop: List[int] = []
        self.relabel: Dict[int, str] = {}
        self.fill: Dict[int, Dict[str, Any]] = {}
        self.attention: List[str] = []
        self.notes: List[str] = []


def plan_dedup(rows, source_accounts: Optional[Dict[str, Any]] = None,
               source_brokers: Optional[Dict[str, str]] = None
               ) -> DedupPlan:
    """Decide, for every row (TaxTransaction or dict), whether it is a
    duplicate. See the block comment above for the rules.
    `source_brokers` maps an input file to the broker a generic mapping
    names (`generic:<name>`, metadata.source_brokers)."""
    source_accounts = source_accounts or {}
    source_brokers = source_brokers or {}
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
        # Two generic mappings naming different brokers are two
        # sources, whatever account number each prints: equal numbers
        # at two brokers made one broker's trades copies of the
        # other's (backlog TJ-0975).
        bk, bi = source_brokers.get(srcs[k]), source_brokers.get(srcs[i])
        if bk and bi and bk != bi:
            return ("distinct", "")
        if srcs[k] == srcs[i]:
            return ("copy", "")
        return verdict(srcs[k], srcs[i])

    # (a, b, verdict) -> example rows
    events: Dict[Tuple[str, str, str], List[int]] = {}
    keep_set: Set[int] = set()
    # Every id in the book, plus each replacement as it is given: a
    # replacement `<id>~2` that another row already carried (a row
    # relabelled by an earlier pass and written out) made two records
    # one id again (audit 2026-10-24 B29).
    taken: Set[Any] = {u for u in uids if u is not None}
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
                k = n + 1
                while f"{uid}~{k}" in taken:
                    k += 1
                plan.relabel[i] = f"{uid}~{k}"
                taken.add(plan.relabel[i])
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
                    _fill_income_facts(plan, rows, k, i, shown.get(s, s))
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


class RenameDestinationConflict(InputContentError):
    """Two rows rename one holding, on one day or within the split-date
    window, to different symbols."""


# A row relabelled by the dedup as a separate record (`<id>~<n>`): its
# file's metadata names it by the plain id.
_RELABEL = re.compile(r"~\d+\Z")


def source_lines_of(metadata_blocks: Iterable[Any]) -> Dict[Tuple[str, str], int]:
    """(file identity, row id) -> line, from the `source_lines` ({id:
    line}) a converted .tt file's metadata holds (taxjson-convert-tt,
    beside its `source_file`). The identity is a row's own
    (`source_identity` of its `source` and `source_key`), so a row is
    joined to the line of ITS file, not to another file's line of the
    same content."""
    from taxjson.lib.brokerages.base import shown_name, source_key
    out: Dict[Tuple[str, str], int] = {}
    for meta in metadata_blocks:
        if not isinstance(meta, dict):
            continue
        lines = meta.get("source_lines")
        path = meta.get("source_file")
        if not isinstance(lines, dict) or not path:
            continue
        ident = source_identity(shown_name(path), source_key(path))
        for rid, line in lines.items():
            if isinstance(line, int) and not isinstance(line, bool):
                out.setdefault((ident, str(rid)), line)
    return out


def _row_line(t: Any, lines: Dict[Tuple[str, str], int]) -> Optional[int]:
    """The .tt line row `t` came from (`source_lines_of`), else None."""
    rid = str(_row_get(t, "id", "") or "")
    if not rid or not lines:
        return None
    ident = source_identity(_row_get(t, "source", ""),
                            _row_get(t, "source_key", ""))
    return lines.get((ident, rid), lines.get((ident, _RELABEL.sub("", rid))))


def _day_number(day: str) -> Optional[int]:
    from datetime import date
    try:
        return date.fromisoformat(day[:10]).toordinal()
    except (TypeError, ValueError):
        return None


def _window_runs(found: List[Tuple[str, str, int]]) -> List[List[Tuple[str, str, int]]]:
    """A holding's rename rows (date, destination, index) in runs: each
    row within SPLIT_DATE_WINDOW_DAYS of the run's previous row (the
    window inside which two dates of a split or rename are ONE event,
    lib/corporate_timeline). A date that does not read joins only rows
    of its own date string."""
    from taxjson.lib.corporate_timeline import SPLIT_DATE_WINDOW_DAYS
    runs: List[List[Tuple[str, str, int]]] = []
    odd: Dict[str, List[Tuple[str, str, int]]] = {}
    last: Optional[int] = None
    for row in sorted(found):
        n = _day_number(row[0])
        if n is None:
            odd.setdefault(row[0], []).append(row)
            continue
        if last is None or n - last > SPLIT_DATE_WINDOW_DAYS:
            runs.append([])
        runs[-1].append(row)
        last = n
    return runs + list(odd.values())


def _written_as(t: Any, written: Dict[int, Tuple[Any, str, str]]) -> Tuple[str, str]:
    """Row t's own (symbol, symbol_new) before ticker.map's mapping
    (`written`), else as it reads."""
    raw = written.get(id(t))
    if raw is not None and raw[0] is t:
        return str(raw[1] or ""), str(raw[2] or "").strip()
    return (str(_row_get(t, "symbol", "") or ""),
            str(_row_get(t, "symbol_new", "") or "").strip())


def _map_lines(sym: str, run: List[Tuple[str, str, int]], rows: Sequence[Any],
               written: Dict[int, Tuple[Any, str, str]]) -> Tuple[List[str], List[str]]:
    """(the symbols the rows were written under, the ticker.map lines that
    make their new symbols one) when each new symbol's rows were written
    under a symbol of its own that ticker.map joins into `sym`: two
    listings the map makes one holding, each renamed by its issuer, so
    both rows can be right (TJ-5283). ([], []) when one written symbol
    names two new symbols (one of those rows is false). The lines map
    each other new symbol to the one the row written as `sym` names (else
    the first), as `GLOBAL RAW CANONICAL`."""
    dest_of: Dict[str, Set[str]] = {}
    for _d, new, i in run:
        dest_of.setdefault(_written_as(rows[i], written)[0], set()).add(new)
    if len(dest_of) < 2 or any(len(v) > 1 for v in dest_of.values()):
        return [], []
    target = (min(dest_of[sym]) if sym in dest_of
              else min(min(v) for v in dest_of.values()))
    news = sorted({new for _d, new, _i in run} - {target})
    return sorted(dest_of), [f"GLOBAL {n} {target}" for n in news]


def _rename_remedy(acct: str, srcs: List[str], maps: List[str], mapped: str) -> str:
    """The ways out of a rename to two symbols: a ticker.map line first when
    the rows were written under symbols the map joined (`_map_lines`: both
    can be right), and deleting or correcting the false row."""
    fix = (f"the file named (a hand-kept .tt line: delete it; a corporate "
           f"action's row: `tjs elections {acct} --set <event id>=ignore`)")
    if not maps:
        return (f"A rename moves the whole holding to one symbol, so only one "
                f"of them can be right: refused.{mapped} Keep the right one: "
                f"delete or correct the other row in {fix}.")
    lines = " and ".join(f"`{m}`" for m in maps)
    head = f"{mapped.strip()} " if mapped else ""
    return (f"{head}The rows were written under different symbols "
            f"({', '.join(srcs)}) that ticker.map makes one holding, so both "
            f"renames can be right (each listing renamed by its issuer): then "
            f"add {lines} to ticker.map, so the new symbols are one holding "
            f"too. Otherwise one row is wrong: delete or correct it in {fix}. "
            f"Refused until then: a rename moves the whole holding to one "
            f"symbol, and taxjson cannot tell which case this is.")


def rename_conflicts(rows: Sequence[Any], *, lines: Optional[Dict[Tuple[str, str], int]] = None,
                     written: Optional[Dict[int, Tuple[Any, str, str]]] = None
                     ) -> List[str]:
    """One line per holding (account, symbol) whose SPLIT rows, on one
    day or within the split-date window (SPLIT_DATE_WINDOW_DAYS: the
    copies of one event), name more than one rename destination: two
    exports that disagree on a ticker change, a hand-kept line beside
    the broker's row. A rename moves the whole holding to one symbol,
    so the engines would apply the first and find nothing for the
    second: one of the rows is false. With one id (the destination was
    no part of it) the second was dropped as an identical row and never
    shown (backlog TJ-4985); copies a few days apart were never compared
    (TJ-5283). A plain split (no destination, or its own symbol) is not
    a rename.

    The rows are judged by the symbols they carry: taxjson-merge2 passes
    them after ticker.map's mapping, so two destinations the map makes
    one symbol are one rename, and two it keeps apart are refused;
    `written` ({id(row): (row, symbol, symbol_new)} before the mapping)
    names a row's own spelling where the map changed it, and two rows
    written under symbols the map joined can both be right: the remedy
    names the ticker.map line first (`_map_lines`, `_rename_remedy`). Each
    row is named by its file as the project holds it (`inputs/<account>/
    <file>`, the audit's and the missing-history writer's form), with its
    line for a .tt line (`lines`: `source_lines_of`), or by the corporate
    action that made it."""
    by_holding: Dict[Tuple[str, str], List[Tuple[str, str, int]]] = {}
    for i, t in enumerate(rows):
        if str(_row_get(t, "action", "") or "") != "SPLIT":
            continue
        sym = str(_row_get(t, "symbol", "") or "")
        new = str(_row_get(t, "symbol_new", "") or "").strip()
        if not new or new == sym:
            continue
        key = (str(_row_get(t, "account", "") or ""), sym)
        by_holding.setdefault(key, []).append(
            (str(_row_get(t, "date", "") or ""), new, i))
    from taxjson.lib.corporate_timeline import SPLIT_DATE_WINDOW_DAYS
    lines = lines or {}
    written = written or {}

    def where(i: int, dated: bool) -> Tuple[str, bool]:
        """Row i named (its date first when the run spans days), and
        whether the mapping changed its spelling."""
        t = rows[i]
        src = str(_row_get(t, "source", "") or "")
        line = _row_line(t, lines) if src else None
        event = str(_row_get(t, "corp_event_id", "") or "")
        said = []
        if src:
            row_acct = str(_row_get(t, "account", "") or "")
            src = f"inputs/{row_acct}/{src}" if row_acct else src
            said.append(f"in {src}:{line}" if line is not None else f"in {src}")
        if event:
            said.append(f"corporate action {event}")
        text = ", ".join(said) or "a row with no source file"
        if dated:
            text = f"on {_row_get(t, 'date', '')} {text}"
        was = " -> ".join(_written_as(t, written))
        if was != f"{_row_get(t, 'symbol', '')} -> {_row_get(t, 'symbol_new', '')}":
            return f"{text}, written {was}", True
        return text, False

    out: List[str] = []
    for (acct, sym), found in sorted(by_holding.items()):
        for run in _window_runs(found):
            by_new: Dict[str, List[int]] = {}
            for _day, new, i in run:
                by_new.setdefault(new, []).append(i)
            if len(by_new) < 2:
                continue
            days = sorted({d for d, _n, _i in run})
            dated = len(days) > 1
            named = {i: where(i, dated) for _d, _n, i in run}
            both = " and ".join(
                f"{new} ({'; '.join(dict.fromkeys(named[i][0] for i in idx))})"
                for new, idx in sorted(by_new.items()))
            when = (days[0] if not dated else
                    ", ".join(days[:-1]) + f" and {days[-1]}")
            within = (f" (rows within {SPLIT_DATE_WINDOW_DAYS} days of each "
                      f"other are one event)" if dated else "")
            mapped = (" Symbols are shown as ticker.map maps them."
                      if any(m for _t, m in named.values()) else "")
            out.append(
                f"dedup: {sym} is renamed on {when} in account {acct} to "
                f"{len(by_new)} different symbols{within}: {both}. "
                + _rename_remedy(acct, *_map_lines(sym, run, rows, written), mapped))
    return out


def _fill_income_facts(plan: DedupPlan, rows, k: int, i: int,
                       src: str) -> None:
    """Row i, a copy of the kept row k, is dropped: an income fact
    (record date, dealer country ...) that only i states is kept on k.
    The content id leaves those facts out, so the copy the file names
    sorted first decided them, and a known Canadian dealer was lost
    when the plainer statement came first (backlog TJ-0980). A fact the
    two copies state differently keeps k's, with a note."""
    from taxjson.model.transaction import INCOME_FACT_FIELDS
    have = plan.fill.setdefault(k, {})
    for f in INCOME_FACT_FIELDS:
        new = _row_get(rows[i], f, "") or ""
        if not new:
            continue
        cur = have.get(f) or _row_get(rows[k], f, "") or ""
        if not cur:
            have[f] = new
        elif str(cur) != str(new):
            plan.notes.append(
                f"dedup: {_row_brief(rows[k])}: its copy in {src} says "
                f"{f} {new!r}, the kept row {cur!r} — the kept row's is "
                f"used")
    if not have:
        plan.fill.pop(k, None)


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
               M.units(_num(_row_get(rows[i], "quantity", 0)), 6))
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
                f"{M.shown(t_net, 2):,.2f}) repeats the exported row in {srcs[j]} "
                f"({_row_brief(e)}, {M.shown(e_net, 2):,.2f}) — same symbol, "
                f"quantity and money on the same trade/settle date, but "
                f"not the same id, so BOTH are booked. If the .tt line "
                f"is that trade, delete it.")
            break
    return out


def deduplicate(transactions: List[TaxTransaction], return_dropped: bool = False,
                *, source_accounts: Optional[Dict[str, Any]] = None,
                source_brokers: Optional[Dict[str, str]] = None,
                report=None, check_renames: bool = True,
                lines: Optional[Dict[Tuple[str, str], int]] = None):
    """Remove duplicate transactions (rules: `plan_dedup`).

    With `return_dropped=True` returns `(kept, dropped)` so callers can
    report which rows collapsed (useful when --dedup removes more rows
    than expected and the user needs to audit which broker/account/date
    combinations are colliding). The default-False signature preserves
    backwards compatibility with the bare `deduplicate(txs)` callers.
    `report`, when given, is a file the ATTENTION/note lines go to.
    A row kept although another kept row shares its id (separate
    records) gets the id `<id>~<n>`.
    Two renames of one holding to different symbols (one day, or within
    the split-date window) are refused first (`rename_conflicts`:
    RenameDestinationConflict, an InputContentError naming each row,
    with its line for a .tt line: `lines`, `source_lines_of`), before
    any row is dropped — unless `check_renames` is False: taxjson-merge2
    refuses them after its ticker.map mapping instead (backlog TJ-5283).
    """
    if check_renames:
        conflicts = rename_conflicts(transactions, lines=lines)
        if conflicts:
            raise RenameDestinationConflict(" ".join(conflicts))
    plan = plan_dedup(transactions, source_accounts, source_brokers)
    for i, new_id in plan.relabel.items():
        transactions[i].id = new_id
    for i, facts in plan.fill.items():
        for f, v in facts.items():
            if isinstance(transactions[i], dict):
                transactions[i][f] = v
            else:
                setattr(transactions[i], f, v)
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


def _dict_of(value) -> Dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def source_accounts_of(metadata_blocks) -> Dict[str, List[str]]:
    """Merge the `source_accounts` maps (input file -> hashed broker
    account ids) of parsed-source metadata blocks. An entry of another
    shape is no account list: ignored, as a plain sort or a conversion
    must not stop on it (backlog TJ-1225)."""
    out: Dict[str, List[str]] = {}
    for meta in metadata_blocks:
        if isinstance(meta, dict):
            for f, accts in _dict_of(meta.get("source_accounts")).items():
                if not isinstance(accts, list):
                    continue
                out[str(f)] = sorted(set(out.get(str(f), ())) | {
                    a for a in accts if isinstance(a, str)})
    return out


def source_brokers_of(metadata_blocks) -> Dict[str, str]:
    """Merge the `source_brokers` maps (input file -> `generic:<name>`)
    of parsed-source metadata blocks."""
    out: Dict[str, str] = {}
    for meta in metadata_blocks:
        if isinstance(meta, dict):
            for f, b in _dict_of(meta.get("source_brokers")).items():
                if b:
                    out.setdefault(str(f), str(b))
    return out


def _doc_metadata_blocks(text: str) -> List[Any]:
    """The metadata of a stage document and of every source it merged
    (metadata.sources[].original_metadata); [] when absent or
    unreadable."""
    from taxjson.lib.tx_load import strip_json_comments
    try:
        doc = json.loads(strip_json_comments(text))
    except ValueError:
        return []
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    if not isinstance(meta, dict):
        return []
    sources = meta.get("sources")
    return [meta] + [s.get("original_metadata")
                     for s in (sources if isinstance(sources, list) else ())
                     if isinstance(s, dict)]


def _doc_source_accounts(text: str) -> Dict[str, List[str]]:
    """metadata.source_accounts of a stage document and of every source
    it merged (metadata.sources[].original_metadata); {} when absent or
    unreadable (the rows' own source_account still applies). A stage
    that passes rows through (taxjson-sort, taxjson-convert-currency)
    writes it back as its own metadata.source_accounts."""
    return source_accounts_of(_doc_metadata_blocks(text))


@guard_main("taxjson-sort")
def main():
    parser = argparse.ArgumentParser(description="Sort and optionally deduplicate transactions")
    parser.add_argument("input", nargs="?", help="Input JSON file (default: stdin)")
    parser.add_argument("--dedup", action="store_true", help="Remove duplicate transactions")
    parser.add_argument("--strict", action="store_true", help="Fail on validation errors")
    parser.add_argument("--no-validation", action="store_true", help="Skip validation")
    args = parser.parse_args()

    # Load input through the shared loader funnel (file: tx_load.load_
    # transactions; stdin: the pipeline's stdin loader) — `#` comments,
    # qty alias, type guards; a malformed row is a clean error.
    # The per-file broker accounts are read whether or not this pass
    # dedups, and written out again: a plain sort dropped them, and a
    # later dedup took two accounts' identical fills for copies
    # (backlog TJ-0976).
    meta_accounts: Dict[str, List[str]] = {}
    meta_brokers: Dict[str, str] = {}
    meta_lines: Dict[Tuple[str, str], int] = {}
    try:
        if args.input:
            transactions = load_transactions(Path(args.input))
            _text = Path(args.input).read_text(encoding="utf-8")
            meta_accounts = _doc_source_accounts(_text)
            meta_brokers = source_brokers_of(_doc_metadata_blocks(_text))
            meta_lines = source_lines_of(_doc_metadata_blocks(_text))
        else:
            import io
            from taxjson.lib.pipeline import load_stdin_transactions
            from taxjson.lib.cli_diag import read_stdin_utf8
            _stdin = read_stdin_utf8()      # UTF-8 whatever the locale
            transactions = load_stdin_transactions(io.StringIO(_stdin))
            meta_accounts = _doc_source_accounts(_stdin)
            meta_brokers = source_brokers_of(_doc_metadata_blocks(_stdin))
            meta_lines = source_lines_of(_doc_metadata_blocks(_stdin))
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
                                   source_accounts=meta_accounts,
                                   source_brokers=meta_brokers,
                                   lines=meta_lines)

    # Sort is a passthrough — preserve full input precision so downstream
    # calculations don't see truncated values.
    output_data = {"transactions": [tx.to_dict() for tx in transactions]}
    if meta_accounts or meta_brokers:
        output_data["metadata"] = {k: v for k, v in (
            ("source_accounts", meta_accounts),
            ("source_brokers", meta_brokers)) if v}

    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    _out.put("")  # Add final newline


if __name__ == "__main__":
    main()
