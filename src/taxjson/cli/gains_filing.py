"""The filing figures sale by sale: gains-base (of the Row listings) and
gains-cra (of Before you file). Both read the books `tjs sum` and `tjs
form-export` read and show each disposition in the filed form's cells
for that one sale (lib/tax_forms `disposition_cells`, `form8949_row`),
so a sale has one set of cents in every report, and their totals are
`tjs sum`'s RETURN row (cli/estimate `_return_figures`)."""
import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.project.accounts import account_of, accounts_of
from taxjson.project.settings import settings_of
from taxjson.lib.cli_diag import note
from taxjson.lib.country import is_usa
from taxjson.lib.in_kind import in_kind_unbooked
from taxjson.lib.out import Doc
from taxjson.lib import out as _out
from taxjson.model import money as M

from taxjson.cli.common import (
    _base_currency, _gains_row_date, _ISO_DATE_RE, _load_json_or_die,
    _refuse_books_older_than_inputs, _settle_basis, _unanswered_covers,
    _uncovered_sales, _view_msg, _view_window, _warn_accounts_without_books,
    _warn_artifact_year, _warn_run_state, _work_read_why,
    _PERIOD_HELP, _add_instrument_filters,
)
from taxjson.cli.file import _taxable_gains_argv
from taxjson.cli.listings import _instrument_filter
from taxjson.pipeline.config import load_config, load_settings
from taxjson.pipeline.inputs import _base
from taxjson.pipeline.messages import _cmd_prog, _details, _die, _die_input
from taxjson.pipeline.sanity import _json_out
from taxjson.pipeline.stages import _tax_date_basis


def _gains_base_filing(root: Path, files: Dict[str, Path], keep: Any, sym_filter: Any,
                       account: str, taxable_of: Dict[str, bool]
                       ) -> Tuple[Optional[Dict[str, Any]], str]:
    """`tjs sum --json`'s filing block for these books (cli/estimate
    `_return_figures`, the one computation: its `totals`, `lines` or
    `parts_8949`, `engine_gain_unrounded`, `engine_denied_unrounded`,
    `date_basis`) and the command that prints it; or None and what
    stands there instead, when the window is not the project's tax year,
    an instrument filter narrows it or no account shown is taxable."""
    from taxjson.cli.estimate import _return_figures
    from taxjson.project.settings import settings_of
    if sym_filter is not None or not (root / "taxjson.toml").is_file():
        return None, ("For the return: tjs sum, the tax year's sales of every instrument in "
                      "the taxable accounts; this view shows fewer.")
    cfg = load_config(root)
    st = settings_of(cfg)
    if not st.year or getattr(keep, "tax_year", None) != str(st.year):
        return None, (f"For the return: tjs sum, the {st.year} tax year's sales in the "
                      f"taxable accounts; this window is another.")
    if not any(taxable_of.values()):
        return None, (f"Not on the return: {', '.join(files)} "
                      f"{'is' if len(files) == 1 else 'are'} sheltered.")
    accts = accounts_of(cfg)
    taxable = {n for n, ok in taxable_of.items() if ok}
    (_y, is_us, date_key, _rows, line_rows, _6781, filing_total, engine_gain, _gap,
     engine_denied, _dgap, _dl) = _return_figures(files, root, cfg, accts, taxable, {},
                                                  False, st.year)
    return {"command": "tjs sum" + (f" {account}" if account else ""),
            "totals": filing_total, ("parts_8949" if is_us else "lines"): line_rows,
            "engine_gain_unrounded": engine_gain,
            "engine_denied_unrounded": engine_denied, "date_basis": date_key}, ""


