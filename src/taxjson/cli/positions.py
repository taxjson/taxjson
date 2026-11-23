"""The Positions commands: list, shares (and the inventory sum
`tjs technicals` reads)."""
import collections.abc as _cabc
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from taxjson.project.accounts import typed_account
from taxjson.lib import project_layout as _PL
from taxjson.lib import out as _out
from taxjson.lib.report_fmt import fmt_money
from taxjson.model.result import Holding
from taxjson.model import money as M
from taxjson.lib.stage_msg import emit_line

from taxjson.cli.common import (
    _base_currency, _ISO_DATE_RE, _load_json_or_die, _out_act_prefix,
    _vprint, _warn_accounts_without_books,
)
from taxjson.pipeline.config import _country, load_config, load_settings
from taxjson.pipeline.freshness import timeline_beside
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.run import _project_base_files
from taxjson.pipeline.sanity import _json_out
from taxjson.pipeline.stages import (
    _read_work_doc, _tax_date_basis,
)


def held_by_symbol(files: Dict[str, Path], *, options: bool,
                   futures: bool = False
                   ) -> Tuple[Dict[str, Dict[str, Any]], Any]:
    """The books' end-of-data inventory (each account's gains file in
    `files`, after ticker.map), summed per symbol: ({symbol: {"qty", "cost",
    "accounts": {account: qty}, "unknown_cost"}}, the books' year). Option
    contracts only with `options`; futures contracts only with `futures`
    (futures options follow `options`). A symbol whose total nets to zero
    is still in it: the caller drops it (`tjs shares`, `tjs technicals`)."""
    from taxjson.lib.option_symbols import is_option_symbol
    from taxjson.lib.ticker_map import is_future_ticker
    by_sym: Dict[str, Dict[str, Any]] = {}
    year = None
    for acct, f in files.items():
        data = _load_json_or_die(f)
        year = year or (data.get("summary") or {}).get("year")
        for h in (data.get("inventory") or []):
            sym = str(h.get("symbol") or "")
            v = Holding(h)
            qty = float(v.quantity or 0)
            if not sym or abs(qty) < 1e-12:
                continue
            if not options and is_option_symbol(sym):
                continue
            if (not futures and is_future_ticker(sym)
                    and not is_option_symbol(sym)):
                # A futures contract is not a share: it was listed as
                # "1 share" with its notional as book cost (S044-04).
                # (Futures options follow --options.)
                continue
            e = by_sym.setdefault(sym, {"qty": 0.0, "cost": 0.0,
                                        "accounts": {},
                                        "unknown_cost": False})
            e["qty"] += qty
            e["cost"] += float(v.total_cost or 0)
            e["accounts"][acct] = e["accounts"].get(acct, 0.0) + qty
            # Units held before the data (an opening cost=unknown): the
            # cost is the known part only (backlog TJ-0774).
            e["unknown_cost"] = e["unknown_cost"] or bool(
                h.get("unknown_cost"))
    return by_sym, year


