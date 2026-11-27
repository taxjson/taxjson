"""taxjson.toml: read, validated and normalized; the project's refusals at load."""
import collections.abc as _cabc
import re
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.project.accounts import accounts_of, typed_account
from taxjson.project.toml_io import decode_toml_bytes, parse_toml, read_toml
from taxjson.lib import project_layout as _PL
from taxjson.lib.config_check import (
    ACCOUNT_KEYS as _ACCOUNT_KEYS, ESTIMATE_KEYS as _ESTIMATE_KEYS,
    INSTALMENTS_KEYS as _INSTALMENTS_KEYS, TOP_LEVEL_TABLES as
    _TOP_LEVEL_TABLES, account_name_problem,
)
from taxjson.project.settings import Settings, as_settings, settings_of
from taxjson.lib.country import (
    PLAN_COUNTRY as _PLAN_COUNTRY, plan_kinds as _plan_kinds,
    SETTING_COUNTRY as _SETTING_COUNTRY,
)

from taxjson.pipeline import run_state
from taxjson.pipeline.messages import _current_cmd, _die, _die_input, _PROG
from taxjson.parsers.registry import corp_action_brokers, rate_reading_brokers


# ---------------------------------------------------------------- config

def _project_above(root: Path, levels: int = 4) -> Optional[Path]:
    """The project a folder inside one belongs to: the nearest folder
    above `root` (up to `levels`) holding a taxjson.toml, or the latest
    year folder of the nearest one holding year folders (in the shared
    inputs/ of a multi-year project). None when there is none."""
    try:
        here = Path(root).resolve()
    except (OSError, RuntimeError):
        return None
    for up in list(here.parents)[:levels]:
        if _PL.has_config(up):
            return up
        years = _PL.year_dirs(up)
        if years:
            return years[-1][1]
    return None


def _no_project_message(root: Path, cmd: str = "") -> Tuple[str, str]:
    """(headline, fix) for a command run where there is no project:
    the folder has no taxjson.toml. The fix names the project above it
    when there is one (`cd` there or -C), else how to start one — never
    "run `tjs run` first", which fails the same way (workflow audit
    2026-10-22, item 15)."""
    import os as _os
    head = f"no taxjson.toml in {root} — not a taxjson project"
    above = _project_above(root)
    if above is not None:
        try:
            where = _os.path.relpath(str(above))
        except ValueError:
            where = str(above)
        if len(where) > len(str(above)):
            where = str(above)
        # Commands to paste: a path with a space is one argument
        # (backlog TJ-0571).
        import shlex as _shlex
        arg = _shlex.quote(where)
        run = f"`{_PROG} -C {arg} {cmd}`" if cmd else \
            f"`{_PROG} -C {arg} ...`"
        return head, f"The project is in {where}: `cd {arg}`, or {run}."
    return head, (f"`{_PROG} init --country canada|usa` creates one "
                  f"(docs/getting-started.md); to use one you have, "
                  f"`cd` into its folder or name it with -C DIR.")


def _use_project_ticker_map(root: Path) -> None:
    """Point lib/markets at the project's ticker.map. It extends the
    shipped market lists (STABLE, SPLITSHARE, INDEXOPT, SECFUT, EVENING,
    MULT, VENUE): every command and stage of this project reads the same one;
    an empty value means the project has none (a stray outer setting
    never applies). A view that soft-reads the config calls it too: its
    MULT lines size an assignment (backlog TJ-0789)."""
    from taxjson.lib.markets import ENV_TICKER_MAP
    _tm = _PL.ticker_map_path(root)
    import os as _os
    _os.environ[ENV_TICKER_MAP] = (str(_tm.resolve()) if _tm.is_file()
                                   else "")


