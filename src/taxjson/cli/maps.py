"""The Set up commands for the maps: format-map, ticker-map, update-tobase-map."""
import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from taxjson.project.toml_io import read_toml
from taxjson.lib import out as _o
from taxjson.lib import project_layout as _PL

from taxjson.cli.common import _raise_requirements, _shared_inputs_note
from taxjson.pipeline.config import _config_shown, load_config, load_settings
from taxjson.pipeline.messages import (
    _details, _die, _die_input, _out_wrap, _PROG, _say,
)
from taxjson.pipeline.sanity import _json_out


def cmd_ticker_map(args: argparse.Namespace) -> None:
    """`tjs ticker-map --suggest [--write [--all]] [--json]`: every
    ticker.map line the last run suggested (lib/ticker_map_suggest),
    each with its reason; the listing pairs the map does not answer
    (MAP-GAP, "verify": TOBASE if one security, DISTINCT if two); the
    rules no symbol of the books reaches (lib/map_hygiene: "Unused
    rules, delete?", never written). --write appends the chosen lines
    to ticker.map (one by one on a terminal: y/n/q, and for a pair to
    verify t/d/s/q; --all: every evidence line, never a pair to
    verify), a comment line above each, never a line the map already
    answers, through safe_write with a backup of the old file."""
    from taxjson.lib import ticker_map_suggest as TS
    from taxjson.lib.out import Doc
    root = Path(args.dir).resolve()
    if not getattr(args, "suggest", False):
        _die_input("ticker-map: say what to do: --suggest lists the "
                   "ticker.map lines the last run suggested",
                   "`tjs ticker-map --suggest --write` appends them.")
    load_config(root)
    if not (root / "work").is_dir():
        _die("no work/ — run `tjs run` first",
             "The suggestions come from the run's messages.")
    offer, skipped = TS.pending(root)
    # The listing pairs the map does not answer (MAP-GAP): to verify,
    # asked on a terminal, never added by --all.
    verify, gap_unread = TS.verify(root, offer)
    # The rules no symbol of the books reaches (root-aware: an option's
    # underlying and a rename chain keep a rule live): listed to delete
    # by hand, never written (lib/map_hygiene).
    from taxjson.lib import map_hygiene as MH
    unused, unread = MH.unused_rules(root)
    # The user's own ticker.map lines tobase.map states identically
    # (Canada): listed ("delete?"); on a terminal --write asks for each
    # (keep is the default), --all never deletes one.
    tob_covered = _tobase_covered(root)
    interactive = (bool(getattr(args, "write", False)) and not args.all
                   and sys.stdin.isatty())
    as_json = bool(getattr(args, "json", False))
    if args.write and not interactive and not args.all and (offer or verify):
        # Nothing can be asked: a pair to verify is never written without
        # its answer, as an evidence line is never written unchosen
        # (exit 2 before any output, so a script reads no partial JSON).
        _die_input("ticker-map --write: not a terminal, so nothing can be "
                   "asked",
                   ("Add --all to append every suggestion the run's "
                    "evidence names (never a pair to verify), or run it "
                    "in a terminal to choose one by one."
                    if offer else
                    f"{len(verify)} listing pair(s) to verify — one "
                    f"security (TOBASE) or two (DISTINCT) — are asked only "
                    f"on a terminal: run it there, or write the line you "
                    f"choose into ticker.map by hand."))
    # Under --json every plain line goes to stderr: stdout is the JSON.
    out = sys.stderr if as_json else sys.stdout

    def say(text: str) -> None:
        print(text, file=out)

    def ask(prompt: str) -> str:
        if as_json:
            print(prompt, end="", file=sys.stderr, flush=True)
            return input()
        return input(prompt)

    if as_json:
        # `suggestions`: the lines the run's evidence names, safe to
        # append; `verify`: the pairs only you can answer (TOBASE or its
        # `alternative`) — apart, so a script appending every suggestion
        # never writes an unproven TOBASE.
        _json_out({"suggestions": [s.record() for s in offer],
                   "verify": [s.record() for s in verify],
                   "skipped": [dict(s.record(), why=w, by=(
                       "suggestion" if TS.covered_by_suggestion(w)
                       else "ticker.map")) for s, w in skipped],
                   "unused": [u.record() for u in unused],
                   "covered": [{"line": c.rule, "lineno": c.lineno,
                                "tobase": c.where} for c in tob_covered],
                   "unused_unread": list(unread),
                   "map_gap_unread": list(gap_unread)},
                  schema="ticker-map --suggest")
        if not args.write:
            return
    elif not args.write or not (offer or (verify and interactive)
                                or (tob_covered and interactive)):
        _det, d = _ticker_map_listing(args, offer, verify, gap_unread, skipped, unused,
                                      tob_covered, unread, TS, MH, Doc)
        if not _det:
            from taxjson.lib.out import act as _act
            if args.write and args.all and verify and not offer:
                d.line(_act("Nothing written: --all, never a pair to "
                            "verify; answer each",
                            "tjs ticker-map --suggest --write"))
            elif offer or verify:
                d.line(_act("Add only the lines right for your "
                            "securities, then `tjs run`",
                            "tjs ticker-map --suggest --write"))
            elif unused or tob_covered:
                d.line("Nothing to add; the lines above are harmless "
                       "(why: --details).")
            else:
                d.line("Nothing to add.")
            d.print()
            return
        d.para("Add a line only when it is right for your securities: "
               "`tjs ticker-map --suggest --write` asks for each one "
               "(a pair to verify: TOBASE, DISTINCT or skip; --all adds "
               "only the lines the run's evidence names, never a pair to "
               "verify), then re-run `tjs run`."
               if offer or verify else
               "Nothing to add; an unused rule is harmless — delete it by "
               "hand only if its symbol will not return." if unused else
               "Nothing to add; a line tobase.map covers is harmless."
               if tob_covered else "Nothing to add.")
        d.print()
        return
    chosen, drop = _ticker_map_choose(offer, interactive, say, ask, verify, TS, tob_covered)
    if not chosen and not drop:
        say("Nothing added to ticker.map.")
        return
    _ticker_map_write(root, drop, chosen, TS, say)


