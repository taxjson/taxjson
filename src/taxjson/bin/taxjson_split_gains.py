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
    from taxjson.lib.corporate_timeline import split_seen
    date_key = "date_settle" if basis == "settle" else "date"
    order = sorted(rows, key=lambda t: (str(t.get(date_key)
                                            or t.get("date") or ""),
                                        str(t.get("date") or ""),
                                        moment_rank(t),
                                        str(t.get("time") or "")))
    bal: Dict[str, float] = {}
    start: Dict[str, str] = {}
    seen: set = set()
    for t in order:
        sym = str(t.get("symbol") or "")
        act = t.get("action")
        if act in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            q = float(t.get("quantity") or 0.0)
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
            if not ratio or split_seen(
                    seen, sym, str(t.get("date") or ""), ratio, new,
                    account=str(t.get("account") or "")) is not None:
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


def split_for_account(combined: Dict[str, Any], account: str,
                      base_txs: Optional[List[dict]]) -> Dict[str, Any]:
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
    starts = (_position_starts(list(base_txs) + openings, _basis)
              if base_txs is not None else {})
    inventory: List[Dict[str, Any]] = []
    for row in combined.get("inventory", []):
        row_acct = row.get("account")
        if row_acct:
            if row_acct == account:
                inventory.append(dict(row))
            continue
        # Blended (account-less) pool row — apportion by the account's
        # own share count at the blended ACB per share.
        if base_txs is None:
            continue
        qty = balance_on(list(base_txs) + openings,
                         row.get("symbol", ""), "9999-12-31")
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
        # every account, it was counted once per account).
        for _f in ("total_cost", "deferred_wash", "base_total_cost"):
            if _f in row:
                r[_f] = round(float(row.get(_f) or 0.0) * share, 6)
        r["account"] = account
        r["blended_pool"] = True    # ACB/share is the s.47 blended figure
        inventory.append(r)

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
    plog = [e for e in _mh_log(combined)
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

    out = split_for_account(combined, args.account, base_txs)
    from taxjson.lib.json_input import dump_filing_json
    dump_filing_json(out, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
