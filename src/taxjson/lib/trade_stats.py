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
loss / wash-sale denial: a gains row's `raw_gain` (its `gain` when the row
predates that field). The denied total is reported separately, never
subtracted.

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
sale at the strike, a put's out of the next sales of the delivered shares
(per delivered share, in date order). Exercised LONG options are not
trades: their cost carried into the shares.
"""
from __future__ import annotations

from collections import deque
from datetime import date as _date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from taxjson.lib.core import (is_option_symbol, parse_option_right,
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


def _days_before(iso: str, days: int) -> str:
    try:
        return (_date.fromisoformat(iso[:10])
                - timedelta(days=days)).isoformat()
    except ValueError:
        return iso


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
    premium is unknown, so its close is no trade (`unknown`).

    Returns {'trades': [ {date, symbol, account, pnl, kind, contracts,
    premium, in_books} ], 'credits': [ {account, underlying, right, shares, amount,
    date, strike, delivered} ] (an assigned option's premium the tax rules
    folded into its shares; `delivered` the assignment row's shares),
    'exercised_long': n, 'exercises': [ {date, in_books} ] (the exercises
    of long options, which are not trades), 'unknown': [ {date, in_books} ]
    (the closes of a write with an unknown premium)}."""
    from taxjson.lib.corporate_timeline import dedupe_split_events
    book = [r for r in rows if isinstance(r, dict)
            and r.get("action") in ("BUYSELL", "ASSIGN", "SPLIT",
                                    "OPENING_BALANCE")
            and is_option_symbol(str(r.get("symbol") or ""))]
    # A split first at its stamp, then an opening, as the engines order
    # them.
    book.sort(key=lambda r: (str(r.get("date") or ""),
                             str(r.get("time") or ""),
                             0 if r.get("action") == "SPLIT"
                             else 1 if r.get("action") == "OPENING_BALANCE"
                             else 2))
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
                    [-q, None, str(r.get("date") or "")])
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
                # the queue stays first in, first out by write date
                # (2026-10 audit #36).
                dest = shorts.setdefault(nk, deque())
                dest.extend(lots)
                shorts[nk] = deque(sorted(dest, key=lambda lot: lot[2]))
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
                exercises.append({"date": date_of(r),
                                  "in_books": in_books is None
                                  or bool(in_books(r))})
            rest = c - close
            if rest > _EPS:
                # A write: the premium received, per contract.
                shorts.setdefault(key, deque()).append(
                    [rest, net / c, str(r.get("date") or "")])
            continue
        rem = c
        lots = shorts.get(key)
        assigned = r.get("action") == "ASSIGN"
        expired = (not assigned and abs(net) < 0.005
                   and abs(_f(r.get("price"))) < _EPS)
        trade = None
        unknown_said = False
        while rem > _EPS and lots:
            lot = lots[0]
            m = min(lot[0], rem)
            if lot[1] is None:
                # A write from before the data: its premium is unknown,
                # so its close is not a trade (counted, never silent).
                if not unknown_said:
                    unknown.append({"date": date_of(r),
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
                    "in_books": in_books is None or bool(in_books(r))}
                trades.append(trade)
            trade["pnl"] += premium - paid
            trade["contracts"] += m
            trade["premium"] += premium
            if assigned:
                mult = _f(r.get("multiplier")) or 100.0
                credits.append({
                    "account": acct,
                    "underlying": parse_option_underlying(sym) or "",
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


def _call_legs(cr: Dict[str, Any],
               sales: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sales an assigned call's premium comes out of: its own share
    leg, told apart from an unrelated sale as the engines pair them
    (CA-OPT-08 / US-OPT-05). A sale dated before the option row needs
    strong identity (a marked ASSIGN leg, or the delivered shares at the
    strike); one on or after it a single sign (marked, at the strike, or
    the delivered shares). Best first: at the strike, marked, sized,
    nearest; extra legs only at the strike or marked. [] when nothing
    identifies a leg (2026-10 audit #35)."""
    strike = _f(cr.get("strike"))
    size = _f(cr.get("delivered")) or _f(cr.get("shares"))
    cands = []
    for i, s in enumerate(sales):
        if (str(s.get("account") or ""),
                str(s.get("symbol") or "")) != cr["key"]:
            continue
        gap = _day_gap(cr["date"], str(s.get("date") or ""))
        if gap is None or not -_LEG_LEAD_DAYS <= gap <= _LEG_LAG_DAYS:
            continue
        marked = s.get("action") == "ASSIGN"
        at_strike = strike > 0 and abs(_f(s.get("price")) - strike) <= max(
            0.005, 1e-6 * strike)
        sized = abs(abs(_f(s.get("quantity"))) - size) < 1e-6
        if gap < 0:
            if not (marked or (at_strike and sized)):
                continue
        elif not (marked or at_strike or sized):
            continue
        cands.append(((not at_strike, not marked, not sized, abs(gap),
                       gap < 0, i), s, marked or at_strike))
    cands.sort(key=lambda c: c[0])
    return [s for n, (_sc, s, strong) in enumerate(cands) if n == 0 or strong]


def credits_by_sale(rows: Iterable[Dict[str, Any]],
                    credits: List[Dict[str, Any]]) -> Dict[str, float]:
    """{sale row id: premium to take out of that sale's P/L} — each
    credit drawn by the account's later SALES of the underlying, per
    delivered share, in date order (a call's from its own share leg,
    which may be dated up to 3 days before the option row; with no leg
    identified, from the sales from 3 days before it on).

    The underlying's splits and renames after the assignment carry the
    open credits with them: a split scales the shares still to be sold,
    a rename moves them to the new symbol (2026-10 audit #32, #33)."""
    from taxjson.lib.corporate_timeline import dedupe_split_events
    open_ = [dict(cr, left=cr["shares"],
                  key=(cr["account"], cr["underlying"]))
             for cr in credits
             if cr["underlying"] and cr["shares"] > _EPS]
    if not open_:
        return {}
    accts = {cr["account"] for cr in open_}
    events = [r for r in rows if isinstance(r, dict)
              and str(r.get("account") or "") in accts
              and not is_option_symbol(str(r.get("symbol") or ""))
              and (r.get("action") == "SPLIT"
                   or (r.get("action") in ("BUYSELL", "ASSIGN")
                       and _f(r.get("quantity")) < 0))]
    # One split reported twice is one event, per account here.
    keep_split = {id(r) for r in dedupe_split_events(
        [r for r in events if r.get("action") == "SPLIT"], per_account=True)}
    events = [r for r in events
              if r.get("action") != "SPLIT" or id(r) in keep_split]
    events.sort(key=lambda r: (str(r.get("date") or ""),
                               str(r.get("time") or ""),
                               0 if r.get("action") == "SPLIT" else 1))
    sales = [r for r in events if r.get("action") != "SPLIT"]
    unsold = {id(s): abs(_f(s.get("quantity"))) for s in sales}
    out: Dict[str, float] = {}

    def draw(cr: Dict[str, Any], s: Dict[str, Any]) -> None:
        take = min(unsold[id(s)], cr["left"])
        if take <= _EPS:
            return
        sid = str(s.get("id") or "")
        if sid:
            out[sid] = out.get(sid, 0.0) + cr["amount"] * take / cr["shares"]
        cr["left"] -= take
        unsold[id(s)] -= take

    for cr in open_:
        cr["start"] = cr["date"]
        if cr["right"] != "C":
            continue
        legs = _call_legs(cr, sales)
        for s in legs:
            draw(cr, s)
        if not legs:
            cr["start"] = _days_before(cr["date"], _LEG_LEAD_DAYS)
    for e in events:
        k = (str(e.get("account") or ""), str(e.get("symbol") or ""))
        d = str(e.get("date") or "")
        if e.get("action") == "SPLIT":
            q = _f(e.get("quantity"))
            ratio = q if q > 0 else 1.0
            nk = (k[0], str(e.get("symbol_new") or "").strip() or k[1])
            for cr in open_:
                if cr["key"] == k and cr["date"] < d:
                    cr["left"] *= ratio
                    cr["shares"] *= ratio
                    cr["key"] = nk
            continue
        for cr in open_:
            if unsold[id(e)] <= _EPS:
                break
            if cr["key"] != k or cr["left"] <= _EPS or cr["start"] > d:
                continue
            draw(cr, e)
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


def closed_trades(gains_docs: Dict[str, Dict[str, Any]],
                  books: Dict[str, List[Dict[str, Any]]],
                  keep: Callable[[str], bool],
                  date_of: Callable[[Dict[str, Any]], str],
                  crypto_accounts: Iterable[str] = (),
                  in_books: Optional[Callable[[Dict[str, Any]], bool]] = None,
                  ) -> Dict[str, Any]:
    """Every closed trade in the window `keep` (applied to
    `date_of(row)`), one record each: {date, symbol, underlying (an
    option's share listing, else the symbol), account, cls, pnl,
    kind (a written option's close)}; with the denied total, the
    tainted dispositions skipped and the option counts `compute`
    reports.

    gains_docs: {account: gains document} (the engine's rows);
    books: {account: base-currency book rows} for the same accounts
    (the written options and the assignment credits)."""
    crypto = set(crypto_accounts)
    out: List[Dict[str, Any]] = []
    denied = 0.0
    denied_n = 0
    tainted = 0
    assigned = 0
    exercised = 0

    credit: Dict[str, float] = {}
    written: List[Dict[str, Any]] = []
    for acct, rows in books.items():
        wo = written_option_trades(rows, date_of, in_books)
        written += wo["trades"]
        # Counted in the window, as the trades are (2026-10 audit #67).
        exercised += sum(1 for e in wo.get("exercises") or []
                         if e["in_books"] and keep(e["date"]))
        tainted += sum(1 for u in wo.get("unknown") or []
                       if u["in_books"] and keep(u["date"]))
        for sid, amt in credits_by_sale(rows, wo["credits"]).items():
            credit[sid] = credit.get(sid, 0.0) + amt

    # A sale split into several gains rows (lots) shares its credit by
    # quantity.
    qty_by_id: Dict[str, float] = {}
    for doc in gains_docs.values():
        for t in doc.get("transactions") or []:
            if isinstance(t, dict) and str(t.get("id") or "") in credit:
                qty_by_id[str(t["id"])] = (qty_by_id.get(str(t["id"]), 0.0)
                                           + abs(_f(t.get("qty"))))

    for acct, doc in gains_docs.items():
        for t in doc.get("manual_reporting_required") or []:
            if isinstance(t, dict) and keep(date_of(t)):
                tainted += 1
        for t in doc.get("transactions") or []:
            if not isinstance(t, dict) or "gain" not in t:
                continue
            if t.get("action") in _INCOME or t.get("deemed"):
                continue
            if not keep(date_of(t)):
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
            pnl = _f(t["raw_gain"] if t.get("raw_gain") is not None
                     else t.get("gain"))
            sid = str(t.get("id") or "")
            if sid in credit and cls in ("long-shares", "short-shares"):
                share = (abs(_f(t.get("qty"))) / qty_by_id[sid]
                         if qty_by_id.get(sid) else 1.0)
                pnl -= credit[sid] * share
            sym = str(t.get("symbol") or "")
            out.append({"date": date_of(t), "symbol": sym,
                        "underlying": _underlying(sym), "account": row_acct,
                        "cls": cls, "pnl": pnl, "kind": ""})

    for tr in written:
        if not tr["in_books"] or not keep(tr["date"]):
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
