"""What the stages printed: echoed to the console and folded into the .sum DIAGNOSTICS."""
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from taxjson.project.settings import settings_of
from taxjson.project.toml_io import TOML_ERRORS, read_toml
from taxjson.lib.report_fmt import fmt_qty6 as _fmt_qty
from taxjson.lib.out import unmarked
from taxjson.lib import out as _out

from taxjson.pipeline import run_state
from taxjson.pipeline.config import load_config
from taxjson.pipeline.messages import (
    _die, _PROG, _run_concise, _say, _say_once, _step,
)
from taxjson.pipeline.tools import (
    _echo_captured, _echo_stage_stderr, _PARSE_COUNT_RE, run_to_file,
)


# taxjson-brokerage's zero-row warning (captured in the parse .diag).
_ZERO_TX_RE = re.compile(r'^warning: (.+?) parsed to 0 transactions\b')


def _load_holdings_summary(toml_path: Path) -> Dict[str, Tuple[float, float]]:
    """Parse a holdings.toml into `{symbol: (qty, total_cost)}`. Used
    by `print_holdings_diff` to compare snapshots. Returns an empty
    dict for missing or unreadable files."""
    if not toml_path.exists():
        return {}
    try:
        doc = read_toml(toml_path)
    except (*TOML_ERRORS, OSError, UnicodeDecodeError):
        return {}
    # A damaged snapshot (it is the previous run's own report, used
    # only for the 'what moved' lines) reads as none: `holding = "x"`
    # or a quantity "abc" tracebacked `run` (re-audit A2-0792).
    rows = doc.get('holding', [])
    if not isinstance(rows, list):
        return {}
    out: Dict[str, Tuple[float, float]] = {}
    for h in rows:
        if not isinstance(h, dict):
            continue
        sym = h.get('symbol')
        if not sym or not isinstance(sym, str):
            continue
        if h.get('asset_type') == 'crypto' and '.' in sym:
            # A coin by its bare name: the venue suffix (BTC.KR, backlog
            # TJ-0418) is not a move, nor is its arrival in the file.
            sym = sym.rsplit('.', 1)[0]
        try:
            out[sym] = (
                float(h.get('quantity', 0.0)),
                float(h.get('total_cost', 0.0)),
            )
        except (TypeError, ValueError):
            continue
    return out


# Alias: tests and older callers import `_fmt_qty` from here.


def maybe_print_holdings_diff(prev_path: Path, curr_path: Path,
                              account: str = "") -> None:
    """Print the holdings diff if a baseline snapshot exists; silent
    otherwise (so first runs don't emit a confusing "all positions are
    new" block). Encapsulated so the first-run-silent rule is testable
    in isolation from `stage_account`."""
    if not prev_path.exists():
        return
    print_holdings_diff(
        _load_holdings_summary(prev_path),
        _load_holdings_summary(curr_path),
        account=account,
    )


def write_holdings_toml(name: str, export_cmd: List[str], cache: Path,
                        reports_dir: Path) -> None:
    """reports/<name>_holdings.toml from `export_cmd` (a taxjson-export
    --holdings-toml command), then the quantities that moved since the
    last run's file."""
    build_holdings_toml(name, export_cmd, cache, reports_dir)
    say_holdings_toml(name, cache, reports_dir, built=True)


def build_holdings_toml(name: str, export_cmd: List[str], cache: Path,
                        reports_dir: Path) -> None:
    """reports/<name>_holdings.toml from `export_cmd`, the file it replaces
    kept as work/<name>_holdings_prev.toml (the stage table's holdings row)."""
    holdings_toml = reports_dir / f"{name}_holdings.toml"
    # Snapshot the existing holdings.toml so the post-export diff
    # has a baseline. First-run / no-prior-snapshot cases produce
    # no diff (correct behaviour — there's nothing to compare to).
    prev_holdings = cache / f"{name}_holdings_prev.toml"
    if holdings_toml.exists():
        # (a fresh file: never written through a symlink at the
        # snapshot's name — security review M1)
        from taxjson.lib.safe_write import write_atomic
        write_atomic(prev_holdings, holdings_toml.read_bytes())
    elif prev_holdings.exists():
        # Stale snapshot from a prior run whose output was wiped;
        # discard so we don't diff against an out-of-date baseline.
        prev_holdings.unlink()
    run_to_file(export_cmd, holdings_toml, capture_diag=False)


