"""The Row listings commands: cash, dil, divs, events, fees, gains, leaps, roc, staking, trades, transfers (gains-base: cli/gains_filing.py)."""
import argparse
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from taxjson.project.accounts import accounts_of
from taxjson.pipeline.config import load_config, load_settings
from taxjson.lib.country import is_usa
from taxjson.lib.in_kind import IN_KIND_FILE
from taxjson.model.result import GainRow
from taxjson.model import money as M
from taxjson.lib.report_fmt import align_columns, fmt_money, fmt_qty
from taxjson.lib import out as _out

from taxjson.cli.common import (
    _accounts_skipped_for_no_inputs, _act_item, _base_currency,
    _CAPITALIZED_LBL, _capitalized_pil_shares, _collect_period_txs,
    _discover_tx_accounts, _dist_adjust_rows, _gains_row_date, _ISO_DATE_RE,
    _leaps_closed, _leaps_contracts, _leaps_empty_doc, _leaps_rule,
    _leaps_scope_guard, _load_json_or_die, _print_scope_split,
    _die_cash_ids, _real_world_legs, _refuse_cash_older_than_inputs, _require_books, _scope_split,
    _settle_basis,
    _staking_view, _TRADE_ACTIONS, _trade_total, _tx_fee,
    _view_income_rules, _view_msg, _view_window, _vprint,
    _warn_accounts_without_books, _warn_bad_dates, _warn_dist_double_entry,
    _PERIOD_HELP, _add_instrument_filters, _add_staking_parser,
)
from taxjson.pipeline.inputs import _tax_date
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.run import _fx_cash_native, _native_tx_file
from taxjson.pipeline.sanity import _has_inputs, _json_out, _print_report_table
from taxjson.pipeline.stages import (
    _read_work_doc,
    transfer_arrivals,
)

# The views' print() calls (PLAN step 15): a one-line message is `out.put`;
# the rest are the ops of a live exact Doc, `out.Doc(exact=True, width_=0,
# live=True)`, each printed when it is added (`page.line(e)` is `print(e)`,
# `page.blank()` is `print()`); `out.put("")` is `print()` where a Doc
# would take cmd_gains over 150 lines.


def _tx_display_line(tx: dict, settle: bool = False,
                     settled: bool = False) -> Optional[str]:
    """Human-readable line for `tjs events`. Money amounts (total,
    fee, dividend/tax/interest/adjust amount) are shown to 2 decimals; quantity
    and per-share price keep their significant digits (a 0.0375 dividend rate
    or a 0.25178314 crypto qty must not be rounded away). Mirrors the .tt field
    layout but is a DISPLAY formatter — distinct from tx_to_tt_line, which
    keeps full precision for round-trippable .tt output. Returns None for
    actions with no representation. `settle=True` writes the SETTLEMENT
    date (a .tt line's single date on a settle-basis project): the
    single-account view is round-trippable taxtext, and pasting its
    trade-dated Dec-31 sale into next year's .tt dropped it from both
    years (S002-03)."""
    money = fmt_money               # dollar amount → 2 decimals (shared)

    def sig(x):                         # qty / price → full precision
        # repr(): EXACT round-trip. The old :.8f truncated a 9-decimal
        # crypto qty, so re-importing the view produced a different
        # transaction id — dedup missed and every trade double-booked
        # (REVIEW #19). Written out in full, never 5e-08: a .tt field
        # a person reads and a parser takes alike.
        from decimal import Decimal
        s = repr(float(x or 0))
        if "e" in s or "E" in s:
            s = format(Decimal(s), "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("", "-0", "-") else s

    action = tx.get("action", "")
    # An income, withholding or fee row keeps the day it was paid on
    # the settle basis too, as tx_to_tt_line writes it (backlog TJ-0862).
    from taxjson.lib.tt_format import tt_time, tx_tt_date
    date = tx_tt_date(tx, "settle" if settle else "trade")
    # An untimed row's clock as the .tt writer gives it (TJ-0983).
    time = tt_time(tx)
    sym = tx.get("symbol", "")
    # "?" like every other view (trades-sum, fees): a CAD label on a
    # currency-less row misstated it in a non-CAD project, and the line
    # is re-importable .tt (R1-287).
    cur = tx.get("currency") or "?"
    qty, price = tx.get("quantity"), tx.get("price")
    net = float(tx.get("net_amount") or 0.0)
    gross = float(tx.get("gross_amount") or 0.0) or net
    # fee + commission: parsers split the charge across both keys
    # (Questrade uses `commission`), so reading only `fee` printed
    # 0.00 on every row — and the "round-trippable" single-account
    # view silently erased the deductible fees on re-import
    # (REVIEW #36/#18). Same rule as _tx_fee (fees/trades-sum).
    fee = (float(tx.get("fee") or 0.0)
           + float(tx.get("commission") or 0.0))

    # The tokens at the end of the line (a contract size, a warrant
    # exercise's `exercise_of=`, an income row's `record=` / `label=` /
    # `type=` ...) are tx_to_tt_line's own (tt_tail): the view wrote
    # none of the income facts, so a pasted trust distribution moved to
    # its pay year, a warrant exercise became a sale at 0, and a size of
    # a million (`x1e+06`) did not read back (backlog TJ-0390, TJ-0377,
    # TJ-0385; A2-0621).
    # `settled`: the round-trippable view also writes a row's other
    # date when the line's date is not both (`settled=`, `traded=`:
    # backlog TJ-5211, TJ-5213). A row no .tt line can say (taxjson-convert-tt
    # refuses it: a move in kind, a reward's FEE row) ends with
    # `not_tt=<why>`, which the reader refuses if the line is pasted,
    # saying why: printed plain it read as cash (backlog TJ-5212).
    from taxjson.lib.tt_format import NOT_TT_KEY, date_token, not_tt_why, tt_tail
    basis = "settle" if settle else "trade"
    tail = tt_tail(tx, action)
    why = not_tt_why(tx, basis)
    if why:
        tail += f" {NOT_TT_KEY}={why}"
    elif settled:
        tail += date_token(tx, basis)

    from taxjson.lib.row_types import is_opening_row
    if is_opening_row(tx):
        # A positions snapshot is written in tx_to_tt_line's OPENING
        # form (its own date, no time column, the lot's acquisition
        # date last). As a BUYSELL line it read back as a purchase:
        # added to the account's earlier rows of the symbol that the
        # snapshot replaces, and without the lot date the US holding
        # period runs from it (US-OPEN-01).
        lot = str(tx.get("lot_date") or "").strip()
        return (f"OPENING {tx.get('date', '')} {sym} {sig(qty)} {cur} "
                f"{money(net)}" + (f" {lot}" if lot else ""))

    # SIGNED total/fee, exactly as convert_tt.tx_to_tt_line emits them:
    # abs() printed every fee rebate as a charge and a penny close's
    # negative proceeds as positive, so the "round-trippable" view
    # flipped both on re-import (audit S039-11).
    if action in ("BUYSELL", "ASSIGN"):
        return f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} {money(net)} {money(fee)}{tail}"
    if action == "TRANSFER":
        # The opt-in DECLARED token rides along as in tx_to_tt_line, or a
        # re-import loses the declaration (audit A2-0989).
        from taxjson.lib.tt_format import MANUAL_TRANSFER_DECLARATION
        decl = (" DECLARED" if tx.get("description")
                == MANUAL_TRANSFER_DECLARATION else "")
        return (f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} "
                f"{money(net)}{decl}{tail}")
    if action == "SPLIT":
        return f"SPLIT {date} {time} {sym} {tx.get('symbol_new') or sym} {sig(qty)}{tail}"
    if action in ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX"):
        # Signed so a reversal row reads as negative instead of masquerading
        # as more income/tax.
        # The withholding-NETTED 9th column, as tx_to_tt_line writes it:
        # without it the line re-parses net as gross (audit A2-0989).
        net9 = f" {money(net)}" if abs(net - gross) > 0.005 else ""
        return f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} {money(gross)}{net9}{tail}"
    if action in ("INTEREST", "FEE"):
        # `symbol=`: the property an INTEREST row is paid on (backlog
        # TJ-0856).
        return f"{action} {date} {time} {cur} {money(net)}{tail}"
    if action in ("ADJUST", "DISALLOW"):
        return f"{action} {date} {time} {sym} {cur} {money(net)}{tail}"
    return None


# Back-compat aliases: the table helpers were promoted to
# taxjson.lib.report_fmt (shared with the standalone report tools); the
# 20+ call sites below keep their historical names.
_align_columns = align_columns

_TT_FACT_TOKEN_RE = re.compile(r"^[a-z_]+=")


def _align_tx_lines(lines: List[str]) -> List[str]:
    """The row views' aligned taxtext: a line's trailing `key=value`
    tokens (`corp=`, `election=`, `record=` ...) are a cell of their own
    after the columns, one start for all. A shorter row's tokens fell
    into the PRICE and TOTAL columns of the trades and set their width
    (backlog TJ-1263). A token mid-line (a `ES=F` symbol) is a column."""
    heads, tails = [], []
    for ln in lines:
        toks = ln.split()
        n = len(toks)
        while n and _TT_FACT_TOKEN_RE.match(toks[n - 1]):
            n -= 1
        heads.append(" ".join(toks[:n]))
        tails.append(" ".join(toks[n:]))
    aligned = _align_columns(heads, numeric_right=True, header=False)
    width = max((len(h) for h, t in zip(aligned, tails) if t), default=0)
    return [f"{h:<{width}} {t}" if t else h for h, t in zip(aligned, tails)]


