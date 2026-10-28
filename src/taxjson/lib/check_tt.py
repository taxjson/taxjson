"""`taxjson check-tt`: every line of the .tt files read with the parsers
`taxjson run` uses (bin/taxjson_convert_tt and the dated-event readers),
and every problem named with its file and line — the run and
`taxjson-convert-tt` stop at the first malformed line; this goes on, so
one pass shows them all.

A line is one of: blank or a comment; a row of the books (BUYSELL,
DIVIDEND, OPENING, an ACQUIRED pair, ...); a dated event or declaration
the run reads but never books as a row (JOURNAL, RENAME, INKIND,
`OPENING ... cost=unknown`, ALLOWLOSS, NOTOWN, the FX-on-cash ledger
lines). An error is a line the run would refuse (a typo'd action, a bad
date or number, an OPENING in a crypto account, two declarations that
disagree);
a warning is what the run would say about the line and book anyway (a
bare symbol in an equity account, an unknown market suffix, a last line
without a line end). Nothing is written.

    check_project(root, accounts)       the project's accounts' .tt files
    check_paths(paths, account=...)     the files named
    render(doc, details=...)            the report
"""
from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ERROR = "error"
WARNING = "warning"
# The kinds a line is counted as.
KIND_ROW = "row"
KIND_EVENT = "event"
KIND_BLANK = "blank"


@dataclass
class Problem:
    severity: str           # ERROR | WARNING
    where: str              # "<file>:<n>" (file as the report names it)
    file: str
    line: int               # 0: the file as a whole
    message: str            # without the `where: ` prefix
    text: str = ""          # the line as written, stripped

    def as_dict(self) -> Dict[str, Any]:
        return {"severity": self.severity, "where": self.where,
                "file": self.file, "line": self.line,
                "message": self.message, "text": self.text}


@dataclass
class FileReport:
    file: str               # as the report names it
    path: Path
    account: str
    lines: int = 0          # lines in the file
    rows: int = 0           # rows of the books (an ACQUIRED pair: 2)
    events: int = 0         # dated events and declarations
    problems: List[Problem] = field(default_factory=list)
    # The rows of the books each line read as: (line, the line as
    # written, stripped, the row) — for the checks across lines
    # (_taxable_transfers).
    txs: List[Tuple[int, str, Dict[str, Any]]] = field(
        default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for p in self.problems if p.severity == ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for p in self.problems if p.severity == WARNING)

    def as_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "account": self.account,
                "lines": self.lines, "rows": self.rows,
                "events": self.events, "errors": self.errors,
                "warnings": self.warnings}


def _strip_where(msg: str, where: str) -> str:
    """The parsers prefix their message with `<where>: `; the report
    prints the place once."""
    msg = str(msg).strip()
    for pfx in (f"{where}: ", f"{where}:"):
        if msg.startswith(pfx):
            return msg[len(pfx):].lstrip()
    return msg


def _captured_warnings(buf: io.StringIO, where: str) -> List[str]:
    """The `warning: ...` lines the parsers print while reading a line
    (bare or unknown suffix, an odd fee), each without its labels."""
    out: List[str] = []
    for raw in buf.getvalue().splitlines():
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        for label in ("warning:", "note:"):
            if low.startswith(label):
                s = s[len(label):].strip()
                break
        out.append(_strip_where(s, where))
    return out