def say_holdings_toml(name: str, cache: Path, reports_dir: Path, *,
                      built: bool) -> None:
    """The holdings handoff's step, every run, and (when this run `built`
    it) the quantities that moved since the file it replaced."""
    holdings_toml = reports_dir / f"{name}_holdings.toml"
    _wrote(holdings_toml, reports_dir.parent, "holdings")
    if not built:
        return
    # Surface quantity changes vs the prior run so the user can
    # sanity-check trades at a glance (e.g. "I didn't expect SAMPLG
    # to move — did I import the wrong file?"). Silent on the
    # first run (no baseline) and silent when nothing changed.
    maybe_print_holdings_diff(cache / f"{name}_holdings_prev.toml", holdings_toml, name)


def fill_moved_cost(name: str, cmd: Optional[List[str]], cache: Path,
                    reports_dir: Path) -> None:
    """After the passes across accounts: write reports/<name>_holdings.toml
    again when it has a holding with no cost in the account's own books
    (`cost_unknown = true`: units moved in from another of your accounts),
    with the figure the passes have for it (work/<name>_gains_wash.json,
    `tjs list`'s; taxjson-export --blended-gains, backlog TJ-0598): the
    holdings row's build, asked again after them
    (stages.stage_moved_holdings). This changes no quantity."""
    holdings = reports_dir / f"{name}_holdings.toml"
    wash = cache / f"{name}_gains_wash.json"
    if not cmd or not wash.is_file() or not holdings.is_file():
        return
    try:
        if "cost_unknown = true" not in holdings.read_text(encoding="utf-8"):
            return
    except (OSError, UnicodeDecodeError):
        return
    try:
        run_to_file(list(cmd) + ["--blended-gains", str(wash)],
                    holdings, capture_diag=False)
    except subprocess.CalledProcessError:
        # The file stage_account wrote stays (cost_unknown).
        _say("warning", f"{name}: the holdings file keeps units moved "
             f"in from another of your accounts at an unknown cost "
             f"(work/{wash.name} could not be read for theirs)",
             prog=_PROG)


def print_holdings_diff(prev: Dict[str, Tuple[float, float]],
                         curr: Dict[str, Tuple[float, float]],
                         account: str = "") -> None:
    """Emit a one-line-per-changed-symbol summary so the user can
    sanity-check what moved since the last run. Quiet when nothing
    changed (no news is good news; the standard `→ holdings.toml`
    print already confirms the file was written). The run's default
    console says it in ONE line naming the symbols (`account` first),
    the line per symbol with --details (backlog TJ-0177)."""
    all_syms = set(prev) | set(curr)
    changes: List[str] = []
    moved: List[str] = []
    qty_eps = 1e-9
    for sym in sorted(all_syms):
        p_qty, _ = prev.get(sym, (0.0, 0.0))
        c_qty, _ = curr.get(sym, (0.0, 0.0))
        if abs(p_qty - c_qty) < qty_eps:
            continue
        moved.append(sym)
        if abs(p_qty) < qty_eps and abs(c_qty) > qty_eps:
            changes.append(f"    + {sym}: {_fmt_qty(c_qty)} (new position)")
        elif abs(c_qty) < qty_eps and abs(p_qty) > qty_eps:
            changes.append(f"    - {sym}: was {_fmt_qty(p_qty)} (closed)")
        else:
            delta = c_qty - p_qty
            sign = '+' if delta > 0 else ''
            changes.append(
                f"    ~ {sym}: {_fmt_qty(p_qty)} → {_fmt_qty(c_qty)} "
                f"({sign}{_fmt_qty(delta)})"
            )
    if changes:
        from taxjson.lib.out import width as _w
        if _run_concise():
            # One line: the count and the first symbols.
            from taxjson.lib.out import show
            run_state.current.details_hidden = True
            show([f"Info: {account + ': ' if account else ''}"
                  f"{_count_noun(len(moved), 'holding')} changed since the "
                  f"last run ("
                  + ", ".join(moved[:3])
                  + (f" +{len(moved) - 3} more" if len(moved) > 3 else "")
                  + ")"], sys.stdout)
            return
        if _w(sys.stdout) > 0:
            # The run's console: a message and its flush-left
            # continuation lines (docs/output-style.md, The run's
            # console).
            from taxjson.lib.out import show
            show([f"Info: holdings changed since the last run "
                  f"({len(changes)}):"]
                 + [line.strip() for line in changes], sys.stdout)
            return
        # A live exact Doc prints each line as the raw print() did.
        d = _out.Doc(exact=True, width_=0, live=True)
        d.line(f"  holdings changes vs prior run ({len(changes)}):")
        for line in changes:
            d.line(line)


