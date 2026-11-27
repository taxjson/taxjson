"""What more than one group of commands reads: the books' files, periods and year checks."""
import argparse
import re
import sys
from datetime import date as date_cls
from pathlib import Path
from typing import (Any, Callable, Dict, List, NamedTuple, Optional, Set,
                    Tuple, Union)

from taxjson.project.accounts import account_of, accounts_of, estimate_of, sheltered_by_name
from taxjson.project.settings import Settings, settings_of
from taxjson.lib import project_layout as _PL
from taxjson.lib.account_list import (
    AccountListError, is_account_list, names_of, parse_accounts,
    refusal as account_list_refusal, unknown_accounts_text,
)
from taxjson.lib import out as _out
from taxjson.lib.project_layout import _GAINS_ALL_SUFFIX
from taxjson.lib.pipeline import income_dating_flags, option_timing_flags
from taxjson.model.result import GainRow
from taxjson.model import money as M
from taxjson.lib.report_fmt import fmt_money
from taxjson.lib.stage_msg import emit_line

from taxjson.pipeline import run_state
from taxjson.pipeline.config import (
    load_settings,
    _country, _income_rules, input_files, load_config,
)
from taxjson.pipeline.diagnostics import _count_noun
from taxjson.pipeline import stage_table as ST
from taxjson.pipeline.basis_label import (
    basis_label as _basis_label, _denials_checked, record_full_history_checks,
    _registered_book, _sheltered_flags,
)
from taxjson.pipeline.inputs import _base, _loss_override_flags, _tax_date
from taxjson.pipeline.messages import (
    _cmd_prog, _current_cmd, _die, _die_input, _out_relpath, _say,
    _say_once,
)
from taxjson.lib.json_input import advises_rerun, check_gains_parts, read_json_doc, read_rows_doc, rerun_advice
from taxjson.pipeline.run import _native_tx_file, _NATIVE_TX_SUFFIXES
from taxjson.pipeline.sanity import _has_inputs
from taxjson.pipeline.stages import (
    _locked_year_flags,
    _mh_prepare, _missing_history_arg, _read_work_doc,
    _SKIPPED_ACCOUNTS_FILE, _registered_flags, _wash_flags,
)
from taxjson.pipeline.tools import _cmd, run_to_file


def _self_cmd(root: Path, *args: str) -> List[str]:
    """`tjs -C ROOT ARGS` as a tool command for dispatch.run_cmd (a
    command relaying another one's --json: instalments and amt the
    estimate, close-year the carryover ledger, watch harvest)."""
    return _cmd("taxjson-run") + ["-C", str(root), *args]


def _exec_tool(cmd: List[str], cwd: Optional[str] = None) -> None:
    """Wrapper-command tail: run the tool (in-process, streaming live)
    and exit with its return code — replaces the historical
    `_exec_tool(cmd)`."""
    from taxjson.pipeline.dispatch import run_cmd
    raise SystemExit(run_cmd(cmd, cwd=cwd).returncode)


_AN_ERROR = re.compile(r"(?:^|: )error: ", re.I)


def _relay_child_stderr(text: str, handled: Tuple[str, ...] = (), *,
                        nested: bool = True) -> None:
    """Show a captured child's stderr (captured unwrapped, pipeline/dispatch)
    to the person, each line wrapped at the house width, flush-left
    (docs/output-style.md: whoever shows captured text wraps it), the label
    first and the child named as the source (`Warning: tjs estimate: ...`,
    lib/out.labelled). A headline's indented details only with --details,
    but an error's, which hold the fix, always. Captured (width 0) a line
    keeps its indent. `handled`: text of the messages the caller has already
    dealt with, left out whole (backlog TJ-1465). `nested`: the child is the
    last nested command, whose marks now count as said; False for any other
    child (backlog TJ-1539)."""
    from taxjson.lib.out import labelled, show, width, wrap
    if nested:
        run_state.relayed_nested()
    dropping = shown_headline = an_error = False
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        detail = ln[:1].isspace()
        if not detail:
            dropping = any(h in ln for h in handled)
            an_error = bool(_AN_ERROR.search(ln))
        if dropping:
            continue
        if detail and not (run_state.current_details or an_error):
            # Essentials first (docs/output-style.md).
            continue
        ln = labelled(ln, sys.stderr, source=True)
        lead = ln[:len(ln) - len(ln.lstrip())]
        hang = lead or "  "
        if width(sys.stderr) > 0:
            lead = hang = ""
        # A detail continues this relay's headline, never what the caller
        # said before it (backlog TJ-1532).
        show(wrap(ln.strip(), None, lead, hang, stream=sys.stderr),
             sys.stderr, cont=detail and shown_headline)
        shown_headline = shown_headline or not detail


def _child_error(stderr: Optional[str], limit: int = 400) -> str:
    """What a wrapper relays of a failed child's stderr: all of it when
    short, else its error line. A fixed-length PREFIX cut a traceback off
    before the exception line, so the actual error never showed (audit
    A2-1190). For a traceback: the lines before it plus its last line;
    for an error message: the message from its error line on (what
    failed, why, then the hint; a long project path once pushed the
    line naming the file out, backlog TJ-0625), a line too long for
    `limit` keeping its start and its end; otherwise the last lines,
    newest kept."""
    raw = [ln.rstrip() for ln in (stderr or "").strip().splitlines()
           if ln.strip()]
    if not raw:
        return "(no error output)"
    # The child's own label is not repeated under the wrapper's one
    # Error: line: neither a person's `Error: taxjson x: ` nor the
    # captured `taxjson x: error: ` a child run for its output writes
    # (backlog TJ-0633: shown as a second Error inside the first).
    # An Info/Warning line of the child's may come first (amt's
    # estimate: workflow audit 2026-10), so every line is stripped.
    lines = [_CHILD_LABEL.sub("", ln) for ln in raw]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    tb = next((i for i, ln in enumerate(lines)
               if ln.startswith("Traceback (most recent call last)")), None)
    err = next((i for i in range(len(raw) - 1, -1, -1)
                if _CHILD_ERROR_LINE.match(raw[i])), None)
    if tb is not None:
        keep = lines[:tb][-2:] + ["(traceback omitted) " + lines[-1]]
    elif err is not None:
        # The error line and what follows it (the message's other
        # lines, its hint), a dozen lines at most. A long line (a path)
        # keeps both ends: what failed is at its start, why at its end.
        half = limit // 2
        return "\n".join(k if len(k) <= limit
                         else k[:half] + " … " + k[-half:]
                         for k in lines[err:err + 12])
    else:
        keep = []
        for ln in reversed(lines):
            if keep and sum(len(k) for k in keep) + len(ln) > limit:
                break
            keep.insert(0, ln)
    return "\n".join(k if len(k) <= limit else k[:limit] + " …"
                     for k in keep)


# A child's error line, in the person's form (`Error: ...`) or the
# captured one (`tjs estimate: error: ...`, `error: ...`).
_CHILD_ERROR_LINE = re.compile(
    r"^(?:Error:|(?:(?:taxjson|tjs)(?:-\w+)?(?: \S+)?: )?(?:error|ERROR):)")
# The label of a child's error line, its program name with it, as
# _child_error drops it: `Error: [taxjson x: ]` or `[taxjson x: ]error: `.
_CHILD_LABEL = re.compile(
    r"^(?:Error:\s+(?:(?:taxjson|tjs)(?:-\w+)*(?: \S+)?:\s+)?"
    r"|(?:(?:taxjson|tjs)(?:-\w+)*(?: \S+)?: )?(?:error|ERROR):\s+)")


def _shared_inputs_note(root: Path, path: Path, prog: str) -> None:
    """A file this command wrote into the exports folder every year's
    project shares ([settings] inputs_dir): one note that it applies to
    every year, not only this one (lib/project_layout). Silent for a
    single-folder project and for a file outside that folder."""
    if not _PL.shared_inputs(root):
        return
    try:
        Path(path).resolve().relative_to(_PL.inputs_dir(root).resolve())
    except (ValueError, OSError):
        return
    _say_once(("shared-write", str(path)), "note",
              f"{_PL.shown(path, root)} is in the exports folder every "
              f"year's project shares: it applies to every year",
              "Each year's run reads it; `tjs run` in another year's "
              "folder shows what it changes there.",
              *_frozen_years_apart(root), prog=prog)


def _frozen_years_apart(root: Path, elections: bool = False) -> List[str]:
    """One line naming the filed years beside `root` that close-year
    froze (lib/project_layout.frozen_project): they read their own copy
    of the exports, so a file saved in the shared folder never reaches
    them (workflow audit 2026-10-22). With `elections`, the name each
    reads its elections under: manifest.json for a year frozen before
    v3.0.0 (lib/project_layout.elections_file), so a corrected copy is
    never put in beside it under the other name. [] when there is none."""
    try:
        frozen = [(y, d) for y, d in _PL.year_dirs(root.parent)
                  if _PL.frozen_project(d) and not _PL.shared_inputs(d)]
    except (OSError, _PL.LayoutError):
        return []
    if not frozen:
        return []
    yrs = [y for y, _d in frozen]
    old = [y for y, d in frozen if elections and _old_frozen_elections(d)]
    name = ""
    if old:
        name = (f" (the elections as {_PL.OLD_ELECTIONS}"
                + (f" in {', '.join(map(str, old))}" if len(old) < len(yrs)
                   else "")
                + f", the name {'it was' if len(old) == 1 else 'they were'}"
                f" filed under)")
    return [f"Not {', '.join(map(str, yrs))} (filed, inputs frozen): "
            f"{'it reads its' if len(yrs) == 1 else 'each reads its'} own "
            f"copy — to amend a filed year, put the corrected file in "
            f"its inputs/<account>/{name} and `tjs run` there."]


def _accounts_skipped_for_no_inputs(root: Path) -> set:
    """Accounts the last `tjs run` skipped because inputs/<name>/
    held no CSV or .tt file — and that STILL hold none (a user who has
    since added files does need the "run `tjs run` first" hint).
    Readers that warn about a missing work/<name>_* artifact should
    stay quiet for these: their absence is expected, not staleness."""
    import json as _json
    try:
        doc = _json.loads((root / "work" / _SKIPPED_ACCOUNTS_FILE)
                          .read_text(encoding="utf-8"))
        names = [str(n) for n in (doc.get("accounts") or [])]
    except (OSError, ValueError, AttributeError):
        return set()
    out = set()
    for n in names:
        d = _PL.inputs_dir(root) / n
        if not (input_files(d, ".csv") or input_files(d, ".tt")):
            out.add(n)
    return out


def _sheltered_expected(root: Path) -> bool:
    """Should work/sheltered_base.json exist? True when the config has
    a sheltered account that `tjs run` did not skip for having no
    inputs — the "no sheltered_base.json" notes are noise otherwise."""
    _acfg = accounts_of(load_config(root, missing_ok=True))
    skipped = _accounts_skipped_for_no_inputs(root)
    return any(c.sheltered and n not in skipped for n, c in _acfg.items())


def _raise_requirements(root: Path, features: Tuple[str, ...] = (),
                        all_years: bool = False,
                        dirs: Optional[List[Path]] = None,
                        quiet: bool = False) -> None:
    """Raise requires_taxjson (lib/requires.raise_requirement) in project
    `root` — with `all_years` in every year folder of its multi-year
    project, or in each of `dirs` — and say what was set in one line
    (not with `quiet`: --json)."""
    from taxjson.lib import requires as _REQ
    from taxjson.lib.missing_history import MISSING_HISTORY_FILES
    top = _PL.multi_root(root) if all_years or dirs else None
    if dirs is None:
        dirs = ([d for _y, d in _PL.year_dirs(top)] if all_years
                and top is not None else
                [root] if _PL.has_config(root) else [])
    base = top or root
    done: Dict[str, List[str]] = {}
    for d in dirs:
        feats = tuple(f for f in features
                      if (f != "missing_history_tt"
                          or not any(_os_lexists(d / n)
                                     for n in MISSING_HISTORY_FILES))
                      # The project's own: only where it reads an
                      # elections.json (never a year frozen with its
                      # manifest.json).
                      and (f != "elections_json"
                           or _PL.holds_elections(d)))
        try:
            new = _REQ.raise_requirement(d, feats)
        except (OSError, ValueError) as e:
            _die_input(f"cannot record the taxjson version "
                       f"{_PL.shown(d / _PL.CONFIG, base)} needs: {e}")
        if new is not None:
            done.setdefault(new, []).append(
                _PL.shown(d / _PL.CONFIG, base))
    for new, where in sorted(done.items()):
        if not quiet:
            _out.put(f"[settings] {_REQ.KEY} = \"{new}\" (the oldest "
                     f"taxjson this layout runs on) in {', '.join(where)}")


def _old_frozen_elections(d: Path) -> bool:
    """`d` is a filed year with frozen inputs whose elections close-year
    froze under the old name (inputs/<account>/manifest.json, no
    elections.json): it reads that copy as frozen, so the rename asks
    nothing of its taxjson.toml (lib/project_layout.elections_file)."""
    if not _PL.frozen_project(d):
        return False
    acct = _PL.inputs_dir(d)
    try:
        names = {p.name for p in acct.glob("*/*")}
    except OSError:
        return False
    return _PL.OLD_ELECTIONS in names and _PL.ELECTIONS not in names


def _os_lexists(p: Path) -> bool:
    import os as _os
    return _os.path.lexists(p)


def _listed_names(account: Any) -> Optional[Tuple[str, ...]]:
    """names_of(account) for a helper only a converted command hands a
    list (the advisories, the US-crypto rule, the leaps empty document,
    `among=`): a string holding a comma is the raw ACCOUNT text, which
    names_of reads as ONE name that matches nothing, so the helper would
    answer silently for no account. A programming error: stopped."""
    if is_account_list(account):
        raise ValueError("internal: pass the names from _accounts, not "
                         "the comma text")
    return names_of(account)


def _uncovered_sales(root: Path, cfg: Dict[str, Any],
                     account: Any = None, *,
                     missing_ok: bool = False) -> List[Dict[str, Any]]:
    """[{symbol, account, sales, proceeds, booked}] — the tax year's
    sales in taxable accounts with no purchase in the files that
    no .tt OPENING cost=unknown line covers (lib/first_run), with what the
    gains files did with them (`booked`, first_run.engine_booking:
    "open" — not in the totals —, "short_cover" or "matched"). []
    outside a project or when the books cannot be read (advisory).
    `account`: None | a name | names (lib/account_list.names_of; ()
    reads none); several: each named one with no book refused (exit 1)
    unless `missing_ok`; the comma text: ValueError (_listed_names)."""
    only = _listed_names(account)
    if not cfg:
        return []
    if only is not None and not only:
        return []
    _require_named_books(root, only, missing_ok)
    try:
        from taxjson.lib import first_run as FR
        from taxjson.lib.country import settings_tax_date
        from taxjson.lib.missing_history import walk_journal_symbols
        settings = settings_of(cfg)
        accounts = accounts_of(cfg)
        txs, _failed = FR.load_books(root / "work")
        if not txs:
            return []
        sheltered = sheltered_by_name(accounts)
        tm = _PL.ticker_map_path(root)
        journal = walk_journal_symbols(root / "work",
                                       tm if tm.is_file() else None)
        rows = FR.uncovered_short_sales(
            txs, settings.year, sheltered=sheltered,
            covered=FR.read_missing_history_pairs(root),
            date_basis=settings_tax_date(settings), journal=journal,
            country=settings.country or None, cache=root / "work")
        booked = FR.engine_booking(root / "work", rows, settings.year,
                                   date_basis=settings_tax_date(settings))
    except Exception:                               # noqa: BLE001
        return []
    return [{"symbol": r.candidate.symbol, "account": r.candidate.account,
             "sales": r.in_year_dispositions,
             "proceeds": r.in_year_proceeds,
             "booked": booked.get((r.candidate.symbol, r.candidate.account),
                                  FR.BOOKED_OPEN)}
            for r in rows if only is None or r.candidate.account in only]


