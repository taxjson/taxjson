"""Dated events declared in an account's .tt files: JOURNAL and RENAME.

ticker.map holds standing truths about securities (GLOBAL, TOBASE,
DISTINCT, DELETE, the lookups). A journal between two listings and a
ticker change happen ON A DATE: they are events in the books, like a
trade or a dividend, written date first in a .tt file of an account
(`taxjson-convert-tt` checks the line; it is never a row of the
converted file — `taxjson run` reads it here):

  JOURNAL <date> <FROM> <TO> <qty> [separate]
      <qty> units moved from listing FROM to listing TO of one security
      inside this account (a Norbert's gambit's journal, a TSX line moved
      to its NYSE line). Booked as the move's two transfer legs (an
      out-leg of FROM and an in-leg of TO sharing a `journal_pair` id,
      `event_source` "tt"), kept with the account's transfer evidence
      (work/<acct>_tt-journal_transfers.json) the way a broker's own
      journal legs are: the holdings view moves the units, the
      missing-history walks read the day as a journal, and
      lib/cross_listings joins FROM and TO as one security (a TOBASE line
      of the run's effective map, its direction lib/cross_listings.
      tobase_direction) — in both countries (tax-logic CA-XLIST-04 /
      US-XLIST-03). No disposition, no row of the tax books. A journal
      whose legs the broker's rows already hold (both legs: the account's
      out-leg of FROM and in-leg of TO, the same quantity, within
      cross_listings.PAIR_DAYS business days) is a duplicate: said as
      Info and not booked twice; with one leg there, only the other leg
      is booked. A line ending `separate` is a journal of its own: booked
      in full, never settled against the broker's legs nor read as a
      restatement of a broker's journal near it.

  RENAME <date> <OLD> <NEW> [late=fold|late=separate]
      a ticker change on that date (lib/renames). Declared once, in any
      account's .tt file, it applies to every account OF THE SAME KIND
      (securities or crypto: a security's ticker change never touches a
      coin, nor a coin's a security) whose books carry OLD before the
      date (a rename is the security's, not an account's), and is
      recorded once. `late=` describes how one broker booked the late
      rows: a line's choice applies to the declaring account's late rows
      and to every account without a line of its own; an account that
      books them differently says so in a line of its own (the same
      change, its own late=) — DatedRename.late_for. Every declaration
      of one change (.tt lines in several accounts, a legacy map line)
      is merged into one event (resolve_renames): its date is the
      earliest declared; declarations that cannot all be true stop the
      run naming the lines (OLD to two symbols, one change on two dates
      more than renames.WINDOW_DAYS apart, a cycle such as A -> B plus
      B -> A, one account choosing late=fold and late=separate). The run
      writes the events into its effective map (work/ticker.map.
      effective, with the line's place in a `# .tt: <where>` note, and
      the merged facts in a JSON tail — EVENT_META) so every stage that
      books renames reads them: the SPLIT row it books carries
      `event_source` "tt".

The legacy ticker.map forms (`JOURNAL FROM TO`: read as TOBASE;
`RENAME OLD NEW YYYY-MM-DD`) are still read, with one Warning per run
(legacy_note); `taxjson format-map --write` migrates them
(lib/ticker_map_format.migrate).

Every event the run booked or refused is recorded in
work/dated_events.state (FORMAT), each with its `source` ("tt", "map",
"ib-conid", "broker") and `where`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from taxjson.lib import project_layout as _PL

STATE = "dated_events.state"
FORMAT = "dated_events/1"
# The transfer-evidence file of an account's declared journal legs:
# work/<acct>_tt-journal_transfers.json (a "transfer_sidecar").
SIDECAR_BROKER = "tt-journal"
SOURCE_TT = "tt"
# The time a declared journal's legs are dated at (a .tt JOURNAL line
# has no time column; the .tt convention for an unknown time).
LEG_TIME = "09:30:00"
# The note the run writes after a .tt RENAME line in its effective map:
# `# .tt: <inputs/acct/file.tt:N> | <the line as written>`.
# The merged event's facts follow in a JSON tail: `... || {json}`.
TT_ORIGIN_RE = re.compile(r"^\s*\.tt: (.+?\.tt:\d+)(?: \| (.*?))?"
                          r"(?: \|\| (\{.*\}))?\s*$", re.IGNORECASE)
# The facts of a legacy ticker.map line's event that .tt lines of the
# same change merged into (its date, its late= choices, its other
# declarations): a comment line of the effective map, `# dated-event:
# {json}` (the line itself stays as the user wrote it, above).
EVENT_META = "# dated-event: "
EFFECTIVE_HEAD = ("# taxjson run: ticker changes declared in .tt files "
                  "(lib/dated_events)")

STATUS_BOOKED = "booked"
STATUS_DUPLICATE = "duplicate"
STATUS_PARTIAL = "partial"


class DatedEventError(ValueError):
    """A .tt JOURNAL or RENAME line that cannot be booked (malformed, in a
    crypto account, contradicting another declaration): str(e) names the
    line."""


def sidecar_path(cache: Path, account: str) -> Path:
    return Path(cache) / f"{account}_{SIDECAR_BROKER}_transfers.json"


def tt_files(acct_dir: Path) -> List[Path]:
    """An account folder's .tt files, as `taxjson run` reads them (the
    suffix in any case; hidden and Office lock files skipped)."""
    acct_dir = Path(acct_dir)
    if not acct_dir.is_dir():
        return []
    return [p for p in sorted(acct_dir.iterdir())
            if p.suffix.lower() == ".tt" and p.is_file()
            and not p.name.startswith((".", "~$"))]


@dataclass
class Journal:
    """One `.tt` JOURNAL line."""
    account: str
    date: str
    frm: str
    to: str
    quantity: float
    where: str                  # "inputs/<acct>/<file>.tt:<n>"
    line: str = ""
    pair: str = ""              # the legs' journal_pair id
    status: str = STATUS_BOOKED
    legs: Tuple[str, ...] = ("out", "in")
    note: str = ""
    source_name: str = ""       # the .tt file's shown name (row `source`)
    source_key: str = ""
    # The line ends with `separate`: a journal of its own, booked in full
    # — never settled against the broker's legs near it, never read as
    # a restatement of a broker's journal (v0.24.1 leftovers, 3).
    separate: bool = False

    def record(self) -> Dict[str, Any]:
        out = {"date": self.date, "account": self.account,
               "from": self.frm, "to": self.to,
               "quantity": self.quantity, "source": SOURCE_TT,
               "where": self.where, "pair": self.pair,
               "status": self.status, "legs": list(self.legs)}
        if self.separate:
            out["separate"] = True
        if self.note:
            out["note"] = self.note
        return out

    def rows(self) -> List[Dict[str, Any]]:
        """The legs this line books (TRANSFER rows; `legs`)."""
        from taxjson.lib.price_chain import quote_currency
        out = []
        for leg, sym, q in (("out", self.frm, -self.quantity),
                            ("in", self.to, self.quantity)):
            if leg not in self.legs:
                continue
            row = {"action": "TRANSFER", "date": self.date,
                   "time": LEG_TIME, "date_settle": self.date,
                   "symbol": sym, "quantity": q,
                   "currency": quote_currency(sym) or "",
                   "price": 0.0, "net_amount": 0.0,
                   "account": self.account,
                   "description": (f"JOURNAL {self.frm} -> {self.to} "
                                   f"({self.where})"),
                   "journal_pair": self.pair, "event_source": SOURCE_TT,
                   "source": self.source_name}
            if self.source_key:
                row["source_key"] = self.source_key
            out.append(row)
        return out


@dataclass
class Declarations:
    journals: List[Journal] = field(default_factory=list)
    # The rename events a .tt line declares and no ticker.map line does
    # (renames.DatedRename, merged: resolve_renames) — the effective
    # map's .tt lines.
    renames: List[Any] = field(default_factory=list)
    # Every rename event of the project, ticker.map's included (set by
    # check_against_map; the .tt ones alone without a map).
    events: List[Any] = field(default_factory=list)
    # The .tt RENAME lines as written (one DatedRename each).
    tt_declared: List[Any] = field(default_factory=list)
    # Info lines: a rename declared twice, a journal the broker's rows
    # already hold.
    notes: List[str] = field(default_factory=list)
    # Warning lines: a journal ticker.map keeps apart (DISTINCT).
    warnings: List[str] = field(default_factory=list)


def _where(acct: str, path: Path, lineno: int) -> str:
    from taxjson.lib.brokerages.base import shown_name
    return f"inputs/{acct}/{shown_name(path)}:{lineno}"


def read_declarations(root: Path, accounts: Dict[str, Any]) -> Declarations:
    """Every JOURNAL and RENAME line of the accounts' .tt files (sorted
    by account). Raises DatedEventError listing each line that cannot be
    booked: a malformed one (the message names the form), a JOURNAL in
    a crypto account, two RENAME lines of one ticker that disagree."""
    from taxjson.bin.taxjson_convert_tt import (parse_journal_line,
                                                parse_rename_line)
    from taxjson.lib.brokerages.base import shown_name, source_key
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.renames import DatedRename
    root = Path(root)
    out = Declarations()
    problems: List[str] = []
    counter: Dict[Tuple[str, str], int] = {}
    tt_renames: List[DatedRename] = []
    for acct in sorted(accounts or {}):
        acfg = accounts.get(acct) or {}
        for tt in tt_files(_PL.inputs_dir(root) / acct):
            try:
                text = read_text_utf8(tt)
            except (OSError, ValueError):
                continue        # the .tt stage names an unreadable file
            for n, raw in enumerate(text.splitlines(), 1):
                if "JOURNAL" not in raw and "RENAME" not in raw:
                    continue
                where = _where(acct, tt, n)
                try:
                    j = parse_journal_line(raw, where)
                    r = None if j is not None else \
                        parse_rename_line(raw, where)
                except ValueError as e:
                    problems.append(str(e))
                    continue
                if j is not None:
                    if isinstance(acfg, dict) and acfg.get("crypto"):
                        problems.append(
                            f"{where}: JOURNAL moves units between two "
                            f"listings of a security; account {acct} is a "
                            f"crypto account (crypto = true) and has none: "
                            f"{j['line']!r}")
                        continue
                    k = (acct, j["date"])
                    counter[k] = counter.get(k, 0) + 1
                    out.journals.append(Journal(
                        acct, j["date"], j["from"], j["to"],
                        j["quantity"], where, j["line"],
                        pair=f"tt:{acct}:{j['date']}#{counter[k]}",
                        source_name=shown_name(tt),
                        source_key=source_key(tt),
                        separate=bool(j.get("separate"))))
                elif r is not None and r.get("noop"):
                    out.notes.append(r["noop"])  # one listing: nothing
                elif r is not None:
                    tt_renames.append(DatedRename(
                        r["old"], r["new"], r["date"], r["late"], where,
                        r["line"], source=SOURCE_TT, account=acct,
                        kind=account_kind(acfg)))
    out.tt_declared = tt_renames
    kept, notes, errs = resolve_renames(tt_renames, [])
    problems += errs
    out.notes += notes
    out.renames = kept
    out.events = list(kept)
    if problems:
        raise DatedEventError("\n".join(problems))
    return out


def account_kind(acfg: Any) -> str:
    """renames.KIND_CRYPTO for a crypto account (crypto = true), else
    renames.KIND_SECURITIES."""
    from taxjson.lib.renames import KIND_CRYPTO, KIND_SECURITIES
    return KIND_CRYPTO if isinstance(acfg, dict) and acfg.get("crypto") \
        else KIND_SECURITIES


def _kinds_meet(a: str, b: str) -> bool:
    """Two declarations can name one event: a legacy map line (no kind)
    meets every account; .tt lines meet within one kind."""
    return not a or not b or a == b


def _decl_text(dr: Any) -> str:
    return (f"{dr.where} (RENAME {dr.date} {dr.old} {dr.new}"
            + (f" late={dr.late}" if dr.late else "") + ")")


def resolve_renames(tt: Sequence[Any], mapped: Sequence[Any]
                    ) -> Tuple[List[Any], List[str], List[str]]:
    """(the rename EVENTS, Info notes, problems) of the project's dated
    RENAME declarations: `tt` the .tt lines as written, `mapped` the
    legacy ticker.map lines (renames.DatedRename). Declarations of one
    change — the same OLD and NEW within renames.WINDOW_DAYS, of one
    account kind (a map line meets both) — are one event, whatever order
    the files are read in:

      - its date is the earliest declared;
      - it is recorded at the map line when there is one (the line stays
        in the map), else at the first .tt line by date and place; the
        others are its `also`;
      - its kind: the map line's (every account) or the .tt lines';
      - late=: each declaring account's own non-empty choice is that
        account's (`lates`); the event's `late` (an account without a
        line of its own) is the map line's, else the one choice the .tt
        lines agree on, else none (an account without its own line then
        lists its late rows as unresolved).

    Problems (each naming every line involved): OLD renamed to two
    symbols within the window; one change declared on two dates further
    apart (two events of one pair); a cycle of changes (A -> B and
    B -> A); one account declaring both late=fold and late=separate."""
    from dataclasses import replace
    from taxjson.lib.renames import SOURCE_TT, WINDOW_DAYS, _days
    decls = list(mapped) + list(tt)
    order = sorted(range(len(decls)), key=lambda i: (
        decls[i].date, decls[i].source == SOURCE_TT, decls[i].where, i))
    clusters: List[List[Any]] = []
    problems: List[str] = []
    notes: List[str] = []
    said: set = set()

    def problem(msg: str) -> None:
        if msg not in said:
            said.add(msg)
            problems.append(msg)

    for i in order:
        dr = decls[i]
        home = None
        for c in clusters:
            for o in c:
                gap = _days(o.date, dr.date)
                if (o.old != dr.old or gap is None or gap > WINDOW_DAYS
                        or not _kinds_meet(o.kind, dr.kind)):
                    continue
                if o.new != dr.new:
                    problem(f"{dr.where}: RENAME {dr.date} {dr.old} "
                            f"{dr.new} contradicts {o.where} ({o.old} -> "
                            f"{o.new} on {o.date}) — one ticker change, "
                            f"one line: {dr.line!r}")
                    home = False
                    break
                home = c
                break
            if home is not None:
                break
        if home is False:
            continue
        if home is None:
            clusters.append([dr])
        else:
            home.append(dr)
    events: List[Any] = []
    for c in clusters:
        maps = [d for d in c if d.source != SOURCE_TT]
        tts = [d for d in c if d.source == SOURCE_TT]
        prim = maps[0] if maps else tts[0]
        own: Dict[str, Dict[str, Any]] = {}
        for d in tts:
            if d.late:
                own.setdefault(d.account, {}).setdefault(d.late, d)
        for acct, by in sorted(own.items()):
            if len(by) > 1:
                problem(f"account {acct} declares the ticker change "
                        f"{prim.old} -> {prim.new} both late=fold and "
                        f"late=separate: "
                        f"{'; '.join(_decl_text(d) for d in by.values())}"
                        f" — keep one line")
        map_late = next((d.late for d in maps if d.late), "")
        tt_lates = sorted({d.late for d in tts if d.late})
        late = map_late or (tt_lates[0] if len(tt_lates) == 1 else "")
        if not map_late and len(tt_lates) > 1:
            notes.append(
                f"{prim.where}: the ticker change {prim.old} -> "
                f"{prim.new} is declared late=fold in one account and "
                f"late=separate in another: each applies to its own "
                f"account's late rows; an account with no line of its "
                f"own has its late rows listed as unresolved (`taxjson "
                f"renames`)")
        lates = tuple(sorted((a, next(iter(by))) for a, by in own.items()
                             if len(by) == 1))
        ev = replace(prim, date=min(d.date for d in c), late=late,
                     kind=prim.kind if maps else tts[0].kind,
                     lates=lates,
                     also=tuple(d.where for d in c if d is not prim))
        if maps:
            ev = replace(ev, kind="")
        for d in c:
            if d is not prim:
                notes.append(f"{d.where}: RENAME {d.old} -> {d.new} on "
                             f"{d.date} repeats {prim.where}: one event, "
                             f"booked once"
                             + (f" on {ev.date}" if d.date != ev.date
                                or prim.date != ev.date else ""))
        events.append(ev)
    # One change on two dates: two events of one pair.
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if (a.old, a.new) == (b.old, b.new) and \
                    _kinds_meet(a.kind, b.kind):
                problem(f"{b.where}: the ticker change {b.old} -> {b.new} "
                        f"is declared on {b.date} here and on {a.date} at "
                        f"{a.where} — one change has one date: keep one "
                        f"line (lines more than {WINDOW_DAYS} days apart "
                        f"would book two changes): {b.line!r}")
    # A cycle: A -> B, B -> A (or longer).
    for kind in ("securities", "crypto"):
        edges: Dict[str, List[Any]] = {}
        for e in events:
            if _kinds_meet(e.kind, kind):
                edges.setdefault(e.old, []).append(e)
        for cyc in _cycles(edges):
            syms = " -> ".join([e.old for e in cyc] + [cyc[0].old])
            problem(f"the ticker changes {syms} form a cycle: "
                    f"{'; '.join(_decl_text(e) for e in cyc)} — a chain "
                    f"of ticker changes must end at one symbol; keep the "
                    f"real ones (a symbol that really changed back is "
                    f"booked in its account with a .tt line `SPLIT <date> "
                    f"<time> OLD NEW 1`)")
    events.sort(key=lambda e: (e.date, e.old, e.new))
    return events, notes, problems


def _cycles(edges: Dict[str, List[Any]]) -> List[List[Any]]:
    """The cycles of the rename graph `edges` ({old: [event]}), each as
    its events in order, each cycle once (from its smallest symbol)."""
    out: List[List[Any]] = []
    seen: set = set()

    def walk(start: str, cur: str, path: List[Any]) -> None:
        for e in edges.get(cur, ()):
            if e.new == start:
                cyc = path + [e]
                key = frozenset(id(x) for x in cyc)
                if key not in seen:
                    seen.add(key)
                    out.append(cyc)
            elif e.new > start and all(x.old != e.new for x in path) \
                    and len(path) < 16:
                walk(start, e.new, path + [e])

    for start in sorted(edges):
        walk(start, start, [])
    return out


def check_against_map(decl: Declarations, tmap) -> List[str]:
    """Settle the .tt declarations against the project's ticker.map
    (`tmap`, None without one): every dated RENAME, the map's legacy
    lines included, is merged into its events (resolve_renames: a .tt
    line repeating a map line is one event, recorded at the map line;
    one the map contradicts is a problem, returned); a .tt RENAME of a
    symbol an undated map rule renames at every date is a problem too.
    Warnings (decl.warnings): a .tt RENAME naming a symbol a TOBASE,
    DELETE or DISTINCT line also decides, and a .tt JOURNAL between two
    listings a DISTINCT line keeps apart (its legs are booked, the
    listings stay separate)."""
    if tmap is None:
        return []
    events, notes, problems = resolve_renames(
        decl.tt_declared, list(tmap.dated or ()))
    from taxjson.lib.renames import SOURCE_TT
    decl.events = events
    decl.renames = [e for e in events if e.source == SOURCE_TT]
    decl.notes = notes      # (read_declarations' notes are renames')
    for dr in decl.renames:
        if dr.old in tmap.glob:
            problems.append(
                f"{dr.where}: RENAME {dr.date} {dr.old} {dr.new} is dated, "
                f"but ticker.map renames {dr.old} to {tmap.glob[dr.old]} at "
                f"every date (GLOBAL or an undated RENAME) — declare the "
                f"change on the name ticker.map gives it, `RENAME "
                f"{dr.date} {tmap.glob[dr.old]} {dr.new}` (it books on "
                f"the {dr.old} rows too), or keep only one of the two: "
                f"{dr.line!r}")
            continue
        for sym in (dr.old, dr.new):
            if sym in (tmap.tobase or {}):
                decl.warnings.append(
                    f"{dr.where}: RENAME {dr.date} {dr.old} {dr.new}: "
                    f"ticker.map also joins {sym} to {tmap.tobase[sym]} "
                    f"(TOBASE, at every date, in the base-currency books) "
                    f"— the change is booked on the exports' symbols "
                    f"first; check that both lines are meant")
            if sym in (tmap.delete or ()):
                decl.warnings.append(
                    f"{dr.where}: RENAME {dr.date} {dr.old} {dr.new}: "
                    f"ticker.map deletes every row of {sym} (DELETE {sym})"
                    f" — the change carries "
                    f"{'nothing' if sym == dr.old else 'the position into rows that are deleted'}"
                    f"; delete one of the two lines")
        if frozenset((dr.old, dr.new)) in (tmap.distinct or ()):
            decl.warnings.append(
                f"{dr.where}: RENAME {dr.date} {dr.old} {dr.new} carries "
                f"the position from one to the other, and ticker.map "
                f"keeps them apart (DISTINCT {dr.old} {dr.new}): after "
                f"the date they are two securities — delete one of the "
                f"two lines if that is not meant")
    for j in decl.journals:
        if frozenset((j.frm, j.to)) in (tmap.distinct or ()):
            decl.warnings.append(
                f"{j.where}: JOURNAL {j.date} {j.frm} {j.to} moves units "
                f"between two listings ticker.map keeps apart (DISTINCT "
                f"{j.frm} {j.to}): the legs are booked, the listings stay "
                f"two securities — delete one of the two lines")
    return problems


def project_renames(root: Path, accounts: Optional[Dict[str, Any]] = None,
                    tmap: Any = None) -> List[Any]:
    """Every rename event of a project as `taxjson run` books it
    (resolve_renames over the .tt lines and ticker.map's dated lines);
    only the map's lines when a .tt line cannot be read (the run names
    it). `tmap` the parsed ticker.map (None: none)."""
    if accounts is None:
        accounts = _project_accounts(root)
    mapped = list(getattr(tmap, "dated", ()) or ()) if tmap else []
    try:
        decl = read_declarations(root, accounts)
    except DatedEventError:
        return mapped
    events, _n, problems = resolve_renames(decl.tt_declared, mapped)
    return events if not problems else mapped + decl.renames


# ------------------------------------------------------------ journals

def settle_journals(journals: Iterable[Journal], legs: Iterable[Any],
                    days: Optional[int] = None) -> List[str]:
    """Set each journal's status against the broker's transfer legs
    (lib/cross_listings.Leg, the account's parsed exports — books and
    sidecars): both legs there (the account's out-leg of FROM and in-leg
    of TO, the same quantity, within `days` business days of the line's
    date, the closest first) is a duplicate, booked from the broker's
    rows only; one leg there books the other only. Returns the Info
    lines."""
    from taxjson.lib.cross_listings import PAIR_DAYS, _d, business_days
    days = PAIR_DAYS if days is None else days
    legs = [g for g in legs if getattr(g, "broker", "") != "tt"]
    claimed: set = set()
    notes: List[str] = []

    def find(j: Journal, sym: str, sign: int):
        best = None
        jd = _d(j.date)
        for g in legs:
            if (id(g) in claimed or g.account != j.account
                    or g.symbol != sym or g.quantity * sign <= 0
                    or abs(abs(g.quantity) - j.quantity)
                    > max(1e-6, 1e-6 * j.quantity)):
                continue
            gd = _d(g.date)
            if gd is None or jd is None:
                continue
            gap = business_days(gd, jd)
            if gap > days:
                continue
            if best is None or gap < best[0]:
                best = (gap, g)
        return best[1] if best else None

    for j in sorted(journals, key=lambda x: (x.account, x.date, x.where)):
        if j.separate:
            # Declared a journal of its own: both legs booked, no
            # broker leg claimed.
            j.status, j.legs, j.note = STATUS_BOOKED, ("out", "in"), ""
            continue
        o = find(j, j.frm, -1)
        i = find(j, j.to, +1)
        for g in (o, i):
            if g is not None:
                claimed.add(id(g))
        if o is not None and i is not None:
            j.status, j.legs = STATUS_DUPLICATE, ()
            j.note = (f"the broker's rows already hold this journal (out "
                      f"of {j.frm} on {o.date}, into {j.to} on {i.date})")
            notes.append(f"{j.where}: JOURNAL {j.date} {j.frm} {j.to} "
                         f"{j.quantity:g} is already in the broker's rows "
                         f"({o.date}, {i.date}): booked from them, not "
                         f"twice")
        elif o is not None or i is not None:
            g = o if o is not None else i
            j.status = STATUS_PARTIAL
            j.legs = ("in",) if o is not None else ("out",)
            j.note = (f"the broker's rows hold the "
                      f"{'out' if o is not None else 'in'}-leg ({g.symbol} "
                      f"on {g.date}); the line adds the other")
            notes.append(f"{j.where}: JOURNAL {j.date} {j.frm} {j.to} "
                         f"{j.quantity:g}: the broker's rows hold its "
                         f"{'out' if o is not None else 'in'}-leg "
                         f"({g.symbol} on {g.date}); the "
                         f"{'in' if o is not None else 'out'}-leg is booked "
                         f"from the .tt line")
        else:
            j.status, j.legs, j.note = STATUS_BOOKED, ("out", "in"), ""
    return notes


def write_sidecars(cache: Path, accounts: Iterable[str],
                   journals: Iterable[Journal]) -> List[Path]:
    """Write each account's declared journal legs to its transfer
    evidence file (sidecar_path); remove the file of an account that has
    none. Returns the files written."""
    by_acct: Dict[str, List[Dict[str, Any]]] = {}
    for j in journals:
        by_acct.setdefault(j.account, []).extend(j.rows())
    written = []
    for acct in sorted(set(accounts)):
        p = sidecar_path(cache, acct)
        rows = by_acct.get(acct) or []
        if not rows:
            if p.exists():
                p.unlink()
            continue
        doc = {"transactions": rows,
               "metadata": {"kind": "transfer_sidecar", "account": acct,
                            "brokerage": SIDECAR_BROKER,
                            "source_brokerage": "manual (.tt)",
                            "converted_by": "taxjson run (dated events)"}}
        text = json.dumps(doc, indent=2, sort_keys=True,
                          ensure_ascii=False) + "\n"
        try:
            old = p.read_text(encoding="utf-8")
        except OSError:
            old = None
        if old != text:
            from taxjson.lib.cli_diag import write_text_atomic
            write_text_atomic(p, text)
        written.append(p)
    return written


def journal_legs(journals: Iterable[Journal]):
    """lib/cross_listings.Leg of each declared leg the journals book
    (broker "tt", `decl` the journal's pair id)."""
    from taxjson.lib.cross_listings import Leg
    out = []
    for j in journals:
        for row in j.rows():
            out.append(Leg(j.account, "tt", row["symbol"], j.date,
                           row["quantity"], currency=row["currency"],
                           journal="tt", decl=j.pair))
    return out


# ------------------------------------------------------------ renames

def _meta(dr: Any) -> Dict[str, Any]:
    return {"account": dr.account, "kind": dr.kind,
            "lates": dict(dr.lates), "also": list(dr.also)}


def effective_lines(events: Iterable[Any]) -> List[str]:
    """The rename events as lines of the run's effective map: an event a
    .tt line records as a RENAME line with its origin note and its
    merged facts (TT_ORIGIN_RE); an event recorded at a legacy map line
    (the line is in the map above) that .tt lines merged into, as an
    EVENT_META comment line naming the map line by its OLD, NEW and
    written date."""
    from taxjson.lib.renames import SOURCE_TT
    out = []
    for dr in events:
        if dr.source == SOURCE_TT:
            out.append(f"RENAME {dr.old} {dr.new} {dr.date}"
                       + (f" late={dr.late}" if dr.late else "")
                       + f"  # .tt: {dr.where} | {dr.line} || "
                       + json.dumps(_meta(dr), sort_keys=True))
        elif dr.also or dr.lates:
            m = _meta(dr)
            m.update({"old": dr.old, "new": dr.new, "written": _written(dr),
                      "date": dr.date, "late": dr.late})
            out.append(EVENT_META + json.dumps(m, sort_keys=True))
    return out


def _written(dr: Any) -> str:
    """The date a declaration's line is written with (a merged event
    keeps its line's text in `line`)."""
    m = re.search(r"\d{4}-\d{2}-\d{2}", dr.line or "")
    return m.group(0) if m else dr.date


def apply_meta(dated: List[Any], metas: List[Dict[str, Any]],
               origin: Dict[int, Dict[str, Any]]) -> List[Any]:
    """The effective map's dated lines with their merged facts: `origin`
    {index in dated: the JSON tail of its .tt origin note}, `metas` the
    EVENT_META lines (a legacy map line's event)."""
    from dataclasses import replace
    out = []
    for i, dr in enumerate(dated):
        m = origin.get(i)
        if m is None:
            m = next((x for x in metas if (x.get("old"), x.get("new"),
                                           x.get("written"))
                      == (dr.old, dr.new, dr.date)), None)
            if m is not None:
                dr = replace(dr, date=str(m.get("date") or dr.date),
                             late=str(m.get("late") or ""))
        if isinstance(m, dict):
            lates = m.get("lates") if isinstance(m.get("lates"), dict) \
                else {}
            dr = replace(
                dr, account=str(m.get("account") or ""),
                kind=str(m.get("kind") or ""),
                lates=tuple(sorted((str(a), str(v)) for a, v in
                                   lates.items())),
                also=tuple(str(w) for w in (m.get("also") or [])
                           if isinstance(m.get("also"), list)))
        out.append(dr)
    return out


def tt_renames(root: Path, accounts: Optional[Dict[str, Any]] = None
               ) -> List[Any]:
    """The .tt RENAME declarations of a project (empty when a line cannot
    be read: `taxjson run` names it). `accounts` defaults to the
    project's taxjson.toml."""
    if accounts is None:
        accounts = _project_accounts(root)
    try:
        return read_declarations(root, accounts).renames
    except DatedEventError:
        return []


def _project_accounts(root: Path) -> Dict[str, Any]:
    try:
        doc = _PL.read_config_soft(root)
    except Exception:                               # noqa: BLE001
        return {}
    accts = doc.get("accounts") if isinstance(doc, dict) else None
    return accts if isinstance(accts, dict) else {}


# ------------------------------------------------------------ legacy map

def legacy_lines(tmap) -> Tuple[int, int]:
    """(JOURNAL lines, dated RENAME lines) of a parsed ticker.map: the
    legacy forms `taxjson format-map --write` migrates."""
    if tmap is None:
        return 0, 0
    return (len(getattr(tmap, "journal", {}) or {}),
            sum(1 for d in (getattr(tmap, "dated", ()) or ())
                if getattr(d, "source", "map") != SOURCE_TT))


def legacy_note(tmap) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the one Warning per run about a ticker.map
    that still holds dated events; None when it holds none."""
    nj, nr = legacy_lines(tmap)
    if not nj and not nr:
        return None
    what = " and ".join(x for x in (
        f"{nj} JOURNAL line(s)" if nj else "",
        f"{nr} dated RENAME line(s)" if nr else "") if x)
    return (f"ticker.map holds {what}: dated events written the old way",
            ["They still work in the tax books (a JOURNAL line is read "
             "as TOBASE, a dated RENAME as the ticker change on its "
             "date). A JOURNAL line no longer moves units in the holdings "
             "view: a journal's units move by its rows — the broker's, or "
             "a .tt line `JOURNAL <date> FROM TO <qty>` when the export "
             "lacks them. Dated events now go in an account's .tt file, "
             "date first (`JOURNAL <date> FROM TO <qty>`, `RENAME <date> "
             "OLD NEW`); `taxjson format-map --write` migrates the lines "
             "(the tax books stay the same — it refuses a move that would "
             "change them) and names a JOURNAL line to add where the "
             "holdings show the two listings long and short."])


# ------------------------------------------------------------ the record

def state_doc(journals: Iterable[Journal],
              renames: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return {"format": FORMAT,
            "journals": [j.record() for j in journals],
            "renames": list(renames)}


def read_state(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """The state file's records ({"journals": [], "renames": []} when
    missing or unreadable)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        doc = None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return {"journals": [], "renames": []}
    # (a hand-edited or truncated file: a key that is not a list, a
    # record that is not an object — dropped, never a traceback)
    return {k: [r for r in (doc.get(k) if isinstance(doc.get(k), list)
                            else []) if isinstance(r, dict)]
            for k in ("journals", "renames")}