def cmd_gains_base(args: argparse.Namespace) -> None:
    """`tjs gains-base`: the window's dispositions in the base currency,
    the filing figures (lib/gains_base): each account's wash-adjusted
    base book (resolve_gains_files: `_gains_wash.json`, else
    `_gains.json`; never the raw ones), each row the filed form's cells
    for that sale (Canada: Schedule 3's, as `tjs gains-cra`; US: Form
    8949's), the totals per account, taxable and sheltered apart, and
    THE RETURN, `tjs sum`'s RETURN row. Books older than the inputs are
    refused as `tjs sum` refuses them."""
    from taxjson.cli.estimate import _refuse_sum_books
    from taxjson.cli.common import _warn_run_state
    from taxjson.lib import gains_base as _GB
    from taxjson.lib.json_input import gains_row_kind
    from taxjson.lib.report_model import gains_basis_label, resolve_gains_files
    from taxjson.lib.tax_forms import DIGITAL_ASSET_BOXES_FROM
    from taxjson.pipeline.stages import _tax_date_basis
    from taxjson.project.accounts import account_of
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, scope, account = _view_window(args, root)
    sym_filter = _instrument_filter(args)
    files = resolve_gains_files(cache, account or None)
    if not files:
        if account:
            _die_input(f"no gains for account {account!r} in work/",
                       "Run `tjs run` first, or check the account name.")
        _die_input("no gains files in work/ — run `tjs run` first")
    _refuse_sum_books(root)
    cfg = load_config(root, missing_ok=True)
    if cfg:
        _warn_run_state(root, cfg)
    if not account:
        _warn_accounts_without_books(root, set(files), "gains-base", "books")
    accts = accounts_of(cfg)
    taxable_of = {n: (accts[n].taxable if n in accts else True) for n in files}
    settings = load_settings(root, missing_ok=True)
    us = is_usa(settings.country)
    # The date the gains files were scoped on, as tjs sum reads them.
    date_key = "date" if _tax_date_basis(settings) == "trade" else "date_settle"
    rows: List[Dict[str, Any]] = []
    manual = bad = 0
    for acct, f in files.items():
        data = _load_json_or_die(f)
        settle = _settle_basis(root, data)
        crypto = account_of(accts, acct).crypto
        listed = [(gains_row_kind(g), g) for g in data.get("transactions") or []]
        listed += [("manual", g) for g in data.get("manual_reporting_required") or []]
        for kind, g in listed:
            if kind == "income" or (sym_filter is not None
                                    and not sym_filter(g.get("symbol") or "")):
                continue
            d = _gains_row_date(g, keep, settle)
            if not _ISO_DATE_RE.match(d):
                bad += kind == "disposition"
                continue
            if not keep(d):
                continue
            if kind == "manual":
                manual += 1
                continue
            # A crypto account's rows as tjs sum reads them (tax_forms
            # mark_crypto); each row's form year is its own.
            e = {**g, "_crypto": True} if crypto else g
            rows.append(_GB.row_us(acct, d, e, taxable_of[acct],
                                   int(d[:4]) >= DIGITAL_ASSET_BOXES_FROM) if us
                        else _GB.row_ca(acct, d, e, taxable_of[acct], int(d[:4]), date_key))
    if bad:
        _view_msg("warning", f"{bad} gain row(s) had a missing/unparseable date and "
                  f"were excluded.")
    if manual:
        _view_msg("note", f"{manual} sale(s) with unknown cost are not shown",
                  "Shares bought before the data (.tt OPENING cost=unknown): their "
                  "basis is unknown; `tjs form-export` lists them for manual reporting.")
    rows.sort(key=lambda r: (r["date"], r["account"], r["symbol"], r["id"]))
    tot = _GB.totals(rows, [(n, taxable_of[n]) for n in files], us)
    filing, why = _gains_base_filing(root, files, keep, sym_filter, account, taxable_of)
    currency = _base_currency(root)
    basis = gains_basis_label(files)
    if getattr(args, "json", False):
        _json_out(_GB.json_doc(rows, tot, currency=currency, scope=scope, basis=basis,
                               filing=filing, unknown_cost=manual, us=us),
                  schema="gains-base")
        return
    _out.render(_GB.render(rows, tot, currency=currency, scope=scope, basis=basis,
                           filing=filing, why=why, prefix=len(files) != 1, us=us,
                           details=_details(args)), details=_details(args))


