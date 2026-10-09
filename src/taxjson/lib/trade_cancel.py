"""Broker trade cancellations (IB code `Ca`) netted against their original.

IB lists a cancelled trade as the ORIGINAL row plus a reversing row coded
`Ca` (same symbol, date/time and price; opposite quantity), and books the
corrected trade as a new row. Read as ordinary trades, the pair is a
phantom round trip: a cancelled loss sale followed by its rebooking turns
into two denied superficial losses, a gain sale overstates the gain, and
the remaining shares' ACB is wrong (audit R1-51).

The IB parser marks each `Ca` trade row with `type = TRADE_CANCEL_TYPE`
and drops it together with its original when both are in the same
statement. The original may sit in an EARLIER statement (a statement
for one year can cancel a fill booked in the previous year's statement),
so taxjson-merge2 runs the same pairing over every input of the account.
A cancellation whose original is in none of the inputs stays booked as a
reversing trade, with a warning.

Rows may be dicts (parser output) or TaxTransaction objects (merge2).
"""

from typing import Any, List, Optional, Tuple

TRADE_CANCEL_TYPE = 'trade_cancel'


def _g(t: Any, key: str, default: Any = None) -> Any:
    if isinstance(t, dict):
        return t.get(key, default)
    return getattr(t, key, default)


def is_trade_cancel(t: Any) -> bool:
    return _g(t, 'type') == TRADE_CANCEL_TYPE


def cancels(orig: Any, ca: Any) -> bool:
    """True when `ca` (a cancellation row) reverses `orig`: same account,
    symbol, currency and trade date, the negated quantity and the same
    price."""
    if is_trade_cancel(orig) or _g(orig, 'action') not in ('BUYSELL',
                                                            'ASSIGN'):
        return False
    if (_g(orig, 'symbol') != _g(ca, 'symbol')
            or _g(orig, 'currency') != _g(ca, 'currency')
            or _g(orig, 'date') != _g(ca, 'date')
            or (_g(orig, 'account') or '') != (_g(ca, 'account') or '')):
        return False
    # `account` is the taxjson label for every statement; the broker
    # account (hashed, stamped by taxjson-brokerage) tells two IB
    # accounts' identical fills apart (audit A2-1095).
    sa_o = _g(orig, 'source_account') or ''
    sa_c = _g(ca, 'source_account') or ''
    if sa_o and sa_c and sa_o != sa_c:
        return False
    q_o = float(_g(orig, 'quantity') or 0.0)
    q_c = float(_g(ca, 'quantity') or 0.0)
    if abs(q_c) < 1e-12 or abs(q_o + q_c) > 1e-9 * max(1.0, abs(q_c)):
        return False
    p_o = float(_g(orig, 'price') or 0.0)
    p_c = float(_g(ca, 'price') or 0.0)
    return abs(p_o - p_c) <= 1e-6 * max(1.0, abs(p_c))


def trade_cancel_what(q_orig: float, q_ca: float) -> str:
    """'the' for a cancellation of a whole trade, 'the rest (N) of the'
    for one that cancels what earlier cancellations left of it (the
    pair's original keeps its booked size; issue #12)."""
    q_o = float(q_orig or 0.0)
    q_c = float(q_ca or 0.0)
    if abs(q_o + q_c) <= 1e-9 * max(1.0, abs(q_c)):
        return 'the'
    return f'the rest ({-q_c:g}) of the'


def _probe(orig: Any, qty: float) -> dict:
    """`orig` as a dict with its quantity replaced (for `cancels`)."""
    d = dict(orig) if isinstance(orig, dict) else orig.to_dict()
    d['quantity'] = qty
    return d