def cmd_shares(args: argparse.Namespace) -> None:
    """`tjs shares [--options] [--taxable|--sheltered] [--json]`: the
    COMBINED quantity held of each symbol across all accounts — the
    canonical inventory (post ticker.map, base-currency, wash-adjusted
    where built), summed per symbol with a per-account breakdown. Shorts
    net against longs (the breakdown shows them). Option contracts are
    left out unless --options; positions netting to zero are dropped."""
    from taxjson.lib.report_model import gains_basis_label, resolve_gains_files
    from taxjson.lib.report_fmt import fmt_qty as qfmt
    root = Path(args.dir).resolve()
    cache = root / "work"
    files = resolve_gains_files(cache)
    if not files:
        _die_input("no gains files in work/ — run `tjs run` first")
    _warn_accounts_without_books(root, files, "shares", "gains file")
    basis = gains_basis_label(files)
    want = None
    if getattr(args, "taxable", False) or getattr(args, "sheltered", False):
        want = "taxable" if args.taxable else "sheltered"
        if not _PL.has_config(root):
            _die("--taxable/--sheltered need taxjson.toml (account "
                 "types).")
        types = {n: (a.get("type", "sheltered") if isinstance(a, _cabc.Mapping)
                     else "sheltered")
                 for n, a in (load_config(root).get("accounts") or {}).items()}
        files = {n: f for n, f in files.items() if types.get(n) == want}
        if not files:
            _die(f"no {want} account with books in work/",
                 "Accounts and their types come from taxjson.toml; run "
                 "`tjs run` first.")
    by_sym, year = held_by_symbol(
        files, options=bool(getattr(args, "options", False)))
    rows = [(sym, e) for sym, e in by_sym.items()
            if abs(e["qty"]) > 1e-9]
    if getattr(args, "sort", "symbol") == "qty":
        rows.sort(key=lambda r: (-abs(r[1]["qty"]), r[0]))
    else:
        rows.sort(key=lambda r: r[0])
    base = _base_currency(root)
    # The inventory is END OF DATA, not the tax year's Dec 31: the
    # header said "tax year 2025" over 2026 positions (S044-05, the
    # sibling of list's R1-282).
    horizon = _books_horizon(cache, list(files))
    if getattr(args, "json", False):
        _json_out({"basis": basis, "year": year, "as_of": horizon,
                   "currency": base,
                   "scope": want or "all",
                   "options_included": bool(getattr(args, "options",
                                                    False)),
                   "rows": [{"symbol": sym, "qty": M.units(e["qty"], 8),
                             "cost": M.shown(e["cost"], 2),
                             "unknown_cost": e["unknown_cost"],
                             "accounts": {a: M.units(q, 8) for a, q in
                                          sorted(e["accounts"].items())}}
                            for sym, e in rows]}, schema="shares")
        return
    from taxjson.lib.out import Doc
    scope = f" ({want} accounts)" if want else ""
    doc = Doc(f"SHARES HELD — combined across{scope} "
              f"{', '.join(sorted(files))}")
    doc.para("As of the books' latest data"
             + (f" ({horizon})" if horizon else "")
             + f", basis: {basis}, after ticker.map; no futures"
             + ("" if getattr(args, "options", False)
                else ", no option contracts")
             + f". COST = combined book cost in {base}.")
    if not rows:
        doc.para("No open positions.")
        doc.print()
        return
    # The per-account breakdown is one cell (it has spaces inside it).
    body = [[sym, qfmt(e["qty"]),
             fmt_money(e["cost"]) + ("+?" if e["unknown_cost"] else ""),
             ", ".join(f"{a} {qfmt(q)}"
                       for a, q in sorted(e["accounts"].items())
                       if abs(q) > 1e-12)]
            for sym, e in rows]
    doc.table(["SYMBOL", "SHARES", "COST", "ACCOUNTS"], body,
              aligns=["<", ">", ">", "<"])
    doc.blank().para(f"{len(rows)} symbol(s).")
    n_unk = sum(1 for _s, e in rows if e["unknown_cost"])
    if n_unk:
        from taxjson.lib.out import act as _act
        doc.item(_act(f"{n_unk} symbol(s) hold units of unknown cost (an "
                      f"opening cost=unknown): COST +? is the known part "
                      f"only", "tjs find-missing-history")[len(_out_act_prefix()):],
                 bullet=_out_act_prefix())
    doc.print()


def _list_positional_date(args: argparse.Namespace) -> None:
    """`tjs list [ACCOUNT] [YYYY-MM-DD]`: a positional YYYY-MM-DD is
    the date (as --date), anything else the account — `list margin
    2026-04-28` and `list 2026-04-28` both work. Sets args.account and
    args.date; two dates, two accounts or a date that differs from
    --date stop with the usage."""
    given = [v for v in (getattr(args, "account", None),
                         getattr(args, "when", None)) if v]
    dates = [v for v in given if _ISO_DATE_RE.match(v)]
    accounts = [v for v in given if not _ISO_DATE_RE.match(v)]
    if len(dates) > 1 or len(accounts) > 1:
        _die(f"list takes one account and one date: got "
             f"{' '.join(given)}",
             "Usage: tjs list [ACCOUNT] [YYYY-MM-DD]")
    if dates:
        if getattr(args, "date", None) and args.date != dates[0]:
            _die(f"two dates: {dates[0]} and --date {args.date}",
                 "Give the date once.")
        args.date = dates[0]
    args.account = accounts[0] if accounts else None
    args.when = None


