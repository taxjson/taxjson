"""`tjs sanity`: positions against the broker's holdings (its own step, never part of `tjs run`)."""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from taxjson.project.accounts import account_of, accounts_of, typed_account, typed_accounts
from taxjson.project.toml_io import read_toml
from taxjson.project.settings import settings_of
from taxjson.lib import out as _out
from taxjson.lib import project_layout as _PL
from taxjson.lib.futures_prefixes import FUTURES_PREFIX_GROUP, canonical_futures_symbol
from taxjson.lib.json_contract import versioned
from taxjson.lib.report_model import format_report_table
from taxjson.model.result import Holding

from taxjson.pipeline.config import _country, input_files, load_config
from taxjson.pipeline.inputs import _home_currency
from taxjson.pipeline.messages import (
    _details, _die, _die_input, _out_relpath, _out_wrap,
)
from taxjson.pipeline.stages import _is_us
from taxjson.lib.privacy import mask_ids
# A holdings file's symbol in the books' spelling (backlog TJ-4605).
from taxjson.lib.symbol_case import book_symbol
from taxjson.model import money as M


def _print_report_table(rows: List[str], padding: str = "   ",
                        rule_before_last: bool = False, **fit) -> None:
    """Prints taxjson.lib.report_model.format_report_table's lines — see
    there (`fit=True, drop=..., key=...`: the house table that fits the
    width) — through an exact Doc: each line as print() printed it."""
    d = _out.Doc(exact=True)
    d.rows(format_report_table(rows, padding=padding,
                               rule_before_last=rule_before_last, **fit))
    _out.render(d)


def _json_out(doc: Dict[str, Any], schema: Optional[str] = None) -> None:
    """Machine output: pure JSON on stdout (notes/warnings stay on
    stderr) — the GUI's data source; never screen-scraped text: a Doc's
    JSON, out.render (lib/out.JSON_SPEC: indent 2, sorted keys,
    default=str). `schema`: the output's id in lib/json_contract.SCHEMAS,
    whose `schema_version` the doc is given (json_contract.versioned).

    STRICT JSON: non-finite floats are emitted as null (out.clean_json).
    json.dumps' default allow_nan=True wrote bare NaN/Infinity tokens,
    which are invalid JSON — node's JSON.parse throws and jq silently
    corrupts them (REVIEW #41)."""
    d = _out.Doc(width_=0)
    d.json = doc if schema is None else versioned(doc, schema)
    _out.render(d, as_json=True)


def _sanity_items_from_config(accounts_cfg: Dict[str, Any],
                              root: Path, year: Optional[int] = None,
                              kinds: Optional[Dict[str, List[str]]] = None
                              ) -> Tuple[List[Tuple[List[str], List[str]]],
                                         List[str]]:
    """Paired `tjs sanity` items from each account's
    `holdings = [...]` (paths; `~` expanded, relative to the project),
    and for the other accounts the snapshots of the year's holdings/
    folder (lib/holdings_dir: by broker account id or file name).
    Accounts that list a common file — one broker export covering
    several taxjson accounts — merge into one `a+b=file` group, since
    a file may sit in only one group. An account whose file is missing
    is left out with a note rather than compared against a partial
    set. Returns (items, notes). `kinds`, when given, is filled with
    which notes are which: "accounts" — the accounts whose `holdings`
    setting was not checked; "info" — the notes that leave nothing
    unchecked (a holdings/ snapshot of an account whose own setting
    names its files; backlog TJ-0020), also in notes; "snapshots" — the
    holdings/ snapshots no account (or two) claims (TJ-0256); "unread" —
    what holdings/ holds that is not read, a file that is no positions
    file or a sub-folder (TJ-0424)."""
    notes: List[str] = []
    _unchecked: List[str] = []
    files_of: Dict[str, List[str]] = {}
    accounts_cfg = typed_accounts(accounts_cfg)
    for name, acfg in accounts_cfg.items():
        raw = acfg.holdings_paths
        if raw is None:
            notes.append(f"account {name}: `holdings` must be a path "
                         f"or a list of paths — not checked")
            _unchecked.append(name)
            continue
        if not raw:
            continue
        paths: List[str] = []
        missing: List[str] = []
        for p in raw:
            pp = Path(str(p)).expanduser()
            if not pp.is_absolute():
                pp = root / pp
            (paths if pp.is_file() else missing).append(str(pp))
        if missing:
            # Masked like the file listing: holdings files are often
            # named after the broker account (S044-03).
            notes.append(f"account {name}: holdings file(s) missing — "
                         f"not checked: "
                         f"{', '.join(_mask_ids_in_path(Path(m).name) for m in missing)}")
            _unchecked.append(name)
            continue
        if paths:
            files_of[name] = paths
    from taxjson.lib import holdings_dir as _HD
    _hfolder, _hnote = _HD.folder_for(root, year)
    if _hnote:
        notes.append(_hnote)
    _info: List[str] = []
    _hnotes: List[str] = []
    if _hfolder is not None:
        _found, _hnotes = _HD.discover(_hfolder, accounts_cfg, root,
                                       info=_info)
        notes += _hnotes + _info
        for name, paths in _found.items():
            files_of.setdefault(name, []).extend(paths)
    # A file in holdings/ that is not read is named, never skipped with
    # "no holdings file" said beside it (backlog TJ-0424).
    _unread = _HD.unread_notes(_PL.holdings_folder(root), accounts_cfg,
                               root)
    notes += _unread
    # Union-find over accounts that share a file.
    parent = {n: n for n in files_of}

    def _find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    owner: Dict[str, str] = {}
    for n, paths in files_of.items():
        for p in paths:
            if p in owner:
                parent[_find(n)] = _find(owner[p])
            else:
                owner[p] = n
    groups: Dict[str, Tuple[List[str], List[str]]] = {}
    for n in files_of:
        accts, files = groups.setdefault(_find(n), ([], []))
        accts.append(n)
        for p in files_of[n]:
            if p not in files:
                files.append(p)
    # Structured (accounts, files) groups — never re-joined into the
    # `a+b=f1+f2` argument syntax, so a `+` or `=` inside a path is safe.
    items = sorted(((sorted(a), list(f)) for a, f in groups.values()),
                   key=lambda g: g[0])
    if kinds is not None:
        kinds["accounts"] = _unchecked
        kinds["info"] = _info
        kinds["snapshots"] = list(_hnotes)
        kinds["unread"] = list(_unread)
    return items, notes


def _named_fold(sym: str, where: str) -> str:
    """`sym` with a future's `/` or `\\` prefix `F:` (lib/futures_prefixes,
    backlog TJ-5150); one that names no contract (`//QZM26.US`) stops the
    command, naming the file (`where`): exit 2, an input it cannot read."""
    try:
        return canonical_futures_symbol(sym)
    except ValueError as e:
        _die_input(f"{where}: {e} — fix the file.")
        raise


def _file_symbol(v: Any, spelled: Dict[str, str], where: str) -> str:
    """A holdings file's `symbol` as the books spell it: upper case
    (lib/symbol_case), a future's `/` or `\\` prefix `F:` (_named_fold).
    `spelled` takes the file's own spelling of a symbol the fold changed
    (`file_symbol` shows it)."""
    raw = book_symbol(str(v or "").strip())
    sym = _named_fold(raw, where)
    if sym != raw:
        spelled[sym] = raw
    return sym


def _mask_ids_in_path(path: str) -> str:
    """`path` with every account-id-shaped token in its FILE NAME masked
    to its first 2 characters + *** (U1234567 -> U1***): the one grammar,
    `taxjson.lib.privacy.mask_ids`, a row's `source` follows."""
    head, sep, name = str(path).rpartition("/")
    return head + sep + mask_ids(name)


def _sanity_note(text: str) -> None:
    """`tjs sanity: note: ...` on stderr, wrapped (lib/out)."""
    from taxjson.lib.out import note
    note(text, prog="tjs sanity")


def _project_file(root: Path, name: str) -> Optional[Path]:
    """`name`, a relative FILE argument that is not under the shell's
    folder, as the project's own file when it is one there (`-C DIR`
    does not change the shell's folder), else None."""
    try:
        q = Path(name).expanduser()
        if q.is_absolute():
            return None
        q = root / q
        return q if q.is_file() else None
    except (OSError, RuntimeError):
        return None


def _not_finite(v) -> str:
    """How sanity says an inventory quantity is not a finite number: a
    400-digit integer (a view reads it as inf) as the row checks say it,
    not in full (backlog TJ-3056)."""
    if type(v) is int:
        return f"is an integer of {len(str(abs(v)))} digits, too large to read"
    return f"{v!r} is not a finite number"


def _read_books(resolved: Dict[str, Path]) -> Tuple[Dict[str, Dict[str, float]],
                                                     Dict[str, str]]:
    """`tjs sanity`'s books: each account's open positions from its gains
    file's inventory, {account: {symbol: quantity}}, and the accounts
    whose file cannot be read (or holds a quantity that is not a finite
    number), {account: "work/<file>: <reason>"}."""
    import json
    from taxjson.lib.json_input import check_gains_parts
    tax: Dict[str, Dict[str, float]] = {}
    unreadable: Dict[str, str] = {}
    for acct, f in resolved.items():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a gains document")
            # `{}` is a damaged gains file, never an empty book that
            # compares clean (backlog TJ-0412).
            check_gains_parts(data, f)
        except (OSError, ValueError) as e:
            # Said once, below: the Error that refuses an account the
            # check takes in (_account), else a warning that it is left
            # out (_warn_left_out) — a warning by its absolute path
            # came first and the Error said it again (backlog TJ-0631).
            unreadable[acct] = f"work/{f.name}: {e}"
            continue
        book: Dict[str, float] = {}
        bad_qty = ""
        for h in (data.get("inventory") or []):
            sym = str(h.get("symbol") or "")
            _qraw = h.get("qty", 0)
            try:
                q = float(Holding(h).quantity or 0)
            except ValueError:      # ResultShapeError: not a number
                q = float("nan")
            if q != q or q in (float("inf"), float("-inf")):
                # A NaN quantity failed every comparison, so the
                # position vanished and the account's empty book
                # compared clean (audit 2026-10-24 B19). The books are
                # unreadable: an account they belong to is refused
                # below (_account), as a gains file that is not JSON is.
                bad_qty = f"work/{f.name}: {sym or '?'}: inventory qty {_not_finite(_qraw)}"
                break
            if sym and abs(q) > 1e-12:
                book[sym] = book.get(sym, 0.0) + q
        if bad_qty:
            unreadable[acct] = bad_qty
            continue
        tax[acct] = book
    return tax, unreadable


def _renames(root: Path) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]],
                                  Set[frozenset], Any]:
    """The renames sanity applies to the files' symbols, as the books were
    merged: (to the base listing, to the native listing, the DISTINCT
    pairs, the map's map_symbol), each None (or empty) with no map or one
    that cannot be read (a warning)."""
    renames: Optional[Dict[str, str]] = None
    native_renames: Optional[Dict[str, str]] = None
    distinct: Set[frozenset] = set()
    map_symbol = None
    from taxjson.lib.project_layout import EFFECTIVE_MAP as _EFF_MAP
    map_file = root / "work" / _EFF_MAP
    if not map_file.is_file():
        map_file = _PL.ticker_map_path(root)
    if map_file.exists():
        try:
            from taxjson.lib.ticker_map_rules import map_symbol, merge_renames
            from taxjson.lib.map_file import load_map_file
            _tmap = load_map_file(map_file)
            renames = merge_renames(_tmap, to_base=True)
            native_renames = merge_renames(_tmap, to_base=False)
            # The pairs ticker.map declares DISTINCT: two securities,
            # never one spelled two ways (_sanity_spellings).
            distinct = set(_tmap.distinct or ())
        except Exception as e:
            from taxjson.lib.out import warn as _warn
            _warn(f"ticker.map not applied ({e}) — cross-listed symbols "
                  f"may mismatch.", prog="tjs sanity")
            renames = None
    return renames, native_renames, distinct, map_symbol


