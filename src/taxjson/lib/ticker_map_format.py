"""`taxjson format-map`: lay ticker.map out in keyword groups.

The ticker.map counterpart of `taxjson format` (lib/config_template): a
short `## ` header saying what the file is, then the rules in groups in a
fixed order, each under a `## --- <Group> ---` heading and a line or two
naming its keywords:

  Spellings                 GLOBAL, and RENAME without a date (it is GLOBAL)
  Listings of one security  TOBASE, DISTINCT
  Clean-up                  DELETE
  Dated events              RENAME with a date, JOURNAL
  Lookups                   QUOTE, EXTRACT, CRYPTO, T1135 and the market
                            lists (STABLE ... VENUE)
  Retired                   TRADINGVIEW (only when the file has one)
  Unrecognized              lines taxjson cannot use (only when present)

Rules:

- Within a group the lines keep the user's order (EXTRACT's first match
  wins, so order can matter); exact duplicates (the same rule and inline
  note, no comment of their own) are dropped and reported.
- A rule line is normalised: the keyword upper case, one space between
  fields (EXTRACT keeps `desc | CUR | SYM`), an inline `# note` kept two
  spaces after it. The symbols are left as written.
- A comment block directly above a line (no blank line between) moves
  with it; so does a block directly below one when a blank line ends it
  (a note on the line above). A commented-out rule (`# GLOBAL A B`, one
  `#`, a keyword in upper case, and a line taxjson would accept) is a
  comment: above a rule it moves with that rule; on its own it goes to its
  keyword's group, the prose directly above it with it. A free-standing
  comment block stays in the group whose heading it sits under; in a file
  never formatted, it goes before the rule that follows it (to that rule's
  group), or after the last rule; one before every rule stays at the top,
  under the header. Comment lines are kept byte for byte (trailing blanks
  dropped). `## ` lines this module writes, and the comment paragraphs
  earlier `taxjson init` templates wrote (recognised by hash, a paragraph
  the user edited kept whole), are regenerated.
- A line whose rule is not in the map (no keyword, malformed, a second
  target or lookup value for one symbol) is kept exactly as written, with
  its comments, in the Unrecognized group at the end (after the line it
  contradicts, so that one still wins). A contradiction that keeps both
  rules (a rename cycle, a DISTINCT pair the renames join, a repeated
  dated RENAME) leaves the lines in their groups; format_map reports
  every problem either way.
- The result must parse to the same map (every rule, lookup and dated
  rename, and the same number of problems) and keep every comment line,
  or FormatError is raised and nothing should be written.
"""
from __future__ import annotations

import re
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from taxjson.lib.config_template import DOC_WIDTH, _hash, _norm
from taxjson.lib.ticker_map import (RENAME_KEYWORDS, RETIRED_KEYWORDS,
                                    SIDE_KEYWORDS, TICKER_MAP_NAME,
                                    side_rules_from_text)

PROSE = "## "

SPELLINGS = "Spellings"
LISTINGS = "Listings of one security"
CLEANUP = "Clean-up"
DATED = "Dated events"
LOOKUPS = "Lookups"
RETIRED = "Retired"
UNRECOGNIZED = "Unrecognized"