def _console_names(files) -> Callable[[str], str]:
    """A function giving a captured line back its real file names for
    the console: the .diag masks account-number-like parts of names
    (shown_name), the person's own terminal names each file as it is on
    disk. A masked name two of `files` share stays masked."""
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.out import one_line
    real: Dict[str, set] = {}
    for p in files or ():
        # Control characters escaped (one_line): a name holding a
        # newline stays one console line.
        real.setdefault(shown_name(p), set()).add(one_line(Path(p).name))
    pairs = sorted(((m, next(iter(r))) for m, r in real.items()
                    if len(r) == 1 and next(iter(r)) != m),
                   key=lambda mr: -len(mr[0]))
    if not pairs:
        return lambda line: line
    rx = re.compile("|".join(
        r"(?<![\w*])" + re.escape(m) + r"(?![\w*])" for m, _r in pairs))
    table = dict(pairs)
    return lambda line: rx.sub(lambda mm: table[mm.group(0)], line)


def echo_parse_stats(out_path: Path, files=None) -> None:
    """Re-emit per-file transaction counts and parse warnings from the
    parser's stderr (captured to `<out_path>.diag` by `run_to_file`)
    onto the wrapper's stdout. Visibility is the safety net that
    would have caught the cycle-4 Webull `_find_header` regression
    instantly instead of needing an empty cache file to surface the
    silent zero-tx output. `files`: the parsed inputs — the console
    names them as they are on disk (the .diag keeps them masked)."""
    diag_path = out_path.with_name(out_path.name + ".diag")
    if not diag_path.exists():
        return
    _real = _console_names(files)
    echoing = False
    # The captured count lines: a live exact Doc prints each as the
    # raw print() did, between _echo_captured's lines.
    d = _out.Doc(exact=True, width_=0, live=True)
    for line in diag_path.read_text(errors="replace").splitlines():
        line = _real(line)
        if (echoing and line[:1] in (" ", "\t") and line.strip()
                and not _PARSE_COUNT_RE.match(line)):
            # An echoed warning's indented continuation (the GLOBAL line
            # under a ticker-change hint, re-audit A2-0613).
            _echo_captured(line)
            continue
        echoing = line.startswith((UNBOOKED_PREFIX, ATTENTION_PREFIX))
        if _PARSE_COUNT_RE.match(line):
            # Shown to a person: `Info: <file>: N tax objects`
            # (stage_msg.reword). Captured: the parser's own line, its
            # leading indent kept. (A file name's control characters
            # are shown escaped.)
            from taxjson.lib.out import shown, width as _w
            if _w(sys.stdout) > 0:
                _echo_captured(line)
            else:
                d.line(shown(line))
        elif line.startswith("warning: ") and " parsed to 0 transactions" in line:
            # Indent the warning to match per-file count nesting.
            _echo_captured(line)
        elif line.startswith(UNBOOKED_PREFIX):
            # Input rows the parser knows are real events but could not
            # book (e.g. Kraken ledger trades with no trades-export
            # fill): the tax numbers are missing them. Console, always.
            _echo_captured(line)
        elif line.startswith(ATTENTION_PREFIX):
            # Statement coverage / identity the numbers silently depend
            # on (an IB statement ending before year end, no Cash Report
            # to reconcile against). Console, always.
            _echo_captured(line)
        elif line.startswith(_codes_note_head()):
            # Questrade internal codes booked under the tickers the
            # project's other exports name (lib/symbol_codes): one note
            # per account, in place of the per-code ATTENTION lines.
            _echo_captured(line)


