"""The Totals by type commands: ccd-sum, dil-sum, divs-sum, fees-sum, leaps-sum, losers, roc-sum, staking-sum, trades-sum, winners."""
import argparse
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from taxjson.project.accounts import accounts_of
from taxjson.lib.pipeline import premium_timing
from taxjson.lib import project_layout as _PL
from taxjson.lib.country import is_usa
from taxjson.model.result import GainRow
from taxjson.model import money as M
from taxjson.lib.report_fmt import fmt_money
from taxjson.lib import out as _out

from taxjson.cli.common import (
    _account_group_of, _base_currency, _box18_fractions, _CAPITALIZED_LBL,
    _capitalized_pil_shares, _collect_period_txs, _discover_tx_accounts,
    _dist_adjust_rows, _exec_tool, _foot, _gains_all_file, _gains_row_date,
    _ISO_DATE_RE, _leaps_closed, _leaps_contracts, _leaps_empty_doc,
    _leaps_rule, _leaps_scope_guard, _load_json_or_die, _lone_positional,
    _period_end, _print_scope_split, _real_world_legs, _require_books, _scope_split,
    _settle_basis, _split_pool_of, _staking_view, _TAX_YEAR_TOKENS,
    _trade_total, _tx_fee, _tx_period_cutoff, _view_income_rules, _view_msg,
    _view_window, _vprint, _warn_accounts_without_books, _warn_bad_dates,
    _warn_dist_double_entry, _window_gains_files, _YEAR_TOKEN_RE,
    _PERIOD_HELP, _add_staking_parser,
)
from taxjson.pipeline.config import _country, load_config, load_settings
from taxjson.pipeline.inputs import _base
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.run import _native_tx_file
from taxjson.pipeline.sanity import _json_out, _print_report_table
from taxjson.pipeline.stages import (
    _read_work_doc,
)
from taxjson.pipeline.tools import _cmd

# The views' print() calls (PLAN step 15): a one-line message is `out.put`;
# the rest are the ops of a live exact Doc, `out.Doc(exact=True, width_=0,
# live=True)`, each printed when it is added (`page.line(e)` is `print(e)`,
# `page.blank()` is `print()`).


def cmd_leaps_sum(args: argparse.Namespace) -> None:
    """Realized-gain summary for closed LEAPS positions over a window
    (default: the tax year): per contract — quantity closed, proceeds,
    cost, gain, expiry — plus the total. A contract qualifies via its
    FULL-history entry buy (>leaps_months to expiry), so in-window exits of
    older entries are included."""
    from taxjson.lib.option_symbols import parse_option_expiry, parse_option_underlying
    root = Path(args.dir).resolve()
    # `leaps-sum margin` → the lone positional is an account, not a window.
    keep, scope, account = _view_window(args, root)
    _files = _leaps_scope_guard(root, account, args)
    leaps = _leaps_contracts(root, account, "leaps-sum")
    if not leaps:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account), schema="leaps-sum")
            return
        _out.put(f"No LEAPS contracts found ({_leaps_rule(root)}).")
        return
    entries, found, basis = _leaps_closed(root, account, leaps, keep,
                                          _files)
    if not found:
        _die_input("no gains files in work/ — run `tjs run` first",
                   "Gains come from the engine.")
    if not entries:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account, basis), schema="leaps-sum")
            return
        _out.put(f"No closed LEAPS positions in {scope}.")
        return
    base_cur = _base_currency(root)

    money = fmt_money               # shared report-layer formatter

    views = [(a, GainRow(e)) for a, e in entries]
    _split = _scope_split(root, [(a, v.gain) for a, v in views])
    agg: Dict[str, Dict[str, float]] = {}
    for _acct, v in views:
        sym = str(v.get("symbol") or "?")
        rec = agg.setdefault(sym, {"qty": 0.0, "proceeds": 0.0,
                                   "cost": 0.0, "gain": 0.0})
        rec["qty"] += abs(float(v.quantity or 0.0))
        rec["proceeds"] += float(v.proceeds or 0.0)
        rec["cost"] += float(v.cost or 0.0)
        rec["gain"] += float(v.gain or 0.0)
    out_lines = ["CONTRACT EXPIRY QTY_CLOSED PROCEEDS COST GAIN"]
    total = 0.0
    def sort_key(item):
        sym, _rec = item
        return (parse_option_underlying(sym) or sym,
                parse_option_expiry(sym) or "", sym)

    if getattr(args, "json", False):
        _json_out({"rows": [dict(rec, contract=sym,
                                 expiry=parse_option_expiry(sym))
                            for sym, rec in sorted(agg.items(),
                                                   key=sort_key)],
                   "total_gain": _foot(r2["gain"]
                                       for r2 in agg.values()),
                   "taxable_gain": _split["taxable"],
                   "sheltered_gain": _split["sheltered"],
                   "currency": base_cur, "basis": basis}, schema="leaps-sum")
        return
    for sym, rec in sorted(agg.items(), key=sort_key):
        total += M.shown(rec["gain"], 2)
        out_lines.append(" ".join([
            sym, parse_option_expiry(sym) or "?", f"{rec['qty']:g}",
            money(rec["proceeds"]), money(rec["cost"]),
            money(rec["gain"])]))
    _vprint(f"LEAPS REALIZED GAINS — {scope} ({base_cur}; "
            f"{_leaps_rule(root)})")
    _vprint(f"Allowed amounts, lot-matched, basis: {basis}; closed "
            f"portions only, no mark-to-market on open contracts.")
    # Too wide (a long contract symbol): the expiry goes first (the
    # contract symbol carries it).
    _print_report_table(out_lines, fit=True, drop=(1,))
    _out.put(f"\nTOTAL REALIZED GAIN: {money(total)} {base_cur}")
    _print_scope_split(_split, base_cur, root)


def _earlier_grant_expiries(root: Path, cache: Path, acct: str,
                            data: Dict[str, Any], keep: Callable[[str], bool],
                            all_history: bool, splits: list
                            ) -> List[Tuple[Dict[str, Any], float, int]]:
    """[(write's gains row, contracts expired, closing records to add)]
    of the grant-timing writes in an earlier year's books whose expiry
    falls in the window `keep` of a view that reads one tax year's gains
    (`data`): that file holds neither the write nor a record of its
    expiry, so the close was counted in no single-year view (backlog
    TJ-1086). A write that file's own pass counts already is its one
    record: only the contracts are added (TJ-1308). Read from the
    full-history file, built on demand: only when the account's book
    holds a sold option of an earlier year expiring in the tax year or
    after it, in a project on grant timing."""
    import json as _json
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_expiry
    from taxjson.lib.grant_writes import grant_write_expiries
    year = str((data.get("summary") or {}).get("year") or "")
    settings = load_settings(root, missing_ok=True)
    if (all_history or not re.fullmatch(r"\d{4}", year)
            or _country(settings) == "usa"
            or premium_timing(settings) != "grant"):
        return []
    start = f"{year}-01-01"
    bpath = cache / f"{acct}_base.json"
    if not bpath.is_file():
        return []
    try:
        base = _read_work_doc(bpath).get("transactions") or []
    except (OSError, ValueError):
        return []
    if not any(isinstance(t, dict)
               and t.get("action") == "BUYSELL"
               and float(t.get("quantity") or 0.0) < 0
               and str(t.get("date") or "") < start
               and is_option_symbol(str(t.get("symbol") or ""))
               and str(parse_option_expiry(str(t.get("symbol"))) or "") >= start
               for t in base):
        return []
    path = _gains_all_file(root, cache, acct, settings)
    if path is None:
        return []
    try:
        doc = _json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []

    def row_key(t: Dict[str, Any]) -> Tuple[str, str, str]:
        return (str(t.get("id") or ""), str(t.get("date") or ""),
                str(t.get("symbol") or ""))
    own_txs = data.get("transactions") or []
    own = {row_key(t) for t in own_txs
           if isinstance(t, dict) and t.get("grant")}
    # What the year's own pass counts of its writes: the two passes pair
    # a buy-back with different writes when a series was written in both
    # years, so a write of the year can have expired in the full history
    # alone (backlog TJ-1256).
    own_counted = {
        row_key(own_txs[i]): q
        for i, (q, _e) in grant_write_expiries(
            own_txs, data.get("inventory") or [], year, splits).items()}
    txs = doc.get("transactions") or []
    out: List[Tuple[Dict[str, Any], float, int]] = []
    for i, (q, exp) in grant_write_expiries(
            txs, doc.get("inventory") or [], None, splits).items():
        t = txs[i]
        if not (t.get("grant") and not t.get("tainted")
                and _ISO_DATE_RE.match(exp) and keep(exp)):
            continue
        k = row_key(t)
        counted = own_counted.get(k, 0.0) if k in own else 0.0
        q -= counted
        if q > 1e-9:
            out.append((t, q, 0 if counted > 1e-9 else 1))
    return out