# (group, its keywords, its intro, its (form, meaning) lines): the text
# under its heading. Every ticker.map keyword is in exactly one group
# (tests check it against RENAME_KEYWORDS + SIDE_KEYWORDS +
# RETIRED_KEYWORDS). RENAME is listed under Dated events; an undated one
# is filed under Spellings (it means GLOBAL: _rule_group).
Doc = Tuple[str, Tuple[Tuple[str, str], ...]]
GROUPS: Tuple[Tuple[str, Tuple[str, ...], Doc], ...] = (
    (SPELLINGS, ("GLOBAL",), ("", (
        ("GLOBAL FROM TO", "FROM is another spelling of TO, in every stage "
         "(an odd broker ticker, an internal code, a coin alias)"),
        ("RENAME FROM TO", "the same (a RENAME without a date)"),
    ))),
    (LISTINGS, ("TOBASE", "DISTINCT"), ("", (
        ("TOBASE FROM TO", "two listings of one security: one cost pool "
         "(in the base currency)"),
        ("DISTINCT A B", "two look-alike listings that are separate "
         "securities"),
    ))),
    (CLEANUP, ("DELETE",), ("", (
        ("DELETE SYMBOL", "drop every row of a symbol that is no real "
         "security (a broker artifact)"),
    ))),
    (DATED, ("RENAME", "JOURNAL"), ("", (
        ("RENAME OLD NEW YYYY-MM-DD [late=fold|late=separate]",
         "a ticker change on that date"),
        ("JOURNAL FROM TO", "a Norbert's Gambit pair: one cost pool, "
         "netted in the holdings view"),
    ))),
    (LOOKUPS, ("QUOTE", "EXTRACT", "CRYPTO", "T1135", "STABLE",
               "SPLITSHARE", "INDEXOPT", "EVENING", "MULT", "VENUE"),
     ("They change no symbol in the books.", (
         ("QUOTE SYMBOL YAHOO_SYMBOL [RATIO]",
          "the Yahoo spelling a price lookup uses"),
         ("EXTRACT DESCRIPTION WORDS | CURRENCY | SYMBOL",
          "the symbol of a broker row with these words"),
         ("CRYPTO SYMBOL YAHOO_ID", "a coin's Yahoo id"),
         ("T1135 SYMBOL COUNTRY", "the T1135 domicile of a symbol (Canada)"),
         ("STABLE SYMBOL USD|NO", "a US-dollar stablecoin, or not one"),
         ("SPLITSHARE ROOT [NO]", "a Canadian split-share corporation"),
         ("INDEXOPT ROOT [NO]", "a broad-based index option root (US)"),
         ("EVENING ROOT [NO]", "an option root with a Cboe evening session"),
         ("MULT SYMBOL N", "an option's contract size"),
         ("VENUE IBCODE SUFFIX|NO", "an IB listing exchange and its suffix"),
     ))),
    (RETIRED, tuple(RETIRED_KEYWORDS), ("", (
        ("TRADINGVIEW ...", "the removed TradingView export: taxjson "
         "ignores the line; delete it"),
    ))),
    (UNRECOGNIZED, (), (
        "Lines taxjson cannot use, kept as written: `taxjson run` refuses "
        "the map until each is fixed (`taxjson format-map` names the "
        "problem of each).", ())),
)
GROUP_NAMES = tuple(g for g, _k, _d in GROUPS)
# Shown only when they hold something.
OPTIONAL_GROUPS = (RETIRED, UNRECOGNIZED)
KEYWORD_GROUP: Dict[str, str] = {kw: g for g, kws, _d in GROUPS
                                 for kw in kws}
ALL_KEYWORDS = RENAME_KEYWORDS + SIDE_KEYWORDS + tuple(RETIRED_KEYWORDS)

HEADER = (
    "ticker.map: standing truths about the securities in your books, one "
    "rule per line, in groups: a spelling to read as another symbol, two "
    "listings that are one security (or are not), a symbol to drop, a "
    "ticker change on a date, and lookups an export cannot give. Optional: "
    "a run needs no rule here. Symbols are case-insensitive; a note goes "
    "after `#`; a line starting `# KEYWORD` is a rule switched off (delete "
    "the `# ` to use it). `taxjson format-map` keeps this layout, and a "
    "comment directly above a line moves with it. Every keyword: "
    "docs/settings.md, ticker.map.")


def _prose(text: str, indent: str = "") -> List[str]:
    """`text` as `## ` lines, wrapped (lib/config_template's width)."""
    return [PROSE + indent + ln for ln in textwrap.wrap(
        text, DOC_WIDTH - len(indent), break_long_words=False,
        break_on_hyphens=False)]


def _doc_lines(doc: Doc) -> List[str]:
    """A group's text: its intro, then each form with its meaning beside
    it (wrapped under itself)."""
    intro, entries = doc
    out = _prose(intro) if intro else []
    w = max((len(f) for f, _m in entries), default=0)
    for form, mean in entries:
        lines = _prose(mean, " " * (w + 2))
        out.append(PROSE + form.ljust(w + 2) + lines[0][len(PROSE) + w + 2:])
        out += lines[1:]
    return out


def heading(group: str) -> str:
    return f"{PROSE}--- {group} ---"


_HEADINGS = {heading(g): g for g in GROUP_NAMES}
# Every `## ` line this module writes (regenerated, never kept as a note).
_OWN_LINES = frozenset(
    _prose(HEADER) + list(_HEADINGS)
    + [ln for _g, _k, doc in GROUPS for ln in _doc_lines(doc)])