# ---- Instrument-class filters (trades / gains) -----------------------
# `--options/--equities/--futures/--puts/--calls` narrow a view to
# particular instrument kinds. Multiple flags OR together; no flag means
# "all instruments" (the historical default). A futures OPTION (F: prefix
# + OCC contract block) is genuinely both a future and an option, so it
# matches `--futures` AND `--options`/`--puts`/`--calls`.
_INSTRUMENT_FLAGS = ("options", "equities", "futures", "puts", "calls")


def _instrument_tags(symbol: str) -> set:
    """The instrument classes a symbol belongs to. A symbol can carry
    several tags: a futures option is `futures` + `options` + `calls`|
    `puts`; a plain equity is just `equities`."""
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_right
    from taxjson.lib.ticker_map import is_future_ticker
    sym = symbol or ""
    tags = set()
    opt = is_option_symbol(sym)
    fut = is_future_ticker(sym)
    if opt:
        tags.add("options")
        right = parse_option_right(sym)
        if right == "C":
            tags.add("calls")
        elif right == "P":
            tags.add("puts")
    if fut:
        tags.add("futures")
    if not opt and not fut:
        tags.add("equities")
    return tags


def _instrument_filter(args: argparse.Namespace):
    """A `symbol -> bool` predicate built from the --options/--equities/…
    flags, or None when none were given (i.e. show every instrument)."""
    wanted = {f for f in _INSTRUMENT_FLAGS if getattr(args, f, False)}
    if not wanted:
        return None
    return lambda sym: bool(wanted & _instrument_tags(sym))


def _tx_view_rows(root: Path, accounts: List[str], cache: Path, actions: Any,
                  symbol_filter: Any, keep: Any, label: str
                  ) -> Tuple[List[Tuple[str, str, str, Dict[str, Any]]], int, Any]:
    """The row views (`tjs events`, `divs`, `dil`, `trades`, `roc`): each
    account's native rows of the actions asked for (and the symbols, for
    the instrument filters) in the window: a trade by its settlement date
    on a settle-basis project, an income row by its tax date, a
    withholding with its payment; a row with no date counted. For `roc`,
    the [[distributions]] entries too and a double entry warned; the rows a
    tax date moved into the window said. Returns the rows (date, time,
    account, row), the rows with no date and the income rules
    (_run_tx_view)."""
    rows = []
    bad_dates = 0
    # A "tax year N" window on a settle-basis project takes a trade by
    # its settlement date, as the books and Schedule 3 do (S039-18).
    _settle = _settle_basis(root)
    # Income and ROC rows are windowed on their tax date (lib/
    # income_dating: a Canadian trust's distribution or ROC by its
    # record date, a listed US RIC January dividend on Dec 31), the
    # date divs-sum / roc-sum / the .sum use — the row views used the
    # pay date, so their tax-year totals disagreed (audit A2-0326,
    # A2-0641, A2-0642, A2-0655, A2-1109, A2-1125, A2-1127).
    _rules = _view_income_rules(root)
    moved = 0
    for acct in accounts:
        native = _native_tx_file(cache, acct)
        if native is None:
            _view_msg("note", f"no native transaction file for account "
                      f"{acct!r}; skipping it.")
            continue
        data = _load_json_or_die(native)
        # The tax withheld on a payment is dated with it (A2-0396).
        _wh = (_rules.withholding_dates(data.get("transactions", []))
               if _rules is not None else {})
        for tx in data.get("transactions", []):
            if actions is not None and tx.get("action") not in actions:
                continue
            if symbol_filter is not None and not symbol_filter(tx.get("symbol") or ""):
                continue
            d = tx.get("date") or ""
            if not _ISO_DATE_RE.match(d):    # can't place it in the window
                bad_dates += 1
                continue
            if tx.get("action") in _TRADE_ACTIONS:
                _wd = _gains_row_date(tx, keep, _settle)
            elif (_rules is not None and tx.get("action") in
                  ("DIVIDEND", "DIVIDEND_IN_LIEU", "ADJUST")):
                _wd = _rules.row_date(tx) or d
            elif id(tx) in _wh:
                _wd = _wh[id(tx)]
            else:
                _wd = d
            if keep(_wd):
                rows.append((d, tx.get("time") or "", acct, tx))
                if _wd[:4] != d[:4]:
                    moved += 1
    if label == "roc":
        for acct, tx in _dist_adjust_rows(cache, accounts, keep):
            rows.append((tx.get("date") or "", tx.get("time") or "",
                         acct, tx))
        # The same ROC as a book ADJUST and a [[distributions]] entry
        # cuts the ACB twice: the roc-sum warning, here too (A2-1116).
        _warn_dist_double_entry(root, accounts, _rules, keep, label)
    if moved:
        _view_msg("note", f"{moved} income row(s) shown with their pay "
                  f"date belong to this window by their tax date",
                  "A Canadian trust's record date, a listed RIC January "
                  "dividend (`tjs tax-logic`), as in divs-sum / "
                  "roc-sum.")
    return rows, bad_dates, _rules


def _tx_view_text(accounts: List[str], root: Path,
                  rows: List[Tuple[str, str, str, Dict[str, Any]]], _crypto_accts: Set[str],
                  _cap_share: Dict[Tuple[str, str], float], label: str, bad_dates: int) -> None:
    """The row views' text: aligned taxtext (one named account's lines
    re-importable, on its settlement dates on a settle-basis project; each
    line prefixed with its account otherwise), the empty state, the
    per-currency totals (buys, sells, dividends, payments in lieu, their
    part in a cover's cost, staking), the rows with no taxtext and those
    with no date said (_run_tx_view's tail)."""
    # A single named account prints pure taxtext (round-trippable); the
    # all-accounts view prefixes each line with the account so the merged
    # chronological list stays legible.
    prefix = len(accounts) != 1
    # The single-account (round-trippable) view writes each line's
    # SETTLEMENT date on a settle-basis project — what a .tt line's one
    # date means there (README "Importing manual cost basis").
    _st = load_settings(root, missing_ok=True)
    settle_dates = (not prefix and _tax_date(_st) == "settle")
    skipped = 0
    out_lines = []
    buys: Dict[str, float] = {}
    sells: Dict[str, float] = {}
    divs: Dict[str, float] = {}
    pils: Dict[str, float] = {}
    capped: Dict[str, float] = {}
    stake: Dict[str, float] = {}
    for _d, _t, acct, tx in rows:
        line = _tx_display_line(tx, settle=settle_dates, settled=not prefix)
        if line is None:
            skipped += 1
            continue
        out_lines.append(f"{acct} {line}" if prefix else line)
        cur = tx.get("currency") or "?"
        act = tx.get("action")
        if act in ("BUYSELL", "ASSIGN"):
            amt = _trade_total(tx)
            q = float(tx.get("quantity") or 0.0)
            if q > 0:
                buys[cur] = buys.get(cur, 0.0) + amt
            elif q < 0:
                sells[cur] = sells.get(cur, 0.0) + amt
        elif act in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            # Signed: a broker reversal row (negative amount) must net
            # against the original posting rather than inflate the total.
            amt = (float(tx.get("gross_amount") or 0.0)
                   or float(tx.get("net_amount") or 0.0))
            _b = (pils if act != "DIVIDEND"
                  else stake if acct in _crypto_accts else divs)
            _cap = amt * _cap_share.get((acct, str(tx.get("id"))), 0.0)
            if _cap:
                amt -= _cap
                capped[cur] = capped.get(cur, 0.0) + _cap
            _b[cur] = _b.get(cur, 0.0) + amt
    if not out_lines:
        # roc/dil are the views most often legitimately empty — zero
        # bytes at rc 0 was indistinguishable from a mis-typed window
        # (REVIEW #37); every sibling prints an explicit empty state.
        _out.put(f"(no {label} rows in this window)")
        return
    # numeric_right: quantities, per-share rates, and amounts line up
    # on their decimal points, same as the report tables, with a
    # single-space gap. The rows stay whitespace-tokenized taxtext —
    # extra padding parses identically when pasted into a .tt file.
    # No header: a row with one more field (a dividend's net amount
    # after withholding) keeps its columns instead of being merged
    # leftwards.
    page = _out.Doc(exact=True, width_=0, live=True)
    for aligned in _align_tx_lines(out_lines):
        page.line(aligned)

    # Per-currency totals footer. Only the categories actually present print,
    # so `dividends` shows just the dividend total, `buysell` the buy/sell
    # totals, and `transactions` all three.
    def _fmt(d):
        return ", ".join(f"{M.shown(v, 2):,.2f} {c}" for c, v in sorted(d.items()))
    footer = [(lbl, d) for lbl, d in
              (("TOTAL BUY:", buys), ("TOTAL SELL:", sells),
               ("TOTAL DIVIDEND:", divs),
               ("TOTAL DIVIDEND IN LIEU:", pils),
               (_CAPITALIZED_LBL, capped),
               ("TOTAL STAKING (crypto, ordinary income):", stake)) if d]
    if footer:
        page.blank()
        _w = max(15, max(len(lbl) for lbl, _d in footer))
        for lbl, d in footer:
            if prefix:
                _vprint(f"{lbl:<{_w}} {_fmt(d)}", hang=" " * (_w + 1))
            else:
                # One named account: re-importable taxtext, its bytes
                # never depend on the width (docs/output-style.md).
                page.line(f"{lbl:<{_w}} {_fmt(d)}")

    if skipped:
        _view_msg("note", f"skipped {skipped} row(s) with no taxtext "
                  f"representation (e.g. OPENING_BALANCE).")
    _warn_bad_dates(bad_dates)


