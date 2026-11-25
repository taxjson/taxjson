#!/usr/bin/env python3
"""Apply non-cash fund distributions from taxjson.toml's [[distributions]].

Canadian ETFs routinely declare REINVESTED (non-cash) capital-gains
distributions — usually each December — that never appear in any broker
CSV yet increase the holder's ACB. Missing them silently overstates the
gain at sale. Some funds likewise report annual return-of-capital
factors only after year-end. This tool turns a small user-maintained
table into the ADJUST rows the engines already understand.

Entries (`[[distributions]]` tables in the project's taxjson.toml):

    [[distributions]]           # reinvested dist -> ACB up
    symbol = "ABC.TO"
    record_date = 2025-12-29
    per_share = 0.2500

    [[distributions]]           # return of capital -> ACB down
    symbol = "ZRE.TO"
    record_date = 2025-12-31
    per_share = -0.12

Per-share amounts are in the project BASE currency (the tool runs on the
already-converted base books). For each map row, the account's share
balance ON the record date is computed from its own book (BUYSELL /
ASSIGN / OPENING_BALANCE / TRANSFER rows, SPLIT-scaled, rename-aware via
symbol_new; keyed on the SETTLEMENT date by default — the holder of
record is the settled position — or the trade date with
`--date-basis trade`) and one ADJUST row is appended on the ticker that
holds the shares on the record date (a key naming a pre- or post-rename
ticker resolves along the SPLIT renames; symbols are case-insensitive;
with `--ticker-map` the key is first renamed like the book was):

    net_amount = balance * per_share      (positive = ACB increase)

with a deterministic id, so a rebuild regenerates rather than
accumulates. Rows for symbols the account doesn't hold on the date are
skipped with a NOTE. Only the ACB side is booked: a reinvested
distribution is also income of the record year (T3/T5), which the
user reports from the slip — the NOTE says so. The pipeline wires this in for taxable equity
accounts whenever taxjson.toml has [[distributions]]; run it manually as:

    taxjson-apply-distributions work/margin_base.json --config taxjson.toml

With `--incomplete-history <project folder>` (the pipeline passes it
when the project has one) the record-date balance includes the
missing-history openings the gains stage will synthesize, so a position
bought before the data gets the right share count.
"""
from taxjson.project.toml_io import parse_toml
from taxjson.lib.out import exit_text
from taxjson.lib.stage_msg import emit_line
import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from taxjson.lib.cli_diag import guard_main
from taxjson.lib import cli_diag
from taxjson.lib.country import country_arg
from taxjson.model import money as M
from taxjson.lib.unit_balance import DATE_BASES, balance_on


PROG = "taxjson-apply-distributions"


def load_rows(config_path: Path) -> List[Tuple[str, str, float]]:
    """[(SYMBOL, record date, per share)] of the [[distributions]]
    entries in the taxjson.toml at `config_path` — an entry that cannot
    be read is fatal: a typo here silently mis-adjusts ACB, so refuse
    rather than skip (lib/project_tables; every taxjson config reader
    refuses the same entries). A repeated SYMBOL+DATE is kept (two
    components of one distribution can be two entries) but warned
    about: a pasted entry or an appended correction ADDS to the first
    one."""
    from taxjson.lib.project_tables import distribution_rows
    try:
        cfg = parse_toml(cli_diag.read_text_utf8(config_path))
    except (ValueError, TypeError) as e:
        sys.exit(exit_text(f"{PROG}: {config_path} is not valid TOML: {e}"))
    rows, problems, warnings = distribution_rows(cfg)
    if problems:
        sys.exit(exit_text(f"{PROG}: {config_path}: " + "; ".join(problems)))
    for w in warnings:
        emit_line(f"{PROG}: warning: {config_path.name} {w}", file=sys.stderr)
    return rows