def _dated_inventory(cache: Path, acct: str, gains: Path, as_of: str,
                     acfg: Dict[str, Any], us: bool
                     ) -> Tuple[Dict[str, Any], str]:
    """`acct`'s positions at the end of `as_of` (the gains file's
    {"inventory", "summary"} shape) and the date basis they are on: the
    engine's own pools that day, from the cost timeline its canonical
    gains pass wrote (`gains`, the file plain `list` reads; timeline_beside,
    lib/cost_timeline; backlog TJ-0417). A blended pass's pools (Canada's
    s.47 pool across the taxable accounts) are apportioned by the
    account's own units that day, as taxjson-split-gains apportions them
    at the books' end; the US engine's lots are the account's own."""
    from types import SimpleNamespace
    from taxjson.lib import cost_timeline as CT
    from taxjson.lib.pipeline import declared_multipliers
    doc = _load_json_or_die(gains)
    split = bool((doc.get("metadata") or {}).get("split_from"))
    combined = (cache / (".cryptoblend_gains_wash.json" if typed_account(acfg).crypto
                         else ".blend_gains_wash.json")
                if split else gains)
    tl_path = timeline_beside(combined)
    if not tl_path.exists():
        _die(f"no cost timeline for {acct} (work/{tl_path.name})",
             "Run `tjs run`: it writes the positions per day with the "
             "gains.")
    tl = _load_json_or_die(tl_path)
    problem = CT.check(tl)
    if problem:
        # A damaged sidecar is an input that cannot be read: exit 2
        # (docs/commands.md's exit codes; backlog TJ-0684).
        _die_input(f"work/{tl_path.name}: {problem}",
                   "Run `tjs run` to rebuild it.")
    basis = str(tl.get("basis") or "settle")
    rows = CT.inventory_on(tl, as_of, eps=CT.us_eps if us else None)
    # A short beside a long, at its own proceeds (backlog TJ-1104).
    rows += CT.short_rows_on(tl, as_of)
    base_p = cache / f"{acct}_base.json"
    book = (list(_load_json_or_die(base_p).get("transactions") or [])
            if base_p.exists() else [])
    if split:
        from taxjson.bin.taxjson_split_gains import (
            _missing_history_openings, _position_starts, _split_inventory)
        from taxjson.lib.positions_check import (blended_pool_rows,
                                                 pool_splits,
                                                 without_pool_cut)
        _pool = blended_pool_rows(cache, acct)
        book = without_pool_cut(book, _pool, acct)
        book += _missing_history_openings(_load_json_or_die(combined), acct)
        # The splits another account of the pool reports: the engine
        # split this account's shares too (backlog TJ-0696).
        book += pool_splits(_pool, acct)
        dk = "date_settle" if basis == "settle" else "date"
        upto = [r for r in book
                if str(r.get(dk) or r.get("date") or "")[:10] <= as_of]
        inv = _split_inventory(rows, acct, book,
                               _position_starts(upto, basis), as_of, basis,
                               pool_rows=_pool)
    else:
        # The account's own pass: every pool in it is the account's (its
        # gains file's inventory, which plain `list` shows, has them all).
        inv = [dict(r, account=acct) for r in rows]
    # The declared contract sizes, as the run puts them on the inventory
    # (pipeline.annotate_inventory_multipliers).
    mults = declared_multipliers(
        [SimpleNamespace(symbol=str(t.get("symbol") or ""),
                         multiplier=t.get("multiplier") or 0.0)
         for t in book if isinstance(t, dict)])
    for r in inv:
        if mults.get(r.get("symbol")):
            r["multiplier"] = mults[r["symbol"]]
    return {"inventory": inv, "summary": doc.get("summary") or {}}, basis


