"""The cross-listed loss radar's state file (work/xlist_loss_radar.state),
read: its findings (read_state) and the reason `tjs ticker-map
--suggest` gives each one's TOBASE line (suggestion_reason). Out of
lib/xlist_loss_radar, which the map suggester read for these while the
radar reads the map's suggestions (step 8 of the architecture plan:
lib's import cycle)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


STATE = "xlist_loss_radar.state"
FORMAT = "xlist_loss_radar/1"


def read_state(cache: Path) -> List[Dict[str, Any]]:
    """The findings the last run wrote ([] when none or unreadable)."""
    try:
        doc = json.loads((Path(cache) / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return []
    return [f for f in doc.get("findings") or [] if isinstance(f, dict)
            and f.get("loss_symbol") and f.get("other_symbol")]


def _kind(country: str, n: int = 1) -> str:
    from taxjson.lib.country import is_usa
    one, many = (("wash sale", "wash sales") if is_usa(country) else
                 ("superficial loss", "superficial losses"))
    return one if n == 1 else many


def _named(f: Dict[str, Any]) -> str:
    """How the exports name the pair: the shared name, or the two names
    that differ only in share wording."""
    nm = f.get("names") or []
    if len(nm) == 2 and (nm[0] or nm[1]):
        return (f"named {nm[0]!r} and {nm[1]!r} — the same company; the "
                f"names differ only in share wording, which may be a "
                f"broker's style or two share classes")
    return f"both named {f.get('name')!r}"


def suggestion_reason(f: Dict[str, Any], country: str) -> str:
    """The reason `tjs ticker-map --suggest` gives the TOBASE line."""
    return (f"a loss on {f['loss_symbol']} and {f['other_symbol']} bought "
            f"within 30 days, {_named(f)}: a possible "
            f"{_kind(country)} across listings — add it if they are one "
            f"security (`{f.get('distinct')}` if not)")
