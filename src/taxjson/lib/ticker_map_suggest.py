"""`taxjson ticker-map --suggest [--write]`: every ticker.map line the last
`taxjson run` suggested, gathered in one place, each with its reason, and
(with --write) appended to ticker.map.

The run's stages name a ticker.map line wherever the books need the
user's judgment; they are spread over the account messages. Sources, all
in work/ (nothing is recomputed but the cheap reads):

* work/cross_listings.state — the transfer journals between two listings
  that `taxjson run` did not join itself (lib/cross_listings: the names
  are missing or disagree, the pairing is ambiguous): `TOBASE FROM TO`
  (never for two companies); and the symbol collisions (one symbol, two
  companies): `EXTRACT words | CURRENCY | SYMBOL` with its `JOURNAL`, or
  a template with a placeholder (listed, never written);
* work/<account>_symbol_codes.state — Questrade internal codes the run
  could not resolve, with a "looks like" candidate: `GLOBAL CODE TICKER`;
* every stage's .diag — a ticker.map line a message names: IB's "one
  stock under several symbols" (a dated `RENAME OLD NEW YYYY-MM-DD`),
  Questrade's and RBC's "looks renamed" hints (`add to ticker.map:
  GLOBAL OLD NEW`), RBC's US-dollar unit hint (`EXTRACT ...`), the
  crypto price checks' `CRYPTO SYMBOL ID` lines, the gains stage's
  unmapped cross-listing journal (`TOBASE FROM TO`).

A line the map already has, or one whose symbol the map already renames
(the user's rule wins; an EXTRACT for the same currency and symbol), is
left out; of two EXTRACT lines for one listing the first is offered (the
other is listed as covered by it). A line with a placeholder
(`<number>`, OLD/NEW, `a|b`) is a template, never a suggestion.

A CONDITIONAL hint — the clause that names the line says "if" ("Only if
the position is really held under the other listing …, add to
ticker.map: TOBASE ROOT.US ROOT.TO", "add `GLOBAL A B` … if it is the
same security") — is offered only when the project's books hold every
symbol the line joins (books_symbols: the parsed exports, .tt files and
opening balances, and the holdings files taxjson.toml lists; an option
counts for its underlying). Without that evidence the hint is a guess:
RBC books a dividend on a symbol no file of the account trades under
the payment currency's listing and names the other listing, which for a
US stock does not exist. The run's own message stays; --suggest shows
nothing. An EXTRACT line creates its symbol (its evidence is the row
description the parser read) and a CRYPTO line's id is a Yahoo id: both
are offered as before.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# The keywords a suggestion may carry.
KEYWORDS = ("GLOBAL", "TOBASE", "JOURNAL", "DISTINCT", "RENAME", "CRYPTO",
            "EXTRACT")
_RENAMES = ("GLOBAL", "TOBASE", "JOURNAL", "RENAME")
_KW = "|".join(KEYWORDS)
_TICK_RE = re.compile(rf"`((?:{_KW}) [^`]+)`")
_ADD_RE = re.compile(rf"add to ticker\.map:\s+((?:{_KW})\s+\S+\s+\S+)")
_BARE_RE = re.compile(rf"^\s+((?:{_KW})\s+\S+\s+\S+)\s*$")
# `EXTRACT description words | CURRENCY | SYMBOL` (its words hold
# spaces: read on its own; a backticked one ends at the backtick).
_EXTRACT_RE = re.compile(r"\bEXTRACT\s+([^|`\n]+?)\s*\|\s*([A-Za-z*]{1,5})"
                         r"\s*\|\s*([^\s`|]+)")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_:/=^]*$")
_PLACEHOLDERS = frozenset("""
FROM TO OLD NEW SYMBOL SYM ROOT CODE A B X Y N YAHOO_ID YAHOO_SYMBOL
TICKER CUR YYYY-MM-DD COIN LISTING
""".split())
# A quoted span (a broker's description or file name, as Python's repr
# writes it: '...' or "..."): text from an export, never a message's own
# suggestion — a description "QZ `GLOBAL A B` CO" must not become a map
# line. A quote opens after a non-word character (a possessive's
# apostrophe never opens one).
_QUOTED_RE = re.compile(r"""(?<![A-Za-z0-9])(?:'(?:[^'\\\n]|\\.)*'"""
                        r"""|"(?:[^"\\\n]|\\.)*")(?![A-Za-z0-9])""")