def rename_records(book_rows: Iterable[Any], declared: Iterable[Any],
                   mapping: Optional[Dict[str, str]] = None
                   ) -> List[Dict[str, Any]]:
    """One record per rename event the books carry (lib/renames.
    rename_events over every account's rows: the SPLIT rows with a new
    symbol), with its machine source and where it was declared; plus each
    declaration (`declared`: renames.DatedRename) no account's books
    carried (accounts [], status "unused"). `mapping`: the base stage's
    undated renames (the books' symbols are the mapped ones)."""
    from taxjson.bin.taxjson_ticker_map import map_symbol
    from taxjson.lib.renames import (SOURCE_MAP, WINDOW_DAYS, _days,
                                     rename_events)
    mapping = mapping or {}
    declared = list(declared)
    out = []
    used = set()
    for e in rename_events(book_rows):
        decl = [dr for dr in declared
                if map_symbol(dr.old, mapping) == e["old"]
                and map_symbol(dr.new, mapping) == e["new"]
                and (_days(dr.date, e["date"]) or 0) <= WINDOW_DAYS]
        for dr in decl:
            used.add(id(dr))
        ids = list(e.get("source_ids") or [])
        src = next((s for s in ("tt", "map", "ib-conid", "broker")
                    if s in ids), ids[0] if ids else "broker")
        rec = {"date": e["date"], "old": e["old"], "new": e["new"],
               "late": next((dr.late for dr in decl if dr.late), ""),
               "source": src, "sources": ids,
               "where": [w for dr in decl
                         for w in (dr.where,) + tuple(dr.also)],
               "accounts": list(e["accounts"]),
               "status": "booked"}
        lates = {a: v for dr in decl for a, v in dr.lates}
        if lates:
            rec["lates"] = lates        # each declaring account's own
        out.append(rec)
    for dr in declared:
        if id(dr) in used:
            continue
        rec = {"date": dr.date, "old": dr.old, "new": dr.new,
               "late": dr.late, "source": dr.source or SOURCE_MAP,
               "sources": [dr.source or SOURCE_MAP],
               "where": [dr.where] + list(dr.also), "accounts": [],
               "status": "unused"}
        if dr.lates:
            rec["lates"] = dict(dr.lates)
        out.append(rec)
    out.sort(key=lambda r: (r["date"], r["old"], r["new"]))
    return out


