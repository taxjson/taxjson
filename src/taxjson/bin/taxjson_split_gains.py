#!/usr/bin/env python3
"""Split a COMBINED (blended multi-account) gains run into per-account
artifacts.

The blended taxable pass computes one gains run over every taxable
equity account's book — Canada's ACB pools blend across accounts (ITA
s.47) and US §1091 matches cross-account (with per-account FIFO basis
via --per-account-basis). This tool then rebuilds the familiar
per-account `<name>_gains_wash.json` files from it, so every downstream
consumer (sum, list, form-export, carryover, reports) keeps reading the
same filenames it always has.

Per-account content:
  transactions    entries whose `account` matches (gains, dividends,
                  manual rows alike)
  wash_sales      records whose loss belongs to the account
  inventory       rows carrying `account` (US per-account lots) are
                  filtered; account-less rows (Canada's blended pools)
                  are APPORTIONED: the account's share count is walked
                  from its own base book and priced at the blended
                  ACB/share — which is exactly the s.47 presentation.
  summary         recomputed for the slice (total_gain,
                  total_disallowed, fees, US count)
  inventory dates position_start_date is the account's own (from its
                  base book); last_acq_date stays the pool's (s.54
                  looks at any acquisition of the identical property)
  inventory_year_end
                  the same: US rows filtered by `account`, Canada's
                  pools apportioned by the account's balance at the
                  year end (`inventory_year_end_basis`, when present,
                  copied)
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib import cli_diag
from taxjson.bin.taxjson_apply_distributions import balance_on, moment_rank

PROG = "taxjson-split-gains"


def _mh_log(doc: Dict[str, Any]) -> List[dict]:
    """The gains file's missing-history log: 'missing_history_log', or
    'phantom_application_log' in a file an older taxjson wrote (a cached
    work/ dir)."""
    log = doc.get("missing_history_log")
    if log is None:
        log = doc.get("phantom_application_log")
    return log or []


def _missing_history_openings(combined: Dict[str, Any],
                              account: str) -> List[dict]:
    """The blended pass's missing-history openings of `account`
    (lib/missing_history.openings_from_log, the one reader every
    positions view shares)."""
    from taxjson.lib.missing_history import openings_from_log
    return openings_from_log(combined, account)


def _position_starts(rows: List[dict], basis: str) -> Dict[str, str]:
    """{symbol: trade date the account's CURRENT position in it opened}
    from the account's own book (renames followed, splits applied once
    per event), the engine's position_start_date convention: the last
    time the balance left zero (or changed sign). The blended row's own
    date is the POOL's start — another account's (audit S050-19)."""
    from taxjson.lib.corporate_timeline import split_copies
    date_key = "date_settle" if basis == "settle" else "date"
    order = sorted(rows, key=lambda t: (str(t.get(date_key)
                                            or t.get("date") or ""),
                                        str(t.get("date") or ""),
                                        moment_rank(t),
                                        str(t.get("time") or "")))
    bal: Dict[str, float] = {}
    start: Dict[str, str] = {}
    # One corporate event per account however many copies (CA-CORP-12).
    dup = split_copies(order, per_account=True)
    # A trade executed before a split and settling after it is in
    # post-split shares under settle ordering, as the engine and
    # balance_on re-denominate it: a June 3 sale of 100 settling June 6
    # over a June 4 2-for-1 left 100 phantom shares here, and a June 9
    # buy's position kept January's start (backlog TJ-1013).
    from taxjson.bin.taxjson_apply_distributions import _settle_lag_factors
    factor = _settle_lag_factors(order) if basis == "settle" else {}
    for t in order:
        sym = str(t.get("symbol") or "")
        act = t.get("action")
        if act in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            q = float(t.get("quantity") or 0.0) * factor.get(id(t), 1.0)
            before = bal.get(sym, 0.0)
            after = before + q
            bal[sym] = after
            if abs(after) < 1e-9:
                start.pop(sym, None)
            elif abs(before) < 1e-9 or (before > 0) != (after > 0):
                start[sym] = str(t.get("date") or "")
        elif act == "SPLIT":
            ratio = float(t.get("quantity") or 0.0)
            new = (t.get("symbol_new") or "").strip()
            if not ratio or id(t) in dup:
                continue
            q = bal.pop(sym, 0.0) * ratio
            st = start.pop(sym, None)
            tgt = new or sym
            if tgt != sym and abs(bal.get(tgt, 0.0)) > 1e-9:
                st = min(filter(None, (st, start.get(tgt)))) \
                    if (st or start.get(tgt)) else None
            bal[tgt] = bal.get(tgt, 0.0) + q
            if st and abs(bal[tgt]) > 1e-9:
                start[tgt] = st
    return start


def _own_premium_cost(lots: Any, account: str, qty: float, share: float,
                      total_cost: Any) -> Optional[float]:
    """A written option's cost in `account` from the engine's table of
    each account's open contracts (core's `account_premiums`), or None
    when its units are not its own lots or the table cannot be read."""
    try:
        own = lots[account]
        if abs(abs(qty) - float(own["units"])) >= 1e-6:
            return None
        rest = float(total_cost or 0.0) + sum(float(e["premium"])
                                              for e in lots.values())
        return round(-float(own["premium"]) + rest * share, 6)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _lots_are_every_position(lots: Any, symbol: str,
                             pool_rows: Optional[List[dict]], until: str,
                             date_basis: str) -> bool:
    """Does every account of the blended pass (`pool_rows`) hold exactly
    its own lots of the table `lots`? If one does not (a contract closed
    or moved across accounts, a long beside the writes), an account's own
    premium and another's share of the whole pool would not add up to
    the pool, so all go by share (backlog TJ-1303). No rows to ask: the
    account's own units decide alone."""
    if not pool_rows:
        return True
    from taxjson.lib.positions_check import pool_splits
    try:
        accounts = {str(r.get("account") or "") for r in pool_rows
                    if isinstance(r, dict)} | set(lots)
        for a in accounts:
            own = [r for r in pool_rows if isinstance(r, dict)
                   and str(r.get("account") or "") == a]
            bal = balance_on(own + pool_splits(pool_rows, a), symbol, until,
                             date_basis=date_basis)
            if abs(bal + (float(lots[a]["units"]) if a in lots else 0.0)
                   ) >= 1e-6:
                return False
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return True