_HEAD_RE = re.compile(r"^(?:[\w.-]+: )?(?:warning|note|error): "
                      r"(?:ATTENTION: |UNBOOKED: )?(?:NOTE: )?")


@dataclass
class Suggestion:
    line: str           # the ticker.map line, e.g. "TOBASE ABC.US ABC.TO"
    reason: str         # one sentence: where it comes from, and why
    source: str         # the work/ file it was read from
    # A line with a placeholder the user must edit (an EXTRACT whose
    # listing or words could not be derived): listed, never written.
    template: bool = False
    # The hint says "if …": offered only when the books hold every
    # symbol the line joins (`needs`; pending).
    conditional: bool = False

    @property
    def keyword(self) -> str:
        return self.line.split()[0]

    @property
    def symbols(self) -> List[str]:
        if self.keyword == "EXTRACT":
            return [self.line.rsplit("|", 1)[1].strip()]
        return self.line.split()[1:]

    @property
    def needs(self) -> List[str]:
        """The symbols of the books a conditional line joins (none for
        an EXTRACT, which creates its symbol, or a CRYPTO id)."""
        if self.keyword in ("EXTRACT", "CRYPTO"):
            return []
        return [x.upper() for x in self.symbols[:2]]

    @property
    def extract_key(self) -> Tuple[str, str]:
        """(CURRENCY, SYMBOL) of an EXTRACT line."""
        parts = [p.strip() for p in self.line.split("|")]
        return parts[1].upper(), parts[2].upper()

    def record(self) -> Dict[str, Any]:
        out = {"line": self.line, "reason": self.reason,
               "source": self.source}
        if self.template:
            out["template"] = True
        return out


def clean_extract(words: str, cur: str, sym: str) -> Optional[str]:
    """`EXTRACT words | CUR | SYM` as a ticker.map line (the parser's
    rule: lib/ticker_map.parse_side_line), or None for a template or a
    line the map would refuse."""
    from taxjson.lib.ticker_map import parse_side_line
    words = " ".join(str(words or "").split())
    line = f"EXTRACT {words} | {str(cur or '').upper()} | {sym}"
    if (not words or any(c in line for c in "<>…#`")
            or str(sym).upper() in _PLACEHOLDERS
            or not _TOKEN_RE.match(str(sym or ""))):
        return None
    try:
        parse_side_line("EXTRACT", line)
    except ValueError:
        return None
    return line


def _clean(line: str) -> Optional[str]:
    """The candidate as a ticker.map line, or None for a template."""
    parts = line.split()
    if len(parts) < 3:
        return None
    kw = parts[0].upper()
    if kw == "EXTRACT":
        m = _EXTRACT_RE.match(line.strip())
        return clean_extract(*m.groups()) if m else None
    if kw not in KEYWORDS:
        return None
    args = parts[1:]
    if kw == "RENAME":
        if len(args) not in (2, 3, 4):
            return None
    elif len(args) != 2:
        return None
    for a in args:
        if (not _TOKEN_RE.match(a) or a.upper() in _PLACEHOLDERS
                or any(c in a for c in "<>|…")):
            return None
    syms = [a.upper() for a in args[:2]]
    if kw != "CRYPTO" and syms[0] == syms[1]:
        return None
    rest = args[2:]
    return " ".join([kw] + syms + rest)


def _headline(text: str, limit: int = 160) -> str:
    """A message's first clause, without its marker."""
    t = _HEAD_RE.sub("", text.strip(), count=1)
    for brk in (" — ", ". ", "; "):
        i = t.find(brk, 20)
        if 0 < i < limit:
            t = t[:i]
            break
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


# Where a message's clause ends: a sentence ("… listing. Only if …",
# not "e.g. `GLOBAL …`"), a semicolon or a dash.
_BREAK_RE = re.compile(r"(?<!\be\.g)(?<!\bi\.e)\.\s+(?=[A-Z])|;\s|\s—\s")
_IF_RE = re.compile(r"\bif\b", re.I)


def _conditional(text: str, start: int, end: int) -> bool:
    """True when the clause of `text` holding text[start:end] (the
    suggested line) puts it under a condition: "Only if …, add …",
    "add … if it is the same security"."""
    if start < 0:
        return bool(_IF_RE.search(text))
    begin = 0
    for m in _BREAK_RE.finditer(text, 0, start):
        begin = m.end()
    m = _BREAK_RE.search(text, end)
    stop = m.start() if m else len(text)
    return bool(_IF_RE.search(text[begin:start] + " " + text[end:stop]))


