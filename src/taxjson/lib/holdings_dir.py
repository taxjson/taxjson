"""The year's holdings/ folder: the broker's positions snapshots.

A project keeps the broker's positions files for its year in holdings/
(`[settings] holdings_dir` names another folder): a `[[holding]]` TOML
per broker account, as a download tool writes it (taxjson-fetch
--positions writes the same shape). `tjs sanity` finds them without
any `holdings = [...]` setting (`tjs run` compares nothing: its closing
list names `tjs sanity` while the project has such files, in_use):

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

The folder every year shares (`holdings_dir = "../holdings"` in a year
folder, beside the year folders: `tjs init`'s layout, project_layout
`shared_holdings`) holds every year's snapshots, and each year compares
its own (`year_files`): those dated from January 1 of the year (when
the year before's folder reads it too; else with no start: the oldest
year compares every earlier one, its opening positions), through
January 15 of the next when the next year's folder reads it too (else with
no end: the newest year compares every later one), and every undated
one (`year_window`; a January 1-15 snapshot is compared by both years:
an open year's books run on, so they agree). The others are named in
one note, with how to compare one anyway, never dropped in silence.
`tjs close-year` copies the year's into the year's own holdings/, which
the filed year reads from then on. Any other `holdings_dir`, and a
single-folder project, compares every snapshot as above (backlog
TJ-5367, TJ-5400, TJ-5404).
"""
from __future__ import annotations


import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.project.accounts import configured_accounts, typed_account, typed_accounts
from taxjson.project.toml_io import read_toml
from taxjson.lib import project_layout as _PL
from taxjson.lib.privacy import mask_ids

HOLDINGS_DIR = "holdings"

README = """\
The broker's positions snapshots for this tax year, one [[holding]] TOML
per broker account (a download tool writes them; `tjs fetch
--positions` writes the same shape). Keep one taken at (or just after)
the year end. `tjs sanity` compares the books with them at each
file's date ([meta] as_of, else [meta] generated_at), with no setting
(`tjs run` does not: run `tjs sanity` after it): a file belongs to the
account whose broker account id is its [meta] broker_account (or
[meta] account) ([accounts.NAME] account or broker_accounts in
taxjson.toml), else the account its name starts with
(margin_holdings.toml, margin_ib_holdings.toml). To keep two dates of
one broker account, give each file its [meta] broker_account.

The broker's own report is read here too: an RBC Holdings Export or an
IB Activity Statement with Open Positions (.csv), named for its account
(margin_holdings.csv). Any other file here is named as not read.
"""

# The README of the folder every year shares (`tjs init`'s layout,
# `holdings_dir = "../holdings"`).
SHARED_README = """\
The broker's positions snapshots of every year, one [[holding]] TOML
per broker account and date (a download tool writes them; `tjs fetch
--positions` writes NAME_live_holdings_YYYY.toml, YYYY the year whose
closing positions it is nearest). Each year folder reads this folder
(holdings_dir = "../holdings"), and its `tjs sanity` compares the
snapshots dated from January 1 of its year (the oldest year: every
earlier one too), through January 15 of the next when the next year's
folder reads it too (else every later one), and every undated one. It names the ones it leaves out, and `tjs sanity
ACCOUNT=../holdings/FILE` compares one anyway. Give each snapshot its
date ([meta] as_of = "YYYY-MM-DD"): an undated one is compared with the
latest books by every year, and one broker account's undated and dated
snapshots side by side are refused. `tjs close-year` copies the year's
snapshots into the year's own holdings/, which the filed year reads
from then on.

A file belongs to the account whose broker account id is its [meta]
broker_account (or [meta] account) ([accounts.NAME] account or
broker_accounts in taxjson.toml), else the account its name starts with
(margin_holdings_2025.toml, margin_ib_holdings.toml). To keep two dates
of one broker account in a year, give each file its [meta]
broker_account.

The broker's own report is read here too: an RBC Holdings Export or an
IB Activity Statement with Open Positions (.csv), named for its account
(margin_holdings_2025.csv). Any other file here is named as not read.
"""

