"""Win/lose statistics on closed positions — the data behind `taxjson
stats` (a trader's view of the books, never a filing number).

One CLOSED TRADE per closing disposition, in six asset classes:

  long-shares      a sale of shares / ETF units you held long
  short-shares     buying back shares you had sold short
  long-options     closing (selling, or expiring) an option you bought
  written-options  closing an option you WROTE (bought back, expired or
                   assigned)
  futures          a closed futures contract (the settlement P/L)
  crypto           a crypto disposition

Every amount is ECONOMIC P/L in the base currency BEFORE any superficial-
loss / wash-sale denial (`economic_pnl`): a gains row's `raw_gain` (its
`gain` when the row predates that field) — the loss sale's whole loss —
plus the earlier denied loss its cost carries: a replacement's sale counts
the shares' own purchase cost, never the s.53(1)(f) / §1091(d) addition
the denial put in its basis, so a denied loss counts once, on its own
sale. The denied total is reported separately, never subtracted.

Shares, long options, futures and crypto come straight from the engine's
gains rows (what `winners` / `leaps-sum` read). A partial close is one
trade per closing disposition, as the engine books it.

Written options are built from the account's base-currency book instead,
because the gains rows of a written option depend on the project's
option_premium_timing (grant timing books the premium at the write and the
buy-back as a separate record; an expiry of an earlier year's write has no
record at all), and an ASSIGNED written option has no option record under
either timing (its premium is folded into the shares' proceeds or cost by
the tax rules). Here each write's contracts queue first in, first out per
account and series; each closing row (a buy-back, an expiry at 0, an
assignment) is one trade: the premium of the contracts it closes minus
what the close paid. The same book gives the same rows under either
timing.

An assigned written option's premium is counted once: as that option's
P/L. The share rows lose it again — a call's premium comes out of the
sale at the strike, a put's out of the delivered shares: the part that
covers a short out of that cover, the rest out of the next sales (per
delivered share, in date order). Which rows those are follows the
engine's own pairing (CA-OPT-08 / US-OPT-05): a leg within 3 days before
or 7 after the option row, else the premium stays with the option (the
engine leaves it undrained and warns); a cash-settled contract (its
underlying never trades as stock in the account, the engine's resolver)
has no share leg at all. Exercised LONG options are not trades: their
cost carried into the shares.
"""
from __future__ import annotations

from collections import deque
from datetime import date as _date
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from taxjson.lib.core import (_make_assign_underlying_resolver,
                              is_option_symbol, parse_option_right,
                              parse_option_strike, parse_option_underlying)
from taxjson.lib.futures import is_plain_future
from taxjson.lib.price_chain import is_crypto_symbol

CLASSES: Tuple[Tuple[str, str], ...] = (
    ("long-shares", "long shares and ETFs"),
    ("short-shares", "short shares (bought back)"),
    ("long-options", "options bought, then sold or expired"),
    ("written-options", "options written (premium minus buy-back; "
                        "expiry or assignment keeps the premium)"),
    ("futures", "closed futures contracts"),
    ("crypto", "crypto dispositions"),
)

_INCOME = frozenset({"DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST",
                     "FEE", "DISALLOW", "ADJUST"})
_EPS = 1e-9
# The engines pair an assignment's stock leg dated up to 3 days BEFORE
# the option row, or up to 7 after it (tax-logic CA-OPT-08 / US-OPT-05).
_LEG_LEAD_DAYS = 3
_LEG_LAG_DAYS = 7


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def classify(row: Dict[str, Any], crypto_account: bool) -> Optional[str]:
    """The class of a gains row, None for a row stats does not count
    (a written option's rows: rebuilt from the book instead)."""
    sym = str(row.get("symbol") or "")
    short = row.get("direction") == "SHORT" or bool(row.get("grant"))
    if is_plain_future(sym):
        return "futures"
    if is_option_symbol(sym):
        return None if short else "long-options"
    if crypto_account or is_crypto_symbol(sym):
        return "crypto"
    return "short-shares" if short else "long-shares"


def _as_tx(r: Dict[str, Any]) -> SimpleNamespace:
    """A book row as the engine's assignment resolver reads one (by
    attribute)."""
    return SimpleNamespace(symbol=str(r.get("symbol") or ""),
                           action=r.get("action"),
                           account=str(r.get("account") or ""),
                           date=str(r.get("date") or ""),
                           exercise_of=r.get("exercise_of") or "")


