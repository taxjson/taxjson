"""`tjs sum` and `tjs estimate`."""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from taxjson.project.accounts import account_of, accounts_of
from taxjson.project.settings import Settings, settings_of
from taxjson.lib import project_layout as _PL
from taxjson.model.result import GainRow
from taxjson.model import money as M
from taxjson.lib.in_kind import in_kind_unbooked
from taxjson.lib.out import Doc
from taxjson.lib.report_fmt import fmt_money, fmt_qty

from taxjson.cli.common import (
    _refuse_books_older_than_inputs, _refuse_cash_older_than_inputs,
    _refuse_other_country_books, _rerun_hint,
    _base_currency, _brief, _carry_back, CarryBack, _claims_back, _stale_opening,
    _typed_opening,
    LedgerUnavailable, _unanswered_covers, _uncovered_sales,
    _warn_artifact_year, _warn_run_state, _work_read_why,
    _OTHER_LOSSES_HELP, _add_deduction_flags,
)
from taxjson.cli.summaries import (
    _own_filed_lock,
    _box18_into_estimate, _carry_inputs, _estimate_deductions,
    _estimate_inputs, _estimate_lt_losses, _fold_estimate_income,
    _instalment_config, _instalments_doc, _issuer_facts,
    _section_1256_of_docs, _tax_estimate_result, _withholding_of_year,
)
from taxjson.pipeline import run_state
from taxjson.pipeline.config import _country, _no_project_message, load_config, load_settings
from taxjson.pipeline.diagnostics import _count_noun
from taxjson.pipeline.messages import (
    _cmd_prog, _current_cmd, _details, _die, _die_input,
)
from taxjson.pipeline.run import (
    _fx_cash_doc, _fx_cash_ledger_choice, _fx_cash_v2_doc,
)
from taxjson.pipeline.sanity import _json_out, _print_report_table
from taxjson.pipeline.stages import (
    _read_work_doc, _tax_date_basis,
)


def in_kind_fix_line(m: Dict[str, Any], currency: str,
                     inputs: str = "inputs") -> str:
    """What values an unbooked move (a work/in_kind.json move dict), as
    the run's offline refusal says it: the .tt INKIND line, its quantity
    signed as the taxable account's leg (`inputs`: the exports folder
    as shown)."""
    leg = m.get("taxable_leg") or []
    try:
        q = float(leg[3]) if len(leg) == 4 else float(m.get("quantity"))
    except (TypeError, ValueError):
        q = 0.0
    a, b = m.get("taxable") or "?", m.get("registered") or "?"
    kind = str(m.get("kind") or "move")
    pair = f"{a} → {b}" if kind == "contribution" else f"{b} → {a}"
    return (f"{kind} {pair}: {abs(q):g} {m.get('symbol', '?')} on "
            f"{m.get('date', '?')} — add to a .tt file in {inputs}/{a}/: "
            f"INKIND {m.get('date', '?')} {m.get('symbol', '?')} {q:g} "
            f"{m.get('currency') or currency} <price per share>")


def _section_1256_gain(files: Dict[str, Path], accounts, year,
                       settings: Settings) -> Tuple[float, float]:
    """US only: (the year's realized P/L of §1256 contracts — futures,
    futures options, broad-based index options, lib/futures.
    section_1256_kind — in the taxable accounts' gains files, the part
    of it the engine termed long-term). The estimate takes the P/L out
    of the term the engine gave it (a long-term part out of the
    long-term gains, the rest out of the short-term ones, where a gain
    with no term goes) and splits it as Form 6781 does, 40% short-term
    and 60% long-term (US-FUT-03; backlog TJ-0466, A2-1124)."""
    docs = []
    for acct in sorted(accounts):
        p = files.get(acct)
        if p is None:
            continue
        try:
            docs.append(_read_work_doc(p))
        except (OSError, ValueError):
            continue                # the account loop already refused it
    return _section_1256_of_docs(docs, year, settings)


def _report_aggregate_usable(gains: Any) -> bool:
    """Is a work/<acct>_report.json `gains` object the complete
    summarize_gains aggregate `sum` reads (its ticker_stats and
    total_fees objects)? A truncated or hand-edited one (`{}`) passed
    the fast path as "no gains" and put REALIZED 0.00 beside a nonzero
    FOR THE RETURN total in the same `sum --json` (2026-10-24 audit
    D07); `sum` recomputes from the gains file instead."""
    return (isinstance(gains, dict)
            and isinstance(gains.get("ticker_stats"), dict)
            and isinstance(gains.get("total_fees"), dict))


def _refuse_sum_books(root: Path) -> None:
    """Exit 1 before `tjs sum` (and `tjs estimate`) prints anything from
    books a full `tjs run` did not build from the inputs as they are now,
    as close-year refuses them (cli/common `_refuse_books_older_than_inputs`),
    and, where the FOR THE RETURN block's line 15300 reads the ledger v2,
    from its cash events missing or not the run's. Owner decision
    2026-11-18: refuse, as the filing commands do (backlog TJ-4347; before,
    a banner and the figures, the line-15300 estimate omitted). Without
    taxjson.toml the command says what it needs."""
    if not _PL.has_config(root):
        return
    cfg = load_config(root)
    _refuse_other_country_books(root, cfg)     # (the more telling reason)
    st = settings_of(cfg)
    try:
        year: Optional[int] = int(st.year)
    except (TypeError, ValueError):
        year = None
    _refuse_books_older_than_inputs(root, cfg, read_upto=year)
    try:
        v2 = _fx_cash_ledger_choice(st) == "v2"
    except SystemExit:
        v2 = False          # (the line says what is wrong with the choice)
    if v2 and _country(st) not in ("us", "usa"):
        _refuse_cash_older_than_inputs(root, cfg)


def _sum_inputs(args: argparse.Namespace) -> Tuple[Any, ...]:
    """`tjs sum`'s inputs, before any output: the gains files (exit 2
    when there are none; 1 with no taxjson.toml either: not a project),
    the estimate's inputs and the config, the province checked, the
    books refused when a full `tjs run` did not build them (TJ-4347),
    then the warnings about the flags, the artifacts' year and the run
    state, in that order (stderr)."""
    from taxjson.lib import out as _out
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    # Canonical per-account gains, wash-adjusted where the pipeline built
    # the cross-account pass — the same basis carryover/t1135/form-export
    # report, so this summary can't disagree with the filing commands.
    files = resolve_gains_files(cache, getattr(args, "account", None)
                                or None)
    if not files:
        if getattr(args, "account", None):
            _die_input(f"no gains for account {args.account!r} in work/",
                       "Run `tjs run` first, or check the account "
                       "name.")
        if not _PL.has_config(root):
            _die(*_no_project_message(root, _current_cmd()))
        _die_input("no gains files in work/", "Run `tjs run` first.")
    basis = gains_basis_label(files)

    # --other-income/--other-losses turn on the marginal tax estimate,
    # which needs the config (country/account types) and only counts
    # TAXABLE accounts' income.
    want_estimate = (getattr(args, "other_income", None) is not None
                     or getattr(args, "other_losses", None) is not None
                     or getattr(args, "deductions", None) is not None
                     or getattr(args, "carrying_charges", None) is not None
                     or getattr(args, "long_term_losses", None) is not None
                     or getattr(args, "estimate", False))
    _oi, _ol = _estimate_inputs(root, args)
    _ded, _cc = _estimate_deductions(root, args)
    _ltl = _estimate_lt_losses(root, args)
    _foreign_by_acct: Dict[str, float] = {}
    # Each security's foreign dividends too: the 15% limit on its
    # withholding is its own (backlog TJ-0638).
    _foreign_by_sec: Dict[str, Dict[str, float]] = {}
    cfg = load_config(root) if _PL.has_config(root) else {}
    _accts = accounts_of(cfg)
    _st = settings_of(cfg)
    _amt_in: Optional[Dict[int, float]] = None
    _carry_src: Dict[str, Any] = {}
    if want_estimate and cfg:
        _ol, _ltl, _amt_in, _carry_src = _carry_inputs(root, args, cfg,
                                                       _ol, _ltl)
        _carry_src["other_income_value"] = _oi
    taxable_accounts = {n for n, c in _accts.items()
                        if c.taxable}
    est = dict(realized=0.0, st=0.0, lt=0.0, div_ca=0.0,
               div_foreign=0.0, pil=0.0, staking=0.0)
    if want_estimate and not cfg:
        _die("the tax estimate needs taxjson.toml",
             "It reads the country and the account types from it.")
    _prog = f"tjs {_current_cmd() or 'sum'}"
    if want_estimate:
        # --other-income/--other-losses were checked by _estimate_inputs
        # above (one guard, A2-1123).
        if _country(_st) == "canada" \
                and not getattr(args, "federal_only", False):
            # Validate the province BEFORE printing anything: a missing
            # or unsupported one used to fail only after the whole sum
            # table had scrolled past (2026-09 CLI audit B20).
            from taxjson.lib import tax_estimate as _te
            _prov = (getattr(args, "province", None)
                     or str(_st.province or "")).strip()
            _te.apply_vintage(_st.year)
            if not _prov:
                _die("the canada estimate needs a province",
                     "Pass --province ON|BC|AB or set `province` under "
                     "[settings] in taxjson.toml.")
            if _prov.upper() not in _te.CA_PROVINCES:
                _die(f"unsupported province {_prov!r} for the estimate",
                     f"Supported: "
                     f"{', '.join(sorted(_te.CA_PROVINCES))}.")
    _refuse_sum_books(root)     # the flags checked; before any output (TJ-4347)
    if getattr(args, "province", None) and not want_estimate:
        _out.warn("--province is ignored without the tax estimate",
                  prog=_prog,
                  details=["Add --other-income or --other-losses, or use "
                           "`tjs estimate`."])
    _run_state: List[str] = []
    if cfg:
        _warn_artifact_year(files, _st.year)
        _run_state = _warn_run_state(root, cfg)
    return (root, cache, files, basis, want_estimate, _oi, _ol, _ded, _cc, _ltl,
            _foreign_by_acct, _foreign_by_sec, cfg, _accts, _st, _amt_in, _carry_src,
            taxable_accounts, est, _prog, _run_state)


def _account_aggregate(acct: str, p: Path, root: Path, taxable_accounts: Any,
                       acct_types: Dict[str, str], year: Any,
                       _prog: str) -> Optional[Tuple[Dict[str, Any], Any, Any]]:
    """One account's gains aggregate for `tjs sum`: (the summarize_gains
    aggregate, the gains file's document when it was read, the year so
    far). The report twin (work/<acct>_report.json) when it is usable
    and on the gains file's basis, else the gains file; None when that
    cannot be read and the account is neither taxable nor configured
    (warned; a taxable or configured one exits 2)."""
    from taxjson.lib.gain_summary import summarize_gains
    from taxjson.lib import out as _out
    res = None
    data = None
    report_p = p.with_name(f"{acct}_report.json")
    if report_p.exists() and report_p.stat().st_mtime >= p.stat().st_mtime:
        # Freshness alone is not enough: after `run --account X` the
        # report is freshly rebuilt from PRE-wash gains (the wash
        # pass is skipped under --account) while resolve_gains_files
        # still returns the older _gains_wash.json — serving it
        # would print pre-wash aggregates under a "wash-adjusted"
        # banner. Require the recorded basis to match the resolved
        # file; reports without one (older projects) fall through to
        # reading the gains file itself.
        p_basis = ("wash-adjusted" if p.name.endswith("_gains_wash.json")
                   else "pre-wash")
        try:
            rep = _read_work_doc(report_p)
            if (rep.get("schema_version") == 1 and "gains" in rep
                    and rep.get("basis") == p_basis
                    and _report_aggregate_usable(rep.get("gains"))):
                res = rep["gains"]
                year = year or rep.get("year")
            elif (rep.get("schema_version") == 1 and "gains" in rep
                    and rep.get("basis") == p_basis):
                # The twin is there but its aggregate is not the
                # one the run writes (damaged, truncated, edited):
                # served, it put REALIZED 0.00 beside the FOR THE
                # RETURN total computed from the gains file (D07).
                # The gains file is read instead, and said.
                _out.warn(f"{_out.relpath(report_p, root)} holds no "
                          f"usable gains aggregate — {acct}'s row is "
                          f"recomputed from {p.name}", prog=_prog,
                          details=["A full `tjs run` rewrites "
                                   "the report file."])
            elif (rep.get("basis") and rep.get("basis") != p_basis):
                # The account was rebuilt on a DIFFERENT basis than
                # the resolved gains file (run --account skips the
                # wash pass). We fall back to the older wash file
                # for label consistency — but say so, or the
                # staleness is invisible.
                _brief1 = _brief(sys.stderr)
                _out.note(f"{acct}: serving the previous {p_basis} "
                          f"numbers"
                          + (" — run a full `tjs run`" if _brief1
                             else ""),
                          prog=_prog,
                          details=[] if _brief1 else [
                              f"The latest per-account rebuild is "
                              f"{rep.get('basis')} only. Run a full "
                              f"`tjs run` to refresh the "
                              f"wash-adjusted aggregates."])
        except (OSError, ValueError):
            res = None
    if res is None:
        try:
            data = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # The file named once, work/<file> (its absolute path
            # came second, in the reader's message), and exit 2: an
            # input that cannot be read, not the 1 of a finding
            # (docs/commands.md, Exit codes; backlog TJ-0641).
            _why = _work_read_why(p, e, root)
            if acct in taxable_accounts:
                # A taxable account's books left out of the filing
                # block, the estimate and instalments with rc 0
                # (R1-277, S004-09): refuse instead.
                _die_input(f"could not read {_why}", _rerun_hint(
                    _why, "A taxable account's gains cannot be left out of the totals."))
            if acct in acct_types:
                # A configured sheltered account dropped out of the
                # SHELTERED / ALL ACCOUNTS tables with rc 0 and a
                # stderr line, changing their totals (A2-1119).
                _die_input(f"could not read {_why}", _rerun_hint(
                    _why, f"Account {acct!r} ({acct_types[acct] or 'untyped'}) would be left "
                    f"out of the account tables and the ALL ACCOUNTS total."))
            _out.warn(f"could not read {_why}", prog=_prog)
            return None
        year = year or (data.get("summary") or {}).get("year")
        res = summarize_gains(data)
    return res, data, year