def _unanswered_covers(root: Path, cfg: Dict[str, Any],
                       account: Any = None, *,
                       missing_ok: bool = False) -> List[Any]:
    """The buys the broker codes as closing with no short held that no
    .tt short line answers (lib/first_run.unanswered_covers). [] outside
    a project or when the books cannot be read (advisory). `account`:
    None | a name | names (lib/account_list.names_of; () reads none);
    several: each named one with no book refused (exit 1) unless
    `missing_ok`; the comma text: ValueError (_listed_names)."""
    only = _listed_names(account)
    if not cfg:
        return []
    if only is not None and not only:
        return []
    _require_named_books(root, only, missing_ok)
    try:
        from taxjson.lib import first_run as FR
        from taxjson.lib.country import settings_tax_date
        from taxjson.lib.missing_history import walk_journal_symbols
        settings = settings_of(cfg)
        accounts = accounts_of(cfg)
        txs, _failed = FR.load_books(root / "work")
        if not txs:
            return []
        sheltered = sheltered_by_name(accounts)
        tm = _PL.ticker_map_path(root)
        covers = FR.unanswered_covers(
            txs, settings.year, sheltered=sheltered,
            date_basis=settings_tax_date(settings),
            journal=walk_journal_symbols(root / "work",
                                         tm if tm.is_file() else None),
            country=settings.country or None, cache=root / "work")
    except Exception:                               # noqa: BLE001
        return []
    return [c for c in covers if only is None or c.account in only]


def _require_named_books(root: Path, names: Optional[Tuple[str, ...]],
                         missing_ok: bool = False) -> None:
    """For two names or more, refuse each with no book in work/ (the
    work/<account>_base.json first_run.load_books reads), exit 1: the
    advisories read every book and would leave its rows out unsaid."""
    if missing_ok or not names or len(names) < 2:
        return
    have = {p.name[:-len("_base.json")]
            for p in _PL.book_files(root / "work")}
    _require_listed(names, have, "books",
                    fix="Run `tjs run` first (an account with no inputs "
                        "has no books).")


