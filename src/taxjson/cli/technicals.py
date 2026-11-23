"""`tjs technicals`: the holdings' technical statistics from their daily
closes. The holdings are `tjs shares`' (cli/positions `held_by_symbol`),
the history lib/price_chain `daily_history`'s (Yahoo Finance, the [fx]
extra, cached in work/.history_cache.json), the numbers lib/technicals'.
Market statistics, not tax figures: nothing in the books is read but the
positions, and nothing is written but the cache."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib import out as _out
from taxjson.lib import technicals as T
from taxjson.model import money as M
from taxjson.cli.common import _warn_accounts_without_books
from taxjson.cli.positions import _books_horizon, held_by_symbol
from taxjson.pipeline.config import load_settings
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.sanity import _json_out

PROG = "tjs technicals"
HISTORY_CACHE = ".history_cache.json"
DETAILS = "tjs technicals --details"
# Offline (TAXJSON_OFFLINE, or no [fx] extra) a row served from a cache
# entry fetched more than this many calendar days ago gets an `! ` line
# (about 5 sessions); after the live tier gave no answer, any older entry
# does.
OLD_CACHE_DAYS = 7
# The kinds of position left out, in the order they are counted.
LEFT_OUT = ("options", "futures", "crypto")
_LEFT_WORDS = {"options": ("option contract", "option contracts"),
               "futures": ("futures contract", "futures contracts"),
               "crypto": ("crypto", "crypto")}


def _count(n: int, words: Tuple[str, str]) -> str:
    return f"{n} {words[0] if n == 1 else words[1]}"

# The default table's statistics and the --details table's, by
# lib/technicals key: (header, decimals, signed).
MAIN_STATS = (("sma20_pct", "SMA20", 1, True), ("sma50_pct", "SMA50", 1, True),
              ("sma200_pct", "SMA200", 1, True), ("rsi14", "RSI", 1, False),
              ("beta", "BETA", 2, False), ("alpha_pct", "ALPHA", 1, True))
MORE_STATS = (("from_high_pct", "HIGH52", 1, True),
              ("from_low_pct", "LOW52", 1, True),
              ("volatility_pct", "VOL", 1, False),
              ("return_1m_pct", "1M", 1, True), ("return_3m_pct", "3M", 1, True),
              ("return_1y_pct", "1Y", 1, True),
              ("max_drawdown_pct", "MAXDD", 1, True),
              ("atr14_pct", "ATR14", 2, False))

# The default view's first legend line.
LEGEND = ("SMA: % from the 20/50/200-close mean. RSI(14), Wilder. Blank: "
          "too little history, or not computable.")

# One line a statistic (--details and docs/commands.md say the same).
DEFINITIONS = (
    "LAST: the last completed session's close, adjusted for splits and "
    "dividends (Yahoo's adjusted series), in the listing's currency; DATE: "
    "its session.",
    "SMA20, SMA50, SMA200: (LAST / the mean of the last 20, 50 or 200 closes "
    "- 1) x 100.",
    "RSI: RSI(14), Wilder's smoothing over the whole series (seeded with the "
    "mean of the first 14 changes); 100 when no close fell.",
    f"BETA: cov / var of the daily simple returns of the holding and the "
    f"benchmark on the dates both have, the last {T.YEAR}.",
    f"ALPHA: Jensen's alpha with a zero risk-free rate, (mean holding return "
    f"- BETA x mean benchmark return) x {T.YEAR} x 100, % a year.",
    f"HIGH52, LOW52: % from the highest and the lowest close of the last "
    f"{T.YEAR + 1} closes ({T.YEAR} returns).",
    f"VOL: the sample standard deviation of the last {T.YEAR} daily returns "
    f"x sqrt({T.YEAR}) x 100 (annualised volatility).",
    f"1M, 3M, 1Y: (LAST / the close {T.MONTH}, {T.QUARTER} or {T.YEAR} "
    f"sessions before - 1) x 100.",
    f"MAXDD: the deepest fall from a running peak close over the last "
    f"{T.YEAR + 1} closes ({T.YEAR} returns), in % (0 or below).",
    "ATR14: Wilder's average true range over 14 sessions, as a % of LAST; "
    "true range = max(high - low, |high - previous close|, |low - previous "
    "close|).",
    "AVGVOL20: the mean volume of the last 20 sessions, in the listing's "
    "units.",
    f"Blank: too little history (each statistic needs its whole window; "
    f"BETA and ALPHA {T.YEAR} returns on dates the benchmark has too), or "
    f"not computable: RSI when no close moved, BETA and ALPHA when the "
    f"benchmark's returns do not vary, ATR14 without {T.ATR_PERIOD + 1} "
    f"sessions in a row with a high and a low, AVGVOL20 when a session of "
    f"the last {T.VOLUME_SESSIONS} has no volume. NOTES names each.",
)


def _holdings(files: Dict[str, Path]
              ) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, List[str]]]:
    """The positions the books hold at their end, one row a symbol (all
    accounts' quantity summed, as `tjs shares`), and those left out by
    kind: option and futures contracts (no daily history of their own
    here) and crypto (a market that trades every day: the windows count
    trading sessions)."""
    from taxjson.lib.option_symbols import is_option_symbol
    from taxjson.lib.price_chain import is_crypto_symbol
    from taxjson.lib.ticker_map import is_future_ticker
    held, _year = held_by_symbol(files, options=True, futures=True)
    rows: List[Tuple[str, Dict[str, Any]]] = []
    left: Dict[str, List[str]] = {k: [] for k in LEFT_OUT}
    for sym, e in sorted(held.items()):
        if abs(e["qty"]) <= 1e-9:
            continue
        kind = ("options" if is_option_symbol(sym)
                else "futures" if is_future_ticker(sym)
                else "crypto" if is_crypto_symbol(sym) else None)
        if kind:
            left[kind].append(sym)
        else:
            rows.append((sym, e))
    return rows, left


def _benchmark(args: argparse.Namespace, root: Path
               ) -> Optional[Tuple[str, str]]:
    """(the benchmark symbol, where it was given), or None: --benchmark,
    else [settings] benchmark."""
    given = getattr(args, "benchmark", None)
    if given is not None:
        sym, where = str(given), "--benchmark"
    else:
        sym, where = (load_settings(root, missing_ok=True).benchmark,
                      "[settings] benchmark")
        if sym is None:
            return None
    raw, sym = sym, sym.strip().upper()
    if not sym or any(c.isspace() for c in sym):
        _die_input(f"{where} {raw!r} is not one symbol",
                   "Write it as the books spell it (XYZQ.TO, XYZQ.US) or as "
                   "Yahoo Finance spells an index (^XYZQ).")
    return sym, where


def _spellings(root: Path, symbols: List[str],
               bench: Optional[Tuple[str, str]]
               ) -> Tuple[Dict[str, str], Dict[str, float], List[str]]:
    """({symbol: its Yahoo spelling}, {symbol: a QUOTE line's ratio other
    than 1}, the symbols with no spelling): the project's ticker.map QUOTE
    line, else the price chain's converter (lib/price_chain
    yf_symbol_for). The benchmark is spelled the same way, but a bare word
    is refused: taxjson reads one as a coin."""
    from taxjson.lib.price_chain import (is_crypto_symbol, load_yf_map,
                                         yf_symbol_for)
    quote = load_yf_map([str(root)])
    pairs: Dict[str, str] = {}
    ratios: Dict[str, float] = {}
    none: List[str] = []
    for sym in symbols:
        if sym in quote:
            pairs[sym] = quote[sym][0]
            if quote[sym][1] != 1:
                ratios[sym] = quote[sym][1]
            continue
        spelled = yf_symbol_for(sym)
        if spelled is None:
            none.append(sym)
        else:
            pairs[sym] = spelled
    if bench is not None and bench[0] not in pairs:
        sym, where = bench
        spelled = quote[sym][0] if sym in quote else (
            None if is_crypto_symbol(sym) else yf_symbol_for(sym))
        if spelled is None:
            _die_input(f"{where} {sym}: not a listing taxjson can spell for "
                       f"Yahoo Finance",
                       "Write it as the books spell a listing (XYZQ.TO, "
                       "XYZQ.US) or as Yahoo spells an index (^XYZQ), or add "
                       f"a ticker.map line QUOTE {sym} YAHOO_SYMBOL.")
        pairs[sym] = str(spelled)
    return pairs, ratios, none


def _refuse_no_history(missing: Dict[str, str]) -> None:
    """Exit 1: not one holding has a daily history (none cached, and the
    live tier is off, missing or answered nothing)."""
    from taxjson.lib import price_chain as PC
    from taxjson.lib.install_hint import extra_hint
    from taxjson.lib.offline import ENV_VAR
    why = set(missing.values())
    where = f"work/{HISTORY_CACHE}"
    if PC.HISTORY_OFFLINE in why:
        _die(f"{ENV_VAR} is set and {where} holds no daily history for "
             f"these holdings — nothing was fetched; unset it (or set "
             f"{ENV_VAR}=0) to fetch them from Yahoo Finance.")
    if PC.HISTORY_NO_EXTRA in why:
        _die(f"daily history needs Yahoo Finance (the [fx] extra), which is "
             f"not installed, and {where} holds none",
             extra_hint("fx"))
    _die("no daily history came back from Yahoo Finance for any holding",
         "Is the network up? A symbol Yahoo spells another way needs a "
         "ticker.map line QUOTE SYMBOL YAHOO_SYMBOL.")


def _stats_rows(rows, pairs, ratios, hist, missing, unspelled, bench_bars):
    """One record a holding: its quantity, history and statistics (all
    None without a history), and why each blank statistic is one."""
    from taxjson.lib.price_chain import HISTORY_NONE
    out = []
    for sym, e in rows:
        h = hist.get(sym)
        st = (T.series_stats(h.bars, bench_bars) if h
              else {k: None for k in T.MIN_SESSIONS})
        out.append({
            "symbol": sym, "qty": e["qty"], "accounts": e["accounts"],
            "quote_symbol": pairs.get(sym), "quote_ratio": ratios.get(sym, 1),
            "history": "ok" if h else ("no-spelling" if sym in unspelled
                                        else missing.get(sym, HISTORY_NONE)),
            "cache_reason": h.cache_reason if h else None,
            "currency": h.currency if h else None,
            "source": h.source if h else None,
            "fetched": h.fetched if h else None,
            "sessions": len(h.bars) if h else 0,
            "first_date": h.bars[0].day if h else None,
            "last_date": h.bars[-1].day if h else None,
            "last_close": h.bars[-1].close if h else None,
            "stats": st,
            "blank": T.blank_reasons(h.bars, st, bench_bars) if h else {},
            "paired_returns": (len(T.paired_returns(h.bars, bench_bars))
                               if h and bench_bars else None),
        })
    return out


def _num(v: Optional[float], places: int) -> Optional[float]:
    """A --json figure cut to `places`, never -0.0."""
    if v is None:
        return None
    x = M.units(v, places)
    return 0.0 if x == 0 else x


def _json_doc(recs, left, bench, bench_hist, bench_why, horizon, latest
              ) -> Dict[str, Any]:
    from taxjson.lib.price_chain import HISTORY_ADJUSTED
    return {
        "as_of": horizon, "adjusted": HISTORY_ADJUSTED,
        "latest_close_date": latest,
        "benchmark": None if bench is None else {
            "symbol": bench[0], "given_by": bench[1],
            "history": "ok" if bench_hist else bench_why,
            "cache_reason": bench_hist.cache_reason if bench_hist else None,
            "quote_symbol": bench_hist.listing if bench_hist else None,
            "currency": bench_hist.currency if bench_hist else None,
            "sessions": len(bench_hist.bars) if bench_hist else 0,
            "last_date": bench_hist.bars[-1].day if bench_hist else None,
            "fetched": bench_hist.fetched if bench_hist else None},
        "windows": {"sma": list(T.SMA_PERIODS), "rsi": T.RSI_PERIOD,
                    "atr": T.ATR_PERIOD, "volume": T.VOLUME_SESSIONS,
                    "month": T.MONTH, "quarter": T.QUARTER, "year": T.YEAR},
        "min_sessions": dict(T.MIN_SESSIONS),
        "left_out": {k: list(v) for k, v in left.items()},
        "rows": [{**{k: r[k] for k in (
            "symbol", "quote_symbol", "history", "cache_reason", "currency",
            "source", "fetched", "sessions", "first_date", "last_date",
            "paired_returns")},
            "qty": _num(r["qty"], 8),
            "accounts": {a: _num(q, 8)
                         for a, q in sorted(r["accounts"].items())},
            "quote_ratio": r["quote_ratio"],
            "last_close": (None if r["last_close"] is None
                           else M.json_money(r["last_close"], 6)),
            "stale": _older(r["last_date"], latest),
            "stats": {k: _num(v, 2 if k == "avg_volume_20" else 4)
                      for k, v in r["stats"].items()},
            "blank": {k: r["blank"][k] for k in T.MIN_SESSIONS
                      if k in r["blank"]}} for r in recs],
    }


def _cell(v: Optional[float], places: int, signed: bool) -> str:
    if v is None:
        return ""
    s = f"{v:+.{places}f}" if signed else f"{v:.{places}f}"
    # A -0.04 shown at one place is 0.0, not -0.0.
    return s[1:] if s.lstrip("+-").strip("0.") == "" and s[0] in "+-" else s


def _price(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{v:,.2f}" if v >= 1 else f"{v:.4f}"


def _sources(recs, bench_hist) -> str:
    """Where the closes came from, for the header: the live tier, the cache
    (with the day or days it was fetched), or both."""
    from taxjson.lib.price_chain import HISTORY_CACHE_SOURCE, HISTORY_LIVE_SOURCE
    hs = [(r["source"], r["fetched"]) for r in recs if r["source"]]
    if bench_hist is not None:
        hs.append((bench_hist.source, bench_hist.fetched))
    live = sorted({s for s, _f in hs if s != HISTORY_CACHE_SOURCE})
    cached = sorted({f for s, f in hs if s == HISTORY_CACHE_SOURCE})
    parts = [("Yahoo Finance" if s == HISTORY_LIVE_SOURCE else s)
             + f" ({max(f for t, f in hs if t == s)})" for s in live]
    if cached:
        parts.append("the history cache, fetched " + _dates(cached))
    return " and ".join(parts) or "none"


def _older(day: Optional[str], latest: Optional[str]) -> bool:
    """Whether a last close of `day` is older than the newest row's
    (`latest`): its DATE is marked `*` (a close of that very day is not)."""
    return bool(day and latest and day < latest)


def _dates(days) -> str:
    ds = sorted(set(days))
    return ds[0] if len(ds) == 1 else f"{ds[0]} to {ds[-1]}"


def _act_named(text: str, names: List[str], cmd: Optional[str]) -> str:
    """An `! ` line naming as many of `names` as fit, and always the first
    (out.act_list leaves them all out when not even one fits)."""
    line = _out.act_list(text, names, cmd)
    if names and line == _out.act(text, cmd):
        line = _out.act(f"{text} ({names[0]}"
                        + (f" +{len(names) - 1} more" if len(names) > 1
                           else "") + ")", cmd)
    return line


def _age(fetched: Optional[str], today: str) -> int:
    """Calendar days from a cache entry's fetch day to today."""
    return (date.fromisoformat(today) - date.fromisoformat(fetched)).days \
        if fetched else 0


