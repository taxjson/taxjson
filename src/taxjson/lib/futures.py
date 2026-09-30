"""Plain futures contracts: identification and settlement-basis booking.

A futures contract costs nothing to open and nothing changes hands but
the daily variation margin; the contract's notional (quantity x price x
multiplier) is never paid. Brokers still report each fill with its
notional (IB's Trades "Notional Value"), so the parsed rows carry it as
`net_amount`. Booked that way, each leg's notional was converted to CAD
at its own date's rate and the CAD gain picked up notional x (close rate
- open rate): FX on money that never moved (R1-0 / R1-52 / R1-204). It
also put the notional on Schedule 3 line 6 and into the T1135 cost.

`settle_futures` rewrites a book's plain-futures trades on the
SETTLEMENT basis, in the native currency, before conversion:

  * the portion of a fill that OPENS or adds to a position carries
    net_amount 0 — nothing is paid, and its commission joins the
    position's native cost;
  * the portion that CLOSES carries the realized native P/L of the units
    it closes (average cost, commissions on both legs included — what
    the broker's Realized P/L and variation margin settle), SIGNED:
    positive = a gain received, negative = a loss paid. Conversion then
    applies the closing leg's own rate (its settlement date — the trade
    date by default for futures, [settings] futures_settle), so the CAD
    gain is the native P/L at the disposition-date rate;
  * a fill that crosses zero (sell 2 while long 1) is split into its
    closing and opening parts; the closing part keeps the row's id.

Rewritten rows carry type = FUTURES_SETTLEMENT. The Canada engine books
a settlement row's money signed: a sell's proceeds = net_amount, a buy's
cost = -net_amount, so both a long and a short close realize exactly the
P/L. `price`, `fee` and `gross_amount` (the fill's notional) are kept
for display only.

Futures OPTIONS are ordinary options (their premium is real cash) and
are never touched. A plain future is an `F:`, `/` or `\\` symbol that is
not an OCC option symbol.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Tuple

from taxjson.lib.core import TaxTransaction, is_option_symbol

FUTURES_SETTLEMENT = "futures_settlement"
_FUTURES_PREFIXES = ("F:", "/", "\\")
_EPS = Decimal("1e-9")


def is_plain_future(symbol: str) -> bool:
    """A futures contract (not an option on one)."""
    s = str(symbol or "")
    return s.startswith(_FUTURES_PREFIXES) and not is_option_symbol(s)


def is_settlement_row(tx) -> bool:
    t = tx.get("type") if isinstance(tx, dict) else getattr(tx, "type", "")
    return (t or "") == FUTURES_SETTLEMENT


def _D(x) -> Decimal:
    return Decimal(repr(float(x or 0.0)))


def _money(tx: TaxTransaction) -> Decimal:
    """The fill's native money in pool terms (engine convention): a
    buy's cost as a magnitude, a sell's proceeds signed."""
    n = _D(tx.net_amount)
    return n if float(tx.quantity or 0.0) < 0 else abs(n)


def _part(tx: TaxTransaction, qty: Decimal, frac: Decimal, net: Decimal,
          keep_id: bool) -> TaxTransaction:
    d = tx.to_dict()
    d["quantity"] = float(qty)
    d["net_amount"] = float(net)
    d["type"] = FUTURES_SETTLEMENT
    for f in ("fee", "commission", "gross_amount", "proceeds"):
        d[f] = float(_D(d.get(f)) * frac)
    if not keep_id:
        d["id"] = None
    return TaxTransaction(**d)


def _settle(transactions: List[TaxTransaction]
            ) -> Tuple[Dict[int, List[TaxTransaction]], Dict[str, int]]:
    """{input index: replacement rows} for the plain-futures fills of
    one book, plus stats. See settle_futures."""
    stats = {"rows": 0, "closes": 0, "split": 0}
    fut_idx = []
    for i, tx in enumerate(transactions):
        if not is_plain_future(tx.symbol):
            continue
        if (tx.type or "") == FUTURES_SETTLEMENT:
            return {}, stats                       # already settled
        if tx.action != "BUYSELL":
            if abs(float(tx.quantity or 0.0)) > 0 and tx.action not in (
                    "DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST",
                    "FEE", "DISALLOW", "ADJUST"):
                raise ValueError(
                    f"futures row {tx.action} {tx.symbol} {tx.date} "
                    f"(id={tx.id}): only BUYSELL fills of a futures "
                    f"contract can be booked (on their settled P/L). "
                    f"Replace it with the buy/sell fills, or remove it.")
            continue
        fut_idx.append(i)

    order = sorted(fut_idx, key=lambda i: (
        transactions[i].date_settle or transactions[i].date,
        transactions[i].date, transactions[i].time or "", i))
    pos: Dict[Tuple[str, str], List[Decimal]] = {}   # (sym, cur) -> [qty, cost]
    out_parts: Dict[int, List[TaxTransaction]] = {}
    for i in order:
        tx = transactions[i]
        q = _D(tx.quantity)
        if abs(q) <= _EPS:
            out_parts[i] = [_part(tx, q, Decimal(1), Decimal(0), True)]
            continue
        key = (tx.symbol, (tx.currency or "").upper())
        p = pos.setdefault(key, [Decimal(0), Decimal(0)])
        money = _money(tx)
        stats["rows"] += 1
        if abs(p[0]) <= _EPS or (p[0] > 0) == (q > 0):
            # Opening / adding: nothing is paid; the fill's money joins
            # the position's native cost (a long's cost, a short's
            # proceeds).
            p[0] += q
            p[1] += money
            out_parts[i] = [_part(tx, q, Decimal(1), Decimal(0), True)]
            continue
        closing = min(abs(q), abs(p[0]))
        frac = closing / abs(q)
        avg = p[1] / abs(p[0])
        close_money = money * frac
        if q < 0:            # sell closes a long
            pl = close_money - avg * closing
        else:                # buy covers a short
            pl = avg * closing - close_money
        stats["closes"] += 1
        p[1] -= avg * closing
        sign = Decimal(1) if q > 0 else Decimal(-1)
        p[0] += sign * closing
        parts = [_part(tx, sign * closing, frac, pl, True)]
        if abs(p[0]) <= _EPS:
            p[0], p[1] = Decimal(0), Decimal(0)
        left = abs(q) - closing
        if left > _EPS:
            # Crossed zero: the rest opens the opposite position.
            stats["split"] += 1
            lfrac = left / abs(q)
            p[0] = sign * left
            p[1] = money * lfrac
            parts.append(_part(tx, sign * left, lfrac, Decimal(0), False))
        out_parts[i] = parts
    return out_parts, stats


def settle_futures(transactions: List[TaxTransaction]
                   ) -> Tuple[List[TaxTransaction], Dict[str, int]]:
    """Rewrite every plain-futures BUYSELL row of one book on the
    settlement basis (module docstring). Every other row passes through
    unchanged and in place; a book already rewritten is returned as is.
    Returns (rows, stats) with stats = {'rows', 'closes', 'split'}.

    A plain-futures row that moves a position through any action other
    than BUYSELL (an OPENING_BALANCE, an ASSIGN, a SPLIT) is refused
    with ValueError: its money cannot be put on the settlement basis,
    and booking its notional would bring back the FX-on-notional error
    silently."""
    parts, stats = _settle(transactions)
    out: List[TaxTransaction] = []
    for i, tx in enumerate(transactions):
        out.extend(parts.get(i, [tx]))
    return out, stats


def settle_futures_dicts(rows: List[dict]) -> List[dict]:
    """settle_futures over row DICTS (report tools reading native books),
    one book per `account` value. Non-futures rows pass through as the
    same dict objects; futures rows come back as settlement dicts that
    keep any extra keys of their source row."""
    from taxjson.lib.core import coerce_transaction_row
    by_acct: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        if isinstance(r, dict) and is_plain_future(r.get("symbol") or ""):
            by_acct.setdefault(str(r.get("account") or ""), []).append(i)
    replaced: Dict[int, List[dict]] = {}
    for acct, idx in by_acct.items():
        objs = [coerce_transaction_row(rows[i], i, f"account {acct}")
                for i in idx]
        parts, _ = _settle(objs)
        for k, i in enumerate(idx):
            if k in parts:
                replaced[i] = [{**rows[i], **p.to_dict()} for p in parts[k]]
    out: List[dict] = []
    for i, r in enumerate(rows):
        out.extend(replaced.get(i, [r]))
    return out
