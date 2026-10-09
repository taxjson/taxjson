"""The year's holdings/ folder: the broker's positions snapshots.

A project keeps the broker's positions files for its year in holdings/
(`[settings] holdings_dir` names another folder): a `[[holding]]` TOML
per broker account, as a download tool writes it (taxjson-fetch
--positions writes the same shape). `taxjson sanity` and the end of
`taxjson run` find them without any `holdings = [...]` setting:

- each file belongs to the account whose broker account ids include the
  file's `[meta] account` (`[accounts.NAME] account = "..."`, or
  `broker_accounts = [...]` when one taxjson account spans several
  broker accounts), else the account its name starts with
  (`<account>_..._holdings.toml`, `<account>.toml`); several files of
  one account are compared together;
- a snapshot is compared at its date (`[meta] as_of`, else the day of
  `[meta] generated_at`); one newer than the books' last day is compared
  with the latest books, with a note;
- an account with its own `holdings = [...]` keeps those files (a file
  it lists is not another account's, nor unclaimed).

Keep a snapshot taken at (or just after) the year end in each year's
folder: a later year's snapshot belongs in the later year's.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib import project_layout as _PL

HOLDINGS_DIR = "holdings"

README = """\
The broker's positions snapshots for this tax year, one [[holding]] TOML
per broker account (a download tool writes them; `taxjson fetch
--positions` writes the same shape). Keep one taken at (or just after)
the year end. `taxjson sanity` and the end of `taxjson run` compare the
books with them at each file's date ([meta] as_of, else [meta]
generated_at), with no setting: a file belongs to the account whose
broker account id is its [meta] account ([accounts.NAME] account or
broker_accounts in taxjson.toml), else the account its name starts with
(margin_holdings.toml, margin_ib_holdings.toml).
"""


def _norm(x: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(x or "")).upper()


def mask(text: str) -> str:
    """Broker-account-like ids in `text` shown as their first 2
    characters + *** (U1234567 -> U1***)."""
    return re.sub(r"(?<![A-Za-z0-9])(U\d{5,}|\d{5,})(?!\d)",
                  lambda m: m.group(1)[:2] + "***", str(text))


def broker_ids(acfg: Any) -> List[str]:
    """The broker account ids an account declares: `account` and
    `broker_accounts`, normalised (letters and digits, upper case)."""
    if not isinstance(acfg, dict):
        return []
    out = []
    one = acfg.get("account")
    if isinstance(one, str) and _norm(one):
        out.append(_norm(one))
    many = acfg.get("broker_accounts")
    if isinstance(many, list):
        out += [_norm(x) for x in many if isinstance(x, str) and _norm(x)]
    return out


def _meta(path: Path) -> Dict[str, Any]:
    from taxjson.lib.tomlcompat import tomllib
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:                                   # noqa: BLE001
        return {}
    m = doc.get("meta") if isinstance(doc, dict) else None
    return m if isinstance(m, dict) else {}


def snapshot_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.suffix.lower() == ".toml" and p.is_file()
                  and not p.name.startswith((".", "~$")))


def folder_for(root: Path, year: Optional[int] = None
               ) -> Tuple[Optional[Path], Optional[str]]:
    """(the year's holdings folder when it holds a snapshot, else None;
    a note — none today)."""
    d = _PL.holdings_folder(Path(root))
    return (d if snapshot_files(d) else None), None


def _resolved(p: Path) -> Path:
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return p.absolute()


def listed_files(root: Path, accounts_cfg: Dict[str, Any]) -> set:
    """The files the accounts' own `holdings = [...]` settings name,
    resolved as the sanity check reads them (`~` expanded, relative to
    the project)."""
    out = set()
    for a in (accounts_cfg or {}).values():
        raw = a.get("holdings") if isinstance(a, dict) else None
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            continue
        for x in raw:
            if not isinstance(x, str) or not x.strip():
                continue
            pp = Path(x).expanduser()
            out.add(_resolved(pp if pp.is_absolute() else Path(root) / pp))
    return out


def discover(folder: Path, accounts_cfg: Dict[str, Any],
             root: Optional[Path] = None
             ) -> Tuple[Dict[str, List[str]], List[str]]:
    """({account: [file paths]}, notes) for the folder's snapshots. An
    account with its own `holdings = [...]` is left out (its setting
    wins), and so is a file such a setting names (`root`: the project
    the paths are relative to; default the folder's parent); a file no
    account claims, or two do, is named in a note."""
    accts = {str(n): a for n, a in (accounts_cfg or {}).items()
             if isinstance(a, dict) and not a.get("holdings")}
    listed = listed_files(Path(root) if root is not None
                          else Path(folder).parent, accounts_cfg)
    ids = {n: set(broker_ids(a)) for n, a in accts.items()}
    files_of: Dict[str, List[str]] = {}
    notes: List[str] = []
    by_len = sorted(accts, key=lambda n: (-len(n), n))
    for p in snapshot_files(folder):
        if _resolved(p) in listed:
            continue                # an account's holdings = [...] has it
        who = None
        acc = _meta(p).get("account") or _meta(p).get("broker_account")
        if acc:
            hits = [n for n in accts if _norm(acc) in ids[n]]
            if len(hits) > 1:
                notes.append(f"{folder.name}/{mask(p.name)}: broker "
                             f"account {mask(str(acc))} is declared by "
                             f"{', '.join(hits)} — not compared")
                continue
            who = hits[0] if hits else None
        if who is None:
            stem = p.stem.lower()
            who = next((n for n in by_len if stem == n.lower()
                        or stem.startswith(n.lower() + "_")), None)
        if who is None:
            notes.append(f"{folder.name}/{mask(p.name)}: no account "
                         f"claims it — add its broker account id to the "
                         f"account (`broker_accounts = [\"...\"]` under "
                         f"[accounts.NAME]) or name it "
                         f"NAME_holdings.toml")
            continue
        files_of.setdefault(who, []).append(str(p))
    return files_of, notes
