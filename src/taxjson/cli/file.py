"""The Before you file commands: form-export, t1135, carryover, option-boundary, close-year, check-filed, handoff (the slip ones: cli/slips.py; gains-cra: cli/gains_filing.py)."""
import argparse
import contextlib
import hashlib
import re
import secrets
import shutil
import sys
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from taxjson.project.accounts import AccountSettings, account_of, accounts_of, estimate_of
from taxjson.project.settings import Settings, settings_of
from taxjson.lib import holdings_freeze as _HF
from taxjson.lib import project_layout as _PL
from taxjson.lib.command_log import ask as _ask
from taxjson.lib.cli_diag import note, tax_year as _tax_year_arg
from taxjson.lib.pipeline import income_dating_flags, option_timing_flags
from taxjson.lib.carry_back import SPLIT_ATTENTION
from taxjson.lib.in_kind import in_kind_unbooked
from taxjson.lib.report_fmt import fmt_money
from taxjson.lib.out import Doc
from taxjson.lib.safe_write import write_atomic as _write_atomic
from taxjson.lib.write_step import WriteStep
from taxjson.model import money as M

from taxjson.cli.checklist_run import checklist_run_helpers, default_run_sub
from taxjson.cli.common import (
    _accounts_skipped_for_no_inputs, _artifact_year_mismatch,
    _box18_fractions, _carried_back_to, _carry_back, _child_error, _claims_back,
    _gain_left_out, _stop_on_incomplete_split,
    _carried_in, _stale_opening, _typed_opening,
    _FROZEN_NOTE_PREFIX, _ledger_amount, _ledger_doc, LedgerUnavailable,
    _relay_child_stderr,
    _load_json_or_die, _books_older_than_inputs, _refuse_books_older_than_inputs,
    _refuse_cash_older_than_inputs,
    _run_state_problems, _RUN_UNFINISHED, _self_cmd, _unanswered_covers,
    _uncovered_sales, _view_income_rules, _warn_artifact_year,
    _warn_run_state, _work_read_why, _year_dir_arg,
)
from taxjson.pipeline import run_state
from taxjson.pipeline.config import (
    _config_shown, _country, _read_config_text, load_config,
)
from taxjson.pipeline.diagnostics import _count_noun
from taxjson.pipeline.inputs import (
    _base, _loss_override_flags, _normalize_country, _tax_date,
)
from taxjson.pipeline.messages import (
    _cmd_prog, _details, _die, _die_input, _PROG, _say, _step,
)
from taxjson.pipeline.run import (
    _acquire_run_lock, _carry_in_detail, _carry_in_moved,
    _check_filed_years, _filed_run_gains, _fx_cash_ledger_choice,
    _fx_cash_v2_doc, _grant_since_warning, _lock_missing_year,
)
from taxjson.pipeline.sanity import _has_inputs, _json_out, _print_report_table
from taxjson.pipeline.stages import (
    _missing_history_arg, _read_work_doc, _tax_date_basis,
)

# The reports' print() calls (PLAN step 15), close-year's too: the ops of a
# live exact Doc, `Doc(exact=True, width_=0, live=True)`, each printed when
# it is added (`page.line(e)` is `print(e)`, `page.blank()` is `print()`,
# `page.raw(...)` a print with keywords).


def _option_boundary_books(root: Path, cfg: Dict[str, Any], cache: Path, _out: Any, _prog: str,
                           TaxTransaction: Any, straddling: Callable[..., Any], year: int,
                           timing: str, since: Any, filed_years: Set[int],
                           filed_timing: Dict[int, Dict[str, Any]], settings: Settings,
                           filed_labels: Dict[int, str], filed_premiums: Dict[int, Any]
                           ) -> Tuple[List[Dict[str, Any]], int, List[str], List[str]]:
    """`tjs option-boundary`: each taxable equity account's written
    options that straddle a year boundary or are open at the year's end,
    from its base book (with the missing-history openings the gains stage
    adds); an account with no book said, or counted empty when its
    inputs/ folder is; a damaged book refused. Returns the rows, the books
    read, and the accounts missing and empty (cmd_option_boundary)."""
    rows = []
    books = 0
    missing = []
    _mh_pairs = None
    _mh = _missing_history_arg(root)
    if _mh is not None:
        from taxjson.lib.missing_history import load_missing_history
        try:
            _mh_pairs = load_missing_history(_mh) or None
        except (OSError, ValueError) as e:
            _die("the .tt OPENING cost=unknown lines cannot be read",
                 str(e))
    # A crypto account writes no options (lib/option_boundary skips it
    # too), and one the run skipped for an empty inputs/ folder has no
    # books to make: "run `tjs run`" about either looped (workflow
    # audit 2026-10-22, item 12).
    _no_inputs = _accounts_skipped_for_no_inputs(root)
    empty = []
    for name, acfg in sorted(accounts_of(cfg).items()):
        if not acfg.taxable or acfg.crypto:
            continue
        base = cache / f"{name}_base.json"
        if not base.exists():
            if name in _no_inputs:
                empty.append(name)
                continue
            missing.append(name)
            _out.warn(f"no work/{base.name}", prog=_prog,
                      details=["Run `tjs run` first."])
            continue
        books += 1
        # A truncated, non-UTF-8 or wrong-shape book was a traceback
        # here (S042-18).
        doc = _load_json_or_die(base)
        if not isinstance(doc.get("transactions"), list):
            # An object without its rows ({"accounts": []}) iterated its
            # keys as rows: an AttributeError traceback (issue #53).
            _die_input(f'work/{base.name} has no "transactions" list — the '
                 f'books are damaged or from an interrupted run',
                 "Run `tjs run` to rebuild them.")
        txs = []
        for r in doc["transactions"]:
            try:
                txs.append(TaxTransaction(**{k: v for k, v in r.items()
                                             if k in TaxTransaction.__dataclass_fields__}))
            except TypeError:
                continue
        if _mh_pairs is not None:
            # The same missing-history openings the gains stage adds: a
            # LONG option bought before the data and sold to close read as a WRITE, with a
            # false checklist ATTENTION when it had expired (S044-09).
            from taxjson.lib.missing_history import synthesize_openings
            txs, _log = synthesize_openings(txs, _mh_pairs)
        for r in straddling(txs, year, timing, since, filed_years,
                            filed_timing=filed_timing,
                            tax_date=_tax_date_basis(settings),
                            filed_labels=filed_labels,
                            filed_premiums=filed_premiums):
            r["account"] = r["account"] or name
            rows.append(r)
    return rows, books, missing, empty


def _option_boundary_text(_out: Any, year: int, args: argparse.Namespace, timing: str,
                          since: Any, partial_locks: Dict[int, str], filed_years: Set[int],
                          rows: List[Dict[str, Any]], attention: List[Dict[str, Any]],
                          amend: List[Dict[str, Any]]) -> None:
    """`tjs option-boundary` as text: the timing and the filed-year
    locks (a partial one noted), then the contracts' table with a `! `
    line for each to amend or review, or with --details each contract's
    where and what to do, and the closing count (cmd_option_boundary)."""
    def _para(text: str, indent: str = "", hang: Optional[str] = None):
        page = Doc(exact=True, width_=0, live=True)
        for _ln in _out.wrap(text, None, indent,
                             indent if hang is None else hang):
            page.line(_ln)

    page = Doc(exact=True, width_=0, live=True)
    page.line(f"OPTION YEAR-BOUNDARY REVIEW — tax year {year}")
    # Essentials first (docs/output-style.md): --details adds each
    # contract's where and what-if.
    _det = _details(args)
    if not _det:
        _para(f"Premium timing: {timing}"
              + (f" (from {since})" if timing == "grant" and since else "")
              + "; filed-year locks: "
              + (", ".join(str(y) + (" (partial: taken before the year "
                                     "ended)" if y in partial_locks else "")
                           for y in sorted(filed_years))
                 if filed_years else "none") + ".")
    else:
        _para(f"Premium timing: {timing}"
          + (f" (contracts written from {since})"
             if timing == "grant" and since else "") + ".")
    if filed_years and _det:
        _para("Filed-year locks: " + ", ".join(
            str(y) + (" (partial: taken before the year ended)"
                      if y in partial_locks else "")
            for y in sorted(filed_years)) + ".")
    elif _det:
        _para("No filed-year locks (run `tjs close-year` after "
              "filing).")
    for _py in sorted(partial_locks):
        _para(_out.label("note", stream=sys.stdout) + partial_locks[_py],
              "", "  ")
    page.blank()
    if not rows:
        _para("No written option straddles a year boundary and none is "
              "open at year end. Nothing to amend.")
        return
    out = ["ACCOUNT SYMBOL WRITTEN UNITS PREMIUM CLOSED KIND PAID"]
    for r in rows:
        # (`?`: the write is not in the books — a short opening balance
        # or a transfer in — so the premium is unknown)
        out.append(" ".join([r["account"], r["symbol"], r["written"], f"{r['units']:g}",
                             "?" if r["premium"] is None else fmt_money(r["premium"]),
                             r["closed"] or "-", r["close_kind"],
                             fmt_money(r["paid"]) if r["paid"] else "-"]))
    if not _det:
        page.line("KIND: how it closed (open: still open at year end); PAID: "
                  "the cost to close it"
                  + ("; PREMIUM ?: the write is not in the books."
                     if any(r["premium"] is None for r in rows) else "."))
    # Too wide: PAID, then UNITS go; then one record per contract.
    _print_report_table(out, fit=True, drop=(7, 3), key=(1, 0))
    page.blank()
    if not _det:
        for r in rows:
            if str(r.get("action", "")).startswith("T1-ADJ"):
                page.line(_out.act(f"{r['symbol']} ({r['account']}): an "
                                   f"amended return (T1-ADJ)",
                                   "tjs option-boundary --details"))
            elif r.get("attention"):
                page.line(_out.act(f"{r['symbol']} ({r['account']}): review "
                                   f"it", "tjs option-boundary --details"))
    for i, r in enumerate(rows if _det else [], 1):
        _lead = f"{i:>3}. "
        _hang = " " * len(_lead)
        _para(f"{r['symbol']} ({r['account']}, written {r['written']}): "
              f"{r['where']}", _lead, _hang)
        _para(f"=> {r['action']}", _hang, _hang + "   ")
    if _det:
        page.blank()
    # (the default view said each as a `! ` line above)
    if attention and _det:
        _para(f"{len(attention)} item(s) need ATTENTION (review; a locked "
              f"year may need a T1-ADJ) — marked above.")
    if amend and _det:
        _para(f"{len(amend)} item(s) require an amended return (T1-ADJ) — "
              f"listed above with the year and amount.")
    if not amend and not attention:
        _para("No amended return is required by these contracts under the "
              "timing in force.")


def cmd_option_boundary(args: argparse.Namespace) -> None:
    """`tjs option-boundary [--json]`: every written option in the
    taxable accounts whose write and close straddle a tax-year boundary
    (or that is still open at the project year's end) — where each
    amount lands under the timing in force (ITA s.49), and whether a
    filed year needs a T1-ADJ. A `filed/<year>.json` lock is what turns
    "if that year was filed" into a fact; ATTENTION rows (a locked year
    kept on transition close timing, an expired contract with no
    expiry row) need action. Exit 1 when no taxable book exists (nothing
    was checked), 0 otherwise."""
    from taxjson.model.transaction import TaxTransaction
    from taxjson.lib.option_boundary import reported_premiums, straddling
    from taxjson.lib.pipeline import option_timing_from_settings
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = settings_of(cfg)
    if not isinstance(settings.year, int):
        # It went on with year 0 and printed "tax year 0" (S044-07).
        _die("[settings] year is required in taxjson.toml",
             "The review is per tax year.")
    from taxjson.lib import out as _out
    _prog = "tjs option-boundary"
    year = int(settings.year)
    kw = option_timing_from_settings(settings)
    timing = kw.get("option_premium_timing", "close") if kw else "close"
    since = kw.get("option_grant_since") if kw else None
    _w = _grant_since_warning(settings, root, accounts_of(cfg))
    if _w:
        _out.warn(_w, prog=_prog)
    filed_years = set()
    filed_timing: Dict[int, Dict[str, Any]] = {}
    filed_labels: Dict[int, str] = {}
    from taxjson.lib import filed_locks as _tfl
    # The project's own locks AND the one [settings] prior_year_record
    # names: in the per-year layout last year's lock lives in the
    # previous project, and "no filed-year locks ... No amended return
    # is required" was printed for a premium that lock put in no return
    # (A2-0036, A2-0335).
    try:
        _locks = _tfl.project_locks(root, settings)
    except _tfl.PriorRecordError as e:
        _die(str(e))
    # What each lock's return reported for the written options: a lock
    # re-closed without an assigned contract's premium is the amended
    # return, and asks for no T1-ADJ (backlog TJ-0085).
    filed_premiums: Dict[int, Any] = {}
    _docs: Dict[int, Dict[str, Any]] = {}
    for fy, f, _where in _locks:
        filed_years.add(fy)
        _lbl = _tfl.lock_label(root, f)
        filed_labels[fy] = _lbl
        try:
            # The one check every reader of a lock applies (read_lock):
            # a damaged lock, or one closed under the other country's
            # law, was read raw for its timing (backlog TJ-0069).
            _doc = _tfl.read_lock(f, settings, label=_lbl,
                                  prior=_where != "local")
        except _tfl.LockProblem as e:
            # The lock's recorded timing drives the advice below: an
            # unreadable lock silently read as "no timing recorded" and
            # the advice flipped to ATTENTION / since = <locked year>
            # (S044-08).
            _out.warn(f"cannot read {_lbl} as a close-year lock — its "
                      f"recorded option timing is unknown", prog=_prog,
                      details=[f"{e}", f"The advice for {fy} below assumes "
                               f"none was recorded; `tjs check-filed` "
                               f"checks the lock."])
            continue
        _docs[fy] = _doc
        _ot = _doc.get("option_timing")
        if isinstance(_ot, dict):
            filed_timing[fy] = _ot
        _rp = reported_premiums(_doc)
        if _rp is not None:
            filed_premiums[fy] = _rp
    # A lock taken before its year ended is a snapshot, not a filed
    # return (A2-1164): said beside the year.
    partial_locks: Dict[int, str] = {}
    from taxjson.lib.filed_locks import partial_year_note
    for fy, _doc in _docs.items():
        _pn = partial_year_note(_doc, fy)
        if _pn:
            partial_locks[fy] = _pn
    rows, books, missing, empty = _option_boundary_books(
        root, cfg, cache, _out, _prog, TaxTransaction, straddling, year, timing, since,
        filed_years, filed_timing, settings, filed_labels, filed_premiums)
    if not books:
        # Nothing was checked: printing the all-clear here let the
        # checklist mark the step done on a project that never ran.
        if empty and not missing:
            _f = ", ".join(f"{_PL.shown(_PL.inputs_dir(root), root)}/{n}"
                           for n in empty)
            _die(f"NOT CHECKED — {_f} {'is' if len(empty) == 1 else 'are'} "
                 f"empty, so the run made no books",
                 "Save the account's exports there, or delete its "
                 "[accounts.NAME] table from taxjson.toml if you do not "
                 "have it.")
        _die("NOT CHECKED — no taxable base files in work/",
             "Run `tjs run` first.")
    amend = [r for r in rows if r["action"].startswith("T1-ADJ")]
    attention = [r for r in rows if r.get("attention")]
    if getattr(args, "json", False):
        _json_out({"year": year, "timing": timing, "since": since,
                   "since_explicit": settings.option_grant_timing_since not in (None, ""),
                   # The grant-timing warning above was given (the run's
                   # gate: a written option in the books) — what the
                   # checklist flags (audit 2026-10-22, item 10).
                   "since_warning": bool(_w),
                   "filed_years": sorted(filed_years),
                   "partial_locks": {str(k): v for k, v in
                                     sorted(partial_locks.items())},
                   "rows": rows,
                   "amend": len(amend), "attention": len(attention),
                   "missing_books": missing}, schema="option-boundary")
        return
    _option_boundary_text(_out, year, args, timing, since, partial_locks, filed_years, rows,
                          attention, amend)


