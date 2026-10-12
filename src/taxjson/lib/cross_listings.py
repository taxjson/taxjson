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
  brokers), the same quantity, dated within PAIR_DAYS business days of
  each other (business_days: weekends not counted), and neither leg
  pairs with any other candidate (after the same-symbol legs cancel
  each other) — except that a reference the broker writes on both legs
  of one journal (RBC's J~, Questrade's journal_pair) pairs those two
  first and no others, and an explicit journal pair unique on its day
  pairs before legs of other days (two equal gambits two days apart);
* the security names are EQUAL: some name the exports give X and some
  name they give Y are the same word for word once normalised
  (lib/symbol_codes.exact_name: case, punctuation, abbreviations, broker
  boilerplate and the generic share words set aside) — every share
  designator and the corporate form included: LP is not CORP, TRUST is
  not FUND, "QZCO CORP" is not "QZCO CORP CL B", "QZALPHA BANK" is not
  "QZALPHA BANK OF CANADA", and a word such as HEDGED must be on both
  sides too. No subset of words, no designator stated by one name only.
  And every other name either listing has states the same designators
  and corporate form (a listing also named "... CL B" never joins).
  An EXPLICIT JOURNAL pair instead (_explicit_journal: one account, one
  day, the same quantity, both legs in the broker's journal wording —
  journal_wording: RBC's TFR "TRANSFER TO C$ / FROM U$  J", IB's
  InterDepot, Questrade's BRW JOURNAL POSITION) compares the two legs'
  OWN names (_journal_names_verdict): a fund renamed later or a listing
  another broker names otherwise says nothing about that journal; a
  corporate-form word one name states and the other states none of,
  and a "COM NEW" spelling, are set aside; every designator counts,
  two stated forms must agree, and a name of either listing that names
  another company refuses;
* no ticker.map rule renames or deletes X or Y (TOBASE / JOURNAL /
  GLOBAL / RENAME / DELETE, either side) and no DISTINCT line pairs the
  two: the user's map always wins, DISTINCT keeps them apart. The map
  is read through its renames: when it books the out-leg as the
  in-leg's other listing (`TOBASE QZAB.US QZAA.TO`, QZAA.US in: the
  account also holds QZAA.TO, lib/listing_suffix), the in-leg is
  joined to that listing (`TOBASE QZAA.US QZAA.TO`); a pair the map
  refuses whose legs it books as two different symbols (map_split) is
  a Warning naming both legs and the line that books them as one —
  each leg is otherwise left unpaired — and `run --strict` stops;
* neither symbol is joined to a third listing by another pair — except
  the listing every other one maps onto (each line's TO) when each of
  those joins rests on its broker's journal pairs (the fund's USD line
  under two symbols over the years, both journaled onto its CAD line).

A transfer between two listings that a journal pair joined is part of
that join (recorded with "via": "journal") when its legs pair uniquely
and their own names agree as a journal's must: a move from one broker's
TSX line to another broker's NYSE line, the two brokers spelling the
corporate form differently.

Everything else stays a suggestion (`taxjson ticker-map --suggest`) —
except two listings whose names name different companies (no leading
company word in common, companies_differ): never a join, never a TOBASE
suggestion. A `.US` symbol whose rows name two different companies, one
of them a Canadian-listed fund's US-dollar units (a TSX fund's US-dollar
unit booked `.US` beside an NYSE stock of the same root), is a SYMBOL
COLLISION (`collisions`): a Warning on the run's console and the EXTRACT
line (plus a TOBASE) that gives the fund's rows their own symbol, never
a join through that symbol. The
joins are written to work/cross_listings.state (JSON) and appended, as
TOBASE lines, to the effective map the merge stages read
(work/ticker.map.effective: the project's ticker.map plus those lines).
A join changes the books, so the run says each one as a Warning naming
the pair and the ticker.map line that undoes it (`DISTINCT X Y`).
No security data is kept here: names, symbols and dates come from the
user's own exports.

A broker's CURRENCY journal (Questrade's BRW "JOURNAL POSITION TO USD" /
"FROM CAD" pair, which the parser pairs within one account and marks
with a `journal_pair` id) moves units between the CAD and USD lines of
one security. In a Canadian project (tax-logic CA-XLIST-03) the two
lines are joined as a ticker.map `TOBASE FROM TO` line would — one
security for the cost and the loss rules, the journal's legs moving the
units in the holdings view —
on the parser's pairing alone (the legs share one description). The
user's map still wins, and a listing joined to two others is only
suggested. In a US project those legs are ordinary transfer legs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta as _timedelta
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
    pair: str = ""                  # a currency journal's id (parser)
    currency: str = ""              # the row's currency
    # The leg's OWN security name (its row's, symbol_codes._row_name):
    # exact_name key and as written.
    name: Tuple[str, ...] = ()
    raw_name: str = ""
    # The broker's journal wording on the row (journal_wording): the
    # broker that wrote it ("" for an ordinary transfer leg), and the
    # reference both legs of one journal share ("" when none).
    journal: str = ""
    ref: str = ""
    # A leg a .tt JOURNAL line booked (lib/dated_events, broker "tt"):
    # that journal's pair id.
    decl: str = ""