def written_option_trades(rows: Iterable[Dict[str, Any]],
                          date_of: Callable[[Dict[str, Any]], str],
                          in_books: Optional[Callable[[Dict[str, Any]],
                                                      bool]] = None,
                          ) -> Dict[str, Any]:
    """Closed written-option trades of ONE account's base-currency book.

    `in_books(row)`: whether the closing row falls in the span the gains
    files cover (a year project's books hold one tax year; the base book
    holds the whole history) — a close outside it is left out, as the
    other classes' rows are.

    An OPENING_BALANCE row (a missing-history `OPENING ... cost=unknown`
    line, synthesized by the caller as the gains stage does) is a
    position held before the data: a long one is sold out of, never
    read as a write (2026-10 audit #59); a short one is a write whose
    premium is unknown, so its close is no trade (`unknown`). A TRANSFER
    moves contracts without a trade: one out takes the written contracts
    off the queue (or the longs), one in adds longs, or a short whose
    premium is unknown (2026-10-24 audit A11).

    An ASSIGN close is a share credit only when the engine's resolver
    finds the underlying trading as stock in the account; a cash-settled
    contract's P/L is the premium minus the cash paid, with no credit
    (`cash_settled` on the trade; audit A44).

    Returns {'trades': [ {date, symbol, account, pnl, kind, contracts,
    premium, in_books, cash_settled} ], 'credits': [ {account, underlying,
    right, shares, amount, date, strike, delivered} ] (an assigned
    option's premium the tax rules folded into its shares; `underlying`
    the stock line the engine pairs it with, `delivered` the assignment
    row's shares), 'exercised_long': n, 'exercises': [ {date, in_books} ]
    (the exercises of long options, which are not trades), 'unknown':
    [ {date, in_books} ] (the closes of a write with an unknown
    premium)}."""
    from taxjson.lib.corporate_timeline import dedupe_split_events
    rows = [r for r in rows if isinstance(r, dict)]
    # The engine's own physical-versus-cash-settled test (lib/core
    # _make_assign_underlying_resolver, as option-boundary reads it):
    # None when the underlying never trades as stock in the account.
    resolve_underlying = _make_assign_underlying_resolver(
        [_as_tx(r) for r in rows], date_of, quiet=True)
    book = [r for r in rows
            if r.get("action") in ("BUYSELL", "ASSIGN", "SPLIT",
                                   "OPENING_BALANCE", "TRANSFER")
            and is_option_symbol(str(r.get("symbol") or ""))]
    # A split first at its stamp, then an opening, as the engines order
    # them.
    book.sort(key=lambda r: (str(r.get("date") or ""),
                             str(r.get("time") or ""),
                             0 if r.get("action") == "SPLIT"
                             else 1 if r.get("action") == "OPENING_BALANCE"
                             else 2))
    # A lot: [contracts open, premium per contract (None: unknown), the
    # write's date, its replay ordinal]. The ordinal keeps the queue's
    # first-in order through a rename: two writes of one day sort by
    # their stamp, not by date alone (2026-10-24 audit A28).
    ordinal = {id(r): n for n, r in enumerate(book)}
    # One split reported twice is one event, per account here; it
    # moves and scales the queued contracts (a renamed series closed
    # against an empty queue and read as a new long: 2026-10 audit R04).
    splits = {id(r) for r in dedupe_split_events(
        [r for r in book if r.get("action") == "SPLIT"], per_account=True)}
    longs: Dict[Tuple[str, str], float] = {}
    shorts: Dict[Tuple[str, str], deque] = {}
    trades: List[Dict[str, Any]] = []
    credits: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    exercises: List[Dict[str, Any]] = []
    for r in book:
        sym = str(r["symbol"])
        acct = str(r.get("account") or "")
        key = (acct, sym)
        q = _f(r.get("quantity"))
        if r.get("action") == "OPENING_BALANCE":
            if q > _EPS:
                longs[key] = longs.get(key, 0.0) + q
            elif q < -_EPS:
                shorts.setdefault(key, deque()).append(
                    [-q, None, str(r.get("date") or ""), ordinal[id(r)]])
            continue
        if r.get("action") == "TRANSFER":
            # Contracts moved, nothing closed: no trade, no credit.
            if q > _EPS:
                # Arriving contracts take a short moved out off the
                # queue; the rest are longs that came in.
                rem = q
                lots = shorts.get(key)
                while rem > _EPS and lots:
                    m = min(lots[0][0], rem)
                    lots[0][0] -= m
                    rem -= m
                    if lots[0][0] <= _EPS:
                        lots.popleft()
                if rem > _EPS:
                    longs[key] = longs.get(key, 0.0) + rem
            elif q < -_EPS:
                # Leaving contracts come out of the longs; the rest is a
                # short that came in, its premium unknown.
                held = longs.get(key, 0.0)
                moved = min(held, -q)
                longs[key] = held - moved
                rest = -q - moved
                if rest > _EPS:
                    shorts.setdefault(key, deque()).append(
                        [rest, None, str(r.get("date") or ""),
                         ordinal[id(r)]])
            continue
        if r.get("action") == "SPLIT":
            if id(r) not in splits:
                continue
            ratio = q if q > 0 else 1.0
            new = str(r.get("symbol_new") or "").strip() or sym
            nk = (acct, new)
            if key in longs:
                # Popped first: a plain split (nk == key) read the old
                # balance again and added it twice (2026-10 audit #31).
                moved = longs.pop(key) * ratio
                longs[nk] = longs.get(nk, 0.0) + moved
            lots = shorts.pop(key, None)
            if lots:
                for lot in lots:
                    lot[0] *= ratio
                    if lot[1] is not None:
                        lot[1] /= ratio
                # Renamed into a series with open writes of its own:
                # the queue stays first in, first out by the write's
                # stamp (2026-10 audit #36; by date alone, two writes
                # of one day lost their order: audit A28).
                dest = shorts.setdefault(nk, deque())
                dest.extend(lots)
                shorts[nk] = deque(sorted(dest, key=lambda lot: lot[3]))
            continue
        # A purchase's money is a magnitude: the quantity's sign says
        # which way it went (a buy-back entered with a negative amount
        # counted as money received: 2026-10 audit R03). A sale's is
        # signed, as the engine books it: a write whose commission
        # exceeds the premium nets a loss (2026-10 audit #34).
        net = _f(r.get("net_amount"))
        if q > 0:
            net = abs(net)
        if abs(q) < _EPS:
            continue
        c = abs(q)
        if q < 0:
            held = longs.get(key, 0.0)
            close = min(held, c)
            longs[key] = held - close
            if close > _EPS and r.get("action") == "ASSIGN":
                exercises.append({"date": date_of(r), "symbol": sym,
                                  "in_books": in_books is None
                                  or bool(in_books(r))})
            rest = c - close
            if rest > _EPS:
                # A write: the premium received, per contract.
                shorts.setdefault(key, deque()).append(
                    [rest, net / c, str(r.get("date") or ""),
                     ordinal[id(r)]])
            continue
        rem = c
        lots = shorts.get(key)
        assigned = r.get("action") == "ASSIGN"
        expired = (not assigned and abs(net) < 0.005
                   and abs(_f(r.get("price"))) < _EPS)
        # The stock line the engine pairs this assignment with; None is
        # a cash-settled contract (an index option), whose premium
        # never reaches a share row (audit A44).
        delivered_to = resolve_underlying(_as_tx(r)) if assigned else None
        trade = None
        unknown_said = False
        while rem > _EPS and lots:
            lot = lots[0]
            m = min(lot[0], rem)
            if lot[1] is None:
                # A write from before the data: its premium is unknown,
                # so its close is not a trade (counted, never silent).
                if not unknown_said:
                    unknown.append({"date": date_of(r), "symbol": sym,
                                    "in_books": in_books is None
                                    or bool(in_books(r))})
                    unknown_said = True
                lot[0] -= m
                rem -= m
                if lot[0] <= _EPS:
                    lots.popleft()
                continue
            premium = lot[1] * m
            paid = net * (m / c)
            if trade is None:
                # One trade per closing row, even when it closes
                # contracts of several writes (the engine's one record).
                trade = {
                    "date": date_of(r), "symbol": sym, "account": acct,
                    "pnl": 0.0, "contracts": 0.0, "premium": 0.0,
                    "kind": ("assigned" if assigned
                             else "expired" if expired else "bought back"),
                    "written": lot[2],
                    "cash_settled": bool(assigned and not delivered_to),
                    "in_books": in_books is None or bool(in_books(r))}
                trades.append(trade)
            trade["pnl"] += premium - paid
            trade["contracts"] += m
            trade["premium"] += premium
            if assigned and delivered_to:
                mult = _f(r.get("multiplier")) or 100.0
                credits.append({
                    "account": acct,
                    "underlying": delivered_to,
                    "right": parse_option_right(sym) or "",
                    "shares": m * mult, "amount": premium,
                    "date": str(r.get("date") or ""),
                    "strike": _f(parse_option_strike(sym)),
                    "delivered": c * mult})
            lot[0] -= m
            rem -= m
            if lot[0] <= _EPS:
                lots.popleft()
        if rem > _EPS:
            longs[key] = longs.get(key, 0.0) + rem
    return {"trades": trades, "credits": credits,
            "exercised_long": len(exercises), "exercises": exercises,
            "unknown": unknown}


