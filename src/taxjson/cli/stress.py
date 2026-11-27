"""`tjs stress`: a crude estimate of what an index move does to the
holdings. The holdings are `tjs technicals`' (cli/positions
`held_by_symbol`, split by cli/technicals `_classify`), their daily closes
and betas too (lib/price_chain `daily_history` through cli/technicals
`_spellings` and `_stats_rows`: lib/technicals `beta_alpha` over the last
YEAR paired returns against the benchmark, `_benchmark`); the model is
lib/stress (BETA x the move for a listing or a coin, Black-Scholes at the
moved price for an option). The positions are the books' at their end;
they are valued at the closes of the valuation date (`_valuation_dates`:
the newest last close of a listing or the benchmark; a coin's, which
trades every day, the newest coin close), and an option's years to
expiry count from its underlying's last close. A view only: nothing in
the books is read but the positions, the declared contract sizes and
the base-currency rates, nothing is written but the history cache, and
no tax figure reads it."""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from taxjson.cli import technicals as TC
from taxjson.cli.common import (_account_group_of, _accounts, _base_currency,
                                _gains_files_of, _require_listed,
                                _warn_accounts_without_books)
from taxjson.cli.positions import _books_horizon, held_by_symbol
from taxjson.lib import out as _out
from taxjson.lib import stress as S
from taxjson.lib import technicals as T
from taxjson.model import money as M
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.sanity import _json_out

PROG = "tjs stress"
DETAILS = "tjs stress --details"
BETA_CMD = "tjs stress --beta SYMBOL=X"
# An index move, in %: above -100 (an index loses at most all of it), at
# most +100.
MOVE_LOW, MOVE_HIGH = -100.0, 100.0
# The rate a year --rate takes, in %, exclusive at both ends.
RATE_LOW, RATE_HIGH = -100.0, 100.0
# The largest beta --beta takes, either sign.
BETA_MAX = 10.0
_BETA_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
# The table's marks, one a meaning (backlog TJ-5428): a PRICE whose last
# close is older than the valuation date (as tjs technicals marks its
# DATE), a BETA given by --beta (the legend names it).
STALE_MARK, GIVEN_MARK = "*", "#"
# Why a holding is not in the TOTAL (`left_out` in --json).
NO_HISTORY = "no-history"        # no daily close for it or its underlying
NO_CURRENCY = "no-currency"      # its series names no currency
NO_RATE = "no-rate"              # no base-currency rate for that currency
NO_VOLATILITY = "no-volatility"  # an option: under YEAR + 1 closes
NO_TERMS = "no-terms"            # an option: no strike above 0, or expiry
NO_SIZE = "no-size"              # an option: the books declare two sizes
NO_BETA = "no-beta"              # no beta: too little shared history
EXPIRED = "expired"              # an option expired by the valuation date
# The reasons a holding has no value at all (NO_BETA and EXPIRED keep
# theirs).
NO_VALUE = (NO_HISTORY, NO_CURRENCY, NO_RATE, NO_VOLATILITY, NO_TERMS,
            NO_SIZE)
_WHY = {
    NO_HISTORY: "no daily history",
    NO_CURRENCY: "its series names no currency",
    NO_RATE: "no rate to the base currency for its currency in work/to_base.csv",
    NO_VOLATILITY: (f"fewer than {T.YEAR + 1} closes of its underlying, too "
                    f"few for the volatility"),
    NO_TERMS: ("its symbol gives no strike above 0, expiry or right (C or "
               "P) to price it with"),
    NO_SIZE: "the books declare more than one contract size",
    NO_BETA: "no beta",
}
_BETA_WHY = {
    T.TOO_FEW_PAIRS: f"fewer than {T.YEAR} daily returns on dates the "
                     f"benchmark has too",
    T.NO_BENCHMARK: "no benchmark series",
    T.FLAT_BENCHMARK: "the benchmark's returns do not vary",
}

VOLATILITY_NOTE = ("The volatility is held. It usually rises in a drop, "
                   "which raises every option's price: written options lose "
                   "more, and long options less, than shown.")

DEFINITIONS = (
    "PRICE: the last close (Yahoo's adjusted series, whose last close is "
    "the session's own), times a ticker.map QUOTE line's ratio, in the base "
    "currency at the newest rate of work/to_base.csv on or before the "
    "valuation date (the newest close of a listing or the benchmark); "
    "marked * when it is older than that date (a coin's: than the newest "
    "coin close). VALUE: QTY x PRICE (an option: x its contract size).",
    f"BETA: cov / var of the daily simple returns of the holding (an "
    f"option: its underlying) and the benchmark on the dates both have, "
    f"the last {T.YEAR} (as tjs technicals), each in its own currency; "
    f"--beta SYMBOL=X gives it, marked {GIVEN_MARK}.",
    "MOVE%: BETA x the index move, the holding's own price move, held at "
    "-100% (no price falls below 0).",
    "CHANGE: a share, unit or coin: VALUE x MOVE%. An option: QTY x size x "
    "(the model at the moved price - the model now), written contracts "
    "(QTY below 0) the other way. CHANGE%: CHANGE / |VALUE|; the TOTAL's: "
    "CHANGE / the sum of the positive VALUEs, the long ones (written "
    "options and shorts would shrink a net value).",
    "The option model: Black-Scholes, European, no dividends: call = S N(d1)"
    " - K e^(-rT) N(d2), put = K e^(-rT) N(-d2) - S N(-d1), d1 = (ln(S / K)"
    " + (r + vol^2 / 2) T) / (vol sqrt(T)), d2 = d1 - vol sqrt(T). A symbol "
    "with no strike above 0, or no expiry, is left out.",
    f"VOL: the sample standard deviation of the underlying's daily log "
    f"returns over a year ({T.YEAR} sessions; a coin's {S.COIN_YEAR} days) "
    f"x the square root of that count. " + VOLATILITY_NOTE,
    "YEARS: calendar days from the underlying's last close (the date of "
    "SPOT) to the expiry / 365. A contract that expires on or before the "
    "valuation date is expired: its intrinsic value, not in the TOTAL. "
    "RATE: --rate, % a year, continuous; 0 unless given. DELTA: N(d1) for a "
    "call, N(d1) - 1 for a put, at today's price.",
    "SIZE: the units of the underlying one contract is a right to: a "
    "ticker.map MULT line, else the size the books declare, else the "
    "standard 100; two sizes declared and no MULT line: left out.",
)


class _Words(argparse.Action):
    """`[ACCOUNT] MOVE...`: the first word is the ACCOUNT (one, or a comma
    list) unless it is a move; every other word must be one. Sets
    `account` (None: every account) and `moves` (the words as typed). A
    first word that reads as a move but names an account is moved over
    by `_account_word`, which can read the project."""

    def __call__(self, parser, namespace, values, option_string=None):
        words = [str(w) for w in (values or [])]
        account = None
        if words and S.parse_move(words[0]) is None:
            account = words.pop(0)
        bad = [w for w in words if S.parse_move(w) is None]
        if bad:
            parser.error(f"{bad[0]!r} is not a move: a % such as -10 or "
                         f"-10% (an ACCOUNT goes first)")
        namespace.account = account
        namespace.moves = words