def _series(n: int) -> str:
    return f"{n} series"


class _Fetch:
    """What the live tier did for the holdings and the benchmark (each
    record's `history` and `cache_reason`, the benchmark's history or why
    it has none): whether a symbol asked gave nothing, whether any gave
    something, and whether the benchmark is a row of its own."""

    def __init__(self, recs, bench, bench_hist, bench_why):
        from taxjson.lib import price_chain as PC
        self.bsym = bench[0] if bench else None
        self.bench_apart = bench is not None and self.bsym not in {
            r["symbol"] for r in recs}
        b_reason = (bench_hist.cache_reason if bench_hist is not None
                    else bench_why)
        self.bench_reason = b_reason if self.bench_apart else None
        asked_none = any(PC.HISTORY_NONE in (r["history"], r["cache_reason"])
                         for r in recs) or self.bench_reason == PC.HISTORY_NONE
        # Yahoo answered today: a series came back on this run, or one
        # the cache holds was fetched today.
        answered = any(r["history"] == "ok" and r["cache_reason"] in (
            None, PC.HISTORY_TODAY) for r in recs) or (
            self.bench_apart and bench_hist is not None
            and bench_hist.cache_reason in (None, PC.HISTORY_TODAY))
        # Every symbol asked gave nothing, and none came back today: the
        # network or Yahoo, not a spelling (no QUOTE advice).
        self.wholesale = asked_none and not answered


