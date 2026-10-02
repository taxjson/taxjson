#!/usr/bin/env python3
"""Apply non-cash fund distributions from a `distributions.map` file.

Canadian ETFs routinely declare REINVESTED (phantom) capital-gains
distributions — usually each December — that never appear in any broker
CSV yet increase the holder's ACB. Missing them silently overstates the
gain at sale. Some funds likewise report annual return-of-capital
factors only after year-end. This tool turns a small user-maintained
table into the ADJUST rows the engines already understand.

Map format (`distributions.map` at the project root, `#` comments):

    # SYMBOL   RECORD_DATE  PER_SHARE_AMOUNT
    XAW.TO     2025-12-29   0.4297      # reinvested dist -> ACB up
    ZRE.TO     2025-12-31   -0.1200     # return of capital -> ACB down

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
accounts whenever `distributions.map` exists; run it manually as:

    taxjson-apply-distributions work/margin_base.json --map distributions.map

With `--incomplete-history phantoms.json` (the pipeline passes it when the
project has one) the record-date balance includes the phantom openings the
gains stage will synthesize, so a position with pre-window history gets the
right share count.
"""

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from taxjson.lib.cli_diag import guard_main
from taxjson.lib import cli_diag
from taxjson.lib.country import country_arg

PROG = "taxjson-apply-distributions"


