"""tobase.map: the interlisted master's pairs for a Canadian project.

taxjson ships a master of Canadian shares that also trade in the United
States under the same share class (src/taxjson/data/interlisted.toml,
built by scripts/build_interlisted.py from OpenFIGI share-class FIGIs and
the Nasdaq Trader symbol directory; keyed by share-class FIGI). A
Canadian project carries its pairs as a file of its own, tobase.map,
beside ticker.map: one `TOBASE US CA` line per US listing (exchange
first, then the OTC listings of the same shares in a section of their
own) and per extra Canadian line (a fund's US-dollar units), each
marked `# master:<FIGI>`, plus `DISTINCT` lines for a Canadian
depositary receipt (CDR) the books hold whose root is a US ticker.
`taxjson init --country canada` writes it, `taxjson new-year` copies it,
`taxjson update-tobase-map` keeps it in step with the installed master.

How a run reads it (tax-logic CA-XLIST-06): with ticker.map, as if its
lines were written there, except that ticker.map wins — a tobase.map
pair is not applied when a ticker.map rule already decides one of its
listings (a TOBASE / JOURNAL / GLOBAL / DELETE / dated RENAME of
either, a TOBASE booking another listing under the one the pair would
move, a DISTINCT pair) (an Info line when the two disagree;
nothing when ticker.map pools the same pair). In a Canadian project a
TSX Venture listing and its TSX spelling (X.V, X.TO) are one listing
for the TOBASE and DISTINCT lines of both files (every parser books a
Venture line as X.TO; a hand-written X.V is the same shares). A US
project does not read tobase.map (US-XLIST-05).

An ended interlisting is never deleted: its lines carry `until=DATE`
(the pair, or with `ended=SYMBOL` that one listing), and a trade in an
ended listing after that date is a Warning: the ticker may now name
another security (`until_findings`).

Nothing here writes; `taxjson update-tobase-map --write` and `init`
write through lib/safe_write.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

TOBASE_MAP = "tobase.map"
MASTER_FILE = "interlisted.toml"
MARKER = "master:"
# The header line `taxjson update-tobase-map` and the checklist read.
STAMP = "# master-generated:"
SECTION_EXCHANGE = "## --- Exchange-listed pairs (one security, one ACB pool) ---"
SECTION_OTC = "## --- OTC listings of the same shares ---"
SECTION_DISTINCT = ("## --- Depositary receipts the books hold (two "
                    "securities) ---")
SECTIONS = (SECTION_EXCHANGE, SECTION_OTC, SECTION_DISTINCT)
_MARK_RE = re.compile(r"master:(BBG[0-9A-Z]{9})")
_UNTIL_RE = re.compile(r"\buntil=(\d{4}-\d{2}-\d{2}|unknown)\b")
_ENDED_RE = re.compile(r"\bended=(\S+)")


# ------------------------------------------------------------ the master

@dataclass
class Master:
    meta: Dict[str, Any]
    security: Dict[str, Dict[str, Any]]
    distinct: Dict[str, Dict[str, Any]]

    @property
    def generated(self) -> str:
        return str(self.meta.get("generated") or "")

    def sources_text(self) -> str:
        return "; ".join(f"{s.get('name')} (as of {s.get('as_of')})"
                         for s in self.meta.get("sources") or []
                         if isinstance(s, dict))


def master_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / MASTER_FILE


@lru_cache(maxsize=4)
def _load(path: str, stamp: Tuple[int, int]) -> Master:
    from taxjson.lib.tomlcompat import tomllib
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    return Master(dict(doc.get("meta") or {}),
                  dict(doc.get("security") or {}),
                  dict(doc.get("distinct") or {}))


def load_master(path: Optional[Path] = None) -> Master:
    """The installed master (or the one at `path`)."""
    p = Path(path) if path is not None else master_path()
    st = p.stat()
    return _load(str(p), (st.st_mtime_ns, st.st_size))


# ------------------------------------------------------------ listings

def venue_alias(sym: str) -> Optional[str]:
    """The other spelling of a TSX / TSX Venture listing (X.V <-> X.TO),
    else None. One listing in a Canadian project (module docstring)."""
    s = str(sym or "").upper()
    if s.endswith(".V") and len(s) > 2:
        return s[:-2] + ".TO"
    if s.endswith(".TO") and len(s) > 3:
        return s[:-3] + ".V"
    return None


def _spellings(sym: str) -> Set[str]:
    a = venue_alias(sym)
    return {sym, a} if a else {sym}


@dataclass(frozen=True)
class GenLine:
    """One line the master gives a project."""
    keyword: str          # TOBASE | DISTINCT
    a: str
    b: str
    figi: str
    section: str          # SECTION_*
    until: str = ""       # "" current; a date or "unknown" when ended
    ended: str = ""       # the one listing that ended ("" = the pair)

    @property
    def rule(self) -> str:
        return f"{self.keyword} {self.a} {self.b}"

    def text(self) -> str:
        tail = f"  # {MARKER}{self.figi}"
        if self.until:
            tail += f" until={self.until}"
            if self.ended:
                tail += f" ended={self.ended}"
        return self.rule + tail


def home_listing(e: Dict[str, Any]) -> str:
    """The listing a security's pairs are booked under: its first
    Canadian listing, or for an issuer the master knows is domiciled
    outside Canada (`domicile`) its first US exchange listing — so the
    books keep a US company's dividends and its T1135 status foreign."""
    ca = [str(x).upper() for x in e.get("ca") or []]
    us = [str(x).upper() for x in e.get("us") or []]
    dom = str(e.get("domicile") or "").upper()
    if dom and dom != "CA" and us:
        return us[0]
    return ca[0]