def _day_gap(a: str, b: str) -> Optional[int]:
    """Days from `a` to `b` (negative when `b` is earlier); None when
    either is unparsable."""
    try:
        return (_date.fromisoformat(b[:10])
                - _date.fromisoformat(a[:10])).days
    except ValueError:
        return None


def _assignment_legs(cr: Dict[str, Any], events: List[Dict[str, Any]],
                     state_at: Callable[[Dict[str, Any], int],
                                        Tuple[Tuple[str, str], float]],
                     ) -> List[int]:
    """The indices in `events` of the share legs an assigned option's
    premium went into: a call's sales, a put's purchases, of the same
    account and underlying (as split or renamed by then: `state_at(cr,
    i)` is the credit's key and split factor at event i), told apart
    from an unrelated trade as the engines pair them (CA-OPT-08 /
    US-OPT-05, lib/core _pair_assign_legs). A leg dated before the
    option row (up to 3 days) needs strong identity (a marked ASSIGN
    leg, or the delivered shares at the strike); one on or after it (up
    to 7 days) a single sign (marked, at the strike, or the delivered
    shares). Best first: at the strike, marked, sized, nearest; extra
    legs only at the strike or marked (2026-10 audit #35). Nothing
    identified: every trade of that side from the option row to 7 days
    after it, as the engine's ledger drains by proximity; none at all:
    [] — the premium stays with the option, as the engine leaves it
    undrained (2026-10-24 audit A44)."""
    want_sale = cr["right"] == "C"
    strike = _f(cr.get("strike"))
    size = _f(cr.get("delivered")) or _f(cr.get("shares"))
    near: List[int] = []
    cands = []
    for i, s in enumerate(events):
        if s.get("action") not in ("BUYSELL", "ASSIGN"):
            continue
        q = _f(s.get("quantity"))
        if abs(q) <= _EPS or (q < 0) != want_sale:
            continue
        key, factor = state_at(cr, i)
        if (str(s.get("account") or ""),
                str(s.get("symbol") or "")) != key:
            continue
        gap = _day_gap(cr["date"], str(s.get("date") or ""))
        if gap is None or not -_LEG_LEAD_DAYS <= gap <= _LEG_LAG_DAYS:
            continue
        if gap >= 0:
            near.append(i)
        marked = s.get("action") == "ASSIGN"
        k = strike / factor if strike > 0 and factor > 0 else 0.0
        at_strike = k > 0 and abs(_f(s.get("price")) - k) <= max(
            0.005, 1e-6 * k)
        sized = abs(abs(q) - size * factor) < 1e-6
        if gap < 0:
            if not (marked or (at_strike and sized)):
                continue
        elif not (marked or at_strike or sized):
            continue
        cands.append(((not at_strike, not marked, not sized, abs(gap),
                       gap < 0, i), i, marked or at_strike))
    if not cands:
        return near
    cands.sort(key=lambda c: c[0])
    return [i for n, (_sc, i, strong) in enumerate(cands)
            if n == 0 or strong]