def _messages(text: str) -> Iterable[Tuple[str, List[str]]]:
    """(marker line, continuation lines) of a captured .diag."""
    head: Optional[str] = None
    cont: List[str] = []
    for ln in text.splitlines():
        if ln[:1] in (" ", "\t") and head is not None:
            cont.append(ln)
            continue
        if head is not None:
            yield head, cont
        head, cont = ln, []
    if head is not None:
        yield head, cont


def _unquoted(text: str) -> str:
    """`text` without its quoted spans (_QUOTED_RE)."""
    return _QUOTED_RE.sub(" ", text)


def from_diag(path: Path, rel: str) -> List[Suggestion]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: List[Suggestion] = []
    for head, cont in _messages(text):
        whole = _unquoted(" ".join([head] + [c.strip() for c in cont]))
        # (candidate, its span in `whole`: the clause around it says
        # whether the hint is conditional)
        found = [(m.group(1), m.start(), m.end())
                 for m in _TICK_RE.finditer(whole)
                 if not m.group(1).startswith("EXTRACT")]
        found += [(m.group(1), m.start(), m.end())
                  for m in _ADD_RE.finditer(whole)]
        found += [(m.group(0), m.start(), m.end())
                  for m in _EXTRACT_RE.finditer(whole)]
        for c in cont:
            m = _BARE_RE.match(_unquoted(c))
            if m:
                i = whole.find(m.group(1))
                found.append((m.group(1), i, i + len(m.group(1))))
        for f, a, b in found:
            line = _clean(f)
            if line:
                out.append(Suggestion(line, _headline(head), rel,
                                      conditional=_conditional(whole, a, b)))
    return out


def from_cross_listings(cache: Path) -> List[Suggestion]:
    from taxjson.lib import cross_listings as XL
    out = []
    state = XL.read_state(cache / XL.STATE)
    for c in state.get("collisions") or []:
        names = " vs ".join(repr(str(n)) for n in (c.get("names") or []))
        why = (f"{c.get('symbol')} names two securities ({names}): the "
               f"rows of {str(c.get('odd') or '')!r} get their own symbol")
        ext = str(c.get("extract") or "")
        m = _EXTRACT_RE.match(ext)
        line = clean_extract(*m.groups()) if m else None
        if line and not c.get("template"):
            out.append(Suggestion(line, why, f"work/{XL.STATE}"))
        elif ext.startswith("EXTRACT ") and "\n" not in ext:
            out.append(Suggestion(
                ext, f"{why} — a template: {c.get('why') or 'edit it'}, "
                f"then add it by hand", f"work/{XL.STATE}", template=True))
        jl = _clean(str(c.get("journal") or ""))
        if jl:
            out.append(Suggestion(
                jl, f"{why}; its transfer journal pairs the separated "
                f"rows with this listing", f"work/{XL.STATE}"))
    for r in state.get("suggested") or []:
        o, i = r.get("out") or {}, r.get("in") or {}
        line = _clean(f"TOBASE {r.get('from')} {r.get('to')}")
        if not line:
            continue
        where = (o.get("account") if o.get("account") == i.get("account")
                 else f"{o.get('account')} -> {i.get('account')}")
        out.append(Suggestion(
            line,
            f"{where}: transfer journal {o.get('symbol')} out "
            f"{float(o.get('quantity') or 0):g} ({o.get('date')}), "
            f"{i.get('symbol')} in ({i.get('date')}); not joined "
            f"automatically: {r.get('reason') or 'unconfirmed'} — add it "
            f"only if they are one security",
            f"work/{XL.STATE}", conditional=True))
    return out


