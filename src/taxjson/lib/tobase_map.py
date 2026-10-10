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
TSX Venture listing and its TSX spelling (X.V, X.TO) are one listing:
a TOBASE or DISTINCT line of either file naming X.V also covers X.TO,
the spelling every parser and .tt line books a Venture line under (so
a line naming X.TO needs nothing more). A US
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
import hashlib
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
SECTION_CURRENCY = ("## --- US-dollar lines of the same TSX units (one "
                    "security) ---")
SECTION_OTC = "## --- OTC listings of the same shares ---"
SECTION_DISTINCT = ("## --- Depositary receipts the books hold (two "
                    "securities) ---")
SECTION_OWN = "## --- Your own lines (kept as written) ---"
SECTION_REMOVED = ("## --- Master lines you removed (kept out; delete a "
                   "`# removed:` line to have it back) ---")
SECTIONS = (SECTION_EXCHANGE, SECTION_CURRENCY, SECTION_OTC,
            SECTION_DISTINCT)
# The marker: `# master:<FIGI>:<h>`, <h> the short hash of the line's
# rule as generated (line_hash): a marked line whose rule no longer
# matches its hash is the user's edit. A marker without <h> (written
# before v0.27.0) is judged by what the master could have generated.
_MARK_RE = re.compile(r"master:(BBG[0-9A-Z]{9})(?::([0-9a-f]{4,12}))?")
_UNTIL_RE = re.compile(r"\buntil=(\d{4}-\d{2}-\d{2}|unknown)\b")
_ENDED_RE = re.compile(r"\bended=(\S+)")
_REUSED_RE = re.compile(r'\breused_by="([^"]*)"')
_COUNTRY_RE = re.compile(r"\bcountry=([A-Za-z]{3})\b")
# A master line the user opted out of: `# removed: master:<FIGI> RULE`
# (the writer leaves it), or the marked line commented out.
_REMOVED_RE = re.compile(r"^#\s*removed:\s*master:(BBG[0-9A-Z]{9})"
                         r"(?::\S*)?\s+(TOBASE|DISTINCT)\s+(\S+)\s+(\S+)",
                         re.I)
_COMMENTED_RE = re.compile(r"^#\s*(TOBASE|DISTINCT)\s+(\S+)\s+(\S+)\s+#"
                           r"\s*master:(BBG[0-9A-Z]{9})", re.I)


def line_hash(rule: str) -> str:
    """The short hash a marker carries of its line's rule as generated
    (`TOBASE FROM TO`, upper-cased, single spaces)."""
    norm = " ".join(str(rule).upper().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:6]


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


def _book_spellings(sym: str) -> Set[str]:
    """`sym` and, for a Venture spelling X.V, the X.TO the books carry."""
    a = venue_alias(sym) if str(sym).upper().endswith(".V") else None
    return {sym, a} if a else {sym}


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
    reused: str = ""      # the security the ended ticker names today
    country: str = ""     # the issuer's country (ISO alpha-3), not Canada

    @property
    def rule(self) -> str:
        return f"{self.keyword} {self.a} {self.b}"

    def text(self) -> str:
        tail = f"  # {MARKER}{self.figi}:{line_hash(self.rule)}"
        if self.until:
            tail += f" until={self.until}"
            if self.ended:
                tail += f" ended={self.ended}"
        if self.reused:
            tail += ' reused_by="' + self.reused.replace('"', "") + '"'
        if self.country:
            tail += f" country={self.country}"
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


def issuer_country(e: Dict[str, Any]) -> str:
    """The ISO alpha-3 code of an entry's `domicile` when it is not
    Canada ("" when unknown or Canadian): tobase.map's `country=`, the
    T1135 country of the pooled symbol (t1135_countries)."""
    from taxjson.lib.t1135_country import iso3_of
    dom = str(e.get("domicile") or "").upper()
    if not dom or dom in ("CA", "CAN"):
        return ""
    return iso3_of(dom) or ""


