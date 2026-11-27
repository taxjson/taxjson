"""close-year's freeze of the positions snapshots of the folder every
year shares (`holdings_dir = "../holdings"` in a year folder,
project_layout `shared_holdings`; backlog TJ-5367, TJ-5405): the
snapshots the year compares are copied into its own holdings/, which
the filed year reads from then on, the lock's `frozen` record naming the
folder and the count; a file an account's own `holdings = [...]` names
in the shared folder is not copied, and the close says so.

The command (cli/file `_freeze_inputs`, `_close_year_report`) makes the
copies, comments `holdings_dir` out under FROZEN_NOTE and says the
refusals: `refuse(input_error, message, fix)` stops it (exit 2 for an
input, else 1), nothing written.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from taxjson.lib import holdings_dir as _HD
from taxjson.lib import project_layout as _PL

# The note above holdings_dir when the freeze copies the positions
# snapshots of a folder every year shares.
FROZEN_NOTE = ("## frozen by `tjs close-year`: this year reads its own "
               "copy of the positions snapshots it compared")

Refuse = Callable[[bool, str, str], None]


def copies(root: Path, cfg: Dict[str, Any], nothing: str, refuse: Refuse
           ) -> List[Tuple[Path, Path]]:
    """close-year's copies (source, target) of the year's positions
    snapshots from the folder every year shares into the year's own
    holdings/: the snapshots its `tjs sanity` compares (holdings_dir
    `year_files`: those dated in the year's window, and undated ones),
    but a file an account's own `holdings = [...]` names (that setting
    reads it where it is). Refused (`refuse`), nothing written, when
    holdings/ is a link out of the project or no folder, or already
    holds a snapshot (or a file of a copy's name): a file there is never
    overwritten."""
    from taxjson.lib.safe_write import link_outside
    own = root / _PL.HOLDINGS
    src = _PL.shown(_PL.holdings_folder(root), root)
    tgt = link_outside(own, root)
    if tgt is not None:
        refuse(True, f"{_PL.HOLDINGS}/ is a symlink to {tgt}, outside the "
               f"project — never written through",
               f"close-year copies the year's positions snapshots from "
               f"{src}/ there: replace the link with a folder, then close. "
               f"{nothing}")
    if os.path.lexists(own) and not own.is_dir():
        refuse(False, f"{_PL.HOLDINGS} is not a folder",
               f"close-year copies the year's positions snapshots from "
               f"{src}/ into the project's {_PL.HOLDINGS}/: move it away, "
               f"then close. {nothing}")
    listed = _HD.listed_files(root, cfg.get("accounts") or {})
    out = [(p, own / p.name) for p in _HD.year_files(root)[0]
           if p.resolve() not in listed]
    busy = _HD.snapshot_files(own) + [d for _p, d in out
                                      if os.path.lexists(d)]
    if busy:
        refuse(False, f"{_PL.HOLDINGS}/{busy[0].name} is there already: "
               f"{_PL.HOLDINGS}/ holds files this project does not read",
               f"close-year copies the year's positions snapshots from "
               f"{src}/ into the project's {_PL.HOLDINGS}/ — move those "
               f"files away (into {src}/ if they belong to a year), then "
               f"close. {nothing}")
    return out


def freeze(root: Path, cfg: Dict[str, Any], holdings_dir: Optional[str],
           shared: bool, previous: Dict[str, Any], rec: Dict[str, Any],
           nothing: str, refuse: Refuse) -> List[Tuple[Path, Path]]:
    """The freeze's copies (`copies`) when the year reads the folder
    every year shares (`shared`, its `holdings_dir`), the lock record
    `rec`'s `holdings_dir` and `holdings_copied` set; a year frozen
    before (re-closed with --force, the key commented out) keeps the
    record of the close that copied them (`previous`), as it keeps
    `inputs_dir`."""
    if not shared:
        for k in ("holdings_dir", "holdings_copied"):
            if previous.get(k) is not None:
                rec[k] = previous[k]
        return []
    hold = copies(root, cfg, nothing, refuse)
    rec["holdings_dir"] = holdings_dir
    rec["holdings_copied"] = len(hold)
    return hold


def summary(rec: Dict[str, Any], year: int) -> str:
    """The part of close-year's Frozen line on the snapshots (this close's
    copy or an earlier one's), "" when the record has none."""
    if rec.get("holdings_copied") is None:
        return ""
    return (f"; holdings/ ({rec['holdings_copied']} positions snapshot(s) "
            f"of {year} frozen from "
            f"{rec.get('holdings_dir') or 'the shared folder'})")


def listed_note(root: Path, rec: Dict[str, Any]) -> Optional[str]:
    """close-year's note on a positions file an account's own `holdings =
    [...]` names in the folder every year shares (the lock record's
    `holdings_dir`): the freeze copies the snapshots the year compares,
    never a file that setting reads where it is, so the filed year goes
    on reading it there. None for a lock before the year ended, which
    freezes nothing."""
    hd = rec.get("holdings_dir")
    if rec.get("partial") or not isinstance(hd, str):
        return None
    shared = (root / hd).resolve()
    accounts = _PL.read_config_soft(root).get("accounts")
    names = sorted(p.name for p in _HD.listed_files(
        root, accounts if isinstance(accounts, dict) else {})
        if p.parent == shared)
    if not names:
        return None
    return (f"not frozen: {', '.join(_HD.mask(n) for n in names)} in "
            f"{hd}/, which an account's `holdings = [...]` names — the "
            f"filed year reads it there; to freeze it, copy it into "
            f"holdings/ and name holdings/{_HD.mask(names[0])} instead")
