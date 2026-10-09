"""`taxjson check-dates`: every trade and settlement date the parsers
produced, checked against the calendar of what was traded.

Trading hours by asset class (times as the broker reports them, Eastern
for IB and the crypto exchanges once converted):

- Crypto trades around the clock, every day. It settles the day it trades.
- Futures and futures options trade 23 hours a day, Sunday evening to
  Friday afternoon (CME Globex). No Saturday session.
- US-listed stocks and ETFs trade on NYSE days, plus the overnight
  session (Sunday to Thursday nights, 20:00 onward). No Friday-night or
  Saturday session.
- Equity and index options trade on exchange days only.
- Canadian listings (.TO .V .CN .NE) trade on TSX days only.

Settlement (stocks and options): never before the trade, never on a day
the market cannot settle, and normally the standard cycle (T+1 from
May 2024, T+2 from Sept 2017, T+3 before; options T+1) counted in
settlement days. A broker that prints its own settlement date may
differ from the standard cycle; that is reported as a note, not an
error. A .tt line carries one date, used as both trade and settlement
date, so only the settlement checks apply to it.

Severities: ERROR (a date that cannot be right: a Saturday stock trade,
a settlement before the trade, a date in the future), WARN (unusual and
worth a look: a trade on an exchange holiday, a settlement on a holiday
of both markets), NOTE (a broker's cycle that differs from the
standard one, an income date on a weekend).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from taxjson.lib import market_calendar as mc
from taxjson.lib.dates import settlement_date

TRADE_ACTIONS = ("BUYSELL", "ASSIGN")

from taxjson.lib.core import is_opening_row  # noqa: E402
INCOME_ACTIONS = ("DIVIDEND", "DIVIDEND_IN_LIEU", "INTEREST", "TAX",
                  "ROC", "PIL")
from taxjson.lib.dates import CA_LISTING_SUFFIXES as CA_SUFFIXES  # noqa: E402
# Corporate events a parser books as a trade (a spin-off leg, a stock
# dividend, cash in lieu, a dividend reinvestment): dated the event or
# payment day, no settlement cycle.
_EVENT_RE = re.compile(r"SPIN|STK DIV|STOCK DIV|IN LIEU|REORG|MERGER|"
                       r"TENDER|REDEMPTION|RETRACTION|CONSOLIDAT|SPLIT|"
                       r"REINV|DRIP|DIVIDEND REINVEST",
                       re.IGNORECASE)
FUTURES_OPEN = dtime(17, 0)       # Sunday evening open (Eastern, lenient)
FUTURES_CLOSE = dtime(17, 0)      # Friday afternoon close
OVERNIGHT_OPEN = dtime(20, 0)     # US equities overnight session
# Cboe Global Trading Hours: from 20:15 Eastern, Sunday to Thursday;
# the session trades on the next trading day. Which roots have it is
# market data (lib/markets.is_evening_session_root).
GTH_OPEN = dtime(20, 15)


def _is_gth_root(root: str) -> bool:
    from taxjson.lib.markets import is_evening_session_root
    return is_evening_session_root(root, note=False)


def _option_root(symbol: str) -> str:
    m = re.match(r"([A-Z]+)", (symbol or "").upper())
    return m.group(1) if m else ""


def _d(s: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _t(s: Any) -> Optional[dtime]:
    try:
        return datetime.strptime(str(s), "%H:%M:%S").time()
    except (TypeError, ValueError):
        return None


def _rows(path: Path) -> List[Dict[str, Any]]:
    """The rows of one parsed file ([] when it does not exist). A file
    that exists but cannot be read raises ValueError naming it: dropping
    it silently turned an ERROR into a clean exit 0 (audit A2-0385)."""
    from taxjson.lib.json_input import InputFileError, read_work_doc
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return []
    # The shared work/ reader: a wrong-shape document, a row that is not
    # an object or a text price is one line naming the file, not a
    # traceback (re-audit A2-0794).
    try:
        doc = read_work_doc(path)
    except InputFileError as e:
        raise ValueError(f"{e} — re-run `taxjson run`") from None
    return list(doc.get("transactions") or [])


def sources(cache: Path, account: str) -> List[Tuple[str, str, Path]]:
    """(kind, label, parsed file) for every input of one account, from
    its sources list: broker exports, .tt files, corporate actions."""
    out: List[Tuple[str, str, Path]] = []
    lst = cache / f"{account}_sources.list"
    try:
        lines = lst.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # unreadable reads as absent: 'run `taxjson run` first', which
        # rewrites it (re-audit A2-0795)
        return out
    brokers = []
    for ln in lines:
        kind, _, name = ln.partition("/")
        if kind in ("setting", "map") or not name:
            continue
        if kind == "tt":
            from taxjson.lib.pipeline import tt_json_path
            out.append(("tt", name, tt_json_path(cache, account, name)))
        elif kind not in brokers:
            brokers.append(kind)
    for b in brokers:
        out.append(("broker", b, cache / f"{account}_{b}.json"))
        corp = cache / f"{account}_{b}_corp.json"
        if corp.exists():
            out.append(("corp", f"{b} corporate actions", corp))
    return out


def asset_class(symbol: str, crypto_account: bool) -> str:
    from taxjson.lib.core import is_option_symbol
    from taxjson.lib.futures import _FUTURES_PREFIXES
    s = (symbol or "").upper()
    if crypto_account:
        return "crypto"
    # Every futures spelling lib/futures reads (F:, /, \): a /ESH5 row
    # was classed a US stock and checked on the NYSE calendar (A2-1194).
    if s.startswith(_FUTURES_PREFIXES):
        return "futures"
    if is_option_symbol(s):
        return "option"
    if s.endswith(CA_SUFFIXES):
        return "ca-equity"
    if s.endswith(".US"):
        return "us-equity"
    return "other-equity"


def _market(cls: str, symbol: str, currency: str) -> Optional[str]:
    if cls in ("ca-equity",):
        return "CAD"
    if cls == "us-equity":
        return "USD"
    if cls == "option":
        return "CAD" if (symbol or "").upper().endswith(CA_SUFFIXES) \
            else "USD"
    return (currency or "").upper() or None


def check_trade_time(cls: str, d: date, t: Optional[dtime], symbol: str,
                     currency: str) -> Optional[Tuple[str, str, str]]:
    """(severity, code, message) when the trade date/time is unexpected."""
    wd = d.weekday()               # Mon=0 .. Sun=6
    if cls == "crypto":
        return None
    if cls == "futures":
        if wd == 5:
            return ("ERROR", "futures-saturday",
                    "futures do not trade on Saturday")
        if wd == 6 and t is not None and t < FUTURES_OPEN:
            return ("ERROR", "futures-sunday-early",
                    "futures open Sunday evening (about 18:00 Eastern)")
        if wd == 4 and t is not None and t > dtime(18, 0):
            return ("WARN", "futures-friday-late",
                    "after the Friday futures close")
        from datetime import timedelta
        cme_closed = {date(d.year, 12, 25), date(d.year, 1, 1),
                      mc._easter(d.year) - timedelta(days=2)}
        # Globex reopens at 18:00 Eastern on Christmas and New Year's Day
        # evenings for the next trade date: those fills are normal
        # (A2-1191). Good Friday has no evening session.
        evening = (t is not None and t >= dtime(18, 0)
                   and (d.month, d.day) in ((12, 25), (1, 1)))
        if d in cme_closed and not evening:
            return ("WARN", "futures-holiday",
                    "CME is closed on Christmas, New Year's Day and "
                    "Good Friday")
        return None
    if cls == "us-equity":
        overnight = t is not None and t >= OVERNIGHT_OPEN
        if wd == 5:
            return ("ERROR", "weekend-trade",
                    "US stocks do not trade on Saturday")
        if wd == 6:
            if overnight:
                return None        # Sunday-night overnight session
            return ("ERROR", "weekend-trade",
                    "US stocks trade on Sunday only in the overnight "
                    "session (20:00 onward)")
        if wd == 4 and overnight:
            return ("ERROR", "friday-night-trade",
                    "there is no Friday-night overnight session")
        if not mc.is_trading_day(d, "USD") and not overnight:
            return ("WARN", "holiday-trade",
                    "the NYSE is closed that day")
        return None
    if cls in ("option", "ca-equity"):
        mkt = _market(cls, symbol, currency)
        if (cls == "option" and wd == 6 and t is not None and t >= GTH_OPEN
                and _is_gth_root(_option_root(symbol))):
            return ("NOTE", "gth-clock-date",
                    "a Cboe Global Trading Hours fill (Sunday evening): "
                    "its trade date is Monday")
        if wd >= 5:
            return ("ERROR", "weekend-trade",
                    f"{'options' if cls == 'option' else 'Canadian listings'}"
                    f" do not trade on weekends")
        if mkt in ("USD", "CAD") and not mc.is_trading_day(d, mkt):
            return ("WARN", "holiday-trade",
                    f"the {'NYSE' if mkt == 'USD' else 'TSX'} is closed "
                    f"that day")
        return None
    if wd >= 5:
        return ("ERROR", "weekend-trade", "exchanges are closed on weekends")
    return None


def _tt_settle_horizon(today: date, cls: str, symbol: str,
                       currency: str) -> date:
    """The latest date a .tt line written today may carry: the
    settlement date of a trade made today (crypto and futures: today)."""
    if cls in ("crypto", "futures"):
        return today
    mkt = _market(cls, symbol, currency) or "USD"
    return _d(settlement_date(today.isoformat(), mkt,
                              cls == "option")) or today


def _settle_day_anywhere(d: date) -> bool:
    return mc.is_settlement_day(d, "USD") or mc.is_settlement_day(d, "CAD")


def analyze(root: Path, cfg: Dict[str, Any], *, today: Optional[date] = None,
            account: Optional[str] = None) -> Dict[str, Any]:
    from taxjson.lib.core import is_option_symbol
    cache = Path(root) / "work"
    today = today or date.today()
    settings = cfg.get("settings", {}) or {}
    year = int(settings.get("year") or today.year)
    # The one validator `run` and tax-logic use: a typo is refused, not
    # read as next_day (audit A2-0697).
    from taxjson.lib.country import futures_settle_mode
    futures_settle = futures_settle_mode(settings)
    issues: List[Dict[str, Any]] = []
    checked = 0
    # With exports shared by every year (`inputs_dir`), rows of later
    # years are expected here: counted (one Info line), not errors.
    from taxjson.lib.project_layout import shared_inputs
    shared = shared_inputs(root)
    later = 0
    per_source: Dict[str, int] = {}

    def add(sev, code, msg, acct, label, r, detail=""):
        issues.append({"severity": sev, "code": code, "message": msg,
                       "detail": detail,
                       "account": acct, "source": label,
                       "symbol": r.get("symbol"), "action": r.get("action"),
                       "date": r.get("date"), "time": r.get("time"),
                       "date_settle": r.get("date_settle"),
                       "quantity": r.get("quantity")})

    for acct, acfg in sorted((cfg.get("accounts") or {}).items()):
        if account and acct != account:
            continue
        crypto = bool((acfg or {}).get("crypto"))
        for kind, label, path in sources(cache, acct):
            try:
                rows = _rows(path)
            except ValueError as e:
                per_source[f"{acct}: {label}"] = 0
                add("ERROR", "unreadable", str(e), acct, label,
                    {"symbol": "-", "action": "-", "date": "-"})
                continue
            per_source[f"{acct}: {label}"] = len(rows)
            for r in rows:
                action = r.get("action")
                td, sd = _d(r.get("date")), _d(r.get("date_settle"))
                checked += 1
                if td is None:
                    add("ERROR", "bad-date", "trade date does not parse",
                        acct, label, r)
                    continue
                if shared and td.year > year + 1:
                    later += 1
                if td.year < 1990 or (td.year > year + 1 and not shared):
                    # One error per bad date: a far-future row was also
                    # counted as dated after today (A2-1192).
                    add("ERROR", "out-of-range",
                        f"far outside the project year {year}",
                        acct, label, r)
                elif td > today and not (
                        kind == "tt" and td <= _tt_settle_horizon(
                            today, asset_class(r.get("symbol") or "",
                                               crypto),
                            r.get("symbol") or "",
                            (r.get("currency") or "").upper())):
                    # A .tt line carries the SETTLEMENT date: a trade
                    # made today is written with a date up to one
                    # settlement cycle ahead (A2-1193).
                    add("ERROR", "future-date", "dated after today",
                        acct, label, r)
                if action in INCOME_ACTIONS:
                    if td.weekday() >= 5 and not crypto:
                        add("NOTE", "income-weekend",
                            "income dated on a weekend (pay dates are "
                            "normally business days)", acct, label, r,
                            f"{td.strftime('%A')}; "
                            f"{float(r.get('net_amount') or 0):,.2f} "
                            f"{r.get('currency') or ''}".strip())
                    continue
                if action not in TRADE_ACTIONS:
                    continue
                if is_opening_row(r):
                    # An opening balance is a positions report's date
                    # (often a month's last day, a weekend), not a
                    # trade (CA-OPEN-01 / US-OPEN-01).
                    continue
                sym = r.get("symbol") or ""
                cls = asset_class(sym, crypto)
                cur = (r.get("currency") or "").upper()
                # A .tt line has no trade time, and a corporate action is
                # an event the broker stamps, not an execution.
                if kind not in ("tt", "corp"):
                    hit = check_trade_time(cls, td, _t(r.get("time")), sym,
                                           cur)
                    if hit:
                        add(hit[0], hit[1], hit[2], acct, label, r)
                # --- settlement
                if cls == "crypto":
                    if sd and sd != td:
                        add("NOTE", "crypto-settle",
                            "crypto settles the day it trades",
                            acct, label, r)
                    continue
                if sd is None:
                    add("ERROR", "no-settle", "no settlement date",
                        acct, label, r)
                    continue
                if sd < td:
                    add("ERROR", "settle-before-trade",
                        "settles before it trades", acct, label, r)
                    continue
                if cls == "futures":
                    if futures_settle == "trade" and sd != td \
                            and kind == "broker":
                        add("NOTE", "futures-settle",
                            "futures settle on the trade date "
                            "(futures_settle = \"trade\")", acct, label, r)
                    if sd.weekday() == 5 or (sd.weekday() == 6
                                             and sd != td):
                        add("ERROR", "settle-weekend",
                            "settles on a weekend", acct, label, r)
                    elif sd.weekday() == 6:
                        # A Sunday-evening Globex fill dated its clock
                        # day: CME's trade date is Monday (A2-1191).
                        add("NOTE", "futures-sunday-date",
                            "a Sunday-evening Globex fill dated (and "
                            "settled) on its clock day; CME's trade date "
                            "is the Monday", acct, label, r)
                    continue
                if is_option_symbol(sym):
                    from taxjson.lib.core import parse_option_expiry
                    _exp = _d(parse_option_expiry(sym))
                    if (_exp and sd > _exp and td <= _exp
                            and abs(float(r.get("price") or 0)) < 1e-9):
                        # An expiry/zero-price close settling after the
                        # contract expired: on a settle basis its gain or
                        # loss moves to the settle year (A2-0386).
                        add("WARN", "settle-after-expiry",
                            "an option expiry (price 0) settles after the "
                            "contract's expiry date", acct, label, r,
                            f"expiry {_exp}; on tax_date = \"settle\" "
                            f"it lands in {sd.year}" if sd.year != _exp.year
                            else f"expiry {_exp}")
                expiry_like = (sd == td and is_option_symbol(sym)
                               and abs(float(r.get("price") or 0)) < 1e-9)
                if action == "ASSIGN" or expiry_like:
                    continue       # dated its exercise/expiry day
                if sd.weekday() >= 5:
                    add("ERROR", "settle-weekend",
                        "settles on a weekend" + (
                            " (a .tt line's date is its settlement date)"
                            if kind == "tt" else ""), acct, label, r)
                    continue
                if not _settle_day_anywhere(sd):
                    _mk = _market(cls, sym, cur) or "USD"
                    _nx = settlement_date(sd.isoformat(), _mk,
                                          cls == "option")
                    add("WARN", "settle-holiday",
                        "settles on a day neither the US nor the Canadian "
                        "market settles", acct, label, r,
                        f"{sd.strftime('%A')}"
                        + (f"; a .tt date is the settlement date: a trade "
                           f"on {sd} settles {_nx}" if kind == "tt" else
                           f"; next settlement day {_nx}"))
                if kind in ("tt", "corp") or cls == "other-equity":
                    continue
                if sd == td and (abs(float(r.get("price") or 0)) < 1e-9
                                 or _EVENT_RE.search(
                                     str(r.get("description") or ""))):
                    continue       # an event row, dated its event day
                if cls == "option":
                    from taxjson.lib.core import parse_option_expiry
                    if (parse_option_expiry(sym) or "") == sd.isoformat():
                        continue   # settlement capped at expiry
                mkt = _market(cls, sym, cur) or "USD"
                expect = _d(settlement_date(td.isoformat(), mkt,
                                            cls == "option"))
                # A Canadian dealer (RBC) dates US trades on the Canadian
                # calendar: the standard cycle on either calendar is fine.
                other = _d(settlement_date(td.isoformat(),
                                           "CAD" if mkt == "USD" else "USD",
                                           cls == "option"))
                if expect and sd not in (expect, other):
                    lag_days = (sd - td).days
                    if lag_days > 7:
                        add("WARN", "settle-late",
                            "settles more than a week after the trade",
                            acct, label, r,
                            f"{lag_days} days; standard cycle {expect}")
                    else:
                        add("NOTE", "settle-cycle",
                            "broker settle date differs from the standard "
                            "cycle", acct, label, r,
                            f"standard cycle {expect}")
    counts: Dict[str, Dict[str, int]] = {}
    for i in issues:
        counts.setdefault(i["severity"], {})
        counts[i["severity"]][i["code"]] = \
            counts[i["severity"]].get(i["code"], 0) + 1
    return {"year": year, "checked": checked, "sources": per_source,
            "issues": issues, "counts": counts, "later_years": later,
            "errors": sum(counts.get("ERROR", {}).values()),
            "warnings": sum(counts.get("WARN", {}).values())}


def render(doc: Dict[str, Any], show_all: bool = False,
           per_code: int = 10, width_: Optional[int] = None) -> List[str]:
    """The report in the house layout (docs/output-style.md): a title, a
    section per severity, each code with its count and meaning and its
    rows as a table that fits the width; the last line says what to do."""
    from taxjson.lib.out import Doc
    n_src = len(doc['sources'])
    d = Doc(f"CHECK DATES — {doc['checked']} rows from {n_src} "
            f"source{'' if n_src == 1 else 's'}", width_=width_)
    heads = {"ERROR": "ERRORS — impossible dates",
             "WARN": "WARNINGS — unusual dates to review",
             "NOTE": "NOTES — information"}
    for sev in ("ERROR", "WARN", "NOTE"):
        codes = doc["counts"].get(sev, {})
        if not codes:
            continue
        d.section(f"{heads[sev]} ({sum(codes.values())})")
        for k, (code, n) in enumerate(sorted(codes.items(),
                                             key=lambda kv: -kv[1])):
            rows = [i for i in doc["issues"]
                    if i["severity"] == sev and i["code"] == code]
            if k:
                d.blank()
            d.para(f"{code}: {n} — {rows[0]['message']}", "  ")
            shown = rows[: None if show_all else per_code]
            d.table(["ACCOUNT", "SOURCE", "SYMBOL", "ACTION", "DATE",
                     "TIME", "SETTLE", "DETAIL"],
                    [[i["account"], i["source"], str(i["symbol"]),
                      i["action"], i["date"], i["time"] or "",
                      i["date_settle"] or "-", i.get("detail") or ""]
                     for i in shown],
                    drop=(5, 1), key=(0, 2, 4), indent="    ")
            if not show_all and len(rows) > per_code:
                d.para(f"... {len(rows) - per_code} more (--all lists "
                       f"them)", "    ")
    if doc.get("later_years"):
        d.blank()
        d.para(f"Info: {doc['later_years']} row(s) dated after "
               f"{doc['year'] + 1}: later years' exports in the shared "
               f"inputs folder, expected (checked like the others; a "
               f"date after today is still an error).")
    d.blank()
    if not doc["issues"]:
        d.para("Every date lands where its market allows.")
    elif doc["errors"]:
        d.para(f"{doc['errors']} date(s) cannot be right; fix the "
               f"source file or the parser.")
    else:
        d.para("No impossible dates; review the warnings.")
    return d.lines()