def load_config(root: Path, *, missing_ok: bool = False,
                reopening: bool = False) -> Dict[str, Any]:
    """The project's taxjson.toml, parsed and checked: every command's one
    reader. It refuses what no command may run on: a taxjson.toml the
    installed version cannot read (lib/requires), a bad account name or type,
    a setting of the other country, a folder setting or an account folder
    leaving the folder that holds the project (lib/project_layout), and a file
    that is there but cannot be read (a dangling symlink, no TOML parser).

    `missing_ok`: the query wrappers' read, which must work from work/ files
    without a hard config dependency: {} when there is no taxjson.toml, and
    the same parse and checks on a file that is there (until 2026-11-05 the
    soft read read nothing without a parser; backlog TJ-1640, TJ-1641). Two
    refusals are the command dispatcher's, applied once per command with its
    exemptions (`cli/main._refuse_newer_projects`, `_LINK_CHECK_FREE_CMDS`:
    `tjs tax-logic` renders the rules of a project it cannot run), so only
    the full read repeats them here; and only the full read points the
    stages at the project's ticker.map and names the project in the
    environment.

    `reopening`: `tjs unclose-year`'s read only. One refusal is left to
    that command, which takes the state back or refuses it itself: a
    `tobase_map` named again beside the year's own tobase.map
    (lib/project_layout.tobase_both_problem). Every other check is made."""
    path = root / "taxjson.toml"
    if path.is_symlink() and not path.exists():
        # Unreadable is not absent: a dangling taxjson.toml switched fees /
        # dil-sum / roc-sum from the tax year to all history at exit 0
        # (re-audit A2-0144).
        _die(f"cannot read {path}: it is a symlink whose target "
             f"does not exist (or loops) — fix or remove it.")
    if not path.exists():
        if missing_ok:
            return {}
        _die(*_no_project_message(root, _current_cmd()))
    if not missing_ok:
        # First: a project made for a newer taxjson is refused before any
        # other check reads it with this version's rules (lib/requires).
        _refuse_newer_config(path)
    _refuse_unreadable_project_inputs(root)
    text = _read_config_text(path)
    try:
        cfg = parse_toml(text) or {}
    except Exception as e:
        # A file that does not parse is a user error to fix, not a reason
        # to guess (audit S049-00, S048-13).
        _die(_toml_problem(path, e), _TOML_FIX)
    # Account names build filesystem paths (inputs/<name>, work/<name>_*)
    # and sub-tool argv (--account <name>): a "../x" name wrote artifacts
    # OUTSIDE the project and a "-x" name was parsed as a flag (2026-09
    # security audit). The first bad name is the headline, before anything
    # builds a path from it; then the types, for every command: the filing
    # commands partition on the type (R1-268).
    # (`accounts = 5` is no table: `_refuse_bad_account_types` names it.)
    if isinstance(cfg.get("accounts"), dict):
        for _n in cfg["accounts"]:
            _problem = account_name_problem(_n)
            if _problem:
                _die(_problem)
    _refuse_bad_account_types(cfg)
    if not missing_ok:
        _refuse_outside_dir_links(root, cfg.get("accounts") or {})
    _normalize_settings(cfg)
    _refuse_folder_settings(root, cfg, reopening=reopening)
    _refuse_old_elections(root, cfg)
    if not missing_ok:
        _use_project_ticker_map(root)
        import os as _os
        # The project itself, for the stages whose input files may sit in
        # a folder shared by every year (`inputs_dir`): the IB parse reads
        # the project's .tt RENAME lines and ticker.map from it, never from
        # the folder above the inputs (lib/project_layout.project_of_input).
        _os.environ[_PL.ENV_PROJECT_ROOT] = str(Path(root).resolve())
    cfg["settings"] = settings_of(cfg)
    return cfg


def load_settings(root: Path, *, missing_ok: bool = False) -> Settings:
    """The project's [settings], typed: `load_config`'s (`missing_ok`: an
    empty one, `.empty`, when there is no taxjson.toml — one type for every
    reader, backlog TJ-1594)."""
    return settings_of(load_config(root, missing_ok=missing_ok))


def _refuse_newer_config(path: Path, where: str = "this project"
                         ) -> Optional[Dict[str, Any]]:
    """Exit 2 when taxjson.toml at `path` records a requires_taxjson
    newer than this taxjson (or one that is not ">=X.Y.Z"):
    lib/requires. The parsed file; None when it cannot be read or
    parsed (left to the reader, which says why)."""
    from taxjson.lib import requires as _REQ
    try:
        cfg = read_toml(path)
    except Exception:                                   # noqa: BLE001
        return None
    why = _REQ.problem(cfg.get("settings"), where)
    if why:
        _die_input(why)
    return cfg


def _refuse_folder_settings(root: Path, cfg: Dict[str, Any], *,
                            reopening: bool = False) -> None:
    """[settings] inputs_dir / holdings_dir / exports_dir that leave the
    folder holding the project, and a year folder holding another
    year's project (lib/project_layout.setting_problems). `reopening`
    (load_config): the two tobase.map files of a year are left to `tjs
    unclose-year`."""
    probs = _PL.setting_problems(root, cfg.get("settings") or {})
    if reopening:
        both = _PL.tobase_both_problem(root, cfg.get("settings") or {})
        probs = [p for p in probs if p != both]
    if probs:
        _die(probs[0] if len(probs) == 1 else
             "taxjson.toml:\n  " + "\n  ".join(probs))


def _refuse_old_elections(root: Path, cfg: Dict[str, Any]) -> None:
    """Exit 2 while the project holds an account's elections under an
    old name — an open year's inputs/<account>/manifest.json (the name
    before v3.0.0), or work/<account>_manifest.json (a layout older
    still) — naming the file and `tjs migrate`; or an account folder
    holding both names, a filed year's too, naming each
    (lib/project_layout.old_elections_problem), as an old per-purpose
    file stops every command. `tjs migrate`, which renames them, runs
    past it; a filed year's frozen copy is read under its own name."""
    accounts = cfg.get("accounts")
    if not isinstance(accounts, dict) or _current_cmd() == "migrate":
        return
    why = _PL.old_elections_problem(root, list(accounts))
    if why is not None:
        _die_input(*why)