def _ccd_gains(resolved: Dict[str, Path], cache: Path, root: Path, keep: Callable[[str], bool],
               _all_history: bool, parse_option_right: Callable[[str], Any],
               is_option_symbol: Callable[[str], Any]
               ) -> Tuple[Dict[str, Dict[str, float]], List[Tuple[str, float]], int]:
    """ccd-sum's covered-call gains: each underlying's short-call closes,
    contracts, premium, buyback and gain in the window (a grant-timing
    write closed by its expiry, an earlier year's write expiring in the
    window; older gains files' direction inferred), the class share found
    among every listing the accounts hold or traded; the rows with an
    unknown cost counted. Returns the underlyings, each close's account
    and gain, and the unknown-cost count (cmd_ccd_sum)."""
    agg: Dict[str, Dict[str, float]] = {}
    _ccd_pairs: List[Tuple[str, float]] = []
    tainted_skipped = 0
    from taxjson.lib.ticker_map import class_share_aliases, underlying_of
    _docs = [(_a, _load_json_or_die(_f)) for _a, _f in resolved.items()]
    # The class share is found among every listing the accounts hold or
    # traded (inventory and native books), not only the year's sales: a
    # A class-share call written on held, unsold SAMPLD.B.TO shares was headed
    # SAMPLD.TO while the holdings TOML said SAMPLD.B.TO (audit A2-1115).
    _alias_syms = [t.get("symbol") for _a, _d in _docs
                   for t in (_d.get("transactions", []) or [])
                   + (_d.get("inventory", []) or [])
                   if isinstance(t, dict)]
    for _a in resolved:
        _nf = _native_tx_file(cache, _a)
        if _nf is not None:
            _alias_syms += [t.get("symbol") for t in
                            (_load_json_or_die(_nf).get("transactions")
                             or []) if isinstance(t, dict)]
    _aliases = class_share_aliases(_alias_syms)
    from taxjson.lib.grant_writes import grant_write_expiries
    _osplits = _option_split_rows(root, cache, [a for a, _d in _docs])
    for acct, data in _docs:
        # A write in an earlier year's books that expired in the window
        # counts its close here, with no premium (backlog TJ-1086).
        for t, _q, _rec in _earlier_grant_expiries(root, cache, acct, data,
                                                   keep, _all_history,
                                                   _osplits.get(acct, [])):
            sym = str(t.get("symbol") or "")
            if parse_option_right(sym) != "C" \
                    or t.get("direction") != "SHORT":
                continue
            rec = agg.setdefault(underlying_of(sym, _aliases),
                                 {"contracts": 0, "qty": 0.0,
                                  "proceeds": 0.0, "cost": 0.0, "gain": 0.0})
            rec["contracts"] += _rec
            rec["qty"] += _q
        # An expired grant-timing write has no closing record: the
        # WRITE stands for its close (A2-0693), dated at the expiry
        # (backlog TJ-0778), through the series' renames (TJ-0903).
        _gclose = grant_write_expiries(
            data.get("transactions") or [], data.get("inventory") or [],
            (data.get("summary") or {}).get("year"),
            _osplits.get(acct, []))
        _settle = _settle_basis(root, data)
        # Routed unknown-cost rows (manual_reporting_required) are
        # tainted too — counted, never silent (audit S040-15 sibling).
        for t in data.get("manual_reporting_required") or []:
            sym = str(t.get("symbol") or "")
            d = _gains_row_date(t, keep, _settle)
            if (is_option_symbol(sym) and parse_option_right(sym) == "C"
                    and _ISO_DATE_RE.match(d) and keep(d)):
                tainted_skipped += 1
        for _ti, t in enumerate(data.get("transactions", [])):
            sym = str(t.get("symbol") or "")
            if not is_option_symbol(sym):
                continue
            if parse_option_right(sym) != "C":
                continue
            d = _gains_row_date(t, keep, _settle)
            in_window = bool(_ISO_DATE_RE.match(d) and keep(d))
            # A grant-timing write that expired: its premium is the
            # write's (its date), its close the expiry's — an expiry in
            # the window counts there even when the write is before it
            # (backlog TJ-0778).
            _exp = _gclose[_ti][1] if (t.get("grant")
                                       and _ti in _gclose) else ""
            expired_in = bool(_exp and _ISO_DATE_RE.match(_exp)
                              and keep(_exp))
            if not in_window and not expired_in:
                continue
            if t.get("tainted"):
                # Unknown-cost rows are excluded by every filing-
                # facing consumer; counting them here fabricated
                # premium totals (REVIEW #2 sibling).
                if in_window:
                    tainted_skipped += 1
                continue
            # SHORT (sold-to-open) legs only — covered-call premium.
            # Older gains files carry no `direction`; infer it the way
            # taxjson-ccd-gains does: a short's gain is cost-vs-proceeds
            # inverted.
            v = GainRow(t)
            cost, proceeds, gain = (float(x or 0) for x in (v.cost, v.proceeds, v.gain))
            direction = t.get("direction")
            inferred = direction is None
            if inferred:
                # Older gains files: shorts satisfy gain == cost -
                # proceeds (fields carry the legs swapped) — same
                # heuristic taxjson-ccd-gains uses. A BREAK-EVEN row
                # satisfies both orientations; classify it LONG
                # (excluded) rather than fabricate a covered-call
                # close with invented premium/buyback totals (2026-09
                # audit).
                direction = ("SHORT"
                             if abs(gain - (cost - proceeds)) < 0.01
                             and abs(gain) >= 0.01
                             else "LONG")
            if direction != "SHORT":
                continue
            # PREMIUM/BUYBACK must satisfy gain = premium - buyback.
            # Rows with an explicit direction use the engines' signed
            # cash-flow convention (FUZZ #E: now identical in BOTH
            # engines): cost = -premium, proceeds = -buyback, so
            # gain == proceeds - cost holds direction-free. Legacy
            # inferred-short rows carry the legs swapped and positive.
            premium, buyback = (cost, proceeds) if inferred else _real_world_legs(v)
            und = underlying_of(sym, _aliases)
            rec = agg.setdefault(und, {"contracts": 0, "qty": 0.0,
                                       "proceeds": 0.0, "cost": 0.0,
                                       "gain": 0.0})
            # A grant-timing record recognises the premium at the
            # WRITE (s.49(1)); it is not a close. CLOSES/QTY count the
            # closing records only (audit S028-07).
            if not t.get("grant"):
                rec["contracts"] += 1
                rec["qty"] += abs(float(v.quantity or 0.0))
            elif expired_in:
                rec["contracts"] += 1
                rec["qty"] += _gclose[_ti][0]
            if not in_window:
                continue
            _ccd_pairs.append((acct, gain))
            rec["proceeds"] += premium
            rec["cost"] += buyback
            rec["gain"] += gain
    return agg, _ccd_pairs, tainted_skipped


def _gains_figures(rec: Dict[str, Any],
                   keys: Tuple[str, ...] = ("proceeds", "cost", "gain",
                                            "denied")) -> Dict[str, Any]:
    """A view's row of figures added from the gains files, as --json
    prints it: each money figure at the files' four places
    (M.GAINS_PLACES), which is the exact sum of their figures; the float
    sum printed its binary error (-42.52499999999995 for -42.525,
    TJ-4930). The text keeps the floats."""
    return {k: (M.json_money(v, M.GAINS_PLACES) if k in keys else v)
            for k, v in rec.items()}


def cmd_ccd_sum(args: argparse.Namespace) -> None:
    """Covered-call (SHORT call) realized-gain summary over a window
    (default: the tax year): per underlying — contracts closed, premium
    collected (proceeds), buyback cost, gain — plus the total. Same
    canonical wash-adjusted gains basis as `leaps-sum`; the windowed
    query twin of the pipeline's cross-account `reports/ccd.rpt`."""
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_right
    from taxjson.lib.report_model import gains_basis_label
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, scope, account = _view_window(args, root)
    resolved, _all_history = _window_gains_files(root, cache, account,
                                                 args)
    if resolved and not account:
        _warn_accounts_without_books(root, resolved, "ccd-sum",
                                     "gains file")
    if not resolved:
        if account:
            _die_input(f"no gains for account {account!r} in work/ — run "
                       f"`tjs run` first, or check the name")
        _die_input("no gains files in work/ — run `tjs run` first")
    basis = gains_basis_label(resolved)

    agg, _ccd_pairs, tainted_skipped = _ccd_gains(resolved, cache, root, keep, _all_history,
                                                  parse_option_right, is_option_symbol)

    base_cur = _base_currency(root)
    if tainted_skipped:
        _view_msg("warning", f"skipped {tainted_skipped} disposition(s) "
                  f"with an unknown cost",
                  "No purchase in your files; form-export, carryover and "
                  "leaps leave them out too.")
    if getattr(args, "json", False):
        _json_out({"rows": [dict(_gains_figures(rec), underlying=und,
                                 contracts=int(rec["contracts"]))
                            for und, rec in sorted(agg.items())],
                   "total_gain": _foot(r["gain"] for r in agg.values()),
                   "taxable_gain": _scope_split(root, _ccd_pairs)[
                       "taxable"],
                   "sheltered_gain": _scope_split(root, _ccd_pairs)[
                       "sheltered"],
                   "tainted_skipped": tainted_skipped,
                   "currency": base_cur, "scope": scope,
                   "basis": basis}, schema="ccd-sum")
        return
    if not agg:
        _out.put(f"No covered-call (short call) closes in {scope}.")
        return

    money = fmt_money
    out_lines = ["UNDERLYING CLOSES QTY PREMIUM BUYBACK GAIN"]
    total = 0.0
    for und, rec in sorted(agg.items()):
        total += M.shown(rec["gain"], 2)
        out_lines.append(" ".join([
            und, str(int(rec["contracts"])), f"{rec['qty']:g}",
            money(rec["proceeds"]), money(rec["cost"]),
            money(rec["gain"])]))
    _vprint(f"COVERED-CALL GAINS — {scope} ({base_cur}, basis: {basis})")
    _vprint("Short call legs only, allowed amounts. PREMIUM = proceeds of "
            "the sold calls; BUYBACK = cost to close (0 at expiry); "
            "CLOSES = closing records. An assignment's share gain is in "
            "the stock's rows.")
    _print_report_table(out_lines, fit=True, drop=(2, 1))
    _out.put(f"\nTOTAL COVERED-CALL GAIN: {money(total)} {base_cur}")
    _print_scope_split(_scope_split(root, _ccd_pairs), base_cur, root)