# The last day of January a snapshot is still the year-end one of the
# year before (year_window, snapshot_year): the year's last trades
# settle and post in the first days of January, and a positions download
# taken then is the year's closing positions.
YEAR_END_THROUGH = 15


def _norm(x: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(x or "")).upper()


def mask(text: str) -> str:
    """Broker-account-like ids in `text` shown as their first 2
    characters + *** (U1234567 -> U1***)."""
    return mask_ids(text)


def broker_ids(acfg: Any) -> List[str]:
    """The broker account ids an account declares: `account` and
    `broker_accounts`, normalised (letters and digits, upper case)."""
    acfg = typed_account(acfg)
    out = []
    one = acfg.account
    if isinstance(one, str) and _norm(one):
        out.append(_norm(one))
    many = acfg.broker_accounts
    if isinstance(many, list):
        out += [_norm(x) for x in many if isinstance(x, str) and _norm(x)]
    return out


def _meta(path: Path) -> Dict[str, Any]:
    try:
        doc = read_toml(path)
    except Exception:                                   # noqa: BLE001
        return {}
    m = doc.get("meta") if isinstance(doc, dict) else None
    return m if isinstance(m, dict) else {}


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
    base = Path(root) if root is not None else folder.parent
    listed = listed_files(base, accounts_cfg)
    # (the folder as the project names it, ../holdings/: TJ-5407)
    at = _folder_shown(base, folder) if root is not None \
        else f"{folder.name}/"
    notes = []
    for p in unread_files(folder):
        if _resolved(p) in listed:
            continue
        if p.is_dir():
            notes.append(f"{at}{mask(p.name)}/ is not read: "
                         f"only the files directly in {at} "
                         f"are — move its snapshots up a level")
        else:
            notes.append(f"{at}{mask(p.name)} is not read: it "
                         f"is not a holdings TOML ([[holding]] tables), "
                         f"an RBC Holdings Export or an IB Activity "
                         f"Statement with Open Positions — save the "
                         f"broker's positions as one of these, or move it "
                         f"out of {at}")
    return notes


def stray_snapshots(root: Path) -> Optional[str]:
    """The warning when the project reads the holdings folder every year
    shares (project_layout `shared_holdings`) while its own holdings/
    still holds snapshots, which nothing reads (the move to the shared
    folder made by hand halfway: backlog TJ-5406); else None."""
    root = Path(root)
    own = snapshot_files(root / HOLDINGS_DIR)
    if not own or not _PL.shared_holdings(root):
        return None
    shown = _folder_shown(root, _PL.holdings_folder(root))
    return (f"{HOLDINGS_DIR}/ holds {len(own)} positions snapshot(s) this "
            f"year does not read ({_names(own)}): holdings_dir = "
            f"\"{_PL.SHARED_HOLDINGS}\" names {shown} — move them there "
            f"(rename one whose name is taken)")


def year_window(year: int, later: bool, earlier: bool = True
                ) -> Tuple[Optional[str], Optional[str]]:
    """(first, last) day of the snapshots tax year `year` compares in a
    folder every year shares: from January 1 of the year (a snapshot of
    the first days of January is the year before's closing positions and
    this year's opening ones: both compare it, and an open year's books
    run on past its end, so the two agree) when the year before's folder
    reads the same shared folder (`earlier`, earlier_year_folder), else
    with no start (None): the oldest sharing year compares every earlier
    snapshot, its opening positions; through YEAR_END_THROUGH January of
    the next year when the next year's folder reads the same shared
    folder (`later`, later_year_folder: that year compares the snapshots
    after it), else with no end (None): the newest sharing year compares
    every later snapshot, as a folder of the year's own does. So none is
    compared by no year (backlog TJ-5400, TJ-5430, TJ-5432)."""
    return (f"{year:04d}-01-01" if earlier else None,
            f"{year + 1:04d}-01-{YEAR_END_THROUGH:02d}" if later else None)