def _nothing_to_compare_json(*, basis: str, cost_tol: float, _inc: List[Dict[str, Any]],
                             _unchk: List[str], _unread: List[str]) -> Dict[str, Any]:
    """`tjs sanity --json` when no account has a holdings file: a document
    like the compare's, nothing compared."""
    return {
        "basis": basis, "accounts": [], "files": [],
        "groups": [],
        "cost": {"compared": 0, "matched": 0,
                 "not_compared": 0, "accounts_not_compared": [],
                 "tolerance": cost_tol, "rows": []},
        "cost_differences": [], "income_share_mismatches": _inc,
        "discrepancies": [], "uncovered_accounts": _unchk,
        "accounts_not_checked": [], "notes": _unread,
        "clean": True,
        "complete": not _unchk and not _unread}


def _sanity_json_doc(*, basis: str, accounts: List[str], files: List[Path],
                     file_labels: List[str], ordered: List[Dict[str, Any]],
                     cost_matched: int, cost_diffs: List[Dict[str, Any]], cost_na: int,
                     cost_skipped: List[str], cost_tol: float, cost_all: List[Dict[str, Any]],
                     income_all: List[Dict[str, Any]], all_rows: List[Dict[str, Any]],
                     uncovered: List[str], cfg_unchecked: List[str], config_notes: List[str],
                     open_notes: List[str], items: List[str]) -> Dict[str, Any]:
    """`tjs sanity --json`'s document: the groups, the costs, the income
    share counts and the verdict (`clean`, `complete`)."""
    return {
        "basis": basis,
        "accounts": accounts,
        # The [meta] account label may be a broker account id:
        # masked like the text listing (S044-16).
        "files": [{"file": _mask_ids_in_path(str(p2)),
                   "file_account": _mask_ids_in_path(lbl)}
                  for p2, lbl in zip(files, file_labels)],
        "groups": [{"accounts": sorted(g["accounts"]),
                    "paired": g["paired"],
                    "files": [_mask_ids_in_path(str(p2))
                              for p2 in g["files"]],
                    "matched_via_underlying": [
                        {"file_symbol": g.get("file_spelling", {}).get(a, a), "taxjson_symbol": b}
                        for a, b in g["via_underlying"]],
                    "fractions_awaiting_cash_in_lieu":
                        g.get("awaiting_cash") or [],
                    "discrepancies": g["rows"],
                    "as_of": g["as_of"] or None,
                    "clean": not g["rows"]}
                   for g in ordered],
        # Costs and income share counts (lib/positions_check):
        # informational, never part of `clean` or the exit code.
        "cost": {"compared": cost_matched + len(cost_diffs),
                 "matched": cost_matched,
                 "not_compared": cost_na,
                 # (registered accounts: book cost is not tax cost)
                 "accounts_not_compared": sorted(set(cost_skipped)),
                 "tolerance": cost_tol,
                 "rows": cost_all},
        "cost_differences": cost_diffs,
        "income_share_mismatches": income_all,
        "discrepancies": all_rows,
        "uncovered_accounts": uncovered,
        # (the accounts whose `holdings` setting was not checked —
        # each named in notes — open positions or not)
        "accounts_not_checked": cfg_unchecked,
        "notes": config_notes,
        "clean": not all_rows,
        # False when an account's CONFIGURED holdings file could
        # not be read (R1-324), a holdings/ snapshot was not
        # compared, or — in the config form — an account with open
        # positions has no holdings file at all (S044-19): its
        # positions were never compared, so "clean" covers the
        # other groups only.
        "complete": not open_notes and not (uncovered
                                            and not items),
    }


class _Placement:
    """`tjs sanity`'s items placed in their groups (cmd_sanity's closures
    before step 15, as methods of the same names). A group is (accounts,
    files): bare items all land in one AGGREGATE group, `A[+A]=F[+F]`
    items in a PAIRED group keyed by its accounts, so a repeated
    left-hand side merges. Each account and file sits in at most one
    group: a second placement is a usage error, never a double count."""

    def __init__(self, root: Path, tax: Dict[str, Dict[str, float]],
                 unreadable: Dict[str, str]) -> None:
        self.root = root
        self.tax = tax
        self.unreadable = unreadable
        self._declared_memo: List[Dict[str, Any]] = []
        self.groups: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        self.bare: Dict[str, Any] = {"accounts": [], "files": [], "paired": False}
        self.placed_accounts: Dict[str, str] = {}
        self.placed_files: Dict[Path, str] = {}

    def _declared(self) -> Dict[str, Any]:
        """taxjson.toml's accounts, read once. (A taxjson.toml that
        cannot be read stops here with its own Error, as the compare
        below would.)"""
        if not self._declared_memo:
            self._declared_memo.append(dict(
                (load_config(self.root) if _PL.has_config(self.root) else {})
                .get("accounts") or {}))
        return self._declared_memo[0]

    def _account(self, name: str, ctx: str) -> str:
        if name in self.tax:
            return name
        if name in self.unreadable:
            # Its book exists but is corrupt: say so, not "not an
            # account" (R1-338 — that sent the user to the config).
            # The damaged file leads, not taxjson.toml (backlog TJ-0057).
            # Exit 2: an input that cannot be read (TJ-0353).
            _die_input(f"{self.unreadable[name]} — the books of {name!r} "
                       f"cannot be read: re-run `tjs run` to rebuild "
                       f"them.")
        if name in self._declared():
            # An account of taxjson.toml with no books: it was refused
            # as "not an account" (backlog TJ-0639). No inputs, no
            # books, nothing to compare its holdings with — said, with
            # its inputs folder; with inputs, the run did not build
            # them. Exit 2: an input missing (docs/commands.md).
            self._no_books(name, ctx)
        # A usage error, exit 2; 1 stays the verdict "positions differ"
        # (docs/commands.md, Exit codes; backlog TJ-0630).
        _die_input(f"{ctx}: {name!r} is not an account "
                   f"(have: {', '.join(sorted(self.tax))})")

    def _no_books(self, name: str, ctx: str) -> None:
        """Refuse account NAME of taxjson.toml, which has no gains file
        in work/ (exit 2), saying why: its run did not build them, or it
        has no inputs to build them from (backlog TJ-0639)."""
        if _has_inputs(self.root, name):
            _die_input(f"{ctx}: no gains file in work/ for account "
                       f"{name!r}, whose inputs are there — the last "
                       f"`tjs run` did not build them (did it "
                       f"fail?): fix and re-run `tjs run`.")
        _in = _PL.inputs_dir(self.root) / name
        _shown = _PL.shown(_in, self.root)
        _how = ("leave it out of the check" if ctx != "taxjson.toml"
                else "take out its "
                + ("`holdings` setting"
                   if typed_account(self._declared().get(name), name).holdings
                   else "snapshot in "
                   + _PL.shown(_PL.holdings_folder(self.root), self.root) + "/")
                + " until it has some")
        _die_input(f"{ctx}: account {name!r} has no inputs "
                   f"({_shown}/ "
                   + ("holds no export" if _in.is_dir() else "is missing")
                   + "): nothing to compare",
                   f"Its holdings are compared with the books `tjs "
                   f"run` builds from its exports. Save them in "
                   f"{_shown}/ and run `tjs run`, or {_how}.")

    def _warn_left_out(self) -> None:
        """Each account whose books cannot be read and that this check
        does not take in: one warning naming its work/ file. One it
        takes in never gets here: _account refuses it with an Error
        that says it all (backlog TJ-0631)."""
        from taxjson.lib.out import warn as _warn
        for _a, _why in self.unreadable.items():
            _file, _, _reason = _why.partition(": ")
            _warn(f"could not read {_file} — {_a!r} is not in this check",
                  prog="tjs sanity",
                  details=[_reason, "Re-run `tjs run` to rebuild it."])

    def _resolved(self, name: str) -> Optional[Path]:
        """The file a holdings argument names, or None when it is not
        one: a symlink loop or an unreadable path is one clean line,
        never a traceback (audit A2-1392)."""
        try:
            p2 = Path(name).expanduser().resolve()
            if p2.is_file():
                return p2
            # The docs' `sanity ib=ib_positions.toml` run with -C from
            # another folder: the project's copy (workflow audit 2026-10).
            p3 = _project_file(self.root, name)
            return p3.resolve() if p3 is not None else None
        except (OSError, RuntimeError):
            return None

    def _file(self, name: str, ctx: str) -> Path:
        p2 = self._resolved(name)
        if p2 is not None:
            return p2
        _die_input(f"{_mask_ids_in_path(ctx)}: "
                   f"{_mask_ids_in_path(name)!r} is not an existing "
                   f".toml file (or a symlink loop)",
                   "A relative FILE is looked for from the shell's folder, "
                   "then from the project's.")

    def _place(self, group: Dict[str, Any], gname: str, acct: Optional[str],
                     path: Optional[Path]) -> None:
        if acct is not None:
            owner = self.placed_accounts.get(acct)
            if owner is None:
                self.placed_accounts[acct] = gname
                group["accounts"].append(acct)
            elif owner == gname:
                # Summing a duplicate DOUBLED its positions and every
                # one showed as a 2x QTY_MISMATCH — while the header
                # printed the deduplicated list. Count once, say so.
                _sanity_note(f"account {acct!r} given more than once — "
                             f"counted once.")
            else:
                _die_input(f"account {acct!r} appears in "
                           f"more than one group ({owner} and {gname})")
        if path is not None:
            owner = self.placed_files.get(path)
            if owner is None:
                self.placed_files[path] = gname
                group["files"].append(path)
            elif owner == gname:
                _sanity_note(f"file {_mask_ids_in_path(path.name)} given "
                             f"more than once — counted once.")
            else:
                _die_input(f"file "
                           f"{_mask_ids_in_path(path.name)} appears in "
                           f"more than one group ({owner} and {gname})")