def moment_rank(t: dict) -> int:
    """Fixed places of a dict row among rows executed on the same date
    (CA-DATE-14 / US-DATE-13): an opening balance first, then a split
    (effective at the open, whatever its clock stamp), then everything
    else in the export's row order (the sort is stable). Without it a
    buy listed before a same-stamp split was doubled in one walk and not
    in the engine (audit A2-0013)."""
    a = t.get("action")
    return 0 if a == "OPENING_BALANCE" else 1 if a == "SPLIT" else 2


def resolve_live_symbol(transactions: List[dict], symbol: str,
                        date: str) -> str:
    """The ticker that holds `symbol`'s shares on `date`, following the
    book's SPLIT renames (symbol -> symbol_new) in BOTH directions: a
    key naming the pre-rename ticker after a rename resolves forward to
    the new ticker, and the current ticker keyed before its rename
    resolves back to the old one. The ADJUST must land on the pool live
    on the record date; stamped on the literal key after a rename it
    hit a dead pool and the ACB increase was lost while the console
    reported it applied (audit S025-10)."""
    renames = sorted(
        (t for t in transactions
         if t.get("action") == "SPLIT"
         and (t.get("symbol_new") or "").strip()
         and (t.get("symbol_new") or "").strip() != t.get("symbol")),
        key=lambda t: (str(t.get("date") or ""), str(t.get("time") or "")))
    cur, seen = symbol, {symbol}
    for t in renames:                       # forward: renamed on/before
        if str(t.get("date") or "") <= date and t.get("symbol") == cur:
            cur = t["symbol_new"].strip()
            if cur in seen:
                break
            seen.add(cur)
    for t in reversed(renames):             # backward: renamed after
        if str(t.get("date") or "") > date \
                and t["symbol_new"].strip() == cur:
            cur = t.get("symbol")
            if cur in seen - {symbol}:
                break
            seen.add(cur)
    return cur


def _symbol_trades(txs: List[dict], symbol: str, account: str
                   ) -> List[dict]:
    """The position-moving rows of `symbol` in `account` ('' = any)."""
    return [t for t in txs
            if t.get("symbol") == symbol
            and t.get("action") in ("BUYSELL", "ASSIGN", "TRANSFER")
            and (not account or not t.get("account")
                 or str(t.get("account")) == str(account))]


def _straddles(t: dict, record_date: str) -> bool:
    """A trade executed on or before the record date that settles after
    it: not the holder of record's."""
    return (str(t.get("date") or "") <= record_date
            < str(t.get("date_settle") or ""))


def _stamp_date(txs: List[dict], symbol: str, record_date: str,
                account: str) -> str:
    """The trade date to stamp a map ADJUST with. Sized on the settled
    position (the holder of record), it must also reach exactly those
    lots in a trade-date-ordered engine (the US, or tax_date = trade).
    A SALE executed on or before the record date that settles AFTER it
    is not the record holder's: its shares are still the holder's, but
    a trade-date walk has already closed their lot by the record date,
    so the ADJUST goes at the end of the day BEFORE the earliest such
    sale (audit A2-0071: a sale traded on the record date dropped the
    ADJUST as 'no open lots'). A straddling PURCHASE needs no
    backdating: the row carries the record date (`record_date`), and
    the US engine spreads it over the open lots settled by then only
    (engine/us/walk.py, the 'dist' ADJUST branch; A2-0988: a buy traded
    on the record date shared it). Backdating for a purchase put the
    whole amount on the lots open BEFORE it — a later lot settled by
    the record date got nothing (audit 2026-10-24 B14). The settle date
    stays the record date, so settle-ordered books are unchanged."""
    straddle = [str(t.get("date") or "")
                for t in _symbol_trades(txs, symbol, account)
                if _straddles(t, record_date)
                and float(t.get("quantity") or 0.0) < 0]
    if not straddle:
        return record_date
    first = _dt.date.fromisoformat(min(straddle))
    return (first - _dt.timedelta(days=1)).isoformat()


