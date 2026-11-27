"""`tjs elections` and the run's election messages."""
import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from taxjson.project.accounts import accounts_of, account_of
from taxjson.project.settings import settings_of
from taxjson.lib import project_layout as _PL

from taxjson.pipeline.config import _country, CORP_ACTION_BROKERS, load_config
from taxjson.pipeline.inputs import _sheltered_default_events, group_inputs
from taxjson.pipeline.messages import _details, _die_input, _PROG
from taxjson.pipeline.run import _load_pending_agg, _manifest_path_for
from taxjson.pipeline.sanity import _json_out
from taxjson.pipeline.stages import _manifest_shown, _symbol_codes_suffix
from taxjson.pipeline.tools import _cmd, run_to_file
from taxjson.lib.out import Doc

# The print() calls (PLAN step 15): the ops of a live exact Doc,
# `Doc(exact=True, width_=0, live=True)`, each printed when it is added
# (`page.line(e)` is `print(e)`); cmd_elections's one line is `out.put`. The
# JSON's print stays: its value is not provably a str.


def _symbol_codes_state(root: Path, name: str) -> Optional[Path]:
    """work/<name>_symbol_codes.state when the run wrote one (lib/
    symbol_codes): what the corp-action extraction books a Questrade
    chain under an internal code as — `tjs elections` and `--redo`
    extract exactly as the run does."""
    p = Path(root) / "work" / f"{name}{_symbol_codes_suffix()}"
    return p if p.is_file() else None


def _hint_text(hints: Dict[str, Any]) -> str:
    """`key=value` pairs of an election's hints (400.0 -> 400)."""
    def _v(v):
        if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return str(v)
    return ", ".join(f"{k}={_v(v)}" for k, v in sorted(hints.items()))


def _defaulted_events(cache: Path, name: str
                      ) -> Dict[str, Dict[str, str]]:
    """The account's events booked by the sheltered default, from its
    corp stage files in work/ ({} before a run): exactly
    `<name>_<broker>_corp.json` for the brokers whose corporate actions
    are staged — a `<name>_*` pattern also read a longer-named sibling's
    (`a_more_ib_corp.json` for account `a`, #50)."""
    return _sheltered_default_events(sorted(
        p for p in (Path(cache) / f"{name}_{b}_corp.json"
                    for b in CORP_ACTION_BROKERS) if p.is_file()))


def _default_label(kind: str) -> str:
    return ("sheltered default ($0 cost for the distributed shares)"
            if kind != "merger" else
            "sheltered default (the old shares' cost carried)")


def _elections_section(doc, name: str, manifest_path: Path, root: Path,
                       country: Optional[str] = None,
                       defaulted: Optional[Dict[str, Dict[str, str]]] = None
                       ) -> int:
    """One account's saved elections into `doc` (lib/out.Doc): a heading
    naming the manifest, then one `- ID: election` item per event with
    its summary, hints and notes aligned under it, and each event the
    run booked by the sheltered default (`defaulted`, nothing saved —
    "No elections recorded" hid them, pre-release review). Returns the
    count (0: nothing added — the caller lists the empty accounts
    together)."""
    from taxjson.lib.corp_actions import Manifest, election_keys
    man = Manifest.load(manifest_path)
    dflt = {e: v for e, v in (defaulted or {}).items()
            if e not in man.records}
    if not man.records and not dflt:
        return 0
    known = election_keys(country) if country else None
    doc.section(f"{name} — {_manifest_shown(manifest_path, root)}")
    for eid, ev in sorted(dflt.items(), key=lambda x: (x[1]["date"], x[0])):
        doc.item(f"{eid}: {_default_label(ev['kind'])}")
        doc.kv([("event", f"{ev['kind']} {ev['symbol']} on {ev['date']}"
                 .replace("spinoff", "spin-off")),
                ("change", f"`tjs elections {name} --set {eid}=<election>`"
                           f" (nothing saved: the run booked it without "
                           f"asking; no tax in the account depends on it)")],
               indent="  ")
    for eid, r in sorted(man.records.items()):
        # A hand-typed key no rule knows: `tjs run` refuses it, so
        # say so here too (audit S072-16 — it was listed as if valid).
        bad = (known is not None and r.election
               and r.election not in known)
        doc.item(f"{eid}: {r.election or '(none)'}"
                 + (f" — UNKNOWN election for {country}" if bad else ""))
        pairs = []
        if r.summary:
            pairs.append(("event", r.summary))
        if r.hints:
            pairs.append(("hints", _hint_text(r.hints)))
        if r.notes:
            pairs.append(("notes", r.notes))
        if bad:
            pairs.append(("fix", f"`tjs run` refuses it; choose again "
                                 f"with `tjs elections {name} --redo "
                                 f"--event {eid}`"))
        doc.kv(pairs, indent="  ")
    return len(man.records) + len(dflt)


def _elections_json_out(names: List[str], inputs_dir: Path,
                        cache: Path) -> None:
    """`tjs elections [ACCOUNT] --json`: {"accounts": {name: {event id:
    {election, summary, hints, notes[, saved: false]}}}} — each account's
    saved elections and the events the run booked by the sheltered
    default (nothing saved), one serializer for every view (backlog
    TJ-0400: the one-account view left the defaulted events out). An
    account with neither is left out. A manifest that cannot be read is
    the account's {"error": ...}, and the command then exits 2 (an
    input that cannot be read), as the text listing stops (backlog
    TJ-0406: it exited 0)."""
    from taxjson.lib.corp_actions import Manifest, SHELTERED_DEFAULT
    doc: Dict[str, Any] = {}
    bad: List[str] = []
    for name in names:
        mp = _manifest_path_for(inputs_dir / name, cache, name)
        dflt = _defaulted_events(cache, name)
        if not mp.exists() and not mp.is_symlink() and not dflt:
            continue
        try:
            man = Manifest.load(mp)
        except (ValueError, OSError) as e:
            doc[name] = {"error": str(e)}
            bad.append(name)
            continue
        doc[name] = {
            eid: {"election": rec.election,
                  "summary": getattr(rec, "summary", "") or "",
                  "hints": dict(rec.hints or {}),
                  "notes": rec.notes or ""}
            for eid, rec in sorted(man.records.items())}
        # Booked by the sheltered default, nothing saved.
        for eid, ev in sorted(dflt.items()):
            doc[name].setdefault(eid, {
                "election": SHELTERED_DEFAULT,
                "summary": f"{ev['kind']} {ev['symbol']} on {ev['date']}",
                "hints": {}, "notes": _default_label(ev["kind"]),
                "saved": False})
    _json_out({"accounts": doc}, schema="elections")
    if bad:
        raise SystemExit(2)