class _Files:
    """The holdings files' side of `tjs sanity` (cmd_sanity's closures
    before step 15, as methods of the same names): each file's book, its
    day and broker account, and each account's rows of the books, read
    once; what the readers record are its attributes."""

    # (an option on a future in any of its prefix spellings, as
    # ticker_map_rules.map_symbol reads one: backlog TJ-5037)
    _OPT_RE = re.compile(r'^(' + FUTURES_PREFIX_GROUP
                         + r'?[A-Z0-9.]+?)(\d{6}[CP]\d+)\.(\S+)$',
                         re.IGNORECASE)
    _VENUE_SFX_RE = re.compile(r'^([A-Za-z0-9]+)\.[A-Za-z]{2,3}$')

    def __init__(self, root: Path, cache: Path, mapped: Any,
                 country: Any = None) -> None:
        self.root = root
        self.cache = cache
        self._mapped = mapped
        # Whether the project books USD stablecoins as US-dollar cash
        # (the parse's basis, rules/country `stablecoin_model`), and the
        # units of each one the files' crypto rows hold, by file: left out
        # of the compare as a cash row is (`_cash_coin`, backlog TJ-4950).
        from taxjson.lib.country import stablecoin_model
        self.stable_cash = bool(country) and stablecoin_model(country) == "cash"
        self.cash_coins: Dict[Path, Dict[str, float]] = {}
        # (the accounts each file is compared with; the books' positions)
        self._accounts_of: Dict[Path, List[str]] = {}
        self.tax: Dict[str, Dict[str, float]] = {}
        self.reports_of: Dict[Path, Any] = {}
        # (a holdings TOML's [meta] table and date, and the reader's error
        # for one whose rows it refused: its cost is then not compared)
        self._toml_meta: Dict[Path, Dict[str, Any]] = {}
        self._toml_day: Dict[Path, str] = {}
        self._unread_cost: Dict[Path, str] = {}
        # (a futures symbol as a file spells it, `/` or `\`: _file_symbol)
        self.spelled: Dict[str, str] = {}
        self.base_rows: Dict[str, List[Dict[str, Any]]] = {}

        # (the year's holdings/ snapshots: dated by generated_at too —
        # lib/holdings_dir)
        self._snap_dirs = {_PL.holdings_folder(root).resolve()}

    def _cash_coin(self, asset_type: Any, symbol: Any) -> str:
        """The coin of a crypto row that the project's books hold as
        US-dollar cash, "" for any other row: a USD stablecoin (the
        parsers' own answer, lib/markets `is_usd_stablecoin`: the shipped
        list as ticker.map STABLE lines change it) in a project whose
        parse folds them into US dollars (a Canadian one, tax-logic
        CA-CRYPTO-02). In a US project it is a coin like any other
        (US-CRYPTO-02), and so it is with no country. The code is read as
        the Kraken parser reads an asset code, after the snapshot's venue
        suffix: a wallet suffix (`USDC.F`, `USDC.HOLD`) names the coin."""
        if not self.stable_cash or str(asset_type or "").lower() != "crypto":
            return ""
        from taxjson.lib.brokerages.kraken import _normalize_asset
        from taxjson.lib.markets import is_usd_stablecoin
        sym = str(symbol or "").strip().upper()
        m = self._VENUE_SFX_RE.match(sym)
        coin = _normalize_asset(m.group(1) if m else sym, fold_stable=False,
                                bonded=False)
        return coin if is_usd_stablecoin(coin) else ""

    # Rows that pay income or move cash in a coin's name without making
    # it a position: a Canadian book's reward in a USD stablecoin is a
    # DIVIDEND row of the coin, its units US-dollar cash.
    _INCOME_ACTIONS = frozenset({"DIVIDEND", "DIVIDEND_IN_LIEU", "TAX",
                                 "INTEREST", "FEE"})

    def _books_hold(self, accounts: List[str], coin: str) -> bool:
        """Whether the books of one of `accounts` carry the coin as a
        POSITION: a non-zero quantity in the inventory, or a row that
        acquires, disposes of or moves it as property (a `.tt` BUYSELL
        line of a stablecoin books one). An income or cash-flow row
        (`_INCOME_ACTIONS`: a reward paid in the coin) is no position."""
        names = {coin, str(self._mapped(coin)).upper()}
        for acct in accounts:
            if any(str(s).upper() in names and abs(q) > 1e-12
                   for s, q in (self.tax.get(acct) or {}).items()):
                return True
            if any(str(r.get("symbol") or "").upper() in names
                   and str(r.get("action") or "").upper() not in self._INCOME_ACTIONS
                   for r in self._rows_of(acct)):
                return True
        return False

    def _skip_cash_coin(self, path: Path, asset_type: Any, symbol: Any,
                        quantity: float) -> bool:
        """Whether the row is a stablecoin the books hold as cash
        (`_cash_coin`) and as no position (`_books_hold`, over the
        accounts the file is compared with): its units are then noted
        under its file, and the row is left out. A stablecoin the books
        do hold as a position is compared as any coin."""
        coin = self._cash_coin(asset_type, symbol)
        if not coin or self._books_hold(self._accounts_of.get(path, []), coin):
            return False
        held = self.cash_coins.setdefault(path, {})
        held[coin] = held.get(coin, 0.0) + quantity
        return True

    def note_cash_coins(self, groups: List[Dict[str, Any]], notes: List[str],
                        kinds: Dict[str, List[str]]) -> None:
        """The stablecoin rows the groups' files hold that were left out
        as cash, said: one note added to `notes` (printed, and the JSON's
        `notes`) and to the "info" `kinds`, the notes that leave nothing
        unchecked. Never dropped in silence."""
        files = [p for g in groups for p in g["files"]]
        tot: Dict[str, float] = {}
        for p in files:
            for coin, q in (self.cash_coins.get(p) or {}).items():
                tot[coin] = tot.get(coin, 0.0) + q
        if not tot:
            return
        coins = ", ".join(f"{c} ({q:g})" for c, q in sorted(tot.items()))
        note = (f"not compared as a coin: {coins} in "
                + ", ".join(_mask_ids_in_path(p.name) for p in files
                            if self.cash_coins.get(p))
                + " — a USD stablecoin is US-dollar cash in this project's books "
                "(tax-logic CA-CRYPTO-02), so the positions hold none and the row "
                "is left out as a cash row is; the cash it is part of is `tjs "
                "fx-cash`'s to reconcile. Not a stablecoin: `STABLE <coin> NO` "
                "in ticker.map, then `tjs run`.")
        notes.append(note)
        kinds.setdefault("info", []).append(note)

    def _ext_book(self, paths: List[Path]) -> Tuple[Dict[str, float],
                                                    List[str],
                                                    Dict[str, str]]:
        """(book, file labels, alt) — `alt` maps an option's symbol
        as the FILE spells it to the same contract keyed by the
        row's `underlying` root. Brokers and tools disagree on the
        option ROOT: IB names a Montréal contract on a class share ABC.B
        `ABC.B 17DEC27 30 C` (taxjson keys ABC.B271217C00030000.TO)
        while a positions export keys it by the exchange option
        root `ABC...` — same contract, and the export's `underlying
        = "ABC.B.TO"` field says so. The compare below falls back to
        the underlying spelling only when the file's spelling has no
        taxjson counterpart, so a real mismatch still shows."""
        from taxjson.lib.brokerages.base import shown_name
        from taxjson.lib.positions_reports import (PositionsDateError,
                                                   PositionsReportError,
                                                   PositionsSnapshotError,
                                                   empty_snapshot,
                                                   read_positions, toml_as_of)
        book: Dict[str, float] = {}
        labels: List[str] = []
        alt: Dict[str, str] = {}
        for path in paths:
            if path.suffix.lower() != ".toml":
                # A broker's own positions report (CSV): read by
                # lib/positions_reports, one reader per broker export.
                try:
                    rep_ = read_positions(path)
                except PositionsReportError as e:
                    _die_input(str(e))
                self.reports_of[path] = rep_
                labels.append(", ".join(rep_.accounts) or path.stem)
                for r in rep_.rows:
                    if r.asset_type == "cash" or abs(r.quantity) <= 1e-12:
                        continue
                    if self._skip_cash_coin(path, r.asset_type, r.symbol, r.quantity):
                        continue
                    tgt = self._mapped(r.symbol)
                    book[tgt] = book.get(tgt, 0.0) + r.quantity
                continue
            # The same file through the positions reader, for its cost
            # columns (the quantity checks below stay the authority).
            try:
                self.reports_of[path] = read_positions(path)
            except (PositionsDateError, PositionsSnapshotError) as e:
                # An impossible date (an `acquired` of 2024-13-01) is
                # refused, as `tjs opening` refuses it: never a note.
                # So is a file that is not a whole snapshot: a
                # holdings_count that is not its number of tables, a
                # contract size that is no positive number. The quantities below
                # never check those, and a cut-short file compared "OK"
                # with the positions it lost unseen (audit 2026-10-25
                # brokers-inputs#1).
                _die_input(str(e))
            except PositionsReportError as e:
                self.reports_of[path] = None
                self._unread_cost[path] = str(e)
            # A broker/position file re-saved with a BOM was refused
            # (re-audit A2-1409, A2-1453); unreadable is exit 2 (A2-0164).
            try:
                doc = read_toml(path)
            except Exception as e:
                _die_input(f"cannot parse {path}: {e}")
            meta = doc.get("meta") if isinstance(doc, dict) else None
            self._toml_meta[path] = meta if isinstance(meta, dict) else {}
            # Its date picks the books it is compared with: an
            # impossible one (2025-02-30) is refused, never read as
            # "no date"; a cost the reader refused leaves the date.
            try:
                self._toml_day[path] = toml_as_of(meta, shown_name(path))
            except PositionsReportError as e:
                _die_input(str(e))
            labels.append(str((meta or {}).get("account") or path.stem)
                          if isinstance(meta, (dict, type(None)))
                          else path.stem)
            holdings = doc.get("holding") if isinstance(doc, dict) else None
            if holdings is None and empty_snapshot(doc):
                holdings = []       # the account holds nothing (#46)
            # A file it cannot read as a snapshot is an input that cannot
            # be read: exit 2, named from the project (docs/commands.md;
            # exit 1 stays "positions differ", backlog TJ-0643).
            _named = _mask_ids_in_path(_out_relpath(path, self.root))
            if not isinstance(holdings, list):
                _die_input(f"{_named} has no [[holding]] "
                           f"array (a holdings file has [[holding]] tables)")
            for h in holdings:
                if not isinstance(h, dict):
                    _die_input(f"{_named}: a [[holding]] "
                               f"entry is not a table")
                if (str(h.get("asset_type") or "")).lower() == "cash":
                    continue
                sym = _file_symbol(h.get("symbol"), self.spelled, _named)
                _qraw = h.get("quantity")
                if _qraw is None or (isinstance(_qraw, str)
                                     and not _qraw.strip()):
                    # A missing or blank quantity read as 0 and the row
                    # vanished: sanity said OK and the checklist ticked
                    # the step (S044-18).
                    _die_input(f"{_named}: "
                               f"{sym or '?'}: a [[holding]] row has no "
                               f"quantity — fix the file (an unreadable "
                               f"row is never skipped).")
                try:
                    # A TOML boolean is not a quantity (`true` read as 1,
                    # re-audit A2-1230).
                    if isinstance(_qraw, bool):
                        raise TypeError("bool")
                    q = float(_qraw)
                except (TypeError, ValueError):
                    _die_input(f"{_named}: {sym or '?'}: "
                               f"quantity {h.get('quantity')!r} is not a "
                               f"number")
                if q != q or q in (float("inf"), float("-inf")):
                    _die_input(f"{_named}: {sym or '?'}: "
                               f"quantity is not finite")
                if not sym and abs(q) > 1e-12:
                    _cus = str(h.get("cusip") or h.get("isin")
                               or "").strip()
                    _die_input(f"{_named}: a [[holding]] "
                               f"row has quantity {q:g} but no symbol"
                               + (f" ({_cus})" if _cus else "")
                               + " — it cannot be compared; fix the file.")
                if not sym or abs(q) <= 1e-12:
                    continue
                if self._skip_cash_coin(path, h.get("asset_type"), sym, q):
                    continue
                if (str(h.get("asset_type") or "").lower() == "crypto"
                        and self._VENUE_SFX_RE.match(sym)):
                    # A snapshot tool's venue suffix (e.g.
                    # `COIN.KR` for Kraken): taxjson keys crypto by the
                    # bare coin, so every coin showed twice, MISSING on
                    # each side (R1-113, R1-334).
                    sym = self._VENUE_SFX_RE.match(sym).group(1)
                tgt = self._mapped(sym)
                book[tgt] = book.get(tgt, 0.0) + q
                und = _named_fold(str(h.get("underlying") or "").strip(), _named)
                m = self._OPT_RE.match(sym)
                if und and m and "." in und:
                    und_root, und_ext = self._mapped(und).rsplit(".", 1)
                    via = f"{und_root}{m.group(2)}.{und_ext}"
                    if via != tgt:
                        alt[tgt] = via
        return book, labels, alt

    def _rows_of(self, acct: str) -> List[Dict[str, Any]]:
        from taxjson.lib import positions_check as PC
        if acct not in self.base_rows:
            # (with the missing-history openings the books hold:
            # units bought before the data — PC.book_rows). Books that
            # are there but cannot be read are not an empty history:
            # read as one, a dated snapshot was compared with the
            # latest books and certified (audit 2026-10-24 B18).
            try:
                self.base_rows[acct] = PC.book_rows(self.cache, acct, strict=True)
            except PC.BaseBookError as e:
                # (exit 2, an input that cannot be read: TJ-0353)
                _die_input(f"the books of {acct!r} cannot be read ({e}) — "
                           f"re-run `tjs run` to rebuild them")
        return self.base_rows[acct]

    def _file_day(self, _p: Path) -> str:
        """The day a file's positions are for ("" when it states none):
        a broker report's date; a holdings TOML's explicit [meta]
        as_of — a holdings/ snapshot's the day of its generated_at too
        (lib/holdings_dir)."""
        _rp = self.reports_of.get(_p)
        _day = _rp.as_of if _rp is not None else self._toml_day.get(_p, "")
        if not _day:
            return ""
        if (_p.suffix.lower() == ".toml" and _p.parent not in self._snap_dirs
                and not (self._toml_meta.get(_p) or {}).get("as_of")):
            return ""
        return _day

    def _file_broker(self, _p: Path, accts: List[str]) -> str:
        """The broker account a file's positions are of, as the file
        states it ("" when it names none, or only a taxjson account's
        name): a holdings TOML's [meta] broker_account, else its
        account (as lib/holdings_dir claims it); a broker report's
        account keys."""
        if _p.suffix.lower() == ".toml":
            _meta = self._toml_meta.get(_p) or {}
            for _k in ("broker_account", "account"):
                _v = str(_meta.get(_k) or "").strip()
                if _v and _v.lower() not in {a.lower() for a in accts}:
                    return re.sub(r"[^A-Za-z0-9]", "", _v).upper()
            return ""
        _rp = self.reports_of.get(_p)
        if _rp is None:
            return ""
        return "+".join(sorted({(r.account_key or r.account)
                                for r in _rp.rows
                                if r.account_key or r.account})
                        or _rp.accounts)