def _account_word(args: argparse.Namespace, root: Path) -> None:
    """`tjs stress 99900099 -10`: a first word that reads as a move but is
    an [accounts] name is the ACCOUNT (as cli/common `_lone_positional`
    reads an exact name)."""
    from taxjson.pipeline.config import load_config
    words = list(getattr(args, "moves", None) or [])
    if getattr(args, "account", None) is not None or not words:
        return
    if words[0] in (load_config(root, missing_ok=True).get("accounts") or {}):
        args.account = words.pop(0)
        args.moves = words


def _moves(args: argparse.Namespace) -> List[float]:
    """The index moves asked for, in %, in the order given (a repeat
    once), or DEFAULT_MOVES; one outside MOVE_LOW to MOVE_HIGH refused."""
    out: List[float] = []
    for word in getattr(args, "moves", None) or []:
        m = S.parse_move(word)
        if m is None or not (MOVE_LOW < m <= MOVE_HIGH):
            _die_input(f"move {word!r} is not a % from above {MOVE_LOW:g} to "
                       f"{MOVE_HIGH:g}", "Give the index's move in %: "
                       "-10 or -10% (a fall), 5 or +5% (a rise).")
        m = 0.0 if m == 0 else m
        if m not in out:
            out.append(m)
    return out or list(S.DEFAULT_MOVES)


def _rate(args: argparse.Namespace) -> Tuple[float, str]:
    """(--rate in % a year, where it came from): 0, the default, unless
    given."""
    text = getattr(args, "rate", None)
    if text is None:
        return 0.0, "default"
    r = S.parse_move(str(text))
    if r is None or not (RATE_LOW < r < RATE_HIGH):
        _die_input(f"--rate {text!r} is not a rate in % a year",
                   "Give the risk-free rate in %: --rate 4 or --rate 4.5%.")
    return (0.0 if r == 0 else r), "--rate"


def _given_betas(args: argparse.Namespace, wanted: Sequence[str]
                 ) -> Dict[str, float]:
    """{symbol: beta} of each --beta SYMBOL=X: a holding's symbol, or an
    option's underlying (never the option's own); a symbol twice, a
    value that is no number from -BETA_MAX to BETA_MAX in ASCII digits or
    a symbol not among `wanted` refused (exit 2)."""
    from taxjson.lib.option_symbols import (is_option_symbol,
                                            parse_option_underlying)
    out: Dict[str, float] = {}
    for text in getattr(args, "beta", None) or []:
        sym, eq, val = str(text).partition("=")
        sym = sym.strip().upper()
        val = val.strip()
        if not eq or not sym or not _BETA_RE.fullmatch(val):
            _die_input(f"--beta {text!r} is not SYMBOL=BETA",
                       "Write it as --beta XYZQ.TO=1.2, the symbol as the "
                       "books spell it.")
        x = float(val)
        if abs(x) > BETA_MAX:
            _die_input(f"--beta {sym}={val}: a beta from {-BETA_MAX:g} to "
                       f"{BETA_MAX:g}")
        if is_option_symbol(sym):
            _die_input(f"--beta {sym}: an option moves with its underlying",
                       f"Give the underlying's: --beta "
                       f"{parse_option_underlying(sym)}=X.")
        if sym in out and out[sym] != x:
            _die_input(f"--beta {sym} is given twice ({out[sym]:g} and {x:g})")
        if sym not in wanted:
            _die_input(f"--beta {sym}: no holding of these accounts is it or "
                       f"an option on it",
                       "Check the symbol, as the books spell it (XYZQ.TO, "
                       "XYZQ.US).")
        out[sym] = x
    return out


def _option_parts(sym: str) -> Tuple[str, Optional[str], Optional[float],
                                     Optional[str]]:
    """(underlying, right, strike, expiry) of an option symbol; None for a
    part that cannot be read."""
    from taxjson.lib.option_symbols import (parse_option_expiry,
                                            parse_option_right,
                                            parse_option_strike,
                                            parse_option_underlying)
    return (str(parse_option_underlying(sym)), parse_option_right(sym),
            parse_option_strike(sym), parse_option_expiry(sym))


def _contract_size(sym: str, und: str, e: Dict[str, Any],
                   declared: Set[float]
                   ) -> Tuple[Optional[float], str, List[float]]:
    """(an equity option's contract size, where it came from, the sizes
    declared): a ticker.map MULT line (lib/markets), else the one size the
    books declare (the gains files' lines, `declared` from the base
    books), else the standard 100 (lib/contract_size); None when they
    declare two."""
    from taxjson.lib import markets
    from taxjson.lib.contract_size import OPTION_CONTRACT_SHARES
    mult = markets.option_contract_size(sym, und)
    if mult:
        return float(mult), "ticker.map MULT", []
    sizes = sorted({M.units(float(x), 10)
                    for x in set(e.get("sizes") or ()) | set(declared)})
    if e.get("size_conflict") or len(sizes) > 1:
        return None, "conflict", sizes
    if sizes:
        return sizes[0], "declared", sizes
    return OPTION_CONTRACT_SHARES, "standard", []


def _declared_sizes(cache: Path, accounts: Sequence[str], syms: Set[str]
                    ) -> Dict[str, Set[float]]:
    """{option symbol of `syms`: the contract sizes its rows in these
    accounts' base books declare}. The gains file's line has none when
    one book's rows declare two (lib/pipeline declared_multipliers): the
    rows say so. A book that cannot be read adds nothing (`tjs run`
    rewrites it)."""
    from taxjson.lib.json_input import read_work_doc
    out: Dict[str, Set[float]] = {}
    if not syms:
        return out
    for a in accounts:
        try:
            doc = read_work_doc(cache / f"{a}_base.json")
        except (OSError, ValueError):
            continue
        for t in (doc.get("transactions") if isinstance(doc, dict)
                  else None) or []:
            sym = str(t.get("symbol") or "") if isinstance(t, dict) else ""
            if sym in syms:
                m = float(t.get("multiplier") or 0.0)
                if m > 0:
                    out.setdefault(sym, set()).add(M.units(m, 10))
    return out


class _Book:
    """The positions of the accounts asked for: the listings and coins
    (`listings`, (symbol, entry) by symbol), the equity options
    (`options`), the futures and options on futures left out
    (`futures`), and the symbols whose closes are read (`symbols`: each
    listing and each option's underlying)."""

    def __init__(self, held: Dict[str, Dict[str, Any]]):
        from taxjson.lib.ticker_map import is_future_ticker
        rows, left = TC._classify(held)
        self.held = held
        self.listings = sorted(rows + [(s, held[s]) for s in left["crypto"]])
        self.options = [(s, held[s]) for s in left["options"]
                        if not is_future_ticker(s)]
        self.futures = sorted(left["futures"] + [
            s for s in left["options"] if is_future_ticker(s)])
        self.symbols = sorted({s for s, _e in self.listings}
                              | {_option_parts(s)[0] for s, _e in self.options})


