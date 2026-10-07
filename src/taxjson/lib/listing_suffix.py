"""A listing read from the evidence, not from the row currency alone
(tax-logic CA-XLIST-02 / US-XLIST-02).

Questrade's and RBC's exports write a bare ticker and a Currency column;
their parsers name the listing from the currency (a USD row is ROOT.US,
a CAD row ROOT.TO). That is wrong when the broker files one listing on
the other currency's row: Questrade's website export writes interlisted
shares that arrived from another broker under the TSX ticker on a USD
row (QZCC for the TSX listing of a company whose NYSE ticker is QZCJ) —
QZCC.US, a listing that does not exist. The transfer journal then joined
QZCJ.US to the phantom QZCC.US (lib/cross_listings), and quotes, the
T1135 domicile and the loss rules read a symbol no market lists.

Before such an account is parsed, `taxjson run` reads the listing from
the project's other books instead, in this order:

1. transfer pairing: a transfer-in of the symbol (quantity q, date d) is
   the arrival of exactly one outgoing transfer of q shares, within
   cross_listings.PAIR_DAYS days, in the export of a broker that names
   the listing (not a CURRENCY_SUFFIX_BROKERS export), whose security
   name is EQUAL to the arriving one (symbol_codes.exact_name, as the
   cross-listing join reads names), and that leg is
   * the other listing of the same ticker (QZCC.TO out, QZCC on a USD
     row in): the in-leg is QZCC.TO; or
   * ANOTHER ticker on the same currency's listing (QZCJ.US out, QZCC on
     a USD row in): two US tickers do not name one company's identical
     shares, so the in-leg is the other country's listing, QZCC.TO.
   An out-leg of the very listing the currency names (a custody move),
   or of another ticker on the other listing (an ordinary TSX -> NYSE
   journal), confirms the row currency's listing.
2. the other listing known (a USD row only, of shares that arrived by a
   transfer the books do not pair): the project's books hold ROOT.TO
   elsewhere — another account or broker, Questrade's own CAD or .TO
   rows included — under an EQUAL name: the row is ROOT.TO. Shares
   bought at the broker keep the listing its USD trade rows name: many
   Canadian companies trade under the same ticker on the NYSE, and a
   USD trade of the bare ticker is that US listing.

Never corrected (the symbol keeps the row currency's listing):
* a ticker.map line naming the listing in any keyword (a rename,
  DELETE, a dated RENAME, a QUOTE, T1135, CRYPTO, STABLE or MULT line, an
  EXTRACT target) or a `DISTINCT ROOT.US ROOT.TO` line: the user's map
  wins (the DISTINCT line is the undo a correction's warning names);
* the listing is real: a broker that names its listings (IB, Webull, a
  generic CSV) has rows of it;
* a rename row in the books joins the two tickers (a ticker change, not
  a cross-listing);
* the account's own books hold the other listing in another currency:
  one native-currency pool cannot hold both — said as a suggestion (a
  TOBASE line, which joins them in the base-currency books) instead.

A correction applies to every row of the symbol in that broker's exports
of the account (the transfer-in, later trades, dividends): written to
work/<account>_<broker>_listing_suffix.state and passed to the parse
(`taxjson-brokerage --listing-fixes`), which rewrites the rows before
ticker.map EXTRACT lines (which win) and the transfer sidecar. No
security data is kept here: tickers and names come from the user's
own exports.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

SUFFIX = "_listing_suffix.state"
FORMAT = "listing_suffix/1"

# The parsers whose listing suffix comes from the row currency when the
# export writes a bare ticker (broker format knowledge, not security
# data): their rows are the candidates, and never evidence of a US
# listing. Every other parser names the listing it traded (IB's symbols
# are the venue's own, Webull lists US shares only).
CURRENCY_SUFFIX_BROKERS = ("questrade", "rbc_direct")

# The two listings a currency names: the US one and the TSX one.
_SFX = {"USD": "US", "CAD": "TO"}
_MARKET = {"US": "US", "TO": "TSX"}
_EPS = 1e-6


def state_path(cache: Path, account: str, broker: str) -> Path:
    return Path(cache) / f"{account}_{broker}{SUFFIX}"


def other_listing(listing: str) -> Optional[str]:
    """ROOT.US -> ROOT.TO and back; None for any other spelling (a
    TSX US-dollar unit ROOT.U.TO is its own security)."""
    s = str(listing or "").upper()
    if "." not in s:
        return None
    root, sfx = s.rsplit(".", 1)
    if not root or root.endswith(".U"):
        return None
    if sfx == "US":
        return f"{root}.TO"
    if sfx == "TO":
        return f"{root}.US"
    return None


def _root(listing: str) -> str:
    return str(listing or "").upper().rsplit(".", 1)[0]


def _sfx(listing: str) -> str:
    s = str(listing or "").upper()
    return s.rsplit(".", 1)[1] if "." in s else ""


def _broker_name(broker: str) -> str:
    from taxjson.lib.symbol_codes import _broker_name as bn
    return bn(broker)


# ------------------------------------------------------------ candidates

@dataclass
class Candidate:
    """One listing a currency-suffix parser names from the row currency
    alone, as the account's trades and transfers show it."""
    listing: str                                 # ROOT.US / ROOT.TO
    currency: str                                # the currency it came from
    names: Set[Tuple[str, ...]] = field(default_factory=set)
    shown: Dict[Tuple[str, ...], str] = field(default_factory=dict)
    arrivals: List[Tuple[str, float]] = field(default_factory=list)

    def add_name(self, text: str) -> None:
        from taxjson.lib.symbol_codes import _CONTROL_RE, exact_name
        toks = exact_name(text)
        if toks:
            self.names.add(toks)
            self.shown.setdefault(toks, _CONTROL_RE.sub(
                lambda m: "\\x%02x" % ord(m.group(0)), " ".join(
                    str(text).split())))


