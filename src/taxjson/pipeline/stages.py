"""The stages `tjs run` walks: stage_account and the passes across accounts."""
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from taxjson.project.accounts import (
    AccountSettings, _transfers_accounts, account_of, accounts_of, typed_account, typed_accounts,
)
from taxjson.project.toml_io import parse_toml
from taxjson.project.settings import Settings, settings_of
from taxjson.lib import project_layout as _PL
from taxjson.lib.project_layout import _GAINS_ALL_SUFFIX
from taxjson.lib.cli_diag import write_text_atomic
from taxjson.lib.in_kind import IN_KIND_FILE
from taxjson.lib.country import (
    command_country_problem, plan_kinds as _plan_kinds,
)
from taxjson.lib.out import exit_text
from taxjson.lib import out as _outlib
from taxjson.lib.pipeline import (
    income_dating_flags, option_timing_flags, tt_json_path,
)
from taxjson.lib.safe_write import write_atomic as _write_atomic
from taxjson.model import money as M

from taxjson.pipeline import run_state
from taxjson.pipeline import stage_table as ST
from taxjson.pipeline.config import (
    load_config, load_settings,
    _brokerage_account_flags, _country, _income_rules,
        _PLAN_KINDS, _PLAN_NAMES, _PROJECT_ROOT_INPUTS, _read_config_text,
    CORP_ACTION_BROKERS, input_files, RATE_READING_BROKERS,
)
from taxjson.pipeline.diagnostics import (
    _attention_short_lines, _count_noun, _diag_lines, _echo_tt_bare_symbols,
    _echo_tt_totals, _split_msg, _wrote, _ZERO_TX_RE, attention_lines,
    ATTENTION_PREFIX, collect_diagnostics, echo_attention_lines,
    echo_parse_stats, run_capture, unbooked_lines, UNBOOKED_PREFIX,
    build_holdings_toml, fill_moved_cost, say_holdings_toml,
)
from taxjson.pipeline.freshness import (
    _read_work_stamp, timeline_beside, unmark)
from taxjson.pipeline.inputs import (
    _account_sidecars, _country_has_corp_rules, _dated_effective_lines,
    _generic_files, _layout_files, _mapping_choice, _inputs_fingerprint,
    _is_generic_group, _LOOKALIKE_RENAME_BROKERS, _loss_override_flags,
    _normalize_country, _note_sheltered_defaults, _parse_outputs,
    _parse_reads,
    _raw_align_adjust_currency, _raw_book_journals,
    _raw_mixed_currency_symbols, _raw_net_withholding,
    _refuse_crypto_openings, _report_detection, _reused_parse,
    _sheltered_elections, _split_generic_groups, _tax_date,
    _warn_expired_open_options, _warn_partial_carryovers,
    _warn_zero_value_spinoffs, group_inputs,
    group_inputs_detailed,
)
from taxjson.pipeline.messages import (
    _die, _die_input, _out_relpath, _out_wrap, _PROG, _say, _say_once,
    _step,
)
from taxjson.pipeline.tools import (
    _cmd, _drop_work_file, _echo_captured, _echo_stage_stderr,
    _work_path_problem, _write_work_stamp, run_to_file,
)


def stage_dated_events(cache: Path, accounts: List[str]):
    """The declared journals settled against the broker's own legs
    (lib/dated_events.settle_journals: a duplicate is booked once, a
    journal with one broker leg books the other), their legs written to
    each account's transfer evidence (work/<acct>_tt-journal_
    transfers.json) — once per run, from every equity account's parsed
    exports (`gathered`, lib/cross_listings.gather). Returns the
    Declarations (None when cmd_run read none: a stand-alone stage)."""
    from taxjson.lib import dated_events as DE
    key = cache.resolve()
    decl = run_state.current.dated_events.get(key)
    if decl is None or key in run_state.current.dated_settled:
        return decl
    run_state.current.dated_settled.add(key)
    if decl.journals:
        from taxjson.lib import cross_listings as XL
        legs, _n, _s = XL.gather(cache, accounts)
        for n in DE.settle_journals(decl.journals, legs):
            _say("note", *_split_msg(n), prog=_PROG)
        # A line restating the broker's own journal with another size
        # would move those units twice: it stops the run.
        _over = XL.partial_overlaps(decl.journals, legs)
        if _over:
            _die(f"{len(_over)} .tt dated-event line(s) cannot be booked",
                 *[f"- {p}" for p in _over],
                 "A journal the broker's rows already hold is booked from "
                 "them; a .tt JOURNAL line adds only what they lack.")
        # A bigger line a day from the broker's journal (the gambit's
        # trade date for its settlement date): booked, and said.
        from taxjson.lib.missing_history import _day_trades
        for _w in XL.near_restatements(decl.journals, legs,
                                       _day_trades(cache, accounts)):
            _say("warning", *_split_msg(_w), prog=_PROG)
    DE.write_sidecars(cache, accounts, decl.journals)
    by_acct: Dict[str, List[str]] = {}
    for j in decl.journals:
        if j.status != DE.STATUS_DUPLICATE:
            by_acct.setdefault(j.account, []).append(
                f"{j.date} {j.frm} -> {j.to} {j.quantity:g}")
    for acct, items in sorted(by_acct.items()):
        _say("note", f"{acct}: {len(items)} journal(s) from .tt lines "
             f"booked as transfer legs (one security, no disposition): "
             f"{'; '.join(items)}", indent="  ", file=sys.stdout)
    return decl


def stage_declared_renames(cache: Path) -> Path:
    """work/declared_renames.list: the ticker changes the project's .tt
    RENAME lines declare (read up front: _read_dated_events), one
    `OLD<TAB>NEW<TAB>WHERE` line each — what taxjson-brokerage
    --declared-renames reads, so a look-alike rename hint they answer
    is a note naming the line. Rewritten only when it changes (a parse
    stage's declared input); empty when there are none."""
    decl = run_state.current.dated_events.get(cache.resolve())
    path = cache / "declared_renames.list"
    text = "".join(f"{dr.old}\t{dr.new}\t{dr.where}\n"
                   for dr in (decl.tt_declared if decl is not None
                              else ()))
    if _read_work_stamp(path) != text:
        _write_work_stamp(path, text)
    return path


def stage_cross_listings(name: str, settings: Settings, cache: Path,
                         ticker_map: Optional[Path],
                         strict: bool = False) -> Optional[Path]:
    """Two listings of one security joined by their transfer journal
    (lib/cross_listings, tax-logic CA-XLIST-01 / US-XLIST-01): computed
    once per run from every equity account's parsed exports (the first
    pass parses them all before any account's books are merged), written
    to work/cross_listings.state, and — when any pair is joined — the map
    the merge stages read becomes work/ticker.map.effective (the
    project's ticker.map plus a TOBASE line per pair). Returns the map
    path the account's merge stages use. One `Warning:` per account
    names the pairs joined through its legs and the `DISTINCT` line that
    undoes each."""
    from taxjson.lib import cross_listings as XL
    key = cache.resolve()
    if key not in run_state.current.cross_listings:
        cfg = load_config(cache.parent, missing_ok=True)
        accounts = [n for n, c in accounts_of(cfg).items() if not c.crypto]
        named: set = set()
        apart: set = set()
        _renames: Dict[str, str] = {}
        if ticker_map is not None and ticker_map.is_file():
            from taxjson.lib.map_file import _parse_map_file
            try:
                _tm = _parse_map_file(ticker_map)[0]
            except (OSError, ValueError):
                _tm = None
            if _tm is not None:
                # A rename / DELETE naming a listing decides it; a
                # DISTINCT line keeps its own pair apart.
                named = set(_tm.delete)
                for _d in (_tm.glob, _tm.tobase, _tm.journal):
                    named.update(_d)
                    named.update(_d.values())
                for _dr in _tm.dated:
                    named.update((_dr.old, _dr.new))
                apart = set(_tm.distinct)
                # The map's renames as the base-currency books apply
                # them: a pair the map decides is read through them.
                from taxjson.lib.ticker_map_rules import merge_renames
                try:
                    _renames = merge_renames(_tm, to_base=True)
                except ValueError:
                    _renames = {}
        _pre = run_state.current.dated_events.get(key)
        for _dr in (_pre.renames if _pre is not None else ()):
            named.update((_dr.old, _dr.new))
        from taxjson.lib.country import is_canada
        from taxjson.lib import dated_events as _DE
        from taxjson.lib import tobase_map as _TBR
        _decl = stage_dated_events(cache, accounts or [name])
        _rows: list = []
        legs, names, shown = XL.gather(cache, accounts or [name], _rows)
        if _decl is not None and _decl.journals:
            legs = sorted(legs + _DE.journal_legs(_decl.journals),
                          key=lambda g: (g.date, g.account, g.broker,
                                         g.symbol, g.quantity))
        _base = str(settings.base_currency or "").upper() or None
        # One symbol naming two companies (a TSX fund's US-dollar unit
        # booked .US beside an NYSE stock of the root): never a join or
        # a TOBASE suggestion — the EXTRACT line that separates them.
        _coll = XL.collisions(_rows, names, shown, legs,
                              base_currency=_base)
        _refused: list = []
        result = XL.analyze(legs, names, shown, map_named=named,
                            map_distinct=apart, base_currency=_base,
                            collided=[c.symbol for c in _coll],
                            # A broker's currency journal between a
                            # security's CAD and USD lines is joined as a
                            # JOURNAL line in a Canadian project only
                            # (CA-XLIST-03); a US project reads its legs
                            # as transfers.
                            currency_journals=is_canada(
                                settings.country),
                            declared=(_decl.journals if _decl is not None
                                      else ()),
                            # The pairs left alone (the user's map, two
                            # companies): `tjs journals` lists them.
                            refused=_refused,
                            map_renames=_renames,
                            # A Canadian depositary receipt and its US
                            # share (the interlisted master, CA-XLIST-06):
                            # never joined from a journal, with no line.
                            receipts=_TBR.receipt_pairs(is_canada(
                                settings.country)))
        result["collisions"] = _coll
        result["refused"] = _refused
        # A pair the map refused whose legs it books as two symbols:
        # each leg is left unpaired (a Warning per account, below).
        result["map_split"] = XL.map_split(_refused, named, _renames,
                                           apart)
        state = cache / XL.STATE
        text = XL.state_text(result)
        if _read_work_stamp(state) != text:
            _write_work_stamp(state, text)
        # A .tt JOURNAL line between two listings nothing shows are one
        # security stops the run (pre-release review M2): a join of two
        # symbols on the user's word alone is a ticker.map TOBASE line.
        _unproven = [p for p in _refused
                     if p.extra.get("refused") == XL.UNPROVEN]
        if _unproven:
            _die(f"{len(_unproven)} .tt JOURNAL line(s) join two listings "
                 f"that nothing shows are one security",
                 *[f"- {p.extra.get('where') or p.out.account}: JOURNAL "
                   f"{p.out.date} {p.out.symbol} {p.into.symbol} — "
                   f"{p.reason}" for p in _unproven],
                 "A JOURNAL line moves units between two listings of ONE "
                 "security (a TSX line and its US-dollar or NYSE line): "
                 "check the two symbols. To join two symbols on your word "
                 "(the same shares under another ticker), add "
                 + " / ".join(
                     "`TOBASE {} {}`".format(*XL.tobase_direction(
                         p.out.symbol, p.into.symbol, _base))
                     for p in _unproven)
                 + " to ticker.map: the line is then booked. Else delete "
                 "the line.")
        eff = cache / _PL.EFFECTIVE_MAP
        # The ticker changes declared in .tt files (lib/dated_events)
        # reach every stage that books renames through the effective map.
        _ren_lines = _dated_effective_lines(cache)
        if result["joined"] or _ren_lines:
            body = XL.effective_map_text(ticker_map, result["joined"],
                                         _ren_lines)
            if _read_work_stamp(eff) != body:
                _write_work_stamp(eff, body)
            path: Optional[Path] = eff
        else:
            # The joins are gone: the books merged with them rebuild, as
            # each stage given the map declares the one it is given now
            # (`@ticker_map`: this file, no longer the effective map:
            # TJ-1807).
            eff.unlink(missing_ok=True)
            path = ticker_map
        run_state.current.cross_listings[key] = (path, result)
    path, result = run_state.current.cross_listings[key]
    for _c in result.get("collisions") or []:
        _h, _d = XL.collision_note(_c)
        _say_once(("xcollide", _c.symbol), "warning", _h, *_d,
                  indent="  ", file=sys.stdout)
    # A listing read from the evidence, not the row currency (lib/
    # listing_suffix): the join that names it says so; the others get
    # a Warning of their own (it changes the books too).
    from taxjson.lib import listing_suffix as LS
    _fixed = {k: LS.why(frm, r)
              for k, (frm, r) in LS.all_corrections(cache).items()}
    msg = XL.joined_note(name, result["joined"], corrected=_fixed)
    if msg:
        # A Warning: the join changes the books (pre-release review H1).
        _say_once(("xlist", name), "warning", msg[0], *msg[1],
                  indent="  ", file=sys.stdout)
    _in_joins = {p.into.symbol for p in result["joined"]
                 if p.into.account == name} | {
        p.out.symbol for p in result["joined"] if p.out.account == name}
    msg = LS.corrections_note(name, cache, skip=_in_joins)
    if msg:
        _say_once(("listing", name), "warning", msg[0], *msg[1],
                  indent="  ", file=sys.stdout)
    # A transfer pair the user's map books as two securities (CA/US-
    # XLIST-01): each leg is left unpaired — said, and --strict stops.
    msg = XL.map_split_note(name, result.get("map_split") or ())
    if msg:
        if strict:
            _die(f"--strict: {msg[0]}", *msg[1][:-1])
        _say_once(("xlist-split", name), "warning", msg[0], *msg[1],
                  indent="  ", file=sys.stdout)
    return path


# work/to_base.csv's companion: the first date it was built from
# (lib/rates_window). A file without one was built from 2000-01-01.
RATES_START_STAMP = ".to_base.start"


def _rates_inputs(root: Path, inputs_dir: Path,
                  accounts: Mapping[str, Any]) -> List[Path]:
    """The files whose dates bound the FX window (lib/rates_window):
    every account's exports and .tt files, the year's slips and its
    positions snapshots. Read-only; a folder that cannot be listed
    gives nothing (the run's own checks name it)."""
    folders = [inputs_dir / n for n in sorted(accounts or {})]
    folders += [_PL.slips_dir(root), _PL.holdings_folder(root)]
    out: List[Path] = []
    for d in folders:
        try:
            out += [p for p in sorted(d.iterdir())
                    if p.is_file() and not p.name.startswith((".", "~$"))
                    and p.suffix.lower() in (".csv", ".tt", ".toml",
                                             ".txt", ".tsv")
                    and not _PL.redacted_copy_beside_exports(p)]
        except OSError:
            continue
    return out


def stage_currency_rates(settings: Settings, cache: Path,
                         inputs: Optional[List[Path]] = None) -> Path:
    """Build to_base.csv by appending taxjson-to-base-curr output for each
    configured source currency. Caches across runs while every source
    currency's rates reach the last few days: a refresh whose download
    failed for one currency (its block ends early) is refetched on the
    next run instead of being served for days behind another currency's
    fresh last line (S046-06). `inputs`: the project's files
    (_rates_inputs) — the window starts at the earliest date in them
    (lib/rates_window), not 2000-01-01, and a file built from a later
    start is rebuilt."""
    _required(settings, "base_currency")
    base = settings.base_currency
    sources = [c for c in (settings.source_currencies if settings.source_currencies is not None else ["USD"]) if c != base]
    # Ensure the cache dir exists before any write — applies to both
    # the no-sources branch (writes an empty file as a marker) and the
    # fetch branch below. Without this, a fresh CAD-only setup with
    # `source_currencies = []` would crash on the empty-write because
    # `work/` didn't exist yet.
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    rates_path = cache / "to_base.csv"
    if rates_path.is_dir():
        # a traceback at st_size / write_text (re-audit A2-1433)
        _die_input(_work_path_problem(rates_path))
    if not sources:
        # Only touch the marker when its content is wrong: an
        # unconditional write bumped the mtime every run, and since
        # to_base.csv is a merge2/convert dep, --fast re-merged every
        # account on every invocation for offline (no-source-currency)
        # configs — the cache never actually cached.
        if not rates_path.exists() or rates_path.stat().st_size:
            _write_atomic(rates_path, "")
        return rates_path
    # `needs_rebuild` here picks up the implicit taxjson.toml dependency
    # (via run_state.config_path), so a change to base_currency or
    # source_currencies invalidates the cached rates file. Without
    # this, switching from CAD-base to USD-base silently reused the
    # old rates. The coverage check catches the inverse failure: an
    # mtime-fresh file whose data ends in the past.
    start: Optional[str] = None
    stamp = cache / RATES_START_STAMP
    if inputs is not None:
        from taxjson.lib.rates_window import window_start
        start = window_start(settings.year, inputs).isoformat()
    built_from = (_read_work_stamp(stamp) or "").strip() or None
    # The stage table's rates row (_rates_build): the record first; the
    # coverage and the window are grounds of its own, asked when the record
    # says fresh. Reused (fetched data, not derived): a run refetches only
    # stale rates.
    ST.execute(_STAGES, ST.StageCtx(work=cache, settings=settings, currencies=sources,
                               window=start, built_from=built_from), "rates", reuse=True)
    return rates_path


def _write_source_stamps(name: str, acct_dir: Path, cache: Path,
                         grouped: Dict[str, List[Path]], settings: Settings,
                         choices: Dict[Path, Any], is_crypto: bool,
                         froc_acb: bool, fut_next: bool) -> None:
    """work/<name>_sources.list and work/<name>_inputs.fingerprint, each
    rewritten only when it changes: the account's inputs (membership and
    content), deps of its parse, corp, .tt and merge stages. Called again
    after a crypto account's sends stage writes or removes the generated
    crypto_sends.tt, so the stages after it ask with the stamps as the run
    leaves them: stamped first without the file, the chain rebuilt once
    on the next `--fast` (backlog TJ-4131)."""
    # Deletion-blindness guard (FUZZ #J): mtime deps only cover files
    # that EXIST — deleting an input CSV left its trades in the cached
    # books under --fast (the broker group's parsed JSON looked fresh
    # against the remaining, older CSVs). Persist the account's input
    # membership; add/remove bumps the manifest's mtime and re-runs the
    # parse + merge. Content-compare first so an unchanged listing
    # never dirties the cache.
    src_manifest = cache / f"{name}_sources.list"
    # Membership covers EVERY input kind that feeds the merge — CSVs and
    # .tt files alike. Listing only CSVs left a deleted .tt's
    # transactions in the cached books under --fast (its converted JSON
    # dropped out of `sources`, so the merge output looked fresh against
    # every remaining dep).
    _map_entries = []
    for _c, _ch in choices.items():
        # WHICH mapping is effective per file (and the layout it stands
        # for) is manifest content: deleting a sidecar switches the file to
        # the shared mapping with no dep getting newer (FUZZ #J class).
        _map_entries.append(f"map/{_c.name}={_ch.manifest()}")
    src_txt = "\n".join(sorted(
        [f"{broker}/{p.name}" for broker, csvs in grouped.items()
         for p in csvs]
        + [f"tt/{p.name}" for p in _account_tt_files(acct_dir, cache, name)]
        + _map_entries
        + (["setting/foreign_return_of_capital=acb"] if froc_acb else [])
        + (["setting/futures_settle=next_day"] if fut_next else [])
        # Income dating overrides move ROC dates (the ACB) and income
        # years: a change re-runs the books.
        + [f"setting/income_dating={' '.join(income_dating_flags(settings))}"]
        * bool(income_dating_flags(settings))
        # The crypto parsers date UTC stamps in this zone: a change
        # re-parses.
        + ([f"setting/local_timezone={settings.local_timezone}"]
           if is_crypto and settings.local_timezone else [])
        # Moves between your own accounts (US-BASIS-05): the legs file
        # going away must rebuild the books.
        + ([f"moves/{name}{OWN_MOVES_SUFFIX}"]
           if (cache / f"{name}{OWN_MOVES_SUFFIX}").is_file() else []))) \
        + "\n"
    if _read_work_stamp(src_manifest) != src_txt:
        _write_work_stamp(src_manifest, src_txt)
    # Content, not just membership: `run --fast` compared mtimes only,
    # so a CSV replaced by a corrected export carrying an OLDER mtime
    # (cp -p, rsync -a, unzip) — or a ticker.map restored the same way
    # — kept the previous parse at exit 0 (R1-253, R1-294). A digest of
    # every input and project-root map file; when it changes, the
    # sources manifest (a dep of every parse/corp/merge stage) is
    # touched so the rebuild cascades.
    _fp_file = cache / f"{name}_inputs.fingerprint"
    _fp_txt = _inputs_fingerprint(
        [p for csvs in grouped.values() for p in csvs]
        + list(_account_tt_files(acct_dir, cache, name))
        + [p for _ch in choices.values() for p in _ch.deps]
        + [acct_dir / "generic.toml"]
        + _layout_files(cache.parent)
        + [_PL.project_map_path(cache.parent, _m)
           for _m in _PROJECT_ROOT_INPUTS]
        # The elections manifest (and its legacy work/ copy), a crypto
        # account's sends.json and taxjson.toml itself change the books
        # too: restored with an older mtime they kept the previous
        # election / setting under --fast (A2-0224, A2-0985, A2-1229).
        + [_m for _m in (acct_dir / "manifest.json",
                         cache / f"{name}_manifest.json")
           if not _is_empty_manifest(_m)]
        + [acct_dir / "sends.json"]
        + ([run_state.config_path] if run_state.config_path is not None else []))
    if _read_work_stamp(_fp_file) != _fp_txt:
        _write_work_stamp(_fp_file, _fp_txt)      # a dep of the merge below


def _not_folders(paths: Iterable[Path]) -> List[Path]:
    """The `paths` that are not folders, to declare: a folder at a file's
    name is not that file (its tool reads none there, or refuses it in its own words:
    an account's manifest.json), and a declared folder stops the run
    (freshness.FolderInputError; backlog TJ-4133, TJ-4134)."""
    return [p for p in paths if not Path(p).is_dir()]


def _account_tt_files(acct_dir: Path, cache: Path, name: str
                      ) -> List[Path]:
    """The account's .tt files the run reads: inputs/<account>/*.tt, and
    with shared exports (lib/project_layout) the crypto_sends.tt this
    year generated in its work/. A generated crypto_sends.tt left in the
    shared folder (written by an older taxjson) is not read — it holds
    the decisions as another year's map spelled them — with a warning
    once; one you wrote yourself is read like any .tt file."""
    tts = input_files(acct_dir, ".tt")
    root = cache.parent
    if not _PL.shared_inputs(root):
        return tts
    from taxjson.lib import crypto_sends as CS
    keep = []
    for t in tts:
        if t.name.lower() == CS.TT_NAME and (
                CS.read_tt(t) or "").startswith(CS.GENERATED_MARK):
            _say_once(("shared-sends-tt", str(t)), "warning",
                      f"{_PL.shown(t, root)}: generated by an older taxjson "
                      f"into the exports every year shares — not read; "
                      f"each year now generates its own in work/ from "
                      f"sends.json",
                      "Delete it when no single-folder project needs it.",
                      prog=_PROG)
            continue
        keep.append(t)
    gen = CS.tt_path(root, name)
    if not gen.is_file():
        return keep
    mine = [t for t in keep if t.name.lower() == CS.TT_NAME]
    if mine:
        # Both would convert to one work/<acct>_tt_crypto_sends.json
        # (pipeline.tt_json_path): one of them silently dropped.
        _die_input(f"{_PL.shown(mine[0], root)} (a .tt file you wrote) and "
                   f"{_PL.shown(gen, root)} (generated from sends.json by "
                   f"`tjs crypto-sends`) have the same name, so the "
                   f"run would read only one of them",
                   f"Rename yours (e.g. {name}_sends_manual.tt) and run "
                   f"again.")
    return keep + [gen]


def _resolve_manifest(acct_dir: Path, cache: Path, name: str,
                      create: bool = False) -> Path:
    """The corp-action elections manifest for an account. Canonical home is
    `inputs/<account>/manifest.json` — elections and hand-typed FMV/ACB
    hints are user DECISIONS, the one thing that is NOT rebuildable from
    inputs, so they must not live in the disposable (gitignored,
    documented-as-safe-to-delete) work/ cache. A legacy
    `work/<account>_manifest.json` is migrated on first touch; `create`
    is kept for callers (nothing is written for a project with no
    elections yet)."""
    user_manifest = acct_dir / "manifest.json"
    legacy = cache / f"{name}_manifest.json"
    if user_manifest.exists():
        return user_manifest
    if user_manifest.is_symlink():
        # A dangling link is not "no manifest": writing through it put
        # an empty {"elections": {}} at the link target (or crashed),
        # and the saved elections were silently gone (re-audit
        # A2-1400). The user's decisions are not rebuildable — refuse.
        import os as _os
        try:
            _tgt = _os.readlink(user_manifest)
        except OSError:
            _tgt = "?"
        _die_input(f"{user_manifest} is a symlink to {_tgt}, which does "
                   f"not exist (or loops) — it should hold your "
                   f"corporate-action elections. Fix the link (or remove "
                   f"it to start a new manifest).")
    if legacy.exists() and _PL.shared_inputs(cache.parent):
        # Exports shared by every year: a run never writes them; the
        # year's legacy copy is read where it is (`tjs elect`
        # saves into the shared folder).
        return legacy
    if legacy.exists():
        acct_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Atomic (tmp + replace, as Manifest.save writes): a failed write
        # left a truncated canonical manifest that then won over the
        # intact legacy copy on every later run (A2-0218).
        from taxjson.lib.safe_write import write_atomic
        write_atomic(user_manifest, legacy.read_text(encoding="utf-8"))
        _say("note", f"migrated the elections manifest to "
             f"{user_manifest}",
             "Version-control this file; the work/ copy is no longer "
             "read once it exists.", file=sys.stdout)
        return user_manifest
    # No manifest yet: the canonical path, not written — Manifest.load
    # treats a missing file as empty, and `tjs elect` writes it when
    # the first election is saved. An empty {"elections": {}} used to
    # be written on every first run of a project reading its own
    # inputs/, which dirtied a frozen year's committed copy (2026-10
    # workflow audit); the shared layout never wrote it.
    return user_manifest


def _locked_year_flags(root: Path, settings: Settings) -> List[str]:
    """`--locked-year Y` per filed-year lock (US projects; US-WASH-22)."""
    from taxjson.lib.filed_locks import locked_year_flags
    return locked_year_flags(root, settings)


def _wash_flags(is_taxable: bool, is_crypto: bool, country: str) -> List[str]:
    """Wash-detection flags for an account's gains run. US crypto gets
    --no-wash: the IRS treats digital assets as property, not "securities",
    so §1091 does not apply — running it denied legitimate crypto losses.
    Canada's superficial-loss rule covers any identical property, crypto
    included, so crypto stays wash-checked there."""
    if not is_taxable:
        return []
    flags = ["--taxable"]
    if is_crypto and country == "usa":
        flags.append("--no-wash")
    return flags


class PendingElectionsError(RuntimeError):
    """An account's corp-action events need elections and prompting
    isn't possible (--no-input or no TTY). Carries the pending-JSON
    path so the orchestrator can aggregate and exit 3 instead of
    crashing."""

    def __init__(self, account: str, pending_path: Path):
        super().__init__(f"{account}: elections required")
        self.account = account
        self.pending_path = pending_path


_SKIPPED_ACCOUNTS_FILE = "skipped_accounts.json"