def _gains_cra_books(cache: Path, acct: str) -> Tuple[Dict[str, Dict[str, Any]],
                                                       Dict[str, List[Dict[str, Any]]],
                                                       Dict[str, Dict[str, Any]], str]:
    """One account's books `tjs gains-cra` reads beside its gains file,
    each by row id: the base-currency book the gains were computed from
    (the trade's currency and its dates), the native gains rows,
    the parsed native transactions (pipeline/run `_native_tx_file`), and
    why the native gains are missing ('' when read). A file that cannot
    be read is as missing: the rows say so."""
    from taxjson.lib.json_input import read_rows_doc
    from taxjson.pipeline.run import _native_tx_file

    def by_id(path: Optional[Path]) -> Optional[List[Dict[str, Any]]]:
        if path is None or not path.is_file():
            return None
        try:
            doc = read_rows_doc(path)
        except ValueError:
            return None
        rows = (doc or {}).get("transactions")
        return [t for t in rows if isinstance(t, dict)] if isinstance(rows, list) else None

    base = {str(t.get("id")): t for t in by_id(cache / f"{acct}_base.json") or []}
    raw_rows = by_id(cache / f"{acct}_raw_gains.json")
    raw: Dict[str, List[Dict[str, Any]]] = {}
    for t in raw_rows or []:
        raw.setdefault(str(t.get("id")), []).append(t)
    native = {str(t.get("id")): t for t in by_id(_native_tx_file(cache, acct)) or []}
    why = "" if raw_rows is not None else f"no readable {acct}_raw_gains.json"
    return base, raw, native, why


def _gains_cra_native(e: Dict[str, Any], books: Tuple[Any, ...], history: Any,
                      base_cur: str) -> Any:
    """lib/gains_cra's Native for one disposition: the trade's currency
    (the base book row's `trade_currency`, else its currency), the
    native gains row of the same id, direction, grant flag and units,
    else for a sale whose proceeds are its own trade's (lib/gains_cra
    own_trade: never a future's P/L or a short's cover) its parsed native
    transaction, the rate table's rate of the trade (bin/taxjson_convert_
    currency `rate_date`, its settlement date, and `get_rate_for_date`, as
    the conversion read it) with the day of the rate used."""
    from taxjson.bin.taxjson_convert_currency import get_rate_for_date, rate_date
    from taxjson.lib import gains_cra as _GC
    from taxjson.model.result import GainRow
    base, raw, native, why = books
    rid = str(e.get("id") or "")
    btx = base.get(rid)
    ntx = native.get(rid)
    cur = str((btx or {}).get("trade_currency") or (btx or {}).get("currency") or "")
    cands = [r for r in raw.get(rid, [])
             if (r.get("direction") or "LONG") == (e.get("direction") or "LONG")
             and bool(r.get("grant")) == bool(e.get("grant"))
             and M.of(GainRow(r).quantity or 0) == M.of(GainRow(e).quantity or 0)]
    row, source = None, ""
    if len(cands) == 1:
        row, source, cur = cands[0], "native gains", cur or str(cands[0].get("currency") or "")
    elif (ntx is not None and (e.get("direction") or "LONG") == "LONG"
          and not e.get("grant") and _GC.own_trade(e)):
        row = _GC.native_from_tx(ntx, e)
        source = "parsed row" if row is not None else ""
        cur = cur or str(ntx.get("currency") or "")
    if row is not None and e.get("_crypto"):
        row = dict(row, _crypto=True)
    if row is not None:
        why = ""
    elif btx is None and ntx is None and rid not in raw:
        why = "no row of this id in its books"
    elif len(cands) > 1:
        why = "more than one native gains row of this sale"
    elif not why:
        why = "no native row of this sale (its id and units)"
    when = rate_date(btx if btx is not None else e)
    rate = None
    used: Dict[str, str] = {}
    if cur and cur != base_cur and history is not None:
        rate = get_rate_for_date(cur, when, history, None, rate_evidence=used)
    return _GC.Native(currency=cur, row=row, source=source, why=why, rate=rate,
                      rate_date=when,
                      used_date=(used.get("fx_date") or "") if rate is not None else "")