@dataclass
class Pair:
    out: Leg
    into: Leg
    frm: str = ""                   # TOBASE / JOURNAL FROM
    to: str = ""                    # TOBASE / JOURNAL TO
    reason: str = ""                # why not joined ("" when joined)
    names: Tuple[str, str] = ("", "")
    extra: Dict[str, Any] = field(default_factory=dict)
    # The ticker.map keyword the join stands for: TOBASE (a transfer
    # journal between two listings) or JOURNAL (a currency journal).
    kind: str = "TOBASE"
    # The broker whose journal wording both legs carry (an explicit
    # journal pair, _explicit_journal), "" otherwise.
    journal: str = ""

    @property
    def source(self) -> str:
        """"tt" for a pair a .tt JOURNAL line declared, else "broker"."""
        return "tt" if self.journal == "tt" else "broker"

    def record(self) -> Dict[str, Any]:
        return {"from": self.frm, "to": self.to, "source": self.source,
                **({"kind": self.kind} if self.kind != "TOBASE" else {}),
                **({"journal": self.journal} if self.journal else {}),
                **({"via": self.extra["via"]} if self.extra.get("via")
                   else {}),
                # A join through the map's own line (analyze): the
                # listing the map books the out-leg as.
                **({"map": self.extra["map"]} if self.extra.get("map")
                   else {}),
                **({"where": self.extra["where"]}
                   if self.extra.get("where") else {}),
                # The broker's pair id the legs share (Questrade's
                # journal_pair: "pair", RBC's J~ reference: "ref").
                **({"ref": self.out.ref.split("|", 1)[0]}
                   if self.out.ref and self.out.ref == self.into.ref
                   else {}),
                # Why a pair was not joined (`refused`): "distinct",
                # "different" or "map" (analyze).
                **({"refused": self.extra["refused"]}
                   if self.extra.get("refused") else {}),
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
    from taxjson.lib.missing_history import journal_leg_key
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
                jw = journal_wording(broker,
                                     str(t.get("description") or ""))
                pair = str(t.get("journal_pair") or "")
                # The broker's pair id: Questrade's journal_pair, RBC's
                # J~ reference (missing_history.journal_leg_key).
                key = journal_leg_key(t, broker=broker)
                ref = "|".join(key) if key else ""
                legs.append(Leg(acct, broker, sym,
                                str(t.get("date"))[:10], q,
                                pair=pair,
                                currency=str(t.get("currency") or "")
                                .upper(),
                                name=toks,
                                raw_name=" ".join(
                                    _row_name(t, broker).split()),
                                journal=jw, ref=ref))
    legs.sort(key=lambda g: (g.date, g.account, g.broker, g.symbol,
                             g.quantity))
    return legs, names, shown


# A broker's journal wording on a transfer row (language, from the
# brokers' own rows; no security data): RBC's TFR currency journal
# ("TFR - <NAME> TRANSFER TO C$  J~<ref>" / "... TRANSFER FROM U$  J",
# the reference from late 2024 on), IB's listing flip ("InterDepot
# (<ROOT>)", the parser's description) and Questrade's BRW currency
# journal ("<NAME> JOURNAL POSITION TO USD" / "FROM CAD ...").
_RBC_JOURNAL_RE = re.compile(
    r"^\s*TFR\s*-.*\bTRANSFER\s+(?:TO|FROM)\s+[CU]\$\s+J(?:~\S+)?\s*$",
    re.IGNORECASE)
_IB_JOURNAL_RE = re.compile(r"^\s*InterDepot\s*\(", re.IGNORECASE)


def journal_wording(broker: str, desc: str) -> str:
    """The broker when a transfer row carries its journal wording
    (_RBC_JOURNAL_RE, _IB_JOURNAL_RE, Questrade's BRW JOURNAL POSITION),
    else "". The reference both legs of one journal share (RBC's J~,
    Questrade's journal_pair) is missing_history.journal_leg_key's."""
    d = " ".join(str(desc or "").split())
    if broker == "rbc_direct":
        if _RBC_JOURNAL_RE.match(d):
            return broker
    elif broker == "ib":
        if _IB_JOURNAL_RE.match(d):
            return broker
    elif broker == "questrade":
        from taxjson.lib.brokerages.questrade import _BRW_JOURNAL_RE
        if _BRW_JOURNAL_RE.search(d):
            return broker
    return ""


def _other_listing(symbol: str) -> Optional[str]:
    """ROOT.US <-> ROOT.TO (lib/listing_suffix.other_listing)."""
    from taxjson.lib.listing_suffix import other_listing
    return other_listing(symbol)


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


def _run_ons(key: Iterable[str]) -> frozenset:
    """The company words of a name (name_tokens' core) run together from
    the start, word by word: "OPEN QZX CORP" -> {OPEN, OPENQZX}, "QZ-TEL
    CORP" -> {QZ, QZTEL} — one company spelled with or without its
    spaces and hyphens shares one (only those of 3+ characters)."""
    from taxjson.lib.symbol_codes import name_tokens
    out, acc = set(), ""
    for w in name_tokens(" ".join(key)):
        if w.startswith("~"):
            continue
        acc += w
        if len(acc) >= 3 and not acc.isdigit():
            out.add(acc)
    return frozenset(out)


def companies_differ(a: Iterable[str], b: Iterable[str]) -> bool:
    """Two names (exact_name keys) clearly name different companies:
    each has leading company words (lead_words) and they share none
    ("QZREALTY TRUST INC" vs "SAMPLEX US DLR CURRENCY ETF"), nor do
    their words run together from the start ("OPEN QZX CORP" and
    "OPENQZX CORP", "QZ-TEL CORP" and "QZTEL CORP": one company two
    brokers space differently; _run_ons). A shared word — the same
    issuer, a rebranded fund ("QZOLD U S DLR CURRENCY ETF" / "QZNEW US
    DLR CURRENCY ETF"), or a name too short to tell — is inconclusive,
    never "different": the names are then compared word for word
    (_names_verdict), "not equal — verify" when they are not."""
    la, lb = lead_words(a), lead_words(b)
    if not la or not lb or la & lb:
        return False
    return not (_run_ons(a) & _run_ons(b))


def business_days(a: _date, b: _date) -> int:
    """The weekdays (Monday to Friday) after the earlier date up to and
    including the later one: Wednesday to the next Tuesday is 4. A
    transfer between brokers is booked by each on its own business
    days; a weekend never counts (holidays do: no exchange calendar is
    assumed)."""
    lo, hi = (a, b) if a <= b else (b, a)
    full, rest = divmod((hi - lo).days, 7)
    return full * 5 + sum(1 for k in range(1, rest + 1)
                          if (lo + _timedelta(days=k)).weekday() < 5)


def _close(a: Leg, b: Leg, days: int) -> bool:
    """The two legs are dated at most `days` business days apart
    (business_days)."""
    da, db = _d(a.date), _d(b.date)
    return bool(da and db and business_days(da, db) <= days)


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


def _claim(legs: List[Leg], j: Any, sym: str, sign: int) -> Optional[Leg]:
    """The leg of a declared journal `j` (lib/dated_events.Journal) on
    listing `sym`, direction `sign`: the one its line booked, else the
    broker's leg it duplicates (the account's, the same quantity, within
    PAIR_DAYS business days of the line's date, the closest first)."""
    for g in legs:
        if (g.decl and g.decl == j.pair and g.symbol == sym
                and g.quantity * sign > 0 and not g.used):
            return g
    jd = _d(j.date)
    best = None
    for g in legs:
        if (g.used or g.decl or g.account != j.account or g.symbol != sym
                or g.quantity * sign <= 0
                or abs(abs(g.quantity) - j.quantity)
                > max(_EPS, 1e-6 * j.quantity)):
            continue
        gd = _d(g.date)
        if gd is None or jd is None:
            continue
        gap = business_days(gd, jd)
        if gap <= PAIR_DAYS and (best is None or gap < best[0]):
            best = (gap, g)
    return best[1] if best else None


def declared_twins(journals: Iterable[Any]) -> Tuple[List[Any], List[str]]:
    """(the .tt JOURNAL lines to book, Warning lines): two IDENTICAL lines
    (one account, date, FROM, TO and quantity — a line pasted twice, or
    one file copied) are one journal, booked once; the second is said as
    a Warning naming both places (two journals of one size on one day
    are one line with the total quantity)."""
    kept: List[Any] = []
    seen: Dict[Tuple[Any, ...], Any] = {}
    warnings: List[str] = []
    for j in journals:
        k = (j.account, j.date, j.frm, j.to, round(float(j.quantity), 8))
        first = seen.get(k)
        if first is None:
            seen[k] = j
            kept.append(j)
            continue
        warnings.append(
            f"{j.where}: JOURNAL {j.date} {j.frm} {j.to} {j.quantity:g} "
            f"repeats {first.where} word for word: one journal, booked "
            f"once — two journals of that size on that day are one line "
            f"with the total quantity")
    return kept, warnings


def partial_overlaps(journals: Iterable[Any], legs: Iterable[Leg],
                     days: int = PAIR_DAYS) -> List[str]:
    """The .tt JOURNAL lines booked in full (status "booked") that move
    MORE units than a journal the broker's rows already hold between the
    same two listings in the same account ON THE LINE'S DATE (its
    out-leg of FROM and in-leg of TO, one quantity): the line most
    likely restates that journal with another size, and booking it in
    full would move those units twice. A line on another date is another
    journal, booked. Returns one problem line each (the run stops on
    it). `days` is kept for the callers' signature."""
    legs = [g for g in legs if g.broker != "tt" and not g.decl]
    out: List[str] = []
    for j in journals:
        if (getattr(j, "status", "booked") != "booked"
                or getattr(j, "separate", False)):
            continue        # (a line declared `separate`: its own journal)
        # A restatement is dated as the broker's journal: a .tt line on
        # another day is another journal, booked (two real journals in
        # one week are no dead end).
        outs = [g for g in legs if g.account == j.account
                and g.symbol == j.frm and g.quantity < 0
                and g.date[:10] == j.date]
        ins = [g for g in legs if g.account == j.account
               and g.symbol == j.to and g.quantity > 0
               and g.date[:10] == j.date]
        for o in outs:
            q = -o.quantity
            if not (q < j.quantity - _EPS):
                continue
            i = next((g for g in ins
                      if abs(g.quantity - q) <= max(_EPS, 1e-6 * q)), None)
            if i is None:
                continue
            out.append(
                f"{j.where}: JOURNAL {j.date} {j.frm} {j.to} "
                f"{j.quantity:g} — the broker's rows already hold a "
                f"journal of {q:g} between them ({o.date}, {i.date}): if "
                f"this line is that journal, delete it; if the rows lack "
                f"part of it, write only the units they lack "
                f"({j.quantity - q:g})")
            break
    return out


# How many business days from the broker's journal a bigger .tt JOURNAL
# line reads as a likely restatement of it (near_restatements): RBC dates
# a gambit's trades on the trade day and its J~ legs on the settlement
# day, one business day later.
NEAR_DAYS = 1


def near_restatements(journals: Iterable[Any], legs: Iterable[Leg],
                      trades: Optional[Dict[Tuple[str, str],
                                            Dict[str, List[float]]]] = None
                      ) -> List[str]:
    """Warning lines for the .tt JOURNAL lines booked in full (status
    "booked") that move MORE units than a journal the broker's rows hold
    between the same two listings in the same account (its out-leg of
    FROM and in-leg of TO, one quantity) dated within NEAR_DAYS business
    days of the line but not on its date — or on the day of that
    journal's trades (`trades`, missing_history._day_trades: a buy of one
    listing and a sale of the other of its quantity, up to PAIR_DAYS
    business days before its legs) — and that no other .tt line already
    restates (second pre-release review, finding 7). Such a line most
    likely restates the broker's journal a day off (the gambit's trade
    date for its settlement date): booked in full, it moves those units
    twice. On the broker's own date it stops the run (partial_overlaps);
    here it is booked, and said."""
    from taxjson.lib.missing_history import _opposite_trades
    journals = list(journals)
    legs = [g for g in legs if g.broker != "tt" and not g.decl]
    out: List[str] = []
    for j in journals:
        if (getattr(j, "status", "booked") != "booked"
                or getattr(j, "separate", False)):
            continue        # (a line declared `separate`: its own journal)
        jd = _d(j.date)
        if jd is None:
            continue
        outs = [g for g in legs if g.account == j.account
                and g.symbol == j.frm and g.quantity < 0]
        ins = [g for g in legs if g.account == j.account
               and g.symbol == j.to and g.quantity > 0]
        for o in outs:
            q = -o.quantity
            if not (q < j.quantity - _EPS):
                continue
            i = next((g for g in ins if _same_qty(o, g)
                      and _close(o, g, PAIR_DAYS)
                      and (not o.ref or o.ref == g.ref)), None)
            if i is None or j.date in (o.date[:10], i.date[:10]):
                continue        # the same date: partial_overlaps stops
            # Another .tt line already restates that journal.
            if any(k is not j and k.account == j.account
                   and k.frm == j.frm and k.to == j.to
                   and getattr(k, "status", "") != "booked"
                   and abs(k.quantity - q) <= max(_EPS, 1e-6 * q)
                   for k in journals):
                continue
            od, idt = _d(o.date), _d(i.date)
            near = any(x is not None and business_days(x, jd) <= NEAR_DAYS
                       for x in (od, idt))
            # The line on the day of that journal's trades (a buy of one
            # listing and a sale of the other, its quantity, before the
            # legs and within PAIR_DAYS business days of them).
            tr = (trades or {}).get((j.account, j.date))
            tday = j.date if (
                tr and od is not None and jd <= max(od, idt or od)
                and business_days(jd, od) <= PAIR_DAYS
                and _opposite_trades(tr, j.frm, j.to)
                and abs(max(tr[j.frm]) - q) <= max(_EPS, 1e-6 * q)) else ""
            if not (near or tday):
                continue
            out.append(
                f"{j.where}: JOURNAL {j.date} {j.frm} {j.to} "
                f"{j.quantity:g} is booked in full beside the broker's "
                f"journal of {q:g} between the same listings ({o.date}, "
                f"{i.date}"
                + (f"; its trades {tday}" if tday else "")
                + f"), a day from the line: together they move "
                f"{j.quantity + q:g} units. If the line restates the "
                f"broker's journal, date it {o.date}: the run then says "
                f"what to write; if it is a separate journal, end the "
                f"line with `separate`")
            break
    return out


def _explicit_journal(o: Leg, i: Leg) -> bool:
    """An explicit journal pair: one account at one broker, one day, the
    same quantity, both legs in that broker's journal wording
    (journal_wording)."""
    return (bool(o.journal) and o.journal == i.journal
            and o.account == i.account and o.broker == i.broker
            and o.date == i.date and _same_qty(o, i))


# A generic share word followed by NEW ("QZCO CORP COM NEW"): a broker's
# spelling of the common shares, read as the share word alone between
# the two legs of one journal.
_COM_NEW_RE = re.compile(r"\b(COM|COMMON|STK|STOCK|SHS|SHARES?)\s+NEW\b")


def _journal_key(raw: str) -> Tuple[str, ...]:
    from taxjson.lib.symbol_codes import exact_name
    return exact_name(_COM_NEW_RE.sub(r"\1", " ".join(
        str(raw or "").upper().split())))


# Corporate forms a journal's names never set aside when only one name
# states them: a partnership's units are not the company's shares.
_KEPT_FORMS = frozenset(("LP", "LLP"))


def _trailing_form(key: Tuple[str, ...], forms: Iterable[str]
                   ) -> Tuple[str, ...]:
    """The corporate-form words at the END of a name key ("QZCO CORP
    INC" -> ("CORPORATION", "INC") as exact_name spells them)."""
    forms = set(forms) - {"THE"}
    n = len(key)
    while n > 0 and key[n - 1] in forms:
        n -= 1
    return tuple(key[n:])


def _journal_names_verdict(o: Leg, i: Leg, nx: Set[Tuple[str, ...]],
                           ny: Set[Tuple[str, ...]],
                           shown: Dict[Tuple[str, ...], str]) -> str:
    """"" when the two legs of a journal pair name one security, else
    why not (DIFFERENT for two companies). The legs' OWN names are
    compared (each row's, on the journal's date), not every name either
    listing ever had — a fund renamed later, or a listing whose name
    another broker spells with other designators, says nothing about
    this journal. Word for word (exact_name), with three broker spellings
    set aside: a leading THE, the corporate-form words (LTD, CORP, INC
    ...) that END one name when the other ends with none (only at the
    end: "QZ SE ASIA FUND" is not "QZ ASIA FUND"; never LP / LLP: a
    partnership is not the company), and a NEW after a generic share
    word ("COM NEW"). Corporate forms both names state must agree (LP
    is not CORP), and every share designator and class letter counts. Any name of either listing that names another company
    (companies_differ with both legs' names) refuses."""
    from taxjson.lib.symbol_codes import _FORM
    a, b = o.name, i.name
    if companies_differ(a, b):
        return DIFFERENT
    for n in sorted(nx | ny):
        if companies_differ(n, a) and companies_differ(n, b):
            return (f"another name of the listings names another company "
                    f"({shown.get(n, ' '.join(n))!r} vs {o.raw_name!r})")
    ka, kb = _journal_key(o.raw_name), _journal_key(i.raw_name)
    # A leading THE is no part of the name.
    ka, kb = (tuple(k[1:]) if k[:1] == ("THE",) else k for k in (ka, kb))
    ta, tb = _trailing_form(ka, _FORM), _trailing_form(kb, _FORM)
    if not ta or not tb:
        # The form one name states and the other leaves out, at its END
        # only ("QZ SE ASIA FUND" is not "QZ ASIA FUND"), and never a
        # partnership's (an LP is not the corporation: _KEPT_FORMS).
        if not set(ta + tb) & _KEPT_FORMS:
            ka, kb = ka[:len(ka) - len(ta)], kb[:len(kb) - len(tb)]
    if ka == kb:
        return ""
    return (f"the legs' names are not equal word for word "
            f"({o.raw_name!r} vs {i.raw_name!r})")


def listing_root(symbol: str) -> str:
    """A listing's symbol without its VENUE suffix (a listing suffix the
    market data knows: lib/markets.known_suffixes) and, on a Canadian
    venue, without the US-dollar unit class (markets.toml
    [usd_unit_class]: SAMPLF.U.TO and SAMPLF.TO are SAMPLF; QZG.US is
    QZG). Any other dotted part is the security's own — a class, a
    warrant, a unit designator: QZG.B.TO is QZG.B, QZG.UN.TO is QZG.UN,
    QZG.WS is QZG.WS, and QZG.A and QZG.B are two roots (second
    pre-release review, finding 12: a class or warrant suffix read as a
    venue joined two securities on one root)."""
    from taxjson.lib.markets import data, known_suffixes
    from taxjson.lib.income_dating import CA_LISTING_SUFFIXES
    s = str(symbol or "").upper().strip()
    if "." not in s:
        return s
    base, sfx = s.rsplit(".", 1)
    if sfx not in known_suffixes():
        return s
    if sfx in CA_LISTING_SUFFIXES:
        cls = re.escape(str(data()["usd_unit_class"]["class"]).upper())
        base = re.sub(rf"[.\-]{cls}$", "", base)
    return base


# Why a .tt JOURNAL line's two listings are not joined: nothing shows
# they are one security (analyze's `refused` reason, "unproven").
UNPROVEN = "unproven"
# analyze's `refused` reason for a pair the interlisted master knows is
# a Canadian depositary receipt and its US share (lib/tobase_map.
# receipt_pairs, Canada): two securities, never joined from a journal.
RECEIPT = "receipt"


def receipt_why(symbol: str, names: Iterable[Tuple[str, ...]] = (),
                written: str = "",
                other_names: Iterable[Tuple[str, ...]] = (),
                other_written: str = "") -> str:
    """Why `symbol` is a depositary receipt for a journal's evidence
    ("" when it is not): written on a venue that lists receipts under
    the underlying's ticker (markets.toml `receipts = true`: a CDR on
    Cboe Canada, QZG.NE — the .tt line's own spelling, as the books fold
    a Canadian venue into .TO), or a name in the exports with a receipt
    word ([lists] receipt_words: "... CDR"). A receipt is its own
    security, never one root with the share it holds (v0.24.1
    leftovers, 2).

    The evidence tells two listings APART, so it counts only when the
    other listing does not carry it too: the venue only when the other
    (`other_written`) is written on another venue that lists no
    receipts (a NEO ETF's CAD and USD units, QZG.NE and QZG.U.NE, are
    two lines of one fund), a receipt word inside the company's name
    (_inside_name) only when no name of the other (`other_names`)
    states one (a company named "QZX SPONSORED HLDGS INC" on both
    sides; v0.24.1 review, M2 / L2). A receipt word after the name
    ("... INC CDR") always counts."""
    from taxjson.lib.markets import (receipt_suffixes, receipt_words,
                                     suffix_of)
    w = str(written or "").upper()
    ow = str(other_written or "").upper()
    rs = receipt_suffixes()
    if w and suffix_of(w) in rs and not (ow and suffix_of(ow) in rs):
        return f"{w} is written on a venue that lists depositary receipts"
    words = receipt_words()
    other_has = any(set(n) & words for n in other_names)
    for n in sorted(names):
        hit = sorted(set(n) & words)
        if not hit:
            continue
        if other_has and all(_inside_name(n, w) for w in hit):
            continue        # a word of the company's name, both sides
        return (f"{symbol} is named as a depositary receipt "
                f"({hit[0]})")
    return ""


def _inside_name(key: Tuple[str, ...], word: str) -> bool:
    """`word` sits inside the company's name: a corporate-form word
    (INC, CORP, PLC ...) follows it ("QZX SPONSORED HLDGS INC"), unlike
    a receipt designator after the name ("QZX PLATFORMS INC CDR", "QZX
    PLC SPONSORED ADR")."""
    from taxjson.lib.symbol_codes import _FORM
    k = list(key)
    return any(w in _FORM and w != "THE"
               for i, x in enumerate(k) if x == word for w in k[i + 1:])


def _receipt_between(a: str, na: Iterable[Tuple[str, ...]], wa: str,
                     b: str, nb: Iterable[Tuple[str, ...]], wb: str
                     ) -> str:
    """receipt_why of either of two listings, each judged against the
    other."""
    na, nb = list(na), list(nb)
    return (receipt_why(a, na, wa, nb, wb)
            or receipt_why(b, nb, wb, na, wa))


def shown_apart(a: str, b: str,
                names: Dict[str, Set[Tuple[str, ...]]],
                receipts: Optional[Dict[frozenset, str]] = None) -> str:
    """Why the exports show two listings are NOT one security, else "":
    a Canadian listing of the two is a depositary receipt (receipt_why:
    a receipt word in its name, or a receipt venue; or, given
    `receipts` — lib/tobase_map.receipt_pairs, a Canadian project —
    the interlisted master knows the pair as a CDR and its US share),
    or the names name different companies (companies_differ for every
    pair of names).
    Shared letters are a candidate, never proof either way: this is
    only the evidence AGAINST (`taxjson tips` MAP-GAP / US-LISTING,
    `ticker-map --suggest`'s conditional hints)."""
    from taxjson.lib.markets import is_canadian_listing
    why = (receipts or {}).get(frozenset((a, b)))
    if why:
        return why
    na, nb = names.get(a, set()), names.get(b, set())
    for sym, ns, other, ons in ((a, na, b, nb), (b, nb, a, na)):
        if is_canadian_listing(sym):
            why = receipt_why(sym, ns, sym, ons, other)
            if why:
                return why
    if na and nb and all(companies_differ(x, y) for x in na for y in nb):
        return DIFFERENT
    return ""


def declared_verdict(frm: str, to: str,
                     names: Dict[str, Set[Tuple[str, ...]]],
                     shown: Dict[Tuple[str, ...], str],
                     written: Tuple[str, str] = ("", "")) -> str:
    """"" when something shows a .tt JOURNAL line's FROM and TO are two
    listings of one security, else why not (DIFFERENT for two
    companies): the exports' names of the two listings must not name
    different companies (companies_differ: a ticker another company
    uses on the other venue), and either the two share one root
    (listing_root: QZG.TO / QZG.U.TO / QZG.US) — unless one is a
    depositary receipt (receipt_why; `written`: the line's own FROM and
    TO spellings), its own security — or some name of each agrees as a
    journal's two legs' names must (_journal_names_verdict). A
    ticker.map line naming either listing is checked before this (the
    user's map decides: a TOBASE line is the deliberate join)."""
    nx, ny = names.get(frm, set()), names.get(to, set())
    if nx and ny and all(companies_differ(a, b) for a in nx for b in ny):
        return DIFFERENT
    receipt = _receipt_between(frm, nx, written[0], to, ny, written[1])
    if listing_root(frm) == listing_root(to) and not receipt:
        return ""
    why = ""
    for a in sorted(nx):
        for b in sorted(ny):
            la = Leg("", "", frm, "", -1.0, name=a,
                     raw_name=shown.get(a, " ".join(a)))
            lb = Leg("", "", to, "", 1.0, name=b,
                     raw_name=shown.get(b, " ".join(b)))
            v = _journal_names_verdict(la, lb, nx, ny, shown)
            if v == "":
                return ""
            why = why or v
    if not nx or not ny:
        why = ("no security name for " + ("either listing" if not nx
                                          and not ny else "one listing"))
    if listing_root(frm) == listing_root(to):
        return (f"nothing shows {frm} and {to} are one security: {receipt}"
                f" (its own security, not a listing of the share) and "
                f"{why}")
    return (f"nothing shows {frm} and {to} are one security: their roots "
            f"differ ({listing_root(frm)}, {listing_root(to)}) and {why}")


def _hub_partners_agree(hub: str, pairs: List[Pair],
                        names: Dict[str, Set[Tuple[str, ...]]],
                        shown: Dict[Tuple[str, ...], str]) -> bool:
    """The listings several journals map onto `hub` are one security
    with each other too (pre-release review M5: "QZCO PLC" -> "QZCO" and
    "QZCO CORP" -> "QZCO" each pass with the hub's name, which states no
    form, yet the PLC is not the CORP). Every two partners agree when
    each one's join is SOLID — its leg's own name is its hub leg's word
    for word (a fund renamed between two journals: each partner equal to
    the hub's name of its day), or a .tt JOURNAL line between two
    listings of one root — or else when the two partners' own leg names
    pass _journal_names_verdict against each other. Partners whose names
    state different corporate forms never agree (forms_differ)."""
    def side(p: Pair) -> Tuple[Leg, Leg]:
        return (p.out, p.into) if p.into.symbol == hub else (p.into, p.out)

    def solid(p: Pair) -> bool:
        mine, theirs = side(p)
        if p.journal == "tt":
            return (listing_root(mine.symbol) == listing_root(hub)
                    and not p.extra.get("receipt"))
        return bool(mine.name and theirs.name
                    and _journal_key(mine.raw_name)
                    == _journal_key(theirs.raw_name))

    def forms(leg: Leg) -> Set[str]:
        """The corporate forms the partner's names state (its leg's own
        and every name of its listing)."""
        from taxjson.lib.symbol_codes import _FORM
        keys = set(names.get(leg.symbol, set()))
        if leg.raw_name:
            keys.add(_journal_key(leg.raw_name))
        return {w for k in keys for w in _trailing_form(k, _FORM)}

    def forms_differ(la: Leg, lb: Leg) -> bool:
        """Two partners whose names state different corporate forms (an
        LP and a CORP), or a partnership form one states and the other
        does not — never pooled, however the hub's formless name agrees
        with each leg (second pre-release review, finding 12)."""
        fa, fb = forms(la), forms(lb)
        if fa and fb and not (fa & fb):
            return True
        return (fa & _KEPT_FORMS) != (fb & _KEPT_FORMS)
    for n, a in enumerate(pairs):
        for b in pairs[n + 1:]:
            if forms_differ(side(a)[0], side(b)[0]):
                return False
            if solid(a) and solid(b):
                continue
            la, lb = side(a)[0], side(b)[0]
            if not (la.name and lb.name) or _journal_names_verdict(
                    la, lb, names.get(la.symbol, set()),
                    names.get(lb.symbol, set()), shown):
                return False
    return True


def analyze(legs: List[Leg], names: Dict[str, Set[Tuple[str, ...]]],
            shown: Dict[Tuple[str, ...], str], *,
            map_named: Iterable[str] = (),
            map_distinct: Iterable[Iterable[str]] = (),
            base_currency: Optional[str] = None,
            days: int = PAIR_DAYS,
            collided: Iterable[str] = (),
            currency_journals: bool = False,
            declared: Iterable[Any] = (),
            refused: Optional[List[Pair]] = None,
            map_renames: Optional[Dict[str, str]] = None,
            receipts: Optional[Dict[frozenset, str]] = None
            ) -> Dict[str, List[Pair]]:
    """{"joined": [...], "suggested": [...]}: the cross-listing journals
    the legs show (module docstring). A pair whose names name different
    companies (companies_differ) is neither joined nor suggested; a pair
    with a `collided` symbol (one symbol, two companies: `collisions`)
    is left to the collision's EXTRACT line. `currency_journals`: join
    the parser-paired currency journals as JOURNAL lines (Canada,
    CA-XLIST-03; the caller gates the country).

    `refused`, when given, receives the unambiguous pairs left alone
    (`taxjson journals` lists them): a ticker.map DISTINCT line keeps
    them apart (extra["refused"] = "distinct"), the user's map names a
    listing and decides ("map"), or the legs of a broker journal name
    different companies ("different"). A coincidence of two unrelated
    transfers (no journal wording, no shared broker reference) whose
    names name different companies is not recorded.

    `declared`: the .tt JOURNAL lines (lib/dated_events.Journal) — each
    joins its two listings on the user's word, in both countries
    (CA-XLIST-04 / US-XLIST-03), with the legs it booked or the broker's
    legs it found; the user's ticker.map still wins (a refusal, "map" or
    "distinct", recorded like the others).

    `receipts` (lib/tobase_map.receipt_pairs, a Canadian project): the
    pairs the interlisted master knows are a depositary receipt and its
    US share — two securities with no line saying so. A journal's legs
    between them are never joined: refused as RECEIPT (a .tt JOURNAL
    line as UNPROVEN: the run stops, a deliberate join being a
    ticker.map TOBASE line); the user's map, naming either, decides
    first.

    `map_renames`: the map's renames as the base-currency books apply
    them (GLOBAL / TOBASE / JOURNAL, chains followed). A pair the map
    decides is read through them: when the map books the out-leg as the
    in-leg's other listing (`TOBASE QZAB.US QZAA.TO`, QZAA.US in) — or
    as whatever the map books that other listing as — the in-leg is
    joined to its other listing (`TOBASE QZAA.US QZAA.TO`,
    extra["map"]): the map's line already says the out-leg is that
    security, and the custody move is one security on both sides."""
    collided = {s.upper() for s in collided}
    named = {s.upper() for s in map_named}
    renames = {str(k).upper(): str(v).upper()
               for k, v in (map_renames or {}).items()}

    def _booked(sym: str) -> str:
        return renames.get(sym, sym)
    apart = {frozenset(x.upper() for x in pair) for pair in map_distinct}
    receipt_of = dict(receipts or {})
    joined: List[Pair] = []
    suggested: List[Pair] = []

    def _refuse(p: Pair, why: str, reason: str) -> None:
        if refused is not None:
            p.extra["refused"] = why
            p.reason = reason
            refused.append(p)

    def _map_reason(a: str, b: str) -> Tuple[str, str]:
        if frozenset((a, b)) in apart:
            return "distinct", (f"ticker.map keeps them apart "
                                f"(DISTINCT {a} {b})")
        sym = a if a in named else b
        return "map", f"ticker.map names {sym}: its line decides"
    # -1. A journal the user declared in a .tt file: its legs (the ones
    #     it booked, else the broker's it duplicates) are one pair.
    for j in declared:
        o = _claim(legs, j, j.frm, -1)
        i = _claim(legs, j, j.to, +1)
        if o is None or i is None:
            continue
        o.used = i.used = True
        if {o.symbol, i.symbol} & collided:
            continue                    # a collision's EXTRACT line decides
        if (o.symbol in named or i.symbol in named
                or frozenset((o.symbol, i.symbol)) in apart):
            # The user's map decides.
            _refuse(Pair(o, i, o.symbol, i.symbol, journal="tt",
                         extra={"where": j.where}),
                    *_map_reason(o.symbol, i.symbol))
            continue
        # On the user's word only when something shows the two are
        # listings of one security (pre-release review M2): a deliberate
        # join of two symbols is a ticker.map TOBASE line.
        # The line's own spellings: a receipt venue (QZG.NE) is folded
        # into .TO in the books.
        _w = (j.line or "").split()
        written = (_w[2], _w[3]) if len(_w) >= 4 else ("", "")
        verdict = declared_verdict(o.symbol, i.symbol, names, shown,
                                   written)
        _rw = receipt_of.get(frozenset((o.symbol, i.symbol)))
        if _rw:
            verdict = (f"nothing shows {o.symbol} and {i.symbol} are one "
                       f"security: {_rw}")
        if verdict:
            if refused is not None:
                nx = names.get(o.symbol, set())
                ny = names.get(i.symbol, set())
                p = Pair(o, i, o.symbol, i.symbol, journal="tt",
                         names=(shown.get(min(nx), "") if nx else "",
                                shown.get(min(ny), "") if ny else ""),
                         extra={"where": j.where})
                _refuse(p, UNPROVEN, verdict)
            continue
        frm, to = tobase_direction(o.symbol, i.symbol, base_currency)
        receipt = bool(_receipt_between(
            o.symbol, names.get(o.symbol, ()), written[0],
            i.symbol, names.get(i.symbol, ()), written[1]))
        joined.append(Pair(o, i, frm, to, journal="tt",
                           extra={"where": j.where,
                                  **({"receipt": True} if receipt
                                     else {})}))
    # 0. A currency journal the parser paired (one account, one day, one
    #    description): its two lines are one security.
    if currency_journals:
        groups: Dict[Tuple[str, str, str], List[Leg]] = {}
        for g in legs:
            # A leg a .tt JOURNAL line already claimed (step -1: the
            # broker's journal the line repeats) is that pair's: never a
            # second record of the same journal.
            if g.pair and not g.used:
                groups.setdefault((g.account, g.broker, g.pair),
                                  []).append(g)
        for _k, gl in sorted(groups.items()):
            o = [g for g in gl if g.quantity < 0]
            i = [g for g in gl if g.quantity > 0]
            if (len(o) != 1 or len(i) != 1 or o[0].symbol == i[0].symbol
                    or not _same_qty(o[0], i[0])):
                continue
            o[0].used = i[0].used = True
            if {o[0].symbol, i[0].symbol} & collided:
                continue    # a collision's EXTRACT line decides
            if (o[0].symbol in named or i[0].symbol in named
                    or frozenset((o[0].symbol, i[0].symbol)) in apart):
                # The user's map decides.
                _refuse(Pair(o[0], i[0], o[0].symbol, i[0].symbol,
                             kind="JOURNAL", journal=o[0].broker),
                        *_map_reason(o[0].symbol, i[0].symbol))
                continue
            _rw = receipt_of.get(frozenset((o[0].symbol, i[0].symbol)))
            if _rw:
                # A depositary receipt and its US share: two securities.
                _refuse(Pair(o[0], i[0], o[0].symbol, i[0].symbol,
                             kind="JOURNAL", journal=o[0].broker),
                        RECEIPT, _rw)
                continue
            # The line in the other currency maps onto the base one
            # (the legs' own currencies; an EXTRACT symbol may not spell
            # its currency).
            cur = {o[0].currency, i[0].currency}
            if base_currency in cur and len(cur) == 2:
                frm, to = ((o[0].symbol, i[0].symbol)
                           if i[0].currency == base_currency
                           else (i[0].symbol, o[0].symbol))
            else:
                frm, to = tobase_direction(o[0].symbol, i[0].symbol,
                                           base_currency)
            nx = names.get(o[0].symbol, set())
            ny = names.get(i[0].symbol, set())
            joined.append(Pair(o[0], i[0], frm, to, kind="JOURNAL",
                               journal=o[0].broker,
                               names=(shown.get(min(nx), "") if nx else "",
                                      shown.get(min(ny), "") if ny
                                      else "")))
    # 0b. A journal inside one account: the legs a broker reference
    #     pairs (RBC's J~ reference, Questrade's journal_pair — in a US
    #     project too, where step 0 does not run) settle within their
    #     account and pair BEFORE any other leg is looked at (second
    #     pre-release review, finding 2): another account's transfer of
    #     the same listing on that day never cancels one of them.
    picked: List[Tuple[Leg, Leg]] = []
    by_ref: Dict[Tuple[str, str, str], List[Leg]] = {}
    for g in legs:
        if g.ref and not g.used:
            by_ref.setdefault((g.account, g.broker, g.ref), []).append(g)
    from dataclasses import replace as _replace
    from taxjson.lib.missing_history import ref_group_journal
    for _k, gl in sorted(by_ref.items()):
        # One rule for a reference group (missing_history.
        # ref_group_journal, as transfer_in and the missing-history walk
        # read it): a journal the broker split over several rows (1000
        # out, 600 + 400 in) is one pair of its total units.
        jr = ref_group_journal((g.symbol, g.quantity, g.date) for g in gl)
        if jr is None:
            continue
        o = sorted((g for g in gl if g.quantity < 0), key=lambda g: g.date)
        i = sorted((g for g in gl if g.quantity > 0), key=lambda g: g.date)
        for g in o + i:
            g.used = True
        if jr[0] != jr[1]:
            # One leg per side: the first, carrying the group's units.
            po = o[0] if len(o) == 1 else _replace(o[0], quantity=-jr[2])
            pi = i[0] if len(i) == 1 else _replace(i[0], quantity=jr[2])
            picked.append((po, pi))

    def _own(g: Leg) -> bool:
        # A journal's leg (a broker reference, a .tt JOURNAL line's):
        # it moves units inside its account only.
        return bool(g.ref or g.decl)
    ins = [g for g in legs if g.quantity > 0]
    outs = [g for g in legs if g.quantity < 0]
    # 1. The same symbol's legs cancel (a custody move, a broker switch):
    #    the same quantity, the closest date first — a journal's leg left
    #    over from its pair only within its own account.
    cands = sorted(((abs((_d(o.date) - _d(i.date)).days), o.date, n, m)
                    for n, o in enumerate(outs) for m, i in enumerate(ins)
                    if o.symbol == i.symbol and _same_qty(o, i)
                    and _close(o, i, days)
                    and (o.account == i.account
                         or not (_own(o) or _own(i)))),
                   key=lambda c: c[:3])
    for _gap, _dt, n, m in cands:
        if not outs[n].used and not ins[m].used:
            outs[n].used = ins[m].used = True
    # 2. Another symbol's leg: the journal fingerprint. Two legs whose
    #    broker reference differs are two journals, never one pair; a
    #    journal's leg pairs within its own account only.
    def _fits(o: Leg, i: Leg) -> bool:
        return (not o.used and not i.used and i.symbol != o.symbol
                and _same_qty(o, i) and _close(o, i, days)
                and (o.account == i.account
                     or not (_own(o) or _own(i)))
                and not (o.ref and i.ref and (o.account, o.broker, o.ref)
                         != (i.account, i.broker, i.ref)))
    # 2a. The broker-referenced pairs (step 0b) come first.
    # 2b. An explicit journal pair (_explicit_journal: one account, one
    #     day, both legs in the broker's journal wording) unique on its
    #     day pairs before any leg of another day (two equal gambits two
    #     days apart are two pairs, not four candidates).
    same_day: Dict[int, List[int]] = {}
    same_back: Dict[int, List[int]] = {}
    for n, o in enumerate(outs):
        for m, i in enumerate(ins):
            if _fits(o, i) and _explicit_journal(o, i):
                same_day.setdefault(n, []).append(m)
                same_back.setdefault(m, []).append(n)
    for n, ms in sorted(same_day.items()):
        if len(ms) == 1 and len(same_back[ms[0]]) == 1:
            outs[n].used = ins[ms[0]].used = True
            picked.append((outs[n], ins[ms[0]]))
    # 2c. The rest: a pair only when neither leg pairs with another.
    links: Dict[int, List[int]] = {}
    back: Dict[int, List[int]] = {}
    for n, o in enumerate(outs):
        for m, i in enumerate(ins):
            if _fits(o, i):
                links.setdefault(n, []).append(m)
                back.setdefault(m, []).append(n)
    cands = [(o, i, False) for o, i in picked] + [
        (outs[n], ins[m], len(ms) > 1 or len(back.get(m, ())) > 1)
        for n, ms in sorted(links.items()) for m in ms]
    for o, i, ambiguous in cands:
        frm, to = tobase_direction(o.symbol, i.symbol, base_currency)
        nx, ny = names.get(o.symbol, set()), names.get(i.symbol, set())
        if o.symbol in collided or i.symbol in collided:
            continue                    # separate the symbol first
        mapped = (o.symbol in named or i.symbol in named
                  or frozenset((o.symbol, i.symbol)) in apart)
        if mapped and refused is None:
            continue                    # the user's map decides
        journal = o.journal if _explicit_journal(o, i) else ""
        verdict = (_journal_names_verdict(o, i, nx, ny, shown)
                   if journal and o.name and i.name else None)
        if verdict is None:
            journal = ""
            verdict = _names_verdict(nx, ny, shown)
        # A broker journal: its wording on both legs, or the broker's
        # pair id both legs share.
        brokered = bool(journal or (o.ref and o.ref == i.ref))
        if mapped:
            # The map books the out-leg as the in-leg's other listing
            # (through its lines): the in-leg is that listing too —
            # joined to it, never the map's own symbols renamed.
            alt = _other_listing(i.symbol)
            if (not ambiguous and verdict == "" and alt
                    and o.symbol != alt and i.symbol not in named
                    and _booked(o.symbol) != _booked(i.symbol)
                    and _booked(alt) == _booked(o.symbol)
                    and (o.symbol in named or alt in named)
                    and not ({frozenset((o.symbol, i.symbol)),
                              frozenset((i.symbol, alt)),
                              frozenset((o.symbol, alt))} & apart)):
                # The base currency's listing is kept (tobase_direction),
                # unless that makes a listing the map already renames the
                # line's FROM (one rename per symbol): the in-leg then
                # joins it.
                jf, jt = tobase_direction(i.symbol, alt, base_currency)
                if jf == alt and alt in renames:
                    jf, jt = i.symbol, alt
                p = Pair(o, i, jf, jt, journal=journal,
                         names=((o.raw_name, i.raw_name) if journal else
                                (shown.get(min(nx), "") if nx else "",
                                 shown.get(min(ny), "") if ny else "")),
                         extra={"map": _booked(o.symbol)})
                joined.append(p)
                continue
            # The user's map decides (listed by `taxjson journals` when
            # the pair is a journal, not a coincidence of two companies).
            # `verdict`: whether the legs' names agree (map_split warns
            # only for those).
            if not ambiguous and (verdict != DIFFERENT or brokered):
                _refuse(Pair(o, i, frm, to, journal=journal,
                             names=(o.raw_name, i.raw_name),
                             extra={"verdict": verdict}),
                        *_map_reason(o.symbol, i.symbol))
            continue
        _rw = receipt_of.get(frozenset((o.symbol, i.symbol)))
        if _rw:
            # A depositary receipt and its US share (the interlisted
            # master): two securities, whatever the legs' names say.
            if not ambiguous:
                _refuse(Pair(o, i, frm, to, journal=journal,
                             names=(o.raw_name, i.raw_name)),
                        RECEIPT, _rw)
            continue
        if verdict == DIFFERENT:
            # Two companies: no TOBASE line.
            if not ambiguous and brokered:
                _refuse(Pair(o, i, frm, to, journal=journal,
                             names=(o.raw_name, i.raw_name)),
                        "different", DIFFERENT)
            continue
        if journal:
            shown_names = (o.raw_name, i.raw_name)
        else:
            shown_names = (shown.get(min(nx), "") if nx else "",
                           shown.get(min(ny), "") if ny else "")
        p = Pair(o, i, frm, to, names=shown_names, journal=journal)
        p.reason = ("the legs pair with more than one other leg"
                    if ambiguous else verdict)
        (suggested if p.reason else joined).append(p)
    # One partner per symbol: a listing joined to two others is
    # ambiguous — except the listing every other one maps onto (each
    # TOBASE line's TO) when each of those joins rests on its broker's
    # journal pairs: the broker itself moved the units between them.
    partners: Dict[str, Set[str]] = {}
    for p in joined:
        partners.setdefault(p.frm, set()).add(p.to)
        partners.setdefault(p.to, set()).add(p.frm)
    hubs = {sym for sym, ps in partners.items() if len(ps) > 1
            and all(len(partners[x]) == 1 for x in ps)
            and all(p.journal for p in joined if sym in (p.frm, p.to))
            and all(p.to == sym for p in joined if sym in (p.frm, p.to))
            and _hub_partners_agree(sym, [p for p in joined
                                          if sym in (p.frm, p.to)],
                                    names, shown)}
    keep: List[Pair] = []
    for p in joined:
        if ((len(partners[p.frm]) > 1 or len(partners[p.to]) > 1)
                and p.to not in hubs):
            p.reason = "a listing pairs with two other listings"
            suggested.append(p)
        else:
            keep.append(p)
    # A transfer between two listings that a journal pair joined is part
    # of that join (one TOBASE line per pair of listings) when its legs
    # pair uniquely and their own names agree as a journal's legs must
    # (a broker move to the other listing, booked by two brokers that
    # spell the name differently).
    by_listings = {(p.frm, p.to) for p in keep if p.journal}
    still: List[Pair] = []
    for p in suggested:
        if ((p.frm, p.to) in by_listings
                and p.reason not in ("the legs pair with more than one "
                                     "other leg",
                                     "a listing pairs with two other "
                                     "listings")
                and p.out.name and p.into.name
                and _journal_names_verdict(
                    p.out, p.into, names.get(p.out.symbol, set()),
                    names.get(p.into.symbol, set()), shown) == ""):
            p.reason = ""
            p.extra["via"] = "journal"
            p.names = (p.out.raw_name, p.into.raw_name)
            keep.append(p)
        else:
            still.append(p)
    return {"joined": keep, "suggested": still}


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
    journal: str = ""               # TOBASE line for the moved rows
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
      fund adds `TOBASE <target> <listing>` (tobase_direction).
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
                journal = f"TOBASE {frm} {to}"
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
    """The TOBASE (or JOURNAL) lines of the joins, one per pair of
    listings, each with its evidence as a comment."""
    seen: Set[Tuple[str, str]] = set()
    out: List[str] = []
    for p in sorted(joined, key=lambda p: (p.frm, p.to, p.out.date)):
        if (p.frm, p.to) in seen:
            continue
        seen.add((p.frm, p.to))
        what = "currency journal" if p.kind == "JOURNAL" else "transfer"
        # (TOBASE for a currency journal too: a JOURNAL line is legacy,
        # read as TOBASE — lib/dated_events)
        out.append(f"TOBASE {p.frm} {p.to}  # {what} {p.out.symbol} -> "
                   f"{p.into.symbol} {p.out.date} ({p.out.account})")
    return out


# The record lists of the state file (read_state).
STATE_KEYS = ("joined", "suggested", "refused", "collisions")


def state_text(result: Dict[str, List[Any]]) -> str:
    doc = {"format": FORMAT,
           "joined": [p.record() for p in result["joined"]],
           "suggested": [p.record() for p in result["suggested"]],
           "collisions": [c.record()
                          for c in result.get("collisions") or []]}
    if result.get("refused"):
        doc["refused"] = [p.record() for p in result["refused"]]
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def read_state(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """The state file's records, one list per STATE_KEYS key (each empty
    when the file is missing or unreadable): the pairs joined, the pairs
    suggested, the pairs left alone (`refused`: analyze) and the symbol
    collisions."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        doc = None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return {k: [] for k in STATE_KEYS}
    # A hand-edited or damaged file: a key that is not a list, a record
    # that is not an object, is skipped (never a crash of a reader).
    return {k: [r for r in (doc.get(k) if isinstance(doc.get(k), list)
                            else []) if isinstance(r, dict)]
            for k in STATE_KEYS}


def effective_map_text(ticker_map: Optional[Path],
                       joined: Iterable[Pair],
                       renames: Iterable[str] = ()) -> Optional[str]:
    """The map the merge stages read: the project's ticker.map as it is,
    then the joins' TOBASE lines, then the ticker changes declared in .tt
    files (`renames`: lib/dated_events.effective_lines). None when there
    is none of them."""
    from taxjson.lib.dated_events import EFFECTIVE_HEAD as _REN_HEAD
    lines = map_lines(joined)
    renames = list(renames)
    base = ""
    if ticker_map is not None and Path(ticker_map).is_file():
        # With the project's tobase.map pairs (lib/tobase_map).
        from taxjson.lib.tobase_map import map_text_with_overlay
        base = map_text_with_overlay(Path(ticker_map))
    if not lines and not base and not renames:
        return None
    if not lines and not renames:
        return base
    if base and not base.endswith("\n"):
        base += "\n"
    out = base
    for head, block in ((EFFECTIVE_HEAD, lines), (_REN_HEAD, renames)):
        if block:
            out += ("\n" if out else "") + head + "\n" + \
                "\n".join(block) + "\n"
    return out


def joined_note(account: str, joined: Iterable[Pair],
                corrected: Optional[Dict[Tuple[str, str], str]] = None
                ) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the one Warning per account naming the
    pairs joined through its legs, each with the ticker.map line that
    undoes it; None when there are none. `corrected`: {(account,
    listing): why} for a leg whose listing was read from the evidence,
    not the row currency (lib/listing_suffix) — said with its pair."""
    corrected = corrected or {}
    items: List[str] = []
    undo: List[str] = []
    seen = set()
    kinds: Set[str] = set()
    for p in sorted(joined, key=lambda p: (p.out.date, p.frm)):
        if account not in (p.out.account, p.into.account):
            continue
        if p.source == "tt":
            continue        # the user's own .tt JOURNAL line (Info)
        k = (p.out.symbol, p.into.symbol, p.out.date)
        if k in seen:
            continue
        seen.add(k)
        kinds.add(p.kind)
        if p.kind == "JOURNAL":
            items.append(f"{p.out.symbol} ↔ {p.into.symbol} (currency "
                         f"journal {p.out.date})")
            undo.append(f"- {p.out.symbol} ↔ {p.into.symbol}: the broker "
                        f"journaled the units between the CAD and USD "
                        f"lines of one security "
                        f"({p.names[0] or p.names[1]!r}), booked as a "
                        f"ticker.map TOBASE line would (the journal's "
                        f"legs move the units in the holdings view); if "
                        f"they are not one security, "
                        f"add `DISTINCT {p.out.symbol} {p.into.symbol}` "
                        f"to ticker.map")
            continue
        if p.extra.get("map"):
            # The in-leg's other listing (the pair's other symbol), and
            # what the map's line says: it books the out-leg as that
            # listing, or that listing as the out-leg.
            alt = p.to if p.frm == p.into.symbol else p.frm
            says = (f"{alt} as {p.out.symbol}"
                    if p.extra["map"] == p.out.symbol
                    else f"{p.out.symbol} as {p.extra['map']}")
            items.append(f"{p.out.symbol} ↔ {p.into.symbol} (transfer "
                         f"{p.out.date}; ticker.map books {says}, so "
                         f"{p.into.symbol} joins {alt})")
            undo.append(f"- {p.out.symbol} ↔ {p.into.symbol}: the two legs "
                        f"of one move, and the map's line books {says}: "
                        f"{p.into.symbol} joins its other listing {alt} "
                        f"in the base-currency books (`TOBASE "
                        f"{p.frm} {p.to}`); if they are not one security, "
                        f"add `DISTINCT {p.out.symbol} {p.into.symbol}` to "
                        f"ticker.map")
            continue
        fixed = [corrected[k] for k in ((p.out.account, p.out.symbol),
                                        (p.into.account, p.into.symbol))
                 if k in corrected]
        items.append(f"{p.out.symbol} ↔ {p.into.symbol} (transfer "
                     f"{p.out.date}" + "".join(f"; {w}" for w in fixed)
                     + ")")
        if p.journal or p.extra.get("via"):
            nm = (repr(p.names[0]) if p.names[0] == p.names[1]
                  or not p.names[1] else
                  f"{p.names[0]!r} / {p.names[1]!r}")
            what = ("the broker's journal moved the units between the "
                    "two listings" if p.journal else
                    "a transfer between two listings a broker journal "
                    "joined")
            undo.append(f"- {p.out.symbol} ↔ {p.into.symbol}: {what}, "
                        f"both legs naming one security ({nm}); if they "
                        f"are not one security, add `DISTINCT "
                        f"{p.out.symbol} {p.into.symbol}` to ticker.map")
            continue
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
             "loss rules), as a ticker.map "
             + " / ".join(sorted(kinds)) + " line would — this "
             "changes your books."] + undo)


