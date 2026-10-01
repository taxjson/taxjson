#!/usr/bin/env python3
"""taxjson — one-command orchestrator for the full tax pipeline.

Replaces the per-account shell scripts (curr.sh, sheltered.sh, margin.sh,
crypto.sh, export.sh, reports.sh) with a single command that reads a
short TOML config and walks a conventional inputs/ tree.

Directory layout:
    taxjson.toml          # config (year, country, base_currency, accounts)
    inputs/
      margin/             # account name = folder name
        *.csv             # broker auto-detected by CSV content
        *.tt              # optional starting-position files
        manifest.json     # corp-actions election manifest (created by the
                          # pipeline on first election; COMMIT this file —
                          # elections are decisions, not rebuildable data)
      rrsp/  tfsa/  ...
    ticker.map            # optional — symbol rules (GLOBAL/TOBASE/JOURNAL/DELETE/DISTINCT)
    ticker_extraction_overrides.txt  # optional — description-keyed ticker fixes
    reports/              # all outputs land here, overwritten on re-run
    work/                 # intermediate JSON (--fast reuses these via mtime)

Subcommands:
    taxjson run                       # full pipeline, FULL rebuild (default —
                                      # never serves stale/cached data)
    taxjson run --fast                # incremental: mtime-cached stages with
                                      # unchanged inputs are skipped
    taxjson run --account margin      # one account
    taxjson sum                       # cross-account realized-gains summary
    taxjson init [DIR] --country ca   # scaffold a new project directory
    taxjson help [COMMAND]            # every other subcommand
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.cli_diag import note
from taxjson.lib.cli_diag import tax_year as _tax_year_arg
from taxjson.lib.numeric import nonneg_float_arg as _nonneg_float_arg
from taxjson.lib.pipeline import (income_dating_flags,
                                  option_timing_flags, tt_json_path)
from taxjson.lib.report_model import (align_columns, fmt_money,
                                      fmt_qty, format_report_table)

from taxjson.lib.tomlcompat import tomllib


# ---------------------------------------------------------------- subprocess

# Two scripts whose module name doesn't follow the s/-/_/ pattern of the
# console-script name in pyproject.toml. Everything else uses the default
# `taxjson-foo-bar` → `taxjson_foo_bar` mapping.
_MODULE_OVERRIDES = {
    "taxjson-to-base-curr": "to_base_curr",
    "taxjson-fill-crypto": "fill_crypto_prices",
}


def _cmd(name: str) -> List[str]:
    """Build the invocation for one of the taxjson sub-tools. Uses
    `python -m taxjson.bin.<module>` so the wrapper works whether or not
    the console scripts are on PATH."""
    module = _MODULE_OVERRIDES.get(name, name.replace("-", "_"))
    return [sys.executable, "-m", f"taxjson.bin.{module}"]


def run_to_file(cmd: List[str], out_path: Path, *, capture_diag: bool = True,
                interactive: bool = False) -> None:
    """Run a sub-tool, writing stdout to out_path.

    stderr is normally captured, never echoed live — the console stays
    clean. When capture_diag is set, stderr is persisted to an
    `<out_path>.diag` sidecar that the .sum report's DIAGNOSTICS section
    folds in. On a non-zero exit the stderr is surfaced so a real failure
    isn't hidden behind the wrapper's own traceback.

    stdin is /dev/null for every stage so a sub-tool can never block the
    pipeline waiting on input it will never get.

    `interactive=True` is for the one stage that legitimately prompts —
    taxjson-corp-actions' election questions. It lets stderr (the prompt)
    and stdin (your answer) pass through to the terminal while stdout still
    goes to the file. At a non-TTY (CI/cron) the tool falls back to its own
    'needs an election' error, which surfaces here as a normal failure.
    """
    from taxjson.lib.dispatch import run_cmd
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a .part sidecar and rename into place only on success. A failed
    # (or Ctrl-C'd, e.g. at the corp-actions election prompt) stage must never
    # leave a truncated output file: its fresh mtime would make needs_rebuild
    # treat the corrupt file as valid and feed it to downstream stages. On
    # failure the previous good output (if any) survives with its old mtime,
    # so the next run correctly rebuilds the stage.
    tmp_path = out_path.with_name(out_path.name + ".part")
    try:
        if interactive:
            # The prompt owns stderr, so no fresh .diag is captured —
            # but a STALE one from a prior non-interactive run of this
            # stage would keep polluting the .sum DIAGNOSTICS banner
            # until hand-deleted from work/ (KNOWN_ISSUES). Clear it.
            out_path.with_name(out_path.name + ".diag").unlink(
                missing_ok=True)
            with tmp_path.open("wb") as f:
                result = run_cmd(cmd, stdout=f,      # stderr+stdin keep TTY
                                 interactive=True)
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd)
            tmp_path.replace(out_path)
            return
        with tmp_path.open("w", encoding="utf-8") as f:
            result = run_cmd(cmd, stdout=f)
        stderr_text = result.stderr or ""
        if capture_diag:
            diag_path = out_path.with_name(out_path.name + ".diag")
            if stderr_text:
                diag_path.write_text(stderr_text, encoding="utf-8")
            elif diag_path.exists():
                diag_path.unlink()
        if result.returncode != 0:
            if stderr_text:
                sys.stderr.write(stderr_text)
            raise subprocess.CalledProcessError(result.returncode, cmd)
        tmp_path.replace(out_path)
    finally:
        tmp_path.unlink(missing_ok=True)


# Matches the per-file count lines `taxjson-brokerage` prints to
# stderr (e.g. `  margin_2025.csv: 142 tax objects`). Used by
# stage_account to echo the counts into the wrapper's console output
# so a keen eye can spot a 0-object parse of a non-empty file before
# downstream stages silently propagate the empty data.
_PARSE_COUNT_RE = re.compile(r'^\s+.+: \d+ tax objects\b')
# taxjson-brokerage's zero-row warning (captured in the parse .diag).
_ZERO_TX_RE = re.compile(r'^warning: (.+?) parsed to 0 transactions\b')


def _load_holdings_summary(toml_path: Path) -> Dict[str, Tuple[float, float]]:
    """Parse a holdings.toml into `{symbol: (qty, total_cost)}`. Used
    by `print_holdings_diff` to compare snapshots. Returns an empty
    dict for missing files or when no TOML reader is available."""
    if tomllib is None or not toml_path.exists():
        return {}
    try:
        with toml_path.open('rb') as f:
            doc = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    out: Dict[str, Tuple[float, float]] = {}
    for h in doc.get('holding', []):
        sym = h.get('symbol')
        if not sym:
            continue
        out[sym] = (
            float(h.get('quantity', 0.0)),
            float(h.get('total_cost', 0.0)),
        )
    return out


# Alias: tests and older callers import `_fmt_qty` from here.
from taxjson.lib.report_model import fmt_qty6 as _fmt_qty  # noqa: E402


def maybe_print_holdings_diff(prev_path: Path, curr_path: Path) -> None:
    """Print the holdings diff if a baseline snapshot exists; silent
    otherwise (so first runs don't emit a confusing "all positions are
    new" block). Encapsulated so the first-run-silent rule is testable
    in isolation from `stage_account`."""
    if not prev_path.exists():
        return
    print_holdings_diff(
        _load_holdings_summary(prev_path),
        _load_holdings_summary(curr_path),
    )


def print_holdings_diff(prev: Dict[str, Tuple[float, float]],
                         curr: Dict[str, Tuple[float, float]]) -> None:
    """Emit a one-line-per-changed-symbol summary so the user can
    sanity-check what moved since the last run. Quiet when nothing
    changed (no news is good news; the standard `→ holdings.toml`
    print already confirms the file was written)."""
    all_syms = set(prev) | set(curr)
    changes: List[str] = []
    qty_eps = 1e-9
    for sym in sorted(all_syms):
        p_qty, _ = prev.get(sym, (0.0, 0.0))
        c_qty, _ = curr.get(sym, (0.0, 0.0))
        if abs(p_qty - c_qty) < qty_eps:
            continue
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
        print(f"  holdings changes vs prior run ({len(changes)}):")
        for line in changes:
            print(line)


def echo_parse_stats(out_path: Path) -> None:
    """Re-emit per-file transaction counts and parse warnings from the
    parser's stderr (captured to `<out_path>.diag` by `run_to_file`)
    onto the wrapper's stdout. Visibility is the safety net that
    would have caught the cycle-4 Webull `_find_header` regression
    instantly instead of needing an empty cache file to surface the
    silent zero-tx output."""
    diag_path = out_path.with_name(out_path.name + ".diag")
    if not diag_path.exists():
        return
    for line in diag_path.read_text(errors="replace").splitlines():
        if _PARSE_COUNT_RE.match(line):
            # Keep the parser's own leading indent — it visually nests
            # the per-file counts under the `parse {broker}: …` header.
            print(line)
        elif line.startswith("warning: ") and " parsed to 0 transactions" in line:
            # Indent the warning to match per-file count nesting.
            print(f"  {line}")
        elif line.startswith(UNBOOKED_PREFIX):
            # Input rows the parser knows are real events but could not
            # book (e.g. Kraken ledger trades with no trades-export
            # fill): the tax numbers are missing them. Console, always.
            print(f"  {line}")
        elif line.startswith(ATTENTION_PREFIX):
            # Statement coverage / identity the numbers silently depend
            # on (an IB statement ending before year end, no Cash Report
            # to reconcile against). Console, always.
            print(f"  {line}")


# Parser warning prefix for rows that are known tax events the parser
# could NOT book. `taxjson run` echoes these to the console and, under
# --strict, refuses to publish (R1-104).
UNBOOKED_PREFIX = "warning: UNBOOKED:"
# Parser warning prefix for input COVERAGE / identity problems (lib/
# brokerages/ib_extractor.ATTENTION_PREFIX): echoed to the console.
ATTENTION_PREFIX = "warning: ATTENTION:"


def unbooked_lines(out_path: Path) -> List[str]:
    diag_path = out_path.with_name(out_path.name + ".diag")
    if not diag_path.exists():
        return []
    return [ln for ln in diag_path.read_text(errors="replace").splitlines()
            if ln.startswith(UNBOOKED_PREFIX)]


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
                            "_gains_wash.json.diag")
_DIAG_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def collect_diagnostics(cache: Path, account: str, *,
                        post_pass: bool = True) -> str:
    """Concatenate persisted stderr diagnostics across an account's
    pipeline stages, keeping ok/warning/note/error lines — bare or
    prog-prefixed (`taxjson-fill-crypto: warning: …`) — plus their
    indented continuation lines. post_pass=False leaves out the
    cross-account passes' sidecars (_POST_PASS_DIAG_SUFFIXES).

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
    year: Optional[int] = None
    try:
        _cfg = load_config(cache.parent)
        accounts_cfg = (_cfg.get("accounts") or {})
        siblings = [a for a in accounts_cfg
                    if a != account and a.startswith(f"{account}_")]
        _y = (_cfg.get("settings") or {}).get("year")
        year = _y if isinstance(_y, int) else None
    except (Exception, SystemExit):
        pass
    blocks: List[List[str]] = []
    for diag in sorted(cache.glob(f"{account}_*.diag")):
        if any(diag.name.startswith(f"{s}_") for s in siblings):
            continue
        if not post_pass and diag.name.endswith(_POST_PASS_DIAG_SUFFIXES):
            continue
        kept_prev = False
        for line in diag.read_text(errors="replace").splitlines():
            is_marker = bool(_DIAG_MARKER_RE.match(line.strip()))
            is_cont = kept_prev and line[:1].isspace() and bool(line.strip())
            if is_marker:
                blocks.append([line.rstrip()])
                kept_prev = True
            elif is_cont:
                blocks[-1].append(line.rstrip())
            else:
                kept_prev = False
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
    from taxjson.lib.dispatch import run_cmd
    result = run_cmd(cmd, capture_output=True)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return (result.stdout or "").encode("utf-8")


def _exec_tool(cmd: List[str], cwd: Optional[str] = None) -> None:
    """Wrapper-command tail: run the tool (in-process, streaming live)
    and exit with its return code — replaces the historical
    `_exec_tool(cmd)`."""
    from taxjson.lib.dispatch import run_cmd
    raise SystemExit(run_cmd(cmd, cwd=cwd).returncode)


_CONFIG_PATH: Optional[Path] = None
_PACKAGE_MTIME_CACHED: Optional[float] = None


def _package_mtime() -> float:
    """Maximum mtime across every `.py` file in the installed taxjson
    package. Used as an implicit dependency for every cached stage so
    that an upgrade / edit to any engine, parser, or wrapper module
    invalidates the cache (which only `run --fast` consults — the
    default run rebuilds everything).

    Without this, a code fix (e.g. a parser regression repair) would
    let `--fast` silently reuse pre-fix cached output until the user
    remembered to drop the flag or touch `taxjson.toml`. That was the
    failure mode that hid the cycle-4 Webull `_find_header` regression
    (back when the cache was the default).

    Result is process-cached because the package files don't change
    underneath us mid-run.
    """
    global _PACKAGE_MTIME_CACHED
    if _PACKAGE_MTIME_CACHED is not None:
        return _PACKAGE_MTIME_CACHED
    import taxjson
    pkg_root = Path(taxjson.__file__).parent
    latest = 0.0
    for p in pkg_root.rglob('*.py'):
        try:
            latest = max(latest, p.stat().st_mtime)
        except OSError:
            continue
    _PACKAGE_MTIME_CACHED = latest
    return latest


def _package_fingerprint() -> str:
    """A CONTENT fingerprint of the installed taxjson package's .py
    sources (relative path + bytes). The mtime key above misses a code
    change whose files keep an older mtime (cp -p, rsync -a, tar x,
    touch -r) and a deleted module, so `run --fast` served output from
    the old code (S039-03); `run` compares this with the stamp the last
    complete run left in work/ and rebuilds everything on a mismatch."""
    import taxjson
    pkg_root = Path(taxjson.__file__).parent
    h = hashlib.sha256()
    for p in sorted(pkg_root.rglob("*.py")):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        h.update(p.relative_to(pkg_root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


_CODE_STAMP = ".code_fingerprint"


def needs_rebuild(out: Path, *inputs: Path) -> bool:
    """Mtime-based rebuild check — consulted only by `run --fast` (the
    default run passes force=True everywhere). Cached output is stale
    when:
      - it doesn't exist, or
      - any declared input is newer than it, or
      - `taxjson.toml` is newer than it (implicit dep via _CONFIG_PATH), or
      - any `.py` file in the installed taxjson package is newer than
        it (implicit dep via `_package_mtime()`).

    The config-file dependency catches edits to year / country /
    tax_date / base_currency. The package-source dependency catches
    engine and parser fixes (a `pip install -U taxjson` or a `git pull`
    in editable mode bumps file mtimes and invalidates the cache).
    Together they make `--fast` safe to reach for: when the cache
    returns a result, it's a result computed by the CURRENT code
    against the CURRENT config and inputs.
    """
    if not out.exists():
        return True
    out_mtime = out.stat().st_mtime
    for inp in inputs:
        if inp.exists() and inp.stat().st_mtime > out_mtime:
            return True
    if _CONFIG_PATH is not None and _CONFIG_PATH.exists():
        if _CONFIG_PATH.stat().st_mtime > out_mtime:
            return True
    if _package_mtime() > out_mtime:
        return True
    return False


# ---------------------------------------------------------------- config

def load_config(root: Path) -> Dict[str, Any]:
    if tomllib is None:
        _die("TOML support requires Python 3.11+ or `pip install tomli`")
    path = root / "taxjson.toml"
    if not path.exists():
        _die(f"no taxjson.toml in {root}. Run `taxjson init` first.")
    text = _read_config_text(path)
    try:
        cfg = tomllib.loads(text)
        # Account names build filesystem paths (inputs/<name>,
        # work/<name>_*) and sub-tool argv (--account <name>): a
        # "../x" name wrote artifacts OUTSIDE the project and a
        # "-x" name was parsed as a flag (2026-09 security audit).
        # Checked HERE, not in validate_config, because every
        # command (not just `run`) derives paths from the names.
        for _n in (cfg.get("accounts") or {}):
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*",
                                str(_n)):
                _die(f"[accounts.{_n!r}] is not a valid account "
                     f"name — use letters, digits, '_', '-' or "
                     f"'.' (must start with a letter, digit or "
                     f"'_'); it becomes file and directory names.")
            if str(_n).upper() == "COMBINED":
                # reports/wash_radar_COMBINED.* is the cross-account
                # radar: an account of that name had its own radar
                # replaced by it (S038-10).
                _die(f"[accounts.{_n}]: the name COMBINED is "
                     f"reserved for the cross-account wash radar — "
                     f"rename the account (and its inputs/{_n}/ "
                     f"folder).")
    except Exception as e:
        _die(f"{path} is not valid TOML: {e}")
    _refuse_bad_account_types(cfg)
    _normalize_settings(cfg)
    return cfg


def _read_config_text(path: Path) -> str:
    """taxjson.toml's text for tomllib. A directory or unreadable file
    tracebacked from every command (S040-03); a leading UTF-8 BOM (what
    Notepad saves) failed as "Invalid statement (at line 1, column 1)"
    with no hint (S038-04) — it is dropped, as TOML allows no BOM."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        _die(f"cannot read {path}: {e.strerror or e}")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        _die(f"{path} is not valid TOML: it is not UTF-8 text ({e}) — "
             f"save it as UTF-8.")


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
    settings = cfg["settings"]
    # The zone crypto UTC stamps are dated in (the parsers and
    # crypto-sends read TAXJSON_LOCAL_TZ): the project's setting wins
    # over the environment, so every command and stage of this project
    # dates a crypto row the same way (partition INPUTS-09).
    _tz = settings.get("local_timezone")
    if _tz:
        import os as _os
        _os.environ["TAXJSON_LOCAL_TZ"] = _tz
    srcs = settings.get("source_currencies")
    if isinstance(srcs, list) and all(isinstance(c, str) for c in srcs):
        settings["source_currencies"] = [c.strip().upper() for c in srcs]
    # year: a typo'd 2204 / 1850 / 0 built empty books and every filing
    # total read 0 with exit 0; `true` failed minutes later inside the
    # gains stage (R1-256). Same plausible range `taxjson init` enforces.
    year = settings.get("year")
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
_TOP_LEVEL_TABLES = ("settings", "accounts", "instalments", "estimate")
_INSTALMENTS_KEYS = ("basis", "prior_year_net_tax", "second_prior_net_tax",
                     "withheld", "prescribed_rate", "prescribed_rates",
                     "paid")


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
                       "ignored — `taxjson fetch` reads `brokerage` + "
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


_CONFIG_WARNED: set = set()


def _warn_config_tables(root: Path) -> None:
    """Print _config_table_warnings once per process (estimate reads
    [estimate] twice; instalments reads both tables)."""
    for msg in _config_table_warnings(_soft_config(root)):
        if msg in _CONFIG_WARNED:
            continue
        _CONFIG_WARNED.add(msg)
        _pfx = f"taxjson {_CURRENT_CMD}" if _CURRENT_CMD else "taxjson"
        print(f"{_pfx}: warning: taxjson.toml: {msg}", file=sys.stderr)


def _nonneg_money(value: Any, what: str) -> float:
    """A config money figure: a finite, non-negative number (a TOML
    boolean is refused — float(True) is 1.0). Dies naming `what`."""
    import math as _math
    # A TOML number only: float() also read the string "5_000" as 5000
    # and "nan" as nan (S043-05).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _die(f"{what} must be a number, got {value!r}")
    try:
        f = float(value)
    except (TypeError, ValueError):
        _die(f"{what} must be a number, got {value!r}")
    if not _math.isfinite(f) or f < 0:
        _die(f"{what} must be a non-negative finite number (enter it as "
             f"a positive amount), got {value!r}")
    return f


def _refuse_bad_account_types(cfg: Dict[str, Any]) -> None:
    """Die on an [accounts.*] entry whose `type` is missing or not
    exactly taxable/sheltered. Applied by EVERY config reader
    (load_config, _soft_config), not only `run`'s validate_config: the
    filing commands partition on an exact string match, so a type
    edited after the run ("Taxable") silently dropped the account from
    estimate, sum, form-export and the close-year lock (R1-268). Silent
    on a valid config."""
    from taxjson.lib.config_check import account_type_problems
    problems = account_type_problems(cfg)
    if problems:
        _die(problems[0] if len(problems) == 1
             else "\n  ".join(["invalid account types:"] + problems))


# The command currently executing (set by main's dispatch loop) so
# shared helpers' errors can say WHICH command in a chain failed.
_CURRENT_CMD = ""


class _CappedHelpFormatter(argparse.HelpFormatter):
    """Help wrapped at a readable width. argparse wraps to the FULL
    terminal width, so on a wide monitor the option descriptions
    sprawl into hard-to-scan lines; classic ~78 columns reads better
    and still shrinks with genuinely narrow terminals."""

    def __init__(self, prog, **kw):
        import shutil
        kw.setdefault("width",
                      min(shutil.get_terminal_size().columns - 2, 78))
        super().__init__(prog, **kw)


def _die(msg: str) -> None:
    prefix = f"taxjson {_CURRENT_CMD}: " if _CURRENT_CMD else "taxjson: "
    sys.exit(prefix + msg)


_ESTIMATE_KEYS = ("other_income", "other_losses", "deductions",
                  "carrying_charges")


def _estimate_deductions(root: Path, args) -> Tuple[float, float]:
    """(deductions, carrying_charges) for the Canada estimate: CLI
    flags win, else the [estimate] table, else zero. `deductions` are
    lines 20700-23500 the AMT allows in full (RRSP 20800, FHSA, RPP);
    `carrying_charges` is line 22100 (50% under the post-2024 AMT).
    other_income stays >= 0: deductions are their own input, not a
    negative income (R1-213)."""
    import math as _math
    _warn_config_tables(root)
    cfg = _soft_config(root).get("estimate") or {}
    out = []
    for key in ("deductions", "carrying_charges"):
        v = getattr(args, key, None)
        src = f"--{key.replace('_', '-')}"
        if v is None:
            v, src = cfg.get(key), f"[estimate] {key}"
        if isinstance(v, bool):
            _die(f"{src} must be a number, got {v!r}")
        try:
            f = float(v or 0.0)
        except (TypeError, ValueError):
            _die(f"{src} must be a number, got {v!r}")
        if not _math.isfinite(f) or f < 0:
            _die(f"{src} must be a non-negative finite number (the "
                 f"amount you deduct, as a positive figure), got {v!r}")
        out.append(f)
    return out[0], out[1]


def _estimate_inputs(root: Path, args) -> Tuple[float, float]:
    """(other_income, other_losses) for the tax estimate: CLI flags
    win, else the [estimate] table, else zero. Shared by cmd_summary
    and cmd_instalments — `taxjson instalments` shells out to the
    estimate, and without a common resolution the two commands
    reported different net tax owing for anyone with employment
    income (5.5x on the auditor's fixture)."""
    _warn_config_tables(root)
    cfg = _soft_config(root).get("estimate") or {}
    oi = getattr(args, "other_income", None)
    ol = getattr(args, "other_losses", None)
    if oi is None:
        oi = cfg.get("other_income")
    if ol is None:
        ol = cfg.get("other_losses")
    # float(True) is 1.0: `other_income = true` was read as $1 (R1-217).
    for _k, _v in (("other_income", oi), ("other_losses", ol)):
        if isinstance(_v, bool):
            _die(f"[estimate] {_k} must be a number, got {_v!r}")
    try:
        oi_f, ol_f = float(oi or 0.0), float(ol or 0.0)
    except (TypeError, ValueError):
        _die("[estimate] other_income/other_losses must be numbers")
    import math as _math
    # Same guard the CLI flags get: a NEGATIVE other_losses (the
    # "-10,000 carryover" sign trap) fabricates taxable gains, and
    # nan/inf poisons every downstream figure — the config path
    # skipped both checks (2026-09 audit).
    if not (_math.isfinite(oi_f) and _math.isfinite(ol_f)) \
            or oi_f < 0 or ol_f < 0:
        _die("other_income/other_losses must each be a non-negative "
             "finite number (enter loss carryovers as positive "
             "amounts) — check the flags and the [estimate] block in "
             "taxjson.toml")
    return oi_f, ol_f


# Every known [settings] key, and the country that owns it, lives in
# lib/country.SETTING_COUNTRY (one table for the config check, the
# commands and scripts/check_tax_rules.py).
from taxjson.lib.country import SETTING_COUNTRY as _SETTING_COUNTRY  # noqa: E402
_SETTINGS_KEYS = tuple(_SETTING_COUNTRY)
_ACCOUNT_KEYS = ("type", "crypto", "transfers", "plan",
                 "brokerage", "account", "query_id", "holdings")
_ACCOUNT_TYPES = ("taxable", "sheltered")


def validate_config(cfg: Dict[str, Any],
                    inputs_dir: Optional[Path] = None) -> List[str]:
    """Schema-check taxjson.toml BEFORE any stage runs. Fatal problems
    sys.exit (they would otherwise surface minutes into a run, or worse,
    not at all — a typo'd account `type` matches neither partition and the
    account silently vanishes from the run while stale reports keep
    looking healthy). Unknown keys and populated-but-unconfigured input
    dirs come back as warnings for the caller to print."""
    import difflib

    def _suggest(key: str, valid: Tuple[str, ...]) -> str:
        close = difflib.get_close_matches(key, valid, n=1)
        return f" (did you mean {close[0]!r}?)" if close else ""

    warnings: List[str] = []
    settings = cfg.get("settings", {})
    for key in settings:
        if key not in _SETTINGS_KEYS:
            warnings.append(f"unknown [settings] key {key!r} is ignored"
                            f"{_suggest(key, _SETTINGS_KEYS)}")

    year = settings.get("year")
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
    settings = cfg["settings"]
    # tax_date against the country's practice: allowed (both countries
    # own the key) but said, once per run (partition INPUTS-05).
    from taxjson.lib.country import default_tax_date as _dtd
    _td, _c = settings.get("tax_date"), settings["country"]
    if _td is not None and _td != _dtd(_c):
        warnings.append(
            f"[settings] tax_date = \"{_td}\" in a {_c} project: "
            + ("the IRS dates a disposition by its TRADE date (the US "
               "default)" if _c == "usa" else
               "CRA practice dates a disposition by its SETTLEMENT date "
               "(the Canadian default)")
            + " — keep it only if you mean to depart from that")
    _opt = settings.get("option_premium_timing")
    if _opt is not None and str(_opt).strip().lower() not in ("grant", "close"):
        _die(f"[settings] option_premium_timing must be \"grant\" or "
             f"\"close\" (got {_opt!r}).")
    _since = settings.get("option_grant_timing_since")
    # 1900 like [settings] year and `init --year`, which writes
    # since = year: init --year 1989 produced a config the next run
    # refused (S047-20).
    if _since is not None and not (isinstance(_since, int)
                                   and not isinstance(_since, bool)
                                   and 1900 <= _since <= 2100):
        _die(f"[settings] option_grant_timing_since must be a tax year "
             f"(got {_since!r}).")
    _bb = settings.get("option_buyback_loss_superficial")
    if _bb is not None and not isinstance(_bb, bool):
        _die(f"[settings] option_buyback_loss_superficial must be true/false (got {_bb!r}).")
    _froc = settings.get("foreign_return_of_capital")
    if _froc is not None and _froc not in ("dividend", "acb"):
        _die(f"[settings] foreign_return_of_capital must be \"dividend\" or \"acb\" (got {_froc!r}).")
    _pyr = settings.get("prior_year_record")
    if _pyr is not None and not isinstance(_pyr, str):
        _die(f"[settings] prior_year_record must be a path string "
             f"(got {_pyr!r}).")
    _fs = settings.get("futures_settle")
    if _fs is not None and _fs not in ("trade", "next_day"):
        _die(f"[settings] futures_settle must be \"trade\" or \"next_day\" (got {_fs!r}).")
    try:
        _income_rules(settings)
    except ValueError as e:
        _die(str(e))
    if "cross_asset" in settings:
        warnings.append(
            "[settings] cross_asset is retired and ignored: a long call "
            "on the same shares is always replacement property for a "
            "share loss (s.54 'a right to acquire'); shares never replace "
            "an option, and only the identical contract replaces an "
            "option. Delete the line.")
    src = settings.get("source_currencies")
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
        if not isinstance(acfg, dict):
            _die(f"[accounts.{name}] must be a table")
        for key in acfg:
            if key not in _ACCOUNT_KEYS:
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
            warnings.append(
                f"[accounts.{name}] plan {_plan!r} is not a known plan "
                f"kind ({' | '.join(_PLAN_KINDS)}) and is ignored"
                f"{_suggest(str(_plan).strip().lower(), _PLAN_KINDS)}")
        brok = acfg.get("brokerage")
        if brok is not None and str(brok) not in ("questrade",
                                                  "ibkr_flex"):
            warnings.append(
                f"[accounts.{name}] brokerage {brok!r} is not a fetch "
                f"source (questrade | ibkr_flex) — `taxjson fetch` "
                f"will refuse it"
                f"{_suggest(str(brok), ('questrade', 'ibkr_flex'))}")

    # CSVs in a SUBFOLDER of an account's inputs are never read (only
    # files directly in inputs/<account>/ are) — say so instead of
    # silently dropping, e.g., inputs/margin/2025/*.csv.
    if inputs_dir is not None and inputs_dir.is_dir():
        for name in accounts:
            adir = inputs_dir / str(name)
            if not adir.is_dir():
                continue
            for sub in sorted(adir.iterdir()):
                if not sub.is_dir() or sub.name.startswith("."):
                    continue
                # Spreadsheets too: an .xlsx here got no word at all
                # (S043-13).
                n_csv = sum(1 for f in sub.rglob("*")
                            if f.is_file() and f.suffix.lower()
                            in (".csv", ".tt") + SPREADSHEET_SUFFIXES)
                if n_csv:
                    warnings.append(
                        f"inputs/{name}/{sub.name}/ holds {n_csv} "
                        f"CSV/.tt/spreadsheet file(s) that are NOT read "
                        f"— only CSV and .tt files "
                        f"directly in inputs/{name}/ are processed; move "
                        f"them up a level (or out of inputs/ if they are "
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
                rel = f"inputs/{name}/{sheet.name}"
                if sheet.stem.lower() in csv_stems:
                    warnings.append(
                        f"{rel} is not read (spreadsheets never are); its "
                        f"CSV conversion {sheet.stem}.csv is. Move the "
                        f"spreadsheet out of inputs/ to silence this.")
                else:
                    unconverted.append(rel)
        if unconverted:
            _die("spreadsheet export(s) in inputs/ are NOT read — only "
                 ".csv and .tt files are, so every trade in them would be "
                 "missing from the books:\n    "
                 + "\n    ".join(unconverted)
                 + "\n  Convert each to CSV next to it (`taxjson-xlsx-to-csv "
                 "FILE.xlsx -o FILE.csv`, or the broker's CSV download) "
                 "and move the spreadsheet out of inputs/.")
    # Inputs dir with data but no [accounts.*] entry: today that folder is
    # silently ignored — the inverse of the configured-but-unpopulated
    # warning the run loop already prints.
    if inputs_dir is not None and inputs_dir.is_dir():
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

_FILENAME_HINTS: Tuple[Tuple[str, str], ...] = (
    ("coinbase", "coinbase"),
    ("cb_", "coinbase"),
    ("kraken", "kraken"),
    ("kr_", "kraken"),
    # The column-mapped escape hatch for unsupported brokers: any
    # `generic_*.csv` plus its TOML mapping sidecar.
    ("generic_", "generic"),
)


# Brokers with corp-action extractors (taxjson-corp-actions). Others
# (webull, coinbase, kraken) have no extractor and skip that stage.
CORP_ACTION_BROKERS = {"ib", "interactive_brokers", "questrade", "qt",
                       "rbc", "rbc_direct"}

# The config advertises `country = "canada | ca | usa | us"` and the gains
# engine accepts all four, but taxjson-corp-actions only knows the canonical
# names. Normalize once so an alias like "ca" doesn't abort the corp-actions
# stage at argparse (which restricts --country to RULES_BY_COUNTRY's keys).
# The one resolver lives in lib/country.py (partition audit R1): an
# unknown or missing country is refused everywhere with one message.
from taxjson.lib.country import command_country_problem  # noqa: E402


_US_EXPERIMENTAL_NOTE = (
    "NOTE: the US engine is EXPERIMENTAL — its rules are implemented and "
    "unit-tested but have not been validated against a real account. "
    "Treat the output as a draft, and consider contributing a redacted "
    "export (`taxjson redact`) so it can be.")


def _normalize_country(c: str) -> str:
    """Canonical canada|usa (lib/country.canonical_country); dies with
    the resolver's message on anything else."""
    from taxjson.lib.country import CountryError, canonical_country
    try:
        return canonical_country(c)
    except CountryError as e:
        _die(str(e))


def _country(settings: Optional[Dict[str, Any]]) -> str:
    """The project's canonical country from a [settings] table. A
    missing or unknown value dies with the one resolver message — never
    a silent Canada (partition audit R1: every reader but `run` used to
    default to it)."""
    from taxjson.lib.country import CountryError, settings_country
    try:
        return settings_country(settings)
    except CountryError as e:
        _die(f"{e}{'' if (settings or {}).get('country') else ' in taxjson.toml'}")


def _home_currency(settings: Dict[str, Any]) -> str:
    """The currency the project's country files in (CAD / USD)."""
    from taxjson.lib.country import home_currency
    return home_currency(_country(settings))


def _base(settings: Dict[str, Any]) -> str:
    """[settings] base_currency, else the country's currency. The
    config readers refuse a base that is not the country's."""
    b = settings.get("base_currency")
    return str(b).strip().upper() if b else _home_currency(settings)


def _tax_date(settings: Dict[str, Any]) -> str:
    """The date basis in force: [settings] tax_date, else the country
    default (lib/country.settings_tax_date)."""
    from taxjson.lib.country import CountryError, resolve_tax_date
    try:
        return resolve_tax_date(_country(settings), settings.get("tax_date"))
    except CountryError as e:
        _die(str(e))


def _country_has_corp_rules(country: str) -> bool:
    """Whether taxjson-corp-actions has election rules for this
    jurisdiction (both countries do: RULES_BY_COUNTRY)."""
    from taxjson.lib.corp_actions import RULES_BY_COUNTRY
    return _normalize_country(country) in RULES_BY_COUNTRY


def _warn_zero_value_spinoffs(name: str, is_taxable: bool,
                              corp_files: List[Path], cache: Path) -> None:
    """A taxable spin-off booked at $0 (the documented `fmv_per_share=0`
    "defer") books no dividend income and a $0 cost for the new shares:
    a later sale overstates the gain by the same amount. It stays loud
    on EVERY run — on the console and, through a `.diag` sidecar, in the
    account's .sum — until a value is set (2026-09 audit: it was silent
    after the prompt). Registered accounts: no tax effect, no warning."""
    import json as _json
    from taxjson.lib.corp_actions import (ALLOCATED_BASIS_HINT,
                                          zero_basis_rollover_rows,
                                          zero_value_merger_rows,
                                          zero_value_spinoff_rows)
    diag = cache / f"{name}_corp_spinoff_value.diag"
    lines: List[str] = []
    if is_taxable:
        for f in corp_files:
            try:
                rows = _json.loads(f.read_text(encoding="utf-8")).get(
                    "transactions", [])
            except (OSError, ValueError):
                continue
            for r in zero_value_spinoff_rows(rows):
                eid = r.get("corp_event_id", "?")
                lines.append(
                    f"warning: {name}: spin-off {r.get('symbol')} on "
                    f"{r.get('date')} (event {eid}) is booked at $0 — no "
                    f"dividend income and a $0 cost for the new shares, "
                    f"so a later sale overstates the gain by the same "
                    f"amount. Set its value: taxjson elect {name} --set "
                    f"{eid}={r.get('corp_election')} --hint "
                    f"fmv_per_share=<value>")
            # A taxable merger at $0 (the "0 to defer" FMV, with or
            # without cash-in-lieu): a fake loss on the old shares and a
            # $0 cost for the new ones (audits S020-06, S074-00).
            for r in zero_value_merger_rows(rows):
                eid = r.get("corp_event_id", "?")
                lines.append(
                    f"warning: {name}: merger into {r.get('symbol')} on "
                    f"{r.get('date')} (event {eid}) is booked at $0 — the "
                    f"old shares' proceeds are only any cash-in-lieu (a "
                    f"fake loss) and the new shares cost $0. Set its "
                    f"value: taxjson elect {name} --set "
                    f"{eid}={r.get('corp_election')} --hint "
                    f"fmv_per_share=<value>")
            # An s.86.1 / §355 rollover allocated $0: the parent keeps
            # its whole cost, the spin-off's sale books the gain
            # (audits S073-21, S074-04).
            for r in zero_basis_rollover_rows(rows):
                eid = r.get("corp_event_id", "?")
                el = r.get("corp_election")
                lines.append(
                    f"warning: {name}: spin-off {r.get('symbol')} on "
                    f"{r.get('date')} (event {eid}, {el}) is booked with "
                    f"$0 allocated cost — the parent keeps all of it and "
                    f"the spin-off's sale books the gain. Set it: taxjson "
                    f"elect {name} --set {eid}={el} --hint "
                    f"{ALLOCATED_BASIS_HINT[el]}=<amount>")
    if lines:
        diag.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for ln in lines:
            print(f"  ! {ln}", file=sys.stderr)
    else:
        diag.unlink(missing_ok=True)


def _warn_expired_open_options(name: str, gains_json: Path, cache: Path,
                               year: Any) -> None:
    """An option still open in the books after its expiry date: the
    export dropped the expiry / assignment / exercise row. For a LONG
    contract the premium paid is a capital loss of the expiry year that
    the books never realize (R1-37); option-boundary covers written
    contracts only. Loud on every run and, through a `.diag` sidecar,
    in the account's .sum. Cutoff: the earlier of the project's year end
    and today — a contract expiring later is simply open."""
    import json as _json
    from datetime import date as _date
    from taxjson.lib.core import is_option_symbol, parse_option_expiry
    diag = cache / f"{name}_expired_options.diag"
    cutoff = min(f"{year}-12-31", _date.today().isoformat())
    lines: List[str] = []
    try:
        inv = _json.loads(gains_json.read_text(encoding="utf-8")).get(
            "inventory") or []
    except (OSError, ValueError, AttributeError):
        inv = []
    for h in inv:
        sym = str(h.get("symbol") or "")
        try:
            qty = float(h.get("qty") or 0.0)
        except (TypeError, ValueError):
            continue
        if abs(qty) < 1e-9 or not is_option_symbol(sym):
            continue
        exp = parse_option_expiry(sym)
        if not exp or exp >= cutoff:
            continue
        side = "long" if qty > 0 else "written"
        lines.append(
            f"warning: {name}: {sym} expired {exp} but the books still "
            f"hold {qty:g} ({side}) — the export is missing its expiry, "
            f"assignment or exercise row"
            + (f"; the {abs(float(h.get('total_cost') or 0.0)):,.2f} paid "
               f"is a loss of {exp[:4]} that is not booked"
               if qty > 0 else "")
            + ". Add the missing row (an expiry is a BUYSELL closing "
              "the position at 0 on the expiry date) and re-run.")
    if lines:
        diag.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for ln in lines:
            print(f"  ! {ln}", file=sys.stderr)
    else:
        diag.unlink(missing_ok=True)


def detect_broker(csv_path: Path) -> Optional[str]:
    """Which parser reads this CSV. In order:

    1. a documented name PREFIX — generic_, cb_, kr_ — is the user's
       explicit routing and always wins;
    2. a positive content match on an equity export's structure (IB's
       Statement header, Questrade's columns, Webull's Action Code)
       wins over a word in the name;
    3. the venue WORDS coinbase / kraken in the name (their CSV shapes
       aren't distinctive enough for content detection, and a Kraken
       ledger looks superficially like an RBC activity export);
    4. whatever content detection found (rbc_direct), else None.

    The word hints used to be checked first, so generic_kraken_export.csv
    and kr_trades_moved_from_coinbase.csv went to the wrong crypto
    parser (0 rows, exit 0) and an IB export named after Kraken Robotics
    was refused as crypto data (R1-127, S044-01, S044-02)."""
    lower = csv_path.name.lower()
    for hint, broker in _FILENAME_HINTS:
        # Underscore-style hints match only at the START of the name:
        # a substring match routed ibkr_statement.csv (contains "kr_")
        # to the Kraken parser (REVIEW-2026-07-ui #3).
        if hint.endswith("_") and lower.startswith(hint):
            return broker
    from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
    by_content = detect_brokerage(csv_path)
    if by_content in _STRUCTURAL_BROKERS:
        return by_content
    for hint, broker in _FILENAME_HINTS:
        if not hint.endswith("_") and hint in lower:
            return broker
    return by_content


# Content matches strong enough to beat a venue word in the file name
# (structural markers a crypto export never carries).
_STRUCTURAL_BROKERS = ("ib", "questrade", "webull")


def input_files(dirpath: Path, suffix: str) -> List[Path]:
    """Input files by suffix, CASE-INSENSITIVE. Excel and several
    brokers export TRADES.CSV / START.TT; glob("*.csv") silently
    ignored them while the GUI import accepted them — a run that
    exits 0 with those trades missing (REVIEW-2026-07-ui #1)."""
    if not dirpath.is_dir():
        return []
    return sorted(p for p in dirpath.iterdir()
                  if p.is_file() and p.suffix.lower() == suffix)


# Spreadsheet suffixes a broker export may arrive in. None is read by
# the run; validate_config refuses them unless converted (R1-64).
SPREADSHEET_SUFFIXES = (".xlsx", ".xls", ".xlsm", ".ods")


def spreadsheet_inputs(dirpath: Path) -> List[Path]:
    """Spreadsheet files directly in an inputs folder (never read)."""
    if not dirpath.is_dir():
        return []
    return sorted(p for p in dirpath.iterdir()
                  if p.is_file() and not p.name.startswith((".", "~$"))
                  and p.suffix.lower() in SPREADSHEET_SUFFIXES)


def group_inputs(account_dir: Path) -> Dict[str, List[Path]]:
    out: Dict[str, List[Path]] = {}
    for csv in input_files(account_dir, ".csv"):
        broker = detect_broker(csv)
        if not broker:
            # Content detection cannot read a cp1252 re-save: the
            # rename advice below pointed at the wrong fix (S024-03).
            try:
                _raw = csv.read_bytes()
                if _raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                    _raw.decode("utf-8-sig")
            except UnicodeDecodeError as e:
                from taxjson.lib.cli_diag import not_utf8
                _die(str(not_utf8(csv, e)))
            except OSError:
                pass
            _die(f"cannot detect broker for {csv}. "
                     f"Rename to start with one of: cb_, kr_ (for crypto), "
                     f"generic_ (any other broker, with a TOML column "
                     f"mapping — see examples/generic_wealthsimple.toml), "
                     f"or check that the CSV header matches a supported "
                     f"broker.")
        out.setdefault(broker, []).append(csv)
    return out


def _raw_mixed_currency_symbols(raw_json: Path) -> List[str]:
    """Symbols whose NATIVE-currency pool would mix currencies —
    following SPLIT renames. A cross-currency rollover (e.g. SSL.TO
    [CAD] --s.85.1(5)--> RGLD.US [USD]) renames the CAD pool onto a
    symbol whose later trades are USD; the raw (unconverted) gains
    stage then dies with a currency-mismatch error and killed the
    whole run. Such positions are unrepresentable in the native view
    BY CONSTRUCTION — the caller skips the raw stage with a warning
    instead (the converted, tax-authoritative books are unaffected)."""
    import json as _json
    try:
        txs = _json.loads(raw_json.read_text(
            encoding="utf-8")).get("transactions", [])
    except (OSError, ValueError):
        return []
    renames: Dict[str, str] = {}
    for t in txs:
        if (t.get("action") == "SPLIT" and t.get("symbol_new")
                and t["symbol_new"] != t.get("symbol")):
            renames[t["symbol"]] = t["symbol_new"]

    def _final(sym: str) -> str:
        seen = set()
        while sym in renames and sym not in seen:
            seen.add(sym)
            sym = renames[sym]
        return sym

    curs: Dict[str, set] = {}
    for t in txs:
        # SPLIT rows are not counted: they carry no money, and a .tt
        # SPLIT is stamped CAD whatever the listing — a USD stock's
        # split then looked like a mixed-currency pool and the holdings
        # refresh was skipped with a misleading "rollover rename"
        # message (R1-126). A rename's currency mix still shows through
        # the trades on either side of it (followed via `renames`).
        if t.get("action") not in ("BUYSELL", "ASSIGN", "TRANSFER"):
            continue
        c, sym = t.get("currency"), t.get("symbol")
        if c and sym:
            curs.setdefault(_final(sym), set()).add(c)
    return sorted(sym for sym, cs in curs.items() if len(cs) > 1)


# ---------------------------------------------------------------- stages

def _rates_coverage_stale(rates_path: Path, today: Optional[date_cls] = None,
                          currencies: Optional[List[str]] = None) -> bool:
    """Whether to_base.csv's DATA has aged out. Freshness is a property of
    the data, not the file's mtime: coverage ends at the generation date,
    so on a stable install (no package/config mtime bumps to invalidate
    the cache) the file would otherwise be reused forever and every trade
    after its last row would silently convert at the --default-rate.
    Stale when the newest rate row is more than 3 days old (tolerates
    weekends/short holidays without refetching daily) — judged PER
    CURRENCY when `currencies` is given: the file concatenates one block
    per source currency, and a USD block cut short by a failed download
    hid behind a fresh AUD last line (S046-06)."""
    today = today or date_cls.today()
    newest: Dict[str, date_cls] = {}
    try:
        with rates_path.open("r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                d = datetime.strptime(parts[0], "%Y-%m-%d").date()
                cur = parts[2].upper() if len(parts) > 2 else ""
                for key in (cur, "*"):
                    if key not in newest or d > newest[key]:
                        newest[key] = d
    except (OSError, ValueError, IndexError):
        return True                          # unreadable/malformed: refetch
    if "*" not in newest:
        return True                          # sources configured, no rows
    keys = [c.upper() for c in currencies] if currencies else ["*"]
    return any(k not in newest or (today - newest[k]).days > 3
               for k in keys)


def stage_currency_rates(settings: Dict[str, Any], cache: Path) -> Path:
    """Build to_base.csv by appending taxjson-to-base-curr output for each
    configured source currency. Caches across runs while every source
    currency's rates reach the last few days: a refresh whose download
    failed for one currency (its block ends early) is refetched on the
    next run instead of being served for days behind another currency's
    fresh last line (S046-06)."""
    base = settings["base_currency"]
    sources = [c for c in settings.get("source_currencies", ["USD"]) if c != base]
    # Ensure the cache dir exists before any write — applies to both
    # the no-sources branch (writes an empty file as a marker) and the
    # fetch branch below. Without this, a fresh CAD-only setup with
    # `source_currencies = []` would crash on the empty-write because
    # `work/` didn't exist yet.
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    rates_path = cache / "to_base.csv"
    if not sources:
        # Only touch the marker when its content is wrong: an
        # unconditional write bumped the mtime every run, and since
        # to_base.csv is a merge2/convert dep, --fast re-merged every
        # account on every invocation for offline (no-source-currency)
        # configs — the cache never actually cached.
        if not rates_path.exists() or rates_path.stat().st_size:
            rates_path.write_text("")
        return rates_path
    # `needs_rebuild` here picks up the implicit taxjson.toml dependency
    # (via _CONFIG_PATH), so a change to base_currency or
    # source_currencies invalidates the cached rates file. Without
    # this, switching from CAD-base to USD-base silently reused the
    # old rates. The coverage check catches the inverse failure: an
    # mtime-fresh file whose data ends in the past.
    if (not needs_rebuild(rates_path)
            and not _rates_coverage_stale(rates_path, currencies=sources)):
        return rates_path
    had_previous = rates_path.exists() and rates_path.stat().st_size > 0
    parts: List[bytes] = []
    try:
        for src in sources:
            print(f"  fetching {src} → {base} rates")
            parts.append(run_capture(_cmd("taxjson-to-base-curr") + [src, base]))
    except Exception as exc:
        # A refresh attempt (e.g. offline, yfinance hiccup) must not turn a
        # usable-if-aging rates file into a hard failure — but say so
        # loudly: rows past the file's coverage convert at --default-rate.
        if had_previous:
            print(f"taxjson: warning: FX rate refresh failed ({exc}); keeping the "
                  f"existing to_base.csv. Transactions dated after its "
                  f"coverage will fall back to the default rate — re-run "
                  f"online to refresh.", file=sys.stderr)
            return rates_path
        raise
    # Atomic: to_base.csv is mtime-cached, so a kill mid-write must never
    # leave a fresh-looking empty/partial rates file (every later run would
    # silently fall back to the default FX rate).
    rates_tmp = rates_path.with_name(rates_path.name + ".part")
    try:
        rates_tmp.write_bytes(b"".join(parts))
        rates_tmp.replace(rates_path)
    finally:
        rates_tmp.unlink(missing_ok=True)
    return rates_path


def _resolve_manifest(acct_dir: Path, cache: Path, name: str,
                      create: bool = False) -> Path:
    """The corp-action elections manifest for an account. Canonical home is
    `inputs/<account>/manifest.json` — elections and hand-typed FMV/ACB
    hints are user DECISIONS, the one thing that is NOT rebuildable from
    inputs, so they must not live in the disposable (gitignored,
    documented-as-safe-to-delete) work/ cache. A legacy
    `work/<account>_manifest.json` is migrated on first touch; with
    `create=True` a fresh empty manifest is written when neither exists."""
    user_manifest = acct_dir / "manifest.json"
    legacy = cache / f"{name}_manifest.json"
    if user_manifest.exists():
        return user_manifest
    if legacy.exists():
        acct_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        user_manifest.write_text(legacy.read_text(encoding="utf-8"),
                                 encoding="utf-8")
        print(f"  migrated elections manifest → {user_manifest} "
              f"(version-control this file; the work/ copy is no longer "
              f"read once this exists)")
        return user_manifest
    if create:
        acct_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        user_manifest.write_text('{"elections": {}}\n')
        return user_manifest
    # Read-only callers (taxjson elect) on a project with no manifest yet:
    # report the canonical path; Manifest.load treats missing as empty.
    return user_manifest


def _wash_flags(is_taxable: bool, is_crypto: bool, country: str) -> List[str]:
    """Wash-detection flags for an account's gains run. US crypto gets
    --no-wash: the IRS treats digital assets as property, not "securities",
    so §1091 does not apply — running it denied legitimate crypto losses.
    Canada's superficial-loss rule covers any identical property, crypto
    included, so crypto stays wash-checked there."""
    if not is_taxable:
        return []
    flags = ["--taxable"]
    if is_crypto and country == "usa":
        flags.append("--no-wash")
    return flags


class PendingElectionsError(RuntimeError):
    """An account's corp-action events need elections and prompting
    isn't possible (--no-input or no TTY). Carries the pending-JSON
    path so the orchestrator can aggregate and exit 3 instead of
    crashing."""

    def __init__(self, account: str, pending_path: Path):
        super().__init__(f"{account}: elections required")
        self.account = account
        self.pending_path = pending_path


_SKIPPED_ACCOUNTS_FILE = "skipped_accounts.json"


def _record_skipped_accounts(cache: Path, names: List[str], *,
                             only: Optional[str] = None) -> None:
    """Persist which accounts `taxjson run` skipped for having no inputs
    (work/skipped_accounts.json), so later commands don't tell the user
    to "run `taxjson run` first" about an account that run deliberately
    skipped. A single-account run (`only`) updates just that entry."""
    import json as _json
    path = cache / _SKIPPED_ACCOUNTS_FILE
    keep = set()
    if only:
        try:
            keep = set(_json.loads(path.read_text(encoding="utf-8"))
                       .get("accounts") or []) - {only}
        except (OSError, ValueError, AttributeError):
            keep = set()
    names_all = sorted(keep | set(names))
    try:
        if names_all:
            cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_text(_json.dumps({"schema_version": 1,
                                         "accounts": names_all},
                                        indent=2) + "\n",
                            encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass                          # advisory only — never break a run


def _accounts_skipped_for_no_inputs(root: Path) -> set:
    """Accounts the last `taxjson run` skipped because inputs/<name>/
    held no CSV or .tt file — and that STILL hold none (a user who has
    since added files does need the "run `taxjson run` first" hint).
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
        d = root / "inputs" / n
        if not (input_files(d, ".csv") or input_files(d, ".tt")):
            out.add(n)
    return out


def _sheltered_expected(root: Path) -> bool:
    """Should work/sheltered_base.json exist? True when the config has
    a sheltered account that `taxjson run` did not skip for having no
    inputs — the "no sheltered_base.json" notes are noise otherwise."""
    _acfg = _soft_config(root).get("accounts") or {}
    skipped = _accounts_skipped_for_no_inputs(root)
    return any((c or {}).get("type") == "sheltered" and n not in skipped
               for n, c in _acfg.items())


def _crypto_broker_files(root: Path, cfg: Dict[str, Any]
                         ) -> Dict[str, List[Tuple[str, Path]]]:
    """{crypto account: [(broker, csv)]} — the raw exchange exports the
    stablecoin pool of `taxjson crypto-sends` is rebuilt from."""
    from taxjson.lib.crypto_sends import crypto_accounts
    out: Dict[str, List[Tuple[str, Path]]] = {}
    for acct in crypto_accounts(cfg):
        d = root / "inputs" / acct
        if d.is_dir():
            out[acct] = [(b, p) for b, ps in group_inputs(d).items()
                         for p in ps]
    return out


def _is_us(cfg: Dict[str, Any]) -> bool:
    return _country((cfg.get("settings") or {})) in ("us",
                                                                 "usa")


def _crypto_sends_tt(root: Path, acct: str, report: Dict[str, Any]
                     ) -> Tuple[str, List[Dict[str, Any]]]:
    """(Re)write inputs/<acct>/crypto_sends.tt from the report. Returns
    (write status, duplicate hand-written lines). Raises ValueError when
    a gift/payment cannot be priced (nothing is written then), and
    crypto_sends.RefusedDecision AFTER writing the file without them
    when a saved decision is one the country refuses (a US gift:
    partition SPEC-01/INPUTS-04)."""
    from taxjson.lib import crypto_sends as CS
    adoc = report["accounts"][acct]
    entries, unpriced = CS.tt_entries(adoc)
    refused = CS.refused_entries(adoc)
    if unpriced:
        _fee_only = all(e.get("network_fee") for e in unpriced)
        raise ValueError(
            "no fair value for " + ", ".join(e["id"] for e in unpriced)
            + " (the price lookup failed or TAXJSON_OFFLINE is set and "
              "the price is not cached) — re-run online"
            + ("" if _fee_only else
               f", or give the value per coin in "
               f"{report['base_currency']}: `taxjson crypto-sends {acct} "
               f"--set ID=gift|payment --price P`")
            + (" (a `-fee` id is the network fee hidden in a send that "
               "arrived short: priced from the send row or the Yahoo "
               "close)" if any(e.get("network_fee") for e in unpriced)
               else "")
            + ". crypto_sends.tt was not changed.")
    status = CS.write_tt(Path(adoc["tt_file"]),
                         CS.render_tt(acct, entries, report["country"]))
    if refused:
        raise CS.RefusedDecision(
            f"inputs/{acct}/{CS.MANIFEST_NAME}: "
            + "; ".join(e["refused"] for e in refused)
            + f" (inputs/{acct}/{CS.TT_NAME} {status} without it)")
    return status, CS.duplicate_lines(root / "inputs" / acct, entries)


def _dup_warning(acct: str, dups: List[Dict[str, Any]]) -> List[str]:
    """One line per hand-written .tt line that books a send the
    generated crypto_sends.tt also books — both file names, the send
    and its timestamp. Nothing is deleted: the owner picks the line."""
    from taxjson.lib import crypto_sends as CS
    out = []
    for d in dups:
        when = (d["timestamp"] if d["same_time"]
                else f"{d['timestamp'][:10]} (the hand-written line has "
                     f"another time)")
        if d["id"].endswith("-fee"):
            out.append(
                f"{d['file']} line {d['line']} and inputs/{acct}/"
                f"{CS.TT_NAME} both sell {CS.fmt_qty(d['quantity'])} "
                f"{d['symbol']} on {when} (the network fee {d['id']}, "
                f"booked from the send that arrived short) — that "
                f"disposition is counted twice. Delete the hand-written "
                f"line.")
            continue
        out.append(
            f"{d['file']} line {d['line']} and inputs/{acct}/{CS.TT_NAME} "
            f"both sell {CS.fmt_qty(d['quantity'])} {d['symbol']} on "
            f"{when} (send {d['id']}) — that disposition is counted "
            f"twice. Delete the hand-written line (crypto_sends.tt is "
            f"generated from sends.json), or, to keep it, record the send "
            f"as `self`: `taxjson crypto-sends {acct} --set "
            f"{d['id']}=self`.")
    return out


def _stage_crypto_sends(root: Path, name: str, interactive: bool) -> None:
    """`taxjson run` hook for a crypto account, right after the parse:
    prompt for undecided sends at a TTY (else one note line), then
    refresh the generated crypto_sends.tt from the saved decisions."""
    from taxjson.lib import crypto_sends as CS
    cfg = _soft_config(root)
    cache = root / "work"
    # Another crypto account not parsed yet (first run of a
    # multi-account project): its arrivals are unknown, so a send to it
    # would look like a gift. Don't ask until its sidecar exists.
    files = _crypto_broker_files(root, cfg)
    unparsed = [a for a, fs in files.items() if a != name and any(
        not (cache / f"{a}_{b}_transfers.json").exists()
        for b, _p in fs if b in ("kraken", "coinbase"))]
    try:
        report = CS.build_report(root, cfg, files, CS.yahoo_usd_price(root),
                                 want=name)
        adoc = report["accounts"].get(name)
        if adoc is None:
            return
        if adoc["undecided"] and interactive and not unparsed:
            if CS.prompt_undecided(adoc["sends"], Path(adoc["manifest"]),
                                   allow_gift=command_country_problem(
                                       "crypto-sends",
                                       _country(cfg.get("settings")),
                                       "gift") is None):
                report = CS.build_report(root, cfg, files,
                                         CS.yahoo_usd_price(root),
                                         want=name)
                adoc = report["accounts"][name]
        if adoc["undecided"] and interactive and unparsed:
            print(f"  note: not asking about crypto sends yet — account(s) "
                  f"{', '.join(unparsed)} have not been parsed, so a send "
                  f"to them would look unmatched.", file=sys.stderr)
        if adoc["undecided"]:
            print(f"  note: {adoc['undecided']} crypto send(s) not yet "
                  f"classified as self / gift / payment — `taxjson "
                  f"crypto-sends {name}` lists them (a gift or payment is "
                  f"a disposition at fair value).", file=sys.stderr)
        status, dups = _crypto_sends_tt(root, name, report)
        if status in ("written", "removed"):
            print(f"  crypto-sends: inputs/{name}/{CS.TT_NAME} {status}")
        for w in _dup_warning(name, dups):
            print(f"taxjson: WARNING: {w}", file=sys.stderr)
    except CS.RefusedDecision as e:
        # A saved decision the country refuses (a US gift): not booked,
        # and the run stops until sends.json says what it was.
        sys.exit(f"taxjson run: {name}: crypto sends: {e}")
    except ValueError as e:
        print(f"taxjson: WARNING: {name}: crypto sends: {e}",
              file=sys.stderr)


def ib_foreign_roc_mode(settings: Dict[str, Any]) -> str:
    """How the IB parser books an issuer-designated '(Return of
    Capital)' from a non-Canadian issuer: lib/country.foreign_roc_mode
    (the one resolver `run` and tax-logic share). Canada: [settings]
    foreign_return_of_capital, default "dividend" (ITA s.90(1)). USA:
    always "acb" (IRC s.301(c)(2); the key is Canada-only and refused
    in a US project — audit S013-01, partition INPUTS-02)."""
    from taxjson.lib.country import CountryError, foreign_roc_mode
    _country(settings)
    try:
        return foreign_roc_mode(settings)
    except CountryError as e:
        _die(str(e))


def _apply_override_log(corp_json: Path, log: Path, account: str) -> None:
    """Rename corp-action rows the way the security overrides renamed
    the same broker's parsed rows ((symbol, currency) -> new symbol).
    A pair the overrides renamed for some rows and not others is
    ambiguous (two securities share the broker's spelling): a corporate
    action on it is refused rather than booked on a guess."""
    import json as _json
    if not log.exists() or not corp_json.exists():
        return
    try:
        doc = _json.loads(log.read_text(encoding="utf-8"))
        corp = _json.loads(corp_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    renamed = {k: v for k, v in (doc.get("renamed") or {}).items() if v}
    kept = set(doc.get("kept") or [])
    if not renamed:
        return
    changed = False
    for t in corp.get("transactions", []) or []:
        cur = str(t.get("currency") or "").upper()
        for fld in ("symbol", "symbol_new"):
            sym = t.get(fld) or ""
            key = f"{sym}|{cur}"
            if key not in renamed:
                continue
            targets = renamed[key]
            if len(targets) > 1 or key in kept:
                _die(f"{account}: a corporate action on {sym} ({cur}), "
                     f"but ticker_extraction_overrides.txt renames some "
                     f"{sym} rows (to {', '.join(targets)}) and not "
                     f"others — two securities share that spelling, so "
                     f"the event cannot be assigned. Record it with a "
                     f".tt file (and `taxjson elect {account} --set "
                     f"<event>=ignore`).")
            t[fld] = targets[0]
            changed = True
    if changed:
        tmp = corp_json.with_name(corp_json.name + ".part")
        tmp.write_text(_json.dumps(corp, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        tmp.replace(corp_json)


# Project-root files the per-account stages read (their content is part
# of each account's input fingerprint).
_PROJECT_ROOT_INPUTS = ("ticker.map", "ticker_extraction_overrides.txt",
                        "distributions.map", "phantoms.json",
                        "crypto_ticker.map")


def _inputs_fingerprint(paths: List[Path]) -> str:
    """One line per existing file: name, size and SHA-256 of the
    content (mtimes deliberately left out — see R1-253)."""
    lines = []
    for p in sorted(set(paths), key=str):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        lines.append(f"{p.name} {len(data)} "
                     f"{hashlib.sha256(data).hexdigest()}")
    return "\n".join(lines) + "\n"


def stage_account(name: str, acfg: Dict[str, Any], settings: Dict[str, Any],
                  inputs_dir: Path, cache: Path, reports_dir: Path,
                  rates: Path, ticker_map: Optional[Path],
                  security_overrides: Optional[Path],
                  force: bool,
                  incomplete_history: Optional[Path] = None,
                  no_input: bool = False,
                  strict: bool = False,
                  ) -> Optional[Dict[str, Path]]:
    """Full per-account pipeline. Returns {base, gains, sum} paths, or None
    when the account has no inputs yet (a configured-but-unpopulated account,
    e.g. straight after `taxjson init`) — that's a warn-and-skip, not a fatal
    error, so the other accounts still run."""
    acct_dir = inputs_dir / name
    if not acct_dir.exists():
        print(f"taxjson: warning: no inputs dir for account '{name}' "
              f"({acct_dir}); skipping.", file=sys.stderr)
        return None

    base_currency = settings["base_currency"]
    country = _normalize_country(settings["country"])
    year = settings["year"]
    # Country-aware default: CRA times dispositions on SETTLEMENT date;
    # the IRS recognizes on the TRADE date. An explicit config wins.
    tax_date = _tax_date(settings)
    is_crypto = acfg.get("crypto", False)
    is_taxable = acfg.get("type", "sheltered") == "taxable"
    include_transfers = acfg.get("transfers", False)

    grouped = group_inputs(acct_dir)
    if not grouped and not input_files(acct_dir, ".tt"):
        _msg = (f"taxjson: warning: no CSVs or .tt files in "
                f"{acct_dir}; skipping account '{name}'.")
        if (cache / f"{name}_base.json").exists():
            # Inputs emptied but artifacts persist: every aggregate
            # keeps counting the account's OLD books with only this
            # line as the signal (2026-09 audit).
            _msg += (f" Its work/ artifacts from previous runs STILL "
                     f"EXIST and are still counted by sum/fees/"
                     f"reports — delete work/{name}_* and "
                     f"reports/{name}* if the account is truly gone.")
        print(_msg, file=sys.stderr)
        return None
    # Path-selection cross-check (KNOWN_ISSUES "crypto-vs-equity path"):
    # the pipeline split keys on the account's `crypto` flag alone, so a
    # kr_/cb_ file dropped into an equity account was routed through the
    # equity path (no fill-crypto → $0 FMVs) and an equity CSV in a
    # crypto account went through merge (no ticker-map/corp-actions) —
    # both silently. A mismatch is always a config or filing error;
    # refuse loudly.
    _CRYPTO_BROKERS = {"coinbase", "kraken"}
    _mismatched = ({b for b in grouped if b in _CRYPTO_BROKERS}
                   if not is_crypto else
                   {b for b in grouped if b not in _CRYPTO_BROKERS})
    if _mismatched:
        flag = "crypto = true" if is_crypto else "no crypto flag"
        _which = ", ".join(f"inputs/{name}/{p.name} ({b})"
                           for b in sorted(_mismatched)
                           for p in grouped[b])
        sys.exit(
            f"taxjson: account '{name}' has {flag} in taxjson.toml but "
            f"its inputs contain {', '.join(sorted(_mismatched))} files "
            f"[{_which}; routed by a cb_/kr_/generic_ name prefix, the "
            f"file's content, or a coinbase/kraken word in its name] "
            f"— the {'equity' if is_crypto else 'crypto'} data would be "
            f"routed through the wrong pipeline. Move the files to an "
            f"account of the matching type, or fix the account's "
            f"`crypto` flag.")
    # .tt-only accounts (hand-maintained records — the documented .tt
    # use case) used to be skipped here with a misleading "no CSVs"
    # warning before the .tt conversion stage ever ran: the whole book
    # silently vanished from every report at exit 0 (REVIEW #20).

    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"==> {name}  ({'taxable' if is_taxable else 'sheltered'}"
          f"{', crypto' if is_crypto else ''})")

    # Deletion-blindness guard (FUZZ #J): mtime deps only cover files
    # that EXIST — deleting an input CSV left its trades in the cached
    # books under --fast (the broker group's parsed JSON looked fresh
    # against the remaining, older CSVs). Persist the account's input
    # membership; add/remove bumps the manifest's mtime and re-runs the
    # parse + merge. Content-compare first so an unchanged listing
    # never dirties the cache.
    src_manifest = cache / f"{name}_sources.list"
    # Parser option (IB foreign ROC): in the manifest so a toggle re-parses.
    _froc_acb = ib_foreign_roc_mode(settings) == "acb"
    from taxjson.lib.country import futures_settle_mode
    _fut_next = futures_settle_mode(settings) == "next_day"
    # Membership covers EVERY input kind that feeds the merge — CSVs and
    # .tt files alike. Listing only CSVs left a deleted .tt's
    # transactions in the cached books under --fast (its converted JSON
    # dropped out of `sources`, so the merge output looked fresh against
    # every remaining dep).
    _map_entries = []
    for _c in grouped.get("generic", []):
        _sc = _c.with_name(_c.name + ".toml")
        _m = _sc if _sc.exists() else _c.parent / "generic.toml"
        # Record WHICH mapping is effective per file: deleting a
        # sidecar switches the file to the shared mapping without any
        # surviving dep getting newer — the FUZZ #J deletion-blindness
        # class, closed here by making the resolution part of the
        # manifest content.
        _map_entries.append(f"map/{_c.name}={_m.name if _m.exists() else '-'}")
    src_txt = "\n".join(sorted(
        [f"{broker}/{p.name}" for broker, csvs in grouped.items()
         for p in csvs]
        + [f"tt/{p.name}" for p in input_files(acct_dir, ".tt")]
        + _map_entries
        + (["setting/foreign_return_of_capital=acb"] if _froc_acb else [])
        + (["setting/futures_settle=next_day"] if _fut_next else [])
        # Income dating overrides move ROC dates (the ACB) and income
        # years: a change re-runs the books.
        + [f"setting/income_dating={' '.join(income_dating_flags(settings))}"]
        * bool(income_dating_flags(settings))
        # The crypto parsers date UTC stamps in this zone: a change
        # re-parses.
        + ([f"setting/local_timezone={settings['local_timezone']}"]
           if is_crypto and settings.get("local_timezone") else []))) + "\n"
    if (not src_manifest.exists()
            or src_manifest.read_text(encoding="utf-8") != src_txt):
        src_manifest.write_text(src_txt, encoding="utf-8")
    # Content, not just membership: `run --fast` compared mtimes only,
    # so a CSV replaced by a corrected export carrying an OLDER mtime
    # (cp -p, rsync -a, unzip) — or a ticker.map restored the same way
    # — kept the previous parse at exit 0 (R1-253, R1-294). A digest of
    # every input and project-root map file; when it changes, the
    # sources manifest (a dep of every parse/corp/merge stage) is
    # touched so the rebuild cascades.
    _fp_file = cache / f"{name}_inputs.fingerprint"
    _fp_txt = _inputs_fingerprint(
        [p for csvs in grouped.values() for p in csvs]
        + list(input_files(acct_dir, ".tt"))
        + [_c.with_name(_c.name + ".toml")
           for _c in grouped.get("generic", [])]
        + [acct_dir / "generic.toml"]
        + [inputs_dir.parent / _m for _m in _PROJECT_ROOT_INPUTS])
    if (not _fp_file.exists()
            or _fp_file.read_text(encoding="utf-8") != _fp_txt):
        _fp_file.write_text(_fp_txt, encoding="utf-8")
        import os as _os
        _os.utime(src_manifest)

    # 1. brokerage parse per broker
    parsed: List[Path] = []
    for broker, csvs in grouped.items():
        out = cache / f"{name}_{broker}.json"
        deps = (list(csvs) + [src_manifest]
                + ([security_overrides] if security_overrides else []))
        if broker == "generic":
            # A mapping edit must rebuild the parse like a CSV edit.
            for _c in csvs:
                _sc = _c.with_name(_c.name + ".toml")
                _m = _sc if _sc.exists() else _c.parent / "generic.toml"
                if _m.exists():
                    deps.append(_m)
        if force or needs_rebuild(out, *deps):
            print(f"  parse {broker}: {len(csvs)} file(s)")
            # --strict: a schema ERROR (negative trade net, zero split
            # ratio, a notional that contradicts the row's declared
            # contract multiplier ...) stops the run instead of
            # scrolling past as a warning into the filed numbers.
            cmd = _cmd("taxjson-brokerage") + ["--account", name,
                                               "--brokerage", broker,
                                               "--strict",
                                               "--account-type",
                                               "taxable" if is_taxable
                                               else "sheltered"]
            # Always explicit: the parser's own default is the neutral
            # cost reduction; s.90(1) is the Canadian project's choice
            # (partition INPUTS-03).
            cmd += ["--country", country, "--foreign-roc",
                    "acb" if _froc_acb else "dividend"]
            if _fut_next:
                cmd += ["--futures-settle", "next_day"]
            _sidecar = out.with_name(out.stem + "_transfers.json")
            if include_transfers:
                cmd.append("--transfers")
                # transfers=false -> true toggle: a stale sidecar
                # would be consumed ALONGSIDE the now-in-book rows
                # (double-counted by the transfers view and the
                # holdings evidence flips) — round-five audit.
                _sidecar.unlink(missing_ok=True)
            else:
                # Custody evidence sidecar: excluded TRANSFER rows are
                # kept queryable (`taxjson transfers`) instead of
                # silently deleted — a depot flip or broker migration
                # is exactly what explains a confusing position later.
                cmd += ["--transfers-out", str(_sidecar)]
            if security_overrides:
                cmd += ["--security-overrides", str(security_overrides),
                        "--override-log",
                        str(out.with_name(out.stem + ".overrides"))]
            else:
                out.with_name(out.stem + ".overrides").unlink(
                    missing_ok=True)
            cmd += [str(p) for p in csvs]
            run_to_file(cmd, out)
            # Surface per-file transaction counts (and any 0-tx
            # warnings) inline so the user can sanity-check at a
            # glance that each CSV contributed the expected number
            # of rows.
            echo_parse_stats(out)
        # Outside the rebuild branch on purpose: `run --strict --fast`
        # on a cached parse must hit the same gate.
        if strict and unbooked_lines(out):
            sys.exit(f"taxjson run --strict: {name}: {broker} input has "
                     f"event(s) the parser could not book (UNBOOKED "
                     f"warning above / in {out.name}.diag) — aborting.")
        parsed.append(out)

    # A non-empty export that parsed to 0 transactions (a renamed
    # header, a kr_-named file that is not a Kraken ledger) drops that
    # whole file from the books. Read from the persisted .diag on EVERY
    # run — cached or not — so the console warning and the --strict
    # gate cannot be skipped by a warm cache (R1-247).
    _empty_files: List[str] = []
    for _out in parsed:
        _d = _out.with_name(_out.name + ".diag")
        try:
            _lines = _d.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for _ln in _lines:
            _m = _ZERO_TX_RE.match(_ln.strip())
            if _m:
                _empty_files.append(_m.group(1))
    if _empty_files:
        _which = ", ".join(f"inputs/{name}/{f}" for f in _empty_files)
        if strict:
            sys.exit(f"taxjson run --strict: {name}: {_which} parsed to 0 "
                     f"transactions — every row in it would be missing "
                     f"from the books. Check the file's header/format (or "
                     f"its name: cb_/kr_/coinbase/kraken route it to a "
                     f"crypto parser), or remove it from inputs/.")
        for _f in _empty_files:
            print(f"taxjson: WARNING: {name}: inputs/{name}/{_f} parsed to "
                  f"0 transactions — NONE of its rows are in the books. "
                  f"Check the file's header/format (or its name: cb_/kr_/"
                  f"coinbase/kraken route it to a crypto parser); "
                  f"`run --strict` refuses this.", file=sys.stderr)

    # 1b. crypto sends: an outgoing transfer that never arrived on
    # another exchange is a gift, a payment, or a move to your own
    # wallet — only the owner knows. Ask at a TTY, then (re)generate
    # inputs/<acct>/crypto_sends.tt BEFORE the .tt stage below reads
    # the account's .tt files, so the decisions book in this run.
    if is_crypto and not include_transfers:
        _stage_crypto_sends(inputs_dir.parent, name,
                            interactive=not (no_input
                                             or not sys.stdin.isatty()))

    # 2. corp-actions per equity broker. taxjson-corp-actions requires
    # --manifest when multiple CSVs are passed, so always provide one.
    corp_files: List[Path] = []
    corp_brokers = sorted(b for b in grouped if b in CORP_ACTION_BROKERS)
    if not is_crypto and corp_brokers and not _country_has_corp_rules(country):
        # No election rules for this jurisdiction, so the stage below is
        # skipped — but broker parsers (RBC in particular) still SKIP
        # merger/CIL rows on the assumption that corp-actions consumes
        # them. With neither owner, those share movements would silently
        # vanish from the books. Say so loudly.
        print(f"taxjson: warning: country={country!r} has no corp-action election "
              f"rules — merger/reorg rows in {', '.join(corp_brokers)} "
              f"files for account {name!r} are NOT processed. RBC merger "
              f"rows are skipped by the parser, so any reorganization's "
              f"share movements will be MISSING from this account's books; "
              f"record them manually via a .tt file.", file=sys.stderr)
    if not is_crypto and _country_has_corp_rules(country):
        manifest_path = _resolve_manifest(acct_dir, cache, name, create=True)
        for broker, csvs in grouped.items():
            if broker not in CORP_ACTION_BROKERS:
                continue
            out = cache / f"{name}_{broker}_corp.json"
            # src_manifest dep: deleting a CSV must dirty the corp
            # stage too, or its rows from the deleted file live on
            # (2026-09 audit — the parse/merge stages had this dep,
            # the corp stage was missed).
            # rates dep: a cross-currency exchange's legs are valued
            # at the event-date rate (one fair value for both legs).
            # ticker_map dep: a temporary code the map now renames is
            # no longer warned about (S072-03).
            if force or needs_rebuild(out, *csvs, manifest_path,
                                      src_manifest, rates,
                                      *([ticker_map] if ticker_map
                                        else [])):
                print(f"  corp-actions {broker}")
                cmd = _cmd("taxjson-corp-actions") + [
                    "--account-name", name,
                    "--country", country,
                    "--brokerage", broker,
                    "--manifest", str(manifest_path),
                    "--rates", str(rates),
                    "--base-currency", base_currency,
                ] + (["--ticker-map", str(ticker_map)] if ticker_map
                     else [])
                # Interactive by default: corp-actions prompts for the tax
                # election (taxable vs rollover) on stderr and reads the
                # answer from stdin. Without a TTY (or with --no-input) it
                # writes the pending elections as JSON and exits 3 — the
                # GUI/headless path: resolve via `taxjson elect --set`,
                # re-run.
                non_interactive = no_input or not sys.stdin.isatty()
                pending_path = cache / f"{name}_pending_elections.json"
                if non_interactive:
                    cmd += ["--no-input",
                            "--pending-json", str(pending_path)]
                cmd += [str(p) for p in csvs]
                pending_path.unlink(missing_ok=True)   # stale from last run
                try:
                    run_to_file(cmd, out,
                                interactive=not non_interactive)
                except subprocess.CalledProcessError as e:
                    if e.returncode == 3 and pending_path.exists():
                        raise PendingElectionsError(name, pending_path)
                    raise
            # ticker_extraction_overrides.txt renamed this broker's
            # trade rows; the corp-action rows of the same security must
            # follow, or a merger consumed an empty un-overridden pool
            # while the real position stayed put (S004-00).
            _apply_override_log(out, cache / f"{name}_{broker}.overrides",
                                name)
            corp_files.append(out)
        _warn_zero_value_spinoffs(name, is_taxable, corp_files, cache)

    # 3. starting-position .tt files. Their converted JSON lives in its
    # own `<acct>_tt_<stem>` namespace: `<acct>_<stem>` collided with
    # the broker parse (`questrade.tt` overwrote <acct>_questrade.json,
    # silently dropping every CSV trade) and with pipeline
    # intermediates like <acct>_base.json (R1-116).
    tt_jsons: List[Path] = []
    from taxjson.lib.config_check import RESERVED_NAME_SUFFIXES
    for tt in input_files(acct_dir, ".tt"):
        _stem = tt.stem.lower()
        _clash = next((x for x in RESERVED_NAME_SUFFIXES
                       if _stem.endswith(x)), None)
        if _clash:
            # work/<acct>_tt_msft_gains.json read as the gains book of a
            # phantom account "<acct>_tt_msft" in `sum`, its fees counted
            # twice (S037-15): the artifact roles come from file names.
            _die(f"inputs/{name}/{tt.name}: a .tt file name may not end "
                 f"in {_clash!r} — its converted JSON would read as a "
                 f"pipeline artifact (a phantom account in the reports). "
                 f"Rename it, e.g. "
                 f"{tt.stem[:-len(_clash)] + _clash.replace('_', '-')}.tt")
        out = tt_json_path(cache, name, tt.name)
        if force or needs_rebuild(out, tt, src_manifest):
            print(f"  convert-tt {tt.name}")
            run_to_file(_cmd("taxjson-convert-tt") + ["--account-name", name, str(tt)],
                        out)
        tt_jsons.append(out)

    # Broker groups REMOVED from inputs/: their parsed JSON, .diag and
    # corp files would otherwise persist forever — stale .diag lines in
    # every .sum, dead fees counted by `taxjson-fees --cache`, dead
    # sources fed to the audit (2026-09 audit).
    _present = {f"{name}_{b}" for b in grouped} \
        | {f"{name}_{b}_corp" for b in grouped} \
        | ({f"{name}_{b}_transfers" for b in grouped}
           if not include_transfers else set()) \
        | {tt_json_path(cache, name, tt.name).stem
           for tt in input_files(acct_dir, ".tt")}
    # Prefix-sibling guard: for account `m`, the glob also matches
    # account `m_extra`'s artifacts — deleting those every run
    # destroyed m_extra's parsed books and (worse) its elections
    # manifest, the one artifact documented as NOT rebuildable
    # (2026-09 adversarial audit). Skip any stem that belongs to a
    # LONGER configured account name, and never touch manifests.
    _sibling_prefixes = []
    try:
        for _other in (_soft_config(acct_dir.parent.parent)
                       .get("accounts") or {}):
            if _other != name and _other.startswith(f"{name}_"):
                _sibling_prefixes.append(f"{_other}_")
    except Exception:
        pass
    for _stale in cache.glob(f"{name}_*.json"):
        _stem = _stale.name[:-len(".json")]
        if _stem in _present:
            continue
        if any(_stale.name.startswith(_pre)
               for _pre in _sibling_prefixes):
            continue
        _sfx = _stale.name[len(name):]
        if any(_sfx.endswith(x) for x in
               ("_base.json", "_gains.json", "_gains_wash.json",
                "_raw.json", "_raw_base.json", "_raw_gains.json",
                "_raw_base_gains.json", "_merged.json", "_sorted.json",
                "_filled.json", "_mapped.json", "_report.json",
                "_pending_elections.json", "_manifest.json",
                "_blend.diag")):
            continue
        # A parsed-source artifact with no surviving input group.
        for _victim in (_stale, _stale.with_name(_stale.name + ".diag")):
            if _victim.exists():
                _victim.unlink()
                print(f"  removed stale {_victim.name} (its input "
                      f"files are gone)")

    sources = tt_jsons + parsed + corp_files

    # 4. merge → base.json (crypto path differs: needs fill-crypto mid-stream)
    base_json = cache / f"{name}_base.json"
    if is_crypto:
        merged = cache / f"{name}_merged.json"
        # src_manifest is a dep here for the same reason it is on the
        # equity merge2 below: deleting an entire broker group (e.g. the
        # only kr_*.csv) removes its parsed JSON from `sources`, and
        # without the manifest nothing newer remains to dirty `merged`.
        if force or needs_rebuild(merged, *sources, src_manifest):
            print("  merge")
            run_to_file(_cmd("taxjson-merge") + [str(p) for p in sources], merged)
        sorted_ = cache / f"{name}_sorted.json"
        if force or needs_rebuild(sorted_, merged):
            print("  sort + dedup")
            run_to_file(_cmd("taxjson-sort") + ["--dedup", str(merged)], sorted_)
        # ticker.map GLOBAL/DELETE apply to crypto books too — the
        # README's contract is "every stage", but this path has no
        # merge2, so a Coinbase ETH2→ETH consolidation silently split
        # pools (2026-09 audit). Mapping runs BEFORE the price fill:
        # FMV lookups keyed by a dead alias (ETH2) either fetched the
        # wrong asset's price or left $0 basis (adversarial audit).
        # TOBASE/JOURNAL stay equity-only.
        mapped = sorted_
        if ticker_map:
            mapped = cache / f"{name}_mapped.json"
            if force or needs_rebuild(mapped, sorted_, ticker_map):
                print("  ticker-map (GLOBAL/DELETE)")
                run_to_file(_cmd("taxjson-ticker-map") + [
                    str(sorted_), "--map", str(ticker_map),
                    "--global-only"], mapped)
        elif (cache / f"{name}_mapped.json").exists():
            (cache / f"{name}_mapped.json").unlink()
        filled = cache / f"{name}_filled.json"
        # The project-root crypto_ticker.map (README) is resolved from
        # the project, never the cwd: `-C <proj>` from anywhere used to
        # ignore it (and a map in the cwd leaked into other projects).
        # Its state (content, or absence) is a rebuild dep through a
        # stamp that changes only when the map does, so `run --fast`
        # re-prices after the map is added, edited, or deleted.
        _cmap = inputs_dir.parent / "crypto_ticker.map"
        try:
            _cstate = ("sha256:" + hashlib.sha256(
                _cmap.read_bytes()).hexdigest()) if _cmap.is_file() \
                else "absent"
        except OSError:
            _cstate = "unreadable"
        _cstamp = cache / f"{name}_crypto_ticker_map.state"
        if (not _cstamp.exists()
                or _cstamp.read_text(encoding="utf-8").strip() != _cstate):
            _cstamp.write_text(_cstate + "\n", encoding="utf-8")
        if force or needs_rebuild(filled, mapped, _cstamp):
            print("  fill-crypto-prices")
            run_to_file(_cmd("taxjson-fill-crypto") + [
                "--project-root", str(inputs_dir.parent), str(mapped)],
                filled)
        if force or needs_rebuild(base_json, filled, rates):
            print(f"  convert-currency → {base_currency}")
            run_to_file(_cmd("taxjson-convert-currency") + [
                str(filled), "--to", base_currency, "--rates", str(rates),
                "--country", country,
            ], base_json)
        # The crypto path has no merge2 stage, so it never validates its
        # output. Run taxjson-validate explicitly and persist the report
        # as a .diag so crypto.sum carries an OK:/error line like the
        # merge2-validated equity accounts.
        validate_diag = cache / f"{name}_validate.diag"
        from taxjson.lib.dispatch import run_cmd as _run_cmd
        # --require-prices: a row fill-crypto could not price is an
        # ERROR here (R1-105), not a silent $0 income/cost.
        # Run from work/ on the bare file name: the report is copied into
        # reports/<name>.sum, and an absolute path put the user's home
        # directory (their OS user name) in every crypto report (S037-18).
        vres = _run_cmd(_cmd("taxjson-validate") + ["--require-prices",
                                                    base_json.name],
                        capture_output=True, cwd=str(base_json.parent))
        report = (vres.stdout or "") + (vres.stderr or "")
        if report.strip():
            validate_diag.write_text(report, encoding="utf-8")
        elif validate_diag.exists():
            validate_diag.unlink()
        # Quiet on success (the report is in the .diag → .sum); only
        # surface to the console if validation actually failed.
        if vres.returncode != 0:
            # `report` is text (run_cmd captures with text=True);
            # stderr.buffer.write(str) raised TypeError and crashed the
            # run instead of printing the report.
            sys.stderr.write(report)
            if strict:
                sys.exit(f"taxjson run --strict: {name}: validation "
                         f"ERROR(s) in the crypto books — aborting.")
            print(f"  !! {name}: validation ERROR(s) in the crypto books "
                  f"— numbers may be wrong. Details: reports/{name}.sum "
                  f"DIAGNOSTICS (or {validate_diag.name}).",
                  file=sys.stderr)
    else:
        cmd = _cmd("taxjson-merge2") + [
            "--sort", "--dedup", "--require-inputs",
            "--to", base_currency, "--rates", str(rates), "--validate",
            "--country", country,
        ]
        if ticker_map:
            cmd += ["--map", str(ticker_map)]
        cmd += [str(p) for p in sources]
        # Non-cash fund distributions (reinvested capital-gains dists /
        # late-published ROC factors) never appear in broker CSVs; a
        # project-root distributions.map turns them into ADJUST rows.
        # Taxable books only — sheltered ACB is moot.
        dist_map = inputs_dir.parent / "distributions.map"
        _apply_dists = is_taxable and dist_map.exists()
        deps = list(sources) + [rates, src_manifest] \
            + ([ticker_map] if ticker_map else []) \
            + ([dist_map] if _apply_dists else []) \
            + ([incomplete_history]
               if _apply_dists and incomplete_history else [])
        if force or needs_rebuild(base_json, *deps):
            print("  merge2 (sort, dedup, ticker-map, convert-currency, validate)")
            if not _apply_dists:
                run_to_file(cmd, base_json)
            else:
                # ONE atomic publish for merge2 + apply-distributions:
                # publishing merge2's output first and adjusting
                # in-place meant a failed/interrupted apply left a
                # fresh-but-unadjusted base that every later --fast
                # run trusted — the ADJUST rows silently vanished
                # until an unrelated rebuild (2026-09 audit).
                _stage = base_json.with_name(base_json.name + ".stage")
                try:
                    run_to_file(cmd, _stage)
                    print("  apply-distributions (distributions.map)")
                    from taxjson.lib.dispatch import run_cmd as _run_cmd_d
                    _dres = _run_cmd_d(
                        _cmd("taxjson-apply-distributions") + [
                            str(_stage), "--map", str(dist_map),
                            "--account", name,
                            # Record-date balance = holder of record,
                            # i.e. the SETTLED position in BOTH
                            # countries: a market fact, not the tax-
                            # year date basis (a US project's trade
                            # tax_date credited a buy traded on the
                            # record date — partition INPUTS-10).
                            "--date-basis", "settle",
                            "--country", country]
                        # Keys go through the same ticker.map renames
                        # as the book (S025-22).
                        + (["--ticker-map", str(ticker_map)]
                           if ticker_map else [])
                        # Size record-date balances WITH the phantom
                        # openings the gains stage synthesizes (S000-08).
                        + (["--incomplete-history",
                            str(incomplete_history)]
                           if incomplete_history else []),
                        capture_output=True)
                    if _dres.stderr:
                        sys.stderr.write(_dres.stderr)
                    if _dres.returncode != 0:
                        sys.exit(f"taxjson run: apply-distributions "
                                 f"failed for {name} (see above).")
                    _stage.replace(base_json)
                    # Publish the stage's diagnostics under the name
                    # every reader uses — the --strict gate and the
                    # .sum banner read <name>_base.json.diag, and a
                    # validation ERROR hidden in the .stage.diag
                    # sailed past --strict (2026-09 audit).
                    _sdiag = _stage.with_name(_stage.name + ".diag")
                    _bdiag = base_json.with_name(base_json.name
                                                 + ".diag")
                    if _sdiag.exists():
                        _sdiag.replace(_bdiag)
                    else:
                        _bdiag.unlink(missing_ok=True)
                finally:
                    _stage.unlink(missing_ok=True)
                    _stage.with_name(_stage.name + ".diag").unlink(
                        missing_ok=True)
        # Hard validation ERRORs must be VISIBLE, not archaeology: they
        # previously lived only in the raw .diag (FUZZ #M). This check
        # runs OUTSIDE the rebuild branch on purpose — the persisted
        # .diag describes the cached books too, and gating it on a
        # rebuild let `run --strict --fast` on unchanged-but-invalid
        # books skip the gate entirely and publish reports at exit 0
        # (the crypto path's unconditional validate never had the
        # hole). The warn-only default still completes the run.
        _diag = base_json.with_name(base_json.name + ".diag")
        if _diag.exists():
            _dtxt = _diag.read_text(errors="replace")
            import re as _re
            _m = _re.search(r"validation: (\d+) error", _dtxt)
            if _m and int(_m.group(1)) > 0:
                if strict:
                    # --strict (KNOWN_ISSUES "per-account validation is
                    # non-fatal"): a CI/cron run must not publish a
                    # wrong .sum at exit 0.
                    sys.exit(
                        f"taxjson run --strict: {name}: "
                        f"{_m.group(1)} validation ERROR(s) in the "
                        f"merged books — aborting. Details: "
                        f"{_diag.name}.")
                print(f"  !! {name}: {_m.group(1)} validation "
                      f"ERROR(s) in the merged books — numbers may "
                      f"be wrong. Details: reports/{name}.sum "
                      f"DIAGNOSTICS (or {_diag.name}).",
                      file=sys.stderr)

    # 5. gains
    gains_json = cache / f"{name}_gains.json"
    traces = cache / f"{name}_gains.traces"
    cmd = _cmd("taxjson-gains") + [
        "--country", country, "--year", str(year),
        "--tax-date", tax_date,
        "--full-traces", str(traces),
    ]
    wash_flags = _wash_flags(is_taxable, is_crypto, country)
    cmd += wash_flags
    if "--no-wash" in wash_flags:
        print("  note: wash-sale rule NOT applied — the IRS treats crypto "
              "as property, not a security (§1091 does not reach it); "
              "losses are allowed in full.")
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    # Project-wide phantom opening-balances (from `find-missing-history
    # --gen-phantoms`). load_phantoms filters by (symbol, account), so passing
    # the whole file to every account's gains run is safe — non-matching pairs
    # are ignored. A rebuild dependency so editing the file re-runs gains.
    gains_deps = [base_json]
    if incomplete_history is not None:
        cmd += ["--incomplete-history", str(incomplete_history)]
        gains_deps.append(incomplete_history)
    cmd.append(str(base_json))
    if force or needs_rebuild(gains_json, *gains_deps):
        print("  gains")
        run_to_file(cmd, gains_json)
    if is_taxable:
        _warn_expired_open_options(name, gains_json, cache, year)

    # 5b. Raw holdings: merge + sort + dedup, NO currency conversion and
    # NO validation; then gains with no options. The same ticker.map is
    # passed, but with no --to the merge applies only GLOBAL renames and
    # DELETEs — TOBASE consolidations are skipped, so cross-listings like
    # AEM.US / AEM.TO stay separate per actual listing. JOURNAL pairs are
    # netted later, at the export aggregation (post-gains — they can't be
    # merged pre-gains without a mixed-currency ACB pool).
    #
    # Emitted as a TOML holdings handoff. Skipped for crypto.
    if not is_crypto:
        raw_json = cache / f"{name}_raw.json"
        # src_manifest: deleting a .tt from a .tt-only account (no CSV
        # group left to cascade the change) kept its positions in the
        # raw holdings under --fast (S037-22) — the same dep merge2 has.
        raw_deps = (list(sources) + [src_manifest]
                    + ([ticker_map] if ticker_map else []))
        if force or needs_rebuild(raw_json, *raw_deps):
            print(f"  raw merge (sort, dedup"
                  f"{', ticker.map' if ticker_map else ''})")
            raw_cmd = _cmd("taxjson-merge2") + ["--sort", "--dedup"]
            if ticker_map:
                raw_cmd += ["--map", str(ticker_map)]
            raw_cmd += [str(p) for p in sources]
            run_to_file(raw_cmd, raw_json, capture_diag=False)
        mixed = _raw_mixed_currency_symbols(raw_json)
        if mixed:
            # Unrepresentable in a native-currency book (cross-currency
            # rollover rename): skip the raw view rather than kill the
            # run. The converted books, gains, wash and .sum reports
            # above are complete and authoritative.
            print(f"  !! raw holdings skipped for '{name}': "
                  f"{', '.join(mixed)} would pool mixed currencies "
                  f"after a cross-currency rollover rename. "
                  f"{name}_holdings.toml was NOT refreshed this run.",
                  file=sys.stderr)
            # The native books of an EARLIER run (before the rollover
            # rows arrived) would otherwise keep serving `taxjson gains`
            # without the rolled-over disposition (2026-09 audit S037-23).
            for _stale in (cache / f"{name}_raw_gains.json",
                           cache / f"{name}_raw_base.json",
                           cache / f"{name}_raw_base_gains.json"):
                if _stale.exists():
                    _stale.unlink()
                    print(f"  removed stale {_stale.name}",
                          file=sys.stderr)
        else:
            raw_gains = cache / f"{name}_raw_gains.json"
            if force or needs_rebuild(raw_gains, raw_json):
                print("  raw gains")
                # Match the country to the rest of the pipeline (it is
                # required; it used to default to Canada, and the US
                # raw-holdings inventory would aggregate FIFO lots as a
                # Canadian-style ACB blended pool — wrong total_cost per
                # symbol on the holdings.toml handoff.
                run_to_file(_cmd("taxjson-gains") + [
                    "--country", country,
                ] + option_timing_flags(settings)
                    + income_dating_flags(settings) + [str(raw_json)],
                            raw_gains, capture_diag=False)
            # Base-currency companion: convert the SAME raw merge to the base
            # currency (per-transaction FX, no ticker consolidation — so symbols
            # stay per-listing and line up 1:1 with raw_gains) then re-run gains.
            # This yields a base-currency ACB per holding (computed by the gains
            # engine at acquisition-date rates), which the holdings export embeds
            # as base_total_cost / base_cost_per_share so downstream tools can see
            # whether a position is in a gain or loss in base-currency terms.
            raw_base_json = cache / f"{name}_raw_base.json"
            if force or needs_rebuild(raw_base_json, raw_json, rates):
                print(f"  raw convert-currency → {base_currency}")
                # capture_diag here (unlike the other raw stages): convert-currency
                # emits its --default-rate FX-fallback summary on stderr, and a
                # silently-defaulted rate would corrupt base_total_cost. Persisting
                # it to the .diag surfaces any missing-rate fallback in the .sum.
                run_to_file(_cmd("taxjson-convert-currency") + [
                    str(raw_json), "--to", base_currency, "--rates", str(rates),
                    "--country", country,
                ], raw_base_json)
            raw_base_gains = cache / f"{name}_raw_base_gains.json"
            if force or needs_rebuild(raw_base_gains, raw_base_json):
                print("  raw base gains")
                run_to_file(_cmd("taxjson-gains") + [
                    "--country", country,
                ] + option_timing_flags(settings)
                    + income_dating_flags(settings) + [str(raw_base_json)],
                            raw_base_gains, capture_diag=False)
            # Machine-readable holdings handoff (TOML) for live-pricing /
            # trading tools. ticker.map's JOURNAL lines net offsetting
            # cross-currency legs (Norbert's Gambit) during aggregation.
            holdings_toml = reports_dir / f"{name}_holdings.toml"
            # Snapshot the existing holdings.toml so the post-export diff
            # has a baseline. First-run / no-prior-snapshot cases produce
            # no diff (correct behaviour — there's nothing to compare to).
            prev_holdings = cache / f"{name}_holdings_prev.toml"
            if holdings_toml.exists():
                shutil.copy2(holdings_toml, prev_holdings)
            elif prev_holdings.exists():
                # Stale snapshot from a prior run whose output was wiped;
                # discard so we don't diff against an out-of-date baseline.
                prev_holdings.unlink()
            # --futures: include futures positions (taxjson-export excludes
            # them by default). A holdings handoff should reflect everything
            # actually held, futures included.
            export_cmd = _cmd("taxjson-export") + [
                "--holdings-toml", "--account-name", name, "--futures",
            ]
            if ticker_map:
                export_cmd += ["--map", str(ticker_map)]
                # Evidenced depot flips: the per-broker transfer
                # sidecars prove which quantities journaled between a
                # security's listings — the holdings view applies
                # exactly those (a JOURNAL map line stays for
                # intrinsically fungible classes like DLR).
                for _sc in sorted(cache.glob(f"{name}_*_transfers.json")):
                    if any(_sc.name.startswith(_pre)
                           for _pre in _sibling_prefixes):
                        continue     # another account's sidecar
                    if _sc.name.startswith(f"{name}_tt_"):
                        continue     # a converted .tt, not a sidecar
                    export_cmd += ["--transfer-evidence", str(_sc)]
            export_cmd += ["--base-gains", str(raw_base_gains),
                           "--base-currency", base_currency,
                           "--trades", str(raw_json)]
            export_cmd.append(str(raw_gains))
            run_to_file(export_cmd, holdings_toml, capture_diag=False)
            print(f"  → {holdings_toml}")
            # Surface quantity changes vs the prior run so the user can
            # sanity-check trades at a glance (e.g. "I didn't expect AAPL
            # to move — did I import the wrong file?"). Silent on the
            # first run (no baseline) and silent when nothing changed.
            maybe_print_holdings_diff(prev_holdings, holdings_toml)

    # 6. summary — assembled in a .part sidecar and renamed on success, so a
    # failing sub-report can't leave a truncated .sum that `taxjson sum`
    # then serves (same atomicity contract as run_to_file).
    sum_path = reports_dir / f"{name}.sum"
    sum_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"  → {sum_path}")
    sum_tmp = sum_path.with_name(sum_path.name + ".part")
    try:
        with sum_tmp.open("wb") as out:
            # The pre-blend baseline: the cross-account passes' notes
            # (written after this file) go in <name>_wash.sum.
            out.write(_diagnostics_banner(cache, name, post_pass=False))
            out.write(run_capture(_cmd("taxjson-sum-gains")
                                  + (["--staking"] if is_crypto else [])
                                  + [str(gains_json)]))
            out.write(run_capture(_cmd("taxjson-sum-income") + [
                "--year", str(year), "--country", country,
            ] + income_dating_flags(settings) + [str(base_json)]))
            if not is_crypto:
                # The portfolio-snapshot report is equity-specific; crypto skips it.
                out.write(run_capture(_cmd("taxjson-export") + [
                    "--report", "--futures", str(gains_json),
                ]))
        sum_tmp.replace(sum_path)
    finally:
        sum_tmp.unlink(missing_ok=True)

    # Machine twin of the text reports (ADDITIVE — the .sum bytes above are
    # untouched): the structured aggregates `taxjson sum` and future
    # consumers read instead of re-deriving them from the gains JSON.
    try:
        from taxjson.lib.report_model import (build_account_report,
                                              load_report_json)
        report_json = cache / f"{name}_report.json"
        _base_rows = load_report_json(base_json).get(
            "transactions", [])
        report_json.write_text(
            _json_dumps_report(build_account_report(
                load_report_json(gains_json), name, basis="pre-wash",
                base_transactions=_base_rows,
                rules=_income_rules(settings))))
    except Exception as e:                      # advisory artifact only
        print(f"taxjson: warning: could not write {name}_report.json: {e}",
              file=sys.stderr)

    return {"base": base_json, "gains": gains_json, "sum": sum_path}


def _income_rules(settings: Dict[str, Any]):
    """The project's lib/income_dating rules (country + overrides)."""
    from taxjson.lib.income_dating import IncomeRules
    return IncomeRules.from_settings(settings)


def _json_dumps_report(payload) -> str:
    import json as _json
    return _json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _diagnostics_banner(cache: Path, account: str, *,
                        post_pass: bool = True) -> bytes:
    """A DIAGNOSTICS section for the top of a .sum file, or empty bytes
    when the account's pipeline stages produced no warnings/notes."""
    diag = collect_diagnostics(cache, account, post_pass=post_pass)
    if not diag:
        return b""
    rule = "=" * 70
    return (f"{rule}\n DIAGNOSTICS — {account}  "
            f"(parser/merge warnings; not part of the tax totals)\n{rule}\n"
            f"{diag}\n{rule}\n\n").encode()


def stage_wash_pass(name: str, settings: Dict[str, Any], cache: Path, reports_dir: Path,
                    sheltered_base: Path,
                    incomplete_history: Optional[Path] = None) -> None:
    """Re-run gains with --sheltered to apply cross-account wash sale."""
    print(f"==> {name} wash-radar pass")
    base_json = cache / f"{name}_base.json"
    wash_gains = cache / f"{name}_gains_wash.json"
    wash_traces = cache / f"{name}_gains_wash.traces"
    cmd = _cmd("taxjson-gains") + [
        "--taxable",
        "--country", _normalize_country(settings["country"]),
        "--year", str(settings["year"]),
        "--tax-date", _tax_date(settings),
        "--sheltered", str(sheltered_base),
        "--full-traces", str(wash_traces),
    ]
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    # Same phantom opening-balances as the main gains pass — without this the
    # wash-adjusted books (which `taxjson wash-sales` PREFERS when present)
    # were computed on different, phantom-less books than <account>.sum.
    if incomplete_history is not None:
        cmd += ["--incomplete-history", str(incomplete_history)]
    cmd.append(str(base_json))
    run_to_file(cmd, wash_gains)
    _render_wash_outputs(name, settings, cache, reports_dir,
                         wash_gains, base_json)


def _render_wash_outputs(name: str, settings: Dict[str, Any], cache: Path,
                         reports_dir: Path, wash_gains: Path,
                         base_json: Path) -> None:
    """The .sum + report.json rendering for one account's wash-adjusted
    gains — shared by the per-account (crypto) and blended (equity)
    passes."""
    wash_sum = reports_dir / f"{name}_wash.sum"
    # .part + rename: a failing sub-report must not truncate the .sum.
    wash_tmp = wash_sum.with_name(wash_sum.name + ".part")
    try:
        with wash_tmp.open("wb") as out:
            out.write(_diagnostics_banner(cache, name))
            _is_c = bool(((_soft_config(cache.parent).get("accounts")
                           or {}).get(name) or {}).get("crypto"))
            out.write(run_capture(_cmd("taxjson-sum-gains")
                                  + (["--staking"] if _is_c else [])
                                  + [str(wash_gains)]))
            out.write(run_capture(_cmd("taxjson-sum-income") + [
                "--year", str(settings["year"]),
                "--country", _normalize_country(settings["country"]),
            ] + income_dating_flags(settings) + [str(base_json)]))
            out.write(run_capture(_cmd("taxjson-export") + [
                "--report", "--futures", str(wash_gains),
            ]))
        wash_tmp.replace(wash_sum)
    finally:
        wash_tmp.unlink(missing_ok=True)

    # Rebuild the machine twin from the wash-adjusted gains: report.json
    # must carry the same basis the query commands resolve to (they prefer
    # <name>_gains_wash.json), or `taxjson sum`'s fast path would serve
    # pre-wash aggregates next to a "wash-adjusted" banner.
    try:
        from taxjson.lib.report_model import (build_account_report,
                                              load_report_json)
        report_json = cache / f"{name}_report.json"
        _base_rows = load_report_json(base_json).get(
            "transactions", [])
        report_json.write_text(
            _json_dumps_report(build_account_report(
                load_report_json(wash_gains), name,
                basis="wash-adjusted",
                base_transactions=_base_rows,
                rules=_income_rules(settings))))
    except Exception as e:                      # advisory artifact only
        print(f"taxjson: warning: could not write {name}_report.json: {e}",
              file=sys.stderr)
    print(f"  → {wash_sum}")


def stage_blended_wash_pass(names: List[str],
                            settings: Dict[str, Any], cache: Path,
                            reports_dir: Path,
                            sheltered_base: Optional[Path],
                            incomplete_history: Optional[Path] = None,
                            tag: str = "blend"
                            ) -> None:
    """ONE combined gains run over every taxable equity account, split
    back into the per-account `<name>_gains_wash.json` artifacts.
    `tag="cryptoblend"` runs the same pass over a Canadian project's
    crypto accounts (ITA s.47 averaging and the superficial-loss rule
    reach identical crypto held on different exchanges) with its own
    dot-prefixed intermediates.

    This is what makes the canonical (filed-from) numbers correct for
    multi-account books: Canada's ACB blends across all non-registered
    accounts (ITA s.47 — the engine's symbol-global pools do this
    naturally on a combined input) and US §1091 wash matching spans
    accounts while FIFO basis stays per account (--per-account-basis).
    Single-account projects produce identical numbers by construction.
    The per-account `<name>.sum` stays the isolated pre-blend baseline —
    comparing the pair shows exactly what blending changed."""
    print(f"==> blended taxable {'crypto ' if tag != 'blend' else ''}"
          f"pass ({', '.join(names)})")
    # Dot-prefixed intermediates: pathlib globs DO match leading dots
    # (`*_base.json` matches `.blend_base.json`), so every discovery
    # site — resolve_gains_files and the radar/missing-history fallback
    # globs — must ALSO filter `startswith(".")` explicitly. A visible
    # name here (or a glob site without the dot filter) would be
    # discovered as a phantom account and every aggregate would
    # double-count.
    combined_base = cache / f".{tag}_base.json"
    run_to_file(_cmd("taxjson-merge") + [
        str(cache / f"{n}_base.json") for n in names], combined_base)
    combined_wash = cache / f".{tag}_gains_wash.json"
    wash_traces = cache / f".{tag}_gains_wash.traces"
    country = _normalize_country(settings["country"])
    cmd = _cmd("taxjson-gains") + [
        "--taxable",
        "--country", country,
        "--year", str(settings["year"]),
        "--tax-date", _tax_date(settings),
        "--full-traces", str(wash_traces),
    ]
    if _normalize_country(country) in ("us", "usa"):
        cmd.append("--per-account-basis")
    if sheltered_base is not None:
        cmd += ["--sheltered", str(sheltered_base)]
    cmd += option_timing_flags(settings)
    cmd += income_dating_flags(settings)
    if incomplete_history is not None:
        cmd += ["--incomplete-history", str(incomplete_history)]
    cmd.append(str(combined_base))
    run_to_file(cmd, combined_wash)
    # The blended run's stderr lands in .blend_gains_wash.json.diag —
    # dot-prefixed, so collect_diagnostics' {account}_*.diag glob can
    # never surface it, and it is the ONLY pass that sees --sheltered
    # (its NOTEs explain the filed numbers). Mirror it into a per-
    # account diag each blended account's .sum banner picks up
    # (2026-09 audit).
    _blend_diag = combined_wash.with_name(combined_wash.name + ".diag")
    for name in names:
        _mirror = cache / f"{name}_{tag}.diag"
        if _blend_diag.exists() and _blend_diag.stat().st_size:
            _mirror.write_text(_blend_diag.read_text(errors="replace"),
                               encoding="utf-8")
        else:
            _mirror.unlink(missing_ok=True)
    for name in names:
        wash_gains = cache / f"{name}_gains_wash.json"
        # The split writes no .diag: a single-account wash pass's old
        # one would otherwise stay in this account's banner (S038-20).
        wash_gains.with_name(wash_gains.name + ".diag").unlink(
            missing_ok=True)
        run_to_file(_cmd("taxjson-split-gains") + [
            str(combined_wash), "--account", name,
            "--base", str(cache / f"{name}_base.json"),
        ], wash_gains, capture_diag=False)
        _render_wash_outputs(name, settings, cache, reports_dir,
                             wash_gains, cache / f"{name}_base.json")
    # Conservation check: the splitter apportions blended inventory
    # rows by each account's BASE-book balance, which does not include
    # phantom (OPENING_BALANCE) shares synthesized in-memory from
    # phantoms.json — those shares silently vanish from every
    # per-account holding. Compare Σ per-account qty vs the blended
    # row and say so instead of staying silent.
    try:
        import json as _json
        for _msg in _blend_conservation_gaps(
                _json.loads(combined_wash.read_text(encoding="utf-8")),
                [_json.loads((cache / f"{name}_gains_wash.json")
                             .read_text(encoding="utf-8"))
                 for name in names]):
            print(f"taxjson: warning: {_msg}", file=sys.stderr)
    except (OSError, ValueError, AttributeError):
        pass


def _blend_conservation_gaps(blended_doc: Dict[str, Any],
                             split_docs: List[Dict[str, Any]]) -> List[str]:
    """One message per blended inventory row whose per-account split
    does not add back up to it (the blended-pass conservation check —
    pinned by tests, G1-13)."""
    blended_inv = {r.get("symbol"): float(r.get("qty") or 0.0)
                   for r in (blended_doc.get("inventory") or [])
                   if not r.get("account")}
    split_sums: Dict[str, float] = {}
    for doc in split_docs:
        for r in (doc.get("inventory") or []):
            if r.get("blended_pool"):
                split_sums[r.get("symbol")] = (
                    split_sums.get(r.get("symbol"), 0.0)
                    + float(r.get("qty") or 0.0))
    out: List[str] = []
    for sym, total in sorted(blended_inv.items()):
        got = split_sums.get(sym, 0.0)
        if abs(total - got) > 1e-4:
            out.append(f"blended {sym} holds {total:g} but the per-account "
                       f"split accounts for only {got:g} — the difference "
                       f"is likely phantom (phantoms.json) shares, which "
                       f"the split cannot attribute to an account. "
                       f"Per-account holdings under-report by the gap.")
    return out


_EXPORT_MATRIX: Tuple[Tuple[str, List[str]], ...] = (
    ("AAll_SA.csv",             ["--seekingalpha"]),
    ("ALongUSD_SA.csv",         ["--seekingalpha", "--no-options", "--no-cad"]),
    ("ALongCAD_SA.csv",         ["--seekingalpha", "--no-options", "--no-usd"]),
    ("AOptionsUSD_SA.csv",      ["--seekingalpha", "--no-equities", "--no-cad"]),
    ("AOptionsCAD_SA.csv",      ["--seekingalpha", "--no-equities", "--no-usd"]),
    ("AOptionsShortUSD_SA.csv", ["--seekingalpha", "--no-equities", "--no-cad", "--short"]),
    ("AOptionsShortCAD_SA.csv", ["--seekingalpha", "--no-equities", "--no-usd", "--short"]),
    ("AOptionsLongUSD_SA.csv",  ["--seekingalpha", "--no-equities", "--no-cad", "--long"]),
    ("AOptionsLongCAD_SA.csv",  ["--seekingalpha", "--no-equities", "--no-usd", "--long"]),
    ("AAll_FG.csv",             ["--fastgraph"]),
    ("ALong_FG.csv",            ["--fastgraph", "--no-options"]),
    ("AOptionsShort_FG.csv",    ["--fastgraph", "--no-equities", "--short"]),
    ("AOptionsLong_FG.csv",     ["--fastgraph", "--no-equities", "--long"]),
    ("AAll_TV.txt",             ["--tradingview"]),
    ("ALong_TV.txt",            ["--tradingview", "--no-options"]),
    ("AOptionsShort_TV.txt",    ["--tradingview", "--no-equities", "--short"]),
    ("AOptionsLong_TV.txt",     ["--tradingview", "--no-equities", "--long"]),
)


def stage_exports(equity_gains: List[Path], reports_dir: Path) -> None:
    if not equity_gains:
        return
    print("==> exports")
    exports_dir = reports_dir / "exports"
    files = [str(p) for p in equity_gains]
    for fname, flags in _EXPORT_MATRIX:
        run_to_file(_cmd("taxjson-export") + flags + files, exports_dir / fname,
                    capture_diag=False)
    print(f"  → {exports_dir}/")


def _warn_cross_taxable_overlap(taxable_bases: List[Tuple[str, Path]],
                                settings: Dict[str, Any]) -> None:
    """Loud caveat for the KNOWN_ISSUES multi-account gap: when the SAME
    security appears in two or more TAXABLE accounts, the per-account
    fan-out computes legally wrong numbers — Canada's ACB must blend
    across all non-registered accounts (ITA s. 47), and US §1091 wash
    matching between two taxable accounts is not performed. This was
    entirely silent before; the run now names the overlapping symbols.
    This function only detects the overlap; the blended pass (the
    per-kind blended wash run) computes the blended numbers."""
    import json as _json
    if len(taxable_bases) < 2:
        return
    by_symbol: Dict[str, set] = {}
    for name, base in taxable_bases:
        try:
            doc = _json.loads(Path(base).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for t in doc.get("transactions", []):
            if t.get("action") in ("BUYSELL", "ASSIGN", "OPENING_BALANCE"):
                sym = (t.get("symbol") or "").strip()
                if sym:
                    by_symbol.setdefault(sym, set()).add(name)
    overlap = sorted(s for s, accts in by_symbol.items() if len(accts) > 1)
    if not overlap:
        return
    country = _country(settings)
    consequence = (
        "the blended pass matches US §1091 wash sales across accounts "
        "(FIFO basis stays per account)"
        if country in ("us", "usa") else
        "the blended pass computes their ACB across all non-registered "
        "accounts (ITA s. 47)")
    shown = ", ".join(overlap[:8]) + (
        f" (+{len(overlap) - 8} more)" if len(overlap) > 8 else "")
    print(f"taxjson: note: {shown} traded in more than one TAXABLE "
          f"account — {consequence}. The wash-adjusted artifacts "
          f"(<account>_wash.sum — the canonical filing numbers) carry "
          f"the blended figures; the per-account <account>.sum "
          f"baseline stays isolated, so the pair shows what blending "
          f"changed.", file=sys.stderr)


def _wash_preferred_gains(p: Path) -> Path:
    """The gains file the cross reports should read for a taxable
    account: the wash-adjusted twin when it exists and is at least as
    fresh, else the plain file. Same basis as the query twins —
    ccd-sum/leaps-sum resolve the wash-adjusted files, so building
    reports/ccd.rpt and leaps.rpt from pre-wash inputs made them
    disagree whenever a wash adjustment touched an option disposition.
    Freshness-guarded so a stale wash file from an older run (e.g. an
    account the wash pass no longer covers) can't shadow the
    just-rebuilt gains."""
    w = p.with_name(p.name.replace("_gains.json", "_gains_wash.json"))
    return w if (w.exists() and p.exists()
                 and w.stat().st_mtime >= p.stat().st_mtime) else p


def stage_cross_reports(all_gains: List[Path],
                        taxable_equity_base: List[Path],
                        sheltered_base: Optional[Path],
                        reports_dir: Path,
                        ticker_map: Optional[Path] = None,
                        phantoms: Optional[Path] = None,
                        *, country: str) -> None:
    if not all_gains:
        return
    print("==> cross-account reports")
    run_to_file(_cmd("taxjson-ccd-gains") + [str(p) for p in all_gains],
                reports_dir / "ccd.rpt", capture_diag=False)
    run_to_file(_cmd("taxjson-leaps-gains") + [str(p) for p in all_gains],
                reports_dir / "leaps.rpt", capture_diag=False)
    if taxable_equity_base:
        # --sheltered adds the cross-account (registered-repurchase) column to
        # the radar, but it's optional: same-account superficial-loss detection
        # runs without it, so the radar still generates for a taxable-only
        # project that defines no registered account.
        sheltered_arg = (["--sheltered", str(sheltered_base)]
                         if sheltered_base else [])
        for tb in taxable_equity_base:
            # Strip only the trailing "_base": replace() removed EVERY
            # "_base", so account a_base_x wrote account a_x's radar
            # (S038-10).
            stem = (tb.name[:-len("_base.json")]
                    if tb.name.endswith("_base.json") else tb.stem)
            # --json-out: structured sidecar next to the .rpt; the web UI
            # reads it and computes countdowns at view time.
            run_to_file(_cmd("taxjson-wash-radar") + [
                "--taxable", str(tb),
                "--json-out", str(reports_dir / f"wash_radar_{stem}.json"),
                "--account", stem,
            ] + _radar_engine_args([tb], phantoms, country)
                + sheltered_arg, reports_dir / f"wash_radar_{stem}.rpt",
                capture_diag=False)
        if len(taxable_equity_base) > 1:
            # Cross-account radar: a loss sold in one taxable account
            # with a rebuy in another IS a wash/superficial trigger
            # (the blended engine disallows it), but each per-account
            # sidecar sees only its own book. One combined run over
            # all taxable bases writes the sidecar harvest's ADVISORY
            # reads — matching what the engine will actually do. The
            # per-account files stay as the same-account views.
            run_to_file(_cmd("taxjson-wash-radar") + [
                "--taxable", *[str(tb) for tb in taxable_equity_base],
                "--json-out",
                str(reports_dir / "wash_radar_COMBINED.json"),
                "--account", "COMBINED",
            ] + _radar_engine_args(taxable_equity_base, phantoms, country)
                + sheltered_arg, reports_dir / "wash_radar_COMBINED.rpt",
                capture_diag=False)
        else:
            # Down to one taxable account: a leftover COMBINED pair
            # from an earlier multi-account state would shadow the
            # fresh per-account sidecar in harvest forever.
            for _ext in (".json", ".rpt"):
                _p = reports_dir / f"wash_radar_COMBINED{_ext}"
                if _p.exists():
                    _p.unlink()
        # Cross-listing lint: flag any root on both .TO and .US that the radar
        # would treat as two securities but maybe shouldn't (a TOBASE that
        # didn't consolidate, or a newly-traded interlisting missing from the
        # map). Advisory only — never fails the run.
        cmd = _cmd("taxjson-lint-crosslistings") + [
            "--taxable", *[str(tb) for tb in taxable_equity_base],
        ] + sheltered_arg
        if ticker_map:
            cmd += ["--map", str(ticker_map)]
        run_to_file(cmd, reports_dir / "crosslistings.rpt", capture_diag=False)
    print(f"  → {reports_dir}/")


def stage_fees(cache: Path, settings: Dict[str, Any], rates: Path,
               reports_dir: Path) -> None:
    """Trading-fee report by brokerage, converted to the base currency for a
    cross-broker comparison. Reads the parsed per-broker JSONs in the cache
    (the only place the brokerage tag survives), year-scoped to the tax year."""
    print("==> fees report")
    # capture_diag=True: taxjson-fees emits FX default-rate fallback and
    # skipped-file warnings on stderr; persist them to the .diag so a silently
    # wrong rate can't slip through (the report body carries them too).
    _tm = cache.parent / "ticker.map"
    run_to_file(_cmd("taxjson-fees") + [
        "--cache", str(cache),
        "--year", str(settings["year"]),
        "--to", settings["base_currency"], "--rates", str(rates),
    ] + (["--ticker-map", str(_tm)] if _tm.exists() else []),
        reports_dir / "fees.rpt")
    print(f"  → {reports_dir}/fees.rpt")


# ---------------------------------------------------------------- entry points

def _refuse_phantoms_for_unknown_accounts(phantoms: Path,
                                          accounts: Dict[str, Any]) -> None:
    """phantoms.json is keyed by account LABEL. An entry whose account is
    not in [accounts] (the account was renamed or removed) used to be
    skipped silently — its opening vanished and the filed gain changed
    (audit S021-05: a pure relabel moved a real book by -71,734.84).
    Refuse the run and name each stale label with a suggestion."""
    import difflib
    from taxjson.lib.phantom_holdings import load_phantoms
    try:
        pairs = load_phantoms(phantoms)
    except (OSError, ValueError) as e:
        sys.exit(f"taxjson run: {phantoms}: {e}")
    stale: Dict[str, List[str]] = {}
    for sym, acct in sorted(pairs):
        if acct not in accounts:
            stale.setdefault(acct, []).append(sym)
    if not stale:
        return
    known = sorted(accounts)
    lines = []
    for acct, syms in sorted(stale.items()):
        near = difflib.get_close_matches(acct, known, n=1, cutoff=0.0)
        hint = f" — did you rename it to {near[0]!r}?" if near else ""
        more = f" (+{len(syms) - 5} more)" if len(syms) > 5 else ""
        lines.append(f"  {acct!r}: {len(syms)} entr"
                     f"{'y' if len(syms) == 1 else 'ies'} "
                     f"({', '.join(syms[:5])}{more}){hint}")
    sys.exit(
        f"taxjson run: {phantoms} names account(s) that are not in "
        f"taxjson.toml [accounts] ({', '.join(known) or 'none'}):\n"
        + "\n".join(lines)
        + "\n  Those openings would be skipped and the gains would "
        "change silently. Edit the \"account\" of each entry to the "
        "current account name (or delete the entries if the account "
        "is gone), then run again.")


def _warn_year_without_activity(year: Any, bases: List[Path]) -> None:
    """Warn when no row of this run's books is dated in [settings]
    year. A typo'd in-range year (2015 for 2025) built all-zero filing
    totals with exit 0 and no word (R1-256)."""
    import json as _json
    if not year or not bases:
        return
    ys = str(year)
    lo = hi = None
    for b in bases:
        try:
            txs = _json.loads(Path(b).read_text(encoding="utf-8")).get(
                "transactions", [])
        except (OSError, ValueError, AttributeError):
            return                   # unreadable: other checks say so
        for t in txs:
            for d in (str(t.get("date") or "")[:10],
                      str(t.get("date_settle") or "")[:10]):
                if d.startswith(ys):
                    return
                if len(d) == 10:
                    lo = d if lo is None or d < lo else lo
                    hi = d if hi is None or d > hi else hi
    if lo is None:
        return                       # empty books: said elsewhere
    print(f"taxjson: warning: no transaction in any account's books is "
          f"dated {ys} (the books run {lo} .. {hi}) — every {ys} filing "
          f"total will be 0. Is [settings] year in taxjson.toml right?",
          file=sys.stderr)


def cmd_run(args: argparse.Namespace) -> None:
    # Full rebuild is the DEFAULT: stale cached artifacts must never
    # feed a filing decision. `--fast` opts back into the mtime cache.
    args.force = not getattr(args, "fast", False)
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    # Register the config path globally so every `needs_rebuild` call
    # picks it up as an implicit dependency. Touching taxjson.toml
    # (e.g. bumping `year` for a new tax season, switching country,
    # adding a source_currency) now invalidates cached outputs.
    global _CONFIG_PATH
    _CONFIG_PATH = root / "taxjson.toml"
    settings = cfg.get("settings", {})
    accounts = cfg.get("accounts", {})
    if _country(settings) == "usa":
        print(_US_EXPERIMENTAL_NOTE, file=sys.stderr)
    _since_warn = _grant_since_warning(settings)
    if _since_warn and any(_c.get("type") == "taxable" and not _c.get("crypto")
                           for _c in accounts.values()):
        # (a crypto-only project writes no options — nothing to warn about)
        print(f"taxjson: warning: {_since_warn}", file=sys.stderr)

    # Orphaned artifacts from RENAMED/REMOVED accounts: work/ files
    # keep matching the discovery globs (resolve_gains_files, fees
    # --cache, the audit's --source scan), so a renamed account was
    # counted TWICE in every aggregate (2026-09 audit). Loud, with the
    # exact fix — not auto-deleted (the user may want the history).
    try:
        _known = set(accounts)
        _cache_names = set()
        for _p in (root / "work").glob("*_base.json"):
            if _p.name.startswith(".") \
                    or _p.name == "sheltered_base.json":
                continue
            _nm = _p.name[:-len("_base.json")]
            if _nm.endswith("_raw"):
                # `S_raw_base.json` is usually account S's raw-books
                # artifact — but only skip it when that S actually
                # exists, or a REAL account named `ib_raw` would never
                # be flagged (2026-09 audit).
                _parent = _nm[:-len("_raw")]
                if _parent in _known or (
                        root / "work" / f"{_parent}_base.json"
                        ).exists():
                    continue
            _cache_names.add(_nm)
        _orphans = sorted(_cache_names - _known)
        if _orphans:
            print(f"taxjson: warning: work/ carries artifacts for "
                  f"account(s) not in taxjson.toml: "
                  f"{', '.join(_orphans)} — these are STILL COUNTED "
                  f"by sum/fees/wash tools (a renamed account is "
                  f"counted twice). Delete work/<name>_* and "
                  f"reports/<name>* for each, or restore the account "
                  f"in the config.", file=sys.stderr)
    except OSError:
        pass
    if not accounts:
        _die("no [accounts.*] sections in taxjson.toml")
    # Warnings FIRST: a typo'd `yeer = 2025` must show its did-you-mean
    # before the "missing year" death it causes.
    for msg in validate_config(cfg, root / "inputs"):
        print(f"taxjson: warning: taxjson.toml: {msg}", file=sys.stderr)
    for required in ("year", "country", "base_currency"):
        if required not in settings:
            _die(f"missing [settings] {required} in taxjson.toml")

    inputs_dir = root / "inputs"
    cache = root / "work"
    reports_dir = root / "reports"
    # work/ or reports/ that is a FILE, or a project directory that
    # cannot be written, tracebacked — reports/ only after every stage
    # had rewritten work/, leaving the two out of step (S038-17).
    for _d in (cache, reports_dir):
        try:
            _d.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as e:
            _die(f"cannot use {_d}: "
                 + ("it exists and is not a directory"
                    if isinstance(e, FileExistsError) or _d.is_file()
                    else (e.strerror or str(e)))
                 + " — nothing was run.")
        import os as _os_mod
        if not _os_mod.access(_d, _os_mod.W_OK | _os_mod.X_OK):
            _die(f"cannot write to {_d} — nothing was run.")
    # --fast trusts cached stages only when they were built by THIS code
    # (content, not mtimes — S039-03). The stamp is removed now and
    # rewritten when the run completes, so a run that stops half-way
    # never vouches for stages it did not rebuild.
    _code_stamp = cache / _CODE_STAMP
    _code_fp = _package_fingerprint()
    if not args.force:
        try:
            _old_fp = _code_stamp.read_text(encoding="utf-8").strip()
        except OSError:
            _old_fp = ""
        if _old_fp != _code_fp:
            print("==> taxjson's code changed since the cached stages were "
                  "built (or no complete run recorded it) — rebuilding "
                  "everything; --fast applies from the next run")
            args.force = True
    _code_stamp.unlink(missing_ok=True)
    # ticker.map — one keyword-prefixed symbol-rule file. GLOBAL renames
    # apply everywhere; TOBASE consolidations apply only in the main
    # (to-base) merge; JOURNAL pairs also net in the holdings export;
    # DELETE nukes a ticker. Each merge/export stage requests its subset.
    ticker_map = root / "ticker.map"
    ticker_map_arg = ticker_map if ticker_map.exists() else None
    if ticker_map_arg:
        # A line the loader cannot parse DROPS its rule, and a dropped
        # TOBASE/GLOBAL/JOURNAL rule moves ACB pools and the Schedule 3
        # gain; its warning used to reach only reports/*.sum while the
        # run (even --strict) exited 0 (S009-03). Refuse up front.
        from taxjson.bin.taxjson_ticker_map import map_file_problems
        try:
            _tm_problems = map_file_problems(ticker_map)
        except OSError as e:        # not UTF-8 (S053-06)
            _die(str(e))
        if _tm_problems:
            _die(f"{len(_tm_problems)} ticker.map problem(s) — a "
                 f"malformed line's rule would be silently dropped, and "
                 f"contradictory rules (a cycle, two targets for one "
                 f"symbol, a DISTINCT pair the renames pool) have no "
                 f"single meaning; either changes ACB pools and gains:"
                 f"\n    "
                 + "\n    ".join(_tm_problems)
                 + "\n  Fix the line (KEYWORD FROM TO, separated by "
                 "spaces; notes after `#`) or delete it.")
    # ticker_extraction_overrides.txt — description-keyed ticker
    # corrections for securities the currency->exchange suffix mislabels.
    sec_overrides = root / "ticker_extraction_overrides.txt"
    sec_overrides_arg = sec_overrides if sec_overrides.exists() else None
    if sec_overrides_arg:
        # Same up-front refusal as ticker.map (S053-04): a malformed
        # line would drop its ticker fix and split an ACB pool.
        from taxjson.bin.taxjson_brokerage import load_security_overrides
        try:
            load_security_overrides(sec_overrides)
        except ValueError as e:
            _die(str(e))
    # phantoms.json — optional project-wide list of (symbol, account) pairs
    # with missing pre-window history (from `find-missing-history
    # --gen-phantoms`). Auto-detected at the root like ticker.map; when present
    # it feeds every account's gains run via --incomplete-history.
    phantoms = root / "phantoms.json"
    phantoms_arg = phantoms if phantoms.exists() else None
    if phantoms_arg:
        _refuse_phantoms_for_unknown_accounts(phantoms_arg, accounts)
    # Deletion detection: needs_rebuild compares mtimes of EXISTING inputs,
    # so removing phantoms.json left cached gains — built WITH phantoms —
    # looking fresh forever. Track application with a marker; on removal,
    # bump the base files' mtimes so every gains stage rebuilds clean.
    _ph_marker = cache / ".phantoms_applied"
    if phantoms_arg:
        print(f"==> phantom openings: {phantoms.name}")
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        _ph_marker.write_text(str(phantoms))
    elif _ph_marker.exists():
        print("==> phantoms.json removed — invalidating cached gains built "
              "with it")
        _ph_marker.unlink()
        import os as _os
        for _f in cache.glob("*_base.json"):
            _os.utime(_f)
        # distributions.map ADJUSTs in the taxable base books were
        # sized WITH the phantom openings (S000-08): drop those books
        # so the merge stage rebuilds them, not just the gains.
        if (root / "distributions.map").exists():
            for _n, _c in accounts.items():
                if isinstance(_c, dict) and _c.get("type") == "taxable":
                    (cache / f"{_n}_base.json").unlink(missing_ok=True)

    # Same deletion-blindness class for the project-root map files:
    # needs_rebuild only sees deps that EXIST, so deleting ticker.map /
    # distributions.map / ticker_extraction_overrides.txt left their
    # renames, ADJUST rows, and ticker fixes in the cached books under
    # --fast forever. Track which are applied; on removal, bump every
    # account's sources manifest (a parse-stage dep) so the rebuild
    # cascades through merge and gains.
    _maps_marker = cache / ".project_maps_applied"
    _maps_now = sorted(p.name for p in (ticker_map, sec_overrides,
                                        root / "distributions.map")
                       if p.exists())
    _maps_before = (_maps_marker.read_text(encoding="utf-8").split()
                    if _maps_marker.exists() else [])
    _maps_removed = sorted(set(_maps_before) - set(_maps_now))
    if _maps_removed:
        print(f"==> {', '.join(_maps_removed)} removed — invalidating "
              f"cached books built with it")
        import os as _os
        for _f in cache.glob("*_sources.list"):
            if not _f.name.startswith("."):
                _os.utime(_f)
    if _maps_now:
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _maps_now != _maps_before:
            _maps_marker.write_text("\n".join(_maps_now) + "\n",
                                    encoding="utf-8")
    elif _maps_marker.exists():
        _maps_marker.unlink()

    print("==> currency rates")
    rates = stage_currency_rates(settings, cache)

    sheltered_items = [(n, c) for n, c in accounts.items()
                       if c.get("type", "sheltered") == "sheltered"]
    taxable_items = [(n, c) for n, c in accounts.items()
                     if c.get("type") == "taxable"]

    if args.account:
        sheltered_items = [(n, c) for n, c in sheltered_items if n == args.account]
        taxable_items = [(n, c) for n, c in taxable_items if n == args.account]
        if not sheltered_items and not taxable_items:
            _die(f"no [accounts.{args.account}] in taxjson.toml")

    no_input = getattr(args, "no_input", False)
    pending_accounts: List[PendingElectionsError] = []

    sheltered_outputs: List[Tuple[str, Dict[str, Path]]] = []
    _skipped_no_input: List[str] = []       # no CSV/.tt — see B7 helper
    for name, acfg in sheltered_items:
        try:
            out = stage_account(name, acfg, settings, inputs_dir, cache,
                                reports_dir, rates, ticker_map_arg,
                                sec_overrides_arg, args.force,
                                incomplete_history=phantoms_arg,
                                no_input=no_input,
                                strict=getattr(args, "strict", False))
        except PendingElectionsError as pe:
            print(f"  !! {name}: corp-action elections required — "
                  f"account deferred", file=sys.stderr)
            pending_accounts.append(pe)
            continue
        if out is None:                     # unpopulated account — skipped
            _skipped_no_input.append(name)
            continue
        sheltered_outputs.append((name, out))

    sheltered_base: Optional[Path] = None
    _sheltered_merge_inputs: List[Path] = []
    if sheltered_outputs and not args.account:
        _sheltered_merge_inputs = [o["base"] for _, o in sheltered_outputs]
    elif sheltered_outputs and args.account:
        # `--account <sheltered>`: the named book is fresh, the other
        # sheltered books are whatever the last run left in work/. Fold
        # them together anyway — leaving sheltered_base.json untouched
        # meant a later `wash-radar --account margin` (and the web/GUI
        # what-if) read a sheltered book that predated this run's buys,
        # exactly the rows a 30-day radar exists to see (2026-09 audit).
        _others = [cache / f"{n}_base.json" for n, c in accounts.items()
                   if c.get("type", "sheltered") == "sheltered"
                   and n != args.account]
        _missing = [p.name for p in _others if not p.exists()]
        _sheltered_merge_inputs = (
            [o["base"] for _, o in sheltered_outputs]
            + [p for p in _others if p.exists()])
        print(f"==> rebuilding sheltered_base.json from this run's "
              f"{args.account} book plus the other sheltered accounts' "
              f"last-built books"
              + (f" (missing: {', '.join(_missing)} — never built; "
                 f"run without --account)" if _missing else ""))
    _pending_sheltered = {pe.account for pe in pending_accounts}
    if (not _sheltered_merge_inputs and not args.account
            and not _pending_sheltered):
        # No sheltered account produced books in this FULL run (the last
        # one was removed, or its inputs emptied): the previous run's
        # combined book must go, or the filed-year lock, check-filed and
        # the radar keep reading registered holdings that no longer
        # exist — a false OK on a moved filed number (S004-05).
        for _ghost in (cache / "sheltered_base.json",
                       cache / "sheltered_base.json.diag"):
            if _ghost.exists():
                _ghost.unlink()
                print(f"  removed stale {_ghost.name} (no sheltered "
                      f"account has books)")
    if _sheltered_merge_inputs:
        print("==> merge sheltered accounts → sheltered_base.json")
        sheltered_base = cache / "sheltered_base.json"
        run_to_file(_cmd("taxjson-merge")
                    + [str(p) for p in _sheltered_merge_inputs],
                    sheltered_base)
        # Sanity-check the combined sheltered file before the wash-radar
        # pass consumes it. Quiet on success; surface and halt on failure.
        from taxjson.lib.dispatch import run_cmd as _run_cmd
        vres = _run_cmd(_cmd("taxjson-validate") + [str(sheltered_base)],
                        capture_output=True)
        if vres.returncode != 0:
            sys.stderr.write((vres.stdout or "") + (vres.stderr or ""))
            # A clean exit, not a raw CalledProcessError traceback —
            # the validator's output above already names the offending
            # transaction(s); tell the user where to look next.
            sys.exit(
                "taxjson run: the combined sheltered book failed "
                "validation (details above) — the wash pass cannot "
                "trust it. The usual cause is a malformed row from a "
                "corp-action election; check the named account's "
                "DIAGNOSTICS in its .sum, fix the election "
                "(`taxjson elect <account> --redo`) or the input row, "
                "and re-run.")

    taxable_outputs: List[Tuple[str, Dict[str, Path], bool]] = []
    _blend_names: List[str] = []
    # Canada: crypto accounts blend with each other when the project
    # CONFIGURES two or more (decided on the config, not on this run's
    # --account subset, so a single-account rerun never rewrites a
    # blended wash file with a one-exchange one).
    _crypto_blend_names: List[str] = []
    _crypto_blend = (
        _country(settings)
        not in ("us", "usa")
        and sum(1 for _c in accounts.values()
                if _c.get("type") == "taxable" and _c.get("crypto")) >= 2)
    if not args.account:
        # A per-account blend mirror is rewritten only for accounts IN
        # this run's blend: one that left it (re-typed, the other
        # crypto account removed) kept its old mirror, and its fixed
        # problem was re-reported in every later banner (S038-08).
        for _n, _c in accounts.items():
            _c = _c or {}
            _taxable = _c.get("type") == "taxable"
            for _tag, _in_blend in (
                    ("blend", _taxable and not _c.get("crypto")),
                    ("cryptoblend", _taxable and bool(_c.get("crypto"))
                     and _crypto_blend)):
                _m = cache / f"{_n}_{_tag}.diag"
                if not _in_blend and _m.exists():
                    _m.unlink()
    for name, acfg in taxable_items:
        try:
            out = stage_account(name, acfg, settings, inputs_dir, cache,
                                reports_dir, rates, ticker_map_arg,
                                sec_overrides_arg, args.force,
                                incomplete_history=phantoms_arg,
                                no_input=no_input,
                                strict=getattr(args, "strict", False))
        except PendingElectionsError as pe:
            print(f"  !! {name}: corp-action elections required — "
                  f"account deferred", file=sys.stderr)
            pending_accounts.append(pe)
            continue
        if out is None:                     # unpopulated account — skipped
            _skipped_no_input.append(name)
            continue
        is_crypto = acfg.get("crypto", False)
        taxable_outputs.append((name, out, is_crypto))
        # Wash second pass: crypto is skipped only for US projects
        # (§1091 does not reach digital assets — matching _wash_flags).
        # Canada's superficial-loss rule covers ANY identical property,
        # crypto included: the engine already wash-checks Canadian
        # crypto in the main pass, so the advisory tooling must not go
        # silent on exactly the account type where 30-day rebuys are
        # most common.
        _crypto_wash_covered = _country(settings) not in ("us", "usa")
        if not is_crypto:
            # Equity taxable accounts are handled by ONE blended pass
            # after this loop (Canada ACB blending / US cross-account
            # §1091 — the multi-account fix). Collected here.
            _blend_names.append(name)
        elif _crypto_wash_covered and _crypto_blend:
            # Two or more Canadian crypto accounts: ONE blended crypto
            # pass after this loop (s.47 averaging and superficial loss
            # across exchanges — each exchange's book alone missed both).
            _crypto_blend_names.append(name)
        elif _crypto_wash_covered and sheltered_base is not None:
            stage_wash_pass(name, settings, cache, reports_dir, sheltered_base,
                            incomplete_history=phantoms_arg)
        elif not args.account and not pending_accounts:
            # A FULL run decided this crypto account gets no wash pass
            # (US policy excludes it, or the sheltered context vanished
            # by data). A stale _gains_wash.json would otherwise keep
            # winning resolve_gains_files forever. (Kept under
            # --account and pending-election deferrals — transient.)
            for _stale in (cache / f"{name}_gains_wash.json",
                           cache / f"{name}_gains_wash.traces",
                           cache / f"{name}_gains_wash.json.diag",
                           reports_dir / f"{name}_wash.sum"):
                _stale.unlink(missing_ok=True)

    _record_skipped_accounts(cache, _skipped_no_input,
                             only=args.account or None)
    if (not args.account and not pending_accounts
            and not sheltered_outputs and not taxable_outputs):
        # Every account was skipped for having no inputs: "Done" with
        # exit 0 read as success on an empty project (2026-09 CLI audit).
        print(f"\ntaxjson run: no account had any input — drop broker "
              f"CSV exports (or .tt files) into {inputs_dir}/<account>/ "
              f"and re-run. Nothing was computed.", file=sys.stderr)
        if getattr(args, "strict", False):
            raise SystemExit(1)
        return
    if _blend_names and not args.account and not pending_accounts:
        stage_blended_wash_pass(_blend_names, settings, cache,
                                reports_dir, sheltered_base,
                                incomplete_history=phantoms_arg)
    if _crypto_blend_names and not args.account and not pending_accounts:
        stage_blended_wash_pass(_crypto_blend_names, settings, cache,
                                reports_dir, sheltered_base,
                                incomplete_history=phantoms_arg,
                                tag="cryptoblend")
    if not args.account and not pending_accounts:
        # A sheltered account never gets a wash pass. One re-typed from
        # taxable kept its old <name>_gains_wash.json / _wash.sum, which
        # resolve_gains_files prefers forever — the "run a full `taxjson
        # run`" remedy never cleared them (S038-19).
        for _n, _c in accounts.items():
            if (_c or {}).get("type") != "sheltered":
                continue
            for _stale in (cache / f"{_n}_gains_wash.json",
                           cache / f"{_n}_gains_wash.traces",
                           cache / f"{_n}_gains_wash.json.diag",
                           reports_dir / f"{_n}_wash.sum"):
                if _stale.exists():
                    _stale.unlink()
                    print(f"  removed stale {_stale.name} ({_n} is "
                          f"sheltered — no wash pass)")

    if pending_accounts:
        # Some account(s) stopped at unresolved corp-action elections.
        # Aggregate the per-account pending JSONs into ONE document a
        # GUI (or the user) can act on, explain the resolution path,
        # and exit 3 — the cross-account passes below would be built
        # from incomplete books.
        import json as _json
        agg: Dict[str, Any] = {"schema_version": 1, "accounts": {}}
        agg_path = cache / "pending_elections.json"
        # Under --account only a SUBSET was re-examined: other accounts'
        # pending entries in the existing aggregate are still live —
        # overwriting (or, below, unlinking) the file lost their
        # ready-to-copy --set lines (REVIEW #33).
        if args.account and agg_path.exists():
            try:
                prior = _json.loads(agg_path.read_text(encoding="utf-8"))
                agg["accounts"].update(
                    {a: d for a, d in (prior.get("accounts")
                                       or {}).items()
                     if a != args.account})
            except (OSError, ValueError):
                pass
        for pe in pending_accounts:
            try:
                agg["accounts"][pe.account] = _json.loads(
                    pe.pending_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                agg["accounts"][pe.account] = {"pending": []}
        agg_path.write_text(_json.dumps(agg, indent=2, sort_keys=True),
                            encoding="utf-8")
        print(f"\ntaxjson run: {len(pending_accounts)} account(s) need "
              f"corp-action elections before their books can build:",
              file=sys.stderr)
        for pe in pending_accounts:
            doc = agg["accounts"].get(pe.account) or {}
            for ev in doc.get("pending", []):
                opts = "|".join(o["election"] for o in ev.get("options",
                                                             []))
                print(f"  taxjson elect {pe.account} --set "
                      f"{ev['event_id']}=<{opts}>", file=sys.stderr)
        print(f"Details (options, descriptions, required hints): "
              f"{agg_path}\nResolve each with `taxjson elect ... --set` "
              f"(add --hint KEY=VALUE where required), or run "
              f"`taxjson run` at a terminal to be prompted; then re-run.",
              file=sys.stderr)
        raise SystemExit(3)
    _agg_path = cache / "pending_elections.json"
    if args.account and _agg_path.exists():
        # This run only proved args.account resolved — drop just its
        # entry; other accounts' pending elections are untouched
        # (REVIEW #33: the unconditional unlink made `elect --pending`
        # claim everything was resolved).
        import json as _json
        try:
            _doc = _json.loads(_agg_path.read_text(encoding="utf-8"))
            _accts = _doc.get("accounts") or {}
            _accts.pop(args.account, None)
            _accts = {a: d for a, d in _accts.items()
                      if (d or {}).get("pending")}
            if _accts:
                _doc["accounts"] = _accts
                _agg_path.write_text(
                    _json.dumps(_doc, indent=2, sort_keys=True),
                    encoding="utf-8")
            else:
                _agg_path.unlink()
        except (OSError, ValueError):
            _agg_path.unlink(missing_ok=True)
    else:
        _agg_path.unlink(missing_ok=True)

    if not args.account:
        equity_gains = [o["gains"] for _, o, is_crypto in taxable_outputs if not is_crypto]
        stage_exports(equity_gains, reports_dir)

        all_gains = ([o["gains"] for _, o in sheltered_outputs] +
                     [_wash_preferred_gains(o["gains"])
                      for _, o, _ in taxable_outputs])
        # Radar/lint bases: equity always; crypto too when the
        # jurisdiction's wash rule covers it (Canada s.54 identical
        # property — same condition as the second pass above).
        _crypto_wash_covered = _country(settings) not in ("us", "usa")
        taxable_equity_base = [
            o["base"] for _, o, is_crypto in taxable_outputs
            if not is_crypto or _crypto_wash_covered]
        stage_cross_reports(all_gains, taxable_equity_base, sheltered_base, reports_dir,
                            ticker_map_arg, phantoms=phantoms_arg,
                            country=_country(settings))
        # Overlap notes per blended group — the note says the blended
        # pass covers the symbol, so it must only name accounts a blend
        # actually spans (crypto blends only in Canada; equity and
        # crypto never blend with each other).
        _warn_cross_taxable_overlap(
            [(n, o["base"]) for n, o, c in taxable_outputs if not c],
            settings)
        if _crypto_blend:
            _warn_cross_taxable_overlap(
                [(n, o["base"]) for n, o, c in taxable_outputs if c],
                settings)
    stage_fees(cache, settings, rates, reports_dir)
    _warn_year_without_activity(
        settings.get("year"),
        [o["base"] for _, o in sheltered_outputs]
        + [o["base"] for _, o, _c in taxable_outputs])

    # Filed-year lock: recompute every closed year from the fresh books
    # and shout if a filed number moved (warn-only; --strict aborts).
    try:
        from taxjson.bin import taxjson_filed as _tf
        _snaps = _tf.list_snapshots(root)
        if _snaps and args.account:
            # A single-account run skipped the cross-account wash pass,
            # so the books the recompute would read are NOT the books
            # a full run produces: printing "OK" here right after
            # declaring the wash pass skipped was a false all-clear
            # that the next full run contradicted with DRIFTED
            # (2026-09 audit). Say so instead of checking.
            print("==> filed-year drift check")
            for _year, _ in _snaps:
                print(f"  filed {_year}: not checked (single-account "
                      f"run — wash pass skipped; run without --account)")
        elif _snaps:
            print("==> filed-year drift check")
            _check_filed_years(root, cache, settings,
                               strict=getattr(args, "strict", False))
    except SystemExit:
        raise
    except Exception as _e:              # advisory guard, never a crash
        print(f"taxjson: warning: filed-year check failed: {_e}",
              file=sys.stderr)

    if not args.account:
        # What these reports were built from (content hashes), so the
        # checklist's run-clean step sees a deleted input or a
        # corrected export copied with an old mtime (S067-07).
        try:
            from taxjson.lib.checklist import record_input_fingerprint
            record_input_fingerprint(root, cfg)
        except Exception as _e:          # advisory, never a crash
            print(f"taxjson: warning: could not record the input "
                  f"fingerprint: {_e}", file=sys.stderr)

    try:
        _fx_cash_after_run(root, cache, reports_dir)
    except Exception as _e:              # advisory guard, never a crash
        print(f"taxjson: warning: fx-cash report failed: {_e}",
              file=sys.stderr)

    # Filing-obligation reminder: a rollover elected in March is
    # forgotten by filing season, and the deferral is INVALID without
    # the paperwork. The option text said so at choose time; say it
    # again where it matters — at the end of every run.
    try:
        from taxjson.lib.corp_actions import (FILING_REQUIRED_ELECTIONS,
                                              Manifest)
        _year = str(settings.get("year", ""))
        _obligations = []
        for _name, _acfg in cfg.get("accounts", {}).items():
            if _acfg.get("type", "sheltered") != "taxable":
                continue          # no gain to defer inside a registered
                                  # plan, so there is nothing to file —
                                  # the election only kept the books
                                  # consistent
            _mp = _manifest_path_for(inputs_dir / _name, cache, _name)
            if not _mp.exists():
                continue
            try:
                _recs = list(Manifest.load(_mp).records.values())
            except (OSError, ValueError, AttributeError) as _e:
                # One unreadable manifest used to discard every
                # account's reminder in silence (S038-23): name it and
                # keep going.
                print(f"taxjson: warning: could not read {_mp} ({_e}) — "
                      f"its elections are not checked for a FILING "
                      f"REQUIRED reminder; fix the file (`taxjson elect "
                      f"{_name}` reads it too).", file=sys.stderr)
                continue
            for _rec in _recs:
                _todo = FILING_REQUIRED_ELECTIONS.get(_rec.election)
                if not _todo:
                    continue
                _sum = _rec.summary or ""
                if _year and _sum[:4].isdigit() and _sum[:4] != _year:
                    continue          # election from another tax year
                _obligations.append(f"  {_name}: {_sum or _rec.event_id}"
                                    f"\n    -> {_todo}")
        if _obligations:
            print(f"\n  ! FILING REQUIRED for {len(_obligations)} "
                  f"election(s) — the deferral is only valid with the "
                  f"paperwork:", file=sys.stderr)
            for _o in _obligations:
                print(_o, file=sys.stderr)
    except Exception:
        pass                          # reminder must never break a run

    if not args.account:
        _code_stamp.write_text(_code_fp + "\n", encoding="utf-8")
    print(f"\nDone. Reports in {reports_dir}/")

    # Broker-positions cross-check, when taxjson.toml declares any
    # account's `holdings` files. A WARNING, never a failing exit:
    # same-day trades that haven't reached the CSVs yet differ
    # routinely, and a hard failure there would teach the user to
    # ignore it. Only a self-vs-external compare catches a stranded
    # position that every internal report agrees on (the FFN phantom
    # shares, the DFDV option class split).
    if not args.account and any((_a or {}).get("holdings")
                                for _a in cfg.get("accounts", {}).values()):
        print("\n==> holdings sanity (taxjson.toml `holdings`)")
        _san_notes = _sanity_items_from_config(cfg.get("accounts", {}),
                                               root)[1]
        for _n in _san_notes:
            # One unreadable holdings file drops that account from the
            # compare while sanity itself still exits 0 — say so here,
            # or the run reads as fully checked (2026-09 audit R1-324).
            print(f"  !! holdings check incomplete: {_n}",
                  file=sys.stderr)
        try:
            cmd_sanity(argparse.Namespace(dir=str(root), items=[],
                                          tolerance=None, json=False))
        except SystemExit as _e:
            if isinstance(_e.code, str):
                # A message exit is a CONFIG problem (missing holdings
                # file, unknown account, malformed .toml) — not a
                # position difference (2026-09 CLI audit B13).
                print(f"  !! holdings check could not run: {_e.code}",
                      file=sys.stderr)
            elif _e.code:
                print("  !! positions differ from the broker holdings "
                      "files — same-day trades not yet in the CSVs are "
                      "the usual cause; anything else is a booking "
                      "problem (see `taxjson sanity`).",
                      file=sys.stderr)
        except Exception as _e:  # noqa: BLE001 — never break a run
            print(f"  holdings sanity skipped: {_e}", file=sys.stderr)
    if args.account:
        # A single-account run can't do cross-account wash detection or the
        # combined exports/cross reports — those are SKIPPED (previously they
        # ran on just this account's data and silently OVERWROTE the combined
        # exports/ccd/leaps/crosslistings with one-account truncations).
        # `<account>_wash.sum` and the combined reports keep whatever the
        # last FULL run wrote.
        print(
            f"\n  ! Single-account run ({args.account}): cross-account wash-sale "
            f"detection and the combined exports/cross reports were skipped — "
            f"they keep the last full run's contents. Run `taxjson run` "
            f"with no --account before filing.",
            file=sys.stderr,
        )


_TEMPLATE_CONFIG = """\
# taxjson configuration — https://github.com/taxjson/taxjson
#
# Keys are grouped and column-aligned so year-over-year projects diff
# cleanly:  diff ~/taxes/2025/taxjson.toml ~/taxes/2026/taxjson.toml
# Every commented key shows its default; uncomment a line to change it.

[settings]
year              = {year}
country           = "{country}"{country_pad}# canada | ca | usa | us
{province_line}base_currency     = "{base_currency}"{base_pad}# report currency (the country's); CAD: Bank of Canada rates, USD: Yahoo
source_currencies = ["{source_currency}"]{source_pad}# currencies you hold besides base_currency (FX rates fetched)
tax_date          = "{tax_date}"{tax_pad}# settle | trade (default: settle for canada — CRA; trade for usa — IRS)
# futures_settle = "trade"            # trade | next_day: IB futures & futures options settle on the TRADE date
#                                     #   (daily variation margin); next_day = the clearing premium date
# local_timezone = "America/Toronto"  # crypto UTC timestamps are dated in this zone (IANA name)
# prior_year_record = "../{prev_year}/filed/{prev_year}.json"
#                                     # last year's close-year record, checked by `taxjson handoff`

# fx_cash_gains = false               # true: end-of-run FX-on-cash report ({fx_rule})
{option_lines}
# One [accounts.NAME] section per folder under inputs/. The folder name
# is the account name. Required: type. Optional: transfers, crypto,
# plan, holdings, and — to pull activity straight from the broker with
# `taxjson fetch` — brokerage + account (Questrade) or query_id (IBKR):
#
#   [accounts.margin]
#   type      = "taxable"
#   brokerage = "questrade"      # questrade | ibkr_flex
#   account   = "12345678"       # Questrade account number
#   # query_id = "123456"        # ibkr_flex: the Flex query id instead
#   holdings  = ["~/broker/12345678_holdings.toml"]   # `taxjson sanity` pairs the account with these files

{account_sections}{instalments_section}"""

# Canada only. ITA s.49(1) written-option premium timing is a Canadian
# rule (the US engine always nets at close), so the US scaffold omits
# the block rather than showing switches that do nothing there.
_TEMPLATE_OPTION_LINES = """\

# Written-option premiums (ITA s.49(1)) — see `taxjson option-boundary`:
# option_premium_timing           = "grant"   # gain in the year WRITTEN; "close" nets the premium at the
#                                             #   closing transaction instead (the pre-s.49 behaviour = feature off)
option_grant_timing_since         = {year}      # contracts written before this year keep close timing. SET ONCE to
#                                             #   the first year you FILE under grant timing and keep it UNCHANGED
#                                             #   in every later year's project (do not bump it with `year`)
# option_buyback_loss_superficial = false     # true: strict s.54 reading — a buy-back loss is superficial when
#                                             #   identical options are bought within 30 days and still held
"""


# Canada only: instalments are a distinct regime (US filers use
# 1040-ES). Commented out — every value must be the user's own.
_TEMPLATE_INSTALMENTS = """
# Estimate inputs (`taxjson estimate`, and the instalments
# current-year basis) — used when the CLI flags aren't given:
#
# [estimate]
# other_income = 120000
# other_losses = 0
# deductions = 0            # RRSP 20800, FHSA, RPP ... (full under AMT)
# carrying_charges = 0      # line 22100 (50% under AMT)

# Tax instalments (`taxjson instalments`, and a summary inside
# `taxjson estimate`). Uncomment and fill in YOUR figures.
#
# [instalments]
# basis                = "current_year"   # current_year | prior_year | cra_reminder
# withheld             = 0                # tax withheld at source this year
# # Last two years' net tax owing, as CRA's instalment chart defines it:
# # lines 42000 + 42200 + 42800 (+ 43200) minus 43700 (tax deducted) and
# # the refundable credits — NOT line 48500, which also subtracts the
# # instalments you paid. Supply BOTH even on current_year: CRA
# # assesses interest on the least of the methods your figures support,
# # and they decide whether instalments are owed at all. Leaving a 0
# # here reads as "I owed nothing" and suppresses both.
# prior_year_net_tax   = 55000
# second_prior_net_tax = 41000
# prescribed_rate      = 0.07             # CRA's overdue-tax rate; or a dated
# # schedule, since CRA resets it quarterly and charges each day at the
# # rate then in force:
# # prescribed_rates = [
# #   { from = "2025-04-01", rate = 0.08 },
# #   { from = "2025-07-01", rate = 0.07 },
# # ]
# paid = [
#   { date = "2026-03-16", amount = 15000 },
#   { date = "2026-05-20", amount = 12000, note = "refund transferred" },
# ]
"""

# Country-shaped scaffold: account names, currencies, and tax-date basis
# in the generated config all follow the jurisdiction. Account order here
# is the order of the [accounts.*] sections and inputs/ folders.
_INIT_BY_COUNTRY = {
    "canada": {"base_currency": "CAD", "source_currency": "USD",
               "tax_date": "settle",
               "accounts": ("margin", "tfsa", "rrsp", "lira", "crypto")},
    "usa":    {"base_currency": "USD", "source_currency": "CAD",
               "tax_date": "trade",
               "accounts": ("margin", "crypto", "roth", "401k")},
}

# Comment column for the [settings] values: every value is padded to
# this width so the trailing comments line up (and so two projects'
# files differ only where their values differ).
_INIT_COMMENT_COL = 22


def _pad(value: str) -> str:
    """Spaces that carry a rendered value out to the comment column."""
    return " " * max(1, _INIT_COMMENT_COL - len(value))


def _render_init_config(country_canon: str,
                        year: Optional[int] = None
                        ) -> Tuple[str, Tuple[str, ...]]:
    """Fill _TEMPLATE_CONFIG for a jurisdiction. Returns (toml_text,
    account_names) — the same tuple drives the inputs/ folder scaffold so
    config sections and input dirs can't drift apart."""
    spec = _INIT_BY_COUNTRY[country_canon]
    sections: List[str] = []
    for name in spec["accounts"]:
        if name == "margin":
            sections.append(f"[accounts.{name}]\n"
                            f'type      = "taxable"          # taxable | sheltered\n'
                            f'# holdings  = ["~/broker/{name}_holdings.toml"]'
                            f"   # `taxjson sanity` reconciles against these\n")
        elif name == "crypto":
            sections.append(f"[accounts.{name}]\n"
                            f'type      = "taxable"\n'
                            f"crypto    = true               # splices "
                            f"fill-crypto-prices into the pipeline\n")
        else:
            sections.append(f"[accounts.{name}]\n"
                            f'type      = "sheltered"\n'
                            f"transfers = true               # keep TRANSFER "
                            f"rows (contributions/withdrawals)\n")
    is_ca = country_canon == "canada"
    yr = int(year) if year is not None else date_cls.today().year
    toml_text = _TEMPLATE_CONFIG.format(
        year=yr,
        country=country_canon,
        country_pad=_pad(f'"{country_canon}"'),
        base_currency=spec["base_currency"],
        base_pad=_pad(f'"{spec["base_currency"]}"'),
        source_currency=spec["source_currency"],
        source_pad=_pad(f'["{spec["source_currency"]}"]'),
        tax_date=spec["tax_date"],
        tax_pad=_pad(f'"{spec["tax_date"]}"'),
        prev_year=yr - 1,
        # `taxjson estimate` REQUIRES a province for canada, so the key
        # is present (commented) rather than discovered at first run.
        province_line=('# province          = "ON"'
                       '                # ON | BC | AB — `taxjson estimate` needs it\n'
                       if is_ca else ""),
        fx_rule=("s.39(1.1), $200 de minimis" if is_ca
                 else "§988, ordinary income"),
        option_lines=(_TEMPLATE_OPTION_LINES.format(year=yr) if is_ca
                      else ""),
        account_sections="\n".join(sections),
        instalments_section=_TEMPLATE_INSTALMENTS if is_ca else "",
    )
    return toml_text, spec["accounts"]


# A commented `ticker.map` stub. The pipeline runs fine without this file, so
# every rule is left commented — it's here to document the format and the four
# keywords so a user can populate it without hunting the README.
_TEMPLATE_TICKER_MAP = """\
# ticker.map — symbol rules for the taxjson pipeline. OPTIONAL: the pipeline
# runs without it. Each non-comment line is:  KEYWORD  from  [to]
#
#   GLOBAL  from to   Plain rename — the same security under a wrong/old
#                     ticker. Applied in EVERY stage (main + holdings).
#   TOBASE  from to   Currency-equivalent cross-listing (e.g. AEM.US / AEM.TO).
#                     Consolidated only in the to-base main pipeline; the
#                     holdings view keeps the two listings separate.
#   JOURNAL from to   A Norbert's Gambit pair (e.g. DLR.U.TO / DLR.TO):
#                     consolidated for ACB AND netted in the holdings view.
#   DELETE  from      Nuke that ticker's transactions (a pure artifact).
#   DISTINCT a b      Declares two look-alike listings are SEPARATE
#                     securities (a CDR vs its US underlying — UNH.TO
#                     is a fractional CAD-hedged receipt over UNH.US,
#                     never map it). Changes no symbol; silences the
#                     scan's MAP-GAP nag for the pair.
#
# Examples — uncomment and edit:
# GLOBAL   FB.US      META.US
# TOBASE   AEM.US     AEM.TO
# JOURNAL  DLR.U.TO   DLR.TO
# DELETE   CASH.US
# DISTINCT UNH.US     UNH.TO
"""

# Keep generated artifacts out of version control. `taxjson run` rebuilds all
# of these from inputs/ + taxjson.toml, so none of them need committing.
_TEMPLATE_GITIGNORE = """\
# Generated by `taxjson run` — rebuildable from inputs/ + taxjson.toml.
work/
reports/
export/
.DS_Store
"""

# Dropped into each empty inputs/<account>/ folder. Doubles as a placeholder
# so the otherwise-empty directory survives a git commit.
_INPUT_README = (
    "Drop this account's broker CSV exports in this folder.\n\n"
    "taxjson auto-detects the broker from each file's header (Interactive "
    "Brokers, RBC Direct, Questrade, Webull). Name crypto exports so the "
    "filename starts with `cb_` (Coinbase) or `kr_` (Kraken).\n"
)


def _manifest_path_for(acct_dir: Path, cache: Path, name: str) -> Path:
    """The corp-action elections manifest the pipeline uses for an account.
    Delegates to _resolve_manifest (read-only mode) so `taxjson elect` and
    the run pipeline can never disagree about which file is live — a
    legacy work/ manifest is migrated to inputs/ on first touch."""
    return _resolve_manifest(acct_dir, cache, name, create=False)


def _print_elections(name: str, manifest_path: Path,
                     country: Optional[str] = None) -> int:
    from taxjson.lib.corp_actions import Manifest, election_keys
    man = Manifest.load(manifest_path) if manifest_path.exists() else Manifest()
    if not man.records:
        print(f"  {name}: no elections recorded.")
        return 0
    known = election_keys(country) if country else None
    print(f"  {name}  ({manifest_path}):")
    for eid, r in sorted(man.records.items()):
        # A hand-typed key no rule knows: `taxjson run` refuses it, so
        # say so here too (audit S072-16 — it was listed as if valid).
        bad = (f"   <- UNKNOWN election for {country}: `taxjson run` "
               f"refuses it; fix with `taxjson elect {name} --redo "
               f"--event {eid}`"
               if known is not None and r.election
               and r.election not in known else "")
        print(f"    [{eid}] {r.election or '(none)'}{bad}")
        if r.summary:
            print(f"        {r.summary}")
        if r.hints:
            print(f"        hints: {r.hints}")
        if r.notes:
            print(f"        notes: {r.notes}")
    return len(man.records)


def _reextract_pending_entry(acct_dir: Path, name: str, country: str,
                             manifest_path: Path,
                             event_id: str) -> Optional[Dict[str, Any]]:
    """The pending-elections entry for ONE event (same shape as
    work/pending_elections.json), rebuilt from the account's broker
    CSVs in-process. That file only lists events a --no-input run
    deferred, so a re-election after `--reset --event ID` (or a --set
    typed before any run) has nothing to validate against without
    this. None when no extractor knows the id. Extractor chatter is
    swallowed: the run repeats it."""
    import contextlib
    import io as _io
    from taxjson.bin.taxjson_corp_actions import (EXTRACTORS, _pending_doc,
                                                  extract_events)
    from taxjson.lib.corp_actions import combine_broker_copies
    try:
        grouped = group_inputs(acct_dir)
    except SystemExit:
        return None
    sink = _io.StringIO()
    for broker, csvs in grouped.items():
        extractor = EXTRACTORS.get(broker)
        if extractor is None:
            continue
        # The same extraction the run does (all the group's files as
        # context, broker-account copies combined), so the id matches.
        try:
            with contextlib.redirect_stderr(sink), \
                    contextlib.redirect_stdout(sink):
                events = combine_broker_copies(
                    extract_events(extractor, csvs, name), stream=sink)
        except Exception:
            continue
        for ev in events:
            if ev.event_id == event_id:
                return _pending_doc([ev], manifest_path,
                                    country)["pending"][0]
    return None


def cmd_elect(args: argparse.Namespace) -> None:
    """View, clear, or redo corporate-action tax elections (taxable vs
    rollover, FMV/ACB hints) that `taxjson run` otherwise prompts for once
    and then reuses silently."""
    from taxjson.lib.corp_actions import Manifest
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    accounts = cfg.get("accounts", {})
    country = _country(cfg.get("settings", {}))
    cache = root / "work"
    inputs_dir = root / "inputs"

    if getattr(args, "pending", False):
        import json as _json
        agg_path = cache / "pending_elections.json"
        if not agg_path.exists():
            if getattr(args, "json", False):
                # Same shape as the pending document itself, so a
                # machine consumer never has to parse prose.
                _json_out({"schema_version": 1, "accounts": {}})
                return
            print("No pending elections (no --no-input run has deferred "
                  "any, or they've been resolved).")
            return
        doc = _json.loads(agg_path.read_text(encoding="utf-8"))
        if getattr(args, "json", False):
            print(_json.dumps(doc, indent=2, sort_keys=True))
            return
        from taxjson.lib.corp_actions import Manifest
        for acct, adoc in sorted((doc.get("accounts") or {}).items()):
            mpath = _manifest_path_for(inputs_dir / acct, cache, acct)
            man = (Manifest.load(mpath) if mpath.exists()
                   else Manifest())
            for ev in adoc.get("pending", []):
                head = f"{acct}: {ev['event_id']}  {ev.get('summary', '')}"
                rec = man.get(ev.get("event_id", ""))
                if rec is not None:
                    # The pending file only clears on the next
                    # successful run — without this marker, "did my
                    # --set take?" looked like NO and users re-set
                    # (or overwrote) their election.
                    head += (f"   [already elected: {rec.election} — "
                             f"re-run `taxjson run` to apply]")
                print(head)
                for o in ev.get("options", []):
                    hints = "".join(f" --hint {h['key']}=..."
                                    for h in o.get("hints", []))
                    print(f"  taxjson elect {acct} --set "
                          f"{ev['event_id']}={o['election']}{hints}")
                    print(f"      {o.get('description', '')}")
                    for h in o.get("hints", []):
                        if h.get("prompt"):
                            print(f"      {h['key']}: {h['prompt']}")
        return

    # No account → list every account's elections (read-only).
    if not args.account:
        if args.redo or args.reset:
            _die("--redo/--reset need an account, e.g. "
                     "`taxjson elect margin --redo`")
        if getattr(args, "set", None) or getattr(args, "hint", None):
            # Falling through to the read-only listing here looked like
            # success (exit 0) while saving NOTHING (REVIEW #7).
            _die("--set/--hint need an account, e.g. "
                     "`taxjson elect margin --set EVENT_ID=ELECTION`")
        if getattr(args, "json", False):
            # Machine listing of SAVED elections (--pending --json
            # already covers the unresolved ones) — previously --json
            # here silently printed the text listing.
            from taxjson.lib.corp_actions import Manifest
            doc: Dict[str, Any] = {}
            for name in accounts:
                mp = _manifest_path_for(inputs_dir / name, cache, name)
                if not mp.exists():
                    continue
                try:
                    man = Manifest.load(mp)
                except Exception as e:
                    doc[name] = {"error": str(e)}
                    continue
                doc[name] = {
                    eid: {"election": rec.election,
                          "summary": getattr(rec, "summary", "") or "",
                          "hints": dict(rec.hints or {}),
                          "notes": rec.notes or ""}
                    for eid, rec in sorted(man.records.items())}
            _json_out({"accounts": doc})
            return
        print("Corporate-action elections:")
        for name in accounts:
            _print_elections(name, _manifest_path_for(inputs_dir / name,
                                                      cache, name), country)
        print("\nRedo one: `taxjson elect <account> --redo` "
              "(add --event <id> for just one event).")
        return

    name = args.account
    if name not in accounts:
        _die(f"no [accounts.{name}] in taxjson.toml")
    acct_dir = inputs_dir / name
    manifest_path = _manifest_path_for(acct_dir, cache, name)
    if getattr(args, "hint", None) and not getattr(args, "set", None):
        _die("--hint only applies with --set EVENT_ID=ELECTION — "
             "nothing was saved.")
    if getattr(args, "json", False):
        if args.redo or args.reset or getattr(args, "set", None):
            _die("--json applies to the listings only (`taxjson elect "
                 "[ACCOUNT] --json`, `taxjson elect --pending --json`), "
                 "not to --set/--redo/--reset.")
        # One account's SAVED elections — same shape as the
        # all-accounts listing (it printed the text listing before).
        doc_one: Dict[str, Any] = {}
        if manifest_path.exists():
            try:
                _man = Manifest.load(manifest_path)
                doc_one[name] = {
                    eid: {"election": rec.election,
                          "summary": getattr(rec, "summary", "") or "",
                          "hints": dict(rec.hints or {}),
                          "notes": rec.notes or ""}
                    for eid, rec in sorted(_man.records.items())}
            except Exception as e:
                doc_one[name] = {"error": str(e)}
        _json_out({"accounts": doc_one})
        return

    # --set: non-interactive election writing (headless/CI bootstrap).
    if getattr(args, "set", None):
        from taxjson.lib.corp_actions import (ElectionRecord,
                                              RULES_BY_COUNTRY)
        if "=" not in args.set:
            sys.exit("taxjson elect --set expects EVENT_ID=ELECTION, e.g. "
                     "--set 20251022-ssl-rgld-51d7=rollover_s_85_1_5")
        event_id, election = args.set.split("=", 1)
        event_id, election = event_id.strip(), election.strip()
        if not event_id:
            # A ""-keyed record could never be targeted by --event
            # afterwards (REVIEW #34).
            sys.exit("taxjson elect --set: empty EVENT_ID (expected "
                     "EVENT_ID=ELECTION; list ids via `taxjson elect "
                     "--pending`)")
        # PER-EVENT validation on every --set: the country-wide set
        # accepted e.g. a spinoff election for a merger, which only
        # exploded at the NEXT run as a raw KeyError traceback (REVIEW
        # #6). The pending doc (written by the exit-3 run that told the
        # user this id) is the cheap source; when it doesn't list the
        # event — the documented `--reset --event ID` then `--set`
        # re-election, or a --set before any run — the event is
        # re-extracted from the account's CSVs instead.
        event_options = None
        event_summary = None
        event_hints_by_option: Dict[str, list] = {}
        pending_entry = None
        agg_path = cache / "pending_elections.json"
        if agg_path.exists():
            import json as _json
            try:
                _pdoc = _json.loads(agg_path.read_text(encoding="utf-8"))
                for _ev in (((_pdoc.get("accounts") or {}).get(name)
                             or {}).get("pending") or []):
                    if _ev.get("event_id") == event_id:
                        pending_entry = _ev
            except (OSError, ValueError):
                pass
        if pending_entry is None:
            pending_entry = _reextract_pending_entry(
                acct_dir, name, country, manifest_path, event_id)
        if pending_entry is not None:
            event_options = {"ignore"} | {
                o.get("election")
                for o in pending_entry.get("options", [])}
            event_summary = pending_entry.get("summary")
            for o in pending_entry.get("options", []):
                event_hints_by_option[o.get("election")] = (
                    o.get("hints") or [])
        if event_options is not None:
            if election not in event_options:
                _die(f"election {election!r} is not valid "
                         f"for event {event_id} — this event offers: "
                         f"{', '.join(sorted(event_options))}")
        else:
            known = {"ignore"}
            for rule in RULES_BY_COUNTRY.get(country, {}).values():
                known.update(k for k, _ in rule.options)
            if election not in known:
                _die(f"unknown election {election!r} for "
                         f"country={country}. Valid: "
                         f"{', '.join(sorted(known))}")
        hints = {}
        for h in (args.hint or []):
            if "=" not in h:
                sys.exit(f"taxjson elect --hint expects KEY=VALUE, got {h!r}")
            k, v = h.split("=", 1)
            try:
                hints[k.strip()] = float(v)
                from taxjson.lib.corp_actions import hint_value_problem
                _prob = hint_value_problem(k.strip(), hints[k.strip()])
                if _prob:
                    # A negative allocated ACB created basis from
                    # nothing and a negative FMV booked negative
                    # dividend income, saved at exit 0; nan/inf failed
                    # only on the next run (S039-00).
                    sys.exit(f"taxjson elect --hint: {_prob}")
            except ValueError:
                # Every hint consumer float()s its value — storing the
                # raw string reported "Election saved" and then crashed
                # the NEXT run with a ValueError traceback (REVIEW #5,
                # classic trigger: European decimal comma).
                sys.exit(f"taxjson elect --hint: {k.strip()}={v.strip()!r} "
                         f"is not a number (use a decimal POINT, e.g. "
                         f"{k.strip()}=12.50)")
        if event_options is not None:
            # The pending doc declares exactly which hints this
            # election needs — a rollover saved WITHOUT its
            # allocated_acb used to print "Election saved" and later
            # emit a $0-basis position with no warning; a typo'd hint
            # key was stored and ignored by every consumer.
            declared = {h.get("key")
                        for h in event_hints_by_option.get(election, [])}
            missing = declared - set(hints)
            unknown = set(hints) - declared
            if missing:
                specs = " ".join(f"--hint {k}=<value>"
                                 for k in sorted(missing))
                sys.exit(f"taxjson elect: {election} needs "
                         f"{', '.join(sorted(missing))} — add {specs} "
                         f"(see `taxjson elect --pending` for what "
                         f"each means)")
            if unknown:
                sys.exit(f"taxjson elect: {election} does not use "
                         f"hint(s) {', '.join(sorted(unknown))}"
                         + (f" (it takes: {', '.join(sorted(declared))})"
                            if declared else " (it takes none)"))
        man = Manifest.load(manifest_path) if manifest_path.exists() else Manifest()
        prior = man.get(event_id)
        # Best summary available: pending doc (the happy path — user
        # copied the id from `elect --pending`), else the prior
        # record's. Only a truly unknown id warns — the old note fired
        # on every VALIDATED save while a bogus id got the same mild
        # text (severity inverted).
        summary = (event_summary
                   or (prior.summary if prior else None)
                   or "(set non-interactively)")
        if prior is None and event_options is None:
            # Saving anyway left a junk record in the committed
            # manifest.json that no run ever reads (2026-09 CLI audit).
            _die(f"{event_id!r} matches no pending event, no saved "
                 f"election, and no corporate action in {name}'s "
                 f"inputs — nothing was saved. Check the id with "
                 f"`taxjson elect --pending` (after a `taxjson run "
                 f"--no-input`) or `taxjson elect {name}`.")
        man.set(ElectionRecord(event_id=event_id, summary=summary,
                               election=election,
                               notes="set via elect --set",
                               hints=hints))
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        man.save(manifest_path)
        print(f"Election saved: {event_id} = {election}"
              + (f" (hints: {hints})" if hints else "")
              + f" → {manifest_path}")
        if ("fmv_per_share" in hints and abs(hints["fmv_per_share"]) < 1e-12
                and election.startswith("taxable_")):
            # 0 is the documented "defer" value (R1-11): say what it books.
            print(f"taxjson elect: warning: fmv_per_share=0 books this "
                  f"{election} at $0 — no income and a $0 cost for the "
                  f"new shares. Every `taxjson run` and the checklist "
                  f"flag it until a value is set.", file=sys.stderr)
        from taxjson.lib.corp_actions import ALLOCATED_BASIS_HINT
        _ak = ALLOCATED_BASIS_HINT.get(election)
        if _ak and _ak in hints and abs(hints[_ak]) < 0.005:
            # The missing hint is refused; an explicit 0 was saved with
            # no word (audits S073-21, S074-04).
            print(f"taxjson elect: warning: {_ak}=0 moves NO cost to the "
                  f"spun-off shares — they book at $0 and the parent "
                  f"keeps all of it. Enter the allocated amount; every "
                  f"`taxjson run` and the checklist flag it until then.",
                  file=sys.stderr)
        return

    if not (args.redo or args.reset):
        print("Corporate-action elections:")
        _print_elections(name, manifest_path, country)
        print(f"\nRedo all: `taxjson elect {name} --redo`  |  "
              f"one: add `--event <id>`  |  just clear: `--reset`")
        return

    # --redo / --reset: clear the chosen elections from the manifest.
    if args.redo and not sys.stdin.isatty():
        # The old path cleared FIRST and then crashed on the missing
        # TTY — the user's saved (non-rebuildable) elections were
        # already gone (REVIEW #4).
        _die("--redo re-prompts interactively and needs a "
                 "terminal. Headless: `taxjson elect {0} --reset "
                 "[--event ID]` to clear, then `taxjson elect {0} --set "
                 "EVENT_ID=ELECTION` (ids/options: `taxjson elect "
                 "--pending` after a `run --no-input`).".format(name))
    manifest_backup = (manifest_path.read_bytes()
                       if manifest_path.exists() else None)
    man = Manifest.load(manifest_path) if manifest_path.exists() else Manifest()
    # `is not None`: --event '' (e.g. an unset shell variable) must NOT
    # silently widen to ALL elections (REVIEW #34 wiped everything).
    if args.event is not None:
        if args.event not in man.records:
            _die(f"no election '{args.event}' for account '{name}'. "
                     f"Run `taxjson elect {name}` to list event ids.")
        targets = [args.event]
    else:
        targets = list(man.records)
    for eid in targets:
        man.records.pop(eid, None)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    man.save(manifest_path)
    print(f"Cleared {len(targets)} election(s) from {manifest_path}.")

    if args.reset:
        print(f"Next `taxjson run` will re-prompt for them.")
        return

    # --redo: re-prompt now by re-running corp-actions interactively.
    # Any failure or Ctrl-C mid-prompt RESTORES the pre-clear manifest —
    # elections are the one non-rebuildable user artifact (REVIEW #4).
    grouped = group_inputs(acct_dir)
    ran = False
    try:
        for broker, csvs in grouped.items():
            if broker not in CORP_ACTION_BROKERS:
                continue
            out = cache / f"{name}_{broker}_corp.json"
            cmd = _cmd("taxjson-corp-actions") + [
                "--account-name", name, "--country", country,
                "--brokerage", broker, "--manifest", str(manifest_path),
            ] + [str(p) for p in csvs]
            run_to_file(cmd, out, interactive=True)
            ran = True
    except (subprocess.CalledProcessError, KeyboardInterrupt):
        if manifest_backup is not None:
            manifest_path.write_bytes(manifest_backup)
        _die(f"--redo interrupted — previous elections for "
                 f"'{name}' were restored unchanged.")
    if not ran:
        print(f"No corp-action events to re-elect for '{name}'.")
    else:
        print(f"\nElections saved. Re-run `taxjson run` to recompute.")


def _tx_period_cutoff(period: str):
    """Oldest date to include for a look-back window: 30d / 6w / 3m / 1y,
    `mtd` / `ytd` (calendar month/year to date), or `all` for no lower bound
    (full history). Days and weeks are exact; months and years use calendar
    arithmetic. Year tokens (YYYY, tax_year) are NOT look-backs — the
    callers resolve them to that calendar year first (_period_keep,
    fees-sum); a lower-bound reading of them (the removed since-based
    commands' "through today") disagreed with every period command
    (S039-04)."""
    import re
    import calendar
    from datetime import date, timedelta
    tok = (period or "").strip().lower()
    if tok in ("all", "max"):
        # date.min.isoformat() == '0001-01-01', so every real row's
        # `d >= cutoff` compare passes — the whole history is included.
        return date.min
    if tok == "mtd":
        today = date.today()
        return date(today.year, today.month, 1)
    if tok == "ytd":
        return date(date.today().year, 1, 1)
    m = re.fullmatch(r"\s*(\d+)\s*([dwmy])\s*", (period or "").lower())
    if not m:
        _die(f"invalid time period {period!r} "
                 f"(use e.g. 30d, 6w, 3m, 1y, mtd, ytd, all)")
    n, unit = int(m.group(1)), m.group(2)
    today = date.today()
    # Magnitudes that walk past year 1 raised ValueError (and huge day
    # counts OverflowError) as raw tracebacks on EVERY period-aware
    # command — including the plausible typo "2026y" (REVIEW #32).
    # Anything reaching past all representable history means "all".
    _CAP_DAYS = (today - date.min).days
    if (unit == "d" and n >= _CAP_DAYS) or \
       (unit == "w" and n >= _CAP_DAYS // 7) or \
       (unit == "m" and n >= today.year * 12) or \
       (unit == "y" and n >= today.year):
        return date.min
    if unit == "d":
        return today - timedelta(days=n)
    if unit == "w":
        return today - timedelta(weeks=n)
    months = n if unit == "m" else 12 * n
    total = today.year * 12 + (today.month - 1) - months
    y, mo = divmod(total, 12)
    mo += 1
    return date(y, mo, min(today.day, calendar.monthrange(y, mo)[1]))


_TAX_YEAR_TOKENS = ("tax_year", "tax-year", "taxyear", "ty")

# A literal 4-digit tax year is a valid PERIOD token: `taxjson events 2025`
# scopes to calendar year 2025 (same shape the -sum roll-ups label
# "tax year N").
_YEAR_TOKEN_RE = re.compile(r"(19|20)\d{2}")

# One canonical help string for every PERIOD positional (AUDIT-2026-07-ui A1).
_PERIOD_HELP = ("Window: 30d, 6w, 3m, 1y, mtd, ytd, all, a tax year "
                "(2025), or tax_year/ty for the config year "
                "(default: tax year)")


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
    settings = _soft_settings(root)
    td = str(settings.get("tax_date") or "").strip().lower()
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


def _period_keep(period: str, root: Path):
    """Resolve a period token to `(keep(date_str) -> bool, scope_label)`.
    `tax_year` (or `ty`) binds the window to the config tax year; a literal
    YYYY is that calendar year; `all` is the whole history; anything else is
    a look-back window (30d/6w/3m/1y)."""
    tok = (period or "").strip().lower()
    if _YEAR_TOKEN_RE.fullmatch(tok):
        return _year_keep(tok), f"tax year {tok}"
    if tok in _TAX_YEAR_TOKENS:
        year = _soft_settings(root).get("year")
        if not year:
            _die("'tax_year' needs [settings] year in taxjson.toml "
                     "(or give an explicit window like 1y).")
        ys = str(year)
        return _year_keep(ys), f"tax year {year}"
    cutoff = _tx_period_cutoff(period).isoformat()      # handles all/max + errors
    if tok in ("all", "max"):
        label = "all history"
    elif tok in ("mtd", "ytd"):
        label = f"{tok.upper()} (since {cutoff})"
    else:
        label = f"last {period}"
    return (lambda d: d >= cutoff), label


# Native (pre-base) per-account transaction file, in preference order: the raw
# equity merge, then the crypto native (post fill-crypto-prices), then sorted.
# These carry NATIVE currency with NO TOBASE / currency-to-base mapping —
# exactly what `taxjson events` shows.
_NATIVE_TX_SUFFIXES = ("_raw.json", "_filled.json", "_sorted.json")


def _native_tx_file(cache: Path, account: str) -> Optional[Path]:
    for suf in _NATIVE_TX_SUFFIXES:
        p = cache / f"{account}{suf}"
        if p.exists():
            return p
    return None


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


def _tx_display_line(tx: dict, settle: bool = False) -> Optional[str]:
    """Human-readable line for `taxjson events`. Money amounts (total,
    fee, dividend/tax/interest/adjust amount) are shown to 2 decimals; quantity
    and per-share price keep their significant digits (a 0.0375 dividend rate
    or a 0.25178314 crypto qty must not be rounded away). Mirrors the .tt field
    layout but is a DISPLAY formatter — distinct from tx_to_tt_line, which
    keeps full precision for round-trippable .tt output. Returns None for
    actions with no representation. `settle=True` writes the SETTLEMENT
    date (a .tt line's single date on a settle-basis project): the
    single-account view is round-trippable taxtext, and pasting its
    trade-dated Dec-31 sale into next year's .tt dropped it from both
    years (S002-03)."""
    money = fmt_money               # dollar amount → 2 decimals (shared)

    def sig(x):                         # qty / price → full precision
        # repr(): EXACT round-trip. The old :.8f truncated a 9-decimal
        # crypto qty, so re-importing the view produced a different
        # transaction id — dedup missed and every trade double-booked
        # (REVIEW #19).
        s = repr(float(x or 0))
        if s.endswith(".0"):
            s = s[:-2]
        return "0" if s in ("", "-0") else s

    action = tx.get("action", "")
    date = tx.get("date", "")
    if settle and tx.get("date_settle"):
        date = tx["date_settle"]
    time = tx.get("time", "09:30:00")
    sym = tx.get("symbol", "")
    # "?" like every other view (trades-sum, fees): a CAD label on a
    # currency-less row misstated it in a non-CAD project, and the line
    # is re-importable .tt (R1-287).
    cur = tx.get("currency") or "?"
    qty, price = tx.get("quantity"), tx.get("price")
    net = float(tx.get("net_amount") or 0.0)
    gross = float(tx.get("gross_amount") or 0.0) or net
    # fee + commission: parsers split the charge across both keys
    # (Questrade uses `commission`), so reading only `fee` printed
    # 0.00 on every row — and the "round-trippable" single-account
    # view silently erased the deductible fees on re-import
    # (REVIEW #36/#18). Same rule as _tx_fee (fees/trades-sum).
    fee = (float(tx.get("fee") or 0.0)
           + float(tx.get("commission") or 0.0))

    # SIGNED total/fee, exactly as convert_tt.tx_to_tt_line emits them:
    # abs() printed every fee rebate as a charge and a penny close's
    # negative proceeds as positive, so the "round-trippable" view
    # flipped both on re-import (audit S039-11).
    if action in ("BUYSELL", "ASSIGN"):
        return f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} {money(net)} {money(fee)}"
    if action == "TRANSFER":
        return f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} {money(net)}"
    if action == "SPLIT":
        return f"SPLIT {date} {time} {sym} {tx.get('symbol_new') or sym} {sig(qty)}"
    if action in ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX"):
        # Signed so a reversal row reads as negative instead of masquerading
        # as more income/tax.
        return f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} {money(gross)}"
    if action in ("INTEREST", "FEE"):
        return f"{action} {date} {time} {cur} {money(net)}"
    if action in ("ADJUST", "DISALLOW"):
        return f"{action} {date} {time} {sym} {cur} {money(net)}"
    return None


# Back-compat aliases: the table helpers were promoted to
# taxjson.lib.report_model (shared with the standalone report tools); the
# 20+ call sites below keep their historical names.
_align_columns = align_columns


def _print_report_table(rows: List[str], padding: str = "   ",
                        rule_before_last: bool = False) -> None:
    """print()s taxjson.lib.report_model.format_report_table — see there."""
    for line in format_report_table(rows, padding=padding,
                                    rule_before_last=rule_before_last):
        print(line)


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _json_out(doc: Dict[str, Any]) -> None:
    """Machine output: pure JSON on stdout (notes/warnings stay on
    stderr) — the GUI's data source; never screen-scraped text.

    STRICT JSON: non-finite floats are emitted as null. json.dumps'
    default allow_nan=True wrote bare NaN/Infinity tokens, which are
    invalid JSON — node's JSON.parse throws and jq silently corrupts
    them (REVIEW #41)."""
    import json
    import math

    def _clean(v):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_clean(x) for x in v]
        return v

    print(json.dumps(_clean(doc), indent=2, sort_keys=True, default=str))


# ---- Instrument-class filters (trades / gains) -----------------------
# `--options/--equities/--futures/--puts/--calls` narrow a view to
# particular instrument kinds. Multiple flags OR together; no flag means
# "all instruments" (the historical default). A futures OPTION (F: prefix
# + OCC contract block) is genuinely both a future and an option, so it
# matches `--futures` AND `--options`/`--puts`/`--calls`.
_INSTRUMENT_FLAGS = ("options", "equities", "futures", "puts", "calls")


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


def _instrument_tags(symbol: str) -> set:
    """The instrument classes a symbol belongs to. A symbol can carry
    several tags: a futures option is `futures` + `options` + `calls`|
    `puts`; a plain equity is just `equities`."""
    from taxjson.lib.core import is_option_symbol, parse_option_right
    from taxjson.lib.ticker_map import is_future_ticker
    sym = symbol or ""
    tags = set()
    opt = is_option_symbol(sym)
    fut = is_future_ticker(sym)
    if opt:
        tags.add("options")
        right = parse_option_right(sym)
        if right == "C":
            tags.add("calls")
        elif right == "P":
            tags.add("puts")
    if fut:
        tags.add("futures")
    if not opt and not fut:
        tags.add("equities")
    return tags


def _instrument_filter(args: argparse.Namespace):
    """A `symbol -> bool` predicate built from the --options/--equities/…
    flags, or None when none were given (i.e. show every instrument)."""
    wanted = {f for f in _INSTRUMENT_FLAGS if getattr(args, f, False)}
    if not wanted:
        return None
    return lambda sym: bool(wanted & _instrument_tags(sym))


def _trade_total(tx: dict) -> float:
    """A trade row's money for the buy/sell totals, in the engine's pool
    terms (core.py _trade_money): a BUY's cost as a magnitude (parsers
    spell it either sign), a SELL's proceeds SIGNED — a penny close whose
    commission exceeds the gross really has negative proceeds, and abs()
    added it to TOTAL SELL (audit S039-11)."""
    net = float(tx.get("net_amount") or 0.0)
    return net if float(tx.get("quantity") or 0.0) < 0 else abs(net)


def _run_tx_view(args: argparse.Namespace, actions, label: str,
                 symbol_filter=None) -> None:
    """Shared engine for `transactions` / `dividends` / `buysell`: read the
    native (pre-base) per-account files, filter by look-back window and (if
    given) an action set, sort chronologically, print aligned taxtext."""
    import json
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, _scope, account = _view_window(args, root)

    if account:
        accounts = [account]
        if _native_tx_file(cache, account) is None:
            sys.exit(f"taxjson {label}: no native transaction file for account "
                     f"{account!r} in {cache} (run `taxjson run` first, or "
                     f"check the name).")
    else:
        accounts = _discover_tx_accounts(cache)
        if not accounts:
            sys.exit(f"taxjson {label}: no transaction files in {cache} "
                     f"(run `taxjson run` first).")

    rows = []
    bad_dates = 0
    # A "tax year N" window on a settle-basis project takes a trade by
    # its settlement date, as the books and Schedule 3 do (S039-18).
    _settle = _settle_basis(root)
    for acct in accounts:
        native = _native_tx_file(cache, acct)
        if native is None:
            print(f"note: no native transaction file for account {acct!r}; "
                  f"skipping.", file=sys.stderr)
            continue
        data = _load_json_or_die(native)
        for tx in data.get("transactions", []):
            if actions is not None and tx.get("action") not in actions:
                continue
            if symbol_filter is not None and not symbol_filter(tx.get("symbol") or ""):
                continue
            d = tx.get("date") or ""
            if not _ISO_DATE_RE.match(d):    # can't place it in the window
                bad_dates += 1
                continue
            _wd = (_gains_row_date(tx, keep, _settle)
                   if tx.get("action") in _TRADE_ACTIONS else d)
            if keep(_wd):
                rows.append((d, tx.get("time") or "", acct, tx))
    if label == "roc":
        for acct, tx in _dist_adjust_rows(cache, accounts, keep):
            rows.append((tx.get("date") or "", tx.get("time") or "",
                         acct, tx))

    # Chronological, oldest → latest (date, then time), across all accounts.
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    # A crypto account's DIVIDEND rows are staking rewards — ordinary
    # income, their own total, never under TOTAL DIVIDEND (S023-11).
    _crypto_accts = {n for n, c in (_soft_config(root).get("accounts")
                                    or {}).items()
                     if isinstance(c, dict) and c.get("crypto")}

    if getattr(args, "json", False):
        # Payments in lieu are their own total, as in `sum` and dil-sum
        # (one "dividend" bucket put them under TOTAL DIVIDEND, and the
        # PIL-only `dil` view labelled them dividends — S039-19, S041-07).
        jb: Dict[str, Dict[str, float]] = {"buy": {}, "sell": {},
                                           "dividend": {},
                                           "dividend_in_lieu": {},
                                           "staking": {}}
        for _d, _t, acct, tx in rows:
            cur = tx.get("currency") or "?"
            act = tx.get("action")
            if act in ("BUYSELL", "ASSIGN"):
                q = float(tx.get("quantity") or 0.0)
                amt = _trade_total(tx)
                bucket = "buy" if q > 0 else "sell" if q < 0 else None
                if bucket:
                    jb[bucket][cur] = jb[bucket].get(cur, 0.0) + amt
            elif act in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                amt = (float(tx.get("gross_amount") or 0.0)
                       or float(tx.get("net_amount") or 0.0))
                _k = ("dividend_in_lieu" if act != "DIVIDEND"
                      else "staking" if acct in _crypto_accts
                      else "dividend")
                jb[_k][cur] = jb[_k].get(cur, 0.0) + amt
        _json_out({"rows": [dict(tx, account=acct)
                            for _d, _t, acct, tx in rows],
                   "totals": {k: v for k, v in jb.items() if v},
                   "bad_dates": bad_dates})
        return

    # A single named account prints pure taxtext (round-trippable); the
    # all-accounts view prefixes each line with the account so the merged
    # chronological list stays legible.
    prefix = len(accounts) != 1
    # The single-account (round-trippable) view writes each line's
    # SETTLEMENT date on a settle-basis project — what a .tt line's one
    # date means there (README "Importing manual cost basis").
    _st = _soft_settings(root)
    settle_dates = (not prefix and _tax_date(_st) == "settle")
    skipped = 0
    out_lines = []
    buys: Dict[str, float] = {}
    sells: Dict[str, float] = {}
    divs: Dict[str, float] = {}
    pils: Dict[str, float] = {}
    stake: Dict[str, float] = {}
    for _d, _t, acct, tx in rows:
        line = _tx_display_line(tx, settle=settle_dates)
        if line is None:
            skipped += 1
            continue
        out_lines.append(f"{acct} {line}" if prefix else line)
        cur = tx.get("currency") or "?"
        act = tx.get("action")
        if act in ("BUYSELL", "ASSIGN"):
            amt = _trade_total(tx)
            q = float(tx.get("quantity") or 0.0)
            if q > 0:
                buys[cur] = buys.get(cur, 0.0) + amt
            elif q < 0:
                sells[cur] = sells.get(cur, 0.0) + amt
        elif act in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            # Signed: a broker reversal row (negative amount) must net
            # against the original posting rather than inflate the total.
            amt = (float(tx.get("gross_amount") or 0.0)
                   or float(tx.get("net_amount") or 0.0))
            _b = (pils if act != "DIVIDEND"
                  else stake if acct in _crypto_accts else divs)
            _b[cur] = _b.get(cur, 0.0) + amt
    if not out_lines:
        # roc/dil are the views most often legitimately empty — zero
        # bytes at rc 0 was indistinguishable from a mis-typed window
        # (REVIEW #37); every sibling prints an explicit empty state.
        print(f"(no {label} rows in this window)")
        return
    # numeric_right: quantities, per-share rates, and amounts line up
    # on their decimal points, same as the report tables. The rows
    # stay whitespace-tokenized taxtext — extra padding parses
    # identically when pasted into a .tt file.
    for aligned in _align_columns(out_lines, numeric_right=True):
        print(aligned)

    # Per-currency totals footer. Only the categories actually present print,
    # so `dividends` shows just the dividend total, `buysell` the buy/sell
    # totals, and `transactions` all three.
    def _fmt(d):
        return ", ".join(f"{v:,.2f} {c}" for c, v in sorted(d.items()))
    footer = [(lbl, d) for lbl, d in
              (("TOTAL BUY:", buys), ("TOTAL SELL:", sells),
               ("TOTAL DIVIDEND:", divs),
               ("TOTAL DIVIDEND IN LIEU:", pils),
               ("TOTAL STAKING (crypto, ordinary income):", stake)) if d]
    if footer:
        print()
        _w = max(15, max(len(lbl) for lbl, _d in footer))
        for lbl, d in footer:
            print(f"{lbl:<{_w}} {_fmt(d)}")

    if skipped:
        print(f"note: skipped {skipped} row(s) with no taxtext representation "
              f"(e.g. OPENING_BALANCE).", file=sys.stderr)
    if bad_dates:
        print(f"taxjson: warning: {bad_dates} row(s) had a missing/unparseable date and "
              f"were excluded from the window.", file=sys.stderr)


def cmd_transactions(args: argparse.Namespace) -> None:      # `events` view
    _run_tx_view(args, actions=None, label="events")


_BOOK_VALUE_DESC_RE = re.compile(
    r"\bBOOK\s+VALUE:?\s*\$?\s*(\d+(?:,\d+)*(?:\.\d+)?)", re.IGNORECASE)


def cmd_transfers_view(args: argparse.Namespace) -> None:
    """`taxjson transfers`: the custody-evidence view. TRANSFER rows
    are deliberately NOT tax events — a taxable book's basis comes from
    its buy/sell history, so the parse stage keeps them OUT of the
    books (in a sidecar) and `events` stays buysell-only. But a depot
    flip, listing journal, broker migration, or crypto withdrawal/send
    is exactly what explains a confusing position — or an undeclared
    FMV disposition — later; this view shows that evidence: sidecar
    rows (taxable accounts) plus in-book TRANSFERs (sheltered accounts
    with `transfers = true`)."""
    import json
    root = Path(args.dir).resolve()
    cache = root / "work"
    want = (args.account or "").strip() or None
    cfg = _soft_config(root)
    if want and cfg and want not in (cfg.get("accounts") or {}):
        # A typo read "no transfer rows ... the broker reported none"
        # with rc 0, as if the account existed (S039-20).
        _die(f"no [accounts.{want}] in taxjson.toml — check the name.")

    def _fee(t: Dict[str, Any]) -> float:
        # A custody move's fee (a crypto withdrawal's network fee) is
        # in the sidecar; the view dropped it (S039-21).
        return float(t.get("fee") or 0) + float(t.get("commission") or 0)

    rows: List[Dict[str, Any]] = []
    for p in sorted(cache.glob("*_transfers.json")):
        try:
            doc = _read_work_doc(p)
            if not isinstance(doc.get("metadata") or {}, dict):
                raise ValueError('"metadata" must be a JSON object')
        except (OSError, ValueError) as e:
            print(f"taxjson: warning: could not read {p.name}: {e}",
                  file=sys.stderr)
            continue
        if (doc.get("metadata") or {}).get("kind") != "transfer_sidecar":
            continue
        acct = (doc.get("metadata") or {}).get("account") or ""
        for t in doc.get("transactions") or []:
            rows.append({"date": t.get("date") or "",
                         "account": t.get("account") or acct,
                         "symbol": t.get("symbol") or "",
                         "quantity": float(t.get("quantity") or 0),
                         "type": t.get("description") or "",
                         # RBC ships Value 0 on in-kind transfers; its
                         # "BOOK VALUE nnn" rides along as evidence.
                         "value": float(t.get("net_amount")
                                        or t.get("book_value") or 0),
                         "fee": _fee(t),
                         "currency": t.get("currency") or "",
                         "where": "sidecar"})
    for name in (cfg.get("accounts") or {}):
        p = cache / f"{name}_base.json"
        if not p.exists():
            continue
        try:
            doc = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # Same warning as the sidecar loop: the account's in-book
            # rows vanished with no word (S039-22).
            print(f"taxjson: warning: could not read {p.name}: {e} — "
                  f"its in-book TRANSFER rows are not shown.",
                  file=sys.stderr)
            continue
        for t in (doc.get("transactions") if isinstance(doc, dict)
                  else doc) or []:
            if t.get("action") != "TRANSFER":
                continue
            # The book row lost the parser's book_value evidence (not a
            # transaction field); RBC's description still carries it
            # ("... BOOK VALUE 16506.95") — shown as the sidecar row is
            # (audit S027-06).
            _value = float(t.get("net_amount") or 0)
            if not _value:
                _bv = _BOOK_VALUE_DESC_RE.search(t.get("description") or "")
                if _bv:
                    from taxjson.lib.brokerages.base import desc_number
                    _value = desc_number(_bv.group(1), strict=False) or 0.0
            rows.append({"date": t.get("date") or "",
                         "account": t.get("account") or name,
                         "symbol": t.get("symbol") or "",
                         "quantity": float(t.get("quantity") or 0),
                         "type": t.get("description") or "",
                         "value": _value,
                         "fee": _fee(t),
                         "currency": t.get("currency") or "",
                         "where": "book"})
    if want:
        rows = [r for r in rows if r["account"] == want]
    rows.sort(key=lambda r: (r["date"], r["account"], r["symbol"]))
    if getattr(args, "json", False):
        _json_out({"transfers": rows, "count": len(rows)})
        return
    print("CUSTODY TRANSFERS — evidence, not tax events (basis comes "
          "from buy/sell history; sidecar = kept out of the books, "
          "book = sheltered transfers kept in)")
    print()
    if not rows:
        print("No transfer rows found"
              + (f" for account {want!r}" if want else "")
              + " — re-run `taxjson run` after enabling the sidecar, "
                "or the broker reported none.")
        return
    out_lines = ["DATE ACCOUNT SYMBOL QTY TYPE VALUE FEE CUR WHERE"]
    for r in rows:
        # Type column: the transfer KIND (InterDepot/Internal/ATON…);
        # some brokers put a whole sentence here — cap it, the --json
        # view carries the full text.
        _ty = (r["type"] or "-").replace(" ", "_")
        if len(_ty) > 24:
            _ty = _ty[:21] + "..."
        out_lines.append(" ".join([
            r["date"], r["account"], r["symbol"],
            fmt_qty(r["quantity"]), _ty,
            fmt_money(r["value"]),
            (f"{r['fee']:g}" if r["fee"] else "-"),
            r["currency"], r["where"]]))
    _print_report_table(out_lines)
    print(f"\n{len(rows)} transfer row(s).")


def cmd_crypto_sends(args: argparse.Namespace) -> None:
    """`taxjson crypto-sends`: every outgoing crypto transfer that did
    not arrive on another of your exchanges, with your decision (self /
    gift / payment), its fair value and the ready .tt line; --set
    records a decision, --write regenerates inputs/<acct>/crypto_sends.tt."""
    from taxjson.lib import crypto_sends as CS
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    accts = CS.crypto_accounts(cfg)
    if not accts:
        _die("no crypto account in taxjson.toml (an [accounts.X] with "
             "`crypto = true`).")
    acct = (args.account or "").strip() or None
    if acct and acct not in accts:
        _die(f"{acct!r} is not a crypto account — crypto accounts: "
             f"{', '.join(accts)}.")
    sets = list(getattr(args, "set", None) or [])
    if (sets or args.write) and not acct:
        if len(accts) != 1:
            _die(f"--set/--write need an account: `taxjson crypto-sends "
                 f"<{'|'.join(accts)}> --set ID=gift`.")
        acct = accts[0]
    if (args.note is not None or args.price is not None) and not sets:
        _die("--note/--price only apply with --set ID=DECISION.")
    if args.json and (sets or args.write):
        _die("--json applies to the listing only.")
    cache = root / "work"
    if not any((cache / f"{a}_{b}_transfers.json").exists()
               for a in accts for b in ("kraken", "coinbase")):
        _die("no crypto transfer evidence in work/ — run `taxjson run` "
             "first (the parse keeps withdrawals/sends in "
             "work/<acct>_<exchange>_transfers.json).")
    try:
        if sets:
            light = CS.build_report(root, cfg, None, None, want=acct,
                                    with_pool=False)
            by_id = {s["id"]: s for s in light["accounts"][acct]["sends"]}
            for item in sets:
                if "=" not in item:
                    _die(f"--set expects ID=DECISION, got {item!r}.")
                sid, dec = (x.strip() for x in item.rsplit("=", 1))
                dec = dec.lower()
                if dec not in CS.DECISIONS:
                    _die(f"decision {dec!r} for {sid} — expected one of "
                         f"{', '.join(CS.DECISIONS)}.")
                _gift_no = command_country_problem(
                    "crypto-sends", _country(cfg.get("settings")), dec)
                if _gift_no:
                    # lib/country.COMMAND_COUNTRY["crypto-sends:gift"]
                    _die(f"{_gift_no}. A US donor's gift is not a sale (the "
                         f"recipient takes over your basis) — record it "
                         f"as `self` (no tax event) or, if you were paid, "
                         f"`payment`.")
                if sid not in by_id:
                    _die(f"no unmatched send {sid!r} in account {acct!r} — "
                         f"`taxjson crypto-sends {acct}` lists the ids.")
                if args.price is not None and not (args.price > 0):
                    _die("--price must be a positive value per coin.")
                CS.record_decision(Path(light["accounts"][acct]["manifest"]),
                                   sid, dec, note=args.note,
                                   price=args.price,
                                   summary=by_id[sid]["summary"])
                print(f"saved: {sid} = {dec}  "
                      f"(inputs/{acct}/{CS.MANIFEST_NAME})")
            if not args.write:
                print(f"Regenerate the .tt lines: `taxjson crypto-sends "
                      f"{acct} --write` (or just `taxjson run`).")
                return
        report = CS.build_report(root, cfg, _crypto_broker_files(root, cfg),
                                 CS.yahoo_usd_price(root), want=acct)
        if args.write:
            status, dups = _crypto_sends_tt(root, acct, report)
            n = len(CS.tt_entries(report["accounts"][acct])[0])
            print(f"inputs/{acct}/{CS.TT_NAME}: {status} "
                  f"({n} BUYSELL line(s)); `taxjson run` books it.")
            for w in _dup_warning(acct, dups):
                print(f"taxjson: WARNING: {w}", file=sys.stderr)
            return
    except ValueError as e:
        _die(str(e))
    if args.json:
        _json_out(report)
        return
    _print_crypto_sends(root, report)


def _print_crypto_sends(root: Path, report: Dict[str, Any]) -> None:
    from taxjson.lib import crypto_sends as CS
    base = report["base_currency"]
    _usa = _country(_soft_settings(root)) == "usa"
    if _usa:
        # A US donor's gift is not a disposition (the recipient takes
        # over the basis); `gift` is refused (COMMAND_COUNTRY).
        print("CRYPTO SENDS — outgoing transfers that did not arrive on "
              "another of your exchanges. A move to your own wallet is "
              "not a sale (self); a payment is a sale at fair market "
              "value. A gift is not a sale for a US donor: record it as "
              "self.")
    else:
        print("CRYPTO SENDS — outgoing transfers that did not arrive on "
              "another of your exchanges. A move to your own wallet is not a "
              "sale (self); a gift or a payment is a disposition at fair "
              "market value.")
    fx_by_year: Dict[str, float] = {}
    for acct, adoc in report["accounts"].items():
        sends = adoc["sends"]
        print(f"\n== {acct}: {len(sends)} unmatched send(s), "
              f"{adoc['undecided']} undecided; {adoc['matched']} matched "
              f"to an arrival (self-custody moves, `taxjson transfers`)")
        _short = adoc.get("network_fees") or []
        if _short:
            # R1-26: the coins lost in transit paid the network fee — a
            # disposition at fair value, written to crypto_sends.tt.
            print(f"  {len(_short)} matched send(s) arrived SHORT — the "
                  f"difference is the network fee paid in the coin, a "
                  f"sale at fair value booked in {CS.TT_NAME}:")
            for _s in _short:
                _fv = _s["fair_value"]
                _val = (f"{_fv['value']:,.2f} {base}  [{_fv['source']}]"
                        if _fv else "UNPRICED (lookup failed or offline)")
                print(f"    {_s['id']}: {_s['summary']}, "
                      f"{CS.fmt_qty(_s['quantity'])} {_s['symbol']} short "
                      f"= {_val}")
        for e in sends:
            dec = (e["decision"] or "PENDING").upper()
            ref = f"   ref {e['ref']}" if e["ref"] else ""
            print(f"\n{e['id']}   {dec}{ref}")
            fee = ("" if not e["fee_booked"] else
                   f"  (plus a {CS.fmt_qty(e['fee_booked'])} {e['symbol']} "
                   f"withdrawal fee — cash, not a sale)" if e["stable"] else
                   f"  ({CS.fmt_qty(e['fee_booked'])} {e['symbol']} network "
                   f"fee already booked from the ledger)")
            print(f"  {e['summary']}{fee}")
            fv = e["fair_value"]
            if fv:
                print(f"  fair value {CS.fmt_price(fv['price'])} {base}/"
                      f"{e['symbol']} = {fv['value']:,.2f} {base}  "
                      f"[{fv['source']}]")
            else:
                print(f"  fair value: UNPRICED (lookup failed or offline) "
                      f"— `--set {e['id']}=... --price P` gives it")
            if e["stable"]:
                fx = e["fx"]
                if base == "USD":
                    print("  stablecoin = US-dollar cash: no sale line and "
                          "no currency gain in a USD project")
                elif fx:
                    tag = ("FX gain" if e["decision"] in CS.DISPOSING
                           else "FX gain if gift/payment")
                    print(f"  stablecoin = US-dollar cash in the books: no "
                          f"sale line. {tag}: value {fx['value']:,.2f} - "
                          f"ACB {fx['acb']:,.2f} (USD pool average cost) = "
                          f"{fx['gain']:+,.2f} {base}")
                    if fx.get("superficial"):
                        print("  that loss is likely SUPERFICIAL (s.54): "
                              "USD/stablecoins acquired within 30 days "
                              "and still held — denied")
                    if fx.get("overdrawn"):
                        print("  (the pool was short: the ledgers do not "
                              "show where part of this USD came from)")
                    if e["decision"] in CS.DISPOSING:
                        y = e["date"][:4]
                        allowed = 0.0 if (fx["gain"] < 0 and
                                          fx.get("superficial")) \
                            else fx["gain"]
                        fx_by_year[y] = fx_by_year.get(y, 0.0) + allowed
                else:
                    print("  stablecoin = US-dollar cash in the books: no "
                          "sale line (FX gain unavailable: no USD rate)")
            elif e["tt"]:
                lead = ".tt" if e["decision"] in CS.DISPOSING else \
                    ".tt if gift/payment"
                print(f"  {lead}: {e['tt']}")
            if e["note"]:
                print(f"  note: {e['note']}")
            if e.get("refused"):
                print(f"  REFUSED: {e['refused']}")
        entries, unpriced = CS.tt_entries(adoc)
        tt = Path(adoc["tt_file"])
        want = (CS.render_tt(acct, entries, report["country"])
                if not unpriced else None)
        have = tt.read_text(encoding="utf-8") if tt.is_file() else None
        print()
        if unpriced:
            print(f"crypto_sends.tt: cannot be written — no fair value for "
                  f"{', '.join(e['id'] for e in unpriced)}.")
        elif want == have:
            print(f"inputs/{acct}/{CS.TT_NAME}: up to date "
                  f"({len(entries)} line(s))." if have else
                  "No gift/payment or network fee needs a sale line.")
        else:
            print(f"inputs/{acct}/{CS.TT_NAME}: OUT OF DATE — run "
                  f"`taxjson crypto-sends {acct} --write` (or `taxjson "
                  f"run`).")
        for w in _dup_warning(acct, CS.duplicate_lines(
                root / "inputs" / acct, entries)):
            print(f"WARNING: {w}")
        for sid in adoc["orphans"]:
            print(f"note: sends.json has a decision for {sid}, which is "
                  f"no longer an unmatched send (inputs changed?) — it is "
                  f"ignored.")
        if adoc["undecided"]:
            print(f"Decide: taxjson crypto-sends {acct} --set "
                  f"ID={'self|payment' if _usa else 'self|gift|payment'}"
                  f" [--note TEXT]   (then --write)")
    if fx_by_year:
        print("\nStablecoin gifts/payments, currency gain (superficial "
              "losses excluded): " + "; ".join(
                  f"{y} {g:+,.2f} {base}" for y, g in sorted(
                      fx_by_year.items())))
        print("  Stablecoins are US-dollar cash in these books, so this is "
              "a foreign-currency gain: ITA s.39(1.1) taxes only the "
              "year's NET currency gain beyond $200 — add it to "
              "`taxjson fx-cash` for the year.")
    pool = report.get("pool")
    if pool and pool.get("overdrafts"):
        print(f"\nnote: the USD/stablecoin pool went short "
              f"{pool['overdrafts']} time(s) — the ledgers do not show "
              f"where that USD came from (an earlier year's export?); "
              f"the shortfall is valued at the day's rate (no gain).")


def cmd_dividends(args: argparse.Namespace) -> None:         # `divs` view
    _run_tx_view(args, actions={"DIVIDEND", "DIVIDEND_IN_LIEU"}, label="divs")


def cmd_dil(args: argparse.Namespace) -> None:               # `dil` view
    _run_tx_view(args, actions={"DIVIDEND_IN_LIEU"}, label="dil")


def cmd_buysell(args: argparse.Namespace) -> None:           # `trades` view
    _run_tx_view(args, actions={"BUYSELL", "ASSIGN"}, label="trades",
                 symbol_filter=_instrument_filter(args))


def cmd_roc(args: argparse.Namespace) -> None:               # `roc` view
    _run_tx_view(args, actions={"ADJUST"}, label="roc")


# A LEAPS position, per the project definition: a LONG option BUY placed
# with more than 3 calendar months left to expiry. The contract set is
# identified over FULL history (not the viewing window), so a sale,
# assignment, or expiry inside the window still shows even when the
# qualifying buy happened before it.
_LEAPS_MONTHS = 3


def _add_months(iso_date: str, months: int) -> str:
    """ISO date `months` calendar months later, clamping the day into the
    target month (Jan 31 + 1mo -> Feb 28/29)."""
    import calendar
    from datetime import date as _date
    y, m, d = (int(x) for x in iso_date.split("-"))
    m += months
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return _date(y, m, min(d, calendar.monthrange(y, m)[1])).isoformat()


def _warn_gains_artifact_scope(files, period_token,
                               root: Optional[Path] = None) -> None:
    """The canonical <acct>_gains[_wash].json artifacts contain ONLY
    the config tax year's dispositions, but the PERIOD grammar accepts
    any window — `winners all` / `ccd-sum 2024` printed
    definite-looking answers silently missing every out-of-year row
    (2026-09 audit). Warn whenever the requested window could exceed
    the artifacts' year."""
    import json as _json
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
        return
    if (tok in _TAX_YEAR_TOKENS or tok == "") and root is not None:
        # The default / tax_year window IS [settings].year: artifacts
        # built for another year cannot answer it, and "No realized
        # dispositions in tax year 2025" from a 2026 build is a false
        # statement, not an empty result (audit S048-14).
        cfg_year = str(_soft_settings(root).get("year") or "")
        if cfg_year and cfg_year not in years:
            _die(f"work/ holds the gains of tax year "
                 f"{', '.join(sorted(years))} but [settings] year is "
                 f"{cfg_year} — run `taxjson run` to rebuild for "
                 f"{cfg_year} (or pass the year the books cover).")
    in_scope = (tok in _TAX_YEAR_TOKENS or tok == ""
                or (tok.isdigit() and tok in years))
    if not in_scope:
        print(f"taxjson: warning: the computed gains artifacts cover "
              f"tax year {', '.join(sorted(years))} only — rows "
              f"outside it are NOT in this report (the requested "
              f"window '{period_token}' may exceed that; "
              f"`taxjson gains` reads the full-history native "
              f"books).", file=sys.stderr)


def _leaps_scope_guard(root: Path, account: Optional[str], args) -> None:
    """leaps/leaps-sum read the same year-scoped gains artifacts as
    ccd-sum and winners, so they carry the same scope guard: `leaps-sum
    all` labelled 'all history' while holding one year (audit S048-11)."""
    from taxjson.lib.report_model import resolve_gains_files
    _warn_gains_artifact_scope(
        resolve_gains_files(root / "work", account or None),
        _period_token(args), root)


def _leaps_contracts(root: Path, account: Optional[str],
                     label: str) -> Dict[str, float]:
    """{option_symbol: full-history net open qty} for contracts with at
    least one qualifying LEAPS entry buy in the native books. Membership
    (`sym in result`) identifies the contract set; the value gives the
    TRUE still-open quantity regardless of any viewing window."""
    import json
    from taxjson.lib.core import is_option_symbol, parse_option_expiry
    cache = root / "work"
    if account:
        accounts = [account]
    else:
        accounts = _discover_tx_accounts(cache)
    from taxjson.lib.report_model import resolve_gains_files
    if not any(_native_tx_file(cache, a) is not None for a in accounts) \
            and not resolve_gains_files(cache, account or None):
        # "No LEAPS contracts found" (exit 0) before any run read as a
        # verdict about the books (2026-09 CLI audit B22).
        _die(f"no gains files in {cache}"
             + (f" for account {account!r}" if account else "")
             + " — run `taxjson run` first (LEAPS come from the built "
               "books).")
    leaps: set = set()
    qty_by_symbol: Dict[str, float] = {}
    # The native books carry the broker's listing (pre-TOBASE: BCE...US)
    # while the gains files carry the ticker.map spelling (BCE...TO);
    # without the rename a genuine LEAPS never matched a gains row and
    # vanished from both views (R1-237).
    _renames: Dict[str, str] = {}
    _tm = root / "ticker.map"
    if _tm.exists():
        try:
            from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                        merge_renames)
            _renames = merge_renames(_parse_map_file(_tm)[0], True)
        except Exception:                               # noqa: BLE001
            _renames = {}
    from taxjson.bin.taxjson_ticker_map import map_symbol as _map_sym
    for acct in accounts:
        native = _native_tx_file(cache, acct)
        if native is None:
            continue
        data = _load_json_or_die(native)
        # Per-ACCOUNT running balance, rows in date order: the old
        # single cross-account dict, iterated in account-name order,
        # made "buy against a short = buy-to-close" depend on the
        # ALPHABETICAL position of the account holding the short —
        # renaming an account changed the LEAPS report (2026-09
        # audit).
        acct_bal: Dict[str, float] = {}
        rows = sorted(data.get("transactions", []),
                      key=lambda t: (str(t.get("date") or ""),
                                     str(t.get("time") or "")))
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
                    qty_by_symbol[_mn] = (qty_by_symbol.get(_mn, 0.0)
                                          + qty_by_symbol.pop(_mo, 0.0)
                                          * _r)
                    if _mo in leaps:
                        leaps.add(_mn)
                continue
            if tx.get("action") not in ("BUYSELL", "ASSIGN"):
                continue
            sym = tx.get("symbol") or ""
            if not is_option_symbol(sym):
                continue
            qty = float(tx.get("quantity") or 0.0)
            prev_bal = acct_bal.get(sym, 0.0)
            acct_bal[sym] = prev_bal + qty
            msym = _map_sym(sym, _renames) if _renames else sym
            qty_by_symbol[msym] = qty_by_symbol.get(msym, 0.0) + qty
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
            if expiry > _add_months(d, _LEAPS_MONTHS):
                leaps.add(msym)
    return {sym: qty_by_symbol.get(sym, 0.0) for sym in leaps}


def cmd_leaps(args: argparse.Namespace) -> None:             # `leaps` view
    """Closed LEAPS positions over the window: one row per engine
    disposition of a qualifying contract (long option buy placed >3
    months to expiry), with lot-matched base-currency gains."""
    root = Path(args.dir).resolve()
    keep, scope, account = _view_window(args, root)
    _leaps_scope_guard(root, account, args)
    leaps = _leaps_contracts(root, account, "leaps")
    if not leaps:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account))
            return
        print("No LEAPS contracts found (long option buys placed more than "
              "3 months before expiry).")
        return
    entries, found, basis = _leaps_closed(root, account, leaps, keep)
    if not found:
        sys.exit("taxjson leaps: no gains files in work/ — run "
                 "`taxjson run` first (closed positions come from the "
                 "gains engine).")
    if not entries:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account, basis))
            return
        print(f"No closed LEAPS positions in {scope}.")
        return
    base_cur = _base_currency(root)

    money = fmt_money               # shared report-layer formatter

    entries.sort(key=lambda ae: (ae[1].get("date") or "",
                                 ae[1].get("symbol") or ""))
    _split = _scope_split(root, [(a, e.get("gain")) for a, e in entries])
    if getattr(args, "json", False):
        _json_out({"rows": [dict(e, account=acct) for acct, e in entries],
                   "total_gain": round(sum(float(e.get("gain") or 0)
                                           for _a, e in entries), 2),
                   "taxable_gain": _split["taxable"],
                   "sheltered_gain": _split["sheltered"],
                   "currency": base_cur, "basis": basis})
        return
    out_lines = ["DATE CONTRACT QTY PROCEEDS COST GAIN DAYS_HELD"]
    total = 0.0
    for _acct, e in entries:
        gain = float(e.get("gain") or 0.0)
        total += gain
        out_lines.append(" ".join([
            e.get("date") or "?", str(e.get("symbol") or "?"),
            f"{abs(float(e.get('qty') or 0.0)):g}",
            money(float(e.get("proceeds") or 0.0)),
            money(float(e.get("cost") or 0.0)),
            money(gain), str(int(e.get("days_held") or 0))]))
    print(f"CLOSED LEAPS POSITIONS — {scope} ({base_cur}; long option "
          f"buys placed >3 months to expiry)")
    print()
    _print_report_table(out_lines)
    print(f"\nTOTAL REALIZED GAIN: {money(total)} {base_cur}")
    _print_scope_split(_split, base_cur)
    print(f"Amounts are the engine's allowed figures — lot-matched, "
          f"basis: {basis}. Partial closes of a contract "
          f"appear as they are realized; still-open contracts are absent.")


def _scope_split(root: Path, pairs) -> Dict[str, float]:
    """{'taxable': x, 'sheltered': y} over (account, gain) pairs, by
    the config's account types (an unknown account counts as taxable,
    the side that must never be understated). The cross-account views
    added registered-account P&L into one headline total with no
    account column (R1-182)."""
    _acfg = _soft_config(root).get("accounts") or {}
    out = {"taxable": 0.0, "sheltered": 0.0}
    for acct, gain in pairs:
        scope = ("sheltered" if (_acfg.get(acct) or {}).get("type")
                 == "sheltered" else "taxable")
        out[scope] += float(gain or 0.0)
    return {k: round(v, 2) for k, v in out.items()}


def _print_scope_split(split: Dict[str, float], base_cur: str) -> None:
    if abs(split.get("sheltered", 0.0)) < 0.005:
        return
    money = fmt_money
    print(f"  TAXABLE accounts:   {money(split['taxable'])} {base_cur}")
    print(f"  SHELTERED accounts: {money(split['sheltered'])} {base_cur}"
          f"  (registered — not taxable events; the return uses the "
          f"taxable figure)")


def _leaps_empty_doc(root: Path, account: Optional[str],
                     basis: Optional[str] = None) -> Dict[str, Any]:
    """Empty-state leaps JSON with the SAME key set as the populated
    document — machine consumers keying on `basis` broke only on the
    empty case."""
    if basis is None:
        from taxjson.lib.report_model import (gains_basis_label,
                                              resolve_gains_files)
        resolved = resolve_gains_files(root / "work", account or None)
        basis = gains_basis_label(resolved) if resolved else "pre-wash"
    return {"rows": [], "total_gain": 0.0,
            "currency": _base_currency(root), "basis": basis}


def _leaps_closed(root: Path, account: Optional[str], leaps,
                  keep) -> Tuple[list, bool, str]:
    """[(account, gains_entry)] for in-window dispositions of LEAPS
    contracts, from the wash-adjusted gains files (falling back to the
    plain gains files) — the lot-matched, superficial-loss-adjusted,
    BASE-currency numbers a return reports. Tainted (phantom-basis)
    dispositions are excluded, mirroring every filing-facing consumer.
    Second element: whether any gains file was found at all; third the
    resolved files' basis label ("wash-adjusted" only when the wash
    files actually exist — the resolver silently falls back to the
    pre-wash gains, and the printed claim must follow it)."""
    import json
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    cache = root / "work"
    entries: list = []
    found = False
    resolved = resolve_gains_files(cache, account or None)
    basis = gains_basis_label(resolved)
    tainted = 0
    for acct, path in resolved.items():
        data = _load_json_or_die(path)
        found = True
        _settle = _settle_basis(root, data)
        # Phantom-basis LEAPS closes routed to manual reporting (or
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
        print(f"taxjson: warning: {tainted} LEAPS disposition(s) in this "
              f"window have phantom cost basis and need MANUAL reporting "
              f"— not shown here (see `taxjson sum`).", file=sys.stderr)
    return entries, found, basis


def cmd_leaps_sum(args: argparse.Namespace) -> None:
    """Realized-gain summary for closed LEAPS positions over a window
    (default: the tax year): per contract — quantity closed, proceeds,
    cost, gain, expiry — plus the total. A contract qualifies via its
    FULL-history entry buy (>3 months to expiry), so in-window exits of
    older entries are included."""
    from taxjson.lib.core import parse_option_expiry, parse_option_underlying
    root = Path(args.dir).resolve()
    # `leaps-sum margin` → the lone positional is an account, not a window.
    keep, scope, account = _view_window(args, root)
    _leaps_scope_guard(root, account, args)
    leaps = _leaps_contracts(root, account, "leaps-sum")
    if not leaps:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account))
            return
        print("No LEAPS contracts found (long option buys placed more than "
              "3 months before expiry).")
        return
    entries, found, basis = _leaps_closed(root, account, leaps, keep)
    if not found:
        sys.exit("taxjson leaps-sum: no gains files in work/ — run "
                 "`taxjson run` first (gains come from the engine).")
    if not entries:
        if getattr(args, "json", False):
            _json_out(_leaps_empty_doc(root, account, basis))
            return
        print(f"No closed LEAPS positions in {scope}.")
        return
    base_cur = _base_currency(root)

    money = fmt_money               # shared report-layer formatter

    _split = _scope_split(root, [(a, e.get("gain")) for a, e in entries])
    agg: Dict[str, Dict[str, float]] = {}
    for _acct, e in entries:
        sym = str(e.get("symbol") or "?")
        rec = agg.setdefault(sym, {"qty": 0.0, "proceeds": 0.0,
                                   "cost": 0.0, "gain": 0.0})
        rec["qty"] += abs(float(e.get("qty") or 0.0))
        rec["proceeds"] += float(e.get("proceeds") or 0.0)
        rec["cost"] += float(e.get("cost") or 0.0)
        rec["gain"] += float(e.get("gain") or 0.0)
    out_lines = ["CONTRACT EXPIRY QTY_CLOSED PROCEEDS COST GAIN"]
    total = 0.0
    def sort_key(item):
        sym, _rec = item
        return (parse_option_underlying(sym) or sym,
                parse_option_expiry(sym) or "", sym)

    if getattr(args, "json", False):
        _json_out({"rows": [dict(rec, contract=sym,
                                 expiry=parse_option_expiry(sym))
                            for sym, rec in sorted(agg.items(),
                                                   key=sort_key)],
                   "total_gain": _foot(r2["gain"]
                                       for r2 in agg.values()),
                   "taxable_gain": _split["taxable"],
                   "sheltered_gain": _split["sheltered"],
                   "currency": base_cur, "basis": basis})
        return
    for sym, rec in sorted(agg.items(), key=sort_key):
        total += round(rec["gain"], 2)
        out_lines.append(" ".join([
            sym, parse_option_expiry(sym) or "?", f"{rec['qty']:g}",
            money(rec["proceeds"]), money(rec["cost"]),
            money(rec["gain"])]))
    print(f"LEAPS REALIZED GAINS — {scope} ({base_cur}; long option buys "
          f"placed >3 months to expiry)")
    print()
    _print_report_table(out_lines)
    print(f"\nTOTAL REALIZED GAIN: {money(total)} {base_cur}")
    _print_scope_split(_split, base_cur)
    print(f"Engine-allowed amounts (lot-matched, basis: {basis}, "
          f"base currency); closed portions only — still-open "
          f"contracts carry no mark-to-market here.")


def cmd_ccd_sum(args: argparse.Namespace) -> None:
    """Covered-call (SHORT call) realized-gain summary over a window
    (default: the tax year): per underlying — contracts closed, premium
    collected (proceeds), buyback cost, gain — plus the total. Same
    canonical wash-adjusted gains basis as `leaps-sum`; the windowed
    query twin of the pipeline's cross-account `reports/ccd.rpt`."""
    import json
    from taxjson.lib.core import (is_option_symbol, parse_option_right,
                                  parse_option_underlying)
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, scope, account = _view_window(args, root)
    resolved = resolve_gains_files(cache, account or None)
    _warn_gains_artifact_scope(resolved, _period_token(args), root)
    if resolved and not account:
        _warn_accounts_without_books(root, resolved, "ccd-sum",
                                     "gains file")
    if not resolved:
        if account:
            sys.exit(f"taxjson ccd-sum: no gains for account {account!r} "
                     f"in {cache} (run `taxjson run` first, or check "
                     f"the name).")
        sys.exit(f"taxjson ccd-sum: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    basis = gains_basis_label(resolved)

    agg: Dict[str, Dict[str, float]] = {}
    _ccd_pairs: List[Tuple[str, float]] = []
    tainted_skipped = 0
    from taxjson.lib.ticker_map import class_share_aliases, underlying_of
    _docs = [(_a, _load_json_or_die(_f)) for _a, _f in resolved.items()]
    _aliases = class_share_aliases(
        t.get("symbol") for _a, _d in _docs
        for t in _d.get("transactions", []) or [])
    for acct, data in _docs:
        _settle = _settle_basis(root, data)
        # Routed phantom-basis rows (manual_reporting_required) are
        # tainted too — counted, never silent (audit S040-15 sibling).
        for t in data.get("manual_reporting_required") or []:
            sym = str(t.get("symbol") or "")
            d = _gains_row_date(t, keep, _settle)
            if (is_option_symbol(sym) and parse_option_right(sym) == "C"
                    and _ISO_DATE_RE.match(d) and keep(d)):
                tainted_skipped += 1
        for t in data.get("transactions", []):
            sym = str(t.get("symbol") or "")
            if not is_option_symbol(sym):
                continue
            if parse_option_right(sym) != "C":
                continue
            d = _gains_row_date(t, keep, _settle)
            if not _ISO_DATE_RE.match(d) or not keep(d):
                continue
            if t.get("tainted"):
                # Phantom-basis rows are excluded by every filing-
                # facing consumer; counting them here fabricated
                # premium totals (REVIEW #2 sibling).
                tainted_skipped += 1
                continue
            # SHORT (sold-to-open) legs only — covered-call premium.
            # Older gains files carry no `direction`; infer it the way
            # taxjson-ccd-gains does: a short's gain is cost-vs-proceeds
            # inverted.
            cost = float(t.get("cost", 0) or 0)
            proceeds = float(t.get("proceeds", 0) or 0)
            gain = float(t.get("gain", 0) or 0)
            direction = t.get("direction")
            inferred = direction is None
            if inferred:
                # Older gains files: shorts satisfy gain == cost -
                # proceeds (fields carry the legs swapped) — same
                # heuristic taxjson-ccd-gains uses. A BREAK-EVEN row
                # satisfies both orientations; classify it LONG
                # (excluded) rather than fabricate a covered-call
                # close with phantom premium/buyback totals (2026-09
                # audit).
                direction = ("SHORT"
                             if abs(gain - (cost - proceeds)) < 0.01
                             and abs(gain) >= 0.01
                             else "LONG")
            if direction != "SHORT":
                continue
            # PREMIUM/BUYBACK must satisfy gain = premium - buyback.
            # Rows with an explicit direction use the engines' signed
            # cash-flow convention (FUZZ #E: now identical in BOTH
            # engines): cost = -premium, proceeds = -buyback, so
            # gain == proceeds - cost holds direction-free. Legacy
            # inferred-short rows carry the legs swapped and positive.
            premium, buyback = ((cost, proceeds) if inferred
                                else (-cost, -proceeds))
            und = underlying_of(sym, _aliases)
            _ccd_pairs.append((acct, gain))
            rec = agg.setdefault(und, {"contracts": 0, "qty": 0.0,
                                       "proceeds": 0.0, "cost": 0.0,
                                       "gain": 0.0})
            # A grant-timing record recognises the premium at the
            # WRITE (s.49(1)); it is not a close. CLOSES/QTY count the
            # closing records only (audit S028-07).
            if not t.get("grant"):
                rec["contracts"] += 1
                rec["qty"] += abs(float(t.get("qty") or 0.0))
            rec["proceeds"] += premium
            rec["cost"] += buyback
            rec["gain"] += gain

    base_cur = _base_currency(root)
    if tainted_skipped:
        print(f"taxjson ccd-sum: warning: skipped {tainted_skipped} "
              f"tainted disposition(s) with phantom cost basis — "
              f"matches form-export/carryover/leaps.", file=sys.stderr)
    if getattr(args, "json", False):
        _json_out({"rows": [dict(rec, underlying=und,
                                 contracts=int(rec["contracts"]))
                            for und, rec in sorted(agg.items())],
                   "total_gain": _foot(r["gain"] for r in agg.values()),
                   "taxable_gain": _scope_split(root, _ccd_pairs)[
                       "taxable"],
                   "sheltered_gain": _scope_split(root, _ccd_pairs)[
                       "sheltered"],
                   "tainted_skipped": tainted_skipped,
                   "currency": base_cur, "scope": scope,
                   "basis": basis})
        return
    if not agg:
        print(f"No covered-call (short call) closes in {scope}.")
        return

    money = fmt_money
    out_lines = ["UNDERLYING CLOSES QTY PREMIUM BUYBACK GAIN"]
    total = 0.0
    for und, rec in sorted(agg.items()):
        total += round(rec["gain"], 2)
        out_lines.append(" ".join([
            und, str(int(rec["contracts"])), f"{rec['qty']:g}",
            money(rec["proceeds"]), money(rec["cost"]),
            money(rec["gain"])]))
    print(f"COVERED-CALL GAINS — {scope} ({base_cur}; SHORT call legs "
          f"only, engine-allowed amounts, basis: {basis})")
    print()
    _print_report_table(out_lines)
    print(f"\nTOTAL COVERED-CALL GAIN: {money(total)} {base_cur}")
    _print_scope_split(_scope_split(root, _ccd_pairs), base_cur)
    print("PREMIUM = proceeds of the sold calls; BUYBACK = cost to "
          "close (0 for expiries); assignments' share gains are NOT "
          "here — they land in the stock's own rows. CLOSES/QTY count "
          "closing records (a premium recognised at the write is not a "
          "close).")


def cmd_winners(args: argparse.Namespace) -> None:
    """`taxjson winners [PERIOD] [ACCOUNT] [--top N]`: per-ticker
    realized gains RANKED — biggest winners and losers over a window
    (default: the tax year), canonical wash-preferred basis, option
    contracts grouped under their underlying."""
    import json
    from taxjson.lib.core import parse_option_underlying
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, scope, account = _view_window(args, root)
    resolved = resolve_gains_files(cache, account or None)
    _warn_gains_artifact_scope(resolved, _period_token(args), root)
    if resolved and not account:
        _warn_accounts_without_books(root, resolved, "winners",
                                     "gains file")
    if not resolved:
        sys.exit(f"taxjson winners: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    basis = gains_basis_label(resolved)
    _INCOME = {"DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST",
               "FEE", "DISALLOW", "ADJUST"}
    agg: Dict[str, Dict[str, float]] = {}
    tainted_skipped = 0
    groups = _account_group_of(root)
    grp_gain = {"taxable": 0.0, "sheltered": 0.0}
    shel_accts = set()
    from taxjson.lib.ticker_map import class_share_aliases, underlying_of
    _docs = [(_a, _load_json_or_die(_f)) for _a, _f in resolved.items()]
    _aliases = class_share_aliases(
        t.get("symbol") for _a, _d in _docs
        for t in _d.get("transactions", []) or [])
    for _acct, data in _docs:
        _settle = _settle_basis(root, data)
        # Pipeline files ROUTE phantom-basis rows out of transactions[]
        # into manual_reporting_required: count them too, or the
        # "nothing is silent" warning never fired on real books (audit
        # S040-15).
        for t in data.get("manual_reporting_required") or []:
            d = _gains_row_date(t, keep, _settle)
            if _ISO_DATE_RE.match(d) and keep(d):
                tainted_skipped += 1
        for t in data.get("transactions", []):
            if t.get("action") in _INCOME:
                continue
            d = _gains_row_date(t, keep, _settle)
            if not _ISO_DATE_RE.match(d) or not keep(d):
                continue
            if t.get("tainted"):
                # Phantom OPENING_BALANCE basis fabricates gains — a
                # $0-cost row was ranked the portfolio's #2 winner and
                # made the total irreconcilable with form-export
                # (REVIEW #2). Excluded like every filing-facing
                # consumer, and COUNTED so nothing is silent.
                tainted_skipped += 1
                continue
            sym = str(t.get("symbol") or "?")
            und = underlying_of(sym, _aliases)
            rec = agg.setdefault(und, {"closes": 0, "proceeds": 0.0,
                                       "cost": 0.0, "gain": 0.0})
            if not t.get("grant"):          # a WRITE is not a close
                rec["closes"] += 1
            # Real-world orientation, as ccd-sum and form-export show
            # it: a SHORT row carries the engine's signed legs (cost =
            # -premium, proceeds = -buyback), so the columns read
            # negative and swapped (S040-14).
            if t.get("direction") == "SHORT":
                rec["proceeds"] -= float(t.get("cost") or 0.0)
                rec["cost"] -= float(t.get("proceeds") or 0.0)
            else:
                rec["proceeds"] += float(t.get("proceeds") or 0.0)
                rec["cost"] += float(t.get("cost") or 0.0)
            rec["gain"] += float(t.get("gain") or 0.0)
            _g = groups.get(_acct)
            if _g:
                grp_gain[_g] += float(t.get("gain") or 0.0)
                if _g == "sheltered":
                    shel_accts.add(_acct)
    ranked = sorted(agg.items(), key=lambda kv: -kv[1]["gain"])
    base_cur = _base_currency(root)
    if tainted_skipped:
        print(f"taxjson winners: warning: skipped {tainted_skipped} "
              f"tainted disposition(s) with phantom cost basis — "
              f"matches form-export/carryover/leaps.", file=sys.stderr)
    if getattr(args, "json", False):
        _json_out({"rows": [dict(rec, ticker=t,
                                 closes=int(rec["closes"]))
                            for t, rec in ranked],
                   "total_gain": _foot(r["gain"] for _t, r in ranked),
                   "total_gain_taxable": round(grp_gain["taxable"], 2),
                   "total_gain_sheltered": round(grp_gain["sheltered"], 2),
                   "sheltered_included": sorted(shel_accts),
                   "tainted_skipped": tainted_skipped,
                   "currency": base_cur, "scope": scope,
                   "basis": basis})
        return
    if not ranked:
        print(f"No realized dispositions in {scope}.")
        return
    money = fmt_money
    top = getattr(args, "top", None)
    if top is None:
        top = 10
    elif top < 1:
        # 0 was silently rewritten to the default and negatives
        # clamped to 1 — the header then advertised a number the user
        # never asked for (REVIEW #44).
        sys.exit(f"taxjson winners: --top must be >= 1, got {top}")
    head = ranked[:top]
    tail = [r for r in ranked[-top:] if r not in head]
    out_lines = ["TICKER CLOSES PROCEEDS COST GAIN"]

    def _row(t, rec):
        return " ".join([t, str(int(rec["closes"])),
                         money(rec["proceeds"]), money(rec["cost"]),
                         money(rec["gain"])])
    for t, rec in head:
        out_lines.append(_row(t, rec))
    hidden = len(ranked) - len(head) - len(tail)
    if hidden > 0:
        out_lines.append(f"... {hidden} ... ... ...")
    for t, rec in tail:
        out_lines.append(_row(t, rec))
    print(f"WINNERS & LOSERS — {scope} ({base_cur}, basis: {basis}; "
          f"options grouped under their underlying; "
          f"top/bottom {top})")
    print()
    _print_report_table(out_lines)
    total = _foot(r["gain"] for _t, r in ranked)
    if shel_accts:
        # A registered account's gains are not taxable events; the
        # headline alone overstated the owner's 2025 Schedule 3 gain by
        # 124% (audit S040-13). Same split and note `sum` prints.
        print(f"\nTAXABLE: {money(grp_gain['taxable'])} {base_cur}   "
              f"SHELTERED ({', '.join(sorted(shel_accts))} — not taxable "
              f"events): {money(grp_gain['sheltered'])} {base_cur}")
        print(f"{len(ranked)} ticker(s); TOTAL REALIZED GAIN (all "
              f"accounts): {money(total)} {base_cur}")
    else:
        print(f"\n{len(ranked)} ticker(s); TOTAL REALIZED GAIN: "
              f"{money(total)} {base_cur}")
    print("Realized dispositions only (engine-allowed amounts) — "
          "dividends/PIL are not included; see divs-sum.")


def _is_period(s: Optional[str]) -> bool:
    """True if `s` is a period token (30d/6w/3m/1y/mtd/ytd/all/YYYY/tax_year)."""
    s = (s or "").strip().lower()
    return (s in ("all", "max", "mtd", "ytd") or s in _TAX_YEAR_TOKENS
            or _YEAR_TOKEN_RE.fullmatch(s) is not None
            or re.fullmatch(r"\d+\s*[dwmy]", s) is not None)


def _period_token(args: argparse.Namespace) -> Optional[str]:
    """The PERIOD positional as _view_window resolves it: None when the
    lone positional was read as an account (`winners margin`) — passing
    the raw token warned that the window 'margin' may exceed the
    artifacts' year (S048-22)."""
    period, account = args.period, args.account
    if period and account is None and not _is_period(period):
        return None
    return period


def _view_window(args: argparse.Namespace, root: Path):
    """Resolve the shared optional `(period, account)` positionals of the
    query commands (events/divs/roc/leaps/trades/gains and the -sum
    roll-ups) into `(keep, scope, account)`. The lone positional is read as
    an account when it isn't a period token (a digit-leading non-token is
    validated as a window so the error names the real problem); with no
    period the scope defaults to the config tax year, else all history."""
    period, account = args.period, args.account
    if period and account is None and not _is_period(period):
        if period.strip()[:1].isdigit():
            _tx_period_cutoff(period)                # sys.exits: invalid period
        account, period = period, None
    if period:
        keep, scope = _period_keep(period, root)         # incl. `tax_year`
    else:
        year = _soft_settings(root).get("year")
        if year:
            ys = str(year)
            keep, scope = _year_keep(ys), f"tax year {year}"
        else:
            keep, scope = (lambda d: True), "all history"
    return keep, scope, account


def _tx_fee(tx: dict) -> float:
    """Total fee on a transaction. Parsers split this across `fee` and
    `commission` (Questrade uses the latter); sum both."""
    return float(tx.get("fee") or 0.0) + float(tx.get("commission") or 0.0)


def _view_income_rules(root: Path):
    """The project's lib/income_dating rules for the income views, or
    None outside a project (every row then keeps its pay date). A
    project whose settings the rules refuse stops the view."""
    settings = _soft_settings(root)
    if not settings.get("country"):
        return None
    try:
        return _income_rules(settings)
    except ValueError as e:
        _die(str(e))


def _warn_accounts_without_books(root: Path, have, label: str,
                                 what: str) -> None:
    """Warn naming each configured account that has inputs but no `what`
    in work/ (its stage failed): the all-accounts views discover
    accounts from work/, so such an account was simply absent — "No
    wash sales", "No dividends" — with rc 0 (S045-09)."""
    have = set(have)
    skipped = _accounts_skipped_for_no_inputs(root)
    missing = sorted(n for n in (_soft_config(root).get("accounts") or {})
                     if n not in have and n not in skipped
                     and _has_inputs(root, n))
    if missing:
        print(f"taxjson {label}: warning: no {what} for account(s) "
              f"{', '.join(missing)} (the last `taxjson run` did not "
              f"build it — did it fail?) — their rows are NOT in this "
              f"report.", file=sys.stderr)


def _collect_period_txs(args: argparse.Namespace, label: str, actions,
                        date_of=None, settle_trades: bool = False):
    """Read native per-account transactions over a window, for the period-aware
    summaries (`fees`/`divs-sum`/`trades-sum`). The lone positional is a period
    (30d/6w/…) when it looks like one, else an account name; with no period the
    scope defaults to the config tax year. Returns (rows, scope_label, bad,
    keep) — `keep(iso_date) -> bool` is the window predicate, for callers
    that must apply the SAME window to a second data source. `date_of`
    (row -> ISO date) picks the date a row is windowed on (the income
    views pass lib/income_dating's tax date); default: its `date`."""
    import json
    root = Path(args.dir).resolve()
    cache = root / "work"
    # `X-sum margin` → the lone positional is an account, not a window
    # (see _view_window).
    keep, scope, account = _view_window(args, root)

    if account:
        accounts = [account]
        if _native_tx_file(cache, account) is None:
            sys.exit(f"taxjson {label}: no native transaction file for account "
                     f"{account!r} in {cache} (run `taxjson run` first, or check "
                     f"the name).")
    else:
        accounts = _discover_tx_accounts(cache)
        if not accounts:
            sys.exit(f"taxjson {label}: no transaction files in {cache} "
                     f"(run `taxjson run` first).")
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
    for name, a in ((_soft_config(root).get("accounts") or {}).items()):
        t = str((a or {}).get("type") or "").strip().lower()
        if t in ("taxable", "sheltered"):
            out[name] = t
    return out


def _dist_adjust_rows(cache: Path, accounts, keep) -> List[Tuple[str, dict]]:
    """distributions.map ACB adjustments: `run` books them (type 'dist',
    id DIST-*) into <acct>_base.json only — the native books the
    transaction views read never see them, so `roc` / `roc-sum` said
    "No ACB adjustments" while the engine applied one (audit R1-163)."""
    out: List[Tuple[str, dict]] = []
    for acct in accounts:
        p = cache / f"{acct}_base.json"
        if not p.exists():
            continue
        doc = _load_json_or_die(p)
        for t in (doc.get("transactions") if isinstance(doc, dict)
                  else doc) or []:
            if (t.get("action") == "ADJUST"
                    and (t.get("type") or "").lower() == "dist"):
                d = t.get("date") or ""
                if _ISO_DATE_RE.match(d) and keep(d):
                    out.append((acct, t))
    return out


def _load_json_or_die(path: Path) -> Any:
    """Read a work/ artifact a query view needs, or stop naming it. The
    views used to warn and skip the file, then print a partial report —
    a smaller total, a missing account, 'No findings — clean scan.' —
    with exit 0 (audit S045-01, S042-05)."""
    try:
        return _read_work_doc(path)
    except (OSError, ValueError) as e:
        _die(f"could not read {path}: {e} — rerun `taxjson run` (this "
             f"view would otherwise leave that file's rows out).")


def _read_work_doc(path: Path) -> Dict[str, Any]:
    """A work/ artifact as a JSON object whose row lists are lists of
    objects (lib/json_input.read_work_doc). Raises OSError/ValueError
    — the views' existing handlers — never an AttributeError or a
    UnicodeDecodeError traceback for a corrupt file (S042-18)."""
    from taxjson.lib.json_input import read_work_doc
    return read_work_doc(path)


def _warn_bad_dates(bad: int) -> None:
    if bad:
        print(f"taxjson: warning: {bad} row(s) had a missing/unparseable date and were "
              f"excluded from the window.", file=sys.stderr)


def cmd_fees(args: argparse.Namespace) -> None:
    """Fees incurred over a window (default: the tax year) — one row per
    fee-bearing transaction plus a per-currency total. `PERIOD` is 30d/6w/3m/1y/
    all; omit it for the tax year. (For the by-brokerage roll-up, see
    `fees-sum`.)"""
    rows, scope, bad, _keep = _collect_period_txs(args, "fees", actions=None)

    money = fmt_money               # shared report-layer formatter

    out_lines = ["ACCOUNT DATE SYMBOL ACTION FEE CUR"]
    totals: Dict[str, float] = {}
    entries = []
    for acct, tx in rows:
        if tx.get("action") == "FEE":
            # Standalone fee rows (e.g. IB monthly/market-data fees) carry
            # the amount in net_amount: POSITIVE = charged, the convention
            # every parser (IB, Questrade, RBC, generic) and fx-cash use
            # (a rebate is negative and nets out). The old flip showed
            # every charge as a rebate (R1-124, R1-54, R1-269).
            fee = float(tx.get("net_amount") or 0.0)
        else:
            fee = _tx_fee(tx)
        if abs(fee) < 0.005:
            continue
        cur = tx.get("currency") or "?"
        entries.append((tx.get("date") or "", acct, tx, fee, cur))
        totals[cur] = totals.get(cur, 0.0) + fee
    entries.sort(key=lambda e: (e[0], e[1]))
    if getattr(args, "json", False):
        _warn_bad_dates(bad)
        _json_out({"rows": [{"account": acct, "date": date,
                             "symbol": tx.get("symbol"),
                             "action": tx.get("action"),
                             "fee": round(fee, 2), "currency": cur}
                            for date, acct, tx, fee, cur in entries],
                   "totals": {c: round(v, 2) for c, v in totals.items()},
                   "scope": scope})
        return
    for date, acct, tx, fee, cur in entries:
        out_lines.append(" ".join([acct, date or "-",
                                    str(tx.get("symbol") or "-"),
                                    str(tx.get("action") or "-"),
                                    money(fee), cur]))
    _warn_bad_dates(bad)
    if not entries:
        print(f"No fees incurred in {scope}.")
        return
    print(f"FEES — {scope}  (trade commissions+fees AND standalone "
          f"FEE rows; fees-sum is per-TRADE only and windows on "
          f"TRADE date, so totals differ by the FEE rows)")
    print()
    _print_report_table(out_lines)
    tot = ", ".join(f"{money(v)} {c}" for c, v in sorted(totals.items()))
    print(f"\n{len(entries)} fee(s); TOTAL FEES: {tot}")


def _foot(values) -> float:
    """A report total that FOOTS: the sum of the rows as printed
    (rounded to the cent), not the raw sum rounded once — the printed
    TOTAL differed from the column by cents (S037-11; `sum` already
    rounds per row)."""
    return round(sum(round(float(v or 0.0), 2) for v in values), 2)


def _foot_by_currency(pairs) -> Dict[str, float]:
    """{currency: _foot of that currency's row amounts}."""
    out: Dict[str, List[float]] = {}
    for cur, v in pairs:
        out.setdefault(cur, []).append(v)
    return {c: _foot(vs) for c, vs in out.items()}


def cmd_divs_sum(args: argparse.Namespace) -> None:
    """Dividend summary over a window (default: the tax year): total received
    per ticker, plus a per-currency grand total. `PERIOD` is 30d/6w/3m/1y/all;
    omit it for the tax year."""
    # DIVIDEND rows, plus (Canada) a payment in lieu that ITA s.260
    # deems a dividend — a Canadian dealer's payment on a Canadian
    # issuer's share, on the dealer's T5 box 24. Every other payment in
    # lieu is ordinary income, reported by `dil-sum` only (counting it
    # in both views put it in the slip tie-out, audit R1-272). Rows are
    # windowed on their tax date (lib/income_dating: a Canadian trust's
    # distribution by its record date).
    _rules = _view_income_rules(Path(args.dir).resolve())
    rows, scope, bad, _keep = _collect_period_txs(
        args, "divs-sum", actions={"DIVIDEND", "DIVIDEND_IN_LIEU"},
        date_of=_rules.income_date if _rules else None)
    rows = [(a, t) for a, t in rows
            if t.get("action") == "DIVIDEND"
            or (_rules is not None and _rules.pil_is_dividend(t))]
    n_pil_div = sum(1 for _a, t in rows
                    if t.get("action") == "DIVIDEND_IN_LIEU")

    money = fmt_money               # shared report-layer formatter
    groups = _account_group_of(Path(args.dir).resolve())

    agg: Dict[Tuple[str, str], float] = {}
    totals: Dict[str, float] = {}
    by_group: Dict[str, Dict[str, float]] = {"taxable": {},
                                             "sheltered": {}}
    shel_accts = set()
    # A crypto account's DIVIDEND rows are staking rewards — other
    # income, never on a T5/T3 — so they are totalled apart instead of
    # blending into the dividend total unmarked (audit S031-04).
    _crypto_accts = {n for n, a in ((_soft_config(Path(args.dir).resolve())
                                     .get("accounts") or {}).items())
                     if (a or {}).get("crypto") is True}
    staking: Dict[str, float] = {}
    staking_taxable: Dict[str, float] = {}
    staking_accts = set()
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        # Signed: reversal rows (negative) net against the original posting.
        amt = (float(tx.get("gross_amount") or 0.0)
               or float(tx.get("net_amount") or 0.0))
        key = (str(tx.get("symbol") or "?"), cur)
        agg[key] = agg.get(key, 0.0) + amt
        totals[cur] = totals.get(cur, 0.0) + amt
        if acct in _crypto_accts:
            staking[cur] = staking.get(cur, 0.0) + amt
            staking_accts.add(acct)
            if groups.get(acct) == "taxable":
                staking_taxable[cur] = staking_taxable.get(cur, 0.0) + amt
        g = groups.get(acct)
        if g:
            by_group[g][cur] = by_group[g].get(cur, 0.0) + amt
            if g == "sheltered":
                shel_accts.add(acct)
    _warn_bad_dates(bad)
    totals = _foot_by_currency((cur, amt) for (_s, cur), amt in agg.items())
    # The figure the T5/T3 slips add up to: taxable accounts' dividends
    # WITHOUT crypto staking (other income, never on a slip) — the
    # "compare with slips" line counted staking in (S048-24).
    slips = {c: v - staking_taxable.get(c, 0.0)
             for c, v in by_group["taxable"].items()}
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "dividend": round(amt, 2)}
                            for (sym, cur), amt in sorted(agg.items())],
                   "totals": {c: round(v, 2) for c, v in totals.items()},
                   "totals_taxable": {c: round(v, 2) for c, v
                                      in by_group["taxable"].items()},
                   "totals_sheltered": {c: round(v, 2) for c, v
                                        in by_group["sheltered"].items()},
                   "totals_slips": {c: round(v, 2) for c, v
                                    in slips.items()},
                   "sheltered_included": sorted(shel_accts),
                   "crypto_staking": {c: round(v, 2) for c, v
                                      in staking.items()},
                   "payments_in_lieu_as_dividends": n_pil_div,
                   "scope": scope})
        return
    if not agg:
        print(f"No dividends in {scope}.")
        return
    out_lines = ["SYMBOL CUR DIVIDEND"]
    for (sym, cur), amt in sorted(agg.items()):
        out_lines.append(" ".join([sym, cur, money(amt)]))
    if n_pil_div:
        print(f"DIVIDENDS — {scope}  (DIVIDEND rows and {n_pil_div} "
              f"payment(s) in lieu deemed dividends by ITA s.260; other "
              f"payments in lieu are in `dil-sum`)")
    else:
        print(f"DIVIDENDS — {scope}  (DIVIDEND rows; payments in lieu "
              f"are in `dil-sum`)")
    print()
    _print_report_table(out_lines)

    def _tot(d):
        return ", ".join(f"{money(v)} {c}" for c, v in sorted(d.items()))
    print()
    if shel_accts or staking_taxable:
        # Registered accounts get no T5/T3 and their dividends are not
        # income: the slip tie-out figure is the TAXABLE line (audit
        # S041-04), without crypto staking (S048-24).
        print(f"TAXABLE (compare with T5/T3 slips"
              + ("; crypto staking excluded" if staking_taxable else "")
              + f"): {_tot(slips) or '0.00'}")
        if shel_accts:
            print(f"SHELTERED ({', '.join(sorted(shel_accts))} — not "
                  f"taxable income, no slips): "
                  f"{_tot(by_group['sheltered'])}")
        print(f"TOTAL DIVIDEND (all accounts): {_tot(totals)}")
    else:
        print(f"TOTAL DIVIDEND: {_tot(totals)}")
    if staking_accts:
        print(f"  of which crypto staking rewards "
              f"({', '.join(sorted(staking_accts))} — other income, not "
              f"dividends; no T5/T3 slip): {_tot(staking)}")


def cmd_dil_sum(args: argparse.Namespace) -> None:
    """Payment-in-lieu summary over a window (default: the tax year):
    DIVIDEND_IN_LIEU rows only — payments received while shares were lent
    out (or short) over the ex-date. Split out from `divs-sum` because
    the tax treatment differs: a payment in lieu is ordinary income (no
    CA gross-up/credit; no US qualified rate) — except, in a Canada
    project, a Canadian dealer's payment on a Canadian issuer's share,
    which ITA s.260 deems a dividend (shown as such, and counted in
    `divs-sum`)."""
    _rules = _view_income_rules(Path(args.dir).resolve())
    rows, scope, bad, _keep = _collect_period_txs(
        args, "dil-sum", actions={"DIVIDEND_IN_LIEU"},
        date_of=_rules.income_date if _rules else None)

    money = fmt_money               # shared report-layer formatter

    # Treatment per row (lib/income_dating): Canada — a Canadian
    # dealer's payment on a Canadian issuer's share is a taxable
    # dividend (ITA s.260; T5 box 24, counted in `divs-sum`); every
    # other payment in lieu is ordinary income (US: non-qualified).
    agg: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    totals: Dict[str, float] = {}
    by_treat: Dict[str, Dict[str, float]] = {"dividend": {}, "ordinary": {}}
    # A registered account's payment in lieu is not income at all: it
    # is its own line, never "ordinary income" (S041-09).
    groups = _account_group_of(Path(args.dir).resolve())
    sheltered: Dict[str, float] = {}
    shel_accts = set()
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        # Signed: reversal rows (negative) net against the original posting.
        amt = (float(tx.get("gross_amount") or 0.0)
               or float(tx.get("net_amount") or 0.0))
        treat = ("dividend" if _rules is not None
                 and _rules.pil_is_dividend(tx) else "ordinary")
        key = (str(tx.get("symbol") or "?"), cur, treat)
        rec = agg.setdefault(key, {"amount": 0.0, "rows": 0})
        rec["amount"] += amt
        rec["rows"] += 1
        totals[cur] = totals.get(cur, 0.0) + amt
        if groups.get(acct) == "sheltered":
            sheltered[cur] = sheltered.get(cur, 0.0) + amt
            shel_accts.add(acct)
            continue
        by_treat[treat][cur] = by_treat[treat].get(cur, 0.0) + amt
    _warn_bad_dates(bad)
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "in_lieu": round(rec["amount"], 2),
                             "rows": int(rec["rows"]),
                             "treatment": treat}
                            for (sym, cur, treat), rec
                            in sorted(agg.items())],
                   "totals": {c: round(v, 2) for c, v in totals.items()},
                   "totals_ordinary": {c: round(v, 2) for c, v
                                       in by_treat["ordinary"].items()},
                   "totals_dividend": {c: round(v, 2) for c, v
                                       in by_treat["dividend"].items()},
                   "totals_sheltered": {c: round(v, 2) for c, v
                                        in sheltered.items()},
                   "sheltered_included": sorted(shel_accts),
                   "scope": scope})
        return
    if not agg:
        print(f"No dividends in lieu in {scope}.")
        return
    out_lines = ["SYMBOL CUR IN_LIEU ROWS TREATMENT"]
    for (sym, cur, treat), rec in sorted(agg.items()):
        out_lines.append(" ".join([
            sym, cur, money(rec["amount"]), str(int(rec["rows"])),
            "dividend(s.260)" if treat == "dividend" else "ordinary"]))
    if by_treat["dividend"]:
        print(f"DIVIDENDS IN LIEU — {scope}  (ordinary income, except a "
              f"Canadian dealer's payment on a Canadian issuer's share: "
              f"a taxable dividend under ITA s.260, on the T5 and in "
              f"`divs-sum`; the slip is authoritative)")
    else:
        print(f"DIVIDENDS IN LIEU — {scope}  (ordinary income: no dividend "
              f"gross-up/credit or qualified rate)")
    print()
    _print_report_table(out_lines)

    def _tot(d):
        return ", ".join(f"{money(v)} {c}" for c, v in sorted(d.items()))
    if by_treat["dividend"] or shel_accts:
        print(f"\nORDINARY INCOME: {_tot(by_treat['ordinary']) or '0.00'}")
    if by_treat["dividend"]:
        print(f"DEEMED DIVIDENDS (s.260, in divs-sum): "
              f"{_tot(by_treat['dividend'])}")
    if shel_accts:
        print(f"SHELTERED ({', '.join(sorted(shel_accts))} — not taxable "
              f"income): {_tot(sheltered)}")
    print(f"\nTOTAL DIVIDEND IN LIEU"
          + (" (all accounts)" if shel_accts else "")
          + f": {_tot(totals)}")


def cmd_roc_sum(args: argparse.Namespace) -> None:
    """Return-of-capital summary over a window (default: the tax year):
    per ticker, the capital returned (= ACB reduced) with broker-classified
    vs manual row counts, plus per-currency totals. Reads ADJUST rows —
    broker-classified ROC carries type='roc'; manual .tt adjustments (e.g.
    T3 box 42 entries) count too."""
    # A Canadian trust's ROC counts in the year it became payable (its
    # record date, lib/income_dating — the year of T3 box 42).
    _rules = _view_income_rules(Path(args.dir).resolve())
    rows, scope, bad, _keep = _collect_period_txs(
        args, "roc-sum", actions={"ADJUST"},
        date_of=_rules.roc_date if _rules else None)
    _root = Path(args.dir).resolve()
    _accts = sorted({a for a, _t in rows}
                    | set(_discover_tx_accounts(_root / "work")))
    _acct_arg = _view_window(args, _root)[2]
    if _acct_arg:
        _accts = [_acct_arg]
    dist_rows = _dist_adjust_rows(_root / "work", _accts, _keep)
    # The same ROC entered as a .tt ADJUST AND in distributions.map
    # reduces the ACB twice — say so (audit R1-163).
    _manual_keys = {(a, str(t.get("symbol") or ""), t.get("date"))
                    for a, t in rows}
    for a, t in dist_rows:
        if (a, str(t.get("symbol") or ""), t.get("date")) in _manual_keys:
            print(f"taxjson roc-sum: warning: {t.get('symbol')} "
                  f"{t.get('date')} ({a}) has an ADJUST in the books AND "
                  f"a distributions.map row — the ACB is reduced twice "
                  f"if both are the same distribution.", file=sys.stderr)
    rows = list(rows) + dist_rows

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], Dict[str, float]] = {}
    totals: Dict[str, float] = {}
    # Registered accounts have no ACB to track and get no T3: the total
    # a user ties to T3 box 42 is the TAXABLE one (S041-10), the same
    # scope as the checklist's roc-entered step.
    groups = _account_group_of(_root)
    by_group: Dict[str, Dict[str, float]] = {"taxable": {},
                                             "sheltered": {}}
    shel_accts = set()
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        _g = groups.get(acct)
        if _g:
            _r = -float(tx.get("net_amount") or 0.0)
            by_group[_g][cur] = by_group[_g].get(cur, 0.0) + _r
            if _g == "sheltered":
                shel_accts.add(acct)
        # ADJUST net_amount is the ACB delta (negative = reduction).
        # Present as capital RETURNED, so a normal ROC posting is
        # positive; negative values are reversals / manual ACB increases.
        returned = -float(tx.get("net_amount") or 0.0)
        key = (str(tx.get("symbol") or "?"), cur)
        rec = agg.setdefault(key, {"returned": 0.0, "roc_rows": 0,
                                   "manual_rows": 0, "dist_rows": 0})
        rec["returned"] += returned
        _typ = (tx.get("type") or "").lower()
        if _typ == "roc":
            rec["roc_rows"] += 1
        elif _typ == "dist":
            rec["dist_rows"] += 1
        else:
            rec["manual_rows"] += 1
        totals[cur] = totals.get(cur, 0.0) + returned
    _warn_bad_dates(bad)
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "capital_returned": round(rec["returned"], 2),
                             "roc_rows": int(rec["roc_rows"]),
                             "manual_rows": int(rec["manual_rows"]),
                             "dist_rows": int(rec["dist_rows"])}
                            for (sym, cur), rec in sorted(agg.items())],
                   "totals": {c: round(v, 2) for c, v in totals.items()},
                   "totals_taxable": {c: round(v, 2) for c, v
                                      in by_group["taxable"].items()},
                   "totals_sheltered": {c: round(v, 2) for c, v
                                        in by_group["sheltered"].items()},
                   "sheltered_included": sorted(shel_accts),
                   "scope": scope})
        return
    if not agg:
        print(f"No ACB adjustments in {scope}.")
        return
    out_lines = ["SYMBOL CUR CAPITAL_RETURNED ROC_ROWS MANUAL_ROWS "
                 "MAP_ROWS"]
    for (sym, cur), rec in sorted(agg.items()):
        out_lines.append(" ".join([sym, cur, money(rec["returned"]),
                                   str(rec["roc_rows"]),
                                   str(rec["manual_rows"]),
                                   str(rec["dist_rows"])]))
    print(f"RETURN OF CAPITAL / ACB ADJUSTMENTS — {scope}")
    print()
    _print_report_table(out_lines)
    def _tot(d):
        return ", ".join(f"{money(v)} {c}" for c, v in sorted(d.items()))
    print()
    if shel_accts:
        print(f"TAXABLE (compare with T3 box 42): "
              f"{_tot(by_group['taxable']) or '0.00'}")
        print(f"SHELTERED ({', '.join(sorted(shel_accts))} — no ACB to "
              f"track, no T3): {_tot(by_group['sheltered'])}")
        print(f"TOTAL CAPITAL RETURNED (all accounts): {_tot(totals)}")
    else:
        print(f"TOTAL CAPITAL RETURNED (ACB reduced): {_tot(totals)}")
    print("Positive = ACB reduced (capital returned). Negative rows are "
          "reversals or manual ACB increases (MAP_ROWS: distributions.map "
          "adjustments, a reinvested distribution shows negative). Enter "
          "fund ROC from your T3 box 42 as .tt ADJUST lines OR in "
          "distributions.map, never both — see the README's ROC section.")


def cmd_trades_sum(args: argparse.Namespace) -> None:
    """Trade summary over a window (default: the tax year): per ticker, the
    buy/sell counts, value bought/sold, and fees, plus per-currency totals.
    `PERIOD` is 30d/6w/3m/1y/all; omit it for the tax year."""
    rows, scope, bad, _keep = _collect_period_txs(
        args, "trades-sum", actions={"BUYSELL", "ASSIGN"},
        settle_trades=True)

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], Dict[str, float]] = {}
    tot_bought: Dict[str, float] = {}
    tot_sold: Dict[str, float] = {}
    tot_fees: Dict[str, float] = {}
    # The SOLD total pooled registered accounts with no word: it is not
    # the T5008 figure, which covers taxable accounts only (S041-12).
    groups = _account_group_of(Path(args.dir).resolve())
    tax_sold: Dict[str, float] = {}
    shel_accts = set()
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        q = float(tx.get("quantity") or 0.0)
        amt = _trade_total(tx)          # sells signed (S039-11)
        fee = _tx_fee(tx)
        if groups.get(acct) == "sheltered":
            shel_accts.add(acct)
        elif q < 0:
            tax_sold[cur] = tax_sold.get(cur, 0.0) + amt
        d = agg.setdefault((str(tx.get("symbol") or "?"), cur),
                           {"buys": 0, "sells": 0, "bought": 0.0, "sold": 0.0,
                            "fees": 0.0})
        if q > 0:
            d["buys"] += 1
            d["bought"] += amt
            tot_bought[cur] = tot_bought.get(cur, 0.0) + amt
        elif q < 0:
            d["sells"] += 1
            d["sold"] += amt
            tot_sold[cur] = tot_sold.get(cur, 0.0) + amt
        d["fees"] += fee
        tot_fees[cur] = tot_fees.get(cur, 0.0) + fee
    _warn_bad_dates(bad)
    for _tot_d, _k in ((tot_bought, "bought"), (tot_sold, "sold"),
                       (tot_fees, "fees")):
        _tot_d.clear()
        _tot_d.update(_foot_by_currency((cur, d[_k]) for (_s, cur), d
                                        in agg.items()))
    if getattr(args, "json", False):
        _json_out({"rows": [dict(d, symbol=sym, currency=cur,
                                 buys=int(d["buys"]), sells=int(d["sells"]))
                            for (sym, cur), d in sorted(agg.items())],
                   "totals": {c: {"bought": round(tot_bought.get(c, 0.0), 2),
                                  "sold": round(tot_sold.get(c, 0.0), 2),
                                  "fees": round(tot_fees.get(c, 0.0), 2)}
                              for c in sorted(set(tot_bought) | set(tot_sold)
                                              | set(tot_fees))},
                   "sold_taxable": {c: round(v, 2)
                                    for c, v in tax_sold.items()},
                   "sheltered_included": sorted(shel_accts),
                   "scope": scope})
        return
    if not agg:
        print(f"No trades in {scope}.")
        return
    out_lines = ["SYMBOL CUR BUYS SELLS BOUGHT SOLD FEES"]
    for (sym, cur), d in sorted(agg.items()):
        out_lines.append(" ".join([
            sym, cur, str(int(d["buys"])), str(int(d["sells"])),
            money(d["bought"]), money(d["sold"]), money(d["fees"])]))
    print(f"TRADES — {scope}")
    print()
    _print_report_table(out_lines)
    curs = sorted(set(tot_bought) | set(tot_sold) | set(tot_fees))
    print()
    for c in curs:
        print(f"TOTAL {c}: bought {money(tot_bought.get(c, 0.0))}, "
              f"sold {money(tot_sold.get(c, 0.0))}, "
              f"fees {money(tot_fees.get(c, 0.0))}")
    if shel_accts:
        print(f"(all accounts, including registered "
              f"{', '.join(sorted(shel_accts))}; sold in taxable accounts "
              f"only: " + (", ".join(f"{money(v)} {c}" for c, v
                                     in sorted(tax_sold.items()))
                           or "0.00") + ")")


def _real_world_legs(t: dict) -> Tuple[float, float]:
    """(proceeds, cost) as a person reads them: a SHORT row carries the
    engine's signed legs (cost = -premium/-short proceeds, proceeds =
    -buy-back/-cover), so its columns read negative and swapped. The
    views show what ccd-sum, winners and form-export show — proceeds
    = what the short brought in, cost = what closing it cost (S048-10,
    S040-14)."""
    proceeds = float(t.get("proceeds") or 0.0)
    cost = float(t.get("cost") or 0.0)
    if t.get("direction") == "SHORT":
        return -cost + 0.0, -proceeds + 0.0
    return proceeds, cost


def _gain_display_line(g: dict) -> str:
    """A realized-gain (disposition) row for `taxjson gains`: money columns
    (proceeds/cost/gain) to 2 decimals, quantity keeps significant digits.
    A short row's legs are shown the real-world way (_real_world_legs)."""
    money = fmt_money               # shared report-layer formatter

    def sig(x):
        s = f"{float(x or 0):.8f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-", "-0") else s

    days = g.get("days_held")
    _p, _c = _real_world_legs(g)
    return " ".join([
        g.get("date", ""), g.get("symbol", ""), sig(g.get("qty")),
        g.get("currency") or "?", money(_p),
        money(_c), money(g.get("gain")),
        "" if days is None else str(days),
    ])


def cmd_gains(args: argparse.Namespace) -> None:
    """Realized gains in NATIVE terms (pre-TOBASE consolidation, pre-currency-
    to-base) — read from the pipeline's `<account>_raw_gains.json`."""
    import json
    root = Path(args.dir).resolve()
    cache = root / "work"
    keep, _scope, account = _view_window(args, root)
    suffix = "_raw_gains.json"

    # Income rows carry an action; dispositions are everything else. Excluding
    # the known income actions (rather than requiring action is None) keeps
    # working if the engine ever tags dispositions with an action.
    _INCOME = {"DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST", "FEE",
               "DISALLOW", "ADJUST"}
    sym_filter = _instrument_filter(args)

    if account:
        accounts = [account]
        if not (cache / f"{account}{suffix}").exists():
            if (cache / f"{account}_base.json").exists():
                sys.exit(f"taxjson gains: no native gains for account "
                         f"{account!r}: crypto accounts have none, and "
                         f"an equity account's native books are skipped "
                         f"after a cross-currency rollover rename (see "
                         f"the run's '!! raw holdings skipped' line) — "
                         f"rerunning will not create them; its converted "
                         f"gains are in `taxjson sum`.")
            sys.exit(f"taxjson gains: no native gains for account "
                     f"{account!r} in {cache} (crypto has none; else run "
                     f"`taxjson run`, or check the name).")
    else:
        accounts = sorted(p.name[: -len(suffix)]
                          for p in cache.glob(f"*{suffix}")
                          if not p.name.startswith("."))
        # A configured equity account with converted books but no native
        # gains had its raw stage skipped (a cross-currency rollover
        # rename): say so, or the view silently omits the whole account
        # while `sum` counts it (2026-09 audit S037-23).
        for _n, _c in sorted((_soft_config(root).get("accounts")
                              or {}).items()):
            if (isinstance(_c, dict) and _c.get("crypto")
                    and _n not in accounts
                    and (cache / f"{_n}_base.json").exists()):
                # The all-accounts view left the crypto account out
                # with no word while `sum` counted it (R1-274).
                print(f"taxjson gains: note: crypto account {_n!r} is "
                      f"not shown (crypto has no native-currency gains); "
                      f"its gains are in `taxjson sum` / `taxjson "
                      f"winners`.", file=sys.stderr)
            elif (isinstance(_c, dict) and not _c.get("crypto")
                    and _n not in accounts
                    and (cache / f"{_n}_base.json").exists()):
                print(f"taxjson gains: note: account {_n!r} has no native "
                      f"gains (the last run skipped its native books — "
                      f"see its '!! raw holdings skipped' line); its "
                      f"converted gains are in `taxjson sum` / "
                      f"`taxjson winners`.", file=sys.stderr)
        if not accounts:
            sys.exit(f"taxjson gains: no native gains files in {cache} "
                     f"(run `taxjson run` first).")

    rows = []
    bad_dates = 0
    for acct in accounts:
        f = cache / f"{acct}{suffix}"
        if not f.exists():
            print(f"note: no native gains for account {acct!r} (e.g. crypto "
                  f"only has base-converted gains); skipping.", file=sys.stderr)
            continue
        data = _load_json_or_die(f)
        _settle = _settle_basis(root, data)
        for g in data.get("transactions", []):
            if g.get("action") in _INCOME:
                continue
            if sym_filter is not None and not sym_filter(g.get("symbol") or ""):
                continue
            d = _gains_row_date(g, keep, _settle)
            if not _ISO_DATE_RE.match(d):
                bad_dates += 1
                continue
            if keep(d):
                rows.append((d, acct, g))

    if bad_dates:
        print(f"taxjson: warning: {bad_dates} gain row(s) had a missing/unparseable "
              f"date and were excluded.", file=sys.stderr)
    rows.sort(key=lambda r: (r[0], r[1]))
    if getattr(args, "json", False):
        jt: Dict[str, float] = {}
        for _d, _acct, g in rows:
            cur = g.get("currency") or "?"
            jt[cur] = jt.get(cur, 0.0) + float(g.get("gain") or 0.0)
        _json_out({"rows": [dict(g, account=acct) for _d, acct, g in rows],
                   "totals": jt})
        return
    if not rows:
        print("(no realized gains in this window)")
        return
    prefix = len(accounts) != 1

    print("REALIZED GAINS — native currency, per account, pre-wash "
          "(pre-TOBASE, pre-currency-to-base; the filing numbers are "
          "`taxjson sum`)")
    print()
    header = (["ACCT"] if prefix else []) + \
        ["DATE", "SYMBOL", "QTY", "CUR", "PROCEEDS", "COST", "GAIN", "DAYS"]
    out_lines = [" ".join(header)]
    totals: Dict[str, float] = {}
    for _d, acct, g in rows:
        line = _gain_display_line(g)
        out_lines.append(f"{acct} {line}" if prefix else line)
        cur = g.get("currency") or "?"
        totals[cur] = totals.get(cur, 0.0) + float(g.get("gain") or 0.0)

    _print_report_table(out_lines)
    print("\nTOTAL GAIN: " + ", ".join(
        f"{v:,.2f} {c}" for c, v in sorted(totals.items())))


_PLAN_NAMES = ("tfsa", "rrsp", "lira", "rrif", "fhsa", "resp", "401k",
               "roth", "ira")


_PLAN_KINDS = _PLAN_NAMES + ("taxable", "sheltered")


def _account_plan(name: str, acfg: Dict[str, Any]) -> str:
    """Registered-plan kind for scan checks: explicit `plan = "tfsa"` in
    taxjson.toml wins (an unknown value is ignored — validate_config
    warns); else inferred from a plan word that is a whole TOKEN of the
    account NAME (the init scaffold names folders tfsa/rrsp/...; `rrsp2`
    and `my-tfsa` count, `admiral` and `spiral` no longer read as an IRA
    and silently skipped the US-LISTING check, R1-243); else the type."""
    explicit = str(acfg.get("plan") or "").strip().lower()
    if explicit in _PLAN_KINDS:
        return explicit
    low = name.lower()
    for p in _PLAN_NAMES:
        if re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", low):
            return p
    return "taxable" if acfg.get("type") == "taxable" else "sheltered"


def _scan_symbol_root(sym: str) -> Tuple[str, str]:
    parts = sym.rsplit(".", 1)
    if len(parts) == 2 and parts[1].upper() in ("TO", "US", "V", "CN", "NE"):
        return parts[0].upper(), parts[1].upper()
    return sym.upper(), ""


# Boilerplate an exchange appends to a listing's name that carries no
# issuer identity — dropped before comparing names across listings.
_ISSUER_NOISE = frozenset(
    "INC INCORPORATED CORP CORPORATION LTD LIMITED CO COMPANY PLC LP "
    "THE NEW CLASS CL A B C COM COMMON ORDINARY SHARES SHS STOCK "
    "SUBORDINATE VOTING NON RESTRICTED SV MV NPV ADR ADS UNITS UNIT "
    "TRUST FUND ETF INDEX INCOME REIT DEPOSITARY RECEIPT".split())


def _norm_issuer_name(name: str) -> str:
    """Issuer name reduced to its identity tokens, order kept:
    'B2Gold Corp.' -> 'B2GOLD', 'The Toronto-Dominion Bank' ->
    'TORONTO DOMINION BANK'. Empty when nothing survives."""
    import re as _re
    tokens = _re.sub(r"[^A-Za-z0-9 ]", " ", name.upper()).split()
    return " ".join(t for t in tokens if t not in _ISSUER_NOISE)


def _is_cdr_name(name: str) -> bool:
    """Exchange name says Canadian Depositary Receipt — a fractional,
    CAD-hedged receipt over a US share, NOT a listing equivalent (the
    receipt ratio floats with the hedge, so no static TOBASE ratio can
    ever be right)."""
    up = f" {name.upper()} "
    return (" CDR " in up or "(CDR)" in up.replace("( ", "(")
            or "CANADIAN DEPOSITARY" in up or "CAD HEDGED" in up
            or "CAD-HEDGED" in up)


def _issuer_names_match(a: str, b: str) -> bool:
    """Same issuer? Both names normalized; equal, or one a prefix of
    the other with the longer adding at most ONE token ('AGNICO
    EAGLE' vs 'AGNICO EAGLE MINES' matches; 'BROOKFIELD CORPORATION'
    vs 'BROOKFIELD RENEWABLE PARTNERS' does not). KNOWN LIMIT: a
    distinct issuer differing by exactly one meaningful token
    ('...ASSET MANAGEMENT' vs '...ASSET MANAGEMENT REINSURANCE' after
    noise-stripping) still matches — a MAP-BAD? finding says VERIFY,
    so false negatives are cheaper than false alarms."""
    na, nb = _norm_issuer_name(a), _norm_issuer_name(b)
    if not na or not nb:
        return True          # no evidence — do not accuse the map
    if na == nb:
        return True
    ta, tb = na.split(), nb.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # Multi-token prefix ('AGNICO EAGLE' vs 'AGNICO EAGLE MINES'):
    # same issuer — but only when the longer name adds at most ONE
    # token. Issuer FAMILIES extend a shared prefix by several
    # ('BROOKFIELD ASSET MANAGEMENT' vs 'BROOKFIELD ASSET MANAGEMENT
    # REINSURANCE PARTNERS' are different companies — round-five
    # audit finding 7).
    return (len(short) >= 2 and len(long_) - len(short) <= 1
            and long_[:len(short)] == short)


def cmd_scan(args: argparse.Namespace) -> None:
    """`taxjson scan`: lint the PROJECT for common tax-efficiency
    mistakes. Canada checks today:

      US-LISTING   a cross-listed CANADIAN issuer held via its US line in
                   a taxable account or TFSA while receiving dividends —
                   USD dividend conversion drag, and brokers can
                   misclassify the payment; the .TO line gives clean
                   eligible-dividend treatment.
      TFSA-US-DIV  a US-domiciled dividend payer inside a TFSA — the 15%
                   US withholding is unrecoverable there (an RRSP is
                   treaty-exempt; a taxable account can claim the FTC).
      MAP-GAP      ticker.map coverage: roots seen under BOTH a .TO and
                   .US listing anywhere in the project with no
                   GLOBAL/TOBASE/JOURNAL entry consolidating them. With
                   --online, additionally probes yfinance for a .TO twin
                   of every US-listed dividend payer the map doesn't
                   know, clusters HELD listings by issuer name to catch
                   DIFFERENT-root dual listings (BTG.US/BTO.TO), and
                   verifies every defined map pair names one issuer
                   (MAP-BAD? on mismatch). Candidates to verify, not
                   verdicts.
      CDR-PAIR     a .TO line whose exchange name says CDR (Canadian
                   Depositary Receipt — UNH.TO over UNH.US): the SAME
                   issuer but NOT a listing equivalent (fractional,
                   CAD-hedged, floating ratio) — never map it; declare
                   `DISTINCT UNH.US UNH.TO` in ticker.map to record
                   the ruling and silence the pair. A map entry that
                   pairs a CDR with its underlying is flagged MAP-BAD?.

    Exit 1 when any finding is reported, 0 on a clean scan."""
    import json
    root = Path(args.dir).resolve()
    cache = root / "work"
    reports = root / "reports"
    cfg = load_config(root)
    settings = cfg.get("settings", {})
    country = _country(settings)
    accounts = cfg.get("accounts", {}) or {}
    if not accounts:
        sys.exit("taxjson scan: no accounts in taxjson.toml.")

    # Per-listing positions (holdings.toml is built PRE-TOBASE, so the
    # US/TO line actually held is visible — the gains inventory is
    # already consolidated and would hide it).
    holdings: Dict[str, list] = {}
    for name in accounts:
        f = reports / f"{name}_holdings.toml"
        if not f.exists() or tomllib is None:
            continue
        try:
            holdings[name] = (tomllib.loads(f.read_text(encoding="utf-8"))
                              .get("holding", []))
        except Exception as e:
            # A warning, then "No findings — clean scan." and exit 0
            # turned an unreadable report into a false all-clear: the
            # findings on that account's positions vanished (S049-10).
            _die(f"could not read {f}: {e} — re-run `taxjson run` to "
                 f"rebuild it (the scan would otherwise leave that "
                 f"account's positions out).")
    _equity_accts = [n for n, c in accounts.items()
                     if not (c or {}).get("crypto")]
    if not holdings and _equity_accts:
        # Printing "No findings — clean scan." (exit 0) over a scan that
        # read nothing was a false all-clear (2026-09 CLI audit B22).
        if set(_equity_accts) <= _accounts_skipped_for_no_inputs(root):
            _die("no account has any input yet — nothing to scan. Drop "
                 "broker CSVs into inputs/<account>/ and `taxjson run`.")
        _die(f"no holdings reports in {reports} — run `taxjson run` "
             f"first (the scan checks per-listing positions; nothing "
             f"was scanned).")

    # Dividend payers, per raw (pre-consolidation) symbol.
    div_syms: set = set()
    for name in accounts:
        f = cache / f"{name}_raw.json"
        if not f.exists():
            continue
        # A truncated raw book turned a real finding into 'No findings —
        # clean scan.' with exit 0 (audit S042-05).
        data = _load_json_or_die(f)
        for t in data.get("transactions", []):
            if t.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                div_syms.add(str(t.get("symbol") or "").upper())

    # ticker.map consolidations (GLOBAL + TOBASE + JOURNAL) and the
    # user's declared-distinct pairs (CDRs etc. — see DISTINCT).
    renames: Dict[str, str] = {}
    raw_rules: Dict[str, str] = {}
    distinct_pairs: set = set()
    map_file = root / "ticker.map"
    if map_file.exists():
        try:
            from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                        merge_renames,
                                                        raw_renames)
            _tmap = load_map_file(map_file)
            raw_rules = raw_renames(_tmap, to_base=True)
            renames = merge_renames(_tmap, to_base=True)
            distinct_pairs = {frozenset(s.upper() for s in pair)
                              for pair in _tmap.distinct}
        except Exception as e:
            print(f"taxjson: warning: could not read ticker.map: {e}",
                  file=sys.stderr)
    renames_u = {k.upper(): v.upper() for k, v in renames.items()}

    def _declared_distinct(a: str, b: str) -> bool:
        return frozenset((a.upper(), b.upper())) in distinct_pairs

    # Every (root, suffix) sighting across holdings + dividend history +
    # the map itself — the cross-listing evidence base.
    seen_suffixes: Dict[str, set] = {}

    from taxjson.lib.core import (is_option_symbol as _is_opt,
                                  parse_option_underlying as _opt_und)

    def _see(sym: str) -> None:
        # An option is a sighting of its underlying's listing: a pair
        # evidenced on one side only by options was a "clean scan"
        # while the engine kept two identity classes (S042-04).
        if _is_opt(sym):
            sym = _opt_und(sym) or sym
        r, suf = _scan_symbol_root(sym)
        if suf:
            seen_suffixes.setdefault(r, set()).add(suf)

    for rows in holdings.values():
        for h in rows:
            _see(str(h.get("symbol") or ""))
    for sym in div_syms:
        _see(sym)
    for old, new in renames_u.items():
        _see(old)
        _see(new)

    def _has_ca_twin(rt: str, sym_u: str) -> bool:
        # ticker.map (GLOBAL/TOBASE onto a .TO listing) is the identity
        # ruling; without one, a .TO sighting of the same root is only
        # evidence (MAP-GAP asks for the ruling too). A DISTINCT ruling
        # settles it the other way: the .TO line is a CDR or another
        # issuer, and "hold it instead" is wrong advice (audit S042-06,
        # S049-09).
        tgt = renames_u.get(sym_u, "")
        if tgt.endswith(".TO"):
            return not _declared_distinct(sym_u, tgt)
        if _declared_distinct(sym_u, f"{rt}.TO"):
            return False
        return "TO" in (seen_suffixes.get(rt) or set())

    findings = []                     # (check, account, symbol, message)
    if country == "canada":
        for name, acfg in accounts.items():
            plan = _account_plan(name, acfg)
            if plan not in ("taxable", "tfsa"):
                continue              # RRSP/LIRA: treaty-exempt, no check
            for h in holdings.get(name, []):
                sym = str(h.get("symbol") or "")
                if float(h.get("quantity", 0) or 0) == 0:
                    continue
                rt, suf = _scan_symbol_root(sym)
                if suf != "US":
                    continue
                sym_u = sym.upper()
                # The US line itself must pay: borrowing the .TO line's
                # dividends through the bare root flagged a US holding
                # that pays nothing (audit S049-09).
                pays = sym_u in div_syms
                if not pays:
                    continue
                if _has_ca_twin(rt, sym_u):
                    _ca = renames_u.get(sym_u) or f"{rt}.TO"
                    findings.append((
                        "US-LISTING", name, sym,
                        f"Canadian issuer held via its US listing in a "
                        f"{plan} account while paying dividends — hold "
                        f"{_ca} instead for clean eligible-dividend "
                        f"treatment (and no USD conversion drag)."))
                elif plan == "tfsa":
                    findings.append((
                        "TFSA-US-DIV", name, sym,
                        "US dividend payer inside a TFSA: the 15% US "
                        "withholding is unrecoverable here. Prefer the "
                        "RRSP (treaty-exempt) or a taxable account "
                        "(foreign tax credit claimable)."))

    # MAP-GAP: both listings seen, no consolidating entry either way.
    for rt in sorted(seen_suffixes):
        sufs = seen_suffixes[rt]
        if "TO" in sufs and "US" in sufs:
            if _declared_distinct(f"{rt}.US", f"{rt}.TO"):
                continue        # user's DISTINCT ruling — settled
            if (f"{rt}.US" not in renames_u
                    and f"{rt}.TO" not in renames_u):
                findings.append((
                    "MAP-GAP", "-", f"{rt}.TO/{rt}.US",
                    "both listings appear in this project but "
                    "ticker.map has no GLOBAL/TOBASE entry — the "
                    "engine treats them as two securities (splits the "
                    "ACB pool; the radar can miss the pair)."))

    # MAP-UNUSED (note, not a finding): rules whose FROM symbol never
    # occurs in any parsed source — judged the way the ENGINE applies
    # the map (taxjson_ticker_map.map_symbol): FROM must equal a symbol
    # exactly, or an option's underlying (ROOT-aware on purpose — a
    # rule with no stock rows is still live through OPTION trades:
    # BCE251121C00050000.US needs `TOBASE BCE.US BCE.TO`; a root-blind
    # check once pruned ten live rules from a real map, 2026-09-15).
    # Chains count: a rule reached through another rule's target is
    # live (R1-139). A suffix-less FROM (`GLOBAL QQOL QQNW`) matches
    # only a suffix-less symbol — the engine never applies it to
    # QQOL.US, so scan must not call it live either (S053-12).
    map_unused: list = []
    if raw_rules:
        from taxjson.lib.core import (is_option_symbol,
                                      parse_option_underlying)
        _seen_syms: set = set()
        _unread: List[str] = []
        for _acct in accounts:
            for _p in _audit_source_files(cache, _acct,
                                          list(accounts)):
                try:
                    _doc = json.loads(_p.read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:
                    # Skipping it silently listed live rules as unused
                    # (S042-10) — and pruning on that note once split
                    # option identity classes.
                    _unread.append(f"{_p.name} ({e})")
                    continue
                for _t in (_doc.get("transactions", _doc)
                           if isinstance(_doc, dict) else _doc) or []:
                    _sym = str((_t or {}).get("symbol") or "").upper()
                    if _sym:
                        _seen_syms.add(_sym)
        _reached: set = set()
        for _sym in _seen_syms:
            _reached.add(_sym)
            if is_option_symbol(_sym):
                try:
                    _reached.add(str(parse_option_underlying(_sym)).upper())
                except Exception:
                    pass
        _rules_u = {k.upper(): v.upper() for k, v in raw_rules.items()}
        _frontier = list(_reached)
        while _frontier:
            _nxt = _rules_u.get(_frontier.pop())
            if _nxt and _nxt not in _reached:
                _reached.add(_nxt)
                _frontier.append(_nxt)
        _suffixed = {}
        for _sym in _reached:
            if "." in _sym:
                _suffixed.setdefault(_sym.rsplit(".", 1)[0], set()).add(_sym)
        for _frm, _to in sorted(raw_rules.items()):
            _fu = _frm.upper()
            if _fu in _reached:
                continue
            _hint = ""
            if "." not in _fu and _fu in _suffixed:
                _alts = ", ".join(sorted(_suffixed[_fu]))
                _hint = (f" (the books only have {_alts}; a rule's FROM "
                         f"matches exactly — write the suffixed form, "
                         f"e.g. {sorted(_suffixed[_fu])[0]})")
            map_unused.append(f"{_frm} -> {_to}{_hint}")
        if _unread:
            print(f"taxjson scan: warning: could not read "
                  f"{'; '.join(_unread)} — the unused-ticker.map-rule "
                  f"check is skipped (its symbols are unknown); re-run "
                  f"`taxjson run`.", file=sys.stderr)
            map_unused = []

    from taxjson.lib.offline import offline_enabled as _offline
    if getattr(args, "online", False) and _offline():
        # The documented kill switch covers this probe too: it sends
        # every held and dividend ticker to Yahoo (S042-12, S048-00).
        print("taxjson scan: note: TAXJSON_OFFLINE is set — the --online "
              "Yahoo Finance probe is skipped (MAP-GAP?/MAP-BAD?/"
              "CDR-PAIR are not checked); the offline checks below "
              "still ran.", file=sys.stderr)
    elif getattr(args, "online", False):
        try:
            import yfinance as yf
        except ImportError:
            print("taxjson: warning: --online needs yfinance "
                  "(pip install -e '.[fx]'); skipping the probe.",
                  file=sys.stderr)
        else:
            def _issuer_names(sym: str) -> Tuple[str, str]:
                """(longName, shortName) — BOTH matter: for a CDR the
                longName is the clean issuer ('Abbott Laboratories')
                and only the shortName carries the receipt marker
                ('ABBOTT LABS CDR (CAD HEDGED)'). Falls back to the
                chart metadata when the info endpoint is throttled."""
                ln = sn = ""
                try:
                    info = yf.Ticker(sym).info or {}
                    ln = str(info.get("longName") or "")
                    sn = str(info.get("shortName") or "")
                except Exception:
                    pass
                if not (ln or sn):
                    try:
                        t = yf.Ticker(sym)
                        t.history(period="5d", timeout=5)
                        md = t.history_metadata or {}
                        ln = str(md.get("longName") or "")
                        sn = str(md.get("shortName") or "")
                    except Exception:
                        pass
                return ln, sn

            probed = []
            for sym in sorted(div_syms):
                rt, suf = _scan_symbol_root(sym)
                if (suf != "US" or _has_ca_twin(rt, sym.upper())
                        or _declared_distinct(f"{rt}.US", f"{rt}.TO")):
                    continue
                try:
                    hist = yf.Ticker(f"{rt}.TO").history(period="5d",
                                                         timeout=5)
                    if hist is not None and not hist.empty:
                        probed.append(rt)
                except Exception:
                    continue
            for rt in probed:
                # A .TO line that exists is very often the CDR, not a
                # cross-listing — check what the exchange calls it
                # before suggesting a merge that would be wrong. An
                # unheld CDR twin is silently skipped (nothing in the
                # books to rule on); a HELD one is surfaced as
                # CDR-PAIR by the issuer-name clustering below.
                _tl, _ts = _issuer_names(f"{rt}.TO")
                if _is_cdr_name(_tl) or _is_cdr_name(_ts):
                    continue
                findings.append((
                    "MAP-GAP?", "-", f"{rt}.US",
                    f"{rt}.TO trades on the TSX — VERIFY whether it is "
                    f"the same issuer; if so, add "
                    f"`TOBASE {rt}.US {rt}.TO` to ticker.map."))

            # Issuer-name evidence — the only way to catch
            # DIFFERENT-root dual listings (BTG.US/BTO.TO) and typo'd
            # map pairs, which same-root scanning can never see.

            _CA_SUFS = ("TO", "V", "CN", "NE")
            held_syms = {str(h.get("symbol") or "").upper()
                         for rows in holdings.values() for h in rows}
            held_syms = {s for s in held_syms
                         if _scan_symbol_root(s)[1]
                         in _CA_SUFS + ("US",)}
            _NAME_CAP = 80
            _probe_list = sorted(held_syms
                                 | set(renames_u)
                                 | set(renames_u.values()))
            if len(_probe_list) > _NAME_CAP:
                print(f"taxjson scan: note: probing issuer names "
                      f"for the first {_NAME_CAP} of "
                      f"{len(_probe_list)} listed symbols "
                      f"(alphabetical; the remainder are NOT probed "
                      f"— every run scans the same window).",
                      file=sys.stderr)
                _probe_list = _probe_list[:_NAME_CAP]
            _pairs = {s: _issuer_names(s) for s in _probe_list}
            names = {s: (ln or sn) for s, (ln, sn) in _pairs.items()}
            is_cdr = {s: _is_cdr_name(ln) or _is_cdr_name(sn)
                      for s, (ln, sn) in _pairs.items()}

            # MAP-VERIFY: each defined pair must name ONE issuer — a
            # typo'd pair silently merges two companies' ACB pools.
            # A CDR paired with anything is wrong even when the issuer
            # matches: the receipt ratio floats, so no consolidation
            # ratio exists.
            for old_, new in sorted(renames_u.items()):
                na, nb = names.get(old_, ""), names.get(new, "")
                if (na and nb
                        and is_cdr.get(old_, False)
                        != is_cdr.get(new, False)):
                    findings.append((
                        "MAP-BAD?", "-", f"{old_}->{new}",
                        f"ticker.map pairs a CDR with its underlying "
                        f"({na!r} vs {nb!r}) — a CDR is a fractional "
                        f"CAD-hedged receipt whose ratio floats; "
                        f"remove the entry and declare "
                        f"`DISTINCT {old_} {new}` instead."))
                elif na and nb and not _issuer_names_match(na, nb):
                    findings.append((
                        "MAP-BAD?", "-", f"{old_}->{new}",
                        f"ticker.map pairs these, but the exchanges "
                        f"name them {na!r} vs {nb!r} — VERIFY: a "
                        f"wrong pair merges two companies' ACB "
                        f"pools."))

            # MAP-GAP (different roots): held listings whose issuer
            # names match across a US and a Canadian line with no map
            # entry connecting them.
            _by_name: Dict[str, set] = {}
            for s, n in names.items():
                if s in held_syms and n:
                    _by_name.setdefault(_norm_issuer_name(n),
                                        set()).add(s)
            for key, syms in sorted(_by_name.items()):
                if not key or len(syms) < 2:
                    continue
                # sorted: `syms` is a set, and its iteration order
                # numbered the findings differently on every run (S042-13).
                us = [s for s in sorted(syms)
                      if _scan_symbol_root(s)[1] == "US"]
                ca = [s for s in sorted(syms)
                      if _scan_symbol_root(s)[1] in _CA_SUFS]
                for u in us:
                    for c in ca:
                        if (u in renames_u or c in renames_u
                                or renames_u.get(u) == c
                                or renames_u.get(c) == u
                                or _declared_distinct(u, c)):
                            continue
                        if is_cdr.get(c) or is_cdr.get(u):
                            # Same issuer, but one side is a CDR —
                            # NOT a mappable listing pair. Suggest
                            # recording the ruling, not a TOBASE.
                            findings.append((
                                "CDR-PAIR", "-", f"{u}/{c}",
                                f"{names.get(c) or names.get(u)!r} "
                                f"is a CDR over {u} — a fractional "
                                f"CAD-hedged receipt, not a listing "
                                f"equivalent; do NOT map them. Add "
                                f"`DISTINCT {u} {c}` to ticker.map "
                                f"to record this and silence the "
                                f"pair."))
                            continue
                        findings.append((
                            "MAP-GAP?", "-", f"{u}/{c}",
                            f"both held and both named "
                            f"{names[u]!r} — looks like one issuer's "
                            f"two listings with no ticker.map entry; "
                            f"VERIFY and add `TOBASE {u} {c}` (or "
                            f"JOURNAL) if so."))

    if getattr(args, "json", False):
        print(json.dumps({"findings": [
            {"check": c, "account": a, "symbol": sy, "message": m}
            for c, a, sy, m in findings],
            "notes": [{"check": "MAP-UNUSED", "rule": r}
                      for r in map_unused]},
            indent=2, sort_keys=True))
        raise SystemExit(1 if findings else 0)

    print(f"SCAN — common tax-efficiency mistakes, {country}"
          f"{', online map probe' if getattr(args, 'online', False) else ''}")
    print()
    if not holdings:
        print("No holdings reports found — run `taxjson run` first; the "
              "scan checks per-listing positions.")
    if map_unused:
        print(f"NOTE: {len(map_unused)} ticker.map rule(s) match no "
              f"parsed symbol in this project (checked stock rows, "
              f"option roots and rename chains): "
              f"{'; '.join(map_unused)}. Unused rules are harmless; "
              f"prune only if you know the symbol will not return.")
        print()
    if not findings:
        print("No findings — clean scan.")
        raise SystemExit(0)
    out_lines = ["CHECK ACCOUNT SYMBOL"]
    for c, a, sy, _m in findings:
        out_lines.append(" ".join([c, a, sy]))
    _print_report_table(out_lines)
    print()
    for i, (c, a, sy, m) in enumerate(findings, 1):
        where = f" [{a}]" if a != "-" else ""
        print(f"{i}. {c}{where} {sy}: {m}")
    print(f"\n{len(findings)} finding(s).")
    raise SystemExit(1)


def cmd_summary(args: argparse.Namespace) -> None:
    """One-row-per-account realized-gains summary (base currency) on the
    filing basis: wash-adjusted gains where the pipeline built the
    cross-account pass (matching `reports/<account>_wash.sum`, carryover,
    t1135 and form-export), plain gains otherwise. The banner names the
    basis. Reuses `summarize_gains` so the aggregation itself matches
    `reports/<account>.sum`."""
    import json
    from taxjson.bin.taxjson_sum_gains import summarize_gains
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    # Canonical per-account gains, wash-adjusted where the pipeline built
    # the cross-account pass — the same basis carryover/t1135/form-export
    # report, so this summary can't disagree with the filing commands.
    files = resolve_gains_files(cache, getattr(args, "account", None)
                                or None)
    if not files:
        if getattr(args, "account", None):
            _die(f"no gains for account {args.account!r} in {cache} "
                 f"(run `taxjson run` first, or check the name).")
        if not (root / "taxjson.toml").exists():
            _die(f"no gains files, and no taxjson.toml in {root} — not "
                 f"a taxjson project (`taxjson init` creates one; -C "
                 f"selects another directory).")
        _die(f"no gains files in {cache} (run `taxjson run` first).")
    basis = gains_basis_label(files)

    money = fmt_money               # shared report-layer formatter

    # --other-income/--other-losses turn on the marginal tax estimate,
    # which needs the config (country/account types) and only counts
    # TAXABLE accounts' income.
    want_estimate = (getattr(args, "other_income", None) is not None
                     or getattr(args, "other_losses", None) is not None
                     or getattr(args, "deductions", None) is not None
                     or getattr(args, "carrying_charges", None) is not None
                     or getattr(args, "estimate", False))
    _oi, _ol = _estimate_inputs(root, args)
    _ded, _cc = _estimate_deductions(root, args)
    _foreign_by_acct: Dict[str, float] = {}
    cfg = load_config(root) if (root / "taxjson.toml").exists() else {}
    taxable_accounts = {n for n, c in cfg.get("accounts", {}).items()
                        if c.get("type") == "taxable"}
    est = dict(realized=0.0, st=0.0, lt=0.0, div_ca=0.0,
               div_foreign=0.0, pil=0.0, staking=0.0)
    if want_estimate and not cfg:
        _die("the tax estimate needs taxjson.toml "
             "(country and account types).")
    if getattr(args, "province", None) and not want_estimate:
        print(f"taxjson {_CURRENT_CMD or 'sum'}: warning: --province is "
              f"ignored without the tax estimate (add --other-income/"
              f"--other-losses, or use `taxjson estimate`).",
              file=sys.stderr)
    if want_estimate:
        import math as _math
        for _flag in ("other_income", "other_losses"):
            _v = getattr(args, _flag, None)
            if _v is not None and (not _math.isfinite(_v) or _v < 0):
                # nan/inf rendered contradictory estimates with rc 0;
                # a NEGATIVE loss fabricated taxable gains — the
                # natural sign trap for "my carryover is -10,000"
                # (REVIEW #25/#42).
                _die(f"--{_flag.replace('_', '-')} "
                     f"must be a non-negative finite number "
                     f"(enter losses as a positive amount), "
                     f"got {_v!r}")
        _settings0 = cfg.get("settings") or {}
        if _country(_settings0) \
                == "canada":
            # Validate the province BEFORE printing anything: a missing
            # or unsupported one used to fail only after the whole sum
            # table had scrolled past (2026-09 CLI audit B20).
            from taxjson.lib import tax_estimate as _te
            _prov = (getattr(args, "province", None)
                     or str(_settings0.get("province", "") or "")).strip()
            _te.apply_vintage(_settings0.get("year"))
            if not _prov:
                _die("the canada estimate needs a province — pass "
                     "--province ON|BC|AB or set `province` under "
                     "[settings] in taxjson.toml.")
            if _prov.upper() not in _te.CA_PROVINCES:
                _die(f"unsupported province {_prov!r} for the estimate "
                     f"(supported: "
                     f"{', '.join(sorted(_te.CA_PROVINCES))})")
    _run_state: List[str] = []
    if cfg:
        _warn_artifact_year(files, (cfg.get("settings") or {}).get("year"))
        _run_state = _warn_run_state(root, cfg)

    # NON-OPT: every non-option disposition (shares, units, futures,
    # crypto) — it was headed STOCK and read as Schedule 3 line 4
    # (audit R1-211, S051-01).
    header = ["ACCOUNT", "NON-OPT", "OPTION", "REALIZED", "DIVIDEND", "PIL",
              "FEES", "TOTAL"]
    acct_types = {n: str(c.get("type") or "")
                  for n, c in cfg.get("accounts", {}).items()}
    acct_rows: List[Dict[str, Any]] = []
    tainted_included = 0
    tainted_routed = 0
    year = None
    for acct, p in files.items():
        # Prefer the machine twin the pipeline wrote (work/<acct>_report.json)
        # when it is at least as fresh as the gains file — same aggregates,
        # no recompute. Falls back to summarize_gains for older projects.
        res = None
        report_p = p.with_name(f"{acct}_report.json")
        if report_p.exists() and report_p.stat().st_mtime >= p.stat().st_mtime:
            # Freshness alone is not enough: after `run --account X` the
            # report is freshly rebuilt from PRE-wash gains (the wash
            # pass is skipped under --account) while resolve_gains_files
            # still returns the older _gains_wash.json — serving it
            # would print pre-wash aggregates under a "wash-adjusted"
            # banner. Require the recorded basis to match the resolved
            # file; reports without one (older projects) fall through to
            # reading the gains file itself.
            p_basis = ("wash-adjusted" if p.name.endswith("_gains_wash.json")
                       else "pre-wash")
            try:
                rep = _read_work_doc(report_p)
                if (rep.get("schema_version") == 1 and "gains" in rep
                        and rep.get("basis") == p_basis):
                    res = rep["gains"]
                    year = year or rep.get("year")
                elif (rep.get("basis") and rep.get("basis") != p_basis):
                    # The account was rebuilt on a DIFFERENT basis than
                    # the resolved gains file (run --account skips the
                    # wash pass). We fall back to the older wash file
                    # for label consistency — but say so, or the
                    # staleness is invisible.
                    print(f"taxjson sum: note: {acct}: serving the "
                          f"previous {p_basis} numbers — the latest "
                          f"per-account rebuild is {rep.get('basis')} "
                          f"only. Run a full `taxjson run` to refresh "
                          f"the wash-adjusted aggregates.",
                          file=sys.stderr)
            except (OSError, ValueError):
                res = None
        if res is None:
            try:
                data = _read_work_doc(p)
            except (OSError, ValueError) as e:
                if acct in taxable_accounts:
                    # A taxable account's books left out of the filing
                    # block, the estimate and instalments with rc 0
                    # (R1-277, S004-09): refuse instead.
                    _die(f"could not read {p}: {e} — a taxable "
                         f"account's gains cannot be left out of the "
                         f"totals. Re-run `taxjson run` to rebuild it.")
                print(f"taxjson: warning: could not read {p}: {e}", file=sys.stderr)
                continue
            year = year or (data.get("summary") or {}).get("year")
            res = summarize_gains(data)
        cap = opt = div = pil = 0.0
        for ticker, cur_map in res.get("ticker_stats", {}).items():
            is_ca_listed = (ticker.rsplit(".", 1)[-1].upper()
                            in ("TO", "V", "CN", "NE"))
            for s in cur_map.values():
                cap += float(s.get("cap", 0) or 0)
                opt += float(s.get("opt", 0) or 0)
                div += float(s.get("div", 0) or 0)
                pil += float(s.get("pil", 0) or 0)
                if acct in taxable_accounts:
                    g = (float(s.get("cap", 0) or 0)
                         + float(s.get("opt", 0) or 0))
                    est["realized"] += g
                    est["st"] += float(s.get("st_gain", 0) or 0)
                    est["lt"] += float(s.get("lt_gain", 0) or 0)
                    d = float(s.get("div", 0) or 0)
                    # A crypto account's DIVIDEND rows are staking
                    # rewards: ordinary income, nothing withheld — not
                    # foreign dividends with an assumed 15% FTC.
                    if (cfg.get("accounts", {}).get(acct)
                            or {}).get("crypto"):
                        est["staking"] += d
                    elif is_ca_listed:
                        est["div_ca"] += d
                    else:
                        est["div_foreign"] += d
                        _foreign_by_acct[acct] = (
                            _foreign_by_acct.get(acct, 0.0) + d)
                    est["pil"] += float(s.get("pil", 0) or 0)
        fees = sum(float(v) for v in (res.get("total_fees") or {}).values())
        tainted_included += int(res.get("tainted_count") or 0)
        tainted_routed += int(res.get("tainted_routed") or 0)
        # Every row foots as printed (S042-21): REALIZED is the engine's
        # gain rounded once (what the .sum and the checklist compare),
        # NON-OPT is rounded on its own, and OPTION is the cent
        # difference — rounding the three separately left rows whose
        # NON-OPT + OPTION missed REALIZED by a cent. TOTAL adds the
        # displayed REALIZED, DIVIDEND and PIL: a payment in lieu is
        # income like the .sum's GRAND TOTAL counts it (S042-22).
        _stock_r = round(cap, 2)
        _real_r = round(cap + opt, 2)
        _div_r, _pil_r = round(div, 2), round(pil, 2)
        acct_rows.append({"account": acct, "stock": _stock_r,
                          "option": round(_real_r - _stock_r, 2) + 0.0,
                          "realized": _real_r,
                          "dividend": _div_r,
                          "pil": _pil_r, "fees": round(fees, 2),
                          "total": round(_real_r + _div_r + _pil_r, 2),
                          "type": acct_types.get(acct, "")})
        if (cfg.get("accounts", {}).get(acct) or {}).get("crypto"):
            # Its DIVIDEND column is staking rewards (S023-11).
            acct_rows[-1]["dividend_is_staking"] = True

    def _sum_rows(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        # Summed from the DISPLAYED (2dp-rounded) per-account figures,
        # so every table's total row exactly equals the sum of the
        # rows above it.
        s = {k: round(sum(r[k] for r in rows), 2)
             for k in ("stock", "option", "realized", "dividend",
                       "pil", "fees", "total")}
        return s

    def _table_lines(rows: List[Dict[str, Any]],
                     total_label: str) -> List[str]:
        lines = [" ".join(header)]
        for r in rows:
            lines.append(" ".join(
                [r["account"], money(r["stock"]), money(r["option"]),
                 money(r["realized"]), money(r["dividend"]),
                 money(r["pil"]), money(r["fees"]), money(r["total"])]))
        s = _sum_rows(rows)
        lines.append(" ".join(
            [total_label, money(s["stock"]), money(s["option"]),
             money(s["realized"]), money(s["dividend"]), money(s["pil"]),
             money(s["fees"]), money(s["total"])]))
        return lines

    # Group by configured account type. Grouped tables only appear
    # when there is genuinely more than one group — a single-type
    # project (or one with no taxjson.toml types) keeps the plain
    # one-table layout.
    group_defs = [
        ("TAXABLE", [r for r in acct_rows if r["type"] == "taxable"]),
        ("SHELTERED", [r for r in acct_rows if r["type"] == "sheltered"]),
        ("UNTYPED", [r for r in acct_rows
                     if r["type"] not in ("taxable", "sheltered")]),
    ]
    group_defs = [(g, rows) for g, rows in group_defs if rows]
    grouped = len(acct_rows) > 1 and len(group_defs) > 1

    # What the return's capital-gains entry asks for: taxable accounts
    # only, on form-export's convention, so these can never disagree with
    # the export. Canada: one row per Schedule 3 line (shares 13199/13200,
    # options & other properties 15199/15300, crypto-assets 15200/15301).
    # USA: Form 8949's own part totals — (d) proceeds, (e) cost, (g)
    # adjustment, (h) gain.
    from taxjson.bin.taxjson_form_export import (filing_lines,
                                                 filing_parts_8949,
                                                 filing_totals,
                                                 load_dispositions,
                                                 mark_crypto)
    _settings = cfg.get("settings") or {}
    _fyear = year or _settings.get("year")
    _is_us = _country(_settings) \
        in ("us", "usa")
    # The date the gains files were scoped on — an explicit tax_date
    # that differs from the country default dropped a year-end sale
    # from FOR THE RETURN (R1-200).
    _date_key = ("date" if _tax_date_basis(_settings) == "trade"
                 else "date_settle")
    filing_rows: List[Dict[str, Any]] = []
    _filing_ents: List[Dict[str, Any]] = []
    for acct, p in files.items():
        if acct not in taxable_accounts:
            continue
        try:
            _ents, _ = load_dispositions([p], _fyear, _date_key)
        except (OSError, ValueError) as e:
            _die(f"{acct}: could not read dispositions from {p}: {e} — "
                 f"re-run `taxjson run` to rebuild it (the FOR THE "
                 f"RETURN block cannot leave a taxable account out).")
        if ((cfg.get("accounts") or {}).get(acct) or {}).get("crypto"):
            _ents = mark_crypto(_ents)
        _filing_ents += _ents
        if _is_us:
            try:
                _parts = filing_parts_8949(_ents)
            except SystemExit as e:
                print(f"taxjson sum: warning: {acct}: {e}", file=sys.stderr)
                continue
            filing_rows.append({"account": acct, **{
                k: round(sum(x[k] for x in _parts), 2)
                for k in ("proceeds", "cost", "adjustment", "gain")},
                "dispositions": len(_ents)})
        else:
            filing_rows.append({"account": acct,
                                **filing_totals(_ents, _fyear)})
    filing_line_rows: List[Dict[str, Any]] = []
    if _is_us:
        try:
            filing_line_rows = filing_parts_8949(_filing_ents)
        except SystemExit:
            filing_line_rows = []           # warned per account above
        _fkeys = ("proceeds", "cost", "adjustment", "gain")
    else:
        filing_line_rows = filing_lines(_filing_ents, _fyear)
        _fkeys = ("proceeds", "acb", "outlays", "gain", "denied")
    filing_total = {k: round(sum(r[k] for r in filing_line_rows), 2)
                    for k in _fkeys}
    if not _is_us:
        # The part of DENIED that no replacement's ACB ever recovers: a
        # registered-account (affiliated) acquisition (s.40(2)(g)(i)).
        # The footer said every denied amount goes onto the
        # replacement's ACB (S043-02, S048-04).
        filing_total["permanently_denied"] = round(sum(
            float(e.get("permanently_disallowed") or 0.0)
            for e in _filing_ents), 2)
    # The RETURN row sums the per-row cents, as filed; the gains files,
    # wash-sales and audit total the unrounded engine values — a few
    # cents apart on a large year (R1-166). Shown, not hidden.
    _engine_gain = round(sum(float(e.get("gain") or 0.0)
                             for e in _filing_ents), 2)
    _round_gap = (round(filing_total.get("gain", 0.0) - _engine_gain, 2)
                  if filing_line_rows else 0.0)
    # FX on foreign cash (s.39(1.1)) is reported on line 15300 too
    # (T4037) but lives outside the engine's dispositions; show the
    # estimate beside the block when the ledger builds, else a pointer.
    _fx_note: Optional[Dict[str, Any]] = None
    _fx_err = ""
    if filing_rows and not _is_us:
        import contextlib as _ctx
        import io as _io
        _fx_buf = _io.StringIO()
        try:
            with _ctx.redirect_stderr(_fx_buf):
                _fxl, _fxv, _, _, _ = _fx_cash_doc(root, cache)
            _fx_note = {"net_gain": round(float(_fxl["net_gain"]), 2),
                        "reportable": round(float(_fxv["reportable"]), 2),
                        "estimate": True, "line": "15300",
                        # The ledger cannot see conversions or deposits
                        # (R1-148): carry its own warning signs so the
                        # figure is never quoted without them.
                        "overdrafts": dict(_fxl.get("overdrafts") or {}),
                        "pools_year_end": dict(
                            _fxl.get("pools_year_end") or {}),
                        "caveat": "explicit conversions and deposits "
                                  "are not in the ledger; the figure "
                                  "can be wrong in either direction"}
        except SystemExit as e:
            # The ledger refused (an unreadable or missing native tx
            # file): say so instead of a silent pointer (S005-04).
            _fx_note, _fx_err = None, str(e.code or "")
        except Exception as e:                      # noqa: BLE001
            _fx_note, _fx_err = None, f"{type(e).__name__}: {e}"
        _fx_warn = [ln for ln in _fx_buf.getvalue().splitlines()
                    if "warning" in ln.lower()]
        if _fx_err or _fx_warn:
            print(f"taxjson {_CURRENT_CMD or 'sum'}: warning: FX-on-cash "
                  f"(line 15300) estimate "
                  + ("omitted — " + _fx_err if _fx_err else
                     "built with warnings — " + "; ".join(_fx_warn)),
                  file=sys.stderr)
    # Base currency is just a label here — soft-read, no hard config
    # dependency (the command works from the work/ gains files).
    base = _base_currency(root)
    if tainted_included:
        # In-line tainted rows (raw engine output): counted in totals.
        print(f"taxjson sum: warning: totals include {tainted_included} "
              f"tainted disposition(s) with phantom cost basis — "
              f"form-export/carryover exclude them, so filing totals "
              f"will differ.", file=sys.stderr)
    if tainted_routed:
        # Pipeline files: tainted rows were STRIPPED to the
        # manual_reporting_required section — totals exclude them, and
        # without this line `sum` gave no signal at all (round-five
        # audit finding: the warning was dead code for pipeline
        # files).
        print(f"taxjson sum: warning: {tainted_routed} tainted "
              f"disposition(s) were routed to manual reporting — "
              f"these totals EXCLUDE them (`taxjson form-export` "
              f"lists them in its MANUAL REPORTING section; report "
              f"them by hand).",
              file=sys.stderr)
    # Scope: unlike the filing commands (carryover/t1135/form-export,
    # taxable-only by law), this summary rolls up EVERY account — say so
    # when sheltered accounts contribute, or the totals look like filing
    # numbers they aren't.
    sheltered_included = []
    try:
        acct_cfg = (load_config(root).get("accounts", {})
                    if (root / "taxjson.toml").exists() else {})
        sheltered_included = sorted(
            a for a in files
            if acct_cfg.get(a, {}).get("type") == "sheltered")
    except SystemExit:
        pass
    if getattr(args, "json", False):
        doc: Dict[str, Any] = {
            "accounts": acct_rows,
            "tainted_included": tainted_included,
            "tainted_routed": tainted_routed,
            # Same row-sum path as the printed tables and subtotals, so
            # totals == Σ subtotals == Σ rows holds exactly for machine
            # consumers (the unrounded accumulation drifted by a cent).
            "totals": _sum_rows(acct_rows),
            "basis": basis, "year": year, "currency": base,
            "filing": {"accounts": filing_rows, "totals": filing_total,
                       ("parts_8949" if _is_us else "lines"):
                           filing_line_rows,
                       "fx_cash": _fx_note,
                       "engine_gain_unrounded": _engine_gain,
                       "date_basis": _date_key},
            "sheltered_included": sheltered_included,
            "run_state_problems": _run_state,
            "subtotals": {g.lower(): _sum_rows(rows)
                          for g, rows in group_defs}}
        if want_estimate:
            doc["estimate"] = _tax_estimate_result(
                cfg, est,
                other_income=_oi, other_losses=_ol,
                deductions=_ded, carrying_charges=_cc,
                province=getattr(args, "province", None),
                actual_withheld=_actual_withholding(
                    cache, set(files) & taxable_accounts,
                    year or (cfg.get("settings") or {}).get("year"),
                    _foreign_by_acct))
            _iyear = year or (cfg.get("settings") or {}).get("year")
            _idoc = _instalments_doc(
                root, doc["estimate"], _iyear,
                _instalment_config(root, _iyear))
            if _idoc:
                doc["instalments"] = _idoc
        _json_out(doc)
        return

    print(f"REALIZED-GAINS SUMMARY — {base}, tax year {year}, "
          f"basis: {basis}  "
          f"(REALIZED = NON-OPT + OPTION capital gain; NON-OPT = "
          f"shares, units, futures and crypto; "
          f"TOTAL = REALIZED + DIVIDEND + PIL)")
    if sheltered_included:
        _filing = ("carryover/form-export"
                   if _is_us
                   else "carryover/t1135/form-export")
        print(f"NOTE: totals include sheltered account(s) "
              f"{', '.join(sheltered_included)} — not taxable events; "
              f"{_filing} exclude them.")
    print()
    if grouped:
        for gname, rows in group_defs:
            print(f"{gname} ACCOUNTS")
            _print_report_table(_table_lines(rows, "SUBTOTAL"),
                                rule_before_last=True)
            print()
        print("ALL ACCOUNTS")
    _print_report_table(_table_lines(acct_rows, "TOTAL"),
                        rule_before_last=True)
    _stk = [r for r in acct_rows
            if r.get("dividend_is_staking") and abs(r["dividend"]) >= 0.005]
    if _stk:
        # The crypto rows' DIVIDEND is staking rewards: ordinary income
        # (no gross-up/credit, no withholding), as the estimate treats
        # it — not a figure for the dividend lines (S023-11).
        print(f"NOTE: DIVIDEND for crypto account(s) "
              + ", ".join(f"{r['account']} ({money(r['dividend'])})"
                          for r in _stk)
              + " is STAKING rewards — ordinary income, not dividends.")

    if filing_rows:
        from taxjson.lib.report_model import render_table as _rt
        _names = ", ".join(r["account"] for r in filing_rows)
        print()
        if _is_us:
            print(f"FOR THE RETURN — taxable accounts ({_names}), {base} "
                  f"(Form 8949 → Schedule D, tax year {_fyear})")
            _body = [[f"{r['label']} → {r['schedule_d']}",
                      money(r["proceeds"]), money(r["cost"]),
                      money(r["adjustment"]), money(r["gain"])]
                     for r in filing_line_rows]
            _foot = [["RETURN", money(filing_total["proceeds"]),
                      money(filing_total["cost"]),
                      money(filing_total["adjustment"]),
                      money(filing_total["gain"])]]
            for _ln in _rt(["FORM 8949", "(d) PROCEEDS", "(e) COST",
                            "(g) ADJUSTMENT", "(h) GAIN"],
                           ["<", ">", ">", ">", ">"], _body, _foot):
                print(_ln)
            print("(d) − (e) + (g) = (h). Column (g) is the code-W wash-"
                  "sale loss disallowed and added back, so (h) is the "
                  "allowed gain; the disallowed loss moves to the "
                  "replacement shares' basis. Per-sale rows: `taxjson "
                  "form-export`.")
            if abs(_round_gap) >= 0.005:
                print(f"Rows are rounded to the cent, as filed: the gains "
                      f"files' unrounded total gain is "
                      f"{money(_engine_gain)} ({_round_gap:+,.2f} on the "
                      f"RETURN row).")
        else:
            print(f"FOR THE RETURN — taxable accounts ({_names}), {base} "
                  f"(Schedule 3, tax year {_fyear})")
            _body = [[f"Line {r['line']} {r['short']} "
                      f"({r['proceeds_code']}/{r['gain_code']})"
                      if r["line"] else
                      f"{r['short']} ({r['proceeds_code']}/"
                      f"{r['gain_code']})",
                      money(r["proceeds"]), money(r["acb"]),
                      money(r["outlays"]), money(r["gain"]),
                      money(r["denied"])] for r in filing_line_rows]
            _foot = [["RETURN", money(filing_total["proceeds"]),
                      money(filing_total["acb"]),
                      money(filing_total["outlays"]),
                      money(filing_total["gain"]),
                      money(filing_total["denied"])]]
            for _ln in _rt(["SCHEDULE 3 LINE", "PROCEEDS", "COST(ACB)",
                            "OUTLAYS", "GAIN", "DENIED"],
                           ["<", ">", ">", ">", ">", ">"], _body, _foot):
                print(_ln)
            _permd = filing_total.get("permanently_denied") or 0.0
            print("PROCEEDS − COST(ACB) − OUTLAYS = GAIN, the allowed gain. "
                  "A short sale shows what it brought in as PROCEEDS and "
                  "the cover as ACB, and sell-side commissions are "
                  "outlays, as on the form. Where a "
                  "superficial loss was DENIED the ACB is REDUCED by it, "
                  "so the gain stays the allowed one; a deferred denial "
                  "is added to the ACB of the replacement property, but "
                  "one caused by a registered-account (affiliated) "
                  "acquisition is lost for good — no ACB addition"
                  + (f" ({money(_permd)} of the DENIED total)"
                     if _permd > 0.005 else "")
                  + ". Per-security rows: `taxjson form-export`; "
                  "per account: `taxjson sum --json`.")
            if abs(_round_gap) >= 0.005:
                print(f"Rows are rounded to the cent, as filed: the gains "
                      f"files' unrounded total gain is "
                      f"{money(_engine_gain)} ({_round_gap:+,.2f} on the "
                      f"RETURN row).")
            if _fx_note is not None:
                print(f"FX on foreign cash (s.39(1.1), ESTIMATE — not in "
                      f"the rows above): net {money(_fx_note['net_gain'])}, "
                      f"reportable {money(_fx_note['reportable'])} after "
                      f"the $200 exemption; T4037 puts it on line 15300. "
                      + (f"The ledger overdrew "
                         + ", ".join(f"{c} {n}x" for c, n in sorted(
                             _fx_note["overdrafts"].items()))
                         + " (conversions/deposits it cannot see); "
                         if _fx_note["overdrafts"] else "")
                      + "unseen conversions make it wrong in either "
                        "direction — review with `taxjson fx-cash` "
                        "before using it.")
            else:
                print("FX on foreign cash (s.39(1.1)) is not in the rows "
                      "above — T4037 puts it on line 15300; see `taxjson "
                      "fx-cash`.")
            # Slip capital gains are part of line 19700 too (R1-44).
            print("Capital gains on T3 (box 21) and T5/T5013 (box 18) "
                  "slips are not in the rows above — Schedule 3 lines "
                  "17600 and 17400, entered from the slips (the books "
                  "carry those distributions as dividends).")

    if want_estimate:
        _print_tax_estimate(
            cfg, est, base,
            other_income=_oi, other_losses=_ol,
            deductions=_ded, carrying_charges=_cc,
            province=getattr(args, "province", None),
            verbose=getattr(args, "verbose", False),
            actual_withheld=_actual_withholding(
                cache, set(files) & taxable_accounts,
                year or (cfg.get("settings") or {}).get("year"),
                _foreign_by_acct),
            root=root,
            year=year or (cfg.get("settings") or {}).get("year"))


def _instalment_config(root: Path,
                       year: Optional[int] = None) -> Dict[str, Any]:
    """Validated [instalments] table ({} when absent). Loud on bad
    input: these figures move money."""
    from taxjson.bin.taxjson_instalments import BASES
    _warn_config_tables(root)
    cfg = (_soft_config(root).get("instalments") or {})
    if not cfg:
        return {}
    out: Dict[str, Any] = {}
    basis = str(cfg.get("basis") or "current_year")
    if basis not in BASES:
        _die(f"[instalments] basis must be one of "
             f"{', '.join(BASES)}, got {basis!r}")
    out["basis"] = basis
    for key in ("prior_year_net_tax", "second_prior_net_tax",
                "withheld", "prescribed_rate"):
        v = cfg.get(key)
        if v is None:
            continue
        # Sign, finiteness and type, like paid[].amount: withheld = -5000
        # overstated net tax owing by 10,000, withheld = nan waived the
        # instalments, a negative rate zeroed the interest, and `true`
        # read as 1.0 — all at exit 0 (R1-217).
        out[key] = _nonneg_money(v, f"[instalments] {key}")
    # CRA resets the prescribed rate quarterly and charges each day at
    # the rate in force that day — so a dated schedule is accepted and
    # applied per day. A scalar prescribed_rate stays valid.
    if (cfg.get("prescribed_rate") is not None
            and cfg.get("prescribed_rates") is not None):
        _die("[instalments] set prescribed_rate OR prescribed_rates, "
             "not both — the dated schedule would silently win and "
             "the single rate be discarded.")
    if out.get("prescribed_rate", 0) >= 1.0:
        _die(f"[instalments] prescribed_rate is a DECIMAL fraction "
             f"(0.08 = 8%), got {out['prescribed_rate']} — that would "
             f"charge {out['prescribed_rate'] * 100:.0f}% a year.")
    if cfg.get("prescribed_rates") is not None:
        if not isinstance(cfg.get("prescribed_rates"), list):
            _die("[instalments] prescribed_rates must be a LIST of "
                 "{ from, rate } tables.")
        sched = []
        for i, row in enumerate(cfg.get("prescribed_rates") or [], 1):
            if not isinstance(row, dict):
                _die(f"[instalments] prescribed_rates[{i}] must be a "
                     f"table like {{ from = \"2026-07-01\", "
                     f"rate = 0.09 }}")
            try:
                # fromisoformat, not strptime: strptime ACCEPTS
                # unpadded "2026-9-01", which then sorts and compares
                # wrong as a string ("2026-9-01" > "2026-12-31"), so
                # the segment silently applied to the wrong window.
                frm = date_cls.fromisoformat(str(row["from"])).isoformat()
                r = _nonneg_money(row["rate"],
                                  f"[instalments] prescribed_rates[{i}]"
                                  f".rate")
                if r >= 1.0:
                    _die(f"[instalments] prescribed_rates[{i}].rate is "
                         f"a DECIMAL fraction (0.08 = 8%), got {r}.")
                sched.append({"from": frm, "rate": r})
            except (KeyError, TypeError, ValueError):
                _die(f"[instalments] prescribed_rates[{i}] needs a "
                     f"YYYY-MM-DD `from` and a numeric `rate`, got "
                     f"{row!r}")
        if not sched:
            _die("[instalments] prescribed_rates is empty — remove it "
                 "or give at least one { from, rate } entry.")
        out["prescribed_rates"] = sorted(sched,
                                         key=lambda x: x["from"])
    if basis == "prior_year" and "prior_year_net_tax" not in out:
        _die("[instalments] basis 'prior_year' needs "
             "prior_year_net_tax (last year's net tax owing).")
    if basis == "cra_reminder" and not (
            "prior_year_net_tax" in out
            and "second_prior_net_tax" in out):
        _die("[instalments] basis 'cra_reminder' needs both "
             "prior_year_net_tax and second_prior_net_tax (the two "
             "preceding years' net tax owing).")
    payments = []
    if cfg.get("paid") is not None and not isinstance(cfg.get("paid"),
                                                      list):
        _die("[instalments] paid must be a LIST of "
             "{ date, amount } tables.")
    for i, row in enumerate(cfg.get("paid") or [], 1):
        if not isinstance(row, dict):
            _die(f"[instalments] paid[{i}] must be a table like "
                 f"{{ date = \"2026-03-15\", amount = 15000 }}")
        d, a = row.get("date"), row.get("amount")
        try:
            # fromisoformat (see prescribed_rates): the downstream
            # walk parses with it, so an unpadded date accepted here
            # crashed later behind a misleading error.
            d = date_cls.fromisoformat(str(d)).isoformat()
            if isinstance(a, bool) or not isinstance(a, (int, float)):
                # A TOML number only: "5_000" read as 5000 (S043-05).
                raise TypeError("not a number")
            a = float(a)
            import math as _math
            if not _math.isfinite(a):
                raise ValueError("non-finite amount")
        except (TypeError, ValueError):
            _die(f"[instalments] paid[{i}] needs a YYYY-MM-DD `date` "
                 f"and a numeric `amount`, got {row!r}")
        if a < 0:
            _die(f"[instalments] paid[{i}] amount is negative "
                 f"({a}) — instalments are payments TO CRA; adjust "
                 f"the amount instead of recording a reversal.")
        if year:
            # Year-rollover trap: leaving LAST year's payments in the
            # table after bumping [settings] year made the schedule
            # read "met" with zero interest and fabricated credit
            # interest — silently the maximally wrong answer.
            lo, hi = f"{year}-01-01", f"{int(year) + 1}-04-30"
            if not (lo <= d <= hi):
                _die(f"[instalments] paid[{i}] is dated {d}, outside "
                     f"tax year {year} ({lo}..{hi}). Instalments are "
                     f"per year — move it to that year's project or "
                     f"remove it.")
        row_out = {"date": d, "amount": a}
        if row.get("note"):
            row_out["note"] = str(row["note"])
        payments.append(row_out)
    out["paid"] = sorted(payments, key=lambda p: p["date"])
    return out


def _net_tax_owing(r: Dict[str, Any], withheld: float) -> float:
    """Instalments are computed on NET TAX OWING — total tax for the
    year (not the incremental investment-income figure) minus amounts
    withheld at source, with any AMT top-up included."""
    total = float((r.get("tax_with") or {}).get("total") or 0.0)
    total += float((r.get("amt") or {}).get("topup") or 0.0)
    return max(0.0, total - withheld)


def _instalments_doc(root: Path, r: Dict[str, Any], year,
                     icfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the instalment picture from an estimate result. None when
    the project declares no [instalments] table."""
    from taxjson.bin import taxjson_instalments as INST
    if not icfg or not year:
        return None
    # Canada-only regime: the US path (1040-ES) differs in dates,
    # safe harbours and penalty basis, and its estimate total omits
    # NIIT — so a US project must not receive a Canadian doc, not
    # even through `sum --estimate --json`.
    if _country(_soft_settings(root)) \
            not in ("canada", "ca"):
        return None
    net = _net_tax_owing(r, float(icfg.get("withheld") or 0.0))
    # None (no rate configured) -> CRA's published quarterly rates.
    rate = (icfg.get("prescribed_rates")
            if icfg.get("prescribed_rates")
            else icfg.get("prescribed_rate"))
    doc = INST.build(
        year=int(year), basis=icfg["basis"], current_net_tax=net,
        payments=icfg.get("paid") or [], annual_rate=rate,
        prior_net_tax=icfg.get("prior_year_net_tax"),
        second_prior_net_tax=icfg.get("second_prior_net_tax"))
    doc["rate_configured"] = bool(icfg.get("prescribed_rates")) or (
        icfg.get("prescribed_rate") is not None)
    return doc


def cmd_instalments(args: argparse.Namespace) -> None:
    """`taxjson instalments`: the year's instalment schedule, what each
    date calls for, what has been paid, and the offset interest /
    s.163.1 penalty that follows from the gap. The current-year basis
    is driven by `taxjson estimate` itself (AMT included), so the two
    commands can never disagree."""
    import json as _json
    from taxjson.bin import taxjson_instalments as INST
    from taxjson.lib.dispatch import run_cmd as _run
    root = Path(args.dir).resolve()
    icfg = _instalment_config(root, _soft_settings(root).get("year"))
    if not icfg:
        _die("no [instalments] section in taxjson.toml. Example:\n"
             "  [instalments]\n"
             "  basis = \"current_year\"      "
             "# current_year | prior_year | cra_reminder\n"
             "  prescribed_rate = 0.07      "
             "# optional: CRA's published rates are built in\n"
             "  withheld = 0                "
             "# tax already withheld at source\n"
             "  paid = [{ date = \"2026-03-15\", amount = 15000 }]")
    settings = _soft_settings(root)
    year = settings.get("year")
    base = str(_base(settings))
    if _country(settings) \
            not in ("canada", "ca"):
        _die("instalments are modeled for canada only (US estimated "
             "taxes use a different regime — see KNOWN_ISSUES).")
    _oi, _ol = _estimate_inputs(root, args)
    _ded, _cc = _estimate_deductions(root, args)
    _argv = [sys.executable, "-m", "taxjson.bin.taxjson_run",
             "-C", str(root), "estimate", "--json"]
    if _oi:
        _argv += ["--other-income", repr(_oi)]
    if _ol:
        _argv += ["--other-losses", repr(_ol)]
    if _ded:
        _argv += ["--deductions", repr(_ded)]
    if _cc:
        _argv += ["--carrying-charges", repr(_cc)]
    res = _run(_argv, capture_output=True)
    if res.returncode != 0:
        _die(f"could not compute the estimate it builds on: "
             f"{(res.stderr or '').strip()[:400]}")
    # The estimate's own warnings (an unreadable or stale-year gains
    # file, tainted sales EXCLUDED, a run that failed) are caveats on
    # every figure below: relay them, never swallow them (S005-05,
    # S007-04, S043-17, S047-22).
    if (res.stderr or "").strip():
        sys.stderr.write(res.stderr if res.stderr.endswith("\n")
                         else res.stderr + "\n")
    r = (_json.loads(res.stdout) or {}).get("estimate") or {}
    if not r:
        _die("the estimate produced no result — run `taxjson run` "
             "first, and set [settings] province.")
    doc = _instalments_doc(root, r, year, icfg)
    if doc is None:
        _die("no `year` under [settings] in taxjson.toml — the "
             "instalment schedule is per tax year.")
    # A year the built-in tables do not cover (a 2023 project on the
    # 2024 tables and the post-2024 AMT) must say so here too (S043-16).
    from taxjson.lib.tax_estimate import vintage_notes
    doc["vintage"] = r.get("vintage")
    doc["vintage_notes"] = (vintage_notes(year, str(r.get("vintage")))
                            if r.get("vintage") else [])
    # The verdict rests on TOTAL net tax owing. Unset other income and
    # withholding were read as 0 without a word, so an employee was
    # told "no instalments required" on the investment tax alone
    # (S041-00): name what was assumed.
    _est_cfg = _soft_config(root).get("estimate") or {}
    _assumed = []
    if (getattr(args, "other_income", None) is None
            and "other_income" not in _est_cfg):
        _assumed.append("other income (employment, pension, business) "
                        "is not set — assumed 0: set [estimate] "
                        "other_income (or pass --other-income)")
    if "withheld" not in icfg:
        _assumed.append("tax withheld at source is not set — assumed 0: "
                        "set [instalments] withheld")
    doc["assumed_zero"] = _assumed
    # Not modelled at all (S043-15, S048-12): CPP/EI payable on
    # self-employment earnings is part of the instalment amount CRA
    # asks for (not of net tax owing or the $3,000 test), and the FX
    # result on foreign cash (line 15300) is outside the estimate.
    doc["not_modelled"] = [
        "CPP/EI payable on self-employment earnings (T1 lines 42100 / "
        "42120) — CRA adds it to the instalments due; add it yourself",
        "FX gains/losses on foreign cash (s.39(1.1), line 15300 — "
        "`taxjson fx-cash`) and capital gains on T3/T5 slips are not in "
        "the estimate this schedule is built on"]
    if getattr(args, "json", False):
        _json_out(doc)
        return
    print(INST.render(doc, base))
    if _assumed:
        print()
        for _n in _assumed:
            print(_wrap_note("NOTE: " + _n + " — the figures above cover "
                             "the investment income only."))
    print()
    for _n in doc["not_modelled"]:
        print(_wrap_note("NOT MODELLED: " + _n + "."))
    if doc["vintage_notes"]:
        print()
        for _n in doc["vintage_notes"]:
            print(_wrap_note("NOTE: " + _n))


def cmd_estimate(args: argparse.Namespace) -> None:
    """`taxjson estimate`: the realized-gains summary table followed by
    the tax ESTIMATE for the year's investment income — the same code
    path `sum` uses for its estimate block (the two can never
    disagree): tax(other income + investment income) minus
    tax(other income), on the filing (wash-adjusted) basis, FTC from
    the books' actual TAX rows. ESTIMATE ONLY — never filing
    numbers."""
    args.estimate = True
    cmd_summary(args)


def _wrap_note(text: str, indent: str = "  ") -> str:
    """Report prose wrapped to the house 78-column width — the AMT
    explanation and the assumptions footer ran off the edge on any
    terminal while every table beside them was capped."""
    import textwrap
    return textwrap.fill(text, width=78, initial_indent=indent,
                         subsequent_indent=indent)


def _trace_bracket_rows(brackets, ti_base: float, ti_with: float):
    """[(band label, base tax, with tax)] over the WITH run's slices,
    matched to the base run's amounts band-by-band — the two-column
    body of the --verbose trace."""
    from taxjson.lib.tax_estimate import bracket_slices
    base_by_lo = {lo: tax for lo, _hi, _r, tax
                  in bracket_slices(ti_base, brackets)}
    rows = []
    for lo, hi, rate, tax in bracket_slices(ti_with, brackets):
        hi_s = "inf" if hi == float("inf") else f"{hi:,.0f}"
        rows.append((f"{lo:,.0f}-{hi_s} @ {rate * 100:.2f}%",
                     base_by_lo.get(lo, 0.0), tax))
    return rows


def _print_trace_table(rows, money) -> None:
    for label, b, w in rows:
        print(f"    {label:<34}{money(b):>14}{money(w):>14}")


def _actual_withholding(cache: Path, taxable_accounts, year,
                        foreign_by_account=None) -> Optional[float]:
    """Net TAX withheld (base currency) across the taxable accounts'
    BASE books for the tax year — positive = withheld, refunds net.
    The base books, not the gains files: the gains engine does not
    carry TAX rows through, so reading its output silently found
    nothing and the estimator always fell back to the flat 15%
    assumption. None when no TAX rows exist at all (no data is not
    the same as no tax)."""
    import json as _json
    ystr = str(year or "")
    total = 0.0
    seen = False
    # Only some parsers emit TAX rows (IB and RBC do; Questrade does
    # not), so an account with foreign dividends and no TAX rows must
    # fall back to the treaty ASSUMPTION for its own share — treating
    # one broker's withholding as the credit for the whole book
    # systematically under-credited.
    from taxjson.lib.tax_estimate import CA_FOREIGN_WITHHOLDING
    foreign_by_account = foreign_by_account or {}
    # Sorted, and summed exactly: callers pass a SET, whose order made
    # the unrounded FTC differ in its last digit from run to run
    # (S043-20).
    import math as _math
    parts: List[float] = []
    for acct in sorted(taxable_accounts):
        p = Path(cache) / f"{acct}_base.json"
        try:
            data = _read_work_doc(p)
        except (OSError, ValueError) as e:
            # Skipping the account dropped its withholding AND its 15%
            # fallback while its foreign dividends were still taxed
            # (R1-223): fall back for it, and say so.
            if foreign_by_account.get(acct):
                print(f"taxjson: warning: could not read {p.name} "
                      f"({e}) — assuming {CA_FOREIGN_WITHHOLDING:.0%} "
                      f"withholding on {acct}'s foreign dividends; "
                      f"re-run `taxjson run`.", file=sys.stderr)
            data = {}
        acct_parts: List[float] = []
        acct_seen = False
        for t in data.get("transactions", []):
            if t.get("action") != "TAX":
                continue
            d = str(t.get("date") or "")
            if ystr and not d.startswith(ystr):
                continue
            acct_seen = True
            acct_parts.append(float(t.get("net_amount") or 0.0))
        if acct_seen:
            seen = True
            parts.extend(acct_parts)
        else:
            parts.append(CA_FOREIGN_WITHHOLDING
                         * float(foreign_by_account.get(acct) or 0.0))
    total = _math.fsum(parts)
    return max(0.0, total) if seen else None


def _tax_estimate_result(cfg: Dict[str, Any], est: Dict[str, float], *,
                         other_income: float, other_losses: float,
                         province: Optional[str],
                         actual_withheld: Optional[float] = None,
                         deductions: float = 0.0,
                         carrying_charges: float = 0.0
                         ) -> Dict[str, Any]:
    """Resolve country/province and run the estimator — shared by the
    text block and `sum --json` so the two can never disagree. For usa,
    un-termed gains are folded into ST (conservative) with a stderr
    note, and the ST input (post-fold, pre-loss) rides along as
    `st_input` for display."""
    from taxjson.lib.tax_estimate import estimate_canada, estimate_usa
    settings = cfg.get("settings", {})
    _est_year = settings.get("year")
    country = _country(settings)
    if country == "canada":
        prov = (province or str(settings.get("province", "") or "")).strip()
        if not prov:
            _die("the canada estimate needs a province — pass "
                 "--province ON|BC|AB or set `province` under "
                 "[settings] in taxjson.toml.")
        try:
            return estimate_canada(realized=est["realized"],
                                   year=_est_year,
                                   eligible_div=est["div_ca"],
                                   foreign_div=est["div_foreign"],
                                   pil=est["pil"],
                                   other_income=other_income,
                                   other_losses=other_losses,
                                   province=prov,
                                   actual_withheld=actual_withheld,
                                   staking=est.get("staking", 0.0),
                                   deductions=deductions,
                                   carrying_charges=carrying_charges)
        except ValueError as e:
            _die(str(e))
    if deductions or carrying_charges:
        _die("--deductions/--carrying-charges are modelled for the "
             "canada estimate only (the US estimate is experimental and "
             "uses the standard deduction).")
    unterm = est["realized"] - est["st"] - est["lt"]
    st_in = est["st"]
    if abs(unterm) > 0.01:
        st_in += unterm
        print(f"taxjson sum: note: {fmt_money(unterm)} of gains carry no "
              f"ST/LT term — treated as SHORT-TERM (conservative); "
              f"re-run `taxjson run` to refresh.", file=sys.stderr)
    r = estimate_usa(st=st_in, lt=est["lt"], year=_est_year,
                     qualified_div=est["div_ca"] + est["div_foreign"],
                     pil=est["pil"] + est.get("staking", 0.0),
                     other_income=other_income,
                     other_losses=other_losses)
    r["st_input"] = round(st_in, 2)
    return r


def _print_tax_estimate(cfg: Dict[str, Any], est: Dict[str, float],
                        base_cur: str, *, other_income: float,
                        other_losses: float,
                        province: Optional[str],
                        deductions: float = 0.0,
                        carrying_charges: float = 0.0,
                        verbose: bool = False,
                        actual_withheld: Optional[float] = None,
                        root: Optional[Path] = None,
                        year: Optional[Any] = None) -> None:
    """Marginal tax-estimate block under `taxjson sum` — TAXABLE
    accounts only, incremental on top of --other-income. Assumptions
    are printed with the numbers; these are never filing figures.
    `verbose` appends the CALCULATION TRACE: every bracket slice,
    credit and surtax tier for the base and with-investments runs."""
    money = fmt_money
    r = _tax_estimate_result(cfg, est, other_income=other_income,
                             other_losses=other_losses, province=province,
                             actual_withheld=actual_withheld,
                             deductions=deductions,
                             carrying_charges=carrying_charges)
    # The result carries the vintage apply_vintage() actually selected
    # for the project year — never the import-time module default.
    RATE_VINTAGE = r.get("vintage", "?")
    print()
    if r["country"] == "canada":
        print(f"TAX ESTIMATE — canada/{r['province']}, rates vintage "
              f"{RATE_VINTAGE} (ESTIMATE ONLY, not filing numbers; "
              f"taxable accounts only)")
        print()
        rows = [
            ("Other income", other_income, ""),
            ("Capital gains (taxable)", r["taxable_gain"],
             f"[{money(est['realized'])} realized - "
             f"{money(r['losses_applied'])} other losses, x50%]"),
            ("Eligible dividends (grossed)", r["grossed_eligible"],
             f"[{money(est['div_ca'])} x1.38, Canadian-listed]"),
            ("Foreign dividends", est["div_foreign"],
             f"[FTC {money(r['ftc_assumed'])} — "
             f"{r.get('ftc_source', 'assumed 15%')}]"),
            ("Payments in lieu", est["pil"], ""),
        ] + ([("Crypto staking (ordinary)", r["staking"],
               "[no withholding, no FTC]")]
             if r.get("staking") else []) \
          + ([("Deductions", -r["deductions"],
               "[lines 20700-23500, e.g. RRSP 20800; in full under AMT]")]
             if r.get("deductions") else []) \
          + ([("Carrying charges", -r["carrying_charges"],
               "[line 22100; 50% under AMT]")]
             if r.get("carrying_charges") else [])
        for label, amt, note in rows:
            print(f"  {label:<30}{money(amt):>14}"
                  + (f"  {note}" if note else ""))
        print()
        print(f"  Tax with investments: {money(r['tax_with']['total'])} "
              f"(federal {money(r['tax_with']['federal'])} + "
              f"{r['province']} {money(r['tax_with']['provincial'])})")
        print(f"  Tax on other income alone: "
              f"{money(r['tax_base']['total'])}")
        print(f"  => ESTIMATED TAX ON INVESTMENT INCOME: "
              f"{money(r['estimated_tax'])} {base_cur}"
              + (f"  ({r['avg_rate_pct']:.1f}% of "
                 f"{money(r['investment_income'])})"
                 if r["avg_rate_pct"] is not None else ""))
        if r["estimated_tax"] < -0.005:
            # Signed (R1-47): eligible dividends at a low bracket earn
            # more credit than the tax on their grossed-up amount.
            print(_wrap_note(
                f"Negative = a saving: the investment income lowers the "
                f"tax on the other income by "
                f"{money(-r['estimated_tax'])} (the dividend tax credit "
                f"exceeds the tax on the grossed-up dividends)."))
        if r["losses_unused"]:
            print(f"  Unused capital losses: {money(r['losses_unused'])} "
                  f"(carry forward)")
        amt = r.get("amt")
        if amt:
            print()
            print(f"  AMT CHECK — post-2024 minimum tax "
                  f"({amt['rate'] * 100:.1f}% over "
                  f"{money(amt['exemption'])} exemption)")
            print(f"  {'Adjusted taxable income':<30}"
                  f"{money(amt['adjusted_income']):>14}")
            print(f"  {'Federal minimum tax':<30}"
                  f"{money(amt['minimum_fed']):>14}")
            print(f"  {'Federal regular tax':<30}"
                  f"{money(amt['regular_fed']):>14}")
            if amt["binding"]:
                print(f"  {'=> AMT TOP-UP':<30}"
                      f"{money(amt['topup']):>14}")
                print(f"  {'   federal / ' + r['province']:<30}"
                      f"{money(amt['excess_fed']) + ' / ' + money(amt['provincial_amt']):>14}")
                print(f"  {'=> TOTAL WITH AMT':<30}"
                      f"{money(r['estimated_tax_with_amt']):>14} "
                      f"{base_cur}")
                print(_wrap_note(
                    f"AMT binds because capital gains enter at 100% "
                    f"(vs 50%) and the dividend tax credit is denied. "
                    f"The federal excess ({money(amt['carryforward'])}) "
                    f"is creditable against REGULAR tax for 7 years, "
                    f"but recovery needs a future year where regular "
                    f"tax exceeds the minimum — gains-heavy, "
                    f"salary-light years keep hitting AMT instead."))
            else:
                print(f"  {'=> does not bind':<30}"
                      f"{money(amt['headroom']):>14}  [headroom]")
                print(_wrap_note(
                    "AMT recomputes with capital gains at 100% (vs "
                    "50%) and the dividend tax credit denied; on "
                    "these numbers regular tax still exceeds the "
                    "minimum, so no top-up is owed."))
        if verbose:
            from taxjson.lib.tax_estimate import (CA_FED_BPA,
                                                  CA_FED_BRACKETS,
                                                  CA_PROVINCES)
            tw, tb = r["trace_with"], r["trace_base"]
            provt = CA_PROVINCES[r["province"]]
            print(f"\n  CALCULATION TRACE — BASE (other income only) "
                  f"vs WITH investments")
            print(f"  Taxable income: BASE {money(tb['ti'])} | WITH "
                  f"{money(tw['ti'])} = "
                  f"{money(other_income + est['pil'] + r.get('staking', 0.0) - r.get('deductions', 0.0) - r.get('carrying_charges', 0.0))}"
                  f" ordinary (after deductions) + {money(r['taxable_gain'])} taxable gains"
                  f" + {money(r['grossed_eligible'])} grossed dividends"
                  f" + {money(est['div_foreign'])} foreign")
            print(f"  {'FEDERAL':<36}{'BASE':>14}{'WITH':>14}")
            _print_trace_table(
                _trace_bracket_rows(CA_FED_BRACKETS, tb["ti"], tw["ti"]),
                money)
            _print_trace_table([
                (f"BPA credit ({CA_FED_BPA:,.0f} max @ "
                 f"{CA_FED_BRACKETS[0][1] * 100:g}%)",
                 -tb["fed_bpa"], -tw["fed_bpa"]),
                (f"DTC 15.0198% x {money(r['grossed_eligible'])}",
                 -tb["fed_dtc"], -tw["fed_dtc"]),
                # The credit's source, as the summary line says: the
                # actual TAX rows capped at 15%, or the 15% assumption
                # (a fixed "15% x" label contradicted it, R1-224).
                (("FTC TAX rows, max 15% x " if str(r.get(
                    "ftc_source", "")).startswith("actual")
                  else "FTC 15% x ") + money(est['div_foreign']),
                 -tb["fed_ftc"], -tw["fed_ftc"]),
                ("= FEDERAL", r["tax_base"]["federal"],
                 r["tax_with"]["federal"]),
            ], money)
            print(f"  {r['province']}")
            _print_trace_table(
                _trace_bracket_rows(provt["brackets"], tb["ti"],
                                    tw["ti"]), money)
            _print_trace_table(
                [(f"BPA credit ({provt['bpa']:,.0f} @ "
                  f"{provt['brackets'][0][1] * 100:.2f}%)",
                  -tb["prov_bpa"], -tw["prov_bpa"]),
                 ("= basic tax", tb["prov_basic"], tw["prov_basic"])]
                + [(f"surtax {rate * 100:.0f}% of basic over {thr:,.0f}",
                    ab, aw)
                   for (thr, rate, ab), (_t2, _r2, aw)
                   in zip(tb["surtax_parts"], tw["surtax_parts"])]
                + [(f"DTC {provt['dtc_eligible'] * 100:.2f}% x "
                    f"{money(r['grossed_eligible'])}",
                    -tb["prov_dtc"], -tw["prov_dtc"])]
                + ([("FTC not used federally (T2036)",
                     -tb.get("prov_ftc", 0.0), -tw.get("prov_ftc", 0.0))]
                   if tw.get("prov_ftc", 0.0) > 0.005 else [])
                + ([("Ontario Health Premium", tb["prov_ohp"],
                     tw["prov_ohp"])] if provt.get("health_premium")
                   else [])
                + [(f"= {r['province']}", r["tax_base"]["provincial"],
                    r["tax_with"]["provincial"])], money)
            _print_trace_table(
                [("TOTAL", r["tax_base"]["total"],
                  r["tax_with"]["total"])], money)
            print(f"    => WITH - BASE = {money(r['estimated_tax'])} "
                  f"estimated tax on investment income"
                  + (" (negative: a saving)"
                     if r["estimated_tax"] < -0.005 else ""))
        if root is not None:
            _icfg = _instalment_config(root, year)
            _idoc = _instalments_doc(root, r, year, _icfg)
            if _idoc and _idoc["required_at_all"]:
                print()
                print(f"  INSTALMENTS — {_idoc['basis'].replace('_', '-')} "
                      f"option (Mar/Jun/Sep/Dec 15)")
                print(f"  {'Required this year':<30}"
                      f"{money(_idoc['required_total']):>14}")
                print(f"  {'Paid to date':<30}"
                      f"{money(_idoc['paid_total']):>14}")
                print(f"  {'Net instalment interest':<30}"
                      f"{money(_idoc['net_interest']):>14}"
                      + (f"  [+ penalty "
                         f"{money(_idoc['penalty'])}]"
                         if _idoc["penalty"] > 0.005 else ""))
                if _idoc["remaining_dates"]:
                    print(f"  {'=> next due ' + _idoc['remaining_dates'][0]:<30}"
                          f"{money(_idoc['per_remaining_date'] or 0.0):>14}"
                          f"  [taxjson instalments]")
                else:
                    print(f"  {'=> balance due April 30':<30}"
                          f"{money(_idoc['shortfall']):>14}"
                          f"  [taxjson instalments]")
        print()
        for _n in r.get("notes") or []:
            print(_wrap_note("NOTE: " + _n))
        print(_wrap_note(r["assumptions"]))
    else:
        st_in = r["st_input"]
        print(f"TAX ESTIMATE — usa (single, standard deduction), rates "
              f"vintage {RATE_VINTAGE} (ESTIMATE ONLY, not filing "
              f"numbers; taxable accounts only)")
        print()
        rows = [
            ("Other income", other_income, ""),
            ("Short-term gains (net)", r["st_net"],
             f"[{money(st_in)} before other losses]"),
            ("Long-term gains (net)", r["lt_net"],
             f"[{money(est['lt'])} before other losses]"),
            ("Qualified dividends",
             est["div_ca"] + est["div_foreign"], ""),
            ("Payments in lieu", est["pil"], "[ordinary]"),
        ] + ([("Crypto staking", est["staking"], "[ordinary]")]
             if est.get("staking") else [])
        for label, amt, note in rows:
            print(f"  {label:<30}{money(amt):>14}"
                  + (f"  {note}" if note else ""))
        print()
        print(f"  Tax with investments: {money(r['tax_with']['total'])} "
              f"+ NIIT {money(r['niit'])}")
        print(f"  Tax on other income alone: "
              f"{money(r['tax_base']['total'])}")
        print(f"  => ESTIMATED TAX ON INVESTMENT INCOME: "
              f"{money(r['estimated_tax'])} {base_cur}"
              + (f"  ({r['avg_rate_pct']:.1f}% of "
                 f"{money(r['investment_income'])})"
                 if r["avg_rate_pct"] is not None else ""))
        if r["ordinary_offset"]:
            print(f"  Ordinary income offset by losses: "
                  f"{money(r['ordinary_offset'])} (max 3,000)")
        if r["losses_unused"]:
            print(f"  Unused capital losses: {money(r['losses_unused'])} "
                  f"(carry forward)")
        if verbose:
            from taxjson.lib.tax_estimate import (US_NIIT_MAGI_THRESHOLD,
                                                  US_ORD_BRACKETS,
                                                  US_STD_DEDUCTION,
                                                  stacked_slices)
            tw, tb = r["trace_with"], r["trace_base"]
            print(f"\n  CALCULATION TRACE — BASE (other income only) "
                  f"vs WITH investments")
            print(f"  Ordinary taxable: BASE {money(tb['ord_taxable'])} "
                  f"({money(other_income)} - "
                  f"{money(US_STD_DEDUCTION)} std deduction) | WITH "
                  f"{money(tw['ord_taxable'])}")
            print(f"  {'ORDINARY':<36}{'BASE':>14}{'WITH':>14}")
            _print_trace_table(
                _trace_bracket_rows(US_ORD_BRACKETS, tb["ord_taxable"],
                                    tw["ord_taxable"]), money)
            _print_trace_table(
                [("= ORDINARY", r["tax_base"]["ordinary"],
                  r["tax_with"]["ordinary"])], money)
            print(f"  PREFERENTIAL — {money(tw['pref_taxable'])} "
                  f"(LT + qualified) STACKED from "
                  f"{money(tw['ord_taxable'])}:")
            from taxjson.lib.tax_estimate import US_LTCG_BRACKETS
            for lo, hi, rate, tax in stacked_slices(
                    tw["ord_taxable"], tw["pref_taxable"],
                    US_LTCG_BRACKETS):
                hi_s = ("inf" if hi == float("inf") else f"{hi:,.0f}")
                print(f"    {f'{lo:,.0f}-{hi_s} @ {rate * 100:.0f}%':<34}"
                      f"{'':>14}{money(tax):>14}")
            print(f"  NIIT: 3.8% x min(investment income, MAGI "
                  f"{money(r['magi'])} - "
                  f"{money(US_NIIT_MAGI_THRESHOLD)}) = "
                  f"3.8% x {money(r['niit_base'])} = {money(r['niit'])}")
            _print_trace_table(
                [("TOTAL (before NIIT)", r["tax_base"]["total"],
                  r["tax_with"]["total"])], money)
            print(f"    => WITH - BASE + NIIT = "
                  f"{money(r['estimated_tax'])} estimated tax on "
                  f"investment income")
        for _n in r.get("notes") or []:
            print(_wrap_note("NOTE: " + _n, indent=""))
        print("Assumes: single filer, standard deduction, all dividends "
              "QUALIFIED, no foreign tax credit, no state tax; interest "
              "income and the §988 result on foreign currency (`taxjson "
              "fx-cash`) not included.")


def _sanity_items_from_config(accounts_cfg: Dict[str, Any],
                              root: Path) -> Tuple[List[Tuple[List[str], List[str]]],
                                                   List[str]]:
    """Paired `taxjson sanity` items from each account's
    `holdings = [...]` (paths; `~` expanded, relative to the project).
    Accounts that list a common file — one broker export covering
    several taxjson accounts — merge into one `a+b=file` group, since
    a file may sit in only one group. An account whose file is missing
    is left out with a note rather than compared against a partial
    set. Returns (items, notes)."""
    notes: List[str] = []
    files_of: Dict[str, List[str]] = {}
    for name, acfg in (accounts_cfg or {}).items():
        raw = (acfg or {}).get("holdings")
        if not raw:
            continue
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            notes.append(f"account {name}: `holdings` must be a path "
                         f"or a list of paths — not checked")
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
            continue
        if paths:
            files_of[name] = paths
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
    return items, notes


def cmd_shares(args: argparse.Namespace) -> None:
    """`taxjson shares [--options] [--taxable|--sheltered] [--json]`: the
    COMBINED quantity held of each symbol across all accounts — the
    canonical inventory (post ticker.map, base-currency, wash-adjusted
    where built), summed per symbol with a per-account breakdown. Shorts
    net against longs (the breakdown shows them). Option contracts are
    left out unless --options; positions netting to zero are dropped."""
    import json
    from taxjson.lib.core import is_option_symbol
    from taxjson.lib.report_model import (fmt_qty as qfmt,
                                          gains_basis_label,
                                          resolve_gains_files)
    from taxjson.lib.ticker_map import is_future_ticker
    root = Path(args.dir).resolve()
    cache = root / "work"
    files = resolve_gains_files(cache)
    if not files:
        sys.exit(f"taxjson shares: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    basis = gains_basis_label(files)
    want = None
    if getattr(args, "taxable", False) or getattr(args, "sheltered", False):
        want = "taxable" if args.taxable else "sheltered"
        if not (root / "taxjson.toml").exists():
            sys.exit("taxjson shares: --taxable/--sheltered need "
                     "taxjson.toml (account types).")
        types = {n: (a.get("type", "sheltered") if isinstance(a, dict)
                     else "sheltered")
                 for n, a in (load_config(root).get("accounts") or {}).items()}
        files = {n: f for n, f in files.items() if types.get(n) == want}
        if not files:
            sys.exit(f"taxjson shares: no {want} account with books in "
                     f"{cache} (accounts and their types come from "
                     f"taxjson.toml; run `taxjson run` first).")
    by_sym: Dict[str, Dict[str, Any]] = {}
    year = None
    for acct, f in files.items():
        data = _load_json_or_die(f)
        year = year or (data.get("summary") or {}).get("year")
        for h in (data.get("inventory") or []):
            sym = str(h.get("symbol") or "")
            qty = float(h.get("qty", 0) or 0)
            if not sym or abs(qty) < 1e-12:
                continue
            if not getattr(args, "options", False) and is_option_symbol(sym):
                continue
            if is_future_ticker(sym) and not is_option_symbol(sym):
                # A futures contract is not a share: it was listed as
                # "1 share" with its notional as book cost (S044-04).
                # (Futures options follow --options.)
                continue
            e = by_sym.setdefault(sym, {"qty": 0.0, "cost": 0.0,
                                        "accounts": {}})
            e["qty"] += qty
            e["cost"] += float(h.get("total_cost", 0) or 0)
            e["accounts"][acct] = e["accounts"].get(acct, 0.0) + qty
    rows = [(sym, e) for sym, e in by_sym.items()
            if abs(e["qty"]) > 1e-9]
    if getattr(args, "sort", "symbol") == "qty":
        rows.sort(key=lambda r: (-abs(r[1]["qty"]), r[0]))
    else:
        rows.sort(key=lambda r: r[0])
    base = _base_currency(root)
    # The inventory is END OF DATA, not the tax year's Dec 31: the
    # header said "tax year 2025" over 2026 positions (S044-05, the
    # sibling of list's R1-282).
    horizon = _books_horizon(cache, list(files))
    if getattr(args, "json", False):
        _json_out({"basis": basis, "year": year, "as_of": horizon,
                   "currency": base,
                   "scope": want or "all",
                   "options_included": bool(getattr(args, "options",
                                                    False)),
                   "rows": [{"symbol": sym, "qty": round(e["qty"], 8),
                             "cost": round(e["cost"], 2),
                             "accounts": {a: round(q, 8) for a, q in
                                          sorted(e["accounts"].items())}}
                            for sym, e in rows]})
        return
    scope = f" ({want} accounts)" if want else ""
    print(f"SHARES HELD — combined across{scope} "
          f"{', '.join(sorted(files))}; as of the latest data in the "
          f"books" + (f" ({horizon})" if horizon else "")
          + f", basis: {basis}  "
          f"(after ticker.map; futures excluded; option contracts "
          f"{'included' if getattr(args, 'options', False) else 'excluded'})")
    print()
    if not rows:
        print("No open positions.")
        return
    # The per-account breakdown has spaces inside it, and the shared
    # table formatter splits cells on whitespace — so align the three
    # numeric-safe columns with it, then append the breakdown as a
    # trailing free-text column.
    out = ["SYMBOL SHARES COST"]
    breakdowns = ["ACCOUNTS"]
    for sym, e in rows:
        out.append(" ".join([sym, qfmt(e["qty"]), fmt_money(e["cost"])]))
        breakdowns.append(", ".join(
            f"{a} {qfmt(q)}" for a, q in sorted(e["accounts"].items())
            if abs(q) > 1e-12))
    lines = format_report_table(out)
    width = max(len(b) for b in breakdowns)
    for i, line in enumerate(lines):
        if i == 1 and set(line.strip()) == {"-"}:      # the header rule
            print(line + "-" * (3 + width))
            continue
        j = i if i == 0 else i - 1                     # rule line offset
        print(f"{line}   {breakdowns[j]}")
    print(f"\n{len(rows)} symbol(s); COST is combined book cost in {base}.")


def cmd_redact(args: argparse.Namespace) -> None:
    """`taxjson redact FILE...`: strip account numbers and identity from
    broker exports (row shapes kept) — see taxjson_redact."""
    from taxjson.bin.taxjson_redact import main as redact_main
    argv: List[str] = []
    if args.out:
        argv += ["--out", args.out]
    for a in args.also or []:
        argv += ["--also", a]
    if args.no_denylist:
        argv.append("--no-denylist")
    if args.force:
        argv.append("--force")
    if args.check:
        argv.append("--check")
    argv += ["--", *args.files]          # a file named `--check` stays a file
    raise SystemExit(redact_main(argv))


def _grant_since_warning(settings: Dict[str, Any]) -> Optional[str]:
    """The warning for a Canada project on grant timing with no explicit
    `option_grant_timing_since`: the default is the PROJECT year, which
    moves every year — consecutive default projects tax a year-straddling
    premium twice (2026-09 audit: +399 in 2025, +298 in 2026, for a 298
    economic gain). None when the key is set or does not apply."""
    if _country(settings) in (
            "us", "usa"):
        return None
    if str(settings.get("option_premium_timing", "grant")).strip().lower() \
            != "grant":
        return None
    if settings.get("option_grant_timing_since") not in (None, ""):
        return None
    yr = settings.get("year")
    if not isinstance(yr, int) or isinstance(yr, bool):
        # No year: the missing year is the real error (run and
        # option-boundary say so); never suggest "since = None"
        # (S044-06).
        return None
    return (f"[settings] option_grant_timing_since is not set, so grant "
            f"timing (ITA s.49(1)) starts at the project year ({yr}) — a "
            f"default that MOVES when you bump `year`: next year's project "
            f"would put this year's year-straddling written options back "
            f"on close timing and tax their premium a second time. Add "
            f"`option_grant_timing_since = <first year you file under "
            f"grant timing>` (e.g. {yr}) to [settings] once and keep it "
            f"unchanged in every later year's project.")


def cmd_tax_logic(args: argparse.Namespace) -> None:
    """`taxjson tax-logic`: a short statement of every rule taxjson
    applies for the project's country, with the project's settings
    filled in (lib/tax_logic). Works outside a project too (defaults)."""
    from taxjson.lib.country import CountryError
    from taxjson.lib.tax_logic import render, rule_sections, sections
    root = Path(args.dir).resolve()
    settings = dict((_soft_config(root).get("settings") or {}))
    if args.country:
        country = _normalize_country(args.country)
    elif settings:
        country = _country(settings)
    else:
        _die("no taxjson.toml here to read the country from — pass "
             "--country canada|usa (or -C DIR to a project)")
    try:
        if getattr(args, "json", False):
            _json_out({"country": country, "year": settings.get("year"),
                       "sections": [{"title": t, "rules": r}
                                    for t, r in sections(country, settings)],
                       "rule_ids": [
                           {"title": t, "rules": [
                               {"id": r.id, "text": r.text,
                                "continues": r.cont} for r in rs]}
                           for t, rs in rule_sections(country, settings)]})
            return
        print(render(country, settings, ids=getattr(args, "ids", False)))
    except (CountryError, ValueError) as e:
        # The same resolvers the engine uses refused a setting: the
        # text would otherwise describe a value `run` reads differently
        # (partition SPEC-12).
        _die(str(e))


def cmd_spinoffs(args: argparse.Namespace) -> None:
    """`taxjson spinoffs`: every spin-off, its election, the value per
    share used and what was booked (lib/corp_views). Exit 1 when one
    needs attention (zero value, no election, ignored)."""
    from taxjson.lib.corp_views import render_spinoffs, spinoffs
    root = Path(args.dir).resolve()
    doc = spinoffs(root, load_config(root), args.account)
    if getattr(args, "json", False):
        _json_out(doc)
    else:
        for ln in render_spinoffs(doc):
            print(ln)
    if any(s["flags"] and not s["sheltered"] for s in doc["spinoffs"]):
        raise SystemExit(1)


def cmd_splits(args: argparse.Namespace) -> None:
    """`taxjson splits`: every split, consolidation and rename in the
    books with holdings before and after, flagging a split applied twice,
    a no-op row and a fractional result (lib/corp_views). Exit 1 on a
    likely double application."""
    from taxjson.lib.corp_views import render_splits, splits
    root = Path(args.dir).resolve()
    items = splits(root, load_config(root), args.account)
    if getattr(args, "json", False):
        _json_out({"splits": items})
    else:
        for ln in render_splits(items):
            print(ln)
    if any("TWICE?" in s["flags"] for s in items):
        raise SystemExit(1)


def cmd_check_dates(args: argparse.Namespace) -> None:
    """`taxjson check-dates`: every trade and settlement date the parsers
    produced, checked against the trading calendar of what was traded
    (crypto 24/7, futures 23/5, US stocks with the overnight session,
    options and Canadian listings on exchange days) and the settlement
    rules (lib/check_dates). Exit 1 on an impossible date."""
    from taxjson.lib.check_dates import analyze, render
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    if not (root / "work").is_dir():
        sys.exit("taxjson check-dates: no work/ — run `taxjson run` first.")
    doc = analyze(root, cfg, account=args.account)
    if not doc["sources"]:
        sys.exit("taxjson check-dates: no parsed sources in work/ — run "
                 "`taxjson run` first.")
    if getattr(args, "json", False):
        _json_out(doc)
    else:
        for ln in render(doc, show_all=args.all):
            print(ln)
    if doc["errors"]:
        raise SystemExit(1)


def cmd_edge_cases(args: argparse.Namespace) -> None:
    """`taxjson edge-cases`: transactions whose treatment turns on a
    boundary — trades that settle in a different year than they trade,
    dispositions in the last/first days of a year, written options and
    expiries across Dec 31, income around New Year, crypto near midnight,
    superficial-loss windows that span the year end, and every taxable
    loss with an acquisition or sale within --margin days of the 30-day
    window's edge — each with where it lands and why (lib/edge_cases)."""
    from taxjson.lib.edge_cases import analyze, render_text
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    if not (root / "work").is_dir():
        sys.exit("taxjson edge-cases: no work/ directory — run `taxjson run` first.")
    doc = analyze(root, cfg, margin=args.margin, account=args.account)
    if getattr(args, "json", False):
        _json_out(doc)
        return
    for line in render_text(doc):
        print(line)


def cmd_option_boundary(args: argparse.Namespace) -> None:
    """`taxjson option-boundary [--json]`: every written option in the
    taxable accounts whose write and close straddle a tax-year boundary
    (or that is still open at the project year's end) — where each
    amount lands under the timing in force (ITA s.49), and whether a
    filed year needs a T1-ADJ. A `filed/<year>.json` lock is what turns
    "if that year was filed" into a fact; ATTENTION rows (a locked year
    kept on transition close timing, an expired contract with no
    expiry row) need action. Exit 1 when no taxable book exists (nothing
    was checked), 0 otherwise."""
    import json
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.option_boundary import straddling
    from taxjson.lib.pipeline import option_timing_from_settings
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = cfg.get("settings", {})
    if not isinstance(settings.get("year"), int):
        # It went on with year 0 and printed "tax year 0" (S044-07).
        _die("[settings] year is required in taxjson.toml (the review "
             "is per tax year).")
    year = int(settings["year"])
    kw = option_timing_from_settings(settings)
    timing = kw.get("option_premium_timing", "close") if kw else "close"
    since = kw.get("option_grant_since") if kw else None
    _w = _grant_since_warning(settings)
    if _w:
        print(f"taxjson option-boundary: warning: {_w}", file=sys.stderr)
    filed_years = set()
    filed_timing: Dict[int, Dict[str, Any]] = {}
    for f in (root / "filed").glob("*.json"):
        try:
            fy = int(f.stem)
        except ValueError:
            continue
        filed_years.add(fy)
        try:
            _ot = (json.loads(f.read_text(encoding="utf-8")) or {}).get(
                "option_timing")
        except (OSError, ValueError, AttributeError) as e:
            # The lock's recorded timing drives the advice below: an
            # unreadable lock silently read as "no timing recorded" and
            # the advice flipped to ATTENTION / since = <locked year>
            # (S044-08).
            print(f"taxjson option-boundary: warning: cannot read "
                  f"filed/{f.name} ({e}) — its recorded option timing "
                  f"is unknown, so the advice for {fy} below assumes "
                  f"none was recorded; `taxjson check-filed` checks the "
                  f"lock.", file=sys.stderr)
            _ot = None
        if isinstance(_ot, dict):
            filed_timing[fy] = _ot
    rows = []
    books = 0
    missing = []
    _phantoms = None
    if (root / "phantoms.json").exists():
        from taxjson.lib.phantom_holdings import load_phantoms
        try:
            _phantoms = load_phantoms(root / "phantoms.json") or None
        except (OSError, ValueError) as e:
            _die(f"phantoms.json is unreadable: {e}")
    for name, acfg in sorted((cfg.get("accounts") or {}).items()):
        if not isinstance(acfg, dict) or acfg.get("type", "sheltered") != "taxable":
            continue
        base = cache / f"{name}_base.json"
        if not base.exists():
            missing.append(name)
            print(f"taxjson option-boundary: warning: no {base.name} — run `taxjson run` first",
                  file=sys.stderr)
            continue
        books += 1
        # A truncated, non-UTF-8 or wrong-shape book was a traceback
        # here (S042-18).
        doc = _load_json_or_die(base)
        txs = []
        for r in (doc.get("transactions", doc) if isinstance(doc, dict) else doc):
            try:
                txs.append(TaxTransaction(**{k: v for k, v in r.items()
                                             if k in TaxTransaction.__dataclass_fields__}))
            except TypeError:
                continue
        if _phantoms is not None:
            # The same phantom openings the gains stage adds: a phantom-
            # backed LONG option sold to close read as a WRITE, with a
            # false checklist ATTENTION when it had expired (S044-09).
            from taxjson.lib.phantom_holdings import synthesize_openings
            txs, _log = synthesize_openings(txs, _phantoms)
        for r in straddling(txs, year, timing, since, filed_years,
                            filed_timing=filed_timing,
                            tax_date=_tax_date_basis(settings)):
            r["account"] = r["account"] or name
            rows.append(r)
    if not books:
        # Nothing was checked: printing the all-clear here let the
        # checklist mark the step done on a project that never ran.
        sys.exit("taxjson option-boundary: NOT CHECKED — no taxable "
                 "base files in work/ (run `taxjson run` first).")
    amend = [r for r in rows if r["action"].startswith("T1-ADJ")]
    attention = [r for r in rows if r.get("attention")]
    if getattr(args, "json", False):
        _json_out({"year": year, "timing": timing, "since": since,
                   "since_explicit": settings.get(
                       "option_grant_timing_since") not in (None, ""),
                   "filed_years": sorted(filed_years), "rows": rows,
                   "amend": len(amend), "attention": len(attention),
                   "missing_books": missing})
        return
    print(f"OPTION YEAR-BOUNDARY REVIEW — tax year {year}; premium timing: {timing}"
          + (f" (contracts written from {since})" if timing == "grant" and since else "")
          + (f"; filed-year locks: {', '.join(str(y) for y in sorted(filed_years))}" if filed_years else "; no filed-year locks (run `taxjson close-year` after filing)"))
    print()
    if not rows:
        print("No written option straddles a year boundary and none is open at year end. Nothing to amend.")
        return
    out = ["ACCOUNT SYMBOL WRITTEN UNITS PREMIUM CLOSED KIND PAID"]
    for r in rows:
        out.append(" ".join([r["account"], r["symbol"], r["written"], f"{r['units']:g}",
                             fmt_money(r["premium"]), r["closed"] or "-", r["close_kind"],
                             fmt_money(r["paid"]) if r["paid"] else "-"]))
    _print_report_table(out)
    print()
    for i, r in enumerate(rows, 1):
        print(f"{i:>3}. {r['symbol']} ({r['account']}, written {r['written']}): {r['where']}")
        print(f"     -> {r['action']}")
    print()
    if attention:
        print(f"{len(attention)} item(s) need ATTENTION (review; a locked year may need a T1-ADJ) — marked above.")
    if amend:
        print(f"{len(amend)} item(s) require an amended return (T1-ADJ) — listed above with the year and amount.")
    elif not attention:
        print("No amended return is required by these contracts under the timing in force.")


def cmd_checklist(args: argparse.Namespace) -> None:
    """`taxjson checklist`: the filing checklist (docs/filing.md) with each
    step auto-detected — the command that proves a step is run and its
    verdict shown — plus manual marks for the steps no command can prove
    (`--done ID`, `--skip ID`, `--undo ID`; stored in checklist.json at the
    project root, which you should commit). `--walk` steps through the open
    items one at a time. Exit 1 while anything is open."""
    import json as _json
    from datetime import date as _date
    from taxjson.lib import checklist as cl

    root = Path(args.dir).resolve()
    cfg = load_config(root)
    settings = cfg.get("settings") or {}
    year = settings.get("year")
    if not isinstance(year, int):
        sys.exit("taxjson checklist: [settings] year is required")
    country = _country(settings)

    ids = [s[0] for s in cl.STEPS]
    if args.note and not (args.done or args.skip):
        sys.exit("taxjson checklist: --note goes with --done or --skip "
                 "(it is stored with the mark).")
    marks = [(args.done, "done"), (args.skip, "skipped"), (args.undo, None)]
    recorded: List[Dict[str, Any]] = []
    for step, mark in marks:
        if step:
            try:
                changed = cl.set_override(root, year, step, mark,
                                          note=args.note or "")
            except KeyError:
                sys.exit(f"taxjson checklist: unknown step {step!r} "
                         f"(ids: {', '.join(ids)})")
            except cl.StateFileError as e:
                sys.exit(f"taxjson checklist: {e}")
            verb = {"done": "marked done", "skipped": "marked skipped",
                    None: "mark removed"}[mark]
            if not changed:
                verb = "had no mark — nothing to undo"
            recorded.append({"step": step, "mark": mark or "undo",
                             "changed": changed, "note": args.note or ""})
            if not args.json:
                print(f"taxjson checklist: {step} {verb}"
                      + (f" (recorded in {cl.STATE_FILE})." if changed
                         else "."))
    if args.reset:
        _state = root / cl.STATE_FILE
        existed = _state.exists()
        _state.unlink(missing_ok=True)
        recorded.append({"step": None, "mark": "reset", "changed": existed})
        if not args.json:
            print(f"taxjson checklist: {cl.STATE_FILE} removed." if existed
                  else f"taxjson checklist: no {cl.STATE_FILE} — nothing to "
                       f"reset.")
    if (args.done or args.skip or args.undo or args.reset) and not args.walk \
            and not args.show:
        if args.json:
            print(_json.dumps({"year": year, "state_file": cl.STATE_FILE,
                               "recorded": recorded}, indent=2))
        return

    ctx = cl.Ctx(root=root, cfg=cfg, year=year, today=_date.today(),
                 run_sub=cl.default_run_sub(root))
    only = [args.only] if args.only else None
    if only and args.only not in ids:
        sys.exit(f"taxjson checklist: unknown step {args.only!r} "
                 f"(ids: {', '.join(ids)})")
    if only and args.quick:
        print("taxjson checklist: --only names one step; ignoring --quick.",
              file=sys.stderr)
        args.quick = False

    if args.walk:
        if not sys.stdin.isatty():
            sys.exit("taxjson checklist --walk needs a terminal (use "
                     "`taxjson checklist` for the report, --done/--skip to "
                     "record steps).")
        try:
            _checklist_walk(ctx, cl, only, quick=args.quick)
        except cl.StateFileError as e:
            sys.exit(f"taxjson checklist: {e}")
        return

    try:
        results = cl.evaluate(ctx, only=only, quick=args.quick,
                              progress=cl.stderr_progress)
    except cl.StateFileError as e:
        sys.exit(f"taxjson checklist: {e}")
    if args.json:
        print(_json.dumps(cl.to_json(results, year, country), indent=2))
    else:
        print(cl.render(results, year, country, quick=args.quick))
    if not all(r.passed for r in results):
        sys.exit(1)


def _checklist_walk(ctx, cl, only, quick: bool = False) -> None:
    """Interactive pass over the open steps, one detector at a time: each
    step is shown as soon as its own check finishes (the whole list can
    take a minute on a big book), with why it matters and what was found,
    then the user's decision is recorded. Ends with the summary; exit 1
    while anything is still open (also after [q]uit)."""
    country = _country(ctx.settings)
    ids = [s[0] for s in cl.STEPS if not only or s[0] in only]
    keys = "[d]one  [s]kip  [r]e-check  [n]ext  [q]uit  (Enter = next)"
    print(f"Filing checklist walk. For each open step: {keys}\n")
    seen = 0
    quit_early = False
    left_open = 0
    for sid in ids:
        r = cl.evaluate(ctx, only=[sid], quick=quick,
                        progress=cl.stderr_progress)[0]
        if r.passed:
            print(f"    {cl.SYMBOL[r.effective]} {sid}: {r.detail}"
                  + (f" (marked {r.override})" if r.override else ""))
            continue
        seen += 1
        _, stage, title, cmd, why = cl.step_meta(sid, country)
        stage_name = dict(cl.STAGES)[stage]
        show = True
        while True:
            if show:
                print(f"\n--- [{stage}. {stage_name}]  {sid}")
                print(f"    {title}")
                print(f"    why:     {why}")
                print(f"    proves:  {cmd}")
                print(f"    found:   {cl.SYMBOL[r.effective]} {r.detail}"
                      + (f" (marked {r.override}"
                         + (f": {r.note}" if r.note else "") + ")"
                         if r.override else ""))
            show = False
            try:
                ans = input("    > ").strip().lower()
            except EOFError:
                print()
                quit_early = True
                break
            if ans in ("q", "quit"):
                quit_early = True
                break
            if ans in ("d", "done"):
                note = input("    note (optional): ").strip()
                cl.set_override(ctx.root, ctx.year, sid, "done", note=note)
                print(f"    recorded: {sid} done")
                break
            if ans in ("s", "skip"):
                note = input("    reason (optional): ").strip()
                cl.set_override(ctx.root, ctx.year, sid, "skipped", note=note)
                print(f"    recorded: {sid} skipped")
                break
            if ans in ("r", "recheck", "re-check"):
                r = cl.evaluate(ctx, only=[sid], progress=cl.stderr_progress)[0]
                if r.passed:
                    print(f"    now: {cl.SYMBOL[r.effective]} {r.detail}")
                    break
                show = True
                continue
            if ans in ("", "n", "next"):
                left_open += 1
                break
            print(f"    unknown key {ans!r} — {keys}")
        if quit_early:
            break
    print(f"\n{seen} open step(s) visited. Summary "
          f"(`taxjson checklist` re-checks everything):")
    results = cl.evaluate(ctx, only=only, quick=True)
    print(cl.render(results, ctx.year, country, quick=True))
    if quit_early or left_open:
        # Open steps remain (the one quit on, the ones passed over).
        sys.exit(1)


def _mask_ids_in_path(path: str) -> str:
    """`path` with every broker-account-like id in its FILE NAME masked
    to its first 2 characters + *** (U1234567 -> U1***, 53123456 ->
    53***): an IB U-number or a run of 5+ digits."""
    head, sep, name = str(path).rpartition("/")
    name = re.sub(r"(?<![A-Za-z0-9])(U\d{5,}|\d{5,})(?!\d)",
                  lambda m: m.group(1)[:2] + "***", name)
    return head + sep + name


def cmd_sanity(args: argparse.Namespace) -> None:
    """`taxjson sanity ITEM... [--tolerance N] [--json]`: LOOSE
    cross-check of open positions against externally produced holdings
    TOML files (portoml-style: [[holding]] symbol/quantity).

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
    (same basis as `taxjson list` — wash-adjusted where built, post
    ticker.map). The files' symbols get the same GLOBAL+TOBASE renames,
    with options following their underlying's rename. Exit 1 on any
    discrepancy in any group."""
    import json
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    tol = float(1e-4 if getattr(args, "tolerance", None) is None
                    else args.tolerance)
    if tomllib is None:
        sys.exit("taxjson sanity: needs tomllib (py3.11+) or tomli")

    # ---- taxjson side ---------------------------------------------------
    resolved = resolve_gains_files(cache)
    if not resolved:
        sys.exit(f"taxjson sanity: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    basis = gains_basis_label(resolved)
    tax: Dict[str, Dict[str, float]] = {}
    unreadable: Dict[str, str] = {}
    for acct, f in resolved.items():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a gains document")
        except (OSError, ValueError) as e:
            print(f"taxjson: warning: could not read {f}: {e}",
                  file=sys.stderr)
            unreadable[acct] = f"work/{f.name}: {e}"
            continue
        book: Dict[str, float] = {}
        for h in (data.get("inventory") or []):
            q = float(h.get("qty", 0) or 0)
            sym = str(h.get("symbol") or "")
            if sym and abs(q) > 1e-12:
                book[sym] = book.get(sym, 0.0) + q
        tax[acct] = book

    # ---- classify the positionals into groups ------------------------------
    # A group is (accounts, files); bare items all land in one AGGREGATE
    # group, `A[+A]=F[+F]` items in a PAIRED group keyed by its accounts
    # so a repeated left-hand side merges. Each account/file belongs to
    # at most one group — a second placement is a usage error rather
    # than a silent double count.
    def _account(name: str, ctx: str) -> str:
        if name in tax:
            return name
        if name in unreadable:
            # Its book exists but is corrupt: say so, not "not an
            # account" (R1-338 — that sent the user to the config).
            sys.exit(f"taxjson sanity: {ctx}: the books of {name!r} "
                     f"cannot be read ({unreadable[name]}) — re-run "
                     f"`taxjson run` to rebuild them.")
        sys.exit(f"taxjson sanity: {ctx}: {name!r} is not an account "
                 f"(have: {', '.join(sorted(tax))})")

    def _file(name: str, ctx: str) -> Path:
        p2 = Path(name).expanduser().resolve()
        if p2.is_file():
            return p2
        sys.exit(f"taxjson sanity: {ctx}: {name!r} is not an existing "
                 f".toml file")

    groups: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    bare: Dict[str, Any] = {"accounts": [], "files": [], "paired": False}
    placed_accounts: Dict[str, str] = {}
    placed_files: Dict[Path, str] = {}

    def _place(group: Dict[str, Any], gname: str, acct: Optional[str],
               path: Optional[Path]) -> None:
        if acct is not None:
            owner = placed_accounts.get(acct)
            if owner is None:
                placed_accounts[acct] = gname
                group["accounts"].append(acct)
            elif owner == gname:
                # Summing a duplicate DOUBLED its positions and every
                # one showed as a 2x QTY_MISMATCH — while the header
                # printed the deduplicated list. Count once, say so.
                print(f"taxjson sanity: note: account {acct!r} given "
                      f"more than once — counted once.",
                      file=sys.stderr)
            else:
                sys.exit(f"taxjson sanity: account {acct!r} appears in "
                         f"more than one group ({owner} and {gname})")
        if path is not None:
            owner = placed_files.get(path)
            if owner is None:
                placed_files[path] = gname
                group["files"].append(path)
            elif owner == gname:
                print(f"taxjson sanity: note: file {path.name} given "
                      f"more than once — counted once.",
                      file=sys.stderr)
            else:
                sys.exit(f"taxjson sanity: file {path.name} appears in "
                         f"more than one group ({owner} and {gname})")

    items = list(args.items or [])
    config_groups: List[Tuple[List[str], List[str]]] = []
    config_notes: List[str] = []
    if not items:
        # No arguments: pairings come from taxjson.toml — each
        # account's `holdings = [...]`. Explicit arguments override
        # the config for that run.
        # Not swallowed: an unreadable taxjson.toml said "no account
        # declares `holdings`" and invited adding keys already there
        # (S049-08) — load_config names the TOML error instead.
        _accts_cfg = load_config(root).get("accounts") or {}
        config_groups, config_notes = _sanity_items_from_config(_accts_cfg,
                                                                root)
        if not config_groups:
            for n in config_notes:
                print(f"taxjson sanity: note: {n}", file=sys.stderr)
            if config_notes:
                sys.exit("taxjson sanity: none of the `holdings` files "
                         "in taxjson.toml could be checked (see the "
                         "notes above) — fix the paths.")
            sys.exit("taxjson sanity: no arguments, and no account in "
                     "taxjson.toml declares `holdings = [...]` (paths "
                     "of its broker positions .toml files). Either "
                     "pass items — `taxjson sanity margin=U1.toml` — "
                     "or add e.g.\n  [accounts.margin]\n  holdings = "
                     "[\"~/portoml-run/U1_holdings.toml\"]")
    for accts, paths in config_groups:
        key = tuple(accts)
        gname = "+".join(key)
        grp = groups.setdefault(key, {"accounts": [], "files": [],
                                      "paired": True})
        for n in key:
            _place(grp, gname, _account(n, "taxjson.toml"), None)
        for f in paths:
            _place(grp, gname, None, _file(f, "taxjson.toml"))
    for a in items:
        if "=" in a and not Path(a).expanduser().is_file():
            lhs, rhs = a.split("=", 1)
            names = [x for x in lhs.split("+") if x.strip()]
            paths = [x for x in rhs.split("+") if x.strip()]
            if not names or not paths:
                sys.exit(f"taxjson sanity: {a!r}: paired form is "
                         f"ACCOUNT[+ACCOUNT...]=FILE[+FILE...]")
            key = tuple(sorted(dict.fromkeys(
                _account(n.strip(), a) for n in names)))
            gname = "+".join(key)
            grp = groups.setdefault(
                key, {"accounts": [], "files": [], "paired": True})
            for n in key:
                _place(grp, gname, n, None)
            for f in paths:
                _place(grp, gname, None, _file(f.strip(), a))
            continue
        if a in tax:
            _place(bare, "the aggregate group", a, None)
            continue
        p2 = Path(a).expanduser().resolve()
        if p2.is_file():
            _place(bare, "the aggregate group", None, p2)
            continue
        sys.exit(f"taxjson sanity: {a!r} is neither an account "
                 f"(have: {', '.join(sorted(tax))}) nor an existing "
                 f".toml file")
    if bare["accounts"] or bare["files"]:
        if not (bare["accounts"] and bare["files"]):
            sys.exit("taxjson sanity: the aggregate form needs at least "
                     "one ACCOUNT and one FILE.toml (free mix, e.g. "
                     "`taxjson sanity margin rrsp U1_holdings.toml "
                     "U2_holdings.toml`), or pair them: "
                     "`margin=U1_holdings.toml+U2_holdings.toml`")
    ordered: List[Dict[str, Any]] = list(groups.values())
    if bare["accounts"]:
        ordered.append(bare)
    if not ordered:
        sys.exit("taxjson sanity: nothing to check")

    # Same consolidation the canonical pipeline applied: GLOBAL+TOBASE
    # renames from ticker.map; options follow their underlying.
    renames: Optional[Dict[str, str]] = None
    map_symbol = None
    map_file = root / "ticker.map"
    if map_file.exists():
        try:
            from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                        map_symbol,
                                                        merge_renames)
            renames = merge_renames(load_map_file(map_file),
                                    to_base=True)
        except Exception as e:
            print(f"taxjson sanity: warning: ticker.map not applied "
                  f"({e}) — cross-listed symbols may mismatch.",
                  file=sys.stderr)
            renames = None

    def _mapped(sym: str) -> str:
        if renames is not None and map_symbol is not None:
            return map_symbol(sym, renames)
        return sym

    _OPT_RE = re.compile(r'^((?:F:)?[A-Z0-9.]+?)(\d{6}[CP]\d+)\.(\S+)$',
                         re.IGNORECASE)
    _VENUE_SFX_RE = re.compile(r'^([A-Za-z0-9]+)\.[A-Za-z]{2,3}$')

    def _ext_book(paths: List[Path]) -> Tuple[Dict[str, float],
                                              List[str],
                                              Dict[str, str]]:
        """(book, file labels, alt) — `alt` maps an option's symbol
        as the FILE spells it to the same contract keyed by the
        row's `underlying` root. Brokers and tools disagree on the
        option ROOT: IB names the Montréal contract on RCI.B
        `RCI.B 16JUL27 55 C` (taxjson keys RCI.B270716C00055000.TO)
        while a positions export keys it by the exchange option
        root `RCI...` — same contract, and the export's `underlying
        = "RCI.B.TO"` field says so. The compare below falls back to
        the underlying spelling only when the file's spelling has no
        taxjson counterpart, so a real mismatch still shows."""
        book: Dict[str, float] = {}
        labels: List[str] = []
        alt: Dict[str, str] = {}
        for path in paths:
            try:
                doc = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                sys.exit(f"taxjson sanity: cannot parse {path}: {e}")
            meta = doc.get("meta") if isinstance(doc, dict) else None
            labels.append(str((meta or {}).get("account") or path.stem)
                          if isinstance(meta, (dict, type(None)))
                          else path.stem)
            holdings = doc.get("holding") if isinstance(doc, dict) else None
            if not isinstance(holdings, list):
                sys.exit(f"taxjson sanity: {path.name} has no [[holding]] "
                         f"array (portoml-style file expected)")
            for h in holdings:
                if not isinstance(h, dict):
                    sys.exit(f"taxjson sanity: {path.name}: a [[holding]] "
                             f"entry is not a table")
                if (str(h.get("asset_type") or "")).lower() == "cash":
                    continue
                sym = str(h.get("symbol") or "").strip()
                _qraw = h.get("quantity")
                if _qraw is None or (isinstance(_qraw, str)
                                     and not _qraw.strip()):
                    # A missing or blank quantity read as 0 and the row
                    # vanished: sanity said OK and the checklist ticked
                    # the step (S044-18).
                    sys.exit(f"taxjson sanity: {path.name}: "
                             f"{sym or '?'}: a [[holding]] row has no "
                             f"quantity — fix the file (an unreadable "
                             f"row is never skipped).")
                try:
                    q = float(_qraw)
                except (TypeError, ValueError):
                    sys.exit(f"taxjson sanity: {path.name}: {sym or '?'}: "
                             f"quantity {h.get('quantity')!r} is not a number")
                if q != q or q in (float("inf"), float("-inf")):
                    sys.exit(f"taxjson sanity: {path.name}: {sym or '?'}: "
                             f"quantity is not finite")
                if not sym and abs(q) > 1e-12:
                    _cus = str(h.get("cusip") or h.get("isin")
                               or "").strip()
                    sys.exit(f"taxjson sanity: {path.name}: a [[holding]] "
                             f"row has quantity {q:g} but no symbol"
                             + (f" ({_cus})" if _cus else "")
                             + " — it cannot be compared; fix the file.")
                if not sym or abs(q) <= 1e-12:
                    continue
                if (str(h.get("asset_type") or "").lower() == "crypto"
                        and _VENUE_SFX_RE.match(sym)):
                    # A snapshot tool's venue suffix (portoml's
                    # `LINK.KR` for Kraken): taxjson keys crypto by the
                    # bare coin, so every coin showed twice, MISSING on
                    # each side (R1-113, R1-334).
                    sym = _VENUE_SFX_RE.match(sym).group(1)
                tgt = _mapped(sym)
                book[tgt] = book.get(tgt, 0.0) + q
                und = str(h.get("underlying") or "").strip()
                m = _OPT_RE.match(sym)
                if und and m and "." in und:
                    und_root, und_ext = _mapped(und).rsplit(".", 1)
                    via = f"{und_root}{m.group(2)}.{und_ext}"
                    if via != tgt:
                        alt[tgt] = via
        return book, labels, alt

    # ---- compare, per group ------------------------------------------------
    # Positions can NET to zero across accounts (or vs the files' sum);
    # only keep true residues.
    all_rows: List[Dict[str, Any]] = []
    for grp in ordered:
        ext_book, file_labels, alt = _ext_book(grp["files"])
        tax_book: Dict[str, float] = {}
        for acct in grp["accounts"]:
            for sym, q in tax[acct].items():
                tax_book[sym] = tax_book.get(sym, 0.0) + q
        via_underlying: List[Tuple[str, str]] = []
        for sym, via in alt.items():
            if sym in ext_book and sym not in tax_book \
                    and via in tax_book:
                ext_book[via] = ext_book.get(via, 0.0) + ext_book.pop(sym)
                via_underlying.append((sym, via))
        grp["via_underlying"] = via_underlying
        rows: List[Dict[str, Any]] = []
        for sym in sorted(set(tax_book) | set(ext_book)):
            tq = tax_book.get(sym, 0.0)
            eq = ext_book.get(sym, 0.0)
            if abs(tq - eq) <= tol:
                continue
            issue = ("MISSING_IN_TAXJSON" if abs(tq) <= tol
                     else "MISSING_IN_HOLDINGS" if abs(eq) <= tol
                     else "QTY_MISMATCH")
            rows.append({"symbol": sym, "issue": issue,
                         "taxjson_qty": tq, "holdings_qty": eq,
                         "accounts": sorted(grp["accounts"])})
        grp["rows"] = rows
        grp["labels"] = file_labels
        grp["positions"] = len(tax_book)
        all_rows.extend(rows)
    accounts = sorted(placed_accounts)
    files: List[Path] = [p2 for g in ordered for p2 in g["files"]]
    file_labels = [lbl for g in ordered for lbl in g["labels"]]
    uncovered = sorted(a for a in tax
                       if a not in placed_accounts and tax[a])

    if getattr(args, "json", False):
        _json_out({
            "basis": basis,
            "accounts": accounts,
            # The [meta] account label is the broker id portoml writes:
            # masked like the text listing (S044-16).
            "files": [{"file": str(p2),
                       "file_account": _mask_ids_in_path(lbl)}
                      for p2, lbl in zip(files, file_labels)],
            "groups": [{"accounts": sorted(g["accounts"]),
                        "paired": g["paired"],
                        "files": [str(p2) for p2 in g["files"]],
                        "matched_via_underlying": [
                            {"file_symbol": a, "taxjson_symbol": b}
                            for a, b in g["via_underlying"]],
                        "discrepancies": g["rows"],
                        "clean": not g["rows"]}
                       for g in ordered],
            "discrepancies": all_rows,
            "uncovered_accounts": uncovered,
            "notes": config_notes,
            "clean": not all_rows,
            # False when an account's CONFIGURED holdings file could
            # not be read (R1-324), or — in the config form — an
            # account with open positions has no holdings file at all
            # (S044-19): its positions were never compared, so "clean"
            # covers the other groups only.
            "complete": not config_notes and not (uncovered
                                                  and not items),
        })
        raise SystemExit(0 if not all_rows else 1)

    for n in config_notes:
        print(f"taxjson sanity: note: {n}", file=sys.stderr)
    multi = len(ordered) > 1 or any(g["paired"] for g in ordered)
    print(f"SANITY — taxjson positions (basis: {basis}) vs external "
          f"holdings TOML ({'paired' if multi else 'loose aggregate'} "
          f"check)")
    print()
    for grp in ordered:
        tag = ("paired" if grp["paired"] else "aggregate") if multi else ""
        print(f"  accounts: {', '.join(sorted(grp['accounts']))}  "
              f"({grp['positions']} combined position(s))"
              + (f"  [{tag}]" if tag else ""))
        # One line per file, PATH first: the label alone (the file's
        # meta.account, else its stem) didn't say which export was
        # actually read — a stale or wrong path is the first thing to
        # rule out when a group disagrees.
        for p2, lbl in zip(grp["files"], grp["labels"]):
            shown = str(p2)
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
            print(f"  file:     {shown}{extra}")
        for a, b in grp["via_underlying"]:
            print(f"  -- option {a} matched taxjson's {b} via its "
                  f"underlying (same contract, different option root)")
        if multi:
            if grp["rows"]:
                print(f"  -> {len(grp['rows'])} discrepancy(ies)")
            else:
                print("  -> OK")
            print()
    for a in uncovered:
        print(f"  -- account {a} not included "
              f"({len(tax[a])} position(s) unchecked)")
    if not multi or uncovered:
        print()
    if uncovered and not items:
        # The checklist's sanity step reads this line: "done" while
        # whole accounts were never tied was a false certificate
        # (S044-19).
        print(f"UNCHECKED: account(s) "
              f"{', '.join(f'{a} ({len(tax[a])} position(s))' for a in uncovered)}"
              f" have open positions but no `holdings` file in "
              f"taxjson.toml — not compared with the broker.")
    if config_notes:
        # A configured holdings file that could not be read drops its
        # whole account from the compare: never let that read as a
        # clean check (the checklist and run's auto-sanity key on this
        # line — 2026-09 audit R1-324).
        print(f"INCOMPLETE: {len(config_notes)} account(s) with "
              f"`holdings` in taxjson.toml were NOT checked (see the "
              f"notes on stderr) — fix the paths, then re-run.")
    if not all_rows:
        print("OK: tickers and quantities agree"
              + (" in every checked group." if config_notes
                 else " in every group." if multi else "."))
    else:
        out_lines = [("ACCOUNTS SYMBOL ISSUE TAXJSON HOLDINGS DIFF"
                      if multi else
                      "SYMBOL ISSUE TAXJSON HOLDINGS DIFF")]
        for r in all_rows:
            cells = [r["symbol"], r["issue"],
                     f"{r['taxjson_qty']:g}", f"{r['holdings_qty']:g}",
                     f"{r['taxjson_qty'] - r['holdings_qty']:+g}"]
            if multi:
                cells.insert(0, "+".join(r["accounts"]))
            out_lines.append(" ".join(cells))
        _print_report_table(out_lines)
        print(f"\n{len(all_rows)} discrepancy(ies).")
    raise SystemExit(0 if not all_rows else 1)


def cmd_positions(args: argparse.Namespace) -> None:
    """List open positions per account, taken from each account's canonical
    gains file's `inventory` (wash-adjusted where built) — i.e. AFTER
    ticker.map consolidation (cross-listings merged) and base-currency
    conversion, so quantities and cost basis match the canonical pipeline.
    One row per (account, symbol); a base-currency book-cost TOTAL closes
    the table."""
    import json
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    # Canonical per-account gains (wash-adjusted where built — same basis
    # as every other query command); the raw/native derivatives keep
    # cross-listings separate and in native currency, which is the opposite
    # of what this view wants.
    as_of = getattr(args, "date", None)
    if as_of:
        # Positions AS OF a date: recompute each account's books from
        # its base.json (already ticker.map-consolidated) up to the
        # date. Deferred wash within the account is kept, but it is
        # PER-ACCOUNT ACB (no s.47 blend across taxable accounts) and
        # before the cross-account wash pass (that exists only for full
        # runs) — the basis label says so (S044-21, R1-282).
        import re as _re
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            sys.exit("taxjson list: --date expects YYYY-MM-DD")
        try:
            datetime.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            # Shape-only validation let 2025-15-02 (swapped day/month)
            # through to the engine's string compare — wrong positions
            # at exit 0 with the bogus date in the banner (REVIEW #27).
            sys.exit(f"taxjson list: --date {as_of} is not a real "
                     f"calendar date")
        from taxjson.lib.dispatch import run_cmd as _run_cmd
        settings = _soft_settings(root)
        country = _country(settings)
        year = str(settings.get("year", "") or as_of[:4])
        # Accounts and their TYPES come from the config — never from
        # globbing work/ (which also holds *_raw_base.json derivatives
        # that would masquerade as accounts), and --taxable must only
        # be passed for taxable accounts (TRANSFER rows are legal in
        # sheltered ones; the engine rightly rejects them otherwise).
        accounts_cfg = (load_config(root).get("accounts", {})
                        if (root / "taxjson.toml").exists() else {})
        if not accounts_cfg:
            sys.exit("taxjson list: --date needs taxjson.toml "
                     "(account names and types).")
        if args.account and args.account not in accounts_cfg:
            sys.exit(f"taxjson list: no [accounts.{args.account}] in "
                     f"taxjson.toml")
        names = ([args.account] if args.account
                 else sorted(accounts_cfg))
        # The cutoff reads the project's date basis, like the gains
        # year and t1135 (R1-10).
        _asof_basis_set = str(settings.get("tax_date") or "").strip().lower() \
            in ("trade", "settle")
        _asof_basis = (str(settings.get("tax_date")).strip().lower()
                       if _asof_basis_set else
                       _tax_date({"country": country}))
        files = {}
        tmp_docs = {}
        _no_input = _accounts_skipped_for_no_inputs(root)
        _phantoms = root / "phantoms.json"
        for n in names:
            b = cache / f"{n}_base.json"
            if not b.exists():
                if args.account:
                    sys.exit(f"taxjson list: no {b.name} in {cache} "
                             f"(run `taxjson run` first).")
                if n in _no_input:
                    continue      # run skipped it: no inputs yet
                # Plain `list` shows this account; vanishing from the
                # as-of view with rc 0 was a silent drop (REVIEW #35).
                print(f"taxjson list: warning: {n} skipped — no "
                      f"{b.name} in {cache} (run `taxjson run`); the "
                      f"as-of total excludes it.", file=sys.stderr)
                continue
            cmd = [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                   "--country", country, "--year", year,
                   "--as-of", as_of, "--no-wash"] + option_timing_flags(
                       settings)
            if _asof_basis_set:
                cmd += ["--tax-date", _asof_basis]
            if accounts_cfg.get(n, {}).get("type") == "taxable":
                cmd.append("--taxable")
            # The same phantom openings every other recompute applies:
            # without them each phantom-backed position showed as a
            # large short (R1-187).
            if _phantoms.exists():
                cmd += ["--incomplete-history", str(_phantoms)]
            res = _run_cmd(cmd + [str(b)], capture_output=True)
            if res.returncode != 0:
                print(f"taxjson: warning: as-of compute failed for "
                      f"{n}: {(res.stderr or '').strip()[:200]}",
                      file=sys.stderr)
                continue
            tmp_docs[n] = json.loads(res.stdout)
            files[n] = b                      # key only; doc from memory
        if not files:
            sys.exit(f"taxjson list: no base files in {cache} "
                     f"(run `taxjson run` first).")
        # The base books are already ticker.map-consolidated; what the
        # recompute lacks is the s.47 blend and the cross-account wash
        # pass (R1-282, S044-21).
        _asof_word = ("settlement date" if _asof_basis == "settle"
                      else "trade date")
        basis = (f"as of {as_of} by {_asof_word} (per-account ACB, "
                 f"before the cross-account wash pass)")
        # A symbol held in two taxable accounts has ONE s.47 ACB on the
        # return (plain `list` shows it); this view recomputes each
        # account alone, so its cost differs — say so rather than
        # present it as the filing ACB (2026-09 audit S044-21).
        _held: Dict[str, List[str]] = {}
        for n, doc in tmp_docs.items():
            if accounts_cfg.get(n, {}).get("type") != "taxable":
                continue
            for it in (doc.get("inventory") or []):
                if abs(float(it.get("qty") or 0.0)) > 1e-9:
                    _held.setdefault(str(it.get("symbol")), []).append(n)
        _shared = sorted(s_ for s_, a in _held.items() if len(set(a)) > 1)
        if _shared:
            print(f"taxjson list: note: --date shows each account's OWN "
                  f"ACB; {len(_shared)} symbol(s) held in more than one "
                  f"taxable account ({', '.join(_shared[:5])}"
                  f"{' ...' if len(_shared) > 5 else ''}) have one "
                  f"blended (s.47) ACB on the return — plain `taxjson "
                  f"list` shows it for the current books.",
                  file=sys.stderr)
    else:
        files = resolve_gains_files(cache, args.account or None)
        if not files:
            if args.account:
                sys.exit(f"taxjson list: no gains for account "
                         f"{args.account!r} in {cache} (run `taxjson "
                         f"run` first, or check the name).")
            sys.exit(f"taxjson list: no gains files in {cache} "
                     f"(run `taxjson run` first).")
        basis = gains_basis_label(files)

    money = fmt_money               # shared report-layer formatter
    from taxjson.lib.report_model import fmt_qty as qfmt
    from taxjson.lib.core import is_option_symbol as _is_opt_sym
    from taxjson.lib.ticker_map import is_future_ticker as _is_future_sym

    header = ["ACCOUNT", "SYMBOL", "QTY", "COST", "COST/SH",
              "DEFERRED", "SINCE"]
    out_lines = [" ".join(header)]
    json_rows: List[Dict[str, Any]] = []
    total_cost = 0.0
    total_deferred = 0.0
    n_pos = 0
    year = None
    for acct, p in files.items():
        if as_of:
            data = tmp_docs[acct]
        else:
            data = _load_json_or_die(p)
        year = year or (data.get("summary") or {}).get("year")
        inv = sorted((data.get("inventory") or []),
                     key=lambda r: str(r.get("symbol") or ""))
        for h in inv:
            qty = float(h.get("qty", 0) or 0)
            if qty == 0:                       # fully closed — not a position
                continue
            if getattr(args, "negative", False) and qty > 0:
                continue
            cost = float(h.get("total_cost", 0) or 0)
            # Signed division on purpose: shorts carry qty < 0 and
            # total_cost < 0 (proceeds credited), so COST/SH comes out
            # positive — same convention as the holdings export. An
            # equity option is 100 shares a contract: per SHARE, like
            # harvest and the holdings report (it was per contract,
            # 100x theirs — S045-03).
            _sym = str(h.get("symbol") or "")
            _mult = (100.0 if _is_opt_sym(_sym)
                     and not _is_future_sym(_sym) else 1.0)
            cps = cost / (qty * _mult) if qty else 0.0
            deferred = float(h.get("deferred_wash", 0) or 0)
            out_lines.append(" ".join([
                acct, str(h.get("symbol") or "?"), qfmt(qty), money(cost),
                money(cps),
                money(deferred) if deferred > 0.005 else "-",
                str(h.get("position_start_date") or "-")]))
            json_rows.append({"account": acct,
                              "symbol": h.get("symbol"),
                              "qty": qty, "cost": round(cost, 2),
                              "cost_per_share": round(cps, 4),
                              "deferred_wash": round(deferred, 2),
                              "since": h.get("position_start_date"),
                              "last_acq_date": h.get("last_acq_date")})
            total_cost += cost
            total_deferred += deferred
            n_pos += 1

    negative_only = getattr(args, "negative", False)

    # What the rows are AS OF: the --date, or the end of the books
    # (plain `list` is end-of-data, not the tax year's Dec 31 — its
    # header said "as of tax year 2025" over 2026 positions, R1-282).
    horizon = as_of or _books_horizon(cache, list(files))
    if getattr(args, "json", False):
        doc = {"rows": json_rows, "basis": basis, "year": year,
               "as_of": horizon,
               "currency": _base_currency(root),
               "totals": {"positions": n_pos,
                          "book_cost": round(total_cost, 2),
                          "deferred_wash": round(total_deferred, 2)}}
        if negative_only:
            doc["filter"] = "negative"
        _json_out(doc)
        return

    if n_pos == 0:
        scope = f" for account {args.account!r}" if args.account else ""
        if negative_only:
            print(f"No negative positions{scope}.")
        else:
            print(f"No open positions{scope}.")
        return

    base = _base_currency(root)
    title = "NEGATIVE POSITIONS" if negative_only else "OPEN POSITIONS"
    when = (f"as of {as_of}" if as_of else
            f"as of the latest data in the books"
            + (f" ({horizon})" if horizon else ""))
    print(f"{title} — {base}, {when}, basis: {basis}  "
          f"(after ticker.map + base-currency conversion; COST is book cost)")
    print()
    _print_report_table(out_lines)
    print(f"\n{n_pos} position(s), total book cost {money(total_cost)} {base}")
    if total_deferred > 0.005:
        _us_l = _country(_soft_settings(root)) == "usa"
        print(f"DEFERRED: {money(total_deferred)} {base} of the book "
              f"cost is "
              + ("losses disallowed under the wash-sale rule (§1091), "
                 "added to these positions' basis"
                 if _us_l else
                 "denied superficial losses parked in these positions")
              + " (recovered when sold without a rebuy in the window).")


def _books_horizon(cache: Path, accounts: List[str]) -> Optional[str]:
    """Latest transaction date across these accounts' base books."""
    import json as _json
    last = None
    for a in accounts:
        try:
            txs = _read_work_doc(cache / f"{a}_base.json").get(
                "transactions", [])
        except (OSError, ValueError, AttributeError):
            continue
        for t in txs:
            d = str(t.get("date") or "")[:10]
            if len(d) == 10 and (last is None or d > last):
                last = d
    return last


def _soft_config(root: Path) -> Dict[str, Any]:
    """Whole taxjson.toml (soft-read; {} when absent or unreadable). The
    single home for the query wrappers' config reads — they must work
    from work/ files without a hard config dependency."""
    cfg_path = root / "taxjson.toml"
    if cfg_path.exists() and tomllib is not None:
        try:
            cfg = tomllib.loads(_read_config_text(cfg_path)) or {}
        except Exception as e:
            # Soft about a MISSING config only. An existing file that
            # does not parse is a user error to fix, not a reason to
            # guess: the {} fallback converted a USD-base project's fees
            # to CAD at an invented 1.35, dropped fees-sum's sibling
            # guard, and made the radar treat every registered book as
            # taxable (audit S049-00, S048-13).
            _die(f"{cfg_path} is not valid TOML ({e}) — fix it before "
                 f"running this command.")
        # Soft about a MISSING or unreadable config, never about an
        # account the filing commands would silently drop (R1-268).
        _refuse_bad_account_types(cfg)
        _normalize_settings(cfg)
        return cfg
    return {}


def _soft_settings(root: Path) -> Dict[str, Any]:
    """[settings] table from taxjson.toml (soft-read; {} when absent)."""
    return _soft_config(root).get("settings", {}) or {}


def _base_currency(root: Path) -> str:
    """Base currency label from taxjson.toml: [settings] base_currency,
    else the country's own currency (never a silent CAD for a US
    project)."""
    return _base(_soft_settings(root))


def cmd_wash_sales(args: argparse.Namespace) -> None:
    """Detail each superficial-loss (wash sale) that OCCURRED in the tax year —
    the losses the engine denied, which `<account>.sum` folds silently into the
    ticker totals. Reads the canonical per-account gains, preferring the
    cross-account `<account>_gains_wash.json` (which also catches registered-
    account repurchases) when the pipeline built it, else `<account>_gains.json`.
    One row per denied disposition + a denied total. With `--explain`, print the
    full ACB / superficial-loss calculation trace for each wash sale instead of
    the table."""
    import json
    root = Path(args.dir).resolve()
    cache = root / "work"

    if args.explain:
        if getattr(args, "json", False):
            # The trace is prose from taxjson-explain — silently
            # printing text under --json broke machine consumers.
            sys.exit("taxjson wash-sales: --explain has no JSON form; "
                     "drop --json (or drop --explain for the "
                     "machine-readable table).")
        _explain_wash_sales(root, cache, args.account)
        return                                          # (raises SystemExit)

    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    resolved = resolve_gains_files(cache, args.account or None)
    if not resolved:
        if args.account:
            sys.exit(f"taxjson wash-sales: no gains for account "
                     f"{args.account!r} in {cache} (run `taxjson run` first, "
                     f"or check the name).")
        sys.exit(f"taxjson wash-sales: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    if not args.account:
        _warn_accounts_without_books(root, resolved, "wash-sales",
                                     "gains file")
    files = list(resolved.items())

    money = fmt_money               # shared report-layer formatter
    from taxjson.lib.report_model import fmt_qty as qfmt

    header = ["ACCOUNT", "DATE", "SYMBOL", "QTY", "PROCEEDS", "COST",
              "GAIN", "DENIED", "ALLOWED"]
    rows = []
    year = None
    for acct, f in files:
        data = _load_json_or_die(f)
        year = year or (data.get("summary") or {}).get("year")
        for t in data.get("transactions", []):
            if t.get("is_wash_sale"):
                rows.append((t.get("date") or "", acct, t))
    rows.sort(key=lambda r: (r[0], r[1], str(r[2].get("symbol") or "")))

    # Deferred amounts still embedded in OPEN positions (across the
    # same canonical files): ties the historical denials to the present.
    embedded = 0.0
    for _acct, f in files:
        try:
            _d2 = _read_work_doc(f)
        except (OSError, ValueError):
            continue
        for h in _d2.get("inventory") or []:
            embedded += float(h.get("deferred_wash", 0) or 0)

    if getattr(args, "json", False):
        jd = jp = 0.0
        for _d, _a, t in rows:
            jd += float(t.get("disallowed_amount") or 0)
            jp += float(t.get("permanently_disallowed") or 0)
        _json_out({"rows": [dict(t, account=acct)
                            for _d, acct, t in rows],
                   "totals": {"denied": round(jd, 2),
                              "permanently_denied": round(jp, 2),
                              "embedded_in_open": round(embedded, 2)},
                   "year": year, "currency": _base_currency(root),
                   "basis": gains_basis_label(resolved)})
        return

    if not rows:
        scope = f" for account {args.account!r}" if args.account else ""
        print(f"No wash sales{scope} — no losses were denied.")
        return

    out_lines = [" ".join(header)]
    total_denied = total_perm = 0.0
    for date, acct, t in rows:
        econ = float(t.get("raw_gain") or 0)        # true economic gain/loss
        denied = float(t.get("disallowed_amount") or 0)
        perm = float(t.get("permanently_disallowed") or 0)
        allowed = econ + denied                     # loss you can claim now
        _p, _c = _real_world_legs(t)                # S048-10
        out_lines.append(" ".join([
            acct, date or "-", str(t.get("symbol") or "?"),
            qfmt(float(t.get("qty") or 0)), money(_p),
            money(_c), money(econ), money(denied),
            money(allowed)]))
        total_denied += denied
        total_perm += perm

    base = _base_currency(root)
    _usa = _country(_soft_settings(root)) == "usa"
    print(f"WASH SALES — {base}, tax year {year}, basis: "
          f"{gains_basis_label(resolved)}  "
          f"(losses denied under "
          f"{'the wash-sale rule, §1091' if _usa else 'the superficial-loss rule, s.54'})")
    print()
    _print_report_table(out_lines)
    perm_note = (f" ({money(total_perm)} permanently denied)"
                 if total_perm > 0.005 else "")
    print(f"\n{len(rows)} wash sale(s); {money(total_denied)} {base} of losses "
          f"denied{perm_note}.")
    if embedded > 0.005:
        print(f"Currently embedded in OPEN positions: {money(embedded)} "
              f"{base} of deferred losses (see `taxjson list` DEFERRED).")
    print("DENIED is added to the cost basis of the repurchased shares (you "
          "recover it on a later sale) — except any permanently-denied amount "
          "from a repurchase in "
          + ("an IRA" if _usa else "a registered account")
          + ", which is lost for good.")


def cmd_t1135(args: argparse.Namespace) -> None:
    """`taxjson t1135`: CRA T1135 foreign-property helper over ALL taxable
    accounts (T1135 is a per-person form; registered accounts are excluded
    from specified foreign property by law, so sheltered accounts are never
    read). Feeds every taxable `<account>_base.json` (full history, base
    currency) plus the year-scoped gains files — preferring the wash-adjusted
    `<account>_gains_wash.json` so the GAIN(LOSS) column matches what
    Schedule 3 reports — into taxjson-t1135."""
    from taxjson.bin import taxjson_t1135
    from taxjson.lib.report_model import resolve_gains_files

    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = cfg.get("settings", {})
    year = settings.get("year")
    if year is None:
        sys.exit("taxjson t1135: no [settings] year in taxjson.toml")
    base_currency = str(_base(settings))
    if base_currency.upper() != "CAD":
        print(f"note: base_currency is {base_currency} — T1135 amounts must "
              f"be reported in CAD; these figures are in {base_currency}.",
              file=sys.stderr)

    taxable = [n for n, c in cfg.get("accounts", {}).items()
               if c.get("type") == "taxable"]
    if not taxable:
        sys.exit("taxjson t1135: no taxable accounts in taxjson.toml — "
                 "T1135 applies to non-registered property only.")

    # All positionals first: argparse won't accept a second base file
    # after an interleaved --gains (the nargs="+" positional is consumed
    # greedily on first contact).
    base_argv: List[str] = []
    gains_argv: List[str] = []
    missing: List[str] = []
    for name in sorted(taxable):
        base = cache / f"{name}_base.json"
        if not base.exists():
            missing.append(name)
            continue
        base_argv.append(str(base))
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            gains_argv += ["--gains", str(gains)]
    argv: List[str] = base_argv + gains_argv
    # A configured taxable account with inputs but no books made the
    # filing-threshold test read "not required" with rc 0 (S006-03).
    _require_taxable_books(root, cfg,
                           {n: 1 for n in taxable if n not in missing},
                           "base book")
    missing = [n for n in missing
               if n not in _accounts_skipped_for_no_inputs(root)]
    if missing:
        print(f"taxjson: warning: no base file for taxable account(s) "
              f"{', '.join(missing)} — run `taxjson run` first; the "
              f"threshold test below may be understated.", file=sys.stderr)
    if not argv:
        sys.exit(f"taxjson t1135: no taxable base files in {cache} "
                 f"(run `taxjson run` first).")
    # The income/gain columns come from the year-scoped gains files: on
    # books built for another year they silently read 0 (S007-06,
    # S048-01).
    _warn_artifact_year(
        {Path(g).name.rsplit("_gains", 1)[0]: Path(g)
         for g in gains_argv[1::2]}, year)
    _warn_run_state(root, cfg)

    argv += ["--year", str(year), "--base-currency", base_currency,
             # S052-10: the gain join and the cost walk follow the
             # project's tax_date, like the gains files.
             "--tax-date", _tax_date_basis(settings)]
    t1135_map = root / "t1135.map"
    if t1135_map.exists():
        argv += ["--map", str(t1135_map)]
    # The same phantom openings the gains stage applies (R1-321): without
    # them a phantom-backed position read as a short that later real
    # buys covered at zero cost.
    phantoms = root / "phantoms.json"
    if phantoms.exists():
        argv += ["--incomplete-history", str(phantoms)]
    # The full-history superficial-loss pass (S008-07) sees what the
    # pipeline's wash pass sees: the registered accounts as context and
    # the project's written-option timing.
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        argv += ["--sheltered", str(sheltered_base)]
    argv += option_timing_flags(settings)
    if args.json:
        argv.append("--json")
    raise SystemExit(taxjson_t1135.main(argv))


def cmd_carryover(args: argparse.Namespace) -> None:
    """`taxjson carryover`: multi-year capital-loss carryforward/carryback
    ledger over all taxable accounts' full-history books. Passes the
    combined sheltered book for wash-window context and the project's
    phantoms.json when present. A root `claimed_losses.txt` (YEAR AMOUNT
    lines) is picked up automatically to fold in what was actually
    claimed on filed returns."""
    from taxjson.bin import taxjson_carryover

    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = cfg.get("settings", {})

    taxable = [n for n, c in cfg.get("accounts", {}).items()
               if c.get("type") == "taxable"]
    if not taxable:
        sys.exit("taxjson carryover: no taxable accounts in taxjson.toml.")
    _acct_cfg = cfg.get("accounts", {})
    base_argv: List[str] = []
    crypto_argv: List[str] = []
    missing: List[str] = []
    for name in sorted(taxable):
        base = cache / f"{name}_base.json"
        if not base.exists():
            missing.append(name)
        elif (_acct_cfg.get(name) or {}).get("crypto"):
            crypto_argv += ["--crypto", str(base)]
        else:
            base_argv.append(str(base))
    # An account the last run skipped for having no inputs has no books
    # to miss — the warning fired for the scaffold's empty `crypto`
    # account on every carryover (R1-264), unlike t1135 and the rest.
    missing = [n for n in missing
               if n not in _accounts_skipped_for_no_inputs(root)]
    if missing:
        print(f"taxjson: warning: no base file for taxable account(s) "
              f"{', '.join(missing)} — run `taxjson run` first; those "
              f"years' nets will be incomplete.", file=sys.stderr)
    if not base_argv and not crypto_argv:
        sys.exit(f"taxjson carryover: no taxable base files in {cache} "
                 f"(run `taxjson run` first).")
    if not base_argv:
        # positional FILEs are required; a crypto-only project feeds
        # its books positionally (the standalone folds/splits them by
        # country policy either way).
        base_argv = [crypto_argv[i + 1]
                     for i in range(0, len(crypto_argv), 2)]
        crypto_argv = []

    argv = base_argv + crypto_argv + [
        "--country", _country(settings),
        "--base-currency", str(_base(settings)),
        # The same year attribution as run / close-year (R1-192).
        "--tax-date", _tax_date_basis(settings),
    ] + option_timing_flags(settings)       # same timing as the returns
    if settings.get("year") is not None:
        # Rows before the project year are flagged as possibly partial.
        argv += ["--project-year", str(int(settings["year"]))]
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        argv += ["--sheltered", str(sheltered_base)]
    phantoms = root / "phantoms.json"
    if phantoms.exists():
        argv += ["--incomplete-history", str(phantoms)]
    claimed = Path(args.claimed) if args.claimed else root / "claimed_losses.txt"
    if claimed.exists():
        argv += ["--claimed", str(claimed)]
    elif args.claimed:
        sys.exit(f"taxjson carryover: no such claimed file: {claimed}")
    # Each locked year's filed realized total: the ledger recomputes
    # every year with THIS project's option timing, and a locked year it
    # disagrees with is flagged (S047-21, S048-20).
    from taxjson.bin import taxjson_filed
    import json as _json
    for _yr, _lp in taxjson_filed.list_snapshots(root):
        try:
            _lock = _json.loads(_lp.read_text(encoding="utf-8"))
            _real = float((_lock.get("totals") or {})["realized"])
        except (OSError, ValueError, KeyError, TypeError,
                AttributeError):
            continue
        argv += ["--filed", f"{_yr}={_real!r}"]
    _w = _grant_since_warning(settings)
    if _w:
        print(f"taxjson carryover: warning: {_w}", file=sys.stderr)
    # Deferred / failed / validation-ERROR books drive the carryforward
    # balance too (S048-21).
    _warn_run_state(root, cfg)
    if args.json:
        argv.append("--json")
    raise SystemExit(taxjson_carryover.main(argv))


def _taxable_gains_argv(root: Path, cache: Path, *,
                        exclude_crypto: bool = False,
                        prog: str = "taxjson",
                        required: bool = False) -> List[str]:
    """`--gains FILE` pairs for every taxable account, preferring the
    wash-adjusted `<account>_gains_wash.json` (the allowed numbers a return
    reports). Shared by the form-export and reconcile-slips wrappers.

    `exclude_crypto` drops `crypto = true` accounts (with a note): no
    exchange issues a T5008/1099-B, so feeding a crypto book to the slip
    reconciler made every one of its dispositions MISSING_FROM_SLIP and
    the command could never exit 0 (2026-09 audit)."""
    cfg = load_config(root)
    accounts_cfg = cfg.get("accounts", {})
    taxable = [n for n, c in accounts_cfg.items()
               if c.get("type") == "taxable"]
    if not taxable:
        _die("no taxable accounts in taxjson.toml")
    if exclude_crypto:
        crypto = sorted(n for n in taxable if accounts_cfg[n].get("crypto"))
        if crypto:
            print(f"{prog}: note: crypto account(s) {', '.join(crypto)} "
                  f"excluded — exchanges issue no T5008/1099-B slips, so "
                  f"there is nothing to reconcile them against.",
                  file=sys.stderr)
            taxable = [n for n in taxable if n not in crypto]
        if not taxable:
            _die("every taxable account is crypto — no slip data exists "
                 "for crypto dispositions (exchanges issue no "
                 "T5008/1099-B); nothing to reconcile.")
    from taxjson.lib.report_model import resolve_gains_files
    argv: List[str] = []
    _no_input = _accounts_skipped_for_no_inputs(root)
    have = {}
    for name in sorted(taxable):
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            argv += ["--gains", str(gains)]
            have[name] = gains
        elif required and _has_inputs(root, name):
            pass                        # refused below, all at once
        elif name not in _no_input:
            print(f"taxjson: warning: no gains file for taxable account {name!r} — "
                  f"run `taxjson run` first.", file=sys.stderr)
    if required:
        # The Schedule 3 / 8949 export and the slip check must not be
        # partial: a failed account was a one-line warning with rc 0,
        # and the checklist certified the partial export (S045-21).
        _require_taxable_books(
            root, {"accounts": {n: accounts_cfg[n] for n in taxable}},
            have, "gains file")
    if not argv:
        _die(f"no taxable gains files in {cache} "
                 f"(run `taxjson run` first).")
    return argv


def cmd_form_export(args: argparse.Namespace) -> None:
    """`taxjson form-export`: render the year's gains as IRS Form 8949 or
    CRA Schedule 3 rows. Form defaults from the project country."""
    from taxjson.bin import taxjson_form_export

    root = Path(args.dir).resolve()
    cache = root / "work"
    _cfg = load_config(root)
    settings = _cfg.get("settings", {})
    country = _country(settings)
    form = args.form or ("8949" if country == "usa" else "schedule3")
    year = settings.get("year")
    _txf_only = [f for f, v in (("--out", getattr(args, "out", None)),
                                ("--box", getattr(args, "box", None)))
                 if v is not None]
    if form != "txf" and _txf_only:
        # Were silently ignored: `--out gains.txf` wrote nothing and
        # printed the table to stdout (2026-09 CLI audit B19).
        _die(f"{' and '.join(_txf_only)} only "
             f"{'applies' if len(_txf_only) == 1 else 'apply'} to --form txf "
             f"(this run renders {form}); use --csv FILE to save the "
             f"{form} rows.")

    gains_argv = _taxable_gains_argv(root, cache, required=True)
    # form-export takes gains files positionally.
    files = [gains_argv[i + 1] for i in range(0, len(gains_argv), 2)]
    _warn_artifact_year({Path(f).name.rsplit("_gains", 1)[0]: Path(f)
                         for f in files}, year)
    _warn_run_state(root, _cfg)
    # Crypto books go on Schedule 3's crypto-assets line: name them.
    from taxjson.lib.report_model import resolve_gains_files
    _crypto_files = []
    for _n, _c in sorted((_cfg.get("accounts") or {}).items()):
        if (_c or {}).get("type") == "taxable" and (_c or {}).get("crypto"):
            _g = resolve_gains_files(cache, _n).get(_n)
            if _g is not None:
                _crypto_files += ["--crypto", str(_g)]
    argv = files + _crypto_files + ["--form", form, "--country", country,
                    "--base-currency",
                    str(settings.get("base_currency", "")),
                    # R1-200: rows are picked by the date the gains
                    # files were scoped on, not the form's default.
                    "--date-basis", _tax_date_basis(settings)]
    if year is not None:
        argv += ["--year", str(year)]
    if args.csv:
        argv += ["--csv", args.csv]
    if args.json:
        argv.append("--json")
    if form == "txf":
        argv += ["--box", args.box or "A"]
        if args.out:
            argv += ["--out", args.out]
    raise SystemExit(taxjson_form_export.main(argv))


def cmd_harvest(args: argparse.Namespace) -> None:
    """`taxjson harvest [SYMBOL]`: unrealized gain/(loss) per open
    position at current prices — "if I sold this today, is it a loss?" —
    over every taxable account's wash-adjusted books, harvestable losses
    first, each loss annotated with the wash radar's advisory. Prices
    resolve IBKR -> yfinance -> cache (work/.price_cache.json). Runs
    with cwd=project root so a root yf_ticker.map is found. Accounts
    with `crypto = true` are excluded unless --crypto is given (the
    price chain serves stock snapshots)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = _soft_settings(root)
    cfg = load_config(root)
    accounts_cfg = cfg.get("accounts", {})
    # Crypto accounts are EXCLUDED by default: the price chain serves
    # stock snapshots, so crypto symbols just spray yfinance lookup
    # errors. `--crypto` opts them back in.
    include_crypto = getattr(args, "crypto", False)

    def _skip_crypto(acfg) -> bool:
        return bool(acfg.get("crypto", False)) and not include_crypto

    skipped = [n for n, c in sorted(accounts_cfg.items())
               if c.get("type") == "taxable" and _skip_crypto(c)]
    taxable = [n for n, c in sorted(accounts_cfg.items())
               if c.get("type") == "taxable" and not _skip_crypto(c)]
    if skipped:
        note("taxjson harvest",
             f"crypto account(s) excluded: {', '.join(skipped)} "
             f"(pass --crypto to include them).")
    if not taxable:
        sys.exit("taxjson harvest: no (non-crypto) taxable accounts — "
                 "pass --crypto to harvest crypto accounts.")
    from taxjson.lib.report_model import resolve_gains_files
    files = []
    _no_input = _accounts_skipped_for_no_inputs(root)
    for name in taxable:
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            files.append(str(gains))
        elif name not in _no_input:
            print(f"taxjson: warning: no gains file for taxable account "
                  f"{name!r} — run `taxjson run` first.", file=sys.stderr)
    if not files:
        sys.exit(f"taxjson harvest: no taxable gains files in {cache} "
                 f"(run `taxjson run` first).")
    cmd = _cmd("taxjson-harvest") + files + [
        "--price-cache", str(cache / ".price_cache.json"),
        "--country", _country(settings),
        "--base-currency", str(_base(settings)),
    ]
    # --options was defined-but-never-forwarded: the wrapper (and the
    # GUI's include-options toggle, which calls through it) silently
    # ignored the flag (round-five audit finding 8).
    if getattr(args, "options", False):
        cmd.append("--options")
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
    _books = [p for p in cache.glob("*_base.json")
              if not p.name.startswith(".")]
    _newest_book = max((p.stat().st_mtime for p in _books), default=0.0)
    if radar_files and any(sc.stat().st_mtime < _newest_book
                           for sc in radar_files):
        from taxjson.lib.dispatch import run_cmd as _run_live
        _live = cache / ".harvest_radar.json"
        _bases = _radar_taxable_bases(root, cache, "taxjson harvest",
                                      empty_ok=True)
        _rcmd = _cmd("taxjson-wash-radar") + [
            "--taxable", *[str(b) for b in _bases],
            "--json-out", str(_live), "--account", "LIVE"]
        _rcmd += _radar_engine_args(_bases, root / "phantoms.json",
                                    _country(settings))
        if (cache / "sheltered_base.json").exists():
            _rcmd += ["--sheltered", str(cache / "sheltered_base.json")]
        _res = _run_live(_rcmd, capture_output=True)
        if _res.returncode == 0 and _live.exists():
            note("taxjson harvest",
                 "the wash-radar reports are older than the books (a "
                 "single-account run?) — using a live radar; run a full "
                 "`taxjson run` to refresh reports/.")
            radar_files = [_live]
        else:
            print("taxjson harvest: warning: the wash-radar reports are "
                  "older than the books and a live radar failed — the "
                  "ADVISORY column is left empty (losses count as 'no "
                  "clear date'); run a full `taxjson run`.",
                  file=sys.stderr)
            radar_files = []
    for sidecar in radar_files:
        cmd += ["--radar", str(sidecar)]
    _tmap = root / "ticker.map"
    if _tmap.exists():
        cmd += ["--ticker-map", str(_tmap)]
    # Sheltered accounts' inventories feed the SH_QTY / SH_ADD columns:
    # shares held sheltered and days since the sheltered side last
    # acquired (a sheltered add within the 30-day window makes a
    # harvested loss permanently denied). Crypto sheltered accounts
    # follow the same --crypto gate as taxable ones.
    for name in sorted(n for n, c in accounts_cfg.items()
                       if c.get("type") != "taxable"
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
    if args.verbose:
        cmd.append("--verbose")
    _exec_tool(cmd, cwd=str(root))


def _artifact_year_mismatch(files: Dict[str, Path],
                            config_year) -> Dict[str, str]:
    """{account: artifact_year} for each gains file whose recorded tax
    year (summary.year, written by the engine) differs from
    [settings].year. After a year bump without a rebuild, work/ still
    holds LAST year's books: close-year would lock them under the new
    year and sum/estimate/form-export would present them as the new
    year's figures (2026-09 CLI audit B2). Unreadable files and files
    without a recorded year are skipped (their own readers complain)."""
    import json as _json
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
    _pfx = f"taxjson {_CURRENT_CMD}" if _CURRENT_CMD else "taxjson"
    print(f"{_pfx}: WARNING: [settings].year is {config_year} but the "
          f"work/ books were built for another tax year: {got}. These "
          f"figures are NOT {config_year}'s — rebuild with `taxjson run` "
          f"first.", file=sys.stderr)


def _tax_date_basis(settings: Dict[str, Any]) -> str:
    """The date the project's gains files are year-scoped on: the
    explicit [settings] tax_date, else the country default (CRA:
    settlement, IRS: trade) — what stage_account feeds the engine."""
    return _tax_date(settings)


def _has_inputs(root: Path, name: str) -> bool:
    """inputs/<name>/ holds an activity file `taxjson run` reads."""
    d = root / "inputs" / name
    return bool(input_files(d, ".csv") or input_files(d, ".tt"))


def _run_state_problems(root: Path, cfg: Dict[str, Any]) -> List[str]:
    """What `taxjson checklist`'s run-clean step finds wrong with the
    books the filing commands read: validation ERRORs in the last run,
    an account deferred on pending elections, inputs changed since the
    last full run (a failed run leaves them newer than the artifacts),
    an account with inputs but no report. [] when clean or when there
    is nothing to judge (no reports yet). The report commands used to
    serve these books with rc 0 and no word (R1-252, S049-11)."""
    try:
        from datetime import date as _d
        from taxjson.lib.checklist import Ctx, d_run_clean
        year = int((cfg.get("settings") or {}).get("year") or 0)
        res = d_run_clean(Ctx(root, cfg, year, _d.today(),
                              lambda *a, **k: (0, "", "")))
    except Exception:                                   # noqa: BLE001
        return []
    if res.status != "attention":
        return []
    return [x.strip() for x in res.detail.split("; ") if x.strip()]


def _warn_run_state(root: Path, cfg: Dict[str, Any]) -> List[str]:
    """Loud stderr banner for _run_state_problems; returns them."""
    probs = _run_state_problems(root, cfg)
    if probs:
        _pfx = f"taxjson {_CURRENT_CMD}" if _CURRENT_CMD else "taxjson"
        print(f"{_pfx}: WARNING: these books are not the clean result of "
              f"the current inputs — {'; '.join(probs)}. The figures "
              f"below may leave sales out or carry default FX; fix and "
              f"re-run `taxjson run` before using them.", file=sys.stderr)
    return probs


def _require_taxable_books(root: Path, cfg: Dict[str, Any],
                           have: Dict[str, Any], what: str) -> None:
    """Die when a configured taxable account that HAS inputs has no
    `what` artifact: a partial filing figure (form-export, t1135,
    close-year) was reported with rc 0 and a one-line warning
    (S045-21, S006-03, S006-07)."""
    missing = sorted(
        n for n, c in (cfg.get("accounts") or {}).items()
        if isinstance(c, dict) and c.get("type") == "taxable"
        and n not in have and _has_inputs(root, n))
    if missing:
        _die(f"no {what} for taxable account(s) {', '.join(missing)} — "
             f"the last `taxjson run` did not build them (did it fail?). "
             f"Fix and re-run `taxjson run`; figures without them would "
             f"leave those accounts out.")


def _filed_run_gains(cmd_tail, out_path):
    """taxjson-gains invocation for the filed-year recompute."""
    run_to_file(_cmd("taxjson-gains") + cmd_tail, Path(out_path),
                capture_diag=False)


def cmd_close_year(args: argparse.Namespace) -> None:
    """`taxjson close-year`: snapshot the current tax year's per-account
    filing aggregates to filed/<year>.json (the filed-year lock)."""
    from taxjson.bin import taxjson_filed
    from taxjson.lib.report_model import (gains_basis_label,
                                          resolve_gains_files)
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = cfg["settings"]
    year = args.year or settings.get("year")
    if year != settings.get("year"):
        sys.exit("taxjson close-year: the work/ gains artifacts are "
                 f"scoped to tax year {settings.get('year')} — set "
                 "[settings].year to the year you are closing, run "
                 "`taxjson run`, then close.")
    taxable = {n for n, c in cfg.get("accounts", {}).items()
               if c.get("type") == "taxable"}
    files = {a: p for a, p in resolve_gains_files(cache).items()
             if a in taxable}
    if not files:
        sys.exit("taxjson close-year: no taxable gains files in work/ — "
                 "run `taxjson run` first.")
    _bad_year = _artifact_year_mismatch(files, settings.get("year"))
    if _bad_year:
        sys.exit(f"taxjson close-year: the work/ books were built for "
                 f"another tax year ("
                 f"{', '.join(f'{a}: {y}' for a, y in sorted(_bad_year.items()))}"
                 f") but [settings].year is {settings.get('year')} — "
                 f"rebuild with `taxjson run` first, then close. "
                 f"Nothing was written.")
    # A taxable account with inputs but no gains file (its stage
    # failed) was left out of the lock without a word, and then never
    # drift-checked (S006-07).
    _require_taxable_books(root, cfg, files, "gains file")
    # The lock certifies the filing numbers: books from a run with
    # validation ERRORs, a pending-election deferral or inputs changed
    # since (a failed run) are not locked without --force (S048-23,
    # S049-11).
    _state = _run_state_problems(root, cfg)
    if _state and not args.force:
        sys.exit("taxjson close-year: the books are not the clean result "
                 "of the current inputs — " + "; ".join(_state)
                 + ". Fix and re-run `taxjson run`, then close (or pass "
                 "--force to lock them anyway). Nothing was written.")
    if _state:
        print("taxjson close-year: WARNING: locking books with open "
              "problems (--force): " + "; ".join(_state),
              file=sys.stderr)
    # Equity taxable accounts always get the blended cross-account pass
    # in a full run; a plain <acct>_gains.json means only `run --account`
    # ran (or an account is deferred) — per-account, unblended numbers
    # that check-filed's blended recompute then reports as DRIFT
    # (S046-01).
    _unblended = sorted(
        a for a, pth in files.items()
        if not (cfg.get("accounts", {}).get(a) or {}).get("crypto")
        and not Path(pth).name.endswith("_gains_wash.json"))
    if _unblended:
        sys.exit(f"taxjson close-year: {', '.join(_unblended)} "
                 f"only has per-account (pre-wash, unblended) gains — "
                 f"the cross-account pass of a full `taxjson run` never "
                 f"ran. Run `taxjson run` with no --account first. "
                 f"Nothing was written.")
    # Staleness guard: after `run --account X` the wash file predates
    # the just-rebuilt plain gains (the blend pass was skipped).
    # `taxjson sum` merely notes this; close-year WRITES the filing
    # lock, so snapshotting stale numbers is a hard stop.
    # A sheltered rebuild (`run --account <sheltered>`) refreshes
    # sheltered_base.json without the wash pass — the same stale lock
    # (2026-09 audit R1-251).
    from taxjson.lib.report_model import stale_wash_inputs
    stale = sorted(
        f"{a} (older than {', '.join(stale_wash_inputs(p))})"
        for a, p in files.items()
        if p.name.endswith("_gains_wash.json") and stale_wash_inputs(p))
    if stale:
        sys.exit(f"taxjson close-year: wash-adjusted gains for "
                 f"{'; '.join(stale)} are STALE (a --account rerun "
                 f"skipped the cross-account wash pass) — run a full "
                 f"`taxjson run` first. Nothing was written.")
    accounts = {}
    raw_aggs = {}
    _ot_docs: Dict[str, Dict[str, Any]] = {}
    import json as _json
    for acct, pth in sorted(files.items()):
        _crypto = bool((cfg.get("accounts", {}).get(acct) or {})
                       .get("crypto"))
        try:
            doc = _read_work_doc(Path(pth))
            _ot_docs[acct] = doc
            accounts[acct] = taxjson_filed.aggregates_from_gains(
                doc, crypto=_crypto, year=int(year))
            raw_aggs[acct] = taxjson_filed.aggregates_from_gains(
                doc, rounded=False)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            # Unreadable, not JSON, or JSON of the wrong shape (a list):
            # one line, never a traceback (S031-19).
            sys.exit(f"taxjson close-year: could not read {pth}: {e} — "
                     f"re-run `taxjson run` to rebuild it. Nothing was "
                     f"written.")
    # A year with no disposition and no income in any taxable book is
    # almost always a typo'd [settings] year (2015, 2204): the lock
    # then guarded an all-zero "filed" year as OK (S045-24).
    if (not any(int(a.get("dispositions") or 0) for a in accounts.values())
            and not any(abs(float(a.get("income") or 0.0)) >= 0.005
                        for a in accounts.values())):
        if not args.force:
            sys.exit(f"taxjson close-year: the taxable books hold no "
                     f"disposition and no income in {year} — check "
                     f"[settings] year (pass --force to lock an empty "
                     f"year). Nothing was written.")
        print(f"taxjson close-year: WARNING: locking {year} with no "
              f"disposition and no income (--force).", file=sys.stderr)
    basis = gains_basis_label(files)
    from taxjson.lib.pipeline import option_timing_from_settings
    from taxjson.lib import handoff as _handoff
    # The lock's option timing describes its totals: it was stamped from
    # the CURRENT taxjson.toml, so a timing edited after the run locked
    # grant-timed totals as close timing and option-boundary advised a
    # T1-ADJ for a premium the lock already held (S046-02).
    _want_ot = option_timing_from_settings(settings) or {}
    _ot_bad = []
    for _a, _doc in sorted(_ot_docs.items()):
        _sm = _doc.get("summary") or {}
        if "option_premium_timing" not in _sm or not _want_ot:
            continue
        _got = (str(_sm.get("option_premium_timing") or "close"),
                _sm.get("option_grant_since"))
        _exp = (_want_ot.get("option_premium_timing"),
                _want_ot.get("option_grant_since"))
        if _got[0] != _exp[0] or (_exp[0] == "grant"
                                  and _got[1] != _exp[1]):
            _ot_bad.append(f"{_a}: built with {_got[0]} timing"
                           + (f" since {_got[1]}" if _got[0] == "grant"
                              else ""))
    if _ot_bad:
        sys.exit(f"taxjson close-year: the gains files were built with "
                 f"another option timing than taxjson.toml now says "
                 f"({'; '.join(_ot_bad)}; settings: "
                 f"{_exp[0]}"
                 + (f" since {_exp[1]}" if _exp[0] == "grant" else "")
                 + ") — run `taxjson run`, then close. Nothing was "
                   "written.")
    filed_csv = (Path(args.filed_dispositions).expanduser()
                 if getattr(args, "filed_dispositions", None) else None)
    if filed_csv is not None and not filed_csv.exists():
        sys.exit(f"taxjson close-year: {filed_csv} not found.")
    if taxjson_filed.snapshot_path(root, year).exists() and not args.force:
        sys.exit(f"taxjson close-year: {taxjson_filed.snapshot_path(root, year)}"
                 f" already exists — the lock protects a filed year. "
                 f"Re-run with --force to replace it (only if you "
                 f"re-filed/amended).")
    # The lock records a FILED return: a year that has not ended cannot
    # have been filed — the lock then drifted on every later run and
    # the checklist said "Return filed and the year locked" (S045-23).
    from datetime import date as _date_cy
    if _date_cy.today() <= _date_cy(int(year), 12, 31):
        if not args.force:
            sys.exit(f"taxjson close-year: tax year {year} has not ended "
                     f"(today is {_date_cy.today().isoformat()}) — the "
                     f"lock records the FILED return; close the year "
                     f"after you file (or pass --force to snapshot it "
                     f"anyway). Nothing was written.")
        print(f"taxjson close-year: WARNING: tax year {year} has not "
              f"ended — locking a partial year (--force); later trades "
              f"in {year} will show as drift.", file=sys.stderr)
    print(f"  recording year-end positions and the {year} dispositions "
          f"for the {int(year) + 1} hand-off ...")
    try:
        extra = _handoff.record_fields(
            root, cfg, int(year), files, _filed_run_gains,
            _handoff_gains_flags(settings), filed_csv)
    except ValueError as e:
        sys.exit(f"taxjson close-year: {e}")
    path = taxjson_filed.write_snapshot(
        root, year, _normalize_country(settings["country"]), basis,
        accounts, force=args.force,
        option_timing=option_timing_from_settings(settings) or None,
        extra=extra, raw=raw_aggs)
    tot = _json.loads(path.read_text())["totals"]
    print(f"closed {year} ({basis}): realized {tot['realized']:,.2f}, "
          f"disallowed {tot['disallowed']:,.2f}, income "
          f"{tot['income']:,.2f} across {len(accounts)} account(s)")
    print(f"  -> {path}  (commit this with your records; "
          f"`taxjson check-filed` now guards it)")
    _ft = extra.get("filed_totals")
    if _ft:
        print(f"  as filed ({_ft['source']}): {_ft['dispositions']} "
              f"dispositions, gain {_ft['gain']:,.2f} — the {int(year) + 1}"
              f" project's `taxjson handoff` checks against these.")
    _ye = extra.get("year_end") or {}
    print(f"  year-end positions: "
          + ", ".join(f"{g} {len(v)}" for g, v in _ye.items())
          + f"; trades settling in {int(year) + 1}: "
          f"{len(extra.get('settle_next_year') or [])}")


def _handoff_gains_flags(settings: Dict[str, Any]) -> List[str]:
    """The taxjson-gains flags a full-history run of this project uses
    (no --year: the hand-off needs every year's pools)."""
    from taxjson.lib.pipeline import option_timing_flags
    country = _country(settings)
    flags = ["--country", country, "--tax-date", _tax_date(settings)]
    if country in ("us", "usa"):
        flags.append("--per-account-basis")
    return (flags + option_timing_flags(settings)
            + income_dating_flags(settings))


def _prior_record_path(root: Path, settings: Dict[str, Any],
                       override: Optional[str]) -> Path:
    if override:
        return Path(override).expanduser()
    configured = settings.get("prior_year_record")
    if configured:
        p = Path(str(configured)).expanduser()
        return p if p.is_absolute() else (root / p)
    return root / "filed" / f"{int(settings.get('year') or 0) - 1}.json"


def cmd_handoff(args: argparse.Namespace) -> None:
    """`taxjson handoff`: check this project against the previous year's
    close-year record — opening positions and cost at Dec 31, trades
    that settle across Dec 31, and sales reported in both years
    (lib/handoff). Exit 1 on any problem."""
    import json as _json
    from taxjson.lib import handoff as _handoff
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    settings = cfg["settings"]
    rp = _prior_record_path(root, settings, args.prior)
    if not rp.exists():
        sys.exit(f"taxjson handoff: no prior-year record at {rp}. Run "
                 f"`taxjson close-year` in the previous year's project, "
                 f"then set [settings] prior_year_record to its "
                 f"filed/<year>.json (or pass --prior).")
    record = _json.loads(rp.read_text(encoding="utf-8"))
    from taxjson.bin.taxjson_filed import lock_country_problem
    _cp = lock_country_problem(record, settings, str(rp))
    if _cp:
        # The other country's closing positions and cost are not this
        # project's opening (a different basis rule and wash rule):
        # "keep or amend" advice on them would be wrong (COMMANDS-08).
        sys.exit(f"taxjson handoff: {_cp}")
    if int(record.get("schema_version") or 1) < 2 \
            or "year_end" not in record:
        sys.exit(f"taxjson handoff: {rp} is a version-1 lock (totals "
                 f"only). Re-close that year with the current taxjson "
                 f"(`taxjson close-year --force`) to record positions.")
    ry = int(record["year"])
    if int(settings.get("year") or 0) != ry + 1:
        print(f"taxjson handoff: note: the record is for {ry}; this "
              f"project's year is {settings.get('year')}.",
              file=sys.stderr)
    if not (root / "work").is_dir():
        sys.exit("taxjson handoff: no work/ — run `taxjson run` first.")
    opening = _handoff.snapshot(root / "work", cfg, f"{ry}-12-31",
                                _filed_run_gains,
                                _handoff_gains_flags(settings),
                                root / "phantoms.json")
    rep = _handoff.check(root, cfg, record, opening)
    if getattr(args, "json", False):
        _json_out(dict(rep, record=str(rp)))
    else:
        for ln in _handoff.render(rep, str(rp)):
            print(ln)
    if rep["problems"]:
        raise SystemExit(1)


def _check_filed_years(root: Path, cache: Path,
                       settings: Dict[str, Any], *,
                       strict: bool) -> int:
    """Drift check for every filed/<year>.json. Returns the number of
    drifting years; prints per-year OK/DRIFT lines."""
    from taxjson.bin import taxjson_filed
    import json as _json
    snaps = taxjson_filed.list_snapshots(root)
    drifting = 0
    # Which snapshot accounts are crypto (they blend only with each
    # other, and only in a Canadian project with two or more);
    # equity accounts recompute via ONE blended combined run — exactly
    # how close-year's numbers were produced — or the check would
    # falsely drift every multi-account book.
    try:
        _acct_cfg = load_config(root).get("accounts", {})
    except SystemExit:
        _acct_cfg = {}
        if snaps:
            print("taxjson: warning: taxjson.toml unreadable — filed-"
                  "year check cannot tell crypto from equity accounts; "
                  "crypto books would be blended into the combined "
                  "equity recompute and may report false drift.",
                  file=sys.stderr)
    _taxable_cfg = {a for a, c in _acct_cfg.items()
                    if isinstance(c, dict) and c.get("type") == "taxable"}
    unreadable = 0
    mismatched = 0
    for year, path in snaps:
        # One lock at a time: an unreadable or hand-edited lock is
        # reported BY NAME and counted as a failure, and the other
        # locks are still checked. A KeyError/JSONDecodeError here used
        # to escape the loop, skip --strict's exit and every later lock
        # (R1-189).
        try:
            snap = _json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(snap, dict) or not isinstance(
                    snap.get("accounts", {}), dict) or not all(
                    isinstance(v, dict)
                    for v in snap.get("accounts", {}).values()):
                raise ValueError("not a close-year lock (no per-account "
                                 "table)")
            # A lock closed under the other country is never recomputed
            # under this one's law (partition COMMANDS-08).
            _cp = taxjson_filed.lock_country_problem(
                snap, settings, f"filed/{path.name}")
            if _cp:
                mismatched += 1
                print(f"  !! filed {year}: {_cp}", file=sys.stderr)
                continue
            _snap_accts = list(snap.get("accounts", {}))
            # A locked account that is no longer a configured taxable
            # account (renamed/removed) is NOT recomputed from its
            # orphan work/<label>_base.json — that blended stale books
            # into the check and said OK (S002-06).
            _gone = ({a for a in _snap_accts if a not in _taxable_cfg}
                     if _acct_cfg else set())
            _snap_accts = [a for a in _snap_accts if a not in _gone]
            # Taxable accounts the BOOKS have but the lock does not
            # (added or renamed after close-year) are recomputed too —
            # in the same blend — so diff_snapshot can report them
            # instead of saying OK.
            _snap_accts += sorted(
                a for a in _taxable_cfg
                if a not in _snap_accts
                and (cache / f"{a}_base.json").exists())
            _crypto = [a for a in _snap_accts
                       if (_acct_cfg.get(a) or {}).get("crypto")]
            _equity = [a for a in _snap_accts if a not in _crypto]
            _lock_timing = snap.get("option_timing")
            recomputed = taxjson_filed.recompute_accounts(
                cache, _equity, _crypto, year,
                taxjson_filed.lock_settings(snap, settings),
                snap.get("basis", ""), _filed_run_gains,
                option_timing=_lock_timing)
            lines = taxjson_filed.diff_snapshot(snap, recomputed,
                                                unconfigured=_gone)
        except SystemExit:
            raise
        except Exception as e:          # this lock only
            unreadable += 1
            print(f"  !! filed {year}: {path.name} could not be checked: "
                  f"{type(e).__name__}: {e} — fix or restore the lock "
                  f"(it is the record of the filed return)",
                  file=sys.stderr)
            continue
        if isinstance(_lock_timing, dict):
            from taxjson.lib.pipeline import option_timing_from_settings
            _cur = option_timing_from_settings(settings) or {}
            if _cur and (
                    _cur.get("option_premium_timing")
                    != _lock_timing.get("option_premium_timing")
                    or _cur.get("option_grant_since")
                    != _lock_timing.get("option_grant_since")):
                print(f"  note: filed {year} recomputed with the option "
                      f"timing its lock records "
                      f"({_lock_timing.get('option_premium_timing')}, "
                      f"since {_lock_timing.get('option_grant_since')}); "
                      f"this project uses "
                      f"{_cur.get('option_premium_timing')}, since "
                      f"{_cur.get('option_grant_since')} — set "
                      f"option_grant_timing_since to match or contracts "
                      f"written around {year} are taxed in the wrong "
                      f"year or twice")
        if lines:
            drifting += 1
            print(f"  !! filed {year} DRIFTED vs {path.name}:",
                  file=sys.stderr)
            for ln in lines:
                print(f"     {ln}", file=sys.stderr)
            print(f"     A code/data change moved an already-filed "
                  f"year. Review; then either amend the return or "
                  f"refresh the lock (`taxjson close-year --force` "
                  f"with [settings].year = {year}).", file=sys.stderr)
        else:
            print(f"  filed {year}: OK (matches {path.name}; "
                  f"{taxjson_filed.NOT_LOCKED})")
    if (unreadable or mismatched) and strict:
        sys.exit(f"taxjson run --strict: {unreadable + mismatched} "
                 f"filed-year lock(s) could not be checked"
                 + (f" ({mismatched} closed under another country)"
                    if mismatched else "")
                 + (f" and {drifting} drifted" if drifting else "")
                 + " — aborting.")
    if drifting and strict:
        sys.exit(f"taxjson run --strict: {drifting} filed year(s) "
                 f"drifted — aborting.")
    return drifting + unreadable + mismatched


def _fx_cash_after_run(root: Path, cache: Path,
                       reports_dir: Path) -> None:
    """End-of-run FX-cash report, ONLY when [settings] fx_cash_gains
    is true — the ledger is advisory and many filers have never
    reported s.39(1.1), so it stays opt-in and touches no other
    output."""
    from taxjson.bin import taxjson_fx_cash as FX
    if not _soft_settings(root).get("fx_cash_gains"):
        return
    print("==> fx gains on cash (fx_cash_gains = true)")
    try:
        ledger, verdict, base, year, country = _fx_cash_doc(root, cache)
    except SystemExit as e:
        print(f"  skipped: {e}", file=sys.stderr)
        return
    text = FX.render_report(ledger, base, year, country, verdict)
    rpt = reports_dir / "fx_cash.rpt"
    tmp = rpt.with_name(rpt.name + ".part")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(rpt)
    print(f"  net {ledger['net_gain']:,.2f} {base}, reportable "
          f"{verdict['reportable']:,.2f} {base} -> {rpt}")


def cmd_check_filed(args: argparse.Namespace) -> None:
    """`taxjson check-filed`: recompute every filed year from the
    current books and diff against the locks. Exit 1 on drift."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_config(root)["settings"]
    from taxjson.bin import taxjson_filed
    if not taxjson_filed.list_snapshots(root):
        print("no filed/<year>.json snapshots — `taxjson close-year` "
              "creates one after you file.")
        return
    print("==> filed-year drift check")
    # "OK" is only as good as the books it recomputes (S048-23).
    _state = _warn_run_state(root, load_config(root))
    if _check_filed_years(root, cache, settings, strict=False):
        raise SystemExit(1)
    if _state:
        print("  (the books above are not clean — see the warning; an OK "
              "here is not conclusive until `taxjson run` is clean)")


def cmd_reconcile_slips(args: argparse.Namespace) -> None:
    """`taxjson reconcile-slips SLIP.csv`: diff broker T5008/1099-B slips
    against the computed dispositions of all taxable accounts."""
    from taxjson.bin import taxjson_reconcile_slips

    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_config(root).get("settings", {})
    year = settings.get("year")

    _slips = (args.slip_csv if isinstance(args.slip_csv, list)
              else [args.slip_csv])
    gains_argv = _taxable_gains_argv(
        root, cache, exclude_crypto=True, prog="taxjson reconcile-slips",
        required=True)
    # Books built for another tax year made every slip row read
    # MISSING_FROM_COMPUTED "dropped CSV rows or a missing statement?"
    # — the wrong cause (S047-24, S049-06).
    _stale = _artifact_year_mismatch(
        {Path(g).name: Path(g) for g in gains_argv[1::2]}, year)
    if _stale:
        got = ", ".join(f"{a} ({y})" for a, y in sorted(_stale.items()))
        _die(f"[settings].year is {year} but the work/ books were built "
             f"for another tax year: {got}. Rebuild with `taxjson run` "
             f"before reconciling the {year} slips.")
    argv = list(_slips) + gains_argv
    tmap = root / "ticker.map"
    if tmap.exists():
        # Slips print the broker's symbols; the books carry the
        # ticker.map consolidations (KGC -> K, R1-19).
        argv += ["--ticker-map", str(tmap)]
    if year is not None:
        argv += ["--year", str(year)]
        # Same date convention stage_account feeds the gains engine:
        # IRS/1099-B scope by TRADE date, CRA/T5008 by SETTLEMENT; an
        # explicit tax_date config wins. Without this, a USA year-end
        # sale settling in January was on the 1099-B (and in
        # form-export) but missing from the computed side.
        tax_date = _tax_date(settings)
        argv += ["--date-basis", tax_date]
    argv += ["--country", _country(settings)]
    if args.tolerance is not None:
        # One token: '--tolerance -1' read the value as an option
        # (S036-00).
        argv.append(f"--tolerance={args.tolerance!r}")
    if args.json:
        argv.append("--json")
    raise SystemExit(taxjson_reconcile_slips.main(argv))


def _explain_wash_sales(root: Path, cache: Path,
                        account: Optional[str]) -> None:
    """`taxjson wash-sales --explain`: print the full ACB / superficial-loss
    calculation trace for each wash sale, via `taxjson-explain --wash-sales`
    (plain text — `taxjson-explain` is color-off by default). Resolves the
    account base file(s) and the country / tax-date / sheltered context from the
    project. Raises SystemExit with the tool's return code."""
    if account:
        base = cache / f"{account}_base.json"
        if not base.exists():
            sys.exit(f"taxjson wash-sales: no {base.name} in {cache} "
                     f"(run `taxjson run` first, or check the name).")
        _refuse_us_crypto_account(root, account, "taxjson wash-sales")
        bases = [base]
    else:
        names = _taxable_equity_account_names(root)
        if names is not None and not names:
            _no_wash_checkable("taxjson wash-sales")
        if names is not None:
            bases = [cache / f"{n}_base.json" for n in sorted(names)
                     if (cache / f"{n}_base.json").exists()]
        else:
            bases = [p for p in sorted(cache.glob("*_base.json"))
                     if not p.name.endswith("_raw_base.json")
                     and p.name != "sheltered_base.json"
                     and not p.name.startswith(".")]
        if not bases:
            sys.exit(f"taxjson wash-sales: no base files in {cache} "
                     f"(run `taxjson run` first).")

    settings = _soft_settings(root)
    common: List[str] = ["--wash-sales"]
    common += ["--country", _country(settings)]
    if settings.get("tax_date"):
        common += ["--tax-date", settings["tax_date"]]
    # The table lists the tax year's wash sales; the trace printed every
    # year's under it (R1-284).
    if isinstance(settings.get("year"), int):
        common += ["--year", str(settings["year"])]
    # Sheltered history makes the ±30-day affiliated-balance window accurate
    # (a registered-account repurchase can trigger/permanently-deny a loss).
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        common += ["--sheltered", str(sheltered_base)]
    # Same phantom openings as the pipeline, so traces match the books.
    phantoms = root / "phantoms.json"
    if phantoms.exists():
        common += ["--incomplete-history", str(phantoms)]
    common += option_timing_flags(settings)

    # Trace the computation the table comes from: the pipeline BLENDS
    # the taxable equity books (s.47 ACB / cross-account §1091), and
    # Canadian crypto books when there are two or more. One explain
    # per account's own book gave each its own pool — "no matching
    # gains" for a real denial, or a denial the books do not have
    # (S046-09).
    _acfg = _soft_config(root).get("accounts") or {}
    _crypto_blend = _country(settings) \
        not in ("us", "usa")
    _by_name = {p.name[:-len("_base.json")]: p for p in bases}
    if account and account in _acfg:
        _is_c = bool((_acfg.get(account) or {}).get("crypto"))
        for n in _taxable_equity_account_names(root) or []:
            if (n != account and bool((_acfg.get(n) or {}).get("crypto"))
                    == _is_c and (cache / f"{n}_base.json").exists()):
                _by_name[n] = cache / f"{n}_base.json"
    equity = [p for n, p in sorted(_by_name.items())
              if not (_acfg.get(n) or {}).get("crypto")]
    crypto = [p for n, p in sorted(_by_name.items())
              if (_acfg.get(n) or {}).get("crypto")]
    groups = ([equity] if equity else []) + (
        [crypto] if crypto and _crypto_blend
        else [[c] for c in crypto])
    import os as _os
    import tempfile as _tf
    from taxjson.lib.dispatch import run_cmd as _run_cmd
    rc = 0
    for grp in groups:
        target, tmp = grp[0], None
        if len(grp) > 1:
            fd, name = _tf.mkstemp(prefix="taxjson_explain_",
                                   suffix=".json")
            _os.close(fd)
            tmp = Path(name)
            res = _run_cmd(_cmd("taxjson-merge") + [str(b) for b in grp],
                           capture_output=True)
            if res.returncode != 0:
                tmp.unlink(missing_ok=True)
                sys.exit(f"taxjson wash-sales: could not merge the "
                         f"taxable base books: "
                         f"{(res.stderr or '').strip()[:300]}")
            tmp.write_text(res.stdout, encoding="utf-8")
            target = tmp
        try:
            # Each trace block self-identifies its account, so no
            # per-file header is needed.
            proc = _run_cmd(_cmd("taxjson-explain") + common
                            + [str(target)])
            rc = proc.returncode or rc
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    raise SystemExit(rc)


def _radar_config(root: Path, prog: str = "taxjson") -> Dict[str, Any]:
    """taxjson.toml for the radar family (wash-radar, watch, buy-check,
    sell-check): {} when the project has none — a bare work/ directory
    still runs — but a file that EXISTS and does not parse stops the
    command. Soft-reading it as {} fell back to globbing every
    *_base.json, sheltered books included, as TAXABLE: registered
    accounts' sales became "losses" with rescue advice to sell RRSP
    shares (2026-09 audit S048-13)."""
    cfg_path = root / "taxjson.toml"
    if cfg_path.exists() and tomllib is not None:
        try:
            tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            sys.exit(f"{prog}: taxjson.toml cannot be read ({e}) — fix "
                     f"it first; the radar will not guess which "
                     f"accounts are taxable.")
    return _soft_config(root)


def _taxable_equity_account_names(root: Path,
                                  prog: str = "taxjson"
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
    crypto_covered = _country((cfg.get("settings") or {})) \
        not in ("us", "usa")
    return [n for n, c in (cfg.get("accounts") or {}).items()
            if (c or {}).get("type") == "taxable"
            and (crypto_covered or not (c or {}).get("crypto"))]


def _refuse_us_crypto_account(root: Path, account: Optional[str],
                              prog: str) -> None:
    """A US project's crypto account is outside the wash-sale rule
    (§1091 does not reach digital assets; the pipeline runs it with
    --no-wash): the explain and radar entry points said a loss the
    return allows in full was disallowed, and advised a trade to
    'rescue' it (S046-10)."""
    if not account:
        return
    cfg = _radar_config(root, prog)
    if (_country((cfg.get("settings") or {})) in ("us", "usa")
            and ((cfg.get("accounts") or {}).get(account) or {})
            .get("crypto")):
        print(f"{prog}: {account} is a crypto account of a US project — "
              f"the wash-sale rule (§1091) does not apply to it, so "
              f"there is nothing to check.")
        raise SystemExit(0)


def _no_wash_checkable(prog: str) -> None:
    print(f"{prog}: no wash-checkable taxable account in taxjson.toml "
          f"(sheltered accounts are context only, and a US project's "
          f"crypto accounts are outside the wash-sale rule, §1091) — "
          f"nothing to check.")
    raise SystemExit(0)


def cmd_wash_radar(args: argparse.Namespace) -> None:
    """Convenience wrapper over `taxjson-wash-radar`: resolve the taxable equity
    account base file(s) and the combined sheltered base, then run the radar
    live (against `--date`, defaulting to today) to stdout. All taxable accounts
    go into ONE invocation so the report — and its Definitions legend — prints
    once, in the fixed advisory order. Recomputes cooling-down windows as of now
    (the per-account reports/wash_radar_<account>.rpt are written by `taxjson
    run`)."""
    root = Path(args.dir).resolve()
    cache = root / "work"

    if args.account:
        base = cache / f"{args.account}_base.json"
        if not base.exists():
            sys.exit(f"taxjson wash-radar: no {base.name} in {cache} "
                     f"(run `taxjson run` first, or check the name).")
        _acct_cfg = _radar_config(
            root, "taxjson wash-radar").get("accounts") or {}
        if (_acct_cfg.get(args.account) or {}).get("type") == "sheltered":
            sys.exit(f"taxjson wash-radar: {args.account} is a "
                     f"sheltered account — the radar advises on "
                     f"TAXABLE loss sales (sheltered books are its "
                     f"context, not its subject).")
        _refuse_us_crypto_account(root, args.account, "taxjson wash-radar")
        bases = [base]
    else:
        bases = _radar_taxable_bases(root, cache, "taxjson wash-radar")

    cmd = _cmd("taxjson-wash-radar") + ["--taxable", *[str(b) for b in bases]]
    cmd += _radar_engine_args(
        bases, root / "phantoms.json",
        _country(_radar_config(root, "taxjson wash-radar").get(
            "settings", {})))
    # Cross-account superficial-loss detection needs the pooled sheltered
    # history; pass it when the pipeline has built it.
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    elif _sheltered_expected(root):
        print("taxjson wash-radar: note: no sheltered_base.json in "
              "work/ — registered-account (permanent-denial) context "
              "disabled; run a full `taxjson run` to build it.",
              file=sys.stderr)
    if args.date:
        # Same shape+calendar validation as `list --date` — the
        # standalone fed the raw string straight into strptime, so a
        # typo'd date died with a traceback instead of a usage error.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            sys.exit("taxjson wash-radar: --date expects YYYY-MM-DD")
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            sys.exit(f"taxjson wash-radar: --date {args.date} is not a "
                     f"real calendar date")
        cmd += ["--date", args.date]
    if args.verbose:
        cmd += ["--verbose"]
    if args.all:
        cmd += ["--all"]
    if getattr(args, "json", False):
        cmd += ["--json"]
    _exec_tool(cmd)


def _radar_taxable_bases(root: Path, cache: Path,
                         prog: str, empty_ok: bool = False) -> List[Path]:
    """Taxable base files for a radar-style run. Prefer the config's
    wash-checkable taxable accounts (sheltered accounts are never
    radar'd; crypto is included only where the jurisdiction's wash
    rule covers it — see _taxable_equity_account_names). Fall back to
    globbing base files when there's no readable config."""
    names = _taxable_equity_account_names(root, prog)
    if names is not None and not names:
        if empty_ok:
            return []
        _no_wash_checkable(prog)
    if names is not None:
        bases = [cache / f"{n}_base.json" for n in sorted(names)
                 if (cache / f"{n}_base.json").exists()]
        # A configured taxable account without books used to vanish
        # from the checks in silence — a sibling's recent buy then read
        # as "SAFE — no tracked position" (S046-11).
        _skipped = _accounts_skipped_for_no_inputs(root)
        _missing = [n for n in sorted(names)
                    if not (cache / f"{n}_base.json").exists()
                    and n not in _skipped]
        if _missing and bases:
            print(f"{prog}: WARNING: no books for taxable account(s) "
                  f"{', '.join(_missing)} (work/<account>_base.json "
                  f"missing) — their trades are INVISIBLE to these "
                  f"window checks, so a SAFE/CLEAR verdict here can be "
                  f"wrong. Run `taxjson run` to build them.",
                  file=sys.stderr)
    else:
        bases = [p for p in sorted(cache.glob("*_base.json"))
                 if not p.name.endswith("_raw_base.json")
                 and p.name != "sheltered_base.json"
                 and not p.name.startswith(".")]
    if not bases:
        sys.exit(f"{prog}: no taxable base files in {cache} "
                 f"(run `taxjson run` first).")
    return bases


def _radar_engine_args(bases: List[Path],
                       phantoms: Optional[Path],
                       country: str) -> List[str]:
    """The radar's engine context, shared by every radar run (wash-radar,
    watch, buy-check, sell-check and the run's reports/wash_radar_*):
    the taxable accounts' gains files (wash-adjusted, s.47-blended — the
    engine decides which sales were losses), the project's
    phantoms.json (the same openings the gains pass applies) and its
    country (Canada's per-holder s.54 test vs the US s.1091 rules)."""
    from taxjson.lib.report_model import resolve_gains_files
    # --country is required by the radar (lib/country): never omitted.
    out: List[str] = ["--country", _normalize_country(str(country))]
    by_dir: Dict[Path, Dict[str, Path]] = {}
    for b in bases:
        b = Path(b)
        if not b.name.endswith("_base.json"):
            continue
        name = b.name[:-len("_base.json")]
        found = by_dir.setdefault(b.parent, resolve_gains_files(b.parent))
        g = found.get(name)
        if g is not None:
            out += ["--gains", str(g)]
    if phantoms is not None and Path(phantoms).exists():
        out += ["--incomplete-history", str(phantoms)]
    return out


def _watch_threshold(args: argparse.Namespace) -> float:
    """--threshold for the harvest dimension; an explicit 0 means "any
    move" (`or 100.0` turned it into 100, R1-242)."""
    v = getattr(args, "threshold", None)
    return 100.0 if v is None else float(v)


def cmd_watch(args: argparse.Namespace) -> None:
    """`taxjson watch`: report only what CHANGED since the last watch
    run — new/changed/cleared radar advisories, moved clear dates, and
    (with --harvest) the harvestable-now loss total. Quiet (no output,
    exit 0) when nothing changed, so a cron line mails only on news.
    State lives in work/.watch_state.json; the first run records a
    baseline. `--exit-code` exits 1 on changes for scripting (the
    default stays 0 so chains like `taxjson run watch` never fail on
    a mere report)."""
    import json as _json
    from datetime import date as _date
    from taxjson.bin import taxjson_watch as _watch
    from taxjson.lib.dispatch import run_cmd as _run
    root = Path(args.dir).resolve()
    cache = root / "work"
    bases = _radar_taxable_bases(root, cache, "taxjson watch")
    cmd = _cmd("taxjson-wash-radar") + [
        "--taxable", *[str(b) for b in bases], "--all", "--json"]
    cmd += _radar_engine_args(
        bases, root / "phantoms.json",
        _country(_radar_config(root, "taxjson watch").get(
            "settings", {})))
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    res = _run(cmd, capture_output=True)
    if res.returncode != 0:
        sys.exit(f"taxjson watch: radar failed: "
                 f"{(res.stderr or '').strip()[:400]}")
    try:
        radar_doc = _json.loads(res.stdout)
    except ValueError as e:
        sys.exit(f"taxjson watch: radar emitted unparseable JSON: {e}")
    cur_radar = _watch.flatten_radar(radar_doc)
    as_of = radar_doc.get("as_of_date") or _date.today().isoformat()

    harvest_now = None
    if (getattr(args, "threshold", None) not in (None, 100.0)
            and not getattr(args, "harvest", False)):
        print("taxjson watch: note: --threshold only applies with "
              "--harvest (ignored this run).", file=sys.stderr)
    if getattr(args, "harvest", False):
        hcmd = [sys.executable, "-m", "taxjson.bin.taxjson_run",
                "-C", str(root), "harvest", "--json"]
        if getattr(args, "no_ibkr", False):
            hcmd.append("--no-ibkr")
        hres = _run(hcmd, capture_output=True)
        if hres.returncode != 0:
            print(f"taxjson watch: warning: harvest failed — the "
                  f"harvest dimension is skipped this run: "
                  f"{(hres.stderr or '').strip()[:200]}",
                  file=sys.stderr)
        else:
            try:
                hdoc = _json.loads(hres.stdout)
                harvest_now = float(
                    ((hdoc.get("totals") or {}).get("harvestable")
                     or {}).get("now") or 0.0)
            except (ValueError, TypeError) as e:
                print(f"taxjson watch: warning: harvest JSON "
                      f"unreadable ({e}) — skipped this run.",
                      file=sys.stderr)

    if getattr(args, "state", None):
        state_path = Path(args.state)
        if not state_path.is_absolute():
            state_path = root / state_path
        # A directory, a path under a file or an unwritable place gave
        # an 11-line traceback (S046-12) — one line, like
        # --gen-phantoms.
        if state_path.is_dir():
            _die(f"--state {args.state} is a directory — pass a FILE "
                 f"path, e.g. {state_path / 'watch_state.json'}")
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _die(f"cannot create the folder for --state {args.state}: "
                 f"{e} — pass a FILE path in a writable folder.")
    else:
        state_path = cache / ".watch_state.json"

    def _save(harvest_value) -> None:
        try:
            _watch.save_state(state_path, cur_radar, harvest_value, as_of)
        except OSError as e:
            _die(f"cannot write the watch state {state_path}: {e} — "
                 f"pass a writable FILE path with --state.")

    state = _watch.load_state(state_path)
    if state is None:
        _save(harvest_now)
        actionable = sum(1 for r in cur_radar.values()
                         if r.get("category") in _watch._ACTIONABLE)
        if getattr(args, "json", False):
            _json_out({"baseline": True, "changes": [],
                       "tracked": len(cur_radar),
                       "actionable": actionable, "as_of": as_of})
        else:
            print(f"watch: baseline recorded — {len(cur_radar)} "
                  f"ticker(s) tracked, {actionable} actionable "
                  f"advisories. Future runs report only changes.")
        return

    changes = _watch.diff_radar(state.get("radar") or {}, cur_radar)
    if harvest_now is not None:
        hch = _watch.diff_harvest(state.get("harvest_now"), harvest_now,
                                  _watch_threshold(args))
        if hch:
            changes.append(hch)
        saved_harvest = harvest_now
    else:
        # This run didn't compute a harvest total (no --harvest, or a
        # transient harvest failure) — CARRY the previous baseline
        # forward. Dropping it meant alternating `watch` /
        # `watch --harvest` cron lines re-baselined every time and a
        # harvest change was never reported.
        saved_harvest = state.get("harvest_now")
    _save(saved_harvest)

    actionable = sum(1 for r in cur_radar.values()
                     if r.get("category") in _watch._ACTIONABLE)
    if getattr(args, "json", False):
        _json_out({"baseline": False, "changes": changes,
                   "tracked": len(cur_radar),
                   "actionable": actionable,
                   "as_of": as_of, "since": state.get("as_of")})
    elif changes:
        print(_watch.render_report(changes, as_of,
                                   since=state.get("as_of")))
    if changes and getattr(args, "exit_code", False):
        raise SystemExit(1)


def _questrade_token_file(cache: Path) -> Path:
    """Where the Questrade refresh token lives, read AND written:
    $QUESTRADE_TOKEN_FILE > ~/.questrade_token — the shared, tool-neutral
    file portoml-ai's Questrade tools default to as well, serving every
    taxjson project on the machine.

    Questrade issues ONE rotating chain per API app: every exchange kills
    the previous token, so the token belongs to the APP, not to a single
    tool or project. One shared file means taxjson and portoml-ai can
    never rotate each other's copy dead. (`cache` is unused since the
    per-project work/.questrade_refresh_token fallback was removed
    pre-1.0; the parameter stays so call sites read uniformly.)"""
    import os as _os
    del cache
    env = _os.environ.get("QUESTRADE_TOKEN_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path("~/.questrade_token").expanduser()


def _questrade_token_write(tok_cache: Path, token: str) -> None:
    """Persist the ROTATED token atomically (tmp + rename), mode 600 — a
    later failure must not lose it, since the old one is already dead."""
    import os as _os
    tok_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = tok_cache.with_name(tok_cache.name + ".part")
    # 0600 from the first byte, and a FRESH file: a stale .part (or a
    # symlink planted there) is unlinked, then O_EXCL|O_NOFOLLOW refuses
    # to follow or reuse anything that reappears before the open.
    tmp.unlink(missing_ok=True)
    fd = _os.open(str(tmp), _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL
                  | getattr(_os, "O_NOFOLLOW", 0), 0o600)
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(tok_cache)
    try:
        _os.chmod(tok_cache, 0o600)          # belt: pre-existing target
    except OSError:
        pass


def _qt_auth_hint(token: str, tok_cache: Path, *,
                  explicit: bool = False) -> str:
    """Recovery advice for a failed Questrade refresh. The cached
    rotating token wins over $QUESTRADE_REFRESH_TOKEN, so a fresh token
    exported over a dead cache failed the same way with no word about
    the env var being skipped (R1-353). Never prints a token."""
    import os as _os
    env = _os.environ.get("QUESTRADE_REFRESH_TOKEN", "").strip()
    cached = (tok_cache.read_text(encoding="utf-8").strip()
              if tok_cache.exists() else "")
    if not explicit and cached and token == cached:
        msg = (f" — the token used is the cached chain in {tok_cache}"
               + (", NOT $QUESTRADE_REFRESH_TOKEN (the cache wins)"
                  if env and env != cached else "")
               + ". If that chain is dead, start a new one: "
                 "`taxjson fetch --refresh-token <new token>` (or "
                 f"delete {tok_cache.name} and set "
                 "$QUESTRADE_REFRESH_TOKEN).")
        return msg
    return (" — generate a new refresh token in Questrade's API centre "
            "and pass it once with `taxjson fetch --refresh-token`.")


def _qt_live_holdings(root: Path, cache: Path, cfg: Dict[str, Any],
                      wanted: List[str], http, say) -> Dict[str, Path]:
    """Fetch live Questrade positions for each fetch-enabled account in
    `wanted` and write work/<account>_live_holdings.toml (the
    portoml-style file `taxjson sanity` reads). Returns
    {account: toml_path}. Shares the rotated-token session flow with
    the activity fetch."""
    import os as _os
    from datetime import datetime as _dt
    from taxjson.bin import taxjson_fetch as F
    fetch_cfg = _fetch_sources(cfg)
    out: Dict[str, Path] = {}
    qt_session = None
    for a in wanted:
        fc = fetch_cfg.get(a) or {}
        if fc.get("source") != "questrade":
            continue
        number = fc.get("number") or ""
        if not number:
            continue
        if qt_session is None:
            tok_cache = _questrade_token_file(cache)
            token = ((tok_cache.read_text(encoding="utf-8").strip()
                      if tok_cache.exists() else "")
                     or _os.environ.get("QUESTRADE_REFRESH_TOKEN",
                                        "").strip())
            if not token:
                _die("no Questrade refresh token — run `taxjson "
                     "fetch --refresh-token ...` once first.")
            try:
                qt_session = F.qt_refresh(token, http)
            except RuntimeError as e:
                _die(f"Questrade auth failed: {e}"
                     + _qt_auth_hint(token, tok_cache))
            _questrade_token_write(tok_cache,
                                   qt_session["refresh_token"])
        try:
            positions = F.qt_positions(qt_session, number, http)
        except RuntimeError as e:
            _die(f"{a}: {e}")
        # The account's BOOK symbols decide a live option's suffix when
        # the books hold that exact contract, and the books' .TO
        # OPTIONS teach Montreal roots (a cash-secured put has no
        # equity leg in the live payload — 2026-09 audit). A .TO
        # EQUITY in the books no longer does: a CDR (AMZN.TO) made the
        # account's US AMZN option .TO live vs .US in the books, a
        # phantom verify mismatch every run (S031-12).
        _book_syms = set()
        try:
            import json as _json
            _bp = cache / f"{a}_base.json"
            if _bp.exists():
                for _r in (_json.loads(_bp.read_text(encoding="utf-8"))
                           .get("transactions") or []):
                    _sym = str(_r.get("symbol") or "").upper()
                    if _sym:
                        _book_syms.add(_sym)
        except Exception:
            pass
        text = F.positions_to_holdings_toml(
            positions, a, number,
            _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            book_symbols=_book_syms)
        toml_path = cache / f"{a}_live_holdings.toml"
        F.write_private(toml_path, text)
        n = sum(1 for pz in positions if pz.get("openQuantity"))
        say(f"  {a}: {n} live position(s) -> {toml_path.name}")
        out[a] = toml_path
    return out


def _merge_csv_text(existing: str, new: str) -> Tuple[str, int]:
    """Union-merge two same-header CSVs on LOGICAL rows (csv module,
    not text lines — a quoted field may contain newlines, and a
    line-based merge interleaved the fragments of multi-line rows into
    silently-wrong data). Duplicates keep the MAX count seen per side:
    byte-identical rows can be physically distinct split fills (the
    parser's disambiguate_split_fills exists for exactly this), so a
    plain set-union would delete real trades on an overlap re-fetch.
    Returns (merged_text, new_row_count). Refuses on a header
    mismatch — a format change must not silently corrupt the file."""
    import csv as _csv
    import io as _io
    from collections import Counter

    def _rows(text):
        return [tuple(r) for r in _csv.reader(_io.StringIO(text))
                if any(f.strip() for f in r)]
    ex_rows, new_rows = _rows(existing), _rows(new)
    if not new_rows:
        return existing, 0
    if not ex_rows:
        return new, max(0, len(new_rows) - 1)
    if ex_rows[0] != new_rows[0]:
        raise ValueError("header mismatch between the existing fetch "
                         "file and the new download")
    ex_c, new_c = Counter(ex_rows[1:]), Counter(new_rows[1:])
    merged_c = ex_c | new_c                    # per-row max count
    added = sum((new_c - ex_c).values())
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(ex_rows[0])
    for row in sorted(merged_c):
        for _ in range(merged_c[row]):
            w.writerow(row)
    return buf.getvalue(), added


def _fetch_sources(cfg: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """{account: {source, number, query_id}} from the `brokerage` +
    `account`/`query_id` keys under [accounts.<name>]."""
    out: Dict[str, Dict[str, str]] = {}
    for a, ac in (cfg.get("accounts") or {}).items():
        b = str((ac or {}).get("brokerage") or "").strip()
        if b:
            out[a] = {"source": b,
                      "number": str((ac or {}).get("account")
                                    or "").strip(),
                      "query_id": str((ac or {}).get("query_id")
                                      or "").strip()}
    return out


def _qt_restatement_suspects(existing_csv: str,
                             new_csv: str) -> List[str]:
    """Rows in the EXISTING fetch file that look like stale copies of a
    broker-restated activity: same (date, symbol, action-ish prefix) as
    a fetched row but different content. The union-merge deliberately
    keeps both (it cannot tell a restatement from a split fill), so a
    corrected dividend/commission would double-count silently — this
    names the suspects so the user can delete the stale copy."""
    import csv as _csv
    import io as _io

    def _rows(text):
        try:
            rows = list(_csv.reader(_io.StringIO(text)))
        except Exception:
            return [], []
        return (rows[0] if rows else []), [r for r in rows[1:] if r]

    hdr, ex_rows = _rows(existing_csv)
    _hdr2, new_rows = _rows(new_csv)
    if not ex_rows or not new_rows:
        return []

    def _key(r):
        # (trade date, symbol, activity type) — colidx by Questrade
        # header names, falling back to positions 0/3/12.
        def _col(name, default):
            try:
                return r[hdr.index(name)] if name in hdr else r[default]
            except (ValueError, IndexError):
                return ""
        return (_col("Transaction Date", 0)[:10],
                _col("Symbol", 3).strip().upper(),
                _col("Activity Type", 12).strip())

    new_by_key: Dict[tuple, set] = {}
    for r in new_rows:
        new_by_key.setdefault(_key(r), set()).add(tuple(r))
    out: List[str] = []
    for r in ex_rows:
        k = _key(r)
        if not k[1]:
            continue
        peers = new_by_key.get(k)
        if peers and tuple(r) not in peers:
            out.append(f"{k[0]} {k[1]} ({k[2] or 'row'})")
    # De-dup while keeping order.
    seen: set = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def _qt_window_overlap(acct_dir: Path, out: Path,
                       start_iso: str,
                       end_iso: str) -> List[Tuple[Path, int]]:
    """Sibling Questrade CSVs with rows dated inside the fetched
    window. The two sources round price/gross differently, so their
    copies of the same trade hash to DIFFERENT ids and never dedup —
    coexisting coverage double-counts the books."""
    hits: List[Tuple[Path, int]] = []
    import csv as _csv
    # input_files, not glob("*.csv"): `run` reads QT_MANUAL.CSV too, so
    # a case-sensitive glob let an overlapping upper-case export double
    # the books with no warning (S046-14).
    for sib in input_files(acct_dir, ".csv"):
        if sib == out:
            continue
        if re.fullmatch(r"questrade_\d{4}\.csv", sib.name):
            # A prior tax year's OWN fetch file: API-written rows are
            # formatted identically, hash to the same ids, and DO
            # dedup — warning about it (every fetch, after a year
            # rollover) was a false alarm whose advice would trim an
            # API file needlessly.
            continue
        try:
            if detect_broker(sib) != "questrade":
                continue
            with sib.open(encoding="utf-8", errors="replace") as f:
                rows = list(_csv.reader(f))
        except Exception:
            continue
        col = _qt_date_col(rows[0] if rows else [])
        n = sum(1 for r in rows[1:] if len(r) > col and _row_date_in_window(
            r[col], start_iso, end_iso))
        if n:
            hits.append((sib, n))
    return hits


def _qt_date_col(header: List[str]) -> int:
    """Index of Questrade's "Transaction Date" column, by header name.
    The window is a TRADE-date window (the API's own); reading r[0]
    trimmed by Settlement Date when a manual export had that column
    first, deleting a trade the fetched file does not hold (R1-74)."""
    for i, h in enumerate(header):
        if h.strip().lstrip("\ufeff").lower() == "transaction date":
            return i
    return 0


def _row_date_in_window(first_field: str, start_iso: str,
                        end_iso: str) -> bool:
    """True only for rows whose first field carries a REAL date INSIDE
    the fetched window (both ends) — a lexicographic compare counted
    (and trimmed!) disclaimer/stray-header rows, and an open end
    counted (and --trim-overlap DELETED!) sibling rows dated months
    AFTER the window, i.e. real trades the fetched file does not own
    (2026-09 audit)."""
    try:
        datetime.strptime(first_field[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return start_iso <= first_field[:10] <= end_iso


def _qt_trim_file(path: Path, start_iso: str, end_iso: str) -> int:
    """Rewrite a Questrade CSV keeping only rows dated OUTSIDE the
    fetch window (the fetched file owns the window — and nothing
    else). The original is kept as <name>.bak. Returns the number of
    rows removed; unparseable rows are kept (safe side)."""
    import csv as _csv
    import io as _io
    from taxjson.bin import taxjson_fetch as F
    with path.open(encoding="utf-8", errors="replace") as f:
        rows = list(_csv.reader(f))
    for i, r in enumerate(rows, 1):
        if any("\n" in c or "\r" in c for c in r):
            # An unbalanced quote swallows the following lines into one
            # record; judged by its first date, the swallowed
            # out-of-window trades were deleted with it and the count
            # said 1 (S046-16). Never rewrite such a file.
            raise ValueError(f"{path.name}: record {i} spans several "
                             f"lines (an unbalanced quote?) — not "
                             f"trimmed; fix the file by hand")
    col = _qt_date_col(rows[0] if rows else [])
    keep = [rows[0]] + [r for r in rows[1:]
                        if not (len(r) > col and _row_date_in_window(
                            r[col], start_iso, end_iso))]
    removed = len(rows) - len(keep)
    if removed:
        # Never clobber an existing backup: a second --trim-overlap
        # run would replace the FULL original with the already-trimmed
        # copy, silently destroying the only copy of the trimmed rows.
        bak = path.with_name(path.name + ".bak")
        n = 2
        while bak.exists():
            bak = path.with_name(f"{path.name}.bak{n}")
            n += 1
        # Copy, then write the trimmed file atomically and private
        # (0600) like every fetched file — replace-then-write_text left
        # a 0664 file, and no file at all if the write failed (R1-74).
        shutil.copy2(path, bak)
        buf = _io.StringIO()
        _csv.writer(buf, lineterminator="\n").writerows(keep)
        F.write_private(path, buf.getvalue())
    return removed


_FLEX_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-?(0[1-9]|1[0-2])-?"
                           r"(0[1-9]|[12]\d|3[01])(?!\d)")


def _flex_dates(text: str) -> List[str]:
    """Sorted ISO dates found on an IB statement's data rows (the
    Statement section — generation time, period — is skipped)."""
    out = set()
    for line in text.splitlines():
        if line.lstrip('"').startswith("Statement"):
            continue
        for m in _FLEX_DATE_RE.finditer(line):
            out.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return sorted(out)


def _flex_lost_dates(existing: str, new: str, year: Any) -> List[str]:
    """Dates of `year` the existing ib_flex.csv covers but the new
    download's date range does not: overwriting would delete those
    rows. A Flex query set to 'Year to date' re-fetched in January
    replaced a whole year of activity at exit 0 (S007-00)."""
    if not year or not existing:
        return []
    old = [d for d in _flex_dates(existing) if d[:4] == str(year)]
    got = _flex_dates(new)
    if not got:
        return old
    return [d for d in old if not got[0] <= d <= got[-1]]


def cmd_fetch(args: argparse.Namespace) -> None:
    """`taxjson fetch [ACCOUNT ...]`: download broker activity straight
    into inputs/ — Questrade REST API or IBKR Flex Web Service,
    configured on the account itself (`brokerage` + `account`/
    `query_id` under [accounts.<name>]). Writes files the existing
    parsers already read (questrade_<year>.csv / ib_flex.csv);
    hand-exported CSVs keep working side by side. Run `taxjson run`
    afterwards (or chain: `taxjson fetch run`)."""
    from taxjson.bin import taxjson_fetch as F
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    fetch_cfg = _fetch_sources(cfg)
    if not fetch_cfg:
        sys.exit("taxjson fetch: no account declares a fetch source. "
                 "Add to taxjson.toml:\n"
                 "  [accounts.margin]\n"
                 "  type = \"taxable\"\n"
                 "  brokerage = \"questrade\"\n"
                 "  account = \"12345678\"\n"
                 "Credentials: $QUESTRADE_REFRESH_TOKEN (first run) / "
                 "$IBKR_FLEX_TOKEN.")
    accounts_cfg = cfg.get("accounts", {})
    wanted = list(args.account) if args.account else sorted(fetch_cfg)
    for a in wanted:
        if a not in fetch_cfg:
            sys.exit(f"taxjson fetch: [accounts.{a}] declares no "
                     f"`brokerage`."
                     if a in accounts_cfg else
                     f"taxjson fetch: no [accounts.{a}] in "
                     f"taxjson.toml.")
    json_mode = bool(getattr(args, "json", False))
    results: Dict[str, Any] = {}

    def say(msg: str) -> None:
        # Progress lines move to stderr under --json so stdout stays
        # a single machine-readable document.
        print(msg, file=sys.stderr if json_mode else sys.stdout)

    if getattr(args, "days", None) is not None and args.days <= 0:
        sys.exit(f"taxjson fetch: --days must be positive, got "
                 f"{args.days}")
    if getattr(args, "year", None) is not None:
        if getattr(args, "from_date", None) or getattr(args, "days",
                                                       None):
            sys.exit("taxjson fetch: --year picks the whole past "
                     "year's window and file — combine it with "
                     "--from/--days and the file name would lie about "
                     "its contents. Use one or the other.")
        from datetime import date as _d
        if not 2000 <= args.year <= _d.today().year:
            sys.exit(f"taxjson fetch: --year {args.year} is outside "
                     f"2000..{_d.today().year}.")
    if getattr(args, "from_date", None):
        from datetime import date as _date_cls
        try:
            # Same parser qt_window uses — strptime accepted unpadded
            # dates that fromisoformat then crashed on.
            _f = _date_cls.fromisoformat(args.from_date)
        except ValueError:
            sys.exit(f"taxjson fetch: --from {args.from_date!r} is not "
                     f"a valid YYYY-MM-DD date")
        if _f > _date_cls.today():
            sys.exit(f"taxjson fetch: --from {args.from_date} is in "
                     f"the future — nothing to fetch.")
    http = F.default_http_get
    qt_session = None
    import os as _os
    for a in wanted:
        fc = fetch_cfg[a]
        source = fc["source"]
        acct_dir = root / "inputs" / a
        if source == "questrade":
            number = fc["number"]
            if not number:
                sys.exit(f"taxjson fetch: [accounts.{a}] needs "
                         f"`account` (the Questrade account number).")
            if qt_session is None:
                tok_cache = _questrade_token_file(cache)
                token = (getattr(args, "refresh_token", None)
                         or (tok_cache.read_text(encoding="utf-8")
                             .strip() if tok_cache.exists() else "")
                         or _os.environ.get("QUESTRADE_REFRESH_TOKEN",
                                            "").strip())
                if not token:
                    sys.exit("taxjson fetch: no Questrade refresh "
                             "token — pass --refresh-token once (or "
                             "set $QUESTRADE_REFRESH_TOKEN); the "
                             "rotating chain then lives in "
                             f"{tok_cache}.")
                try:
                    qt_session = F.qt_refresh(token, http)
                except RuntimeError as e:
                    sys.exit(f"taxjson fetch: Questrade auth failed: "
                             f"{e}" + _qt_auth_hint(
                                 token, tok_cache,
                                 explicit=bool(getattr(
                                     args, "refresh_token", None))))
                # Persist the ROTATED token immediately — a later
                # failure must not lose it (the old one is now dead).
                _questrade_token_write(tok_cache,
                                       qt_session["refresh_token"])
            _fetch_year = (getattr(args, "year", None)
                           or cfg.get("settings", {}).get("year"))
            try:
                start, end = F.qt_window(getattr(args, "days", None),
                                         getattr(args, "from_date", None),
                                         year=_fetch_year)
            except ValueError as e:
                sys.exit(f"taxjson fetch: {e}")
            if _fetch_year and (getattr(args, "days", None)
                                or getattr(args, "from_date", None)):
                from datetime import date as _dd
                _y = int(_fetch_year)
                if (start < _dd(_y - 1, 12, 1)
                        or end > _dd(_y + 1, 1, 31)):
                    # R1-354: the file is named for the project year
                    # whatever the window; the parser dates each row
                    # itself, so this is only a label — but say so.
                    say(f"  note: the window {start} -> {end} reaches "
                        f"outside tax year {_y}'s (Dec 1 {_y - 1} .. Jan "
                        f"31 {_y + 1}); those rows go into "
                        f"questrade_{_y}.csv too (each row is still "
                        f"dated by its own trade/settle date).")
            say(f"fetch {a}: questrade #{F.mask_account_number(number)} "
                f"{start} -> {end}")
            try:
                acts = F.qt_activities(qt_session, number, start, end,
                                       http)
            except RuntimeError as e:
                sys.exit(f"taxjson fetch: {a}: {e}")
            new_csv = F.qt_to_csv(acts, number)
            by_type = F.activity_type_counts(acts)
            if by_type:
                say("  types: " + ", ".join(
                    f"{t} {n}" for t, n in by_type.items()))
            out = acct_dir / (f"questrade_{_fetch_year}.csv"
                              if _fetch_year else "questrade_api.csv")
            try:
                existing = (out.read_text(encoding="utf-8")
                            if out.exists() else "")
                merged, added = _merge_csv_text(existing, new_csv)
            except ValueError as e:
                sys.exit(f"taxjson fetch: {a}: {e} — move the old "
                         f"{out.name} aside and re-fetch.")
            overlap_now = _qt_window_overlap(acct_dir, out,
                                             start.isoformat(),
                                             end.isoformat())
            results[a] = {
                "source": "questrade", "file": out.name,
                "window": [start.isoformat(), end.isoformat()],
                "downloaded": len(acts), "added": added,
                "by_type": by_type,
                "overlaps": [{"file": sib.name, "rows": n}
                             for sib, n in overlap_now],
            }
            if getattr(args, "dry_run", False):
                say(f"  would add {added} row(s) to {out.name} "
                    f"({len(acts)} activities downloaded)")
                for sib, n in overlap_now:
                    say(f"  note: {sib.name} has {n} row(s) inside "
                        f"the window (see --trim-overlap)")
                continue
            F.write_private(out, merged)
            say(f"  {out.name}: +{added} new row(s) "
                f"({len(acts)} downloaded)")
            _restated = _qt_restatement_suspects(existing, new_csv)
            for _line in _restated[:5]:
                say(f"  note: possible broker RESTATEMENT — an "
                    f"existing row matches a fetched row on "
                    f"(date, symbol, action) but differs elsewhere; "
                    f"if Questrade corrected it, delete the stale "
                    f"copy: {_line}")
            if len(_restated) > 5:
                say(f"  note: (+{len(_restated) - 5} more possible "
                    f"restatements)")
            overlap = _qt_window_overlap(acct_dir, out,
                                         start.isoformat(),
                                         end.isoformat())
            if overlap and getattr(args, "trim_overlap", False):
                results[a]["trimmed"] = []
                for sib, n in overlap:
                    try:
                        cut = _qt_trim_file(sib, start.isoformat(),
                                            end.isoformat())
                    except ValueError as e:
                        print(f"taxjson fetch: WARNING: {a}/{e}",
                              file=sys.stderr)
                        results[a]["trimmed"].append(
                            {"file": sib.name, "rows": 0,
                             "refused": str(e)})
                        continue
                    results[a]["trimmed"].append(
                        {"file": sib.name, "rows": cut})
                    say(f"  {sib.name}: trimmed {cut} row(s) inside "
                        f"the fetched window (original kept as "
                        f"{sib.name}.bak)")
            elif overlap:
                for sib, n in overlap:
                    print(f"taxjson fetch: WARNING: {a}/{sib.name} has "
                          f"{n} row(s) dated on/after {start} — the "
                          f"same trades exported manually round "
                          f"price/gross differently than the API, so "
                          f"they will NOT dedup against {out.name} and "
                          f"the books will double-count. Re-run with "
                          f"--trim-overlap (keeps a .bak), or remove "
                          f"the overlapping rows yourself.",
                          file=sys.stderr)
        elif source == "ibkr_flex":
            query_id = fc["query_id"]
            if not query_id:
                sys.exit(f"taxjson fetch: [accounts.{a}] needs "
                         f"`query_id` (the Flex query id).")
            token = (getattr(args, "flex_token", None)
                     or _os.environ.get("IBKR_FLEX_TOKEN", "").strip())
            if not token:
                sys.exit("taxjson fetch: no IBKR Flex token — set "
                         "$IBKR_FLEX_TOKEN (or pass --flex-token).")
            say(f"fetch {a}: ibkr flex query {query_id}")
            try:
                raw = F.flex_fetch(token, query_id, http)
            except RuntimeError as e:
                sys.exit(f"taxjson fetch: {a}: {e}")
            text = raw.decode("utf-8", "replace")
            out = acct_dir / "ib_flex.csv"
            _nstmt = F.flex_statement_count(text)
            if _nstmt > 1:
                sys.exit(f"taxjson fetch: {a}: the Flex download "
                         f"contains {_nstmt} account statements — a "
                         f"multi-account query would blend books that "
                         f"must stay separate. Scope the Flex query "
                         f"to ONE account (one query per taxjson "
                         f"account; the token is shared).")
            if not F.looks_like_ib_statement(text):
                bad = out.with_suffix(".csv.unrecognized")
                saved = ""
                if not getattr(args, "dry_run", False):
                    F.write_private(bad, text)
                    saved = f" — saved to {bad.name}"
                sys.exit(f"taxjson fetch: {a}: the Flex download is "
                         f"not in the section,Header/Data CSV shape "
                         f"the IB parser reads{saved}. "
                         f"In the Flex query settings choose format "
                         f"CSV and enable 'include section code and "
                         f"line descriptor', then re-fetch.")
            results[a] = {"source": "ibkr_flex", "file": out.name,
                          "lines": len(text.splitlines())}
            _pyear = cfg.get("settings", {}).get("year")
            _existing = (out.read_text(encoding="utf-8", errors="replace")
                         if out.exists() else "")
            _lost = _flex_lost_dates(_existing, text, _pyear)
            _got = _flex_dates(text)
            _span = f"{_got[0]}..{_got[-1]}" if _got else "no dated rows"
            if _lost:
                _new = out.with_name(out.name + ".new")
                if not getattr(args, "dry_run", False):
                    F.write_private(_new, text)
                sys.exit(f"taxjson fetch: {a}: the Flex download covers "
                         f"{_span}, but {out.name} holds {len(_lost)} "
                         f"{_pyear} activity date(s) outside it "
                         f"({_lost[0]}..{_lost[-1]}) — replacing it "
                         f"would delete that activity from the books. "
                         + ("" if getattr(args, "dry_run", False) else
                            f"The download was saved as {_new.name} "
                            f"(not read by `taxjson run`). ")
                         + f"Set the Flex query's period to cover "
                         f"{_pyear}, or move {out.name} aside to accept "
                         f"the new file.")
            if getattr(args, "dry_run", False):
                say(f"  would write {out.name} "
                    f"({len(text.splitlines())} lines, {_span})")
                continue
            if _existing and _existing != text:
                # Never lose the previous statement: numbered backups,
                # like --trim-overlap's (not read by `taxjson run`).
                bak = out.with_name(out.name + ".bak")
                n = 2
                while bak.exists():
                    bak = out.with_name(f"{out.name}.bak{n}")
                    n += 1
                F.write_private(bak, _existing)
            # Atomic like the Questrade path: a crash mid-write must
            # not leave a truncated statement for the next run.
            F.write_private(out, text)
            say(f"  {out.name}: {len(text.splitlines())} lines, {_span} "
                f"(overwritten — a Flex query re-covers its whole "
                f"configured period"
                + (f"; previous copy kept as {bak.name}"
                   if _existing and _existing != text else "") + ")")
            if (_pyear and _got and int(_pyear) < date_cls.today().year
                    and (_got[0] > f"{_pyear}-01-10"
                         or _got[-1] < f"{_pyear}-12-20")):
                print(f"taxjson fetch: WARNING: {a}: the Flex download "
                      f"covers {_span}, not the whole tax year "
                      f"{_pyear} — set the query's period to the full "
                      f"year (Jan 1 to Dec 31, plus January for "
                      f"year-end settlements).", file=sys.stderr)
        else:
            sys.exit(f"taxjson fetch: [accounts.{a}] brokerage must be "
                     f"'questrade' or 'ibkr_flex', got {source!r}.")
    if getattr(args, "positions", False) \
            and not getattr(args, "dry_run", False):
        live = _qt_live_holdings(root, cache, cfg, wanted, http, say)
        for a, pth in live.items():
            results.setdefault(a, {})["live_holdings"] = pth.name
        if live and not json_mode:
            files_str = " ".join(str(pv) for pv in live.values())
            print(f"cross-check after rebuilding: taxjson run sanity "
                  f"{' '.join(live)} {files_str}")
    if json_mode:
        _json_out({"accounts": results,
                   "dry_run": bool(getattr(args, "dry_run", False))})
    elif not getattr(args, "dry_run", False):
        print("fetch complete — run `taxjson run` to rebuild "
              "(or chain: `taxjson fetch run`).")


def _fx_cash_doc(root: Path, cache: Path):
    """(doc, verdict, base, year, country) for the FX-cash report —
    shared by the fx-cash command and the end-of-run hook."""
    import json as _json
    from taxjson.bin import taxjson_fx_cash as FX
    from taxjson.lib.price_chain import load_fx_history
    cfg = _soft_config(root)
    settings = cfg.get("settings", {}) or {}
    base = str(_base(settings)).upper()
    year = settings.get("year")
    if not year:
        sys.exit("taxjson fx-cash: needs [settings] year in "
                 "taxjson.toml.")
    country = _country(settings)
    txs: List[Dict[str, Any]] = []
    found = False
    for name, acfg in sorted((cfg.get("accounts") or {}).items()):
        if (acfg or {}).get("type") != "taxable":
            continue                    # s.39 reaches the person's
            # taxable holdings; registered accounts are exempt.
        f = _native_tx_file(cache, name)
        if f is None:
            if _has_inputs(root, name):
                # A partial ledger flipped the line-15300 figure's sign
                # with rc 0 (S005-04, S046-19).
                sys.exit(f"taxjson fx-cash: no native transaction file "
                         f"for taxable account {name!r} — run `taxjson "
                         f"run` first; a ledger without it would be "
                         f"partial.")
            continue
        try:
            doc = _read_work_doc(f)
        except (OSError, ValueError) as e:
            sys.exit(f"taxjson fx-cash: could not read {f}: {e} — "
                     f"re-run `taxjson run` to rebuild it; a ledger "
                     f"without account {name!r} would be partial.")
        found = True
        for t in doc.get("transactions", []):
            t.setdefault("account", name)
            txs.append(t)
    if not found:
        sys.exit(f"taxjson fx-cash: no native transaction files in "
                 f"{cache} (run `taxjson run` first).")
    fx = load_fx_history(cache / "to_base.csv", base)
    ledger = FX.build_ledger(txs, base, fx, int(year), country=country)
    verdict = FX.apply_jurisdiction(ledger["net_gain"], country)
    return ledger, verdict, base, int(year), country


def cmd_fx_cash(args: argparse.Namespace) -> None:
    """`taxjson fx-cash`: FX capital gains on foreign-currency cash
    (ITA s.39(1.1) with the $200 de minimis; §988 ordinary-income
    figure for US projects). A standalone REPORT — nothing here
    changes the engine's gains, sum, or the filing exports. Runs on
    demand regardless of the `fx_cash_gains` setting (which only
    controls the end-of-run report)."""
    from taxjson.bin import taxjson_fx_cash as FX
    root = Path(args.dir).resolve()
    cache = root / "work"
    ledger, verdict, base, year, country = _fx_cash_doc(root, cache)
    if getattr(args, "json", False):
        _json_out({"per_currency": ledger["per_currency"],
                   "events": (ledger["events"]
                              if getattr(args, "events", False)
                              else None),
                   "net_gain": ledger["net_gain"],
                   "reportable": verdict["reportable"],
                   "rule": verdict["rule"],
                   "overdrafts": ledger["overdrafts"],
                   "unrated": ledger["unrated"],
                   "open_pools": ledger["pools"],
                   "pools_year_end": ledger.get("pools_year_end") or {},
                   "currency": base, "year": year})
        return
    print(FX.render_report(ledger, base, year, country, verdict))
    if getattr(args, "events", False) and ledger["events"]:
        print("\nDATE ACCOUNT CUR UNITS RATE GAIN SYMBOL")
        for e in ledger["events"]:
            print(f"{e['date']} {e['account']} {e['currency']} "
                  f"{e['units']:,.2f} {e['rate']:g} {e['gain']:+,.2f} "
                  f"{e['symbol'] or '-'}")


def _radar_country_is_usa(root: Path) -> bool:
    """Wording only, AFTER _wash_class_context: the radar it ran was
    given the project's country through the one resolver (a missing or
    unknown country already stopped it), so this only picks the rule's
    name for the text."""
    from taxjson.lib.country import CountryError, canonical_country
    try:
        return canonical_country(_soft_settings(root).get("country")) \
            == "usa"
    except CountryError:
        return False


def _wash_class_context(root: Path, cache: Path, prog: str):
    """(radar, canon, last_loss) shared by buy-check and sell-check:
    the combined radar document flattened per ticker, a symbol-class
    canonicalizer (known-exchange-suffix roots union-folded with the
    project's ticker.map pairs), and each class's most recent LOSING
    disposition from the taxable gains files (economic raw_gain, so a
    wash-denied loss still shows)."""
    import json as _json
    from taxjson.bin.taxjson_watch import flatten_radar
    from taxjson.lib.dispatch import run_cmd as _run
    from taxjson.lib.report_model import resolve_gains_files
    bases = _radar_taxable_bases(root, cache, prog)
    cmd = _cmd("taxjson-wash-radar") + [
        "--taxable", *[str(b) for b in bases], "--all", "--json"]
    cmd += _radar_engine_args(
        bases, root / "phantoms.json",
        _country(_radar_config(root, prog).get("settings", {})))
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    else:
        if _sheltered_expected(root):
            print(f"{prog}: note: no sheltered_base.json in work/ — "
                  f"sheltered-account activity is invisible to the "
                  f"window checks; run a full `taxjson run` to build "
                  f"it.", file=sys.stderr)
    res = _run(cmd, capture_output=True)
    if res.returncode != 0:
        _die(f"radar failed: {(res.stderr or '').strip()[:400]}")
    radar = flatten_radar(_json.loads(res.stdout))

    # Identity comes ONLY from ticker.map (GLOBAL/TOBASE/JOURNAL), SPLIT
    # renames in the books, and an option's own underlying. Two listings
    # that merely share a root (XYZ.TO / XYZ.US) are NOT assumed to be the
    # same security: a CDR, a different issuer (DLR.US Digital Realty vs
    # DLR.TO the Global X ETF) or a share class would all be merged
    # wrongly by suffix stripping. The engine pools by exact mapped symbol,
    # and this matcher must agree with it.
    _distinct_pairs: set = set()
    _tm = None
    _map_path = root / "ticker.map"
    if _map_path.exists():
        try:
            from taxjson.bin.taxjson_ticker_map import load_map_file
            _tm = load_map_file(_map_path)
            _distinct_pairs = {frozenset(s.strip().upper() for s in p)
                               for p in _tm.distinct}
        except Exception as e:
            print(f"{prog}: warning: could not read ticker.map ({e}) "
                  f"— mapped cross-listings will not match.",
                  file=sys.stderr)

    def _root(t: str) -> str:
        t = t.strip().upper()
        # An option is a right to acquire ITS underlying — identical
        # property for s.40(2)(g)/§1091 purposes — so it folds to the
        # underlying symbol exactly as parsed (suffix kept).
        from taxjson.lib.core import parse_option_underlying
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
            print(f"{prog}: warning: {why} would merge a DISTINCT pair "
                  f"from ticker.map ({a} ~ {b}); the pair stays "
                  f"separate.", file=sys.stderr)

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
    for _bp in bases + ([sheltered_base] if sheltered_base.exists()
                        else []):
        try:
            _bdoc = _json.loads(_bp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for _t in _bdoc.get("transactions", []) or []:
            if (_t.get("action") or "").upper() != "SPLIT":
                continue
            _new = (_t.get("symbol_new") or "").strip()
            _old = (_t.get("symbol") or "").strip()
            if _new and _old and _new.upper() != _old.upper():
                _union(_old, _new, f"SPLIT-rename in {_bp.name}")

    # An option root that names no share listing in the books but
    # matches exactly ONE class share of that root on the same exchange
    # is that class: RBC books Rogers' Montreal options under the root
    # RCI (RCI270115C00046000.TO) while the shares are RCI.B.TO, and the
    # call — a right to acquire those shares — fell into an empty
    # RCI.TO class, so buy-check said SAFE (S047-01).
    from taxjson.lib.core import is_option_symbol as _is_opt
    _shares = {t.strip().upper() for t in radar if not _is_opt(t)}
    for _bp in bases:
        try:
            for _t in _json.loads(_bp.read_text(encoding="utf-8")).get(
                    "transactions", []) or []:
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
    # equity sharing its root (ETH.US) (S047-06).
    canon.bare_listings = {_sy for _sy in _shares if "." not in _sy}

    _acct_cfg = _soft_config(root).get("accounts") or {}
    _taxable = {a for a, c in _acct_cfg.items()
                if (c or {}).get("type") == "taxable"}
    last_loss = _last_loss_by_class(
        resolve_gains_files(cache), canon, _taxable,
        usa=_radar_country_is_usa(root))
    return radar, canon, last_loss


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
            print(f"taxjson: warning: could not read {Path(_p).name} "
                  f"({e}) — the 'last loss sale' line leaves out "
                  f"account {_a}; re-run `taxjson run`.", file=sys.stderr)
            continue
        # A sale routed to manual reporting (phantom basis) is still a
        # loss sale for the window: its row has no 'gain', only the
        # engine's raw_gain (S047-02).
        _rows = [(t, False) for t in _doc.get("transactions", [])] + [
            (t, True) for t in _doc.get("manual_reporting_required") or []]
        for _t, _routed in _rows:
            if not _t.get("qty"):
                continue
            if _routed:
                if _t.get("raw_gain") is None:
                    continue
                _g = float(_t.get("raw_gain") or 0.0)
            else:
                if "gain" not in _t:
                    continue
                _g = float(_t.get("raw_gain", _t.get("gain")) or 0.0)
            if _g >= 0:
                continue
            _c = canon(str(_t.get("symbol") or ""))
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
                                 "gain": round(_g, 2),
                                 "phantom_basis": _routed,
                                 "account": _t.get("account") or _a}
    return last_loss


def _fold_class_separator(symbol: str) -> str:
    """A share-class ticker typed the broker/Yahoo way (RCI-B, 'RCI B',
    RCI/B, RCI-B.TO) in the books' dotted spelling (RCI.B, RCI.B.TO).
    Unfolded, the query matched nothing and buy-check answered SAFE
    while a loss on RCI.B.TO sat inside the window (S007-02). Option
    symbols pass through unchanged."""
    from taxjson.lib.core import is_option_symbol
    s = " ".join(str(symbol).split()).upper()
    if is_option_symbol(s):
        return s
    m = re.fullmatch(r"([A-Z0-9]+)[-/ ]([A-Z]{1,2})((?:\.[A-Z]{1,3})?)", s)
    return f"{m.group(1)}.{m.group(2)}{m.group(3)}" if m else s


def _class_matches(radar: Dict[str, Dict[str, Any]], canon, want: str):
    """(class root, {ticker: row}, note) for one queried symbol.

    A query WITH an exchange suffix (XYZ.TO) names one listing and sees
    exactly that listing's class — the listings ticker.map joins to it,
    its split renames and its options. A BARE query (XYZ, BRK.B) names
    no listing: it is resolved to every listing that carries that
    ticker, and each of those keeps its own class. Nothing is ever
    merged by stripping a suffix — only ticker.map makes two listings
    one security. When the bare ticker resolves to more than one class
    the worst verdict is shown with a note asking for an explicit
    spelling."""
    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    q = want.strip().upper()
    base, _, ext = q.rpartition(".")
    if base and ext in (set(KNOWN_SUFFIXES) | {"VN"}):
        wroot = canon(q)
        return wroot, {t: r for t, r in radar.items()
                       if canon(t) == wroot}, None
    _bare = set(getattr(canon, "bare_listings", None) or ())
    if q in _bare or any(t.strip().upper() == q for t in radar):
        # The books hold a suffix-less listing of exactly this name (a
        # coin): that IS the security asked about. `buy-check ETH` gave
        # ETH.US's verdict too, and nothing could ask about the coin
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
    # Bare ticker: the listings that carry it — in the books, or as
    # q.<exchange> joined to the books by a ticker.map rule — then their
    # classes. Each listing keeps its own class.
    booked = {canon(t) for t in radar}
    listings = [t for t in radar
                if t.strip().upper().rpartition(".")[0] == q
                or t.strip().upper() == q]
    listings += [f"{q}.{sx}" for sx in sorted(KNOWN_SUFFIXES)
                 if f"{q}.{sx}" not in listings
                 and canon(f"{q}.{sx}") != f"{q}.{sx}"
                 and canon(f"{q}.{sx}") in booked]
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
                      mode: str) -> Dict[str, Dict[str, Any]]:
    """Keep the radar rows a trade in `want` can actually affect (ITA
    s.54, one-way option rule): buying SHARES touches share losses only
    (shares never replace an option); buying a CALL also touches the
    underlying's share losses (a right to acquire them) and its own
    contract; any other option — and SELLING an option — touches only
    the identical contract (a different series never replaces it)."""
    from taxjson.lib.core import is_option_symbol, parse_option_right
    q = want.strip().upper()
    if not is_option_symbol(q):
        return {t: r for t, r in matches.items()
                if not is_option_symbol(t)}
    call_buy = mode == "buy" and parse_option_right(q) == "C"
    return {t: r for t, r in matches.items()
            if t.strip().upper() == q
            or (call_buy and not is_option_symbol(t))}


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
    return (f"last loss sale this tax year: {ll['symbol']} "
            + (f"{_kind} " if _kind else "")
            + f"{ll['date']} ({_ago_s}, {ll['gain']:,.2f}"
            + (", phantom basis — routed to manual reporting"
               if ll.get("phantom_basis") else "")
            + f") — {_inout}.")


def cmd_buy_check(args: argparse.Namespace) -> None:
    """`taxjson buy-check SYMBOL...`: is buying this ticker TODAY safe
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
        root, cache, "taxjson buy-check")
    _usa = _radar_country_is_usa(root)
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
    for want in args.symbol:
        want = _fold_class_separator(want)
        wroot, matches, _note = _class_matches(radar, _canon, want)
        matches = _replacement_rows(want, matches, "buy")
        # Worst verdict across the class (cross-listings included).
        verdict, lines = "SAFE", ([_note] if _note else [])
        clears = None
        # A VIOLATION anywhere in the class poisons every leg's date:
        # cross-listings are identical property, so a BLOCKED leg's
        # own "safe from" date can be WEEKS before the violation
        # leg's loss becomes safely rebuyable — printing it (or
        # exporting it as the class clears_at) invited a rebuy that
        # cancels the newer loss (2026-09 audit).
        _class_has_violation = any(
            (r.get("category") or "") == "VIOLATION"
            for r in matches.values())
        for t, r in sorted(matches.items()):
            cat = r.get("category") or ""
            if cat in ("BLOCKED", "COOLING", "VIOLATION"):
                verdict = "UNSAFE"
                # VIOLATION's clears_at is the radar's SELL-BY
                # deadline (loss + 30 — the last day to rescue), NOT
                # a re-entry date: printing it as "safe to buy from"
                # invited a rebuy up to four weeks early, killing the
                # loss permanently. Only the re-entry categories
                # carry a usable date; take the LATEST across the
                # class, not the first alphabetically.
                _cd = (r.get("clears_at")
                       if cat != "VIOLATION" and not _class_has_violation
                       else None)
                if _cd:
                    clears = max(clears, _cd) if clears else _cd
                # Only the rebought units' share of the loss is denied
                # (audit S054-07): state it per unit when the radar
                # carries the loss and the units sold at it.
                _rl, _rq = r.get("recent_loss"), r.get("recent_loss_qty")
                _per = (f" on as many units as you buy (about "
                        f"${float(_rl) / float(_rq):,.2f} of it per unit)"
                        if _rl and _rq and float(_rq) > 1e-9 else "")
                lines.append(
                    f"{t}: {cat} — a loss sold within the past 30 "
                    f"days; buying now cancels it{_per}"
                    + ("" if _usa else
                       " if you still hold the shares 30 days after "
                       "that sale")
                    + f" (DEFERRED if bought taxable, PERMANENT if bought "
                    f"{'in an IRA' if _usa else 'sheltered'})."
                    + (f" Safe to buy from {_cd}." if _cd else
                       " Wait until 31 days after the LATEST in-window "
                       "loss sale (see `taxjson wash-radar`)."))
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
                    lines.append(
                        f"{t}: {r.get('advisory')} Buying now changes "
                        f"nothing for that loss, but it starts a new "
                        f"30-day window for a later loss sale.")
            elif cat in ("LOCKED", "EXITABLE", "CAUTION"):
                if verdict == "SAFE":
                    verdict = "SAFE*"
                lines.append(
                    f"{t}: {cat} — no recent loss sale, buying is "
                    f"safe TODAY, but it extends the wash window: "
                    f"{_future_rule}.")
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
        _ll = _last_loss.get(wroot)
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
        _json_out({"results": results, "scope_note": _scope})
    else:
        for r in results:
            print(f"{r['symbol']}: {r['verdict']}")
            for ln in r["detail"]:
                print(f"  {ln}")
        print(_scope)
    if unsafe:
        raise SystemExit(1)


def cmd_sell_check(args: argparse.Namespace) -> None:
    """`taxjson sell-check SYMBOL...`: is selling this ticker AT A
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
    loss at today's price is `taxjson harvest`'s job."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    radar, _canon, _last_loss = _wash_class_context(
        root, cache, "taxjson sell-check")
    _usa = _radar_country_is_usa(root)
    unsafe = 0
    results = []
    for want in args.symbol:
        want = _fold_class_separator(want)
        wroot, matches, _note = _class_matches(radar, _canon, want)
        matches = _replacement_rows(want, matches, "sell")
        verdict, lines = "SAFE", ([_note] if _note else [])
        clears = None        # safe-to-sell-from date (worst = LATEST)
        act_by = None        # rescue deadline: last TRADE date (EARLIEST)
        for t, r in sorted(matches.items()):
            cat = r.get("category") or ""
            adv = r.get("advisory") or ""
            if cat == "LOCKED":
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
                lines.append(f"{t}: {cat} — a recent loss sale is "
                             f"already in its cooling window; if any "
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
                else:
                    lines.append(f"{t}: CLEAR — safe to sell at a loss "
                                 f"now; do not rebuy on EITHER side "
                                 f"(taxable or "
                                 f"{'IRA' if _usa else 'sheltered'}) for "
                                 f"30 days.")
        if len(lines) == bool(_note):
            lines.append(f"{wroot}: no tracked taxable position — "
                         f"nothing to sell (or run `taxjson run` to "
                         f"refresh the books).")
        _lll = _last_loss_line(_last_loss.get(wroot))
        if _lll:
            lines.append(_lll)
        if verdict in ("UNSAFE", "PARTIAL"):
            unsafe += 1
        results.append({"symbol": want.strip().upper(),
                        "verdict": verdict,
                        "clears_at": clears,
                        "act_by": act_by, "detail": lines,
                        "last_loss": _last_loss.get(wroot)})
    # SAFE is "as far as this project's accounts show" (CA-PLAN-04 /
    # US-PLAN-04, audit S054-22).
    from taxjson.lib.wash_scope import scope_note as _scope_note
    _scope = _scope_note("usa" if _usa else "canada")
    if getattr(args, "json", False):
        _json_out({"results": results, "scope_note": _scope})
    else:
        for r in results:
            print(f"{r['symbol']}: {r['verdict']}")
            for ln in r["detail"]:
                print(f"  {ln}")
        print(_scope)
    if unsafe:
        raise SystemExit(1)


# Pipeline artifacts in work/ that are NOT parsed-source files — the
# audit's source join must never read a derived book as provenance.
_AUDIT_DERIVED_SUFFIXES = (
    "_base.json", "_gains.json", "_gains_wash.json", "_raw.json",
    "_raw_base.json", "_raw_gains.json", "_raw_base_gains.json",
    "_merged.json", "_sorted.json", "_filled.json", "_report.json",
    # The crypto ticker.map stage's output: a renamed copy of the
    # parse, read as a second source it claimed a false 2-file dedup
    # and hid the rename (S047-12).
    "_mapped.json", "_manifest.json",
    "_pending_elections.json", "_validate.diag",
)


def _audit_source_files(cache: Path, name: str,
                        accounts=None) -> List[Path]:
    """The parsed-source JSONs for one account: everything the pipeline
    wrote as `{name}_*.json` that is not a derived book. These are the
    per-brokerage parses, converted .tt files, and corp-action rows —
    the nominal-currency provenance the audit joins dispositions to.
    `accounts` (the configured names) keeps a longer-named sibling's
    files out: `margin_*.json` also matches margin_us's parses, which
    the audit then listed twice (S047-13)."""
    siblings = [a for a in (accounts or [])
                if a != name and str(a).startswith(f"{name}_")]
    out = []
    for p in sorted(cache.glob(f"{name}_*.json")):
        if any(p.name.endswith(sfx) for sfx in _AUDIT_DERIVED_SUFFIXES):
            continue
        if any(p.name.startswith(f"{sib}_") for sib in siblings):
            continue
        out.append(p)
    return out


def cmd_audit(args: argparse.Namespace) -> None:
    """`taxjson audit`: the authoritative justification of every
    capital-gain figure. One block per taxable disposition: the parsed
    broker row (nominal currency, original ticker, source file), the
    ticker.map rename, the exact FX rate applied (with provenance and
    a to-the-cent recomputation against the base books), the engine's
    disposition math, the wash-sale / superficial-loss determination
    with replacement lots resolved, a tie-out against the pipeline's
    saved gains files, and the full ACB/FIFO pool trace. Runs the same
    blended computation the pipeline runs (ITA s.47 cross-account ACB
    / US cross-account §1091), so the numbers ARE the filed numbers —
    and exits 1 if any cross-check disagrees."""
    import json as _json
    import os as _os
    import tempfile
    from taxjson.lib.dispatch import run_cmd as _run

    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = _soft_settings(root)
    if not settings:
        _die("no taxjson.toml here — audit is a project command "
             "(run it from the project root, or pass -C).")
    country = _country(settings)
    tax_date = _tax_date(settings)
    base_currency = str(settings.get("base_currency")
                        or _home_currency(settings)).upper()
    year = None if getattr(args, "all_years", False) else (
        getattr(args, "year", None) or settings.get("year"))

    acfgs = _soft_config(root).get("accounts") or {}
    taxable = {n: (c or {}) for n, c in acfgs.items()
               if (c or {}).get("type") == "taxable"}
    if not taxable:
        _die("no taxable accounts in taxjson.toml — nothing to audit.")
    equity = sorted(n for n, c in taxable.items() if not c.get("crypto"))
    crypto = sorted(n for n, c in taxable.items() if c.get("crypto"))
    _acct = getattr(args, "account", None)
    if _acct:
        if _acct not in taxable:
            _die(f"--account {_acct!r} is not a taxable account in "
                 f"taxjson.toml (audit covers: "
                 f"{', '.join(sorted(taxable))}).")
        # Only the computation that holds the account: the blended
        # equity pass (still over ALL equity books — display filter
        # only) or that crypto account's own books. The others would
        # print empty 0/0 blocks.
        if _acct in crypto:
            equity, crypto = [], [_acct]
        else:
            crypto = []

    sheltered_base = cache / "sheltered_base.json"
    phantoms = root / "phantoms.json"
    rates = cache / "to_base.csv"
    tmap = root / "ticker.map"

    # A LOCKED year is recomputed with the option timing its lock
    # recorded, as check-filed does: `audit --year 2025` from a 2026
    # project without since = 2025 printed -1,000 for a year filed at
    # -601, with no word (S048-18).
    _timing_flags = option_timing_flags(settings)
    if year and str(year) != str(settings.get("year")):
        from taxjson.bin import taxjson_filed as _tf
        _lp = _tf.snapshot_path(root, year)
        if _lp.exists():
            try:
                _lock = _json.loads(_lp.read_text(encoding="utf-8"))
                _lot = (_lock.get("option_timing")
                        if isinstance(_lock, dict) else None)
            except (OSError, ValueError) as e:
                _lot = None
                print(f"taxjson audit: warning: cannot read "
                      f"filed/{_lp.name} ({e}) — {year} is recomputed "
                      f"with this project's option timing, which may "
                      f"not be the timing it was filed on.",
                      file=sys.stderr)
            if isinstance(_lot, dict):
                _lf = _tf._lock_timing_flags(settings, int(year), _lot)
                if _lf != _timing_flags:
                    print(f"taxjson audit: note: {year} is locked "
                          f"(filed/{_lp.name}) — recomputed with the "
                          f"option timing its lock recorded "
                          f"({' '.join(_lf)}), not this project's "
                          f"({' '.join(_timing_flags) or 'none'}).",
                          file=sys.stderr)
                _timing_flags = _lf

    def common_flags() -> List[str]:
        fl = ["--country", country, "--tax-date", tax_date,
              "--base-currency", base_currency]
        if year:
            fl += ["--year", str(year)]
        if settings.get("year"):
            # The saved gains files are year-scoped (R1-191).
            fl += ["--check-year", str(settings.get("year"))]
        if rates.exists():
            fl += ["--rates", str(rates)]
        if tmap.exists():
            fl += ["--map", str(tmap)]
        if phantoms.exists():
            fl += ["--incomplete-history", str(phantoms)]
        fl += _timing_flags
        for sym in getattr(args, "symbol", None) or []:
            fl += ["--symbol", sym]
        if getattr(args, "gain_id", None):
            fl += ["--id", args.gain_id]
        if getattr(args, "date", None):
            fl += ["--date", args.date]
        if getattr(args, "account", None):
            fl += ["--account", args.account]
        if getattr(args, "summary", False):
            fl.append("--summary")
        if getattr(args, "no_trace", False):
            fl.append("--no-trace")
        if getattr(args, "no_color", False):
            fl.append("--no-color")
        # "Nothing matched" is decided across ALL invocations below.
        fl += ["--no-match-rc", "3"]
        return fl

    # (flags, cleanup_path|None) per engine computation, mirroring the
    # pipeline: ONE blended pass over the equity taxable accounts, then
    # the crypto accounts — ONE blended pass too for a Canadian project
    # with two or more of them (the pipeline's crypto blend), else each
    # on its own books.
    invocations: List[Tuple[List[str], Optional[Path]]] = []
    crypto_blend = country not in ("us", "usa") and len(crypto) >= 2

    _no_input = _accounts_skipped_for_no_inputs(root)
    # Configured accounts (with inputs) the audit could not cover: the
    # tie-out printed "✓" over the rest and exited 0, and the checklist
    # marked every disposition traced (S047-16).
    _uncovered: List[str] = []

    def _blended_invocation(names: List[str], *, is_crypto: bool) -> None:
        bases = [cache / f"{n}_base.json" for n in names
                 if (cache / f"{n}_base.json").exists()]
        missing = [n for n in names
                   if not (cache / f"{n}_base.json").exists()
                   and n not in _no_input]
        _uncovered.extend(missing)
        if missing:
            print(f"taxjson audit: note: no books yet for "
                  f"{', '.join(missing)} — run `taxjson run` to include "
                  f"them.", file=sys.stderr)
        if not bases:
            return
        cleanup: Optional[Path] = None
        if len(bases) == 1:
            base_arg = bases[0]
        else:
            # Merge the CURRENT per-account base books — the same
            # inputs the pipeline's blended pass merged, rebuilt fresh
            # so a stale .blend artifact can't smuggle old numbers
            # past the tie-out.
            fd, tmp = tempfile.mkstemp(prefix="taxjson_audit_",
                                       suffix=".json")
            _os.close(fd)
            cleanup = Path(tmp)
            res = _run(_cmd("taxjson-merge")
                       + [str(b) for b in bases],
                       capture_output=True)
            if res.returncode != 0:
                cleanup.unlink(missing_ok=True)
                _die(f"could not merge the taxable base books: "
                     f"{(res.stderr or '').strip()[:300]}")
            cleanup.write_text(res.stdout, encoding="utf-8")
            base_arg = cleanup
        fl = common_flags() + ["--base", str(base_arg)]
        if sheltered_base.exists():
            fl += ["--sheltered", str(sheltered_base)]
        if country in ("us", "usa") and not is_crypto:
            fl.append("--per-account-basis")
        for n in names:
            for src in _audit_source_files(cache, n, list(acfgs)):
                fl += ["--source", str(src)]
            if (cache / f"{n}_filled.json").exists():
                fl += ["--filled", str(cache / f"{n}_filled.json")]
            chk = cache / f"{n}_gains_wash.json"
            if not chk.exists():
                chk = cache / f"{n}_gains.json"
            if chk.exists():
                fl += ["--check", str(chk)]
        invocations.append((fl, cleanup))

    if equity:
        _blended_invocation(equity, is_crypto=False)
    if crypto_blend:
        _blended_invocation(crypto, is_crypto=True)

    for n in ([] if crypto_blend else crypto):
        base = cache / f"{n}_base.json"
        if not base.exists():
            if n not in _no_input:
                _uncovered.append(n)
                print(f"taxjson audit: note: no books yet for {n} — run "
                      f"`taxjson run` to include it.", file=sys.stderr)
            continue
        fl = common_flags() + ["--base", str(base)]
        if country in ("us", "usa"):
            # §1091 does not reach digital assets — mirror the
            # pipeline's --no-wash on US crypto books.
            fl.append("--no-wash")
        elif sheltered_base.exists():
            fl += ["--sheltered", str(sheltered_base)]
        for src in _audit_source_files(cache, n, list(acfgs)):
            fl += ["--source", str(src)]
        if (cache / f"{n}_filled.json").exists():
            # Crypto rows priced by fill-crypto-prices (R1-271).
            fl += ["--filled", str(cache / f"{n}_filled.json")]
        chk = cache / f"{n}_gains_wash.json"
        if not chk.exists():
            chk = cache / f"{n}_gains.json"
        if chk.exists():
            fl += ["--check", str(chk)]
        invocations.append((fl, None))

    if not invocations:
        _die("no computed books in work/ — run `taxjson run` first.")

    rc = 0
    no_match = 0
    json_docs: List[Dict[str, Any]] = []
    try:
        for fl, _cl in invocations:
            cmd = _cmd("taxjson-audit") + fl
            if getattr(args, "json", False):
                res = _run(cmd + ["--json"], capture_output=True)
                if res.stderr:
                    sys.stderr.write(res.stderr)
                if res.returncode == 3:
                    no_match += 1
                    continue
                try:
                    json_docs.append(_json.loads(res.stdout))
                except ValueError:
                    _die(f"taxjson-audit produced no JSON "
                         f"(rc={res.returncode}).")
                rc = max(rc, res.returncode or 0)
            else:
                res = _run(cmd)
                if res.returncode == 3:
                    no_match += 1
                    continue
                rc = max(rc, res.returncode or 0)
    finally:
        for _fl, _cl in invocations:
            if _cl is not None:
                _cl.unlink(missing_ok=True)
    if no_match and no_match == len(invocations):
        _filt = [f"{k} {v}" for k, v in (
            ("symbol", " ".join(getattr(args, "symbol", None) or [])),
            ("--id", getattr(args, "gain_id", None)),
            ("--date", getattr(args, "date", None)),
            ("--account", _acct)) if v]
        _die(f"no disposition matches {', '.join(_filt)}"
             + (f" in {year}" if year else "")
             + " — check the filter (`taxjson audit --summary` lists "
               "every event; --date matches the trade or settlement "
               "date).")

    if getattr(args, "json", False):
        _json_out(_merge_audit_json(json_docs, base_currency, country))
    if _uncovered and not _acct:
        print(f"taxjson audit: WARNING: not audited — no books for "
              f"{', '.join(sorted(set(_uncovered)))}; their dispositions "
              f"are not in the tie-out above. Run `taxjson run`.",
              file=sys.stderr)
        rc = max(rc, 1)
    if rc:
        raise SystemExit(rc)


def _merge_audit_json(docs: List[Dict[str, Any]], base_currency: str,
                      country: str) -> Dict[str, Any]:
    """One `taxjson audit --json` document from the per-book
    taxjson-audit runs: events concatenated, totals summed over EVERY
    book (S026-11 pins it), reconciliation failures kept (they were
    dropped when two books were merged)."""
    if len(docs) == 1:
        return docs[0]
    from taxjson.bin.taxjson_audit import TOTALS_NOTE
    events = [e for d in docs for e in d.get("events") or []]

    def _total(ev_key: str, doc_key: str) -> float:
        # Over the events, rounded ONCE: adding each book's already-
        # rounded total put the audit a cent off wash-sales (S047-17).
        # Books whose events carry no amount keep their own totals.
        if events and all(ev_key in e for e in events):
            return round(sum(float(e.get(ev_key) or 0.0)
                             for e in events), 2)
        return round(sum(float(d.get(doc_key) or 0.0) for d in docs), 2)
    return {"base_currency": base_currency, "country": country,
            "events": events,
            "total_gain": _total("gain", "total_gain"),
            "total_disallowed": _total("disallowed_amount",
                                       "total_disallowed"),
            "totals_note": TOTALS_NOTE,
            "reconciliation_failures": [
                f for d in docs
                for f in d.get("reconciliation_failures") or []],
            "failed": any(d.get("failed") for d in docs)}


def cmd_find_missing_history(args: argparse.Namespace) -> None:
    """Convenience wrapper over `taxjson-missing-history`: resolve the account
    base file(s) from the project and default the year from taxjson.toml."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    if args.account:
        f = cache / f"{args.account}_base.json"
        if not f.exists():
            sys.exit(f"taxjson find-missing-history: no {f.name} in {cache} "
                     f"(run `taxjson run` first, or check the name).")
        files = [f]
    else:
        # Per-account full-history base files only — skip the raw/native
        # derivatives and the combined sheltered file (its accounts are
        # already covered individually).
        files = sorted(p for p in cache.glob("*_base.json")
                       if not p.name.endswith("_raw_base.json")
                       and p.name != "sheltered_base.json"
                       and not p.name.startswith("."))
        if not files:
            sys.exit(f"taxjson find-missing-history: no base files in {cache} "
                     f"(run `taxjson run` first).")

    year = args.year
    if year is None:
        year = _soft_settings(root).get("year")

    # --gen-phantoms: instead of the diagnostic report, emit a phantom
    # opening-balance file for the truncated-history rows (positions that go
    # negative — the subset phantoms actually fix). $0-cost corp-action rows
    # are intentionally NOT emitted; those need a merger/spinoff basis, not a
    # synthetic opening balance. The emit itself (incl. year-scoping) lives in
    # `taxjson-gains --suggest-phantoms`, which takes one base file, so we run
    # it per account and merge the JSON arrays (keyed by symbol+account).
    if args.gen_phantoms:
        import json
        import tempfile

        merged: Dict[Tuple[str, str], dict] = {}
        for f in files:
            with tempfile.NamedTemporaryFile(
                    "r", suffix=".json", delete=False) as tmp:
                tmp_path = tmp.name
            cmd = _cmd("taxjson-gains") + ["--suggest-phantoms", tmp_path]
            # The project's jurisdiction, as the pipeline passes it
            # (taxjson-gains requires it). (Sheltered books
            # stay in: a short in a registered account is the MOST
            # certain phantom, and `run` applies phantoms.json to every
            # account's gains stage.)
            cmd += ["--country", _country(_soft_settings(root))]
            if year:
                cmd += ["--year", str(year)]
            if args.include_options:
                cmd += ["--include-options-in-suggestions"]
            if args.all_history:
                cmd += ["--all-history"]
            cmd += [str(f)]
            # Capture the inner tool's stderr and re-emit everything except
            # the lines referencing the temp file (deleted below; our merged
            # summary replaces them). Devnulling ALL of it swallowed the
            # "Filtered N candidate(s)... Use --all-history" warning — the
            # only signal that a year-scoped run dropped a pair.
            from taxjson.lib.dispatch import run_cmd as _run_cmd
            proc = _run_cmd(cmd, capture_output=True)
            if proc.stdout:
                sys.stdout.write(proc.stdout)
            for line in (proc.stderr or "").splitlines():
                if tmp_path in line:
                    continue
                if line.strip():
                    print(f"  [{f.name}] {line}", file=sys.stderr)
            if proc.returncode != 0:
                sys.exit(f"taxjson find-missing-history --gen-phantoms: "
                         f"taxjson-gains failed on {f.name} "
                         f"(exit {proc.returncode}).")
            try:
                entries = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entries = []
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            for e in entries:
                merged[(e.get("symbol"), e.get("account"))] = e

        out = Path(args.gen_phantoms)
        rows = list(merged.values())
        try:
            out.write_text(json.dumps(rows, indent=2) + "\n",
                           encoding="utf-8")
        except OSError as e:
            # A directory (or unwritable path) argument crashed with a
            # raw traceback AFTER all the per-account work (REVIEW #40).
            sys.exit(f"taxjson find-missing-history: cannot write "
                     f"--gen-phantoms {out}: {e} — pass a FILE path, "
                     f"e.g. {out / 'phantoms.json' if out.is_dir() else 'phantoms.json'}")
        n_reg = sum(1 for e in rows
                    if "Registered" in (e.get("_note") or ""))
        # Resolved: a relative path names a file under the CWD, and
        # comparing it unresolved with the resolved root told the user
        # to save a file already in place (S047-19).
        hint = ("`taxjson run` auto-detects it"
                if out.resolve() == (root / "phantoms.json").resolve()
                else f"save it as {root / 'phantoms.json'} and `taxjson run` "
                     f"picks it up, or pass it to taxjson-gains "
                     f"--incomplete-history")
        print(f"\nWrote {len(rows)} phantom candidate(s) to {out} "
              f"({n_reg} in registered accounts — almost certainly phantom).\n"
              f"Review the file and remove any entries that are real short "
              f"positions. Then {hint}.", file=sys.stderr)
        return

    cmd = _cmd("taxjson-missing-history") + [str(f) for f in files]
    if not args.account:
        # The glob above sees only books that exist: a CONFIGURED
        # account whose book was never built (a failed parse, deferred
        # elections) was skipped silently and the checklist marked the
        # step done (2026-09 audit S047-18). Accounts `run` skipped for
        # having no inputs at all are expected to have none.
        _have = {f.name[: -len("_base.json")] for f in files}
        _quiet = _accounts_skipped_for_no_inputs(root)
        for _n in sorted((_soft_config(root).get("accounts") or {})):
            if _n not in _have and _n not in _quiet:
                print(f"taxjson find-missing-history: note: account {_n} "
                      f"has no work/{_n}_base.json — not checked (run "
                      f"`taxjson run`).", file=sys.stderr)
                cmd += ["--unchecked-account", _n]
    if year:
        cmd += ["--year", str(year)]
    if args.include_options:
        cmd += ["--include-options"]
    if (root / "phantoms.json").exists():
        cmd += ["--phantoms", str(root / "phantoms.json")]
    if (root / "ticker.map").exists():
        # Name the broker's ticker of a renamed symbol (S049-01).
        cmd += ["--ticker-map", str(root / "ticker.map")]
    _exec_tool(cmd)


def cmd_fees_sum(args: argparse.Namespace) -> None:
    """Convenience wrapper over `taxjson-fees`: trading-fee report by brokerage
    (converted to the base currency), reading the parsed per-broker JSONs in
    work/. Resolves cache / base currency / rates from the project. Like the
    other roll-ups it takes an optional PERIOD window (30d/6w/…, wired to
    `taxjson-fees --since`), defaulting to the tax year; a lone non-period
    positional is read as an account (its per-broker files only)."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    if not cache.exists():
        sys.exit(f"taxjson fees-sum: no {cache} (run `taxjson run` first).")
    settings = _soft_settings(root)

    period, account = args.period, args.account
    if period and account is None and not _is_period(period):
        if period.strip()[:1].isdigit():
            _tx_period_cutoff(period)                # sys.exits: invalid period
        account, period = period, None


    cmd = _cmd("taxjson-fees")
    if account:
        # Scope to one account by feeding only its parsed per-broker files;
        # non-broker JSONs (…_base.json etc.) are skipped by taxjson-fees.
        # Exclude files that actually belong to a LONGER-named sibling
        # account sharing this prefix (accounts `margin` and `margin_us`:
        # the glob "margin_*.json" also matches margin_us_ib.json, leaking
        # the sibling's fees into this account's total).
        accounts_cfg = _soft_config(root).get("accounts") or {}
        siblings = [a for a in accounts_cfg
                    if a != account and a.startswith(f"{account}_")]
        files = sorted(
            f for f in cache.glob(f"{account}_*.json")
            if not any(f.name.startswith(f"{sib}_") for sib in siblings))
        if not files:
            sys.exit(f"taxjson fees-sum: no files for account {account!r} in "
                     f"{cache} (run `taxjson run` first, or check the name).")
        cmd += [str(f) for f in files]
    else:
        cmd += ["--cache", str(cache)]

    tok = (period or "").strip().lower()
    if period and _YEAR_TOKEN_RE.fullmatch(tok):         # literal year window
        cmd += ["--year", tok]
    elif period and tok in _TAX_YEAR_TOKENS:             # explicit tax_year
        year = settings.get("year")
        if year:
            cmd += ["--year", str(year)]
    elif period and tok not in ("all", "max"):           # look-back window
        cmd += ["--since", _tx_period_cutoff(period).isoformat()]
    elif not period:                                     # default: tax year
        year = settings.get("year")
        if year:
            cmd += ["--year", str(year)]

    # taxjson-fees needs --rates alongside --to; pass both only when the rate
    # file exists, else report native per-brokerage amounts (no conversion).
    rates = cache / "to_base.csv"
    if rates.exists():
        cmd += ["--to", _base(settings),
                "--rates", str(rates)]
    if args.by_account:
        cmd += ["--by-account"]
    if (root / "ticker.map").exists():
        # DELETE'd symbols' fees, like the books (S038-11).
        cmd += ["--ticker-map", str(root / "ticker.map")]
    if args.json:
        cmd += ["--json"]
    _exec_tool(cmd)


def cmd_serve(args: argparse.Namespace) -> None:
    # Lazy import so the [web] extra is only needed for this subcommand.
    from taxjson.web.server import serve
    raise SystemExit(serve(args.dir, host=args.host, port=args.port,
                           require_token=getattr(args, "token", False)))


def cmd_init(args: argparse.Namespace) -> None:
    country = _normalize_country(args.country)
    if country not in _INIT_BY_COUNTRY:
        sys.exit(f"taxjson init: unknown country {args.country!r} "
                 f"(expected canada | ca | usa | us)")

    _year = getattr(args, "year", None)
    _max_year = date_cls.today().year + 1
    if _year is not None and not (1900 <= _year <= _max_year):
        # 0/-5/20255 scaffolded projects whose every report was
        # silently all-zero (REVIEW #28); 2099 fetched no FX rates
        # and built empty books (2026-09 CLI audit).
        sys.exit(f"taxjson init: --year {_year} is not a plausible tax "
                 f"year (expected 1900..{_max_year})")

    # The positional `path` (if given) overrides the global -C/--dir flag.
    target = getattr(args, "path", None) or args.dir
    root = Path(target).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # An existing FILE, a path under a file, or an unwritable
        # parent tracebacked (R1-263).
        _die(f"cannot create the project directory {root}: "
             + ("a file of that name exists"
                if isinstance(e, FileExistsError) or root.is_file()
                else (e.strerror or str(e))))
    cfg = root / "taxjson.toml"
    if cfg.exists() and not args.force:
        _die(f"{cfg} already exists (use --force to overwrite)")

    written: List[str] = []

    # The config is (re)written — the guard above already enforces --force.
    config_text, account_names = _render_init_config(
        country, getattr(args, "year", None))
    bak_name = "taxjson.toml.bak"
    if cfg.exists():
        # --force re-templates: keep the user's previous config (their
        # accounts, holdings, instalments) recoverable. Never overwrite
        # an earlier backup: a second --force (fixing the --country)
        # replaced the only copy of the user's config with the first
        # template (R1-255). Same numbering as fetch's .bak files.
        bak = cfg.with_name(bak_name)
        n = 1
        while bak.exists() and bak.read_bytes() != cfg.read_bytes():
            bak = cfg.with_name(f"{bak_name}{n}")
            n += 1
        if not bak.exists():
            shutil.copy2(cfg, bak)
        bak_name = bak.name
        written.append(f"{bak_name} (your previous config)")
    cfg.write_text(config_text)
    written.append("taxjson.toml")
    if country == "usa":
        print(_US_EXPERIMENTAL_NOTE, file=sys.stderr)

    # Everything else is written ONLY when absent, so re-running `init
    # --force` re-templates the config without clobbering a ticker.map or
    # README the user has already populated.
    def _stub(rel: str, content: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(content)
            written.append(rel)

    _stub("ticker.map", _TEMPLATE_TICKER_MAP)
    _stub(".gitignore", _TEMPLATE_GITIGNORE)
    for acct in account_names:
        # The README also keeps the empty input dir present under git.
        _stub(f"inputs/{acct}/README.txt", _INPUT_README)

    print(f"Initialized taxjson project at {root}")
    for rel in written:
        print(f"  wrote {rel}")
    # Folders a previous scaffold (e.g. --force from canada to usa)
    # left under inputs/ that the new config has no section for.
    _inputs = root / "inputs"
    _orphans = sorted(d.name for d in _inputs.iterdir()
                      if d.is_dir() and d.name not in account_names
                      and d.name != "slips") if _inputs.is_dir() else []
    if _orphans:
        print(f"  note: inputs/ has folder(s) with no [accounts.*] "
              f"section in the new config: {', '.join(_orphans)} — "
              f"re-add their sections (see {bak_name}) or remove "
              f"the folders.")
    import shlex as _shlex
    print("\nNext:")
    print(f"  1. edit {cfg} — set the year, accounts, and source currencies")
    print("  2. drop broker CSV exports into inputs/<account>/")
    print(f"  3. run: taxjson -C {_shlex.quote(str(root))} run")


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


def main() -> None:
    # Tax data is private: everything this process and its pipeline
    # stages create is owner-only (files 0600, dirs 0700) whatever the
    # shell umask — SECURITY.md promises it.
    from taxjson.bin._entry import private_umask
    private_umask()
    p = argparse.ArgumentParser(prog="taxjson",
                                description=__doc__.splitlines()[0],
                                formatter_class=_CappedHelpFormatter)
    p.add_argument("-C", "--dir", default=".", help="Project root (default: cwd)")
    try:
        from importlib.metadata import version as _pkg_version
        _ver = _pkg_version("taxjson")
    except Exception:
        _ver = "unknown"
    p.add_argument("--version", action="version",
                   version=f"taxjson {_ver}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    p_run = sub.add_parser(
        "run",
        help="Run the full pipeline (full rebuild by default; "
             "--fast reuses cached stages)")
    p_run.add_argument("--account", help="Process only one account")
    p_run.add_argument("--fast", action="store_true",
                       help="Incremental run: reuse mtime-cached stage "
                            "outputs; stages whose inputs, config and "
                            "code are unchanged are skipped")
    p_run.add_argument("--no-input", action="store_true",
                       help="Never prompt (GUI/CI): unresolved corp-action "
                            "elections write work/pending_elections.json "
                            "and exit 3 — resolve with `taxjson elect "
                            "... --set`, then re-run")
    p_run.add_argument("--strict", action="store_true",
                       help="Promote per-account validation ERRORs "
                            "(oversold positions, malformed rows) to "
                            "fatal — the run stops instead of "
                            "publishing reports with a DIAGNOSTICS "
                            "banner. Recommended for CI/cron")
    p_run.set_defaults(func=cmd_run)

    p_elect = sub.add_parser(
        "elect", help="View/redo corporate-action tax elections")
    p_elect.add_argument("account", nargs="?",
                         help="Account to act on (omit to list all accounts)")
    p_elect.add_argument("--redo", action="store_true",
                         help="Clear the election(s) and re-prompt now")
    p_elect.add_argument("--reset", action="store_true",
                         help="Clear the election(s) without re-prompting "
                              "(next `taxjson run` asks again)")
    p_elect.add_argument("--event", metavar="ID",
                         help="Scope --redo/--reset to one event id "
                              "(from the list); default is all")
    p_elect.add_argument("--set", metavar="EVENT_ID=ELECTION",
                         help="Write one election non-interactively "
                              "(headless/CI bootstrap), e.g. --set "
                              "20251022-ssl-rgld-51d7=rollover_s_85_1_5")
    p_elect.add_argument("--hint", action="append", metavar="KEY=VALUE",
                         help="Hint for --set (repeatable), e.g. "
                              "--hint fmv_per_share=12.5")
    p_elect.add_argument("--pending", action="store_true",
                         help="Show elections a --no-input/headless run "
                              "left unresolved (work/pending_elections"
                              ".json), with ready-to-copy --set lines")
    p_elect.add_argument("--json", action="store_true",
                         help="With --pending: emit the raw pending "
                              "document")
    p_elect.set_defaults(func=cmd_elect)

    p_init = sub.add_parser("init", help="Scaffold a new project")
    p_init.add_argument("path", nargs="?", help="Directory to initialize (default: cwd)")
    p_init.add_argument("--country", required=True,
                        choices=["canada", "ca", "usa", "us"],
                        help="Jurisdiction to scaffold for (required; shapes "
                             "the config's currencies, tax-date basis, and "
                             "account folders)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing")
    p_init.add_argument("--year", type=int,
                        help="Tax year for the generated config "
                             "(default: current year)")
    p_init.set_defaults(func=cmd_init)

    p_tx = sub.add_parser(
        "events",
        help="Print native (pre-base) transactions in taxtext format over a "
             "look-back window, chronological oldest→latest")
    p_tx.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_tx.add_argument("account", nargs="?",
                      help="Account (default: all accounts, merged)")
    p_tx.add_argument("--json", action="store_true",
                     help="Emit JSON instead of text")
    p_tx.set_defaults(func=cmd_transactions)

    p_div = sub.add_parser(
        "divs",
        help="Like `events` but only DIVIDEND and DIVIDEND_IN_LIEU rows "
             "(native, taxtext)")
    p_div.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_div.add_argument("account", nargs="?", help="Account (default: all)")
    p_div.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_div.set_defaults(func=cmd_dividends)

    p_xfer = sub.add_parser(
        "transfers",
        help="Custody-transfer EVIDENCE view: depot flips, listing "
             "journals, broker migrations, and crypto "
             "withdrawals/sends (matched pairs read as self-custody "
             "moves; unmatched out-legs are gift/payment candidates "
             "— dispositions at FMV if they left your ownership). "
             "The TRANSFER rows the books deliberately exclude; "
             "sidecar rows from taxable parses + in-book rows from "
             "sheltered accounts.")
    p_xfer.add_argument("account", nargs="?",
                        help="Account (default: all accounts)")
    p_xfer.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_xfer.set_defaults(func=cmd_transfers_view)

    p_csend = sub.add_parser(
        "crypto-sends",
        help="Crypto withdrawals/sends that did not arrive on another of "
             "your exchanges: decide self (own wallet) / gift / payment, "
             "see the fair value and the .tt BUYSELL line; --write "
             "generates inputs/<acct>/crypto_sends.tt. Stablecoins get "
             "the currency-gain calculation instead of a sale line.")
    p_csend.add_argument("account", nargs="?",
                         help="Crypto account (default: all)")
    p_csend.add_argument("--set", action="append", metavar="ID=DECISION",
                         help="Record a decision (self | gift | payment) "
                              "for a send id from the listing; "
                              "repeatable")
    p_csend.add_argument("--note", metavar="TEXT",
                         help="With --set: a note kept with the decision "
                              "and written into crypto_sends.tt")
    p_csend.add_argument("--price", type=float, metavar="P",
                         help="With --set: fair value per coin in the base "
                              "currency, when the price lookup fails")
    p_csend.add_argument("--write", action="store_true",
                         help="(Re)generate inputs/<acct>/crypto_sends.tt "
                              "from the decisions (idempotent)")
    p_csend.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_csend.set_defaults(func=cmd_crypto_sends)

    p_dil = sub.add_parser(
        "dil",
        help="Like `events` but only DIVIDEND_IN_LIEU rows — payments in "
             "lieu received while shares were lent out or short over the "
             "ex-date (native, taxtext)")
    p_dil.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_dil.add_argument("account", nargs="?", help="Account (default: all)")
    p_dil.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_dil.set_defaults(func=cmd_dil)

    p_roc = sub.add_parser(
        "roc",
        help="Like `events` but only ADJUST rows — return-of-capital ACB "
             "reductions (broker-classified) and manual .tt adjustments")
    p_roc.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_roc.add_argument("account", nargs="?", help="Account (default: all)")
    p_roc.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_roc.set_defaults(func=cmd_roc)

    p_leaps = sub.add_parser(
        "leaps",
        help="Closed LEAPS positions over a window — engine dispositions "
             "of long option buys placed >3 months to expiry, with "
             "lot-matched base-currency gains")
    p_leaps.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_leaps.add_argument("account", nargs="?", help="Account (default: all)")
    p_leaps.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_leaps.set_defaults(func=cmd_leaps)

    p_bs = sub.add_parser(
        "trades",
        help="Like `events` but only BUYSELL/ASSIGN rows (native, taxtext)")
    p_bs.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_bs.add_argument("account", nargs="?", help="Account (default: all)")
    _add_instrument_filters(p_bs)
    p_bs.add_argument("--json", action="store_true",
                     help="Emit JSON instead of text")
    p_bs.set_defaults(func=cmd_buysell)

    p_g = sub.add_parser(
        "gains",
        help="Realized gains in NATIVE terms (pre-TOBASE, pre-currency-to-base)")
    p_g.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_g.add_argument("account", nargs="?", help="Account (default: all)")
    _add_instrument_filters(p_g)
    p_g.add_argument("--json", action="store_true",
                    help="Emit JSON instead of text")
    p_g.set_defaults(func=cmd_gains)

    p_scan = sub.add_parser(
        "scan",
        help="Lint the project for common tax-efficiency mistakes "
             "(US-listed Canadian dividend payers in taxable/TFSA, US "
             "payers in TFSA, ticker.map cross-listing gaps); exit 1 "
             "on findings")
    p_scan.add_argument("--online", action="store_true",
                        help="Also probe yfinance for .TO twins of "
                             "unmapped US-listed dividend payers "
                             "(ticker.map coverage candidates)")
    p_scan.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    p_scan.set_defaults(func=cmd_scan)

    p_sum = sub.add_parser(
        "sum",
        help="Cross-account realized-gains summary (base currency, one "
             "row per account): TAXABLE / SHELTERED / ALL tables when "
             "both types exist, one table otherwise; "
             "--other-income/--other-losses add a marginal tax ESTIMATE")
    p_sum.add_argument("--other-income", type=float, default=None,
                       metavar="AMT",
                       help="Non-investment income (employment etc.) the "
                            "investment income stacks on top of — turns "
                            "on the tax-estimate block")
    p_sum.add_argument("--other-losses", type=float, default=None,
                       metavar="AMT",
                       help="Prior-year capital losses (full dollars) to "
                            "net against this year's gains — turns on "
                            "the tax-estimate block")
    for _p in (p_sum,):
        _add_deduction_flags(_p)
    p_sum.add_argument("account", nargs="?",
                       help="Account (default: all accounts). No "
                            "PERIOD here by design: sum reports the "
                            "tax year's filing basis from the built "
                            "artifacts — windowed views are "
                            "winners/ccd-sum/leaps-sum.")
    p_sum.add_argument("--province", default=None, metavar="PROV",
                       help="Province for the canada estimate (ON/BC/AB; "
                            "default: `province` under [settings])")
    p_sum.add_argument("--verbose", "-v", action="store_true",
                       help="With the estimate: print the CALCULATION "
                            "TRACE — every bracket slice, credit and "
                            "surtax tier, base vs with-investments")
    p_sum.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_sum.set_defaults(func=cmd_summary)

    p_est = sub.add_parser(
        "estimate",
        help="ESTIMATE the tax on the year's investment income — "
             "tax(other income + investment income) minus tax(other "
             "income), bracketed on top of what you already earn "
             "(Canada: 50%% inclusion, eligible gross-up/DTC, FTC "
             "from the books' actual TAX rows; taxable accounts "
             "only). Planning numbers, never filing numbers")
    p_est.add_argument("--other-income", type=float, default=None,
                       metavar="AMT",
                       help="Employment/other income the investment "
                            "income stacks on top of (default: 0)")
    p_est.add_argument("--other-losses", type=float, default=None,
                       metavar="AMT",
                       help="Prior-year capital losses applied, in "
                            "FULL dollars (netted before the 50%% "
                            "inclusion)")
    _add_deduction_flags(p_est)
    p_est.add_argument("--province", default=None,
                       help="Canada: ON|BC|AB (default: `province` "
                            "under [settings])")
    p_est.add_argument("--verbose", "-v", action="store_true",
                       help="Full CALCULATION TRACE: every bracket "
                            "slice, credit and surtax tier for the "
                            "base and with-investments runs")
    p_est.add_argument("--json", action="store_true",
                       help="Emit the full document as JSON (accounts, "
                            "totals, estimate, and instalments when "
                            "configured)")
    p_est.set_defaults(func=cmd_estimate)

    p_inst = sub.add_parser(
        "instalments",
        help="Canadian tax instalments: what each of the four dates "
             "(Mar/Jun/Sep/Dec 15) calls for under your chosen basis, "
             "what you have paid, and the offset interest plus "
             "s.163.1 penalty that follow from any gap. The "
             "current-year basis is driven by `taxjson estimate` "
             "itself (AMT included). Configure [instalments] in "
             "taxjson.toml")
    p_inst.add_argument("--json", action="store_true",
                        help="Emit the schedule and interest as JSON")
    p_inst.set_defaults(func=cmd_instalments)

    # Period-aware roll-ups: optional PERIOD positional (30d/6w/…), else the
    # config tax year. A lone non-period positional is read as an account.
    p_dsum = sub.add_parser(
        "divs-sum",
        help="Dividend total per ticker over a window (default: tax "
             "year), each row in its tax year (lib/income_dating)")
    p_dsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_dsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_dsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_dsum.set_defaults(func=cmd_divs_sum)

    p_dilsum = sub.add_parser(
        "dil-sum",
        help="Payment-in-lieu total per symbol over a window (default: "
             "tax year) with each row's treatment — ordinary income, or "
             "(Canada) a Canadian dealer's payment on a Canadian share, "
             "a dividend under ITA s.260 (also in divs-sum)")
    p_dilsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_dilsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_dilsum.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_dilsum.set_defaults(func=cmd_dil_sum)

    p_rsum = sub.add_parser(
        "roc-sum",
        help="Return-of-capital / ACB-adjustment total per ticker over a "
             "window (default: tax year)")
    p_rsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_rsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_rsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_rsum.set_defaults(func=cmd_roc_sum)

    p_win = sub.add_parser(
        "winners",
        help="Per-ticker realized gains RANKED — biggest winners and "
             "losers over a window (default: tax year)")
    p_win.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_win.add_argument("account", nargs="?",
                       help="Account (default: all)")
    p_win.add_argument("--top", type=int, default=10, metavar="N",
                       help="Show the top/bottom N (default: "
                            "%(default)s); --json carries all")
    p_win.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_win.set_defaults(func=cmd_winners)

    p_ccd = sub.add_parser(
        "ccd-sum",
        help="Covered-call (short call) realized-gain summary per "
             "underlying over a window (default: tax year)")
    p_ccd.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_ccd.add_argument("account", nargs="?", help="Account (default: all)")
    p_ccd.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_ccd.set_defaults(func=cmd_ccd_sum)

    p_lsum = sub.add_parser(
        "leaps-sum",
        help="Realized-gain summary for closed LEAPS positions, per "
             "contract with total (default: tax year)")
    p_lsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_lsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_lsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_lsum.set_defaults(func=cmd_leaps_sum)

    p_tsum = sub.add_parser(
        "trades-sum",
        help="Trade summary per ticker (buys/sells, value, fees) over a window "
             "(default: tax year)")
    p_tsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_tsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_tsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_tsum.set_defaults(func=cmd_trades_sum)

    p_pos = sub.add_parser(
        "list",
        help="List open positions per account (qty + base-currency book cost) "
             "after ticker.map consolidation and base-currency conversion")
    p_pos.add_argument("account", nargs="?", help="Account (default: all)")
    p_pos.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                       help="Positions AS OF this date — each account's "
                            "books (already ticker.map-consolidated) "
                            "recomputed alone with the engine's --as-of "
                            "cutoff on the project's date basis "
                            "(settlement date unless tax_date = "
                            "\"trade\"); phantoms.json applied; "
                            "per-account ACB (no s.47 blend across "
                            "taxable accounts), before the cross-account "
                            "wash pass")
    p_pos.add_argument("--negative", action="store_true",
                       help="Show only positions with negative quantity "
                            "(short positions — or, in accounts that "
                            "can't short, missed corporate actions / "
                            "import gaps)")
    p_pos.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_pos.set_defaults(func=cmd_positions)

    p_sh = sub.add_parser(
        "shares",
        help="Combined shares held of each symbol across all accounts "
             "(post ticker.map), with a per-account breakdown")
    p_sh.add_argument("--options", action="store_true",
                      help="Include option contracts (excluded by default)")
    g = p_sh.add_mutually_exclusive_group()
    g.add_argument("--taxable", action="store_true",
                   help="Only taxable accounts")
    g.add_argument("--sheltered", action="store_true",
                   help="Only sheltered (registered) accounts")
    p_sh.add_argument("--sort", choices=["symbol", "qty"], default="symbol",
                      help="Order by symbol (default) or by size")
    p_sh.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_sh.set_defaults(func=cmd_shares)

    p_red = sub.add_parser(
        "redact",
        help="Strip the account numbers, names and contact details it "
             "recognises from broker exports (row shapes kept) so a "
             "statement can be shared as a parser sample or bug report "
             "— review the output before sharing")
    p_red.add_argument("files", nargs="+", metavar="FILE")
    p_red.add_argument("--out", metavar="DIR",
                       help="Write redacted copies here (default: beside "
                            "each input as NAME.redacted.EXT)")
    p_red.add_argument("--also", action="append", default=[],
                       metavar="REGEX",
                       help="Extra pattern to replace with REDACTED "
                            "(repeatable)")
    p_red.add_argument("--no-denylist", action="store_true",
                       help="Ignore ~/.config/taxjson/pii-denylist")
    p_red.add_argument("--force", action="store_true",
                       help="Overwrite an existing redacted copy")
    p_red.add_argument("--check", action="store_true",
                       help="Report only; write nothing; exit 1 if "
                            "anything would be redacted")
    p_red.set_defaults(func=cmd_redact)

    p_ob = sub.add_parser(
        "option-boundary",
        help="Written options that straddle a tax-year boundary: where the "
             "premium and any later amount land under ITA s.49, and whether "
             "a filed year needs a T1-ADJ")
    p_ob.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_ob.set_defaults(func=cmd_option_boundary)

    p_edge = sub.add_parser(
        "edge-cases",
        help="Year-boundary and superficial-loss-window edge cases: trades "
             "that settle in another year, options and income around Dec 31, "
             "and acquisitions/sales near day 30 of a loss's window — where "
             "each lands and why")
    p_edge.add_argument("account", nargs="?",
                        help="Account (default: all)")
    p_edge.add_argument("--margin", type=int, default=3, metavar="DAYS",
                        help="How close to day 30 counts as an edge "
                             "(default 3: days 27-33)")
    p_edge.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_edge.set_defaults(func=cmd_edge_cases)

    p_cd = sub.add_parser(
        "check-dates",
        help="Check every trade and settlement date against its market's "
             "calendar (crypto 24/7, futures 23/5, stocks incl. overnight, "
             "options and Canadian listings on exchange days); exit 1 on "
             "an impossible date")
    p_cd.add_argument("account", nargs="?", help="Account (default: all)")
    p_cd.add_argument("--all", action="store_true",
                      help="List every row, not the first 10 per kind")
    p_cd.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_cd.set_defaults(func=cmd_check_dates)

    p_spin = sub.add_parser(
        "spinoffs",
        help="Every spin-off: election, value per share used, income and "
             "new-share cost booked; flags a zero value or missing election")
    p_spin.add_argument("account", nargs="?", help="Account (default: all)")
    p_spin.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_spin.set_defaults(func=cmd_spinoffs)

    p_split = sub.add_parser(
        "splits",
        help="Every split, consolidation and rename with holdings before "
             "and after; flags a split applied twice or a fractional result")
    p_split.add_argument("account", nargs="?", help="Account (default: all)")
    p_split.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_split.set_defaults(func=cmd_splits)

    p_logic = sub.add_parser(
        "tax-logic",
        help="A short statement of every rule taxjson applies for this "
             "project's country, with its settings filled in")
    from taxjson.lib.country import country_arg as _country_arg
    p_logic.add_argument("--country", type=_country_arg,
                         metavar="{canada,ca,usa,us}",
                         help="Country (default: the project's; required "
                              "outside a project)")
    p_logic.add_argument("--ids", action="store_true",
                         help="Prefix each statement with its rule id "
                              "(e.g. [CA-SL-02]), the id tests and code "
                              "cite")
    p_logic.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text (each rule with "
                              "its id)")
    p_logic.set_defaults(func=cmd_tax_logic)

    p_ck = sub.add_parser(
        "checklist",
        help="The filing checklist (docs/filing.md) with each step "
             "auto-detected; --done/--skip/--undo record the steps no "
             "command can prove; --walk steps through the open ones")
    p_ck.add_argument("--walk", action="store_true",
                      help="Interactive: visit each open step in turn")
    p_ck.add_argument("--done", metavar="ID", help="Mark a step done")
    p_ck.add_argument("--skip", metavar="ID", help="Mark a step skipped (n/a for you)")
    p_ck.add_argument("--undo", metavar="ID", help="Remove a manual mark")
    p_ck.add_argument("--note", metavar="TEXT", help="Note to store with --done/--skip")
    p_ck.add_argument("--reset", action="store_true",
                      help="Remove every manual mark (deletes checklist.json)")
    p_ck.add_argument("--only", metavar="ID", help="Check one step")
    p_ck.add_argument("--quick", action="store_true",
                      help="Skip the detectors that run slow sub-commands "
                           "(audit, sanity, ...)")
    p_ck.add_argument("--show", action="store_true",
                      help="With --done/--skip/--undo: also print the checklist")
    p_ck.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_ck.set_defaults(func=cmd_checklist)

    p_san = sub.add_parser(
        "sanity",
        help="Cross-check positions vs external holdings .toml files "
             "(portoml-style): bare items are summed together "
             "(aggregate), ACCOUNT[+ACCOUNT]=FILE[+FILE] items are "
             "checked as their own paired group")
    p_san.add_argument("items", nargs="*",
                       metavar="ACCOUNT|FILE|ACCOUNT[+ACCOUNT]=FILE[+FILE]",
                       help="Bare account names and holdings .toml files "
                            "form one aggregate group (combined positions "
                            "vs combined holdings); ACCOUNT=FILE items "
                            "pair specific accounts with specific files "
                            "— many-to-many with '+', and a repeated "
                            "left-hand side merges (margin=ibkr.toml "
                            "margin=webull.toml). With NO items the "
                            "pairings come from taxjson.toml: each "
                            "account's `holdings = [...]` (paths of its "
                            "broker positions files)")
    p_san.add_argument("--tolerance", type=_nonneg_float_arg, default=1e-4,
                       help="Quantity tolerance (default: 0.0001)")
    p_san.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_san.set_defaults(func=cmd_sanity)

    p_wash = sub.add_parser(
        "wash-radar",
        help="Superficial-loss / wash-sale radar per taxable account "
             "(recomputed live as of today; also written to "
             "reports/wash_radar_<account>.rpt by `taxjson run`)")
    p_wash.add_argument("account", nargs="?",
                        help="Account (default: all taxable)")
    p_wash.add_argument("--date", help="As-of date YYYY-MM-DD (default: today)")
    p_wash.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose radar output")
    p_wash.add_argument("--all", action="store_true",
                        help="Include CLEAR (no-risk) positions too")
    p_wash.add_argument("--json", action="store_true",
                        help="Emit the structured radar document (absolute "
                             "clears_at dates) instead of the text report")
    p_wash.set_defaults(func=cmd_wash_radar)

    p_watch = sub.add_parser(
        "watch",
        help="Report only what CHANGED since the last watch run — "
             "new/changed/cleared radar advisories, moved clear dates "
             "(--harvest adds the harvestable-now total). Quiet when "
             "nothing changed (cron mails only on news); state in "
             "work/.watch_state.json")
    p_watch.add_argument("--harvest", action="store_true",
                         help="Also watch the harvestable-now loss "
                              "total (runs `taxjson harvest --json`; "
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
                              "`taxjson run watch` don't fail on news)")
    p_watch.add_argument("--json", action="store_true",
                         help="Emit the change list as JSON")
    p_watch.set_defaults(func=cmd_watch)

    p_fetch = sub.add_parser(
        "fetch",
        help="Download broker activity straight into inputs/ — "
             "configure `brokerage` + `account`/`query_id` under "
             "[accounts.<name>] (Questrade REST API, IBKR Flex Web "
             "Service). Writes files the existing parsers read; "
             "hand-exported CSVs keep working side by side")
    p_fetch.add_argument("account", nargs="*",
                         help="Accounts to fetch (default: every "
                              "account declaring a `brokerage`)")
    p_fetch.add_argument("--year", type=int, default=None, metavar="N",
                         help="Questrade: backfill a PAST tax year — "
                              "fetch its whole window (Dec 1 of N-1 "
                              "through Jan 31 of N+1) into "
                              "questrade_N.csv. Not combinable with "
                              "--from/--days")
    p_fetch.add_argument("--json", action="store_true",
                         help="Emit a machine-readable summary "
                              "(files, windows, rows added, activity "
                              "types, overlaps); progress moves to "
                              "stderr")
    p_fetch.add_argument("--days", type=int, default=None, metavar="N",
                         help="Questrade: fetch only the last N days "
                              "(default: the whole tax-year window "
                              "Dec 1 of the prior year to Jan 31 "
                              "of the next; "
                              "trailing 90 days when the config has "
                              "no year)")
    p_fetch.add_argument("--from", dest="from_date", default=None,
                         metavar="YYYY-MM-DD",
                         help="Questrade: fetch from this date")
    p_fetch.add_argument("--refresh-token", default=None,
                         help="Questrade refresh token (first run; "
                              "rotations are cached in "
                              "~/.questrade_token, shared machine-"
                              "wide)")
    p_fetch.add_argument("--flex-token", default=None,
                         help="IBKR Flex Web Service token (else "
                              "$IBKR_FLEX_TOKEN)")
    p_fetch.add_argument("--positions", action="store_true",
                         help="Also snapshot LIVE holdings per "
                              "Questrade account into "
                              "work/<account>_live_holdings.toml "
                              "(cross-check with `taxjson sanity`)")
    p_fetch.add_argument("--trim-overlap", action="store_true",
                         help="Trim rows inside the fetched window "
                              "from manually exported Questrade CSVs "
                              "in the same folder (originals kept as "
                              ".bak) — the two sources round "
                              "price/gross differently, so their "
                              "copies of the same trade never dedup")
    p_fetch.add_argument("--dry-run", action="store_true",
                         help="Show what would be written without "
                              "touching inputs/")
    p_fetch.set_defaults(func=cmd_fetch)

    p_canbuy = sub.add_parser(
        "buy-check",
        help="Is buying a ticker TODAY safe from the wash-sale / "
             "superficial-loss rules? UNSAFE when a loss was sold "
             "within the past 30 days (the rebuy cancels it — "
             "permanently if bought in a sheltered account); SAFE* "
             "when buying merely extends an open wash window. "
             "Root-matched (`buy-check NU` covers NU.US and "
             "cross-listings). Exit 1 when any symbol is unsafe")
    p_canbuy.add_argument("symbol", nargs="+",
                          help="Ticker(s) to check (root or full, "
                               "e.g. NU or NU.US)")
    p_canbuy.add_argument("--json", action="store_true",
                          help="Emit verdicts as JSON")
    p_canbuy.set_defaults(func=cmd_buy_check)

    p_sellchk = sub.add_parser(
        "sell-check",
        help="Is selling a ticker AT A LOSS today safe from the "
             "superficial-loss / wash-sale rules? UNSAFE when a "
             "registered account's recent buy it still holds would "
             "deny the loss on the whole position (LOCKED); PARTIAL "
             "when only some of the units are at risk (the line says "
             "how many); ACTION when a rescueable violation is open "
             "(sell the replacement); SAFE*/SAFE otherwise with the "
             "applicable caveats. buy-check's sell-side twin; whether "
             "it IS a loss at today's price is `taxjson harvest`'s "
             "job. Exit 1 on UNSAFE or PARTIAL")
    p_sellchk.add_argument("symbol", nargs="+",
                           help="Ticker(s) to check (root or full, "
                                "e.g. NU or NU.US)")
    p_sellchk.add_argument("--json", action="store_true",
                           help="Emit verdicts as JSON")
    p_sellchk.set_defaults(func=cmd_sell_check)

    p_audit = sub.add_parser(
        "audit",
        help="The authoritative justification of every capital gain: "
             "one block per disposition tracing broker row -> ticker "
             "map -> FX rate -> ACB/FIFO pool -> gain, with the wash/"
             "superficial-loss math shown, every step recomputed and "
             "cross-checked against the books, and a tie-out against "
             "the pipeline's saved gains files. Exit 1 when any check "
             "disagrees")
    p_audit.add_argument("symbol", nargs="*",
                         help="Filter: symbol prefix(es) "
                              "(default: every disposition)")
    p_audit.add_argument("--id", dest="gain_id",
                         help="Filter: transaction id prefix")
    p_audit.add_argument("--date", help="Filter: disposition date "
                                        "(YYYY-MM-DD)")
    p_audit.add_argument("--account", help="Filter: account name "
                                           "(display only — the "
                                           "computation stays blended)")
    # One plausible-year check for every --year (S047-14): 2204 or -5
    # audited nothing and printed an all-checkmark reconciliation.
    p_audit.add_argument("--year", type=_tax_year_arg,
                         help="Tax year (default: taxjson.toml)")
    p_audit.add_argument("--all-years", action="store_true",
                         help="Audit every year on the books")
    p_audit.add_argument("--summary", action="store_true",
                         help="One line per event (find ids to dive "
                              "into)")
    p_audit.add_argument("--no-color", action="store_true",
                         help="Disable ANSI color even on a tty "
                              "(NO_COLOR is honored too)")
    p_audit.add_argument("--no-trace", action="store_true",
                         help="Omit the engine pool traces")
    p_audit.add_argument("--json", action="store_true",
                         help="Emit the full audit as JSON")
    p_audit.set_defaults(func=cmd_audit)

    p_ws = sub.add_parser(
        "wash-sales",
        help="Detail each wash sale (superficial loss) that occurred in the "
             "tax year and the loss denied — what <account>.sum hides in totals")
    p_ws.add_argument("account", nargs="?", help="Account (default: all)")
    p_ws.add_argument("--explain", action="store_true",
                      help="Print the full ACB / superficial-loss calculation "
                           "trace for each wash sale instead of the summary "
                           "table")
    p_ws.add_argument("--json", action="store_true",
                     help="Emit JSON instead of text")
    p_ws.set_defaults(func=cmd_wash_sales)

    p_fxc = sub.add_parser(
        "fx-cash",
        help="FX capital gains on foreign-currency CASH (ITA "
             "s.39(1.1) with the $200 de minimis; §988 ordinary-income "
             "figure for US projects) — a standalone report from the "
             "taxable accounts' native books; changes NO other number. "
             "Set fx_cash_gains = true under [settings] to also print "
             "it at the end of every run")
    p_fxc.add_argument("--events", action="store_true",
                       help="List each in-year disposal event (date, "
                            "units, rate, gain)")
    p_fxc.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_fxc.set_defaults(func=cmd_fx_cash)

    p_t1135 = sub.add_parser(
        "t1135",
        help="CRA T1135 foreign-property helper: cost-based filing-threshold "
             "test plus per-property / per-country tables over all taxable "
             "accounts (put per-symbol domicile overrides in t1135.map)")
    p_t1135.add_argument("--json", action="store_true",
                         help="Emit the report as JSON instead of text")
    p_t1135.set_defaults(func=cmd_t1135)

    p_carry = sub.add_parser(
        "carryover",
        help="Multi-year capital-loss carryforward/carryback ledger "
             "(Canada: running balance + T1A carryback candidates; US: "
             "ST/LT carryover worksheet). Record filed reality in "
             "claimed_losses.txt (YEAR AMOUNT lines)")
    p_carry.add_argument("--claimed", metavar="FILE",
                         help="Losses actually applied on filed returns "
                              "(default: claimed_losses.txt if present)")
    p_carry.add_argument("--json", action="store_true",
                         help="Emit the ledger as JSON instead of text")
    p_carry.set_defaults(func=cmd_carryover)

    p_forms = sub.add_parser(
        "form-export",
        help="Render the year's gains as IRS Form 8949 (code-W wash "
             "adjustments + Schedule D totals) or CRA Schedule 3 rows "
             "(default form follows the project country)")
    p_forms.add_argument("--form",
                         choices=["8949", "schedule3", "txf"],
                         default=None,
                         help="Which form (default: 8949 for usa, "
                              "schedule3 for canada; txf = TurboTax-"
                              "importable file built from the 8949 "
                              "rows, US projects only)")
    p_forms.add_argument("--box", default=None, choices=["A", "B", "C"],
                         help="txf only: 8949 checkbox pairing (A/D "
                              "basis-reported, the default; B/E, C/F)")
    p_forms.add_argument("--out", metavar="FILE", default=None,
                         help="txf only: write the .txf here instead "
                              "of stdout")
    p_forms.add_argument("--csv", metavar="FILE",
                         help="Also write the rows as CSV")
    p_forms.add_argument("--json", action="store_true",
                         help="Emit the report as JSON instead of text")
    p_forms.set_defaults(func=cmd_form_export)

    p_harv = sub.add_parser(
        "harvest",
        help="Unrealized gain/(loss) per open position at current prices "
             "— tax-loss-harvest view, losses first, with the wash "
             "radar's advisory (IBKR -> yfinance -> cache)")
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

    p_rec = sub.add_parser(
        "reconcile-slips",
        help="Diff broker T5008 / 1099-B slip CSVs against computed "
             "dispositions (exit 1 on any mismatch)")
    p_rec.add_argument("slip_csv", nargs="+",
                       help="Slip CSV(s) — headers matched loosely "
                       "(symbol/ticker, quantity/box 16, proceeds/box 21, "
                       "cost/box 20); several (one per broker) are "
                       "reconciled together")
    p_rec.add_argument("--tolerance", type=_nonneg_float_arg, default=None,
                       help="Absolute per-symbol tolerance, a number >= 0 "
                            "(default 1.00)")
    p_rec.add_argument("--json", action="store_true",
                       help="Emit the reconciliation as JSON instead of "
                            "text")
    p_rec.set_defaults(func=cmd_reconcile_slips)

    p_close = sub.add_parser(
        "close-year",
        help="Snapshot the current tax year's filing aggregates to "
             "filed/<year>.json — the filed-year lock that "
             "check-filed (and every full run) guards")
    p_close.add_argument("--year", type=_tax_year_arg, default=None,
                         help="Must match [settings].year (guard)")
    p_close.add_argument("--force", action="store_true",
                         help="Replace an existing lock (re-filed/"
                              "amended years only)")
    p_close.add_argument("--filed-dispositions", metavar="CSV",
                         help="The dispositions the return actually "
                              "reported, when it was prepared with another "
                              "tool (CSV: symbol,date,qty,proceeds,cost,gain"
                              "[,account]); the next year's `taxjson "
                              "handoff` checks doubles against these")
    p_close.set_defaults(func=cmd_close_year)

    p_hand = sub.add_parser(
        "handoff",
        help="Check this project against the previous year's close-year "
             "record: opening positions and cost, trades settling across "
             "Dec 31, sales reported in both years (exit 1 on a problem)")
    p_hand.add_argument("--prior", metavar="PATH",
                        help="The previous year's filed/<year>.json "
                             "(default: [settings] prior_year_record, else "
                             "filed/<year-1>.json here)")
    p_hand.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_hand.set_defaults(func=cmd_handoff)

    p_chk = sub.add_parser(
        "check-filed",
        help="Recompute every filed year from the current books and "
             "report drift vs the locks (exit 1 on drift)")
    p_chk.set_defaults(func=cmd_check_filed)

    p_fmh = sub.add_parser(
        "find-missing-history",
        help="Find positions with missing cost basis (truncated buy history "
             "or $0-basis corp actions) that distort a year's gain")
    p_fmh.add_argument("account", nargs="?", help="Account (default: all)")
    p_fmh.add_argument("--year", type=_tax_year_arg,
                       help="Tax year to scope relevance (default: config year)")
    p_fmh.add_argument("--include-options", action="store_true",
                       help="Also check option positions")
    p_fmh.add_argument("--gen-phantoms", metavar="FILE",
                       help="Instead of the report, emit a phantom "
                            "opening-balance file (for the truncated-history "
                            "rows) to FILE for review. Save it as "
                            "phantoms.json at the project root and `taxjson "
                            "run` auto-applies it via --incomplete-history")
    p_fmh.add_argument("--all-history", action="store_true",
                       help="With --gen-phantoms, emit every candidate, not "
                            "just those affecting the tax year")
    p_fmh.set_defaults(func=cmd_find_missing_history)

    p_fees = sub.add_parser(
        "fees",
        help="Fees incurred over a window (default: tax year), one row per "
             "fee-bearing trade + a per-currency total")
    p_fees.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_fees.add_argument("account", nargs="?", help="Account (default: all)")
    p_fees.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_fees.set_defaults(func=cmd_fees)

    p_fsum = sub.add_parser(
        "fees-sum",
        help="Trading-fee report by brokerage (base currency) over a window "
             "(default: tax year; trade dates); the totals `taxjson run` "
             "writes to reports/fees.rpt, by account by default")
    p_fsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_fsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_fsum.add_argument("--by-account", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Break fees down by account (default; use "
                             "--no-by-account for brokerage totals only)")
    p_fsum.add_argument("--json", action="store_true",
                        help="Machine-readable JSON instead of the report")
    p_fsum.set_defaults(func=cmd_fees_sum)

    p_serve = sub.add_parser(
        "serve", help="Launch the local web UI (needs the [web] extra)")
    # No --dir here: inherit the global -C/--dir like every other subcommand.
    # (A subparser --dir would silently clobber -C via the shared dest.)
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="Bind host (default: 127.0.0.1, local-only)")
    p_serve.add_argument("--port", type=int, default=8765,
                         help="Bind port, 1-65535 (default: %(default)s)")
    p_serve.add_argument("--token", action="store_true",
                         help="Require the per-run access token on a "
                              "loopback bind too (127.0.0.1 is reachable by "
                              "every account on this machine; any other "
                              "bind always requires it)")
    p_serve.set_defaults(func=cmd_serve)

    p_help = sub.add_parser(
        "help", help="Show top-level help, or help for one COMMAND")
    p_help.add_argument("topic", nargs="?", help="Subcommand to explain")

    def _help(a: argparse.Namespace) -> None:
        if a.topic and a.topic in sub.choices:
            sub.choices[a.topic].print_help()
        elif a.topic:
            print(f"taxjson: no such command {a.topic!r}\n", file=sys.stderr)
            p.print_help()
            raise SystemExit(2)
        else:
            p.print_help()
    p_help.set_defaults(func=_help)

    # Synopsis in every subcommand's own -h: argparse prints only a
    # parser's `description` there, and add_parser does NOT inherit it
    # from the `help` one-liner shown in the top-level listing — so
    # `taxjson run -h` listed options with no statement of what the
    # command does. Reuse the curated help text as the description.
    # (_choices_actions is argparse's stable-in-practice registry of
    # the per-command help strings; there is no public accessor.)
    for _act in getattr(sub, "_choices_actions", []):
        _sp = sub.choices.get(_act.dest)
        if _sp is None:
            continue
        if _sp.description is None and _act.help:
            # argparse %-expands `help=` but NOT `description=`, so a
            # curated "50%%" leaked as a literal double percent.
            _sp.description = _act.help.replace("%%", "%")
        # Same width cap as the top-level parser — add_parser doesn't
        # inherit formatter_class, and 40 call-site edits would drift.
        _sp.formatter_class = _CappedHelpFormatter

    argv = sys.argv[1:]
    commands = set(sub.choices)
    segments = _split_command_segments(p, argv, commands)
    if len(segments) > 1:
        # Validate the WHOLE chain before executing ANY of it — a
        # bad later segment (`taxjson events -- 30d`: '30d' is not a
        # command) previously half-ran the chain, executing `events`
        # and then dying rc 2.
        for seg in segments:
            first = next((t for t in seg if t in commands), None)
            if first is None or not _parses_ok(p, seg):
                bad = " ".join(seg)
                _die(f"{bad!r} is not a valid command in "
                         f"this chain — nothing was executed. (A "
                         f"literal `--` separates chained commands; "
                         f"the token after it must start a command.)")
        # Self-diagnosing ambiguity note: a boundary token that the
        # PREVIOUS segment could also have consumed as a positional.
        for a, b in zip(segments, segments[1:]):
            boundary = next((t for t in b if t in commands), None)
            if boundary and _parses_ok(p, a + [boundary]):
                print(f"taxjson: note: {boundary!r} starts a new "
                      f"chained command; the previous command "
                      f"({next(t for t in a if t in commands)!r}) "
                      f"could also have taken it as an argument — "
                      f"run the commands separately if that was the "
                      f"intent.", file=sys.stderr)
    global _CURRENT_CMD
    from taxjson.lib.corp_actions import ManifestError
    for seg in segments:
        args = p.parse_args(seg)
        _CURRENT_CMD = next((t for t in seg if t in commands), "")
        if (args.cmd not in ("init", "help", "redact")
                and not Path(args.dir).is_dir()):
            # `-C typo sum` said "no gains files in typo/work (run
            # `taxjson run` first)" — send the user to the real problem.
            _die(f"no such directory: {args.dir} (-C/--dir names the "
                 f"project root — the folder holding taxjson.toml)")
        _enforce_command_country(args)
        try:
            args.func(args)
        except SystemExit as e:
            # A failing command stops the chain and propagates its
            # code; an explicit success (sys.exit(0) / None) lets the
            # next command run.
            if e.code not in (None, 0):
                raise
        except ManifestError as e:
            # A hand-edited elections manifest that does not load: one
            # line naming the file, never a traceback (audit S072-05).
            sys.exit(f"taxjson {args.cmd}: error: {e}")
        except subprocess.CalledProcessError as e:
            # A pipeline stage failed. The child's own stderr already
            # explained WHY (run_to_file echoes it) — re-raising the
            # CalledProcessError just buried that explanation under a
            # second traceback (2026-09 audit).
            sys.exit(f"taxjson: stage failed: "
                     f"{' '.join(str(c) for c in (e.cmd or [])[-3:])} "
                     f"(exit {e.returncode}) — see the error above.")
    return


def _enforce_command_country(args: argparse.Namespace) -> None:
    """Refuse a command (or command:variant) the project's country does
    not own — lib/country.COMMAND_COUNTRY — before it runs, once, for
    every entry point (partition audit R3: t1135, option-boundary and
    form-export --form schedule3 ran in US projects and gave Canadian
    advice; 8949/txf in a Canada project failed only by accident)."""
    from taxjson.lib.country import (command_country, command_country_problem,
                                     flag_country_problems, given_flags)
    cmd = getattr(args, "cmd", "") or ""
    variant = getattr(args, "form", None) if cmd == "form-export" else None
    flags = {f: v for f, v in given_flags(args).items()
             if v not in (None, False, "")}
    if command_country(cmd, variant) is None and not flags:
        return
    settings = _soft_settings(Path(args.dir).resolve())
    if not settings:
        _die("no taxjson.toml here — this command needs a project "
             "(its country decides whether it applies).")
    country = _country(settings)
    msg = command_country_problem(cmd, country, variant)
    if msg:
        _die(msg)
    # `estimate --province XX` in a US project was silently ignored
    # (partition COMMANDS-10 / SPEC-09): FLAG_COUNTRY, like the config.
    problems = flag_country_problems(country, flags)
    if problems:
        _die("; ".join(problems))


def _parses_ok(parser: argparse.ArgumentParser, seg: List[str]) -> bool:
    """Would argparse accept `seg` as one complete command? Trial parse
    with stderr suppressed — argparse raises SystemExit on rejection."""
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            # stdout too: argparse prints --help THERE, and a trial
            # parse hitting -h leaked the full help block into the
            # real output stream.
            parser.parse_args(seg)
        return True
    except SystemExit:
        return False


def _split_command_segments(parser: argparse.ArgumentParser,
                            argv: List[str],
                            commands: set) -> List[List[str]]:
    """Split one argv into per-command segments so `taxjson run sum`
    (or `taxjson run --fast sum --json`) executes the commands in
    order.

    Everything before the FIRST command token is a global prefix
    (`-C DIR`), re-applied to every segment. After that, a token
    matching a command name is a boundary only if the segment built so
    far is itself a COMPLETE valid command (trial-parsed) — so
    `run --fast | sum` splits (run --fast parses) while
    `run --account sum` does not (`--account` still needs its value)
    and `show sum` keeps `sum` as the account name. A literal `--`
    forces a boundary for the rare ambiguous spelling.
    """
    prefix: List[str] = []
    i = 0
    while i < len(argv) and argv[i] not in commands:
        prefix.append(argv[i])
        i += 1
    if i == len(argv):                  # no command at all — let
        return [argv]                   # argparse print its usual error
    segments: List[List[str]] = []
    cur: List[str] = [argv[i]]
    i += 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--" and cur and _parses_ok(parser, prefix + cur):
            segments.append(cur)
            cur = []
        elif (tok in commands
                and cur                 # empty right after a `--`
                and cur[0] != "help"    # `taxjson help divs` — help's
                                        # argument IS a command name
                and _parses_ok(parser, prefix + cur)):
            segments.append(cur)
            cur = [tok]
        else:
            cur.append(tok)
        i += 1
    if cur:
        segments.append(cur)
    return [prefix + s for s in segments if s]


if __name__ == "__main__":
    main()