# ------------------------------------------------------------ migration

RENAMES_TT = "renames.tt"
RENAMES_TT_HEAD = (
    "# Ticker changes: dated events, one line each, date first:\n"
    "#   RENAME <date> <OLD> <NEW> [late=fold|late=separate]\n"
    "# A line here applies to every account of this account's kind\n"
    "# (securities, or crypto in a crypto account) whose books hold OLD\n"
    "# before the date.\n")


def _book_symbols(root: Path, acct: str) -> Optional[set]:
    """The symbols an account's books name (work/<acct>_base.json: each
    row's symbol, a rename row's new symbol, an option's underlying);
    None when the account has no books yet."""
    from taxjson.lib.renames import _option_root, rename_target
    p = Path(root) / "work" / f"{acct}_base.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    rows = doc.get("transactions") if isinstance(doc, dict) else doc
    out: set = set()
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or "").upper()
        out.add(sym)
        root_ = _option_root(sym)
        if root_:
            out.add(root_.upper())
        if rename_target(r):
            out.add(rename_target(r).upper())
    return out


def _carries(root: Path, acct: str, old: str, new: str, date: str) -> bool:
    """The account's books carry the change: a SPLIT row renaming OLD (or
    to NEW) within renames.WINDOW_DAYS of the date."""
    from taxjson.lib.renames import WINDOW_DAYS, _days, rename_target
    p = Path(root) / "work" / f"{acct}_base.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return False
    rows = doc.get("transactions") if isinstance(doc, dict) else doc
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not rename_target(r):
            continue
        gap = _days(str(r.get("date") or ""), date)
        if gap is not None and gap <= WINDOW_DAYS and (
                str(r.get("symbol") or "").upper() == old
                or rename_target(r).upper() == new):
            return True
    return False