def _stamp_problem(txs: List[dict], symbol: str, record_date: str,
                   account: str, stamp: str) -> Optional[str]:
    """Why a backdated stamp (`_stamp_date`) cannot reach exactly the
    holder of record's lots in a per-lot, trade-date-ordered engine, or
    None: a trade of the symbol between the stamp and the record date
    that settles by the record date — a purchase (open only after the
    stamp: it gets no share) or a sale (its lot is open at the stamp:
    it gets one). Said, never silently misplaced (audit 2026-10-24
    B14). A trade that straddles the record date is placed right
    either way: a sale's lot is the holder's and open at the stamp, a
    purchase is not the holder's and opens after it (backlog TJ-0021,
    TJ-0027: a straddling purchase was named as a miss)."""
    if stamp >= record_date:
        return None
    between = sorted(
        (str(t.get("date") or ""), float(t.get("quantity") or 0.0))
        for t in _symbol_trades(txs, symbol, account)
        if stamp < str(t.get("date") or "") <= record_date
        and not _straddles(t, record_date))
    if not between:
        return None
    rows = ", ".join(f"{'bought' if q > 0 else 'sold'} {abs(q):g} on {d}"
                     for d, q in between)
    return (f"dated {stamp} so that it reaches the shares of the sale "
            f"settling after the record date, it misses the lots of "
            f"the trade(s) in between ({rows}): a per-lot engine puts it "
            f"on the lots open on {stamp} — check those lots' basis, or "
            f"enter per-lot ADJUST rows by hand")


def _warn_roc_overlaps(txs: List[dict], symbol: str, key: str,
                       date: str, account: str, amount: float,
                       country: Optional[str]) -> None:
    """A map return of capital the book may already carry, or whose cash
    is still counted as income:

    - the broker's own ROC (an ADJUST of type 'roc', or a .tt ADJUST)
      dated on the map date — its pay date or its printed record date:
      both lower the cost, so the ACB is cut twice (audit A2-0072; the
      R1-163 check compared raw dates only, and only in roc-sum);
    - a DIVIDEND row of the same distribution (paid within 60 days of
      the record date): the cash stays in dividend income while the map
      row lowers the cost, so the same dollars count twice (A2-0232)."""
    def _acct_ok(t: dict) -> bool:
        return (not account or not t.get("account")
                or str(t.get("account")) == str(account))
    for t in txs:
        if (t.get("action") != "ADJUST" or t.get("symbol") != symbol
                or str(t.get("id") or "").startswith("DIST-")
                or float(t.get("net_amount") or 0.0) >= 0
                or not _acct_ok(t)):
            continue
        dates = {str(t.get("date") or ""), str(t.get("record_date") or "")}
        if date in dates:
            emit_line(f"{PROG}: warning: [[distributions]] {key} {date}: the "
                      f"book already has a return-of-capital ADJUST of "
                      f"{M.shown(float(t.get('net_amount') or 0.0), 2):.2f} on {symbol} "
                      f"(dated {t.get('date')}"
                      + (f", record date {t.get('record_date')}"
                     if t.get("record_date") else "")
                      + ") — if both are the same distribution the cost is "
                      f"reduced TWICE. Delete the [[distributions]] entry "
                      f"(or the .tt ADJUST).", file=sys.stderr)
    try:
        lo = _dt.date.fromisoformat(date)
    except ValueError:
        return
    hi = (lo + _dt.timedelta(days=60)).isoformat()
    divs = [t for t in txs
            if t.get("action") == "DIVIDEND" and t.get("symbol") == symbol
            and _acct_ok(t) and date <= str(t.get("date") or "") <= hi]
    slip = ("the T3 box 42" if country != "usa"
            else "Form 1099-DIV box 3")
    if divs:
        d = min(divs, key=lambda t: str(t.get("date") or ""))
        emit_line(f"{PROG}: warning: [[distributions]] {key} {date}: the "
                  f"return of capital ({M.shown(amount, 2):+.2f}) lowers the cost, but the "
                  f"cash of the distribution paid {d.get('date')} is booked as "
                  f"a DIVIDEND row and still counted IN FULL as income by "
                  f"taxjson (divs-sum, the estimate). Report income from the "
                  f"slip ({slip} part is not income).", file=sys.stderr)