# Comment lines earlier `taxjson init` ticker.map templates wrote (every
# version before the grouped layout), as hashes of their _norm text
# (lib/config_template: leading '#'s and blanks dropped, years generic;
# sha256, first 16 hex digits). Their commented-out example rules are
# not here: those are kept like the user's own. format-map regenerates
# these lines (the new header) instead of keeping them as notes; a bare
# `#` line beside one goes with it. When the HEADER or a group's text
# changes, add the old lines' hashes here.
_LEGACY_TEMPLATE_HASHES = frozenset("""
00a3f0154037172c 073bfe795ee08d6b 0abd4d7da73b73a1 0e0f17b044b09c49
15ebd6f0aac94027 208f8b138e273883 283534607a882ad0 28690b485a860f91
2b577ed893a8f4a0 2cc59cd628441c44 3ce39165443b6351 418b2aaca345e818
43aa33fa691f26fe 455e9eb53cfffcf4 4cb6ec017c0c31a8 4de745aeab6694f3
4e732dba203e8b70 530d5b56790d7f0c 54098570a516ee12 55da8b9532665902
5766c4673142b2ab 5da5373b81e28e86 6257c02acdf8fd3b 62d07ddcd4e8626f
6423d61bc021f2c5 68d4489ee559b472 6b86caba16ca90f3 6cfbde668d339194
70b9b941625e1672 7582793a2046b476 794abc923ce8ae7b 7a70a201496dcf09
820fedc12f7c411d 83165efb8b38283c 8687fdeac707ef39 8708d77fbeae50a2
8e28e7caa035bb2e 8e410f2a6702f44b 94038cb7dd941eb0 99edf6b3006f1513
a175827dc46f5185 a8d478b5ddd42491 a9b58705ca106c2a ac2dd6b64541323d
b01f65c5eee57ccb ba4250e7b402ef87 ba5ad447551ade4a bd04ca4ba3f73aed
bdd7713581cc79ed be6786485b6ea26b c3cfba0990e440b4 cb7472ebf86a801d
cc4f90c34dc82b18 ccc2de5407f0b248 cf96752f3f331088 d9eae8f569b22a0b
e2b4c2cd06694258 e8f2ed7569e503bd ecff9d4866de8089 f3cf7a16576d97c4
f9d2e8bf0c3996e0 f9f4fb8408af37c9 fa5932b0b777a54f fb45c83e3a4dd8d8
fc493d8d1d00b0b3 fd3d15db6d964346 fe502ff31abba977
""".split())


class FormatError(ValueError):
    """The map cannot be laid out without changing what it means or
    losing a comment; str(e) is the user-facing reason."""


# ------------------------------------------------------------ lines

def _split_lines(text: str) -> List[str]:
    """The map's lines as the parser numbers them (split at newlines
    only), a BOM and trailing blanks dropped."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [ln.rstrip() for ln in lines]


def _is_legacy(line: str) -> bool:
    n = _norm(line.strip())
    return bool(n) and _hash(n) in _LEGACY_TEMPLATE_HASHES


def _accepted(line: str) -> bool:
    """Would taxjson read `line`, alone in a map, without a problem?"""
    from taxjson.bin.taxjson_ticker_map import _parse_map_text
    return not _parse_map_text(line + "\n", TICKER_MAP_NAME)[1]


def commented_rule(line: str) -> Optional[str]:
    """The keyword of a commented-out rule (`# GLOBAL A B`: one `#`, a
    keyword in upper case, and a line taxjson accepts once the `# ` is
    deleted), or None for prose."""
    st = line.strip()
    if not st.startswith("#") or st.startswith("##"):
        return None
    body = st[1:].strip()
    toks = body.split()
    if len(toks) < 2 or toks[0] not in ALL_KEYWORDS:
        return None
    if toks[0] in RETIRED_KEYWORDS:
        return toks[0]
    return toks[0] if _accepted(body) else None


def _switched_off(line: str) -> Optional[str]:
    """commented_rule, except an earlier template's line (its
    `#   CRYPTO  SYMBOL YAHOO_ID` describes the keyword: it is that
    template's text, not an example)."""
    kw = commented_rule(line)
    return None if kw is None or _is_legacy(line) else kw


