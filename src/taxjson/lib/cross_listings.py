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
* the security names agree: some name the exports give X and some name
  they give Y normalise to the same company and the same share
  (lib/symbol_codes.name_relation "same"), and no two of their names
  disagree on the share designators (class letter, voting, ADR,
  preferred, unit ...) — an ADR never joins its ordinary shares, class A
  never joins class B;
* no ticker.map rule renames or deletes X or Y (TOBASE / JOURNAL /
  GLOBAL / RENAME / DELETE, either side) and no DISTINCT line pairs the
  two: the user's map always wins, DISTINCT keeps them apart;
* neither symbol is joined to a third listing by another pair.

Everything else stays a suggestion (`taxjson ticker-map --suggest`). The
joins are written to work/cross_listings.state (JSON) and appended, as
TOBASE lines, to the effective map the merge stages read
(work/ticker.map.effective: the project's ticker.map plus those lines).
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


def gather(cache: Path, accounts: Iterable[str]
           ) -> Tuple[List[Leg], Dict[str, Set[Tuple[str, ...]]],
                      Dict[Tuple[str, ...], str]]:
    """(transfer legs, symbol -> normalised names, normalised name -> the
    name as written) from every account's parsed exports in work/."""
    from taxjson.lib.symbol_codes import (_CONTROL_RE, _plain_listing,
                                          _row_name, is_code, name_tokens)
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
                if not _plain_listing(sym) or is_code(sym):
                    continue
                toks = name_tokens(_row_name(t, broker))
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


def _close(a: Leg, b: Leg, days: int) -> bool:
    da, db = _d(a.date), _d(b.date)
    return bool(da and db and abs((da - db).days) <= days)


def _same_qty(a: Leg, b: Leg) -> bool:
    qa, qb = abs(a.quantity), abs(b.quantity)
    return abs(qa - qb) <= max(_EPS, 1e-6 * max(qa, qb))


def _names_verdict(nx: Set[Tuple[str, ...]], ny: Set[Tuple[str, ...]]
                   ) -> str:
    """"" when the two listings' names agree (some pair names the same
    share, none names another class of it), else why not."""
    from taxjson.lib.symbol_codes import name_relation
    if not nx or not ny:
        return "no security name for " + ("either listing" if not nx
                                          and not ny else "one listing")
    rel = {name_relation(a, b) for a in nx for b in ny}
    if "class" in rel:
        return "the names differ in the share class or kind"
    if "same" not in rel:
        return "the names do not match"
    return ""


def analyze(legs: List[Leg], names: Dict[str, Set[Tuple[str, ...]]],
            shown: Dict[Tuple[str, ...], str], *,
            map_named: Iterable[str] = (),
            map_distinct: Iterable[Iterable[str]] = (),
            base_currency: Optional[str] = None,
            days: int = PAIR_DAYS) -> Dict[str, List[Pair]]:
    """{"joined": [...], "suggested": [...]}: the cross-listing journals
    the legs show (module docstring)."""
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
            if len(ms) > 1 or len(back.get(m, ())) > 1:
                p.reason = "the legs pair with more than one other leg"
            else:
                p.reason = _names_verdict(nx, ny)
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


def state_text(result: Dict[str, List[Pair]]) -> str:
    doc = {"format": FORMAT,
           "joined": [p.record() for p in result["joined"]],
           "suggested": [p.record() for p in result["suggested"]]}
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def read_state(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """The state file's records ({"joined": [], "suggested": []} when
    missing or unreadable)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        doc = None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return {"joined": [], "suggested": []}
    return {k: [r for r in (doc.get(k) or []) if isinstance(r, dict)]
            for k in ("joined", "suggested")}


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


def joined_note(account: str, joined: Iterable[Pair]) -> Optional[str]:
    """One line per account: the pairs joined through its legs."""
    items = []
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
    if not items:
        return None
    return f"{account}: joined as one security: " + ", ".join(items)