def credits_by_sale(rows: Iterable[Dict[str, Any]],
                    credits: List[Dict[str, Any]],
                    covers: Optional[Dict[str, float]] = None,
                    ) -> Dict[str, float]:
    """{row id: premium to take out of that row's P/L} — each assigned
    option's premium drawn by the rows of the underlying whose gain the
    engine folded it into (CA-OPT-08 / US-OPT-05), replayed in date
    order with the underlying's splits and renames (a split scales the
    shares still to be drawn, a rename moves them: 2026-10 audit #32,
    #33; a split between the assignment and its leg scales the credit
    before the leg draws it: 2026-10-24 audit A12).

    A call's premium comes out of its own share leg(s) (`_assignment_legs`:
    the sale at the strike, dated up to 3 days before the option row or
    7 after it). A put's goes into the delivered shares, so its credit
    opens at the delivery leg (an earlier sale of other shares keeps its
    P/L: A49): the delivered shares that cover a short carry it in that
    cover's own gain (`covers`: {row id: shares the gains rows book as a
    short cover}; A37), the rest in the account's later sales, per
    delivered share. The last leg coming takes the remainder, as the
    engine's ledger does. With no leg in the engine's window the premium
    stays with the option, where the engine leaves it undrained (A44)."""
    from taxjson.lib.corporate_timeline import dedupe_split_events
    open_ = [dict(cr, left=cr["shares"], amt_left=cr["amount"],
                  key=(cr["account"], cr["underlying"]))
             for cr in credits
             if cr["underlying"] and cr["shares"] > _EPS]
    if not open_:
        return {}
    covers = covers or {}
    accts = {cr["account"] for cr in open_}
    events = [r for r in rows if isinstance(r, dict)
              and str(r.get("account") or "") in accts
              and not is_option_symbol(str(r.get("symbol") or ""))
              and (r.get("action") == "SPLIT"
                   or (r.get("action") in ("BUYSELL", "ASSIGN")
                       and abs(_f(r.get("quantity"))) > _EPS))]
    # One split reported twice is one event, per account here.
    keep_split = {id(r) for r in dedupe_split_events(
        [r for r in events if r.get("action") == "SPLIT"], per_account=True)}
    events = [r for r in events
              if r.get("action") != "SPLIT" or id(r) in keep_split]
    events.sort(key=lambda r: (str(r.get("date") or ""),
                               str(r.get("time") or ""),
                               0 if r.get("action") == "SPLIT" else 1))

    def key_of(e: Dict[str, Any]) -> Tuple[str, str]:
        return (str(e.get("account") or ""), str(e.get("symbol") or ""))

    def split_of(e: Dict[str, Any]) -> Tuple[float, Tuple[str, str]]:
        q = _f(e.get("quantity"))
        k = key_of(e)
        return (q if q > 0 else 1.0,
                (k[0], str(e.get("symbol_new") or "").strip() or k[1]))

    # The splits that reach each credit — dated after its assignment,
    # following the renames — by event index.
    chains: Dict[int, List[Tuple[int, float, Tuple[str, str]]]] = {}
    for cr in open_:
        key = cr["key"]
        chain = []
        for i, e in enumerate(events):
            if (e.get("action") != "SPLIT" or key_of(e) != key
                    or not cr["date"] < str(e.get("date") or "")):
                continue
            ratio, nk = split_of(e)
            chain.append((i, ratio, nk))
            key = nk
        chains[id(cr)] = chain

    def state_at(cr: Dict[str, Any], j: int) -> Tuple[Tuple[str, str], float]:
        key, factor = cr["key"], 1.0
        for i, ratio, nk in chains[id(cr)]:
            if i >= j:
                break
            factor *= ratio
            key = nk
        return key, factor

    for cr in open_:
        legs = _assignment_legs(cr, events, state_at)
        cr["legs"] = set(legs)
        cr["last_leg"] = max(legs) if legs else None
        # A put's credit opens at its delivery.
        cr["open_at"] = min(legs) if legs else None
    out: Dict[str, float] = {}

    def draw(cr: Dict[str, Any], s: Dict[str, Any], room: float) -> float:
        take = min(room, cr["left"])
        if take <= _EPS:
            return 0.0
        sid = str(s.get("id") or "")
        amt = cr["amt_left"] * take / cr["left"]
        if sid:
            out[sid] = out.get(sid, 0.0) + amt
        cr["amt_left"] -= amt
        cr["left"] -= take
        return take

    def at_leg(cr: Dict[str, Any], j: int, q: float) -> None:
        # The last leg takes what is left of the premium (a smaller
        # leg: the engine folds the whole remainder into it).
        if j == cr["last_leg"] and q < cr["left"]:
            cr["left"] = q

    for j, e in enumerate(events):
        k = key_of(e)
        if e.get("action") == "SPLIT":
            ratio, nk = split_of(e)
            d = str(e.get("date") or "")
            for cr in open_:
                if cr["key"] == k and cr["date"] < d:
                    cr["left"] *= ratio
                    cr["shares"] *= ratio
                    cr["key"] = nk
            continue
        q = abs(_f(e.get("quantity")))
        if _f(e.get("quantity")) > 0:
            # A put's delivery: the shares that covered a short carry
            # their part of the premium in the cover's own gain.
            room = min(covers.get(str(e.get("id") or ""), 0.0), q)
            for cr in open_:
                if cr["right"] == "C" or j not in cr["legs"]:
                    continue
                at_leg(cr, j, q)
                if room > _EPS:
                    room -= draw(cr, e, room)
            continue
        room = q
        # A call's own share leg first.
        for cr in open_:
            if room <= _EPS:
                break
            if cr["right"] == "C" and j in cr["legs"]:
                at_leg(cr, j, q)
                room -= draw(cr, e, room)
        # Then the puts whose delivered shares are being sold.
        for cr in open_:
            if room <= _EPS:
                break
            if (cr["right"] == "C" or cr["key"] != k
                    or cr["open_at"] is None or j <= cr["open_at"]):
                continue
            room -= draw(cr, e, room)
    return out


