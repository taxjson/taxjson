"""A broker's internal security codes resolved to real tickers (tax-logic
CA-ACB-CODES / US-BASIS-CODES).

Questrade's website export writes some rows of a security under an
internal code (one letter + digits, X000123) instead of its ticker --
typically shares transferred in from another broker, before Questrade
links them to the listing: the transfer-in, its dividends, later rows.
The parser resolves a code from the account's own trades and transfers
of the same description (questrade.QtAccountContext). A code nothing in
the account resolves was a security of its own, with one ATTENTION line
asking for a ticker.map GLOBAL line.

`taxjson run` now infers the ticker from the rest of the project's books
before the account is parsed, in this order, and records the evidence:

1. transfer pairing: a transfer-in of the code (quantity q on date d) is
   the arrival of an OUTGOING transfer of q shares in another broker's
   export of the project (any account) dated from PAIR_DAYS_BEFORE days
   before d to PAIR_DAYS_AFTER days after it, whose security name
   agrees with the code's description (names_agree mode="pairing": the
   same company, and no CONFLICTING share designator — a class letter
   or ORDINARY / ADR stated by one broker only is not a conflict).
   Every transfer-in that pairs must name the same ticker, an outgoing
   transfer may pair with one code only, and a leg of another class of
   the same company in the window makes it ambiguous;
2. name match (the code has no transfer-in at all): the code's
   normalised name (name_tokens) EQUALS the name of exactly ONE listing
   known elsewhere in the project's books — share designators
   included — or, failing that, exactly one listing of the SAME
   broker's descriptions of which the code's is a cut-off prefix, the
   code's description being exactly the export's width
   (names_agree mode="name_only", same_broker);
3. otherwise the code stays as exported: one ATTENTION line per code
   with the GLOBAL line to add (and a quantity/date or near-name
   candidate, with its GLOBAL line, when one exists but is not
   certain).

Any ticker.map rule naming the code's listing (a rename, DELETE,
DISTINCT, a dated RENAME) always wins: such a code is never inferred and
is recorded under "mapped". No name->ticker table is kept: names and
tickers come from the user's own exports; the only word lists are
language — the generic corporate-form / share-word vocabulary set
aside, the abbreviations folded (ABBREVIATIONS: RES = RESOURCES,
N V = NV ...), the broker boilerplate cut, and the share designators
kept when comparing names.

The result is written to work/<acct>_symbol_codes.state (JSON) and passed
to the parser (`taxjson-brokerage --symbol-codes`), which books every row
of a resolved code under the ticker and prints ONE note per account
(codes_note) instead of the per-code ATTENTION lines.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SUFFIX = "_symbol_codes.state"
FORMAT = "symbol_codes/1"

# The window an outgoing transfer may sit in around the arrival: the
# sending broker usually books it first.
PAIR_DAYS_BEFORE = 10
PAIR_DAYS_AFTER = 3

# The note's fixed head: the console echoes it from the parse .diag and
# tests / tools parse the entries after it (parse_codes_note).
NOTE_HEAD = "note: Questrade internal symbol codes resolved"

# Words that say nothing about WHICH security a name is: the broker's
# transfer wording and the generic "common shares" words. Dropped.
_GENERIC = frozenset("""
COMMON COM STOCK STK SHARES SHARE SHS SHRS REGISTERED REG REGISTRY EACH ECH
REPR REP RECEIPT RECEIPTS
TRANSFER TRANSFERRED TFER TFR IN OUT FROM TO ACATS ATON INTERDEPOT
DELIVER DELIVERED RECEIVE RECEIVED
""".split())
# Corporate-form words: noise, but only because every other word of the
# two names (the share designators included) must then agree — "QZX INC"
# is "QZX CORP", never "NEW QZX CORP" nor "QZX INC CL B".
_FORM = frozenset("""
INC INCORPORATED CORP CORPORATION CO COMPANY LTD LIMITED PLC LLC LP LLP
SA NV AG SE AS ASA AB OYJ SPA BV THE
""".split())
# Abbreviations brokers use for the same English word, folded to one
# spelling on BOTH sides before names are compared ("EOG RES INC" is
# "EOG RESOURCES INC"). Language, not security data: no ticker or issuer
# appears here, and a security-specific spelling belongs in ticker.map.
ABBREVIATIONS: Dict[str, str] = {
    "RES": "RESOURCES",
    "MFG": "MANUFACTURING",
    "CO": "COMPANY",
    "CORP": "CORPORATION",
    "INTL": "INTERNATIONAL",
    "HLDGS": "HOLDINGS", "HLDG": "HOLDINGS", "HOLDING": "HOLDINGS",
    "GRP": "GROUP",
    "TECH": "TECHNOLOGIES", "TECHNOLOGY": "TECHNOLOGIES",
    "SYS": "SYSTEMS",
    "SVCS": "SERVICES",
    "FINL": "FINANCIAL",
    "INDS": "INDUSTRIES",
    "REGISTRY": "REG",
    "SHS": "SHARES", "SHRS": "SHARES",
    "SPONS": "SPONSORED",
}
# Spellings split over several tokens, joined before the words are read:
# "N V" / "N.V." is NV, "N Y" is NY, "A/S" is AS, "&" is AND, IB's "-SP
# ADR" is a sponsored ADR, "AMERICAN DEPOSITARY SHARES" an ADR.
_PHRASES = (
    (re.compile(r"&"), " AND "),
    (re.compile(r"\bA\s*/\s*S\b"), " AS "),
    (re.compile(r"\bN\s*[.\s]\s*V\b\.?"), " NV "),
    (re.compile(r"\bN\s*[.\s]\s*Y\b\.?"), " NY "),
    (re.compile(r"\bS\s*[.\s]\s*A\b\.?"), " SA "),
    (re.compile(r"\bP\s*[.\s]\s*L\s*[.\s]\s*C\b\.?"), " PLC "),
    (re.compile(r"\bL\s*[.\s]\s*L\s*[.\s]\s*C\b\.?"), " LLC "),
    (re.compile(r"\bL\s*[.\s]\s*P\b\.?"), " LP "),
    (re.compile(r"\bSP\s+ADRS?\b"), " SPONSORED ADR "),
    (re.compile(r"\bAMERICAN\s+DEPOSI?TA?(?:RY|RIES)\b"), " ADR "),
)
# IB's domicile qualifier after '/' ("NU HOLDINGS LTD/CAYMAN ISL-A"): up
# to the '-' that starts the class, or the end. Kept when it holds a share
# designator ("QZX CORP/NEW").
_DOMICILE_RE = re.compile(r"(?<=[A-Z0-9]{2})\s*/\s*([A-Z][A-Z .]*?)\s*(?=-|$)")
# Words after which the rest describes the depositary's underlying shares
# ("SPONSORED ADR REPSTG 5 COM ...", "ADS EACH RPRSNTNG ONE CL A ORD"):
# cut, with the rest.
_REPRESENTING = frozenset("""
REPSTG RPSTG REPRESENTING REPRESENTG REPRSNTNG RPRSNTNG REPR
""".split())
_TRANSFER_WORDS = frozenset(("TRANSFER", "TRANSFERRED", "TFER", "TFR"))
_TRANSFER_NEXT = frozenset(("IN", "FROM", "TO", "OUT", "BOOK"))
# Words that say WHICH share of a company a name is — its voting rights,
# a depositary wrapper, an ordinary / preferred share, a unit / warrant /
# right, a successor issuer (NEW) — kept as designators, one spelling
# each (marked "~"). CL / CLASS themselves are dropped: the class LETTER
# after them is the designator, and so is any lone letter ("QZBRK B").
# Generic vocabulary, not security data.
_DESIGNATORS = {
    "VOTING": "VOTING", "VTG": "VOTING", "VOT": "VOTING",
    "SUB": "SUBORDINATE", "SUBORD": "SUBORDINATE",
    "SUBORDINATE": "SUBORDINATE",
    "NON": "NON", "NONVOTING": "NON",
    "MULTIPLE": "MULTIPLE", "MULT": "MULTIPLE",
    "RESTRICTED": "RESTRICTED", "RESTR": "RESTRICTED",
    "ADR": "ADR", "ADRS": "ADR", "ADS": "ADR", "SPONSORED": "ADR",
    "SPON": "ADR", "DEPOSITARY": "ADR", "DEPOSITORY": "ADR",
    "UNSPONSORED": "UNSPONSORED",
    "ORD": "ORDINARY", "ORDINARY": "ORDINARY",
    "PFD": "PREFERRED", "PREF": "PREFERRED", "PREFERRED": "PREFERRED",
    "SER": "SERIES", "SERIES": "SERIES",
    "UNIT": "UNIT", "UNITS": "UNIT", "UTS": "UNIT",
    "WARRANT": "WARRANT", "WARRANTS": "WARRANT", "WTS": "WARRANT",
    "WT": "WARRANT",
    "RIGHT": "RIGHT", "RIGHTS": "RIGHT", "RTS": "RIGHT",
    "NEW": "NEW",
}
_CLASS_WORDS = frozenset(("CL", "CLASS"))
# The voting-rights designators a broker may leave out after the class
# letter (Questrade cuts "CLASS B SUBORDINATE VOTING" to "CL B").
_VOTING = frozenset(("~VOTING", "~SUBORDINATE", "~NON", "~MULTIPLE",
                     "~RESTRICTED"))
# The depositary / ordinary form of a share: one broker may leave it out
# (IB "QZX INC" for Questrade's "QZX INC ORDINARY SHARES").
_FORM_MARKS = frozenset(("~ADR", "~ORDINARY", "~UNSPONSORED"))
# A name the export cut right after CL / CLASS: its class is unknown, so
# it agrees only with a name cut the same way.
_CUT_CLASS = "~CLASS?"
# A name whose cut-off boilerplate held a share designator (name_tokens):
# it agrees only on designators both names state.
_CUT_MARK = "~!"

# The width Questrade's website export cuts a description at (the rows of
# a transferred-in code): a description of exactly this many characters,
# all of it the security name, may be cut off (questrade.scan_code_uses).
QT_DESC_WIDTH = 60

_WORD_RE = re.compile(r"[A-Z0-9]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_INTERNAL_CODE_RE = re.compile(r"^[A-Z]\d+$")


def _dealer_phrases() -> Tuple[Tuple[str, ...], ...]:
    """The multi-word broker names a transfer-in description may carry
    after the security ("QZX INC INTERACTIVE BROKERS LLC 146.16"): the
    supported parsers' display names — no list of dealers is kept."""
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    out = []
    for v in DISPLAY_NAMES.values():
        ws = tuple(_WORD_RE.findall(str(v).upper()))
        if len(ws) >= 2:
            out.append(ws)
    return tuple(out)