def cmd_winners(args: argparse.Namespace) -> None:
    """`tjs winners [PERIOD] [ACCOUNT] [--top N]`: the biggest
    realized WINNERS over a window (default: the tax year), positive
    gains only, best first (`losers` is the other side)."""
    doc = _ranked_view(args, "winners")
    if doc is not None:
        _json_out(doc, schema="winners")


def cmd_losers(args: argparse.Namespace) -> None:
    """`tjs losers [PERIOD] [ACCOUNT] [--top N]`: the biggest
    realized LOSERS over a window (default: the tax year), negative
    gains only, worst first (`winners` is the other side)."""
    doc = _ranked_view(args, "losers")
    if doc is not None:
        _json_out(doc, schema="losers")


def _ranked_gains(root: Path, resolved: Dict[str, Path], cache: Path,
                  keep: Callable[[str], bool], _all_history: bool
                  ) -> Tuple[Dict[str, Dict[str, Any]], int, Dict[str, float], Set[str]]:
    """winners / losers: each ticker's realized closes, proceeds, cost,
    gain and denied loss in the window (an option under its underlying, a
    grant-timed write by its expiry), the rows with an unknown cost
    counted, the gains of the taxable and sheltered accounts. Returns the
    tickers, the unknown-cost count, the group totals and the sheltered
    accounts (_ranked_view)."""
    _INCOME = {"DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST",
               "FEE", "DISALLOW", "ADJUST"}
    agg: Dict[str, Dict[str, Any]] = {}
    tainted_skipped = 0
    groups = _account_group_of(root)
    grp_gain = {"taxable": 0.0, "sheltered": 0.0}
    shel_accts = set()
    from taxjson.lib.ticker_map import class_share_aliases, underlying_of
    _docs = [(_a, _load_json_or_die(_f)) for _a, _f in resolved.items()]
    # Held shares (inventory) too: a covered call's class share is
    # often only held (re-audit A2-0715).
    _aliases = class_share_aliases(
        t.get("symbol") for _a, _d in _docs
        for t in (_d.get("transactions", []) or [])
        + (_d.get("inventory", []) or []) if isinstance(t, dict))
    from taxjson.lib.grant_writes import grant_write_expiries
    # (through the series' renames, as ccd-sum: backlog TJ-0903)
    _osplits = _option_split_rows(root, cache, [a for a, _d in _docs])

    def _ticker_rec(und: str) -> Dict[str, Any]:
        return agg.setdefault(und, {"closes": 0, "proceeds": 0.0,
                                    "cost": 0.0, "gain": 0.0,
                                    "denied": 0.0,
                                    "_grp": {"taxable": 0.0,
                                             "sheltered": 0.0},
                                    "_shel": set()})
    for _acct, data in _docs:
        _gclose = grant_write_expiries(
            data.get("transactions") or [], data.get("inventory") or [],
            (data.get("summary") or {}).get("year"),
            _osplits.get(_acct, []))
        # A write in an earlier year's books that expired in the window:
        # its close, no gain (backlog TJ-1086, TJ-1112).
        for t, _q, _rec in _earlier_grant_expiries(root, cache, _acct, data,
                                                   keep, _all_history,
                                                   _osplits.get(_acct, [])):
            _ticker_rec(underlying_of(str(t.get("symbol") or "?"),
                                      _aliases))["closes"] += _rec
        _settle = _settle_basis(root, data)
        # Pipeline files ROUTE unknown-cost rows out of transactions[]
        # into manual_reporting_required: count them too, or the
        # "nothing is silent" warning never fired on real books (audit
        # S040-15).
        for t in data.get("manual_reporting_required") or []:
            d = _gains_row_date(t, keep, _settle)
            if _ISO_DATE_RE.match(d) and keep(d):
                tainted_skipped += 1
        for _ti, t in enumerate(data.get("transactions", [])):
            if t.get("action") in _INCOME:
                continue
            d = _gains_row_date(t, keep, _settle)
            in_window = bool(_ISO_DATE_RE.match(d) and keep(d))
            # An expired grant-timing write's gain is the write's (its
            # date), its close the expiry's, as ccd-sum counts it
            # (backlog TJ-0778, TJ-1112).
            _exp = (_gclose[_ti][1] if t.get("grant") and _ti in _gclose
                    else "")
            expired_in = bool(_exp and _ISO_DATE_RE.match(_exp)
                              and keep(_exp))
            if not in_window:
                if expired_in and not t.get("tainted"):
                    _ticker_rec(underlying_of(str(t.get("symbol") or "?"),
                                              _aliases))["closes"] += 1
                continue
            if t.get("tainted"):
                # A missing-history OPENING_BALANCE basis fabricates gains — a
                # $0-cost row was ranked the portfolio's #2 winner and
                # made the total irreconcilable with form-export
                # (REVIEW #2). Excluded like every filing-facing
                # consumer, and COUNTED so nothing is silent.
                tainted_skipped += 1
                continue
            sym = str(t.get("symbol") or "?")
            und = underlying_of(sym, _aliases)
            rec = _ticker_rec(und)
            # A WRITE is not a close — unless it expired in the window,
            # when it is the only record of that close (A2-0693).
            if not t.get("grant") or expired_in:
                rec["closes"] += 1
            # Real-world orientation, as ccd-sum and form-export show
            # it: a SHORT row carries the engine's signed legs (cost =
            # -premium, proceeds = -buyback), so the columns read
            # negative and swapped (S040-14).
            v = GainRow(t)
            _p, _c = _real_world_legs(v)
            rec["proceeds"] += _p
            rec["cost"] += _c
            gain = float(v.gain or 0.0)
            rec["gain"] += gain
            # The loss the superficial-loss / wash-sale rule denied:
            # GAIN = PROCEEDS - COST + DENIED, so the row adds up
            # (backlog TJ-0120).
            rec["denied"] += float(v.disallowed_amount or 0.0)
            _g = groups.get(_acct)
            if _g:
                grp_gain[_g] += gain
                rec["_grp"][_g] += gain
                if _g == "sheltered":
                    shel_accts.add(_acct)
                    rec["_shel"].add(_acct)
    return agg, tainted_skipped, grp_gain, shel_accts


def _ranked_text(account: Optional[str], ranked: List[Tuple[str, Dict[str, Any]]], scope: str,
                 losers: bool, side_rows: List[Tuple[str, Dict[str, Any]]], basis: str,
                 top: int, base_cur: str, root: Path, side_shel: Set[str],
                 side_grp: Dict[str, float]) -> None:
    """winners / losers' text: the empty states, the side's top N with a
    DENIED column when a shown row has a denial, the taxable / sheltered
    split, the side's total and the overall one (_ranked_view's tail)."""
    # One account asked for: the title and the side's total name it,
    # as the TOTAL REALIZED GAIN line does (backlog TJ-0260).
    where = f"account {account}" if account else ""
    page = _out.Doc(exact=True, width_=0, live=True)
    if not ranked:
        page.line(f"No realized dispositions in {scope}"
                  + (f" ({where})" if where else "") + ".")
        return
    word = "losers" if losers else "winners"
    other = "winners" if losers else "losers"
    money = fmt_money
    if not side_rows:
        page.line(f"No realized {word} in {scope} ("
                  + (f"{where}, " if where else "")
                  + f"basis: {basis}); the other side: tjs {other}.")
        return
    head = side_rows[:top]
    # A DENIED column only when a shown row has a denial: without one,
    # PROCEEDS - COST is the GAIN already (backlog TJ-0120).
    with_denied = any(M.shown(rec["denied"], 2) for _t, rec in head)
    out_lines = ["TICKER CLOSES PROCEEDS COST "
                 + ("DENIED GAIN" if with_denied else "GAIN")]
    for t, rec in head:
        out_lines.append(" ".join([t, str(int(rec["closes"])),
                                   money(rec["proceeds"]),
                                   money(rec["cost"])]
                                  + ([money(rec["denied"])]
                                     if with_denied else [])
                                  + [money(rec["gain"])]))
    _vprint(f"{word.upper()} — {scope} ({base_cur}, "
            + (f"{where}, " if where else "")
            + f"{'worst' if losers else 'top'} {top})")
    _rule = ("" if not with_denied
             else "wash-sale" if _country(load_settings(root, missing_ok=True)) == "usa"
             else "superficial-loss")
    _vprint(f"Realized dispositions, allowed amounts, basis: {basis}; an "
            f"option under its underlying; dividends and PIL: tjs divs-sum."
            + (f" GAIN = PROCEEDS − COST + DENIED (the loss the {_rule} "
               f"rule denied)." if with_denied else ""))
    _print_report_table(out_lines, fit=True,
                        drop=(4, 2, 3) if with_denied else (2, 3))
    side_total = _foot(r["gain"] for _t, r in side_rows)
    total = _foot(r["gain"] for _t, r in ranked)
    page.blank()
    if side_shel:
        # A registered account's gains are not taxable events; the
        # headline alone overstated a Schedule 3 gain several-fold
        # (audit S040-13). Same split and note `sum` prints.
        _vprint(f"TAXABLE: {money(side_grp['taxable'])} {base_cur}   "
                f"SHELTERED ({', '.join(sorted(side_shel))} — not "
                f"taxable events): {money(side_grp['sheltered'])} "
                f"{base_cur}")
    shown = (f"{len(head)} of {len(side_rows)} shown"
             if len(head) < len(side_rows)
             else "1 shown" if len(head) == 1
             else f"all {len(head)} shown")
    from taxjson.lib.out import kv_lines as _kvl
    for ln in _kvl([(f"{word.upper()} TOTAL",
                     f"{money(side_total)} {base_cur} ({shown}"
                     + (f", {account}" if account else "") + ")"),
                    ("TOTAL REALIZED GAIN",
                     f"{money(total)} {base_cur} ({len(ranked)} ticker(s), "
                     f"{account or 'all accounts'}; the {other}: "
                     f"tjs {other})")]):
        page.line(ln)


