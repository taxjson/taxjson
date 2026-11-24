"""The Summaries commands but sum and estimate: amt, div-estimate, fx-cash, instalments, option-yield, plot-tax, stats, win-rates."""
import argparse
import re
import sys
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from taxjson.project.accounts import account_of, accounts_of, estimate_of
from taxjson.project.settings import Settings, settings_of
from taxjson.lib import out as _out
from taxjson.lib import project_layout as _PL
from taxjson.lib.report_fmt import fmt_money
from taxjson.model.result import GainRow, Holding
from taxjson.model import money as M
from taxjson.lib.stage_msg import emit_line

from taxjson.cli.common import (
    _refuse_cash_older_than_inputs, _rerun_hint, _side_doc_or_die,
    _account_group_of, _base_currency, _box18_fractions, _child_error,
    _relay_child_stderr, _discover_tx_accounts, _gains_all_file,
    _gains_row_date, _is_period, _ISO_DATE_RE, _load_json_or_die,
    _option_openings, _out_act_prefix, _self_cmd, _settle_basis,
    _split_pool_of, _tt_openings, _tx_period_cutoff, _view_income_rules,
    _view_msg, _view_window, _warn_accounts_without_books,
    _warn_artifact_year, _warn_period_or_account, _warn_run_state,
    _window_gains_files, _work_read_why, _year_keep, _OTHER_LOSSES_HELP,
    _PERIOD_HELP, _add_deduction_flags,
)
from taxjson.pipeline import run_state
from taxjson.pipeline.config import (
    load_settings,
    _config_table_warnings, _country,
    _use_project_ticker_map, load_config,
)
from taxjson.pipeline.freshness import timeline_beside
from taxjson.pipeline.inputs import _base
from taxjson.pipeline.messages import _current_cmd, _details, _die, _die_input
from taxjson.pipeline.run import (
    _fx_cash_doc, _fx_cash_ledger_choice, _fx_cash_v2_doc, _native_tx_file,
    FX_CASH_LEDGERS,
)
from taxjson.pipeline.sanity import _has_inputs, _json_out
from taxjson.pipeline.stages import (
    _read_work_doc, _tax_date_basis,
)
from taxjson.lib.cli_diag import tax_year as _tax_year_arg


def _warn_config_tables(root: Path) -> None:
    """Print _config_table_warnings once for a project (estimate reads
    [estimate] twice; instalments reads both tables)."""
    for msg in _config_table_warnings(load_config(root, missing_ok=True)):
        if msg in run_state.current.config_warned:
            continue
        run_state.current.config_warned.add(msg)
        _cmd = _current_cmd()
        _pfx = f"tjs {_cmd}" if _cmd else "tjs"
        emit_line(f"{_pfx}: warning: taxjson.toml: {msg}", file=sys.stderr)


def _nonneg_money(value: Any, what: str) -> float:
    """A config money figure: a finite, non-negative number (a TOML
    boolean is refused — float(True) is 1.0). Dies naming `what`."""
    import math as _math
    # A TOML number only: float() also read the string "5_000" as 5000
    # and "nan" as nan (S043-05).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _die(f"{what} must be a number, got {value!r}")
    try:
        f = float(value)
    except (TypeError, ValueError):
        _die(f"{what} must be a number, got {value!r}")
    if not _math.isfinite(f) or f < 0:
        _die(f"{what} must be a non-negative finite number (enter it as "
             f"a positive amount), got {value!r}")
    return f


def _estimate_deductions(root: Path, args) -> Tuple[float, float]:
    """(deductions, carrying_charges) for the Canada estimate: CLI
    flags win, else the [estimate] table, else zero. `deductions` are
    lines 20700-23500 the AMT allows in full (RRSP 20800, FHSA, RPP);
    `carrying_charges` is line 22100 (50% under the post-2024 AMT).
    other_income stays >= 0: deductions are their own input, not a
    negative income (R1-213)."""
    import math as _math
    _warn_config_tables(root)
    est = estimate_of(load_config(root, missing_ok=True))
    out = []
    for key in ("deductions", "carrying_charges"):
        v = getattr(args, key, None)
        src = f"--{key.replace('_', '-')}"
        if v is None:
            v, src = getattr(est, key), f"[estimate] {key}"
        if isinstance(v, bool):
            _die(f"{src} must be a number, got {v!r}")
        try:
            f = float(v or 0.0)
        except (TypeError, ValueError):
            _die(f"{src} must be a number, got {v!r}")
        if not _math.isfinite(f) or f < 0:
            _die(f"{src} must be a non-negative finite number (the "
                 f"amount you deduct, as a positive figure), got {v!r}")
        out.append(f)
    return out[0], out[1]


def _estimate_lt_losses(root: Path, args) -> float:
    """The US long-term capital loss carryover (Schedule D line 14):
    --long-term-losses, else [estimate] long_term_losses, else 0
    (`--other-losses` is then the short-term carryover; tax-logic
    US-EST-CARRY-TERM, re-audit A2-0481 / A2-0809). Both are refused in
    a Canada project (lib/country FLAG_COUNTRY / CONFIG_COUNTRY)."""
    import math as _math
    v = getattr(args, "long_term_losses", None)
    src = "--long-term-losses"
    if v is None:
        est = estimate_of(load_config(root, missing_ok=True))
        v, src = est.long_term_losses, "[estimate] long_term_losses"
    if isinstance(v, bool):
        _die(f"{src} must be a number, got {v!r}")
    try:
        f = float(v or 0.0)
    except (TypeError, ValueError):
        _die(f"{src} must be a number, got {v!r}")
    if not _math.isfinite(f) or f < 0:
        _die(f"{src} must be a non-negative finite number (enter loss "
             f"carryovers as positive amounts), got {v!r}")
    return f


def _carry_inputs(root: Path, args, cfg: Dict[str, Any], ol: float,
                  ltl: float):
    """The carry-forwards the estimate uses (lib/carryforward):
    explicit input wins (--other-losses / --long-term-losses, their
    [estimate] keys; [estimate] amt_carryover),
    else the latest close-year lock before the project year. Returns
    (other_losses, lt_losses, amt_carryover_by_year | None, sources) —
    `sources` says where each figure came from (printed and in --json;
    tax-logic CA-CARRY-03, CA-AMT-08, US-CARRY-02)."""
    from taxjson.lib import carryforward as CF
    settings = settings_of(cfg)
    country = _country(settings)
    est_cfg = estimate_of(cfg)

    def _given(flag_attr: str, key: str) -> Optional[str]:
        if getattr(args, flag_attr, None) is not None:
            return "--" + flag_attr.replace("_", "-")
        if est_cfg.has(key):
            return f"[estimate] {key}"
        return None
    src: Dict[str, Any] = {
        "other_losses": _given("other_losses", "other_losses"),
        "long_term_losses": (_given("long_term_losses", "long_term_losses")
                             if country == "usa" else None),
        "amt_carryover": None, "notes": [],
        "other_income_entered": _given("other_income", "other_income")
        is not None,
        "other_income_value": None}
    explicit = bool(src["other_losses"] or src["long_term_losses"])
    try:
        lr = CF.resolve_losses(root, settings, country, explicit)
    except CF.CarryInputError as e:
        _die(str(e))
    if "other_losses" in lr:
        ol = float(lr["other_losses"])
        src["other_losses"] = lr["source"]
        if country == "usa":
            ltl = float(lr.get("lt_losses") or 0.0)
            src["long_term_losses"] = lr["source"]
        src["notes"] += lr["notes"]
        if lr.get("partial_lock"):
            src["partial_lock"] = lr["partial_lock"]
    amt = None
    if country == "canada":
        try:
            a = CF.resolve_amt(root, settings, est_cfg)
        except CF.CarryInputError as e:
            _die(str(e))
        amt = a["by_year"]
        src["amt_carryover"] = a["source"]
        if a.get("partial_lock"):
            src["partial_lock"] = a["partial_lock"]
        for n in a["notes"]:
            if n not in src["notes"]:
                src["notes"].append(n)
    return ol, ltl, amt, src


def _estimate_inputs(root: Path, args) -> Tuple[float, float]:
    """(other_income, other_losses) for the tax estimate: CLI flags
    win, else the [estimate] table, else zero. Shared by cmd_summary
    and cmd_instalments — `tjs instalments` shells out to the
    estimate, and without a common resolution the two commands
    reported different net tax owing for anyone with employment
    income (5.5x on the auditor's fixture)."""
    _warn_config_tables(root)
    est = estimate_of(load_config(root, missing_ok=True))
    import math as _math
    out = []
    for key in ("other_income", "other_losses"):
        v = getattr(args, key, None)
        src = f"--{key.replace('_', '-')}"
        if v is None:
            v, src = getattr(est, key), f"[estimate] {key}"
        # float(True) is 1.0: `other_income = true` was read as $1
        # (R1-217).
        if isinstance(v, bool):
            _die(f"{src} must be a number, got {v!r}")
        try:
            f = float(v or 0.0)
        except (TypeError, ValueError):
            _die(f"{src} must be a number, got {v!r}")
        # The ONE guard for flags and config alike (A2-1123: cmd_summary
        # kept a second copy this one masked): a NEGATIVE other_losses
        # (the "-10,000 carryover" sign trap) fabricates taxable gains,
        # and nan/inf poisons every downstream figure (REVIEW #25/#42).
        if not _math.isfinite(f) or f < 0:
            _die(f"{src} must be a non-negative finite number (enter "
                 f"loss carryovers as positive amounts), got {v!r}")
        out.append(f)
    oi_f, ol_f = out
    return oi_f, ol_f


def cmd_stats(args: argparse.Namespace) -> None:
    """`tjs stats [YEAR] [ACCOUNT] [--all-history] [--json]`: win/lose
    statistics on closed trades per asset class (lib/trade_stats) —
    economic P/L in the base currency before any superficial-loss /
    wash-sale denial, the denied total on its own line. A view, never a
    filing number."""
    from taxjson.lib import trade_stats
    root = Path(args.dir).resolve()
    if args.all_history:
        if args.period and _is_period(args.period):
            _die_input("give a YEAR or --all-history, not both")
        if args.period and args.account is None:
            args.account, args.period = args.period, None
        args.period = "all"
    settings = load_settings(root, missing_ok=True)
    country = _country(settings)
    usa = country == "usa"
    inp = _closed_trade_inputs(args, root, "stats")
    keep, scope, account = inp["keep"], inp["scope"], inp["account"]
    groups, resolved = inp["groups"], inp["resolved"]
    res = trade_stats.compute(inp["gains"], inp["books"], keep,
                              inp["date_of"], inp["crypto"],
                              inp["in_books"], pools=inp["pools"],
                              timelines=inp["timelines"],
                              split_at_open=inp["split_at_open"],
                              fifo_books=inp["fifo_books"],
                              cover_docs=inp["cover_docs"],
                              fifo_timelines=inp["fifo_timelines"],
                              history_docs=inp["history_docs"],
                              cover_history_docs=inp["cover_history_docs"])
    base = _base_currency(root)
    sheltered = bool(account) and groups.get(account) == "sheltered"
    rule = "wash-sale" if usa else "superficial-loss"
    if getattr(args, "json", False):
        _json_out(dict(res, scope=scope, currency=base,
                       accounts=sorted(resolved),
                       sheltered=sheltered,
                       basis=f"economic P/L before any {rule} denial",
                       option_premium_timing=(
                           None if usa else
                           settings.option_premium_timing
                           or "grant")), schema="stats")
        return
    money = fmt_money

    def _m(v):
        return "-" if v is None else money(v)

    def _row(label, st):
        wr = ("-" if st["win_rate"] is None
              else f"{st['win_rate'] * 100:.1f}%")
        pf = ("-" if st["profit_factor"] is None
              else f"{st['profit_factor']:.2f}")
        return [label, str(st["trades"]), str(st["wins"]),
                str(st["losses"]), wr, money(st["net_pl"]),
                _m(st["avg_win"]), _m(st["avg_loss"]),
                _m(st["largest_win"]), _m(st["largest_loss"]), pf]
    kind = ("retirement (IRA)" if usa else "registered")
    who = (f"account {account}" + (f" — {kind}, not taxable events"
                                   if sheltered else "")
           if account else _accounts_phrase(sorted(resolved)))
    from taxjson.lib.out import Doc
    doc = Doc(f"CLOSED-TRADE STATISTICS — {scope} ({base}; {who})")
    _det = _details(args)
    doc.para(f"Economic P/L in {base} before any {rule} denial, not a "
             f"filing number.")
    if _det:
        doc.blank()
    else:
        # The legend, before the table (docs/output-style.md).
        doc.para("WIN_RATE = wins / trades; PROFIT_FACTOR = gross wins / "
                 "gross losses.")
    # Too wide: the largest win/loss go first, then the averages (the
    # --json output keeps them all).
    doc.table(["CLASS", "TRADES", "WINS", "LOSSES", "WIN_RATE", "NET_P/L",
               "AVG_WIN", "AVG_LOSS", "LARGEST_WIN", "LARGEST_LOSS",
               "PROFIT_FACTOR"],
              [_row(cls, res["classes"][cls])
               for cls, _desc in trade_stats.CLASSES],
              foot=[_row("TOTAL", res["total"])], drop=(8, 9, 6, 7))
    doc.blank()
    doc.para(f"Denied by the {rule} rule (NOT subtracted above): "
             f"{money(res['denied_total'])} {base} over "
             f"{res['denied_count']} disposition(s).")
    # A move in kind (a registered account's withdrawal in kind) is no
    # trade: its P/L apart (backlog TJ-0629).
    _mv = res["moved_in_kind"]
    if _mv["count"]:
        doc.para(f"Moved in kind, not trades (NOT in the table): "
                 f"{money(_mv['net_pl'])} {base} of P/L over "
                 f"{_mv['count']} move(s), at the value they moved at.")
    if not _det:
        if res["tainted_skipped"]:
            doc.para(f"{res['tainted_skipped']} disposition(s) with an "
                     f"unknown cost are not counted — tjs "
                     f"find-missing-history")
        from taxjson.lib.out import details_hint as _dh
        doc.para(_dh("tjs stats --details", "how the trades are counted"))
        doc.print()
        return
    doc.section("How the trades are counted")
    doc.items([
        "WIN_RATE = wins / trades (a break-even trade is neither); "
        "PROFIT_FACTOR = gross wins / gross losses.",
        "One trade per closing disposition (a partial close is its own "
        "trade).",
        "A written option is one trade from write to close, whatever the "
        "premium timing: the premium minus the buy-back, or the premium "
        "kept at expiry or assignment."])
    if res["assigned_written_options"]:
        doc.item(f"{res['assigned_written_options']} assigned written "
                 f"option close(s): the premium counts on the option row "
                 f"and is taken out of the shares' P/L (the tax rules "
                 f"fold it into the shares' proceeds or cost).")
    if res["exercised_long_options"]:
        doc.item(f"{res['exercised_long_options']} exercised long "
                 f"option(s) are not trades: their cost carried into the "
                 f"shares.")
    if _mv["count"]:
        doc.item(f"{_mv['count']} move(s) in kind (a registered "
                 f"account's withdrawal in kind) are not trades: the "
                 f"books count each as a sale at its value, so the shares "
                 f"left keep their cost, but nothing was sold. Their P/L "
                 f"is the line under the table.")
    if res["tainted_skipped"]:
        doc.item(f"{res['tainted_skipped']} disposition(s) with an "
                 f"unknown cost (no purchase in your files) are not "
                 f"counted.")
    doc.print()