def _dealer_at(words: List[str], i: int) -> bool:
    """A broker's name starts at words[i] (its last word may be cut short
    when it ends the description)."""
    for ph in _dealer_phrases():
        n = 0
        for k, w in enumerate(ph):
            j = i + k
            if j >= len(words):
                break
            if words[j] == w or (j == len(words) - 1 and len(words[j]) >= 3
                                 and w.startswith(words[j])):
                n += 1
            else:
                break
        if n >= 2:
            return True
    return False


def _keep_domicile(m: "re.Match") -> str:
    seg = _WORD_RE.findall(m.group(1))
    return " " + m.group(1) if any(w in _DESIGNATORS for w in seg) else " "


def name_words(text: str) -> Tuple[Tuple[str, ...], bool]:
    """(words, chopped): a security name read as upper-case words with
    the abbreviations folded (ABBREVIATIONS, "N V" -> NV, "&" -> AND),
    IB's domicile suffix ("/CAYMAN ISL") dropped and the broker
    boilerplate cut with everything after it — the depositary's
    "REPSTG 5 COM ...", a transfer wording ("TRANSFER IN ..."), a broker's
    name ("INTERACTIVE BROKERS ..."). `chopped`: something was cut off
    the end, so the name no longer ends where the export cut it. Every
    word is kept in order (the generic words too): the truncation check
    compares word sequences.

    No broker's event wording is cut here: a Questrade row description
    is read through questrade_name first (its dividend / transfer / trade
    wording is Questrade's own; another broker's name is the name)."""
    words, chopped, _tail = _name_words_tail(text)
    return words, chopped