def _cause_act(doc, recs, f: _Fetch, bench_hist, why: str, label: str,
               cmd: str, today: str, old_days: Optional[int]) -> None:
    """The one `! ` line of one reason the live tier gave nothing (`why`):
    the holdings it left without a history (and the benchmark, but after
    no answer: that one has a line of its own) and the series it left on a
    cache entry older than `old_days` calendar days (None: any)."""
    from taxjson.lib.price_chain import HISTORY_NONE
    miss = [r["symbol"] for r in recs if r["history"] == why]
    if (f.bench_apart and bench_hist is None and f.bench_reason == why
            and why != HISTORY_NONE):
        miss.append(f"{f.bsym} (benchmark)")
    old = [r for r in recs if r["cache_reason"] == why
           and (old_days is None or _age(r["fetched"], today) > old_days)]
    b_old = (f.bench_apart and bench_hist is not None
             and f.bench_reason == why
             and (old_days is None or _age(bench_hist.fetched, today)
                  > old_days))
    days = [r["fetched"] for r in old] + ([bench_hist.fetched] if b_old
                                         else [])
    n_old = len(old) + int(b_old)
    if miss and days:
        doc.line(_act_named(f"{label}: {_series(n_old)} cached "
                            f"{_dates(days)}, {len(miss)} uncached", miss,
                            cmd))
    elif days:
        doc.line(_out.act_list(f"{label}: {_series(n_old)} from the history "
                               f"cache of {_dates(days)}",
                               [r["symbol"] for r in old]
                               + ([f.bsym] if b_old else []), cmd))
    elif miss:
        doc.line(_act_named(f"{_count(len(miss), ('symbol', 'symbols'))} "
                            f"not in the history cache, {label}", miss, cmd))


