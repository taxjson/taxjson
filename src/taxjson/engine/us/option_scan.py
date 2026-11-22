"""The warn-only scan of calls bought as replacements of a share loss.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence
from datetime import datetime, timedelta

from taxjson.model import money as M
from taxjson.lib.option_symbols import (
    _FUTURES_PREFIX_RE, is_option_symbol, parse_option_right, parse_option_underlying,
)
from taxjson.lib.contract_size import option_contract_size
from taxjson.engine.assign import class_root_aliases
from taxjson.engine.replacement import _opening_option_buys

from taxjson.engine.us.terms import share_qty

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction


def detect_option_replacement_matches(loss_entries: Sequence[Dict[str, Any]], events: Sequence[TaxTransaction], *, date_of: Callable[[TaxTransaction], str],
                                      canonical: Optional[Callable[[str], str]] = None, statute_label: str = '',
                                      window_days: int = 30,
                                      check_held_at_end: bool = False) -> List[Dict[str, Any]]:
    """WARN-ONLY call-as-replacement scan, used by the (experimental) US
    engine; the Canada engine enforces the same rule in its solver.

      'call_vs_share_loss': loss on LONG shares + LONG CALL acquired on
          the same underlying inside the ±window (IRS §1091 "option to
          acquire"; CRA s.54 "a right to acquire").

    One-way, per the owner's policy (2026-09-29): options replace shares,
    never the reverse, and an option is replaced only by the identical
    contract (the engines' own symbol matching). A put is a right to
    SELL, never replacement property. Detection only — numbers unchanged.

    `loss_entries`: dicts with symbol, date (already on the calling
    engine's window basis), amount (negative), id, direction, and
    optionally `open_qty`: the loss shares no share purchase replaced
    (the engine's matches; each loss share is matched once, US-WASH-20),
    the most a call can still replace — a loss replaced in full is not
    flagged (backlog TJ-0467: the flag put the whole loss at risk).
    `events`: the full multi-scope stream (taxable + sheltered +
    affiliated — an affiliated or registered-account acquisition is still
    a replacement under both regimes).
    `canonical`: symbol canonicalizer (SplitTimeline.canonical) so a
    rename between the loss and the option acquisition still matches.
    Underlying matching is suffix-exact after canonicalization: an option
    suffixed .US never matches shares held under a .TO listing.
    `check_held_at_end`: CRA s.54 additionally requires the replacement
    still be owned at the end of the +window; when True each warning
    carries held_at_window_end for the OPTION's own position (across all
    scopes)."""
    canon = canonical or (lambda s: s)
    # A class-share root (SAMPLD for SAMPLD.B.TO, SAMPLCB for SAMPLC.B.US) names the
    # class line it delivers (A2-0016/0207).
    _cls = class_root_aliases(
        [ev.symbol for ev in events]
        + [str(l.get('symbol') or '') for l in loss_entries])
    acqs: List[List[Any]] = []     # [canonical underlying, right, buy row, opening contracts]
    for ev, opening in _opening_option_buys(events, date_of):
        und = parse_option_underlying(ev.symbol)
        # A futures option is never sized as shares: its own rule flags
        # it for a manual check (futures_option_vs_loss, US-WASH-15 /
        # CA-SL-15 — A2-0014/0056).
        if not und or _FUTURES_PREFIX_RE.match(und):
            continue
        acqs.append([canon(_cls.get(und.upper(), und)),
                     parse_option_right(ev.symbol), ev, opening])
    if not acqs:
        return []

    def _d(s: str) -> datetime:
        return datetime.strptime(s, '%Y-%m-%d')

    # Each contract is a right to its contract size in shares (100, or
    # the declared size of a mini / adjusted contract — A2-0957) and
    # backs one denial: it is used up across the losses in date order
    # (audit S069-17 / S070-00 / S070-01 — one contract used to be cited
    # as denying every loss in its window in full).
    left = {id(a[2]): a[3] * option_contract_size(a[2]) for a in acqs}
    out = []
    order = sorted(range(len(loss_entries)),
                   key=lambda n: str(loss_entries[n].get('date') or ''))
    for n in order:
        entry = loss_entries[n]
        symbol = entry['symbol']
        if is_option_symbol(symbol):
            continue
        if entry.get('direction') == 'SHORT':
            continue            # a put is not a right to acquire
        want = 'C'
        try:
            loss_dt = _d(entry['date'])
        except (KeyError, TypeError, ValueError):
            continue
        loss_c = canon(symbol)
        window_end = loss_dt + timedelta(days=window_days)
        loss_qty = abs(float(entry.get('qty') or 0.0))
        need = loss_qty if loss_qty > 1e-9 else float('inf')
        if loss_qty > 1e-9 and entry.get('open_qty') is not None:
            # A share count at the engine's precision: the open part is
            # a money ratio's (backlog TJ-0634).
            need = min(loss_qty, max(0.0, share_qty(entry['open_qty'])))
            if need <= 1e-9:
                continue
        denied_qty = (loss_qty - need) if loss_qty > 1e-9 else 0.0
        by_contract: Dict[str, Dict[str, Any]] = {}
        for und_c, right, ev, opening in acqs:
            if right != want or und_c != loss_c or need <= 1e-9:
                continue
            try:
                ev_dt = _d(date_of(ev))
            except (TypeError, ValueError):
                continue
            if abs((ev_dt - loss_dt).days) > window_days:
                continue
            take = min(need, left[id(ev)])
            if take <= 1e-9:
                continue
            left[id(ev)] -= take
            need -= take
            rec = by_contract.setdefault(
                ev.symbol, {'shares': 0.0, 'first': None, 'contracts': 0.0,
                            'size': option_contract_size(ev)})
            rec['shares'] += take
            # The contracts the buy opened, whole: a call that covers
            # only part of its shares against this loss is still one
            # contract bought (backlog TJ-0619: "0.1 contract(s)").
            rec['contracts'] += opening
            if rec['first'] is None or date_of(ev) < rec['first']:
                rec['first'] = date_of(ev)
        for occ, rec in sorted(by_contract.items()):
            held = None
            if check_held_at_end:
                hb = 0.0
                for ev in events:
                    if ev.symbol != occ or \
                            ev.action not in ('BUYSELL', 'ASSIGN'):
                        continue
                    try:
                        if _d(date_of(ev)) <= window_end:
                            hb += ev.quantity
                    except (TypeError, ValueError):
                        continue
                held = hb > 1e-6
            frac = (min(1.0, rec['shares'] / loss_qty)
                    if loss_qty > 1e-9 else 1.0)
            out.append({
                'rule': 'call_vs_share_loss',
                'loss_symbol': symbol,
                'loss_date': entry['date'],
                'loss_amount': M.present(M.of(entry['amount']), 2),
                'loss_id': entry.get('id', ''),
                'loss_qty': loss_qty or None,
                'option_symbol': occ,
                'option_acquired': rec['first'],
                # The contracts bought; covered_shares, how many of
                # their shares count against this loss (TJ-0619).
                'option_qty': rec['contracts'],
                'contract_size': rec['size'],
                # Share counts at the engine's precision (TJ-0634).
                'covered_shares': share_qty(rec['shares']),
                'at_risk_amount': M.present(M.of(entry['amount']) * M.of(frac), 2),
                # The loss shares share purchases already replaced.
                'replaced_qty': (share_qty(denied_qty)
                                 if denied_qty > 1e-9 else None),
                'held_at_window_end': held,
                'statute': statute_label,
            })
    return out