def _print_elections(names: List[str], inputs_dir: Path, cache: Path,
                     root: Path, country: Optional[str],
                     footer: str) -> None:
    """The saved-elections listing of `tjs elections [ACCOUNT]`."""
    from taxjson.lib.out import Doc
    doc = Doc("CORPORATE-ACTION ELECTIONS")
    empty = []
    for name in names:
        if not _elections_section(doc, name, _manifest_path_for(
                inputs_dir / name, cache, name), root, country,
                _defaulted_events(cache, name)):
            empty.append(name)
    if empty:
        doc.blank().para(f"No elections recorded: {', '.join(empty)}.")
    doc.blank().para(footer)
    doc.print()


def _reextract_pending_entry(acct_dir: Path, name: str, country: str,
                             manifest_path: Path,
                             event_id: str,
                             root: Optional[Path] = None
                             ) -> Optional[Dict[str, Any]]:
    """The pending-elections entry for ONE event (same shape as
    work/pending_elections.json), rebuilt from the account's broker
    CSVs in-process. That file only lists events a --no-input run
    deferred, so a re-election after `--reset --event ID` (or a --set
    typed before any run) has nothing to validate against without
    this. None when no extractor knows the id."""
    from taxjson.bin.taxjson_corp_actions import _pending_doc
    for ev in _extracted_events(acct_dir, name, root):
        if ev.event_id == event_id:
            return _pending_doc([ev], manifest_path, country)["pending"][0]
    return None


def _extracted_events(acct_dir: Path, name: str,
                      root: Optional[Path] = None):
    """Each corporate-action event in the account's broker exports,
    extracted in-process the way the run's corp-actions stage does (all
    of a broker's files as context, broker-account copies combined), so
    the ids match. An export no extractor reads is passed over; the
    extractors' chatter is swallowed: the run repeats it."""
    import contextlib
    import io as _io
    from taxjson.bin.taxjson_corp_actions import extractor_for
    from taxjson.lib.corp_extract import (extract_events, project_corp_case,
                                          project_corp_renames)
    from taxjson.lib.corp_actions import combine_broker_copies
    try:
        grouped = group_inputs(acct_dir)
    except SystemExit:
        return
    sink = _io.StringIO()
    _root = root or acct_dir.parent.parent
    # The map the run's stage read: a spin-off parent named by a broker
    # code (`GLOBAL X123456 PARENT.TO`) takes its mapped identity, so the
    # id is the run's (backlog TJ-0849).
    renames = project_corp_renames(_root)
    case = project_corp_case(_root)
    for broker, csvs in grouped.items():
        extractor = extractor_for(broker)
        if extractor is None:
            continue
        try:
            with contextlib.redirect_stderr(sink), \
                    contextlib.redirect_stdout(sink):
                events = combine_broker_copies(
                    extract_events(extractor, csvs, name, renames=renames,
                                   symbol_codes=_symbol_codes_state(
                                       _root, name), case=case),
                    stream=sink)
        except Exception:
            continue
        yield from events


def _extracted_event_ids(acct_dir: Path, name: str,
                         root: Optional[Path] = None) -> set:
    """The ids of the events in the account's exports (_extracted_events):
    what `elections --redo` can ask again about (backlog TJ-0556)."""
    return {ev.event_id for ev in _extracted_events(acct_dir, name, root)}


def _wrong_country_section(doc, wrong: List[Dict[str, str]],
                           country: str) -> None:
    """`elections --pending`: the saved elections this country's rules do not
    know, each with the command that replaces it."""
    from taxjson.lib.country import display_name
    doc.section(f"NOT {display_name(country).upper()} ELECTIONS — "
                f"`tjs run` refuses them")
    for w in wrong:
        doc.item(f"{w['account']}: {w['event_id']} (saved: "
                 f"{w['election']})")
        from taxjson.lib.out import Verbatim
        doc.kv([("event", w["summary"]),
                ("redo", Verbatim(f"tjs elections {w['account']} --redo "
                                  f"--event {w['event_id']}"))],
               indent="  ")


def _option_brief(desc: str) -> str:
    """An election option's description cut to its first clause (`elections
    --pending`'s OPTION table; --details prints the whole text)."""
    import re as _re
    head = _re.split(r"(?<!\bs)\. |: | — |; ", (desc or "").strip(), 1)[0]
    head = head.rstrip(".")
    return head if len(head) <= 52 else head[:49].rstrip() + "..."


def _cil_parts(ev: Dict[str, Any]) -> List[tuple]:
    """A pending event's `cash_in_lieu_parts` as (day, fraction, cash)
    tuples; [] when the document (work/pending_elections.json, perhaps
    an older release's) holds none it can read."""
    out: List[tuple] = []
    parts = ev.get("cash_in_lieu_parts") or ()
    for part in parts if isinstance(parts, list) else ():
        if not (isinstance(part, list) and len(part) == 3
                and all(isinstance(x, (int, float))
                        and not isinstance(x, bool) for x in part[1:])):
            return []
        out.append((str(part[0] or ""), part[1], part[2]))
    return out