def map_split(refused: Iterable[Pair], map_named: Iterable[str],
              map_renames: Optional[Dict[str, str]] = None,
              map_distinct: Iterable[Iterable[str]] = ()
              ) -> List[Tuple[Pair, str, str, str]]:
    """The pairs the user's map refused ("map", analyze) whose legs the
    map books as two different symbols — each leg is then left unpaired:
    the units leave one security and arrive in another. [(pair, the
    out-leg as booked, the in-leg as booked, the ticker.map line that
    books them as one)] — the line maps the leg no line names onto the
    other leg as booked; "" when the map names both. Only pairs whose
    legs' names agree (analyze's verdict "": a quantity and a date in
    common with an unrelated or unnamed leg are no move), and none a
    `DISTINCT` line keeps apart (the legs, or the legs as booked)."""
    named = {s.upper() for s in map_named}
    ren = {str(k).upper(): str(v).upper()
           for k, v in (map_renames or {}).items()}
    apart = {frozenset(str(x).upper() for x in pair)
             for pair in map_distinct}
    out: List[Tuple[Pair, str, str, str]] = []
    for p in refused:
        if p.extra.get("refused") != "map":
            continue
        if p.extra.get("verdict"):
            continue                    # the names do not show one move
        eo = ren.get(p.out.symbol, p.out.symbol)
        ei = ren.get(p.into.symbol, p.into.symbol)
        if eo == ei:
            continue                    # the map books them as one
        if {frozenset((p.into.symbol, eo)), frozenset((p.out.symbol, ei)),
                frozenset((eo, ei))} & apart:
            continue                    # the user keeps them apart
        if p.into.symbol not in named:
            line = f"TOBASE {p.into.symbol} {eo}"
        elif p.out.symbol not in named:
            line = f"TOBASE {p.out.symbol} {ei}"
        else:
            line = ""
        out.append((p, eo, ei, line))
    return out