def _disagrees(declared: Iterable[Any], acct: str, old: str, new: str,
               date: str, late: str) -> bool:
    """`acct`'s own .tt lines declare the change OLD -> NEW (within
    renames.WINDOW_DAYS of `date`) with another late= than `late`: a
    moved line there would make it declare both."""
    from taxjson.lib.renames import WINDOW_DAYS, _days
    if not late:
        return False
    for d in declared:
        gap = _days(d.date, date)
        if (d.account == acct and (d.old, d.new) == (old, new)
                and gap is not None and gap <= WINDOW_DAYS
                and d.late and d.late != late):
            return True
    return False


def home_accounts(root: Path, accounts: Dict[str, Any], old: str,
                  new: str, date: str, late: str = "",
                  declared: Iterable[Any] = ()) -> List[Tuple[str, str]]:
    """[(account, why)] of the .tt file(s) a migrated ticker.map RENAME
    goes to: one per account KIND whose books name OLD or NEW (a .tt
    RENAME applies to its own kind only — securities or crypto; a legacy
    map line applied to both); the securities kind when no account's
    books name them (or no account has books yet), crypto only in a
    project with no securities account. Within a kind (taxjson.toml
    order): the first account whose books carry the change (a SPLIT row
    renaming OLD within renames.WINDOW_DAYS of the date), else the first
    whose books name OLD or NEW, else the kind's only account, else its
    first taxable account, else its first account — among the accounts
    whose own .tt lines (`declared`) agree with the moved line's `late`
    when there is one (a home that declares the change with the other
    late= would declare both: second pre-release review, 9)."""
    from taxjson.lib.renames import KIND_CRYPTO, KIND_SECURITIES
    names = list(accounts or {})
    kind = {a: account_kind(accounts.get(a)) for a in names}
    touch = {}
    for a in names:
        syms = _book_symbols(root, a)
        touch[a] = bool(syms) and (old in syms or new in syms)
    kinds = [k for k in (KIND_SECURITIES, KIND_CRYPTO)
             if any(touch[a] and kind[a] == k for a in names)]
    if not kinds:
        kinds = [KIND_SECURITIES if any(kind[a] == KIND_SECURITIES
                                        for a in names) else KIND_CRYPTO]
    out = []
    declared = list(declared)
    for k in kinds:
        mine = [a for a in names if kind[a] == k]
        if not mine:
            continue
        agree = [a for a in mine
                 if not _disagrees(declared, a, old, new, date, late)]
        mine = agree or mine
        pick = next(((a, "its books carry the change") for a in mine
                     if _carries(root, a, old, new, date)), None) \
            or next(((a, f"its books hold {old} or {new}") for a in mine
                     if touch[a]), None)
        if pick is None and len(mine) == 1:
            pick = (mine[0], "the project's only account"
                    if len(names) == 1 else f"the only {k} account")
        if pick is None:
            pick = next(((a, f"no account's books carry it yet: the "
                          f"first taxable {k} account") for a in mine
                         if (accounts.get(a) or {}).get("type")
                         == "taxable"), None) \
                or (mine[0], f"the first {k} account")
        out.append(pick)
    return out