def _tx_period_cutoff(period: str):
    """Oldest date to include for a look-back window: 30d / 6w / 3m / 1y,
    `mtd` / `ytd` (calendar month/year to date), or `all` for no lower bound
    (full history). Days and weeks are exact; months and years use calendar
    arithmetic. Year tokens (YYYY, tax_year) are NOT look-backs — the
    callers resolve them to that calendar year first (_period_keep,
    fees-sum); a lower-bound reading of them (the removed since-based
    commands' "through today") disagreed with every period command
    (S039-04). The window ends today (`_period_keep`: a row dated
    after today is not in it; backlog TJ-0329)."""
    import re
    import calendar
    from datetime import date, timedelta
    tok = (period or "").strip().lower()
    if tok in ("all", "max"):
        # date.min.isoformat() == '0001-01-01', so every real row's
        # `d >= cutoff` compare passes — the whole history is included.
        return date.min
    if tok == "mtd":
        today = date_cls.today()
        return date(today.year, today.month, 1)
    if tok == "ytd":
        return date(date_cls.today().year, 1, 1)
    m = re.fullmatch(r"\s*(\d+)\s*([dwmy])\s*", (period or "").lower())
    if not m:
        # A usage error: exit 2, as every command's (backlog TJ-0157).
        _die_input(f"invalid time period {period!r} "
                   f"(use e.g. 30d, 6w, 3m, 1y, mtd, ytd, all)")
    n, unit = int(m.group(1)), m.group(2)
    today = date_cls.today()
    # Magnitudes that walk past year 1 raised ValueError (and huge day
    # counts OverflowError) as raw tracebacks on EVERY period-aware
    # command — including the plausible typo "2026y" (REVIEW #32).
    # Anything reaching past all representable history means "all".
    _CAP_DAYS = (today - date.min).days
    if (unit == "d" and n >= _CAP_DAYS) or \
       (unit == "w" and n >= _CAP_DAYS // 7):
        return date.min
    if unit == "d":
        return today - timedelta(days=n)
    if unit == "w":
        return today - timedelta(weeks=n)
    months = n if unit == "m" else 12 * n
    total = today.year * 12 + (today.month - 1) - months
    if total < 12:
        # Before year 1 (the current month counts: `{year*12-1}m` in
        # October reached year 0).
        return date.min
    y, mo = divmod(total, 12)
    mo += 1
    return date(y, mo, min(today.day, calendar.monthrange(y, mo)[1]))


_TAX_YEAR_TOKENS = ("tax_year", "tax-year", "taxyear", "ty")

# A literal 4-digit tax year is a valid PERIOD token: `tjs events 2025`
# scopes to calendar year 2025 (same shape the -sum roll-ups label
# "tax year N").
_YEAR_TOKEN_RE = re.compile(r"(19|20)\d{2}")


def _year_keep(ys: str):
    """A tax-year window predicate, tagged `.tax_year` so the gains views
    can apply it on the project's tax_date basis (_gains_row_date)."""
    keep = (lambda d: d.startswith(ys))
    keep.tax_year = ys
    return keep


def _settle_basis(root: Path, doc: Optional[Dict[str, Any]] = None) -> bool:
    """True when dispositions belong to a tax year by SETTLEMENT date: the
    gains file's own summary.tax_date_basis when it says, else [settings]
    tax_date, else the country default (Canada settle, USA trade) — the
    same rule `sum`, form-export and the gains engine follow."""
    b = str(((doc or {}).get("summary") or {}).get("tax_date_basis")
            or "").strip().lower()
    if b in ("settle", "trade"):
        return b == "settle"
    settings = load_settings(root, missing_ok=True)
    td = str(settings.tax_date or "").strip().lower()
    if td in ("settle", "trade"):
        return td == "settle"
    return _tax_date(settings) == "settle"


def _gains_row_date(t: Dict[str, Any], keep, settle: bool) -> str:
    """The date a gains row is windowed on: its SETTLEMENT date for a
    tax-year window on a settle-basis project (a Dec-31 trade that
    settles in January is next year's disposition — the gains artifacts,
    `sum` and form-export all place it there), else the trade date. The
    trade-date window dropped such rows from every year's winners /
    ccd-sum / leaps / gains (audit R1-171, R1-186, R1-238, R1-273)."""
    if settle and getattr(keep, "tax_year", None):
        return str(t.get("date_settle") or t.get("date") or "")
    return str(t.get("date") or "")


def _period_end(period: str) -> Optional[str]:
    """The last day (ISO) a look-back or to-date window keeps: today
    for 30d / 6w / 3m / 1y / mtd / ytd, None (no upper bound) for
    `all`. A window from its cutoff with no end kept the rows dated
    after today — a trade entered ahead, a scheduled fee — in `mtd`,
    `ytd` and `30d` (backlog TJ-0329). Year tokens are not look-backs
    (`_period_keep` and fees-sum resolve them first)."""
    tok = (period or "").strip().lower()
    if tok in ("all", "max"):
        return None
    return date_cls.today().isoformat()


def _period_keep(period: str, root: Path):
    """Resolve a period token to `(keep(date_str) -> bool, scope_label)`.
    `tax_year` (or `ty`) binds the window to the config tax year; a literal
    YYYY is that calendar year; `all` is the whole history; anything else is
    a look-back window (30d/6w/3m/1y) or `mtd` / `ytd`, from its cutoff
    through today (`_period_end`)."""
    tok = (period or "").strip().lower()
    if _YEAR_TOKEN_RE.fullmatch(tok):
        return _year_keep(tok), f"tax year {tok}"
    if tok in _TAX_YEAR_TOKENS:
        year = load_settings(root, missing_ok=True).year
        if not year:
            _die("'tax_year' needs [settings] year in taxjson.toml "
                     "(or give an explicit window like 1y).")
        ys = str(year)
        return _year_keep(ys), f"tax year {year}"
    cutoff = _tx_period_cutoff(period).isoformat()      # handles all/max + errors
    end = _period_end(period)
    if tok in ("all", "max"):
        label = "all history"
    elif tok in ("mtd", "ytd"):
        label = f"{tok.upper()} (since {cutoff})"
    else:
        label = f"last {period}"
    if end is None:
        return (lambda d: d >= cutoff), label
    # The date part: a stamped `2026-10-27T…` is still today's.
    return (lambda d: d >= cutoff and d[:10] <= end), label


def _discover_tx_accounts(cache: Path) -> List[str]:
    # The same suffixes the single-account form reads (_native_tx_file):
    # after a failed fill-crypto stage only <acct>_sorted.json exists,
    # and the all-accounts views dropped the account silently (S039-07).
    names = set()
    for suf in _NATIVE_TX_SUFFIXES:
        for p in cache.glob(f"*{suf}"):
            # pathlib's `*` matches leading dots — keep dot-prefixed
            # pipeline intermediates from masquerading as accounts.
            if not p.name.startswith("."):
                names.add(p.name[: -len(suf)])
    return sorted(names)


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _trade_total(tx: dict) -> float:
    """A trade row's money for the buy/sell totals, in the engine's pool
    terms (engine _ca_trade_money): a BUY's cost as a magnitude (parsers
    spell it either sign), a SELL's proceeds SIGNED — a penny close whose
    commission exceeds the gross really has negative proceeds, and abs()
    added it to TOTAL SELL (audit S039-11)."""
    net = float(tx.get("net_amount") or 0.0)
    return net if float(tx.get("quantity") or 0.0) < 0 else abs(net)


def _vprint(text: str = "", indent: str = "", hang: Optional[str] = None
            ) -> None:
    """A query view's prose line, wrapped at the house width
    (docs/output-style.md; unwrapped when captured for a program)."""
    from taxjson.lib.out import fill
    # A live exact Doc prints the line as the raw print() did.
    d = _out.Doc(exact=True, width_=0, live=True)
    d.line(fill(text, indent=indent, hang=hang) if text else "")


def _view_msg(kind: str, text: str, *details: str) -> None:
    """A query view's diagnostic on stderr in the house style
    (docs/output-style.md): `tjs <cmd>: <kind>: <headline>`, then
    each detail indented. `kind`: note | warning."""
    from taxjson.lib import out
    out.emit(kind, text, prog=_cmd_prog(), details=details)


def _capitalized_pil_shares(root: Path, accounts, usa: bool) -> Dict[Tuple[str, str], float]:
    """{(account, row id): the share of a dividend-compensation row
    the US engine put into a cover's cost (§263(h); US-INC-04)} from the
    gains files, for the views that read the native rows (backlog
    TJ-1066). A share: the native row and the gains file can differ in
    currency. A US project's (`usa`: the view's income rules' country,
    lib/country `is_usa`; a Canadian run writes none) and `accounts`
    only, the view's (backlog TJ-2989, TJ-2990, TJ-3001, TJ-3004). A
    file that is not a gains file's shape, or whose payments in lieu the
    row checks refuse, stops the view (exit 2), as `_load_json_or_die`
    does: the share would be left in the figures (TJ-2988, TJ-2997).
    The share is lib/income_summary's (TJ-2993)."""
    from taxjson.lib.income_summary import capitalized_pil_shares
    from taxjson.lib.report_model import resolve_gains_files
    out: Dict[Tuple[str, str], float] = {}
    want = set(accounts)
    if not want or not usa:
        return out
    for acct, p in resolve_gains_files(root / "work", warn_stale=False).items():
        if acct not in want:
            continue
        try:
            doc = read_json_doc(Path(p))
            check_gains_parts(doc, Path(p))
            shares = capitalized_pil_shares(doc.get("transactions") or [], p)
        except (OSError, ValueError) as e:
            why = f"could not read {_work_read_why(Path(p), e)}"
            _die_input(why, _rerun_hint(why, "The view would show its payments in lieu whole."))
        out.update({(acct, k): v for k, v in shares.items()})
    return out


_CAPITALIZED_LBL = "CAPITALIZED (§263(h), in the cover's cost):"


def _require_books(root: Path, account: Any = None,
                   also: Tuple[str, ...] = ()) -> None:
    """Refuse a books view over a project with no books: never run
    (no work/), a run that died before any book was written, or an
    account with no book. spinoffs, splits, transfers, edge-cases and
    fees-sum printed an empty report ('No spin-offs', nine 'None.'
    sections) at rc 0 where their twins say 'run `tjs run` first'
    (re-audit A2-0797, A2-1395). `account`: None | a name | names
    (lib/account_list.names_of), each named one with no book refused
    (exit 1); () names none, nothing to require. A string is one name,
    never split: a command not converted passes its text whole, read as
    before lists."""
    cache = root / "work"
    if not cache.is_dir():
        _die("no work/ — run `tjs run` first.")
    asked = names_of(account)
    if asked is not None and not asked:
        return
    names = set(_discover_tx_accounts(cache)) | {
        p.name[:-len("_base.json")] for p in cache.glob("*_base.json")
        if not p.name.startswith(".")
        and not p.name.endswith("_raw_base.json")
        and p.name != "sheltered_base.json"}
    # `also`: the stage files a view reads besides the books (parsed
    # per-broker JSONs for fees-sum, transfer sidecars for transfers).
    extra = any(not p.name.startswith(".")
                for g in also for p in cache.glob(g))
    if asked and len(asked) == 1:
        if asked[0] not in names:
            _die(f"no books for account {asked[0]!r} in work/ — run "
                 f"`tjs run` first (an account with no inputs has no "
                 f"books).")
    elif asked:
        _require_listed(asked, names, "books",
                        fix="Run `tjs run` first (an account with no "
                            "inputs has no books).")
    elif not names and not extra:
        _die("no books in work/ — run `tjs run` first (or the last "
             "run stopped before writing any; see its error).")


# A LEAPS position: a LONG option BUY placed with more than
# `[settings] leaps_months` calendar months left to expiry (default 9,
# the market convention; a views-only setting — no tax figure uses it).
# The contract set is identified over FULL history (not the viewing
# window), so a sale, assignment, or expiry inside the window still
# shows even when the qualifying buy happened before it.
LEAPS_MONTHS_DEFAULT = 9


def _leaps_months(root: Path) -> int:
    """The project's LEAPS cut-off in months ([settings] leaps_months)."""
    cfg = load_config(root) if _PL.has_config(root) else {}
    v = settings_of(cfg).leaps_months
    return int(v) if v is not None else LEAPS_MONTHS_DEFAULT


def _leaps_rule(root: Path) -> str:
    """The definition the LEAPS views print."""
    return f"long option buys placed >{_leaps_months(root)} months to expiry"


def _add_months(iso_date: str, months: int) -> str:
    """ISO date `months` calendar months later, clamping the day into the
    target month (Jan 31 + 1mo -> Feb 28/29)."""
    import calendar
    from datetime import date as _date
    y, m, d = (int(x) for x in iso_date.split("-"))
    m += months
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return _date(y, m, min(d, calendar.monthrange(y, m)[1])).isoformat()


def _gains_artifact_scope(files, period_token,
                          root: Optional[Path] = None
                          ) -> Tuple[Set[str], bool]:
    """(the tax years the gains files hold, whether the requested
    window lies inside them). The canonical <acct>_gains[_wash].json
    artifacts contain ONLY the config tax year's dispositions, but the
    PERIOD grammar accepts any window — `winners all` / `ccd-sum 2024`
    printed definite-looking answers silently missing every
    out-of-year row (2026-09 audit); _window_gains_files reads the
    full-history books for such a window. [settings] year differing
    from the books' year dies here (S048-14)."""
    tok = str(period_token or "").strip().lower()
    years = set()
    for p in (files or {}).values():
        try:
            y = str((_read_work_doc(p).get("summary") or {})
                    .get("year") or "")
        except (OSError, ValueError):
            continue
        if y and y != "all":
            years.add(y)
    if not years:
        return years, True
    if (tok in _TAX_YEAR_TOKENS or tok == "") and root is not None:
        # The default / tax_year window IS [settings].year: artifacts
        # built for another year cannot answer it, and "No realized
        # dispositions in tax year 2025" from a 2026 build is a false
        # statement, not an empty result (audit S048-14).
        cfg_year = str(load_settings(root, missing_ok=True).year or "")
        if cfg_year and cfg_year not in years:
            _die(f"work/ holds the gains of tax year "
                 f"{', '.join(sorted(years))} but [settings] year is "
                 f"{cfg_year}",
                 f"Run `tjs run` to rebuild for {cfg_year} (or pass "
                 f"the year the books cover).")
    in_scope = (tok in _TAX_YEAR_TOKENS or tok == ""
                or (tok.isdigit() and tok in years))
    return years, in_scope


def _warn_gains_artifact_scope(files, period_token,
                               root: Optional[Path] = None) -> None:
    """The warning for a window outside the tax-year gains files when
    no full-history file can be built (_window_gains_files)."""
    years, in_scope = _gains_artifact_scope(files, period_token, root)
    if not in_scope:
        _view_msg("warning", f"the computed gains cover tax year "
                  f"{', '.join(sorted(years))} only — rows outside it are "
                  f"NOT in this report",
                  f"The requested window '{period_token}' may exceed that; "
                  f"`tjs gains` reads the full-history native books.")


def _gains_all_file(root: Path, cache: Path, account: str,
                    settings: Settings) -> Optional[Path]:
    """work/<account>_gains_all.json: every disposition the account's
    books hold, from the SAME consolidated base-currency book the tax
    year's gains are computed from (<account>_base.json: ticker.map's
    TOBASE pooling, per-transaction FX) through the gains engine with
    no --year and the flags stage_account's gains stage passes, and the
    registered accounts' book the run's pass reads (_registered_book), so
    the rows inside the tax year equal the filing file's, denials
    included. Built for a windowed view whose window reaches outside the
    tax year (_window_gains_files), every time a command reads it: once
    per command (`_built_this_command`), never from a record's word
    (TJ-4307). An account the run's cross-account
    pass blended with others is computed with them (_gains_all_blend):
    its cost pooled across them as the tax year's (TJ-0677). The
    engine's superficial-loss / wash-sale denials are in it, as the tax
    year's: across those taxable accounts, and a registered account's
    purchase in every year (TJ-5347). What the build read is recorded with
    the file (pipeline/basis_label `record_full_history_checks`): the
    basis label's word for it.
    None without a base book."""
    base_json = cache / f"{account}_base.json"
    if not base_json.is_file():
        return None
    out = cache / f"{account}{_GAINS_ALL_SUFFIX}"
    if _built_this_command(out):
        return out
    acfg = account_of(accounts_of(load_config(root, missing_ok=True)), account)
    country = _country(settings)
    is_crypto = bool(acfg.crypto)
    is_taxable = acfg.taxable
    if is_taxable:
        from taxjson.lib.report_model import wash_blend
        blend = wash_blend(cache, account)
        if blend is not None:
            return _gains_all_blend(root, cache, account, blend[0],
                                    blend[1], settings)
    shel = _registered_book(cache, country, taxable=is_taxable, crypto=is_crypto)
    cmd = _cmd("taxjson-gains") + ["--country", country,
                                  "--tax-date", _tax_date(settings)]
    cmd += _wash_flags(is_taxable, is_crypto, country) + _registered_flags(is_taxable)
    cmd += _sheltered_flags(shel)
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    cmd += _locked_year_flags(root, settings)
    cmd += _loss_override_flags(cache)
    if is_crypto:
        cmd.append("--spot-crypto")
    mh = _missing_history_arg(root)
    if mh is not None:
        cmd += ["--incomplete-history", str(mh)]
    cmd.append(str(base_json))
    # The stage table's gains_all row, built every time; vouched for as it is
    # built (`at_once`: a view command completes no run), as the sweep reads
    # the record of a removed account's file (`account_files`, `_vouched`).
    # The registered book is among its inputs (`sheltered`).
    _built_now(ST.execute(_ON_DEMAND, ST.StageCtx(
        work=cache, settings=settings, account=account, crypto=is_crypto,
        mh=mh, sheltered=shel, root_dir=root, own=cmd), "gains_all", at_once=True))
    record_full_history_checks(root, cache, [account],
                                shel if _sheltered_flags(shel) else None, country)
    return out


def _gains_all_blend(root: Path, cache: Path, account: str, tag: str,
                     members: List[str], settings: Settings
                     ) -> Optional[Path]:
    """work/<account>_gains_all.json for an account of the run's
    cross-account pass (report_model.wash_blend: `tag`, `members`): ONE
    gains run with no --year over the members' merged base books, with
    the flags stage_blended_wash_pass passes and the registered accounts'
    book it reads (_registered_book: none for a US crypto blend, which
    carries no rule; TJ-5347), split back per account. Built per account
    alone, an account's full history lost the cost pooled with the
    others' purchases (ITA s. 47), so `stats all` gave a sale another
    gain than the tax year (backlog TJ-0677). None when a member has no
    base book."""
    acfgs = load_config(root, missing_ok=True).get("accounts") or {}
    # taxjson.toml order, as the run's blended pass merges them
    # (CA-DATE-14 / US-DATE-13).
    names = ([n for n in acfgs if n in members]
             + [n for n in members if n not in acfgs])
    country = _country(settings)
    shel = _registered_book(cache, country, taxable=True, crypto=tag != "blend")
    ctx = ST.StageCtx(
        work=cache, settings=settings, tag=tag, members=names,
        crypto_members=frozenset(names if tag != "blend" else ()), sheltered=shel,
        root_dir=root)
    if not all(b.is_file() for b in ST.member_books(ST.Inst("gains_all_blend", ctx))):
        return None
    _us = country in ("us", "usa")
    combined_base = cache / f".{tag}_all_base.json"
    cmd = _cmd("taxjson-gains") + ["--taxable", "--country", country,
                                  "--tax-date", _tax_date(settings)]
    if _us:
        cmd.append("--per-account-basis")
        if tag != "blend":
            # A US crypto blend: lots carried between the accounts, no
            # wash-sale rule (stage_blended_wash_pass no_wash).
            cmd += ["--no-wash", "--spot-crypto"]
    cmd += _sheltered_flags(shel)
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    cmd += _locked_year_flags(root, settings)
    cmd += _loss_override_flags(cache)
    mh = _missing_history_arg(root)
    if mh is not None:
        cmd += ["--incomplete-history", str(mh)]
    cmd.append(str(combined_base))
    # The stage table's gains_all_blend row, then gains_all_split's per
    # member, each built every time and vouched for as it is built: every
    # member's file, so the command's read of another member takes it
    # (`_built_this_command`).
    ctx.mh, ctx.own = mh, _AllBlend(cmd, combined_base)
    _built_now(ST.execute(_ON_DEMAND, ctx, "gains_all_blend", "gains_all_split",
                          at_once=True))
    record_full_history_checks(root, cache, names,
                                shel if _sheltered_flags(shel) else None, country)
    return cache / f"{account}{_GAINS_ALL_SUFFIX}"


def _built_this_command(out: Path) -> bool:
    """Whether this command built `out` already (`_built_now`): a view reads
    an account's full-history file more than once (`stats` over the pool
    covers, `winners` and `leaps-sum` with the grant expiries, each member of
    a blend), and builds it once. A record's word never serves one: each
    command builds what it reads (TJ-4307)."""
    return str(Path(out).absolute()) in run_state.current.views_built


def _built_now(ran: List[ST.Inst]) -> None:
    run_state.current.views_built.update(str(ST.output(i).absolute()) for i in ran)


# ---------------------------------------------------------------- the on-demand stage steps
# The steps of the stage table's full-history rows, built by the windowed
# views (_window_gains_files): each its row's build, the missing history's
# stamp first (stages._mh_prepare).

class _AllBlend(NamedTuple):
    """What gains_all_blend's build reads beyond its StageCtx."""
    cmd: List[str]          # the gains run over the members' merged books
    merged: Path            # work/.<tag>_all_base.json


def _gains_all_build(i: ST.Inst, _r: ST.Ready) -> None:
    run_to_file(i.ctx.own, ST.output(i), capture_diag=False)


def _gains_all_blend_build(i: ST.Inst, _r: ST.Ready) -> None:
    # The row's books (BOOK, in the members' taxjson.toml order:
    # ST.member_books), not its inputs: those hold taxjson.toml, the
    # loss overrides, the locks and the missing history too.
    own, books = i.ctx.own, ST.member_books(i)
    _check_books_before_merge(books)
    run_to_file(_cmd("taxjson-merge") + [str(b) for b in books], own.merged)
    run_to_file(own.cmd, ST.output(i), capture_diag=False)


def _gains_all_split_build(i: ST.Inst, _r: ST.Ready) -> None:
    c = i.ctx
    run_to_file(_cmd("taxjson-split-gains") + [
        str(ST.output(ST.Inst("gains_all_blend", c))), "--account", i.account,
        "--base", str(c.work / f"{i.account}_base.json")],
        ST.output(i), capture_diag=False)


# The stage table with these steps bound: what ST.execute runs for a view.
_ON_DEMAND = ST.bound({
    "gains_all": ST.Steps(_gains_all_build, _mh_prepare),
    "gains_all_blend": ST.Steps(_gains_all_blend_build, _mh_prepare),
    "gains_all_split": ST.Steps(_gains_all_split_build),
})


def _gains_files_of(cache: Path, account: Any = None, *,
                    missing_ok: bool = False) -> Dict[str, Path]:
    """resolve_gains_files for None (every account) | a name | names
    (lib/account_list.names_of; () reads none): one name as it always
    was read, several through `accounts=` ({account: path}, sorted by
    account), each named one with no gains file refused, naming it
    (exit 1), unless `missing_ok` (a command that unions over accounts
    some of which may have none): then left out. A string is one name,
    never split, as `_window_gains_files` and `_require_books` read it:
    a command not converted passes its text whole (`tjs stats 1y,alpha
    --all-history` is refused as before lists); a converted one passes
    the names (`_accounts`)."""
    from taxjson.lib.report_model import resolve_gains_files
    names = names_of(account)
    if names is None:
        return resolve_gains_files(cache, None)
    if not names:
        return {}
    if len(names) == 1:
        return resolve_gains_files(cache, names[0])
    files = resolve_gains_files(cache, None, accounts=names)
    if not missing_ok:
        _require_listed(names, files, "gains file",
                        fix="Run `tjs run` first, or check the names (an "
                            "account with no inputs has no books).")
    return files


def _window_gains_files(root: Path, cache: Path, account: Any,
                        args: argparse.Namespace, *,
                        missing_ok: bool = False
                        ) -> Tuple[Dict[str, Path], bool]:
    """The gains files a windowed view (stats, win-rates, winners,
    losers, ccd-sum, leaps, leaps-sum) reads: the tax year's filing
    files (resolve_gains_files: wash-adjusted when the cross-account
    pass ran) for a window inside the tax year; for one reaching
    outside it (`all`, `2y`, another year, --all-history) each
    account's full-history file (_gains_all_file), said in a note: what
    its denials are checked against (_denials_checked). Second element:
    whether the full-history files were read. An
    account with no base book to build from keeps its tax-year file,
    with the old warning. `account`: None | a name | names, and
    `missing_ok`: _gains_files_of's (a string is one name, never
    split)."""
    resolved = _gains_files_of(cache, account, missing_ok=missing_ok)
    tok = _period_token(args)
    years, in_scope = _gains_artifact_scope(resolved, tok, root)
    if in_scope or not resolved:
        return resolved, False
    settings = load_settings(root, missing_ok=True)
    out: Dict[str, Path] = {}
    for acct in resolved:
        p = _gains_all_file(root, cache, acct, settings)
        if p is None:
            _warn_gains_artifact_scope(resolved, tok, root)
            return resolved, False
        out[acct] = p
    acfgs, country = accounts_of(load_config(root, missing_ok=True)), _country(settings)
    books = {a: _registered_book(cache, country, taxable=account_of(acfgs, a).taxable,
                                 crypto=bool(account_of(acfgs, a).crypto)) for a in out}
    _view_msg("note", f"the window reaches outside tax year "
              f"{', '.join(sorted(years))}: read from the full-history "
              f"books",
              "Consolidated, in the base currency and across your "
              "taxable accounts as the tax year's gains are; "
              + _denials_checked(books) + " A view, not a filing figure.")
    return out, True


def _leaps_scope_guard(root: Path, account: Any,
                       args, *, missing_ok: bool = False) -> Dict[str, Path]:
    """leaps/leaps-sum read the same year-scoped gains artifacts as
    ccd-sum and winners, so they resolve them the same way: the
    full-history files for a window outside the tax year (`leaps-sum
    all` labelled 'all history' while holding one year, audit
    S048-11). The files _leaps_closed reads. `missing_ok`:
    _gains_files_of's."""
    files, _all_history = _window_gains_files(root, root / "work", account,
                                              args, missing_ok=missing_ok)
    return files


def _leaps_accounts(cache: Path, account: Any, missing_ok: bool = False
                    ) -> Tuple[Optional[Tuple[str, ...]], Optional[List[str]]]:
    """(the names given, the accounts _leaps_contracts reads: None when
    () is named, none), each named account of a list with no gains file
    refused first (exit 1) unless `missing_ok`."""
    named = names_of(account)
    if named is not None and not named:
        return named, None
    if named is not None and len(named) > 1:
        _gains_files_of(cache, named, missing_ok=missing_ok)
    return named, (list(named) if named else _discover_tx_accounts(cache))


def _leaps_contracts(root: Path, account: Any,
                     label: str, *, missing_ok: bool = False
                     ) -> Dict[str, float]:
    """{option_symbol: full-history net open qty} for contracts with at
    least one qualifying LEAPS entry buy in the native books. Membership
    (`sym in result`) identifies the contract set; the value gives the
    TRUE still-open quantity regardless of any viewing window.
    `account`: None | a name | names (lib/account_list.names_of; ()
    reads none); `missing_ok`: _gains_files_of's."""
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_expiry
    months = _leaps_months(root)
    cache = root / "work"
    named, accounts = _leaps_accounts(cache, account, missing_ok)
    if accounts is None:
        return {}
    if not any(_native_tx_file(cache, a) is not None for a in accounts) \
            and not _gains_files_of(cache, named, missing_ok=True):
        # "No LEAPS contracts found" (exit 0) before any run read as a
        # verdict about the books (2026-09 CLI audit B22); a list: exit 1.
        (_die if named and len(named) > 1 else _die_input)(
            "no gains files in work/" + _accounts_shown(named)
            + " — run `tjs run` first", "LEAPS come from the built books.")
    # An account with option gains but no native book: its LEAPS
    # entries are unknowable, and the report silently left them out with rc 0
    # (audit A2-0117; S040-00 covered an unreadable book only).
    _no_native = sorted(
        a for a, gf in _gains_files_of(cache, named, missing_ok=True).items()
        if _native_tx_file(cache, a) is None
        and any(is_option_symbol(str(t.get("symbol") or ""))
                for t in (_load_json_or_die(gf).get("transactions")
                          or []) if isinstance(t, dict)))
    if _no_native:
        _die(f"no native transaction file (work/<account>_raw.json) for "
             f"account(s) {', '.join(_no_native)}, which have gains",
             "Rerun `tjs run` (this report would otherwise leave "
             "their LEAPS out).")
    leaps: set = set()
    qty_by_symbol: Dict[str, float] = {}
    # The native books carry the broker's listing (pre-TOBASE: SAMPMN...US)
    # while the gains files carry the ticker.map spelling (SAMPMN...TO);
    # without the rename a genuine LEAPS never matched a gains row and
    # vanished from both views (R1-237).
    _renames: Dict[str, str] = {}
    _tm = _PL.ticker_map_path(root)
    if _tm.exists():
        # Read as `run` reads it: an unreadable map stops the report —
        # treated as empty, a TOBASE-renamed LEAPS vanished with rc 0
        # (audit A2-0329, A2-1126).
        try:
            from taxjson.lib.ticker_map_rules import merge_renames
            from taxjson.lib.map_file import load_map_file
            _renames = merge_renames(load_map_file(_tm), True)
        except Exception as e:                          # noqa: BLE001
            _die(f"could not read ticker.map: {e}",
                 "Fix it (`tjs run` refuses it too); the LEAPS report "
                 "would otherwise miss the renamed contracts.")
    from taxjson.lib.ticker_map_rules import map_symbol as _map_sym
    from taxjson.lib.corporate_timeline import dedupe_split_events

    def _is_opt_split(tx) -> bool:
        return (isinstance(tx, dict) and tx.get("action") == "SPLIT"
                and is_option_symbol(str(tx.get("symbol") or "")))
    _native_rows: Dict[str, Optional[list]] = {}

    def _rows_of(a: str) -> Optional[list]:
        if a not in _native_rows:
            nf = _native_tx_file(cache, a)
            _native_rows[a] = (None if nf is None else
                               [t for t in (_load_json_or_die(nf)
                                            .get("transactions") or [])
                                if isinstance(t, dict)])
        return _native_rows[a]
    # A split or rename of a series is the security's: one account of a
    # Canadian project's blended taxable pool reports it, and the gains
    # carry it into the others' contracts. Each account replays its
    # pool's option splits (one event reported twice is one), so an
    # account filter no longer loses the renamed LEAPS (backlog
    # TJ-0907).
    pool_of = _split_pool_of(root)
    _pool_splits: Dict[str, list] = {}
    _pools = {pool_of(a) for a in accounts}
    for _a in sorted(set(_discover_tx_accounts(cache)) | set(accounts)):
        if pool_of(_a) not in _pools:
            continue                 # (another pool's book is not read)
        _pool_splits.setdefault(pool_of(_a), []).extend(
            t for t in (_rows_of(_a) or []) if _is_opt_split(t))
    for acct in accounts:
        own = _rows_of(acct)
        if own is None:
            continue
        # Per-ACCOUNT running balance, rows in date order: the old
        # single cross-account dict, iterated in account-name order,
        # made "buy against a short = buy-to-close" depend on the
        # ALPHABETICAL position of the account holding the short —
        # renaming an account changed the LEAPS report (2026-09
        # audit). The open quantity too: a split applies to each
        # account's own contracts.
        acct_bal: Dict[str, float] = {}
        acct_qty: Dict[str, float] = {}
        # A contract held before the data (a .tt OPENING cost=unknown
        # line) is the position its first sale closes, as the gains
        # stage and option-yield read it: without it the sale read as
        # a write and a later real entry as its buy-back (backlog
        # TJ-0777).
        rows = sorted([t for t in own if not _is_opt_split(t)]
                      + dedupe_split_events(
                          _pool_splits.get(pool_of(acct), []))
                      + _option_openings(root, own, native=True),
                      key=lambda t: (str(t.get("date") or ""),
                                     0 if t.get("action") == "OPENING_BALANCE"
                                     else 1,
                                     str(t.get("time") or ""),
                                     0 if t.get("action") == "SPLIT" else 1))
        for tx in rows:
            if tx.get("action") == "SPLIT":
                # A rename-SPLIT of a contract (an OCC adjustment) carries
                # its lots — and its LEAPS entry — to the new symbol, as
                # the engine carries them; the renamed LEAPS vanished
                # from leaps/leaps-sum (S040-02).
                _old = tx.get("symbol") or ""
                _new = str(tx.get("symbol_new") or "").strip()
                if (is_option_symbol(_old) and _new
                        and _new.upper() != _old.upper()):
                    _r = float(tx.get("quantity") or 1.0) or 1.0
                    acct_bal[_new] = (acct_bal.get(_new, 0.0)
                                      + acct_bal.pop(_old, 0.0) * _r)
                    _mo = _map_sym(_old, _renames) if _renames else _old
                    _mn = _map_sym(_new, _renames) if _renames else _new
                    acct_qty[_mn] = (acct_qty.get(_mn, 0.0)
                                     + acct_qty.pop(_mo, 0.0) * _r)
                    if _mo in leaps:
                        leaps.add(_mn)
                continue
            if tx.get("action") not in ("BUYSELL", "ASSIGN",
                                        "OPENING_BALANCE"):
                continue
            sym = tx.get("symbol") or ""
            if not is_option_symbol(sym):
                continue
            qty = float(tx.get("quantity") or 0.0)
            prev_bal = acct_bal.get(sym, 0.0)
            acct_bal[sym] = prev_bal + qty
            msym = _map_sym(sym, _renames) if _renames else sym
            acct_qty[msym] = acct_qty.get(msym, 0.0) + qty
            if msym in leaps or tx.get("action") != "BUYSELL" or qty <= 0:
                continue                       # entry must be a LONG buy
            if prev_bal < -1e-9:
                # A BUY against THIS ACCOUNT'S short position is a
                # buy-to-CLOSE (covered-call exit), not a LEAPS entry —
                # it was double-counting the same gain in both ccd-sum
                # and leaps-sum (FUZZ #L). Only the closing portion is
                # excluded: a buy that covers AND opens long can still
                # qualify via its opening remainder.
                if qty <= -prev_bal + 1e-9:
                    continue
            expiry = parse_option_expiry(sym)
            d = tx.get("date") or ""
            if not expiry or not _ISO_DATE_RE.match(d):
                continue
            if expiry > _add_months(d, months):
                leaps.add(msym)
        for s, q in acct_qty.items():
            qty_by_symbol[s] = qty_by_symbol.get(s, 0.0) + q
    return {sym: qty_by_symbol.get(sym, 0.0) for sym in leaps}


def _scope_split(root: Path, pairs) -> Dict[str, float]:
    """{'taxable': x, 'sheltered': y} over (account, gain) pairs, by
    the config's account types (an unknown account counts as taxable,
    the side that must never be understated). The cross-account views
    added registered-account P&L into one headline total with no
    account column (R1-182)."""
    _acfg = accounts_of(load_config(root, missing_ok=True))
    out = {"taxable": 0.0, "sheltered": 0.0}
    for acct, gain in pairs:
        scope = "sheltered" if account_of(_acfg, acct).sheltered else "taxable"
        out[scope] += float(gain or 0.0)
    return {k: M.shown(v, 2) for k, v in out.items()}


def _print_scope_split(split: Dict[str, float], base_cur: str,
                       root: Optional[Path] = None) -> None:
    if abs(split.get("sheltered", 0.0)) < 0.005:
        return
    from taxjson.lib.out import kv_lines
    money = fmt_money
    # An IRA is not "registered" (a Canadian term; audit A2-1324).
    _kind = ("retirement (IRA)" if root is not None
             and _country(load_settings(root, missing_ok=True)) == "usa" else "registered")
    # A live exact Doc prints each line as the raw print() did.
    d = _out.Doc(exact=True, width_=0, live=True)
    for ln in kv_lines([
            ("TAXABLE accounts", f"{money(split['taxable'])} {base_cur}"),
            ("SHELTERED accounts", f"{money(split['sheltered'])} "
             f"{base_cur} ({_kind} — not taxable events; the return uses "
             f"the taxable figure)")], indent="  "):
        d.line(ln)


def _leaps_empty_doc(root: Path, account: Any,
                     basis: Optional[str] = None, *,
                     files: Optional[Dict[str, Path]] = None,
                     missing_ok: bool = False) -> Dict[str, Any]:
    """Empty-state leaps JSON with the SAME key set as the populated
    document — machine consumers keying on `basis` broke only on the
    empty case; the taxable / sheltered totals were missing from it
    (backlog TJ-0541). `basis` None: the label of `files`, the files
    the view's window resolved (_leaps_scope_guard: the full-history
    ones for a window outside the tax year, TJ-5474), else of the tax
    year's. `account`: None | a name | names (the comma text:
    ValueError, _listed_names), and `missing_ok`: _gains_files_of's."""
    account = _listed_names(account)
    if basis is None:
        resolved = (files if files else _gains_files_of(root / "work", account,
                                                        missing_ok=missing_ok))
        basis = _basis_label(root, resolved) if resolved else "pre-wash"
    return {"rows": [], "total_gain": 0.0, "taxable_gain": 0.0,
            "sheltered_gain": 0.0,
            "currency": _base_currency(root), "basis": basis}


def _leaps_closed(root: Path, account: Any, leaps,
                  keep, files: Optional[Dict[str, Path]] = None, *,
                  missing_ok: bool = False
                  ) -> Tuple[list, bool, str]:
    """[(account, gains_entry)] for in-window dispositions of LEAPS
    contracts, from the wash-adjusted gains files (falling back to the
    plain gains files) — the lot-matched, superficial-loss-adjusted,
    BASE-currency numbers a return reports. Tainted (unknown-cost)
    dispositions are excluded, mirroring every filing-facing consumer.
    Second element: whether any gains file was found at all; third the
    resolved files' basis label (_basis_label: "wash-adjusted" only when
    a rule checked the sales completely — the resolver silently falls
    back to the pre-wash gains, and the printed claim must follow it). `account`:
    None | a name | names, and `missing_ok`: _gains_files_of's (when
    `files` is not given)."""
    cache = root / "work"
    entries: list = []
    found = False
    resolved = (files if files is not None
                else _gains_files_of(cache, account, missing_ok=missing_ok))
    basis = _basis_label(root, resolved)
    tainted = 0
    for acct, path in resolved.items():
        data = _load_json_or_die(path)
        found = True
        _settle = _settle_basis(root, data)
        # Unknown-cost LEAPS closes routed to manual reporting (or
        # flagged in-line) vanished with no word, and the view said
        # "No closed LEAPS positions" (S040-06): count them.
        for e in data.get("manual_reporting_required") or []:
            d = _gains_row_date(e, keep, _settle)
            if ((e.get("symbol") or "") in leaps
                    and _ISO_DATE_RE.match(d) and keep(d)):
                tainted += 1
        for e in data.get("transactions", []):
            sym = e.get("symbol") or ""
            if sym not in leaps:
                continue
            if e.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            if (e.get("tainted") and "gain" in e
                    and e.get("direction") != "SHORT"
                    and not e.get("grant")):
                d = _gains_row_date(e, keep, _settle)
                if _ISO_DATE_RE.match(d) and keep(d):
                    tainted += 1
                continue
            if "gain" not in e or "qty" not in e or e.get("tainted"):
                continue
            # LONG dispositions only: the write (grant record) and the
            # buy-back of a contract that qualified through a long entry
            # are covered-call legs — counted in ccd-sum, and counted a
            # second time here (R1-172).
            if e.get("direction") == "SHORT" or e.get("grant"):
                continue
            d = _gains_row_date(e, keep, _settle)
            if not _ISO_DATE_RE.match(d) or not keep(d):
                continue
            entries.append((acct, e))
    if tainted:
        _view_msg("warning", f"{tainted} LEAPS disposition(s) in this "
                  f"window have an unknown cost and need MANUAL reporting",
                  "No purchase in your files; not shown here (see "
                  "`tjs sum`).")
    return entries, found, basis


def _is_period(s: Optional[str]) -> bool:
    """True if `s` is a period token (30d/6w/3m/1y/mtd/ytd/all/YYYY/tax_year)."""
    s = (s or "").strip().lower()
    return (s in ("all", "max", "mtd", "ytd") or s in _TAX_YEAR_TOKENS
            or _YEAR_TOKEN_RE.fullmatch(s) is not None
            or re.fullmatch(r"\d+\s*[dwmy]", s) is not None)


def _warn_period_or_account(tok: str) -> None:
    """Say, once, that a lone positional naming both a period and a
    configured account (an account called `2025` or `1y`) was read as
    the period: every account, not that one (backlog TJ-0909)."""
    if tok in run_state.current.ambiguous_warned:
        return
    run_state.current.ambiguous_warned.add(tok)
    _view_msg("warning", f"'{tok}' is both a period and an account name: "
              f"read as the period (every account)",
              f"For the account, give a period before it: `tjs "
              f"{_current_cmd() or 'stats'} all {tok}` (or "
              f"`tax_year {tok}`).")


def _period_token(args: argparse.Namespace) -> Optional[str]:
    """The PERIOD positional as _view_window resolves it: None when the
    lone positional was read as an account (`winners margin`) — passing
    the raw token warned that the window 'margin' may exceed the
    artifacts' year (S048-22)."""
    period, account = args.period, args.account
    if period and account is None and not _is_period(period):
        return None
    return period


def _lone_positional(root: Path, period: Optional[str],
                     account: Optional[str]
                     ) -> Tuple[Optional[str], Optional[str]]:
    """(period, account) of the `[PERIOD] [ACCOUNT]` positionals: a lone
    one is the account when it is no period token. An exact configured
    account name is the account even when it starts with a digit
    (`stats 123abc` was refused as an invalid period; backlog TJ-0909);
    one that is also a period (`2025`) stays the period, said once; any
    other digit-leading non-period is a malformed period (exit 2). One
    with a comma is the accounts (`winners margin,crypto`,
    lib/account_list), unless it starts with a digit: a period, as
    before lists (`divs 1y,margin`, a malformed one)."""
    if not period or account is not None:
        return period, account
    tok = str(period).strip()
    if is_account_list(period) and not tok[:1].isdigit():
        return None, period
    named = tok in (load_config(root, missing_ok=True).get("accounts") or {})
    if named and _is_period(tok):
        _warn_period_or_account(tok)
        return period, None
    if named:
        return None, tok
    if not _is_period(period):
        if tok[:1].isdigit():
            _tx_period_cutoff(period)                # sys.exits: invalid period
        return None, period
    return period, None


def _view_window(args: argparse.Namespace, root: Path):
    """Resolve the shared optional `(period, account)` positionals of the
    query commands (events/divs/roc/leaps/trades/gains and the -sum
    roll-ups) into `(keep, scope, account)`. The lone positional is read as
    an account when it isn't a period token (a digit-leading non-token is
    validated as a window so the error names the real problem); with no
    period the scope defaults to the config tax year, else all history.
    `account` is the text given (a list's comma text): a command that
    takes a list reads `_view_scope`'s names instead."""
    period, account = _lone_positional(root, args.period, args.account)
    keep, scope = _window_of(period, root)
    return keep, scope, account


def _window_of(period: Optional[str], root: Path
               ) -> Tuple[Callable[[str], bool], str]:
    """(keep, scope) of a PERIOD token: with none, the config tax year,
    else all history."""
    if period:
        keep, scope = _period_keep(period, root)         # incl. `tax_year`
        return keep, scope
    year = load_settings(root, missing_ok=True).year
    if year:
        ys = str(year)
        return _year_keep(ys), f"tax year {year}"
    return (lambda d: True), "all history"


def _view_scope(args: argparse.Namespace, root: Path
                ) -> Tuple[Any, str, Optional[Tuple[str, ...]]]:
    """`_view_window` for a command that takes an ACCOUNT list
    (lib/account_list): `(keep, scope, accounts)`, `accounts` the names
    in the order given (one name: a tuple of one) or None for every
    account, as `_accounts` reads them. The lone positional is the
    accounts when it has a comma and starts with no digit, or names an
    account (`_lone_positional`)."""
    period, account = _lone_positional(root, args.period, args.account)
    keep, scope = _window_of(period, root)
    return keep, scope, _accounts(args, root, text=account or "")


def _configured_names(root: Path) -> Optional[List[str]]:
    """The [accounts] names of the project, or None when it has none
    (no taxjson.toml, no [accounts]: the command says what is missing)."""
    accounts = load_config(root, missing_ok=True).get("accounts")
    if not isinstance(accounts, dict) or not accounts:
        return None
    return [str(n) for n in accounts]


def _period_comma_hint(tokens: Any) -> Tuple[str, ...]:
    """The detail under a list's refusal whose first name is a period
    word (`ytd,margin`): no period has a comma. () otherwise."""
    toks = [tokens] if isinstance(tokens, str) else list(tokens or ())
    first = str(toks[0]).split(" ")[0].split(",")[0].strip() if toks else ""
    return ("A period never has a comma.",) if first and _is_period(first) \
        else ()


def _refuse_list(err: AccountListError, args: argparse.Namespace,
                 mode: str = "") -> None:
    """Stop on an ACCOUNT list refused (lib/account_list.refusal): exit
    1 for a list, whatever is wrong with it."""
    head, details, code = account_list_refusal(
        err, _current_cmd() or str(getattr(args, "cmd", "") or ""), mode)
    (_die_input if code == 2 else _die)(head, *details,
                                        *_period_comma_hint(err.text))


def _accounts(args: argparse.Namespace, root: Optional[Path] = None, *,
              text: Any = None, dest: str = "account",
              one: Union[None, bool, str] = None
              ) -> Optional[Tuple[str, ...]]:
    """The accounts a command was given (lib/account_list), in the
    order given; None for every account, never (). Parsed on every call
    (cli/main sets nothing). `text`: the ACCOUNT text the command
    decided, one token or several (div-estimate, stats, list's date
    swap, the items commands, which parse their own positionals; ""
    for none); without it, `args.<dest>` (`account`: the ACCOUNT
    positional or `--account`), and for `account` with none given, the
    lone [PERIOD] as `_lone_positional` reads it (`winners
    margin,crypto`; one starting with a digit is a period).

    A list (a comma, or several tokens with one) is checked and refused
    naming the bad name, exit 1: an empty, artifact, path or repeated
    name, and one not in [accounts] (when the project has them). One
    name is returned as given, unchecked: the command's own check names
    it, with today's message and code. `one`: the mode takes one
    account (the flag naming it, `--set`, or True for the command):
    more than one is refused (exit 1), naming the mode."""
    if text is None:
        text = getattr(args, dest, None)
        period = (getattr(args, "period", None)
                  if dest == "account" and hasattr(args, dest) else None)
        if text is None and period:
            r = Path(args.dir).resolve() if root is None else root
            text = _lone_positional(r, period, None)[1]
    toks = ([str(t) for t in text if t] if isinstance(text, (list, tuple))
            else [str(text)] if text else [])
    if not toks:
        return None
    mode = one if isinstance(one, str) else ""
    if not any(is_account_list(t) for t in toks):
        names: Optional[Tuple[str, ...]] = tuple(toks)
        if one and len(toks) > 1:
            _refuse_list(AccountListError("one", toks, " ".join(toks),
                                          count=len(toks)), args, mode)
        return names
    r = (root if root is not None
         else Path(getattr(args, "dir", None) or ".").resolve())
    try:
        names = parse_accounts(toks, _configured_names(r), one=bool(one))
    except AccountListError as e:
        _refuse_list(e, args, mode)
        names = None                                    # (not reached)
    return names


def _accounts_shown(names: Optional[Tuple[str, ...]]) -> str:
    """' for account 'x'' / ' for accounts 'x', 'y'' ('' for None or
    ()): the words a message about the named accounts ends with."""
    if not names:
        return ""
    return (f" for account {names[0]!r}" if len(names) == 1 else
            " for accounts " + ", ".join(repr(n) for n in names))


def _require_listed(names: Any, have: Any, what: str, *, code: int = 2,
                    fix: Optional[str] = None) -> None:
    """Refuse the accounts of `names` (None | a name | a list | names:
    the ones asked for) that `have` (the names that have `what`: a dict
    of files, a set) lacks, naming each, in the order asked: a named
    account with no file was left out of the report with no word
    (resolve_gains_files leaves out a name it finds no file for). Exit
    1 for two names or more (every refusal of a list exits 1); `code`
    for one name (2: run first or check the name, as most single-account
    sites say). `fix`: the remedy shown under it. None and () ask for
    nothing."""
    asked = names_of(names)
    if not asked:
        return
    present = set(have)
    missing = tuple(n for n in asked if n not in present)
    if not missing:
        return
    (_die_input if code == 2 and len(asked) == 1 else _die)(
        f"no {what}{_accounts_shown(missing)} in work/",
        fix or ("Run `tjs run` first, or check the name (an account with "
                "no inputs has no books)."))


def _tx_fee(tx: dict) -> float:
    """Total fee on a transaction. Parsers split this across `fee` and
    `commission` (Questrade uses the latter); sum both."""
    return float(tx.get("fee") or 0.0) + float(tx.get("commission") or 0.0)


def _view_income_rules(root: Path):
    """The project's lib/income_dating rules for the income views. A
    project with no country, or whose settings the rules refuse, stops
    the view."""
    settings = load_settings(root, missing_ok=True)
    # No country (no taxjson.toml): the window, the account types and
    # the dating rules are all unknown — refuse, as `divs` / `roc` do,
    # instead of an 'all history' total with registered accounts folded
    # in (audit A2-1110).
    _country(settings)
    try:
        return _income_rules(settings)
    except ValueError as e:
        _die(str(e))


def _warn_accounts_without_books(root: Path, have, label: str,
                                 what: str, *, among: Any = None) -> None:
    """Warn naming each configured account that has inputs but no `what`
    in work/ (its stage failed): the all-accounts views discover
    accounts from work/, so such an account was simply absent — "No
    wash sales", "No dividends" — with rc 0 (S045-09). `among`: only
    these accounts (None | a name | names: an ACCOUNT list's; the comma
    text: ValueError, _listed_names)."""
    have = set(have)
    only = _listed_names(among)
    skipped = _accounts_skipped_for_no_inputs(root)
    missing = sorted(n for n in (load_config(root, missing_ok=True).get("accounts") or {})
                     if n not in have and n not in skipped
                     and (only is None or n in only)
                     and _has_inputs(root, n))
    if missing:
        # Whole: a default view's one-line form cuts the words that say the
        # report is incomplete (backlog TJ-1527).
        from taxjson.lib.out import warn
        warn(f"no {what} for account(s) {', '.join(missing)} (the last `tjs run` "
             f"did not build it — did it fail?) — their rows are NOT in this "
             f"report.", prog=f"tjs {label}", whole=True)


def _collect_period_txs(args: argparse.Namespace, label: str, actions,
                        date_of=None, settle_trades: bool = False, *,
                        missing_ok: bool = False):
    """Read native per-account transactions over a window, for the period-aware
    summaries (`fees`/`divs-sum`/`trades-sum`). The lone positional is a period
    (30d/6w/…) when it looks like one, else an account name; with no period the
    scope defaults to the config tax year. Returns (rows, scope_label, bad,
    keep) — `keep(iso_date) -> bool` is the window predicate, for callers
    that must apply the SAME window to a second data source. `date_of`
    (row -> ISO date) picks the date a row is windowed on (the income
    views pass characterize/income_dating's tax date); default: its `date`.
    An ACCOUNT list (`_view_scope`) reads each named account, one with
    no native transaction file refused, naming each (exit 1), unless
    `missing_ok` (then left out)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    # `X-sum margin` → the lone positional is an account, not a window
    # (see _view_window); `X-sum margin,crypto` the accounts listed.
    keep, scope, named = _view_scope(args, root)

    if named and len(named) == 1:
        account = named[0]
        accounts = [account]
        if _native_tx_file(cache, account) is None:
            _die_input(f"no native transaction file for account "
                       f"{account!r} in work/ — run `tjs run` first, "
                       f"or check the name")
    elif named is not None:
        accounts = list(named)
        if not missing_ok:
            _require_listed(named, [a for a in named
                                    if _native_tx_file(cache, a) is not None],
                            "native transaction file",
                            fix="Run `tjs run` first, or check the names.")
    else:
        accounts = _discover_tx_accounts(cache)
        if not accounts:
            _die("no transaction files in work/ — run `tjs run` first")
        _warn_accounts_without_books(root, accounts, label,
                                     "transaction file")

    rows, bad = [], 0
    # settle_trades: a "tax year N" window over a settle-basis project
    # takes a trade by its SETTLEMENT date, as Schedule 3 does (a
    # Dec-31 sale settling in January is next year's) — S039-18.
    _settle = settle_trades and _settle_basis(root)
    for acct in accounts:
        native = _native_tx_file(cache, acct)
        if native is None:
            continue
        data = _load_json_or_die(native)
        for tx in data.get("transactions", []):
            if actions is not None and tx.get("action") not in actions:
                continue
            d = tx.get("date") or ""
            if not _ISO_DATE_RE.match(d):
                bad += 1
                continue
            if date_of:
                d = date_of(tx)
            elif _settle and tx.get("action") in _TRADE_ACTIONS:
                d = _gains_row_date(tx, keep, True)
            if not keep(d):
                continue
            rows.append((acct, tx))
    return rows, scope, bad, keep


# Native rows that are dispositions/acquisitions, windowed on the
# project's tax_date basis in a tax-year view.
_TRADE_ACTIONS = ("BUYSELL", "ASSIGN", "EXERCISE")


def _account_group_of(root: Path) -> Dict[str, str]:
    """{account: 'taxable'|'sheltered'} from taxjson.toml ({} without a
    config) — the split the roll-up views print so a registered account's
    income or gains never pass for taxable ones (audit S040-13, S041-04)."""
    out: Dict[str, str] = {}
    for name, a in accounts_of(load_config(root, missing_ok=True)).items():
        t = str(a.type or "").strip().lower()
        if t in ("taxable", "sheltered"):
            out[name] = t
    return out


def _double_roc_warnings(root: Path, accounts, keep, rules=None
                         ) -> List[str]:
    """One sentence per [[distributions]] entry that books the same ROC an
    ADJUST in the books already books (same account, symbol and date):
    the ACB is reduced twice (audit R1-163). Matched over ALL rows, not
    the window's: the broker row is windowed on its record date and the
    map row on its own date, so a pair straddling the year end never met
    (audit A2-0072). The map date may be either the pay date or the
    printed record date. Shared by roc-sum, `run` and the checklist's
    roc-entered step (A2-0361)."""
    cache = root / "work"
    if rules is None:
        rules = _view_income_rules(root)
    all_native: List[Tuple[str, dict]] = []
    for a in accounts:
        nf = _native_tx_file(cache, a)
        if nf is None:
            continue
        all_native += [(a, t) for t in
                       (_load_json_or_die(nf).get("transactions") or [])
                       if t.get("action") == "ADJUST"]
    all_dist = _dist_adjust_rows(cache, accounts, lambda _d: True)
    book_keys: Dict[Tuple[str, str, str], str] = {}
    for a, t in all_native:
        if float(t.get("net_amount") or 0.0) >= 0:
            continue
        w = rules.roc_date(t) if rules else str(t.get("date") or "")
        for d in (t.get("date"), t.get("record_date")):
            if d:
                book_keys[(a, str(t.get("symbol") or ""), str(d))] = w
    out = []
    for a, t in all_dist:
        md = str(t.get("date_settle") or t.get("date") or "")
        w = book_keys.get((a, str(t.get("symbol") or ""), md))
        if w is not None and (keep(md) or keep(w)):
            out.append(f"{t.get('symbol')} {md} ({a}) has an ADJUST in the "
                       f"books AND a [[distributions]] entry — the ACB is "
                       f"reduced twice if both are the same distribution.")
    return out


def _dist_adjust_rows(cache: Path, accounts, keep) -> List[Tuple[str, dict]]:
    """[[distributions]] ACB adjustments: `run` books them (type 'dist',
    id DIST-*) into <acct>_base.json only — the native books the
    transaction views read never see them, so `roc` / `roc-sum` said
    "No ACB adjustments" while the engine applied one (audit R1-163)."""
    out: List[Tuple[str, dict]] = []
    # Only a project with [[distributions]] has such rows; without them
    # a missing base book hides nothing.
    _has_map = bool(load_config(cache.parent, missing_ok=True).get("distributions"))
    missing: List[str] = []
    for acct in accounts:
        p = cache / f"{acct}_base.json"
        if not p.exists():
            if _has_map:
                missing.append(p.name)
            continue
        doc = _load_json_or_die(p)
        for t in (doc.get("transactions") if isinstance(doc, dict)
                  else doc) or []:
            # The map's rows only (id DIST-*): RBC's NOTIONAL
            # DISTRIBUTION row carries type 'dist' too, but it is a
            # native row the view already shows — counted twice, with
            # a false double-entry warning (audit A2-0116).
            if (t.get("action") == "ADJUST"
                    and (t.get("type") or "").lower() == "dist"
                    and str(t.get("id") or "").startswith("DIST-")):
                # The map's own date (the record date) is date_settle:
                # the trade-date stamp can sit a day earlier so a
                # trade-ordered engine reaches the record holder's lots
                # (audit A2-0071).
                d = t.get("date_settle") or t.get("date") or ""
                if _ISO_DATE_RE.match(d) and keep(d):
                    out.append((acct, t))
    if missing and not warned_missing_base_once(missing):
        # The map's adjustments live only in the base books: without
        # one they vanished from roc / roc-sum with rc 0 (A2-1128).
        emit_line(f"tjs: warning: taxjson.toml has [[distributions]] but "
                  f"{', '.join(missing)} is missing in {cache} (the last "
                  f"`tjs run` did not build it) — that account's map "
                  f"ACB adjustments are NOT in this report.", file=sys.stderr)
    return out


def warned_missing_base_once(names) -> bool:
    """True when this chain of commands already warned about these base books
    (roc-sum reads them twice: the window's rows and the double-entry
    check's)."""
    key = tuple(sorted(names))
    if key in run_state.current.missing_base_warned:
        return True
    run_state.current.missing_base_warned.add(key)
    return False


def _warn_dist_double_entry(root: Path, accounts, rules, keep,
                            label: str) -> None:
    """Print _double_roc_warnings for a view (roc-sum, roc; A2-1116)."""
    for w in _double_roc_warnings(root, accounts, keep, rules):
        _view_msg("warning", w)

def _load_json_or_die(path: Path) -> Any:
    """Read a work/ artifact a query view needs, or stop naming it. The
    views used to warn and skip the file, then print a partial report —
    a smaller total, a missing account, a clean report —
    with exit 0 (audit S045-01, S042-05). A file that cannot be read is
    an input: exit 2, never the 1 of a command's finding (backlog
    TJ-0596, docs/commands.md's exit codes)."""
    try:
        return _read_work_doc(path)
    except (OSError, ValueError) as e:
        _die_unreadable(path, e)


def _die_unreadable(path: Path, e: BaseException) -> None:
    """Stop a view on a work/ file it cannot read (exit 2), naming it."""
    _die_input(rerun_advice(f"could not read {_work_read_why(path, e)}", " — rerun `tjs run`")
               + " (this view would otherwise leave that file's rows out).")


def _side_doc_or_die(path: Path, fields, only=None) -> Optional[Dict[str, Any]]:
    """A work/ gains document a view reads beside the shown account's
    (another account's covers, an earlier year's history), or None when
    it cannot be read (left out, as before): json_input read_rows_doc,
    its `transactions` rows (those `only` is true of) held to the types
    of `fields`, the ones its views read. A refused figure stops the
    view, as _load_json_or_die does for the shown account's own file
    (audit S045-01; the whole file was left out in silence: backlog
    TJ-3043, TJ-3054)."""
    try:
        return read_rows_doc(path, fields=fields, only=only)
    except ValueError as e:
        _die_unreadable(path, e)


def _rerun_hint(why: str, hint: str) -> str:
    """`hint` with the advice to re-run `tjs run` to rebuild the file,
    unless `why` (a refusal from the row checks) gives it (backlog
    TJ-2977)."""
    return hint + ("" if advises_rerun(why) else " Re-run `tjs run` to rebuild it.")


def _work_read_why(path: Path, e: BaseException,
                   root: Optional[Path] = None) -> str:
    """`<file>: <reason>` for a work/ file its reader refused, the file
    said once and from the project (`work/margin_gains.json: not valid
    JSON (…)`): the reader's message names it by its absolute path, and
    `could not read work/x: /abs/…/work/x: …` said it twice
    (docs/output-style.md; backlog TJ-0596, TJ-0641)."""
    if root is not None:
        shown = _out_relpath(path, root)
    elif path.parent.name == "work":
        shown = _out_relpath(path, path.parent.parent)
    else:
        shown = str(path)
    why = str(e)
    return (why.replace(str(path), shown) if str(path) in why
            else f"{shown}: {why}")


def _warn_bad_dates(bad: int) -> None:
    if bad:
        _view_msg("warning", f"{bad} row(s) had a missing/unparseable "
                  f"date and were excluded from the window.")


def _foot(values) -> float:
    """A report total that FOOTS: the sum of the rows as printed
    (rounded to the cent), not the raw sum rounded once — the printed
    TOTAL differed from the column by cents (S037-11; `sum` already
    rounds per row)."""
    return M.shown(sum(M.shown(float(v or 0.0), 2) for v in values), 2)


def _box18_fractions(root: Path, rules=None
                     ) -> Optional[Dict[Tuple[str, str], float]]:
    """{(account, row id): share of the DIVIDEND row that is a T5 box 18
    capital-gains dividend} from the project's [[capital_gains_dividends]]
    (taxjson.toml; lib/cg_dividends; tax-logic CA-INC-06), or None
    without entries. Canada only: a US project's table is refused with
    the rest of the config (lib/country CONFIG_COUNTRY). Matched over
    EVERY year in the native books, on the dividend's tax date; an
    entry without an account covers the taxable accounts."""
    from taxjson.lib.cg_dividends import (CgDividendMapError, allocate,
                                          entries_from_config)
    try:
        entries = entries_from_config(load_config(root, missing_ok=True))
    except CgDividendMapError as e:
        _die(str(e))
    if entries is None:
        return None
    if rules is None:
        rules = _view_income_rules(root)
    date_of = (rules.income_date if rules is not None
               else (lambda t: str(t.get("date") or "")))
    cache = root / "work"
    rows: List[Tuple[str, dict]] = []
    for acct in _discover_tx_accounts(cache):
        native = _native_tx_file(cache, acct)
        if native is None:
            continue
        for t in _load_json_or_die(native).get("transactions", []):
            if t.get("action") == "DIVIDEND":
                rows.append((acct, t))
    groups = _account_group_of(root)
    taxable = ({a for a, g in groups.items() if g == "taxable"}
               if groups else None)
    try:
        return allocate(entries, rows, date_of=date_of,
                        default_accounts=taxable)
    except CgDividendMapError as e:
        _die(str(e))


def _staking_rows(args: argparse.Namespace):
    """Rewards and their acquisitions from the final base-currency books.

    Read one consolidated book per configured crypto account, never the
    per-broker parses alongside it. This preserves the run's dedup, prices
    and FX and excludes intermediate books and the combined sheltered book.
    """
    from datetime import date as _date
    from taxjson.lib.tx_load import validate_staking_reward
    root = Path(args.dir).resolve()
    cfg = load_config(root, missing_ok=True)
    settings = settings_of(cfg)
    country = _country(settings)
    currency = _base_currency(root)
    keep, scope, selected = _view_scope(args, root)
    _require_books(root, selected)
    configured = accounts_of(cfg)
    if selected and len(selected) == 1:
        if selected[0] not in configured:
            _die_input(f"unknown account {selected[0]!r} — check taxjson.toml")
        accounts = list(selected)
    elif selected is not None:
        # A list (exit 1): each name not in [accounts], then each with no
        # base-currency book, named.
        unknown = [n for n in selected if n not in configured]
        if unknown:
            _die(unknown_accounts_text(unknown, configured))
        _require_listed(selected, [
            n for n in selected
            if (root / "work" / f"{n}_base.json").is_file()],
            "base-currency book", fix="Run `tjs run` first; staking totals "
            "need their saved prices and exchange rates.")
        accounts = list(selected)
    else:
        accounts = sorted(n for n, a in configured.items()
                          if a.crypto is True)
    rows = []
    tt_rewards = 0
    for acct in accounts:
        path = root / "work" / f"{acct}_base.json"
        if not path.is_file():
            if selected or _has_inputs(root, acct):
                _die_input(f"no base-currency book for {acct!r} — run "
                           "`tjs run` first; staking totals need its "
                           "saved prices and exchange rates")
            continue
        crypto = account_of(configured, acct).crypto is True
        for tx in _load_json_or_die(path).get("transactions", []):
            action = tx.get("action")
            if action not in {"DIVIDEND", "BUYSELL"}:
                continue
            if action == "DIVIDEND" and not (crypto or tx.get("staking_reward")):
                continue
            when = str(tx.get("date") or "")
            try:
                _date.fromisoformat(when)
            except ValueError:
                _die_input(f"{path.name}: invalid reward/acquisition date "
                           f"{when!r} — rerun `tjs run`")
            if not keep(when):
                continue
            if str(tx.get("currency") or "").strip().upper() != currency.upper():
                _die_input(f"{path.name}: {tx.get('symbol', '?')} on {when} "
                           f"is not in {currency} — rerun `tjs run` "
                           "before totalling staking")
            if action == "DIVIDEND":
                facts = tx.get("staking_reward") or {}
                if not isinstance(facts, dict):
                    _die_input(f"{path.name}: damaged staking evidence — "
                               "rerun `tjs run`")
                if facts:
                    try:
                        validate_staking_reward(facts, path.name)
                    except ValueError as exc:
                        _die_input(f"{exc} — rerun `tjs run`")
                broker = str(facts.get("broker") or "unknown")
                if args.symbol and str(tx.get("symbol") or "").casefold() != args.symbol.casefold():
                    continue
                if args.broker and broker.casefold() != args.broker.casefold():
                    if broker == "unknown":
                        # A .tt reward line never records an exchange, so
                        # no rerun gives it one: left out of another
                        # exchange's view, and counted. An export's reward
                        # without one is a book built before the evidence.
                        if not str(tx.get("source") or "").lower().endswith(".tt"):
                            _die_input(f"{path.name}: exchange is unknown for a reward "
                                       "in this window — rerun `tjs run` before "
                                       "filtering staking by exchange")
                        tt_rewards += 1
                    continue
            rows.append((acct, tx))
    if tt_rewards:
        _say("note", f"{tt_rewards} reward(s) in this window come from .tt "
             f"lines, which name no exchange: not in --broker {args.broker}",
             "`tjs staking --broker unknown` lists them.")
    return rows, scope, currency, country, settings


def _staking_view(args: argparse.Namespace, *, summary: bool) -> Optional[Dict[str, Any]]:
    """staking / staking-sum: the rewards report (lib/staking_report),
    its text printed; with --json nothing printed and the report
    returned, which the command writes under its output's id
    (lib/json_contract)."""
    from taxjson.lib import out
    from taxjson.lib.staking_report import build_report, render
    rows, scope, currency, country, settings = _staking_rows(args)
    try:
        report = build_report(
            rows, currency=currency, scope=scope, country=country,
            account_groups=_account_group_of(Path(args.dir).resolve()),
            local_timezone=str(settings.local_timezone or "unknown"))
    except ValueError as exc:
        _die_input(f"staking report: {exc}")
    if getattr(args, "json", False):
        return report
    out.show(render(report, summary=summary,
                    details=getattr(args, "details", False)), file=sys.stdout)
    return None


def _option_openings(root: Path, rows: List[Dict[str, Any]],
                     native: bool = False) -> List[Dict[str, Any]]:
    """The missing-history openings (`OPENING <date> <SYMBOL> <qty>
    cost=unknown` .tt lines) of the option contracts in `rows` (one
    account's book), as the OPENING_BALANCE rows the gains stage
    synthesizes in memory — the books on disk never carry them, so the
    option views read a long contract bought before the data and sold
    to close as a write (2026-10 audit #59; option-boundary's S044-09).
    `native`: the rows are the native books (symbols mapped by GLOBAL
    renames only). [] without such lines."""
    return _tt_openings(root, rows, native=native, options_only=True)


def _tt_openings(root: Path, rows: List[Dict[str, Any]], *,
                 native: bool = False,
                 options_only: bool = False) -> List[Dict[str, Any]]:
    """The missing-history openings of `rows` (one account's book), as
    the OPENING_BALANCE rows the gains stage synthesizes in memory:
    every symbol's, or the option contracts' alone (`options_only`,
    `_option_openings`). A view that reads a position from the books on
    disk reads a sale of units bought before the data as a short
    without them (div-estimate's long or short test: 2026-10-25 audit
    planning-us#4). `native`: the rows are the native books (symbols
    mapped by GLOBAL renames only). [] without such lines."""
    mh = _missing_history_arg(root)
    if mh is None or not rows:
        return []
    from taxjson.model.transaction import TaxTransaction
    from taxjson.lib.option_symbols import is_option_symbol
    from taxjson.lib.missing_history import (load_missing_history,
                                             synthesize_openings)
    try:
        pairs = load_missing_history(mh, native=native)
    except (OSError, ValueError) as e:
        _die("the .tt OPENING cost=unknown lines cannot be read", str(e))
    fixed = getattr(pairs, "fixed", None) or {}
    if options_only \
            and not any(is_option_symbol(str(sym)) for sym, _a in fixed) \
            and not any(is_option_symbol(str(sym)) for sym, _a in pairs):
        return []
    if not fixed and not pairs:
        return []
    fields = TaxTransaction.__dataclass_fields__
    txs = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            txs.append(TaxTransaction(**{k: v for k, v in r.items()
                                         if k in fields}))
        except TypeError:
            continue
    try:
        out, _log = synthesize_openings(txs, pairs, flag_stale=False)
    except ValueError as e:
        _die("the .tt OPENING cost=unknown lines cannot be applied", str(e))
    import dataclasses as _dc
    return [_dc.asdict(t) for t in out
            if t.action == "OPENING_BALANCE"
            and (not options_only or is_option_symbol(str(t.symbol or "")))]


def _gains_blended(cache: Path, acct: str) -> bool:
    """Whether the account's tax-year gains came from the blended
    taxable pass (its wash file's metadata says `blended`)."""
    import json as _json
    wash = cache / f"{acct}_gains_wash.json"
    if not wash.is_file():
        return False
    try:
        doc = _json.loads(wash.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(doc, dict)
                and (doc.get("metadata") or {}).get("blended"))


def _split_pool_of(root: Path) -> Callable[[str], str]:
    """account -> the pool its corporate events are shared in, for the
    option views that replay the native books per account: in a
    Canadian project the taxable equity accounts the run blended are
    one pool (ITA s.47, CA-CORP-12: a split or rename of a series that
    one of them reports is the security's, and the gains carry it into
    the others' contracts), every other account a pool of its own. A
    split posted in one account left the others' contracts in the old
    series (backlog TJ-0907). A US project, or books without a
    taxjson.toml country: each account its own."""
    settings = load_settings(root, missing_ok=True)
    if not settings.country or _country(settings) == "usa":
        return lambda a: a
    from taxjson.lib.crypto_sends import crypto_accounts
    cache = root / "work"
    crypto = set(crypto_accounts(load_config(root, missing_ok=True)))
    members = {a for a, g in _account_group_of(root).items()
               if g == "taxable" and a not in crypto
               and _gains_blended(cache, a)}
    return lambda a: "\0blended" if a in members else a


def _out_act_prefix() -> str:
    from taxjson.lib import out as _out
    return _out.ACT


def _real_world_legs(v: GainRow) -> Tuple[float, float]:
    """(proceeds, cost) of a gains row (its model/result view) as a
    person reads them: a SHORT row carries the
    engine's signed legs (cost = -premium/-short proceeds, proceeds =
    -buy-back/-cover), so its columns read negative and swapped. The
    views show what ccd-sum, winners and form-export show — proceeds
    = what the short brought in, cost = what closing it cost (S048-10,
    S040-14)."""
    proceeds = float(v.proceeds or 0.0)
    cost = float(v.cost or 0.0)
    if v.get("direction") == "SHORT":
        return -cost + 0.0, -proceeds + 0.0
    return proceeds, cost


def _base_currency(root: Path) -> str:
    """Base currency label from taxjson.toml: [settings] base_currency,
    else the country's own currency (never a silent CAD for a US
    project)."""
    return _base(load_settings(root, missing_ok=True))


def _artifact_year_mismatch(files: Dict[str, Path],
                            config_year) -> Dict[str, str]:
    """{account: artifact_year} for each gains file whose recorded tax
    year (summary.year, written by the engine) differs from
    [settings].year. After a year bump without a rebuild, work/ still
    holds LAST year's books: close-year would lock them under the new
    year and sum/estimate/form-export would present them as the new
    year's figures (2026-09 CLI audit B2). Unreadable files and files
    without a recorded year are skipped (their own readers complain)."""
    out: Dict[str, str] = {}
    if config_year is None:
        return out
    for acct, pth in files.items():
        try:
            doc = _read_work_doc(Path(pth))
        except (OSError, ValueError):
            continue
        yr = ((doc.get("summary") or {}).get("year")
              if isinstance(doc, dict) else None)
        if yr is not None and str(yr) != str(config_year):
            out[acct] = str(yr)
    return out


def _warn_artifact_year(files: Dict[str, Path], config_year) -> None:
    """Loud stderr banner for _artifact_year_mismatch (report commands:
    they still print, but the numbers belong to another year)."""
    bad = _artifact_year_mismatch(files, config_year)
    if not bad:
        return
    got = ", ".join(f"{a} ({y})" for a, y in sorted(bad.items()))
    if _brief(sys.stderr):
        _say("warning", f"these figures are NOT {config_year}'s: work/ "
             f"was built for another year — run `tjs run`",
             prog=_cmd_prog())
        return
    _say("warning", f"[settings].year is {config_year} but the work/ "
         f"books were built for another tax year: {got}",
         f"These figures are NOT {config_year}'s — rebuild with `tjs "
         f"run` first.", prog=_cmd_prog())


def _brief(stream=None) -> bool:
    """A shared report-command warning shows only its headline: shown to
    a person (width > 0) without --details (docs/output-style.md,
    Essentials first). Captured (width 0) it keeps every detail line."""
    from taxjson.lib.out import width as _w
    return (not run_state.current_details
            and _w(sys.stderr if stream is None else stream) > 0)


# _run_state_problems' entry for a run that died after writing work/:
# close-year refuses those books even with --force (backlog TJ-0015).
_RUN_UNFINISHED = ("the last `tjs run` did not finish: reports/ has "
                   "no .sum, but work/ holds the partial books it wrote")


def _run_state_problems(root: Path, cfg: Dict[str, Any]) -> List[str]:
    """What `tjs checklist`'s run-clean step finds wrong with the
    books the filing commands read: validation ERRORs in the last run,
    an account deferred on pending elections, inputs changed since the
    last full run (a failed run leaves them newer than the artifacts),
    an account with inputs but no report. [] when clean or when there
    is nothing to judge (no reports yet). The report commands used to
    serve these books with rc 0 and no word (R1-252, S049-11). A
    "blocked" step with work/ books but no reports/ (a run that died
    before writing them) is a problem too: close-year locked those with
    rc 0 (A2-0035, A2-0658)."""
    from taxjson.lib.checklist import Ctx, RunHelpersMissing, d_run_clean
    try:
        from datetime import date as _d
        year = int(settings_of(cfg).year or 0)
        # (No run's readers: d_run_clean asks none; one that came to would
        # stop here, never read as clean books: TJ-3539.)
        res = d_run_clean(Ctx(root, cfg, year, _d.today(),
                              lambda *a, **k: (0, "", "")))
    except RunHelpersMissing:
        raise
    except Exception:                                   # noqa: BLE001
        return []
    if res.status == "blocked":
        # No .sum yet: nothing to judge — unless a run aborted after
        # writing work/ (a first run that died at a later stage). Those
        # partial books are what sum / t1135 / list read (A2-0658). A
        # `tjs clean` stopped part-way leaves these too: it removes the
        # .sum files first and these books last (lib/clean PARTIAL_BOOKS).
        from taxjson.lib.clean import PARTIAL_BOOKS
        cache = root / "work"
        try:
            partial = any(
                not p.name.startswith(".")
                for pat in PARTIAL_BOOKS
                for p in cache.glob(pat))
        except OSError:
            partial = False
        if partial:
            return [_RUN_UNFINISHED]
        return []
    if res.status != "attention":
        return []
    # Each problem whole: one may hold "; " itself ("inputs changed ...
    # (changed: X; added: Y)" was two problems, backlog TJ-0109).
    return [x.strip() for x in (res.problems or res.detail.split("; "))
            if x.strip()]


def _books_older_than_inputs(root: Path, cfg: Dict[str, Any], *,
                             read_upto: Optional[int] = None) -> str:
    """Why the books in work/ are not what a full `tjs run` of the inputs
    as they are now builds, by the last full run's record of what it read
    (lib/checklist `inputs_changed`, the rule run-clean reads, as
    find-missing-history's refusal reads the record: cli/books
    `_refuse_stale_books_write`): the inputs changed since, no record
    (every full run writes one: TJ-4309), one that cannot be read, or books
    another taxjson built (the record's package fingerprint: TJ-4310). ""
    when they are current. `read_upto`: the latest year the caller reads
    (a lock of that year or a later one, closed from these books since,
    moves none of its figures: checklist `inputs_changed`)."""
    from taxjson.lib.checklist import inputs_changed
    from taxjson.lib.fingerprint_record import FINGERPRINT_FILE, books_code
    from taxjson.pipeline.freshness import package_fingerprint
    try:
        diff = inputs_changed(root, cfg, read_upto=read_upto)
    except (OSError, ValueError) as e:
        return f"the inputs cannot be read: {e}"
    if diff:
        return f"the books in work/ are older than the inputs ({diff})"
    if diff is None:
        return (f"no full `tjs run` recorded what the books in work/ were built "
                f"from (work/{FINGERPRINT_FILE})")
    if books_code(root) != package_fingerprint():
        return ("the books in work/ were built by another taxjson (the last full "
                "run's record names other code)")
    return ""


def _rebuild_first(root: Path, cfg: Dict[str, Any]) -> str:
    """The fix of a refusal of books older than the inputs: `tjs run`, or,
    when the last run deferred accounts at pending elections (it leaves no
    record: TJ-4356), the elections first (backlog TJ-0148)."""
    deferred = sorted(n for n in (cfg.get("accounts") or {})
                      if (root / "work" / f"{n}_pending_elections.json").exists())
    if deferred:
        return (f"The last `tjs run` deferred {', '.join(deferred)} at pending "
                f"elections (`tjs elections --pending`): make the elections, then "
                f"run `tjs run`.")
    return "Run `tjs run` first."


def _refuse_cash_older_than_inputs(root: Path, cfg: Dict[str, Any]) -> None:
    """Exit 1 before a command reads the cash events (lib/cash_events
    `collect`: `tjs cash`, `tjs fx-cash --ledger v2`, `tjs t1135` with cash
    held outside Canada, `tjs sum` with the ledger v2, the checklist) when the
    books in work/ are older than the inputs (`_books_older_than_inputs`,
    the books' own check): the events are the parse's sidecars
    (work/<account>_<broker>.cash), written by the same full run, so they are
    as old as the books. No flag skips it: `tjs run` always rebuilds
    everything, and nothing reads data older than the inputs (the owner's
    decision of 2026-11-17). A lock of the project's year closed from these
    books since is no change, as close-year reads it (`read_upto`: a US
    project's lock is an input, review of commit 6). The dispatcher asks it
    before the books' banner too (cli/main `_banner_run_state`). Books as new
    as the inputs with an export group's events missing, or not the run's
    (cash_events `_sidecar_files`), are refused here too, before any output."""
    try:
        year: Optional[int] = int(settings_of(cfg).year)
    except (TypeError, ValueError):
        year = None
    why = _books_older_than_inputs(root, cfg, read_upto=year)
    if why:
        _die(why, "The cash events are read from the last `tjs run`'s parse "
                  "(work/<account>_<broker>.cash), not from the exports. "
             + _rebuild_first(root, cfg))
    from taxjson.lib import cash_events as CE
    from taxjson.lib.country import CountryError, settings_country
    try:
        CE.collect(root, cfg, settings_country(settings_of(cfg)), {})
    except CountryError:
        return                  # (the command says the country is missing)
    except CE.CashIdConflict as e:
        _die_cash_ids(e.conflicts)
    except (OSError, ValueError) as e:
        _die(str(e))


def _die_cash_ids(conflicts: List[str]) -> None:
    """Exit 2 on a broker's id that two exports hold with different
    content (lib/cash_events `CashIdConflict`: an input refused, backlog
    TJ-5034): one conflict the error itself, several one below the
    other under a line that counts them."""
    if len(conflicts) == 1:
        _die_input(conflicts[0])
    _die_input(f"{len(conflicts)} broker's ids are in two exports with different content",
               *conflicts)


def _refuse_books_older_than_inputs(root: Path, cfg: Dict[str, Any], *,
                                    read_upto: Optional[int] = None) -> None:
    """Exit 1 before check-filed or form-export reads books a full
    `tjs run` did not build from the inputs as they are now (`tjs run`
    always rebuilds everything: the owner's decision of 2026-11-17; backlog
    TJ-4305): older than the inputs (`_books_older_than_inputs`), or an
    `--account` run left the cross-account pass behind, a taxable account's
    wash-adjusted gains older than a book it read or an account it did not
    see (report_model.stale_wash_inputs), or an equity account with plain
    gains only that no election defers. Books with no record, or built by
    another taxjson, are refused too. close-year asks the same among its
    own refusals (cli/file `cmd_close_year`)."""
    why = _books_older_than_inputs(root, cfg, read_upto=read_upto)
    fix = _rebuild_first(root, cfg)
    if why:
        _die(why, fix)
    from taxjson.lib.report_model import resolve_gains_files, stale_wash_inputs
    cache = root / "work"
    accts = accounts_of(cfg)
    files = {a: p for n, c in accts.items() if c.taxable
             for a, p in resolve_gains_files(cache, n, warn_stale=False).items()}
    stale = sorted(f"{a} (older than {', '.join(s)})" for a, p in files.items()
                   if p.name.endswith("_gains_wash.json") and (s := stale_wash_inputs(p)))
    plain = sorted(a for a, p in files.items()
                   if not account_of(accts, a).crypto
                   and not p.name.endswith("_gains_wash.json")
                   and not (cache / f"{a}_pending_elections.json").exists())
    if stale or plain:
        _die(f"the wash-adjusted gains of {'; '.join(stale + plain)} are not the "
             f"last full run's", "A `tjs run --account` rebuilt books since, or built "
             "an account alone: the pass across your accounts did not run. " + fix)


def _refuse_other_country_books(root: Path, cfg: Dict[str, Any]) -> None:
    """Die when work/ was built under the other country: its figures
    follow that country's law (a superficial-loss denial, an s.47 pool,
    CAD amounts) and every view would print them under this country's
    labels and citations at exit 0 (audit A2-0147). The country comes
    from the last full run's record."""
    from taxjson.lib.fingerprint_record import books_country
    from taxjson.lib.country import CountryError, settings_country
    built = books_country(root)
    try:
        now = settings_country(settings_of(cfg))
    except CountryError:
        return
    if built and built != now:
        _die(f"the books in work/ were built by the last full run for country = \"{built}\", but "
             f"taxjson.toml now says \"{now}\" — their figures follow the "
             f"other country's law. Run `tjs run` to rebuild them "
             f"before using any report.")


def _warn_run_state(root: Path, cfg: Dict[str, Any]) -> List[str]:
    """Loud stderr banner for _run_state_problems; returns them. Books
    built under the other country are refused outright."""
    _refuse_other_country_books(root, cfg)
    probs = _run_state_problems(root, cfg)
    if probs and _brief(sys.stderr):
        _filed = ("; the filed year's inputs changed"
                  if _filed_year_stale_hint(root, cfg, probs) else "")
        _say("warning", f"these books are not the clean result of the "
             f"current inputs ({_count_noun(len(probs), 'problem')}"
             f"{_filed}): `tjs checklist --run`", prog=_cmd_prog())
    elif probs:
        _say("warning", "these books are not the clean result of the "
             "current inputs",
             *[f"- {p}" for p in probs],
             "The figures below may leave sales out or carry default FX; "
             "fix and re-run `tjs run` before using them."
             + _filed_year_stale_hint(root, cfg, probs),
             prog=_cmd_prog())
    return probs


def _filed_year_stale_hint(root: Path, cfg: Dict[str, Any],
                           probs: List[str]) -> str:
    """When the books' year is filed (filed/<year>.json) and one of
    `probs` (_run_state_problems) is that the inputs changed: the
    sentence saying they may have moved the filed figures — in a
    multi-year project a shared input or ticker.map edited while
    working on a later year (lib/project_layout). Any other problem (a
    run that did not finish) gets no such sentence."""
    year = settings_of(cfg).year
    if year is None or not (root / "filed" / f"{year}.json").is_file():
        return ""
    if not any(str(p).startswith("inputs changed since the last")
               for p in probs):
        return ""
    return (f" {year} is filed (filed/{year}.json) and its inputs changed "
            f"since the last run: `tjs run` here recomputes the year "
            f"and says whether the filed figures moved (DRIFTED).")
_FROZEN_NOTE_PREFIX = "## frozen by `tjs close-year`"


class LedgerUnavailable(Exception):
    """`tjs carryover --json` gave no ledger to go by, or one that left a
    book out; str() says why, in one line. No ledger is not "no carry-back":
    whoever reads one says it could not be used (backlog TJ-1422)."""


class CarryBack(NamedTuple):
    """The part of a year's net capital loss `tjs carryover` finds carried
    back by [carryover] claimed. `incomplete` (lib/carry_back.py): the ledger
    says it left sales out, so `amount` may change (backlog TJ-1436).
    `unknown`: why the ledger could not be used, `amount` then meaning nothing
    (backlog TJ-1422)."""
    amount: float = 0.0
    incomplete: Optional[Dict[str, Any]] = None
    unknown: Optional[str] = None
    # {year: amount} of the earlier years' carry-backs that are the person's
    # own figures ([carryover] carried_back; backlog TJ-4880).
    stated: Optional[Dict[str, float]] = None
    # {earlier year: the part of `amount` carried back to it}, the row's
    # `carried_back_to`; `to_split`: "entry" when the person's by-target
    # [carryover] carried_back entry for the year says it, "oldest_first"
    # when it is the ledger's proposal and another split was open, else None;
    # `stated_to`: {year: {earlier year: amount}} of the earlier years'
    # carry-backs whose years are the person's figures (backlog TJ-4882).
    to: Optional[Dict[str, float]] = None
    to_split: Optional[str] = None
    stated_to: Optional[Dict[str, Dict[str, float]]] = None
    # The ledger's `carried_in_not_in_nets`: the balance carried in and the
    # [carryover] carried_back entries hold only with a gain or loss the nets
    # don't have (lib/carry_back `not_in_nets`; backlog TJ-4886).
    not_in_nets: Optional[List[Dict[str, Any]]] = None


def _ledger_amount(row: Dict[str, Any], key: str) -> float:
    """A money figure of a ledger row: 0.0 when null or absent, and only
    then. A default ahead of this check read false as 0.00 and true as
    1.00 (backlog TJ-1440)."""
    from taxjson.lib.tx_load import finite_num
    v = row.get(key)
    if v is None:
        return 0.0
    if not finite_num(v):
        raise LedgerUnavailable(f"`tjs carryover --json` gave {key} {v!r} "
                                f"for {row.get('year')}")
    return float(v)


def _carried_back_to(row: Dict[str, Any]) -> Dict[int, float]:
    """A ledger row's {earlier year: the part of its loss carried back to
    that year}."""
    from taxjson.lib.tx_load import finite_num
    to = row.get("carried_back_to")
    if to is None:
        return {}
    try:
        if not all(finite_num(a) for a in to.values()):
            raise ValueError
        return {int(y): float(a) for y, a in to.items()}
    except (AttributeError, TypeError, ValueError):
        raise LedgerUnavailable(f"`tjs carryover --json` gave carried_back_to "
                                f"{to!r} for {row.get('year')}") from None


def _good_left_out_sale(e: Any) -> bool:
    """An `excluded_unknown_cost` entry a message can print: no year is real
    (a date that names none), a year or proceeds of another kind is not."""
    from taxjson.lib.tx_load import finite_num
    return (isinstance(e, dict)
            and (e.get("year") is None
                 or (isinstance(e["year"], int)
                     and not isinstance(e["year"], bool)))
            and (e.get("proceeds") is None or finite_num(e["proceeds"])))


def _ledger_doc(root: Path, *flags: str) -> Dict[str, Any]:
    """`tjs carryover --json`, read once and whole: every caller gets a
    ledger whose rows, figures and completeness signals are good, or
    LedgerUnavailable. Raises when the child fails, prints something else
    or a figure that is no number, or left a taxable account's book out.
    `incomplete_years` is not a failure: see lib/carry_back.py."""
    import json as _json
    from taxjson.pipeline.dispatch import run_cmd as _run
    res = _run(_self_cmd(root, "carryover", "--json", *flags),
               capture_output=True)
    if res.returncode != 0:
        why = " ".join(_child_error(res.stderr).split()) \
            if (res.stderr or "").strip() else (
                f"`tjs carryover --json` exited {res.returncode} with no "
                f"error output")
        # One line: every reader puts it in a sentence (backlog TJ-1444).
        raise LedgerUnavailable(why if len(why) <= 240 else
                                f"... {why[-236:]}")
    try:
        doc = _json.loads(res.stdout)
    except ValueError:
        doc = None
    rows = doc.get("rows") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not all(
            isinstance(x, dict) and isinstance(x.get("year"), int)
            and not isinstance(x["year"], bool) for x in rows):
        raise LedgerUnavailable("`tjs carryover --json` printed no ledger")
    for x in rows:
        _ledger_amount(x, "carried_back")
        _ledger_amount(x, "carryforward_balance")
        _carried_back_to(x)
    inc = doc.get("incomplete_years")
    if inc is not None and not (isinstance(inc, list) and all(
            isinstance(y, int) and not isinstance(y, bool) for y in inc)):
        raise LedgerUnavailable(f"`tjs carryover --json` gave "
                                f"incomplete_years {inc!r}")
    excl = doc.get("excluded_unknown_cost")
    if excl is not None and not (isinstance(excl, list) and all(
            _good_left_out_sale(e) for e in excl)):
        raise LedgerUnavailable("`tjs carryover --json` gave an "
                                "excluded_unknown_cost that is no list of "
                                "sales")
    from taxjson.lib.carry_back import left_out_marks, left_out_unchecked
    try:
        left_out_marks(doc)     # which claims those sales can move (TJ-1487)
        left_out_unchecked(doc)     # ...named without every fold (TJ-5291)
    except ValueError as e:
        raise LedgerUnavailable(str(e)) from None
    books = doc.get("missing_books")
    if books is not None and not isinstance(books, list):
        raise LedgerUnavailable(f"`tjs carryover --json` gave missing_books "
                                f"{books!r}")
    if books:
        raise LedgerUnavailable(
            f"no base file for taxable account(s) "
            f"{', '.join(map(str, books))}: run `tjs run`")
    return doc


def _typed_opening(source: Any) -> bool:
    """Whether a carry-forward's source label says the person gave the figure
    ([estimate] other_losses, --other-losses), not a lock."""
    from taxjson.bin.taxjson_carryover import TYPED_OPENING
    return str(source or "").startswith(TYPED_OPENING)


def _carried_in(root: Path, cfg: Dict[str, Any]) -> Optional[Tuple[float, bool]]:
    """(the net capital loss a plain `tjs estimate` takes into the project
    year, whether the person gave it) for a Canadian project: [estimate]
    other_losses, else the latest lock's balance, else 0 (CA-CARRY-02). `tjs
    carryover` asks on it, as the estimate does. None when it can't be told
    (another country, no year, a lock that can't be read)."""
    from taxjson.lib import carryforward as CF
    settings = settings_of(cfg)
    if _country(settings) != "canada" or settings.year is None:
        return None
    est = estimate_of(cfg)
    try:
        if est.has("other_losses"):
            return 0.0 + (est.other_losses or 0.0), True
        took = CF.resolve_losses(root, settings, "canada", False)
    except (CF.CarryInputError, TypeError, ValueError):
        return None
    return 0.0 + (took.get("other_losses") or 0.0), False


def _carry_back(root: Path, year: int, created: float = 0.0,
                filed_net: Optional[float] = None, balance: float = 0.0,
                opening: Optional[float] = None, typed: bool = False) -> CarryBack:
    """How much of `year`'s net capital loss `tjs carryover` finds carried
    back by [carryover] claimed (lines under earlier years). No row is an
    answer when a year has no dispositions, but not when `created`, a loss:
    its sales have nets, so the ledger has a row (backlog TJ-1424, TJ-1442);
    and not with a `balance` to split, the loss the year has left or took in: a
    carried-in balance can carry back in a year with no loss (TJ-1454), and only
    a row says whether it did (backlog TJ-1484).
    `filed_net`: the gain the filed return shows, for the ledger to fold on
    in place of the books' (backlog TJ-1452); without one, the books', whatever
    the year's own lock records (backlog TJ-1493). Raises LedgerUnavailable
    (`_ledger_doc`), also when the ledger can't tell whether an earlier return
    met a claim (lib/carry_back `cant_tell`) or which claimed years an earlier
    carry-back went to (`split_cant_tell`): the split would rest on a guess
    (backlog TJ-1574, TJ-4882)."""
    from taxjson.lib.carry_back import cant_tell_text, not_in_nets, split_left_out
    if not year:
        return CarryBack()
    # `opening`: the balance the caller's estimate took in, `typed` when the
    # person gave it. The ledger asks on the project's own (`_carried_in`): an
    # estimate run on another balance (--other-losses) has no split to read
    # off it (backlog TJ-4885).
    cfg = {} if opening is None else load_config(root, missing_ok=True)
    mine = _carried_in(root, cfg) if cfg else None
    if opening is not None and mine is not None and (
            abs(mine[0] - opening) > 0.005 or mine[1] != typed):
        raise LedgerUnavailable(
            f"`tjs carryover` folds [carryover] claimed on the balance this "
            f"project carries in ({M.shown(mine[0], 2):,.2f}), not on the "
            f"{M.shown(opening, 2):,.2f} given here (--other-losses): set "
            f"[estimate] other_losses to see this split")
    doc = _ledger_doc(root, "--on-books" if filed_net is None
                      else f"--filed-net={filed_net!r}")
    rows = [x for x in doc["rows"] if x["year"] == year]
    if not rows and (created > 0.005 or balance > 0.005):
        raise LedgerUnavailable(
            f"`tjs carryover --json` has no row for {year}, a year with a loss"
            + ("" if created > 0.005 else " carried in or left to carry"))
    try:
        why, off = cant_tell_text(doc), not_in_nets(doc)
    except ValueError as e:
        raise LedgerUnavailable(str(e)) from None
    if why:
        raise LedgerUnavailable(why)
    stated = {str(x["year"]): _ledger_amount(x, "carried_back_stated")
              for x in doc["rows"]
              if x["year"] < year and x.get("carried_back_stated") is not None}
    # The years a carry-back went to: this year's, and the earlier years' the
    # person gave (a lock's own record of its year is the lock's).
    to: Dict[str, float] = {}
    for x in rows:
        for t, a in _carried_back_to(x).items():
            to[str(t)] = M.shown(to.get(str(t), 0.0) + a, 2)
    said = [x.get("carried_back_to_said") or x.get("carried_back_split") for x in rows]
    stated_to = {str(x["year"]): {str(t): a for t, a in _carried_back_to(x).items()}
                 for x in doc["rows"]
                 if x["year"] < year and x.get("carried_back_to_said")
                 and x.get("carried_back_to_in_lock") != x["year"]}
    return CarryBack(sum(_ledger_amount(x, "carried_back") for x in rows),
                     split_left_out(doc, year), stated=stated or None,
                     not_in_nets=off or None, to=to or None,
                     to_split=next((s for s in said if s), None),
                     stated_to=stated_to or None)


def _stale_opening(root: Path, source: Any) -> Optional[str]:
    """Why the net capital loss a Canadian estimate took in can't be carried
    or locked: the close-year lock it came from (`source`, the estimate's
    `carry_sources` label) opened from an earlier record that was closed again
    since (`tjs close-year --force`), so its closing is built on a balance that
    record no longer carries. None when the figure is typed in, the lock names
    no record, or the record still carries what the lock opened with (backlog
    TJ-4883; `tjs carryover` warns of the same lock, TJ-1587)."""
    from taxjson.bin import taxjson_carryover as _co
    from taxjson.lib import filed_locks
    rec = filed_locks.record_of_label(root / "filed" / "lock.json", source)
    if rec is None:
        return None
    try:
        stale = _co.lock_figure(rec[1], "canada").get("opening_stale")
    except _co.LockUnreadable:
        return None
    if not stale:
        return None
    return (f"{rec[0]}'s close-year lock opened with "
            f"{M.shown(stale['opening'], 2):,.2f}, but the record it took that "
            f"from now carries {M.shown(stale['record_now'], 2):,.2f}: {rec[0]} "
            f"was not closed again after that record changed. Close {rec[0]} "
            f"again first (`tjs close-year --force` in {rec[0]}'s project), "
            f"then come back to this year")


def _claims_back(cfg: Dict[str, Any], year: Any) -> bool:
    from taxjson.lib.project_tables import claims_back
    return claims_back(cfg, year)


def _gain_left_out(root: Path, cfg: Dict[str, Any], settings: Settings,
                   ncl: Dict[str, Any], accepted: Optional[str]) -> None:
    """close-year (cli/file `_carryforwards_for_lock`): a lock with no loss to
    split whose split can still move: a gain on books that leave out a sale
    of the year with an unknown cost is no proof there is no loss, and a loss
    [carryover] claimed carries back (T1A); the ledger marks it where a result
    of such a sale can move what the year carries back. So the stop and the record of
    `_split_for_lock` follow, on the ledger's own rule (lib/carry_back
    `split_left_out`; backlog TJ-1488). Only with a claim in reach; the
    books' net, as the estimate's ledger folds it (a filed return's holds the
    year's sales: no call). A ledger that can't be used checks nothing here,
    and says so: no carry-back of the lock rests on it."""
    from taxjson.lib.carry_back import incomplete_record, split_left_out
    if not _claims_back(cfg, settings.year):
        return
    try:
        left = split_left_out(_ledger_doc(root, "--on-books"),
                              int(settings.year or 0))
    except LedgerUnavailable as e:
        _out.warn("could not check whether this year's sales with an unknown "
                  "cost can make a loss that [carryover] claimed carries back "
                  "(`tjs carryover`): the carryover ledger could not be used",
                  prog=_cmd_prog(), details=[str(e)])
        return
    if left:
        _stop_on_incomplete_split(left, accepted, int(settings.year or 0), False)
        ncl["carry_back_incomplete"] = incomplete_record(left)


def _stop_on_incomplete_split(incomplete: Dict[str, Any], accepted: Optional[str],
                              year: int, loss: bool) -> None:
    """close-year (cli/file `_split_for_lock`, `_gain_left_out`): the year's
    T1A carry-back rests on a ledger that left sales with an unknown cost out:
    stop, or, `accepted` (how: --force, or the yes of close-year's question),
    lock it and say so, naming them in the default view too. A stop with a way
    past it: an unknown cost can be settled, and no hand-reported result can
    be entered in the ledger (the owner's decisions, backlog TJ-1436, TJ-1460,
    TJ-1463). The words follow the year's row (`loss`: the year has a loss of
    its own) and the sales (lib/carry_back `split_words`; TJ-1487, TJ-1488)."""
    from taxjson.lib.carry_back import split_words
    sales = [f"- {s.get('date')} {s.get('symbol')}"
             + (f" ({s['account']})" if s.get("account") else "")
             + f": proceeds {M.shown(s.get('proceeds') or 0.0, 2):,.2f}"
             for s in incomplete.get("sales") or [] if _good_left_out_sale(s)]
    if len(sales) > 5:
        sales[5:] = [f"- and {len(sales) - 5} more (`tjs carryover --json`)"]
    said = split_words(incomplete, year, loss)
    if accepted:
        _out.show(_out.message(
            "warning", f"{said.accepted} ({accepted})",
            prog=_cmd_prog(), details=sales, stream=sys.stderr), sys.stderr)
        return
    _die(said.head, *sales, *said.why,
         "Give them a cost (`tjs find-missing-history`), `tjs run`, then "
         "close; or, if the cost stays unknown, pass --force to lock the "
         f"{said.locks} as computed. Nothing was written.")


def _radar_config(root: Path, prog: str = "tjs") -> Dict[str, Any]:
    """taxjson.toml for the radar family (wash-radar, watch, buy-check,
    sell-check): {} when the project has none — a bare work/ directory
    still runs — but a file that EXISTS and does not parse stops the
    command. Soft-reading it as {} fell back to globbing every
    *_base.json, sheltered books included, as TAXABLE: registered
    accounts' sales became "losses" with rescue advice to sell RRSP
    shares (2026-09 audit S048-13)."""
    # The one reader stops on a file that does not parse, naming the line
    # (a Notepad BOM is dropped, as every other command reads it: re-audit
    # A2-0470, A2-1454 to A2-1459, the S038-04 twin); until 2026-11-05 the
    # radar parsed it once more first, with its own wording (TJ-1645).
    return load_config(root, missing_ok=True)


def _taxable_equity_account_names(root: Path,
                                  prog: str = "tjs"
                                  ) -> Optional[List[str]]:
    """Wash-checkable taxable account names from taxjson.toml (no hard
    exit when the config is absent; an unreadable one stops — see
    _radar_config). Crypto accounts are excluded only for US projects
    (§1091 does not reach digital assets); Canada's superficial-loss
    rule covers any identical property, so Canadian crypto accounts are
    included — matching _wash_flags and the run pipeline's second pass.
    None means 'no config', so the caller falls back to globbing base
    files; an EMPTY list is a real answer (a US crypto-only project has
    nothing wash-checkable) — treating it as 'unknown' globbed the
    crypto books back in and applied §1091 to them (S046-10)."""
    cfg = _radar_config(root, prog)
    if not cfg or not (cfg.get("accounts") or {}):
        return None                 # no accounts table: glob work/
    crypto_covered = _country(settings_of(cfg)) != "usa"
    return [n for n, c in accounts_of(cfg).items()
            if c.taxable
            and (crypto_covered or not c.crypto)]


def _refuse_us_crypto_account(root: Path, account: Any,
                              prog: str, json_doc=None) -> None:
    """A US project's crypto account is outside the wash-sale rule
    (§1091 does not reach digital assets; the pipeline runs it with
    --no-wash): the explain and radar entry points said a loss the
    return allows in full was disallowed, and advised a trade to
    'rescue' it (S046-10). `json_doc`: as _no_wash_checkable's.
    `account`: None | a name | names (lib/account_list.names_of): every
    one named a US crypto account is nothing to check (exit 0, as for
    one); some of them, a list to give again without them (exit 1, as
    every refusal of a list). The comma text: ValueError (_listed_names)."""
    named = _listed_names(account)
    if not named:
        return
    cfg = _radar_config(root, prog)
    if _country(settings_of(cfg)) != "usa":
        return
    crypto = [n for n in named if account_of(accounts_of(cfg), n).crypto]
    if not crypto:
        return
    one = len(crypto) == 1
    what = (f"{crypto[0]} is a crypto account" if one else
            f"{', '.join(crypto)} are crypto accounts")
    if len(crypto) == len(named):
        _not_applicable(prog, f"{what} of a US project — the wash-sale "
                        f"rule (§1091) does not apply to "
                        f"{'it' if one else 'them'}, so there is nothing "
                        f"to check.", json_doc)
        return
    rest = ",".join(n for n in named if n not in crypto)
    _die(f"{what} of a US project: the wash-sale rule (§1091) does not "
         f"apply to {'it' if one else 'them'}",
         f"Leave {'it' if one else 'them'} out of the list: {rest!r}.")


_NO_WASH_CHECKABLE = (
    "no wash-checkable taxable account in taxjson.toml (sheltered "
    "accounts are context only, and a US project's crypto accounts are "
    "outside the wash-sale rule, §1091) — nothing to check.")


def _not_applicable(prog: str, why: str, json_doc=None) -> None:
    """Stop with exit 0: the wash rule has nothing to check here. In
    text, `why` after the command's name; with `json_doc` (the
    command's --json: a callable printing its empty document with
    `not_applicable` set to the `why` it is given, under the output's
    id, lib/json_contract), that document. The prose broke every
    --json consumer of the planning commands (backlog TJ-0330)."""
    if json_doc is not None:
        json_doc(why)
    else:
        from taxjson.lib.out import fill
        # A live exact Doc prints the line as the raw print() did.
        d = _out.Doc(exact=True, width_=0, live=True)
        d.line(fill(f"{prog}: {why}", hang="  "))
    raise SystemExit(0)


def _no_wash_checkable(prog: str, json_doc=None) -> None:
    """No wash-checkable taxable account (a US crypto-only project, or
    only sheltered accounts): _not_applicable."""
    _not_applicable(prog, _NO_WASH_CHECKABLE, json_doc)


def _check_books_before_merge(paths: List[Path]) -> None:
    """Load each base book a command is about to merge into a temp file,
    and die naming the damaged one. taxjson-merge passes a well-formed
    JSON file with a bad row straight through, so the engine's error
    named the deleted /tmp merge file instead of work/<acct>_base.json
    (A2-1143)."""
    from taxjson.lib.tx_load import load_transactions
    for b in paths:
        try:
            load_transactions(Path(b))
        except Exception as e:                      # noqa: BLE001
            msg = str(e).strip().splitlines()[0] if str(e).strip() \
                else type(e).__name__
            msg = msg.replace(f"load_transactions({b}): ", "")
            _die(f"cannot read {b}: {msg} — re-run `tjs run` to "
                 f"rebuild it.")


def _year_dir_arg(top: Path, year: Any) -> str:
    """The `-C` argument that names year folder `year` (in `top`) from
    the folder the command was run in: `2024` in the folder holding the
    years, `../2024` in a year folder (a bare `2024` there names
    2025/2024; backlog TJ-0262); the full path when it is further away.
    Quoted for the shell."""
    import os as _os
    import shlex as _shlex
    p = Path(top) / str(year)
    try:
        rel = _os.path.relpath(p)
    except ValueError:                      # another drive (Windows)
        rel = str(p)
    if rel.startswith(_os.pardir + _os.sep + _os.pardir):
        rel = str(p)
    return _shlex.quote(rel)


def _os_path_lexists(p: Path) -> bool:
    import os
    return os.path.lexists(p)


def _act_item(text: str, cmd: Optional[str] = None):
    """Doc.item() arguments for an `! ` act-on line (lib/out.act): one
    line at the house width, wrapped under itself only on a terminal
    narrower than it."""
    from taxjson.lib import out
    return out.act(text, cmd)[len(out.ACT):], "", out.ACT


# One canonical help string for every PERIOD positional (AUDIT-2026-07-ui A1).
_PERIOD_HELP = ("Window: 30d, 6w, 3m, 1y, mtd, ytd, all, a tax year "
                "(2025), or tax_year/ty for the config year "
                "(default: tax year)")


def _add_instrument_filters(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group(
        "instrument filters",
        "Restrict to instrument classes (combine freely; default: all). "
        "A futures option counts as both a future and an option.")
    g.add_argument("--options", action="store_true",
                   help="Option contracts (OCC symbols)")
    g.add_argument("--equities", action="store_true",
                   help="Equities/ETFs (neither options nor futures)")
    g.add_argument("--futures", action="store_true",
                   help="Futures (symbols starting with F:, / or "
                        "a backslash)")
    g.add_argument("--calls", action="store_true",
                   help="Call options only")
    g.add_argument("--puts", action="store_true",
                   help="Put options only")


# The carryover flag means what each country's return does with it
# (re-audit A2-0481: the help cited the Canadian 50% inclusion in a US
# project).
_OTHER_LOSSES_HELP = ("Prior-year capital loss carryover applied, in "
                      "FULL dollars (Canada: net capital losses, netted "
                      "against the gains before the 50%% inclusion; US: "
                      "the SHORT-term carryover — give the long-term one "
                      "with --long-term-losses)")


def _add_deduction_flags(p: argparse.ArgumentParser) -> None:
    """--deductions / --carrying-charges for the Canada estimate
    (`sum` and `estimate`); [estimate] deductions / carrying_charges
    are the config equivalents."""
    p.add_argument("--deductions", type=float, default=None,
                   metavar="AMT",
                   help="Canada: deductions from total income that the "
                        "AMT allows in full — RRSP (line 20800), FHSA, "
                        "RPP ... (default: [estimate] deductions, else 0)")
    p.add_argument("--carrying-charges", type=float, default=None,
                   metavar="AMT",
                   help="Canada: interest and carrying charges (line "
                        "22100), deducted in full from regular income "
                        "and at 50%% in the AMT base (default: "
                        "[estimate] carrying_charges, else 0)")
    p.add_argument("--long-term-losses", type=float, default=None,
                   metavar="AMT",
                   help="US: the LONG-term capital loss carryover "
                        "(Schedule D line 14), netted against long-term "
                        "gains first; --other-losses is then the "
                        "short-term one (default: [estimate] "
                        "long_term_losses, else 0)")


def _add_staking_parser(sub: argparse._SubParsersAction, name: str,
                        handler: Callable[[argparse.Namespace], None],
                        what: str) -> None:
    """`staking` and `staking-sum`: one option list, in two groups."""
    _staking = sub.add_parser(
        name, help=what,
        description=what + ". Uses the saved base-currency books and "
        "their source evidence. Missing commission evidence is unknown, "
        "not zero. Other crypto rewards are identified separately.")
    _staking.add_argument("period", nargs="?", help=_PERIOD_HELP)
    _staking.add_argument("account", nargs="?",
                          help="Account or comma-separated accounts "
                               "(default: crypto accounts)")
    _staking.add_argument("--broker", help="Exchange name, such as coinbase or kraken")
    _staking.add_argument("--symbol", help="Coin symbol as shown in the books")
    _staking.add_argument("--json", action="store_true", help="Emit the full evidence as JSON")
    # An ACCOUNT list (lib/account_list): `_staking_rows` reads it.
    _staking.set_defaults(account_list=True)
    _staking.set_defaults(func=handler)
