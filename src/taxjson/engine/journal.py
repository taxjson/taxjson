"""Listing journals and opening rows: a journal's two legs and currency factor,
an opening's source.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from decimal import Decimal

from taxjson.lib.numeric import D
from taxjson.model import money as M
from taxjson.lib.row_types import LISTING_JOURNAL_TYPE
from taxjson.lib.unbacked_close import is_unbacked_opening

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction


# "own-account move #N: FROM -> TO (...)" — the legs' description.
_MOVE_DESC_RE = re.compile(r'own-account move #\d+: (\S+) -> (\S+)')


def _opening_source(tx: TaxTransaction, symbol: str) -> Dict[str, Any]:
    """An OPENING cost=unknown row's own fields — account, symbol, date
    and signed quantity — as the pool (`unknown_from`) and each
    disposition drawing on its units (the record's `unknown_cost_from`)
    carry them, so the run names the line without re-joining sale and
    line by symbol and account (lib/first_run.unknown_cost_sales;
    backlog TJ-3209). Units, never money. The opening of a marked CLOSE
    row's unbacked units (lib/unbacked_close OPENING_KIND; no line holds
    it) names that row's own date and units, and says so
    (`marked_close`: CA-OPT-12, backlog TJ-5349)."""
    src = {'account': str(tx.account or ''),
           'symbol': str(symbol or tx.symbol or ''),
           'date': str(tx.date or '')[:10],
           'quantity': float(tx.quantity or 0.0)}
    if is_unbacked_opening(tx):
        src['marked_close'] = True
    return src


def _is_listing_journal(t: TaxTransaction) -> bool:
    return t.action == 'TRANSFER' and t.type == LISTING_JOURNAL_TYPE


def _journal_legs(transactions: Iterable[TaxTransaction]
                  ) -> Dict[str, Tuple[TaxTransaction, TaxTransaction]]:
    """journal_pair -> (out-leg, in-leg) of each listing journal
    (LISTING_JOURNAL_TYPE) whose two legs are both in the book."""
    legs: Dict[str, List[TaxTransaction]] = {}
    for t in transactions:
        if _is_listing_journal(t) and t.journal_pair:
            legs.setdefault(t.journal_pair, []).append(t)
    out: Dict[str, Tuple[TaxTransaction, TaxTransaction]] = {}
    for k, ts in legs.items():
        neg = [t for t in ts if (t.quantity or 0.0) < 0]
        pos = [t for t in ts if (t.quantity or 0.0) > 0]
        if (len(neg) == 1 and len(pos) == 1
                and neg[0].symbol != pos[0].symbol):
            out[k] = (neg[0], pos[0])
    return out


def _journal_factor(src_currency: str, src_leg: TaxTransaction,
                    dst_leg: TaxTransaction) -> Optional[Decimal]:
    """The factor that restates a journaled cost in the receiving
    listing's currency: 1 when the source pool is already in it (every
    pool of a base-currency book); else the in-leg's `price` (the
    source's currency over the receiver's), inverted when a short moves
    the other way (from the in-leg's listing to the out-leg's). None:
    no rate on file — the cost is unknown in this book."""
    cur = dst_leg.currency
    if not src_currency or not cur or src_currency == cur:
        return Decimal(1)
    f = M.of((dst_leg if dst_leg.quantity > 0 else src_leg).price or 0.0)
    if f <= 0:
        return None
    return D(f) if dst_leg.quantity > 0 else Decimal(1) / D(f)
