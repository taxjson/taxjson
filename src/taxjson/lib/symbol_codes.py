"""A broker's internal security codes resolved to real tickers (tax-logic
CA-ACB-CODES / US-BASIS-CODES).

Questrade's website export writes some rows of a security under an
internal code (one letter + digits, X000123) instead of its ticker --
typically shares transferred in from another broker, before Questrade
links them to the listing: the transfer-in, its dividends, later rows.
The parser resolves a code from the account's own trades and transfers
of the same description (questrade.QtAccountContext). A code nothing in
the account resolves was a security of its own, with one ATTENTION line
asking for a ticker.map GLOBAL line.

`taxjson run` now infers the ticker from the rest of the project's books
before the account is parsed, in this order, and records the evidence:

1. transfer pairing: a transfer-in of the code (quantity q on date d) is
   the arrival of an OUTGOING transfer of q shares in another broker's
   export of the project (any account) dated from PAIR_DAYS_BEFORE days
   before d to PAIR_DAYS_AFTER days after it, whose security name
   matches the code's description (names_match: the same company AND
   the same share designators). Every transfer-in that pairs must name
   the same ticker, an outgoing transfer may pair with one code only,
   and a leg of another class of the same company in the window makes
   it ambiguous;
2. name match (the code has no transfer-in at all): the code's
   normalised name (name_tokens) EQUALS the name of exactly ONE listing
   known elsewhere in the project's books — share designators
   included;
3. otherwise the code stays as exported: one ATTENTION line per code
   with the GLOBAL line to add (and a quantity/date or near-name
   candidate, with its GLOBAL line, when one exists but is not
   certain).

Any ticker.map rule naming the code's listing (a rename, DELETE,
DISTINCT, a dated RENAME) always wins: such a code is never inferred and
is recorded under "mapped". No name->ticker table is kept: names and
tickers come from the user's own exports; the only word lists are the
generic corporate-form / share-word vocabulary set aside and the share
designators kept when comparing names.

The result is written to work/<acct>_symbol_codes.state (JSON) and passed
to the parser (`taxjson-brokerage --symbol-codes`), which books every row
of a resolved code under the ticker and prints ONE note per account
(codes_note) instead of the per-code ATTENTION lines.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SUFFIX = "_symbol_codes.state"
FORMAT = "symbol_codes/1"

# The window an outgoing transfer may sit in around the arrival: the
# sending broker usually books it first.
PAIR_DAYS_BEFORE = 10
PAIR_DAYS_AFTER = 3

# The note's fixed head: the console echoes it from the parse .diag and
# tests / tools parse the entries after it (parse_codes_note).
NOTE_HEAD = "note: Questrade internal symbol codes resolved"

# Words that say nothing about WHICH security a name is: the broker's
# transfer wording and the generic "common shares" words. Dropped.
_GENERIC = frozenset("""
COMMON COM STOCK STK SHARES SHARE SHS REGISTERED REG EACH REPRESENTING
REPR REP RECEIPT RECEIPTS
TRANSFER TRANSFERRED TFER TFR IN OUT FROM TO ACATS ATON INTERDEPOT
DELIVER DELIVERED RECEIVE RECEIVED
""".split())
# Corporate-form words: noise, but only because every other word of the
# two names (the share designators included) must then agree — "QZX INC"
# is "QZX CORP", never "NEW QZX CORP" nor "QZX INC CL B".
_FORM = frozenset("""
INC INCORPORATED CORP CORPORATION CO COMPANY LTD LIMITED PLC LLC LP LLP
SA NV AG SE ASA AB OYJ SPA BV THE
""".split())
# Words that say WHICH share of a company a name is — its voting rights,
# a depositary wrapper, an ordinary / preferred share, a unit / warrant /
# right, a successor issuer (NEW) — kept as designators, one spelling
# each (marked "~"). CL / CLASS themselves are dropped: the class LETTER
# after them is the designator, and so is any lone letter ("QZBRK B").
# Generic vocabulary, not security data.
_DESIGNATORS = {
    "VOTING": "VOTING", "VTG": "VOTING", "VOT": "VOTING",
    "SUB": "SUBORDINATE", "SUBORD": "SUBORDINATE",
    "SUBORDINATE": "SUBORDINATE",
    "NON": "NON", "NONVOTING": "NON",
    "MULTIPLE": "MULTIPLE", "MULT": "MULTIPLE",
    "RESTRICTED": "RESTRICTED", "RESTR": "RESTRICTED",
    "ADR": "ADR", "ADRS": "ADR", "ADS": "ADR", "SPONSORED": "ADR",
    "SPON": "ADR", "DEPOSITARY": "ADR", "DEPOSITORY": "ADR",
    "UNSPONSORED": "UNSPONSORED",
    "ORD": "ORDINARY", "ORDINARY": "ORDINARY",
    "PFD": "PREFERRED", "PREF": "PREFERRED", "PREFERRED": "PREFERRED",
    "SER": "SERIES", "SERIES": "SERIES",
    "UNIT": "UNIT", "UNITS": "UNIT", "UTS": "UNIT",
    "WARRANT": "WARRANT", "WARRANTS": "WARRANT", "WTS": "WARRANT",
    "WT": "WARRANT",
    "RIGHT": "RIGHT", "RIGHTS": "RIGHT", "RTS": "RIGHT",
    "NEW": "NEW",
}
_CLASS_WORDS = frozenset(("CL", "CLASS"))
# The voting-rights designators a broker may leave out after the class
# letter (Questrade cuts "CLASS B SUBORDINATE VOTING" to "CL B").
_VOTING = frozenset(("~VOTING", "~SUBORDINATE", "~NON", "~MULTIPLE",
                     "~RESTRICTED"))

_WORD_RE = re.compile(r"[A-Z0-9]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_INTERNAL_CODE_RE = re.compile(r"^[A-Z]\d+$")


def name_tokens(text: str) -> Tuple[str, ...]:
    """A security name normalised for comparing: the words that name the
    company, in order, upper-cased, then its share designators sorted
    (each marked "~": the class letter, VOTING / SUBORDINATE / NON /
    MULTIPLE, ADR, ORDINARY, PREFERRED, UNIT, WARRANT, RIGHT, NEW).
    Generic share words, the transfer wording and corporate-form words
    (INC, CORP, LTD ...) are dropped; punctuation and case never
    count."""
    core: List[str] = []
    marks = set()
    for w in _WORD_RE.findall(str(text or "").upper()):
        if w in _GENERIC or w in _FORM or w in _CLASS_WORDS:
            continue
        if w in _DESIGNATORS:
            marks.add("~" + _DESIGNATORS[w])
        elif len(w) == 1 and not w.isdigit():
            marks.add("~" + w)
        elif w not in core:
            core.append(w)
    return tuple(core) + tuple(sorted(marks))


def _core(tokens: Iterable[str]) -> Tuple[str, ...]:
    return tuple(t for t in tokens if not t.startswith("~"))


def _marks(tokens: Iterable[str]) -> frozenset:
    return frozenset(t for t in tokens if t.startswith("~"))


def _strong(tokens: Iterable[str]) -> bool:
    return any(len(t) >= 3 and not t.isdigit() and not t.startswith("~")
               for t in tokens)


def _n_strong(tokens: Iterable[str]) -> int:
    return sum(1 for t in tokens
               if len(t) >= 3 and not t.isdigit() and not t.startswith("~"))


def _companies_match(a: Tuple[str, ...], b: Tuple[str, ...]) -> bool:
    """The company words of two names agree (transfer pairing): the same
    words; or the same first word with one name's words all in the
    other's and at least two strong words shared. A one-word name
    matches only itself ("BANK" never pairs "BANK OF QZLAND")."""
    ca, cb = _core(a), _core(b)
    if not ca or not cb or not (_strong(ca) and _strong(cb)):
        return False
    sa, sb = set(ca), set(cb)
    if sa == sb:
        return True
    if len(ca) == 1 or len(cb) == 1 or ca[0] != cb[0]:
        return False
    return (sa <= sb or sb <= sa) and _n_strong(sa & sb) >= 2


def _same_share(a: Tuple[str, ...], b: Tuple[str, ...]) -> bool:
    """The share designators of two names agree. For transfer pairing
    (the quantity and date already agree) a name may leave out the
    voting words when both name the same class letter."""
    ma, mb = _marks(a), _marks(b)
    if ma == mb:
        return True
    la, lb = ({m for m in ma if len(m) == 2},
              {m for m in mb if len(m) == 2})
    return bool(la) and la == lb and (ma ^ mb) <= _VOTING


def name_relation(a: Tuple[str, ...], b: Tuple[str, ...]) -> str:
    """How two normalised names relate (transfer pairing): "same" (one
    company, the same share), "class" (one company, but the share class /
    voting / ADR / NEW designators differ — e.g. class A vs class C), or
    "" (different companies)."""
    if not _companies_match(a, b):
        return ""
    return "same" if _same_share(a, b) else "class"


def names_match(a: Tuple[str, ...], b: Tuple[str, ...]) -> bool:
    """Two normalised names name the same share (transfer pairing)."""
    return name_relation(a, b) == "same"


def is_code(symbol: str) -> bool:
    """A broker-internal code (one letter + digits), with or without a
    listing suffix."""
    root = str(symbol or "").upper().split(".")[0]
    return bool(_INTERNAL_CODE_RE.match(root))


def _plain_listing(symbol: str) -> bool:
    """A share listing (ROOT.SUFFIX), not an option / future / code."""
    s = str(symbol or "")
    return (bool(s) and " " not in s and not s.startswith("F:")
            and not _CONTROL_RE.search(s)
            and "." in s and not is_code(s)
            and not re.search(r"\d{6}[CP]\d", s))


def _d(s: Any) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


# ------------------------------------------------------------ inputs

@dataclass
class CodeUse:
    """One internal code in the receiving account's exports that the
    account's own rows do not resolve."""
    code: str
    name: str                                   # description key
    currencies: List[str] = field(default_factory=list)
    # (date, quantity) of each transfer-in row of the code
    arrivals: List[Tuple[str, float]] = field(default_factory=list)
    rows: int = 0


