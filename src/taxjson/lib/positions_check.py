"""What `tjs sanity` checks beyond the quantity table: the books'
COST against a positions report's, the books' positions on a past date,
and income paid on a share count the books do not hold.

Costs (`compare_costs`): a broker's book value is not the books' ACB and
is not meant to be. The difference is reported with a REASON, never as
a failure (sanity's exit code stays the quantity check's):

  Canada (the books' ACB is the s.47 average over all your taxable
  accounts, CA-ACB-01, with denied superficial losses added, s.53(1)(f)):
    superficial-loss  the books' ACB carries denied superficial losses;
                      a broker's book value does not
    pooled            the same shares in another of your taxable
                      accounts: the books average them (s.47), the
                      broker shows this account's own cost
    return-of-capital the books lowered the ACB for a return of capital
                      the broker may not have applied (or the reverse)
    spin-off          a spin-off's election moved part of the parent's
                      ACB to the new shares (s.86.1); the broker may
                      split it differently, or not at all
    broker-fx         the broker's book value in Canadian dollars for a
                      foreign listing, converted at its own rates; the
                      books convert each purchase at the Bank of Canada
                      rate of its day
    lot-basis         the broker's cost is its LOT basis (sales matched
                      to lots); Canadian ACB is the average cost
  United States (the books' basis is FIFO lots per account with
  §1091(d) additions):
    wash-sale         wash-sale basis additions — a broker shows them
                      only for washes inside the same account
    lot-method        the broker may match sales to lots by another
                      method than FIFO (specific identification)
    return-of-capital as above
    spin-off          as above (§355)
  Both: unexplained — none of those closes the gap: a missing or
  mis-costed purchase, an opening line's cost, or the broker's error.

A cost in the project's base currency is compared with the filing books
(work/<acct>_gains_wash.json, else _gains.json); a cost in the
position's own foreign currency with the account's native-currency
books (work/<acct>_raw_gains.json: no superficial-loss additions, no
pooling — said in the row).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Rows that move a position (quantity); SPLIT scales or renames it.
_QTY_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER', 'OPENING_BALANCE')
# Rows that change a position or its cost after a date.
_COST_ACTIONS = _QTY_ACTIONS + ('SPLIT', 'ADJUST', 'DISALLOW')

# The share count a dividend row's description states ("... ON 500
# SHS", "ON 40 SHRS") — the parsers' own pattern (brokerages.base).
from taxjson.lib.brokerages.base import _DIV_QTY_ON_SHS_RE  # noqa: E402


class BaseBookError(ValueError):
    """An account's merged books (work/<acct>_base.json) exist but
    cannot be read: the message names the file and why."""


def load_base_rows(cache: Path, account: str, *,
                   strict: bool = False) -> List[Dict[str, Any]]:
    """The account's merged books (work/<acct>_base.json) rows, [] when
    missing or unreadable. `strict`: a file that is there but cannot be
    read (not JSON, not a books document) raises BaseBookError instead
    — read as an empty history, it made `sanity` compare a dated
    snapshot with the latest books and certify it (audit 2026-10-24
    B18); a missing file is still no history. A books document is the
    object `tjs run` writes, with a `transactions` list: `{}` or a
    bare list is no books either (backlog TJ-0010)."""
    p = Path(cache) / f"{account}_base.json"
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:
        if strict:
            raise BaseBookError(f"work/{p.name}: cannot be read "
                                f"({e.strerror or e})") from None
        return []
    try:
        doc = json.loads(text)
    except ValueError as e:
        if strict:
            raise BaseBookError(f"work/{p.name}: not JSON ({e})") from None
        return []
    if strict and not (isinstance(doc, dict)
                       and isinstance(doc.get("transactions"), list)):
        raise BaseBookError(f"work/{p.name}: not a books document "
                            f"(no transactions list)")
    rows = doc.get("transactions") if isinstance(doc, dict) else doc
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        if strict:
            raise BaseBookError(f"work/{p.name}: not a books document "
                                f"(no transactions list)")
        return []
    return [r for r in rows if isinstance(r, dict)]


def pool_splits(pool_rows: Iterable[Dict[str, Any]],
                account: str) -> List[Dict[str, Any]]:
    """The splits (and renames) of a blended pass's merged books
    (`pool_rows`) that only other accounts' books carry, as copies
    booked in `account`. The engines apply a split to the whole
    security, every account's shares (Canada's s.47 pool, the US lots
    alike), but each account's books hold only the splits its own
    exports report: a walk of one account's books left its shares
    unsplit, so its share of a pool was apportioned on the old count and
    `sanity` compared the old count (backlog TJ-0696). An event the
    account reports itself (the same symbol and new symbol within
    SPLIT_DATE_WINDOW_DAYS: its own ratio, a merger's per-account one)
    is not added, and an event several other accounts report is added
    once, its earliest copy — as the T1135 custody walk reads them
    (taxjson_t1135 Custody.book)."""
    from datetime import date as _d
    from taxjson.lib.corporate_timeline import (SPLIT_DATE_WINDOW_DAYS,
                                                normalize_symbol_new)

    def _event(t: Dict[str, Any]) -> Tuple[str, str]:
        sym = str(t.get("symbol") or "")
        return sym, normalize_symbol_new(sym, t.get("symbol_new"))

    def _day(t: Dict[str, Any]) -> Optional[Any]:
        try:
            return _d.fromisoformat(str(t.get("date") or "")[:10])
        except ValueError:
            return None
    splits = [r for r in pool_rows if isinstance(r, dict)
              and str(r.get("action") or "").upper() == "SPLIT"]
    seen: Dict[Tuple[str, str], List[Any]] = {}
    for r in splits:
        if str(r.get("account") or "") == account:
            seen.setdefault(_event(r), []).append(_day(r))
    out = []
    for r in sorted((r for r in splits
                     if str(r.get("account") or "") != account),
                    key=lambda r: str(r.get("date") or "")):
        d = _day(r)
        if any(o is None or d is None
               or abs((o - d).days) <= SPLIT_DATE_WINDOW_DAYS
               for o in seen.get(_event(r), ())):
            continue            # the account's own copy, or one added
        seen.setdefault(_event(r), []).append(d)
        out.append(dict(r, account=account,
                        pool_split_of=str(r.get("account") or "")))
    return out


def without_pool_cut(rows: List[Dict[str, Any]],
                     pool_rows: Optional[List[Dict[str, Any]]],
                     account: str) -> List[Dict[str, Any]]:
    """`account`'s `rows` less those the pass across accounts cut at its
    snapshot (taxjson-merge --opening-cutoff, a rename only another
    account reports): they are not in `pool_rows`, the merged books. A
    view that walks such a row with pool_splits would carry the old
    purchase through that rename beside the snapshot's shares (backlog
    TJ-1168). `rows` as they are without pool rows of the account."""
    kept = {r.get("id") for r in pool_rows or []
            if isinstance(r, dict) and r.get("account") == account}
    if not kept:
        return rows
    return [r for r in rows if r.get("id") in kept or not r.get("id")
            or r.get("account") != account]


# The merged books of the blended passes (taxjson_run
# stage_blended_wash_pass): the taxable accounts', the crypto accounts'.
BLEND_BASES = (".blend_base.json", ".cryptoblend_base.json")


def blended_pool_rows(cache: Path, account: str) -> List[Dict[str, Any]]:
    """The rows of the blended pass's merged books `account` is in, when
    its filing books (work/<account>_gains_wash.json) are split from
    that pass; [] otherwise (its own pass: its pools are its own)."""
    try:
        doc = json.loads((Path(cache) / f"{account}_gains_wash.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    if not (isinstance(meta, dict) and meta.get("split_from")):
        return []
    for name in BLEND_BASES:
        try:
            pool = json.loads((Path(cache) / name).read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = pool.get("transactions") if isinstance(pool, dict) else None
        if isinstance(rows, list) and any(
                isinstance(r, dict) and r.get("account") == account
                for r in rows):
            return rows
    return []


def book_rows(cache: Path, account: str, *,
              strict: bool = False) -> List[Dict[str, Any]]:
    """The rows the account's books hold positions from: its merged rows
    (load_base_rows; `strict` as there) plus the missing-history
    openings its gains run booked (the .tt OPENING cost=unknown
    lines: units bought before the data, which no row of the merged
    books carries — lib/missing_history.openings_from_log). Every view
    of the books' positions on a date reads these, so it holds the
    same units the books do — and the splits another account of its
    blended pool reports (pool_splits: the engines split every
    account's shares; backlog TJ-0696)."""
    from taxjson.lib.missing_history import openings_from_log
    _pool = blended_pool_rows(cache, account)
    rows = without_pool_cut(load_base_rows(cache, account, strict=strict),
                            _pool, account)
    rows = rows + pool_splits(_pool, account)
    for name in (f"{account}_gains.json", f"{account}_gains_wash.json"):
        p = Path(cache) / name
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return rows + openings_from_log(doc, account)
    return rows


def load_inventory(path: Path) -> Dict[str, Dict[str, Any]]:
    """{symbol: inventory entry} of a gains file (summed per symbol).
    `unknown_cost`: a row of the symbol has no known cost (an OPENING
    cost=unknown line's units): its total_cost is a placeholder, never
    a cost to compare (backlog TJ-0818)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for h in (doc.get("inventory") or []) if isinstance(doc, dict) else []:
        sym = str(h.get("symbol") or "")
        if not sym:
            continue
        e = out.setdefault(sym, {"qty": 0.0, "total_cost": 0.0,
                                 "deferred_wash": 0.0,
                                 "currency": h.get("currency") or "",
                                 "unknown_cost": False})
        e["unknown_cost"] = e["unknown_cost"] or bool(h.get("unknown_cost"))
        e["qty"] += float(h.get("qty") or 0.0)
        e["total_cost"] += float(h.get("total_cost") or 0.0)
        e["deferred_wash"] += float(h.get("deferred_wash") or 0.0)
    return out


def last_day(rows: Iterable[Dict[str, Any]]) -> str:
    return max((str(r.get("date") or "") for r in rows
                if r.get("action") in _COST_ACTIONS), default="")


def _ordered(rows, *, clock: bool = False):
    """The rows in the books' order, by trade date. At one date an
    opening balance first, then a split — effective at the market's
    open, whatever clock time its row carries — then the day's trades
    by clock time, as the Canada engine and the record-date balance walk
    take them (corporate_timeline 'ca_balance', apply_distributions
    .balance_on): a noon-stamped split no longer scaled a 09:30
    purchase of the same day (backlog TJ-0812). `clock`: the US
    engine's order instead (corporate_timeline 'us_main': clock time
    first, then the rung), which takes a split stamped after a trade
    after it. Rows tied on the key keep their order (stable sort)."""
    def _rung(r):
        a = r.get("action")
        return 0 if a == "OPENING_BALANCE" else 1 if a == "SPLIT" else 2
    if clock:
        return sorted(rows, key=lambda r: (str(r.get("date") or ""),
                                           str(r.get("time") or ""),
                                           _rung(r)))
    return sorted(rows, key=lambda r: (str(r.get("date") or ""), _rung(r),
                                       str(r.get("time") or "")))


def positions_on(rows: Iterable[Dict[str, Any]], day: str, *,
                 settled: bool = False,
                 before: bool = False,
                 clock: bool = False) -> Dict[str, float]:
    """{symbol: quantity} after every row traded on or before `day`
    (`before`: strictly before it; `settled`: by settlement date — the
    holder of record). Splits scale the symbol's shares; a rename moves
    them to the new symbol. One split reported twice (two accounts'
    or two sources' copies, nearby dates) applies once: the position
    is keyed by symbol, so the event is too (split_copies,
    CA-CORP-12). Rows are taken in the books' order (_ordered; `clock`:
    the US engine's)."""
    from taxjson.lib.corporate_timeline import split_copies
    rows = list(rows)
    dup = split_copies(rows)
    pos: Dict[str, float] = {}
    for r in _ordered(rows, clock=clock):
        d = str((r.get("date_settle") if settled else None)
                or r.get("date") or "")
        if (d >= day) if before else (d > day):
            continue
        a = r.get("action")
        sym = str(r.get("symbol") or "")
        if a in _QTY_ACTIONS:
            pos[sym] = pos.get(sym, 0.0) + float(r.get("quantity") or 0.0)
        elif a == "SPLIT" and id(r) not in dup:
            new = str(r.get("symbol_new") or "").strip() or sym
            q = pos.pop(sym, 0.0) * float(r.get("quantity") or 0.0)
            pos[new] = pos.get(new, 0.0) + q
    return {s: q for s, q in pos.items() if abs(q) > 1e-9}


def touched_after(rows: Iterable[Dict[str, Any]], day: str) -> Set[str]:
    """Symbols with a position or cost row dated after `day`."""
    out: Set[str] = set()
    for r in rows:
        if r.get("action") in _COST_ACTIONS \
                and str(r.get("date") or "") > day:
            out.add(str(r.get("symbol") or ""))
            if r.get("symbol_new"):
                out.add(str(r["symbol_new"]))
    return out


def roc_symbols(rows: Iterable[Dict[str, Any]]) -> Set[str]:
    """Symbols the books lowered the cost of for a return of capital
    (an ADJUST that is one, income_dating.is_return_of_capital: type
    roc, or a negative amount). A spin-off's parent cost reduction is
    not one (spinoff_symbols; 2026-10-23 audit #11)."""
    from taxjson.lib.income_dating import is_return_of_capital
    out: Set[str] = set()
    for r in rows:
        if not is_return_of_capital(r):
            continue
        if (str(r.get("type") or "").lower() == "roc"
                or float(r.get("net_amount") or 0.0) < 0):
            out.add(str(r.get("symbol") or ""))
    return out


def spinoff_symbols(rows: Iterable[Dict[str, Any]]) -> Set[str]:
    """Symbols whose cost a spin-off's election moved to the new shares
    (the parent's cost-reduction ADJUST a corporate action books: it
    carries corp_event_id, or the US type spinoff_355) — and the new
    shares themselves: the purchase the same event books (its
    corp_event_id, or spinoff_355), whose cost is the allocation, not a
    purchase the broker may have costed otherwise (backlog TJ-0444:
    "unexplained")."""
    rows = list(rows)
    out: Set[str] = set()
    events: Set[Tuple[str, str]] = set()
    for r in rows:
        if r.get("action") != "ADJUST":
            continue
        if (r.get("corp_event_id")
                or str(r.get("type") or "").lower() == "spinoff_355"):
            out.add(str(r.get("symbol") or ""))
            if r.get("corp_event_id"):
                events.add((str(r.get("account") or ""),
                            str(r.get("corp_event_id"))))
    for r in rows:
        if r.get("action") != "BUYSELL":
            continue
        try:
            if float(r.get("quantity") or 0.0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        if ((str(r.get("account") or ""), str(r.get("corp_event_id")))
                in events
                or str(r.get("type") or "").lower() == "spinoff_355"):
            out.add(str(r.get("symbol") or ""))
    return out


def fractions_awaiting_cash(rows: Iterable[Dict[str, Any]], day: str
                            ) -> Dict[str, List[Dict[str, Any]]]:
    """{symbol: [{"quantity", "received", "paid"}]}: the fractions of
    s.86.1 spin-offs the books hold at the end of `day` and the broker
    does not — received with the whole shares on the spin-off date and
    sold for their cash in lieu on a later day (lib/corp_actions
    ._allocated_spinoff_selling_fraction, CA-CORP-05). A broker's
    snapshot between the two shows the whole shares only, which is no
    difference to report (backlog TJ-0149). A share-for-share merger's
    fraction likewise: the exchange (a SPLIT row) gives it, and its sale
    for the cash in lieu is dated when the cash is paid (backlog
    TJ-0866); its entries carry "event": "merger"."""
    rows = list(rows)
    got: Dict[str, str] = {}
    merged: Dict[str, str] = {}
    for r in rows:
        eid = str(r.get("corp_event_id") or "")
        if not eid or float(r.get("quantity") or 0.0) <= 0:
            continue
        d = str(r.get("date") or "")[:10]
        if r.get("action") == "BUYSELL":
            got[eid] = min(got.get(eid, d), d)
        elif r.get("action") == "SPLIT":
            merged[eid] = min(merged.get(eid, d), d)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        q = float(r.get("quantity") or 0.0)
        eid = str(r.get("corp_event_id") or "")
        if r.get("action") != "BUYSELL" or not -1.0 < q < 0.0 or not eid:
            continue
        paid = str(r.get("date") or "")[:10]
        if r.get("corp_election") == "rollover_s_86_1":
            rec, extra = got.get(eid, ""), {}
        elif eid in merged and r.get("corp_cash"):
            rec, extra = merged[eid], {"event": "merger"}
        else:
            continue
        if rec and rec <= day < paid:
            out.setdefault(str(r.get("symbol") or ""), []).append(
                {"quantity": -q, "received": rec, "paid": paid, **extra})
    return out


# ------------------------------------------------------------------ income

# The window before a pay date in which the shares a dividend states must
# have been held when the row gives no record or ex-date: the record
# date of a monthly or quarterly payer sits in it (a quarterly payer's
# pay date can trail its record date by several weeks).
INCOME_WINDOW_DAYS = 45


def _record_date_of(r: Dict[str, Any]) -> str:
    """The row's record date: its `record_date` (a parser's, a .tt
    record=), else the one its description prints ("REC 09/26/25")."""
    rec = str(r.get("record_date") or "")
    if rec:
        return rec
    from taxjson.lib.brokerages.base import income_facts_from_description
    try:
        return str(income_facts_from_description(
            str(r.get("description") or "")).get("record_date") or "")
    except Exception:                                   # noqa: BLE001
        return ""


def held_as(rows: Iterable[Dict[str, Any]], sym: str, day: str, *,
            settled: bool = False, before: bool = False,
            clock: bool = False) -> float:
    """The units the books held on `day` of the security `sym` names
    now: its own position plus the units a later ticker change (a
    rename SPLIT row into `sym` — a broker's corporate action, a dated
    .tt RENAME, a ticker.map rename — dated after `day`) carried in from
    the old symbol, at the change's ratio. A dividend paid under the new
    ticker on a record date before the change was earned on the old
    one's shares.

    The renames are followed by date, in the books' order: each other
    symbol's position on `day` moves with the first rename of that
    symbol after `day`, then with the first rename of where it landed,
    and so on; it counts (at the ratios so far) when that path reaches
    `sym`. A rename of a ticker that had already been renamed away
    (a reused ticker: OLD -> NEW, then a new OLD -> OTHER) carries only
    the shares bought under the reused ticker, never the ones that left
    with the earlier rename (audit finding 61). `clock`: the US
    engine's order of a day's rows (_ordered; backlog TJ-1094)."""
    rows = list(rows)
    pos = positions_on(rows, day, settled=settled, before=before,
                       clock=clock)
    # Each held symbol's shares on `day`: where they are now, and the
    # ratio applied on the way.
    lots = {s: (s, 1.0) for s in pos if s != sym}
    total = pos.get(sym, 0.0)
    for r in _ordered(rows, clock=clock):
        if not lots:
            break
        if r.get("action") != "SPLIT":
            continue
        new = str(r.get("symbol_new") or "").strip()
        old = str(r.get("symbol") or "")
        if not new or new == old:
            continue
        d = str((r.get("date_settle") if settled else None)
                or r.get("date") or "")
        later = (d >= day) if before else (d > day)
        if not later:
            continue
        try:
            ratio = float(r.get("quantity") or 0.0) or 1.0
        except (TypeError, ValueError):
            ratio = 1.0
        for s, (at, f) in list(lots.items()):
            if at != old:
                continue
            if new == sym:
                total += pos[s] * f * ratio
                del lots[s]
            else:
                lots[s] = (new, f * ratio)
    return total


def last_entitled_trade_day(record: str, currency: str) -> Optional[str]:
    """The last trading day whose trade settles by `record` on the
    market's own calendar (the currency's: USD the US, CAD Canada) —
    the eve of the ex-date. The settlement cycle is the one of the
    trade's day, by the canonical calendar (lib/dates
    .last_trade_date_settling_by: T+3 before September 5 2017 in Canada
    and the US, T+2 after, T+1 from May 2024): a hardcoded T+2 before
    the T+1 move entitled a T+3-era purchase a day late (backlog
    TJ-0815, TJ-0826). None for a bad date."""
    from datetime import datetime
    from taxjson.lib.dates import last_trade_date_settling_by
    try:
        datetime.strptime(str(record or ""), "%Y-%m-%d")
    except ValueError:
        return None
    try:
        return last_trade_date_settling_by(record, currency or "USD")
    except ValueError:
        return None


def _max_held(rows, sym: str, pay: str, days: int, *,
              clock: bool = False) -> Optional[float]:
    """The most shares of `sym` the rows held at the end of any day of
    the `days` before `pay` (pay day included); None for a bad date."""
    from datetime import datetime, timedelta
    try:
        d0 = datetime.strptime(pay, "%Y-%m-%d")
    except ValueError:
        return None
    best = 0.0
    for k in range(days + 1):
        day = (d0 - timedelta(days=k)).strftime("%Y-%m-%d")
        best = max(best, held_as(rows, sym, day, clock=clock))
    return best


def income_share_mismatches(rows: List[Dict[str, Any]], account: str,
                            tol: float = 1e-4, *,
                            clock: bool = False) -> List[Dict[str, Any]]:
    """Dividend rows whose description STATES the share count ("ON 500
    SHS") for more shares than the books held when the payment was
    earned. Entitlement is fixed at the record date, not the pay date:
    a sale after it (the dividend paid days or weeks later, the position
    0 by then) is no finding. With the record date (the row's, a .tt
    record=, or the "REC mm/dd/yy" its description prints) the books'
    SETTLED position that day is compared (T+3, T+2 or T+1 settlement: the
    holder of record), or the shares traded by the eve of the ex-date
    on the market's calendar (last_entitled_trade_day), whichever is
    more; with only the ex-date, the position at the end of
    the day before it; with neither, the most the books held at the end
    of any day of the INCOME_WINDOW_DAYS before the pay date. Reported:
    a payment on more shares than that (on a symbol never held: 0) — a
    payment on fewer shares (another broker account's part, a partial
    entitlement) is not. Rows of one broker account are compared with
    that account's rows (and the hand-written ones): one taxjson account
    may hold two brokers' accounts (a count the whole account held is
    accepted too)."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("action") not in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            continue
        m = _DIV_QTY_ON_SHS_RE.search(str(r.get("description") or ""))
        if not m:
            continue
        try:
            stated = float(r.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        if stated <= 0:
            continue
        sym = str(r.get("symbol") or "")
        src = str(r.get("source_account") or "")
        mine = [t for t in rows
                if not src or not t.get("source_account")
                or t.get("source_account") == src]
        rec = _record_date_of(r)
        # (the listing's market decides the calendar: a US share paid
        # in CAD by a Canadian broker settles on US days)
        cur = ("USD" if sym.endswith(".US") else
               "CAD" if sym.rsplit(".", 1)[-1] in ("TO", "V", "CN", "NE")
               and "." in sym else str(r.get("currency") or "").upper())
        exd = str(r.get("ex_date") or "")
        pay = str(r.get("date") or "")

        def _held(book):
            # (through a ticker change after the date: the shares were
            # held under the old symbol then — held_as)
            if rec:
                # The settled position on the record date, or the shares
                # traded by the eve of the ex-date on the market's own
                # calendar (a broker's settle date can follow another
                # market's holiday), whichever is more.
                h = held_as(book, sym, rec, settled=True, clock=clock)
                eve = last_entitled_trade_day(rec, cur)
                if eve:
                    h = max(h, held_as(book, sym, eve, clock=clock))
                return h
            if exd:
                return held_as(book, sym, exd, before=True, clock=clock)
            return _max_held(book, sym, pay, INCOME_WINDOW_DAYS, clock=clock)
        held = _held(mine)
        if held is None:
            continue
        if rec:
            when, basis = rec, "record date"
        elif exd:
            when, basis = exd, "ex-date"
        else:
            when = pay
            basis = f"{INCOME_WINDOW_DAYS} days to the pay date"
        if held + tol >= stated:
            continue
        if mine is not rows and len(mine) != len(rows):
            # The whole account's position (a holding moved between the
            # account's brokers, rows stamped with another broker
            # account): enough there is no finding.
            alt = _held(rows)
            if alt is not None and alt + tol >= stated:
                continue
        out.append({"account": account, "symbol": sym, "date": pay,
                    "on": when, "basis": basis, "stated_shares": stated,
                    "books_shares": held,
                    "amount": float(r.get("net_amount") or 0.0),
                    "currency": r.get("currency") or ""})
    return out


# ------------------------------------------------------------------- costs

def _within(a: float, b: float, tol_abs: float, tol_rel: float) -> bool:
    return abs(a - b) <= max(tol_abs, tol_rel * max(abs(a), abs(b)))


def compare_cost(*, symbol: str, broker_cost: float, cost_currency: str,
                 cost_kind: str, position_currency: str, base: str,
                 country: str, filing: Optional[Dict[str, Any]],
                 own: Optional[float], native: Optional[Dict[str, Any]],
                 pooled: bool, roc: bool, tol_abs: float,
                 tol_rel: float, short: bool = False,
                 spinoff: bool = False) -> Dict[str, Any]:
    """One symbol's cost row. `filing`: the summed filing-books
    inventory entry (base currency) of the group's accounts; `own`: the
    accounts' own pre-pooling cost (base currency); `native`: the
    native-currency books entry. Returns {status: match | differs |
    n/a, books_cost, basis, diff, reasons, explained, note}."""
    usa = country == "usa"
    cur = (cost_currency or "").upper()
    base = (base or "").upper()
    pos_cur = (position_currency or "").upper()
    row: Dict[str, Any] = {"symbol": symbol, "broker_cost": broker_cost,
                           "cost_currency": cur, "cost_kind": cost_kind,
                           "books_cost": None, "basis": "", "diff": None,
                           "reasons": [], "explained": False, "note": ""}
    if cur == base or not cur:
        if filing is None:
            row.update(status="n/a", note="no books cost for it")
            return row
        if filing.get("unknown_cost"):
            # Units whose cost the books do not know (an OPENING
            # cost=unknown line): their 0 is no cost, so a broker's 0
            # is not a match (backlog TJ-0818).
            row.update(status="n/a", note="the books' cost is unknown "
                                          "(an opening line's cost=unknown)")
            return row
        books = float(filing["total_cost"])
        row.update(books_cost=books, basis="filing books "
                   + ("(FIFO lots, wash-adjusted)" if usa else
                      "(s.47 ACB)"))
    else:
        if native is None or (native.get("currency") or "").upper() != cur:
            row.update(status="n/a",
                       note=f"no {cur} books for it (the books are in "
                            f"{base})")
            return row
        if native.get("unknown_cost"):
            row.update(status="n/a", note="the books' cost is unknown "
                                          "(an opening line's cost=unknown)")
            return row
        books = float(native["total_cost"])
        row.update(books_cost=books,
                   basis=f"native {cur} books (this account's own cost: "
                         f"no {'wash-sale additions' if usa else 'superficial-loss additions, no s.47 pooling'})")
    if short and books * broker_cost < 0:
        # A short position's cost is its opening proceeds: the books
        # carry it negative (the inventory convention), some reports
        # positive — compared as magnitudes.
        books, broker_cost = abs(books), abs(broker_cost)
        row.update(books_cost=books, broker_cost=broker_cost)
    diff = books - broker_cost
    row["diff"] = diff
    if _within(books, broker_cost, tol_abs, tol_rel):
        row["status"] = "match"
        return row
    row["status"] = "differs"
    reasons: List[str] = []
    explained = False
    in_filing = row["basis"].startswith("filing")
    deferred = float((filing or {}).get("deferred_wash") or 0.0)
    if in_filing and abs(deferred) > 0.005:
        reasons.append("wash-sale" if usa else "superficial-loss")
        if _within(books - deferred, broker_cost, tol_abs, tol_rel):
            explained = True
    if in_filing and not usa and pooled:
        reasons.append("pooled")
        if own is not None and _within(own, broker_cost, tol_abs,
                                       tol_rel):
            explained = True
    if roc:
        reasons.append("return-of-capital")
    if spinoff:
        reasons.append("spin-off")
    if (in_filing and not usa and cur == base and pos_cur
            and pos_cur != base):
        reasons.append("broker-fx")
    if cost_kind == "lot basis" and not usa:
        reasons.append("lot-basis")
    if usa and cost_kind == "lot basis":
        reasons.append("lot-method")
    if not reasons:
        reasons.append("unexplained")
    row["reasons"] = reasons
    row["explained"] = explained
    return row


REASON_TEXT = {
    "superficial-loss": "the books' ACB carries denied superficial "
                        "losses (s.53(1)(f)); a broker's book value "
                        "does not",
    "pooled": "the books average the same shares in your other taxable "
              "accounts (s.47); the broker shows this account's own "
              "cost",
    "return-of-capital": "the books lowered the cost for a return of "
                         "capital the broker may not have applied",
    "spin-off": "a spin-off's election moved part of the parent's cost "
                "to the new shares; the broker may split it differently, "
                "or not at all",
    "broker-fx": "the broker converted a foreign listing's cost at its "
                 "own rates; the books use each purchase day's Bank of "
                 "Canada rate",
    "lot-basis": "the broker's cost is its lot basis; Canadian ACB is "
                 "the average cost of all identical shares",
    "wash-sale": "the books' basis carries wash-sale additions "
                 "(§1091(d)); a broker shows only washes inside the same "
                 "account",
    "lot-method": "the broker may match sales to lots by another method "
                  "than FIFO",
    "unexplained": "none of the known differences closes it: a missing "
                   "or mis-costed purchase, an opening line's cost, or "
                   "the broker's own error",
}
