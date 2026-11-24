"""The country a security is under on the T1135, and what decided it
(tax-logic CA-RPT-02, CA-RPT-21; backlog TJ-4957).

A share's country is decided in this order, the deciding step kept with
the answer (`Decision.source`):

  1. `override`: a ticker.map `T1135 SYMBOL COUNTRY` line, on the symbol
     or on another symbol of the same security through its ticker
     changes (before or after the change);
  2. `isin` / `tobase_map`: the issuer's data. The first two letters of
     the ISIN an export gives the security (a row's `isin`, or its
     `issuer_country`), and tobase.map's issuer country. Where the two
     disagree, or two ISINs do, nothing is picked: REVIEW. A prefix that
     is no country's (`XS`, `EU`) is REVIEW too;
  4. `learned`: a country step 2 gave the security in one account, for
     the accounts that hold the same security with no ISIN of their
     own. Worked out from the run's books each time; nothing is saved;
  5. `assumed`: the listing's suffix.

(Step 3 of the proposal, a public lookup, is not built: nothing about
the holdings leaves the machine.)

"The same security" is what guards a ticker another company uses later.
The books' rows are walked once (`Holdings`): a *leg* is one unbroken
holding of a symbol over every account of the project (it ends when, at
the end of a day, the taxable accounts' pool is flat and no registered
account holds it; a transfer out followed by a transfer in within
TRANSFER_BRIDGE_DAYS aside), and a ticker change
joins the old symbol's leg to the new one's when it is a rename of one
security: the same ISIN where both sides carry one, else a change that
merges into no position already held. A merger into shares already held,
and a ticker bought again after it was sold or renamed away, are other
securities: neither an ISIN nor a T1135 line crosses to them.

An ISIN is read for the report's year: the rows of the year when they
carry one, else the last before it, else the first after (an issuer
that moved keeps each year's country).

No network, no file written: `tjs t1135` reads this from the books of
the run.
"""
import re
from datetime import date as _date
from typing import (Any, Callable, Dict, FrozenSet, Iterable, List, Optional,
                    Set, Tuple)

REVIEW = "??"
CAN = "CAN"

# What decided a country (`Decision.source`, the report's
# `country_source`).
OVERRIDE = "override"
ISIN = "isin"
TOBASE = "tobase_map"
LEARNED = "learned"
ASSUMED = "assumed"
# Not a share: an option or a futures contract keeps its own listing's
# country (the rule, not an assumption); crypto has no listing; a suffix
# no market has is unclassified.
LISTING = "listing"
CRYPTO = "crypto"
UNCLASSIFIED = "unclassified"

# Why a security is named for review (`Decision.review`).
NO_COUNTRY = "isin_no_country"          # XS, EU: a prefix no country has
DISAGREE = "sources_disagree"           # two ISINs, or an ISIN and tobase.map
LINES_DISAGREE = "lines_disagree"       # two T1135 lines of one security
REUSED = "ticker_reused"                # two securities, one ticker, one year
RECEIPT = "depositary_receipt"
EARLIER = "earlier_holding"             # another holding's ISIN says otherwise
EARLIER_COUNTRY = "earlier_country"     # ... names another foreign country

# The quantity under which a position is flat, and how long after a
# transfer out a transfer in still continues the same holding.
EPS = 1e-6
TRANSFER_BRIDGE_DAYS = 31

_MOVES = ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER", "SPLIT")

# A security name that says "depositary receipt": the words, or ADR,
# ADS, GDR or CDR standing alone in an upper-case name ("SAMPLE HOLDING
# SPON ADR"), a broker's "-CDR" / "-ADR" at a name's end included
# ("SAMPLE PLATFORMS INC-CDR", "SAMPLE INC-CDR (CAD HEDGED)": how
# Interactive Brokers names one; then the end or punctuation, never
# another word: "SAMPLE-ADR HOLDINGS" is a name).
# Never the letters as the start of a hyphenated word or inside one:
# "ADS-TEC ENERGY" and "CADRE" are names.
_RECEIPT_WORDS = re.compile(r"DEPOSIT[AO]RY\s+(?:RECEIPT|SHARE)", re.I)
_RECEIPT_ABBR = re.compile(r"(?:(?:^|[\s/(])(?:ADR|ADS|GDR|CDR)S?"
                           r"(?=$|[\s/).,])"
                           r"|-(?:ADR|ADS|GDR|CDR)S?(?=\s*(?:$|[().,/])))")