def cmd_t1135(args: argparse.Namespace) -> None:
    """`tjs t1135`: CRA T1135 foreign-property helper over ALL taxable
    accounts (T1135 is a per-person form; registered accounts are excluded
    from specified foreign property by law, so sheltered accounts are never
    read). Feeds every taxable `<account>_base.json` (full history, base
    currency) plus the year-scoped gains files — preferring the wash-adjusted
    `<account>_gains_wash.json` so the GAIN(LOSS) column matches what
    Schedule 3 reports — into taxjson-t1135. Each account read needs its
    gains file (the income and gain columns): refused without it."""
    from taxjson.bin import taxjson_t1135
    from taxjson.lib.report_model import resolve_gains_files

    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    _accts = accounts_of(cfg)
    settings = settings_of(cfg)
    year = settings.year
    if year is None:
        _die("no [settings] year in taxjson.toml")
    taxable = [n for n, c in _accts.items()
               if c.taxable]
    if not taxable:
        _die("no taxable accounts in taxjson.toml",
             "T1135 applies to non-registered property only.")

    # All positionals first: argparse won't accept a second base file
    # after an interleaved --gains (the nargs="+" positional is consumed
    # greedily on first contact).
    base_argv: List[str] = []
    gains_argv: List[str] = []
    missing: List[str] = []
    no_gains: List[str] = []
    for name in taxable:          # taxjson.toml order (CA-DATE-14)
        base = cache / f"{name}_base.json"
        if not base.exists():
            missing.append(name)
            continue
        base_argv.append(str(base))
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            gains_argv += ["--gains", str(gains)]
        else:
            no_gains.append(name)
    argv: List[str] = base_argv + gains_argv
    # A configured taxable account with inputs but no books made the
    # filing-threshold test read "not required" with rc 0 (S006-03).
    _require_taxable_books(root, cfg,
                           {n: 1 for n in taxable if n not in missing},
                           "base book")
    _refuse_t1135_books(root, cfg, int(year))
    base_currency = str(_base(settings))
    if base_currency.upper() != "CAD":
        note("tjs t1135",
             f"base_currency is {base_currency} — T1135 amounts must be "
             f"reported in CAD; these figures are in {base_currency}.")
    # The income and gain columns come from the gains files: an
    # account whose books were read without them showed 0 income and 0
    # gain, rc 0 and no word (backlog TJ-0306).
    if no_gains:
        _die(f"no gains file for taxable account(s) {', '.join(no_gains)} "
             f"in work/ — the T1135 income and gain(loss) columns come "
             f"from it",
             "Re-run `tjs run` to rebuild it; without it those "
             "accounts' income and gains would read 0.")
    missing = [n for n in missing
               if n not in _accounts_skipped_for_no_inputs(root)]
    if missing:
        from taxjson.lib.out import warn as _warn
        _warn(f"no base file for taxable account(s) {', '.join(missing)}",
              prog="tjs t1135",
              details=["Run `tjs run` first; the threshold test below "
                       "may be understated."], whole=True)
    if not argv:
        _die("no taxable base files in work/", "Run `tjs run` first.")
    # The income/gain columns come from the year-scoped gains files: on
    # books built for another year they silently read 0 (S007-06,
    # S048-01).
    _warn_artifact_year(
        {Path(g).name.rsplit("_gains", 1)[0]: Path(g)
         for g in gains_argv[1::2]}, year)
    _warn_run_state(root, cfg)

    argv += ["--year", str(year), "--base-currency", base_currency,
             # S052-10: the gain join and the cost walk follow the
             # project's tax_date, like the gains files.
             "--tax-date", _tax_date_basis(settings)]
    # An account held with a custodian outside Canada: its Canadian
    # issuers' shares are T1135 property (tax-logic CA-RPT-02; backlog
    # TJ-0369). The config check has read the value.
    for name in taxable:
        cc = account_of(_accts, name).custodian_country
        if cc is not None:
            argv += ["--custodian", f"{name}={cc}"]
    # The domicile overrides are ticker.map's T1135 lines (once
    # t1135.map). A dangling link (or a directory) is not "no map": the
    # overrides it holds change the filing verdict (A2-0355).
    t1135_map = _PL.ticker_map_path(root)
    if t1135_map.is_file():
        argv += ["--map", str(t1135_map)]
    elif t1135_map.is_symlink() or t1135_map.exists():
        _die(f"cannot read {t1135_map.name} (a broken link or not a "
             f"file)",
             "Fix or remove it; its T1135 country overrides change the "
             "verdict.")
    # The same missing-history openings the gains stage applies (R1-321):
    # without them a position bought before the data read as a short that
    # later real buys covered at zero cost.
    mh_file = _missing_history_arg(root)
    if mh_file is not None:
        argv += ["--incomplete-history", str(mh_file)]
    # The full-history superficial-loss pass (S008-07) sees what the
    # pipeline's wash pass sees: the registered accounts as context and
    # the project's written-option timing.
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        argv += ["--sheltered", str(sheltered_base)]
    argv += option_timing_flags(settings)
    # ...and its income dating: a listed corporation's ROC on its pay
    # date in that pass (audit A2-0339).
    argv += income_dating_flags(settings)
    # ...and the filing positions the books took (.tt ALLOWLOSS): no ACB
    # addition for them.
    argv += _loss_override_flags(cache)
    if args.json:
        argv.append("--json")
    if not _details(args) and not getattr(args, "json", False):
        # Essentials first (docs/output-style.md): the stage's default
        # view; --details prints its whole report.
        argv.append("--brief")
    # The cash an account holds outside Canada (a crypto account's, one
    # whose custodian is abroad) is foreign property at its cost: the
    # FX-on-cash ledger v2 gives it day by day (tax-logic CA-RPT-20;
    # backlog TJ-0454, TJ-0609).
    cash_doc = _t1135_cash_doc(root, cache, cfg, settings)
    if cash_doc is None:
        raise SystemExit(taxjson_t1135.main(argv))
    import json as _json
    import tempfile
    with tempfile.TemporaryDirectory(prefix="taxjson-t1135-") as td:
        cash_file = Path(td) / "cash.json"
        _write_atomic(cash_file, _json.dumps(cash_doc), encoding="utf-8")
        rc = taxjson_t1135.main(argv + ["--cash", str(cash_file)])
    raise SystemExit(rc)


def _refuse_t1135_books(root: Path, cfg: Dict[str, Any], year: int) -> None:
    """Exit 1 before `tjs t1135` prints anything from books a full `tjs run`
    did not build from the inputs as they are now, as close-year refuses
    them (cli/common `_refuse_books_older_than_inputs`), or, where it counts
    cash held outside Canada, from cash events missing or not the run's
    (`_refuse_cash_older_than_inputs`). The T1135 test is a filing figure.
    Owner decision 2026-11-18: refuse, as the filing commands do (backlog
    TJ-4347; before, a banner, the securities' figures, the cash refused)."""
    _refuse_books_older_than_inputs(root, cfg, read_upto=year)
    if _t1135_reads_cash(settings_of(cfg), accounts_of(cfg)):
        _refuse_cash_older_than_inputs(root, cfg)


def _t1135_reads_cash(settings: Settings, accts) -> bool:
    """Whether `tjs t1135` reads the cash events: a Canadian project with a
    taxable account that holds cash outside Canada (lib/t1135_cash)."""
    from taxjson.lib import t1135_cash as TC
    from taxjson.lib.country import is_canada
    return is_canada(_country(settings)) and bool(TC.counted_accounts(accts))


def _t1135_cash_doc(root: Path, cache: Path, cfg: Dict[str, Any],
                    settings: Settings) -> Optional[Dict[str, Any]]:
    """The cash rows of `tjs t1135` (lib/t1135_cash.cash_doc), None
    when no taxable account holds cash outside Canada (or the project
    is not Canadian). The FX-on-cash ledger v2 costs it; where the ledger
    cannot run, the doc says so (an unknown cost, the total not settled)
    and the securities' test still stands. Books or cash events older
    than the inputs never reach it: `cmd_t1135` refused them first."""
    from taxjson.lib import t1135_cash as TC
    if not _t1135_reads_cash(settings, accounts_of(cfg)):
        return None
    counted = TC.counted_accounts(accounts_of(cfg))
    import contextlib
    import io
    ledger = None
    refusal = ""
    year = int(settings.year)
    base = str(_base(settings)).upper()
    # The cash day by day on the dates the walk dates the securities
    # on: on trade dates a purchase's cash leaves as its shares come in
    # (backlog TJ-0627).
    basis = _tax_date_basis(settings)
    # The ledger's own notes are `tjs fx-cash`'s to say.
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            ledger = _fx_cash_v2_doc(root, cache, holdings=True,
                                     holdings_basis=basis)[0]
        except SystemExit as e:
            refusal = str(e.code or "").strip()
        except (OSError, ValueError) as e:
            refusal = str(e)
    stable: List[str] = []
    if ledger is not None and ledger.get("stablecoins_as_cash"):
        from taxjson.lib.brokerages._crypto_common import USD_STABLECOINS
        stable = sorted(USD_STABLECOINS)
    refusal = refusal.split("\n", 1)[0]
    for p in ("tjs fx-cash: error: ", "tjs fx-cash: "):
        if refusal.startswith(p):
            refusal = refusal[len(p):]
    return TC.cash_doc(ledger, counted, year, base,
                       refusal=refusal and f"fx-cash v2: {refusal}",
                       stablecoins=stable, basis=basis)


def _box18_by_year(root: Path, accounts) -> Dict[int, float]:
    """{tax year: T5 box 18 capital-gains dividends} named in
    [[capital_gains_dividends]], in the base currency, dated as the
    estimate dates them (_box18_into_estimate). {} without the map."""
    fr = _box18_fractions(root)
    if not fr:
        return {}
    from taxjson.lib.cg_dividends import row_amount
    rules = _view_income_rules(root)
    out: Dict[int, float] = {}
    for acct in sorted(accounts):
        p = root / "work" / f"{acct}_base.json"
        if not p.exists():
            continue
        try:
            data = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # Named once, from the project; exit 2, as the estimate's
            # _box18_into_estimate (backlog TJ-0641, TJ-0645).
            _die_input(f"could not read {_work_read_why(p, e, root)}",
                       "It holds the capital-gains dividends "
                       "[[capital_gains_dividends]] names. Re-run "
                       "`tjs run` to rebuild it.")
        for t in data.get("transactions", []):
            f = fr.get((acct, str(t.get("id"))))
            if not f or t.get("action") != "DIVIDEND":
                continue
            d = rules.income_date(t) if rules is not None \
                else str(t.get("date") or "")
            if len(str(d)) >= 4 and str(d)[:4].isdigit():
                y = int(str(d)[:4])
                out[y] = out.get(y, 0.0) + row_amount(t) * f
    return out


def cmd_carryover(args: argparse.Namespace) -> None:
    """`tjs carryover`: multi-year capital-loss carryforward/carryback
    ledger over all taxable accounts' full-history books. Passes the
    combined sheltered book for wash-window context and the project's
    .tt OPENING cost=unknown lines when present. taxjson.toml's [carryover] claimed
    (`claimed = { 2023 = 4000.00 }`) folds in what was actually claimed
    on filed returns."""
    from taxjson.bin import taxjson_carryover

    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = settings_of(cfg)

    taxable = [n for n, c in accounts_of(cfg).items()
               if c.taxable]
    if not taxable:
        _die("no taxable accounts in taxjson.toml")
    _acct_cfg = accounts_of(cfg)
    base_argv: List[str] = []
    crypto_argv: List[str] = []
    missing: List[str] = []
    for name in taxable:          # taxjson.toml order (CA-DATE-14)
        base = cache / f"{name}_base.json"
        if not base.exists():
            missing.append(name)
        elif account_of(_acct_cfg, name).crypto:
            crypto_argv += ["--crypto", str(base)]
        else:
            base_argv.append(str(base))
    # An account the last run skipped for having no inputs has no books
    # to miss — the warning fired for the scaffold's empty `crypto`
    # account on every carryover (R1-264), unlike t1135 and the rest.
    missing = [n for n in missing
               if n not in _accounts_skipped_for_no_inputs(root)]
    if missing:
        from taxjson.lib.out import warn as _warn
        _warn(f"no base file for taxable account(s) {', '.join(missing)}",
              prog="tjs carryover",
              details=["Run `tjs run` first; those years' nets will "
                       "be incomplete."], whole=True)
    if not base_argv and not crypto_argv:
        _die("no taxable base files in work/", "Run `tjs run` first.")
    # A crypto-only project passes its books as --crypto only: fed as
    # positional FILEs they lost the US no-wash pass (US-WASH-13; audit
    # A2-0146, A2-0411, A2-0412).

    argv = base_argv + crypto_argv + [
        a for n in missing for a in ("--missing-book", n)] + [
        "--country", _country(settings),
        "--base-currency", str(_base(settings)),
        # The same year attribution as run / close-year (R1-192).
        "--tax-date", _tax_date_basis(settings),
    ] + option_timing_flags(settings)       # same timing as the returns
    # ...and the same income dating: a listed corporation's ROC on its
    # pay date (audit A2-0123, A2-0339, A2-0341, A2-1141).
    argv += income_dating_flags(settings)
    argv += _loss_override_flags(cache)     # .tt ALLOWLOSS positions
    if settings.year is not None:
        # Rows before the project year are flagged as possibly partial.
        argv += ["--project-year", str(int(settings.year))]
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        argv += ["--sheltered", str(sheltered_base)]
    mh_file = _missing_history_arg(root)
    if mh_file is not None:
        argv += ["--incomplete-history", str(mh_file)]
    # [carryover] claimed (taxjson.toml): the losses actually applied
    # on filed returns (checked with the rest of the config).
    from taxjson.lib.project_tables import claimed_losses
    for _y, _amt in sorted(claimed_losses(cfg)[0].items()):
        argv += ["--claimed-year", f"{_y}={_amt!r}"]
    if _country(settings) == "canada":
        # A total, or by the year it went to (YEAR:TARGET=AMOUNT; TJ-4882).
        from taxjson.lib.project_tables import carried_back_entries
        _backs, _to, _ = carried_back_entries(cfg)
        for _y, _amt in sorted(_backs.items()):
            argv += [a for _t, _a in sorted(_to.get(_y, {}).items())
                     for a in ("--carried-back-year", f"{_y}:{_t}={_a!r}")] or [
                "--carried-back-year", f"{_y}={_amt!r}"]
    argv.append("--claims-in-project")      # the notes name the toml
    # Each filed-year lock — this project's filed/<year>.json and the
    # prior_year_record lock of the per-year layout (A2-0338, A2-0121):
    # a year before the project year takes what the lock says was
    # filed; a later one is compared (S047-21, S048-20). An unreadable
    # lock is named, never skipped (A2-0336).
    from taxjson.lib import filed_locks
    try:
        _locks = filed_locks.project_locks(root, settings)
    except filed_locks.PriorRecordError as e:
        _die(str(e))
    if _country(settings) == "canada" and settings.year is not None:
        # ...and the records those locks opened from: what a rebuilt year's
        # return carried back is in its own lock (backlog TJ-1574).
        def _usable(p: Path, y: int) -> bool:
            try:
                return taxjson_carryover.lock_figure(p, "canada").get("year") == y
            except taxjson_carryover.LockUnreadable:
                return False
        _locks = filed_locks.records_behind(_locks, int(settings.year), _usable)
    for _yr, _lp, _where in _locks:
        argv += ["--filed-lock", f"{_yr}={_lp}"]
    # The prior_year_record lock deleted after its year was closed: named
    # as a lock that cannot be read (backlog TJ-0272).
    _mp = filed_locks.missing_prior_lock(root, settings)
    if _mp is not None and _mp[0] not in {y for y, _p, _w in _locks}:
        import os as _os
        argv += ["--filed-lock", f"{_mp[0]}={_os.path.normpath(_mp[1])}"]
    # T5 box 18 capital-gains dividends named in
    # [[capital_gains_dividends]] are capital gains of their year: the
    # estimate nets them, so the ledger does too (A2-0678).
    for _y, _amt in sorted(_box18_by_year(root, taxable).items()):
        argv += ["--slip-gains", f"{_y}={_amt!r}"]
    _ret = _project_return_row(root, cfg, taxable)
    if _ret is not None:
        # The project year's net as the return files it (backlog
        # TJ-0208): what close-year records and the estimate taxes.
        argv += ["--return-row",
                 f"{int(settings.year)}={_ret['realized']!r}"]
    # close-year's and the estimate's: the net the fold takes. Either flag and
    # the engine doesn't read the year's own lock at all; with neither it reads
    # it, for that net too (backlog TJ-1493, TJ-1573).
    if getattr(args, "filed_net", None) is not None:
        argv += [f"--filed-net={args.filed_net!r}"]
    _in = _carried_in(root, cfg)
    if _in is not None:
        # The balance a plain estimate takes in: the ledger asks what the
        # estimate would (backlog TJ-4885).
        argv += [f"--carried-in={_in[0]!r}"] + (["--carried-in-typed"] if _in[1] else [])
    if getattr(args, "on_books", False):
        argv.append("--on-books")
    _w = _grant_since_warning(settings, root, accounts_of(cfg))
    if _w:
        from taxjson.lib.out import warn as _warn
        _warn(_w, prog="tjs carryover")
    # Deferred / failed / validation-ERROR books drive the carryforward
    # balance too (S048-21).
    _warn_run_state(root, cfg)
    if args.json:
        argv.append("--json")
    if not _details(args) and not getattr(args, "json", False):
        # Essentials first (docs/output-style.md): the stage's default
        # view; --details prints its whole report.
        argv.append("--brief")
    raise SystemExit(taxjson_carryover.main(argv))


