"""The Explain and check commands: audit, wash-sales, tax-logic, edge-cases, check-dates, check-tt, journals, renames, spinoffs, splits (sanity: pipeline/sanity.py)."""
import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from taxjson.project.accounts import account_of, accounts_of
from taxjson.project.settings import Settings, settings_of
from taxjson.lib import project_layout as _PL
from taxjson.lib.project_layout import _audit_source_files
from taxjson.lib.out import exit_text
from taxjson.lib import out as _out
from taxjson.lib.pipeline import income_dating_flags, option_timing_flags
from taxjson.model.result import GainRow, Holding
from taxjson.model import money as M
from taxjson.lib.report_fmt import fmt_money
from taxjson.lib.stage_msg import emit_line

from taxjson.cli.common import (
    _accounts_skipped_for_no_inputs, _act_item, _base_currency,
    _check_books_before_merge, _child_error, _discover_tx_accounts,
    _load_json_or_die, _no_wash_checkable, _os_path_lexists,
    _real_world_legs, _refuse_us_crypto_account, _require_books,
    _sheltered_expected, _taxable_equity_account_names,
    _warn_accounts_without_books, _warn_artifact_year,
)
from taxjson.pipeline.config import (
    load_settings,
    _country, _no_project_message, _use_project_ticker_map, load_config,
)
from taxjson.pipeline.inputs import (
    _home_currency, _loss_override_flags, _normalize_country, _tax_date,
)
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.sanity import _json_out, _project_file, cmd_sanity
from taxjson.pipeline.stages import (
    _locked_year_flags, _missing_history_arg,
    _read_work_doc, OWN_MOVES_SUFFIX,
)
from taxjson.pipeline.tools import _cmd
from taxjson.lib.numeric import nonneg_float_arg as _nonneg_float_arg
from taxjson.lib.cli_diag import tax_year as _tax_year_arg

# The commands' print() calls (PLAN step 15): the ops of a live exact Doc,
# `out.Doc(exact=True, width_=0, live=True)`, each printed when it is added
# (`page.line(e)` is `print(e)`, `page.blank()` is `print()`).


def cmd_tax_logic(args: argparse.Namespace) -> None:
    """`tjs tax-logic`: a short statement of every rule taxjson
    applies for the project's country, with the project's settings
    filled in (lib/tax_logic). Works outside a project too (defaults)."""
    from taxjson.lib.country import CountryError
    from taxjson.lib.tax_logic import render, rule_sections, sections
    root = Path(args.dir).resolve()
    settings = load_settings(root, missing_ok=True)
    if args.country:
        country = _normalize_country(args.country)
    elif not settings.empty:
        country = _country(settings)
    elif _PL.has_config(root):
        _die("taxjson.toml here has no [settings] country to read — pass "
             "--country canada|usa (or -C DIR to a project)")
    else:
        _die("no taxjson.toml here to read the country from — pass "
             "--country canada|usa (or -C DIR to a project)")
    page = _out.Doc(exact=True, width_=0, live=True)
    try:
        if getattr(args, "json", False):
            _json_out({"country": country, "year": settings.year,
                       "sections": [{"title": t, "rules": r}
                                    for t, r in sections(country, settings)],
                       "rule_ids": [
                           {"title": t, "rules": [
                               {"id": r.id, "text": r.text,
                                "continues": r.cont} for r in rs]}
                           for t, rs in rule_sections(country, settings)]},
                      schema="tax-logic")
            return
        page.line(render(country, settings, ids=getattr(args, "ids", False)))
    except (CountryError, ValueError) as e:
        # The same resolvers the engine uses refused a setting: the
        # text would otherwise describe a value `run` reads differently
        # (partition SPEC-12).
        _die(str(e))


def cmd_spinoffs(args: argparse.Namespace) -> None:
    """`tjs spinoffs`: every spin-off, its election, the value per
    share used and what was booked (lib/corp_views). Exit 1 when one
    needs attention (zero value, no election, ignored)."""
    from taxjson.bin.taxjson_corp_actions import extractor_for
    from taxjson.lib.corp_views import ViewError, render_spinoffs, spinoffs
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    _require_books(root, args.account)
    try:
        doc = spinoffs(root, cfg, args.account, extractor_for=extractor_for)
    except ViewError as e:
        _die(str(e))
    page = _out.Doc(exact=True, width_=0, live=True)
    if getattr(args, "json", False):
        _json_out(doc, schema="spinoffs")
    else:
        for ln in render_spinoffs(doc):
            page.line(ln)
    if any(s["flags"] and not s["sheltered"] for s in doc["spinoffs"]):
        raise SystemExit(1)


def cmd_splits(args: argparse.Namespace) -> None:
    """`tjs splits`: every split, consolidation and rename in the
    books with holdings before and after, noting a split two sources
    report within a week (one event, applied once) and flagging the same
    ratio again further apart (applied twice), a no-op row and a
    fractional result (lib/corp_views). Exit 1 on a likely double
    application."""
    from taxjson.lib.corp_views import ViewError, render_splits, splits
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    _require_books(root, args.account)
    try:
        items = splits(root, cfg, args.account)
    except ViewError as e:
        _die(str(e))
    page = _out.Doc(exact=True, width_=0, live=True)
    if getattr(args, "json", False):
        _json_out({"splits": items}, schema="splits")
    else:
        for ln in render_splits(items):
            page.line(ln)
    if any("TWICE?" in s["flags"] for s in items):
        raise SystemExit(1)


def cmd_journals(args: argparse.Namespace) -> None:
    """`tjs journals`: every broker journal between two listings of
    one security, per account — its date, FROM -> TO, quantity, broker,
    how it was found and its state: joined (the map line that pools the
    two), suggested (the reason, and the `.tt` / ticker.map line that
    settles it) or refused (the reason, and the undo) — read from the
    last run's work/ (lib/journals). `--pending` lists only the pending
    journals (suggested, or refused with no ticker.map line behind it)
    and exits 1 when there is one."""
    from taxjson.lib.journals import JournalsError, render, report
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    want = (args.account or "").strip() or None
    if want and want not in (cfg.get("accounts") or {}):
        _die_input(f"no [accounts.{want}] in taxjson.toml — check the "
                   f"name.")
    try:
        doc = report(root, cfg, want, args.year)
    except JournalsError as e:
        _die(f"{e} — run `tjs run` first",
             "The journals are read from the last run's work/ folder.")
    if args.pending:
        doc = dict(doc, journals=[j for j in doc["journals"]
                                  if j["pending"]])
    page = _out.Doc(exact=True, width_=0, live=True)
    if getattr(args, "json", False):
        _json_out(doc, schema="journals")
    else:
        for ln in render(doc, pending=args.pending):
            page.line(ln)
    if args.pending and doc["pending"]:
        raise SystemExit(1)


def cmd_renames(args: argparse.Namespace) -> None:
    """`tjs renames`: every ticker change in the books as a dated
    event (date, source, position and book cost carried), every trade in
    an old ticker after its rename with the .tt line that resolves it,
    the look-alike renames the exports show (suggested, with the `.tt`
    line `RENAME <date> OLD NEW` that books each) and the undated
    ticker.map renames (lib/renames), and each declared rename that
    books nothing. Exit 1 while a late trade is unresolved; with
    --pending, also while a suggestion is open or a declaration books
    nothing."""
    from taxjson.lib.renames import pending_renames, render, report, run_notes_missing
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    # No books is no check, as `splits` and `spinoffs` say: it read "no
    # renames, nothing pending" at exit 0 (backlog TJ-0319).
    _require_books(root, args.account)
    if not args.account:
        _warn_accounts_without_books(
            root, [n for n in (cfg.get("accounts") or {})
                   if (root / "work" / f"{n}_base.json").is_file()],
            "renames", "books")
    try:
        doc = report(root, cfg, args.account)
    except ValueError as e:
        _die(str(e))
    pending = getattr(args, "pending", False)
    page = _out.Doc(exact=True, width_=0, live=True)
    if getattr(args, "json", False):
        if pending:
            # The events of the unresolved late trades: old symbol, new
            # symbol and date (backlog TJ-0687).
            doc = dict(doc, renames=pending_renames(doc),
                       late=[x for x in doc["late"]
                             if x["resolution"] == "unresolved"],
                       undated=[])
        _json_out(doc, schema="renames")
    else:
        _no_notes = run_notes_missing(root, doc)
        for ln in render(doc, pending=pending) + ([_no_notes] if _no_notes else []):
            page.line(ln)
    if doc["unresolved"] or (pending and doc["pending"]):
        raise SystemExit(1)


def cmd_check_dates(args: argparse.Namespace) -> None:
    """`tjs check-dates`: every trade and settlement date the parsers
    produced, checked against the trading calendar of what was traded
    (crypto 24/7, futures 23/5, US stocks with the overnight session,
    options and Canadian listings on exchange days) and the settlement
    rules (lib/check_dates). Exit 1 on an impossible date."""
    from taxjson.lib.check_dates import analyze, render
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    if not (root / "work").is_dir():
        _die("no work/ — run `tjs run` first")
    try:
        doc = analyze(root, cfg, account=args.account)
    except ValueError as e:
        # [settings] futures_settle refused as `run` refuses it (A2-0697).
        _die(str(e))
    if not doc["sources"]:
        _die("no parsed sources in work/ — run `tjs run` first")
    if not args.account:
        _warn_accounts_without_books(root, _discover_tx_accounts(root / "work"),
                                     "check-dates", "parsed source")
    page = _out.Doc(exact=True, width_=0, live=True)
    if getattr(args, "json", False):
        _json_out(doc, schema="check-dates")
    else:
        for ln in render(doc, show_all=args.all, details=_details(args)):
            page.line(ln)
    if doc["errors"]:
        raise SystemExit(1)