def _name_words_tail(text: str
                     ) -> Tuple[Tuple[str, ...], bool, Tuple[str, ...]]:
    """name_words plus the words cut off the end (the boilerplate and
    everything after it)."""
    # Whitespace collapsed FIRST: the patterns below are quadratic on a
    # long run of spaces (40,000 took over a minute).
    s = " ".join(_CONTROL_RE.sub(" ", str(text or "").upper()).split())
    for rx, rep in _PHRASES:
        s = rx.sub(rep, s)
    s = _DOMICILE_RE.sub(_keep_domicile, s)
    words = [ABBREVIATIONS.get(w, w) for w in _WORD_RE.findall(s)]
    for i in range(1, len(words)):
        w = words[i]
        if (w in _REPRESENTING
                or (w in _TRANSFER_WORDS
                    and (i == len(words) - 1
                         or words[i + 1] in _TRANSFER_NEXT))
                or _dealer_at(words, i)):
            return tuple(words[:i]), True, tuple(words[i:])
    return tuple(words), False, ()


# A Questrade row describes the event after the security's name: "<NAME>
# CASH DIV ON 49 SHS REC 09/21/26 PAY 09/23/26", "<NAME> SUBST PAY ON 41
# SHS ... IN LIEU OF DIVIDEND". The parser's own description key
# (questrade._get_desc_key: dividend, distribution, tax, DRIP, split,
# transfer and trade wording) cuts that wording off, so the name compares
# as the security's name; a substitute payment's wording is added here
# (the parser keeps it in its key on purpose). Questrade's wording only:
# another broker's name is never cut by these patterns.
_SUBST_PAY_RE = re.compile(r"\s+SUBST(?:ITUTE)?\s+PAY(?:MENT)?\b.*$")
# The one cut of the parser's key that is not event wording: "<NAME>
# COMMON STOCK ..." — what follows may still name the share ("QZCO INC
# COMMON STOCK CLASS C"), so its designators are carried into the name.
_COMMON_STOCK_RE = re.compile(r"\s+COMMON\s+STOCK\b(.*)$")
_CLASS_LETTER_RE = re.compile(r"\b(?:CL|CLASS)\s+([A-Z])\b")


def questrade_name(desc: str) -> str:
    """The security name a Questrade row description gives: the parser's
    description key (questrade._get_desc_key — its event wording cut),
    without a substitute payment's wording, and with any share designator
    the key's "COMMON STOCK ..." cut dropped carried back ("QZCO INC
    COMMON STOCK CLASS C" is "QZCO INC CL C", never "QZCO INC")."""
    from taxjson.lib.brokerages.questrade import (_DESC_NOISE_RES,
                                                   _get_desc_key)
    d = " ".join(str(desc or "").split())
    key = " ".join(_SUBST_PAY_RE.sub("", _get_desc_key(d)).split())
    if not key:
        return key
    # The tail the COMMON STOCK cut removes, once the event wording
    # (cut first, as the key cuts it) is gone.
    rest = _SUBST_PAY_RE.sub("", d.upper())
    for rx in _DESC_NOISE_RES:
        if "COMMON STOCK" in rx.pattern:
            break
        rest = rx.sub("", rest)
    m = _COMMON_STOCK_RE.search(rest)
    if not m:
        return key
    tail = m.group(1)
    carried: List[str] = []
    for c in _CLASS_LETTER_RE.findall(tail):
        carried += ["CL", c]
    carried += [w for w in _WORD_RE.findall(_CLASS_LETTER_RE.sub(" ", tail))
                if w in _DESIGNATORS]
    if carried and not (_marks(_tokens(carried))
                        <= _marks(_tokens(_WORD_RE.findall(key)))):
        key = f"{key} {' '.join(carried)}"
    return key
    m = _COMMON_STOCK_RE.search(d.upper())
    if not m:
        return key
    tail = m.group(1)
    carried: List[str] = []
    for c in _CLASS_LETTER_RE.findall(tail):
        carried += ["CL", c]
    for w in _WORD_RE.findall(_CLASS_LETTER_RE.sub(" ", tail)):
        if w in _DESIGNATORS:
            carried.append(w)
    have = set(_WORD_RE.findall(key))
    extra = [w for w in carried if w not in have or w == "CL"]
    if extra and " ".join(extra) not in key:
        key = f"{key} {' '.join(extra)}"
    return key


# Spellings of one word folded for the exact comparison (exact_name):
# the same word, never another corporate form.
_EXACT_FOLD = {"CL": "CLASS", "INCORPORATED": "INC", "LIMITED": "LTD"}
# The generic share words exact_name sets aside ("QZX INC COMMON SHARES"
# is "QZX INC"); every other word counts, the corporate form included.
_SHARE_WORDS = frozenset("COMMON COM STOCK STK SHARES SHARE SHS SHRS"
                         .split())


def exact_name(text: str) -> Tuple[str, ...]:
    """A security name for an EXACT comparison (cross-listing joins,
    tax-logic CA-XLIST-01 / US-XLIST-01): name_words (case, punctuation,
    the abbreviation table, broker boilerplate), the generic share words
    (COMMON, STOCK, SHARES ...) set aside and one spelling per word
    (CL / CLASS, SUB / SUBORDINATE, SPONSORED ADR / ADR, INCORPORATED /
    INC) — every other word kept in order: the corporate form (LP, CORP,
    TRUST, FUND are different), each share designator, and words such
    as HEDGED or OF. Boilerplate cut off with a designator in it is kept
    (it names the share)."""
    words, _chopped, cut_off = _name_words_tail(text)
    if _cut_designators(cut_off):
        words = words + cut_off
    out: List[str] = []
    for w in words:
        if w in _SHARE_WORDS:
            continue
        w = _EXACT_FOLD.get(w, w)
        w = _DESIGNATORS.get(w, w)
        if not out or out[-1] != w:
            out.append(w)
    return tuple(out)


def exact_marks(key: Iterable[str]) -> frozenset:
    """The share designators, class letters and corporate-form words of
    an exact_name key: what says WHICH security of an issuer it is."""
    return frozenset(w for w in key if w in _DESIGNATORS.values()
                     or w in _FORM or (len(w) == 1 and not w.isdigit()))


def _is_mark_word(w: str) -> bool:
    return w in _DESIGNATORS or (len(w) == 1 and not w.isdigit())