# A per-share amount is a plain decimal: float() also took 'nan', 'inf',
# '1e309' and '1_0' (read as 10), which either failed later at the gains
# stage under a wrong ROC/ACB-up label or silently applied 100x the
# amount (audit S025-14).
_AMOUNT_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def load_map(path: Path) -> List[Tuple[str, str, float]]:
    """[(symbol, date, per_share)] — malformed lines are fatal: a typo
    here silently mis-adjusts ACB, so refuse rather than skip. A
    repeated SYMBOL+DATE is kept (two components of one distribution
    can be entered on two lines) but warned about: a pasted line or an
    appended correction ADDS to the first one."""
    rows: List[Tuple[str, str, float]] = []
    seen: dict = {}
    # utf-8-sig: an editor's byte-order mark used to become part of the
    # first symbol, so that row was skipped as "no \ufeffXYZ.TO shares
    # held" with the BOM invisible in the note (audit S000-06).
    for lineno, raw in enumerate(
            cli_diag.read_text_utf8(path).splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            sys.exit(f"{PROG}: {path}:{lineno}: expected "
                     f"'SYMBOL DATE PER_SHARE', got {raw!r}")
        sym, date, amt = parts
        if not _AMOUNT_RE.fullmatch(amt):
            sys.exit(f"{PROG}: {path}:{lineno}: bad per-share amount "
                     f"{amt!r} (want a plain decimal such as 0.4297 or "
                     f"-0.12)")
        per_share = float(amt)
        # YYYY-MM-DD exactly, and a real date: '2025/06/19' or
        # '2025-6-19' compared as strings against the book's ISO dates
        # and sized the wrong record-date balance (audit S025-19).
        try:
            if not _DATE_RE.fullmatch(date):
                raise ValueError
            _dt.date.fromisoformat(date)
        except ValueError:
            sys.exit(f"{PROG}: {path}:{lineno}: bad date {date!r} "
                     f"(want YYYY-MM-DD)")
        # Book symbols are upper case: a lowercase key matched nothing
        # and the row was skipped as "no shares held" (audit S025-13).
        key = (sym.upper(), date)
        if key in seen:
            print(f"{PROG}: warning: {path}:{lineno}: {key[0]} {date} "
                  f"repeats line {seen[key]} — both amounts are applied "
                  f"(they ADD). If this line is a correction or a pasted "
                  f"copy, delete the other one.", file=sys.stderr)
        else:
            seen[key] = lineno
        rows.append((key[0], date, per_share))
    return rows


DATE_BASES = ("trade", "settle")


def _row_date(t: dict, date_basis: str) -> str:
    """The date a row changes the holder-of-record balance on. Under
    `settle` that is `date_settle` (falling back to the trade date for
    rows that carry none — SPLIT / OPENING_BALANCE / ADJUST); under
    `trade` it is `date`."""
    if date_basis == "settle":
        return str(t.get("date_settle") or t.get("date") or "")
    return str(t.get("date") or "")


def moment_rank(t: dict) -> int:
    """Fixed places of a dict row among rows executed on the same date
    (CA-DATE-14 / US-DATE-13): an opening balance first, then a split
    (effective at the open, whatever its clock stamp), then everything
    else in the export's row order (the sort is stable). Without it a
    buy listed before a same-stamp split was doubled in one walk and not
    in the engine (audit A2-0013)."""
    a = t.get("action")
    return 0 if a == "OPENING_BALANCE" else 1 if a == "SPLIT" else 2


def balance_on(transactions: List[dict], symbol: str, date: str,
               date_basis: str = "settle") -> float:
    """Shares of `symbol` held at end of `date`, from the book's own
    rows: position deltas summed per account and raw symbol, each SPLIT
    applied to its own symbol's shares, renames (symbol_new) moving
    them — so a map row keyed to the CURRENT ticker finds shares bought
    under a pre-rename one, and only those.

    `date_basis` picks which date a row moves the balance on. Fund
    record dates go by the holder of record — the SETTLED position —
    so under `settle` (what `taxjson run` uses in both countries: the
    record date is a market fact, not the tax-year date basis —
    partition INPUTS-10) a sale traded 06-19 that settles 06-22 still
    holds on a 06-19 record date, and a buy traded ON the record date
    (settling after it) is not yet credited. `trade` is a manual
    override."""
    if date_basis not in DATE_BASES:
        raise ValueError(f"date_basis must be one of {DATE_BASES}, "
                         f"got {date_basis!r}")
    # A trade executed BEFORE a split but settling AFTER it: the engine
    # re-denominates its quantity into post-split shares (core.py, the
    # settle-lag re-denomination), because under settle ordering the
    # pool splits first. Do the same here, or the pre-split sale was
    # subtracted from the already-split balance (audit S000-07: 1500
    # shares sized an ADJUST where the engine held 1000).
    factor = _settle_lag_factors(transactions) \
        if date_basis == "settle" else {}
    # Order like the Canada balance walk (corporate_timeline 'ca_balance'
    # profile, tax-logic CA-DATE-14): at one sort date an opening balance
    # or a settle-lagged execution first, then a split (effective at the
    # open), then the day's own trades — whatever the export's row order
    # (audit A2-0225: a buy listed before a same-stamp SPLIT was scaled
    # by it).
    def _phase(t: dict) -> int:
        act = t.get("action")
        if act == "OPENING_BALANCE":
            return 0
        if act == "SPLIT":
            return 1
        d = str(t.get("date") or "")
        return 0 if d and d < _row_date(t, date_basis) else 2
    rows = sorted(transactions,
                  key=lambda t: (_row_date(t, date_basis), _phase(t),
                                 str(t.get("time") or "")))
    # One corporate event = one application: an account fed by two
    # brokers carries the same SPLIT once per broker CSV (distinct ids,
    # so upstream dedup keeps both). The gains engine dedupes these per
    # corporate event; without the same dedup here, the blended
    # per-account split apportioned DOUBLED quantities (2026-09 audit).
    from taxjson.lib.corporate_timeline import (normalize_symbol_new,
                                                split_seen)
    _seen_splits = set()
    # Shares per (account, raw symbol), the way the engine walk holds
    # them: a SPLIT scales and moves only the shares of ITS symbol (in
    # its account), so shares already held under a rename's target are
    # not scaled by the rename's ratio (audit A2-0021), an old ticker
    # traded again after its rename stays its own holding (A2-0074),
    # and one account's copy of a split never scales another account's
    # shares (A2-0986).
    held: dict = {}
    for t in rows:
        if _row_date(t, date_basis) > date:
            break
        sym = str(t.get("symbol") or "")
        acct = str(t.get("account") or "")
        act = t.get("action")
        if act in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            held[(acct, sym)] = held.get((acct, sym), 0.0) + (
                float(t.get("quantity") or 0.0) * factor.get(id(t), 1.0))
        elif act == "SPLIT":
            ratio = float(t.get("quantity") or 0.0)
            if not ratio:
                continue
            # split_seen: the same split booked on two dates by two
            # brokers (within a week) is still ONE event.
            if split_seen(_seen_splits, sym,
                          str(t.get("date") or ""), ratio,
                          t.get("symbol_new") or "",
                          account=acct) is not None:
                continue
            target = normalize_symbol_new(sym, t.get("symbol_new")) or sym
            for key in [k for k in held
                        if k[1] == sym and (not acct or k[0] == acct)]:
                moved = held.pop(key) * ratio
                held[(key[0], target)] = held.get((key[0], target),
                                                  0.0) + moved
    return sum(q for (_a, s), q in held.items() if s == symbol)


def _settle_lag_factors(transactions: List[dict]) -> dict:
    """{id(row): factor} for BUYSELL/ASSIGN rows whose settle lag
    straddles a plain SPLIT of their symbol: executed (date, time)
    before the split's moment, settling after its date. The same test
    the gains engine uses to re-denominate the executed quantity."""
    splits = [t for t in transactions
              if t.get("action") == "SPLIT" and t.get("date")
              and float(t.get("quantity") or 0.0)
              and (t.get("symbol_new") or t.get("symbol"))
              == t.get("symbol")]
    out: dict = {}
    if not splits:
        return out
    for t in transactions:
        d, ds = str(t.get("date") or ""), str(t.get("date_settle") or "")
        if t.get("action") not in ("BUYSELL", "ASSIGN") or not d \
                or not ds or d >= ds:
            continue
        f = 1.0
        for sp in splits:
            spd = str(sp.get("date_settle") or sp.get("date"))
            if (sp.get("symbol") == t.get("symbol")
                    and (d, str(t.get("time") or "00:00:00"))
                    < (spd, str(sp.get("time") or "00:00:00"))
                    and spd < ds):
                f *= float(sp["quantity"])
        if abs(f - 1.0) > 1e-12:
            out[id(t)] = f
    return out


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


def _stamp_date(txs: List[dict], symbol: str, record_date: str,
                account: str) -> str:
    """The trade date to stamp a map ADJUST with. Sized on the settled
    position (the holder of record), it must also reach exactly those
    lots in a trade-date-ordered engine (the US, or tax_date = trade):
    a trade executed on or before the record date that settles AFTER it
    is not the record holder's, so the ADJUST goes at the end of the day
    BEFORE the earliest such trade (audit A2-0071: a sale traded on the
    record date dropped the ADJUST as 'no open lots'; A2-0988: a buy
    traded on the record date shared it). The settle date stays the
    record date, so settle-ordered books are unchanged."""
    straddle = [str(t.get("date") or "") for t in txs
                if t.get("symbol") == symbol
                and t.get("action") in ("BUYSELL", "ASSIGN", "TRANSFER")
                and (not account or not t.get("account")
                     or str(t.get("account")) == str(account))
                and str(t.get("date") or "") <= record_date
                < str(t.get("date_settle") or "")]
    if not straddle:
        return record_date
    first = _dt.date.fromisoformat(min(straddle))
    return (first - _dt.timedelta(days=1)).isoformat()


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
            print(f"{PROG}: warning: distributions.map {key} {date}: the "
                  f"book already has a return-of-capital ADJUST of "
                  f"{float(t.get('net_amount') or 0.0):.2f} on {symbol} "
                  f"(dated {t.get('date')}"
                  + (f", record date {t.get('record_date')}"
                     if t.get("record_date") else "")
                  + ") — if both are the same distribution the cost is "
                  f"reduced TWICE. Delete the map line (or the .tt "
                  f"ADJUST).", file=sys.stderr)
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
        print(f"{PROG}: warning: distributions.map {key} {date}: the "
              f"return of capital ({amount:+.2f}) lowers the cost, but the "
              f"cash of the distribution paid {d.get('date')} is booked as "
              f"a DIVIDEND row and still counted IN FULL as income by "
              f"taxjson (divs-sum, the estimate). Report income from the "
              f"slip ({slip} part is not income).", file=sys.stderr)


def _phantom_openings(txs: List[dict], phantoms) -> List[dict]:
    """The OPENING_BALANCE rows the gains stage will synthesize from
    phantoms.json for this book — the SAME synthesize_openings call, so
    the record-date balance here agrees with the position the engine
    books. Used for sizing only; never written to the base book (the
    gains stage adds its own)."""
    if not phantoms:
        return []
    from taxjson.lib.core import coerce_transaction_row
    from taxjson.lib.phantom_holdings import synthesize_openings
    rows = [coerce_transaction_row(t, i, PROG) for i, t in enumerate(txs)]
    out, _log = synthesize_openings(rows, phantoms)
    return [t.to_dict() for t in out[len(rows):]
            if t.action == "OPENING_BALANCE"]


# Where the income of a reinvested distribution is reported, by the
# project's country (the tool books the cost side only).
_INCOME_SLIP = {"canada": "the T3/T5 slip",
                "usa": "Form 1099-DIV (box 2a for a capital-gain "
                       "distribution)",
                None: "your tax slip"}


def apply_distributions(doc: dict, map_rows, account: str,
                        date_basis: str = "settle",
                        phantoms=None, renames=None,
                        country: Optional[str] = None) -> Tuple[dict, int]:
    """`phantoms` — the (symbol, account) set from phantoms.json. The
    record-date balance must include the phantom openings the gains
    stage synthesizes (audit S000-08: sized on the phantom-less book, a
    phantom-backed position got half the ADJUST, or none).

    `renames` — the ticker.map renames the base book went through
    (GLOBAL + TOBASE + JOURNAL): a key naming the broker's listing is
    mapped the same way before the lookup (audit S025-22: a key the
    holdings report shows was skipped as "no shares held")."""
    txs = doc.get("transactions", [])
    # Regenerate, never accumulate: drop rows this tool added before.
    txs = [t for t in txs
           if not str(t.get("id") or "").startswith("DIST-")]
    sizing = txs + _phantom_openings(txs, phantoms)
    applied = 0
    used_ids: set = set()
    for key, date, per_share in map_rows:
        if per_share == 0:
            # A 0 is a placeholder (the fund has not published yet), not
            # a distribution: it used to be reported as an applied
            # "return of capital" and a $0 ADJUST that the checklist's
            # roc-entered step counted (audit S025-23).
            print(f"NOTE: distributions.map: {key} {date} has per-share "
                  f"amount 0 — a placeholder, NOT applied. Enter the "
                  f"fund's declared amount once it is published.",
                  file=sys.stderr)
            continue
        sym = key
        if renames:
            from taxjson.bin.taxjson_ticker_map import map_symbol
            sym = map_symbol(sym, renames)
        live = resolve_live_symbol(sizing, sym, date)
        # A key that still holds shares under its own name on the record
        # date is that holding — a ticker reused after its rename (FB
        # bought again after FB -> META) is not the renamed pool (audit
        # A2-0074).
        if live != sym and balance_on(sizing, sym, date,
                                      date_basis) > 1e-9:
            live = sym
        sym = live
        via = f" (as {sym})" if sym != key else ""
        bal = balance_on(sizing, sym, date, date_basis)
        if bal <= 1e-9:
            print(f"NOTE: distributions.map: no {key}{via} shares held "
                  f"on {date} in this book — row skipped.",
                  file=sys.stderr)
            continue
        amount = round(bal * per_share, 6)
        # Neutral without a country: the row is saved into the book,
        # and "ACB" is Canada's word (A2-1234).
        from taxjson.lib.country import COST_TERM
        _cost = COST_TERM[country]
        kind = (f"reinvested distribution ({_cost} up)" if per_share > 0
                else f"return of capital ({_cost} down)")
        # Deterministic AND unique: a SYMBOL+DATE entered twice (two
        # components, or a pasted line load_map warned about) gives
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
            "account": account,
            "id": rid,
            "description": f"{kind}: {bal:g} sh x {per_share:g}/sh "
                           f"per distributions.map",
        })
        # A reinvested distribution is income of the record year (T3
        # box 21/26/49, or a T5 stock dividend): the ACB rises only
        # because that amount is taxed. This tool books the ACB side
        # only — say so, or the estimate silently omits it (S026-00).
        income = (f" Report the {amount:.2f} itself as income from "
                  f"{_INCOME_SLIP[country]} — it is not counted as income "
                  f"by taxjson (estimate, divs-sum)." if per_share > 0
                  else "")
        if per_share < 0:
            _warn_roc_overlaps(txs, sym, key, date, account, amount,
                               country)
        ccy = doc.get("metadata", {}).get("target_currency", "")
        unit = f" {ccy}" if ccy else ""
        print(f"NOTE: {key}{via} {date}: {kind} — {bal:g} sh x "
              f"{per_share:g}{unit} = {amount:+.2f} "
              f"{_cost} adjustment.{income}", file=sys.stderr)
        applied += 1
    doc["transactions"] = txs
    return doc, applied