def _print_pending(root: Path, inputs_dir: Path, cache: Path,
                   agg: Dict[str, Any], wrong: List[Dict[str, str]],
                   country: str, details: bool = False) -> None:
    """`tjs elections --pending`: each deferred event with its options —
    a table of the options (first clause of what each books, the hint it
    needs) and one `--set` line to copy; with `details`, each option's
    whole description and its own ready `--set` line — and a last line
    saying what to do (the checklist's elections step shows that
    line)."""
    from taxjson.lib.corp_actions import Manifest
    from taxjson.lib.out import Doc, Verbatim
    events = [(acct, ev) for acct, adoc in
              sorted((agg.get("accounts") or {}).items())
              for ev in (adoc.get("pending") or [])]
    n = len(events)
    doc = Doc(f"PENDING ELECTIONS — {n} event{'s' if n != 1 else ''}"
              if events else None)
    if wrong:
        _wrong_country_section(doc, wrong, country)
    open_n = 0
    for acct, ev in events:
        eid = ev.get("event_id", "")
        man = Manifest.load(_manifest_path_for(inputs_dir / acct, cache,
                                               acct))
        rec = man.get(eid)
        doc.section(f"{acct}: {eid}")
        if ev.get("summary"):
            doc.para(ev["summary"], indent="  ")
        if ev.get("action_type") == "spinoff":
            # The fraction the holding was entitled to and the cash paid
            # for it: the row's ratio counts the delivered shares only
            # (backlog TJ-0166).
            from taxjson.lib.corp_views import cash_in_lieu_line
            _cil = cash_in_lieu_line(
                float(ev.get("qty_received") or 0.0),
                float(ev.get("fraction_in_lieu") or 0.0),
                float(ev.get("cash_in_lieu") or 0.0),
                str(ev.get("cash_in_lieu_currency") or ev.get("currency")
                    or ""), str(ev.get("cash_in_lieu_date") or ""),
                parts=_cil_parts(ev))
            if _cil:
                doc.para(f"cash in lieu: {_cil}", indent="  ")
        if ev.get("sheltered"):
            from taxjson.bin.taxjson_corp_actions import sheltered_note
            _note = sheltered_note(country)
            doc.para(_note[0].upper() + _note[1:] + ".", indent="  ")
        if rec is not None:
            # The pending file only clears on the next successful run —
            # without this marker, "did my --set take?" looked like NO
            # and users re-set (or overwrote) their election.
            doc.para(f"already elected: {rec.election} — re-run "
                     f"`tjs run` to apply", indent="  ")
        else:
            open_n += 1
        if not details:
            opts = ev.get("options", [])
            doc.table(["OPTION", "WHAT IT BOOKS", "HINT"],
                      [[o["election"], _option_brief(o.get("description")),
                        ", ".join(h["key"] for h in (o.get("hints") or []))]
                       for o in opts], indent="  ", drop=(2,))
            _hinted = any(o.get("hints") for o in opts)
            doc.kv([("set", Verbatim(
                f"tjs elections {acct} --set {eid}=OPTION"
                + (" [--hint KEY=VALUE]" if _hinted else "")))],
                indent="  ")
            continue
        for i, o in enumerate(ev.get("options", []), 1):
            hints = o.get("hints") or []
            doc.blank().line(f"  {i}. {o['election']}")
            if o.get("description"):
                doc.para(o["description"], indent="     ")
            pairs = [("hint", f"{h['key']} — "
                              f"{h.get('prompt') or 'a number'}")
                     for h in hints]
            hint_args = "".join(f" --hint {h['key']}=..." for h in hints)
            pairs.append(("set", Verbatim(
                f"tjs elections {acct} --set {eid}={o['election']}"
                f"{hint_args}")))
            doc.kv(pairs, indent="     ")
    doc.blank()
    if open_n and not details:
        doc.line("What each option books, and its hints: "
                 "tjs elections --pending --details")
    if open_n:
        # ONE short line: `tjs checklist` shows it as the step's
        # detail.
        doc.line(f"{open_n} election{'s' if open_n != 1 else ''} pending: "
                 f"set each with `tjs elections ACCOUNT --set`, then "
                 f"`tjs run`.")
    elif events:
        doc.line("Every pending event has an election saved: run "
                 "`tjs run` to apply them.")
    else:
        doc.line("Replace each of them with `tjs elections ACCOUNT --redo "
                 "--event ID`, then run `tjs run`.")
    doc.print()


def _set_record(event_id: str, summary: str, election: str,
                hints: Dict[str, Any], pending_entry: Optional[dict]):
    """The election `tjs elections --set` saves, with the id the export's
    spelling gave the event when its pending entry has one (a symbol
    hashed into the id spelled with a lower-case letter): a ticker.map
    DISTINCT line that later keeps that spelling apart sends the
    election to the event read in it (backlog TJ-4616)."""
    from taxjson.lib.corp_actions import ElectionRecord
    read = (pending_entry or {}).get("read_event_id")
    return ElectionRecord(event_id=event_id, summary=summary,
                          election=election, notes="set via elections --set",
                          hints=hints,
                          read_event_id=read if isinstance(read, str) else "")


def _redo_saved_records(log: Path) -> Dict[str, List[Dict[str, Any]]]:
    """{event id: [record as a dict, ...]} of the elections an
    interrupted `elections --redo`'s child saved (its --saved-log; backlog
    TJ-0955); empty when it saved none or the log cannot be read."""
    import json as _json
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        lines = Path(log).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            rec = _json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("event_id"), str):
            out.setdefault(rec["event_id"], []).append(rec)
    return out


def _update_manifest(manifest_path: Path, change):
    """Change the elections manifest as one locked step
    (corp_actions.Manifest.update: load, `change(manifest)`, save when
    it returns a true value; backlog TJ-0544), or one line naming it
    when its folder cannot be written (a read-only inputs/<acct>/): it
    was a PermissionError traceback on the .tmp file (re-audit
    A2-1418). Saved through lib/elections_save `update`: a
    requires_taxjson the save raised is one note each. Returns (the
    manifest, the change's value)."""
    from taxjson.lib import elections_save
    from taxjson.lib.corp_actions import ManifestError
    from taxjson.lib.requires import raised_note
    from taxjson.lib.safe_write import LockLinkError
    from taxjson.pipeline.messages import _say
    try:
        man, result, raised = elections_save.update(manifest_path, change)
    except (LockLinkError, ManifestError) as e:
        _die_input(f"{e} — nothing was saved.")
    except OSError as e:
        _die_input(f"cannot write {manifest_path}: {e.strerror or e} — "
                   f"nothing was saved; make its folder writable and "
                   f"re-run.")
    for line in raised_note(raised, Path(manifest_path).parent.parent.parent):
        _say("note", line, prog=f"{_PROG} elections")
    return man, result


def _foreign_pending_options(entry: Dict[str, Any], country: str
                             ) -> List[str]:
    """The options of a pending-elections entry that `country`'s rules
    do not know (lib/corp_actions.election_keys): an entry a run wrote
    before the project's country changed (backlog TJ-0540)."""
    from taxjson.lib.corp_actions import election_keys
    known = election_keys(country)
    return sorted({str(o.get("election")) for o in entry.get("options") or []
                   if o.get("election") not in known})