def cmd_positions(args: argparse.Namespace) -> None:
    """List open positions per account, taken from each account's canonical
    gains file's `inventory` (wash-adjusted where built) — i.e. AFTER
    ticker.map consolidation (cross-listings merged) and base-currency
    conversion, so quantities and cost basis match the canonical pipeline.
    One row per (account, symbol); a base-currency book-cost TOTAL closes
    the table."""
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    _list_positional_date(args)
    # Canonical per-account gains (wash-adjusted where built — same basis
    # as every other query command); the raw/native derivatives keep
    # cross-listings separate and in native currency, which is the opposite
    # of what this view wants.
    as_of = getattr(args, "date", None)
    if as_of:
        files, tmp_docs, basis = _positions_as_of(args, as_of, root, cache, resolve_gains_files,
                                                  gains_basis_label)
    else:
        files = resolve_gains_files(cache, args.account or None)
        if not files:
            if args.account:
                _die_input(f"no gains for account {args.account!r} in "
                           f"work/ — run `tjs run` first, or check the "
                           f"name")
            _die_input("no gains files in work/ — run `tjs run` first")
        basis = gains_basis_label(files)

    money = fmt_money               # shared report-layer formatter
    from taxjson.lib.report_fmt import fmt_qty as qfmt
    from taxjson.lib.option_symbols import is_option_symbol as _is_opt_sym
    from taxjson.lib.ticker_map import is_future_ticker as _is_future_sym

    header = ["ACCOUNT", "SYMBOL", "QTY", "COST", "COST/SH",
              "DEFERRED", "SINCE"]
    out_rows: List[List[str]] = []
    json_rows: List[Dict[str, Any]] = []
    # A short that is a purchase missing from the files (a sale with
    # nothing to close), told apart from a real short — the pairs
    # find-missing-history reports (lib/missing_history.
    # missing_history_suspect).
    _suspects = _missing_history_suspects(root, cache)
    total_cost = 0.0
    total_deferred = 0.0
    n_pos = 0
    n_unknown = 0
    year = None
    for acct, p in files.items():
        if as_of:
            data = tmp_docs[acct]
        else:
            data = _load_json_or_die(p)
        year = year or (data.get("summary") or {}).get("year")
        inv = sorted(data.get("inventory") or [], key=lambda r: str(r.get("symbol") or ""))
        total_cost, total_deferred, n_pos, n_unknown = _positions_account_rows(
            inv, args, _is_opt_sym, _is_future_sym, _suspects, acct, n_unknown, out_rows, qfmt,
            money, json_rows, total_cost, total_deferred, n_pos)

    negative_only = getattr(args, "negative", False)

    # What the rows are AS OF: the --date, or the end of the books
    # (plain `list` is end-of-data, not the tax year's Dec 31 — its
    # header said "as of tax year 2025" over 2026 positions, R1-282).
    horizon = as_of or _books_horizon(cache, list(files))
    if getattr(args, "json", False):
        doc = {"rows": json_rows, "basis": basis, "year": year,
               "as_of": horizon,
               "currency": _base_currency(root),
               "totals": {"positions": n_pos,
                          "book_cost": M.shown(total_cost, 2),
                          "deferred_wash": M.shown(total_deferred, 2),
                          "unknown_cost_positions": n_unknown}}
        if negative_only:
            doc["filter"] = "negative"
        _json_out(doc, schema="list")
        return

    # The report: a live exact Doc prints each line as the raw print()
    # did, when it is added.
    d = _out.Doc(exact=True, width_=0, live=True)
    if n_pos == 0:
        scope = f" for account {args.account!r}" if args.account else ""
        if negative_only:
            d.line(f"No negative positions{scope}.")
        else:
            d.line(f"No open positions{scope}.")
        return

    base, _det, n_sus = _positions_table(root, negative_only, as_of, horizon, args, basis,
                                         total_deferred, out_rows, header, n_pos, money,
                                         total_cost, n_unknown)
    if not _det:
        from taxjson.lib.out import act as _act
        if n_sus and not negative_only:
            d.line(_act(f"{n_sus} short position(s) with no purchase in "
                        f"your files (missing history?)",
                        "tjs list --negative"))
        elif n_sus:
            # Essentials first: the fixes are find-missing-history's.
            d.line(_act("supply each missing purchase, or record it as an "
                        "opening with no cost", "tjs find-missing-history"))
        if total_deferred > 0.005:
            _us_l = _country(load_settings(root, missing_ok=True)) == "usa"
            d.line(f"DEFERRED in the cost:  {money(total_deferred)} {base} "
                   + ("of disallowed wash-sale losses (§1091)" if _us_l
                      else "of denied superficial losses (s.54)"))
        from taxjson.lib.out import details_hint as _dh
        d.line(_dh("tjs list --details"))
        return
    if n_sus and not negative_only:
        _vprint(f"{n_sus} short position(s) marked `missing history?`: a "
                f"sale with no purchase in your files, not a real short — "
                f"`tjs list --negative` lists them apart.", hang="  ")
    if total_deferred > 0.005:
        _us_l = _country(load_settings(root, missing_ok=True)) == "usa"
        _vprint(f"DEFERRED: {money(total_deferred)} {base} of the book "
                f"cost is "
                + ("losses disallowed under the wash-sale rule (§1091), "
                   "added to these positions' basis"
                   if _us_l else
                   "denied superficial losses parked in these positions")
                + " (recovered when sold without a rebuy in the window).",
                hang="  ")