def home_account(root: Path, accounts: Dict[str, Any], old: str, new: str,
                 date: str) -> Tuple[str, str]:
    """(account, why): the first of home_accounts ('' when the project
    has no account)."""
    homes = home_accounts(root, accounts, old, new, date)
    return homes[0] if homes else ("", "the first account")


@dataclass
class MigrationPlan:
    """What `taxjson format-map --write` adds to which .tt file, and
    whether the books stay the same (plan_migration)."""
    # {inputs/<acct>/renames.tt: [blocks]}: each block a moved line's
    # comments and line, in order.
    adds: Dict[str, List[List[str]]] = field(default_factory=dict)
    homes: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    # A moved line and an existing .tt line of the same change that
    # differ (date, late=): booked as one event — to reconcile.
    twins: List[str] = field(default_factory=list)
    # Why the migration would change the books (nothing is written).
    refused: List[str] = field(default_factory=list)


def _view(events: Sequence[Any], accounts: Dict[str, Any],
          syms: Dict[str, Optional[set]],
          held: Optional[Any] = None) -> set:
    """What the books of each account get from the rename events: (account,
    OLD, NEW, date, the account's late=) for each event that applies to
    the account's kind and names a symbol its books hold (every event,
    for an account without books). The late= is the one the run applies
    (DatedRename.late_for): the event's reaches an account without a
    line of its own only when it `held` OLD before the date — held(acct,
    event), its books carrying the change (_carries); an account without
    books is taken as holding."""
    out = set()
    for acct in accounts:
        kind = account_kind(accounts.get(acct))
        have = syms.get(acct)
        for e in events:
            if not e.applies_to(kind):
                continue
            if have is not None and e.old not in have and e.new not in have:
                continue
            h = True if held is None or have is None else held(acct, e)
            out.add((acct, e.old, e.new, e.date, e.late_for(acct, h)))
    return out