def _ranked_view(args: argparse.Namespace, side: str) -> Optional[Dict[str, Any]]:
    """winners / losers: per-ticker realized gains over a window,
    canonical wash-preferred basis, option contracts grouped under
    their underlying, ranked; the text shows one side's top N (winners:
    gains above zero, best first; losers: below zero, worst first) and
    its taxable / sheltered split. --json carries every ticker ranked
    from that side, as `winners --json` always did, with the side's own
    totals beside the overall ones: returned, not printed, for the
    command to write under its output's id (lib/json_contract)."""
    from taxjson.lib.report_model import gains_basis_label
    losers = side == "losers"
    top = getattr(args, "top", None)
    if top is None:
        top = 10
    elif top < 1:
        # 0 was silently rewritten to the default and negatives
        # clamped to 1 — the header then advertised a number the user
        # never asked for (REVIEW #44). A usage error: exit 2, as
        # every command's, said before any file is read (backlog
        # TJ-0157, TJ-0241).
        _die_input(f"--top must be >= 1, got {top}")
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, scope, account = _view_window(args, root)
    resolved, _all_history = _window_gains_files(root, cache, account,
                                                 args)
    if resolved and not account:
        _warn_accounts_without_books(root, resolved, side, "gains file")
    if not resolved:
        _die_input("no gains files in work/ — run `tjs run` first")
    basis = gains_basis_label(resolved)
    agg, tainted_skipped, grp_gain, shel_accts = _ranked_gains(root, resolved, cache, keep,
                                                               _all_history)
    ranked = sorted(agg.items(), key=lambda kv: (kv[1]["gain"]
                                                 if losers
                                                 else -kv[1]["gain"]))
    # One side: a winner gained, a loser lost (a ticker that nets to
    # zero is neither).
    side_rows = [(t, r) for t, r in ranked
                 if (M.shown(r["gain"], 2) < 0 if losers
                     else M.shown(r["gain"], 2) > 0)]
    side_grp = {"taxable": 0.0, "sheltered": 0.0}
    side_shel = set()
    for _t, r in side_rows:
        for g in side_grp:
            side_grp[g] += r["_grp"][g]
        side_shel |= r["_shel"]
    base_cur = _base_currency(root)
    if tainted_skipped:
        _view_msg("warning", f"skipped {tainted_skipped} disposition(s) "
                  f"with an unknown cost",
                  "No purchase in your files; form-export, carryover and "
                  "leaps leave them out too.")
    if getattr(args, "json", False):
        return {"rows": [dict(_gains_figures({k: v for k, v in rec.items()
                                              if not k.startswith("_")}),
                              ticker=t, closes=int(rec["closes"]))
                         for t, rec in ranked],
                "total_gain": _foot(r["gain"] for _t, r in ranked),
                "total_gain_taxable": M.shown(grp_gain["taxable"], 2),
                "total_gain_sheltered": M.shown(grp_gain["sheltered"], 2),
                "sheltered_included": sorted(shel_accts),
                "side": side,
                "side_tickers": [t for t, _r in side_rows],
                "side_total_gain": _foot(r["gain"]
                                         for _t, r in side_rows),
                "side_total_gain_taxable": M.shown(side_grp["taxable"],
                                                   2),
                "side_total_gain_sheltered": M.shown(
                    side_grp["sheltered"], 2),
                "tainted_skipped": tainted_skipped,
                "currency": base_cur, "scope": scope,
                "basis": basis}
    _ranked_text(account, ranked, scope, losers, side_rows, basis, top, base_cur, root,
                 side_shel, side_grp)
    return None


def _view_trust_units(root: Path, rules) -> frozenset:
    """The symbols every account's books show to be a Canadian trust's
    units (characterize/income_dating.trust_units, over the whole history, not
    the view's window): a payment in lieu on one is ordinary income,
    as in the gains files (CA-INC-07; A2-1465)."""
    if rules is None:
        return frozenset()
    cache = root / "work"
    rows = []
    for acct in _discover_tx_accounts(cache):
        native = _native_tx_file(cache, acct)
        if native is None:
            continue
        try:
            data = _read_work_doc(native)
        except (OSError, ValueError):
            continue          # the view's own read names a bad file
        rows += [t for t in (data.get("transactions") or [])
                 if isinstance(t, dict) and t.get("action") == "DIVIDEND"]
    return rules.trust_units(rows)


def _foot_by_currency(pairs) -> Dict[str, float]:
    """{currency: _foot of that currency's row amounts}."""
    out: Dict[str, List[float]] = {}
    for cur, v in pairs:
        out.setdefault(cur, []).append(v)
    return {c: _foot(vs) for c, vs in out.items()}


def cmd_staking_sum(args: argparse.Namespace) -> None:
    """Reward totals by exchange and asset, with evidence coverage."""
    report = _staking_view(args, summary=True)
    if report is not None:
        _json_out(report, schema="staking-sum")


def _divs_text(agg: Dict[Tuple[str, str], float], money: Callable[[Any], str], scope: str,
               n_pil_div: int, args: argparse.Namespace, shel_accts: Set[str],
               staking_taxable: Dict[str, float], slips: Dict[str, float],
               by_group: Dict[str, Dict[str, float]], totals: Dict[str, float],
               staking_accts: Set[str], staking: Dict[str, float],
               cg_agg: Dict[Tuple[str, str], float], cg_by_group: Dict[str, Dict[str, float]],
               cg_totals: Dict[str, float]) -> None:
    """`tjs divs-sum`'s text: each ticker's dividends, the totals (taxable
    for the T5/T3 slips or Form 1099-DIV, sheltered, all accounts, the
    crypto staking within them) and the T5 box 18 capital-gains
    dividends apart (cmd_divs_sum's tail)."""
    out_lines = ["SYMBOL CUR DIVIDEND"]
    for (sym, cur), amt in sorted(agg.items()):
        out_lines.append(" ".join([sym, cur, money(amt)]))
    page = _out.Doc(exact=True, width_=0, live=True)
    _vprint(f"DIVIDENDS — {scope}")
    if n_pil_div:
        _vprint(f"DIVIDEND rows and {n_pil_div} payment(s) in lieu deemed "
                f"dividends by ITA s.260; other payments in lieu are in "
                f"`dil-sum`.")
    else:
        _vprint("DIVIDEND rows; payments in lieu are in `dil-sum`.")
    page.blank()
    _print_report_table(out_lines, fit=True)

    def _tot(d):
        return ", ".join(f"{money(v)} {c}" for c, v in sorted(d.items()))
    page.blank()
    # The slips a dividend ties to: T5/T3 in Canada, Form 1099-DIV in the
    # US (audit A2-0741, A2-1354).
    _usa_v = _country(load_settings(Path(args.dir).resolve(), missing_ok=True)) == "usa"
    _slips_w = "Form 1099-DIV" if _usa_v else "T5/T3 slips"
    from taxjson.lib.out import kv_lines as _kvl
    _figs = []
    if shel_accts or staking_taxable:
        # Registered accounts get no T5/T3 and their dividends are not
        # income: the slip tie-out figure is the TAXABLE line (audit
        # S041-04), without crypto staking (S048-24).
        _figs.append((f"TAXABLE ({_slips_w})", _tot(slips) or "0.00"))
        if shel_accts:
            _figs.append((f"SHELTERED ({', '.join(sorted(shel_accts))}, "
                          f"not taxable)", _tot(by_group['sheltered'])))
        _figs.append(("TOTAL DIVIDEND (all accounts)", _tot(totals)))
    else:
        _figs.append(("TOTAL DIVIDEND", _tot(totals)))
    if staking_accts:
        _figs.append((f"  of which staking rewards ("
                      f"{', '.join(sorted(staking_accts))}, other income, "
                      f"no {'1099-DIV' if _usa_v else 'slip'})",
                      _tot(staking)))
    for ln in _kvl(_figs):
        page.line(ln)
    if cg_agg:
        page.blank()
        _vprint("CAPITAL-GAINS DIVIDENDS — T5 box 18, line 17400")
        _vprint("A capital gain at 50% inclusion, not a dividend; named in "
                "[[capital_gains_dividends]], NOT in the totals above.")
        page.blank()
        _print_report_table(["SYMBOL CUR AMOUNT"] + [
            " ".join([sym, cur, money(a)])
            for (sym, cur), a in sorted(cg_agg.items())], fit=True)
        page.blank()
        if cg_by_group["sheltered"]:
            _vprint(f"TAXABLE: {_tot(cg_by_group['taxable']) or '0.00'}; "
                    f"SHELTERED: {_tot(cg_by_group['sheltered'])}",
                    hang="  ")
        _vprint(f"TOTAL CAPITAL-GAINS DIVIDENDS: {_tot(cg_totals)}",
                hang="  ")