def _view_positionals(items, accounts_cfg, *,
                      all_history: bool = False
                      ) -> Tuple[Optional[str], Optional[str], List[str]]:
    """(period, account, the rest upper-cased) of the free-order
    positionals of `win-rates` / `option-yield` (`QQQ 1y margin`, any
    order). An exact configured account name is an account even when
    it starts with a digit (`123abc`: it was refused as an invalid
    period, backlog TJ-0909); one that is also a period (`2025`) is the
    account when a period is given beside it (or --all-history), the
    period when an account is, the first of two such tokens the period,
    and alone the period, said in a warning. A digit-leading token with
    a dot is a listing (`1234.HK`: refused as an invalid period, backlog
    TJ-0785), unless a number stands after the dot (`1.5y`, `2.5m`: read
    as a symbol, the window fell back to the tax year: backlog TJ-1085);
    any other digit-leading non-period is a malformed period."""
    # A symbol's future `/` or `\` prefix is `F:`, as the books spell it
    # (lib/futures_prefixes, backlog TJ-5150).
    from taxjson.lib.futures_prefixes import canonical_futures_symbol
    period = account = None
    rest: List[str] = []
    both: List[str] = []
    for tok in (items or []):
        t = str(tok or "").strip()
        if not t:
            continue
        is_p, is_a = _is_period(t), t in accounts_cfg
        if is_p and is_a:
            both.append(t)
        elif period is None and is_p:
            period = t
        elif account is None and is_a:
            account = t
        elif (t[:1].isdigit() and not is_p
              and ("." not in t or re.fullmatch(r"\d+\.\d+[A-Za-z]*", t))):
            _tx_period_cutoff(t)                # sys.exits: invalid period
        else:
            rest.append(canonical_futures_symbol(t.upper()))
    for i, t in enumerate(both):
        if account is None and (period is not None or all_history):
            account = t
        elif period is None and account is not None:
            period = t
        elif period is None and account is None:
            period = t
            if i == len(both) - 1:
                _warn_period_or_account(t)
        else:
            rest.append(canonical_futures_symbol(t.upper()))
    return period, account, rest


def cmd_div_estimate(args: argparse.Namespace) -> None:
    """`tjs div-estimate [PERIOD] [ACCOUNT] [--months N] [--date D]`:
    upcoming dividends projected from each held position's own dividend
    history in the books (lib/div_estimate; CA-PLAN-06 / US-PLAN-07).
    The history is the native books (work/<acct>_raw.json, what
    divs-sum reads), the shares held now each account's native
    holdings (work/<acct>_raw_gains.json inventory, one row per
    listing), the base-currency rate the books' latest (work/
    to_base.csv). Crypto accounts are left out (staking is not a
    dividend). Planning numbers only."""
    from taxjson.lib import div_estimate as DE
    from taxjson.lib.option_symbols import is_option_symbol
    from taxjson.lib.price_chain import latest_rate, load_fx_history
    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_settings(root, missing_ok=True)
    rules = _view_income_rules(root)        # refuses a project w/o country
    period, account = args.period, args.account
    if period and account is None and DE.parse_window(period) is None:
        account, period = period, None
    months = 12
    if period:
        months = DE.parse_window(period)
        if months is None:
            _die_input(f"{period!r} is not a forward window",
                       "Give Nd, Nw, Nm or Ny (6m, 1y), or --months N.")
        # The same limits as --months (a 100000y window reached a
        # date past year 9999 and a traceback; '0m' was one month:
        # backlog TJ-0039).
        if months < 1:
            _die_input(f"{period!r} is a zero-length window",
                       "Give a window of 1 to 120 months (1m to 10y), "
                       "as --months takes.")
        if months > 120:
            _die_input(f"{period!r} is longer than 120 months",
                       "Give a window up to 10y (120m).")
    if args.months is not None:
        if period:
            _die_input("give a PERIOD or --months, not both")
        if args.months < 1 or args.months > 120:
            _die_input("--months must be between 1 and 120")
        months = args.months
    if args.date:
        from taxjson.lib.dates import as_of_date_problem
        _bad = as_of_date_problem(args.date)
        if _bad:
            _die_input(_bad)
    as_of = args.date or date_cls.today().isoformat()
    accounts_cfg = accounts_of(load_config(root, missing_ok=True))
    if account and account not in accounts_cfg:
        _die_input(f"no [accounts.{account}] in taxjson.toml",
                   "Check the name.")
    from taxjson.lib.clean import no_books
    missing = no_books(cache)
    if missing:
        _die(f"{missing} — run `tjs run` first.")
    groups = _account_group_of(root)
    rows: List[Tuple[str, Dict[str, Any]]] = []
    positions: List[Dict[str, Any]] = []
    missing: List[str] = []
    # A payer's history is read from every account (one schedule per
    # listing); the account filter picks the positions estimated.
    for n in sorted(accounts_cfg):
        if accounts_cfg[n].crypto:
            continue
        native = _native_tx_file(cache, n)
        inv = cache / f"{n}_raw_gains.json"
        if native is not None:
            data = _load_json_or_die(native)
            ntx = [t for t in (data.get("transactions") or [])
                   if isinstance(t, dict)]
            rows += [(n, t) for t in ntx]
            # Units held before the data (a .tt OPENING cost=unknown
            # line), opened as the gains stage opens them: a dividend
            # row is read against the position the books hold, not a
            # short made of a sale of those units (2026-10-25 audit
            # planning-us#4).
            rows += [(n, t) for t in _tt_openings(root, ntx, native=True)
                     if str(t.get("account") or n) == n]
        if account and n != account:
            continue
        if native is None or not inv.exists():
            if _has_inputs(root, n):
                missing.append(n)
            continue
        for h in (_load_json_or_die(inv).get("inventory") or []):
            sym = str(h.get("symbol") or "").strip()       # objects: the row checks (TJ-2996)
            qty = float(Holding(h).quantity or 0.0)
            if (not sym or qty <= 1e-9 or sym.startswith("CASH.")
                    or is_option_symbol(sym.upper())):
                continue
            positions.append({"account": n, "group": groups.get(n, ""),
                              "symbol": sym, "qty": qty})
    if missing:
        _view_msg("warning", f"no native books for {', '.join(missing)} "
                  f"(work/<account>_raw.json, _raw_gains.json): not "
                  f"estimated",
                  "Run `tjs run`; a raw holdings view skipped for "
                  "mixed currencies leaves an account out too.")
    if not rows and not positions and not missing:
        _die("no books in work/ — run `tjs run` first.")
    base = _base_currency(root)
    hist = load_fx_history(cache / "to_base.csv", base)

    def _fx(cur: str):
        r, _d = latest_rate(hist, cur, as_of)
        return r

    def _fx_on(cur: str, on: str):
        # A pay date's rate (a withholding posted in another currency
        # than its dividend: backlog TJ-0548).
        r, _d = latest_rate(hist, cur, on)
        return r
    try:
        year = int(str(settings.year or as_of[:4]))
    except ValueError:
        year = int(as_of[:4])
    # The whole horizon (the window, and the January after the tax
    # year) must stay in the calendar: `--date 9999-09-01 --months 120`
    # reached year 10009 and a traceback (2026-10 audit A30).
    _bad = DE.horizon_problem(as_of, months, year)
    if _bad:
        _die_input(_bad, "Give an earlier --date or a shorter window.")
    pays = DE.payments(rows, as_of=as_of)
    # A dividend paid while the books put the account short: counted,
    # but a short is charged a dividend, not paid one, so a trade is
    # likely missing (backlog TJ-0073). One warning per account and
    # security.
    for (_a, _s), _ds in sorted(pays.short_paid.items()):
        _view_msg("warning",
                  (f"{_a}: {_s} dividend paid on {_ds[0]}" if len(_ds) == 1
                   else f"{_a}: {len(_ds)} {_s} dividends paid from "
                   f"{_ds[0]}") + " while the books show it short",
                  "A short position is charged a dividend, not paid "
                  "one: the books likely miss a purchase or a transfer "
                  "in before it. The estimate counts the payment as "
                  "income.",
                  "Find the missing trade: `tjs find-missing-history`.")
    doc = DE.estimate(positions, pays, as_of=as_of,
                      months=months, tax_year=year, base=base, fx=_fx,
                      income_date=rules.income_date if rules else None,
                      fx_on=_fx_on)
    if getattr(args, "json", False):
        _json_out(doc, schema="div-estimate")
        return
    DE.render(doc, _details(args), country=_country(settings)).print()


def _accounts_phrase(names: List[str]) -> str:
    """`taxable accounts: a, b` up to three names, else `4 taxable
    accounts` (a title that names every account wraps)."""
    if len(names) <= 3:
        return f"taxable accounts: {', '.join(names)}"
    return f"{len(names)} taxable accounts"


def _closed_trade_inputs(args: argparse.Namespace, root: Path,
                         label: str) -> Dict[str, Any]:
    """What `stats` and `win-rates` read (lib/trade_stats): the window
    and account of the positionals, each account's gains document and
    base-currency book (taxable accounts by default; a sheltered one
    only when named), the row-dating closure and the span the written
    options keep to (a year project's gains files hold one tax year)."""
    from taxjson.lib.crypto_sends import crypto_accounts
    cache = root / "work"
    keep, scope, account = _view_window(args, root)
    groups = _account_group_of(root)
    resolved, all_history = _window_gains_files(root, cache, account,
                                                args)
    if not account:
        # Taxable accounts by default; a sheltered one only when named.
        resolved = {a: f for a, f in resolved.items()
                    if groups.get(a) != "sheltered"}
        if resolved:
            _warn_accounts_without_books(
                root, list(resolved) + [a for a, g in groups.items()
                                        if g == "sheltered"],
                label, "gains file")
    if not resolved:
        what = (f"account {account!r}" if account else "a taxable account")
        _die(f"no books for {what} in work/ — run `tjs run` first")
    books: Dict[str, list] = {}
    base_rows: Dict[str, list] = {}
    gains: Dict[str, Dict[str, Any]] = {}
    for acct, path in resolved.items():
        gains[acct] = _load_json_or_die(path)
        bpath = cache / f"{acct}_base.json"
        if not bpath.is_file():
            _die(f"no base-currency book for account {acct!r} "
                 f"(work/{bpath.name})", "Run `tjs run` first.")
        base_rows[acct] = list(_load_json_or_die(bpath).get("transactions")
                               or [])
        books[acct] = base_rows[acct] + _option_openings(root,
                                                         base_rows[acct])
    settle = _settle_basis(root, next(iter(gains.values())))
    # The gains files of a year project hold that tax year only; the
    # written options (rebuilt from the whole-history base book) keep
    # to the same span.
    _years = {str((d.get("summary") or {}).get("year") or "")
              for d in gains.values()}
    _by = next(iter(_years)) if len(_years) == 1 else ""
    in_books = None
    if re.fullmatch(r"\d{4}", _by):
        def in_books(r, _y=_by):
            d = (r.get("date_settle") if settle else None) or r.get("date")
            return str(d or "").startswith(_y)
    crypto = crypto_accounts(load_config(root, missing_ok=True))
    pools = _acb_pools(root, cache, gains, groups, crypto, base_rows)
    fifo = None
    if pools is None:
        # The US lots, the stocks' missing-history openings with them: a
        # lot of unknown cost a sale takes first (backlog TJ-1097).
        books = {a: rows + _tt_openings(root, rows)
                 for a, rows in base_rows.items()}
        fifo = _fifo_books(root, cache, groups, crypto, base_rows)
    # An assignment's contract size: the project's MULT lines first, as
    # the engine sizes it (TJ-0789).
    _use_project_ticker_map(root)
    from taxjson.lib.country import engine_sort_profile
    country = _country(load_settings(root, missing_ok=True))
    return {"keep": keep, "scope": scope, "account": account,
            "groups": groups, "resolved": resolved, "gains": gains,
            "books": books, "settle": settle, "in_books": in_books,
            "date_of": lambda r: _gains_row_date(r, keep, settle),
            "crypto": crypto, "pools": pools,
            "timelines": _acb_pool_timelines(cache, pools, gains),
            "cover_docs": _pool_cover_docs(root, cache, pools, gains,
                                           all_history),
            # The written options' replay orders a split as the
            # country's engine does (backlog TJ-0697).
            "split_at_open": engine_sort_profile(country) == "ca_main",
            "fifo_books": fifo,
            "fifo_timelines": _fifo_timelines(cache, fifo, books, gains),
            "history_docs": _fifo_history_docs(root, cache, fifo, books,
                                               _by, all_history),
            "cover_history_docs": _pool_history_cover_docs(
                root, cache, pools, _by, all_history)}


def _fifo_books(root: Path, cache: Path, groups: Dict[str, str],
                crypto: Iterable[str], base_rows: Dict[str, list]
                ) -> Dict[str, list]:
    """{account: base-currency book rows} of every taxable account of a
    US project (FIFO lots per account, US-BASIS-01), whether shown or
    not: a move between two of your own accounts carries its lots, and
    an assigned put's premium with them, into the other (lib/trade_stats
    share_credits; backlog TJ-0788)."""
    crypto = set(crypto)
    out: Dict[str, list] = {}
    for acct, g in sorted(groups.items()):
        if g != "taxable" or acct in crypto:
            continue
        rows = base_rows.get(acct)
        if rows is None:
            bpath = cache / f"{acct}_base.json"
            if not bpath.is_file():
                continue
            rows = list(_load_json_or_die(bpath).get("transactions") or [])
        out[acct] = rows + _tt_openings(root, rows)
    return out


def _fifo_timelines(cache: Path, fifo: Optional[Dict[str, list]],
                    books: Dict[str, list],
                    gains: Dict[str, Dict[str, Any]]
                    ) -> Optional[Dict[str, Optional[Dict[str, Any]]]]:
    """{account: the engine's cost timeline} of a US project's lots
    (`_acb_pool_timelines`, one account at a time): what
    lib/trade_stats.share_credits reads a spin-off's part of the basis
    from (backlog TJ-1095). None in a Canadian project."""
    if fifo is None:
        return None
    accts = sorted(set(fifo) | set(books))
    tls = _acb_pool_timelines(cache, [{a: []} for a in accts], gains) or []
    return dict(zip(accts, tls))


def _stats_cover_doc(path: Path) -> Optional[Dict[str, Any]]:
    """Another account's (or an earlier year's) gains document that stats
    reads covers and deemed gains from (common `_side_doc_or_die` on the
    rows and fields its views read there: trade_stats `side_row`,
    `SIDE_FIELDS`; backlog TJ-3043, TJ-3054)."""
    from taxjson.lib.trade_stats import SIDE_FIELDS, side_row
    return _side_doc_or_die(path, SIDE_FIELDS, only=side_row)


