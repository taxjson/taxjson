#!/usr/bin/env python3
"""Resolve broker corporate-action events into taxjson rows.

Usage:
    taxjson-corp-actions --brokerage ib IB.csv --country canada > corp.json
    taxjson-corp-actions --brokerage ib stmt_q1.csv stmt_q2.csv \\
        --manifest elections.json > corp.json

All events from all years are surfaced — even old ones — because corp
actions shape inventory across years. An unresolved 2024 spinoff would
leave the 2025 pool wrong; the downstream `taxjson-gains --year` filter
takes care of selecting which year actually realizes the gain.

Elections are stored next to the CSV as `.<csv>.elections` (a hidden
JSON file) by default. On first run with new events the tool prompts
interactively (TTY required); on subsequent runs the saved elections
let it emit deterministically without prompts. A non-TTY run that hits
an unresolved event errors out instead of silently picking a default —
the wrong tax treatment is much worse than a hung pipeline.

Multiple input files: pass several CSV paths and the extractor runs
once per file; events concatenate. The default hidden-manifest path
only makes sense for a single file, so `--manifest` is required when
more than one input is given.

Every event prompt also offers an `ignore` option for IB noise rows
(cross-listing journals, sub-account moves, etc.) that look like corp
actions but aren't economically taxable events. Ignored events still
go into the manifest, so future runs skip them silently.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from taxjson.lib.corp_actions import (
    CorporateAction,
    ElectionRecord,
    apply_auto_defaults,
    HINTS_BY_ELECTION,
    IGNORE_ELECTION,
    Manifest,
    RULES_BY_COUNTRY,
    options_for,
    parse_ib_corporate_actions,
    parse_questrade_corporate_actions,
    parse_rbc_corporate_actions,
    resolve_event,
)


# Registry of (brokerage_id → extractor). Each extractor takes a CSV
# path plus an account label and returns a list of CorporateAction
# events. Extractors that don't natively report an account (Questrade's
# CSV puts the account # in a column we ignore for now) accept the
# label from `--account-name` so downstream tools see the right pool key.
EXTRACTORS = {
    'ib': parse_ib_corporate_actions,
    'interactive_brokers': parse_ib_corporate_actions,
    'questrade': parse_questrade_corporate_actions,
    'qt': parse_questrade_corporate_actions,
    'rbc': parse_rbc_corporate_actions,
    'rbc_direct': parse_rbc_corporate_actions,
}


def _default_manifest_path(csv_path: Path) -> Path:
    """`<dir>/.<csv-filename>.elections` — sits next to the CSV, hidden
    by default so it doesn't clutter directory listings but is easy to
    find via `ls -a` when the user wants to audit decisions."""
    return csv_path.parent / f".{csv_path.name}.elections"


def _format_options(options) -> str:
    lines = []
    for i, (key, desc) in enumerate(options, start=1):
        lines.append(f"  [{i}] {key}\n      {desc}")
    return "\n".join(lines)


def _prompt_election(event: CorporateAction, country: str) -> ElectionRecord:
    """Walk the user through one event. Always includes an `ignore` option
    at the end, regardless of country/event_type, since IB noise is the
    common case where no country-specific tax rule applies."""
    options = options_for(country, event.action_type)
    if not options or options == [IGNORE_ELECTION]:
        # No country rule for this event type — only `ignore` makes sense.
        # The user can still mark it ignored and move on.
        print(
            f"warning: no {country} rule for event type '{event.action_type}'. "
            f"You can ignore this event or add a rule in taxjson.lib.corp_actions.",
            file=sys.stderr,
        )

    print(file=sys.stderr)
    print(f"EVENT {event.event_id} — {event.summary()}", file=sys.stderr)
    print(f"  qty_disposed: {event.qty_disposed}", file=sys.stderr)
    print(f"  qty_received: {event.qty_received}", file=sys.stderr)
    print(f"  account:      {event.account}", file=sys.stderr)
    if event.raw_descriptions:
        print("  source row(s):", file=sys.stderr)
        for d in event.raw_descriptions:
            print(f"    {d}", file=sys.stderr)
    print(file=sys.stderr)
    print("Tax treatment options:", file=sys.stderr)
    print(_format_options(options), file=sys.stderr)
    print(file=sys.stderr)

    # input() writes its prompt to stdout, which leaks into the emitted
    # JSON when the user redirects output to a file. Print prompts to
    # stderr ourselves and call input() with no argument so it stays
    # silent on stdout. Same pattern applies to every prompt below.
    def _ask(prompt: str) -> str:
        sys.stderr.write(prompt)
        sys.stderr.flush()
        return input().strip()

    while True:
        choice = _ask(f"Choose an option [1-{len(options)}, "
                      f"Enter = 1 {options[0][0]}]: ")
        if not choice:
            choice = "1"            # Enter takes the listed default
        try:
            idx = int(choice) - 1
            # Explicit range check: int('0')-1 == -1 previously fell
            # into Python NEGATIVE indexing and silently elected the
            # LAST option (`ignore`) — a correctness trap, not a typo.
            if not 0 <= idx < len(options):
                raise IndexError
            key, _ = options[idx]
            break
        except (ValueError, IndexError):
            print(f"  invalid choice '{choice}' — pick a number from "
                  f"the list", file=sys.stderr)
    print(f"  Recorded: {key}", file=sys.stderr)

    # Some elections need extra numeric input (FMV, allocated ACB, etc).
    # Prompt for each declared hint and store on the manifest record.
    # Empty input was previously coerced to 0 silently, which meant a
    # user pressing Enter to "skip" recorded `fmv_per_share=0.0` and
    # emitted zero-value tax rows. Confirm explicitly when the user
    # wants 0 so the choice is conscious.
    hints: dict = {}
    for hint_spec in HINTS_BY_ELECTION.get(key, []):
        hint_key, prompt_text = hint_spec[0], hint_spec[1]
        # Optional third element: needed(event) predicate — skip the prompt
        # when the event already carries the number (broker-reported FMV).
        if len(hint_spec) > 2 and not hint_spec[2](event):
            continue
        while True:
            raw = _ask(f"  {hint_key}: {prompt_text}\n  > ")
            if raw == '':
                confirm = _ask(
                    f"  empty input — record {hint_key}=0? "
                    f"(y to confirm, anything else to re-enter): "
                )
                if confirm.lower() != 'y':
                    continue
                raw = '0'
            try:
                hints[hint_key] = float(raw)
                break
            except ValueError:
                print(f"  '{raw}' isn't a number — try again", file=sys.stderr)

    notes = _ask("Notes (optional, will be saved with the election): ")
    return ElectionRecord(
        event_id=event.event_id,
        summary=event.summary(),
        election=key,
        notes=notes,
        hints=hints,
    )


def _load_manifest_or_die(path: Path) -> Manifest:
    try:
        return Manifest.load(path)
    except ValueError as e:
        print(f"taxjson-corp-actions: error: {e}", file=sys.stderr)
        raise SystemExit(2)


def _emit_resolved(events: List[CorporateAction], manifest: Manifest, country: str) -> dict:
    """Emit taxjson rows for every event. Events whose election is
    `ignore` produce no rows (resolve_event returns an empty list).
    Hints stored on the manifest record (FMV, ACB allocation, etc.)
    flow into the country rule via `resolve_event`."""
    transactions = []
    emitted_event_count = 0
    ignored = 0
    emitted_ids: set = set()
    for ev in events:
        # Overlapping statement CSVs (partial-year + full-year downloads)
        # surface the same corporate event once per file. Emit each event_id
        # once — double emission scaled the pool by the ratio twice (or
        # disposed the source twice on a taxable election). Byte-identical
        # dedup downstream can't be relied on: IB rows carry statement-
        # specific times.
        if ev.event_id in emitted_ids:
            continue
        emitted_ids.add(ev.event_id)
        rec = manifest.get(ev.event_id)
        if rec is None:
            raise RuntimeError(f"event {ev.event_id} unresolved at emit time")
        rows = resolve_event(ev, rec.election, country=country, hints=rec.hints)
        if rows:
            emitted_event_count += 1
            transactions.extend(rows)
        else:
            ignored += 1
    return {
        'transactions': transactions,
        'metadata': {
            'source': 'taxjson-corp-actions',
            'country': country,
            'event_count': emitted_event_count,
            'ignored_count': ignored,
        },
    }


def _unresolved(events, manifest):
    return [ev for ev in events if manifest.get(ev.event_id) is None]


# Exit code when elections are REQUIRED but prompting isn't possible
# (--no-input or no TTY). Distinct from 2 (usage/data errors) so
# orchestrators and GUIs can branch on "needs a decision" vs "broken".
EXIT_ELECTIONS_REQUIRED = 3


def _pending_doc(missing: List[CorporateAction], manifest_path: Path,
                 country: str) -> dict:
    """Machine-readable pending-elections document — everything a GUI
    (or `taxjson elect --set`) needs to resolve each event without a
    TTY: the event's identity/quantities/source rows, every available
    election with its description, and the numeric hints that election
    would prompt for (hint `needed` predicates evaluated against THIS
    event, so a broker-reported FMV drops its hint here exactly like
    the interactive path skips the prompt)."""
    pending = []
    for ev in missing:
        options = []
        for key, desc in options_for(country, ev.action_type):
            hints = []
            for hint_spec in HINTS_BY_ELECTION.get(key, []):
                if len(hint_spec) > 2 and not hint_spec[2](ev):
                    continue
                hints.append({"key": hint_spec[0],
                              "prompt": hint_spec[1]})
            options.append({"election": key, "description": desc,
                            "hints": hints})
        pending.append({
            "event_id": ev.event_id,
            "summary": ev.summary(),
            "account": ev.account,
            "action_type": ev.action_type,
            "qty_disposed": ev.qty_disposed,
            "qty_received": ev.qty_received,
            # Decision-relevant facts a renderer can show without
            # parsing the summary string (first step toward a numeric
            # consequence preview).
            "date": ev.date,
            "fmv": ev.fmv,
            "currency": ev.currency,
            "ratio_new": ev.ratio_new,
            "ratio_old": ev.ratio_old,
            "source_rows": list(ev.raw_descriptions or []),
            "options": options,
        })
    return {"schema_version": 1, "country": country,
            "manifest": str(manifest_path), "pending": pending}


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Resolve broker corporate-action events into taxjson rows. "
            "Manages an elections manifest automatically so re-runs are "
            "deterministic; prompts only when new events appear and a TTY "
            "is available."
        )
    )
    parser.add_argument(
        '--brokerage',
        dest='brokerage_id',
        required=True,
        choices=sorted(EXTRACTORS),
        help="Brokerage that produced the statement",
    )
    parser.add_argument(
        'inputs',
        nargs='+',
        metavar='input',
        help=(
            "Broker statement CSV(s). Multiple files are extracted in "
            "order and their events concatenated. Use --manifest when "
            "passing more than one file."
        ),
    )
    parser.add_argument(
        '--country', default='canada', choices=sorted(RULES_BY_COUNTRY),
        help="Tax jurisdiction whose election rules to apply (default: canada)",
    )
    parser.add_argument(
        '--manifest',
        help=(
            "Path to elections manifest. Defaults to `.<csv>.elections` "
            "next to the input CSV — hidden by leading dot so it doesn't "
            "clutter listings."
        ),
    )
    parser.add_argument(
        '--account-name',
        metavar='NAME',
        default=None,
        help=(
            "Account label stamped on every emitted taxjson row (e.g. "
            "'Margin', 'RRSP', 'TFSA', 'LIRA'). Matches the equivalent "
            "flag on taxjson-brokerage so corp-action rows pool correctly "
            "with the broker's other trades for the same account. When "
            "omitted, the extractor's own DEFAULT_ACCOUNT (e.g. 'IB', "
            "'Questrade') is kept — previously this defaulted to the "
            "literal 'default', causing rows from the same security to "
            "split across two pools when taxjson-brokerage was run "
            "without --account-name on the same data."
        ),
    )
    parser.add_argument(
        '--list', dest='list_only', action='store_true',
        help="Just print extracted events; don't prompt, don't write, don't emit JSON.",
    )
    parser.add_argument(
        '--no-input', action='store_true',
        help="Never prompt, even at a TTY — unresolved events exit "
             f"{EXIT_ELECTIONS_REQUIRED} (see --pending-json).",
    )
    parser.add_argument(
        '--pending-json', metavar='FILE', default=None,
        help="When elections are required and prompting isn't possible, "
             "write the pending events (with their options and hints) "
             "as JSON to FILE for a GUI or `taxjson elect --set` to "
             "resolve.",
    )
    args = parser.parse_args()

    csv_paths = [Path(p) for p in args.inputs]
    missing = [p for p in csv_paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"taxjson-corp-actions: error: input CSV not found: {p}",
                  file=sys.stderr)
        raise SystemExit(2)

    if args.manifest:
        manifest_path = Path(args.manifest)
    elif len(csv_paths) == 1:
        manifest_path = _default_manifest_path(csv_paths[0])
    else:
        # The hidden `.<csv>.elections` default would silently bind to
        # one of N files; we'd rather force an explicit choice than guess.
        print(
            "taxjson-corp-actions: error: --manifest is required when multiple input files are given "
            "(the default hidden manifest is per-file and can't safely cover "
            "multiple CSVs).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    extractor = EXTRACTORS[args.brokerage_id]
    events = []
    for csv_path in csv_paths:
        # Only override the extractor's own default account label when
        # the user explicitly passed --account-name. Mirrors the same
        # change made to taxjson-brokerage so a forgotten flag doesn't
        # clobber a meaningful default with the literal string 'default'.
        if args.account_name is None:
            events.extend(extractor(csv_path))
        else:
            events.extend(extractor(csv_path, args.account_name))

    if args.list_only:
        for ev in events:
            print(f"{ev.event_id}  {ev.summary()}")
        return

    if not events:
        # Empty result is still a valid JSON document — keeps downstream
        # pipelines that always feed this output into `taxjson-merge`
        # working without conditional logic.
        json.dump({'transactions': [], 'metadata': {
            'source': 'taxjson-corp-actions', 'country': args.country, 'event_count': 0,
        }}, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return

    manifest = _load_manifest_or_die(manifest_path)

    # Rekey elections saved under the pre-2026-07 opaque hash ids to
    # the human-legible scheme — silently, before anything consults
    # the manifest, so an id-scheme change never re-prompts for (or
    # orphans) decisions the user already made.
    if manifest.migrate_legacy(events):
        manifest.save(manifest_path)

    # Auto-defaults first: event types with exactly one sane treatment
    # (name changes) are elected without prompting — the manifest record
    # is still written, so the audit trail is intact and `taxjson elect
    # --redo` can override like any hand-made election.
    auto_applied = apply_auto_defaults(events, manifest, args.country)
    if auto_applied:
        manifest.save(manifest_path)
        for ev in auto_applied:
            print(f"note: auto-elected {ev.event_id} ({ev.summary()}) — "
                  f"no decision required; `taxjson elect --redo` to "
                  f"override.", file=sys.stderr)

    missing = _unresolved(events, manifest)

    if missing:
        if args.no_input or not sys.stdin.isatty():
            reason = ("--no-input" if args.no_input
                      else "stdin is not a TTY")
            print(
                f"taxjson-corp-actions: error: {len(missing)} corp-action event(s) need an election but "
                f"{reason}. Run interactively to populate "
                f"{manifest_path}, or set each non-interactively with "
                f"`taxjson elect <account> --set <event_id>=<election>`:",
                file=sys.stderr,
            )
            for ev in missing:
                print(f"  - {ev.event_id}: {ev.summary()}", file=sys.stderr)
            if args.pending_json:
                doc = _pending_doc(missing, manifest_path, args.country)
                Path(args.pending_json).parent.mkdir(parents=True,
                                                     exist_ok=True)
                Path(args.pending_json).write_text(
                    json.dumps(doc, indent=2, sort_keys=True),
                    encoding="utf-8")
                print(f"taxjson-corp-actions: note: pending elections "
                      f"written to {args.pending_json}", file=sys.stderr)
            raise SystemExit(EXIT_ELECTIONS_REQUIRED)

        print(
            f"\nFound {len(missing)} new corp-action event(s) needing an election.\n"
            f"Decisions will be saved to {manifest_path} for future runs.\n",
            file=sys.stderr,
        )
        for ev in missing:
            try:
                rec = _prompt_election(ev, args.country)
            except (EOFError, KeyboardInterrupt):
                # Answers given SO FAR are already saved (per-event
                # save below) — losing six typed FMVs to a Ctrl-C at
                # event 7 defeated the whole atomic-manifest design,
                # and EOF used to print two stacked tracebacks.
                print(f"\ntaxjson-corp-actions: interrupted — "
                      f"{manifest_path} keeps the elections answered "
                      f"so far; rerun to continue with the rest.",
                      file=sys.stderr)
                raise SystemExit(EXIT_ELECTIONS_REQUIRED)
            manifest.set(rec)
            manifest.save(manifest_path)   # atomic; never lose answers
        print(f"\nManifest saved to {manifest_path}\n", file=sys.stderr)

    out = _emit_resolved(events, manifest, args.country)
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == '__main__':
    main()