def parse_line(line: str, where: str, *, account: str, crypto: bool,
               equity: bool, rows: Optional[List[Dict[str, Any]]] = None
               ) -> Tuple[str, List[str]]:
    """One line through the readers `taxjson run` applies, in the run's
    order. Returns (kind, warnings); raises ValueError for a line the
    run would refuse, its message as the run prints it. `rows`, when
    given, receives the rows of the books the line reads as."""
    from taxjson.bin import taxjson_convert_tt as CT
    from taxjson.lib.cash_events import CashLineError
    from taxjson.lib.cash_events import parse_line as _cashline
    from taxjson.lib.core import is_opening_row
    from taxjson.lib.loss_overrides import parse_line as _allowloss
    if not CT.strip_tt_comment(line).strip():
        return KIND_BLANK, []
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        if CT.parse_inkind_line(line, where) is not None:
            return KIND_EVENT, _captured_warnings(buf, where)
        j = CT.parse_journal_line(line, where)
        if j is not None:
            if crypto:
                raise ValueError(
                    f"JOURNAL moves units between two listings of a "
                    f"security; account {account} is a crypto account "
                    f"(crypto = true) and has none: {j['line']!r}")
            return KIND_EVENT, _captured_warnings(buf, where)
        if CT.parse_rename_line(line, where) is not None:
            return KIND_EVENT, _captured_warnings(buf, where)
        if CT.parse_unknown_opening_line(line, where) is not None:
            return KIND_EVENT, _captured_warnings(buf, where)
        if _allowloss(line, where) is not None:
            return KIND_EVENT, _captured_warnings(buf, where)
        from taxjson.lib.transfer_in import parse_not_own_line
        if parse_not_own_line(line, where) is not None:
            return KIND_EVENT, _captured_warnings(buf, where)
        try:
            if _cashline(line, where) is not None:
                return KIND_EVENT, _captured_warnings(buf, where)
        except CashLineError as e:
            # As tt_to_json says it: read under the v2 ledger only.
            raise ValueError(
                f"{e} — a line of the FX-on-cash ledger v2 only (read "
                f"with fx_cash_ledger = \"v2\" or `taxjson fx-cash "
                f"--ledger v2`; the default ledger ignores it): fix it "
                f"to the form shown, or delete it") from e
        expanded = CT.expand_acquired(line)
        n = 0
        for one in (expanded if expanded is not None else [line]):
            tx = CT.parse_tt_line(one, account_name=account, source=where)
            if not tx:
                continue
            n += 1
            if crypto and is_opening_row(tx):
                # The run refuses it after the conversion
                # (taxjson_run._refuse_crypto_openings).
                raise ValueError(
                    f"account {account} is a crypto account: an OPENING "
                    f"line is for share accounts only — enter the coins' "
                    f"purchases as BUYSELL lines instead: "
                    f"{line.strip()!r}")
            if equity:
                CT._warn_bare_equity_symbol(tx, one, where)
            if rows is not None:
                rows.append(tx)
    return (KIND_ROW if n else KIND_BLANK), _captured_warnings(buf, where)


def check_file(path: Path, *, file: str, account: str, crypto: bool,
               equity: bool) -> FileReport:
    """Every line of one file."""
    from taxjson.lib.cli_diag import read_text_utf8
    rep = FileReport(file=file, path=Path(path), account=account)
    try:
        text = read_text_utf8(path)
    except (OSError, ValueError) as e:
        rep.problems.append(Problem(ERROR, file, file, 0,
                                    f"cannot be read: {e}"))
        return rep
    # The lines as tt_to_json reads and numbers them, split at a line
    # feed only: str.splitlines numbered every line after a form feed or
    # a U+2028 differently from the run (backlog TJ-0029).
    from taxjson.bin.taxjson_convert_tt import tt_lines
    lines = tt_lines(text)
    rep.lines = len(lines)
    for n, raw in enumerate(lines, 1):
        where = f"{file}:{n}"
        shown = raw.strip()
        txs: List[Dict[str, Any]] = []
        try:
            kind, warns = parse_line(raw, where, account=account,
                                     crypto=crypto, equity=equity,
                                     rows=txs)
        except ValueError as e:
            rep.problems.append(Problem(ERROR, where, file, n,
                                        _strip_where(e, where), shown))
            continue
        rep.txs.extend((n, shown, tx) for tx in txs)
        if kind == KIND_ROW:
            rep.rows += 1
        elif kind == KIND_EVENT:
            rep.events += 1
        for w in warns:
            rep.problems.append(Problem(WARNING, where, file, n, w, shown))
    # As tt_to_json warns: a file cut short still parses, its last number
    # possibly truncated.
    if text and not text.endswith(("\n", "\r")):
        from taxjson.bin.taxjson_convert_tt import strip_tt_comment
        last = strip_tt_comment(lines[-1]).strip()
        if last:
            rep.problems.append(Problem(
                WARNING, f"{file}:{len(lines)}", file, len(lines),
                "the last line has no line end — if the file was cut "
                "short, its last number may be truncated; check it", last))
    return rep


def _doc(reports: List[FileReport], extra: List[Problem]) -> Dict[str, Any]:
    problems = [p for r in reports for p in r.problems] + extra
    order = {ERROR: 0, WARNING: 1}
    problems.sort(key=lambda p: (order[p.severity], p.file, p.line))
    return {
        "files": [r.as_dict() for r in reports],
        "problems": [p.as_dict() for p in problems],
        "lines": sum(r.lines for r in reports),
        "rows": sum(r.rows for r in reports),
        "events": sum(r.events for r in reports),
        "errors": sum(1 for p in problems if p.severity == ERROR),
        "warnings": sum(1 for p in problems if p.severity == WARNING),
    }


