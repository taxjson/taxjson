"""An export's symbol in the books' spelling: upper case (backlog TJ-4600,
TJ-4601), with the spellings each export used kept aside, so that two
spellings of one symbol that differ only in letter case are never joined
in silence (backlog TJ-4605, TJ-4606).

Every place that reads a symbol from a broker's export upper-cases it
here (`book_symbol`): the parsers' rows (lib/brokerages/base.parse_file)
and the corporate-action events (lib/corp_extract.extract_events). No
broker is known to write a symbol in lower case, but one that did would
book `zyab` and `ZYAB` as two securities, or, read upper case, join two
securities a broker spells apart only by a letter's case (a share class
in lower case, as `ZYXa` beside `ZYXA`). So each reader records the
spellings it read (`record`), and `tjs run` stops on two of them
(`collisions`, `collision_message`) until a ticker.map line says which
they are:

- `GLOBAL ZYXa.US ZYXA.US`: one security, booked ZYXA.US;
- `DISTINCT ZYXa.US ZYXA.US`: two, the lower-case spelling booked with
  its lower-case letters as a class (`class_spelling`: ZYX.A.US).

A leaf: the ticker.map reader, the parsers, the corporate-action reader
and the run all use it."""
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple
import re

# The fields of a parsed row or a corporate-action row that name a
# security.
SYMBOL_FIELDS = ('symbol', 'symbol_new', 'exercise_of')
# The metadata key of a parse's or a corporate-action read's output
# that holds the spellings it read ({SYMBOL: {spelling: [files]}}).
SPELLINGS_KEY = 'symbol_spellings'
SAME, APART = 'same', 'distinct'

# A root whose last letters are lower case after an upper-case start:
# the lower-case letters a class (ZYXa -> ZYX.A).
_CLASS_RE = re.compile(r'([A-Z0-9][A-Z0-9.]*?)([a-z]+)')


def has_lower(sym) -> bool:
    """Whether `sym` is a symbol spelled with a lower-case letter. A
    placeholder in parentheses (`(unknown parent)`: a spin-off whose
    parent no row names, which the elections compare as written) is no
    symbol: never folded nor counted."""
    return (isinstance(sym, str) and sym != sym.upper()
            and '(' not in sym)


def class_spelling(sym: str) -> Optional[str]:
    """The books' spelling of `sym` kept apart from its upper-case
    spelling (a DISTINCT line): the lower-case letters ending its root a
    class, dotted (ZYXa.US -> ZYX.A.US); None when its lower-case letters
    do not end the root after an upper-case start."""
    root, dot, rest = str(sym or '').partition('.')
    m = _CLASS_RE.fullmatch(root)
    if not m or has_lower(rest):
        return None
    return f"{m.group(1)}.{m.group(2).upper()}{dot}{rest}"


def book_symbol(sym, case: Optional[Mapping[str, str]] = None):
    """`sym` as the books spell it: upper case, or, for a spelling a
    ticker.map DISTINCT line keeps apart (`case`: {spelling: SAME or
    APART}), its class spelling. Anything not a string is returned as
    is."""
    if not has_lower(sym):
        return sym
    if case and case.get(sym) == APART:
        return class_spelling(sym) or sym.upper()
    return sym.upper()


def book_ident(value, case: Optional[Mapping[str, str]] = None):
    """A corporate action's identifying field (`source_isin`,
    `target_isin`) as the books spell it: a symbol a broker writes there
    in place of an ISIN (RBC; an Interactive Brokers parent with none),
    alone or before an `@` (`zyab@2025-03-17`), spelled as `book_symbol`
    spells it, so two copies of one event in two spellings hash alike
    (backlog TJ-4616), and a spelling a DISTINCT line keeps apart (its
    root without the listing, as RBC writes it) its class spelling, so
    two classes stay two events; an ISIN or a broker code (upper case)
    as is."""
    if not has_lower(value):
        return value
    head, at, tail = value.partition('@')
    apart = any(kind == APART and spelling.partition('.')[0] == head
                for spelling, kind in (case or {}).items())
    return ((class_spelling(head) if apart else None)
            or book_symbol(head, case)) + at + tail


def record(seen: Dict[str, Dict[str, int]], sym,
           case: Optional[Mapping[str, str]] = None) -> None:
    """Count `sym` as read, under its upper-case spelling: a spelling a
    ticker.map line names (`case`) is decided and not counted."""
    if not isinstance(sym, str) or not sym or '(' in sym:
        return
    if case and sym in case:
        return
    up = sym.upper()
    by = seen.setdefault(up, {})
    by[sym] = by.get(sym, 0) + 1


def fold_rows(rows, case: Optional[Mapping[str, str]] = None,
              seen: Optional[Dict[str, Dict[str, int]]] = None,
              given: Optional[Callable[[Mapping], bool]] = None) -> None:
    """Spell every row's symbols as the books do (`book_symbol`),
    counting what each read in `seen` when given. A row whose symbol a
    ticker.map line gave it (`given(row)`: an EXTRACT line a parser
    applies as it reads, RBC's) holds no spelling of the export's: its
    symbol is not counted."""
    for t in rows:
        for k in SYMBOL_FIELDS:
            v = t.get(k)
            if not isinstance(v, str):
                continue
            if seen is not None and not (k == 'symbol' and given
                                         and given(t)):
                record(seen, v, case)
            if has_lower(v):
                t[k] = book_symbol(v, case)