def _echo_tt_totals(account: str, tt: Path, out_path: Path,
                    strict: bool = False) -> None:
    """The .tt lines whose total is not qty x price +/- fee
    (lib/tt_totals, read from the .tt stage's .diag): one `Warning:`
    each on the console, naming file:line, the written total and the
    formula's; `--strict` stops on them. The file is named as the
    run's step line names it (masked: brokerages.base.shown_name), and
    the line is quoted as written (an ACQUIRED line, not the BUYSELL it
    expands to: third pre-release review, finding 9)."""
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.tt_totals import read_diag, source_line
    found = read_diag(out_path)
    if not found:
        return
    shown = shown_name(tt)
    for m in found:
        line_no = m.where.rsplit(":", 1)[-1]
        head, details = m.message(f"inputs/{account}/{shown}:{line_no}",
                                  source_line(tt, m.where))
        _say_once(("tt-total", account, m.where), "warning", head,
                  *details, prog=_PROG)
    if strict:
        _die(f"--strict: {account}: inputs/{account}/{shown} has "
             f"{len(found)} line(s) whose total is not qty x price +/- "
             f"fee (warning above) — aborting",
             "Fix the total, or make the line agree with it (the "
             "warning above says how).")


# convert-tt's warning for a bare symbol on an equity account's .tt line
# (tt_format._warn_bare_equity_symbol), as its .diag keeps it.
_TT_BARE_RE = re.compile(r"^warning: (?P<where>\S+:\d+): symbol (?P<sym>\S+) "
                         r"has no market suffix")


def _echo_tt_bare_symbols(account: str, tt_jsons: List[Path],
                          parsed: List[Path]) -> None:
    """A .tt line whose symbol has no market suffix (AAPL) while the
    account's broker rows hold the suffixed listing (AAPL.US) opens a
    separate pool: the broker's sale of AAPL.US then goes short. The
    convert-tt warning reached only the .sum; the console gets one line
    naming file:line and the listing the books hold. Read from the
    persisted .diag on every run, cached or not."""
    import json as _json
    bare = []
    for out in tt_jsons:
        for line in _diag_lines(out):
            m = _TT_BARE_RE.match(line)
            if m:
                bare.append((m["where"], m["sym"].upper()))
    if not bare:
        return
    held: Dict[str, set] = {}
    for p in parsed:
        try:
            rows = _json.loads(p.read_text(encoding="utf-8")).get(
                "transactions") or []
        except (OSError, ValueError, AttributeError):
            continue
        for t in rows:
            sym = str((t or {}).get("symbol") or "").upper()
            if "." in sym:
                held.setdefault(sym.split(".", 1)[0], set()).add(sym)
    for where, sym in bare:
        listings = sorted(held.get(sym, ()))
        if not listings:
            continue
        _say_once(("tt-bare", account, where), "warning",
                  f"inputs/{account}/{where}: {sym} has no market suffix: "
                  f"a pool of its own, apart from {' / '.join(listings)} "
                  f"in the books", prog=_PROG)