def cmd_gains_cra(args: argparse.Namespace) -> None:
    """`tjs gains-cra`: the tax year's dispositions of the taxable
    accounts, one row per sale in the CRA's column layout (lib/gains_cra:
    Schedule 3's cells per disposition, the native proceeds and FX), then
    Schedule 3's line totals (`tjs form-export`'s, `tjs sum`'s FOR THE
    RETURN rows); Canada only (lib/country COMMAND_COUNTRY). Stale books
    are refused as form-export refuses them."""
    from taxjson.bin import taxjson_form_export as _FE
    from taxjson.lib import gains_cra as _GC
    from taxjson.lib import readiness as _RD
    from taxjson.lib.exchange_rates import load_exchange_rates
    from taxjson.lib.json_contract import versioned
    from taxjson.lib.json_input import dump_filing_json
    from taxjson.lib.tax_forms import mark_crypto

    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = settings_of(cfg)
    year = settings.year
    base_cur = _base(settings)
    accts = accounts_of(cfg)
    want = getattr(args, "account", None)
    if want and want in accts and not accts[want].taxable:
        _die(f"{want!r} is not a taxable account",
             "Schedule 3 reports the taxable accounts' dispositions; `tjs gains-base "
             f"{want}` lists its rows.")
    gains_argv = _taxable_gains_argv(root, cache, required=True)
    _refuse_books_older_than_inputs(root, cfg, read_upto=year)
    files = {Path(f).name.rsplit("_gains", 1)[0]: Path(f)
             for f in gains_argv[1::2]}
    if want:
        files = {n: p for n, p in files.items() if n == want}
        if not files:
            _die_input(f"no gains for account {want!r} in work/",
                       "Run `tjs run` first, or check the account name.")
    _warn_artifact_year(files, year)
    state_probs = _warn_run_state(root, cfg)
    date_key = "date" if _tax_date_basis(settings) == "trade" else "date_settle"
    rates_path = cache / "to_base.csv"
    try:
        history = (load_exchange_rates(rates_path, target_curr=base_cur)
                   if rates_path.is_file() else None)
    except ValueError:
        history = None
    entries: List[Dict[str, Any]] = []
    natives: List[Any] = []
    manual: List[Dict[str, Any]] = []
    tainted = 0
    for n, p in sorted(files.items()):
        try:
            ents, skipped = _FE.load_dispositions([p], year, date_key)
            manual += _FE.load_manual_rows([p], year, date_key)
        except (OSError, ValueError) as e:
            _die_input(f"{n}: could not read dispositions from {_work_read_why(p, e, root)}",
                       "Re-run `tjs run` to rebuild it.")
        if account_of(accts, n).crypto:
            ents = mark_crypto(ents)
        tainted += skipped
        books = _gains_cra_books(cache, n)
        natives += [_gains_cra_native(e, books, history, base_cur) for e in ents]
        entries += ents
    manual = [dict(m, cover_cost=M.shown(_FE.manual_figures(m)[1], 2),
                   short_sale_proceeds=None) if _FE.is_short_cover(m) else m
              for m in manual]
    doc = _GC.report(entries, natives, year=year, date_key=date_key, base=base_cur,
                     account=want or None)
    _mp, _mc = _FE.manual_totals(manual)
    doc.update(manual_reporting_required=manual, manual_proceeds=M.shown(_mp, 2),
               manual_cover_cost=M.shown(_mc, 2))
    _ynote = _FE.year_not_ended_note(year)
    if _ynote:
        doc["year_not_ended"] = _ynote
    # The sales not in the rows, of the accounts shown (ACCOUNT's alone).
    taxable = {want} if want else {n for n, c in accts.items() if c.taxable}
    ik = len([m for m in in_kind_unbooked(cache, year) if m.get("taxable") in taxable])
    np_ = len([r for r in _uncovered_sales(root, cfg, want or None)
               if r.get("booked") == "open"])
    ub = len(_unanswered_covers(root, cfg, want or None))
    if getattr(args, "csv", None):
        _gains_cra_csv(doc, Path(args.csv))
    if getattr(args, "json", False):
        paths = list(files.values())
        doc["readiness"] = _RD.build(
            run_state=state_probs or (), unknown_cost=max(tainted, len(manual)),
            no_purchase=np_, unbacked_cover=ub,
            conservation=_RD.conservation_lines(paths), in_kind_unbooked=ik,
            unconsumed_premium=_RD.unconsumed_premium_lines(paths),
            unsettled_compensation=_RD.unsettled_compensation_lines(paths))
        dump_filing_json(versioned(doc, "gains-cra"), sys.stdout)
        Doc(exact=True, width_=0, live=True).blank()
        return
    details = _details(args)
    tail = _FE._not_in_rows({"not_in_rows": {"in_kind_unbooked": ik, "no_purchase": np_,
                                              "unbacked_cover": ub}})
    if _ynote:
        tail.append(_ynote[len("NOTE: "):] if _ynote.startswith("NOTE: ") else _ynote)
    lines = _FE._manual_console(manual, base_cur, details) if manual else []
    while lines and not lines[-1]:
        lines.pop()
    from taxjson.lib import out as _out
    _out.render(_GC.render(doc, details=details, width=_out.width(), tail=tail,
                           manual=lines), details=details)