def _taxable_gains_argv(root: Path, cache: Path, *,
                        exclude_crypto: bool = False,
                        prog: str = "tjs",
                        required: bool = False) -> List[str]:
    """`--gains FILE` pairs for every taxable account, preferring the
    wash-adjusted `<account>_gains_wash.json` (the allowed numbers a return
    reports). Shared by the form-export and reconcile-slips wrappers.

    `exclude_crypto` drops `crypto = true` accounts (with a note): no
    exchange issues a T5008/1099-B, so feeding a crypto book to the slip
    reconciler made every one of its dispositions MISSING_FROM_SLIP and
    the command could never exit 0 (2026-09 audit)."""
    cfg = load_config(root)
    accounts_cfg = accounts_of(cfg)
    taxable = [n for n, c in accounts_cfg.items() if c.taxable]
    if not taxable:
        _die("no taxable accounts in taxjson.toml")
    if exclude_crypto:
        crypto = sorted(n for n in taxable if accounts_cfg[n].crypto)
        if crypto:
            note(prog, f"crypto account(s) {', '.join(crypto)} excluded "
                       f"(exchanges issue no slips)",
                 details=(["Exchanges issue no T5008/1099-B slips, so "
                           "there is nothing to reconcile them against."]
                          if run_state.current_details else []))
            taxable = [n for n in taxable if n not in crypto]
        if not taxable:
            _die("every taxable account is crypto — nothing to reconcile",
                 "No slip data exists for crypto dispositions (exchanges "
                 "issue no T5008/1099-B).")
    from taxjson.lib.report_model import resolve_gains_files
    argv: List[str] = []
    _no_input = _accounts_skipped_for_no_inputs(root)
    have = {}
    for name in sorted(taxable):
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            argv += ["--gains", str(gains)]
            have[name] = gains
        elif required and _has_inputs(root, name):
            pass                        # refused below, all at once
        elif name not in _no_input:
            from taxjson.lib.out import warn as _warn
            _warn(f"no gains file for taxable account {name!r}",
                  prog=prog, details=["Run `tjs run` first."])
    if required:
        # The Schedule 3 / 8949 export and the slip check must not be
        # partial: a failed account was a one-line warning with rc 0,
        # and the checklist certified the partial export (S045-21).
        _require_taxable_books(
            root, {"accounts": {n: accounts_cfg[n] for n in taxable}},
            have, "gains file")
    if not argv:
        _die("no taxable gains files in work/", "Run `tjs run` first.")
    return argv


def cmd_form_export(args: argparse.Namespace) -> None:
    """`tjs form-export`: render the year's gains as IRS Form 8949 or
    CRA Schedule 3 rows. Form defaults from the project country."""
    from taxjson.bin import taxjson_form_export

    root = Path(args.dir).resolve()
    cache = root / "work"
    _cfg = load_config(root)
    settings = settings_of(_cfg)
    country = _country(settings)
    form = args.form or ("8949" if country == "usa" else "schedule3")
    year = settings.year
    _txf_only = [f for f, v in (("--out", getattr(args, "out", None)),
                                ("--box", getattr(args, "box", None)))
                 if v is not None]
    if form != "txf" and _txf_only:
        # Were silently ignored: `--out gains.txf` wrote nothing and
        # printed the table to stdout (2026-09 CLI audit B19).
        _die(f"{' and '.join(_txf_only)} only "
             f"{'applies' if len(_txf_only) == 1 else 'apply'} to --form txf "
             f"(this run renders {form})",
             f"Use --csv FILE to save the {form} rows.")

    gains_argv = _taxable_gains_argv(root, cache, required=True)
    # The rows of a return: a full run's of the inputs as they are.
    _refuse_books_older_than_inputs(root, _cfg, read_upto=year)
    # form-export takes gains files positionally.
    files = [gains_argv[i + 1] for i in range(0, len(gains_argv), 2)]
    _warn_artifact_year({Path(f).name.rsplit("_gains", 1)[0]: Path(f)
                         for f in files}, year)
    _state_probs = _warn_run_state(root, _cfg)
    # Crypto books go on Schedule 3's crypto-assets line: name them.
    from taxjson.lib.report_model import resolve_gains_files
    _crypto_files = []
    for _n, _c in sorted(accounts_of(_cfg).items()):
        if _c.taxable and _c.crypto:
            _g = resolve_gains_files(cache, _n).get(_n)
            if _g is not None:
                _crypto_files += ["--crypto", str(_g)]
    argv = files + _crypto_files + ["--form", form, "--country", country,
                    "--base-currency",
                    str(settings.base_currency or ""),
                    # R1-200: rows are picked by the date the gains
                    # files were scoped on, not the form's default.
                    "--date-basis", _tax_date_basis(settings)]
    if year is not None:
        argv += ["--year", str(year)]
    if args.csv:
        argv += ["--csv", args.csv]
    if args.json:
        argv.append("--json")
        for _p in _state_probs or ():
            argv += ["--run-state-problem", str(_p)]
    # What `tjs sum` counts as missing from the totals that the gains
    # files cannot tell: an in-kind move with a registered account the
    # run could not value, a sale with no purchase in the files — the
    # same readiness record, and a `! ` line each (backlog TJ-0251).
    _taxable = {n for n, c in accounts_of(_cfg).items()
                if c.taxable}
    _ik = [m for m in in_kind_unbooked(cache, year)
           if m.get("taxable") in _taxable]
    _np = [r for r in _uncovered_sales(root, _cfg)
           if r.get("booked") == "open"]
    if _ik:
        argv += ["--in-kind-unbooked", str(len(_ik))]
    if _np:
        argv += ["--no-purchase", str(len(_np))]
    _ub = _unanswered_covers(root, _cfg)
    if _ub:
        argv += ["--unbacked-cover", str(len(_ub))]
    if not _details(args) and not getattr(args, "json", False):
        # Essentials first (docs/output-style.md): the stage's default
        # view; --details prints its whole report.
        argv.append("--brief")
    if form == "txf":
        argv += ["--box", args.box or "A"]
        if args.out:
            argv += ["--out", args.out]
    raise SystemExit(taxjson_form_export.main(argv))


def _require_taxable_books(root: Path, cfg: Dict[str, Any],
                           have: Dict[str, Any], what: str) -> None:
    """Die when a configured taxable account that HAS inputs has no
    `what` artifact: a partial filing figure (form-export, t1135,
    close-year) was reported with rc 0 and a one-line warning
    (S045-21, S006-03, S006-07)."""
    missing = sorted(
        n for n, c in accounts_of(cfg).items()
        if c.taxable
        and n not in have and _has_inputs(root, n))
    if missing:
        _die(f"no {what} for taxable account(s) {', '.join(missing)} — "
             f"the last `tjs run` did not build them (did it fail?). "
             f"Fix and re-run `tjs run`; figures without them would "
             f"leave those accounts out.")


def _close_year_attention(root: Path, cfg: Dict[str, Any],
                          year: int) -> List[Tuple[str, str]]:
    """(step id, what it found) for each checklist step before the lock
    (lib/checklist, the steps ahead of filed-lock) whose result, marks
    applied, needs attention — or is still to do where the lock needs
    it done: inputs committed, so the filed books can be rebuilt
    (_CLOSE_YEAR_TODO; backlog TJ-0090)."""
    from taxjson.lib import checklist as cl
    ids = cl.item_ids()
    only = ids[:ids.index("filed-lock")] if "filed-lock" in ids else ids
    ctx = cl.Ctx(root=root, cfg=cfg, year=year, today=date_cls.today(),
                 run_sub=default_run_sub(root), run=checklist_run_helpers())
    try:
        results = cl.evaluate(ctx, only=only, progress=cl.stderr_progress)
    except cl.StateFileError as e:
        return [("checklist.json", str(e))]
    return [(r.id, " ".join(str(r.detail or "").split()))
            for r in results if r.effective == "attention"
            or (r.effective == "todo" and r.id in _CLOSE_YEAR_TODO)]


# Checklist steps before the lock whose "todo" stops close-year like an
# "attention" (docs/commands.md: uncommitted inputs stop the close).
_CLOSE_YEAR_TODO = frozenset({"inputs-committed"})


def _return_row_totals(cfg: Dict[str, Any], files: Dict[str, Path],
                       year: int) -> Optional[Dict[str, float]]:
    """`realized` and `disallowed` as `tjs sum`'s FOR THE RETURN row
    files them, for the taxable gains `files`: each disposition's cents
    summed per return line (Canada: Schedule 3's gain and denied; USA:
    Form 8949's (h) and (g), the §1256 net of Form 6781 added to the
    gain). The lock's `totals` round the engine's unrounded sum once,
    a cent or two apart on a large year: close-year and `tjs years`
    show these, check-filed compares the totals (backlog TJ-0192). None
    when the dispositions cannot be read."""
    _accts = accounts_of(cfg)
    import io
    from taxjson.lib.tax_forms import mark_crypto
    from taxjson.bin.taxjson_form_export import (filing_6781, filing_lines, filing_parts_8949,
                                                 load_dispositions)
    settings = settings_of(cfg)
    date_key = ("date" if _tax_date_basis(settings) == "trade"
                else "date_settle")
    ents: List[Dict[str, Any]] = []
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            for acct, p in sorted(files.items()):
                got, _ = load_dispositions([Path(p)], year, date_key)
                if account_of(_accts, acct).crypto:
                    got = mark_crypto(got)
                ents += got
            if _country(settings) in ("us", "usa"):
                rows = filing_parts_8949(ents, year)
                gain = (sum(float(r["gain"]) for r in rows)
                        + float(filing_6781(ents).get("gain") or 0.0))
                denied = sum(float(r["adjustment"]) for r in rows)
            else:
                rows = filing_lines(ents, year, date_key)
                gain = sum(float(r["gain"]) for r in rows)
                denied = sum(float(r["denied"]) for r in rows)
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return None
    return {"realized": M.shown(gain, 2) + 0.0,
            "disallowed": M.shown(denied, 2) + 0.0}


def _project_return_row(root: Path, cfg: Dict[str, Any],
                        taxable: List[str]) -> Optional[Dict[str, float]]:
    """The project year's RETURN row (`_return_row_totals`) over the
    taxable accounts' gains files in work/, for `tjs carryover`'s
    ledger (Canada: its net is one figure; backlog TJ-0208). None in a
    US project, with no year, or when a taxable account with books has
    no gains file of the year (a partial RETURN row is no figure)."""
    from taxjson.lib.report_model import resolve_gains_files
    settings = settings_of(cfg)
    if _country(settings) in ("us", "usa") or settings.year is None:
        return None
    cache = Path(root) / "work"
    files = {a: p for a, p in resolve_gains_files(cache).items()
             if a in taxable}
    books = {a for a in taxable if (cache / f"{a}_base.json").exists()}
    if not files or not books <= set(files) \
            or _artifact_year_mismatch(files, settings.year):
        return None
    return _return_row_totals(cfg, files, int(settings.year))


def _replaced_totals_moved(old: Dict[str, Any], new: Dict[str, Any]
                           ) -> List[str]:
    """What moved from a replaced lock's totals to the new lock's, each as
    "realized 10.00 -> 10.01": money on any cent, in whole cents
    (`M.cents_apart`: float error never trips it, TJ-3381), the
    dispositions count on any change. No cent passes here: the replaced
    lock is the reference check-filed compares with, so a cent let through
    at each re-close would add up unseen (TJ-3390)."""
    moved = []
    for k in ("realized", "disallowed", "income", "dispositions"):
        try:
            a, b = float(old.get(k) or 0), float(new.get(k) or 0)
        except (TypeError, ValueError):
            continue
        count = k == "dispositions"
        if (abs(a - b) > 0) if count else (M.cents_apart(a, b) > 0):
            # A count reads as one, money as money (TJ-0189).
            p, cut = (0, M.units) if count else (2, M.shown)  # TJ-3347, TJ-3361
            moved.append(f"{k} {cut(a, p):,.{p}f} -> {cut(b, p):,.{p}f}")
    return moved


def _close_year_refusals(root: Path, cfg: Dict[str, Any], files: Dict[str, Path],
                         _accts: Dict[str, AccountSettings], cache: Path, year: Any,
                         args: argparse.Namespace, _nothing: str, _out: Any, _prog: str) -> None:
    """`tjs close-year`: the books are refused unless they are the clean
    result of the current inputs (--force locks open problems, never a run
    that did not finish), the cross-account pass built them (a deferred
    election named), their wash files are current and they are not older
    than the inputs; with --force, the open problems said
    (cmd_close_year)."""
    # The lock certifies the filing numbers: books with open problems are
    # not locked without --force (S048-23, S049-11), some never (below).
    _state = _run_state_problems(root, cfg)
    # Equity taxable accounts always get the blended cross-account pass
    # in a full run; a plain <acct>_gains.json means only `run --account`
    # ran (or an account is deferred) — per-account, unblended numbers
    # that check-filed's blended recompute then reports as DRIFT
    # (S046-01).
    _unblended = sorted(
        a for a, pth in files.items()
        if not account_of(_accts, a).crypto
        and not Path(pth).name.endswith("_gains_wash.json"))
    # Staleness guard: after `run --account X` the wash file predates the
    # rebuilt plain gains (the blend pass skipped): `tjs sum` notes this;
    # close-year WRITES the lock, so stale numbers are a hard stop.
    # A sheltered rebuild (`run --account <sheltered>`) refreshes
    # sheltered_base.json without the wash pass — the same stale lock
    # (2026-09 audit R1-251).
    from taxjson.lib.report_model import stale_wash_inputs
    stale = sorted(
        f"{a} (older than {', '.join(stale_wash_inputs(p))})"
        for a, p in files.items()
        if p.name.endswith("_gains_wash.json") and stale_wash_inputs(p))
    # A run deferred at an election skips the cross-account pass too: the
    # refusal names it, not an `--account` rerun (backlog TJ-0148).
    _deferred = sorted(
        n for n in (cfg.get("accounts") or {})
        if (cache / f"{n}_pending_elections.json").exists())
    _why_unblended = (
        f"The last `tjs run` deferred {', '.join(_deferred)} at "
        f"pending elections (`tjs elections --pending`), so its "
        f"cross-account pass never ran: make the elections, run "
        f"`tjs run`, then close." if _deferred else None)
    # --force locks books with open problems, but never books it cannot:
    # a run that did not finish (empty reports as filed, TJ-0015), books
    # the cross-account pass did not build or older than the inputs
    # (refused below, TJ-4305). The refusal offers --force when it helps.
    _unfinished, _changed = _RUN_UNFINISHED in _state, _books_older_than_inputs(
        root, cfg, read_upto=year)
    if _state and (not args.force or _unfinished):
        _forceable = not (_unfinished or _unblended or stale or _changed)
        _die("the books are not the clean result of the current inputs",
             *[f"- {_p}" for _p in _state],
             f"Fix and re-run `tjs run`, then close"
             + (" (or pass --force to lock them anyway)" if _forceable
                else "") + f". {_nothing}")
    if _unblended:
        _die(f"{', '.join(_unblended)} only has per-account (pre-wash, "
             f"unblended) gains",
             (_why_unblended or
              "The cross-account pass of a full `tjs run` never ran. "
              "Run `tjs run` with no --account first.")
             + f" {_nothing}")
    if stale:
        _die(f"wash-adjusted gains for {'; '.join(stale)} are STALE",
             (_why_unblended or
              "A --account rerun skipped the cross-account wash pass — "
              "run a full `tjs run` first.") + f" {_nothing}")
    if _changed:
        _die(_changed,
             f"Run `tjs run` first: --force never locks them. {_nothing}")
    if _state:
        _out.warn("locking books with open problems (--force)", prog=_prog,
                  details=[f"- {_p}" for _p in _state])