def _elect_pending_view(args: argparse.Namespace, fail: Callable[..., Any],
                        accounts: Dict[str, Any], root: Path, cfg: Dict[str, Any],
                        cache: Path, country: str, _out: Any, inputs_dir: Path) -> None:
    """`tjs elections --pending`: the flags it does not take refused, the
    account checked, the saved elections of another country's rules and
    the pending document read, then its JSON or text listing
    (cmd_elections)."""
    import json as _json
    from taxjson.lib.json_contract import versioned
    # --pending lists; a --set beside it was never read, and the
    # command said nothing and exited 0 (backlog TJ-0539).
    _also = [f for f, on in (
        ("--set", getattr(args, "set", None)),
        ("--hint", getattr(args, "hint", None)),
        ("--redo", args.redo), ("--reset", args.reset),
        ("--event", args.event is not None)) if on]
    if _also:
        fail(f"--pending lists the pending elections: it does not "
             f"take {', '.join(_also)}",
             "Save an election without it: `tjs elections ACCOUNT "
             "--set EVENT_ID=ELECTION`. Nothing was saved.")
    # `elections ACCOUNT --pending`: that account's only (it listed
    # every account's; backlog TJ-0539).
    scope = args.account
    if scope and scope not in accounts:
        fail(f"no [accounts.{scope}] in taxjson.toml",
             f"Accounts: {', '.join(accounts) or '(none)'}.")
    # A saved election the project's country does not know (a
    # Canadian rollover left in a project switched to usa) stops
    # `tjs run`; it is pending too (re-audit A2-0725).
    from taxjson.lib.corp_views import (ViewError,
                                        wrong_country_elections)
    try:
        _wrong = wrong_country_elections(root, cfg)
    except ViewError as e:
        fail(str(e))
    if scope:
        _wrong = [w for w in _wrong if w.get("account") == scope]
    agg_path = cache / "pending_elections.json"
    if agg_path.exists():
        try:
            doc = _load_pending_agg(agg_path)
        except ValueError as e:
            # One line, exit 2 — not a traceback (re-audit A2-0471,
            # A2-0779, A2-1419, A2-1460). It is a work file `run`
            # rebuilds.
            fail(str(e), "A damaged work file: re-run `tjs run "
                 "--no-input` to rebuild it.")
        # The file's own format number (`tjs run` writes 1; no reader
        # checks it) is not the output's: `--json` carries the
        # registry's (lib/json_contract, `elections --pending`), whatever
        # the file holds.
        doc.pop("schema_version", None)
        if scope:
            doc["accounts"] = {a: d for a, d in doc["accounts"].items()
                               if a == scope}
    else:
        # Same shape as the pending document itself, so a machine
        # consumer never has to parse prose.
        doc = {"accounts": {}}
    if getattr(args, "json", False):
        if _wrong:
            doc = dict(doc, wrong_country=_wrong)
        print(_json.dumps(versioned(doc, "elections --pending"), indent=2,
                          sort_keys=True))
        return
    page = Doc(exact=True, width_=0, live=True)
    if not _wrong and not any(d.get("pending")
                              for d in doc["accounts"].values()):
        page.line(f"No pending elections{f' for {scope}' if scope else ''}"
                  f" (no --no-input run has deferred any, or they've been "
                  f"resolved).")
        return
    _stale = sorted(a for a, d in doc["accounts"].items()
                    if any(_foreign_pending_options(ev, country)
                           for ev in d.get("pending") or []))
    if _stale:
        # `elections --set` refuses their options (backlog TJ-0540).
        from taxjson.lib.country import display_name
        _out.warn(f"work/pending_elections.json lists "
                  f"{', '.join(_stale)}'s events with another "
                  f"country's elections",
                  prog=f"{_PROG} elections",
                  details=[f"It was written before this project's "
                           f"country became {display_name(country)}: "
                           f"re-run `tjs run --no-input` to "
                           f"rebuild it."])
    _print_pending(root, inputs_dir, cache, doc, _wrong, country,
                   _details(args))
    return


def _elect_all_view(args: argparse.Namespace, fail: Callable[..., Any],
                    accounts: Dict[str, Any], inputs_dir: Path, cache: Path, root: Path,
                    country: str) -> None:
    """`tjs elections` with no account: --redo, --reset, --set and --hint
    refused, then every account's saved elections, as JSON or text
    (cmd_elections)."""
    if args.redo or args.reset:
        fail("--redo/--reset need an account",
             "For example: `tjs elections margin --redo`.")
    if getattr(args, "set", None) or getattr(args, "hint", None):
        # Falling through to the read-only listing here looked like
        # success (exit 0) while saving NOTHING (REVIEW #7).
        fail("--set/--hint need an account",
             "For example: `tjs elections margin --set "
             "EVENT_ID=ELECTION`. Nothing was saved.")
    if getattr(args, "json", False):
        # Machine listing of SAVED elections (--pending --json
        # already covers the unresolved ones) — previously --json
        # here silently printed the text listing.
        _elections_json_out(list(accounts), inputs_dir, cache)
        return
    _print_elections(list(accounts), inputs_dir, cache, root, country,
                     "Redo one: `tjs elections ACCOUNT --redo` (add "
                     "`--event ID` for just one event).")
    return


def _elect_one_set(args: argparse.Namespace, fail: Callable[..., Any], name: str) -> None:
    """One --set per command: two refused, the one kept as a string
    (cmd_elections)."""
    if isinstance(getattr(args, "set", None), list):
        # A second --set used to replace the first silently (argparse
        # keeps the last) and the run still asked for the dropped event
        # (re-audit A2-0563 / A2-0568). --hint belongs to ONE election,
        # so one --set per command.
        if len(args.set) > 1:
            fail(f"{len(args.set)} --set flags — give ONE --set per "
                 f"command",
                 f"Its --hint flags belong to it: run `tjs elections "
                 f"{name} --set ...` once per event. Nothing was saved.")
        args.set = args.set[0]


