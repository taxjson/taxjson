"""`tjs edge-cases`: the transactions whose tax treatment turns on a
boundary, with where each one lands and why.

Two kinds of boundary:

1. The tax-year boundary. A trade on Dec 30 that settles on Jan 2 is a
   disposition of the NEW year when the project books on the settlement
   date (CRA practice, `tax_date = "settle"`), and of the old year on the
   trade date. Futures settle on their trade date. Written options, income
   dated around New Year, crypto near midnight UTC and superficial-loss
   windows that span Dec 31 are reported too.

2. The superficial-loss window (ITA s.54: an identical property acquired
   in the 30 days before or after the disposition and still held on day
   30). Acquisitions and rescue sales that fall within a few days of the
   window's edge are listed with their exact day count and the engine's
   verdict. The window is counted on the engine's FIXED window dates —
   settlement dates in Canada (CA-SL-01), trade dates in the US
   (US-WASH-01) — whatever `tax_date` says; `tax_date` only decides the
   year a row lands in (audit A2-0133/0134/0135/1206). The count on the
   other date is shown for reference only: it cannot change the verdict.

Everything is read from the run's work files, so the symbols are the ones
the engine pooled (ticker.map already applied): identical property is the
same symbol, plus a long call on the same shares (s.54 'a right to
acquire'), which the engine counts as replacement property for a share
loss.

A US project (tax-logic US-RPT-05) is explained with ITS law: the §1091
wash-sale window on trade dates with no still-held test (so no "sale
near day 30" items and no held-on-day-30 reasoning), a long call listed
as a warning only (the US engine does not deny on it), Form 8949 rather
than Schedule 3, IRAs rather than registered accounts, and no written-
option year boundary (premiums are taxed at the close, §1234).
"""
from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from taxjson.lib.corporate_timeline import dedupe_split_events

WINDOW = 30
# Rows that change a position (OPENING_BALANCE: a missing-history opening
# or a broker's opening position, A2-0389). SPLIT rows scale it (_walk).
ACQ_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER', 'OPENING_BALANCE')
# The engine's income actions (it has no 'PIL' or 'ROC' action: a
# payment in lieu is DIVIDEND_IN_LIEU, a return of capital an ADJUST
# income_dating.is_return_of_capital names — A2-1207, TJ-0685).
INCOME_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'INTEREST', 'TAX')