def normalise_rule(line: str) -> str:
    """A rule line laid out: the keyword upper case, one space between
    fields (EXTRACT: `KEYWORD desc | CUR | SYM`, the description's own
    spacing kept), an inline `# note` two spaces after it."""
    code, hash_, note = line.partition("#")
    toks = code.split()
    kw = toks[0].upper()
    if kw == "EXTRACT":
        body = code.strip()[len(toks[0]):]
        out = kw + " " + " | ".join(p.strip() for p in body.split("|"))
    else:
        out = " ".join([kw] + toks[1:])
    if hash_:
        out += "  #" + note.rstrip()
    return out


def _rule_group(keyword: str, line: str) -> str:
    if keyword == "RENAME":
        code = line.split("#", 1)[0].split()
        return DATED if len(code) > 3 else SPELLINGS
    return KEYWORD_GROUP[keyword]


# ------------------------------------------------------------ items

@dataclass
class Item:
    """One unit that moves as a whole: a rule (live or commented out)
    with the comment lines above and below it, or a free-standing
    comment block (`note`)."""
    kind: str                 # rule | commented | note | bad | retired
    seq: int                  # source order
    line: str = ""            # the line itself, as it will be written
    lineno: int = 0           # its line number in the source (1-based)
    above: List[str] = field(default_factory=list)
    below: List[str] = field(default_factory=list)
    group: str = ""
    heading: Optional[str] = None    # the group heading it sat under

    def lines(self) -> List[str]:
        return self.above + ([self.line] if self.line else []) + self.below

    def starts_with_comment(self) -> bool:
        return bool(self.above) or self.kind in ("commented", "note")

    def ends_with_comment(self) -> bool:
        return bool(self.below) or self.kind in ("commented", "note")

    def bare_commented(self) -> bool:
        return (self.kind == "commented" and not self.above
                and not self.below)


@dataclass
class FormatResult:
    text: str
    changed: bool
    counts: Dict[str, int]                 # live lines per group
    problems: List[str]                    # what `taxjson run` refuses
    duplicates: List[Tuple[int, str]]      # (line number, line) dropped
    retired: int                           # live lines in Retired
    preamble: int                          # free-standing blocks kept at the top


def _problems(text: str) -> Tuple[List[str], Dict[int, str], set]:
    """(every problem `taxjson run` refuses the map for, {line number:
    the problem} of each line whose rule is not in the map, the line
    numbers a problem or a no-op note starts with)."""
    from taxjson.bin.taxjson_ticker_map import _parse_map_text
    dropped: List[str] = []
    _tm, problems, notes = _parse_map_text(text, TICKER_MAP_NAME, dropped)
    at = re.compile(re.escape(TICKER_MAP_NAME) + r":(\d+): ")
    lines: Dict[int, str] = {}
    for p in dropped:
        m = at.match(p)
        if m:
            lines.setdefault(int(m.group(1)), p)
    named = {int(m.group(1)) for m in map(at.match, problems + notes) if m}
    return problems, lines, named


def _drop_template(lines: List[str]) -> List[Optional[str]]:
    """`lines` with the template's own comment lines replaced by None: a
    `## ` line this module writes (a group heading is kept: it marks
    where a free-standing block sits), and a paragraph of an earlier
    `taxjson init` template — comment lines between bare `#` lines,
    commented-out rules or the block's ends, every one of them that
    template's text (a paragraph the user edited is kept whole) — with
    the bare `#` lines beside it."""
    out: List[Optional[str]] = list(lines)

    def comment(i: int) -> bool:
        return lines[i].strip().startswith("#")

    def bare(i: int) -> bool:
        return lines[i].strip() == "#"

    def rule(i: int) -> bool:
        return bool(_switched_off(lines[i]))

    for i, ln in enumerate(lines):
        st = ln.strip()
        if st in _OWN_LINES and st not in _HEADINGS:
            out[i] = None
    dropped = [False] * len(lines)
    i = 0
    while i < len(lines):
        if not comment(i) or out[i] is None or bare(i) or rule(i) \
                or lines[i].strip() in _HEADINGS:
            i += 1
            continue
        j = i
        while (j < len(lines) and comment(j) and not bare(j)
               and out[j] is not None and not rule(j)
               and lines[j].strip() not in _HEADINGS):
            j += 1
        if all(_is_legacy(lines[k]) for k in range(i, j)):
            for k in range(i, j):
                out[k] = None
                dropped[k] = True
        i = j
    for i in range(len(lines)):
        if out[i] is None or not bare(i):
            continue
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(lines) and comment(j) and bare(j):
                j += step
            if 0 <= j < len(lines) and dropped[j]:
                out[i] = None
                break
    return out