@dataclass
class OutLeg:
    """An outgoing transfer in another broker's export of the project."""
    account: str
    broker: str
    symbol: str
    date: str
    quantity: float                             # shares sent (> 0)
    currency: str
    names: List[Tuple[str, ...]]


@dataclass
class NameEntry:
    symbol: str
    tokens: Tuple[str, ...]
    shown: str                                  # the name as written
    account: str
    broker: str


def _broker_name(broker: str) -> str:
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    b = str(broker or "")
    if b.startswith("generic-"):
        return b[len("generic-"):]
    return DISPLAY_NAMES.get(b, b or "broker")


def _row_name(t: Dict[str, Any], broker: str) -> str:
    """The security name a parsed row carries: IB's Financial Instrument
    name, else the description of a broker whose descriptions name the
    company (an IB description is the transfer kind or the raw
    symbol)."""
    nm = str(t.get("security_name") or "").strip()
    if nm:
        return nm
    if broker == "ib":
        return ""
    desc = str(t.get("description") or "").strip()
    if broker == "questrade":
        from taxjson.lib.brokerages.questrade import _get_desc_key
        return _get_desc_key(desc)
    return desc


def _load(p: Path) -> Optional[Dict[str, Any]]:
    """A JSON object from `p`, else None (missing, unreadable, not JSON,
    nested too deep to parse, not an object)."""
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) else None


