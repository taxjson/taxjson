"""The dated RENAME migration (`tjs format-map --write`): where each
dated RENAME line the map moves out goes (an account's renames.tt,
home_accounts), and a simulation of the run before and after the move
(plan_migration, MigrationPlan): the move is refused when it would
change the books, or when an account with exports but no books yet
could not be checked (_has_inputs). Out of lib/dated_events, where the
check of an account's inputs called up into the broker detector
(lib/brokerages/detect.detect) while modules the detector imports read
dated_events (step 8 of the architecture plan: lib's import cycle)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from taxjson.lib import project_layout as _PL
from taxjson.lib.dated_events import (RENAMES_TT, DatedEventError,
                                      _book_symbols, _carries, _decl_text,
                                      _kinds_meet, account_kind,
                                      home_accounts, read_declarations,
                                      resolve_renames)


def _has_inputs(root: Path, acct: str) -> bool:
    """The account's inputs folder holds a file a run builds books from:
    a .tt or an activity .csv (not a hidden file or an Office lock file,
    a redacted copy or a positions report, as the run skips them; a
    README or a sidecar builds none, so no run would ever cure a refusal
    keyed on it: backlog TJ-1182). A .csv that cannot be read as an
    export counts: the run stops on it and says why."""
    from taxjson.lib.brokerages.detect import detect
    folder = _PL.inputs_dir(Path(root)) / acct
    try:
        files = [p for p in folder.iterdir()
                 if p.is_file() and not p.name.startswith((".", "~$"))
                 and p.suffix.lower() in (".csv", ".tt")
                 and not _PL.redacted_copy_beside_exports(p)]
    except OSError:
        return False
    for p in files:
        if p.suffix.lower() == ".tt":
            return True
        try:
            if not detect(p).positions:
                return True
        except Exception:                           # noqa: BLE001
            return True
    return False


@dataclass
class MigrationPlan:
    """What `tjs format-map --write` adds to which .tt file, and
    whether the books stay the same (plan_migration)."""
    # {inputs/<acct>/renames.tt: [blocks]}: each block a moved line's
    # comments and line, in order.
    adds: Dict[str, List[List[str]]] = field(default_factory=dict)
    homes: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    # A moved line and an existing .tt line of the same change that
    # differ (date, late=): booked as one event — to reconcile.
    twins: List[str] = field(default_factory=list)
    # Why the migration would change the books (nothing is written).
    refused: List[str] = field(default_factory=list)


def _view(events: Sequence[Any], accounts: Dict[str, Any],
          syms: Dict[str, Optional[set]],
          held: Optional[Any] = None) -> set:
    """What the books of each account get from the rename events: (account,
    OLD, NEW, date, the account's late=) for each event that applies to
    the account's kind and names a symbol its books hold (every event,
    for an account without books). The late= is the one the run applies
    (DatedRename.late_for): the event's reaches an account without a
    line of its own only when it `held` OLD before the date — held(acct,
    event), its books carrying the change (_carries); an account without
    books is taken as holding."""
    out = set()
    for acct in accounts:
        kind = account_kind(accounts.get(acct))
        have = syms.get(acct)
        for e in events:
            if not e.applies_to(kind):
                continue
            if have is not None and e.old not in have and e.new not in have:
                continue
            h = True if held is None or have is None else held(acct, e)
            out.add((acct, e.old, e.new, e.date, e.late_for(acct, h)))
    return out


def plan_migration(root: Path, accounts: Dict[str, Any], map_text: str,
                   moved: Sequence[Any]) -> MigrationPlan:
    """Where each dated RENAME line `format_map(migrate=True)` moves out of
    the map goes (home_accounts), and a simulation of the run: the
    rename events the project books now (its .tt lines plus the map's
    dated lines, resolve_renames) against the ones it would book after
    the move (the .tt lines plus the moved ones). Any difference — an
    account's late= choice, a date, a contradiction the move creates —
    refuses the migration (MigrationPlan.refused names the lines);
    accounts are compared on the events naming a symbol their books hold
    (work/<acct>_base.json; an account without books on every event that
    still reaches it after the move — a legacy map line that applied to
    both kinds no longer reaches an account of the other kind, and with
    no books there is nothing to change)."""
    from taxjson.lib.ticker_map_rules import _parse_map_text
    from taxjson.lib.rename_decl import DatedRename, SOURCE_TT
    plan = MigrationPlan()
    root = Path(root)
    try:
        decl = read_declarations(root, accounts)
    except DatedEventError as e:
        plan.refused = [f"a .tt dated-event line cannot be booked: {m}"
                        for m in str(e).splitlines()]
        return plan
    tmap = _parse_map_text(map_text, "ticker.map")[0]
    mapped = list(tmap.dated or ())
    before, _n, bad = resolve_renames(decl.tt_declared, mapped)
    if bad:
        plan.refused = [f"the project's dated renames contradict each "
                        f"other (`tjs run` stops on them): {m}"
                        for m in bad]
        return plan
    have = {(d.old, d.new, d.date, d.late, d.kind): d.where
            for d in decl.tt_declared}
    added: List[Any] = []
    syms = {a: _book_symbols(root, a) for a in accounts}
    _held: Dict[Tuple[str, str, str, str], bool] = {}

    def held(acct: str, e: Any) -> bool:
        k = (acct, e.old, e.new, e.date)
        if k not in _held:
            _held[k] = _carries(root, acct, e.old, e.new, e.date)
        return _held[k]
    before_v = _view(before, accounts, syms, held)

    def moved_rename(mv: Any, acct: str, kind: str) -> Any:
        rel = f"inputs/{acct}/{RENAMES_TT}"
        return DatedRename(mv.old, mv.new, mv.date, mv.late,
                           f"{rel} (moved from ticker.map)",
                           mv.tt_line.split("  #")[0], source=SOURCE_TT,
                           account=acct, kind=kind)

    def keeps(mv: Any, nd: Any) -> bool:
        """Every account's view of this change is the same after the
        move with the line in `nd`'s account (None: no line added — a
        home that already has it), the lines moved so far included."""
        after, _n2, bad2 = resolve_renames(
            list(decl.tt_declared) + added + ([nd] if nd else []), [])
        if bad2:
            return False
        pair = (mv.old, mv.new)
        return ({x for x in _view(after, accounts, syms, held)
                 if x[1:3] == pair}
                == {x for x in before_v if x[1:3] == pair})
    for mv in moved:
        homes = home_accounts(root, accounts, mv.old, mv.new, mv.date,
                              "" if mv.commented else mv.late,
                              decl.tt_declared)
        if not homes:
            continue
        for n, (acct, why) in enumerate(homes):
            kind = account_kind(accounts.get(acct))
            key = (mv.old, mv.new, mv.date, mv.late, kind)
            dup = not mv.commented and key in have
            if not mv.commented and not keeps(
                    mv, None if dup else moved_rename(mv, acct, kind)):
                # Prefer a home whose line keeps every account's late=
                # resolution (an account relying on the map line's
                # event-level late= keeps it; v0.24.1 leftovers, 5).
                # (The home itself, when another account already has the
                # line: its own copy may be what keeps it.)
                alt = next((a for a in ([acct] if dup else [])
                            + [b for b in accounts if b != acct]
                            if account_kind(accounts.get(a)) == kind
                            and keeps(mv, moved_rename(mv, a, kind))),
                           None)
                if alt is not None:
                    if alt != acct:
                        why = ("its line keeps every account's late= "
                               "choice of this change")
                    acct, dup = alt, False
            rel = f"inputs/{acct}/{RENAMES_TT}"
            block = list(mv.comments) if n == 0 else []
            if dup:
                plan.skipped.append(f"{mv.tt_line.split('  #')[0]} "
                                    f"(already in {have[key]})")
            else:
                block.append(("# " + mv.tt_line) if mv.commented
                             else mv.tt_line)
            if block:
                plan.adds.setdefault(rel, []).append(block)
            if mv.commented or dup:
                continue
            plan.homes.append(f"{mv.old} -> {mv.new} ({mv.date}): {rel} "
                              f"({why})")
            nd = moved_rename(mv, acct, kind)
            added.append(nd)
            for d in decl.tt_declared:
                if (d.old, d.new) == (nd.old, nd.new) and \
                        _kinds_meet(d.kind, kind) and \
                        (d.date, d.late) != (nd.date, nd.late):
                    from taxjson.lib.rename_decl import WINDOW_DAYS, _days
                    gap = _days(d.date, nd.date)
                    if gap is not None and gap <= WINDOW_DAYS:
                        plan.twins.append(
                            f"ticker.map's `RENAME {mv.old} {mv.new} "
                            f"{mv.date}{' late=' + mv.late if mv.late else ''}`"
                            f" and {_decl_text(d)} declare one change "
                            f"differently: booked as one event — keep "
                            f"one line")
    after, _n, bad = resolve_renames(list(decl.tt_declared) + added, [])
    if bad:
        plan.refused = [f"after the move: {m}" for m in bad]
        return plan
    old_v = before_v
    new_v = _view(after, accounts, syms, held)
    by_pair = {(mv.old, mv.new): mv for mv in moved if not mv.commented}
    for acct, o, nw, d, late in sorted(old_v - new_v):
        alt = sorted(x for x in new_v if x[:3] == (acct, o, nw))
        if not alt and syms.get(acct) is None:
            if _has_inputs(root, acct):
                # Its exports are there, unread: whether they name the
                # symbols is not known until a run builds its books, and
                # the moved line would no longer reach it (backlog
                # TJ-0940: a crypto rename moved to the securities
                # account before the first run).
                plan.refused.append(
                    f"account {acct} has no books yet (no work/{acct}_"
                    f"base.json), so whether its trades name {o} or {nw} "
                    f"is not known, and the moved line would no longer "
                    f"reach it — run `tjs run`, then format the map "
                    f"again")
                continue
            # An account without books or inputs, of a kind the moved
            # line no longer reaches (a legacy map line applied to both
            # kinds): nothing booked changes (second pre-release
            # review, 9).
            continue
        now = (f"{d}" + (f" late={late}" if late else ""))
        then = ", ".join(f"{x[3]}" + (f" late={x[4]}" if x[4] else "")
                         for x in alt) or "not booked"
        mv = by_pair.get((o, nw))
        own = any(x.account == acct and (x.old, x.new) == (o, nw)
                  for x in decl.tt_declared)
        if late and mv is not None and mv.late == late and not own:
            # The account relies on the map line's event-level late=,
            # and no single home keeps it: its own line does.
            plan.refused.append(
                f"account {acct}: the ticker change {o} -> {nw} is "
                f"booked {now} now (ticker.map's late=, the account "
                f"having no line of its own) and would be {then} after "
                f"the move, wherever the line goes — add `"
                f"{mv.tt_line.split('  #')[0]}` to "
                f"inputs/{acct}/{RENAMES_TT} (or another .tt file of "
                f"{acct}), then format the map again")
            continue
        plan.refused.append(
            f"account {acct}: the ticker change {o} -> {nw} is booked "
            f"{now} now and would be {then} after the move — the map line "
            f"and the account's .tt lines of this change must say the "
            f"same (date, late=)")
    for acct, o, nw, d, late in sorted(new_v - old_v):
        if not any(x[:3] == (acct, o, nw) for x in old_v):
            plan.refused.append(
                f"account {acct}: the ticker change {o} -> {nw} on {d} "
                f"would be booked after the move and is not now")
    return plan