def _new_stats() -> Dict[str, Any]:
    return {"trades": 0, "wins": 0, "losses": 0, "net_pl": 0.0,
            "gross_wins": 0.0, "gross_losses": 0.0,
            "largest_win": None, "largest_loss": None}


def _add(st: Dict[str, Any], pnl: float) -> None:
    pnl = round(pnl, 2)
    st["trades"] += 1
    st["net_pl"] += pnl
    if pnl > 0:
        st["wins"] += 1
        st["gross_wins"] += pnl
        if st["largest_win"] is None or pnl > st["largest_win"]:
            st["largest_win"] = pnl
    elif pnl < 0:
        st["losses"] += 1
        st["gross_losses"] += pnl
        if st["largest_loss"] is None or pnl < st["largest_loss"]:
            st["largest_loss"] = pnl


def _finish(st: Dict[str, Any]) -> Dict[str, Any]:
    n, w, lo = st["trades"], st["wins"], st["losses"]
    gw, gl = round(st["gross_wins"], 2), round(st["gross_losses"], 2)
    return {
        "trades": n, "wins": w, "losses": lo,
        "breakeven": n - w - lo,
        "win_rate": round(w / n, 4) if n else None,
        "net_pl": round(st["net_pl"], 2),
        "gross_wins": gw, "gross_losses": gl,
        "avg_win": round(gw / w, 2) if w else None,
        "avg_loss": round(gl / lo, 2) if lo else None,
        "largest_win": st["largest_win"],
        "largest_loss": st["largest_loss"],
        # gross wins / gross losses; None with no losing trade.
        "profit_factor": round(gw / -gl, 4) if gl < 0 else None,
    }