def _elect_set_event(args: argparse.Namespace, country: str, fail: Callable[..., Any],
                     cache: Path, name: str, acct_dir: Path, manifest_path: Path, root: Path
                     ) -> Tuple[str, str, Any, Any, Dict[str, List[Any]], Any]:
    """--set's event and election: EVENT_ID=ELECTION read, the event's
    entry found in the pending document (one written under another
    country's rules set aside) or re-extracted from the exports, the
    election checked against the event's options or the country's.
    Returns the id, the election, the event's options, summary and hints
    by option, and its pending entry (cmd_elections)."""
    from taxjson.lib.corp_actions import election_keys
    if "=" not in args.set:
        # The example names a key this country accepts (a US
        # project refuses the Canadian s.85.1 key; audit A2-0718).
        _ex = "reorg_368" if country == "usa" else "rollover_s_85_1_5"
        fail("--set expects EVENT_ID=ELECTION",
             f"For example `--set 20250317-abg-abh-1a2b={_ex}`; "
             f"`tjs elections --pending` lists each event's "
             f"choices.")
    event_id, election = args.set.split("=", 1)
    event_id, election = event_id.strip(), election.strip()
    if not event_id:
        # A ""-keyed record could never be targeted by --event
        # afterwards (REVIEW #34).
        fail("--set: empty EVENT_ID (expected EVENT_ID=ELECTION)",
             "`tjs elections --pending` lists the ids.")
    # PER-EVENT validation on every --set: the country-wide set
    # accepted e.g. a spinoff election for a merger, which only
    # exploded at the NEXT run as a raw KeyError traceback (REVIEW
    # #6). The pending doc (written by the exit-3 run that told the
    # user this id) is the cheap source; when it doesn't list the
    # event — the documented `--reset --event ID` then `--set`
    # re-election, or a --set before any run — the event is
    # re-extracted from the account's CSVs instead.
    event_options = None
    event_summary = None
    event_hints_by_option: Dict[str, list] = {}
    pending_entry = None
    stale_entry = None
    agg_path = cache / "pending_elections.json"
    if agg_path.exists():
        try:
            _pdoc = _load_pending_agg(agg_path)
            for _ev in (((_pdoc.get("accounts") or {}).get(name)
                         or {}).get("pending") or []):
                if _ev.get("event_id") == event_id:
                    pending_entry = _ev
        except ValueError:
            pass            # damaged: re-extracted below (A2-0779)
    if pending_entry is not None \
            and _foreign_pending_options(pending_entry, country):
        # Written by a run before the project's country changed: its
        # options are the other country's. They were taken as this
        # event's, and a Canadian rollover was saved into a US
        # project at exit 0 (backlog TJ-0540). The event is
        # re-extracted with this country's rules instead.
        stale_entry, pending_entry = pending_entry, None
    if pending_entry is None:
        pending_entry = _reextract_pending_entry(
            acct_dir, name, country, manifest_path, event_id, root)
    if pending_entry is None and stale_entry is not None:
        from taxjson.lib.country import display_name
        fail(f"work/pending_elections.json lists {event_id} with "
             f"another country's elections",
             f"It was written before this project's country became "
             f"{display_name(country)}. Re-run `tjs run "
             f"--no-input` to rebuild it: `tjs elections --pending` "
             f"then lists the event's {display_name(country)} "
             f"elections. Nothing was saved.")
    if pending_entry is not None:
        event_options = {"ignore"} | {
            o.get("election")
            for o in pending_entry.get("options", [])}
        event_summary = pending_entry.get("summary")
        for o in pending_entry.get("options", []):
            event_hints_by_option[o.get("election")] = (
                o.get("hints") or [])
    if event_options is not None:
        # This country's options only: an entry offering another
        # country's was set aside above (backlog TJ-0540).
        if election not in event_options:
            fail(f"election {election!r} is not valid for event "
                 f"{event_id}",
                 f"This event offers: "
                 f"{', '.join(sorted(event_options))}.")
    else:
        known = election_keys(country)
        if election not in known:
            fail(f"unknown election {election!r} for "
                 f"country={country}",
                 f"Valid: {', '.join(sorted(known))}.")
    return event_id, election, event_options, event_summary, event_hints_by_option, pending_entry