def _partly_cancels(orig: Any, ca: Any) -> bool:
    """True when `ca` reverses PART of `orig`: IB lists an order filled
    in several executions as one Order row, and a Ca naming one
    execution (-40 of a 440-share order) never matched the aggregate
    exactly (audit A2-0298). Every other field must match as for a
    full cancellation; the original is larger, in the same direction."""
    q_o = float(_g(orig, 'quantity') or 0.0)
    q_c = float(_g(ca, 'quantity') or 0.0)
    if abs(q_c) < 1e-12 or q_o * q_c >= 0 or abs(q_o) <= abs(q_c) + 1e-9:
        return False
    return cancels(_probe(orig, -q_c), ca)


def _reduced(orig: Any, ca: Any) -> Any:
    """`orig` less the cancelled execution: quantity and every money
    field scaled by the share that stays."""
    q_o = float(_g(orig, 'quantity') or 0.0)
    q_c = float(_g(ca, 'quantity') or 0.0)
    keep = (q_o + q_c) / q_o
    d = dict(orig) if isinstance(orig, dict) else orig.to_dict()
    d['quantity'] = q_o + q_c
    for f in ('net_amount', 'gross_amount', 'proceeds', 'commission',
              'fee'):
        if d.get(f) not in (None, ''):
            d[f] = round(float(d[f]) * keep, 10)
    if isinstance(orig, dict):
        if 'id' in d:
            d.pop('id')
        return d
    d['id'] = None
    return type(orig)(**d)


def pair_cancellations(txs: List[Any], partials: Optional[list] = None
                       ) -> Tuple[List[Any], List[Tuple[Any, Any]],
                                  List[Any]]:
    """Drop every cancellation row together with the original it reverses.

    Returns (kept, pairs, unmatched): `kept` in the input order, `pairs`
    as (original, cancellation), `unmatched` the cancellation rows whose
    original is not in `txs` (they stay in `kept`). Candidates are
    judged on their current quantity: after a partial cancellation
    (below) the reduced order is what a later cancellation must match,
    and one cancelling exactly its rest pairs with the original row
    (the order is gone in full; the cancelled quantity is the
    cancellation's). Among several
    candidate originals the one with the same time wins, else the latest
    one before the cancellation in the list, else the first after it.

    A cancellation of one execution of a larger order (no exact
    original, ONE larger same-direction original otherwise matching —
    the same time preferred) reduces that original pro rata instead:
    the reduced row takes its place in `kept`, the cancellation is
    dropped, and (original, cancellation, reduced) is appended to
    `partials` when a list is given."""
    used = set()
    pairs = []
    unmatched = []
    replaced = {}
    for ci, ca in enumerate(txs):
        if not is_trade_cancel(ca):
            continue
        # Both searches read each original as it stands NOW: an order
        # an earlier cancellation already reduced is matched by its
        # residual (cancelling exactly the rest removes it), never by
        # its original size (issue #12).
        cands = [i for i, t in enumerate(txs)
                 if i not in used and i != ci
                 and cancels(replaced.get(i, t), ca)]
        if not cands:
            part = [i for i, t in enumerate(txs)
                    if i not in used and i != ci and not is_trade_cancel(t)
                    and _partly_cancels(replaced.get(i, t), ca)]
            same = [i for i in part
                    if (_g(txs[i], 'time') or '') == (_g(ca, 'time') or '')]
            part = same or part
            if len(part) == 1:
                oi = part[0]
                before = replaced.get(oi, txs[oi])
                replaced[oi] = _reduced(before, ca)
                used.add(ci)
                if partials is not None:
                    partials.append((before, ca, replaced[oi]))
                continue
            unmatched.append(ca)
            continue
        same_time = [i for i in cands
                     if (_g(txs[i], 'time') or '') == (_g(ca, 'time') or '')]
        pool = same_time or cands
        before = [i for i in pool if i < ci]
        oi = before[-1] if before else pool[0]
        used.update((oi, ci))
        pairs.append((txs[oi], ca))
    kept = [replaced.get(i, t) for i, t in enumerate(txs) if i not in used]
    return kept, pairs, unmatched