def _tt_path_item(item: str) -> bool:
    """A `check-tt` item that names a file (a path ending in .tt, or
    with a folder in it, or an existing file), not an account."""
    return (item.lower().endswith(".tt") or "/" in item
            or Path(item).is_file())


def _tt_file(root: Path, item: str) -> Optional[Path]:
    """The file a `check-tt` FILE item names, or None: from the shell's
    folder, then from the project's (`-C DIR` does not change the
    shell's folder: _project_file), then a name check-tt itself prints
    (inputs/<account>/x.tt) in the project's inputs folder — in the
    default years layout that is ../inputs/ of the year folder. Before,
    only the shell's folder was looked in (backlog TJ-0033, TJ-0041)."""
    try:
        p = Path(item).expanduser()
        if p.is_file():
            return p
        q = _project_file(root, item)
        if q is not None:
            return q
        parts = Path(item).parts
        if not p.is_absolute() and len(parts) > 1 and parts[0] == "inputs":
            q = _PL.inputs_dir(root).joinpath(*parts[1:])
            if q.is_file():
                return q
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def cmd_check_tt(args: argparse.Namespace) -> None:
    """`tjs check-tt [ACCOUNT | FILE.tt ...]`: every line of the .tt
    files read with the run's own parsers, every problem named with its
    file and line (lib/check_tt) — the run stops at the first. With no
    item, the project's accounts; an account name, its files; a path,
    that file (`--account` names the account it is read as). Exit 1 on
    a line the run would refuse; nothing is written."""
    from taxjson.lib import check_tt as CT
    root = Path(args.dir).resolve()
    items = list(args.items or [])
    paths = [i for i in items if _tt_path_item(i)]
    names = [i for i in items if i not in paths]
    files = []
    for i in paths:
        f = _tt_file(root, i)
        if f is None:
            _die_input(f"{i}: no such file",
                       "A relative FILE is looked for from the shell's "
                       "folder, then from the project's (`-C DIR`), and "
                       "a name check-tt prints (inputs/<account>/x.tt) "
                       "in the project's inputs folder.")
        files.append(f)
    if names or not paths:
        if not _os_path_lexists(root / _PL.CONFIG):
            _die(*_no_project_message(root, "check-tt"))
        cfg = load_config(root)
        accounts = accounts_of(cfg)
        unknown = [n for n in names if n not in accounts]
        if unknown:
            _die_input(f"no account {unknown[0]!r} in taxjson.toml",
                       "The accounts: " + ", ".join(sorted(accounts))
                       + ". A file is named by its path (ending in .tt).")
        doc = CT.check_project(root, accounts, only=names or None)
    else:
        doc = {"files": [], "problems": [], "lines": 0, "rows": 0,
               "events": 0, "errors": 0, "warnings": 0}
    if paths:
        crypto = None
        if getattr(args, "crypto", False):
            crypto = True
        elif getattr(args, "equity", False):
            crypto = False
        more = CT.check_paths(files, account=getattr(args, "account", None),
                              crypto=crypto, shown=paths,
                              root=root if _PL.has_config(root) else None)
        for k in ("files", "problems"):
            doc[k] = doc[k] + more[k]
        for k in ("lines", "rows", "events", "errors", "warnings"):
            doc[k] += more[k]
    page = _out.Doc(exact=True, width_=0, live=True)
    if getattr(args, "json", False):
        _json_out(doc, schema="check-tt")
    else:
        for ln in CT.render(doc):
            page.line(ln)
    if doc["errors"]:
        raise SystemExit(1)


def cmd_edge_cases(args: argparse.Namespace) -> None:
    """`tjs edge-cases`: transactions whose treatment turns on a
    boundary — trades that settle in a different year than they trade,
    dispositions in the last/first days of a year, written options and
    expiries across Dec 31, income around New Year, crypto near midnight,
    superficial-loss windows that span the year end, and every taxable
    loss with an acquisition or sale within --margin days of the 30-day
    window's edge — each with where it lands and why (lib/edge_cases)."""
    from taxjson.lib.edge_cases import analyze, render_text
    if args.margin < 0:
        # A negative margin emptied the window-edge section at rc 0
        # (audit A2-1198); winners --top refuses < 1 the same way. A
        # usage error: exit 2, said before any file is read (backlog
        # TJ-0157, TJ-0241).
        _die_input(f"--margin must be >= 0, got {args.margin}")
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    if not (root / "work").is_dir():
        _die("no work/ directory — run `tjs run` first")
    _require_books(root, args.account)
    try:
        doc = analyze(root, cfg, margin=args.margin, account=args.account)
    except ValueError as e:
        # An unreadable work file, a damaged row (lib/json_input's row
        # funnel, A2-0330) or an invalid futures_settle: named, never a
        # silent "None." or a traceback (audit A2-1199, A2-0697).
        _die(str(e))
    if getattr(args, "json", False):
        _json_out(doc, schema="edge-cases")
        return
    page = _out.Doc(exact=True, width_=0, live=True)
    for line in render_text(doc, details=_details(args)):
        page.line(line)


def _wash_sales_rows(root: Path, files: List[Tuple[str, Path]]
                     ) -> Tuple[List[str], List[Any], Any, Dict[str, str], bool]:
    """`tjs wash-sales`: the table's header, its rows (each denied
    disposition of the gains files, by date, account and symbol), the
    tax year, the warn-only flags (option replacements and the engine's
    manual checks) and whether the project is a US one (cmd_wash_sales)."""
    header = ["ACCOUNT", "DATE", "SYMBOL", "QTY", "PROCEEDS", "COST",
              "GAIN", "DENIED", "ALLOWED"]
    rows = []
    year = None
    # Warn-only manual-check flags (CA-SL-14/15, US-WASH-14/15): they
    # deny nothing, so the table never showed them and the command said
    # "no losses were denied" over them (A2-0413).
    flags: Dict[str, str] = {}
    _usa = _country(load_settings(root, missing_ok=True)) == "usa"
    for acct, f in files:
        data = _load_json_or_die(f)
        year = year or (data.get("summary") or {}).get("year")
        for t in data.get("transactions", []):
            if t.get("is_wash_sale"):
                rows.append((t.get("date") or "", acct, t))
        for w in data.get("option_replacement_warnings") or []:
            if not isinstance(w, dict):
                continue
            try:
                from taxjson.engine.replacement import format_option_replacement_warning as _fmt_orw
                _txt = _fmt_orw(w, country="usa" if _usa else "canada")
            except (KeyError, TypeError, ValueError):
                _txt = (f"{w.get('loss_symbol')} loss on "
                        f"{w.get('loss_date')}: {w.get('option_symbol')} "
                        f"[{w.get('rule')}]")
            flags.setdefault(_txt, acct)
        # The engine's own manual checks: a loss with an unknown-cost
        # disposition in its window, or an unknown-cost loss with a
        # purchase in it. The books deny nothing for them, so the
        # command said "no losses were denied" and listed no flag
        # (backlog TJ-0551).
        from taxjson.lib.missing_history import loss_warning_text
        for w in data.get("superficial_loss_warnings") or []:
            if isinstance(w, dict):
                flags.setdefault(
                    f"{acct}: "
                    + loss_warning_text(w, "usa" if _usa else "canada"),
                    acct)
    rows.sort(key=lambda r: (r[0], r[1], str(r[2].get("symbol") or "")))
    return header, rows, year, flags, _usa


def _wash_sales_embedded(files: List[Tuple[str, Path]]) -> float:
    """`tjs wash-sales`: the deferred amounts still in the open positions'
    cost, over the same gains files; an unreadable one skipped
    (cmd_wash_sales)."""
    # Deferred amounts still embedded in OPEN positions (across the
    # same canonical files): ties the historical denials to the present.
    embedded = 0.0
    for _acct, f in files:
        try:
            _d2 = _read_work_doc(f)
        except (OSError, ValueError):
            continue
        for h in _d2.get("inventory") or []:
            embedded += float(Holding(h).deferred_wash or 0)
    return embedded