def _read_config_text(path: Path) -> str:
    """taxjson.toml's text for tomllib. A directory or unreadable file
    tracebacked from every command (S040-03); a leading UTF-8 BOM (what
    Notepad saves) failed as "Invalid statement (at line 1, column 1)"
    with no hint (S038-04) — toml_io drops it, as TOML allows no BOM."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        _die(f"cannot read {path}: {e.strerror or e}")
    try:
        return decode_toml_bytes(raw)      # the one BOM and newline policy
    except UnicodeDecodeError as e:
        from taxjson.lib.cli_diag import not_utf8_headline
        _die(not_utf8_headline(_config_shown(path), e)
             + " — it is not valid TOML",
             "Save it as UTF-8 in your editor.")


# The fix under "taxjson.toml is not valid TOML: ..." (the parser names
# the line and column).
_TOML_FIX = ("Correct that line in taxjson.toml (docs/settings.md lists "
             "every key), then run the command again.")


def _toml_problem(path: Path, exc: BaseException) -> str:
    """`taxjson.toml:28: not valid TOML: <the parser's reason> (column
    42)` — the line first, so the headline stays one line."""
    text = str(exc)
    m = re.search(r"\s*\(at line (\d+), column (\d+)\)\s*$", text)
    if m:
        return (f"{_config_shown(path)}:{m[1]}: not valid TOML: "
                f"{text[:m.start()]} (column {m[2]})")
    return f"{_config_shown(path)} is not valid TOML: {text}"


def _config_shown(path: Path) -> str:
    """A taxjson.toml as a message names it: relative to the folder the
    command was run in when it is inside it (`taxjson.toml`,
    `2025/taxjson.toml`), else as given. The absolute path pushed the
    headline under a bare "Error:" (backlog TJ-0123)."""
    from taxjson.lib.out import relpath
    return relpath(path, Path.cwd())


# Commands that never date a crypto row: a crypto project without
# [settings] local_timezone still runs them (`tjs format` shows the
# key to add; `migrate` moves old files).
_ZONE_FREE_CMDS = ("format", "format-map", "migrate", "init", "help")


def _normalize_settings(cfg: Dict[str, Any]) -> None:
    """Canonical `country` (canada|usa) and a checked `tax_date` for
    EVERY config reader, not only `run`'s validate_config: check-filed,
    audit and close-year passed the raw strings to the gains engine,
    whose argparse refused "Canada"/"CA"/"Settle" — the filed-year
    drift guard went silently off and the checklist called the crash
    drift (S031-21, S031-24). A missing country, a setting or table the
    country does not own, and a base currency that is not the
    country's are refused here, for every command."""
    from taxjson.lib.config_check import settings_problems
    problems = settings_problems(cfg)
    if problems:
        # The country, its ownership of every setting (partition audit
        # R1/R2: a missing country was Canada everywhere but `run`; a
        # Canada-only key was silently ignored or honoured in a US
        # project) and the base currency: lib/config_check.
        _die(problems[0] if len(problems) == 1 else
             "taxjson.toml:\n  " + "\n  ".join(problems))
    table = cfg["settings"]
    # The zone crypto UTC stamps are dated in (the parsers and
    # crypto-sends read TAXJSON_LOCAL_TZ): the project's setting wins
    # over the environment, so every command and stage of this project
    # dates a crypto row the same way (partition INPUTS-09); the
    # variable applies only OUTSIDE a project (re-audit A2-0165). There
    # is no default zone (CA-DATE-12 / US-DATE-11): a project with a
    # crypto account and no setting stops here, naming the key — one
    # user's zone silently dated everyone's midnight fills. `tjs
    # format` / `migrate` do not come through here.
    import os as _os
    from taxjson.lib.timezones import (ENV_LOCAL_TZ, missing_timezone_message, timezone_typo_note)
    _tz = table.get("local_timezone")
    if not _tz and _current_cmd() in _ZONE_FREE_CMDS:
        pass                # they only read or rewrite the config text
    elif not _tz:
        _crypto = sorted(str(n) for n, a in
                         accounts_of(cfg).items()
                         if a.crypto is True)
        if _crypto:
            _die(f"[accounts.{_crypto[0]}] is a crypto account"
                 + (f" (and {len(_crypto) - 1} more)" if len(_crypto) > 1
                    else "")
                 + " but [settings] has no local_timezone"
                 # A misspelled key: this stops before the unknown-key
                 # warning that names it (backlog TJ-0034).
                 + timezone_typo_note(table) + " — "
                 + missing_timezone_message("project")
                 # The scaffold has a crypto account whether or not the
                 # user holds any coins (new-user study).
                 + f" If you have no crypto, delete the "
                   f"[accounts.{_crypto[0]}] section from taxjson.toml "
                   f"instead.")
        # No crypto account: an outer TAXJSON_LOCAL_TZ still never dates
        # this project's rows (a crypto file in another account is
        # refused by its parser with the same message).
        _os.environ.pop(ENV_LOCAL_TZ, None)
    else:
        _os.environ[ENV_LOCAL_TZ] = _tz
    srcs = table.get("source_currencies")
    if isinstance(srcs, list) and all(isinstance(c, str) for c in srcs):
        table["source_currencies"] = [c.strip().upper() for c in srcs]
    # year: a typo'd 2204 / 1850 / 0 built empty books and every filing
    # total read 0 with exit 0; `true` failed minutes later inside the
    # gains stage (R1-256). Same plausible range `tjs init` enforces.
    year = table.get("year")
    if year is not None:
        if isinstance(year, bool) or not isinstance(year, int):
            _die(f"[settings] year must be an integer tax year, "
                 f"got {year!r}")
        _max_year = date_cls.today().year + 1
        if not 1900 <= year <= _max_year:
            _die(f"[settings] year = {year} is not a plausible tax year "
                 f"(expected 1900..{_max_year})")