def _positions_as_of(args: argparse.Namespace, as_of: Any, root: Path, cache: Path,
                     resolve_gains_files: Callable[..., Dict[str, Path]],
                     gains_basis_label: Callable[..., str]
                     ) -> Tuple[Dict[str, Path], Dict[str, Dict[str, Any]], str]:
    """tjs list --date: the date checked (a real calendar date), the
    accounts and their types from taxjson.toml, each account's pools
    that day from the cost timeline its canonical gains pass wrote,
    and the basis said by trade or settlement date. Returns the
    gains files, each account's positions that day and the basis."""
    # Positions AS OF a date: the engine's own pools that day, read
    # from the cost timeline each account's canonical gains pass (the
    # file plain `list` reads) wrote — the blended pass: Canada's s.47
    # ACB, the US engine's per-account FIFO lots, an own move's units
    # with the cost they brought (backlog TJ-0417; a recompute of
    # each account's books alone valued them at 0, TJ-0278 / TJ-0387,
    # and took a same-day sale before their arrival, TJ-0277).
    import re as _re
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        _die_input("--date expects YYYY-MM-DD")
    try:
        datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        # Shape-only validation let 2025-15-02 (swapped day/month)
        # through to the engine's string compare — wrong positions
        # at exit 0 with the bogus date in the banner (REVIEW #27).
        _die_input(f"--date {as_of} is not a real calendar date")
    settings = load_settings(root, missing_ok=True)
    # Accounts and their TYPES come from the config (a crypto
    # account's blended pass is the crypto one).
    accounts_cfg = (load_config(root).get("accounts", {})
                    if _PL.has_config(root) else {})
    if not accounts_cfg:
        _die("--date needs taxjson.toml (account names and types).")
    if args.account and args.account not in accounts_cfg:
        _die(f"no [accounts.{args.account}] in taxjson.toml")
    files = resolve_gains_files(cache, args.account or None)
    if not files:
        if args.account:
            _die_input(f"no gains for account {args.account!r} in "
                       f"work/ — run `tjs run` first")
        _die_input("no gains files in work/ — run `tjs run` first")
    from taxjson.lib.country import is_usa
    _us = is_usa(_country(settings))
    tmp_docs = {}
    _bases = set()
    for n, p in files.items():
        tmp_docs[n], _b = _dated_inventory(
            cache, n, p, as_of, accounts_cfg.get(n) or {}, _us)
        _bases.add(_b)
    _asof_basis = (next(iter(_bases)) if len(_bases) == 1
                   else _tax_date_basis(settings))
    _asof_word = ("settlement date" if _asof_basis == "settle"
                  else "trade date")
    basis = (f"{gains_basis_label(files)}, as of {as_of} by "
             f"{_asof_word}")
    return files, tmp_docs, basis