def _is_crypto(acfg: Any) -> bool:
    return isinstance(acfg, dict) and bool(acfg.get("crypto"))


def _taxable_transfers(reports: List[FileReport], account: str,
                       acfg: Any) -> None:
    """The run's refusal of TRANSFER rows in a taxable account's books
    (lib/pipeline._handle_transfers, TransferValidationError), read from
    the account's .tt lines: a TRANSFER or ACQUIRED line whose TRANSFER
    row survives what the run nets first — an OPENING snapshot's cut-off
    (lib/opening.apply_opening_cutoff) and the zero-net groups
    (pipeline._drop_self_cancelling_transfers) — is an ERROR of its
    line; check-tt said "Every line parses." and the run stopped
    (backlog TJ-0006). With `transfers = true` the account's exports
    keep their TRANSFER rows in the books, and one may cancel the line:
    a WARNING. A sheltered account's TRANSFER rows are booked. (A broker
    trade inside a pair's span also stops the run; check-tt does not
    read the exports.)"""
    if not isinstance(acfg, dict) or acfg.get("type") != "taxable":
        return
    rows = [(r, n, shown, tx) for r in reports for n, shown, tx in r.txs]
    if not any(tx.get("action") == "TRANSFER" for *_x, tx in rows):
        return
    from taxjson.lib.core import coerce_transaction_row
    from taxjson.lib.opening import OpeningError, apply_opening_cutoff
    from taxjson.lib.pipeline import _drop_self_cancelling_transfers
    txs: List[Any] = []
    at: Dict[int, Tuple[FileReport, int, str]] = {}
    for r, n, shown, tx in rows:
        try:
            t = coerce_transaction_row(dict(tx), 0, "check-tt")
        except ValueError:
            continue
        txs.append(t)
        at[id(t)] = (r, n, shown)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        try:
            txs = apply_opening_cutoff(txs, report=buf)
        except OpeningError:
            pass            # _opening_snapshots names it (two dates)
        txs, _dropped = _drop_self_cancelling_transfers(txs)
    in_book = bool(acfg.get("transfers"))
    for t in txs:
        if t.action != "TRANSFER" or id(t) not in at:
            continue
        r, n, shown = at[id(t)]
        where = f"{r.file}:{n}"
        if any(p.where == where and p.severity == ERROR
               for p in r.problems):
            continue
        acquired = (shown.split() or [""])[0] == "ACQUIRED"
        what = (f"an ACQUIRED line books a counter-TRANSFER in taxable "
                f"account {account}" if acquired else
                f"a TRANSFER line in taxable account {account}")
        if in_book:
            r.problems.append(Problem(
                WARNING, where, r.file, n,
                f"{what}: `tjs run` refuses it unless it cancels a "
                f"TRANSFER row of the account's exports (transfers = "
                f"true keeps them in the books) — check that it does",
                shown))
            continue
        fix = ("write the purchase as a BUYSELL line dated its true day "
               "instead (the broker's transfer-in is kept out of the "
               "books)" if acquired else
               "replace it with the buy/sell history that established "
               "the position (BUYSELL lines)")
        r.problems.append(Problem(
            ERROR, where, r.file, n,
            f"{what} (type = \"taxable\"): `tjs run` stops on it "
            f"(TRANSFER is not allowed in taxable accounts) — {fix}",
            shown))


def _opening_snapshots(reports: List[FileReport], account: str) -> None:
    """The run's one snapshot date per symbol per account (lib/opening
    .snapshots, which merge2's cut-off applies to the account's books),
    read from the account's known-cost OPENING lines across all its
    files: a symbol with OPENING lines of two dates is an ERROR of the
    first line whose date differs, naming the line it disagrees with.
    check-tt said "Every line parses." and the run stopped on it
    (backlog TJ-0322). One error per symbol."""
    from taxjson.lib.core import is_opening_row
    from taxjson.lib.opening import OpeningError, snapshots
    first: Dict[str, Tuple[FileReport, int, Dict[str, Any]]] = {}
    said: set = set()
    for r in reports:
        for n, shown, tx in r.txs:
            if not is_opening_row(tx):
                continue
            sym = str(tx.get("symbol") or "")
            if sym in said:
                continue
            if sym not in first:
                first[sym] = (r, n, tx)
                continue
            r0, n0, tx0 = first[sym]
            try:
                snapshots([dict(tx0, account=account),
                           dict(tx, account=account)])
            except OpeningError as e:
                said.add(sym)
                where = f"{r.file}:{n}"
                r.problems.append(Problem(
                    ERROR, where, r.file, n,
                    f"{e} The other line: {r0.file}:{n0}.", shown))


