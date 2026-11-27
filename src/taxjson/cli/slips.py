"""The slip commands of Before you file: reconcile-slips and slip-audit."""
import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

from taxjson.project.settings import settings_of
from taxjson.lib import project_layout as _PL
from taxjson.lib.cli_diag import note

from taxjson.cli.common import (
    _accounts, _artifact_year_mismatch, _require_listed, _warn_run_state,
)

from taxjson.cli.file import _taxable_gains_argv
from taxjson.pipeline.config import _country, load_config
from taxjson.pipeline.inputs import _tax_date
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.sanity import _json_out
from taxjson.lib.numeric import nonneg_float_arg as _nonneg_float_arg
from taxjson.model import money as M


def cmd_reconcile_slips(args: argparse.Namespace) -> None:
    """`tjs reconcile-slips SLIP.csv`: diff broker T5008/1099-B slips
    against the computed dispositions of all taxable accounts."""
    from taxjson.bin import taxjson_reconcile_slips

    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_config(root)["settings"]
    year = settings.year

    _slips = (args.slip_csv if isinstance(args.slip_csv, list)
              else [args.slip_csv])
    # IB's dividends report (T5/T3 data, `tjs slip-audit`) lives in
    # inputs/slips/ too: the documented `inputs/slips/*.csv` names it.
    from taxjson.lib.ib_dividends import is_dividends_report
    _ib = [f for f in _slips if is_dividends_report(Path(f))]
    if _ib:
        from taxjson.lib.brokerages.base import shown_name
        note("tjs reconcile-slips",
             f"skipped {', '.join(shown_name(f) for f in _ib)}: IB's "
             f"dividends report (T5/T3 income, read by `tjs "
             f"slip-audit`), not a T5008")
        _slips = [f for f in _slips if f not in _ib]
        if not _slips:
            _die_input("no T5008 slip CSV to reconcile",
                       "IB's dividends report is read by `tjs "
                       "slip-audit`.")
    gains_argv = _taxable_gains_argv(
        root, cache, exclude_crypto=True, prog="tjs reconcile-slips",
        required=True)
    # Books built for another tax year made every slip row read
    # MISSING_FROM_COMPUTED "dropped CSV rows or a missing statement?"
    # — the wrong cause (S047-24, S049-06).
    _stale = _artifact_year_mismatch(
        {Path(g).name: Path(g) for g in gains_argv[1::2]}, year)
    if _stale:
        got = ", ".join(f"{a} ({y})" for a, y in sorted(_stale.items()))
        _die(f"[settings].year is {year} but the work/ books were built "
             f"for another tax year: {got}",
             f"Rebuild with `tjs run` before reconciling the {year} "
             f"slips.")
    argv = list(_slips) + gains_argv
    tmap = _PL.ticker_map_path(root)
    if tmap.exists():
        # Slips print the broker's symbols; the books carry the
        # ticker.map consolidations (SAMPLK -> SAMPLJ, R1-19).
        argv += ["--ticker-map", str(tmap)]
    if year is not None:
        argv += ["--year", str(year)]
        # Same date convention stage_account feeds the gains engine:
        # IRS/1099-B scope by TRADE date, CRA/T5008 by SETTLEMENT; an
        # explicit tax_date config wins. Without this, a USA year-end
        # sale settling in January was on the 1099-B (and in
        # form-export) but missing from the computed side.
        tax_date = _tax_date(settings)
        argv += ["--date-basis", tax_date]
    argv += ["--country", _country(settings)]
    if args.tolerance is not None:
        # One token: '--tolerance -1' read the value as an option
        # (S036-00).
        argv.append(f"--tolerance={args.tolerance!r}")
    if args.json:
        argv.append("--json")
    if not _details(args) and not getattr(args, "json", False):
        # Essentials first (docs/output-style.md): the stage's default
        # view; --details prints its whole report.
        argv.append("--brief")
    raise SystemExit(taxjson_reconcile_slips.main(argv))


