#!/usr/bin/env python3
"""Resolve broker corporate-action events into taxjson rows.

Usage:
    taxjson-corp-actions --brokerage ib IB.csv --country canada > corp.json
    taxjson-corp-actions --brokerage ib stmt_q1.csv stmt_q2.csv \\
        --country canada --manifest elections.json > corp.json

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

from taxjson.lib.stage_msg import emit_line
import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from taxjson.lib import out as _out
from taxjson.lib.safe_write import write_atomic
from taxjson.lib.country import add_country_argument

from taxjson.lib.corp_actions import (
    CorporateAction,
    ElectionRecord,
    apply_auto_defaults,
    HINTS_BY_ELECTION,
    IGNORE_ELECTION,
    Manifest,
    combine_broker_copies,
    options_for,
    parse_ib_corporate_actions,
    parse_questrade_corporate_actions,
    parse_rbc_corporate_actions,
    rates_converter,
    read_tt_sales,
    resolve_event,
    SHELTERED_ELECTION_MODES,
    SHELTERED_ELECTIONS_DEFAULT,
    sheltered_default_applies,
    sheltered_default_rows,
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


def extract_events(extractor, csv_paths: List[Path],
                   account: Optional[str] = None,
                   renames: Optional[dict] = None,
                   symbol_codes: Optional[Path] = None
                   ) -> List[CorporateAction]:
    """Run `extractor` over every file of one broker group of an
    account. Extractors that resolve securities by name (Questrade and
    RBC spin-off parents, RBC merger placeholders) see ALL the group's
    files as context: the parent was usually bought in another export.
    `symbol_codes`: the account's internal-code record (work/<acct>
    _symbol_codes.state), for an extractor that books a chain under
    a broker's internal code (Questrade). The one extraction definition
    for the CLI, `taxjson elect` and `taxjson spinoffs`, so all three
    compute the same event ids and targets."""
    events: List[CorporateAction] = []
    paths = [Path(p) for p in csv_paths]
    for csv_path in paths:
        kw = ({'context_files': paths}
              if getattr(extractor, 'accepts_context', False) else {})
        if renames and getattr(extractor, 'accepts_renames', False):
            kw['renames'] = renames
        if (symbol_codes
                and getattr(extractor, 'accepts_symbol_codes', False)):
            kw['symbol_codes'] = symbol_codes
        if account is None:
            events.extend(extractor(csv_path, **kw))
        else:
            events.extend(extractor(csv_path, account, **kw))
    return events


def map_renames(path: Path) -> dict:
    """The renames of a ticker.map as the extractors read them (a
    spin-off's parent named by a broker code: `GLOBAL X123456
    PARENT.TO`). OSError / ValueError when the map cannot be read."""
    from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                merge_renames)
    return merge_renames(_parse_map_file(Path(path))[0], to_base=True)


def project_corp_map(root: Path) -> Optional[Path]:
    """The map `taxjson run`'s corporate-action stage read for the
    project at `root`: the run's effective map (work/ticker.map.effective:
    ticker.map plus the listings it joined and the .tt renames) when the
    last run wrote one, else ticker.map; None without either. `taxjson
    elect` extracts with it, so its event ids are the run's (backlog
    TJ-0849)."""
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.cross_listings import EFFECTIVE_MAP
    for p in (Path(root) / "work" / EFFECTIVE_MAP,
              _PL.ticker_map_path(root)):
        if p.is_file():
            return p
    return None


def project_corp_renames(root: Path) -> Optional[dict]:
    """map_renames of project_corp_map (None when there is no map or it
    cannot be read: `taxjson run` refuses a bad map up front)."""
    p = project_corp_map(root)
    if p is None:
        return None
    try:
        return map_renames(p) or None
    except (OSError, ValueError):
        return None


def _default_manifest_path(csv_path: Path) -> Path:
    """`<dir>/.<csv-filename>.elections` — sits next to the CSV, hidden
    by default so it doesn't clutter directory listings but is easy to
    find via `ls -a` when the user wants to audit decisions."""
    return csv_path.parent / f".{csv_path.name}.elections"


def _format_options(options) -> str:
    """The numbered option list of the election prompt: the key on the
    number's line, its description wrapped under it."""
    lines = []
    for i, (key, desc) in enumerate(options, start=1):
        lines.append(f"  [{i}] {key}")
        lines.extend(_out.wrap(desc, indent="      ", stream=sys.stderr))
    return "\n".join(lines)


# Said with an event of a sheltered (registered) account when it is
# asked (sheltered_elections = "ask", or an event the default does not
# book — corp_actions.sheltered_default_applies): the election sets the
# cost the holdings view and the sheltered books carry, and no tax in
# the account depends on it; the holdings still count for the taxable
# accounts' superficial-loss rule (Canada, CA-SL-*) or wash-sale rule
# (USA, US-WASH-*), each country's own.
def sheltered_note(country: str) -> str:
    """The sentence (no final period) said with a sheltered account's
    event, in the project's country's terms."""
    from taxjson.lib.country import is_usa
    rule = "wash-sale" if is_usa(country) else "superficial-loss"
    return (f"sheltered account: this election sets the holdings' cost "
            f"in the books — no tax in this account; its holdings still "
            f"count for the {rule} rule")


def _prompt_election(event: CorporateAction, country: str,
                     sheltered: bool = False) -> ElectionRecord:
    """Walk the user through one event. Always includes an `ignore` option
    at the end, regardless of country/event_type, since IB noise is the
    common case where no country-specific tax rule applies. `sheltered`:
    the event is a sheltered account's — said (sheltered_note), still
    asked."""
    options = options_for(country, event.action_type)
    if not options or options == [IGNORE_ELECTION]:
        # No country rule for this event type — only `ignore` makes sense.
        # The user can still mark it ignored and move on.
        _out.warn(f"no {country} rule for event type "
                  f"'{event.action_type}'",
                  details=["You can ignore this event, or add a rule in "
                           "taxjson.lib.corp_actions."])

    doc = _out.Doc(stream=sys.stderr)
    doc.section(f"EVENT {event.event_id}")
    doc.para(event.summary(), indent="  ")
    if sheltered:
        note = sheltered_note(country)
        doc.para(note[0].upper() + note[1:] + ".", indent="  ")
    doc.kv([("account", event.account),
            ("qty disposed", f"{event.qty_disposed:g}"),
            ("qty received", f"{event.qty_received:g}")], indent="  ")
    if event.raw_descriptions:
        doc.line("  source rows:")
        for d in event.raw_descriptions:
            doc.line(f"    {d}")
    doc.section("Tax treatment options")
    doc.line(_format_options(options))
    print(file=sys.stderr)
    doc.print(file=sys.stderr)
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
        # The explanation once; a re-entry asks for the number only.
        print(file=sys.stderr)
        for ln in _out.wrap(f"{hint_key}: {prompt_text}", indent="  ",
                            hang="    ", stream=sys.stderr):
            print(ln, file=sys.stderr)
        while True:
            raw = _ask(f"  {hint_key} = ")
            if raw == '':
                confirm = _ask(
                    f"  empty input — record {hint_key}=0? "
                    f"(y to confirm, anything else to re-enter): "
                )
                if confirm.lower() != 'y':
                    continue
                raw = '0'
            try:
                val = float(raw)
            except ValueError:
                print(f"  '{raw}' isn't a number — try again", file=sys.stderr)
                continue
            from taxjson.lib.corp_actions import hint_value_problem
            prob = hint_value_problem(hint_key, val)
            if prob:
                print(f"  {prob} — try again", file=sys.stderr)
                continue
            hints[hint_key] = val
            break

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
        emit_line(f"taxjson-corp-actions: error: {e}", file=sys.stderr)
        raise SystemExit(2)


def _update_or_die(path: Path, change):
    """Manifest.update (load, change, save as one locked step; backlog
    TJ-0544), its refusals one error line, exit 2, as a load's."""
    from taxjson.lib.safe_write import LockLinkError
    try:
        return Manifest.update(path, change)
    except (ValueError, LockLinkError) as e:
        emit_line(f"taxjson-corp-actions: error: {e}", file=sys.stderr)
        raise SystemExit(2)


def _log_saved(log: Optional[str], records) -> None:
    """Append each saved ElectionRecord of `records` to the --saved-log
    file (one JSON line each; nothing without one)."""
    if not log:
        return
    from dataclasses import asdict
    with open(log, 'a', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(asdict(rec), sort_keys=True) + '\n')


def _emit_resolved(events: List[CorporateAction], manifest: Manifest,
                   country: str, fx=None,
                   sheltered_default: frozenset = frozenset(),
                   tt_sales: Optional[List[dict]] = None) -> dict:
    """Emit taxjson rows for every event. Events whose election is
    `ignore` produce no rows (resolve_event returns an empty list).
    Hints stored on the manifest record (FMV, ACB allocation, etc.)
    flow into the country rule via `resolve_event`. An event id in
    `sheltered_default` with no saved election is a sheltered account's
    event booked without asking (corp_actions.sheltered_default_rows);
    a saved election always wins."""
    transactions = []
    emitted_event_count = 0
    ignored = 0
    defaulted = []
    # Overlapping statement CSVs (partial-year + full-year downloads)
    # surface the same corporate event once per file: emitting each copy
    # scaled the pool by the ratio twice (or disposed the source twice).
    # But the same event in a SECOND broker account is a second holding,
    # not a duplicate — combine_broker_copies tells the two apart (it is
    # idempotent; main() already applied it and printed its notes).
    import io
    events = combine_broker_copies(events, stream=io.StringIO())
    for ev in events:
        rec = manifest.get(ev.event_id)
        if rec is None and ev.event_id in sheltered_default:
            emitted_event_count += 1
            transactions.extend(sheltered_default_rows(ev, country))
            defaulted.append(ev.event_id)
            continue
        if rec is None:
            raise RuntimeError(f"event {ev.event_id} unresolved at emit time")
        rows = resolve_event(ev, rec.election, country=country,
                             hints=rec.hints, fx=fx, tt_sales=tt_sales)
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
            # A sheltered account's events booked without asking.
            'sheltered_defaults': sorted(set(defaulted)),
        },
    }


def _unresolved(events, manifest):
    return [ev for ev in events if manifest.get(ev.event_id) is None]


# Exit code when elections are REQUIRED but prompting isn't possible
# (--no-input or no TTY). Distinct from 2 (usage/data errors) so
# orchestrators and GUIs can branch on "needs a decision" vs "broken".
EXIT_ELECTIONS_REQUIRED = 3


def _pending_doc(missing: List[CorporateAction], manifest_path: Path,
                 country: str, sheltered: bool = False) -> dict:
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
            # A spin-off's fraction paid in cash: the holding was
            # entitled to it too (`taxjson elect --pending` says so; the
            # s.86.1 allocation covers it, CA-CORP-05; backlog TJ-0166).
            "cash_in_lieu": float(ev.cash_in_lieu or 0.0),
            "cash_in_lieu_currency": ev.cash_in_lieu_currency or "",
            "fraction_in_lieu": float(ev.fraction_in_lieu or 0.0),
            "cash_in_lieu_date": ev.cash_in_lieu_date or "",
            "options": options,
            # A sheltered account's event that is asked: no tax in the
            # account depends on it (sheltered_note).
            "sheltered": bool(sheltered),
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
    add_country_argument(
        parser, help="Tax jurisdiction whose election rules to apply "
                     "(required): canada | usa")
    parser.add_argument(
        '--manifest',
        help=(
            "Path to elections manifest. Defaults to `.<csv>.elections` "
            "next to the input CSV — hidden by leading dot so it doesn't "
            "clutter listings."
        ),
    )
    parser.add_argument(
        '--manifest-shown', metavar='TEXT', default=None,
        help="How the messages name the manifest (`taxjson run` passes "
             "it as `taxjson elect` shows it: relative to the project, "
             "../inputs/<account>/manifest.json for shared exports); "
             "default: --manifest as given.",
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
        '--rates', metavar='FILE', default=None,
        help="Rates file (the conversion stage's `DATE TIME FROM TO RATE` "
             "lines) used to express a cross-currency exchange's value in "
             "each leg's own currency at the event date.",
    )
    parser.add_argument(
        '--base-currency', metavar='CUR', default=None,
        help="The rates file's target (base) currency (default: the "
             "--country's home currency, CAD or USD).",
    )
    parser.add_argument(
        '--decisions-only', action='store_true',
        help='Save to the manifest only the elections answered at the '
             'prompt: never a rekey of old ids or an automatic election '
             '(a manifest shared by every year, written by no run as a '
             'side effect)')
    parser.add_argument(
        '--ticker-map', metavar='FILE', default=None,
        help="The project's ticker.map (`taxjson run` passes it): a "
             "temporary code it already renames is not warned about.",
    )
    parser.add_argument(
        '--symbol-codes', metavar='FILE', default=None,
        help="The account's internal-code record (`taxjson run` passes "
             "work/<acct>_symbol_codes.state): a Questrade chain booked "
             "under an internal code takes the listing it resolves to.",
    )
    parser.add_argument(
        '--sheltered', action='store_true',
        help="The account is sheltered (registered): no tax in the "
             "account depends on an election, which only sets the "
             "holdings' cost; the holdings still count for the "
             "superficial-loss / wash-sale rule (`taxjson run` passes "
             "it). See --sheltered-elections.",
    )
    parser.add_argument(
        '--sheltered-elections', choices=SHELTERED_ELECTION_MODES,
        default=SHELTERED_ELECTIONS_DEFAULT,
        help="With --sheltered: \"zero\" (the default) books a spin-off "
             "or merger that has no saved election without asking — a "
             "spin-off's new shares at $0 cost, the parent keeping its "
             "cost; a merger's new shares taking the old shares' cost; "
             "\"ask\" asks as in a taxable account ([settings] "
             "sheltered_elections).",
    )
    parser.add_argument(
        '--tt', metavar='FILE', action='append', default=[],
        help="One of the account's .tt files (`taxjson run` passes each): "
             "a spin-off's fraction sold there by hand is its cash in "
             "lieu booked, not an UNBOOKED warning.",
    )
    parser.add_argument(
        '--ask-event', metavar='ID', default=None,
        help="Ask only about this event (`taxjson elect --redo --event "
             "ID`): no other event is asked about or elected, and the "
             "rows of an event that has no election are left out of the "
             "output (`taxjson run` asks about it).",
    )
    parser.add_argument(
        '--saved-log', metavar='FILE', default=None,
        help="Append each election this run saves (one JSON line: the "
             "record) to FILE: `taxjson elect --redo` reads it to tell "
             "its own answers from an election another command saved "
             "meanwhile when it puts the cleared ones back (backlog "
             "TJ-0955).",
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
    parser.add_argument(
        '--pending-json-shown', metavar='TEXT', default=None,
        help="How the note names --pending-json (`taxjson run` passes it "
             "relative to the project, work/<account>_pending_elections"
             ".json); default: --pending-json as given.",
    )
    args = parser.parse_args()
    if not args.base_currency:
        # Country-blind CAD used to be the default (re-audit A2-1363).
        from taxjson.lib.country import home_currency
        args.base_currency = home_currency(args.country)

    csv_paths = [Path(p) for p in args.inputs]
    missing = [p for p in csv_paths if not p.exists()]
    if missing:
        for p in missing:
            emit_line(f"taxjson-corp-actions: error: input CSV not found: {p}",
                      file=sys.stderr)
        raise SystemExit(2)
    if args.ticker_map and not Path(args.ticker_map).exists():
        # A named --ticker-map that does not exist is refused, not
        # silently skipped (re-audit A2-1436).
        emit_line(f"taxjson-corp-actions: error: no such file: --ticker-map "
                  f"{args.ticker_map}", file=sys.stderr)
        raise SystemExit(2)

    if args.manifest:
        manifest_path = Path(args.manifest)
    elif len(csv_paths) == 1:
        manifest_path = _default_manifest_path(csv_paths[0])
    else:
        # The hidden `.<csv>.elections` default would silently bind to
        # one of N files; we'd rather force an explicit choice than guess.
        emit_line(
            "taxjson-corp-actions: error: --manifest is required when multiple input files are given "
            "(the default hidden manifest is per-file and can't safely cover "
            "multiple CSVs).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    extractor = EXTRACTORS[args.brokerage_id]
    # Only override the extractor's own default account label when
    # the user explicitly passed --account-name. Mirrors the same
    # change made to taxjson-brokerage so a forgotten flag doesn't
    # clobber a meaningful default with the literal string 'default'.
    from taxjson.lib.brokerages.base import BrokerageParseError
    renames = None
    if args.ticker_map and Path(args.ticker_map).exists():
        try:
            renames = map_renames(Path(args.ticker_map))
        except (OSError, ValueError) as e:
            emit_line(f"taxjson-corp-actions: error: {args.ticker_map}: {e}",
                      file=sys.stderr)
            raise SystemExit(2)
    try:
        events = extract_events(extractor, csv_paths, args.account_name,
                                renames=renames,
                                symbol_codes=args.symbol_codes)
    except BrokerageParseError as e:
        emit_line(f"taxjson-corp-actions: error: {e}", file=sys.stderr)
        raise SystemExit(2)
    # One event per id across the account's broker accounts (overlapping
    # statements kept once; a second broker account's holding added).
    events = combine_broker_copies(events)

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
    # The manifest as the messages name it: the run's own form, never
    # the absolute path it passes (backlog TJ-0216).
    shown = args.manifest_shown or str(manifest_path)

    # Rekey elections saved under the pre-2026-07 opaque hash ids to
    # the human-legible scheme — silently, before anything consults
    # the manifest, so an id-scheme change never re-prompts for (or
    # orphans) decisions the user already made. Each save is a locked
    # load-change-save of the file as it is then (Manifest.update): an
    # election `taxjson elect --set` saved meanwhile is kept (backlog
    # TJ-0544).
    if manifest.migrate_legacy(events) and not args.decisions_only:
        manifest, _n = _update_or_die(
            manifest_path, lambda m: m.migrate_legacy(events))
    for _note in manifest.migration_notes:
        emit_line(f"note: {_note}.", file=sys.stderr)

    # Auto-defaults first: event types with exactly one sane treatment
    # (name changes) are elected without prompting — the manifest record
    # is still written, so the audit trail is intact and `taxjson elect
    # --redo` can override like any hand-made election.
    # --ask-event: that event only — another event's election is never
    # saved by a redo of one (backlog TJ-0847).
    in_scope = (events if args.ask_event is None
                else [ev for ev in events if ev.event_id == args.ask_event])
    auto_applied = apply_auto_defaults(in_scope, manifest, args.country)
    if auto_applied and not args.decisions_only:
        manifest, auto_applied = _update_or_die(
            manifest_path,
            lambda m: apply_auto_defaults(in_scope, m, args.country))
        _log_saved(args.saved_log,
                   [manifest.records[ev.event_id] for ev in auto_applied
                    if ev.event_id in manifest.records])
        for ev in auto_applied:
            emit_line(f"note: auto-elected {ev.event_id} ({ev.summary()}) — "
                      f"no decision required; `taxjson elect --redo` to "
                      f"override.", file=sys.stderr)

    missing = _unresolved(events, manifest)
    # A sheltered account's spin-off or merger: booked without asking
    # (corp_actions.SHELTERED_DEFAULT) unless --sheltered-elections ask.
    # Never written to the manifest, so a later election there wins and
    # "ask" brings the question back.
    sheltered_default = frozenset()
    if args.sheltered and args.sheltered_elections == 'zero':
        sheltered_default = frozenset(
            ev.event_id for ev in missing if sheltered_default_applies(ev))
        missing = [ev for ev in missing
                   if ev.event_id not in sheltered_default]
    # --ask-event: the other events without an election are neither asked
    # about nor booked here (backlog TJ-0847).
    not_asked = ({ev.event_id for ev in missing
                  if ev.event_id != args.ask_event}
                 if args.ask_event is not None else set())
    missing = [ev for ev in missing if ev.event_id not in not_asked]

    if missing:
        if args.no_input or not sys.stdin.isatty():
            reason = ("--no-input" if args.no_input
                      else "no terminal to answer on")
            _out.error(
                f"{len(missing)} corp-action event(s) need an election "
                f"— not asked: {reason}", prog="taxjson-corp-actions",
                details=[f"- {ev.event_id}: {ev.summary()}"
                         for ev in missing]
                + [f"Answer at a terminal (`taxjson run`), or set each "
                   f"with `taxjson elect ACCOUNT --set EVENT_ID=ELECTION`; "
                   f"the elections are saved in {shown}."])
            if args.pending_json:
                doc = _pending_doc(missing, manifest_path, args.country,
                                   sheltered=args.sheltered)
                Path(args.pending_json).parent.mkdir(parents=True,
                                                     exist_ok=True)
                write_atomic(
                    Path(args.pending_json),
                    json.dumps(doc, indent=2, sort_keys=True),
                    encoding="utf-8")
                # Named as the run names its work/ files, never by the
                # absolute path it passes (backlog TJ-0602).
                emit_line(f"taxjson-corp-actions: note: pending elections "
                          f"written to "
                          f"{args.pending_json_shown or args.pending_json}",
                          file=sys.stderr)
            raise SystemExit(EXIT_ELECTIONS_REQUIRED)

        print(
            f"\nFound {len(missing)} new corp-action event(s) needing an election.\n"
            f"Decisions will be saved to {shown} for future runs.\n",
            file=sys.stderr,
        )
        for ev in missing:
            try:
                rec = _prompt_election(ev, args.country,
                                       sheltered=args.sheltered)
            except (EOFError, KeyboardInterrupt):
                # Answers given SO FAR are already saved (per-event
                # save below) — losing six typed FMVs to a Ctrl-C at
                # event 7 defeated the whole atomic-manifest design,
                # and EOF used to print two stacked tracebacks.
                print(f"\ntaxjson-corp-actions: interrupted — "
                      f"{shown} keeps the elections answered "
                      f"so far; rerun to continue with the rest.",
                      file=sys.stderr)
                raise SystemExit(EXIT_ELECTIONS_REQUIRED)
            # Saved at once (never lose answers), under the lock.
            manifest, _ = _update_or_die(
                manifest_path, lambda m, rec=rec: m.set(rec) or True)
            _log_saved(args.saved_log, [rec])
            if args.decisions_only:
                # The save re-read the file: a run of shared exports
                # keeps its legacy-id rekeys and auto-defaults in memory
                # only (backlog TJ-1118).
                manifest.migrate_legacy(events)
                apply_auto_defaults(in_scope, manifest, args.country)
        print(f"\nManifest saved to {shown}\n", file=sys.stderr)

    fx = None
    if args.rates:
        try:
            fx = rates_converter(Path(args.rates), args.base_currency)
        except (OSError, ValueError) as e:
            emit_line(f"taxjson-corp-actions: warning: rates file "
                      f"{args.rates} unusable ({e}); cross-currency "
                      f"exchanges are booked in the consideration's "
                      f"currency.", file=sys.stderr)
    if not_asked:
        emit_line(f"note: {len(not_asked)} other event(s) without an "
                  f"election not asked (only {args.ask_event} was): "
                  f"`taxjson run` asks about them.", file=sys.stderr)
        events = [ev for ev in events if ev.event_id not in not_asked]
    try:
        out = _emit_resolved(events, manifest, args.country, fx=fx,
                             sheltered_default=sheltered_default,
                             tt_sales=read_tt_sales(args.tt or []))
    except (KeyError, ValueError) as e:
        # A saved election the rules cannot apply (an unknown election
        # key, a misspelled or missing hint): refuse by name instead of
        # a traceback — or, worse, silently $0 rows (audit S072-07).
        msg = e.args[0] if e.args else str(e)
        emit_line(f"taxjson-corp-actions: error: {msg} (manifest "
                  f"{shown})", file=sys.stderr)
        raise SystemExit(2)
    # The statements the events were read from, by their shown names:
    # `taxjson audit` names them beside a corporate-action row, as a
    # parsed row names its export (backlog TJ-0359). Not `input_files`,
    # which the export checks read as broker exports of their own.
    from taxjson.lib.brokerages.base import shown_name
    out['metadata']['statements'] = [shown_name(p) for p in csv_paths]
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == '__main__':
    main()