def _ticker_map_listing(args: argparse.Namespace, offer: List[Any], verify: List[Any],
                        gap_unread: List[str], skipped: List[Any], unused: List[Any],
                        tob_covered: List[Any], unread: List[str], TS: Any, MH: Any,
                        Doc: Any) -> Tuple[bool, Any]:
    """tjs ticker-map --suggest, the listing: the lines the run's
    evidence names, the pairs to verify, the lines skipped, the unused
    rules and the lines tobase.map covers, each one's reason with
    --details. Returns --details given and the Doc, for its closing
    lines."""
    # Essentials first (docs/output-style.md): the lines; each one's
    # reason, the skipped ones and the advice behind --details.
    _det = _details(args)
    d = Doc(f"TICKER.MAP SUGGESTIONS — {len(offer)} from the last run"
            + (f", {len(verify)} to verify" if verify else ""))
    if offer:
        d.blank()
        if not _det:
            d.line("Lines for ticker.map, from the run's evidence (a "
                   "pair's reason under it; every reason: --details).")
        for s in offer:
            d.line(s.line + (("   (a template: edit, then add it by "
                              "hand)" if _det else "   (template: edit "
                              "it, add by hand)") if s.template else
                             ("   (a .tt line: add it to a .tt file of "
                              "the account)" if _det else
                              "   (a .tt line)") if s.tt else ""))
            if _det or s.conditional or s.keyword in ("TOBASE",
                                                      "DISTINCT"):
                # A pair joins (or parts) two listings' cost: its
                # evidence stays in the default view.
                d.para(s.reason, indent="  ")
    if verify:
        d.section(f"To verify: one security or two? ({len(verify)})")
        for s in verify:
            d.line(f"{s.line}   or   {s.alternative}")
            # (the evidence to choose by: kept in the default view)
            d.para(s.reason, indent="  ")
    if gap_unread:
        d.blank()
        d.para(f"The listing-pair check skipped what it could not "
               f"read: {'; '.join(gap_unread)} — re-run `tjs "
               f"run`.")
    # A suggestion another one covers is not answered by the map:
    # its own heading.
    covered = [x for x in skipped if TS.covered_by_suggestion(x[1])]
    answered = [x for x in skipped
                if not TS.covered_by_suggestion(x[1])]
    _skip_lines = []
    for head, items in (("Already answered by ticker.map", answered),
                        ("Covered by another suggestion", covered)):
        if items and _det:
            d.section(f"{head} ({len(items)})")
            for s, why in items:
                d.item(f"{s.line}: {why}", "  ")
        elif items:
            _skip_lines.append(f"{head} ({len(items)})")
    if _skip_lines:
        d.blank()
        d.line("; ".join(_skip_lines)
               + " — tjs ticker-map --suggest --details")
    if unused:
        d.section(f"Unused rules, delete? ({len(unused)})")
        for u in unused:
            d.line(u.line)
            if _det:
                d.para(u.reason, indent="  ")
    if tob_covered:
        d.section(f"Covered by tobase.map (delete?) "
                  f"({len(tob_covered)})")
        for c in tob_covered:
            d.item(f"{c.rule} (ticker.map:{c.lineno}): {c.where} "
                   f"states the same pair", "  ")
        if _det:
            d.para("Keeping them is harmless: your line holds even if "
                   "the interlisted master later retracts the pair. "
                   "`tjs ticker-map --suggest --write` on a "
                   "terminal asks for each (keep is the default).",
                   indent="  ")
    if unread:
        d.blank()
        d.para(MH.first_unread(unread))
    d.blank()
    return _det, d


def _ticker_map_choose(offer: List[Any], interactive: bool, say: Any, ask: Any,
                       verify: List[Any], TS: Any, tob_covered: List[Any]
                       ) -> Tuple[List[Any], List[int]]:
    """tjs ticker-map --suggest --write: the lines to add (each asked on
    a terminal, y/n/q; a pair to verify, t/d/s/q; --all: every
    evidence line, never a template, a .tt line or a pair to verify)
    and the lines tobase.map covers to delete (asked on a terminal).
    Returns the lines chosen and the line numbers to delete."""
    chosen = []
    stopped = False
    for s in offer:
        if s.template:
            # A placeholder (<LISTING>, <words ...>) would rename rows to
            # a symbol that is not one: never written.
            say(f"Not added (a template — edit it, then add it by hand): "
                  f"{s.line}")
            continue
        if s.tt:
            # A dated event is a .tt line of an account (lib/dated_events),
            # never a ticker.map line.
            say(f"Not added (a .tt line — add it to a .tt file of the "
                  f"account): {s.line}")
            continue
        if interactive:
            say(s.line)
            for ln in _out_wrap(s.reason, indent="  ", hang="  "):
                say(ln)
            try:
                ans = ask("Add this line? [y/n/q] ").strip().lower()
            except EOFError:
                ans = "q"
            if ans.startswith("q"):
                stopped = True
                break
            if not ans.startswith("y"):
                continue
        chosen.append(s)
    for s in verify:
        if not interactive:
            # --all writes only what the evidence proves: a shared root
            # is a candidate, never proof (MAP-GAP).
            say(f"Not added (to verify — one security or two? `tjs "
                  f"ticker-map --suggest --write` on a terminal asks): "
                  f"{s.line} / {s.alternative}")
            continue
        if stopped:
            break
        say(f"{s.line}   or   {s.alternative}")
        for ln in _out_wrap(s.reason, indent="  ", hang="  "):
            say(ln)
        try:
            ans = ask("One security or two? [t]OBASE / [d]ISTINCT / "
                        "[s]kip / [q]uit ").strip().lower()
        except EOFError:
            ans = "q"
        if ans.startswith("q"):
            break
        # The comment above the line records the answer and what the
        # exports showed (the reason's first clause, without the lines).
        why = s.reason.split(" — ")[0]
        if ans.startswith("t"):
            chosen.append(TS.Suggestion(
                s.line, f"one security (your answer): {why}", s.source))
        elif ans.startswith("d"):
            chosen.append(TS.Suggestion(
                s.alternative, f"two securities (your answer): {why}",
                s.source))
    # The lines tobase.map covers: asked one by one on a terminal (keep
    # is the default); --all and a script never delete one.
    drop: List[int] = []
    if interactive and tob_covered and not stopped:
        say(f"{len(tob_covered)} ticker.map line(s) tobase.map states "
            f"identically (keeping one is harmless: it holds even if the "
            f"master later retracts the pair):")
        for c in tob_covered:
            try:
                ans = ask(f"{c.rule} (ticker.map:{c.lineno}; {c.where}): "
                          f"[k]eep / [d]elete / [q]uit? [k] "
                          ).strip().lower()
            except EOFError:
                ans = "q"
            if ans.startswith("q"):
                break
            if ans.startswith("d"):
                drop.append(c.lineno)
    return chosen, drop