def _run_tx_view(args: argparse.Namespace, actions, label: str,
                 symbol_filter=None) -> Optional[Dict[str, Any]]:
    """Shared engine for `transactions` / `dividends` / `buysell`: read the
    native (pre-base) per-account files, filter by look-back window and (if
    given) an action set, sort chronologically, print aligned taxtext.
    With --json it prints nothing and returns the document, which the
    command writes under its output's id (lib/json_contract)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, _scope, account = _view_window(args, root)

    if account:
        accounts = [account]
        if _native_tx_file(cache, account) is None:
            _die_input(f"no native transaction file for account "
                       f"{account!r} in work/ — run `tjs run` first, "
                       f"or check the name")
    else:
        accounts = _discover_tx_accounts(cache)
        if not accounts:
            _die("no transaction files in work/ — run `tjs run` first")
        # S045-09 twin (audit A2-1182): an account with inputs but no
        # books was simply absent from trades/divs/events/roc.
        # An account whose stage failed has no native book: say so, as
        # the -sum twins do (audit A2-0333).
        _warn_accounts_without_books(root, accounts, label,
                                     "transaction file")

    rows, bad_dates, _rules = _tx_view_rows(root, accounts, cache, actions, symbol_filter, keep,
                                            label)

    # Chronological, oldest → latest (date, then time), across all accounts.
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    # A crypto account's DIVIDEND rows are staking rewards — ordinary
    # income, their own total, never under TOTAL DIVIDEND (S023-11).
    _crypto_accts = {n for n, c in accounts_of(load_config(root, missing_ok=True)).items()
                     if c.crypto}

    # A payment in lieu's part in a cover's cost is in no total, as in `sum` (TJ-1066).
    _cap_share = _capitalized_pil_shares(root, {r[2] for r in rows if r[3].get("action") == "DIVIDEND_IN_LIEU"},
                                         is_usa(_rules.country))

    if getattr(args, "json", False):
        # Payments in lieu are their own total, as in `sum` and dil-sum
        # (one "dividend" bucket put them under TOTAL DIVIDEND, and the
        # PIL-only `dil` view labelled them dividends — S039-19, S041-07).
        # Added exactly, the rows' money as held: a float sum printed its
        # binary error (TJ-4930).
        jb: Dict[str, Dict[str, Decimal]] = {"buy": {}, "sell": {},
                                           "dividend": {},
                                           "dividend_in_lieu": {},
                                           "capitalized_263h": {},
                                           "staking": {}}
        for _d, _t, acct, tx in rows:
            cur = tx.get("currency") or "?"
            act = tx.get("action")
            if act in ("BUYSELL", "ASSIGN"):
                q = float(tx.get("quantity") or 0.0)
                amt = M.of(_trade_total(tx))
                bucket = "buy" if q > 0 else "sell" if q < 0 else None
                if bucket:
                    jb[bucket][cur] = jb[bucket].get(cur, Decimal(0)) + amt
            elif act in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                amt = M.of(float(tx.get("gross_amount") or 0.0)
                           or float(tx.get("net_amount") or 0.0))
                _k = ("dividend_in_lieu" if act != "DIVIDEND"
                      else "staking" if acct in _crypto_accts
                      else "dividend")
                _cap = amt * M.of(_cap_share.get((acct, str(tx.get("id"))), 0.0))
                if _cap:
                    amt -= _cap
                    jb["capitalized_263h"][cur] = (
                        jb["capitalized_263h"].get(cur, Decimal(0)) + _cap)
                jb[_k][cur] = jb[_k].get(cur, Decimal(0)) + amt
        return {"rows": [dict(tx, account=acct)
                         for _d, _t, acct, tx in rows],
                "totals": {k: {c: M.json_money(t, M.DERIVED_PLACES)
                               for c, t in v.items()}
                           for k, v in jb.items() if v},
                "bad_dates": bad_dates}

    _tx_view_text(accounts, root, rows, _crypto_accts, _cap_share, label, bad_dates)
    return None


def cmd_transactions(args: argparse.Namespace) -> None:      # `events` view
    doc = _run_tx_view(args, actions=None, label="events")
    if doc is not None:
        _json_out(doc, schema="events")


_BOOK_VALUE_DESC_RE = re.compile(
    r"\bBOOK\s+VALUE:?\s*\$?\s*(\d+(?:,\d+)*(?:\.\d+)?)", re.IGNORECASE)


def _transfer_rows(cache: Path, root: Path, cfg: Dict[str, Any],
                   want: Optional[str]) -> List[Dict[str, Any]]:
    """`tjs transfers`' rows: each transfer sidecar's rows (taxable
    accounts) and each account's in-book TRANSFERs but its lot moves
    (sheltered accounts with `transfers = true`), a missing or unreadable
    file warned; the named account's only, by date (cmd_transfers_view)."""
    def _fee(t: Dict[str, Any]) -> float:
        # A custody move's fee (a crypto withdrawal's network fee) is
        # in the sidecar; the view dropped it (S039-21).
        return float(t.get("fee") or 0) + float(t.get("commission") or 0)

    _COIN_FEE_RE = re.compile(r"\(fee ([0-9.eE+-]+) ([A-Za-z0-9.]+)\)")

    def _coin_fee(t: Dict[str, Any]) -> Tuple[float, str]:
        # A fee paid IN COINS (Kraken: fee_qty/fee_currency; never the
        # money `fee` field, S061-17), else the "(fee 0.05 ABC)" the
        # description of a sidecar from an older parse carries — the
        # FEE column was empty on every real withdrawal (A2-0663).
        try:
            q = float(t.get("fee_qty") or 0)
        except (TypeError, ValueError):
            q = 0.0
        if q:
            return q, str(t.get("fee_currency") or "")
        m = _COIN_FEE_RE.search(str(t.get("description") or ""))
        if m:
            try:
                return float(m.group(1)), m.group(2)
            except ValueError:
                pass
        return 0.0, ""

    def _kind(t: Dict[str, Any], broker: str = "") -> str:
        # The TYPE column: the transfer's kind, never the broker's free
        # text (a Questrade or RBC description names the security and
        # the other account's number: backlog TJ-0055). IB's
        # "<kind> (<symbol>)" gives its kind (ACATS, InterDepot,
        # Internal), a crypto row its own one- or two-word type (Send,
        # withdrawal); else the direction. A .tt JOURNAL line's legs
        # are a journal.
        from taxjson.lib.dated_events import SIDECAR_BROKER
        if broker == SIDECAR_BROKER:
            return "journal"
        d = ("" if broker in ("questrade", "rbc_direct") else
             _COIN_FEE_RE.sub("", str(t.get("description") or "")).strip())
        m = re.fullmatch(r"([A-Za-z][\w/-]*(?: [A-Za-z][\w/-]*)?) "
                         r"\([^()]+\)", d)
        if m:
            return m.group(1)
        if d and len(d.split()) <= 2 and not re.search(r"\d", d):
            return d
        try:
            q = float(t.get("quantity") or 0)
        except (TypeError, ValueError):
            q = 0.0
        return "transfer-in" if q > 0 else "transfer-out" if q < 0 else "-"

    rows: List[Dict[str, Any]] = []
    for p in sorted(cache.glob("*_transfers.json")):
        try:
            doc = _read_work_doc(p)
            if not isinstance(doc.get("metadata") or {}, dict):
                raise ValueError('"metadata" must be a JSON object')
        except (OSError, ValueError) as e:
            _view_msg("warning", f"could not read {p.name}: {e}",
                      "Its transfer rows are not shown.")
            continue
        if (doc.get("metadata") or {}).get("kind") != "transfer_sidecar":
            continue
        acct = (doc.get("metadata") or {}).get("account") or ""
        _broker = str((doc.get("metadata") or {}).get("brokerage") or "")
        for t in doc.get("transactions") or []:
            rows.append({"date": t.get("date") or "",
                         "account": t.get("account") or acct,
                         "symbol": t.get("symbol") or "",
                         "quantity": float(t.get("quantity") or 0),
                         "type": t.get("description") or "",
                         "_kind": _kind(t, _broker),
                         # RBC ships Value 0 on in-kind transfers; its
                         # "BOOK VALUE nnn" rides along as evidence.
                         "value": float(t.get("net_amount")
                                        or t.get("book_value") or 0),
                         "fee": _fee(t),
                         "fee_qty": _coin_fee(t)[0],
                         "fee_currency": _coin_fee(t)[1],
                         "currency": t.get("currency") or "",
                         "where": "sidecar"})
    _no_inputs = _accounts_skipped_for_no_inputs(root)
    from taxjson.lib.row_types import LOT_MOVE_TYPE
    # A move of your own is its two sidecar rows (IN_BOOKS own_move);
    # the books' lot-move legs of it (Canada: units only, CA-ACB-OWN-MOVE,
    # backlog TJ-0093; US: the lots carried, US-BASIS-05) are not more
    # transfers, in either country (backlog TJ-0276).
    for name in (cfg.get("accounts") or {}):
        p = cache / f"{name}_base.json"
        if not p.exists():
            # Missing says so like unreadable does (re-audit A2-1232).
            if name not in _no_inputs and _has_inputs(root, name):
                _view_msg("warning", f"no work/{p.name}: {name}'s "
                          f"in-book TRANSFER rows are not shown",
                          "Run `tjs run` to build it.")
            continue
        try:
            doc = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # Same warning as the sidecar loop: the account's in-book
            # rows vanished with no word (S039-22).
            _view_msg("warning", f"could not read {p.name}: {e}",
                      "Its in-book TRANSFER rows are not shown.")
            continue
        for t in (doc.get("transactions") if isinstance(doc, dict)
                  else doc) or []:
            if (t.get("action") != "TRANSFER"
                    or t.get("type") == LOT_MOVE_TYPE):
                continue
            # The book row lost the parser's book_value evidence (not a
            # transaction field); RBC's description still carries it
            # ("... BOOK VALUE 13579.24") — shown as the sidecar row is
            # (audit S027-06).
            _value = float(t.get("net_amount") or 0)
            if not _value:
                _bv = _BOOK_VALUE_DESC_RE.search(t.get("description") or "")
                if _bv:
                    from taxjson.lib.brokerages.base import desc_number
                    _value = desc_number(_bv.group(1), strict=False) or 0.0
            rows.append({"date": t.get("date") or "",
                         "account": t.get("account") or name,
                         "symbol": t.get("symbol") or "",
                         "quantity": float(t.get("quantity") or 0),
                         "type": t.get("description") or "",
                         "_kind": _kind(t),
                         "value": _value,
                         "fee": _fee(t),
                         "fee_qty": _coin_fee(t)[0],
                         "fee_currency": _coin_fee(t)[1],
                         "currency": t.get("currency") or "",
                         "where": "book"})
    if want:
        rows = [r for r in rows if r["account"] == want]
    rows.sort(key=lambda r: (r["date"], r["account"], r["symbol"]))
    return rows