def folded(reads: Iterable[Tuple[str, Mapping[str, Mapping[str, int]]]]
           ) -> Dict[str, Dict[str, List[str]]]:
    """What an output keeps of the spellings its reads counted (`reads`:
    (where, seen) for each file read): each symbol some file read in a
    spelling with a lower-case letter, every spelling any file read it
    in, the files beside each ({SYMBOL: {spelling: [where]}}). Empty when
    every spelling was upper case (a broker's usual export: nothing is
    written)."""
    reads = list(reads)
    ups = sorted({up for _w, seen in reads for up, by in seen.items()
                  if any(has_lower(s) for s in by)})
    out: Dict[str, Dict[str, List[str]]] = {}
    for up in ups:
        for where, seen in reads:
            merge(out, {up: {s: [where] for s in seen.get(up) or {}}})
    return out


def spellings_meta(reads: Iterable[Tuple[str, Mapping[str, Mapping[str, int]]]]
                   ) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    """An output's metadata entry of the spellings its reads counted
    (`folded`): {SPELLINGS_KEY: ...}, or {} when every spelling was upper
    case."""
    got = folded(reads)
    return {SPELLINGS_KEY: got} if got else {}


def merge(into: Dict[str, Dict[str, List[str]]],
          more: Mapping[str, Mapping[str, Iterable[str]]]) -> None:
    for up, by in more.items():
        dst = into.setdefault(up, {})
        for s, ws in by.items():
            lst = dst.setdefault(s, [])
            for w in ws:
                if w not in lst:
                    lst.append(w)


def source_label(doc: Mapping) -> str:
    """The files a parse's or a corporate-action read's output (`doc`)
    was read from, by name, as `collisions` names them."""
    md = (doc or {}).get('metadata') or {}
    if md.get('source') == 'taxjson-corp-actions':
        return (", ".join(str(n) for n in md.get('statements') or ())
                + " (corporate actions)")
    return ", ".join(str(f).replace('\\', '/').rsplit('/', 1)[-1]
                     for f in md.get('input_files') or ())


def collisions(sources: Iterable[Tuple[str, Mapping]]
               ) -> Dict[str, Dict[str, List[str]]]:
    """{SYMBOL: {spelling: [where]}} of every symbol read in two or more
    spellings across `sources` ((the folder its files are in, a parse's
    or a corporate-action read's JSON)): the spellings each source kept
    (`SPELLINGS_KEY`), and, for a symbol a source did not keep, its rows'
    symbol as booked (an export that spelled it upper case)."""
    docs = list(sources)
    kept: Dict[str, Dict[str, List[str]]] = {}
    for folder, doc in docs:
        own = ((doc or {}).get('metadata') or {}).get(SPELLINGS_KEY) or {}
        merge(kept, {up: {s: [folder + w for w in ws]
                          for s, ws in by.items()}
                     for up, by in own.items()})
    if not kept:
        return {}
    for folder, doc in docs:
        own = ((doc or {}).get('metadata') or {}).get(SPELLINGS_KEY) or {}
        for t in (doc or {}).get('transactions') or ():
            for k in SYMBOL_FIELDS:
                v = t.get(k) if isinstance(t, dict) else None
                if v in kept and v not in own:
                    merge(kept, {v: {v: [folder + source_label(doc)]}})
    return {up: by for up, by in sorted(kept.items()) if len(by) > 1}


def collision_message(up: str, by: Mapping[str, Iterable[str]]
                      ) -> Tuple[str, List[str]]:
    """(the message, its detail lines) of one symbol read in two or more
    spellings (`collisions`)."""
    spellings = sorted(by, key=lambda s: (has_lower(s), s))
    said = "; ".join(f"{s} in {', '.join(by[s])}" for s in spellings)
    lowers = [s for s in spellings if has_lower(s)]
    same = " and ".join(f"`GLOBAL {s} {up}`" for s in lowers)
    apart = [s for s in lowers if class_spelling(s)]
    details = [f"taxjson reads a symbol in any letter case as one "
               f"security: it would book them all as {up}, one ACB pool. "
               f"Say in ticker.map which they are, then `tjs run`.",
               f"One security (an export spelled by hand): {same}."]
    if apart:
        details.append(
            "Two securities (a broker's class letter in lower case): "
            + " and ".join(f"`DISTINCT {s} {up}` (booked "
                           f"{class_spelling(s)})" for s in apart) + ".")
    return (f"the exports spell {up} {len(spellings)} ways, differing only "
            f"in letter case: {said}", details)


def read_sources(outputs: Iterable[Tuple[str, object]]
                 ) -> List[Tuple[str, Mapping]]:
    """(its folder, its JSON) of each (account, parse or corporate-action
    output file) of `outputs`, as `collisions` reads them; an output that
    cannot be read is left out."""
    import json
    from pathlib import Path
    docs = []
    for acct, path in outputs:
        try:
            docs.append((f"inputs/{acct}/", json.loads(
                Path(str(path)).read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return docs


def refusal(sources: Iterable[Tuple[str, Mapping]]
            ) -> Optional[Tuple[str, List[str]]]:
    """(the message, its detail lines) that stops a run whose `sources`
    (`collisions`) spell a symbol two ways that differ only in letter
    case and no ticker.map line says which they are: the first such
    symbol's, the others named after it; None when there is none."""
    found = collisions(sources)
    if not found:
        return None
    up, by = next(iter(found.items()))
    msg, details = collision_message(up, by)
    if len(found) > 1:
        details.append(f"Also spelled more than one way: "
                       f"{', '.join(list(found)[1:])}.")
    return msg, details