def _close_year_checks(option_timing_from_settings: Callable[..., Any], settings: Settings,
                       _ot_docs: Dict[str, Dict[str, Any]], _nothing: str,
                       args: argparse.Namespace, _handoff: Any, year: Any, taxjson_filed: Any,
                       root: Path, _out: Any, _json: Any, _prog: str, filed_locks: Any,
                       cfg: Dict[str, Any], files: Dict[str, Path]
                       ) -> Tuple[Optional[Path], Optional[Dict[str, Any]], bool]:
    """`tjs close-year`: the checks before anything is written: the gains
    files' option timing against taxjson.toml's, --filed-dispositions read
    (that year's rows only), an existing lock kept without --force, the
    filed/ folder writable, the lock being replaced read (its filed rows of
    that year only) and a partial freeze thawed, a year not ended refused
    without --force, a dividend compensation still open said. Returns the
    filed dispositions' file, the replaced lock and whether the year has
    not ended (cmd_close_year)."""
    # The lock's option timing describes its totals: it was stamped from
    # the CURRENT taxjson.toml, so a timing edited after the run locked
    # grant-timed totals as close timing and option-boundary advised a
    # T1-ADJ for a premium the lock already held (S046-02).
    _want_ot = option_timing_from_settings(settings) or {}
    _ot_bad = []
    for _a, _doc in sorted(_ot_docs.items()):
        _sm = _doc.get("summary") or {}
        if "option_premium_timing" not in _sm or not _want_ot:
            continue
        _got = (str(_sm.get("option_premium_timing") or "close"),
                _sm.get("option_grant_since"))
        _exp = (_want_ot.get("option_premium_timing"),
                _want_ot.get("option_grant_since"))
        if _got[0] != _exp[0] or (_exp[0] == "grant"
                                  and _got[1] != _exp[1]):
            _ot_bad.append(f"{_a}: built with {_got[0]} timing"
                           + (f" since {_got[1]}" if _got[0] == "grant"
                              else ""))
    if _ot_bad:
        _die("the gains files were built with another option timing than "
             "taxjson.toml now says",
             f"{'; '.join(_ot_bad)}; settings: {_exp[0]}"
             + (f" since {_exp[1]}" if _exp[0] == "grant" else "") + ".",
             f"Run `tjs run`, then close. {_nothing}")
    filed_csv = (Path(args.filed_dispositions).expanduser()
                 if getattr(args, "filed_dispositions", None) else None)
    if filed_csv is not None and not filed_csv.exists():
        _die_input(f"{filed_csv} not found.")
    if filed_csv is not None:
        # Read it up front, through the broker decode funnel: a UTF-16
        # save, a directory or a stray quote is one line naming the
        # file, exit 2, before anything is computed (A2-1136, A2-1138).
        # A row of another year is refused the same way: summed into
        # filed_totals it carried that year's loss into the next one
        # (backlog TJ-0298).
        try:
            _handoff.load_filed_dispositions(filed_csv, int(year))
        except ValueError as e:
            _die_input(str(e), _nothing)
    if taxjson_filed.snapshot_path(root, year).exists() and not args.force:
        _die(f"{_out.relpath(taxjson_filed.snapshot_path(root, year), root)}"
             f" already exists — the lock protects a filed year",
             "Re-run with --force to replace it (only if you "
             "re-filed/amended).")
    # Where the lock goes is checked before the inputs are frozen: a
    # file at filed/ failed the lock's write after taxjson.toml stopped
    # naming the shared exports (backlog TJ-0917).
    _lock_dir_bad = _lock_folder_problem(root)
    if _lock_dir_bad:       # an output it cannot write: exit 2 (TJ-4896)
        _die_input(f"cannot write filed/{year}.json: {_lock_dir_bad}",
                   f"Move it away (or make the folder writable), then "
                   f"close. {_nothing}")
    _old_lock = None
    if taxjson_filed.snapshot_path(root, year).exists():
        try:
            _old_lock = filed_locks.fold_lock_symbols(_json.loads(
                taxjson_filed.snapshot_path(root, year).read_text(encoding="utf-8")))
            if not isinstance(_old_lock, dict):
                raise ValueError("not a close-year lock")
        except (OSError, ValueError) as e:
            _out.warn(f"the lock being replaced could not be read: {e}",
                      prog=_prog,
                      details=["Anything it recorded (the filed totals, "
                               "dispositions filed with another tool) is "
                               "not carried into the new lock."])
            _old_lock = None
    if (filed_csv is None and _old_lock is not None
            and isinstance(_old_lock.get("filed_dispositions"), list)):
        # The filed dispositions --force keeps (below) hold the same
        # invariant as a new CSV: the replaced lock's year only, checked
        # before anything is thawed or written (TJ-0298).
        _other = filed_locks.filed_rows_outside(
            _old_lock["filed_dispositions"], int(year))
        if _other:
            _die_input(
                f"the lock being replaced records {len(_other)} filed "
                f"disposition(s) dated outside {year}: "
                f"{', '.join(_other[:5])}"
                + (f" and {len(_other) - 5} more" if len(_other) > 5
                   else ""),
                f"Its filed totals counted them in {year}. Pass "
                f"--filed-dispositions with the sales the {year} return "
                f"reported (that year's rows only). {_nothing}")
    # A partial lock an earlier release froze: thawed first, so this
    # close reads the whole year (workflow audit 2026-10-23).
    _thaw_partial_freeze(root, cfg, int(year), _old_lock,
                         nothing=_nothing)
    # The lock records a FILED return: a year that has not ended cannot
    # have been filed — the lock then drifted on every later run and
    # the checklist said "Return filed and the year locked" (S045-23).
    from datetime import date as _date_cy
    _partial_year = _date_cy.today() <= _date_cy(int(year), 12, 31)
    if _partial_year:
        if not args.force:
            _die(f"tax year {year} has not ended (today is "
                 f"{_date_cy.today().isoformat()})",
                 f"The lock records the FILED return; close the year "
                 f"after you file (or pass --force to snapshot it "
                 f"anyway). {_nothing}")
        _out.warn(f"tax year {year} has not ended — locking a partial "
                  f"year (--force)", prog=_prog,
                  details=[f"The inputs are not frozen: the year keeps "
                           f"reading its exports, so later trades in "
                           f"{year} show as drift. Close the year again "
                           f"after filing."])
    # Said under --yes too: this year's income moves if the short is
    # covered in time, and the lock would then drift (backlog TJ-1065).
    from taxjson.lib import readiness as _RD
    _open_comp = _RD.unsettled_compensation_lines(files.values())
    if _open_comp:
        _out.warn(f"{_count_noun(len(_open_comp), 'dividend compensation payment')}"
                  f" on a short still inside its 45 days: not settled",
                  prog=_prog, details=_open_comp)
    return filed_csv, _old_lock, _partial_year