def _elect_set_save(args: argparse.Namespace, fail: Callable[..., Any], event_options: Any,
                    event_hints_by_option: Dict[str, List[Any]], election: str, Manifest: Any,
                    manifest_path: Path, event_id: str, name: str, event_summary: Any,
                    pending_entry: Any, _out: Any, root: Path) -> None:
    """--set's hints and save: each --hint read as a number and checked,
    the event's declared hints required and no other taken, an id that
    matches nothing refused; then the election saved under the
    manifest's lock and said, with a warning for a value of 0
    (cmd_elections)."""
    hints = {}
    for h in (args.hint or []):
        if "=" not in h:
            fail(f"--hint expects KEY=VALUE, got {h!r}",
                 "Nothing was saved.")
        k, v = h.split("=", 1)
        try:
            hints[k.strip()] = float(v)
            from taxjson.lib.corp_actions import hint_value_problem
            _prob = hint_value_problem(k.strip(), hints[k.strip()])
            if _prob:
                # A negative allocated ACB created basis from
                # nothing and a negative FMV booked negative
                # dividend income, saved at exit 0; nan/inf failed
                # only on the next run (S039-00).
                fail(f"--hint: {_prob}", "Nothing was saved.")
        except ValueError:
            # Every hint consumer float()s its value — storing the
            # raw string reported "Election saved" and then crashed
            # the NEXT run with a ValueError traceback (REVIEW #5,
            # classic trigger: European decimal comma).
            fail(f"--hint: {k.strip()}={v.strip()!r} is not a number",
                 f"Use a decimal POINT, e.g. `{k.strip()}=12.50`. "
                 f"Nothing was saved.")
    if event_options is not None:
        # The pending doc declares exactly which hints this
        # election needs — a rollover saved WITHOUT its
        # allocated_acb used to print "Election saved" and later
        # emit a $0-basis position with no warning; a typo'd hint
        # key was stored and ignored by every consumer.
        declared = {h.get("key")
                    for h in event_hints_by_option.get(election, [])}
        missing = declared - set(hints)
        unknown = set(hints) - declared
        if missing:
            specs = " ".join(f"--hint {k}=<value>"
                             for k in sorted(missing))
            # What each hint is, said here: `elections --pending` lists
            # only events a --no-input run deferred, so for an event
            # already elected (a re-election) it said "No pending
            # elections" (backlog TJ-0037).
            prompts = {h.get("key"): h.get("prompt")
                       for h in event_hints_by_option.get(election, [])}
            fail(f"{election} needs {', '.join(sorted(missing))}",
                 *[f"{k}: {prompts[k]}" for k in sorted(missing)
                   if prompts.get(k)],
                 f"Add `{specs}`. Nothing was saved.")
        if unknown:
            fail(f"{election} does not use hint(s) "
                 f"{', '.join(sorted(unknown))}",
                 (f"It takes: {', '.join(sorted(declared))}."
                  if declared else "It takes none.")
                 + " Nothing was saved.")
    if Manifest.load(manifest_path).get(event_id) is None \
            and event_options is None:
        # Saving anyway left a junk record in the committed
        # elections.json that no run ever reads (2026-09 CLI audit).
        fail(f"{event_id!r} matches no pending event — nothing was "
             f"saved",
             f"It is no saved election either, and no corporate "
             f"action in {name}'s inputs. Check the id with `tjs "
             f"elections --pending` (after a `tjs run --no-input`) or "
             f"`tjs elections {name}`.")

    def _set(man) -> bool:
        prior = man.get(event_id)
        # Best summary available: pending doc (the happy path —
        # user copied the id from `elections --pending`), else the
        # prior record's.
        summary = (event_summary
                   or (prior.summary if prior else None)
                   or "(set non-interactively)")
        man.set(_set_record(event_id, summary, election, hints,
                            pending_entry))
        return True
    # The load, the change and the save under the manifest's lock:
    # another writer's election is never dropped (TJ-0544).
    _update_manifest(manifest_path, _set)
    _done = _out.Doc(f"Election saved: {event_id} = {election}")
    _done.kv(([("hints", _hint_text(hints))] if hints else [])
             + [("file", _out.Verbatim(_manifest_shown(manifest_path,
                                                       root)))],
             indent="  ")
    _done.line("Run `tjs run` to apply it.")
    _done.print()
    from taxjson.lib.corp_actions import declares_zero_value
    if declares_zero_value(election, hints):
        # A spin-off's 0 is the user's declared value (a warrant
        # distributed at no value): an Info on every run, never a
        # Warning or pending.
        _out.note(f"fmv_per_share=0 books this {election} at the $0 "
                  f"value you declared",
                  prog=f"{_PROG} elections",
                  details=["No income and a $0 cost for the new "
                           "shares; `tjs run` lists it as an "
                           "Info. To change it: `tjs elections "
                           f"{name} --redo --event {event_id}`."])
    elif ("fmv_per_share" in hints
            and abs(hints["fmv_per_share"]) < 1e-12
            and election.startswith("taxable_")):
        # A merger's 0 is the documented "defer" value (R1-11): say
        # what it books.
        _out.warn(f"fmv_per_share=0 books this {election} at $0",
                  prog=f"{_PROG} elections",
                  details=["No income and a $0 cost for the new "
                           "shares. Every `tjs run` and the "
                           "checklist flag it until a value is "
                           "set."])
    from taxjson.lib.corp_actions import ALLOCATED_BASIS_HINT
    _ak = ALLOCATED_BASIS_HINT.get(election)
    if _ak and _ak in hints and abs(hints[_ak]) < 0.005:
        # The missing hint is refused; an explicit 0 was saved with
        # no word (audits S073-21, S074-04).
        _out.warn(f"{_ak}=0 moves NO cost to the spun-off shares",
                  prog=f"{_PROG} elections",
                  details=["They book at $0 and the parent keeps all "
                           "of it. Enter the allocated amount; every "
                           "`tjs run` and the checklist flag it "
                           "until then."])


def _elect_saved(args: argparse.Namespace, fail: Callable[..., Any], name: str, Manifest: Any,
                 manifest_path: Path) -> Any:
    """--redo/--reset's start: --redo refused away from a terminal
    (nothing cleared), then the saved elections read (cmd_elections)."""
    if args.redo and not sys.stdin.isatty():
        # The old path cleared FIRST and then crashed on the missing
        # TTY — the user's saved (non-rebuildable) elections were
        # already gone (REVIEW #4).
        fail("--redo asks again, so it needs a terminal",
             f"Headless: `tjs elections {name} --reset [--event ID]` to "
             f"clear, then `tjs elections {name} --set EVENT_ID=ELECTION` "
             f"(ids and options: `tjs elections --pending` after a "
             f"`tjs run --no-input`). Nothing was cleared.")
    # `is not None`: --event '' (e.g. an unset shell variable) must NOT
    # silently widen to ALL elections (REVIEW #34 wiped everything).
    saved = Manifest.load(manifest_path)
    return saved


def _elect_clear(target: Optional[str], askable: Any, current_id: Callable[[str], str],
                 manifest_path: Path, root: Path, _out: Any, name: str) -> Dict[str, Any]:
    """--redo/--reset's clear: the chosen elections removed under the
    manifest's lock (one of an event --redo cannot ask about again
    kept, and named), the count said. Returns the records cleared, kept
    to put back when --redo is interrupted (cmd_elections)."""
    cleared: Dict[str, Any] = {}
    kept: List[str] = []

    def _clear(man) -> bool:
        targets = ([target] if target is not None
                   else list(man.records))
        for eid in targets:
            if eid not in man.records:
                continue
            if askable is not None and current_id(eid) not in askable:
                # An event no export has any more: --redo could not ask
                # again, so its election stays (backlog TJ-0556).
                kept.append(eid)
                continue
            cleared[eid] = man.records.pop(eid)
        return True
    # Under the manifest's lock: an election another command saves
    # meanwhile is kept (backlog TJ-0544).
    _update_manifest(manifest_path, _clear)
    page = Doc(exact=True, width_=0, live=True)
    page.line(f"Cleared {len(cleared)} election(s) from "
              f"{_manifest_shown(manifest_path, root)}.")
    if kept:
        _out.note(f"kept {len(kept)} election(s) of events in none of "
                  f"{name}'s exports: {', '.join(sorted(kept))}",
                  prog=f"{_PROG} elections",
                  details=[f"--redo cannot ask again about them. To "
                           f"clear one: `tjs elections {name} --reset "
                           f"--event ID`."])
    return cleared