def _config_groups(root: Path, resolved: Dict[str, Path], unreadable: Dict[str, str],
                   config_kinds: Dict[str, List[str]]
                   ) -> Tuple[List[Tuple[List[str], List[str]]], List[str], Dict[str, Any]]:
    """With no items, the groups taxjson.toml pairs (each account's
    `holdings`, the holdings/ snapshots): (groups, notes, the configured
    accounts), `config_kinds` filled (_sanity_items_from_config); an
    account whose books cannot be read, or that has inputs and no books,
    refused first."""
    # Not swallowed: an unreadable taxjson.toml said "no account
    # declares `holdings`" and invited adding keys already there
    # (S049-08) — load_config names the TOML error instead.
    _scfg = load_config(root)
    _accts_cfg = accounts_of(_scfg)
    # Every account of taxjson.toml is in this check: one whose books
    # cannot be read, or that has inputs and no books at all (its
    # run failed), is refused here — it dropped out of the coverage,
    # and "clean, complete" covered the others only (backlog
    # TJ-0307). Named, and refused, as when it is paired with a file
    # (_account).
    # (exit 2, an input that cannot be read: TJ-0353)
    _bad = [a for a in _accts_cfg if a in unreadable]
    if _bad:
        _die_input(f"{unreadable[_bad[0]]} — the books of {_bad[0]!r} "
                   f"cannot be read: re-run `tjs run` to rebuild "
                   f"them"
                   + (f" (also: {', '.join(_bad[1:])})"
                      if len(_bad) > 1 else "") + ".")
    _absent = [a for a in _accts_cfg if a not in resolved
               and _has_inputs(root, a)]
    if _absent:
        _die_input(f"no gains file in work/ for account(s) "
                   f"{', '.join(_absent)}, whose inputs are there — "
                   f"the last `tjs run` did not build them (did it "
                   f"fail?): fix and re-run `tjs run`.")
    config_groups, config_notes = _sanity_items_from_config(
        _accts_cfg, root, settings_of(_scfg).year,
        kinds=config_kinds)
    return config_groups, config_notes, _accts_cfg


def _no_groups_refusal(pl: "_Placement", root: Path, config_notes: List[str],
                       config_kinds: Dict[str, List[str]]) -> List[str]:
    """taxjson.toml pairs no group: the accounts left out warned, the
    notes said, and refused when one says a `holdings` path or a
    holdings/ snapshot is wrong. Returns the notes on the files in
    holdings/ that are not read."""
    pl._warn_left_out()
    # A file holdings/ holds that is not read is named; alone it
    # leaves the accounts unchecked, as with no file (TJ-0424).
    _unread = list(config_kinds.get("unread") or ())
    for n in config_notes:
        _sanity_note(n)
    if [n for n in config_notes if n not in _unread]:
        # What the notes are about: an account's `holdings` paths
        # (fix the paths), or holdings/ snapshots no account
        # claims (claim them: no path is wrong; backlog TJ-0256).
        _n_paths = len(config_kinds.get("accounts") or ())
        _snaps = len(config_kinds.get("snapshots") or ())
        _hold = _PL.shown(_PL.holdings_folder(root), root)
        # Exit 2, not the verdict's 1: a named input missing or
        # one no account claims compared nothing; 1 stays
        # "positions differ" (docs/commands.md, Exit codes;
        # backlog TJ-0640, as TJ-0353 and TJ-0630 did).
        if _snaps and not _n_paths:
            _die_input(f"none of the snapshots in {_hold}/ could "
                       f"be compared (see the notes above) — "
                       f"claim each for its account",
                       "Add the account's broker account id to "
                       "its [accounts.NAME] (`broker_accounts = "
                       "[\"...\"]`) and the same `broker_account` "
                       "to the file's [meta], name the file "
                       "NAME_holdings.toml, or list it in the "
                       "account's `holdings = [...]`.")
        _die_input("none of the `holdings` files "
                   "in taxjson.toml (or the holdings/ folder) "
                   "could be checked (see the notes above) — fix "
                   "the "
                   + ("paths." if not _snaps else
                      "paths, and claim each snapshot for its "
                      "account."))
    return _unread


def _group_items(pl: "_Placement", config_groups: List[Tuple[List[str], List[str]]],
                 items: List[str]) -> List[Dict[str, Any]]:
    """The groups to compare: taxjson.toml's pairs, then the items (each
    `A[+A]=F[+F]` a paired group, the bare ones the aggregate group), in
    that order; a usage error refused. The accounts left out warned."""
    tax, unreadable, groups, bare = pl.tax, pl.unreadable, pl.groups, pl.bare
    for accts, paths in config_groups:
        key = tuple(accts)
        gname = "+".join(key)
        grp = groups.setdefault(key, {"accounts": [], "files": [],
                                      "paired": True})
        for n in key:
            pl._place(grp, gname, pl._account(n, "taxjson.toml"), None)
        for f in paths:
            pl._place(grp, gname, None, pl._file(f, "taxjson.toml"))
    for a in items:
        if "=" in a and not Path(a).expanduser().is_file():
            lhs, rhs = a.split("=", 1)
            names = [x for x in lhs.split("+") if x.strip()]
            paths = [x for x in rhs.split("+") if x.strip()]
            if not names or not paths:
                _die_input(f"{a!r}: paired form is "
                           f"ACCOUNT[+ACCOUNT...]=FILE[+FILE...]")
            key = tuple(sorted(dict.fromkeys(
                pl._account(n.strip(), a) for n in names)))
            gname = "+".join(key)
            grp = groups.setdefault(
                key, {"accounts": [], "files": [], "paired": True})
            for n in key:
                pl._place(grp, gname, n, None)
            for f in paths:
                pl._place(grp, gname, None, pl._file(f.strip(), a))
            continue
        if a in tax or a in unreadable or a in pl._declared():
            # An account whose books cannot be read, or that has none
            # (TJ-0639), is refused as such (_account), never as
            # "neither an account nor a file".
            pl._place(bare, "the aggregate group", pl._account(a, a), None)
            continue
        p2 = pl._resolved(a)
        if p2 is not None:
            pl._place(bare, "the aggregate group", None, p2)
            continue
        _die_input(f"{_mask_ids_in_path(a)!r} is neither "
                   f"an account "
                   f"(have: {', '.join(sorted(tax))}) nor an existing "
                   f".toml file")
    if bare["accounts"] or bare["files"]:
        if not (bare["accounts"] and bare["files"]):
            _die_input("the aggregate form needs at least "
                       "one ACCOUNT and one FILE.toml (free mix, e.g. "
                       "`tjs sanity margin rrsp margin.toml "
                       "rrsp.toml`), or pair them: "
                       "`margin=margin.toml+margin-2.toml`")
    ordered: List[Dict[str, Any]] = list(groups.values())
    if bare["accounts"]:
        ordered.append(bare)
    if not ordered:
        _die_input("nothing to check")
    pl._warn_left_out()
    return ordered


def _split_by_date(fl: "_Files", grp: Dict[str, Any], root: Path
                   ) -> Tuple[Dict[Path, Any], Dict[Path, str], List[List[Path]]]:
    """One group's files read, dated and cut into the units compared:
    (each file's book, each file's day, the units). Several snapshots of
    one broker account are compared one date at a time; files dated
    differently that do not each name their broker account refused."""
    from taxjson.lib import positions_check as PC
    # Each file is read once. A group's files are summed — one
    # taxjson account across several broker accounts — unless they
    # are several snapshots of the SAME broker account(s) at
    # different dates: two observations of one position are never
    # a sum (a January 10 and a February 20 compared as 30 against
    # the current 20; audit 2026-10-24 B17). Those are compared one
    # date at a time, each against the books on its day.
    for _p in grp["files"]:
        fl._accounts_of[_p] = list(grp["accounts"])
    parsed = {_p: fl._ext_book([_p]) for _p in grp["files"]}
    days = {_p: fl._file_day(_p) for _p in grp["files"]}
    brokers = {_p: fl._file_broker(_p, grp["accounts"])
               for _p in grp["files"]}
    _by_broker: Dict[str, Set[str]] = {}
    for _p in grp["files"]:
        if brokers[_p]:
            _by_broker.setdefault(brokers[_p], set()).add(days[_p])
    _repeated = sorted(b for b, ds in _by_broker.items() if len(ds) > 1)
    # A dated file beside an undated one: the undated one is read as
    # the latest books', so the two are of different days once the
    # books moved after the dated one — 10 on June 30 plus 5 now was
    # compared with the 15 of today and certified clean, without a
    # word (backlog TJ-0257).
    _dset = set(days.values())
    _end_g = (PC.last_day([r for a in grp["accounts"]
                           for r in fl._rows_of(a)])
              if "" in _dset and len(_dset) > 1 else "")
    _undated_apart = bool(_end_g) and any(d and d < _end_g
                                          for d in _dset)
    units: List[List[Path]] = [list(grp["files"])]
    if _repeated:
        _udays = sorted({days[_p] for _p in grp["files"]})
        _at = {d: {brokers[_p] for _p in grp["files"] if days[_p] == d}
               for d in _udays}
        _named = ", ".join(_mask_ids_in_path(_p.name)
                           for _p in grp["files"]
                           if brokers[_p] in _repeated)
        if ("" in _udays or any(s != set(_by_broker) for s in _at.values())):
            # Snapshots of one broker account at several dates
            # beside files that are not at each of those dates:
            # neither a sum nor a per-date compare is a complete
            # observation. A date with every broker account's
            # snapshot is one, checked in a run of its own: a
            # repeated ACCOUNT= merges into one group, so the
            # advice to pair each date in a group of its own could
            # not be followed (backlog TJ-0065).
            _acct = "+".join(grp["accounts"])
            _whole = [d for d in _udays
                      if d and _at[d] == set(_by_broker)]
            _one = ""
            if _whole:

                def _rel(_p: Path) -> str:
                    try:
                        return str(_p.relative_to(root))
                    except ValueError:
                        return str(_p)
                _one = (f" Or compare one date's files in a run of "
                        f"their own: `tjs sanity {_acct}="
                        + "+".join(_mask_ids_in_path(_rel(_p))
                                   for _p in grp["files"]
                                   if days[_p] == _whole[-1])
                        + f"` ({_whole[-1]}): a repeated `{_acct}=` "
                        f"merges into one group, so each date needs "
                        f"a run of its own.")
            _die(f"{_acct}: {_named} are snapshots "
                 f"of one broker account at different dates "
                 f"({', '.join(d or 'undated' for d in _udays)}), "
                 f"beside other files of the group",
                 "Their sum is no snapshot of one day, and the other "
                 "files are not at each of those dates. Keep one "
                 "snapshot per broker account (the year-end ones)."
                 + _one)
        units = [[_p for _p in grp["files"] if days[_p] == d]
                 for d in _udays]
        _sanity_note(f"{'+'.join(grp['accounts'])}: {len(units)} "
                     f"snapshots of the same broker account(s) "
                     f"({', '.join(_udays)}): each is compared with "
                     f"the books on its own day, never summed.")
    elif ((len({d for d in days.values() if d}) > 1 or _undated_apart)
          and any(not brokers[_p] for _p in grp["files"])):
        # Files of one group at different dates, not every one
        # naming its broker account: one broker account at two
        # dates (compared one date at a time) cannot be told from
        # two broker accounts of one taxjson account (summed), and
        # either guess gives a false verdict for the other — a sum
        # of two dates said MISSING_IN_TAXJSON for a correct pair
        # (audit 2026-10-25 first-project#2). Refused, naming the
        # files and the key that tells them apart.
        _unnamed = [_p for _p in grp["files"] if not brokers[_p]]
        _die_input(
            f"{'+'.join(grp['accounts'])}: "
            + ", ".join(f"{_mask_ids_in_path(_p.name)} "
                        f"({days[_p] or 'undated'})"
                        for _p in grp["files"])
            + f" are dated differently and "
            f"{'none' if len(_unnamed) == len(grp['files']) else 'not every one'}"
            f" names its broker account",
            "One broker account's snapshots at several dates are "
            "compared one date at a time; different broker accounts' "
            "are summed. Without each file's broker account the two "
            "cannot be told apart, and a sum of two dates is no "
            "snapshot."
            + (f" An undated file is read as of the books' last day "
               f"({_end_g})." if _undated_apart else ""),
            "Add `broker_account = \"...\"` (the broker's account "
            "number) to the [meta] of "
            + ", ".join(_mask_ids_in_path(_p.name) for _p in _unnamed)
            + "; or keep one snapshot per broker account (the year-end "
            "one); or check one file at a time: `tjs sanity "
            "ACCOUNT=FILE`.")
    return parsed, days, units


