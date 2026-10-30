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

Rewritten rows carry type = FUTURES_SETTLEMENT. Both engines book a
settlement row's money signed: a sell's proceeds = net_amount, a buy's
cost = -net_amount, so both a long and a short close realize exactly the
P/L. `price`, `fee` and `gross_amount` (the fill's notional) are kept
for display only.

The settlement basis is a market fact (nothing is paid to open a
future), so BOTH countries use it; the rewrite is chosen by the
project's COUNTRY, never by the base currency (partition ENGINE-02: it
used to run only for a CAD target, so a US book kept FX on notional and
a US book on a CAD base fed settlement rows to a US engine that booked
them with the sign inverted). What differs by country is which open
units a partial close consumes — the country's own lot rule:
``method_for("canada")`` = "average" (s.47 average cost; tax-logic
CA-FX-04), ``method_for("usa")`` = "fifo" (FIFO, US-FUT-01).

Futures OPTIONS are ordinary options (their premium is real cash) and
are never touched. A plain future is an `F:`, `/` or `\\` symbol that is
not an OCC option symbol.
"""
from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from taxjson.lib.core import TaxTransaction, is_option_symbol

FUTURES_SETTLEMENT = "futures_settlement"
_FUTURES_PREFIXES = ("F:", "/", "\\")
_EPS = Decimal("1e-9")

# Which open units a partial close consumes, by country (module doc).
METHODS = ("average", "fifo")
_METHOD_BY_COUNTRY = {"canada": "average", "usa": "fifo"}


def method_for(country) -> str:
    """"average" (Canada, s.47) | "fifo" (US) for a country spelling
    lib/country accepts; anything else raises CountryError."""
    from taxjson.lib.country import canonical_country
    return _METHOD_BY_COUNTRY[canonical_country(country)]


def has_plain_futures(transactions) -> bool:
    for tx in transactions:
        sym = tx.get("symbol") if isinstance(tx, dict) else \
            getattr(tx, "symbol", "")
        if is_plain_future(sym or ""):
            return True
    return False


def is_plain_future(symbol: str) -> bool:
    """A futures contract (not an option on one)."""
    s = str(symbol or "")
    return s.startswith(_FUTURES_PREFIXES) and not is_option_symbol(s)


# Cash-settled options on a broad-based index listed on a US exchange:
# "nonequity options", §1256 contracts (IRC §1256(b)(1)(D), (g)(3)) —
# marked to market at year end and split 60/40, reported on Form 6781,
# never on Form 8949. Matched on the option ROOT. The roots are market
# data, not code: taxjson/data/markets.toml (lib/markets.
# index_option_roots), extended or overridden by ticker.map INDEXOPT
# lines. An index option missing from it is filed as an ordinary option,
# so the US Form 8949 notes still tell the user to check (tax-logic
# US-OPT-04).


_FUTURE_ROOT_RE = re.compile(
    r'^(?:F:|[\\/])([A-Z0-9]+?)(?:[FGHJKMNQUVXZ]\d{1,2})?(?:\..*)?$')


def futures_root(symbol: str) -> str:
    """A plain future's contract family: 'ZZQ' for 'F:ZZQZ6.US' or
    '/ZZQZ26' (the book prefix, the month code and year, and a listing
    suffix dropped); '' for anything else."""
    s = str(symbol or "").strip().upper()
    if not is_plain_future(s):
        return ""
    m = _FUTURE_ROOT_RE.match(s)
    return m.group(1) if m else ""


def is_security_future(symbol: str) -> bool:
    """A plain future the project's ticker.map declares a security
    future (`SECFUT ROOT`, lib/markets): a single-stock or narrow-based
    index future (backlog TJ-0730)."""
    root = futures_root(symbol)
    if not root:
        return False
    from taxjson.lib.markets import is_security_future_root
    return is_security_future_root(root)


def section_1256_kind(symbol: str) -> str:
    """Which US §1256 contract `symbol` is, '' for none: "future" (a
    plain futures contract, unless the ticker.map declares its root a
    security future — IRC §1256(b)(2)(A) excludes one held by anyone
    but a dealer, backlog TJ-0730), "futures option" (an option on one — the
    `F:`, `/` or `\\` prefix) or "index option" (a broad-based index
    option, lib/markets.is_index_option_root). tax-logic US-FUT-02 /
    US-OPT-04:
    none of them is modelled, so the US filing outputs keep them off
    Form 8949 and list them for Form 6781 by hand. US-only: Canada
    has no §1256 (a future is an ordinary property on Schedule 3)."""
    from taxjson.lib.core import parse_option_underlying
    s = str(symbol or "")
    if is_plain_future(s):
        return "" if is_security_future(s) else "future"
    if not is_option_symbol(s):
        return ""
    if s.startswith(_FUTURES_PREFIXES):
        return "futures option"
    root = (parse_option_underlying(s) or "").split(".", 1)[0]
    from taxjson.lib.markets import is_index_option_root
    return "index option" if is_index_option_root(root) else ""


def is_settlement_row(tx) -> bool:
    t = tx.get("type") if isinstance(tx, dict) else getattr(tx, "type", "")
    return (t or "") == FUTURES_SETTLEMENT


def _D(x) -> Decimal:
    return Decimal(repr(float(x or 0.0)))


def _money(tx: TaxTransaction) -> Decimal:
    """The fill's native money in pool terms (engine convention): a
    buy's cost, a sell's proceeds, both SIGNED — a buy at a negative
    price (WTI, April 2020) receives money, a negative cost; the
    magnitude booked a loss as a gain (audit A2-0092). Every parser
    gives a positive-price buy a positive cost."""
    return _D(tx.net_amount)


def _part(tx: TaxTransaction, qty: Decimal, frac: Decimal, net: Decimal,
          keep_id: bool, leg: str = "", money: Decimal = Decimal(0)
          ) -> TaxTransaction:
    d = tx.to_dict()
    d["quantity"] = float(qty)
    d["net_amount"] = float(net)
    d["type"] = FUTURES_SETTLEMENT
    d["settle_native"] = (
        f"{leg} {money} {net} {(tx.currency or '').upper() or '-'}"
        if leg else "")
    for f in ("fee", "commission", "gross_amount", "proceeds"):
        d[f] = float(_D(d.get(f)) * frac)
    if not keep_id:
        d["id"] = None
    return TaxTransaction(**d)


def _own_fee(tx: TaxTransaction) -> Decimal:
    """A fill's commissions (commission + fee, + charged), inside its
    net_amount."""
    return _D(tx.commission) + _D(tx.fee)


def _broker_books(transactions, idx) -> Dict[int, str]:
    """{index: broker book} for the plain-futures rows `idx`: the row's
    broker account (its hashed `source_account`) when the contract's
    rows come from two or more broker accounts and every one of them
    names its own; '' (one book) otherwise. Two broker accounts' exports
    in one project folder are two positions: a short opened in one
    never closes a long held in the other (TJ-0949). A row naming no
    broker account (a .tt line) keeps the contract one book, as
    before."""
    srcs: Dict[Tuple[str, str], set] = {}
    for i in idx:
        tx = transactions[i]
        srcs.setdefault((tx.symbol, (tx.currency or "").upper()),
                        set()).add(str(getattr(tx, "source_account", "")
                                       or ""))
    out: Dict[int, str] = {}
    for i in idx:
        tx = transactions[i]
        s = srcs[(tx.symbol, (tx.currency or "").upper())]
        out[i] = (str(tx.source_account) if len(s) > 1 and "" not in s
                  else "")
    return out


def _take_moved_position(pos: Dict[Tuple[str, str, str], list],
                         key: Tuple[str, str, str], tx: TaxTransaction,
                         q: Decimal) -> None:
    """A fill the broker codes CLOSING alone (IB code C) for more than
    its own broker account holds: the rest was moved to it from another
    broker account of the folder (TJ-1162). A futures row cannot carry a
    transfer, so the fill itself is the only sign: the units it lacks
    come from the other books holding that side, their money with them.
    All of them are closed by this fill, so their place in the lots
    does not matter."""
    from taxjson.lib.missing_history import open_close_codes
    if open_close_codes(tx) != ("C",):
        return
    p = pos[key]
    if abs(p[0]) > _EPS and (p[0] > 0) == (q > 0):
        return                  # adds to its own position: no close
    need = abs(q) - abs(p[0])
    for k, other in sorted(pos.items()):
        if need <= _EPS:
            break
        if (k == key or k[:2] != key[:2] or abs(other[0]) <= _EPS
                or (other[0] > 0) == (q > 0)):
            continue
        sign = Decimal(1) if other[0] > 0 else Decimal(-1)
        mv = min(need, abs(other[0]))
        left = mv
        while left > _EPS and other[1]:
            lot = other[1][0]
            take = min(left, lot[0])
            part = [take, lot[1] * take / lot[0], lot[2] * take / lot[0]]
            lot[0] -= take
            lot[1] -= part[1]
            lot[2] -= part[2]
            left -= take
            if lot[0] <= _EPS:
                other[1].pop(0)
            p[1].append(part)
        other[0] -= sign * mv
        if abs(other[0]) <= _EPS:
            other[0], other[1] = Decimal(0), []
        p[0] += sign * mv
        need -= mv


def _settle(transactions: List[TaxTransaction], method: str,
            open_fees: Optional[Dict[int, List[Optional[Decimal]]]] = None
            ) -> Tuple[Dict[int, List[TaxTransaction]], Dict[str, int]]:
    """{input index: replacement rows} for the plain-futures fills of
    one book, plus stats. See settle_futures. `method`: "average" keeps
    one pooled lot per contract; "fifo" keeps each opening as its own
    lot and a close consumes the oldest first. `open_fees`, when given,
    receives {input index: [per replacement row, the opening fills'
    commissions inside a close's basis; None for an opening part]} —
    what the FX cash ledger needs to date each commission on its own
    fill."""
    if method not in METHODS:
        raise ValueError(f"futures settlement method must be one of "
                         f"{METHODS}, got {method!r}")
    stats = {"rows": 0, "closes": 0, "split": 0}
    # A book is settled once, as a whole. One already rewritten comes
    # back as is (idempotent); one that MIXES settled rows with raw
    # fills is refused — the shortcut used to return the whole input on
    # the first settled row it met, so a raw fill beside it kept its
    # notional as its money (audit 2026-10-24 B09).
    settled = [i for i, tx in enumerate(transactions)
               if is_plain_future(tx.symbol)
               and (tx.type or "") == FUTURES_SETTLEMENT]
    if settled:
        raw = [i for i, tx in enumerate(transactions)
               if is_plain_future(tx.symbol)
               and (tx.type or "") != FUTURES_SETTLEMENT
               and tx.action == "BUYSELL"
               and abs(float(tx.quantity or 0.0)) > 0]
        if raw:
            def _name(i):
                tx = transactions[i]
                return f"{tx.symbol} {tx.date} (id={tx.id})"
            raise ValueError(
                f"futures book mixes {len(settled)} row(s) already on the "
                f"settlement basis (e.g. {_name(settled[0])}) with "
                f"{len(raw)} unsettled fill(s) (e.g. {_name(raw[0])}): a "
                f"book is settled once, as a whole, so the unsettled "
                f"fill's notional would stay its money. Keep the raw "
                f"fills in a book of their own, or rebuild the book from "
                f"the exports (`taxjson run`).")
        return {}, stats                           # already settled
    fut_idx = []
    for i, tx in enumerate(transactions):
        if not is_plain_future(tx.symbol):
            continue
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

    if method == "fifo":
        # The US engine's order (corporate_timeline.event_sort_key,
        # profile us_main): trade date, then time. Settle-first let a
        # later trade with an earlier settle date be the "first in"
        # (2026-10 audit #102). Each row keeps its own date_settle.
        order = sorted(fut_idx, key=lambda i: (
            transactions[i].date, transactions[i].time or "", i))
    else:
        order = sorted(fut_idx, key=lambda i: (
            transactions[i].date_settle or transactions[i].date,
            transactions[i].date, transactions[i].time or "", i))
    # (sym, cur, broker book) -> [signed qty, [[open units, their native
    # money, their commissions], ...]]: "average" keeps a single pooled
    # lot; "fifo" one lot per opening.
    books = _broker_books(transactions, fut_idx)
    pos: Dict[Tuple[str, str, str], list] = {}
    out_parts: Dict[int, List[TaxTransaction]] = {}
    for i in order:
        tx = transactions[i]
        q = _D(tx.quantity)
        if abs(q) <= _EPS:
            out_parts[i] = [_part(tx, q, Decimal(1), Decimal(0), True)]
            continue
        fee = _own_fee(tx)
        key = (tx.symbol, (tx.currency or "").upper(), books[i])
        p = pos.setdefault(key, [Decimal(0), []])
        if books[i]:
            _take_moved_position(pos, key, tx, q)
        money = _money(tx)
        stats["rows"] += 1
        if abs(p[0]) <= _EPS or (p[0] > 0) == (q > 0):
            # Opening / adding: nothing is paid; the fill's money joins
            # the position's native cost (a long's cost, a short's
            # proceeds).
            p[0] += q
            if method == "average" and p[1]:
                p[1][0][0] += abs(q)
                p[1][0][1] += money
                p[1][0][2] += fee
            else:
                p[1].append([abs(q), money, fee])
            out_parts[i] = [_part(tx, q, Decimal(1), Decimal(0), True,
                                  "LO" if q > 0 else "SO", money)]
            if open_fees is not None:
                open_fees[i] = [None]
            continue
        closing = min(abs(q), abs(p[0]))
        frac = closing / abs(q)
        # The open money the closed units carry: the pooled average, or
        # the oldest lots first (FIFO).
        need, basis, basis_fee = closing, Decimal(0), Decimal(0)
        while need > _EPS and p[1]:
            lot = p[1][0]
            take = min(need, lot[0])
            part_money = lot[1] * take / lot[0]
            part_fee = lot[2] * take / lot[0]
            basis += part_money
            basis_fee += part_fee
            lot[0] -= take
            lot[1] -= part_money
            lot[2] -= part_fee
            need -= take
            if lot[0] <= _EPS:
                p[1].pop(0)
        close_money = money * frac
        if q < 0:            # sell closes a long
            pl = close_money - basis
        else:                # buy covers a short
            pl = basis - close_money
        stats["closes"] += 1
        sign = Decimal(1) if q > 0 else Decimal(-1)
        p[0] += sign * closing
        parts = [_part(tx, sign * closing, frac, pl, True,
                       "SC" if q > 0 else "LC", close_money)]
        fees: List[Optional[Decimal]] = [basis_fee]
        if abs(p[0]) <= _EPS:
            p[0], p[1] = Decimal(0), []
        left = abs(q) - closing
        if left > _EPS:
            # Crossed zero: the rest opens the opposite position.
            stats["split"] += 1
            lfrac = left / abs(q)
            p[0] = sign * left
            p[1] = [[left, money * lfrac, fee * lfrac]]
            parts.append(_part(tx, sign * left, lfrac, Decimal(0), False,
                               "LO" if q > 0 else "SO", money * lfrac))
            fees.append(None)
        out_parts[i] = parts
        if open_fees is not None:
            open_fees[i] = fees
    return out_parts, stats


def settle_futures(transactions: List[TaxTransaction], method: str
                   ) -> Tuple[List[TaxTransaction], Dict[str, int]]:
    """Rewrite every plain-futures BUYSELL row of one book on the
    settlement basis (module docstring). Every other row passes through
    unchanged and in place; a book already rewritten is returned as is.
    Returns (rows, stats) with stats = {'rows', 'closes', 'split'}.
    `method`: the country's lot rule, ``method_for(country)``.

    A plain-futures row that moves a position through any action other
    than BUYSELL (an OPENING_BALANCE, an ASSIGN, a SPLIT) is refused
    with ValueError: its money cannot be put on the settlement basis,
    and booking its notional would bring back the FX-on-notional error
    silently."""
    parts, stats = _settle(transactions, method)
    out: List[TaxTransaction] = []
    for i, tx in enumerate(transactions):
        out.extend(parts.get(i, [tx]))
    return out, stats


def settle_futures_dicts(rows: List[dict], method: str, *,
                         fee_basis: bool = False) -> List[dict]:
    """settle_futures over row DICTS (report tools reading native books),
    one book per `account` value. Non-futures rows pass through as the
    same dict objects; futures rows come back as settlement dicts that
    keep any extra keys of their source row. `fee_basis`: each
    settlement dict also carries `_open_fee`, the opening fills'
    commissions inside a close's basis (None on an opening part), so
    the FX cash ledger dates every commission on its own fill
    (taxjson_fx_cash._cash_legs)."""
    from taxjson.lib.core import coerce_transaction_row
    by_acct: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        if isinstance(r, dict) and is_plain_future(r.get("symbol") or ""):
            by_acct.setdefault(str(r.get("account") or ""), []).append(i)
    replaced: Dict[int, List[dict]] = {}
    for acct, idx in by_acct.items():
        objs = [coerce_transaction_row(rows[i], i, f"account {acct}")
                for i in idx]
        fees: Dict[int, List[Optional[Decimal]]] = {}
        parts, _ = _settle(objs, method, fees if fee_basis else None)
        for k, i in enumerate(idx):
            if k in parts:
                replaced[i] = [{**rows[i], **p.to_dict()} for p in parts[k]]
                for d, f in zip(replaced[i], fees.get(k, ())):
                    d["_open_fee"] = None if f is None else float(f)
    out: List[dict] = []
    for i, r in enumerate(rows):
        out.extend(replaced.get(i, [r]))
    return out


# ----------------------------------------------------------------------
# Canada: one average cost across your taxable accounts (issue #76)
#
# Each account's books are settled on their own (settle_futures runs in
# the account's conversion, before any other account is read), so a
# close carries that ACCOUNT's average cost. Canada pools identical
# property across all of a taxpayer's taxable accounts (s.47, tax-logic
# CA-FX-04): the Canadian engine re-averages the closes over every
# taxable account it is given, from the native facts each settlement
# part records (TaxTransaction.settle_native), before it books them.
#
# A long and a short of the same contract are two positions, never one
# pool: each account's fill closes only that account's own position
# (the broker realizes it there, in that year), so the legs keep the
# account's direction (LO/LC/SO/SC) and only the AVERAGE behind a close
# is taken across accounts — longs with longs, shorts with shorts.

_LEGS = ("LO", "LC", "SO", "SC")
# The Canadian engine's pool key for a short futures position: its own
# pool beside the long one, so a short in one account never closes a
# long in another (lib/core CanadaTaxRules.compute_gains).
SHORT_POOL_SUFFIX = "\x00short"


def parse_settle_native(value) -> "Tuple[str, Decimal, Decimal, str, Decimal] | None":
    """(leg, money, pl, currency, rate) from a settlement part's
    `settle_native`, rate None before conversion; None when absent or
    unreadable (a book settled by an older taxjson)."""
    parts = str(value or "").split()
    if len(parts) not in (4, 5) or parts[0] not in _LEGS:
        return None
    try:
        money, pl = Decimal(parts[1]), Decimal(parts[2])
        rate = Decimal(parts[4]) if len(parts) == 5 else None
    except Exception:
        return None
    return parts[0], money, pl, parts[3], rate


def stamp_rate(tx: TaxTransaction, rate) -> None:
    """Record on a converted settlement part the rate its conversion
    applied (1 when the contract is in the target currency). A part
    converted again (USD to EUR, then EUR to CAD) composes the rates,
    so the recorded rate always takes its native P/L to the currency
    it is in now; the first rate alone priced the Canadian re-averaged
    close in the intermediate currency (backlog TJ-0745)."""
    v = str(getattr(tx, "settle_native", "") or "")
    parts = v.split()
    if len(parts) == 4:
        tx.settle_native = f"{v} {rate}"
    elif len(parts) == 5 and parse_settle_native(v) is not None:
        prior = Decimal(parts[4])
        composed = prior * Decimal(str(rate))
        tx.settle_native = " ".join(parts[:4] + [str(composed)])


def engine_pool_key(tx) -> str:
    """The Canadian engine's pool key for a row: its symbol, or the
    symbol's short-futures pool for a settlement part of a SHORT
    position (leg SO/SC)."""
    sym = tx.symbol
    if (tx.type or "") == FUTURES_SETTLEMENT and \
            str(getattr(tx, "settle_native", "") or "")[:1] == "S":
        return sym + SHORT_POOL_SUFFIX
    return sym


def pool_across_accounts(transactions: List[TaxTransaction]
                         ) -> Tuple[List[TaxTransaction], List[str]]:
    """Canada (s.47, tax-logic CA-FX-04): re-average every futures close
    over the open contracts of ALL the taxable accounts in
    `transactions` (one taxpayer's taxable books, already settled and
    converted per account). Returns (rows, notes).

    A contract held in one account only is left exactly as its own
    settlement booked it. For a contract in two or more accounts, the
    open units of each direction are one pool [units, native money]
    across the accounts; a close takes the pool's average, its native
    P/L is recomputed, and converted at the rate that close's
    conversion used. Only the money of a close changes: every leg stays
    in its account, its date and its direction, so no realized P/L
    moves to another year. A contract whose parts lack the recorded
    facts (books settled by an older taxjson) is left as booked and
    named in `notes`."""
    by_sym: Dict[str, List[int]] = {}
    for i, tx in enumerate(transactions):
        if (tx.type or "") == FUTURES_SETTLEMENT and is_plain_future(
                tx.symbol or ""):
            by_sym.setdefault(tx.symbol, []).append(i)
    notes: List[str] = []
    out = list(transactions)
    for sym, idx in sorted(by_sym.items()):
        # Books settled apart: the project's accounts, and the broker
        # accounts of one folder (_broker_books, TJ-0949).
        _bk = _broker_books(transactions, idx)
        if len({(transactions[i].account, _bk[i]) for i in idx}) < 2:
            continue
        legs = {}
        for i in idx:
            tx = transactions[i]
            if abs(float(tx.quantity or 0.0)) <= 1e-12:
                continue
            leg = parse_settle_native(getattr(tx, "settle_native", ""))
            if leg is None or (leg[4] is None and (
                    (tx.currency or "").upper() != leg[3])):
                legs = None
                break
            legs[i] = leg
        if legs is None:
            notes.append(
                f"note: {sym}: held in several taxable accounts, but its "
                f"settled rows do not record their native amounts — each "
                f"account's own average cost is used; re-run `taxjson "
                f"run` to rebuild the books (tax-logic CA-FX-04).")
            continue
        order = sorted(legs, key=lambda i: (
            transactions[i].date_settle or transactions[i].date,
            transactions[i].date, transactions[i].time or "", i))
        # (currency, "L"|"S") -> [open units, their native money]
        pool: Dict[Tuple[str, str], List[Decimal]] = {}
        for i in order:
            tx = transactions[i]
            leg, money, old_pl, cur, rate = legs[i]
            units = abs(_D(tx.quantity))
            p = pool.setdefault((cur, leg[0]), [Decimal(0), Decimal(0)])
            if leg[1] == "O":
                p[0] += units
                p[1] += money
                continue
            if p[0] <= _EPS:
                # Cannot happen on books settled per account (the
                # account's own openings are in the pool); kept as its
                # account booked it, and said.
                notes.append(
                    f"note: {sym}: the close on {tx.date} in account "
                    f"{tx.account} has no open "
                    f"{'long' if leg[0] == 'L' else 'short'} contracts "
                    f"behind it across the taxable accounts — kept at "
                    f"its account's own average cost (tax-logic "
                    f"CA-FX-04).")
                continue
            take = min(units, p[0])
            basis = p[1] * take / p[0]
            p[0] -= take
            p[1] -= basis
            if p[0] <= _EPS:
                p[0], p[1] = Decimal(0), Decimal(0)
            pl = money - basis if leg[0] == "L" else basis - money
            if abs(pl - old_pl) <= Decimal("1e-6"):
                continue
            native = float(pl)
            net = (native if rate is None
                   else float(Decimal(str(native)) * rate))
            new = copy.copy(tx)
            new.net_amount = net
            out[i] = new
    return out, notes