def window_of(root: Path, year: int) -> Tuple[Optional[str], Optional[str]]:
    """The window of the year `year` of the project `root`, by the year
    folders beside it (year_window)."""
    return year_window(year, later_year_folder(root, year),
                       earlier_year_folder(root, year))


def window_span(first: Optional[str], last: Optional[str]) -> str:
    """A window as the notes say it: `FIRST to LAST`, `from FIRST`,
    `through LAST`, or `of any date`."""
    return (f"{first} to {last}" if first and last else
            f"from {first}" if first else
            f"through {last}" if last else "of any date")


def _in_window(day: str, first: Optional[str], last: Optional[str]) -> bool:
    return (first is None or first <= day) and (last is None or day <= last)


def window_holds(day: str, year: int, later: bool,
                 earlier: bool = True) -> bool:
    """Tax year `year` compares a snapshot dated `day` in the folder
    every year shares (year_window; `later`, `earlier`: the next year's,
    the year before's folder reads it too)."""
    return _in_window(day, *year_window(year, later, earlier))


def later_year_folder(root: Path, year: int) -> bool:
    """A year folder of `year + 1` beside the project that reads the same
    folder every year shares (project_layout `shared_holdings`: the
    `holdings/` beside the year folders), which cuts the year's window
    (year_window). One reading a `holdings/` of its own, or filed with
    its copy, compares none of the shared snapshots: it cuts nothing
    (backlog TJ-5430)."""
    return _sharing_year(root, year + 1)


def earlier_year_folder(root: Path, year: int) -> bool:
    """A year folder of `year - 1` beside the project that reads the same
    folder every year shares, which starts the year's window at January
    1 (year_window; else the oldest sharing year compares every earlier
    snapshot: backlog TJ-5432)."""
    return _sharing_year(root, year - 1)


def _sharing_year(root: Path, year: int) -> bool:
    return any(y == year and _PL.shared_holdings(d)
               for y, d in _PL.year_dirs(_resolved(Path(root)).parent))


def compared_by(root: Path, day: str) -> List[int]:
    """The years of the year folders beside the project (its own too)
    that read the holdings folder every year shares (project_layout
    `shared_holdings`) and compare a snapshot dated `day` (year_window),
    oldest first: what `tjs fetch --positions` says of today's
    snapshot, never a year with no folder."""
    dirs = _PL.year_dirs(_resolved(Path(root)).parent)
    sharing = {y for y, d in dirs if _PL.shared_holdings(d)}
    return [y for y in sorted(sharing)
            if window_holds(day, y, y + 1 in sharing, y - 1 in sharing)]


def snapshot_year(day: str) -> int:
    """The year a snapshot dated `day` (YYYY-MM-DD) is named for (`tjs
    fetch --positions`' NAME_live_holdings_YYYY.toml): from January 1 to
    YEAR_END_THROUGH it is the year before's closing positions, later
    its own year's, so a year-end download never replaces the next
    year's file."""
    y = int(day[:4])
    return y - 1 if day[5:] <= f"01-{YEAR_END_THROUGH:02d}" else y


def holdings_year(root: Path, today: Optional[str] = None) -> Optional[int]:
    """The year a positions snapshot taken `today` (default: today) is of
    when the project reads a folder every year shares (its
    `tjs fetch --positions` file is named for it:
    NAME_live_holdings_YYYY.toml, so one year's never replaces
    another's); None for a folder of the project's own."""
    if not _PL.shared_holdings(Path(root)):
        return None
    import datetime as _dt
    return snapshot_year(today or _dt.date.today().isoformat())


def fetch_years(root: Path) -> Tuple[Optional[int], Optional[List[int]]]:
    """What `tjs fetch --positions` is told of a snapshot taken today
    (lib/fetchers FetchRequest): (the year its file is named for,
    holdings_year; the year folders that compare it, compared_by) in a
    project reading the folder every year shares, else (None, None)."""
    import datetime as _dt
    year = holdings_year(root)
    if year is None:
        return None, None
    return year, compared_by(root, _dt.date.today().isoformat())