def cmd_divs_sum(args: argparse.Namespace) -> None:
    """Dividend summary over a window (default: the tax year): total received
    per ticker, plus a per-currency grand total. `PERIOD` is 30d/6w/3m/1y/all;
    omit it for the tax year."""
    # DIVIDEND rows, plus (Canada) a payment in lieu that ITA s.260
    # deems a dividend — a Canadian dealer's payment on a Canadian
    # corporation's share (not a trust's unit, CA-INC-07), on the
    # dealer's T5 box 24. Every other payment in lieu is ordinary
    # income, reported by `dil-sum` only (counting it
    # in both views put it in the slip tie-out, audit R1-272). Rows are
    # windowed on their tax date (characterize/income_dating: a Canadian trust's
    # distribution by its record date).
    _rules = _view_income_rules(Path(args.dir).resolve())
    rows, scope, bad, _keep = _collect_period_txs(
        args, "divs-sum", actions={"DIVIDEND", "DIVIDEND_IN_LIEU"},
        date_of=_rules.income_date if _rules else None)
    _trusts = _view_trust_units(Path(args.dir).resolve(), _rules)
    rows = [(a, t) for a, t in rows
            if t.get("action") == "DIVIDEND"
            or (_rules is not None and _rules.pil_is_dividend(t, _trusts))]
    n_pil_div = sum(1 for _a, t in rows
                    if t.get("action") == "DIVIDEND_IN_LIEU")

    money = fmt_money               # shared report-layer formatter
    groups = _account_group_of(Path(args.dir).resolve())

    agg: Dict[Tuple[str, str], float] = {}
    totals: Dict[str, float] = {}
    by_group: Dict[str, Dict[str, float]] = {"taxable": {},
                                             "sheltered": {}}
    shel_accts = set()
    # A crypto account's DIVIDEND rows are staking rewards — other
    # income, never on a T5/T3 — so they are totalled apart instead of
    # blending into the dividend total unmarked (audit S031-04).
    _crypto_accts = {n for n, a in accounts_of(load_config(Path(args.dir).resolve(),
                                                           missing_ok=True)).items()
                     if a.crypto is True}
    staking: Dict[str, float] = {}
    staking_taxable: Dict[str, float] = {}
    staking_accts = set()
    # T5 box 18 capital-gains dividends named in capital_gains_dividends
    # .map (Canada, tax-logic CA-INC-06): a capital gain, not a
    # dividend — shown apart and left out of the dividend totals.
    _box18 = _box18_fractions(Path(args.dir).resolve(), _rules)
    cg_agg: Dict[Tuple[str, str], float] = {}
    cg_by_group: Dict[str, Dict[str, float]] = {"taxable": {},
                                                "sheltered": {}}
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        # Signed: reversal rows (negative) net against the original posting.
        amt = (float(tx.get("gross_amount") or 0.0)
               or float(tx.get("net_amount") or 0.0))
        _f = (_box18 or {}).get((acct, str(tx.get("id")))) \
            if tx.get("action") == "DIVIDEND" else None
        if _f:
            _cg = amt * _f
            amt -= _cg
            _k = (str(tx.get("symbol") or "?"), cur)
            cg_agg[_k] = cg_agg.get(_k, 0.0) + _cg
            _g = groups.get(acct)
            if _g:
                cg_by_group[_g][cur] = cg_by_group[_g].get(cur, 0.0) + _cg
        key = (str(tx.get("symbol") or "?"), cur)
        agg[key] = agg.get(key, 0.0) + amt
        totals[cur] = totals.get(cur, 0.0) + amt
        if acct in _crypto_accts:
            staking[cur] = staking.get(cur, 0.0) + amt
            staking_accts.add(acct)
            if groups.get(acct) == "taxable":
                staking_taxable[cur] = staking_taxable.get(cur, 0.0) + amt
        g = groups.get(acct)
        if g:
            by_group[g][cur] = by_group[g].get(cur, 0.0) + amt
            if g == "sheltered":
                shel_accts.add(acct)
    _warn_bad_dates(bad)
    totals = _foot_by_currency((cur, amt) for (_s, cur), amt in agg.items())
    # The figure the T5/T3 slips add up to: taxable accounts' dividends
    # WITHOUT crypto staking (other income, never on a slip) — the
    # "compare with slips" line counted staking in (S048-24).
    slips = {c: v - staking_taxable.get(c, 0.0)
             for c, v in by_group["taxable"].items()}
    cg_totals = _foot_by_currency((cur, a) for (_s, cur), a
                                  in cg_agg.items())
    cg_doc = None
    if _box18 is not None:
        cg_doc = {"rows": [{"symbol": sym, "currency": cur,
                            "amount": M.shown(a, 2)}
                           for (sym, cur), a in sorted(cg_agg.items())],
                  "totals": {c: M.shown(v, 2) for c, v in cg_totals.items()},
                  "totals_taxable": {c: M.shown(v, 2) for c, v
                                     in cg_by_group["taxable"].items()},
                  "totals_sheltered": {c: M.shown(v, 2) for c, v
                                       in cg_by_group["sheltered"].items()},
                  "line": "17400"}
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "dividend": M.shown(amt, 2)}
                            for (sym, cur), amt in sorted(agg.items())],
                   "capital_gains_dividends": cg_doc,
                   "totals": {c: M.shown(v, 2) for c, v in totals.items()},
                   "totals_taxable": {c: M.shown(v, 2) for c, v
                                      in by_group["taxable"].items()},
                   "totals_sheltered": {c: M.shown(v, 2) for c, v
                                        in by_group["sheltered"].items()},
                   "totals_slips": {c: M.shown(v, 2) for c, v
                                    in slips.items()},
                   "sheltered_included": sorted(shel_accts),
                   "crypto_staking": {c: M.shown(v, 2) for c, v
                                      in staking.items()},
                   "payments_in_lieu_as_dividends": n_pil_div,
                   "scope": scope}, schema="divs-sum")
        return
    if not agg and not cg_agg:
        _out.put(f"No dividends in {scope}.")
        return
    _divs_text(agg, money, scope, n_pil_div, args, shel_accts, staking_taxable, slips, by_group,
               totals, staking_accts, staking, cg_agg, cg_by_group, cg_totals)


def _option_split_rows(root: Path, cache: Path,
                       accounts: Iterable[str]) -> Dict[str, list]:
    """{account: the SPLIT rows of option series its gains rows went
    through}: the base books' (the gains' own symbols) of every account
    of its split pool (_split_pool_of), one event once — what
    grant_writes.grant_write_expiries follows a renamed write through
    (backlog TJ-0903)."""
    from taxjson.lib.option_symbols import is_option_symbol
    from taxjson.lib.corporate_timeline import dedupe_split_events
    pool_of = _split_pool_of(root)
    by_pool: Dict[str, list] = {}
    accounts = list(accounts)
    pools = {pool_of(a) for a in accounts}
    for a in sorted(set(_discover_tx_accounts(cache)) | set(accounts)):
        if pool_of(a) not in pools:
            continue
        bpath = cache / f"{a}_base.json"
        if not bpath.is_file():
            continue
        try:
            rows = _read_work_doc(bpath).get("transactions") or []
        except (OSError, ValueError):
            continue
        by_pool.setdefault(pool_of(a), []).extend(
            t for t in rows if isinstance(t, dict)
            and t.get("action") == "SPLIT"
            and is_option_symbol(str(t.get("symbol") or "")))
    return {a: dedupe_split_events(by_pool.get(pool_of(a), []))
            for a in accounts}

