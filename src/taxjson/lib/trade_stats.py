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
                              parse_option_underlying)
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
# the option row (tax-logic CA-OPT-08 / US-OPT-05).
_LEG_LEAD_DAYS = 3


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

    Returns {'trades': [ {date, symbol, account, pnl, kind, contracts,
    premium, in_books} ], 'credits': [ {account, underlying, right, shares, amount,
    date} ] (an assigned option's premium the tax rules folded into its
    shares), 'exercised_long': n}."""
    book = [r for r in rows if isinstance(r, dict)
            and r.get("action") in ("BUYSELL", "ASSIGN")
            and is_option_symbol(str(r.get("symbol") or ""))]
    book.sort(key=lambda r: (str(r.get("date") or ""),
                             str(r.get("time") or "")))
    longs: Dict[Tuple[str, str], float] = {}
    shorts: Dict[Tuple[str, str], deque] = {}
    trades: List[Dict[str, Any]] = []
    credits: List[Dict[str, Any]] = []
    exercised_long = 0
    for r in book:
        sym = str(r["symbol"])
        acct = str(r.get("account") or "")
        key = (acct, sym)
        q = _f(r.get("quantity"))
        net = _f(r.get("net_amount"))
        if abs(q) < _EPS:
            continue
        c = abs(q)
        if q < 0:
            held = longs.get(key, 0.0)
            close = min(held, c)
            longs[key] = held - close
            if close > _EPS and r.get("action") == "ASSIGN":
                exercised_long += 1
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
        while rem > _EPS and lots:
            lot = lots[0]
            m = min(lot[0], rem)
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
                    "date": str(r.get("date") or "")})
            lot[0] -= m
            rem -= m
            if lot[0] <= _EPS:
                lots.popleft()
        if rem > _EPS:
            longs[key] = longs.get(key, 0.0) + rem
    return {"trades": trades, "credits": credits,
            "exercised_long": exercised_long}


def credits_by_sale(rows: Iterable[Dict[str, Any]],
                    credits: List[Dict[str, Any]]) -> Dict[str, float]:
    """{sale row id: premium to take out of that sale's P/L} — each
    credit drawn by the account's later SALES of the underlying, per
    delivered share, in date order (a call's from the sale at the strike,
    which may be dated up to 3 days before the option row)."""
    open_: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for cr in credits:
        if not cr["underlying"] or cr["shares"] <= _EPS:
            continue
        start = (_days_before(cr["date"], _LEG_LEAD_DAYS)
                 if cr["right"] == "C" else cr["date"])
        open_.setdefault((cr["account"], cr["underlying"]), []).append(
            dict(cr, left=cr["shares"], start=start))
    if not open_:
        return {}
    sales = [r for r in rows if isinstance(r, dict)
             and r.get("action") in ("BUYSELL", "ASSIGN")
             and _f(r.get("quantity")) < 0
             and (str(r.get("account") or ""), str(r.get("symbol") or ""))
             in open_]
    sales.sort(key=lambda r: (str(r.get("date") or ""),
                              str(r.get("time") or "")))
    out: Dict[str, float] = {}
    for s in sales:
        lst = open_[(str(s.get("account") or ""), str(s["symbol"]))]
        need = abs(_f(s.get("quantity")))
        d = str(s.get("date") or "")
        for cr in lst:
            if need <= _EPS:
                break
            if cr["left"] <= _EPS or cr["start"] > d:
                continue
            take = min(need, cr["left"])
            amt = cr["amount"] * take / cr["shares"]
            sid = str(s.get("id") or "")
            if sid:
                out[sid] = out.get(sid, 0.0) + amt
            cr["left"] -= take
            need -= take
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


def compute(gains_docs: Dict[str, Dict[str, Any]],
            books: Dict[str, List[Dict[str, Any]]],
            keep: Callable[[str], bool],
            date_of: Callable[[Dict[str, Any]], str],
            crypto_accounts: Iterable[str] = (),
            in_books: Optional[Callable[[Dict[str, Any]], bool]] = None,
            ) -> Dict[str, Any]:
    """Per-class statistics over the window `keep` (applied to
    `date_of(row)`).

    gains_docs: {account: gains document} (the engine's rows);
    books: {account: base-currency book rows} for the same accounts
    (the written options and the assignment credits)."""
    crypto = set(crypto_accounts)
    per: Dict[str, Dict[str, Any]] = {c: _new_stats() for c, _ in CLASSES}
    total = _new_stats()
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
        exercised += wo["exercised_long"]
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
            _add(per[cls], pnl)
            _add(total, pnl)

    for tr in written:
        if not tr["in_books"] or not keep(tr["date"]):
            continue
        if tr["kind"] == "assigned":
            assigned += 1
        _add(per["written-options"], tr["pnl"])
        _add(total, tr["pnl"])
    return {
        "classes": {c: _finish(per[c]) for c, _ in CLASSES},
        "total": _finish(total),
        "denied_total": round(denied, 2),
        "denied_count": denied_n,
        "tainted_skipped": tainted,
        "assigned_written_options": assigned,
        "exercised_long_options": exercised,
    }