def _wash_sales_table(rows: List[Any], views: List[GainRow], denied_by_row: List[float],
                      qfmt: Callable[..., str], money: Callable[..., str],
                      _cents: Callable[..., float], perm_by_row: List[float], root: Path,
                      Doc: Any, _usa: bool, year: Any, gains_basis_label: Callable[..., str],
                      resolved: Dict[str, Path], _det: bool, header: List[str],
                      embedded: float, _flags_section: Callable[[Any], None],
                      _out_details_hint: Callable[..., str], _ws_cmd: str) -> None:
    """`tjs wash-sales` as text, with denied losses: the table (each row's
    proceeds and cost as the broker shows them, the loss, its denied and
    allowed parts), the totals and the deferred amount; then the flags and
    a pointer to --details, or with --details what DENIED means and the
    unrounded total when the rows' cents differ from it (cmd_wash_sales)."""
    body = []
    for (date, acct, t), v, denied in zip(rows, views, denied_by_row):
        econ = float(v.raw_gain or 0)               # true economic gain/loss
        allowed = econ + denied                     # loss you can claim now
        _p, _c = _real_world_legs(v)                # S048-10
        body.append([
            acct, date or "-", str(t.get("symbol") or "?"),
            qfmt(float(v.quantity or 0)), money(_p),
            money(_c), money(econ), money(denied),
            money(allowed)])
    _engine_denied = sum(denied_by_row)          # the JSON's denied_unrounded
    total_denied = _cents(denied_by_row)
    total_perm = _cents(perm_by_row)

    base = _base_currency(root)
    doc = Doc(f"{'WASH SALES' if _usa else 'SUPERFICIAL LOSSES'} — {base}, "
              f"tax year {year}, basis: {gains_basis_label(resolved)}")
    if _det:
        doc.para("Losses denied under "
                 + ("the wash-sale rule, §1091." if _usa
                    else "the superficial-loss rule, s.54."))
        doc.blank()
    else:
        # The legend, before the table (docs/output-style.md).
        doc.para("DENIED: the loss "
                 + ("the wash-sale rule (§1091) disallows" if _usa
                    else "the superficial-loss rule (s.54) denies")
                 + "; ALLOWED: the loss you claim.")
    # Too wide (a long option symbol): COST, then PROCEEDS, go first —
    # GAIN is their difference.
    doc.table(header, body, drop=(5, 4), key=(2, 1, 0))
    perm_note = (f" ({money(total_perm)} permanently denied)"
                 if total_perm > 0.005 else "")
    doc.blank()
    _figs = [("DENIED", f"{money(total_denied)} {base} over {len(rows)} "
                        f"{'wash sale(s)' if _usa else 'superficial loss(es)'}"
                        f"{perm_note}")]
    if embedded > 0.005:
        _figs.append(("DEFERRED", f"{money(embedded)} {base} in open "
                                  f"positions' cost — tjs list"))
    doc.kv(_figs)
    if not _det:
        _flags_section(doc)
        doc.para(_out_details_hint(
            _ws_cmd + " (what DENIED means); --explain: each denial's "
                      "trace", ""))
        doc.print()
        return
    doc.section("WHAT DENIED MEANS")
    if _usa:
        doc.item("DENIED is added to the cost basis of the repurchased "
                 "shares: you recover it on a later sale.")
        doc.item("A permanently-denied amount, from a repurchase in an "
                 "IRA, is lost for good.")
    else:
        doc.item("DENIED is added to the ACB of the substituted property "
                 "(s.53(1)(f)): you recover it on a later sale.")
        # An affiliated person's purchase: their own ACB, not "lost
        # for good" (S033-03 wording; re-audit A2-1233).
        doc.item("A permanently-denied amount is lost for good when the "
                 "repurchase is in a registered account; one bought by an "
                 "affiliated person is added to that person's own ACB "
                 "(s.53(1)(f)), not yours.")
    _gap = M.shown(total_denied - _engine_denied, 2)
    if abs(_gap) >= 0.005:
        doc.item(f"Rows are rounded to the cent, as filed: the gains "
                 f"files' unrounded total denied is "
                 f"{money(_engine_denied)} ({_gap:+,.2f} on the DENIED "
                 f"total).")
    _flags_section(doc)
    doc.print()


def cmd_wash_sales(args: argparse.Namespace) -> None:
    """Detail each superficial-loss (wash sale) that OCCURRED in the tax year —
    the losses the engine denied, which `<account>.sum` folds silently into the
    ticker totals. Reads the canonical per-account gains, preferring the
    cross-account `<account>_gains_wash.json` (which also catches registered-
    account repurchases) when the pipeline built it, else `<account>_gains.json`.
    One row per denied disposition + a denied total. With `--explain`, print the
    full ACB / superficial-loss calculation trace for each wash sale instead of
    the table."""
    root = Path(args.dir).resolve()
    cache = root / "work"

    if args.explain:
        if getattr(args, "json", False):
            # The trace is prose from taxjson-explain — silently
            # printing text under --json broke machine consumers.
            sys.exit(exit_text("tjs wash-sales: --explain has no JSON form; "
                               "drop --json (or drop --explain for the "
                               "machine-readable table)."))
        _explain_wash_sales(root, cache, args.account)
        return                                          # (raises SystemExit)

    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    resolved = resolve_gains_files(cache, args.account or None)
    if not resolved:
        # work/ named from the project, and exit 2 for the input
        # missing, as sanity says it (backlog TJ-0642, TJ-0631).
        if args.account:
            _die_input(f"no gains for account {args.account!r} in work/ "
                       f"— run `tjs run` first, or check the name.")
        _die_input("no gains files in work/ — run `tjs run` first.")
    if not args.account:
        _warn_accounts_without_books(root, resolved, "wash-sales",
                                     "gains file")
    # The filing views' banners: per-account books after `run --account`
    # (no blended pass) and books built for another tax year printed
    # 'No wash sales' at rc 0 (re-audit A2-0400, A2-0405).
    # (The run-state banner: the dispatch loop, _RUN_STATE_BANNER_CMDS.)
    _cfg_ws = load_config(root, missing_ok=True)
    if _cfg_ws:
        _warn_artifact_year(resolved,
                            settings_of(_cfg_ws).year)
    files = list(resolved.items())

    money = fmt_money               # shared report-layer formatter
    from taxjson.lib.report_fmt import fmt_qty as qfmt

    header, rows, year, flags, _usa = _wash_sales_rows(root, files)

    from taxjson.lib.out import Doc
    from taxjson.lib.out import details_hint as _out_details_hint

    # The user's filing positions against the rule (.tt ALLOWLOSS): the
    # loss is allowed, so the table never shows it (CA-SL-18 /
    # US-WASH-25).
    from taxjson.lib import loss_overrides as _LO
    _lo_items = _LO.gather(dict(files))
    _lo_basis = ("trade" if (load_settings(root, missing_ok=True).tax_date
                             or ("trade" if _usa else "settle")) == "trade"
                 else "settle")
    _positions = [p for p in _LO.positions(_lo_items, year, _lo_basis)
                  if p["in_year"]]

    _det = _details(args)
    _ws_cmd = "tjs wash-sales --details"

    def _flags_section(doc):
        if not _det:
            # Essentials first (docs/output-style.md): one act-on line
            # each; --details lists them.
            if _positions or flags:
                doc.blank()
            if _positions:
                doc.item(*_act_item(
                    f"FILING POSITIONS: {len(_positions)} loss(es) "
                    f"claimed against the rule (.tt ALLOWLOSS)", _ws_cmd))
            if flags:
                doc.item(*_act_item(
                    f"MANUAL CHECK: {len(flags)} warn-only flag(s) to "
                    f"decide by hand", _ws_cmd))
            return
        if _positions:
            doc.section(f"FILING POSITIONS — {len(_positions)} loss(es) "
                        f"claimed against the rule (.tt ALLOWLOSS)")
            doc.para("Allowed in the books although the rule would "
                     + ("disallow" if _usa else "deny")
                     + " them: your position, not the rule's test.")
            doc.items([ln for it in _lo_items
                       if it.get("status") == _LO.STATUS_APPLIED
                       and _LO.in_year(it, year, _lo_basis)
                       for ln in _LO.describe(it)])
        if not flags:
            return
        doc.section(f"MANUAL CHECK — {len(flags)} warn-only flag(s)")
        doc.para("The books deny nothing for them: decide each by hand.")
        doc.items(sorted(flags))

    embedded = _wash_sales_embedded(files)

    # The totals are the rows' cents added, as the table prints them and
    # the filed rows (sum's RETURN row) carry them; the engine's
    # unrounded total, a cent away over two rows, is a --details note
    # (backlog TJ-0428, as TJ-0209 did for sum).
    views = [GainRow(t) for _d, _a, t in rows]
    denied_by_row = [float(v.disallowed_amount or 0) for v in views]
    perm_by_row = [float(v.permanently_disallowed or 0) for v in views]

    def _cents(values) -> float:
        return M.shown(sum(M.shown(x, 2) for x in values), 2)
    if getattr(args, "json", False):
        _json_out({"rows": [dict(t, account=acct)
                            for _d, acct, t in rows],
                   "totals": {"denied": _cents(denied_by_row),
                              # The engine's total rounded once: what
                              # close-year's lock records (A2-0867).
                              "denied_unrounded": M.shown(sum(denied_by_row), 2),
                              "permanently_denied": _cents(perm_by_row),
                              "embedded_in_open": M.shown(embedded, 2)},
                   "year": year, "currency": _base_currency(root),
                   "basis": gains_basis_label(resolved),
                   "manual_check_flags": sorted(flags),
                   **({"filing_positions": _positions}
                      if _positions else {})}, schema="wash-sales")
        return

    # The rule's own name: never "wash sale" in a Canadian project
    # (audit A2-0748, A2-1249, A2-1326, A2-1352, A2-1371, A2-1372).
    if not rows:
        scope = f" for account {args.account!r}" if args.account else ""
        # Each country's own term (A2-1366): a Canadian project has
        # superficial losses, not wash sales.
        doc = Doc(f"No {'wash sales' if _usa else 'superficial losses'}"
                  f"{scope} — no losses were denied.")
        _flags_section(doc)
        doc.print()
        return

    _wash_sales_table(rows, views, denied_by_row, qfmt, money, _cents, perm_by_row, root, Doc,
                      _usa, year, gains_basis_label, resolved, _det, header, embedded,
                      _flags_section, _out_details_hint, _ws_cmd)


