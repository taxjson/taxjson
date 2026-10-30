"""`taxjson option-yield`: how the option trades on an underlying did
over a window — premiums received and paid, the profit or loss on the
contracts closed in the window and how they closed (expired, bought
back, assigned, sold, exercised), what is still open, and the yield on
the strike notional at risk, time-weighted over the window and
annualized. Planning numbers only, never a filing figure (tax-logic
CA-PLAN-07 / US-PLAN-08): the tax treatment of a premium (ITA s.49
timing, §1234) is in the gains, not here.

Contracts are matched per account and option series, first in first
out, from the native books (what `taxjson events` shows): a sale with
no long position WRITES a contract, a purchase with no short position
BUYS one; the opposite trade closes the oldest open contract of that
series. A close at price 0 with no money is an expiry; an ASSIGN row
is an assignment (of a written contract) or an exercise (of a bought
one). A SPLIT on the series re-denominates and renames the open
contracts. A TRANSFER row moves contracts out of the account (the
oldest open contracts of the series leave, "transferred": no money,
no P/L here) or in (a contract whose premium is in the books it came
from: unknown here, said so); a zero-net cluster of one account's
TRANSFER rows (a custody move out and back, a cancel and rebook) is a
no-op, as the gains run reads it (2026-10-24 audit A06). Money keeps
the engine's convention — the quantity carries
the direction, a purchase's amount is a magnitude (a .tt book's negative
buy-back amount and a parser's positive one read the same), a sale's is
signed (a write whose commission exceeds the premium nets a loss).

The yield is the realized profit or loss of the window over the cost
of the underlying's shares held now in the accounts read (the books'
holdings: a covered-call writer's capital), annualized by 365 / the
window's days. With no shares held (naked or cash-secured contracts)
it is over the average strike notional open in the window instead
(strike x contract size x contracts, each contract weighted by its
days open inside the window), and the row says so. A contract opened
before the data (a missing-history opening) has no premium: its closes
are listed, its money is unknown, and the row is flagged. So is a
contract closed in another currency than it was opened in (never
converted here): its money is unknown, and the close's row is named.

This module is pure: the command (taxjson_run.cmd_option_yield) reads
the books and hands the rows in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from taxjson.lib.core import (is_option_symbol, parse_option_expiry,
                              parse_option_right, parse_option_strike,
                              parse_option_underlying)
from taxjson.lib.corporate_timeline import dedupe_split_events

SCHEMA_VERSION = 1
WRITTEN = "written"
BOUGHT = "bought"
# How a contract closed: the written side, then the bought side.
EXPIRED = "expired"
BOUGHT_BACK = "bought back"
ASSIGNED = "assigned"
SOLD = "sold"
EXERCISED = "exercised"
# Either side: the contracts left the account (no money, no P/L).
TRANSFERRED = "transferred"
# A yield basis: shares are held, their cost in another currency than
# the options' (never converted here): no yield (backlog TJ-0543).
OTHER_CURRENCY = "other_currency"
_EPS = 1e-9


@dataclass
class Close:
    date: str
    kind: str
    units: float
    cash: float          # signed: money received (+) or paid (-)
    pnl: float           # this close's share of the contract's premium + cash
    splits_seen: int = 0  # how many of the lot's splits came before it
    currency: str = ""   # the row's currency when not the lot's (money unknown)


@dataclass
class Lot:
    """One opened position in a series. `units`, `symbol`, `strike` and
    `expiry` are as OPENED; the splits and renames the series went
    through later are dated in `splits` (in replay order: each came
    after the open), and the `_on(end)` accessors give the lot as it
    stood at a window's end (a later split used to rewrite an earlier
    window's counts: 2026-10 audit R10). A close's `units` are the
    contracts of its own moment."""
    account: str
    symbol: str
    underlying: str
    right: str           # C | P
    strike: float
    expiry: str
    currency: str
    side: str            # WRITTEN | BOUGHT
    open_date: str
    units: float
    size: float          # units of the underlying per contract
    cash_open: float     # signed: a write's premium (+), a purchase's cost (-)
    unknown_cost: bool = False
    # Opened by a TRANSFER in: the premium is in the books the contract
    # came from (unknown here), not missing history.
    transfer_in: bool = False
    # The replay ordinal of the opening row: the FIFO order, finer than
    # `open_date` (two writes of one day renamed into one series:
    # 2026-10-24 audit A28).
    seq: int = 0
    open_units: float = 0.0          # contracts open now, in today's units
    closes: List[Close] = field(default_factory=list)
    notional: float = 0.0    # strike x size x units at the open (split-invariant)
    splits: List[Tuple[str, float, str]] = field(default_factory=list)
    # (date, contract size) from a split that moved the lot into a
    # series of another size (backlog TJ-1003), in replay order.
    resizes: List[Tuple[str, float]] = field(default_factory=list)

    def factor(self, through: str, first: int = 0) -> float:
        """The ratios of the lot's splits from the `first`-th on, dated
        up to and including `through`. The splits are the ones replayed
        after the open, so one stamped later on the open's own day counts
        and one stamped before it never reached the lot (a date
        comparison dropped the first: 2026-10 audit #80)."""
        f = 1.0
        for d, ratio, _new in self.splits[first:]:
            if d <= through:
                f *= ratio
        return f

    def units_on(self, through: str) -> float:
        return self.units * self.factor(through)

    def per_unit_on(self, through: str) -> float:
        u = self.units_on(through)
        return self.cash_open / u if u else 0.0

    def symbol_on(self, through: str) -> str:
        sym = self.symbol
        for d, _ratio, new in self.splits:
            if d <= through and new:
                sym = new
        return sym

    def underlying_on(self, through: str) -> str:
        sym = self.symbol_on(through)
        if sym == self.symbol:
            return self.underlying
        return parse_option_underlying(sym) or sym

    def strike_on(self, through: str) -> float:
        """The strike at `through`: the renamed series' own strike when
        a split moved the lot into another series (a strike adjustment
        booked as a ratio-1 SPLIT kept the old strike: 2026-10 audit
        #81), else the opened strike over the split ratios."""
        sym = self.symbol_on(through)
        if sym != self.symbol:
            s = parse_option_strike(sym)
            if s:
                return float(s)
        f = self.factor(through)
        return self.strike / f if f else self.strike

    def size_on(self, through: str) -> float:
        """The contract size in the series of `through`: the opened
        one, else the size of the series a split moved the lot into."""
        size = self.size
        for d, s in self.resizes:
            if d <= through:
                size = s
        return size

    def notional_on(self, through: str) -> float:
        """The lot's strike notional in the series of `through`: the
        opened notional, rescaled when the series' strike is not the
        opened one over the split ratios (a renamed strike), and when
        its contract size is not the opened one (a series renamed into
        one the ticker.map or its rows size otherwise: it kept the old
        size, backlog TJ-1003)."""
        f = self.factor(through)
        base = self.strike / f if f else self.strike
        now = self.strike_on(through)
        out = (self.notional * (now / base) if base and now
               else self.notional)
        size = self.size_on(through)
        return out * (size / self.size) if self.size and size else out

    def expiry_on(self, through: str) -> str:
        sym = self.symbol_on(through)
        return (self.expiry if sym == self.symbol
                else parse_option_expiry(sym) or self.expiry)

    def close_units_on(self, c: "Close", through: str) -> float:
        """A close's contracts in the units of `through`."""
        return c.units * self.factor(through, c.splits_seen)

    def units_open_on(self, end: str) -> float:
        """Contracts still open at the end of `end` (closes after it
        not yet taken), in the units of `end`."""
        return self.units_on(end) - sum(self.close_units_on(c, end)
                                        for c in self.closes
                                        if c.date <= end)


def _row(t: Dict[str, Any], key: str, default: Any = None) -> Any:
    v = t.get(key)
    return default if v is None else v


def _is_flat(t: Dict[str, Any]) -> bool:
    """A close at price 0 with no money but its fees: an expiry. A fee
    the broker charged on the expiry (IB's `C;Ep` with a commission)
    is no buy-back price: it made the expiry read as "bought back"
    (backlog TJ-0790). The fee still comes off the P/L (the row's net
    money is the close's cash)."""
    if abs(float(_row(t, "price", 0.0) or 0.0)) >= 1e-12:
        return False
    net = abs(float(_row(t, "net_amount", 0.0) or 0.0))
    if net < 1e-9:
        return True
    gross = (abs(float(_row(t, "gross_amount", 0.0) or 0.0))
             + abs(float(_row(t, "proceeds", 0.0) or 0.0)))
    fees = (abs(float(_row(t, "fee", 0.0) or 0.0))
            + abs(float(_row(t, "commission", 0.0) or 0.0)))
    return gross < 1e-9 and fees > 1e-9 and net <= fees + 1e-6


def default_size(t: Dict[str, Any]) -> float:
    """The row's contract size: its multiplier when it states one,
    else the standard 100 shares."""
    m = float(_row(t, "multiplier", 0.0) or 0.0)
    return m if m > 0 else 100.0


def _kept_transfer_ids(opts: List[Tuple[str, Dict[str, Any]]]) -> set:
    """ids of the TRANSFER rows of `opts` the gains run keeps: a
    zero-net cluster of one account's TRANSFER rows (a custody move out
    and back within days, a cancel and rebook) is a no-op there
    (pipeline._drop_self_cancelling_transfers), so it is one here."""
    if not any(_row(t, "action") == "TRANSFER" for _a, t in opts):
        return set()
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.pipeline import _drop_self_cancelling_transfers
    fields = TaxTransaction.__dataclass_fields__
    objs: List[TaxTransaction] = []
    back: Dict[int, int] = {}
    for a, t in opts:
        try:
            o = TaxTransaction(**{k: v for k, v in dict(t, account=a).items()
                                  if k in fields})
        except (TypeError, ValueError):
            continue
        back[id(o)] = id(t)
        objs.append(o)
    kept, _dropped = _drop_self_cancelling_transfers(objs)
    return {back[id(o)] for o in kept if o.action == "TRANSFER"}


def contracts(rows: Iterable[Tuple[str, Dict[str, Any]]], *,
              size_of: Optional[Callable[[Dict[str, Any]], float]] = None,
              stated_size: Optional[Callable[[str], Optional[float]]] = None,
              through: Optional[str] = None,
              pool_of: Optional[Callable[[str], str]] = None,
              split_at_open: bool = False) -> List[Lot]:
    """The option contracts of the books, matched per account and
    series (FIFO), in the order they were opened. `rows` are
    (account, native row) pairs, any actions, any order; `through`
    stops the replay after that date. `pool_of` (account -> pool): a
    SPLIT one account reports moves the series' contracts of every
    account of its pool (a Canadian project's blended taxable
    accounts: the event is the security's; reported in one account, it
    left the others' contracts in the old series, backlog TJ-0907);
    default: each account its own pool. `split_at_open`: a split takes
    effect at the start of its day whatever its clock stamp, after the
    day's opening balance, as the Canadian engine orders it (lib/
    country engine_sort_profile 'ca_main'): a morning buy-back of the
    new series before a noon-stamped split read as a new long (backlog
    TJ-0697). False: by the clock, as the US engine does."""
    from types import SimpleNamespace
    from taxjson.lib.missing_history import OrderStarts, unbacked_close
    size_of = size_of or default_size
    every = [(a, t) for a, t in rows
             if _row(t, "action", "") in ("BUYSELL", "ASSIGN", "SPLIT",
                                          "OPENING_BALANCE", "TRANSFER")
             and is_option_symbol(str(_row(t, "symbol", "") or "").upper())]
    # A day's order: the opening balance first whatever its stamp (it
    # is the position the day starts from: a native close stamped
    # midnight sorted before its synthetic midnight opening and read as
    # a new write, 2026-10-24 audit A22), then by clock, a split before
    # the trades of its moment (`split_at_open`: a split right after
    # the opening, before every trade of its day; TJ-0697).
    every.sort(key=lambda at: (str(_row(at[1], "date", "")),
                               0 if _row(at[1], "action") == "OPENING_BALANCE"
                               else 1 if split_at_open
                               and _row(at[1], "action") == "SPLIT"
                               else 2,
                               str(_row(at[1], "time", "") or "00:00:00"),
                               0 if _row(at[1], "action") == "SPLIT" else 1))
    opts = [(a, t) for a, t in every
            if through is None or str(_row(t, "date", "") or "") <= through]
    pool_of = pool_of or (lambda a: a)
    acct_of = {id(t): a for a, t in opts}
    # One split reported twice is one event, per pool here.
    splits = dedupe_split_events(
        [t for _a, t in opts if _row(t, "action") == "SPLIT"],
        per_account=True,
        account_of=lambda t: pool_of(acct_of.get(id(t), "")))
    split_ids = {id(t) for t in splits}
    transfer_ids = _kept_transfer_ids(opts)
    # A series' contract size is the series': a multiplier one of its
    # rows declares sizes the rows that state none (a futures option's
    # x1000 on its close alone priced the open at the default 100:
    # backlog TJ-0694). A row's own multiplier stays its own. A size is
    # no later event the as-of replay hides: a row after `through`
    # declares it too (backlog TJ-1087).
    declared: Dict[str, float] = {}
    for _a, t in every:
        m = float(_row(t, "multiplier", 0.0) or 0.0)
        s = str(_row(t, "symbol", "") or "").upper()
        if m > 0 and s not in declared:
            declared[s] = m

    def _size(t: Dict[str, Any], sym: str) -> float:
        if float(_row(t, "multiplier", 0.0) or 0.0) <= 0 and sym in declared:
            t = dict(t, multiplier=declared[sym])
        return float(size_of(t) or 0.0) or 100.0

    def _series_size(sym: str) -> Optional[float]:
        """A series' own contract size, where its rows or the size
        resolver (a ticker.map MULT line) state one; None: the default,
        stated nowhere."""
        if sym in declared:
            return declared[sym]
        # (a MULT line saying 100 states it too: a value equal to the
        # default does not say who stated it; backlog TJ-1223)
        said = stated_size(sym) if stated_size else None
        if said:
            return float(said)
        bare = {"symbol": sym}
        s = float(size_of(bare) or 0.0)
        return s if s > 0 and s != default_size(bare) else None
    pos: Dict[Tuple[str, str], float] = {}
    open_lots: Dict[Tuple[str, str], List[Lot]] = {}
    out: List[Lot] = []
    orders = OrderStarts()
    for seq, (acct, t) in enumerate(opts):
        sym = str(_row(t, "symbol", "") or "").upper()
        key = (acct, sym)
        q = float(_row(t, "quantity", 0.0) or 0.0)
        action = _row(t, "action", "")
        d = str(_row(t, "date", "") or "")
        if action == "SPLIT":
            if id(t) not in split_ids:
                continue
            ratio = q if q > 0 else 1.0
            new = str(_row(t, "symbol_new", "") or "").strip().upper() or sym
            # Renamed into a series of a size of its own: the lots take
            # it from here (TJ-1003); a series of no stated size keeps
            # the lot's.
            new_size = _series_size(new) if new != sym else None
            pool = pool_of(acct)
            for x in sorted({a for a, s in list(pos) + list(open_lots)
                             if s == sym and a != acct
                             and pool_of(a) == pool}) + [acct]:
                xkey = (x, sym)
                p_old = pos.pop(xkey, 0.0) * ratio
                moved = open_lots.pop(xkey, [])
                for lot in moved:
                    lot.open_units *= ratio
                    lot.splits.append((d, ratio, new))
                    if new_size and abs(new_size - lot.size_on(d)) > 1e-9:
                        lot.resizes.append((d, new_size))
                nk = (x, new)
                pos[nk] = pos.get(nk, 0.0) + p_old
                if moved:
                    # Renamed into a series with open contracts of its
                    # own: the queue stays first in, first out, by the
                    # opening's replay order (2026-10 audit #36; by open
                    # date alone a 09:00 write moved in closed after the
                    # destination's 10:00 write, 2026-10-24 audit A28).
                    dest = open_lots.setdefault(nk, [])
                    dest.extend(moved)
                    dest.sort(key=lambda lot: lot.seq)
            continue
        if q == 0:
            continue
        transfer = action == "TRANSFER"
        if transfer and id(t) not in transfer_ids:
            continue
        p = pos.get(key, 0.0)
        # A purchase's money is a magnitude, a sale's signed (2026-10
        # audit #34: a write netting less than its commission is a loss).
        # A transfer moves no money here: the contract's premium stays
        # in the books it came from.
        money = (0.0 if transfer
                 else float(_row(t, "net_amount", 0.0) or 0.0))
        if q > 0:
            money = abs(money)
        und = parse_option_underlying(sym) or sym
        strike = float(parse_option_strike(sym) or 0.0)
        size = _size(t, sym)
        cur = str(_row(t, "currency", "") or "").upper()
        if action == "OPENING_BALANCE":
            lot = Lot(account=acct, symbol=sym, underlying=und,
                      right=parse_option_right(sym) or "",
                      strike=strike, expiry=parse_option_expiry(sym) or "",
                      currency=cur, side=(BOUGHT if q > 0 else WRITTEN),
                      open_date=d, units=abs(q), size=size, cash_open=0.0,
                      unknown_cost=True, seq=seq, open_units=abs(q),
                      notional=strike * size * abs(q))
            open_lots.setdefault(key, []).append(lot)
            out.append(lot)
            pos[key] = p + q
            continue
        if q < 0:
            closing = min(-q, max(p, 0.0))
            opening = -q - closing
            kind = (TRANSFERRED if transfer
                    else EXERCISED if action == "ASSIGN"
                    else EXPIRED if _is_flat(t) else SOLD)
            sign = 1.0
        else:
            closing = min(q, max(-p, 0.0))
            opening = q - closing
            kind = (TRANSFERRED if transfer
                    else ASSIGNED if action == "ASSIGN"
                    else EXPIRED if _is_flat(t) else BOUGHT_BACK)
            sign = -1.0
        aq = abs(q)
        # The broker's own word that the trade CLOSES (IB code C) where
        # the books hold nothing it can close: what it closed was opened
        # before the data — a contract of unknown cost closed here, as a
        # .tt OPENING cost=unknown line makes it (opened on no date of
        # the data: `opened` ""), never a new write (a sale) or a new
        # long (a buy), as option-boundary reads it (backlog TJ-0565).
        ev = SimpleNamespace(quantity=q,
                             open_close=str(_row(t, "open_close", "") or ""),
                             date=d, time=str(_row(t, "time", "") or ""))
        p_order = orders.prev(key, ev, p)
        unbacked = 0.0
        if (action == "BUYSELL" and opening > _EPS
                and unbacked_close(ev, p, p_order)):
            unbacked, opening = opening, 0.0
            lot = Lot(account=acct, symbol=sym, underlying=und,
                      right=parse_option_right(sym) or "",
                      strike=strike, expiry=parse_option_expiry(sym) or "",
                      currency=cur, side=(BOUGHT if q < 0 else WRITTEN),
                      open_date="", units=unbacked, size=size, cash_open=0.0,
                      unknown_cost=True, seq=seq, open_units=0.0,
                      notional=strike * size * unbacked)
            lot.closes.append(Close(date=d, kind=kind, units=unbacked,
                                    cash=sign * money * (unbacked / aq),
                                    pnl=0.0, splits_seen=0))
            out.append(lot)
        if closing > _EPS:
            cash_close = sign * money * (closing / aq)
            per_close = cash_close / closing
            rem = closing
            for lot in open_lots.get(key, []):
                if rem <= _EPS:
                    break
                take = min(rem, lot.open_units)
                if take <= _EPS:
                    continue
                c_cash = per_close * take
                # A close in another currency than the open: its money
                # is not the lot's, so the lot's premium and P/L are
                # unknown (never converted here: 2026-10 audit #37).
                other = (cur if cur and lot.currency and cur != lot.currency
                         and not transfer else "")
                if other:
                    lot.unknown_cost = True
                lot.closes.append(Close(date=d, kind=kind, units=take,
                                        cash=c_cash,
                                        pnl=(0.0 if transfer else
                                             lot.per_unit_on(d) * take
                                             + c_cash),
                                        splits_seen=len(lot.splits),
                                        currency=other))
                lot.open_units -= take
                rem -= take
            open_lots[key] = [l for l in open_lots.get(key, [])
                              if l.open_units > _EPS]
        if opening > _EPS:
            lot = Lot(account=acct, symbol=sym, underlying=und,
                      right=parse_option_right(sym) or "",
                      strike=strike, expiry=parse_option_expiry(sym) or "",
                      currency=cur, side=(WRITTEN if q < 0 else BOUGHT),
                      open_date=d, units=opening, size=size,
                      cash_open=sign * money * (opening / aq),
                      unknown_cost=transfer, transfer_in=transfer,
                      seq=seq, open_units=opening,
                      notional=strike * size * opening)
            open_lots.setdefault(key, []).append(lot)
            out.append(lot)
        # (the contracts an unbacked close closed were held before it)
        pos[key] = p + q + (unbacked if q < 0 else -unbacked)
    return out


def _days(a: str, b: str) -> int:
    """Days from `a` to `b` (exclusive of `b`), never negative."""
    try:
        return max(0, (date.fromisoformat(b) - date.fromisoformat(a)).days)
    except ValueError:
        return 0


def _next_day(d: str) -> str:
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


# A listing's last part that is the security's own, not a market: units,
# warrants, rights, preferreds, debentures (SYN.UN, SYN.WS).
_SECURITY_PARTS = frozenset(("UN", "WS", "WT", "RT", "PR", "DB"))


def underlying_matches(und: str, wanted: Iterable[str]) -> bool:
    """`QQQ` names QQQ.US (any market), `QQQ.US` that listing; `SYN.B`
    names the class share SYN.B.US, never SYN.US: a market suffix comes
    off, the security's own part stays (the first dot dropped the
    class, backlog TJ-0692). A market is a known listing suffix, or a
    last part of two letters or more that is no designator of the
    security (`SYNX.DE`, as a manual or generic book spells it: only
    the built-in venues came off and the bare ticker named nothing,
    backlog TJ-1084); a one-letter part is a class."""
    from taxjson.lib.markets import strip_listing_suffix
    u = und.upper()
    root = strip_listing_suffix(u)
    if root == u and "." in u:
        base, part = u.rsplit(".", 1)
        if (base and len(part) >= 2 and part.isalpha()
                and part not in _SECURITY_PARTS):
            root = base
    for w in wanted:
        w = str(w or "").strip().upper()
        if w and (w == u or w == root):
            return True
    return False


def _blank() -> Dict[str, Any]:
    return {"written_contracts": 0.0, "bought_contracts": 0.0,
            "received": 0.0, "paid": 0.0, "cash_flow": 0.0,
            "closed_contracts": 0.0, "realized": 0.0,
            "outcomes": {}, "wins": 0.0,
            "open_contracts": 0.0, "open_cash": 0.0,
            "notional_days": 0.0, "unknown_cost": 0,
            "unknown_closes": 0.0, "currency_mismatch": [],
            "transferred_out": 0.0, "transferred_in": 0.0}


def _add_notional_days(acc: Dict[str, Any], lot: Lot, start: str,
                       end: str, end_excl: str) -> None:
    units = lot.units_on(end)
    if units <= _EPS:
        return
    opened = lot.open_date

    def span(a: str, b: str, cu: float) -> None:
        """`cu` contracts (the units of `end`) open over [a, b): the
        lot's per-contract notional of each stretch between its splits
        and renames. One notional over the whole stretch priced the
        days before a strike adjustment at the strike after it
        (2026-10-24 audit A05)."""
        if b <= a:
            return
        marks = sorted({d for d, _r, _n in lot.splits if a < d < b})
        edges = [a] + marks + [b]
        for lo, hi in zip(edges, edges[1:]):
            acc["notional_days"] += (_days(lo, hi) * cu
                                     * lot.notional_on(lo) / units)
    # Each close ends the exposure of its units; the rest runs to the
    # window's end.
    taken = 0.0
    for c in lot.closes:
        if c.date > end:
            continue
        cu = lot.close_units_on(c, end)
        taken += cu
        span(max(opened, start), min(c.date, end_excl), cu)
    left = units - taken
    if left > _EPS:
        span(max(opened, start), end_excl, left)


def _fold(acc: Dict[str, Any], lot: Lot, start: str, end: str) -> None:
    """Add a lot's window figures, in the units of `end`."""
    end_excl = _next_day(end)
    # Opened before the data (a missing-history lot), not unknown for a
    # close in another currency alone: that close is named on its own,
    # whenever it is (it makes the lot's every P/L unknown).
    mixed = [c for c in lot.closes if c.currency]
    before_data = lot.unknown_cost and not mixed and not lot.transfer_in
    acc["currency_mismatch"].extend(
        f"{lot.underlying_on(end)} {c.date} {c.currency}" for c in mixed)
    # Received / paid: every dollar in and out of the window, the opens
    # and the closes alike (a put written and bought back: received
    # its premium, paid the buy-back).
    if start <= lot.open_date <= end:
        if lot.transfer_in:
            # Not written or bought here: the contract arrived.
            acc["transferred_in"] += lot.units_on(end)
        elif lot.side == WRITTEN:
            acc["written_contracts"] += lot.units_on(end)
        else:
            acc["bought_contracts"] += lot.units_on(end)
        acc["received"] += max(lot.cash_open, 0.0)
        acc["paid"] += max(-lot.cash_open, 0.0)
        acc["cash_flow"] += lot.cash_open
        if before_data:
            acc["unknown_cost"] += 1
    for c in lot.closes:
        if start <= c.date <= end:
            cu = lot.close_units_on(c, end)
            acc["closed_contracts"] += cu
            if not c.currency:
                # (money in another currency is not added to this one's)
                acc["received"] += max(c.cash, 0.0)
                acc["paid"] += max(-c.cash, 0.0)
                acc["cash_flow"] += c.cash
            acc["outcomes"][c.kind] = acc["outcomes"].get(c.kind, 0.0) + cu
            if c.kind == TRANSFERRED:
                # The contracts left with their premium: nothing was
                # realized here, nothing is unknown.
                acc["transferred_out"] += cu
                continue
            if lot.unknown_cost:
                # The premium is unknown: no P/L, no win or loss (a
                # zero placeholder read as a definite loss: 2026-10
                # audit R06).
                acc["unknown_closes"] += cu
                if before_data and lot.open_date < start:
                    acc["unknown_cost"] += 1
                continue
            acc["realized"] += c.pnl
            if c.pnl > 0.005:
                acc["wins"] += cu
    left = lot.units_open_on(end)
    if lot.open_date <= end and left > _EPS:
        acc["open_contracts"] += left
        acc["open_cash"] += lot.per_unit_on(end) * left
    _add_notional_days(acc, lot, start, end, end_excl)


def _finish(acc: Dict[str, Any], days: int,
            shares: Tuple[float, float] = (0.0, 0.0),
            capital: Optional[Tuple[str, float]] = None,
            other: Optional[Dict[str, Tuple[float, float]]] = None,
            shares_unknown: bool = False
            ) -> Dict[str, Any]:
    """The window's figures with the yield: on the cost of the shares
    held (`shares` = (quantity, cost)), else on the average strike
    notional open in the window; `capital` = (basis, amount) imposes
    the denominator (a total: the sum of its rows' own). A window with
    a close whose premium is unknown has no realized P/L, win rate or
    yield (None). `other` = {currency: (quantity, cost)}: shares held
    whose cost is in another currency than the options' — never
    converted here, so there is no yield (basis OTHER_CURRENCY); they
    were added to the capital as if in the options' currency (backlog
    TJ-0543). `shares_unknown`: the shares' cost is not all known (an
    opening with cost=unknown): the capital is incomplete, so there is
    no yield (the known part read as the whole: backlog TJ-0774)."""
    avg = acc["notional_days"] / days if days > 0 else 0.0
    sq, sc = shares
    other = {k: v for k, v in sorted((other or {}).items())
             if v[0] > _EPS}
    if capital is not None:
        basis, cap = capital
    elif other:
        basis, cap = OTHER_CURRENCY, 0.0
    elif sq > _EPS and (sc > 0.005 or shares_unknown):
        basis, cap = "shares", sc
    elif avg > 0.005:
        basis, cap = "notional", avg
    else:
        basis, cap = "", 0.0
    unknown = acc["unknown_closes"] > _EPS
    # A contract transferred out closed nothing: no P/L, no yield on it.
    settled = acc["closed_contracts"] - acc["transferred_out"]
    y = ((acc["realized"] / cap)
         if cap > 0.005 and not unknown and settled > _EPS
         and basis != OTHER_CURRENCY and not shares_unknown
         else None)
    out = {k: (round(v, 2) if isinstance(v, float) else v)
           for k, v in acc.items() if k != "notional_days"}
    out["outcomes"] = {k: round(v, 4) for k, v in
                       sorted(acc["outcomes"].items())}
    for k in ("written_contracts", "bought_contracts", "closed_contracts",
              "open_contracts", "wins", "unknown_closes",
              "transferred_out", "transferred_in"):
        out[k] = round(acc[k], 4)
    out["shares_held"] = round(sq, 4)
    out["shares_cost"] = round(sc, 2)
    out["shares_cost_unknown"] = bool(shares_unknown)
    out["shares_other_currency"] = {
        k: {"shares": round(q, 4), "cost": round(c, 2)}
        for k, (q, c) in other.items()}
    known = settled - acc["unknown_closes"]
    # The win rate: the contracts closed at a profit over the contracts
    # closed in the window (a close at exactly zero is not a win).
    out["win_rate"] = (round(acc["wins"] / known, 4)
                       if known > _EPS and not unknown else None)
    if unknown:
        out["realized"] = None
    out["avg_notional"] = round(avg, 2)
    out["yield_basis"] = basis
    out["capital"] = round(cap, 2)
    out["_capital"] = cap                 # unrounded, for the totals
    out["yield"] = None if y is None else round(y, 6)
    out["annualized"] = (None if y is None or days <= 0
                         else round(y * 365.0 / days, 6))
    return out


def analyze(lots: Iterable[Lot], *, start: str, end: str, scope: str,
            underlyings: Iterable[str] = (),
            accounts: Optional[Iterable[str]] = None,
            groups: Optional[Dict[str, str]] = None,
            shares: Optional[Dict[tuple, tuple]] = None,
            unknown_shares: Iterable[tuple] = (),
            aliases: Optional[Dict[str, str]] = None
            ) -> Dict[str, Any]:
    """The window's figures per underlying and per account, side and
    right, the contracts closed in the window and those open at its
    end, with totals per currency. `shares` = {(account, underlying[,
    currency]): (quantity, cost)} of the underlying's shares held now,
    the yield's capital where there are any and their cost is in the
    options' currency (no currency: taken as the options').
    `unknown_shares`: the keys of `shares` whose cost is not all known
    (a holding opened with cost=unknown): no yield on them (backlog
    TJ-0774). `aliases` = {option underlying: share line}: a class
    share's option root (SYNB.TO for SYN.B.TO, core.class_root_aliases)
    is that share line (backlog TJ-0910)."""
    wanted = [w for w in underlyings if str(w or "").strip()]
    accts = set(accounts) if accounts is not None else None
    groups = groups or {}
    aliases = {str(k).upper(): str(v).upper()
               for k, v in (aliases or {}).items()}
    unknown_keys = set(unknown_shares or ())
    # (account, underlying, quantity, cost, currency, cost unknown): a
    # key or value without the currency is taken as the options'.
    held_rows = []
    for key, v in (shares or {}).items():
        a, u = key[0], key[1]
        if accts is not None and a not in accts:
            continue
        scur = key[2] if len(key) > 2 else (v[2] if len(v) > 2 else "")
        held_rows.append((a, str(u).upper(), float(v[0]), float(v[1]),
                          str(scur or "").upper(), key in unknown_keys))
    days = _days(start, _next_day(end))
    by_und: Dict[Tuple[str, str], Dict[str, Any]] = {}
    by_part: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
    by_cur: Dict[str, Dict[str, Any]] = {}
    closed: List[Dict[str, Any]] = []
    open_: List[Dict[str, Any]] = []
    seen_unds: set = set()
    for lot in lots:
        if accts is not None and lot.account not in accts:
            continue
        # The series as it stood at the window's end: a renamed series
        # is found by its new underlying (2026-10 audit R05, R10).
        raw_und = lot.underlying_on(end)
        und = aliases.get(raw_und.upper(), raw_und)
        sym = lot.symbol_on(end)
        strike, expiry = lot.strike_on(end), lot.expiry_on(end)
        seen_unds.update({und, raw_und})
        if wanted and not (underlying_matches(und, wanted)
                           or underlying_matches(raw_und, wanted)):
            continue
        touches = ((start <= lot.open_date <= end)
                   or any(start <= c.date <= end for c in lot.closes)
                   or (lot.open_date <= end
                       and lot.units_open_on(end) > _EPS))
        if not touches:
            continue
        k = (und, lot.currency)
        _fold(by_und.setdefault(k, _blank()), lot, start, end)
        pk = (und, lot.currency, lot.account, lot.side, lot.right)
        _fold(by_part.setdefault(pk, _blank()), lot, start, end)
        _fold(by_cur.setdefault(lot.currency, _blank()), lot, start, end)
        per_unit = lot.per_unit_on(end)
        for c in lot.closes:
            if start <= c.date <= end:
                cu = lot.close_units_on(c, end)
                moved = c.kind == TRANSFERRED
                closed.append({
                    "underlying": und, "symbol": sym,
                    "account": lot.account, "side": lot.side,
                    "right": lot.right, "strike": strike,
                    "expiry": expiry, "currency": lot.currency,
                    "opened": lot.open_date, "closed": c.date,
                    "how": c.kind, "contracts": round(cu, 4),
                    "premium": (None if lot.unknown_cost
                                else round(per_unit * cu, 2)),
                    # (a transfer out has no close money and no P/L)
                    "close_cash": (None if c.currency or moved
                                   else round(c.cash, 2)),
                    "pnl": (None if lot.unknown_cost or moved
                            else round(c.pnl, 2)),
                    "days_open": _days(lot.open_date, c.date)})
        left = lot.units_open_on(end)
        if lot.open_date <= end and left > _EPS:
            open_.append({
                "underlying": und, "symbol": sym,
                "account": lot.account, "side": lot.side,
                "right": lot.right, "strike": strike,
                "expiry": expiry, "currency": lot.currency,
                "opened": lot.open_date, "contracts": round(left, 4),
                "premium": (None if lot.unknown_cost
                            else round(per_unit * left, 2)),
                "days_open": _days(lot.open_date, _next_day(end)),
                "expired": bool(expiry and expiry < end)})

    def _shares_of(und: str, cur: str, acct: Optional[str] = None):
        """(quantity, cost) of the underlying's shares whose cost is in
        `cur`, {currency: (quantity, cost)} of those in another
        currency (a share value without one is taken as `cur`), and
        whether the cost of those in `cur` is not all known."""
        q = c = 0.0
        unknown = False
        other: Dict[str, Tuple[float, float]] = {}
        for a, u, sq, sc, scur, unk in held_rows:
            scur = scur or cur
            if u == und and (acct is None or a == acct) and (
                    acct is not None or any(
                        p[0] == und and p[1] == cur and p[2] == a
                        for p in by_part)):
                if scur == cur:
                    q += sq
                    c += sc
                    unknown = unknown or unk
                else:
                    oq, oc = other.get(scur, (0.0, 0.0))
                    other[scur] = (oq + sq, oc + sc)
        return (q, c), other, unknown
    rows = []
    for (und, cur), acc in sorted(by_und.items()):
        held, other, unk = _shares_of(und, cur)
        r = _finish(acc, days, held, other=other, shares_unknown=unk)
        r.update({"underlying": und, "currency": cur,
                  "accounts": sorted({p[2] for p in by_part
                                      if p[0] == und and p[1] == cur})})
        rows.append(r)
    parts = []
    for (und, cur, acct, side, right), acc in sorted(by_part.items()):
        held, other, unk = _shares_of(und, cur, acct)
        r = _finish(acc, days, held, other=other, shares_unknown=unk)
        r.update({"underlying": und, "currency": cur, "account": acct,
                  "group": groups.get(acct, ""), "side": side,
                  "right": right})
        parts.append(r)
    totals = {}
    for cur, acc in sorted(by_cur.items()):
        mine = [r for r in rows if r["currency"] == cur]
        sq = sum(r["shares_held"] for r in mine)
        sc = sum(r["shares_cost"] for r in mine)
        # The capital is each underlying's own denominator added up (a
        # share cost alone dropped the uncovered underlyings' notional:
        # 2026-10 audit R07).
        bases = {r["yield_basis"] for r in mine if r["yield_basis"]}
        cap = sum(r["_capital"] for r in mine)
        basis = (OTHER_CURRENCY if OTHER_CURRENCY in bases
                 else next(iter(bases)) if len(bases) == 1
                 else "mixed" if bases else "")
        other: Dict[str, Tuple[float, float]] = {}
        for r in mine:
            for k, v in r["shares_other_currency"].items():
                oq, oc = other.get(k, (0.0, 0.0))
                other[k] = (oq + v["shares"], oc + v["cost"])
        t = _finish(acc, days, (sq, sc), capital=(basis, cap), other=other,
                    shares_unknown=any(r["shares_cost_unknown"]
                                       for r in mine))
        t["notional_capital"] = round(sum(
            r["_capital"] for r in mine if r["yield_basis"] == "notional"), 2)
        totals[cur] = t
    for r in rows + parts + list(totals.values()):
        r.pop("_capital", None)
    unmatched = [w for w in wanted
                 if not any(underlying_matches(u, [w]) for u in seen_unds)]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "option-yield",
        "scope": scope, "start": start, "end": end, "days": days,
        "underlyings": [str(w).strip().upper() for w in wanted],
        "unmatched_underlyings": [str(w).strip().upper() for w in unmatched],
        "rows": rows, "parts": parts,
        "closed": sorted(closed, key=lambda c: (c["closed"], c["opened"],
                                                c["symbol"])),
        "open": sorted(open_, key=lambda o: (o["expiry"], o["opened"],
                                             o["symbol"])),
        "totals": totals,
    }