def _slip_audit_import_cra(root: Path, cfg: Dict[str, Any],
                           args: argparse.Namespace) -> None:
    """`tjs slip-audit [ACCOUNT] --import-cra PDF|DIR ... [--write]
    [--json]`: CRA-downloaded T5 / T3 slip PDFs as [[slip]] tables, each
    placed in a project account (and broker account, a T3 its fund) by
    the books (lib/cra_slips). A slip read twice is kept once; an
    amended slip replaces its original (in this import, or a table
    already in slips.toml — commented out, the file backed up). Shows
    what it would add; --write writes it to inputs/slips/slips.toml."""
    import contextlib
    import datetime as _dtm
    import io
    import tempfile
    from taxjson.lib import cra_slips as CS
    from taxjson.lib import out
    from taxjson.lib import slip_audit as SA
    from taxjson.lib.ib_dividends import IBReportError, read_report
    from taxjson.lib.safe_write import OutsideLinkError, write_user_file
    year = int(settings_of(cfg).year)
    acct = (_accounts(args, root, one="--import-cra") or (None,))[0]
    as_json = bool(getattr(args, "json", False))
    if acct and acct not in SA.slip_accounts(cfg):
        _die_input(f"account {acct!r} gets no T5 or T3",
                   "Name a taxable, non-crypto [accounts.*] account.")
    try:
        paths = CS.pdf_paths(args.import_cra)
    except CS.CraSlipError as e:
        _die_input(str(e))
    slips, skipped = [], []
    for p in paths:
        try:
            got = CS.read_pdf(p)
        except CS.CraSlipError as e:
            if "pdftotext is not installed" in str(e):
                _die_input(str(e))
            skipped.append(str(e))
            continue
        for sl in got:
            if sl.year != year:
                skipped.append(f"{sl.shown}: a {sl.year} slip; the "
                               f"project's year is {year}")
            elif sl.status == "cancelled":
                skipped.append(f"{sl.shown}: a cancelled slip — not "
                               f"imported (delete the table of the slip it "
                               f"cancels from slips.toml)")
            else:
                slips.append(sl)
    n_read = len(slips)
    slips, dup_notes = CS.drop_duplicates(slips)
    sf = SA.slips_dir(root) / SA.SLIPS_FILE
    old_text = ""
    have: List[Any] = []
    if sf.is_file():
        try:
            have = SA.load_slips_file(sf, cfg, year)[0]
            old_text = sf.read_text(encoding="utf-8-sig")
        except (SA.SlipsError, OSError, UnicodeDecodeError) as e:
            _die_input(str(e))
    reports, seen = [], set()
    for p in SA.find_ib_reports(root):
        try:
            rep = read_report(p)
        except IBReportError:
            continue
        if SA.report_identity(rep) not in seen:
            seen.add(SA.report_identity(rep))
            reports.append(rep)
    rate = {}
    for c in sorted({s.currency for s in slips} - {"CAD"}):
        a = SA.annual_average(c, year)
        rate[c] = a["rate"] if a else None
    try:
        # The market list's notes are the views' to print.
        with contextlib.redirect_stderr(io.StringIO()):
            groups = CS.book_groups(root, cfg, year)
    except ValueError as e:
        _die_input(str(e))
    # Originals and amended slips are placed apart, so an amended slip
    # lands where its original does and the pair is not shared out as
    # two slips.
    placed = []
    for part in ([s for s in slips if s.status != "amended"],
                 [s for s in slips if s.status == "amended"]):
        if part:
            try:
                placed += CS.place(part, groups, year, rate, reports,
                                   account=acct)
            except CS.CraSlipError as e:      # the shipped issuer file
                _die(str(e))
    order = {id(s): i for i, s in enumerate(slips)}
    placed.sort(key=lambda pl: order[id(pl.slip)])
    salt = SA.key_salt(root, create=True)
    keyed = [(pl, SA.broker_key(salt, pl.key) if pl.key else "")
             for pl in placed]
    hashes = {h for g in groups for h in g.hashes} | {
        r.account_hash for r in reports}
    existing = []
    for i, s0 in enumerate(have, 1):
        keys = {s0.broker_key} if s0.broker_key else set()
        keys |= {SA.broker_key(salt, h) for h in s0.broker_hashes
                 if h in hashes}
        existing.append(CS.Existing(
            index=i, type=s0.type, account=s0.account,
            security=s0.security, issuer=s0.issuer, status=s0.status,
            currency=s0.currency, boxes=dict(s0.boxes),
            keys=frozenset(k for k in keys if k),
            imported=s0.origin.startswith("cra:"), source=s0.origin))
    plan = CS.plan_import(keyed, existing)
    notes = [f"{pl.slip.shown}: {pl.how}" for pl in placed
             if not pl.account]
    notes += dup_notes + plan.notes
    notes += [f"skipped {m}" for m in skipped]
    in_import = list(plan.superseded)
    replaced_notes = in_import + [
        f"slips.toml [[slip]] #{e.index} ({e.status or 'original'}): "
        f"replaced by the amended slip {pl.slip.shown}"
        for e, pl, _k in plan.replace]
    tables = [CS.table(pl, k) for pl, k in plan.add] + [
        CS.table(pl, k) for _e, pl, k in plan.replace]
    text = ("\n\n".join(tables) + "\n") if tables else ""
    new_text = None
    if tables:
        today = _dtm.date.today().isoformat()
        base = old_text
        if plan.replace:
            try:
                base = SA.comment_out_tables(old_text, {
                    e.index: (f"Replaced by the amended slip "
                              f"{CS._comment(pl.slip.shown)} (tjs "
                              f"slip-audit --import-cra, {today}):")
                    for e, pl, _k in plan.replace})
            except SA.SlipsError as e:
                _die_input(str(e))
        if not base:
            base = f"year = {year}\n"
        sep = "" if base.endswith("\n\n") else (
            "\n" if base.endswith("\n") else "\n\n")
        new_text = base + sep + text
        # Never write a file slip-audit cannot read back.
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td) / SA.SLIPS_FILE
            tp.write_text(new_text, encoding="utf-8")
            try:
                back = SA.load_slips_file(tp, cfg, year)[0]
            except SA.SlipsError as e:
                _die(f"the tables to write do not read back ({e}); "
                     f"slips.toml is unchanged")
        if len(back) != len(have) - len(plan.replace) + len(tables):
            _die("the tables to write do not read back as written; "
                 "slips.toml is unchanged")
    backup = None
    if args.write and new_text is not None:
        sf.parent.mkdir(parents=True, exist_ok=True)
        try:
            backup = write_user_file(sf, new_text, root)
        except OutsideLinkError as e:
            _die_input(str(e))
    if as_json:
        _json_out({
            "year": year,
            "slips": [{"file": pl.slip.shown, "type": pl.slip.type,
                       "status": pl.slip.status, "issuer": pl.slip.issuer,
                       "currency": pl.slip.currency,
                       "boxes": {b: M.shown(v, 2) for b, v in
                                 pl.slip.boxes.items()},
                       "account": pl.account,
                       "broker_account": (list(pl.group.sources)
                                          if pl.group else None),
                       "fund": pl.security or None, "how": pl.how}
                      for pl in placed],
            "add": [pl.slip.shown for pl, _k in plan.add],
            "replace": [{"table": e.index, "by": pl.slip.shown}
                        for e, pl, _k in plan.replace],
            "replaced_in_import": in_import,
            "not_imported": notes, "tables": text,
            "written": bool(args.write and new_text is not None),
            "backup": (f"inputs/slips/{backup.name}" if backup
                       else None)}, schema="slip-audit --import-cra")
        return
    d = out.Doc(f"CRA SLIP IMPORT — tax year {year}: {n_read} slip(s) "
                f"read")
    body = [[pl.slip.shown, pl.slip.type, pl.slip.status, pl.slip.issuer,
             pl.slip.currency, pl.account or "-",
             ", ".join(pl.group.sources) if pl.group else "-",
             pl.security or ""] for pl in placed]
    if body:
        d.blank()
        d.table(["FILE", "TYPE", "STATUS", "ISSUER", "CUR", "ACCOUNT",
                 "BROKER ACCOUNT", "FUND"], body, drop=(3, 6, 2), key=0)
    if replaced_notes:
        d.section("Amended")
        d.items(replaced_notes)
    if notes:
        d.section("Not imported")
        d.items(notes)
    if text:
        d.section(f"{'Added to' if args.write else 'Would add to'} "
                  f"inputs/slips/{SA.SLIPS_FILE}")
        for ln in text.splitlines():
            d.line("  " + ln if ln else "")
    d.blank()
    if not text:
        d.para("Nothing to add.")
    elif args.write:
        d.para(f"Added {len(tables)} slip(s)"
               + (f", {len(plan.replace)} replacing an earlier slip (commented "
                  f"out" + (f"; the old file is {backup.name}" if backup
                            else "") + ")" if plan.replace else "")
               + "; `tjs slip-audit` compares them with the books.")
    else:
        d.para(f"Run again with --write to add {len(tables)} slip(s) to "
               f"inputs/slips/{SA.SLIPS_FILE}"
               + (f" ({len(plan.replace)} replacing an earlier slip, which is "
                  f"commented out)" if plan.replace else "") + ".")
    d.print()