def _account_rows(files: Dict[str, Path], root: Path, cache: Path, cfg: Dict[str, Any],
                  _accts: Any, _st: Settings, taxable_accounts: Any, est: Dict[str, float],
                  _foreign_by_acct: Dict[str, float],
                  _foreign_by_sec: Dict[str, Dict[str, float]], want_estimate: bool,
                  _prog: str) -> Tuple[List[Dict[str, Any]], int, int, Any]:
    """`tjs sum`'s account rows, one per gains file (shown figures, each
    rounded once), the counts of sales with an unknown cost (in the
    totals, routed out of them) and the year; the estimate's income
    folded into `est` and the foreign-income maps as it goes, then the
    T5 box 18 gains and (US) the section 1256 gain."""
    acct_types = {n: str(c.type or "")
                  for n, c in _accts.items()}
    acct_rows: List[Dict[str, Any]] = []
    tainted_included = 0
    tainted_routed = 0
    year = None
    for acct, p in files.items():
        # Prefer the machine twin the pipeline wrote (work/<acct>_report.json)
        # when it is at least as fresh as the gains file — same aggregates,
        # no recompute. Falls back to summarize_gains for older projects.
        got = _account_aggregate(acct, p, root, taxable_accounts,
                                 acct_types, year, _prog)
        if got is None:
            continue
        res, data, year = got
        cap = opt = div = pil = 0.0
        for cur_map in res.get("ticker_stats", {}).values():
            for s in cur_map.values():
                cap += float(s.get("cap", 0) or 0)
                opt += float(s.get("opt", 0) or 0)
                div += float(s.get("div", 0) or 0)
                pil += float(s.get("pil", 0) or 0)
        if acct in taxable_accounts:
            _issuer = (_issuer_facts(cache, acct) if want_estimate
                       else {})
            _rows = None
            if _issuer.get("by_symbol"):
                # Each dividend by its own issuer's country (backlog
                # TJ-0726): the gains rows the aggregate summarizes.
                if data is None:
                    try:
                        data = _read_work_doc(p)
                    except (OSError, ValueError):
                        data = None
                _rows = ((data or {}).get("transactions") or []
                         if data is not None else None)
            _fold_estimate_income(
                res, acct, est, _foreign_by_acct,
                crypto=bool(account_of(_accts, acct).crypto),
                issuer=_issuer, rows=_rows,
                foreign_by_security=_foreign_by_sec)
        fees = sum(float(v) for v in (res.get("total_fees") or {}).values())
        tainted_included += int(res.get("tainted_count") or 0)
        tainted_routed += int(res.get("tainted_routed") or 0)
        # Every row foots as printed (S042-21): REALIZED is the engine's
        # gain rounded once (what the .sum and the checklist compare),
        # NON-OPT is rounded on its own, and OPTION is the cent
        # difference — rounding the three separately left rows whose
        # NON-OPT + OPTION missed REALIZED by a cent. TOTAL adds the
        # displayed REALIZED, DIVIDEND and PIL: a payment in lieu is
        # income like the .sum's GRAND TOTAL counts it (S042-22).
        _stock_r = M.shown(cap, 2)
        _real_r = M.shown(cap + opt, 2)
        _div_r, _pil_r = M.shown(div, 2), M.shown(pil, 2)
        acct_rows.append({"account": acct, "stock": _stock_r,
                          "option": M.shown(_real_r - _stock_r, 2) + 0.0,
                          "realized": _real_r,
                          "dividend": _div_r,
                          "pil": _pil_r, "fees": M.shown(fees, 2),
                          "total": M.shown(_real_r + _div_r + _pil_r, 2),
                          "type": acct_types.get(acct, "")})
        if account_of(_accts, acct).crypto:
            # Its DIVIDEND column is staking rewards (S023-11).
            acct_rows[-1]["dividend_is_staking"] = True
    if want_estimate and cfg:
        # T5 box 18 capital-gains dividends ([[capital_gains_dividends]],
        # tax-logic CA-INC-06): out of the dividends, into the gains.
        _box18_into_estimate(root, est, set(files) & taxable_accounts,
                             year or _st.year,
                             _foreign_by_acct, _foreign_by_sec)
        if _country(_st) in ("us", "usa"):
            est["s1256"], est["s1256_lt"] = _section_1256_gain(
                files, set(files) & taxable_accounts,
                year or _st.year,
                _st)
    return acct_rows, tainted_included, tainted_routed, year


def _return_figures(files: Dict[str, Path], root: Path, cfg: Dict[str, Any], _accts: Any,
                    taxable_accounts: Any, est: Dict[str, float], want_estimate: bool,
                    year: Any) -> Tuple[Any, ...]:
    """`tjs sum`'s FOR THE RETURN figures: the filing year, the country,
    the date basis, each taxable account's filing totals, the form
    lines (Canada: Schedule 3; US: Form 8949's parts, the section 1256
    contracts apart), the RETURN totals with the permanent denials,
    and the rounding gaps against the engine's unrounded totals.
    Canada with the estimate: `est["realized"]` is the RETURN row's gain.
    A taxable account's dispositions that cannot be read exit 2."""
    # What the return's capital-gains entry asks for: taxable accounts
    # only, on form-export's convention, so these can never disagree with
    # the export. Canada: one row per Schedule 3 line (shares 13199/13200,
    # options & other properties 15199/15300, crypto-assets 15200/15301).
    # USA: Form 8949's own part totals — (d) proceeds, (e) cost, (g)
    # adjustment, (h) gain.
    from taxjson.lib.tax_forms import mark_crypto
    from taxjson.bin.taxjson_form_export import (filing_6781, filing_lines, filing_parts_8949,
                                                 filing_totals, load_dispositions)
    _settings = settings_of(cfg)
    _fyear = year or _settings.year
    _is_us = _country(_settings) \
        in ("us", "usa")
    # The date the gains files were scoped on — an explicit tax_date
    # that differs from the country default dropped a year-end sale
    # from FOR THE RETURN (R1-200).
    _date_key = ("date" if _tax_date_basis(_settings) == "trade"
                 else "date_settle")
    filing_rows: List[Dict[str, Any]] = []
    _filing_ents: List[Dict[str, Any]] = []
    for acct, p in files.items():
        if acct not in taxable_accounts:
            continue
        try:
            _ents, _ = load_dispositions([p], _fyear, _date_key)
        except (OSError, ValueError) as e:
            # Named once, exit 2 (backlog TJ-0641).
            _die_input(f"{acct}: could not read dispositions from "
                       f"{_work_read_why(p, e, root)}",
                       "Re-run `tjs run` to rebuild it (the FOR THE "
                       "RETURN block cannot leave a taxable account out).")
        if account_of(_accts, acct).crypto:
            _ents = mark_crypto(_ents)
        _filing_ents += _ents
        if _is_us:
            try:
                _parts = filing_parts_8949(_ents, _fyear)
            except SystemExit as e:
                # Dropping the account (and, below, every account's
                # Part I/II lines) printed RETURN 0.00 next to a nonzero
                # realized total with rc 0, where form-export fails and
                # the Canada branch dies (A2-1120).
                _die(f"{acct}: {e}",
                     "Re-run `tjs run` to rebuild it (the FOR THE "
                     "RETURN block cannot leave a taxable account out).")
            filing_rows.append({"account": acct, **{
                k: M.shown(sum(x[k] for x in _parts), 2)
                for k in ("proceeds", "cost", "adjustment", "gain")},
                "dispositions": len(_ents)})
        else:
            filing_rows.append({"account": acct,
                                **filing_totals(_ents, _fyear,
                                                _date_key)})
    filing_line_rows: List[Dict[str, Any]] = []
    _filing_6781: Optional[Dict[str, Any]] = None
    if _is_us:
        try:
            filing_line_rows = filing_parts_8949(_filing_ents,
                                                 _fyear)
            # §1256 contracts stay off Form 8949 (Form 6781 by hand,
            # US-FUT-02 / US-OPT-04; A2-0324): shown beside the block.
            _filing_6781 = filing_6781(_filing_ents)
        except SystemExit:
            filing_line_rows = []           # warned per account above
        _fkeys = ("proceeds", "cost", "adjustment", "gain")
    else:
        filing_line_rows = filing_lines(_filing_ents, _fyear, _date_key)
        _fkeys = ("proceeds", "acb", "outlays", "gain", "denied")
    filing_total = {k: M.shown(sum(r[k] for r in filing_line_rows), 2)
                    for k in _fkeys}
    # The part of the denial that no replacement's basis/ACB ever
    # recovers: Canada, a registered-account (or affiliated)
    # acquisition (s.40(2)(g)(i)); USA, a repurchase in an IRA (US-
    # WASH-11). The footer said every denied amount moves to the
    # replacement (S043-02, S048-04; the US twin A2-0647).
    from taxjson.lib.futures import section_1256_kind as _s1256k
    _views_8949 = [GainRow(e) for e in _filing_ents
                   if not (_is_us and _s1256k(str(e.get("symbol") or "")))]
    filing_total["permanently_denied"] = M.shown(sum(
        float(v.permanently_disallowed or 0.0)
        for v in _views_8949), 2)
    if not _is_us:
        # An in-kind contribution's loss, nil by s.40(2)(g)(iv) — its own
        # figure, never in DENIED (CA-INKIND-03).
        filing_total["denied_contribution"] = M.shown(sum(
            float(v.denied_contribution or 0.0)
            for v in _views_8949), 2)
    if _filing_6781 is not None:
        filing_total["section_1256_gain"] = _filing_6781["gain"]
    # The RETURN row sums the per-row cents, as filed; the gains files,
    # wash-sales and audit total the unrounded engine values — a few
    # cents apart on a large year (R1-166). Shown, not hidden. (US: the
    # §1256 contracts are not on the RETURN row, so not in this either.)
    _engine_gain = M.shown(sum(float(v.gain or 0.0)
                             for v in _views_8949), 2)
    _round_gap = (M.shown(filing_total.get("gain", 0.0) - _engine_gain, 2)
                  if filing_line_rows else 0.0)
    if want_estimate and not _is_us and filing_line_rows:
        # Canada: the estimate taxes the gain the user files — the
        # RETURN row's per-row cents — not the unrounded engine total
        # summed from the account tables, a cent or so off the RETURN
        # row it printed under.
        est["realized"] = filing_total["gain"]
    # The DENIED column (US: the code-W adjustment) has the same per-row
    # rounding gap; R1-166's headline mismatch was a denied total, and
    # form-export already names it (A2-0912).
    _denied_key = "adjustment" if _is_us else "denied"
    _engine_denied = M.shown(sum(
        d for d in (float(v.disallowed_amount or 0.0) for v in _views_8949)
        # Form 8949 adds back only a positive disallowance (code W).
        if not _is_us or d > 1e-9),
        2)
    _denied_gap = (M.shown(filing_total.get(_denied_key, 0.0)
                         - _engine_denied, 2)
                   if filing_line_rows else 0.0)
    _denied_label = "adjustment (g)" if _is_us else "denied"
    return (_fyear, _is_us, _date_key, filing_rows, filing_line_rows, _filing_6781,
            filing_total, _engine_gain, _round_gap, _engine_denied, _denied_gap, _denied_label)


def _fx_for_sum(root: Path, cache: Path, filing_rows: List[Dict[str, Any]], _is_us: bool,
                _full_msgs: bool, _acts: List[str], _prog: str) -> Optional[Dict[str, Any]]:
    """Canada: the FX-on-cash status `sum` shows beside line 15300
    (`_fx_cash_status`, its stderr held), None when the ledger refused;
    a refusal or a warning is an `! ` line in `_acts`, or with
    `_full_msgs` a warning on stderr."""
    from taxjson.lib import out as _out
    # FX on foreign cash (s.39(1.1)) is reported on line 15300 too
    # (T4037) but lives outside the engine's dispositions. The default
    # ledger (v1) is NOT RELIABLE and its figure is never shown as
    # reportable (CA-FX-07); the opt-in v2 says what it computed or why
    # it refused (_fx_cash_status).
    _fx_note: Optional[Dict[str, Any]] = None
    _fx_err = ""
    if filing_rows and not _is_us:
        import contextlib as _ctx
        import io as _io
        _fx_buf = _io.StringIO()
        try:
            with _ctx.redirect_stderr(_fx_buf):
                _fx_note = _fx_cash_status(root, cache)
            _fx_note = dict(_fx_note, line="15300")
        except SystemExit as e:
            # The ledger refused (an unreadable or missing native tx
            # file): say so instead of a silent pointer (S005-04).
            _fx_note, _fx_err = None, str(e.code or "")
        except Exception as e:                      # noqa: BLE001
            _fx_note, _fx_err = None, f"{type(e).__name__}: {e}"
        _fx_warn = [ln for ln in _fx_buf.getvalue().splitlines()
                    if "warning" in ln.lower()]
        if (_fx_err or _fx_warn) and not _full_msgs:
            _acts.append(_out.act(
                "FX-on-cash (line 15300) estimate "
                + ("omitted" if _fx_err else "built with warnings"),
                "tjs fx-cash"))
        elif _fx_err or _fx_warn:
            _out.warn("FX-on-cash (line 15300) estimate "
                      + ("omitted" if _fx_err else
                         "built with warnings"), prog=_prog,
                      details=([_fx_err] if _fx_err else
                               [ln.strip() for ln in _fx_warn]))
    return _fx_note


def _book_checks(files: Dict[str, Path], taxable_accounts: Any, tainted_included: int,
                 tainted_routed: int, _full_msgs: bool, _acts: List[str],
                 _prog: str) -> Tuple[List[str], List[str], List[str]]:
    """`tjs sum`'s checks of the books: the engine's conservation lines,
    an assignment's premium in no gain, dividend compensation still
    inside its 45 days, and the sales with an unknown cost; each an `! `
    line in `_acts`, or with `_full_msgs` a warning on stderr. Returns
    the first three's lines (the JSON's readiness)."""
    from taxjson.lib import out as _out
    # The engine's conservation check (a cost or quantity the books lost
    # track of) reached only each account's .sum DIAGNOSTICS: said here
    # too, and carried in --json's readiness (2026-10 critique).
    from taxjson.lib import readiness as _RD
    _conservation = _RD.conservation_lines(files.values())
    if _conservation and not _full_msgs:
        _acts.append(_out.act(
            f"{_count_noun(len(_conservation), 'conservation problem')} in "
            f"the books: a cost or quantity the totals do not account for",
            "tjs audit"))
    elif _conservation:
        _out.warn(f"{_count_noun(len(_conservation), 'conservation problem')}"
                  f" in the books: a cost or quantity the totals do not "
                  f"account for", prog=_prog, details=_conservation)
    # An assignment's premium no stock leg took is in no gain of a
    # taxable account: the filing totals leave it out (backlog TJ-0824).
    _unconsumed = _RD.unconsumed_premium_lines(
        p for a, p in files.items() if a in taxable_accounts)
    _unc_head = ("option-assignment premium in no gain: an assignment's "
                 "stock leg is not in the books")
    if _unconsumed and not _full_msgs:
        _acts.append(_out.act(_unc_head, "tjs audit"))
    elif _unconsumed:
        _out.warn(_unc_head, prog=_prog, details=_unconsumed)
    # Dividend compensation on a short still inside its 45 days: the
    # year's income moves if it is covered in time (backlog TJ-1065).
    _unsettled_comp = _RD.unsettled_compensation_lines(
        p for a, p in files.items() if a in taxable_accounts)
    _usc_head = (f"{_count_noun(len(_unsettled_comp), 'dividend compensation payment')}"
                 f" on a short still inside its 45 days: not settled")
    if _unsettled_comp and not _full_msgs:
        _acts.append(_out.act(_usc_head, "tjs sum --details"))
    elif _unsettled_comp:
        _out.warn(_usc_head, prog=_prog, details=_unsettled_comp)
    if tainted_included and not _full_msgs:
        _acts.append(_out.act(
            f"totals include {_count_noun(tainted_included, 'sale')} with an "
            f"unknown cost; the filing totals leave them out",
            "tjs form-export"))
    elif tainted_included:
        # In-line tainted rows (raw engine output): counted in totals.
        _out.warn(f"totals include {tainted_included} disposition(s) "
                  f"with an unknown cost (no purchase in your files)",
                  prog=_prog,
                  details=["form-export and carryover exclude them, so "
                           "the filing totals will differ."])
    if tainted_routed and not _full_msgs:
        _acts.append(_out.act(
            f"{_count_noun(tainted_routed, 'sale')} with an unknown cost NOT "
            f"in these totals: report them by hand", "tjs form-export"))
    elif tainted_routed:
        # Pipeline files: tainted rows were STRIPPED to the
        # manual_reporting_required section — totals exclude them, and
        # without this line `sum` gave no signal at all (round-five
        # audit finding: the warning was dead code for pipeline
        # files).
        _out.warn(f"{tainted_routed} disposition(s) with an unknown cost "
                  f"(no purchase in your files) were routed to manual "
                  f"reporting — these totals EXCLUDE them", prog=_prog,
                  details=["`tjs form-export` lists them in its MANUAL "
                           "REPORTING section; report them by hand."])
    return _conservation, _unconsumed, _unsettled_comp