def cmd_dil_sum(args: argparse.Namespace) -> None:
    """Payment-in-lieu summary over a window (default: the tax year):
    DIVIDEND_IN_LIEU rows only — payments received while shares were lent
    out (or short) over the ex-date. Split out from `divs-sum` because
    the tax treatment differs: a payment in lieu is ordinary income (no
    CA gross-up/credit; no US qualified rate) — except, in a Canada
    project, a Canadian dealer's payment on a Canadian issuer's share,
    which ITA s.260 deems a dividend (shown as such, and counted in
    `divs-sum`)."""
    _rules = _view_income_rules(Path(args.dir).resolve())
    rows, scope, bad, _keep = _collect_period_txs(
        args, "dil-sum", actions={"DIVIDEND_IN_LIEU"},
        date_of=_rules.income_date if _rules else None)
    _trusts = _view_trust_units(Path(args.dir).resolve(), _rules)

    money = fmt_money               # shared report-layer formatter

    # Treatment per row (characterize/income_dating): Canada — a Canadian
    # dealer's payment on a Canadian issuer's share is a taxable
    # dividend (ITA s.260; T5 box 24, counted in `divs-sum`); every
    # other payment in lieu is ordinary income (US: non-qualified).
    agg: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    totals: Dict[str, float] = {}
    by_treat: Dict[str, Dict[str, float]] = {"dividend": {}, "ordinary": {}}
    # The part of dividend compensation in a cover's cost: no payment
    # in lieu at all, as in `sum` (§263(h); backlog TJ-1066).
    _cap_share = _capitalized_pil_shares(Path(args.dir).resolve(), {a for a, _tx in rows},
                                         is_usa(_rules.country))
    capitalized: Dict[str, float] = {}
    # A registered account's payment in lieu is not income at all: it
    # is its own line, never "ordinary income" (S041-09).
    groups = _account_group_of(Path(args.dir).resolve())
    sheltered: Dict[str, float] = {}
    shel_accts = set()
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        # Signed: reversal rows (negative) net against the original posting.
        amt = (float(tx.get("gross_amount") or 0.0)
               or float(tx.get("net_amount") or 0.0))
        treat = ("dividend" if _rules is not None
                 and _rules.pil_is_dividend(tx, _trusts) else "ordinary")
        _cap = amt * _cap_share.get((acct, str(tx.get("id"))), 0.0)
        if _cap:
            amt -= _cap
            capitalized[cur] = capitalized.get(cur, 0.0) + _cap
            _crec = agg.setdefault((str(tx.get("symbol") or "?"), cur,
                                    "capitalized"),
                                   {"amount": 0.0, "rows": 0})
            _crec["amount"] += _cap
            _crec["rows"] += 1
            if abs(amt) < 0.005:
                continue
        key = (str(tx.get("symbol") or "?"), cur, treat)
        rec = agg.setdefault(key, {"amount": 0.0, "rows": 0})
        rec["amount"] += amt
        rec["rows"] += 1
        totals[cur] = totals.get(cur, 0.0) + amt
        if groups.get(acct) == "sheltered":
            sheltered[cur] = sheltered.get(cur, 0.0) + amt
            shel_accts.add(acct)
            continue
        by_treat[treat][cur] = by_treat[treat].get(cur, 0.0) + amt
    _warn_bad_dates(bad)
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "in_lieu": M.shown(rec["amount"], 2),
                             "rows": int(rec["rows"]),
                             "treatment": treat}
                            for (sym, cur, treat), rec
                            in sorted(agg.items())],
                   "totals": {c: M.shown(v, 2) for c, v in totals.items()},
                   "totals_ordinary": {c: M.shown(v, 2) for c, v
                                       in by_treat["ordinary"].items()},
                   "totals_dividend": {c: M.shown(v, 2) for c, v
                                       in by_treat["dividend"].items()},
                   "totals_sheltered": {c: M.shown(v, 2) for c, v
                                        in sheltered.items()},
                   "totals_capitalized": {c: M.shown(v, 2) for c, v
                                          in capitalized.items()},
                   "sheltered_included": sorted(shel_accts),
                   "scope": scope}, schema="dil-sum")
        return
    if not agg:
        _out.put(f"No dividends in lieu in {scope}.")
        return
    out_lines = ["SYMBOL CUR IN_LIEU ROWS TREATMENT"]
    for (sym, cur, treat), rec in sorted(agg.items()):
        out_lines.append(" ".join([
            sym, cur, money(rec["amount"]), str(int(rec["rows"])),
            {"dividend": "dividend(s.260)",
             "capitalized": "capitalized(§263(h))"}.get(treat, "ordinary")]))
    page = _out.Doc(exact=True, width_=0, live=True)
    _vprint(f"DIVIDENDS IN LIEU — {scope}")
    if by_treat["dividend"]:
        _vprint("Payments in lieu are ordinary income, except a Canadian "
                "dealer's payment on a Canadian issuer's share: a taxable "
                "dividend under ITA s.260, on the T5 and in `divs-sum`; "
                "the slip is authoritative.")
    else:
        _vprint("Payments in lieu are ordinary income: no dividend "
                "gross-up/credit or qualified rate.")
    if capitalized:
        _vprint("Capitalized: dividend compensation paid on a short sale "
                "closed within 45 days is the cover's cost (§263(h)), not "
                "a payment in lieu; `tjs sum` and the total below leave "
                "it out.")
    page.blank()
    _print_report_table(out_lines, fit=True, drop=(3,))

    def _tot(d):
        return ", ".join(f"{money(v)} {c}" for c, v in sorted(d.items()))
    if by_treat["dividend"] or shel_accts:
        page.blank()
        _vprint(f"ORDINARY INCOME: {_tot(by_treat['ordinary']) or '0.00'}",
                hang="  ")
    if by_treat["dividend"]:
        _vprint(f"DEEMED DIVIDENDS (s.260, in divs-sum): "
                f"{_tot(by_treat['dividend'])}", hang="  ")
    if shel_accts:
        _vprint(f"SHELTERED ({', '.join(sorted(shel_accts))} — not taxable "
                f"income): {_tot(sheltered)}", hang="  ")
    if capitalized:
        page.blank()
        _vprint(f"{_CAPITALIZED_LBL} {_tot(capitalized)}", hang="  ")
    page.blank()
    _vprint(f"TOTAL DIVIDEND IN LIEU"
            + (" (all accounts)" if shel_accts else "")
            + f": {_tot(totals) or '0.00'}", hang="  ")


def cmd_roc_sum(args: argparse.Namespace) -> None:
    """Return-of-capital summary over a window (default: the tax year):
    per ticker, the capital returned (= ACB reduced) with broker-classified
    vs manual row counts, plus per-currency totals. Reads ADJUST rows —
    broker-classified ROC carries type='roc'; a manual row is a .tt line
    (e.g. a T3 box 42 entry, `type=roc` or not: backlog TJ-0427) or a
    typeless ADJUST. An ADJUST that is no return of capital
    (a reinvested distribution, a spin-off's cost allocation:
    income_dating.is_return_of_capital) is listed apart, outside the
    totals compared with T3 box 42 (2026-10-23 audit #5, #11)."""
    from taxjson.characterize.income_dating import is_return_of_capital
    # A Canadian trust's ROC counts in the year it became payable (its
    # record date, characterize/income_dating — the year of T3 box 42).
    _rules = _view_income_rules(Path(args.dir).resolve())
    rows, scope, bad, _keep = _collect_period_txs(
        args, "roc-sum", actions={"ADJUST"},
        date_of=_rules.roc_date if _rules else None)
    _root = Path(args.dir).resolve()
    _accts = sorted({a for a, _t in rows}
                    | set(_discover_tx_accounts(_root / "work")))
    _acct_arg = _view_window(args, _root)[2]
    if _acct_arg:
        _accts = [_acct_arg]
    dist_rows = _dist_adjust_rows(_root / "work", _accts, _keep)
    _warn_dist_double_entry(_root, _accts, _rules, _keep, "roc-sum")
    rows = list(rows) + dist_rows

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], Dict[str, float]] = {}
    totals: Dict[str, float] = {}
    # Registered accounts have no ACB to track and get no T3: the total
    # a user ties to T3 box 42 is the TAXABLE one (S041-10), the same
    # scope as the checklist's roc-entered step.
    groups = _account_group_of(_root)
    by_group: Dict[str, Dict[str, float]] = {"taxable": {},
                                             "sheltered": {}}
    shel_accts = set()
    other: List[Dict[str, Any]] = []
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        if not is_return_of_capital(tx):
            _dist = str(tx.get("id") or "").startswith("DIST-")
            other.append({"account": acct,
                          "symbol": str(tx.get("symbol") or "?"),
                          "currency": cur,
                          "date": str((tx.get("date_settle") if _dist
                                       else None)
                                      or tx.get("date") or "")[:10],
                          "acb_change": float(tx.get("net_amount") or 0.0),
                          "kind": _adjust_kind(tx)})
            continue
        _g = groups.get(acct)
        if _g:
            _r = -float(tx.get("net_amount") or 0.0)
            by_group[_g][cur] = by_group[_g].get(cur, 0.0) + _r
            if _g == "sheltered":
                shel_accts.add(acct)
        # ADJUST net_amount is the ACB delta (negative = reduction).
        # Present as capital RETURNED, so a normal ROC posting is
        # positive; negative values are reversals / manual ACB increases.
        returned = -float(tx.get("net_amount") or 0.0)
        key = (str(tx.get("symbol") or "?"), cur)
        rec = agg.setdefault(key, {"returned": 0.0, "roc_rows": 0,
                                   "manual_rows": 0, "dist_rows": 0})
        rec["returned"] += returned
        _typ = (tx.get("type") or "").lower()
        if _typ == "dist" and str(tx.get("id") or "").startswith("DIST-"):
            rec["dist_rows"] += 1
        elif str(tx.get("source") or "").lower().endswith(".tt"):
            # A line typed into a .tt file (a T3's box 42, the line
            # slip-audit suggests), `type=roc` or not: manual, never the
            # broker's (backlog TJ-0427).
            rec["manual_rows"] += 1
        elif _typ in ("roc", "dist"):
            # Broker-classified: a ROC, or RBC's notional distribution
            # (type 'dist' in the native book, not a map row — A2-0116).
            rec["roc_rows"] += 1
        else:
            rec["manual_rows"] += 1
        totals[cur] = totals.get(cur, 0.0) + returned
    _warn_bad_dates(bad)
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "capital_returned": M.shown(rec["returned"], 2),
                             "roc_rows": int(rec["roc_rows"]),
                             "manual_rows": int(rec["manual_rows"]),
                             "dist_rows": int(rec["dist_rows"])}
                            for (sym, cur), rec in sorted(agg.items())],
                   "totals": {c: M.shown(v, 2) for c, v in totals.items()},
                   "totals_taxable": {c: M.shown(v, 2) for c, v
                                      in by_group["taxable"].items()},
                   "totals_sheltered": {c: M.shown(v, 2) for c, v
                                        in by_group["sheltered"].items()},
                   "sheltered_included": sorted(shel_accts),
                   "other_adjustments": [
                       dict(o, acb_change=M.shown(o["acb_change"], 2))
                       for o in other],
                   "scope": scope}, schema="roc-sum")
        return
    # Each country's own slip and cost word (A2-0735): a US fund's
    # nondividend distribution is 1099-DIV box 3 and reduces basis.
    _us = _country(load_settings(_root, missing_ok=True)) == "usa"
    _cw = "basis" if _us else "ACB"
    _slip = "1099-DIV box 3" if _us else "T3 box 42"
    if not agg and not other:
        _out.put(f"No {_cw} adjustments in {scope}.")
        return
    if not agg:
        _out.put(f"No return of capital in {scope}.")
        _roc_sum_other(other, _cw, _slip)
        return
    out_lines = ["SYMBOL CUR CAPITAL_RETURNED ROC_ROWS MANUAL_ROWS "
                 "MAP_ROWS"]
    for (sym, cur), rec in sorted(agg.items()):
        out_lines.append(" ".join([sym, cur, money(rec["returned"]),
                                   str(rec["roc_rows"]),
                                   str(rec["manual_rows"]),
                                   str(rec["dist_rows"])]))
    page = _out.Doc(exact=True, width_=0, live=True)
    _vprint(f"RETURN OF CAPITAL / {_cw.upper()} ADJUSTMENTS — {scope}")
    page.blank()
    _print_report_table(out_lines, fit=True, drop=(5, 4, 3))
    def _tot(d):
        return ", ".join(f"{money(v)} {c}" for c, v in sorted(d.items()))
    page.blank()
    if shel_accts:
        _vprint(f"TAXABLE (compare with {_slip}): "
                f"{_tot(by_group['taxable']) or '0.00'}", hang="  ")
        _vprint(f"SHELTERED ({', '.join(sorted(shel_accts))} — no {_cw} "
                f"to track, no {'1099-DIV' if _us else 'T3'}): "
                f"{_tot(by_group['sheltered'])}", hang="  ")
        _vprint(f"TOTAL CAPITAL RETURNED (all accounts): {_tot(totals)}",
                hang="  ")
    else:
        _vprint(f"TOTAL CAPITAL RETURNED ({_cw} reduced): {_tot(totals)}",
                hang="  ")
    _roc_sum_other(other, _cw, _slip)
    page.blank()
    for item in (f"Positive = {_cw} reduced (capital returned). Negative "
                 f"rows are reversals or manual {_cw} increases (MAP_ROWS: "
                 f"[[distributions]] return-of-capital entries).",
                 f"Enter fund ROC from your {_slip} as .tt ADJUST lines OR "
                 f"as [[distributions]] in taxjson.toml, never both — see "
                 f"docs/tax-rules.md, \"Return of capital\"."):
        _vprint(item, indent="- ", hang="  ")