def snapshot_id(path: Path) -> Tuple[str, str]:
    """(the broker account a snapshot names, normalised, else ""; its
    day, snapshot_day): two snapshots of one account with the same are
    added up by `tjs sanity`, one date at a time."""
    p = Path(path)
    broker = _norm(_meta(p).get("broker_account")) \
        if p.suffix.lower() == ".toml" else ""
    return broker, snapshot_day(p)


def snapshot_day(path: Path) -> str:
    """The day a snapshot is of, as `tjs sanity` dates it: a holdings
    TOML's [meta] as_of, else the day of its generated_at; a broker's
    report its own date. "" when it states none, or cannot be read (the
    compare then reads it and says why: never left out for that)."""
    import csv
    from taxjson.lib.positions_reports import read_positions, toml_as_of
    p = Path(path)
    try:
        if p.suffix.lower() == ".toml":
            return toml_as_of(_meta(p), p.name) or ""
        return read_positions(p).as_of or ""
    except (OSError, ValueError, LookupError, csv.Error):
        return ""                   # (PositionsReportError: a ValueError)


def year_files(root: Path, year: Optional[int] = None
               ) -> Tuple[List[Path], Optional[str]]:
    """(the snapshots of the project's holdings folder its `tjs sanity`
    compares, the note on the ones it leaves out, or None). A folder of
    the project's own, or one a `holdings_dir` of the person's names:
    all of them. The folder every year shares (project_layout
    `shared_holdings`): those dated in the year's window (year_window;
    `year` default the project's), and an undated one (compared with the
    latest books, by every year); the others are named, earlier and
    later, in one note that says how to compare one anyway."""
    root = Path(root)
    year, kept, early, late = _split_by_year(root, year)
    if not (early or late):
        return kept, None
    first, last = window_of(root, year)
    shown = _folder_shown(root, _PL.holdings_folder(root))
    parts = ([f"{len(early)} dated before {first}: {_names(early)}"]
             if early else []) + (
        [f"{len(late)} after {last}, {year + 1}'s: {_names(late)}"]
        if late else [])
    return kept, (f"{shown}: {len(early) + len(late)} snapshot(s) of other "
                  f"years not compared ({'; '.join(parts)}) — {year} "
                  f"compares those dated {window_span(first, last)}; to "
                  f"compare one anyway, "
                  f"name it: `tjs sanity ACCOUNT="
                  f"{shown}{mask((early + late)[0].name)}`")


def _names(files: List[Path]) -> str:
    """The first three files' names, then a count."""
    out = ", ".join(mask(p.name) for p in files[:3])
    return out + (f" +{len(files) - 3} more" if len(files) > 3 else "")


def other_years_files(root: Path) -> List[Path]:
    """The snapshots of a folder every year shares that the year leaves
    out (year_files' note names them); none in a folder of its own."""
    _year, _kept, early, late = _split_by_year(Path(root), None)
    return early + late


def _split_by_year(root: Path, year: Optional[int]
                   ) -> Tuple[int, List[Path], List[Path], List[Path]]:
    """(the year, the snapshots it compares, the others dated before its
    window, those after: year_files). A snapshot an account's own
    `holdings = [...]` names is never one of the others: that setting
    reads it, whatever its date."""
    files = snapshot_files(_PL.holdings_folder(root))
    if not files or not _PL.shared_holdings(root):
        return 0, files, [], []
    if year is None:
        year = _PL._settings(root).year
    if not isinstance(year, int) or isinstance(year, bool):
        return 0, files, [], []
    first, last = window_of(root, year)
    kept: List[Path] = []
    early: List[Path] = []
    late: List[Path] = []
    listed: Optional[set] = None
    for p in files:
        day = snapshot_day(p)
        if not day or _in_window(day, first, last):
            kept.append(p)
            continue
        if listed is None:
            accounts = _PL.read_config_soft(root).get("accounts")
            listed = listed_files(root, accounts if isinstance(
                accounts, dict) else {})
        if _resolved(p) not in listed:
            (early if first is not None and day < first
             else late).append(p)
    return year, kept, early, late