# Where the income of a reinvested distribution is reported, by the
# project's country (the tool books the cost side only).
_INCOME_SLIP = {"canada": "the T3/T5 slip",
                "usa": "Form 1099-DIV (box 2a for a capital-gain "
                       "distribution)",
                None: "your tax slip"}


# A return of capital beyond the cost, by country (CA-ACB-07 / US-ROC-02).
_ROC_EXCESS_LAW = {"canada": "ITA s.40(3)", "usa": "IRC §301(c)(3)"}


def _snapshot_after(txs: List[dict], symbol: str, date: str
                    ) -> Optional[str]:
    """The date of this book's opening snapshot (lib/opening) of
    `symbol` dated on or after `date`, if any: the cut-off left out the
    book's rows of `symbol` up to that day."""
    from taxjson.lib.row_types import is_opening_row
    days = [str(t.get("date") or "") for t in txs
            if is_opening_row(t) and t.get("symbol") == symbol
            and str(t.get("date") or "") >= date]
    return min(days) if days else None


def _snapshot_on(txs: List[dict], symbol: str, date: str) -> bool:
    """Whether this book has an opening snapshot (lib/opening) of
    `symbol` dated exactly `date`."""
    from taxjson.lib.row_types import is_opening_row
    return any(is_opening_row(t) and t.get("symbol") == symbol
               and str(t.get("date") or "")[:10] == date[:10] for t in txs)