def _quote_acts(doc, recs, f: _Fetch, bench_hist) -> None:
    """The holdings and the benchmark Yahoo had nothing for, when other
    symbols did come back (a spelling: a ticker.map QUOTE line); a
    symbol with no spelling at all; the benchmark with no series at all
    on a line of its own (no QUOTE advice for an index or after no answer
    at all; one the cache served is on the cache's line, as a holding
    is: another spelling would lose its entry)."""
    from taxjson.lib import price_chain as PC
    spell = [r["symbol"] for r in recs if r["history"] == "no-spelling"
             or (r["history"] == PC.HISTORY_NONE and not f.wholesale)]
    if len(spell) == 1:
        doc.line(_out.act(f"no daily history for {spell[0]}",
                          f"add a ticker.map line: QUOTE {spell[0]} "
                          f"YAHOO_SYMBOL"))
    elif spell:
        doc.line(_act_named(f"no daily history for {len(spell)} symbols",
                            spell, "ticker.map QUOTE lines: " + DETAILS))
    if bench_hist is not None or f.bench_reason != PC.HISTORY_NONE \
            or not f.bsym:
        return
    b = f.bsym
    if f.wholesale:
        doc.line(_out.act(f"no daily history for the benchmark {b}: Yahoo "
                          f"gave no answer", "network down?"))
    elif b.startswith("^"):
        doc.line(_out.act(f"no daily history for the benchmark {b} from "
                          f"Yahoo Finance", "check the index's spelling there"))
    else:
        doc.line(_out.act(f"no daily history for the benchmark {b}",
                          f"add a ticker.map line: QUOTE {b} YAHOO_SYMBOL"))