def _transfer_arrivals(root: Path, cache: Path, cfg: Dict[str, Any],
                       rows: List[Dict[str, Any]]) -> None:
    """Each row's IN_BOOKS (`arrival`): an in-kind move's leg, or what the
    books did with a taxable account's transfer-in (your own move, book
    value, covered, partly covered, missing history, no cost; UNCLASSIFIED
    when work/ cannot be read, warned) (cmd_transfers_view)."""
    # What the books did with each transfer-IN of a taxable account
    # (lib/transfer_in): your own move (its out leg is yours too), booked
    # at the broker's stated book value, covered by your .tt purchase,
    # or NO COST — shares from outside the books with no cost.
    _status = {"book_value": "book value", "covered": ".tt covers",
               "missing_history": "missing history",
               # Audit 2026-10-24 B41: .tt purchases for part of the
               # units — nothing booked until the rest is declared.
               "partly_covered": "PARTLY covered",
               "no_cost": "NO COST"}
    # A transfer-in is your own move only on the pairing's word (its
    # identity in `_paired`); when the classification cannot be made
    # (a damaged work/ file), every one reads UNCLASSIFIED — it was "own
    # move", as if a transfer-out of yours had been matched (backlog
    # TJ-0308).
    _arr: Dict[Tuple[str, str, str], List[str]] = {}
    _paired: Set[Tuple[str, str, str, float]] = set()
    _paired_list: List[Any] = []
    _why_not = ""
    try:
        for a in transfer_arrivals(root, cache, accounts_of(cfg),
                                   paired=_paired_list):
            _arr.setdefault((a.account, a.symbol, a.date), []).append(
                # A stated book value of 0 is no cost, said as what it
                # is (audit 2026-10-24 B40).
                _status[a.status] + (" (0 stated)" if a.zero_book_value
                                     and a.status == "no_cost" else ""))
        _paired = set(_paired_list)
    except (OSError, ValueError) as e:
        _arr, _why_not = {}, str(e)
    except Exception as e:                          # noqa: BLE001
        _arr, _why_not = {}, f"{type(e).__name__}: {e}"
    if _why_not:
        _view_msg("warning", f"transfer-ins not classified: {_why_not}",
                  "Each taxable transfer-in reads UNCLASSIFIED: whether "
                  "it is a move of your own or shares from outside your "
                  "books is not known. Run `tjs run` to rebuild work/.")
    _tax_eq = {n for n, c in accounts_of(cfg).items()
               if c.taxable
               and not c.crypto and not c.transfers}
    # The legs of the in-kind moves to and from your registered
    # accounts (lib/in_kind, work/in_kind.json).
    _ik: Dict[Tuple[str, str, str, float], str] = {}
    try:
        import json as _json
        _ikdoc = _json.loads((cache / IN_KIND_FILE).read_text(
            encoding="utf-8"))
        for _m in _ikdoc.get("moves") or []:
            _lbl = (f"in-kind {_m.get('kind')}"
                    + ("" if _m.get("booked") else
                       " (ambiguous, NOT booked)"
                       if _m.get("problem") == "ambiguous" else
                       " (NOT booked)"))
            for _leg in ([_m.get("taxable_leg"), _m.get("registered_leg")]
                         + list(_m.get("registered_legs") or [])):
                if isinstance(_leg, list) and len(_leg) == 4:
                    _ik[(str(_leg[0]), str(_leg[1]), str(_leg[2]),
                         M.units(float(_leg[3]), 8))] = _lbl
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    for r in rows:
        r["arrival"] = "-"
        _lbl = _ik.get((r["account"], r["symbol"], r["date"][:10],
                        M.units(float(r["quantity"]), 8)))
        if _lbl:
            r["arrival"] = _lbl
        elif (r["where"] == "sidecar" and r["quantity"] > 0
                and r["account"] in _tax_eq):
            got = _arr.get((r["account"], r["symbol"], r["date"][:10]))
            r["arrival"] = (got.pop(0) if got else
                            "own move" if (r["account"], r["symbol"],
                                           r["date"][:10],
                                           M.units(float(r["quantity"]), 8))
                            in _paired else "UNCLASSIFIED")