def _codes_note_head() -> str:
    from taxjson.lib.stage_msg import NOTE_HEAD
    return NOTE_HEAD


def _split_msg(text: str) -> Tuple[str, ...]:
    """A one-string message as (headline, detail) — split at its first
    clause break (lib/out.split_message) — or (text,)."""
    from taxjson.lib.out import split_message
    head, rest = split_message(str(text))
    return (head, rest) if rest else (head,)


def _wrote(path: Path, root: Path, what: str = "summary") -> None:
    """`==> Writing summary reports/margin.sum`: a file the run writes,
    named relative to the project."""
    from taxjson.lib.out import relpath
    shown = relpath(path, root)
    _step(f"Writing {what} {shown}{'/' if path.is_dir() else ''}")


# Parser warning prefix for rows that are known tax events the parser
# could NOT book. `tjs run` echoes these to the console and, under
# --strict, refuses to publish (R1-104).
UNBOOKED_PREFIX = "warning: UNBOOKED:"
# Parser warning prefix for input COVERAGE / identity problems (lib/
# brokerages/ib_extractor.ATTENTION_PREFIX): echoed to the console.
ATTENTION_PREFIX = "warning: ATTENTION:"


def echo_attention_lines(out_path: Path, prefix: Union[str, Tuple[str, ...]] = "",
                         exclude: Optional[str] = None,
                         once: bool = False,
                         unless: Optional[Callable[[str], bool]] = None) -> None:
    """Print the ATTENTION lines of a stage's persisted .diag (only
    those whose text starts with `prefix`, or with one of several, none
    starting with `exclude`, none `unless` says to leave out) on the
    console. `once`: an engine pass's
    line — shown once per run whichever pass says it (_echo_captured)."""
    starts = tuple(ATTENTION_PREFIX + " " + p
                   for p in ((prefix,) if isinstance(prefix, str) else prefix))
    diag_path = out_path.with_name(out_path.name + ".diag")
    try:
        lines = diag_path.read_text(errors="replace").splitlines()
    except OSError:
        return
    echoing = False
    for line in lines:
        if echoing and line[:1] in (" ", "\t") and line.strip():
            _echo_captured(line, once=once)
            continue
        echoing = (line.startswith(starts)
                   and not (exclude and line.startswith(
                       ATTENTION_PREFIX + " " + exclude))
                   and not (unless is not None and unless(line)))
        if echoing:
            _echo_captured(line, once=once)


def _diag_lines(out_path: Path) -> List[str]:
    """The lines of a stage's persisted .diag ([] when none)."""
    try:
        return out_path.with_name(out_path.name + ".diag").read_text(
            errors="replace").splitlines()
    except OSError:
        return []


def _attention_short_lines(out_path: Path) -> List[str]:
    """The `ATTENTION: short:` lines of a gains stage's persisted .diag
    (lib/pipeline.ATTENTION_SHORT)."""
    from taxjson.lib.pipeline import ATTENTION_SHORT
    diag_path = out_path.with_name(out_path.name + ".diag")
    try:
        lines = diag_path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in lines if ln.startswith(ATTENTION_SHORT)]


def _attention_unbacked_lines(out_path: Path) -> List[str]:
    """The `ATTENTION: unbacked close:` lines of a gains stage's
    persisted .diag (lib/unbacked_close.ATTENTION_UNBACKED): a trade the
    broker marks CLOSE with units the account's position cannot back,
    reported by hand (CA-ACB-11, CA-OPT-12)."""
    from taxjson.lib.unbacked_close import ATTENTION_UNBACKED
    return [ln for ln in _diag_lines(out_path)
            if ln.startswith(ATTENTION_UNBACKED)]