def _no_purchase_checks(args: argparse.Namespace, root: Path, cfg: Dict[str, Any], cache: Path,
                        files: Dict[str, Path], _st: Settings, _fyear: Any, base: str,
                        _full_msgs: bool, _acts: List[str],
                        _prog: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                                             List[Dict[str, Any]]]:
    """`tjs sum`'s sales with no purchase in the files (those booked as
    shorts covered later, those not in the totals) and the in-kind moves
    with a registered account not booked; each an `! ` line in `_acts`,
    or with `_full_msgs` a warning on stderr. Returns (the sales not in
    the totals, those in them, the unbooked moves)."""
    from taxjson.lib import out as _out
    # Sales with no purchase in the files that no .tt OPENING
    # cost=unknown line covers: the engine books them as an open short, so their gain is
    # simply not in these totals — and nothing above said so for a
    # Questrade / RBC / Webull sale (new-user study).
    # Only the sales the gains files lack are out of the totals
    # (first_run.engine_booking): one the engine closed as a short sale
    # within the year, or sold from a purchase it reads first, is in
    # them.
    _no_purchase = _uncovered_sales(root, cfg,
                                    getattr(args, "account", None) or None)
    _uncovered = [{k: v for k, v in r.items() if k != "booked"}
                  for r in _no_purchase if r.get("booked") == "open"]
    _in_totals = [r for r in _no_purchase if r.get("booked") != "open"]
    _covers = [r for r in _in_totals if r.get("booked") == "short_cover"]
    if _covers and not _full_msgs:
        # (Short enough to name a symbol within 100 columns: "with no
        # purchase in your files ... a later purchase closed" passed
        # them with one name, backlog TJ-0229.)
        _acts.append(_out.act_list(
            f"{_count_noun(len(_covers), 'sale')} with no purchase booked "
            f"as shorts covered later",
            [r["symbol"] for r in _covers], "tjs find-missing-history"))
    elif _covers:
        _shown_c = ", ".join(f"{r['symbol']} ({r['account']})"
                             for r in _covers[:4]) \
            + (f" +{len(_covers) - 4} more" if len(_covers) > 4 else "")
        _out.warn(f"{len(_covers)} position(s) sold in "
                  f"{_st.year} with no "
                  f"purchase in your files were booked as short sales closed "
                  f"by a later purchase (or shares moved in from another of "
                  f"your accounts): their gain is here at its cost", prog=_prog,
                  details=[f"No .tt OPENING cost=unknown line: {_shown_c}.",
                           "If you held the shares before your files "
                           "start, `tjs find-missing-history` lists "
                           "them and the fixes (docs/getting-started.md, "
                           "step 5).",
                           "A short sale still open reads the same: it is "
                           "in no total until it is covered."])
    if _uncovered and not _full_msgs:
        _acts.append(_out.act_list(
            f"{_count_noun(len(_uncovered), 'sale')} with no purchase NOT in "
            f"these totals, or a short",
            [r["symbol"] for r in _uncovered],
            "tjs find-missing-history"))
    elif _uncovered:
        _shown = ", ".join(f"{r['symbol']} ({r['account']})"
                           for r in _uncovered[:4]) \
            + (f" +{len(_uncovered) - 4} more" if len(_uncovered) > 4
               else "")
        _out.warn(f"{len(_uncovered)} position(s) sold in "
                  f"{_st.year} with no "
                  f"purchase in your files: their gain is NOT in these "
                  f"totals", prog=_prog,
                  details=[f"No .tt OPENING cost=unknown line: {_shown}.",
                           "`tjs find-missing-history` lists them and "
                           "the fixes (docs/getting-started.md, step 5)."])
    # An in-kind move to or from a registered account the run could not
    # value is NOT booked: a contribution's deemed sale (or a
    # withdrawal's purchase) is missing from the books, and only the
    # run's own warning said so (backlog TJ-0095).
    _ik_unbooked = [m for m in in_kind_unbooked(cache, _fyear)
                    if m.get("taxable") in files]
    _ik_head = (f"{_count_noun(len(_ik_unbooked), 'in-kind move')} with a "
                f"registered account NOT booked: NOT in these totals")
    if _ik_unbooked and not _full_msgs:
        _acts.append(_out.act_list(
            _ik_head, sorted({str(m.get("symbol")) for m in _ik_unbooked}),
            "tjs transfers"))
    elif _ik_unbooked:
        _ik_in = _PL.shown(_PL.inputs_dir(root), root)
        _out.warn(_ik_head, prog=_prog,
                  details=[in_kind_fix_line(m, base, _ik_in)
                           for m in _ik_unbooked]
                  + ["Then re-run `tjs run`; `tjs transfers` "
                     "lists every move (docs/tax-rules.md, In-kind "
                     "contributions and withdrawals)."])
    return _uncovered, _in_totals, _ik_unbooked