def _not_own_accounts(reports: List[FileReport], root: Path, acct: str,
                      acfg: Any, accounts: Dict[str, Any]) -> None:
    """A NOTOWN line the run reads and says undoes no pairing, known by
    its accounts alone (lib/transfer_in.not_own_account_problem): in an
    account whose transfer rows are never paired, or naming a
    from-account taxjson.toml does not declare. WARNINGs: the run books
    as without the line and says so (backlog TJ-0075)."""
    if not reports:
        return
    from taxjson.lib.transfer_in import (NOTOWN_KEYWORD,
                                         not_own_account_problem,
                                         read_not_own)
    for n in read_not_own(root, [acct]):
        msg = not_own_account_problem(n, accounts)
        if not msg:
            continue
        file, _, num = n.where.rpartition(":")
        rep = next((r for r in reports if r.file == file), None)
        if rep is None or not num.isdigit():
            continue
        rep.problems.append(Problem(
            WARNING, n.where, file, int(num),
            f"this {NOTOWN_KEYWORD} line undoes no pairing: {msg}", n.line))


def check_project(root: Path, accounts: Dict[str, Any],
                  only: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """The .tt files of the project's accounts (`only`: these accounts),
    as `taxjson run` finds them (inputs/<account>/*.tt, the shared
    folder with `inputs_dir`), then the checks across lines the run
    makes up front: two declarations of one ticker change that disagree
    (lib/dated_events.read_declarations), a symbol opened twice at an
    unknown cost (lib/missing_history.read_tt_openings), a symbol with
    known-cost OPENING snapshots of two dates (_opening_snapshots), a
    TRANSFER row a taxable account's books cannot hold
    (_taxable_transfers), and a file name the run refuses."""
    return _doc(*_project_reports(root, accounts, only))


def _project_reports(root: Path, accounts: Dict[str, Any],
                     only: Optional[Iterable[str]] = None
                     ) -> Tuple[List[FileReport], List[Problem]]:
    """check_project's file reports and its problems of no one file."""
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.config_check import RESERVED_NAME_SUFFIXES
    from taxjson.lib.dated_events import DatedEventError, tt_files
    from taxjson.lib.dated_events import read_declarations
    from taxjson.lib.missing_history import TtOpeningError, read_tt_openings
    from taxjson.lib.pipeline import tt_json_path
    root = Path(root)
    names = sorted(only) if only is not None else sorted(accounts or {})
    reports: List[FileReport] = []
    extra: List[Problem] = []
    for acct in names:
        acfg = (accounts or {}).get(acct) or {}
        crypto = _is_crypto(acfg)
        by_out: Dict[str, str] = {}
        mine: List[FileReport] = []
        for tt in tt_files(_PL.inputs_dir(root) / acct):
            file = f"inputs/{acct}/{shown_name(tt)}"
            rep = check_file(tt, file=file, account=acct, crypto=crypto,
                             equity=not crypto)
            reports.append(rep)
            mine.append(rep)
            # The names the run refuses (taxjson_run.stage_account):
            # problems of the file, counted in its row of the table
            # (backlog TJ-0012: its status said `ok`).
            out = tt_json_path(root / "work", acct, tt.name).name
            if out in by_out:
                rep.problems.append(Problem(
                    ERROR, file, file, 0,
                    f"converts to the same work file as inputs/{acct}/"
                    f"{by_out[out]} ({out}): one would overwrite the "
                    f"other's rows; rename one (or merge the two)"))
            by_out.setdefault(out, shown_name(tt))
            stem = tt.stem.lower()
            clash = next((x for x in RESERVED_NAME_SUFFIXES
                          if stem.endswith(x)), None)
            if clash:
                rep.problems.append(Problem(
                    ERROR, file, file, 0,
                    f"a .tt file name may not end in {clash!r}: its "
                    f"converted JSON would read as a pipeline artifact "
                    f"(a spurious extra account in the reports); rename "
                    f"it, e.g. {tt.stem[:-len(clash)]}"
                    f"{clash.replace('_', '-')}.tt"))
        _taxable_transfers(mine, acct, acfg)
        if not crypto:
            _opening_snapshots(mine, acct)
        _not_own_accounts(mine, root, acct, acfg, accounts or {})
    # Across lines and files: the run's own up-front readers. A line
    # already refused above is not named twice.
    seen = {p.where for r in reports for p in r.problems
            if p.severity == ERROR}
    sub = {a: (accounts or {}).get(a) or {} for a in names}

    def _add(text: str) -> None:
        for msg in str(text).split("\n"):
            msg = msg.strip()
            if not msg:
                continue
            where, file, n = "", "", 0
            head = msg.split(" ", 1)[0].rstrip(":")
            if head.startswith("inputs/") and ":" in head:
                file, _, num = head.rpartition(":")
                if num.isdigit():
                    where, n = head, int(num)
            if where and where in seen:
                continue
            prob = Problem(ERROR, where or "project", file, n,
                           _strip_where(msg, where) if where else msg)
            rep = next((r for r in reports if r.file == file), None)
            (rep.problems if rep is not None else extra).append(prob)

    try:
        read_declarations(root, sub)
    except DatedEventError as e:
        _add(str(e))
    try:
        read_tt_openings(root, names)
    except TtOpeningError as e:
        _add(str(e))
    return reports, extra


def project_root(path: Path) -> Optional[Path]:
    """The project a .tt file sits in: the folder taxjson.toml is in, up
    to three levels above the file (as taxjson_convert_tt._equity_account
    looks), or, in the shared layout (inputs/ beside the year folders),
    the newest year folder. None when there is no project."""
    from taxjson.lib import project_layout as _PL
    for d in list(Path(path).resolve().parents)[:3]:
        if _PL.has_config(d):
            return d
        years = _PL.year_dirs(d)
        if years:
            return years[-1][1]
    return None


def _accounts_of(root: Path) -> Dict[str, Any]:
    from taxjson.lib import project_layout as _PL
    cfg = _PL.read_config_soft(root)
    accts = cfg.get("accounts") if isinstance(cfg, dict) else None
    return accts if isinstance(accts, dict) else {}


def project_accounts(path: Path) -> Optional[Dict[str, Any]]:
    """The `[accounts]` of the project a .tt file sits in (project_root);
    None when there is no project."""
    root = project_root(path)
    return None if root is None else _accounts_of(root)


def _reads(root: Path, acct: str, path: Path) -> bool:
    """`path` is one of the .tt files `taxjson run` reads for account
    `acct` of the project at `root` (inputs/<acct>/, or the shared
    folder `inputs_dir` names)."""
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.dated_events import tt_files
    try:
        me = Path(path).resolve()
        return any(t.resolve() == me
                   for t in tt_files(_PL.inputs_dir(root) / acct))
    except (OSError, RuntimeError):
        return False


def _as_in_project(root: Path, accounts: Dict[str, Any], acct: str,
                   path: Path, label: str
                   ) -> Tuple[FileReport, List[Problem]]:
    """The file as `check_project` checks it in its account — its own
    lines, its name, the checks across its account's lines (a symbol
    opened twice at an unknown cost, two RENAME declarations that
    disagree, a TRANSFER the run does not net) — named `label`; and the
    problems of the account's other files that name it."""
    reports, loose = _project_reports(root, accounts, only=[acct])
    me = Path(path).resolve()
    rep = next(r for r in reports if r.path.resolve() == me)
    old = rep.file
    for p in rep.problems:
        if p.where == old or p.where.startswith(f"{old}:"):
            p.where = label + p.where[len(old):]
        p.file = label
    rep.file = label
    others = [p for r in reports if r is not rep for p in r.problems]
    return rep, [p for p in others + loose
                 if p.severity == ERROR and old in p.message]


def check_paths(paths: Iterable[Path], account: Optional[str] = None,
                crypto: Optional[bool] = None,
                shown: Optional[Iterable[str]] = None,
                root: Optional[Path] = None) -> Dict[str, Any]:
    """The files named, each read as the account `account` (default: the
    name of the folder it sits in, as inputs/<account>/x.tt names it),
    each named in the report as `shown` gives it (default: its path).
    The account's kind (crypto or shares) comes from the project's
    taxjson.toml (`root`, the command's project, when the file is one
    of its account's; else the project above the file) when it declares
    that account; `crypto` overrides it. A file the run reads for that
    account gets the account's checks too, as `check_project` makes
    them (backlog TJ-0007: two OPENING cost=unknown lines of one symbol,
    a file name the run refuses, said "Every line parses."), unless
    `account` or `crypto` reads it as another account or kind. A file in
    no project, or of an account the project does not declare, is
    checked as a share account's without the bare-symbol warning (its
    kind is not known)."""
    reports: List[FileReport] = []
    extra: List[Problem] = []
    loose: Dict[str, List[FileReport]] = {}
    paths = [Path(p) for p in paths]
    labels = list(shown) if shown is not None else [str(p) for p in paths]
    for p, label in zip(paths, labels):
        acct = account or p.resolve().parent.name or "default"
        proj = root if (root is not None and _reads(root, acct, p)) \
            else project_root(p)
        accts = _accounts_of(proj) if proj is not None else None
        acfg = (accts or {}).get(acct)
        if crypto is not None:
            is_crypto, equity = crypto, not crypto
        elif isinstance(acfg, dict):
            is_crypto = _is_crypto(acfg)
            equity = not is_crypto
        else:
            is_crypto, equity = False, False
        if (isinstance(acfg, dict) and is_crypto == _is_crypto(acfg)
                and acct == p.resolve().parent.name
                and _reads(proj, acct, p)):
            rep, more = _as_in_project(proj, accts, acct, p, label)
            reports.append(rep)
            extra.extend(x for x in more if x not in extra)
            continue
        rep = check_file(p, file=label, account=acct, crypto=is_crypto,
                         equity=equity)
        reports.append(rep)
        loose.setdefault(acct, []).append(rep)
    # The files named outside their project's account: the snapshot
    # dates across the files read as one account (backlog TJ-0322).
    for acct, reps in loose.items():
        _opening_snapshots(reps, acct)
    return _doc(reports, extra)


def _status(f: Dict[str, Any]) -> str:
    parts = []
    if f["errors"]:
        parts.append(f"{f['errors']} error{_s(f['errors'])}")
    if f["warnings"]:
        parts.append(f"{f['warnings']} warning{_s(f['warnings'])}")
    return ", ".join(parts) if parts else "ok"


def render(doc: Dict[str, Any], width_: Optional[int] = None) -> List[str]:
    """The report (docs/output-style.md): a title, every file checked
    with its account, counts and status (`ok`, or its errors and
    warnings), then the errors and the warnings, each `file:line:
    message`. The last line says what to do."""
    from taxjson.lib.out import Doc, act
    n_files = len(doc["files"])
    d = Doc(f"CHECK TT — {doc['lines']} line{_s(doc['lines'])} in "
            f"{n_files} file{_s(n_files)} ({doc['rows']} "
            f"row{_s(doc['rows'])} of the books, {doc['events']} "
            f"event{_s(doc['events'])})", width_=width_)
    if doc["files"]:
        d.blank()
        d.table(["FILE", "ACCOUNT", "LINES", "ROWS", "EVENTS", "STATUS"],
                [[f["file"], f["account"], str(f["lines"]), str(f["rows"]),
                  str(f["events"]), _status(f)] for f in doc["files"]],
                indent="  ")
    for sev, head in ((ERROR, "ERRORS — lines the run refuses"),
                      (WARNING, "WARNINGS — lines the run books as "
                                "written")):
        ps = [p for p in doc["problems"] if p["severity"] == sev]
        if not ps:
            continue
        d.section(f"{head} ({len(ps)})")
        for p in ps:
            d.item(f"{p['where']}: {p['message']}")
    d.blank()
    if not n_files:
        d.para("No .tt file to check.")
    elif doc["errors"]:
        # Problems, not lines: a file name the run refuses is one too
        # (backlog TJ-0012).
        d.line(act(f"{doc['errors']} problem{_s(doc['errors'])} the run "
                   f"refuses; fix {'it' if doc['errors'] == 1 else 'them'} "
                   f"before `tjs run`"))
    elif doc["warnings"]:
        d.para(f"Every line parses; {doc['warnings']} "
               f"warning{_s(doc['warnings'])} to look at.")
    else:
        d.para("Every line parses.")
    return d.lines()


def _s(n: int) -> str:
    return "" if n == 1 else "s"