def _items(text: str) -> List[Item]:
    """The items of a map's text, each with its group."""
    lines = _split_lines(text)
    kept = _drop_template(lines)
    unusable = _problems(text)[1]
    items: List[Item] = []
    pending: List[str] = []
    after: Optional[Item] = None       # the live line just above pending
    last_live: Optional[Item] = None   # the line directly above
    heading_now: Optional[str] = None
    seq = 0

    def nxt() -> int:
        nonlocal seq
        seq += 1
        return seq

    def flush() -> None:
        nonlocal pending, after
        block, pending = pending, []
        owner, after = after, None
        if not block:
            return
        crs = [i for i, ln in enumerate(block) if _switched_off(ln)]
        if not crs:
            if owner is not None:
                owner.below = block
            else:
                items.append(Item("note", nxt(), above=block,
                                  heading=heading_now))
            return
        start = 0
        last = None
        for i in crs:
            kw = _switched_off(block[i])
            last = Item("commented", nxt(), line=block[i],
                        above=block[start:i],
                        group=_rule_group(kw, block[i][block[i].index("#")
                                                       + 1:]),
                        heading=heading_now)
            items.append(last)
            start = i + 1
        last.below = block[start:]

    for lineno, ln in enumerate(kept, 1):
        if ln is None:
            continue
        st = ln.strip()
        if not st or st in _HEADINGS:
            flush()
            last_live = None
            if st:
                heading_now = _HEADINGS[st]
            continue
        if st.startswith("#"):
            if not pending:
                after = last_live
            pending.append(ln)
            last_live = None
            continue
        kw = st.split("#", 1)[0].split()
        kw = kw[0].upper() if kw else ""
        if lineno in unusable or kw not in ALL_KEYWORDS:
            item = Item("bad", nxt(), line=ln, lineno=lineno,
                        group=UNRECOGNIZED)
        elif kw in RETIRED_KEYWORDS:
            item = Item("retired", nxt(), line=st, lineno=lineno,
                        group=RETIRED)
        else:
            item = Item("rule", nxt(), line=normalise_rule(st),
                        lineno=lineno, group=_rule_group(kw, st))
        item.above, pending, after = pending, [], None
        item.heading = heading_now
        items.append(item)
        last_live = item
    flush()
    _place_notes(items)
    return items


def _place_notes(items: List[Item]) -> None:
    """The group of each free-standing block: the heading it sits under;
    else, before every rule, none (the top of the file); else the group
    of the rule after it, or of the last rule."""
    rules = [it for it in items if it.kind != "note"]
    for it in items:
        if it.kind != "note":
            continue
        if it.heading is not None:
            it.group = it.heading
            continue
        before = [r for r in rules if r.seq < it.seq]
        later = [r for r in rules if r.seq > it.seq]
        if not before:
            it.group = ""
        else:
            it.group = (later[0] if later else before[-1]).group


# ------------------------------------------------------------ render

def _render(items: List[Item]) -> str:
    out: List[str] = _prose(HEADER)
    for it in items:
        if it.group == "":
            out += [""] + it.lines()
    for group, _kws, doc in GROUPS:
        members = [it for it in items if it.group == group]
        if group in OPTIONAL_GROUPS and not members:
            continue
        out += ["", heading(group)] + _doc_lines(doc)
        prev: Optional[Item] = None
        for it in sorted(members, key=lambda x: x.seq):
            if prev is None:
                out.append("")
            elif not (prev.bare_commented() and it.bare_commented()) and (
                    it.starts_with_comment() or prev.ends_with_comment()):
                out.append("")
            out += it.lines()
            prev = it
    return "\n".join(out) + "\n"


# ------------------------------------------------------------ meaning