def _record_skipped_accounts(cache: Path, names: List[str], *,
                             only: Optional[str] = None) -> None:
    """Persist which accounts `tjs run` skipped for having no inputs
    (work/skipped_accounts.json), so later commands don't tell the user
    to "run `tjs run` first" about an account that run deliberately
    skipped. A single-account run (`only`) updates just that entry."""
    import json as _json
    path = cache / _SKIPPED_ACCOUNTS_FILE
    keep = set()
    if only:
        try:
            keep = set(_json.loads(path.read_text(encoding="utf-8"))
                       .get("accounts") or []) - {only}
        except (OSError, ValueError, AttributeError):
            keep = set()
    names_all = sorted(keep | set(names))
    try:
        if names_all:
            cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            _write_atomic(path, _json.dumps({"schema_version": 1,
                                             "accounts": names_all},
                                            indent=2) + "\n",
                          encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass                          # advisory only — never break a run


def _crypto_broker_files(root: Path, cfg: Dict[str, Any]
                         ) -> Dict[str, List[Tuple[str, Path]]]:
    """{crypto account: [(broker, csv)]} — the raw exchange exports the
    stablecoin pool of `tjs crypto-sends` is rebuilt from."""
    from taxjson.lib.crypto_sends import crypto_accounts
    out: Dict[str, List[Tuple[str, Path]]] = {}
    for acct in crypto_accounts(cfg):
        d = _PL.inputs_dir(root) / acct
        if d.is_dir():
            out[acct] = [(b, p) for b, ps in group_inputs(d).items()
                         for p in ps]
    return out


def _is_us(cfg: Dict[str, Any]) -> bool:
    return _country(settings_of(cfg)) == "usa"


def _required(settings: Settings, *keys: str) -> None:
    """KeyError naming the first of `keys` the settings leave unset: the
    run checks them up front, a stage called on its own did not (the
    dict's subscript used to)."""
    for key in keys:
        if getattr(settings, key) is None:
            raise KeyError(key)


def _sends_tt_shown(root: Path, acct: str) -> str:
    """The generated crypto_sends.tt of `acct` as the user finds it:
    inputs/<acct>/crypto_sends.tt, or with shared exports this year's
    work/crypto_sends/<acct>/crypto_sends.tt (crypto_sends.tt_path)."""
    from taxjson.lib import crypto_sends as CS
    return _PL.shown(CS.tt_path(root, acct), root)


def _sends_json_shown(root: Path, acct: str) -> str:
    """The account's sends.json (the decisions) as the user finds it:
    inputs/<acct>/sends.json, or ../inputs/<acct>/sends.json."""
    from taxjson.lib import crypto_sends as CS
    return _PL.shown(_PL.inputs_dir(root) / acct / CS.MANIFEST_NAME, root)


def _crypto_sends_tt(root: Path, acct: str, report: Dict[str, Any]
                     ) -> Tuple[str, List[Dict[str, Any]]]:
    """(Re)write the generated crypto_sends.tt (crypto_sends.tt_path) from the report. Returns
    (write status, duplicate hand-written lines). Raises
    crypto_sends.RefusedDecision AFTER writing the file without them
    when a saved decision is one the country refuses (a US gift:
    partition SPEC-01/INPUTS-04), and crypto_sends.UnpricedSends AFTER
    writing the priced lines when a gift/payment or network fee cannot
    be priced (its ``dups`` carries the duplicate lines)."""
    from taxjson.lib import crypto_sends as CS
    adoc = report["accounts"][acct]
    entries, unpriced = CS.tt_entries(adoc)
    refused = CS.refused_entries(adoc)
    # Every priced entry is written on its own; one that cannot be
    # priced is reported AFTER the write (re-audit A2-0240: a single
    # unpriceable network fee used to hold back the owner's fully
    # priced gifts and payments, and left an old crypto_sends.tt).
    status = CS.write_tt(Path(adoc["tt_file"]),
                         CS.render_tt(acct, entries, report["country"]))
    if refused:
        raise CS.RefusedDecision(
            f"{_sends_json_shown(root, acct)}: "
            + "; ".join(e["refused"] for e in refused)
            + f" ({_sends_tt_shown(root, acct)} {status} without it)")
    if unpriced:
        _fee = [e["id"] for e in unpriced if e.get("network_fee")]
        _dec = [e["id"] for e in unpriced if not e.get("network_fee")]
        _exc = CS.UnpricedSends(
            "no fair value for " + ", ".join(e["id"] for e in unpriced)
            + " (the price lookup failed or TAXJSON_OFFLINE is set and "
              "the price is not cached) — NOT booked. Re-run online, or "
              f"give the value per coin in {report['base_currency']}: "
            + "; ".join(
                ([f"`tjs crypto-sends {acct} --set {i}="
                  f"{_disposing_word(report['country']).replace('/', '|')}"
                  f" --price P`" for i in _dec[:1]] if _dec else [])
                + ([f"`tjs crypto-sends {acct} --set {i}=fee "
                    f"--price P` (the network fee hidden in a send that "
                    f"arrived short)" for i in _fee[:1]] if _fee else []))
            + f". {_sends_tt_shown(root, acct)} {status} with the "
              f"{len(entries)} priced line(s).")
        _exc.dups = CS.duplicate_lines(_PL.inputs_dir(root) / acct, entries)
        _exc.status = status
        raise _exc
    return status, CS.duplicate_lines(_PL.inputs_dir(root) / acct, entries)


def _send_decisions(country: str) -> List[str]:
    """The crypto-sends decisions the project's country accepts (a US
    project refuses `gift`, lib/country COMMAND_COUNTRY
    "crypto-sends:gift"; US-SEND-02)."""
    from taxjson.lib import crypto_sends as CS
    return [d for d in CS.DECISIONS
            if command_country_problem("crypto-sends", country, d) is None]


def _disposing_word(country: str) -> str:
    """'gift/payment' in Canada, 'payment' in the US: the decisions that
    write a sale (audit A2-1283, A2-1285, A2-1329)."""
    from taxjson.lib import crypto_sends as CS
    return "/".join(d for d in _send_decisions(country)
                    if d in CS.DISPOSING)


def _dup_warning(acct: str, dups: List[Dict[str, Any]],
                 root: Optional[Path] = None) -> List[str]:
    """One line per hand-written .tt line that books a send the
    generated crypto_sends.tt also books — both file names, the send
    and its timestamp. Nothing is deleted: the owner picks the line."""
    from taxjson.lib import crypto_sends as CS
    gen = (_sends_tt_shown(root, acct) if root is not None
           else f"inputs/{acct}/{CS.TT_NAME}")
    out = []
    for d in dups:
        when = (d["timestamp"] if d["same_time"]
                else f"{d['timestamp'][:10]} (the hand-written line has "
                     f"another date or time, or a near quantity)")
        if d["id"].endswith("-fee"):
            out.append(
                f"{d['file']} line {d['line']} and {gen} "
                f"both sell {CS.fmt_qty(d['quantity'])} "
                f"{d['symbol']} on {when} (the network fee {d['id']}, "
                f"booked from the send that arrived short) — that "
                f"disposition is counted twice. Delete the hand-written "
                f"line.")
            continue
        out.append(
            f"{d['file']} line {d['line']} and {gen} "
            f"both sell {CS.fmt_qty(d['quantity'])} {d['symbol']} on "
            f"{when} (send {d['id']}) — that disposition is counted "
            f"twice. Delete the hand-written line (crypto_sends.tt is "
            f"generated from sends.json), or, to keep it, record the send "
            f"as `self`: `tjs crypto-sends {acct} --set "
            f"{d['id']}=self`.")
    return out


def _crypto_sends_problems(name: str, adoc: Dict[str, Any]) -> List[str]:
    """What the run must not book silently for crypto account `name`
    (each a WARNING; `run --strict` stops on any): a saved gift/payment
    the automatic pairing overrode (re-audit A2-0004). (A US move
    between two crypto accounts is carried by the blended crypto pass,
    US-CRYPTO-05 — no longer a problem, A2-0003.)"""
    out = []
    for o in adoc.get("overridden") or []:
        out.append(
            f"inputs/{name}/sends.json saves {o['id']} ({o['summary']}) as "
            f"`{o['decision']}`, but that send now pairs with an arrival "
            f"— {o['arrival']} — so it is booked as your own move and the "
            f"{o['decision']} is NOT booked. If that arrival is unrelated: "
            f"`tjs crypto-sends {name} --set {o['id']}={o['decision']} "
            f"--unpair`; if the pairing is right: `tjs crypto-sends "
            f"{name} --set {o['id']}=self`.")
    return out


def _stage_crypto_sends(root: Path, name: str, interactive: bool,
                        strict: bool = False) -> None:
    """`tjs run` hook for a crypto account, right after the parse:
    prompt for undecided sends at a TTY (else one note line), then
    refresh the generated crypto_sends.tt from the saved decisions.

    What the books then lack or hold twice — a decided gift/payment
    that cannot be written (no fair value, a malformed sends.json), a
    hand-written .tt line that sells a send the generated file sells
    too — goes to work/<acct>_crypto_sends.diag (UNBOOKED / ATTENTION
    lines: the account .sum DIAGNOSTICS and the checklist's run-clean
    step read them), and `run --strict` stops on it and on an undecided
    send (A2-0112, A2-0127, A2-0362)."""
    from taxjson.lib import crypto_sends as CS
    cfg = load_config(root, missing_ok=True)
    cache = root / "work"
    _diag = cache / f"{name}_crypto_sends.diag"
    # Another crypto account not parsed yet, or parsed before its
    # export changed (a `run --account` subset): its arrivals are
    # unknown or stale, so a send to it would look like a gift. Don't
    # ask, and say so (re-audit A2-0077). A full run parses every
    # crypto account before this stage (cmd_run), so this is rare.
    files = _crypto_broker_files(root, cfg)
    unparsed = CS.stale_evidence(cache, files,
                                 skip=[name] + _transfers_accounts(cfg))
    # (prefix, message): UNBOOKED for a declared disposition the run
    # could not book, ATTENTION for the rest — written to
    # work/<acct>_crypto_sends.diag for the .sum and run-clean.
    _problems: List[Tuple[str, str]] = []
    _dups: List[str] = []
    # Said and kept in the .sum, but not a --strict stop: a coin priced
    # under its default Yahoo id while the price cache holds a numbered
    # id of its ticker (the CRYPTO line to add is in the message).
    _price_fn = CS.yahoo_usd_price(root)
    try:
        report = CS.build_report(root, cfg, files, _price_fn,
                                 want=name, exports_only=True)
        adoc = report["accounts"].get(name)
        if adoc is None:
            _diag.unlink(missing_ok=True)
            return
        if adoc["undecided"] and interactive and not unparsed:
            if CS.prompt_undecided(adoc["sends"], Path(adoc["manifest"]),
                                   allow_gift=command_country_problem(
                                       "crypto-sends",
                                       _country(cfg.get("settings")),
                                       "gift") is None):
                report = CS.build_report(root, cfg, files, _price_fn,
                                         want=name, exports_only=True)
                adoc = report["accounts"][name]
        if unparsed:
            _problems.append((ATTENTION_PREFIX,
                f"the transfer evidence of {'; '.join(unparsed)} is not "
                f"current, so a send to it may look unmatched (or a "
                f"network fee be missed) — run without --account."))
        if adoc["undecided"]:
            # A US donor's gift is not a sale (US-SEND-02; audit A2-0721,
            # A2-0740, A2-1286): the US note names payments only.
            _ctry = _country(cfg.get("settings"))
            _decs = " / ".join(_send_decisions(_ctry))
            _why = ("a payment is a sale at fair value; a gift is not a "
                    "sale for a US donor, and taxjson cannot book it yet"
                    if _ctry == "usa" else
                    "a gift or payment is a disposition at fair value")
            _say("note", f"{adoc['undecided']} crypto send(s) not yet "
                 f"classified as {_decs}",
                 f"`tjs crypto-sends {name}` lists them ({_why}).",
                 indent="  ")
            if strict and not unparsed:
                # A pending decision, like a pending election: a send
                # that may be a disposition is not in the books (A2-0362).
                _diag.unlink(missing_ok=True)
                _die(f"--strict: {name}: {adoc['undecided']} crypto "
                     f"send(s) not yet classified as {_decs} — aborting",
                     f"Decide each with `tjs crypto-sends {name} "
                     f"--set ID=...`.")
        if adoc.get("orphans"):
            _say("note", f"{_sends_json_shown(root, name)} has "
                 f"{len(adoc['orphans'])} decision(s) for send ids that "
                 f"no longer exist ({', '.join(adoc['orphans'][:3])}"
                 f"{' ...' if len(adoc['orphans']) > 3 else ''})",
                 f"The inputs or local_timezone changed? They are "
                 f"ignored; `tjs crypto-sends {name}` lists the "
                 f"current ids.", indent="  ")
        _problems += [(ATTENTION_PREFIX, w)
                      for w in _crypto_sends_problems(name, adoc)]
        try:
            status, dups = _crypto_sends_tt(root, name, report)
        except CS.UnpricedSends as e:
            status, dups = getattr(e, "status", ""), getattr(e, "dups", [])
            # A declared disposition the run could not book (R1-104).
            _problems.append((UNBOOKED_PREFIX, str(e)))
        _tt_shown = _sends_tt_shown(root, name)
        if status == "written":
            _step(f"Writing crypto sends {_tt_shown}")
        elif status == "removed":
            _step(f"Removing crypto sends {_tt_shown}")
        # Booked twice (A2-0127): --strict stops, the .sum says so.
        _dups = _dup_warning(name, dups, root)
    except CS.RefusedDecision as e:
        # A saved decision the country refuses (a US gift): not booked,
        # and the run stops until sends.json says what it was.
        _diag.unlink(missing_ok=True)
        _die(f"{name}: crypto sends: {e}")
    except ValueError as e:
        # A decided gift/payment that cannot be written is a missing
        # disposition: the .sum DIAGNOSTICS carries it (A2-0112).
        tt = CS.tt_path(root, name)
        if tt.exists():
            # The old generated file was written from decisions that can
            # no longer be read: it may book a send since reclassified
            # (gift -> self), or a gift a US project refuses (US-SEND-02).
            # Booking it on a guess is worse than stopping (audit A2-0415).
            _diag.unlink(missing_ok=True)
            _die(f"{name}: crypto sends: {e}",
                 f"The generated {_sends_tt_shown(root, name)} was "
                 f"written from the earlier decisions and is not booked "
                 f"on a guess — fix the cause above (the decisions are "
                 f"in {_sends_json_shown(root, name)}), or delete "
                 f"{CS.TT_NAME} to run without the decided sales, and "
                 f"run again.")
        msg = (f"{e} The decided gift(s)/payment(s) it names are NOT "
               f"booked"
               + (f"; the previous {_sends_tt_shown(root, name)} is "
                  f"still booked as it was" if tt.exists() else "")
               + ". `run --strict` refuses this.")
        _problems.append((UNBOOKED_PREFIX, msg))
    for _p, w in _problems:
        _echo_captured(f"{_p} {name}: crypto sends: {w}", indent="",
                       file=sys.stderr)
    for w in _dups:
        _echo_captured(f"{ATTENTION_PREFIX} {w}", indent="",
                       file=sys.stderr)
    _problems += [(ATTENTION_PREFIX, w) for w in _dups]
    # (a stand-in price function, as in tests, has no .attention)
    _id_notes = [f"crypto id: {w}"
                 for w in getattr(_price_fn, "attention", ())]
    for w in _id_notes:
        _echo_captured(f"{ATTENTION_PREFIX} {name}: crypto sends: {w}",
                       indent="", file=sys.stderr)
    if _problems or _id_notes:
        _write_atomic(_diag, "".join(f"{p} {name}: crypto sends: {m}\n"
                                     for p, m in _problems + [
                                         (ATTENTION_PREFIX, w)
                                         for w in _id_notes]),
                      encoding="utf-8")
    else:
        _diag.unlink(missing_ok=True)
    if strict and _problems:
        _die(f"--strict: {name}: crypto sends: {len(_problems)} "
             f"problem(s) above would leave the books wrong — aborting")


def ib_foreign_roc_mode(settings: Settings) -> str:
    """How the IB parser books an issuer-designated '(Return of
    Capital)' from a non-Canadian issuer: lib/country.foreign_roc_mode
    (the one resolver `run` and tax-logic share). Canada: [settings]
    foreign_return_of_capital, default "dividend" (ITA s.90(1)). USA:
    always "acb" (IRC s.301(c)(2); the key is Canada-only and refused
    in a US project — audit S013-01, partition INPUTS-02)."""
    from taxjson.lib.country import CountryError, foreign_roc_mode
    _country(settings)
    try:
        return foreign_roc_mode(settings)
    except CountryError as e:
        _die(str(e))


def _apply_override_log(corp_json: Path, log: Path, account: str) -> None:
    """Rename corp-action rows the way the security overrides renamed
    the same broker's parsed rows ((symbol, currency) -> new symbol).
    A pair the overrides renamed for some rows and not others is
    ambiguous (two securities share the broker's spelling): a corporate
    action on it is refused rather than booked on a guess."""
    import json as _json
    if not log.exists() or not corp_json.exists():
        return
    try:
        doc = _json.loads(log.read_text(encoding="utf-8"))
        corp = _json.loads(corp_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    renamed = {k: v for k, v in (doc.get("renamed") or {}).items() if v}
    kept = set(doc.get("kept") or [])
    if not renamed:
        return
    changed = False
    for t in corp.get("transactions", []) or []:
        cur = str(t.get("currency") or "").upper()
        for fld in ("symbol", "symbol_new"):
            sym = t.get(fld) or ""
            key = f"{sym}|{cur}"
            if key not in renamed:
                continue
            targets = renamed[key]
            if len(targets) > 1 or key in kept:
                _die(f"{account}: a corporate action on {sym} ({cur}), "
                     f"but a ticker.map EXTRACT line renames some "
                     f"{sym} rows (to {', '.join(targets)}) and not "
                     f"others — two securities share that spelling, so "
                     f"the event cannot be assigned. Record it with a "
                     f".tt file (and `tjs elect {account} --set "
                     f"<event>=ignore`).")
            t[fld] = targets[0]
            changed = True
    if changed:
        from taxjson.lib.safe_write import write_atomic
        write_atomic(corp_json,
                     _json.dumps(corp, indent=2, sort_keys=True) + "\n")


def _config_has_distributions() -> bool:
    """True when the project's taxjson.toml has [[distributions]]
    entries (checked by every config reader — lib/project_tables)."""
    if run_state.config_path is None or not run_state.config_path.is_file():
        return False
    try:
        return bool(parse_toml(_read_config_text(run_state.config_path))
                    .get("distributions"))
    except Exception:                               # noqa: BLE001
        return False


def _is_empty_manifest(path: Path) -> bool:
    """An elections manifest with no elections — the file the corp stage
    creates on an account's first run — is the same as none, so its
    appearance is no change in a record."""
    import json as _json
    try:
        return _json.loads(path.read_text(encoding="utf-8")) == {
            "elections": {}}
    except (OSError, ValueError):
        return False


# The lock's `frozen` record of the edits `tjs migrate` made to a
# frozen year's taxjson.toml (backlog TJ-0231).
_MIGRATED = "migrated"


def _migrate_edit(root: Path, frozen: Dict[str, Any]
                  ) -> Optional[Dict[str, Any]]:
    """The lock's record of `tjs migrate`'s edit of the frozen
    taxjson.toml (frozen.migrated) when the file is as migrate left it,
    else None (backlog TJ-0231)."""
    from taxjson.lib.checklist import run_config_state
    m = frozen.get(_MIGRATED) if isinstance(frozen, dict) else None
    if not isinstance(m, dict) or not isinstance(m.get(_PL.CONFIG), str):
        return None
    now = run_config_state(Path(root) / _PL.CONFIG)
    return m if now is not None and now[0] == m[_PL.CONFIG] else None


def _parse_rates(broker: str, rates: Optional[Path]) -> List[Path]:
    """The run's rates file as a parse's `--rates` and its input: [] or [the
    file]. Only a parser that reads it gets it (the registry's `reads_rates`:
    Coinbase and Kraken check a stablecoin fill for a de-peg through it, its
    notes in the parse's .diag, and Kraken values a fiat leg of a multi-leg
    receipt with it, which sets the split's shares), so a rates change
    rebuilds that parse and no other parse can read it undeclared (TJ-2497,
    TJ-2508; the freshness-edge audit found it)."""
    if broker not in RATE_READING_BROKERS or not rates:
        return []
    p = Path(rates)
    return [p] if p.is_file() and p.stat().st_size else []


def _books_left(cache: Path, name: str) -> bool:
    """Whether account `name`'s books from earlier runs stay in work/: there,
    and not this full run's to remove (the last run listed them: stage_sweep
    removes them before any report reads them and says so, TJ-4270)."""
    return (cache / f"{name}_base.json").exists() and not (
        run_state.current.full_run and bool(ST.swept_accounts(cache, [name])))


def stage_account(name: str, acfg: Mapping[str, Any], settings: Settings,
                  inputs_dir: Path, cache: Path, reports_dir: Path,
                  rates: Path, ticker_map: Optional[Path],
                  security_overrides: Optional[Path],
                  incomplete_history: Optional[Path] = None,
                  no_input: bool = False,
                  strict: bool = False,
                  parse_only: bool = False,
                  ) -> Optional[Dict[str, Path]]:
    """Full per-account pipeline. Returns {base, gains, sum} paths, or None
    when the account has no inputs yet (a configured-but-unpopulated account,
    e.g. straight after `tjs init`) — that's a warn-and-skip, not a fatal
    error, so the other accounts still run. `parse_only`: stop after the
    broker parse (returns None) — cmd_run parses every crypto account
    first so each one's sends pair against current arrivals."""
    acct_dir = inputs_dir / name
    if not acct_dir.exists():
        # Once per run: the first pass stages the account too. The
        # folder is named as it is (../inputs/tfsa with shared exports).
        _root = cache.parent
        _say_once(("no-dir", name), "warning",
                  f"no inputs dir for account '{name}' "
                  f"({_PL.shown(acct_dir, _root)}); skipping", prog=_PROG)
        _sib = _root.parent / _PL.INPUTS
        if (not _PL.shared_inputs(_root)
                and _PL.YEAR_DIR_RE.match(_root.name)
                and (_sib / name).is_dir()):
            # A year folder beside a folder of exports for every year
            # that does not read it (a hand-made year folder).
            _say_once(("no-dir-shared",), "note",
                      f"../{_PL.INPUTS}/{name}/ "
                      f"is beside this year folder, but this project "
                      f"reads its own {_PL.INPUTS}/",
                      f"To read the exports every year shares, add "
                      f"`{_PL.INPUTS_KEY} = \"../{_PL.INPUTS}\"` to "
                      f"[settings] in taxjson.toml.", prog=_PROG)
        return None

    _required(settings, "base_currency", "country", "year")
    base_currency = settings.base_currency
    country = _normalize_country(settings.country)
    year = settings.year
    # Country-aware default: CRA times dispositions on SETTLEMENT date;
    # the IRS recognizes on the TRADE date. An explicit config wins.
    tax_date = _tax_date(settings)
    acfg = typed_account(acfg, name)
    is_crypto = bool(acfg.crypto)
    is_taxable = acfg.taxable
    include_transfers = bool(acfg.transfers)

    _grouped_raw, _detected = group_inputs_detailed(acct_dir)
    grouped = _split_generic_groups(_grouped_raw)
    if not grouped and not _account_tt_files(acct_dir, cache, name):
        # Say why a folder holding only a positions report has no books.
        _pos_only = [d for d in _detected if d.positions]
        if _pos_only:
            _report_detection(name, _pos_only, cache)
        _details = []
        if _books_left(cache, name):
            # Inputs emptied but artifacts persist: every aggregate
            # keeps counting the account's OLD books with only this
            # line as the signal (2026-09 audit).
            _details.append(
                f"Its work/ artifacts from previous runs STILL EXIST and "
                f"are still counted by sum/fees/reports — delete "
                f"work/{name}_* and reports/{name}* if the account is "
                f"truly gone.")
        # The folder as configured (../inputs/margin with exports shared
        # by every year, never the year folder's own: backlog TJ-0133).
        _say_once(("no-inputs", name),
                  "warning", f"no CSVs or .tt files in "
                  f"{_PL.shown(acct_dir, cache.parent)}; "
                  f"skipping account '{name}'", *_details, prog=_PROG)
        return None
    # Path-selection cross-check (KNOWN_ISSUES "crypto-vs-equity path"):
    # the pipeline split keys on the account's `crypto` flag alone, so a
    # kr_/cb_ file dropped into an equity account was routed through the
    # equity path (no fill-crypto → $0 FMVs) and an equity CSV in a
    # crypto account went through merge (no ticker-map/corp-actions) —
    # both silently. A mismatch is always a config or filing error;
    # refuse loudly.
    _CRYPTO_BROKERS = {"coinbase", "kraken"}
    _mismatched = ({b for b in grouped if b in _CRYPTO_BROKERS}
                   if not is_crypto else
                   {b for b in grouped if b not in _CRYPTO_BROKERS})
    if _mismatched:
        flag = "crypto = true" if is_crypto else "no crypto flag"
        _how = {d.path: d.reason for d in _detected}
        from taxjson.lib.out import one_line as _one_line
        _which = ", ".join(f"inputs/{name}/{_one_line(p.name)} "
                           f"({b}: {_how.get(p, '?')})"
                           for b in sorted(_mismatched)
                           for p in grouped[b])
        _die(f"account '{name}' has {flag} in taxjson.toml but its inputs "
             f"contain {', '.join(sorted(_mismatched))} files",
             f"The {'equity' if is_crypto else 'crypto'} data would be "
             f"routed through the wrong pipeline: {_which}.",
             "Move the files to an account of the matching type, or fix "
             "the account's `crypto` flag.")
    # .tt-only accounts (hand-maintained records — the documented .tt
    # use case) used to be skipped here with a misleading "no CSVs"
    # warning before the .tt conversion stage ever ran: the whole book
    # silently vanished from every report at exit 0 (REVIEW #20).

    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    _kind = (f"{'taxable' if is_taxable else 'sheltered'}"
             f"{', crypto' if is_crypto else ''}")
    if parse_only:
        # Read first so moves between your accounts pair across them.
        _step(f"{name}  ({_kind}, first pass: "
              f"{'sends' if is_crypto else 'transfers'} between your "
              f"accounts)")
    else:
        _step(f"{name}  ({_kind})")
    # How each input CSV was routed: one line per file (owner request —
    # detection must be visible, never a silent file-name guess).
    _report_detection(name, _detected, cache)

    # Parser option (IB foreign ROC): in the manifest so a toggle re-parses.
    _froc_acb = ib_foreign_roc_mode(settings) == "acb"
    from taxjson.lib.country import futures_settle_mode
    _fut_next = futures_settle_mode(settings) == "next_day"
    # Each generic file's mapping, chosen once: the manifest, the
    # fingerprint and the rebuild deps read it.
    _choices = {_c: _mapping_choice(_c) for _c in _generic_files(grouped)}
    # The account's input membership and content stamps.
    _write_source_stamps(name, acct_dir, cache, grouped, settings, _choices,
                         is_crypto, _froc_acb, _fut_next)
    # What the account's stages' inputs resolve against (the stage table),
    # and what their steps read beside them.
    _ctx = ST.StageCtx(work=cache, settings=settings, account=name,
                  crypto=is_crypto, taxable=is_taxable,
                  transfers=include_transfers, grouped=grouped,
                  choices=_choices, ticker_map=ticker_map,
                  overrides=security_overrides, mh=incomplete_history,
                  own=_AccountRun(acfg=acfg, acct_dir=acct_dir, rates=rates,
                                  country=country, base_currency=base_currency,
                                  year=year, tax_date=tax_date, strict=strict,
                                  no_input=no_input, froc_acb=_froc_acb,
                                  fut_next=_fut_next))

    # 1. brokerage parse per broker (the parse row; --strict refuses a
    # parse with events it could not book, cached or not). Questrade
    # last: its internal symbol codes are resolved from the other
    # exports' transfers and names (stage_symbol_codes), this account's
    # included; and a broker that names a listing from the row currency
    # alone (lib/listing_suffix) after the ones that name it: its
    # listings are read from their rows (stage_listing_suffix).
    _codes_state = cache / f"{name}{_symbol_codes_suffix()}"
    if "questrade" not in grouped or is_crypto:
        _codes_state.unlink(missing_ok=True)
    from taxjson.lib import listing_suffix as _LS
    for _b in _LS.CURRENCY_SUFFIX_BROKERS:
        if _b not in grouped or is_crypto:
            _LS.state_path(cache, name, _b).unlink(missing_ok=True)
    _parsed_by: Dict[str, Path] = {
        str(j.broker): ST.output(j)
        for j in ST.execute(_STAGES, _ctx, "parse")}
    # The sources keep the folder's broker order.
    parsed = spellings_checked(cache, name, [_parsed_by[b] for b in grouped])

    # A non-empty export that parsed to 0 transactions (a renamed
    # header, a kr_-named file that is not a Kraken ledger) drops that
    # whole file from the books. Read from the persisted .diag on EVERY
    # run — cached or not — so the console warning and the --strict
    # gate cannot be skipped by a warm cache (R1-247).
    _empty_files: List[str] = []
    for _out in parsed:
        _d = _out.with_name(_out.name + ".diag")
        try:
            _lines = _d.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for _ln in _lines:
            _m = _ZERO_TX_RE.match(_ln.strip())
            if _m:
                _empty_files.append(_m.group(1))
    if _empty_files:
        _which = ", ".join(f"inputs/{name}/{f}" for f in _empty_files)
        if strict:
            _die(f"--strict: {name}: {_which} parsed to 0 transactions",
                 "Every row in it would be missing from the books. Check "
                 "the file's header/format (or its name: cb_/kr_/"
                 "coinbase/kraken route it to a crypto parser), or remove "
                 "it from inputs/.")
        for _f in _empty_files:
            # Once per run: the first pass reads the account too.
            _say_once(("empty", name, _f), "warning",
                      f"{name}: inputs/{name}/{_f} parsed to 0 "
                      f"transactions — NONE of its rows are in the books",
                      "Check the file's header/format (or its name: cb_/"
                      "kr_/coinbase/kraken route it to a crypto parser); "
                      "`run --strict` refuses this.", prog=_PROG)

    if parse_only:
        # The transfer evidence of an export REMOVED from inputs/ must
        # not pair or book anything in this run (re-audit A2-1026; the
        # sweep below removes the rest of its artifacts).
        for _b in ("kraken", "coinbase"):
            _side = cache / f"{name}_{_b}_transfers.json"
            if _b not in grouped and _side.exists():
                _side.unlink()
                _say("note", f"removed stale work/{_side.name} (its input "
                     f"files are gone)", indent="  ", file=sys.stdout)
        return None

    # 1a. Two listings of one security joined by their transfer journal
    # (CA-XLIST-01 / US-XLIST-01): from here on the account's stages
    # read the effective map (ticker.map plus the joins' TOBASE lines).
    if not is_crypto:
        ticker_map = stage_cross_listings(name, settings, cache, ticker_map,
                                          strict=strict)
    elif _dated_effective_lines(cache):
        # A ticker change declared in a .tt file (a coin's new ticker)
        # reaches the crypto books through the effective map too.
        ticker_map = stage_cross_listings(name, settings, cache, ticker_map)
    _ctx.ticker_map = ticker_map

    # 1b. crypto sends: an outgoing transfer that never arrived on
    # another exchange is a gift, a payment, or a move to your own
    # wallet — only the owner knows. Ask at a TTY, then (re)generate
    # inputs/<acct>/crypto_sends.tt BEFORE the .tt stage below reads
    # the account's .tt files, so the decisions book in this run.
    if is_crypto and not include_transfers:
        _stage_crypto_sends(cache.parent, name,
                            interactive=not (no_input
                                             or not sys.stdin.isatty()),
                            strict=strict)
        _write_source_stamps(name, acct_dir, cache, grouped, settings,
                             _choices, is_crypto, _froc_acb, _fut_next)
    _ctx.tt_files = _account_tt_files(acct_dir, cache, name)

    # 2. corp-actions per equity broker. taxjson-corp-actions requires
    # --manifest when multiple CSVs are passed, so always provide one.
    corp_files: List[Path] = []
    corp_brokers = sorted(b for b in grouped if b in CORP_ACTION_BROKERS)
    if not is_crypto and corp_brokers and not _country_has_corp_rules(country):
        # No election rules for this jurisdiction, so the stage below is
        # skipped — but broker parsers (RBC in particular) still SKIP
        # merger/CIL rows on the assumption that corp-actions consumes
        # them. With neither owner, those share movements would silently
        # vanish from the books. Say so loudly.
        _say("warning", f"country={country!r} has no corp-action "
             f"election rules — merger/reorg rows in "
             f"{', '.join(corp_brokers)} files for account {name!r} are "
             f"NOT processed",
             "RBC merger rows are skipped by the parser, so any "
             "reorganization's share movements will be MISSING from this "
             "account's books; record them manually via a .tt file.",
             prog=_PROG)
    if not is_crypto and _country_has_corp_rules(country):
        manifest_path = _resolve_manifest(acct_dir, cache, name, create=True)
        _ctx.manifest = manifest_path
        # The corp row: its UNBOOKED and two-statement ATTENTION lines
        # echoed on every run and refused by --strict.
        corp_files = [ST.output(j)
                      for j in ST.execute(_STAGES, _ctx, "corp")]
        from taxjson.lib.corp_actions import declared_zero_value_ids
        _warn_zero_value_spinoffs(
            name, is_taxable, corp_files, cache,
            declared_zero_value_ids(manifest_path))
        if not is_taxable:
            _note_sheltered_defaults(name, corp_files)

    # 3. starting-position .tt files. Their converted JSON lives in its
    # own `<acct>_tt_<stem>` namespace: `<acct>_<stem>` collided with
    # the broker parse (`questrade.tt` overwrote <acct>_questrade.json,
    # silently dropping every CSV trade) and with pipeline
    # intermediates like <acct>_base.json (R1-116).
    # Two .tt files whose names differ only in the suffix's case
    # (start.tt / start.TT) convert to ONE work file: the second
    # overwrote the first and its trades vanished (re-audit A2-0399).
    _tt_by_out: Dict[str, str] = {}
    for tt in _account_tt_files(acct_dir, cache, name):
        _o = tt_json_path(cache, name, tt.name).name
        if _o in _tt_by_out:
            _die(f"inputs/{name}/{_tt_by_out[_o]} and inputs/{name}/"
                 f"{tt.name} convert to the same work file ({_o})",
                 "One would overwrite the other's rows. Rename one (or "
                 "merge the two).")
        _tt_by_out[_o] = tt.name
    # The tt row: a name a pipeline artifact ends in refused first; a
    # line whose total is not its qty x price +/- fee echoed on every
    # run and refused by --strict.
    _ctx.tt_files = _account_tt_files(acct_dir, cache, name)
    tt_jsons = [ST.output(j) for j in ST.execute(_STAGES, _ctx, "tt")]
    _echo_tt_bare_symbols(name, tt_jsons, parsed)

    # Broker groups REMOVED from inputs/: their parsed JSON, .diag and
    # corp files would otherwise persist forever — stale .diag lines in
    # every .sum, dead fees counted by `taxjson-fees-sum --cache`, dead
    # sources fed to the audit (2026-09 audit).
    _present = {f"{name}_{b}" for b in grouped} \
        | {f"{name}_{b}_corp" for b in grouped} \
        | ({f"{name}_{b}_transfers" for b in grouped}
           if not include_transfers else set()) \
        | {tt_json_path(cache, name, tt.name).stem
           for tt in _account_tt_files(acct_dir, cache, name)} \
        | {f"{name}_tt-journal_transfers"}      # lib/dated_events
    # Prefix-sibling guard: for account `m`, the glob also matches
    # account `m_extra`'s artifacts — deleting those every run
    # destroyed m_extra's parsed books and (worse) its elections
    # manifest, the one artifact documented as NOT rebuildable
    # (2026-09 adversarial audit). Skip any stem that belongs to a
    # LONGER configured account name, and never touch manifests.
    _sibling_prefixes = []
    try:
        for _other in (load_config(cache.parent, missing_ok=True)
                       .get("accounts") or {}):
            if _other != name and _other.startswith(f"{name}_"):
                _sibling_prefixes.append(f"{_other}_")
    except Exception:
        pass
    _ctx.siblings = tuple(_sibling_prefixes)
    for _stale in cache.glob(f"{name}_*.json"):
        _stem = _stale.name[:-len(".json")]
        if _stem in _present:
            continue
        if any(_stale.name.startswith(_pre)
               for _pre in _sibling_prefixes):
            continue
        _sfx = _stale.name[len(name):]
        if any(_sfx.endswith(x) for x in
               ("_base.json", "_gains.json", "_gains_wash.json",
                _GAINS_ALL_SUFFIX,
                "_raw.json", "_raw_base.json", "_raw_gains.json",
                "_raw_base_gains.json", "_merged.json", "_sorted.json",
                "_filled.json", "_mapped.json", "_report.json",
                "_pending_elections.json", "_manifest.json",
                TRANSFER_COSTS_SUFFIX,
                "_blend.diag", OWN_MOVES_SUFFIX)):
            continue
        # A parsed-source artifact with no surviving input group.
        for _victim in (_stale, _stale.with_name(_stale.name + ".diag")):
            if _victim.exists():
                _victim.unlink()
                _say("note", f"removed stale work/{_victim.name} (its "
                     f"input files are gone)", indent="  ",
                     file=sys.stdout)

    sources = tt_jsons + spellings_checked(cache, name, parsed + corp_files)
    # Shares that arrived by transfer from outside the books: booked at
    # the broker's stated book value, or said to have no cost
    # (lib/transfer_in, CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
    if is_taxable and not is_crypto and not include_transfers:
        _tc = stage_transfer_arrivals(name, cache.parent, cache,
                                      tt_jsons, country)
        if _tc is not None:
            sources = sources + [_tc]
        # Gone (a .tt line now covers the bookings): the equity merge2
        # declares the file present or absent, so its going is a change
        # the record sees (the FUZZ #J deletion-blindness class).
    # A US project's moves between your own taxable accounts, written by
    # `tjs run` before the accounts' books (stage_own_account_moves;
    # US-BASIS-05): their TRANSFER legs are part of this account's books.
    _own_moves = cache / f"{name}{OWN_MOVES_SUFFIX}"
    if is_taxable and not include_transfers and _own_moves.is_file():
        sources = sources + [_own_moves]

    # 4. merge → base.json (crypto path differs: needs fill-crypto mid-stream)
    _ctx.own.sources = sources
    base_json = cache / f"{name}_base.json"
    if is_crypto:
        _refuse_crypto_openings(name, tt_jsons, _parsed_by)
        # The crypto chain's rows: merged, sorted, mapped through the
        # ticker.map's GLOBAL/DELETE lines when there is a map, priced
        # (its suspect price ids echoed on every run), converted.
        ST.execute(_STAGES, _ctx, "crypto_merged", "crypto_sorted",
                   "crypto_mapped", "crypto_filled", "crypto_base")
        # The crypto path has no merge2 stage, so it never validates its
        # output: the crypto_validate row runs taxjson-validate and keeps
        # the report as a .diag, so crypto.sum carries an OK:/error line
        # like the merge2-validated equity accounts.
        ST.execute(_STAGES, _ctx, "crypto_validate")
    else:
        # Non-cash fund distributions (reinvested capital-gains dists /
        # late-published ROC factors) never appear in broker CSVs;
        # taxjson.toml's [[distributions]] turn them into ADJUST rows.
        # Taxable books only — sheltered ACB is moot. (taxjson.toml is
        # every stage's implicit dep — needs_rebuild.) The base row: a
        # validation ERROR in its .diag said on every run, refused by
        # --strict.
        _ctx.dists = is_taxable and _config_has_distributions()
        ST.execute(_STAGES, _ctx, "base")

    # Cross-file dedup ambiguities (bin/taxjson_sort.plan_dedup, audit
    # R1-296): echoed from the persisted .diag on EVERY run, cached or
    # not — a row booked once (or twice) on a guess must not scroll by.
    echo_attention_lines(cache / f"{name}_sorted.json" if is_crypto
                         else base_json, prefix="dedup: ")
    # A ticker.map rename to a bare symbol (audit A2-0304).
    echo_attention_lines(base_json, prefix="ticker.map: ")
    # An opening snapshot leaving the account's earlier rows of its
    # symbols out of the books (lib/opening, CA-OPEN-03).
    if not is_crypto:
        echo_attention_lines(base_json, prefix="opening: ")
    # A split booked twice with a rounded ratio (A2-0070): one event, applied
    # once; a late row over-closing a renamed contract (TJ-5008): on the console.
    if not is_crypto:
        echo_attention_lines(base_json, prefix=("split: ", "rename: "))
        _warn_partial_carryovers(name, corp_files, base_json, cache)
        # Opposite US futures positions of two broker accounts (TJ-1265).
        echo_attention_lines(base_json, prefix="futures: ")

    # 5. gains (the gains row: rebuilt when its cost timeline is missing;
    # its ATTENTION lines echoed on every run, a short where none can
    # exist refused by --strict)
    gains_json = cache / f"{name}_gains.json"
    ST.execute(_STAGES, _ctx, "gains")

    # 5b. Raw holdings: merge + sort + dedup, NO currency conversion and
    # NO validation; then gains with no options. The same ticker.map is
    # passed, but with no --to the merge applies only GLOBAL renames and
    # DELETEs — TOBASE consolidations are skipped, so cross-listings like
    # SAMPLM.US / SAMPLM.TO stay separate per actual listing. JOURNAL pairs are
    # netted later, at the export aggregation (post-gains — they can't be
    # merged pre-gains without a mixed-currency ACB pool).
    #
    # Emitted as a TOML holdings handoff. A crypto account's comes from
    # its own books instead (below). The command is returned: after the
    # passes across accounts the run writes the file again with their
    # figures for units moved in from another of your accounts
    # (fill_moved_holdings, backlog TJ-0598).
    holdings_cmd: Optional[List[str]] = None
    if not is_crypto:
        raw_json = cache / f"{name}_raw.json"
        # The raw row's every_run says the notes of its fixes and whether
        # the native books pool mixed currencies (`raw_mixed`): those
        # books are not built then, and an earlier run's are removed.
        ST.execute(_STAGES, _ctx, "raw", "raw_gains", "raw_base",
                   "raw_base_gains")
        if not _ctx.raw_mixed:
            raw_gains = cache / f"{name}_raw_gains.json"
            raw_base_gains = cache / f"{name}_raw_base_gains.json"
            # Machine-readable holdings handoff (TOML) for live-pricing /
            # trading tools. A journal between two listings (Norbert's
            # gambit) moves its units by its transfer legs — the broker's
            # rows or a .tt JOURNAL line's (lib/dated_events); a legacy
            # ticker.map JOURNAL line no longer nets the listings here.
            # --futures: include futures positions (taxjson-export excludes
            # them by default). A holdings handoff should reflect everything
            # actually held, futures included.
            export_cmd = _cmd("taxjson-export") + [
                "--holdings-toml", "--account-name", name, "--futures",
                "--country", country,
            ]
            if ticker_map:
                # (No --transfer-evidence: the journals between listings
                # are in the native books already, _raw_book_journals.)
                export_cmd += ["--map", str(ticker_map)]
            export_cmd += ["--base-gains", str(raw_base_gains),
                           "--base-currency", base_currency,
                           "--trades", str(raw_json)]
            export_cmd.append(str(raw_gains))
            holdings_cmd = export_cmd
    else:
        # A crypto account's holdings come from its own books: the raw
        # pass above has no crypto price fill, so a coin-for-coin swap
        # would have no cost there, and a coin has no other listing to
        # keep apart. Its figures are the account's own, in the base
        # currency, before the passes across accounts. --dust-threshold:
        # a coin's real amount is often under the 0.001 default; under a
        # millionth of a unit costing under 1 is an exchange's rounding
        # residue (a sale of "all" leaving 1e-07), left out and said.
        # --symbol-suffix: the exchange's venue suffix (BTC.KR), the form
        # its own holdings snapshot uses (backlog TJ-0418). Several
        # exchanges in the folder (--venue-book): each coin its own
        # venue's, bare when they share it (backlog TJ-0603).
        from taxjson.lib.markets import crypto_venue_suffix
        _sfx = {crypto_venue_suffix(b) or "" for b in grouped}
        _venues: List[str] = []
        if len(grouped) > 1 and len(_sfx) > 1:
            for _b in grouped:
                for _p in (cache / f"{name}_{_b}.json",
                           cache / f"{name}_{_b}_transfers.json"):
                    if _p.is_file():
                        _venues += ["--venue-book", f"{_b}={_p}"]
        holdings_cmd = _cmd("taxjson-export") + [
            "--holdings-toml", "--crypto", "--account-name", name,
            "--country", country, "--dust-threshold", "1e-6"] + (
            _venues if _venues else
            ["--symbol-suffix", next(iter(_sfx))]
            if len(_sfx) == 1 and next(iter(_sfx)) else []) + [
            "--base-gains", str(gains_json),
            "--base-currency", base_currency,
            "--trades", str(base_json), str(gains_json)]

    # 6. the renders (TJ-1828): the holdings handoff and the summary, each
    # built and its step said every run. The report
    # model is rendered once the passes across accounts ran (stage_report).
    _ctx.own.holdings_cmd = holdings_cmd
    ST.execute(_STAGES, _ctx, "crypto_holdings" if is_crypto else "holdings",
               "sum")
    sum_path = reports_dir / f"{name}.sum"

    return {"base": base_json, "gains": gains_json, "sum": sum_path,
            "holdings_cmd": holdings_cmd, "stage_ctx": _ctx}


# ---------------------------------------------------------------- the account's stage steps
# The steps of the stage table's rates row and stage_account's rows
# (pipeline/stage_table, bound below in _STAGES): `execute` asks over the
# inputs a row declares, then calls them. A step reads the instance's StageCtx
# and, through `own`, the account's run (_AccountRun).

@dataclass
class _AccountRun:
    """What an account's stage steps read beyond the stage table's StageCtx: the
    account's settings and the run's options, and what stage_account finds
    as it goes."""
    acfg: Any
    acct_dir: Path
    rates: Path
    country: str
    base_currency: str
    year: Any
    tax_date: str
    strict: bool
    no_input: bool
    froc_acb: bool
    fut_next: bool
    corp_said: bool = False             # "Processing corporate actions" shown
    # the books' sources: the .tt files', the parses', the corporate
    # actions', the arrivals' and the moves', in that order
    sources: List[Path] = field(default_factory=list)
    # the renders: the holdings handoff's command, whether this run built
    # it (its changes since the last one are said), the validation's
    # (exit status, report) when it ran
    holdings_cmd: Optional[List[str]] = None
    holdings_built: bool = False
    validated: Optional[Tuple[int, str]] = None
    # asked again after the passes across accounts (stage_moved_holdings),
    # and whether they fill this account's units moved in (TJ-0598)
    after_passes: bool = False
    fills: bool = False


def _rates_build(i: ST.Inst, _r: ST.Ready) -> bool:
    """The rates row (stage_currency_rates): each source currency's rates
    into to_base.csv, and the window they start at stamped. False: a file
    built with no window, or kept after a refresh failed (unmarked here),
    is not vouched for on the row's own grounds."""
    c = i.ctx
    rates_path = ST.output(i)
    base, start = c.settings.base_currency, c.window
    had_previous = rates_path.exists() and rates_path.stat().st_size > 0
    parts: List[bytes] = []
    from taxjson.lib.offline import offline_enabled
    # Offline the helper reads its cache only: no download to announce.
    _fetch = ("Loading cached" if offline_enabled() else "Downloading")
    try:
        for src in c.currencies:
            _step(f"{_fetch} {src} → {base} rates")
            parts.append(run_capture(
                _cmd("taxjson-to-base-curr") + [src, base]
                + (["--start", start] if start else [])))
    except Exception as exc:
        # A refresh attempt (e.g. offline, yfinance hiccup) must not turn a
        # usable-if-aging rates file into a hard failure — but say so
        # loudly: rows past the file's coverage convert at --default-rate.
        if had_previous:
            _say("warning", f"FX rate refresh failed ({exc}); keeping the "
                 f"existing to_base.csv",
                 "Transactions dated after its coverage (beyond its last "
                 "rate and the look-back) have no rate and stop the "
                 "conversion — re-run online to refresh.", prog=_PROG)
            unmark(rates_path)        # the old file is not vouched for
            return False
        raise
    # Atomic: to_base.csv is mtime-cached, so a kill mid-write must never
    # leave a fresh-looking empty/partial rates file (every later run would
    # silently fall back to the default FX rate).
    from taxjson.lib.safe_write import write_atomic
    write_atomic(rates_path, b"".join(parts))
    stamp = c.work / RATES_START_STAMP
    if start:
        _write_work_stamp(stamp, start + "\n")
        return True
    _drop_work_file(stamp)
    return False


def _parse_prepare(i: ST.Inst) -> List[str]:
    """The parse's stamps, each rewritten only when it changes (the ask
    declares them), and the parser's arguments naming them."""
    from taxjson.lib import listing_suffix as _LS
    c, broker = i.ctx, i.broker or ""
    name, cache, csvs = c.account, c.work, list(c.grouped[broker])
    args: List[str] = []
    if broker == "questrade" and not c.crypto:
        # Rewritten only when the inference changes: a dep.
        codes = cache / f"{name}{_symbol_codes_suffix()}"
        stage_symbol_codes(name, csvs, cache.parent, cache, codes)
        args = ["--symbol-codes", str(codes)]
    if broker == "ib" and not c.crypto:
        # The project's other IB accounts: a one-contract-id ticker
        # change is dated from all of them (review 2 #4); their
        # statements are deps, and the .tt lines it reads (TJ-4129).
        _ibp, _ibp_deps = stage_ib_project(name, cache.parent, cache)
        _ib_tt_state(name, csvs, cache)
        if _ibp_deps:
            args += ["--project-statements", str(_ibp)]
    if broker in _LOOKALIKE_RENAME_BROKERS and not c.crypto:
        # A look-alike ticker change a .tt RENAME line already
        # declares is a note, not an ATTENTION (a dep, rewritten
        # only when the lines change).
        args += ["--declared-renames", str(stage_declared_renames(cache))]
    if broker in _LS.CURRENCY_SUFFIX_BROKERS and not c.crypto:
        # The listings read from the evidence (a dep, rewritten only
        # when they change).
        args += ["--listing-fixes",
                 str(stage_listing_suffix(name, broker, csvs, cache.parent, cache))]
    return args


def _parse_build(i: ST.Inst, r: ST.Ready) -> None:
    """The parse of one broker group (taxjson-brokerage)."""
    c, a, broker = i.ctx, i.ctx.own, i.broker or ""
    name, csvs, out = c.account, c.grouped[broker], ST.output(i)
    ticker_map, security_overrides = c.ticker_map, c.overrides
    _rin = _parse_rates(broker, a.rates)     # [] or [the rates file]
    # --strict: a schema ERROR (negative trade net, zero split
    # ratio, a notional that contradicts the row's declared
    # contract multiplier ...) stops the run instead of
    # scrolling past as a warning into the filed numbers.
    cmd = _cmd("taxjson-brokerage") + ["--account", name,
                                       "--brokerage",
                                       "generic"
                                       if _is_generic_group(broker)
                                       else broker,
                                       "--strict",
                                       "--account-type",
                                       "taxable" if c.taxable
                                       else "sheltered"]
    cmd += _brokerage_account_flags(a.acfg)
    # Always explicit: the parser's own default is the neutral
    # cost reduction; s.90(1) is the Canadian project's choice
    # (partition INPUTS-03).
    cmd += ["--country", a.country, "--foreign-roc",
            "acb" if a.froc_acb else "dividend"]
    if a.fut_next:
        cmd += ["--futures-settle", "next_day"]
    if isinstance(a.year, int):
        # An export's own timestamp vs the year (RBC "as of",
        # S063-22).
        cmd += ["--tax-year", str(a.year)]
    if ticker_map:
        cmd += ["--ticker-map", str(ticker_map)]
    cmd += r.prep
    if _rin:       # the de-peg check (A2-0590), Kraken's leg split
        cmd += ["--rates", str(_rin[0])]
    _sidecar = out.with_name(out.stem + "_transfers.json")
    if c.transfers:
        cmd.append("--transfers")
    else:
        # Custody evidence sidecar: excluded TRANSFER rows are
        # kept queryable (`tjs transfers`) instead of
        # silently deleted — a depot flip or broker migration
        # is exactly what explains a confusing position later.
        cmd += ["--transfers-out", str(_sidecar)]
    if security_overrides:
        cmd += ["--security-overrides", str(security_overrides),
                "--override-log",
                str(out.with_name(out.stem + ".overrides"))]
    if c.taxable:
        # The cash events of each export (lib/cash_events `cash_sidecar`):
        # what `tjs cash`, `tjs fx-cash --ledger v2` and `tjs t1135` read,
        # never the exports again (PLAN step 16 commit 6).
        from taxjson.lib.cash_events import sidecar_path
        cmd += ["--cash-out", str(sidecar_path(out))]
    cmd += [str(p) for p in csvs]
    # The first pass (transfers / sends between your accounts)
    # parsed this account in this run: the same command over
    # the same bytes is the same parse — taken as it is, its
    # messages already shown under the first pass.
    src_manifest = c.work / f"{name}_sources.list"
    _reads = _parse_reads(
        cmd, a.acct_dir,
        [d for d in r.inputs if d != src_manifest]
        + ([security_overrides] if security_overrides else []))
    if _reused_parse(out, _reads):
        return
    from taxjson.lib.brokerages.base import DISPLAY_NAMES
    _step(f"Reading {len(csvs)} "
          + (f"{DISPLAY_NAMES.get(broker, broker)} "
             if len(c.grouped) > 1 else "")
          + ("file" if len(csvs) == 1 else "files"))
    if c.transfers:
        # transfers=false -> true toggle: a stale sidecar
        # would be consumed ALONGSIDE the now-in-book rows
        # (double-counted by the transfers view and the
        # holdings evidence flips) — round-five audit.
        _sidecar.unlink(missing_ok=True)
    if not security_overrides:
        out.with_name(out.stem + ".overrides").unlink(
            missing_ok=True)
    run_state.current.parsed.pop(out, None)
    run_to_file(cmd, out)
    run_state.current.parsed[out] = (_reads, _parse_outputs(out))
    # Surface per-file transaction counts (and any 0-tx
    # warnings) inline so the user can sanity-check at a
    # glance that each CSV contributed the expected number
    # of rows.
    echo_parse_stats(out, csvs)


def _parse_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    # `run --strict --fast` on a cached parse must hit the same gate.
    out = ST.output(i)
    if i.ctx.own.strict and unbooked_lines(out):
        _die(f"--strict: {i.ctx.account}: {i.broker} input has event(s) the "
             f"parser could not book — aborting",
             f"See the UNBOOKED warning above (also in "
             f"work/{out.name}.diag).")


def _corp_build(i: ST.Inst, _r: ST.Ready) -> None:
    """The corporate actions of one equity broker group
    (taxjson-corp-actions), its elections asked or left pending."""
    c, a, broker = i.ctx, i.ctx.own, i.broker or ""
    name, cache, csvs, out = c.account, c.work, c.grouped[broker], ST.output(i)
    manifest_path, ticker_map = c.manifest, c.ticker_map
    assert manifest_path is not None
    _codes_state = cache / f"{name}{_symbol_codes_suffix()}"
    _corp_codes = (_codes_state if broker == "questrade"
                   and _codes_state.is_file() else None)
    if not a.corp_said:
        _step("Processing corporate actions")
        a.corp_said = True
    cmd = _cmd("taxjson-corp-actions") + [
        "--account-name", name,
        "--country", a.country,
        "--brokerage", broker,
        "--manifest", str(manifest_path),
        # Named as `tjs elect` names it (../inputs/...
        # for shared exports), never by its absolute path
        # (backlog TJ-0216).
        "--manifest-shown",
        _manifest_shown(manifest_path, cache.parent),
    ] + [
        # The outputs the run checked, its spellings checked against
        # them before an election is read or saved (TJ-4616).
        _a for _acct, _p in _checked_outputs(cache)
        for _a in ("--spellings-from", f"{_acct}={_p}")] + (
        # Shared exports: only an answer typed at the prompt
        # is saved there, never a rekey or an auto-election
        # as a side effect of this year's run.
        ["--decisions-only"] if _PL.shared_inputs(cache.parent)
        else []) + [
        "--rates", str(a.rates),
        "--base-currency", a.base_currency,
    ] + (["--ticker-map", str(ticker_map)] if ticker_map
         else []) + (["--symbol-codes", str(_corp_codes)]
                     if _corp_codes else []) + [
        _a for _t in c.tt_files
        for _a in ("--tt", str(_t))] + (
        # A sheltered account's spin-off / merger is booked
        # without asking unless [settings]
        # sheltered_elections = "ask" (CA-CORP-11 /
        # US-CORP-12); asked, the prompt says no tax depends
        # on it.
        [] if c.taxable else [
            "--sheltered", "--sheltered-elections",
            _sheltered_elections(c.settings)])
    # Interactive by default: corp-actions prompts for the tax
    # election (taxable vs rollover) on stderr and reads the
    # answer from stdin. Without a TTY (or with --no-input) it
    # writes the pending elections as JSON and exits 3 — the
    # GUI/headless path: resolve via `tjs elect --set`,
    # re-run.
    non_interactive = a.no_input or not sys.stdin.isatty()
    pending_path = cache / f"{name}_pending_elections.json"
    if non_interactive:
        # The tool sees the same stdin: --no-input only when
        # it was given, so its message says which it was
        # (a flag, or no terminal; 2026-10 workflow audit).
        # Its note names the file from the project, as the
        # run names its work/ files (backlog TJ-0602).
        cmd += ["--pending-json", str(pending_path),
                "--pending-json-shown",
                _out_relpath(pending_path, cache.parent)]
    if a.no_input:
        cmd += ["--no-input"]
    cmd += [str(p) for p in csvs]
    pending_path.unlink(missing_ok=True)   # stale from last run
    try:
        run_to_file(cmd, out,
                    interactive=not non_interactive)
    except subprocess.CalledProcessError as e:
        if e.returncode == 3 and pending_path.exists():
            raise PendingElectionsError(name, pending_path)
        raise


def _corp_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    c, broker, out = i.ctx, i.broker, ST.output(i)
    name, strict = c.account, c.own.strict
    # ticker.map EXTRACT lines renamed this broker's
    # trade rows; the corp-action rows of the same security must
    # follow, or a merger consumed an empty un-overridden pool
    # while the real position stayed put (S004-00).
    _apply_override_log(out, c.work / f"{name}_{broker}.overrides",
                        name)
    # The corp stage's own UNBOOKED lines (a Questrade DIS chain
    # that nets a removal, a lapse it cannot book) follow the
    # parse stage's contract: echoed on every run, refused by
    # --strict. They used to reach only the .diag and the .sum
    # (A2-0211, the S015-02 twin).
    for _ln in unbooked_lines(out):
        _echo_captured(_ln)
    if strict and unbooked_lines(out):
        _die(f"--strict: {name}: {broker} corporate actions hold "
             f"event(s) taxjson could not book — aborting",
             f"See the UNBOOKED warning above (also in "
             f"work/{out.name}.diag).")
    # Two statements of one broker account that disagree on an
    # event (lib/corp_actions combine_broker_copies): the copy
    # kept sets the gain. They reached only the .diag and the
    # .sum (2026-10 critique: a blocker must not stay in a
    # stage log): echoed, and refused by --strict.
    _conflicts = attention_lines(out)
    for _ln in _conflicts:
        _echo_captured(_ln)
    if strict and _conflicts:
        _die(f"--strict: {name}: two {broker} statements disagree "
             f"on a corporate action — aborting",
             f"See the ATTENTION warning above (also in "
             f"work/{out.name}.diag); keep only the current "
             f"statement in the account folder.")


def _tt_prepare(i: ST.Inst) -> None:
    tt, name = i.tt, i.ctx.account
    assert tt is not None
    from taxjson.lib.config_check import RESERVED_NAME_SUFFIXES
    _stem = tt.stem.lower()
    _clash = next((x for x in RESERVED_NAME_SUFFIXES
                   if _stem.endswith(x)), None)
    if _clash:
        # work/<acct>_tt_msft_gains.json read as the gains book of a
        # spurious account "<acct>_tt_msft" in `sum`, its fees counted
        # twice (S037-15): the artifact roles come from file names.
        _die(f"inputs/{name}/{tt.name}: a .tt file name may not end "
             f"in {_clash!r}",
             "Its converted JSON would read as a pipeline artifact (a "
             "spurious extra account in the reports). Rename it, e.g. "
             f"{tt.stem[:-len(_clash)] + _clash.replace('_', '-')}.tt")


def _tt_build(i: ST.Inst, _r: ST.Ready) -> None:
    tt = i.tt
    assert tt is not None
    # The name as the .tt warnings name it (_echo_tt_totals).
    from taxjson.lib.brokerages.base import shown_name as _shown
    _step(f"Reading {_shown(tt)}")
    run_to_file(_cmd("taxjson-convert-tt") + ["--account-name", i.ctx.account]
                + ([] if i.ctx.crypto else ["--equity"]) + [str(tt)],
                ST.output(i))


def _tt_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    # A line whose total is not its qty x price +/- fee: the total is
    # booked as written. Read from the persisted .diag on EVERY run,
    # cached or not (as the 0-transaction check), shown on the
    # console and refused by --strict (QA F2: the warning reached only
    # the .sum DIAGNOSTICS).
    assert i.tt is not None
    _echo_tt_totals(i.ctx.account, i.tt, ST.output(i), i.ctx.own.strict)


def _crypto_merged_build(i: ST.Inst, _r: ST.Ready) -> None:
    _step("Merging the exports")
    run_to_file(_cmd("taxjson-merge") + [str(p) for p in i.ctx.own.sources],
                ST.output(i))


def _crypto_sorted_build(i: ST.Inst, _r: ST.Ready) -> None:
    _step("Sorting, de-duplicating")
    run_to_file(_cmd("taxjson-sort") + [
        "--dedup", str(ST.output(ST.Inst("crypto_merged", i.ctx)))], ST.output(i))


def _crypto_mapped_build(i: ST.Inst, _r: ST.Ready) -> None:
    # ticker.map GLOBAL/DELETE apply to crypto books too — the
    # README's contract is "every stage", but this path has no
    # merge2, so a Coinbase ETH2→ETH consolidation silently split
    # pools (2026-09 audit). Mapping runs BEFORE the price fill:
    # FMV lookups keyed by a dead alias (ETH2) either fetched the
    # wrong asset's price or left $0 basis (adversarial audit).
    # TOBASE/JOURNAL stay equity-only.
    _step("Mapping tickers")
    run_to_file(_cmd("taxjson-ticker-map") + [
        str(ST.output(ST.Inst("crypto_sorted", i.ctx))),
        "--map", str(i.ctx.ticker_map),
        "--global-only", "--kind", "crypto",
        "--country", i.ctx.own.country], ST.output(i))


def _crypto_priced(i: ST.Inst) -> Path:
    """The book fill-crypto prices: the mapped one, or the sorted one when
    there is no map."""
    return ST.output(ST.Inst("crypto_mapped" if i.ctx.ticker_map
                             else "crypto_sorted", i.ctx))


def _crypto_filled_prepare(i: ST.Inst) -> None:
    """The map's CRYPTO lines' stamp, the price fill's input (and a mapped
    book a run with a map left, gone without one)."""
    c = i.ctx
    if not c.ticker_map:
        _old = ST.output(ST.Inst("crypto_mapped", c))
        if _old.exists():
            _old.unlink()
    mapped = _crypto_priced(i)
    # The project ticker.map's CRYPTO lines (README) are resolved
    # from the project, never the cwd: `-C <proj>` from anywhere
    # used to ignore them (and a map in the cwd leaked into other
    # projects). Their state (content, or absence) is a rebuild dep
    # through a stamp that changes only when they do, so `run
    # --fast` re-prices after a CRYPTO line is added, edited, or
    # deleted. fill-crypto also reads a ticker.map in its input
    # file's folder (work/), later wins: that one is part of the
    # stamp too (re-audit A2-0585).
    from taxjson.lib.ticker_map import side_rules_in as _side_in
    _cparts = []
    for _cdir in (c.work.parent, mapped.parent):
        try:
            _crules = sorted(_side_in([_cdir]).crypto.items())
            _cparts.append(("sha256:" + hashlib.sha256(
                repr(_crules).encode()).hexdigest()) if _crules
                else "absent")
        except (OSError, ValueError):
            _cparts.append("unreadable")
    _cstate = (_cparts[0] if _cparts[1] == "absent"
               else " ".join(_cparts))
    _cstamp = ST.output(ST.Inst("crypto_map", c))
    if (_read_work_stamp(_cstamp) or "").strip() != _cstate:
        _write_work_stamp(_cstamp, _cstate + "\n")


def _crypto_filled_build(i: ST.Inst, _r: ST.Ready) -> None:
    _step("Looking up crypto prices")
    run_to_file(_cmd("taxjson-fill-crypto") + [
        "--project-root", str(i.ctx.work.parent), str(_crypto_priced(i))],
        ST.output(i))


def _crypto_filled_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    # A coin priced under an id that looks wrong (no CRYPTO line
    # while the price cache holds a numbered id of the ticker, or
    # Yahoo closes far off the coin's own trade prices): console,
    # every run (from the persisted .diag).
    from taxjson.bin.fill_crypto_prices import (
        ATTENTION_CRYPTO_ID as _ATT_CID)
    echo_attention_lines(ST.output(i),
                         prefix=_ATT_CID[len(ATTENTION_PREFIX) + 1:])


def _crypto_base_build(i: ST.Inst, _r: ST.Ready) -> None:
    a = i.ctx.own
    _step(f"Converting currency to {a.base_currency}")
    run_to_file(_cmd("taxjson-convert-currency") + [
        str(ST.output(ST.Inst("crypto_filled", i.ctx))), "--to", a.base_currency,
        "--rates", str(a.rates), "--country", a.country,
    ], ST.output(i))


def _mh_prepare(i: ST.Inst) -> None:
    """The missing history's stamp, rewritten when its lines change
    (_missing_history_deps), before a stage that declares it asks."""
    _missing_history_deps(i.ctx.mh)


def _base_prepare(i: ST.Inst) -> None:
    if i.ctx.dists:
        _mh_prepare(i)


def _base_build(i: ST.Inst, _r: ST.Ready) -> None:
    """The equity merge (taxjson-merge2), and the [[distributions]] applied
    to it in one atomic publish."""
    c, a = i.ctx, i.ctx.own
    name, ticker_map, base_json = c.account, c.ticker_map, ST.output(i)
    cmd = _cmd("taxjson-merge2") + [
        "--sort", "--dedup", "--require-inputs",
        "--to", a.base_currency, "--rates", str(a.rates), "--validate",
        "--country", a.country,
    ]
    if a.year:
        # An opening snapshot that would leave out a sale of the
        # tax year is refused (lib/opening, CA-OPEN-03), dated on
        # the project's tax_date basis (backlog TJ-0961).
        cmd += ["--year", str(a.year), "--tax-date", a.tax_date]
    if ticker_map:
        cmd += ["--map", str(ticker_map)]
    cmd += [str(p) for p in a.sources]
    _step("Sorting, de-duplicating, mapping tickers, converting "
          "currency")
    if not c.dists:
        run_to_file(cmd, base_json)
        return
    # ONE atomic publish for merge2 + apply-distributions:
    # publishing merge2's output first and adjusting
    # in-place meant a failed/interrupted apply left a
    # fresh-but-unadjusted base that every later --fast
    # run trusted — the ADJUST rows silently vanished
    # until an unrelated rebuild (2026-09 audit).
    _stage = base_json.with_name(base_json.name + ".stage")
    try:
        run_to_file(cmd, _stage)
        _step("Applying [[distributions]] from taxjson.toml")
        from taxjson.pipeline.dispatch import run_cmd as _run_cmd_d
        _dres = _run_cmd_d(
            _cmd("taxjson-apply-distributions") + [
                str(_stage), "--config", str(run_state.config_path),
                "--account", name,
                # Record-date balance = holder of record,
                # i.e. the SETTLED position in BOTH
                # countries: a market fact, not the tax-
                # year date basis (a US project's trade
                # tax_date credited a buy traded on the
                # record date — partition INPUTS-10).
                "--date-basis", "settle",
                "--country", a.country]
            # Keys go through the same ticker.map renames
            # as the book (S025-22).
            + (["--ticker-map", str(ticker_map)]
               if ticker_map else [])
            # Size record-date balances WITH the
            # missing-history openings the gains stage synthesizes (S000-08).
            + (["--incomplete-history",
                str(c.mh)]
               if c.mh else []),
            capture_output=True)
        if _dres.stderr:
            _echo_stage_stderr(_dres.stderr)
        if _dres.returncode != 0:
            sys.exit(exit_text(f"tjs run: apply-distributions "
                               f"failed for {name} (see above)."))
        _stage.replace(base_json)
        # Publish the stage's diagnostics under the name
        # every reader uses — the --strict gate and the
        # .sum banner read <name>_base.json.diag, and a
        # validation ERROR hidden in the .stage.diag
        # sailed past --strict (2026-09 audit).
        _sdiag = _stage.with_name(_stage.name + ".diag")
        _bdiag = base_json.with_name(base_json.name
                                     + ".diag")
        if _sdiag.exists():
            _sdiag.replace(_bdiag)
        else:
            _bdiag.unlink(missing_ok=True)
    finally:
        _stage.unlink(missing_ok=True)
        _stage.with_name(_stage.name + ".diag").unlink(
            missing_ok=True)


def _base_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    # Hard validation ERRORs must be VISIBLE, not archaeology: they
    # previously lived only in the raw .diag (FUZZ #M). This check
    # runs OUTSIDE the rebuild branch on purpose — the persisted
    # .diag describes the cached books too, and gating it on a
    # rebuild let `run --strict --fast` on unchanged-but-invalid
    # books skip the gate entirely and publish reports at exit 0
    # (the crypto path's unconditional validate never had the
    # hole). The warn-only default still completes the run.
    name, base_json = i.ctx.account, ST.output(i)
    _diag = base_json.with_name(base_json.name + ".diag")
    if _diag.exists():
        _dtxt = _diag.read_text(errors="replace")
        _m = re.search(r"validation: (\d+) error", _dtxt)
        if _m and int(_m.group(1)) > 0:
            if i.ctx.own.strict:
                # --strict (KNOWN_ISSUES "per-account validation is
                # non-fatal"): a CI/cron run must not publish a
                # wrong .sum at exit 0.
                _die(f"--strict: {name}: {_m.group(1)} validation "
                     f"ERROR(s) in the merged books — aborting",
                     f"Details: work/{_diag.name}.")
            _say("warning", f"{name}: {_m.group(1)} validation "
                 f"ERROR(s) in the merged books — numbers may be "
                 f"wrong", f"Details: reports/{name}.sum DIAGNOSTICS "
                 f"(or work/{_diag.name}).", indent="  ")


def _gains_prepare(i: ST.Inst) -> List[str]:
    """The gains run's command (said first: the crypto wash note), and the
    missing history's stamp."""
    c, a = i.ctx, i.ctx.own
    cache, settings, gains_json = c.work, c.settings, ST.output(i)
    traces = cache / f"{c.account}_gains.traces"
    cmd = _cmd("taxjson-gains") + [
        "--country", a.country, "--year", str(a.year),
        "--tax-date", a.tax_date,
        "--full-traces", str(traces),
    ]
    wash_flags = _wash_flags(c.taxable, c.crypto, a.country)
    cmd += wash_flags
    if "--no-wash" in wash_flags:
        # A rule of the run, not of the account: said once.
        _say_once("no-crypto-wash", "note",
                  "wash-sale rule NOT applied to crypto",
                  "The IRS treats crypto as property, not a security "
                  "(§1091 does not reach it); losses are allowed in full.",
                  indent="  ", file=sys.stdout,
                  short="wash-sale rule NOT applied to crypto (property, "
                        "not a security): losses allowed in full")
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    cmd += _locked_year_flags(cache.parent, settings)
    cmd += _loss_override_flags(cache)
    if c.crypto:
        # Spot coins cannot be short: a sale with nothing held is
        # missing history, said as ATTENTION (re-audit A2-0137).
        cmd.append("--spot-crypto")
    # Project-wide missing-history opening balances (from
    # `find-missing-history --write-missing-history`). load_missing_history filters by (symbol, account), so passing
    # the whole file to every account's gains run is safe — non-matching pairs
    # are ignored. A rebuild dependency so editing the file re-runs gains.
    _mh_prepare(i)
    if c.mh is not None:
        cmd += ["--incomplete-history", str(c.mh)]
    # The pools per day beside the gains (`list --date`; backlog TJ-0417).
    cmd += ["--cost-timeline", str(timeline_beside(gains_json))]
    cmd.append(str(cache / f"{c.account}_base.json"))
    return cmd


def _gains_build(i: ST.Inst, r: ST.Ready) -> None:
    _step("Calculating capital gains")
    run_to_file(r.prep, ST.output(i))


def _gains_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    c, a = i.ctx, i.ctx.own
    name, gains_json = c.account, ST.output(i)
    # The gains stage's ATTENTION lines, from the persisted .diag on
    # EVERY run, cached or not: a broker-coded CLOSING row the books
    # cannot back (audit A2-0006), an unapplied basis adjustment
    # (A2-0199), income a record date moves across a year end (A2-0073,
    # A2-0229), a missing-history line on a real short or a written
    # option (A2-0637 / A2-0639). One call covers all — but the positions
    # that go short: the run lists those once, after every account's
    # books, the ones that bear on the tax year one by one and the rest
    # in one line (_report_short_positions).
    echo_attention_lines(gains_json, exclude="short: ", once=True)
    run_state.current.short_sources.append(
        (name, c.work / f"{name}_base.json", gains_json,
         c.taxable and not c.crypto))
    # A short where none can exist (a registered account, spot crypto,
    # a sale the broker codes CLOSING): missing history the numbers
    # depend on — --strict refuses (re-audit A2-0395 / A2-0137 / A2-1223).
    _shorts = _attention_short_lines(gains_json)
    if _shorts and a.strict:
        echo_attention_lines(gains_json, prefix="short: ", once=True)
        _die(f"--strict: {name}: {len(_shorts)} position(s) go short where "
             f"none can exist — history is missing",
             "A registered account, spot crypto, or a sale the broker "
             "codes closing: see the Short position warnings above. Spot "
             "crypto and registered accounts cannot be short.")
    if c.taxable:
        _warn_expired_open_options(name, gains_json, c.work, a.year,
                                   tax_date=_tax_date_basis(c.settings))


def _raw_build(i: ST.Inst, _r: ST.Ready) -> None:
    """The native books: merge2 with no currency conversion and no
    validation (stage_account 5b)."""
    c, a = i.ctx, i.ctx.own
    raw_cmd = _cmd("taxjson-merge2") + ["--sort", "--dedup",
                                         "--country", a.country]
    if c.ticker_map:
        raw_cmd += ["--map", str(c.ticker_map)]
    raw_cmd += [str(p) for p in a.sources]
    run_to_file(raw_cmd, ST.output(i), capture_diag=False)


def _raw_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    c, a = i.ctx, i.ctx.own
    name, cache, raw_json = c.account, c.work, ST.output(i)
    # Idempotent: rewrites only rows still in another currency (also
    # a raw merge an earlier version left behind).
    for _ln in _raw_align_adjust_currency(raw_json, a.rates,
                                          a.base_currency):
        _say("note", _ln, indent="  ")
    # A withholding in another currency than its dividend, netted
    # at its own day's rate (backlog TJ-0618).
    for _ln in _raw_net_withholding(raw_json, a.rates, a.base_currency):
        _say("note", _ln, indent="  ")
    # Journals between two listings, booked on their date with
    # their cost (lib/listing_journals, backlog TJ-0361).
    for _ln in _raw_book_journals(
            raw_json, _account_sidecars(cache, name, c.siblings),
            c.ticker_map, a.rates, a.base_currency):
        _say("note", _ln, indent="  ")
    mixed = _raw_mixed_currency_symbols(raw_json)
    c.raw_mixed = bool(mixed)
    if not mixed:
        return
    # Unrepresentable in a native-currency book (cross-currency
    # rollover rename, or a foreign-currency cost adjustment with
    # no rate on file): skip the raw view rather than kill the
    # run. The converted books, gains, wash and .sum reports
    # above are complete and authoritative.
    _say("warning", f"raw holdings skipped for '{name}': "
         f"{', '.join(mixed)} would pool mixed currencies",
         "A cross-currency rollover rename, or a cost adjustment "
         "or corporate-action row in another currency with no "
         f"exchange rate on file. reports/{name}_holdings.toml "
         "was NOT refreshed this run.", indent="  ")
    # The native books of an EARLIER run (before the rollover
    # rows arrived) would otherwise keep serving `tjs gains`
    # without the rolled-over disposition (2026-09 audit S037-23).
    for _stale in (cache / f"{name}_raw_gains.json",
                   cache / f"{name}_raw_base.json",
                   cache / f"{name}_raw_base_gains.json"):
        if _stale.exists():
            _stale.unlink()
            _say("note", f"removed stale work/{_stale.name}",
                 indent="  ")


def _native_gains_build(i: ST.Inst, _r: ST.Ready) -> None:
    """The native books' gains (raw_gains), or their base-currency twin's
    (raw_base_gains): no options."""
    c, a = i.ctx, i.ctx.own
    book = ST.output(ST.Inst("raw" if i.row == "raw_gains" else "raw_base", c))
    # The missing-history lines apply to the native books too:
    # without it holdings.toml listed
    # every listed pair as a SHORT, and `tjs gains` showed a
    # tainted sale as a realized gain (audit A2-0111 / A2-0305,
    # R1-275 / R1-322). A pair spelled with a TOBASE target the
    # native books do not use simply has no rows here.
    # (--native-books: the lines' symbols mapped as these rows
    # were — GLOBAL renames only, issue #23.)
    _ph_raw = (["--incomplete-history", str(c.mh), "--native-books"]
               if c.mh is not None else [])
    # Match the country to the rest of the pipeline (it is
    # required; it used to default to Canada, and the US
    # raw-holdings inventory would aggregate FIFO lots as a
    # Canadian-style ACB blended pool — wrong total_cost per
    # symbol on the holdings.toml handoff.
    run_to_file(_cmd("taxjson-gains") + [
        "--country", a.country,
    ] + option_timing_flags(c.settings)
        + income_dating_flags(c.settings) + _ph_raw
        + [str(book)],
                ST.output(i), capture_diag=False)


def _raw_base_build(i: ST.Inst, _r: ST.Ready) -> None:
    # Base-currency companion: convert the SAME raw merge to the base
    # currency (per-transaction FX, no ticker consolidation — so symbols
    # stay per-listing and line up 1:1 with raw_gains) then re-run gains.
    # This yields a base-currency ACB per holding (computed by the gains
    # engine at acquisition-date rates), which the holdings export embeds
    # as base_total_cost / base_cost_per_share so downstream tools can see
    # whether a position is in a gain or loss in base-currency terms.
    # capture_diag here (unlike the other raw stages): convert-currency
    # emits its --default-rate FX-fallback summary on stderr, and a
    # silently-defaulted rate would corrupt base_total_cost. Persisting
    # it to the .diag surfaces any missing-rate fallback in the .sum.
    a = i.ctx.own
    run_to_file(_cmd("taxjson-convert-currency") + [
        str(ST.output(ST.Inst("raw", i.ctx))), "--to", a.base_currency,
        "--rates", str(a.rates), "--country", a.country,
    ], ST.output(i))


# ---------------------------------------------------------------- the blend's stage steps
# The steps of the stage table's blend_base, blend_gains and split rows
# (bound in _STAGES), run by stage_blended_wash_pass. A step reads the blend's
# StageCtx and, through `own`, the pass's options (_BlendRun).

@dataclass
class _BlendRun:
    """What the blend's stage steps read beyond its StageCtx."""
    reports_dir: Path
    no_wash: bool
    spot_crypto: bool


def _blend_base_build(i: ST.Inst, r: ST.Ready) -> None:
    # The opening snapshots' cut-off again over every account's rows: a
    # rename only another account's export carries reaches this
    # account's old symbol here (backlog TJ-0964). The books merged are
    # the row's inputs (BOOK alone; a blend is of one kind, so in the
    # members' order: ST.member_books).
    settings = i.ctx.settings
    run_to_file(_cmd("taxjson-merge") + [
        "--opening-cutoff", "--year", str(settings.year),
        "--country", _normalize_country(settings.country),
        "--tax-date", _tax_date(settings)] + [str(p) for p in r.inputs], ST.output(i))


def _blend_gains_prepare(i: ST.Inst) -> List[str]:
    """The blended gains run's command; the missing history's stamp first."""
    c, b = i.ctx, i.ctx.own
    cache, settings, combined_wash = c.work, c.settings, ST.output(i)
    _mh_prepare(i)
    country = _normalize_country(settings.country)
    cmd = _cmd("taxjson-gains") + [
        "--taxable",
        "--country", country,
        "--year", str(settings.year),
        "--tax-date", _tax_date(settings),
        "--full-traces", str(cache / f".{c.tag}_gains_wash.traces"),
    ]
    if _normalize_country(country) in ("us", "usa"):
        cmd.append("--per-account-basis")
    if b.no_wash:
        # A US project's crypto accounts (§1091 does not reach digital
        # assets): blended only so moves between them carry their lots.
        cmd.append("--no-wash")
    if b.spot_crypto:
        cmd.append("--spot-crypto")
    if c.sheltered is not None:
        cmd += ["--sheltered", str(c.sheltered)]
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    cmd += _locked_year_flags(cache.parent, settings)
    cmd += _loss_override_flags(cache)
    if c.mh is not None:
        cmd += ["--incomplete-history", str(c.mh)]
    # The pools per day, pooled (Canada) or per account (US): what
    # `list --date` shows each account (backlog TJ-0417).
    cmd += ["--cost-timeline", str(timeline_beside(combined_wash))]
    cmd.append(str(ST.output(ST.Inst("blend_base", c))))
    return cmd


def _blend_gains_build(i: ST.Inst, r: ST.Ready) -> None:
    run_to_file(r.prep, ST.output(i))


def _split_build(i: ST.Inst, _r: ST.Ready) -> None:
    """An account's split of the blended gains, gated on the record like
    the blend itself."""
    c, name, wash_gains = i.ctx, i.account, ST.output(i)
    # The split writes no .diag: a single-account wash pass's old
    # one would otherwise stay in this account's banner (S038-20).
    wash_gains.with_name(wash_gains.name + ".diag").unlink(missing_ok=True)
    run_to_file(_cmd("taxjson-split-gains") + [
        str(ST.output(ST.Inst("blend_gains", c))), "--account", name,
        "--base", str(c.work / f"{name}_base.json"),
        # Another account's split applies to this one's shares too
        # (backlog TJ-0696).
        "--pool-base", str(ST.output(ST.Inst("blend_base", c))),
    ], wash_gains, capture_diag=False)


def _split_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    """Its .sum and report.json twin rendered every run, as the per-account
    render is (one rule for both: the twin says wash-adjusted after every
    run, the banners fold today's diags, TJ-1827). Every account's wash twin
    is current after the split (the cross reports read it)."""
    c, name = i.ctx, i.account
    run_state.current.undated_rows += _flag_undated_listings(
        name, c.work, c.settings, ST.output(ST.Inst("blend_gains", c)))
    _render_wash_outputs(name, c.settings, c.work, c.own.reports_dir, blended=True)
    run_state.current.wash_current.add(name)


# work/<account>_listing_evidence.diag: the ATTENTION lines of the cost
# rows on a listing no dated row of their statement shows held
# (_flag_undated_listings); in the wash twin's banner only.
LISTING_EVIDENCE_SUFFIX = "_listing_evidence.diag"


def _undated_listing_rows(cache: Path, name: str, cost: List[str]
                          ) -> Dict[str, Dict[str, str]]:
    """{row id: what the parser's note names} of the account's cost rows
    (`cost`: their ids in the books, in order) a statement left on a
    listing none of its dated rows shows held, the account's exports
    naming another (lib/stage_msg.undated_listing_note, in a parse's
    .diag). A note names its row by file and line, the parse's rows by
    file, symbol and day: the notes of one file, symbol and day go to its
    cost rows in the file's order, one each."""
    import json as _json
    from taxjson.lib.stage_msg import parse_undated_listing_note
    want = set(cost)
    rows: Dict[str, Dict[str, str]] = {}
    for diag in sorted(cache.glob(f"{name}_*.json.diag")):
        try:
            notes = [n for n in (parse_undated_listing_note(ln) for ln in
                                 diag.read_text(errors="replace").splitlines()
                                 if ln.startswith("note: ")) if n]
            notes = [n for n in notes if n["what"] == "return of capital"]
            if not notes:
                continue
            txs = _json.loads(diag.with_suffix("").read_text(
                encoding="utf-8")).get("transactions") or []
        except (OSError, ValueError, AttributeError):
            continue
        groups: Dict[Tuple[str, str, str], List[Dict[str, str]]] = {}
        for n in notes:
            groups.setdefault((n["file"], n["symbol"], n["day"]), []).append(n)
        for (file, sym, day), ns in groups.items():
            ns.sort(key=lambda n: int(n["line"] or 0))
            mine = [t for t in txs if isinstance(t, dict)
                    and str(t.get("id") or "") in want
                    and t.get("account") == name
                    and t.get("symbol") == sym and t.get("date") == day]
            there = [t for t in mine if t.get("source") == file] or mine
            for k, t in enumerate(there):
                # (more rows than notes: the last note's, without a line)
                n = ns[k] if k < len(ns) else dict(ns[-1], line="")
                rows.setdefault(str(t["id"]), n)
    return rows


def _map_renames(cache: Path) -> Optional[Dict[str, str]]:
    """The renames of the map the books were merged with (the run's
    effective map, else ticker.map); {} with no map, None unreadable."""
    tm = cache / _PL.EFFECTIVE_MAP
    if not tm.is_file():
        tm = _PL.ticker_map_path(cache.parent)
    if not tm.is_file():
        return {}
    try:
        from taxjson.lib.ticker_map_rules import merge_renames
        from taxjson.lib.map_file import load_map_file
        import contextlib as _cl
        import io as _io
        with _cl.redirect_stderr(_io.StringIO()):
            return merge_renames(load_map_file(tm), to_base=True)
    except (OSError, ValueError):
        return None


def _one_pool(renames: Optional[Dict[str, str]], symbols: List[str]) -> bool:
    """Whether the map books `symbols` as one symbol: one cost pool."""
    if renames is None:
        return False
    from taxjson.lib.ticker_map_rules import map_symbol
    return len({map_symbol(s, renames) for s in symbols}) < 2


def _dec(v: Any) -> Decimal:
    """A JSON number as a Decimal (0 for none)."""
    return Decimal(str(v if v is not None else 0))


def _books_did(timeline: Dict[str, Any], rid: str, amount: Decimal,
               account: str, booked: str, day: str
               ) -> Tuple[str, Optional[Decimal], Optional[Decimal]]:
    """What the engine did with the cost row `rid` (an amount of `amount`
    returned), read from its cost timeline: (the pool's symbol, the cost
    it took off the pool, the rest it booked as a gain: all of it for a
    row that left its pool as it was); (`booked`, None, None) when the
    timeline does not say. A timeline that records no row ids (the US
    engine's) is read by the pool and the day: the state of `booked` dated
    `day` that keeps the units and takes no more than the amount off the
    cost."""
    from taxjson.lib import cost_timeline as CT
    try:
        seq = CT.row_seqs(timeline).get(rid)
        fields = list(timeline.get("fields") or CT.FIELDS)
        rows = [dict(zip(fields, r)) for r in timeline.get("rows") or []]
    except (AttributeError, TypeError, ValueError):
        return booked, None, None
    zero = _dec(0)
    if seq is None and not timeline.get("row_ids"):
        last: Dict[Any, Dict[str, Any]] = {}
        for x in rows:
            key = (x.get("account"), x.get("symbol"))
            prev = last.get(key)
            last[key] = x
            if (prev is None or x.get("symbol") != booked
                    or x.get("date") != day
                    or x.get("account") not in ("", account)
                    or x.get("qty") != prev.get("qty")):
                continue
            took = _dec(prev.get("total_cost")) - _dec(x.get("total_cost"))
            if zero < took <= amount + _dec("0.005"):
                took = min(took, amount)
                return booked, took, amount - took
        return booked, None, None
    if seq is None:
        return booked, None, None
    here = [r for r in rows if r.get("seq") == seq
            and r.get("account") in ("", account)]
    if not here:
        # No state after it: the row changed no pool.
        return booked, zero, amount
    r = next((x for x in here if x.get("symbol") == booked), here[0])
    before = zero
    for x in rows:
        if (x.get("seq") is not None and x["seq"] < seq
                and x.get("symbol") == r.get("symbol")
                and x.get("account") == r.get("account")):
            before = _dec(x.get("total_cost"))
    took = min(amount, max(zero, before - _dec(r.get("total_cost"))))
    return str(r.get("symbol")), took, max(zero, amount - took)


def _unapplied(gains: Path, symbol: str, day: str) -> bool:
    """Whether the engine run that wrote `gains` said it did not apply a
    cost adjustment of `symbol` on `day` (the US engine's ATTENTION for a
    return of capital that finds no lots: no gain is booked)."""
    head = f"warning: ATTENTION: unapplied basis adjustment: {symbol} ADJUST "
    return any(ln.startswith(head) and f" on {day} " in ln
               for ln in _diag_lines(gains))


def _flag_undated_listings(name: str, cache: Path, settings: Settings,
                           gains: Path, *, pooled_apart: bool = False
                           ) -> List[str]:
    """A cost row (a return of capital) a statement left on a listing none
    of its dated rows shows held that day, while the account's exports
    name another listing of the ticker (the parser's notes: the split
    exports, a statement that trades one listing later, a statement with
    no Open Positions whose purchase is in another year's). An ATTENTION
    each, wherever the row landed: the payment and the listing it may
    belong to first, then what the books did with it, from the engine's
    cost timeline (beside `gains`: the blended pass's gains, or the
    account's own under `run --account`; so a row of an earlier year says
    what that year's books did): the cost it took off the pool, the rest a
    gain, or not applied (the engine's own word in its .diag). `pooled_apart`: `run --account` in a project that pools
    cost across taxable accounts, where the account's own books are not
    the filed ones: nothing is said of what they did. None where the map
    books the listings as one pool. On the console and in
    work/<name>_listing_evidence.diag (the wash twin's banner, the
    closing list, the checklist); returns each row as `--strict` names
    it (backlog TJ-5011, TJ-5201, TJ-5202)."""
    import json as _json
    from taxjson.lib import cross_listings as XL
    from taxjson.lib.stage_msg import UNDATED_LISTING_GAIN
    from taxjson.lib.ticker_map_rules import map_symbol
    diag = cache / f"{name}{LISTING_EVIDENCE_SUFFIX}"
    amounts: Dict[str, Any] = {}
    try:
        # The books' rows: a cost row is an ADJUST there (a foreign
        # issuer's return of capital becomes one after the parse).
        for t in _json.loads((cache / f"{name}_base.json").read_text(
                encoding="utf-8")).get("transactions") or []:
            if isinstance(t, dict) and t.get("action") == "ADJUST":
                amounts[str(t.get("id"))] = abs(_dec(t.get("net_amount")))
    except (OSError, ValueError, AttributeError, TypeError, ArithmeticError):
        amounts = {}
    rows = _undated_listing_rows(cache, name, list(amounts))
    doc: Dict[str, Any] = {}
    if rows and not pooled_apart:
        try:
            doc = _json.loads(timeline_beside(gains).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
    renames = _map_renames(cache) if rows else {}
    base = str(settings.base_currency or "").upper() or None
    cur = f" {base}" if base else ""
    lines: List[str] = []
    shorts: List[str] = []
    for rid in [r for r in amounts if r in rows]:
        n = rows[rid]
        sym = n["symbol"]
        flat = [s for s in n["flat"].split(", ") if s]
        others = [s for s in n["listings"].split(", ") if s and s != sym]
        if not others or _one_pool(renames, [sym] + others):
            continue
        alts = [s for s in others if s not in flat]
        alt = " or ".join(alts)
        where = f"{n['file']} line {n['line']}" if n["line"] else n["file"]
        amount = amounts[rid]
        booked, took, gain = _books_did(
            doc, rid, amount, name, map_symbol(sym, renames or {}), n["day"])
        on = booked if booked == sym else f"{booked} ({sym} in the statement)"
        # The headline: the fact first, short enough for a 100-column
        # console's one line; the row's file last.
        head = (f"{n['what']} {n['day']} on {on} "
                + (f"may belong to {alt}" if alt
                   else "that no dated row shows held")
                + f" ({name}: {where}).")
        cent = _dec("0.005")
        unapplied = not pooled_apart and _unapplied(gains, booked, n["day"])
        if n["why"] == "moved" and not unapplied and (
                gain is None or took is None or gain < cent or took >= cent):
            # Moved off its ISIN's listing on another statement's word:
            # said only where the books found nothing there to reduce.
            continue
        if pooled_apart:
            did = ("The books that pool the taxable accounts say what was "
                   "done with it: a full `tjs run`.")
        elif unapplied:
            did = (f"The books did not apply it: {booked} held nothing that "
                   f"day (the Unapplied basis adjustment warning).")
        elif took is None or gain is None:
            did = (f"The books' record of that day does not say what was "
                   f"done with it: `tjs audit {booked}`.")
        elif gain < cent:
            did = f"The books reduced the cost of {booked} by {took:.2f}{cur}."
        elif took < cent:
            did = (f"The books took it as a capital gain of {gain:.2f}{cur}: "
                   f"{booked} had no cost to reduce that day.")
        else:
            did = (f"The books reduced the cost of {booked} by {took:.2f}{cur}, "
                   f"all it had, and took the other {gain:.2f}{cur} as a "
                   f"capital gain.")
        if n["why"] == "moved":
            why = (f"The row was moved off its ISIN's listing {alt}: the "
                   f"account's statements hold or trade {sym}, and no dated "
                   f"row shows it held that day.")
        elif n["why"] == "split":
            why = ("The statement has no position in the ticker, and the "
                   "account's other exports name its listings with no dates "
                   "the statement can use, so the row kept the listing its "
                   "ISIN names.")
        else:
            why = (f"No dated row of the statement shows {sym} held that day, "
                   f"and the account's exports also name "
                   f"{' and '.join(others)}"
                   + (f" (the statement's own rows show {' and '.join(flat)} "
                      f"not held that day)" if flat else "") + ".")
        if alt:
            then = (f"If {alt} was the listing held, its cost is too high by "
                    f"{amount:.2f}{cur} and what is booked on {booked} is "
                    f"not real.")
        else:
            then = (f"If {booked} was not held that day, what is booked on it "
                    f"is not real.")
        tob = " / ".join("`TOBASE {} {}`".format(*XL.tobase_direction(o, sym, base))
                         for o in others)
        fix = ("Download a statement that holds the account's Open Positions "
               "section, or every trade of the ticker from before the "
               "holding's purchase, and keep it in place of the split "
               f"exports; or, if the listings are one security, add {tob} to "
               "ticker.map (one cost pool).")
        lines.append(f"{UNDATED_LISTING_GAIN}{head} {did} {why} {then} {fix}")
        shorts.append(f"{where}: {n['what']} of {n['day']} on {booked}")
    for line in lines:
        _echo_captured(line)
    if lines:
        _write_atomic(diag, "".join(ln + "\n" for ln in lines), encoding="utf-8")
    else:
        diag.unlink(missing_ok=True)
    return shorts


def undated_strict(rows: List[str]) -> str:
    """`run --strict`'s stop on the rows _flag_undated_listings named."""
    return (f"--strict: {len(rows)} cost row(s) on a listing no dated row "
            f"shows held, where the account's exports name another "
            f"({'; '.join(rows)}) — aborting")


def flag_undated_account(name: str, cache: Path, settings: Settings,
                         gains: Path, *, pooled_apart: bool,
                         strict: bool) -> None:
    """`tjs run --account NAME`, which runs no pass across accounts: the
    undated-listing check from the account's own gains (TJ-5202)."""
    rows = _flag_undated_listings(name, cache, settings, gains,
                                  pooled_apart=pooled_apart)
    if rows and strict:
        _die(undated_strict(rows))


def _wash_prepare(i: ST.Inst) -> List[str]:
    """The account's wash pass's command; the missing history's stamp first."""
    c, settings, wash_gains = i.ctx, i.ctx.settings, ST.output(i)
    cache = c.work
    _mh_prepare(i)
    cmd = _cmd("taxjson-gains") + [
        "--taxable",
        "--country", _normalize_country(settings.country),
        "--year", str(settings.year),
        "--tax-date", _tax_date(settings),
        "--sheltered", str(c.sheltered),
        "--full-traces", str(ST.wash_traces(i)),
    ]
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    cmd += _locked_year_flags(cache.parent, settings)
    cmd += _loss_override_flags(cache)
    # Same missing-history opening balances as the main gains pass — without
    # this the wash-adjusted books (which `tjs wash-sales` PREFERS when
    # present) were computed on different books than <account>.sum.
    if c.mh is not None:
        cmd += ["--incomplete-history", str(c.mh)]
    cmd += ["--cost-timeline", str(timeline_beside(wash_gains))]
    cmd.append(str(cache / f"{i.account}_base.json"))
    return cmd


def _wash_build(i: ST.Inst, r: ST.Ready) -> None:
    run_to_file(r.prep, ST.output(i))


def _wash_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    """What the pass's file is read for every run: the record of its
    inputs, the loss windows, the cross reports' wash twin, its renders."""
    c, name, wash_gains = i.ctx, i.account, ST.output(i)
    _record_wash_inputs(c.work, [wash_gains], [name], c.sheltered, tag=name)
    run_state.current.loss_context_gains.append(wash_gains)
    run_state.current.wash_current.add(name)     # the cross reports read it
    _render_wash_outputs(name, c.settings, c.work, c.own.reports_dir)



# ---------------------------------------------------------------- the renders
# The stage table's render rows (TJ-1828): an account's crypto validation,
# holdings handoff, summary, wash twin and report model, each built when
# what it folds changed. The step lines are said every run, as before.

def _crypto_validate_build(i: ST.Inst, _r: ST.Ready) -> None:
    c = i.ctx
    base_json, validate_diag = c.work / f"{i.account}_base.json", ST.output(i)
    from taxjson.pipeline.dispatch import run_cmd as _run_cmd
    # --require-prices: a row fill-crypto could not price is an
    # ERROR here (R1-105), not a silent $0 income/cost.
    # Run from work/ on the bare file name: the report is copied into
    # reports/<name>.sum, and an absolute path put the user's home
    # directory (their OS user name) in every crypto report (S037-18).
    vres = _run_cmd(_cmd("taxjson-validate") + ["--require-prices",
                                                base_json.name],
                    capture_output=True, cwd=str(base_json.parent))
    report = (vres.stdout or "") + (vres.stderr or "")
    if report.strip():
        _write_atomic(validate_diag, report, encoding="utf-8")
    elif validate_diag.exists():
        validate_diag.unlink()
    c.own.validated = (vres.returncode, report)


def _crypto_validate_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    """A failed validation said, and --strict's stop (every run validates
    again)."""
    c, name = i.ctx, i.account
    if c.own.validated is None or c.own.validated[0] == 0:
        return
    # Quiet on success (the report is in the .diag → .sum); only
    # surface to the console if validation actually failed.
    # `report` is text (run_cmd captures with text=True);
    # stderr.buffer.write(str) raised TypeError and crashed the
    # run instead of printing the report.
    _echo_stage_stderr(c.own.validated[1])
    if c.own.strict:
        _die(f"--strict: {name}: validation ERROR(s) in the "
             f"crypto books — aborting")
    _say("warning", f"{name}: validation ERROR(s) in the crypto "
         f"books — numbers may be wrong",
         f"Details: reports/{name}.sum DIAGNOSTICS (or "
         f"work/{ST.output(i).name}).", indent="  ")


def _holdings_build(i: ST.Inst, _r: ST.Ready) -> None:
    c, out = i.ctx, ST.output(i)
    if not c.own.after_passes:
        build_holdings_toml(i.account, c.own.holdings_cmd, c.work, out.parent)
        c.own.holdings_built = True
        return
    # Asked again after the passes: the handoff (unless this run wrote it
    # already), then their figures for units moved in from another of
    # your accounts.
    if not c.own.holdings_built:
        run_to_file(c.own.holdings_cmd, out, capture_diag=False)
    if c.own.fills:
        fill_moved_cost(i.account, c.own.holdings_cmd, c.work, out.parent)


def _holdings_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    c = i.ctx
    if not c.own.after_passes:
        say_holdings_toml(i.account, c.work, ST.output(i).parent,
                          built=c.own.holdings_built)


def stage_moved_holdings(fills: Iterable[str],
                         taxable_outputs: Iterable[Tuple[str, Dict[str, Any], bool]]
                         ) -> None:
    """After the passes across accounts: each taxable account's holdings
    row asked again, its wash file now what they wrote. An account in them
    (`fills`) whose handoff has units moved in from another of your
    accounts (`cost_unknown = true`) gets the figure the passes have for
    them (diagnostics.fill_moved_cost, backlog TJ-0598); stage_account
    wrote the file and said its changes, and this changes no quantity."""
    names = set(fills)
    for name, out, _crypto in taxable_outputs:
        ctx = out.get("stage_ctx")
        if ctx is None or not ctx.own.holdings_cmd:
            continue
        ctx.own.after_passes, ctx.own.fills = True, name in names
        ST.execute(_STAGES, ctx, "crypto_holdings" if ctx.crypto else "holdings")


def _sum_prepare(i: ST.Inst) -> None:
    sum_path = ST.output(i)
    sum_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _wrote(sum_path, sum_path.parent.parent)


def _sum_build(i: ST.Inst, _r: ST.Ready) -> None:
    """The summary — assembled in a .part sidecar and renamed on success, so a
    failing sub-report can't leave a truncated .sum that `tjs sum` then
    serves (same atomicity contract as run_to_file)."""
    c, name = i.ctx, i.account
    gains_json, base_json = c.work / f"{name}_gains.json", c.work / f"{name}_base.json"
    country = c.own.country
    from taxjson.lib.safe_write import atomic_open
    with atomic_open(ST.output(i), binary=True) as out:
        # The pre-blend baseline: the cross-account passes' notes
        # (written after this file) go in <name>_wash.sum.
        out.write(_diagnostics_banner(c.work, name, post_pass=False))
        out.write(run_capture(_cmd("taxjson-sum-gains")
                              + (["--staking"] if c.crypto else [])
                              + ["--country", country]
                              + [str(gains_json)]))
        out.write(run_capture(_cmd("taxjson-sum-income") + [
            "--year", str(c.own.year), "--country", country,
        ] + income_dating_flags(c.settings) + [
            "--gains", str(gains_json), str(base_json)]))
        if not c.crypto:
            # The portfolio-snapshot report is equity-specific; crypto skips it.
            out.write(run_capture(_cmd("taxjson-export") + [
                "--report", "--futures", str(gains_json),
            ]))


@dataclass
class _WashSum:
    """What a wash twin's render reads beyond its StageCtx."""
    blended: bool       # a blended pass's split: the plain gains' notes left out


def _wash_sum_build(i: ST.Inst, _r: ST.Ready) -> None:
    """The .sum rendering for one account's wash-adjusted gains — shared by
    the per-account (crypto) and blended (equity) passes."""
    c, name, settings = i.ctx, i.account, i.ctx.settings
    wash_gains, base_json = c.work / f"{name}_gains_wash.json", c.work / f"{name}_base.json"
    # .part + rename: a failing sub-report must not truncate the .sum.
    from taxjson.lib.safe_write import atomic_open
    with atomic_open(ST.output(i), binary=True) as out:
        out.write(_diagnostics_banner(c.work, name, blended=c.own.blended))
        out.write(run_capture(_cmd("taxjson-sum-gains")
                              + (["--staking"] if c.crypto else [])
                              + ["--country", _normalize_country(
                                  settings.country)]
                              + [str(wash_gains)]))
        out.write(run_capture(_cmd("taxjson-sum-income") + [
            "--year", str(settings.year),
            "--country", _normalize_country(settings.country),
        ] + income_dating_flags(settings) + [
            "--gains", str(wash_gains), str(base_json)]))
        out.write(run_capture(_cmd("taxjson-export") + [
            "--report", "--futures", str(wash_gains),
        ]))


def _wash_sum_every_run(i: ST.Inst, _r: ST.Ready) -> None:
    _wrote(ST.output(i), ST.output(i).parent.parent)


def _report_build(i: ST.Inst, _r: ST.Ready) -> None:
    """The machine twin of the text reports: the structured aggregates `tjs
    sum` and future consumers read instead of re-deriving them from the
    gains JSON, on the basis the query commands resolve to (they prefer
    <name>_gains_wash.json): the wash-adjusted gains when this run's pass
    wrote them, else the account's own, or `tjs sum`'s fast path would
    serve pre-wash aggregates next to a "wash-adjusted" banner."""
    c, name = i.ctx, i.account
    washed = ST.washed(i)
    gains = c.work / (f"{name}_gains_wash.json" if washed else f"{name}_gains.json")
    try:
        from taxjson.lib.report_model import load_report_json
        from taxjson.lib.account_report import build_account_report
        _base_rows = load_report_json(c.work / f"{name}_base.json").get(
            "transactions", [])
        from taxjson.lib.safe_write import write_atomic
        write_atomic(ST.output(i), _json_dumps_report(build_account_report(
                load_report_json(gains), name,
                basis="wash-adjusted" if washed else "pre-wash",
                base_transactions=_base_rows,
                rules=_income_rules(c.settings))))
    except Exception as e:                      # advisory artifact only
        _say("warning", f"could not write {name}_report.json: {e}",
             prog=_PROG)


def stage_reports(sheltered_outputs: Iterable[Tuple[str, Dict[str, Any]]],
                  taxable_outputs: Iterable[Tuple[str, Dict[str, Any], bool]]
                  ) -> None:
    """Each account's work/<name>_report.json (from what stage_account
    returned) once the passes across accounts ran (the stage table's
    report row)."""
    for out in [o for _n, o in sheltered_outputs] + [o for _n, o, _c in taxable_outputs]:
        ctx = out["stage_ctx"]
        ST.execute(_STAGES, ST.StageCtx(work=ctx.work, settings=ctx.settings,
                                        account=ctx.account, crypto=ctx.crypto),
                   "report")



# ---------------------------------------------------------------- the reports
# The reports across accounts (stage_cross_reports) and the fees report.

@dataclass
class _CrossRun:
    """What the cross-account reports read beyond their StageCtx."""
    gains: List[Path]       # every account's gains: a taxable one's wash file, if any
    bases: List[Path]       # the taxable books (Canada: crypto too) the radar reads
    country: str


def _ccd_build(i: ST.Inst, r: ST.Ready) -> None:
    run_to_file(_cmd("taxjson-ccd-gains") + [str(p) for p in r.inputs],
                ST.output(i), capture_diag=False)


def _leaps_build(i: ST.Inst, r: ST.Ready) -> None:
    run_to_file(_cmd("taxjson-leaps-gains") + [str(p) for p in r.inputs],
                ST.output(i), capture_diag=False)


def _radar_cmd(i: ST.Inst, show: Optional[Path], label: str) -> List[str]:
    c = i.ctx
    bases = c.own.bases
    # --json-out: structured sidecar next to the .rpt (harvest
    # --radar reads it and computes countdowns at its own time).
    # Every taxable book decides the statuses (the engine
    # pools them); --show keeps the rows to this account's
    # listings. A sidecar that read its own book alone called a
    # sibling account's rebuy COOLING / "full exit is fine"
    # (audit 2026-10-23 #6) — the same view as `wash-radar
    # <account>`.
    return (_cmd("taxjson-wash-radar") + ["--taxable", *[str(b) for b in bases]]
            + (["--show", str(show)] if show is not None else [])
            + ["--json-out", str(ST.radar_json(i)), "--account", label]
            + _radar_engine_args(bases, c.mh, c.own.country, ticker_map=c.ticker_map)
            + (["--sheltered", str(c.sheltered)] if c.sheltered else []))


def _write_today(work: Path) -> None:
    """work/radar_today.state: the day the wash radar counts its windows
    from (taxjson-wash-radar's default --date), rewritten when it turns."""
    stamp = work / "radar_today.state"
    day = datetime.now().strftime("%Y-%m-%d") + "\n"
    if _read_work_stamp(stamp) != day:
        _write_work_stamp(stamp, day)


def _radar_prepare(i: ST.Inst) -> List[str]:
    """The radar's stamps, and its command: made before the ask, since
    the gains it pools are resolved through the wash pass's record."""
    _mh_prepare(i)
    _write_today(i.ctx.work)
    if i.row == "radar_combined":
        # Cross-account radar: one run over all taxable bases, every
        # row — the sidecar harvest's ADVISORY reads. The
        # per-account files are its rows for one account.
        return _radar_cmd(i, None, "COMBINED")
    # Strip only the trailing "_base": replace() removed EVERY
    # "_base", so account a_base_x wrote account a_x's radar
    # (S038-10).
    show = next(b for b in i.ctx.own.bases
                if (b.name[:-len("_base.json")] if b.name.endswith("_base.json")
                    else b.stem) == i.account)
    return _radar_cmd(i, show, i.account)


def _radar_build(i: ST.Inst, r: ST.Ready) -> None:
    run_to_file(r.prep, ST.output(i), capture_diag=False)


def _crosslistings_build(i: ST.Inst, _r: ST.Ready) -> None:
    c = i.ctx
    cmd = _cmd("taxjson-lint-crosslistings") + [
        "--taxable", *[str(tb) for tb in c.own.bases],
    ] + (["--sheltered", str(c.sheltered)] if c.sheltered else [])
    if c.ticker_map:
        cmd += ["--map", str(c.ticker_map)]
    run_to_file(cmd, ST.output(i), capture_diag=False)


@dataclass
class _Fees:
    """What the fees report reads beyond its StageCtx."""
    rates: Path
    books: List[Path] = field(default_factory=list)
    named: bool = False             # the books are named to it, not --cache


def _broker_tagged(p: Path) -> bool:
    import json as _json
    try:
        doc = _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True             # one it cannot read stops the report: named
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    return bool(isinstance(meta, dict) and meta.get("source_brokerage"))


def _fees_prepare(i: ST.Inst) -> None:
    """The books the fees report counts: each work/*.json a broker's
    parse or a .tt file wrote (`metadata.source_brokerage`, the report's
    own rule), named to it; with none, every work/*.json (its --cache:
    the honest empty report). The others never move it (backlog TJ-0434),
    and a view's book built on demand must not rebuild it."""
    _wrote(ST.output(i), ST.output(i).parent.parent, "fees report")
    every = [p for p in sorted(i.ctx.work.glob("*.json")) if not p.name.startswith(".")]
    tagged = [p for p in every if _broker_tagged(p)]
    i.ctx.own.books, i.ctx.own.named = tagged or every, bool(tagged)


def _fees_build(i: ST.Inst, _r: ST.Ready) -> None:
    # capture_diag=True: taxjson-fees-sum emits FX default-rate fallback and
    # skipped-file warnings on stderr; persist them to the .diag so a silently
    # wrong rate can't slip through (the report body carries them too).
    c, settings = i.ctx, i.ctx.settings
    run_to_file(_cmd("taxjson-fees") + (
        [str(p) for p in c.own.books] if c.own.named else ["--cache", str(c.work)]) + [
        "--year", str(settings.year),
        "--to", settings.base_currency, "--rates", str(c.own.rates),
    ] + (["--ticker-map", str(c.ticker_map)] if c.ticker_map else []),
        ST.output(i))


# The stage table with these steps bound: what ST.execute runs.
_STAGES = ST.bound({
    "rates": ST.Steps(_rates_build),
    "parse": ST.Steps(_parse_build, _parse_prepare, _parse_every_run),
    "corp": ST.Steps(_corp_build, every_run=_corp_every_run),
    "tt": ST.Steps(_tt_build, _tt_prepare, _tt_every_run),
    "crypto_merged": ST.Steps(_crypto_merged_build),
    "crypto_sorted": ST.Steps(_crypto_sorted_build),
    "crypto_mapped": ST.Steps(_crypto_mapped_build),
    "crypto_filled": ST.Steps(_crypto_filled_build, _crypto_filled_prepare,
                              _crypto_filled_every_run),
    "crypto_base": ST.Steps(_crypto_base_build),
    "base": ST.Steps(_base_build, _base_prepare, _base_every_run),
    "gains": ST.Steps(_gains_build, _gains_prepare, _gains_every_run),
    "raw": ST.Steps(_raw_build, every_run=_raw_every_run),
    "raw_gains": ST.Steps(_native_gains_build, _mh_prepare),
    "raw_base": ST.Steps(_raw_base_build),
    "raw_base_gains": ST.Steps(_native_gains_build, _mh_prepare),
    "blend_base": ST.Steps(_blend_base_build),
    "blend_gains": ST.Steps(_blend_gains_build, _blend_gains_prepare),
    "split": ST.Steps(_split_build, every_run=_split_every_run),
    "wash": ST.Steps(_wash_build, _wash_prepare, _wash_every_run),
    "crypto_validate": ST.Steps(_crypto_validate_build,
                                every_run=_crypto_validate_every_run),
    "holdings": ST.Steps(_holdings_build, every_run=_holdings_every_run),
    "crypto_holdings": ST.Steps(_holdings_build, every_run=_holdings_every_run),
    "sum": ST.Steps(_sum_build, _sum_prepare),
    "wash_sum": ST.Steps(_wash_sum_build, every_run=_wash_sum_every_run),
    "report": ST.Steps(_report_build),
    "ccd": ST.Steps(_ccd_build),
    "leaps": ST.Steps(_leaps_build),
    "radar": ST.Steps(_radar_build, _radar_prepare),
    "radar_combined": ST.Steps(_radar_build, _radar_prepare),
    "crosslistings": ST.Steps(_crosslistings_build),
    "fees": ST.Steps(_fees_build, _fees_prepare),
})


def _json_dumps_report(payload) -> str:
    # The machine twin of the filing figures: a NaN / infinity is
    # refused, never written as a bare token (issue #8).
    from taxjson.lib.json_input import filing_json_text
    return filing_json_text(payload, indent=2, sort_keys=True) + "\n"


def _diagnostics_banner(cache: Path, account: str, *,
                        post_pass: bool = True,
                        blended: bool = False) -> bytes:
    """A DIAGNOSTICS section for the top of a .sum file, or empty bytes
    when the account's pipeline stages produced no warnings/notes."""
    diag = collect_diagnostics(cache, account, post_pass=post_pass,
                               blended=blended)
    if not diag:
        return b""
    rule = "=" * 70
    return (f"{rule}\n DIAGNOSTICS — {account}  "
            f"(parser/merge warnings; not part of the tax totals)\n{rule}\n"
            f"{diag}\n{rule}\n\n").encode()


def _wash_rule_words(settings: Settings) -> str:
    """The country's loss-denial rule, plural, for a console step."""
    return ("wash sales" if _country(settings) in ("us", "usa")
            else "superficial losses")


def stage_wash_pass(name: str, settings: Settings, cache: Path, reports_dir: Path,
                    sheltered_base: Path,
                    incomplete_history: Optional[Path] = None) -> None:
    """Re-run gains with --sheltered to apply cross-account wash sale: the
    stage table's wash row (a crypto account's own pass)."""
    _step(f"Checking {name} for {_wash_rule_words(settings)} with the "
          f"sheltered accounts")
    ST.execute(_STAGES, ST.StageCtx(
        work=cache, settings=settings, account=name, crypto=True,
        sheltered=sheltered_base, mh=incomplete_history,
        own=_BlendRun(reports_dir, False, False)), "wash")


def _record_wash_inputs(cache: Path, washes: List[Path], names: List[str],
                        sheltered_base: Optional[Path], *, tag: str) -> None:
    """Remember what the wash files were built from (every member's
    base and plain gains, the sheltered book, the loss-override state)
    so a later `run --account <member>` makes them stale for every
    account of the blend, not only the one rerun (GitHub issue #15;
    report_model.stale_wash_inputs). Advisory: a failure is a warning."""
    from taxjson.lib.report_model import record_wash_inputs
    from taxjson.lib.loss_overrides import state_path
    inputs = [cache / f"{n}{suf}" for n in names
              for suf in ("_base.json", "_gains.json")]
    inputs.append(sheltered_base if sheltered_base is not None
                  else cache / "sheltered_base.json")
    inputs.append(state_path(cache))
    try:
        record_wash_inputs(cache, washes, inputs, blend=tag,
                           configured=accounts_of(load_config(
                               cache.parent, missing_ok=True)))
    except OSError as e:
        _say("warning", f"could not record the inputs of the "
             f"cross-account pass: {e}",
             "A later `run --account` may leave the wash-adjusted "
             "numbers stale without a warning; run a full `tjs run` "
             "before filing.", indent="  ")


def _say_transfer_windows(settings: Settings) -> None:
    """ONE warning per run (CA-SL-16 / US-WASH-23): every transfer-in
    the books keep as a move between accounts, not a purchase, that sits
    inside a taxable loss's 30-day window — gathered from this run's
    loss-context passes (lib/pipeline.transfers_in_loss_windows)."""
    import json as _json
    from taxjson.lib.pipeline import transfer_window_message
    items: List[Dict[str, Any]] = []
    seen = set()
    for p in run_state.current.loss_context_gains:
        try:
            doc = _json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for x in (doc.get("transfers_in_loss_windows") or []
                  if isinstance(doc, dict) else []):
            if not isinstance(x, dict):
                continue
            k = (x.get("account"), x.get("symbol"), x.get("date"),
                 x.get("qty"))
            if k not in seen:
                seen.add(k)
                items.append(x)
    run_state.current.loss_context_gains.clear()
    if not items or ("xferwin",) in run_state.current.shown:
        return
    # Once per run, as the engine echoes are (_echo_captured once): a
    # failed stage's echo of a pass's own line is not shown after it.
    run_state.current.shown.add(("xferwin",))
    items.sort(key=lambda r: (str(r.get("date")), str(r.get("account")),
                              str(r.get("symbol"))))
    head, details, _legacy = transfer_window_message(
        items, _normalize_country(settings.country))
    _say("warning", head, *details, indent="  ", file=sys.stdout)


def _render_wash_outputs(name: str, settings: Settings, cache: Path,
                         reports_dir: Path, *, blended: bool = False) -> None:
    """An account's wash twin, <name>_wash.sum (the stage table's wash_sum
    row) — shared by the per-account (crypto) and blended (equity) passes.
    Its report model is rendered with the others' (stage_report)."""
    _is_c = bool(account_of(accounts_of(load_config(cache.parent, missing_ok=True)),
                            name).crypto)
    ST.execute(_STAGES, ST.StageCtx(work=cache, settings=settings, account=name,
                                    crypto=_is_c, root_dir=reports_dir.parent,
                                    own=_WashSum(blended)), "wash_sum")


TRANSFER_COSTS_SUFFIX = "_transfer_costs.json"


def _symbol_codes_suffix() -> str:
    from taxjson.lib.symbol_codes import SUFFIX
    return SUFFIX


def stage_ib_project(name: str, root: Path, cache: Path
                     ) -> Tuple[Path, List[Path]]:
    """work/<name>_ib_project.state: the project's OTHER securities
    accounts' IB statements ({"account": name, "accounts": {label:
    [paths]}}), passed to the IB parse as --project-statements: a ticker
    change IB shows as one contract id under two symbols is the
    security's, dated from every account's rows (review 2 #4). Returns
    (the state, those statements: deps of the parse; none when no other
    account has an IB statement — then the parse gets no
    --project-statements). Written only when it changes, and kept with
    `"accounts": {}` when the last other statement goes: the state is
    always a dep, so `run --fast` re-parses then too (third pre-release
    review, 6)."""
    import json as _json
    from taxjson.lib.brokerages.detect import detect
    out = cache / f"{name}_ib_project.state"
    others: Dict[str, List[str]] = {}
    deps: List[Path] = []
    for label, acfg in sorted(accounts_of(load_config(root, missing_ok=True)).items()):
        if label == name or acfg.crypto:
            continue
        acct_dir = _PL.inputs_dir(root) / label
        if not acct_dir.is_dir():
            continue
        found = []
        for csv in input_files(acct_dir, ".csv"):
            try:
                det = detect(csv)
            except Exception:                      # noqa: BLE001
                continue        # that account's own stage names it
            if det.broker == "ib" and not det.positions:
                found.append(csv)
        if found:
            others[label] = [str(p) for p in found]
            deps.extend(found)
    text = _json.dumps({"account": name, "accounts": others}, indent=1,
                      sort_keys=True) + "\n"
    if _read_work_stamp(out) != text:
        _write_work_stamp(out, text)
    return out, deps


def _ib_tt_state(name: str, csvs: List[Path], cache: Path) -> Path:
    """work/<name>_ib_tt.state: what the IB parse of `csvs` reads from the
    project's .tt files (lib/brokerages/ib_extractor.tt_reads_text: every
    RENAME line, and the account's SPLIT rows that move a position to
    another symbol, each with its file and line number). A dep of the
    parse, rewritten only when it changes (a line inserted above one of
    them too): undeclared, a line added under `--fast` kept the old parse
    (TJ-4129)."""
    from taxjson.lib.brokerages.ib_extractor import tt_reads_text
    out = cache / f"{name}_ib_tt.state"
    text = tt_reads_text(csvs)
    if _read_work_stamp(out) != text:
        _write_work_stamp(out, text)
    return out


def stage_symbol_codes(name: str, csvs: List[Path], root: Path,
                       cache: Path, out: Path) -> Path:
    """work/<name>_symbol_codes.state: the Questrade internal codes of
    this account that its own rows do not resolve, inferred from the
    project's other exports (lib/symbol_codes: a paired transfer out,
    else a unique name match; a ticker.map rule for the code wins).
    Written only when it changes — it is a dep of the Questrade parse,
    which books the codes under their tickers and says so in one note."""
    from taxjson.lib import symbol_codes as SC
    from taxjson.lib.brokerages.questrade import (QuestradeBrokerage,
                                                  journal_codes,
                                                  scan_code_uses)
    try:
        uses = scan_code_uses(csvs)
    except Exception:                              # noqa: BLE001
        uses = []           # the parse itself names a broken export
    try:
        by_journal = journal_codes(csvs)
    except Exception:                              # noqa: BLE001
        by_journal = {}     # the parse itself names a broken export
    result: Dict[str, Any] = {"resolved": {}, "unresolved": {}}
    if uses:
        accounts = [n for n, c in accounts_of(load_config(root, missing_ok=True)).items()
                    if not c.crypto]
        outs, names = SC.project_evidence(
            cache, accounts or [name], receiving=(name, "questrade"))
        named = _ticker_map_named(root)
        helper = QuestradeBrokerage()

        def listing_ok(listing: str, cur: str) -> bool:
            return helper.apply_currency_suffix(listing, cur) == listing

        def mapped(code: str) -> bool:
            # ANY ticker.map rule naming the code (a rename, DELETE,
            # DISTINCT, a dated RENAME) wins — the test the parser
            # applies too (base.ticker_map_names), so the record, the
            # books and `tjs transfers` agree.
            return any(helper.apply_currency_suffix(code, cur) in named
                       for cur in sorted({"USD", "CAD"}
                                         | {c for u in uses
                                            if u.code == code
                                            for c in u.currencies}))
        result = SC.resolve(uses, outs, names, listing_ok=listing_ok,
                            mapped=mapped,
                            code_listing=helper.apply_currency_suffix)
    if by_journal:
        # Codes a BRW journal leg of this account names (the parser
        # books them so): recorded for `tjs transfers`; a ticker.map
        # rule naming the code wins here too.
        named_j = _ticker_map_named(root)
        _h = QuestradeBrokerage()
        for code, r in sorted(by_journal.items()):
            if any(_h.apply_currency_suffix(code, c) in named_j
                   for c in ("USD", "CAD", r.get("currency") or "USD")):
                result.setdefault("mapped", {})[code] = {
                    "how": "ticker.map", "evidence": "ticker.map rule"}
            else:
                result["resolved"][code] = dict(r)
    text = SC.state_text(name, result)
    if _read_work_stamp(out) != text:
        _write_work_stamp(out, text)
    return out


def _listing_suffix_text(name: str, broker: str, csvs: List[Path],
                         root: Path, cache: Path) -> str:
    """The state text stage_listing_suffix writes (lib/listing_suffix)."""
    from taxjson.lib import listing_suffix as LS
    scan = LS.SCANNERS[broker](csvs)
    result: Dict[str, Any] = {"corrected": {}, "kept": {}}
    if scan.cands:
        accounts = [n for n, c in accounts_of(load_config(root, missing_ok=True)).items()
                    if not c.crypto]
        ev = LS.project_evidence(cache, accounts or [name],
                                 receiving=(name, broker))
        # ANY ticker.map line naming the listing (a rename either way,
        # DELETE, DISTINCT, a dated RENAME, and a lookup line: QUOTE,
        # T1135, an EXTRACT target ...) wins: `DISTINCT ROOT.US ROOT.TO`
        # keeps the row currency's listing, and a line written for the
        # listing as filed keeps meaning what it says.
        named = {str(x).upper()
                 for x in _ticker_map_named(root, lookups=True)}
        # The map's renames (GLOBAL / TOBASE / JOURNAL, chains followed)
        # apply to an out-leg before it is read as evidence: a correct
        # `TOBASE QZAB.US QZAA.TO` books QZAB's transfer out as QZAA.TO.
        result = LS.resolve(scan, ev, account=name, broker=broker,
                            mapped=lambda s: s in named,
                            renames=_ticker_map_renames(root))
        # The TOBASE line of a correction's transfer journal, as the
        # join will read it (the explicit lines `ticker-map --suggest`
        # shows).
        from taxjson.lib.cross_listings import tobase_direction
        base = str(load_settings(root, missing_ok=True).base_currency or "").upper() or None
        for r in result["corrected"].values():
            o = str((r.get("pair") or {}).get("symbol") or "")
            # A pair the map's own line joins needs no line of its own.
            if o and o != r["symbol"] and not r["pair"].get("map"):
                r["join"] = list(tobase_direction(o, r["symbol"], base))
    return LS.state_text(name, broker, result)


def stage_listing_suffix(name: str, broker: str, csvs: List[Path],
                         root: Path, cache: Path) -> Path:
    """work/<name>_<broker>_listing_suffix.state: the symbols of this
    group whose listing suffix came from the row currency alone and that
    the project's other books name as the other listing (lib/
    listing_suffix, tax-logic CA-XLIST-02 / US-XLIST-02). Written only
    when it changes — it is a dep of the parse, which books every row of
    such a symbol under that listing (--listing-fixes)."""
    from taxjson.lib import listing_suffix as LS
    out = LS.state_path(cache, name, broker)
    run_state.current.listing_groups.setdefault(
        (cache.resolve(), name), {})[broker] = list(csvs)
    text = _listing_suffix_text(name, broker, csvs, root, cache)
    if _read_work_stamp(out) != text:
        _write_work_stamp(out, text)
    return out


def _listing_suffix_stale(root: Path, cache: Path) -> List[str]:
    """The accounts whose listing evidence changed after every account
    was parsed (an account parsed before the one holding its evidence)."""
    from taxjson.lib import listing_suffix as LS
    out: List[str] = []
    for (c, name), groups in sorted(run_state.current.listing_groups.items()):
        if c != cache.resolve():
            continue
        for broker, csvs in sorted(groups.items()):
            text = _listing_suffix_text(name, broker, csvs, root, cache)
            if _read_work_stamp(LS.state_path(cache, name, broker)) != text:
                out.append(name)
                break
    return out


def _ticker_map_named(root: Path, lookups: bool = False) -> frozenset:
    """Every symbol a rule of the project's ticker.map names
    (ticker_map_rules.named_symbols; `lookups`: a lookup line's symbol
    too); empty without a map. A map that does not parse is refused by
    `tjs run` up front."""
    tm = _PL.ticker_map_path(root)
    if not tm.is_file():
        return frozenset()
    from taxjson.lib.ticker_map_rules import named_symbols
    from taxjson.lib.map_file import _parse_map_file
    try:
        return named_symbols(_parse_map_file(tm)[0], lookups=lookups)
    except (OSError, ValueError):
        return frozenset()


def _ticker_map_renames(root: Path) -> Dict[str, str]:
    """The project's ticker.map renames as the base-currency merge books
    them (ticker_map_rules.merge_renames: GLOBAL, TOBASE, JOURNAL and
    undated RENAME lines, chains followed); empty without a map or when
    it does not parse (`tjs run` refuses such a map up front)."""
    tm = _PL.ticker_map_path(root)
    if not tm.is_file():
        return {}
    from taxjson.lib.ticker_map_rules import merge_renames
    from taxjson.lib.map_file import _parse_map_file
    try:
        return merge_renames(_parse_map_file(tm)[0], to_base=True)
    except (OSError, ValueError):
        return {}


def _accounts_with_symbol_codes(inputs_dir: Path,
                                accounts: Mapping[str, AccountSettings]) -> List[str]:
    """The equity accounts whose Questrade exports hold internal codes
    their own rows do not resolve: `tjs run` parses every other
    account first, so the inference sees their transfers and names."""
    from taxjson.lib.brokerages.detect import detect
    from taxjson.lib.brokerages.questrade import scan_code_uses
    out = []
    for n, c in accounts.items():
        if c.crypto:
            continue
        qt = []
        for csv in input_files(inputs_dir / n, ".csv"):
            try:
                if detect(csv).broker == "questrade":
                    qt.append(csv)
            except Exception:                      # noqa: BLE001
                continue    # the account's own stage reports the file
        try:
            if qt and scan_code_uses(qt):
                out.append(n)
        except Exception:                          # noqa: BLE001
            continue
    return out


def _transfer_key(root: Path, *, effective: bool = False):
    """The security a transfer row moves, after ticker.map's undated
    renames (the books are mapped by the merge stage; the transfer
    evidence is not). `effective`: the map the books were merged with,
    the run's effective map (ticker.map plus the listings the run
    joined by their transfer journal, lib/cross_listings) when it wrote
    one — the arrivals' pairing: with ticker.map alone, a journal the
    run joined itself (RBC ZZD to ZZD.U, IB ZZK.US to Questrade ZZK.TO)
    kept two keys, so its in leg was an arrival with NO cost whose
    advice (enter the purchase) held the shares twice (backlog
    TJ-0440)."""
    from taxjson.lib.project_layout import EFFECTIVE_MAP as _EFF
    tm = root / "work" / _EFF
    if not (effective and tm.is_file()):
        tm = _PL.ticker_map_path(root)
    renames: Dict[str, str] = {}
    if tm.is_file():
        try:
            from taxjson.lib.ticker_map_rules import merge_renames
            from taxjson.lib.map_file import load_map_file
            import contextlib as _cl
            import io as _io
            with _cl.redirect_stderr(_io.StringIO()):
                renames = merge_renames(load_map_file(tm), to_base=True)
        except (OSError, ValueError):
            renames = {}
    from taxjson.lib.ticker_map_rules import map_symbol

    def key(sym: str) -> str:
        s = str(sym or "").strip()
        return map_symbol(s, renames).upper() if renames else s.upper()
    return key


def transfer_arrivals(root: Path, cache: Path,
                      accounts: Optional[Dict[str, Any]] = None,
                      slow: Optional[List[Any]] = None,
                      not_own: Optional[List[Any]] = None,
                      moves: Optional[List[Any]] = None,
                      paired: Optional[List[Any]] = None):
    """[transfer_in.Arrival] of every taxable equity account (the
    shares that arrived by transfer from outside the books), each
    marked covered when its account's converted .tt rows account for
    it. Read from work/ (the sidecars and the converted .tt files).
    `slow`: given a list, the moves of your own slower than the window
    are appended to it (transfer_in.SlowMove; audit 2026-10-25
    diff-engine#4). The accounts' .tt NOTOWN lines each undo one
    pairing (transfer_in.NotOwn; backlog TJ-0075); `not_own`: given a
    list, every line read is appended to it, `matched` set on those
    that undid one. `moves`: given a list, every move of your own
    between two of the accounts the pairing cancelled — none a NOTOWN
    line undid (transfer_in.OwnMove; backlog TJ-0093). `paired`: given a
    list, the identity of every transfer-in the pairing cancelled
    (transfer_in.arrivals; backlog TJ-0308). A converted .tt file that
    is not a book of row objects is an InputFileError (a ValueError)
    naming it: the arrivals its rows cover cannot be told apart."""
    from taxjson.lib import transfer_in as TI
    own: List[Any] = moves if moves is not None else []
    found = _paired_transfers(root, cache, accounts, slow=slow,
                              not_own=not_own, moves=own, paired=paired)
    if not found:
        return found
    key = _transfer_key(root, effective=True)
    tt_rows: Dict[str, List[Dict[str, Any]]] = {}
    for n in sorted({a.account for a in found}):
        rows: List[Dict[str, Any]] = []
        for tt in input_files(_PL.inputs_dir(root) / n, ".tt"):
            p = tt_json_path(cache, n, tt.name)
            if not p.is_file():
                continue        # not converted yet: the run converts
                #                 each account's .tt files in turn
            # The strict work/ reader (backlog TJ-0308): a damaged file
            # (`[null]`, `{"transactions": 42}`, not JSON) was an
            # AttributeError or skipped in silence, and `tjs
            # transfers` then called each arrival a move of your own.
            rows += _read_work_doc(p).get("transactions") or []
        tt_rows[n] = rows
    # The declarations the converted .tt rows leave out (backlog
    # TJ-0822): the dated renames (applied downstream), and the booked
    # in-kind moves' taxable side with the broker rows a contribution
    # takes its shares from first.
    renames, ik, broker_rows = _coverage_declarations(
        root, cache, sorted(tt_rows))
    own_in: Dict[str, List[Tuple[str, str, float]]] = {}
    own_out: Dict[str, List[Tuple[str, str, float]]] = {}
    for m in own:
        own_in.setdefault(m.in_account, []).append(
            (m.in_date, m.in_symbol, m.quantity))
        own_out.setdefault(m.out_account, []).append(
            (m.out_date, m.out_symbol, m.quantity))
    TI.mark_covered(found, tt_rows, key=key, renames=renames,
                    broker_rows=broker_rows, in_kind=ik, own_in=own_in,
                    own_out=own_out)
    # A .tt OPENING cost=unknown line is the user's own answer for the
    # position (cost unknown, reported by hand): it wins over the
    # broker's book value.
    from taxjson.lib.first_run import read_missing_history_pairs
    TI.mark_missing_history(found, read_missing_history_pairs(root))
    return found


def _coverage_declarations(root: Path, cache: Path, accounts: List[str]):
    """(renames, in_kind, broker_rows) for transfer_in.mark_covered
    (backlog TJ-0822): the project's dated renames as (date, old, new)
    — the .tt RENAME lines and ticker.map's dated lines, merged as the
    run books them; per account in `accounts`, its booked in-kind moves'
    taxable side as (date, symbol, signed units) (work/in_kind.json:
    the last in-kind stage's), and its parsed broker rows (not its
    transfer rows; backlog TJ-1054). Nothing on a file it cannot read."""
    import json as _json
    renames: List[Tuple[str, str, str]] = []
    try:
        from taxjson.lib.dated_events import project_renames
        from taxjson.lib.renames import _ticker_map
        from taxjson.lib.rename_decl import KIND_SECURITIES
        tmap, _ren = _ticker_map(root)
        for ev in project_renames(root, None, tmap):
            if ev.applies_to(KIND_SECURITIES):
                renames.append((str(ev.date)[:10], str(ev.old),
                                str(ev.new)))
    except Exception:                               # noqa: BLE001
        renames = []                # the run names a line it cannot read
    ik: Dict[str, List[Tuple[str, str, float]]] = {}
    try:
        doc = _json.loads((cache / IN_KIND_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    for m in (doc.get("moves") if isinstance(doc, dict) else None) or []:
        if not (isinstance(m, dict) and m.get("booked")):
            continue
        leg = m.get("taxable_leg")
        acct = str(m.get("taxable") or "")
        if acct not in accounts or not (isinstance(leg, list)
                                        and len(leg) == 4):
            continue
        try:
            q = abs(float(m.get("quantity") or 0.0))
        except (TypeError, ValueError):
            continue
        from taxjson.lib.in_kind import CONTRIBUTION
        sign = -1.0 if m.get("kind") == CONTRIBUTION else 1.0
        ik.setdefault(acct, []).append(
            (str(m.get("date") or leg[2])[:10], str(leg[1]), sign * q))
    broker_rows: Dict[str, List[Dict[str, Any]]] = {}
    from taxjson.lib.cross_listings import _parsed_files
    for acct in accounts:
        rows: List[Dict[str, Any]] = []
        for _b, p, sidecar in _parsed_files(cache, acct):
            if sidecar or not p.is_file():
                continue
            try:
                d = _json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows += [t for t in (d.get("transactions") or []
                                 if isinstance(d, dict) else [])
                     if isinstance(t, dict) and t.get("action") != "TRANSFER"]
        broker_rows[acct] = rows
    return renames, ik, broker_rows


def own_move_plan(root: Path, cache: Path,
                  accounts: Optional[Dict[str, Any]] = None,
                  not_own: Optional[List[Any]] = None
                  ) -> List[Any]:
    """[transfer_in.OwnMove]: the moves of your own between two taxable
    share accounts — the ONE pairing of their transfer rows that every
    stage reads (backlog TJ-0269): the arrival booking (an in leg it
    pairs is no arrival), Canada's unit legs (CA-ACB-OWN-MOVE), the
    US lot carry (US-BASIS-05) and find-missing-history's purchase
    drafts (backlog TJ-0599) — the same call transfer_arrivals makes
    (_paired_transfers), so they can never choose different partners.
    None a .tt NOTOWN line undid; `not_own`: given a list, the NOTOWN
    lines read, `matched` set on those that undid one."""
    moves: List[Any] = []
    _paired_transfers(root, cache, accounts, moves=moves, not_own=not_own)
    return moves


def _paired_transfers(root: Path, cache: Path,
                      accounts: Optional[Dict[str, Any]] = None, *,
                      slow: Optional[List[Any]] = None,
                      not_own: Optional[List[Any]] = None,
                      moves: Optional[List[Any]] = None,
                      paired: Optional[List[Any]] = None):
    """The pairing of transfer_arrivals (its arguments), before the .tt
    coverage: [transfer_in.Arrival], none marked covered yet."""
    from taxjson.lib import transfer_in as TI
    accounts = typed_accounts(accounts if accounts is not None
                              else load_config(root, missing_ok=True).get("accounts"))
    _lines = TI.read_not_own(root, list(accounts))
    if not_own is not None:
        not_own.extend(_lines)
    names = [n for n, c in accounts.items()
             if c.taxable and not c.crypto and not c.transfers]
    if not names:
        return []
    key = _transfer_key(root, effective=True)
    # A leg any in-kind move names (booked or not: a US contribution in
    # kind never is) went to or came from a registered account: never a
    # slower move of your own (audit 2026-10-25 diff-engine#4).
    _ik_any = in_kind_taxable_legs(cache, every=True)
    # Nor, within the window, a move of your own at all: the in-kind
    # move's own leg (not its alternatives, which another move of your
    # own may pair), booked or not (backlog TJ-0230).
    _ik_leg = in_kind_taxable_legs(cache, every=True, legs_only=True)
    # Nor an arrival from outside your books: a booked withdrawal's in
    # leg is a purchase at fair market value, a contribution's out leg
    # cancels nothing, and a move NOT booked is the in-kind warning's,
    # with its INKIND line. Only the booked ones were left out: an
    # unbooked withdrawal's in leg was also a NO-cost arrival whose
    # advice (the sending plan's purchase) booked the wrong cost
    # (backlog TJ-0439).
    _ik = _ik_leg
    # The .tt JOURNAL legs too: a journal inside one account cancels
    # within its own pair (TI.own_journal_legs), with the broker's leg a
    # one-legged line stands beside — never another account's arrival
    # (pre-release review H1).
    return TI.arrivals([r for r in TI.sidecar_rows(cache, names,
                                                   declared=True)
                        if _leg_ident(r[0], r[2]) not in _ik], key=key,
                       slow=slow,
                       no_slow=lambda r: _leg_ident(r[0], r[2]) in _ik_any,
                       no_move=lambda r: _leg_ident(r[0], r[2]) in _ik_leg,
                       not_own=_lines, moves=moves, paired=paired,
                       apart=_map_distinct(root))


def _map_distinct(root: Path) -> Callable[[str, str], bool]:
    """(a, b) -> the user's ticker.map keeps the two symbols apart (a
    DISTINCT line, read as the map parser reads it): never one
    security's listing journal (backlog TJ-0821)."""
    pairs: Set[frozenset] = set()
    tm = _PL.ticker_map_path(root)
    if tm.is_file():
        try:
            from taxjson.lib.map_file import load_map_file
            import contextlib as _cl
            import io as _io
            with _cl.redirect_stderr(_io.StringIO()):
                pairs = set(load_map_file(tm).distinct)
        except (OSError, ValueError):
            pairs = set()

    def apart(a: str, b: str) -> bool:
        return frozenset((str(a).upper(), str(b).upper())) in pairs
    return apart


def stage_transfer_arrivals(name: str, root: Path, cache: Path,
                            tt_jsons: List[Path], country: str
                            ) -> Optional[Path]:
    """The acquisitions a taxable account's books take for shares that
    arrived by transfer at a STATED book value
    (work/<name>_transfer_costs.json, a source of the books; None when
    there are none), and one ATTENTION line per kind of uncovered
    arrival — every run, so a fixed one (a .tt line covering it) goes
    quiet. tt_jsons: this account's converted .tt files, already
    written."""
    from taxjson.lib import transfer_in as TI
    from taxjson.lib import tt_format as TTF
    out = cache / f"{name}{TRANSFER_COSTS_SUFFIX}"
    # In-kind moves to and from your registered accounts first: they
    # decide which transfer rows are not arrivals (lib/in_kind).
    _ik_rows = in_kind_state(root, cache, load_settings(root, missing_ok=True)).rows.get(name, [])
    # Canada: the moves of your own this pairing cancels between two
    # accounts, which the accounts' books carry (CA-ACB-OWN-MOVE,
    # backlog TJ-0093) — the same call that books the arrivals.
    from taxjson.lib.country import basis_pooled_across_accounts as _bp
    paired: Optional[List[Any]] = [] if _bp(country) else None
    slow: List[Any] = []
    not_own: List[Any] = []
    try:
        found = [a for a in transfer_arrivals(root, cache, slow=slow,
                                              not_own=not_own,
                                              moves=paired)
                 if a.account == name]
    except Exception as e:                          # noqa: BLE001
        _say("warning", f"transfer-ins not checked ({type(e).__name__}: "
             f"{e})", indent="  ")
        found, not_own = [], []
        paired = [] if paired is not None else None
    rows = TI.booked_rows(found) + _ik_rows
    # A .tt NOTOWN line that undid no pairing (backlog TJ-0075): said by
    # the account it is in, or, in an account whose transfer rows are
    # never paired, once by the first account checked; the books are as
    # without it.
    _accts = accounts_of(load_config(root, missing_ok=True))
    for n in not_own:
        if n.matched:
            continue
        why = TI.not_own_account_problem(n, _accts)
        if n.account != name and not why:
            continue
        _say_once(
            ("notown", n.where), "warning",
            f"{n.where}: this {TTF.NOTOWN_KEYWORD} line undoes no pairing, "
            f"so nothing changed",
            f"The line: `{n.line}`.",
            (f"It is ignored: {why}." if why else
             f"The run paired no transfer-in of {n.symbol} in "
             f"{n.account} on {n.date} with a transfer-out of the same "
             f"units from {n.from_account} on {n.from_date} as a move of "
             f"your own: the line undoes such a pairing (within "
             f"{TI.OWN_MOVE_DAYS} days, or a slower one the run says as "
             f"Info) and never makes one."),
            f"The form is `{TTF.NOTOWN_FORM}`: the dates, units and "
            f"accounts as `tjs transfers` lists the two rows. Fix "
            f"the line or delete it.",
            indent="  ")
    from taxjson.lib.country import basis_pooled_across_accounts
    if basis_pooled_across_accounts(country):
        _stage_pooled_own_moves(name, root, cache, paired or [], country)
        # A move of your own whose legs post further apart than the
        # window, paired because nothing else could be either leg's
        # partner (audit 2026-10-25 diff-engine#4): said by the
        # receiving account, both dates named. A US project says it on
        # the own-account move line that carries the lots.
        from taxjson.lib.country import COST_TERM
        _cost = COST_TERM.get(country, "cost")
        for m in slow:
            if m.in_account != name:
                continue
            _say("note", f"Own-account move: {m.text()}: not a purchase "
                 f"— the shares keep their {_cost}",
                 f"No other transfer of the security and quantity could "
                 f"be either leg's partner, so nothing is booked for the "
                 f"transfer-in (a book value on its row is not used). If "
                 f"they are not one move, add `{m.not_own_line()}` to a "
                 f".tt file in {_inputs_shown(root)}/{m.in_account}/: the "
                 f"transfer-in is "
                 f"then booked as one from outside your books "
                 f"(docs/troubleshooting.md; `tjs transfers` lists "
                 f"both rows).",
                 indent="  ", file=sys.stdout)
    for ln in TI.attention_lines(found, country):
        _echo_captured(f"{ATTENTION_PREFIX} {ln}")
    if not rows:
        out.unlink(missing_ok=True)
        return None
    import json as _json
    text = _json.dumps({"transactions": rows,
                        # No source_brokerage: the fees report counts
                        # broker trades only (these are not trades).
                        "metadata": {"kind": "transfer_book_value",
                                     "account": name}},
                       indent=2, sort_keys=True) + "\n"
    try:
        same = out.read_text(encoding="utf-8") == text
    except OSError:
        same = False
    if not same:
        write_text_atomic(out, text)
    return out


def _leg_ident(account: str, t: Dict[str, Any]) -> Tuple[str, str, str, float]:
    """A transfer row's identity (lib/in_kind.Leg.ident)."""
    try:
        q = M.units(float(t.get("quantity") or 0.0), 8)
    except (TypeError, ValueError):
        q = 0.0
    return (account, str(t.get("symbol") or ""),
            str(t.get("date") or "")[:10], q)


def in_kind_taxable_legs(cache: Path, every: bool = False, *,
                         legs_only: bool = False
                         ) -> Set[Tuple[str, str, str, float]]:
    """The taxable transfer rows the last run's booked in-kind moves
    account for (work/in_kind.json). `every`: the legs of every in-kind
    move, booked or not, and their alternatives — rows paired with a
    registered account's, never a slower move of your own between two
    taxable accounts (audit 2026-10-25 diff-engine#4); with `legs_only`
    not the alternatives: the rows never a move of your own at all
    (backlog TJ-0230)."""
    import json as _json
    try:
        doc = _json.loads((cache / IN_KIND_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return in_kind_leg_idents(doc.get("moves") if isinstance(doc, dict)
                              else None, every=every, legs_only=legs_only)


def in_kind_leg_idents(moves: Any, every: bool = False, *,
                       legs_only: bool = False
                       ) -> Set[Tuple[str, str, str, float]]:
    """The taxable legs of in-kind moves (lib/in_kind.Move.as_dict
    dicts): the booked ones', or with `every` every move's, their
    alternatives too unless `legs_only`."""
    out = set()
    for m in moves or []:
        if not isinstance(m, dict) or not (every or m.get("booked")):
            continue
        legs = [m.get("taxable_leg")] + (list(m.get("alternatives") or [])
                                         if every and not legs_only
                                         else [])
        for leg in legs:
            if not (isinstance(leg, list) and len(leg) == 4):
                continue
            try:
                out.add((str(leg[0]), str(leg[1]), str(leg[2]),
                         M.units(float(leg[3]), 8)))
            except (TypeError, ValueError):
                continue
    return out


class _InKindState:
    """The in-kind moves of one run (lib/in_kind): the moves, the
    INKIND-line problems, the taxable accounts' booked rows, and the
    transfer rows possibly moved in kind in parts (lib/in_kind.in_parts)."""

    def __init__(self, moves=None, problems=None, rows=None, parts=None):
        self.moves = list(moves or [])
        self.problems = list(problems or [])
        self.rows: Dict[str, List[Dict[str, Any]]] = dict(rows or {})
        self.parts = list(parts or [])


def _in_kind_plans(accounts: Mapping[str, Any], country: str) -> Dict[str, str]:
    """account -> "" (a taxable equity account whose transfer rows are
    kept out of the books) or its plan (a registered equity account)."""
    plans: Dict[str, str] = {}
    for n, c in typed_accounts(accounts).items():
        if c.crypto:
            continue
        if c.taxable:
            if not c.transfers:
                plans[n] = ""
        elif c.type in (None, "sheltered"):     # another type: not a plan
            plans[n] = _account_plan(n, c, country)
    return plans


def _registered_transfer_rows(names: List[str], cache: Path
                              ) -> List[Tuple[str, str, Dict[str, Any]]]:
    """(account, broker, TRANSFER row) of registered accounts: the
    transfer sidecars (transfers = false) and the parsed exports'
    in-book TRANSFER rows (transfers = true), both before the books'
    currency conversion — the .tt JOURNAL legs included (the caller
    settles each journal inside its account: TI.movable_rows)."""
    import json as _json
    from taxjson.lib import transfer_in as TI
    rows = list(TI.sidecar_rows(cache, names, declared=True))
    longer = sorted(names, key=len, reverse=True)
    for n in names:
        others = [o for o in longer if o != n and o.startswith(f"{n}_")]
        for p in sorted(cache.glob(f"{n}_*.json")):
            if any(p.name.startswith(f"{o}_") for o in others):
                continue
            try:
                doc = _json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            md = doc.get("metadata") if isinstance(doc, dict) else None
            # A broker parse (taxjson-brokerage's output) only.
            if (not isinstance(md, dict) or not md.get("format_version")
                    or not md.get("source_brokerage")
                    or md.get("kind")):
                continue
            broker = p.name[len(n) + 1:-len(".json")]
            for t in doc.get("transactions") or []:
                if (isinstance(t, dict) and t.get("action") == "TRANSFER"
                        and t.get("symbol") and t.get("account") == n):
                    rows.append((n, broker, t))
    return rows


def _inkind_lines(root: Path, names: List[str]
                  ) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    """((account, INKIND line), problems) from the .tt files of the
    taxable accounts `names`."""
    from taxjson.lib.tt_format import parse_inkind_line, tt_lines
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.cli_diag import read_text_utf8
    out: List[Tuple[str, Dict[str, Any]]] = []
    problems: List[str] = []
    for n in names:
        for tt in input_files(_PL.inputs_dir(root) / n, ".tt"):
            try:
                text = read_text_utf8(tt)
            except (OSError, ValueError):
                continue
            for i, line in enumerate(tt_lines(text), 1):
                if "INKIND" not in line:
                    continue
                try:
                    ln = parse_inkind_line(line, f"{shown_name(tt)}:{i}")
                except ValueError as e:
                    problems.append(f"{e} — ignored")
                    continue
                if ln is not None:
                    out.append((n, ln))
    return out, problems


def _in_kind_close(root: Path, cache: Path):
    """The close lookup lib/in_kind.value uses: Yahoo's close on the
    move's date through the price chain (ticker.map QUOTE spelling, else
    the derived one), cached in work/.close_cache.json; TAXJSON_OFFLINE
    serves the cache only."""
    from taxjson.lib import in_kind as IK
    from taxjson.lib import price_chain as PC
    try:
        quote = PC.load_yf_map([root])
    except Exception:                               # noqa: BLE001
        quote = {}

    def close(m):
        q = quote.get(m.key)
        if q is not None and abs(float(q[1]) - 1.0) > 1e-9:
            return None             # a merged ticker's ratio: not a close
        ysym = q[0] if q is not None else PC.yf_symbol_for(m.key)
        if not ysym or PC.is_crypto_symbol(m.key):
            return None
        try:
            return PC.close_on(m.key, ysym, m.date,
                               cache_path=cache / ".close_cache.json")
        except PC.OfflineCloseMissing as e:
            raise IK.CloseUnavailable(str(e))
    return close


def _inputs_shown(root: Path) -> str:
    """The exports folder as a repair line names it, relative to the
    project: `inputs`, or `../inputs` when every year shares one (a .tt
    line added to the year folder's own inputs/ is never read; backlog
    TJ-0827)."""
    try:
        return _PL.shown(_PL.inputs_dir(root), Path(root))
    except Exception:                               # noqa: BLE001
        return "inputs"


def in_kind_state(root: Path, cache: Path, settings: Settings
                  ) -> _InKindState:
    """The in-kind moves between your taxable and registered accounts
    (lib/in_kind; tax-logic CA-INKIND-* / US-INKIND-*), computed once per
    run from the transfer evidence in work/ (every equity account is
    parsed before any books are merged) and the taxable accounts' .tt
    INKIND lines; written to work/in_kind.json. A move that cannot be
    valued because TAXJSON_OFFLINE hides the close stops the run."""
    key_dir = cache.resolve()
    st = run_state.current.in_kind.get(key_dir)
    if st is not None:
        return st
    from taxjson.lib import in_kind as IK
    from taxjson.lib.brokerages.base import DISPLAY_NAMES
    cfg = load_config(root, missing_ok=True)
    accounts = accounts_of(cfg)
    country = _normalize_country(settings.country
                                 or settings_of(cfg).country or "canada")
    plans = _in_kind_plans(accounts, country)
    taxable = sorted(n for n, p in plans.items() if not p)
    registered = sorted(n for n, p in plans.items() if p)
    st = _InKindState()
    run_state.current.in_kind[key_dir] = st
    if not taxable:
        (cache / IN_KIND_FILE).unlink(missing_ok=True)
        return st
    from taxjson.lib import transfer_in as TI
    key = _transfer_key(root)
    rows = list(TI.sidecar_rows(cache, taxable, declared=True))
    if registered:
        rows += _registered_transfer_rows(registered, cache)
    # A journal inside one account (its legs' pair id, a .tt JOURNAL
    # line) is never an in-kind move (pre-release review H1).
    rows = TI.movable_rows(rows)
    legs = IK.legs(rows, plans, key=key)
    lines, problems = _inkind_lines(root, taxable)
    own, declared = IK.line_legs(lines, legs, key=key)
    pairing = IK.pair_all(legs, own=own, declared=declared)
    moves = pairing.moves
    refused: List[str] = []
    problems += IK.apply_lines(moves, lines, legs, key=key, refused=refused)
    st.parts = IK.in_parts(pairing)
    IK.decide(moves, country)
    IK.value(moves, _in_kind_close(root, cache),
             broker_names=DISPLAY_NAMES)
    st.moves, st.problems = moves, problems
    st.rows = IK.booked_rows(moves, country)
    import json as _json
    p = cache / IN_KIND_FILE
    if moves or problems or st.parts:
        text = _json.dumps({"metadata": {"kind": "in_kind_moves"},
                            "moves": [m.as_dict() for m in moves],
                            "problems": problems,
                            "in_parts": [
                                {"leg": list(g.ident()),
                                 "plan_legs": [list(h.ident())
                                               for h in hs]}
                                for g, hs in st.parts]},
                           indent=2, sort_keys=True) + "\n"
        try:
            same = p.read_text(encoding="utf-8") == text
        except OSError:
            same = False
        if not same:
            cache.mkdir(parents=True, exist_ok=True)
            write_text_atomic(p, text)
    else:
        p.unlink(missing_ok=True)
    offline = [m for m in moves if m.problem == "offline"]
    if offline:
        _die(f"{len(offline)} in-kind move(s) between your taxable and "
             f"registered accounts cannot be valued: TAXJSON_OFFLINE is "
             f"set and the close cache has no price for the date",
             *[f"{m.kind} {m.pair_text()}: {m.quantity:g} {m.symbol} on "
               f"{m.date} — add to a .tt file in {_inputs_shown(root)}/"
               f"{m.taxable.account}/: INKIND {m.date} {m.symbol} "
               f"{(-m.quantity if m.kind == IK.CONTRIBUTION else m.quantity):g} "
               f"{m.taxable.currency or 'CAD'} <price per share>"
               for m in offline],
             # An INKIND line refused (its price and total disagree,
             # backlog TJ-0933) is why a move has no value: said here.
             *problems,
             "Or unset TAXJSON_OFFLINE to look the close up (an "
             "estimate). docs/getting-started.md, step 5c.")
    if refused:
        # As a share's line stops the conversion; only the run knows an
        # option's contract size (backlog TJ-1163).
        _die(f"{len(refused)} INKIND line(s) refused: price and total "
             f"disagree at the contract's size", *refused,
             "A contract of another size: a ticker.map `MULT <root> <size>` "
             "line. Nothing was built.")
    return st


def stage_in_kind_context(root: Path, cache: Path, settings: Settings,
                          sheltered_base: Optional[Path]) -> Optional[Path]:
    """The loss-rule context (work/sheltered_base.json): each booked
    in-kind contribution's plan acquisition is a purchase for s.54 /
    §1091 whatever the transfer policy (CA-INKIND-04), and a withdrawal
    leg the context lacks is the plan's disposal (lib/in_kind.
    mark_sheltered). With no registered book in the project the context
    is written for those rows alone. Returns the context's path (None
    when there is none)."""
    from taxjson.lib import in_kind as IK
    st = in_kind_state(root, cache, settings)
    if not any(m.booked for m in st.moves):
        return sheltered_base
    import json as _json
    p = sheltered_base or (cache / "sheltered_base.json")
    if sheltered_base is None and p.exists():
        return None             # a previous run's context (--account)
    doc: Dict[str, Any] = {"transactions": []}
    if sheltered_base is not None:
        try:
            doc = _json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return sheltered_base
    rows = doc.get("transactions") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return sheltered_base
    n = IK.mark_sheltered(rows, st.moves, key=_transfer_key(root),
                          currency=str(settings.base_currency
                                       or "").upper())
    if not n:
        return sheltered_base
    write_text_atomic(p, _json.dumps(doc, indent=2, sort_keys=True))
    from taxjson.pipeline.dispatch import run_cmd as _run_cmd
    vres = _run_cmd(_cmd("taxjson-validate") + [str(p)],
                    capture_output=True)
    if vres.returncode != 0:
        _echo_stage_stderr((vres.stdout or "") + (vres.stderr or ""))
        _die("the sheltered book with the in-kind contributions marked "
             "failed validation (details above)",
             "Check the transfer rows `tjs transfers` labels "
             "in-kind, or value them with a .tt INKIND line.")
    return p


def spellings_checked(cache: Path, name: str, files: List[Path]
                      ) -> List[Path]:
    """`files` (account `name`'s parse outputs, and then those with its
    corporate-action outputs), once checked: the run stops when the
    exports it read spell one symbol two ways that differ only in letter
    case (`zyab` and `ZYAB`, `ZYXa` and `ZYXA`): in one file, across
    files, or between the trades and the corporate actions
    (lib/symbol_case, backlog TJ-4606). The books read a symbol in any
    case as one security, which would join two securities a broker
    spells apart only by a letter's case; a ticker.map line says which
    they are (GLOBAL: one; DISTINCT: two). `files` are read with those of
    every account this run checked in `cache` before it: the
    superficial-loss rule reads every account. The parse outputs are
    checked before the corporate-action stage, which reads them too
    (`_checked_outputs`): a run that stops on two spellings has saved no
    election under the id one spelling gives (backlog TJ-4616)."""
    from taxjson.lib.symbol_case import read_sources, refusal
    run_state.current.broker_sources[(cache, name)] = list(files)
    said = refusal(read_sources(_checked_outputs(cache)))
    if said:
        _die(said[0], *said[1])
    return list(files)


def _checked_outputs(cache: Path) -> List[Tuple[str, Path]]:
    """(account, output) of each parse and corporate-action output this
    run checked in `cache` (`spellings_checked`)."""
    return [(acct, p) for (work, acct), paths
            in run_state.current.broker_sources.items() if work == cache
            for p in paths]


def _say_listing_spellings(root: Path) -> None:
    """The ticker.map lines that pair a bare ticker with a share listing
    where the books hold the ticker's US listing, not the bare symbol
    (ticker_map_rules.listing_spelling_notes): a DISTINCT line also
    keeps the US listing apart (Info); a rename-type line joins nothing
    (Warning naming the line to write). Advisory: never fatal."""
    tm = _PL.ticker_map_path(root)
    if not tm.is_file():
        return
    try:
        from taxjson.lib.ticker_map_rules import listing_spelling_notes
        from taxjson.lib.cli_diag import read_text_utf8
        from taxjson.lib.ticker_map_suggest import books_symbols
        infos, warns = listing_spelling_notes(
            read_text_utf8(tm), tm.name, books_symbols(root))
    except Exception:                               # noqa: BLE001
        return
    for m in infos:
        _say_once(("spelling", m), "note", m, prog=_PROG)
    for m in warns:
        _say_once(("spelling", m), "warning", m, prog=_PROG)


def _say_tobase_map(root: Path, ticker_map: Path) -> None:
    """What the run reads from tobase.map (lib/tobase_map, tax-logic
    CA-XLIST-06 / US-XLIST-05), said once: the pairs ticker.map decides
    otherwise (Info, each with the line that wins), a tobase.map with no
    ticker.map beside it (not read: Warning), a US project's tobase.map
    (not read: Info). Never fatal (a line it cannot use is a ticker.map
    problem, refused with the others)."""
    from taxjson.lib import tobase_map as TB
    tpath = TB.tobase_path(root)
    if not tpath.is_file():
        return
    if not ticker_map.is_file():
        _say("warning", f"{TB.TOBASE_MAP} is not read: there is no "
             f"ticker.map beside it",
             "Create an empty ticker.map (`tjs update-tobase-map "
             "--write` makes one) so the interlisted pairs apply.",
             prog=_PROG)
        return
    try:
        from taxjson.lib.cli_diag import read_text_utf8
        ov = TB.overlay_for(ticker_map, read_text_utf8(ticker_map))
    except (OSError, ValueError):
        return
    if ov is None:
        return
    if ov.ignored_country:
        _say("note", f"{TB.TOBASE_MAP} is not read in a US project",
             "The interlisted pairs apply to Canadian projects only for "
             "now (tax-logic US-XLIST-05); write a pair you need in "
             "ticker.map.", prog=_PROG)
        return
    if ov.overridden:
        _say("note", f"{TB.TOBASE_MAP}: ticker.map decides "
             f"{len(ov.overridden)} of its pair(s) otherwise (ticker.map "
             f"wins)",
             *[f"- {rule} not applied ({why})"
               for rule, why in ov.overridden],
             "Delete the ticker.map line to use the master's pair, or "
             "keep it: `tjs update-tobase-map` lists these too.",
             prog=_PROG)
    if ov.internal:
        _say("note", f"{TB.TOBASE_MAP}: {len(ov.internal)} of its line(s) "
             f"not applied: another {TB.TOBASE_MAP} line decides the same "
             f"listing",
             *[f"- {rule} not applied ({why})"
               for rule, why in ov.internal],
             f"Usually a line you edited beside the master's: keep one of "
             f"the two (`tjs update-tobase-map` lists your edits).",
             prog=_PROG)


def _say_tobase_until(root: Path) -> None:
    """One Warning per ended interlisting traded after its tobase.map
    `until` date (lib/tobase_map.until_findings). Advisory."""
    from taxjson.lib import tobase_map as TB
    if not TB.tobase_path(root).is_file():
        return
    if TB.project_country(root) != "canada":
        return
    try:
        found = TB.until_findings(root)
    except Exception:                               # noqa: BLE001
        return
    for f in found:
        head, details = TB.until_message(f)
        _say_once(("tobase-until", f.symbol, f.until), "warning", head,
                  *details, prog=_PROG)


def _say_xlist_losses(root: Path, cfg: Dict[str, Any], cache: Path, *,
                      strict: bool = False) -> None:
    """A loss on one listing and a purchase of another listing of the
    same root under an equal name within 30 days (lib/xlist_loss_radar,
    tax-logic CA-XLIST-05 / US-XLIST-04): one Warning per pair naming the
    TOBASE and DISTINCT lines that answer it, the findings written to
    work/xlist_loss_radar.state (`ticker-map --suggest`);
    `strict`: a pair the map does not answer stops the run. Advisory: a
    failure to read the books is never fatal."""
    from taxjson.lib import xlist_loss_radar as XR
    from taxjson.lib import xlist_state as XST
    from taxjson.lib.country import settings_country
    country = settings_country(settings_of(cfg))
    try:
        found = XR.analyze(root, cfg)
    except Exception as e:                          # noqa: BLE001
        _say("warning", f"the cross-listing loss check failed: {e}",
             prog=_PROG)
        return
    state = cache / XST.STATE
    text = XR.state_text(found, country)
    if _read_work_stamp(state) != text:
        _write_work_stamp(state, text)
    if not found:
        return
    for f in found[:XR.RADAR_SHOWN]:
        head, details = XR.message(f.record(), country)
        _say_once(("xlist-loss", f.loss_symbol, f.other_symbol), "warning",
                  head, *details, indent="  ", file=sys.stdout,
                  cmd="tjs ticker-map --suggest")
    if len(found) > XR.RADAR_SHOWN:
        # The rest counted in one line (third pre-release review,
        # finding 9: one Warning per pair flooded the console).
        head, details = XR.more_message(len(found) - XR.RADAR_SHOWN,
                                        country)
        _say_once(("xlist-loss-more",), "warning", head, *details,
                  indent="  ", file=sys.stdout)
    if strict:
        _die(f"--strict: {len(found)} possible "
             f"{XST._kind(country, len(found))} across listings (warning "
             f"above) — aborting",
             "Add the TOBASE line (one security) or the DISTINCT line (two) "
             "to ticker.map.")


def _say_similar_losses(root: Path, cfg: Dict[str, Any], cache: Path
                        ) -> None:
    """A loss on a fund with a fund ticker.map declares SIMILAR bought
    inside its window (lib/similar, tax-logic CA-SL-20 / US-WASH-26):
    one Warning per pair, the findings written to work/similar.state
    (the checklist's wash-reviewed step). Nothing is denied and
    `--strict` never stops for one: the line asks for the pair to be
    watched, the decision is the user's. Advisory: a failure to read
    the books is never fatal."""
    from taxjson.lib import similar as SIM
    from taxjson.lib.country import settings_country
    country = settings_country(settings_of(cfg))
    try:
        found = SIM.analyze(root, cfg)
    except Exception as e:                          # noqa: BLE001
        _say("warning", f"the similar-funds check failed: {e}", prog=_PROG)
        return
    state = cache / SIM.STATE
    text = SIM.state_text(found, country)
    if _read_work_stamp(state) != text:
        _write_work_stamp(state, text)
    for f in found[:SIM.SHOWN]:
        head, details = SIM.message(f.record(), country)
        _say_once(("similar-loss", f.loss_symbol, f.other_symbol),
                  "warning", head, *details, indent="  ", file=sys.stdout,
                  short=f"similar fund in a loss's window: {f.loss_symbol} "
                        f"sold at a loss, {f.other_symbol} bought "
                        f"(SIMILAR) — `tjs run --details`")
    if len(found) > SIM.SHOWN:
        head, details = SIM.more_message(len(found) - SIM.SHOWN, country)
        _say_once(("similar-loss-more",), "warning", head, *details,
                  indent="  ", file=sys.stdout)


def _checklist_answered(root: Path, year: Any, step: str
                        ) -> Optional[Tuple[str, str, frozenset]]:
    """(mark, note, the question keys a DONE mark recorded) of a done or
    forced mark on checklist step `step` for this year in
    checklist.json, None when there is no such mark (or the file cannot
    be read): a question the user answered there (option-boundary's
    transition question, export-coverage) is said as a note, not a
    Warning — a DONE mark answers the keys it recorded only
    (checklist.question_answers); a forced mark accepts them all."""
    from taxjson.lib import checklist as _cl
    try:
        st = _cl.load_state(root)
    except _cl.StateFileError:
        return None
    if st.get("year") not in (None, year):
        return None
    ov = (st.get("overrides") or {}).get(step) or {}
    kind = _cl.mark_kind(ov)
    if kind is None:
        return None
    keys = frozenset(str(k) for k in (ov.get("answers") or [])
                     if isinstance(k, str))
    return kind, str(ov.get("note") or ""), keys


def _mark_answers(mark: Optional[Tuple[str, str, frozenset]],
                  key: str) -> bool:
    """A checklist mark answers the question `key`: a forced mark accepts
    every question, a DONE mark the keys it recorded."""
    from taxjson.lib.checklist import MARK_FORCED
    return mark is not None and (mark[0] == MARK_FORCED or key in mark[2])


def _say_option_transition(root: Path, cfg: Dict[str, Any]) -> None:
    """Contracts written before `option_grant_timing_since` and closed in
    the project year, with no lock saying how their write year was
    filed (lib/option_boundary.project_question_rows, CA-OPT-11): their
    premium is in this year's gain, right only if that return did not
    report it. One Warning with the premium at stake and the question;
    a note once checklist.json marks option-boundary done (the answer)."""
    from taxjson.lib import option_boundary as OB
    from taxjson.lib.pipeline import option_timing_from_settings
    settings = settings_of(cfg)
    try:
        rows = OB.project_question_rows(root, cfg)
        since = (option_timing_from_settings(settings) or {}).get(
            "option_grant_since")
    except Exception as e:                          # noqa: BLE001
        _say("warning", f"the option grant-timing check failed: {e}",
             prog=_PROG)
        return
    if not rows:
        return
    year = settings.year
    from taxjson.lib.checklist import option_keys
    mark = _checklist_answered(root, year, OB.QUESTION_STEP)
    # A DONE mark answers the contracts it recorded; one it did not (a
    # contract the books gained since) is asked again.
    done = [r for r in rows if _mark_answers(mark, option_keys([r])[0])]
    asked = [r for r in rows if r not in done]
    if done:
        head, _details = OB.question_message(done, int(year), since)
        _ans = (f"answered in checklist.json (option-boundary marked "
                f"{mark[0]}{': ' + mark[1] if mark[1] else ''})")
        _say_once(("option-transition", "answered"), "note",
                  f"{head} — {_ans}", indent="  ", file=sys.stdout,
                  short=f"{len(done)} option contract(s) on transition "
                        f"close timing: {_ans}")
    if asked:
        head, details = OB.question_message(asked, int(year), since)
        if done:
            details = details + [
                "checklist.json's option-boundary mark answered other "
                "contracts; mark it done again once these are answered."]
        _tot = sum(float(r.get("premium") or 0.0) for r in asked)
        _wys = "/".join(sorted({str(r["write_year"]) for r in asked}))
        _say_once(("option-transition",), "warning", head, *details,
                  indent="  ", file=sys.stdout,
                  short=f"{len(asked)} option contract(s) written in "
                        f"{_wys}: is {M.shown(_tot, 2):,.2f} of premium taxed in "
                        f"{year} right? — `tjs checklist --run`")


def _say_export_coverage(root: Path, cfg: Dict[str, Any]) -> None:
    """A broker whose exports for an account end before the tax year's
    end (before today in the running year) while it still holds
    positions there (lib/export_coverage): one Warning per broker and
    account, a note once checklist.json marks export-coverage done."""
    from taxjson.lib import export_coverage as EC
    try:
        gaps = EC.find_gaps(root, cfg)
    except Exception as e:                          # noqa: BLE001
        _say("warning", f"the export-coverage check failed: {e}",
             prog=_PROG)
        return
    if not gaps:
        return
    year = settings_of(cfg).year
    mark = _checklist_answered(root, year, EC.STEP)
    for g in gaps:
        # Per end too: two accounts of one broker in a combined account
        # stop on different days (backlog TJ-0930).
        key = ("export-coverage", g.account, g.broker, g.end)
        _where = (f"{EC.broker_name(g.broker)} exports for {g.account} "
                  f"end {g.end}")
        if g.info:
            # Every position open at the end was closed by .tt lines.
            _say_once(key, "note", EC.info_message(g), indent="  ",
                      file=sys.stdout,
                      short=f"{_where}: the positions then open were "
                            f"closed by .tt lines")
            continue
        head, details = EC.message(g, int(year))
        _n_open = len(g.positions or ()) + len(g.expired or ())
        # A DONE mark answers the gaps it recorded (account, broker,
        # end — the user knows the broker had no later activity there);
        # a new gap or a later end asks again. A skip accepts them all.
        if _mark_answers(mark, g.key):
            _marked = (f"marked {mark[0]} in checklist.json ({EC.STEP}"
                       f"{': ' + mark[1] if mark[1] else ''})")
            _say_once(key, "note", f"{head} — {_marked}", indent="  ",
                      file=sys.stdout, short=f"{_where}: {_marked}")
            continue
        from taxjson.lib.out import width as _ow
        _say_once(key, "warning", head, *details, indent="  ",
                  file=sys.stdout,
                  short=(head if len("Warning: " + head) <= _ow(sys.stdout)
                         else f"{_where} with {_n_open} position(s) open: "
                              f"download the rest of {year}"))


def _say_in_kind(root: Path, cache: Path, settings: Settings, *,
                 strict: bool = False) -> None:
    """ONE warning per run: each in-kind move booked (accounts, symbol,
    quantity, date, value and its source, the gain or the denied loss
    from the books) and each one not booked (CA-INKIND-* /
    US-INKIND-*). `strict`: a move not booked stops the run."""
    st = run_state.current.in_kind.get(cache.resolve())
    if st is None:
        return
    from taxjson.lib import in_kind as IK
    pm = IK.parts_message(st.parts, _inputs_shown(root))
    if pm is not None and ("inkind-parts",) not in run_state.current.shown:
        run_state.current.shown.add(("inkind-parts",))
        _say("warning", pm[0], *pm[1], indent="  ", file=sys.stdout)
        if strict:
            _die(f"--strict: {len(st.parts)} transfer row(s) of a taxable "
                 f"account possibly in-kind in parts (warning above)",
                 "Answer each with a .tt INKIND line (the move's value, "
                 "or plan=own); nothing else was changed.")
    if ("inkind",) in run_state.current.shown:
        return
    key = _transfer_key(root)
    docs, refused = _checked_gains_docs(cache)
    res = IK.contribution_results(docs, key=key)
    msg = IK.message(st.moves, st.problems, res,
                     _normalize_country(settings.country),
                     str(settings.base_currency or "").upper(),
                     _inputs_shown(root))
    if msg is None:
        return
    run_state.current.shown.add(("inkind",))
    for why in refused:     # said with the results it leaves out (TJ-3061)
        _say("warning", "the in-kind results below leave a gains file out",
             why, indent="  ", file=sys.stdout)
    _say("warning", msg[0], *msg[1], indent="  ", file=sys.stdout)
    _unbooked = [m for m in st.moves if not m.booked]
    if strict and (_unbooked or st.problems):
        _die(f"--strict: {len(_unbooked) + len(st.problems)} in-kind "
             f"move(s) or INKIND line(s) not booked (warning above)",
             "Value each with a .tt INKIND line, or fix the transfer "
             "rows; nothing else was changed.")


def _custody_pairing(root: Path, cache: Path,
                     accounts: Optional[Dict[str, Any]] = None
                     ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(moves, partial): the moves of a security between two of your
    taxable share accounts whose lots the US books carry (US-BASIS-05)
    — own_move_plan's, the pairing that also decides which transfer-ins
    came from outside the books (backlog TJ-0269: the carry had a
    pairing of its own, the first in leg of the same quantity in file
    order, which could send the lots to an account whose transfer-in
    the arrival stage had booked from outside, and leave the real
    receiver short). Each move: `symbol` (the security after
    ticker.map), `qty`, `from`, `to`, `date_out`, `date_in`, and `slow`
    (the days between its legs) for a move slower than the window.
    `partial`: the moves that took part of a transfer row (the rows'
    quantities do not pair; a delivery in two parts takes all of each),
    with `out_left` (the units of the out row that stay with the
    sender) and `in_left` (of the in row: an arrival from outside)."""
    moves: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []
    for m in own_move_plan(root, cache, accounts):
        mv: Dict[str, Any] = {
            "symbol": m.key, "qty": float(m.quantity),
            "from": m.out_account, "to": m.in_account,
            "date_out": m.out_date, "date_in": m.in_date}
        if m.slow:
            mv["slow"] = m.slow
        moves.append(mv)
        if m.out_left > 1e-9 or m.in_left > 1e-9:
            partial.append(dict(mv, out_left=m.out_left,
                                in_left=m.in_left))
    return moves, partial


def own_account_custody_moves(names: List[str], cache: Path,
                              root: Optional[Path] = None
                              ) -> List[Dict[str, Any]]:
    """The moves of a security between two of these taxable accounts
    whose lots a US project's books carry (_custody_pairing). `root`
    (default `cache`): the project whose ticker.map and .tt NOTOWN lines
    the pairing reads."""
    return _custody_pairing(Path(root or cache), cache,
                            {n: {"type": "taxable"} for n in names})[0]


OWN_MOVES_SUFFIX = "_own_moves.json"


def _move_legs(moves: List[Dict[str, Any]], currency: str = "",
               note: str = "basis and purchase dates carried"
               ) -> Dict[str, List[Dict]]:
    """{account: [TRANSFER lot_move rows]}: each move as an out leg in
    the sender and an in leg in the receiver at the same moment (the
    earlier of the two dates; securities at the start of that day), the
    pair told apart from any other by its numbered description (`note`
    closes it). A move's `symbol_out` / `symbol_in` and `currency_out` /
    `currency_in`, when given, are each leg's own (its transfer row's)."""
    from taxjson.lib.row_types import LOT_MOVE_TYPE
    out: Dict[str, List[Dict]] = {}
    for k, m in enumerate(sorted(
            moves, key=lambda m: (m["date"], m.get("time") or "",
                                  m["symbol"], m["from"], m["to"],
                                  m["qty"])), 1):
        # A move's `signed_by` (how its rows' brokers sign a transfer row,
        # transfer_in.OwnMove) is carried after the note: `; rows signed by
        # direction`, which the engine reads a written option's move by
        # (backlog TJ-3243).
        _sb = m.get("signed_by") or ""
        desc = (f"own-account move #{k}: {m['from']} -> {m['to']} "
                f"({note}{'; rows signed by ' + _sb if _sb else ''})")
        for acct, q, side in ((m["from"], -m["qty"], "out"),
                              (m["to"], m["qty"], "in")):
            out.setdefault(acct, []).append({
                "action": "TRANSFER", "type": LOT_MOVE_TYPE,
                "date": m["date"], "date_settle": m["date"],
                "time": m.get("time") or "00:00:00",
                "symbol": m.get(f"symbol_{side}") or m["symbol"],
                "quantity": float(q), "price": 0.0, "net_amount": 0.0,
                "currency": m.get(f"currency_{side}") or currency,
                "account": acct, "description": desc})
    return out


def _stage_pooled_own_moves(name: str, root: Path, cache: Path,
                            paired: List[Any], country: str) -> None:
    """Canada (tax-logic CA-ACB-OWN-MOVE): the moves of your own between
    two taxable accounts that the transfer-in pairing cancelled
    (transfer_in.OwnMove), as this account's lot-move legs in
    work/<name>_own_moves.json, a source of its books. The shares
    leave the sender and join the receiver at the start of the earlier
    leg's day, so every per-account view (`list`, `sanity`,
    `find-missing-history`, the checklist) sees the receiver hold them
    (backlog TJ-0093). The ACB is one pool across the accounts (s.47):
    the engine books no leg — no disposition, no acquisition, no figure
    moves. A leg the pairing left stays the transfer in or out it is.
    Written only when it changes (a declared input)."""
    from taxjson.lib.country import home_currency
    moves = [{"symbol": m.key, "qty": float(m.quantity),
              "from": m.out_account, "to": m.in_account,
              "date": min(m.out_date, m.in_date), "time": "00:00:00",
              "symbol_out": m.out_symbol, "symbol_in": m.in_symbol,
              "currency_out": m.out_currency.upper(),
              "currency_in": m.in_currency.upper(),
              "signed_by": getattr(m, "signed_by", "")}
             for m in paired]
    # A leg's currency is its transfer row's; the base currency only
    # for a row that names none (the caller's country: never re-read).
    legs = (_move_legs(moves, str(load_settings(root, missing_ok=True).base_currency
                                  or home_currency(country)).upper(),
                       note="not a sale: the ACB stays one pool").get(name)
            if moves else None)
    p = cache / f"{name}{OWN_MOVES_SUFFIX}"
    if not legs:
        p.unlink(missing_ok=True)
        return
    import json as _json
    text = _json.dumps({"transactions": legs,
                        "metadata": {"kind": "own_account_moves",
                                     "account": name}},
                       indent=2, sort_keys=True) + "\n"
    try:
        same = p.read_text(encoding="utf-8") == text
    except OSError:
        same = False
    if not same:
        cache.mkdir(parents=True, exist_ok=True)
        _write_atomic(p, text, encoding="utf-8")


def stage_own_account_moves(root: Path, cfg: Dict[str, Any],
                            settings: Settings, cache: Path, *,
                            strict: bool = False) -> List[Dict[str, Any]]:
    """US projects (tax-logic US-BASIS-05, US-CRYPTO-05): a move between
    two of your own TAXABLE accounts is not a sale — the moved lots keep
    their basis and purchase dates, and the US books keep lots per
    account, so the run carries them. Securities pair from the transfer
    sidecars, by the pairing that also decides which transfer-ins came
    from outside the books (own_move_plan; backlog TJ-0269); coins from
    the crypto-sends pairing. Each account gets
    work/<account>_own_moves.json, a source of its books: the TRANSFER
    legs the US engine books as a lot move (the blended pass hands the
    sender's FIFO lots to the receiver). A move that takes part of a
    transfer row (the quantities do not pair) is said as ATTENTION
    (--strict stops). Canada pools the ACB across the accounts (s.47):
    no lots to carry, nothing to say; its share accounts' legs, which
    move units only, are stage_transfer_arrivals' (CA-ACB-OWN-MOVE), and
    its crypto accounts' are this stage's: the same legs, units only,
    from the crypto-sends pairing, so each exchange's own books (`list`,
    `sanity`, `find-missing-history`) see the receiver hold the coins
    (backlog TJ-0281); the blended crypto pass books none. Returns the
    moves."""
    from taxjson.lib.country import basis_pooled_across_accounts
    accounts = accounts_of(cfg)
    taxable = [n for n, c in accounts.items()
               if c.taxable and not c.transfers]
    moves: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []
    pooled = basis_pooled_across_accounts(_country(settings))
    equity = [n for n in taxable if not accounts[n].crypto]
    crypto = [n for n in taxable if accounts[n].crypto]
    if not pooled:
        if len(equity) >= 2:
            # The in-kind moves to and from your retirement accounts
            # first (every account's evidence is parsed by now;
            # work/in_kind.json): their legs never pair as a move of
            # your own.
            in_kind_state(root, cache, settings)
            # One pairing with the arrival stage's (backlog TJ-0269); a
            # pairing a .tt NOTOWN line undoes carries no lots (backlog
            # TJ-0075).
            try:
                em, partial = _custody_pairing(root, cache, accounts)
            except Exception as e:                  # noqa: BLE001
                _say("warning", f"moves between your own accounts not "
                     f"paired ({type(e).__name__}: {e})",
                     "No lots are carried between them: the receiving "
                     "account's sales read as a short.", indent="  ")
                em = []
            for m in em:
                m["date"] = min(m["date_out"][:10], m["date_in"][:10])
                m["time"] = "00:00:00"
            moves += em
    if len(crypto) >= 2:
        from taxjson.lib import crypto_sends as CS
        try:
            moves += CS.own_moves(root, cfg, crypto,
                                  _crypto_broker_files(root, cfg))
        except ValueError as e:
            _say("warning", f"crypto moves between your accounts not "
                 f"paired: {e}", indent="  ")
    moves = _mapped_moves(moves, root)
    from taxjson.lib.country import home_currency
    legs = _move_legs(moves, str(settings.base_currency
                                 or home_currency(_country(settings))
                                 ).upper(),
                      **({"note": "not a sale: the ACB stays one pool"}
                         if pooled else {}))
    import json as _json
    for n in accounts:
        p = cache / f"{n}{OWN_MOVES_SUFFIX}"
        if (n in taxable and not accounts[n].crypto and pooled):
            continue    # Canada: stage_transfer_arrivals writes these
        rows = legs.get(n) if n in taxable else None
        if not rows:
            if p.exists():
                p.unlink()
            continue
        text = _json.dumps({"transactions": rows,
                            "metadata": {"kind": "own_account_moves",
                                         "account": n}},
                           indent=2, sort_keys=True) + "\n"
        try:
            same = p.read_text(encoding="utf-8") == text
        except OSError:
            same = False
        if not same:
            cache.mkdir(parents=True, exist_ok=True)
            _write_atomic(p, text, encoding="utf-8")
    from taxjson.lib.out import width as _ow
    from taxjson.lib.transfer_in import not_own_text, slow_dates
    # Canada carries no lots: crypto-sends says each pairing.
    for m in ([] if pooled else moves):
        # A move slower than the window names both legs' dates (audit
        # 2026-10-25 diff-engine#4).
        _when = (slow_dates(m["date_out"], m["date_in"], m["slow"])
                 if m.get("slow") else m["date"])
        _head = (f"own-account move: {m['symbol']} {m['qty']:g} "
                 f"{m['from']} -> {m['to']} ({_when}): not a sale")
        _rest = f"the lots' basis and purchase dates go to {m['to']}."
        if m.get("slow"):
            # The line that undoes a slow pairing (backlog TJ-0075).
            _no = not_own_text(m["date_in"], m["symbol"], m["qty"],
                               m["from"], m["date_out"])
            _rest += (f" If they are not one move, add `{_no}` to a .tt "
                      f"file in {_inputs_shown(root)}/{m['to']}/ "
                      f"(docs/troubleshooting.md).")
        if _ow(sys.stdout) > 0:
            # The run's console: a message, one line without --details,
            # the rest with it (backlog TJ-0270).
            _say("note", _head[:1].upper() + _head[1:],
                 _rest[:1].upper() + _rest[1:], file=sys.stdout)
            continue
        _mv = f"{_head} — {_rest}"
        # A live exact Doc prints each line as the raw print() did.
        d = _outlib.Doc(exact=True, width_=0, live=True)
        for _ln in _out_wrap(_mv, indent="  ", hang="    "):
            d.line(_ln)
    for u in partial:
        # The pairing took part of a row (backlog TJ-0269): the lots of
        # the units it paired are carried, as the transfer-ins read.
        _rest = ""
        if u["out_left"] > 1e-9:
            _rest += (f" The other {u['out_left']:g} of the transfer-out "
                      f"stay with {u['from']}: a transfer-out never leaves "
                      f"the books.")
        if u["in_left"] > 1e-9:
            _rest += (f" The other {u['in_left']:g} of the transfer-in "
                      f"came from outside your books: its ATTENTION line "
                      f"says what is booked.")
        _say("attention", f"{u['symbol']}: {u['qty']:g} moved out of "
             f"{u['from']} ({u['date_out']}) into {u['to']} "
             f"({u['date_in']}), but the quantities do not pair",
             f"The pairing took {u['qty']:g} of the units as one move of "
             f"your own: {u['from']}'s lots for them go to {u['to']}."
             + _rest + " Check the transfer rows (`tjs transfers`) "
             "and the sales of those shares (US-BASIS-05).", indent="  ")
    if partial and strict:
        _die(f"--strict: {len(partial)} transfer(s) between your own "
             f"taxable accounts whose quantities do not pair (warning "
             f"above) — aborting")
    return moves


def _mapped_moves(moves: List[Dict[str, Any]], root: Path
                  ) -> List[Dict[str, Any]]:
    """The moves' symbols through ticker.map's undated renames (the
    books are mapped by the merge stage; the transfer evidence is not)."""
    tm = _PL.ticker_map_path(root)
    if not moves or not tm.is_file():
        return moves
    try:
        from taxjson.lib.ticker_map_rules import map_symbol, merge_renames
        from taxjson.lib.map_file import load_map_file
        import contextlib as _cl
        import io as _io
        with _cl.redirect_stderr(_io.StringIO()):
            renames = merge_renames(load_map_file(tm), to_base=True)
    except (OSError, ValueError):
        return moves
    return [dict(m, symbol=map_symbol(m["symbol"], renames)) for m in moves]


def stage_blended_wash_pass(names: List[str],
                            settings: Settings, cache: Path,
                            reports_dir: Path,
                            sheltered_base: Optional[Path],
                            incomplete_history: Optional[Path] = None,
                            tag: str = "blend",
                            no_wash: bool = False,
                            spot_crypto: bool = False,
                            strict: bool = False
                            ) -> None:
    """ONE combined gains run over every taxable equity account, split
    back into the per-account `<name>_gains_wash.json` artifacts.
    `tag="cryptoblend"` runs the same pass over a Canadian project's
    crypto accounts (ITA s.47 averaging and the superficial-loss rule
    reach identical crypto held on different exchanges) with its own
    dot-prefixed intermediates.

    This is what makes the canonical (filed-from) numbers correct for
    multi-account books: Canada's ACB blends across all non-registered
    accounts (ITA s.47 — the engine's symbol-global pools do this
    naturally on a combined input) and US §1091 wash matching spans
    accounts while FIFO basis stays per account (--per-account-basis).
    Single-account projects produce identical numbers by construction.
    The per-account `<name>.sum` stays the isolated pre-blend baseline —
    comparing the pair shows exactly what blending changed."""
    _us = _normalize_country(settings.country) in ("us", "usa")
    _what = "crypto" if tag != "blend" else "taxable"
    if no_wash:
        _step(f"Carrying lots across {_what} accounts ({', '.join(names)})")
    elif _us:
        _step(f"Checking wash sales across {_what} accounts "
              f"({', '.join(names)})")
    else:
        _step(f"Pooling cost and checking superficial losses across "
              f"{_what} accounts ({_names_phrase(names)})")
    # Dot-prefixed intermediates: pathlib globs DO match leading dots
    # (`*_base.json` matches `.blend_base.json`), so every discovery
    # site — resolve_gains_files and the radar/missing-history fallback
    # globs — must ALSO filter `startswith(".")` explicitly. A visible
    # name here (or a glob site without the dot filter) would be
    # discovered as a spurious account and every aggregate would
    # double-count.
    _ctx = ST.StageCtx(work=cache, settings=settings, tag=tag, members=names,
                  crypto_members=frozenset(names if tag != "blend" else ()),
                  mh=incomplete_history,
                  sheltered=sheltered_base if not no_wash else None,
                  own=_BlendRun(reports_dir, no_wash, spot_crypto))
    ST.execute(_STAGES, _ctx, "blend_base", "blend_gains")
    combined_wash = cache / f".{tag}_gains_wash.json"
    if sheltered_base is not None and not no_wash:
        run_state.current.loss_context_gains.append(combined_wash)
    # A wash-sale basis add that reaches a sale in a filed year (US-WASH-22)
    # is decided only here, across accounts: on the console.
    from taxjson.lib.pipeline import ATTENTION_WASH_LOCKED
    echo_attention_lines(combined_wash,
                         prefix=ATTENTION_WASH_LOCKED[len("ATTENTION: "):],
                         once=True)
    # A move between your own accounts that the sender's lots cannot
    # back (US-BASIS-05): on the console, and --strict stops.
    echo_attention_lines(combined_wash, prefix="own-account move: ",
                         once=True)
    _mv = [ln for ln in _diag_lines(combined_wash)
           if ln.startswith(ATTENTION_PREFIX + " own-account move: ")]
    if _mv and strict:
        _die(f"--strict: {len(_mv)} move(s) between your own accounts "
             f"the sending account's lots do not cover (warning above) "
             f"— aborting")
    # The blended run's stderr lands in .blend_gains_wash.json.diag —
    # dot-prefixed, so collect_diagnostics' {account}_*.diag glob can
    # never surface it, and it is the ONLY pass that sees --sheltered
    # (its NOTEs explain the filed numbers). Mirror it into a per-
    # account diag each blended account's .sum banner picks up
    # (2026-09 audit), when it changed.
    _blend_diag = combined_wash.with_name(combined_wash.name + ".diag")
    from taxjson.lib.safe_write import write_changed
    for name in names:
        _mirror = cache / f"{name}_{tag}.diag"
        if _blend_diag.exists() and _blend_diag.stat().st_size:
            write_changed(_mirror, _blend_diag.read_text(errors="replace"))
        else:
            _mirror.unlink(missing_ok=True)
    ST.execute(_STAGES, _ctx, "split")
    _record_wash_inputs(cache, [cache / f"{n}_gains_wash.json"
                                for n in names], names,
                        sheltered_base, tag=tag)
    # A cost row on a listing no dated row shows held
    # (_flag_undated_listings, said after each split).
    _undated = list(run_state.current.undated_rows)
    run_state.current.undated_rows.clear()
    if _undated and strict:
        _die(undated_strict(_undated))
    # Conservation check: the splitter apportions blended inventory
    # rows by each account's BASE-book balance, which does not include
    # missing-history (OPENING_BALANCE) shares synthesized in-memory
    # from the .tt OPENING cost=unknown lines — those shares silently vanish from every
    # per-account holding. Compare Σ per-account qty vs the blended
    # row and say so instead of staying silent.
    for _msg in _blend_gap_messages(combined_wash, cache, names):
        _echo_captured(f"{_PROG}: warning: {_msg}", indent="",
                       file=sys.stderr, source=False)


def _blend_gap_messages(combined_wash: Path, cache: Path,
                        names: List[str]) -> List[str]:
    """_blend_conservation_gaps of the blended pass's files, their
    inventory quantities (what it reads) through the row checks (a view
    reads a 400-digit qty as inf: step 6c(6c3), backlog TJ-3046; only
    those, TJ-3053); none when a file cannot be read or is refused."""
    from taxjson.lib.json_input import read_rows_doc
    try:
        docs = [read_rows_doc(p, key="inventory", fields=("qty",))
                for p in [combined_wash] + [cache / f"{name}_gains_wash.json" for name in names]]
        if any(d is None for d in docs):
            return []
        return _blend_conservation_gaps(docs[0], docs[1:])
    except (ValueError, AttributeError):
        return []


def _blend_conservation_gaps(blended_doc: Dict[str, Any],
                             split_docs: List[Dict[str, Any]]) -> List[str]:
    """One message per blended inventory row whose per-account split
    does not add back up to it (the blended-pass conservation check —
    pinned by tests, G1-13)."""
    from taxjson.model.result import Holding
    blended_inv = {r.get("symbol"): float(Holding(r).quantity or 0.0)
                   for r in (blended_doc.get("inventory") or [])
                   if not r.get("account")}
    split_sums: Dict[str, float] = {}
    for doc in split_docs:
        for r in (doc.get("inventory") or []):
            if r.get("blended_pool"):
                split_sums[r.get("symbol")] = (
                    split_sums.get(r.get("symbol"), 0.0)
                    + float(Holding(r).quantity or 0.0))
    out: List[str] = []
    for sym, total in sorted(blended_inv.items()):
        got = split_sums.get(sym, 0.0)
        # Name the direction (audit A2-0021/A2-0074: an EXCESS was
        # reported as 'only ... under-report' and blamed on the
        # missing-history openings).
        if total - got > 1e-4:
            out.append(f"blended {sym} holds {total:g} but the per-account "
                       f"split accounts for only {got:g} — "
                       f"{total - got:g} short. Shares from .tt OPENING cost=unknown lines "
                       f"cannot be attributed to an account; otherwise a "
                       f"rename or split of {sym} is read differently by "
                       f"the per-account walk. Per-account holdings "
                       f"under-report by the gap.")
        elif got - total > 1e-4:
            out.append(f"blended {sym} holds {total:g} but the per-account "
                       f"split accounts for {got:g} — {got - total:g} MORE "
                       f"than the pool. Check the renames and splits of "
                       f"{sym} (and any ticker reused after a rename). "
                       f"Per-account holdings over-report by the excess.")
    return out


# The watchlist exports (Seeking Alpha *_SA.csv, FastGraph *_FG.csv,
# TradingView *_TV.txt) an earlier version wrote to reports/exports/ and
# this one no longer does: an outdated copy would read as current, so a
# full run removes them, and the folder once nothing else is in it.
_RETIRED_EXPORTS: Tuple[str, ...] = ("*_SA.csv", "*_FG.csv", "*_TV.txt")


def _sweep_retired_exports(reports_dir: Path) -> None:
    """Remove the retired watchlist exports (_RETIRED_EXPORTS) an earlier
    run left in reports/exports/, then the folder if that empties it — in
    one line. A file of the user's own there (another name) is kept, and
    so is the folder."""
    exports_dir = reports_dir / "exports"
    if not exports_dir.is_dir() or exports_dir.is_symlink():
        return
    gone = 0
    for pattern in _RETIRED_EXPORTS:
        for p in sorted(exports_dir.glob(pattern)):
            if p.is_file() or p.is_symlink():
                p.unlink()
                gone += 1
    try:
        exports_dir.rmdir()
        removed_dir = True
    except OSError:          # not empty: the user's own files stay
        removed_dir = False
    if not gone and not removed_dir:
        return
    what = (f"{gone} old watchlist file(s) in "
            f"{reports_dir.name}/exports/")
    if removed_dir:
        what += " and the folder"
    _say("note", f"removed {what} (the Seeking Alpha / FastGraph / "
         f"TradingView exports were removed)", indent="  ",
         file=sys.stdout)


def stage_sweep(cache: Path, reports_dir: Path, *, late: bool = False,
                accounts: Sequence[str] = ()) -> None:
    """A full run, its accounts' books built, before a report reads them:
    the retired exports go (`_sweep_retired_exports`), and what the last
    run's row instances built and this run's do not (an account, a
    broker's export or a kind gone from the project: the stage table's
    `sweep_vanished`, TJ-4265), with the own files of an account not in
    `accounts` (taxjson.toml's: TJ-4264), said in one line; what it kept
    (edited since taxjson wrote it, or not taxjson's: TJ-4274) in another,
    once a run. `late`: the run's reports built, the rest (a radar of an
    account no longer taxable)."""
    if not late:
        _sweep_retired_exports(reports_dir)
    swept = ST.sweep_vanished(cache, spare=() if late else ST.LATE_ROWS,
                              configured=list(accounts))
    gone = swept.gone
    if gone:
        _say("note", f"removed {_count_noun(len(gone), 'file')} the last run "
             f"built and this one does not (an account, an export or a kind "
             f"gone from the project): {', '.join(gone[:3])}"
             f"{' ...' if len(gone) > 3 else ''}", indent="  ", file=sys.stdout)
    shown = run_state.current.shown
    kept = [(f, why) for f, why in swept.kept if ("sweep-kept", f) not in shown]
    shown.update(("sweep-kept", f) for f, _why in kept)
    if kept:
        _why = {ST.EDITED: "edited since taxjson wrote it", ST.NOT_OURS: "not written by taxjson"}
        _say("note", f"kept {_count_noun(len(kept), 'file')} the last run listed "
             f"and this one does not build: "
             f"{', '.join(f'{f} ({_why[why]})' for f, why in kept[:3])}"
             f"{' ...' if len(kept) > 3 else ''}; delete "
             f"{'it' if len(kept) == 1 else 'them'} yourself if you do not need "
             f"{'it' if len(kept) == 1 else 'them'}", indent="  ", file=sys.stdout)


def _tt_has_no_rows(data: bytes) -> bool:
    """True when a .tt file's bytes hold only blank lines, `#` comments
    and SPLIT or dated RENAME lines: nothing it could book twice (a
    split or a rename applies to each account's own holding; backlog
    TJ-1184)."""
    from taxjson.lib.tt_format import strip_tt_comment, tt_lines
    text = data.decode("utf-8", errors="replace").lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return not any(strip_tt_comment(ln).split()[:1] not in ([], ["SPLIT"],
                                                            ["RENAME"])
                   for ln in tt_lines(text))


def _duplicate_input_files(inputs_dir: Path,
                           accounts: Mapping[str, Any]) -> List[List[str]]:
    """Groups of input files (inputs/<account>/*.csv|.tt) with identical
    content that sit under two or more DIFFERENT configured accounts —
    a copied export books every row twice, and parsers whose rows carry
    no broker account (Kraken, a .tt, an IB dividend) had no check at all
    (audit A2-0366). Whitespace-only files are ignored. Each group lists
    `account/file` names."""
    import hashlib
    by_hash: Dict[str, List[Tuple[str, str]]] = {}
    for name in sorted(accounts or {}):
        d = inputs_dir / name
        for p in input_files(d, ".csv") + input_files(d, ".tt"):
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if not data.strip():
                continue
            if p.suffix.lower() == ".tt" and _tt_has_no_rows(data):
                # Comments only (`# Manual adjustments, none yet`), or
                # one split in each account: no row to book twice
                # (backlog TJ-0939, TJ-1184).
                continue
            by_hash.setdefault(hashlib.sha256(data).hexdigest(), []).append(
                (name, f"{name}/{p.name}"))
    return [[f for _a, f in grp] for grp in by_hash.values()
            if len({a for a, _f in grp}) > 1]


def _duplicate_crypto_exports(cache: Path, names: List[str]) -> List[str]:
    """One line per pair of crypto accounts whose parsed exchange rows
    share transaction ids: the same export filed under two accounts is
    booked twice (re-audit A2-0569; the per-account dedup only sees one
    account). Exchange ids are unique per row (Coinbase IDs, Kraken
    txids; a content hash when the export has none)."""
    import json as _json
    ids: Dict[str, Dict[str, str]] = {}
    for n in names:
        mine: Dict[str, str] = {}
        for b in ("coinbase", "kraken"):
            p = cache / f"{n}_{b}.json"
            try:
                doc = _json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for t in doc.get("transactions") or []:
                if t.get("id"):
                    mine[str(t["id"])] = b
        ids[n] = mine
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = set(ids.get(a, {})) & set(ids.get(b, {}))
            if both:
                ex = sorted({ids[a][k] for k in both})
                out.append(
                    f"crypto accounts {a} and {b} hold {len(both)} "
                    f"identical {'/'.join(ex)} row(s) (the same "
                    f"transaction ids): the same export is in both "
                    f"inputs/{a}/ and inputs/{b}/, so its trades are "
                    f"booked twice. Keep it in one account.")
    return out


def _source_brokerages(doc: Dict[str, Any]) -> Dict[str, str]:
    """{input file name: brokerage} of a merged book, from the merge's
    metadata (sources[].original_metadata: input_files and
    source_brokerage); a row's `source` is that file name."""
    out: Dict[str, str] = {}
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    for src in (meta or {}).get("sources") or ():
        om = (src or {}).get("original_metadata") or {}
        broker = str(om.get("source_brokerage") or "")
        for f in om.get("input_files") or ():
            out[Path(str(f)).name] = broker
        for f in (om.get("source_accounts") or {}):
            out.setdefault(Path(str(f)).name, broker)
    return out


def _warn_shared_broker_accounts(bases: List[Tuple[str, Path]]
                                 ) -> List[str]:
    """One broker account's export in TWO taxjson accounts books every
    row twice (audit A2-0293, A2-0630). The rows carry their broker
    account (hashed, `source_account`); name each pair of taxjson
    accounts that share one, with the count of identical rows. The key
    is (brokerage, account id): two brokers' accounts may carry the same
    number (Questrade's, RBC's and Webull's 8-digit ids), and are two
    accounts. Returns each pair's one-line form, which the run records
    for the checklist's run-clean step and its closing list (backlog
    TJ-0478)."""
    import json
    seen: Dict[Tuple[str, str], Dict[str, set]] = {}
    for name, base in bases:
        try:
            doc = json.loads(Path(base).read_text())
            rows = doc.get("transactions")
        except (OSError, ValueError, AttributeError):
            continue
        brokers = _source_brokerages(doc)
        per: Dict[Tuple[str, str], set] = {}
        for t in rows or ():
            if isinstance(t, dict) and t.get("source_account"):
                # The id hashes the taxjson account label: compare the
                # row's content instead.
                key = (brokers.get(Path(str(t.get("source") or "")).name,
                                   ""), str(t["source_account"]))
                per.setdefault(key, set()).add(
                    (t.get("date"), t.get("action"), t.get("symbol"),
                     t.get("quantity"), t.get("net_amount")))
        seen[name] = per
    names = sorted(seen)
    shared: List[str] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for _broker, h in sorted(set(seen[a]) & set(seen[b])):
                same = len(seen[a][(_broker, h)] & seen[b][(_broker, h)])
                if same:
                    # Rows in both books: what the run-clean step and
                    # close-year refuse. A history split between two
                    # folders books nothing twice (warned, not recorded).
                    shared.append(f"broker account #{h[:6]} in {a} and {b}")
                _say("attention", f"the same broker account (#{h[:6]}) "
                     f"feeds two taxjson accounts, {a} and {b} ({same} "
                     f"identical row(s))",
                     ("Every row in both is booked twice. " if same else
                      "Its history is split between them: each account's "
                      "books miss the other's rows. ")
                     + "Put each broker account's exports under ONE "
                     "inputs/<account>/ folder.", indent="  ",
                     file=sys.stdout,
                     short=f"broker account #{h[:6]} is in both {a} and "
                           f"{b}: keep its exports in one inputs/ folder")
    return shared


def _warn_cross_taxable_overlap(taxable_bases: List[Tuple[str, Path]],
                                settings: Settings) -> None:
    """Loud caveat for the KNOWN_ISSUES multi-account gap: when the SAME
    security appears in two or more TAXABLE accounts, the per-account
    fan-out computes legally wrong numbers — Canada's ACB must blend
    across all non-registered accounts (ITA s. 47), and US §1091 wash
    matching between two taxable accounts is not performed. This was
    entirely silent before; the run now names the overlapping symbols.
    This function only detects the overlap; the blended pass (the
    per-kind blended wash run) computes the blended numbers."""
    import json as _json
    if len(taxable_bases) < 2:
        return
    by_symbol: Dict[str, set] = {}
    for name, base in taxable_bases:
        try:
            doc = _json.loads(Path(base).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for t in doc.get("transactions", []):
            # A position moved in kind into the account (a declared
            # TRANSFER) is held there too (re-audit A2-1231).
            if t.get("action") in ("BUYSELL", "ASSIGN", "OPENING_BALANCE",
                                   "TRANSFER"):
                sym = (t.get("symbol") or "").strip()
                if sym:
                    by_symbol.setdefault(sym, set()).add(name)
    overlap = sorted(s for s, accts in by_symbol.items() if len(accts) > 1)
    if not overlap:
        return
    country = _country(settings)
    consequence = (
        "the blended pass matches US §1091 wash sales across accounts "
        "(FIFO basis stays per account)"
        if country in ("us", "usa") else
        "the blended pass computes their ACB across all non-registered "
        "accounts (ITA s. 47)")
    shown = ", ".join(overlap[:8]) + (
        f" (+{len(overlap) - 8} more)" if len(overlap) > 8 else "")
    _say("note", f"{shown} traded in more than one TAXABLE account",
         f"{consequence[0].upper()}{consequence[1:]}. The wash-adjusted "
         f"artifacts (<account>_wash.sum — the canonical filing numbers) "
         f"carry the blended figures; the per-account <account>.sum "
         f"baseline stays isolated, so the pair shows what blending "
         f"changed.", prog=_PROG)


def _wash_preferred_gains(p: Path) -> Path:
    """The gains file the cross reports should read for a taxable
    account: the wash-adjusted twin when it exists and is at least as
    fresh, else the plain file. Same basis as the query twins —
    ccd-sum/leaps-sum resolve the wash-adjusted files, so building
    reports/ccd.rpt and leaps.rpt from pre-wash inputs made them
    disagree whenever a wash adjustment touched an option disposition.
    Freshness-guarded so a stale wash file from an older run (e.g. an
    account the wash pass no longer covers) can't shadow the
    just-rebuilt gains."""
    w = p.with_name(p.name.replace("_gains.json", "_gains_wash.json"))
    name = p.name[:-len("_gains.json")]
    # Current when this run's wash pass covered the account (TJ-1820: a
    # rebuilt book of the same bytes is not "newer"); else the file-time
    # rule, for a reader outside a run.
    if name in run_state.current.wash_current:
        return w if w.exists() else p
    return w if (w.exists() and p.exists()
                 and w.stat().st_mtime >= p.stat().st_mtime) else p


def stage_cross_reports(all_gains: List[Path],
                        taxable_equity_base: List[Path],
                        sheltered_base: Optional[Path],
                        reports_dir: Path,
                        ticker_map: Optional[Path] = None,
                        missing_history: Optional[Path] = None,
                        *, country: str) -> None:
    """The reports across accounts (the stage table's ccd, leaps, radar,
    radar_combined and crosslistings rows)."""
    if not all_gains:
        return
    _step(f"Writing cross-account reports to "
          f"{_out_relpath(reports_dir, reports_dir.parent)}/")
    cache = Path(taxable_equity_base[0]).parent if taxable_equity_base else reports_dir.parent / "work"
    stems = [tb.name[:-len("_base.json")] if tb.name.endswith("_base.json") else tb.stem
             for tb in taxable_equity_base]
    ctx = ST.StageCtx(work=cache, root_dir=reports_dir.parent, ticker_map=ticker_map,
                      mh=missing_history, sheltered=sheltered_base, members=stems,
                      own=_CrossRun(list(all_gains), list(taxable_equity_base), country))
    rows = ["ccd", "leaps"]
    if taxable_equity_base:
        # --sheltered adds the cross-account (registered-repurchase) column to
        # the radar, but it's optional: same-account superficial-loss detection
        # runs without it, so the radar still generates for a taxable-only
        # project that defines no registered account.
        rows.append("radar")
        if len(taxable_equity_base) > 1:
            rows.append("radar_combined")
        else:
            # Down to one taxable account: a leftover COMBINED pair
            # from an earlier multi-account state would shadow the
            # fresh per-account sidecar in harvest forever.
            for _ext in (".json", ".rpt"):
                _p = reports_dir / f"wash_radar_COMBINED{_ext}"
                if _p.exists():
                    _p.unlink()
        # Cross-listing lint: flag any root on both .TO and .US that the radar
        # would treat as two securities but maybe shouldn't (a TOBASE that
        # didn't consolidate, or a newly-traded interlisting missing from the
        # map). Advisory only — never fails the run.
        rows.append("crosslistings")
    # The radar: an instance per taxable book (the context's members), its
    # rows that account's.
    ST.execute(_STAGES, ctx, *rows)


def stage_fees(cache: Path, settings: Settings, rates: Path,
               reports_dir: Path) -> None:
    """Trading-fee report by brokerage, converted to the base currency for a
    cross-broker comparison (the stage table's fees row). Reads the parsed
    per-broker JSONs in the cache (the only place the brokerage tag
    survives), year-scoped to the tax year."""
    _tm = _PL.ticker_map_path(cache.parent)
    ST.execute(_STAGES, ST.StageCtx(work=cache, settings=settings, root_dir=reports_dir.parent,
                                    ticker_map=_tm if _tm.exists() else None,
                                    own=_Fees(rates)),
               "fees")


# ---------------------------------------------------------------- entry points

def _missing_history_arg(root: Path) -> Optional[Path]:
    """What a stage's --incomplete-history gets: the project folder when
    an account's .tt files open units at unknown cost (`OPENING ...
    cost=unknown`; the stages read the lines through it — lib/
    missing_history.load_missing_history), else None."""
    from taxjson.lib.missing_history import missing_history_arg
    return missing_history_arg(root)


# The .tt OPENING cost=unknown lines a stage given --incomplete-history
# reads, as work/<this> (_missing_history_deps; backlog TJ-4128).
MISSING_HISTORY_STATE = "missing_history.state"


def _missing_history_deps(incomplete_history: Optional[Path]) -> List[Path]:
    """What a stage given --incomplete-history declares (none without):
    the project folder it names has no content hash, so an edited OPENING
    cost=unknown line never reached a record (backlog TJ-4128). The lines
    load_missing_history reads, in work/missing_history.state (rewritten
    only when they change: each with its file and line number, so a line
    inserted above one rebuilds too, and an edit that leaves them where
    they are does not), and the map files it reads them through
    (lib/missing_history.missing_history_reads)."""
    if incomplete_history is None:
        return []
    from taxjson.lib.missing_history import missing_history_reads
    root = Path(incomplete_history)
    text, files = missing_history_reads(root)
    stamp = root / "work" / MISSING_HISTORY_STATE
    if _read_work_stamp(stamp) != text:
        stamp.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_work_stamp(stamp, text)
    return [stamp, *files]


def _manifest_shown(manifest_path: Path, root: Path) -> str:
    """An account's manifest.json as `elect` shows it: relative to the
    project, the shared exports' as ../inputs/<account>/manifest.json
    (out.relpath gave the absolute path, over the width: backlog
    TJ-0176)."""
    return _PL.shown(Path(manifest_path).resolve(), Path(root).resolve())


# The fields in_kind contribution_results reads (its key's date, its
# views' figures): what _checked_gains_docs checks (backlog TJ-3044).
_IN_KIND_FIELDS = ("date", "gain", "denied_contribution")


def _checked_gains_docs(cache: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(documents, refusals): each account's gains document (report_model
    resolve_gains_files) for in_kind contribution_results, its in-kind
    contribution rows' `_IN_KIND_FIELDS` (what the views read) through
    the row checks (json_input read_rows_doc; backlog TJ-2984). Only
    those: a damaged field nothing reads here leaves the document in
    (TJ-3044). A file that cannot be read is left out, as before; one
    whose figures are refused is left out, its refusal returned for the
    in-kind message to say (never in silence). The configured accounts'
    documents only (backlog TJ-4301)."""
    from taxjson.lib import in_kind as IK
    from taxjson.lib.json_input import read_rows_doc
    from taxjson.lib.report_model import resolve_gains_files
    docs, refused = [], []
    for p in resolve_gains_files(cache, accounts=accounts_of(
            load_config(cache.parent, missing_ok=True))).values():
        try:
            doc = read_rows_doc(p, fields=_IN_KIND_FIELDS, shown=f"{p.parent.name}/{p.name}",
                                only=lambda e: e.get("in_kind") == IK.CONTRIBUTION)
        except ValueError as e:
            refused.append(str(e))
            continue
        if doc is not None:
            docs.append(doc)
    return docs, refused



def _read_work_doc(path: Path) -> Dict[str, Any]:
    """A work/ artifact as a JSON object whose row lists are lists of
    objects (lib/json_input.read_work_doc). Raises OSError/ValueError
    — the views' existing handlers — never an AttributeError or a
    UnicodeDecodeError traceback for a corrupt file (S042-18)."""
    from taxjson.lib.json_input import read_work_doc
    return read_work_doc(path)

def _names_phrase(names: List[str]) -> str:
    """`a, b, c` up to three names, else `N accounts` (a step line
    naming every account wraps)."""
    return ", ".join(names) if len(names) <= 3 else f"{len(names)} accounts"


def _account_plan(name: str, acfg: Mapping[str, Any],
                  country: Optional[str] = None) -> str:
    """Registered-plan kind for the tips checks: explicit `plan = "tfsa"` in
    taxjson.toml wins (an unknown value is ignored — validate_config
    warns); else inferred from a plan word that is a whole TOKEN of the
    account NAME (the init scaffold names folders tfsa/rrsp/...; `rrsp2`
    and `my-tfsa` count, `admiral` and `spiral` no longer read as an IRA
    and silently skipped the US-LISTING check, R1-243); else the type."""
    # Only the project country's plans count, and a taxable account is
    # taxable whatever its plan says (tips skipped one with plan =
    # "401k"; validate_config warns on the contradiction, the other
    # country's plan is refused — audit A2-0739, A2-1332).
    kinds = _plan_kinds(country) if country else list(_PLAN_KINDS)
    acfg = typed_account(acfg, name)
    if acfg.taxable:
        return "taxable"
    explicit = str(acfg.plan or "").strip().lower()
    if explicit in kinds:
        return explicit
    low = name.lower()
    for p in _PLAN_NAMES:
        if p in kinds and re.search(
                rf"(?<![a-z]){re.escape(p)}(?![a-z])", low):
            return p
    return "sheltered"


def _tax_date_basis(settings: Settings) -> str:
    """The date the project's gains files are year-scoped on: the
    explicit [settings] tax_date, else the country default (CRA:
    settlement, IRS: trade) — what stage_account feeds the engine."""
    return _tax_date(settings)


def _has_tt_openings(root: Path) -> bool:
    from taxjson.lib.missing_history import has_tt_openings
    return has_tt_openings(root)


def _radar_engine_args(bases: List[Path],
                       project: Optional[Path],
                       country: str,
                       ticker_map: Optional[Path] = None) -> List[str]:
    """The radar's engine context, shared by every radar run (wash-radar,
    watch, buy-check, sell-check and the run's reports/wash_radar_*):
    the taxable accounts' gains files (wash-adjusted, s.47-blended — the
    engine decides which sales were losses), the project's .tt OPENING
    cost=unknown lines (the same openings the gains pass applies; the
    project folder `project` is passed when it has any), its
    country (Canada's per-holder s.54 test vs the US s.1091 rules), its
    transfer policy (--transfers-as-acquisitions) and its ticker.map
    (`ticker_map`, else the project's: the listings a TOBASE pair books
    as one security, whose calendars the radar's dates follow —
    backlog TJ-0446, TJ-0447)."""
    from taxjson.lib.report_model import resolve_gains_files
    # --country is required by the radar (lib/country): never omitted.
    out: List[str] = ["--country", _normalize_country(str(country))]
    if ticker_map is None and project is not None:
        ticker_map = _PL.ticker_map_path(Path(project))
    if ticker_map is not None and Path(ticker_map).is_file():
        out += ["--ticker-map", str(ticker_map)]
    by_dir: Dict[Path, Dict[str, Path]] = {}
    for b in bases:
        b = Path(b)
        if not b.name.endswith("_base.json"):
            continue
        name = b.name[:-len("_base.json")]
        found = by_dir.setdefault(b.parent, resolve_gains_files(b.parent))
        g = found.get(name)
        if g is not None:
            out += ["--gains", str(g)]
    if project is not None and _has_tt_openings(Path(project)):
        out += ["--incomplete-history", str(project)]
    if project is not None:
        # The project's transfer policy, as the engine passes read it
        # (CA-SL-16/17, US-WASH-23/24; pre-release review L5).
        try:
            from taxjson.lib.pipeline import transfers_as_acquisitions
            if transfers_as_acquisitions(load_settings(
                    Path(project), missing_ok=True)):
                out.append("--transfers-as-acquisitions")
        except Exception:                                  # noqa: BLE001
            pass
    # The project's .tt ALLOWLOSS filing positions (US-WASH-25): the US
    # radar's engine applies them as the gains pass does (pre-release
    # review; a claimed loss read WASHED).
    _work = {Path(b).parent for b in bases}
    if len(_work) == 1:
        out += _loss_override_flags(next(iter(_work)))
    if project is not None and out[1] == "canada":
        # The project's corporate_distributions list: the radar's own
        # pool moves a Canadian TRUST's return of capital to its record
        # date as the engine does, and leaves a listed corporation's on
        # its pay date (CA-INC-DATE-ROC-TRUST; audit A2-1174/A2-1178).
        try:
            _s = load_settings(Path(project), missing_ok=True)
            _corp = _s.corporate_distributions or []
            # CA-SL-11 / CA-SL-12 in the radar's own pool too (a loss
            # outside the gains files' year — audit A2-0442).
            if _s.option_buyback_loss_superficial is True:
                out.append("--option-buyback-wash")
        except Exception:                                  # noqa: BLE001
            _corp = []
        if isinstance(_corp, (list, tuple)):
            for _c in _corp:
                if isinstance(_c, str) and _c.strip():
                    out += ["--corporate-distribution", _c.strip()]
    return out
