"""The Before you trade commands: wash-radar, buy-check, sell-check, rebuy, harvest, watch."""
import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from taxjson.project.accounts import account_of, accounts_of
from taxjson.project.settings import settings_of
from taxjson.lib import out as _out
from taxjson.lib import project_layout as _PL
from taxjson.lib.cli_diag import note
from taxjson.model.result import GainRow, gain_before_denial

from taxjson.cli.common import (
    _accounts, _accounts_skipped_for_no_inputs, _base_currency, _basis_label, _child_error,
    _exec_tool, _no_wash_checkable, _radar_config,
    _refuse_us_crypto_account, _require_listed, _self_cmd,
    _sheltered_expected, _taxable_equity_account_names,
)
from taxjson.pipeline.config import _country, load_config, load_settings
from taxjson.pipeline.inputs import _base, _normalize_country
from taxjson.pipeline.messages import _details, _die, _die_input
from taxjson.pipeline.run import _refuse_map_problems
from taxjson.pipeline.sanity import _json_out
from taxjson.pipeline.stages import (
    _radar_engine_args, _read_work_doc,
)
from taxjson.pipeline.tools import _cmd
from taxjson.lib.numeric import nonneg_float_arg as _nonneg_float_arg
from taxjson.model import money as M


def _radar_as_of(sidecar: Path) -> Optional[str]:
    """The day a wash-radar --json sidecar was computed as of
    (`as_of_date`, YYYY-MM-DD), None when it does not say or cannot be
    read (backlog TJ-1009)."""
    import json
    try:
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    d = doc.get("as_of_date") if isinstance(doc, dict) else None
    return d if isinstance(d, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", d) else None


def _live_radar_error(res) -> list:
    """The live radar's own error, for harvest's Warning when it failed
    (TJ-3315). Exact money's stop ends harvest instead, as it ends
    buy-check: a bug in taxjson, not a radar to go without (TJ-3329)."""
    if not res.returncode:
        return []
    if M.STOPPED in (res.stderr or ""):
        _die("the wash radar failed", _child_error(res.stderr))
    return [_child_error(res.stderr)]

def cmd_harvest(args: argparse.Namespace) -> None:
    """`tjs harvest [SYMBOL]`: unrealized gain/(loss) per open
    position at current prices — "if I sold this today, is it a loss?" —
    over every taxable account's wash-adjusted books, harvestable losses
    first, each loss annotated with the wash radar's advisory. Prices
    resolve IBKR -> yfinance -> cache (work/.price_cache.json). Runs
    with cwd=project root so the root ticker.map QUOTE lines are found. Accounts
    with `crypto = true` are excluded unless --crypto is given (the
    price chain serves stock snapshots)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = cfg["settings"]
    accounts_cfg = accounts_of(cfg)
    # Crypto accounts are EXCLUDED by default: the price chain serves
    # stock snapshots, so crypto symbols just spray yfinance lookup
    # errors. `--crypto` opts them back in.
    include_crypto = getattr(args, "crypto", False)

    def _skip_crypto(acfg) -> bool:
        return bool(acfg.crypto) and not include_crypto

    skipped = [n for n, c in sorted(accounts_cfg.items())
               if c.taxable and _skip_crypto(c)]
    taxable = [n for n, c in sorted(accounts_cfg.items())
               if c.taxable and not _skip_crypto(c)]
    if skipped:
        note("tjs harvest",
             f"crypto account(s) excluded: {', '.join(skipped)} "
             f"(pass --crypto to include them).")
    if not taxable:
        _die("no (non-crypto) taxable accounts",
             "Pass --crypto to harvest crypto accounts.")
    from taxjson.lib.report_model import resolve_gains_files
    files = []
    _no_input = _accounts_skipped_for_no_inputs(root)
    for name in taxable:
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            files.append(str(gains))
        elif name not in _no_input:
            from taxjson.lib.out import warn as _warn
            _warn(f"no gains file for taxable account {name!r} — run "
                  f"`tjs run` first.", prog="tjs harvest")
    if not files:
        _die("no taxable gains files in work/",
             "Run `tjs run` first.")
    # Its rename rules decide which option contract is quoted: with a
    # malformed `GLOBAL OLD.TO NEW.TO` dropped, the books' NEW contract
    # was quoted for the OLD one held, a loss read as a gain (backlog
    # TJ-0352, audit TJ-0399). Refused as `tjs run` refuses it.
    _strict_ticker_map(
        root, "its rename rules decide which contract an option row is "
        "quoted as and its SIMILAR lines the wash caveats, so a "
        "valuation without the rule can price another contract "
        "(`tjs run` refuses this map too)")
    cmd = _cmd("taxjson-harvest") + files + [
        "--price-cache", str(cache / ".price_cache.json"),
        "--basis-label", _basis_label(root, [Path(f) for f in files]),
        "--country", _country(settings),
        "--base-currency", str(_base(settings)),
    ]
    # --options was defined-but-never-forwarded: the wrapper (and the
    # GUI's include-options toggle, which calls through it) silently
    # ignored the flag (round-five audit finding 8).
    if getattr(args, "options", False):
        cmd.append("--options")
    # Crypto accounts are named to the tool, which decides by country
    # (US-PLAN-05: outside §1091 in a usa project; Canada unchanged).
    for name in taxable:
        if accounts_cfg[name].crypto:
            cmd += ["--crypto-account", name]
    rates = cache / "to_base.csv"
    if rates.exists():
        cmd += ["--rates", str(rates)]
    per_acct_sidecars = []
    for f in files:
        acct = Path(f).name
        for suf in ("_gains_wash.json", "_gains.json"):
            if acct.endswith(suf):
                acct = acct[: -len(suf)]
                break
        sidecar = root / "reports" / f"wash_radar_{acct}.json"
        if sidecar.exists():
            per_acct_sidecars.append(sidecar)
    radar_files = _harvest_radar_files(root, cache, settings, files, per_acct_sidecars)
    for sidecar in radar_files:
        cmd += ["--radar", str(sidecar)]
    _tmap = _PL.ticker_map_path(root)
    if _tmap.exists():
        cmd += ["--ticker-map", str(_tmap)]
    # Sheltered accounts' inventories feed the SH_QTY / SH_ADD columns:
    # shares held sheltered and days since the sheltered side last
    # acquired (a sheltered add within the 30-day window makes a
    # harvested loss permanently denied). Crypto sheltered accounts
    # follow the same --crypto gate as taxable ones.
    for name in sorted(n for n, c in accounts_cfg.items()
                       if not c.taxable
                       and not _skip_crypto(c)):
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            cmd += ["--sheltered", str(gains)]
    for sym in (args.symbol or []):
        cmd += ["--symbol", sym]
    if args.no_ibkr:
        cmd.append("--no-ibkr")
    if args.ibkr_port is not None:
        cmd += ["--ibkr-port", str(args.ibkr_port)]
    if args.json:
        cmd.append("--json")
    elif not _details(args):
        # Essentials first (docs/output-style.md): COLUMNS, NOTES and
        # the scope paragraph are the --details view.
        cmd.append("--brief")
    if args.verbose:
        cmd.append("--verbose")
    _exec_tool(cmd, cwd=str(root))


def _harvest_radar_files(root: Path, cache: Path, settings: Any, files: List[str],
                         per_acct_sidecars: List[Path]) -> List[Path]:
    """harvest: the wash-radar reports it reads, the combined one while
    it is fresh; reports older than the books or of an earlier day
    replaced by a live radar run (refused without the registered
    accounts' books), or none, with a warning, when that fails."""
    combined_sidecar = root / "reports" / "wash_radar_COMBINED.json"
    _fresh_refs = per_acct_sidecars or [Path(f) for f in files
                                        if Path(f).exists()]
    if combined_sidecar.exists() and all(
            combined_sidecar.stat().st_mtime >= s2.stat().st_mtime
            for s2 in _fresh_refs):
        # The cross-account radar (one run over every taxable base) —
        # a rebuy in a sibling taxable account is a trigger the
        # per-account sidecars can't see. Preferred only while FRESH:
        # a per-account sidecar rebuilt after it means the combined
        # view is stale and must not shadow it.
        radar_files = [combined_sidecar]
    else:
        radar_files = list(per_acct_sidecars)
    # A sidecar older than the books it describes — `run --account`
    # rebuilds an account's books (and sheltered_base.json) but skips
    # the cross reports — reported a registered-account buy's LOCKED
    # name as CLEAR / claimable now (2026-09 audit S038-09). Run the
    # radar live over the current books instead.
    # (is_file: a dangling *_base.json symlink was a FileNotFoundError
    # traceback, re-audit A2-1422)
    _books = [p for p in cache.glob("*_base.json")
              if not p.name.startswith(".") and p.is_file()]
    _newest_book = max((p.stat().st_mtime for p in _books), default=0.0)
    _old_books = bool(radar_files) and any(
        sc.stat().st_mtime < _newest_book for sc in radar_files)
    # A sidecar computed as of an EARLIER day is stale too, however new
    # its file: a registered account's purchase leaves the 30-day window
    # by the calendar alone, so the LOCKED units (at_risk_qty) a sale
    # today would lose shrink while the books stay as they are. harvest
    # counts the clear date down but took the units as timeless, and
    # said nothing was claimable now (backlog TJ-1009). The radar dates
    # its run by this computer's day, as here.
    _today_iso = datetime.now().strftime("%Y-%m-%d")
    _old_day = not _old_books and any(
        (_radar_as_of(sc) or _today_iso) < _today_iso
        for sc in radar_files)
    if _old_books or _old_day:
        if (not (cache / "sheltered_base.json").exists()
                and _sheltered_expected(root)):
            # A live radar without the registered accounts' books drops
            # their recent buys: a LOCKED loss read CLEAR, claimable now
            # (backlog TJ-1206). Refuse, as the checks do (TJ-0350).
            _die_input("no sheltered_base.json in work/: the registered "
                       "accounts' trades are missing from this harvest",
                       "A registered account's recent buy can deny a loss "
                       "for good, so the wash-radar reports cannot be "
                       "brought up to date without them. Run `tjs "
                       "run` first to rebuild it.")
        from taxjson.pipeline.dispatch import run_cmd as _run_live
        _live = cache / ".harvest_radar.json"
        _bases = _radar_taxable_bases(root, cache, "tjs harvest",
                                      empty_ok=True)
        _rcmd = _cmd("taxjson-wash-radar") + [
            "--taxable", *[str(b) for b in _bases],
            "--json-out", str(_live), "--account", "LIVE"]
        _rcmd += _radar_engine_args(_bases, root,
                                    _country(settings))
        if (cache / "sheltered_base.json").exists():
            _rcmd += ["--sheltered", str(cache / "sheltered_base.json")]
        _res = _run_live(_rcmd, capture_output=True)
        if _res.returncode == 0 and _live.exists():
            if _old_books:
                note("tjs harvest",
                     "the wash-radar reports are older than the books (a "
                     "single-account run?) — using a live radar",
                     details=["Run a full `tjs run` to refresh "
                              "reports/."])
            radar_files = [_live]
        else:
            from taxjson.lib.out import warn as _warn
            _warn("the wash-radar reports are "
                  + ("older than the books" if _old_books
                     else "of an earlier day")
                  + " and a live radar failed — the ADVISORY column is "
                  "left empty",
                  prog="tjs harvest",
                  details=["Losses count as 'no clear date'; run a full `tjs run`.",
                           *_live_radar_error(_res)])
            radar_files = []
    return radar_files


def _empty_radar_json(root: Path, args: argparse.Namespace):
    """The callable _not_applicable takes for `wash-radar --json`: it
    prints the radar's document with no rows (taxjson_wash_radar's
    payload keys) and `not_applicable` the why it is given."""
    def doc(why: str) -> None:
        from datetime import date as _date, datetime as _dt
        from taxjson.lib.wash_scope import scope_note
        country = _normalize_country(_country(
            settings_of(_radar_config(root, "tjs wash-radar"))))
        names = _accounts(args, root)
        _json_out({"country": country,
                   "generated_at": _dt.now().isoformat(timespec="seconds"),
                   "as_of_date": args.date or _date.today().isoformat(),
                   "account": args.account,
                   "account_names": (list(names) if names is not None
                                     else None),
                   "include_all": bool(args.all),
                   "rows_scoped": bool(args.account),
                   "scope_note": scope_note(country),
                   "sections": [], "similar": [],
                   "not_applicable": why}, schema="wash-radar")
    return doc if getattr(args, "json", False) else None


def cmd_wash_radar(args: argparse.Namespace) -> None:
    """Convenience wrapper over `taxjson-wash-radar`: resolve the taxable equity
    account base file(s) and the combined sheltered base, then run the radar
    live (against `--date`, defaulting to today) to stdout. All taxable accounts
    go into ONE invocation so the report — and its Definitions legend — prints
    once, in the fixed advisory order. Recomputes cooling-down windows as of now
    (the per-account reports/wash_radar_<account>.rpt are written by `tjs
    run`). An ACCOUNT scopes the rows shown (--show), never the books
    read: every taxable account still decides each status, as the
    engine pools them (audit 2026-10-23 #6)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    _na_json = _empty_radar_json(root, args)

    names = _accounts(args, root)
    if names is not None:
        bases, show = _radar_scoped_bases(root, cache, names, _na_json)
    else:
        bases = _radar_taxable_bases(root, cache, "tjs wash-radar",
                                     json_doc=_na_json)
        show = []
    # Its SIMILAR groups (lib/similar, below) and renames: the lenient
    # loader dropped a line it could not parse (backlog TJ-0352).
    _strict_ticker_map(
        root, "its rename and SIMILAR rules decide which listings the "
        "radar's caveats join, so a status without the rule can miss "
        "one (`tjs run` refuses this map too)")

    cmd = _cmd("taxjson-wash-radar") + ["--taxable", *[str(b) for b in bases]]
    cmd += show
    cmd += _radar_engine_args(
        bases, root,
        _country(_radar_config(root, "tjs wash-radar").get(
            "settings", {})))
    # Cross-account superficial-loss detection needs the pooled sheltered
    # history; pass it when the pipeline has built it.
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    elif _sheltered_expected(root):
        note("tjs wash-radar",
             "no sheltered_base.json in work/ — registered-account "
             "(permanent-denial) context disabled",
             details=["Run a full `tjs run` to build it."])
    if args.date:
        # Same shape+calendar validation as `list --date` — the
        # standalone fed the raw string straight into strptime, so a
        # typo'd date died with a traceback instead of a usage error.
        # A date whose 30-day window leaves the calendar Python can
        # hold (9999-12-31) is refused too (issue #54).
        from taxjson.lib.dates import as_of_date_problem
        _bad = as_of_date_problem(args.date)
        if _bad:
            _die_input(_bad)
        cmd += ["--date", args.date]
    # The SIMILAR groups of ticker.map (lib/similar): the radar lists
    # each group's statuses (a caveat; the funds stay two securities).
    from taxjson.lib import similar as _SIM
    for _g in _SIM.booked_groups(root):
        cmd += ["--similar", " ".join(_g)]
    if args.all:
        cmd += ["--all"]
    if getattr(args, "json", False):
        cmd += ["--json"]
    elif not (_details(args) or args.verbose):
        # Essentials first (docs/output-style.md): the stage's full
        # report (reports/wash_radar_*.rpt) is the --details view.
        # --verbose is that view too: the radar never read the flag,
        # so it changed nothing (backlog TJ-0156).
        cmd += ["--brief"]
    _exec_tool(cmd)


_RADAR_SUBJECT = ("The radar advises on TAXABLE loss sales (sheltered "
                  "books are its context, not its subject).")


def _radar_scoped_bases(root: Path, cache: Path, names: Tuple[str, ...],
                        na_json: Any) -> Tuple[List[Path], List[str]]:
    """`tjs wash-radar ACCOUNT[,ACCOUNT...]`: the taxable books (every
    one decides the statuses) and the child's --show of the named
    accounts' books, which scope the rows. Each named account needs a
    book and must be one the radar advises on: one name keeps its words
    and exit 2 (a US crypto account: nothing to check, exit 0); a list
    names each one that breaks a rule, exit 1."""
    own = [cache / f"{n}_base.json" for n in names]
    one = len(names) == 1
    if one and not own[0].exists():
        _die_input(f"no {own[0].name} in work/",
                   "Run `tjs run` first, or check the account "
                   "name.")
    if not one:
        _require_listed(names, [n for n, b in zip(names, own) if b.exists()],
                        "books",
                        fix="Run `tjs run` first, or check the names.")
    _acct_cfg = accounts_of(_radar_config(root, "tjs wash-radar"))
    shel = [n for n in names if account_of(_acct_cfg, n).sheltered]
    if one and shel:
        # A usage error (the wrong account for the radar): exit 2,
        # as an unknown account's (TJ-0157); 1 is a verdict's
        # status (backlog TJ-0601).
        _die_input(f"{shel[0]} is a sheltered account", _RADAR_SUBJECT)
    if shel:
        rest = ",".join(n for n in names if n not in shel)
        _die(f"{', '.join(shel)} "
             + ("is a sheltered account" if len(shel) == 1
                else "are sheltered accounts"),
             _RADAR_SUBJECT,
             f"Leave {'it' if len(shel) == 1 else 'them'} out of the "
             f"list: {rest!r}." if rest else
             "Name taxable accounts.")
    _refuse_us_crypto_account(root, names, "tjs wash-radar", na_json)
    # Every taxable book decides the statuses (the superficial-loss
    # pool and §1091 look across all your accounts); the accounts
    # only scope the rows shown. It used to read this book alone,
    # so a sibling account's rebuy or loss was invisible (audit
    # 2026-10-23 #6).
    bases = _radar_taxable_bases(root, cache, "tjs wash-radar",
                                 empty_ok=True)
    have = {b.resolve() for b in bases}
    for b in own:
        if b.resolve() not in have:
            bases.append(b)
            have.add(b.resolve())
    show = ["--show", *[str(b) for b in own]]
    for n in names:
        show += ["--account", n]
    return bases, show


def _radar_taxable_bases(root: Path, cache: Path,
                         prog: str, empty_ok: bool = False,
                         json_doc=None,
                         warn_missing: bool = True) -> List[Path]:
    """Taxable base files for a radar-style run. Prefer the config's
    wash-checkable taxable accounts (sheltered accounts are never
    radar'd; crypto is included only where the jurisdiction's wash
    rule covers it — see _taxable_equity_account_names). Fall back to
    globbing base files when there's no readable config. None of them
    stops the command (exit 0, _no_wash_checkable; `json_doc` prints
    its --json document). `warn_missing=False`: the caller says itself that
    a configured account has no books (watch: _radar_missing_context)."""
    names = _taxable_equity_account_names(root, prog)
    if names is not None and not names:
        if empty_ok:
            return []
        _no_wash_checkable(prog, json_doc)
    if names is not None:
        bases = [cache / f"{n}_base.json" for n in names
                 if (cache / f"{n}_base.json").exists()]
        # A configured taxable account without books used to vanish
        # from the checks in silence — a sibling's recent buy then read
        # as "SAFE — no tracked position" (S046-11).
        _skipped = _accounts_skipped_for_no_inputs(root)
        _missing = [n for n in sorted(names)
                    if not (cache / f"{n}_base.json").exists()
                    and n not in _skipped]
        if _missing and bases and warn_missing:
            from taxjson.lib.out import warn as _warn
            _warn(f"no books for taxable account(s) "
                  f"{', '.join(_missing)} (work/<account>_base.json "
                  f"missing)", prog=prog,
                  details=["Their trades are INVISIBLE to these window "
                           "checks, so a SAFE/CLEAR verdict here can be "
                           "wrong. Run `tjs run` to build them."])
    else:
        bases = [p for p in sorted(cache.glob("*_base.json"))
                 if not p.name.endswith("_raw_base.json")
                 and p.name != "sheltered_base.json"
                 and not p.name.startswith(".")]
    if not bases:
        from taxjson.lib.out import fail
        fail("no taxable base files in work/", prog=prog,
             details=["Run `tjs run` first."])
    return bases


def _radar_missing_context(root: Path, cache: Path) -> List[str]:
    """The work/ books a radar run should read and cannot, as
    `work/<file>` names: each wash-checkable taxable account's
    <account>_base.json, and the combined sheltered_base.json when the
    project has a sheltered account (an account `tjs run` skipped
    for having no inputs has no books to miss). [] with no readable
    accounts table (the radar then globs work/)."""
    names = _taxable_equity_account_names(root) or []
    skipped = _accounts_skipped_for_no_inputs(root)
    out = [f"work/{n}_base.json" for n in sorted(names)
           if n not in skipped and not (cache / f"{n}_base.json").exists()]
    if (_sheltered_expected(root)
            and not (cache / "sheltered_base.json").exists()):
        out.append("work/sheltered_base.json")
    return out


def _watch_threshold(args: argparse.Namespace) -> float:
    """--threshold for the harvest dimension; an explicit 0 means "any
    move" (`or 100.0` turned it into 100, R1-242)."""
    v = getattr(args, "threshold", None)
    return 100.0 if v is None else float(v)


def cmd_watch(args: argparse.Namespace) -> None:
    """`tjs watch`: report only what CHANGED since the last watch
    run — new/changed/cleared radar advisories, moved clear dates, and
    (with --harvest) the harvestable-now loss total. Quiet (no output,
    exit 0) when nothing changed, so a cron line mails only on news.
    State lives in work/.watch_state.json; the first run records a
    baseline. `--exit-code` exits 1 on changes for scripting (the
    default stays 0 so chains like `tjs run watch` never fail on
    a mere report)."""
    import json as _json
    from datetime import date as _date
    from taxjson.bin import taxjson_watch as _watch
    from taxjson.pipeline.dispatch import run_cmd as _run
    root = Path(args.dir).resolve()
    cache = root / "work"

    def _na_doc(why: str) -> None:
        # No wash-checkable account: watch's document, nothing tracked
        # and no state written (backlog TJ-0330).
        from taxjson.lib.wash_scope import scope_note
        _json_out({"baseline": False, "changes": [], "tracked": 0,
                   "actionable": 0, "as_of": _date.today().isoformat(),
                   "scope_note": scope_note(_country(settings_of(
                       _radar_config(root, "tjs watch")))),
                   "not_applicable": why}, schema="watch")
    bases = _radar_taxable_bases(
        root, cache, "tjs watch",
        json_doc=_na_doc if getattr(args, "json", False) else None,
        warn_missing=False)
    cmd = _cmd("taxjson-wash-radar") + [
        "--taxable", *[str(b) for b in bases], "--all", "--json"]
    _wcountry = _country(_radar_config(root, "tjs watch").get(
        "settings", {}))
    cmd += _radar_engine_args(bases, root, _wcountry)
    # A CLEAR is "safe as far as this project's accounts show"
    # (CA-PLAN-04 / US-PLAN-04, re-audit A2-0909).
    from taxjson.lib.wash_scope import scope_note as _scope_note
    _scope = _scope_note(_wcountry)
    state_path = _watch_state_path(args, root, cache)

    # The books the radar's verdicts need. Without the registered
    # accounts' combined books a LOCKED name read CLEAR, "safe to sell
    # at a loss", and that state replaced the baseline (backlog
    # TJ-0303); a taxable account's missing books hide its buys the
    # same way. Nothing is compared or saved until they are back.
    missing = _radar_missing_context(root, cache)
    if missing:
        since = _watch.baseline_date(state_path)
        kept = (f"the baseline of {since} is kept" if since
                else "no baseline is recorded")
        from taxjson.lib.out import warn as _warn
        _one = len(missing) == 1
        _warn(f"nothing compared: {', '.join(missing)} "
              f"{'is' if _one else 'are'} missing, and without "
              f"{'it' if _one else 'them'} the radar misses those "
              f"accounts' buys (a LOCKED name reads CLEAR); {kept}",
              prog="tjs watch",
              details=["Run a full `tjs run` to rebuild "
                       f"{'it' if _one else 'them'}, then `tjs "
                       "watch` again."])
        if getattr(args, "json", False):
            _json_out({"baseline": False, "changes": [],
                       "incomplete": missing, "tracked": None,
                       "actionable": None,
                       "as_of": _date.today().isoformat(),
                       "since": since, "scope_note": _scope}, schema="watch")
        return
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    res = _run(cmd, capture_output=True)
    if res.returncode != 0:
        _die("the wash radar failed", _child_error(res.stderr))
    try:
        radar_doc = _json.loads(res.stdout)
    except ValueError as e:
        _die(f"the wash radar emitted unparseable JSON: {e}")
    cur_radar = _watch.flatten_radar(radar_doc)
    as_of = radar_doc.get("as_of_date") or _date.today().isoformat()

    harvest_now, harvest_left, harvest_valued = _watch_harvest_total(args, root)

    def _save(harvest_value, harvest_omitted=None,
              valued=None) -> None:
        try:
            _watch.save_state(state_path, cur_radar, harvest_value, as_of,
                              harvest_omitted, valued)
        except OSError as e:
            _die_input(f"cannot write the watch state {state_path}: {e}",
                       "Pass a writable FILE path with --state.")

    _left_keys = [x["key"] for x in harvest_left]
    state = _watch.load_state(state_path)
    if state is None:
        _save(harvest_now, _left_keys if harvest_now is not None else None,
              harvest_valued if harvest_now is not None else None)
        actionable = sum(1 for r in cur_radar.values()
                         if r.get("category") in _watch._ACTIONABLE)
        if getattr(args, "json", False):
            _json_out({"baseline": True, "changes": [], "incomplete": [],
                       "tracked": len(cur_radar),
                       "actionable": actionable, "as_of": as_of,
                       "scope_note": _scope}, schema="watch")
        else:
            from taxjson.lib.out import fill
            # The baseline's two lines: a live exact Doc, each written as
            # the print() wrote it.
            d = _out.Doc(exact=True, width_=0, live=True)
            d.line(f"WATCH — baseline recorded (as of {as_of})")
            d.line(fill(f"{len(cur_radar)} ticker(s) tracked, {actionable} "
                        f"actionable advisories. Future runs report only "
                        f"changes."))
        return

    changes = _watch.diff_radar(state.get("radar") or {}, cur_radar)
    harvest_incomplete = _watch_harvest_compare(
        args, state, changes, harvest_now, harvest_left, harvest_valued, _left_keys, _save)

    actionable = sum(1 for r in cur_radar.values()
                     if r.get("category") in _watch._ACTIONABLE)
    if getattr(args, "json", False):
        _json_out({"baseline": False, "changes": changes,
                   "incomplete": harvest_incomplete,
                   "tracked": len(cur_radar),
                   "actionable": actionable,
                   "as_of": as_of, "since": state.get("as_of"),
                   "scope_note": _scope}, schema="watch")
    elif changes:
        d = _out.Doc(exact=True, width_=0, live=True)
        d.line(_watch.render_report(changes, as_of,
                                    since=state.get("as_of"),
                                    scope=_scope))
    if changes and getattr(args, "exit_code", False):
        raise SystemExit(1)


def _watch_state_path(args: argparse.Namespace, root: Path, cache: Path) -> Path:
    """watch: the state file, --state's (checked: a directory or an
    unwritable folder refused) or work/.watch_state.json."""
    if getattr(args, "state", None):
        state_path = Path(args.state)
        if not state_path.is_absolute():
            state_path = root / state_path
        # A directory, a path under a file or an unwritable place gave
        # an 11-line traceback (S046-12) — one line, like
        # --write-missing-history.
        if state_path.is_dir():
            _die_input(f"--state {args.state} is a directory",
                       f"Pass a FILE path, e.g. "
                       f"{state_path / 'watch_state.json'}")
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _die_input(f"cannot create the folder for --state "
                       f"{args.state}: {e}",
                       "Pass a FILE path in a writable folder.")
    else:
        state_path = cache / ".watch_state.json"
    return state_path


def _watch_harvest_total(args: argparse.Namespace, root: Path
                         ) -> Tuple[Optional[float], List[Dict[str, str]], List[str]]:
    """watch: this run's harvest dimension (with --harvest): the
    harvestable total from `tjs harvest --json`, or None when it is not
    run or fails; the positions the total leaves out; the ones it
    values."""
    import json as _json
    from taxjson.bin import taxjson_watch as _watch
    from taxjson.pipeline.dispatch import run_cmd as _run
    harvest_now = None
    # The positions this run's harvest total leaves out (backlog
    # TJ-0912): compared with the baseline's before the totals are.
    harvest_left: List[Dict[str, str]] = []
    harvest_valued: List[str] = []
    if (getattr(args, "threshold", None) not in (None, 100.0)
            and not getattr(args, "harvest", False)):
        note("tjs watch", "--threshold only applies with --harvest "
             "(ignored this run).")
    if getattr(args, "harvest", False):
        hcmd = _self_cmd(root, "harvest", "--json")
        if getattr(args, "no_ibkr", False):
            hcmd.append("--no-ibkr")
        hres = _run(hcmd, capture_output=True)
        if hres.returncode != 0:
            from taxjson.lib.out import warn as _warn
            _warn("harvest failed — the harvest dimension is skipped "
                  "this run", prog="tjs watch",
                  details=[_child_error(hres.stderr)])
        else:
            try:
                hdoc = _json.loads(hres.stdout)
                harvest_now = float(
                    ((hdoc.get("totals") or {}).get("harvestable")
                     or {}).get("now") or 0.0)
                harvest_left = _watch.harvest_left_out(hdoc)
                harvest_valued = _watch.harvest_valued(hdoc)
            except (ValueError, TypeError, AttributeError) as e:
                harvest_now = None
                from taxjson.lib.out import warn as _warn
                _warn(f"harvest JSON unreadable ({e}) — skipped this "
                      f"run.", prog="tjs watch")
    return harvest_now, harvest_left, harvest_valued


def _watch_harvest_compare(args: argparse.Namespace, state: Dict[str, Any],
                           changes: List[Dict[str, Any]], harvest_now: Optional[float],
                           harvest_left: List[Dict[str, str]], harvest_valued: List[str],
                           _left_keys: List[str], _save: Callable[..., None]) -> List[str]:
    """watch: this run's harvest total against the baseline's (a change
    added to `changes`), unless it leaves out a position the baseline
    valued (then the baseline is kept, and those positions returned);
    the state saved through `_save`."""
    from taxjson.bin import taxjson_watch as _watch
    harvest_incomplete: List[str] = []
    saved_left = state.get("harvest_omitted")
    saved_valued = state.get("harvest_valued")
    _cover = (_watch.harvest_coverage(saved_left, _left_keys, saved_valued)
              if harvest_now is not None
              and state.get("harvest_now") is not None else "same")
    if harvest_now is not None and _cover == "worse":
        # The total leaves out a position the baseline valued (its FX
        # rate or price is missing this run): the move is the coverage,
        # not the market. A "fell from 200 to 100" alert, then a "rose"
        # one when the rate came back, and the partial total replaced
        # the baseline (backlog TJ-0912). Nothing compared; the
        # baseline is kept.
        _was = set(saved_left or [])
        _new = [x["shown"] for x in harvest_left if x["key"] not in _was
                and (saved_valued is None or x["key"] in saved_valued)]
        harvest_incomplete = [f"harvest: {t}" for t in _new]
        from taxjson.lib.out import warn as _warn
        _details = ["`tjs harvest` names what is missing (a rate: "
                    "`tjs run`; a price: run it online)."]
        # It stays out until the user acts: every run ends here, and the
        # state's totals cannot be recomputed for the rest (TJ-1154).
        if any("cost basis unknown" in t for t in _new):
            _details.append(
                "A position left out for an unknown cost stays out until "
                "you give its cost on its OPENING line, and until you give "
                "its cost no move in any position is compared.")
        _warn(f"harvestable losses not compared: {len(_new)} "
              f"position(s) the baseline valued are left out this run "
              f"({', '.join(_new)}); the baseline of "
              f"{state.get('as_of') or '?'} is kept",
              prog="tjs watch", details=_details)
        saved_harvest = state.get("harvest_now")
        _save(saved_harvest, saved_left, saved_valued)
    elif harvest_now is not None:
        # A position left out that the baseline never valued is in
        # neither total: the totals still compare (backlog TJ-1040).
        _unvalued = [x["shown"] for x in harvest_left
                     if saved_left is not None
                     and x["key"] not in saved_left]
        if _unvalued:
            note("tjs watch", f"not in the harvestable total: "
                                  f"{', '.join(_unvalued)} (`tjs "
                                  f"harvest` names what is missing).")
        if _cover == "same":
            hch = _watch.diff_harvest(state.get("harvest_now"),
                                      harvest_now, _watch_threshold(args))
            if hch:
                changes.append(hch)
        # "better": it values positions the baseline left out — not
        # comparable either, but the fuller total becomes the baseline.
        saved_harvest = harvest_now
        _save(saved_harvest, _left_keys, harvest_valued)
    else:
        # This run didn't compute a harvest total (no --harvest, or a
        # transient harvest failure) — CARRY the previous baseline
        # forward. Dropping it meant alternating `watch` /
        # `watch --harvest` cron lines re-baselined every time and a
        # harvest change was never reported.
        saved_harvest = state.get("harvest_now")
        _save(saved_harvest, saved_left, saved_valued)
    return harvest_incomplete


def _radar_country_is_usa(root: Path) -> bool:
    """Wording only, AFTER _wash_class_context: the radar it ran was
    given the project's country through the one resolver (a missing or
    unknown country already stopped it), so this only picks the rule's
    name for the text."""
    from taxjson.lib.country import CountryError, canonical_country
    try:
        return canonical_country(load_settings(root, missing_ok=True).country) \
            == "usa"
    except CountryError:
        return False


def _us_crypto_coins(root: Path, cache: Path, prog: str
                     ) -> Dict[str, List[str]]:
    """{SYMBOL: [account, ...]} for the coins in a US project's crypto
    accounts' books ({} in Canada, where crypto accounts are radar'd
    like any other: CA-SL-13)."""
    import json as _json
    from taxjson.lib.option_symbols import is_option_symbol
    cfg = _radar_config(root, prog) or {}
    if _country(settings_of(cfg)) != "usa":
        return {}
    out: Dict[str, List[str]] = {}
    for n, c in sorted(accounts_of(cfg).items()):
        if not (c.taxable and c.crypto):
            continue
        try:
            doc = _json.loads((cache / f"{n}_base.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = (doc if isinstance(doc, list)
                else (doc.get("transactions") or [])
                if isinstance(doc, dict) else [])
        for t in rows:
            if not isinstance(t, dict):
                continue
            sy = str(t.get("symbol") or "").strip().upper()
            if sy and "." not in sy and not is_option_symbol(sy):
                if n not in out.setdefault(sy, []):
                    out[sy].append(n)
    return out


def _us_coin_answer(canon, radar: Dict[str, Dict[str, Any]],
                    q: str) -> Optional[List[str]]:
    """buy-check / sell-check lines for a bare coin query in a US
    project's crypto account, or None: the coin is outside the
    wash-sale rule (US-WASH-13; US-PLAN-05); an equity sharing its root
    (SAMPLT.US) keeps its own verdict under its own name."""
    coins = getattr(canon, "us_crypto_coins", None) or {}
    q = q.strip().upper()
    if q not in coins:
        return None
    lines = [f"{q}: a coin in crypto account(s) "
             f"{', '.join(coins[q])} — crypto is not subject to the "
             f"wash-sale rule (US-WASH-13): no wash-sale exposure."]
    others = sorted({t for t in radar
                     if t.strip().upper().rpartition(".")[0] == q})
    if others:
        lines.append(f"{', '.join(others)} "
                     f"{'is a separate listing' if len(others) == 1 else 'are separate listings'}"
                     f" — query {'it' if len(others) == 1 else 'them'} "
                     f"by name.")
    return lines


def _strict_ticker_map(root: Path, effect: str):
    """The project's ticker.map (tobase.map's pairs included) as a
    TickerMap, None when it has none — read strictly: the loader DROPS
    a line it cannot parse (a warning), and the verdict read without a
    dropped `GLOBAL OLD NEW` turned UNSAFE into SAFE (backlog TJ-0304),
    harvest quoted another option contract (TJ-0352) and lib/similar
    lost a SIMILAR group. A problem line stops the command as `tjs
    run` stops (`effect`: what a dropped rule changes here), and so
    does a map that cannot be read (audit A2-0683): both exit 2, an
    input to fix, never a verdict's 1 (backlog TJ-0351)."""
    _map_path = _PL.ticker_map_path(root)
    if not _map_path.exists():
        return None
    from taxjson.lib.map_file import load_map_file, map_file_problems
    try:
        _map_problems = map_file_problems(_map_path)
        if not _map_problems:
            return load_map_file(_map_path)
    except Exception as e:                                 # noqa: BLE001
        _die_input(f"ticker.map cannot be read ({e})",
                   "`tjs run` refuses it too; fix it first (its "
                   "cross-listing and rename rules decide which listings "
                   "are the same security).")
    _refuse_map_problems(_map_path, _map_problems, effect, code=2)
    return None


def _wash_class_context(root: Path, cache: Path, prog: str,
                        radar_args: Sequence[str] = (), json_doc=None):
    """(radar, canon, last_loss) shared by buy-check and sell-check:
    the combined radar document flattened per ticker, a symbol-class
    canonicalizer (known-exchange-suffix roots union-folded with the
    project's ticker.map pairs), and each class's most recent LOSING
    disposition from the taxable gains files (economic raw_gain, so a
    wash-denied loss still shows). `radar_args`: more radar options
    (rebuy's --date and --cleared-days). `json_doc`: prints the command's
    --json document when the project has no wash-checkable account
    (_no_wash_checkable)."""
    import json as _json
    from taxjson.bin.taxjson_watch import flatten_radar
    from taxjson.pipeline.dispatch import run_cmd as _run
    from taxjson.lib.report_model import resolve_gains_files
    bases = _radar_taxable_bases(root, cache, prog, json_doc=json_doc)
    # The map decides which listings are one security: refused, as
    # `tjs run` refuses it, before any verdict (backlog TJ-0304).
    _tm = _strict_ticker_map(
        root, "the map decides which listings are one security, so a "
        "verdict without the rule can miss a cross-listed loss and read "
        "SAFE (`tjs run` refuses this map too)")
    cmd = _cmd("taxjson-wash-radar") + [
        "--taxable", *[str(b) for b in bases], "--all", "--json"]
    _proj_country = _country(settings_of(_radar_config(root, prog)))
    cmd += _radar_engine_args(
        bases, root, _proj_country)
    cmd += list(radar_args)
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    elif _sheltered_expected(root):
        # Without the registered accounts' books their recent buys are
        # invisible, and a LOCKED loss sale read SAFE, exit 0, after an
        # Info line (backlog TJ-0350, audit TJ-0394): refuse, as `watch`
        # compares nothing (TJ-0303). Exit 2: an input to rebuild.
        _die_input("no sheltered_base.json in work/: the registered "
                   "accounts' trades are missing from this check",
                   "A registered account's recent buy can deny a loss "
                   "for good, so a verdict without them can read SAFE. "
                   "Run `tjs run` first to rebuild it.")
    res = _run(cmd, capture_output=True)
    if res.returncode != 0:
        _die("the wash radar failed", _child_error(res.stderr))
    radar = flatten_radar(_json.loads(res.stdout))

    # Identity comes ONLY from ticker.map (GLOBAL/TOBASE/JOURNAL), SPLIT
    # renames in the books, and an option's own underlying. Two listings
    # that merely share a root (SAMPLE.TO / SAMPLE.US) are NOT assumed to be the
    # same security: a CDR, a different issuer (SAMPLF.US an NYSE issuer vs
    # SAMPLF.TO a TSX ETF) or a share class would all be merged
    # wrongly by suffix stripping. The engine pools by exact mapped symbol,
    # and this matcher must agree with it.
    # (_tm: the map, read strictly above.)
    _distinct_pairs: set = (
        {frozenset(s.strip().upper() for s in p) for p in _tm.distinct}
        if _tm is not None else set())

    def _root(t: str) -> str:
        t = t.strip().upper()
        # An option is a right to acquire ITS underlying — identical
        # property for s.40(2)(g)/§1091 purposes — so it folds to the
        # underlying symbol exactly as parsed (suffix kept).
        from taxjson.lib.option_symbols import parse_option_underlying
        _u = parse_option_underlying(t)
        return _u.upper() if _u else t

    _parent: Dict[str, str] = {}

    def _find(x: str) -> str:
        while _parent.get(x, x) != x:
            x = _parent[x]
        return x

    def _union(a: str, b: str, why: str) -> None:
        ra, rb = _find(_root(a)), _find(_root(b))
        if ra == rb:
            return
        _parent[ra] = rb
        # A DISTINCT pair must never end up in one class — not even
        # transitively through a third symbol. Undo and say so.
        if any(_find(x) == _find(y) for x, y in
               (tuple(p) for p in _distinct_pairs if len(p) == 2)):
            del _parent[ra]
            from taxjson.lib.out import warn as _warn
            _warn(f"{why} would merge a DISTINCT pair from ticker.map "
                  f"({a} ~ {b}); the pair stays separate.", prog=prog)

    if _tm is not None:
        for _src, _dst in list(_tm.glob.items()) \
                + list(_tm.tobase.items()) \
                + list(_tm.journal.items()):
            _union(_src, _dst, "ticker.map")

    # SPLIT-renames are identical property too (the engine and the
    # radar match on the rename class): a loss on OLD.TO followed by
    # a NEW.TO rebuy is a violation that `sell-check OLD.TO` must
    # find under NEW.TO's radar row. Same union condition as
    # SplitTimeline.from_transactions (non-empty, non-self symbol_new).
    # Renames are DATED (A2-0197): a ticker that trades again after
    # its rename date names a different security NOW, so it is not
    # joined to the renamed holding (lib/renames).
    from taxjson.lib.renames import late_rows as _late_rows
    from taxjson.lib.rename_rows import rename_events as _rename_events
    _all_rows: List[Dict[str, Any]] = []
    for _bp in bases + ([sheltered_base] if sheltered_base.exists()
                        else []):
        try:
            _bdoc = _json.loads(_bp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # A bare-array book is a book (the radar's own read_json_doc
        # contract — audit A2-1180).
        _brows = (_bdoc if isinstance(_bdoc, list)
                  else (_bdoc.get("transactions", []) or [])
                  if isinstance(_bdoc, dict) else [])
        _all_rows += [_t for _t in _brows if isinstance(_t, dict)]
    _late_old = {e["old"] for _r, e in _late_rows(
        _all_rows, _rename_events(_all_rows), _proj_country)}
    for _t in _all_rows:
        if (_t.get("action") or "").upper() != "SPLIT":
            continue
        _new = (_t.get("symbol_new") or "").strip()
        _old = (_t.get("symbol") or "").strip()
        if _new and _old and _new.upper() != _old.upper():
            if _old in _late_old:
                continue
            _union(_old, _new, "SPLIT-rename in the books")

    # An option root that names no share listing in the books but
    # matches exactly ONE class share of that root on the same exchange
    # is that class: RBC books Rogers' Montreal options under the root
    # SAMPLD (SAMPLD271217C00030000.TO) while the shares are SAMPLD.B.TO, and the
    # call — a right to acquire those shares — fell into an empty
    # SAMPLD.TO class, so buy-check said SAFE (S047-01).
    from taxjson.lib.option_symbols import is_option_symbol as _is_opt
    _shares = {t.strip().upper() for t in radar if not _is_opt(t)}
    for _bp in bases:
        try:
            _bdoc = _json.loads(_bp.read_text(encoding="utf-8"))
            _brows = (_bdoc if isinstance(_bdoc, list)
                      else _bdoc.get("transactions", []) or [])
            for _t in _brows:
                _sy = str(_t.get("symbol") or "").strip().upper()
                if _sy and not _is_opt(_sy):
                    _shares.add(_sy)
        except (OSError, ValueError, AttributeError):
            continue
    _by_root: Dict[str, set] = {}
    for _sy in _shares:
        _m = re.fullmatch(r"(.+)\.([A-Z]{1,2})\.([A-Z]{1,3})", _sy)
        if _m:
            _by_root.setdefault(f"{_m.group(1)}.{_m.group(3)}",
                                set()).add(_find(_sy))
    _class_alias = {r: next(iter(cs)) for r, cs in _by_root.items()
                    if len(cs) == 1 and r not in _shares
                    and _find(r) == r}

    def canon(t: str) -> str:
        r = _find(_root(t))
        if _is_opt(t.strip().upper()) and r in _class_alias:
            return _class_alias[r]
        return r

    # Suffix-less listings in the books — crypto coins (ETH): a bare
    # query that names one of them means that listing, never also the
    # equity sharing its root (SAMPLT.US) (S047-06).
    canon.bare_listings = {_sy for _sy in _shares if "." not in _sy}
    # One option contract, two spellings: a ticker.map line pairing two
    # option symbols (GLOBAL SYN<series>.TO ALT<series>.TO) makes them
    # the SAME contract, as the engine books it. The class fold above
    # joins only their underlyings, so the exact-series filters
    # dropped the booked spelling's loss and `buy-check SYN<series>.TO`
    # answered SAFE (backlog TJ-1000). {option: its one spelling}.
    _opt_parent: Dict[str, str] = {}
    # A share line on one market (GLOBAL SYN.TO ALT.TO) books the
    # options too (ticker_map.map_symbol): a spelling the books do not
    # hold is the contract the line rewrites it to (backlog TJ-1172). A
    # spelling the books hold is its own contract (the engine keeps a
    # collision apart). Rules chain, as in the engine (backlog TJ-1301).
    # Between markets a code can name two contracts
    # (guard_option_listing_collisions): a line there rewrites only a
    # spelling the parsed rows hold, the rows booked under the line's
    # target (backlog TJ-1280). A native Montreal contract stays its own.
    _share_rules: Dict[str, str] = {}
    _cross_rules: Dict[str, str] = {}

    def _opt_find(x: str) -> str:
        while _opt_parent.get(x, x) != x:
            x = _opt_parent[x]
        return x
    if _tm is not None:
        for _src, _dst in list(_tm.glob.items()) \
                + list(_tm.tobase.items()) \
                + list(_tm.journal.items()):
            _s, _d = str(_src).strip().upper(), str(_dst).strip().upper()
            if _s != _d and _is_opt(_s) and _is_opt(_d):
                _rs, _rd = _opt_find(_s), _opt_find(_d)
                if _rs != _rd:
                    _opt_parent[_rs] = _rd
            elif _s != _d and not _is_opt(_s) and not _is_opt(_d):
                (_share_rules
                 if _s.rpartition(".")[2] == _d.rpartition(".")[2]
                 else _cross_rules)[_s] = _d
    _booked = {str(_t.get("symbol") or "").strip().upper()
               for _t in _all_rows}

    _parsed: List[set] = []     # _parsed_symbols, read once if needed

    def _option_spelling(x: str) -> str:
        from taxjson.lib.ticker_map_rules import map_symbol
        if x in _booked or x in _opt_parent or not _is_opt(x):
            return x
        if _cross_rules and not _parsed:
            _parsed.append(_parsed_symbols(cache))
        _rules = (dict(_share_rules, **_cross_rules)
                  if _parsed and x in _parsed[0] else _share_rules)
        _seen = {x}
        while True:
            _n = map_symbol(x, _rules)
            if _n in _seen:
                return x
            _seen.add(_n)
            x = _n
            if x in _booked:
                return x
    canon.option_id = lambda t: _opt_find(
        _option_spelling(str(t).strip().upper()))
    # The share listings each class joins (ticker.map / tobase.map
    # pairs, renames, the books' own listings): `rebuy` names the other
    # listings a buy of which counts too (XYZ.US for XYZ.TO).
    _members: Dict[str, set] = {}
    for _sy in set(_parent) | _shares:
        if _sy and not _is_opt(_sy):
            _members.setdefault(canon(_sy), set()).add(_sy)
    canon.members = {k: sorted(v) for k, v in _members.items()}
    # A US project leaves its crypto accounts out of the radar (§1091
    # does not reach digital assets, US-WASH-13), so a coin there is
    # in no radar row: `buy-check SAMPLT` took SAMPLT.US's verdict and
    # `sell-check BTC` said "no tracked taxable position" (audit
    # A2-0749, A2-0750, A2-1340). {coin: [crypto accounts]}.
    canon.us_crypto_coins = _us_crypto_coins(root, cache, prog)
    # The classes ticker.map SIMILAR lines put beside each class
    # (lib/similar): a caveat in buy-check / sell-check, the listings
    # `rebuy` names as flagged — never one class (the engine keeps them
    # apart, and so must this matcher).
    from taxjson.lib import similar as _SIM
    _sim: Dict[str, set] = {}
    for _g in _SIM.groups(root):
        _roots = {canon(_s) for _s in _g}
        for _r in _roots:
            _sim.setdefault(_r, set()).update(_roots - {_r})
    canon.similar = {k: sorted(v) for k, v in _sim.items() if v}

    _acct_cfg = accounts_of(load_config(root, missing_ok=True))
    _taxable = {a for a, c in _acct_cfg.items() if c.taxable}
    last_loss = _last_loss_by_class(
        resolve_gains_files(cache), canon, _taxable,
        usa=_radar_country_is_usa(root))
    return radar, canon, last_loss


_OPTION_LOSS_KEY = "option:"


def _parsed_symbols(cache: Path) -> set:
    """Every symbol the books' per-file parses hold, before ticker.map
    (work/<account>_<file>.json; the merged books hold mapped symbols)."""
    import json as _json
    derived = ("_base.json", "_raw.json", "_gains.json", "_gains_wash.json",
               "_report.json", "_filled.json", "_sorted.json")
    out: set = set()
    for p in cache.glob("*_*.json"):
        if p.name.startswith(".") or p.name.endswith(derived):
            continue
        try:
            doc = _json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = doc if isinstance(doc, list) else (
            doc.get("transactions") if isinstance(doc, dict) else None)
        out.update(str(t.get("symbol") or "").strip().upper()
                   for t in rows or [] if isinstance(t, dict))
    return out


def _option_id(canon, symbol: str) -> str:
    """`symbol` (upper-cased) as the one spelling of its contract: an
    option paired with another option symbol by ticker.map is the same
    contract under either name (canon.option_id, backlog TJ-1000); any
    other symbol is itself."""
    s = str(symbol).strip().upper()
    f = getattr(canon, "option_id", None)
    return f(s) if f is not None else s


def _last_loss_for(last_loss: Dict[str, Dict[str, Any]], want: str,
                   wroot: str, mode: str, closes_short: bool = False,
                   canon=None):
    """The last loss a trade in `want` can affect: a share query sees
    the class's share losses; an option query sees its own contract's
    (either spelling ticker.map pairs, `canon`), plus (a call BUY that
    opens a position) the underlying's share losses (one-way option
    rule, audit A2-1172)."""
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_right
    q = want.strip().upper()
    if not is_option_symbol(q):
        return last_loss.get(wroot)
    cands = [last_loss.get(_OPTION_LOSS_KEY + _option_id(canon, q))]
    if mode == "buy" and parse_option_right(q) == "C" and not closes_short:
        cands.append(last_loss.get(wroot))
    cands = [c for c in cands if c]
    return max(cands, key=lambda c: c["date"]) if cands else None


def _last_loss_by_class(gains_files, canon, taxable, *, usa: bool
                        ) -> Dict[str, Dict[str, Any]]:
    """Each symbol class's most recent LOSING disposition in the taxable
    gains files (economic raw_gain, so a denied loss still shows), dated
    on the country's window basis — the SETTLE date under s.54, the
    TRADE date under §1091 — so the "N days ago / INSIDE the window"
    line agrees with the radar's verdict at the day-30 edge (audit
    S050-00; it used settle dates in US projects too)."""
    last_loss: Dict[str, Dict[str, Any]] = {}
    for _a, _p in gains_files.items():
        if taxable and _a not in taxable:
            continue
        try:
            _doc = _read_work_doc(Path(_p))
        except (OSError, ValueError) as e:
            # The context line vanished in silence (S047-03).
            from taxjson.lib.out import warn as _warn
            _warn(f"could not read {Path(_p).name} ({e})",
                  prog="tjs",
                  details=[f"The 'last loss sale' line leaves out account "
                           f"{_a}; re-run `tjs run`."])
            continue
        # A sale routed to manual reporting (unknown cost) is still a
        # loss sale for the window: its row has no 'gain', only the
        # engine's raw_gain (S047-02).
        _rows = [(t, False) for t in _doc.get("transactions", [])] + [
            (t, True) for t in _doc.get("manual_reporting_required") or []]
        for _t, _routed in _rows:
            _v = GainRow(_t)
            if not _v.quantity:
                continue
            if _routed:
                if _v.raw_gain is None:
                    continue
                _g = float(_v.raw_gain or 0.0)
            else:
                if "gain" not in _t:
                    continue
                _g = float(gain_before_denial(_v) or 0.0)
            if _g >= 0:
                continue
            # An option's loss is keyed by its own contract, never by the
            # underlying's class: shares never replace an option, so a
            # share query's context line must not show a written call's
            # buy-back loss as INSIDE its window (audit A2-1172).
            _sym = str(_t.get("symbol") or "")
            from taxjson.lib.option_symbols import is_option_symbol as _is_o
            _c = (_OPTION_LOSS_KEY + _option_id(canon, _sym)
                  if _is_o(_sym.strip().upper()) else canon(_sym))
            _d = str((_t.get("date") or _t.get("date_settle")) if usa
                     else (_t.get("date_settle") or _t.get("date")) or "")
            _prev = last_loss.get(_c)
            if _prev is None or _d > _prev["date"]:
                last_loss[_c] = {"date": _d,
                                 # The window date's kind, printed with
                                 # it: the radar labels the same sale by
                                 # its trade date (S047-05).
                                 "date_kind": "traded" if usa else "settled",
                                 "symbol": _t.get("symbol"),
                                 "gain": M.shown(_g, 2),
                                 # SHORT: the loss was on covering a
                                 # short, not a sale (backlog TJ-0122).
                                 "direction": _t.get("direction") or "LONG",
                                 "unknown_cost": _routed,
                                 "account": _t.get("account") or _a}
    return last_loss


def _fold_class_separator(symbol: str) -> str:
    """A share-class ticker typed the broker/Yahoo way (SAMPLD-B, 'SAMPLD B',
    SAMPLD/B, SAMPLD-B.TO) in the books' dotted spelling (SAMPLD.B, SAMPLD.B.TO).
    Unfolded, the query matched nothing and buy-check answered SAFE
    while a loss on SAMPLD.B.TO sat inside the window (S007-02). Option
    symbols pass through unchanged."""
    from taxjson.lib.futures_prefixes import canonical_futures_symbol
    from taxjson.lib.option_symbols import is_option_symbol
    # A future's `/` or `\` prefix is `F:`, as the books spell it
    # (lib/futures_prefixes, backlog TJ-5150).
    s = canonical_futures_symbol(" ".join(str(symbol).split()).upper())
    if is_option_symbol(s):
        return s
    m = re.fullmatch(r"([A-Z0-9]+)[-/ ]([A-Z]{1,2})((?:\.[A-Z]{1,3})?)", s)
    return f"{m.group(1)}.{m.group(2)}{m.group(3)}" if m else s


def _bare_listings(radar: Dict[str, Dict[str, Any]], canon,
                   q: str) -> List[str]:
    """The listings a BARE ticker (no exchange suffix) names: those in
    the books that carry it, q.<exchange> joined to the books by a
    ticker.map rule, and q.<exchange> a ticker.map SIMILAR line names
    (never held: the look-alike a tax-loss swap is about to buy, whose
    caveat `buy-check ZZH` dropped while `buy-check ZZH.TO` gave it;
    workflow audit 2026-10-22 #8). Each listing keeps its own class."""
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    q = q.strip().upper()
    booked = {canon(t) for t in radar}
    similar = set(getattr(canon, "similar", None) or ())
    listings = [t for t in radar
                if t.strip().upper().rpartition(".")[0] == q
                or t.strip().upper() == q]
    for sx in sorted(KNOWN_SUFFIXES):
        t = f"{q}.{sx}"
        if t in listings:
            continue
        c = canon(t)
        if (c != t and c in booked) or c in similar:
            listings.append(t)
    return listings


def _class_matches(radar: Dict[str, Dict[str, Any]], canon, want: str):
    """(class root, {ticker: row}, note) for one queried symbol.

    A query WITH an exchange suffix (SAMPLE.TO) names one listing and sees
    exactly that listing's class — the listings ticker.map joins to it,
    its split renames and its options. A BARE query (SAMPLE, SAMPLC.B) names
    no listing: it is resolved to every listing that carries that
    ticker, and each of those keeps its own class. Nothing is ever
    merged by stripping a suffix — only ticker.map makes two listings
    one security. When the bare ticker resolves to more than one class
    the worst verdict is shown with a note asking for an explicit
    spelling."""
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    q = want.strip().upper()
    base, _, ext = q.rpartition(".")
    if base and ext in KNOWN_SUFFIXES:
        wroot = canon(q)
        return wroot, {t: r for t, r in radar.items()
                       if canon(t) == wroot}, None
    _bare = set(getattr(canon, "bare_listings", None) or ())
    if q in _bare or any(t.strip().upper() == q for t in radar):
        # The books hold a suffix-less listing of exactly this name (a
        # coin): that IS the security asked about. `buy-check ETH` gave
        # SAMPLT.US's verdict too, and nothing could ask about the coin
        # alone (S047-06); the equity keeps its own spelling.
        wroot = canon(q)
        others = sorted({t for t in radar
                         if t.strip().upper().rpartition(".")[0] == q
                         and canon(t) != wroot})
        note = (f"{q}: this is the {q} listing in the books (a coin or "
                f"other suffix-less symbol); {', '.join(others)} "
                f"{'is a separate listing' if len(others) == 1 else 'are separate listings'}"
                f" — query {'it' if len(others) == 1 else 'them'} by "
                f"name." if others else None)
        return wroot, {t: r for t, r in radar.items()
                       if canon(t) == wroot}, note
    listings = _bare_listings(radar, canon, q)
    classes = sorted({canon(t) for t in listings}) or [canon(q)]
    matches = {t: r for t, r in radar.items() if canon(t) in classes}
    note = None
    if len(classes) > 1:
        note = (f"{q}: ambiguous — {', '.join(sorted(listings))} are "
                f"separate listings (only ticker.map makes two listings "
                f"one security); this is the worst verdict across them. "
                f"Query one explicitly.")
    return classes[0], matches, note


def _replacement_rows(want: str, matches: Dict[str, Dict[str, Any]],
                      mode: str, closes_short: bool = False, canon=None
                      ) -> Dict[str, Dict[str, Any]]:
    """Keep the radar rows a trade in `want` can actually affect (ITA
    s.54, one-way option rule): buying SHARES touches share losses only
    (shares never replace an option); buying a CALL also touches the
    underlying's share losses (a right to acquire them) and its own
    contract; any other option — and SELLING an option — touches only
    the identical contract (a different series never replaces it) —
    under either spelling a ticker.map line pairs (`canon`, backlog
    TJ-1000)."""
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_right
    q = want.strip().upper()
    if not is_option_symbol(q):
        return {t: r for t, r in matches.items()
                if not is_option_symbol(t)}
    # A buy that closes a written call acquires nothing (CA-SL-05,
    # US-WASH-12; audit A2-0370): it touches only its own contract.
    from taxjson.lib.option_symbols import _FUTURES_PREFIX_RE, parse_option_underlying
    # A futures option is never sized as a replacement of the futures
    # loss (CA-SL-15 / US-WASH-15): buy-check flags it instead.
    call_buy = (mode == "buy" and parse_option_right(q) == "C"
                and not closes_short
                and not _FUTURES_PREFIX_RE.match(
                    parse_option_underlying(q) or ""))
    qid = _option_id(canon, q)
    return {t: r for t, r in matches.items()
            if _option_id(canon, t) == qid
            or (call_buy and not is_option_symbol(t))}


def _similar_lines(radar: Dict[str, Dict[str, Any]], canon, wroot: str,
                   q: str, mode: str, usa: bool) -> List[str]:
    """buy-check's / sell-check's caveat lines for the share rows of the
    classes ticker.map declares SIMILAR to the queried class (lib/
    similar, CA-SL-20 / US-WASH-26): on a buy, a similar fund sold at a
    loss inside the window; on a sale, one bought inside it. A caveat
    (SAFE*), never a verdict: the books keep the funds apart."""
    from taxjson.lib import similar as _SIM
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    from taxjson.lib.option_symbols import is_option_symbol
    q = q.strip().upper()
    simmap = getattr(canon, "similar", None) or {}
    roots = {wroot}
    base, _, ext = q.rpartition(".")
    if not (base and ext in KNOWN_SUFFIXES) and q not in set(
            getattr(canon, "bare_listings", None) or ()):
        # A bare query may name several classes (the worst verdict is
        # shown): each one's look-alikes are a caveat, none dropped.
        roots |= {canon(t) for t in _bare_listings(radar, canon, q)}
    sim = set()
    for _r in roots:
        sim.update(simmap.get(_r) or [])
    sim -= roots
    if not sim or is_option_symbol(q):
        return []
    want = _SIM.LOSS_CATEGORIES if mode == "buy" else _SIM.BUY_CATEGORIES
    out = []
    for t, r in sorted(radar.items()):
        tu = t.strip().upper()
        if is_option_symbol(tu) or canon(tu) not in sim:
            continue
        cat = r.get("category") or ""
        if cat in want:
            out.append(_SIM.caveat(tu, cat, q, mode,
                                   "usa" if usa else "canada"))
        elif mode == "sell" and isinstance(r.get("sheltered_buy"), dict):
            # Held only in a registered account / IRA that bought it in
            # the window: the radar files it CLEAR (nothing taxable to
            # sell), but the buy is "in any of your accounts" (CA-SL-20
            # / US-WASH-26; workflow audit 2026-10-22 #13).
            out.append(_SIM.caveat(tu, cat, q, mode,
                                   "usa" if usa else "canada",
                                   sheltered_buy=r["sheltered_buy"]))
        elif mode == "sell" and isinstance(r.get("recent_buy"), dict):
            # Bought inside the window under another status — a
            # VIOLATION's own replacement: the category says nothing of
            # the buy, and sell-check said plain SAFE while the run
            # warns after the sale (backlog TJ-0449).
            out.append(_SIM.caveat(tu, cat, q, mode,
                                   "usa" if usa else "canada",
                                   bought=r["recent_buy"]))
    return out


_FLAG_WORDS = {
    "right_vs_share_loss": "a warrant/right on",
    "adjusted_option_vs_loss": "a call on an adjusted option series of",
    "futures_option_vs_loss": "a call on the futures contract of",
}


def _flagged_share_rows(radar: Dict[str, Dict[str, Any]], q: str):
    """[(ticker, row, rule)] for the share rows a purchase of `q` would
    be flagged against (never sized): `q` a warrant/right on them, a call
    on an adjusted series of them (root + digit), or a call on their
    futures contract — the engines' warn-only rules (core.
    detect_right_replacement_matches / detect_unresolved_option_
    replacement_matches)."""
    from taxjson.engine.assign import _split_underlying
    from taxjson.engine.replacement import right_underlying
    from taxjson.lib.futures_prefixes import canonical_futures_symbol
    from taxjson.lib.option_symbols import (root_matches_stock,
                                            is_option_symbol, parse_option_right,
                                            parse_option_underlying)
    q = q.strip().upper()
    if is_option_symbol(q):
        if parse_option_right(q) != "C":
            return []
        und = parse_option_underlying(q) or ""
        b, ext = _split_underlying(und)
        b = canonical_futures_symbol(b)
        if b.startswith("F:"):
            kind = "futures_option_vs_loss"
        elif b[-1:].isdigit():
            kind = "adjusted_option_vs_loss"
        else:
            return []
    else:
        und = right_underlying(q)
        if not und:
            return []
        b, ext = _split_underlying(und)
        kind = "right_vs_share_loss"
    out = []
    for t, r in radar.items():
        tu = t.strip().upper()
        if is_option_symbol(tu) or tu == q:
            continue
        sb, sext = _split_underlying(tu)
        sb = canonical_futures_symbol(sb)
        if sext != ext:
            continue
        if kind == "right_vs_share_loss":
            if sb != b:
                continue
        elif not root_matches_stock(b, sb):
            continue
        out.append((t, r, kind))
    return sorted(out, key=lambda x: x[0])


def _short_cover_loss_date(r: Dict[str, Any]) -> Optional[str]:
    """The date of a radar row's recent loss when every loss still in
    its window was on covering a short (direction SHORT), else None:
    buy-check / sell-check word it as a cover, never as a sale (backlog
    TJ-0122)."""
    live = [l for l in r.get("losses") or [] if not l.get("cleared")]
    if live and all(l.get("direction") == "SHORT" for l in live):
        return max(str(l.get("trade_date") or "") for l in live) or None
    return None


def _passed_violation_text(r: Dict[str, Any]) -> str:
    """A settled VIOLATION's advisory (its rescue date has passed)
    without the forward view the radar appends to it ("For a loss sale
    today: …"), which sell-check / buy-check state as its own line
    (backlog TJ-0775)."""
    adv = str(r.get("advisory") or "")
    fwd = r.get("forward")
    if isinstance(fwd, dict) and fwd.get("advisory"):
        adv = adv.replace(f" For a loss sale today: {fwd['advisory']}", "")
    return adv


def _with_forward_rows(matches: Dict[str, Dict[str, Any]]):
    """(ticker, row) of `matches` in ticker order; after a VIOLATION
    whose rescue date has passed, its `forward` view (what a loss sale
    or a buy TODAY meets: a registered account's recent buy still held
    is LOCKED) as a row of its own, marked `after_passed`. The settled
    loss masked it: sell-check said SAFE* before a sale the rule denies
    (backlog TJ-0775)."""
    for t, r in sorted(matches.items()):
        yield t, r
        fwd = r.get("forward")
        if ((r.get("category") or "") == "VIOLATION"
                and r.get("deadline_passed") and isinstance(fwd, dict)
                and fwd.get("category")):
            yield t, {**r, "category": fwd.get("category"),
                      "advisory": fwd.get("advisory") or "",
                      "clears_at": fwd.get("clears_at"),
                      "at_risk_qty": fwd.get("at_risk_qty"),
                      "deadline_passed": False, "forward": None,
                      "notes": [], "after_passed": True}


def _window_over(r: Dict[str, Any]) -> bool:
    """A VIOLATION whose rescue date has passed and none of whose
    losses a buy today can still replace (each one's re-entry date has
    come, the radar's `losses`): its window is over for a buyer, as
    `rebuy` lists it (backlog TJ-0775). An older radar without the
    losses is not judged."""
    losses = r.get("losses")
    return ((r.get("category") or "") == "VIOLATION"
            and bool(r.get("deadline_passed"))
            and isinstance(losses, list) and bool(losses)
            and not any(not x.get("cleared") and x.get("buy_replaces")
                        for x in losses if isinstance(x, dict)))


def _loss_within_30(r: Dict[str, Any]) -> str:
    """'a loss sold within the past 30 days', or its short-cover form."""
    d = _short_cover_loss_date(r)
    return (f"a loss on covering a short ({d}) within the past 30 days"
            if d else "a loss sold within the past 30 days")


def _last_loss_line(ll) -> Optional[str]:
    if not ll:
        return None
    from datetime import date as _date
    try:
        _ago = (_date.today() - _date.fromisoformat(ll["date"])).days
        # `date` is the country's window date (settle for Canada): a
        # sale made today settles later (T+1), so a negative age means
        # "not settled yet", not "-1 days ago".
        _ago_s = (f"{_ago} days ago" if _ago >= 0 else
                  f"traded, settles in {-_ago} day"
                  f"{'s' if _ago < -1 else ''}")
        _inout = ("INSIDE the 30-day window" if _ago <= 30
                  else "outside the 30-day window")
    except ValueError:
        _ago_s, _inout = "?", "window position unknown"
    _kind = ll.get("date_kind")
    _head = ("last loss this tax year (covering a short)"
             if ll.get("direction") == "SHORT"
             else "last loss sale this tax year")
    return (f"{_head}: {ll['symbol']} "
            + (f"{_kind} " if _kind else "")
            + f"{ll['date']} ({_ago_s}, {M.shown(ll['gain'], 2):,.2f}"
            + (", unknown cost (no purchase in your files) — routed to "
               "manual reporting"
               if ll.get("unknown_cost") else "")
            + f") — {_inout}.")


# A buy-check line's trailing caveat on the window a buy starts: the
# --details view (docs/output-style.md, Essentials first).
_FUTURE_CAVEAT = " (Any buy starts a 30-day window: "


def _print_check_results(results: List[Dict[str, Any]],
                         country: str, details: bool = True,
                         cmd: str = "") -> None:
    """buy-check / sell-check text: per symbol a `SYMBOL: VERDICT` line
    and its detail lines as `- ` items (a radar note quoted in one as
    its own `Info:` item), a blank line between symbols, then the scope
    paragraph (lib/wash_scope). The --json document carries the same
    results."""
    from taxjson.lib import out
    from taxjson.lib.wash_scope import advisory_lines, scope_lines
    w = out.width()
    lines: List[str] = []
    for r in results:
        if lines:
            lines.append("")
        lines.append(f"{r['symbol']}: {r['verdict']}")
        for ln in r["detail"]:
            if not details:
                ln = ln.split(_FUTURE_CAVEAT, 1)[0]
            lines += advisory_lines(ln, w, "- ", "  ")
    if details:
        lines.append("")
        lines += scope_lines(country, w)
    else:
        from taxjson.lib.wash_scope import scope_more
        lines += out.wrap(scope_more(f"tjs {cmd} --details", country), w)
    # The text: a live exact Doc, written as the print() wrote it (the
    # lines are wrapped to `w` above; the Doc wraps nothing).
    d = out.Doc(exact=True, width_=0, live=True)
    d.line("\n".join(lines))


def _check_na_json(root: Path, args: argparse.Namespace, *, sell: bool = False):
    """buy-check (sell-check with `sell`) --json when the project has no
    wash-checkable account (_no_wash_checkable): the callable that
    prints their document with no result and `not_applicable` the why
    it is given (backlog TJ-0330)."""
    if not getattr(args, "json", False):
        return None

    def doc(why: str) -> None:
        from taxjson.lib.wash_scope import scope_note
        d = {"results": [], "scope_note": scope_note(
            "usa" if _radar_country_is_usa(root) else "canada"),
            "not_applicable": why}
        if sell:
            _json_out(d, schema="sell-check")
        else:
            _json_out(d, schema="buy-check")
    return doc


def cmd_buy_check(args: argparse.Namespace) -> None:
    """`tjs buy-check SYMBOL...`: is buying this ticker TODAY safe
    from the wash-sale / superficial-loss rules? Unsafe when a loss
    was sold within the past 30 days (the rebuy cancels it — deferred
    if bought taxable, PERMANENTLY denied if bought sheltered);
    safe-with-a-caveat when a wash window is merely open (buying
    extends it for future loss sales). Root-matched with ticker.map
    equivalences folded in. Exit 1 when any queried symbol is
    unsafe."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    radar, _canon, _last_loss = _wash_class_context(
        root, cache, "tjs buy-check",
        json_doc=_check_na_json(root, args))
    _usa = _radar_country_is_usa(root)

    def _money_cur(x: float) -> str:
        """Money as the radar's advisories print it: fmt_money and the
        books' base currency, the country's own (base_currency refuses
        any other; backlog TJ-0244)."""
        from taxjson.lib.report_fmt import fmt_money
        return f"{fmt_money(x)} {'USD' if _usa else 'CAD'}"
    # The rule's name in the project's law (never "superficial" in a
    # US project, never "wash sale" in a Canadian one).
    _sl_adj = "a wash sale" if _usa else "superficial"
    # s.54 'superficial loss' (b): the substituted property must still
    # be held at the END of the 30 days after the sale — a full exit,
    # or a rebuy sold again before day 30, leaves the loss standing.
    # §1091 has no such test. The texts below said every in-window
    # loss sale "would be superficial" and every rebuy "cancels" the
    # loss (S048-19, S049-15).
    _future_rule = (
        f"a PARTIAL loss sale of this name within 30 days of a buy "
        f"would be {_sl_adj}; selling the full position is not"
        if _usa else
        f"a loss sale of this name within 30 days of a buy would be "
        f"{_sl_adj} if you still hold the bought shares 30 days after "
        f"the sale — a full exit is not")
    unsafe = 0
    results = []
    from taxjson.lib.option_symbols import is_option_symbol as _is_opt_sym
    from taxjson.lib.option_symbols import parse_option_right as _opt_right
    for want in args.symbol:
        want = _fold_class_separator(want)
        _coin = _us_coin_answer(_canon, radar, want)
        if _coin is not None:
            results.append({"symbol": want.strip().upper(),
                            "verdict": "SAFE", "clears_at": None,
                            "detail": _coin, "last_loss": None})
            continue
        wroot, matches, _note = _class_matches(radar, _canon, want)
        _q = want.strip().upper()
        # Short this very contract: the buy closes it and acquires
        # nothing (CA-SL-05 / US-WASH-12; audit A2-0370).
        _own = radar.get(_q) or (next(
            (r for t, r in radar.items()
             if _option_id(_canon, t) == _option_id(_canon, _q)), None)
            if _is_opt_sym(_q) else None) or {}
        _short_n = -(float(_own.get("taxable_qty") or 0.0)
                     + float(_own.get("sheltered_qty") or 0.0))
        _closes = (_is_opt_sym(_q) and _opt_right(_q) == "C"
                   and _short_n > 1e-9)
        matches = _replacement_rows(want, matches, "buy", _closes,
                                    canon=_canon)
        verdict, lines, clears = _buy_check_class_verdict(
            radar, matches, _note, _q, _closes, _short_n, _usa, _money_cur, _future_rule)
        matches = {t: r for t, r in matches.items()
                   if (r.get("category") or "")}
        if len(lines) == bool(_note) and matches:
            cats = ", ".join(f"{t} ({r.get('category')})"
                             for t, r in sorted(matches.items()))
            lines.append(f"{cats}: no loss sale in the past 30 days — "
                         f"safe to buy. (Any buy starts a 30-day "
                         f"window: {_future_rule}.)")
        elif not lines:
            lines.append(f"{wroot}: no wash exposure on record — safe "
                         f"to buy. (Any buy starts a 30-day window: "
                         f"{_future_rule}.)")
        for _sl in _similar_lines(radar, _canon, wroot, want, "buy", _usa):
            if verdict == "SAFE":
                verdict = "SAFE*"
            lines.append(_sl)
        _ll = _last_loss_for(_last_loss, want, wroot, "buy", _closes,
                             canon=_canon)
        _lll = _last_loss_line(_ll)
        if _lll:
            lines.append(_lll)
        if verdict == "UNSAFE":
            unsafe += 1
        results.append({"symbol": want.strip().upper(),
                        "verdict": verdict,
                        "clears_at": clears, "detail": lines,
                        "last_loss": _ll})

    # SAFE is "as far as this project's accounts show" (CA-PLAN-04 /
    # US-PLAN-04, audit S054-22).
    from taxjson.lib.wash_scope import scope_note as _scope_note
    _scope = _scope_note("usa" if _usa else "canada")
    if getattr(args, "json", False):
        _json_out({"results": results, "scope_note": _scope}, schema="buy-check")
    else:
        _print_check_results(results, "usa" if _usa else "canada",
                             _details(args), "buy-check")
    if unsafe:
        raise SystemExit(1)


def _buy_check_class_verdict(radar: Dict[str, Dict[str, Any]],
                             matches: Dict[str, Dict[str, Any]], _note: Optional[str],
                             _q: str, _closes: bool, _short_n: float, _usa: bool,
                             _money_cur: Callable[[float], str], _future_rule: str
                             ) -> Tuple[str, List[str], Optional[str]]:
    """buy-check: one queried symbol's verdict across its class (the
    worst row's), the lines that say why, and the date a buy is safe
    from; the flagged rights and calls on the shares included."""
    from taxjson.lib.option_symbols import is_option_symbol as _is_opt_sym
    from taxjson.lib.option_symbols import parse_option_right as _opt_right
    # Worst verdict across the class (cross-listings included).
    verdict, lines = "SAFE", ([_note] if _note else [])
    if _closes:
        verdict = "SAFE*"
        lines.append(
            f"{_q}: you are short {_short_n:g} contract(s) — a buy "
            f"that closes them acquires nothing, so it never "
            f"replaces a loss on the shares; buying MORE than "
            f"{_short_n:g} opens a long call, a right to acquire "
            f"them.")
    # A US long call is a note only (US-WASH-12 / US-PLAN-02): the
    # engine never denies a share loss on it, so buying one is not
    # UNSAFE for the shares (it was, against the engine's verdict).
    _us_call_note = (_usa and _is_opt_sym(_q) and _opt_right(_q) == "C"
                     and not _closes)
    clears = None
    # A VIOLATION anywhere in the class poisons every leg's date:
    # cross-listings are identical property, so a BLOCKED leg's
    # own "safe from" date can be WEEKS before the violation
    # leg's loss becomes safely rebuyable — printing it (or
    # exporting it as the class clears_at) invited a rebuy that
    # cancels the newer loss (2026-09 audit).
    # A settled one whose window is over for a buyer (backlog
    # TJ-0775) poisons nothing.
    _class_has_violation = any(
        (r.get("category") or "") == "VIOLATION"
        and not _window_over(r)
        for r in matches.values())
    # Such a class's safe-from date is the radar's re-entry date
    # (rebuy_at: the first trading day a buy no longer replaces any
    # of its losses, the date `rebuy` prints), the latest across the
    # class — never a VIOLATION's sell-by CLEARS date, which the
    # radar's table shows and buy-check used to send the reader to
    # (backlog TJ-0137). None when a loss row lacks it.
    _loss_rows = [r for r in matches.values()
                  if (r.get("category") or "") in ("BLOCKED", "COOLING",
                                                   "VIOLATION")
                  and not _window_over(r)]
    _class_rebuy = (max(str(r["rebuy_at"]) for r in _loss_rows)
                    if _loss_rows and all(r.get("rebuy_at")
                                          for r in _loss_rows)
                    else None)
    for t, r in _with_forward_rows(matches):
        cat = r.get("category") or ""
        # A loss whose window is over (a settled VIOLATION) is no
        # reason to wait: "no recent loss sale" is a buy's view.
        _no_loss = ("no loss sale a buy can still replace"
                    if r.get("after_passed") else "no recent loss sale")
        if _usa and r.get("outside_wash_rule"):
            # A futures contract / futures option: outside §1091
            # (US-WASH-18) — a re-purchase never disallows its loss
            # (audit A2-0435: UNSAFE, exit 1).
            if verdict == "SAFE":
                verdict = "SAFE*"
            lines.append(f"{t}: {r.get('advisory')}")
            continue
        _sc = r.get("short_cover_loss") if _usa else None
        if _sc and not _is_opt_sym(_q):
            # §1091(e) / US-WASH-05: only a new SHORT sale replaces a
            # short-cover loss; a buy never does (audit A2-0436).
            # Beside a long loss a buy can replace (the row's
            # recent_loss), that one still decides the verdict
            # below (backlog TJ-0559).
            # A WASHED row's other losses are washed ones (its
            # advisory says which), never the short cover alone.
            _long_too = bool(r.get("recent_loss")) or cat == "WASHED"
            if verdict == "SAFE":
                verdict = "SAFE*"
            lines.append(
                f"{t}: "
                + ("a loss within the past 30 days was also on"
                   if _long_too else
                   "the loss sold within the past 30 days was on")
                + f" covering a short ({_sc.get('date')}) — a buy does "
                f"not replace it (§1091(e)); only a new short sale "
                f"before {_sc.get('reshort_ok_from')} would disallow "
                f"it.")
            if cat in ("BLOCKED", "COOLING") and not _long_too:
                continue
        if (_us_call_note and not _is_opt_sym(t.strip().upper())
                and cat in ("BLOCKED", "COOLING", "VIOLATION",
                            "WASHED")):
            if verdict == "SAFE":
                verdict = "SAFE*"
            lines.append(
                f"{t}: {cat} — {_loss_within_30(r)}; "
                f"{_q} is a long call on these shares: §1091 may "
                f"treat it as an option to acquire them, so buying it "
                f"now may make that loss a wash sale — the US engine "
                f"only flags it (a warning, call_vs_share_loss), so "
                f"check it by hand.")
            continue
        verdict, clears = _buy_check_row_verdict(
            t, r, cat, verdict, lines, clears, _q, _usa, _sc, _no_loss,
            _class_has_violation, _class_rebuy, _money_cur, _future_rule)
    # A warrant/right, a call on an adjusted series or a futures
    # option on the shares of a recent loss: the engines flag it for
    # a manual check (CA-SL-14/15, US-WASH-14/15); buy-check said
    # SAFE, 'no wash exposure on record' (audit A2-0129).
    for t, r, kind in _flagged_share_rows(radar, _q):
        cat = r.get("category") or ""
        if cat not in ("BLOCKED", "COOLING", "VIOLATION", "WASHED"):
            continue
        if verdict == "SAFE":
            verdict = "SAFE*"
        lines.append(
            f"{t}: {cat} — {_loss_within_30(r)}; {_q} "
            f"is {_FLAG_WORDS[kind]} these shares: a right to acquire "
            f"them bought now may make that loss "
            + ("a wash sale (§1091 \"contract or option to "
               "acquire\")" if _usa
               else "superficial (s.54 para (i))")
            + f" — the engine only flags it [{kind}], so check it by "
              f"hand.")
    return verdict, lines, clears


def _buy_check_row_verdict(t: str, r: Any, cat: str, verdict: str,
                           lines: List[str], clears: Optional[str], _q: str, _usa: bool,
                           _sc: Optional[Dict[str, Any]], _no_loss: str,
                           _class_has_violation: bool, _class_rebuy: Optional[str],
                           _money_cur: Callable[[float], str],
                           _future_rule: str) -> Tuple[str, Optional[str]]:
    """buy-check: one radar row's part of the verdict by its category (a
    window over, BLOCKED/COOLING/VIOLATION, WASHED, LOCKED,
    EXITABLE/CAUTION), its line added to `lines`; the verdict and the
    safe-from date so far."""
    from taxjson.lib.option_symbols import is_option_symbol as _is_opt_sym
    if _window_over(r):
        # Its rescue date has passed and every loss's re-entry
        # date has come: a buy traded today settles after the
        # window — it replaces nothing (`rebuy` lists the loss
        # as cleared; backlog TJ-0775).
        if verdict == "SAFE":
            verdict = "SAFE*"
        lines.append(f"{t}: {_passed_violation_text(r)} Its 30-day "
                     f"window has ended: a buy now does not "
                     f"replace that loss.")
    elif cat in ("BLOCKED", "COOLING", "VIOLATION"):
        verdict = "UNSAFE"
        # VIOLATION's clears_at is the radar's SELL-BY
        # deadline (loss + 30 — the last day to rescue), NOT
        # a re-entry date: printing it as "safe to buy from"
        # invited a rebuy up to four weeks early, killing the
        # loss permanently. Only the re-entry categories
        # carry a usable date (a class with a VIOLATION: the
        # rows' rebuy_at); take the LATEST across the
        # class, not the first alphabetically.
        _cd = (_class_rebuy if _class_has_violation
               else r.get("clears_at"))
        if _cd:
            clears = max(clears, _cd) if clears else _cd
        # Only the rebought units' share of the loss is denied
        # (audit S054-07): state it per unit when the radar
        # carries the loss and the units sold at it — money as
        # the advisories print it, fmt_money and the base
        # currency, never '$' (backlog TJ-0244, TJ-0121).
        _rl, _rq = r.get("recent_loss"), r.get("recent_loss_qty")
        # One unit's share (the radar's _per_unit_denial): two
        # Canadian sales in the window are each replaced by the
        # same unit, and a US short-cover loss is no buy's —
        # the total over the units sold halved or inflated it
        # (backlog TJ-0559, TJ-0560). An older radar: the ratio.
        _ps = r.get("recent_loss_per_unit")
        if _ps is None and _rl and _rq and float(_rq) > 1e-9:
            _ps = float(_rl) / float(_rq)
        _upto = r.get("recent_loss_per_unit_qty")
        _first = (f" for the first {float(_upto):g} units"
                  if _upto else "")
        _per = (f" on as many units as you buy (about "
                f"{_money_cur(float(_ps))} of it per unit"
                f"{_first})" if _ps else "")
        if (_per and _is_opt_sym(_q)
                and not _is_opt_sym(t.strip().upper())):
            # A call replaces the shares it is a right to: one
            # standard contract is 100 of them (CA-SL-05; the
            # per-unit figure read as per contract, audit
            # A2-0752).
            from taxjson.lib.contract_size import OPTION_CONTRACT_SHARES
            _ps = float(_ps)
            _per = (f" on as many shares as the calls cover "
                    f"(about {_money_cur(_ps)} of it per share, "
                    f"{_money_cur(_ps * OPTION_CONTRACT_SHARES)} "
                    f"per standard {OPTION_CONTRACT_SHARES:g}-share "
                    f"contract{_first.replace('units', 'shares')})")
        lines.append(
            f"{t}: {cat} — {_loss_within_30(r)}; buying now "
            f"cancels it{_per}"
            + ("" if _usa else
               " if you still hold the shares 30 days after "
               + ("that cover" if _short_cover_loss_date(r)
                  else "that sale"))
            + f" (DEFERRED if bought taxable, PERMANENT if bought "
            f"{'in an IRA' if _usa else 'sheltered'})."
            + (f" Safe to buy from {_cd}." if _cd else
               " Wait until 31 days after the LATEST in-window "
               "loss sale (its REBUY ON date: `tjs rebuy`)."))
    elif cat == "WASHED":
        # US §1091: the loss is already disallowed (nothing a
        # buy can make worse) unless part of an in-window loss
        # is still allowed — then a buy before clears_at
        # disallows that part too.
        _cd = r.get("clears_at")
        if _cd:
            verdict = "UNSAFE"
            clears = max(clears, _cd) if clears else _cd
            lines.append(f"{t}: {r.get('advisory')} Safe to buy "
                         f"from {_cd}.")
        else:
            if verdict == "SAFE":
                verdict = "SAFE*"
            _wadv = str(r.get("advisory") or "")
            if _sc and not _is_opt_sym(_q):
                # The short cover has its line above: "that
                # loss" is the washed one (backlog TJ-0681).
                for _n in r.get("notes") or []:
                    if "covering a short" in str(_n):
                        _wadv = _wadv.replace(" " + str(_n), "")
            lines.append(
                f"{t}: {_wadv} Buying now changes "
                f"nothing for that loss, but it starts a new "
                f"30-day window for a later loss sale.")
    elif cat == "LOCKED":
        # A registered account's / IRA's in-window buy denies a
        # taxable loss sale even as a full exit: the generic
        # "a full exit is not" contradicted sell-check's LOCKED
        # verdict (audit A2-0408). The radar's advisory states
        # the case in the project's law.
        if verdict == "SAFE":
            verdict = "SAFE*"
        _adv = str(r.get("advisory") or "")
        _adv = _adv.split(":", 1)[1].strip() if \
            _adv.startswith("LOCKED:") else _adv
        lines.append(
            f"{t}: LOCKED — {_no_loss}, buying is safe "
            f"TODAY, but selling even the full taxable position "
            f"at a loss does not escape the rule: {_adv} Buying "
            f"more extends the wash window.")
    elif cat in ("EXITABLE", "CAUTION"):
        if verdict == "SAFE":
            verdict = "SAFE*"
        lines.append(
            f"{t}: {cat} — {_no_loss}"
            + (" a buy can disallow" if _sc and not
               r.get("after_passed") else "")
            + f", buying is safe TODAY, but it extends the wash "
            f"window: {_future_rule}.")
    return verdict, clears


def cmd_sell_check(args: argparse.Namespace) -> None:
    """`tjs sell-check SYMBOL...`: is selling this ticker AT A
    LOSS today safe from the superficial-loss / wash-sale rules?
    UNSAFE when a registered account's recent buy it still holds
    would deny the loss on the WHOLE taxable position (LOCKED), or when
    an open violation is backed by a registered account's in-window
    buy; ACTION when a violation can be rescued by selling the taxable
    replacement; SAFE* for conditional cases (full-exit-only,
    sheltered-holds forward caveat); PARTIAL when only some units of a
    LOCKED position are at risk; SAFE otherwise
    — with the standard rule: no rebuy on EITHER side for 30 days
    after. Root-matched with ticker.map equivalences. Exit 1 when any
    queried symbol is UNSAFE or PARTIAL. Whether the sale would BE a
    loss at today's price is `tjs harvest`'s job."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    radar, _canon, _last_loss = _wash_class_context(
        root, cache, "tjs sell-check",
        json_doc=_check_na_json(root, args, sell=True))
    _usa = _radar_country_is_usa(root)
    unsafe = 0
    results = []
    for want in args.symbol:
        want = _fold_class_separator(want)
        _coin = _us_coin_answer(_canon, radar, want)
        if _coin is not None:
            results.append({"symbol": want.strip().upper(),
                            "verdict": "SAFE", "clears_at": None,
                            "act_by": None, "detail": _coin,
                            "last_loss": None})
            continue
        wroot, matches, _note = _class_matches(radar, _canon, want)
        matches = _replacement_rows(want, matches, "sell", canon=_canon)
        verdict, lines = "SAFE", ([_note] if _note else [])
        clears = None        # safe-to-sell-from date (worst = LATEST)
        act_by = None        # rescue deadline: last TRADE date (EARLIEST)
        for t, r in _with_forward_rows(matches):
            cat = r.get("category") or ""
            adv = r.get("advisory") or ""
            verdict, clears, act_by = _sell_check_row_verdict(
                t, r, cat, adv, verdict, lines, clears, act_by, _usa)
            # Warn-only flags (a US long call, a warrant, an adjusted
            # series, a futures option bought in the window; a US
            # short-cover loss's re-short rule), whatever the row's
            # category — a BLOCKED row dropped them (audit A2-0687 /
            # A2-0434 / A2-0445); skipped when the line already quotes
            # the advisory that carries them.
            for _n in r.get("notes") or []:
                if any(_n in ln for ln in lines):
                    continue
                if verdict == "SAFE":
                    verdict = "SAFE*"
                lines.append(f"{t}: {_n}")
        if len(lines) == bool(_note):
            lines.append(f"{wroot}: no tracked taxable position — "
                         f"nothing to sell (or run `tjs run` to "
                         f"refresh the books).")
        for _sl in _similar_lines(radar, _canon, wroot, want, "sell", _usa):
            if verdict == "SAFE":
                verdict = "SAFE*"
            lines.append(_sl)
        _ll = _last_loss_for(_last_loss, want, wroot, "sell",
                             canon=_canon)
        _lll = _last_loss_line(_ll)
        if _lll:
            lines.append(_lll)
        if verdict in ("UNSAFE", "PARTIAL"):
            unsafe += 1
        results.append({"symbol": want.strip().upper(),
                        "verdict": verdict,
                        "clears_at": clears,
                        "act_by": act_by, "detail": lines,
                        "last_loss": _ll})
    # SAFE is "as far as this project's accounts show" (CA-PLAN-04 /
    # US-PLAN-04, audit S054-22).
    from taxjson.lib.wash_scope import scope_note as _scope_note
    _scope = _scope_note("usa" if _usa else "canada")
    if getattr(args, "json", False):
        _json_out({"results": results, "scope_note": _scope}, schema="sell-check")
    else:
        _print_check_results(results, "usa" if _usa else "canada",
                             _details(args), "sell-check")
    if unsafe:
        raise SystemExit(1)


def _sell_check_row_verdict(t: str, r: Any, cat: str, adv: str, verdict: str,
                            lines: List[str], clears: Optional[str],
                            act_by: Optional[str], _usa: bool
                            ) -> Tuple[str, Optional[str], Optional[str]]:
    """sell-check: one radar row's part of the verdict by its category
    (outside the wash rule, LOCKED, VIOLATION, WASHED, EXITABLE/CAUTION/
    RISK, BLOCKED/COOLING, CLEAR), its line added to `lines`; the
    verdict, the safe-from date and the rescue deadline so far."""
    if _usa and r.get("outside_wash_rule"):
        # A futures contract / futures option: outside §1091
        # (US-WASH-18) — the radar's own line, not 'do not rebuy'.
        lines.append(f"{t}: {adv}")
    elif cat == "LOCKED":
        # A registered account's in-window buy it still holds
        # denies the loss on up to that many units — the rest of
        # a sale stands (s.54, per holder). UNSAFE only when the
        # whole taxable position is at risk; a fraction is
        # PARTIAL, its line stating the units (still exit 1: a
        # part of the loss would be lost for good). An older
        # radar without at_risk_qty stays UNSAFE.
        _risk = r.get("at_risk_qty")
        _tq = abs(float(r.get("taxable_qty") or 0.0))
        if (_risk is not None and _tq > 1e-9
                and float(_risk) < _tq - 1e-6):
            if verdict != "UNSAFE":
                verdict = "PARTIAL"
        else:
            verdict = "UNSAFE"
        _cd = r.get("clears_at")
        if _cd:                     # worst case across the class
            clears = max(clears, _cd) if clears else _cd
        lines.append(f"{t}: {adv}")
    elif cat == "VIOLATION" and r.get("deadline_passed"):
        # The last rescue trade date has passed: the loss is
        # denied and no sale undoes it — never an ACTION with a
        # sell-by date in the past (audit A2-0368). A loss sale
        # today of the shares still held is judged on its own:
        # the row's `forward` view follows it (backlog TJ-0775).
        if verdict == "SAFE":
            verdict = "SAFE*"
        lines.append(f"{t}: {_passed_violation_text(r)}")
    elif cat == "VIOLATION":
        _cd = r.get("clears_at")
        if _cd:
            # A VIOLATION's date is a SELL-BY deadline — the
            # radar's last TRADE date that settles inside the
            # engine's loss_settle+30 bound (never the settle
            # date itself: trading ON that settles a day late
            # and the loss is denied). The binding one across
            # a class of identical property is the EARLIEST,
            # not the latest — max() exported a date that
            # missed the tighter leg's rescue by days
            # (2026-09 audit).
            act_by = min(act_by, _cd) if act_by else _cd
        _rescue = r.get("rescue")
        if _rescue is None:
            # A radar from before the per-holder rule: any
            # sheltered holding was treated as backing.
            _shl_accts = (["a sheltered account"]
                          if abs(float(r.get("sheltered_qty")
                                       or 0.0)) > 0.01 else [])
        else:
            # Only a registered account that bought inside the
            # window and still holds backs the denial (s.54,
            # per holder); shares it held before the window
            # never do.
            _shl_accts = sorted({
                f"'{x.get('account') or 'unknown'}'"
                for x in _rescue
                if x.get("holder") == "sheltered"})
        if _shl_accts:
            # The registered-matched portion is denied for good
            # ONLY if that account still holds at the window's
            # end — selling there too before the deadline
            # defeats it.
            verdict = "UNSAFE"
            lines.append(
                f"{t}: {adv} Registered holder(s) "
                f"{', '.join(_shl_accts)} bought inside the "
                f"window: unless they also sell by that trade "
                f"date" + (f" ({_cd})" if _cd else "")
                + f", the registered-matched portion is "
                f"permanently denied.")
        else:
            if verdict not in ("UNSAFE", "PARTIAL"):
                verdict = "ACTION"
            lines.append(f"{t}: {adv}")
    elif cat == "WASHED":
        # US §1091: an earlier loss is already disallowed and no
        # sale undoes it — there is nothing to rescue (never
        # ACTION). Selling the replacement realizes the deferred
        # loss that sits in its basis.
        if verdict == "SAFE":
            verdict = "SAFE*"
        lines.append(
            f"{t}: {adv} A sale now cannot restore that loss; "
            f"selling the replacement lot realizes the loss added "
            f"to its basis, but a PARTIAL loss sale with other "
            f"buys in the last 30 days is a wash sale again.")
    elif cat in ("EXITABLE", "CAUTION", "RISK"):
        if verdict == "SAFE":
            verdict = "SAFE*"
        lines.append(f"{t}: {adv}")
    elif cat in ("BLOCKED", "COOLING"):
        _scd = _short_cover_loss_date(r)
        lines.append(f"{t}: {cat} — "
                     + (f"a recent loss on covering a short "
                        f"({_scd}) is" if _scd else
                        "a recent loss sale is")
                     + f" already in its cooling window; if any "
                     f"position remains, a further loss sale "
                     f"is fine only with no buys within ±30 "
                     f"days.")
    elif cat == "CLEAR":
        if abs(float(r.get("taxable_qty") or 0.0)) <= 0.01:
            # Sheltered-only holding: nothing to sell at a
            # loss, and a registered disposition has no tax
            # effect at all.
            lines.append(
                f"{t}: held only in "
                f"{'IRA(s)' if _usa else 'sheltered account(s)'}"
                f" — nothing to sell at a loss (a "
                f"{'tax-deferred' if _usa else 'registered'} "
                f"disposition has no tax effect).")
        elif float(r.get("taxable_qty") or 0.0) < 0:
            lines.append(f"{t}: {adv}")
        else:
            lines.append(f"{t}: CLEAR — safe to sell at a loss "
                         f"now; do not rebuy on EITHER side "
                         f"(taxable or "
                         f"{'IRA' if _usa else 'sheltered'}) for "
                         f"30 days.")
    return verdict, clears, act_by


def cmd_rebuy(args: argparse.Namespace) -> None:
    """`tjs rebuy [SYMBOL ...] [--date D]`: every ticker sold at a
    loss whose repurchase window is still open, with the first day it
    can be bought back without the loss being denied (CA-PLAN-05 /
    US-PLAN-06). The rows are the wash radar's own (its `losses` and
    `rebuy_at`, run as of the date with --all), matched to the query
    the way buy-check matches (ticker.map / tobase.map, renames, an
    option's underlying); lib/rebuy groups and renders them."""
    from taxjson.lib import rebuy as RB
    from taxjson.lib.ticker_map import class_share_aliases, underlying_of
    root = Path(args.dir).resolve()
    cache = root / "work"
    if args.date:
        from taxjson.lib.dates import as_of_date_problem
        _bad = as_of_date_problem(args.date)
        if _bad:
            _die_input(_bad)
    as_of = args.date or RB.as_of_default()

    def _na_doc(why: str) -> None:
        # No wash-checkable account: rebuy's document with no row
        # (backlog TJ-0330).
        from taxjson.lib.wash_scope import scope_note as _sn
        c = "usa" if _radar_country_is_usa(root) else "canada"
        d = RB.build({}, as_of, c)
        d.update(currency=_base_currency(root), scope_note=_sn(c),
                 any_account=RB.any_account_line(c), not_applicable=why)
        _json_out(d, schema="rebuy")
    radar, canon, _ll = _wash_class_context(
        root, cache, "tjs rebuy",
        ["--date", as_of, "--cleared-days", str(RB.CLEARED_DAYS)],
        json_doc=_na_doc if getattr(args, "json", False) else None)
    country = "usa" if _radar_country_is_usa(root) else "canada"
    want = None
    asked = []
    if args.symbol:
        want = set()
        for s in args.symbol:
            _w, _m, _n = _class_matches(radar, canon,
                                        _fold_class_separator(s))
            _ws = {_w} | {canon(t) for t in _m}
            want |= _ws
            asked.append((s.strip().upper(), _ws, bool(_m)))
    _aliases = class_share_aliases(radar)
    doc = RB.build(radar, as_of, country, canon=canon,
                   underlying=lambda s: underlying_of(s, _aliases),
                   want=want)
    if asked:
        # A SYMBOL with no row is said so, with the count of the other
        # tickers that have an open window: "No ticker sold at a loss
        # has an open repurchase window" was false then, and a mistyped
        # symbol read as nothing to wait for (backlog TJ-0104).
        full = RB.build(radar, as_of, country, canon=canon,
                        underlying=lambda s: underlying_of(s, _aliases))
        doc["asked"] = [a for a, _ws, _b in asked]
        doc["unmatched"] = [
            {"symbol": a, "in_books": in_books}
            for a, _ws, in_books in asked
            if not any(canon(x["ticker"]) in _ws
                       for x in doc["rows"] + doc["cleared"])]
        doc["others_open"] = len({r["ticker"] for r in full["rows"]}
                                 - {r["ticker"] for r in doc["rows"]})
    from taxjson.lib.wash_scope import scope_note as _scope_note
    doc["currency"] = _base_currency(root)
    doc["scope_note"] = _scope_note(country)
    doc["any_account"] = RB.any_account_line(country)
    if getattr(args, "json", False):
        _json_out(doc, schema="rebuy")
        return
    RB.render(doc, doc["currency"], _details(args)).print()


def _register_wash_radar(sub: argparse._SubParsersAction) -> None:
    p_wash = sub.add_parser(
        "wash-radar",
        help="Open superficial-loss / wash-sale windows today",
        description="Superficial-loss / wash-sale radar per taxable "
             "account: the open windows and their clear dates, "
             "recomputed live as of today (also written to "
             "reports/wash_radar_<account>.rpt by `tjs run`).")
    p_wash.add_argument("account", nargs="?",
                        metavar="ACCOUNT[,ACCOUNT...]",
                        help="Account, or several as one comma list "
                             "(default: all taxable): the rows shown; "
                             "every taxable book decides each status")
    p_wash.add_argument("--date", help="As-of date YYYY-MM-DD (default: today)")
    p_wash.add_argument("--verbose", "-v", action="store_true",
                        help="The full report, as --details: every "
                             "advisory, the definitions and the scope")
    p_wash.add_argument("--all", action="store_true",
                        help="Also the OTHER rows: listings with no open "
                             "position and no loss in the window "
                             "(nothing to watch); the default view "
                             "already has the CLEAR rows")
    p_wash.add_argument("--json", action="store_true",
                        help="Emit the structured radar document (absolute "
                             "clears_at dates) instead of the text report")
    p_wash.set_defaults(func=cmd_wash_radar, account_list=True)


def _register_watch(sub: argparse._SubParsersAction) -> None:
    p_watch = sub.add_parser(
        "watch",
        help="Report what changed since the last watch (cron)",
        description="Report only what CHANGED since the last watch run — "
             "new/changed/cleared radar advisories, moved clear dates "
             "(--harvest adds the harvestable-now total). Quiet when "
             "nothing changed (cron mails only on news); state in "
             "work/.watch_state.json.")
    p_watch.add_argument("--harvest", action="store_true",
                         help="Also watch the harvestable-now loss "
                              "total (runs `tjs harvest --json`; "
                              "needs a price source)")
    p_watch.add_argument("--threshold", type=_nonneg_float_arg, default=100.0,
                         metavar="AMT",
                         help="Report the harvest total only when it "
                              "moves by more than AMT in base currency "
                              "(default: 100)")
    p_watch.add_argument("--no-ibkr", action="store_true",
                         help="Forwarded to harvest: skip the IBKR "
                              "price tier")
    p_watch.add_argument("--state", metavar="PATH", default=None,
                         help="State file for THIS watch cadence "
                              "(default work/.watch_state.json; "
                              "relative paths resolve against the "
                              "project root). Separate files let a "
                              "daily and a weekly cron line each keep "
                              "their own baseline instead of fighting "
                              "over one")
    p_watch.add_argument("--exit-code", action="store_true",
                         help="Exit 1 when changes were reported "
                              "(default: always 0, so chains like "
                              "`tjs run watch` don't fail on news)")
    p_watch.add_argument("--json", action="store_true",
                         help="Emit the change list as JSON")
    p_watch.set_defaults(func=cmd_watch)


def _register_buy_check(sub: argparse._SubParsersAction) -> None:
    p_canbuy = sub.add_parser(
        "buy-check",
        help="Check if buying a ticker today cancels a loss",
        description="Is buying a ticker TODAY safe from the wash-sale / "
             "superficial-loss rules? UNSAFE when a loss was sold "
             "within the past 30 days (the rebuy cancels it — "
             "permanently if bought in a sheltered account); SAFE* "
             "when buying merely extends an open wash window. "
             "Root-matched (`buy-check SAMPLU` covers SAMPLU.US and "
             "cross-listings). Exit 1 when any symbol is unsafe.")
    p_canbuy.add_argument("symbol", nargs="+",
                          help="Ticker(s) to check (root or full, "
                               "e.g. SAMPLU or SAMPLU.US)")
    p_canbuy.add_argument("--json", action="store_true",
                          help="Emit verdicts as JSON")
    p_canbuy.set_defaults(func=cmd_buy_check)


def _register_sell_check(sub: argparse._SubParsersAction) -> None:
    p_sellchk = sub.add_parser(
        "sell-check",
        help="Check if selling at a loss today keeps the loss",
        description="Is selling a ticker AT A LOSS today safe from the "
             "superficial-loss / wash-sale rules? UNSAFE when a "
             "registered account's recent buy it still holds would "
             "deny the loss on the whole position (LOCKED); PARTIAL "
             "when only some of the units are at risk (the line says "
             "how many); ACTION when a rescueable violation is open "
             "(sell the replacement); SAFE*/SAFE otherwise with the "
             "applicable caveats. buy-check's sell-side twin; whether "
             "it IS a loss at today's price is `tjs harvest`'s "
             "job. Exit 1 on UNSAFE or PARTIAL.")
    p_sellchk.add_argument("symbol", nargs="+",
                           help="Ticker(s) to check (root or full, "
                                "e.g. SAMPLU or SAMPLU.US)")
    p_sellchk.add_argument("--json", action="store_true",
                           help="Emit verdicts as JSON")
    p_sellchk.set_defaults(func=cmd_sell_check)


def _register_rebuy(sub: argparse._SubParsersAction) -> None:
    p_rebuy = sub.add_parser(
        "rebuy",
        help="When each ticker sold at a loss can be bought back",
        description="Every ticker sold at a loss whose repurchase window "
             "is still open, with the first day it can be bought back "
             "without the loss being denied (superficial loss, s.54, on "
             "settlement dates; wash sale, §1091, on trade dates). A buy "
             "in ANY of your accounts counts, registered ones included, "
             "where the loss is denied for good. Option series are "
             "listed under their underlying, each with its own date; "
             "ticker.map / tobase.map listings of the same security "
             "count (XYZ.US for XYZ.TO). wash-radar is the status of "
             "every holding; buy-check answers one ticker today.")
    p_rebuy.add_argument("symbol", nargs="*",
                         help="Only these tickers (root or full, e.g. "
                              "SAMPLU or SAMPLU.US; default: all)")
    p_rebuy.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                         help="As of this date (default: today)")
    p_rebuy.add_argument("--json", action="store_true",
                         help="Emit the rows as JSON")
    p_rebuy.set_defaults(func=cmd_rebuy)


def _register_harvest(sub: argparse._SubParsersAction) -> None:
    p_harv = sub.add_parser(
        "harvest",
        help="Unrealized gains and losses at current prices",
        description="Unrealized gain/(loss) per open position at current "
             "prices — the tax-loss-harvest view, losses first, with "
             "the wash radar's advisory on each loss (prices: IBKR -> "
             "yfinance -> cache).")
    p_harv.add_argument("symbol", nargs="*", default=[],
                        help="Only these symbols (e.g. AAA.TO BBB.US)")
    p_harv.add_argument("--crypto", action="store_true",
                        help="Include crypto = true accounts (excluded "
                             "by default — the price chain serves stock "
                             "snapshots, so crypto symbols mostly fail "
                             "to price)")
    p_harv.add_argument("--options", action="store_true",
                        help="Include OCC option positions (e.g. LEAPS) "
                             "— priced only through IBKR (+ cache); "
                             "adds a DTE column")
    p_harv.add_argument("--no-ibkr", action="store_true",
                        help="Skip the IBKR tier (no TWS/Gateway running)")
    p_harv.add_argument("--ibkr-port", type=int, default=None,
                        help="4001 Gateway live, 7496 TWS live "
                             "(default: 4001)")
    p_harv.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    p_harv.add_argument("--verbose", "-v", action="store_true",
                        help="Per-tier price-chain diagnostics")
    p_harv.set_defaults(func=cmd_harvest)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_wash_radar(sub)
    _register_watch(sub)
    _register_buy_check(sub)
    _register_sell_check(sub)
    _register_rebuy(sub)
    _register_harvest(sub)
