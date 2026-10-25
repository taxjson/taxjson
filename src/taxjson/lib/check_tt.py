"""`taxjson check-tt`: every line of the .tt files read with the parsers
`taxjson run` uses (bin/taxjson_convert_tt and the dated-event readers),
and every problem named with its file and line — the run and
`taxjson-convert-tt` stop at the first malformed line; this goes on, so
one pass shows them all.

A line is one of: blank or a comment; a row of the books (BUYSELL,
DIVIDEND, OPENING, an ACQUIRED pair, ...); a dated event or declaration
the run reads but never books as a row (JOURNAL, RENAME, INKIND,
`OPENING ... cost=unknown`, ALLOWLOSS, the FX-on-cash ledger lines). An
error is a line the run would refuse (a typo'd action, a bad date or
number, an OPENING in a crypto account, two declarations that disagree);
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
               equity: bool) -> Tuple[str, List[str]]:
    """One line through the readers `taxjson run` applies, in the run's
    order. Returns (kind, warnings); raises ValueError for a line the
    run would refuse, its message as the run prints it."""
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
    lines = text.splitlines()
    rep.lines = len(lines)
    for n, raw in enumerate(lines, 1):
        where = f"{file}:{n}"
        shown = raw.strip()
        try:
            kind, warns = parse_line(raw, where, account=account,
                                     crypto=crypto, equity=equity)
        except ValueError as e:
            rep.problems.append(Problem(ERROR, where, file, n,
                                        _strip_where(e, where), shown))
            continue
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


def check_project(root: Path, accounts: Dict[str, Any],
                  only: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """The .tt files of the project's accounts (`only`: these accounts),
    as `taxjson run` finds them (inputs/<account>/*.tt, the shared
    folder with `inputs_dir`), then the checks across lines the run
    makes up front: two declarations of one ticker change that disagree
    (lib/dated_events.read_declarations), a symbol opened twice at an
    unknown cost (lib/missing_history.read_tt_openings), and a file
    name the run refuses."""
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
        for tt in tt_files(_PL.inputs_dir(root) / acct):
            file = f"inputs/{acct}/{shown_name(tt)}"
            reports.append(check_file(tt, file=file, account=acct,
                                      crypto=crypto, equity=not crypto))
            # The names the run refuses (taxjson_run.stage_account).
            out = tt_json_path(root / "work", acct, tt.name).name
            if out in by_out:
                extra.append(Problem(
                    ERROR, file, file, 0,
                    f"converts to the same work file as inputs/{acct}/"
                    f"{by_out[out]} ({out}): one would overwrite the "
                    f"other's rows; rename one (or merge the two)"))
            by_out.setdefault(out, shown_name(tt))
            stem = tt.stem.lower()
            clash = next((x for x in RESERVED_NAME_SUFFIXES
                          if stem.endswith(x)), None)
            if clash:
                extra.append(Problem(
                    ERROR, file, file, 0,
                    f"a .tt file name may not end in {clash!r}: its "
                    f"converted JSON would read as a pipeline artifact "
                    f"(a spurious extra account in the reports); rename "
                    f"it, e.g. {tt.stem[:-len(clash)]}"
                    f"{clash.replace('_', '-')}.tt"))
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
    return _doc(reports, extra)


def project_accounts(path: Path) -> Optional[Dict[str, Any]]:
    """The `[accounts]` of the project a .tt file sits in: the folder
    taxjson.toml is in, up to three levels above the file (as
    taxjson_convert_tt._equity_account looks), or, in the shared layout
    (inputs/ beside the year folders), the newest year folder's. None
    when there is no project."""
    from taxjson.lib import project_layout as _PL

    def _accounts(root: Path) -> Dict[str, Any]:
        cfg = _PL.read_config_soft(root)
        accts = cfg.get("accounts") if isinstance(cfg, dict) else None
        return accts if isinstance(accts, dict) else {}

    for d in list(Path(path).resolve().parents)[:3]:
        if _PL.has_config(d):
            return _accounts(d)
        years = _PL.year_dirs(d)
        if years:
            return _accounts(years[-1][1])
    return None


def check_paths(paths: Iterable[Path], account: Optional[str] = None,
                crypto: Optional[bool] = None) -> Dict[str, Any]:
    """The files named, each read as the account `account` (default: the
    name of the folder it sits in, as inputs/<account>/x.tt names it).
    The account's kind (crypto or shares) comes from the project's
    taxjson.toml above the file when it declares that account; `crypto`
    overrides it. A file in no project, or of an account the project
    does not declare, is checked as a share account's without the
    bare-symbol warning (its kind is not known)."""
    reports: List[FileReport] = []
    for p in paths:
        p = Path(p)
        acct = account or p.resolve().parent.name or "default"
        accts = project_accounts(p)
        acfg = (accts or {}).get(acct)
        if crypto is not None:
            is_crypto, equity = crypto, not crypto
        elif isinstance(acfg, dict):
            is_crypto = _is_crypto(acfg)
            equity = not is_crypto
        else:
            is_crypto, equity = False, False
        reports.append(check_file(p, file=str(p), account=acct,
                                  crypto=is_crypto, equity=equity))
    return _doc(reports, [])


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
        d.line(act(f"{doc['errors']} line{_s(doc['errors'])} cannot be "
                   f"read; fix {'it' if doc['errors'] == 1 else 'them'} "
                   f"before `tjs run`"))
    elif doc["warnings"]:
        d.para(f"Every line parses; {doc['warnings']} "
               f"warning{_s(doc['warnings'])} to look at.")
    else:
        d.para("Every line parses.")
    return d.lines()


def _s(n: int) -> str:
    return "" if n == 1 else "s"