def _history_acts(doc, recs, bench, bench_hist, bench_why, today) -> None:
    """The `! ` lines about where the closes came from, one line a reason:
    TAXJSON_OFFLINE or no [fx] extra (the symbols with no history, and
    the series served from a cache entry older than OLD_CACHE_DAYS); no
    answer from Yahoo (the series served from an older entry, and when
    nothing came back at all, the symbols with none); the spellings to
    add; the benchmark."""
    from taxjson.lib import price_chain as PC
    f = _Fetch(recs, bench, bench_hist, bench_why)
    _cause_act(doc, recs, f, bench_hist, PC.HISTORY_OFFLINE,
               "TAXJSON_OFFLINE set", "unset it to fetch", today,
               OLD_CACHE_DAYS)
    _cause_act(doc, recs, f, bench_hist, PC.HISTORY_NO_EXTRA,
               "no [fx] extra", DETAILS, today, OLD_CACHE_DAYS)
    if f.wholesale:
        _cause_act(doc, recs, f, bench_hist, PC.HISTORY_NONE,
                   "Yahoo gave no answer", "network down?", today, None)
    else:
        old = [r for r in recs if r["cache_reason"] == PC.HISTORY_NONE]
        b_old = bench_hist is not None and f.bench_reason == PC.HISTORY_NONE
        if old or b_old:
            days = [r["fetched"] for r in old] + ([bench_hist.fetched]
                                                 if b_old else [])
            doc.line(_out.act_list(
                f"Yahoo gave no answer: "
                f"{_series(len(old) + int(b_old))} from the cache of "
                f"{_dates(days)}", [r["symbol"] for r in old]
                + ([f.bsym] if b_old else []), DETAILS))
    _quote_acts(doc, recs, f, bench_hist)


def _benchmark_acts(doc, recs, bench_hist, latest) -> None:
    """The benchmark's `! ` lines: a last close older than the rows' (BETA
    and ALPHA end there), and rows in another currency (their BETA and
    ALPHA include the exchange rate's moves)."""
    if bench_hist is None:
        return
    bday = bench_hist.bars[-1].day
    if _older(bday, latest):
        doc.line(_out.act(f"BETA and ALPHA end on {bday}: the benchmark's "
                          f"last close is older than the rows' ({latest})"))
    cur = bench_hist.currency
    other = [r["symbol"] for r in recs
             if r["stats"]["beta"] is not None and cur and r["currency"]
             and r["currency"] != cur]
    if other:
        doc.line(_out.act_list(
            f"{_count(len(other), ('row', 'rows'))} not in {cur}, the "
            f"benchmark's currency: BETA and ALPHA include the exchange "
            f"rate's moves", other))