def master_lines(master: Master, books: Optional[Set[str]] = None
                 ) -> List[GenLine]:
    """Every line the master gives a project: the TOBASE pairs of each
    security (current listings and the ended ones, kept for the years
    they traded) and, when `books` (the symbols the books name) is
    given, a DISTINCT line for each depositary receipt the books hold."""
    out: List[GenLine] = []
    for sc, e in master.security.items():
        ca = [str(x).upper() for x in e.get("ca") or []]
        if not ca:
            continue
        base = home_listing(e)
        until = str(e.get("until") or "")
        seen: Set[str] = set()

        def add(sym: str, section: str, u: str = until,
                ended: str = "") -> None:
            sym = sym.upper()
            if sym == base or sym in seen:
                return
            seen.add(sym)
            out.append(GenLine("TOBASE", sym, base, sc, section, u, ended))
        for u in e.get("us") or []:
            add(str(u), SECTION_EXCHANGE)
        for c in ca:
            add(c, SECTION_EXCHANGE)
        for u in e.get("us_otc") or []:
            add(str(u), SECTION_OTC)
        for h in e.get("history") or []:
            if not isinstance(h, dict) or not h.get("listing"):
                continue
            sec = SECTION_OTC if h.get("kind") == "us_otc" else \
                SECTION_EXCHANGE
            hu = str(h.get("until") or "unknown")
            add(str(h["listing"]), sec, until or hu,
                "" if until else str(h["listing"]).upper())
    if books is not None:
        for sc, d in master.distinct.items():
            ca, us = str(d.get("ca") or "").upper(), \
                str(d.get("us") or "").upper()
            if ca and us and (_spellings(ca) & books):
                out.append(GenLine("DISTINCT", us, ca, sc, SECTION_DISTINCT,
                                   str(d.get("until") or "")))
    out.sort(key=lambda g: (SECTIONS.index(g.section), g.b, g.a))
    return out


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("taxjson")
    except Exception:                                   # noqa: BLE001
        return "unknown"


def header(master: Master) -> List[str]:
    return [
        "# tobase.map: the interlisted master's pairs for this project",
        "# (Canada). Each `TOBASE US CA` line makes two listings of one",
        "# company's same shares one security: one ACB pool, one security",
        "# for the superficial-loss rule (tax-logic CA-XLIST-06).",
        "# Lines marked `# master:<FIGI>` are kept by `taxjson",
        "# update-tobase-map`; do not edit them. To override a pair, write",
        "# a line in ticker.map (ticker.map wins: DISTINCT US CA, or your",
        "# own TOBASE). `until=` marks an interlisting that ended.",
        f"# Generated by taxjson {_version()} from interlisted.toml.",
        f"# Sources: {master.sources_text()}.",
        f"{STAMP} {master.generated}",
    ]