def _split_inventory(rows: List[Dict[str, Any]], account: str,
                     book: Optional[List[dict]], starts: Dict[str, str],
                     until: str, date_basis: str = "settle",
                     pool_rows: Optional[List[dict]] = None
                     ) -> List[Dict[str, Any]]:
    """The account's inventory rows: its own (US per-account lots), and
    its share of each blended pool by its balance at the end of `until`
    (on `date_basis`: the pools' own, a trade-date project's year end on
    trade dates, backlog TJ-0343; `taxjson list --date` gives the
    project's). A short pool's row beside a long (`short_pool`) is its
    account's own, so the account's balance takes it back before its
    share of the long is read: the two never net (backlog TJ-1028)."""
    inventory: List[Dict[str, Any]] = []
    own_short: Dict[str, float] = {}
    for row in rows:
        if row.get("short_pool") and row.get("account") == account:
            sym = row.get("symbol", "")
            own_short[sym] = own_short.get(sym, 0.0) - float(row.get("qty")
                                                             or 0.0)
    for row in rows:
        row_acct = row.get("account")
        if row_acct:
            if row_acct == account:
                inventory.append(dict(row))
            continue
        # Blended (account-less) pool row — apportion by the account's
        # own share count at the blended ACB per share.
        if book is None:
            continue
        qty = (balance_on(book, row.get("symbol", ""), until,
                          date_basis=date_basis)
               + own_short.get(row.get("symbol", ""), 0.0))
        total_qty = float(row.get("qty") or 0.0)
        if abs(qty) < 1e-9 or abs(total_qty) < 1e-9:
            continue
        share = qty / total_qty
        r = dict(row)
        # Full precision (audit S050-21: 6 dp turned 4e-07 ETH into a
        # 0.0-unit row with a cost); 10 dp only trims float noise.
        r["qty"] = round(qty, 10)
        # SINCE is the account's own position start; last_acq_date stays
        # the POOL's on purpose (s.54 looks at any acquisition of the
        # identical property, in any account).
        _st = starts.get(row.get("symbol", ""))
        if _st and "position_start_date" in row:
            r["position_start_date"] = _st
        # Every ADDITIVE field scales with the account's share — the
        # deferred superficial loss too (audit R1-160: copied whole into
        # every account, it was counted once per account), and a written
        # option's premium recognised at the write (grant timing: copied
        # whole, harvest charged each account the pool's premium as a
        # buy-back loss, backlog TJ-1010).
        for _f in ("total_cost", "deferred_wash", "base_total_cost",
                   "recognised_premium"):
            if _f in row:
                r[_f] = round(float(row.get(_f) or 0.0) * share, 6)
        # A written option open in several accounts: its own contracts'
        # premium, which a close takes, with its share of what the lots
        # do not hold (backlog TJ-1251). Units that are not its own lots
        # (a contract moved in), in any account, go by share (TJ-1303).
        _lots = r.pop("account_premiums", None)
        _cost = (_own_premium_cost(_lots, account, qty, share,
                                   row.get("total_cost"))
                 if _lots is not None and _lots_are_every_position(
                     _lots, row.get("symbol", ""), pool_rows, until,
                     date_basis)
                 else None)
        if _cost is not None:
            r["total_cost"] = _cost
        r["account"] = account
        r["blended_pool"] = True    # ACB/share is the s.47 blended figure
        inventory.append(r)
    return inventory