def _explain_wash_sales(root: Path, cache: Path,
                        account: Optional[str]) -> None:
    """`tjs wash-sales --explain`: print the full ACB / superficial-loss
    calculation trace for each wash sale, via `taxjson-explain --wash-sales`
    (plain text — `taxjson-explain` is color-off by default). Resolves the
    account base file(s) and the country / tax-date / sheltered context from the
    project. Raises SystemExit with the tool's return code."""
    if account:
        base = cache / f"{account}_base.json"
        if not base.exists():
            # (work/ named from the project: backlog TJ-0642)
            _die_input(f"no {base.name} in work/ — run `tjs run` "
                       f"first, or check the name.")
        if account_of(accounts_of(load_config(root, missing_ok=True)),
                      account).type == "sheltered":
            # A registered account's own sale is no superficial loss
            # (CA-ACB-05; an IRA's, no wash sale): the table says none,
            # and the trace replayed the registered book as taxable,
            # each row twice (backlog TJ-0600).
            _usa = _country(load_settings(root, missing_ok=True)) in ("us", "usa")
            from taxjson.lib.out import Doc
            Doc(f"No {'wash sales' if _usa else 'superficial losses'} "
                f"for account {account!r} — it is a sheltered account."
                ).para("Its own sales are kept out of the filing totals, "
                       "so none is denied. Its purchases can deny a "
                       "taxable account's loss: `tjs wash-sales "
                       "--explain` traces those.").print()
            raise SystemExit(0)
        _refuse_us_crypto_account(root, account, "tjs wash-sales")
        bases = [base]
    else:
        names = _taxable_equity_account_names(root)
        if names is not None and not names:
            _no_wash_checkable("tjs wash-sales")
        if names is not None:
            bases = [cache / f"{n}_base.json" for n in names
                     if (cache / f"{n}_base.json").exists()]
        else:
            bases = [p for p in sorted(cache.glob("*_base.json"))
                     if not p.name.endswith("_raw_base.json")
                     and p.name != "sheltered_base.json"
                     and not p.name.startswith(".")]
        if not bases:
            # Exit 2, an input missing, work/ named from the project
            # (backlog TJ-0642).
            _die_input("no base files in work/ — run `tjs run` "
                       "first.")

    settings = load_settings(root, missing_ok=True)
    common: List[str] = ["--wash-sales", "--layout", "report"]
    common += ["--country", _country(settings)]
    if _country(settings) in ("us", "usa"):
        # The blended table keeps FIFO per account (US-BASIS-01): said
        # explicitly, as `tjs audit` does, so the trace of a merged
        # book never pools lots across accounts (re-audit A2-0155).
        common.append("--per-account-basis")
    if settings.tax_date:
        common += ["--tax-date", settings.tax_date]
    # The table lists the tax year's wash sales; the trace printed every
    # year's under it (R1-284).
    if isinstance(settings.year, int):
        common += ["--year", str(settings.year)]
    # Sheltered history makes the ±30-day affiliated-balance window accurate
    # (a registered-account repurchase can trigger/permanently-deny a loss).
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        common += ["--sheltered", str(sheltered_base)]
    elif _sheltered_expected(root):
        # Without it the replay lost a registered account's denial the
        # table still lists (audit TJ-0394): refuse, as the wash checks
        # do (backlog TJ-0350).
        _die_input("no sheltered_base.json in work/: the registered "
                   "accounts' trades are missing from the replay",
                   "A registered account's buy can deny a loss for good, "
                   "so a trace without them can contradict the table. "
                   "Run `tjs run` first to rebuild it.")
    # Same missing-history openings as the pipeline, so traces match the
    # books.
    mh_file = _missing_history_arg(root)
    if mh_file is not None:
        common += ["--incomplete-history", str(mh_file)]
    common += option_timing_flags(settings)
    common += income_dating_flags(settings)
    common += _loss_override_flags(cache)
    if account:
        # The table lists this account's denials only; the merged book
        # below keeps the other accounts as context, so the trace shows
        # only this account's dispositions too (backlog TJ-0413).
        common += ["--account", account]

    # Trace the computation the table comes from: the pipeline BLENDS
    # the taxable equity books (s.47 ACB / cross-account §1091), and
    # Canadian crypto books when there are two or more. One explain
    # per account's own book gave each its own pool — "no matching
    # gains" for a real denial, or a denial the books do not have
    # (S046-09).
    _acfg = accounts_of(load_config(root, missing_ok=True))
    _crypto_blend = _country(settings) \
        not in ("us", "usa")
    _by_name = {p.name[:-len("_base.json")]: p for p in bases}
    if account and account in _acfg:
        _is_c = bool(_acfg[account].crypto)
        for n in _taxable_equity_account_names(root) or []:
            if (n != account and bool(account_of(_acfg, n).crypto)
                    == _is_c and (cache / f"{n}_base.json").exists()):
                _by_name[n] = cache / f"{n}_base.json"
    # Merged in taxjson.toml order, as the pipeline's blend: rows of
    # different accounts at one moment follow that order (CA-DATE-14 /
    # US-DATE-13); an alphabetical merge traced another book (A2-1592).
    _toml_pos = {n: i for i, n in enumerate(_acfg)}
    _ordered = sorted(_by_name.items(), key=lambda kv: (
        _toml_pos.get(kv[0], len(_toml_pos)), kv[0]))
    equity = [p for n, p in _ordered if not account_of(_acfg, n).crypto]
    crypto = [p for n, p in _ordered if account_of(_acfg, n).crypto]
    groups = ([equity] if equity else []) + (
        [crypto] if crypto and _crypto_blend
        else [[c] for c in crypto])
    import os as _os
    import tempfile as _tf
    from taxjson.pipeline.dispatch import run_cmd as _run_cmd
    _usa = _country(settings) in ("us", "usa")
    page = _out.Doc(exact=True, width_=0, live=True)
    page.line(f"{'WASH SALES' if _usa else 'SUPERFICIAL LOSSES'} — how each denial was "
              f"computed, {_base_currency(root)}"
              + (f", tax year {settings.year}" if isinstance(settings.year, int) else ""))
    page.blank()
    sys.stdout.flush()
    rc = 0
    # Each book's explain exits _NO_MATCH (silently) when it has no
    # denial; the note is said once, only when no book had one — a
    # crypto book with none printed "no matching gains found" under
    # the equity book's real denial.
    _NO_MATCH = 3
    matched = False
    for grp in groups:
        target, tmp = grp[0], None
        if len(grp) > 1:
            _check_books_before_merge(grp)
            fd, name = _tf.mkstemp(prefix="taxjson_explain_",
                                   suffix=".json")
            _os.close(fd)
            tmp = Path(name)
            res = _run_cmd(_cmd("taxjson-merge") + [str(b) for b in grp],
                           capture_output=True)
            if res.returncode != 0:
                tmp.unlink(missing_ok=True)
                sys.exit(exit_text(f"tjs wash-sales: could not merge the "
                                   f"taxable base books: "
                                   f"{_child_error(res.stderr)}"))
            tmp.write_text(res.stdout, encoding="utf-8")
            target = tmp
        try:
            # Each trace block self-identifies its account, so no
            # per-file header is needed.
            proc = _run_cmd(_cmd("taxjson-explain") + common
                            + ["--no-match-status", str(_NO_MATCH),
                               str(target)])
            if proc.returncode == _NO_MATCH:
                continue
            matched = True
            rc = proc.returncode or rc
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    if not matched:
        emit_line("tjs wash-sales: note: no matching gains found",
                  file=sys.stderr)
    raise SystemExit(rc)


def _audit_diag(kind: str, text: str) -> None:
    """`tjs audit: <kind>: ...` on stderr, wrapped (lib/out)."""
    from taxjson.lib.out import emit
    emit(kind, text, prog="tjs audit")