def economic_pnl(row: Dict[str, Any]) -> float:
    """A gains row's P/L before any superficial-loss / wash-sale denial
    (CA-RPT-16 / US-RPT-12): its `raw_gain` (`gain` on a row that
    predates the field), plus what its cost carries of an EARLIER denied
    loss — the engine's `deferred_loss_recovered` (the s.53(1)(f) ACB
    addition / §1091(d) basis add released by this disposition; a
    short's comes off its proceeds) and a US replacement sold before
    the loss (`wash_basis_added`, US-WASH-22). Without them the denied
    loss counted twice: in the loss sale's raw gain and again in the
    replacement's cost (audit 2026-10-25 planning-us#1,
    brokers-inputs#2)."""
    pnl = _f(row["raw_gain"] if row.get("raw_gain") is not None
             else row.get("gain"))
    pnl += _f(row.get("deferred_loss_recovered"))
    for add in row.get("wash_basis_added") or ():
        if isinstance(add, dict):
            pnl += _f(add.get("amount"))
    return pnl


def closed_trades(gains_docs: Dict[str, Dict[str, Any]],
                  books: Dict[str, List[Dict[str, Any]]],
                  keep: Callable[[str], bool],
                  date_of: Callable[[Dict[str, Any]], str],
                  crypto_accounts: Iterable[str] = (),
                  in_books: Optional[Callable[[Dict[str, Any]], bool]] = None,
                  symbol_ok: Optional[Callable[[str], bool]] = None,
                  ) -> Dict[str, Any]:
    """Every closed trade in the window `keep` (applied to
    `date_of(row)`), one record each: {date, symbol, underlying (an
    option's share listing, else the symbol), account, cls, pnl,
    kind (a written option's close)}; with the denied total, the
    tainted dispositions skipped and the option counts `compute`
    reports. `symbol_ok(symbol)`, when given, keeps only the rows of
    the symbols it accepts: the trades and the denied and unknown-cost
    tallies alike (`win-rates SYMBOL`: backlog TJ-0025).

    gains_docs: {account: gains document} (the engine's rows);
    books: {account: base-currency book rows} for the same accounts
    (the written options and the assignment credits)."""
    crypto = set(crypto_accounts)
    if symbol_ok is None:
        def symbol_ok(_sym: str) -> bool:
            return True
    out: List[Dict[str, Any]] = []
    denied = 0.0
    denied_n = 0
    tainted = 0
    assigned = 0
    exercised = 0

    # The share rows the gains engine booked as a short's cover (a put's
    # delivered shares covering a short keep the premium in that cover's
    # gain: 2026-10-24 audit A37), per account and row id.
    covers: Dict[str, Dict[str, float]] = {}
    for acct, doc in gains_docs.items():
        for t in doc.get("transactions") or []:
            if (isinstance(t, dict) and "gain" in t
                    and t.get("direction") == "SHORT"
                    and not is_option_symbol(str(t.get("symbol") or ""))
                    and t.get("id")):
                c = covers.setdefault(acct, {})
                c[str(t["id"])] = c.get(str(t["id"]), 0.0) + abs(_f(t.get("qty")))

    # A row id is unique within one account's book only: the credits
    # are keyed by account and id (audit A07).
    credit: Dict[Tuple[str, str], float] = {}
    written: List[Dict[str, Any]] = []
    for acct, rows in books.items():
        wo = written_option_trades(rows, date_of, in_books)
        written += wo["trades"]
        # Counted in the window, as the trades are (2026-10 audit #67).
        exercised += sum(1 for e in wo.get("exercises") or []
                         if e["in_books"] and keep(e["date"])
                         and symbol_ok(str(e.get("symbol") or "")))
        tainted += sum(1 for u in wo.get("unknown") or []
                       if u["in_books"] and keep(u["date"])
                       and symbol_ok(str(u.get("symbol") or "")))
        for sid, amt in credits_by_sale(rows, wo["credits"],
                                        covers.get(acct)).items():
            credit[(acct, sid)] = credit.get((acct, sid), 0.0) + amt

    # A sale split into several gains rows (lots) shares its credit by
    # quantity.
    qty_by_id: Dict[Tuple[str, str], float] = {}
    for acct, doc in gains_docs.items():
        for t in doc.get("transactions") or []:
            if not isinstance(t, dict):
                continue
            ck = (acct, str(t.get("id") or ""))
            if ck in credit:
                qty_by_id[ck] = qty_by_id.get(ck, 0.0) + abs(_f(t.get("qty")))

    for acct, doc in gains_docs.items():
        for t in doc.get("manual_reporting_required") or []:
            if (isinstance(t, dict) and keep(date_of(t))
                    and symbol_ok(str(t.get("symbol") or ""))):
                tainted += 1
        for t in doc.get("transactions") or []:
            if not isinstance(t, dict) or "gain" not in t:
                continue
            if t.get("action") in _INCOME or t.get("deemed"):
                continue
            if not keep(date_of(t)):
                continue
            if not symbol_ok(str(t.get("symbol") or "")):
                continue
            denied += _f(t.get("disallowed_amount"))
            if _f(t.get("disallowed_amount")) > 0.001:
                denied_n += 1
            row_acct = str(t.get("account") or acct)
            cls = classify(t, row_acct in crypto or acct in crypto)
            if cls is None:
                continue
            if t.get("tainted"):
                tainted += 1
                continue
            pnl = economic_pnl(t)
            ck = (acct, str(t.get("id") or ""))
            if ck in credit and cls in ("long-shares", "short-shares"):
                share = (abs(_f(t.get("qty"))) / qty_by_id[ck]
                         if qty_by_id.get(ck) else 1.0)
                pnl -= credit[ck] * share
            sym = str(t.get("symbol") or "")
            out.append({"date": date_of(t), "symbol": sym,
                        "underlying": _underlying(sym), "account": row_acct,
                        "cls": cls, "pnl": pnl, "kind": ""})

    for tr in written:
        if not tr["in_books"] or not keep(tr["date"]):
            continue
        if not symbol_ok(str(tr["symbol"] or "")):
            continue
        if tr["kind"] == "assigned":
            assigned += 1
        out.append({"date": tr["date"], "symbol": tr["symbol"],
                    "underlying": _underlying(tr["symbol"]),
                    "account": tr["account"], "cls": "written-options",
                    "pnl": tr["pnl"], "kind": tr["kind"]})
    out.sort(key=lambda r: (r["date"], r["symbol"], r["account"]))
    return {
        "trades": out,
        "denied_total": round(denied, 2),
        "denied_count": denied_n,
        "tainted_skipped": tainted,
        "assigned_written_options": assigned,
        "exercised_long_options": exercised,
    }