def _files(args: argparse.Namespace, root: Path, cache: Path):
    """(the accounts named, None for every account; their gains files)."""
    names = _accounts(args, root)
    files = _gains_files_of(cache, names)
    if names:
        _require_listed(names, files, "gains file")
    if not files:
        _die_input("no gains files in work/ — run `tjs run` first")
    _warn_accounts_without_books(root, files, "stress", "gains file",
                                 among=names)
    return names, files


def _accounts_text(root: Path, files: Dict[str, Path]) -> str:
    """The accounts read, for the header: `3 accounts: a, b, tfsa
    (registered: tfsa)`."""
    from taxjson.lib.country import is_canada
    from taxjson.pipeline.config import _country, load_config
    groups = _account_group_of(root)
    accts = sorted(files)
    reg = [a for a in accts if groups.get(a) == "sheltered"]
    text = (f"{TC._count(len(accts), ('account', 'accounts'))}: "
            f"{', '.join(accts)}")
    if not reg:
        return text
    word = ("registered" if is_canada(_country(
        load_config(root, missing_ok=True).get("settings"))) else "sheltered")
    return text + (f" ({word})" if len(accts) == 1
                   else f" ({word}: {', '.join(reg)})")


def _refuse_no_benchmark() -> None:
    _die('no benchmark (`benchmark = "SYMBOL"` under [settings], or '
         '--benchmark): the betas are measured against it',
         "Name the index or the listing the move is in, as the books spell a "
         "listing (XYZQ.TO, XYZQ.US) or as Yahoo Finance spells an index "
         "(^XYZQ); --beta SYMBOL=X gives one holding's beta instead.")


def _rates(cache: Path, base: str, quotes: Dict[str, Dict[str, Any]],
           on: str) -> Dict[str, Tuple[float, Optional[str]]]:
    """{currency: (the rate to the base currency, its date)} of each
    series' currency: 1 for the base itself, else the newest rate of
    work/to_base.csv on or before `on`, the valuation date (none: left
    out)."""
    from taxjson.lib.price_chain import latest_rate, load_fx_history
    curs = sorted({q["currency"] for q in quotes.values() if q["currency"]})
    hist = (load_fx_history(cache / "to_base.csv", base)
            if any(c != base for c in curs) else {})
    out: Dict[str, Tuple[float, Optional[str]]] = {}
    for cur in curs:
        if cur == base:
            out[cur] = (1.0, None)
            continue
        r, day = latest_rate(hist, cur, on)
        if r is not None and r > 0:
            out[cur] = (r, day)
    return out


def _quote_problem(q: Optional[Dict[str, Any]], fx) -> Optional[str]:
    if q is None or q["history"] != "ok" or q["last_close"] is None:
        return NO_HISTORY
    if not q["currency"]:
        return NO_CURRENCY
    if q["currency"] not in fx:
        return NO_RATE
    return None


def _beta_of(sym: str, q: Optional[Dict[str, Any]], given: Dict[str, float]
             ) -> Tuple[Optional[float], Optional[str]]:
    """(the beta of `sym`, where it came from: "--beta" or "history")."""
    if sym in given:
        return given[sym], "--beta"
    b = q["stats"]["beta"] if q is not None else None
    return (b, "history") if b is not None else (None, None)


def _pct(change: float, value: float) -> Optional[float]:
    return None if value == 0 else change / abs(value) * 100.0


def _record(sym: str, kind: str, group: str, e: Optional[Dict[str, Any]]
            ) -> Dict[str, Any]:
    return {"symbol": sym, "kind": kind, "group": group,
            "qty": e["qty"] if e else None,
            "accounts": dict(e["accounts"]) if e else {},
            "currency": None, "fx": None, "price": None, "value": None,
            "beta": None, "beta_from": None, "left_out": None, "why": None,
            "valued_on": None, "moves": [], "option": None}


def _moves_of(r: Dict[str, Any], run: "_Run", beta: float,
              change: Callable[[float], Optional[float]]) -> None:
    """Each move's record on row `r`: the price's move (BETA x the move,
    held at -100%) and `change(pct)`, the CHANGE at that price move."""
    for m in run.moves:
        pct, held = S.moved(beta, m)
        c = change(pct)
        r["moves"].append({"move_pct": m, "price_move_pct": pct,
                           "held": held, "change": c,
                           "change_pct": None if c is None
                           else _pct(c, r["value"])})


def _listing(sym: str, e: Dict[str, Any], run: "_Run") -> Dict[str, Any]:
    """One share, unit or coin held: its value and its change at each
    move (VALUE x BETA x the move)."""
    from taxjson.lib.price_chain import is_crypto_symbol
    q = run.quotes.get(sym)
    r = _record(sym, "crypto" if is_crypto_symbol(sym) else "share", sym, e)
    why = _quote_problem(q, run.fx)
    if why or q is None:
        r["left_out"] = why
        return r
    rate = run.fx[q["currency"]][0]
    unit = q["last_close"] * q["quote_ratio"] * rate
    value = M.shown(e["qty"] * unit, 2)
    beta, src = _beta_of(sym, q, run.given)
    r.update(currency=q["currency"], fx=rate, price=unit, value=value,
             beta=beta, beta_from=src, left_out=None if src else NO_BETA,
             valued_on=q["last_date"])
    if beta is not None:
        _moves_of(r, run, beta, lambda pct: M.shown(value * pct / 100.0, 2))
    return r


def _option_problem(right: Optional[str], strike: Optional[float],
                    expiry: Optional[str], q: Optional[Dict[str, Any]],
                    run: "_Run", size: Optional[float]) -> Optional[str]:
    """Why an option cannot be priced (the reasons in NO_VALUE), or None."""
    if right not in ("C", "P") or not strike or strike <= 0 or not expiry:
        return NO_TERMS
    why = _quote_problem(q, run.fx)
    if why or q is None:
        return why
    if size is None:
        return NO_SIZE
    if expiry > run.on(q["symbol"]) and q["log_vol"] is None:
        return NO_VOLATILITY
    return None