def _ticker_map_write(root: Path, drop: List[int], chosen: List[Any], TS: Any,
                      say: Any) -> None:
    """tjs ticker-map --suggest --write: the new ticker.map (the lines
    deleted, the chosen ones appended) checked to be one `tjs run`
    accepts, then written through safe_write with a backup, and the
    line saying so; a link leaving the project refused."""
    tm = _PL.ticker_map_path(root)
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.safe_write import (OutsideLinkError, link_outside,
                                        write_user_file)
    _outside = link_outside(tm, root)
    if _outside is not None:
        _die_input(f"ticker-map --write: ticker.map is a symlink to "
                   f"{_outside}, outside the project — nothing was written",
                   "Add the lines to the file it points at, or replace the "
                   "link with a copy.")
    current = read_text_utf8(tm) if tm.is_file() else ""
    if drop:
        from taxjson.lib.tobase_map import without_lines
        current = without_lines(current, drop)
    text = TS.appended_text(current, chosen) if chosen else current
    # The new map must be one `tjs run` accepts (no contradiction).
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:
        _probe = Path(_d) / "ticker.map"
        _probe.write_text(text, encoding="utf-8")
        from taxjson.lib.map_file import map_file_problems
        _problems = map_file_problems(_probe)
    if _problems:
        _die("ticker-map --write: the lines would make ticker.map "
             "contradict itself — nothing was written",
             *[f"- {p}" for p in _problems])
    try:
        # The user-file guard migrate and format use: a link inside the
        # project stays a link (its target is replaced), the permission
        # bits are kept, the old text is kept as ticker.map.bak[N].
        bak = write_user_file(tm, text, root, suffix=".suggest.part")
    except OutsideLinkError as e:
        _die_input(f"ticker-map --write: {e}")
    except OSError as e:
        # Named from where the command was run, with the fix (backlog
        # TJ-0219).
        from taxjson.lib.cli_diag import cannot_write
        _h, _fix = cannot_write(_config_shown(tm), e, tm)
        _die_input(f"ticker-map --write: {_h}", *([_fix] if _fix else []),
                   "Nothing was written.")
    say((f"Added {len(chosen)} line(s) to ticker.map" if chosen else
         "ticker.map")
        + (f"; deleted {len(drop)} line(s) tobase.map covers" if drop
           else "")
        + (f" (the old file is {bak.name})" if bak else "")
        + ". Run `tjs run` to apply them.")


def _tobase_covered(root: Path) -> List[Any]:
    """The user's ticker.map lines the project's tobase.map states
    identically (lib/tobase_map.covered_lines): Canada only, [] without
    either file or when one cannot be read."""
    from taxjson.lib import tobase_map as TB
    tm = _PL.ticker_map_path(root)
    if not tm.is_file() or TB.project_country(root) != "canada":
        return []
    try:
        from taxjson.lib.cli_diag import read_text_utf8
        return TB.covered_lines(read_text_utf8(tm), TB.read_tobase(root))
    except (OSError, ValueError):
        return []


def cmd_format_map(args: argparse.Namespace) -> None:
    """`tjs format-map [--write [--no-backup] | --check]`: lay
    ticker.map out in keyword groups (lib/ticker_map_format): a short
    header, then Spellings, Listings of one security, Clean-up and
    Lookups, each under its heading, the user's order kept in a group, a
    comment directly above a line moved with it, spacing normalised,
    exact duplicates dropped, a line taxjson cannot use kept as written
    in an Unrecognized group. Dated events leave the map (v0.24): a
    `JOURNAL A B` line becomes `TOBASE A B`, and each dated `RENAME OLD
    NEW YYYY-MM-DD [late=...]` moves, with its comments, to a .tt line
    `RENAME YYYY-MM-DD OLD NEW [late=...]` in inputs/<account>/renames.tt
    (lib/dated_events.home_accounts: one per account kind whose books
    hold the symbols). The parsed map before and after (plus the moved
    lines) must mean the same, every comment be kept, and the run book
    the same rename events in every account after the move as before it
    — the project's .tt lines included (lib/rename_migration.plan_migration)
    — or nothing is written (exit 2, naming the lines). Every target is
    checked before the first write (a link to outside the project — of
    ticker.map, a .tt file or its inputs/<account> folder — is refused);
    the .tt files are written first, a block they already hold is not
    added again, and a failed write says what was already written.
    Default: print a unified diff, the .tt additions and the lines per
    group; --check fails while the map is not formatted, a migration is
    pending, or `tjs run` refuses the map."""
    import difflib
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.ticker_map_format import FormatError, format_map
    prog = f"{_PROG} format-map"
    if args.no_backup and not args.write:
        _die_input("--no-backup applies only with --write")
    root = Path(args.dir).resolve()
    # The .tt files it moves lines to may be in the shared inputs folder
    # (lib/project_layout): their links are judged against the folder
    # holding the year folders.
    _data = _PL.write_boundary(root)
    tm = _PL.ticker_map_path(root)
    if not tm.exists() and not tm.is_symlink():
        _die_input(f"no ticker.map in {root}",
                   "`tjs init` writes one; the run needs none.")
    if tm.is_dir():
        _die_input(f"{tm} is a directory, not a file")
    text, res = _format_map_read(tm, read_text_utf8, format_map, FormatError, prog)
    # Where each moved RENAME goes (a .tt RENAME applies to every account
    # of its kind holding the old symbol), and whether the run would book
    # the same events after the move (lib/rename_migration.plan_migration).
    from taxjson.lib import dated_events as DE, rename_migration as RMG
    plan = RMG.MigrationPlan()
    pending = res.migration_pending
    if args.check:
        _format_map_check(res, pending, prog)
        return
    if res.moved:
        cfg = load_config(root)
        accounts = cfg.get("accounts") or {}
        if not accounts:
            _die_input("ticker.map's dated RENAME lines move to an "
                       "account's .tt file, and taxjson.toml has no "
                       "account", "Nothing was written.")
        plan = RMG.plan_migration(root, accounts, text, res.moved)
        if plan.refused:
            _die_input("moving ticker.map's dated RENAME lines to .tt "
                       "files would change the books — nothing was "
                       "written",
                       *[f"- {m}" for m in plan.refused],
                       *[f"- {m}" for m in plan.twins],
                       "Make the map line and the .tt line(s) of each "
                       "change say the same (or delete one), run "
                       "`tjs run`, then format the map again. An "
                       "account without books yet (no work/<account>_"
                       "base.json) is compared on every change of its "
                       "kind: run `tjs run` first.")
    doc = _o.Doc(exact=True, width_=0, live=True)
    if not res.changed and not pending:
        doc.line("ticker.map is already formatted.")
        return
    counts = ", ".join(f"{g} {n}" for g, n in res.counts.items()
                       if n or g not in ("Retired", "Unrecognized",
                                         "Dated events"))
    # What each .tt file gets: a block already in the file (an earlier
    # --write, a restored backup) is not added again.
    from taxjson.lib.safe_write import (OutsideLinkError, link_outside,
                                        write_user_file)
    texts = _format_map_tt_texts(plan, root, read_text_utf8, DE)
    if not args.write:
        sys.stdout.writelines(difflib.unified_diff(
            text.splitlines(keepends=True),
            res.text.splitlines(keepends=True),
            fromfile="ticker.map", tofile="ticker.map (formatted)"))
        for rel, (cur, new_txt, _n) in sorted(texts.items()):
            doc.line(f"--- {rel}{'' if cur else ' (new file)'}")
            doc.line(f"+++ {rel} (with the dated events from ticker.map)")
            for ln in new_txt[len(cur):].lstrip("\n").splitlines():
                doc.line(f"+{ln}")
        _migration_notes(res, plan.homes, plan.skipped, prog,
                         plan.twins, root, text)
        _say("note", f"lines per group: {counts}", prog=prog)
        _say("note", "dry run: nothing written",
             "`tjs format-map --write` applies this; the books are "
             "the same either way.", prog=prog)
        return
    _format_map_targets(tm, root, texts, link_outside, _data)
    # The .tt files first: a map without its moved lines must never be
    # left behind without them.
    written: List[str] = []

    def _fail(what: str, e: BaseException, path: Path) -> None:
        # The file named from where the command was run, and the fix
        # (`chmod u+w` it or its folder): the read-only reason named
        # the bare file name, wrong with -C from elsewhere (backlog
        # TJ-0219).
        from taxjson.lib.cli_diag import cannot_write
        if isinstance(e, OSError):
            head, fix = cannot_write(_config_shown(path), e, path)
        else:
            head, fix = f"cannot write {what}: {e}", ""
        _die_input(head, *([fix] if fix else []),
                   ("Already written: " + ", ".join(written)
                    + " — ticker.map is unchanged; re-running "
                    "`tjs format-map --write` does not add those "
                    "lines twice.") if written else "Nothing was written.")
    for rel, (cur, new_txt, n_rules) in sorted(texts.items()):
        p = _PL.project_path(root, rel)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            write_user_file(p, new_txt, _data,
                            suffix=".format.part",
                            backup=bool(cur) and not args.no_backup)
        except (OutsideLinkError, OSError) as e:
            _fail(rel, e, p)
        written.append(rel)
        doc.line(f"wrote {_PL.shown(p, root)} ({n_rules} ticker change(s) "
                 f"from ticker.map)")
        _shared_inputs_note(root, p, prog)
    try:
        bak = write_user_file(tm, res.text, root,
                              suffix=".format.part",
                              backup=not args.no_backup)
    except (OutsideLinkError, OSError) as e:
        _fail("ticker.map", e, tm)
    doc.line("formatted ticker.map"
             + (f" (previous version: {bak.name})" if bak else ""))
    _migration_notes(res, plan.homes, plan.skipped, prog, plan.twins, root,
                     text)
    _say("note", f"lines per group: {counts}", prog=prog)