def render(master: Master, books: Optional[Set[str]] = None) -> str:
    """A new tobase.map: the header, then each section's lines."""
    lines = master_lines(master, books)
    out = header(master)
    for sec in SECTIONS:
        block = [g.text() for g in lines if g.section == sec]
        if block:
            out += ["", sec] + block
    return "\n".join(out) + "\n"


# ------------------------------------------------------------ reading

@dataclass
class FileLine:
    lineno: int
    raw: str
    keyword: str
    a: str
    b: str
    figi: str = ""        # the marker's FIGI ("" = the user's own line)
    until: str = ""
    ended: str = ""

    @property
    def rule(self) -> str:
        return f"{self.keyword} {self.a} {self.b}"


@dataclass
class TobaseFile:
    lines: List[FileLine]
    stamp: str            # the master-generated date it was made from
    problems: List[str]   # `tobase.map:N: why: 'line'`
    text: str = ""


def parse_tobase(text: str, name: str = TOBASE_MAP) -> TobaseFile:
    """The rule lines of a tobase.map (TOBASE / DISTINCT only), its
    master stamp, and the lines it cannot use."""
    lines: List[FileLine] = []
    problems: List[str] = []
    stamp = ""
    for n, raw in enumerate(str(text or "").splitlines(), 1):
        s = raw.strip()
        if s.startswith(STAMP):
            stamp = s[len(STAMP):].strip()
            continue
        body, _, note = raw.partition("#")
        parts = body.split()
        if not parts:
            continue
        kw = parts[0].upper()
        if kw not in ("TOBASE", "DISTINCT") or len(parts) != 3:
            problems.append(f"{name}:{n}: tobase.map holds `TOBASE FROM "
                            f"TO` and `DISTINCT A B` lines only: "
                            f"{s!r}")
            continue
        m = _MARK_RE.search(note)
        u = _UNTIL_RE.search(note)
        en = _ENDED_RE.search(note)
        lines.append(FileLine(n, raw, kw, parts[1].upper(),
                              parts[2].upper(), m.group(1) if m else "",
                              u.group(1) if u else "",
                              en.group(1).upper() if en else ""))
    return TobaseFile(lines, stamp, problems, str(text or ""))


def tobase_path(root) -> Path:
    return Path(root) / TOBASE_MAP


def read_tobase(root) -> Optional[TobaseFile]:
    p = tobase_path(root)
    if not p.is_file():
        return None
    from taxjson.lib.cli_diag import read_text_utf8
    return parse_tobase(read_text_utf8(p), p.name)


def project_country(root) -> Optional[str]:
    """The project's canonical country, None when it cannot be read."""
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.country import CountryError, settings_country
    try:
        return settings_country(_PL._settings(Path(root)))
    except CountryError:
        return None


# ------------------------------------------------------------ overlay