def _option(sym: str, e: Dict[str, Any], run: "_Run") -> Dict[str, Any]:
    """One equity option held, re-priced: Black-Scholes at the
    underlying's last close and at its moved price (lib/stress), the years
    from that close and the underlying's volatility held, the contract's
    size; written contracts (QTY below 0) the other way. One that expired
    on or before the valuation date: its intrinsic value, not in the
    TOTAL."""
    und, right, strike, expiry = _option_parts(sym)
    q = run.quotes.get(und)
    r = _record(sym, "option", und, e)
    size, basis, sizes = _contract_size(sym, und, e, run.sizes.get(sym, set()))
    why = _option_problem(right, strike, expiry, q, run, size)
    if why or q is None or size is None or not (strike and expiry and right):
        r["left_out"] = why
        if why == NO_SIZE:
            r["why"] = (f"the books declare {', '.join(f'{x:g}' for x in sizes)}"
                        if sizes else _WHY[NO_SIZE])
        return r
    expired = expiry <= run.on(und)
    on = q["last_date"]
    days = (date.fromisoformat(expiry) - date.fromisoformat(on)).days
    years = 0.0 if expired else days / S.YEAR_DAYS
    rate, vol = run.fx[q["currency"]][0], q["log_vol"] or 0.0
    spot = q["last_close"] * q["quote_ratio"]
    r_ = run.rate_pct / 100.0
    model = S.black_scholes(right, spot, strike, years, r_, vol)
    per = e["qty"] * size * rate
    value = M.shown(per * model, 2)
    beta, src = (None, None) if expired else _beta_of(und, q, run.given)
    r.update(currency=q["currency"], fx=rate, price=model * rate, value=value,
             beta=beta, beta_from=src, valued_on=on,
             left_out=EXPIRED if expired else None if src else NO_BETA,
             option={"underlying": und, "right": right, "strike": strike,
                     "expiry": expiry, "size": size, "size_from": basis,
                     "years": years, "expired": expired,
                     "volatility": q["log_vol"], "rate": r_, "spot": spot,
                     "model": model,
                     "delta": S.delta(right, spot, strike, years, r_, vol)})
    if beta is not None:
        _moves_of(r, run, beta, lambda pct: M.shown(M.shown(
            per * S.black_scholes(right, spot * (1.0 + pct / 100.0), strike,
                                  years, r_, vol), 2) - value, 2))
    return r


def _underlying(und: str, run: "_Run") -> Dict[str, Any]:
    """The row heading an option whose underlying is not held: its price,
    beta and move, no quantity or value."""
    q = run.quotes.get(und)
    r = _record(und, "underlying", und, None)
    if _quote_problem(q, run.fx) or q is None:
        return r
    rate = run.fx[q["currency"]][0]
    beta, src = _beta_of(und, q, run.given)
    r.update(currency=q["currency"], fx=rate,
             price=q["last_close"] * q["quote_ratio"] * rate, beta=beta,
             beta_from=src, valued_on=q["last_date"])
    if beta is not None:
        _moves_of(r, run, beta, lambda pct: None)
    return r


def _sort_key(r: Dict[str, Any]) -> Tuple[Any, ...]:
    o = r["option"] or {}
    if r["kind"] == "option" and not o:
        o = dict(zip(("underlying", "right", "strike", "expiry"),
                     _option_parts(r["symbol"])))
    return (r["group"], r["kind"] == "option", o.get("expiry") or "",
            o.get("right") or "", o.get("strike") or 0.0, r["symbol"])


class _Run:
    """What one `tjs stress` computed: the moves, the rate, the betas
    given, the closes (`quotes`, cli/technicals `_stats_rows` records by
    symbol, with `log_vol`), the base-currency rates, the valuation dates
    (`valued`, the listings' and their options'; `coins_valued`, the
    coins', None without one: `_valuation_dates`), the series older than
    their date (`stale`), the rows (by group: a listing, then its
    options) and the totals (`long_value`: the positive VALUEs, the
    TOTAL's CHANGE% is of them)."""

    def __init__(self, book: _Book, quotes, fx, given, moves, rate,
                 valued: Tuple[str, Optional[str]],
                 sizes: Dict[str, Set[float]]):
        self.book, self.quotes, self.fx, self.given = book, quotes, fx, given
        self.moves = moves
        self.rate_pct, self.rate_from = rate
        (self.valued, self.coins_valued), self.sizes = valued, sizes
        self.stale = sorted(s for s, q in quotes.items()
                            if TC._older(q["last_date"], self.on(s)))
        rows = [_listing(s, e, self) for s, e in book.listings]
        rows += [_option(s, e, self) for s, e in book.options]
        held = {s for s, _e in book.listings}
        rows += [_underlying(u, self) for u in sorted(
            {_option_parts(s)[0] for s, _e in book.options} - held)]
        self.rows = sorted(rows, key=_sort_key)
        self.held = [r for r in self.rows if r["kind"] != "underlying"]
        self.inc = [r for r in self.held if r["left_out"] is None]
        self.value = M.exact_total([r["value"] for r in self.inc])
        self.long_value = M.exact_total([r["value"] for r in self.inc
                                         if r["value"] > 0])
        self.changes = [M.exact_total([r["moves"][i]["change"]
                                       for r in self.inc])
                        for i in range(len(moves))]
        self.no_beta = self._out(NO_BETA)
        self.expired = self._out(EXPIRED)
        self.left_value = M.exact_total([r["value"] for r in
                                         self.no_beta + self.expired])
        self.no_value = [r for r in self.held if r["left_out"] in NO_VALUE]

    def on(self, sym: str) -> str:
        """The valuation date `sym` is judged against: a coin's, the
        coins'; any other's (a listing, an index, an underlying), the
        listings'."""
        from taxjson.lib.price_chain import is_crypto_symbol
        if is_crypto_symbol(sym):
            return self.coins_valued or self.valued
        return self.valued

    def dates(self) -> str:
        """The valuation date as said: the listings', and the coins' when
        a coin is read and its date differs."""
        c = self.coins_valued
        return self.valued + (f" (coins: {c})" if c and c != self.valued
                              else "")

    def _out(self, why: str) -> List[Dict[str, Any]]:
        return [r for r in self.held if r["left_out"] == why]

    def total_pct(self, i: int) -> Optional[float]:
        return (self.changes[i] / self.long_value * 100.0
                if self.long_value > 0 else None)


def _quotes(root: Path, cache: Path, book: _Book, bench, fetchers, today):
    """({symbol: its history record (cli/technicals `_stats_rows`) with
    `log_vol`}, the benchmark's series or None, why it has none): the
    closes of every listing and underlying, and of the benchmark, through
    the price chain and its cache; refused when not one has any. A coin's
    volatility is of COIN_YEAR calendar days, the rest's of YEAR
    sessions."""
    from taxjson.lib.price_chain import daily_history, is_crypto_symbol
    syms = book.symbols
    pairs, ratios, unspelled = TC._spellings(root, syms, bench)
    hist, missing = (daily_history(pairs, cache_path=cache / TC.HISTORY_CACHE,
                                   fetchers=fetchers, today=today)
                     if pairs else ({}, {}))
    if not any(s in hist for s in syms) and len(unspelled) < len(syms):
        TC._refuse_no_history({s: missing[s] for s in syms if s in missing})
    bench_hist = hist.get(bench[0]) if bench else None
    bench_why = (None if bench is None or bench_hist
                 else missing.get(bench[0], "none"))
    recs = TC._stats_rows(
        [(s, book.held.get(s) or {"qty": 0.0, "accounts": {}}) for s in syms],
        pairs, ratios, hist, missing, set(unspelled),
        bench_hist.bars if bench_hist else None)
    for r in recs:
        h = hist.get(r["symbol"])
        r["log_vol"] = (S.log_volatility(
            [b.close for b in h.bars],
            S.COIN_YEAR if is_crypto_symbol(r["symbol"]) else T.YEAR)
            if h else None)
    return {r["symbol"]: r for r in recs}, bench_hist, bench_why


