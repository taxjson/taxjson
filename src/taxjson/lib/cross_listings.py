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

Everything else stays a suggestion (`taxjson ticker-map --suggest`). The
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
one security. In a Canadian project (tax-logic CA-XLIST-02) the two
lines are joined as a ticker.map `JOURNAL FROM TO` line would — one
security for the cost and the loss rules, netted in the holdings view —
on the parser's pairing alone (the legs share one description). The
user's map still wins, and a listing joined to two others is only
suggested. In a US project those legs are ordinary transfer legs.
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
    pair: str = ""                  # a currency journal's id (parser)
    currency: str = ""              # the row's currency


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

    def record(self) -> Dict[str, Any]:
        return {"from": self.frm, "to": self.to,
                **({"kind": self.kind} if self.kind != "TOBASE" else {}),
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
    """(transfer legs, symbol -> normalised names (symbol_codes.
    exact_name), normalised name -> the name as written) from every
    account's parsed exports in work/."""
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
                                str(t.get("date"))[:10], q,
                                pair=str(t.get("journal_pair") or ""),
                                currency=str(t.get("currency") or "")
                                .upper()))
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


def _names_verdict(nx: Set[Tuple[str, ...]], ny: Set[Tuple[str, ...]],
                   shown: Dict[Tuple[str, ...], str]) -> str:
    """"" when the two listings' names agree, else why not. Exact
    equality only (lib/symbol_codes.exact_name): some name of X must be
    some name of Y word for word — the corporate form and every share
    designator included — and no other name of either listing may state
    other designators or another corporate form than that shared name
    (symbol_codes.exact_marks). Anything less stays a suggestion."""
    from taxjson.lib.symbol_codes import exact_marks
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
            currency_journals: bool = False) -> Dict[str, List[Pair]]:
    """{"joined": [...], "suggested": [...]}: the cross-listing journals
    the legs show (module docstring). `currency_journals`: join the
    parser-paired currency journals as JOURNAL lines (Canada,
    CA-XLIST-02; the caller gates the country)."""
    named = {s.upper() for s in map_named}
    apart = {frozenset(x.upper() for x in pair) for pair in map_distinct}
    joined: List[Pair] = []
    suggested: List[Pair] = []
    # 0. A currency journal the parser paired (one account, one day, one
    #    description): its two lines are one security.
    if currency_journals:
        groups: Dict[Tuple[str, str, str], List[Leg]] = {}
        for g in legs:
            if g.pair:
                groups.setdefault((g.account, g.broker, g.pair),
                                  []).append(g)
        for _k, gl in sorted(groups.items()):
            o = [g for g in gl if g.quantity < 0]
            i = [g for g in gl if g.quantity > 0]
            if (len(o) != 1 or len(i) != 1 or o[0].symbol == i[0].symbol
                    or not _same_qty(o[0], i[0])):
                continue
            o[0].used = i[0].used = True
            if (o[0].symbol in named or i[0].symbol in named
                    or frozenset((o[0].symbol, i[0].symbol)) in apart):
                continue                # the user's map decides
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
                               names=(shown.get(min(nx), "") if nx else "",
                                      shown.get(min(ny), "") if ny
                                      else "")))
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
                p.reason = _names_verdict(nx, ny, shown)
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
    """The TOBASE (or JOURNAL) lines of the joins, one per pair of
    listings, each with its evidence as a comment."""
    seen: Set[Tuple[str, str]] = set()
    out: List[str] = []
    for p in sorted(joined, key=lambda p: (p.frm, p.to, p.out.date)):
        if (p.frm, p.to) in seen:
            continue
        seen.add((p.frm, p.to))
        what = "currency journal" if p.kind == "JOURNAL" else "transfer"
        out.append(f"{p.kind} {p.frm} {p.to}  # {what} {p.out.symbol} -> "
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


def joined_note(account: str, joined: Iterable[Pair]
                ) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the one Warning per account naming the
    pairs joined through its legs, each with the ticker.map line that
    undoes it; None when there are none."""
    items: List[str] = []
    undo: List[str] = []
    seen = set()
    kinds: Set[str] = set()
    for p in sorted(joined, key=lambda p: (p.out.date, p.frm)):
        if account not in (p.out.account, p.into.account):
            continue
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
                        f"ticker.map JOURNAL line would (netted in the "
                        f"holdings view); if they are not one security, "
                        f"add `DISTINCT {p.out.symbol} {p.into.symbol}` "
                        f"to ticker.map")
            continue
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
             "loss rules), as a ticker.map "
             + " / ".join(sorted(kinds)) + " line would — this "
             "changes your books."] + undo)