def _underlying(sym: str) -> str:
    """An option's share listing (what `win-rates` groups it under),
    else the symbol itself."""
    if is_option_symbol(sym):
        try:
            return parse_option_underlying(sym) or sym
        except Exception:                                  # noqa: BLE001
            return sym
    return sym


def compute(gains_docs: Dict[str, Dict[str, Any]],
            books: Dict[str, List[Dict[str, Any]]],
            keep: Callable[[str], bool],
            date_of: Callable[[Dict[str, Any]], str],
            crypto_accounts: Iterable[str] = (),
            in_books: Optional[Callable[[Dict[str, Any]], bool]] = None,
            ) -> Dict[str, Any]:
    """Per-class statistics over the window `keep` (applied to
    `date_of(row)`): the closed trades of `closed_trades` folded per
    class, with a total."""
    res = closed_trades(gains_docs, books, keep, date_of, crypto_accounts,
                        in_books)
    per: Dict[str, Dict[str, Any]] = {c: _new_stats() for c, _ in CLASSES}
    total = _new_stats()
    for tr in res["trades"]:
        _add(per[tr["cls"]], tr["pnl"])
        _add(total, tr["pnl"])
    return {
        "classes": {c: _finish(per[c]) for c, _ in CLASSES},
        "total": _finish(total),
        "denied_total": res["denied_total"],
        "denied_count": res["denied_count"],
        "tainted_skipped": res["tainted_skipped"],
        "assigned_written_options": res["assigned_written_options"],
        "exercised_long_options": res["exercised_long_options"],
    }


