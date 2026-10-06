"""Two listings of one security joined by their transfer journal (tax-logic
CA-XLIST-01 / US-XLIST-01).

A broker that moves a position from one listing to another (a TSX line to
its NYSE line, a US-dollar line to the Canadian-dollar one) writes an
out-leg of the old symbol and an in-leg of the new one. With no ticker.map
rule joining them, the books held two securities: the old listing's
shares never sold, the new listing's sales read as a short, and the
superficial-loss / wash-sale walk saw a disposal and an acquisition.

`taxjson run` joins such a pair itself, as a ticker.map `TOBASE FROM TO`
line would, when the evidence is unambiguous:

* the legs pair uniquely: an out-leg of X and an in-leg of Y (X != Y) in
  your accounts (the same account, or two of yours: a move across
  brokers), the same quantity, dated within PAIR_DAYS of each other, and
  neither leg pairs with any other candidate (after the same-symbol legs
  cancel each other);
* the security names are EQUAL: some name the exports give X and some
  name they give Y are the same word for word once normalised
  (lib/symbol_codes.exact_name: case, punctuation, abbreviations, broker
  boilerplate and the generic share words set aside) — every share
  designator and the corporate form included: LP is not CORP, TRUST is
  not FUND, "QZCO CORP" is not "QZCO CORP CL B", "QZALPHA BANK" is not
  "QZALPHA BANK OF CANADA", and a word such as HEDGED must be on both
  sides too. No subset of words, no designator stated by one name only.
  And every other name either listing has states the same designators
  and corporate form (a listing also named "... CL B" never joins);
* no ticker.map rule renames or deletes X or Y (TOBASE / JOURNAL /
  GLOBAL / RENAME / DELETE, either side) and no DISTINCT line pairs the
  two: the user's map always wins, DISTINCT keeps them apart;
* neither symbol is joined to a third listing by another pair.

Everything else stays a suggestion (`taxjson ticker-map --suggest`) —
except two listings whose names name different companies (no leading
company word in common, companies_differ): never a join, never a TOBASE
suggestion. A `.US` symbol whose rows name two different companies, one
of them a Canadian-listed fund's US-dollar units (a TSX fund's US-dollar
unit booked `.US` beside an NYSE stock of the same root), is a SYMBOL
COLLISION (`collisions`): a Warning on the run's console and the EXTRACT
line (plus a JOURNAL) that gives the fund's rows their own symbol, never
a join through that symbol. The
joins are written to work/cross_listings.state (JSON) and appended, as
TOBASE lines, to the effective map the merge stages read
(work/ticker.map.effective: the project's ticker.map plus those lines).
A join changes the books, so the run says each one as a Warning naming
the pair and the ticker.map line that undoes it (`DISTINCT X Y`).
No security data is kept here: names, symbols and dates come from the
user's own exports.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

STATE = "cross_listings.state"
FORMAT = "cross_listings/1"
EFFECTIVE_MAP = "ticker.map.effective"
# How far apart (days) the out-leg and the in-leg of one journal may be.
PAIR_DAYS = 5
_EPS = 1e-6

# The comment line that opens the joined lines in the effective map.
EFFECTIVE_HEAD = ("# taxjson run: listings joined by their transfer journal "
                  "(work/cross_listings.state; a ticker.map rule naming "
                  "either symbol wins)")


@dataclass
class Leg:
    account: str
    broker: str
    symbol: str
    date: str
    quantity: float                 # signed: + in, - out
    used: bool = False


@dataclass
class Pair:
    out: Leg
    into: Leg
    frm: str = ""                   # TOBASE FROM
    to: str = ""                    # TOBASE TO
    reason: str = ""                # why not joined ("" when joined)
    names: Tuple[str, str] = ("", "")
    extra: Dict[str, Any] = field(default_factory=dict)

    def record(self) -> Dict[str, Any]:
        return {"from": self.frm, "to": self.to,
                "out": {"account": self.out.account,
                        "broker": self.out.broker,
                        "symbol": self.out.symbol, "date": self.out.date,
                        "quantity": round(-self.out.quantity, 8)},
                "in": {"account": self.into.account,
                       "broker": self.into.broker,
                       "symbol": self.into.symbol, "date": self.into.date,
                       "quantity": round(self.into.quantity, 8)},
                "names": list(self.names),
                **({"reason": self.reason} if self.reason else {})}


def _d(s: Any) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def _parsed_files(cache: Path, acct: str) -> List[Tuple[str, Path, bool]]:
    """(broker, path, is_sidecar) of an account's parsed exports."""
    from taxjson.lib.symbol_codes import _parser_ids
    files: List[Tuple[str, Path, bool]] = []
    for b in _parser_ids():
        if b in ("coinbase", "kraken"):
            continue
        files.append((b, cache / f"{acct}_{b}.json", False))
        files.append((b, cache / f"{acct}_{b}_transfers.json", True))
    for p in sorted(cache.glob(f"{acct}_generic-*.json")):
        stem = p.name[len(acct) + 1:-len(".json")]
        if stem.endswith("_transfers"):
            files.append((stem[:-len("_transfers")], p, True))
        else:
            files.append((stem, p, False))
    return files