def _compare_unit(fl: "_Files", grp: Dict[str, Any], units: List[List[Path]],
                  _files: List[Path], parsed: Dict[Path, Any], days: Dict[Path, str],
                  tax: Dict[str, Dict[str, float]], cache: Path, tol: float,
                  _clock: bool) -> Dict[str, Any]:
    """One unit of a group compared: the group (or, for one date of
    several, its copy with that date's files) with its date, the options
    matched by their underlying, the fractions awaiting their cash, and
    `rows`, the differences."""
    from taxjson.lib import positions_check as PC
    sub = grp if len(units) == 1 else dict(grp, files=_files)
    ext_book: Dict[str, float] = {}
    file_labels: List[str] = []
    alt: Dict[str, str] = {}
    for _p in _files:
        _b, _l, _a = parsed[_p]
        for sym, q in _b.items():
            ext_book[sym] = ext_book.get(sym, 0.0) + q
        file_labels.extend(_l)
        alt.update(_a)
    tax_book: Dict[str, float] = {}
    for acct in sub["accounts"]:
        for sym, q in tax[acct].items():
            tax_book[sym] = tax_book.get(sym, 0.0) + q
    # A broker report dated before the books' last row is
    # compared with the books' positions ON its date (rebuilt
    # from the account's rows); a holdings TOML only with an
    # explicit [meta] as_of. One date for the group, else the
    # latest books.
    sub["as_of"] = ""
    _dates = {days[_p] for _p in _files}
    _grows = [r for a in sub["accounts"] for r in fl._rows_of(a)]
    _books_end = PC.last_day(_grows)
    _one_day = (next(iter(_dates)) if len(_dates) == 1
                and "" not in _dates else "")
    # (the files' one date, also when it is after the group's
    # own books end: the pooled cost may have moved since,
    # backlog TJ-0817)
    sub["file_day"] = _one_day
    if _one_day and _books_end and _one_day < _books_end:
        sub["as_of"] = _one_day
        tax_book = PC.positions_on(_grows, sub["as_of"],
                                   clock=_clock)
    elif (_one_day and _books_end and _one_day > _books_end
          and any(_p.parent in fl._snap_dirs for _p in _files)):
        _sanity_note(f"{'+'.join(sub['accounts'])}: the snapshot "
                     f"is dated {_one_day}, after the books' "
                     f"last day {_books_end}: compared with the "
                     f"latest books.")
    elif _one_day and not _grows and not any(
            (cache / f"{a}_base.json").exists()
            for a in sub["accounts"]):
        # No rows to rebuild the day's positions from: said,
        # never a silent compare with the latest books.
        _sanity_note(f"{'+'.join(sub['accounts'])}: the file is "
                     f"dated {_one_day}, and the books' rows "
                     f"(work/<account>_base.json) are missing: "
                     f"compared with the latest books — "
                     f"`tjs run` writes them.")
    _ds = sorted(d for d in _dates if d)
    # (an undated file beside a dated one is of the books' last
    # day: two dates too, backlog TJ-0257)
    if ((len(_ds) > 1 or (_ds and "" in _dates)) and _books_end
            and _ds[0] < _books_end):
        # Different broker accounts' snapshots at different
        # dates are summed (one taxjson account across brokers)
        # and compared with the latest books: the books cannot
        # say which broker account a later trade was in. Said
        # when the books moved after the earliest date, so a
        # difference there is read as one (backlog TJ-0066).
        _early = PC.positions_on(_grows, _ds[0], clock=_clock)
        _late = PC.positions_on(_grows, _books_end, clock=_clock)
        _moved = sorted(s for s in set(_early) | set(_late)
                        if abs(_early.get(s, 0.0)
                               - _late.get(s, 0.0)) > tol)
        if _moved:
            # (the point before the first clause break: the
            # default view's one line, TJ-0160)
            _sanity_note(
                f"{'+'.join(sub['accounts'])}: "
                f"{', '.join(_moved[:3])}"
                + (f" +{len(_moved) - 3} more" if len(_moved) > 3
                   else "")
                + f" changed after {_ds[0]}, the date of "
                + ", ".join(_mask_ids_in_path(_p.name)
                            for _p in _files if days[_p] == _ds[0])
                + ": compared with the latest books — "
                "snapshots of different broker accounts at "
                "different dates ("
                + ", ".join(f"{_mask_ids_in_path(_p.name)} "
                            f"{days[_p] or 'undated'}"
                            for _p in _files)
                + ") are summed (the books cannot tell which "
                "broker account a trade was in), so a difference "
                "there may be that change; keep each broker "
                "account's snapshot of one date (the year end).")
    via_underlying: List[Tuple[str, str]] = []
    for sym, via in alt.items():
        if sym in ext_book and sym not in tax_book \
                and via in tax_book:
            ext_book[via] = ext_book.get(via, 0.0) + ext_book.pop(sym)
            via_underlying.append((sym, via))
    sub["via_underlying"] = via_underlying
    sub["file_spelling"] = {a: fl.spelled[a] for a, _b in via_underlying if a in fl.spelled}
    # An s.86.1 spin-off's fraction the books hold on the
    # snapshot's date until its cash in lieu is paid after it:
    # the broker shows the whole shares only (backlog TJ-0149).
    _await = (PC.fractions_awaiting_cash(_grows, sub["as_of"])
              if sub["as_of"] else {})
    sub["awaiting_cash"] = []
    rows: List[Dict[str, Any]] = []
    for sym in sorted(set(tax_book) | set(ext_book)):
        tq = tax_book.get(sym, 0.0)
        eq = ext_book.get(sym, 0.0)
        if abs(tq - eq) <= tol:
            continue
        _fr = _await.get(sym) or []
        _pend = sum(f["quantity"] for f in _fr)
        if _fr and abs(tq - _pend - eq) <= tol:
            sub["awaiting_cash"].append(
                {"symbol": sym, "taxjson_qty": tq,
                 "holdings_qty": eq, "fractions": _fr})
            continue
        issue = ("MISSING_IN_TAXJSON" if abs(tq) <= tol
                 else "MISSING_IN_HOLDINGS" if abs(eq) <= tol
                 else "QTY_MISMATCH")
        rows.append({"symbol": sym, "issue": issue,
                     "taxjson_qty": tq, "holdings_qty": eq,
                     "accounts": sorted(sub["accounts"])})
    sub["rows"] = rows
    sub["labels"] = file_labels
    sub["positions"] = len(tax_book)
    return sub


def _ext_costs(fl: "_Files", grp: Dict[str, Any], _shel: List[str], _mapped: Any,
               _native_mapped: Any) -> Dict[str, Dict[str, Any]]:
    """One group's costs as its broker reports state them, by symbol in
    the books' spelling (none for a group with a sheltered account)."""
    ext_cost: Dict[str, Dict[str, Any]] = {}
    # An option the quantity check matched by its underlying (the
    # file's root spelling of the books' contract): its cost is the
    # books' contract's too (backlog TJ-0813).
    _via = dict(grp.get("via_underlying") or ())
    for _p in (grp["files"] if not _shel else ()):
        _rp = fl.reports_of.get(_p)
        if _rp is None:
            continue
        for r in _rp.rows:
            if r.asset_type == "cash" or abs(r.quantity) <= 1e-12:
                continue
            _coin = fl._cash_coin(r.asset_type, r.symbol)
            if _coin and _coin in (fl.cash_coins.get(_p) or {}):
                continue        # left out as cash (`_skip_cash_coin`): no cost
            k = _mapped(r.symbol)
            _nk = _native_mapped(r.symbol)
            if k in _via:
                k = _nk = _via[k]
            e = ext_cost.setdefault(k, {
                "cost": 0.0, "currencies": set(), "kinds": set(),
                "missing": False, "pos_cur": r.currency or "",
                "native": _nk, "qty": 0.0})
            e["qty"] += r.quantity
            if r.cost is None or not r.cost_kind:
                e["missing"] = True
                continue
            e["cost"] += float(r.cost)
            e["currencies"].add((r.cost_currency or r.currency
                                 or "").upper())
            e["kinds"].add(r.cost_kind)
    return ext_cost


def _cost_rows(fl: "_Files", grp: Dict[str, Any], ext_cost: Dict[str, Dict[str, Any]],
               qty_bad: Set[str], cost_all: List[Dict[str, Any]], *, tol: float,
               cost_tol: float, _ctry: Any, _taxable: Set[str], cache: Path, _mapped: Any,
               _base_cur: str, _own_inv: Dict[str, Dict[str, Dict[str, Any]]],
               _fil_inv: Dict[str, Dict[str, Dict[str, Any]]],
               _nat_inv: Dict[str, Dict[str, Dict[str, Any]]]) -> None:
    """One group's costs compared with the books' (lib/positions_check),
    a row each into `cost_all`: n/a when the report states none, or the
    books (or the pool) moved after its date."""
    from taxjson.lib import positions_check as PC
    from taxjson.lib.country import CANADA as _CANADA
    _grows = [r for a in grp["accounts"] for r in fl._rows_of(a)]
    _after = (PC.touched_after(_grows, grp["as_of"])
              if grp["as_of"] else set())
    # Canada: the filing cost is the s.47 average over every taxable
    # account, read from the LATEST books — another account's trade
    # after the report's date moved it, so it is not that day's
    # cost, even when this account's own books end before that date
    # (backlog TJ-0817). {symbol: the first such account}.
    _pool_after: Dict[str, str] = {}
    _fday = grp.get("as_of") or grp.get("file_day") or ""
    if _fday and _ctry == _CANADA:
        for _oa in sorted(_taxable - set(grp["accounts"])):
            for _s in PC.touched_after(PC.book_rows(cache, _oa),
                                       _fday):
                # (in the books' spelling after ticker.map, as the
                # report's symbols are compared)
                _pool_after.setdefault(_mapped(_s), _oa)
    _roc = PC.roc_symbols(_grows)
    _spin = PC.spinoff_symbols(_grows)
    crow_list: List[Dict[str, Any]] = []
    for sym in sorted(ext_cost):
        e = ext_cost[sym]
        if sym in qty_bad or abs(e["qty"]) <= tol:
            continue
        base_row = {"symbol": sym, "accounts": sorted(grp["accounts"]),
                    "quantity": e["qty"]}
        if e["missing"] or len(e["currencies"]) != 1:
            cost_all.append(dict(base_row, status="n/a",
                                 note=("the report states no cost"
                                       if e["missing"] else
                                       "costs in several currencies")))
            continue
        if sym in _after:
            cost_all.append(dict(base_row, status="n/a",
                                 note=f"the books changed after the "
                                      f"report's date {grp['as_of']}"))
            continue
        if sym in _pool_after:
            cost_all.append(dict(base_row, status="n/a",
                                 note=f"the pooled ACB changed after "
                                      f"the report's date "
                                      f"{_fday} (account "
                                      f"{_pool_after[sym]} traded it)"))
            continue

        def _sum(inv, key):
            hit = [inv[a][key] for a in grp["accounts"]
                   if key in inv.get(a, {})]
            if not hit:
                return None
            return {"total_cost": sum(h["total_cost"] for h in hit),
                    "deferred_wash": sum(h["deferred_wash"]
                                         for h in hit),
                    "currency": hit[0].get("currency") or "",
                    "qty": sum(h["qty"] for h in hit),
                    "unknown_cost": any(h.get("unknown_cost")
                                        for h in hit)}
        _own = _sum(_own_inv, sym)
        _pooled = any(sym in _fil_inv.get(a, {})
                      for a in _taxable - set(grp["accounts"]))
        res = PC.compare_cost(
            symbol=sym, broker_cost=e["cost"],
            cost_currency=next(iter(e["currencies"])),
            cost_kind=sorted(e["kinds"])[0],
            position_currency=e["pos_cur"], base=_base_cur,
            country=_ctry, filing=_sum(_fil_inv, sym),
            own=(_own or {}).get("total_cost"),
            native=_sum(_nat_inv, e["native"]),
            pooled=_pooled, roc=sym in _roc, tol_abs=cost_tol,
            tol_rel=0.001, short=e["qty"] < 0,
            spinoff=sym in _spin)
        cost_all.append(dict(base_row, **res))