def _main_table(doc, recs, latest) -> None:
    body = []
    for r in recs:
        st = r["stats"]
        day = r["last_date"] or ""
        if _older(day, latest):
            day += "*"
        body.append([r["symbol"], _qty(r["qty"]), _price(r["last_close"]),
                     day] + [_cell(st[k], p, sg) for k, _h, p, sg in MAIN_STATS])
    doc.table(["SYMBOL", "QTY", "LAST", "DATE"] + [h for _k, h, _p, _s
                                                   in MAIN_STATS],
              body, aligns=["<", ">", ">", "<"] + [">"] * len(MAIN_STATS),
              drop=(1, 2))


def _qty(q: float) -> str:
    from taxjson.lib.report_fmt import fmt_qty
    return fmt_qty(q)


def _details_part(doc, recs, left, bench, bench_hist, ratios) -> None:
    """The long form: the other statistics, the history behind each row,
    why a statistic is blank, what is left out, and the definitions."""
    doc.section("OTHER STATISTICS")
    doc.legend("% from LAST or of it; VOL, 1M, 3M, 1Y and MAXDD in %; "
               "AVGVOL20 in units (DEFINITIONS below).")
    doc.table(["SYMBOL"] + [h for _k, h, _p, _s in MORE_STATS] + ["AVGVOL20"],
              [[r["symbol"]] + [_cell(r["stats"][k], p, sg)
                                for k, _h, p, sg in MORE_STATS]
               + ["" if r["stats"]["avg_volume_20"] is None
                  else f"{r['stats']['avg_volume_20']:,.0f}"] for r in recs],
              aligns=["<"] + [">"] * (len(MORE_STATS) + 1))
    doc.section("HISTORY")
    doc.legend("QUOTE: the Yahoo Finance spelling (a ticker.map QUOTE line, "
               "else taxjson's); SESSIONS: the daily closes read.")
    hist_rows = [[r["symbol"], r["quote_symbol"] or "", r["currency"] or "",
                  str(r["sessions"]), r["first_date"] or "",
                  r["last_date"] or "", r["source"] or r["history"],
                  r["fetched"] or ""] for r in recs]
    if bench is not None and bench[0] not in {r["symbol"] for r in recs}:
        hist_rows.append([f"{bench[0]} (benchmark)",
                          bench_hist.listing if bench_hist else "",
                          (bench_hist.currency or "") if bench_hist else "",
                          str(len(bench_hist.bars)) if bench_hist else "0",
                          bench_hist.bars[0].day if bench_hist else "",
                          bench_hist.bars[-1].day if bench_hist else "",
                          bench_hist.source if bench_hist else "none",
                          bench_hist.fetched if bench_hist else ""])
    doc.table(["SYMBOL", "QUOTE", "CUR", "SESSIONS", "FIRST", "LAST",
               "SOURCE", "FETCHED"], hist_rows,
              aligns=["<", "<", "<", ">", "<", "<", "<", "<"])
    doc.section("NOTES")
    _notes(doc, recs, left, bench, ratios)
    doc.section("DEFINITIONS")
    doc.items(DEFINITIONS)


# Why a statistic the data does not allow is blank (lib/technicals
# blank_reasons; TOO_SHORT and TOO_FEW_PAIRS are said with their counts).
_BLANK_WHY = {
    T.FLAT: "no close moved",
    T.FLAT_BENCHMARK: "the benchmark's returns do not vary",
    T.NO_HIGH_LOW: f"fewer than {T.ATR_PERIOD + 1} sessions in a row with a "
                   f"high and a low",
    T.NO_VOLUME: f"a session of the last {T.VOLUME_SESSIONS} has no volume",
}


