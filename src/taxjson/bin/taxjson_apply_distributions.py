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
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from taxjson.lib import cli_diag
from taxjson.lib.country import country_arg

PROG = "taxjson-apply-distributions"


def load_map(path: Path) -> List[Tuple[str, str, float]]:
    """[(symbol, date, per_share)] — malformed lines are fatal: a typo
    here silently mis-adjusts ACB, so refuse rather than skip."""
    rows: List[Tuple[str, str, float]] = []
    # utf-8-sig: an editor's byte-order mark used to become part of the
    # first symbol, so that row was skipped as "no \ufeffXYZ.TO shares
    # held" with the BOM invisible in the note (audit S000-06).
    for lineno, raw in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            sys.exit(f"{PROG}: {path}:{lineno}: expected "
                     f"'SYMBOL DATE PER_SHARE', got {raw!r}")
        sym, date, amt = parts
        try:
            per_share = float(amt)
        except ValueError:
            sys.exit(f"{PROG}: {path}:{lineno}: bad per-share amount "
                     f"{amt!r}")
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            sys.exit(f"{PROG}: {path}:{lineno}: bad date {date!r} "
                     f"(want YYYY-MM-DD)")
        # Book symbols are upper case: a lowercase key matched nothing
        # and the row was skipped as "no shares held" (audit S025-13).
        rows.append((sym.upper(), date, per_share))
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


def balance_on(transactions: List[dict], symbol: str, date: str,
               date_basis: str = "settle") -> float:
    """Shares of `symbol` held at end of `date`, from the book's own
    rows: position deltas summed, SPLIT ratios applied, renames
    (symbol_new) followed — so a map row keyed to the CURRENT ticker
    finds shares bought under a pre-rename one.

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
    aliases = {symbol}
    changed = True
    while changed:                      # follow rename chains backwards
        changed = False
        for t in transactions:
            new = (t.get("symbol_new") or "").strip()
            if (t.get("action") == "SPLIT" and new in aliases
                    and t.get("symbol") not in aliases):
                aliases.add(t["symbol"])
                changed = True
    # A trade executed BEFORE a split but settling AFTER it: the engine
    # re-denominates its quantity into post-split shares (core.py, the
    # settle-lag re-denomination), because under settle ordering the
    # pool splits first. Do the same here, or the pre-split sale was
    # subtracted from the already-split balance (audit S000-07: 1500
    # shares sized an ADJUST where the engine held 1000).
    factor = _settle_lag_factors(transactions) \
        if date_basis == "settle" else {}
    bal = 0.0
    rows = sorted(transactions,
                  key=lambda t: (_row_date(t, date_basis),
                                 str(t.get("date") or ""),
                                 str(t.get("time") or "")))
    # One corporate event = one application: an account fed by two
    # brokers carries the same SPLIT once per broker CSV (distinct ids,
    # so upstream dedup keeps both). The gains engine dedupes these per
    # corporate event; without the same dedup here, the blended
    # per-account split apportioned DOUBLED quantities (2026-09 audit).
    from taxjson.lib.corporate_timeline import split_seen
    _seen_splits = set()
    for t in rows:
        if _row_date(t, date_basis) > date:
            break
        if t.get("symbol") not in aliases:
            continue
        act = t.get("action")
        if act in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            bal += float(t.get("quantity") or 0.0) * factor.get(id(t), 1.0)
        elif act == "SPLIT":
            ratio = float(t.get("quantity") or 0.0)
            if not ratio:
                continue
            # split_seen: the same split booked on two dates by two
            # brokers (within a week) is still ONE event.
            if split_seen(_seen_splits, str(t.get("symbol") or ""),
                          str(t.get("date") or ""), ratio,
                          t.get("symbol_new") or "",
                          account=str(t.get("account") or "")) is not None:
                continue
            bal *= ratio
    return bal


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
    for key, date, per_share in map_rows:
        sym = key
        if renames:
            from taxjson.bin.taxjson_ticker_map import map_symbol
            sym = map_symbol(sym, renames)
        sym = resolve_live_symbol(sizing, sym, date)
        via = f" (as {sym})" if sym != key else ""
        bal = balance_on(sizing, sym, date, date_basis)
        if bal <= 1e-9:
            print(f"NOTE: distributions.map: no {key}{via} shares held "
                  f"on {date} in this book — row skipped.",
                  file=sys.stderr)
            continue
        amount = round(bal * per_share, 6)
        _cost = "basis" if country == "usa" else "ACB"
        kind = (f"reinvested distribution ({_cost} up)" if per_share > 0
                else f"return of capital ({_cost} down)")
        txs.append({
            "action": "ADJUST",
            "date": date, "time": "23:59:58", "date_settle": date,
            "symbol": sym, "quantity": 0.0,
            "currency": doc.get("metadata", {}).get("target_currency", ""),
            "net_amount": amount, "gross_amount": 0.0,
            "type": "dist",
            "account": account,
            "id": f"DIST-{sym}-{date}-{account}",
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
        print(f"NOTE: {key}{via} {date}: {kind} — {bal:g} sh x "
              f"{per_share:g} = {amount:+.2f} "
              f"{'basis' if country == 'usa' else 'ACB'} "
              f"adjustment.{income}", file=sys.stderr)
        applied += 1
    doc["transactions"] = txs
    return doc, applied


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base_json", type=Path)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--account", default="")
    ap.add_argument("--date-basis", choices=DATE_BASES, default="settle",
                    help="Which date a trade moves the record-date "
                         "balance on: settle (the holder of record is "
                         "the settled position — a market fact in both "
                         "countries, and what `taxjson run` uses) or "
                         "trade (a manual override).")
    ap.add_argument("--country", type=country_arg, default=None,
                    metavar="{canada,ca,usa,us}",
                    help="Only words the income note (T3/T5 slip or "
                         "Form 1099-DIV); `taxjson run` passes it.")
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

    if not args.map.exists():
        cli_diag.error(PROG, f"no such map file: {args.map}")
        return 2
    from taxjson.lib.json_input import InputFileError, read_json_doc
    try:
        # A bare-array book is accepted, as load_transactions does
        # (audit S079-11).
        doc = read_json_doc(args.base_json)
    except InputFileError as e:
        cli_diag.error(PROG, f"could not read {e}")
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