def _fifo_history_docs(root: Path, cache: Path,
                       fifo: Optional[Dict[str, list]],
                       books: Dict[str, list], year: str,
                       all_history: bool) -> Dict[str, Dict[str, Any]]:
    """{account: full-history gains document} of a US year view whose
    taxable accounts hold an assigned option (in any of them: a move
    carries its lots to another): each account with a return of capital
    of an earlier year, or of the view's span in an account not shown.
    The year's gains rows shown do not hold its deemed gain, which took
    the premium in the lots' cost (backlog TJ-1133, TJ-1221, TJ-1255).
    Only those: the file is built on demand (_gains_all_file). An
    account whose file cannot be read is left out."""
    from taxjson.lib.option_symbols import is_option_symbol
    out: Dict[str, Dict[str, Any]] = {}
    if fifo is None or all_history or not re.fullmatch(r"\d{4}", year):
        return out
    start, after = f"{year}-01-01", f"{int(year) + 1}-01-01"
    settings = load_settings(root, missing_ok=True)
    joint = dict(fifo)
    joint.update(books)
    if not any(isinstance(r, dict) and r.get("action") == "ASSIGN"
               and is_option_symbol(str(r.get("symbol") or ""))
               for rows in joint.values() for r in rows):
        return out
    for acct, rows in sorted(joint.items()):
        rows = [r for r in rows if isinstance(r, dict)]
        until = after if acct not in books else start
        if not any(r.get("action") == "ADJUST"
                   and str(r.get("date") or "") < until
                   and float(r.get("net_amount") or 0.0) < 0
                   for r in rows):
            continue
        path = _gains_all_file(root, cache, acct, settings)
        if path is None:
            continue
        doc = _stats_cover_doc(Path(path))
        if doc is not None:
            out[acct] = doc
    return out


def _pool_history_cover_docs(root: Path, cache: Path,
                             pools: Optional[List[Dict[str, list]]],
                             year: str, all_history: bool
                             ) -> Dict[str, Dict[str, Any]]:
    """{account: full-history gains document} of the accounts of a
    Canadian year view's pools with an option assigned before the year,
    in a pool whose shares went short before it (what a put's delivery
    can have covered): lib/trade_stats.closed_trades reads
    those covers, which the year's files do not hold, so the delivery
    is not read as shares into the pool (backlog TJ-1222). The file is
    built on demand (_gains_all_file). An account whose file cannot be
    read is left out."""
    from taxjson.lib.option_symbols import is_option_symbol
    out: Dict[str, Dict[str, Any]] = {}
    if not pools or all_history or not re.fullmatch(r"\d{4}", year):
        return out
    start = f"{year}-01-01"
    settings = load_settings(root, missing_ok=True)
    for pool in pools:
        early = {a: [r for r in rows if isinstance(r, dict)
                     and str(r.get("date") or "") < start]
                 for a, rows in pool.items()}
        moved = sorted((r for rows in early.values() for r in rows
                        if r.get("action") in ("BUYSELL", "ASSIGN",
                                               "OPENING_BALANCE", "TRANSFER")
                        and not is_option_symbol(str(r.get("symbol") or ""))),
                       key=lambda r: (str(r.get("date") or ""),
                                      str(r.get("time") or "")))
        net: Dict[str, float] = {}
        went_short = False
        for r in moved:
            sym = str(r.get("symbol") or "")
            net[sym] = net.get(sym, 0.0) + float(r.get("quantity") or 0.0)
            went_short = went_short or net[sym] < -1e-6
        if not went_short:
            continue
        for acct, rows in sorted(early.items()):
            if acct in out or not any(
                    r.get("action") == "ASSIGN"
                    and is_option_symbol(str(r.get("symbol") or ""))
                    for r in rows):
                continue
            path = _gains_all_file(root, cache, acct, settings)
            if path is None:
                continue
            doc = _stats_cover_doc(Path(path))
            if doc is not None:
                out[acct] = doc
    return out