def _blank_note(r) -> Optional[str]:
    """One holding's blanks and why (NOTES), or None: no blank but those
    of no benchmark."""
    heads = {k: h for k, h, _p, _s in MAIN_STATS + MORE_STATS}
    heads["avg_volume_20"] = "AVGVOL20"
    by: Dict[str, List[str]] = {}
    for k in T.MIN_SESSIONS:
        why = r["blank"].get(k)
        if why and why != T.NO_BENCHMARK:
            by.setdefault(why, []).append(heads[k])
    if not by:
        return None
    parts = []
    if T.TOO_SHORT in by:
        parts.append(f"{r['sessions']} closes, too few for "
                     f"{', '.join(by.pop(T.TOO_SHORT))}")
    if T.TOO_FEW_PAIRS in by:
        parts.append(f"{', '.join(by.pop(T.TOO_FEW_PAIRS))}: "
                     f"{r['paired_returns']} daily returns on dates the "
                     f"benchmark has too, {T.YEAR} needed")
    parts += [f"{', '.join(names)}: {_BLANK_WHY[why]}"
              for why, names in by.items()]
    return f"{r['symbol']}: blank: " + "; ".join(parts) + "."


def _notes(doc, recs, left, bench, ratios) -> None:
    from taxjson.lib.install_hint import extra_hint
    from taxjson.lib import price_chain as PC
    for r in recs:
        note = _blank_note(r) if r["sessions"] else None
        if note:
            doc.item(note)
    for r in recs:
        if r["history"] in ("no-spelling", PC.HISTORY_NONE):
            doc.item(f"{r['symbol']}: "
                     + (f"asked Yahoo Finance as {r['quote_symbol']}: no "
                        f"daily history came back" if r["quote_symbol"]
                        else "no Yahoo Finance spelling")
                     + f". A ticker.map line `QUOTE {r['symbol']} "
                     f"YAHOO_SYMBOL` names the one Yahoo uses; when no "
                     f"symbol came back, the network or Yahoo did not "
                     f"answer.")
        elif r["history"] == PC.HISTORY_NO_EXTRA:
            doc.item(f"{r['symbol']}: not in the history cache, and Yahoo "
                     f"Finance (the [fx] extra) is not installed: "
                     f"{extra_hint('fx')}.")
        elif r["history"] == PC.HISTORY_OFFLINE:
            doc.item(f"{r['symbol']}: not in the history cache, and "
                     f"TAXJSON_OFFLINE is set: nothing was fetched.")
    for sym, ratio in sorted(ratios.items()):
        doc.item(f"{sym}: LAST is the close of its QUOTE line's listing "
                 f"(ratio {ratio:g}); the percentages are the same either "
                 f"way.")
    for kind in LEFT_OUT:
        if left[kind]:
            doc.item(f"Left out, {_LEFT_WORDS[kind][1]}: "
                     f"{', '.join(left[kind])}.")
    doc.item("Each series is in its own listing's currency (no exchange "
             "rate); a close dated today is left out until the next day "
             "(it may be the live price). A DATE marked * is older than the "
             "newest row's: the listing did not trade since, or its market "
             "was closed.")
    doc.item(f"The history cache serves a series fetched today without "
             f"asking again; an older one when Yahoo Finance gives no "
             f"answer, with TAXJSON_OFFLINE set or without the [fx] extra "
             f"(a line above says so when it is older than "
             f"{OLD_CACHE_DAYS} days offline, or at all after no answer).")
    if bench is not None:
        doc.item(f"Benchmark {bench[0]} ({bench[1]}): BETA and ALPHA pair "
                 f"the two series' returns between the dates both have, so "
                 f"a holiday on one market is skipped by both.")
    doc.item("Market statistics from past prices: not tax figures, and "
             "not advice.")


def _header(recs, left, bench, bench_hist, latest) -> List[Tuple[str, str]]:
    header = [("Closes", "daily, adjusted for splits and dividends, from "
               + _sources(recs, bench_hist))]
    if bench is not None:
        if bench_hist is None:
            b = ": no daily history, so BETA and ALPHA are blank"
        else:
            bday = bench_hist.bars[-1].day
            b = f", last close {bday}" + (
                "*" if _older(bday, latest) else "")
        header.append(("Benchmark", f"{bench[0]} ({bench[1]})" + b))
    if any(left.values()):
        header.append(("Left out", ", ".join(
            _count(len(left[k]), _LEFT_WORDS[k]) for k in LEFT_OUT if left[k])
            + " (--details names them)"))
    return header