@dataclass
class Scan:
    """One (account, broker) group's exports, read before the parse."""
    # the listings named from the row currency alone
    cands: Dict[str, Candidate] = field(default_factory=dict)
    # every share listing of its trades and transfers (a symbol that
    # names its listing too) -> the row currencies it is in
    currencies: Dict[str, Set[str]] = field(default_factory=dict)
    # ... and the names it carries there
    names: Dict[str, Set[Tuple[str, ...]]] = field(default_factory=dict)

    def candidate(self, listing: str, cur: str) -> Candidate:
        c = self.cands.get(listing)
        if c is None:
            c = self.cands[listing] = Candidate(listing, cur)
        return c

    def saw(self, listing: str, cur: str, name: str) -> None:
        from taxjson.lib.symbol_codes import exact_name
        self.currencies.setdefault(listing, set()).add(cur)
        toks = exact_name(name)
        self.names.setdefault(listing, set())
        if toks:
            self.names[listing].add(toks)


def scan_questrade(paths: Iterable[Path]) -> Scan:
    """The bare tickers of a Questrade account's Trades and Transfers
    rows (a .TO / venue-suffixed symbol names its listing; options and
    internal codes are not listings)."""
    from taxjson.lib.brokerages.base import (BrokerageParseError,
                                             canonical_ca_listing,
                                             parse_strict_number)
    from taxjson.lib.brokerages.questrade import (
        _DATE_FMTS, _FX_SETTLED_RE, _INTERNAL_CODE_RE, QuestradeBrokerage,
        _read_qt_rows)
    from taxjson.lib.symbol_codes import questrade_name
    helper = QuestradeBrokerage()
    scan = Scan()
    for p in paths:
        try:
            rows = _read_qt_rows(Path(p))
        except Exception:                          # noqa: BLE001
            continue            # the parse itself names a broken export
        for _ln, row in rows:
            act = (row.get('Activity Type') or '').strip()
            if act not in ('Trades', 'Transfers'):
                continue
            raw = (row.get('Symbol') or '').strip().upper()
            desc = row.get('Description') or ''
            cur = (row.get('Currency') or '').strip().upper()
            if (not raw or raw.startswith('.')
                    or _INTERNAL_CODE_RE.match(raw)
                    or helper.parse_option_from_description(desc)):
                continue
            if cur == 'CAD' and _FX_SETTLED_RE.search(desc):
                cur = 'USD'          # listing currency, not settlement
            if cur not in _SFX:
                continue
            listing = helper.apply_currency_suffix(raw, cur)
            scan.saw(listing, cur, questrade_name(desc))
            if (canonical_ca_listing(raw, cur) is not None
                    or _sfx(listing) != _SFX[cur]
                    or other_listing(listing) is None):
                continue
            c = scan.candidate(listing, cur)
            c.add_name(questrade_name(desc))
            if act != 'Transfers':
                continue
            try:
                q = parse_strict_number(row.get('Quantity'),
                                        field='Quantity', allow_blank=True,
                                        blank=0.0)
            except BrokerageParseError:
                continue
            dt = helper.parse_date(
                (row.get('Transaction Date') or '').strip(), *_DATE_FMTS)
            if dt is not None and q > _EPS:
                c.arrivals.append((dt.strftime('%Y-%m-%d'), q))
    return scan