# Top-level tables taxjson.toml may carry. Anything else is ignored —
# which is why a misspelled [estimates] / [Estimate] / [instalment]
# silently fell back to 0 other income or no instalment schedule
# (R1-216, R1-257).
# The key lists live in lib/config_check (shared with the template).


def _did_you_mean(key: str, valid) -> str:
    import difflib
    close = difflib.get_close_matches(str(key).lower(), list(valid), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _config_table_warnings(cfg: Dict[str, Any]) -> List[str]:
    """Unknown top-level tables/keys and unknown [estimate] /
    [instalments] keys. Shared by `run` (validate_config) and by the
    commands that READ those tables (estimate, sum, instalments), which
    used to use 0 for a misspelled key with no word (S038-13)."""
    out: List[str] = []
    for key in cfg:
        if key in _TOP_LEVEL_TABLES:
            continue
        if key == "fetch" and isinstance(cfg[key], dict):
            out.append("the retired [fetch.<account>] tables are "
                       "ignored — `tjs fetch` reads `brokerage` + "
                       "`account` / `query_id` under [accounts.<name>]")
            continue
        what = ("table" if isinstance(cfg[key], dict) else "key")
        out.append(f"unknown top-level {what} "
                   f"{'[' + key + ']' if what == 'table' else repr(key)} "
                   f"is ignored{_did_you_mean(key, _TOP_LEVEL_TABLES)}")
    for table, allowed in (("instalments", _INSTALMENTS_KEYS),
                           ("estimate", _ESTIMATE_KEYS)):
        tbl = cfg.get(table)
        if not isinstance(tbl, dict):
            continue
        for key in tbl:
            if key not in allowed:
                out.append(f"unknown [{table}] key {key!r} is "
                           f"ignored{_did_you_mean(key, allowed)}")
    return out


def _unknown_setting_warnings(cfg: Dict[str, Any]) -> List[str]:
    """One warning per [settings] key this taxjson does not read ([] for a
    `settings` that is no table: load_config refuses it in its own words)."""
    return [f"unknown [settings] key {key!r} is ignored"
            f"{_did_you_mean(key, _SETTINGS_KEYS)}"
            for key in settings_of(cfg).present if key not in _SETTINGS_KEYS]


def _refuse_bad_account_types(cfg: Dict[str, Any]) -> None:
    """Die on an [accounts.*] entry whose `type` is missing or not
    exactly taxable/sheltered. Applied by EVERY config reader
    (load_config, with or without missing_ok), not only `run`'s validate_config: the
    filing commands partition on an exact string match, so a type
    edited after the run ("Taxable") silently dropped the account from
    estimate, sum, form-export and the close-year lock (R1-268). Silent
    on a valid config."""
    from taxjson.lib.config_check import account_type_problems
    problems = account_type_problems(cfg)
    if problems:
        _die(problems[0] if len(problems) == 1
             else "\n  ".join(["invalid account types:"] + problems))


# Every known [settings] key, and the country that owns it, lives in
# lib/country.SETTING_COUNTRY (one table for the config check, the
# commands and scripts/check_tax_rules.py).
_SETTINGS_KEYS = tuple(_SETTING_COUNTRY)
# [accounts.<name>] keys: lib/config_check.ACCOUNT_KEYS (_ACCOUNT_KEYS).


def _brokerage_account_flags(acfg: Dict[str, Any]) -> List[str]:
    """taxjson-brokerage flags an [accounts.<name>] table asks for:
    `combined_broker_accounts = true` (every broker account in the
    label's statements is the user's and taxable together — the
    'statement spans N accounts' ATTENTION becomes a NOTE; the parser
    refuses it on a sheltered label unless the statement shows one
    plan). config_check refuses a non-boolean value."""
    acfg = typed_account(acfg)
    flags = (["--combined-broker-accounts"]
             if acfg.combined_broker_accounts is True else [])
    # Webull's exercise/assignment charge: a broker convention the user
    # states (config_check checks the value; none: nothing inferred).
    if acfg.exercise_fee is not None:
        flags += ["--exercise-fee", f"{float(acfg.exercise_fee):g}"]
    # RBC's year-end posting day (default 06-30 in the parser).
    if acfg.year_end_posting is not None:
        flags += ["--year-end-posting", str(acfg.year_end_posting)]
    return flags
_ACCOUNT_TYPES = ("taxable", "sheltered")


def _fetch_brokerages(cfg: Dict[str, Any]) -> Tuple[str, ...]:
    """The `brokerage` values the installed fetcher plugins serve —
    () when no account declares one (no plugin scan) or none is
    installed."""
    from taxjson.lib import fetchers as _FP
    if not _FP.declared_brokerages(cfg):
        return ()
    return tuple(_FP.served_brokerages())


def _fetch_account_keys(cfg: Dict[str, Any]) -> Tuple[str, ...]:
    """Extra [accounts.<name>] keys the installed fetcher plugins
    declare (beyond brokerage / account / query_id, always accepted).
    Scanned only when an account declares a `brokerage`."""
    from taxjson.lib import fetchers as _FP
    if not _FP.declared_brokerages(cfg):
        return ()
    return tuple(k for f in _FP.installed() for k in f.account_keys)


def _fetch_plugin_note(cfg: Dict[str, Any]) -> Optional[str]:
    """One note when accounts declare a `brokerage` but no fetcher
    plugin is installed: the keys are accepted (they only matter to
    `tjs fetch`), and the user learns how to get the command."""
    from taxjson.lib import fetchers as _FP
    declared = _FP.declared_brokerages(cfg)
    if not declared or _FP.installed():
        return None
    names = ", ".join(f"[accounts.{a}]" for a in declared)
    stale = _FP.mismatched()
    if stale:
        # Installed, of another version (TJ-2554): not "none installed".
        return (f"{names} brokerage is read only by `tjs fetch`, and "
                f"{stale[0]}")
    return (f"{names} brokerage is read only by `tjs fetch`, and no "
            f"fetcher is installed — {_FP.INSTALL_HINT}")


def validate_config(cfg: Dict[str, Any],
                    inputs_dir: Optional[Path] = None, *,
                    inputs_shown: str = "inputs",
                    shared: bool = False) -> List[str]:
    """Schema-check taxjson.toml BEFORE any stage runs. Fatal problems
    sys.exit (they would otherwise surface minutes into a run, or worse,
    not at all — a typo'd account `type` matches neither partition and the
    account silently vanishes from the run while stale reports keep
    looking healthy). Unknown keys and populated-but-unconfigured input
    dirs come back as warnings for the caller to print.

    `inputs_dir` is the folder the run reads its exports from, named
    `inputs_shown` in the messages ("../inputs" when every year shares
    one: `shared`). The checks for exports it would not read (a
    subfolder, a spreadsheet) run on it either way: a shared folder
    skipped them, and its spreadsheet or year subfolder was left out of
    the books at exit 0 (backlog TJ-0482, TJ-0582). A shared folder's
    accounts another year has are the run's own note, not a warning."""
    import difflib

    def _suggest(key: str, valid: Tuple[str, ...]) -> str:
        close = difflib.get_close_matches(key, valid, n=1)
        return f" (did you mean {close[0]!r}?)" if close else ""

    table = settings_of(cfg)
    warnings: List[str] = _unknown_setting_warnings(cfg)
    # Said here: a command chained after the run does not repeat it.
    run_state.current.config_warned.update(warnings)

    year = table.year
    if year is not None and not isinstance(year, int):
        _die(f"[settings] year must be an integer, got {year!r}")
    # Country (required), date basis, base currency, and every
    # setting/table the country does not own: the same check every
    # config reader applies (lib/config_check.settings_problems).
    from taxjson.lib.config_check import settings_problems
    _problems = settings_problems(cfg)
    if _problems:
        _die(_problems[0] if len(_problems) == 1 else
             "taxjson.toml:\n  " + "\n  ".join(_problems))
    table = settings_of(cfg)
    # tax_date against the country's practice: allowed (both countries
    # own the key) but said, once per run (partition INPUTS-05).
    from taxjson.lib.country import default_tax_date as _dtd
    _td, _c = table.tax_date, table.country
    if _td is not None and _td != _dtd(_c):
        warnings.append(
            f"[settings] tax_date = \"{_td}\" in a {_c} project: "
            + ("the IRS dates a disposition by its TRADE date (the US "
               "default)" if _c == "usa" else
               "CRA practice dates a disposition by its SETTLEMENT date "
               "(the Canadian default)")
            + " — keep it only if you mean to depart from that")
    # option_premium_timing, option_grant_timing_since (1900..2100, as
    # S047-20 set) and option_buyback_loss_superficial are refused by
    # load_config (lib/config_check.bool_setting_problems, every config
    # reader) before this runs; the twin checks that stood here were
    # dead code no test could reach (audit A2-1211).
    _froc = table.foreign_return_of_capital
    if _froc is not None and _froc not in ("dividend", "acb"):
        _die(f"[settings] foreign_return_of_capital must be \"dividend\" or \"acb\" (got {_froc!r}).")
    _fs = table.futures_settle
    if _fs is not None and _fs not in ("trade", "next_day"):
        _die(f"[settings] futures_settle must be \"trade\" or \"next_day\" (got {_fs!r}).")
    try:
        _income_rules(table)
    except ValueError as e:
        _die(str(e))
    if table.has("cross_asset"):
        # Each country's own rule (audit A2-1241, A2-1273): the US
        # engine only flags a long call (US-WASH-12).
        try:
            _ca_usa = _country(table) == "usa"
        except SystemExit:
            _ca_usa = False
        warnings.append(
            "[settings] cross_asset is retired and ignored: "
            + ("a long call bought in a share loss's window is always "
               "flagged as a warning (§1091 'option to acquire'; the US "
               "engine does not deny on it); the identical option is a "
               "replacement for an option loss. Delete the line."
               if _ca_usa else
               "a long call on the same shares is always replacement "
               "property for a share loss (s.54 'a right to acquire'); "
               "shares never replace an option, and only the identical "
               "contract replaces an option. Delete the line."))
    src = table.source_currencies
    if src is not None and (not isinstance(src, list)
                            or not all(isinstance(c, str) for c in src)):
        _die(f"[settings] source_currencies must be a list of "
                 f"currency codes, got {src!r}")

    # Unknown top-level tables ([estimates]) and unknown [estimate] /
    # [instalments] keys — the same check the commands reading those
    # tables print (R1-216, R1-257, S038-13).
    warnings.extend(_config_table_warnings(cfg))
    accounts = cfg.get("accounts", {})
    for name, acfg in accounts.items():
        if not isinstance(acfg, _cabc.Mapping):
            _die(f"[accounts.{name}] must be a table")
        for key in acfg:
            if key not in _ACCOUNT_KEYS \
                    and key not in _fetch_account_keys(cfg):
                warnings.append(f"unknown [accounts.{name}] key {key!r} is "
                                f"ignored{_suggest(key, _ACCOUNT_KEYS)}")
    # Missing / invalid `type`: fatal, and the SAME check every other
    # config reader applies (lib/config_check.py).
    _refuse_bad_account_types(cfg)
    for name, acfg in accounts.items():
        for flag in ("crypto", "transfers"):
            if flag in acfg and not isinstance(acfg[flag], bool):
                _die(f"[accounts.{name}] {flag} must be "
                         f"true/false, got {acfg[flag]!r}")
        _plan = acfg.get("plan")
        if _plan is not None and str(_plan).strip().lower() \
                not in _PLAN_KINDS:
            # Name and suggest only the project's own country's plans
            # (a US 'hsa' was offered Canada's 'fhsa'; A2-1272).
            try:
                _pk = _plan_kinds(_country(settings_of(cfg)))
            except SystemExit:
                _pk = list(_PLAN_KINDS)
            warnings.append(
                f"[accounts.{name}] plan {_plan!r} is not a known plan "
                f"kind ({' | '.join(_pk)}) and is ignored"
                f"{_suggest(str(_plan).strip().lower(), _pk)}")
        elif _plan is not None and _ptype_conflict(acfg):
            warnings.append(
                f"[accounts.{name}] plan {str(_plan).strip().lower()!r} "
                f"contradicts type = {acfg.get('type')!r} — the type "
                f"decides how the account is taxed; fix one of them")
        brok = acfg.get("brokerage")
        _served = _fetch_brokerages(cfg)
        if brok is not None and _served and str(brok) not in _served:
            # Only the installed fetcher plugins know their brokerages
            # (lib/fetchers); with none installed the key is accepted
            # and cmd_run prints one note instead (_fetch_plugin_note).
            warnings.append(
                f"[accounts.{name}] brokerage {brok!r} is not a fetch "
                f"source ({' | '.join(_served)}) — `tjs fetch` "
                f"will refuse it{_suggest(str(brok), _served)}")

    # CSVs in a SUBFOLDER of an account's inputs are never read (only
    # files directly in inputs/<account>/ are) — say so instead of
    # silently dropping, e.g., inputs/margin/2025/*.csv.
    _in = inputs_shown.rstrip("/") or "inputs"
    if inputs_dir is not None and inputs_dir.is_dir():
        for name in accounts:
            adir = inputs_dir / str(name)
            if not adir.is_dir():
                continue
            for sub in sorted(adir.iterdir()):
                if (not sub.is_dir() or sub.name.startswith(".")
                        or sub.suffix.lower() == ".numbers"):
                    continue
                # Spreadsheets too: an .xlsx here got no word at all
                # (S043-13).
                n_csv = sum(1 for f in sub.rglob("*")
                            if f.is_file() and f.suffix.lower()
                            in (".csv", ".tt") + SPREADSHEET_SUFFIXES)
                if n_csv:
                    warnings.append(
                        f"{_in}/{name}/{sub.name}/ holds {n_csv} "
                        f"CSV/.tt/spreadsheet file(s) that are NOT read "
                        f"— only CSV and .tt files "
                        f"directly in {_in}/{name}/ are processed; move "
                        f"them up a level (or out of {_in}/ if they are "
                        f"not meant for this account)")
    # Spreadsheet exports (Questrade's default download is .xlsx) sit
    # next to the CSVs but are never read: the run exited 0 with every
    # trade in them missing and never named the file (R1-64/R1-248).
    # Fatal unless the file's CSV conversion sits beside it.
    if inputs_dir is not None and inputs_dir.is_dir():
        unconverted: List[str] = []
        for name in accounts:
            adir = inputs_dir / str(name)
            csv_stems = {p.stem.lower() for p in input_files(adir, ".csv")}
            for sheet in spreadsheet_inputs(adir):
                rel = f"{_in}/{name}/{sheet.name}"
                if sheet.stem.lower() in csv_stems:
                    warnings.append(
                        f"{rel} is not read (spreadsheets never are); its "
                        f"CSV conversion {sheet.stem}.csv is. Move the "
                        f"spreadsheet out of {_in}/ to silence this.")
                else:
                    unconverted.append(rel)
        if unconverted:
            _die(f"spreadsheet export(s) in {_in}/ are NOT read — only "
                 ".csv and .tt files are, so every trade in them would be "
                 "missing from the books:\n    "
                 + "\n    ".join(unconverted)
                 + "\n  Convert each to CSV next to it (`taxjson-xlsx-to-csv "
                 "FILE.xlsx -o FILE.csv`, or the broker's CSV download) "
                 f"and move the spreadsheet out of {_in}/.")
    # Inputs dir with data but no [accounts.*] entry: today that folder is
    # silently ignored — the inverse of the configured-but-unpopulated
    # warning the run loop already prints. A shared folder's other
    # accounts belong to other years (the run's own note names them).
    if inputs_dir is not None and inputs_dir.is_dir() and not shared:
        for sub in sorted(inputs_dir.iterdir()):
            # inputs/slips/ is the checklist's home for T5008/1099-B
            # CSVs (read by reconcile-slips) — not an account folder.
            if not sub.is_dir() or sub.name in accounts \
                    or sub.name == "slips":
                continue
            if input_files(sub, ".csv") or input_files(sub, ".tt") \
                    or spreadsheet_inputs(sub):
                warnings.append(f"inputs/{sub.name}/ contains data but has "
                                f"no [accounts.{sub.name}] section — it "
                                f"will NOT be processed")
    return warnings


# ---------------------------------------------------------------- broker detection

# Brokers with corp-action extractors (taxjson-corp-actions): the
# registry's (ids; the pipeline's groups are ids). Others have no
# extractor and skip that stage.
CORP_ACTION_BROKERS = corp_action_brokers()
# Brokers whose rows use the run's rates: their parse declares the rates
# file (the registry's; TJ-2497).
RATE_READING_BROKERS = rate_reading_brokers()


def _country(settings: Optional[Dict[str, Any]]) -> str:
    """The project's canonical country from a [settings] table. A
    missing or unknown value dies with the one resolver message — never
    a silent Canada (partition audit R1: every reader but `run` used to
    default to it)."""
    from taxjson.lib.country import CountryError, settings_country
    try:
        return settings_country(settings)
    except CountryError as e:
        _die(f"{e}{'' if as_settings(settings).country else ' in taxjson.toml'}")


def input_files(dirpath: Path, suffix: str) -> List[Path]:
    """Input files by suffix, CASE-INSENSITIVE. Excel and several
    brokers export TRADES.CSV / START.TT; glob("*.csv") silently
    ignored them while the GUI import accepted them — a run that
    exits 0 with those trades missing (REVIEW-2026-07-ui #1)."""
    if not dirpath.is_dir():
        return []
    # Hidden files (macOS '._x.csv' AppleDouble) and Office lock files
    # ('~$x.csv') are never inputs; the checklist's input fingerprint
    # skips them too, so the two agree on the input set (A2-1145,
    # A2-1166).
    out = []
    for p in sorted(dirpath.iterdir()):
        if p.suffix.lower() != suffix or p.name.startswith((".", "~$")):
            continue
        if _PL.redacted_copy_beside_exports(p):
            # `tjs redact FILE`'s copy beside its export: booked
            # twice (backlog TJ-0476); group_inputs_detailed says so.
            continue
        if p.is_file():
            out.append(p)
        elif p.is_symlink():
            # A statement that is a dangling (or looping) symlink was
            # left out of the books at exit 0 (re-audit A2-0143/A2-0403).
            import os as _os
            try:
                tgt = _os.readlink(p)
            except OSError:
                tgt = "?"
            _die(f"{p} is a symlink to {tgt}, which does not exist (or "
                 f"loops) — its rows would be missing from the books. "
                 f"Fix the link or remove it.")
    return out


# Spreadsheet suffixes a broker export may arrive in. None is read by
# the run; validate_config refuses them unless converted (R1-64; Apple
# Numbers too, A2-1156 — lib/checklist.SPREADSHEET_SUFFIXES is the same).
SPREADSHEET_SUFFIXES = (".xlsx", ".xls", ".xlsm", ".ods", ".numbers")


def spreadsheet_inputs(dirpath: Path) -> List[Path]:
    """Spreadsheet files directly in an inputs folder (never read)."""
    if not dirpath.is_dir():
        return []
    # A Numbers document can be a package DIRECTORY (macOS bundle copied
    # as-is): it is a spreadsheet all the same (re-audit A2-0714).
    return sorted(p for p in dirpath.iterdir()
                  if (p.is_file() or (p.is_dir()
                                      and p.suffix.lower() == ".numbers"))
                  and not p.name.startswith((".", "~$"))
                  and p.suffix.lower() in SPREADSHEET_SUFFIXES)


# Project-root files the per-account stages read (their content is part
# of each account's input fingerprint; taxjson.toml is added there too).
# (missing_history.json is no longer read: its .tt lines are account
# inputs.)
_PROJECT_ROOT_INPUTS = ("ticker.map", "tobase.map")


def _unreadable_project_inputs(root: Path) -> List[str]:
    """Project-root input names that exist as a symlink whose target is
    missing (or loops), or as a directory — never "absent" (A2-0313)."""
    import os
    out = []
    for name in _PROJECT_ROOT_INPUTS:
        p = _PL.project_map_path(root, name)
        if p.is_symlink() and not p.exists():
            try:
                tgt = os.readlink(p)
            except OSError:
                tgt = "?"
            out.append(f"{name} is a symlink to {tgt}, which does not "
                       f"exist (or loops)")
        elif p.is_dir():
            out.append(f"{name} is a directory, not a file")
    return out


def _refuse_legacy_project_files(root: Path) -> None:
    """Stop (exit 2) while the project still holds an old per-purpose
    file whose contents now live in ticker.map / taxjson.toml
    (yf_ticker.map, distributions.map, ...): it is no longer read, and
    running on without it would silently drop its rules. Only `tjs
    migrate` (which moves them) runs past this."""
    from taxjson.lib.migrate import legacy_files, legacy_message
    names = legacy_files(root)
    if names:
        _die_input(legacy_message(names))


# The folders taxjson writes into: generated state, reports, the filed
# lock, and the account folders (elections manifest, crypto_sends.tt).
_WRITTEN_DIRS = ("work", "reports", "filed", "export", "inputs")


def _refuse_outside_dir_links(root: Path, accounts: Dict[str, Any]) -> None:
    """Die (exit 2) when a folder taxjson writes into is a symlink that
    leaves the project: the books, reports and elections would land in
    whatever it points at (2026-10 security review LOW c). A link inside
    the project is kept, as for ticker.map (safe_write.link_outside)."""
    from taxjson.lib.safe_write import link_outside
    names = list(_WRITTEN_DIRS) + [f"inputs/{a}" for a in accounts]
    bad = []
    for n in names:
        # (inputs/ shared by every year — lib/project_layout — is judged
        # against the folder holding the year folders)
        p = _PL.project_path(root, n)
        target = link_outside(p, root if p.is_relative_to(root)
                              else _PL.write_boundary(root))
        if target is not None:
            bad.append(f"{n}/ -> {target}")
    if bad:
        _die_input("folder(s) that are symlinks to outside the project — "
                   "taxjson writes there; nothing was run:\n    "
                   + "\n    ".join(bad),
                   "Replace each link with a real folder (move its "
                   "contents in), or run in the folder the link points "
                   "into.")


def _refuse_unreadable_project_inputs(root: Path) -> None:
    """Die when a project-root input exists as a name but cannot be read.
    Every config reader calls it, so no command (tips, sanity, harvest,
    fees, ...) builds as if ticker.map were absent (re-audit A2-0401 /
    A2-0144; `run` since A2-0313). An old file whose contents now live
    elsewhere (a per-purpose map, missing_history.json) stops it first,
    naming `tjs migrate`."""
    _refuse_legacy_project_files(root)
    _unreadable = _unreadable_project_inputs(root)
    if _unreadable:
        # exit 2: inputs that cannot be read (re-audit A2-0164, A2-1438)
        _die_input("project file(s) that exist but cannot be read — fix or "
             "remove each; nothing was run:\n    "
             + "\n    ".join(_unreadable))


def _income_rules(settings: Dict[str, Any]):
    """The project's lib/income_dating rules (country + overrides)."""
    from taxjson.lib.income_dating import IncomeRules
    return IncomeRules.from_settings(settings)


# One per-country plan table (lib/country.PLAN_COUNTRY): the HSA, LIF,
# RDSP ... its missing_history twin knew were missing, and the other
# country's plans were accepted silently (audit A2-0739, A2-1272,
# A2-1332).
_PLAN_NAMES = tuple(k for k in _PLAN_COUNTRY
                    if k not in ("taxable", "sheltered"))
_PLAN_KINDS = tuple(_PLAN_COUNTRY)


def _ptype_conflict(acfg: Dict[str, Any]) -> bool:
    """A registered plan on a taxable account, or plan = "taxable" on a
    sheltered one (audit A2-1332): tips read the plan and skipped
    the account's checks."""
    plan = str(acfg.get("plan") or "").strip().lower()
    typ = acfg.get("type")
    if plan in ("", "sheltered"):
        return plan == "sheltered" and typ == "taxable"
    if plan == "taxable":
        return typ == "sheltered"
    return plan in _PLAN_NAMES and typ == "taxable"