def _adjust_kind(tx: Dict[str, Any]) -> str:
    """What an ADJUST that is no return of capital is, in a few words
    (roc-sum's OTHER table)."""
    typ = str(tx.get("type") or "").lower()
    up = float(tx.get("net_amount") or 0.0) > 0
    if typ == "dist":
        if str(tx.get("id") or "").startswith("DIST-"):
            return ("reinvested distribution ([[distributions]])" if up
                    else "[[distributions]] entry")
        return "notional distribution" if up else "distribution"
    if tx.get("corp_event_id") or typ == "spinoff_355":
        el = str(tx.get("corp_election") or "")
        return (f"spin-off cost allocation ({el})" if el
                else "spin-off cost allocation")
    if str(tx.get("id") or "").startswith(("WASH_", "HANDOFF_WASH_")):
        return "deferred loss"
    return typ or "adjustment"


def _roc_sum_other(other: List[Dict[str, Any]], cw: str,
                   slip: str) -> None:
    """roc-sum: the ADJUST rows that are no return of capital — a
    reinvested or notional distribution raises the cost, a spin-off
    moves part of it to the new shares — each with its kind, outside
    the totals compared with the slip."""
    if not other:
        return
    page = _out.Doc(exact=True, width_=0, live=True)
    from taxjson.lib.out import fit_table
    page.blank()
    _vprint(f"OTHER {cw.upper()} ADJUSTMENTS (not a return of capital, not "
            f"in {slip}; + = {cw} raised)", hang="  ")
    page.blank()
    body = [[o["symbol"], o["currency"], o["date"],
             f"{M.shown(o['acb_change'], 2):+,.2f}", o["kind"]]
            for o in sorted(other, key=lambda o: (o["symbol"], o["date"]))]
    for ln in fit_table(["SYMBOL", "CUR", "DATE", f"{cw.upper()}_CHANGE",
                         "KIND"], body, drop=(2,)):
        page.line(ln)


def cmd_trades_sum(args: argparse.Namespace) -> None:
    """Trade summary over a window (default: the tax year): per ticker, the
    buy/sell counts, value bought/sold, and fees, plus per-currency totals.
    `PERIOD` is 30d/6w/3m/1y/all; omit it for the tax year."""
    rows, scope, bad, _keep = _collect_period_txs(
        args, "trades-sum", actions={"BUYSELL", "ASSIGN"},
        settle_trades=True)

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], Dict[str, float]] = {}
    # The rows' money added exactly, for --json: the float sums printed
    # their binary error there (TJ-4930); the text keeps the floats.
    exact: Dict[Tuple[str, str], Dict[str, Decimal]] = {}
    tot_bought: Dict[str, float] = {}
    tot_sold: Dict[str, float] = {}
    tot_fees: Dict[str, float] = {}
    # The SOLD total pooled registered accounts with no word: it is not
    # the T5008 figure, which covers taxable accounts only (S041-12).
    groups = _account_group_of(Path(args.dir).resolve())
    tax_sold: Dict[str, float] = {}
    shel_accts = set()
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        q = float(tx.get("quantity") or 0.0)
        amt = _trade_total(tx)          # sells signed (S039-11)
        fee = _tx_fee(tx)
        if groups.get(acct) == "sheltered":
            shel_accts.add(acct)
        elif q < 0:
            tax_sold[cur] = tax_sold.get(cur, 0.0) + amt
        _key = (str(tx.get("symbol") or "?"), cur)
        d = agg.setdefault(_key,
                           {"buys": 0, "sells": 0, "bought": 0.0, "sold": 0.0,
                            "fees": 0.0})
        x = exact.setdefault(_key, {"bought": Decimal(0), "sold": Decimal(0),
                                    "fees": Decimal(0)})
        if q > 0:
            d["buys"] += 1
            d["bought"] += amt
            x["bought"] += M.of(amt)
            tot_bought[cur] = tot_bought.get(cur, 0.0) + amt
        elif q < 0:
            d["sells"] += 1
            d["sold"] += amt
            x["sold"] += M.of(amt)
            tot_sold[cur] = tot_sold.get(cur, 0.0) + amt
        d["fees"] += fee
        x["fees"] += M.exact_sum((tx.get("fee") or 0, tx.get("commission") or 0))
        tot_fees[cur] = tot_fees.get(cur, 0.0) + fee
    _warn_bad_dates(bad)
    for _tot_d, _k in ((tot_bought, "bought"), (tot_sold, "sold"),
                       (tot_fees, "fees")):
        _tot_d.clear()
        _tot_d.update(_foot_by_currency((cur, d[_k]) for (_s, cur), d
                                        in agg.items()))
    if getattr(args, "json", False):
        _json_out({"rows": [dict(d, symbol=sym, currency=cur,
                                 buys=int(d["buys"]), sells=int(d["sells"]),
                                 **{k: M.json_money(v, M.DERIVED_PLACES)
                                    for k, v in exact[(sym, cur)].items()})
                            for (sym, cur), d in sorted(agg.items())],
                   "totals": {c: {"bought": M.shown(tot_bought.get(c, 0.0), 2),
                                  "sold": M.shown(tot_sold.get(c, 0.0), 2),
                                  "fees": M.shown(tot_fees.get(c, 0.0), 2)}
                              for c in sorted(set(tot_bought) | set(tot_sold)
                                              | set(tot_fees))},
                   "sold_taxable": {c: M.shown(v, 2)
                                    for c, v in tax_sold.items()},
                   "sheltered_included": sorted(shel_accts),
                   "scope": scope}, schema="trades-sum")
        return
    if not agg:
        _out.put(f"No trades in {scope}.")
        return
    out_lines = ["SYMBOL CUR BUYS SELLS BOUGHT SOLD FEES"]
    for (sym, cur), d in sorted(agg.items()):
        out_lines.append(" ".join([
            sym, cur, str(int(d["buys"])), str(int(d["sells"])),
            money(d["bought"]), money(d["sold"]), money(d["fees"])]))
    page = _out.Doc(exact=True, width_=0, live=True)
    _vprint(f"TRADES — {scope}")
    page.blank()
    _print_report_table(out_lines, fit=True, drop=(6, 2, 3))
    curs = sorted(set(tot_bought) | set(tot_sold) | set(tot_fees))
    page.blank()
    for c in curs:
        _vprint(f"TOTAL {c}: bought {money(tot_bought.get(c, 0.0))}, "
                f"sold {money(tot_sold.get(c, 0.0))}, "
                f"fees {money(tot_fees.get(c, 0.0))}", hang="  ")
    if shel_accts:
        # A US project's sheltered accounts are IRAs (A2-0735).
        from taxjson.lib.country import CountryError, settings_country
        try:
            _usa_t = settings_country(load_settings(
                Path(args.dir).resolve(), missing_ok=True)) == "usa"
        except CountryError:
            _usa_t = False
        _kind = "retirement (IRA)" if _usa_t else "registered"
        _vprint(f"SOLD IN TAXABLE ACCOUNTS: "
                + (", ".join(f"{money(v)} {c}" for c, v
                             in sorted(tax_sold.items())) or "0.00")
                + f"  (the totals include the {_kind} "
                f"{', '.join(sorted(shel_accts))})", hang="  ")