def unbooked_lines(out_path: Path) -> List[str]:
    diag_path = out_path.with_name(out_path.name + ".diag")
    if not diag_path.exists():
        return []
    return [ln for ln in diag_path.read_text(errors="replace").splitlines()
            if ln.startswith(UNBOOKED_PREFIX)]


def attention_lines(out_path: Path) -> List[str]:
    """A stage's captured `warning: ATTENTION:` lines (its .diag)."""
    diag_path = out_path.with_name(out_path.name + ".diag")
    if not diag_path.exists():
        return []
    return [ln for ln in diag_path.read_text(errors="replace").splitlines()
            if ln.startswith(ATTENTION_PREFIX)]


_DIAG_MARKER_RE = re.compile(
    # `warning: …` (parser-layer, bare) or GNU-style `<prog>: warning: …`
    # (bin CLIs per AUDIT-2026-07-ui §1C1, e.g. `taxjson-fill-crypto: warning:`).
    # `validation:` — merge2 --validate's report header ("validation: N
    # error(s) …" + indented TX lines); FUZZ #M found hard ERRORs living
    # only in the raw .diag because this regex dropped the header.
    r"^(?:[\w./-]+:\s+)?(?:ok|warning|note|error|validation):",
    re.IGNORECASE)


# Sidecars written by the cross-account passes AFTER <acct>.sum: the
# blended pass's per-account mirror and the single-account crypto wash
# pass's stderr. They belong to <acct>_wash.sum only — read into the
# baseline <acct>.sum they were one run stale (R1-259).
_POST_PASS_DIAG_SUFFIXES = ("_blend.diag", "_cryptoblend.diag",
                            "_gains_wash.json.diag", "_listing_evidence.diag")
_DIAG_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# An account's file as the messages name it: inputs/<account>/<file>
# (the .tt line locations, lib/dated_events._where; the parsers' and the
# run's own messages), or a path ending so (a stage given the absolute
# path of an input file).
_DIAG_INPUTS_RE = re.compile(r"(?<![\w.-])inputs/([^/\s`'\"]+)/")


def _diag_block_account(block: List[str], names: List[str]
                        ) -> Optional[str]:
    """The one account (of `names`) whose files a diagnostics block
    (marker line and continuations) names by their inputs/<account>/
    path, or None: none, or files of two accounts (a blend-wide
    message). Backlog TJ-0252."""
    found = {m.group(1) for ln in block
             for m in _DIAG_INPUTS_RE.finditer(ln)} & set(names)
    return next(iter(found)) if len(found) == 1 else None