def _tokens(words: Iterable[str]) -> Tuple[str, ...]:
    core: List[str] = []
    marks = set()
    for w in words:
        if w in _GENERIC or w in _FORM or w in _CLASS_WORDS:
            continue
        if w in _DESIGNATORS:
            marks.add("~" + _DESIGNATORS[w])
        elif len(w) == 1 and not w.isdigit():
            marks.add("~" + w)
        elif w not in core:
            core.append(w)
    return tuple(core) + tuple(sorted(marks))


def name_tokens(text: str, cut: bool = False) -> Tuple[str, ...]:
    """A security name normalised for comparing (name_words): the words
    that name the company, in order, then its share designators sorted
    (each marked "~": the class letter, VOTING / SUBORDINATE / NON /
    MULTIPLE, ADR, ORDINARY, PREFERRED, UNIT, WARRANT, RIGHT, NEW).
    Generic share words, the transfer wording and corporate-form words
    (INC, CORP, LTD ...) are dropped; punctuation, case and the
    abbreviation (RES / RESOURCES) never count.

    `cut`: the export may have cut the name off at its width — the last
    word may be a fragment, so it is left out (unless it is a designator
    or a lone letter); a name cut right after CL / CLASS has an unknown
    class (it then agrees with no name that is not cut the same way)."""
    words, chopped, cut_off = _name_words_tail(text)
    tail: Tuple[str, ...] = ()
    if _cut_designators(cut_off):
        # A share designator went with the boilerplate cut off ("ADS
        # EACH RPRSNTNG ONE CL A ORD"): this name never agrees on the
        # strength of a designator stated by the other name only.
        tail = (_CUT_MARK,)
    if cut and not chopped and words:
        if words[-1] in _CLASS_WORDS:
            tail += (_CUT_CLASS,)
        elif not _is_mark_word(words[-1]):
            words = words[:-1]
    toks = _tokens(words)
    if tail:
        toks = _core(toks) + tuple(sorted(set(_marks(toks)) | set(tail)))
    return toks


def _cut_designators(words: Iterable[str]) -> bool:
    """A share designator among the words name_words cut off: a
    designator word, or a class letter after CL / CLASS."""
    ws = list(words)
    return any(w in _DESIGNATORS
               or (w in _CLASS_WORDS and k + 1 < len(ws)
                   and len(ws[k + 1]) == 1 and not ws[k + 1].isdigit())
               for k, w in enumerate(ws))


def _core(tokens: Iterable[str]) -> Tuple[str, ...]:
    return tuple(t for t in tokens if not t.startswith("~"))


def _marks(tokens: Iterable[str]) -> frozenset:
    return frozenset(t for t in tokens if t.startswith("~"))


def _strong(tokens: Iterable[str]) -> bool:
    return any(len(t) >= 3 and not t.isdigit() and not t.startswith("~")
               for t in tokens)


def _n_strong(tokens: Iterable[str]) -> int:
    return sum(1 for t in tokens
               if len(t) >= 3 and not t.isdigit() and not t.startswith("~"))


def _companies_match(a: Tuple[str, ...], b: Tuple[str, ...]) -> bool:
    """The company words of two names agree (transfer pairing): the same
    words; or the same first word with one name's words all in the
    other's and at least two strong words shared. A one-word name
    matches only itself ("BANK" never pairs "BANK OF QZLAND")."""
    ca, cb = _core(a), _core(b)
    if not ca or not cb or not (_strong(ca) and _strong(cb)):
        return False
    sa, sb = set(ca), set(cb)
    if sa == sb:
        return True
    if len(ca) == 1 or len(cb) == 1 or ca[0] != cb[0]:
        return False
    return (sa <= sb or sb <= sa) and _n_strong(sa & sb) >= 2


def _shown(marks: Iterable[str]) -> str:
    return " ".join(sorted(m[1:] for m in marks
                           if m != _CUT_MARK)) or "none"


def _share_agrees(a: Tuple[str, ...], b: Tuple[str, ...],
                  mode: str = "pairing") -> Tuple[bool, str]:
    """(agree, why) for the share designators of two names.

    name_only: they must be EQUAL. pairing (the quantity and date already
    agree): designators are read in three groups —
    * class (the class letter and the voting words): if both names state
      one they must agree (a name may leave out the voting words when
      both name the same letter); a lone class LETTER on one side only
      is not a conflict, voting words on one side only are;
    * form (ADR / ORDINARY / UNSPONSORED): if both state one they must
      agree; one side only is not a conflict;
    * the rest (NEW, PREFERRED, SERIES, UNIT, WARRANT, RIGHT, a class cut
      off by the export) must be equal."""
    ma, mb = _marks(a), _marks(b)
    if ma == mb:
        return True, ""
    cut_off = _CUT_MARK in ma or _CUT_MARK in mb
    ma, mb = ma - {_CUT_MARK}, mb - {_CUT_MARK}
    if ma == mb:
        return True, ""
    if mode == "name_only":
        return False, (f"the share designators differ ({_shown(ma)} vs "
                       f"{_shown(mb)})")
    ca = {m for m in ma if len(m) == 2 or m in _VOTING}
    cb = {m for m in mb if len(m) == 2 or m in _VOTING}
    fa, fb = ma & _FORM_MARKS, mb & _FORM_MARKS
    ra, rb = ma - ca - fa, mb - cb - fb
    if ra != rb:
        return False, (f"another issue or kind of security "
                       f"({_shown(ra)} vs {_shown(rb)})")
    notes: List[str] = []
    if ca and cb:
        la = {m for m in ca if m not in _VOTING}
        lb = {m for m in cb if m not in _VOTING}
        if ca != cb and not (la and la == lb and (ca ^ cb) <= _VOTING):
            return False, (f"another share class ({_shown(ca)} vs "
                           f"{_shown(cb)})")
    elif ca or cb:
        one = ca or cb
        if one & _VOTING or len(one) != 1:
            return False, (f"another share class ({_shown(ca)} vs "
                           f"{_shown(cb)})")
        notes.append(f"class {_shown(one)} named on one side only")
    if fa and fb:
        if fa != fb:
            return False, (f"another share form ({_shown(fa)} vs "
                           f"{_shown(fb)})")
    elif fa or fb:
        notes.append(f"{_shown(fa or fb)} named on one side only")
    if notes and cut_off:
        return False, ("a share designator named on one side only, and "
                       "the other name's designators were cut off with "
                       "its broker wording")
    return True, "; ".join(notes)