def plan_migration(root: Path, accounts: Dict[str, Any], map_text: str,
                   moved: Sequence[Any]) -> MigrationPlan:
    """Where each dated RENAME line `format_map(migrate=True)` moves out of
    the map goes (home_accounts), and a simulation of the run: the
    rename events the project books now (its .tt lines plus the map's
    dated lines, resolve_renames) against the ones it would book after
    the move (the .tt lines plus the moved ones). Any difference — an
    account's late= choice, a date, a contradiction the move creates —
    refuses the migration (MigrationPlan.refused names the lines);
    accounts are compared on the events naming a symbol their books hold
    (work/<acct>_base.json; an account without books on every event that
    still reaches it after the move — a legacy map line that applied to
    both kinds no longer reaches an account of the other kind, and with
    no books there is nothing to change)."""
    from taxjson.bin.taxjson_ticker_map import _parse_map_text
    from taxjson.lib.renames import DatedRename, SOURCE_TT
    plan = MigrationPlan()
    root = Path(root)
    try:
        decl = read_declarations(root, accounts)
    except DatedEventError as e:
        plan.refused = [f"a .tt dated-event line cannot be booked: {m}"
                        for m in str(e).splitlines()]
        return plan
    tmap = _parse_map_text(map_text, "ticker.map")[0]
    mapped = list(tmap.dated or ())
    before, _n, bad = resolve_renames(decl.tt_declared, mapped)
    if bad:
        plan.refused = [f"the project's dated renames contradict each "
                        f"other (`taxjson run` stops on them): {m}"
                        for m in bad]
        return plan
    have = {(d.old, d.new, d.date, d.late, d.kind): d.where
            for d in decl.tt_declared}
    added: List[Any] = []
    syms = {a: _book_symbols(root, a) for a in accounts}
    _held: Dict[Tuple[str, str, str, str], bool] = {}

    def held(acct: str, e: Any) -> bool:
        k = (acct, e.old, e.new, e.date)
        if k not in _held:
            _held[k] = _carries(root, acct, e.old, e.new, e.date)
        return _held[k]
    before_v = _view(before, accounts, syms, held)

    def moved_rename(mv: Any, acct: str, kind: str) -> Any:
        rel = f"inputs/{acct}/{RENAMES_TT}"
        return DatedRename(mv.old, mv.new, mv.date, mv.late,
                           f"{rel} (moved from ticker.map)",
                           mv.tt_line.split("  #")[0], source=SOURCE_TT,
                           account=acct, kind=kind)

    def keeps(mv: Any, nd: Any) -> bool:
        """Every account's view of this change is the same after the
        move with the line in `nd`'s account (None: no line added — a
        home that already has it), the lines moved so far included."""
        after, _n2, bad2 = resolve_renames(
            list(decl.tt_declared) + added + ([nd] if nd else []), [])
        if bad2:
            return False
        pair = (mv.old, mv.new)
        return ({x for x in _view(after, accounts, syms, held)
                 if x[1:3] == pair}
                == {x for x in before_v if x[1:3] == pair})
    for mv in moved:
        homes = home_accounts(root, accounts, mv.old, mv.new, mv.date,
                              "" if mv.commented else mv.late,
                              decl.tt_declared)
        if not homes:
            continue
        for n, (acct, why) in enumerate(homes):
            kind = account_kind(accounts.get(acct))
            key = (mv.old, mv.new, mv.date, mv.late, kind)
            dup = not mv.commented and key in have
            if not mv.commented and not keeps(
                    mv, None if dup else moved_rename(mv, acct, kind)):
                # Prefer a home whose line keeps every account's late=
                # resolution (an account relying on the map line's
                # event-level late= keeps it; v0.24.1 leftovers, 5).
                # (The home itself, when another account already has the
                # line: its own copy may be what keeps it.)
                alt = next((a for a in ([acct] if dup else [])
                            + [b for b in accounts if b != acct]
                            if account_kind(accounts.get(a)) == kind
                            and keeps(mv, moved_rename(mv, a, kind))),
                           None)
                if alt is not None:
                    if alt != acct:
                        why = ("its line keeps every account's late= "
                               "choice of this change")
                    acct, dup = alt, False
            rel = f"inputs/{acct}/{RENAMES_TT}"
            block = list(mv.comments) if n == 0 else []
            if dup:
                plan.skipped.append(f"{mv.tt_line.split('  #')[0]} "
                                    f"(already in {have[key]})")
            else:
                block.append(("# " + mv.tt_line) if mv.commented
                             else mv.tt_line)
            if block:
                plan.adds.setdefault(rel, []).append(block)
            if mv.commented or dup:
                continue
            plan.homes.append(f"{mv.old} -> {mv.new} ({mv.date}): {rel} "
                              f"({why})")
            nd = moved_rename(mv, acct, kind)
            added.append(nd)
            for d in decl.tt_declared:
                if (d.old, d.new) == (nd.old, nd.new) and \
                        _kinds_meet(d.kind, kind) and \
                        (d.date, d.late) != (nd.date, nd.late):
                    from taxjson.lib.renames import WINDOW_DAYS, _days
                    gap = _days(d.date, nd.date)
                    if gap is not None and gap <= WINDOW_DAYS:
                        plan.twins.append(
                            f"ticker.map's `RENAME {mv.old} {mv.new} "
                            f"{mv.date}{' late=' + mv.late if mv.late else ''}`"
                            f" and {_decl_text(d)} declare one change "
                            f"differently: booked as one event — keep "
                            f"one line")
    after, _n, bad = resolve_renames(list(decl.tt_declared) + added, [])
    if bad:
        plan.refused = [f"after the move: {m}" for m in bad]
        return plan
    old_v = before_v
    new_v = _view(after, accounts, syms, held)
    by_pair = {(mv.old, mv.new): mv for mv in moved if not mv.commented}
    for acct, o, nw, d, late in sorted(old_v - new_v):
        alt = sorted(x for x in new_v if x[:3] == (acct, o, nw))
        if not alt and syms.get(acct) is None:
            # An account without books, of a kind the moved line no
            # longer reaches (a legacy map line applied to both kinds):
            # nothing booked changes (second pre-release review, 9).
            continue
        now = (f"{d}" + (f" late={late}" if late else ""))
        then = ", ".join(f"{x[3]}" + (f" late={x[4]}" if x[4] else "")
                         for x in alt) or "not booked"
        mv = by_pair.get((o, nw))
        own = any(x.account == acct and (x.old, x.new) == (o, nw)
                  for x in decl.tt_declared)
        if late and mv is not None and mv.late == late and not own:
            # The account relies on the map line's event-level late=,
            # and no single home keeps it: its own line does.
            plan.refused.append(
                f"account {acct}: the ticker change {o} -> {nw} is "
                f"booked {now} now (ticker.map's late=, the account "
                f"having no line of its own) and would be {then} after "
                f"the move, wherever the line goes — add `"
                f"{mv.tt_line.split('  #')[0]}` to "
                f"inputs/{acct}/{RENAMES_TT} (or another .tt file of "
                f"{acct}), then format the map again")
            continue
        plan.refused.append(
            f"account {acct}: the ticker change {o} -> {nw} is booked "
            f"{now} now and would be {then} after the move — the map line "
            f"and the account's .tt lines of this change must say the "
            f"same (date, late=)")
    for acct, o, nw, d, late in sorted(new_v - old_v):
        if not any(x[:3] == (acct, o, nw) for x in old_v):
            plan.refused.append(
                f"account {acct}: the ticker change {o} -> {nw} on {d} "
                f"would be booked after the move and is not now")
    return plan