def scan_rbc(paths: Iterable[Path]) -> Scan:
    """The tickers of an RBC account's trade and transfer rows (RBC
    writes every ticker bare: the parser's suffix is the row
    currency's)."""
    from taxjson.lib.brokerages.base import canonical_ca_listing
    from taxjson.lib.brokerages.rbc_direct import (RbcBrokerage,
                                                   read_rbc_rows)
    from taxjson.lib.corp_actions import (rbc_is_option_code,
                                          rbc_is_temp_symbol)
    from taxjson.lib.symbol_codes import rbc_name
    helper = RbcBrokerage()
    scan = Scan()
    for p in paths:
        try:
            exp = read_rbc_rows(Path(p))
        except Exception:                          # noqa: BLE001
            continue
        for r in exp.rows:
            if r.cls not in ('trade', 'transfer'):
                continue
            raw = (r.symbol or '').strip().upper()
            cur = (r.currency or '').strip().upper()
            if (not raw or rbc_is_temp_symbol(raw) or rbc_is_option_code(raw)
                    or helper._row_occ(r) or cur not in _SFX):
                continue
            listing = helper.apply_currency_suffix(raw, cur)
            # The name: the Symbol Description, else the row description
            # with RBC's wording (a transfer's account reference
            # included) cut (symbol_codes.rbc_name).
            name = r.symdesc or rbc_name(r.desc, trade=r.cls == 'trade')
            scan.saw(listing, cur, name)
            if (canonical_ca_listing(raw, cur) is not None
                    or _sfx(listing) != _SFX[cur]
                    or other_listing(listing) is None):
                continue
            c = scan.candidate(listing, cur)
            c.add_name(name)
            if r.cls == 'transfer' and r.qty > _EPS and r.date:
                c.arrivals.append((r.date[:10], r.qty))
    return scan


SCANNERS: Dict[str, Callable[[Iterable[Path]], Scan]] = {
    "questrade": scan_questrade,
    "rbc_direct": scan_rbc,
}


# ------------------------------------------------------------ evidence

@dataclass
class OutLeg:
    account: str
    broker: str
    symbol: str
    date: str
    quantity: float                              # shares sent (> 0)
    names: Set[Tuple[str, ...]] = field(default_factory=set)


@dataclass
class Evidence:
    outs: List[OutLeg] = field(default_factory=list)
    # listing -> {exact names} from a broker that names its listings
    named: Dict[str, Set[Tuple[str, ...]]] = field(default_factory=dict)
    # listing -> [(account, broker, currency, {names})] from any export
    # (a currency-suffix broker's too, its corrections left out)
    seen: Dict[str, List[Tuple[str, str, str, frozenset]]] = field(
        default_factory=dict)
    renames: Set[frozenset] = field(default_factory=set)
    shown: Dict[Tuple[str, ...], str] = field(default_factory=dict)