def _close_year_extra(args: argparse.Namespace, root: Path, cfg: Dict[str, Any], year: Any,
                      filed_csv: Optional[Path], _nothing: str, _out: Any, _prog: str,
                      _handoff: Any, files: Dict[str, Path], settings: Settings,
                      _old_lock: Optional[Dict[str, Any]]
                      ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """`tjs close-year`: the checklist items before the lock that need
    attention, confirmed at a terminal (refused on a pipe without --yes);
    then the lock's hand-off fields (year-end positions, the dispositions,
    the filed ones the replaced lock kept), its carryforwards and the
    FX-on-cash ledger v2's close. Returns the fields and the carryforwards
    (cmd_close_year)."""
    _accepted = "--force" if args.force else None
    # The checklist's steps before the lock that need attention: listed
    # and confirmed on a terminal; without one, refused unless --yes
    # (new-user walkthrough: a year was locked with them open).
    if not getattr(args, "yes", False):
        _attn = _close_year_attention(root, cfg, int(year))
        if args.force or filed_csv is not None:
            # The split is decided later, on the filed figure, and --force has
            # answered it: asking asked a pipe for a flag it had (TJ-1475).
            _attn = [(sid, d) for sid, d in _attn
                     if not (sid == "carryover" and SPLIT_ATTENTION in d)]
        if _attn:
            _head = (f"{len(_attn)} checklist item(s) before the lock need "
                     f"attention")
            _items = [f"- {sid}: {detail}" for sid, detail in _attn]
            if not sys.stdin.isatty():
                _die(_head, *_items,
                     f"Fix them (`{_PROG} checklist` says how), or pass "
                     f"--yes to lock {year} anyway. {_nothing}")
            # The question is about these items: listed in the default
            # view too, as the piped refusal lists them (backlog TJ-0035).
            _out.show(_out.message("warning", _head, prog=_prog,
                                   details=_items, stream=sys.stderr),
                      sys.stderr)
            try:
                _ans = _ask(f"Lock {year} anyway? [y/N] ")
            except EOFError:
                _ans = ""
            if not _ans.strip().lower().startswith("y"):
                _die(f"{year} not locked", _nothing)
            # One decision: that yes covers the split too, listed above or
            # not. The warning names its sales (TJ-1463, TJ-1491).
            _accepted = _accepted or "answered yes"
    _step(f"Recording year-end positions and the {year} dispositions "
          f"for the {int(year) + 1} hand-off")
    try:
        extra = _handoff.record_fields(
            root, cfg, int(year), files, _filed_run_gains,
            _handoff_gains_flags(settings, root / "work"), filed_csv)
    except _handoff.BooksError as e:
        _die(f"{e}", _nothing)
    except ValueError as e:
        _die(str(e))
    page = Doc(exact=True, width_=0, live=True)
    if (filed_csv is None and _old_lock is not None
            and _old_lock.get("filed_dispositions") is not None):
        # --force (what check-filed's DRIFTED advice says) used to drop
        # the dispositions another tool actually filed, and handoff's
        # double-reporting check silently fell back to taxjson's own
        # (A2-0119). They describe the RETURN, not the books: keep them.
        extra["filed_dispositions"] = _old_lock["filed_dispositions"]
        if isinstance(_old_lock.get("filed_totals"), dict):
            extra["filed_totals"] = _old_lock["filed_totals"]
        page.line(_out.fill(
            f"Kept the {len(extra['filed_dispositions'])} filed "
            f"disposition(s) the replaced lock recorded"
            + (f" (from {_old_lock['filed_totals'].get('source')})"
               if isinstance(_old_lock.get("filed_totals"), dict)
               else "")
            + " — pass --filed-dispositions to replace them."))
    _cf = _carryforwards_for_lock(root, cfg, _details(args),
                                  extra.get("filed_totals"), _accepted)
    if _cf is not None:
        extra["carryforwards"] = _cf
    if _fx_cash_ledger_choice(settings) == "v2":
        # The FX-on-cash ledger v2's close (each account's foreign cash
        # and its cost, each debt): the next year's opening pool
        # (CA-FX-07). Only a COMPUTED ledger is carried.
        try:
            _v2doc = _fx_cash_v2_doc(root, root / "work")[0]
        except SystemExit as e:
            _v2doc = {"status": "not_computed",
                      "problems": [{"text": str(e.code or "")}]}
        if _v2doc.get("status") == "computed":
            extra["fx_cash_v2"] = _v2doc["close"]
            page.line(_out.fill(f"Recorded the FX-on-cash ledger v2's close "
                                f"for the {int(year) + 1} opening pool."))
        else:
            _out.warn("the FX-on-cash ledger v2 did not compute — its "
                      "close is not recorded (the next year needs "
                      "CASHOPEN lines)", prog=_prog,
                      details=[str((_v2doc.get("problems") or [{}])[0]
                                   .get("text", ""))])
    return extra, _cf


def _close_year_report(root: Path, year: Any, _prog: str, _json: Any, path: Path,
                       _old_lock: Optional[Dict[str, Any]], _out: Any, basis: str,
                       accounts: Dict[str, Any], _ret: Optional[Dict[str, float]],
                       filed_locks: Any, args: argparse.Namespace, extra: Dict[str, Any],
                       _frozen: Dict[str, Any], _cf: Optional[Dict[str, Any]],
                       _kept_reports: int) -> None:
    """`tjs close-year` once the lock is written (the reports as filed,
    `_kept_reports` files, kept before it): the replaced lock's other
    totals and the later years filed on its loss said, then the CLOSED
    block (the RETURN row's figures, the lock, the filed totals, what was
    frozen, the year-end positions), the carryforwards and a loss the
    books carry that the lock does not take in (cmd_close_year)."""
    from taxjson.lib.country import lock_denied_label
    tot = (_lock := _json.loads(path.read_text()))["totals"]
    _old_tot = (_old_lock or {}).get("totals")
    if isinstance(_old_tot, dict):
        # The replaced lock was the record of the filed return: say what
        # moved, so a re-close made only to record positions (handoff's
        # advice for a version-1 lock) does not overwrite the filed
        # totals in silence (A2-0345).
        _moved = _replaced_totals_moved(_old_tot, tot)
        if _moved:
            _out.warn(f"the replaced {year} lock recorded other totals: "
                      f"{'; '.join(_moved)}", prog=_prog,
                      details=[f"The lock is the record of the FILED "
                               f"return: this is right only if you amended "
                               f"{year} to these figures — otherwise "
                               f"restore the old lock from your records "
                               f"(git): `tjs check-filed` against it names "
                               f"any drift of more than a cent."])
    if _old_lock is not None:
        # A later year filed on the net capital loss the replaced lock
        # carried: the amendment reaches its return too (backlog
        # TJ-0430).
        for _ly, _lp, _ld, _cm in _later_locks_on(
                root, int(year), _json.loads(path.read_text())):
            _out.warn(f"{_ly} was filed on the {M.shown(_cm[0], 2):,.2f} net capital "
                      f"loss the replaced {year} lock carried; {year} now "
                      f"carries {M.shown(_cm[2], 2):,.2f}", prog=_prog,
                      details=[_carry_in_detail(
                          _ly, int(year), _cm,
                          _out.relpath(path, root),
                          ("`tjs check-filed` " if _ld == root else
                           f"`tjs -C {_year_dir_arg(_ld.parent, _ly)}"
                           f" check-filed` ")
                          + f"names it ({_lp}); amend {_ly} as {year}: "
                          f"`tjs run`, and after filing `tjs "
                          f"close-year --force` in {_ly}'s project.")])
    page = Doc(exact=True, width_=0, live=True)
    page.blank()
    page.line(f"CLOSED {year} — {basis}, {len(accounts)} taxable account(s)")
    # Realized and disallowed as the return files them (`tjs sum`'s
    # RETURN row), not the engine total a cent or two apart (TJ-0192).
    _shown = {**tot, **(_ret or {})}
    # Income as `tjs sum`'s taxable SUBTOTAL adds it (each
    # account's cents), not the lock's total rounded once (TJ-0212).
    _inc = filed_locks.income_as_summed({"accounts": accounts})
    if _inc is not None:
        _shown["income"] = _inc
    _rows = [("Realized", f"{M.shown(_shown['realized'], 2):,.2f}"),
             (lock_denied_label(_lock), f"{M.shown(_shown['disallowed'], 2):,.2f}"),
             ("Income", f"{M.shown(_shown['income'], 2):,.2f}")]
    _mw = max(len(v) for _, v in _rows)
    _kv = [(k, _out.Verbatim(f"{v:>{_mw}}")) for k, v in _rows]
    # Essentials first (docs/output-style.md): --details adds the why.
    _det = _details(args)
    _rnd = []
    if _det and _ret is not None and any(
            abs(float(tot[k]) - _ret[k]) > 0.005
            for k in ("realized", "disallowed")):
        _rnd.append(f"realized and disallowed are the RETURN "
                    f"row of `tjs sum` (each sale's cents); "
                    f"the engine totals the lock's drift check "
                    f"compares are {M.shown(tot['realized'], 2):,.2f} and "
                    f"{M.shown(tot['disallowed'], 2):,.2f}")
    if _det and abs(float(tot["income"]) - float(_shown["income"])) > 0.005:
        _rnd.append(f"income is the taxable SUBTOTAL of `tjs sum` "
                    f"(each account's cents); the lock's total the drift "
                    f"check compares is {M.shown(tot['income'], 2):,.2f}")
    if _rnd:
        _kv.append(("Rounding", "; ".join(_rnd)))
    _kv.append(("Lock", f"{_out.relpath(path, root)} — commit it with "
                        f"your records; `tjs check-filed` now guards "
                        f"it" if _det else
                        f"{_out.relpath(path, root)} — commit it with "
                        f"your records"))
    _ft = extra.get("filed_totals")
    if _ft:
        _kv.append(("As filed", f"{_ft['source']}: {_ft['dispositions']} "
                                f"dispositions, gain {M.shown(_ft['gain'], 2):,.2f}"
                                + (f" — the {int(year) + 1} project's "
                                   f"`tjs handoff` checks against "
                                   f"these" if _det else "")))
    _kv.append(("Frozen", _frozen_summary(_frozen, _kept_reports,
                                          int(year), _det)))
    _hn = _HF.listed_note(root, _frozen)
    if _hn:
        _kv.append(("Note", _hn))
    _gi = _gitignore_hides_filed_reports(root)
    if _gi and _kept_reports:
        # An older scaffold's unanchored rule: the reports as filed
        # would be left out of every commit (2026-10 workflow audit).
        _kv.append(("Note", f".gitignore's `{_gi}` also ignores "
                            f"filed/{year}/reports/ — change the line to "
                            f"`/reports/` so the reports as filed are "
                            f"committed"))
    _ye = extra.get("year_end") or {}
    _kv.append(("Year-end positions",
                (", ".join(f"{g} {len(v)}" for g, v in _ye.items())
                 or "none")
                + f"; trades settling in {int(year) + 1}: "
                f"{len(extra.get('settle_next_year') or [])}"))
    for _ln in _out.kv_lines(_kv, indent="  "):
        page.line(_ln)
    if _cf is not None:
        for _ln in _carryforwards_summary(_cf, int(year), _det):
            page.line(_ln)
        _bl = _books_loss_not_carried(root, _cf, int(year))
        if _bl is not None:
            page.blank()
            _out.show(_out.wrap(_out.labelled(
                f"note: these books carry a net capital loss of "
                f"{M.shown(_bl, 2):,.2f} into {year} (`tjs carryover`) that "
                f"the lock does not take in: with no [estimate] "
                f"other_losses and no earlier close-year record, "
                f"{year} started from 0.00. If that loss is yours "
                f"(your notice of assessment's balance), set "
                f"[estimate] other_losses to it, `tjs run`, then "
                f"`tjs close-year --force`."), None, "", ""), sys.stdout)


def cmd_close_year(args: argparse.Namespace) -> None:
    """`tjs close-year`: snapshot the current tax year's per-account
    filing aggregates to filed/<year>.json (the filed-year lock), all or
    nothing (lib/write_step `WriteStep`, the lock its commit file):
    whatever stops it before the lock is written puts back all it wrote
    and says nothing was written (`_not_closed`), and once the lock is
    written the year is closed (`_closed_anyway`), a Ctrl-C as it ends
    too; a Ctrl-C pressed again meanwhile waits, and one held or swallowed
    is raised before the lock and at the end (`raise_held`), never lost
    (backlog TJ-4627)."""
    state = WriteStep()
    try:
        state.hold_interrupts()
        _close_year(args, state)
        state.raise_held()
        state.release()
    except BaseException as e:
        try:
            while True:
                try:
                    # (an interrupt raised as it is called: the call again;
                    # held again when one as it ended let go of Ctrl-C)
                    state.hold_interrupts()
                    written = state.settle()
                    break
                except KeyboardInterrupt:
                    pass
            if not written:
                _not_closed(e)
            elif not (isinstance(e, SystemExit) and e.code in (None, 0)):
                _closed_anyway(Path(args.dir).resolve(), state)
        finally:
            state.release()
        raise


def _close_year(args: argparse.Namespace, state: WriteStep) -> None:
    """cmd_close_year's steps: the checks, then the freeze, the reports as
    filed and the lock as one step (`state`), the lock written last, then
    the report of the close."""
    from taxjson.bin import taxjson_filed
    from taxjson.lib import filed_locks
    from taxjson.lib.report_model import (filed_basis_label,
                                          resolve_gains_files)
    from taxjson.lib import out as _out
    _prog = "tjs close-year"
    _nothing = "Nothing was written."
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    _accts = accounts_of(cfg)
    settings = cfg["settings"]
    year = args.year or settings.year
    if year != settings.year:
        _die(f"the work/ gains artifacts are scoped to tax year "
             f"{settings.year}",
             "Set [settings].year to the year you are closing, run "
             "`tjs run`, then close.")
    taxable = {n for n, c in _accts.items()
               if c.taxable}
    files = {a: p for a, p in resolve_gains_files(cache).items()
             if a in taxable}
    if not files:
        _die("no taxable gains files in work/", "Run `tjs run` first.")
    # The books, inputs and reports stay as they are until the lock is
    # written: no `tjs run` meanwhile (backlog TJ-0924).
    _acquire_run_lock(cache, closing=True)
    _bad_year = _artifact_year_mismatch(files, settings.year)
    if _bad_year:
        _die(f"the work/ books were built for another tax year ("
             f"{', '.join(f'{a}: {y}' for a, y in sorted(_bad_year.items()))}"
             f") but [settings].year is {settings.year}",
             f"Rebuild with `tjs run` first, then close. {_nothing}")
    # A taxable account with inputs but no gains file (its stage
    # failed) was left out of the lock without a word, and then never
    # drift-checked (S006-07).
    _require_taxable_books(root, cfg, files, "gains file")
    _close_year_refusals(root, cfg, files, _accts, cache, year, args, _nothing, _out, _prog)
    accounts = {}
    raw_aggs = {}
    _ot_docs: Dict[str, Dict[str, Any]] = {}
    import json as _json
    for acct, pth in sorted(files.items()):
        _crypto = bool(account_of(_accts, acct).crypto)
        try:
            doc = _read_work_doc(Path(pth))
            _ot_docs[acct] = doc
            accounts[acct] = filed_locks.aggregates_from_gains(
                doc, crypto=_crypto, year=int(year))
            raw_aggs[acct] = filed_locks.aggregates_from_gains(
                doc, rounded=False)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            # Unreadable, not JSON, or JSON of the wrong shape (a list):
            # one line, never a traceback (S031-19), exit 2 (TJ-4896).
            _die_input(f"could not read {_out.relpath(pth, root)}: {e}",
                       f"Re-run `tjs run` to rebuild it. {_nothing}")
    # A year with no disposition and no income in any taxable book is
    # almost always a typo'd [settings] year (2015, 2204): the lock
    # then guarded an all-zero "filed" year as OK (S045-24).
    if (not any(int(a.get("dispositions") or 0) for a in accounts.values())
            and not any(abs(float(a.get("income") or 0.0)) >= 0.005
                        for a in accounts.values())):
        if not args.force:
            _die(f"the taxable books hold no disposition and no income "
                 f"in {year}",
                 f"Check [settings] year (pass --force to lock an empty "
                 f"year). {_nothing}")
        _out.warn(f"locking {year} with no disposition and no income "
                  f"(--force)", prog=_prog)
    basis = filed_basis_label(files)
    from taxjson.lib.pipeline import option_timing_from_settings
    from taxjson.lib import handoff as _handoff
    filed_csv, _old_lock, _partial_year = _close_year_checks(
        option_timing_from_settings, settings, _ot_docs, _nothing, args, _handoff, year,
        taxjson_filed, root, _out, _json, _prog, filed_locks, cfg, files)
    extra, _cf = _close_year_extra(args, root, cfg, year, filed_csv, _nothing, _out, _prog,
                                   _handoff, files, settings, _old_lock)
    # The exports the books were built from become this project's own
    # inputs/ (the lock records their hashes) before the lock is
    # written: the filed year stays reproducible while the shared
    # folder moves on with the next year.
    # A year that has not ended is hashed, not frozen: it keeps reading
    # the shared exports, so the rest of the year reaches the books
    # (and shows as drift against this snapshot) and the close after
    # filing freezes the whole year (workflow audit 2026-10-23).
    # The freeze, the reports as filed and the lock are one step, the
    # lock written last (the commit point): whatever stops the close
    # before it puts taxjson.toml, the copies and filed/ back, so
    # "Nothing was written" is true and the year goes on reading the
    # shared exports (backlog TJ-0917, TJ-4627).
    state.begin(taxjson_filed.snapshot_path(root, year))
    _frozen = _frozen_with_books(root, cfg, settings, int(year),
                                 nothing=_nothing,
                                 previous=(_old_lock or {}).get("frozen"),
                                 partial=_partial_year, undo=state.undo)
    extra["frozen"] = _frozen
    # The RETURN row's figures, as filed: what the CLOSED block and
    # `tjs years` show (display only; check-filed compares `totals`).
    _ret = _return_row_totals(cfg, files, int(year))
    if _ret is not None:
        extra["return_row"] = _ret
    # The year's T5 box 18 capital-gains dividends
    # [[capital_gains_dividends]] names (Canada; a US project's table is
    # refused with its config): capital gains of the year beside the
    # RETURN row's, so the next year's carryover counts them in the
    # carry-back room this year offers (backlog TJ-0898). The next
    # project's inputs no longer hold them.
    _slip_cg = _box18_by_year(root, taxable).get(int(year), 0.0)
    if abs(_slip_cg) > 0.005:
        extra["slip_gains"] = M.shown(_slip_cg, 2)
    _kept = _keep_filed_reports(root, int(year), nothing=_nothing, state=state)
    state.raise_held()          # a Ctrl-C the close went on after: not locked
    path = taxjson_filed.write_snapshot(
        root, year, _normalize_country(settings.country), basis,
        accounts, force=args.force,
        option_timing=option_timing_from_settings(settings) or None,
        extra=extra, raw=raw_aggs)
    # The year is closed: the reports a --force replaced go, and the rest
    # is the report of the close.
    state.committed = True
    state.settle(stopping=False)
    _close_year_report(root, year, _prog, _json, path, _old_lock, _out, basis, accounts,
                       _ret, filed_locks, args, extra, _frozen, _cf, _kept)


def _closed_anyway(root: Path, state: WriteStep) -> None:
    """Said when close-year stops once the lock is written, before the
    exception goes on: the year is closed, so it is not taken for a failed
    close (backlog TJ-4627)."""
    from taxjson.lib import out as _out
    lock = state.commit or root
    _out.warn(f"{lock.stem} is closed: {_out.relpath(lock, root)} is written, "
              f"but the report of the close stopped", prog=f"{_PROG} close-year",
              details=["`tjs years` shows the year filed and `tjs check-filed` "
                       "checks it: do not delete the lock or close the year "
                       "again for this."])


def _not_closed(e: BaseException) -> None:
    """close-year stopped before the lock was written, all it wrote put
    back (`WriteStep.settle`). A refusal says so itself, and an interrupt
    or a closed pipe goes on as it is. An input or output that cannot be
    read or written keeps the line and the exit 2 every command gives it
    (lib/cli_diag `input_error_text`), and says nothing was written. Any
    other error is one Error saying nothing was written, then goes on as
    it did (a traceback: a bug) (backlog TJ-4627)."""
    from taxjson.lib.cli_diag import input_error_text
    from taxjson.lib.corp_actions import ManifestError
    if isinstance(e, (SystemExit, KeyboardInterrupt, BrokenPipeError)):
        return
    text = str(e) if isinstance(e, ManifestError) else input_error_text(e)
    if text is not None:
        _die_input(text, "Nothing was written: fix that, then close again.")
    _say("error", "stopped before the lock was written: nothing was written",
         prog=f"{_PROG} close-year")


def _lock_folder_problem(root: Path) -> Optional[str]:
    """Why close-year could not write filed/<year>.json, checked before
    anything is frozen: filed/ is not a folder, or it (or the project
    folder that would hold it) cannot be written. None when it can."""
    import os as _os
    d = root / "filed"
    if d.is_symlink() or d.exists():
        if not d.is_dir():
            return "filed exists and is not a folder"
        if not _os.access(d, _os.W_OK | _os.X_OK):
            return "filed/ cannot be written (Permission denied)"
        return None
    if not _os.access(root, _os.W_OK | _os.X_OK):
        return "the project folder cannot be written (Permission denied)"
    return None


def _config_rewrite_target(root: Path, nothing: str, then: str = "close") -> Path:
    """The file close-year writes to rewrite taxjson.toml (its freeze
    and thaw; unclose-year's too, `then` "reopen"), checked before
    anything is written, as every editor of a
    user file checks it (lib/safe_write.write_user_file; backlog
    TJ-0918): a link inside the project is followed (the link stays,
    its target is written), a link leaving it is refused, and so is a
    read-only file (made read-only to keep it as it is)."""
    from taxjson.lib.cli_diag import cannot_write
    from taxjson.lib.safe_write import (ReadOnlyFileError, link_outside,
                                        refuse_read_only)
    cfg_path = root / _PL.CONFIG
    tgt = link_outside(cfg_path, root)
    if tgt is not None:
        _die_input(f"{_config_shown(cfg_path)} is a symlink to {tgt}, "
                   f"outside the project — never written through: "
                   f"replace the link with a copy, then {then}", nothing)
    real = cfg_path.resolve() if cfg_path.is_symlink() else cfg_path
    try:
        refuse_read_only(real)
    except ReadOnlyFileError as e:
        _h, _fix = cannot_write(_config_shown(cfg_path), e, cfg_path)
        _die_input(_h, *([_fix] if _fix else []), nothing)
    return real


_FROZEN_NOTE = ("## frozen by `tjs close-year`: this year reads its "
                "own copy of the exports it was filed from")
# The note above the second key the freeze comments out (the first
# carries _FROZEN_NOTE; the keys need not be adjacent).
_FROZEN_NOTE_2 = "## frozen by `tjs close-year` too"


def _one_frozen_note(text: str) -> str:
    """The second note dropped when its key directly follows the first
    key: one note reads over both (workflow audit 2026-10)."""
    lines = text.split("\n")
    for i, ln in enumerate(lines[:-2]):
        if ln == _FROZEN_NOTE and lines[i + 2] == _FROZEN_NOTE_2 \
                and lines[i + 1].startswith("# "):
            del lines[i + 2]
            break
    return "\n".join(lines)


def _gitignore_hides_filed_reports(root: Path) -> Optional[str]:
    """The `.gitignore` line (an unanchored `reports/`, the scaffold
    before this release) that also ignores filed/<year>/reports/, else
    None: the reports as filed would never be committed. The project's
    own file, else the one in the folder holding the year folders."""
    for d in (root, root.parent):
        try:
            lines = (d / ".gitignore").read_text(
                encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            if ln.strip() in ("reports/", "reports", "**/reports/"):
                return ln.strip()
        return None
    return None


def _user_layout_copies(root: Path, cfg) -> List[Tuple[Path, Path]]:
    """A generic-importer layout outside the project (the user's folder,
    or one taxjson ships) that the books are read with
    (checklist.input_files lists it): copied
    into the project's layouts/, which shadows the user's, so the frozen
    year reads its own copy (TJ-2146)."""
    from taxjson.lib.brokerages.generic import layout_dirs_of
    from taxjson.lib.checklist import input_files
    users = [Path(d).resolve() for d in layout_dirs_of(root)[1:]]
    return [(p, root / "layouts" / p.name) for p in input_files(root, cfg)
            if p.suffix.lower() == ".toml" and p.parent.resolve() in users]


def _refuse(input_error: bool, msg: str, *details: str) -> None:
    """A refusal of lib code (lib/holdings_freeze) as the command's error:
    exit 2 for an input, else 1."""
    (_die_input if input_error else _die)(msg, *details)


def _frozen_config_text(root: Path, shared: bool, shared_tob: bool,
                        shared_hold: bool) -> Tuple[str, List[str]]:
    """The freeze's taxjson.toml: (its text with the settings of the
    shared folders it copied commented out under close-year's notes,
    the problems the result's [settings] has — [] when none)."""
    text = _read_config_text(root / _PL.CONFIG)
    try:
        if shared:
            text = _PL.set_key_text(
                text, ("settings", _PL.INPUTS_KEY), None,
                note=_FROZEN_NOTE)
        if shared_hold:
            text = _PL.set_key_text(
                text, ("settings", _PL.HOLDINGS_KEY), None,
                note=_HF.FROZEN_NOTE)
        if shared_tob:
            text = _PL.set_key_text(
                text, ("settings", _PL.TOBASE_KEY), None,
                note=_FROZEN_NOTE_2 if shared else _FROZEN_NOTE)
        if shared and shared_tob:
            text = _one_frozen_note(text)
        return text, _PL.setting_problems(
            root, _PL.parse_config_text(text).get("settings") or {})
    except _PL.LayoutError as e:
        return text, [str(e)]


def _frozen_with_books(root: Path, cfg: Dict[str, Any], settings: Settings, year: int,
                       **freeze: Any) -> Dict[str, Any]:
    """The lock's `frozen` record: the freeze of the inputs
    (`_freeze_inputs`, which takes `freeze`) and the hashes of the books
    the lock is closed from (`books`, lib/checklist `book_hashes`): a lock
    put in place after a run is no change only when they are the books'
    then (`_locks_from_these_books`, TJ-4311)."""
    from taxjson.lib.checklist import book_hashes
    return dict(_freeze_inputs(root, cfg, settings, year, **freeze),
                books=book_hashes(root, cfg))


def _freeze_inputs(root: Path, cfg: Dict[str, Any],
                   settings: Settings, year: int, *,
                   nothing: str,
                   previous: Optional[Dict[str, Any]] = None,
                   partial: bool = False,
                   undo: Optional[List[Callable[[], None]]] = None
                   ) -> Dict[str, Any]:
    """close-year's freeze. With exports shared by every year
    (`inputs_dir`), every file the run read (lib/checklist.input_files)
    is copied into this project's own inputs/<account>/, the shared
    tobase.map beside ticker.map, a shared holdings folder's snapshots of
    the year into holdings/ (lib/holdings_freeze), and taxjson.toml stops
    naming the shared ones (commented out under _FROZEN_NOTE; lib/project_layout:
    a project with no `inputs_dir` reads its own inputs/). A project
    that already reads its own files only records them. Returns the
    lock's `frozen` record: the settings replaced (None when none),
    how many files were copied, and the hashes of every input
    (lib/checklist.input_fingerprint) the frozen books come from.
    Refuses (nothing written) when inputs/<account>/ already holds
    files this project does not read, or taxjson.toml cannot be
    edited safely. `partial` (a year that has not ended, locked with
    --force): nothing is copied and taxjson.toml is left alone — the
    year goes on reading the shared exports, so its later trades show
    as drift against the hashes — and the record says `partial`
    (workflow audit 2026-10-23). `undo`: given a list, the freeze
    appends, before its first write, the step that takes it back (the
    copies removed, taxjson.toml as it was), run when the lock is not
    written (backlog TJ-0917, TJ-4627)."""
    from taxjson.lib.checklist import (frozen_files, input_files,
                                       input_fingerprint)
    if partial:
        return {"partial": True, "inputs_dir": None, "tobase_map": None,
                "copied": 0,
                "inputs": frozen_files(input_fingerprint(root, cfg))}
    shared = _PL.shared_inputs(root)
    shared_tob = _PL.shared_tobase(root)
    shared_hold = _PL.shared_holdings(root)
    # taxjson.toml is checked before the first copy (backlog TJ-0918).
    cfg_real = (_config_rewrite_target(root, nothing)
                if shared or shared_tob or shared_hold else None)
    # A year frozen before (re-closed with --force) keeps the record
    # of the shared folders it stopped naming: new-year reads it.
    previous = previous if isinstance(previous, dict) else {}
    rec: Dict[str, Any] = {
        "inputs_dir": previous.get("inputs_dir") if not shared else None,
        "tobase_map": previous.get("tobase_map") if not shared_tob
        else None,
        "copied": 0}
    copies: List[Tuple[Path, Path]] = []
    if shared:
        src_dir = _PL.inputs_dir(root)
        own = root / _PL.INPUTS
        busy = []
        for a in sorted(cfg.get("accounts") or {}):
            try:
                if any(p.is_file() for p in (own / a).iterdir()):
                    busy.append(a)
            except OSError:
                continue
        if busy:
            _die(f"inputs/{busy[0]}/ already holds files this project "
                 f"does not read"
                 + (f" (and {len(busy) - 1} more account folder(s))"
                    if len(busy) > 1 else ""),
                 f"close-year copies each account's exports from "
                 f"{_PL.shown(src_dir, root)}/ into this project's "
                 f"inputs/ — move those files away (into the shared "
                 f"folder if they belong to the books), then close. "
                 f"{nothing}")
        from taxjson.lib import crypto_sends as _CS

        def _generated(p: Path) -> bool:
            try:
                return (p.name.lower() == _CS.TT_NAME and (
                    _CS.read_tt(p) or "").startswith(_CS.GENERATED_MARK))
            except ValueError:
                return False

        for p in input_files(root, cfg):
            try:
                rel = p.relative_to(src_dir)
            except ValueError:
                continue            # the project's own files
            if _generated(p):
                # An older taxjson's crypto_sends.tt in the shared
                # folder: the books never read it (_account_tt_files),
                # the frozen year would.
                continue
            copies.append((p, own / rel))
        rec["inputs_dir"] = settings.inputs_dir
    copies += _user_layout_copies(root, cfg)
    rec["copied"] = len(copies)             # the exports; tobase.map apart
    copies += _HF.freeze(root, cfg, settings.holdings_dir, shared_hold,
                         previous, rec, nothing, _refuse)
    if shared:
        # The crypto_sends.tt this year generated in work/ goes where the
        # frozen year reads and writes it, inputs/<account>/, so the lock
        # records it there: else the next run's copy was "a frozen input
        # changed — restore it" for good (backlog TJ-0089).
        for a in sorted(cfg.get("accounts") or {}):
            gen = _CS.tt_path(root, a)
            if gen.is_file():
                copies.append((gen, root / _PL.INPUTS / a / _CS.TT_NAME))
    if shared_tob:
        copies.append((_PL.tobase_map_path(root), root / _PL.TOBASE_MAP))
        rec["tobase_map"] = settings.tobase_map
    done: List[Path] = []
    made_dirs: List[Path] = []
    cfg_before: Optional[bytes] = None

    def _undo() -> None:
        # taxjson.toml as it was, when the freeze rewrote it (a second
        # call finds it as it was).
        if cfg_before is not None and cfg_real is not None:
            try:
                if cfg_real.read_bytes() != cfg_before:
                    _write_atomic(cfg_real, cfg_before, keep_mode=True)
            except OSError as e:
                _say("warning", f"taxjson.toml could not be put back "
                     f"as it was before close-year: {e.strerror or e}",
                     "It no longer names the shared folders: set "
                     "inputs_dir (tobase_map, holdings_dir) again by hand.",
                     prog=f"{_PROG} close-year")
        for d in done:
            with contextlib.suppress(OSError):
                d.unlink()
        # The folders the copies made, deepest first, when empty.
        for d in reversed(made_dirs):
            with contextlib.suppress(OSError):
                d.rmdir()

    if undo is not None:
        undo.append(_undo)          # before the first write (TJ-4627)
    src = dst = None
    try:
        for src, dst in copies:
            for _d in reversed([dst.parent, *dst.parent.parents]):
                if root in _d.parents and not _d.exists() \
                        and _d not in made_dirs:
                    made_dirs.append(_d)
            dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Recorded first: a halfway copy goes, a file there before stays.
            if not (dst.exists() or dst.is_symlink()):
                done.append(dst)
            shutil.copy2(src, dst)
    except OSError as e:
        _undo()
        _die_input(f"cannot copy {_PL.shown(src, root)} to "
                   f"{_PL.shown(dst, root)}: {e.strerror or e}", nothing)
    if shared or shared_tob or shared_hold:
        text, bad = _frozen_config_text(root, shared, shared_tob,
                                        shared_hold)
        if bad:
            _undo()
            _die(f"taxjson.toml cannot be pointed at the frozen copy: "
                 f"{bad[0]}", nothing)
        try:
            cfg_before = cfg_real.read_bytes()
            # A link inside the project stays a link: its target is
            # written, with its permission bits (backlog TJ-0918).
            _write_atomic(cfg_real, text, keep_mode=True)
        except OSError as e:
            _undo()
            _die_input(f"cannot rewrite taxjson.toml: {e.strerror or e}",
                       nothing)
    rec["inputs"] = frozen_files(input_fingerprint(root, cfg))
    return rec


def _thaw_text(text: str, keys: Sequence[str]) -> str:
    """taxjson.toml's text with the `keys` (inputs_dir / tobase_map)
    close-year's freeze commented out under its note set again: the
    note dropped, the commented line restored as it was."""
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith(_FROZEN_NOTE_PREFIX):
            j = i + 1
            thawed: List[str] = []
            while j < len(lines):
                m = re.match(r"#\s*(inputs_dir|tobase_map)(\s*=.*)$",
                             lines[j].strip())
                if not m or m.group(1) not in keys:
                    break
                thawed.append(m.group(1) + m.group(2))
                j += 1
            if thawed:
                out += thawed
                i = j
                continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def _thaw_partial_freeze(root: Path, cfg: Dict[str, Any], year: int,
                         old_lock: Any, *, nothing: str) -> None:
    """A lock taken before its year ended by a taxjson that froze it
    anyway (v0.31.0 / v0.31.1): the year reads a copy of the exports as
    they were then, so the rest of the year never reached its books and
    re-closing locked the partial figures (workflow audit 2026-10-23,
    item 7). close-year --force first thaws it — taxjson.toml names the
    shared folders the lock recorded again, the unchanged copies are
    removed, the lock's `frozen` record becomes a `partial` one (hashed
    only) — then stops, so the books are rebuilt from the shared
    exports before the year is closed. Returns when there is nothing to
    thaw; refuses, changing nothing, when a copy was edited or added
    since, or is not in the shared folder."""
    import json as _json
    from taxjson.bin import taxjson_filed
    from taxjson.lib import filed_locks
    fz = old_lock.get("frozen") if isinstance(old_lock, dict) else None
    if (not isinstance(fz, dict) or fz.get("partial")
            or not filed_locks.partial_year_note(old_lock, year)):
        return
    want: Dict[str, str] = {}
    for key, rk, shared in (
            (_PL.INPUTS_KEY, "inputs_dir", _PL.shared_inputs(root)),
            (_PL.TOBASE_KEY, "tobase_map", _PL.shared_tobase(root))):
        v = fz.get(rk)
        if isinstance(v, str) and v.strip() and not shared:
            want[key] = v
    if not want:
        return
    hashes = fz.get("inputs") if isinstance(fz.get("inputs"), dict) else {}

    def _shared_path(v: str) -> Path:
        q = Path(v).expanduser()
        return q if q.is_absolute() else root / q

    removes: List[Path] = []
    bad: List[str] = []
    if _PL.INPUTS_KEY in want:
        src_dir = _shared_path(want[_PL.INPUTS_KEY])
        own = root / _PL.INPUTS
        for a in sorted(cfg.get("accounts") or {}):
            try:
                files = sorted(p for p in (own / a).iterdir()
                               if p.is_file())
            except OSError:
                continue
            for p in files:
                key = f"{_PL.INPUTS}/{a}/{p.name}"
                try:
                    digest = hashlib.sha256(p.read_bytes()).hexdigest()
                except OSError as e:
                    bad.append(f"{key}: {e.strerror or e}")
                    continue
                if hashes.get(key) != digest:
                    bad.append(f"{key} ("
                               + ("changed since the freeze"
                                  if key in hashes
                                  else "added after the freeze") + ")")
                elif not (src_dir / a / p.name).is_file():
                    bad.append(f"{key} (not in "
                               f"{want[_PL.INPUTS_KEY]}/{a}/)")
                else:
                    removes.append(p)
    if _PL.TOBASE_KEY in want:
        own_tob = root / _PL.TOBASE_MAP
        if own_tob.is_file():
            try:
                digest = hashlib.sha256(own_tob.read_bytes()).hexdigest()
            except OSError as e:
                digest = ""
                bad.append(f"{_PL.TOBASE_MAP}: {e.strerror or e}")
            if digest and digest not in {v for k, v in hashes.items()
                                         if k.endswith(_PL.TOBASE_MAP)}:
                bad.append(f"{_PL.TOBASE_MAP} (changed since the freeze)")
            elif digest and not _shared_path(
                    want[_PL.TOBASE_KEY]).is_file():
                bad.append(f"{_PL.TOBASE_MAP} (no "
                           f"{want[_PL.TOBASE_KEY]})")
            elif digest:
                removes.append(own_tob)
    _at = str(old_lock.get("closed_at") or "")[:10]
    _what = (f"the {year} lock being replaced was taken on {_at}, before "
             f"the year ended, and froze a copy of the exports as they "
             f"were then: the books never got the rest of the year")
    if bad:
        _die(_what, *[f"- {b}" for b in bad[:6]],
             f"Move each into the shared folder (if it belongs to the "
             f"books) or out of the project; close-year then reads the "
             f"shared exports again. {nothing}")
    cfg_path = root / _PL.CONFIG
    text = _thaw_text(_read_config_text(cfg_path), list(want))
    try:
        for key, v in want.items():
            st = _PL.parse_config_text(text).get("settings") or {}
            if st.get(key) != v:
                text = _PL.set_key_text(text, ("settings", key), v,
                                        after=_PL.EXPORTS_KEY)
        _st = _PL.parse_config_text(text).get("settings") or {}
        # The frozen tobase.map beside the setting goes with the thaw.
        _both = (_PL.tobase_both_problem(root, _st)
                 if root / _PL.TOBASE_MAP in removes else None)
        bad_set = [b for b in _PL.setting_problems(root, _st)
                   if b != _both]
    except _PL.LayoutError as e:
        bad_set = [str(e)]
    if bad_set:
        _die(_what, f"taxjson.toml cannot name the shared folders again: "
             f"{bad_set[0]}", nothing)
    # Written through a link inside the project; a read-only one is
    # refused before anything changes (backlog TJ-0918).
    cfg_real = _config_rewrite_target(root, nothing)
    lock_path = taxjson_filed.snapshot_path(root, year)
    doc = dict(old_lock)
    doc["frozen"] = {"partial": True, "inputs_dir": None,
                     "tobase_map": None, "copied": 0, "inputs": hashes}
    try:
        _write_atomic(cfg_real, text, keep_mode=True)
        _write_atomic(lock_path, _json.dumps(doc, indent=2, sort_keys=True)
                      + "\n")
        for p in removes:
            p.unlink()
        for a in sorted(cfg.get("accounts") or {}):
            with contextlib.suppress(OSError):
                (root / _PL.INPUTS / a).rmdir()
        with contextlib.suppress(OSError):
            (root / _PL.INPUTS).rmdir()
    except OSError as e:
        _die_input(_what, f"the thaw did not finish: {e.strerror or e}",
                   f"Check that taxjson.toml names "
                   f"{' and '.join(want.values())} and remove the copies "
                   f"in {_PL.INPUTS}/ by hand, then `tjs run` and close.")
    _die(_what,
         f"Thawed: taxjson.toml reads {' and '.join(want.values())} "
         f"again, the {len(removes)} unchanged copied file(s) are "
         f"removed, and the {year} lock is a partial snapshot (its "
         f"inputs hashed, not frozen).",
         f"Run `tjs run` (later trades in {year} now show as drift), "
         f"then close the year after filing. Nothing was locked.")


def _keep_filed_reports(root: Path, year: int, *, nothing: str,
                        state: WriteStep) -> int:
    """The reports as filed, what `tjs compare filed` diffs against, put
    in place before the lock is written (close-year's commit point):
    reports/ copied into a new folder of this call's own in
    filed/<year>/, which then takes the name filed/<year>/reports/; one
    there already (--force) is moved into a holding folder of its own
    first. Each write's undo step (`state`) is added before the write:
    the new folder removed, the old one returned, the folders made
    removed; the holding folder goes once the lock is written. Returns
    the number of files kept. A report that cannot be read, or a folder
    that cannot be written, is a refusal (exit 2) with nothing written:
    it was a warning after the lock, the year closed without them
    (backlog TJ-4627)."""
    from taxjson.lib import compare as _cmp
    dest = _cmp.filed_reports_dir(root, year)
    # This call's own folders: named, and their undo steps added, before
    # they are made.
    _tag = secrets.token_hex(8)
    stage = dest.parent / f".reports.{_tag}.part"
    hold = dest.parent / f".reports.{_tag}.old"
    undo = state.undo

    def _new_out() -> None:
        if not stage.exists():          # renamed: the new reports
            shutil.rmtree(dest)

    def _old_back() -> None:
        old = hold / dest.name
        if old.exists() or old.is_symlink():
            try:
                old.rename(dest)
            except OSError as e:
                _say("warning", f"the reports as filed before this close "
                     f"could not be put back: {e.strerror or e}",
                     f"They are in {_PL.shown(old, root)}/: move them to "
                     f"{_PL.shown(dest, root)}/.", prog=f"{_PROG} close-year")
                raise

    try:
        files = _cmp.report_files(root / "reports")
        for d in (dest.parent.parent, dest.parent):     # filed/, filed/<year>/
            if not d.is_dir():
                undo.append(d.rmdir)
                d.mkdir()
        undo.append(lambda: shutil.rmtree(stage))
        stage.mkdir(mode=0o700)
        for name, data in files.items():
            _write_atomic(stage / name, data)
        if dest.exists() or dest.is_symlink():
            undo += [hold.rmdir, _old_back]     # hold/ empty once put back
            state.done.append(lambda: shutil.rmtree(hold))
            hold.mkdir(mode=0o700)
            dest.rename(hold / dest.name)
        undo.append(_new_out)
        stage.rename(dest)
    except (OSError, _cmp.CompareError) as e:
        why = (str(e) if isinstance(e, _cmp.CompareError) else
               f"{_PL.shown(dest, root)}/: {e.strerror or e}")
        _die_input(f"the reports as filed cannot be kept: {why}",
                   f"The lock is written only after them (`tjs compare "
                   f"filed` diffs against them): fix that, then close "
                   f"again. {nothing}")
    return len(files)


def _frozen_summary(rec: Dict[str, Any], kept: int, year: int,
                    details: bool) -> str:
    """close-year's `Frozen:` line: what was copied and where the
    reports as filed are; --details adds what reads them."""
    if rec.get("partial"):
        what = (f"nothing — {year} has not ended: it keeps reading its "
                f"exports, so later trades show as drift; the close "
                f"after filing freezes them")
        if kept:
            what += f"; reports so far: filed/{year}/reports/"
        if details:
            what += (f"; {len(rec.get('inputs') or {})} input file(s) "
                     f"hashed in the lock — `tjs check-filed` names "
                     f"each one that changes")
        return what
    if rec.get("copied"):
        what = (f"inputs/ ({rec['copied']} file(s) copied from "
                f"{rec.get('inputs_dir') or 'the shared folder'}"
                + (f", tobase.map from {rec['tobase_map']}"
                   if rec.get("tobase_map") else "") + ")")
    elif rec.get("tobase_map") and not rec.get("inputs_dir"):
        what = (f"the project's own inputs/; tobase.map copied from "
                f"{rec['tobase_map']}")
    else:
        what = "the project's own inputs/" + (
            f" (frozen earlier from {rec['inputs_dir']})"
            if rec.get("inputs_dir") else "")
    what += _HF.summary(rec, year)          # (the snapshots: TJ-5367)
    if kept:
        what += f"; reports as filed: filed/{year}/reports/"
    if details:
        what += (f"; {len(rec.get('inputs') or {})} input file(s) hashed "
                 f"in the lock — taxjson.toml reads the copy from now on, "
                 f"`tjs check-filed` says when a frozen input "
                 f"changes, `tjs compare filed` diffs the reports")
    return what


def _carryforwards_for_lock(root: Path, cfg: Dict[str, Any],
                            details: bool = True,
                            filed_totals: Optional[Dict[str, Any]] = None,
                            accepted: Optional[str] = None
                            ) -> Optional[Dict[str, Any]]:
    """The `carryforwards` block close-year writes (lib/carryforward):
    the year's net capital loss (Canada) or ST/LT capital loss
    carryover (USA), and the Canadian minimum tax carryover — from the
    estimate itself (`tjs estimate --json`, the computation `amt`
    prints), with the inputs the project gives it. A Canada project
    with no supported province gets a federal-only estimate (no other
    province's tables stand in; every figure the block carries forward
    is federal), with a note. A failing estimate stops the close (its
    inputs are wrong), naming the reason; so does a carryover ledger
    that a recorded claim needs and that cannot be used, and, without
    `force`, one that left sales out of the split. `filed_totals`
    (Canada; the dispositions filed with another tool,
    --filed-dispositions): the net capital loss is the filed return's —
    its gain stands in for the books' dispositions (CA-CARRY-01)."""
    import json as _json
    from taxjson.lib import carryforward as CF
    from taxjson.pipeline.dispatch import run_cmd as _run
    from taxjson.lib.tax_estimate import _VINTAGES
    settings = settings_of(cfg)
    country = _country(settings)
    argv = _self_cmd(root, "estimate", "--json")
    prov = str(settings.province or "").strip().upper()
    federal_only = False
    if country == "canada" and prov not in {
            k for v in _VINTAGES.values() for k in v["CA_PROVINCES"]}:
        argv += ["--federal-only"]
        federal_only = True
        from taxjson.lib.out import fill as _fill
        from taxjson.lib.out import label as _label
        from taxjson.lib.out import show as _show, width as _ow
        _show(_fill(_label("note", stream=sys.stdout)
                    + (f"province {prov} is not modelled" if prov else
                       "no [settings] province")
                    + (" — the carry-forwards are computed federal-only "
                       "(no provincial tax or minimum-tax recovery)."
                       if details else
                       " — the carry-forwards are federal-only."),
                    hang="  " if _ow(sys.stdout) <= 0 else "").split("\n"),
              sys.stdout)
    res = _run(argv, capture_output=True)
    if res.returncode != 0:
        _die("could not compute the carry-forwards the lock records (the "
             "estimate failed)", _child_error(res.stderr),
             "Fix the input, then close. Nothing was written.")
    _doc = _json.loads(res.stdout) or {}
    r = _doc.get("estimate") or {}
    _stale = _stale_opening(root, (r.get("carry_sources") or {}).get(
        "other_losses")) if country == "canada" else None
    if _stale:
        # A filing command refuses stale input (the owner's rule; TJ-4883).
        _die("the net capital loss carried into this year comes from a "
             "close-year lock that is stale", _stale + ". Nothing was written.")
    est_cfg = estimate_of(cfg)
    src = dict(r.get("carry_sources") or {})
    src["other_income_entered"] = est_cfg.has("other_income")
    src["other_income_value"] = est_cfg.other_income or 0.0
    block = CF.record_block(country, r, src)
    ncl = block.get("net_capital_loss")
    if (isinstance(ncl, dict) and isinstance(filed_totals, dict)
            and filed_totals.get("gain") is not None):
        _as_filed(ncl, _doc, float(filed_totals["gain"]))
    _filed_gain = ((filed_totals or {}).get("gain")
                   if isinstance(filed_totals, dict) else None)
    if isinstance(ncl, dict) and float(ncl.get("closing") or 0.0) > 0.005:
        _split_for_lock(root, cfg, settings, r, ncl, accepted, _filed_gain)
    elif isinstance(ncl, dict) and _filed_gain is None:
        _gain_left_out(root, cfg, settings, ncl, accepted)  # cli/common; TJ-1488
    mt = block.get("minimum_tax")
    if federal_only and isinstance(mt, dict):
        mt["province"] = None
        mt.pop("recovered_provincial", None)
        mt.pop("recovered_provincial_credit", None)
        mt.pop("recovered_provincial_surtax", None)
    # The estimate's own warnings are caveats on the figures locked (tainted
    # sales EXCLUDED): relayed as `amt` and `instalments` do (backlog TJ-1434).
    # Not the carry-back's or the run state's: this close has answered them
    # (TJ-1465).
    if (res.stderr or "").strip():
        _relay_child_stderr(res.stderr, handled=(
            "carry-back of this year's loss", "this year's carry-back may change",
            "these books are not the clean result of the current inputs"))
    return block


def _split_for_lock(root: Path, cfg: Dict[str, Any], settings: Settings,
                    r: Dict[str, Any], ncl: Dict[str, Any],
                    accepted: Optional[str],
                    filed_gain: Optional[float] = None) -> None:
    """Take the T1A carry-back out of the loss the lock carries forward: a
    carry-back of this year's loss ([carryover] claimed under an earlier year)
    leaves it with the next year, and the estimate's lock never read it, so it
    was claimed twice. `ncl` holds a balance to split.
    `filed_gain`: this close's filed return, so a build of its own on that net;
    the estimate's ledger is on the books (backlog TJ-1493).
    """
    def stop(why: Any) -> None:
        # Not 0: the lock would carry forward what was carried back, claimed
        # twice (backlog TJ-1422).
        _die("could not compute the carry-forwards the lock records (the "
             "carryover ledger could not be used)", str(why),
             "Run `tjs carryover` to see why, then close. Nothing was written.")

    if r.get("carry_back_unknown"):
        stop(r["carry_back_unknown"])
    year = settings.year
    back = float(r.get("losses_carried_back") or 0.0)
    incomplete, stated = r.get("carry_back_incomplete"), r.get("carry_back_stated")
    to: Any = (r.get("losses_carried_back_to"), r.get("carry_back_split"),
               r.get("carry_back_stated_to"))
    if incomplete is not None and not (
            isinstance(incomplete, dict)
            and isinstance(incomplete.get("years"), list)):
        stop(f"`tjs estimate --json` gave carry_back_incomplete {incomplete!r}")
    if _claims_back(cfg, year) and (filed_gain is not None
                                    or "losses_carried_back" not in r):
        try:
            cb = _carry_back(root, int(year or 0),
                             float(ncl.get("created") or 0.0), filed_gain,
                             balance=float(ncl["closing"]),
                             opening=float(ncl.get("opening") or 0.0),
                             typed=_typed_opening(ncl.get("opening_source")))
        except LedgerUnavailable as e:
            stop(e)
        back, incomplete, stated = cb.amount, cb.incomplete, cb.stated
        to = (cb.to, cb.to_split, cb.stated_to)
    if incomplete:
        from taxjson.lib.carry_back import incomplete_record
        _stop_on_incomplete_split(incomplete, accepted, int(year or 0),
                                  float(ncl.get("created") or 0.0) > 0.005)
        ncl["carry_back_incomplete"] = incomplete_record(incomplete)
    if stated:
        # The split rests on the person's own figures for those years.
        ncl["carry_back_stated"] = stated
    if back > 0.005:
        # At most the loss the lock holds (a filed loss below the books'
        # one): the rest of the claim stays for a later year's loss.
        back = min(back, float(ncl["closing"]))
        ncl["carried_back"] = M.shown(back, 2)
        ncl["closing"] = M.shown(max(0.0, ncl["closing"] - back), 2)
    from taxjson.lib.carry_back import lock_split
    why = lock_split(ncl, back, *to)
    if why:
        stop(why)


def _as_filed(ncl: Dict[str, Any], doc: Dict[str, Any],
              filed_gain: float) -> None:
    """Make the lock's net capital loss block `ncl` the filed return's:
    the dispositions filed with another tool (gain `filed_gain`) stand
    in for the books' (`doc`, the year's `estimate --json`); the T5 box
    18 capital gains the estimate adds stay. The next year's carryover
    reads the filed gain from the lock, so the balance the lock carries
    out comes from it too (workflow audit 2026-10-23 item 4)."""
    filing = (doc.get("filing") or {}).get("totals") or {}
    books = filing.get("gain")
    if books is None:
        books = (doc.get("totals") or {}).get("realized")
    if books is None:
        return
    books = float(books)
    if abs(books - filed_gain) <= 0.005:
        return
    est = doc.get("estimate") or {}
    realized = (filed_gain + float(est.get("capital_gains_dividends")
                                   or 0.0))
    opening = float(ncl.get("opening") or 0.0)
    ncl["created"] = M.shown(max(0.0, -realized), 2)
    ncl["applied"] = M.shown(min(max(0.0, realized), opening), 2)
    ncl["closing"] = M.shown(max(0.0, opening - realized), 2)
    ncl["filed_gain"] = M.shown(filed_gain, 2)
    ncl["books_gain"] = M.shown(books, 2)


def _carry_back_for_handoff(root: Path, cfg: Dict[str, Any],
                            settings: Settings) -> Any:
    """What the carryover ledger says for handoff's claim comparison
    (lib/carryforward.LedgerCarryBack; backlog TJ-1426). No claim under an
    earlier year: no ledger."""
    from taxjson.lib.carry_back import (cant_tell_text, left_out_text, split_left_out,
                                        split_text)
    from taxjson.lib.carryforward import LedgerCarryBack
    if _country(settings) != "canada" \
            or not _claims_back(cfg, settings.year):
        return LedgerCarryBack()
    year = int(settings.year or 0)
    try:
        doc = _ledger_doc(root)
        why = cant_tell_text(doc)
    except (LedgerUnavailable, ValueError) as e:
        return LedgerCarryBack(unknown=str(e))
    if why:
        return LedgerCarryBack(unknown=why)
    amounts: Dict[int, float] = {}
    proposed = False
    for x in doc["rows"]:
        if x["year"] == year:
            for y, a in _carried_back_to(x).items():
                amounts[y] = amounts.get(y, 0.0) + a
            # The years are the ledger's proposal, another split open (TJ-4882).
            proposed = proposed or bool(x.get("carried_back_split"))
    left = split_left_out(doc, year)
    return LedgerCarryBack(amounts=amounts,
                           incomplete=left_out_text(left) if left else "",
                           proposed=split_text(amounts) if proposed else "")


def _books_loss_not_carried(root: Path, cf: Dict[str, Any],
                            year: int) -> Optional[float]:
    """The net capital loss `tjs carryover` finds carried into
    `year` from the books' earlier years when the lock's block `cf`
    took none in (no [estimate] other_losses, no earlier close-year
    record: it starts from 0); None when it took one in, or the books
    show none. A first year with taxjson locked 0 carried forward in
    silence while the same project's ledger showed the loss (backlog
    TJ-0431). None with a warning when the ledger cannot be used: the
    check did not run (backlog TJ-1427)."""
    ncl = cf.get("net_capital_loss") if isinstance(cf, dict) else None
    if not isinstance(ncl, dict) or str(ncl.get("opening_source")
                                        or "none") != "none" \
            or abs(float(ncl.get("opening") or 0.0)) > 0.005:
        return None
    try:
        rows = [x for x in _ledger_doc(root)["rows"] if x["year"] < year]
        bal = _ledger_amount(rows[-1] if rows else {},
                             "carryforward_balance")
    except LedgerUnavailable as e:
        from taxjson.lib.out import warn as _warn
        _warn(f"could not check the loss these books carry into {year} (`tjs "
              f"carryover`): the carryover ledger could not be used",
              prog=_cmd_prog(), details=[str(e)])
        return None
    return bal if bal > 0.005 else None


def _carryforwards_summary(cf: Dict[str, Any], year: int,
                           details: bool = True) -> List[str]:
    """close-year's lines on what the lock carries into year + 1
    (`details`: the notes in full and what reads them)."""
    from taxjson.lib.carry_back import by_year_said, split_proposed_said
    from taxjson.lib.out import kv_lines, labelled, wrap
    kv: List[Tuple[str, str]] = []
    notes: List[str] = []
    ncl = cf.get("net_capital_loss")
    if isinstance(ncl, dict):
        kv.append(("Net capital loss",
                   f"applied {M.shown(ncl['applied'], 2):,.2f}, created "
                   f"{M.shown(ncl['created'], 2):,.2f}"
                   + (f" (as filed: gain {M.shown(ncl['filed_gain'], 2):,.2f}; the "
                      f"books {M.shown(ncl['books_gain'], 2):,.2f})"
                      if ncl.get("filed_gain") is not None else "")
                   + (f", carried back {M.shown(ncl['carried_back'], 2):,.2f}"
                      if ncl.get("carried_back") else "")
                   + f", carried into {year + 1}: "
                   f"{M.shown(ncl['closing'], 2):,.2f} (100% amounts)"
                   + "".join(f"; {y}'s carry-back {M.shown(a, 2):,.2f} is your "
                             f"figure ([carryover] carried_back)" for y, a in
                             sorted((ncl.get("carry_back_stated") or {}).items()))
                   + by_year_said(ncl)))
        notes += [n for n in [split_proposed_said(ncl, year)] if n]
    cl = cf.get("capital_loss")
    if isinstance(cl, dict):
        from taxjson.lib.carryforward import CLAIMED_KEY
        _claimed = cl.get("deduction_from") == CLAIMED_KEY
        kv.append((f"Capital loss carryover into {year + 1}",
                   f"short-term {M.shown(cl['st_closing'], 2):,.2f}, long-term "
                   f"{M.shown(cl['lt_closing'], 2):,.2f}"))
        if _claimed:
            kv.append((f"Deducted in {year}",
                       f"{M.shown(cl['deduction_used'], 2):,.2f} ({CLAIMED_KEY})"))
        _avail = float(cl.get("deduction_available") or 0.0)
        _used = float(cl.get("deduction_used") or 0.0)
        if not _claimed and not cl.get("other_income_entered") \
                and "deduction_used" in cl and _avail - _used > 0.005:
            # The lock counts the deduction as used only as far as the
            # estimate's taxable income absorbs it: with no other income
            # entered, none of it (backlog TJ-0464).
            notes.append(
                f"note: no [estimate] other_income and no {CLAIMED_KEY} "
                f"entry for {year}: {M.shown(_used, 2):,.2f} of the {M.shown(_avail, 2):,.2f} "
                f"deduction against ordinary income is counted as used"
                + (", so the carryover above is too high if your return "
                   "took more. Record the Schedule D line 21 deduction "
                   f"your return took under {CLAIMED_KEY} (or set "
                   f"[estimate] other_income), then close {year} again "
                   f"with --force." if details else
                   f" — record your return's under {CLAIMED_KEY} and "
                   f"close again with --force."))
        _s1256 = float(cl.get("section_1256_gain") or 0.0)
        if details and abs(_s1256) > 0.005 and (
                float(cl.get("st_closing") or 0.0)
                + float(cl.get("lt_closing") or 0.0) > 0.005):
            # The carryover took the §1256 net as Form 6781 splits it
            # (US-FUT-03; backlog TJ-0466); what is still by hand.
            notes.append(
                f"note: the carryover takes the {year} §1256 net, "
                f"{M.shown(_s1256, 2):,.2f}, as Form 6781 splits it: "
                f"{M.shown(float(cl.get('section_1256_short_term') or 0.0), 2):,.2f} "
                f"short-term (40%), "
                f"{M.shown(float(cl.get('section_1256_long_term') or 0.0), 2):,.2f} "
                f"long-term (60%). Not modelled: a contract still open "
                f"at Dec 31 is not marked to market, and a net §1256 loss "
                f"is not carried back (§1212(c)); check Form 6781 against "
                f"your 1099-B.")
    mt = cf.get("minimum_tax")
    if isinstance(mt, dict):
        kv.append(("Minimum tax",
                   f"recovered {M.shown(mt['recovered_federal'], 2):,.2f}, created "
                   f"{M.shown(mt['created'], 2):,.2f}, carried into {year + 1}: "
                   f"{M.shown(mt['closing'], 2):,.2f}"))
        if not mt.get("other_income_entered") and details:
            notes.append("note: the minimum tax was computed with no "
                         "[estimate] other_income — compare it with the "
                         "T691 you filed; the next project's [estimate] "
                         "amt_carryover (from your notice of assessment) "
                         "wins over this record.")
        elif not mt.get("other_income_entered"):
            notes.append("note: no [estimate] other_income — compare the "
                         "minimum tax with the T691 you filed.")
    if not kv:
        return []
    out = ["", f"CARRIED INTO {year + 1}"] + kv_lines(kv, indent="  ")
    if notes:
        # A message, not a row of the block: flush-left under it.
        out.append("")
    for i, n in enumerate(notes):
        block = wrap(labelled(n), None, "", "")
        out += block
        if len(block) > 1 and i + 1 < len(notes):
            out.append("")      # one blank line after a wrapped message
    if details:
        # `amt` reads the minimum tax block only (Canada): a US lock has
        # none, and `amt` refuses a US project (backlog TJ-0473).
        out += wrap(f"The {year + 1} project's estimate"
                    + (", carryover and amt" if isinstance(mt, dict)
                       else " and carryover")
                    + " read these; its handoff checks its inputs "
                    "against them.", None, "  ", "  ")
    return out


def _handoff_gains_flags(settings: Settings,
                         cache: Optional[Path] = None) -> List[str]:
    """The taxjson-gains flags a full-history run of this project uses
    (no --year: the hand-off needs every year's pools); `cache`, the
    project's work/, adds its .tt ALLOWLOSS positions."""
    from taxjson.lib.pipeline import option_timing_flags
    country = _country(settings)
    flags = ["--country", country, "--tax-date", _tax_date(settings)]
    if country in ("us", "usa"):
        flags.append("--per-account-basis")
    return (flags + option_timing_flags(settings)
            + income_dating_flags(settings)
            + (_loss_override_flags(cache) if cache is not None else []))


def _prior_record_path(root: Path, settings: Settings,
                       override: Optional[str]) -> Path:
    if override:
        return Path(override).expanduser()
    from taxjson.lib.filed_locks import PriorRecordError, prior_record_setting
    try:
        p = prior_record_setting(root, settings)
    except PriorRecordError as e:
        # The same refusal `tjs run` gives — not "no prior-year
        # record at <root>/5" (A2-1135).
        _die(str(e))
    if p is not None:
        return p
    year = settings.year
    if not isinstance(year, int) or isinstance(year, bool):
        # It looked for filed/-1.json (A2-1162).
        _die("[settings] year is required in taxjson.toml",
             "The hand-off is from the year before it; or pass --prior.")
    return root / "filed" / f"{year - 1}.json"


def _handoff_positions(rep: Dict[str, Any], cache: Path, ry: int,
                       settings: Settings) -> None:
    """The .tt ALLOWLOSS filing positions on sales up to the closed
    year `ry` (CA-SL-18 / US-WASH-25): they shape the opening cost the
    hand-off compares (no ACB / basis raised for the replacement), so
    the report names them (`filing_positions` and a note; pre-release
    review). The closed year's project must take the same position."""
    from taxjson.lib import loss_overrides as _LO
    from taxjson.lib.report_model import resolve_gains_files
    if not _LO.read_state(cache):
        return
    key = "date" if _tax_date_basis(settings) == "trade" else "date_settle"
    fps = [fp for fp in _LO.positions(_LO.gather(resolve_gains_files(cache)))
           if str(fp.get(key) or fp.get("date"))[:4] <= str(ry)]
    if not fps:
        return
    rep["filing_positions"] = [
        {k: v for k, v in fp.items() if k != "in_year"} for fp in fps]
    us = _country(settings) in ("us", "usa")
    rep.setdefault("notes", []).append(
        f"{len(fps)} filing position(s) taken against the "
        + ("wash-sale rule (§1091)" if us else "superficial-loss rule (s.54)")
        + f" on sales up to {ry} (.tt ALLOWLOSS): "
        + "; ".join(f"{fp['date']} {fp['symbol']}, the rule would "
                    f"{'disallow' if us else 'deny'} "
                    f"{M.shown(fp['would_disallow'], 2):,.2f} ({fp['where']})"
                    for fp in fps)
        + f". The opening " + ("basis" if us else "ACB")
        + " here carries no denied loss for them; the " + str(ry)
        + " project must hold the same line, or its closing cost differs.")


def cmd_handoff(args: argparse.Namespace) -> None:
    """`tjs handoff`: check this project against the previous year's
    close-year record — opening positions and cost at Dec 31, trades
    that settle across Dec 31, and sales reported in both years
    (lib/handoff). Exit 1 on any problem."""
    from taxjson.lib import handoff as _handoff
    from taxjson.lib import out as _out
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    settings = cfg["settings"]
    rp = _prior_record_path(root, settings, args.prior)
    _rp_label = _out.relpath(rp, root)
    if not rp.exists():
        # A missing named input: exit 2, not handoff's "problems found"
        # code 1 (re-audit A2-0164). A filed year's deleted lock says so,
        # not to set prior_year_record (backlog TJ-0272).
        from taxjson.lib.filed_locks import missing_lock_problem
        _gone = missing_lock_problem(
            rp, _rp_label if args.prior else
            f"[settings] prior_year_record "
            f"({settings.prior_year_record})")
        if _gone:
            _die_input(_gone)
        _die_input(f"no prior-year record at {_rp_label}",
                   "Run `tjs close-year` in the previous year's "
                   "project, then set [settings] prior_year_record to its "
                   "filed/<year>.json (or pass --prior).")
    # A truncated, non-UTF-8, unreadable or wrong-shape record, or a field
    # of the wrong type: one line naming it, exit 2 (re-audit A2-0162,
    # A2-0473, A2-0475, A2-0476, A2-0803; a BOM is accepted, A2-0776).
    try:
        record = _handoff.load_record(rp)
    except _handoff.RecordError as e:
        _die_input(str(e))
    _rprob = _handoff.record_problem(record)
    if _rprob:
        _die_input(f"{_rp_label}: not a usable close-year record: "
                   f"{_rprob}",
                   "Restore it (or re-close that year with `tjs "
                   "close-year --force` in its project).")
    from taxjson.lib.filed_locks import lock_country_problem
    _cp = lock_country_problem(record, settings, str(rp), prior=True)
    if _cp:
        # The other country's closing positions and cost are not this
        # project's opening (a different basis rule and wash rule):
        # "keep or amend" advice on them would be wrong (COMMANDS-08).
        _die(_cp)
    if int(record.get("schema_version") or 1) < 2 \
            or "year_end" not in record:
        _die(f"{_rp_label} is a version-1 lock (totals only)",
             "Re-close that year with the current taxjson (`tjs "
             "close-year --force` in its project) to record positions — "
             "run `tjs check-filed` there first: if it reports "
             "DRIFTED, the re-close would replace the filed totals, so "
             "resolve the drift (or amend) before re-closing.")
    ry = int(record["year"])
    # A record of another year than the one before this project's is a
    # problem of the record in the report (lib/handoff.check; backlog
    # TJ-0555), not a note beside a clean verdict.
    if not (root / "work").is_dir():
        _die("no work/", "Run `tjs run` first.")
    from taxjson.characterize.income_dating import IncomeRulesError
    try:
        _hflags = _handoff_gains_flags(settings, root / "work")
    except IncomeRulesError as e:
        # A bad [settings] corporate_distributions & co.: one line, as
        # divs-sum gives it (re-audit A2-0802).
        _die(str(e))
    try:
        # A US project's lots at that Dec 31 too (backlog TJ-0554).
        _open_lots: Dict[str, Any] = {}
        _open_shorts: Dict[str, Any] = {}
        opening = _handoff.snapshot(root / "work", cfg, f"{ry}-12-31",
                                    _filed_run_gains, _hflags,
                                    _missing_history_arg(root),
                                    lots_out=_open_lots,
                                    shorts_out=_open_shorts)
        # A claim under the closed year that this project's own loss
        # carries back (T1A) is not a difference from its record: the
        # record was closed before the carry-back (backlog TJ-0078).
        rep = _handoff.check(root, cfg, record, opening,
                             carry_back=_carry_back_for_handoff(
                                 root, cfg, settings),
                             opening_lots=_open_lots,
                             opening_shorts=_open_shorts)
    except _handoff.BooksError as e:
        # Not a fabricated "a lot or a sale is missing" (A2-1137).
        _die(str(e))
    _handoff_positions(rep, root / "work", ry, settings)
    page = Doc(exact=True, width_=0, live=True)
    if getattr(args, "json", False):
        _json_out(dict(rep, record=str(rp)), schema="handoff")
    else:
        for ln in _handoff.render(rep, _rp_label,
                                  int(settings.year or 0) or None):
            page.line(ln)
    if rep["problems"]:
        raise SystemExit(1)
    if rep.get("carry_unchecked"):
        raise SystemExit(2)


def _later_locks_on(root: Path, year: int, lock: Any
                    ) -> List[Tuple[int, str, Path,
                                    Tuple[float, float, float]]]:
    """[(later year, its lock shown from `root`, its folder, what
    _carry_in_moved found)] — each close-year lock of a later year,
    in this project's filed/ or a year folder beside it, that took
    its net capital loss in from `year`'s record as it was before
    `lock` replaced it (backlog TJ-0430)."""
    import os as _os
    from taxjson.lib import filed_locks
    cand: List[Tuple[int, Path, Path]] = [
        (y, p, root) for y, p in filed_locks.list_snapshots(root)
        if y > year]
    multi = _PL.multi_root(root)
    if multi is not None:
        for y, d in _PL.year_dirs(multi):
            p = d / "filed" / f"{y}.json"
            if y > year and d.resolve() != root.resolve() and p.is_file():
                cand.append((y, p, d))
    out = []
    for y, p, d in sorted(cand, key=lambda t: t[0]):
        try:
            doc = filed_locks.load_lock_json(p)
        except (OSError, ValueError):
            continue
        got = _carry_in_moved(doc, lock, year)
        if got is not None:
            out.append((y, _os.path.relpath(p, root), d, got))
    return out


def cmd_check_filed(args: argparse.Namespace) -> None:
    """`tjs check-filed`: recompute every filed year from the
    current books and diff against the locks. Exit 1 on drift or a
    damaged lock, 2 when only the recompute failed on an input (nothing
    was judged)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_config(root)["settings"]
    from taxjson.lib import filed_locks
    from taxjson.lib import out as _out
    # (A frozen year whose lock was deleted is checked as a damaged
    # one: said, exit 1; backlog TJ-0263.)
    _snaps = filed_locks.list_snapshots(root)
    page = Doc(exact=True, width_=0, live=True)
    if not _snaps and _lock_missing_year(root, settings) is None:
        # A year folder beside filed ones: its own lock is what is
        # missing, not every lock (backlog TJ-0092).
        _multi = _PL.multi_root(root)
        _others = (_filed_year_folders(_multi, skip=root)
                   if _multi is not None else [])
        if _others:
            import os as _os

            def _at(p: Path) -> str:
                # From here (`2024`, `../2024`), else the full path.
                rel = _os.path.relpath(p)
                return (str(p) if rel.startswith(_os.pardir + _os.sep
                                                 + _os.pardir) else rel)
            page.line(_out.fill(
                f"No filed/<year>.json lock in this project: "
                f"{settings.year} is not closed (`tjs "
                f"close-year` locks it after you file). The filed years "
                f"are checked in their own folders: "
                + ", ".join(f"`tjs -C {_at(p)} check-filed`"
                            for p in _others) + "."))
            return
        page.line(_out.fill("No filed/<year>.json snapshots — `tjs "
                            "close-year` creates one after you file."))
        return
    # "OK" is only as good as the books it recomputes (S048-23): a full
    # run's of the inputs as they are, or nothing is judged (the newest
    # lock closed since is no change: it is compared with its own books).
    _cfg = load_config(root)
    _refuse_books_older_than_inputs(root, _cfg, read_upto=max(
        (y for y, _p in _snaps), default=settings.year))
    page.raw("FILED-YEAR LOCK CHECK", flush=True)
    _state = _warn_run_state(root, _cfg)
    _counts: Dict[str, int] = {}
    if _check_filed_years(root, cache, settings, strict=False,
                          counts=_counts):
        # 1: a year drifted or a lock is damaged / of another country;
        # 2: only the recompute failed on an input (A2-0676) — nothing
        # was judged, so it is not reported as drift.
        if _counts.get("drifted") or _counts.get("lock"):
            raise SystemExit(1)
        raise SystemExit(2)
    if _state:
        page.line(_out.fill("The books above are not clean — see the warning; "
                            "an OK here is not conclusive until `tjs run` "
                            "is clean."))


def _filed_year_folders(folder: Path, skip: Optional[Path] = None
                        ) -> List[Path]:
    """The year folders of the multi-year `folder` holding a filed-year
    lock (filed/<year>.json), newest first, `skip` left out: where
    `tjs check-filed` has something to check (backlog TJ-0092)."""
    out = []
    for _y, p in reversed(_PL.year_dirs(folder)):
        if skip is not None and p.resolve() == Path(skip).resolve():
            continue
        try:
            if any(q.stem.isdigit() and q.is_file()
                   for q in (p / "filed").glob("*.json")):
                out.append(p)
        except OSError:
            continue
    return out


def _register_option_boundary(sub: argparse._SubParsersAction) -> None:
    p_ob = sub.add_parser(
        "option-boundary",
        help="Written options across a year end",
        description="Written options that straddle a tax-year boundary, "
                    "or are open at year end: where the premium and any "
                    "later amount land under ITA s.49 for the premium "
                    "timing in force, and whether a filed year needs a "
                    "T1-ADJ.")
    p_ob.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_ob.set_defaults(func=cmd_option_boundary)


def _register_t1135(sub: argparse._SubParsersAction) -> None:
    p_t1135 = sub.add_parser(
        "t1135",
        help="Foreign property (T1135): test and tables",
        description="CRA T1135 foreign-property helper: cost-based "
             "filing-threshold test plus per-property / per-country "
             "tables over all taxable accounts (per-symbol domicile "
             "overrides: ticker.map `T1135 SYMBOL COUNTRY` lines).")
    p_t1135.add_argument("--json", action="store_true",
                         help="Emit the report as JSON instead of text")
    p_t1135.set_defaults(func=cmd_t1135)


def _register_carryover(sub: argparse._SubParsersAction) -> None:
    p_carry = sub.add_parser(
        "carryover",
        help="Capital-loss carryforward ledger across years",
        description="Multi-year capital-loss carryforward/carryback "
             "ledger (Canada: running balance + T1A carryback "
             "candidates; US: ST/LT carryover worksheet). A close-year "
             "lock's recorded balance becomes the running balance at "
             "its year end. Record the losses your returns applied in "
             "taxjson.toml: [carryover] claimed = { 2023 = 4000.00 }.")
    p_carry.add_argument("--json", action="store_true",
                         help="Emit the ledger as JSON instead of text")
    # close-year's: the gain a return filed with another tool shows, for the
    # project year's row (backlog TJ-1452). The estimate's: its books', though
    # the year's own lock records a filed return (backlog TJ-1493).
    p_carry.add_argument("--filed-net", type=float, default=None,
                         help=argparse.SUPPRESS)
    p_carry.add_argument("--on-books", action="store_true",
                         help=argparse.SUPPRESS)
    p_carry.set_defaults(func=cmd_carryover)


def _register_form_export(sub: argparse._SubParsersAction) -> None:
    p_forms = sub.add_parser(
        "form-export",
        help="Schedule 3 / Form 8949 rows, or a TurboTax TXF",
        description="Render the year's gains as CRA Schedule 3 rows or "
             "IRS Form 8949 (code-W wash adjustments + Schedule D "
             "totals) — the default form follows the project country — "
             "or, for a US project, a TurboTax-importable TXF file "
             "(--form txf).")
    p_forms.add_argument("--form",
                         choices=["8949", "schedule3", "txf"],
                         default=None,
                         help="Which form (default: 8949 for usa, "
                              "schedule3 for canada; txf = TurboTax-"
                              "importable file built from the 8949 "
                              "rows, US projects only)")
    p_forms.add_argument("--box", default=None, choices=["A", "B", "C"],
                         help="txf only: 8949 checkbox pairing for "
                              "securities (A/D basis-reported, the "
                              "default; B/E, C/F); from 2025 a crypto "
                              "account's rows belong on boxes G-L, which "
                              "the TXF leaves out with a warning")
    p_forms.add_argument("--out", metavar="FILE", default=None,
                         help="txf only: write the .txf here instead "
                              "of stdout")
    p_forms.add_argument("--csv", metavar="FILE",
                         help="Also write the rows as CSV")
    p_forms.add_argument("--json", action="store_true",
                         help="Emit the report as JSON instead of text")
    p_forms.set_defaults(func=cmd_form_export)


def _register_close_year(sub: argparse._SubParsersAction) -> None:
    p_close = sub.add_parser(
        "close-year",
        help="Lock the year you filed (filed/<year>.json)",
        description="Snapshot the current tax year's filing aggregates "
             "to filed/<year>.json — the filed-year lock that "
             "check-filed (and every full run) guards. It also records "
             "what next year needs: every sale, the positions and cost "
             "at Dec 31, the trades that settle in January, and the "
             "carry-forwards (net capital loss, minimum tax carryover; "
             "US: the capital loss carryover) that next year's "
             "estimate, carryover and handoff read.")
    p_close.add_argument("--year", type=_tax_year_arg, default=None,
                         help="Must match [settings].year (guard)")
    p_close.add_argument("--force", action="store_true",
                         help="Replace an existing lock (re-filed/"
                              "amended years), or lock past a stop that "
                              "names this flag (a year not ended or empty, "
                              "open problems, a carry-back that leaves out "
                              "sales with an unknown cost)")
    p_close.add_argument("--yes", action="store_true",
                         help="Lock even when checklist steps before the "
                              "lock need attention (they are listed and, "
                              "on a terminal, asked about)")
    p_close.add_argument("--filed-dispositions", metavar="CSV",
                         help="The dispositions the return actually "
                              "reported, when it was prepared with another "
                              "tool (CSV: symbol,date,qty,proceeds,cost,gain"
                              "[,account]); the next year's `tjs "
                              "handoff` checks doubles against these")
    p_close.set_defaults(func=cmd_close_year)


def _register_handoff(sub: argparse._SubParsersAction) -> None:
    p_hand = sub.add_parser(
        "handoff",
        help="Check this year starts where last year ended",
        description="Check this project against the previous year's "
             "close-year record: opening positions and cost, trades "
             "settling across Dec 31, sales reported in both years, and "
             "carry-forward inputs that differ from the recorded ones "
             "(exit 1 on a problem).")
    p_hand.add_argument("--prior", metavar="PATH",
                        help="The previous year's filed/<year>.json "
                             "(default: [settings] prior_year_record, else "
                             "filed/<year-1>.json here)")
    p_hand.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_hand.set_defaults(func=cmd_handoff)


def _register_check_filed(sub: argparse._SubParsersAction) -> None:
    p_chk = sub.add_parser(
        "check-filed",
        help="Recompute filed years and report any drift",
        description="Recompute every filed year from the current books "
             "and report drift vs the locks (exit 1 on drift or a "
             "damaged lock, 2 when only an input failed).")
    p_chk.set_defaults(func=cmd_check_filed)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_option_boundary(sub)
    _register_t1135(sub)
    _register_carryover(sub)
    _register_form_export(sub)
    _register_close_year(sub)
    _register_handoff(sub)
    _register_check_filed(sub)
