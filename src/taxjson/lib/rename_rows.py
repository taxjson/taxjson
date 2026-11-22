"""The books' rename rows, read: a SPLIT row that renames
(rename_target), where it came from (row_source, row_source_id,
SOURCE_LABELS, TICKER_MAP_SOURCE), and the rename events the rows of
any accounts make (rename_events: copies within WINDOW_DAYS merged).
Out of lib/renames, which lib/dated_events read for these while
renames reads the declarations dated_events resolves (step 8 of the
architecture plan: lib's import cycle)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from taxjson.lib.rename_decl import SOURCE_MAP, SOURCE_TT, WINDOW_DAYS, _days


# The `source` stamped on a SPLIT row the pipeline books from a dated
# ticker.map RENAME line.
TICKER_MAP_SOURCE = "ticker.map"


def _g(r: Any, k: str, default: Any = "") -> Any:
    if isinstance(r, dict):
        v = r.get(k, default)
    else:
        v = getattr(r, k, default)
    return default if v is None else v


def _option_root(sym: str) -> Optional[str]:
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying
    if sym and is_option_symbol(sym):
        return parse_option_underlying(sym)
    return None


def rename_target(r: Any) -> str:
    """The new symbol of a SPLIT row that renames ('' otherwise)."""
    if str(_g(r, "action")).upper() != "SPLIT":
        return ""
    new = str(_g(r, "symbol_new") or "").strip()
    return "" if not new or new == str(_g(r, "symbol")) else new


# The words of a rename's `source` (the SOURCE column of `tjs
# renames`): the stamp a stage writes on the SPLIT row. A source not
# named here is shown as it is (a broker row's file name: `broker row
# (<file>)`).
SOURCE_LABELS: Dict[str, str] = {
    TICKER_MAP_SOURCE: "ticker.map line",
    "map": "ticker.map line",
    "tt": ".tt line",
    "ib-conid": "detected IB contract id",
    "map-undated": "legacy undated map",
    "legacy": "legacy undated map",
}


def row_source(r: Any) -> str:
    """Where a rename row came from, for the report (SOURCE_LABELS): the
    row's `event_source` (the stage that booked it) when it names one,
    else its `source`."""
    ev = str(_g(r, "event_source") or "")
    if ev in SOURCE_LABELS:
        src = str(_g(r, "source") or "")
        if ev == "tt" and src.lower().endswith(".tt"):
            return f".tt line ({src})"
        return SOURCE_LABELS[ev]
    src = str(_g(r, "source") or "")
    if src in SOURCE_LABELS:
        return SOURCE_LABELS[src]
    if src.lower().endswith(".tt"):
        return f".tt line ({src})"
    if _g(r, "corp_event_id"):
        return "broker corporate action"
    return f"broker row ({src})" if src else "broker row"


def row_source_id(r: Any) -> str:
    """The machine source of a rename row: "tt", "map", "ib-conid" or
    "broker" (a broker's own row: a corporate action, a parser's SPLIT)."""
    ev = str(_g(r, "event_source") or "")
    if ev:
        return ev
    src = str(_g(r, "source") or "")
    if src == TICKER_MAP_SOURCE:
        return SOURCE_MAP
    if src.lower().endswith(".tt"):
        return SOURCE_TT
    return "broker"


def rename_events(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """The rename events in `rows` (dicts or TaxTransactions, any
    accounts): one per (old, new) with copies within WINDOW_DAYS merged
    onto the earliest date. Each: date, old, new, ratio, sources,
    accounts, rows."""
    evs: List[Dict[str, Any]] = []
    for r in sorted((r for r in rows if rename_target(r)),
                    key=lambda r: (str(_g(r, "date")), str(_g(r, "symbol")))):
        old, new = str(_g(r, "symbol")), rename_target(r)
        d = str(_g(r, "date"))[:10]
        for e in evs:
            gap = _days(e["date"], d)
            if (e["old"], e["new"]) == (old, new) and gap is not None \
                    and gap <= WINDOW_DAYS:
                break
        else:
            e = {"date": d, "old": old, "new": new,
                 "ratio": float(_g(r, "quantity", 0.0) or 0.0),
                 "sources": [], "source_ids": [], "accounts": [],
                 "rows": []}
            evs.append(e)
        e["rows"].append(r)
        s = row_source(r)
        if s not in e["sources"]:
            e["sources"].append(s)
        s = row_source_id(r)
        if s not in e["source_ids"]:
            e["source_ids"].append(s)
        a = str(_g(r, "_acct") or _g(r, "account") or "")
        if a and a not in e["accounts"]:
            e["accounts"].append(a)
    return evs