def _transfers_text(codes: List[Dict[str, Any]], args: argparse.Namespace,
                    rows: List[Dict[str, Any]], want: Optional[str]) -> None:
    """`tjs transfers`' text: the table, the empty state, the action items,
    the symbol codes and, with --details, what each IN_BOOKS value means
    (cmd_transfers_view's tail)."""
    from taxjson.lib.out import Doc

    def _codes_section(doc) -> None:
        if not codes:
            return
        doc.section("SYMBOL CODES — Questrade internal codes")
        doc.para("Booked under the ticker inferred from your exports "
                 "(the account's own rows of the same description, "
                 "another account's transfer or name, or a currency "
                 "journal of the account; a ticker.map GLOBAL line for "
                 "the code "
                 "overrides it); an unresolved code stays its own "
                 "security until ticker.map maps it.")
        doc.items([f"{c['account']}: {c['code']} → {c['symbol']} "
                   f"({c['evidence']})" if c["symbol"] else
                   f"{c['account']}: {c['code']} → ticker.map rule"
                   if c["how"] == "ticker.map" else
                   f"{c['account']}: {c['code']} UNRESOLVED "
                   f"({c['evidence']})" for c in codes])
    _det = _details(args)
    _td = "tjs transfers --details"

    def _brief_codes(doc) -> None:
        # Essentials first: the codes, one line each kind (--details
        # lists them).
        unres = [c for c in codes if not c["symbol"]
                 and c["how"] != "ticker.map"]
        inferred = [c for c in codes if c["symbol"]]
        if unres:
            doc.item(*_act_item(f"{len(unres)} Questrade code(s) UNRESOLVED: "
                              f"map them in ticker.map", _td))
        if inferred:
            doc.para(f"{len(inferred)} Questrade code(s) booked under an "
                     f"inferred ticker — {_td}")

    doc = Doc("CUSTODY TRANSFERS — evidence, not tax events")
    if _det:
        doc.para("Basis comes from the buy and sell history. WHERE: "
                 "sidecar = kept out of the books; book = a sheltered "
                 "account's transfers, kept in.")
        doc.blank()
    if not rows:
        if not _det:
            doc.blank()
        doc.para("No transfer rows"
                 + (f" for account {want!r}" if want else "")
                 + " in the books.")
        (_codes_section if _det else _brief_codes)(doc)
        doc.print()
        return
    if not _det:
        # The legend, before the table (docs/output-style.md).
        doc.para("WHERE: sidecar = kept out of the books, book = kept in; "
                 "IN_BOOKS: how a transfer-in is booked.")
    body = []
    for r in rows:
        # Type column: the transfer KIND (InterDepot/Internal/ATON…,
        # else its direction: _kind) — capped; the --json view carries
        # the row's full text.
        _ty = (r["_kind"] or "-").replace(" ", "_")
        if len(_ty) > 24:
            _ty = _ty[:21] + "..."
        body.append([
            r["date"], r["account"], r["symbol"],
            fmt_qty(r["quantity"]), _ty,
            fmt_money(r["value"]),
            (f"{r['fee']:g}" if r["fee"]
             else (f"{r['fee_qty']:g}_{r['fee_currency']}".rstrip("_")
                   if r["fee_qty"] else "-")),
            r["currency"], r["where"], r["arrival"].replace(" ", "_")])
    # Too wide: the fee, the sidecar/book column, then the kind go first.
    doc.table(["DATE", "ACCOUNT", "SYMBOL", "QTY", "TYPE", "VALUE", "FEE",
               "CUR", "WHERE", "IN_BOOKS"], body, drop=(6, 8, 4),
              key=(0, 1, 2))
    doc.blank().para(f"{len(rows)} transfer row(s).")
    if not _det:
        # One line each, within 100 columns (docs/output-style.md; the
        # fix is in --details: backlog TJ-0043).
        from taxjson.lib.out import ACT as _ACT, act_list as _act_list
        _nc = [r for r in rows
               if r["arrival"].replace(" ", "_").startswith("NO_COST")]
        if _nc:
            doc.item(_act_list(
                f"{len(_nc)} transfer-in(s) with no cost",
                list(dict.fromkeys(r["symbol"] for r in _nc)),
                _td)[len(_ACT):], "", _ACT)
        _pc = [r for r in rows
               if r["arrival"].replace(" ", "_") == "PARTLY_covered"]
        if _pc:
            doc.item(_act_list(
                f"{len(_pc)} transfer-in(s) only partly covered, NOT "
                f"booked", list(dict.fromkeys(r["symbol"] for r in _pc)),
                _td)[len(_ACT):], "", _ACT)
        _uc = [r for r in rows if r["arrival"] == "UNCLASSIFIED"]
        if _uc:
            # Backlog TJ-0308: no pairing's word, so not "own move".
            doc.item(_act_list(
                f"{len(_uc)} transfer-in(s) UNCLASSIFIED",
                list(dict.fromkeys(r["symbol"] for r in _uc)),
                "tjs run")[len(_ACT):], "", _ACT)
        _brief_codes(doc)
        from taxjson.lib.out import details_hint as _dh
        doc.para(_dh(_td, "what each IN_BOOKS value means"))
        doc.print()
        return
    if any(r["arrival"] != "-" for r in rows):
        from taxjson.lib.transfer_in import OWN_MOVE_DAYS, SLOW_MOVE_DAYS
        doc.section("IN_BOOKS — a taxable account's transfer-in")
        doc.items([
            # Audit 2026-10-25 diff-engine#4: a slower move of your own
            # cancels too when nothing else could pair with either leg.
            f"own_move: your own shares moving (a transfer-out of yours "
            f"cancels it: within {OWN_MOVE_DAYS} days, or up to "
            f"{SLOW_MOVE_DAYS} days before it when no other transfer "
            f"could be either leg's partner). Not one move: a .tt "
            f"NOTOWN line in the receiving account's folder undoes the "
            f"pairing (docs/settings.md).",
            "book_value: from outside your books, booked at the book "
            "value the broker states on the row.",
            ".tt_covers: your .tt purchase covers it.",
            "PARTLY_covered: your .tt purchases account for some of its "
            "units only — NOT booked (the covered units would be held "
            "twice): add the other units' purchase to a .tt file, or all "
            "the units' when those purchases are another holding.",
            "missing_history: a .tt OPENING cost=unknown line opens it (cost "
            "unknown, reported by hand).",
            "NO_COST: from outside your books with no cost — add the "
            "original purchase to a .tt file (docs/getting-started.md, "
            "step 5c). `(0_stated)`: the row states a book value of 0, "
            "usually the broker's placeholder for a cost it was not "
            "given; never booked as a $0 lot.",
            "in-kind_contribution / in-kind_withdrawal: a move between a "
            "taxable and a registered account, booked in the taxable "
            "account as a sale / purchase at fair market value (`tjs "
            "run` lists each).",
            "UNCLASSIFIED: neither paired with a transfer-out of yours nor "
            "read as an arrival — work/ could not be read (the warning "
            "says which file): run `tjs run`."])
    _codes_section(doc)
    doc.print()


