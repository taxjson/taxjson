"""Dated events declared in an account's .tt files: JOURNAL and RENAME.

ticker.map holds standing truths about securities (GLOBAL, TOBASE,
DISTINCT, DELETE, the lookups). A journal between two listings and a
ticker change happen ON A DATE: they are events in the books, like a
trade or a dividend, written date first in a .tt file of an account
(`taxjson-convert-tt` checks the line; it is never a row of the
converted file — `taxjson run` reads it here):

  JOURNAL <date> <FROM> <TO> <qty>
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
      is booked.

  RENAME <date> <OLD> <NEW> [late=fold|late=separate]
      a ticker change on that date (lib/renames). Declared once, in any
      account's .tt file, it applies to every account whose books carry
      OLD before the date (a rename is the security's, not an
      account's), and is recorded once. The run writes it into its
      effective map (work/ticker.map.effective, with the line's place in
      a `# .tt: <where>` note) so every stage that books renames reads
      it: the SPLIT row it books carries `event_source` "tt".

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
TT_ORIGIN_RE = re.compile(r"^\s*\.tt: (.+?\.tt:\d+)(?: \| (.*?))?\s*$",
                          re.IGNORECASE)
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

    def record(self) -> Dict[str, Any]:
        out = {"date": self.date, "account": self.account,
               "from": self.frm, "to": self.to,
               "quantity": self.quantity, "source": SOURCE_TT,
               "where": self.where, "pair": self.pair,
               "status": self.status, "legs": list(self.legs)}
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
    renames: List[Any] = field(default_factory=list)   # renames.DatedRename
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
        for tt in tt_files(root / "inputs" / acct):
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
                        source_key=source_key(tt)))
                elif r is not None:
                    tt_renames.append(DatedRename(
                        r["old"], r["new"], r["date"], r["late"], where,
                        r["line"], source=SOURCE_TT))
    kept, notes, errs = _merge_renames(tt_renames, [])
    problems += errs
    out.notes += notes
    out.renames = kept
    if problems:
        raise DatedEventError("\n".join(problems))
    return out


def _merge_renames(tt: Sequence[Any], mapped: Sequence[Any]
                   ) -> Tuple[List[Any], List[str], List[str]]:
    """(the .tt renames to book, Info notes, problems): a .tt RENAME that
    repeats another declaration of the same change (the same OLD and NEW
    within renames.WINDOW_DAYS: another .tt line, a ticker.map line) is
    one event, recorded once; one that renames OLD to another symbol
    within the window contradicts it."""
    from taxjson.lib.renames import WINDOW_DAYS, _days
    kept: List[Any] = []
    notes: List[str] = []
    problems: List[str] = []
    for dr in tt:
        twin = None
        for other in list(mapped) + kept:
            gap = _days(other.date, dr.date)
            if other.old != dr.old or gap is None or gap > WINDOW_DAYS:
                continue
            if other.new != dr.new:
                problems.append(
                    f"{dr.where}: RENAME {dr.date} {dr.old} {dr.new} "
                    f"contradicts {other.where} ({other.old} -> "
                    f"{other.new} on {other.date}) — one ticker change, one "
                    f"line: {dr.line!r}")
                twin = other
                break
            if other.late and dr.late and other.late != dr.late:
                problems.append(
                    f"{dr.where}: RENAME {dr.date} {dr.old} {dr.new} "
                    f"late={dr.late} contradicts {other.where} "
                    f"(late={other.late}) — keep one line: {dr.line!r}")
            notes.append(f"{dr.where}: RENAME {dr.old} -> {dr.new} on "
                         f"{dr.date} repeats {other.where}: one event, "
                         f"booked once")
            twin = other
            break
        if twin is None:
            kept.append(dr)
    return kept, notes, problems


def check_against_map(decl: Declarations, tmap) -> List[str]:
    """Settle the .tt declarations against the project's ticker.map
    (`tmap`, None without one): a .tt RENAME the map's dated RENAME
    already declares is booked once (a note), one the map contradicts —
    another target, or an undated rule renaming OLD at every date — is a
    problem (returned). A .tt JOURNAL between two listings a DISTINCT
    line keeps apart is a Warning (decl.warnings): its legs are booked,
    the listings stay separate."""
    if tmap is None:
        return []
    kept, notes, problems = _merge_renames(decl.renames,
                                           list(tmap.dated or ()))
    for dr in kept:
        if dr.old in tmap.glob:
            problems.append(
                f"{dr.where}: RENAME {dr.date} {dr.old} {dr.new} is dated, "
                f"but ticker.map renames {dr.old} to {tmap.glob[dr.old]} at "
                f"every date (GLOBAL or an undated RENAME) — keep one of "
                f"the two: {dr.line!r}")
    decl.renames = kept
    decl.notes += notes
    for j in decl.journals:
        if frozenset((j.frm, j.to)) in (tmap.distinct or ()):
            decl.warnings.append(
                f"{j.where}: JOURNAL {j.date} {j.frm} {j.to} moves units "
                f"between two listings ticker.map keeps apart (DISTINCT "
                f"{j.frm} {j.to}): the legs are booked, the listings stay "
                f"two securities — delete one of the two lines")
    return problems


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

def effective_lines(renames: Iterable[Any]) -> List[str]:
    """The .tt RENAME declarations as lines of the run's effective map,
    each with its origin note (TT_ORIGIN_RE)."""
    out = []
    for dr in renames:
        out.append(f"RENAME {dr.old} {dr.new} {dr.date}"
                   + (f" late={dr.late}" if dr.late else "")
                   + f"  # .tt: {dr.where} | {dr.line}")
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
        from taxjson.lib.tomlcompat import tomllib
        doc = tomllib.loads((Path(root) / "taxjson.toml").read_text(
            encoding="utf-8-sig"))
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
            ["They still work (a JOURNAL line is read as TOBASE, a dated "
             "RENAME as the ticker change on its date). Dated events now "
             "go in an account's .tt file, date first (`JOURNAL <date> "
             "FROM TO <qty>`, `RENAME <date> OLD NEW`); `taxjson "
             "format-map --write` migrates the lines, and the books stay "
             "the same."])


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
    return {k: [r for r in (doc.get(k) or []) if isinstance(r, dict)]
            for k in ("journals", "renames")}


def rename_records(book_rows: Iterable[Any], declared: Iterable[Any]
                   ) -> List[Dict[str, Any]]:
    """One record per rename event the books carry (lib/renames.
    rename_events over every account's rows: the SPLIT rows with a new
    symbol), with its machine source and where it was declared; plus each
    declaration (`declared`: renames.DatedRename) no account's books
    carried (accounts [], status "unused")."""
    from taxjson.lib.renames import (SOURCE_MAP, WINDOW_DAYS, _days,
                                     rename_events)
    out = []
    used = set()
    for e in rename_events(book_rows):
        decl = [dr for dr in declared if dr.old == e["old"]
                and dr.new == e["new"]
                and (_days(dr.date, e["date"]) or 0) <= WINDOW_DAYS]
        for dr in decl:
            used.add(id(dr))
        ids = list(e.get("source_ids") or [])
        src = next((s for s in ("tt", "map", "ib-conid", "broker")
                    if s in ids), ids[0] if ids else "broker")
        out.append({"date": e["date"], "old": e["old"], "new": e["new"],
                    "late": next((dr.late for dr in decl if dr.late), ""),
                    "source": src, "sources": ids,
                    "where": [dr.where for dr in decl],
                    "accounts": list(e["accounts"]),
                    "status": "booked"})
    for dr in declared:
        if id(dr) in used:
            continue
        out.append({"date": dr.date, "old": dr.old, "new": dr.new,
                    "late": dr.late, "source": dr.source or SOURCE_MAP,
                    "sources": [dr.source or SOURCE_MAP],
                    "where": [dr.where], "accounts": [],
                    "status": "unused"})
    out.sort(key=lambda r: (r["date"], r["old"], r["new"]))
    return out


# ------------------------------------------------------------ migration

RENAMES_TT = "renames.tt"
RENAMES_TT_HEAD = (
    "# Ticker changes: dated events, one line each, date first:\n"
    "#   RENAME <date> <OLD> <NEW> [late=fold|late=separate]\n"
    "# A line here applies to every account whose books hold OLD.\n")


def home_account(root: Path, accounts: Dict[str, Any], old: str, new: str,
                 date: str) -> Tuple[str, str]:
    """(account, why) of the .tt file a migrated ticker.map RENAME goes
    to — one file: the rename applies project-wide wherever it is
    declared. The first account (taxjson.toml order) whose books carry
    the event (a SPLIT row renaming OLD within renames.WINDOW_DAYS of the
    date, in work/<acct>_base.json); else the only account; else the
    first taxable account (else the first account)."""
    from taxjson.lib.renames import WINDOW_DAYS, _days, rename_target
    names = list(accounts or {})
    cache = Path(root) / "work"
    for acct in names:
        p = cache / f"{acct}_base.json"
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        rows = doc.get("transactions") if isinstance(doc, dict) else doc
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not rename_target(r):
                continue
            gap = _days(str(r.get("date") or ""), date)
            if gap is not None and gap <= WINDOW_DAYS and (
                    str(r.get("symbol") or "").upper() == old
                    or rename_target(r).upper() == new):
                return acct, "its books carry the change"
    if len(names) == 1:
        return names[0], "the project's only account"
    for acct in names:
        if (accounts.get(acct) or {}).get("type") == "taxable":
            return acct, ("no account's books carry it yet: the first "
                          "taxable account")
    return (names[0] if names else ""), "the first account"