@guard_main("taxjson-apply-distributions")
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Map lines are SYMBOL RECORD_DATE PER_SHARE. The per-share "
               "amount is in the project BASE currency (the books this "
               "tool reads are already converted): convert a US-listed "
               "fund's published USD factor to the base currency first.")
    ap.add_argument("base_json", type=Path)
    ap.add_argument("--map", type=Path, required=True,
                    help="distributions.map (per-share amounts in the "
                         "project base currency)")
    ap.add_argument("--account", default="")
    ap.add_argument("--date-basis", choices=DATE_BASES, default="settle",
                    help="Which date a trade moves the record-date "
                         "balance on: settle (the holder of record is "
                         "the settled position — a market fact in both "
                         "countries, and what `taxjson run` uses) or "
                         "trade (a manual override).")
    ap.add_argument("--country", type=country_arg, default=None,
                    metavar="{canada,ca,usa,us}",
                    help="Only words the output: the book row's "
                         "description and the note (ACB and the T3/T5 "
                         "slip for canada, basis and Form 1099-DIV for "
                         "usa, neutral 'cost' without it); `taxjson "
                         "run` passes it.")
    ap.add_argument("--ticker-map", type=Path, default=None,
                    metavar="TICKER_MAP",
                    help="ticker.map the base book went through: map "
                         "keys are renamed the same way (GLOBAL, TOBASE, "
                         "JOURNAL) before the lookup (`taxjson run` "
                         "passes it).")
    ap.add_argument("--incomplete-history", type=Path, default=None,
                    metavar="PHANTOMS_JSON",
                    help="phantoms.json: size each record-date balance "
                         "with the phantom openings the gains stage "
                         "will synthesize (`taxjson run` passes it).")
    args = ap.parse_args(argv)

    if args.ticker_map is not None and not args.ticker_map.exists():
        # A named input that does not exist is refused, not silently
        # skipped (re-audit A2-1436; reconcile-slips' twin refuses too).
        cli_diag.error(PROG, f"no such file: --ticker-map {args.ticker_map}")
        return 2
    if not args.map.exists():
        cli_diag.error(PROG, f"no such map file: {args.map}")
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
    account = args.account or next(
        (t.get("account") for t in doc.get("transactions", [])
         if t.get("account")), "")
    phantoms = None
    if args.incomplete_history is not None:
        from taxjson.lib.phantom_holdings import load_phantoms
        try:
            phantoms = load_phantoms(args.incomplete_history)
        except (OSError, ValueError) as e:
            cli_diag.error(PROG, f"could not read "
                                 f"{args.incomplete_history}: {e}")
            return 2
    renames = None
    if args.ticker_map is not None and args.ticker_map.exists():
        from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                    merge_renames)
        tmap, _problems, _notes = _parse_map_file(args.ticker_map)
        renames = merge_renames(tmap, to_base=True)
    doc, applied = apply_distributions(doc, load_map(args.map), account,
                                       args.date_basis, phantoms=phantoms,
                                       renames=renames,
                                       country=args.country)

    tmp = args.base_json.with_name(args.base_json.name + ".part")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(args.base_json)
    print(f"{PROG}: applied {applied} adjustment(s) to "
          f"{args.base_json.name}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