def _positions_account_rows(inv: List[Any], args: argparse.Namespace,
                            _is_opt_sym: Callable[[str], bool],
                            _is_future_sym: Callable[[str], bool],
                            _suspects: Set[Tuple[str, str]], acct: str, n_unknown: int,
                            out_rows: List[List[str]], qfmt: Callable[..., str],
                            money: Callable[..., str], json_rows: List[Dict[str, Any]],
                            total_cost: float, total_deferred: float, n_pos: int
                            ) -> Tuple[float, float, int, int]:
    """tjs list: one account's rows, a row per open position (a closed
    one skipped; --negative: the shorts only): the cost per share (an
    equity option per share, 100 a contract), the deferred loss, the
    missing-history mark and the unknown cost, appended to the text
    and JSON rows. Returns the totals with this account's added."""
    for h in inv:
        v = Holding(h)
        qty = float(v.quantity or 0)
        if qty == 0:                       # fully closed — not a position
            continue
        if getattr(args, "negative", False) and qty > 0:
            continue
        cost = float(v.total_cost or 0)
        # Signed division on purpose: shorts carry qty < 0 and
        # total_cost < 0 (proceeds credited), so COST/SH comes out
        # positive — same convention as the holdings export. An
        # equity option is 100 shares a contract: per SHARE, like
        # harvest and the holdings report (it was per contract,
        # 100x theirs — S045-03).
        _sym = str(h.get("symbol") or "")
        # A declared contract size (a futures option's CL 1000 —
        # S026-22) wins; an undeclared futures option stays per
        # contract.
        _mult = (float(h["multiplier"]) if _is_opt_sym(_sym)
                 and h.get("multiplier")
                 else 100.0 if _is_opt_sym(_sym)
                 and not _is_future_sym(_sym) else 1.0)
        cps = cost / (qty * _mult) if qty else 0.0
        deferred = float(v.deferred_wash or 0)
        suspect = qty < 0 and (_sym, acct) in _suspects
        # Units held before the data (an opening cost=unknown): the
        # cost is the known part only, said so (backlog TJ-0774).
        unk = bool(h.get("unknown_cost"))
        n_unknown += unk
        out_rows.append([
            acct, str(h.get("symbol") or "?"), qfmt(qty),
            money(cost) + ("+?" if unk else ""),
            money(cps) + ("+?" if unk else ""),
            money(deferred) if deferred > 0.005 else "-",
            str(h.get("position_start_date") or "-"),
            "missing history?" if suspect else "-"])
        json_rows.append({"account": acct,
                          "symbol": h.get("symbol"),
                          "qty": qty, "cost": M.shown(cost, 2),
                          "cost_per_share": M.shown(cps, 4),
                          "deferred_wash": M.shown(deferred, 2),
                          "since": h.get("position_start_date"),
                          "last_acq_date": h.get("last_acq_date"),
                          "missing_history_suspect": suspect,
                          "unknown_cost": unk})
        total_cost += cost
        total_deferred += deferred
        n_pos += 1
    return total_cost, total_deferred, n_pos, n_unknown