@dataclass
class Overlay:
    """What a run reads from tobase.map beside a ticker.map: `lines` the
    rule lines added to the map (tobase.map's applied lines, then the
    .V / .TO spellings of every TOBASE and DISTINCT line), `generated`
    the FROM symbols and DISTINCT pairs those lines add (exempt from the
    unused-rule check), `overridden` (tobase.map rule, why) the pairs
    ticker.map decides otherwise, `same` how many ticker.map already
    pools, `problems` the tobase.map lines that cannot be used."""
    lines: List[str] = field(default_factory=list)
    generated: Set[Any] = field(default_factory=set)
    applied: int = 0
    same: int = 0
    overridden: List[Tuple[str, str]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    ignored_country: str = ""

    @property
    def text(self) -> str:
        if not self.lines:
            return ""
        return ("\n# --- tobase.map (applied by taxjson; ticker.map wins) "
                "---\n" + "\n".join(self.lines) + "\n")


class _Renames:
    """The base-currency renames as an incremental table (one hop each),
    to test a line before adding it."""

    def __init__(self, raw: Dict[str, str], distinct: Iterable[frozenset]):
        self.raw = dict(raw)
        self.distinct = [tuple(sorted(p)) for p in distinct if len(p) == 2]

    def final(self, s: str) -> str:
        seen = set()
        while s in self.raw and s not in seen:
            seen.add(s)
            s = self.raw[s]
        return s

    def can_add(self, frm: str, to: str) -> bool:
        """`frm -> to` keeps the map one meaning: frm has no rule yet,
        no cycle, no DISTINCT pair pooled."""
        if frm in self.raw or frm == to:
            return False
        end = self.final(to)
        if end == frm:
            return False
        for a, b in self.distinct:
            fa, fb = self.final(a), self.final(b)
            fa = end if fa == frm else fa
            fb = end if fb == frm else fb
            if fa == fb:
                return False
        return True

    def add(self, frm: str, to: str) -> None:
        self.raw[frm] = to

    def add_distinct(self, a: str, b: str) -> bool:
        if self.final(a) == self.final(b):
            return False
        self.distinct.append(tuple(sorted((a, b))))
        return True


def _distinct_covers(pairs: Iterable[frozenset], a: str, b: str) -> bool:
    sa, sb = _spellings(a), _spellings(b)
    for p in pairs:
        if len(p) != 2:
            continue
        x, y = tuple(p)
        if (x in sa and y in sb) or (x in sb and y in sa):
            return True
    return False


def compute_overlay(ticker_text: str, tob: Optional[TobaseFile],
                    canada: bool, name: str = "ticker.map") -> Overlay:
    """The Overlay of `tob` (a parsed tobase.map, or None) over a
    ticker.map's text. `canada`: the project is Canadian (else tobase.map
    is not read and no .V / .TO spelling is added)."""
    from taxjson.bin.taxjson_ticker_map import _parse_map_text
    ov = Overlay()
    if not canada:
        if tob is not None and tob.lines:
            ov.ignored_country = "usa"
        return ov
    tm = _parse_map_text(ticker_text, name)[0]
    raw: Dict[str, str] = dict(tm.glob)
    raw.update(tm.tobase)
    raw.update(tm.journal)
    ren = _Renames(raw, tm.distinct)
    # What a ticker.map rule decides: the FROM of every rename (TOBASE,
    # JOURNAL, GLOBAL, undated RENAME), a DELETE, a dated RENAME's old
    # symbol, a DISTINCT pair — and, for a tobase.map line's FROM only,
    # a TOBASE / JOURNAL target (that symbol is the base of another
    # pairing). Another listing joining the same base is no conflict.
    decided: Dict[str, str] = {}
    targets: Dict[str, str] = {}
    for d in (tm.tobase, tm.journal):
        for f, t in d.items():
            decided.setdefault(f, f"TOBASE {f} {t}")
            targets.setdefault(t, f"TOBASE {f} {t}")
    for f, t in tm.glob.items():
        decided.setdefault(f, f"GLOBAL {f} {t}")
    for s in tm.delete:
        decided.setdefault(s, f"DELETE {s}")
    for dr in tm.dated:
        decided.setdefault(dr.old, f"RENAME {dr.old} {dr.new} {dr.date}")
    if tob is not None:
        ov.problems = list(tob.problems)
        for ln in tob.lines:
            a, b = ln.a, ln.b
            if ln.keyword == "DISTINCT":
                if _distinct_covers(tm.distinct, a, b):
                    ov.same += 1
                elif ren.add_distinct(a, b):
                    ov.lines.append(f"DISTINCT {a} {b}")
                    ov.generated.add(frozenset((a, b)))
                    ov.applied += 1
                else:
                    ov.overridden.append(
                        (ln.rule, f"ticker.map books {a} and {b} as one "
                                  f"security ({ren.final(a)})"))
                continue
            if _distinct_covers(tm.distinct, a, b):
                ov.overridden.append((ln.rule, f"ticker.map: DISTINCT "
                                               f"{a} {b}"))
                continue
            hit = next((decided[s] for s in sorted(_spellings(a)
                                                   | _spellings(b))
                        if s in decided), None)
            if hit is None:
                hit = next((targets[s] for s in sorted(_spellings(a))
                            if s in targets), None)
            if hit is not None:
                fa, fb = ren.final(a), ren.final(b)
                if fa == fb or venue_alias(fa) == fb or \
                        ren.final(venue_alias(a) or a) == fb or \
                        fa == ren.final(venue_alias(b) or b):
                    ov.same += 1
                else:
                    ov.overridden.append((ln.rule, f"ticker.map: {hit}"))
                continue
            if not ren.can_add(a, b):
                ov.overridden.append(
                    (ln.rule, f"it would contradict the map's other lines "
                              f"({a} is booked as {ren.final(a)})"))
                continue
            ren.add(a, b)
            ov.lines.append(f"TOBASE {a} {b}")
            ov.generated.add(a)
            ov.applied += 1
    # .V / .TO: one listing (Canada). Every TOBASE line's two symbols and
    # every DISTINCT pair also cover the other spelling.
    tob_pairs = list(tm.tobase.items()) + [
        tuple(x.split()[1:]) for x in ov.lines if x.startswith("TOBASE ")]
    for f, t in tob_pairs:
        for x in (f, t):
            alt = venue_alias(x)
            if alt is None or alt in ren.raw or alt == t:
                continue
            if alt in tm.delete or alt in decided and decided[alt] != \
                    decided.get(x):
                continue
            if ren.can_add(alt, t):
                ren.add(alt, t)
                ov.lines.append(f"TOBASE {alt} {t}")
                ov.generated.add(alt)
    dpairs = list(tm.distinct) + [frozenset(x.split()[1:])
                                  for x in ov.lines
                                  if x.startswith("DISTINCT ")]
    have = {frozenset(p) for p in dpairs}
    for p in dpairs:
        if len(p) != 2:
            continue
        x, y = sorted(p)
        for sx in _spellings(x):
            for sy in _spellings(y):
                q = frozenset((sx, sy))
                if q in have or sx == sy:
                    continue
                if ren.add_distinct(sx, sy):
                    have.add(q)
                    ov.lines.append(f"DISTINCT {sx} {sy}")
                    ov.generated.add(q)
    return ov


_OVERLAY_CACHE: Dict[Tuple, Overlay] = {}


def _stamp(p: Path) -> Optional[Tuple[int, int]]:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def overlay_for(map_path: Path, ticker_text: str) -> Optional[Overlay]:
    """The Overlay a project's ticker.map at `map_path` gets (its folder's
    tobase.map and country), None when it adds nothing to say or read."""
    root = Path(map_path).parent
    cfg = root / "taxjson.toml"
    tpath = tobase_path(root)
    if not cfg.is_file() and not tpath.is_file():
        return None
    key = (str(tpath), _stamp(tpath), str(cfg), _stamp(cfg),
           hash(ticker_text))
    if key in _OVERLAY_CACHE:
        return _OVERLAY_CACHE[key]
    country = project_country(root)
    try:
        tob = read_tobase(root)
    except (OSError, ValueError) as e:
        ov = Overlay(problems=[f"{TOBASE_MAP}: cannot be read: {e}"])
        _OVERLAY_CACHE[key] = ov
        return ov
    ov = compute_overlay(ticker_text, tob, country == "canada")
    if len(_OVERLAY_CACHE) > 32:
        _OVERLAY_CACHE.clear()
    _OVERLAY_CACHE[key] = ov
    if not ov.lines and not ov.problems and not ov.ignored_country:
        return None
    return ov


def map_text_with_overlay(map_path: Path) -> str:
    """A project ticker.map's text with its tobase.map overlay (the text
    the merge stages read: lib/cross_listings.effective_map_text)."""
    from taxjson.lib.cli_diag import read_text_utf8
    text = read_text_utf8(Path(map_path))
    ov = overlay_for(Path(map_path), text)
    if ov is None or not ov.text:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ov.text


# ------------------------------------------------------------ the books

def books_symbol_dates(root) -> Dict[str, List[str]]:
    """{symbol: the dates of its rows} in the project's parsed exports
    and .tt books in work/ (before ticker.map renames: the same files
    ticker_map_suggest.books_symbols reads); an option counts for its
    underlying."""
    from taxjson.bin.taxjson_run import _AUDIT_DERIVED_SUFFIXES
    from taxjson.lib.core import parse_option_underlying
    out: Dict[str, List[str]] = {}
    cache = Path(root) / "work"
    for p in sorted(cache.glob("*.json")) if cache.is_dir() else []:
        if p.name.endswith(_AUDIT_DERIVED_SUFFIXES):
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        rows = doc.get("transactions") if isinstance(doc, dict) else None
        for t in rows if isinstance(rows, list) else []:
            if not isinstance(t, dict):
                continue
            d = str(t.get("date") or "")[:10]
            for k in ("symbol", "symbol_new"):
                s = str(t.get(k) or "").upper()
                if not s:
                    continue
                und = parse_option_underlying(s)
                for x in (s, str(und).upper() if und else ""):
                    if x:
                        out.setdefault(x, []).append(d)
    return out


@dataclass
class UntilFinding:
    symbol: str           # the ended listing traded
    pair: str             # the tobase.map rule
    until: str
    dates: List[str]
    where: str            # tobase.map:N


def until_findings(root, symbol_dates: Optional[Dict[str, List[str]]]
                   = None) -> List[UntilFinding]:
    """The trades in an ended listing after its `until` date (a dated
    tobase.map line): the ticker may now name another security."""
    tob = read_tobase(root)
    if tob is None:
        return []
    dated = [ln for ln in tob.lines if ln.keyword == "TOBASE"
             and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ln.until or "")]
    if not dated:
        return []
    if symbol_dates is None:
        symbol_dates = books_symbol_dates(root)
    out: List[UntilFinding] = []
    for ln in dated:
        syms = [ln.ended] if ln.ended else [ln.a, ln.b]
        for s in syms:
            late = sorted({d for x in _spellings(s)
                           for d in symbol_dates.get(x, ()) if d > ln.until})
            if late:
                out.append(UntilFinding(s, ln.rule, ln.until, late,
                                        f"{TOBASE_MAP}:{ln.lineno}"))
    return out