def _refuse_slipless_listed(names: Any, slip_accounts: List[str]) -> None:
    """A list (`tjs slip-audit margin,rrsp`) naming an account that gets
    no T5 or T3 (registered, crypto) is refused, naming each, exit 1 (a
    single name: lib/slip_audit.audit's words, exit 2)."""
    if not names or len(names) < 2:
        return
    bad = [n for n in names if n not in slip_accounts]
    if not bad:
        return
    rest = ",".join(n for n in names if n not in bad)
    _die(("account " if len(bad) == 1 else "accounts ")
         + ", ".join(repr(n) for n in bad)
         + (" gets" if len(bad) == 1 else " get")
         + " no T5 or T3: only a taxable, non-crypto account does",
         f"Leave {'it' if len(bad) == 1 else 'them'} out of the list: "
         f"{rest!r}." if rest else
         "Name taxable, non-crypto [accounts.*] accounts.")


def cmd_slip_audit(args: argparse.Namespace) -> None:
    """`tjs slip-audit [ACCOUNT]`: the T5 / T3 slips in inputs/slips/
    (slips.toml, IB's dividends reports) against the books' income, per
    account and box (lib/slip_audit; tax-logic CA-SLIP-01..04). Canada
    only (lib/country COMMAND_COUNTRY). Exit 1 on a finding or on
    something not compared (`not_compared`)."""
    from taxjson.lib import slip_audit as SA
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    settings = settings_of(cfg)
    year = settings.year
    if year is None:
        _die_input("taxjson.toml has no [settings] year",
                   "slip-audit compares one tax year's slips.")
    from taxjson.lib.clean import no_books
    missing = no_books(root / "work")
    if missing:
        # (no books: before any run, or after a `tjs clean` that kept the
        # slip-key salt, the BOOKS column would read 0.00 throughout)
        _die("no work/ in the project" if missing == "no work/" else missing,
             "Run `tjs run` first.")
    # One account, or several (a comma list); --import-cra places the
    # slips no broker matches in one account.
    names = _accounts(args, root, one="--import-cra"
                      if getattr(args, "import_cra", None) else None)
    if getattr(args, "template", False):
        try:
            sys.stdout.write(SA.template(root, cfg))
        except (SA.SlipsError, ValueError) as e:
            _die_input(str(e))
        return
    if getattr(args, "import_cra", None):
        _slip_audit_import_cra(root, cfg, args)
        return
    _refuse_slipless_listed(names, SA.slip_accounts(cfg))
    if names and len(names) > 1:
        # A listed account with no books has no income to compare: it
        # was left out with no word (one with inputs and unreadable
        # books is the audit's `not_compared`).
        _require_listed(names, [n for n in names
                                if (root / "work" / f"{n}_base.json").exists()],
                        "books")
    if getattr(args, "write", False):
        _die_input("--write goes with --import-cra",
                   "It writes the imported slips into inputs/slips/"
                   "slips.toml.")
    tol = (SA.DEFAULT_TOLERANCE if args.tolerance is None
           else float(args.tolerance))
    # The books' income is what the slips are compared with: say when
    # they are not the clean result of the current inputs, as every
    # other review of the books does (backlog TJ-0557).
    _warn_run_state(root, cfg)
    try:
        rep = SA.audit(root, cfg, tolerance=tol, account=names)
    except SA.SlipsError as e:
        _die_input(str(e))
    if args.json:
        _json_out(rep, schema="slip-audit")
    else:
        for ln in SA.render(rep, details=_details(args)):
            print(ln)
    # Something not compared (an account with no readable books, a
    # report or a slip matched to no account, an unknown IB component)
    # is never a pass either: exit 0 with "Every slip agrees" certified
    # an account it never read (backlog TJ-0305).
    if rep["issues"] or rep.get("not_compared"):
        raise SystemExit(1)