@dataclass
class Row:
    """One parsed row of a share listing (or a broker code): what an
    EXTRACT line would match (its description and currency) and the
    security name it carries."""
    account: str
    broker: str
    symbol: str
    currency: str
    description: str
    key: Tuple[str, ...]            # symbol_codes.exact_name of its name
    action: str = ""
    name: str = ""                  # the name (symbol_codes._row_name)


def gather(cache: Path, accounts: Iterable[str],
           rows: Optional[List[Row]] = None
           ) -> Tuple[List[Leg], Dict[str, Set[Tuple[str, ...]]],
                      Dict[Tuple[str, ...], str]]:
    """(transfer legs, symbol -> normalised names (symbol_codes.
    exact_name), normalised name -> the name as written) from every
    account's parsed exports in work/. `rows`, when given, receives
    every share-listing and broker-code row (Row: the symbol-collision
    check, `collisions`)."""
    from taxjson.lib.symbol_codes import (_CONTROL_RE, _plain_listing,
                                          _row_name, exact_name, is_code)
    legs: List[Leg] = []
    names: Dict[str, Set[Tuple[str, ...]]] = {}
    shown: Dict[Tuple[str, ...], str] = {}
    all_accts = sorted(set(accounts), key=len, reverse=True)
    for acct in sorted(set(accounts)):
        longer = [o for o in all_accts if o != acct
                  and o.startswith(f"{acct}_")]
        for broker, p, sidecar in _parsed_files(cache, acct):
            if any(p.name.startswith(f"{o}_") for o in longer):
                continue                # a longer-named sibling's file
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                continue
            if not isinstance(doc, dict):
                continue
            md = doc.get("metadata") or {}
            if sidecar and (not isinstance(md, dict)
                            or md.get("kind") != "transfer_sidecar"
                            or md.get("account") != acct):
                continue
            txs = doc.get("transactions")
            for t in (txs if isinstance(txs, list) else []):
                if not isinstance(t, dict):
                    continue
                sym = str(t.get("symbol") or "").upper()
                if rows is not None and sym and (
                        _plain_listing(sym) or is_code(sym)):
                    rows.append(Row(acct, broker, sym,
                                    str(t.get("currency") or "").upper(),
                                    str(t.get("description") or ""),
                                    exact_name(_row_name(t, broker)),
                                    str(t.get("action") or "").upper(),
                                    _row_name(t, broker)))
                if not _plain_listing(sym) or is_code(sym):
                    continue
                toks = exact_name(_row_name(t, broker))
                if toks:
                    names.setdefault(sym, set()).add(toks)
                    shown.setdefault(toks, _CONTROL_RE.sub(
                        lambda m: "\\x%02x" % ord(m.group(0)),
                        " ".join(_row_name(t, broker).split())))
                if t.get("action") != "TRANSFER":
                    continue
                try:
                    q = float(t.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    continue
                if abs(q) <= _EPS or _d(t.get("date")) is None:
                    continue
                legs.append(Leg(acct, broker, sym,
                                str(t.get("date"))[:10], q))
    legs.sort(key=lambda g: (g.date, g.account, g.broker, g.symbol,
                             g.quantity))
    return legs, names, shown


def tobase_direction(out_sym: str, in_sym: str,
                     base_currency: Optional[str]) -> Tuple[str, str]:
    """(FROM, TO) of the TOBASE line joining two listings: the listing in
    another currency maps onto the base-currency one (the quote currency
    from the spelling: SAMPLF.U.TO is the USD unit); without a known
    base, the .US listing is taken as the foreign one."""
    from taxjson.lib.price_chain import quote_currency
    ca, cb = quote_currency(out_sym), quote_currency(in_sym)
    if base_currency and ca != cb and base_currency in (ca, cb):
        return (in_sym, out_sym) if ca == base_currency else (out_sym, in_sym)
    return (in_sym, out_sym) if in_sym.endswith(".US") else (out_sym, in_sym)


# _names_verdict: the two listings' names name different companies.
DIFFERENT = "the names name different companies"
# How many of a name's leading company words `lead_words` reads.
_LEAD = 3


def lead_words(key: Iterable[str]) -> frozenset:
    """The first company words of a name (an exact_name key, read again
    through symbol_codes.name_tokens: generic share words, corporate
    form, designators and transfer wording set aside), at most _LEAD
    strong ones (3+ letters, not a number). A broker's trailing
    boilerplate rarely reaches them: the name comes first."""
    from taxjson.lib.symbol_codes import name_tokens
    out: List[str] = []
    for w in name_tokens(" ".join(key)):
        if w.startswith("~") or len(w) < 3 or w.isdigit():
            continue
        out.append(w)
        if len(out) == _LEAD:
            break
    return frozenset(out)


def companies_differ(a: Iterable[str], b: Iterable[str]) -> bool:
    """Two names (exact_name keys) clearly name different companies:
    each has leading company words (lead_words) and they share none
    ("QZREALTY TRUST INC" vs "SAMPLEX US DLR CURRENCY ETF"). A shared
    word — the same issuer, a rebranded fund ("QZOLD U S DLR CURRENCY
    ETF" / "QZNEW US DLR CURRENCY ETF"), or a name too short to tell —
    is inconclusive, never "different"."""
    la, lb = lead_words(a), lead_words(b)
    return bool(la) and bool(lb) and not (la & lb)


def _close(a: Leg, b: Leg, days: int) -> bool:
    da, db = _d(a.date), _d(b.date)
    return bool(da and db and abs((da - db).days) <= days)


def _same_qty(a: Leg, b: Leg) -> bool:
    qa, qb = abs(a.quantity), abs(b.quantity)
    return abs(qa - qb) <= max(_EPS, 1e-6 * max(qa, qb))


def _names_verdict(nx: Set[Tuple[str, ...]], ny: Set[Tuple[str, ...]],
                   shown: Dict[Tuple[str, ...], str]) -> str:
    """"" when the two listings' names agree, else why not. Exact
    equality only (lib/symbol_codes.exact_name): some name of X must be
    some name of Y word for word — the corporate form and every share
    designator included — and no other name of either listing may state
    other designators or another corporate form than that shared name
    (symbol_codes.exact_marks). Anything less stays a suggestion."""
    from taxjson.lib.symbol_codes import exact_marks
    if nx and ny and all(companies_differ(a, b) for a in nx for b in ny):
        return DIFFERENT
    if not nx or not ny:
        return "no security name for " + ("either listing" if not nx
                                          and not ny else "one listing")
    both = sorted(nx & ny)
    if not both:
        a, b = min(nx), min(ny)
        return (f"the names are not equal word for word "
                f"({shown.get(a, ' '.join(a))!r} vs "
                f"{shown.get(b, ' '.join(b))!r})")
    marks = exact_marks(both[0])
    for n in sorted(nx | ny):
        if exact_marks(n) != marks:
            return (f"another name of the listings states another share "
                    f"or corporate form ({shown.get(n, ' '.join(n))!r} vs "
                    f"{shown.get(both[0], ' '.join(both[0]))!r})")
    return ""


def analyze(legs: List[Leg], names: Dict[str, Set[Tuple[str, ...]]],
            shown: Dict[Tuple[str, ...], str], *,
            map_named: Iterable[str] = (),
            map_distinct: Iterable[Iterable[str]] = (),
            base_currency: Optional[str] = None,
            days: int = PAIR_DAYS,
            collided: Iterable[str] = ()) -> Dict[str, List[Pair]]:
    """{"joined": [...], "suggested": [...]}: the cross-listing journals
    the legs show (module docstring). A pair whose names name different
    companies (companies_differ) is neither joined nor suggested; a pair
    with a `collided` symbol (one symbol, two companies: `collisions`)
    is left to the collision's EXTRACT line."""
    collided = {s.upper() for s in collided}
    named = {s.upper() for s in map_named}
    apart = {frozenset(x.upper() for x in pair) for pair in map_distinct}
    ins = [g for g in legs if g.quantity > 0]
    outs = [g for g in legs if g.quantity < 0]
    # 1. The same symbol's legs cancel (a custody move, a broker switch):
    #    the same quantity, the closest date first.
    cands = sorted(((abs((_d(o.date) - _d(i.date)).days), o.date, n, m)
                    for n, o in enumerate(outs) for m, i in enumerate(ins)
                    if o.symbol == i.symbol and _same_qty(o, i)
                    and _close(o, i, days)), key=lambda c: c[:3])
    for _gap, _dt, n, m in cands:
        if not outs[n].used and not ins[m].used:
            outs[n].used = ins[m].used = True
    # 2. Another symbol's leg: the journal fingerprint.
    links: Dict[int, List[int]] = {}
    back: Dict[int, List[int]] = {}
    for n, o in enumerate(outs):
        if o.used:
            continue
        for m, i in enumerate(ins):
            if (i.used or i.symbol == o.symbol or not _same_qty(o, i)
                    or not _close(o, i, days)):
                continue
            links.setdefault(n, []).append(m)
            back.setdefault(m, []).append(n)
    joined: List[Pair] = []
    suggested: List[Pair] = []
    for n, ms in sorted(links.items()):
        for m in ms:
            o, i = outs[n], ins[m]
            frm, to = tobase_direction(o.symbol, i.symbol, base_currency)
            nx, ny = names.get(o.symbol, set()), names.get(i.symbol, set())
            p = Pair(o, i, frm, to,
                     names=(shown.get(min(nx), "") if nx else "",
                            shown.get(min(ny), "") if ny else ""))
            if (o.symbol in named or i.symbol in named
                    or frozenset((o.symbol, i.symbol)) in apart):
                continue                # the user's map decides
            if o.symbol in collided or i.symbol in collided:
                continue                # separate the symbol first
            verdict = _names_verdict(nx, ny, shown)
            if verdict == DIFFERENT:
                continue                # two companies: no TOBASE line
            if len(ms) > 1 or len(back.get(m, ())) > 1:
                p.reason = "the legs pair with more than one other leg"
            else:
                p.reason = verdict
            (suggested if p.reason else joined).append(p)
    # One partner per symbol: a listing joined to two others is ambiguous.
    partners: Dict[str, Set[str]] = {}
    for p in joined:
        partners.setdefault(p.frm, set()).add(p.to)
        partners.setdefault(p.to, set()).add(p.frm)
    keep: List[Pair] = []
    for p in joined:
        if len(partners[p.frm]) > 1 or len(partners[p.to]) > 1:
            p.reason = "a listing pairs with two other listings"
            suggested.append(p)
        else:
            keep.append(p)
    return {"joined": keep, "suggested": suggested}


@dataclass
class Collision:
    """One book symbol whose exports name two (or more) different
    companies: the rows of one of them (`odd`) get their own symbol with
    an EXTRACT line."""
    symbol: str
    names: List[str]                # one name per company, as written
    where: List[str]                # "account (broker)" per company
    odd: str                        # the name of the rows to move
    extract: str                    # the EXTRACT line
    template: bool                  # extract has a placeholder to edit
    journal: str = ""               # JOURNAL line for the moved rows
    why: str = ""                   # how the target / words were found

    def record(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "names": self.names,
                "where": self.where, "odd": self.odd,
                "extract": self.extract, "template": self.template,
                "journal": self.journal, "why": self.why}


# The placeholder of a template EXTRACT line (the user edits it).
PLACEHOLDER_WORDS = "<words that name it>"
# The fewest words a suggested EXTRACT names (a shorter phrase, such as
# one generic word, may match another security's row later).
_MIN_WORDS = 3


def _groups(keys: Iterable[Tuple[str, ...]]) -> List[Set[Tuple[str, ...]]]:
    """The names, grouped by company: two names whose companies are not
    clearly different (companies_differ) are one company, and so is
    anything linked through them."""
    keys = sorted(set(keys))
    up = {k: k for k in keys}

    def top(k):
        while up[k] != k:
            k = up[k]
        return k
    for n, a in enumerate(keys):
        for b in keys[n + 1:]:
            if not companies_differ(a, b):
                up[top(a)] = top(b)
    out: Dict[Tuple[str, ...], Set[Tuple[str, ...]]] = {}
    for k in keys:
        out.setdefault(top(k), set()).add(k)
    return sorted(out.values(), key=lambda g: sorted(g))


def extract_words(target: List[str], others: List[str],
                  min_words: int = _MIN_WORDS,
                  seeds: Iterable[str] = ()) -> Optional[str]:
    """The shortest run of whole words (at least `min_words`, or the
    whole description when shorter, with one strong word) that EVERY
    description of `target` carries and NO description of `others` does
    — tested with the matcher an EXTRACT line uses
    (base.extract_words_match), so the line moves exactly those rows. The
    run is taken from the security's names first (`seeds`: the names the
    brokers' wording was cut from, symbol_codes.rbc_name /
    questrade_name — no dealer wording in them), shortest first, else
    from the shortest target description; among equally short runs the
    one with the fewest one- or two-letter words, then the first. None
    when no run qualifies."""
    from taxjson.lib.brokerages.base import extract_words_match
    descs = sorted({" ".join(d.split()) for d in target if d.strip()},
                   key=lambda d: (len(d), d))
    if not descs:
        return None
    others = [d for d in {" ".join(o.split()) for o in others} if d]
    sources = [x.split() for x in sorted(
        {" ".join(x.split()) for x in seeds if x.strip()},
        key=lambda d: (len(d), d))] + [descs[0].split()]
    longest = max(len(w) for w in sources)
    for size in range(1, longest + 1):
        for words in sources:
            n = len(words)
            if size > n or size < min(min_words, n):
                continue
            # Fewest short words first ("DLR CURRENCY ETF" before "U S
            # DLR": a one- or two-letter word is the spelling brokers
            # vary), then the earliest.
            spans = sorted(range(0, n - size + 1), key=lambda i: (
                sum(len(w) < 3 for w in words[i:i + size]), i))
            for i in spans:
                run = words[i:i + size]
                cand = " ".join(run)
                if (not any(len(w) >= 3 and w.isalpha() for w in run)
                        or "|" in cand or "#" in cand):
                    continue
                if (all(extract_words_match(cand, d) for d in descs)
                        and not any(extract_words_match(cand, o)
                                    for o in others)):
                    return cand
    return None


def _display(group: Iterable[Tuple[str, ...]],
             shown: Dict[Tuple[str, ...], str]) -> str:
    """A company's name as one of its rows writes it: the shortest (the
    one with the least broker wording)."""
    return min((shown.get(k) or " ".join(k) for k in group),
               key=lambda t: (len(t), t))


def collisions(rows: List[Row], names: Dict[str, Set[Tuple[str, ...]]],
               shown: Dict[Tuple[str, ...], str], legs: List[Leg], *,
               base_currency: Optional[str] = None,
               days: int = PAIR_DAYS) -> List[Collision]:
    """Book symbols that carry two securities: a `.US` symbol whose trade
    / transfer rows name two clearly different companies
    (companies_differ), the rows of one of them reading as a
    Canadian-listed fund's US-dollar units (markets.USD_UNITS_RE: "... U
    S DLR CURRENCY ETF", "... USD UNITS") and the other's not — a TSX
    fund's US-dollar unit booked `.US` beside an NYSE stock of the same
    root. Names alone never decide it: a company that renamed itself
    (two names, one security) is common, so without that positive
    evidence there is no collision. For each:

    * the rows to move: that fund's; their target the TSX unit class
      (markets.usd_unit_listing, ROOT.U.TO);
    * the words: extract_words over the descriptions of every row of the
      fund in the project (any broker, any symbol whose leading company
      words share two with it), none of another security's — a template
      with a placeholder when no run qualifies;
    * a transfer journal pairing the symbol with another listing of the
      fund adds `JOURNAL <target> <listing>` (tobase_direction).
    """
    from taxjson.lib.markets import (USD_UNITS_RE, strip_listing_suffix,
                                     usd_unit_listing)
    from taxjson.lib.symbol_codes import _broker_name
    out: List[Collision] = []
    for sym in sorted({r.symbol for r in rows}):
        if not sym.endswith(".US"):
            continue
        mine = [r for r in rows if r.symbol == sym and r.key
                and r.action in ("BUYSELL", "TRANSFER")]
        groups = _groups(r.key for r in mine)
        if len(groups) < 2:
            continue
        root = strip_listing_suffix(sym)
        info = []
        for g in groups:
            lead = frozenset().union(*(lead_words(k) for k in g))
            g_rows = [r for r in rows if r.symbol == sym and r.key in g]
            # Every row of this security in the project: its own rows
            # under the symbol, and any listing's or broker code's rows
            # whose leading words share two with it.
            fam = g_rows + [r for r in rows if r.symbol != sym
                            and r.key and len(lead_words(r.key) & lead) >= 2]
            units = (all(r.currency == "USD" for r in g_rows)
                     and any(USD_UNITS_RE.search(r.description)
                             for r in g_rows))
            info.append((g, g_rows, fam, lead, units))
        odd = [x for x in info if x[4]]
        if not odd or len(odd) == len(info):
            continue
        target = usd_unit_listing(root)
        sh = [_display(x[0], shown) for x in info]
        wh = [", ".join(sorted({f"{r.account} at {_broker_name(r.broker)}"
                                for r in x[1]})) or "?"
              for x in info]
        for g, g_rows, fam, lead, _units in odd:
            fam_ids = {id(r) for r in fam}
            others = [r.description for r in rows if id(r) not in fam_ids]
            words = extract_words([r.description for r in fam], others,
                                  seeds=[r.name for r in fam])
            why = (f"its rows read as a Canadian-listed fund's US-dollar "
                   f"units: {target}")
            if words is None:
                why += ("; no run of words is common to its descriptions "
                        "and absent from every other row's: replace the "
                        "placeholder with words that name it")
            line = f"EXTRACT {words or PLACEHOLDER_WORDS} | USD | {target}"
            partners = set()
            for o in legs:
                for i in legs:
                    if (o.quantity < 0 < i.quantity and _same_qty(o, i)
                            and _close(o, i, days)
                            and sym in (o.symbol, i.symbol)
                            and o.symbol != i.symbol):
                        p = i.symbol if o.symbol == sym else o.symbol
                        if (p != target and any(
                                len(lead_words(k) & lead) >= 2
                                for k in names.get(p, ()))):
                            partners.add(p)
            journal = ""
            if len(partners) == 1:
                frm, to = tobase_direction(target, partners.pop(),
                                           base_currency)
                journal = f"JOURNAL {frm} {to}"
            out.append(Collision(sym, sh, wh, _display(g, shown), line,
                                 words is None, journal, why))
    return out


def collision_note(c: Collision) -> Tuple[str, List[str]]:
    """(headline, details) of the run's Warning for one collision."""
    named = "; ".join(f"{n!r} in {w}" for n, w in zip(c.names, c.where))
    det = [f"One symbol for {len(c.names)} securities: their rows share "
           f"one pool and one security for the loss rules until the "
           f"rows of {c.odd!r} get their own symbol:",
           f"  {c.extract}"]
    if c.journal:
        det.append(f"  {c.journal}")
    if c.template:
        det.append(f"The line has a placeholder to edit first ({c.why}).")
    return (f"{c.symbol} names two securities: {named} — add the EXTRACT "
            f"line (`taxjson ticker-map --suggest`)", det)


def map_lines(joined: Iterable[Pair]) -> List[str]:
    """The TOBASE lines of the joins, one per pair of listings, each with
    its evidence as a comment."""
    seen: Set[Tuple[str, str]] = set()
    out: List[str] = []
    for p in sorted(joined, key=lambda p: (p.frm, p.to, p.out.date)):
        if (p.frm, p.to) in seen:
            continue
        seen.add((p.frm, p.to))
        out.append(f"TOBASE {p.frm} {p.to}  # transfer {p.out.symbol} -> "
                   f"{p.into.symbol} {p.out.date} ({p.out.account})")
    return out


def state_text(result: Dict[str, List[Any]]) -> str:
    doc = {"format": FORMAT,
           "joined": [p.record() for p in result["joined"]],
           "suggested": [p.record() for p in result["suggested"]],
           "collisions": [c.record()
                          for c in result.get("collisions") or []]}
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def read_state(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """The state file's records ({"joined": [], "suggested": []} when
    missing or unreadable)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        doc = None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return {"joined": [], "suggested": [], "collisions": []}
    return {k: [r for r in (doc.get(k) or []) if isinstance(r, dict)]
            for k in ("joined", "suggested", "collisions")}


def effective_map_text(ticker_map: Optional[Path],
                       joined: Iterable[Pair]) -> Optional[str]:
    """The map the merge stages read: the project's ticker.map as it is,
    then the joins' TOBASE lines. None when there is neither."""
    lines = map_lines(joined)
    base = ""
    if ticker_map is not None and Path(ticker_map).is_file():
        from taxjson.lib.cli_diag import read_text_utf8
        base = read_text_utf8(Path(ticker_map))
    if not lines and not base:
        return None
    if not lines:
        return base
    if base and not base.endswith("\n"):
        base += "\n"
    return base + ("\n" if base else "") + EFFECTIVE_HEAD + "\n" + \
        "\n".join(lines) + "\n"


def joined_note(account: str, joined: Iterable[Pair]
                ) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the one Warning per account naming the
    pairs joined through its legs, each with the ticker.map line that
    undoes it; None when there are none."""
    items: List[str] = []
    undo: List[str] = []
    seen = set()
    for p in sorted(joined, key=lambda p: (p.out.date, p.frm)):
        if account not in (p.out.account, p.into.account):
            continue
        k = (p.out.symbol, p.into.symbol, p.out.date)
        if k in seen:
            continue
        seen.add(k)
        items.append(f"{p.out.symbol} ↔ {p.into.symbol} (transfer "
                     f"{p.out.date})")
        undo.append(f"- {p.out.symbol} ↔ {p.into.symbol}: their names are "
                    f"the same word for word "
                    f"({p.names[0] or p.names[1]!r}); if they are not one "
                    f"security, add `DISTINCT {p.out.symbol} {p.into.symbol}` "
                    f"to ticker.map")
    if not items:
        return None
    return (f"{account}: joined as one security by their transfer journal: "
            + ", ".join(items),
            ["Booked as one security (one cost pool, one security for the "
             "loss rules), as a ticker.map TOBASE line would — this "
             "changes your books."] + undo)