def meaning(text: str) -> tuple:
    """What a map's text means to taxjson: every rename table, DELETE,
    DISTINCT, dated rename (in order, without its line number), lookup
    and market-list entry (EXTRACT in order), and how many problems,
    no-op notes and retired lines it has."""
    from taxjson.bin.taxjson_ticker_map import _parse_map_text
    tmap, problems, notes = _parse_map_text(text, TICKER_MAP_NAME)
    side = side_rules_from_text(text, TICKER_MAP_NAME)
    return (tmap.glob, tmap.tobase, tmap.journal, frozenset(tmap.delete),
            frozenset(tmap.distinct),
            tuple((d.old, d.new, d.date, d.late) for d in tmap.dated),
            frozenset(tmap.undated_rename), frozenset(tmap.lookup_named),
            side.quote, side.crypto, tuple(side.extract), side.t1135,
            side.stable, side.splitshare, side.indexopt, side.evening,
            side.mult, side.venue, len(problems), len(notes),
            len(side.problems), len(side.retired))


def _comment_lines(text: str) -> Counter:
    """Every comment the user wrote: whole comment lines and the `#...`
    of a line, template text left out."""
    out: Counter = Counter()
    for ln in _drop_template(_split_lines(text)):
        if ln is None or "#" not in ln:
            continue
        st = ln.strip()
        if st in _HEADINGS:
            continue
        out[st if st.startswith("#") else st[st.index("#"):]] += 1
    return out


def format_map(text: str) -> FormatResult:
    """Lay a ticker.map's text out in groups (module docstring). Raises
    FormatError when the result would mean something else or lose a
    comment (nothing should then be written)."""
    original = text
    if text.startswith("\ufeff"):
        text = text[1:]
    items = _items(text)
    named = _problems(text)[2]
    seen: Dict[str, int] = {}
    dups: List[Tuple[int, str]] = []
    kept: List[Item] = []
    for it in items:
        # (a line a problem or note names is never dropped: a repeated
        # dated RENAME is booked twice, a no-op line is counted)
        if (it.kind == "rule" and not it.above and not it.below
                and it.lineno not in named):
            if it.line in seen:
                dups.append((it.lineno, it.line))
                continue
            seen[it.line] = it.lineno
        elif it.kind == "rule":
            seen.setdefault(it.line, it.lineno)
        kept.append(it)
    new = _render(kept)
    if meaning(new) != meaning(text):
        raise FormatError(
            "laying the map out in groups would change what it means "
            "(two lines whose order matters are in different groups) — "
            "fix the problems `taxjson run` reports, then format it again")
    lost = (_comment_lines(text)
            - Counter(ln[ln.index("#"):] for _n, ln in dups if "#" in ln)
            - _comment_lines(new))
    if lost:
        raise FormatError(
            f"laying the map out would lose comment text "
            f"({next(iter(lost))!r}) — please report this")
    counts = {g: sum(1 for it in kept if it.group == g
                     and it.kind in ("rule", "bad", "retired"))
              for g in GROUP_NAMES}
    return FormatResult(
        text=new, changed=new != original,
        counts=counts, problems=_problems(text)[0], duplicates=dups,
        retired=counts[RETIRED],
        preamble=sum(1 for it in kept if it.group == ""))


# ------------------------------------------------------------ template

# The commented-out examples `taxjson init` writes, one group after the
# other (format_map files each under its keyword's group).
_INIT_EXAMPLES = (
    "# GLOBAL ABCX-B.US ABCX.B.US",
    "# GLOBAL ZZC2 ZZC",
    "# TOBASE XYZQ.US XYZQ.TO",
    "# DISTINCT WXYQ.US WXYQ.TO",
    "# DELETE ZZZQ.US",
    "# RENAME OLDQ.US NEWQ.US 2024-06-10",
    "# JOURNAL ABCX.U.TO ABCX.TO",
    "# QUOTE XYZQ.TO XYZQ.V",
    "# EXTRACT Example US Dollar Unit Fund | USD | ABCX.U.TO",
    "# CRYPTO ABC ABC12345",
    "# T1135 XYZQ.US CA",
    "# STABLE ZZUSD USD",
    "# SPLITSHARE ZZQ",
    "# MULT ZZQ1 50",
)


def init_template() -> str:
    """The ticker.map `taxjson init` writes: the formatted layout with a
    commented-out example in each group (format_map leaves it as is)."""
    return format_map("\n".join(_INIT_EXAMPLES) + "\n").text