def cmd_transfers_view(args: argparse.Namespace) -> None:
    """`tjs transfers`: the custody-evidence view. TRANSFER rows
    are deliberately NOT tax events — a taxable book's basis comes from
    its buy/sell history, so the parse stage keeps them OUT of the
    books (in a sidecar) and `events` stays buysell-only. But a depot
    flip, listing journal, broker migration, or crypto withdrawal/send
    is exactly what explains a confusing position — or an undeclared
    FMV disposition — later; this view shows that evidence: sidecar
    rows (taxable accounts) plus in-book TRANSFERs (sheltered accounts
    with `transfers = true`)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    want = (args.account or "").strip() or None
    cfg = load_config(root, missing_ok=True)
    if not cfg:
        # With no config the sheltered accounts' in-book rows were left
        # out and the view said "the broker reported none" at rc 0
        # (re-audit A2-0717); the other views refuse.
        _die("no taxjson.toml in the project folder",
             "The accounts (and their in-book TRANSFER rows) are unknown; "
             "run from the project root.")
    if want and cfg and want not in (cfg.get("accounts") or {}):
        # A typo read "no transfer rows ... the broker reported none"
        # with rc 0, as if the account existed (S039-20).
        _die_input(f"no [accounts.{want}] in taxjson.toml — check the "
                   f"name.")
    _require_books(root, also=("*_transfers.json",))

    rows = _transfer_rows(cache, root, cfg, want)
    _transfer_arrivals(root, cache, cfg, rows)
    # Questrade internal codes the run booked under a ticker it inferred
    # from the other exports (lib/symbol_codes), and those it could not.
    from taxjson.lib.symbol_codes import SUFFIX as _SC_SUFFIX, read_state
    codes: List[Dict[str, Any]] = []
    for name in sorted(cfg.get("accounts") or {}):
        if want and name != want:
            continue
        st = read_state(cache / f"{name}{_SC_SUFFIX}")
        for code, r in sorted((st.get("resolved") or {}).items()):
            codes.append({"account": name, "code": code,
                          "symbol": r.get("symbol") or "",
                          "how": r.get("how") or "",
                          "evidence": r.get("evidence") or ""})
        for code, r in sorted((st.get("unresolved") or {}).items()):
            codes.append({"account": name, "code": code, "symbol": "",
                          "how": "unresolved",
                          "evidence": r.get("detail") or
                          "no transfer or name in the project identifies "
                          "it"})
        # A code a ticker.map rule names: booked by that rule, never
        # inferred (lib/symbol_codes).
        for code, r in sorted((st.get("mapped") or {}).items()):
            codes.append({"account": name, "code": code, "symbol": "",
                          "how": "ticker.map",
                          "evidence": "a ticker.map rule names it; booked "
                                      "as that rule says"})
    if getattr(args, "json", False):
        # (--json unchanged: `type` is the row's full text)
        _json_out({"transfers": [{k: v for k, v in r.items()
                                  if not k.startswith("_")} for r in rows],
                   "count": len(rows), "symbol_codes": codes}, schema="transfers")
        return
    _transfers_text(codes, args, rows, want)


def cmd_dividends(args: argparse.Namespace) -> None:         # `divs` view
    doc = _run_tx_view(args, actions={"DIVIDEND", "DIVIDEND_IN_LIEU"}, label="divs")
    if doc is not None:
        _json_out(doc, schema="divs")


def cmd_dil(args: argparse.Namespace) -> None:               # `dil` view
    doc = _run_tx_view(args, actions={"DIVIDEND_IN_LIEU"}, label="dil")
    if doc is not None:
        _json_out(doc, schema="dil")


def cmd_buysell(args: argparse.Namespace) -> None:           # `trades` view
    doc = _run_tx_view(args, actions={"BUYSELL", "ASSIGN"}, label="trades",
                       symbol_filter=_instrument_filter(args))
    if doc is not None:
        _json_out(doc, schema="trades")


def cmd_roc(args: argparse.Namespace) -> None:               # `roc` view
    doc = _run_tx_view(args, actions={"ADJUST"}, label="roc")
    if doc is not None:
        _json_out(doc, schema="roc")


def cmd_leaps(args: argparse.Namespace) -> None:             # `leaps` view
    """Closed LEAPS positions over the window: one row per engine
    disposition of a qualifying contract (long option buy placed more
    than [settings] leaps_months to expiry), with lot-matched
    base-currency gains."""
    root = Path(args.dir).resolve()
    keep, scope, account = _view_window(args, root)
    _files = _leaps_scope_guard(root, account, args)
    leaps = _leaps_contracts(root, account, "leaps")
    if not leaps:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account), schema="leaps")
            return
        _out.put(f"No LEAPS contracts found ({_leaps_rule(root)}).")
        return
    entries, found, basis = _leaps_closed(root, account, leaps, keep,
                                          _files)
    if not found:
        _die_input("no gains files in work/ — run `tjs run` first",
                   "Closed positions come from the gains engine.")
    if not entries:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account, basis), schema="leaps")
            return
        _out.put(f"No closed LEAPS positions in {scope}.")
        return
    base_cur = _base_currency(root)

    money = fmt_money               # shared report-layer formatter

    entries.sort(key=lambda ae: (ae[1].get("date") or "",
                                 ae[1].get("symbol") or ""))
    views = [(a, GainRow(e)) for a, e in entries]
    _split = _scope_split(root, [(a, v.gain) for a, v in views])
    if getattr(args, "json", False):
        _json_out({"rows": [dict(v.fields, account=acct) for acct, v in views],
                   "total_gain": M.shown(sum(float(v.gain or 0)
                                             for _a, v in views), 2),
                   "taxable_gain": _split["taxable"],
                   "sheltered_gain": _split["sheltered"],
                   "currency": base_cur, "basis": basis}, schema="leaps")
        return
    out_lines = ["DATE CONTRACT QTY PROCEEDS COST GAIN DAYS_HELD"]
    total = 0.0
    for _acct, v in views:
        gain = float(v.gain or 0.0)
        total += gain
        out_lines.append(" ".join([
            v.get("date") or "?", str(v.get("symbol") or "?"),
            f"{abs(float(v.quantity or 0.0)):g}",
            money(float(v.proceeds or 0.0)),
            money(float(v.cost or 0.0)),
            money(gain), str(int(v.get("days_held") or 0))]))
    page = _out.Doc(exact=True, width_=0, live=True)
    _vprint(f"CLOSED LEAPS POSITIONS — {scope} ({base_cur}; "
            f"{_leaps_rule(root)})")
    page.blank()
    # Too wide (a long contract symbol): DAYS_HELD goes first.
    _print_report_table(out_lines, fit=True, drop=(6,), key=(0, 1))
    page.line(f"\nTOTAL REALIZED GAIN: {money(total)} {base_cur}")
    _print_scope_split(_split, base_cur, root)
    _vprint(f"Allowed amounts, lot-matched, basis: {basis}; a partial "
            f"close as realized; open contracts not shown.")


def cmd_fees(args: argparse.Namespace) -> None:
    """Fees incurred over a window (default: the tax year) — one row per
    fee-bearing transaction plus a per-currency total. `PERIOD` is 30d/6w/3m/1y/
    all; omit it for the tax year. (For the by-brokerage roll-up, see
    `fees-sum`.)"""
    rows, scope, bad, _keep = _collect_period_txs(args, "fees", actions=None)

    money = fmt_money               # shared report-layer formatter

    out_lines = ["ACCOUNT DATE SYMBOL ACTION FEE CUR"]
    import math as _math
    parts: Dict[str, List[float]] = {}
    entries = []
    n_small = 0
    for acct, tx in rows:
        if tx.get("action") == "FEE":
            # Standalone fee rows (e.g. IB monthly/market-data fees) carry
            # the amount in net_amount: POSITIVE = charged, the convention
            # every parser (IB, Questrade, RBC, generic) and fx-cash use
            # (a rebate is negative and nets out). The old flip showed
            # every charge as a rebate (R1-124, R1-54, R1-269).
            fee = float(tx.get("net_amount") or 0.0)
        else:
            fee = _tx_fee(tx)
        if abs(fee) < 1e-9:
            continue
        cur = tx.get("currency") or "?"
        # Every fee is in the total, unrounded; one under half a cent
        # is only not listed (it would print as 0.00). Skipping it
        # before the sum made three 0.004 commissions "no fees"
        # (backlog TJ-0328).
        parts.setdefault(cur, []).append(fee)
        if abs(fee) < 0.005:
            n_small += 1
            continue
        entries.append((tx.get("date") or "", acct, tx, fee, cur))
    totals = {c: _math.fsum(v) for c, v in parts.items()}
    entries.sort(key=lambda e: (e[0], e[1]))
    if getattr(args, "json", False):
        _warn_bad_dates(bad)
        _json_out({"rows": [{"account": acct, "date": date,
                             "symbol": tx.get("symbol"),
                             "action": tx.get("action"),
                             "fee": M.shown(fee, 2), "currency": cur}
                            for date, acct, tx, fee, cur in entries],
                   "totals": {c: M.shown(v, 2) + 0.0
                              for c, v in totals.items()},
                   # The fees under half a cent: in `totals`, not in
                   # `rows` (backlog TJ-0328).
                   "unlisted_small_fees": n_small,
                   "scope": scope}, schema="fees")
        return
    for date, acct, tx, fee, cur in entries:
        out_lines.append(" ".join([acct, date or "-",
                                    str(tx.get("symbol") or "-"),
                                    str(tx.get("action") or "-"),
                                    money(fee), cur]))
    _warn_bad_dates(bad)
    if not parts:
        _out.put(f"No fees incurred in {scope}.")
        return
    page = _out.Doc(exact=True, width_=0, live=True)
    _vprint(f"FEES — {scope}")
    _vprint("Trade commissions and fees, and the FEE rows; per-trade "
            "totals by broker: tjs fees-sum.")
    if entries:
        _print_report_table(out_lines, fit=True, drop=(3,), key=(0, 1, 2))
        page.blank()
    tot = ", ".join(f"{money(v)} {c}" for c, v in sorted(totals.items()))
    n = len(entries) + n_small
    unlisted = (f" ({n_small} under half a cent, not listed)"
                if n_small else "")
    page.line(f"{n} fee(s){unlisted}; TOTAL FEES: {tot}")


def cmd_staking(args: argparse.Namespace) -> None:
    """One reward per row, with commissions and the recorded income."""
    report = _staking_view(args, summary=False)
    if report is not None:
        _json_out(report, schema="staking")


def _gain_display_line(g: GainRow) -> str:
    """A realized-gain (disposition) row for `tjs gains` (its view):
    money columns (proceeds/cost/gain) to 2 decimals, quantity keeps
    significant digits. A short row's legs are shown the real-world way
    (_real_world_legs)."""
    money = fmt_money               # shared report-layer formatter

    def sig(x):
        s = f"{float(x or 0):.8f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-", "-0") else s

    days = g.get("days_held")
    _p, _c = _real_world_legs(g)
    return " ".join([
        g.get("date", ""), g.get("symbol", ""), sig(g.quantity),
        g.get("currency") or "?", money(_p),
        money(_c), money(g.gain),
        "" if days is None else str(days),
    ])


def cmd_gains(args: argparse.Namespace) -> None:
    """Realized gains in NATIVE terms (pre-TOBASE consolidation, pre-currency-
    to-base) — read from the pipeline's `<account>_raw_gains.json`."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, _scope, account = _view_window(args, root)
    suffix = "_raw_gains.json"

    # Income rows carry an action; dispositions are everything else. Excluding
    # the known income actions (rather than requiring action is None) keeps
    # working if the engine ever tags dispositions with an action.
    _INCOME = {"DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST", "FEE",
               "DISALLOW", "ADJUST"}
    sym_filter = _instrument_filter(args)

    if account:
        accounts = [account]
        if not (cache / f"{account}{suffix}").exists():
            if (cache / f"{account}_base.json").exists():
                _die(f"no native gains for account {account!r}",
                     "Crypto accounts have none, and an equity account's "
                     "native books are skipped after a cross-currency "
                     "rollover rename (see the run's 'raw holdings "
                     "skipped' line) — rerunning will not create them; "
                     "its converted gains are in `tjs sum`.")
            _die_input(f"no native gains for account {account!r} in work/",
                       "Crypto has none; else run `tjs run`, or check the "
                       "name.")
    else:
        accounts = sorted(p.name[: -len(suffix)]
                          for p in cache.glob(f"*{suffix}")
                          if not p.name.startswith("."))
        # A configured equity account with converted books but no native
        # gains had its raw stage skipped (a cross-currency rollover
        # rename): say so, or the view silently omits the whole account
        # while `sum` counts it (2026-09 audit S037-23).
        _noted = []
        for _n, _c in sorted(accounts_of(load_config(root, missing_ok=True)).items()):
            if (_c.crypto
                    and _n not in accounts
                    and (cache / f"{_n}_base.json").exists()):
                _noted.append(_n)
                # The all-accounts view left the crypto account out
                # with no word while `sum` counted it (R1-274).
                _view_msg("note", f"crypto account {_n!r} is not shown "
                          f"(crypto has no native-currency gains)",
                          "Its gains are in `tjs sum` / `tjs "
                          "winners`.")
            elif (not _c.crypto
                    and _n not in accounts
                    and (cache / f"{_n}_base.json").exists()):
                _noted.append(_n)
                _view_msg("note", f"account {_n!r} has no native gains",
                          "The last run skipped its native books (see its "
                          "'raw holdings skipped' line); its converted "
                          "gains are in `tjs sum` / `tjs "
                          "winners`.")
        if not accounts and not _noted:
            _die_input("no native gains files in work/ — run `tjs run` "
                       "first")
        # S045-09 twin (audit A2-1182): accounts with books are named by
        # the notes above; one with inputs but no books at all is not.
        _warn_accounts_without_books(
            root, set(accounts) | {
                n for n in (load_config(root, missing_ok=True).get("accounts") or {})
                if (cache / f"{n}_base.json").exists()},
            "gains", "books")
        if not accounts:
            # Every account with books was named above (crypto only, or
            # native books skipped): nothing to show, and no run to ask
            # for — a run cannot make them (backlog TJ-0130).
            return

    rows = []
    bad_dates = 0
    manual = 0
    for acct in accounts:
        f = cache / f"{acct}{suffix}"
        if not f.exists():
            _view_msg("note", f"no native gains for account {acct!r} "
                      f"(crypto has only base-converted gains); skipping "
                      f"it.")
            continue
        data = _load_json_or_die(f)
        _settle = _settle_basis(root, data)
        # Missing-history sales (bought before the data) are routed out of
        # the gains, as in `sum`: count them so the view says so (audit
        # A2-0305).
        for g in data.get("manual_reporting_required") or []:
            if sym_filter is not None and not sym_filter(g.get("symbol") or ""):
                continue
            d = _gains_row_date(g, keep, _settle)
            if _ISO_DATE_RE.match(d) and keep(d):
                manual += 1
        for g in data.get("transactions", []):
            if g.get("action") in _INCOME:
                continue
            if sym_filter is not None and not sym_filter(g.get("symbol") or ""):
                continue
            d = _gains_row_date(g, keep, _settle)
            if not _ISO_DATE_RE.match(d):
                bad_dates += 1
                continue
            if keep(d):
                rows.append((d, acct, g))

    if bad_dates:
        _view_msg("warning", f"{bad_dates} gain row(s) had a "
                  f"missing/unparseable date and were excluded.")
    if manual:
        _view_msg("note", f"{manual} sale(s) with unknown cost are not shown",
                  "Shares bought before the data (.tt OPENING cost=unknown): "
                  "their basis is unknown; `tjs form-export` lists "
                  "them for manual reporting.")
    rows.sort(key=lambda r: (r[0], r[1]))
    if getattr(args, "json", False):
        jt: Dict[str, Decimal] = {}         # added exactly (TJ-4930)
        for _d, _acct, g in rows:
            cur = g.get("currency") or "?"
            jt[cur] = jt.get(cur, Decimal(0)) + M.of(GainRow(g).gain or 0.0)
        _json_out({"rows": [dict(g, account=acct) for _d, acct, g in rows],
                   "totals": {c: M.json_money(t, M.GAINS_PLACES)
                              for c, t in jt.items()}}, schema="gains")
        return
    if not rows:
        _out.put("(no realized gains in this window)")
        return
    prefix = len(accounts) != 1

    _vprint("REALIZED GAINS — native currency, per account, pre-wash")
    _vprint("Before the base-currency conversion; the filing figures: "
            "tjs sum.")
    header = (["ACCT"] if prefix else []) + \
        ["DATE", "SYMBOL", "QTY", "CUR", "PROCEEDS", "COST", "GAIN", "DAYS"]
    out_lines = [" ".join(header)]
    totals: Dict[str, float] = {}
    for _d, acct, g in rows:
        v = GainRow(g)
        line = _gain_display_line(v)
        out_lines.append(f"{acct} {line}" if prefix else line)
        cur = g.get("currency") or "?"
        totals[cur] = totals.get(cur, 0.0) + float(v.gain or 0.0)

    # Too wide: DAYS, then the proceeds and the cost go first.
    _o = 1 if prefix else 0
    _print_report_table(out_lines, fit=True, drop=(7 + _o, 4 + _o, 5 + _o),
                        key=(0, 1, 2) if prefix else (0, 1))
    _out.put("")
    _vprint("TOTAL GAIN: " + ", ".join(
        f"{M.shown(v, 2):,.2f} {c}" for c, v in sorted(totals.items())), hang="  ")