def cmd_fees_sum(args: argparse.Namespace) -> None:
    """Convenience wrapper over `taxjson-fees-sum`: trading-fee report by brokerage
    (converted to the base currency), reading the parsed per-broker JSONs in
    work/. Resolves cache / base currency / rates from the project. Like the
    other roll-ups it takes an optional PERIOD window (30d/6w/…, wired to
    `taxjson-fees-sum --since`), defaulting to the tax year; a lone non-period
    positional is read as an account (its per-broker files only)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    if not cache.exists():
        _die("no work/ — run `tjs run` first")
    _require_books(root, also=("*_*.json",))
    settings = load_settings(root, missing_ok=True)

    period, account = _lone_positional(root, args.period, args.account)

    cmd = _cmd("taxjson-fees")
    if account:
        # Scope to one account by feeding only its parsed per-broker files;
        # non-broker JSONs (…_base.json etc.) are skipped by taxjson-fees-sum.
        # Exclude files that actually belong to a LONGER-named sibling
        # account sharing this prefix (accounts `margin` and `margin_us`:
        # the glob "margin_*.json" also matches margin_us_ib.json, leaking
        # the sibling's fees into this account's total).
        accounts_cfg = load_config(root, missing_ok=True).get("accounts") or {}
        siblings = [a for a in accounts_cfg
                    if a != account and a.startswith(f"{account}_")]
        files = sorted(
            f for f in cache.glob(f"{account}_*.json")
            if not any(f.name.startswith(f"{sib}_") for sib in siblings))
        if not files:
            _die_input(f"no files for account {account!r} in work/ — run "
                       f"`tjs run` first, or check the name")
        cmd += [str(f) for f in files]
    else:
        cmd += ["--cache", str(cache)]
        _warn_accounts_without_books(root, _discover_tx_accounts(cache),
                                     "fees-sum", "transaction file")

    tok = (period or "").strip().lower()
    if period and _YEAR_TOKEN_RE.fullmatch(tok):         # literal year window
        cmd += ["--year", tok]
    elif period and tok in _TAX_YEAR_TOKENS:             # explicit tax_year
        year = settings.year
        if year:
            cmd += ["--year", str(year)]
    elif period and tok not in ("all", "max"):           # look-back window
        # Through today, as every period view (backlog TJ-0329).
        cmd += ["--since", _tx_period_cutoff(period).isoformat(),
                "--until", _period_end(period)]
    elif not period:                                     # default: tax year
        year = settings.year
        if year:
            cmd += ["--year", str(year)]

    # taxjson-fees-sum needs --rates alongside --to; pass both only when the rate
    # file exists, else report native per-brokerage amounts (no conversion).
    rates = cache / "to_base.csv"
    if rates.exists():
        cmd += ["--to", _base(settings),
                "--rates", str(rates)]
    if args.by_account:
        cmd += ["--by-account"]
    if (_PL.ticker_map_path(root)).exists():
        # DELETE'd symbols' fees, like the books (S038-11).
        cmd += ["--ticker-map", str(_PL.ticker_map_path(root))]
    if args.json:
        cmd += ["--json"]
    elif not _details(args):
        # Essentials first (docs/output-style.md).
        cmd += ["--brief"]
    _exec_tool(cmd)


def _register_divs_sum(sub: argparse._SubParsersAction) -> None:
    # Period-aware roll-ups: optional PERIOD positional (30d/6w/…), else the
    # config tax year. A lone non-period positional is read as an account.
    p_dsum = sub.add_parser(
        "divs-sum",
        help="Dividend total per ticker over a window",
        description="Dividend total per ticker over a window (default: "
                    "the tax year), each payment counted in its own tax "
                    "year: the pay date, or in Canada a Canadian trust's "
                    "record date (`tjs tax-logic` states the "
                    "rule).")
    p_dsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_dsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_dsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_dsum.set_defaults(func=cmd_divs_sum)


def _register_staking_sum(sub: argparse._SubParsersAction) -> None:
    _add_staking_parser(
        sub, "staking-sum", cmd_staking_sum,
        "Reward and commission totals by exchange and coin")


def _register_dil_sum(sub: argparse._SubParsersAction) -> None:
    p_dilsum = sub.add_parser(
        "dil-sum",
        help="Payment-in-lieu total per symbol, with its treatment",
        description="Payment-in-lieu total per symbol over a window "
             "(default: the tax year) with each row's treatment — "
             "ordinary income, or (Canada) a Canadian dealer's payment "
             "on a Canadian corporation's share, a dividend under ITA "
             "s.260 (also in divs-sum).")
    p_dilsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_dilsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_dilsum.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_dilsum.set_defaults(func=cmd_dil_sum)


def _register_roc_sum(sub: argparse._SubParsersAction) -> None:
    p_rsum = sub.add_parser(
        "roc-sum",
        help="Return-of-capital total per ticker",
        description="Return-of-capital total per ticker over a window "
             "(default: the tax year): the ACB (Canada, T3 box 42) or "
             "basis (USA, Form 1099-DIV box 3) adjustments.")
    p_rsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_rsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_rsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_rsum.set_defaults(func=cmd_roc_sum)


def _register_winners_losers(sub: argparse._SubParsersAction) -> None:
    for _side, _fn, _what in (
            ("winners", cmd_winners,
             "the biggest realized WINNERS (gains above zero), best "
             "first"),
            ("losers", cmd_losers,
             "the biggest realized LOSERS (gains below zero), worst "
             "first")):
        p_win = sub.add_parser(
            _side,
            help=("Biggest realized winners by ticker" if _side
                  == "winners" else "Biggest realized losers by ticker"),
            description=f"Per-ticker realized gains ranked: {_what}, "
                        f"over a window (default: the tax year); options "
                        f"are grouped under their underlying, with the "
                        f"taxable / sheltered split of what is shown. "
                        f"--json carries every ticker, ranked from this "
                        f"side.")
        p_win.add_argument("period", nargs="?", help=_PERIOD_HELP)
        p_win.add_argument("account", nargs="?",
                           help="Account (default: all)")
        p_win.add_argument("--top", type=int, default=10, metavar="N",
                           help="Show the first N (default: "
                                "%(default)s); --json carries all")
        p_win.add_argument("--json", action="store_true",
                           help="Emit JSON instead of text")
        p_win.set_defaults(func=_fn)


def _register_ccd_sum(sub: argparse._SubParsersAction) -> None:
    p_ccd = sub.add_parser(
        "ccd-sum",
        help="Covered-call gains per underlying",
        description="Covered-call (short call) realized-gain summary per "
                    "underlying over a window (default: the tax year), "
                    "split into taxable and sheltered parts when "
                    "registered accounts contribute.")
    p_ccd.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_ccd.add_argument("account", nargs="?", help="Account (default: all)")
    p_ccd.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_ccd.set_defaults(func=cmd_ccd_sum)


def _register_leaps_sum(sub: argparse._SubParsersAction) -> None:
    p_lsum = sub.add_parser(
        "leaps-sum",
        help="LEAPS gains per contract",
        description="Realized-gain summary for closed LEAPS positions, "
                    "per contract with a total, over a window (default: "
                    "the tax year).")
    p_lsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_lsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_lsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_lsum.set_defaults(func=cmd_leaps_sum)


def _register_trades_sum(sub: argparse._SubParsersAction) -> None:
    p_tsum = sub.add_parser(
        "trades-sum",
        help="Buys, sells and fees per ticker over a window",
        description="Trade summary per ticker (buys and sells, value, "
                    "fees) over a window (default: the tax year).")
    p_tsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_tsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_tsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_tsum.set_defaults(func=cmd_trades_sum)


def _register_fees_sum(sub: argparse._SubParsersAction) -> None:
    p_fsum = sub.add_parser(
        "fees-sum",
        help="Trading fees by broker over a window",
        description="Trading-fee report by brokerage (base currency) over "
             "a window (default: tax year; trade dates); the totals "
             "`tjs run` writes to reports/fees.rpt, by account by "
             "default.")
    p_fsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_fsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_fsum.add_argument("--by-account", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Break fees down by account (default; use "
                             "--no-by-account for brokerage totals only)")
    p_fsum.add_argument("--json", action="store_true",
                        help="Machine-readable JSON instead of the report")
    p_fsum.set_defaults(func=cmd_fees_sum)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_divs_sum(sub)
    _register_staking_sum(sub)
    _register_dil_sum(sub)
    _register_roc_sum(sub)
    _register_winners_losers(sub)
    _register_ccd_sum(sub)
    _register_leaps_sum(sub)
    _register_trades_sum(sub)
    _register_fees_sum(sub)
