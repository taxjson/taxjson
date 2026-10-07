"""`taxjson journals`: every broker journal between two listings of one
security, per account, with how it was found and what the books made
of it (read-only, no network; a completed `taxjson run` is needed).

A journal moves a position from one listing to another: an out-leg of
FROM and an in-leg of TO, the same quantity. Where they come from:

  - work/cross_listings.state (lib/cross_listings): the pairs the run
    joined (`joined`), suggested (`suggested`, with its reason) and left
    alone (`refused`: a ticker.map DISTINCT line, a ticker.map line
    that names a listing, or the legs naming two companies). A record
    says how it was found: `kind` JOURNAL (a Questrade BRW currency
    journal), `journal` (the broker whose journal wording both legs
    carry: Questrade's BRW, RBC's TFR ... J / J~, IB's InterDepot),
    `ref` (the broker's pair id both legs share), `via: journal` (a
    move between two listings a journal joined), and — written by other
    stages — `source` (a `.tt` line, a ticker.map line ...), shown as
    SOURCES names it or as it is;
  - the parsed exports in work/ (books and transfer sidecars): a broker
    journal the legs' own pair id marks (Questrade's `journal_pair`,
    RBC's J~ reference: missing_history.journal_leg_key) that the state
    does not list;
  - the map the books were merged with (work/ticker.map.effective, else
    ticker.map): which line pools the two listings.

Each journal is in one state:

  joined     one security in the books: the TOBASE / JOURNAL (or
             GLOBAL / RENAME) line that pools them, where it is, and how
             to undo it;
  suggested  not joined: the reason, and the lines that settle it (a
             `.tt` line `JOURNAL <date> FROM TO QTY` in the account's
             inputs, or a ticker.map line);
  refused    kept apart: the reason, and how to undo it (remove the
             DISTINCT line, or the ticker.map line to add when the two
             listings are one security after all). A journal the user's
             ticker.map keeps apart (a DISTINCT line, a line naming a
             listing) is a decision made: listed, never pending; one
             refused because its legs name two companies is pending.

The JSON form (`--json`, FORMAT) is a stable schema: docs/settings.md
describes it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

FORMAT = "taxjson-journals/1"
STATES = ("joined", "suggested", "refused")

# How a journal was found: the id in the JSON, the words in the text.
# A `source` another stage writes that is not here is shown as it is.
SOURCES: Dict[str, str] = {
    "questrade-brw": "Questrade journal (BRW)",
    "rbc-journal-ref": "RBC journal transfer (J~ ref)",
    "rbc-journal": "RBC journal transfer (J)",
    "ib-interdepot": "IB InterDepot",
    "cross-broker": "cross-broker move with a listing change",
    "transfer": "transfer with a listing change",
    "tt": ".tt line",
    "map": "ticker.map line",
    "ticker.map": "ticker.map line",
    "ib-conid": "IB contract id",
}

_RULE_KEYWORDS = ("GLOBAL", "TOBASE", "JOURNAL", "RENAME")
_EPS = 1e-6


class JournalsError(ValueError):
    """The project has no completed run to read."""


# ------------------------------------------------------------ the map

class _Map:
    """The map the books were merged with: its rename rules (one hop,
    FROM -> TO), each rename line's (file:line, text), and the DISTINCT
    lines."""

    def __init__(self, root: Path):
        from taxjson.lib import cross_listings as XL
        cache = root / "work"
        eff = cache / XL.EFFECTIVE_MAP
        self.path: Optional[Path] = eff if eff.is_file() else (
            root / "ticker.map" if (root / "ticker.map").is_file() else None)
        self.raw: Dict[str, str] = {}
        self.lines: Dict[str, Tuple[str, str]] = {}
        self.distinct: Dict[frozenset, Tuple[str, str]] = {}
        if self.path is None:
            return
        from taxjson.lib.cli_diag import read_text_utf8
        try:
            text = read_text_utf8(self.path)
        except (OSError, ValueError):
            return
        # The effective map is the project's ticker.map, then the run's
        # own lines (under EFFECTIVE_HEAD): a line above it is the
        # user's ticker.map line, with the same number.
        own = self.path.name == XL.EFFECTIVE_MAP
        in_head = False
        for n, raw in enumerate(text.splitlines(), 1):
            # Every block the run appends opens with a "# taxjson run:"
            # comment (XL.EFFECTIVE_HEAD and the like).
            if own and (raw.strip() == XL.EFFECTIVE_HEAD
                        or raw.startswith("# taxjson run:")):
                in_head = True
                continue
            body = raw.split("#", 1)[0].split()
            if len(body) < 3:
                continue
            kw = body[0].upper()
            where = (f"work/{XL.EFFECTIVE_MAP}:{n}" if in_head
                     else f"ticker.map:{n}")
            line = " ".join([kw] + [b.upper() for b in body[1:]])
            a, b = body[1].upper(), body[2].upper()
            if kw == "DISTINCT":
                self.distinct.setdefault(frozenset((a, b)), (where, line))
                continue
            if kw not in _RULE_KEYWORDS:
                continue
            # A dated RENAME line is an event, not a pooling rule.
            if kw == "RENAME" and len(body) > 3:
                continue
            if a not in self.raw:
                self.raw[a] = b
                self.lines[a] = (where, line)

    def chain(self, sym: str) -> List[str]:
        out = [sym]
        while out[-1] in self.raw and self.raw[out[-1]] not in out:
            out.append(self.raw[out[-1]])
        return out

    def end(self, sym: str) -> str:
        return self.chain(sym)[-1]

    def joining(self, a: str, b: str) -> List[Tuple[str, str]]:
        """The (where, line) of the lines that pool `a` and `b` onto one
        symbol; [] when the map does not."""
        if a == b or self.end(a) != self.end(b):
            return []
        out = []
        for s in self.chain(a)[:-1] + self.chain(b)[:-1]:
            if s in self.lines and self.lines[s] not in out:
                out.append(self.lines[s])
        return out

    def naming(self, a: str, b: str) -> List[Tuple[str, str]]:
        """The rename lines whose FROM is `a` or `b`."""
        return [self.lines[s] for s in (a, b) if s in self.lines]


# ---------------------------------------------------------- the records

def _f(x: Any) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _qty(q: float) -> str:
    return f"{q:.8f}".rstrip("0").rstrip(".") if q % 1 else f"{q:.0f}"


# A record's `source` that only says "a broker's rows" (the broker is
# then read from how the pair was found).
_BROKER_SOURCES = ("broker", "")


def source_of(rec: Dict[str, Any]) -> str:
    """The source id of a state record (SOURCES): its own `source` when
    it names one ("tt", "map" ...: passed through as it is), else read
    from how the pair was found."""
    src = str(rec.get("source") or "").strip()
    if src not in _BROKER_SOURCES:
        return src
    o, i = rec.get("out") or {}, rec.get("in") or {}
    journal = str(rec.get("journal") or "")
    ref = str(rec.get("ref") or "")
    broker = str(o.get("broker") or rec.get("broker") or "")
    if journal == "tt" or broker.startswith("tt"):
        return "tt"                     # a .tt JOURNAL line's legs
    if journal == "questrade" or (broker == "questrade" and (
            str(rec.get("kind") or "") == "JOURNAL" or ref == "pair")):
        return "questrade-brw"
    if journal == "rbc_direct" or (broker == "rbc_direct" and ref == "ref"):
        return "rbc-journal-ref" if ref == "ref" else "rbc-journal"
    if journal == "ib":
        return "ib-interdepot"
    if (o.get("account") != i.get("account")
            or o.get("broker") != i.get("broker")):
        return "cross-broker"
    return "transfer"


def source_text(src: str) -> str:
    return SOURCES.get(src, src)


def _leg_key(acct: str, sym: str, date: str, in_sym: str,
             q: float) -> Tuple[str, str, str, str, float]:
    return (acct, sym.upper(), str(date)[:10], in_sym.upper(),
            round(abs(q), 6))


def _from_record(rec: Dict[str, Any], state: str) -> Dict[str, Any]:
    o, i = rec.get("out") or {}, rec.get("in") or {}
    frm = str(o.get("symbol") or rec.get("from") or "").upper()
    to = str(i.get("symbol") or rec.get("to") or "").upper()
    qty = abs(_f(o.get("quantity") if o else rec.get("quantity")))
    acct = str(o.get("account") or rec.get("account") or "")
    src = source_of(rec)
    return {
        "account": acct,
        "broker": str(o.get("broker") or rec.get("broker") or ""),
        "date": str(o.get("date") or rec.get("date") or "")[:10],
        "from": frm, "to": to, "quantity": qty,
        "in": {"account": str(i.get("account") or acct),
               "broker": str(i.get("broker") or o.get("broker")
                             or rec.get("broker") or ""),
               "date": str(i.get("date") or o.get("date")
                           or rec.get("date") or "")[:10]},
        "source": src, "found_by": source_text(src),
        "state": state,
        # The map line's direction (lib/cross_listings.tobase_direction)
        # and keyword.
        "_map": (str(rec.get("from") or frm).upper(),
                 str(rec.get("to") or to).upper(),
                 str(rec.get("kind") or "TOBASE").upper()),
        "_refused": str(rec.get("refused") or ""),
        "reason": str(rec.get("reason") or "") or None,
        "names": [str(n) for n in (rec.get("names") or [])][:2],
        # Where a declared journal was written (a .tt line's place).
        "where": str(rec.get("where") or "") or None,
    }


def _broker_journals(cache: Path, accounts: Iterable[str],
                     base_currency: Optional[str] = None
                     ) -> List[Dict[str, Any]]:
    """The broker journals the parsed exports mark (a Questrade
    journal_pair, RBC's J~ reference: missing_history.journal_leg_key):
    one out-leg and one in-leg of the same quantity in one account under
    one pair id, on two symbols."""
    from taxjson.lib import cross_listings as XL
    legs, _names, _shown = XL.gather(cache, accounts)
    groups: Dict[Tuple[str, str, str], List[Any]] = {}
    for g in legs:
        if g.ref:
            groups.setdefault((g.account, g.broker, g.ref), []).append(g)
    out = []
    for (acct, broker, ref), gl in sorted(groups.items()):
        # Overlapping exports repeat a row: one leg per (symbol, qty).
        uniq = {(g.symbol, round(g.quantity, 6)): g for g in gl}
        o = [g for g in uniq.values() if g.quantity < 0]
        i = [g for g in uniq.values() if g.quantity > 0]
        if len(o) != 1 or len(i) != 1 or o[0].symbol == i[0].symbol \
                or abs(abs(o[0].quantity) - i[0].quantity) > _EPS:
            continue
        frm, to = XL.tobase_direction(o[0].symbol, i[0].symbol,
                                      base_currency)
        rec = {"from": frm, "to": to,
               "out": {"account": acct, "broker": broker,
                       "symbol": o[0].symbol, "date": o[0].date,
                       "quantity": -o[0].quantity},
               "in": {"account": acct, "broker": broker,
                      "symbol": i[0].symbol, "date": i[0].date,
                      "quantity": i[0].quantity},
               "ref": ref.split("|", 1)[0],
               "journal": o[0].journal or broker,
               "names": [o[0].raw_name, i[0].raw_name]}
        out.append(rec)
    return out


def _settle_lines(j: Dict[str, Any]) -> List[str]:
    frm, to, kw = j["_map"]
    return [f"JOURNAL {j['date']} {j['from']} {j['to']} "
            f"{_qty(j['quantity'])}",
            f"{'JOURNAL' if kw == 'JOURNAL' else 'TOBASE'} {frm} {to}"]


def _classify(j: Dict[str, Any], m: _Map) -> None:
    """Fill in the state's fields: line / line_at (joined), settle
    (suggested), undo (joined, refused), and whether it is pending: a
    suggested journal, or one refused with no line of the user's
    behind it (the legs name two companies). A journal the user's map
    decides (a DISTINCT line, a line naming a listing) is a decision
    made, never pending (`decided_by`: "ticker.map")."""
    a, b = j["from"], j["to"]
    j.update(line=None, line_at=None, settle=[], undo=None,
             pending=False, decided_by=None)
    joins = m.joining(a, b)
    if j["state"] == "refused" and j["_refused"] == "map" and joins:
        j["state"], j["reason"] = "joined", None
    if j["state"] == "suggested" and joins:
        # The map pools them (a line added since the run, or a line the
        # run's join stands for).
        j["state"], j["reason"] = "joined", None
    if j["state"] == "joined":
        if joins:
            j["line_at"], j["line"] = joins[0][0], "; ".join(
                ln for _w, ln in joins)
            if len(joins) > 1:
                j["line_at"] = ", ".join(w for w, _l in joins)
        elif a == b:
            j["line"] = "(one symbol in the books)"
        if j["line_at"] and not j["line_at"].startswith("ticker.map:"):
            j["undo"] = (f"add `DISTINCT {a} {b}` to ticker.map if they "
                         f"are not one security")
        elif j["line_at"]:
            j["undo"] = (f"remove the line at {j['line_at']} if they are "
                         f"not one security")
        return
    if j["state"] == "suggested":
        j["pending"] = True
        j["settle"] = _settle_lines(j)
        if not j["reason"]:
            j["reason"] = ("the run did not pair the legs (a symbol that "
                           "names two securities, or a run older than "
                           "this command: re-run `taxjson run`)")
        return
    # refused
    why = j["_refused"]
    pair = frozenset((a, b))
    if why == "distinct" or pair in m.distinct:
        where, line = m.distinct.get(pair, ("ticker.map", f"DISTINCT {a} {b}"))
        j["reason"] = j["reason"] or f"ticker.map keeps them apart ({line})"
        j["decided_by"] = "ticker.map"
        j["undo"] = (f"remove `{line}` ({where}) to let `taxjson run` "
                     f"decide")
    elif why == "map":
        named = m.naming(a, b)
        if named:
            j["reason"] = (f"ticker.map names a listing otherwise: "
                           + "; ".join(f"{ln} ({w})" for w, ln in named))
        j["decided_by"] = "ticker.map"
        j["undo"] = ("edit that line, or add `" + _settle_lines(j)[1]
                     + "` to ticker.map if they are one security")
    else:
        # The legs name two companies: no line of the user's says so.
        j["pending"] = True
        j["undo"] = ("add `" + _settle_lines(j)[1] + "` to ticker.map only "
                     "if they are one security, or `DISTINCT " + a + " " + b
                     + "` if they are not")


def report(root: Path, cfg: Dict[str, Any], account: Optional[str] = None,
           year: Optional[int] = None) -> Dict[str, Any]:
    """The journals of the project's last run (module docstring), as the
    `--json` document. Raises JournalsError without a completed run."""
    from taxjson.lib import cross_listings as XL
    root = Path(root)
    cache = root / "work"
    state_path = cache / XL.STATE
    if not cache.is_dir():
        raise JournalsError("no work/ folder")
    if not state_path.is_file():
        raise JournalsError(f"no work/{XL.STATE}")
    st = XL.read_state(state_path)
    accts = [n for n, c in ((cfg.get("accounts") or {}).items())
             if not (c or {}).get("crypto")]
    found: List[Dict[str, Any]] = []
    seen = set()
    for state in STATES:
        for rec in st.get(state) or []:
            j = _from_record(rec, state)
            k = _leg_key(j["account"], j["from"], j["date"], j["to"],
                         j["quantity"])
            if k in seen:
                continue
            seen.add(k)
            found.append(j)
    base = str((cfg.get("settings") or {}).get("base_currency")
               or "").upper() or None
    for rec in _broker_journals(cache, accts, base):
        j = _from_record(rec, "suggested")
        k = _leg_key(j["account"], j["from"], j["date"], j["to"],
                     j["quantity"])
        if k in seen:
            continue
        seen.add(k)
        j["reason"] = None
        found.append(j)
    m = _Map(root)
    out = []
    for j in found:
        if account and account not in (j["account"], j["in"]["account"]):
            continue
        if year and not str(j["date"]).startswith(f"{int(year):04d}-"):
            continue
        _classify(j, m)
        j.pop("_map")
        j.pop("_refused")
        out.append(j)
    out.sort(key=lambda j: (j["account"], j["date"], j["from"], j["to"]))
    counts = {s: sum(1 for j in out if j["state"] == s) for s in STATES}
    # The refused journals the user's own ticker.map decided (not
    # pending).
    counts["decided"] = sum(1 for j in out if j["decided_by"])
    return {"format": FORMAT, "account": account,
            "year": int(year) if year else None,
            "map": (None if m.path is None else
                    ("work/" + m.path.name if m.path.parent == cache
                     else m.path.name)),
            "journals": out, "counts": counts,
            "pending": sum(1 for j in out if j["pending"])}


# ------------------------------------------------------------ the text

_BROKERS = {"rbc_direct": "RBC", "questrade": "Questrade", "ib": "IB",
            "webull": "Webull"}


def _broker(b: str) -> str:
    return _BROKERS.get(b, b)


def _map_cell(j: Dict[str, Any]) -> str:
    """The MAP LINE cell: the line that pools the two listings and where
    it is — `ticker.map:N` for the user's line, `run` for the run's own
    join (work/ticker.map.effective; --json has its line number)."""
    if not j["line"]:
        return "—"
    at = j["line_at"] or ""
    places = sorted({("run" if w.strip().startswith("work/") else w.strip())
                     for w in at.split(",") if w.strip()})
    return f"{j['line']} ({', '.join(places)})" if places else j["line"]


def render(doc: Dict[str, Any], width_: Optional[int] = None,
           pending: bool = False) -> List[str]:
    """The journals in the house layout (docs/output-style.md): a table
    per account, then the journals not joined with the lines that settle
    them (never wrapped); the last line counts them."""
    from taxjson.lib.out import Doc
    js = [j for j in doc["journals"] if not pending or j["pending"]]
    scope = []
    if doc.get("year"):
        scope.append(f"tax year {doc['year']}")
    else:
        scope.append("every year")
    if doc.get("account"):
        scope.append(f"account {doc['account']}")
    what = "pending journals" if pending else "journals"
    d = Doc(f"JOURNALS — {', '.join(scope)}: {len(js)} {what} between two "
            f"listings", width_=width_)
    if not js:
        d.blank()
        d.para("No journal between two listings is pending." if pending
               else "No journal between two listings in the books.")
    by_acct: Dict[str, List[Dict[str, Any]]] = {}
    for j in js:
        by_acct.setdefault(j["account"], []).append(j)
    for acct in sorted(by_acct):
        rows = by_acct[acct]
        d.section(f"ACCOUNT {acct} ({len(rows)})")
        body = []
        for j in rows:
            to = j["to"]
            if j["in"]["account"] != j["account"]:
                to += f" ({j['in']['account']})"
            body.append([j["date"], f"{j['from']} → {to}",
                         _qty(j["quantity"]),
                         _broker(j["broker"]) + (
                             f" → {_broker(j['in']['broker'])}"
                             if j["in"]["broker"] != j["broker"] else ""),
                         j["found_by"], j["state"],
                         _map_cell(j)])
        d.table(["DATE", "FROM → TO", "QUANTITY", "BROKER", "FOUND BY",
                 "STATE", "MAP LINE"], body, aligns="<<><<<<",
                drop=(3,), key=(0, 1), indent="  ")
        for j in rows:
            if j["state"] == "joined":
                continue
            d.blank()
            how = (f"{j['state']} by your ticker.map" if j["decided_by"]
                   else j["state"])
            d.item(f"{j['date']} {j['from']} → {j['to']} {how}: "
                   f"{j['reason']}", "  ")
            if j["state"] == "suggested":
                d.para(f"If they are one security, add the .tt line to a "
                       f".tt file in inputs/{j['account']}/, or the "
                       f"ticker.map line:", "    ")
                for ln in j["settle"]:
                    d.line(f"      {ln}")
            elif j["undo"]:
                d.para(f"{'To change it' if j['decided_by'] else 'Undo'}: "
                       f"{j['undo']}.", "    ")
    c = doc["counts"]
    d.blank()
    tail = (f"{c['joined']} joined, {c['suggested']} suggested, "
            f"{c['refused']} refused"
            + (f" ({c['decided']} by your ticker.map)" if c.get("decided")
               else "") + f"; {doc['pending']} pending.")
    if doc["pending"]:
        tail += (" Settle each with the line it names, then re-run "
                 "`taxjson run`.")
    d.para(tail)
    return d.lines()