def _d(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _work_doc(path: Path) -> Dict[str, Any]:
    """A work/ document through the shared reader (json_input.read_work_doc:
    an object or a bare row list, row lists of objects, typed fields) —
    `transactions: 5` or a row that is a string was a traceback here
    (re-audit A2-0794 / A2-1408). ValueError naming the file."""
    from taxjson.lib.json_input import InputFileError, read_work_doc
    try:
        return read_work_doc(path)
    except InputFileError as e:
        raise ValueError(f"{e} — re-run `tjs run`") from None


def _rows(path: Path) -> List[Dict[str, Any]]:
    # The shared row funnel (A2-0330): a wrong-typed date or quantity
    # is a one-line error naming the file, not a traceback later on.
    return list(_work_doc(path).get("transactions") or [])


def _is_future(sym: str) -> bool:
    """A futures row in any spelling lib/futures reads (F:, /, \\) —
    not only F: (A2-1194)."""
    from taxjson.lib.futures import _FUTURES_PREFIXES
    return (sym or "").upper().startswith(_FUTURES_PREFIXES)


def _is_option(sym: str) -> bool:
    from taxjson.lib.core import is_option_symbol
    return bool(is_option_symbol(sym or ''))


def _underlying(sym: str) -> Optional[str]:
    from taxjson.lib.core import parse_option_underlying
    return parse_option_underlying(sym or '')


def _right(sym: str) -> Optional[str]:
    from taxjson.lib.core import parse_option_right
    return parse_option_right(sym or '')


def _expiry(sym: str) -> Optional[date]:
    import re
    m = re.match(r"^[A-Z0-9.:]+?(\d{6})[CP]\d{8}", sym or '')
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%y%m%d").date()
    except ValueError:
        return None


def _walk(rows: Iterable[Dict[str, Any]], datef, until: Optional[date] = None,
          pre: Optional[Dict[int, float]] = None,
          pre_pooled: Optional[Dict[int, float]] = None,
          pool_of=None) -> Dict[Tuple[str, str], float]:
    """Positions per (account, symbol) after the rows dated on or before
    `until` (by `datef`; every row when None): trades, transfers and
    openings add their quantity, a SPLIT scales the position by its
    ratio and moves it to `symbol_new` (A2-0388). `pre` gets each row's
    position before it, keyed by id(row); `pre_pooled` the position of
    the row's pool (`pool_of(account)`: the accounts the engine pools,
    Book.pool) before it (backlog TJ-0690). A SPLIT scales the whole
    pool once, whichever accounts report it, as the engine does (backlog
    TJ-1205, TJ-0696)."""
    pos: Dict[Tuple[str, str], float] = {}
    pooled: Dict[Tuple[str, str], float] = {}
    pool_of = pool_of or (lambda a: a)

    moving = [r for r in rows
              if r.get("action") in ACQ_ACTIONS + ("SPLIT",)]
    moving.sort(key=lambda r: (str(datef(r) or ""), r.get("time") or ""))
    # One split reported twice is ONE event per account (CA-CORP-12).
    moving = dedupe_split_events(
        moving, per_account=True,
        account_of=lambda r: r.get("_acct") or "")
    pool_events = {id(r) for r in dedupe_split_events(
        moving, per_account=True,
        account_of=lambda r: pool_of(r.get("_acct") or ""))}
    for r in moving:
        d = datef(r)
        if until is not None and (d is None or d > until):
            continue
        k = (r.get("_acct") or "", r.get("symbol") or "")
        if pre is not None:
            pre[id(r)] = pos.get(k, 0.0)
        if pre_pooled is not None:
            pre_pooled[id(r)] = pooled.get((pool_of(k[0]), k[1]), 0.0)
        q = float(r.get("quantity") or 0)
        if r.get("action") == "SPLIT":
            ratio = q if q > 0 else 1.0
            new = (str(r.get("symbol_new") or "")).strip() or k[1]
            old_p = pos.pop(k, 0.0)
            nk = (k[0], new)
            pos[nk] = pos.get(nk, 0.0) + old_p * ratio
            if id(r) in pool_events:
                pk = (pool_of(k[0]), k[1])
                held = pooled.pop(pk, 0.0)
                npk = (pk[0], new)
                pooled[npk] = pooled.get(npk, 0.0) + held * ratio
        else:
            pos[k] = pos.get(k, 0.0) + q
            pk = (pool_of(k[0]), k[1])
            pooled[pk] = pooled.get(pk, 0.0) + q
    return pos


def _marked_short_view(book: "Book") -> Tuple[Dict[int, float],
                                              Dict[int, float]]:
    """Canada: ({id(row): the taxable long pool before it}, {id(row): the
    units of a sale the broker's mark sends to the short pool}), as the
    engine routes them (core _MarkedShortRouter: CA-DISP-04, backlog
    TJ-0721), so a marked short is no sale of held units and does not net
    against another account's long (backlog TJ-1204, TJ-1207). Both empty
    when no taxable row carries a mark."""
    from taxjson.lib.core import (TaxTransaction, _MarkedShortRouter,
                                  _row_part)
    router = _MarkedShortRouter()
    fields = TaxTransaction.__dataclass_fields__
    views: Dict[int, Any] = {}
    for r in book.txs:
        if (book.kind.get(r["_acct"]) != "taxable"
                or r.get("action") not in ACQ_ACTIONS):
            continue
        try:
            t = TaxTransaction(**{k: v for k, v in r.items() if k in fields})
        except (TypeError, ValueError):
            continue
        t.account = r["_acct"]
        views[id(r)] = t
    if not any(t.action == "BUYSELL"
               and (router._opens_short(t) or "O" in router._codes(t))
               for t in views.values()):
        return {}, {}
    rows = [r for r in book.txs if book.kind.get(r["_acct"]) == "taxable"
            and (id(r) in views or r.get("action") == "SPLIT")]
    rows.sort(key=lambda r: (str(book.wdate(r) or ""), r.get("time") or ""))
    long_pool: Dict[str, float] = {}
    pre: Dict[int, float] = {}
    marked: Dict[int, float] = {}
    for r in dedupe_split_events(rows):
        sym = r.get("symbol") or ""
        if r.get("action") == "SPLIT":
            q = float(r.get("quantity") or 0)
            ratio = q if q > 0 else 1.0
            new = (str(r.get("symbol_new") or "")).strip() or sym
            router.split(sym, new, ratio)
            long_pool[new] = (long_pool.get(new, 0.0)
                              + long_pool.pop(sym, 0.0) * ratio)
            continue
        t = views[id(r)]
        pre[id(r)] = long_pool.get(sym, 0.0)
        units = router.crossing_units(t)
        parts = [t]
        if units is not None:
            first = _row_part(t, units, "C", t.id)
            parts = [first, _row_part(t, abs(float(t.quantity)) - units, "O",
                                      t.id, rest_of=first)]
        for part in parts:
            q = float(part.quantity or 0)
            if not router.walk(part):
                long_pool[sym] = long_pool.get(sym, 0.0) + q
            elif q < 0:
                marked[id(r)] = marked.get(id(r), 0.0) - q
    return pre, marked


def _opening_qty(book: "Book", r: Dict[str, Any]) -> float:
    """The part of a buy that OPENS (or adds to) a long position: a
    buy-to-close of a written option acquires nothing (core
    _opening_qty) — A2-0699, A2-1197."""
    q = float(r.get("quantity") or 0)
    before = book.pre_pos.get(id(r), 0.0)
    if q <= 0:
        return 0.0
    return q if before >= -1e-9 else max(0.0, q + before)


class Book:
    """The project's transactions (every account) and the taxable
    dispositions, as the engine saw them."""

    def __init__(self, root: Path, cfg: Dict[str, Any],
                 account: Optional[str] = None):
        from taxjson.lib.report_model import resolve_gains_files
        self.root = root
        self.cache = root / "work"
        settings = cfg.get("settings", {}) or {}
        self.year = int(settings.get("year") or 0)
        from taxjson.lib.country import (futures_settle_mode,
                                         settings_country, settings_tax_date)
        from taxjson.lib.missing_history import LOSS_RULE
        from taxjson.lib.income_dating import IncomeRules
        self.country = settings_country(settings)
        self.usa = self.country == "usa"
        # tax_date decides the YEAR a row lands in; the loss window is
        # counted on the engine's fixed dates (CA settle, US trade).
        self.basis = settings_tax_date(settings)
        self.window_basis = LOSS_RULE[self.country][1]
        self.futures_settle = futures_settle_mode(settings)
        self.income_rules = IncomeRules.from_settings(settings)
        from taxjson.lib.tax_logic import _local_tz
        self.local_tz = _local_tz(settings)
        from taxjson.lib.pipeline import option_timing_from_settings
        _kw = option_timing_from_settings(settings) or {}
        self.timing = _kw.get("option_premium_timing", "close")
        # Canada: whether a written option's loss is tested (CA-SL-12)
        # or exempt (CA-SL-11); the gains files' summary says what the
        # engine ran with.
        self.buyback_superficial = bool(
            _kw.get("option_buyback_loss_superficial"))
        accounts = cfg.get("accounts") or {}
        self.taxable = sorted(n for n, a in accounts.items()
                              if isinstance(a, dict)
                              and a.get("type") == "taxable")
        self.crypto = {n for n, a in accounts.items()
                       if isinstance(a, dict) and a.get("crypto")}
        self.kind = {n: (a.get("type") if isinstance(a, dict) else None)
                     for n, a in accounts.items()}
        self.only = account
        self.txs: List[Dict[str, Any]] = []
        self.missing: List[str] = []
        self.missing_history_openings = 0
        mh_pairs = self._missing_history()
        for name in sorted(accounts):
            p = self.cache / f"{name}_base.json"
            if not p.exists():
                self.missing.append(name)
                continue
            rows = [dict(r, _acct=name) for r in _rows(p)]
            if mh_pairs:
                rows += self._openings(rows, mh_pairs)
            self.txs.extend(rows)
        # Position before each row, per (account, symbol), on the tax
        # date basis (year straddles, opening vs closing call buys).
        self.pre_pos: Dict[int, float] = {}
        # And the position of the row's pool before it: Canada's
        # taxable accounts are one ACB pool, so a sale in one account
        # disposes of shares another holds (backlog TJ-0690).
        self.pool_pre_pos: Dict[int, float] = {}
        _walk(self.txs, self.bdate, pre=self.pre_pos,
              pre_pooled=self.pool_pre_pos, pool_of=self.pool)
        # A broker-marked short has a pool of its own (Canada only:
        # _marked_short_view); a book without one reads pool_pre_pos.
        self.long_pre_pos, self.marked_short = (
            ({}, {}) if self.usa else _marked_short_view(self))
        self.gains: List[Dict[str, Any]] = []
        # Covers traded this year, their loss the next's (US-DATE-01).
        self.covers_next_year: List[Dict[str, Any]] = []
        self.inventory: List[Dict[str, Any]] = []
        # The pools (Canada) or lots (US) at the end of the project
        # year: the engine's inventory_year_end; a gains file without it
        # (one written before it) gives its final inventory, marked
        # `_year_end` False (backlog TJ-0282, TJ-0284).
        self.year_end_pools: List[Dict[str, Any]] = []
        resolved = resolve_gains_files(self.cache, None) or {}
        for acct, f in resolved.items():
            doc = _work_doc(Path(f))
            _summ = doc.get("summary")
            if (isinstance(_summ, dict)
                    and "option_buyback_loss_superficial" in _summ):
                self.buyback_superficial = bool(
                    _summ["option_buyback_loss_superficial"])
            for t in doc.get("transactions") or []:
                if t.get("gain") is None or t.get("action"):
                    continue
                t = dict(t)
                t["_acct"] = t.get("account") or acct
                if self.kind.get(t["_acct"], self.kind.get(acct)) != "taxable":
                    continue            # registered: no gain or loss to report
                self.gains.append(t)
            for t in doc.get("covers_realized_next_year") or []:
                t = dict(t)
                t["_acct"] = t.get("account") or acct
                if self.kind.get(t["_acct"], self.kind.get(acct)) == "taxable":
                    self.covers_next_year.append(t)
            for h in doc.get("inventory") or []:
                h = dict(h)
                h["_acct"] = h.get("account") or acct
                self.inventory.append(h)
            _ye = doc.get("inventory_year_end")
            # The date basis the engine read the year end on: its walk's
            # (Canada settlement, US trade), or the one the gains file
            # names (a US settle or Canadian trade project, backlog
            # TJ-0343).
            _yeb = doc.get("inventory_year_end_basis")
            for h in (_ye if isinstance(_ye, list)
                      else doc.get("inventory") or []):
                if isinstance(h, dict):
                    self.year_end_pools.append(dict(
                        h, _acct=h.get("account") or acct,
                        _year_end=isinstance(_ye, list),
                        _ye_basis=(_yeb if _yeb in ("settle", "trade")
                                   else self.window_basis)))

    def _missing_history(self):
        """The (symbol, account) pairs the accounts' .tt OPENING
        cost=unknown lines open, as every twin view applies them
        (A2-1208); None without any. A line that cannot be read:
        ValueError."""
        from taxjson.lib.missing_history import (load_missing_history,
                                                  missing_history_arg)
        p = missing_history_arg(self.root)
        if p is None:
            return None
        try:
            return load_missing_history(p) or None
        except (OSError, ValueError) as e:
            raise ValueError(f"the .tt OPENING cost=unknown lines cannot "
                             f"be read ({e})") from None

    def _openings(self, rows: List[Dict[str, Any]], mh_pairs
                  ) -> List[Dict[str, Any]]:
        """The OPENING_BALANCE rows the gains stage synthesizes for this
        account's positions bought before the data (missing history)."""
        from dataclasses import asdict
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.missing_history import synthesize_openings
        name = rows[0]["_acct"] if rows else ""
        fields = TaxTransaction.__dataclass_fields__
        txs = []
        for r in rows:
            try:
                txs.append(TaxTransaction(**{k: v for k, v in r.items()
                                             if k in fields}))
            except (TypeError, ValueError):
                continue
        ids = {id(t) for t in txs}
        new, _log = synthesize_openings(txs, mh_pairs, flag_stale=False)
        out = []
        for t in new:
            if id(t) not in ids and t.action == "OPENING_BALANCE":
                out.append(dict(asdict(t), _acct=name))
        self.missing_history_openings += len(out)
        return out

    def bdate(self, r: Dict[str, Any]) -> Optional[date]:
        """The row's tax-year date: settlement or trade (tax_date)."""
        if self.basis == "settle":
            return _d(r.get("date_settle")) or _d(r.get("date"))
        return _d(r.get("date"))

    def other(self, r: Dict[str, Any]) -> Optional[date]:
        if self.basis == "settle":
            return _d(r.get("date"))
        return _d(r.get("date_settle")) or _d(r.get("date"))

    def wdate(self, r: Dict[str, Any]) -> Optional[date]:
        """The date the engine counts a loss window on: the settlement
        date in Canada (CA-SL-01), the trade date in the US (US-WASH-01)
        — whatever tax_date says (missing_history.loss_window_date)."""
        if self.window_basis == "settle":
            return _d(r.get("date_settle")) or _d(r.get("date"))
        return _d(r.get("date"))

    def wother(self, r: Dict[str, Any]) -> Optional[date]:
        """The other date, shown for reference only."""
        if self.window_basis == "settle":
            return _d(r.get("date"))
        return _d(r.get("date_settle")) or _d(r.get("date"))

    def in_wash_scope(self, acct: str) -> bool:
        """§1091 does not reach digital assets: a US crypto account has
        no wash-sale window (wash-radar and wash-sales leave it out,
        A2-1201). Canada's s.54 covers crypto."""
        return not (self.usa and acct in self.crypto)

    def pool(self, acct: str) -> str:
        """The holder whose units the engine counts together: in
        Canada every taxable account is one (the ACB pool, and the
        superficial-loss rule's held units, CA-SL-08), each registered
        or affiliated account alone; in the US each account alone."""
        if not self.usa and self.kind.get(acct) == "taxable":
            return "\0taxable"
        return acct

    def wanted(self, acct: str) -> bool:
        return not self.only or acct == self.only

    def window_row(self, r: Dict[str, Any]) -> bool:
        """A row that can be a window acquisition (or, Canada, a sale
        near day 30): a trade, assignment or transfer in wash scope. A
        US stock dividend is not a purchase (US-STKDIV-01), so the US
        never lists it as an in-window acquisition (re-audit A2-1547);
        Canada counts it (CA-STKDIV-01)."""
        from taxjson.lib.core import is_stock_dividend, not_a_purchase
        from taxjson.lib.country import stock_dividend_in_loss_window
        # An opening balance is not a purchase (CA-OPEN-01 / US-OPEN-01).
        if (r.get("action") not in ACQ_ACTIONS
                or r.get("action") == "OPENING_BALANCE"
                or not_a_purchase(r)
                or not self.in_wash_scope(r["_acct"])):
            return False
        return (stock_dividend_in_loss_window(self.country)
                or not is_stock_dividend(r))


# ------------------------------------------------------------ year boundary

def _lands(book: Book, r: Dict[str, Any]) -> Optional[int]:
    d = book.bdate(r)
    return d.year if d else None


def year_straddles(book: Book) -> List[Dict[str, Any]]:
    """Trades whose trade date and settlement date fall in different
    years, around this project's two year ends."""
    y = book.year
    # Opening balances (missing history, a broker's opening position) and
    # splits count (A2-0389, A2-1208): Book.pre_pos.
    # The position before the row is its pool's (Book.pool: Canada's
    # taxable accounts are one ACB pool, backlog TJ-0690).
    pre_pos = book.pool_pre_pos
    # Each sale's own gain rows (one per lot), by the id the engine
    # keeps; by (account, symbol, settlement date) only when one trade
    # has that key — two sales of a day each showed their sum (backlog
    # TJ-0691).
    gains_by_id: Dict[Tuple, List[Dict[str, Any]]] = {}
    gains_by_key: Dict[Tuple, List[Dict[str, Any]]] = {}
    for g in book.gains + book.covers_next_year:
        if g.get("id"):
            gains_by_id.setdefault((g["_acct"], g.get("symbol"),
                                    str(g["id"])), []).append(g)
        gains_by_key.setdefault((g["_acct"], g.get("symbol"),
                                 g.get("date_settle") or g.get("date")),
                                []).append(g)
    trades_by_key: Dict[Tuple, int] = {}
    for r in book.txs:
        if r.get("action") in ("BUYSELL", "ASSIGN"):
            k = (r["_acct"], r.get("symbol"),
                 r.get("date_settle") or r.get("date"))
            trades_by_key[k] = trades_by_key.get(k, 0) + 1
    out = []
    for r in book.txs:
        if r.get("action") not in ("BUYSELL", "ASSIGN"):
            continue
        if not book.wanted(r["_acct"]):
            continue
        t, s = _d(r.get("date")), _d(r.get("date_settle"))
        if not t or not s or t.year == s.year:
            continue
        if not ({t.year, s.year} & {y}):
            continue
        sym = r.get("symbol") or ""
        qty = float(r.get("quantity") or 0)
        lands = _lands(book, r)
        taxable = book.kind.get(r["_acct"]) == "taxable"
        hits = (gains_by_id.get((r["_acct"], sym, str(r["id"])), [])
                if r.get("id") else [])
        key = (r["_acct"], sym, r.get("date_settle"))
        if not hits and trades_by_key.get(key) == 1:
            hits = gains_by_key.get(key, [])
        # A short cover's loss realized on delivery is the settle year's,
        # whatever the trade date (US-DATE-01; the engine keeps the trade
        # in `trade_date`). A cover of several lots can be both: a gain
        # stays on the trade date (backlog TJ-1189).
        moved = [h for h in hits if h.get("trade_date")]
        if moved and book.basis != "settle":
            gain = sum(float(h.get("gain") or 0) for h in moved)
            mq = sum(float(h.get("qty") or 0) for h in moved)
            out.append({
                "account": r["_acct"], "symbol": sym, "qty": mq,
                "trade_date": str(t), "settle_date": str(s),
                "lands_in": s.year, "taxable": taxable,
                "gain": round(gain, 2),
                "why": (f"traded {t}, settles {s}; a short sale closed at a "
                        f"loss is realized when the shares are delivered to "
                        f"close it, so it is a {s.year} short cover: a "
                        f"disposition whose gain or loss is in {s.year}'s "
                        f"Form 8949 ({gain:+,.2f})")})
            hits = [h for h in hits if not h.get("trade_date")]
            qty -= mq
            if abs(qty) <= 1e-9:
                continue
        g = None
        if hits:
            g = {"gain": sum(float(h.get("gain") or 0) for h in hits)}
        opt = _is_option(sym)
        before = pre_pos.get(id(r), 0.0)
        # A row the engine booked a disposition for closes a position,
        # whatever its own account held (TJ-0690), except the premium
        # grant timing books on the write itself (direction SHORT, the
        # write's own id): that write opens one (s.49(1), TJ-1067).
        grant_write = (opt and qty < 0 and bool(hits)
                       and all(h.get("direction") == "SHORT" for h in hits))
        closing = ((bool(hits) and not grant_write)
                   or (before < -1e-9 and qty > 0)
                   or (before > 1e-9 and qty < 0))
        if closing and qty > 0:
            what = ("buy-to-close of a written option" if opt
                    else "short cover")
        elif closing:
            what = "option sale" if opt else "sale"
        elif qty < 0:
            what = "option write" if opt else "short sale"
        else:
            what = "option purchase" if opt else "purchase"
        why = (f"traded {t}, settles {s}; tax_date = \"{book.basis}\" books "
               f"it on the {'settlement' if book.basis == 'settle' else 'trade'}"
               f" date, so it is a {lands} {what}")
        if not taxable:
            why += (" (IRA: no tax effect, but it counts for wash-sale "
                    "windows)" if book.usa else
                    " (registered account: no tax effect, but it counts for "
                    "superficial-loss windows)")
        elif closing:
            why += (f": a disposition whose gain or loss is in {lands}'s "
                    f"{'Form 8949' if book.usa else 'Schedule 3'}"
                    + (f" ({float(g['gain']):+,.2f})" if g is not None else ""))
        elif opt and qty < 0 and book.usa:
            why += "; the premium is taxed when the option is closed (§1234)"
        elif opt and qty < 0:
            why += (f"; under grant timing the premium is a gain in {lands}"
                    if book.timing == "grant" else
                    f"; the premium is realized when the option is closed")
        else:
            why += f"; the cost joins the pool in {lands}"
        out.append({"account": r["_acct"], "symbol": sym, "qty": qty,
                    "trade_date": str(t), "settle_date": str(s),
                    "lands_in": lands, "taxable": taxable,
                    "gain": (round(float(g.get("gain") or 0), 2)
                             if g is not None else None),
                    "why": why})
    out.sort(key=lambda x: (x["trade_date"], x["account"], x["symbol"]))
    return out


def last_days_dispositions(book: Book, days: int = 3) -> List[Dict[str, Any]]:
    """Taxable dispositions traded in the last `days` calendar days of a
    year or the first `days` of the next, that do not straddle (listed so
    the year they land in is explicit, e.g. futures on the trade date)."""
    y = book.year
    out = []
    for g in book.gains:
        if not book.wanted(g["_acct"]):
            continue
        # The trade date itself: a short cover's loss realized on delivery
        # is dated its settle date and listed as a straddle (TJ-1189).
        t, s = (_d(g.get("trade_date") or g.get("date")),
                _d(g.get("date_settle")))
        if not t or not s or t.year != s.year:
            continue
        near = any(abs((t - date(yy, 12, 31)).days) < days
                   or 0 <= (t - date(yy + 1, 1, 1)).days < days
                   for yy in (y - 1, y))
        if not near:
            continue
        sym = g.get("symbol") or ""
        fut = _is_future(sym)
        why = f"traded and settles in {t.year}"
        if fut:
            why += (" (futures settle on the trade date"
                    + (": futures_settle = \"trade\")" if
                       book.futures_settle == "trade" else ")"))
        out.append({"account": g["_acct"], "symbol": sym,
                    "qty": float(g.get("qty") or 0), "trade_date": str(t),
                    "settle_date": str(s), "lands_in": t.year,
                    "gain": round(float(g.get("gain") or 0), 2), "why": why})
    out.sort(key=lambda x: (x["trade_date"], x["symbol"]))
    return out


def _assign_settlement(book: Book, acct: str, opts: List[Dict[str, Any]]
                       ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """(underlying, share legs) of the option ASSIGN rows `opts` of
    `acct`, as the engines settle them (lib/core): the underlying the
    assignment resolver names (_make_assign_underlying_resolver; None:
    it never trades as shares in the account, so the assignment is
    CASH-SETTLED, backlog TJ-0675), and the share legs the premium
    folds into — each option's own legs (_pair_assign_legs: dated up to
    3 days before to 7 days after it, a delivery on the next business
    day of a new year included, backlog TJ-0674), else the first trade
    in the assignment's direction up to 7 days after it, or with none
    the first in the other direction, as the premium ledger drains an
    unpaired entry (_AssignLedger.take, backlog TJ-1068), among the
    trades whose shares the options paired with them left over (backlog
    TJ-1230); [] when none (the run warns the premium is unconsumed)."""
    import contextlib
    import io
    from taxjson.lib.core import (TaxTransaction, _assign_delivery_shares,
                                  _assign_direction,
                                  _make_assign_underlying_resolver,
                                  _MARKED_LEG_MAX_LAG_DAYS,
                                  _pair_assign_legs)
    from taxjson.lib.corporate_timeline import event_sort_key
    cache = book.__dict__.setdefault("_assign_books", {})
    if acct not in cache:
        fields = TaxTransaction.__dataclass_fields__
        txs: List[Any] = []
        rows: Dict[str, Dict[str, Any]] = {}
        for n, r in enumerate(book.txs):
            if r["_acct"] != acct or r.get("action") not in ("BUYSELL",
                                                             "ASSIGN"):
                continue
            try:
                t = TaxTransaction(**{k: v for k, v in r.items()
                                      if k in fields})
            except (TypeError, ValueError):
                continue
            # Unique ids: a base book's ids can repeat.
            t.id = f"edge-{n}"
            t.account = acct
            rows[t.id] = r
            txs.append(t)
        if book.usa:
            txs.sort(key=lambda x: event_sort_key(
                x, profile="us_main", date_of=lambda t: t.date))
        else:
            txs.sort(key=lambda x: event_sort_key(
                x, profile="ca_main",
                date_of=lambda t: t.date_settle or t.date))
        # The run already printed the pairing's notes (an assumed
        # contract size, an ambiguous option root): not again here.
        with contextlib.redirect_stderr(io.StringIO()):
            resolve = _make_assign_underlying_resolver(
                txs, lambda t: t.date, quiet=True)
            pairs = _pair_assign_legs(txs, lambda _t: True, resolve)
        # Each trade's shares its paired options do not take first
        # (_AssignPremiumLedger.take, step 1).
        left = {t.id: abs(float(t.quantity or 0)) for t in txs}
        for t in txs:
            need = _assign_delivery_shares(t) if t.id in pairs else None
            for leg_id in pairs.get(t.id, ()):
                took = min(need or 0.0, left.get(leg_id, 0.0))
                left[leg_id] = left.get(leg_id, 0.0) - took
                need = (need or 0.0) - took
        cache[acct] = (txs, rows, resolve, pairs, left)
    txs, rows, resolve, pairs, left = cache[acct]
    ids = {i for i, r in rows.items() if any(r is o for o in opts)}
    und: Optional[str] = None
    got: List[str] = []
    for n, t in enumerate(txs):
        if t.id not in ids:
            continue
        with contextlib.redirect_stderr(io.StringIO()):
            u = resolve(t)
        if not u:
            continue
        und = und or u
        paired = pairs.get(t.id)
        if paired:
            got += [i for i in paired if i not in got]
            continue
        d = _assign_direction(t)
        leg = None
        for x in txs[n + 1:]:
            q = float(x.quantity or 0)
            try:
                gap = (datetime.strptime(x.date, "%Y-%m-%d")
                       - datetime.strptime(t.date, "%Y-%m-%d")).days
            except (TypeError, ValueError):
                continue
            if (x.symbol == u and x.action in ("BUYSELL", "ASSIGN")
                    and abs(q) > 1e-12 and 0 <= gap
                    <= _MARKED_LEG_MAX_LAG_DAYS):
                free = left.get(x.id, 0.0) > 1e-9
                if d is None or (1 if q > 0 else -1) == d:
                    # A later leg of its own direction keeps the entry
                    # from an opposite trade before it, even one with
                    # no shares left to take it.
                    leg = x if free else None
                    if free:
                        break
                elif free:
                    leg = leg or x
        if leg is not None and leg.id not in got:
            got.append(leg.id)
    return und, [rows[i] for i in got]


def option_expiries(book: Book) -> List[Dict[str, Any]]:
    """Option contracts expiring in the last week of December or the first
    week of January that were open before their expiry day, and what
    that day did with them.

    The position is replayed with the series' splits and renames (the
    shared `_walk`): a series a SPLIT retired holds nothing, its
    successor what the split gave it (backlog TJ-0564). On the expiry
    day an assignment or exercise is not an expiry (A2-0700) and a
    priced close is a trade, listed with the trades; only the rest
    expires (a sale of part of the position that day was counted as
    expiring: backlog TJ-0563). One row per outcome: `held` the
    position at the start of the expiry day (a split or rename dated
    that day applied: the day's rows are the successor's, backlog
    TJ-0623), `units` the contracts of the row's
    outcome (signed as held), `traded` those closed by a priced trade
    that day; an assigned or exercised part is a row of its own
    (`assigned`), landing where its share leg does."""
    y = book.year

    def in_window(exp: date) -> bool:
        return any(date(yy, 12, 31) - timedelta(days=6) <= exp
                   <= date(yy, 12, 31) + timedelta(days=7)
                   for yy in (y - 1, y))

    opt_rows = [r for r in book.txs
                if _is_option(str(r.get("symbol") or ""))
                and r.get("action") in ACQ_ACTIONS + ("SPLIT",)]
    series = set()
    for r in opt_rows:
        names = [str(r.get("symbol") or "")]
        if r.get("action") == "SPLIT":
            names.append(str(r.get("symbol_new") or "").strip())
        for sym in names:
            exp = _expiry(sym)
            if sym and exp and in_window(exp):
                series.add((r["_acct"], sym))
    before: Dict[date, Dict[Tuple[str, str], float]] = {}
    out = []
    for (acct, sym) in sorted(series):
        if not book.wanted(acct) or book.kind.get(acct) != "taxable":
            continue
        exp = _expiry(sym)
        if exp not in before:
            # The position at the start of the expiry day: the rows
            # before it, and a split or rename dated that day (it applies
            # before the day's trades, which then read the successor:
            # backlog TJ-0623).
            before[exp] = _walk(
                [r for r in opt_rows
                 if r.get("action") == "SPLIT"
                 or (_d(r.get("date")) or exp) < exp],
                lambda r: _d(r.get("date")), until=exp)
        held = before[exp].get((acct, sym), 0.0)
        if abs(held) < 1e-9:
            continue
        side = "long" if held > 0 else "written"
        sign = 1.0 if held > 0 else -1.0
        on_day = [r for r in opt_rows
                  if r["_acct"] == acct and r.get("symbol") == sym
                  and r.get("action") != "SPLIT"
                  and _d(r.get("date")) == exp]
        left = abs(held)
        # The contracts assigned or exercised that day, then those a
        # priced trade closed (a close is the other way round from the
        # position); the rest expire.
        assigned = min(left, sum((abs(float(r.get("quantity") or 0))
                                  for r in on_day
                                  if r.get("action") == "ASSIGN"), 0.0))
        left -= assigned
        traded = min(left, sum(
            (abs(float(r.get("quantity") or 0)) for r in on_day
            if r.get("action") == "BUYSELL"
            and abs(float(r.get("price") or 0)) > 1e-9
            and float(r.get("quantity") or 0) * sign < 0), 0.0))
        left -= traded
        expiring = left if left > 1e-9 else 0.0
        closed = "sold" if side == "long" else "bought back"
        what = "assigned" if side == "written" else "exercised"
        traded_note = (f"{traded:g} {closed} that day, a trade listed with "
                       f"the trades" if traded > 1e-9 else "")
        base = {"account": acct, "symbol": sym, "held": held,
                "expiry": str(exp), "traded": traded}
        if assigned > 1e-9:
            opts = [r for r in on_day if r.get("action") == "ASSIGN"]
            und, legs = _assign_settlement(book, acct, opts)
            premium = "premium" if side == "written" else "cost"
            settlement = "physical"
            if und is None:
                # Cash-settled (the underlying never trades as shares
                # in the account): the option is closed for cash on
                # the day, nothing folds into a share leg (TJ-0675).
                settlement = "cash"
                cash = sum(abs(float(r.get("net_amount") or 0))
                           for r in opts)
                od = book.bdate(opts[0]) if opts else None
                lands = od.year if od else exp.year
                why = (f"{side} {assigned:g} {what} on its expiry date "
                       f"{exp} and settled in cash ({cash:,.2f} "
                       f"{'paid' if side == 'written' else 'received'})"
                       f": {_underlying(sym) or 'the underlying'} never "
                       f"trades as shares in this account, so no share "
                       f"leg takes the {premium} — a disposition of the "
                       f"option that lands in {lands}")
            elif legs:
                # The share leg the engine pairs it with, which can be
                # dated after the expiry (a delivery on the next
                # business day of a new year, TJ-0674).
                ld = book.bdate(legs[0])
                lands = ld.year if ld else exp.year
                why = (f"{side} {assigned:g} {what} on its expiry date "
                       f"{exp}: not an expiry — the {premium} folds into "
                       f"the {und} leg of {legs[0].get('date')}"
                       + (f" (settles {legs[0].get('date_settle')})"
                          if legs[0].get("date_settle") not in
                          (None, "", legs[0].get("date")) else "")
                       + f", which lands in {lands}")
            else:
                lands = None
                why = (f"{side} {assigned:g} {what} on its expiry date "
                       f"{exp}: not an expiry, but no {und} leg in this "
                       f"account up to 7 days after it takes the "
                       f"{premium} (the run warns it is unconsumed: add "
                       f"the missing share row)")
            if abs(held) - assigned > 1e-9:
                why += (f" ({abs(held):g} held"
                        + (f"; {traded_note}" if traded_note else "")
                        + (f"; {expiring:g} expiring" if expiring else "")
                        + ")")
            out.append(dict(base, units=sign * assigned, lands_in=lands,
                            assigned=True, settlement=settlement,
                            outcome=f"{assigned:g} {what}"
                            + (" for cash" if settlement == "cash" else "")
                            + (f", {traded:g} {closed}"
                               if traded > 1e-9 and not expiring else ""),
                            why=why))
        if not expiring:
            continue
        why = (f"{side} {expiring:g} expiring {exp}: an expiry is a "
               f"disposition on the expiry date itself, so it lands in "
               f"{exp.year}")
        if abs(held) - expiring > 1e-9:
            why += (f" ({abs(held):g} held"
                    + (f"; {traded_note}" if traded_note else "")
                    + (f"; {assigned:g} {what}" if assigned > 1e-9
                       else "")
                    + ")")
        if side == "written" and not book.usa:
            why += (" (under grant timing the premium was already a gain"
                    " in the year written; see `tjs option-boundary`)")
        out.append(dict(base, units=sign * expiring, lands_in=exp.year,
                        outcome=(f"{traded:g} {closed}, " if traded > 1e-9
                                 else "") + f"{expiring:g} expire",
                        why=why))
    return out


def _income_why(book: Book, r: Dict[str, Any], pay: date, lands: date,
                is_roc: bool) -> str:
    """Why an income row (or a return of capital) lands in its year, on
    the project's own law: lib/income_dating is the one source of the
    date (A2-0387, A2-1209)."""
    rules = book.income_rules
    a = str(r.get("action") or "")
    if is_roc:
        rec = rules.roc_record_date(r)
        if rec:
            return (f"a Canadian trust's return of capital lowers the ACB "
                    f"on its record date {rec}, not the pay date {pay} "
                    f"(s.53(2)(h)): {lands.year}")
        if book.usa:
            return (f"a nondividend distribution lowers the basis when "
                    f"paid ({pay}, §301(c)(2)): {lands.year}")
        return (f"a return of capital lowers the ACB when paid ({pay})"
                + ("" if not rules.is_canadian_trust(r) else
                   " — the export prints no record date, so the pay date "
                   "is used; a trust's ROC lowers the ACB when payable "
                   "(s.53(2)(h)): check the T3")
                + f": {lands.year}")
    if book.usa:
        if rules.ric_prior_year(r):
            return (f"listed in ric_january_dividends: a January fund/REIT "
                    f"dividend declared in Oct-Dec is received on Dec 31 "
                    f"(§852(b)(7), §857(b)(9)), not the pay date {pay}: "
                    f"{lands.year}")
        return (f"income is taxed in the year it is paid (the date the "
                f"broker books it, {pay}): {lands.year}")
    rec = rules.trust_record_date(r)
    if rec:
        return (f"a Canadian trust's distribution is income of the year it "
                f"became payable — its record date {rec}, not the pay date "
                f"{pay} (s.104(13)): {lands.year}")
    if a == "DIVIDEND" and str(r.get("income_label") or "").lower() \
            == "distribution" and rules.is_canadian_trust(r):
        return (f"a Canadian trust's distribution is income when payable "
                f"(s.104(13)); the export prints no record date, so the "
                f"pay date {pay} is used — check the T3: {lands.year}")
    if a == "DIVIDEND_IN_LIEU":
        return f"a payment in lieu is income when paid ({pay}): {lands.year}"
    if a == "DIVIDEND":
        return (f"a dividend is income when paid (s.82(1); the date the "
                f"broker books it, {pay}), not the record or ex-dividend "
                f"date: {lands.year}")
    if a == "TAX":
        return (f"tax withheld goes with the payment it was taken from "
                f"({pay}): {lands.year}")
    return f"income of the year it is paid ({pay}): {lands.year}"


def income_near_new_year(book: Book, days: int = 5) -> List[Dict[str, Any]]:
    """Income rows (dividends, payments in lieu, interest, tax withheld)
    and returns of capital paid — or dated by a record date — within
    `days` of a year end, with the year lib/income_dating puts them in
    (the engine's and divs-sum's year)."""
    from taxjson.lib.income_dating import is_return_of_capital
    y = book.year
    rules = book.income_rules
    out = []
    for r in book.txs:
        a = r.get("action")
        # The shared classification: a hand-entered ADJUST with no type
        # is a return of capital too, as roc-sum reads it (backlog
        # TJ-0685: only `type=roc` was listed).
        is_roc = is_return_of_capital(r)
        if a not in INCOME_ACTIONS and not is_roc:
            continue
        if not book.wanted(r["_acct"]):
            continue
        if book.kind.get(r["_acct"]) != "taxable" or r["_acct"] in book.crypto:
            continue
        if abs(float(r.get("net_amount") or 0)) < 1:
            continue
        pay = _d(r.get("date"))
        if not pay:
            continue
        lands = _d(rules.row_date(r)) or pay
        near = False
        for yy in (y - 1, y):
            ye = date(yy, 12, 31)
            if any(-days < (x - ye).days <= days for x in (pay, lands)):
                near = True
        if not near:
            continue
        out.append({"account": r["_acct"], "symbol": r.get("symbol"),
                    "action": "ROC" if is_roc else a, "date": str(pay),
                    "tax_date": str(lands),
                    "amount": round(float(r.get("net_amount") or 0), 2),
                    "lands_in": lands.year,
                    "why": _income_why(book, r, pay, lands, is_roc)})
    out.sort(key=lambda x: (x["date"], x["symbol"] or ""))
    return out


def _local_to_utc(ts: datetime, tz_name: str) -> datetime:
    """Naive local wall-clock time in `tz_name` -> naive UTC (the inverse
    of the parsers' _crypto_common.utc_to_local)."""
    from datetime import timezone
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        from taxjson.lib.brokerages._crypto_common import utc_to_local
        for off in (5, 4):
            cand = ts + timedelta(hours=off)
            if utc_to_local(cand, tz_name) == ts:
                return cand
        # A time inside the hour the clocks repeat (utc_to_local orders
        # its two passes, backlog TJ-0747) or skip: within an hour of
        # standard time, far from any year end.
        return ts + timedelta(hours=5)
    return ts.replace(tzinfo=tz).astimezone(timezone.utc).replace(
        tzinfo=None)


def crypto_midnight(book: Book) -> List[Dict[str, Any]]:
    """Crypto rows whose local date and UTC date fall in different years
    at one of the project's year ends. The books date crypto in the
    project's local_timezone; the exchanges' own statements are in UTC,
    so these rows sit in the other year there (A2-1200, A2-1202..1204:
    the UTC time came from a fixed EST offset whatever the zone)."""
    y = book.year
    out = []
    if not book.local_tz:
        # No zone named (no crypto account: `taxjson` refuses a crypto
        # project without one): nothing was dated in a local zone.
        return out
    for r in book.txs:
        if r["_acct"] not in book.crypto or not book.wanted(r["_acct"]):
            continue
        try:
            ts = datetime.strptime(f"{r.get('date')} {r.get('time') or '00:00:00'}",
                                   "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if not (y - 1 <= ts.year <= y + 1):
            continue
        utc = _local_to_utc(ts, book.local_tz)
        if utc.year == ts.year or min(ts.year, utc.year) not in (y - 1, y):
            continue
        out.append({"account": r["_acct"], "symbol": r.get("symbol"),
                    "action": r.get("action"), "local": str(ts),
                    "utc": str(utc), "timezone": book.local_tz,
                    "qty": float(r.get("quantity") or 0),
                    "lands_in": ts.year,
                    "why": (f"booked at {ts} local time ({book.local_tz}), "
                            f"in {ts.year}; in UTC this is {utc}, "
                            f"{utc.year} on the exchange's own statement")})
    out.sort(key=lambda x: x["local"])
    return out


def windows_across_year_end(book: Book) -> List[Dict[str, Any]]:
    """Losses whose 61-day window spans Dec 31, with where the denied
    amount goes. The window and the acquisitions on its other side are
    on the engine's window dates (Book.wdate), not tax_date (A2-1206)."""
    y = book.year
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    # US: a short-cover loss is replaced by a new short sale, never a
    # purchase (US-WASH-05, US-PLAN-02): the opening part of each sale,
    # as a copy with that many units (backlog TJ-0686). Canada counts
    # purchases only, after a short too (CA-SL-07).
    shorts: Dict[str, List[Dict[str, Any]]] = {}
    for r in book.txs:
        if not book.window_row(r):
            continue
        q = float(r.get("quantity") or 0)
        if q > 0:
            by_sym.setdefault(r.get("symbol") or "", []).append(r)
        elif q < 0 and book.usa and r.get("action") == "BUYSELL":
            opening = -q - min(-q, max(book.pre_pos.get(id(r), 0.0), 0.0))
            if opening > 1e-9:
                shorts.setdefault(r.get("symbol") or "", []).append(
                    dict(r, quantity=opening, _short=True))
    out = []
    for g in book.gains:
        if float(g.get("raw_gain", g.get("gain") or 0) or 0) >= -0.005:
            continue
        if not book.wanted(g["_acct"]) or not book.in_wash_scope(g["_acct"]):
            continue
        ld, ty = book.wdate(g), book.bdate(g)
        if not ld or not ty:
            continue
        for yy in (y - 1, y):
            ye = date(yy, 12, 31)
            lo, hi = ld - timedelta(days=WINDOW), ld + timedelta(days=WINDOW)
            if not (lo <= ye < hi):
                continue
            short = book.usa and g.get("direction") == "SHORT"
            other_side = [r for r in (shorts if short else by_sym).get(
                              g.get("symbol") or "", [])
                          if (d := book.wdate(r)) and lo <= d <= hi
                          and (d.year != ld.year)]
            if not other_side:
                continue
            denied = float(g.get("disallowed_amount") or 0)
            perm = float(g.get("permanently_disallowed") or 0)
            sign = "-" if short else "+"
            acq = ", ".join(f"{r['_acct']} {book.wdate(r)} "
                            f"{sign}{float(r.get('quantity') or 0):g}"
                            for r in other_side[:4])
            allowed_by = None
            if denied > 0.005:
                verdict = (f"denied {denied:,.2f}"
                           + (f" ({perm:,.2f} permanently, "
                              f"{'IRA' if book.usa else 'registered'} "
                              f"replacement)" if perm > 0.005 else
                              "; taken off the replacement short sale's "
                              "proceeds, so it comes back when that short "
                              "is covered" if short else
                              "; added to the replacement's cost, so it "
                              "comes back when that lot is sold"))
            else:
                allowed_by, verdict = _allowed_why(book, g, hi, other_side)
            out.append({"account": g["_acct"], "symbol": g.get("symbol"),
                        "loss_date": str(ld),
                        "raw_loss": round(float(g.get("raw_gain") or 0), 2),
                        "denied": round(denied, 2), "permanent": round(perm, 2),
                        "allowed_by": allowed_by,
                        "other_year_acquisitions": acq,
                        "why": (f"a {ty.year} "
                                + ("short-cover " if short else "")
                                + f"loss whose window runs "
                                f"{lo}..{hi}, across Dec 31 {yy}: "
                                + ("new short sales" if short
                                   else "acquisitions")
                                + f" in the other year ({acq}) count. "
                                f"{verdict}")})
            break
    out.sort(key=lambda x: (x["loss_date"], x["symbol"] or ""))
    return out


def _allowed_why(book: Book, g: Dict[str, Any], day30: date,
                 acqs: Iterable[Dict[str, Any]] = ()
                 ) -> Tuple[str, str]:
    """(allowed_by, verdict) for a loss the engine denied nothing of,
    from what the books show, never a guess (backlog TJ-0283): a .tt
    ALLOWLOSS position, a written option's exempt loss (Canada,
    CA-SL-11), an in-kind contribution's loss denied by s.40(2)(g)(iv)
    instead (CA-INKIND-03), nothing held at the end of day 30 (Canada,
    CA-SL-02), each purchase in the window (`acqs`) the lot sold or
    another loss's replacement (US, _us_allowed_why); otherwise no
    reason is claimed."""
    from taxjson.lib.trace_format import filing_position_text
    fp = filing_position_text(g, book.country)
    if fp:
        return "filing_position", f"allowed by your {fp}"
    if book.usa:
        return _us_allowed_why(book, g, list(acqs))
    sym = g.get("symbol") or ""
    if (_is_option(sym) and g.get("direction") == "SHORT"
            and not book.buyback_superficial):
        return "buyback_exempt", (
            "allowed: a written option's loss is exempt from the rule "
            "(option_buyback_loss_superficial = false), so its window is "
            "not tested")
    dc = float(g.get("denied_contribution") or 0)
    if dc > 0.005:
        return "in_kind_contribution", (
            f"not a superficial loss: an in-kind contribution to a "
            f"registered plan, whose loss ({dc:,.2f}) s.40(2)(g)(iv) "
            f"denies for good")
    holders = _holders_tested(book, sym, g["_acct"],
                              day30 - timedelta(days=WINDOW))
    held = sum(_long_at(book, sym, day30, h) for h in holders)
    # Units of a holder that acquired nothing in the window back no
    # denial (CA-SL-08, backlog TJ-1113): named, never counted.
    apart = sum(_long_at(book, sym, day30, h)
                for h in {book.pool(r["_acct"]) for r in book.txs}
                if h not in holders)
    aside = (f"; {apart:g} held by another holder that acquired none in "
             f"the window do not count" if abs(apart) > 1e-9 else "")
    if abs(held) <= 1e-9:
        return "not_held_day30", (f"allowed: no units were held at the end "
                                  f"of day 30 ({day30}){aside}")
    return "unknown", (f"no denial applied by the engine ({held:g} units "
                       f"were held at the end of day 30, {day30}{aside}; "
                       f"the books do not record why)")


def _us_allowed_why(book: Book, g: Dict[str, Any],
                    acqs: List[Dict[str, Any]]) -> Tuple[str, str]:
    """(allowed_by, verdict) for a US loss with nothing disallowed whose
    window holds the purchases `acqs`: each one is, as the books show
    it, part of the lot this sale sold (the sale's rows name each lot
    sold by its acquisition date) or a replacement another loss used
    (that loss's `wash_replacements` names the purchase). A purchase
    neither accounts for is said to be unexplained, never assumed
    (backlog TJ-0286)."""
    used: Dict[str, List[Tuple[Dict[str, Any], float]]] = {}
    sold: Dict[Tuple[str, str], float] = {}
    sid = g.get("id")
    for o in book.gains:
        if o is g:
            continue
        for w in o.get("wash_replacements") or []:
            if isinstance(w, dict) and w.get("tx_id"):
                used.setdefault(str(w["tx_id"]), []).append(
                    (o, float(w.get("match_qty") or 0)))
    for o in book.gains:
        # One sale is a row per lot sold, all with the sale's id.
        if (sid and o.get("id") == sid and o["_acct"] == g["_acct"]
                and o.get("symbol") == g.get("symbol")):
            k = (o["_acct"], str(o.get("acquired_date") or "")[:10])
            sold[k] = sold.get(k, 0.0) + abs(float(o.get("qty") or 0))
    reasons: List[str] = []
    unexplained: List[str] = []
    kinds = set()
    for r in acqs:
        q = float(r.get("quantity") or 0)
        d = str(book.wdate(r))
        label = f"{r['_acct']} {d} {'-' if r.get('_short') else '+'}{q:g}"
        k = (r["_acct"], d)
        s = min(q, sold.get(k, 0.0))
        sold[k] = sold.get(k, 0.0) - s
        uses = used.get(str(r.get("id") or ""), [])
        u = min(q - s, sum(m for _o, m in uses))
        if s + u < q - 1e-6:
            unexplained.append(label)
            continue
        parts = []
        if s > 1e-9:
            kinds.add("lot_sold")
            parts.append("is the lot sold" if s >= q - 1e-6 else
                         f"is the lot sold ({s:g} units)")
        if u > 1e-9:
            kinds.add("replacement_used")
            losses = sorted({
                "another lot of this sale's loss" if o.get("id") == sid
                else (f"the {str(o.get('date') or '')[:10]} loss"
                      + ("" if o["_acct"] == g["_acct"]
                         else f" in {o['_acct']}"))
                for o, _m in uses})
            parts.append("replaced " + " and ".join(losses))
        reasons.append(f"{label} " + " and ".join(parts))
    if unexplained or not acqs:
        what = ", ".join(unexplained) or "the purchases in the window"
        return "unknown", (
            f"no disallowance applied by the engine (the books do not "
            f"record why {what} "
            f"{'is' if len(unexplained) == 1 else 'are'} not its "
            f"replacement" + (f"; {'; '.join(reasons)}" if reasons else "")
            + ")")
    return ("replacement_used" if "replacement_used" in kinds
            else "lot_sold"), (
        f"allowed: none of those purchases is its replacement "
        f"({'; '.join(reasons)})")


def deferred_into_next_year(book: Book) -> List[Dict[str, Any]]:
    """The denied losses in the cost of units held at the end of the
    project year (`as_of` that day): the engine's year-end pools (Canada)
    or lots (US-RPT-13), so a later sale or purchase does not change
    them. The engine reads the day on its own dates (Canada settlement,
    the US trade dates: the loss window's), or on the project's
    tax_date when it is the other (the gains file's
    `inventory_year_end_basis`, TJ-0343); a project whose tax_date is
    not the date read (an older gains file) is told which. A gains file
    without them gives its final
    inventory, `as_of` "end_of_data", and the text says so (backlog
    TJ-0282, TJ-0284)."""
    out = []
    for h in book.year_end_pools:
        dw = float(h.get("deferred_wash") or 0)
        if dw > 0.005 and book.wanted(h["_acct"]):
            # The day is read on the engine's walk dates (CA-SL-01
            # settle, US-WASH-01 trade) unless the file names another.
            read_on = h.get("_ye_basis") or book.window_basis
            on = ("" if book.basis == read_on else
                  " (by settlement date)" if read_on == "settle" else
                  " (by trade date)")
            qty = float(h.get("qty") or 0)
            # A short position (negative units) carries the denial in
            # the proceeds of its open short sales, and it lands when
            # they are covered (US Reg. §1.1091-1(g); backlog TJ-0342).
            short = qty < 0
            state = "still short" if short else "still held"
            if h["_year_end"]:
                held = f"{state} at the end of {book.year}{on}"
            else:
                held = (f"{'short' if short else 'held'} at the end of the "
                        f"books, which may be after {book.year} (re-run "
                        f"`tjs run` for the year-end figure)")
            where = (f"the proceeds of the {abs(qty):g} units" if short
                     else f"the cost of the {qty:g} units")
            out.append({"account": h["_acct"], "symbol": h.get("symbol"),
                        "qty": qty, "deferred": round(dw, 2),
                        "as_of": (f"{book.year}-12-31" if h["_year_end"]
                                  else "end_of_data"),
                        "why": (f"{dw:,.2f} of denied losses sits in {where} "
                                f"{held}; it reduces the gain (or grows the "
                                f"loss) of the year they are "
                                f"{'covered' if short else 'sold'}")})
    out.sort(key=lambda x: -x["deferred"])
    return out


# --------------------------------------------------------- window edges

def _names_of(book: Book, sym: str) -> Set[str]:
    """`sym` and every symbol renamed into it by a SPLIT."""
    names = {sym}
    grew = True
    while grew:
        grew = False
        for r in book.txs:
            if (r.get("action") == "SPLIT"
                    and str(r.get("symbol_new") or "").strip() in names
                    and (r.get("symbol") or "") not in names):
                names.add(r.get("symbol") or "")
                grew = True
    return names


def _holders_tested(book: Book, sym: str, acct: str, loss: date) -> Set[str]:
    """The holders (Book.pool) the Canadian day-30 test is about for a
    loss of `sym` in `acct` on `loss`: the loss's own and each that
    acquired units in the window. The engine takes the units acquired
    and held per holder (CA-SL-08), so another holder's older units back
    no denial (backlog TJ-1113)."""
    out = {book.pool(acct)}
    names = _names_of(book, sym)
    for r in book.txs:
        if ((r.get("symbol") or "") in names and book.window_row(r)
                and float(r.get("quantity") or 0) > 0
                and (d := book.wdate(r))
                and abs((d - loss).days) <= WINDOW):
            out.add(book.pool(r["_acct"]))
    return out


def _held_at(book: Book, sym: str, when: date,
             holder: Optional[str] = None) -> float:
    """Units of `sym` held across every account (or, with `holder`, the
    accounts of that Book.pool) at the end of `when` (a window date):
    openings and SPLIT ratios applied, and a position renamed into
    `sym` by a SPLIT counted under it (A2-0388/0389)."""
    names = _names_of(book, sym)
    rows = [r for r in book.txs if (r.get("symbol") or "") in names
            and book.in_wash_scope(r["_acct"])]
    pos = _walk(rows, book.wdate, until=when)
    return sum(v for (a, s_), v in pos.items() if s_ == sym
               and (holder is None or book.pool(a) == holder))


def _long_at(book: Book, sym: str, when: date, holder: str) -> float:
    """Units a holder holds at the end of `when`: none while it is net
    short, whose negative must not hide another holder's units in a sum
    (backlog TJ-1260)."""
    return max(0.0, _held_at(book, sym, when, holder))


def _losses(book: Book):
    """(gain row, loss window date, the other date) for each taxable
    loss of the project year — the year by tax_date, the window date by
    the engine's fixed basis (A2-0133/0134/0135)."""
    for g in book.gains:
        if not book.wanted(g["_acct"]) or not book.in_wash_scope(g["_acct"]):
            continue
        if float(g.get("raw_gain", g.get("gain") or 0) or 0) >= -0.005:
            continue
        ty, ld = book.bdate(g), book.wdate(g)
        if not ty or not ld or ty.year != book.year:
            continue
        yield g, ld, book.wother(g)


def _verdict(g: Dict[str, Any]) -> str:
    denied = float(g.get("disallowed_amount") or 0)
    perm = float(g.get("permanently_disallowed") or 0)
    if denied <= 0.005:
        return "allowed"
    return f"denied {denied:,.2f}" + (f" ({perm:,.2f} permanently)"
                                      if perm > 0.005 else "")


def _group(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge same-day fills of the same kind in the same account."""
    merged: Dict[Tuple, Dict[str, Any]] = {}
    for it in items:
        k = (it["kind"], it["account"], it["date"], it.get("option"))
        if k in merged:
            merged[k]["qty"] += it["qty"]
            if it.get("opens_short"):
                merged[k]["opens_short"] = (merged[k].get("opens_short", 0.0)
                                            + it["opens_short"])
        else:
            merged[k] = dict(it)
    return sorted(merged.values(), key=lambda i: (i["day"], i["kind"]))


def _describe(book: Book, it: Dict[str, Any], held_end: float) -> str:
    basis = "settlement" if book.window_basis == "settle" else "trade"
    other = "trade" if basis == "settlement" else "settlement"
    side = "before" if it["day"] < 0 else "after"
    reg = ((" (IRA)" if book.usa else " (registered)")
           if it["sheltered"] else "")
    if it["kind"] == "sale":
        txt = (f"sale of {abs(it['qty']):g} in {it['account']}{reg} on "
               f"{it['date']}, day {it['day']} after the loss: "
               + ("on or before day 30, so those units were NOT still held "
                  "when the window closed" if it["inside"] else
                  "after day 30, so those units were still held when the "
                  "window closed"))
        if it.get("opens_short"):
            txt += (f" (the other {it['opens_short']:g} sold opened a "
                    f"short)")
    elif it["kind"] == "short sale":
        txt = (f"short sale of {abs(it['qty']):g} in {it['account']}{reg} "
               f"on {it['date']}, day {it['day']} after the loss: it opens "
               f"a short (no units were held to sell), so it says nothing "
               f"about the units held when the window closed")
    else:
        where = "INSIDE" if it["inside"] else "OUTSIDE"
        txt = (f"{it['kind']} of {it['qty']:g} in {it['account']}{reg} on "
               f"{it['date']}: day {abs(it['day'])} {side} the loss -> "
               f"{where} the window")
        # (Shares held say nothing of a long call: its own check is
        # calls_in_windows.)
        if (it["kind"] == "acquisition" and it["inside"] and it["day"] < 0
                and held_end <= 1e-9 and not book.usa):
            txt += (" (but none of it was still held on day 30, so it is "
                    "not a replacement: it is the lot that was sold)")
    alt = it["other_basis_day"]
    if alt is not None and abs(alt) != abs(it["day"]):
        # Reference only: the engine counts the window on the
        # {basis} date whatever tax_date says (A2-0135).
        txt += (f" (day {abs(alt)} on the {other} date, which does not "
                f"decide the window)")
    if it.get("option") and book.usa:
        txt += (f". {it['option']} may be an option to acquire the shares "
                f"(§1091(a)); the US engine does not deny on it — a "
                f"warning only, check it by hand")
    elif it.get("option"):
        txt += (f". {it['option']} is a right to acquire the shares "
                f"(s.54 para (i)): replacement property if still held on "
                f"day 30")
    return txt


def _long_calls(book: Book) -> Dict[str, List[Tuple[Dict[str, Any], float]]]:
    """Call purchases that OPEN a long position, by underlying, with the
    opening quantity: a buy-to-close of a written call acquires no
    option (core._opening_qty; A2-0699, A2-1197)."""
    calls: Dict[str, List[Tuple[Dict[str, Any], float]]] = {}
    for r in book.txs:
        sym = r.get("symbol") or ""
        if r.get("action") != "BUYSELL" or _right(sym) != "C":
            continue
        if not book.in_wash_scope(r["_acct"]):
            continue
        oq = _opening_qty(book, r)
        und = _underlying(sym)
        # A futures option is never a sized replacement of the futures
        # loss, however it is spelled — the engines flag it for a
        # manual check (CA-SL-15 / US-WASH-15; audit A2-0378).
        from taxjson.lib.core import _FUTURES_PREFIX_RE
        if oq > 1e-9 and und and not _FUTURES_PREFIX_RE.match(und):
            calls.setdefault(und, []).append((r, oq))
    return calls


def window_edges(book: Book, margin: int = 3) -> List[Dict[str, Any]]:
    """For each taxable loss in the project year: acquisitions of the same
    security (any account) within `margin` days of either window edge, and
    (Canada only: s.54's still-held test) sales within `margin` days of
    day 30 that decide 'still held'. The US (§1091) has no still-held
    test, so a sale near day 30 decides nothing, and a long call is a
    warning only (calls_in_windows), never a window item."""
    lo_edge, hi_edge = WINDOW - margin, WINDOW + margin
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    calls = _long_calls(book)
    for r in book.txs:
        if book.window_row(r):
            by_sym.setdefault(r.get("symbol") or "", []).append(r)
    out = []
    for g, ld, lo_ in _losses(book):
        sym = g.get("symbol") or ""
        items = []
        sold_by_loss = _sold_by_loss(book, g, ld) if book.usa else {}
        for r in by_sym.get(sym, []):
            d, od = book.wdate(r), book.wother(r)
            if not d:
                continue
            off = (d - ld).days
            q = float(r.get("quantity") or 0)
            if book.usa and q > 0 and off < 0 and r["_acct"] == g["_acct"]:
                # US: shares closed by the loss's own sale (or sold
                # before it) never replace it — a purchase in the
                # selling account replaces only with shares still
                # unsold at the loss (backlog TJ-0471: the lot sold
                # was listed as the day-30 acquisition).
                gone = min(q, sold_by_loss.get(str(d), 0.0))
                sold_by_loss[str(d)] = sold_by_loss.get(str(d), 0.0) - gone
                q -= gone
                if q <= 1e-9:
                    continue
            alt = (od - lo_).days if od and lo_ else None
            base = {"account": r["_acct"], "date": str(d), "qty": q,
                    "day": off, "other_basis_day": alt,
                    "sheltered": book.kind.get(r["_acct"]) != "taxable"}
            if q > 0 and lo_edge <= abs(off) <= hi_edge:
                items.append(dict(base, kind="acquisition",
                                  inside=abs(off) <= WINDOW))
            elif q < 0 and lo_edge <= off <= hi_edge and not book.usa:
                # Only the units the holder (Book.pool) held long before
                # the row are SOLD; the rest opens a short and says
                # nothing about the units held on day 30 (backlog
                # TJ-1014: a new short on day 31 was explained as
                # replacement units still held when the window closed).
                # A broker-marked short sells none of them (TJ-1207), and
                # is not netted with them (TJ-1204).
                held = max(0.0, book.long_pre_pos.get(
                    id(r), book.pool_pre_pos.get(id(r), 0.0)))
                sold = min(-q - book.marked_short.get(id(r), 0.0), held)
                short = -q - sold
                if sold > 1e-9:
                    items.append(dict(base, kind="sale", qty=-sold,
                                      inside=off <= WINDOW,
                                      **({"opens_short": short}
                                         if short > 1e-9 else {})))
                elif short > 1e-9:
                    items.append(dict(base, kind="short sale", qty=-short,
                                      inside=off <= WINDOW))
        if not _is_option(sym) and not book.usa:
            for r, oq in calls.get(sym, []):
                d, od = book.wdate(r), book.wother(r)
                if not d:
                    continue
                off = (d - ld).days
                if not lo_edge <= abs(off) <= hi_edge:
                    continue
                alt = (od - lo_).days if od and lo_ else None
                items.append({"kind": "long call", "account": r["_acct"],
                              "sheltered": book.kind.get(r["_acct"]) != "taxable",
                              "date": str(d), "qty": oq,
                              "day": off, "other_basis_day": alt,
                              "option": r.get("symbol"),
                              "inside": abs(off) <= WINDOW})
        if not items:
            continue
        end = ld + timedelta(days=WINDOW)
        # Canada: the holders the engine's test is about (CA-SL-08,
        # backlog TJ-1113). The US has no still-held test.
        holders = (None if book.usa
                   else _holders_tested(book, sym, g["_acct"], ld))
        held_end = (_held_at(book, sym, end) if holders is None
                    else sum(_long_at(book, sym, end, h) for h in holders))
        items = _group(items)
        for it in items:
            pool = book.pool(it["account"])
            own_end = (held_end if book.usa
                       else _held_at(book, sym, end, pool))
            # Units its holder held on day 30 beyond those bought after
            # the loss: only these can be a pre-loss acquisition still
            # on hand.
            bought_after = sum(float(r.get("quantity") or 0)
                               for r in by_sym.get(sym, [])
                               if float(r.get("quantity") or 0) > 0
                               and (book.usa or book.pool(r["_acct"]) == pool)
                               and (d := book.wdate(r)) and ld < d <= end)
            it["why"] = _describe(book, it, own_end - bought_after
                                  if it["day"] < 0 else own_end)
        out.append({"account": g["_acct"], "symbol": sym,
                    "loss_date": str(ld),
                    "qty": float(g.get("qty") or 0),
                    "raw_loss": round(float(g.get("raw_gain") or 0), 2),
                    "held_at_day30": round(held_end, 6),
                    "verdict": _verdict(g), "items": items})
    out.sort(key=lambda x: (x["loss_date"], x["symbol"]))
    return out


def _sold_by_loss(book: Book, g: Dict[str, Any], ld) -> Dict[str, float]:
    """US: {purchase date: units} of the loss's account and security that
    were sold by the time of the loss — by its own sale (every gain row
    of that sale) or an earlier one — read from the gain rows' lot
    dates (`acquired_date`, on the window basis's trade date). A row
    with no lot date counts for nothing."""
    out: Dict[str, float] = {}
    sym, acct, gid = g.get("symbol"), g["_acct"], g.get("id")
    for r in book.gains:
        if r["_acct"] != acct or r.get("symbol") != sym:
            continue
        acq = str(r.get("acquired_date") or "")[:10]
        d = book.wdate(r)
        if not acq or not d:
            continue
        if not (d < ld or (gid and r.get("id") == gid and d == ld)):
            continue
        if (r.get("direction") or "LONG") == "SHORT":
            continue
        out[acq] = out.get(acq, 0.0) + abs(float(r.get("qty") or 0.0))
    return out


def calls_in_windows(book: Book) -> List[Dict[str, Any]]:
    """Share losses with a long call on the same shares bought inside the
    window (s.54 'a right to acquire'). The engine treats a call still
    held on day 30 as replacement property (100 shares per contract)."""
    calls = _long_calls(book)
    out = []
    for g, ld, lo_ in _losses(book):
        sym = g.get("symbol") or ""
        if _is_option(sym) or sym not in calls:
            continue
        end = ld + timedelta(days=WINDOW)
        items = []
        for r, oq in calls[sym]:
            d, od = book.wdate(r), book.wother(r)
            if not d or abs((d - ld).days) > WINDOW:
                continue
            osym = r.get("symbol")
            # Held by the call's own holder (Book.pool), as the engine
            # takes the held units per holder (CA-SL-08): another
            # account's call never backs this one (backlog TJ-0689).
            held = _held_at(book, osym, end, book.pool(r["_acct"]))
            alt = (od - lo_).days if od and lo_ else None
            items.append({"kind": "long call", "account": r["_acct"],
                          "sheltered": book.kind.get(r["_acct"]) != "taxable",
                          "date": str(d), "qty": oq,
                          "day": (d - ld).days, "inside": True,
                          "other_basis_day": alt, "option": osym,
                          "held_at_day30": held})
        if not items:
            continue
        items = _group(items)
        for it in items:
            if book.usa:
                it["why"] = _describe(book, it, 1.0)
                continue
            it["why"] = (_describe(book, it, 1.0)
                         + ("; still held on day 30, so it backs a denial"
                            if it["held_at_day30"] > 1e-9
                            else "; no longer held on day 30, so it does not"))
        out.append({"account": g["_acct"], "symbol": sym,
                    "loss_date": str(ld), "qty": float(g.get("qty") or 0),
                    "raw_loss": round(float(g.get("raw_gain") or 0), 2),
                    "verdict": _verdict(g), "items": items})
    out.sort(key=lambda x: (x["loss_date"], x["symbol"]))
    return out


def analyze(root: Path, cfg: Dict[str, Any], *, margin: int = 3,
            account: Optional[str] = None) -> Dict[str, Any]:
    from taxjson.lib.pipeline import option_timing_from_settings
    from taxjson.lib.core import TaxTransaction
    book = Book(root, cfg, account)
    if not book.usa:
        from taxjson.lib.option_boundary import straddling
    settings = cfg.get("settings", {}) or {}
    kw = option_timing_from_settings(settings) or {}
    timing = kw.get("option_premium_timing", "close")
    since = kw.get("option_grant_since")
    written = []
    # The filed-year locks option-boundary reads — filed/<year>.json and
    # the prior_year_record lock — with the timing each recorded, so the
    # two commands give one verdict on the same contract (A2-0390,
    # A2-1205).
    filed: set = set()
    filed_timing: Dict[int, Dict[str, Any]] = {}
    filed_premiums: Dict[int, Any] = {}
    # Each lock's name in the advice: a prior_year_record lock by its
    # path, as option-boundary names it (backlog TJ-0225).
    filed_labels: Dict[int, str] = {}
    if not book.usa:
        import sys as _sys
        from taxjson.lib.option_boundary import filed_locks
        filed, filed_timing = filed_locks(
            root, settings,
            warn=lambda m: emit_line(f"tjs edge-cases: warning: {m}",
                                     file=_sys.stderr),
            premiums=filed_premiums, labels=filed_labels)
    # Written options across a year end are an s.49(1) grant-timing
    # boundary (option-boundary is Canada-only); a US premium is taxed
    # at the close (§1234), so there is nothing to place.
    for name in ([] if book.usa else book.taxable):
        if not book.wanted(name):
            continue
        txs = []
        for r in book.txs:
            if r["_acct"] != name:
                continue
            try:
                txs.append(TaxTransaction(**{k: v for k, v in r.items()
                                             if k in TaxTransaction.__dataclass_fields__}))
            except TypeError:
                continue
        for r in straddling(txs, book.year, timing, since, filed,
                            filed_timing=filed_timing,
                            tax_date=book.basis,
                            filed_labels=filed_labels,
                            filed_premiums=filed_premiums):
            r["account"] = r.get("account") or name
            written.append(r)
    return {
        "year": book.year, "basis": book.basis, "country": book.country,
        "window_basis": book.window_basis,
        "futures_settle": book.futures_settle, "margin": margin,
        "missing_history_openings": book.missing_history_openings,
        "missing_books": book.missing,
        "year_boundary": {
            "straddles": year_straddles(book),
            "last_days": last_days_dispositions(book),
            "written_options": written,
            "option_expiries": option_expiries(book),
            "loss_windows": windows_across_year_end(book),
            "deferred_at_year_end": deferred_into_next_year(book),
            "income": income_near_new_year(book),
            "crypto_midnight": crypto_midnight(book),
        },
        "window_edges": window_edges(book, margin),
        "calls_in_windows": calls_in_windows(book),
        "renamed_late": _renamed_late(root, cfg, account),
    }


def _renamed_late(root: Path, cfg: Dict[str, Any],
                  account: Optional[str]) -> List[Dict[str, Any]]:
    """Trades in a ticker after the date it was renamed away
    (lib/renames): a different security unless ticker.map folds them."""
    from taxjson.lib.renames import report
    try:
        return report(root, cfg, account, undated=False,
                      hints=False)["late"]
    except ValueError:
        return []


def _money(x: Optional[float]) -> str:
    return "-" if x is None else f"{x:,.2f}"


def render_text(doc: Dict[str, Any], verbose: bool = False,
                width_: Optional[int] = None,
                details: bool = False) -> List[str]:
    """The report in the house layout (docs/output-style.md): a title,
    one section per kind of edge — a short upper-case heading with its
    count, then a table that fits the width. The default view is the
    essentials (Essentials first): the sections with rows, a window
    edge's verdict and a late rename's resolution under its row, and one
    line naming the edges with none. `details` adds every section, the
    rule each turns on, each row's why and the counting notes."""
    from taxjson.lib.out import Doc, fit_table, wrap
    y, basis = doc["year"], doc["basis"]
    usa = doc.get("country") == "usa"
    rule = "Wash-sale" if usa else "Superficial-loss"
    d = Doc(f"EDGE CASES — tax year {y}; date basis: {basis}"
            f"{' (settlement date, CRA)' if basis == 'settle' and not usa else (' (settlement date)' if basis == 'settle' else ' (trade date)')}"
            f"; futures: {doc['futures_settle']} date", width_=width_)
    w = d.w
    yb = doc["year_boundary"]

    def table(headers, rows, cells, details):
        """Rows as a fitted table, each row's detail lines (wrapped,
        indented) under it."""
        body = [[str(c) for c in cells(r)] for r in rows]
        lines = fit_table(headers, body, width_=w, indent="  ",
                          per_record=False)
        d.line("\n".join(lines[:2]))
        for r, ln in zip(rows, lines[2:]):
            d.line(ln)
            for t in details(r):
                for x in wrap(t, w, "    ", "      "
                              if t.startswith("- ") else "    "):
                    d.line(x)

    _all = details
    _none: List[str] = []

    def section(title, rows, headers, cells, empty="None.", note=None,
                details=lambda r: [r["why"]] if r.get("why") else [],
                essential=None, legend=None):
        """`essential`: the detail lines the default view keeps under a
        row; `legend`: the line the default view puts above the
        table."""
        if not rows and not _all:
            _none.append(title[:1].lower() + title[1:])
            return
        d.section(f"{title.upper()} ({len(rows)})")
        if note and _all:
            d.para(note, "  ")
        if not rows:
            d.para(empty, "  ")
            return
        if legend and not _all:
            d.para(legend, "  ")
        table(headers, rows, cells,
              details if _all else (essential or (lambda r: [])))

    section("Trades that settle in a different year than they trade",
            yb["straddles"],
            ["ACCOUNT", "SYMBOL", "QTY", "TRADE", "SETTLE", "LANDS IN"],
            lambda r: (r["account"], r["symbol"], f"{r['qty']:+g}",
                       r["trade_date"], r["settle_date"], r["lands_in"]))
    section("Dispositions in the last and first days of a year",
            yb["last_days"],
            ["ACCOUNT", "SYMBOL", "QTY", "TRADE", "SETTLE", "GAIN",
             "LANDS IN"],
            lambda r: (r["account"], r["symbol"], f"{r['qty']:g}",
                       r["trade_date"], r["settle_date"], _money(r["gain"]),
                       r["lands_in"]))
    if not usa:
        def _to_do(r):
            """A row that asks for something (ATTENTION, a T1-ADJ): the
            default view says so under it, as option-boundary's does;
            the JSON's `attention` alone was easy to miss (backlog
            TJ-0396)."""
            a = str(r.get("action") or "")
            if r.get("attention"):
                return ["needs attention: tjs option-boundary --details"]
            if a.startswith("T1-ADJ"):
                return ["an amended return (T1-ADJ): tjs option-boundary "
                        "--details"]
            return []
        section("Written options across a year end", yb["written_options"],
                ["ACCOUNT", "SYMBOL", "WRITTEN", "PREMIUM", "CLOSED"],
                lambda r: (r["account"], r["symbol"], r["written"],
                           _money(r["premium"]), r["closed"] or "open"),
                empty="None open across Dec 31.",
                details=lambda r: ([r["where"]] if r.get("where") else [])
                + ([f"=> {r['action']}"] if r.get("action") else []),
                essential=_to_do)
    section("Options expiring at a year end", yb["option_expiries"],
            ["ACCOUNT", "SYMBOL", "HELD", "ON EXPIRY DAY", "EXPIRES",
             "LANDS IN"],
            lambda r: (r["account"], r["symbol"], f"{r['held']:+g}",
                       r.get("outcome") or "", r["expiry"],
                       "-" if r["lands_in"] is None else r["lands_in"]))
    section(f"{rule} windows that span Dec 31", yb["loss_windows"],
            ["ACCOUNT", "SYMBOL", "LOSS", "ON", "DENIED"],
            lambda r: (r["account"], r["symbol"], _money(r["raw_loss"]),
                       r["loss_date"], _money(r["denied"])))
    _eod = any(r.get("as_of") == "end_of_data"
               for r in yb["deferred_at_year_end"])
    _short = any(float(r.get("qty") or 0) < 0
                 for r in yb["deferred_at_year_end"])
    section("Denied losses carried in positions held at the end of "
            + ("the books" if _eod else str(y)),
            yb["deferred_at_year_end"],
            ["ACCOUNT", "SYMBOL", "UNITS", "DEFERRED"],
            lambda r: (r["account"], r["symbol"], f"{r['qty']:g}",
                       _money(r["deferred"])),
            legend=("DEFERRED: denied losses in the cost of units still "
                    "held (negative UNITS: the proceeds of units still "
                    "short); they lower the gain when sold (or covered)."
                    if _short else
                    "DEFERRED: denied losses in the cost of units still "
                    "held; they lower the gain when sold."))
    section("Income paid around New Year", yb["income"],
            ["ACCOUNT", "SYMBOL", "ACTION", "DATE", "AMOUNT", "LANDS IN"],
            lambda r: (r["account"], str(r["symbol"]), r["action"],
                       r["date"], _money(r["amount"]), r["lands_in"]))
    section("Crypto near midnight at a year end", yb["crypto_midnight"],
            ["ACCOUNT", "SYMBOL", "ACTION", "LOCAL TIME", "QTY",
             "LANDS IN"],
            lambda r: (r["account"], str(r["symbol"]), r["action"],
                       r["local"], f"{r['qty']:+g}", r["lands_in"]))

    def loss_cells(r):
        c = [r["account"], r["symbol"], _money(r["raw_loss"]),
             r["loss_date"], f"{r['qty']:g}"]
        return c + ([] if usa else [f"{r['held_at_day30']:g}"])
    loss_headers = (["ACCOUNT", "SYMBOL", "LOSS", "ON", "UNITS"]
                    + ([] if usa else ["HELD DAY 30"]))

    def items(r):
        # The verdict first, then each acquisition or sale near the edge.
        return [f"verdict: {r['verdict']}"] + [f"- {it['why']}"
                                                   for it in r["items"]]

    we = doc["window_edges"]
    section(f"{rule} window edges", we, loss_headers, loss_cells,
            note=(f"{y} losses with activity within {doc['margin']} days "
                  f"of day 30."), details=items,
            essential=lambda r: [f"verdict: {r['verdict']}"])
    cw = doc.get("calls_in_windows") or []
    section("Long calls bought inside a share loss's window", cw,
            loss_headers[:5],
            lambda r: (r["account"], r["symbol"], _money(r["raw_loss"]),
                       r["loss_date"], f"{r['qty']:g}"),
            note=("A warning only: §1091 may treat a call as an option to "
                  "acquire the shares, but the US engine does not deny the "
                  "loss on it; check these by hand." if usa else
                  "A call is a right to acquire the shares (s.54), so one "
                  "still held on day 30 is replacement property."),
            details=items,
            essential=lambda r: [f"verdict: {r['verdict']}"])
    rl = doc.get("renamed_late") or []
    section("Trades in an old ticker after its rename", rl,
            ["ACCOUNT", "SYMBOL", "QTY", "DATE", "RENAMED TO", "ON"],
            lambda r: (r["account"], str(r["symbol"]), f"{r['qty']:+g}",
                       r["date"], r["renamed_to"], r["rename_date"]),
            note=("A separate security unless ticker.map folds them "
                  "(`tjs renames`)."),
            details=lambda r: [str(r["resolution"])],
            essential=lambda r: [str(r["resolution"])])
    if not _all:
        d.blank()
        if doc.get("missing_books"):
            d.para(f"No work files for: {', '.join(doc['missing_books'])} "
                   f"(run `tjs run`).")
        d.line(f"None on the other {len(_none)} edges checked; the rules and "
               f"each row's why: tjs edge-cases --details"
               if _none else
               "The rules and each row's why: tjs edge-cases --details")
        return d.lines()
    d.section("HOW THE WINDOW IS COUNTED")
    if usa:
        d.para("Window day counts are on TRADE dates, as the engine "
               "counts them whatever tax_date says (tax_date only "
               "decides the year). The window is the "
               "30 days before and after the loss (§1091): a purchase "
               "inside it in any account, IRAs included, makes the loss a "
               "wash sale whatever is sold later — there is no still-held "
               "test. Crypto is property, not a security: no wash-sale "
               "window.", "  ")
    else:
        d.para("Window day counts are on SETTLEMENT dates, as the engine "
               "counts them whatever tax_date says (tax_date only "
               "decides the year). The window is the 30 days "
               "before and after the loss, and the replacement must still be "
               "held at the end of day 30 (ITA s.54).", "  ")
    d.blank()
    if doc.get("missing_books"):
        d.para(f"No work files for: {', '.join(doc['missing_books'])} "
               f"(run `tjs run`).")
    d.para("Whether last year's closing positions, January settlements "
           "and corrections are carried into this year exactly once: "
           "`tjs handoff`.")
    return d.lines()