def _audit_flags(settings: Settings, year: Any, root: Path, tax_date: str, country: str,
                 base_currency: str, rates: Path, tmap: Path, cache: Path,
                 mh_file: Optional[Path], args: argparse.Namespace) -> Callable[[], List[str]]:
    """`tjs audit`: the flags every taxjson-audit computation shares (the
    country, date basis, currency, years, rates, maps, dated events,
    missing history, option timing, income dating, filing positions,
    filed years and the filters), returned as the function that builds
    them; a year other than the project's that is locked recomputed with
    the option timing and date basis its lock recorded, each said, a
    damaged lock's said and another country's refused (cmd_audit)."""
    # A LOCKED year is recomputed with the option timing its lock
    # recorded, as check-filed does: `audit --year 2025` from a 2026
    # project without since = 2025 printed -1,000 for a year filed at
    # -601, with no word (S048-18).
    # The lock may live in the previous year's project (per-year
    # layout, [settings] prior_year_record) — it counts the same as a
    # local filed/<year>.json (A2-0335, A2-0664). Its recorded DATE
    # BASIS applies too, as check-filed's lock_settings does: a year
    # filed on settlement dates is audited on settlement dates
    # (A2-0334, A2-1129).
    _timing_flags = option_timing_flags(settings)
    if year and str(year) != str(settings.year):
        from taxjson.bin import taxjson_filed as _tf
        from taxjson.lib import filed_locks
        try:
            _hit = _tf.lock_for_year(root, settings, int(year))
        except filed_locks.PriorRecordError as e:
            _die(str(e))
        if _hit is not None:
            _lp, _where = _hit
            _lname = filed_locks.lock_label(root, _lp)
            _lock = None
            # The one reader every lock consumer uses (read_lock: the
            # JSON, no key written twice, the shape and year field). A
            # damaged lock's settings are not applied, and said.
            try:
                _lock = filed_locks.read_lock(_lp, label=_lname)
                _lot = (_lock.get("option_timing")
                        if isinstance(_lock, dict) else None)
            except filed_locks.LockProblem as e:
                _lock = _lot = None
                _audit_diag("warning",
                            f"{e} — {year} is recomputed with this "
                            f"project's option timing and date basis, "
                            f"which may not be the ones it was filed "
                            f"on.")
            # A lock closed under the other country is refused before
            # any of its settings apply, as check-filed and handoff
            # refuse it: a US lock's trade date basis moved a Canadian
            # sale into the audited year and the audit passed (backlog
            # TJ-0550).
            _cp = (filed_locks.lock_country_problem(
                _lock, settings, _lname,
                prior=_where == "prior_year_record")
                if isinstance(_lock, dict) else None)
            if _cp:
                _die(f"audit --year {year}: {_lname} is another "
                     f"country's lock", _cp)
            _lb = _tf.lock_settings(_lock, settings).tax_date \
                if isinstance(_lock, dict) else None
            if _lb in ("settle", "trade") and _lb != tax_date:
                _audit_diag("note",
                            f"{year} is locked ({_lname}) — recomputed on "
                            f"the {_lb} date basis its lock recorded, not "
                            f"this project's {tax_date} basis.")
                tax_date = _lb
            if isinstance(_lot, dict):
                _lf = _tf._lock_timing_flags(settings, int(year), _lot)
                if _lf != _timing_flags:
                    _audit_diag("note",
                                f"{year} is locked ({_lname}) — "
                                f"recomputed with the option timing its "
                                f"lock recorded ({' '.join(_lf)}), not "
                                f"this project's "
                                f"({' '.join(_timing_flags) or 'none'}).")
                _timing_flags = _lf

    def common_flags() -> List[str]:
        fl = ["--country", country, "--tax-date", tax_date,
              "--base-currency", base_currency]
        if year:
            fl += ["--year", str(year)]
        if settings.year:
            # The saved gains files are year-scoped (R1-191).
            fl += ["--check-year", str(settings.year)]
        if rates.exists():
            fl += ["--rates", str(rates)]
        if tmap.exists():
            fl += ["--map", str(tmap)]
        # The listings the run joined by their transfer journal (its
        # effective map): `audit QZD.U.TO` finds the sale the run booked
        # as QZD.TO (backlog TJ-0773).
        from taxjson.lib.project_layout import EFFECTIVE_MAP as _EFF_MAP
        if (cache / _EFF_MAP).is_file():
            fl += ["--effective-map", str(cache / _EFF_MAP)]
        from taxjson.lib.dated_events import STATE as _DE_STATE
        if (cache / _DE_STATE).is_file():
            # The .tt RENAME line a ticker change's row came from
            # (backlog TJ-0359).
            fl += ["--dated-events", str(cache / _DE_STATE)]
        if mh_file is not None:
            fl += ["--incomplete-history", str(mh_file)]
        fl += _timing_flags
        # The project's income-dating overrides, as the run applied
        # them (re-audit A2-0033: the trust ROC record date).
        fl += income_dating_flags(settings)
        # ...and its filing positions against the loss rule (ALLOWLOSS).
        fl += _loss_override_flags(root / "work")
        # ...and its filed years: a US wash-sale basis add to a
        # replacement sold in one is booked in the loss's year
        # (US-WASH-22), as every gains pass of the run books it
        # (backlog TJ-0313). Canada: none.
        fl += _locked_year_flags(root, settings)
        for sym in getattr(args, "symbol", None) or []:
            fl += ["--symbol", sym]
        if getattr(args, "gain_id", None):
            fl += ["--id", args.gain_id]
        if getattr(args, "date", None):
            fl += ["--date", args.date]
        if getattr(args, "account", None):
            fl += ["--account", args.account]
        if getattr(args, "summary", False):
            fl.append("--summary")
        if getattr(args, "no_trace", False):
            fl.append("--no-trace")
        if getattr(args, "no_color", False):
            fl.append("--no-color")
        if not _details(args):
            # Essentials first (docs/output-style.md): the rounding
            # note is the --details view.
            fl.append("--brief")
        # "Nothing matched" is decided across ALL invocations below.
        fl += ["--no-match-rc", "3"]
        return fl
    return common_flags


def _audit_invocations(crypto: List[str], crypto_group: List[str], root: Path,
                       acfgs: Dict[str, Any], cache: Path,
                       common_flags: Callable[[], List[str]], country: str,
                       sheltered_base: Path, equity: List[str]
                       ) -> Tuple[List[Tuple[List[str], Optional[Path], List[str]]], List[str],
                                  List[str], List[str]]:
    """`tjs audit`: the computations, as the run made them: one blended
    pass over the equity accounts (their base books merged afresh), then
    the crypto accounts' blend or each crypto account on its own books;
    an account deferred on an election, and those it blends with, left
    out; one with no books yet noted. Returns them, the accounts not
    covered, those pending and those held back (cmd_audit)."""
    import os as _os
    import tempfile
    from taxjson.pipeline.dispatch import run_cmd as _run
    # (flags, cleanup_path|None) per engine computation, mirroring the
    # pipeline: ONE blended pass over the equity taxable accounts, then
    # the crypto accounts — ONE blended pass too for a Canadian project
    # with two or more of them (the pipeline's crypto blend), else each
    # on its own books.
    invocations: List[Tuple[List[str], Optional[Path], List[str]]] = []  # flags, merged base, accounts
    crypto_blend = [n for n in crypto if n in crypto_group]

    _no_input = _accounts_skipped_for_no_inputs(root)
    # Configured accounts (with inputs) the audit could not cover: the
    # tie-out printed "✓" over the rest and exited 0, and the checklist
    # marked every disposition traced (S047-16).
    _uncovered: List[str] = []

    # An account the last run deferred at a corp-action election (its
    # work/<acct>_pending_elections.json): its book is incomplete and
    # the cross-account pass never ran, so its dispositions cannot tie
    # (2026-10 workflow audit: 3 MISMATCHED and FAILED over a pending
    # election). Left out, and said.
    _pending = sorted(n for n in acfgs
                      if (cache / f"{n}_pending_elections.json").exists())
    # The accounts blended with a deferred one: the run's cross-account
    # pass never ran, so their saved gains are per-account figures that
    # the audit's blended recomputation cannot tie (3 MISMATCHED and a
    # "stale work/" FAILED over a pending election: backlog TJ-0196).
    # Not audited either, and said.
    _held_back: List[str] = []

    def _blended_invocation(names: List[str], *, is_crypto: bool) -> None:
        if _pending:
            # The run runs no cross-account pass while ANY account
            # (sheltered too) waits on an election (cmd_run: `not
            # pending_accounts`), the crypto blend included: an equity
            # election left the crypto accounts' per-account gains,
            # which the audit's blend could not tie (backlog TJ-0314).
            _held_back.extend(n for n in names if n not in _pending)
            return
        bases = [cache / f"{n}_base.json" for n in names
                 if (cache / f"{n}_base.json").exists()]
        missing = [n for n in names
                   if not (cache / f"{n}_base.json").exists()
                   and n not in _no_input]
        _uncovered.extend(missing)
        if missing:
            _audit_diag("note", f"no books yet for {', '.join(missing)} "
                                f"— run `tjs run` to include them.")
        if not bases:
            return
        cleanup: Optional[Path] = None
        if len(bases) == 1:
            base_arg = bases[0]
        else:
            # Merge the CURRENT per-account base books — the same
            # inputs the pipeline's blended pass merged, rebuilt fresh
            # so a stale .blend artifact can't smuggle old numbers
            # past the tie-out.
            _check_books_before_merge(bases)
            fd, tmp = tempfile.mkstemp(prefix="taxjson_audit_",
                                       suffix=".json")
            _os.close(fd)
            cleanup = Path(tmp)
            res = _run(_cmd("taxjson-merge")
                       + [str(b) for b in bases],
                       capture_output=True)
            if res.returncode != 0:
                cleanup.unlink(missing_ok=True)
                _die(f"could not merge the taxable base books: "
                     f"{_child_error(res.stderr)}")
            cleanup.write_text(res.stdout, encoding="utf-8")
            base_arg = cleanup
        fl = common_flags() + ["--base", str(base_arg)]
        _us = country in ("us", "usa")
        if _us and is_crypto:
            # A US crypto blend carries lots only: §1091 does not reach
            # digital assets (stage_blended_wash_pass no_wash).
            fl.append("--no-wash")
        elif sheltered_base.exists():
            fl += ["--sheltered", str(sheltered_base)]
        if _us:
            fl.append("--per-account-basis")
        for n in names:
            for src in _audit_source_files(cache, n, list(acfgs)):
                fl += ["--source", str(src)]
            if (cache / f"{n}_filled.json").exists():
                fl += ["--filled", str(cache / f"{n}_filled.json")]
            chk = cache / f"{n}_gains_wash.json"
            if not chk.exists():
                chk = cache / f"{n}_gains.json"
            if chk.exists():
                fl += ["--check", str(chk)]
        invocations.append((fl, cleanup, [n for n in names if n not in missing]))

    if equity:
        _blended_invocation(equity, is_crypto=False)
    if crypto_blend:
        _blended_invocation(crypto_blend, is_crypto=True)

    for n in crypto:
        if n in crypto_blend:
            continue
        if n in _pending:
            continue                    # its book is incomplete: named below
        base = cache / f"{n}_base.json"
        if not base.exists():
            if n not in _no_input:
                _uncovered.append(n)
                _audit_diag("note", f"no books yet for {n} — run "
                                    f"`tjs run` to include it.")
            continue
        fl = common_flags() + ["--base", str(base)]
        if country in ("us", "usa"):
            # §1091 does not reach digital assets — mirror the
            # pipeline's --no-wash on US crypto books.
            fl.append("--no-wash")
        elif sheltered_base.exists():
            fl += ["--sheltered", str(sheltered_base)]
        for src in _audit_source_files(cache, n, list(acfgs)):
            fl += ["--source", str(src)]
        if (cache / f"{n}_filled.json").exists():
            # Crypto rows priced by fill-crypto-prices (R1-271).
            fl += ["--filled", str(cache / f"{n}_filled.json")]
        chk = cache / f"{n}_gains_wash.json"
        if not chk.exists():
            chk = cache / f"{n}_gains.json"
        if chk.exists():
            fl += ["--check", str(chk)]
        invocations.append((fl, None, [n]))
    return invocations, _uncovered, _pending, _held_back