def _text(args, recs, left, bench, bench_hist, ratios, horizon, latest,
          bench_why: Optional[str], today: str) -> None:
    doc = _out.Doc(f"TECHNICALS — {_count(len(recs), ('holding', 'holdings'))}"
                   f" at the end of the "
                   f"books" + (f" ({horizon})" if horizon else ""))
    if not recs:
        doc.para("No open positions.")
        doc.print()
        return
    doc.kv(_header(recs, left, bench, bench_hist, latest))
    doc.blank()
    doc.legend(LEGEND)
    if any(r["stats"]["beta"] is not None for r in recs):
        doc.legend(f"BETA: cov/var, ALPHA: Jensen's (risk-free 0, % a year), "
                   f"over {T.YEAR} daily returns against {bench[0]}.")
    _main_table(doc, recs, latest)
    doc.blank()
    _history_acts(doc, recs, bench, bench_hist, bench_why, today)
    old = [r["symbol"] for r in recs if _older(r["last_date"], latest)]
    if old:
        doc.line(_out.act_list(f"DATE marked *: last close older than "
                               f"{latest} for "
                               f"{_count(len(old), ('symbol', 'symbols'))}",
                               old, DETAILS))
    _benchmark_acts(doc, recs, bench_hist, latest)
    with doc.details():
        _details_part(doc, recs, left, bench, bench_hist, ratios)
    if not _details(args):
        doc.hint(DETAILS, "the other statistics, the history, the notes")
    doc.print(details=_details(args))


def cmd_technicals(args: argparse.Namespace, *, fetchers=None,
                   today: Optional[str] = None) -> None:
    """`tjs technicals [--benchmark SYMBOL] [--details] [--json]`: every
    position the books hold at their end (one row a symbol, all accounts,
    as `tjs shares`) with its technical statistics from about a year of
    daily closes (lib/technicals). `fetchers` and `today` replace the live
    tier and the day for tests (lib/price_chain daily_history)."""
    from taxjson.lib.price_chain import daily_history
    from taxjson.lib.report_model import resolve_gains_files
    root = Path(args.dir).resolve()
    cache = root / "work"
    files = resolve_gains_files(cache)
    if not files:
        _die_input("no gains files in work/ — run `tjs run` first")
    _warn_accounts_without_books(root, files, "technicals", "gains file")
    today = today or date.today().isoformat()
    rows, left = _holdings(files)
    bench = _benchmark(args, root)
    symbols = [s for s, _e in rows]
    pairs, ratios, unspelled = (_spellings(root, symbols, bench) if symbols
                                else ({}, {}, []))
    hist, missing = (daily_history(pairs, cache_path=cache / HISTORY_CACHE,
                                   fetchers=fetchers, today=today)
                     if pairs else ({}, {}))
    if symbols and not any(s in hist for s in symbols) \
            and len(unspelled) < len(symbols):
        _refuse_no_history({s: missing[s] for s in symbols if s in missing})
    bench_hist = hist.get(bench[0]) if bench and symbols else None
    if bench is None and symbols:
        _out.note("no benchmark (`benchmark = \"SYMBOL\"` under [settings], "
                  "or --benchmark): BETA and ALPHA blank", prog=PROG,
                  whole=True)
    bench_why = (None if bench is None or bench_hist or not symbols
                 else missing.get(bench[0], "none"))
    recs = _stats_rows(rows, pairs, ratios, hist, missing, set(unspelled),
                       bench_hist.bars if bench_hist else None)
    latest = max((r["last_date"] for r in recs if r["last_date"]),
                 default=None)
    horizon = _books_horizon(cache, list(files))
    if getattr(args, "json", False):
        _json_out(_json_doc(recs, left, bench, bench_hist, bench_why,
                            horizon, latest), schema="technicals")
        return
    _text(args, recs, left, bench, bench_hist, ratios, horizon, latest,
          bench_why, today)


def register(sub: argparse._SubParsersAction) -> None:
    """This module's command, for the `tjs` parser."""
    p = sub.add_parser(
        "technicals",
        help="Each holding's moving averages, RSI, beta and alpha",
        description="Every position the books hold at their end (one row a "
                    "symbol, all accounts) with technical statistics from "
                    "about a year of daily closes adjusted for splits and "
                    "dividends: % from the 20-, 50- and 200-session moving "
                    "averages, RSI(14), beta and alpha against a benchmark; "
                    "--details adds the 52-week range, volatility, returns, "
                    "drawdown, ATR and volume. The closes come from Yahoo "
                    "Finance (the [fx] extra) and are cached in "
                    "work/.history_cache.json; with TAXJSON_OFFLINE set, "
                    "only the cache is read. Market statistics, not tax "
                    "figures or advice.")
    p.add_argument("--benchmark", metavar="SYMBOL", default=None,
                   help="The symbol beta and alpha are measured against, "
                        "spelled as the books spell it (or as Yahoo spells "
                        "an index); default: [settings] benchmark")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of text")
    p.set_defaults(func=cmd_technicals)