def _format_map_read(tm: Path, read_text_utf8: Any, format_map: Any, FormatError: Any,
                      prog: str) -> Tuple[str, Any]:
    """tjs format-map: ticker.map read and laid out (a map it cannot
    read or format stops the command); its problems, the exact
    duplicates dropped, the lines of a removed feature and the
    header's notes said. Returns the text and the result."""
    try:
        text = read_text_utf8(tm)
    except OSError as e:
        _die_input(f"cannot read {tm}: {e.strerror or e}")
    try:
        res = format_map(text, migrate=True)
    except FormatError as e:
        _die_input(f"ticker.map: {e}", "Nothing was written.")
    if res.problems:
        _n_bad = res.counts.get("Unrecognized", 0)
        _say("warning", f"{len(res.problems)} ticker.map problem(s): "
             f"`tjs run` refuses the map until each is fixed",
             *[f"- {m}" for m in res.problems],
             *([f"The {_n_bad} line(s) taxjson cannot use are kept as "
                f"written in the \"Unrecognized\" group."] if _n_bad
               else []),
             *(["Its dated events are moved out once the map has no "
                "problem."] if res.problems else []), prog=prog)
    if res.duplicates:
        _say("note", f"dropped {len(res.duplicates)} exact duplicate "
             f"line(s)",
             *[f"line {n}: {ln}" for n, ln in res.duplicates], prog=prog)
    if res.retired:
        _say("note", f"{res.retired} line(s) of a removed feature are in "
             f"the \"Retired\" group", "taxjson ignores them; delete "
             "them.", prog=prog)
    _format_map_header_notes(res, prog)
    return text, res


def _format_map_check(res: Any, pending: Any, prog: str) -> None:
    """tjs format-map --check: a warning while dated events wait to
    migrate or the map is not formatted, or the line saying it is; a
    warning while `tjs run` refuses the map; exit 1 for any of them."""
    # --check reports; it plans nothing (a project without accounts
    # can still be checked). It fails while the map is not formatted,
    # a migration is pending, or `tjs run` refuses the map.
    if pending:
        _say("warning", f"ticker.map holds dated events to migrate: "
             f"{len(res.journals)} JOURNAL line(s) (to TOBASE), "
             f"{len([m for m in res.moved if not m.commented])} dated "
             f"RENAME line(s) (to a .tt file)",
             "`tjs format-map` shows the changes, `tjs "
             "format-map --write` applies them.", prog=prog)
    elif res.changed:
        _say("warning", "ticker.map is not formatted",
             "`tjs format-map` shows the changes, `tjs "
             "format-map --write` applies them.", prog=prog)
    else:
        _o.put("ticker.map is already formatted.")
    if res.problems:
        _say("warning", f"`tjs run` refuses this ticker.map: "
             f"{len(res.problems)} problem(s), listed above",
             "Fix each line, then check again.", prog=prog)
    if pending or res.changed or res.problems:
        sys.exit(1)


def _format_map_tt_texts(plan: Any, root: Path, read_text_utf8: Any, DE: Any
                         ) -> Dict[str, Tuple[str, str, int]]:
    """tjs format-map: what each .tt file gets from the dated RENAME
    lines the map moves out, by its path in the project: its text now,
    its new text and the RENAME lines added; a block already in the
    file (an earlier --write, a restored backup) is not added again,
    and a file with nothing to add is left out."""
    texts: Dict[str, Tuple[str, str, int]] = {}
    for rel, blocks in sorted(plan.adds.items()):
        p = _PL.project_path(root, rel)
        try:
            cur = read_text_utf8(p) if p.is_file() else ""
        except OSError as e:
            _die_input(f"cannot read {rel}: {e.strerror or e}",
                       "Nothing was written.")
        from taxjson.lib.tt_format import tt_lines
        have = [ln.strip() for ln in tt_lines(cur)]
        lines: List[str] = []
        n_rules = 0
        for block in blocks:
            body = [ln.strip() for ln in block]
            if _block_in(body, have):
                continue
            last = body[-1] if body else ""
            if last.startswith(("RENAME", "# RENAME")) and last in have:
                # The line is there (its comments edited since): only the
                # comments that are not.
                block = [ln for ln in block[:-1] if ln.strip() not in have]
            lines.extend(block)
            n_rules += sum(1 for ln in block if ln.startswith("RENAME"))
        if not lines:
            continue
        new_txt = (cur + ("" if not cur or cur.endswith("\n") else "\n")
                   + ("\n" if cur.strip() else "")
                   + ("" if cur.strip() else DE.RENAMES_TT_HEAD)
                   + "\n".join(lines) + "\n")
        texts[rel] = (cur, new_txt, n_rules)
    return texts


