"""A broker's corporate-action extractor run over one broker group of an
account's files (extract_events), and the renames a project's
corporate-action map makes (project_corp_map, map_renames,
project_corp_renames). bin/taxjson_corp_actions.py writes the events;
the corporate-action views and `tjs elections` read them from here. The
extractor table stays in bin/taxjson_corp_actions.py (EXTRACTORS,
extractor_for): it folds a label through the parser registry, which lib
may not reach.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from taxjson.lib.corp_actions import CorporateAction


# The fields of an event that name a security as the books spell it (the
# ISIN fields, which an extractor fills with a symbol when the export
# names no ISIN, are hashed into the event id as read: kept, so a saved
# election keeps its key).
EVENT_SYMBOL_FIELDS = ('source_symbol', 'target_symbol')
# ... and the fields hashed into the id, which an extractor fills with a
# symbol where the export names no ISIN (RBC): spelled as the books spell
# it too, the id recomputed (CorporateAction.rehash, which keeps the id
# as read for a saved election: backlog TJ-4616).
EVENT_IDENT_FIELDS = ('source_isin', 'target_isin')


def extract_events(extractor, csv_paths: List[Path],
                   account: Optional[str] = None,
                   renames: Optional[dict] = None,
                   symbol_codes: Optional[Path] = None,
                   case: Optional[Mapping[str, str]] = None,
                   spellings: Optional[List[Tuple[str, Dict]]] = None
                   ) -> List[CorporateAction]:
    """Run `extractor` over every file of one broker group of an
    account. Extractors that resolve securities by name (Questrade and
    RBC spin-off parents, RBC merger placeholders) see ALL the group's
    files as context: the parent was usually bought in another export.
    `symbol_codes`: the account's internal-code record (work/<acct>
    _symbol_codes.state), for an extractor that books a chain under
    a broker's internal code (Questrade). The one extraction definition
    for the CLI, `tjs elections` and `tjs spinoffs`, so all three
    compute the same event ids and targets.

    The extractors read the export's cells and descriptions themselves,
    not through a parser's parse_file: each event's symbols are spelled
    as the books spell them here (lib/symbol_case.book_symbol: upper
    case; `case`, the map's case-pair lines), so a spin-off of a `zyab`
    cell is ZYAB's (backlog TJ-4605). `spellings`, when given, receives
    (where, the spellings read) for each file, for `tjs run`'s check of
    two spellings of one symbol. The ISIN fields that hold a symbol are
    spelled alike and the id recomputed, so overlapping exports in two
    spellings give one event and one election; an export in upper case
    keeps its id, and an election saved under the id as read follows
    the event (`CorporateAction.read_event_id`, backlog TJ-4616)."""
    from taxjson.lib.symbol_case import (book_ident, book_symbol, has_lower,
                                         record)
    events: List[CorporateAction] = []
    paths = [Path(p) for p in csv_paths]
    for csv_path in paths:
        kw = ({'context_files': paths}
              if getattr(extractor, 'accepts_context', False) else {})
        if renames and getattr(extractor, 'accepts_renames', False):
            kw['renames'] = renames
        if (symbol_codes
                and getattr(extractor, 'accepts_symbol_codes', False)):
            kw['symbol_codes'] = symbol_codes
        if account is None:
            got = extractor(csv_path, **kw)
        else:
            got = extractor(csv_path, account, **kw)
        seen: Dict[str, Dict[str, int]] = {}
        # A broker whose parser upper-cases as it reads (Questrade) has
        # no spelling of its trades to set these beside: not counted.
        count = not getattr(extractor, 'parse_reads_upper_case', False)
        for ev in got:
            for f in EVENT_SYMBOL_FIELDS:
                v = getattr(ev, f, None)
                if count:
                    record(seen, v, case)
                if has_lower(v):
                    setattr(ev, f, book_symbol(v, case))
            if any(has_lower(getattr(ev, f, None))
                   for f in EVENT_IDENT_FIELDS):
                for f in EVENT_IDENT_FIELDS:
                    setattr(ev, f, book_ident(getattr(ev, f), case))
                ev.rehash()
        if spellings is not None:
            spellings.append((f"{csv_path.name} (corporate actions)", seen))
        events.extend(got)
    return events


def map_tables(path: Path) -> Tuple[dict, Dict[str, str]]:
    """(map_renames, the case-pair lines: {an export's spelling: SAME or
    APART}, lib/symbol_case) of a ticker.map, read once. OSError /
    ValueError when the map cannot be read."""
    from taxjson.lib.ticker_map_rules import merge_renames
    from taxjson.lib.map_file import _parse_map_file
    tmap = _parse_map_file(Path(path))[0]
    return merge_renames(tmap, to_base=True), dict(tmap.case_spellings)


def map_renames(path: Path) -> dict:
    """The renames of a ticker.map as the extractors read them (a
    spin-off's parent named by a broker code: `GLOBAL X123456
    PARENT.TO`). OSError / ValueError when the map cannot be read."""
    from taxjson.lib.ticker_map_rules import merge_renames
    from taxjson.lib.map_file import _parse_map_file
    return merge_renames(_parse_map_file(Path(path))[0], to_base=True)


def project_corp_map(root: Path) -> Optional[Path]:
    """The map `tjs run`'s corporate-action stage read for the
    project at `root`: the run's effective map (work/ticker.map.effective:
    ticker.map plus the listings it joined and the .tt renames) when the
    last run wrote one, else ticker.map; None without either. `tjs
    elections` extracts with it, so its event ids are the run's (backlog
    TJ-0849)."""
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.project_layout import EFFECTIVE_MAP
    for p in (Path(root) / "work" / EFFECTIVE_MAP,
              _PL.ticker_map_path(root)):
        if p.is_file():
            return p
    return None


def project_corp_case(root: Path) -> Optional[Dict[str, str]]:
    """The case-pair lines (map_tables) of project_corp_map (None when
    there is no map or it cannot be read)."""
    p = project_corp_map(root)
    if p is None:
        return None
    try:
        return map_tables(p)[1] or None
    except (OSError, ValueError):
        return None


def project_corp_renames(root: Path) -> Optional[dict]:
    """map_renames of project_corp_map (None when there is no map or it
    cannot be read: `tjs run` refuses a bad map up front)."""
    p = project_corp_map(root)
    if p is None:
        return None
    try:
        return map_renames(p) or None
    except (OSError, ValueError):
        return None