_RECEIPT_TYPES = frozenset({"ADR", "ADS", "GDR", "CDR", "NVDR", "DR"})


def is_share(symbol: str) -> bool:
    """A listed security that is not an option or a futures contract:
    what an issuer's country is decided for."""
    from taxjson.lib.futures import is_plain_future
    from taxjson.lib.option_symbols import is_option_symbol
    return (bool(symbol) and "." in symbol and "@" not in symbol
            and not is_option_symbol(symbol) and not is_plain_future(symbol))


def says_receipt(row: Dict[str, Any]) -> bool:
    """Whether a row's export calls its security a depositary receipt:
    its instrument type (`security_type`, IB's `Type`) or its name
    (`security_name`)."""
    if str(row.get("security_type") or "").strip().upper() in _RECEIPT_TYPES:
        return True
    name = str(row.get("security_name") or "")
    return bool(_RECEIPT_WORDS.search(name) or _RECEIPT_ABBR.search(name))


def prefix_country(prefix: str) -> Optional[str]:
    """The country an ISIN's first two letters name: ISO alpha-3 (CAN
    for Canada), None when they are no country's (`XS`, `EU`)."""
    from taxjson.lib.t1135_country import iso3_of
    p = str(prefix or "").strip().upper()
    if p in ("CA", "CAN"):
        return CAN
    return iso3_of(p) if len(p) in (2, 3) else None


def country_word(country: Optional[str]) -> str:
    """A decided country as a T1135 line writes it (`CA`: not foreign
    property)."""
    return "CA" if country is None else country


class Evidence:
    """One row's word on its security's issuer: the ISIN (or only its
    country letters), the account whose export gave it, the day."""
    __slots__ = ("date", "account", "isin", "prefix")

    def __init__(self, date: str, account: str, isin: str,
                 prefix: str) -> None:
        self.date, self.account = date, account
        self.isin, self.prefix = isin, prefix


class Leg:
    """One unbroken holding of `symbol` over every account: from `start`
    to `end` (None while held). `marks`: {account: [(day, its units
    after the day)]}; `receipt`: an export calls it a depositary
    receipt."""
    __slots__ = ("n", "symbol", "start", "end", "evidence", "marks",
                 "receipt", "bridge")

    def __init__(self, n: int, symbol: str, start: str) -> None:
        self.n, self.symbol, self.start = n, symbol, start
        self.end: Optional[str] = None
        self.evidence: List[Evidence] = []
        self.marks: Dict[str, List[Tuple[str, float]]] = {}
        self.receipt = False
        self.bridge = False         # ended by a transfer out

    def in_year(self, year: int) -> bool:
        return (self.start <= f"{year}-12-31"
                and (self.end is None or self.end >= f"{year}-01-01"))

    def isins(self) -> Set[str]:
        return {e.isin for e in self.evidence if e.isin}

    def present(self, year: int) -> Set[str]:
        """The accounts that held it, or had a row of it, in `year`."""
        a, b = f"{year}-01-01", f"{year}-12-31"
        out: Set[str] = {e.account for e in self.evidence
                         if a <= e.date <= b}
        for acct, marks in self.marks.items():
            before = [q for d, q in marks if d < a]
            if any(a <= d <= b for d, _q in marks) \
                    or (before and before[-1] > EPS):
                out.add(acct)
        return out


def _days_apart(a: str, b: str) -> Optional[int]:
    try:
        return abs((_date.fromisoformat(b[:10])
                    - _date.fromisoformat(a[:10])).days)
    except ValueError:
        return None