def _cost_notes(fl: "_Files", cost_skipped: List[str]) -> None:
    """The notes on the costs not compared: the registered accounts', and
    each file's whose cost fields the reader refused."""
    if cost_skipped:
        _sanity_note(f"cost not compared for registered accounts "
                     f"({', '.join(sorted(set(cost_skipped)))}): book cost "
                     f"there is not tax cost (quantities are compared).")
    for _p, _err in fl._unread_cost.items():
        # A malformed cost field: the quantities are still compared,
        # at the file's own date; the cost is not (never silently). The
        # point and its reason before the first clause break: the
        # default view's one line is the part before it, which was the
        # file and row alone (backlog TJ-0160).
        _where, _brk, _why = _err.rpartition("): ")
        _sanity_note(f"{_where}) cost is not compared: {_why} — its "
                     f"quantities are." if _brk else
                     f"{_err} — its cost is not compared (quantities "
                     f"are).")


def _sanity_books(args: argparse.Namespace) -> Tuple[Path, Path, float, float, Dict[str, Path],
                                                     str, Dict[str, Dict[str, float]],
                                                     Dict[str, str]]:
    """`tjs sanity`'s project, its tolerances and the books' positions
    (cmd_sanity): exit 2 with no gains files."""
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    tol = float(1e-4 if getattr(args, "tolerance", None) is None
                    else args.tolerance)
    cost_tol = float(1.0 if getattr(args, "cost_tolerance", None) is None
                     else args.cost_tolerance)

    # ---- taxjson side ---------------------------------------------------
    resolved = resolve_gains_files(cache)
    if not resolved:
        # Books that are missing or cannot be read are an input this
        # check cannot read: exit 2, as docs/commands.md's exit-code
        # table says; 1 stays the verdict "positions differ" (backlog
        # TJ-0353).
        # Named from the project (docs/output-style.md; TJ-0631).
        _die_input("no gains files in work/ — run `tjs run` first.")
    basis = gains_basis_label(resolved)
    tax, unreadable = _read_books(resolved)
    return root, cache, tol, cost_tol, resolved, basis, tax, unreadable


def _sanity_nothing_to_compare(pl: "_Placement", root: Path, config_notes: List[str],
                               config_kinds: Dict[str, List[str]],
                               tax: Dict[str, Dict[str, float]], _accts_cfg: Dict[str, Any],
                               cache: Path, tol: float, args: argparse.Namespace, basis: str,
                               cost_tol: float) -> None:
    """No arguments and no account's `holdings` to compare (cmd_sanity):
    the accounts left unchecked, where to put a positions file, and the
    dividends paid on more shares than the books held; the refusal when a
    note says a `holdings` path could not be read."""
    from taxjson.lib import positions_check as PC
    _unread = _no_groups_refusal(pl, root, config_notes, config_kinds)
    # Nothing to compare with is not an error: say which
    # accounts went unchecked, as when some have a file (the
    # checklist reads the UNCHECKED line; 2026-10 workflow
    # audit: docs/commands.md promised this, the command died).
    # Every account with open positions, a registered one too
    # (as the compare and the run count them); --json is a
    # document like the compare's (backlog TJ-0135).
    _unchk = sorted(a for a in tax if tax[a] and a in _accts_cfg)
    # The dividends paid on more shares than the books held: a
    # check of the books alone, run with no snapshot too (it was
    # skipped here: backlog TJ-0688). Informational, as below.
    _inc: List[Dict[str, Any]] = []
    for a in sorted(tax):
        if a in _accts_cfg:
            _inc.extend(PC.income_share_mismatches(
                PC.book_rows(cache, a), a, tol,
                clock=_is_us(load_config(root, missing_ok=True))))
    if getattr(args, "json", False):
        _json_out(_nothing_to_compare_json(basis=basis, cost_tol=cost_tol, _inc=_inc,
                                           _unchk=_unchk, _unread=_unread), schema="sanity")
        return
    # The text: a live exact Doc, each line printed when it is added,
    # as the print() it replaces printed it.
    d = _out.Doc(exact=True, width_=0, live=True)
    if _details(args) and _unchk:
        for a in _unchk:
            d.line(f"  - account {a} not included "
                   f"({len(tax[a])} position(s) unchecked)")
        d.blank()
    if _unchk:
        d.line(f"UNCHECKED: {len(_unchk)} account(s) with positions "
               f"and no holdings file: " + ", ".join(_unchk[:3])
               + (f" +{len(_unchk) - 3} more" if len(_unchk) > 3
                  else "")
               + " — nothing to compare with")
    else:
        d.line("No account holds a position in the books, and no "
               "holdings file — nothing to compare with.")
    d.blank()
    _hold = _PL.holdings_folder(root)
    # README.txt only where it is: `init --single` makes no
    # holdings/ folder (TJ-0135).
    _how = ("; its README.txt says how"
            if (_hold / "README.txt").is_file() else
            ", named NAME_holdings.toml; the format: "
            "docs/settings.md, Holdings TOML")
    for _ln in _out_wrap(
            f"Put the broker's positions .toml in "
            f"{_PL.shown(_hold, root)}/ (one per account{_how}; "
            f"its RBC Holdings Export or IB statement, named "
            f"NAME_holdings.csv, is read too), "
            f"or pass one: `tjs sanity margin=positions.toml` "
            f"(docs/commands.md, sanity)."):
        d.line(_ln)
    _sanity_print_extras([], [], [], 0, 0, _inc, True,
                         concise=not _details(args))
    return


def _sanity_country(root: Path) -> Tuple[Dict[str, Any], Any, Any, bool]:
    """The project's account types, settings and country, and whether its
    engine is the US one (cmd_sanity)."""
    # The project's country and account types (read once): the costs
    # need them, and the books' positions on a past day are replayed in
    # the engine's order (positions_on: the US engine takes a split by
    # its clock time). A config the quantity check never needed must not
    # stop it. With no country the costs are not compared (never a
    # silent Canada).
    try:
        _pcfg = (load_config(root) if _PL.has_config(root)
                 else {})
    except SystemExit:
        _pcfg = {}
    _cfg_accts = accounts_of(_pcfg)
    _settings = settings_of(_pcfg)
    try:
        _ctry = _country(_settings) if not _settings.empty else None
    except SystemExit:
        _ctry = None
    from taxjson.lib.country import is_usa as _is_usa
    _clock = bool(_ctry) and _is_usa(_ctry)
    return _cfg_accts, _settings, _ctry, _clock


def _sanity_costs(_settings: Any, _ctry: Any, _cfg_accts: Dict[str, Any],
                  resolved: Dict[str, Path], cache: Path, ordered: List[Dict[str, Any]],
                  fl: "_Files", _mapped: Any, _native_mapped: Any, tol: float, cost_tol: float,
                  _clock: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str],
                                         List[Dict[str, Any]], int, int]:
    """The books' costs against the reports' and the dividends paid on more
    shares than the books held, per group (cmd_sanity): never a failing
    exit."""
    from taxjson.lib import positions_check as PC
    # ---- costs (never a failing exit; lib/positions_check) ----------------
    _base_cur = str(_settings.base_currency or "").upper() or (
        _home_currency(_settings) if _ctry else "")
    _taxable = {a for a, c in _cfg_accts.items() if c.taxable}
    _own_inv: Dict[str, Dict[str, Dict[str, Any]]] = {}
    _nat_inv: Dict[str, Dict[str, Dict[str, Any]]] = {}
    _fil_inv: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for acct, f in resolved.items():
        _fil_inv[acct] = PC.load_inventory(f)
        _own_inv[acct] = PC.load_inventory(cache / f"{acct}_gains.json")
        _nat_inv[acct] = PC.load_inventory(cache / f"{acct}_raw_gains.json")
    cost_all: List[Dict[str, Any]] = []
    income_all: List[Dict[str, Any]] = []
    # Cost is compared for taxable accounts only: a registered account's
    # broker book cost is not a tax cost (an in-kind transfer in resets
    # it to the market value; none is reported) — Canada's RRSP/TFSA...
    # and the US IRA/401(k)/HSA alike (type = "sheltered"). Its
    # quantities are compared as every account's are.
    cost_skipped: List[str] = []
    for grp in ordered if _ctry else ():
        qty_bad = {r["symbol"] for r in grp["rows"]}
        _shel = sorted(a for a in grp["accounts"]
                       if account_of(_cfg_accts, a).sheltered)
        if _shel:
            cost_skipped.extend(_shel)
        ext_cost = _ext_costs(fl, grp, _shel, _mapped, _native_mapped)
        _cost_rows(fl, grp, ext_cost, qty_bad, cost_all, tol=tol, cost_tol=cost_tol,
                   _ctry=_ctry, _taxable=_taxable, cache=cache, _mapped=_mapped,
                   _base_cur=_base_cur, _own_inv=_own_inv, _fil_inv=_fil_inv,
                   _nat_inv=_nat_inv)
        for a in grp["accounts"]:
            income_all.extend(PC.income_share_mismatches(fl._rows_of(a), a,
                                                         tol, clock=_clock))
    _cost_notes(fl, cost_skipped)
    cost_diffs = [c for c in cost_all if c.get("status") == "differs"]
    cost_matched = sum(1 for c in cost_all if c.get("status") == "match")
    cost_na = sum(1 for c in cost_all if c.get("status") == "n/a")
    return cost_all, income_all, cost_skipped, cost_diffs, cost_matched, cost_na


def _sanity_groups_text(d: _out.Doc,
                        ordered: List[Dict[str, Any]], basis: str, multi: bool, _det: bool,
                        root: Path) -> None:
    """`tjs sanity`'s title and each group's accounts, date, files, option
    matches and fractions awaiting their cash (cmd_sanity)."""
    from taxjson.lib.out import kv_lines as _kv, wrap as _wrap
    _what = ("external holdings TOML"
             if all(p2.suffix.lower() == ".toml"
                    for g in ordered for p2 in g["files"])
             else "the broker's positions")
    for ln in _wrap(f"SANITY — taxjson positions (basis: {basis}) vs "
                    f"{_what} ({'paired' if multi else 'loose aggregate'} "
                    f"check)", None, "", "  "):
        d.line(ln)
    for grp in ordered:
        d.blank()
        tag = ("paired" if grp["paired"] else "aggregate") if multi else ""
        pairs = [("accounts", f"{', '.join(sorted(grp['accounts']))}  "
                              f"({grp['positions']} combined position(s))"
                              + (f"  [{tag}]" if tag else ""))]
        if grp["as_of"]:
            pairs.append(("as of", f"{grp['as_of']} — the report's date; "
                                   f"the books' positions on that day "
                                   f"(rebuilt from "
                                   f"work/<account>_base.json)" if _det
                          else f"{grp['as_of']} (the report's date)"))
        # One line per file, PATH first: the label alone (the file's
        # meta.account, else its stem) didn't say which export was
        # actually read — a stale or wrong path is the first thing to
        # rule out when a group disagrees.
        for p2, lbl in zip(grp["files"], grp["labels"]):
            shown = str(p2)
            try:
                # Inside the project: relative to it (lib/out.relpath).
                shown = str(p2.resolve().relative_to(root))
            except (ValueError, OSError):
                try:
                    shown = "~/" + str(p2.relative_to(Path.home()))
                except ValueError:
                    pass
            # A holdings file named after the broker account put the
            # real account number on the console (R1-351): the file
            # name's ids are masked like the IB warning masks them.
            shown = _mask_ids_in_path(shown)
            extra = (f"  (account {_mask_ids_in_path(lbl)})"
                     if lbl not in p2.stem else "")
            pairs.append(("file", f"{shown}{extra}"))
        for a, b in grp["via_underlying"]:
            pairs.append(("option", f"{grp.get('file_spelling', {}).get(a, a)} matched taxjson's {b} via its "
                                    f"underlying (same contract, "
                                    f"different option root)"))
        for _a in grp.get("awaiting_cash") or ():
            _f = _a["fractions"][0]
            _more = _a["taxjson_qty"] - _a["holdings_qty"]
            _what = ("a merger's fraction (received"
                     if _f.get("event") == "merger"
                     else "a spin-off's fraction (s.86.1, received")
            pairs.append(("fraction", f"{_a['symbol']}: {_more:g} more in "
                                      f"the books, {_what} {_f['received']}) "
                                      f"sold for its cash in lieu on "
                                      f"{_f['paid']}, after this date — "
                                      f"not a difference"))
        if multi:
            pairs.append(("result", f"{len(grp['rows'])} discrepancy(ies)"
                                    if grp["rows"] else "OK"))
        for ln in _kv(pairs, "  "):
            d.line(ln)


