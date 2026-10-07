"""`taxjson edge-cases`: the transactions whose tax treatment turns on a
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
from typing import Any, Dict, Iterable, List, Optional, Tuple

WINDOW = 30
# Rows that change a position (OPENING_BALANCE: a missing_history.json opening
# or a broker's opening position, A2-0389). SPLIT rows scale it (_walk).
ACQ_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER', 'OPENING_BALANCE')
# The engine's income actions (it has no 'PIL' or 'ROC' action: a
# payment in lieu is DIVIDEND_IN_LIEU, a return of capital an ADJUST of
# type roc — A2-1207).
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
        raise ValueError(f"{e} — re-run `taxjson run`") from None


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
          pre: Optional[Dict[int, float]] = None
          ) -> Dict[Tuple[str, str], float]:
    """Positions per (account, symbol) after the rows dated on or before
    `until` (by `datef`; every row when None): trades, transfers and
    openings add their quantity, a SPLIT scales the position by its
    ratio and moves it to `symbol_new` (A2-0388). `pre` gets each row's
    position before it, keyed by id(row)."""
    pos: Dict[Tuple[str, str], float] = {}
    moving = [r for r in rows
              if r.get("action") in ACQ_ACTIONS + ("SPLIT",)]
    moving.sort(key=lambda r: (str(datef(r) or ""), r.get("time") or ""))
    for r in moving:
        d = datef(r)
        if until is not None and (d is None or d > until):
            continue
        k = (r.get("_acct") or "", r.get("symbol") or "")
        if pre is not None:
            pre[id(r)] = pos.get(k, 0.0)
        q = float(r.get("quantity") or 0)
        if r.get("action") == "SPLIT":
            ratio = q if q > 0 else 1.0
            new = (str(r.get("symbol_new") or "")).strip() or k[1]
            p = pos.pop(k, 0.0) * ratio
            nk = (k[0], new)
            pos[nk] = pos.get(nk, 0.0) + p
        else:
            pos[k] = pos.get(k, 0.0) + q
    return pos


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
        _walk(self.txs, self.bdate, pre=self.pre_pos)
        self.gains: List[Dict[str, Any]] = []
        self.inventory: List[Dict[str, Any]] = []
        resolved = resolve_gains_files(self.cache, None) or {}
        for acct, f in resolved.items():
            doc = _work_doc(Path(f))
            for t in doc.get("transactions") or []:
                if t.get("gain") is None or t.get("action"):
                    continue
                t = dict(t)
                t["_acct"] = t.get("account") or acct
                if self.kind.get(t["_acct"], self.kind.get(acct)) != "taxable":
                    continue            # registered: no gain or loss to report
                self.gains.append(t)
            for h in doc.get("inventory") or []:
                h = dict(h)
                h["_acct"] = h.get("account") or acct
                self.inventory.append(h)

    def _missing_history(self):
        """missing_history.json's (symbol, account) pairs (or the legacy
        phantoms.json's), as every twin view applies them (A2-1208);
        None without the file. Both names present: ValueError."""
        from taxjson.lib.missing_history import (load_missing_history,
                                                  project_missing_history_file)
        p = project_missing_history_file(self.root)
        if p is None:
            return None
        try:
            return load_missing_history(p) or None
        except (OSError, ValueError) as e:
            raise ValueError(f"{p.name} cannot be read ({e})") from None

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
    # Opening balances (missing_history.json, a broker's opening position) and
    # splits count (A2-0389, A2-1208): Book.pre_pos.
    pre_pos = book.pre_pos
    gains_by_key: Dict[Tuple, List[Dict[str, Any]]] = {}
    for g in book.gains:
        gains_by_key.setdefault((g["_acct"], g.get("symbol"),
                                 g.get("date_settle") or g.get("date")),
                                []).append(g)
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
        hits = gains_by_key.get((r["_acct"], sym, r.get("date_settle")), [])
        g = None
        if hits:
            g = {"gain": sum(float(h.get("gain") or 0) for h in hits)}
        opt = _is_option(sym)
        before = pre_pos.get(id(r), 0.0)
        closing = (before < -1e-9 and qty > 0) or (before > 1e-9 and qty < 0)
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
        t, s = _d(g.get("date")), _d(g.get("date_settle"))
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


def option_expiries(book: Book) -> List[Dict[str, Any]]:
    """Option contracts expiring in the last week of December or the first
    week of January that were open at the year end before expiry."""
    y = book.year
    pos: Dict[Tuple[str, str], float] = {}
    for r in sorted(book.txs, key=lambda r: (r.get("date") or "",
                                             r.get("time") or "")):
        sym = r.get("symbol") or ""
        if not _is_option(sym) or r.get("action") not in ACQ_ACTIONS:
            continue
        exp = _expiry(sym)
        if not exp:
            continue
        pos.setdefault((r["_acct"], sym), 0.0)
    out = []
    for (acct, sym) in sorted(pos):
        if not book.wanted(acct) or book.kind.get(acct) != "taxable":
            continue
        exp = _expiry(sym)
        for yy in (y - 1, y):
            ye = date(yy, 12, 31)
            if not (ye - timedelta(days=6) <= exp <= ye + timedelta(days=7)):
                continue
            mine = [r for r in book.txs
                    if r["_acct"] == acct and r.get("symbol") == sym
                    and r.get("action") in ACQ_ACTIONS]
            held = sum(float(r.get("quantity") or 0) for r in mine
                       if (_d(r.get("date")) or exp) < exp)
            if abs(held) < 1e-9:
                continue
            side = "long" if held > 0 else "written"
            # What happened ON the expiry day (A2-0700): an assignment or
            # exercise is not an expiry — the premium (or cost) folds
            # into the share leg, which lands where that leg settles; a
            # priced close that day is a trade, listed with the trades.
            on_day = [r for r in mine if _d(r.get("date")) == exp]
            if any(r.get("action") == "ASSIGN" for r in on_day):
                und = _underlying(sym) or ""
                legs = [r for r in book.txs
                        if r["_acct"] == acct and r.get("action") == "ASSIGN"
                        and r.get("symbol") == und
                        and _d(r.get("date")) == exp]
                ld = book.bdate(legs[0]) if legs else None
                lands = ld.year if ld else exp.year
                what = "assigned" if side == "written" else "exercised"
                why = (f"{side} {abs(held):g} {what} on its expiry date "
                       f"{exp}: not an expiry — the "
                       f"{'premium' if side == 'written' else 'cost'} "
                       f"folds into the {und or 'share'} leg"
                       + (f" (settles {legs[0].get('date_settle')})"
                          if legs and legs[0].get("date_settle") else "")
                       + f", which lands in {lands}")
                out.append({"account": acct, "symbol": sym, "held": held,
                            "expiry": str(exp), "lands_in": lands,
                            "assigned": True, "why": why})
                continue
            closed_by_trade = sum(
                float(r.get("quantity") or 0) for r in on_day
                if r.get("action") == "BUYSELL"
                and abs(float(r.get("price") or 0)) > 1e-9)
            if abs(held + closed_by_trade) < 1e-9:
                continue
            why = (f"{side} {abs(held):g} expiring {exp}: an expiry is a "
                   f"disposition on the expiry date itself, so it lands in "
                   f"{exp.year}")
            if side == "written" and not book.usa:
                why += (" (under grant timing the premium was already a gain"
                        " in the year written; see `taxjson option-boundary`)")
            out.append({"account": acct, "symbol": sym, "held": held,
                        "expiry": str(exp), "lands_in": exp.year, "why": why})
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
    y = book.year
    rules = book.income_rules
    out = []
    for r in book.txs:
        a = r.get("action")
        is_roc = (a == "ADJUST"
                  and str(r.get("type") or "").lower() == "roc")
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
        raise ValueError(f"local_timezone {tz_name!r} is not available "
                         f"(install the `tzdata` package)")
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
    for r in book.txs:
        if book.window_row(r) and float(r.get("quantity") or 0) > 0:
            by_sym.setdefault(r.get("symbol") or "", []).append(r)
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
            other_side = [r for r in by_sym.get(g.get("symbol") or "", [])
                          if (d := book.wdate(r)) and lo <= d <= hi
                          and (d.year != ld.year)]
            if not other_side:
                continue
            denied = float(g.get("disallowed_amount") or 0)
            perm = float(g.get("permanently_disallowed") or 0)
            acq = ", ".join(f"{r['_acct']} {book.wdate(r)} +{float(r.get('quantity') or 0):g}"
                            for r in other_side[:4])
            if denied > 0.005:
                verdict = (f"denied {denied:,.2f}"
                           + (f" ({perm:,.2f} permanently, "
                              f"{'IRA' if book.usa else 'registered'} "
                              f"replacement)" if perm > 0.005 else
                              f"; added to the replacement's cost, so it "
                              f"comes back when that lot is sold"))
            elif book.usa:
                verdict = ("allowed (the engine matched none of those "
                           "purchases as a replacement: it was the lot "
                           "sold, or an earlier loss used it)")
            else:
                verdict = "allowed (the replacement was not still held on day 30, " \
                          "or it was sold before the window closed)"
            out.append({"account": g["_acct"], "symbol": g.get("symbol"),
                        "loss_date": str(ld),
                        "raw_loss": round(float(g.get("raw_gain") or 0), 2),
                        "denied": round(denied, 2), "permanent": round(perm, 2),
                        "other_year_acquisitions": acq,
                        "why": (f"a {ty.year} loss whose window runs "
                                f"{lo}..{hi}, across Dec 31 {yy}: acquisitions "
                                f"in the other year ({acq}) count. {verdict}")})
            break
    out.sort(key=lambda x: (x["loss_date"], x["symbol"] or ""))
    return out


def deferred_into_next_year(book: Book) -> List[Dict[str, Any]]:
    out = []
    for h in book.inventory:
        dw = float(h.get("deferred_wash") or 0)
        if dw > 0.005 and book.wanted(h["_acct"]):
            out.append({"account": h["_acct"], "symbol": h.get("symbol"),
                        "qty": float(h.get("qty") or 0),
                        "deferred": round(dw, 2),
                        "why": (f"{dw:,.2f} of denied losses sits in the cost "
                                f"of the {float(h.get('qty') or 0):g} units "
                                f"still held at the end of {book.year}; it "
                                f"reduces the gain (or grows the loss) of the "
                                f"year they are sold")})
    out.sort(key=lambda x: -x["deferred"])
    return out


# --------------------------------------------------------- window edges

def _held_at(book: Book, sym: str, when: date) -> float:
    """Units of `sym` held across every account at the end of `when` (a
    window date): openings and SPLIT ratios applied, and a position
    renamed into `sym` by a SPLIT counted under it (A2-0388/0389)."""
    names = {sym}
    grew = True
    while grew:                     # every symbol renamed into `sym`
        grew = False
        for r in book.txs:
            if (r.get("action") == "SPLIT"
                    and str(r.get("symbol_new") or "").strip() in names
                    and (r.get("symbol") or "") not in names):
                names.add(r.get("symbol") or "")
                grew = True
    rows = [r for r in book.txs if (r.get("symbol") or "") in names
            and book.in_wash_scope(r["_acct"])]
    pos = _walk(rows, book.wdate, until=when)
    return sum(v for (_a, s_), v in pos.items() if s_ == sym)


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
    else:
        where = "INSIDE" if it["inside"] else "OUTSIDE"
        txt = (f"{it['kind']} of {it['qty']:g} in {it['account']}{reg} on "
               f"{it['date']}: day {abs(it['day'])} {side} the loss -> "
               f"{where} the window")
        if (it["inside"] and it["day"] < 0 and held_end <= 1e-9
                and not book.usa):
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
        for r in by_sym.get(sym, []):
            d, od = book.wdate(r), book.wother(r)
            if not d:
                continue
            off = (d - ld).days
            q = float(r.get("quantity") or 0)
            alt = (od - lo_).days if od and lo_ else None
            base = {"account": r["_acct"], "date": str(d), "qty": q,
                    "day": off, "other_basis_day": alt,
                    "sheltered": book.kind.get(r["_acct"]) != "taxable"}
            if q > 0 and lo_edge <= abs(off) <= hi_edge:
                items.append(dict(base, kind="acquisition",
                                  inside=abs(off) <= WINDOW))
            elif q < 0 and lo_edge <= off <= hi_edge and not book.usa:
                items.append(dict(base, kind="sale", inside=off <= WINDOW))
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
        held_end = _held_at(book, sym, end)
        # Units held on day 30 beyond those bought after the loss: only
        # these can be a pre-loss acquisition still on hand.
        bought_after = sum(float(r.get("quantity") or 0)
                           for r in by_sym.get(sym, [])
                           if float(r.get("quantity") or 0) > 0
                           and (d := book.wdate(r)) and ld < d <= end)
        pre_held = held_end - bought_after
        items = _group(items)
        for it in items:
            it["why"] = _describe(book, it,
                                  pre_held if it["day"] < 0 else held_end)
        out.append({"account": g["_acct"], "symbol": sym,
                    "loss_date": str(ld),
                    "qty": float(g.get("qty") or 0),
                    "raw_loss": round(float(g.get("raw_gain") or 0), 2),
                    "held_at_day30": round(held_end, 6),
                    "verdict": _verdict(g), "items": items})
    out.sort(key=lambda x: (x["loss_date"], x["symbol"]))
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
            held = _held_at(book, osym, end)
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
    if not book.usa:
        import sys as _sys
        from taxjson.lib.option_boundary import filed_locks
        filed, filed_timing = filed_locks(
            root, settings,
            warn=lambda m: emit_line(f"taxjson edge-cases: warning: {m}",
                                     file=_sys.stderr))
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
                            tax_date=book.basis):
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
                width_: Optional[int] = None) -> List[str]:
    """The report in the house layout (docs/output-style.md): a title,
    one section per kind of edge — a short upper-case heading with its
    count, the rule it turns on as a wrapped line, then a table that fits
    the width with each row's why indented under it — and the counting
    notes at the end."""
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

    def section(title, rows, headers, cells, empty="None.", note=None,
                details=lambda r: [r["why"]] if r.get("why") else []):
        d.section(f"{title.upper()} ({len(rows)})")
        if note:
            d.para(note, "  ")
        if not rows:
            d.para(empty, "  ")
            return
        table(headers, rows, cells, details)

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
        section("Written options across a year end", yb["written_options"],
                ["ACCOUNT", "SYMBOL", "WRITTEN", "PREMIUM", "CLOSED"],
                lambda r: (r["account"], r["symbol"], r["written"],
                           _money(r["premium"]), r["closed"] or "open"),
                empty="None open across Dec 31.",
                details=lambda r: [r["where"]] if r.get("where") else [])
    section("Options expiring at a year end", yb["option_expiries"],
            ["ACCOUNT", "SYMBOL", "HELD", "EXPIRES", "LANDS IN"],
            lambda r: (r["account"], r["symbol"], f"{r['held']:+g}",
                       r["expiry"], r["lands_in"]))
    section(f"{rule} windows that span Dec 31", yb["loss_windows"],
            ["ACCOUNT", "SYMBOL", "LOSS", "ON", "DENIED"],
            lambda r: (r["account"], r["symbol"], _money(r["raw_loss"]),
                       r["loss_date"], _money(r["denied"])))
    section(f"Denied losses carried in positions held at the end of {y}",
            yb["deferred_at_year_end"],
            ["ACCOUNT", "SYMBOL", "UNITS", "DEFERRED"],
            lambda r: (r["account"], r["symbol"], f"{r['qty']:g}",
                       _money(r["deferred"])))
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
                  f"of day 30."), details=items)
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
            details=items)
    rl = doc.get("renamed_late") or []
    section("Trades in an old ticker after its rename", rl,
            ["ACCOUNT", "SYMBOL", "QTY", "DATE", "RENAMED TO", "ON"],
            lambda r: (r["account"], str(r["symbol"]), f"{r['qty']:+g}",
                       r["date"], r["renamed_to"], r["rename_date"]),
            note=("A separate security unless ticker.map folds them "
                  "(`taxjson renames`)."),
            details=lambda r: [str(r["resolution"])])
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
               f"(run `taxjson run`).")
    d.para("Whether last year's closing positions, January settlements "
           "and corrections are carried into this year exactly once: "
           "`taxjson handoff`.")
    return d.lines()
