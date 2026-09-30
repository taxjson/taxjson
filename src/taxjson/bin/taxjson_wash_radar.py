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
                                            split_seen)
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


class _EngineLosses:
    """The gains engine's dispositions, keyed by (account, tx id), from
    the --gains files. `rows_for(tx)` is None when the files do not
    cover the row's account and tax year (the radar then uses its own
    pool), else the engine's LOSS rows for it (possibly empty: the
    engine booked a gain, or no disposition at all)."""

    def __init__(self):
        self.by_key: Dict[tuple, list] = {}
        self.cover: Dict[str, set] = {}     # account -> {(year, basis)}

    @classmethod
    def load(cls, paths):
        self = cls()
        for p in paths or []:
            try:
                doc = json.loads(Path(p).read_text(encoding='utf-8'))
            except (OSError, ValueError) as e:
                sys.exit(f"taxjson-wash-radar: --gains {p}: {e}")
            summ = doc.get('summary') or {}
            year = str(summ.get('year') or '').strip()
            if not year:
                # A gains file with no year covers nothing we can
                # bound — refuse rather than guess.
                sys.exit(f"taxjson-wash-radar: --gains {p}: no "
                         f"summary.year — pass the pipeline's "
                         f"work/<acct>_gains*.json files")
            basis = str(summ.get('tax_date_basis') or 'settle').lower()
            buyback_wash = bool(summ.get('option_buyback_loss_superficial'))
            accts = set()
            meta_acct = ((doc.get('metadata') or {}).get('account') or '')
            if meta_acct:
                accts.add(meta_acct)
            else:
                stem = Path(p).name
                for suf in ('_gains_wash.json', '_gains.json'):
                    if stem.endswith(suf):
                        accts.add(stem[:-len(suf)])
            for r in doc.get('transactions') or []:
                if r.get('action') or not r.get('qty') or 'id' not in r:
                    continue
                acct = str(r.get('account') or meta_acct or '')
                accts.add(acct)
                rows = self.by_key.setdefault((acct, str(r['id'])), [])
                g = r.get('raw_gain', r.get('gain'))
                if g is None or float(g) >= -0.01 or r.get('grant') \
                        or r.get('deemed'):
                    continue
                # A buy-back loss on a written option (any premium
                # timing) is not fed to the engine's superficial-loss
                # solver unless the project opts in (core.py
                # _wash_eligible).
                if (not buyback_wash
                        and r.get('is_option')
                        and (r.get('direction') or '') == 'SHORT'):
                    continue
                rows.append({**r, 'raw_gain': float(g)})
            for a in accts:
                self.cover.setdefault(a, set()).add((year, basis))
        return self

    def rows_for(self, tx):
        acct = (getattr(tx, 'account', '') or '').strip()
        cov = self.cover.get(acct)
        if not cov:
            return None
        for year, basis in cov:
            d = (tx.date if basis == 'trade'
                 else (tx.date_settle or tx.date)) or ''
            if d[:4] == year:
                return self.by_key.get((acct, str(tx.id)), [])
        return None


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
    parser.add_argument("--gains", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="The engine's gains files for the taxable "
                             "accounts (work/<acct>_gains_wash.json or "
                             "_gains.json; repeatable). A disposition the "
                             "engine computed is a loss or not by the "
                             "ENGINE's raw_gain (s.47 blend across taxable "
                             "accounts, denied-loss ACB bumps, option cost "
                             "folded on exercise), not by the radar's own "
                             "per-account pool. Dispositions outside the "
                             "files' tax year fall back to the radar's pool.")
    parser.add_argument("--incomplete-history", metavar="FILE",
                        default=None,
                        help="phantoms.json: synthesize the same opening "
                             "balances the gains engine applies, so "
                             "phantom-backed positions are not shown as "
                             "shorts")
    parser.add_argument("--country", default="canada",
                        help="Project country (default: canada). Canada "
                             "(s.54): only a LONG acquisition still held "
                             "by the SAME holder at day 30 backs a denial "
                             "(taxable pool, or each registered account on "
                             "its own). usa (s.1091): a re-short triggers a "
                             "short-cover loss and an IRA purchase in the "
                             "window denies the loss even after the IRA "
                             "sold (Rev. Rul. 2008-5)")

    args = parser.parse_args()
    us_mode = str(args.country or "").strip().lower() in ("us", "usa")

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
    # A trade executed BEFORE a split that settles AFTER it: the walk
    # orders by settle date, so the pool splits first and the executed
    # (pre-split) quantity must be re-denominated into post-split units
    # (qty x ratio; money untouched) — the engine's rule (core.py, the
    # settle-lag straddle). Without it a pre-split sale of 84 left 8.4
    # phantom post-split shares held (real FFN.TO 11-for-10, 2026-07).
    _lag_splits = [t for t in transactions if t.action == 'SPLIT' and t.date]
    if _lag_splits:
        for t in transactions:
            if not (t.action in ('BUYSELL', 'ASSIGN') and t.date
                    and t.date_settle and t.date < t.date_settle):
                continue
            _f = 1.0
            for sp in _lag_splits:
                _spd = sp.date_settle or sp.date
                if (sp.symbol == t.symbol
                        and (t.date, t.time or '00:00:00')
                        < (_spd, sp.time or '00:00:00')
                        and _spd < t.date_settle
                        # a RENAME-split straddle is refused by the
                        # engine; the radar leaves the row as booked
                        and (getattr(sp, 'symbol_new', '') or '')
                        in ('', sp.symbol)
                        and float(sp.quantity or 0)):
                    _f *= float(sp.quantity)
            if abs(_f - 1.0) > 1e-12:
                t.quantity = float(t.quantity) * _f

    _tax_rows = [t for t in transactions if t._group == 'TAXABLE']
    _shl_rows = [t for t in transactions if t._group != 'TAXABLE']
    _tax_rows, _ = _drop_self_cancelling_transfers(_tax_rows)
    # The sheltered book gets the same treatment the engine's wash
    # context does (lib/pipeline._handle_transfers): moves near a
    # taxable trade of the symbol are not netted, and a netted own-
    # account move (rrsp -> rrsp2) stays as balance-only TRANSFER rows —
    # dropping both legs left the receiver short and the sender long,
    # so per-account holdings (and who bought what) came out wrong.
    _shl_rows, _ = _drop_self_cancelling_transfers(
        _shl_rows, main_transactions=_tax_rows)
    _own_moves: list = []
    _shl_rows = _net_cross_account_transfers(
        _shl_rows, main_transactions=_tax_rows, netted_out=_own_moves)
    _shl_rows = _shl_rows + _own_moves

    # Phantom openings (phantoms.json): the SAME OPENING_BALANCE rows
    # the gains pass synthesizes (lib/pipeline.prepare_books), or a
    # phantom-backed position walks negative here — shown as a short,
    # its rebuys booked as short covers with invented losses, and real
    # superficial-loss violations missed (2026-09 audit). Pairs are
    # (symbol, account), so each group's rows are walked separately.
    if args.incomplete_history:
        from taxjson.lib.phantom_holdings import (load_phantoms,
                                                  synthesize_openings)
        try:
            _phantoms = load_phantoms(Path(args.incomplete_history))
        except (OSError, ValueError) as e:
            sys.exit(f"taxjson-wash-radar: --incomplete-history "
                     f"{args.incomplete_history}: {e}")

        def _with_openings(rows, group):
            new_rows, _log = synthesize_openings(rows, _phantoms)
            for t in new_rows:
                if not hasattr(t, '_group'):
                    t._group = group
                    t._file = str(args.incomplete_history)
                    _d = t.date_settle or t.date
                    t._epoch = date_to_epoch(_d)
                    t._epoch_full = date_time_to_epoch(_d, t.time)
            return new_rows
        _tax_rows = _with_openings(_tax_rows, 'TAXABLE')
        _shl_rows = _with_openings(_shl_rows, 'SHELTERED')
    transactions = _tax_rows + _shl_rows

    # As-of filter: a trade is in the books once it is MADE (trade date
    # on or before the as-of date), even though it settles later. The
    # old settle-date filter hid every trade made today (T+1) — and
    # Friday's trades all weekend — so buy-check said SAFE right after
    # a loss sale and sell-check said SAFE right after a buy (2026-09
    # audit). Windows stay settle-based (the rows keep their settle
    # epochs); only the "is it booked yet" test uses the trade date.
    def _booked(t):
        return date_to_epoch(t.date or t.date_settle) <= today_epoch

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
        [t for t in transactions if _booked(t)],
        date_of=lambda t: t.date_settle or t.date).canonical

    account_pool_qty = {} # (symbol, group, account) -> qty
    account_pool_acb = {} # (symbol, group, account) -> total_cost
    recent_losses = {}    # alias class -> [loss dicts] (ALL in-window losses)
    # Every OPENING, all history, per alias class: who acquired how many
    # units when. The superficial-loss test is PER HOLDER (the engine,
    # core.py): the taxable book is one holder (one s.47 pool across
    # the taxable accounts), each registered account is its own, and a
    # holder backs a denial only with units it ACQUIRED inside the
    # window and still holds at its end — min(acquired, held). Units a
    # registered account held before the window neither create nor
    # back a denial. Canada counts LONG acquisitions only (a new short
    # sale or written option acquires nothing, s.54); the US keeps the
    # direction-matched s.1091(e) re-short rule.
    acq_events = {}       # alias class -> [acquisition dicts]
    # s.54 para (i): a LONG CALL is a right to acquire the underlying
    # shares, so opening one is a trigger for a LONG SHARE loss (never
    # for an option loss: options wash only against the identical
    # contract). Keyed by the UNDERLYING's alias class.
    call_acq = {}         # underlying class -> [acquisition dicts]
    from taxjson.lib.core import (OPTION_CONTRACT_SHARES, parse_option_expiry,
                                  parse_option_right, parse_option_underlying)

    def _call_underlying_cls(sym):
        if parse_option_right(sym) != 'C':
            return None
        und = parse_option_underlying(sym)
        return alias_of(und) if und else None
    seen_splits = set()   # (symbol, account, date, ratio, symbol_new) dedup

    def _holder(group, pool_acct):
        return ('TAXABLE', '') if group == 'TAXABLE' else ('SHELTERED',
                                                           pool_acct)

    def _record_acq(cls, tx, pool_acct, qty, direction):
        ev = {'epoch': tx._epoch, 'qty': qty, 'dir': direction,
              'holder': _holder(tx._group, pool_acct),
              'account': (getattr(tx, 'account', '') or '').strip(),
              'tx_obj': id(tx),
              'symbol': tx.symbol}
        acq_events.setdefault(cls, []).append(ev)
        _u = _call_underlying_cls(tx.symbol) if direction == 'LONG' else None
        if _u:
            call_acq.setdefault(_u, []).append(ev)

    engine = _EngineLosses.load(args.gains)

    def _record_loss(cls, tx, loss):
        loss.setdefault('tx_obj', id(tx))
        recent_losses.setdefault(cls, []).append(loss)

    def _engine_record(cls, tx):
        """Record the engine's losses for this row. Returns False when
        the engine does not cover the row (the caller falls back to the
        radar's own pool), True when it does (losses, if any, recorded)."""
        if tx._group != 'TAXABLE' or tx.action in ('TRANSFER',
                                                   'OPENING_BALANCE'):
            return False
        rows = engine.rows_for(tx)
        if rows is None:
            return False
        for r in rows:
            _record_loss(cls, tx, {
                'date': tx.date,
                'epoch': tx._epoch,
                'qty': abs(float(r.get('qty') or 0.0)),
                'loss': -float(r['raw_gain']),
                'direction': r.get('direction') or 'LONG',
                'currency': (getattr(tx, 'currency', '')
                             or '').strip().upper(),
                'is_option': bool(r.get('is_option',
                                        is_option_ticker(tx.symbol))),
                'source': 'engine',
            })
        return True

    for tx in transactions:
        if not _booked(tx):
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
            if split_seen(seen_splits, ticker, tx.date, ratio,
                          getattr(tx, 'symbol_new', ''),
                          account=acct) is not None:
                continue
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

        # The engine's verdict on this row, when its gains files cover
        # it: an OPENING here can still be an engine disposition when
        # the two pools disagree (the engine is the authority).
        if is_opening:
            _engine_record(cls, tx)

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
            
            # Record EVERY opening (not just the last 30 days from today):
            # the trigger test is against the ±30-day window around the
            # LOSS SALE, anchored to the sale date, not to today. Holder
            # and direction are kept: a short OPENING is never an
            # acquisition in Canada, and a registered account's buy
            # backs a denial only while THAT account still holds.
            _record_acq(cls, tx, acct, abs_qty,
                        'LONG' if qty_raw > 0 else 'SHORT')
        else:
            # Closing
            avg_cost_unit = account_pool_acb[key] / abs(current_inv) if abs(current_inv) > 1e-6 else 0.0
            closing_qty = min(abs_qty, abs(current_inv))
            cost_of_shares_closed = closing_qty * avg_cost_unit
            
            # Calculate gain only if taxable — from the ENGINE's gains
            # when they cover this row (its s.47 blended pool, the
            # s.53(1)(f) bump of an earlier denied loss, option cost
            # folded on exercise); the radar's own per-account pool
            # missed real losses that way and buy-check said SAFE
            # (2026-09 audit). The own-pool figure is the fallback for
            # rows outside the engine's tax year.
            if _engine_record(cls, tx):
                pass
            elif (tx._group == 'TAXABLE' and tx.action != 'TRANSFER'
                  # An exercise/assignment leg of an OPTION is not a
                  # disposition at a loss: the option's cost or premium
                  # folds into the share leg (s.49(3)/(3.1)). Booked
                  # here as "sold for 0", it invented a loss equal to
                  # the option's cost (2026-09 audit: DELL, IMG, QQQ).
                  and not (tx.action == 'ASSIGN'
                           and is_option_ticker(ticker))):
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
                    _record_loss(cls, tx, {
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
                    _record_acq(cls, tx, acct, leftover_qty,
                                'LONG' if qty_raw > 0 else 'SHORT')
        
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

        # Class-level totals drive the still-held decisions (the engine
        # sums balances over the whole rename class); the per-ticker
        # tax_q / shl_q are what the row displays. They coincide unless
        # a rename was booked in one account but not another.
        cls_tax_q = 0.0
        cls_shl_q = 0.0
        for (t, grp, acct), qty in account_pool_qty.items():
            if alias_of(t) != cls:
                continue
            if grp == 'TAXABLE':
                cls_tax_q += qty
                if t == ticker:
                    tax_q += qty
            else:
                cls_shl_q += qty
                if t == ticker:
                    shl_q += qty

        def _held_by_holder(direction='LONG'):
            """{holder: units} of the class each holder holds NOW on
            `direction`'s side. The engine's still-held threshold
            (1e-6): a 0.009 BTC rebuy still backs a denial."""
            h = {}
            for (t, grp, pacct), qty in account_pool_qty.items():
                if alias_of(t) != cls:
                    continue
                k = _holder(grp, pacct)
                h[k] = h.get(k, 0.0) + qty
            sgn = 1.0 if direction == 'LONG' else -1.0
            return {k: sgn * v for k, v in h.items() if sgn * v > 1e-6}

        long_held = _held_by_holder('LONG')
        calls_held = {}          # (holder, call symbol) -> contracts
        if not is_option_ticker(ticker):
            for (t, grp, pacct), qty in account_pool_qty.items():
                if _call_underlying_cls(t) != cls:
                    continue
                k = (_holder(grp, pacct), t)
                calls_held[k] = calls_held.get(k, 0.0) + qty
            calls_held = {k: v for k, v in calls_held.items() if v > 1e-6}

        def _backing(lo, hi, crit, share_loss, exclude=None, end_date=None):
            """Per-holder units backing a denial for a loss whose window
            is [lo, hi]: min(acquired in the window, held now) for each
            holder (the forward view of the engine's day-30 balance);
            a US IRA's purchase backs it whatever it holds now. Long
            calls (share losses only) back it per (holder, series),
            unless the series expires before the window's end."""
            held = long_held if crit == 'LONG' else _held_by_holder(crit)
            acq, last = {}, {}
            for e in acq_events.get(cls, []):
                if e['dir'] != crit or not (lo <= e['epoch'] <= hi):
                    continue
                if exclude is not None and e.get('tx_obj') == exclude:
                    continue       # the loss row's own leftover leg
                h = e['holder']
                acq[h] = acq.get(h, 0.0) + e['qty']
                if e['epoch'] >= last.get(h, (-1e18, ''))[0]:
                    last[h] = (e['epoch'], e['account'])
            back = {}
            for h, a in acq.items():
                cap = (a if (us_mode and h[0] == 'SHELTERED')
                       else min(a, held.get(h, 0.0)))
                if cap > 1e-6:
                    back[h] = {'units': cap, 'held': held.get(h, 0.0),
                               'last': last[h][0], 'account': last[h][1]}
            calls = {}
            if share_loss and crit == 'LONG':
                cacq, clast = {}, {}
                for e in call_acq.get(cls, []):
                    if not (lo <= e['epoch'] <= hi):
                        continue
                    if exclude is not None and e.get('tx_obj') == exclude:
                        continue
                    if end_date and (parse_option_expiry(e['symbol'])
                                     or '9999') < end_date:
                        continue
                    k = (e['holder'], e['symbol'])
                    cacq[k] = cacq.get(k, 0.0) + e['qty']
                    if e['epoch'] >= clast.get(k, (-1e18, ''))[0]:
                        clast[k] = (e['epoch'], e['account'])
                for k, a in cacq.items():
                    cap = min(a, calls_held.get(k, 0.0))
                    if cap > 1e-6:
                        calls[k] = {'contracts': cap,
                                    'held': calls_held.get(k, 0.0),
                                    'last': clast[k][0],
                                    'account': clast[k][1]}
            return back, calls

        def _units(back, calls):
            return (sum(b['units'] for b in back.values())
                    + OPTION_CONTRACT_SHARES
                    * sum(c['contracts'] for c in calls.values()))

        def _who(h, acct_label=''):
            if h[0] == 'TAXABLE':
                return 'taxable'
            return f"sheltered '{acct_label or h[1] or 'unknown'}'"

        # The engine's verdict, loss by loss: the units a holder acquired
        # inside the loss's ±30-day window AND still holds (the Canada
        # rule; LONG acquisitions only — a re-short or a new written
        # option never triggers). Denied units = min(sold, backing).
        violations = []
        for l in in_window_losses:
            crit = (l.get('direction', 'LONG') if us_mode else 'LONG')
            back, calls = _backing(
                l['epoch'] - window_sec, l['epoch'] + window_sec, crit,
                share_loss=(not l.get('is_option')
                            and l.get('direction', 'LONG') == 'LONG'),
                exclude=l.get('tx_obj'),
                end_date=epoch_to_date(l['epoch'] + window_sec))
            denied = min(float(l.get('qty') or 0.0), _units(back, calls))
            if denied > 1e-6:
                violations.append((l, denied, back, calls))

        # Anything held on either side (display threshold): a recent
        # loss with a position left is BLOCKED (don't add), else COOLING.
        held_any = (sum(abs(qty) for (t, grp, acct), qty
                        in account_pool_qty.items()
                        if alias_of(t) == cls and abs(qty) > 0.01)
                    + sum(calls_held.values()))

        adv = ""
        is_relevant = False
        clear_in = "-"
        clears_at = None   # ABSOLUTE clear date for --json-out consumers
        settle_deadline = None   # VIOLATION only: the engine's settle bound
        rescue_j = None          # VIOLATION only: who must sell what
        denied_j = None          # VIOLATION only: units denied as things stand
        at_risk_j = None         # LOCKED only: taxable units a loss sale today
        #                          would lose to a registered holder

        if in_window_losses:
            is_relevant = True
            if violations:
                # Already superficial: a replacement acquired in the
                # window is still held. Rescue = every backing holder
                # exits its WHOLE holding (min(acquired, held) reaches 0
                # only at a zero balance), settling on or before
                # loss_settle+30 — the engine's held-at-end boundary. The
                # date the user acts on is the last TRADE date that
                # settles in time; the settle bound is shown too.
                worst = min((v[0] for v in violations),
                            key=lambda l: l['epoch'])
                settle_d = epoch_to_date(worst['epoch'] + 30 * 86400)
                safe_d = last_trade_date_settling_by(
                    settle_d, worst.get('currency') or 'USD',
                    bool(worst.get('is_option')))
                days_left = int((date_to_epoch(safe_d) - today_epoch)
                                / 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                settle_deadline = settle_d
                rescue: Dict[Any, dict] = {}
                rescue_calls: Dict[Any, dict] = {}
                for _l, _d, back, calls in violations:
                    for h, b in back.items():
                        rescue[h] = b
                    for k, c in calls.items():
                        rescue_calls[k] = c
                verb = ("Cover" if (us_mode and worst.get('direction',
                                                          'LONG') == 'SHORT')
                        else "Sell")
                unit_word = ("contract(s)" if is_option_ticker(ticker)
                             else "shares")
                share_units = sum(b['held'] for b in rescue.values())
                parts = [f"{_who(h, b['account'])} {_qfmt(b['held'])}"
                         for h, b in sorted(rescue.items())]
                _what = (f"{share_units:.4f} {unit_word} ({', '.join(parts)})"
                         if rescue else "")
                if rescue_calls:
                    _cparts = [f"{_who(k[0], c['account'])} {k[1]} "
                               f"{_qfmt(c['held'])}"
                               for k, c in sorted(rescue_calls.items())]
                    _cw = (f"{sum(c['held'] for c in rescue_calls.values()):g}"
                           f" long call contract(s) ({', '.join(_cparts)})")
                    _what = f"{_what} and {_cw}" if _what else _cw
                _shl = any(h[0] == 'SHELTERED' for h in rescue) or any(
                    k[0][0] == 'SHELTERED' for k in rescue_calls)
                denied_j = round(sum(v[1] for v in violations), 6)
                rescue_j = (
                    [{"holder": ('taxable' if h[0] == 'TAXABLE'
                                 else 'sheltered'),
                      "account": ('' if h[0] == 'TAXABLE'
                                  else (b['account'] or h[1])),
                      "symbol": ticker, "qty": b['held']}
                     for h, b in sorted(rescue.items())]
                    + [{"holder": ('taxable' if k[0][0] == 'TAXABLE'
                                   else 'sheltered'),
                        "account": ('' if k[0][0] == 'TAXABLE'
                                    else (c['account'] or k[0][1])),
                        "symbol": k[1], "qty": c['held'], "call": True}
                       for k, c in sorted(rescue_calls.items())])
                adv = (f"VIOLATION: {verb} {_what} "
                       f"by {safe_d} (last TRADE date — the sale must "
                       f"SETTLE by {settle_d}) to rescue the loss"
                       f" ({_qfmt(denied_j)} units denied as things "
                       f"stand)"
                       + ("; the part a registered account backs is "
                          "denied PERMANENTLY unless that account sells "
                          "too" if _shl else "")
                       + ".")
            else:
                # No held in-window replacement: the binding constraint
                # is the LATEST loss's window (re-entry before it closes
                # disallows).
                last = max(in_window_losses, key=lambda l: l['epoch'])
                safe_d = epoch_to_date(last['epoch'] + 31 * 86400)
                days_left = int(31 - (today_epoch - last['epoch']) / 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                loss_amt = sum(l['loss'] for l in in_window_losses)
                if held_any > 0.01:
                    # Still holding, but no replacement bought in the
                    # window is held: the loss is allowed so far —
                    # buying MORE before the window closes would disallow.
                    adv = f"BLOCKED: Recent loss of ${loss_amt:.4f} on {last['date']}. Re-entry before {safe_d} will disallow the loss."
                else:
                    # Fully exited at a loss: safe, just don't re-enter
                    # until the window closes.
                    adv = f"COOLING: Loss of ${loss_amt:.4f} on {last['date']}. Safe to re-enter on {safe_d}."

        if not adv and abs(tax_q) > 0.01:
            # Forward view: a loss sale of the taxable position TODAY.
            # Its window reaches back 30 days; each holder's backing is
            # what it bought in that span and still holds.
            crit = ('SHORT' if (us_mode and cls_tax_q < 0) else 'LONG')
            lo = today_epoch - window_sec
            back, calls = _backing(
                lo, today_epoch + window_sec, crit,
                share_loss=(crit == 'LONG' and not is_option_ticker(ticker)),
                end_date=epoch_to_date(today_epoch + window_sec))
            shl_back = {h: b for h, b in back.items() if h[0] == 'SHELTERED'}
            shl_calls = {k: c for k, c in calls.items()
                         if k[0][0] == 'SHELTERED'}
            tax_long = abs(cls_tax_q)
            at_risk = min(tax_long, _units(shl_back, shl_calls))
            # Taxable acquisitions in the window (a PARTIAL sale is
            # superficial; a full exit is not) and registered buyers
            # that have since sold out (no backing today; re-arms only
            # through a new buy).
            tax_evs = [e for e in acq_events.get(cls, [])
                       if e['holder'][0] == 'TAXABLE' and e['dir'] == crit
                       and e['epoch'] >= lo]
            tax_evs += [e for e in call_acq.get(cls, [])
                        if crit == 'LONG' and not is_option_ticker(ticker)
                        and e['holder'][0] == 'TAXABLE' and e['epoch'] >= lo
                        and calls_held.get((e['holder'], e['symbol']), 0.0)
                        > 1e-6]
            tax_acq = max(tax_evs, key=lambda e: e['epoch']) if tax_evs else None
            gone = [e for e in acq_events.get(cls, [])
                    if e['holder'][0] == 'SHELTERED' and e['dir'] == crit
                    and e['epoch'] >= lo and e['holder'] not in shl_back]
            gone_last = max(gone, key=lambda e: e['epoch']) if gone else None
            shl_caveat = None
            if gone_last is not None:
                shl_caveat = (
                    f"NOTE: SHELTERED "
                    f"'{gone_last['account'] or 'unknown'}' bought "
                    f"{epoch_to_date(gone_last['epoch'])} but holds none "
                    f"of it now — that leg re-arms only if an affiliated "
                    f"account re-buys within 30 days AFTER your sale.")
            pre_window_shl = (abs(cls_shl_q) > 0.01 and not shl_back)
            if at_risk > 1e-6:
                is_relevant = True
                latest = max([b['last'] for b in shl_back.values()]
                             + [c['last'] for c in shl_calls.values()]
                             + ([tax_acq['epoch']] if tax_acq else []))
                safe_d = epoch_to_date(latest + 31 * 86400)
                days_left = int(31 - (today_epoch - latest) / 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                at_risk_j = round(at_risk, 6)
                who = "; ".join(
                    [f"'{b['account'] or h[1] or 'unknown'}' bought "
                     f"{epoch_to_date(b['last'])} and still holds "
                     f"{_qfmt(b['units'])} of those shares"
                     for h, b in sorted(shl_back.items())]
                    + [f"'{c['account'] or k[0][1] or 'unknown'}' bought "
                       f"{_qfmt(c['contracts'])} {k[1]} call(s) on "
                       f"{epoch_to_date(c['last'])}"
                       for k, c in sorted(shl_calls.items())])
                if us_mode:
                    keep = ("that portion is permanently denied (an IRA "
                            "purchase in the window denies it even if the "
                            "IRA has sold)")
                else:
                    keep = ("that portion is permanently denied unless the "
                            "registered account sells them within 30 days "
                            "after your sale")
                adv = (f"LOCKED: Recent buy in SHELTERED account(s): {who}. "
                       f"Selling the taxable position at a loss before "
                       f"{safe_d} is a superficial loss for up to "
                       f"{_qfmt(at_risk)} of your {_qfmt(tax_long)} "
                       f"shares — {keep}; the rest of the loss stands.")
                if tax_acq:
                    adv += (f" A PARTIAL loss sale is also superficial for "
                            f"the taxable buy on "
                            f"{epoch_to_date(tax_acq['epoch'])} (deferred "
                            f"into the remaining shares).")
            elif tax_acq:
                is_relevant = True
                safe_d = epoch_to_date(tax_acq['epoch'] + 31 * 86400)
                days_left = int(31 - (today_epoch - tax_acq['epoch']) / 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                _full = ("the FULL position (shares AND the long calls "
                         "bought in the window)" if any(
                             _call_underlying_cls(e['symbol']) == cls
                             and alias_of(e['symbol']) != cls
                             for e in tax_evs) else "the FULL position")
                # Taxable-only in-window buy: NOT a hard lock — the
                # still-held test fails if the ENTIRE position (incl. the
                # recent buy) is disposed, so a full exit realizes the
                # loss today. Only a PARTIAL loss sale is superficial.
                adv = (f"EXITABLE: Recent buy in "
                       f"'{tax_acq['account'] or 'unknown'}' on "
                       f"{epoch_to_date(tax_acq['epoch'])}. Selling "
                       f"{_full} at a loss is fine now; a PARTIAL loss "
                       f"sale before {safe_d} is superficial (basis "
                       f"defers into the remaining shares).")
                if pre_window_shl:
                    adv += (f" Sheltered accounts hold "
                            f"{_qfmt(abs(cls_shl_q))} sh bought before the "
                            f"window — they do not make this loss "
                            f"superficial, but a sheltered buy (a DRIP too) "
                            f"within 30 days AFTER the sale would, "
                            f"permanently.")
                if shl_caveat:
                    adv += f" {shl_caveat}"
            elif shl_caveat:
                is_relevant = True
                safe_d = epoch_to_date(gone_last['epoch'] + 31 * 86400)
                days_left = int(31 - (today_epoch - gone_last['epoch'])
                                / 86400)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                adv = (f"CAUTION: a loss sale is NOT superficial IF "
                       f"you exit your FULL taxable position. "
                       f"{shl_caveat}")

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
                # VIOLATION only: units denied as things stand, and who
                # must sell what to rescue the loss (every backing
                # holder exits its whole holding of the class).
                "denied_qty": denied_j,
                "rescue": rescue_j,
                # LOCKED only: taxable units whose loss a sale TODAY
                # would lose to a registered holder's in-window buy.
                "at_risk_qty": at_risk_j,
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
    print("  VIOLATION: You sold at a loss and a holder that BOUGHT the same security inside the ±30-day window still holds it (your taxable accounts, or a registered account). That holder sells ALL of it by the printed TRADE date to rescue the loss — the rescue sale must SETTLE within 30 days of the loss's settlement (the printed date already allows for the T+1 lag and any settlement holiday inside it). Shares a registered account held before the window never make a loss superficial.")
    print("  BLOCKED: You sold at a loss in the last 30 days. Buying now cancels that loss.")
    print("  LOCKED: A registered account bought in the last 30 days and still holds those shares. A taxable loss sale is superficial for up to that many shares (the rest of the loss stands) — permanently denied unless that account sells them within 30 days after your sale.\n  EXITABLE: You bought in the last 30 days in a taxable account. Selling the FULL position at a loss is fine; a partial loss sale is superficial (basis defers into the rest).\n  CAUTION: A registered account bought recently but has since sold what it bought. A full-exit loss sale stands unless an affiliated account re-buys within 30 days after.")
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