def apply_distributions(doc: dict, map_rows, account: str,
                        date_basis: str = "settle",
                        phantoms=None, renames=None,
                        country: Optional[str] = None) -> Tuple[dict, int]:
    """`phantoms` — the (symbol, account) set of the .tt OPENING cost=unknown lines
    (the parameter keeps its pre-rename name). The record-date balance
    must include the missing-history openings the gains stage
    synthesizes (audit S000-08: sized on the book without them, a
    position bought before the data got half the ADJUST, or none).

    `renames` — the ticker.map renames the base book went through
    (GLOBAL + TOBASE + JOURNAL): a key naming the broker's listing is
    mapped the same way before the lookup (audit S025-22: a key the
    holdings report shows was skipped as "no shares held")."""
    txs = doc.get("transactions", [])
    # Regenerate, never accumulate: drop rows this tool added before.
    txs = [t for t in txs
           if not str(t.get("id") or "").startswith("DIST-")]
    # Sized on the book the gains run will walk (its missing-history
    # openings first), less a stock dividend row that books no shares:
    # decided on that book, checked against the run's answer after it
    # (pipeline/stages `_say_presize_disagreements`; TJ-5282).
    from taxjson.lib.stock_dividend_cover import presized
    sizing, _no_shares = presized(txs, phantoms, country, PROG)
    applied = 0
    used_ids: set = set()
    for key, date, per_share in map_rows:
        if per_share == 0:
            # A 0 is a placeholder (the fund has not published yet), not
            # a distribution: it used to be reported as an applied
            # "return of capital" and a $0 ADJUST that the checklist's
            # roc-entered step counted (audit S025-23).
            emit_line(f"NOTE: [[distributions]]: {key} {date} has per-share "
                      f"amount 0 — a placeholder, NOT applied. Enter the "
                      f"fund's declared amount once it is published.",
                      file=sys.stderr)
            continue
        sym = key
        if renames:
            from taxjson.lib.ticker_map_rules import map_symbol
            sym = map_symbol(sym, renames)
        live = resolve_live_symbol(sizing, sym, date)
        # A key that still holds shares under its own name on the record
        # date is that holding — a ticker reused after its rename (OLDTKR
        # bought again after OLDTKR -> NEWTKR) is not the renamed pool (audit
        # A2-0074).
        if live != sym and balance_on(sizing, sym, date,
                                      date_basis) > 1e-9:
            live = sym
        sym = live
        via = f" (as {sym})" if sym != key else ""
        bal = balance_on(sizing, sym, date, date_basis)
        if bal <= 1e-9:
            snap = _snapshot_after(txs, sym, date)
            if snap and per_share < 0:
                # The snapshot replaced the record date's rows (lib/
                # opening, CA-OPEN-03 / US-OPEN-03): its cost stands for
                # this return of capital, but a part beyond the cost is
                # a gain of its year the snapshot cannot carry (#77).
                emit_line(f"warning: ATTENTION: [[distributions]]: "
                          f"{key}{via} return of capital of {date} is "
                          f"dated before the opening snapshot of {snap} — "
                          f"the snapshot's cost stands for it, so it is "
                          f"not applied. A return of capital beyond the "
                          f"cost is a gain of {date[:4]} "
                          f"({_ROC_EXCESS_LAW.get(country, 'a capital gain')}"
                          f") the snapshot cannot show: if it went beyond, "
                          f"take the snapshot from a statement before "
                          f"{date}.", file=sys.stderr)
                continue
            emit_line(f"NOTE: [[distributions]]: no {key}{via} shares held "
                      f"on {date} in this book"
                      f"{f' (before its opening snapshot of {snap})' if snap else ''}"
                      f" — row skipped.",
                      file=sys.stderr)
            continue
        amount = M.units(bal * per_share, 6)
        if _snapshot_on(txs, sym, date):
            # Dated on the opening snapshot's own day (lib/opening,
            # CA-OPEN-03 / US-OPEN-03): the snapshot's quantity holds
            # the shares, but whether its book cost already carries
            # this distribution taxjson cannot tell — a year-end
            # statement's often does not yet. Applied, and said
            # (2026-10 audit #26: it was added silently).
            emit_line(f"NOTE: [[distributions]]: {key}{via} {date} is "
                      f"dated on the opening snapshot's own day — "
                      f"applied on top of the snapshot's cost; if that "
                      f"statement's book cost already includes it, "
                      f"remove the entry.", file=sys.stderr)
        # Neutral without a country: the row is saved into the book,
        # and "ACB" is Canada's word (A2-1234).
        from taxjson.lib.country import COST_TERM
        _cost = COST_TERM[country]
        kind = (f"reinvested distribution ({_cost} up)" if per_share > 0
                else f"return of capital ({_cost} down)")
        # Deterministic AND unique: a SYMBOL+DATE entered twice (two
        # components, or a pasted entry load_rows warned about) gives
        # each ADJUST its own id (audit S025-16).
        rid = f"DIST-{sym}-{date}-{account}"
        n = 2
        while rid in used_ids:
            rid = f"DIST-{sym}-{date}-{account}-{n}"
            n += 1
        used_ids.add(rid)
        adj_date = _stamp_date(txs, sym, date, account)
        txs.append({
            "action": "ADJUST",
            "date": adj_date, "time": "23:59:58", "date_settle": date,
            "symbol": sym, "quantity": 0.0,
            "currency": doc.get("metadata", {}).get("target_currency", ""),
            "net_amount": amount, "gross_amount": 0.0,
            "type": "dist",
            # The record date: the US engine spreads the row over the
            # open lots settled by it only (B14; the Canada pool needs
            # no lot).
            "record_date": date,
            "account": account,
            "id": rid,
            "description": f"{kind}: {bal:g} sh x {per_share:g}/sh "
                           f"per [[distributions]]",
        })
        from taxjson.lib.country import is_usa
        trouble = _stamp_problem(txs, sym, date, account, adj_date)
        # Only a per-lot engine (the US) misplaces it. Without --country
        # the tool cannot tell which engine reads the book, so it says
        # it: is_usa(None) raised and the documented call without a
        # country crashed (backlog TJ-0508).
        if trouble and (country is None or is_usa(country)):
            emit_line(f"warning: ATTENTION: [[distributions]] {key}{via} "
                      f"{date}: the basis adjustment is {trouble}.",
                      file=sys.stderr)
        # A reinvested distribution is income of the record year (T3
        # box 21/26/49, or a T5 stock dividend): the ACB rises only
        # because that amount is taxed. This tool books the ACB side
        # only — say so, or the estimate silently omits it (S026-00).
        income = (f" Report the {M.shown(amount, 2):.2f} itself as income from "
                  f"{_INCOME_SLIP[country]} — it is not counted as income "
                  f"by taxjson (estimate, divs-sum)." if per_share > 0
                  else "")
        if per_share < 0:
            _warn_roc_overlaps(txs, sym, key, date, account, amount,
                               country)
        ccy = doc.get("metadata", {}).get("target_currency", "")
        unit = f" {ccy}" if ccy else ""
        emit_line(f"NOTE: {key}{via} {date}: {kind} — {bal:g} sh x "
                  f"{per_share:g}{unit} = {M.shown(amount, 2):+.2f} "
                  f"{_cost} adjustment.{income}", file=sys.stderr)
        applied += 1
    doc["transactions"] = txs
    return doc, applied