def split_for_account(combined: Dict[str, Any], account: str,
                      base_txs: Optional[List[dict]],
                      pool_rows: Optional[List[dict]] = None
                      ) -> Dict[str, Any]:
    # What the pass across accounts cut at this account's snapshot is
    # not in its walk either (backlog TJ-1168).
    from taxjson.lib.positions_check import without_pool_cut
    if base_txs is not None:
        base_txs = without_pool_cut(base_txs, pool_rows, account)
    txs = [e for e in combined.get("transactions", [])
           if e.get("account") == account]
    manual = [e for e in combined.get("manual_reporting_required", [])
              if e.get("account") == account]
    # Wash records are matched by the LOSS's transaction id against the
    # account's own entries — the DISALLOW virtual tx carries the
    # engine-default account ('PORTFOLIO'), not the loss's, so an
    # account-field filter silently dropped every record.
    _entry_ids = {e.get("tx_id") or e.get("id")
                  for e in txs if (e.get("tx_id") or e.get("id"))}
    wash = [w for w in combined.get("wash_sales", [])
            if (w.get("loss_tx") or {}).get("account") == account
            or w.get("loss_tx_id") in _entry_ids]

    # Missing-history openings the blended pass synthesized for THIS
    # account (.tt OPENING cost=unknown lines): they are in the pool but not in the
    # base book
    # (audit R1-275 / R1-322 — a pool showed negative shares at a
    # negative cost, another vanished).
    openings = _missing_history_openings(combined, account)
    _basis = (combined.get("summary") or {}).get("tax_date_basis") \
        or "settle"
    # The splits another account of the pool reports (`pool_rows`: the
    # blended pass's merged books): the engine split this account's
    # shares too, so its count is walked with them (backlog TJ-0696).
    from taxjson.lib.positions_check import pool_splits
    _pool = pool_splits(pool_rows or [], account)
    starts = (_position_starts(list(base_txs) + openings + _pool, _basis)
              if base_txs is not None else {})
    book = ((list(base_txs) + openings + _pool) if base_txs is not None
            else None)
    inventory = _split_inventory(combined.get("inventory", []), account,
                                 book, starts, "9999-12-31",
                                 pool_rows=pool_rows)
    # The pools or lots at the year end (the engine's
    # inventory_year_end): the US engine's per-account lots are the
    # account's own, Canada's pools go by the account's balance at that
    # day (backlog TJ-0282, TJ-0284).
    _ye_year = str((combined.get("summary") or {}).get("year") or "")
    ye_inventory = (_split_inventory(
        combined["inventory_year_end"], account, book, {},
        f"{_ye_year}-12-31",
        "trade" if combined.get("inventory_year_end_basis") == "trade"
        else "settle")
                    if "inventory_year_end" in combined and _ye_year
                    else None)

    realized = disallowed = 0.0
    for e in txs:
        if "gain" in e and "qty" in e and not e.get("tainted"):
            realized += float(e.get("gain") or 0.0)
            disallowed += float(e.get("disallowed_amount")
                                or e.get("disallowed") or 0.0)
    summary = dict(combined.get("summary") or {})
    summary["total_gain"] = round(realized, 2)
    summary["total_disallowed"] = round(disallowed, 2)
    if "count" in summary:          # US: this file's records (S050-23)
        summary["count"] = len(txs)
    # Per-slice fee map: copying the COMBINED map into every account's
    # file made each .sum carry all accounts' fees (`taxjson sum`
    # multi-counted them — the FUZZ #I class). The map is rebuilt from
    # the account's BASE book exactly the way pipeline.run_gains builds
    # it (raw BUYSELL/ASSIGN rows, year-scoped, nested
    # stocks/options/total buckets) — gain entries only carry sell-side
    # fee shares, so they cannot reproduce it.
    import re as _re
    fees: Dict[str, Dict[str, float]] = {}
    year_str = str(summary.get("year") or "") or None
    date_attr = ("date_settle"
                 if summary.get("tax_date_basis") == "settle" else "date")
    for btx in (base_txs or []):
        if btx.get("action") not in ("BUYSELL", "ASSIGN"):
            continue
        d = btx.get(date_attr) or btx.get("date") or ""
        if year_str and not d.startswith(year_str):
            continue
        fee = (float(btx.get("commission") or 0)
               + float(btx.get("fee") or 0))
        # != 0, not > 0 — rebates must NET, matching pipeline.py's
        # canonical builder (a <= 0 skip overstated every blended
        # account's FEES by its rebate total).
        if fee == 0:
            continue
        curr = btx.get("currency") or "?"
        is_opt = bool(_re.search(r"\d{6}[CP]\d+", btx.get("symbol") or ""))
        from taxjson.lib.futures import is_plain_future
        asset = ("options" if is_opt else
                 "futures" if is_plain_future(btx.get("symbol") or "")
                 else "stocks")
        bucket = fees.setdefault(
            curr, {"stocks": 0.0, "options": 0.0, "total": 0.0})
        bucket[asset] = bucket.get(asset, 0.0) + fee
        bucket["total"] += fee
    summary["total_fees_by_currency"] = fees

    out = {
        "metadata": {
            "split_from": "blended taxable pass",
            "account": account,
            "blended": True,
        },
        "transactions": txs,
        "manual_reporting_required": manual,
        "wash_sales": wash,
        "inventory": inventory,
        **({"inventory_year_end": ye_inventory}
           if ye_inventory is not None else {}),
        # The date the year end was read on, when not the engine's walk
        # date (a US settle or Canadian trade project, backlog TJ-0343).
        **({"inventory_year_end_basis":
            combined["inventory_year_end_basis"]}
           if ye_inventory is not None
           and "inventory_year_end_basis" in combined else {}),
        "summary": summary,
    }
    # The manual superficial-loss warnings (unknown-cost neighbours)
    # follow their loss's account (audit R1-325: the split dropped them).
    slw = [w for w in combined.get("superficial_loss_warnings") or []
           if (w.get("account") or account) == account
           or any((d.get("account") or "") == account
                  for d in w.get("tainted_dispositions") or [])]
    if slw:
        out["superficial_loss_warnings"] = slw
    # Option/right-replacement warnings follow their loss (by its id),
    # and the missing-history log its account (audit S050-11: both were dropped
    # from the canonical per-account file).
    _loss_ids = _entry_ids | {e.get("tx_id") or e.get("id")
                              for e in manual}
    orw = [w for w in combined.get("option_replacement_warnings") or []
           if w.get("loss_id") in _loss_ids]
    if orw:
        out["option_replacement_warnings"] = orw
    # The account's filing positions against the loss rule (.tt
    # ALLOWLOSS, lib/loss_overrides): each follows the account its line
    # is in.
    lo = [it for it in combined.get("loss_overrides") or []
          if isinstance(it, dict) and it.get("account") == account]
    if lo:
        out["loss_overrides"] = lo
    # Grant-timed closes that leave no record (backlog TJ-1091).
    nil = [c for c in combined.get("grant_nil_closes") or []
           if c.get("account") == account]
    if nil:
        out["grant_nil_closes"] = nil
    # A cover of the year whose loss is the next year's (backlog TJ-1189).
    later = [c for c in combined.get("covers_realized_next_year") or []
             if c.get("account") == account]
    if later:
        out["covers_realized_next_year"] = later
    plog =[e for e in _mh_log(combined)
            if e.get("account") == account]
    if plog:
        out["missing_history_log"] = plog
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=" ".join(__doc__.split("\n\n")[0].split()))
    ap.add_argument("combined_json", type=Path)
    ap.add_argument("--account", required=True)
    ap.add_argument("--base", type=Path, default=None,
                    help="The account's base book (for apportioning "
                         "blended account-less inventory rows)")
    ap.add_argument("--pool-base", type=Path, default=None,
                    help="The blended pass's merged books (every account "
                         "of the pool): the splits another account "
                         "reports apply to this account's shares too")
    args = ap.parse_args(argv)

    # read_work_doc: rows of objects with numeric money fields — a text
    # qty was a traceback further on (re-audit A2-0793).
    from taxjson.lib.json_input import InputFileError, read_work_doc
    try:
        combined = read_work_doc(args.combined_json)
    except InputFileError as e:
        cli_diag.error(PROG, f"could not read {e}")
        return 2
    base_txs = None
    if args.base:
        # A named base book that is missing or unreadable is fatal, as
        # an unreadable combined file is (audit S050-15: it emptied the
        # account's fee map and dropped its blended holdings, exit 0).
        try:
            _base_doc = read_work_doc(args.base)
            if "transactions" not in _base_doc:
                # Read as an empty book: no fees, no holdings (#6).
                raise InputFileError(
                    f'{args.base}: no "transactions" list (keys: '
                    f"{', '.join(sorted(map(str, _base_doc))) or 'none'})")
            base_txs = _base_doc["transactions"]
        except (InputFileError, AttributeError) as e:
            cli_diag.error(PROG, f"could not read --base {e}")
            return 2

    pool_rows = None
    if args.pool_base:
        try:
            pool_rows = list(read_work_doc(args.pool_base)
                             .get("transactions") or [])
        except (InputFileError, AttributeError) as e:
            cli_diag.error(PROG, f"could not read --pool-base {e}")
            return 2
    out = split_for_account(combined, args.account, base_txs, pool_rows)
    from taxjson.lib.json_input import dump_filing_json
    dump_filing_json(out, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
