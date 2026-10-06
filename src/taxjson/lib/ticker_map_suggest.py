"""`taxjson ticker-map --suggest [--write]`: every ticker.map line the last
`taxjson run` suggested, gathered in one place, each with its reason, and
(with --write) appended to ticker.map.

The run's stages name a ticker.map line wherever the books need the
user's judgment; they are spread over the account messages. Sources, all
in work/ (nothing is recomputed but the cheap reads):

* work/cross_listings.state — the transfer journals between two listings
  that `taxjson run` did not join itself (lib/cross_listings: the names
  are missing or disagree, the pairing is ambiguous): `TOBASE FROM TO`;
* work/<account>_symbol_codes.state — Questrade internal codes the run
  could not resolve, with a "looks like" candidate: `GLOBAL CODE TICKER`;
* every stage's .diag — a ticker.map line a message names: IB's "one
  stock under several symbols" (`GLOBAL OLD NEW`), Questrade's and RBC's
  "looks renamed" hints (`add to ticker.map:  GLOBAL OLD NEW`), the
  crypto price checks' `CRYPTO SYMBOL ID` lines, the gains stage's
  unmapped cross-listing journal (`TOBASE FROM TO`).

A line the map already has, or one whose symbol the map already renames
(the user's rule wins), is left out. A line with a placeholder
(`<number>`, OLD/NEW, `a|b`) is a template, never a suggestion.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# The keywords a suggestion may carry.
KEYWORDS = ("GLOBAL", "TOBASE", "JOURNAL", "DISTINCT", "RENAME", "CRYPTO")
_RENAMES = ("GLOBAL", "TOBASE", "JOURNAL", "RENAME")
_KW = "|".join(KEYWORDS)
_TICK_RE = re.compile(rf"`((?:{_KW}) [^`]+)`")
_ADD_RE = re.compile(rf"add to ticker\.map:\s+((?:{_KW})\s+\S+\s+\S+)")
_BARE_RE = re.compile(rf"^\s+((?:{_KW})\s+\S+\s+\S+)\s*$")
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

    @property
    def keyword(self) -> str:
        return self.line.split()[0]

    @property
    def symbols(self) -> List[str]:
        return self.line.split()[1:]

    def record(self) -> Dict[str, Any]:
        return {"line": self.line, "reason": self.reason,
                "source": self.source}


def _clean(line: str) -> Optional[str]:
    """The candidate as a ticker.map line, or None for a template."""
    parts = line.split()
    if len(parts) < 3:
        return None
    kw = parts[0].upper()
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
        found = [m.group(1) for m in _TICK_RE.finditer(whole)]
        found += [m.group(1) for m in _ADD_RE.finditer(whole)]
        found += [m.group(1) for c in cont
                  for m in [_BARE_RE.match(_unquoted(c))] if m]
        for f in found:
            line = _clean(f)
            if line:
                out.append(Suggestion(line, _headline(head), rel))
    return out


def from_cross_listings(cache: Path) -> List[Suggestion]:
    from taxjson.lib import cross_listings as XL
    out = []
    for r in XL.read_state(cache / XL.STATE).get("suggested") or []:
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
            f"work/{XL.STATE}"))
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
            detail = str(info.get("detail") or "")
            for m in _TICK_RE.finditer(_unquoted(detail)):
                line = _clean(m.group(1))
                if line:
                    out.append(Suggestion(
                        line, f"{acct}: Questrade code {code} "
                        f"{_headline(detail.split(' — add ')[0], 200)}",
                        f"work/{p.name}"))
    return out


def gather(root: Path) -> List[Suggestion]:
    """Every suggestion in the project's work/ (deduplicated by line, the
    first source's reason kept), in a stable order."""
    cache = Path(root) / "work"
    found: List[Suggestion] = []
    if cache.is_dir():
        found += from_cross_listings(cache)
        found += from_symbol_codes(cache)
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


@dataclass
class MapState:
    renamed: Dict[str, str]         # FROM -> target, every rename rule
    named: Set[str]                 # every symbol a rule names
    distinct: Set[frozenset]
    crypto: Set[str]
    lines: Set[str]                 # the map's rule lines, normalised


def map_state(path: Path) -> MapState:
    if not Path(path).is_file():
        return MapState({}, set(), set(), set(), set())
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
                    {str(k).upper() for k in side.crypto}, lines)


def already(s: Suggestion, st: MapState) -> Optional[str]:
    """Why the map already answers `s` (None: it does not)."""
    if s.line.upper() in st.lines:
        return "already in ticker.map"
    kw, syms = s.keyword, s.symbols
    if kw == "CRYPTO":
        return ("ticker.map has a CRYPTO line for it"
                if syms[0] in st.crypto else None)
    a, b = syms[0], syms[1]
    if frozenset((a, b)) in st.distinct:
        return "ticker.map keeps the two apart (DISTINCT)"
    if kw == "DISTINCT":
        return None
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


def pending(root: Path) -> Tuple[List[Suggestion], List[Tuple[Suggestion, str]]]:
    """(the suggestions to offer, [(a suggestion left out, why)])."""
    st = map_state(Path(root) / "ticker.map")
    offer: List[Suggestion] = []
    skipped: List[Tuple[Suggestion, str]] = []
    froms: Dict[str, str] = {}
    for s in gather(root):
        why = already(s, st)
        if why:
            skipped.append((s, why))
            continue
        if s.keyword in _RENAMES:
            prev = froms.get(s.symbols[0])
            if prev is not None:
                skipped.append((s, f"another suggestion maps "
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
