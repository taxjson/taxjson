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
   window's edge are listed with their exact day count on the engine's
   date basis, the count on the other basis, and the engine's verdict.

Everything is read from the run's work files, so the symbols are the ones
the engine pooled (ticker.map already applied): identical property is the
same symbol, plus a long call on the same shares (s.54 'a right to
acquire'), which the engine counts as replacement property for a share
loss.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

WINDOW = 30
ACQ_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER')
INCOME_ACTIONS = ('DIVIDEND', 'INTEREST', 'TAX', 'PIL', 'ROC')


def _d(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _rows(path: Path) -> List[Dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("transactions", []) if isinstance(doc, dict) else doc
    return [r for r in rows if isinstance(r, dict)]


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
        from taxjson.lib.country import settings_tax_date
        self.basis = settings_tax_date(settings)
        self.futures_settle = settings.get("futures_settle") or "trade"
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
        for name in sorted(accounts):
            p = self.cache / f"{name}_base.json"
            if not p.exists():
                self.missing.append(name)
                continue
            for r in _rows(p):
                r = dict(r)
                r["_acct"] = name
                self.txs.append(r)
        self.gains: List[Dict[str, Any]] = []
        self.inventory: List[Dict[str, Any]] = []
        resolved = resolve_gains_files(self.cache, None) or {}
        for acct, f in resolved.items():
            try:
                doc = json.loads(Path(f).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for t in doc.get("transactions", []):
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

    def bdate(self, r: Dict[str, Any]) -> Optional[date]:
        """The engine's date for a row: settlement or trade."""
        if self.basis == "settle":
            return _d(r.get("date_settle")) or _d(r.get("date"))
        return _d(r.get("date"))

    def other(self, r: Dict[str, Any]) -> Optional[date]:
        if self.basis == "settle":
            return _d(r.get("date"))
        return _d(r.get("date_settle")) or _d(r.get("date"))

    def wanted(self, acct: str) -> bool:
        return not self.only or acct == self.only


# ------------------------------------------------------------ year boundary

def _lands(book: Book, r: Dict[str, Any]) -> Optional[int]:
    d = book.bdate(r)
    return d.year if d else None


def year_straddles(book: Book) -> List[Dict[str, Any]]:
    """Trades whose trade date and settlement date fall in different
    years, around this project's two year ends."""
    y = book.year
    pre_pos: Dict[int, float] = {}
    run: Dict[Tuple[str, str], float] = {}
    for r in sorted((r for r in book.txs
                     if r.get("action") in ("BUYSELL", "ASSIGN", "TRANSFER")),
                    key=lambda r: (str(book.bdate(r) or ""), r.get("time") or "")):
        k = (r["_acct"], r.get("symbol") or "")
        pre_pos[id(r)] = run.get(k, 0.0)
        run[k] = run.get(k, 0.0) + float(r.get("quantity") or 0)
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
            why += (" (registered account: no tax effect, but it counts for "
                    "superficial-loss windows)")
        elif closing:
            why += (f": a disposition whose gain or loss is in {lands}'s "
                    f"Schedule 3"
                    + (f" ({float(g['gain']):+,.2f})" if g is not None else ""))
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
        fut = sym.startswith("F:")
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
            held = sum(float(r.get("quantity") or 0) for r in book.txs
                       if r["_acct"] == acct and r.get("symbol") == sym
                       and r.get("action") in ACQ_ACTIONS
                       and (_d(r.get("date")) or exp) < exp)
            if abs(held) < 1e-9:
                continue
            side = "long" if held > 0 else "written"
            why = (f"{side} {abs(held):g} expiring {exp}: an expiry is a "
                   f"disposition on the expiry date itself, so it lands in "
                   f"{exp.year}")
            if side == "written":
                why += (" (under grant timing the premium was already a gain"
                        " in the year written; see `taxjson option-boundary`)")
            out.append({"account": acct, "symbol": sym, "held": held,
                        "expiry": str(exp), "lands_in": exp.year, "why": why})
    return out


def income_near_new_year(book: Book, days: int = 5) -> List[Dict[str, Any]]:
    y = book.year
    out = []
    for r in book.txs:
        if r.get("action") not in INCOME_ACTIONS or not book.wanted(r["_acct"]):
            continue
        if book.kind.get(r["_acct"]) != "taxable" or r["_acct"] in book.crypto:
            continue
        if abs(float(r.get("net_amount") or 0)) < 1:
            continue
        d = _d(r.get("date"))
        if not d:
            continue
        for yy in (y - 1, y):
            ye = date(yy, 12, 31)
            if -days < (d - ye).days <= days:
                out.append({"account": r["_acct"], "symbol": r.get("symbol"),
                            "action": r.get("action"), "date": str(d),
                            "amount": round(float(r.get("net_amount") or 0), 2),
                            "lands_in": d.year,
                            "why": (f"income is taxed in the year it is PAID "
                                    f"(the date the broker books it, {d}), "
                                    f"not the record or ex-dividend date: "
                                    f"{d.year}")})
    out.sort(key=lambda x: (x["date"], x["symbol"] or ""))
    return out


def crypto_midnight(book: Book, hours: int = 5) -> List[Dict[str, Any]]:
    """Crypto rows within `hours` of midnight at a year end (local time;
    the exchanges' CSVs are in UTC, so the UTC date can differ)."""
    y = book.year
    out = []
    for r in book.txs:
        if r["_acct"] not in book.crypto or not book.wanted(r["_acct"]):
            continue
        try:
            ts = datetime.strptime(f"{r.get('date')} {r.get('time') or '00:00:00'}",
                                   "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        for yy in (y - 1, y):
            mid = datetime(yy + 1, 1, 1)
            if abs((ts - mid).total_seconds()) <= hours * 3600:
                out.append({"account": r["_acct"], "symbol": r.get("symbol"),
                            "action": r.get("action"), "local": str(ts),
                            "qty": float(r.get("quantity") or 0),
                            "lands_in": ts.year,
                            "why": (f"booked at {ts} local time, in {ts.year}; "
                                    f"in UTC this is about {ts + timedelta(hours=5)}"
                                    f" (EST), which may be the other year on the "
                                    f"exchange's own statement")})
    out.sort(key=lambda x: x["local"])
    return out


def windows_across_year_end(book: Book) -> List[Dict[str, Any]]:
    """Losses whose 61-day window spans Dec 31, with where the denied
    amount goes."""
    y = book.year
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for r in book.txs:
        if r.get("action") in ACQ_ACTIONS and float(r.get("quantity") or 0) > 0:
            by_sym.setdefault(r.get("symbol") or "", []).append(r)
    out = []
    for g in book.gains:
        if float(g.get("raw_gain", g.get("gain") or 0) or 0) >= -0.005:
            continue
        if not book.wanted(g["_acct"]):
            continue
        ld = book.bdate(g)
        if not ld:
            continue
        for yy in (y - 1, y):
            ye = date(yy, 12, 31)
            lo, hi = ld - timedelta(days=WINDOW), ld + timedelta(days=WINDOW)
            if not (lo <= ye < hi):
                continue
            other_side = [r for r in by_sym.get(g.get("symbol") or "", [])
                          if (d := book.bdate(r)) and lo <= d <= hi
                          and (d.year != ld.year)]
            if not other_side:
                continue
            denied = float(g.get("disallowed_amount") or 0)
            perm = float(g.get("permanently_disallowed") or 0)
            acq = ", ".join(f"{r['_acct']} {book.bdate(r)} +{float(r.get('quantity') or 0):g}"
                            for r in other_side[:4])
            if denied > 0.005:
                verdict = (f"denied {denied:,.2f}"
                           + (f" ({perm:,.2f} permanently, registered "
                              f"replacement)" if perm > 0.005 else
                              f"; added to the replacement's cost, so it "
                              f"comes back when that lot is sold"))
            else:
                verdict = "allowed (the replacement was not still held on day 30, " \
                          "or it was sold before the window closed)"
            out.append({"account": g["_acct"], "symbol": g.get("symbol"),
                        "loss_date": str(ld),
                        "raw_loss": round(float(g.get("raw_gain") or 0), 2),
                        "denied": round(denied, 2), "permanent": round(perm, 2),
                        "other_year_acquisitions": acq,
                        "why": (f"a {ld.year} loss whose window runs "
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
    """Units of `sym` held across every account at the end of `when`."""
    return sum(float(r.get("quantity") or 0) for r in book.txs
               if r.get("symbol") == sym and r.get("action") in ACQ_ACTIONS
               and (d := book.bdate(r)) and d <= when)


def _losses(book: Book):
    for g in book.gains:
        if not book.wanted(g["_acct"]):
            continue
        if float(g.get("raw_gain", g.get("gain") or 0) or 0) >= -0.005:
            continue
        ld = book.bdate(g)
        if not ld or ld.year != book.year:
            continue
        yield g, ld, book.other(g)


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
    basis = "settlement" if book.basis == "settle" else "trade"
    other = "trade" if basis == "settlement" else "settlement"
    side = "before" if it["day"] < 0 else "after"
    reg = " (registered)" if it["sheltered"] else ""
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
        if it["inside"] and it["day"] < 0 and held_end <= 1e-9:
            txt += (" (but none of it was still held on day 30, so it is "
                    "not a replacement: it is the lot that was sold)")
    if it["other_basis_day"] is not None:
        txt += (f"; on the {other}-date basis it is day "
                f"{abs(it['other_basis_day'])}")
        if it["basis_flip"]:
            txt += " — THE DATE BASIS DECIDES THIS ONE"
    if it.get("option"):
        txt += (f". {it['option']} is a right to acquire the shares "
                f"(s.54 para (i)): replacement property if still held on "
                f"day 30")
    return txt


def window_edges(book: Book, margin: int = 3) -> List[Dict[str, Any]]:
    """For each taxable loss in the project year: acquisitions of the same
    security (any account) within `margin` days of either window edge, and
    sales within `margin` days of day 30 that decide 'still held'."""
    lo_edge, hi_edge = WINDOW - margin, WINDOW + margin
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    calls: Dict[str, List[Dict[str, Any]]] = {}
    for r in book.txs:
        if r.get("action") in ACQ_ACTIONS:
            sym = r.get("symbol") or ""
            by_sym.setdefault(sym, []).append(r)
            if (_right(sym) == "C" and r.get("action") == "BUYSELL"
                    and float(r.get("quantity") or 0) > 0):
                und = _underlying(sym)
                if und:
                    calls.setdefault(und, []).append(r)
    out = []
    for g, ld, lo_ in _losses(book):
        sym = g.get("symbol") or ""
        items = []
        for r in by_sym.get(sym, []):
            d, od = book.bdate(r), book.other(r)
            if not d:
                continue
            off = (d - ld).days
            q = float(r.get("quantity") or 0)
            alt = (od - lo_).days if od and lo_ else None
            base = {"account": r["_acct"], "date": str(d), "qty": q,
                    "day": off, "other_basis_day": alt,
                    "sheltered": book.kind.get(r["_acct"]) != "taxable"}
            if q > 0 and lo_edge <= abs(off) <= hi_edge:
                inside = abs(off) <= WINDOW
                items.append(dict(base, kind="acquisition", inside=inside,
                                  basis_flip=(alt is not None and
                                              (abs(alt) <= WINDOW) != inside)))
            elif q < 0 and lo_edge <= off <= hi_edge:
                inside = off <= WINDOW
                items.append(dict(base, kind="sale", inside=inside,
                                  basis_flip=(alt is not None and
                                              (alt <= WINDOW) != inside)))
        if not _is_option(sym):
            for r in calls.get(sym, []):
                d, od = book.bdate(r), book.other(r)
                if not d:
                    continue
                off = (d - ld).days
                if not lo_edge <= abs(off) <= hi_edge:
                    continue
                alt = (od - lo_).days if od and lo_ else None
                inside = abs(off) <= WINDOW
                items.append({"kind": "long call", "account": r["_acct"],
                              "sheltered": book.kind.get(r["_acct"]) != "taxable",
                              "date": str(d), "qty": float(r.get("quantity") or 0),
                              "day": off, "other_basis_day": alt,
                              "option": r.get("symbol"), "inside": inside,
                              "basis_flip": (alt is not None and
                                             (abs(alt) <= WINDOW) != inside)})
        if not items:
            continue
        end = ld + timedelta(days=WINDOW)
        held_end = _held_at(book, sym, end)
        # Units held on day 30 beyond those bought after the loss: only
        # these can be a pre-loss acquisition still on hand.
        bought_after = sum(float(r.get("quantity") or 0)
                           for r in by_sym.get(sym, [])
                           if float(r.get("quantity") or 0) > 0
                           and (d := book.bdate(r)) and ld < d <= end)
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
    calls: Dict[str, List[Dict[str, Any]]] = {}
    for r in book.txs:
        sym = r.get("symbol") or ""
        if (r.get("action") == "BUYSELL" and _right(sym) == "C"
                and float(r.get("quantity") or 0) > 0):
            und = _underlying(sym)
            if und:
                calls.setdefault(und, []).append(r)
    out = []
    for g, ld, lo_ in _losses(book):
        sym = g.get("symbol") or ""
        if _is_option(sym) or sym not in calls:
            continue
        end = ld + timedelta(days=WINDOW)
        items = []
        for r in calls[sym]:
            d, od = book.bdate(r), book.other(r)
            if not d or abs((d - ld).days) > WINDOW:
                continue
            osym = r.get("symbol")
            held = _held_at(book, osym, end)
            alt = (od - lo_).days if od and lo_ else None
            items.append({"kind": "long call", "account": r["_acct"],
                          "sheltered": book.kind.get(r["_acct"]) != "taxable",
                          "date": str(d), "qty": float(r.get("quantity") or 0),
                          "day": (d - ld).days, "inside": True,
                          "other_basis_day": alt, "option": osym,
                          "held_at_day30": held,
                          "basis_flip": (alt is not None and abs(alt) > WINDOW)})
        if not items:
            continue
        items = _group(items)
        for it in items:
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
    from taxjson.lib.option_boundary import straddling
    from taxjson.lib.pipeline import option_timing_from_settings
    from taxjson.lib.core import TaxTransaction
    book = Book(root, cfg, account)
    settings = cfg.get("settings", {}) or {}
    kw = option_timing_from_settings(settings) or {}
    timing = kw.get("option_premium_timing", "close")
    since = kw.get("option_grant_since")
    written = []
    for name in book.taxable:
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
        filed = set()
        for f in (root / "filed").glob("*.json"):
            try:
                filed.add(int(f.stem))
            except ValueError:
                pass
        for r in straddling(txs, book.year, timing, since, filed):
            r["account"] = r.get("account") or name
            written.append(r)
    return {
        "year": book.year, "basis": book.basis,
        "futures_settle": book.futures_settle, "margin": margin,
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
    }


def _money(x: Optional[float]) -> str:
    return "-" if x is None else f"{x:,.2f}"


def render_text(doc: Dict[str, Any], verbose: bool = False) -> List[str]:
    y, basis = doc["year"], doc["basis"]
    L: List[str] = []
    L.append(f"EDGE CASES — tax year {y}; date basis: {basis}"
             f"{' (settlement date, CRA)' if basis == 'settle' else ' (trade date)'}"
             f"; futures: {doc['futures_settle']} date")
    L.append("")
    yb = doc["year_boundary"]

    def section(title: str, rows: List[Dict[str, Any]], line, empty: str):
        L.append(f"== {title} ({len(rows)})")
        if not rows:
            L.append(f"   {empty}")
        for r in rows:
            L.append("   " + line(r))
            L.append("      " + r["why"]) if r.get("why") else None
        L.append("")

    section("Trades that settle in a different year than they trade",
            yb["straddles"],
            lambda r: (f"{r['account']:<8} {r['symbol']:<24} {r['qty']:>+12g}  "
                       f"trade {r['trade_date']}  settle {r['settle_date']}  "
                       f"-> {r['lands_in']}"),
            "None.")
    section("Dispositions in the last and first days of a year",
            yb["last_days"],
            lambda r: (f"{r['account']:<8} {r['symbol']:<24} {r['qty']:>12g}  "
                       f"{r['trade_date']} (settle {r['settle_date']})  gain "
                       f"{_money(r['gain'])}  -> {r['lands_in']}"),
            "None.")
    L.append(f"== Written options across a year end ({len(yb['written_options'])})")
    if not yb["written_options"]:
        L.append("   None open across Dec 31.")
    for r in yb["written_options"]:
        L.append(f"   {r['account']:<8} {r['symbol']:<24} written {r['written']}  "
                 f"premium {_money(r['premium'])}  closed {r['closed'] or 'open'}")
        L.append(f"      {r['where']}")
    L.append("")
    section("Options expiring at a year end", yb["option_expiries"],
            lambda r: (f"{r['account']:<8} {r['symbol']:<24} held {r['held']:+g}"
                       f"  expires {r['expiry']}  -> {r['lands_in']}"),
            "None.")
    section("Superficial-loss windows that span Dec 31", yb["loss_windows"],
            lambda r: (f"{r['account']:<8} {r['symbol']:<24} loss "
                       f"{_money(r['raw_loss'])} on {r['loss_date']}  denied "
                       f"{_money(r['denied'])}"),
            "None.")
    section(f"Denied losses carried in positions held at the end of {y}",
            yb["deferred_at_year_end"],
            lambda r: (f"{r['account']:<8} {r['symbol']:<24} {r['qty']:>12g} "
                       f"units  deferred {_money(r['deferred'])}"),
            "None.")
    section("Income paid around New Year", yb["income"],
            lambda r: (f"{r['account']:<8} {str(r['symbol']):<24} "
                       f"{r['action']:<9} {r['date']}  {_money(r['amount'])}"
                       f"  -> {r['lands_in']}"),
            "None.")
    section("Crypto near midnight at a year end", yb["crypto_midnight"],
            lambda r: (f"{r['account']:<8} {str(r['symbol']):<10} "
                       f"{r['action']:<8} {r['local']}  {r['qty']:+g}"
                       f"  -> {r['lands_in']}"),
            "None.")

    we = doc["window_edges"]
    L.append(f"== Superficial-loss window edges: {y} losses with activity "
             f"within {doc['margin']} days of day 30 ({len(we)})")
    if not we:
        L.append("   None.")
    for r in we:
        L.append(f"   {r['account']:<8} {r['symbol']:<24} loss "
                 f"{_money(r['raw_loss'])} on {r['loss_date']} "
                 f"({r['qty']:g} units): {r['verdict']}; held on day 30: "
                 f"{r['held_at_day30']:g}")
        for it in r["items"]:
            L.append(f"      - {it['why']}")
    L.append("")
    cw = doc.get("calls_in_windows") or []
    L.append(f"== Long calls bought inside a share loss's window ({len(cw)}): "
             f"a call is a right to acquire the shares (s.54), so one still "
             f"held on day 30 is replacement property")
    if not cw:
        L.append("   None.")
    for r in cw:
        L.append(f"   {r['account']:<8} {r['symbol']:<24} loss "
                 f"{_money(r['raw_loss'])} on {r['loss_date']} "
                 f"({r['qty']:g} units): {r['verdict']}")
        for it in r["items"]:
            L.append(f"      - {it['why']}")
    L.append("")
    flips = sum(1 for r in we for it in r["items"] if it.get("basis_flip"))
    L.append("Day counts use the engine's date basis. The window is the 30 days "
             "before and after the loss, and the replacement must still be "
             "held at the end of day 30 (ITA s.54).")
    if flips:
        L.append(f"{flips} item(s) are marked THE BASIS DECIDES THIS ONE: the "
                 f"window verdict would differ on the other date basis.")
    if doc.get("missing_books"):
        L.append(f"No work files for: {', '.join(doc['missing_books'])} "
                 f"(run `taxjson run`).")
    L.append("Whether last year's closing positions, January settlements "
             "and corrections are carried into this year exactly once: "
             "`taxjson handoff`.")
    return L