def _sanity_unchecked_text(d: _out.Doc,
                           uncovered: List[str], _det: bool, tax: Dict[str, Dict[str, float]],
                           items: List[str], open_notes: List[str],
                           config_kinds: Dict[str, List[str]], cfg_unchecked: List[str],
                           root: Path) -> None:
    """The accounts not compared, the UNCHECKED line the checklist reads,
    and the INCOMPLETE line for notes that leave something unchecked
    (cmd_sanity)."""
    if uncovered and _det:
        d.blank()
        for a in uncovered:
            d.line(f"  - account {a} not included "
                   f"({len(tax[a])} position(s) unchecked)")
    elif uncovered and items:
        # (with no items the UNCHECKED line below names them)
        d.blank()
        _unc = [f"{a} ({len(tax[a])})" for a in uncovered]
        d.line("Not compared (no holdings file given): "
               + ", ".join(_unc[:4])
               + (f" +{len(_unc) - 4} more" if len(_unc) > 4 else ""))
    d.blank()
    if uncovered and not items:
        # The checklist's sanity step reads this line: "done" while
        # whole accounts were never tied was a false certificate
        # (S044-19).
        _named = list(uncovered)
        d.line(f"UNCHECKED: {len(uncovered)} account(s) with positions and "
               f"no holdings file: " + ", ".join(_named[:3])
               + (f" +{len(_named) - 3} more" if len(_named) > 3 else "")
               + ("" if _det else " — tjs sanity --details"))
    if open_notes:
        # A configured holdings file that could not be read drops its
        # whole account from the compare: never let that read as a
        # clean check (the checklist's step keys on this line — 2026-09
        # audit R1-324). It counts what the notes say
        # (a holdings/ snapshot is not a path in taxjson.toml: backlog
        # TJ-0020).
        _nunread = len(config_kinds.get("unread") or ())
        _nsnap = len(open_notes) - len(cfg_unchecked) - _nunread
        _hshown = _PL.shown(_PL.holdings_folder(root), root)
        d.line("INCOMPLETE: " + "; ".join(
            ([f"{len(cfg_unchecked)} account(s) with `holdings` in "
              f"taxjson.toml not checked"] if cfg_unchecked else [])
            + ([f"{_nsnap} snapshot(s) in {_hshown}/ not compared"]
               if _nsnap > 0 else [])
            + ([f"{_nunread} file(s) in {_hshown}/ not read"]
               if _nunread else []))
               + " (the notes on stderr): fix "
               + ("them" if _nsnap > 0 or _nunread else "the paths")
               + ", then re-run")


def _sanity_verdict_text(d: _out.Doc,
                         all_rows: List[Dict[str, Any]], open_notes: List[str], multi: bool,
                         uncovered: List[str], items: List[str], _det: bool, root: Path,
                         distinct: Any) -> None:
    """The OK line, or the differences' legend and table with a `! ` line
    per kind (with --details, their count and the missing-history note)
    (cmd_sanity)."""
    from taxjson.lib.out import wrap as _wrap
    from taxjson.lib.out import act as _act
    if not all_rows:
        d.line("OK: tickers and quantities agree"
               + (" in every checked group." if open_notes
                  else " in every group." if multi else "."))
    else:
        if (uncovered and not items) or open_notes:
            d.blank()
        # The legend, before the table (Essentials first).
        d.line("ISSUE: MISSING_IN_HOLDINGS = not in the broker's file; "
               "MISSING_IN_TAXJSON = not in the books.")
        out_lines = [("ACCOUNTS SYMBOL ISSUE TAXJSON HOLDINGS DIFF"
                      if multi else
                      "SYMBOL ISSUE TAXJSON HOLDINGS DIFF")]
        for r in all_rows:
            # Both quantities at one precision (0.7552 beside 0.75
            # read as two scales).
            _dec = max(len((f"{v:g}".split(".") + [""])[1])
                       for v in (r["taxjson_qty"], r["holdings_qty"]))
            _dec = min(_dec, 8)
            cells = [r["symbol"], r["issue"],
                     f"{r['taxjson_qty']:.{_dec}f}",
                     f"{r['holdings_qty']:.{_dec}f}",
                     f"{r['taxjson_qty'] - r['holdings_qty']:+g}"]
            if multi:
                cells.insert(0, "+".join(r["accounts"]))
            out_lines.append(" ".join(cells))
        _print_report_table(out_lines, fit=True, key=(0, 1) if multi
                            else 0)
        _fewer = any(r["holdings_qty"] - r["taxjson_qty"] > 0
                     for r in all_rows)
        if not _det:
            d.blank()
            # One `! ` line per kind of difference, each naming the fix
            # that works for it: find-missing-history sees a position
            # the books hold short, never one bought before the data or
            # typed under another spelling (backlog TJ-0423).
            for _text, _cmd in _sanity_pointers(root, all_rows,
                                                distinct=distinct):
                d.line(_act(_text, _cmd))
        else:
            d.line(f"\n{len(all_rows)} discrepancy(ies).")
        if _fewer and _det:
            # Fewer shares in the books than at the broker: on a first
            # project that is history the download does not reach
            # (new-user study), not a booking bug.
            for ln in _wrap(
                    "Fewer shares in taxjson than at the broker usually "
                    "means missing history: purchases from before your "
                    "download starts, or shares transferred in. See "
                    "`tjs find-missing-history`, `tjs transfers` "
                    "and docs/getting-started.md step 5. A trade after "
                    "your last export is the other usual cause."):
                d.line(ln)


def cmd_sanity(args: argparse.Namespace) -> None:
    """`tjs sanity ITEM... [--tolerance N] [--json]`: LOOSE
    cross-check of open positions against externally produced holdings
    TOML files (a [[holding]] array of symbol/quantity tables).

    Two argument forms, freely mixed:

    * bare `ACCOUNT` / `FILE.toml` items — the AGGREGATE form: every
      bare account's positions are SUMMED into one book and compared
      against the SUM of every bare file. (Pairing was cumbersome for
      a quick check, and the pathological case — every total matching
      while positions sit in the wrong accounts — is rare.)
    * `ACCOUNT[+ACCOUNT...]=FILE[+FILE...]` — a PAIRED group: only
      those accounts against only those files. Many-to-many because a
      taxjson account can span several broker accounts (margin =
      IBKR + Webull → two exports) and one broker export can cover
      several taxjson accounts. Repeating a left-hand side
      (`margin=ibkr.toml margin=webull.toml`) merges into one group.

    Each group is compared independently; an account or file may sit
    in only one group. taxjson side: the canonical gains inventory
    (same basis as `tjs list` — wash-adjusted where built, post
    ticker.map). The files' symbols get the same GLOBAL+TOBASE renames,
    with options following their underlying's rename. Exit 1 on any
    discrepancy in any group.

    A FILE may also be a broker's own positions report
    (lib/positions_reports: an IB Activity Statement's Open Positions,
    an RBC Holdings Export). When a report is dated before the books'
    last row, the books' positions ON that day are compared (rebuilt
    from work/<acct>_base.json). After the quantities, the books' COST
    is compared with the report's where it states one, with a reason
    for each difference (lib/positions_check) — never a failing exit —
    and a dividend paid on a share count the books did not hold on its
    record date is listed (informational)."""
    root, cache, tol, cost_tol, resolved, basis, tax, unreadable = _sanity_books(args)

    # ---- classify the positionals into groups ------------------------------
    # A group is (accounts, files); bare items all land in one AGGREGATE
    # group, `A[+A]=F[+F]` items in a PAIRED group keyed by its accounts
    # so a repeated left-hand side merges. Each account/file belongs to
    # at most one group — a second placement is a usage error rather
    # than a silent double count.
    pl = _Placement(root, tax, unreadable)
    placed_accounts = pl.placed_accounts

    items = list(args.items or [])
    config_groups: List[Tuple[List[str], List[str]]] = []
    config_notes: List[str] = []
    # Which notes are which (_sanity_items_from_config): the accounts
    # whose `holdings` setting was not checked, and the notes that leave
    # nothing unchecked.
    config_kinds: Dict[str, List[str]] = {}
    if not items:
        # No arguments: pairings come from taxjson.toml — each
        # account's `holdings = [...]`. Explicit arguments override
        # the config for that run.
        config_groups, config_notes, _accts_cfg = _config_groups(root, resolved, unreadable,
                                                                 config_kinds)
        if not config_groups:
            _sanity_nothing_to_compare(pl, root, config_notes, config_kinds, tax, _accts_cfg,
                                       cache, tol, args, basis, cost_tol)
            return
    ordered = _group_items(pl, config_groups, items)

    # Same consolidation the canonical pipeline applied: GLOBAL+TOBASE
    # renames from the map the books were merged with — the run's
    # effective map (ticker.map plus the listings it joined by their
    # transfer journal, lib/cross_listings) when it wrote one, else
    # ticker.map; options follow their underlying.
    renames, native_renames, distinct, map_symbol = _renames(root)

    def _mapped(sym: str) -> str:
        if renames is not None and map_symbol is not None:
            return map_symbol(sym, renames)
        return sym

    def _native_mapped(sym: str) -> str:
        if native_renames is not None and map_symbol is not None:
            return map_symbol(sym, native_renames)
        return sym

    # ---- compare, per group ------------------------------------------------
    # Positions can NET to zero across accounts (or vs the files' sum);
    # only keep true residues.
    all_rows: List[Dict[str, Any]] = []
    _cfg_accts, _settings, _ctry, _clock = _sanity_country(root)
    fl = _Files(root, cache, _mapped, _ctry)
    fl.tax = tax

    expanded: List[Dict[str, Any]] = []
    for grp in ordered:
        parsed, days, units = _split_by_date(fl, grp, root)
        for _files in units:
            sub = _compare_unit(fl, grp, units, _files, parsed, days, tax, cache, tol, _clock)
            all_rows.extend(sub["rows"])
            expanded.append(sub)
    ordered = expanded
    fl.note_cash_coins(ordered, config_notes, config_kinds)
    (cost_all, income_all, cost_skipped, cost_diffs, cost_matched,
     cost_na) = _sanity_costs(_settings, _ctry, _cfg_accts, resolved, cache, ordered, fl, _mapped,
                              _native_mapped, tol, cost_tol, _clock)

    accounts = sorted(placed_accounts)
    files: List[Path] = [p2 for g in ordered for p2 in g["files"]]
    file_labels = [lbl for g in ordered for lbl in g["labels"]]
    uncovered = sorted(a for a in tax
                       if a not in placed_accounts and tax[a])
    # The notes that leave something unchecked: an account's `holdings`
    # setting that could not be read, a holdings/ snapshot no account
    # (or two) claims. A snapshot of an account whose own setting names
    # its files leaves nothing unchecked: that account is compared with
    # those (backlog TJ-0020).
    _info_notes = set(config_kinds.get("info") or ())
    open_notes = [n for n in config_notes if n not in _info_notes]
    cfg_unchecked = list(config_kinds.get("accounts") or ())

    if getattr(args, "json", False):
        _json_out(_sanity_json_doc(
            basis=basis, accounts=accounts, files=files, file_labels=file_labels,
            ordered=ordered, cost_matched=cost_matched, cost_diffs=cost_diffs,
            cost_na=cost_na, cost_skipped=cost_skipped, cost_tol=cost_tol,
            cost_all=cost_all, income_all=income_all, all_rows=all_rows,
            uncovered=uncovered, cfg_unchecked=cfg_unchecked, config_notes=config_notes,
            open_notes=open_notes, items=items), schema="sanity")
        raise SystemExit(0 if not all_rows else 1)

    # Essentials first (docs/output-style.md): --details adds the
    # explanations, the cost/income tables and the not-compared list.
    _det = _details(args)
    for n in config_notes:
        _sanity_note(n)
    multi = len(ordered) > 1 or any(g["paired"] for g in ordered)
    # The text: a live exact Doc the parts add to, each line printed
    # when it is added, as the print() it replaces printed it.
    d = _out.Doc(exact=True, width_=0, live=True)
    _sanity_groups_text(d, ordered, basis, multi, _det, root)
    _sanity_unchecked_text(d, uncovered, _det, tax, items, open_notes, config_kinds, cfg_unchecked,
                           root)
    _sanity_verdict_text(d, all_rows, open_notes, multi, uncovered, items, _det, root, distinct)
    _sanity_print_extras(ordered, cost_all, cost_diffs, cost_matched,
                         cost_na, income_all, multi,
                         concise=not _det)
    raise SystemExit(0 if not all_rows else 1)