def _sum_rows(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    # Summed from the DISPLAYED (2dp-rounded) per-account figures,
    # so every table's total row exactly equals the sum of the
    # rows above it.
    s = {k: M.shown(sum(r[k] for r in rows), 2)
         for k in ("stock", "option", "realized", "dividend",
                   "pil", "fees", "total")}
    return s


@dataclass
class SumModel:
    """What `tjs sum` computed, before any output (build_sum): the
    inputs, the account rows and their groups, the FOR THE RETURN
    figures, the checks' findings (`acts`: the `! ` lines a person sees
    without --details) and the scope notes. `--json` prints it
    (_sum_json); the text and the estimate read it."""
    root: Path
    cache: Path
    files: Dict[str, Path]
    basis: str
    want_estimate: bool
    other_income: Any
    other_losses: Any
    deductions: Any
    carrying_charges: Any
    lt_losses: Any
    foreign_by_account: Dict[str, float]
    foreign_by_security: Dict[str, Dict[str, float]]
    cfg: Dict[str, Any]
    settings: Settings
    amt_carryover: Optional[Dict[int, float]]
    carry_sources: Dict[str, Any]
    taxable_accounts: Any
    est: Dict[str, float]
    run_state: List[str]
    acct_rows: List[Dict[str, Any]]
    tainted_included: int
    tainted_routed: int
    year: Any
    group_defs: List[Tuple[str, List[Dict[str, Any]]]]
    grouped: bool
    filing_year: Any
    is_us: bool
    date_key: str
    filing_rows: List[Dict[str, Any]]
    filing_line_rows: List[Dict[str, Any]]
    filing_6781: Optional[Dict[str, Any]]
    filing_total: Dict[str, float]
    engine_gain: float
    round_gap: float
    engine_denied: float
    denied_gap: float
    denied_label: str
    fx_note: Optional[Dict[str, Any]]
    acts: List[str]
    base: str
    conservation: List[str]
    unconsumed_premium: List[str]
    unsettled_compensation: List[str]
    no_purchase_uncovered: List[Dict[str, Any]]
    no_purchase_in_totals: List[Dict[str, Any]]
    in_kind_unbooked_moves: List[Dict[str, Any]]
    sheltered_included: List[str]
    filing_positions: List[Dict[str, Any]]


def build_sum(args: argparse.Namespace) -> SumModel:
    """`tjs sum`'s figures and findings: the inputs, the account rows,
    the FOR THE RETURN figures and the checks, the sum's refusals and
    stderr messages in the order the command always gave them, before
    any output (the estimate's warnings, the carry-back's and the
    withholding's, and `--json`'s unanswered covers come later, as
    before)."""
    from taxjson.lib import out as _out
    (root, cache, files, basis, want_estimate, _oi, _ol, _ded, _cc, _ltl, _foreign_by_acct,
     _foreign_by_sec, cfg, _accts, _st, _amt_in, _carry_src, taxable_accounts, est, _prog,
     _run_state) = _sum_inputs(args)
    acct_rows, tainted_included, tainted_routed, year = _account_rows(
        files, root, cache, cfg, _accts, _st, taxable_accounts, est,
        _foreign_by_acct, _foreign_by_sec, want_estimate, _prog)

    # Group by configured account type. Grouped tables only appear
    # when there is genuinely more than one group — a single-type
    # project (or one with no taxjson.toml types) keeps the plain
    # one-table layout.
    group_defs = [
        ("TAXABLE", [r for r in acct_rows if r["type"] == "taxable"]),
        ("SHELTERED", [r for r in acct_rows if r["type"] == "sheltered"]),
        ("UNTYPED", [r for r in acct_rows
                     if r["type"] not in ("taxable", "sheltered")]),
    ]
    group_defs = [(g, rows) for g, rows in group_defs if rows]
    grouped = len(acct_rows) > 1 and len(group_defs) > 1

    (_fyear, _is_us, _date_key, filing_rows, filing_line_rows, _filing_6781, filing_total,
     _engine_gain, _round_gap, _engine_denied, _denied_gap, _denied_label) = _return_figures(
        files, root, cfg, _accts, taxable_accounts, est, want_estimate, year)
    # Essentials first (docs/output-style.md): shown to a person without
    # --details, each warning below is one `! ` line after the tables
    # (`_acts`); with --details, under --json, or captured (width 0) it
    # is the full stderr message it always was.
    _full_msgs = (_details(args) or bool(getattr(args, "json", False))
                  or _out.width(sys.stderr) == 0)
    _acts: List[str] = []
    _fx_note = _fx_for_sum(root, cache, filing_rows, _is_us, _full_msgs, _acts, _prog)
    # Base currency is just a label here — soft-read, no hard config
    # dependency (the command works from the work/ gains files).
    base = _base_currency(root)
    _conservation, _unconsumed, _unsettled_comp = _book_checks(
        files, taxable_accounts, tainted_included, tainted_routed, _full_msgs, _acts, _prog)
    _uncovered, _in_totals, _ik_unbooked = _no_purchase_checks(
        args, root, cfg, cache, files, _st, _fyear, base, _full_msgs, _acts, _prog)
    # Scope: unlike the filing commands (carryover/t1135/form-export,
    # taxable-only by law), this summary rolls up EVERY account — say so
    # when sheltered accounts contribute, or the totals look like filing
    # numbers they aren't.
    sheltered_included = []
    try:
        acct_cfg = (accounts_of(load_config(root))
                    if _PL.has_config(root) else {})
        sheltered_included = sorted(
            a for a in files if account_of(acct_cfg, a).sheltered)
    except SystemExit:
        pass
    from taxjson.lib import loss_overrides as _LO
    _positions = _LO.positions(
        _LO.gather(files), year,
        "trade" if _date_key == "date" else "settle")
    return SumModel(
        root=root, cache=cache, files=files, basis=basis, want_estimate=want_estimate,
        other_income=_oi, other_losses=_ol, deductions=_ded, carrying_charges=_cc,
        lt_losses=_ltl, foreign_by_account=_foreign_by_acct,
        foreign_by_security=_foreign_by_sec, cfg=cfg, settings=_st, amt_carryover=_amt_in,
        carry_sources=_carry_src, taxable_accounts=taxable_accounts, est=est,
        run_state=_run_state, acct_rows=acct_rows, tainted_included=tainted_included,
        tainted_routed=tainted_routed, year=year, group_defs=group_defs, grouped=grouped,
        filing_year=_fyear, is_us=_is_us, date_key=_date_key, filing_rows=filing_rows,
        filing_line_rows=filing_line_rows, filing_6781=_filing_6781, filing_total=filing_total,
        engine_gain=_engine_gain, round_gap=_round_gap, engine_denied=_engine_denied,
        denied_gap=_denied_gap, denied_label=_denied_label, fx_note=_fx_note, acts=_acts,
        base=base, conservation=_conservation, unconsumed_premium=_unconsumed,
        unsettled_compensation=_unsettled_comp, no_purchase_uncovered=_uncovered,
        no_purchase_in_totals=_in_totals, in_kind_unbooked_moves=_ik_unbooked,
        sheltered_included=sheltered_included, filing_positions=_positions)


def _sum_json(args: argparse.Namespace, m: SumModel) -> None:
    """`tjs sum --json` (and `tjs estimate --json`): the document from
    the model, with the estimate and the instalments when asked for."""
    from taxjson.lib import readiness as _RD
    root, cache, files, basis = m.root, m.cache, m.files, m.basis
    want_estimate, _oi, _ol = m.want_estimate, m.other_income, m.other_losses
    _ded, _cc, _ltl = m.deductions, m.carrying_charges, m.lt_losses
    _foreign_by_acct, _foreign_by_sec, cfg = m.foreign_by_account, m.foreign_by_security, m.cfg
    _st, _amt_in, _carry_src = m.settings, m.amt_carryover, m.carry_sources
    taxable_accounts, est, _run_state = m.taxable_accounts, m.est, m.run_state
    acct_rows, tainted_included = m.acct_rows, m.tainted_included
    tainted_routed, year, group_defs, _is_us = m.tainted_routed, m.year, m.group_defs, m.is_us
    _date_key, filing_rows, filing_line_rows = m.date_key, m.filing_rows, m.filing_line_rows
    filing_total, _engine_gain, _engine_denied = m.filing_total, m.engine_gain, m.engine_denied
    _fx_note, base, _conservation = m.fx_note, m.base, m.conservation
    _unconsumed, _unsettled_comp = m.unconsumed_premium, m.unsettled_compensation
    _uncovered, _in_totals = m.no_purchase_uncovered, m.no_purchase_in_totals
    _ik_unbooked, sheltered_included = m.in_kind_unbooked_moves, m.sheltered_included
    _positions = m.filing_positions
    doc: Dict[str, Any] = {
        "accounts": acct_rows,
        "tainted_included": tainted_included,
        "tainted_routed": tainted_routed,
        # The same counts under their plain name ("tainted" is the
        # engine's word for a disposition with an unknown cost; the
        # keys above stay for existing readers).
        "unknown_cost_included": tainted_included,
        "unknown_cost_routed": tainted_routed,
        # Sales with no purchase that no .tt OPENING cost=unknown
        # line covers and the gains files lack: not in the totals at all
        # (an open short).
        "no_purchase_uncovered": _uncovered,
        # Such sales the gains engine booked (a short sale a later
        # purchase of the year closed, or sold from a purchase it
        # reads first): in the totals.
        "no_purchase_in_totals": _in_totals,
        # Same row-sum path as the printed tables and subtotals, so
        # totals == Σ subtotals == Σ rows holds exactly for machine
        # consumers (the unrounded accumulation drifted by a cent).
        "totals": _sum_rows(acct_rows),
        "basis": basis, "year": year, "currency": base,
        "filing": {"accounts": filing_rows, "totals": filing_total,
                   ("parts_8949" if _is_us else "lines"):
                       filing_line_rows,
                   "fx_cash": _fx_note,
                   "engine_gain_unrounded": _engine_gain,
                   "engine_denied_unrounded": _engine_denied,
                   "date_basis": _date_key},
        "sheltered_included": sheltered_included,
        "run_state_problems": _run_state,
        # One record of whether the figures are complete (lib/
        # readiness): the keys above, and the engine's conservation
        # warnings, as `complete` and `issues`.
        "readiness": _RD.build(
            run_state=_run_state or (), unknown_cost=tainted_routed,
            no_purchase=len(_uncovered),
            unbacked_cover=len(_unanswered_covers(
                root, cfg, getattr(args, "account", None) or None)),
            conservation=_conservation,
            in_kind_unbooked=len(_ik_unbooked),
            unconsumed_premium=_unconsumed,
            unsettled_compensation=_unsettled_comp),
        "subtotals": {g.lower(): _sum_rows(rows)
                      for g, rows in group_defs}}
    if _positions:
        # The user's filing positions against the loss rule (.tt
        # ALLOWLOSS): in the totals above, listed so they are never
        # silent (CA-SL-18 / US-WASH-25).
        doc["filing_positions"] = _positions
    if want_estimate:
        doc["estimate"] = _tax_estimate_result(
            cfg, est,
            other_income=_oi, other_losses=_ol,
            deductions=_ded, carrying_charges=_cc, lt_losses=_ltl,
            amt_carryover=_amt_in, carry_sources=_carry_src,
            province=getattr(args, "province", None),
            federal_only=bool(getattr(args, "federal_only", False)),
            **_withholding_of_year(
                cache, set(files) & taxable_accounts,
                year or _st.year,
                _foreign_by_acct, _foreign_by_sec))
        _iyear = year or _st.year
        _r = doc["estimate"]
        if not _is_us and "losses_unused" in _r:
            # The T1A carry-back the console prints (TJ-0084);
            # `losses_unused` stays the figure before it (backlog
            # TJ-0226).
            _add_carry_back_split(root, cfg, _iyear, _r)
        _idoc = _instalments_doc(
            root, doc["estimate"], _iyear,
            _instalment_config(root, _iyear))
        if _idoc:
            doc["instalments"] = _idoc
    if isinstance(doc.get("estimate"), dict):
        doc["estimate"] = _json_estimate(doc["estimate"])
    # `tjs estimate --json` (cmd_estimate sets `estimate`) is an output of
    # its own: the same document (lib/json_contract.SCHEMAS).
    if getattr(args, "estimate", False):
        _json_out(doc, schema="estimate")
    else:
        _json_out(doc, schema="sum")


def _json_cut(v: Any) -> Any:
    """`v` with every float in it at M.DERIVED_PLACES (dicts, lists and
    tuples at any depth; a tuple as a list, as JSON writes it)."""
    if isinstance(v, float):
        return M.json_money(v, M.DERIVED_PLACES)
    if isinstance(v, dict):
        return {k: _json_cut(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_cut(x) for x in v]
    return v


def _json_estimate(r: Dict[str, Any]) -> Dict[str, Any]:
    """The estimate as --json prints it (TJ-4930): its traces' steps (a
    bracket's tax, a credit, the foreign income's share and its limits:
    the rates' products and quotients, in float) at M.DERIVED_PLACES,
    where a float printed its binary error (58956.79750000001 for
    58956.7975). Its other figures are cut already; the text keeps the
    traces as they are."""
    return {k: (_json_cut(v) if k in ("trace_with", "trace_base") else v)
            for k, v in r.items()}


def _sum_tables(d: Doc,
                grouped: bool, group_defs: List[Tuple[str, List[Dict[str, Any]]]],
                _table_lines: Callable[[List[Dict[str, Any]], str], List[str]],
                sheltered_included: List[str], acct_rows: List[Dict[str, Any]],
                _stk: List[Dict[str, Any]], _det: bool, _para: Callable[..., None],
                money: Callable[[Any], str]) -> None:
    """`tjs sum`'s account tables: one per account type when there is more
    than one (each with its SUBTOTAL), then every account with the TOTAL;
    with --details, the crypto rows' staking note (cmd_summary)."""
    _tdrop = (6, 5)           # FEES, then PIL, when the width is short
    if grouped:
        for gname, rows in group_defs:
            d.line(f"{gname} ACCOUNTS"
                   + (" — not taxable" if gname == "SHELTERED" else ""))
            _print_report_table(_table_lines(rows, "SUBTOTAL"),
                                rule_before_last=True, fit=True,
                                drop=_tdrop)
            d.blank()
        d.line("ALL ACCOUNTS"
               + (" — sheltered included" if sheltered_included else ""))
    _print_report_table(_table_lines(acct_rows, "TOTAL"),
                        rule_before_last=True, fit=True, drop=_tdrop)
    if _stk and _det:
        _para("DIVIDEND for crypto account(s) "
              + ", ".join(f"{r['account']} ({money(r['dividend'])})"
                          for r in _stk)
              + " is STAKING rewards — ordinary income, not dividends.")


def _sum_return_us(d: Doc,
                   _para: Callable[..., None], base: str, _names: str, _fyear: Any,
                   _ynote: str, _det: bool, _rounding_legend: Callable[[], None],
                   money: Callable[[Any], str], filing_line_rows: List[Dict[str, Any]],
                   filing_total: Dict[str, float], _filing_6781: Optional[Dict[str, Any]],
                   _acts: List[str], _item: Callable[[str], None], _round_gap: float,
                   _engine_gain: float, _denied_gap: float, _denied_label: str,
                   _engine_denied: float, _tail: List[str]) -> None:
    """FOR THE RETURN in the US (cmd_summary): Form 8949's lines and the
    RETURN row, a §1256 `! ` line (`_acts`), the notes with --details,
    and the pointer `_tail` ends with."""
    from taxjson.lib import out as _out
    _para(f"FOR THE RETURN — taxable accounts ({_names}), {base} "
          f"(Form 8949 → Schedule D, tax year {_fyear})")
    if _ynote:
        _para(_ynote if _det else
              f"Tax year {_fyear} has not ended: these are "
              f"YEAR-TO-DATE figures.")
    _para("(h) = (d) − (e) + (g), the allowed gain; (g) = the "
          "code-W wash-sale loss disallowed, added back.")
    _rounding_legend()
    _body = [[f"{r['label']} → {r['schedule_d']}",
              money(r["proceeds"]), money(r["cost"]),
              money(r["adjustment"]), money(r["gain"])]
             for r in filing_line_rows]
    _foot = [["RETURN", money(filing_total["proceeds"]),
              money(filing_total["cost"]),
              money(filing_total["adjustment"]),
              money(filing_total["gain"])]]
    for _ln in _out.fit_table(
            ["FORM 8949", "(d) PROCEEDS", "(e) COST",
             "(g) ADJUSTMENT", "(h) GAIN"], _body,
            aligns=["<", ">", ">", ">", ">"], foot=_foot):
        print(_ln)
    _permd = filing_total.get("permanently_denied") or 0.0
    if _filing_6781 and _filing_6781.get("dispositions"):
        _acts.append(_out.act(
            f"{_count_noun(_filing_6781['dispositions'], '§1256 sale')}"
            f", net {money(_filing_6781['gain'])}, not on Form "
            f"8949: Form 6781 by hand", "tjs form-export"))
    if _det:
        d.blank()     # the notes stand apart from the RETURN row
        _item("(d) − (e) + (g) = (h). Column (g) is the code-W "
              "wash-sale loss disallowed and added back, so (h) "
              "is the allowed gain; the disallowed loss moves to "
              "the replacement shares' basis"
              + (f", except {money(_permd)} from a repurchase in "
                 f"an IRA, which is lost for good — no basis "
                 f"addition (US-WASH-11)" if _permd > 0.005
                 else "") + ".")
        if _filing_6781 and _filing_6781.get("dispositions"):
            _item(f"Not on Form 8949: "
                  f"{_filing_6781['dispositions']} §1256 contract "
                  f"disposition(s), net "
                  f"{money(_filing_6781['gain'])} — Form 6781 by "
                  f"hand (60/40; year-end marking not modelled, "
                  f"US-FUT-02 / US-OPT-04). `tjs form-export` "
                  f"lists them.")
        if abs(_round_gap) >= 0.005:
            _item(f"Rows are rounded to the cent, as filed: the "
                  f"gains files' unrounded total gain is "
                  f"{money(_engine_gain)} ({M.shown(_round_gap, 2):+,.2f} on "
                  f"the RETURN row).")
        if abs(_denied_gap) >= 0.005:
            _item(f"Rows are rounded to the cent, as filed: the "
                  f"gains files' unrounded total {_denied_label} "
                  f"is {money(_engine_denied)} "
                  f"({M.shown(_denied_gap, 2):+,.2f} on the RETURN row).")
        _item("Per-sale rows: `tjs form-export`.")
    _tail.append("More: tjs sum --details (notes); per sale: "
                 "tjs form-export")


def _sum_return_ca(d: Doc,
                   _para: Callable[..., None], base: str, _names: str, _fyear: Any,
                   _ynote: str, _det: bool, money: Callable[[Any], str],
                   filing_line_rows: List[Dict[str, Any]], filing_total: Dict[str, float],
                   _rounding_legend: Callable[[], None], _fx_note: Optional[Dict[str, Any]],
                   _acts: List[str], _item: Callable[[str], None], _round_gap: float,
                   _engine_gain: float, _denied_gap: float, _denied_label: str,
                   _engine_denied: float, _tail: List[str]) -> None:
    """FOR THE RETURN in Canada (cmd_summary): Schedule 3's lines and the
    RETURN row, the line-15300 FX `! ` line (`_acts`), the notes with
    --details, and the pointer `_tail` ends with."""
    from taxjson.lib import out as _out
    _para(f"FOR THE RETURN — taxable accounts ({_names}), {base} "
          f"(Schedule 3, tax year {_fyear})")
    if _ynote:
        _para(_ynote if _det else
              f"Tax year {_fyear} has not ended: these are "
              f"YEAR-TO-DATE figures.")
    _body = [[f"Line {r['line']} {r['short']} "
              f"({r['proceeds_code']}/{r['gain_code']})"
              if r["line"] else
              f"{r['short']} ({r['proceeds_code']}/"
              f"{r['gain_code']})",
              money(r["proceeds"]), money(r["acb"]),
              money(r["outlays"]), money(r["gain"]),
              money(r["denied"])] for r in filing_line_rows]
    _foot = [["RETURN", money(filing_total["proceeds"]),
              money(filing_total["acb"]),
              money(filing_total["outlays"]),
              money(filing_total["gain"]),
              money(filing_total["denied"])]]
    # Too wide: DENIED goes first (its total moves to the legend),
    # then one record per line.
    _s3 = _out.fit_table(
        ["SCHEDULE 3 LINE", "PROCEEDS", "COST(ACB)", "OUTLAYS",
         "GAIN", "DENIED"], _body,
        aligns=["<", ">", ">", ">", ">", ">"], foot=_foot,
        drop=(5,))
    _den_shown = any("DENIED" in _ln for _ln in _s3[:1])
    _para("GAIN = PROCEEDS − COST(ACB) − OUTLAYS, the allowed "
          "gain; "
          + ("DENIED = superficial losses denied." if _den_shown
             else f"superficial losses denied: "
                  f"{money(filing_total['denied'])}."))
    _rounding_legend()
    for _ln in _s3:
        print(_ln)
    _permd = filing_total.get("permanently_denied") or 0.0
    # FX on foreign cash (line 15300): never in the rows.
    if _fx_note is None:
        _acts.append(_out.act("FX on foreign cash (line 15300) is "
                              "not in the rows", "tjs fx-cash"))
    elif (_fx_note.get("ledger") == "v2"
            and _fx_note.get("status") == "computed"):
        _acts.append(_out.act(
            f"FX on foreign cash (line 15300), not in the rows: "
            f"reportable {money(_fx_note['reportable'])}",
            "tjs fx-cash"))
    elif _fx_note.get("ledger") == "v2":
        _acts.append(_out.act("FX on foreign cash (line 15300): "
                              "not computed, not in the rows",
                              "tjs fx-cash"))
    elif _fx_note.get("active"):
        _acts.append(_out.act("FX on foreign cash (line 15300): "
                              "not in the rows; its estimate is "
                              "NOT RELIABLE", "tjs fx-cash"))
    if _det:
        d.blank()     # the notes stand apart from the RETURN row
        # A short sale's opening commission is not on its
        # closing row: it stays netted (backlog TJ-0119).
        _item("PROCEEDS − COST(ACB) − OUTLAYS = GAIN, the allowed "
              "gain. A short sale shows what it brought in, net "
              "of its opening commission, as PROCEEDS and the "
              "cover as ACB; the commissions of other sales are "
              "outlays, as on the form.")
        _item(f"Where a superficial loss was DENIED "
              f"({money(filing_total['denied'])} in all) the ACB "
              f"is REDUCED by it, so the gain stays the allowed "
              f"one; a deferred denial is added to the ACB of the "
              f"replacement property, but one caused by a "
              f"registered-account acquisition is lost for good, "
              f"and one caused by an affiliated person's "
              f"acquisition is permanent for this return (that "
              f"person adds it to their own ACB, s.53(1)(f)) — no "
              f"ACB addition here"
              + (f" ({money(_permd)} of the DENIED total)"
                 if _permd > 0.005 else "") + ".")
        _dcon = filing_total.get("denied_contribution") or 0.0
        if _dcon > 0.005:
            _item(f"Denied: contribution to a registered plan — "
                  f"{money(_dcon)} of losses on in-kind "
                  f"contributions to an RRSP/TFSA/... are nil "
                  f"(s.40(2)(g)(iv)): the ACB shown is reduced by "
                  f"them, they are lost for good (no ACB addition "
                  f"anywhere) and they are not in DENIED.")
        if abs(_round_gap) >= 0.005:
            _item(f"Rows are rounded to the cent, as filed: the "
                  f"gains files' unrounded total gain is "
                  f"{money(_engine_gain)} ({M.shown(_round_gap, 2):+,.2f} on "
                  f"the RETURN row).")
        if abs(_denied_gap) >= 0.005:
            _item(f"Rows are rounded to the cent, as filed: the "
                  f"gains files' unrounded total {_denied_label} "
                  f"is {money(_engine_denied)} "
                  f"({M.shown(_denied_gap, 2):+,.2f} on the RETURN row).")
        if _fx_note is not None:
            _item(_fx_sum_item(_fx_note, money))
        else:
            _item("FX on foreign cash (s.39(1.1)) is not in the "
                  "rows above — T4037 puts it on line 15300; see "
                  "`tjs fx-cash`.")
        # Slip capital gains are part of line 19700 too (R1-44).
        _item("Capital gains on T3 (box 21) and T5/T5013 (box 18) "
              "slips are not in the rows above — Schedule 3 lines "
              "17600 and 17400, entered from the slips (the books "
              "carry those distributions as dividends).")
        _item("Per-security rows: `tjs form-export`; per "
              "account: `tjs sum --json`.")
    # Slip capital gains are part of line 19700 too (R1-44).
    _tail.append("Not in the rows: T3/T5 slip capital gains (lines "
                 "17600, 17400). More: tjs sum --details")


def _sum_filing_positions(d: Doc,
                          _positions: List[Dict[str, Any]], _is_us: bool, _acts: List[str],
                          _det: bool, _para: Callable[..., None],
                          _item: Callable[[str], None], money: Callable[[Any], str]) -> None:
    """The filing positions against the loss rule (.tt ALLOWLOSS): their
    `! ` line (`_acts`), and with --details each one (cmd_summary)."""
    from taxjson.lib import out as _out
    if _positions:
        _rule = ("wash-sale rule (§1091)" if _is_us
                 else "superficial-loss rule (s.54)")
        _acts.append(_out.act(
            f"{_count_noun(len(_positions), 'loss', 'losses')} claimed "
            f"against the {_rule.split(' (')[0]} (.tt ALLOWLOSS)",
            "tjs sum --details"))
    if _positions and _det:
        d.blank()
        # "the totals above include them" only of this year's sales: a
        # position on another year's sale shapes this year's ACB, not
        # its totals (pre-release review).
        _n_in = sum(1 for _fp in _positions if _fp["in_year"])
        _n_out = len(_positions) - _n_in
        _para(f"FILING POSITIONS — {len(_positions)} loss(es) claimed "
              f"against the {_rule} (.tt ALLOWLOSS lines)"
              + ("; the totals above include them" if not _n_out else
                 "; sales of another year: not in the totals above, "
                 "their effect on the " + ("basis" if _is_us else "ACB")
                 + " carries" if not _n_in else
                 f"; the totals above include the {_n_in} of this year, "
                 f"not the {_n_out} of another year"))
        for _fp in _positions:
            _item(f"{_fp['account']} {_fp['date']} {_fp['symbol']} "
                  f"{fmt_qty(_fp['qty'])} units: loss {money(_fp['loss'])} "
                  f"claimed; the rule would "
                  f"{'disallow' if _is_us else 'deny'} "
                  f"{money(_fp['would_disallow'])}"
                  + (f" ({money(_fp['would_permanent'])} for good)"
                     if _fp['would_permanent'] > 0.005 else "")
                  + f" — {_fp['why']}. Your reason: \"{_fp['reason']}\" "
                  f"({_fp['where']})"
                  + ("" if _fp["in_year"] else
                     " [a sale of another year: its effect on the "
                     + ("basis" if _is_us else "ACB") + " carries]"))
        _item("A filing position is yours, not the rule's test: be ready "
              "to support it (" + ("IRS" if _is_us else "CRA")
              + " guidance, a tax professional). Delete the line to apply "
              "the rule.")


def cmd_summary(args: argparse.Namespace) -> None:
    """One-row-per-account realized-gains summary (base currency) on the
    filing basis: wash-adjusted gains where the pipeline built the
    cross-account pass (matching `reports/<account>_wash.sum`, carryover,
    t1135 and form-export; refused without them), plain gains otherwise.
    The banner names the basis. Reuses `summarize_gains`: matches
    `reports/<account>.sum`."""
    from taxjson.lib import out as _out
    m = build_sum(args)
    if getattr(args, "json", False):
        _sum_json(args, m)
        return

    from taxjson.bin.taxjson_form_export import year_not_ended_text
    root, cache, files, basis = m.root, m.cache, m.files, m.basis
    want_estimate, _oi, _ol = m.want_estimate, m.other_income, m.other_losses
    _ded, _cc, _ltl = m.deductions, m.carrying_charges, m.lt_losses
    _foreign_by_acct, _foreign_by_sec, cfg = m.foreign_by_account, m.foreign_by_security, m.cfg
    _st, _amt_in, _carry_src = m.settings, m.amt_carryover, m.carry_sources
    taxable_accounts, est, acct_rows, year = m.taxable_accounts, m.est, m.acct_rows, m.year
    group_defs, grouped, _fyear, _is_us = m.group_defs, m.grouped, m.filing_year, m.is_us
    filing_rows, filing_line_rows = m.filing_rows, m.filing_line_rows
    _filing_6781, filing_total, _engine_gain = m.filing_6781, m.filing_total, m.engine_gain
    _round_gap, _engine_denied, _denied_gap = m.round_gap, m.engine_denied, m.denied_gap
    _denied_label, _fx_note, _acts, base = m.denied_label, m.fx_note, m.acts, m.base
    sheltered_included, _positions = m.sheltered_included, m.filing_positions
    money = fmt_money               # shared report-layer formatter

    # NON-OPT: every non-option disposition (shares, units, futures,
    # crypto) — it was headed STOCK and read as Schedule 3 line 4
    # (audit R1-211, S051-01).
    header = ["ACCOUNT", "NON-OPT", "OPTION", "REALIZED", "DIVIDEND", "PIL",
              "FEES", "TOTAL"]

    def _table_lines(rows: List[Dict[str, Any]],
                     total_label: str) -> List[str]:
        lines = [" ".join(header)]
        for r in rows:
            lines.append(" ".join(
                [r["account"], money(r["stock"]), money(r["option"]),
                 money(r["realized"]), money(r["dividend"]),
                 money(r["pil"]), money(r["fees"]), money(r["total"])]))
        s = _sum_rows(rows)
        lines.append(" ".join(
            [total_label, money(s["stock"]), money(s["option"]),
             money(s["realized"]), money(s["dividend"]), money(s["pil"]),
             money(s["fees"]), money(s["total"])]))
        return lines

    def _para(text: str, indent: str = "") -> None:
        for _ln in _out.wrap(text, None, indent, indent):
            print(_ln)

    def _item(text: str) -> None:
        for _ln in _out.wrap(text, None, "- ", "  "):
            print(_ln)

    def _rounding_legend() -> None:
        # The taxable accounts' SUBTOTAL adds the account rows (each
        # rounded once), the RETURN row each sale's cents: the legend
        # says so when they differ, where the reader compares them
        # (backlog TJ-0209; the US §1256 net is off the RETURN row).
        _sub = M.shown(sum(r["realized"] for r in acct_rows
                         if r["account"] in taxable_accounts), 2)
        _ret = (float(filing_total.get("gain") or 0.0)
                + float((_filing_6781 or {}).get("gain") or 0.0))
        if filing_line_rows and abs(_sub - _ret) >= 0.005:
            # Not led by a row label: a line that starts "RETURN " or
            # "TOTAL" reads as that row to a script.
            _para(f"The {'SUBTOTAL' if grouped else 'TOTAL'} adds the "
                  f"account rows, RETURN each sale's cents: they can "
                  f"differ by a cent or two.")

    # The text: a live exact Doc, each op written to stdout as it is
    # added, as the print() it replaces wrote it.
    d = _out.Doc(exact=True, width_=0, live=True)
    _det = _details(args)
    d.line(f"REALIZED-GAINS SUMMARY — {base}, tax year {year}, "
           f"basis: {basis}")
    # The legend, before the tables (docs/output-style.md, Essentials
    # first): what the columns mean, and a crypto row's DIVIDEND.
    _para("REALIZED = NON-OPT (shares, units, futures, crypto) + OPTION; "
          "TOTAL = REALIZED + DIVIDEND + PIL.")
    _stk = [r for r in acct_rows
            if r.get("dividend_is_staking") and abs(r["dividend"]) >= 0.005]
    if _stk:
        # The crypto rows' DIVIDEND is staking rewards: ordinary income
        # (no gross-up/credit, no withholding), as the estimate treats
        # it — not a figure for the dividend lines (S023-11).
        _para("DIVIDEND of " + ", ".join(r["account"] for r in _stk)
              + " is staking rewards: ordinary income, not dividends.")
    _filing = ("carryover and form-export" if _is_us
               else "carryover, t1135 and form-export")
    if sheltered_included and (_det or not grouped):
        _para(f"Totals include sheltered account(s) "
              f"{', '.join(sheltered_included)} — not taxable events; "
              f"{_filing} exclude them.")
    d.blank()
    _sum_tables(d, grouped, group_defs, _table_lines, sheltered_included, acct_rows, _stk, _det,
                _para, money)

    # After the data: the one-line act-on and must-not-miss lines
    # (`_acts`, `_tail`); --details adds the notes they leave out.
    _tail: List[str] = []
    if filing_rows:
        _names = ", ".join(r["account"] for r in filing_rows)
        _ynote = year_not_ended_text(_fyear)
        d.blank()
        if _is_us:
            _sum_return_us(d, _para, base, _names, _fyear, _ynote, _det, _rounding_legend, money,
                           filing_line_rows, filing_total, _filing_6781, _acts, _item,
                           _round_gap, _engine_gain, _denied_gap, _denied_label,
                           _engine_denied, _tail)
        else:
            _sum_return_ca(d, _para, base, _names, _fyear, _ynote, _det, money, filing_line_rows,
                           filing_total, _rounding_legend, _fx_note, _acts, _item, _round_gap,
                           _engine_gain, _denied_gap, _denied_label, _engine_denied, _tail)

    _sum_filing_positions(d, _positions, _is_us, _acts, _det, _para, _item, money)
    if not _det and (_acts or _tail):
        d.blank()
        for _ln in _acts + _tail:
            print(_ln)

    if want_estimate:
        _print_tax_estimate(
            cfg, est, base,
            other_income=_oi, other_losses=_ol,
            deductions=_ded, carrying_charges=_cc, lt_losses=_ltl,
            amt_carryover=_amt_in, carry_sources=_carry_src,
            province=getattr(args, "province", None),
            verbose=getattr(args, "verbose", False),
            **_withholding_of_year(
                cache, set(files) & taxable_accounts,
                year or _st.year,
                _foreign_by_acct, _foreign_by_sec),
            root=root,
            year=year or _st.year)


def cmd_estimate(args: argparse.Namespace) -> None:
    """`tjs estimate`: the realized-gains summary table followed by
    the tax ESTIMATE for the year's investment income — the same code
    path `sum` uses for its estimate block (the two can never
    disagree): tax(other income + investment income) minus
    tax(other income), on the filing (wash-adjusted) basis, FTC from
    the books' actual TAX rows. ESTIMATE ONLY — never filing
    numbers.

    Essentials first (docs/output-style.md): the default view is the
    estimate block alone (the gains table is `tjs sum`) with the
    summary's act-on `! ` lines; --details and --json keep the summary
    first."""
    args.estimate = True
    if getattr(args, "json", False) or _details(args):
        cmd_summary(args)
        return
    import contextlib as _ctx
    import io as _io
    import os as _os
    from taxjson.lib import out as _out
    buf = _io.StringIO()
    # The captured text keeps the width the person's stdout has.
    _old_w = _os.environ.get("TAXJSON_WIDTH")
    _os.environ["TAXJSON_WIDTH"] = str(_out.width(sys.stdout))
    try:
        with _ctx.redirect_stdout(buf):
            cmd_summary(args)
    except SystemExit:
        sys.stdout.write(buf.getvalue())
        raise
    finally:
        if _old_w is None:
            _os.environ.pop("TAXJSON_WIDTH", None)
        else:
            _os.environ["TAXJSON_WIDTH"] = _old_w
    text = buf.getvalue()
    at = text.find("TAX ESTIMATE —")
    head, block = (text[:at], text[at:]) if at >= 0 else ("", text)
    lines = block.rstrip("\n").split("\n")
    more = [ln for ln in lines if ln.startswith("More: ")]
    for ln in lines:
        if not ln.startswith("More: "):
            print(ln)
    # The act-on lines first, then the pointers to the long form.
    for ln in [x for x in head.split("\n") if x.startswith(_out.ACT)]:
        print(ln)
    for ln in more:
        print(ln.rstrip() + "; the return's lines: tjs sum")


def _wrap_note(text: str, indent: str = "  ") -> str:
    """Report prose wrapped to the house width (lib/out: 100, or the
    terminal's) — the AMT explanation and the assumptions footer ran off
    the edge on any terminal while every table beside them was capped."""
    from taxjson.lib.out import fill
    return fill(text, indent=indent)


def _trace_bracket_rows(brackets, ti_base: float, ti_with: float):
    """[(band label, base tax, with tax)] over every band either run
    reaches, band by band — the two-column body of the --verbose trace.
    A band's label ends where the higher of the two incomes stops in
    it: a band only the base run reaches (an investment loss moving the
    WITH run down a bracket) is listed with 0.00 in WITH, so each column
    adds up to its total (backlog TJ-0472)."""
    from taxjson.lib.tax_estimate import bracket_slices
    base = {lo: (hi, rate, tax) for lo, hi, rate, tax
            in bracket_slices(ti_base, brackets)}
    with_ = {lo: (hi, rate, tax) for lo, hi, rate, tax
             in bracket_slices(ti_with, brackets)}
    rows = []
    for lo in sorted(set(base) | set(with_)):
        hi = max(x[0] for x in (base.get(lo), with_.get(lo)) if x)
        rate = (with_.get(lo) or base[lo])[1]
        hi_s = "inf" if hi == float("inf") else f"{M.shown(hi, 0):,.0f}"
        rows.append((f"{M.shown(lo, 0):,.0f}-{hi_s} @ {rate * 100:.2f}%",
                     (base.get(lo) or (0, 0, 0.0))[2],
                     (with_.get(lo) or (0, 0, 0.0))[2]))
    return rows


def _trace_table_lines(rows, money) -> List[str]:
    """Rows of the --verbose trace: label, BASE and WITH in fixed columns."""
    return [f"    {label:<34}{money(b):>14}{money(w):>14}" for label, b, w in rows]


def _est_row_lines(details: bool, label: str, amt: float, note: str = "",
                   short: Optional[str] = None) -> List[str]:
    """One figure row of the estimate, as its lines."""
    from taxjson.lib import out as _out
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    # The default view takes the row's `short` note (one line).
    if not details and short is not None:
        note = short
    # `  label  amount  [note]`: a long note wraps under itself.
    head = f"  {label:<30}{money(amt):>14}"
    if not note:
        _push(head)
        return lines
    for _ln in _out.wrap(note, None, head + "  ", " " * (len(head) + 2)):
        _push(_ln)
    return lines


def _est_notes_lines(r: Dict[str, Any], indent: str) -> List[str]:
    """The estimate's notes, a `- ` item each (--details)."""
    from taxjson.lib import out as _out
    lines: List[str] = []
    _push = lines.append
    for _n in r.get("notes") or []:
        for _ln in _out.wrap(_n, None, indent + "- ", indent + "  "):
            _push(_ln)
    return lines


def _assumes_lines(text: str, indent: str = "  ") -> List[str]:
    """The assumptions as a list under "Assumes:" (--details)."""
    from taxjson.lib import out as _out
    lines: List[str] = []
    _push = lines.append
    # "Assumes: a; b; c." as a list — split on the top-level "; "
    # only (a parenthesis may hold its own).
    body = text[len("Assumes:"):].strip() if text.startswith(
        "Assumes:") else text
    parts, depth, cur = [], 0, ""
    for i, ch in enumerate(body):
        depth += (ch == "(") - (ch == ")")
        if ch == ";" and depth == 0 and body[i + 1:i + 2] == " ":
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    parts.append(cur.strip().rstrip("."))
    _push(indent + "Assumes:")
    for _p in parts:
        if _p:
            for _ln in _out.wrap(_p, None, indent + "- ",
                                 indent + "  "):
                _push(_ln)
    return lines


def _est_tail_lines(res: Dict[str, Any], yr: Any, assumes: str) -> List[str]:
    """The default view's closing lines: a rate-table mismatch and
    provisional carryovers to act on, the one-line assumptions and
    the pointer to the notes behind --details."""
    from taxjson.lib import out as _out
    lines: List[str] = []
    _push = lines.append
    if yr is not None and str(res.get("vintage")) != str(yr):
        _push(_out.act(f"Tax year {yr} uses the {res.get('vintage')} "
                       f"rate tables", "tjs estimate --details"))
    # Carryovers read from a lock taken before its year ended are
    # provisional: one line, the note behind --details (issue #55).
    _pl = (res.get("carry_sources") or {}).get("partial_lock")
    if _pl:
        _push(_out.act(f"Carryovers come from the {_pl} lock, taken "
                       f"before {_pl} ended: provisional",
                       "tjs estimate --details"))
    n = len(res.get("notes") or [])
    _push("")
    _push(assumes)
    _push(_out.details_hint("tjs estimate --details",
                            "assumptions" + (f", {n} note(s)"
                                             if n else "")))
    return lines


def _estimate_tail(r: Dict[str, Any], year: Any, details: bool, short: str,
                   assumes: Optional[str] = None) -> List[str]:
    """The estimate's closing lines: with --details the notes and the
    assumptions (`assumes`, else the result's own) after a blank line;
    else the default view's tail, its assumptions the one line `short`."""
    if not details:
        return _est_tail_lines(r, year, short)
    return ([""] + _est_notes_lines(r, "  ")
            + _assumes_lines(r["assumptions"] if assumes is None else assumes))


def _ca_rows(r: Dict[str, Any], est: Dict[str, float], other_income: float, details: bool,
             RATE_VINTAGE: Any) -> Tuple[List[str], str, str]:
    """Canada: the title and the legend, then the figure rows (the
    income, the gains, the dividends and their foreign tax credit,
    the deductions). With them, the 15% and the credit's basis the
    --verbose trace names."""
    from taxjson.lib import tax_estimate as _TE
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    _push(f"TAX ESTIMATE — canada/{r['province']}, rates vintage "
          f"{RATE_VINTAGE}")
    if not details:
        # The legend, directly above the rows it explains.
        _push("ESTIMATE ONLY, not filing numbers: taxable accounts "
              "only; [ ] says how an amount was built.")
    else:
        _push("ESTIMATE ONLY, not filing numbers; taxable accounts "
              "only.")
        _push("")
    _ftc_src = str(r.get("ftc_source") or "")
    # The foreign tax counted and, when the T2209 / T2036 limits
    # leave some of it uncredited, what the credits take (backlog
    # TJ-0367).
    _ftc_tax = float(r.get("foreign_tax_creditable",
                           r.get("ftc_assumed")) or 0.0)
    _ftc_cr = float(r.get("ftc_federal", _ftc_tax) or 0.0) + float(
        r.get("ftc_provincial") or 0.0)
    _ftc_part = (f"FTC {money(_ftc_cr)} of {money(_ftc_tax)}"
                 if _ftc_tax - _ftc_cr > 0.005
                 else f"FTC {money(_ftc_tax)}")
    _ftc_split = (f"; credited: federal {money(r['ftc_federal'])} "
                  f"(T2209), {r['province']} "
                  f"{money(r.get('ftc_provincial') or 0.0)} (T2036)"
                  if _ftc_tax - _ftc_cr > 0.005
                  or float(r.get("ftc_provincial") or 0.0) > 0.005
                  else "")
    # Which part of the foreign tax the TAX rows hold and which is
    # the 15% assumption: "from the TAX rows" named an assumed
    # credit actual (backlog TJ-0422).
    _pct15 = _TE.fmt_pct(_TE.CA_FOREIGN_WITHHOLDING)
    _basis = r.get("ftc_basis") or ("tax_rows"
                                    if _ftc_src.startswith("actual")
                                    else "assumed")
    if _basis == "tax_rows_and_assumed":
        _ftc_short = (f"[{_ftc_part}: TAX rows "
                      f"{money(r.get('foreign_tax_from_rows') or 0.0)}"
                      f" + assumed {_pct15} "
                      f"{money(r.get('foreign_tax_by_assumption') or 0.0)}]")
    elif _basis == "tax_rows":
        _ftc_short = f"[{_ftc_part}, from the TAX rows]"
    else:
        _ftc_short = (f"[{_ftc_part}, assumed {_pct15}"
                      + ("; none on spin-off shares"
                         if float(r.get("foreign_div_noncash") or 0.0)
                         > 0.005 else "") + "]")
    # The eligible dividends taxed: compensation paid on a short
    # above the dividends received is not negative income (backlog
    # TJ-0727).
    _elig = float(r.get("eligible_dividends", est["div_ca"]))
    _elig_x = float(r.get("eligible_compensation_excess") or 0.0)
    _elig_floor = (f"; net {money(-_elig_x)}: compensation paid "
                   f"above the dividends is not deducted"
                   if _elig_x > 0.005 else "")
    rows = [
        ("Other income", other_income, ""),
        ("Capital gains (taxable)", r["taxable_gain"],
         f"[{money(est['realized'])} realized"
         + (f" + {money(r['capital_gains_dividends'])} box 18"
            if r.get("capital_gains_dividends") else "")
         + f" - {money(r['losses_applied'])} other losses, "
           f"x{_TE.fmt_pct(_TE.CA_INCLUSION)}]"),
    ] + ([("Capital-gains dividends", r["capital_gains_dividends"],
           "[T5 box 18, line 17400: in the gains above, not "
           "grossed up]", "[T5 box 18: in the gains above]")]
         if r.get("capital_gains_dividends") else []) + [
        ("Eligible dividends (grossed)", r["grossed_eligible"],
         f"[{money(_elig)} x{_TE.CA_ELIGIBLE_GROSSUP:g}, "
         f"Canadian issuers, "
         f"trust distributions included{_elig_floor}]",
         f"[{money(_elig)} x{_TE.CA_ELIGIBLE_GROSSUP:g}"
         f"{_elig_floor}]"),
        ("Foreign dividends", est["div_foreign"],
         f"[{_ftc_part} — "
         f"{r.get('ftc_source') or 'assumed ' + _pct15}"
         f"{_ftc_split}]",
         _ftc_short),
        ("Payments in lieu", est["pil"], ""),
    ] + ([("Crypto staking (ordinary)", r["staking"],
           "[no withholding, no FTC]")]
         if r.get("staking") else []) \
      + ([("Deductions", -r["deductions"],
           "[lines 20700-23500, e.g. RRSP 20800; in full under AMT]",
           "[lines 20700-23500]")]
         if r.get("deductions") else []) \
      + ([("Carrying charges", -r["carrying_charges"],
           f"[line 22100; "
           f"{_TE.fmt_pct(_TE.CA_AMT_CARRYING_CHARGE_ALLOWANCE)} under "
           f"AMT]")]
         if r.get("carrying_charges") else [])
    for label, amt, note, *short in rows:
        lines += _est_row_lines(details, label, amt, note, *short)
    return lines, _pct15, _basis


def _ca_tax_lines(r: Dict[str, Any], base_cur: str, details: bool) -> List[str]:
    """Canada: the tax with and without the investments, and the headline."""
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    _push("")
    lines += _est_row_lines(details, "Tax with investments", r['tax_with']['total'],
                            f"federal {money(r['tax_with']['federal'])} + "
                            f"{r['province']} {money(r['tax_with']['provincial'])}")
    lines += _est_row_lines(details, "Tax on other income alone", r['tax_base']['total'], "")
    _push(f"  => ESTIMATED TAX ON INVESTMENT INCOME: "
          f"{money(r['estimated_tax'])} {base_cur}"
          + (f"  ({r['avg_rate_pct']:.1f}% of "
             f"{money(r['investment_income'])})"
             if r["avg_rate_pct"] is not None else ""))
    if r["estimated_tax"] < -0.005 and not details:
        _push("  (negative: a saving — the dividend tax credit "
              "exceeds the tax on the grossed-up dividends)")
    elif r["estimated_tax"] < -0.005:
        # Signed (R1-47): eligible dividends at a low bracket earn
        # more credit than the tax on their grossed-up amount.
        _push(_wrap_note(
            f"Negative = a saving: the investment income lowers the "
            f"tax on the other income by "
            f"{money(-r['estimated_tax'])} (the dividend tax credit "
            f"exceeds the tax on the grossed-up dividends)."))
    return lines


def _ca_losses_lines(r: Dict[str, Any], root: Optional[Path], cfg: Dict[str, Any], year: Any,
                     other_losses: float, _g: str) -> List[str]:
    """Canada: the unused losses and the net capital losses carried in
    (the carry-back read first: it may warn)."""
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    # The part of the year's loss [carryover] claimed carries back
    # (T1A) does not carry forward: close-year's lock and
    # `tjs carryover` take it out (backlog TJ-0084).
    _line = _unused_losses_line(root, cfg, year, r, money, _g)
    if _line:
        _push(_line)
    _ls = (r.get("carry_sources") or {}).get("other_losses")
    if _ls and other_losses:
        _push(f"  Net capital losses carried in:{_g}"
              f"{money(other_losses)} — from {_ls}")
    elif not _ls and year:
        # No lock before the year carried one and none was entered:
        # the 0 is the default, said, and where the books' figure
        # is — as the US estimate says it (backlog TJ-0612).
        _push(f"  Net capital losses carried in:{_g}{money(0.0)} — no "
              f"close-year lock before {year} and none entered: see "
              f"`tjs carryover`")
    return lines


def _ca_amt_lines(r: Dict[str, Any], base_cur: str, details: bool, _h: str) -> List[str]:
    """Canada: the AMT check and the minimum tax carryover (none without
    an AMT result)."""
    from taxjson.lib import tax_estimate as _TE
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    amt = r.get("amt")
    if amt:
        _push("")
        _push(f"{_h}AMT CHECK — post-2024 minimum tax "
              f"({amt['rate'] * 100:.1f}% over "
              f"{money(amt['exemption'])} exemption)")
        _push(f"  {'Adjusted taxable income':<30}"
              f"{money(amt['adjusted_income']):>14}")
        _push(f"  {'Federal minimum tax':<30}"
              f"{money(amt['minimum_fed']):>14}")
        _push(f"  {'Federal regular tax':<30}"
              f"{money(amt['regular_fed']):>14}")
        # The top-up the other income owes on its own is not the
        # investment income's (backlog TJ-0489): it comes off.
        _base_top = float(amt.get("base_topup") or 0.0)
        if amt["binding"]:
            _push(f"  {'=> AMT TOP-UP':<30}"
                  f"{money(amt['topup']):>14}")
            # One row each: "federal / ON" as one figure overran
            # the amount column.
            _push(f"  {'   federal':<30}"
                  f"{money(amt['excess_fed']):>14}")
            _push(f"  {'   ' + str(r['province']):<30}"
                  f"{money(amt['provincial_amt']):>14}")
            # The provincial foreign tax credit of a minimum-tax
            # year (form T2036) less the regular one: part of the
            # top-up (backlog TJ-0591).
            _pfc = float(amt.get("provincial_ftc_change") or 0.0)
            if abs(_pfc) > 0.005:
                _push(f"  {'   ' + str(r['province']) + ' FTC (T2036)':<30}"
                      f"{money(_pfc):>14}  [credit "
                      f"{money(amt.get('provincial_ftc_min_tax', 0.0))}"
                      f" with AMT, "
                      f"{money(amt.get('provincial_ftc_regular', 0.0))}"
                      f" without]")
        else:
            _push(f"  {'=> does not bind':<30}"
                  f"{money(amt['headroom']):>14}  [headroom]")
        if _base_top > 0.005:
            _push(f"  {'Top-up on other income alone':<30}"
                  f"{money(-_base_top):>14}  [owed without the "
                  f"investments]")
        if amt["binding"] or _base_top > 0.005:
            # Named as the headline above and `amt` name it: the
            # investment income's tax, not the total tax (backlog
            # TJ-0436: "TOTAL WITH AMT" under the regular tax read
            # as the return's total).
            _push(f"  => ESTIMATED TAX ON INVESTMENT INCOME WITH AMT: "
                  f"{money(r['estimated_tax'] + amt.get('topup_attributed', amt['topup']))} "
                  f"{base_cur}")
        if amt["binding"]:
            if details:
                _push(_wrap_note(
                    f"AMT binds because capital gains enter at 100% "
                    f"(vs {_TE.fmt_pct(_TE.CA_INCLUSION)}) and the "
                    f"dividend tax credit is denied. "
                    # The carryover is s.120.2(3)'s additional tax,
                    # not always the excess (backlog TJ-0626).
                    f"The carryover it creates "
                    f"({money(amt['carryforward'])}: the additional "
                    f"tax of s.120.2(3), T691 Part 7 line 14) "
                    f"is creditable against REGULAR tax for 7 years, "
                    f"but recovery needs a future year where regular "
                    f"tax exceeds the minimum — gains-heavy, "
                    f"salary-light years keep hitting AMT instead."))
        else:
            if details:
                _push(_wrap_note(
                    f"AMT recomputes with capital gains at 100% (vs "
                    f"{_TE.fmt_pct(_TE.CA_INCLUSION)}) and the dividend "
                    f"tax credit denied; on "
                    "these numbers regular tax still exceeds the "
                    "minimum, so no top-up is owed."))
        _cy = amt.get("carryover") or {}
        if _cy.get("entered") or _cy.get("created", 0.0) > 0.005:
            _push(f"{_h}MINIMUM TAX CARRYOVER (s.120.2; line 40427)"
                  + (f" — from {_cy['source']}"
                     if _cy.get("source") else ""))
            if _cy.get("entered"):
                _push(f"  {'Available from prior years':<30}"
                      f"{money(_cy.get('available', 0.0)):>14}")
                # The provincial share apart from the surtax it
                # saves (backlog TJ-0016).
                _sur = float(_cy.get("recovered_provincial_surtax")
                             or 0.0)
                _share = _cy.get("recovered_provincial_credit",
                                 _cy.get("recovered_provincial", 0.0))
                # The nominal line 40427 credit, less the foreign tax
                # credit the lower tax limits (backlog TJ-0653).
                from taxjson.lib.tax_estimate import (
                    shown_credits_reduced)
                _red = shown_credits_reduced(_cy)
                _push(f"  {'Recovered this year':<30}"
                      f"{money(_cy.get('recovered', 0.0)):>14}"
                      f"  [federal "
                      f"{money(_cy.get('recovered_federal', 0.0))}"
                      f" + {r['province']} {money(_share)}"
                      + (f" + {r['province']} surtax {money(_sur)}"
                         if abs(_sur) > 0.005 else "")
                      + (f" - foreign tax credit it limits "
                         f"{money(_red)}" if abs(_red) > 0.005
                         else "") + "]")
                if details and abs(_cy.get("attributed", 0.0)
                                   - _cy.get("recovered", 0.0)) > 0.005:
                    _push(_wrap_note(
                        f"The other income alone would recover "
                        f"{money(_cy.get('base_recovered', 0.0))}, so "
                        f"the investment income changes this year's "
                        f"recovery by "
                        f"{money(_cy.get('attributed', 0.0))} — the "
                        f"part counted in the figure below (what is "
                        f"not recovered stays carried forward)."))
                if abs(_cy.get("attributed", 0.0)) > 0.005:
                    _push(f"  => ESTIMATED TAX ON INVESTMENT INCOME "
                          f"WITH AMT AND CARRYOVER: "
                          f"{money(r['estimated_tax_with_amt'])} "
                          f"{base_cur}")
            _push(f"  {'Carried forward':<30}"
                  f"{money(_cy.get('closing', 0.0)):>14}  "
                  f"[`tjs amt` for the detail]")
    return lines


def _ca_trace(r: Dict[str, Any], est: Dict[str, float], other_income: float, _pct15: str,
              _basis: str) -> List[str]:
    """Canada, --verbose: the CALCULATION TRACE, BASE against WITH,
    federal then provincial."""
    from taxjson.lib import tax_estimate as _TE
    from taxjson.lib import out as _out
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    from taxjson.lib.tax_estimate import (CA_FED_BPA,
                                          CA_FED_BRACKETS,
                                          CA_PROVINCES)
    tw, tb = r["trace_with"], r["trace_base"]
    provt = CA_PROVINCES[r["province"]]
    _push(f"\n  CALCULATION TRACE — BASE (other income only) "
          f"vs WITH investments")
    _push(_out.fill(
          f"Taxable income: BASE {money(tb['ti'])} | WITH "
          f"{money(tw['ti'])} = "
          f"{money(other_income + est['pil'] + r.get('staking', 0.0) - r.get('deductions', 0.0) - r.get('carrying_charges', 0.0))}"
          f" ordinary (after deductions) + {money(r['taxable_gain'])} taxable gains"
          f" + {money(r['grossed_eligible'])} grossed dividends"
          f" + {money(est['div_foreign'])} foreign",
          None, "  ", "    "))
    _push(f"  {'FEDERAL':<36}{'BASE':>14}{'WITH':>14}")
    lines += _trace_table_lines(
        _trace_bracket_rows(CA_FED_BRACKETS, tb["ti"], tw["ti"]),
        money)
    lines += _trace_table_lines([
        (f"BPA credit ({M.shown(CA_FED_BPA, 0):,.0f} max @ "
         f"{CA_FED_BRACKETS[0][1] * 100:g}%)",
         -tb["fed_bpa"], -tw["fed_bpa"]),
        (f"DTC {_TE.fmt_pct(_TE.CA_FED_DTC_ELIGIBLE)} x "
         f"{money(r['grossed_eligible'])}",
         -tb["fed_dtc"], -tw["fed_dtc"]),
        # The credit's source, as the summary line says: the
        # actual TAX rows capped at 15%, or the 15% assumption
        # (a fixed "15% x" label contradicted it, R1-224).
        # The federal credit stops at the T2209 limit
        # (backlog TJ-0367): the row says so, with the tax.
        (f"FTC T2209 limit, tax {money(tw['foreign_tax'])}"
         if tw.get("foreign_tax", tw["fed_ftc"])
         - tw["fed_ftc"] > 0.005
         # Rows and the assumption both: the row says so
         # (backlog TJ-0422); the assumption alone is on the
         # dividends less a spin-off's deemed one.
         else (f"FTC TAX rows, max {_pct15} x "
               + money(est['div_foreign'])
               if _basis == "tax_rows"
               else f"FTC rows+assumed, max {_pct15} x "
               + money(est['div_foreign'])
               if _basis == "tax_rows_and_assumed"
               else f"FTC {_pct15} x "
               + money(est['div_foreign']
                       - float(r.get("foreign_div_noncash")
                               or 0.0))),
         -tb["fed_ftc"], -tw["fed_ftc"]),
        ("= FEDERAL", r["tax_base"]["federal"],
         r["tax_with"]["federal"]),
    ], money)
    _push(f"  {r['province']}")
    lines += _trace_table_lines(
        _trace_bracket_rows(provt["brackets"], tb["ti"],
                            tw["ti"]), money)
    lines += _trace_table_lines(
        [(f"BPA credit ({M.shown(provt['bpa'], 0):,.0f} @ "
          f"{provt['brackets'][0][1] * 100:.2f}%)",
          -tb["prov_bpa"], -tw["prov_bpa"]),
         ("= basic tax", tb["prov_basic"], tw["prov_basic"])]
        + [(f"surtax {rate * 100:.0f}% of basic over {M.shown(thr, 0):,.0f}",
            ab, aw)
           for (thr, rate, ab), (_t2, _r2, aw)
           in zip(tb["surtax_parts"], tw["surtax_parts"])]
        + [(f"DTC {provt['dtc_eligible'] * 100:.2f}% x "
            f"{money(r['grossed_eligible'])}",
            -tb["prov_dtc"], -tw["prov_dtc"])]
        + ([("FTC not used federally (T2036)",
             -tb.get("prov_ftc", 0.0), -tw.get("prov_ftc", 0.0))]
           if tw.get("prov_ftc", 0.0) > 0.005 else [])
        + ([("Ontario Health Premium", tb["prov_ohp"],
             tw["prov_ohp"])] if provt.get("health_premium")
           else [])
        + [(f"= {r['province']}", r["tax_base"]["provincial"],
            r["tax_with"]["provincial"])], money)
    lines += _trace_table_lines(
        [("TOTAL", r["tax_base"]["total"],
          r["tax_with"]["total"])], money)
    _push(f"    => WITH - BASE = {money(r['estimated_tax'])} "
          f"estimated tax on investment income"
          + (" (negative: a saving)"
             if r["estimated_tax"] < -0.005 else ""))
    return lines


def _instalment_lines(root: Path, r: Dict[str, Any], year: Any, _h: str) -> List[str]:
    """Canada: the instalments, when the year requires them (the
    [instalments] configuration read first: it may refuse)."""
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    _icfg = _instalment_config(root, year)
    _idoc = _instalments_doc(root, r, year, _icfg)
    if _idoc and _idoc["required_at_all"]:
        _push("")
        _push(f"{_h}INSTALMENTS — {_idoc['basis'].replace('_', '-')} "
              f"option (Mar/Jun/Sep/Dec 15)")
        _push(f"  {'Required this year':<30}"
              f"{money(_idoc['required_total']):>14}")
        _push(f"  {'Paid to date':<30}"
              f"{money(_idoc['paid_total']):>14}")
        _push(f"  {'Net instalment interest':<30}"
              f"{money(_idoc['net_interest']):>14}"
              + (f"  [+ penalty "
                 f"{money(_idoc['penalty'])}]"
                 if _idoc["penalty"] > 0.005 else ""))
        if _idoc["remaining_dates"]:
            _push(f"  {'=> next due ' + _idoc['remaining_dates'][0]:<30}"
                  f"{money(_idoc['per_remaining_date'] or 0.0):>14}"
                  f"  [tjs instalments]")
        else:
            from taxjson.lib.instalment_rates import balance_due_label
            # The instalments still behind, not the balance
            # owed at filing: the year's tax less what was paid
            # is more than this (backlog TJ-0146).
            _push(f"  {'=> instalments behind':<30}"
                  f"{money(_idoc['shortfall']):>14}"
                  f"  [due with the balance "
                  f"{balance_due_label()}; tjs instalments]")
    return lines


def _us_lines(r: Dict[str, Any], est: Dict[str, float], other_income: float,
              other_losses: float, lt_losses: float, year: Any, base_cur: str, details: bool,
              _g: str, RATE_VINTAGE: Any) -> List[str]:
    """US: the title and the legend, the figure rows, the tax lines and
    the capital loss carryovers."""
    from taxjson.lib import tax_estimate as _TE
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    st_in, lt_in = r["st_input"], r["lt_input"]
    _push(f"TAX ESTIMATE — usa (single, standard deduction), rates "
          f"vintage {RATE_VINTAGE}")
    if not details:
        _push("ESTIMATE ONLY, not filing numbers: taxable accounts "
              "only; [ ] says how an amount was built.")
    else:
        _push("ESTIMATE ONLY, not filing numbers; taxable accounts "
              "only.")
        _push("")
    rows = [
        ("Other income", other_income, ""),
        ("Short-term gains (net)", r["st_net"],
         f"[{money(st_in)} before carryovers; short-term "
         f"carryover {money(r.get('carryover_short_term', 0.0))}]",
         f"[{money(st_in)} less carryover "
         f"{money(r.get('carryover_short_term', 0.0))}]"),
        ("Long-term gains (net)", r["lt_net"],
         f"[{money(lt_in)} before carryovers; long-term "
         f"carryover {money(r.get('carryover_long_term', 0.0))}]",
         f"[{money(lt_in)} less carryover "
         f"{money(r.get('carryover_long_term', 0.0))}]"),
        ("Qualified dividends",
         est["div_ca"] + est["div_foreign"], ""),
        # What the computation counts: compensation paid on a short
        # is not subtracted (backlog TJ-1318).
        ("Payments in lieu",
         est["pil"] + float(r.get("pil_paid_not_deducted") or 0.0),
         "[ordinary]"),
    ] + ([("Paid on short sales", -float(r["pil_paid_not_deducted"]),
           "[not deducted: Schedule A only]")]
         if r.get("pil_paid_not_deducted") else []) + (
        [("Crypto staking", est["staking"], "[ordinary]")]
        if est.get("staking") else [])
    for label, amt, note, *short in rows:
        lines += _est_row_lines(details, label, amt, note, *short)
    _push("")
    _push(f"  Tax with investments:{_g}{money(r['tax_with']['total'])}"
          f" + NIIT {money(r['niit'])}")
    _push(f"  Tax on other income alone:{_g}"
          f"{money(r['tax_base']['total'])}")
    _push(f"  => ESTIMATED TAX ON INVESTMENT INCOME: "
          f"{money(r['estimated_tax'])} {base_cur}"
          + (f"  ({r['avg_rate_pct']:.1f}% of "
             f"{money(r['investment_income'])})"
             if r["avg_rate_pct"] is not None else ""))
    if r["ordinary_offset"]:
        _push(f"  Ordinary income offset by losses:{_g}"
              f"{money(r['ordinary_offset'])}{_g}(max "
              f"{M.shown(_TE.US_ORDINARY_LOSS_CAP, 0):,.0f})")
    if r["losses_unused"]:
        _push(f"  Unused capital losses:{_g}{money(r['losses_unused'])}"
              f"{_g}(carry forward"
              + (f"; {money(r['offset_used_for_carryover'])} deducted: "
                 f"[carryover] claimed {year}"
                 if r.get("offset_used_from") == "claimed" else "")
              + ")")
    _ls = (r.get("carry_sources") or {}).get("other_losses")
    if _ls and (other_losses or lt_losses):
        _push(f"  Capital loss carryovers in:{_g}short-term "
              f"{money(other_losses)}, long-term {money(lt_losses)}"
              f" — from {_ls}")
    elif not _ls and year:
        # No lock before the year carried one and none was entered:
        # the 0 is the default, said, and where the books' figure is
        # (backlog TJ-0470).
        _push(f"  Capital loss carryovers in:{_g}{money(0.0)} — no "
              f"close-year lock before {year} and none entered: "
              f"see `tjs carryover`")
    return lines


def _us_trace(r: Dict[str, Any], other_income: float) -> List[str]:
    """US, --verbose: the CALCULATION TRACE, the ordinary and the
    stacked preferential brackets, the ceiling and the NIIT."""
    from taxjson.lib import tax_estimate as _TE
    from taxjson.lib import out as _out
    money = fmt_money
    lines: List[str] = []
    _push = lines.append
    from taxjson.lib.tax_estimate import (US_NIIT_MAGI_THRESHOLD,
                                          US_ORD_BRACKETS,
                                          US_STD_DEDUCTION,
                                          stacked_slices)
    tw, tb = r["trace_with"], r["trace_base"]
    _push(f"\n  CALCULATION TRACE — BASE (other income only) "
          f"vs WITH investments")
    _push(f"  Ordinary taxable: BASE {money(tb['ord_taxable'])} "
          f"({money(other_income)} - "
          f"{money(US_STD_DEDUCTION)} std deduction) | WITH "
          f"{money(tw['ord_taxable'])}")
    _push(f"  {'ORDINARY':<36}{'BASE':>14}{'WITH':>14}")
    lines += _trace_table_lines(
        _trace_bracket_rows(US_ORD_BRACKETS, tb["ord_taxable"],
                            tw["ord_taxable"]), money)
    lines += _trace_table_lines(
        [("= ORDINARY", r["tax_base"]["ordinary"],
          r["tax_with"]["ordinary"])], money)
    _push(f"  PREFERENTIAL — {money(tw['pref_taxable'])} "
          f"(LT + qualified) STACKED from "
          f"{money(tw['ord_taxable'])}:")
    from taxjson.lib.tax_estimate import US_LTCG_BRACKETS
    for lo, hi, rate, tax in stacked_slices(
            tw["ord_taxable"], tw["pref_taxable"],
            US_LTCG_BRACKETS):
        hi_s = ("inf" if hi == float("inf") else f"{M.shown(hi, 0):,.0f}")
        _push(f"    {f'{M.shown(lo, 0):,.0f}-{hi_s} @ {rate * 100:.0f}%':<34}"
              f"{'':>14}{money(tax):>14}")
    if tw.get("ceiling"):
        # The worksheet's last lines (backlog TJ-0509): all of
        # the taxable income at the ordinary rates is less.
        _push(_out.fill(
              f"Ceiling (Pub 505 Worksheet 2-7 lines 39-40): "
              f"{money(tw['ord_taxable'] + tw['pref_taxable'])} "
              f"all at the ordinary rates is "
              f"{money(tw['all_ordinary'])}, less than "
              f"{money(r['tax_with']['ordinary'])} + "
              f"{money(tw['pref_stacked'])} stacked: the tax is "
              f"the ceiling", None, "  ", "    "))
        lines += _trace_table_lines(
            [("= PREFERENTIAL", r["tax_base"]["preferential"],
              r["tax_with"]["preferential"])], money)
    _niit = _TE.fmt_pct(_TE.US_NIIT_RATE)
    _push(_out.fill(
          f"NIIT: {_niit} x min(investment income, MAGI "
          f"{money(r['magi'])} - "
          f"{money(US_NIIT_MAGI_THRESHOLD)}) = "
          f"{_niit} x {money(r['niit_base'])} = {money(r['niit'])}",
          None, "  ", "    "))
    lines += _trace_table_lines(
        [("TOTAL (before NIIT)", r["tax_base"]["total"],
          r["tax_with"]["total"])], money)
    _push(f"    => WITH - BASE + NIIT = "
          f"{money(r['estimated_tax'])} estimated tax on "
          f"investment income")
    return lines


def _print_tax_estimate(cfg: Dict[str, Any], est: Dict[str, float],
                        base_cur: str, *, other_income: float,
                        other_losses: float,
                        province: Optional[str],
                        deductions: float = 0.0,
                        carrying_charges: float = 0.0,
                        lt_losses: float = 0.0,
                        verbose: bool = False,
                        actual_withheld: Optional[float] = None,
                        root: Optional[Path] = None,
                        year: Optional[Any] = None,
                        amt_carryover: Optional[Dict[int, float]] = None,
                        carry_sources: Optional[Dict[str, Any]] = None,
                        withheld_assumed: float = 0.0,
                        foreign_div_noncash: float = 0.0,
                        withholding_by_security=None
                        ) -> None:
    """Marginal tax-estimate block under `tjs sum` — TAXABLE
    accounts only, incremental on top of --other-income. Assumptions
    are printed with the numbers; these are never filing figures.
    `verbose` appends the CALCULATION TRACE: every bracket slice,
    credit and surtax tier for the base and with-investments runs.
    Every printed rate is read from lib/tax_estimate's constants (the
    ones tax-logic states), never a literal. Each part returns its
    lines and is printed as soon as it is built: the carry-back's
    warning and an [instalments] refusal follow the lines before them.
    """
    r = _tax_estimate_result(cfg, est, other_income=other_income,
                             other_losses=other_losses, province=province,
                             actual_withheld=actual_withheld,
                             withheld_assumed=withheld_assumed,
                             foreign_div_noncash=foreign_div_noncash,
                             withholding_by_security=withholding_by_security,
                             deductions=deductions,
                             carrying_charges=carrying_charges,
                             lt_losses=lt_losses,
                             amt_carryover=amt_carryover,
                             carry_sources=carry_sources)
    # The result carries the vintage apply_vintage() actually selected
    # for the project year — never the import-time module default.
    RATE_VINTAGE = r.get("vintage", "?")
    # Essentials first (docs/output-style.md): the figures and a legend;
    # the notes, the assumptions and the AMT explanation with --details
    # (the --verbose calculation trace is the most detailed view).
    details = run_state.current_details or verbose
    # The default view's figure lines keep a column gap after the label
    # and its sub-headings start the line, so they read as a block of
    # figures (lib/out.classify); --details keeps the long layout.
    _g = " " if details else "  "
    _h = "  " if details else ""

    def _put(part_lines: List[str]) -> None:
        for _ln in part_lines:
            print(_ln)

    print()
    if r["country"] == "canada":
        lines, _pct15, _basis = _ca_rows(r, est, other_income, details, RATE_VINTAGE)
        _put(lines)
        _put(_ca_tax_lines(r, base_cur, details))
        _put(_ca_losses_lines(r, root, cfg, year, other_losses, _g))
        _put(_ca_amt_lines(r, base_cur, details, _h))
        if verbose:
            _put(_ca_trace(r, est, other_income, _pct15, _basis))
        if root is not None:
            _put(_instalment_lines(root, r, year, _h))
        # The trust gross-up is stated on the default view too
        # (owner decision A2-0828, CA-EST-TRUST).
        _put(_estimate_tail(r, year, details, "Assumes Canadian dividends (trusts' too) "
                            "eligible; no interest, FX on cash or slip gains"))
        return
    _put(_us_lines(r, est, other_income, other_losses, lt_losses, year, base_cur, details, _g,
                   RATE_VINTAGE))
    if verbose:
        _put(_us_trace(r, other_income))
    _put(_estimate_tail(r, year, details, "Assumes single filer, standard deduction, "
                        "qualified dividends; no state tax, no §988 FX",
                        "Assumes: single filer, standard deduction, all "
                        "dividends QUALIFIED, no foreign tax credit, no state "
                        "tax; §1256 contracts (futures, index options) split "
                        "60/40 long-/short-term (Form 6781), with no year-end "
                        "marking or loss carryback; interest income and the "
                        "§988 result on foreign currency (`tjs fx-cash`) "
                        "not included."))


def _carry_back_or_warn(root: Optional[Path], cfg: Dict[str, Any], year: Any,
                        est: Dict[str, Any]) -> CarryBack:
    """Of the estimate's unused capital losses (Canada), the part
    [carryover] claimed carries back (T1A), as `tjs carryover` folds
    it: what the estimate prints and its --json carries
    (`losses_carried_back`; backlog TJ-0084, TJ-0226). 0.0 with no
    claim under an earlier year (the ledger is not built then); `unknown`,
    with a warning, when the ledger that decides it cannot be used
    (backlog TJ-1422). A warning too when it left sales out (TJ-1436). The
    headlines hold the command and the reason, in that order: the default
    view drops details and cuts a line at the width (TJ-1443). `losses_unused`
    and not `losses_created`: a carried-in balance can carry back in a year
    with no loss, when the lock before it recorded none (TJ-1454)."""
    from taxjson.lib import out as _out
    from taxjson.lib.carry_back import left_out_text
    unused = est["losses_unused"]
    stale = None if root is None else _stale_opening(
        root, (est.get("carry_sources") or {}).get("other_losses"))
    if stale:
        # The balance carried in is a stale lock's: no carry-forward to
        # print or lock, whatever the claims (backlog TJ-4883).
        _out.warn(f"the net capital loss carried in is a stale lock's: {stale}",
                  prog=_cmd_prog())
        return CarryBack(unknown=stale)
    if not unused or root is None or not _claims_back(cfg, year):
        return CarryBack()
    try:
        cb = _carry_back(root, int(year),
                         float(est.get("losses_created") or 0.0),
                         balance=float(unused),
                         opening=est.get("losses_opening") or 0.0,
                         typed=_typed_opening(
                             (est.get("carry_sources") or {}).get("other_losses")))
    except LedgerUnavailable as e:
        _out.warn(f"carry-back of this year's loss unknown (`tjs carryover`): "
                  f"{e}", prog=_cmd_prog())
        return CarryBack(unknown=str(e))
    left_out = _left_out_shown(root, cfg, year, cb)
    if left_out:
        _out.warn(f"carry-back of this year's loss may change (`tjs "
                  f"carryover`): sales with an unknown cost left out, "
                  f"{left_out_text(left_out)}", prog=_cmd_prog(),
                  details=["`tjs close-year` stops on this until they have a "
                           "cost, or locks the split with --force."])
    return cb._replace(amount=min(cb.amount, unused))


def _left_out_shown(root: Optional[Path], cfg: Dict[str, Any], year: Any,
                    cb: CarryBack) -> Any:
    """`cb.incomplete` as a person should read it: the return in the year's own
    lock holds the year's own sales, so finding a cost for one moves nothing
    (backlog TJ-1490). `--json` keeps all of it: a plain re-close folds on the
    books, and stops on it."""
    from taxjson.lib.carry_back import filed_left_out
    if cb.incomplete and _own_filed_lock(root, cfg, year):
        return filed_left_out(cb.incomplete, int(year))
    return cb.incomplete


def _filed_lock_line(root: Optional[Path], cfg: Dict[str, Any], year: Any,
                     money: Any, gap: str) -> str:
    """A line beside the books' split, which matches no return when the year's
    own lock records another (backlog TJ-1489)."""
    ncl = _own_filed_lock(root, cfg, year)
    if not ncl or "filed_gain" not in ncl:
        return ""
    return (f"  Locked for {year}:{gap}{money(float(ncl.get('closing') or 0.0))}"
            f"{gap}(carry forward; "
            f"{money(float(ncl.get('carried_back') or 0.0))} carried back: "
            f"the return filed with another tool)")


def _unused_losses_line(root: Optional[Path], cfg: Dict[str, Any], year: Any,
                        est: Dict[str, Any], money: Any,
                        gap: str) -> Optional[str]:
    """The estimate's "Unused capital losses" line, None with no loss.
    It never calls the whole loss carry-forward without knowing: a pipe
    keeps this and not the warning (backlog TJ-1423, TJ-1436). The reason
    goes on a line of its own, for the width (backlog TJ-1466)."""
    from taxjson.lib.carry_back import left_out_text
    unused = est["losses_unused"]
    cb = _carry_back_or_warn(root, cfg, year, est)
    if cb.unknown:
        return (f"  Unused capital losses:{gap}{money(unused)}{gap}(before "
                f"any carry-back; the carry-forward is unknown)\n"
                + ("    The lock the loss carried in came from is stale: "
                   "`tjs carryover`" if "was not closed again" in cb.unknown
                   else "    The carryover ledger could not be used: `tjs "
                        "carryover`"))
    shown = _left_out_shown(root, cfg, year, cb)
    left_out = (f"\n    may change: sales with an unknown cost left out, "
                f"{left_out_text(shown)}" if shown else "")
    filed = _filed_lock_line(root, cfg, year, money, gap)
    if filed:
        left_out += f"\n{filed}"
    if cb.amount > 0.005:
        return (f"  Unused capital losses:{gap}{money(unused - cb.amount)}"
                f"{gap}(carry forward; {money(cb.amount)} carried back: "
                f"[carryover] claimed, T1A){left_out}")
    if unused:
        return (f"  Unused capital losses:{gap}{money(unused)}{gap}"
                f"(carry forward){left_out}")
    return filed or None    # no loss in the books, one in the return (TJ-1496)


def _add_carry_back_split(root: Optional[Path], cfg: Dict[str, Any],
                          year: Any, est: Dict[str, Any]) -> None:
    """The two figures `estimate --json` carries beside `losses_unused`.
    When the ledger could not be used they are left out, not 0, and
    `carry_back_unknown` says why: close-year's lock reads them (backlog
    TJ-1422, TJ-1472). `carry_back_incomplete`: the ledger left sales out, and
    close-year stops on it (backlog TJ-1436)."""
    cb = _carry_back_or_warn(root, cfg, year, est)
    if cb.unknown:
        est["carry_back_unknown"] = cb.unknown
        return
    est["losses_carried_back"] = M.shown(cb.amount, 2) + 0.0
    est["losses_carried_forward"] = M.shown(
        est["losses_unused"] - cb.amount, 2) + 0.0
    if cb.incomplete:
        est["carry_back_incomplete"] = cb.incomplete
    if cb.stated:
        est["carry_back_stated"] = cb.stated
    ncl = _own_filed_lock(root, cfg, year)
    if ncl and "filed_gain" in ncl:
        # These figures are the books'. What the lock holds, beside them.
        est["locked_split"] = {
            "carried_back": float(ncl.get("carried_back") or 0.0),
            "carried_forward": float(ncl.get("closing") or 0.0)}


def _fx_cash_status(root: Path, cache: Path,
                    ledger: Optional[str] = None) -> Dict[str, Any]:
    """The FX-on-cash result every output shares (sum, fx-cash --json,
    checklist, the end-of-run note): `reliable`, `headline` (the one
    line shown in place of a reportable figure), `reasons`, and the
    figures only where they may be quoted. v1 (default): NOT RELIABLE,
    the raw numbers under `unreliable_raw`. v2 (opt-in): `computed`
    (labelled under audit) or `not_computed` with its problems."""
    from taxjson.bin import taxjson_fx_cash as FX
    settings = load_settings(root, missing_ok=True)
    which = _fx_cash_ledger_choice(settings, ledger)
    if which == "v2":
        return _fx_cash_v2_status(root, cache)
    doc, verdict, base, year, country = _fx_cash_doc(root, cache)
    st = FX.unreliable_status(doc, year, verdict)
    st.update(year=year, currency=base, rule=verdict.get("rule"))
    return st


def _fx_sum_item(st: Dict[str, Any], money) -> str:
    """`tjs sum` FOR THE RETURN's FX-on-cash line."""
    if st.get("ledger") == "v2" and st.get("status") == "computed":
        return (f"FX on foreign cash, ledger {st.get('label')}, "
                f"s.39(1.1): net {money(st['net_gain'])}, reportable "
                f"{money(st['reportable'])} after the $200 exemption — not "
                f"in the rows above; T4037 puts it on line 15300. Review "
                f"`tjs fx-cash` and mark the checklist's fx-cash step "
                f"reviewed before using it.")
    if st.get("ledger") == "v2":
        return (st["headline"] + " (`tjs fx-cash` lists each "
                "problem).")
    return (st["headline"] + " (line 15300; `tjs fx-cash` shows the "
            "raw ledger, and the opt-in ledger v2 reads the missing "
            "cash events).")


def _fx_cash_v2_status(root: Path, cache: Path) -> Dict[str, Any]:
    """The ledger v2's status (`_fx_cash_status`); its refusal (SystemExit)
    when the books are older than the inputs, as the ledger's own."""
    from taxjson.lib import fx_cash_v2 as V2
    _refuse_cash_older_than_inputs(root, load_config(root, missing_ok=True))
    doc, verdict, _cash, base, year, _country = _fx_cash_v2_doc(root, cache)
    st = {"ledger": "v2", "label": V2.LABEL, "status": doc["status"],
          "reliable": doc["status"] == "computed", "under_audit": True,
          "headline": V2.headline(doc, verdict),
          "reasons": [p["text"] for p in doc["problems"]],
          "problems": doc["problems"], "year": year, "currency": base}
    if verdict is not None:
        st.update(net_gain=doc["net_gain"],
                  reportable=verdict["reportable"], rule=verdict["rule"],
                  review_key=f"v2|{year}|{M.units(verdict['reportable'], 2):.2f}")
    return st


def _register_sum(sub: argparse._SubParsersAction) -> None:
    p_sum = sub.add_parser(
        "sum",
        help="The year's realized gains, by account and form line",
        description="Cross-account realized-gains summary for the tax "
                    "year (base currency, one row per account): "
                    "TAXABLE / SHELTERED / ALL tables when both types "
                    "exist, one table otherwise, and the FOR THE RETURN "
                    "block by form line; --other-income/--other-losses "
                    "add a marginal tax ESTIMATE.")
    p_sum.add_argument("--other-income", type=float, default=None,
                       metavar="AMT",
                       help="Non-investment income (employment etc.) the "
                            "investment income stacks on top of — turns "
                            "on the tax-estimate block")
    p_sum.add_argument("--other-losses", type=float, default=None,
                       metavar="AMT", help=_OTHER_LOSSES_HELP
                       + " — turns on the tax-estimate block")
    for _p in (p_sum,):
        _add_deduction_flags(_p)
    p_sum.add_argument("account", nargs="?",
                       help="Account (default: all accounts). No "
                            "PERIOD here by design: sum reports the "
                            "tax year's filing basis from the built "
                            "artifacts — windowed views are "
                            "winners/ccd-sum/leaps-sum.")
    p_sum.add_argument("--province", default=None, metavar="PROV",
                       help="Province for the canada estimate (ON/BC/AB; "
                            "default: `province` under [settings])")
    p_sum.add_argument("--verbose", "-v", action="store_true",
                       help="With the estimate: print the CALCULATION "
                            "TRACE — every bracket slice, credit and "
                            "surtax tier, base vs with-investments")
    p_sum.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_sum.set_defaults(func=cmd_summary)


def _register_estimate(sub: argparse._SubParsersAction) -> None:
    p_est = sub.add_parser(
        "estimate",
        help="Estimate the tax on the year's investment income",
        description="ESTIMATE the tax on the year's investment income — "
             "tax(other income + investment income) minus tax(other "
             "income), bracketed on top of what you already earn "
             "(Canada: 50% inclusion, eligible gross-up/DTC, FTC "
             "from the books' actual TAX rows, the minimum tax check "
             "and any carryover; taxable accounts only). Planning "
             "numbers, never filing numbers.")
    p_est.add_argument("--other-income", type=float, default=None,
                       metavar="AMT",
                       help="Employment/other income the investment "
                            "income stacks on top of (default: "
                            "[estimate] other_income, else 0)")
    p_est.add_argument("--other-losses", type=float, default=None,
                       metavar="AMT", help=_OTHER_LOSSES_HELP
                       + " (default: [estimate] other_losses, else the "
                         "carry-forward of the latest close-year lock "
                         "before the year, else 0)")
    _add_deduction_flags(p_est)
    p_est.add_argument("--province", default=None,
                       help="Canada: ON|BC|AB (default: `province` "
                            "under [settings])")
    # close-year's hand-off (with --json): the federal carry-forwards
    # of a Canadian project with no supported province. Not a user
    # option: the printed estimate needs a province.
    p_est.add_argument("--federal-only", action="store_true",
                       help=argparse.SUPPRESS)
    p_est.add_argument("--verbose", "-v", action="store_true",
                       help="Full CALCULATION TRACE: every bracket "
                            "slice, credit and surtax tier for the "
                            "base and with-investments runs")
    p_est.add_argument("--json", action="store_true",
                       help="Emit the full document as JSON (accounts, "
                            "totals, estimate, and instalments when "
                            "configured)")
    p_est.set_defaults(func=cmd_estimate)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_sum(sub)
    _register_estimate(sub)