def _pct(v: Optional[float]) -> str:
    return "-" if v is None else f"{v * 100:,.2f}%"


def _money(v: Optional[float]) -> str:
    """A money figure; `?` when it is unknown (a premium before the
    data)."""
    from taxjson.lib.report_model import fmt_money
    return "?" if v is None else fmt_money(v)


def _n(v: float) -> str:
    return f"{v:,.4f}".rstrip("0").rstrip(".") if v else "0"


def _rate(v: Optional[float]) -> str:
    """A win rate: `67%`, `-` with nothing closed."""
    return "-" if v is None else f"{v * 100:.0f}%"


def _side_right(side: str, right: str) -> str:
    return f"{side} {({'C': 'call', 'P': 'put'}).get(right, right or '?')}"


def render(doc: Dict[str, Any], cmd: str = "tjs option-yield"):
    """The text view (lib/out.Doc): a legend, the contracts closed in
    the window, those open at its end, then the summary per underlying
    (and per account, side and right), the totals per currency and
    what to look at."""
    from taxjson.lib import out
    from taxjson.lib.report_model import fmt_money
    which = (", ".join(doc["underlyings"]) + ": " if doc["underlyings"]
             else "")
    d = out.Doc(f"OPTION YIELD — planning numbers, {which}{doc['scope']} "
                f"({doc['start']} to {doc['end']}, {doc['days']} days)")
    rows = doc["rows"]
    if not rows:
        d.para("No option contract of " + (", ".join(doc["underlyings"])
                                           if doc["underlyings"]
                                           else "any underlying")
               + " was opened, closed or open in the window.")
        for w in doc["unmatched_underlyings"]:
            d.item(out.act(f"{w}: no option on it in the books (the "
                           f"underlying's ticker, e.g. QQQ or QQQ.US)",
                           "tjs events"), bullet="")
        return d
    d.para("Planning numbers, not filing. WIN% = closed at a profit; "
           "YIELD = P/L / share cost (* strike notional), annualized.")
    if doc["closed"]:
        body = []
        for c in doc["closed"]:
            moved = c["how"] == TRANSFERRED
            body.append([
                c["closed"], c["symbol"], c["account"], c["currency"],
                _side_right(c["side"], c["right"]), _n(c["contracts"]),
                c["how"], str(c["days_open"]),
                ("?" if c["premium"] is None else fmt_money(c["premium"])),
                ("-" if moved else _money(c["close_cash"])),
                ("-" if moved else "?" if c["pnl"] is None
                 else fmt_money(c["pnl"]))])
        d.table(["CLOSED", "SERIES", "ACCOUNT", "CUR", "SIDE", "QTY", "HOW",
                 "DAYS", "PREMIUM", "CLOSE", "P/L"],
                body, aligns=["<", "<", "<", "<", "<", ">", "<", ">", ">",
                              ">", ">"], drop=(7, 4, 3, 2))
    else:
        d.para("No contract closed in the window.")
    d.section(f"OPEN AT {doc['end']}")
    if doc["open"]:
        body = []
        for o in doc["open"]:
            body.append([
                o["opened"], o["symbol"], o["account"], o["currency"],
                _side_right(o["side"], o["right"]), _n(o["contracts"]),
                o["expiry"] + (" (past)" if o["expired"] else ""),
                str(o["days_open"]),
                ("?" if o["premium"] is None else fmt_money(o["premium"]))])
        d.table(["OPENED", "SERIES", "ACCOUNT", "CUR", "SIDE", "QTY",
                 "EXPIRY", "DAYS", "PREMIUM"],
                body, aligns=["<", "<", "<", "<", "<", ">", "<", ">", ">"],
                drop=(7, 4, 3, 2))
    else:
        d.para("None.")
    d.section("SUMMARY")
    body = []
    for r in rows:
        star = "*" if r["yield_basis"] == "notional" else ""
        body.append([
            r["underlying"], r["currency"],
            _n(r["written_contracts"]), _n(r["bought_contracts"]),
            fmt_money(r["received"]), fmt_money(r["paid"]),
            _money(r["realized"]), _rate(r["win_rate"]),
            _n(r["open_contracts"]),
            _n(r["shares_held"]),
            ("?" if r.get("shares_cost_unknown")
             else fmt_money(r["shares_cost"])),
            _pct(r["yield"]) + star, _pct(r["annualized"]) + star])
    d.table(["UNDERLYING", "CUR", "WRITTEN", "BOUGHT", "RECEIVED", "PAID",
             "REALIZED", "WIN%", "OPEN", "SHARES", "SHARE COST", "YIELD",
             "ANNUAL"],
            body, aligns=["<", "<", ">", ">", ">", ">", ">", ">", ">", ">",
                          ">", ">", ">"],
            drop=(9, 10, 1, 8, 3, 5))
    if len(doc["parts"]) > len(rows):
        d.blank()
        body = []
        for p in doc["parts"]:
            oc = ", ".join(f"{_n(v)} {k}" for k, v in p["outcomes"].items())
            star = "*" if p["yield_basis"] == "notional" else ""
            body.append([
                p["underlying"], p["account"],
                _side_right(p["side"], p["right"]),
                _n(p["written_contracts"] + p["bought_contracts"]),
                fmt_money(p["received"] - p["paid"]),
                _money(p["realized"]), _rate(p["win_rate"]),
                _pct(p["annualized"]) + star, oc or "-"])
        d.table(["UNDERLYING", "ACCOUNT", "SIDE", "OPENED", "NET CASH",
                 "REALIZED", "WIN%", "ANNUAL", "CLOSED HOW"],
                body, aligns=["<", "<", "<", ">", ">", ">", ">", ">", "<"],
                drop=(3, 8))
    d.blank()
    kv = []
    for cur, t in doc["totals"].items():
        if t["realized"] is None:
            kv.append((f"Realized, {cur}",
                       f"unknown: {_n(t['unknown_closes'])} of the "
                       f"{_n(t['closed_contracts'])} contract(s) closed "
                       f"had no premium in the books; net cash "
                       f"{fmt_money(t['cash_flow'])}"))
            continue
        moved = (f"; {_n(t['transferred_out'])} transferred out (no P/L)"
                 if t["transferred_out"] > _EPS else "")
        kv.append((f"Realized, {cur}",
                   f"{fmt_money(t['realized'])} on {_n(t['closed_contracts'])}"
                   f" contract(s) closed ({_n(t['wins'])} at a profit: "
                   f"win rate {_rate(t['win_rate'])}); "
                   f"net cash {fmt_money(t['cash_flow'])}{moved}"))
        if t["yield_basis"] == OTHER_CURRENCY:
            held = "; ".join(
                f"{_n(v['shares'])} share(s) held cost "
                f"{fmt_money(v['cost'])} {k}"
                for k, v in t["shares_other_currency"].items())
            kv.append((f"Yield, {cur}",
                       f"none: {held}, not {cur} (never converted here)"))
            continue
        if t.get("shares_cost_unknown"):
            kv.append((f"Yield, {cur}",
                       f"unknown: the cost of the {_n(t['shares_held'])} "
                       f"share(s) held is not all known (known part "
                       f"{fmt_money(t['shares_cost'])})"))
            continue
        if t["yield_basis"] == "shares":
            on = (f"the {fmt_money(t['shares_cost'])} cost of "
                  f"{_n(t['shares_held'])} share(s) held")
        elif t["yield_basis"] == "mixed":
            on = (f"{fmt_money(t['capital'])} of capital (the "
                  f"{fmt_money(t['shares_cost'])} cost of "
                  f"{_n(t['shares_held'])} share(s) held, and an average "
                  f"strike notional of {fmt_money(t['notional_capital'])} "
                  f"where none are)")
        else:
            on = (f"an average strike notional of "
                  f"{fmt_money(t['avg_notional'])} (no shares held)")
        kv.append((f"Yield, {cur}",
                   f"{_pct(t['yield'])} on {on}, {_pct(t['annualized'])}"
                   f" annualized"))
    d.kv(kv)
    acts = []
    opened = [o for o in doc["open"] if o["expired"]]
    if opened:
        acts.append(out.act(
            f"{_n(sum(o['contracts'] for o in opened))} open contract(s) "
            f"past expiry with no expiry or assignment row: add the "
            f"export covering the expiry date"))
    unk = [r["underlying"] for r in rows if r["unknown_cost"]]
    if unk:
        acts.append(out.act(
            f"{len(unk)} underlying(s) with a contract opened before the "
            f"data (premium unknown): {', '.join(unk[:3])}",
            "tjs find-missing-history"))
    moved_in = sum(r["transferred_in"] for r in rows)
    if moved_in > _EPS:
        acts.append(out.act(
            f"{_n(moved_in)} contract(s) received by transfer (premium "
            f"unknown: it is in the books the contract came from)",
            "tjs events"))
    # (one close row taking two contracts is one row to check)
    mixed = list(dict.fromkeys(m for r in rows
                               for m in r["currency_mismatch"]))
    if mixed:
        acts.append(out.act_list(
            f"{len(mixed)} close(s) not in the open's currency: check "
            f"that row's currency", mixed))
    unk_sh = [r["underlying"] for r in rows if r.get("shares_cost_unknown")]
    if unk_sh:
        acts.append(out.act(
            f"{len(unk_sh)} underlying(s) whose shares held have an "
            f"unknown cost (no yield): {', '.join(unk_sh[:3])}",
            "tjs find-missing-history"))
    xcur = [r["underlying"] for r in rows
            if r["yield_basis"] == OTHER_CURRENCY]
    if xcur:
        acts.append(out.act(
            f"{len(xcur)} underlying(s) whose shares cost another currency "
            f"than its options (no yield): {', '.join(xcur[:3])}",
            "tjs list"))
    for w in doc["unmatched_underlyings"]:
        acts.append(out.act(f"{w}: no option on it in the books",
                            "tjs events"))
    if acts:
        d.blank()
        for a in acts:
            d.item(a[len(out.ACT):], bullet=out.ACT)
    return d