def _parser_ids() -> List[str]:
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    return sorted(DISPLAY_NAMES)


def project_evidence(cache: Path, accounts: Iterable[str], *,
                     receiving: Tuple[str, str]
                     ) -> Tuple[List[OutLeg], List[NameEntry]]:
    """(outgoing transfers, known names) from every account's parsed
    exports in work/ (`accounts`: the configured equity account names).
    The receiving (account, broker) group is left out, and so are
    Questrade's own rows of every account (their symbols may be this
    very inference, written back), except their real tickers' names from
    rows that are not themselves inferred (`<acct>_symbol_codes.state`
    lists those)."""
    outs: List[OutLeg] = []
    names: List[NameEntry] = []
    ids = _parser_ids()
    for acct in sorted(set(accounts)):
        files: List[Tuple[str, Path, bool]] = []
        for b in ids:
            files.append((b, cache / f"{acct}_{b}.json", False))
            files.append((b, cache / f"{acct}_{b}_transfers.json", True))
        for p in sorted(cache.glob(f"{acct}_generic-*.json")):
            stem = p.name[len(acct) + 1:-len(".json")]
            if stem.endswith("_transfers"):
                files.append((stem[:-len("_transfers")], p, True))
            else:
                files.append((stem, p, False))
        _st = read_state(cache / f"{acct}{SUFFIX}")
        inferred = {str(r.get("symbol") or "").upper()
                    for r in (_st.get("resolved") or {}).values()}
        for broker, p, sidecar in files:
            if (acct, broker) == tuple(receiving):
                continue
            if broker in ("coinbase", "kraken"):
                continue
            doc = _load(p)
            if doc is None:
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
                if not _plain_listing(sym):
                    continue
                if broker == "questrade" and sym in inferred:
                    continue
                nm = _row_name(t, broker)
                toks = name_tokens(nm)
                if toks:
                    # One line of text: it is quoted in the record and
                    # a warning (a control character shown escaped).
                    names.append(NameEntry(
                        sym, toks, _CONTROL_RE.sub(
                            lambda m: "\\x%02x" % ord(m.group(0)),
                            " ".join(nm.split())), acct, broker))
                if (t.get("action") != "TRANSFER" or broker == "questrade"):
                    continue
                try:
                    q = float(t.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    continue
                if q >= -1e-9:
                    continue
                outs.append(OutLeg(acct, broker, sym,
                                   str(t.get("date") or "")[:10], -q,
                                   str(t.get("currency") or "").upper(),
                                   [toks] if toks else []))
    # An outgoing leg with no name of its own takes the names its
    # account's rows give the same symbol (IB: the trades' instrument
    # names).
    by = {}
    for n in names:
        by.setdefault((n.account, n.broker, n.symbol), set()).add(n.tokens)
    for o in outs:
        extra = sorted(by.get((o.account, o.broker, o.symbol), ()))
        o.names = sorted(set(o.names) | set(extra))
    outs.sort(key=lambda o: (o.date, o.account, o.broker, o.symbol,
                             o.quantity))
    return outs, names


# ------------------------------------------------------------ inference

def _suggest(code_listing: Callable[[str, str], str], code: str,
             listing: str, currencies: List[str],
             listing_ok: Callable[[str, str], bool]) -> str:
    """The ticker.map line that would book `code` as `listing`."""
    cur = next((c for c in currencies if listing_ok(listing, c)),
               currencies[0] if currencies else "")
    return f"GLOBAL {code_listing(code, cur)} {listing}"


def _default_code_listing(code: str, cur: str) -> str:
    from taxjson.lib.brokerages.questrade import QuestradeBrokerage
    return QuestradeBrokerage().apply_currency_suffix(code, cur)


def resolve(uses: Iterable[CodeUse], outs: List[OutLeg],
            names: List[NameEntry], *,
            listing_ok: Callable[[str, str], bool],
            mapped: Callable[[str], bool] = lambda _c: False,
            code_listing: Optional[Callable[[str, str], str]] = None
            ) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{"resolved": {code: {...}}, "unresolved": {code: {...}},
    "mapped": {code: {...}}} for the receiving account's codes.
    `listing_ok(listing, currency)`: the listing is the one the receiving
    parser books a row of that currency under (a CAD row never resolves
    to a .US listing). `mapped(code)`: a ticker.map rule names the code
    (any rule: a rename, DELETE, DISTINCT, a dated RENAME) — never
    inferred, recorded under "mapped". `code_listing(code, currency)`:
    the code's own listing, for the GLOBAL line an unresolved code's
    warning suggests."""
    code_listing = code_listing or _default_code_listing
    uses = sorted(uses, key=lambda u: u.code)
    resolved: Dict[str, Dict[str, Any]] = {}
    unresolved: Dict[str, Dict[str, Any]] = {}
    mapped_out: Dict[str, Dict[str, Any]] = {}
    # code -> [(arrival, [candidate legs])]
    pairs: Dict[str, List[Tuple[Tuple[str, float], List[OutLeg]]]] = {}
    # legs whose quantity and date pair but whose names name another
    # share of the same company (class A vs class C, ordinary vs ADR)
    siblings: Dict[str, List[OutLeg]] = {}
    loose: Dict[str, List[OutLeg]] = {}
    for u in uses:
        if mapped(u.code):
            mapped_out[u.code] = {"how": "ticker.map",
                                  "evidence": "ticker.map rule"}
            continue
        toks = name_tokens(u.name)
        pairs[u.code] = []
        for d, q in sorted(u.arrivals):
            dd = _d(d)
            cands, near = [], []
            for o in outs:
                od = _d(o.date)
                if dd is None or od is None:
                    continue
                gap = (dd - od).days
                if not (-PAIR_DAYS_AFTER <= gap <= PAIR_DAYS_BEFORE):
                    continue
                if abs(o.quantity - q) > 1e-6:
                    continue
                if not any(listing_ok(o.symbol, c) for c in u.currencies):
                    continue
                rel = {name_relation(toks, n) for n in o.names}
                if "same" in rel:
                    cands.append(o)
                elif "class" in rel:
                    siblings.setdefault(u.code, []).append(o)
                else:
                    near.append(o)
            pairs[u.code].append(((d, q), cands))
            loose.setdefault(u.code, []).extend(near)
    # One outgoing transfer pairs with one code.
    claimed: Dict[int, set] = {}
    for code, arr in pairs.items():
        for _a, cands in arr:
            for o in cands:
                claimed.setdefault(id(o), set()).add(code)
    for u in uses:
        if u.code not in pairs:
            continue
        arr = pairs[u.code]
        listings = set()
        conflict = False
        legs: List[OutLeg] = []
        for _a, cands in arr:
            ls = {o.symbol for o in cands}
            if len(ls) > 1:
                conflict = True
            listings |= ls
            legs += cands
        sib = sorted({o.symbol for o in siblings.get(u.code, [])}
                     - listings)
        shared = sorted({c for o in legs for c in claimed[id(o)]} - {u.code})
        if legs and (conflict or len(listings) > 1 or shared or sib):
            if sib:
                why = (f"it pairs with transfers out of "
                       f"{', '.join(sorted(listings | set(sib)))} by "
                       f"quantity and date, and their names differ only in "
                       f"the share class or form")
            elif len(listings) > 1:
                why = (f"it pairs with transfers out of "
                       f"{', '.join(sorted(listings))}")
            else:
                why = (f"the same transfer out also pairs with "
                       f"{', '.join(shared)}")
            unresolved[u.code] = {"reason": "ambiguous",
                                  "candidates": sorted(listings | set(sib)),
                                  "detail": why}
            continue
        if legs:
            o = sorted(legs, key=lambda x: (x.date, x.account))[0]
            resolved[u.code] = {
                "symbol": o.symbol, "currency": o.currency or
                (u.currencies[0] if u.currencies else ""),
                "how": "transfer",
                "evidence": (f"paired with the {_broker_name(o.broker)} "
                             f"transfer out of {o.quantity:g} on {o.date}, "
                             f"account {o.account}"),
            }
            continue
        if sib:
            o = sorted(siblings[u.code], key=lambda x: (x.date, x.symbol))[0]
            line = _suggest(code_listing, u.code, o.symbol, u.currencies,
                            listing_ok)
            unresolved[u.code] = {
                "reason": "class_differs", "candidates": sib,
                "detail": (f"the {_broker_name(o.broker)} transfer out of "
                           f"{o.quantity:g} {o.symbol} on {o.date} pairs by "
                           f"quantity and date, but its name is another "
                           f"share class or form of the company — add "
                           f"`{line}` to ticker.map if it is the same "
                           f"share")}
            continue
        # 2. name match — only for a code with no transfer-in at all
        # (a transfer-in that pairs with nothing is not identified by
        # name: the near miss is named instead). The normalised names
        # must be EQUAL, share designators included, and name exactly
        # one listing; anything less is only suggested.
        toks = name_tokens(u.name)
        hits: Dict[str, NameEntry] = {}
        alike: Dict[str, NameEntry] = {}
        if _strong(toks):
            for n in sorted(names, key=lambda n: (n.symbol, n.account,
                                                  n.broker)):
                if not any(listing_ok(n.symbol, c) for c in u.currencies):
                    continue
                if n.tokens == toks:
                    hits.setdefault(n.symbol, n)
                elif (name_relation(toks, n.tokens)
                      or set(_core(toks)) == set(_core(n.tokens))):
                    alike.setdefault(n.symbol, n)
        if len(hits) == 1 and not u.arrivals:
            n = next(iter(hits.values()))
            cur = next((c for c in u.currencies
                        if listing_ok(n.symbol, c)), "")
            resolved[u.code] = {
                "symbol": n.symbol, "currency": cur, "how": "name",
                "evidence": (f"name match: {n.shown!r} on "
                             f"{_broker_name(n.broker)} rows of account "
                             f"{n.account}"),
            }
            continue
        info: Dict[str, Any] = {"reason": ("ambiguous" if len(hits) > 1
                                           else "no_evidence")}
        near = sorted({(o.symbol, o.date, o.quantity, o.broker)
                       for o in loose.get(u.code, [])})
        if len(hits) > 1:
            info["candidates"] = sorted(hits)
            info["detail"] = (f"its name matches "
                              f"{', '.join(sorted(hits))}")
        elif near:
            s_, d, q, b = near[0]
            info["candidates"] = sorted({x[0] for x in near})
            info["detail"] = (f"the {_broker_name(b)} transfer out of "
                              f"{q:g} {s_} on {d} pairs by quantity and "
                              f"date, but the names do not confirm it")
        elif hits or alike:
            look = hits or alike
            n = next(iter(look.values()))
            info["candidates"] = sorted(look)
            why = ("its transfer-in pairs with no transfer out in the "
                   "project" if u.arrivals else
                   "the names are not equal word for word")
            if len(look) == 1:
                line = _suggest(code_listing, u.code, n.symbol,
                                u.currencies, listing_ok)
                info["detail"] = (
                    f"looks like {n.symbol} by name ({n.shown!r} on "
                    f"{_broker_name(n.broker)} rows of account "
                    f"{n.account}), not applied: {why} — add `{line}` "
                    f"to ticker.map if right")
            else:
                info["detail"] = (
                    f"its name resembles {', '.join(sorted(look))}, not "
                    f"applied: {why}")
        unresolved[u.code] = info
    return {"resolved": resolved, "unresolved": unresolved,
            "mapped": mapped_out}


# ------------------------------------------------------------ the record

_BUCKETS = ("resolved", "unresolved", "mapped")


def state_text(account: str, result: Dict[str, Any]) -> str:
    return json.dumps({"format": FORMAT, "account": account,
                       **{k: result.get(k) or {} for k in _BUCKETS}},
                      indent=2, sort_keys=True) + "\n"


def _text_ok(v: Any) -> bool:
    return isinstance(v, str) and not _CONTROL_RE.search(v)


def state_problem(doc: Dict[str, Any]) -> Optional[str]:
    """Why a parsed record is not one this module wrote (None: it is).
    Every bucket a table of internal codes; a resolved code names a
    plain share listing with its evidence; every text one line."""
    if not _text_ok(doc.get("account", "")):
        return "the account is not a line of text"
    for key in _BUCKETS:
        v = doc.get(key, {})
        if not isinstance(v, dict):
            return f"{key!r} is not a table of codes"
        for code, r in v.items():
            if not (isinstance(code, str) and _INTERNAL_CODE_RE.match(code)):
                return f"{key!r} holds {code!r}, not an internal code"
            if not isinstance(r, dict):
                return f"{key!r} entry {code} is not a table"
            for f, x in r.items():
                if f == "candidates":
                    if not (isinstance(x, list)
                            and all(_text_ok(c) for c in x)):
                        return f"{key!r} entry {code}: bad candidates"
                elif not _text_ok(x):
                    return f"{key!r} entry {code}: {f!r} is not one line " \
                           f"of text"
    for code, r in doc.get("resolved", {}).items():
        if not _plain_listing(r.get("symbol")):
            return f"resolved entry {code}: {r.get('symbol')!r} is not a " \
                   f"share listing"
        if not r.get("evidence"):
            return f"resolved entry {code} has no evidence"
    return None


_WARNED: set = set()


def read_state(path: Optional[Path]) -> Dict[str, Any]:
    """The recorded inference ({} when none). A record that is not one
    this module wrote (not JSON, nested too deep, a wrong shape, a
    symbol that is not a plain listing, a control character) is
    ignored with ONE warning per file — never a traceback; `taxjson
    run` writes it afresh."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    doc = _load(p)
    why = None
    if doc is None:
        why = "not a JSON object"
    elif doc.get("format") != FORMAT:
        why = f"format is not {FORMAT}"
    else:
        why = state_problem(doc)
    if why is None:
        return doc
    key = str(p.resolve())
    if key not in _WARNED:
        _WARNED.add(key)
        from taxjson.lib.brokerages.base import shown_name
        from taxjson.lib.stage_msg import emit_line
        emit_line(f"warning: {shown_name(p)}: ignored ({why}) — the "
                  f"Questrade internal codes it records are not applied; "
                  f"`taxjson run` writes it afresh.")
    return {}


def codes_note(resolved: Dict[str, Dict[str, Any]]) -> str:
    """ONE line per account for the resolved codes (the .diag / .sum keep
    it whole; parse_codes_note reads it back):

    note: Questrade internal symbol codes resolved (2): X1 → AAA.US
    (paired with ...); X2 → BBB.TO (name match: ...) — inferred from the
    project's other exports; a ticker.map GLOBAL line overrides one."""
    items = [f"{c} → {r['symbol']} ({r['evidence']})"
             for c, r in sorted(resolved.items())]
    return (f"{NOTE_HEAD} ({len(items)}): " + "; ".join(items)
            + " — inferred from the project's other exports (`taxjson "
              "transfers` lists them); a ticker.map GLOBAL line for the "
              "code overrides the inference.")


_ENTRY_RE = re.compile(r"([A-Z]\d+) → (\S+) \(((?:[^()]|\([^()]*\))*)\)")


def parse_codes_note(line: str) -> Dict[str, Tuple[str, str]]:
    """{code: (listing, evidence)} from a codes_note line."""
    if NOTE_HEAD not in line:
        return {}
    return {m.group(1): (m.group(2), m.group(3))
            for m in _ENTRY_RE.finditer(line)}
