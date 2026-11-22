"""The cash-event book: what the FX-on-cash ledger v2 reads besides the
trades and income of the position books (tax-logic CA-FX-07 / US-FX-03).

A broker export says more about cash than its trades: currency
conversions, deposits and withdrawals, and the statement's own cash
balances. The parsers' position books leave all of it out (none of it
is a disposition of a security). This module gathers it into a separate
list of events, never rows of the books:

    FXCONV    a conversion: <from currency, amount> -> <to currency,
              amount>, at the base-currency amount actually paid or
              received when one side is the base currency
    CASHMOVE  cash in or out of an account: a deposit, a withdrawal, a
              transfer — signed (+ in, - out)
    CASHBAL   a statement's cash balance in one currency at the END of a
              day (trade-date basis, as the statement prints it)
    CASHOPEN  the opening balance of the ledger's year (a .tt line only)
    FLOW      a fee a cash event charged in a foreign currency

Sources (the parse's: taxjson-brokerage --cash-out writes what each file
holds to work/<account>_<broker>.cash, `cash_sidecar`, which `collect` reads;
each built by the broker's parse from the rows it read, the parser's
`cash_events` (or why not, its `cash_problem`), for the brokers the parser
registry marks `builds_cash`): IB Trades/Forex rows,
Deposits & Withdrawals and the Cash Report's Starting/Ending Cash; RBC's
cash rows (WIR/DEP/TFI ...); Questrade's conversions (an FXT row's two
legs), deposits, withdrawals, cash-only journals and stock-lending
income; Kraken's ledger (fiat funding, fiat-for-
fiat trades and its running balances); Coinbase fiat and stablecoin
moves and conversions; and `.tt` lines for whatever no export carries
(Webull, a bank account), written date first like the other dated lines:

    FXCONV <date> <from> <amount> <to> <amount> [value=<base>] [at=<book>]
    CASHMOVE <date> <cur> <signed amount> <how> [at=<book>]
        how: own | kept | spot | cost=<base> | proceeds=<base>
    CASHOPEN <date> <cur> <signed units> <base cost> [at=<book>]
    CASHBAL <date> <cur> <balance> [at=<book>]
    CASHBOOK <book> [complete]  (whose cash the file's rows move;
                        `complete`: the book's cash lines are all of its
                        conversions, deposits and withdrawals)

A CASHMOVE line that names a move a broker export carries (same account
folder, date, currency and amount) DECLARES that move — what it was —
instead of adding one.

Every event belongs to a BOOK: one broker account's cash, named
`<account label>/<broker>` (plus `:<first 4 of the hashed broker
account>`, or more when two share those 4, when a label holds several
accounts of one broker), or a book
a `.tt` line names with `at=<name>` (a bank account kept by hand).

A line is read (`parse_line`, `FORMS`), and a broker's conversions,
moves, balances and fees built (`conv`, `move`, `balance`, `flow`), by
lib/cash_lines, which the parsers call too.
"""
from __future__ import annotations


import json
import re
from datetime import date as _date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from taxjson.project.accounts import configured_accounts, typed_account
from taxjson.lib import project_layout as _PL
from taxjson.lib.cash_lines import CashLineError, KEYWORDS, parse_line
from taxjson.model import money as M

# Parser ids (lib/brokerages/detect) -> the short name a book and an
# `at=` token use.
BROKER_SHORT = {"ib": "ib", "rbc_direct": "rbc", "questrade": "questrade",
                "webull": "webull", "coinbase": "coinbase",
                "kraken": "kraken", "generic": "generic"}


# ------------------------------------------------------------ .tt lines

