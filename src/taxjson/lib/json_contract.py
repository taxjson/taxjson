"""The `--json` contract: each output's `schema_version`, in one place.

Every `--json` output is one JSON object whose top-level `schema_version`
is the integer SCHEMAS gives it (docs/settings.md, "`--json` outputs").
An output is a command, or a mode of it that prints a document of its own
(`compare --save`, `years --diff`, `amt YEAR`), or a taxjson-* tool's
document no command prints. A variant of one (empty, not applicable,
nothing to compare, the other country's form) carries its output's number.

New keys may be added at any depth, and new enum values; the number goes up
(announced in docs/upgrading.md) when a key is removed, renamed or moved, a
value changes type, unit or meaning, or an enum value is renamed or removed.

`versioned(doc, output)` is how a writer sets it: the doc with its number,
refused when it is not a dict or already carries another one. The writers'
own bytes (sorting, NaN handling) are theirs. tests/test_json_contract.py
holds every `--json` to an entry here, every output to a golden view (or a
test, UNHELD there) and every entry to its docs row.
"""
from typing import Any, Dict, Mapping, Tuple

# An output's id is its words: the command (or tool) first, then the flags
# or arguments that select the mode. `YEAR` stands for a 4-digit year, and
# `a/b` for either word.
SCHEMAS: Dict[str, int] = {
    # Set up
    "checklist": 3,
    "checklist --done/--undo/--reset": 1,
    "years": 2,
    "years --diff": 1,
    "align": 1,
    "align --write": 1,
    "fetch": 1,
    "fetch --list": 1,
    "elect": 1,
    "elect --pending": 1,
    "ticker-map --suggest": 1,
    "update-tobase-map": 1,
    # Build the books
    "compare": 1,
    "compare --save": 1,
    "compare --delete": 1,
    "compare --list": 1,
    "snapshot": 1,
    "clean": 1,
    "crypto-sends": 1,
    # Summaries
    "amt": 1,
    "amt YEAR": 1,
    "div-estimate": 1,
    "estimate": 1,
    "fx-cash": 1,
    "fx-cash --ledger v2": 1,
    "instalments": 1,
    "option-yield": 1,
    "plot-tax": 1,
    "stats": 1,
    "sum": 1,
    "win-rates": 1,
    # Positions
    "list": 1,
    "shares": 1,
    "technicals": 1,
    # Row listings
    "cash": 1,
    "dil": 1,
    "divs": 1,
    "events": 1,
    "fees": 1,
    "gains": 1,
    "gains-base": 1,
    "leaps": 1,
    "roc": 1,
    "staking": 1,
    "trades": 1,
    "transfers": 1,
    # Totals by type
    "ccd-sum": 1,
    "dil-sum": 1,
    "divs-sum": 1,
    "fees-sum": 1,
    "leaps-sum": 1,
    "losers": 1,
    "roc-sum": 1,
    "staking-sum": 1,
    "trades-sum": 1,
    "winners": 1,
    # Before you trade
    "wash-radar": 1,
    "buy-check": 1,
    "sell-check": 1,
    "rebuy": 1,
    "harvest": 1,
    "tips": 2,
    "watch": 1,
    # Before you file
    "form-export": 1,
    "gains-cra": 1,
    "t1135": 2,
    "reconcile-slips": 1,
    "slip-audit": 1,
    "slip-audit --import-cra": 1,
    # 2: `carried_back_inferred` is gone from a Canadian row (a balance typed
    # in settles no carry-back: backlog TJ-4886).
    "carryover": 2,
    "option-boundary": 1,
    "handoff": 1,
    # Explain and check
    "audit": 1,
    "wash-sales": 1,
    "tax-logic": 1,
    "edge-cases": 1,
    "check-dates": 1,
    "check-tt": 1,
    "sanity": 1,
    "journals": 1,
    "renames": 1,
    "spinoffs": 1,
    "splits": 1,
    # Maintainer
    "channels": 1,
    # The tools' own documents (a tjs command prints none of them)
    "taxjson-sum-gains": 1,
    "taxjson-sum-income": 1,
    "taxjson-audit": 1,
}

# The taxjson-* tools whose --json document is a command's (the command
# runs the tool): the output id they print.
TOOL_OUTPUTS: Dict[str, str] = {
    "taxjson-carryover": "carryover",
    "taxjson-fees-sum": "fees-sum",
    "taxjson-form-export": "form-export",
    "taxjson-harvest": "harvest",
    "taxjson-reconcile-slips": "reconcile-slips",
    "taxjson-t1135": "t1135",
    "taxjson-wash-radar": "wash-radar",
}

BOTH = ("canada", "usa")
# The countries a golden view holds an output's shape on: both, but for a
# command only Canada has, and a document whose keys no country's rules
# shape (one book holds it). The US `handoff` is held by tests/test_handoff.py:
# no US book has a second year.
COUNTRIES: Dict[str, Tuple[str, ...]] = {o: ("canada",) for o in (
    "amt", "amt YEAR", "instalments", "option-boundary", "slip-audit",
    "slip-audit --import-cra", "t1135",
    "checklist --done/--undo/--reset", "years", "years --diff", "align",
    "align --write", "elect --pending", "update-tobase-map", "compare",
    "compare --save", "compare --delete", "compare --list", "snapshot",
    "handoff", "gains-cra")}


def countries(output: str) -> Tuple[str, ...]:
    """The countries a golden view holds `output` on (COUNTRIES)."""
    return COUNTRIES.get(output, BOTH)


def versioned(doc: Mapping[str, Any], output: str) -> Dict[str, Any]:
    """`doc` with its output's `schema_version` (SCHEMAS[output]), first
    among its keys; a new dict. Refused (ValueError): an output SCHEMAS
    does not know, a doc that is not a dict, or one whose `schema_version`
    is another number (or not an integer)."""
    if output not in SCHEMAS:
        raise ValueError(f"--json output {output!r} has no entry in "
                         f"lib/json_contract.SCHEMAS")
    if not isinstance(doc, dict):
        raise ValueError(f"--json output {output!r}: a JSON object, not "
                         f"{type(doc).__name__}")
    n = SCHEMAS[output]
    if "schema_version" in doc:
        have = doc["schema_version"]
        if type(have) is not int or have != n:
            raise ValueError(f"--json output {output!r}: schema_version "
                             f"{have!r}, the registry's is {n}")
    out: Dict[str, Any] = {"schema_version": n}
    out.update(doc)
    return out