def collect_diagnostics(cache: Path, account: str, *,
                        post_pass: bool = True,
                        blended: bool = False) -> str:
    """Concatenate persisted stderr diagnostics across an account's
    pipeline stages, keeping ok/warning/note/error lines — bare or
    prog-prefixed (`taxjson-fill-crypto: warning: …`) — plus their
    indented continuation lines. post_pass=False leaves out the
    cross-account passes' sidecars (_POST_PASS_DIAG_SUFFIXES).
    blended=True (the filing-basis <acct>_wash.sum of an account the
    blended s.47 pass covers) leaves out the isolated per-account gains
    pass's <acct>_gains.json.diag: the blend re-ran the engine over the
    same rows (its mirror <acct>_blend.diag carries what it said), and
    the per-account pool's notes — a s.40(3) deemed gain on a return
    of capital beyond the account's own ACB or on its empty pool — are
    not what the blended books booked (A2-0654, A2-1117).

    The engine runs over the whole history, so a year's banner used to
    ask for action on events of a LATER year (declare 2026 transfer
    legs in the 2025 report, S038-05). A note whose dates all fall
    after the tax year's 30-day superficial-loss tail is listed last,
    under its own heading."""
    out: List[str] = []
    # Sibling-prefix guard (mirrors cmd_fees_sum): the glob for account
    # 'margin' also matches 'margin_us_*.diag', so account margin's
    # DIAGNOSTICS banner absorbed the other account's errors and sent
    # the user auditing the wrong book. cache is always <root>/work, so
    # the config is one level up; on any load problem fall back to the
    # unfiltered glob (a false extra line beats a crash here).
    siblings: List[str] = []
    names: List[str] = []
    year: Optional[int] = None
    try:
        _cfg = load_config(cache.parent)
        accounts_cfg = (_cfg.get("accounts") or {})
        names = [str(a) for a in accounts_cfg]
        siblings = [a for a in accounts_cfg
                    if a != account and a.startswith(f"{account}_")]
        _y = settings_of(_cfg).year
        year = _y if isinstance(_y, int) else None
    except (Exception, SystemExit):
        pass
    blocks: List[List[str]] = []
    for diag in sorted(cache.glob(f"{account}_*.diag")):
        if any(diag.name.startswith(f"{s}_") for s in siblings):
            continue
        if not post_pass and diag.name.endswith(_POST_PASS_DIAG_SUFFIXES):
            continue
        if blended and diag.name == f"{account}_gains.json.diag":
            continue
        start = len(blocks)
        kept_prev = False
        for line in diag.read_text(errors="replace").splitlines():
            is_marker = bool(_DIAG_MARKER_RE.match(line.strip()))
            is_cont = kept_prev and line[:1].isspace() and bool(line.strip())
            if is_marker:
                # (the end-of-run mark is the run's alone: out.unmarked)
                blocks.append([unmarked(line.rstrip())])
                kept_prev = True
            elif is_cont:
                blocks[-1].append(line.rstrip())
            else:
                kept_prev = False
        if diag.name in (f"{account}_blend.diag",
                         f"{account}_cryptoblend.diag"):
            # The blended pass's mirror is the whole blend's stderr,
            # copied into every blended account's banner: a message
            # about another account's file names that account in front
            # (backlog TJ-0252). Nothing is dropped; a blend-wide one,
            # or one about this account's own files, stays as it is.
            for b in blocks[start:]:
                who = _diag_block_account(b, names)
                if who is not None and who != account:
                    b[0] = f"[{who}] {b[0]}"
    # The gains stage and the blend stage both persist the engine's
    # stderr, so every engine line reached a _wash.sum twice (audit
    # R1-181): a block (marker line + continuations) already kept is
    # not repeated.
    seen: set = set()
    _uniq: List[List[str]] = []
    for b in blocks:
        key = "\n".join(b)
        if key in seen:
            continue
        seen.add(key)
        _uniq.append(b)
    blocks = _uniq
    later: List[List[str]] = []
    if year is not None:
        # Past the year's end AND its 30-day window (a January rebuy
        # still denies a December loss).
        cutoff = f"{year + 1}-01-30"
        now: List[List[str]] = []
        for b in blocks:
            dates = ["-".join(m) for ln in b
                     for m in _DIAG_DATE_RE.findall(ln)]
            # Errors stay where they are, whatever their dates.
            is_err = re.search(r"\b(?:error|validation):", b[0], re.I)
            (later if dates and not is_err and min(dates) > cutoff
             else now).append(b)
        blocks = now
    for b in blocks:
        out.extend(b)
    if later:
        out.append(f"-- notes about events after {year} (they do not "
                   f"affect the {year} figures; act on them in that "
                   f"year's project):")
        for b in later:
            out.extend(b)
    return "\n".join(out)


def run_capture(cmd: List[str]) -> bytes:
    from taxjson.pipeline.dispatch import run_cmd
    result = run_cmd(cmd, capture_output=True)
    if result.stderr:
        _echo_stage_stderr(result.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd,
                                            stderr=result.stderr)
    return (result.stdout or "").encode("utf-8")


def _count_noun(n: int, one: str, many: Optional[str] = None) -> str:
    """`1 sale`, `2 sales` (`many` for an irregular plural)."""
    return f"{n} {one if n == 1 else (many or one + 's')}"