def cmd_cash(args: argparse.Namespace) -> None:
    """`tjs cash`: the cash events of the taxable accounts over a
    window — the conversions, deposits and withdrawals the exports carry
    and the project's .tt cash lines, the rows the FX-on-cash ledger v2
    reads — each as the .tt line that declares it (lib/cash_events
    tt_line: FXCONV, CASHMOVE, CASHOPEN; a fee as a `#FEE` comment
    line), oldest to latest, with per-currency totals. A broker's move
    that no .tt line declares shows `?` where its own / kept / spot /
    cost= / proceeds= goes (and its `row=` where a line without one would
    fit a Kraken Hybrid Earn row alike); one in the base currency needs none (a
    `#CASHMOVE` comment line); a stablecoin send decided in the account's
    sends.json shows that decision (`spot` for a gift or a payment, `kept`
    for `self`). A .tt CASHMOVE line written for a Kraken Hybrid Earn row
    is left out and said in a Warning, as the ledger refuses it (lib/
    cash_events `parked_lines`). Statement balances are not listed (`tjs
    fx-cash --ledger v2` reconciles them). `[PERIOD] [ACCOUNT]` as
    `tjs events`."""
    from taxjson.lib import cash_events as CE
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, scope, account = _view_window(args, root)
    cfg = load_config(root, missing_ok=True)
    accounts = accounts_of(cfg)
    taxable = sorted(n for n, c in accounts.items()
                     if c.taxable)
    if account:
        if account not in accounts:
            _die(f"no account {account!r} in taxjson.toml",
                 f"Accounts: {', '.join(sorted(accounts)) or 'none'}.")
        if account not in taxable:
            _die(f"account {account!r} is not a taxable account",
                 "Cash events are read for the taxable accounts "
                 "(s.39(1.1) / §988 reach those alone): "
                 f"{', '.join(taxable) or 'none'}.")
    _refuse_cash_older_than_inputs(root, cfg)
    txs, _base, _year, country, _fx = _fx_cash_native(root, cache,
                                                       prog="tjs cash")
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for t in txs:
        by_label.setdefault(str(t.get("account")), []).append(t)
    try:
        cash = CE.collect(root, cfg, country, by_label, work=cache)
    except CE.CashIdConflict as e:
        _die_cash_ids(e.conflicts)
    except (OSError, ValueError) as e:
        _die(str(e))
    CE.apply_declarations(cash["events"], cash["lines"], _base)
    # What the ledger refuses is not listed or counted here either
    # (backlog TJ-4951, TJ-4953): a .tt CASHMOVE line written for a Kraken
    # Hybrid Earn row (cash that never moved), one that fits such a row
    # and a cash move alike, or whose row= names none, said instead
    # (lib/cash_events `refused_lines`); a stablecoin
    # send a line and its sends.json decision declare two ways, said too
    # (the move itself is listed, as the line declares it).
    turned = [(kind, lns, text) for kind, lns, text in CE.refused_lines(cash["lines"], _base)
              if keep(lns[0]["date"]) and (not account or lns[0].get("label") == account)]
    refused = [ln for _kind, lns, _text in turned for ln in lns]
    said = [(f"{text}: not listed or counted here, and `tjs fx-cash --ledger v2` refuses "
             f"it — {CE.parked_fix(lns[0])}" if kind == "parked" else
             f"{text} (not listed or counted here; `tjs fx-cash --ledger v2` gives no "
             f"figure until then)") for kind, lns, text in turned] + [
        CE.send_conflict_text(e) for e in cash["events"]
        if e.get("send_conflict") and keep(e["date"])
        and (not account or e.get("label") == account)]
    for p in list(cash["problems"]) + list(cash.get("warnings") or []) + said:
        _view_msg("warning", p)
    evs = [e for e in list(cash["events"])
           + [x for x in cash["lines"] if not x.get("matched")
              and not any(x is ln for ln in refused)]
           if e.get("date") and keep(e["date"])
           and (not account or e.get("label") == account)]
    evs.sort(key=lambda e: (e["date"], e.get("label") or "", e["kind"]))
    # Each line with the at= its copy needs to stay in its book (backlog
    # TJ-0889).
    rows = [(e, ln) for e, ln in
            ((e, CE.tt_line(e, _base, at=CE.at_token(e, cash["books"])))
             for e in evs)
            if ln]

    # Per-currency totals: deposits (+) and withdrawals (-), the
    # conversions per currency pair, the fees paid, and the other cash
    # a cash event brought in (a stock-lending receipt, a rebate: a
    # FLOW received, never a fee; backlog TJ-0693).
    dep: Dict[str, float] = {}
    wd: Dict[str, float] = {}
    conv: Dict[str, float] = {}
    fees: Dict[str, float] = {}
    credits: Dict[str, float] = {}
    for e, _ln in rows:
        k = e["kind"]
        if k == "CASHMOVE":
            d = dep if e["amount"] > 0 else wd
            d[e["currency"]] = d.get(e["currency"], 0.0) + abs(e["amount"])
        elif k == "FXCONV":
            pair = f"{e['from_ccy']}->{e['to_ccy']}"
            conv[pair] = conv.get(pair, 0.0) + float(e["from_amt"])
        elif k == "FLOW" and e.get("leg") != "trade":
            d = credits if float(e["amount"]) > 0 else fees
            d[e["currency"]] = d.get(e["currency"], 0.0) + abs(e["amount"])
    if getattr(args, "json", False):
        _json_out({"scope": scope, "account": account,
                   "rows": [dict({k: v for k, v in e.items()
                                  if not k.startswith("_")}, tt=ln)
                            for e, ln in rows],
                   "totals": {"deposits": dep, "withdrawals": wd,
                              "converted": conv, "fees": fees,
                              "credits": credits},
                   "problems": list(cash["problems"]) + said}, schema="cash")
        return
    if not rows:
        _out.put("(no cash events in this window)")
        return
    # One account prints the lines as they are; the merged view prefixes
    # each with its account, as `tjs events` does. The keyword, date,
    # currency and amount line up in columns (the amounts on their
    # decimal points); what follows (a conversion's other side, a move's
    # declaration) is as written, and the comments line up after the
    # longest line.
    prefix = not account and len(taxable) != 1
    head_n = 5 if prefix else 4
    heads, tails = [], []
    for e, ln in rows:
        parts = (f"{e['label']} {ln}" if prefix else ln).split()
        heads.append(" ".join(parts[:head_n]))
        tails.append(" ".join(parts[head_n:]))
    bodies = [f"{h} {t}" if t else h for h, t in
              zip(_align_columns(heads, numeric_right=True, header=False),
                  tails)]
    comments = [CE.tt_comment(e) for e, _ln in rows]
    width = max(len(b) for b in bodies)
    page = _out.Doc(exact=True, width_=0, live=True)
    for body, c in zip(bodies, comments):
        page.line(f"{body:<{width}}  # {c}" if c else body)

    def _fmt(d: Dict[str, float]) -> str:
        return ", ".join(f"{M.shown(v, 2):,.2f} {c}" for c, v in sorted(d.items()))
    footer = [(lbl, d) for lbl, d in
              (("TOTAL DEPOSITS:", dep), ("TOTAL WITHDRAWALS:", wd),
               ("TOTAL CONVERTED:", conv), ("TOTAL FEES:", fees),
               ("TOTAL OTHER CREDITS:", credits)) if d]
    if footer:
        page.blank()
        _w = max(len(lbl) for lbl, _d in footer)
        for lbl, d in footer:
            if prefix:
                _vprint(f"{lbl:<{_w}} {_fmt(d)}", hang=" " * (_w + 1))
            else:
                page.line(f"{lbl:<{_w}} {_fmt(d)}")