def from_symbol_codes(cache: Path) -> List[Suggestion]:
    from taxjson.lib.symbol_codes import SUFFIX
    out = []
    for p in sorted(cache.glob(f"*{SUFFIX}")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if not isinstance(doc, dict):
            continue
        acct = str(doc.get("account") or p.name[:-len(SUFFIX)])
        for code, info in sorted((doc.get("unresolved") or {}).items()):
            if not isinstance(info, dict):
                continue
            detail = _unquoted(str(info.get("detail") or ""))
            for m in _TICK_RE.finditer(detail):
                line = _clean(m.group(1))
                if line:
                    out.append(Suggestion(
                        line, f"{acct}: Questrade code {code} "
                        f"{_headline(detail.split(' — add ')[0], 200)}",
                        f"work/{p.name}",
                        conditional=_conditional(detail, m.start(),
                                                 m.end())))
    return out


def from_listing_suffix(cache: Path) -> List[Suggestion]:
    """The explicit lines of the listings `taxjson run` read from the
    evidence, not the row currency (lib/listing_suffix)."""
    from taxjson.lib import listing_suffix as LS
    out = []
    for line, reason, cond in LS.suggestions(cache):
        ln = _clean(line)
        if ln:
            out.append(Suggestion(ln, reason, f"work/*{LS.SUFFIX}",
                                  conditional=cond))
    return out


def gather(root: Path) -> List[Suggestion]:
    """Every suggestion in the project's work/ (deduplicated by line, the
    first source's reason kept), in a stable order."""
    cache = Path(root) / "work"
    found: List[Suggestion] = []
    if cache.is_dir():
        found += from_cross_listings(cache)
        found += from_symbol_codes(cache)
        found += from_listing_suffix(cache)
        for p in sorted(cache.glob("*.diag")):
            found += from_diag(p, f"work/{p.name}")
    out: List[Suggestion] = []
    seen: Set[str] = set()
    for s in found:
        if s.line in seen:
            continue
        seen.add(s.line)
        out.append(s)
    return out


def _holdings_files(root: Path) -> List[Path]:
    """The holdings files taxjson.toml's accounts list (`holdings`)."""
    from taxjson.lib.tomlcompat import tomllib
    cfg_path = root / "taxjson.toml"
    if tomllib is None or not cfg_path.is_file():
        return []
    try:
        cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    out: List[Path] = []
    accts = cfg.get("accounts") if isinstance(cfg, dict) else None
    for acfg in (accts.values() if isinstance(accts, dict) else []):
        raw = acfg.get("holdings") if isinstance(acfg, dict) else None
        for x in ([raw] if isinstance(raw, str) else
                  raw if isinstance(raw, list) else []):
            pp = Path(str(x)).expanduser()
            pp = pp if pp.is_absolute() else root / pp
            if pp.is_file():
                out.append(pp)
    return out


def books_symbols(root: Path) -> Set[str]:
    """Every symbol the project's books name before ticker.map renames
    them — the evidence a conditional hint needs: each account's parsed
    exports and transfer sidecars, corporate-action rows and .tt files
    (OPENING balances too) in work/ (never a derived book: those carry
    the map's renames), plus the holdings files taxjson.toml lists. An
    option adds its underlying listing."""
    from taxjson.bin.taxjson_run import _AUDIT_DERIVED_SUFFIXES
    from taxjson.lib.core import parse_option_underlying
    syms: Set[str] = set()
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
            if isinstance(t, dict):
                syms.update(str(t.get(k) or "").upper()
                            for k in ("symbol", "symbol_new"))
    from taxjson.lib.positions_reports import (PositionsReportError,
                                               read_positions)
    for p in _holdings_files(Path(root)):
        try:
            syms.update(str(r.symbol).upper()
                        for r in read_positions(p).rows)
        except (PositionsReportError, OSError, ValueError):
            continue
    syms.discard("")
    for s in list(syms):
        und = parse_option_underlying(s)
        if und:
            syms.add(str(und).upper())
    return syms


@dataclass
class MapState:
    renamed: Dict[str, str]         # FROM -> target, every rename rule
    named: Set[str]                 # every symbol a rule names
    distinct: Set[frozenset]
    crypto: Set[str]
    extract: List[Tuple[str, str, str]]  # (words lower, CUR, SYMBOL)
    lines: Set[str]                 # the map's rule lines, normalised
    dated: Set[frozenset] = frozenset()   # the dated RENAME pairs


def map_state(path: Path) -> MapState:
    if not Path(path).is_file():
        return MapState({}, set(), set(), set(), [], set())
    from taxjson.bin.taxjson_ticker_map import _parse_map_file, named_symbols
    from taxjson.lib.ticker_map import read_side_rules
    tm = _parse_map_file(Path(path))[0]
    renamed: Dict[str, str] = {}
    for d in (tm.glob, tm.tobase, tm.journal):
        renamed.update({k.upper(): v.upper() for k, v in d.items()})
    side = read_side_rules(Path(path))
    lines = set()
    from taxjson.lib.cli_diag import read_text_utf8
    for raw in read_text_utf8(Path(path)).splitlines():
        ln = " ".join(raw.split("#", 1)[0].split()).upper()
        if ln:
            lines.add(ln)
    return MapState(renamed, set(named_symbols(tm)),
                    {frozenset(p) for p in tm.distinct},
                    {str(k).upper() for k in side.crypto},
                    [(d, c, str(v).upper()) for d, c, v in side.extract],
                    lines,
                    {frozenset((dr.old.upper(), dr.new.upper()))
                     for dr in tm.dated})


def already(s: Suggestion, st: MapState) -> Optional[str]:
    """Why the map already answers `s` (None: it does not)."""
    if s.line.upper() in st.lines:
        return "already in ticker.map"
    kw, syms = s.keyword, s.symbols
    if kw == "EXTRACT":
        cur, sym = s.extract_key
        words = s.line.split("|")[0][len("EXTRACT"):].strip().lower()
        for d, c, v in st.extract:
            if c in (cur, "*") and (d == words or v == sym):
                return "ticker.map has an EXTRACT line for it"
        return None
    if kw == "CRYPTO":
        return ("ticker.map has a CRYPTO line for it"
                if syms[0] in st.crypto else None)
    a, b = syms[0], syms[1]
    if frozenset((a, b)) in st.distinct:
        return "ticker.map keeps the two apart (DISTINCT)"
    if kw == "DISTINCT":
        return None
    if frozenset((a, b)) in st.dated:
        return "ticker.map already renames the two (a dated RENAME)"
    if a in st.renamed:
        return f"ticker.map already maps {a}"

    def _end(x):
        seen = set()
        while x in st.renamed and x not in seen:
            seen.add(x)
            x = st.renamed[x]
        return x
    if _end(a) == _end(b):
        return "ticker.map already joins the two"
    return None


# The start of the reason a suggestion is left out for another one
# (covered_by_suggestion), never for the map.
_COVERED = "another suggestion "


def covered_by_suggestion(why: str) -> bool:
    """True when `why` (pending's reason) names another suggestion, not
    a ticker.map rule."""
    return why.startswith(_COVERED)


def pending(root: Path) -> Tuple[List[Suggestion], List[Tuple[Suggestion, str]]]:
    """(the suggestions to offer, [(a suggestion left out, why)]). A
    conditional suggestion whose symbols the books do not all hold is
    in neither list (books_symbols)."""
    st = map_state(Path(root) / "ticker.map")
    offer: List[Suggestion] = []
    skipped: List[Tuple[Suggestion, str]] = []
    froms: Dict[str, str] = {}
    extracts: Dict[Tuple[str, str], str] = {}
    books: Optional[Set[str]] = None
    for s in gather(root):
        why = already(s, st)
        if why:
            skipped.append((s, why))
            continue
        if s.conditional and s.needs:
            if books is None:
                books = books_symbols(Path(root))
            if not all(x in books for x in s.needs):
                continue
        if s.keyword == "EXTRACT":
            prev = extracts.get(s.extract_key)
            if prev is not None:
                skipped.append((s, f"{_COVERED}moves those rows "
                                   f"({prev})"))
                continue
            extracts[s.extract_key] = s.line
        if s.keyword in _RENAMES:
            prev = froms.get(s.symbols[0])
            if prev is not None:
                skipped.append((s, f"{_COVERED}maps "
                                   f"{s.symbols[0]} ({prev})"))
                continue
            froms[s.symbols[0]] = s.line
        offer.append(s)
    return offer, skipped


def comment(s: Suggestion, today: Optional[str] = None) -> str:
    """The comment written after an appended line: the date and reason,
    on one line."""
    day = today or _date.today().isoformat()
    why = " ".join(s.reason.replace("#", "").split())
    return f"taxjson ticker-map --suggest {day}: {why}"


def appended_text(current: str, chosen: Iterable[Suggestion],
                  today: Optional[str] = None) -> str:
    """ticker.map's text with the chosen lines appended (one comment
    line above each)."""
    chosen = list(chosen)
    if not chosen:
        return current
    out = current
    if out and not out.endswith("\n"):
        out += "\n"
    if out.strip():
        out += "\n"
    for s in chosen:
        out += f"# {comment(s, today)}\n{s.line}\n"
    return out
