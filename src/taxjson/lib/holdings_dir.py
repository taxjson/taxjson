"""The year's holdings/ folder: the broker's positions snapshots.

A project keeps the broker's positions files for its year in holdings/
(`[settings] holdings_dir` names another folder): a `[[holding]]` TOML
per broker account, as a download tool writes it (taxjson-fetch
--positions writes the same shape). `tjs sanity` and the end of
`tjs run` find them without any `holdings = [...]` setting:

- each file belongs to the account whose broker account ids include the
  file's `[meta] broker_account`, else its `[meta] account`
  (`[accounts.NAME] account = "..."`, or `broker_accounts = [...]` when
  one taxjson account spans several broker accounts), else the account
  its name starts with
  (`<account>_..._holdings.toml`, `<account>.toml`); several files of
  one account at one date are summed; at different dates each broker
  account's snapshots are compared one date at a time (`tjs
  sanity` refuses files that do not name their broker account);
- a snapshot is compared at its date (`[meta] as_of`, else the day of
  `[meta] generated_at`); one newer than the books' last day is compared
  with the latest books, with a note;
- an account with its own `holdings = [...]` keeps those files (a file
  it lists is not another account's, nor unclaimed);
- a broker's own positions report (an RBC Holdings Export, an IB
  Activity Statement with Open Positions: lib/positions_reports) is read
  like a TOML, claimed by its name (`margin_holdings.csv`); any other
  file there is named as not read, never skipped in silence (backlog
  TJ-0424).

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
per broker account (a download tool writes them; `tjs fetch
--positions` writes the same shape). Keep one taken at (or just after)
the year end. `tjs sanity` and the end of `tjs run` compare the
books with them at each file's date ([meta] as_of, else [meta]
generated_at), with no setting: a file belongs to the account whose
broker account id is its [meta] broker_account (or [meta] account)
([accounts.NAME] account or broker_accounts in taxjson.toml), else the
account its name starts with
(margin_holdings.toml, margin_ib_holdings.toml). To keep two dates of
one broker account, give each file its [meta] broker_account.

The broker's own report is read here too: an RBC Holdings Export or an
IB Activity Statement with Open Positions (.csv), named for its account
(margin_holdings.csv). Any other file here is named as not read.
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


def empty_snapshot(doc: Any) -> bool:
    """A snapshot of an account that holds nothing (#46): no
    `[[holding]]` table, a `[meta]` table and nothing else (a
    top-level `schema_version` aside), and no `holdings_count` above 0
    — what taxjson-fetch --positions writes after every position is
    closed. A `[[holdings]]` typo, another tool's file or a count with
    no rows is not one."""
    if not isinstance(doc, dict) or "holding" in doc:
        return False
    meta = doc.get("meta")
    if not isinstance(meta, dict) or set(doc) - {"meta", "schema_version"}:
        return False
    count = meta.get("holdings_count", 0)
    return (isinstance(count, int) and not isinstance(count, bool)
            and count == 0)


# A broker's positions report (lib/positions_reports) is a .csv; the
# folder's README.txt (tjs init writes one) is not a snapshot.
_REPORT_SUFFIXES = (".csv",)
_README_NAMES = ("readme.txt", "readme.md", "readme")


def _listing(folder: Path) -> List[Path]:
    """The folder's entries, hidden ones and Office lock files aside."""
    try:
        entries = sorted(Path(folder).iterdir())
    except OSError:
        return []
    return [p for p in entries if not p.name.startswith((".", "~$"))]


def report_kind(path: Path) -> Optional[str]:
    """'rbc_holdings' | 'ib_open_positions' when `path` is a broker's
    positions report `tjs sanity` reads (lib/positions_reports),
    else None."""
    if Path(path).suffix.lower() not in _REPORT_SUFFIXES:
        return None
    from taxjson.lib.positions_reports import detect_positions
    try:
        kind = detect_positions(path)
    except Exception:                                   # noqa: BLE001
        return None
    return kind if kind in ("rbc_holdings", "ib_open_positions") else None


def snapshot_files(folder: Path) -> List[Path]:
    """The folder's positions files: each `.toml`, and each broker's
    positions report (report_kind) — an RBC Holdings Export or an IB
    statement saved there was skipped while `tjs sanity` read the
    same file given by name (backlog TJ-0424)."""
    if not Path(folder).is_dir():
        return []
    return [p for p in _listing(folder) if p.is_file()
            and (p.suffix.lower() == ".toml" or report_kind(p))]


def unread_files(folder: Path) -> List[Path]:
    """What the folder holds that is never read: a file that is neither
    a `.toml` nor a broker's positions report (README.txt aside), and a
    sub-folder holding files (only the folder's own files are read)."""
    if not Path(folder).is_dir():
        return []
    out = []
    for p in _listing(folder):
        if p.is_dir():
            try:
                if any(q.is_file() for q in p.rglob("*")):
                    out.append(p)
            except OSError:
                out.append(p)
            continue
        if (not p.is_file() or p.name.lower() in _README_NAMES
                or p.suffix.lower() == ".toml" or report_kind(p)):
            continue
        out.append(p)
    return out


def unread_notes(folder: Path, accounts_cfg: Dict[str, Any],
                 root: Optional[Path] = None) -> List[str]:
    """A note naming each thing in the folder `tjs sanity` does not
    read (unread_files), with the fix; a file an account's own
    `holdings = [...]` names is that setting's (`root`: the project
    its paths are relative to; default the folder's parent)."""
    folder = Path(folder)
    listed = listed_files(Path(root) if root is not None
                          else folder.parent, accounts_cfg)
    notes = []
    for p in unread_files(folder):
        if _resolved(p) in listed:
            continue
        if p.is_dir():
            notes.append(f"{folder.name}/{mask(p.name)}/ is not read: "
                         f"only the files directly in {folder.name}/ "
                         f"are — move its snapshots up a level")
        else:
            notes.append(f"{folder.name}/{mask(p.name)} is not read: it "
                         f"is not a holdings TOML ([[holding]] tables), "
                         f"an RBC Holdings Export or an IB Activity "
                         f"Statement with Open Positions — save the "
                         f"broker's positions as one of these, or move it "
                         f"out of {folder.name}/")
    return notes


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
             root: Optional[Path] = None,
             info: Optional[List[str]] = None
             ) -> Tuple[Dict[str, List[str]], List[str]]:
    """({account: [file paths]}, notes) for the folder's snapshots. An
    account with its own `holdings = [...]` is left out (its setting
    wins), and so is a file such a setting names (`root`: the project
    the paths are relative to; default the folder's parent); a file no
    account claims, or two do, is named in a note. `info`, when given,
    takes the notes that leave nothing unchecked instead — a snapshot
    of an account whose own setting names its files: that account is
    compared with those (backlog TJ-0020)."""
    every = {str(n): a for n, a in (accounts_cfg or {}).items()
             if isinstance(a, dict)}
    accts = {n: a for n, a in every.items() if not a.get("holdings")}
    listed = listed_files(Path(root) if root is not None
                          else Path(folder).parent, accounts_cfg)
    # Ownership by broker id is settled against EVERY account, the ones
    # with their own `holdings = [...]` included: a snapshot stating
    # such an account's id used to fall through to the file-name rule
    # and land on another account (audit 2026-10-24 A13).
    ids = {n: set(broker_ids(a)) for n, a in every.items()}
    files_of: Dict[str, List[str]] = {}
    notes: List[str] = []
    by_len = sorted(accts, key=lambda n: (-len(n), n))
    for p in snapshot_files(folder):
        if _resolved(p) in listed:
            continue                # an account's holdings = [...] has it
        who = None
        report = p.suffix.lower() != ".toml"
        meta = {} if report else _meta(p)
        # `broker_account` first: it is the broker's id whatever local
        # alias `account` carries (taxjson-fetch writes the taxjson
        # account's name there, which a renamed account no longer
        # matches, #47); `account` holds the id in other tools' files.
        hits: List[str] = []
        acc = None
        for key in ("broker_account", "account"):
            acc = meta.get(key)
            hits = ([n for n in every if _norm(acc) in ids[n]]
                    if acc else [])
            if hits:
                break
        if len(hits) > 1:
            notes.append(f"{folder.name}/{mask(p.name)} is not compared: "
                         f"its broker account {mask(str(acc))} is "
                         f"declared by {', '.join(hits)}")
            continue
        who = hits[0] if hits else None
        if who is not None and who not in accts:
            # The point and its reason before the first clause break:
            # the default view's one line is the part before it, which
            # ended before "not compared" (backlog TJ-0255, as TJ-0160).
            (notes if info is None else info).append(
                f"{folder.name}/{mask(p.name)} is not compared: its "
                f"broker account {mask(str(acc))} is {who}'s, whose own "
                f"`holdings = [...]` setting names the files compared — "
                f"list it there, or drop the setting")
            continue
        if who is None:
            stem = p.stem.lower()
            who = next((n for n in by_len if stem == n.lower()
                        or stem.startswith(n.lower() + "_")), None)
        if who is None and report:
            # A broker's report has no [meta]: its name claims it.
            notes.append(f"{folder.name}/{mask(p.name)}: no account "
                         f"claims it — name it NAME_holdings"
                         f"{p.suffix.lower()} (NAME: the account it is "
                         f"of), or list it in that account's "
                         f"`holdings = [...]`")
            continue
        if who is None:
            notes.append(f"{folder.name}/{mask(p.name)}: no account "
                         f"claims it — add its broker account id to the "
                         f"account (`broker_accounts = [\"...\"]` under "
                         f"[accounts.NAME]) or name it "
                         f"NAME_holdings.toml")
            continue
        files_of.setdefault(who, []).append(str(p))
    return files_of, notes
