"""A ticker.map file read from disk: _parse_map_file (a project's map
with its tobase.map overlay, lib/tobase_map; parsed once a process
while what it reads is unchanged, _PARSED), map_file_problems (the
lines `tjs run` refuses), load_map_file (the TickerMap, its problems
said once), _map_rules (the renames of the map a run applied and
ticker.map's DISTINCT pairs, which the loss radars, lib/loss_overrides
and lib/export_coverage read). Out of lib/ticker_map_rules, whose
parser lib/tobase_map reads while this reader read tobase_map, and
lib/xlist_loss_radar (step 8 of the architecture plan: lib's import
cycle)."""

import os
import threading
from collections import OrderedDict
from copy import copy
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from taxjson.lib import project_layout as _PL
from taxjson.lib.ticker_map_rules import (TickerMap, _parse_map_text,
                                          merge_renames, named_symbols)


# The maps this process has parsed (backlog TJ-4961): `tjs run` read its
# ticker.map about 100 times, each a full parse of some 1,900 lines with
# the tobase.map overlay. Keyed on what the parse reads (and the path,
# which only costs hits): the file's resolved path and name, its text as
# read (never its modification time: a file rewritten in the same clock
# tick at the same size is another key) and, for a ticker.map, the
# overlay's lines, problems and generated symbols. The file is read
# every time, so a file that is missing, unreadable or changed raises or
# parses as before. The parse prints nothing (load_map_file says the
# problems of every call). Least recently used out past _PARSED_MAX;
# each caller gets its own copy (_own_copy), never the cached parse.
ParsedMap = Tuple[TickerMap, List[str], List[str]]
_PARSED_MAX = 8
_PARSED: "OrderedDict[Tuple[Any, ...], ParsedMap]" = OrderedDict()
_PARSED_LOCK = threading.Lock()


def _parse_map_file(file_path: Path) -> ParsedMap:
    """_parse_map_text of the file at `file_path` (a non-UTF-8 map is a
    one-line error naming it, S053-06; a BOM is dropped). A project's
    ticker.map is read with its tobase.map (lib/tobase_map: the
    interlisted master's pairs, ticker.map winning, and in a Canadian
    project the .V / .TO spellings of its TOBASE and DISTINCT lines);
    a tobase.map line that cannot be used is a problem naming it. The
    same text with the same overlay is parsed once a process (_PARSED)."""
    from taxjson.lib.cli_diag import read_text_utf8
    text = read_text_utf8(file_path)
    file_path = Path(file_path)
    ov = None
    if file_path.name == "ticker.map":
        from taxjson.lib.tobase_map import overlay_for
        ov = overlay_for(file_path, text)
        if ov is not None and not (ov.text or ov.problems):
            ov = None
    key = (os.path.realpath(file_path), file_path.name, text,
           None if ov is None else (tuple(ov.lines), tuple(ov.problems),
                                    frozenset(ov.generated)))
    with _PARSED_LOCK:
        parsed = _PARSED.get(key)
        if parsed is not None:
            _PARSED.move_to_end(key)
    if parsed is None:
        parsed = _parse_with_overlay(text, file_path.name, ov)
        with _PARSED_LOCK:
            _PARSED[key] = parsed
            while len(_PARSED) > _PARSED_MAX:
                _PARSED.popitem(last=False)
    return _own_copy(parsed)


def _parse_with_overlay(text: str, name: str, ov: Any) -> ParsedMap:
    """(TickerMap, problems, notes) of a map's `text` with the tobase.map
    Overlay `ov` (lib/tobase_map; None: the text alone)."""
    if ov is None:
        tmap, problems, notes = _parse_map_text(text, name)
        return tmap, problems, notes
    sep = "" if not text or text.endswith("\n") else "\n"
    tmap, problems, notes = _parse_map_text(text + sep + ov.text, name)
    gen = frozenset(ov.generated)
    own = named_symbols(_parse_map_text(text, name)[0], lookups=True)
    ov_syms = {p.upper() for ln in ov.lines for p in ln.split()[1:3]}
    return (tmap._replace(generated=gen,
                          overlay_named=frozenset(ov_syms - own)),
            list(ov.problems) + problems, notes)


def _own_copy(parsed: ParsedMap) -> ParsedMap:
    """A caller's own copy of a cached parse (TickerMap, problems, notes):
    every dict, set and list in it new, so a caller that adds to what it
    got changes no other caller's map; the rest is immutable (str, tuple,
    frozenset, MappingProxyType, the frozen DatedRename)."""
    tmap, problems, notes = parsed
    return (type(tmap)._make(copy(v) if isinstance(v, (dict, set, list))
                             else v for v in tmap),
            list(problems), list(notes))


def map_file_problems(file_path: Path) -> List[str]:
    """The ticker-map lines that cannot be parsed (their rules would be
    dropped), each as `<file>:<lineno>: <message>`. `tjs run`
    refuses a map with any: a dropped TOBASE/GLOBAL/JOURNAL rule
    silently changes ACB pools and the Schedule 3 gain (S009-03)."""
    return _parse_map_file(file_path)[1]


def load_map_file(file_path: Path) -> "TickerMap":
    """Parse a keyword-prefixed ticker-map file into a TickerMap. A
    malformed line is skipped with a warning naming its line number
    (`tjs run` refuses such a map up front — map_file_problems),
    said once (lib/ticker_map.say_map_message)."""
    from taxjson.lib.ticker_map import map_problem_line, say_map_message
    tmap, problems, notes = _parse_map_file(file_path)
    for msg in problems:
        say_map_message(map_problem_line(msg))
    for msg in notes:
        say_map_message(f"warning: {msg} — skipping")
    return tmap


def _map_rules(root: Path, cache: Path
               ) -> Tuple[Dict[str, str], Set[frozenset]]:
    """(the base-stage renames of the map the run applied — the
    effective map, else ticker.map —, the DISTINCT pairs of ticker.map)."""
    renames: Dict[str, str] = {}
    distinct: Set[frozenset] = set()
    eff = cache / _PL.EFFECTIVE_MAP
    tm = _PL.ticker_map_path(root)
    for p, what in ((eff if eff.is_file() else tm, "renames"),
                    (tm, "distinct")):
        if not p.is_file():
            continue
        try:
            tmap = load_map_file(p)
        except Exception:                           # noqa: BLE001
            continue
        if what == "renames":
            try:
                renames = {k.upper(): v.upper() for k, v in
                           merge_renames(tmap, to_base=True).items()}
            except Exception:                       # noqa: BLE001
                renames = {}
        else:
            distinct = {frozenset(str(s).upper() for s in pair)
                        for pair in getattr(tmap, "distinct", ()) or ()}
    return renames, distinct
