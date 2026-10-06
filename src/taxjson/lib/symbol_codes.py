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
   matches the code's description (names_match). Every transfer-in that
   pairs must name the same ticker, and an outgoing transfer may pair
   with one code only;
2. name match (no transfer-in pairs at all): the code's company name is,
   word for word after normalising, the name of exactly ONE listing
   known elsewhere in the project's books;
3. otherwise the code stays as exported: one ATTENTION line per code
   with the GLOBAL line to add (and a quantity/date candidate when one
   exists but the names do not confirm it).

A ticker.map rule for the code's listing always wins: such a code is
never inferred. No name->ticker table is kept: names and tickers come
from the user's own exports; the only word list is the generic
corporate-form / share-class vocabulary stripped before comparing names.

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

# Generic words that say what KIND of share or company a name is, not
# which one: corporate forms, share classes, depositary wrappers, and
# the transfer wording a broker appends. Not security data.
_NOISE = frozenset("""
INC INCORPORATED CORP CORPORATION CO COMPANY LTD LIMITED PLC LLC LP LLP
SA NV AG SE ASA AB OYJ SPA BV THE
CLASS CL COMMON COM STOCK STK SHARES SHARE SHS ORDINARY ORD SUBORDINATE
VOTING VTG NEW
ADR ADS SPONSORED SPON UNSPONSORED DEPOSITARY DEPOSITORY RECEIPT RECEIPTS
REPRESENTING REP EACH REGISTERED REG
TRANSFER TRANSFERRED TFER TFR IN OUT FROM TO ACATS ATON INTERDEPOT
DELIVER DELIVERED RECEIVE RECEIVED
""".split())

_WORD_RE = re.compile(r"[A-Z0-9]+")
_INTERNAL_CODE_RE = re.compile(r"^[A-Z]\d+$")


def name_tokens(text: str) -> Tuple[str, ...]:
    """The words that name the company, in order, upper-cased: noise words
    (_NOISE) and single letters (a share-class letter) dropped."""
    out: List[str] = []
    for w in _WORD_RE.findall(str(text or "").upper()):
        if w in _NOISE or (len(w) == 1 and not w.isdigit()):
            continue
        if w not in out:
            out.append(w)
    return tuple(out)


def _strong(tokens: Iterable[str]) -> bool:
    return any(len(t) >= 3 and not t.isdigit() for t in tokens)


def names_match(a: Tuple[str, ...], b: Tuple[str, ...]) -> bool:
    """Two normalised names name the same company (transfer pairing, where
    the quantity and date already agree): the same words, or the same
    first word with one name's words all in the other's."""
    if not a or not b or not (_strong(a) and _strong(b)):
        return False
    sa, sb = set(a), set(b)
    if sa == sb:
        return True
    if a[0] != b[0]:
        return False
    return sa <= sb or sb <= sa


def is_code(symbol: str) -> bool:
    """A broker-internal code (one letter + digits), with or without a
    listing suffix."""
    root = str(symbol or "").upper().split(".")[0]
    return bool(_INTERNAL_CODE_RE.match(root))


def _plain_listing(symbol: str) -> bool:
    """A share listing (ROOT.SUFFIX), not an option / future / code."""
    s = str(symbol or "")
    return (bool(s) and " " not in s and not s.startswith("F:")
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
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
            for t in doc.get("transactions") or []:
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
                    names.append(NameEntry(sym, toks, " ".join(nm.split()),
                                           acct, broker))
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

def resolve(uses: Iterable[CodeUse], outs: List[OutLeg],
            names: List[NameEntry], *,
            listing_ok: Callable[[str, str], bool],
            mapped: Callable[[str], bool] = lambda _c: False
            ) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{"resolved": {code: {...}}, "unresolved": {code: {...}}} for the
    receiving account's codes. `listing_ok(listing, currency)`: the
    listing is the one the receiving parser books a row of that currency
    under (a CAD row never resolves to a .US listing). `mapped(code)`:
    ticker.map already renames the code (never inferred)."""
    uses = sorted(uses, key=lambda u: u.code)
    resolved: Dict[str, Dict[str, Any]] = {}
    unresolved: Dict[str, Dict[str, Any]] = {}
    # code -> [(arrival, [candidate legs])]
    pairs: Dict[str, List[Tuple[Tuple[str, float], List[OutLeg]]]] = {}
    loose: Dict[str, List[OutLeg]] = {}
    for u in uses:
        if mapped(u.code):
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
                if any(names_match(toks, n) for n in o.names):
                    cands.append(o)
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
        shared = sorted({c for o in legs for c in claimed[id(o)]} - {u.code})
        if legs and (conflict or len(listings) > 1 or shared):
            why = (f"it pairs with transfers out of "
                   f"{', '.join(sorted(listings))}" if len(listings) > 1
                   else f"the same transfer out also pairs with "
                        f"{', '.join(shared)}")
            unresolved[u.code] = {"reason": "ambiguous",
                                  "candidates": sorted(listings),
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
        # 2. name match: no transfer-in of the code paired at all.
        toks = name_tokens(u.name)
        hits: Dict[str, NameEntry] = {}
        if _strong(toks):
            for n in sorted(names, key=lambda n: (n.symbol, n.account,
                                                  n.broker)):
                if (set(n.tokens) == set(toks)
                        and any(listing_ok(n.symbol, c)
                                for c in u.currencies)):
                    hits.setdefault(n.symbol, n)
        if len(hits) == 1:
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
        info: Dict[str, Any] = {"reason": ("ambiguous" if hits
                                           else "no_evidence")}
        if hits:
            info["candidates"] = sorted(hits)
            info["detail"] = (f"its name matches "
                              f"{', '.join(sorted(hits))}")
        else:
            near = sorted({(o.symbol, o.date, o.quantity, o.broker)
                           for o in loose.get(u.code, [])})
            if near:
                s, d, q, b = near[0]
                info["candidates"] = sorted({x[0] for x in near})
                info["detail"] = (f"the {_broker_name(b)} transfer out of "
                                  f"{q:g} {s} on {d} pairs by quantity and "
                                  f"date, but the names do not confirm it")
        unresolved[u.code] = info
    return {"resolved": resolved, "unresolved": unresolved}


# ------------------------------------------------------------ the record

def state_text(account: str, result: Dict[str, Any]) -> str:
    return json.dumps({"format": FORMAT, "account": account,
                       "resolved": result.get("resolved") or {},
                       "unresolved": result.get("unresolved") or {}},
                      indent=2, sort_keys=True) + "\n"


def read_state(path: Optional[Path]) -> Dict[str, Any]:
    """The recorded inference ({} when none or unreadable)."""
    if not path:
        return {}
    doc = _load(Path(path))
    if not doc or doc.get("format") != FORMAT:
        return {}
    return doc


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