def _format_map_targets(tm: Path, root: Path, texts: Dict[str, Tuple[str, str, int]],
                        link_outside: Callable[..., Optional[str]], _data: Path) -> None:
    """tjs format-map --write, before anything is written: ticker.map
    and each .tt file must be inside the project (a link of the file,
    or of its inputs/<account> folder, pointing outside is refused),
    and no two accounts' .tt files may be one file."""
    # Every target is checked before anything is written: ticker.map and
    # each .tt file inside the project (a link — of the file or of its
    # inputs/<account> folder — pointing outside is refused).
    targets = [("ticker.map", tm)] + [(rel, _PL.project_path(root, rel))
                                      for rel in texts]
    for rel, p in targets:
        _out = link_outside(p, root if rel == "ticker.map" else _data)
        if _out is None and rel != "ticker.map":
            _par = p.parent
            while not _par.exists() and _par != _data:
                _par = _par.parent
            try:
                _par.resolve().relative_to(_data.resolve())
            except ValueError:
                _out = _par.resolve()
        if _out is not None:
            _die_input(f"{rel} would be written through a link to "
                       f"{_out}, outside the project — nothing was "
                       f"written",
                       "Replace the link with the folder or file it "
                       "points at, or migrate the lines by hand.")
    # Two accounts' .tt files that are one file (links to the same
    # target inside the project): each text was made from the file as
    # it is, so the second write dropped the first one's lines (backlog
    # TJ-0938). Refused before anything is written.
    _by_real: Dict[Path, List[str]] = {}
    for rel in texts:
        _by_real.setdefault(_PL.project_path(root, rel).resolve(),
                            []).append(rel)
    _alias = [rels for rels in _by_real.values() if len(rels) > 1]
    if _alias:
        _die_input(f"{' and '.join(sorted(_alias[0]))} are one file (a link "
                   f"to the same target) — nothing was written",
                   "Each account's dated RENAME lines go in its own .tt "
                   "file: replace the links with files of their own, "
                   "then format the map again.")


def _format_map_header_notes(res, prog: str) -> None:
    """format-map's Info lines on the map's comments
    (lib/ticker_map_format.FormatResult): an earlier taxjson header
    replaced by the current one, one kept because the user edited it, and
    the user's comments that still describe the legacy dated events as map
    rules (kept: they are the user's)."""
    if res.regenerated:
        _say("note", f"{res.regenerated} comment line(s) of an earlier "
             f"taxjson header are replaced by the current header",
             prog=prog)
    n = len(res.edited_headers)
    if n:
        _say("note", ("a comment block looks" if n == 1 else
                      f"{n} comment blocks look")
             + " like an old taxjson header you edited — kept; delete "
             + ("it if it no longer applies" if n == 1
                else "them if they no longer apply"),
             *[f"- {ln}" for ln in res.edited_headers], prog=prog)
    n = len(res.dated_comments)
    if n:
        _say("note", f"{n} comment block(s) still describe JOURNAL or a "
             f"dated RENAME as a ticker.map rule",
             *[f"- {ln}" for ln in res.dated_comments],
             "A journal or a ticker change on a date is a .tt line of an "
             "account now (docs/settings.md, \".tt files\"); your comments "
             "are kept: edit them if they no longer apply.", prog=prog)