def _positions_table(root: Path, negative_only: Any, as_of: Any, horizon: Any,
                     args: argparse.Namespace, basis: str, total_deferred: float,
                     out_rows: List[List[str]], header: List[str], n_pos: int,
                     money: Callable[..., str], total_cost: float, n_unknown: int
                     ) -> Tuple[str, bool, int]:
    """tjs list, the text report: the title and its date, the legend
    (or the --details basis), the table (with --negative, the real
    shorts and the missing histories apart), the count line and the
    line on units of unknown cost. Returns the base currency,
    --details given and the missing-history count, for the lines
    after it."""
    base = _base_currency(root)
    title = "NEGATIVE POSITIONS" if negative_only else "OPEN POSITIONS"
    when = (f"as of {as_of}" if as_of else
            f"as of the latest data in the books"
            + (f" ({horizon})" if horizon else ""))
    _vprint(f"{title} — {base}, {when}")
    # The report's lines between _vprint's prose: a live exact Doc
    # prints each as the raw print() did, when it is added (the
    # table's through its own: a Doc is never read by a nested
    # function).
    d = _out.Doc(exact=True, width_=0, live=True)
    _det = _details(args)
    if _det:
        _vprint(f"COST is book cost after ticker.map and the base-currency "
                f"conversion, basis: {basis}.")
        d.blank()
    else:
        # The legend, before the table (docs/output-style.md).
        _vprint(f"COST: book cost in {base} after ticker.map, basis: "
                f"{basis}" + ("; DEFERRED: denied losses in it."
                              if total_deferred > 0.005 else "."))
    from taxjson.lib.out import fit_table as _fit_table
    n_sus = sum(1 for r in out_rows if r[7] != "-")

    def _table(rows, note=True):
        # Too wide: SINCE, then COST/SH, then DEFERRED go first. The
        # NOTE column only when a row carries one.
        dt = _out.Doc(exact=True, width_=0, live=True)
        cols = header + (["NOTE"] if n_sus and note else [])
        for ln in _fit_table(cols, [r[:len(cols)] for r in rows],
                             drop=(6, 4, 5), key=(0, 1)):
            dt.line(ln)
    if negative_only and n_sus:
        # Two kinds of negative position: a real short (the broker marks
        # it, an option or a future sold to open) and a purchase missing
        # from the files.
        shorts = [r for r in out_rows if r[7] == "-"]
        if shorts:
            _vprint(f"Short positions ({len(shorts)}):")
            _table(shorts, note=False)
            d.blank()
        _vprint(f"Missing history (a sale with no purchase in your files) "
                f"({n_sus}):")
        _table([r for r in out_rows if r[7] != "-"], note=False)
        d.blank()
    if negative_only and n_sus and not _det:
        pass        # one `! ` line under the count (below)
    elif negative_only and n_sus:
        _vprint("Each is a sale your files show nothing to close for: a "
                "purchase before the data (or a transfer-in) is missing. "
                "Supply it (`tjs find-missing-history` lists the "
                "fixes), or record them all as openings with no cost so "
                "the shorts disappear: `tjs find-missing-history "
                "--write-missing-history --all-history` (their sales then "
                "leave the totals and are reported by hand; "
                "`--outside-year` records only the ones that do not touch "
                "the tax year).", hang="  ")
        d.blank()
    else:
        _table(out_rows)
        d.blank()
    _vprint(f"{n_pos} position(s), total book cost {money(total_cost)} "
            f"{base}" + (f", plus the unknown cost in {n_unknown} of "
                         f"them" if n_unknown else ""))
    if n_unknown:
        from taxjson.lib.out import act as _act_u
        d.line(_act_u(f"{n_unknown} position(s) hold units of unknown cost "
                      f"(an opening cost=unknown): COST +? is the known "
                      f"part only", "tjs find-missing-history"))
    return base, _det, n_sus


def _books_horizon(cache: Path, accounts: List[str]) -> Optional[str]:
    """Latest transaction date across these accounts' base books, on
    the project's date basis: a settle-basis book has already applied
    a Dec-31 trade's January settlement, so its horizon is that
    settlement date (audit A2-0698). A base book that exists but cannot
    be read is named on stderr instead of silently moving the date
    earlier (A2-0702)."""
    from taxjson.lib.country import (CountryError, resolve_tax_date,
                                     settings_country)
    try:
        _s = load_settings(cache.parent, missing_ok=True)
        settle = resolve_tax_date(settings_country(_s), _s.tax_date) == "settle"
    except CountryError:
        settle = False        # no usable config: the trade date
    last = None
    for a in accounts:
        p = cache / f"{a}_base.json"
        try:
            doc = _read_work_doc(p)
            txs = doc.get("transactions", []) if isinstance(doc, dict) \
                else doc
            if not isinstance(txs, list):
                raise ValueError("no transactions list")
        except FileNotFoundError:
            continue
        except (OSError, ValueError, AttributeError) as e:
            emit_line(f"tjs: warning: work/{p.name} cannot be read "
                      f"({str(e)[:120]}) — the 'as of' date leaves account "
                      f"{a} out; re-run `tjs run`.", file=sys.stderr)
            continue
        for t in txs:
            if not isinstance(t, dict):
                continue
            d = str((t.get("date_settle") if settle else None)
                    or t.get("date") or "")[:10]
            if len(d) == 10 and (last is None or d > last):
                last = d
    return last