def read_state(path: Path) -> Dict[str, Any]:
    """The state file's records ({"corrected": {}, "kept": {}} when
    missing, unreadable or of another format)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        doc = None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return {"corrected": {}, "kept": {}}
    out: Dict[str, Any] = {"account": str(doc.get("account") or ""),
                           "broker": str(doc.get("broker") or "")}
    for k in ("corrected", "kept"):
        v = doc.get(k)
        out[k] = {str(s).upper(): r for s, r in (v or {}).items()
                  if isinstance(r, dict) and isinstance(s, str)
                  and (k == "kept" or isinstance(r.get("symbol"), str))} \
            if isinstance(v, dict) else {}
    return out


def fixes(path: Optional[Path]) -> Dict[str, str]:
    """{listing as the currency names it: the listing the evidence
    names} of a state file (empty when there is none)."""
    if not path:
        return {}
    return {s: str(r["symbol"]).upper()
            for s, r in read_state(Path(path))["corrected"].items()}


def project_evidence(cache: Path, accounts: Iterable[str], *,
                     receiving: Tuple[str, str]) -> Evidence:
    """What the project's other books say about listings: every
    account's parsed exports in work/ (books and transfer sidecars)
    except the receiving (account, broker) group. A currency-suffix
    broker's rows are never an out-leg or a US listing's evidence, and
    the listings its own corrections wrote are left out."""
    from taxjson.lib.cross_listings import _parsed_files
    from taxjson.lib.symbol_codes import (_CONTROL_RE, _plain_listing,
                                          _row_name, exact_name, is_code)
    ev = Evidence()
    accts = sorted(set(accounts))
    by_len = sorted(accts, key=len, reverse=True)
    names_of: Dict[Tuple[str, str, str], Set[Tuple[str, ...]]] = {}
    for acct in accts:
        longer = [o for o in by_len if o != acct
                  and o.startswith(f"{acct}_")]
        for broker, p, sidecar in _parsed_files(Path(cache), acct):
            if (acct, broker) == tuple(receiving):
                continue
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
            naming = broker not in CURRENCY_SUFFIX_BROKERS
            written = set() if naming else {
                str(r["symbol"]).upper() for r in read_state(
                    state_path(cache, acct, broker))["corrected"].values()}
            txs = doc.get("transactions")
            for t in (txs if isinstance(txs, list) else []):
                if not isinstance(t, dict):
                    continue
                sym = str(t.get("symbol") or "").upper()
                new = str(t.get("symbol_new") or "").upper()
                if (t.get("action") == "SPLIT" and new and new != sym
                        and _root(new) != _root(sym)):
                    ev.renames.add(frozenset((_root(sym), _root(new))))
                if not _plain_listing(sym) or is_code(sym):
                    continue
                if sym in written:
                    continue
                nm = _row_name(t, broker)
                toks = exact_name(nm)
                if toks:
                    ev.shown.setdefault(toks, _CONTROL_RE.sub(
                        lambda m: "\\x%02x" % ord(m.group(0)),
                        " ".join(nm.split())))
                    names_of.setdefault((acct, broker, sym), set()).add(toks)
                ev.seen.setdefault(sym, [])
                ev.seen[sym].append((acct, broker, str(
                    t.get("currency") or "").upper(),
                    frozenset([toks] if toks else [])))
                if naming:
                    ev.named.setdefault(sym, set())
                    if toks:
                        ev.named[sym].add(toks)
                if not naming or t.get("action") != "TRANSFER":
                    continue
                try:
                    q = float(t.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    continue
                if q >= -_EPS or not str(t.get("date") or "")[:10]:
                    continue
                ev.outs.append(OutLeg(acct, broker, sym,
                                      str(t.get("date"))[:10], -q))
    # An out-leg with no name of its own takes the names its account's
    # rows give the same symbol (IB: the trades' instrument names).
    for o in ev.outs:
        o.names = set(names_of.get((o.account, o.broker, o.symbol), ()))
    ev.outs.sort(key=lambda o: (o.date, o.account, o.broker, o.symbol,
                                o.quantity))
    return ev


# ------------------------------------------------------------ inference

def _close(a: str, b: str, days: int) -> bool:
    from taxjson.lib.cross_listings import _d
    da, db = _d(a), _d(b)
    return bool(da and db and abs((da - db).days) <= days)


def _same_qty(a: float, b: float) -> bool:
    return abs(a - b) <= max(_EPS, 1e-6 * max(abs(a), abs(b)))


def _names_ok(a: Set[Tuple[str, ...]], b: Iterable[Tuple[str, ...]],
              shown: Dict[Tuple[str, ...], str]) -> bool:
    from taxjson.lib.cross_listings import _names_verdict
    return _names_verdict(set(a), set(b), shown) == ""


def resolve(scan: Scan, ev: Evidence, *, account: str, broker: str,
            mapped: Callable[[str], bool] = lambda _s: False,
            distinct: Iterable[Iterable[str]] = (),
            days: Optional[int] = None) -> Dict[str, Any]:
    """{"corrected": {listing: {...}}, "kept": {listing: {...}}} for one
    (account, broker) group (module docstring): `scan` its exports read
    before the parse, `ev` the rest of the project's books.
    `mapped(symbol)`: a ticker.map line names it (any keyword);
    `distinct`: the map's DISTINCT pairs."""
    from taxjson.lib.cross_listings import PAIR_DAYS
    days = PAIR_DAYS if days is None else days
    cands = scan.cands
    apart = {frozenset(str(x).upper() for x in p) for p in distinct}
    shown = dict(ev.shown)
    for c in cands.values():
        for k, v in c.shown.items():
            shown.setdefault(k, v)
    # The account's own rows of each listing: their currencies (one
    # native pool), this group's and its other brokers'.
    own: Dict[str, Set[str]] = {k: set(v) for k, v in
                                scan.currencies.items()}
    for sym, rows in ev.seen.items():
        for a, _b, cur, _nm in rows:
            if a == account and cur:
                own.setdefault(sym, set()).add(cur)
    # Where a listing is known: the other books, and this group's own
    # rows that name their listing (a .TO symbol, never a candidate).
    known: Dict[str, List[Tuple[str, str, frozenset]]] = {
        sym: [(a, b, nm) for a, b, _c, nm in rows]
        for sym, rows in ev.seen.items()}
    for sym, nm in scan.names.items():
        if sym not in cands:
            known.setdefault(sym, []).append((account, broker,
                                              frozenset(nm)))
    corrected: Dict[str, Dict[str, Any]] = {}
    kept: Dict[str, Dict[str, Any]] = {}
    # 1. transfer pairing: each arrival's name-agreeing partners.
    verdicts: Dict[str, List[Tuple[str, OutLeg]]] = {}
    claims: Dict[int, Set[Tuple[str, int]]] = {}
    for lst in sorted(cands):
        c = cands[lst]
        alt = other_listing(lst)
        for n, (d, q) in enumerate(sorted(c.arrivals)):
            legs = [o for o in ev.outs
                    if _same_qty(o.quantity, q) and _close(o.date, d, days)
                    and o.names and _names_ok(c.names, o.names, shown)]
            for o in legs:
                claims.setdefault(id(o), set()).add((lst, n))
            if len(legs) != 1:
                continue        # none, or ambiguous: not evidence
            o = legs[0]
            if o.symbol == lst:
                verdicts.setdefault(lst, []).append(("keep", o))
            elif o.symbol == alt:
                verdicts.setdefault(lst, []).append(("other", o))
            elif (_root(o.symbol) != _root(lst)
                  and _sfx(o.symbol) == _sfx(lst)):
                verdicts.setdefault(lst, []).append(("other", o))
            elif _root(o.symbol) != _root(lst) and _sfx(o.symbol) == _sfx(alt):
                verdicts.setdefault(lst, []).append(("keep", o))
    for lst in sorted(cands):
        c = cands[lst]
        alt = other_listing(lst)
        vs = [(k, o) for k, o in verdicts.get(lst, ())
              if len(claims.get(id(o), ())) == 1]
        how = evidence = ""
        pair: Dict[str, Any] = {}
        if vs and all(k == "other" for k, _o in vs):
            o = sorted((o for _k, o in vs), key=lambda x: (x.date, x.symbol))[0]
            if (o.symbol != alt and frozenset((_root(o.symbol), _root(lst)))
                    in ev.renames):
                kept[lst] = {"symbol": alt, "reason": (
                    f"{o.symbol} and {lst} are joined by a rename row in "
                    f"the books (a ticker change, not two listings)")}
                continue
            how = "transfer"
            evidence = (f"its transfer-in is the {_broker_name(o.broker)} "
                        f"transfer out of {o.quantity:g} {o.symbol} on "
                        f"{o.date} (account {o.account}) under the same "
                        f"name")
            pair = {"symbol": o.symbol, "date": o.date,
                    "account": o.account, "broker": o.broker}
        elif vs:
            continue            # the transfer confirms the row's listing
        elif c.currency == "USD" and c.arrivals and alt in known:
            names = set().union(*(nm for _a, _b, nm in known[alt]))
            if not names or not _names_ok(c.names, names, shown):
                continue
            a, b, _nm = sorted(known[alt], key=lambda k: k[:2])[0]
            how = "listing"
            evidence = (f"{alt} is in the project's books "
                        f"({_broker_name(b)}, account {a}) under the "
                        f"same name")
        else:
            continue
        # Guards: the map, a real listing, one native-currency pool.
        if mapped(lst) or frozenset((lst, alt)) in apart:
            kept[lst] = {"symbol": alt, "reason": "a ticker.map rule names "
                         "it (the map wins)"}
            continue
        if lst in ev.named:
            kept[lst] = {"symbol": alt, "reason": (
                f"{lst} is a real listing: another broker's export trades "
                f"it")}
            continue
        mixed = sorted(own.get(alt, set()) - {c.currency})
        if mixed:
            kept[lst] = {"symbol": alt, "line": f"TOBASE {lst} {alt}",
                         "reason": (
                             f"{evidence}, but this account also holds "
                             f"{alt} in {', '.join(mixed)} — one native-"
                             f"currency pool cannot hold both"),
                         "how": how}
            continue
        corrected[lst] = {"symbol": alt, "how": how, "evidence": evidence,
                          "broker": broker, "account": account,
                          "currency": c.currency,
                          **({"pair": pair} if pair else {})}
    return {"corrected": corrected, "kept": kept}