def _elect_redo(cache: Path, cfg: Dict[str, Any], root: Path, name: str,
                current_id: Callable[[str], str], target: Optional[str],
                grouped: Dict[str, List[Path]], country: str, manifest_path: Path,
                cleared: Dict[str, Any], fail: Callable[..., Any]) -> None:
    """--redo's prompt: the corporate-action tool run again at the
    terminal for each corporate-action export, with the run's valuation
    inputs and, for --event, that event alone (its output to a scratch
    folder); the elections it saves logged; an interruption or a failure
    puts the cleared elections back (one another command saved meanwhile
    kept). The scratch folder and the log's folder are made, used and
    removed here (cmd_elections's tail)."""
    # --redo: re-prompt now by re-running corp-actions interactively.
    # Any failure or Ctrl-C mid-prompt RESTORES the pre-clear manifest —
    # elections are the one non-rebuildable user artifact (REVIEW #4).
    # The same valuation inputs `tjs run` passes (event-date rates,
    # base currency, ticker.map): without them a cross-currency merger's
    # new shares were booked in the wrong currency, and a following
    # `run --fast` kept that corp file (A2-0981, A2-0982).
    _redo_corp_flags: List[str] = []
    _rates = cache / "to_base.csv"
    if _rates.exists():
        _redo_corp_flags += ["--rates", str(_rates)]
    _base_cur = settings_of(cfg).base_currency
    if _base_cur:
        _redo_corp_flags += ["--base-currency", str(_base_cur)]
    # The map `elections` extracted the ids with (project_corp_map: the
    # run's effective map when it wrote one; backlog TJ-0849).
    from taxjson.lib.corp_extract import project_corp_map
    _tmap = project_corp_map(root)
    if _tmap is not None:
        _redo_corp_flags += ["--ticker-map", str(_tmap)]
    _acfg = account_of(accounts_of(cfg), name)
    # --redo asks again even in a sheltered account (the default would
    # book the event without asking, CA-CORP-11 / US-CORP-12).
    _redo_sheltered = ([] if _acfg.taxable
                       else ["--sheltered", "--sheltered-elections", "ask"])
    # --event: that event only. The child asks about it alone — it asked
    # about every event without an election and saved each answer, and
    # an interrupted redo kept them (backlog TJ-0847) — and its output,
    # which leaves out the events it did not ask about, is not the corp
    # file `tjs run` reads: it goes to a scratch folder, removed.
    _ask = (["--ask-event", current_id(target)] if target is not None
            else [])
    import tempfile as _tempfile
    _scratch = (_tempfile.TemporaryDirectory(prefix="taxjson-redo-")
                if _ask else None)
    # The elections the child saves, one JSON line each: the restore
    # tells them from one another command saved meanwhile (TJ-0955).
    _logdir = _tempfile.TemporaryDirectory(prefix="taxjson-redo-log-")
    _saved_log = Path(_logdir.name) / "saved.jsonl"
    ran = False
    try:
        for broker, csvs in grouped.items():
            if broker not in CORP_ACTION_BROKERS:
                continue
            out = (Path(_scratch.name) if _scratch is not None
                   else cache) / f"{name}_{broker}_corp.json"
            _sc = _symbol_codes_state(root, name)
            cmd = _cmd("taxjson-corp-actions") + [
                "--account-name", name, "--country", country,
                "--brokerage", broker, "--elections", str(manifest_path),
                "--elections-shown", _manifest_shown(manifest_path, root),
                "--saved-log", str(_saved_log),
            ] + _redo_corp_flags + _redo_sheltered + _ask + (
                ["--symbol-codes", str(_sc)]
                if _sc and broker == "questrade" else []) + [
                str(p) for p in csvs]
            run_to_file(cmd, out, interactive=True)
            ran = True
    except (subprocess.CalledProcessError, KeyboardInterrupt):
        ours = _redo_saved_records(_saved_log)
        conflicts: List[str] = []

        def _restore(man) -> bool:
            # The cleared elections back, over whatever the interrupted
            # prompt saved for them; another event's election saved
            # meanwhile stays (backlog TJ-0544). One saved under an older
            # id: the answer saved under its current id goes (TJ-0848).
            # An election of the same event another command saved after
            # the clear (`elections --set`, a run's prompt) is newer than
            # the cleared one: it stays (backlog TJ-0955).
            from dataclasses import asdict
            for eid, rec in cleared.items():
                ids = {eid, current_id(eid)}
                if any(i in man.records
                       and asdict(man.records[i]) not in ours.get(i, [])
                       for i in ids):
                    conflicts.append(current_id(eid))
                    continue
                for i in ids:
                    man.records.pop(i, None)
                man.set(rec)
            return bool(cleared)
        _update_manifest(manifest_path, _restore)
        if conflicts:
            fail("--redo interrupted",
                 f"The previous elections for '{name}' were restored, "
                 f"except {', '.join(sorted(conflicts))}: another command "
                 f"saved an election for it after the redo cleared it, "
                 f"and that newer one was kept (`tjs elections {name}` "
                 f"shows it).", code=1)
        fail("--redo interrupted",
             f"The previous elections for '{name}' were restored "
             f"unchanged.", code=1)
    finally:
        if _scratch is not None:
            _scratch.cleanup()
        _logdir.cleanup()
    page = Doc(exact=True, width_=0, live=True)
    if not ran:
        page.line(f"No corp-action events to re-elect for '{name}'.")
    else:
        page.line("\nElections saved. Run `tjs run` to recompute.")