def _register_cash(sub: argparse._SubParsersAction) -> None:
    p_cash = sub.add_parser(
        "cash",
        help="Cash deposits, withdrawals and conversions",
        description="The cash events of the taxable accounts over a "
                    "look-back window (default: the tax year): the "
                    "conversions, deposits and withdrawals the exports "
                    "carry and the project's .tt cash lines — what "
                    "`tjs fx-cash --ledger v2` reads — each as the "
                    ".tt line that declares it (FXCONV, CASHMOVE, "
                    "CASHOPEN; a fee as a #FEE comment line), oldest to "
                    "latest, with per-currency totals. A broker's move no "
                    ".tt line declares shows `?` where own / kept / spot / "
                    "cost= / proceeds= goes. Statement balances are not "
                    "listed.")
    p_cash.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_cash.add_argument("account", nargs="?",
                        help="A taxable account (default: all, merged, "
                             "each line prefixed with its account)")
    p_cash.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_cash.set_defaults(func=cmd_cash)


def _register_events(sub: argparse._SubParsersAction) -> None:
    p_tx = sub.add_parser(
        "events",
        help="Every transaction over a window, oldest first",
        description="Print the transactions over a look-back window "
                    "(default: the tax year) in the .tt text format, in "
                    "their own currency (before conversion to the base "
                    "currency), oldest to latest.")
    p_tx.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_tx.add_argument("account", nargs="?",
                      help="Account (default: all accounts, merged)")
    p_tx.add_argument("--json", action="store_true",
                     help="Emit JSON instead of text")
    p_tx.set_defaults(func=cmd_transactions)


def _register_divs(sub: argparse._SubParsersAction) -> None:
    p_div = sub.add_parser(
        "divs",
        help="Dividend rows over a window",
        description="Like `events`, but only DIVIDEND and "
                    "DIVIDEND_IN_LIEU rows (in their own currency, .tt "
                    "text format).")
    p_div.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_div.add_argument("account", nargs="?", help="Account (default: all)")
    p_div.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_div.set_defaults(func=cmd_dividends)


def _register_transfers(sub: argparse._SubParsersAction) -> None:
    p_xfer = sub.add_parser(
        "transfers",
        help="Custody transfers the books leave out",
        description="Custody-transfer EVIDENCE view: depot flips, listing "
             "journals, broker migrations, and crypto "
             "withdrawals/sends (matched pairs read as self-custody "
             "moves; unmatched out-legs are gift/payment candidates "
             "— dispositions at fair value if they left your "
             "ownership; in a US project only a payment is a sale). "
             "These are the TRANSFER rows the books deliberately "
             "exclude (cost comes from the buy and sell history): "
             "sidecar rows from taxable parses plus in-book rows from "
             "sheltered accounts.")
    p_xfer.add_argument("account", nargs="?",
                        help="Account (default: all accounts)")
    p_xfer.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_xfer.set_defaults(func=cmd_transfers_view)


def _register_dil(sub: argparse._SubParsersAction) -> None:
    p_dil = sub.add_parser(
        "dil",
        help="Payment-in-lieu rows over a window",
        description="Like `events`, but only DIVIDEND_IN_LIEU rows — "
                    "payments in lieu of a dividend, received while your "
                    "shares were lent out or short over the ex-date (in "
                    "their own currency, .tt text format).")
    p_dil.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_dil.add_argument("account", nargs="?", help="Account (default: all)")
    p_dil.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_dil.set_defaults(func=cmd_dil)


def _register_roc(sub: argparse._SubParsersAction) -> None:
    p_roc = sub.add_parser(
        "roc",
        help="Return-of-capital and other cost-adjustment rows",
        description="Like `events`, but only ADJUST rows — "
                    "return-of-capital ACB / basis reductions (as the "
                    "broker classified them) and manual .tt "
                    "adjustments.")
    p_roc.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_roc.add_argument("account", nargs="?", help="Account (default: all)")
    p_roc.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_roc.set_defaults(func=cmd_roc)


def _register_leaps(sub: argparse._SubParsersAction) -> None:
    p_leaps = sub.add_parser(
        "leaps",
        help="Closed LEAPS positions over a window",
        description="Closed LEAPS positions over a window (default: the "
                    "tax year) — the engine's dispositions of long "
                    "option buys placed more than [settings] "
                    "leaps_months (default 9) months to expiry, "
                    "with lot-matched base-currency gains.")
    p_leaps.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_leaps.add_argument("account", nargs="?", help="Account (default: all)")
    p_leaps.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_leaps.set_defaults(func=cmd_leaps)


def _register_trades(sub: argparse._SubParsersAction) -> None:
    p_bs = sub.add_parser(
        "trades",
        help="Buy, sell and assignment rows over a window",
        description="Like `events`, but only BUYSELL and ASSIGN rows (in "
                    "their own currency, .tt text format).")
    p_bs.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_bs.add_argument("account", nargs="?", help="Account (default: all)")
    _add_instrument_filters(p_bs)
    p_bs.add_argument("--json", action="store_true",
                     help="Emit JSON instead of text")
    p_bs.set_defaults(func=cmd_buysell)


def _register_gains(sub: argparse._SubParsersAction) -> None:
    p_g = sub.add_parser(
        "gains",
        help="Realized gains in the trade's own currency",
        description="Realized gains in NATIVE terms: before ticker.map's "
                    "TOBASE consolidation and before conversion to the "
                    "base currency, over a window (default: the tax "
                    "year).")
    p_g.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_g.add_argument("account", nargs="?", help="Account (default: all)")
    _add_instrument_filters(p_g)
    p_g.add_argument("--json", action="store_true",
                    help="Emit JSON instead of text")
    p_g.set_defaults(func=cmd_gains)


def _register_staking(sub: argparse._SubParsersAction) -> None:
    _add_staking_parser(
        sub, "staking", cmd_staking,
        "Rewards, commissions and net income per event")


def _register_fees(sub: argparse._SubParsersAction) -> None:
    p_fees = sub.add_parser(
        "fees",
        help="Fees paid per trade over a window",
        description="Fees incurred over a window (default: the tax "
             "year), one row per fee-bearing trade plus a per-currency "
             "total.")
    p_fees.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_fees.add_argument("account", nargs="?", help="Account (default: all)")
    p_fees.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_fees.set_defaults(func=cmd_fees)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_cash(sub)
    _register_events(sub)
    _register_divs(sub)
    _register_transfers(sub)
    _register_dil(sub)
    _register_roc(sub)
    _register_leaps(sub)
    _register_trades(sub)
    _register_gains(sub)
    _register_staking(sub)
    _register_fees(sub)