def retracted_listings(e: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """{listing: why} of an entry's `retracted` list."""
    out: Dict[str, str] = {}
    for r in (e or {}).get("retracted") or []:
        if isinstance(r, dict) and r.get("listing"):
            out[str(r["listing"]).upper()] = str(r.get("reason") or
                                                 "a correction")
    return out


def master_lines(master: Master, books: Optional[Set[str]] = None
                 ) -> List[GenLine]:
    """Every line the master gives a project: the TOBASE pairs of each
    security (current listings and the ended ones, kept for the years
    they traded; never a retracted listing) and, when `books` (the
    symbols the books name) is given, a DISTINCT line for each
    depositary receipt the books hold."""
    out: List[GenLine] = []
    for sc, e in master.security.items():
        ca = [str(x).upper() for x in e.get("ca") or []]
        if not ca:
            continue
        base = home_listing(e)
        until = str(e.get("until") or "")
        country = issuer_country(e)
        seen: Set[str] = set(retracted_listings(e))

        def add(sym: str, section: str, u: str = until,
                ended: str = "", reused: str = "") -> None:
            sym = sym.upper()
            if sym == base or sym in seen:
                return
            seen.add(sym)
            out.append(GenLine("TOBASE", sym, base, sc, section, u, ended,
                               reused, country))
        for u in e.get("us") or []:
            add(str(u), SECTION_EXCHANGE)
        for c in ca:
            # A second Canadian line (a fund's X.U, US dollars) of a
            # Canadian-booked security: the same units.
            add(c, SECTION_CURRENCY if base.endswith(".TO")
                else SECTION_EXCHANGE)
        for u in e.get("us_otc") or []:
            add(str(u), SECTION_OTC)
        for h in e.get("history") or []:
            if not isinstance(h, dict) or not h.get("listing"):
                continue
            sec = SECTION_OTC if h.get("kind") == "us_otc" else \
                SECTION_EXCHANGE
            hu = str(h.get("until") or "unknown")
            add(str(h["listing"]), sec, until or hu,
                "" if until else str(h["listing"]).upper(),
                str(h.get("reused_by") or ""))
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
        "# Lines marked `# master:<FIGI>:<check>` are kept by `taxjson",
        "# update-tobase-map`. To override a pair, write a line in",
        "# ticker.map (ticker.map wins: DISTINCT US CA, or your own",
        "# TOBASE); a line you edit here is yours (kept as written); to",
        "# opt out of one, delete it (the update records it as `#",
        "# removed:` and never adds it back). `until=` marks an",
        "# interlisting that ended; `country=` the issuer's country when",
        "# not Canada (its T1135 country).",
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
    hash: str = ""        # the marker's check (line_hash); "" = old marker
    reused: str = ""
    country: str = ""
    note: str = ""        # the user's own words after the marker
    comments: List[str] = field(default_factory=list)  # lines above it

    @property
    def rule(self) -> str:
        return f"{self.keyword} {self.a} {self.b}"


@dataclass
class TobaseFile:
    lines: List[FileLine]
    stamp: str            # the master-generated date it was made from
    problems: List[str]   # `tobase.map:N: why: 'line'`
    text: str = ""
    # (FIGI, rule) -> the line: master lines the user opted out of (a
    # `# removed:` line, or the marked line commented out).
    optouts: Dict[Tuple[str, str], str] = field(default_factory=dict)
    # Lines that are not TOBASE / DISTINCT (kept as written; the run
    # refuses them: problems), and the user's comment lines after the
    # last rule line.
    others: List[Tuple[int, str]] = field(default_factory=list)
    tail_comments: List[str] = field(default_factory=list)


def _note_rest(note: str) -> str:
    """A marked line's comment without the marker and the attributes
    taxjson writes: the user's own words, if any."""
    rest = _MARK_RE.sub("", note, count=1)
    for rx in (_UNTIL_RE, _ENDED_RE, _REUSED_RE, _COUNTRY_RE):
        rest = rx.sub("", rest)
    return " ".join(rest.split())


def parse_tobase(text: str, name: str = TOBASE_MAP) -> TobaseFile:
    """The rule lines of a tobase.map (TOBASE / DISTINCT only), its
    master stamp, the lines it cannot use, the master lines the user
    opted out of, and the user's comment lines (each kept with the rule
    line below it). The header (the comment block down to the stamp)
    and the section titles are taxjson's, not kept."""
    lines: List[FileLine] = []
    problems: List[str] = []
    optouts: Dict[Tuple[str, str], str] = {}
    others: List[Tuple[int, str]] = []
    stamp = ""
    rows = str(text or "").splitlines()
    head_end = next((i for i, r in enumerate(rows)
                     if r.strip().startswith(STAMP)), -1)
    pending: List[str] = []
    for n, raw in enumerate(rows, 1):
        s = raw.strip()
        if s.startswith(STAMP):
            stamp = s[len(STAMP):].strip()
            continue
        if n - 1 < head_end:
            continue
        if not s:
            continue
        if s.startswith("#"):
            m = _REMOVED_RE.match(s)
            if m:
                rule = f"{m.group(2).upper()} {m.group(3).upper()} " \
                       f"{m.group(4).upper()}"
                optouts[(m.group(1), rule)] = s
                continue
            m = _COMMENTED_RE.match(s)
            if m:
                rule = f"{m.group(1).upper()} {m.group(2).upper()} " \
                       f"{m.group(3).upper()}"
                optouts[(m.group(4), rule)] = s
                continue
            if s.startswith("## --- "):
                continue        # a section title (taxjson's)
            pending.append(raw.rstrip())
            continue
        body, _, note = raw.partition("#")
        parts = body.split()
        kw = parts[0].upper()
        if kw not in ("TOBASE", "DISTINCT") or len(parts) != 3:
            problems.append(f"{name}:{n}: tobase.map holds `TOBASE FROM "
                            f"TO` and `DISTINCT A B` lines only: "
                            f"{s!r}")
            others.extend((n, c) for c in pending)
            pending = []
            others.append((n, raw.rstrip()))
            continue
        m = _MARK_RE.search(note)
        u = _UNTIL_RE.search(note)
        en = _ENDED_RE.search(note)
        ru = _REUSED_RE.search(note)
        co = _COUNTRY_RE.search(note)
        lines.append(FileLine(
            n, raw, kw, parts[1].upper(), parts[2].upper(),
            m.group(1) if m else "", u.group(1) if u else "",
            en.group(1).upper() if en else "",
            (m.group(2) or "") if m else "", ru.group(1) if ru else "",
            co.group(1).upper() if co else "",
            _note_rest(note) if m else "", pending))
        pending = []
    return TobaseFile(lines, stamp, problems, str(text or ""), optouts,
                      others, pending)


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
    ticker.map decides otherwise, `internal` (rule, why) the tobase.map
    lines another tobase.map line contradicts (an edited line), `same`
    how many ticker.map already pools, `problems` the tobase.map lines
    that cannot be used, `countries` {symbol: ISO alpha-3} the issuer
    country of each pooled symbol a line's `country=` names (the T1135
    country: t1135_countries), `final` the base each symbol ends at."""
    lines: List[str] = field(default_factory=list)
    generated: Set[Any] = field(default_factory=set)
    applied: int = 0
    same: int = 0
    overridden: List[Tuple[str, str]] = field(default_factory=list)
    internal: List[Tuple[str, str]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    ignored_country: str = ""
    countries: Dict[str, str] = field(default_factory=dict)
    renames: Dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if not self.lines:
            return ""
        return ("\n" + OVERLAY_HEAD + "\n" + "\n".join(self.lines) + "\n")

    def final(self, sym: str) -> str:
        """The symbol `sym` is booked as (ticker.map and the applied
        tobase.map lines, one hop at a time)."""
        seen = set()
        while sym in self.renames and sym not in seen:
            seen.add(sym)
            sym = self.renames[sym]
        return sym


# The overlay's head in a map text (Overlay.text): what follows, down to
# the first blank line, is tobase.map's (own_map_text drops it).
OVERLAY_HEAD = "# --- tobase.map (applied by taxjson; ticker.map wins) ---"


def own_map_text(text: str) -> str:
    """A map text (a ticker.map read with its overlay, or the run's
    effective map) without the tobase.map section: the lines the user
    and the books gave (lib/map_hygiene's sightings)."""
    out: List[str] = []
    skip = False
    for ln in str(text or "").splitlines():
        if ln.strip() == OVERLAY_HEAD:
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        if not skip:
            out.append(ln)
    return "\n".join(out) + ("\n" if out else "")


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
    base_ren = _Renames(raw, tm.distinct)
    countries: List[Tuple[str, str, str]] = []
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
                    if ln.country:
                        countries.append((a, b, ln.country))
                else:
                    ov.overridden.append((ln.rule, f"ticker.map: {hit}"))
                continue
            if not ren.can_add(a, b):
                if base_ren.can_add(a, b):
                    # ticker.map alone allows it: another tobase.map
                    # line (an edited one) decides otherwise.
                    ov.internal.append(
                        (ln.rule, f"another {TOBASE_MAP} line already "
                                  f"books {a} as {ren.final(a)}"))
                else:
                    ov.overridden.append(
                        (ln.rule, f"it would contradict ticker.map's "
                                  f"lines ({a} is booked as "
                                  f"{ren.final(a)})"))
                continue
            ren.add(a, b)
            ov.lines.append(f"TOBASE {a} {b}")
            ov.generated.add(a)
            ov.applied += 1
            if ln.country:
                countries.append((a, b, ln.country))
    # .V / .TO: one listing (Canada). A TOBASE or DISTINCT line naming a
    # Venture spelling X.V also covers X.TO, the spelling the books carry
    # (every parser and .tt line books a Venture line in CAD as X.TO, so
    # the books never hold X.V: a line naming X.TO needs nothing more).
    tob_pairs = list(tm.tobase.items()) + [
        tuple(x.split()[1:]) for x in ov.lines if x.startswith("TOBASE ")]
    for f, t in tob_pairs:
        for x in (f, t):
            alt = venue_alias(x) if x.endswith(".V") else None
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
        for sx in _book_spellings(x):
            for sy in _book_spellings(y):
                q = frozenset((sx, sy))
                if q in have or sx == sy:
                    continue
                if ren.add_distinct(sx, sy):
                    have.add(q)
                    ov.lines.append(f"DISTINCT {sx} {sy}")
                    ov.generated.add(q)
    ov.renames = dict(ren.raw)
    # The issuer's country (`country=`) for the symbols the line pools,
    # as the books carry them after the renames (the base) and before.
    for a, b, c in countries:
        for x in {a, b, ov.final(a), ov.final(b)}:
            ov.countries.setdefault(x, c)
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
    if not ov.lines and not ov.problems and not ov.ignored_country \
            and not ov.countries:
        return None
    return ov


def t1135_countries(map_path: Path) -> Dict[str, str]:
    """{symbol: ISO alpha-3} the issuer country tobase.map's applied
    lines give the symbols they pool (`country=`, the master's
    `domicile` of an issuer domiciled outside Canada): the T1135 country
    of a foreign issuer booked under its US listing (BMU, not the .US
    suffix's USA). A ticker.map `T1135` line overrides it."""
    from taxjson.lib.cli_diag import read_text_utf8
    p = Path(map_path)
    if p.name != "ticker.map" or not p.is_file():
        return {}
    try:
        ov = overlay_for(p, read_text_utf8(p))
    except (OSError, ValueError):
        return {}
    return dict(ov.countries) if ov is not None else {}


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
    reused: str = ""      # the security the ticker names today, if known


def until_findings(root, symbol_dates: Optional[Dict[str, List[str]]]
                   = None) -> List[UntilFinding]:
    """The trades in an ended listing after its `until` date (a dated
    tobase.map line): the ticker may now name another security. A line
    marked `reused_by=` (the master knows the ticker names another
    security today) names EVERY row of the ended listing, whatever its
    date: a reused ticker in the books may be either company."""
    tob = read_tobase(root)
    if tob is None:
        return []
    dated = [ln for ln in tob.lines if ln.keyword == "TOBASE"
             and (re.fullmatch(r"\d{4}-\d{2}-\d{2}", ln.until or "")
                  or ln.reused)]
    if not dated:
        return []
    if symbol_dates is None:
        symbol_dates = books_symbol_dates(root)
    out: List[UntilFinding] = []
    for ln in dated:
        syms = [ln.ended] if ln.ended else [ln.a, ln.b]
        if ln.reused and not ln.ended:
            syms = [ln.a]
        for s in syms:
            late = sorted({d for x in _spellings(s)
                           for d in symbol_dates.get(x, ())
                           if ln.reused or d > ln.until})
            if late:
                out.append(UntilFinding(s, ln.rule, ln.until, late,
                                        f"{TOBASE_MAP}:{ln.lineno}",
                                        ln.reused))
    return out


def until_message(f: UntilFinding) -> Tuple[str, List[str]]:
    a, b = f.pair.split()[1:]
    other = b if f.symbol in _spellings(a) else a
    shown = ", ".join(f.dates[:3]) + (f" and {len(f.dates) - 3} more"
                                      if len(f.dates) > 3 else "")
    fix = (f"If it is another security, add `DISTINCT {f.symbol} {other}` "
           f"to ticker.map (it keeps the two apart for every date: book "
           f"the earlier trades of the old security under a symbol of "
           f"their own with a dated `.tt` RENAME line if the year holds "
           f"both).")
    if f.reused:
        when = (f" (the interlisting ended {f.until})"
                if f.until and f.until != "unknown" else "")
        return (f"{f.symbol} has {len(f.dates)} row date(s) in the books "
                f"and the ticker is reused: today it names {f.reused} "
                f"({f.where}: `{f.pair}`{when}): {shown}",
                [f"This line pools every {f.symbol} row with {other}, "
                 f"but a row may be {f.reused}'s. Check the security's "
                 f"name on each row. " + fix])
    return (f"{f.symbol} has {len(f.dates)} row date(s) after "
            f"{f.until}, when its interlisting ended ({f.where}: "
            f"`{f.pair}`): {shown}",
            [f"The ticker may now name another security (a reused "
             f"ticker), which this line would pool with {other}. Check "
             f"the security's name on those rows. " + fix])


# ------------------------------------------------------------ updating

@dataclass
class Plan:
    """What `taxjson update-tobase-map` would do."""
    added: List[GenLine] = field(default_factory=list)
    ended: List[Tuple[FileLine, GenLine]] = field(default_factory=list)
    # (line, why): unedited marked lines removed — a listing the master
    # retracted (why: its reason) or a pair it no longer gives.
    retracted: List[Tuple[FileLine, str]] = field(default_factory=list)
    # (line, why): an edited line naming a retracted listing (kept).
    retracted_edited: List[Tuple[FileLine, str]] = field(
        default_factory=list)
    edited: List[FileLine] = field(default_factory=list)
    # (master line, the edited line that stands in for it)
    suppressed: List[Tuple[GenLine, FileLine]] = field(default_factory=list)
    unknown: List[FileLine] = field(default_factory=list)
    # (old line, new line, pooled): a US ticker change; pooled = both
    # are booked as the same symbol already (nothing more to write).
    renamed: List[Tuple[GenLine, GenLine, bool]] = field(
        default_factory=list)
    conflicts: List[Tuple[str, str]] = field(default_factory=list)
    # Master lines the user opted out of: already recorded, and newly
    # found deleted (recorded as `# removed:` by --write).
    opted_out: List[GenLine] = field(default_factory=list)
    removed_now: List[GenLine] = field(default_factory=list)
    others: List[str] = field(default_factory=list)
    new_text: str = ""
    old_stamp: str = ""
    new_stamp: str = ""

    @property
    def changes(self) -> bool:
        return bool(self.added or self.ended or self.retracted
                    or self.removed_now)


def _entry_listings(e: Dict[str, Any]) -> Set[str]:
    out = {str(x).upper() for k in ("ca", "us", "us_otc")
           for x in e.get(k) or []}
    for h in e.get("history") or []:
        if isinstance(h, dict) and h.get("listing"):
            out.add(str(h["listing"]).upper())
    return out


def could_generate(master: Master, figi: str) -> Set[str]:
    """Every rule a master could have given for `figi` (an older marker
    without a check, written before v0.27.0, is the user's edit only
    when its rule is none of these): each listing of the entry, its
    retracted ones included, booked under its first Canadian listing or
    its home listing; a receipt's DISTINCT pair."""
    e = master.security.get(figi)
    if e is not None:
        ls = _entry_listings(e) | set(retracted_listings(e))
        ca = [str(x).upper() for x in e.get("ca") or []]
        bases = {home_listing(e)} | ({ca[0]} if ca else set())
        return {f"TOBASE {x} {b}" for x in ls for b in bases if x != b}
    d = master.distinct.get(figi)
    if d is not None:
        return {f"DISTINCT {str(d.get('us') or '').upper()} "
                f"{str(d.get('ca') or '').upper()}"}
    return set()


def is_edited(ln: FileLine, master: Master) -> bool:
    """A marked line the user changed: its rule no longer matches the
    marker's check (line_hash), or for an older marker without one, a
    rule the master could not have given (could_generate)."""
    if ln.hash:
        return ln.hash != line_hash(ln.rule)
    return ln.rule not in could_generate(master, ln.figi)


def _was_given(master: Master, g: GenLine, stamp: str) -> bool:
    """The master the file was made from (its stamp) already gave `g`:
    the entry was there (first_seen) and neither listing was added
    since (the entry's `added`)."""
    if not stamp or g.keyword != "TOBASE":
        return False
    e = master.security.get(g.figi) or {}
    fs = str(e.get("first_seen") or "unknown")
    if fs == "unknown" or fs > stamp:
        # An entry of unknown age: never taken for a deletion (the line
        # is added again; commenting it out opts out for good).
        return False
    for a in e.get("added") or []:
        if isinstance(a, dict) and str(a.get("listing") or "").upper() in \
                (g.a, g.b) and str(a.get("since") or "") >= stamp:
            return False
    return True


def plan_update(master: Master, tob: Optional[TobaseFile],
                books: Set[str], ticker_text: str) -> Plan:
    """The changes that bring a tobase.map (None: none yet) in step with
    `master`.

    - Added: the master's lines the file lacks — except one the user
      edited a marked line of (the same FIGI and the same FROM, or the
      same two listings: no duplicate), one the user opted out of (a
      `# removed:` line, or the marked line commented out), and one the
      file's own master already gave that the user deleted (recorded as
      `# removed:` from now on: removed_now).
    - Ended: lines whose interlisting ended (annotated, kept).
    - Retracted: unedited marked lines naming a listing the master
      retracted (with its reason) or a pair it no longer gives (removed);
      an edited one is kept and flagged (retracted_edited).
    - Edited: marked lines the user changed (is_edited: the marker's
      check; an older marker by could_generate) — kept as written, a
      reversed direction included.
    - Renamed: a US ticker change (the new line added beside the old);
      `pooled` when both already book as one symbol.
    - Conflicts: the pairs ticker.map decides otherwise (reported).
    The user's own lines, comment lines, trailing comments on marked
    lines and lines that are not TOBASE / DISTINCT are kept."""
    want = master_lines(master, books)
    by_rule: Dict[Tuple[str, str], GenLine] = {(g.figi, g.rule): g
                                               for g in want}
    plan = Plan(old_stamp=tob.stamp if tob else "",
                new_stamp=master.generated)
    flines = list(tob.lines) if tob else []
    optouts = dict(tob.optouts) if tob else {}
    # Each kept file line -> its text in the new file ("" = regenerated
    # from the master's line).
    out_marked: Dict[int, str] = {}
    own: List[FileLine] = []
    edited_keys: Set[Tuple[str, str]] = set()
    for ln in flines:
        if not ln.figi:
            own.append(ln)
            continue
        rinfo = ""
        e = master.security.get(ln.figi)
        rl = retracted_listings(e)
        for x in (ln.a, ln.b):
            if x in rl:
                rinfo = f"{x}: {rl[x]}"
                break
        if is_edited(ln, master):
            plan.edited.append(ln)
            if rinfo:
                plan.retracted_edited.append((ln, rinfo))
            for g in want:
                if g.figi == ln.figi and (
                        g.a == ln.a or {g.a, g.b} == {ln.a, ln.b}) \
                        and g.rule != ln.rule:
                    edited_keys.add((g.figi, g.rule))
                    plan.suppressed.append((g, ln))
            own.append(ln)
            continue
        g = by_rule.get((ln.figi, ln.rule))
        if g is not None:
            if g.until and g.until != ln.until:
                plan.ended.append((ln, g))
            out_marked[ln.lineno] = ""
            continue
        if ln.keyword == "DISTINCT" and ln.figi in master.distinct and \
                ln.rule in could_generate(master, ln.figi):
            # The books no longer hold the receipt: kept (a past year's
            # books may).
            out_marked[ln.lineno] = ln.raw.rstrip()
            continue
        if rinfo:
            plan.retracted.append((ln, f"retracted ({rinfo})"))
            continue
        if e is None and ln.figi not in master.distinct:
            # A FIGI this master does not know (entries are never
            # deleted: a newer master's, or a typo): kept, reported.
            plan.unknown.append(ln)
            own.append(ln)
            continue
        plan.retracted.append((ln, "the master no longer gives it"))
    have_marked = {(ln.figi, ln.rule) for ln in flines if ln.figi}
    own_rules = {ln.rule for ln in flines if not ln.figi}
    for g in want:
        key = (g.figi, g.rule)
        if key in have_marked or key in edited_keys:
            continue
        if g.rule in own_rules:
            continue        # the user's own line for the same pair
        if key in optouts:
            plan.opted_out.append(g)
            continue
        if tob is not None and _was_given(master, g, tob.stamp) and not any(
                ln.figi == g.figi and g.a in (ln.a, ln.b) for ln in flines):
            # The file's own master gave it and it is gone: the user
            # deleted it.
            plan.removed_now.append(g)
            continue
        plan.added.append(g)
    # A US ticker change: an ended listing beside a current one of the
    # same security, both added or present.
    ov_new: Optional[Overlay] = None
    if tob is not None:
        for g in plan.added:
            if g.until or g.keyword != "TOBASE":
                continue
            for ln in flines:
                if ln.figi == g.figi and ln.b == g.b and ln.a != g.a:
                    old = by_rule.get((ln.figi, ln.rule))
                    if old is not None and old.ended == ln.a:
                        plan.renamed.append((old, g, False))
    ov = compute_overlay(ticker_text, parse_tobase(
        "\n".join(g.rule for g in want)), True)
    plan.conflicts = list(ov.overridden)
    plan.others = [r for _n, r in (tob.others if tob else ())]
    # The new file: the header, the master's lines in their sections
    # (the ended ones annotated; a receipt's DISTINCT line the books no
    # longer show kept; a kept line's comments above it and the user's
    # words after its marker kept), then the user's own lines as
    # written, the lines tobase.map cannot use, the opt-outs.
    by_key_file = {(ln.figi, ln.rule): ln for ln in flines
                   if ln.figi and ln.lineno in out_marked}
    out = header(master)
    gone_keys = {(g.figi, g.rule) for g in plan.removed_now} | \
        set(optouts) | edited_keys
    for sec in SECTIONS:
        block: List[str] = []
        for g in want:
            if g.section != sec or g.rule in own_rules or \
                    (g.figi, g.rule) in gone_keys:
                continue
            fl = by_key_file.get((g.figi, g.rule))
            if fl is not None:
                block += fl.comments
                block.append(g.text() + (f" {fl.note}" if fl.note else ""))
            else:
                block.append(g.text())
        if sec == SECTION_DISTINCT:
            for ln in flines:
                if ln.figi and out_marked.get(ln.lineno):
                    block += ln.comments
                    block.append(out_marked[ln.lineno])
        if block:
            out += ["", sec] + block
    tail = [c for ln in flines if ln.figi and ln.lineno not in out_marked
            and ln not in own for c in ln.comments]
    own_block: List[str] = []
    for ln in own:
        own_block += ln.comments
        own_block.append(ln.raw.rstrip())
    own_block += [r for _n, r in (tob.others if tob else ())]
    own_block += tail + list(tob.tail_comments if tob else ())
    if own_block:
        out += ["", SECTION_OWN] + own_block
    removed = sorted(set(optouts.values()) | {
        f"# removed: {MARKER}{g.figi} {g.rule}" for g in plan.removed_now})
    if removed:
        out += ["", SECTION_REMOVED] + removed
    plan.new_text = "\n".join(out) + "\n"
    # Whether each ticker change is already one security in the new file.
    if plan.renamed:
        ov_new = compute_overlay(ticker_text, parse_tobase(plan.new_text),
                                 True)
        plan.renamed = [(o, n, ov_new.final(o.a) == ov_new.final(n.a))
                        for o, n, _p in plan.renamed]
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