def cmd_elections(args: argparse.Namespace) -> None:
    """View, clear, or redo corporate-action tax elections (taxable vs
    rollover, FMV/ACB hints) that `tjs run` otherwise prompts for once
    and then reuses silently."""
    from taxjson.lib.corp_actions import Manifest
    from taxjson.lib import out as _out

    def fail(headline: str, *details: str, code: int = 2):
        # One headline naming what was refused, the why and the fix as
        # indented detail lines (docs/output-style.md). Exit 2: what
        # elections refuses is a usage error (a flag, an account, an event
        # or an election it does not take) or an input it cannot read;
        # it exited 1, as a finding does (backlog TJ-0539).
        _out.fail(headline, prog=f"{_PROG} elections", details=details,
                  code=code)

    root = Path(args.dir).resolve()
    cfg = load_config(root)
    accounts = cfg.get("accounts", {})
    country = _country(settings_of(cfg))
    cache = root / "work"
    inputs_dir = _PL.inputs_dir(root)

    if getattr(args, "pending", False):
        _elect_pending_view(args, fail, accounts, root, cfg, cache, country, _out, inputs_dir)
        return
    if args.event is not None and not (args.redo or args.reset):
        # --event scopes --redo/--reset; alone it was ignored and the
        # listing printed (backlog TJ-0539).
        fail("--event applies to --redo/--reset only",
             f"For example: `tjs elections "
             f"{args.account or 'ACCOUNT'} --reset --event ID`. Nothing "
             f"was changed.")

    # No account → list every account's elections (read-only).
    if not args.account:
        _elect_all_view(args, fail, accounts, inputs_dir, cache, root, country)
        return

    name = args.account
    if name not in accounts:
        fail(f"no [accounts.{name}] in taxjson.toml",
             f"Accounts: {', '.join(accounts) or '(none)'}.")
    acct_dir = inputs_dir / name
    manifest_path = _manifest_path_for(acct_dir, cache, name)
    if getattr(args, "hint", None) and not getattr(args, "set", None):
        fail("--hint only applies with --set EVENT_ID=ELECTION",
             "Nothing was saved.")
    if getattr(args, "json", False):
        if args.redo or args.reset or getattr(args, "set", None):
            fail("--json applies to the listings only, not to "
                 "--set/--redo/--reset",
                 "Listings: `tjs elections [ACCOUNT] --json`, `tjs "
                 "elections --pending --json`.")
        # One account's elections — the all-accounts listing's entry
        # for it (it printed the text listing before; it left out the
        # events the sheltered default booked: backlog TJ-0400).
        _elections_json_out([name], inputs_dir, cache)
        return

    # --set: non-interactive election writing (headless/CI bootstrap).
    _elect_one_set(args, fail, name)
    if getattr(args, "set", None):
        (event_id, election, event_options, event_summary, event_hints_by_option,
         pending_entry) = _elect_set_event(args, country, fail, cache, name, acct_dir,
                                           manifest_path, root)
        _elect_set_save(args, fail, event_options, event_hints_by_option, election, Manifest,
                        manifest_path, event_id, name, event_summary, pending_entry, _out, root)
        return

    if not (args.redo or args.reset):
        _print_elections([name], inputs_dir, cache, root, country,
                         f"Redo all: `tjs elections {name} --redo`; one "
                         f"event: add `--event ID`; just clear: "
                         f"`--reset`.")
        return

    # --redo / --reset: clear the chosen elections from the manifest.
    saved = _elect_saved(args, fail, name, Manifest, manifest_path)
    # {saved id: current id} of the records saved under an older id that
    # the run applies to an event of the exports (Manifest.migration_
    # aliases): a run on shared exports does not rewrite the manifest, so
    # such a record keeps its old id, and --event takes either (backlog
    # TJ-0848). Read only when an id needs it: it extracts the events.
    _aliases: List[Dict[str, str]] = []

    def aliases() -> Dict[str, str]:
        if not _aliases:
            _aliases.append(saved.migration_aliases(
                list(_extracted_events(acct_dir, name, root))))
        return _aliases[0]
    target = args.event          # the saved record's id
    if args.event is not None and args.event not in saved.records:
        _olds = sorted(o for o, n in aliases().items() if n == args.event)
        if len(_olds) != 1:
            fail(f"no election '{args.event}' for account '{name}'",
                 f"`tjs elections {name}` lists the event ids.")
        target = _olds[0]
    askable = None

    def current_id(eid: str) -> str:
        """The id the exports give the event of saved record `eid`."""
        if eid == target and target != args.event:
            return args.event
        if askable is None or eid in askable:
            return eid
        return aliases().get(eid, eid)
    if args.redo:
        # What --redo can ask again about, found BEFORE anything is
        # cleared: it cleared first, then found no export to ask from
        # ("No corp-action events to re-elect") and exited 0 with the
        # election gone (backlog TJ-0556).
        grouped = group_inputs(acct_dir)
        if not any(b in CORP_ACTION_BROKERS for b in grouped):
            fail(f"--redo: {name} has no corporate-action export to ask "
                 f"again from",
                 f"Its saved elections were kept: nothing was cleared. "
                 f"To clear one anyway (the next `tjs run` asks only "
                 f"if its event is back in the exports): `tjs elections "
                 f"{name} --reset --event ID`.")
        askable = _extracted_event_ids(acct_dir, name, root)
        if args.event is not None and current_id(target) not in askable:
            fail(f"--redo: event {args.event} is in none of {name}'s "
                 f"exports",
                 f"There is nothing to ask again about: its saved "
                 f"election was kept, nothing was cleared. To clear it "
                 f"anyway: `tjs elections {name} --reset --event "
                 f"{args.event}`.")
        if any(e not in askable for e in saved.records):
            aliases()           # read before the lock is taken
    # The records cleared, kept to put back when --redo is interrupted.
    cleared = _elect_clear(target, askable, current_id, manifest_path, root, _out, name)

    if args.reset:
        _out.put(f"Next `tjs run` will re-prompt for them.")
        return

    _elect_redo(cache, cfg, root, name, current_id, target, grouped, country, manifest_path,
                cleared, fail)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's command, for the `tjs` parser."""
    p_elect = sub.add_parser(
        "elections", help="Review or set corporate-action tax elections",
        description="List the corporate-action events in the books "
                    "(mergers, spin-offs, reorganisations) with the tax "
                    "election recorded for each; redo or reset one, show "
                    "what a --no-input run left pending, or write one "
                    "non-interactively with --set EVENT_ID=ELECTION.")
    p_elect.add_argument("account", nargs="?",
                         help="Account to act on (omit to list all accounts)")
    p_elect.add_argument("--redo", action="store_true",
                         help="Clear the election(s) and re-prompt now")
    p_elect.add_argument("--reset", action="store_true",
                         help="Clear the election(s) without re-prompting "
                              "(next `tjs run` asks again)")
    p_elect.add_argument("--event", metavar="ID",
                         help="Scope --redo/--reset to one event id "
                              "(from the list); default is all")
    p_elect.add_argument("--set", metavar="EVENT_ID=ELECTION",
                         action="append",
                         help="Write one election non-interactively "
                              "(headless/CI bootstrap), e.g. --set "
                              "20250317-abg-abh-1a2b=rollover_s_85_1_5 "
                              "(Canada) or =reorg_368 (USA); the "
                              "event's own choices are listed by "
                              "`tjs elections ACCOUNT`")
    p_elect.add_argument("--hint", action="append", metavar="KEY=VALUE",
                         help="Hint for --set (repeatable), e.g. "
                              "--hint fmv_per_share=12.5")
    p_elect.add_argument("--pending", action="store_true",
                         help="Show elections a --no-input/headless run "
                              "left unresolved (work/pending_elections"
                              ".json), with ready-to-copy --set lines")
    p_elect.add_argument("--json", action="store_true",
                         help="With --pending: emit the raw pending "
                              "document")
    p_elect.set_defaults(func=cmd_elections)
