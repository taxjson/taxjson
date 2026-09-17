#!/usr/bin/env python3
"""
taxjson_wash_radar.py

Consolidated Tax-Efficient Holding Advisor (CRA ITA 54 focus).
Ported from tt_wash_radar.pl.

Analyzes potential wash sales (superficial losses) based on current holdings
and recent transaction history across taxable and sheltered accounts.

Usage:
    python -m taxjson.bin.taxjson_wash_radar --taxable tax1.json --sheltered sh1.json [--date YYYY-MM-DD]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import (SplitTimeline, radar_priority,
                                            split_event_key)
from taxjson.lib.ticker_map import is_option_ticker

# UTC-noon epoch helpers: shared home in lib/dates (the DST rationale
# lives there). Names re-exported for external callers.
from taxjson.lib.dates import (  # noqa: E402,F401
    date_time_to_epoch, date_to_epoch, epoch_to_date,
    last_trade_date_settling_by, noon_utc)

# Ordering ladder lives in lib/corporate_timeline with every other sort
# profile. The radar carried a private copy for years and engine ordering
# fixes never reached it — the repo's highest fix-ratio file for exactly
# that reason. Alias kept for the sort call below and external callers.
get_tx_priority = radar_priority

# Advisory sections, most-actionable first. Each row's category is the keyword
# before the first ':' in its advisory (e.g. "LOCKED: ..." → "LOCKED"). Rows
# are grouped into these sections, alphabetical by ticker within each.
_CATEGORY_ORDER = ["VIOLATION", "BLOCKED", "LOCKED", "EXITABLE",
                   "CAUTION", "COOLING", "RISK", "CLEAR"]
_CATEGORY_TITLE = {
    "VIOLATION": "VIOLATION — wash sale; act to rescue the loss",
    "BLOCKED": "BLOCKED — do not buy",
    "LOCKED": "LOCKED — recent buy; do not sell at a loss",
    "EXITABLE": "EXITABLE — loss OK only if you exit the FULL position",
    "CAUTION": "CAUTION — recent buy, but the buyer has since sold out",
    "COOLING": "COOLING — wait before re-entering",
    "RISK": "RISK — sellable now; sheltered still holds (pause DRIPs "
            "30 days after selling)",
    "CLEAR": "CLEAR — safe to sell at a loss",
}


def _advisory_category(adv: str) -> str:
    return adv.split(":", 1)[0].strip() if adv else ""


def _category_rank(cat: str) -> int:
    return _CATEGORY_ORDER.index(cat) if cat in _CATEGORY_ORDER else len(_CATEGORY_ORDER)


def _qfmt(x: float) -> str:
    """Trim a share quantity for display: 400.0000 -> 400, 3.3333 -> 3.3333."""
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-", "-0") else s


def main():
    parser = argparse.ArgumentParser(description="Tax-Efficient Holding Advisor")
    # nargs='+' + extend: both `--taxable a b` (historical) and repeated
    # `--taxable a --taxable b` (A2 composability) work.
    parser.add_argument("--taxable", nargs='+', action='extend', default=[],
                        required=True, metavar="FILE",
                        help="Taxable transaction JSON files (repeatable)")
    parser.add_argument("--sheltered", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="Sheltered transaction JSON files (repeatable)")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--all", "-a", action="store_true", help="Show all tickers, not just relevant ones")
    parser.add_argument("--json-out", metavar="PATH", default=None,
                        help="Also write the report as structured JSON (the "
                             ".rpt text is unchanged; consumers like the web "
                             "UI read this and compute countdowns at VIEW "
                             "time from the absolute clears_at dates)")
    parser.add_argument("--account", default="",
                        help="Account label stamped into --json-out")
    parser.add_argument("--json", action="store_true",
                        help="Print the structured radar document (the "
                             "--json-out payload) to stdout instead of "
                             "the text report")

    args = parser.parse_args()

    orig_stdout = sys.stdout
    if args.json:
        # One code path builds both outputs: the text report renders
        # into a discarded buffer and only the structured payload is
        # printed at the end. Restore to the SAVED stream, not
        # sys.__stdout__ — under in-process dispatch with output
        # capture, __stdout__ is the real terminal and the JSON would
        # escape the capture.
        import io
        sys.stdout = io.StringIO()

    if args.date:
        try:
            today_dt = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            sys.exit(f"taxjson-wash-radar: --date {args.date!r} is not "
                     f"a valid YYYY-MM-DD date")
    else:
        today_dt = datetime.now()

    # Noon UTC on today's (local) calendar date — same basis as
    # date_to_epoch so every days_since is an exact whole number.
    today_dt = noon_utc(today_dt)
    today_epoch = today_dt.timestamp()

    window_sec = 30 * 86400

    all_tx_data: List[Dict[str, Any]] = []

    def load_files(files, group_type):
        for f in files:
            path = Path(f)
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                txs = data.get("transactions", [])
                for tx in txs:
                    # Enrich with source info
                    tx['_group'] = group_type
                    tx['_file'] = str(path)
                    all_tx_data.append(tx)

    load_files(args.taxable, 'TAXABLE')
    load_files(args.sheltered, 'SHELTERED')

    # Convert to objects and sort
    transactions = []
    import inspect
    sig = inspect.signature(TaxTransaction)
    valid_keys = sig.parameters.keys()

    for t in all_tx_data:
        # Create a clean dict for TaxTransaction
        d = {k: v for k, v in t.items() if not k.startswith('_')}
        
        # Handle aliases
        if 'qty' in d and 'quantity' not in d:
            d['quantity'] = d.pop('qty')
        
        # Filter to only include valid TaxTransaction fields
        d = {k: v for k, v in d.items() if k in valid_keys}
        
        try:
            tx_obj = TaxTransaction(**d)
            # Restore metadata
            tx_obj._group = t['_group']
            tx_obj._file = t['_file']
            # SETTLEMENT-basis epochs, matching the gains engine's CRA
            # window (settle end-to-end). Trade-date windows disagreed
            # with the engine by 1-2 business days at the ±30d edges: a
            # 31-trade-day gap that is 29 settle-days showed 0 VIOLATIONS
            # here while the engine disallowed the full loss.
            _d = tx_obj.date_settle or tx_obj.date
            tx_obj._epoch = date_to_epoch(_d)
            tx_obj._epoch_full = date_time_to_epoch(_d, tx_obj.time)
            transactions.append(tx_obj)
        except TypeError as e:
            if args.verbose:
                print(f"Warning: Skipping invalid transaction in {t.get('_file')}: {e}", file=sys.stderr)

    # Custody-move TRANSFER noise (broker moves, cancel/rebook
    # restatements, registered-to-registered moves) must be invisible
    # here exactly as it is to the gains engine: raw out/in legs booked
    # book-value ACB swings that inflated loss estimates (or erased
    # them, for zero-net in-legs), and the in-leg registered as an
    # "acquisition" that turned a pure custody move into a VIOLATION.
    from taxjson.lib.pipeline import (_drop_self_cancelling_transfers,
                                      _net_cross_account_transfers)
    _tax_rows = [t for t in transactions if t._group == 'TAXABLE']
    _shl_rows = [t for t in transactions if t._group != 'TAXABLE']
    _tax_rows, _ = _drop_self_cancelling_transfers(_tax_rows)
    _shl_rows, _ = _drop_self_cancelling_transfers(_shl_rows)
    _shl_rows = _net_cross_account_transfers(_shl_rows)
    transactions = _tax_rows + _shl_rows

    transactions.sort(key=lambda x: (x._epoch_full, get_tx_priority(x)))

    # SPLIT-rename equivalence classes — the SAME union the engine
    # matches on (core.py: alias_of = split_timeline.canonical; a loss
    # on OLD.TO considers NEW.TO buys as triggers and NEW.TO shares as
    # still-held). The radar keyed its loss/trigger/acquisition maps by
    # RAW ticker, so a loss under the old name followed by a rebuy
    # under the new one showed COOLING + EXITABLE while the engine
    # denied the loss (2026-09 audit). Pools stay keyed by raw symbol
    # (rows print per ticker); only the MATCHING is class-level.
    alias_of = SplitTimeline.from_transactions(
        [t for t in transactions if t._epoch <= today_epoch],
        date_of=lambda t: t.date_settle or t.date).canonical

    account_pool_qty = {} # (symbol, group, account) -> qty
    account_pool_acb = {} # (symbol, group, account) -> total_cost
    global_lacq = {}      # alias class -> {group -> {epoch, account}} latest buy per group
    recent_losses = {}    # alias class -> [loss dicts] (ALL in-window losses)
    open_events = {}      # alias class -> [(epoch, direction)] opening events, all history
    seen_splits = set()   # (symbol, account, date, ratio, symbol_new) dedup

    for tx in transactions:
        if tx._epoch > today_epoch:
            continue

        # Cash-flow events don't change the share count. Their `quantity` is
        # the number of shares the dividend/tax/interest was computed ON (for
        # reconciliation), NOT shares acquired — counting it inflated the
        # running position (e.g. two WSP.TO dividends of 50 + 50.053 made a
        # fully-sold sheltered position look like +100.0533 held, triggering a
        # bogus "sheltered holdings exist" wash-sale warning). Only BUYSELL /
        # ASSIGN / SPLIT / TRANSFER / OPENING_BALANCE (+ ADJUST for ACB) move
        # the pool, matching the gains engine and phantom-holdings walks.
        if tx.action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST',
                         'FEE', 'DISALLOW'):
            continue

        ticker = tx.symbol
        cls = alias_of(ticker)     # rename-class key for the matching maps
        # Pool per (ticker, group, account) — NOT per file. All sheltered
        # accounts share one --sheltered file, so a per-file pool would apply a
        # split that happened in ONE registered account (e.g. a 10:1 in the
        # TFSA) to the COMBINED sheltered position. Keying by account scopes
        # each split to its own pool; quantities are still summed per group at
        # report time, so the taxable/sheltered totals are unchanged for
        # non-split tickers.
        group = tx._group
        acct = (getattr(tx, 'account', '') or '').strip() or tx._file
        key = (ticker, group, acct)

        if tx.action == 'ADJUST':
            account_pool_acb[key] = account_pool_acb.get(key, 0.0) + tx.net_amount
            continue

        if tx.action == 'SPLIT':
            # Apply the split MULTIPLICATIVELY to the share count, mirroring the
            # gains engine (`running *= ratio`); the ratio is in `quantity`
            # (e.g. 1.1 for a 1.1-for-1). Total ACB is unchanged by a split —
            # only per-share cost changes. The old no-op left the running
            # quantity off by the split factor, so post-split sells underflowed
            # the pool into a phantom negative position (LFE.TO showed -780 for
            # a holding that is actually closed). A SPLIT carrying a non-empty
            # `symbol_new` also renames the pool to the new ticker.
            ratio = tx.quantity
            if not ratio or abs(ratio) <= 1e-12:
                continue
            # One corporate event = one application per account pool: a
            # single account fed by TWO brokers (margin dir with IB + RBC
            # CSVs) carries the same split once per broker, and their ids
            # differ so upstream dedup can't collapse them. Keyed by the
            # shared split-event identity (normalized against the mapped
            # ticker, matching this walk's pool keys).
            _skey = split_event_key(ticker, tx.date, ratio,
                                    getattr(tx, 'symbol_new', ''),
                                    account=acct)
            if _skey in seen_splits:
                continue
            seen_splits.add(_skey)
            scaled_qty = account_pool_qty.get(key, 0.0) * ratio
            acb = account_pool_acb.get(key, 0.0)
            new_sym = (getattr(tx, 'symbol_new', '') or '').strip()
            if new_sym and new_sym != ticker:
                account_pool_qty.pop(key, None)
                account_pool_acb.pop(key, None)
                dst = (new_sym, group, acct)
                account_pool_qty[dst] = account_pool_qty.get(dst, 0.0) + scaled_qty
                account_pool_acb[dst] = account_pool_acb.get(dst, 0.0) + acb
            else:
                account_pool_qty[key] = scaled_qty
                account_pool_acb[key] = acb
            continue

        qty_raw = tx.quantity
        abs_qty = abs(qty_raw)
        
        if key not in account_pool_qty:
            account_pool_qty[key] = 0.0
            account_pool_acb[key] = 0.0
            
        current_inv = account_pool_qty[key]
        is_opening = (current_inv > 1e-6 and qty_raw > 0) or (current_inv < -1e-6 and qty_raw < 0) or (abs(current_inv) <= 1e-6)

        if is_opening:
            # Net amount in TaxTransaction includes fees. A TRANSFER-in
            # with no stated book value (Questrade emits 0.0) must not
            # zero-dilute the pool's ACB — carry the average cost.
            if tx.action == 'TRANSFER' and abs(tx.net_amount) <= 0.005 \
                    and abs(current_inv) > 1e-6:
                account_pool_acb[key] += (
                    account_pool_acb[key] / abs(current_inv)) * abs_qty
            else:
                account_pool_acb[key] += abs(tx.net_amount)

            if tx.action in ('TRANSFER', 'OPENING_BALANCE'):
                # Moving/synthesizing your own shares acquires nothing:
                # neither is a superficial-loss trigger (the gains
                # engine excludes both — core.py's trigger filter), so
                # neither may feed global_lacq/open_events. Quantity and
                # value still moved above.
                account_pool_qty[key] += qty_raw
                continue
            
            # Track acquisitions globally, keeping the latest buy in EACH group
            # (taxable vs sheltered) separately. A buy in a sheltered/registered
            # account is a superficial-loss trigger too (affiliated person, CRA
            # ITA 54), but it is permanently denied, whereas a taxable buy can be
            # rescued by a full exit. So the advisory below must know about BOTH
            # recent buys, not just whichever happened to land last — keeping
            # only the single latest would drop the group distinction (and its
            # very different tax consequence) when both account types bought
            # inside the window.
            grp_acq = global_lacq.setdefault(cls, {})
            prev = grp_acq.get(tx._group)
            if prev is None or tx._epoch > prev['epoch']:
                grp_acq[tx._group] = {
                    'epoch': tx._epoch,
                    'account': (getattr(tx, 'account', '') or '').strip(),
                }

            # Record EVERY opening event (not just the last 30 days from
            # today): the superficial-loss trigger test is against the CRA
            # ±30-day window around the LOSS SALE (ITA 54), which is anchored
            # to the sale date, not to today. Gating on today's window made a
            # trigger bought shortly before the loss "age out" as the report
            # date advanced, silently downgrading VIOLATION → BLOCKED and
            # hiding the sell-by rescue deadline. Direction is recorded so a
            # short-OPENING sale is never treated as a trigger for a LONG
            # loss (and vice versa) — it previously produced "VIOLATION:
            # Sell N shares" advice on a short position, i.e. telling the
            # user to increase the short.
            open_events.setdefault(cls, []).append(
                (tx._epoch, 'LONG' if qty_raw > 0 else 'SHORT'))
        else:
            # Closing
            avg_cost_unit = account_pool_acb[key] / abs(current_inv) if abs(current_inv) > 1e-6 else 0.0
            closing_qty = min(abs_qty, abs(current_inv))
            cost_of_shares_closed = closing_qty * avg_cost_unit
            
            # Calculate gain only if taxable
            if tx._group == 'TAXABLE' and tx.action != 'TRANSFER':
                # Prorate proceeds to the CLOSED portion: a sale that
                # crosses zero (sell 150 holding 100) otherwise nets the
                # FULL proceeds against only the closed shares' cost —
                # a real loss computed as a gain, silently downgrading
                # BLOCKED to CLEAR (2026-09 audit).
                proceeds = abs(tx.net_amount) * (closing_qty / abs_qty
                                                 if abs_qty > 1e-9
                                                 else 1.0)
                gain = (proceeds - cost_of_shares_closed) if current_inv > 0 else (cost_of_shares_closed - proceeds)
                
                if gain < -0.01:
                    # Keep EVERY loss (a list, not last-wins): a newer small
                    # loss used to overwrite an older loss whose VIOLATION
                    # was still active, silently dropping its rescue
                    # deadline from the report.
                    recent_losses.setdefault(cls, []).append({
                        'date': tx.date,
                        'epoch': tx._epoch,
                        'qty': closing_qty,
                        'loss': abs(gain),
                        'direction': 'LONG' if current_inv > 0 else 'SHORT',
                        # Settlement market for the rescue-deadline
                        # walk-back (T+1 CAD/USD equities; options T+1).
                        'currency': (getattr(tx, 'currency', '')
                                     or '').strip().upper(),
                        'is_option': is_option_ticker(ticker),
                    })
            
            account_pool_acb[key] -= cost_of_shares_closed
            leftover_qty = abs_qty - closing_qty
            if leftover_qty > 1e-6:
                # Reset ACB for the cross-over or remaining
                account_pool_acb[key] = (leftover_qty / abs_qty) * abs(tx.net_amount)
                # The leftover OPENS a fresh position on the flip side —
                # record it like any opening, or the new short/long is
                # invisible to the trigger walk (2026-09 audit).
                if tx.action not in ('TRANSFER', 'OPENING_BALANCE'):
                    _dir = 'LONG' if qty_raw > 0 else 'SHORT'
                    grp_acq = global_lacq.setdefault(cls, {})
                    prev = grp_acq.get(tx._group)
                    if prev is None or tx._epoch > prev['epoch']:
                        grp_acq[tx._group] = {
                            'epoch': tx._epoch,
                            'account': (getattr(tx, 'account', '')
                                        or '').strip(),
                        }
                    open_events.setdefault(cls, []).append(
                        (tx._epoch, _dir))
        
        account_pool_qty[key] += qty_raw
        if abs(account_pool_qty[key]) < 1e-6:
            account_pool_acb[key] = 0.0
            account_pool_qty[key] = 0.0

    # Reporting
    table_data = []
    row_records = []   # structured twins of table_data rows (for --json-out)
    all_tickers = set(t for t, g, a in account_pool_qty.keys())
    all_tickers.update(recent_losses.keys())

    sorted_tickers = sorted(all_tickers, key=lambda t: (is_option_ticker(t), t))

    for ticker in sorted_tickers:
        tax_q = 0.0
        shl_q = 0.0
        cls = alias_of(ticker)

        # ALL losses still inside their own 30-day windows — not just the
        # latest one. A newer small loss must not hide an older loss whose
        # VIOLATION (and sell-by rescue deadline) is still active.
        in_window_losses = [
            l for l in recent_losses.get(cls, [])
            if (today_epoch - l['epoch']) / 86400 <= 30
        ]

        def _loss_has_trigger(l):
            # Superficial-loss trigger: a DIRECTION-MATCHED opening inside
            # the CRA ±30-day window AROUND THE LOSS SALE ([sale-30d,
            # sale+30d], ITA 54) — not "the last 30 days from today", and
            # never a short-opening sale triggering a LONG loss (or vice
            # versa).
            return any(abs(e - l['epoch']) <= window_sec
                       and d == l.get('direction', 'LONG')
                       for e, d in open_events.get(cls, []))

        triggered = [l for l in in_window_losses if _loss_has_trigger(l)]

        # Class-level totals drive the still-held decisions (the engine
        # sums bal_at_end over the whole rename class); the per-ticker
        # tax_q / shl_q are what the row displays. They coincide unless
        # a rename was booked in one account but not another.
        cls_tax_q = 0.0
        cls_shl_q = 0.0
        for (t, grp, acct), qty in account_pool_qty.items():
            if alias_of(t) != cls:
                continue
            # Group ('TAXABLE'/'SHELTERED') is carried in the pool key now,
            # so no file-path lookup is needed.
            if grp == 'TAXABLE':
                cls_tax_q += qty
                if t == ticker:
                    tax_q += qty
            else:
                cls_shl_q += qty
                if t == ticker:
                    shl_q += qty

        held_qty = sum(abs(qty) for (t, grp, acct), qty
                       in account_pool_qty.items()
                       if alias_of(t) == cls and abs(qty) > 0.01)

        adv = ""
        is_relevant = False
        clear_in = "-"
        clears_at = None   # ABSOLUTE clear date for --json-out consumers
        settle_deadline = None   # VIOLATION only: the engine's settle bound

        if in_window_losses:
            is_relevant = True
            if triggered and held_qty > 0.01:
                # Trigger acquired in the window AND still holding: already
                # superficial — rescue needs a full exit that SETTLES on
                # or before loss_settle+30, the engine's own held-at-end
                # boundary (core.py end_window_date = loss sort/settle
                # date + 30d; a transaction whose settle date is <= that
                # counts toward bal_at_end). The other branches use +31
                # because they answer a DIFFERENT question — the first
                # safe day to re-enter — where the boundary flips.
                # Printing +31 here told the user a sell-by date one day
                # past the rescue window; printing the SETTLE bound as
                # the trade date did the same thing one lag later: a
                # user who traded ON it settled T+1 and the loss was
                # denied (permanently, for a registered-account trigger).
                # So the date the user acts on is the last TRADE date
                # that settles in time; the settle bound is shown too.
                worst = min(triggered, key=lambda l: l['epoch'])
                settle_d = epoch_to_date(worst['epoch'] + 30 * 86400)
                safe_d = last_trade_date_settling_by(
                    settle_d, worst.get('currency') or 'USD',
                    bool(worst.get('is_option')))
                days_left = int((date_to_epoch(safe_d) - today_epoch)
                                / 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                settle_deadline = settle_d
                verb = ("Sell" if worst.get('direction', 'LONG') == 'LONG'
                        else "Cover")
                adv = (f"VIOLATION: {verb} {held_qty:.4f} shares (globally) "
                       f"by {safe_d} (last TRADE date — the sale must "
                       f"SETTLE by {settle_d}) to rescue the loss.")
            else:
                # No active trigger: the binding constraint is the LATEST
                # loss's window (re-entry before it closes disallows).
                last = max(in_window_losses, key=lambda l: l['epoch'])
                safe_d = epoch_to_date(last['epoch'] + 31 * 86400)
                days_left = int(31 - (today_epoch - last['epoch']) / 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                loss_amt = sum(l['loss'] for l in in_window_losses)
                if held_qty > 0.01:
                    # Still holding, but no in-window acquisition: the loss
                    # is allowed so far — buying MORE before the window
                    # closes would disallow it.
                    adv = f"BLOCKED: Recent loss of ${loss_amt:.4f} on {last['date']}. Re-entry before {safe_d} will disallow the loss."
                else:
                    # Fully exited at a loss: safe, just don't re-enter
                    # until the window closes.
                    adv = f"COOLING: Loss of ${loss_amt:.4f} on {last['date']}. Safe to re-enter on {safe_d}."
        
        if not adv and abs(tax_q) > 0.01:
            grp_acq = global_lacq.get(cls) or {}
            tax_acq = grp_acq.get('TAXABLE')
            shl_acq = grp_acq.get('SHELTERED')
            # Drop acquisitions that have already aged out of the 30-day window.
            if tax_acq and (today_epoch - tax_acq['epoch']) > window_sec:
                tax_acq = None
            if shl_acq and (today_epoch - shl_acq['epoch']) > window_sec:
                shl_acq = None
            # s. 40(2)(g) still-held test: the loss is superficial
            # only if IDENTICAL property is still held by the
            # affiliated group at the end of the 30-day period. Shares
            # are fungible — the test is against the GROUP's total
            # holding of the ticker, NOT the acquiring account's own
            # pool (checking only the acquirer flagged a bare CAUTION
            # on ALK.TO while OTHER sheltered accounts held 17,200
            # sh). Downgrade only when the ENTIRE sheltered side is
            # flat; a taxable in-window buy keeps its LOCKED "exit
            # FULL position" advice regardless (the seller's own
            # retained shares are identical property too).
            shl_caveat = None
            if shl_acq and abs(cls_shl_q) <= 0.01:
                # Group flat: the sheltered leg cannot make the loss
                # superficial TODAY (fungibility satisfied — nothing
                # identical is held sheltered); it only re-arms via a
                # re-buy within 30 days after the sale. Drop the leg
                # whether or not a taxable in-window buy remains — the
                # combined branch used to keep claiming "permanently
                # denied" (real SLV.US case: margin bought 06-29,
                # lira bought 07-08 then ALL sheltered exited).
                shl_caveat = (
                    f"NOTE: SHELTERED "
                    f"'{shl_acq['account'] or 'unknown'}' bought "
                    f"{epoch_to_date(shl_acq['epoch'])} but all "
                    f"sheltered accounts are now at 0 — that leg "
                    f"re-arms only if an affiliated account re-buys "
                    f"within 30 days AFTER your sale.")
                shl_acq = None
            if shl_caveat and not tax_acq:
                is_relevant = True
                adv = (f"CAUTION: a loss sale is NOT superficial IF "
                       f"you exit your FULL taxable position. "
                       f"{shl_caveat}")
            if tax_acq or shl_acq:
                is_relevant = True
                # The lock clears only once the MOST RECENT in-window buy (in
                # any account) ages past 30 days.
                latest_epoch = max(a['epoch'] for a in (tax_acq, shl_acq) if a)
                days_since = (today_epoch - latest_epoch) / 86400
                days_left = int(31 - days_since)
                safe_d = epoch_to_date(latest_epoch + 31 * 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                tax_d = (epoch_to_date(tax_acq['epoch'])
                         if tax_acq else None)
                shl_d = (epoch_to_date(shl_acq['epoch'])
                         if shl_acq else None)
                if tax_acq and shl_acq:
                    adv = (f"LOCKED: Recent buys — taxable '{tax_acq['account'] or 'unknown'}' "
                           f"on {tax_d} and SHELTERED '{shl_acq['account'] or 'unknown'}' on {shl_d}. "
                           f"Selling the taxable position at a loss before {safe_d} is a superficial "
                           f"loss; the portion matched to the registered-account buy is permanently "
                           f"denied (cannot be rescued by selling).")
                elif shl_acq:
                    adv = (f"LOCKED: Recent buy in SHELTERED account '{shl_acq['account'] or 'unknown'}' "
                           f"on {shl_d}. Selling the taxable position at a loss before {safe_d} is a "
                           f"superficial loss (permanently denied — a registered-account buy cannot be "
                           f"rescued by selling).")
                elif abs(cls_shl_q) > 0.01:
                    # Taxable in-window buy AND a standing sheltered
                    # holding (however old): a full TAXABLE exit does
                    # NOT fully rescue the loss. CRA's denial is
                    # min(sold, acquired-in-window, held at +30) and
                    # the sheltered shares keep the still-held term
                    # alive — up to that many shares' loss is denied
                    # PERMANENTLY (registered basis is unrecoverable).
                    # The plain "full exit is fine" advisory encoded
                    # exactly the heuristic a real FFH.TO trade
                    # disproved (2026-09).
                    adv = (f"EXITABLE: Recent buy in "
                           f"'{tax_acq['account'] or 'unknown'}' on "
                           f"{tax_d}, and sheltered accounts still "
                           f"hold {abs(cls_shl_q):.4f} sh. Even a FULL "
                           f"taxable exit leaves up to that many "
                           f"shares' loss PERMANENTLY denied "
                           f"(still-held via the sheltered side); a "
                           f"PARTIAL loss sale before {safe_d} is "
                           f"superficial too. Fully clear only after "
                           f"{safe_d}, or accept the denied portion.")
                else:
                    # Taxable-only in-window buy: NOT a hard lock — the
                    # still-held test fails if the ENTIRE position
                    # (incl. the recent buy) is disposed, so a full
                    # exit realizes the loss today. Only a PARTIAL
                    # loss sale before safe_d is superficial. A bare
                    # "LOCKED(clears:...)" in harvest read as "cannot
                    # sell at all" (real TA.TO case).
                    adv = (f"EXITABLE: Recent buy in "
                           f"'{tax_acq['account'] or 'unknown'}' on "
                           f"{tax_d}. Selling the FULL position at a "
                           f"loss is fine now; a PARTIAL loss sale "
                           f"before {safe_d} is superficial (basis "
                           f"defers into the remaining shares)."
                           + (f" {shl_caveat}" if shl_caveat else ""))

        if not adv and abs(tax_q) > 0.01 and abs(cls_shl_q) > 0.01:
            # No acquisition on EITHER side within the past 30 days:
            # s.40(2)(g) needs an acquisition INSIDE the ±30-day
            # window, not mere ownership — so a loss sale TODAY is
            # claimable. The sheltered holding matters for the FORWARD
            # half only: an affiliated add (a DRIP is the classic)
            # within 30 days after the sale denies the loss
            # PERMANENTLY (registered-account basis is unrecoverable).
            is_relevant = True
            adv = ("RISK: Sellable at a loss NOW (no buys in the last "
                   "30 days) — but sheltered accounts still hold, so "
                   "any affiliated buy (including a DRIP) within 30 "
                   "days AFTER the sale denies the loss PERMANENTLY. "
                   "Pause DRIPs/sheltered adds for 30 days, or sell "
                   "the sheltered shares too.")

        if not adv:
            if abs(tax_q) > 0.01 or abs(shl_q) > 0.01:
                is_relevant = True
                adv = "CLEAR: No recent buys. Safe to sell at a loss (do not repurchase for 30 days)."
            # (Fully-exited recent losses are routed to COOLING in the loss
            # branch above.)

        if args.all or is_relevant:
            table_data.append([ticker, _qfmt(tax_q), _qfmt(shl_q), clear_in, adv])
            row_records.append({
                "ticker": ticker,
                "taxable_qty": tax_q, "taxable_display": _qfmt(tax_q),
                "sheltered_qty": shl_q, "sheltered_display": _qfmt(shl_q),
                "clears_at": clears_at,
                # VIOLATION only: the engine's settle-date bound behind
                # the trade-date clears_at (None elsewhere).
                "settle_deadline": settle_deadline,
                "clears_in_at_generation": clear_in,
                "advisory": adv,
                "category": _advisory_category(adv),
            })

    # Dynamic Formatting
    headers = ["TICKER", "TAXABLE", "SHELTERED", "CLEARS", "ADVISORY / ACTION REQUIRED"]
    widths = [len(h) for h in headers]
    for row in table_data:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    fmt = " | ".join(f"{{:<{w}}}" for w in widths)

    total_width = sum(widths) + 3 * (len(widths) - 1)

    # Group rows by advisory category (alphabetical by ticker within each).
    table_data.sort(key=lambda r: (_category_rank(_advisory_category(r[4])), r[0]))
    by_cat: Dict[str, list] = {}
    for r in table_data:
        by_cat.setdefault(_advisory_category(r[4]), []).append(r)

    # Decorative rules capped at a sane width (the advisory column can push the
    # table well past 200 chars; a full-width bar would be an eyesore).
    head_w = min(total_width, 96)

    # The column header repeats under each section divider (rather than once at
    # the top) so a long report stays readable when scrolled past a section.
    header_line = fmt.format(*headers)
    rule_line = "-+-".join("-" * w for w in widths)

    # Always print every advisory section in the fixed order — even empty ones
    # — so a clean `VIOLATION (0)` / `BLOCKED (0)` is visible at a glance rather
    # than silently absent. Any extra category (e.g. OTHER from --all) follows.
    extras = [c for c in by_cat if c not in _CATEGORY_ORDER]
    for i, cat in enumerate(_CATEGORY_ORDER + extras):
        rows_c = by_cat.get(cat, [])
        if i:
            print()
        title = _CATEGORY_TITLE.get(cat, cat or "OTHER")
        print(f"--- {title} ({len(rows_c)}) ".ljust(total_width, "-"))
        if rows_c:
            print(header_line)
            print(rule_line)
            for row in rows_c:
                print(fmt.format(*row))
        else:
            print("(none)")

    if args.json_out or args.json:
        # Structured sidecar: same rows/grouping as the printed .rpt, plus
        # ABSOLUTE clears_at dates so consumers (the web UI) can compute
        # "clears in Nd" at VIEW time instead of serving the generation-day
        # countdown forever.
        recs_by_cat = {}
        row_records.sort(key=lambda r: (_category_rank(r["category"]),
                                        r["ticker"]))
        for rec in row_records:
            recs_by_cat.setdefault(rec["category"], []).append(rec)
        extras_j = [c for c in recs_by_cat if c not in _CATEGORY_ORDER]
        sections_j = [{
            "category": cat,
            "title": _CATEGORY_TITLE.get(cat, cat or "OTHER"),
            "rows": recs_by_cat.get(cat, []),
        } for cat in _CATEGORY_ORDER + extras_j]
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "as_of_date": today_dt.strftime("%Y-%m-%d"),
            "account": args.account,
            "include_all": bool(args.all),
            "sections": sections_j,
        }
        if args.json_out:
            out_path = Path(args.json_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload, indent=2,
                                           sort_keys=True)
                                + "\n", encoding="utf-8")

    print("-" * head_w)
    print("Definitions:")
    print("  VIOLATION: You sold at a loss but still hold the same security. Sell it by the printed TRADE date to rescue the loss — the rescue sale must SETTLE within 30 days of the loss's settlement (T+1 assumed; an exchange holiday inside the lag moves the last safe trade date one day EARLIER).")
    print("  BLOCKED: You sold at a loss in the last 30 days. Buying now cancels that loss.")
    print("  LOCKED: A sheltered/registered account bought in the last 30 days and the sheltered side still holds. Selling taxable at a loss is superficial; the registered-matched portion is denied for good.\n  EXITABLE: You bought in the last 30 days in a taxable account. Selling the FULL position at a loss is fine; a partial loss sale is superficial (basis defers into the rest).\n  CAUTION: A sheltered account bought recently but all sheltered accounts are now at 0. A full-exit loss sale stands unless an affiliated account re-buys within 30 days after.")
    print("  COOLING: You recently sold out at a loss. Wait 30 days from the sale before buying back.")
    print("  RISK: Sellable at a loss NOW — but a sheltered account still holds, so an affiliated buy (e.g. a DRIP) within 30 days AFTER the sale denies the loss permanently. Pause sheltered adds for 30 days.")
    print("  CLEAR: No recent buys. Safe to sell at a loss (don't buy back for 30 days).")
    print()

    if args.json:
        # Discard the buffered text report and emit only the payload.
        sys.stdout = orig_stdout
        print(json.dumps(payload, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