def until_message(f: UntilFinding) -> Tuple[str, List[str]]:
    a, b = f.pair.split()[1:]
    other = b if f.symbol in _spellings(a) else a
    shown = ", ".join(f.dates[:3]) + (f" and {len(f.dates) - 3} more"
                                      if len(f.dates) > 3 else "")
    return (f"{f.symbol} has {len(f.dates)} row date(s) after "
            f"{f.until}, when its interlisting ended ({f.where}: "
            f"`{f.pair}`): {shown}",
            [f"The ticker may now name another security (a reused "
             f"ticker), which this line would pool with {other}. Check "
             f"the security's name on those rows. If it is another "
             f"security, add `DISTINCT {f.symbol} {other}` to ticker.map "
             f"(it keeps the two apart for every date: book the earlier "
             f"trades of the old security under a symbol of their own "
             f"with a dated `.tt` RENAME line if the year holds both)."])


# ------------------------------------------------------------ updating

@dataclass
class Plan:
    """What `taxjson update-tobase-map` would do."""
    added: List[GenLine] = field(default_factory=list)
    ended: List[Tuple[FileLine, GenLine]] = field(default_factory=list)
    retracted: List[FileLine] = field(default_factory=list)
    edited: List[FileLine] = field(default_factory=list)
    renamed: List[Tuple[GenLine, GenLine]] = field(default_factory=list)
    conflicts: List[Tuple[str, str]] = field(default_factory=list)
    new_text: str = ""
    old_stamp: str = ""
    new_stamp: str = ""

    @property
    def changes(self) -> bool:
        return bool(self.added or self.ended or self.retracted)