def _valuation_dates(quotes: Dict[str, Dict[str, Any]], bench_hist
                     ) -> Tuple[Optional[str], Optional[str]]:
    """(the listings' valuation date, the coins'), None where nothing is
    read. The listings': the newest last close of a listing, an
    underlying or the benchmark, so the same whichever accounts are named
    (backlog TJ-5427); with none, the coins'. The coins': the newest coin
    close. A coin trades every day: its Sunday close makes no listing
    stale."""
    from taxjson.lib.price_chain import is_crypto_symbol
    days = [q["last_date"] for s, q in quotes.items()
            if q["last_date"] and not is_crypto_symbol(s)]
    if bench_hist is not None and bench_hist.bars:
        days.append(bench_hist.bars[-1].day)
    coins = max((q["last_date"] for s, q in quotes.items()
                 if q["last_date"] and is_crypto_symbol(s)), default=None)
    return max(days, default=None) or coins, coins


def cmd_stress(args: argparse.Namespace, *, fetchers=None,
               today: Optional[str] = None) -> None:
    """`tjs stress [ACCOUNT[,ACCOUNT...]] [MOVE ...] [--benchmark SYMBOL]
    [--beta SYMBOL=X] [--rate R] [--details] [--json]`: each position the
    books hold at their end (one row a symbol, the accounts named or
    all), its value at the closes of the valuation date (`_valuation_dates`),
    and its change when the benchmark moves by MOVE % (default
    -20 -10 -5 5 10): a listing or a coin by BETA x the move, an option
    re-priced (lib/stress). `fetchers` and `today` replace the live tier
    and the day for tests (lib/price_chain daily_history)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    _account_word(args, root)
    moves, rate = _moves(args), _rate(args)
    _names, files = _files(args, root, cache)
    today = today or date.today().isoformat()
    held, _year = held_by_symbol(files, options=True, futures=True)
    book = _Book(held)
    given = _given_betas(args, book.symbols)
    bench = TC._benchmark(args, root)
    ctx: Dict[str, Any] = {
        "bench": bench, "bench_hist": None, "bench_why": None,
        "horizon": _books_horizon(cache, list(files)),
        "accounts": _accounts_text(root, files),
        "base": _base_currency(root), "today": today}
    quotes: Dict[str, Dict[str, Any]] = {}
    if book.symbols:
        if bench is None and any(s not in given for s in book.symbols):
            _refuse_no_benchmark()
        quotes, ctx["bench_hist"], ctx["bench_why"] = _quotes(
            root, cache, book, bench, fetchers, today)
    ctx["latest"] = max((q["last_date"] for q in quotes.values()
                         if q["last_date"]), default=None)
    ctx["valued"], coins = _valuation_dates(quotes, ctx["bench_hist"])
    valued = ctx["valued"] or today
    run = _Run(book, quotes, _rates(cache, ctx["base"], quotes, valued), given,
               moves, rate, (valued, coins), _declared_sizes(
                   cache, list(files), {s for s, _e in book.options}))
    if getattr(args, "json", False):
        _json_out(_json_doc(run, ctx), schema="stress")
    elif not book.symbols:
        _empty(run, ctx)
    else:
        _text(args, run, ctx)


def _empty(run: "_Run", ctx: Dict[str, Any]) -> None:
    """No position to move (the futures left out only)."""
    doc = _out.Doc(f"STRESS — {_moves_text(run.moves)}: no holdings")
    doc.kv([_positions(ctx)])
    doc.blank()
    doc.para("No open positions to move.")
    _futures_act(doc, run)
    doc.print()


# --------------------------------------------------------------- the JSON

def _j(v: Optional[float], places: int) -> Optional[float]:
    return TC._num(v, places)


def _json_row(r: Dict[str, Any]) -> Dict[str, Any]:
    o = r["option"]
    return {
        "symbol": r["symbol"], "kind": r["kind"],
        "underlying": r["group"] if r["kind"] == "option" else None,
        "qty": _j(r["qty"], 8),
        "accounts": {a: _j(q, 8) for a, q in sorted(r["accounts"].items())},
        "currency": r["currency"], "fx_rate": r["fx"],
        "valued_on": r["valued_on"],
        "price": None if r["price"] is None else M.json_money(r["price"], 6),
        "value": None if r["value"] is None else M.json_money(r["value"], 2),
        "beta": _j(r["beta"], 4), "beta_from": r["beta_from"],
        "in_total": r["kind"] != "underlying" and r["left_out"] is None,
        "left_out": r["left_out"],
        "moves": [{"move_pct": m["move_pct"],
                   "price_move_pct": _j(m["price_move_pct"], 4),
                   "held_at_floor": m["held"],
                   "change": (None if m["change"] is None
                              else M.json_money(m["change"], 2)),
                   "change_pct": _j(m["change_pct"], 4)} for m in r["moves"]],
        "option": None if o is None else {
            "underlying": o["underlying"], "right": o["right"],
            "strike": o["strike"], "expiry": o["expiry"], "size": o["size"],
            "size_from": o["size_from"], "years": _j(o["years"], 6),
            "expired": o["expired"], "volatility": _j(o["volatility"], 6),
            "rate": _j(o["rate"], 6),
            "underlying_price": M.json_money(o["spot"], 6),
            "model_price": M.json_money(o["model"], 6),
            "delta": _j(o["delta"], 6)},
    }


def _json_doc(run: _Run, ctx: Dict[str, Any]) -> Dict[str, Any]:
    from taxjson.lib.price_chain import HISTORY_ADJUSTED
    bench, bh = ctx["bench"], ctx["bench_hist"]
    return {
        "as_of": ctx["horizon"], "expiry_from": ctx["valued"],
        "coins_valued_on": run.coins_valued,
        "latest_close_date": ctx["latest"], "currency": ctx["base"],
        "adjusted": HISTORY_ADJUSTED, "moves_pct": list(run.moves),
        "benchmark": None if bench is None else {
            "symbol": bench[0], "given_by": bench[1],
            "history": "ok" if bh else ctx["bench_why"],
            "cache_reason": bh.cache_reason if bh else None,
            "quote_symbol": bh.listing if bh else None,
            "currency": bh.currency if bh else None,
            "sessions": len(bh.bars) if bh else 0,
            "last_date": bh.bars[-1].day if bh else None,
            "fetched": bh.fetched if bh else None},
        "model": {"rate_pct": run.rate_pct, "rate_from": run.rate_from,
                  "beta_returns": T.YEAR, "volatility_returns": T.YEAR,
                  "coin_volatility_returns": S.COIN_YEAR,
                  "year_days": S.YEAR_DAYS, "volatility_held": True,
                  "fx_held": True, "time_passes": False,
                  "cash_modelled": False},
        "fx": {c: {"rate": r, "date": d} for c, (r, d) in sorted(run.fx.items())},
        "rows": [_json_row(r) for r in run.rows],
        "totals": [{"move_pct": m, "change": M.json_money(run.changes[i], 2),
                    "change_pct": _j(run.total_pct(i), 4)}
                   for i, m in enumerate(run.moves)],
        "total_value": M.json_money(run.value, 2),
        "long_value": M.json_money(run.long_value, 2),
        "left_out_value": M.json_money(run.left_value, 2),
        "left_out": {"futures": list(run.book.futures),
                     **{k: [r["symbol"] for r in run.held if r["left_out"] == k]
                        for k in NO_VALUE + (NO_BETA, EXPIRED)}},
        "history": [{k: q[k] for k in (
            "symbol", "quote_symbol", "history", "cache_reason", "currency",
            "source", "fetched", "sessions", "first_date", "last_date",
            "paired_returns")} | {
            "last_close": (None if q["last_close"] is None
                           else M.json_money(q["last_close"], 6)),
            "beta": _j(q["stats"]["beta"], 4),
            "volatility": _j(q["log_vol"], 6)}
            for _s, q in sorted(run.quotes.items())],
    }


# --------------------------------------------------------------- the text

def _signed(v: Optional[float], places: int = 1) -> str:
    return TC._cell(v, places, True)


def _change(v: Optional[float]) -> str:
    if v is None:
        return ""
    s = f"{M.shown(v, 2):+,.2f}"
    return s[1:] if s in ("+0.00", "-0.00") else s


def _money(v: Optional[float]) -> str:
    from taxjson.lib.report_fmt import fmt_money
    return "" if v is None else fmt_money(v)


def _label(r: Dict[str, Any]) -> str:
    """A row's SYMBOL cell: the ticker, or under it an option's right,
    strike and expiry (its symbol when they cannot be read)."""
    if r["kind"] != "option":
        return r["symbol"]
    _u, right, strike, expiry = _option_parts(r["symbol"])
    if right is None or strike is None or expiry is None:
        return f"  {r['symbol']}"
    return f"  {right} {strike:g} {expiry}"


def _beta_cell(r: Dict[str, Any]) -> str:
    if r["beta"] is None:
        return ""
    return _beta_text(r["beta"]) + (GIVEN_MARK if r["beta_from"] == "--beta"
                                    else "")


def _beta_text(b: float) -> str:
    """A beta to 2 places, one that rounds to 0 shown 0.00, never -0.00."""
    s = f"{b:.2f}"
    return "0.00" if s == "-0.00" else s


def _price_cell(r: Dict[str, Any], run: _Run) -> str:
    """PRICE, marked STALE_MARK when its series' last close is older than
    its valuation date (`_Run.on`)."""
    p = str(TC._price(r["price"]))
    return p + STALE_MARK if p and r["group"] in run.stale else p


def _table(doc: _out.Doc, run: _Run) -> None:
    """The holdings, one row a symbol (an option under its underlying),
    then the TOTAL: one move, MOVE%, CHANGE and CHANGE%; several, one
    CHANGE column a move and the TOTAL's % under it."""
    one = len(run.moves) == 1
    body = []
    for r in run.rows:
        held = r["kind"] != "underlying"
        cells = [_label(r), TC._qty(r["qty"]) if held else "",
                 _price_cell(r, run), _money(r["value"]), _beta_cell(r)]
        mv = r["moves"]
        if one:
            m = mv[0] if mv else None
            cells += [_signed(m["price_move_pct"]) if m else "",
                      _change(m["change"]) if m else "",
                      _signed(m["change_pct"]) if m else ""]
        else:
            cells += [_change(m["change"]) for m in mv] or [""] * len(run.moves)
        body.append(cells)
    heads = ["SYMBOL", "QTY", "PRICE", "VALUE", "BETA"]
    if one:
        heads += ["MOVE%", "CHANGE", "CHANGE%"]
        foot = [["TOTAL", "", "", _money(run.value), "", "",
                 _change(run.changes[0]), _signed(run.total_pct(0))]]
    else:
        heads += [f"{m:+g}%" for m in run.moves]
        foot = [["TOTAL", "", "", _money(run.value), ""]
                + [_change(c) for c in run.changes],
                ["CHANGE%", "", "", "", ""]
                + [_signed(run.total_pct(i)) for i in range(len(run.moves))]]
    doc.table(heads, body, foot=foot,
              aligns=["<"] + [">"] * (len(heads) - 1))