def _audit_report(_held_back: List[str], _pending: List[str],
                  invocations: List[Tuple[List[str], Optional[Path], List[str]]],
                  args: argparse.Namespace, _acct: Optional[str], year: Any,
                  _uncovered: List[str], base_currency: str, country: str) -> None:
    """`tjs audit`'s run and report: a refusal when nothing can be
    audited; each computation through taxjson-audit, as text or JSON,
    until one stops at an input, its merged book removed; a filter that
    matched nothing refused; what was not audited, and why, in the JSON
    and as warnings; the exit status (cmd_audit)."""
    import json as _json
    from taxjson.pipeline.dispatch import run_cmd as _run
    _also = sorted(set(_held_back))
    _pending_head = (f"not audited — corp-action elections pending for "
                     f"{', '.join(_pending)}"
                     + (f" (so {', '.join(_also)} too: no cross-account "
                        f"pass)" if _also else ""))
    if not invocations and _pending:
        _die(_pending_head,
             "Decide them (`tjs elect --pending`), then `tjs run`.")
    if not invocations:
        _die("no computed books in work/ — run `tjs run` first.")

    rc = 0
    no_match = 0
    _printed_groups = 0
    json_docs: List[Dict[str, Any]] = []
    _stop: Optional[int] = None         # the book whose audit stopped (exit 2)
    page = _out.Doc(exact=True, width_=0, live=True)
    try:
        for _i, (fl, _cl, _ns) in enumerate(invocations):
            cmd = _cmd("taxjson-audit") + fl
            if getattr(args, "json", False):
                res = _run(cmd + ["--json"], capture_output=True)
                if res.stderr:
                    sys.stderr.write(res.stderr)
                if res.returncode == 3:
                    no_match += 1
                    continue
                if res.returncode == 2:
                    # An input the audit cannot read: its own one line
                    # above says which; the same exit code as the text
                    # mode, and the next book would only repeat it
                    # (re-audit A2-0796).
                    rc, _stop = 2, _i
                    break
                try:
                    json_docs.append(_json.loads(res.stdout))
                except ValueError:
                    _die(f"taxjson-audit produced no JSON "
                         f"(rc={res.returncode}).")
                rc = max(rc, res.returncode or 0)
            else:
                if _printed_groups:
                    page.blank()            # one blank line between groups
                    sys.stdout.flush()
                _printed_groups += 1
                res = _run(cmd)
                if res.returncode == 3:
                    no_match += 1
                    continue
                rc = max(rc, res.returncode or 0)
                if res.returncode == 2:
                    _stop = _i
                    break               # (A2-0796: printed once)
    finally:
        for _cl in [c for _f, c, _n in invocations if c is not None]:
            _cl.unlink(missing_ok=True)
    if no_match and no_match == len(invocations) and _also:
        # The filter's dispositions may be in an account held back.
        _die(_pending_head,
             "Decide them (`tjs elect --pending`), then `tjs run`.")
    if no_match and no_match == len(invocations):
        _filt = [f"{k} {v}" for k, v in (
            ("symbol", " ".join(getattr(args, "symbol", None) or [])),
            ("--id", getattr(args, "gain_id", None)),
            ("--date", getattr(args, "date", None)),
            ("--account", _acct)) if v]
        _die(f"no disposition matches {', '.join(_filt)}"
             + (f" in {year}" if year else "")
             + " — check the filter (`tjs audit --summary` lists "
               "every event; --date matches the trade or settlement "
               "date).")

    # What the audit could not cover, decided BEFORE anything is shown:
    # the --json document's `failed` and the exit status say the same
    # (it said failed=false over an account with no books while the
    # command exited 1: backlog TJ-0315). One line each (the checklist
    # shows a failed command's last line).
    not_audited, coverage_failures = _stopped_at(invocations, _stop)
    if _uncovered and not _acct:
        _nb = sorted(set(_uncovered))
        not_audited += [{"account": n, "reason": "no_books"} for n in _nb]
        coverage_failures.append(
            f"not audited — no books for {', '.join(_nb)}; their "
            f"dispositions are not in the tie-out above. Run `tjs "
            f"run`.")
    if _pending and not _acct:
        not_audited += [{"account": n, "reason": "elections_pending"}
                        for n in _pending]
        not_audited += [{"account": n, "reason": "no_cross_account_pass"}
                        for n in _also]
        coverage_failures.append(
            f"{_pending_head}; decide them (`tjs elect --pending`), "
            f"then `tjs run`.")
    if coverage_failures:
        rc = max(rc, 1)
    if getattr(args, "json", False):
        doc = _merge_audit_json(json_docs, base_currency, country)
        doc["not_audited"] = not_audited
        doc["coverage_failures"] = coverage_failures
        doc["failed"] = bool(doc.get("failed")) or bool(coverage_failures)
        # The merged document's own number: one book's taxjson-audit
        # document comes back whole, with that output's (lib/json_contract).
        doc.pop("schema_version", None)
        _json_out(doc, schema="audit")
    elif not _details(args) and not rc:
        from taxjson.lib.out import details_hint as _dh
        page.line(_dh("tjs audit --details",
                      "what the tie-out compares, and why the totals can "
                      "differ from the return's rows by cents"))
    if coverage_failures:
        from taxjson.lib.out import warn as _warn
        for _cf in coverage_failures:
            _warn(_cf, prog="tjs audit")
    if rc:
        raise SystemExit(rc)


