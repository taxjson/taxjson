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
                  total_disallowed, year)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib import cli_diag
from taxjson.bin.taxjson_apply_distributions import balance_on

PROG = "taxjson-split-gains"


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
        qty = balance_on(base_txs, row.get("symbol", ""), "9999-12-31")
        total_qty = float(row.get("qty") or 0.0)
        if abs(qty) < 1e-9 or abs(total_qty) < 1e-9:
            continue
        share = qty / total_qty
        r = dict(row)
        r["qty"] = round(qty, 6)
        r["total_cost"] = round(float(row.get("total_cost") or 0.0)
                                * share, 6)
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
        bucket = fees.setdefault(
            curr, {"stocks": 0.0, "options": 0.0, "total": 0.0})
        bucket["options" if is_opt else "stocks"] += fee
        bucket["total"] += fee
    summary["total_fees_by_currency"] = fees

    return {
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("combined_json", type=Path)
    ap.add_argument("--account", required=True)
    ap.add_argument("--base", type=Path, default=None,
                    help="The account's base book (for apportioning "
                         "blended account-less inventory rows)")
    args = ap.parse_args(argv)

    try:
        combined = json.loads(args.combined_json.read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        cli_diag.error(PROG, f"could not read {args.combined_json}: {e}")
        return 2
    base_txs = None
    if args.base and args.base.exists():
        try:
            base_txs = json.loads(args.base.read_text(
                encoding="utf-8")).get("transactions", [])
        except (OSError, json.JSONDecodeError) as e:
            cli_diag.warn(PROG, f"could not read {args.base}: {e} — "
                                f"blended inventory rows omitted")

    out = split_for_account(combined, args.account, base_txs)
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