def _journal_gaps(root: Path, map_text: str,
                  res) -> List[Tuple[str, str, str, float]]:
    """(account, FROM, TO, qty) for each legacy JOURNAL pair a format-map
    migration rewrites whose listings the last run's holdings
    (reports/<acct>_holdings.toml) show with opposite signs: the long one
    is FROM, the quantity the smaller of the two."""
    if not res.journals:
        return []
    from taxjson.lib.ticker_map_rules import _parse_map_text
    try:
        pairs = dict(_parse_map_text(map_text, "ticker.map")[0].journal)
    except ValueError:
        return []
    out = []
    for hp in sorted((root / "reports").glob("*_holdings.toml")):
        try:
            doc = read_toml(hp)
        except (OSError, ValueError):
            continue
        qty: Dict[str, float] = {}
        for h in doc.get("holding") or []:
            if isinstance(h, dict):
                try:
                    qty[str(h.get("symbol") or "").upper()] = \
                        qty.get(str(h.get("symbol") or "").upper(), 0.0) \
                        + float(h.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    continue
        acct = hp.name[:-len("_holdings.toml")]
        for a, b in sorted(pairs.items()):
            qa, qb = qty.get(a, 0.0), qty.get(b, 0.0)
            if qa * qb < 0:
                frm, to = (a, b) if qa > 0 else (b, a)
                out.append((acct, frm, to, min(abs(qa), abs(qb))))
    return out


def _block_in(block: List[str], have: List[str]) -> bool:
    """`block` (stripped lines) appears, in order and contiguous, in
    `have`."""
    n = len(block)
    if not n:
        return True
    return any(have[i:i + n] == block for i in range(len(have) - n + 1))


def _migration_notes(res, homes: List[str], skipped: List[str],
                     prog: str, twins: List[str] = (),
                     root: Optional[Path] = None,
                     map_text: str = "") -> None:
    """The migration's Info lines (format-map): what moved where, the
    .tt lines that declare a moved change differently (to reconcile),
    and a `.tt JOURNAL` line for each migrated JOURNAL whose two
    listings the holdings show long and short (M7)."""
    if res.journals:
        _say("note", f"{len(res.journals)} JOURNAL line(s) rewritten as "
             f"TOBASE (what they mean now)",
             "A journal on a date is a .tt line of the account: `JOURNAL "
             "<date> <FROM> <TO> <qty>` (only needed when the broker's "
             "export lacks the journal's rows).", prog=prog)
        for acct, frm, to, qty in (_journal_gaps(root, map_text, res)
                                   if root else ()):
            _say("warning", f"{acct}: the holdings show {frm} long and "
                 f"{to} short by {qty:g} — a journal the export lacks",
                 "The tax books join the two listings (TOBASE); the "
                 "holdings view moves units only by a journal's rows. "
                 "Add to a .tt file of the account (the journal's date):",
                 f"  JOURNAL <date> {frm} {to} {qty:g}", prog=prog)
    if twins:
        _say("note", f"{len(twins)} moved line(s) repeat a .tt line of the "
             f"same change written differently (booked as one event)",
             *twins, prog=prog)
    if homes:
        _say("note", f"{len(homes)} dated RENAME line(s) moved to a .tt "
             f"file (a .tt RENAME applies to every account of its kind — "
             f"securities or crypto — holding the old symbol)", *homes,
             prog=prog)
    if skipped:
        _say("note", f"{len(skipped)} dated RENAME line(s) already "
             f"declared in a .tt file: removed from ticker.map", *skipped,
             prog=prog)
    if res.dropped_examples:
        _say("note", f"dropped {len(res.dropped_examples)} switched-off "
             f"example(s) an earlier `tjs init` wrote",
             *res.dropped_examples, prog=prog)


def cmd_update_tobase_map(args: argparse.Namespace) -> None:
    """`tjs update-tobase-map [--write] [--json]`: bring the
    project's tobase.map in step with the installed interlisted master
    (lib/tobase_map, tax-logic CA-XLIST-06). Dry run by default; --write
    applies it (the previous file kept as tobase.map.bak). Canada only
    (lib/country COMMAND_COUNTRY)."""
    from taxjson.lib import tobase_map as TB
    from taxjson.lib.safe_write import write_user_file
    from taxjson.lib.ticker_map_suggest import books_symbols
    root = Path(args.dir).resolve()
    if not _PL.has_config(root):
        root = _tobase_root_year(root, args.json)
    both = _PL.tobase_both_problem(root, load_settings(root, missing_ok=True))
    if both:
        _die_input(both, "Nothing was written.")
    shared = _PL.shared_tobase(root)
    # Every year that reads the file this one does (its own: this year).
    readers = [root]
    if shared:
        _lay = _PL.tobase_layout(root.parent)
        readers = [root.parent / str(y) for y in
                   _lay["shared"].get(_PL.tobase_map_path(root), [])] \
            or [root]
    _yrs_text = ", ".join(p.name for p in readers)
    boundary = root.parent if shared else root
    master = TB.load_master()
    books: Set[str] = set()
    for _r in readers:
        books |= books_symbols(_r)
    tm = _PL.ticker_map_path(root)
    from taxjson.lib.cli_diag import read_text_utf8
    ticker_text = read_text_utf8(tm) if tm.is_file() else ""
    tob = TB.read_tobase(root)
    # Essentials first (docs/output-style.md): --details adds the
    # master's figures, each line's reason and the long headings.
    _det = _details(args)
    from taxjson.lib.out import act as _act
    doc = _o.Doc(exact=True, width_=0, live=True)
    if shared and not args.json and not _det:
        doc.line(f"{_PL.tobase_shown(root)}: read by {_yrs_text}.")
    elif shared and not args.json:
        for _ln in _out_wrap(
                f"{_PL.tobase_shown(root)}: read by {_yrs_text} — a "
                f"change applies to each (a filed year closed with its "
                f"inputs frozen reads its own copy instead; each "
                f"year's ticker.map still wins)."):
            doc.line(_ln)
    nsec = len(master.security)
    exch = sum(1 for e in master.security.values() if e.get("us"))
    otc = sum(1 for e in master.security.values()
              if e.get("us_otc") and not e.get("us"))
    head = (f"The installed interlisted master (generated "
            f"{master.generated}): {nsec} Canadian securities, {exch} with "
            f"a US exchange listing, {otc} with OTC listings only.")
    no_run = not books
    if tob is None:
        text = TB.render(master, shared=shared)
        n_lines = sum(1 for ln in text.splitlines()
                      if ln.split("#", 1)[0].strip())
        changes = TB.books_changes(master, books, ticker_text)
        ov = TB.compute_overlay(ticker_text, TB.parse_tobase(text), True)
        _tobase_map_first_report(args, master, n_lines, changes, ov, _det, head, no_run)
        if not args.write:
            if not args.json and not _det:
                doc.line(_act("Nothing written: create tobase.map, then "
                              "`tjs run`", "tjs update-tobase-map --write"))
            elif not args.json:
                for _ln in _out_wrap(
                        "Nothing written: `tjs update-tobase-map "
                        "--write` creates tobase.map; then `tjs run`."):
                    doc.line(_ln)
            return
        if not tm.is_file():
            from taxjson.lib.ticker_map_format import init_template
            write_user_file(tm, init_template(), root, backup=False)
        write_user_file(TB.tobase_path(root), text, boundary, backup=False)
        if not args.json:
            doc.line(f"Wrote {_PL.tobase_shown(root)} ({n_lines} lines). Run "
                     f"`tjs run` "
                     + (f"in each year that reads it ({_yrs_text}) "
                        if shared else "")
                     + "to rebuild the books with it.")
        _raise_requirements(root, dirs=readers, quiet=args.json)
        return
    plan = TB.plan_update(master, tob, books, ticker_text, shared=shared)
    in_books = [g for g in plan.added
                if g.keyword == "TOBASE" and ({g.a, TB.venue_alias(g.a)}
                                              & books)]
    made_map = False
    if args.write and not tm.is_file():
        # tobase.map is read beside a ticker.map only (_say_tobase_map).
        from taxjson.lib.ticker_map_format import init_template
        write_user_file(tm, init_template(), root, backup=False)
        made_map = True
    if args.json:
        _tobase_map_update_json(master, plan, in_books, made_map, args)
    else:
        _tobase_map_update_text(_det, head, plan, master, in_books, _act, TB)
    stale = plan.old_stamp != plan.new_stamp
    if not args.write:
        if (plan.changes or stale) and not args.json and not _det:
            doc.line(_act("Nothing written", "tjs update-tobase-map --write"))
        elif (plan.changes or stale) and not args.json:
            doc.line("Nothing written: `tjs update-tobase-map --write` "
                     "applies it.")
        if not tm.is_file() and not args.json:
            doc.line("There is no ticker.map beside tobase.map (it is not "
                     "read without one): `tjs update-tobase-map --write` "
                     "makes one.")
        return
    if made_map and not args.json:
        doc.line("Wrote an empty ticker.map (tobase.map is read beside one).")
    if not plan.changes and not stale:
        return
    bak = write_user_file(TB.tobase_path(root), plan.new_text, boundary,
                          backup=not args.no_backup)
    if not args.json:
        doc.line(f"Wrote {_PL.tobase_shown(root)}"
                 + (f" (the previous file kept as {bak.name})" if bak else "")
                 + ". Run `tjs run` "
                 + (f"in each year that reads it ({_yrs_text}) "
                    if shared else "")
                 + "to rebuild the books with it.")
    _raise_requirements(root, dirs=readers, quiet=args.json)


def _tobase_map_first_report(args: argparse.Namespace, master: Any, n_lines: int,
                              changes: Any, ov: Any, _det: bool, head: str,
                              no_run: bool) -> None:
    """tjs update-tobase-map with no tobase.map yet: the JSON form, or
    the text: how many pair lines a new file would hold, which of them
    change these books (or that the books are not built yet), and the
    pairs ticker.map decides otherwise."""
    doc = _o.Doc(exact=True, width_=0, live=True)
    if args.json:
        _json_out({"exists": False,
                   "master_generated": master.generated,
                   "lines": n_lines,
                   "changes_books": [{"line": g.rule, "how": how}
                                     for g, how in changes],
                   "ticker_map_decides": [{"line": r, "why": w}
                                          for r, w in ov.overridden],
                   "written": bool(args.write)}, schema="update-tobase-map")
    else:
        if _det:
            for _ln in _out_wrap(head):
                doc.line(_ln)
        for _ln in _out_wrap(
                f"This project has no tobase.map yet: one would hold "
                f"{n_lines} pair line(s)."):
            doc.line(_ln)
        if no_run:
            for _ln in _out_wrap(
                    "The books are not built yet (`tjs run`): "
                    "what the pairs change in them cannot be shown."):
                doc.line(_ln)
        elif changes:
            doc.line(f"{len(changes)} of them change these books (read "
                     f"over their full history, every year the exports "
                     f"hold):")
            for g, how in changes:
                for _ln in _out_wrap(f"- {g.rule}: {how}", "  ",
                                     "    "):
                    doc.line(_ln)
        elif not _det:
            doc.line("None of them changes these books.")
        else:
            for _ln in _out_wrap(
                    "None of them changes these books (ticker.map "
                    "already pools every pair they hold, or they hold "
                    "none): they apply when you trade such a "
                    "security."):
                doc.line(_ln)
        if ov.overridden:
            doc.line(f"ticker.map decides {len(ov.overridden)} pair(s) "
                     f"otherwise (ticker.map wins; reported only):")
            for r, w in ov.overridden:
                for _ln in _out_wrap(f"- {r}" + (f" ({w})" if _det
                                                 else ""),
                                     "  ", "    "):
                    doc.line(_ln)


def _tobase_map_update_json(master: Any, plan: Any, in_books: List[Any], made_map: bool,
                            args: argparse.Namespace) -> None:
    """tjs update-tobase-map --json, a tobase.map there: what the
    installed master changes in it, line by line, and whether it is
    written."""
    _json_out({
        "exists": True,
        "master_generated": master.generated,
        "file_master_generated": plan.old_stamp or None,
        "added": [{"line": g.rule, "figi": g.figi,
                   "section": g.section.strip("#- ")} for g in plan.added],
        "added_in_books": [g.rule for g in in_books],
        "ended": [{"line": ln.rule, "until": g.until} for ln, g in
                  plan.ended],
        "retracted": [{"line": ln.rule, "why": w}
                      for ln, w in plan.retracted],
        "retracted_but_edited": [{"line": ln.rule, "why": w}
                                 for ln, w in plan.retracted_edited],
        "edited": [ln.rule for ln in plan.edited],
        "edited_replaces": [{"master": g.rule, "yours": ln.rule}
                            for g, ln in plan.suppressed],
        "unknown_figi": [ln.rule for ln in plan.unknown],
        "opted_out": [g.rule for g in plan.opted_out],
        "removed_by_you": [g.rule for g in plan.removed_now],
        "not_tobase_lines": plan.others,
        "made_ticker_map": made_map,
        "ticker_changed": [{"old": o.rule, "new": n.rule,
                            "until": o.until, "pooled": pooled}
                           for o, n, pooled in plan.renamed],
        "ticker_map_decides": [{"line": r, "why": w}
                               for r, w in plan.conflicts],
        "written": bool(args.write and (plan.changes or
                                        plan.old_stamp !=
                                        plan.new_stamp))},
              schema="update-tobase-map")


def _tobase_map_update_text(_det: bool, head: str, plan: Any, master: Any,
                            in_books: List[Any], _act: Callable[..., str], TB: Any
                            ) -> None:
    """tjs update-tobase-map, a tobase.map there: the master it was made
    from, the lines added (those naming a symbol of these books first),
    ended, retracted, edited, kept and removed, each ticker change, the
    pairs ticker.map decides otherwise, or that it is up to date."""
    doc = _o.Doc(exact=True, width_=0, live=True)
    if _det:
        for _ln in _out_wrap(head):
            doc.line(_ln)
    doc.line(f"tobase.map was made from the master of "
             f"{plan.old_stamp or 'an unknown date'}"
             + ("." if _det else f"; installed: {master.generated}."))
    if plan.added:
        doc.line(f"Added: {len(plan.added)} line(s)"
                 + (" (new interlistings or listings)" if _det else "")
                 + (f"; {len(in_books)} name a symbol of "
                                 f"these books:" if in_books else "."))
        for g in in_books:
            doc.line(f"  + {g.rule}")
        if len(plan.added) <= 30:
            for g in plan.added:
                if g not in in_books:
                    doc.line(f"  + {g.rule}")
    if plan.ended:
        doc.line(f"Ended: {len(plan.ended)} interlisting(s)"
                 + (" (annotated with `until`, kept for the years they "
                    "traded):" if _det else ":"))
        for ln, g in plan.ended:
            doc.line(f"  ~ {ln.rule} until {g.until}")
    if plan.retracted:
        doc.line(f"Retracted: {len(plan.retracted)} line(s)"
                 + (" the master no longer gives or no longer needs "
                    "(removed; never a line you edited):" if _det
                    else " (removed):"))
        for ln, why in plan.retracted:
            for _ln in _out_wrap(f"- {ln.rule}"
                                 + (f": {why}" if _det else ""),
                                 "  ", "    "):
                doc.line(_ln)
    if plan.retracted_edited and not _det:
        doc.line(_act(f"{len(plan.retracted_edited)} line(s) you edited "
                      f"were retracted by the master: check each",
                      "tjs update-tobase-map --details"))
        for ln, why in plan.retracted_edited:
            doc.line(f"  ? {ln.rule} ({TB.TOBASE_MAP}:{ln.lineno})")
    elif plan.retracted_edited:
        from taxjson.lib.out import attention
        attention(f"{len(plan.retracted_edited)} line(s) you edited are "
                  f"lines the master RETRACTED (kept as you wrote them; "
                  f"check each):",
                  details=[f"! {ln.rule} ({TB.TOBASE_MAP}:{ln.lineno}): "
                           f"{why}" for ln, why in plan.retracted_edited],
                  file=sys.stdout)
    if plan.edited:
        doc.line(f"Left as written: {len(plan.edited)} marked line(s) you "
                 f"edited" + (" (yours now; the master's line for the "
                              "same listing is not added beside it):"
                              if _det else ":"))
        for ln in plan.edited:
            doc.line(f"  = {ln.rule} ({TB.TOBASE_MAP}:{ln.lineno})")
    if plan.unknown:
        doc.line(f"Kept: {len(plan.unknown)} marked line(s) whose FIGI "
                 f"this master does not know:")
        for ln in plan.unknown:
            doc.line(f"  ? {ln.rule} ({TB.TOBASE_MAP}:{ln.lineno})")
    if plan.removed_now:
        doc.line(f"Removed by you: {len(plan.removed_now)} master line(s) "
                 f"you deleted" + (" (recorded as `# removed:` lines and "
                                   "never added again; delete such a "
                                   "line to have it back):" if _det
                                   else ":"))
        for g in plan.removed_now:
            doc.line(f"  x {g.rule}")
    if plan.opted_out:
        doc.line(f"Kept out: {len(plan.opted_out)} master line(s) you "
                 f"opted out of (`# removed:` or commented out).")
    if plan.others:
        doc.line(f"Kept as written: {len(plan.others)} line(s) that are "
                 f"not TOBASE / DISTINCT lines" + (" (the run refuses "
                                                   "them: move them to "
                                                   "ticker.map):" if _det
                                                   else " (move them to "
                                                   "ticker.map):"))
        for r in plan.others:
            doc.line(f"  ! {r.strip()}")
    for o, n, pooled in plan.renamed:
        old_sym, new_sym = o.a, n.a
        when = o.until if o.until and o.until != "unknown" else \
            "YYYY-MM-DD"
        if pooled:
            msg = (f"Ticker change: {old_sym} is now {new_sym} (the "
                   f"old line is kept for the years it traded). Both "
                   f"are booked as {n.b} already: nothing more to "
                   f"write.")
        else:
            msg = (f"Ticker change: {old_sym} is now {new_sym} (the "
                   f"old line is kept for the years it traded). Book "
                   f"the change as a dated event: a .tt line `RENAME "
                   f"{when} {old_sym} {new_sym}` (the first day it "
                   f"traded as {new_sym}).")
        for _ln in _out_wrap(msg, "", "  "):
            doc.line(_ln)
    if plan.conflicts:
        doc.line(f"ticker.map decides {len(plan.conflicts)} pair(s) "
                 f"otherwise (ticker.map wins; reported only):")
        for r, w in plan.conflicts:
            for _ln in _out_wrap(f"- {r}" + (f" ({w})" if _det else ""),
                                 "  ", "    "):
                doc.line(_ln)
    if not plan.changes:
        doc.line(f"tobase.map is up to date with the installed master.")


def _tobase_root_year(top: Path, as_json: bool) -> Path:
    """`update-tobase-map` in the folder holding the year folders: the
    newest year that reads the tobase.map every year shares (the file is
    updated as from there). Dies when there is no such file or it is
    ambiguous: a year keeps a copy of its own, or the years name
    different files."""
    yrs = _PL.year_dirs(top)
    if not yrs:
        _die_input(f"no taxjson.toml in {top}: run it in a project's "
                   f"(year) folder")
    lay = _PL.tobase_layout(top)
    if lay["own"]:
        _die_input(f"the year folders {', '.join(str(y) for y in sorted(lay['own']))} "
                   f"keep a tobase.map of their own — which file to "
                   f"update is ambiguous here",
                   "`tjs migrate` here makes the copies one file every "
                   "year reads; or run update-tobase-map in a year folder.")
    if len(lay["shared"]) != 1:
        if not lay["shared"]:
            _die_input("no year folder reads a shared tobase.map "
                       f"([settings] {_PL.TOBASE_KEY}) — run "
                       f"update-tobase-map in a year folder")
        _die_input("the year folders read different tobase.map files: "
                   + "; ".join(f"{_PL.shown(p, top)} ({', '.join(map(str, ys))})"
                               for p, ys in sorted(lay["shared"].items())),
                   "Point each year's [settings] tobase_map at one file, "
                   "or run update-tobase-map in a year folder.")
    (_p, ys), = lay["shared"].items()
    year = max(ys)
    doc = _o.Doc(exact=True, width_=0, live=True)
    if not as_json:
        for _ln in _out_wrap(f"(In the folder holding the years: the shared "
                             f"{_PL.shown(_p, top)}, updated as from "
                             f"{year}/, the newest year that reads it; "
                             f"ticker.map conflicts are {year}'s.)"):
            doc.line(_ln)
    return top / str(year)


def _register_format_map(sub: argparse._SubParsersAction) -> None:
    p_fmap = sub.add_parser(
        "format-map", help="Lay out ticker.map in keyword groups",
        description="Rewrite ticker.map in the layout `tjs init` "
                    "writes: a short header, then the rules in groups "
                    "(Spellings, Listings of one security, Clean-up, Dated "
                    "events, Lookups), each under its heading, your order "
                    "kept within a group. A comment directly above a line "
                    "moves with it; other comments stay where they are; "
                    "spacing is normalised and exact duplicate lines "
                    "dropped; a line taxjson cannot use is kept as written "
                    "in an \"Unrecognized\" group. The parsed map must "
                    "stay identical or nothing is written. Default: print "
                    "the changes as a diff.")
    _fmap_mode = p_fmap.add_mutually_exclusive_group()
    _fmap_mode.add_argument("--write", action="store_true",
                            help="Write the formatted file (the previous "
                                 "one is kept as ticker.map.bak)")
    _fmap_mode.add_argument("--check", action="store_true",
                            help="Exit 1 when the file is not formatted "
                                 "(writes nothing; for CI)")
    p_fmap.add_argument("--no-backup", action="store_true",
                        help="With --write: no ticker.map.bak")
    p_fmap.set_defaults(func=cmd_format_map)


def _register_update_tobase_map(sub: argparse._SubParsersAction) -> None:
    p_tb = sub.add_parser(
        "update-tobase-map",
        help="Update the interlisted pairs (tobase.map)",
        description="Canada: compare the project's tobase.map, the "
                    "interlisted master's pairs (a TSX line and its US "
                    "exchange and OTC listings: one security), with the "
                    "master this taxjson installs, and list what an update "
                    "does: lines added (new interlistings), ended "
                    "interlistings (annotated with `until`, never "
                    "deleted), retracted lines (removed only when you did "
                    "not edit them), ticker changes (the new line added, "
                    "the old kept, with the dated RENAME event to write), "
                    "the pairs your ticker.map decides otherwise "
                    "(ticker.map wins; reported only), and the DISTINCT "
                    "lines earlier versions wrote for a depositary receipt "
                    "(retracted: look-alike listings are never joined). "
                    "Without a tobase.map it shows the pairs that would "
                    "change the books. In a year folder that reads the "
                    "tobase.map every year shares ([settings] tobase_map), "
                    "or in the folder holding the years, it updates that "
                    "one file for every year. Dry run by default; --write "
                    "applies it.")
    p_tb.add_argument("--write", action="store_true",
                      help="Write tobase.map (the previous one is kept as "
                           "tobase.map.bak)")
    p_tb.add_argument("--no-backup", action="store_true",
                      help="With --write: no tobase.map.bak")
    p_tb.add_argument("--json", action="store_true",
                      help="Emit JSON (docs/settings.md)")
    p_tb.set_defaults(func=cmd_update_tobase_map)


def _register_ticker_map(sub: argparse._SubParsersAction) -> None:
    p_tm = sub.add_parser(
        "ticker-map",
        help="ticker.map lines the last run suggested; add them",
        description="Every ticker.map line the last `tjs run` "
             "suggested — two listings a transfer journal pairs but the "
             "run did not join, a Questrade code with a likely ticker, "
             "a ticker change IB, Questrade or RBC shows, a coin's "
             "Yahoo id — each with its reason; the listing pairs that "
             "share their letters with no TOBASE or DISTINCT line (to "
             "verify); the rules no symbol of the books reaches (unused, "
             "never written); in Canada, your TOBASE / DISTINCT lines "
             "tobase.map states identically (covered: delete?). --write "
             "appends the chosen ones (asked one by one on a terminal; "
             "--all: every line the run's evidence names, never a pair to "
             "verify, never a covered line deleted), with a comment, "
             "never one the map already answers, keeping a backup of the "
             "old file; on a terminal it also asks keep / delete for each "
             "covered line (keep is the default).")
    p_tm.add_argument("--suggest", action="store_true",
                      help="List the suggested lines (required)")
    p_tm.add_argument("--write", action="store_true",
                      help="Append the chosen lines to ticker.map")
    p_tm.add_argument("--all", action="store_true",
                      help="With --write: append every line the run's "
                           "evidence names without asking (never a "
                           "listing pair to verify)")
    p_tm.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_tm.set_defaults(func=cmd_ticker_map)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_format_map(sub)
    _register_update_tobase_map(sub)
    _register_ticker_map(sub)