def folder_for(root: Path, year: Optional[int] = None
               ) -> Tuple[Optional[Path], Optional[str]]:
    """(the year's holdings folder when it holds a snapshot the year
    compares, else None; the note on the snapshots of other years it
    leaves out, or None: year_files)."""
    files, note = year_files(Path(root), year)
    return (_PL.holdings_folder(Path(root)) if files else None), note


def where_used(root: Path, accounts_cfg: Any) -> List[str]:
    """Where the positions files are that `tjs sanity` compares with no
    arguments, as the run's closing list names them (pipeline/run
    `_first_run_summary`): the snapshot folder when it holds one
    (folder_for: holdings/, or the folder `[settings] holdings_dir`
    names, shown from the project), and the accounts whose own
    `holdings = [...]` names files. [] when there are none."""
    out: List[str] = []
    folder = folder_for(Path(root))[0]
    if folder is not None:
        out.append(_folder_shown(Path(root), folder))
    named = sorted(n for n, a in configured_accounts(accounts_cfg).items()
                   if a.holdings)
    if named:
        shown = ", ".join(named[:3]) + (f" +{len(named) - 3} more"
                                        if len(named) > 3 else "")
        out.append(f"`holdings = [...]` of {shown}")
    return out


def _folder_shown(root: Path, folder: Path) -> str:
    """`holdings/`, `../holdings/`: the folder from the project (a
    `holdings_dir` stays beside the year folders, project_layout
    folder_setting), else its name alone."""
    import os
    try:
        rel = os.path.relpath(_resolved(folder), _resolved(root))
    except ValueError:                  # another drive
        rel = folder.name
    if rel.startswith(("../../", "..\\..\\")) or os.path.isabs(rel):
        rel = folder.name
    return rel.replace(os.sep, "/").rstrip("/") + "/"


def in_use(root: Path, accounts_cfg: Any) -> bool:
    """The project holds positions files `tjs sanity` compares with no
    arguments: an account's `holdings = [...]`, or a snapshot in the
    year's holdings folder (where_used). The checklist's sanity step
    asks it (checklist.d_sanity); the run's closing list names them."""
    return bool(where_used(root, accounts_cfg))


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
    for a in typed_accounts(accounts_cfg).values():
        for x in a.holdings_paths or []:
            if not isinstance(x, str) or not x.strip():
                continue
            pp = Path(x).expanduser()
            out.add(_resolved(pp if pp.is_absolute() else Path(root) / pp))
    return out


def discover(folder: Path, accounts_cfg: Dict[str, Any],
             root: Optional[Path] = None,
             info: Optional[List[str]] = None,
             files: Optional[List[Path]] = None
             ) -> Tuple[Dict[str, List[str]], List[str]]:
    """({account: [file paths]}, notes) for the folder's snapshots
    (`files`, when given: the ones of them the year compares,
    year_files). An
    account with its own `holdings = [...]` is left out (its setting
    wins), and so is a file such a setting names (`root`: the project
    the paths are relative to; default the folder's parent); a file no
    account claims, or two do, is named in a note. `info`, when given,
    takes the notes that leave nothing unchecked instead — a snapshot
    of an account whose own setting names its files: that account is
    compared with those (backlog TJ-0020)."""
    every = configured_accounts(accounts_cfg)
    accts = {n: a for n, a in every.items() if not a.holdings}
    listed = listed_files(Path(root) if root is not None
                          else Path(folder).parent, every)
    # Ownership by broker id is settled against EVERY account, the ones
    # with their own `holdings = [...]` included: a snapshot stating
    # such an account's id used to fall through to the file-name rule
    # and land on another account (audit 2026-10-24 A13).
    ids = {n: set(broker_ids(a)) for n, a in every.items()}
    files_of: Dict[str, List[str]] = {}
    notes: List[str] = []
    by_len = sorted(accts, key=lambda n: (-len(n), n))
    for p in (snapshot_files(folder) if files is None else files):
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