def _gains_cra_csv(doc: Dict[str, Any], path: Path) -> None:
    """`--csv FILE`: lib/gains_cra csv_rows written through a new temp
    file beside it and replaced (lib/safe_write atomic_open), each text
    cell safe in a spreadsheet, as form-export writes its CSV; a path
    that cannot be written (a folder, a missing folder) exits 1, as
    form-export's --csv does, naming the path and why."""
    import csv
    from taxjson.bin.taxjson_form_export import spreadsheet_cell
    from taxjson.lib import gains_cra as _GC
    from taxjson.lib.safe_write import atomic_open
    if path.is_dir():
        _die(f"cannot write --csv {path}: it is a folder",
             f"Name a file: `tjs gains-cra --csv {path / 'gains-cra.csv'}`.")
    try:
        with atomic_open(path, newline="") as f:
            w = csv.writer(f)
            for row in _GC.csv_rows(doc):
                w.writerow([spreadsheet_cell(c) for c in row])
    except OSError as e:
        _die(f"cannot write --csv {path}: {e.strerror or e}")
    note(_cmd_prog(), f"wrote {path}")


def _register_gains_base(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "gains-base",
        help="Realized gains in the base currency, as filed",
        description="Realized gains in the BASE currency, the filing figures: "
                    "each account's wash-adjusted books after ticker.map's "
                    "consolidation and the conversion (the books `tjs sum` "
                    "and `tjs form-export` read), one row per disposition "
                    "in the filed form's cells for that sale (Canada: "
                    "Schedule 3's, as `tjs gains-cra` shows them, with the "
                    "gain before any superficial-loss denial, the denied "
                    "amount and the allowed gain; US: Form 8949's), over a "
                    "window (default: the tax year); the totals per account, "
                    "then THE RETURN, `tjs sum`'s RETURN row.")
    p.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p.add_argument("account", nargs="?", help="Account (default: all)")
    _add_instrument_filters(p)
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of text")
    p.set_defaults(func=cmd_gains_base)


def _register_gains_cra(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "gains-cra",
        help="One row per sale in the CRA's columns",
        description="The tax year's dispositions of the taxable accounts, one "
             "row per sale: Sale date, Settle date, Description, Qty, the "
             "proceeds in the trade's currency and the Bank of Canada rate "
             "(FX), then Schedule 3's Proceeds, ACB, Commissions and "
             "Gain/Loss in CAD for that one sale; a table per currency, then "
             "the Schedule 3 line totals, the figures `tjs form-export` and "
             "`tjs sum` show. Canada only.")
    p.add_argument("account", nargs="?",
                   help="One taxable account (default: every taxable account)")
    p.add_argument("--csv", metavar="FILE",
                   help="Also write the rows as CSV (a currency column, raw numbers)")
    p.add_argument("--json", action="store_true",
                   help="Emit the report as JSON instead of text")
    p.set_defaults(func=cmd_gains_cra)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_gains_base(sub)
    _register_gains_cra(sub)