def read_project_lines(root: Path, accounts: Dict[str, Any]
                       ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every cash line of the TAXABLE accounts' .tt files (each with
    `label`, `file` and `where` "inputs/<acct>/<file>:<line>"), and the
    problems: a malformed line, a line in a sheltered account (its cash
    is outside s.39(1.1) / §988), a .tt file that cannot be read (its
    lines would be missing from the ledger)."""
    from taxjson.lib.tt_format import tt_lines
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.dated_events import _where, tt_files
    out: List[Dict[str, Any]] = []
    problems: List[str] = []
    # An entry that is no table is no account: its lines are not "not
    # taxable" (the config check names it).
    tables = configured_accounts(accounts)
    for acct in sorted(accounts or {}):
        typed = tables.get(acct)
        for tt in tt_files(_PL.inputs_dir(Path(root)) / acct):
            try:
                text = read_text_utf8(tt)
            except (OSError, ValueError) as e:
                # Said, not skipped: its CASHOPEN/CASHMOVE lines would
                # be missing and the ledger refuse for the wrong reason
                # (2026-10 audit #91).
                from taxjson.lib.brokerages.base import shown_name
                why = getattr(e, "strerror", None) or str(e)
                if why.startswith(f"{tt}: "):
                    why = why[len(f"{tt}: "):]
                problems.append(f"inputs/{acct}/{shown_name(tt)}: cannot "
                                f"be read ({why})")
                continue
            for n, raw in enumerate(tt_lines(text), 1):
                if not any(k in raw for k in KEYWORDS):
                    continue
                where = _where(acct, tt, n)
                try:
                    ev = parse_line(raw, where)
                except CashLineError as e:
                    problems.append(str(e))
                    continue
                if ev is None:
                    continue
                if typed is not None and not typed.taxable:
                    problems.append(
                        f"{where}: {ev['kind']} is a line of the FX-on-"
                        f"cash ledger; account {acct} is "
                        f"{typed.type or 'not taxable'} (its cash "
                        f"is not in it) — move the line to a taxable "
                        f"account's .tt: {ev['line']!r}")
                    continue
                from taxjson.lib.brokerages.base import shown_name
                ev.update(label=acct, file=shown_name(tt))
                out.append(ev)
    return out, problems


# ------------------------------------------------------------ events


def apply_declarations(events: List[Dict[str, Any]],
                       lines: List[Dict[str, Any]]) -> None:
    """A .tt CASHMOVE line that matches a broker's move (the account,
    the currency, the amount within a cent, its date the move's trade
    or settlement date, and its book when it names one) declares it:
    the move takes the line's `decl` (and `decl_where`), the line is
    marked `matched`. A line matching none is a move of its own. In
    place; the ledger (lib/fx_cash_v2.build) and `tjs cash` read
    the same declarations."""
    moves = [e for e in events if e["kind"] == "CASHMOVE"]
    for ln in [x for x in lines if x["kind"] == "CASHMOVE"]:
        cands = [m for m in moves
                 if m.get("label") == ln.get("label")
                 and m.get("decl") is None
                 and m["currency"] == ln["currency"]
                 and abs(m["amount"] - ln["amount"]) <= 0.01
                 and ln["date"] in (m["date"], m["settle"])
                 # The line's book, from its at= or its file's CASHBOOK
                 # line (the folder's only book otherwise): a CASHBOOK-
                 # scoped declaration matched another book's identical
                 # move (2026-10 audit #85).
                 and (not ln.get("book") or m.get("book") == ln.get("book"))]
        if cands:
            m = min(cands, key=lambda e: (e["date"], e.get("line_where", "")))
            m["decl"] = ln["decl"]
            m["decl_where"] = ln.get("where")
            ln["matched"] = True


UNDECLARED = "?"        # a CASHMOVE line's how, when nothing declared it


def at_token(e: Dict[str, Any], books: "Books") -> Optional[str]:
    """The `at=` a copy of the event's .tt line needs to stay in its
    book: None when a line without one, in a new .tt file of its
    account folder (no CASHBOOK line), reads into that book anyway (the
    folder's only book); else the book's name inside the folder (`alpha`
    for margin/alpha, `ib:3135` for a broker's book). A copied line of a
    book kept by hand, or of one of several broker books, lost its book
    and read into the folder's default one (backlog TJ-0889)."""
    b, label = e.get("book"), e.get("label")
    if not b or not label or not str(b).startswith(f"{label}/"):
        return None
    if books.for_tt(label, "") == b:
        return None
    return str(b)[len(str(label)) + 1:] or None


def tt_line(e: Dict[str, Any], base: Optional[str] = None,
            at: Optional[str] = None) -> Optional[str]:
    """A cash event as the .tt line that declares it (FORMS; `at`: the
    book's `at=` token, at_token, when a copy needs one to keep its
    book; `tjs cash` lists them): an FXCONV, a CASHMOVE (a broker's
    move nothing declared shows UNDECLARED where own / kept / spot /
    cost= / proceeds= goes, so the line is not copied as it is), a
    CASHOPEN; a FLOW (the fee a cash event charged) as a `#FEE` comment
    line, which a .tt file ignores (a trade's cash leg, `leg` "trade":
    `#TRADE`; a FLOW received — a stock-lending receipt, a rebate — as a
    `#CREDIT` one, never a fee: backlog TJ-0693). None for a CASHBAL (a statement
    fact, never written by hand), a PERIOD, a NOTE, a CASHBOOK.

    `base`: the project's base currency. A broker's move IN it is no
    foreign cash and the ledger needs no declaration of it: a
    `#CASHMOVE` comment line, never UNDECLARED — `?` on a Canadian-dollar
    deposit asked for a line ledger v2 ignores (backlog TJ-0461)."""
    k = e["kind"]
    tail = f" at={at}" if at else ""
    if k == "FXCONV":
        s = (f"FXCONV {e['date']} {e['from_ccy']} {M.shown(e['from_amt'], 2):,.2f} "
             f"{e['to_ccy']} {M.shown(e['to_amt'], 2):,.2f}")
        if e.get("value") is not None:
            s += f" value={M.shown(e['value'], 2):,.2f}"
        return s + tail
    if k == "CASHMOVE":
        d = e.get("decl")
        if not d and base and str(e["currency"]).upper() == base.upper():
            return (f"#CASHMOVE {e['date']} {e['currency']} "
                    f"{M.shown(e['amount'], 2):+,.2f}")
        if not d:
            how = UNDECLARED
        elif d.get("value") is not None:
            how = f"{d['how']}={M.shown(d['value'], 2):,.2f}"
        else:
            how = str(d["how"])
        return (f"CASHMOVE {e['date']} {e['currency']} "
                f"{M.shown(e['amount'], 2):+,.2f} {how}{tail}")
    if k == "CASHOPEN":
        return (f"CASHOPEN {e['date']} {e['currency']} {M.shown(e['units'], 2):,.2f} "
                f"{M.shown(e['cost'], 2):,.2f}{tail}")
    if k == "FLOW":
        # A trade's cash leg (a Coinbase Convert's stablecoin side,
        # backlog TJ-0916) is no fee; a FLOW received (a stock-lending
        # receipt, a rebate) is a credit, never a fee (backlog TJ-0693).
        if e.get("leg") == "trade":
            kw = "#TRADE"
        else:
            kw = "#CREDIT" if float(e["amount"]) > 0 else "#FEE"
        return f"{kw} {e['date']} {e['currency']} {M.shown(e['amount'], 2):+,.2f}"
    return None


def tt_comment(e: Dict[str, Any]) -> str:
    """What follows `# ` after a tt_line: a .tt line's file and line, a
    broker event's description (and where a .tt line declared it)."""
    if e.get("origin") == "tt":
        return str(e.get("where") or "")
    desc = " ".join(str(e.get("desc") or "").split())
    if e.get("decl_where"):
        desc = (desc + "; " if desc else "") + f"declared in {e['decl_where']}"
    return desc


def pair_internal(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop the moves of one statement that cancel inside it: an IB
    internal transfer between two accounts of a combined statement (the
    out and the in, same day, currency and amount), and a deposit
    advance with its cancellation. What is left unpaired stays, to be
    declared like any other move."""
    keep = [True] * len(events)
    by_class: Dict[Tuple, List[int]] = {}
    for i, e in enumerate(events):
        if e.get("kind") == "CASHMOVE" and e.get("internal"):
            # One statement: its account folder (label) and file too —
            # two accounts' statements both named activity.csv paired
            # one's transfer out with the other's transfer in (2026-10
            # audit #60). (`where` is the file's base name.)
            by_class.setdefault((e["internal"], e.get("label", ""),
                                 e.get("file", ""), e.get("where", ""),
                                 e["currency"]), []).append(i)
    for (cls, _l, _f, _w, _c), idx in by_class.items():
        pos = [i for i in idx if events[i]["amount"] > 0]
        neg = [i for i in idx if events[i]["amount"] < 0]
        used: set = set()
        for i in pos:
            best = None
            for j in neg:
                if j in used:
                    continue
                if abs(events[i]["amount"] + events[j]["amount"]) > 0.005:
                    continue
                if cls == "transfer" and events[i]["date"] != \
                        events[j]["date"]:
                    continue
                gap = abs((_d(events[i]["date"])
                           - _d(events[j]["date"])).days)
                if best is None or gap < best[0]:
                    best = (gap, j)
            if best is not None:
                used.add(best[1])
                keep[i] = keep[best[1]] = False
    return [e for i, e in enumerate(events) if keep[i]]


def _identity(e: Dict[str, Any]) -> Tuple:
    k = e["kind"]
    if k == "FXCONV":
        return (k, e.get("book"), e["date"], e["from_ccy"],
                M.units(e["from_amt"], 2), e["to_ccy"], M.units(e["to_amt"], 2))
    if k == "CASHBAL":
        return (k, e.get("book"), e["date"], e["currency"],
                M.units(e["balance"], 2))
    if k in ("CASHMOVE", "FLOW"):
        return (k, e.get("book"), e["date"], e["currency"],
                M.units(e["amount"], 2), e.get("desc", ""))
    return (k, id(e))


def dedup_across_files(events: List[Dict[str, Any]],
                       warnings: Optional[List[str]] = None
                       ) -> List[Dict[str, Any]]:
    """Two exports of one account that overlap (a year's statement and a
    year-to-date one) carry the same cash event twice: an event repeated
    in ANOTHER file of the book is one event, the most any one file
    holds of it kept (two identical conversions in one file are two) —
    the position books' cross-file rule. An event that carries its
    broker's own ID (`_broker_id`: a Coinbase row's ID) is one event per
    ID: two exports holding the same ID overlap, two holding different
    IDs are two executions, and an ID-less copy (an older export with no
    ID column) adds only what its file holds beyond the IDs (backlog
    TJ-0865: two exports' distinct round trips alike in date and amount
    were read as one). The settlement date is not part
    of an event's identity; when two files date one event's settlement
    differently, a line saying so (both files, both dates) is added to
    `warnings` (2026-10 audit #86)."""
    per: Dict[Tuple, Dict[str, int]] = {}
    # The ID-less events per identity and file, and the IDs per identity.
    bare: Dict[Tuple, Dict[str, int]] = {}
    ids: Dict[Tuple, set] = {}
    settles: Dict[Tuple, Dict[str, str]] = {}
    for e in events:
        k = _identity(e)
        f = per.setdefault(k, {})
        f[e.get("file", "")] = f.get(e.get("file", ""), 0) + 1
        bid = str(e.get("_broker_id") or "")
        if bid:
            ids.setdefault(k, set()).add(bid)
        else:
            b = bare.setdefault(k, {})
            b[e.get("file", "")] = b.get(e.get("file", ""), 0) + 1
        if e["kind"] in ("FXCONV", "CASHMOVE", "FLOW"):
            settles.setdefault(k, {}).setdefault(
                e.get("file", ""), str(e.get("settle") or e["date"]))
    if warnings is not None:
        for k, by_file in settles.items():
            if len(set(by_file.values())) < 2:
                continue
            files = sorted(by_file)
            e0 = next(e for e in events if _identity(e) == k)
            said = "; ".join(f"{f or '?'} settles it {by_file[f]}"
                             for f in files)
            warnings.append(
                f"{e0.get('book') or e0.get('label') or '?'}: one "
                f"{e0['kind']} of {e0['date']} is in {len(files)} files "
                f"with different settlement dates ({said}) — kept once, "
                f"dated {by_file[max(per[k], key=lambda f: (per[k][f], f))]}"
                f"; check which file is right")
    # The ID-less copies kept: those of the file holding the most,
    # beyond the events the IDs already account for.
    keep_n = {k: max(0, max(v.values()) - len(ids.get(k, ())))
              for k, v in bare.items()}
    keep_file = {k: max(v, key=lambda f: (v[f], f))
                 for k, v in bare.items()}
    seen_ids: set = set()
    # A conversion's commission (a FLOW with `_fee_of`, the broker's row
    # of its conversion) is kept with its own conversion: deduplicated
    # apart, the file tie-break kept one file's fee and the other
    # file's conversion, and the fee then matched no kept conversion
    # (backlog TJ-0860).
    conv_of: Dict[Tuple, Dict[str, Any]] = {}
    for e in events:
        if e["kind"] == "FXCONV" and e.get("where"):
            conv_of.setdefault((e.get("label", ""), e.get("file", ""),
                                e.get("book"), str(e["where"])), e)
    out: List[Dict[str, Any]] = []
    kept: set = set()
    fees: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    for i, e in enumerate(events):
        conv = (conv_of.get((e.get("label", ""), e.get("file", ""),
                             e.get("book"), str(e["_fee_of"])))
                if e["kind"] == "FLOW" and e.get("_fee_of") else None)
        if conv is not None:
            fees.append((i, e, conv))
            continue
        k = _identity(e)
        bid = str(e.get("_broker_id") or "")
        if bid:
            if (k, bid) in seen_ids:
                continue
            seen_ids.add((k, bid))
        else:
            if e.get("file", "") != keep_file[k]:
                continue
            if keep_n[k] <= 0:
                continue
            keep_n[k] -= 1
        out.append(e)
        kept.add(id(e))
    if fees:
        pos = {id(e): i for i, e in enumerate(events)}
        for i, e, conv in fees:
            if id(conv) in kept:
                out.append(e)
        out.sort(key=lambda e: pos[id(e)])
    return out


def _d(s: str) -> _date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


# ------------------------------------------------------------ books

def book_name(label: str, broker: str, account: str = "",
              several: bool = False, width: int = 4) -> str:
    """`<label>/<broker>[:<first `width` (4) of the hashed account>]`."""
    short = BROKER_SHORT.get(broker, broker)
    if account and several:
        return f"{label}/{short}:{account[:width]}"
    return f"{label}/{short}"


def _short_width(accounts: Any) -> int:
    """The shortest prefix (4 hex at least) that tells the hashed
    `accounts` apart: two accounts whose hashes share the first 4 get
    longer book names (`ib:31350`, `ib:3135a`), never one merged book."""
    accts = sorted({a for a in accounts if a})
    width = 4
    longest = max((len(a) for a in accts), default=width)
    while width < longest and \
            len({a[:width] for a in accts}) < len(accts):
        width += 1
    return width


class Books:
    """The books of the taxable accounts: which (label, broker, hashed
    broker account) each file's cash belongs to, and how a `.tt` file or
    an `at=` token picks one."""

    def __init__(self) -> None:
        # label -> {broker: set(account hashes)}
        self.seen: Dict[str, Dict[str, set]] = {}
        # (label, file name) -> broker  (CSV files of the label)
        self.file_broker: Dict[Tuple[str, str], str] = {}
        # (label, file name) -> set(account hashes) the file's rows carry
        self.file_accounts: Dict[Tuple[str, str], set] = {}
        # (label, .tt file name) -> the book its CASHBOOK line names
        self.tt_book: Dict[Tuple[str, str], str] = {}
        # books a `CASHBOOK <book> complete` line declares complete
        self.complete: set = set()

    def add(self, label: str, broker: str, account: str = "") -> None:
        self.seen.setdefault(label, {}).setdefault(broker, set()).add(
            account or "")

    def _several(self, label: str, broker: str) -> bool:
        accts = {a for a in self.seen.get(label, {}).get(broker, set())
                 if a}
        return len(accts) > 1

    def name(self, label: str, broker: str, account: str = "") -> str:
        accts = {a for a in self.seen.get(label, {}).get(broker, set())
                 if a}
        if not account and len(accts) == 1:
            account = next(iter(accts))
        return book_name(label, broker, account,
                         self._several(label, broker), _short_width(accts))

    def all_names(self, label: str) -> List[str]:
        out = []
        for broker, accts in sorted(self.seen.get(label, {}).items()):
            real = sorted(a for a in accts if a) or [""]
            for a in real:
                out.append(self.name(label, broker, a))
        return sorted(set(out))

    def unread(self) -> List[Tuple[str, str]]:
        """[(book, broker)] of every book whose broker's export the
        ledger reads no cash event from: its parse builds none (the parser
        registry's `builds_cash`, lib/brokerages/base CASH_EVENT_BROKERS;
        Webull, the generic importer). A book of theirs that moves foreign
        cash is refused until a `CASHBOOK <book> complete` line says its .tt
        cash lines are all of them (pre-release review M8)."""
        from taxjson.lib.brokerages.base import CASH_EVENT_BROKERS
        out = []
        for label in sorted(self.seen):
            for broker in sorted(self.seen[label]):
                if broker in CASH_EVENT_BROKERS:
                    continue
                accts = sorted(a for a in self.seen[label][broker]
                               if a) or [""]
                for a in accts:
                    out.append((self.name(label, broker, a), broker))
        return sorted(set(out))

    def for_file(self, label: str, file: str, account: str = ""
                 ) -> Optional[str]:
        """The book of a native row from `file` (a CSV the label's
        detection routed, or a .tt file), or None when it cannot be told
        (a .tt file in a label with several books and no name to go by)."""
        broker = self.file_broker.get((label, file))
        if broker:
            if not account:
                accts = {a for a in self.file_accounts.get(
                    (label, file), set()) if a}
                if len(accts) == 1:
                    account = next(iter(accts))
            return self.name(label, broker, account)
        return self.for_tt(label, file)

    def for_tt(self, label: str, file: str) -> Optional[str]:
        """A .tt file's book: its CASHBOOK line, else the folder's only
        book (or `<label>/tt` in a folder of .tt files only), else None —
        never guessed from the file's name."""
        named = self.tt_book.get((label, file))
        if named:
            return named
        names = self.all_names(label)
        if not names:
            return f"{label}/tt"        # a folder of .tt files only
        if len(names) == 1:
            return names[0]
        return None

    def for_at(self, label: str, at: str) -> Tuple[Optional[str], str]:
        """(book, problem) for an `at=` token: a broker's book of the
        label (`ib`, `rbc:3f2a`), else a book kept by hand under that
        name."""
        short, _, pre = at.partition(":")
        long_ = {v: k for k, v in BROKER_SHORT.items()}.get(short.lower())
        if long_ and long_ in self.seen.get(label, {}):
            accts = sorted(a for a in self.seen[label][long_] if a)
            if pre:
                hit = [a for a in accts if a.startswith(pre.lower())]
                if len(hit) != 1:
                    return None, (f"at={at} names no single {short} "
                                  f"account of {label} (books: "
                                  f"{', '.join(self.all_names(label))})")
                return self.name(label, long_, hit[0]), ""
            if len(accts) > 1:
                return None, (f"at={at}: {label} holds {len(accts)} "
                              f"{short} accounts — name one (books: "
                              f"{', '.join(self.all_names(label))})")
            return self.name(label, long_, accts[0] if accts else ""), ""
        return f"{label}/{at}", ""


# ------------------------------------------------------------ the parse's sidecar

# What taxjson-brokerage --cash-out writes beside a parse's books
# (work/<account>_<broker>.json -> work/<account>_<broker>.cash): a name
# no `*.json` glob over work/ picks up as a book.
SIDECAR_SUFFIX = ".cash"
SIDECAR_KIND = "cash_events"


def sidecar_path(parsed: Path) -> Path:
    """The cash-events sidecar of the parse whose books are `parsed`."""
    return Path(parsed).with_name(Path(parsed).stem + SIDECAR_SUFFIX)


def cash_sidecar(broker: str, account: str, paths: List[Path], country: str,
                 built: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 problems: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The cash-events sidecar of one parse (taxjson-brokerage --cash-out):
    for each of `paths` (one broker group's exports), its name as the run
    lists it (`name`), as diagnostics show it (`file`, `shown_name`), its
    events and `problem`: a cell the parse cannot read for them is a
    problem of that file, never a refused parse (the events read cells the
    parse itself never reads). Every parse writes one, Webull's and the
    generic importer's empty: `collect` still sees their files. `country`
    decides the stablecoin basis, recorded for `collect` to check. `built`:
    the events the parse built of a file (the parser's `cash_events`, by
    `str` of its path), taken as they are; a file of a broker whose parse
    builds them (CASH_EVENT_BROKERS) without them is that file's problem,
    never an empty list read as no events; another broker's files have
    none. `problems`: why the parse could not build a file's events (the
    parser's `cash_problem`, by `str` of its path, worded as `type:
    message`): that file's problem, its events none."""
    from taxjson.lib.brokerages.base import CASH_EVENT_BROKERS, shown_name
    from taxjson.lib.country import stablecoin_model
    stable_cash = stablecoin_model(country) == "cash"
    files: List[Dict[str, Any]] = []
    for p in sorted(Path(x) for x in paths):
        entry: Dict[str, Any] = {"name": p.name, "file": shown_name(p), "events": [],
                                 "problem": ""}
        if problems and problems.get(str(p)):
            entry["problem"] = problems[str(p)]
        elif built and str(p) in built:
            entry["events"] = list(built[str(p)])
        elif broker in CASH_EVENT_BROKERS:
            entry["problem"] = (f"ValueError: the cash events of {broker}'s exports are "
                                f"its parse's (taxjson-brokerage --cash-out): none for "
                                f"this file")
        files.append(entry)
    return {"files": files,
            "metadata": {"kind": SIDECAR_KIND, "account": account, "brokerage": broker,
                         "stablecoins_as_cash": stable_cash,
                         "input_files": sorted(p.name for p in map(Path, paths))}}


def _missing(label: str, group: str, rel: str) -> str:
    return (f"no cash events recorded for the {group} exports of taxable account "
            f"{label!r} ({rel}) — run `tjs run` first; a ledger without them would be "
            f"partial")


def _sidecar_files(work: Path, label: str, stable_cash: bool
                   ) -> List[Tuple[str, str, Dict[str, Any]]]:
    """(broker, its parser id; the file's entry) for every export of the
    taxable account `label` the last run parsed, from its sources list and
    each broker group's sidecar (`cash_sidecar`). ValueError naming `tjs
    run` for a list or a sidecar that is missing, cannot be read, or does
    not match the other (a run cut short, a sidecar of another basis)."""
    from taxjson.lib.check_dates import sources
    lst = work / f"{label}_sources.list"
    if not lst.is_file():
        raise ValueError(_missing(label, "broker", f"work/{lst.name}"))
    listed: Dict[str, Set[str]] = {}
    for line in lst.read_text(encoding="utf-8").splitlines():
        kind, _, name = line.partition("/")
        if name and kind not in ("setting", "map", "moves", "tt"):
            listed.setdefault(kind, set()).add(name)
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    for kind, group, parsed in sources(work, label):
        if kind != "broker":
            continue
        side = sidecar_path(parsed)
        rel = f"work/{side.name}"
        try:
            doc = json.loads(side.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(_missing(label, group, rel)) from None
        except (OSError, ValueError) as e:
            raise ValueError(f"{rel} cannot be read ({e}) — run `tjs run` to rebuild "
                             f"it; a ledger without it would be partial") from None
        md = doc.get("metadata") if isinstance(doc, dict) else None
        files = doc.get("files") if isinstance(doc, dict) else None
        if (not isinstance(md, dict) or md.get("kind") != SIDECAR_KIND
                or not isinstance(files, list)
                or not all(isinstance(f, dict) and isinstance(f.get("events"), list)
                           for f in files)):
            raise ValueError(f"{rel} is no cash-events file — run `tjs run` to rebuild it")
        names = {str(f.get("name")) for f in files}
        if names != listed.get(group, set()):
            raise ValueError(f"{rel} names other exports than the last run's "
                             f"work/{lst.name} — run `tjs run` first")
        if bool(md.get("stablecoins_as_cash")) != stable_cash:
            raise ValueError(f"{rel} was read with stablecoins as "
                             f"{'cash' if md.get('stablecoins_as_cash') else 'property'}, "
                             f"the project's country now books them as "
                             f"{'cash' if stable_cash else 'property'} — run `tjs run` first")
        out += [(str(md.get("brokerage") or ""), f["name"], f) for f in files]
    return out


# ------------------------------------------------------------ collection

def collect(root: Path, cfg: Dict[str, Any], country: str,
            native_by_label: Dict[str, List[Dict[str, Any]]],
            work: Optional[Path] = None) -> Dict[str, Any]:
    """Every cash event of the taxable accounts: {"events": [...] (each
    with `label` and `book`), "lines": [...] (the .tt cash lines, `book`
    resolved where it can be), "books": Books, "problems": [...]}.
    `native_by_label`: the accounts' native rows (their `source` and
    `source_account` tell each CSV's accounts). The events of the exports
    are the last run's parse's (`cash_sidecar`, in `work`, root/work by
    default), in the order of the account folder's files: ValueError
    naming `tjs run` when one is missing (`_sidecar_files`). Whether they
    are as new as the inputs is the caller's to refuse (cli/common
    `_refuse_cash_older_than_inputs`)."""
    from taxjson.lib.country import stablecoin_model
    # (A missing country refused, as everywhere past the parse.)
    stable_cash = stablecoin_model(country) == "cash"
    work = Path(work) if work is not None else Path(root) / "work"
    accounts = cfg.get("accounts") or {}
    books = Books()
    events: List[Dict[str, Any]] = []
    problems: List[str] = []
    for label in sorted(accounts):
        acfg = typed_account(accounts.get(label), label)
        if not acfg.taxable:
            continue
        folder = _PL.inputs_dir(Path(root)) / label
        for row in native_by_label.get(label, []):
            src = re.sub(r"#\d+$", "", str(row.get("source") or ""))
            sa = str(row.get("source_account") or "")
            books.file_accounts.setdefault((label, src), set()).add(sa)
        if not folder.is_dir() or not any(
                p.suffix.lower() == ".csv" and not p.name.startswith((".", "~$"))
                and p.is_file() and not _PL.redacted_copy_beside_exports(p)
                for p in folder.iterdir()):
            continue
        # The folder's files in its order (`tjs run`'s input_files), each
        # under the broker its detection routed it to.
        for broker, _n, entry in sorted(_sidecar_files(work, label, stable_cash),
                                        key=lambda x: x[1]):
            name = str(entry.get("file") or "")
            books.file_broker[(label, name)] = broker
            for a in books.file_accounts.get((label, name), {""}):
                books.add(label, broker, a)
            if entry.get("problem"):
                problems.append(f"{label}/{name}: its cash events could "
                                f"not be read ({entry['problem']})")
                continue
            found = [dict(ev) for ev in entry["events"]]
            for ev in found:
                ev["label"] = label
                ev["broker"] = broker
                ev["file"] = name
                acct = ev.get("account") or ""
                books.add(label, broker, acct)
            events.extend(found)
    for ev in events:
        acct = ev.get("account") or ""
        if not acct:
            accts = {a for a in books.file_accounts.get(
                (ev["label"], ev["file"]), set()) if a}
            if len(accts) == 1:
                acct = next(iter(accts))
        ev["book"] = books.name(ev["label"], ev["broker"], acct)
    warnings: List[str] = []
    events = dedup_across_files(pair_internal(events), warnings)
    lines, lp = read_project_lines(root, accounts)
    problems.extend(lp)
    for ev in [x for x in lines if x["kind"] == "CASHBOOK"]:
        b, why = books.for_at(ev["label"], ev["book_name"])
        k = (ev["label"], ev["file"])
        if why:
            problems.append(f"{ev['where']}: {why}")
        elif k in books.tt_book and books.tt_book[k] != b:
            problems.append(f"{ev['where']}: a second CASHBOOK in "
                            f"{ev['file']} names another book ("
                            f"{books.tt_book[k]}) — one file, one account's "
                            f"cash; split the file")
        else:
            books.tt_book[k] = b
        if b and ev.get("complete"):
            books.complete.add(b)
    lines = [x for x in lines if x["kind"] != "CASHBOOK"]
    for ev in lines:
        if ev.get("at"):
            b, why = books.for_at(ev["label"], ev["at"])
        else:
            b = books.for_tt(ev["label"], ev["file"])
            why = "" if b else (
                f"its account folder holds several books "
                f"({', '.join(books.all_names(ev['label'])) or 'none'}) "
                f"— add at=<book>, or a CASHBOOK <book> line to the file")
        ev["book"] = b
        ev["book_problem"] = why
    return {"events": events, "lines": lines, "books": books,
            "problems": problems, "warnings": warnings,
            "stablecoins_as_cash": stable_cash}