def _missing_history_suspects(root: Path, cache: Path
                              ) -> Set[Tuple[str, str]]:
    """{(symbol, account)} of the positions that go short in the
    project's books because a sale had nothing to close
    (lib/missing_history.missing_history_suspect) — read like
    find-missing-history reads them. Empty when the books cannot be
    read (advisory)."""
    try:
        from taxjson.lib.tx_load import load_transactions
        from taxjson.lib.missing_history import (account_types_near,
                                                  missing_history_suspects,
                                                  walk_journal_symbols)
        files = _project_base_files(cache)
        txs = []
        for f in files:
            try:
                txs.extend(load_transactions(f))
            except (OSError, ValueError):
                continue
        tm = _PL.ticker_map_path(root)
        journal = walk_journal_symbols(cache, tm if tm.is_file() else None)
        return missing_history_suspects(
            txs, registered=(account_types_near(files[0]) if files
                             else {}),
            country=_country(load_settings(root, missing_ok=True)), journal_symbols=journal)
    except Exception:                               # noqa: BLE001
        return set()


def _register_list(sub: argparse._SubParsersAction) -> None:
    p_pos = sub.add_parser(
        "list",
        help="Open positions per account, with book cost",
        description="List the open positions per account (quantity and "
                    "base-currency book cost) after ticker.map "
                    "consolidation and base-currency conversion, at the "
                    "end of the books or a date (`list margin 2026-04-28`, "
                    "`list 2026-04-28`, or --date).")
    p_pos.add_argument("account", nargs="?",
                       help="Account (default: all); a YYYY-MM-DD here is "
                            "the date (as --date)")
    p_pos.add_argument("when", nargs="?", metavar="YYYY-MM-DD",
                       help="Positions as of this date (as --date)")
    p_pos.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                       help="Positions AS OF this date, on the project's "
                            "date basis (settlement date unless tax_date "
                            "= \"trade\"): the engine's pools that day "
                            "from the pass plain `list` reads, which "
                            "`tjs run` writes per day with the gains "
                            "(Canada: each account's share of the s.47 "
                            "ACB across the taxable accounts; USA: the "
                            "account's own FIFO lots), the deferred "
                            "losses, missing-history lines and own moves "
                            "applied")
    p_pos.add_argument("--negative", action="store_true",
                       help="Show only positions with negative quantity, "
                            "in two sections: real short positions, and "
                            "missing history (a sale with no purchase in "
                            "your files: marked `missing history?` in the "
                            "plain list)")
    p_pos.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_pos.set_defaults(func=cmd_positions)


def _register_shares(sub: argparse._SubParsersAction) -> None:
    p_sh = sub.add_parser(
        "shares",
        help="Total shares held per symbol, all accounts",
        description="Combined quantity held of each symbol across all "
                    "accounts (after ticker.map), with a per-account "
                    "breakdown and the combined book cost; option "
                    "contracts only with --options.")
    p_sh.add_argument("--options", action="store_true",
                      help="Include option contracts (excluded by default)")
    g = p_sh.add_mutually_exclusive_group()
    g.add_argument("--taxable", action="store_true",
                   help="Only taxable accounts")
    g.add_argument("--sheltered", action="store_true",
                   help="Only sheltered (registered) accounts")
    p_sh.add_argument("--sort", choices=["symbol", "qty"], default="symbol",
                      help="Order by symbol (default) or by size")
    p_sh.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_sh.set_defaults(func=cmd_shares)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_list(sub)
    _register_shares(sub)