# `win-rates --by`: how the closed trades are grouped.
GROUPINGS: Tuple[Tuple[str, str], ...] = (
    ("symbol", "share listing (an option under its underlying)"),
    ("account", "account"),
    ("month", "month of the close"),
    ("class", "asset class"),
)


def _group_key(tr: Dict[str, Any], by: str) -> str:
    if by == "symbol":
        return tr["underlying"]
    if by == "account":
        return tr["account"]
    if by == "month":
        return str(tr["date"])[:7]
    return tr["cls"]


def group(trades: Iterable[Dict[str, Any]], by: str = "symbol"
          ) -> Dict[str, Any]:
    """The closed trades folded per `by` (a GROUPINGS name): {'by',
    'rows': [{key, classes, ...stats}] (most trades first, then the
    net P/L, then the key), 'total'}."""
    if by not in dict(GROUPINGS):
        raise ValueError(f"unknown grouping {by!r}")
    per: Dict[str, Dict[str, Any]] = {}
    classes: Dict[str, Dict[str, int]] = {}
    total = _new_stats()
    for tr in trades:
        k = _group_key(tr, by)
        _add(per.setdefault(k, _new_stats()), tr["pnl"])
        c = classes.setdefault(k, {})
        c[tr["cls"]] = c.get(tr["cls"], 0) + 1
        _add(total, tr["pnl"])
    rows = []
    for k, st in per.items():
        row = _finish(st)
        row["key"] = k
        row["classes"] = dict(sorted(classes[k].items()))
        rows.append(row)
    if by == "month":
        rows.sort(key=lambda r: r["key"])
    else:
        rows.sort(key=lambda r: (-r["trades"], -r["net_pl"], r["key"]))
    return {"by": by, "rows": rows, "total": _finish(total)}