@guard_main("taxjson-apply-distributions")
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Entries are [[distributions]] tables (symbol, record_date, "
               "per_share). The per-share amount is in the project BASE "
               "currency (the books this tool reads are already "
               "converted): convert a US-listed fund's published USD "
               "factor to the base currency first.")
    ap.add_argument("base_json", type=Path)
    ap.add_argument("--config", type=Path, required=True,
                    help="the project's taxjson.toml: its [[distributions]] "
                         "entries (per-share amounts in the project base "
                         "currency)")
    ap.add_argument("--account", default="")
    ap.add_argument("--date-basis", choices=DATE_BASES, default="settle",
                    help="Which date a trade moves the record-date "
                         "balance on: settle (the holder of record is "
                         "the settled position — a market fact in both "
                         "countries, and what `tjs run` uses) or "
                         "trade (a manual override).")
    ap.add_argument("--country", type=country_arg, default=None,
                    metavar="{canada,ca,usa,us}",
                    help="Only words the output: the book row's "
                         "description and the note (ACB and the T3/T5 "
                         "slip for canada, basis and Form 1099-DIV for "
                         "usa, neutral 'cost' without it); `tjs "
                         "run` passes it.")
    ap.add_argument("--ticker-map", type=Path, default=None,
                    metavar="TICKER_MAP",
                    help="ticker.map the base book went through: map "
                         "keys are renamed the same way (GLOBAL, TOBASE, "
                         "JOURNAL) before the lookup (`tjs run` "
                         "passes it).")
    ap.add_argument("--incomplete-history", type=Path, default=None,
                    metavar="DIR",
                    help="the project folder (its .tt OPENING cost=unknown "
                         "lines): size each record-date "
                         "balance with the missing-history openings the "
                         "gains stage will synthesize (`tjs run` "
                         "passes it).")
    args = ap.parse_args(argv)

    if args.ticker_map is not None and not args.ticker_map.exists():
        # A named input that does not exist is refused, not silently
        # skipped (re-audit A2-1436; reconcile-slips' twin refuses too).
        cli_diag.error(PROG, f"no such file: --ticker-map {args.ticker_map}")
        return 2
    if not args.config.is_file():
        cli_diag.error(PROG, f"no such config file: {args.config}")
        return 2
    from taxjson.lib.json_input import InputFileError, read_work_doc
    try:
        # A bare-array book is accepted, as load_transactions does
        # (audit S079-11); a row whose quantity is text is one line
        # naming the file and row (re-audit A2-0793).
        doc = read_work_doc(args.base_json)
    except InputFileError as e:
        cli_diag.error(PROG, f"could not read {e}")
        return 2

    book_accounts = sorted({str(t.get("account"))
                            for t in doc.get("transactions", [])
                            if t.get("account")})
    if args.account and book_accounts \
            and args.account not in book_accounts:
        # The ADJUST is booked on the label given: one no row carries
        # (a typo, a different case) put the ACB change in a pool of its
        # own — dropped by the US per-account basis (audit S025-12).
        cli_diag.error(PROG, f"--account {args.account!r} is not an "
                             f"account of {args.base_json.name} (its rows "
                             f"carry {', '.join(map(repr, book_accounts))})")
        return 2
    if len(book_accounts) > 1:
        # One book is one account's (`tjs run` passes each taxable
        # account's own): the record-date balance summed every account's
        # shares and put the whole ADJUST on one of them, and a rebuild
        # dropped the other accounts' rows (backlog TJ-0507).
        cli_diag.error(PROG, f"{args.base_json.name} holds the rows of "
                             f"{len(book_accounts)} accounts "
                             f"({', '.join(map(repr, book_accounts))}): "
                             f"size each account's own book (`tjs "
                             f"run` does), one file per account")
        return 2
    account = args.account or next(
        (t.get("account") for t in doc.get("transactions", [])
         if t.get("account")), "")
    phantoms = None
    if args.incomplete_history is not None:
        from taxjson.lib.missing_history import load_missing_history
        try:
            phantoms = load_missing_history(args.incomplete_history)
        except (OSError, ValueError) as e:
            cli_diag.error(PROG, f"could not read "
                                 f"{args.incomplete_history}: {e}")
            return 2
    renames = None
    if args.ticker_map is not None and args.ticker_map.exists():
        from taxjson.lib.ticker_map_rules import merge_renames
        from taxjson.lib.map_file import _parse_map_file
        tmap, problems, _notes = _parse_map_file(args.ticker_map)
        if problems:
            # A malformed or contradictory line's rule is dropped: the
            # distribution's symbol then names no holding and the book
            # was rewritten with no adjustment, at exit 0 (backlog
            # TJ-5044). `tjs run` refuses the same map up front.
            _say_map_problems(args.ticker_map, problems)
            return 2
        renames = merge_renames(tmap, to_base=True)
    doc, applied = apply_distributions(doc, load_rows(args.config), account,
                                       args.date_basis, phantoms=phantoms,
                                       renames=renames,
                                       country=args.country)

    from taxjson.lib.safe_write import write_atomic
    write_atomic(args.base_json,
                 json.dumps(doc, indent=2, sort_keys=True) + "\n")
    # A labelled note naming the account, not the file: the run's
    # console showed "taxjson-apply-distributions: applied 2
    # adjustment(s) to margin_base.json.stage." (backlog TJ-0163).
    emit_line(f"{PROG}: note: {applied} [[distributions]] adjustment(s) "
              f"applied" + (f" to {account}'s books" if account else ""),
              file=sys.stderr)
    return 0


def _say_map_problems(ticker_map: Path, problems: List[str]) -> None:
    """The refusal of a --ticker-map with problems (lib/map_file
    `_parse_map_file`), headed by the files they are in: the map given,
    and the tobase.map a project's ticker.map is read with (its lines'
    problems start `tobase.map:`), as `tjs run` names them (pipeline/run
    `_refuse_map_problems`, backlog TJ-0125)."""
    from taxjson.lib.tobase_map import TOBASE_MAP, tobase_path
    files: List[str] = []
    for p in problems:
        f = (str(tobase_path(ticker_map.parent))
             if ticker_map.name == "ticker.map"
             and p.startswith(f"{TOBASE_MAP}:") else str(ticker_map))
        if f not in files:
            files.append(f)
    files.sort(key=lambda f: f != str(ticker_map))
    cli_diag.error(PROG, f"{len(problems)} problem(s) in "
                         f"{' and '.join(files)}; nothing was written",
                   details=[f"- {p}" for p in problems] + [
                       "`tjs run` refuses these lines too: a malformed line "
                       "gives no rule, and contradictory lines no single "
                       "one. Fix each line (notes after `#`) or delete it, "
                       "then run the tool again."])


if __name__ == "__main__":
    sys.exit(main())