def _entry_listings(e: Dict[str, Any]) -> Set[str]:
    out = {str(x).upper() for k in ("ca", "us", "us_otc")
           for x in e.get(k) or []}
    for h in e.get("history") or []:
        if isinstance(h, dict) and h.get("listing"):
            out.add(str(h["listing"]).upper())
    return out


def plan_update(master: Master, tob: Optional[TobaseFile],
                books: Set[str], ticker_text: str) -> Plan:
    """The changes that bring a tobase.map (None: none yet) in step with
    `master`: lines added, lines whose interlisting ended (annotated,
    kept), unedited marked lines the master no longer gives (removed),
    marked lines the user edited (left), US ticker changes (the new
    line added beside the old, with the dated RENAME event to write),
    and the pairs ticker.map decides otherwise (reported only)."""
    want = master_lines(master, books)
    by_rule: Dict[Tuple[str, str], GenLine] = {(g.figi, g.rule): g
                                               for g in want}
    plan = Plan(old_stamp=tob.stamp if tob else "",
                new_stamp=master.generated)
    have: Dict[Tuple[str, str], FileLine] = {}
    keep: List[str] = []
    if tob is not None:
        for ln in tob.lines:
            if ln.figi:
                have[(ln.figi, ln.rule)] = ln
    for g in want:
        if (g.figi, g.rule) not in have:
            # A DISTINCT line the user wrote (unmarked) for the same pair.
            if tob is not None and any(
                    not ln.figi and ln.rule == g.rule for ln in tob.lines):
                continue
            plan.added.append(g)
    if tob is not None:
        for ln in tob.lines:
            if not ln.figi:
                keep.append(ln.raw)
                continue
            g = by_rule.get((ln.figi, ln.rule))
            if g is not None:
                if g.until and g.until != ln.until:
                    plan.ended.append((ln, g))
                continue
            e = master.security.get(ln.figi) or master.distinct.get(ln.figi)
            ok = e is not None and (
                {ln.a, ln.b} <= _entry_listings(e) if ln.figi in
                master.security else
                {ln.a, ln.b} <= {str(e.get("ca") or "").upper(),
                                 str(e.get("us") or "").upper()})
            if ok and ln.keyword == "DISTINCT":
                # The books no longer hold the receipt: kept (a past
                # year's books may).
                keep.append(ln.raw)
                continue
            if ok:
                plan.retracted.append(ln)
            else:
                plan.edited.append(ln)
                keep.append(ln.raw)
    # A US ticker change: an ended listing beside a current one of the
    # same security, both added or present.
    if tob is not None:
        for g in plan.added:
            if g.until or g.keyword != "TOBASE":
                continue
            for ln in tob.lines:
                if ln.figi == g.figi and ln.b == g.b and ln.a != g.a:
                    old = by_rule.get((ln.figi, ln.rule))
                    if old is not None and old.ended == ln.a:
                        plan.renamed.append((old, g))
    ov = compute_overlay(ticker_text, parse_tobase(
        "\n".join(g.rule for g in want)), True)
    plan.conflicts = list(ov.overridden)
    # The new file: the header, the master's lines in their sections
    # (the ended ones annotated; a receipt's DISTINCT line the books no
    # longer show kept), then the user's own lines as written.
    user_rules = {ln.rule for ln in (tob.lines if tob else ())
                  if not ln.figi}
    kept_distinct = [ln.raw.strip() for ln in (tob.lines if tob else ())
                     if ln.figi and ln.keyword == "DISTINCT"
                     and ln.raw in keep]
    out = header(master)
    for sec in SECTIONS:
        block = [g.text() for g in want
                 if g.section == sec and g.rule not in user_rules]
        if sec == SECTION_DISTINCT:
            block += kept_distinct
        if block:
            out += ["", sec] + block
    own = [r.rstrip() for r in keep if r.strip() not in kept_distinct]
    if own:
        out += ["", "## --- Your own lines (kept as written) ---"] + own
    plan.new_text = "\n".join(out) + "\n"
    return plan


def books_changes(master: Master, books: Set[str], ticker_text: str
                  ) -> List[Tuple[GenLine, str]]:
    """The master's pairs that would change these books, each with how:
    both listings held ("joins two pools") or one ("books X as Y"); a
    pair ticker.map already decides changes nothing here."""
    want = [g for g in master_lines(master, books) if g.keyword == "TOBASE"]
    ov = compute_overlay(ticker_text, parse_tobase(
        "\n".join(g.rule for g in want)), True)
    applied = {tuple(x.split()[1:]) for x in ov.lines
               if x.startswith("TOBASE ")}
    out: List[Tuple[GenLine, str]] = []
    for g in want:
        if (g.a, g.b) not in applied:
            continue
        ha = bool(_spellings(g.a) & books)
        hb = bool(_spellings(g.b) & books)
        if ha and hb:
            out.append((g, f"joins {g.a} and {g.b}: one ACB pool, one "
                           f"security for the superficial-loss rule (the "
                           f"options of {g.a} are booked under {g.b})"))
        elif ha:
            out.append((g, f"books {g.a} as {g.b}, its options too (the "
                           f"books hold no {g.b} yet)"))
    return out


def today() -> str:
    return _dt.date.today().isoformat()