def state_text(account: str, broker: str, result: Dict[str, Any]) -> str:
    doc = {"format": FORMAT, "account": account, "broker": broker,
           "corrected": result.get("corrected") or {},
           "kept": result.get("kept") or {}}
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ------------------------------------------------------------ messages

def why(listing: str, record: Dict[str, Any]) -> str:
    """'QZCC.US read as QZCC.TO: Questrade files the TSX listing on a USD
    row'."""
    to = str(record.get("symbol") or "")
    cur = str(record.get("currency") or ("USD" if listing.endswith(".US")
                                         else "CAD"))
    return (f"{listing} read as {to}: "
            f"{_broker_name(str(record.get('broker') or ''))} files the "
            f"{_MARKET.get(_sfx(to), _sfx(to))} listing on a {cur} row")


def all_corrections(cache: Path) -> Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]]:
    """{(account, corrected listing): (listing as filed, record)} from
    every state file in work/."""
    out: Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]] = {}
    for p in sorted(Path(cache).glob(f"*{SUFFIX}")):
        st = read_state(p)
        acct = st.get("account") or ""
        for frm, r in sorted(st["corrected"].items()):
            r = dict(r)
            r.setdefault("broker", st.get("broker") or "")
            out[(acct, str(r["symbol"]).upper())] = (frm, r)
    return out


