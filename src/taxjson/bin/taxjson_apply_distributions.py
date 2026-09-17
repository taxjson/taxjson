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
`--date-basis trade`) and one ADJUST row is appended:

    net_amount = balance * per_share      (positive = ACB increase)

with a deterministic id, so a rebuild regenerates rather than
accumulates. Rows for symbols the account doesn't hold on the date are
skipped with a NOTE. The pipeline wires this in for taxable equity
accounts whenever `distributions.map` exists; run it manually as:

    taxjson-apply-distributions work/margin_base.json --map distributions.map
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

from taxjson.lib import cli_diag

PROG = "taxjson-apply-distributions"


def load_map(path: Path) -> List[Tuple[str, str, float]]:
    """[(symbol, date, per_share)] — malformed lines are fatal: a typo
    here silently mis-adjusts ACB, so refuse rather than skip."""
    rows: List[Tuple[str, str, float]] = []
    for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
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
        rows.append((sym, date, per_share))
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
    so under `settle` (the CRA default) a sale traded 06-19 that
    settles 06-22 still holds on a 06-19 record date, and a buy traded
    ON the record date (settling after it) is not yet credited. Under
    `trade` the trade date rules (IRS-style projects)."""
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
    from taxjson.lib.corporate_timeline import split_event_key
    _seen_splits = set()
    for t in rows:
        if _row_date(t, date_basis) > date:
            break
        if t.get("symbol") not in aliases:
            continue
        act = t.get("action")
        if act in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            bal += float(t.get("quantity") or 0.0)
        elif act == "SPLIT":
            ratio = float(t.get("quantity") or 0.0)
            if not ratio:
                continue
            _k = split_event_key(str(t.get("symbol") or ""),
                                 str(t.get("date") or ""), ratio,
                                 t.get("symbol_new") or "",
                                 account=str(t.get("account") or ""))
            if _k in _seen_splits:
                continue
            _seen_splits.add(_k)
            bal *= ratio
    return bal


def apply_distributions(doc: dict, map_rows, account: str,
                        date_basis: str = "settle") -> Tuple[dict, int]:
    txs = doc.get("transactions", [])
    # Regenerate, never accumulate: drop rows this tool added before.
    txs = [t for t in txs
           if not str(t.get("id") or "").startswith("DIST-")]
    applied = 0
    for sym, date, per_share in map_rows:
        bal = balance_on(txs, sym, date, date_basis)
        if bal <= 1e-9:
            print(f"NOTE: distributions.map: no {sym} shares held on "
                  f"{date} in this book — row skipped.", file=sys.stderr)
            continue
        amount = round(bal * per_share, 6)
        kind = ("reinvested distribution (ACB up)" if per_share > 0
                else "return of capital (ACB down)")
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
        print(f"NOTE: {sym} {date}: {kind} — {bal:g} sh x "
              f"{per_share:g} = {amount:+.2f} ACB adjustment.",
              file=sys.stderr)
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
                         "balance on: settle (holder of record = "
                         "settled position; CRA default) or trade. "
                         "`taxjson run` passes the project's tax_date.")
    args = ap.parse_args(argv)

    if not args.map.exists():
        cli_diag.error(PROG, f"no such map file: {args.map}")
        return 2
    try:
        doc = json.loads(args.base_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        cli_diag.error(PROG, f"could not read {args.base_json}: {e}")
        return 2

    account = args.account or next(
        (t.get("account") for t in doc.get("transactions", [])
         if t.get("account")), "")
    doc, applied = apply_distributions(doc, load_map(args.map), account,
                                       args.date_basis)

    tmp = args.base_json.with_name(args.base_json.name + ".part")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(args.base_json)
    print(f"{PROG}: applied {applied} adjustment(s) to "
          f"{args.base_json.name}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