def _number(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


class Holdings:
    """The books' shares as legs and securities (the module docstring):
    `legs`, `by_symbol` ({symbol: its legs, in order}), `edges` (the
    ticker changes: (old leg, new leg, day, "rename" or "merge")), and
    `security(leg)`: the legs that are one security with it.

    `rows`: the taxable accounts' rows. Canada pools them, and they
    carry no transfer between two of them, so shares bought in one and
    sold in another are flat only as a pool: a symbol is held by them
    while the pool is long. The pool's quantity is the engine's own
    where its cost timeline is given (`timeline`, lib/cost_timeline:
    its splits, ticker changes and cut-off openings are the engine's),
    else the accounts' units summed, a split scaling the units of the
    account whose row it is. `sheltered`: the registered accounts'
    rows, each account on its own, its units never below zero (a sale
    of shares bought before its file begins takes nothing from anyone)
    and scaled by its own split rows only. Where a registered account's
    books lack a split, or its later exports are missing, its units
    are stale and its holding never ends. So a registered account
    alone never carries a leg over to the taxable accounts: where the
    taxable pool was flat and it is long again, a new leg begins that
    day, whatever a registered account still holds. The registered
    account's ISIN from before is then another holding's: not learned,
    and kept in play where it says otherwise on being foreign property
    (Countries, `earlier_holding`). Its rows dated while the taxable
    accounts hold the symbol are the same leg's, and are read."""

    def __init__(self, rows: Iterable[Dict[str, Any]],
                 date_of: Callable[[Dict[str, Any]], str],
                 sheltered: Iterable[Dict[str, Any]] = (),
                 timeline: Optional[Dict[str, Any]] = None) -> None:
        self.legs: List[Leg] = []
        self.by_symbol: Dict[str, List[Leg]] = {}
        self.edges: List[Tuple[Leg, Leg, str, str]] = []
        self._open: Dict[str, Leg] = {}
        # {(account, symbol): units}: the taxable accounts' (they sum
        # to the pool), the registered accounts' (each its own).
        self._acct: Dict[Tuple[str, str], float] = {}
        self._reg: Dict[Tuple[str, str], float] = {}
        # {symbol: the day from which only a registered account holds
        # its open leg}.
        self._bridged: Dict[str, str] = {}
        both = [(t, False) for t in rows] + [(t, True) for t in sheltered]
        dated = sorted(((date_of(t), i, t, reg)
                        for i, (t, reg) in enumerate(both)
                        if isinstance(t, dict)), key=lambda x: (x[0], x[1]))
        self._pool = self._pool_days(timeline)
        self._walk(dated)
        self._place_evidence(dated)
        self._join()

    # ------------------------------------------------------------ the walk

    @staticmethod
    def _pool_days(timeline: Optional[Dict[str, Any]]
                   ) -> Dict[str, Tuple[List[str], List[float]]]:
        """{symbol: (days, the taxable pool's quantity at each day's
        end)} from the engine's cost timeline (a symbol's accounts
        summed: Canada's are one, account "")."""
        if not timeline:
            return {}
        from taxjson.lib import cost_timeline as _CT
        last: Dict[str, Dict[str, Dict[str, float]]] = {}
        state: Dict[str, Dict[str, float]] = {}
        for r in _CT.rows(timeline):
            sym, day = str(r.get("symbol") or ""), str(r.get("date") or "")
            if not sym or not day:
                continue
            per = state.setdefault(sym, {})
            per[str(r.get("account") or "")] = _number(r.get("qty"))
            last.setdefault(sym, {})[day] = dict(per)
        return {sym: (sorted(days),
                      [sum(days[d].values()) for d in sorted(days)])
                for sym, days in last.items()}

    def _leg(self, symbol: str, day: str) -> Leg:
        leg = Leg(len(self.legs), symbol, day)
        self.legs.append(leg)
        self.by_symbol.setdefault(symbol, []).append(leg)
        return leg

    def _long(self, symbol: str, day: str) -> bool:
        """Whether `symbol` is held at the end of `day`: the taxable
        pool long, or some registered account."""
        if any(q > EPS for (_a, s), q in self._reg.items() if s == symbol):
            return True
        return self._pool_long(symbol, day)

    def _pool_long(self, symbol: str, day: str) -> bool:
        """Whether the taxable accounts' pool of `symbol` is long at
        the end of `day`."""
        if symbol in self._pool:
            from bisect import bisect_right
            days, qty = self._pool[symbol]
            k = bisect_right(days, day) - 1
            return k >= 0 and qty[k] > EPS
        return sum(q for (_a, s), q in self._acct.items()
                   if s == symbol) > EPS

    def _walk(self, dated: List[Tuple[str, int, Dict[str, Any], bool]]
              ) -> None:
        i = 0
        while i < len(dated):
            day = dated[i][0]
            j = i
            touched: Dict[str, Dict[str, Any]] = {}
            while j < len(dated) and dated[j][0] == day:
                self._row(dated[j][2], day, touched, dated[j][3])
                j += 1
            for sym, how in touched.items():
                self._end_of_day(sym, day, how)
            i = j

    @staticmethod
    def _how() -> Dict[str, Any]:
        """What a day did to a symbol: a transfer in, a transfer out,
        the accounts whose units moved."""
        return {"in": False, "out": False, "accounts": set()}

    def _row(self, t: Dict[str, Any], day: str,
             touched: Dict[str, Dict[str, Any]], reg: bool) -> None:
        from taxjson.lib.corporate_timeline import normalize_symbol_new
        action = str(t.get("action") or "").upper()
        sym = str(t.get("symbol") or "")
        if action not in _MOVES or not day or not is_share(sym):
            return
        acct = str(t.get("account") or "")
        units = self._reg if reg else self._acct
        how = touched.setdefault(sym, self._how())
        if action == "SPLIT":
            ratio = _number(t.get("quantity"))
            new = normalize_symbol_new(sym, t.get("symbol_new"))
            if new and is_share(new):
                self._rename(sym, new, day, ratio if ratio > 0 else 1.0,
                             acct if reg else None)
                touched.setdefault(new, self._how())
            elif not new and ratio > 0 and (acct, sym) in units:
                # A split with no ticker change: its own account's
                # units, never another's (each has its own row, or the
                # engine's pool says).
                units[(acct, sym)] *= ratio
                how["accounts"].add(acct)
            return
        q = units.get((acct, sym), 0.0) + _number(t.get("quantity"))
        units[(acct, sym)] = max(q, 0.0) if reg else q
        how["accounts"].add(acct)
        if action == "TRANSFER":
            how["in" if _number(t.get("quantity")) > 0 else "out"] = True

    def _rename(self, old: str, new: str, day: str, ratio: float,
                reg: Optional[str]) -> None:
        """A ticker change: the old symbol's units, and its leg, go to
        the new symbol (a registered account's row moves its own units;
        a taxable one's every taxable account's: the pool's). Into a
        symbol with no position it is a rename; into one already held,
        a merger."""
        units = self._acct if reg is None else self._reg
        moved = [a for a, s in units if s == old and reg in (None, a)]
        for a in moved:
            u = units.pop((a, old)) * ratio
            units[(a, new)] = units.get((a, new), 0.0) + u
        was = self._open.get(old)
        if was is None:
            return
        held = self._open.get(new)
        to = held or self._leg(new, day)
        self._open[new] = to
        if not any(a is was and b is to for a, b, _d, _k in self.edges):
            self.edges.append((was, to, day, "merge" if held else "rename"))
        if not self._long(old, day):
            # (Still held under the old symbol where a registered
            # account's own row of the change comes on a later day.)
            self._open.pop(old)
            was.end = day
        for a in moved:
            was.marks.setdefault(a, []).append((day, 0.0))
            to.marks.setdefault(a, []).append((day, units[(a, new)]))

    def _end_of_day(self, sym: str, day: str, how: Dict[str, Any]) -> None:
        flat = not self._long(sym, day)
        leg = self._open.get(sym)
        taxable = self._pool_long(sym, day)
        if leg is not None and taxable and sym in self._bridged:
            # The taxable accounts buy what only a registered account
            # held: a new leg (the class docstring); the old one ended,
            # for them, the day their pool went flat.
            leg.end, leg.bridge = max(self._bridged[sym], leg.start), False
            self._open.pop(sym)
            leg = None
        if taxable or flat:
            self._bridged.pop(sym, None)
        else:
            self._bridged.setdefault(sym, day)
        if leg is None and not flat:
            last = (self.by_symbol.get(sym) or [None])[-1]
            gap = _days_apart(last.end, day) if last and last.end else None
            if (last is not None and last.bridge and how.get("in")
                    and gap is not None and gap <= TRANSFER_BRIDGE_DAYS):
                leg = last                  # the transfer arrives
                leg.end, leg.bridge = None, False
            else:
                leg = self._leg(sym, day)
            self._open[sym] = leg
        if leg is None:
            return
        for acct in sorted(how["accounts"]):
            leg.marks.setdefault(acct, []).append(
                (day, self._reg.get((acct, sym),
                                    self._acct.get((acct, sym), 0.0))))
        if flat:
            leg.end, leg.bridge = day, bool(how.get("out"))
            self._open.pop(sym, None)

    # -------------------------------------------------------- the evidence

    def _place_evidence(self,
                        dated: List[Tuple[str, int, Dict[str, Any], bool]]
                        ) -> None:
        """Each row's ISIN (or issuer letters) and receipt word, on the
        leg of its symbol that began last before it: a dividend paid
        after the sale is the sold holding's."""
        for day, _i, t, _reg in dated:
            sym = str(t.get("symbol") or "")
            if not is_share(sym):
                continue
            # One ISIN (`isin`), or the several a statement gives one
            # ticker on one market (`isins`, a list).
            one = str(t.get("isin") or "").strip().upper()
            more = t.get("isins") if isinstance(t.get("isins"), list) else []
            isins = [one] if one else [str(x).strip().upper()
                                       for x in more if str(x).strip()]
            said = [(x, x[:2]) for x in isins] or [
                ("", str(t.get("issuer_country") or "").strip().upper())]
            said = [(x, pre) for x, pre in said if pre]
            receipt = says_receipt(t)
            if not said and not receipt:
                continue
            legs = self.by_symbol.get(sym)
            if not legs:
                legs = [self._leg(sym, day)]
                legs[0].end = day
            leg = ([x for x in legs if x.start <= day] or legs[:1])[-1]
            for isin, prefix in said:
                leg.evidence.append(Evidence(
                    day, str(t.get("account") or ""), isin, prefix))
            leg.receipt = leg.receipt or receipt

    # ------------------------------------------------------ the securities

    def _join(self) -> None:
        """Union the legs a ticker change makes one security: a rename
        unless both sides carry ISINs and share none; a merger only when
        both carry one they share."""
        root = list(range(len(self.legs)))

        def find(n: int) -> int:
            while root[n] != n:
                root[n] = root[root[n]]
                n = root[n]
            return n
        self.kept: List[Tuple[Leg, Leg]] = []
        for old, new, _day, kind in self.edges:
            a, b = old.isins(), new.isins()
            same = bool(a & b)
            if (kind == "rename" and not (a and b and not same)) \
                    or (kind == "merge" and same):
                self.kept.append((old, new))
                root[find(old.n)] = find(new.n)
        groups: Dict[int, List[Leg]] = {}
        for leg in self.legs:
            groups.setdefault(find(leg.n), []).append(leg)
        self._sec = {leg.n: frozenset(g) for g in groups.values()
                     for leg in g}

    def security(self, leg: Leg) -> FrozenSet[Leg]:
        return self._sec[leg.n]

    def securities(self, symbol: str, year: int) -> List[FrozenSet[Leg]]:
        """The securities `symbol` names in `year`: those with a leg of
        it in the year; with none, the one it last named before the
        year's end, else the first it ever named. None for a symbol the
        books never held."""
        legs = self.by_symbol.get(symbol) or []
        mine = [x for x in legs if x.in_year(year)]
        if not mine and legs:
            mine = ([x for x in legs if x.start <= f"{year}-12-31"]
                    or legs[:1])[-1:]
        out: List[FrozenSet[Leg]] = []
        for leg in mine:
            if self.security(leg) not in out:
                out.append(self.security(leg))
        return out

    def in_year(self, symbol: str, year: int) -> bool:
        """Whether `symbol` was held, or its rows speak, in `year`."""
        a, b = f"{year}-01-01", f"{year}-12-31"
        return any(x.in_year(year) or any(a <= e.date <= b
                                          for e in x.evidence)
                   for x in self.by_symbol.get(symbol) or [])

    def shared(self, symbol: str) -> bool:
        """Whether more than one security ever had `symbol`."""
        return len({self.security(x)
                    for x in self.by_symbol.get(symbol) or []}) > 1


class Decision:
    """A symbol's country for the report's year: `country` (ISO
    alpha-3, None for not foreign property, REVIEW when nothing may be
    picked) and `source`, what decided it. `account`: whose export gave
    the ISIN (`isin`, `learned`); `also`: the accounts that hold it and
    take the country from that ISIN; `prefix`: the ISIN's letters;
    `line`: the T1135 line (`override`); `listing`: what the listing
    alone says; `review`: why it is named for review; `given`: what the
    sources that disagree each say (CAN, ISO alpha-3)."""
    __slots__ = ("symbol", "country", "source", "account", "also",
                 "prefix", "line", "listing", "review", "given", "symbols",
                 "other")

    def __init__(self, symbol: str, country: Optional[str], source: str,
                 listing: Optional[str] = None, **kw: Any) -> None:
        self.symbol, self.country, self.source = symbol, country, source
        self.listing = listing
        self.account: str = kw.get("account", "")
        self.also: List[str] = kw.get("also", [])
        self.prefix: str = kw.get("prefix", "")
        self.line: str = kw.get("line", "")
        self.review: str = kw.get("review", "")
        self.given: List[str] = kw.get("given", [])
        self.symbols: List[str] = kw.get("symbols", [symbol])
        # EARLIER: the other holdings whose ISIN says otherwise:
        # [{account, country, isin_country_code, first, last}].
        self.other: List[Dict[str, str]] = []

    def foreign(self) -> bool:
        return self.country is not None

    def facts(self) -> Dict[str, Any]:
        """The decision as the report's row keys."""
        out: Dict[str, Any] = {"country_source": self.source}
        if self.account:
            out["country_source_account"] = self.account
        if self.also:
            out["country_learned_accounts"] = list(self.also)
        if self.prefix:
            out["isin_country_code"] = self.prefix
        if self.line:
            out["country_line"] = self.line
        if self.review:
            out["country_review"] = self.review
        if self.other:
            out["country_earlier"] = [dict(e) for e in self.other]
        return out


def _for_year(ev: List[Evidence], year: int) -> List[Evidence]:
    """The evidence that speaks for `year`: its own rows'; with none,
    the last day's before it and the first day's after it, both: a
    year between two whose ISINs name different countries is theirs to
    disagree on (REVIEW), never the earlier one's."""
    a, b = f"{year}-01-01", f"{year}-12-31"
    inside = [e for e in ev if a <= e.date <= b]
    if inside:
        return inside
    last = max((e.date for e in ev if e.date < a), default=None)
    first = min((e.date for e in ev if e.date > b), default=None)
    return [e for e in ev if e.date in (last, first)]


class Countries:
    """The country of every symbol of the books for `year` (the module
    docstring). `lines`: the ticker.map T1135 lines ({symbol: ISO
    alpha-3, None for not foreign property}); `tobase`: tobase.map's
    issuer countries ({symbol: ISO alpha-3}); `by_listing(symbol)`: what
    the listing's suffix alone says."""

    def __init__(self, holdings: Holdings, year: int,
                 lines: Dict[str, Optional[str]], tobase: Dict[str, str],
                 by_listing: Callable[[str], Optional[str]]) -> None:
        self.h, self.year = holdings, year
        self.lines, self.tobase = dict(lines), dict(tobase)
        self.by_listing = by_listing
        self._seen: Dict[str, Decision] = {}
        self._next: Dict[int, List[Leg]] = {}
        self._prev: Dict[int, List[Leg]] = {}
        for old, new in holdings.kept:
            self._next.setdefault(old.n, []).append(new)
            self._prev.setdefault(new.n, []).append(old)

    def with_lines(self, more: Dict[str, Optional[str]]) -> "Countries":
        """The same books with the T1135 lines `more` added: what the
        report would read once they are in ticker.map."""
        return Countries(self.h, self.year, {**self.lines, **more},
                         self.tobase, self.by_listing)

    def overrides(self, symbols: Iterable[str]) -> Dict[str, Optional[str]]:
        """What the cost walk classifies by: every share of `symbols`
        under its decided country (an assumed one too, so the walk's
        own carry over a ticker change never moves a line across a
        merger), and each T1135 line as written (an option's, a coin's,
        one that names no symbol of the books)."""
        out: Dict[str, Optional[str]] = dict(self.lines)
        for s in symbols:
            if is_share(s):
                out[s] = self.decide(s).country
        return out

    def decide(self, symbol: str) -> Decision:
        if symbol not in self._seen:
            self._seen[symbol] = self._decide(symbol)
        return self._seen[symbol]

    def _decide(self, symbol: str) -> Decision:
        listing = self.by_listing(symbol)
        if symbol in self.lines:
            c = self.lines[symbol]
            return Decision(symbol, c, OVERRIDE, listing,
                            line=f"T1135 {symbol} {country_word(c)}")
        if not is_share(symbol):
            return Decision(symbol, listing,
                            CRYPTO if "." not in symbol
                            else UNCLASSIFIED if listing == REVIEW
                            else LISTING, listing)
        secs = self.h.securities(symbol, self.year) or [frozenset()]
        each = [self._security(sec, symbol, listing) for sec in secs]
        if len({d.country for d in each}) == 1:
            return self._other_holdings(each[0], secs)
        # One ticker, two securities in the year (bought again after its
        # change), and they do not agree: nothing is picked.
        return Decision(symbol, REVIEW, each[0].source, listing,
                        review=REUSED,
                        given=sorted({CAN if d.country is None else d.country
                                      for d in each}))

    def _other_holdings(self, d: Decision, secs: List[FrozenSet[Leg]]
                        ) -> Decision:
        """`d`, named EARLIER when another holding of the ticker (any
        account, any year: a leg of the symbol that is not this
        security's) carried an ISIN that says otherwise on being foreign
        property. The guard on a ticker another company uses later
        stops the country being learned; it does not make that ISIN
        nothing: both readings are walked (backlog TJ-5106). A T1135
        line, a security already open, and a receipt already walked are
        left as they are."""
        if d.source == OVERRIDE or d.country == REVIEW \
                or (d.review == RECEIPT and d.country is None):
            return d
        mine = {x.n for sec in secs for x in sec}
        found: Dict[Tuple[str, str], List[str]] = {}
        for leg in self.h.by_symbol.get(d.symbol) or []:
            if leg.n in mine:
                continue
            for e in leg.evidence:
                c = prefix_country(e.prefix)
                if c is not None and c != (d.country or CAN):
                    found.setdefault((e.account, c), [e.prefix]).append(
                        e.date)
        # On being foreign property: walked. Else (foreign under each,
        # another country): named, the total cannot move.
        walked = {k: v for k, v in found.items()
                  if (k[1] != CAN) != d.foreign()}
        if not walked and (not found or d.review):
            return d
        d.review = EARLIER if walked else EARLIER_COUNTRY
        d.given = sorted({c for _a, c in walked or found})
        d.other = [{"account": a, "country": c, "isin_country_code": v[0],
                    "first": min(v[1:]), "last": max(v[1:])}
                   for (a, c), v in sorted((walked or found).items())]
        return d

    # ------------------------------------------------------------- step 1

    def _chain_line(self, sec: FrozenSet[Leg], symbol: str
                    ) -> List[Tuple[Optional[str], str]]:
        """The T1135 lines that reach `symbol` through its security's
        ticker changes: the nearest earlier symbol's first (a line
        follows its symbol forward), else the nearest later one's (and
        back). [(country, the symbol the line is on)], more than one
        when the nearest lines disagree."""
        start = [x for x in sec if x.symbol == symbol]
        for edges in (self._prev, self._next):
            seen = {x.n for x in start}
            level = start
            while level:
                level = [y for x in level for y in edges.get(x.n, ())
                         if y.n not in seen]
                seen |= {y.n for y in level}
                hits = sorted({(country_word(self.lines[y.symbol]), y.symbol)
                               for y in level if y.symbol in self.lines})
                if hits:
                    words = {w for w, _s in hits}
                    keep = hits if len(words) > 1 else hits[:1]
                    return [(None if w == "CA" else w, s) for w, s in keep]
        return []

    # ----------------------------------------------------- steps 2, 4 and 5

    def _security(self, sec: FrozenSet[Leg], symbol: str,
                  listing: Optional[str]) -> Decision:
        names = sorted({x.symbol for x in sec} | {symbol})
        receipt = RECEIPT if any(x.receipt for x in sec) else ""
        lines = self._chain_line(sec, symbol)
        if len(lines) == 1:
            c, on = lines[0]
            return Decision(symbol, c, OVERRIDE, listing, symbols=names,
                            line=f"T1135 {on} {country_word(c)}")
        if lines:
            return Decision(symbol, REVIEW, OVERRIDE, listing, symbols=names,
                            review=LINES_DISAGREE,
                            given=sorted({CAN if c is None else c
                                          for c, _on in lines}))
        ev = _for_year([e for x in sec for e in x.evidence], self.year)
        by_isin = {prefix_country(e.prefix) for e in ev}
        by_map = {self.tobase[s] for s in names if s in self.tobase}
        given = sorted((by_isin - {None}) | by_map)
        prefixes = sorted({e.prefix for e in ev})
        if not given and not ev:
            return Decision(symbol, listing,
                            UNCLASSIFIED if listing == REVIEW else ASSUMED,
                            listing, symbols=names, review=receipt)
        if not given:
            return Decision(symbol, REVIEW, ISIN, listing, symbols=names,
                            review=NO_COUNTRY, prefix=prefixes[0],
                            account=sorted({e.account for e in ev})[0])
        if len(given) > 1:
            return Decision(symbol, REVIEW, ISIN if by_isin - {None}
                            else TOBASE, listing, symbols=names,
                            review=DISAGREE, given=given)
        country = None if given[0] == CAN else given[0]
        if given[0] not in by_isin:
            return Decision(symbol, country, TOBASE, listing, symbols=names,
                            review=receipt)
        gave = sorted({e.account for e in ev
                       if prefix_country(e.prefix) == given[0]})
        here: Set[str] = set()
        for x in sec:
            here |= x.present(self.year)
        own = [a for a in gave if a in here]
        return Decision(
            symbol, country, ISIN if own or not here else LEARNED, listing,
            symbols=names, review=receipt, account=(own or gave)[0],
            also=sorted(here - set(gave)) if own else [],
            prefix=sorted({e.prefix for e in ev
                           if prefix_country(e.prefix) == given[0]})[0])