def _acb_pool_timelines(cache: Path, pools: Optional[List[Dict[str, list]]],
                        gains: Dict[str, Dict[str, Any]]
                        ) -> Optional[List[Optional[Dict[str, Any]]]]:
    """Each of `_acb_pools`' pools' engine cost timeline (lib/cost_timeline,
    `timeline_beside` the gains pass that walked it: the blended pass's
    for the blended accounts, else the account's own), None where there
    is none or it cannot be read: lib/trade_stats.pooled_put_credits
    reads a spin-off's part of the ACB from it (backlog TJ-0894)."""
    import json as _json
    if pools is None:
        return None
    out: List[Optional[Dict[str, Any]]] = []
    for pool in pools:
        acct = next(iter(pool), "")
        doc = gains.get(acct)
        wash = cache / f"{acct}_gains_wash.json"
        if doc is None and wash.is_file():
            try:
                doc = _json.loads(wash.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = None
        split = isinstance(doc, dict) and bool(
            (doc.get("metadata") or {}).get("split_from"))
        tl = None
        for g in ([cache / ".blend_gains_wash.json"] if split
                  else [wash, cache / f"{acct}_gains.json"]):
            try:
                tl = _json.loads(timeline_beside(g).read_text(
                    encoding="utf-8"))
            except (OSError, ValueError):
                continue
            break
        out.append(tl if isinstance(tl, dict) else None)
    return out


def _pool_cover_docs(root: Path, cache: Path,
                     pools: Optional[List[Dict[str, list]]],
                     gains: Dict[str, Dict[str, Any]],
                     all_history: bool) -> Dict[str, Dict[str, Any]]:
    """{account: gains document} of the accounts of `_acb_pools`' pools
    that the view does not show (a view of one account), the same kind
    of file as the shown ones (the tax year's, else the full-history
    one): lib/trade_stats.closed_trades reads their short covers, so a
    put's shares that covered another account's short keep their
    premium out of the shown account's sales (backlog TJ-0994). An
    account whose file cannot be read is left out."""
    from taxjson.lib.report_model import resolve_gains_files
    missing = sorted({a for pool in (pools or []) for a in pool}
                     - set(gains))
    if not missing:
        return {}
    files = resolve_gains_files(cache, None)
    settings = load_settings(root, missing_ok=True) if all_history else Settings()
    out: Dict[str, Dict[str, Any]] = {}
    for acct in missing:
        path = (_gains_all_file(root, cache, acct, settings)
                if all_history else files.get(acct))
        if path is None:
            continue
        doc = _stats_cover_doc(Path(path))
        if doc is not None:
            out[acct] = doc
    return out


def _acb_pools(root: Path, cache: Path, gains: Dict[str, Dict[str, Any]],
               groups: Dict[str, str], crypto: Iterable[str],
               base_rows: Dict[str, list]
               ) -> Optional[List[Dict[str, list]]]:
    """A Canadian project's average-cost pools for `stats` / `win-rates`
    (lib/trade_stats.closed_trades `pools`), each {account: base book
    with its missing-history openings}: the taxable equity accounts the
    run blended (ITA s.47: one cost per security across them; their
    gains files say `blended`), every one of them whether shown or not,
    and each other account read a pool of its own. An assigned put's
    premium is drawn by the pool's sales as the gains rows' cost holds
    it (backlog TJ-0546). None in a US project (FIFO per account)."""
    import json as _json
    if _country(load_settings(root, missing_ok=True)) == "usa":
        return None
    crypto = set(crypto)

    def book(acct: str) -> Optional[list]:
        rows = base_rows.get(acct)
        if rows is None:
            bpath = cache / f"{acct}_base.json"
            if not bpath.is_file():
                return None
            rows = list(_load_json_or_die(bpath).get("transactions") or [])
        return rows + _tt_openings(root, rows)

    def blended(acct: str) -> bool:
        doc = gains.get(acct)
        if doc is None:
            wash = cache / f"{acct}_gains_wash.json"
            if not wash.is_file():
                return False
            try:
                doc = _json.loads(wash.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
        return bool(isinstance(doc, dict)
                    and (doc.get("metadata") or {}).get("blended"))
    members = [a for a, g in sorted(groups.items())
               if g == "taxable" and a not in crypto and blended(a)]
    pools: List[Dict[str, list]] = []
    blend = {a: rows for a in members
             if (rows := book(a)) is not None}
    if blend:
        pools.append(blend)
    for acct in gains:
        if acct in blend or acct in crypto:
            continue
        rows = book(acct)
        if rows is not None:
            pools.append({acct: rows})
    return pools


def cmd_win_rates(args: argparse.Namespace) -> None:
    """`tjs win-rates [SYMBOL...] [PERIOD] [ACCOUNT] [--by
    symbol|account|month|class] [--all-history] [--json]`: the win
    rate of every closed trade in the window, grouped per share
    listing (an option under its underlying) by default, else per
    account, month of the close or asset class (lib/trade_stats
    closed_trades + group) — economic P/L in the base currency before
    any superficial-loss / wash-sale denial, as `stats` counts it. A
    view, never a filing number."""
    from taxjson.lib import trade_stats
    from taxjson.lib.option_yield import one_letter_listings, underlying_matches
    root = Path(args.dir).resolve()
    settings = load_settings(root, missing_ok=True)
    usa = _country(settings) == "usa"
    accounts_cfg = accounts_of(load_config(root, missing_ok=True))
    # The positionals: a period token, an account name, the rest are
    # symbols (`win-rates QQQ 1y margin`), in any order.
    period, account, syms = _view_positionals(
        args.items, accounts_cfg, all_history=bool(args.all_history))
    if args.all_history:
        if period:
            _die_input("give a PERIOD or --all-history, not both")
        period = "all"
    args.period, args.account = period, account
    inp = _closed_trade_inputs(args, root, "win-rates")

    def _sym_ok(sym: str) -> bool:
        # An option under its underlying, as the rows are grouped; the
        # denied and unknown-cost tallies take the same filter (backlog
        # TJ-0025).
        return (underlying_matches(trade_stats._underlying(sym), syms)
                or sym.upper() in syms)
    res = trade_stats.closed_trades(inp["gains"], inp["books"], inp["keep"],
                                    inp["date_of"], inp["crypto"],
                                    inp["in_books"],
                                    symbol_ok=_sym_ok if syms else None,
                                    pools=inp["pools"],
                                    timelines=inp["timelines"],
                                    split_at_open=inp["split_at_open"],
                                    fifo_books=inp["fifo_books"],
                                    cover_docs=inp["cover_docs"],
                                    fifo_timelines=inp["fifo_timelines"],
                                    history_docs=inp["history_docs"],
                                    cover_history_docs=inp[
                                        "cover_history_docs"])
    trades = res["trades"]
    unmatched: List[str] = []
    if syms:
        for w in syms:
            if not any(underlying_matches(t["underlying"], [w])
                       or t["symbol"].upper() == w for t in trades):
                unmatched.append(w)
    g = trade_stats.group(trades, args.by)
    base = _base_currency(root)
    scope, account = inp["scope"], inp["account"]
    sheltered = bool(account) and inp["groups"].get(account) == "sheltered"
    rule = "wash-sale" if usa else "superficial-loss"
    doc = dict(g, scope=scope, currency=base,
               accounts=sorted(inp["resolved"]), sheltered=sheltered,
               symbols=syms, unmatched_symbols=unmatched,
               basis=f"economic P/L before any {rule} denial",
               denied_total=res["denied_total"],
               denied_count=res["denied_count"],
               tainted_skipped=res["tainted_skipped"],
               # Not trades (TJ-0629): apart.
               moved_in_kind={k: res["moved_in_kind"][k]
                              for k in ("count", "net_pl")})
    if getattr(args, "json", False):
        _json_out(doc, schema="win-rates")
        return
    money = fmt_money

    def _m(v):
        return "-" if v is None else money(v)

    def _row(label, st, classes=None):
        wr = ("-" if st["win_rate"] is None
              else f"{st['win_rate'] * 100:.1f}%")
        pf = ("-" if st["profit_factor"] is None
              else f"{st['profit_factor']:.2f}")
        row = [label, str(st["trades"]), str(st["wins"]),
               str(st["losses"]), wr, money(st["net_pl"]),
               _m(st["avg_win"]), _m(st["avg_loss"]), pf]
        if args.by != "class":
            row.append(", ".join(f"{k} {n}" for k, n in
                                 (classes or {}).items()) or "-")
        return row
    kind = ("retirement (IRA)" if usa else "registered")
    who = (f"account {account}" + (f" — {kind}, not taxable events"
                                   if sheltered else "")
           if account else _accounts_phrase(sorted(inp["resolved"])))
    head = {"symbol": "TICKER", "account": "ACCOUNT", "month": "MONTH",
            "class": "CLASS"}[args.by]
    from taxjson.lib.out import Doc, act, details_hint
    d = Doc(f"WIN RATES — {scope} ({base}; {who}), by {head.lower()}")
    d.para(f"Economic P/L in {base} before any {rule} denial, not a "
           f"filing number. WIN_RATE = wins / trades; PROFIT_FACTOR = "
           f"gross wins / gross losses"
           + ("; an option under its underlying."
              if args.by == "symbol" else "."))
    headers = [head, "TRADES", "WINS", "LOSSES", "WIN_RATE", "NET_P/L",
               "AVG_WIN", "AVG_LOSS", "PROFIT_FACTOR"]
    aligns = ["<", ">", ">", ">", ">", ">", ">", ">", ">"]
    drop = (6, 7, 8)
    if args.by != "class":
        headers.append("CLASSES")
        aligns.append("<")
        drop = (9, 6, 7, 8)
    if g["rows"]:
        d.table(headers,
                [_row(r["key"], r, r.get("classes")) for r in g["rows"]],
                foot=[_row("TOTAL", g["total"], None)], aligns=aligns,
                drop=drop)
    else:
        d.para("No closed trade in the window.")
    d.blank()
    d.para(f"Denied by the {rule} rule (NOT subtracted above): "
           f"{money(res['denied_total'])} {base} over "
           f"{res['denied_count']} disposition(s).")
    _mv = res["moved_in_kind"]
    if _mv["count"]:
        d.para(f"Moved in kind, not trades (NOT in the table): "
               f"{money(_mv['net_pl'])} {base} of P/L over "
               f"{_mv['count']} move(s), at the value they moved at.")
    acts = []
    if res["tainted_skipped"]:
        acts.append(act(f"{res['tainted_skipped']} disposition(s) with an "
                        f"unknown cost are not counted",
                        "tjs find-missing-history"))
    for w in unmatched:
        _listed = one_letter_listings(
            w, (trade_stats._underlying(str(r.get("symbol") or ""))
                for rows in inp["books"].values() for r in rows))
        if _listed:
            # backlog TJ-1149: SYN.T and a class share read alike.
            acts.append(act(f"{w}: no closed trade on it in the window; the "
                            f"books hold {', '.join(_listed)}, which {w} does "
                            f"not name (a one-letter last part can be a class "
                            f"share): give the full listing",
                            f"tjs win-rates {_listed[0]}"))
            continue
        acts.append(act(f"{w}: no closed trade on it in the window",
                        "tjs trades"))
    if acts:
        d.blank()
        for a in acts:
            d.item(a[len(_out_act_prefix()):], bullet=_out_act_prefix())
    d.para(details_hint("tjs stats", "the same trades per asset class"))
    d.print()


def cmd_option_yield(args: argparse.Namespace) -> None:
    """`tjs option-yield [UNDERLYING...] [PERIOD] [ACCOUNT] [--json]`:
    how the option trades on an underlying did over a window — premiums
    in and out, the profit or loss of the contracts closed in it and
    how they closed, what is still open, and the yield on the strike
    notional at risk, time-weighted and annualized (lib/option_yield;
    CA-PLAN-07 / US-PLAN-08). The contracts are matched per account and
    series from the native books (work/<acct>_raw.json), every account
    read so a contract opened before the window still closes in it.
    Planning numbers only."""
    from taxjson.lib import markets
    from taxjson.lib import option_yield as OY
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying
    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_settings(root, missing_ok=True)
    from taxjson.lib.country import engine_sort_profile
    # (refuses a project w/o country)
    split_at_open = engine_sort_profile(_country(settings)) == "ca_main"
    # A contract's size: the project's MULT lines first (`_size`; they
    # were never read here: backlog TJ-0789).
    _use_project_ticker_map(root)
    accounts_cfg = accounts_of(load_config(root, missing_ok=True))
    # The positionals: a period token, an account name, the rest are
    # underlyings (`option-yield QQQ 1y margin`), in any order.
    period, account, unds = _view_positionals(args.items, accounts_cfg)
    args.period, args.account = period, account
    keep, scope, account = _view_window(args, root)
    if not cache.is_dir():
        _die("no work/ — run `tjs run` first.")
    accounts = ([account] if account else _discover_tx_accounts(cache))
    if account and _native_tx_file(cache, account) is None:
        _die_input(f"no native transaction file for account {account!r} "
                   f"in work/ — run `tjs run` first, or check the name")
    if not accounts:
        _die("no transaction files in work/ — run `tjs run` first")
    if not account:
        _warn_accounts_without_books(root, accounts, "option-yield",
                                     "transaction file")
    rows: List[Tuple[str, Dict[str, Any]]] = []
    first = ""
    # Every account's rows: the contracts are matched per account, and
    # an account filter keeps only that account's contracts.
    for n in _discover_tx_accounts(cache):
        if account_of(accounts_cfg, n).crypto:
            continue
        native = _native_tx_file(cache, n)
        if native is None:
            continue
        _ntx = [t for t in (_load_json_or_die(native).get("transactions")
                            or []) if isinstance(t, dict)]
        for t in _ntx:
            d = str(t.get("date") or "")
            if _ISO_DATE_RE.match(d) and (not first or d < first):
                first = d
            rows.append((n, t))
        # A contract held before the data (a .tt OPENING cost=unknown
        # line): its opening, as the gains stage adds it (audit #59).
        rows += [(n, t) for t in _option_openings(root, _ntx, native=True)]
    today = date_cls.today().isoformat()
    ty = getattr(keep, "tax_year", None)
    if ty:
        start, end = f"{ty}-01-01", f"{ty}-12-31"
    elif period and period.strip().lower() in ("all", "max"):
        start, end = (first or today), today
    elif period:
        start, end = _tx_period_cutoff(period).isoformat(), today
    else:
        start, end = (first or today), today
    # A tax year that has not begun holds nothing yet: its own (empty)
    # window, not today's (the start moved back to today and reported
    # this year's closes under next year's label: backlog TJ-0784).
    future = start > today
    if not future:
        end = min(end, today)
    if start > end:
        start = end

    def _size(t: Dict[str, Any]) -> float:
        sym = str(t.get("symbol") or "").upper()
        ovr = markets.option_contract_size(
            sym, parse_option_underlying(sym) or "")
        return float(ovr) if ovr else OY.default_size(t)

    def _mult_line(sym: str) -> Optional[float]:
        return markets.option_contract_size(
            sym, parse_option_underlying(sym) or "")
    # The books as of the window's end: a later split or rename does
    # not rewrite the window's counts (audit R10).
    # A split takes effect as the country's engine orders it (TJ-0697).
    lots = [] if future else OY.contracts(rows, size_of=_size,
                                          stated_size=_mult_line, through=end,
                                          pool_of=_split_pool_of(root),
                                          split_at_open=split_at_open)
    if future:
        _view_msg("note", f"{scope} has not begun: no contract of the "
                  f"books is in it yet")
    # The underlying's shares held now, per account and the currency of
    # their cost (the native holdings, as div-estimate reads them): the
    # yield's capital, where it is in the options' currency. A CAD cost
    # was added to a USD option's capital as USD (backlog TJ-0543).
    shares: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
    # Holdings whose cost is not all known (an opening cost=unknown):
    # no yield on their partial cost (backlog TJ-0774).
    unknown_shares: Set[Tuple[str, str, str]] = set()
    for n in _discover_tx_accounts(cache):
        if account_of(accounts_cfg, n).crypto:
            continue
        inv = cache / f"{n}_raw_gains.json"
        if not inv.exists():
            continue
        for h in (_load_json_or_die(inv).get("inventory") or []):
            sym = str(h.get("symbol") or "").strip().upper()
            v = Holding(h)
            qty = float(v.quantity or 0.0)
            if (not sym or qty <= 1e-9 or sym.startswith("CASH.")
                    or is_option_symbol(sym)):
                continue
            hcur = str(h.get("currency") or "").strip().upper()
            q0, c0 = shares.get((n, sym, hcur), (0.0, 0.0))
            shares[(n, sym, hcur)] = (q0 + qty, c0 + float(v.total_cost or 0.0))
            if h.get("unknown_cost"):
                unknown_shares.add((n, sym, hcur))
    # A class share's option root (SYNB for SYN.B.TO) is that share
    # line, as the engines read it (engine/assign class_root_aliases): its
    # shares were not found, so not counted as the capital (backlog
    # TJ-0910).
    from taxjson.engine.assign import class_root_aliases
    aliases = class_root_aliases(
        [str(t.get("symbol") or "") for _n, t in rows]
        + [k[1] for k in shares])
    doc = OY.analyze(lots, start=start, end=end, scope=scope,
                     underlyings=unds,
                     accounts=([account] if account else None),
                     groups=_account_group_of(root), shares=shares,
                     unknown_shares=unknown_shares, aliases=aliases)
    if getattr(args, "json", False):
        _json_out(doc, schema="option-yield")
        return
    # (A message on stderr owes its own blank line: lib/out
    # settling_streams. An unconditional one here was an empty stderr
    # line before every report — workflow audit 2026-10.)
    OY.render(doc).print()


def _issuer_facts(cache: Path, acct: str) -> Dict[str, Any]:
    """The issuer's country of each income row of an account's base book
    that carries one (an IB CA/US ISIN): {"by_id": {row id: Canadian
    issuer?}, "by_symbol": {symbol: [(date, Canadian issuer?), ...] in
    date order}}. The estimate splits Canadian (eligible) from foreign
    dividends with the SAME test the engine uses for a s.260 payment in
    lieu, income_dating.is_canadian_issuer, not the listing suffix alone
    (A2-0319, A2-0662), row by row: one flag per symbol, the last row's
    in all history, classified every payment of the symbol by it, an
    earlier year's too (backlog TJ-0726). A symbol with no such row is
    not in by_symbol: the caller falls back to its listing."""
    from taxjson.characterize.income_dating import is_canadian_issuer
    by_id: Dict[str, bool] = {}
    by_sym: Dict[str, List[Tuple[str, bool]]] = {}
    try:
        data = _read_work_doc(cache / f"{acct}_base.json")
    except (OSError, ValueError):
        return {"by_id": by_id, "by_symbol": by_sym}
    for t in data.get("transactions", []) or []:
        if not isinstance(t, dict) or not t.get("issuer_country"):
            continue
        if t.get("action") not in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            continue
        sym = str(t.get("symbol") or "")
        if not sym:
            continue
        ca = is_canadian_issuer(t)
        if t.get("id"):
            by_id[str(t["id"])] = ca
        by_sym.setdefault(sym, []).append((str(t.get("date") or ""), ca))
    for v in by_sym.values():
        v.sort(key=lambda x: x[0])
    return {"by_id": by_id, "by_symbol": by_sym}


def _issuer_fact_at(facts: List[Tuple[str, bool]], date: str) -> bool:
    """The symbol's issuer fact in force on `date`: the latest row's on
    or before it, else the earliest (a later row never reclassifies an
    earlier payment; backlog TJ-0726)."""
    pick = facts[0][1]
    for d, ca in facts:
        if d and date and d > date:
            break
        pick = ca
    return pick


def _issuer_split(rows, issuer: Optional[Dict[str, Any]]
                  ) -> Dict[Tuple[str, str], List[float]]:
    """{(symbol, currency): [Canadian, foreign]} dividends of the gains
    rows `rows` whose symbol has issuer facts (`_issuer_facts`), each
    row classified by its own base row's issuer country, a row without
    one by the symbol's fact on its date (backlog TJ-0726)."""
    out: Dict[Tuple[str, str], List[float]] = {}
    by_id = (issuer or {}).get("by_id") or {}
    by_sym = (issuer or {}).get("by_symbol") or {}
    if not by_sym:
        return out
    for t in rows or []:
        if not isinstance(t, dict) or t.get("action") not in (
                "DIVIDEND", "DIVIDEND_IN_LIEU"):
            continue
        sym = str(t.get("symbol") or "")
        if sym not in by_sym:
            continue
        ca = by_id.get(str(t.get("id") or ""))
        if ca is None:
            ca = _issuer_fact_at(by_sym[sym], str(t.get("date") or ""))
        cell = out.setdefault((sym, t.get("currency") or "?"), [0.0, 0.0])
        cell[0 if ca else 1] += float(GainRow(t).dividend or 0)
    return out


def _section_1256_of_docs(docs, year, settings: Settings
                          ) -> Tuple[float, float]:
    """`_section_1256_gain` over gains documents already read (plot-tax
    passes each month's slice, so its months split the §1256 P/L as the
    year's estimate does)."""
    from taxjson.lib.futures import section_1256_kind
    key = ("date" if _tax_date_basis(settings) == "trade"
           else "date_settle")
    ystr = str(year or "")
    total = long_term = 0.0
    for data in docs:
        for t in data.get("transactions", []) or []:
            if not isinstance(t, dict) or t.get("action", "BUYSELL") in (
                    "DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            d = str(t.get(key) or t.get("date") or "")
            if ystr and not d.startswith(ystr):
                continue
            if section_1256_kind(str(t.get("symbol") or "")):
                gain = float(GainRow(t).gain or 0.0)
                total += gain
                if t.get("term") == "LONG_TERM":
                    long_term += gain
    return total, long_term


def _box18_into_estimate(root: Path, est: Dict[str, float], accounts,
                         year, foreign_by_acct: Dict[str, float],
                         foreign_by_security: Optional[
                             Dict[str, Dict[str, float]]] = None) -> None:
    """Move the tax year's T5 box 18 capital-gains dividends named in
    [[capital_gains_dividends]] out of the estimate's dividends and into
    `est["cg_div"]` (taxed as capital gains by estimate_canada). Base-
    currency amounts from each taxable account's base book (the native
    row's id is the base row's id); a foreign one out of its security's
    dividends in `foreign_by_security` too. No map: nothing changes."""
    fr = _box18_fractions(root)
    est.setdefault("cg_div", 0.0)
    if not fr:
        return
    from taxjson.lib.cg_dividends import row_amount
    rules = _view_income_rules(root)
    ystr = str(year or "")
    cache = root / "work"
    # Each bucket's mapped amounts are netted first and taken out once:
    # floored row by row, a 100 dividend and its -60 reversal took
    # max(0, 40 - 100) = 0, then 0 + 60 = 60 of phantom dividends
    # (backlog TJ-1020).
    ca_cg = foreign_cg = 0.0
    acct_cg: Dict[str, float] = {}
    sec_cg: Dict[Tuple[str, str], float] = {}
    for acct in sorted(accounts):
        p = cache / f"{acct}_base.json"
        try:
            data = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # Named once, from the project; exit 2 (backlog TJ-0641).
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
            if ystr and not str(d).startswith(ystr):
                continue
            cg = row_amount(t) * f
            from taxjson.characterize.income_dating import is_canadian_issuer
            # The issuer test the dividend split uses (A2-0319).
            if is_canadian_issuer(t):
                ca_cg += cg
            else:
                foreign_cg += cg
                acct_cg[acct] = acct_cg.get(acct, 0.0) + cg
                sym = str(t.get("symbol") or "")
                sec_cg[(acct, sym)] = sec_cg.get((acct, sym), 0.0) + cg
            est["cg_div"] += cg
    if ca_cg:
        est["div_ca"] = max(0.0, est["div_ca"] - ca_cg)
    if foreign_cg:
        est["div_foreign"] = max(0.0, est["div_foreign"] - foreign_cg)
    for acct, cg in acct_cg.items():
        foreign_by_acct[acct] = max(0.0, foreign_by_acct.get(acct, 0.0)
                                    - cg)
    for (acct, sym), cg in sec_cg.items():
        by = (foreign_by_security or {}).get(acct)
        if by is not None and sym in by:
            by[sym] = max(0.0, by[sym] - cg)


def _fold_estimate_income(res: Dict[str, Any], acct: str,
                          est: Dict[str, float],
                          foreign_by_acct: Dict[str, float], *,
                          crypto: bool,
                          issuer: Optional[Dict[str, Any]] = None,
                          rows=None,
                          foreign_by_security: Optional[
                              Dict[str, Dict[str, float]]] = None
                          ) -> None:
    """Add one taxable account's `summarize_gains` aggregates to the
    estimator's inputs: realized gains (and their ST/LT split), its
    dividends as Canadian (eligible), foreign (`foreign_by_acct` too,
    for the FTC, and `foreign_by_security` {account: {symbol: amount}}
    when given: the 15% limit is each security's, backlog TJ-0638) or,
    in a crypto account, staking rewards (ordinary
    income, nothing withheld — not foreign dividends with an assumed
    15% FTC), and the payments in lieu. `sum` / `estimate` and
    `plot-tax` feed the estimator through this one path. `issuer`:
    `_issuer_facts` of the account; `rows`: the gains rows `res`
    summarizes, whose dividends are then classified row by row (each
    by its own issuer country, backlog TJ-0726)."""
    from taxjson.characterize.income_dating import is_canadian_listing
    by_sym = (issuer or {}).get("by_symbol") or {}
    split = _issuer_split(rows, issuer) if rows is not None else {}
    for ticker, cur_map in res.get("ticker_stats", {}).items():
        # The issuer's ISIN country when the books carry it, else the
        # listing (A2-0319, A2-0662); with no rows to split, the
        # symbol's latest fact.
        is_ca_listed = (by_sym[ticker][-1][1] if ticker in by_sym
                        else is_canadian_listing(ticker))

        def _foreign(amount: float) -> None:
            est["div_foreign"] += amount
            foreign_by_acct[acct] = foreign_by_acct.get(acct, 0.0) + amount
            if foreign_by_security is not None:
                by = foreign_by_security.setdefault(acct, {})
                by[ticker] = by.get(ticker, 0.0) + amount
        for cur, s in cur_map.items():
            est["realized"] += (float(s.get("cap", 0) or 0)
                                + float(s.get("opt", 0) or 0))
            est["st"] += float(s.get("st_gain", 0) or 0)
            est["lt"] += float(s.get("lt_gain", 0) or 0)
            d = float(s.get("div", 0) or 0)
            cell = split.get((ticker, cur))
            if crypto:
                est["staking"] += d
            elif cell is not None and abs(cell[0] + cell[1] - d) <= 0.005:
                # Row by row: each payment by its own issuer's country.
                est["div_ca"] += cell[0]
                if cell[1]:
                    _foreign(cell[1])
            elif is_ca_listed:
                est["div_ca"] += d
            else:
                _foreign(d)
            _p = float(s.get("pil", 0) or 0)
            est["pil"] += _p
            # Compensation paid on a short (US: no deduction under the
            # standard deduction, US-INC-04; TJ-0728), row by row: one
            # netted against a payment received is still paid (TJ-1060),
            # a reversal of one received nets. A security's net below
            # zero counts whole, as on gains files of an older run.
            _paid = max(float(s.get("pil_paid", 0) or 0), -_p)
            if _paid > 0:
                est["pil_paid"] = est.get("pil_paid", 0.0) + _paid


def cmd_plot_tax(args: argparse.Namespace) -> None:
    """`tjs plot-tax [--other-income AMT] [--other-losses AMT]
    [--deductions AMT] [--carrying-charges AMT] [--long-term-losses
    AMT] [--province P] [--json]`: the tax the year's investment income
    generated month by month as an ASCII chart against the year's
    estimate (lib/tax_plot; CA-PLAN-08 / US-PLAN-09). The estimator
    `estimate` uses runs once per month on the taxable accounts' gains
    and income dated up to the month's end (the same inputs, the same
    code path); a month's tax is that run less the month before's.
    Planning numbers only."""
    from taxjson.lib.gain_summary import summarize_gains
    from taxjson.lib import tax_plot as TP
    from taxjson.lib.report_model import resolve_gains_files
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root) if _PL.has_config(root) else {}
    if not cfg:
        _die("the tax estimate needs taxjson.toml",
             "It reads the country and the account types from it.")
    settings = settings_of(cfg)
    country = _country(settings)
    year = settings.year
    if year is None:
        _die("no `year` under [settings] in taxjson.toml",
             "The chart is the project's tax year, month by month.")
    year = int(year)
    prov = _plot_tax_province(args, settings, year, country)
    taxable = {n for n, c in accounts_of(cfg).items()
               if c.taxable}
    files = {a: p for a, p in resolve_gains_files(cache, None).items()
             if a in taxable}
    if not files:
        _die_input("no gains files for a taxable account in work/",
                   "Run `tjs run` first.")
    _warn_artifact_year(files, year)
    _warn_run_state(root, cfg)
    oi, ol = _estimate_inputs(root, args)
    ded, cc = _estimate_deductions(root, args)
    ltl = _estimate_lt_losses(root, args)
    ol, ltl, amt_in, carry_src = _carry_inputs(root, args, cfg, ol, ltl)
    carry_src["other_income_value"] = oi
    docs = _plot_tax_docs(files, root)
    settle = _settle_basis(root, next(iter(docs.values())))
    keep = _year_keep(str(year))

    def date_of(t):
        return _gains_row_date(t, keep, settle)
    issuer = {a: _issuer_facts(cache, a) for a in docs}
    crypto = {n for n, c in accounts_of(cfg).items()
              if c.crypto}

    def inputs(through: Optional[str]):
        est = dict(realized=0.0, st=0.0, lt=0.0, div_ca=0.0,
                   div_foreign=0.0, pil=0.0, staking=0.0)
        fba: Dict[str, float] = {}
        fbs: Dict[str, Dict[str, float]] = {}
        slices = []
        for a, doc in docs.items():
            sl = TP.slice_doc(doc, date_of, through) if through else doc
            slices.append(sl)
            _fold_estimate_income(summarize_gains(sl), a, est, fba,
                                  crypto=a in crypto,
                                  issuer=issuer[a],
                                  rows=sl.get("transactions") or [],
                                  foreign_by_security=fbs)
        if country in ("us", "usa"):
            # Each month splits the §1256 P/L it holds 40/60, as the
            # year's estimate does (US-FUT-03; backlog TJ-0466).
            est["s1256"], est["s1256_lt"] = _section_1256_of_docs(
                slices, year, settings)
        return est, fba, fbs

    def run(est, withheld):
        return _tax_estimate_result(
            cfg, est, other_income=oi, other_losses=ol, province=prov,
            deductions=ded, carrying_charges=cc, lt_losses=ltl,
            amt_carryover=amt_in, carry_sources=carry_src,
            **withheld)
    # The year as `estimate` computes it: the T5 box 18 capital-gains
    # dividends are dated to the year (the US §1256 split is each
    # month's own, in inputs()).
    est, fba, fbs = inputs(None)
    _box18_into_estimate(root, est, set(docs), year, fba, fbs)
    # The foreign tax withheld, as `estimate` reads it (the base books'
    # TAX rows; the 15% assumption only for an account with none in
    # the year): with no such argument every month and the year ran on
    # the assumption (backlog TJ-0299). A month's run credits what was
    # recorded by its end. The US estimate takes no such credit. A
    # spin-off's deemed dividend takes no assumed withholding (backlog
    # TJ-0422). Each security's withholding counts up to 15% of its own
    # dividends (backlog TJ-0638).
    wh_noncash: Dict[str, List[Tuple]] = {}
    wh_rows = (_withholding_rows(cache, set(docs), year, fba, wh_noncash)
               if country == "canada" else None)

    def withheld(fb, fs, through=None) -> Dict[str, Any]:
        if wh_rows is None:
            return {}
        kw = _withholding_kwargs(_withholding_split(
            wh_rows, fb, through, wh_noncash))
        kw["withholding_by_security"] = _withholding_by_security(
            wh_rows, fs, through, wh_noncash)
        return kw
    points = []
    for m in TP.months_of_year(year):
        end = TP.month_end(m)
        m_est, m_fba, m_fbs = inputs(end)
        r = run(m_est, withheld(m_fba, m_fbs, end))
        points.append({"month": m, "cum_tax": r["estimated_tax"],
                       "cum_income": r["investment_income"]})
    ry = run(est, withheld(fba, fbs))
    base = _base_currency(root)
    label = (f"canada/{prov.upper()}" if country == "canada" else "usa")
    n_routed, n_inline = _plot_tax_unknown_cost(docs)
    doc = TP.build(points, year=year, base=base,
                   year_estimate=ry["estimated_tax"],
                   year_income=ry["investment_income"], label=label,
                   accounts=docs, with_amt=ry.get("estimated_tax_with_amt"),
                   other_income=oi, unknown_cost_routed=n_routed,
                   unknown_cost_included=n_inline)
    if getattr(args, "json", False):
        if n_routed:
            from taxjson.lib import out as _out
            _out.warn(f"{n_routed} disposition(s) with an unknown cost "
                      f"(no purchase in your files) were routed to manual "
                      f"reporting — these figures EXCLUDE them",
                      prog="tjs plot-tax",
                      details=["`tjs form-export` lists them in its "
                               "MANUAL REPORTING section; report them by "
                               "hand."])
        _json_out(doc, schema="plot-tax")
        return
    from taxjson.lib import out as _out
    TP.render(doc, _out.width(sys.stdout)).print()


def _plot_tax_province(args: argparse.Namespace, settings: Settings, year: int,
                       country: str) -> Optional[str]:
    """tjs plot-tax: in Canada the province, from --province or the
    settings (the estimate's vintage set for the year); a missing or
    unsupported one stops the command. Returns it (None in the US)."""
    prov = None
    if country == "canada":
        from taxjson.lib import tax_estimate as _te
        prov = (getattr(args, "province", None)
                or str(settings.province or "")).strip()
        _te.apply_vintage(year)
        if not prov:
            _die("the canada estimate needs a province",
                 "Pass --province ON|BC|AB or set `province` under "
                 "[settings] in taxjson.toml.")
        if prov.upper() not in _te.CA_PROVINCES:
            _die(f"unsupported province {prov!r} for the estimate",
                 f"Supported: {', '.join(sorted(_te.CA_PROVINCES))}.")
    return prov


def _plot_tax_docs(files: Dict[str, Path], root: Path) -> Dict[str, Dict[str, Any]]:
    """tjs plot-tax: each taxable account's gains file read; one that
    cannot be read stops the command (exit 2). Returns them by
    account."""
    docs: Dict[str, Dict[str, Any]] = {}
    for acct, p in files.items():
        try:
            docs[acct] = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # Named once, from the project; exit 2 (TJ-0641); the advice once (TJ-2977).
            why = f"could not read {_work_read_why(p, e, root)}"
            _die_input(why, _rerun_hint(why, "A taxable account's gains cannot be left out."))
    return docs


def _plot_tax_unknown_cost(docs: Dict[str, Dict[str, Any]]) -> Tuple[int, int]:
    """tjs plot-tax: the sales with an unknown cost in the books, as
    `estimate` counts them. Returns those routed to manual reporting
    and those in-line in raw engine output."""
    # The sales with an unknown cost, counted as `estimate` counts them
    # (summarize_gains): routed to manual reporting, out of every
    # figure, or in-line in raw engine output (backlog TJ-0783).
    n_routed = sum(len(d.get("manual_reporting_required") or [])
                   for d in docs.values())
    n_inline = sum(1 for d in docs.values()
                   for t in d.get("transactions") or []
                   if isinstance(t, dict) and t.get("tainted")
                   and "gain" in t)
    return n_routed, n_inline


def _instalment_config(root: Path,
                       year: Optional[int] = None) -> Dict[str, Any]:
    """Validated [instalments] table ({} when absent). Loud on bad
    input: these figures move money."""
    from taxjson.lib.instalment_rates import BASES
    _warn_config_tables(root)
    cfg = (load_config(root, missing_ok=True).get("instalments") or {})
    if not cfg:
        return {}
    out: Dict[str, Any] = {}
    basis = str(cfg.get("basis") or "current_year")
    if basis not in BASES:
        _die(f"[instalments] basis must be one of "
             f"{', '.join(BASES)}, got {basis!r}")
    out["basis"] = basis
    for key in ("prior_year_net_tax", "second_prior_net_tax",
                "withheld", "prescribed_rate"):
        v = cfg.get(key)
        if v is None:
            continue
        # Sign, finiteness and type, like paid[].amount: withheld = -5000
        # overstated net tax owing by 10,000, withheld = nan waived the
        # instalments, a negative rate zeroed the interest, and `true`
        # read as 1.0 — all at exit 0 (R1-217).
        out[key] = _nonneg_money(v, f"[instalments] {key}")
    # CRA resets the prescribed rate quarterly and charges each day at
    # the rate in force that day — so a dated schedule is accepted and
    # applied per day. A scalar prescribed_rate stays valid.
    if (cfg.get("prescribed_rate") is not None
            and cfg.get("prescribed_rates") is not None):
        _die("[instalments] set prescribed_rate OR prescribed_rates, "
             "not both — the dated schedule would silently win and "
             "the single rate be discarded.")
    if out.get("prescribed_rate", 0) >= 1.0:
        _die(f"[instalments] prescribed_rate is a DECIMAL fraction "
             f"(0.08 = 8%), got {out['prescribed_rate']} — that would "
             f"charge {out['prescribed_rate'] * 100:.0f}% a year.")
    if cfg.get("prescribed_rates") is not None:
        if not isinstance(cfg.get("prescribed_rates"), list):
            _die("[instalments] prescribed_rates must be a LIST of "
                 "{ from, rate } tables.")
        sched = []
        for i, row in enumerate(cfg.get("prescribed_rates") or [], 1):
            if not isinstance(row, dict):
                _die(f"[instalments] prescribed_rates[{i}] must be a "
                     f"table like {{ from = \"2026-07-01\", "
                     f"rate = 0.09 }}")
            try:
                # fromisoformat, not strptime: strptime ACCEPTS
                # unpadded "2026-9-01", which then sorts and compares
                # wrong as a string ("2026-9-01" > "2026-12-31"), so
                # the segment silently applied to the wrong window.
                frm = date_cls.fromisoformat(str(row["from"])).isoformat()
                r = _nonneg_money(row["rate"],
                                  f"[instalments] prescribed_rates[{i}]"
                                  f".rate")
                if r >= 1.0:
                    _die(f"[instalments] prescribed_rates[{i}].rate is "
                         f"a DECIMAL fraction (0.08 = 8%), got {r}.")
                sched.append({"from": frm, "rate": r})
            except (KeyError, TypeError, ValueError):
                _die(f"[instalments] prescribed_rates[{i}] needs a "
                     f"YYYY-MM-DD `from` and a numeric `rate`, got "
                     f"{row!r}")
        if not sched:
            _die("[instalments] prescribed_rates is empty — remove it "
                 "or give at least one { from, rate } entry.")
        out["prescribed_rates"] = sorted(sched,
                                         key=lambda x: x["from"])
    if basis == "prior_year" and "prior_year_net_tax" not in out:
        _die("[instalments] basis 'prior_year' needs "
             "prior_year_net_tax (last year's net tax owing).")
    if basis == "cra_reminder" and not (
            "prior_year_net_tax" in out
            and "second_prior_net_tax" in out):
        _die("[instalments] basis 'cra_reminder' needs both "
             "prior_year_net_tax and second_prior_net_tax (the two "
             "preceding years' net tax owing).")
    payments = []
    if cfg.get("paid") is not None and not isinstance(cfg.get("paid"),
                                                      list):
        _die("[instalments] paid must be a LIST of "
             "{ date, amount } tables.")
    for i, row in enumerate(cfg.get("paid") or [], 1):
        if not isinstance(row, dict):
            _die(f"[instalments] paid[{i}] must be a table like "
                 f"{{ date = \"2026-03-15\", amount = 15000 }}")
        d, a = row.get("date"), row.get("amount")
        try:
            # fromisoformat (see prescribed_rates): the downstream
            # walk parses with it, so an unpadded date accepted here
            # crashed later behind a misleading error.
            d = date_cls.fromisoformat(str(d)).isoformat()
            if isinstance(a, bool) or not isinstance(a, (int, float)):
                # A TOML number only: "5_000" read as 5000 (S043-05).
                raise TypeError("not a number")
            a = float(a)
            import math as _math
            if not _math.isfinite(a):
                raise ValueError("non-finite amount")
        except (TypeError, ValueError):
            _die(f"[instalments] paid[{i}] needs a YYYY-MM-DD `date` "
                 f"and a numeric `amount`, got {row!r}")
        if a < 0:
            _die(f"[instalments] paid[{i}] amount is negative "
                 f"({a}) — instalments are payments TO CRA; adjust "
                 f"the amount instead of recording a reversal.")
        if year:
            # Year-rollover trap: leaving LAST year's payments in the
            # table after bumping [settings] year made the schedule
            # read "met" with zero interest and fabricated credit
            # interest — silently the maximally wrong answer.
            # The balance's on-time day ends the year: April 30, or the
            # Monday after a weekend April 30 (backlog TJ-0915).
            from taxjson.lib.instalment_rates import balance_payment_deadline
            lo, hi = (f"{year}-01-01",
                      balance_payment_deadline(int(year)).isoformat())
            # A prepayment made before January 1 counts only when the
            # row designates this year (`tax_year = YEAR`): an
            # undesignated prior-year date is the rollover trap above.
            # The interest model credits it from January 1 (A2-0648).
            _ty = row.get("tax_year")
            if _ty is not None and (isinstance(_ty, bool)
                                    or not isinstance(_ty, int)
                                    or _ty != int(year) or d >= lo):
                _die(f"[instalments] paid[{i}] tax_year = {_ty!r}: "
                     f"`tax_year` designates a payment made BEFORE "
                     f"January 1 as a prepayment of this project's "
                     f"{year} instalments — it must be {year}, on a "
                     f"row dated before {lo}.")
            if not (lo <= d <= hi) and _ty is None:
                _die(f"[instalments] paid[{i}] is dated {d}, outside "
                     f"tax year {year} ({lo}..{hi}). Instalments are "
                     f"per year — move it to that year's project or "
                     f"remove it"
                     + (f"; if it is a prepayment of the {year} "
                        f"instalments, add `tax_year = {year}` to the "
                        f"row (it earns credit from January 1)"
                        if d < lo else "") + ".")
        row_out = {"date": d, "amount": a}
        if row.get("note"):
            row_out["note"] = str(row["note"])
        payments.append(row_out)
    out["paid"] = sorted(payments, key=lambda p: p["date"])
    return out


def _net_tax_owing(r: Dict[str, Any], withheld: float) -> float:
    """Instalments are computed on NET TAX OWING — total tax for the
    year (not the incremental investment-income figure) minus amounts
    withheld at source, with any AMT top-up included and any minimum
    tax carryover recovered (s.120.2) taken off."""
    total = float((r.get("tax_with") or {}).get("total") or 0.0)
    total += float((r.get("amt") or {}).get("topup") or 0.0)
    total -= float(((r.get("amt") or {}).get("carryover") or {})
                   .get("recovered") or 0.0)
    return max(0.0, total - withheld)


def _instalments_doc(root: Path, r: Dict[str, Any], year,
                     icfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the instalment picture from an estimate result. None when
    the project declares no [instalments] table."""
    from taxjson.bin import taxjson_instalments as INST
    if not icfg or not year:
        return None
    # Canada-only regime: the US path (1040-ES) differs in dates,
    # safe harbours and penalty basis, and its estimate total omits
    # NIIT — so a US project must not receive a Canadian doc, not
    # even through `sum --estimate --json`.
    if _country(load_settings(root, missing_ok=True)) \
            not in ("canada", "ca"):
        return None
    net = _net_tax_owing(r, float(icfg.get("withheld") or 0.0))
    # None (no rate configured) -> CRA's published quarterly rates.
    rate = (icfg.get("prescribed_rates")
            if icfg.get("prescribed_rates")
            else icfg.get("prescribed_rate"))
    doc = INST.build(
        year=int(year), basis=icfg["basis"], current_net_tax=net,
        payments=icfg.get("paid") or [], annual_rate=rate,
        prior_net_tax=icfg.get("prior_year_net_tax"),
        second_prior_net_tax=icfg.get("second_prior_net_tax"))
    doc["rate_configured"] = bool(icfg.get("prescribed_rates")) or (
        icfg.get("prescribed_rate") is not None)
    return doc


def cmd_instalments(args: argparse.Namespace) -> None:
    """`tjs instalments`: the year's instalment schedule, what each
    date calls for, what has been paid, and the offset interest /
    s.163.1 penalty that follows from the gap. The current-year basis
    is driven by `tjs estimate` itself (AMT included), so the two
    commands can never disagree."""
    import json as _json
    from taxjson.bin import taxjson_instalments as INST
    from taxjson.pipeline.dispatch import run_cmd as _run
    root = Path(args.dir).resolve()
    icfg = _instalment_config(root, load_settings(root, missing_ok=True).year)
    if not icfg:
        _die("no [instalments] section in taxjson.toml",
             "Add one with basis, withheld and paid (docs/settings.md, "
             "[instalments]).")
    settings = load_settings(root, missing_ok=True)
    year = settings.year
    base = str(_base(settings))
    if _country(settings) \
            not in ("canada", "ca"):
        _die("instalments are modeled for canada only",
             "US estimated taxes use a different regime — see "
             "docs/limits.md.")
    _oi, _ol = _estimate_inputs(root, args)
    _ded, _cc = _estimate_deductions(root, args)
    _argv = _self_cmd(root, "estimate", "--json")
    if _oi:
        _argv += ["--other-income", repr(_oi)]
    if _ol:
        _argv += ["--other-losses", repr(_ol)]
    if _ded:
        _argv += ["--deductions", repr(_ded)]
    if _cc:
        _argv += ["--carrying-charges", repr(_cc)]
    res = _run(_argv, capture_output=True)
    if res.returncode != 0:
        # The estimate's exit 2 (a work/ file it cannot read, a usage
        # error) stays 2: 1 is a command's finding (backlog TJ-0641).
        (_die_input if res.returncode == 2 else _die)(
            "could not compute the estimate it builds on",
            _child_error(res.stderr))
    # The estimate's own warnings (an unreadable or stale-year gains
    # file, tainted sales EXCLUDED, a run that failed) are caveats on
    # every figure below: relay them, never swallow them (S005-05,
    # S007-04, S043-17, S047-22).
    if (res.stderr or "").strip():
        _relay_child_stderr(res.stderr)
    r = (_json.loads(res.stdout) or {}).get("estimate") or {}
    if not r:
        _die("the estimate produced no result",
             "Run `tjs run` first, and set [settings] province.")
    doc = _instalments_doc(root, r, year, icfg)
    if doc is None:
        _die("no `year` under [settings] in taxjson.toml",
             "The instalment schedule is per tax year.")
    # A year the built-in tables do not cover (a 2023 project on the
    # 2024 tables and the post-2024 AMT) must say so here too (S043-16).
    from taxjson.lib.tax_estimate import vintage_notes
    doc["vintage"] = r.get("vintage")
    doc["vintage_notes"] = (vintage_notes(year, str(r.get("vintage")))
                            if r.get("vintage") else [])
    # The verdict rests on TOTAL net tax owing. Unset other income and
    # withholding were read as 0 without a word, so an employee was
    # told "no instalments required" on the investment tax alone
    # (S041-00): name what was assumed.
    _est_cfg = estimate_of(load_config(root, missing_ok=True))
    _assumed = []
    # `instalments` has no --other-income: the advice named one, and the
    # parser refused it (backlog TJ-0407). The toml key is the input.
    if (getattr(args, "other_income", None) is None
            and not _est_cfg.has("other_income")):
        _assumed.append("other income (employment, pension, business) "
                        "is not set — assumed 0: set [estimate] "
                        "other_income in taxjson.toml")
    if "withheld" not in icfg:
        _assumed.append("tax withheld at source is not set — assumed 0: "
                        "set [instalments] withheld")
    doc["assumed_zero"] = _assumed
    # Not modelled at all (S043-15, S048-12): CPP/EI payable on
    # self-employment earnings is part of the instalment amount CRA
    # asks for (not of net tax owing or the $3,000 test), and the FX
    # result on foreign cash (line 15300) is outside the estimate.
    doc["not_modelled"] = [
        "CPP/EI payable on self-employment earnings (T1 lines 42100 / "
        "42120) — CRA adds it to the instalments due; add it yourself",
        "FX gains/losses on foreign cash (s.39(1.1), line 15300 — "
        "`tjs fx-cash`) and capital gains on T3/T5 slips are not in "
        "the estimate this schedule is built on"]
    if getattr(args, "json", False):
        _json_out(doc, schema="instalments")
        return
    from taxjson.lib import out as _out
    # The report: a live exact Doc prints each line as the raw print()
    # did, when it is added (the notes' items print through their own:
    # a Doc is never read by a nested function).
    d = _out.Doc(exact=True, width_=0, live=True)
    if not _details(args):
        # Essentials first (docs/output-style.md): the explanations,
        # the assumptions and what is not modelled with --details.
        d.line(INST.render_brief(doc, base))
        if _assumed:
            d.line(_out.act("Assumed 0: " + ", ".join(
                 a.split(" (")[0].split(" is ")[0] for a in _assumed),
                 "tjs instalments --details"))
        if doc["vintage_notes"]:
            d.line(_out.act("Rate tables of another year used",
                            "tjs instalments --details"))
        d.line("Not modelled: self-employed CPP/EI, FX on cash, slip "
               "gains — tjs instalments --details")
        return
    d.line(INST.render(doc, base))

    def _items(texts) -> None:
        di = _out.Doc(exact=True, width_=0, live=True)
        for _t in texts:
            for _ln in _out.wrap(_t, None, "  - ", "    "):
                di.line(_ln)

    if _assumed:
        d.blank()
        d.line("  Assumed zero — the figures above cover the investment "
               "income only")
        _items(_n[:1].upper() + _n[1:] + "." for _n in _assumed)
    d.blank()
    d.line("  Not modelled")
    _items(_n + "." for _n in doc["not_modelled"])
    if doc["vintage_notes"]:
        d.blank()
        d.line("  Rate tables")
        _items(doc["vintage_notes"])


def cmd_amt(args: argparse.Namespace) -> None:
    """`tjs amt [YEAR]`: the year's Canadian minimum tax line by
    line — regular tax, adjusted taxable income, exemption, rate,
    credits, whether it binds, the provincial AMT, the carryover
    created, the carryovers available by year of origin with their
    7-year limit, what is recovered and what carries forward. Every
    figure is the estimate's own (`tjs estimate --json`), so the
    two agree to the cent (tax-logic CA-AMT-01). A closed earlier
    year prints what its close-year lock recorded."""
    import json as _json
    from taxjson.lib import amt_report as AR
    from taxjson.lib import carryforward as CF
    from taxjson.pipeline.dispatch import run_cmd as _run
    root = Path(args.dir).resolve()
    settings = load_settings(root, missing_ok=True)
    if settings.empty:
        _die("no taxjson.toml here", "Run it in a project.")
    py = settings.year
    year = args.year if args.year is not None else py
    if year is None:
        _die("no `year` under [settings] in taxjson.toml",
             "Pass YEAR.")
    if py is None or int(year) != int(py):
        from taxjson.bin import taxjson_filed
        from taxjson.lib import filed_locks
        try:
            lk = taxjson_filed.lock_for_year(root, settings, int(year))
        except filed_locks.PriorRecordError as e:
            _die(str(e))
        if lk is None:
            _die(f"this project's books are for {py}; {year} has no "
                 f"close-year lock here",
                 f"Neither filed/{year}.json nor [settings] "
                 f"prior_year_record. Run `tjs amt` in the {year} "
                 f"project.")
        try:
            # The one lock check, under this project's country: a US
            # lock is refused by name, not "records no minimum tax"
            # (2026-10-25 audit year-end#1).
            block = CF.lock_block(lk[0], _country(settings))
        except CF.CarryInputError as e:
            _die(str(e))
        mt = (block or {}).get("minimum_tax")
        label = filed_locks.lock_label(root, lk[0])
        if not isinstance(mt, dict):
            _die(f"{label} records no minimum tax",
                 f"It was closed before taxjson recorded carry-forwards, "
                 f"or with no province set. Run `tjs amt` in the "
                 f"{year} project, or re-close {year} there.")
        if getattr(args, "json", False):
            _json_out({"year": int(year), "recorded": True,
                       "lock": label, "minimum_tax": mt, "law": AR.LAW},
                      schema="amt YEAR")
            return
        d = _out.Doc(exact=True, width_=0, live=True)
        for ln in AR.render_recorded(int(year), label, mt):
            d.line(ln)
        return
    argv = _self_cmd(root, "estimate", "--json")
    for flag, attr in (("--other-income", "other_income"),
                       ("--other-losses", "other_losses"),
                       ("--deductions", "deductions"),
                       ("--carrying-charges", "carrying_charges"),
                       ("--province", "province")):
        v = getattr(args, attr, None)
        if v is not None:
            argv += [flag, str(v) if isinstance(v, str) else repr(v)]
    res = _run(argv, capture_output=True)
    if res.returncode != 0:
        # The estimate's exit 2 (a work/ file it cannot read, a usage
        # error) stays 2: 1 is a command's finding (backlog TJ-0641).
        (_die_input if res.returncode == 2 else _die)(
            "could not compute the estimate it builds on",
            _child_error(res.stderr))
    if (res.stderr or "").strip():
        _relay_child_stderr(res.stderr)
    r = (_json.loads(res.stdout) or {}).get("estimate") or {}
    if not r or not r.get("amt"):
        _die("the estimate produced no minimum tax result",
             "Run `tjs run` first, and set [settings] province.")
    doc = AR.build(r, int(year), str(_base(settings)))
    if getattr(args, "json", False):
        _json_out(doc, schema="amt")
        return
    # The report: live exact Docs print each line as the raw print()
    # did, when it is added.
    d = _out.Doc(exact=True, width_=0, live=True)
    for ln in AR.render(doc, details=_details(args)):
        d.line(ln)


def _withholding_rows(cache: Path, taxable_accounts, year,
                      foreign_by_account=None,
                      noncash: Optional[Dict[str, List[Tuple]]] = None
                      ) -> Dict[str, Optional[List[Tuple]]]:
    """{account: [(date, net TAX amount, symbol)] of the tax year in its
    BASE book, or None when the book holds no TAX row in the year} — the
    withholding `_withholding_total` credits, each row with the security
    it was withheld on ("" when the row names none; backlog TJ-0638).
    The base books, not the
    gains files: the gains engine does not carry TAX rows through, so
    reading its output silently found nothing and the estimator always
    fell back to the flat 15% assumption. `noncash`, when given, is
    filled with {account: [(date, amount, symbol)]}: the year's taxable
    spin-off
    distributions (a deemed dividend paid in shares: no cash, so nothing
    withheld and no withholding to assume; backlog TJ-0422)."""
    from taxjson.lib.corp_actions import FMV_SPINOFF_ELECTIONS
    from taxjson.characterize.income_dating import is_canadian_issuer
    from taxjson.lib.tax_estimate import CA_FOREIGN_WITHHOLDING
    ystr = str(year or "")
    foreign_by_account = foreign_by_account or {}
    out: Dict[str, Optional[List[Tuple]]] = {}
    # Sorted: callers pass a SET, whose order made the unrounded FTC
    # differ in its last digit from run to run (S043-20).
    for acct in sorted(taxable_accounts):
        p = Path(cache) / f"{acct}_base.json"
        try:
            data = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # Skipping the account dropped its withholding AND its 15%
            # fallback while its foreign dividends were still taxed
            # (R1-223): fall back for it, and say so.
            if foreign_by_account.get(acct):
                emit_line(f"tjs: warning: could not read {p.name} "
                          f"({e}) — assuming {CA_FOREIGN_WITHHOLDING:.0%} "
                          f"withholding on {acct}'s foreign dividends; "
                          f"re-run `tjs run`.", file=sys.stderr)
            data = {}
        rows: List[Tuple] = []
        acct_seen = False
        for t in data.get("transactions", []):
            d = str(t.get("date") or "")
            if ystr and not d.startswith(ystr):
                continue
            sym = str(t.get("symbol") or "")
            # A foreign issuer's only, by the test the dividend split
            # uses (A2-0319): a Canadian one is not in the foreign
            # dividends the assumption applies to.
            if (noncash is not None and t.get("action") == "DIVIDEND"
                    and t.get("corp_election") in FMV_SPINOFF_ELECTIONS
                    and not is_canadian_issuer(t)):
                noncash.setdefault(acct, []).append(
                    (d, float(t.get("net_amount") or 0.0), sym))
            if t.get("action") != "TAX":
                continue
            acct_seen = True
            rows.append((d, float(t.get("net_amount") or 0.0), sym))
        out[acct] = rows if acct_seen else None
    return out


def _withholding_split(rows: Dict[str, Optional[List[Tuple]]],
                       foreign_by_account=None,
                       through: Optional[str] = None,
                       noncash: Optional[Dict[str, List[Tuple]]] = None
                       ) -> Tuple[Optional[float], float, float]:
    """(total, assumed, noncash) over `_withholding_rows`: `total` is
    the net TAX withheld (base currency; positive = withheld, refunds
    net) plus the 15% assumed for each account with no TAX row in the
    year, None when no account's book holds one (no data is not the
    same as no tax); `assumed` is the assumed part; `noncash` the
    spin-off deemed dividends of those accounts, which take no
    assumption (`_withholding_rows`' `noncash`; backlog TJ-0422). With
    `through` (ISO), the rows dated up to it only (`plot-tax`'s month:
    the credit a month's run takes is the withholding recorded by its
    end; backlog TJ-0299)."""
    # Only some parsers emit TAX rows (IB and RBC do; Questrade does
    # not), so an account with foreign dividends and no TAX rows must
    # fall back to the treaty ASSUMPTION for its own share — treating
    # one broker's withholding as the credit for the whole book
    # systematically under-credited. An account with TAX rows in the
    # year is credited what it recorded, never the assumption, before
    # its first one too.
    from taxjson.lib.tax_estimate import CA_FOREIGN_WITHHOLDING
    import math as _math
    foreign_by_account = foreign_by_account or {}
    noncash = noncash or {}
    seen = False
    # Summed exactly (S043-20).
    parts: List[float] = []
    assumed: List[float] = []
    shares: List[float] = []
    for acct in sorted(rows):
        acct_rows = rows[acct]
        if acct_rows is None:
            nc = _math.fsum(r[1] for r in noncash.get(acct) or ()
                            if through is None or r[0][:10] <= through)
            fd = float(foreign_by_account.get(acct) or 0.0)
            nc = min(max(0.0, nc), max(0.0, fd))
            shares.append(nc)
            assumed.append(CA_FOREIGN_WITHHOLDING * (fd - nc))
            continue
        seen = True
        parts.extend(r[1] for r in acct_rows
                     if through is None or r[0][:10] <= through)
    total = _math.fsum(parts + assumed)
    return (max(0.0, total) if seen else None, _math.fsum(assumed),
            _math.fsum(shares))


def _withholding_by_security(
        rows: Dict[str, Optional[List[Tuple]]],
        foreign_by_security: Dict[str, Dict[str, float]],
        through: Optional[str] = None,
        noncash: Optional[Dict[str, List[Tuple]]] = None
        ) -> List[Dict[str, Any]]:
    """`_withholding_split`'s withholding paired with each security's
    foreign dividends, the entries of tax_estimate.ca_withholding_limit,
    which counts each at most 15% of its security's own (ITA s.20(11),
    s.126(7); backlog TJ-0638; tax-logic CA-EST-FTC). A security's
    dividends (`foreign_by_security`: {account: {symbol: amount}}, a
    spin-off's deemed dividend in them) and TAX rows are summed across
    the taxable accounts; an account with no TAX row in the year adds
    the 15% assumed on each security's cash dividends (none on a
    deemed dividend). A TAX row that names no security, or one with no
    foreign dividend in the books, is its account's unpaired
    withholding: one entry per account, whose `dividends` are the
    account's foreign cash dividends of the securities none of its own
    TAX rows names, each up to the part of its security's dividends
    whose 15% the paired, the assumed and an earlier account's unpaired
    withholding leave (backlog TJ-0715). `through`: as
    `_withholding_split`'s."""
    from taxjson.lib.tax_estimate import CA_FOREIGN_WITHHOLDING
    import math as _math
    noncash = noncash or {}

    def _upto(rs):
        return [r for r in rs or ()
                if through is None or str(r[0])[:10] <= through]

    def _sym(r) -> str:
        return str(r[2]) if len(r) > 2 and r[2] else ""

    divs: Dict[str, List[float]] = {}
    for acct in sorted(foreign_by_security):
        for sym, v in foreign_by_security[acct].items():
            divs.setdefault(sym, []).append(float(v or 0.0))
    income = {s: _math.fsum(v) for s, v in divs.items()}

    def _cash(acct: str) -> Dict[str, float]:
        """{symbol: cash dividends} of an account: its foreign dividends
        less its spin-off deemed dividends; a deemed dividend booked
        under a symbol its dividends do not carry comes off the
        account's cash dividends pro rata (what `_withholding_split`
        takes off the account's)."""
        by = {s: max(0.0, float(v or 0.0))
              for s, v in (foreign_by_security.get(acct) or {}).items()}
        nc_by: Dict[str, List[float]] = {}
        for r in _upto(noncash.get(acct)):
            nc_by.setdefault(_sym(r), []).append(float(r[1]))
        cash: Dict[str, float] = {}
        left = 0.0
        for s, v in nc_by.items():
            n = max(0.0, _math.fsum(v))
            if s in by:
                cash[s] = max(0.0, by[s] - n)
                left += max(0.0, n - by[s])
            else:
                left += n
        for s, v in by.items():
            cash.setdefault(s, v)
        total = _math.fsum(cash.values())
        if left > 0 and total > 0:
            k = max(0.0, total - left) / total
            cash = {s: v * k for s, v in cash.items()}
        return cash

    withheld: Dict[str, List[float]] = {}
    assumed: Dict[str, List[float]] = {}
    unpaired: Dict[str, List[float]] = {}
    # The securities each account's own TAX rows name.
    named: Dict[str, set] = {}
    for acct in sorted(rows):
        acct_rows = rows[acct]
        if acct_rows is None:
            for s, c in _cash(acct).items():
                assumed.setdefault(s, []).append(CA_FOREIGN_WITHHOLDING * c)
            continue
        for r in _upto(acct_rows):
            s = _sym(r)
            if s and income.get(s, 0.0) > 0.005:
                withheld.setdefault(s, []).append(float(r[1]))
                named.setdefault(acct, set()).add(s)
            else:
                unpaired.setdefault(acct, []).append(float(r[1]))
    out: List[Dict[str, Any]] = [
        {"security": s, "dividends": income.get(s, 0.0),
         "withheld": _math.fsum(withheld.get(s) or ()),
         "assumed": _math.fsum(assumed.get(s) or ())}
        for s in sorted(set(income) | set(withheld) | set(assumed))]
    # Each security's dividends whose 15% room its paired (and assumed)
    # withholding leaves: an account's unpaired withholding may use it
    # on the account's own cash dividends of the securities none of ITS
    # TAX rows names. Excluding every security another account's TAX
    # rows named zeroed that room (backlog TJ-0715); the security's
    # 15% ceiling still holds across the accounts.
    left = {s: max(0.0, income.get(s, 0.0)
                   - (_math.fsum(withheld.get(s) or ())
                      + _math.fsum(assumed.get(s) or ()))
                   / CA_FOREIGN_WITHHOLDING)
            for s in income}
    for acct in sorted(unpaired):
        own = named.get(acct) or set()
        by = {s: min(c, left.get(s, 0.0))
              for s, c in _cash(acct).items() if s not in own}
        room = _math.fsum(by.values())
        w = _math.fsum(unpaired[acct])
        out.append({"security": "", "account": acct, "dividends": room,
                    "withheld": w, "assumed": 0.0})
        # The room this account's withholding takes, off each security
        # pro rata, so another account's cannot take it again.
        cap = CA_FOREIGN_WITHHOLDING * room
        k = min(1.0, max(0.0, w) / cap) if cap > 0 else 0.0
        for s, v in by.items():
            left[s] = max(0.0, left[s] - v * k)
    return out


def _withholding_total(rows: Dict[str, Optional[List[Tuple]]],
                       foreign_by_account=None,
                       through: Optional[str] = None,
                       noncash: Optional[Dict[str, List[Tuple]]] = None
                       ) -> Optional[float]:
    """The `total` of `_withholding_split`: the foreign tax withheld
    the estimate credits, None when no account has a TAX row."""
    return _withholding_split(rows, foreign_by_account, through,
                              noncash)[0]


def _withholding_kwargs(split: Tuple[Optional[float], float, float]
                        ) -> Dict[str, Any]:
    """`estimate_canada`'s withholding arguments from a
    `_withholding_split`: the total, its assumed part and the deemed
    dividends no withholding is assumed on (backlog TJ-0422)."""
    total, assumed, noncash = split
    return {"actual_withheld": total, "withheld_assumed": assumed,
            "foreign_div_noncash": noncash}


def _actual_withholding(cache: Path, taxable_accounts, year,
                        foreign_by_account=None) -> Optional[float]:
    """Net TAX withheld (base currency) across the taxable accounts'
    BASE books for the tax year (`_withholding_rows`,
    `_withholding_total`)."""
    return _withholding_of_year(cache, taxable_accounts, year,
                                foreign_by_account)["actual_withheld"]


def _withholding_of_year(cache: Path, taxable_accounts, year,
                         foreign_by_account=None,
                         foreign_by_security: Optional[
                             Dict[str, Dict[str, float]]] = None
                         ) -> Dict[str, Any]:
    """`_withholding_kwargs` for the tax year: the taxable accounts'
    BASE books' TAX rows, the 15% assumption for an account with none
    and its spin-off deemed dividends, which take none. With
    `foreign_by_security` ({account: {symbol: foreign dividends}}), the
    same withholding paired with each security's dividends as well
    (`withholding_by_security`: the 15% limit is each security's,
    backlog TJ-0638)."""
    nc: Dict[str, List[Tuple]] = {}
    rows = _withholding_rows(cache, taxable_accounts, year,
                             foreign_by_account, nc)
    kw = _withholding_kwargs(
        _withholding_split(rows, foreign_by_account, None, nc))
    if foreign_by_security is not None:
        kw["withholding_by_security"] = _withholding_by_security(
            rows, foreign_by_security, None, nc)
    return kw


def _tax_estimate_result(cfg: Dict[str, Any], est: Dict[str, float], *,
                         other_income: float, other_losses: float,
                         province: Optional[str],
                         actual_withheld: Optional[float] = None,
                         deductions: float = 0.0,
                         carrying_charges: float = 0.0,
                         lt_losses: float = 0.0,
                         amt_carryover: Optional[Dict[int, float]] = None,
                         carry_sources: Optional[Dict[str, Any]] = None,
                         federal_only: bool = False,
                         withheld_assumed: float = 0.0,
                         foreign_div_noncash: float = 0.0,
                         withholding_by_security=None
                         ) -> Dict[str, Any]:
    """Resolve country/province and run the estimator — shared by the
    text block and `sum --json` so the two can never disagree. For usa,
    un-termed gains are folded into ST (conservative) with a stderr
    note, and the ST input (post-fold, pre-loss) rides along as
    `st_input` for display. The withholding arguments are
    `_withholding_kwargs`' (Canada)."""
    from taxjson.lib.tax_estimate import estimate_canada, estimate_usa
    settings = settings_of(cfg)
    _est_year = settings.year
    country = _country(settings)
    if country == "canada":
        prov = (province or str(settings.province or "")).strip()
        if federal_only:
            # close-year with no supported province: the federal
            # carry-forwards, no province standing in (CA-CARRY-01).
            prov = None
        elif not prov:
            _die("the canada estimate needs a province",
                 "Pass --province ON|BC|AB or set `province` under "
                 "[settings] in taxjson.toml.")
        _cs = carry_sources or {}
        try:
            r = estimate_canada(realized=est["realized"],
                                   year=_est_year,
                                   eligible_div=est["div_ca"],
                                   foreign_div=est["div_foreign"],
                                   pil=est["pil"],
                                   other_income=other_income,
                                   other_losses=other_losses,
                                   province=prov,
                                   actual_withheld=actual_withheld,
                                   withheld_assumed=withheld_assumed,
                                   foreign_div_noncash=foreign_div_noncash,
                                   withholding_by_security=(
                                       withholding_by_security),
                                   staking=est.get("staking", 0.0),
                                   deductions=deductions,
                                   carrying_charges=carrying_charges,
                                   capital_gains_dividends=est.get(
                                       "cg_div", 0.0),
                                   amt_carryover=amt_carryover,
                                   amt_carryover_source=_cs.get(
                                       "amt_carryover"))
        except ValueError as e:
            _die(str(e))
        r["carry_sources"] = _carry_sources_doc(_cs)
        r["notes"] = list(_cs.get("notes") or []) + list(r.get("notes")
                                                         or [])
        return r
    if deductions or carrying_charges:
        _die("--deductions/--carrying-charges are modelled for the "
             "canada estimate only",
             "The US estimate is experimental and uses the standard "
             "deduction.")
    unterm = est["realized"] - est["st"] - est["lt"]
    st_in = est["st"]
    if abs(unterm) > 0.01:
        st_in += unterm
        from taxjson.lib.out import note as _onote
        _onote(f"{fmt_money(unterm)} of gains carry no ST/LT term — "
               f"treated as SHORT-TERM (conservative)",
               prog=f"tjs {_current_cmd() or 'sum'}",
               details=["Re-run `tjs run` to refresh."])
    # The year's own [carryover] claimed entry: the deduction its filed
    # return's taxable income absorbed, which the carryover after the
    # year (and close-year's lock) counts as used (backlog TJ-0464).
    from taxjson.lib.project_tables import claimed_losses
    try:
        _claimed = claimed_losses(cfg)[0].get(int(_est_year))
    except (TypeError, ValueError):
        _claimed = None
    # §1256 P/L leaves the term the engine gave it (a gain with no term
    # is in st_in) and comes back 40% short-term / 60% long-term, as
    # Form 6781 puts it on Schedule D (US-FUT-03; backlog TJ-0466).
    s1256 = float(est.get("s1256") or 0.0)
    s1256_lt = float(est.get("s1256_lt") or 0.0)
    # Dividend compensation paid on a short is no reduction of ordinary
    # income: deductible only as investment interest on Schedule A
    # (itemized), and the estimate takes the standard deduction; one on
    # a short covered within 45 days is already in the cover's cost
    # (§263(h); US-INC-04, backlog TJ-0728).
    _pil_paid = float(est.get("pil_paid") or 0.0)
    r = estimate_usa(st=st_in - (s1256 - s1256_lt),
                     lt=est["lt"] - s1256_lt, year=_est_year,
                     qualified_div=est["div_ca"] + est["div_foreign"],
                     pil=est["pil"] + _pil_paid + est.get("staking", 0.0),
                     other_income=other_income,
                     other_losses=other_losses,
                     lt_losses=lt_losses,
                     deduction_claimed=_claimed,
                     section_1256=s1256)
    if _claimed is not None:
        r["deduction_claimed"] = M.shown(float(_claimed), 2)
    r["carryover_short_term"] = M.shown(other_losses, 2)
    r["carryover_long_term"] = M.shown(lt_losses, 2)
    r["carry_sources"] = _carry_sources_doc(carry_sources or {})
    r["notes"] = (list((carry_sources or {}).get("notes") or [])
                  + list(r.get("notes") or []))
    if abs(s1256) > 0.005:
        r.setdefault("notes", []).append(
            f"{fmt_money(s1256)} of §1256 P/L (futures, futures options, "
            f"broad-based index options such as SPX) is split 60/40 as "
            f"Form 6781 does, whatever the holding period: "
            f"{fmt_money(r['section_1256_short_term'])} short-term (40%) "
            f"and {fmt_money(r['section_1256_long_term'])} long-term "
            f"(60%). Not modelled: contracts still open at year end are "
            f"not marked to market, and a net §1256 loss is not carried "
            f"back (§1212(c)): both by hand on Form 6781.")
    if _pil_paid > 0.005:
        r["pil_paid_not_deducted"] = M.shown(_pil_paid, 2)
        r.setdefault("notes", []).append(
            f"{fmt_money(_pil_paid)} of dividend compensation paid on "
            f"short sales is not subtracted from ordinary income: it is "
            f"deductible only as investment interest on Schedule A (Form "
            f"4952) when you itemize, and this estimate takes the "
            f"standard deduction (US-INC-04).")
    return r


def _carry_sources_doc(cs: Dict[str, Any]) -> Dict[str, Any]:
    """Where the estimate's carry-forward inputs came from (JSON)."""
    return {k: cs.get(k) for k in ("other_losses", "long_term_losses",
                                   "amt_carryover", "partial_lock")
            if k in cs}


def _cmd_fx_cash_v2(root: Path, cache: Path, args) -> None:
    from taxjson.lib import fx_cash_v2 as V2
    _refuse_cash_older_than_inputs(root, load_config(root, missing_ok=True))
    doc, verdict, cash, base, year, country = _fx_cash_v2_doc(root, cache)
    evs = None
    if getattr(args, "cash_events", False):
        evs = sorted(list(cash["events"]) + list(cash["lines"]),
                     key=lambda e: (e.get("date", ""), e.get("book") or ""))
    if getattr(args, "json", False):
        out = dict(doc)
        out["headline"] = V2.headline(doc, verdict)
        if verdict is not None:
            out.update(reportable=verdict["reportable"],
                       rule=verdict["rule"],
                       review_key=f"v2|{year}|{M.units(verdict['reportable'], 2):.2f}")
        if not getattr(args, "events", False):
            out["events"] = None
        if evs is not None:
            out["cash_events"] = [{k: v for k, v in e.items()
                                   if not k.startswith("_")} for e in evs]
        _json_out(out, schema="fx-cash --ledger v2")
        return
    # The report: live exact Docs print each line as the raw print()
    # did, when it is added.
    d = _out.Doc(exact=True, width_=0, live=True)
    d.line(V2.render(doc, verdict, country, cash_events=evs))
    if getattr(args, "events", False) and doc["events"]:
        from taxjson.lib import out as _o
        d.blank()
        d.line("EVENTS")
        d.line("\n".join(_o.fit_table(
             ["DATE", "BOOK", "CUR", "UNITS", "RATE", "GAIN", "WHAT"],
             [[e["date"], e["book"], e["currency"], f"{M.shown(e['units'], 2):,.2f}",
               f"{e['rate']:g}" if e["rate"] is not None else "-",
               f"{M.shown(e['gain'], 2):+,.2f}", e["what"] or "-"]
              for e in doc["events"]],
             aligns=["<", "<", "<", ">", ">", ">", "<"], key=0)))


def cmd_fx_cash(args: argparse.Namespace) -> None:
    """`tjs fx-cash`: FX gains on foreign-currency cash (ITA
    s.39(1.1) with the $200 de minimis; the §988 ordinary-income figure
    for US projects). A standalone REPORT — nothing here changes the
    engine's gains, sum, or the filing exports. The default ledger (v1)
    is NOT RELIABLE and says so first; `--ledger v2` (or [settings]
    fx_cash_ledger = "v2") runs the opt-in ledger that reads the cash
    events and refuses instead of guessing (lib/fx_cash_v2)."""
    from taxjson.bin import taxjson_fx_cash as FX
    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_settings(root, missing_ok=True)
    which = _fx_cash_ledger_choice(settings, getattr(args, "ledger", None))
    if which == "v2":
        _cmd_fx_cash_v2(root, cache, args)
        return
    ledger, verdict, base, year, country = _fx_cash_doc(root, cache)
    st = FX.unreliable_status(ledger, year, verdict)
    if getattr(args, "json", False):
        _json_out({"ledger": "v1", "status": st["status"],
                   "reliable": False, "active": st["active"],
                   "headline": st["headline"],
                   "reasons": st["reasons"],
                   "overdrafts_in_year": st["overdrafts_in_year"],
                   "unreliable_raw": st["unreliable_raw"],
                   "per_currency": ledger["per_currency"],
                   "events": (ledger["events"]
                              if getattr(args, "events", False)
                              else None),
                   "rule": verdict["rule"],
                   "overdrafts": ledger["overdrafts"],
                   "unrated": ledger["unrated"],
                   "open_pools": ledger["pools"],
                   "pools_year_end": ledger.get("pools_year_end") or {},
                   "currency": base, "year": year}, schema="fx-cash")
        return
    # The report: live exact Docs print each line as the raw print()
    # did, when it is added.
    d = _out.Doc(exact=True, width_=0, live=True)
    d.line((FX.render_report if _details(args) else FX.render_brief)(
         ledger, base, year, country, verdict))
    if getattr(args, "events", False) and ledger["events"]:
        d.blank()
        d.line("EVENTS")
        d.line("\n".join(FX.render_events(ledger["events"])))


def _own_filed_lock(root: Optional[Path], cfg: Dict[str, Any], year: Any
                    ) -> Optional[Dict[str, Any]]:
    """The net capital loss block of the year's own lock, when that lock records
    a return filed with another tool, and None for no such lock, a damaged one
    or a partial-year snapshot (backlog TJ-1489). The estimate is the books';
    this is what it says beside them."""
    from taxjson.bin import taxjson_filed
    from taxjson.lib import filed_locks
    if root is None or not year:
        return None
    path = taxjson_filed.snapshot_path(root, year)
    if not path.exists():
        return None
    try:
        doc = filed_locks.read_lock(path, settings_of(cfg),
                                      prior=True, carried=True)
    except filed_locks.LockProblem:
        return None
    ncl = (doc.get("carryforwards") or {}).get("net_capital_loss")
    if (not isinstance(doc.get("filed_totals"), dict) or not isinstance(ncl, dict)
            or filed_locks.partial_year_note(doc, doc.get("year"))):
        return None
    return ncl


def _register_plot_tax(sub: argparse._SubParsersAction) -> None:
    p_pt = sub.add_parser(
        "plot-tax",
        help="Chart of the tax each month's income generated",
        description="An ASCII chart of the tax the year's investment "
             "income generated month by month, against the year's "
             "estimate: the estimator `estimate` uses runs once per "
             "month on the taxable accounts' gains and income dated up "
             "to the month's end, and a month's tax is that run less "
             "the month before's (a loss month is negative). The table "
             "under the chart carries the running total. Planning "
             "numbers, never filing numbers.")
    p_pt.add_argument("--other-income", type=float, default=None,
                      metavar="AMT",
                      help="Employment/other income the investment "
                           "income stacks on top of (default: "
                           "[estimate] other_income, else 0)")
    p_pt.add_argument("--other-losses", type=float, default=None,
                      metavar="AMT", help=_OTHER_LOSSES_HELP
                      + " (default: [estimate] other_losses, else the "
                        "carry-forward of the latest close-year lock "
                        "before the year, else 0)")
    _add_deduction_flags(p_pt)
    p_pt.add_argument("--province", default=None,
                      help="Canada: ON|BC|AB (default: `province` "
                           "under [settings])")
    p_pt.add_argument("--json", action="store_true",
                      help="Emit the document as JSON")
    p_pt.set_defaults(func=cmd_plot_tax)


def _register_amt(sub: argparse._SubParsersAction) -> None:
    p_amt = sub.add_parser(
        "amt",
        help="The year's minimum tax (AMT), line by line",
        description="Canada: the year's minimum tax (AMT) line by line — "
             "adjusted taxable income, exemption, rate, credits, whether "
             "it binds, the provincial AMT, and the carryover (ITA "
             "s.120.2): available by year of origin with its 7-year "
             "limit, recovered this year, carried forward. The "
             "estimate's own figures; YEAR = a closed year prints what "
             "its lock recorded.")
    p_amt.add_argument("year", nargs="?", type=_tax_year_arg, default=None,
                       help="Tax year (default: [settings] year)")
    p_amt.add_argument("--other-income", type=float, default=None,
                       metavar="AMT",
                       help="Employment/other income (default: [estimate] "
                            "other_income, else 0)")
    p_amt.add_argument("--other-losses", type=float, default=None,
                       metavar="AMT", help=_OTHER_LOSSES_HELP)
    _add_deduction_flags(p_amt)
    p_amt.add_argument("--province", default=None,
                       help="ON|BC|AB (default: `province` under "
                            "[settings])")
    p_amt.add_argument("--json", action="store_true",
                       help="Emit the breakdown as JSON")
    p_amt.set_defaults(func=cmd_amt)


def _register_instalments(sub: argparse._SubParsersAction) -> None:
    p_inst = sub.add_parser(
        "instalments",
        help="Instalments due, paid, and interest owed",
        description="Canadian tax instalments: what each of the four "
             "dates (Mar/Jun/Sep/Dec 15) calls for under your chosen "
             "basis, what you have paid, and the offset interest plus "
             "s.163.1 penalty that follow from any gap. The "
             "current-year basis is driven by `tjs estimate` "
             "itself (AMT included). Configure [instalments] in "
             "taxjson.toml.")
    p_inst.add_argument("--json", action="store_true",
                        help="Emit the schedule and interest as JSON")
    p_inst.set_defaults(func=cmd_instalments)


def _register_div_estimate(sub: argparse._SubParsersAction) -> None:
    p_divest = sub.add_parser(
        "div-estimate",
        help="Upcoming dividends from each holding's own history",
        description="Estimate upcoming dividends from each held "
             "position's own dividend history in the books: its "
             "payment frequency (monthly, quarterly, semi-annual or "
             "annual; irregular payers flagged; quarterly, shown as "
             "assumed, for a payer with too few payments to tell), "
             "latest and 12-month "
             "amount per share, the shares held now and the next pay "
             "date and amount; totals for the rest of the tax year and "
             "the next N months, taxable and sheltered, in the base "
             "currency at the books' latest rate, with withholding "
             "where the books show a rate. Payments are dated as in "
             "divs-sum. Planning numbers only.")
    p_divest.add_argument("period", nargs="?",
                          help="Forward window: Nd, Nw, Nm or Ny "
                               "(default 12m)")
    p_divest.add_argument("account", nargs="?",
                          help="Account (default: all)")
    p_divest.add_argument("--months", type=int, default=None, metavar="N",
                          help="Forward window in months (default 12)")
    p_divest.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                          help="As of this date (default: today)")
    p_divest.add_argument("--json", action="store_true",
                          help="Emit JSON instead of text")
    p_divest.set_defaults(func=cmd_div_estimate)


def _register_option_yield(sub: argparse._SubParsersAction) -> None:
    p_oy = sub.add_parser(
        "option-yield",
        help="Option premiums, P/L and yield per underlying",
        description="How the option trades on an underlying did over a "
             "window: premiums received and paid, the profit or loss of "
             "the contracts closed in it and how they closed (expired, "
             "bought back, assigned, sold, exercised), what is still "
             "open, and the yield on the cost of the underlying's shares "
             "you hold (on the strike notional where you hold none), "
             "annualized. Contracts "
             "are matched per account and series from the books. "
             "Planning numbers only: the tax treatment of each premium "
             "is in the gains.")
    p_oy.add_argument("items", nargs="*", metavar="UNDERLYING|PERIOD|ACCOUNT",
                      help="Underlyings (QQQ, QQQ.US; default: all), a "
                           "window (" + _PERIOD_HELP + ") and an account "
                           "(default: all), in any order")
    p_oy.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_oy.set_defaults(func=cmd_option_yield)


def _register_stats(sub: argparse._SubParsersAction) -> None:
    p_stats = sub.add_parser(
        "stats",
        help="Win/loss statistics on closed trades",
        description="Win/lose statistics on closed trades per asset "
             "class (long and short shares, long and written options, "
             "futures, crypto): trades, wins, losses, win rate, net "
             "P/L, average and largest win and loss, profit factor — "
             "economic P/L in the base currency before any "
             "superficial-loss / wash-sale denial (default: the tax "
             "year, taxable accounts).")
    p_stats.add_argument("period", nargs="?", metavar="YEAR",
                         help="Tax year (default: the project's year); "
                              "any window token works too (30d, ytd, "
                              "all ...)")
    p_stats.add_argument("account", nargs="?",
                         help="One account (default: every taxable "
                              "account); a sheltered account is shown "
                              "only when named")
    p_stats.add_argument("--all-history", action="store_true",
                         help="Every closed trade the books hold, not "
                              "one year")
    p_stats.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_stats.set_defaults(func=cmd_stats)


def _register_win_rates(sub: argparse._SubParsersAction) -> None:
    p_wr = sub.add_parser(
        "win-rates",
        help="Win rate of your closed trades per ticker",
        description="The win rate of every closed trade in the window "
             "(shares, options, futures, crypto), grouped per share "
             "listing — an option counts under its underlying — or, "
             "with --by, per account, month of the close or asset "
             "class: trades, wins, losses, win rate, net P/L, average "
             "win and loss, profit factor. Economic P/L in the base "
             "currency before any superficial-loss / wash-sale denial, "
             "the trades `stats` counts (default: the tax year, taxable "
             "accounts). A view, never a filing number.")
    p_wr.add_argument("items", nargs="*", metavar="SYMBOL|PERIOD|ACCOUNT",
                      help="Symbols (QQQ, QQQ.US; default: all), a "
                           "window (30d, 6m, 1y, mtd, ytd, all, a tax "
                           "year; default: the tax year) and an account "
                           "(default: every taxable account; a sheltered "
                           "account is shown only when named), in any "
                           "order")
    p_wr.add_argument("--by", default="symbol",
                      choices=("symbol", "account", "month", "class"),
                      help="Group the trades per symbol (default), "
                           "account, month of the close or asset class")
    p_wr.add_argument("--all-history", action="store_true",
                      help="Every closed trade the books hold, not one "
                           "year")
    p_wr.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_wr.set_defaults(func=cmd_win_rates)


def _register_fx_cash(sub: argparse._SubParsersAction) -> None:
    p_fxc = sub.add_parser(
        "fx-cash",
        help="Currency gains on foreign cash (separate report)",
        description="FX capital gains on foreign-currency CASH (ITA "
             "s.39(1.1) with the $200 de minimis; the §988 "
             "ordinary-income figure for US projects) — a standalone "
             "report; it changes NO other number. The default ledger "
             "(v1) reads only trades and income and is NOT RELIABLE: "
             "never file its figure. --ledger v2 (opt-in, under audit) "
             "also reads conversions, deposits/withdrawals and statement "
             "balances and refuses instead of guessing. Set "
             "fx_cash_gains = true under [settings] to also print it at "
             "the end of every run.")
    p_fxc.add_argument("--ledger", choices=FX_CASH_LEDGERS,
                       help="v1 (the default: NOT RELIABLE, never a "
                            "filing figure) or v2 (opt-in, under audit: "
                            "reads conversions, deposits/withdrawals and "
                            "statement balances, models margin debt, "
                            "refuses instead of guessing). Overrides "
                            "[settings] fx_cash_ledger.")
    p_fxc.add_argument("--cash-events", action="store_true",
                       help="v2: list every cash event read (conversions, "
                            "moves, balances) after the report")
    p_fxc.add_argument("--events", action="store_true",
                       help="List each in-year disposal event (date, "
                            "units, rate, gain)")
    p_fxc.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_fxc.set_defaults(func=cmd_fx_cash)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_plot_tax(sub)
    _register_amt(sub)
    _register_instalments(sub)
    _register_div_estimate(sub)
    _register_option_yield(sub)
    _register_stats(sub)
    _register_win_rates(sub)
    _register_fx_cash(sub)