def corrections_note(account: str, cache: Path,
                     skip: Iterable[str] = ()) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the one Warning per account naming the
    symbols read as another listing than the row currency's (those in
    `skip` — named by the account's join warning — left out); None when
    there are none."""
    skip = {str(s).upper() for s in skip}
    items: List[str] = []
    details: List[str] = []
    for (acct, to), (frm, r) in sorted(all_corrections(cache).items()):
        if acct != account or to in skip:
            continue
        items.append(why(frm, r))
        details.append(f"- {frm}: {r.get('evidence')}; if {frm} is right, "
                       f"add `DISTINCT {frm} {to}` to ticker.map")
    if not items:
        return None
    return (f"{account}: " + "; ".join(items),
            ["Every row of the symbol in the account (transfer-in, trades, "
             "dividends) is booked under the listing the evidence names — "
             "this changes your books."] + details)


def suggestions(cache: Path) -> List[Tuple[str, str, bool]]:
    """[(ticker.map line, reason, conditional)]: the explicit lines equal
    to each correction (a GLOBAL line, plus the TOBASE line of its
    transfer journal), and the TOBASE line of each listing kept because
    one pool cannot hold both currencies — conditional: right only if
    the two are one security."""
    out: List[Tuple[str, str, bool]] = []
    for p in sorted(Path(cache).glob(f"*{SUFFIX}")):
        st = read_state(p)
        acct = st.get("account") or p.name[:-len(SUFFIX)]
        for frm, r in sorted(st["corrected"].items()):
            r = dict(r)
            r.setdefault("broker", st.get("broker") or "")
            to = str(r["symbol"]).upper()
            out.append((f"GLOBAL {frm} {to}",
                        f"{acct}: {why(frm, r)} ({r.get('evidence')}); "
                        f"`taxjson run` already books it so — the line "
                        f"only makes it explicit", False))
            pr = r.get("pair") or {}
            o = str(pr.get("symbol") or "").upper()
            j = r.get("join")
            if o and isinstance(j, list) and len(j) == 2:
                a, b = (str(x).upper() for x in j)
                out.append((f"TOBASE {a} {b}",
                            f"{acct}: the transfer journal {o} -> {to} "
                            f"({pr.get('date')}) joins the two listings; "
                            f"with the GLOBAL line above in the map, "
                            f"write this one too", False))
        for frm, r in sorted(st["kept"].items()):
            line = str(r.get("line") or "")
            if line:
                out.append((line, f"{acct}: {frm} reads as "
                            f"{r.get('symbol')} — {r.get('reason')}; add "
                            f"it only if they are one security", True))
    return out