def _sanity_spellings(rows: List[Dict[str, Any]],
                      distinct: Iterable[frozenset] = ()
                      ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(row, row) pairs of sanity differences that are one security
    spelled two ways: a symbol the books lack (MISSING_IN_TAXJSON) and
    one the holdings lack (MISSING_IN_HOLDINGS) of the same accounts,
    the same root before the exchange suffix (ZZB / ZZB.TO) and the same
    quantity — never a pair ticker.map declares DISTINCT (`distinct`:
    frozenset pairs, TickerMap.distinct): two securities, whose
    differences stay missing and extra holdings (backlog TJ-0836)."""
    def root_(s: str) -> str:
        return str(s or "").split(".", 1)[0].upper()
    apart = {frozenset(str(x).upper() for x in pair) for pair in distinct}
    out = []
    used: set = set()
    for a in rows:
        if a["issue"] != "MISSING_IN_TAXJSON":
            continue
        for b in rows:
            if (id(b) in used or b["issue"] != "MISSING_IN_HOLDINGS"
                    or b["accounts"] != a["accounts"]
                    or root_(b["symbol"]) != root_(a["symbol"])
                    or frozenset((str(a["symbol"]).upper(),
                                  str(b["symbol"]).upper())) in apart
                    or abs(b["taxjson_qty"] - a["holdings_qty"])
                    > 1e-6 * max(1.0, abs(a["holdings_qty"]))):
                continue
            used.add(id(b))
            out.append((a, b))
            break
    return out


def _sanity_pointers(root: Path, rows: List[Dict[str, Any]],
                     distinct: Iterable[frozenset] = ()
                     ) -> List[Tuple[str, str]]:
    """The `! ` lines closing `tjs sanity`'s table: (text, command)
    per kind of difference, each naming the fix that works for it
    (docs/getting-started.md, step 5b) — a symbol spelled two ways (the
    books' symbol, `tjs list`), shares transferred in with no cost
    (`tjs transfers`, from reports/run_summary.json), a position the
    books hold short (a sale with no purchase: `tjs find-missing-
    history`), one symbol more in one account and as many fewer in
    another (booked in the wrong account), fewer shares in the books and none short (bought before
    the data: an opening balance, `tjs opening`; find-missing-history
    sees nothing there and said "No missing-cost-basis issues found",
    backlog TJ-0423), more shares in the books (`tjs audit`)."""
    def names(rs: List[Dict[str, Any]]) -> str:
        syms = list(dict.fromkeys(r["symbol"] for r in rs))
        return ", ".join(syms[:2]) + (f" +{len(syms) - 2} more"
                                      if len(syms) > 2 else "")
    spelled = _sanity_spellings(rows, distinct)
    paired = {id(x) for pair in spelled for x in pair}
    no_cost: set = set()
    try:
        from taxjson.lib.first_run import SUMMARY_FILE
        import json as _json
        _doc = _json.loads((root / "reports" / SUMMARY_FILE)
                           .read_text(encoding="utf-8"))
        no_cost = {(str(i.get("symbol")), str(i.get("account")))
                   for i in _doc.get("transfer_in_no_cost") or []}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    # One symbol more in one account's books and as many fewer in
    # another's: booked in the wrong account (an export in the wrong
    # inputs/<account>/ folder), not missing history.
    moved: List[Dict[str, Any]] = []
    for a in rows:
        if id(a) in paired:
            continue
        for b in rows:
            if (b is a or id(b) in paired or b["symbol"] != a["symbol"]
                    or b["accounts"] == a["accounts"]):
                continue
            da = a["taxjson_qty"] - a["holdings_qty"]
            db = b["taxjson_qty"] - b["holdings_qty"]
            if da > 0 > db and abs(da + db) <= 1e-6 * max(1.0, da):
                paired |= {id(a), id(b)}
                moved += [a, b]
                break
    transfer, short, before, more = [], [], [], []
    for r in rows:
        if id(r) in paired:
            continue
        if r["holdings_qty"] - r["taxjson_qty"] <= 0:
            more.append(r)
        elif any((r["symbol"], a) in no_cost for a in r["accounts"]):
            transfer.append(r)
        elif r["taxjson_qty"] < 0:
            short.append(r)
        else:
            before.append(r)
    # (head, the symbols, tail, command): the symbols are left out of a
    # line that would pass the 100 columns of an `! ` line.
    kinds: List[Tuple[str, str, str, str]] = []
    if spelled:
        a, b = spelled[0]
        kinds.append((f"{len(spelled)} symbol spelling",
                      f"{a['symbol']} / {b['symbol']}"
                      + (f" +{len(spelled) - 1} more" if len(spelled) > 1
                         else ""),
                      ": use the books' symbol in the holdings file",
                      "tjs list"))
    if moved:
        kinds.append((f"{len(moved) // 2} booked in another account",
                      names(moved), ": check the export's folder",
                      "tjs audit SYMBOL"))
    if transfer:
        kinds.append((f"{len(transfer)} transferred in with no cost",
                      names(transfer), ", kept out of the books",
                      "tjs transfers"))
    if short:
        kinds.append((f"{len(short)} short in the books", names(short),
                      ": a sale with no purchase in your files",
                      "tjs find-missing-history"))
    if before:
        accts = sorted({a for r in before for a in r["accounts"]})
        kinds.append((f"{len(before)} with fewer shares",
                      names(before), ": bought before your data, step 5b",
                      f"tjs opening "
                      f"{accts[0] if len(accts) == 1 else 'ACCOUNT'} FILE"))
    if more:
        kinds.append((f"{len(more)} more in the books", names(more),
                      ": a trade after your last export, or a booking "
                      "problem", "tjs audit SYMBOL"))
    from taxjson.lib.out import act as _act_line
    out: List[Tuple[str, str]] = []
    for head, syms, tail, cmd in kinds:
        text = f"{head} ({syms}){tail}"
        if len(_act_line(text, cmd)) > 100:
            text = f"{head}{tail}"
        out.append((text, cmd))
    return out


def _sanity_print_extras(groups, cost_all, cost_diffs, cost_matched,
                         cost_na, income_all, multi,
                         concise: bool = False) -> None:
    """The COST and INCOME sections of `tjs sanity` (after the
    quantity check, never changing its exit code). `concise` (the
    default view): one line each; `--details` has the tables."""
    from taxjson.lib.positions_check import REASON_TEXT
    from taxjson.lib.out import wrap as _wrap

    def _p(text: str, indent: str = "", hang: str = "  ") -> None:
        # Wrapped at the house width; captured (the checklist reads the
        # last line) it stays one line, as before.
        # Its own live exact Doc (a closure may not hold the function's).
        nd = _out.Doc(exact=True, width_=0, live=True)
        for ln in _wrap(text, None, indent, hang):
            nd.line(ln)
    # The text: a live exact Doc, each line printed when it is added,
    # as the print() it replaces printed it.
    d = _out.Doc(exact=True, width_=0, live=True)
    compared = cost_matched + len(cost_diffs)
    if concise:
        # The default view (Essentials first): one line each,
        # informational; --details has the tables and the reasons.
        if cost_diffs:
            d.blank()
            d.line(f"COST: {len(cost_diffs)} of {compared} differ from the "
                   f"broker's book value (informational) — tjs sanity "
                   f"--details")
        if income_all:
            d.blank()
            d.line(f"INCOME: {len(income_all)} dividend row(s) state shares "
                   f"the books did not hold — tjs sanity --details")
        return
    if compared or cost_na:
        d.blank()
        _p(f"COST — books vs the reports' cost: {compared} compared, "
           f"{cost_matched} within tolerance, {len(cost_diffs)} "
           f"differ" + (f", {cost_na} not compared" if cost_na else "")
           + " (informational: a broker's book value is not your "
             "ACB/basis; never changes the exit code).")
    if cost_diffs:
        lines = [("ACCOUNTS " if multi else "")
                 + "SYMBOL QTY CUR BROKER BOOKS DIFF REASON"]
        for c in cost_diffs:
            cells = [c["symbol"], f"{c['quantity']:g}", c["cost_currency"]
                     or "-", f"{M.shown(c['broker_cost'], 2):,.2f}",
                     f"{M.shown(c['books_cost'], 2):,.2f}", f"{M.shown(c['diff'], 2):+,.2f}",
                     ",".join(c["reasons"])
                     + ("" if c.get("explained") or
                        c["reasons"] == ["unexplained"] else "?")]
            if multi:
                cells.insert(0, "+".join(c["accounts"]))
            lines.append(" ".join(x.replace(" ", "_") if i < len(cells) - 1
                                  else x for i, x in enumerate(cells)))
        d.blank()
        _print_report_table(lines, fit=True, drop=(len(lines[0].split())
                                                    - 1,),
                            key=(0, 1) if multi else 0)
        d.blank()
        used = sorted({r for c in cost_diffs for r in c["reasons"]})
        d.line("Reasons" + (" (a ? = a possible cause, not checked to "
                            "close the gap):"
                            if any(not c.get("explained")
                                   and c["reasons"] != ["unexplained"]
                                   for c in cost_diffs) else ":"))
        for r in used:
            _p(f"{r}: {REASON_TEXT.get(r, r)}", "  ", "    ")
        natives = sorted({c["basis"] for c in cost_diffs
                          if c.get("basis", "").startswith("native")})
        for b in natives:
            _p(f"BOOKS here is the {b}.", "  ", "    ")
    na = [c for c in cost_all if c.get("status") == "n/a"]
    if na:
        _p("Not compared: " + "; ".join(
            f"{c['symbol']} ({c.get('note') or 'n/a'})" for c in na[:8])
            + (f"; +{len(na) - 8} more" if len(na) > 8 else ""))
    if income_all:
        from taxjson.lib.positions_check import (
            INCOME_WINDOW_DAYS as _INCOME_WINDOW_DAYS)
        d.blank()
        _p(f"INCOME ON SHARES THE BOOKS DO NOT HOLD — {len(income_all)} "
           f"dividend row(s) state more shares than the books held when "
           f"it was earned (on the record date; without one, at any "
           f"time in the {_INCOME_WINDOW_DAYS} days to the pay date) — "
           f"missing history or a missing trade is the usual cause; "
           f"informational:")
        lines = ["ACCOUNT SYMBOL PAID ON STATED BOOKS"]
        for m in income_all:
            lines.append(f"{m['account']} {m['symbol']} {m['date']} "
                         f"{m['on']}_({m['basis'].replace(' ', '_')}) "
                         f"{m['stated_shares']:g} {m['books_shares']:g}")
        # The old layout on purpose: the checklist's sanity step shows
        # the output's last line, which is this table's last row.
        _print_report_table(lines)


def _has_inputs(root: Path, name: str) -> bool:
    """inputs/<name>/ holds an activity file `tjs run` reads."""
    d = _PL.inputs_dir(root) / name
    return bool(input_files(d, ".csv") or input_files(d, ".tt"))