def _relation(a: Tuple[str, ...], b: Tuple[str, ...]) -> Tuple[str, str]:
    """(name_relation, why) for transfer pairing."""
    if not _companies_match(a, b):
        return "", "different companies"
    ok, why = _share_agrees(a, b, "pairing")
    return ("same", why) if ok else ("class", why)


def name_relation(a: Tuple[str, ...], b: Tuple[str, ...]) -> str:
    """How two normalised names relate (transfer pairing): "same" (one
    company, the same share), "class" (one company, but the share class /
    voting / ADR / NEW designators conflict — e.g. class A vs class C), or
    "" (different companies)."""
    return _relation(a, b)[0]


def names_match(a: Tuple[str, ...], b: Tuple[str, ...]) -> bool:
    """Two normalised names name the same share (transfer pairing)."""
    return name_relation(a, b) == "same"


def _truncation_agrees(short: str, long: str, cut: bool) -> bool:
    """`short` is `long` cut off by the export: `cut` — the caller's
    evidence that the export cut `short` at its width (the row's
    description is exactly QT_DESC_WIDTH characters, all of it the name:
    desc_cut) — is required, whatever the last word looks like (a
    complete word that starts a longer one, PARTNERS / PARTNERSHIP, is
    not a cut by itself); then its words are the first words of `long`,
    its last word may be a fragment of the next one, at least three
    strong words come before the cut, and the part cut off holds no
    share designator."""
    if not cut:
        return False
    sw, chopped = name_words(short)
    lw, _ = name_words(long)
    if chopped or not sw or len(sw) > len(lw):
        return False
    k = len(sw) - 1
    head, last = sw[:k], sw[k]
    if tuple(lw[:k]) != head or not lw[k].startswith(last):
        return False
    whole = lw[k] == last
    if whole and len(sw) == len(lw):
        return False                      # equal: not a truncation
    kept = sw if whole else head
    if _n_strong(_core(_tokens(head))) < 3:
        return False
    return _marks(_tokens(kept)) == _marks(_tokens(lw))


def names_agree(a: str, b: str, mode: str = "pairing", *,
                a_cut: bool = False, b_cut: bool = False,
                same_broker: bool = False) -> Tuple[bool, str]:
    """(agree, reason): do two security names, as two exports write them,
    name the same share? The one test lib/symbol_codes applies; reusable
    wherever two brokers' names of a security are compared.

    Both names are normalised alike (name_words / name_tokens: case,
    punctuation, generic share words, corporate-form words, the
    abbreviation table, broker boilerplate). Then:

    mode="pairing" — the names of the two legs of ONE transfer that
    already agree by quantity and date (strong evidence on their own):
    the company words agree (the same words, or one name's words all in
    the other's with the same first word and two strong words shared; a
    one-word name only itself), and no share designator CONFLICTS — both
    names stating different classes (A vs C), voting rights (subordinate
    vs multiple), forms (ADR vs ORDINARY) refuse; NEW, PREFERRED, UNIT,
    WARRANT, RIGHT, SERIES must be equal; a class letter or ORDINARY /
    ADR stated by one name only does not refuse (said in the reason).

    mode="name_only" — no transfer pairs the names: the normalised names
    must be EQUAL, designators included; or, with `same_broker` (both
    descriptions written by the same broker), one is the other cut off by
    the export (`a_cut` / `b_cut`: the evidence that the export cut
    that name at its width — required; a last word that merely starts a
    longer word is no evidence) with three strong words before the cut
    and no designator cut off.
    Uniqueness (one listing only) is the caller's test.

    The reason is "" never: "same company and share" (plus any one-sided
    designator note) when they agree, else why not."""
    if mode not in ("pairing", "name_only"):
        raise ValueError(f"names_agree: unknown mode {mode!r}")
    if mode == "pairing":
        rel, why = _relation(name_tokens(a, cut=a_cut),
                             name_tokens(b, cut=b_cut))
        if rel == "same":
            return True, ("same company and share" + (f"; {why}"
                                                       if why else ""))
        return False, why
    ta, tb = name_tokens(a), name_tokens(b)
    if ta and ta == tb and _strong(ta):
        return True, "same company and share"
    if same_broker:
        if (_truncation_agrees(a, b, a_cut)
                or _truncation_agrees(b, a, b_cut)):
            return True, ("same company and share; one description is the "
                          "other cut off by the export")
    if not _companies_match(ta, tb):
        return False, "different companies"
    return False, _share_agrees(ta, tb, "name_only")[1] or (
        "the names are not equal word for word")


def desc_cut(desc: str, key: str) -> bool:
    """The export may have cut this description off inside the security
    name: it is exactly QT_DESC_WIDTH characters long and all of it is
    the name (`key`: the name read from it — no dividend / transfer
    wording after it)."""
    d = " ".join(str(desc or "").split())
    return len(d) == QT_DESC_WIDTH and bool(key) and key == d.upper()


def is_code(symbol: str) -> bool:
    """A broker-internal code (one letter + digits), with or without a
    listing suffix."""
    root = str(symbol or "").upper().split(".")[0]
    return bool(_INTERNAL_CODE_RE.match(root))


def _plain_listing(symbol: str) -> bool:
    """A share listing (ROOT.SUFFIX), not an option / future / code."""
    from taxjson.lib.ticker_map_suggest import _TOKEN_RE
    s = str(symbol or "")
    # One ticker.map token (no '#', no Unicode line separator): the
    # symbol is written into a map line (a join, a suggestion).
    return (bool(s) and bool(_TOKEN_RE.fullmatch(s)) and not s.startswith("F:")
            and "." in s and not is_code(s)
            and not re.search(r"\d{6}[CP]\d", s))


def _d(s: Any) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


# ------------------------------------------------------------ inputs

@dataclass
class CodeUse:
    """One internal code in the receiving account's exports that the
    account's own rows do not resolve."""
    code: str
    name: str                                   # description key
    currencies: List[str] = field(default_factory=list)
    # (date, quantity) of each transfer-in row of the code
    arrivals: List[Tuple[str, float]] = field(default_factory=list)
    rows: int = 0
    # the description the name comes from may be cut off at the export's
    # width (QT_DESC_WIDTH): its last word may be a fragment
    name_cut: bool = False


@dataclass
class OutLeg:
    """An outgoing transfer in another broker's export of the project."""
    account: str
    broker: str
    symbol: str
    date: str
    quantity: float                             # shares sent (> 0)
    currency: str
    names: List[Tuple[str, ...]]