def _register_reconcile_slips(sub: argparse._SubParsersAction) -> None:
    p_rec = sub.add_parser(
        "reconcile-slips",
        help="Compare broker T5008 / 1099-B slips to the books",
        description="Diff broker T5008 / 1099-B slip CSVs against the "
             "computed dispositions, per symbol, before filing; several "
             "slip files (one per broker) are reconciled together. "
             "Exit 1 on any mismatch.")
    p_rec.add_argument("slip_csv", nargs="+",
                       help="Slip CSV(s) — headers matched loosely "
                       "(symbol/ticker, quantity/box 16, proceeds/box 21, "
                       "cost/box 20); several (one per broker) are "
                       "reconciled together")
    p_rec.add_argument("--tolerance", type=_nonneg_float_arg, default=None,
                       help="Absolute per-symbol tolerance, a number >= 0 "
                            "(default 1.00)")
    p_rec.add_argument("--json", action="store_true",
                       help="Emit the reconciliation as JSON instead of "
                            "text")
    p_rec.set_defaults(func=cmd_reconcile_slips)


def _register_slip_audit(sub: argparse._SubParsersAction) -> None:
    p_sa = sub.add_parser(
        "slip-audit",
        help="T5 / T3 slips against the books' income",
        description="Compare the broker's T5 and T3 slips with the "
             "books' income, per account and slip box: Canadian and "
             "foreign dividends, box 18 capital-gains dividends, "
             "foreign tax, return of capital, interest. The slips are "
             "inputs/slips/slips.toml (typed from the PDFs; --template "
             "prints one) and IB's dividends reports "
             "(U*.YYYY.dividends.csv) in inputs/slips/. A slip in USD is "
             "compared in USD, with both CAD conversions (each payment "
             "at the Bank of Canada rate of its date, and the year's "
             "average rate). Lists missing payments, accounts with "
             "income and no slip, and the [[capital_gains_dividends]] "
             "entries and ROC .tt lines that bring the books to the "
             "slips. Changes nothing. Canada only. Exit 1 on a finding.")
    p_sa.add_argument("account", nargs="?", default=None,
                      metavar="ACCOUNT[,ACCOUNT...]",
                      help="Only this account, or these (one comma list; "
                           "default: every taxable account; one with "
                           "--import-cra)")
    p_sa.add_argument("--tolerance", type=_nonneg_float_arg, default=None,
                      help="Allowed difference per box in CAD, a number "
                           ">= 0 (default 1.00; a box holding converted "
                           "money also allows 0.5%% of that part)")
    p_sa.add_argument("--template", action="store_true",
                      help="Print a slips.toml to fill in (a T5 per "
                           "account and currency with income)")
    p_sa.add_argument("--import-cra", nargs="+", metavar="PDF|DIR",
                      default=None,
                      help="Read the T5 / T3 slip PDFs downloaded from "
                           "CRA My Account (a folder: its *.pdf) into "
                           "[[slip]] tables, each placed in an account "
                           "by the books; shows them (ACCOUNT: where the "
                           "slips no broker matches go)")
    p_sa.add_argument("--write", action="store_true",
                      help="With --import-cra: append the tables to "
                           "inputs/slips/slips.toml")
    p_sa.add_argument("--json", action="store_true",
                      help="Emit the audit as JSON instead of text (a "
                           "stable schema: docs/settings.md)")
    p_sa.set_defaults(func=cmd_slip_audit, account_list=True)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_reconcile_slips(sub)
    _register_slip_audit(sub)