def cmd_audit(args: argparse.Namespace) -> None:
    """`tjs audit`: the authoritative justification of every
    capital-gain figure. One block per taxable disposition: the parsed
    broker row (nominal currency, original ticker, source file), the
    ticker.map rename, the exact FX rate applied (with provenance and
    a to-the-cent recomputation against the base books), the engine's
    disposition math, the wash-sale / superficial-loss determination
    with replacement lots resolved, a tie-out against the pipeline's
    saved gains files, and the full ACB/FIFO pool trace. Runs the same
    blended computation the pipeline runs (ITA s.47 cross-account ACB
    / US cross-account §1091), so the numbers ARE the filed numbers —
    and exits 1 if any cross-check disagrees."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_settings(root, missing_ok=True)
    if settings.empty:
        _die("no taxjson.toml here — audit is a project command "
             "(run it from the project root, or pass -C).")
    # The recomputation reads the market lines (SECFUT, INDEXOPT, MULT)
    # the run read: it soft-reads the config, which does not point
    # lib/markets at the project (backlog TJ-1029).
    _use_project_ticker_map(root)
    country = _country(settings)
    tax_date = _tax_date(settings)
    base_currency = str(settings.base_currency
                        or _home_currency(settings)).upper()
    year = None if getattr(args, "all_years", False) else (
        getattr(args, "year", None) or settings.year)

    acfgs = accounts_of(load_config(root, missing_ok=True))
    taxable = {n: c for n, c in acfgs.items() if c.taxable}
    if not taxable:
        _die("no taxable accounts in taxjson.toml — nothing to audit.")
    # taxjson.toml order, as the pipeline's blended pass merges them
    # (CA-DATE-14 / US-DATE-13); alphabetical failed the tie-out on a
    # book with same-moment rows in two accounts (A2-1592 sibling).
    equity = [n for n, c in taxable.items() if not c.crypto]
    crypto = [n for n, c in taxable.items() if c.crypto]
    # The crypto accounts the run computes in ONE blended pass (cmd_run's
    # _crypto_blend / _us_crypto_blend), decided on the configuration as
    # the run decides it: every crypto account of a Canadian project
    # with two or more (s.47 pooled ACB, the superficial-loss rule
    # across exchanges), or a US project's crypto accounts without
    # `transfers` once a move between two of them was paired (their
    # work/<account>_own_moves.json): FIFO per account, no wash-sale
    # rule, the move carrying the sender's lots (US-CRYPTO-05). Each
    # account on its own used to lose the moved coins' basis: a correct
    # gain failed the tie-out (backlog TJ-0311).
    if country not in ("us", "usa"):
        crypto_group = list(crypto) if len(crypto) >= 2 else []
    elif any(not taxable[n].transfers
             and (cache / f"{n}{OWN_MOVES_SUFFIX}").is_file()
             for n in crypto):
        crypto_group = [n for n in crypto if not taxable[n].transfers]
    else:
        crypto_group = []
    _acct = getattr(args, "account", None)
    if _acct:
        if _acct not in taxable:
            _die(f"--account {_acct!r} is not a taxable account in "
                 f"taxjson.toml (audit covers: "
                 f"{', '.join(sorted(taxable))}).")
        # Only the computation that holds the account: the blended
        # equity pass (still over ALL equity books — display filter
        # only) or the crypto pass that holds it — the crypto blend over
        # all its books (narrowing it to one book re-ran that book's own
        # cost and failed the tie-out, #79), else that account's own
        # books. The others would print empty 0/0 blocks.
        if _acct in crypto:
            equity = []
            crypto = (list(crypto_group) if _acct in crypto_group
                      else [_acct])
        else:
            crypto = []

    sheltered_base = cache / "sheltered_base.json"
    mh_file = _missing_history_arg(root)
    rates = cache / "to_base.csv"
    tmap = _PL.ticker_map_path(root)

    common_flags = _audit_flags(settings, year, root, tax_date, country, base_currency, rates,
                                tmap, cache, mh_file, args)

    invocations, _uncovered, _pending, _held_back = _audit_invocations(
        crypto, crypto_group, root, acfgs, cache, common_flags, country, sheltered_base, equity)

    _audit_report(_held_back, _pending, invocations, args, _acct, year, _uncovered,
                  base_currency, country)


def _stopped_at(invocations: List[Tuple[List[str], Optional[Path], List[str]]],
                stop: Optional[int]) -> Tuple[List[Dict[str, str]], List[str]]:
    """What `tjs audit` left unaudited when a book's audit stopped at an
    error (exit 2: an input it cannot read; A2-0796 stops there, as an
    input every book shares would repeat its line): that book's accounts
    (`stopped`) and the later books' (`not_reached`), as `not_audited`
    entries and one coverage line, so the text, `--json` and the exit
    status name them (backlog TJ-3007, TJ-3014 to TJ-3016)."""
    if stop is None:
        return [], []
    stopped = invocations[stop][2]
    later = [n for _f, _c, ns in invocations[stop + 1:] for n in ns]
    return ([{"account": n, "reason": "stopped"} for n in stopped]
            + [{"account": n, "reason": "not_reached"} for n in later],
            [f"not audited — {', '.join(stopped + later)}: the audit stopped at "
             f"the error above. Fix it, then `tjs audit` again."])


def _merge_audit_json(docs: List[Dict[str, Any]], base_currency: str,
                      country: str) -> Dict[str, Any]:
    """One `tjs audit --json` document from the per-book
    taxjson-audit runs: events concatenated, totals summed over EVERY
    book (S026-11 pins it), reconciliation failures kept (they were
    dropped when two books were merged). The gain total is the computed
    dispositions', as one book's is: an unknown-cost disposition
    (`manual_reporting`) is reported by hand and counted in
    `manual_reporting` only (backlog TJ-0302)."""
    if len(docs) == 1:
        return docs[0]
    from taxjson.bin.taxjson_audit import (disallowed_total, gain_total,
                                           manual_reporting_totals, totals_note)
    events = [e for d in docs for e in d.get("events") or []]

    def _total(ev_key: str, doc_key: str, of_events) -> float:
        # Over the events, rounded ONCE: adding each book's already-
        # rounded total put the audit a cent off wash-sales (S047-17).
        # Books whose events carry no amount keep their own totals.
        # The audit's own sums (backlog TJ-3019).
        if events and all(ev_key in e for e in events):
            return M.shown(of_events(events), 2)
        return M.shown(sum(float(d.get(doc_key) or 0.0) for d in docs), 2)
    out = {"base_currency": base_currency, "country": country,
           "events": events,
           "total_gain": _total("gain", "total_gain", gain_total),
           "total_disallowed": _total("disallowed_amount",
                                      "total_disallowed", disallowed_total),
           "manual_reporting": manual_reporting_totals(events),
           "totals_note": totals_note(country),
           # Each book's pool-trade check summed, and what the audit
           # compares (backlog TJ-0332, TJ-0333).
           "pool_trades": {
               **{k: sum(int((d.get("pool_trades") or {}).get(k) or 0)
                         for d in docs)
                  for k in ("checked", "tied", "untraced")},
               **{k: [m for d in docs for m in
                      (d.get("pool_trades") or {}).get(k) or []]
                  for k in ("mismatched", "untraced_rows")},
               # A fill reduced after a broker cancellation, where a book
               # has one (backlog TJ-5270).
               **{k: [m for d in docs for m in
                      (d.get("pool_trades") or {}).get(k) or []]
                  for k in ("reduced_rows",)
                  if any((d.get("pool_trades") or {}).get(k) for d in docs)}},
           # The rows that are not trades, the same way (TJ-0359).
           "pool_other_rows": {
               **{k: sum(int((d.get("pool_other_rows") or {}).get(k) or 0)
                         for d in docs)
                  for k in ("checked", "tied", "untraced")},
               **{k: [m for d in docs for m in
                      (d.get("pool_other_rows") or {}).get(k) or []]
                  for k in ("mismatched", "rows")}},
           "scope": next((d["scope"] for d in docs if d.get("scope")),
                         None),
           "reconciliation_failures": [
               f for d in docs
               for f in d.get("reconciliation_failures") or []],
           "failed": any(d.get("failed") for d in docs)}
    return out


def _register_edge_cases(sub: argparse._SubParsersAction) -> None:
    p_edge = sub.add_parser(
        "edge-cases",
        help="Trades on a year-end or 30-day-window edge",
        description="Year-boundary and superficial-loss-window edge "
             "cases: trades that settle in another year, options and "
             "income around Dec 31, crypto near midnight, renames, and "
             "acquisitions/sales near day 30 of a loss's window — "
             "where each lands and why.")
    p_edge.add_argument("account", nargs="?",
                        help="Account (default: all)")
    p_edge.add_argument("--margin", type=int, default=3, metavar="DAYS",
                        help="How close to day 30 counts as an edge "
                             "(default 3: days 27-33)")
    p_edge.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_edge.set_defaults(func=cmd_edge_cases)


def _register_check_dates(sub: argparse._SubParsersAction) -> None:
    p_cd = sub.add_parser(
        "check-dates",
        help="Check trade and settlement dates for each market",
        description="Check every trade and settlement date against its "
             "market's calendar (crypto 24/7, futures 23/5, stocks "
             "incl. overnight, options and Canadian listings on "
             "exchange days); exit 1 on an impossible date.")
    p_cd.add_argument("account", nargs="?", help="Account (default: all)")
    p_cd.add_argument("--all", action="store_true",
                      help="List every row, not the first 10 per kind")
    p_cd.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_cd.set_defaults(func=cmd_check_dates)


def _register_check_tt(sub: argparse._SubParsersAction) -> None:
    p_ctt = sub.add_parser(
        "check-tt",
        help="Check every .tt line and name each problem",
        description="Read every line of the .tt files with the parsers "
             "`tjs run` uses and name each problem with its file "
             "and line (the run stops at the first): a typo'd action, "
             "a bad date or number, an OPENING in a crypto account, "
             "two declarations that disagree; warnings for what the "
             "run books as written. With no item, every account's "
             "inputs/<account>/*.tt; an account name, its files; a "
             "path ending in .tt, that file. Nothing is written; "
             "exit 1 on a line the run would refuse.")
    p_ctt.add_argument("items", nargs="*", metavar="ACCOUNT|FILE.tt",
                       help="Account names and/or .tt files (default: "
                            "every account's)")
    p_ctt.add_argument("--account", metavar="NAME",
                       help="The account a FILE.tt is read as (default: "
                            "the name of the folder it sits in)")
    _kind = p_ctt.add_mutually_exclusive_group()
    _kind.add_argument("--crypto", action="store_true",
                       help="A FILE.tt outside a project is a crypto "
                            "account's (an OPENING line is refused)")
    _kind.add_argument("--equity", action="store_true",
                       help="A FILE.tt outside a project is a share "
                            "account's (a bare symbol is warned about)")
    p_ctt.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_ctt.set_defaults(func=cmd_check_tt)


def _register_spinoffs(sub: argparse._SubParsersAction) -> None:
    p_spin = sub.add_parser(
        "spinoffs",
        help="Spin-offs: election, value used, cost booked",
        description="Every spin-off: the election, the value per share "
                    "used, the income and new-share cost booked; flags a "
                    "zero value or a missing election.")
    p_spin.add_argument("account", nargs="?", help="Account (default: all)")
    p_spin.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_spin.set_defaults(func=cmd_spinoffs)


def _register_splits(sub: argparse._SubParsersAction) -> None:
    p_split = sub.add_parser(
        "splits",
        help="Splits and consolidations, holdings before/after",
        description="Every split, consolidation and rename with the "
                    "holdings before and after; flags a split applied "
                    "twice or a fractional result. Exit 1 on a likely "
                    "double application.")
    p_split.add_argument("account", nargs="?", help="Account (default: all)")
    p_split.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_split.set_defaults(func=cmd_splits)


def _register_journals(sub: argparse._SubParsersAction) -> None:
    p_jn = sub.add_parser(
        "journals",
        help="Broker journals between listings, and their state",
        description="Every broker journal between two listings of one "
             "security, per account: its date, FROM -> TO, quantity, "
             "broker, how it was found (a Questrade BRW journal, an RBC "
             "journal transfer, an IB InterDepot, a move across brokers "
             "with a listing change, a .tt or ticker.map line) and its "
             "state — joined (the ticker.map line that pools them), "
             "suggested (the reason, and the `.tt` line `JOURNAL <date> "
             "FROM TO QTY` or ticker.map line that settles it) or "
             "refused (the reason, and the undo). Read from the last "
             "`tjs run`; no network.")
    p_jn.add_argument("--account", metavar="NAME",
                      help="Only this account's journals")
    p_jn.add_argument("--year", type=int, metavar="YYYY",
                      help="Only the journals dated in this year "
                           "(default: every year)")
    p_jn.add_argument("--pending", action="store_true",
                      help="Only the pending journals (suggested, or "
                           "refused with no ticker.map line of yours "
                           "behind it); exit 1 when there is one")
    p_jn.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text (a stable schema: "
                           "docs/settings.md)")
    p_jn.set_defaults(func=cmd_journals)


def _register_renames(sub: argparse._SubParsersAction) -> None:
    p_ren = sub.add_parser(
        "renames",
        help="Ticker changes as dated events",
        description="Every ticker change as a dated event (where it came "
             "from, the position and cost it carried) and every trade "
             "in an old ticker after its rename, with the `.tt` line "
             "in the account's folder that resolves it (`RENAME "
             "YYYY-MM-DD OLD NEW late=fold` or `late=separate`); exit 1 "
             "while one is not declared. Each dated "
             "rename names its source (a broker row, an IB contract id, "
             "a .tt line, a ticker.map line); the look-alike renames "
             "the Questrade, RBC and Webull exports show are listed as "
             "suggested, with the `.tt` line `RENAME <date> OLD NEW` "
             "that books each.")
    p_ren.add_argument("account", nargs="?", help="Account (default: all)")
    p_ren.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_ren.add_argument("--pending", action="store_true",
                       help="Only the undeclared late trades, the "
                            "suggested renames and the declared renames "
                            "that book nothing; exit 1 when there is one")
    p_ren.set_defaults(func=cmd_renames)


def _register_tax_logic(sub: argparse._SubParsersAction) -> None:
    p_logic = sub.add_parser(
        "tax-logic",
        help="Every tax rule taxjson applies, a bullet each",
        description="A short statement of every rule taxjson applies for "
             "this project's country, with the project's settings "
             "filled in. It is the spec the code is tested against: "
             "each statement has a rule id (--ids) that the tests cite, "
             "and Canadian and US rules never mix.")
    from taxjson.lib.country import country_arg as _country_arg
    p_logic.add_argument("--country", type=_country_arg,
                         metavar="{canada,ca,usa,us}",
                         help="Country (default: the project's; required "
                              "outside a project)")
    p_logic.add_argument("--ids", action="store_true",
                         help="Prefix each statement with its rule id "
                              "(e.g. [CA-SL-02]), the id tests and code "
                              "cite")
    p_logic.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text (each rule with "
                              "its id)")
    p_logic.set_defaults(func=cmd_tax_logic)


def _register_sanity(sub: argparse._SubParsersAction) -> None:
    p_san = sub.add_parser(
        "sanity",
        help="Compare positions with your broker's holdings",
        description="Cross-check the open positions against your "
             "broker's holdings files (.toml, [[holding]] tables): bare "
             "items are summed together (aggregate), "
             "ACCOUNT[+ACCOUNT]=FILE[+FILE] items are checked as their "
             "own paired group; with no items, the year's holdings/ "
             "snapshots (each matched to its account by the broker "
             "account id or the file name) and any account's "
             "`holdings = [...]` in taxjson.toml. Exit 1 on any "
             "difference.")
    p_san.add_argument("items", nargs="*",
                       metavar="ACCOUNT|FILE|ACCOUNT[+ACCOUNT]=FILE[+FILE]",
                       help="Bare account names and holdings .toml files "
                            "form one aggregate group (combined positions "
                            "vs combined holdings); ACCOUNT=FILE items "
                            "pair specific accounts with specific files "
                            "— many-to-many with '+', and a repeated "
                            "left-hand side merges (margin=ibkr.toml "
                            "margin=webull.toml). With NO items the "
                            "pairings come from the year's holdings/ "
                            "folder (matched by broker account id or "
                            "file name) and each account's "
                            "`holdings = [...]` in taxjson.toml (paths "
                            "of its broker positions files)")
    p_san.add_argument("--tolerance", type=_nonneg_float_arg, default=1e-4,
                       help="Quantity tolerance (default: 0.0001)")
    p_san.add_argument("--cost-tolerance", type=_nonneg_float_arg,
                       default=1.0,
                       help="Cost difference ignored, in the cost's "
                            "currency (default: 1.00, or 0.1%% of the "
                            "cost when larger)")
    p_san.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_san.set_defaults(func=cmd_sanity)


def _register_audit(sub: argparse._SubParsersAction) -> None:
    p_audit = sub.add_parser(
        "audit",
        help="Trace every capital gain back to its broker row",
        description="The authoritative justification of every capital "
             "gain: one block per disposition tracing it from the "
             "broker row through the ticker map, the FX rate and the "
             "ACB/FIFO pool to the gain, with the "
             "wash/superficial-loss math shown, every step recomputed "
             "and cross-checked against the books, and a tie-out "
             "against the pipeline's saved gains files. Exit 1 when "
             "any check disagrees.")
    p_audit.add_argument("symbol", nargs="*",
                         help="Filter: symbol prefix(es) "
                              "(default: every disposition)")
    p_audit.add_argument("--id", dest="gain_id",
                         help="Filter: transaction id prefix")
    p_audit.add_argument("--date", help="Filter: disposition date "
                                        "(YYYY-MM-DD)")
    p_audit.add_argument("--account", help="Filter: account name "
                                           "(display only — the "
                                           "computation stays blended)")
    # One plausible-year check for every --year (S047-14): 2204 or -5
    # audited nothing and printed an all-checkmark reconciliation.
    p_audit.add_argument("--year", type=_tax_year_arg,
                         help="Tax year (default: taxjson.toml)")
    p_audit.add_argument("--all-years", action="store_true",
                         help="Audit every year on the books")
    p_audit.add_argument("--summary", action="store_true",
                         help="One line per event (find ids to dive "
                              "into)")
    p_audit.add_argument("--no-color", action="store_true",
                         help="Disable ANSI color even on a tty "
                              "(NO_COLOR is honored too)")
    p_audit.add_argument("--no-trace", action="store_true",
                         help="Omit the engine pool traces")
    p_audit.add_argument("--json", action="store_true",
                         help="Emit the full audit as JSON")
    p_audit.set_defaults(func=cmd_audit)


def _register_wash_sales(sub: argparse._SubParsersAction) -> None:
    p_ws = sub.add_parser(
        "wash-sales",
        help="Each loss denied this year, and why",
        description="Detail each superficial loss / wash sale of the tax "
             "year and the loss denied — what <account>.sum shows only "
             "in totals — plus the warn-only flags (warrants, adjusted "
             "series, futures options) that need a manual check.")
    p_ws.add_argument("account", nargs="?", help="Account (default: all)")
    p_ws.add_argument("--explain", action="store_true",
                      help="Print the full ACB / superficial-loss calculation "
                           "trace for each wash sale instead of the summary "
                           "table")
    p_ws.add_argument("--json", action="store_true",
                     help="Emit JSON instead of text")
    p_ws.set_defaults(func=cmd_wash_sales)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_edge_cases(sub)
    _register_check_dates(sub)
    _register_check_tt(sub)
    _register_spinoffs(sub)
    _register_splits(sub)
    _register_journals(sub)
    _register_renames(sub)
    _register_tax_logic(sub)
    _register_sanity(sub)
    _register_audit(sub)
    _register_wash_sales(sub)