@dataclass
class NameEntry:
    symbol: str
    tokens: Tuple[str, ...]
    shown: str                                  # the name as written
    account: str
    broker: str
    cut: bool = False                           # see CodeUse.name_cut


def _broker_name(broker: str) -> str:
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    b = str(broker or "")
    if b.startswith("generic-"):
        return b[len("generic-"):]
    return DISPLAY_NAMES.get(b, b or "broker")


def _row_name(t: Dict[str, Any], broker: str) -> str:
    """The security name a parsed row carries: IB's Financial Instrument
    name, else the description of a broker whose descriptions name the
    company (an IB description is the transfer kind or the raw
    symbol)."""
    nm = str(t.get("security_name") or "").strip()
    if nm:
        return nm
    if broker == "ib":
        return ""
    desc = str(t.get("description") or "").strip()
    if broker == "questrade":
        return questrade_name(desc)
    return desc


def _load(p: Path) -> Optional[Dict[str, Any]]:
    """A JSON object from `p`, else None (missing, unreadable, not JSON,
    nested too deep to parse, not an object)."""
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) else None


def _parser_ids() -> List[str]:
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    return sorted(DISPLAY_NAMES)


def project_evidence(cache: Path, accounts: Iterable[str], *,
                     receiving: Tuple[str, str]
                     ) -> Tuple[List[OutLeg], List[NameEntry]]:
    """(outgoing transfers, known names) from every account's parsed
    exports in work/ (`accounts`: the configured equity account names).
    The receiving (account, broker) group is left out, and so are
    Questrade's own rows of every account (their symbols may be this
    very inference, written back), except their real tickers' names from
    rows that are not themselves inferred (`<acct>_symbol_codes.state`
    lists those)."""
    outs: List[OutLeg] = []
    names: List[NameEntry] = []
    ids = _parser_ids()
    for acct in sorted(set(accounts)):
        files: List[Tuple[str, Path, bool]] = []
        for b in ids:
            files.append((b, cache / f"{acct}_{b}.json", False))
            files.append((b, cache / f"{acct}_{b}_transfers.json", True))
        for p in sorted(cache.glob(f"{acct}_generic-*.json")):
            stem = p.name[len(acct) + 1:-len(".json")]
            if stem.endswith("_transfers"):
                files.append((stem[:-len("_transfers")], p, True))
            else:
                files.append((stem, p, False))
        _st = read_state(cache / f"{acct}{SUFFIX}")
        inferred = {str(r.get("symbol") or "").upper()
                    for r in (_st.get("resolved") or {}).values()}
        for broker, p, sidecar in files:
            if (acct, broker) == tuple(receiving):
                continue
            if broker in ("coinbase", "kraken"):
                continue
            doc = _load(p)
            if doc is None:
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
                if not _plain_listing(sym):
                    continue
                if broker == "questrade" and sym in inferred:
                    continue
                nm = _row_name(t, broker)
                toks = name_tokens(nm)
                if toks:
                    # One line of text: it is quoted in the record and
                    # a warning (a control character shown escaped).
                    names.append(NameEntry(
                        sym, toks, _CONTROL_RE.sub(
                            lambda m: "\\x%02x" % ord(m.group(0)),
                            " ".join(nm.split())), acct, broker,
                        cut=(broker == "questrade" and desc_cut(
                            str(t.get("description") or ""), nm))))
                if (t.get("action") != "TRANSFER" or broker == "questrade"):
                    continue
                try:
                    q = float(t.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    continue
                if q >= -1e-9:
                    continue
                outs.append(OutLeg(acct, broker, sym,
                                   str(t.get("date") or "")[:10], -q,
                                   str(t.get("currency") or "").upper(),
                                   [toks] if toks else []))
    # An outgoing leg with no name of its own takes the names its
    # account's rows give the same symbol (IB: the trades' instrument
    # names).
    by = {}
    for n in names:
        by.setdefault((n.account, n.broker, n.symbol), set()).add(n.tokens)
    for o in outs:
        extra = sorted(by.get((o.account, o.broker, o.symbol), ()))
        o.names = sorted(set(o.names) | set(extra))
    outs.sort(key=lambda o: (o.date, o.account, o.broker, o.symbol,
                             o.quantity))
    return outs, names


# ------------------------------------------------------------ inference

def _suggest(code_listing: Callable[[str, str], str], code: str,
             listing: str, currencies: List[str],
             listing_ok: Callable[[str, str], bool]) -> str:
    """The ticker.map line that would book `code` as `listing`."""
    cur = next((c for c in currencies if listing_ok(listing, c)),
               currencies[0] if currencies else "")
    return f"GLOBAL {code_listing(code, cur)} {listing}"


def _default_code_listing(code: str, cur: str) -> str:
    from taxjson.lib.brokerages.questrade import QuestradeBrokerage
    return QuestradeBrokerage().apply_currency_suffix(code, cur)


def resolve(uses: Iterable[CodeUse], outs: List[OutLeg],
            names: List[NameEntry], *,
            listing_ok: Callable[[str, str], bool],
            mapped: Callable[[str], bool] = lambda _c: False,
            code_listing: Optional[Callable[[str, str], str]] = None,
            broker: str = "questrade"
            ) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{"resolved": {code: {...}}, "unresolved": {code: {...}},
    "mapped": {code: {...}}} for the receiving account's codes.
    `listing_ok(listing, currency)`: the listing is the one the receiving
    parser books a row of that currency under (a CAD row never resolves
    to a .US listing). `mapped(code)`: a ticker.map rule names the code
    (any rule: a rename, DELETE, DISTINCT, a dated RENAME) — never
    inferred, recorded under "mapped". `code_listing(code, currency)`:
    the code's own listing, for the GLOBAL line an unresolved code's
    warning suggests. `broker`: the receiving parser — a name cut off by
    its export is matched only to that broker's own descriptions."""
    code_listing = code_listing or _default_code_listing
    uses = sorted(uses, key=lambda u: u.code)
    resolved: Dict[str, Dict[str, Any]] = {}
    unresolved: Dict[str, Dict[str, Any]] = {}
    mapped_out: Dict[str, Dict[str, Any]] = {}
    # code -> [(arrival, [candidate legs])]
    pairs: Dict[str, List[Tuple[Tuple[str, float], List[OutLeg]]]] = {}
    # legs whose quantity and date pair but whose names name another
    # share of the same company (class A vs class C, ordinary vs ADR)
    siblings: Dict[str, List[OutLeg]] = {}
    loose: Dict[str, List[OutLeg]] = {}
    # id(leg) -> the one-sided designators the pairing let through
    notes: Dict[int, str] = {}
    for u in uses:
        if mapped(u.code):
            mapped_out[u.code] = {"how": "ticker.map",
                                  "evidence": "ticker.map rule"}
            continue
        toks = name_tokens(u.name, cut=u.name_cut)
        pairs[u.code] = []
        for d, q in sorted(u.arrivals):
            dd = _d(d)
            cands, near = [], []
            tolerated: List[OutLeg] = []
            n_sib = 0
            for o in outs:
                od = _d(o.date)
                if dd is None or od is None:
                    continue
                gap = (dd - od).days
                if not (-PAIR_DAYS_AFTER <= gap <= PAIR_DAYS_BEFORE):
                    continue
                if abs(o.quantity - q) > 1e-6:
                    continue
                if not any(listing_ok(o.symbol, c) for c in u.currencies):
                    continue
                rels = [_relation(toks, n) for n in o.names]
                rel = {r for r, _w in rels}
                if "same" in rel:
                    cands.append(o)
                    if all(w for r, w in rels if r == "same"):
                        notes[id(o)] = min(w for r, w in rels
                                           if r == "same")
                        tolerated.append(o)
                elif "class" in rel:
                    siblings.setdefault(u.code, []).append(o)
                    n_sib += 1
                else:
                    near.append(o)
            if tolerated and len(cands) + n_sib + len(near) > 1:
                # A designator stated by one name only is let through
                # only when the leg is the arrival's ONLY quantity-and-
                # date partner: with another leg in the window it may
                # be another share of the company — not inferred.
                for o in tolerated:
                    cands.remove(o)
                    siblings.setdefault(u.code, []).append(o)
            pairs[u.code].append(((d, q), cands))
            loose.setdefault(u.code, []).extend(near)
    # One outgoing transfer pairs with one code.
    claimed: Dict[int, set] = {}
    for code, arr in pairs.items():
        for _a, cands in arr:
            for o in cands:
                claimed.setdefault(id(o), set()).add(code)
    for u in uses:
        if u.code not in pairs:
            continue
        arr = pairs[u.code]
        listings = set()
        conflict = False
        legs: List[OutLeg] = []
        for _a, cands in arr:
            ls = {o.symbol for o in cands}
            if len(ls) > 1:
                conflict = True
            listings |= ls
            legs += cands
        sib = sorted({o.symbol for o in siblings.get(u.code, [])}
                     - listings)
        shared = sorted({c for o in legs for c in claimed[id(o)]} - {u.code})
        if legs and (conflict or len(listings) > 1 or shared or sib):
            if sib:
                why = (f"it pairs with transfers out of "
                       f"{', '.join(sorted(listings | set(sib)))} by "
                       f"quantity and date, and their names differ only in "
                       f"the share class or form")
            elif len(listings) > 1:
                why = (f"it pairs with transfers out of "
                       f"{', '.join(sorted(listings))}")
            else:
                why = (f"the same transfer out also pairs with "
                       f"{', '.join(shared)}")
            unresolved[u.code] = {"reason": "ambiguous",
                                  "candidates": sorted(listings | set(sib)),
                                  "detail": why}
            continue
        if legs:
            o = sorted(legs, key=lambda x: (x.date, x.account))[0]
            resolved[u.code] = {
                "symbol": o.symbol, "currency": o.currency or
                (u.currencies[0] if u.currencies else ""),
                "how": "transfer",
                "evidence": (f"paired with the {_broker_name(o.broker)} "
                             f"transfer out of {o.quantity:g} on {o.date}, "
                             f"account {o.account}"
                             + (f"; {notes[id(o)]}" if id(o) in notes
                                else "")),
            }
            continue
        if sib:
            o = sorted(siblings[u.code], key=lambda x: (x.date, x.symbol))[0]
            line = _suggest(code_listing, u.code, o.symbol, u.currencies,
                            listing_ok)
            unresolved[u.code] = {
                "reason": "class_differs", "candidates": sib,
                "detail": (f"the {_broker_name(o.broker)} transfer out of "
                           f"{o.quantity:g} {o.symbol} on {o.date} pairs by "
                           f"quantity and date, but its name is another "
                           f"share class or form of the company — add "
                           f"`{line}` to ticker.map if it is the same "
                           f"share")}
            continue
        # 2. name match — only for a code with no transfer-in at all
        # (a transfer-in that pairs with nothing is not identified by
        # name: the near miss is named instead). The normalised names
        # must be EQUAL, share designators included, and name exactly
        # one listing; anything less is only suggested.
        toks = name_tokens(u.name)
        hits: Dict[str, NameEntry] = {}
        cuts: Dict[str, NameEntry] = {}
        alike: Dict[str, NameEntry] = {}
        if _strong(toks):
            for n in sorted(names, key=lambda n: (n.symbol, n.account,
                                                  n.broker)):
                if not any(listing_ok(n.symbol, c) for c in u.currencies):
                    continue
                if n.tokens == toks:
                    hits.setdefault(n.symbol, n)
                elif n.broker == broker and names_agree(
                        u.name, n.shown, "name_only", a_cut=u.name_cut,
                        b_cut=n.cut, same_broker=True)[0]:
                    # one description is the other cut off by the
                    # same broker's export
                    cuts.setdefault(n.symbol, n)
                elif (name_relation(toks, n.tokens)
                      or set(_core(toks)) == set(_core(n.tokens))):
                    alike.setdefault(n.symbol, n)
        if not hits and len(cuts) == 1 and not u.arrivals:
            n = next(iter(cuts.values()))
            cur = next((c for c in u.currencies
                        if listing_ok(n.symbol, c)), "")
            resolved[u.code] = {
                "symbol": n.symbol, "currency": cur, "how": "name",
                "evidence": (f"name match: {n.shown!r} on "
                             f"{_broker_name(n.broker)} rows of account "
                             f"{n.account}, one description cut off by "
                             f"the export"),
            }
            continue
        if not hits and len(cuts) > 1:
            hits = cuts
        elif not hits:
            for sym_, n_ in cuts.items():
                alike.setdefault(sym_, n_)
        if len(hits) == 1 and not u.arrivals:
            n = next(iter(hits.values()))
            cur = next((c for c in u.currencies
                        if listing_ok(n.symbol, c)), "")
            resolved[u.code] = {
                "symbol": n.symbol, "currency": cur, "how": "name",
                "evidence": (f"name match: {n.shown!r} on "
                             f"{_broker_name(n.broker)} rows of account "
                             f"{n.account}"),
            }
            continue
        info: Dict[str, Any] = {"reason": ("ambiguous" if len(hits) > 1
                                           else "no_evidence")}
        near = sorted({(o.symbol, o.date, o.quantity, o.broker)
                       for o in loose.get(u.code, [])})
        if len(hits) > 1:
            info["candidates"] = sorted(hits)
            info["detail"] = (f"its name matches "
                              f"{', '.join(sorted(hits))}")
        elif near:
            s_, d, q, b = near[0]
            info["candidates"] = sorted({x[0] for x in near})
            line = _suggest(code_listing, u.code, s_, u.currencies,
                            listing_ok)
            info["detail"] = (f"the {_broker_name(b)} transfer out of "
                              f"{q:g} {s_} on {d} pairs by quantity and "
                              f"date, but the names do not confirm it — "
                              f"add `{line}` to ticker.map if it is the "
                              f"same security")
        elif hits or alike:
            look = hits or alike
            n = next(iter(look.values()))
            info["candidates"] = sorted(look)
            why = ("its transfer-in pairs with no transfer out in the "
                   "project" if u.arrivals else
                   "the names are not equal word for word")
            if len(look) == 1:
                line = _suggest(code_listing, u.code, n.symbol,
                                u.currencies, listing_ok)
                info["detail"] = (
                    f"looks like {n.symbol} by name ({n.shown!r} on "
                    f"{_broker_name(n.broker)} rows of account "
                    f"{n.account}), not applied: {why} — add `{line}` "
                    f"to ticker.map if right")
            else:
                info["detail"] = (
                    f"its name resembles {', '.join(sorted(look))}, not "
                    f"applied: {why}")
        unresolved[u.code] = info
    return {"resolved": resolved, "unresolved": unresolved,
            "mapped": mapped_out}


# ------------------------------------------------------------ the record

_BUCKETS = ("resolved", "unresolved", "mapped")


def state_text(account: str, result: Dict[str, Any]) -> str:
    return json.dumps({"format": FORMAT, "account": account,
                       **{k: result.get(k) or {} for k in _BUCKETS}},
                      indent=2, sort_keys=True) + "\n"


def _text_ok(v: Any) -> bool:
    return isinstance(v, str) and not _CONTROL_RE.search(v)


def state_problem(doc: Dict[str, Any]) -> Optional[str]:
    """Why a parsed record is not one this module wrote (None: it is).
    Every bucket a table of internal codes; a resolved code names a
    plain share listing with its evidence; every text one line."""
    if not _text_ok(doc.get("account", "")):
        return "the account is not a line of text"
    for key in _BUCKETS:
        v = doc.get(key, {})
        if not isinstance(v, dict):
            return f"{key!r} is not a table of codes"
        for code, r in v.items():
            if not (isinstance(code, str) and _INTERNAL_CODE_RE.match(code)):
                return f"{key!r} holds {code!r}, not an internal code"
            if not isinstance(r, dict):
                return f"{key!r} entry {code} is not a table"
            for f, x in r.items():
                if f == "candidates":
                    if not (isinstance(x, list)
                            and all(_text_ok(c) for c in x)):
                        return f"{key!r} entry {code}: bad candidates"
                elif not _text_ok(x):
                    return f"{key!r} entry {code}: {f!r} is not one line " \
                           f"of text"
    for code, r in doc.get("resolved", {}).items():
        if not _plain_listing(r.get("symbol")):
            return f"resolved entry {code}: {r.get('symbol')!r} is not a " \
                   f"share listing"
        if not r.get("evidence"):
            return f"resolved entry {code} has no evidence"
    return None


_WARNED: set = set()


def read_state(path: Optional[Path]) -> Dict[str, Any]:
    """The recorded inference ({} when none). A record that is not one
    this module wrote (not JSON, nested too deep, a wrong shape, a
    symbol that is not a plain listing, a control character) is
    ignored with ONE warning per file — never a traceback; `taxjson
    run` writes it afresh."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    doc = _load(p)
    why = None
    if doc is None:
        why = "not a JSON object"
    elif doc.get("format") != FORMAT:
        why = f"format is not {FORMAT}"
    else:
        why = state_problem(doc)
    if why is None:
        return doc
    key = str(p.resolve())
    if key not in _WARNED:
        _WARNED.add(key)
        from taxjson.lib.brokerages.base import shown_name
        from taxjson.lib.stage_msg import emit_line
        emit_line(f"warning: {shown_name(p)}: ignored ({why}) — the "
                  f"Questrade internal codes it records are not applied; "
                  f"`taxjson run` writes it afresh.")
    return {}


def codes_note(resolved: Dict[str, Dict[str, Any]]) -> str:
    """ONE line per account for the resolved codes (the .diag / .sum keep
    it whole; parse_codes_note reads it back):

    note: Questrade internal symbol codes resolved (2): X1 → AAA.US
    (paired with ...); X2 → BBB.TO (name match: ...) — inferred from the
    project's other exports; a ticker.map GLOBAL line overrides one."""
    items = [f"{c} → {r['symbol']} ({r['evidence']})"
             for c, r in sorted(resolved.items())]
    return (f"{NOTE_HEAD} ({len(items)}): " + "; ".join(items)
            + " — inferred from the project's exports (`taxjson "
              "transfers` lists them); a ticker.map GLOBAL line for the "
              "code overrides the inference.")


_ENTRY_RE = re.compile(r"([A-Z]\d+) → (\S+) \(((?:[^()]|\([^()]*\))*)\)")


def parse_codes_note(line: str) -> Dict[str, Tuple[str, str]]:
    """{code: (listing, evidence)} from a codes_note line."""
    if NOTE_HEAD not in line:
        return {}
    return {m.group(1): (m.group(2), m.group(3))
            for m in _ENTRY_RE.finditer(line)}