def _moves_text(moves: Sequence[float]) -> str:
    return ", ".join(f"{m:+g}%" for m in moves)


def _positions(ctx: Dict[str, Any]) -> Tuple[str, str]:
    return ("Positions", "the books' end" + (
        f", {ctx['horizon']}," if ctx["horizon"] else "")
        + f" of {ctx['accounts']}")


def _header(run: _Run, ctx: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Benchmark, the two dates (the positions', the closes'), the rates
    held and one Options line; the rest is in NOTES."""
    bench, bh = ctx["bench"], ctx["bench_hist"]
    out: List[Tuple[str, str]] = []
    if bench is None:
        out.append(("Benchmark", "none: every beta given by --beta"))
    else:
        out.append(("Benchmark", f"{bench[0]} ({bench[1]})" + (
            f", last close {bh.bars[-1].day}" if bh else
            ": no daily history, so no beta from it")))
    out.append(_positions(ctx))
    out.append(("Valued", f"at the closes of {run.dates()}, in {ctx['base']}"))
    other = [(c, r, d) for c, (r, d) in sorted(run.fx.items()) if d]
    if other:
        out.append(("Rates", "; ".join(f"{c} {r:.4f} ({d})" for c, r, d in other)
                    + ", held constant"))
    if run.book.options:
        out.append(("Options", f"Black-Scholes, rate {run.rate_pct:g}% ("
                    + ("--rate" if run.rate_from == "--rate"
                       else "the default, --rate R")
                    + "), the volatility of 1 year, held"))
    return out


def _futures_act(doc: _out.Doc, run: _Run) -> None:
    if run.book.futures:
        doc.line(TC._act_named(
            f"futures not modelled: "
            f"{TC._count(len(run.book.futures), ('contract', 'contracts'))}, "
            f"not in the TOTAL", list(run.book.futures), None))


def _acts(doc: _out.Doc, run: _Run, ctx: Dict[str, Any]) -> None:
    """After the table: what the TOTAL leaves out, a move held at -100%,
    the closes older than the valuation date, where the closes came from
    (cli/technicals `_history_acts`)."""
    if run.no_beta:
        doc.line(TC._act_named(
            f"no beta: {TC._count(len(run.no_beta), ('holding', 'holdings'))}"
            f", {_money(M.exact_total([r['value'] for r in run.no_beta]))} "
            f"of value, not in the TOTAL",
            sorted({r["group"] for r in run.no_beta}), BETA_CMD))
    if run.no_value:
        doc.line(TC._act_named(
            f"{TC._count(len(run.no_value), ('holding', 'holdings'))} with "
            f"no value, not in the TOTAL",
            [r["symbol"] for r in run.no_value], DETAILS))
    if run.expired:
        doc.line(TC._act_named(
            f"expired but in the books, not in the TOTAL: "
            f"{_money(M.exact_total([r['value'] for r in run.expired]))} "
            f"intrinsic", [r["symbol"] for r in run.expired], "tjs run"))
    _futures_act(doc, run)
    floor = sorted({r["group"] for r in run.rows
                    if any(m["held"] for m in r["moves"])})
    if floor:
        doc.line(_out.act_list("MOVE% held at -100% (BETA x the index move "
                               "is below it)", floor, DETAILS))
    if run.stale:
        doc.line(_out.act_list(
            f"PRICE marked {STALE_MARK}: last close older than "
            f"{_stale_on(run)} for "
            f"{TC._count(len(run.stale), ('symbol', 'symbols'))}",
            run.stale, DETAILS))
    bh = ctx["bench_hist"]
    if bh is not None and TC._older(bh.bars[-1].day, run.valued):
        doc.line(_out.act(f"BETA ends on {bh.bars[-1].day}: the benchmark's "
                          f"last close is older than the holdings' "
                          f"({run.valued})"))
    TC._history_acts(doc, list(run.quotes.values()), ctx["bench"], bh,
                     ctx["bench_why"], ctx["today"], DETAILS)


def _stale_on(run: _Run) -> str:
    """The valuation date the stale series are older than: the listings',
    the coins', or both (`_Run.dates`) when both kinds are stale."""
    on = {run.on(s) for s in run.stale}
    return on.pop() if len(on) == 1 else run.dates()


def _legends(doc: _out.Doc, run: _Run, ctx: Dict[str, Any]) -> None:
    """The table's two legend lines."""
    bench = ctx["bench"]
    given = any(r["beta_from"] == "--beta" for r in run.rows)
    if bench:
        against = (f"BETA vs {bench[0]}, {T.YEAR} daily returns"
                   + (f" ({GIVEN_MARK} --beta)" if given else ""))
    else:
        against = ("BETA given by --beta"
                   + (f", marked {GIVEN_MARK}" if given else "")
                   + " (no benchmark)")
    opt = "; an option's PRICE: the model's" if run.book.options else ""
    if len(run.moves) == 1:
        doc.legend(f"{against}; MOVE% = BETA x {run.moves[0]:+g}%.")
        doc.legend(f"CHANGE% = CHANGE / |VALUE|, the TOTAL's / the positive "
                   f"VALUEs{opt}.")
    else:
        doc.legend(f"{against}; each % column: the CHANGE at that "
                   f"index move.")
        doc.legend(f"CHANGE%: the TOTAL's / the positive VALUEs{opt}.")


def _text(args: argparse.Namespace, run: _Run, ctx: Dict[str, Any]) -> None:
    bench = ctx["bench"]
    doc = _out.Doc(f"STRESS — {_moves_text(run.moves)}"
                   + (f" in {bench[0]}" if bench else "")
                   + f": {TC._count(len(run.held), ('holding', 'holdings'))}")
    doc.kv(_header(run, ctx))
    doc.blank()
    _legends(doc, run, ctx)
    _table(doc, run)
    doc.blank()
    _acts(doc, run, ctx)
    with doc.details():
        _details_part(doc, run, ctx)
    if not _details(args):
        doc.hint(DETAILS, "per account, the options' inputs, the notes")
    doc.print(details=_details(args))


# ------------------------------------------------------------ --details

def _by_account(doc: _out.Doc, run: _Run) -> None:
    """Each account's part of each row (its quantity's share of VALUE and
    CHANGE), and each account's TOTAL."""
    heads = ["ACCOUNT", "SYMBOL", "QTY", "VALUE"] + (
        ["CHANGE"] if len(run.moves) == 1
        else [f"{m:+g}%" for m in run.moves])
    body, foot = [], []
    for acct in sorted({a for r in run.held for a in r["accounts"]}):
        value: List[Optional[float]] = []
        changes: List[List[float]] = [[] for _m in run.moves]
        for r in run.held:
            q = r["accounts"].get(acct)
            if q is None or not r["qty"]:
                continue
            part = q / r["qty"]
            v = None if r["value"] is None else M.shown(r["value"] * part, 2)
            cs = [M.shown(m["change"] * part, 2) for m in r["moves"]]
            body.append([acct, r["symbol"], TC._qty(q), _money(v)]
                        + ([_change(c) for c in cs] or [""] * len(run.moves)))
            if r["left_out"] is None:
                value.append(v)
                for i, c in enumerate(cs):
                    changes[i].append(c)
        foot.append([acct, "TOTAL", "", _money(M.exact_total(value))]
                    + [_change(M.exact_total(c)) for c in changes])
    doc.section("BY ACCOUNT")
    doc.legend("Each account's part of a row (its share of QTY); its TOTAL: "
               "the rows in the TOTAL.")
    doc.table(heads, body, foot=foot, aligns=["<", "<"] + [">"] * (len(heads) - 2))


def _options_part(doc: _out.Doc, run: _Run) -> None:
    rows = [r for r in run.held if r["option"]]
    if not rows:
        return
    doc.section("OPTIONS")
    doc.legend("SPOT and MODEL a share, in the underlying's currency; YEARS "
               "from SPOT's close (HISTORY: LAST); DELTA at SPOT.")
    doc.table(["SYMBOL", "SIZE", "SPOT", "STRIKE", "EXPIRY", "YEARS", "VOL%",
               "RATE%", "MODEL", "DELTA"],
              [[r["symbol"], f"{o['size']:g}", TC._price(o["spot"]),
                f"{o['strike']:g}", o["expiry"], f"{o['years']:.3f}",
                "" if o["volatility"] is None
                else f"{o['volatility'] * 100:.1f}", f"{o['rate'] * 100:g}",
                f"{o['model']:.4f}", f"{o['delta']:+.3f}"]
               for r in rows for o in [r["option"]]],
              aligns=["<", ">", ">", ">", "<", ">", ">", ">", ">", ">"])


def _history_part(doc: _out.Doc, run: _Run) -> None:
    doc.section("HISTORY")
    doc.legend("QUOTE: the Yahoo Finance spelling; BETA from the history "
               "(--beta's above); VOL% for an option's underlying.")
    doc.table(["SYMBOL", "QUOTE", "CUR", "SESSIONS", "LAST", "CLOSE", "BETA",
               "VOL%", "SOURCE", "FETCHED"],
              [[s, q["quote_symbol"] or "", q["currency"] or "",
                str(q["sessions"]), q["last_date"] or "",
                TC._price(q["last_close"]),
                "" if q["stats"]["beta"] is None
                else _beta_text(q["stats"]["beta"]),
                "" if q["log_vol"] is None else f"{q['log_vol'] * 100:.1f}",
                q["source"] or q["history"], q["fetched"] or ""]
               for s, q in sorted(run.quotes.items())],
              aligns=["<", "<", "<", ">", "<", ">", ">", ">", "<", "<"])


def _left_out_notes(doc: _out.Doc, run: _Run) -> None:
    """One item a holding the TOTAL leaves out, with why."""
    for r in run.held:
        if r["left_out"] == NO_BETA:
            q = run.quotes.get(r["group"]) or {}
            why = _BETA_WHY.get((q.get("blank") or {}).get("beta"),
                                "too little history")
            doc.item(f"{r['symbol']}: not in the TOTAL: no beta"
                     + (f" for its underlying {r['group']}"
                        if r["kind"] == "option" else "")
                     + f" ({why}); --beta {r['group']}=X gives one.")
        elif r["left_out"] == EXPIRED:
            doc.item(f"{r['symbol']}: expired on {r['option']['expiry']}, on "
                     f"or before the closes of {run.on(r['group'])}, but the "
                     f"books still hold it: its expiry, exercise or assignment row "
                     f"is missing (tjs run names it too). Its intrinsic "
                     f"value, {_money(r['value'])}, is not in the TOTAL.")
        elif r["left_out"]:
            doc.item(f"{r['symbol']}: not in the TOTAL: "
                     f"{r['why'] or _WHY[r['left_out']]}"
                     + (f" ({r['group']})" if r["kind"] == "option" else "")
                     + ("; a ticker.map MULT line gives the size"
                        if r["left_out"] == NO_SIZE else "") + ".")
    if run.book.futures:
        doc.item(f"Left out, futures and options on futures (not modelled): "
                 f"{', '.join(run.book.futures)}.")


def _notes(doc: _out.Doc, run: _Run, ctx: Dict[str, Any]) -> None:
    from taxjson.lib.contract_size import OPTION_CONTRACT_SHARES
    _left_out_notes(doc, run)
    bh, base = ctx["bench_hist"], ctx["base"]
    other = sorted({f"{r['group']} ({r['currency']})" for r in run.rows
                    if r["currency"] and r["currency"] != base})
    if other:
        doc.item(f"Priced in another currency: {', '.join(other)}. A BETA is "
                 f"of the listing's own-currency returns against the "
                 f"benchmark's" + (f" ({bh.currency})" if bh and bh.currency
                                   else "")
                 + f", and the rate to {base} is held: a CHANGE carries no "
                 f"exchange-rate move.")
    standard = [r["symbol"] for r in run.held
                if r["option"] and r["option"]["size_from"] == "standard"]
    if standard:
        doc.item(f"No contract size in the books for "
                 f"{TC._count(len(standard), ('option', 'options'))}: the "
                 f"standard {OPTION_CONTRACT_SHARES:g} ({', '.join(standard)}; a "
                 f"ticker.map MULT line gives another).")
    if run.stale:
        doc.item(f"Last close older than {_stale_on(run)}: "
                 f"{', '.join(run.stale)}: valued at that close (PRICE marked "
                 f"{STALE_MARK}), and an option on it counts its years from "
                 f"it.")
    doc.item("Positions: the books' at their end"
             + (f" ({ctx['horizon']})" if ctx["horizon"] else "")
             + f"; prices: the closes of {run.valued} (the newest close of a "
             "listing or the benchmark"
             + (f"; a coin's: of {run.coins_valued}, the newest coin close, a "
                "coin trading every day" if run.coins_valued else "")
             + "), daily, adjusted for splits and dividends, from "
             + TC._sources(list(run.quotes.values()),
                           bh if ctx["bench"] and ctx["bench"][0]
                           not in run.quotes else None)
             + ". Each series moves in its own listing's currency; the rate "
             "to the base currency is held, and so is everything but the "
             "price: no time passes, the volatility and the rate stay.")
    if run.book.options:
        doc.item(VOLATILITY_NOTE)
    doc.item("Cash is not modelled, nor dividends, margin, or a holding's "
             "move apart from the index's.")
    doc.item("A view only: no tax figure reads it. Not advice: an estimate "
             "from past prices under the assumptions above.")


def _details_part(doc: _out.Doc, run: _Run, ctx: Dict[str, Any]) -> None:
    _by_account(doc, run)
    _options_part(doc, run)
    _history_part(doc, run)
    doc.section("NOTES")
    _notes(doc, run, ctx)
    doc.section("DEFINITIONS")
    doc.items(DEFINITIONS)


# A move is a number below 0 as often as not (`tjs stress -10`): a word of
# a minus and digits (`-10`, `-10%`, `-2.5`) is a positional, never an
# option, here. argparse's own pattern takes no `%`.
NEGATIVE_WORD = re.compile(r"^-[0-9.]+%?$")


def register(sub: argparse._SubParsersAction) -> None:
    """This module's command, for the `tjs` parser."""
    p = sub.add_parser(
        "stress",
        help="Holdings' estimated change for an index move",
        usage="%(prog)s [ACCOUNT[,ACCOUNT...]] [MOVE ...] [--benchmark SYMBOL] "
              "[--beta SYMBOL=X] [--rate R] [--details] [--json]\n"
              "       (the ACCOUNT and the MOVEs together, the options "
              "before or after them)",
        description="A crude estimate of what a move of the benchmark index "
                    "does to every position the books hold at their end (one "
                    "row a symbol, the accounts named or all), valued at the "
                    "newest closes: a share, unit or coin moves by its beta x "
                    "the move (a year of daily returns against the "
                    "benchmark), an option is re-priced with Black-Scholes at "
                    "the underlying's moved price, its volatility held. MOVE "
                    "is the index's move in %, -10 or -10% (default: -20 -10 "
                    "-5 5 10); the ACCOUNT and the moves go together, the "
                    "options before or after them. The closes come from "
                    "Yahoo Finance (the [fx] extra), cached in "
                    "work/.history_cache.json; with TAXJSON_OFFLINE set, "
                    "only the cache is read. Values in the base currency at "
                    "the latest rate. A view only: no tax figure reads it, "
                    "and it is not advice.")
    p._negative_number_matcher = NEGATIVE_WORD
    p.add_argument("account", nargs="*", action=_Words,
                   metavar="ACCOUNT|MOVE",
                   help="An account, or several as one comma list (first, "
                        "optional), then each index move in %%: -10 or -10%%")
    p.add_argument("--benchmark", metavar="SYMBOL", default=None,
                   help="The index or listing the move is in, spelled as the "
                        "books spell it (or as Yahoo spells an index); "
                        "default: [settings] benchmark")
    p.add_argument("--beta", metavar="SYMBOL=X", action="append", default=[],
                   help="A holding's beta, or an option's underlying's, "
                        "instead of the one from history, -10 to 10 "
                        "(repeatable)")
    p.add_argument("--rate", metavar="R", default=None,
                   help="The risk-free rate for the options, %% a year "
                        "(default 0)")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of text")
    # ACCOUNT takes a comma list (lib/account_list; cli/common `_accounts`).
    p.set_defaults(func=cmd_stress, account_list=True, moves=[])