def map_split_note(account: str, items: Iterable[Tuple[Pair, str, str, str]]
                   ) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the one Warning per account naming the
    transfer pairs its ticker.map books as two securities (map_split);
    None when there are none."""
    heads: List[str] = []
    details: List[str] = []
    for p, eo, ei, line in items:
        if account not in (p.out.account, p.into.account):
            continue
        q = f"{-p.out.quantity:g}"
        heads.append(f"{q} {p.out.symbol} out {p.out.date} (booked as "
                     f"{eo}) / {p.into.symbol} in {p.into.date}"
                     + (f" (booked as {ei})" if ei != p.into.symbol
                        else ""))
        if line:
            # The leg the map names: its line may be the one to correct.
            other = (p.out.symbol if line.split()[1] == p.into.symbol
                     else p.into.symbol)
            fix = (f"if they are one security, add `{line}` to "
                   f"ticker.map (or correct the line naming {other})")
        else:
            fix = (f"if they are one security, make the lines naming "
                   f"{p.out.symbol} and {p.into.symbol} book them as one "
                   f"symbol")
        details.append(f"- {p.out.symbol} → {p.into.symbol} "
                       f"({p.out.account} → {p.into.account}): {fix}; if "
                       f"they are two, add `DISTINCT {p.out.symbol} "
                       f"{p.into.symbol}`")
    if not heads:
        return None
    return (f"{account}: ticker.map books the two legs of a transfer as two "
            f"securities: " + "; ".join(heads),
            ["The legs pair as one move (the same quantity, within "
             f"{PAIR_DAYS} business days), but the units leave one "
             "security and arrive in another: the out-leg leaves your "
             "books (in a registered account, a withdrawal at fair "
             "value) and the in-leg opens a position with no cost "
             "carried."] + details
            + ["`taxjson journals` lists the pair; `run --strict` stops on "
               "it."])
