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
    taxjson show margin               # print reports/margin.sum
    taxjson init [DIR]                # scaffold a new project directory
"""

import argparse
import re
import shutil
import subprocess
import sys
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.cli_diag import note
from taxjson.lib.pipeline import option_timing_flags
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
_PARSE_COUNT_RE = re.compile(r'^\s+\S+: \d+ tax objects$')


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


_DIAG_MARKER_RE = re.compile(
    # `warning: …` (parser-layer, bare) or GNU-style `<prog>: warning: …`
    # (bin CLIs per AUDIT-2026-07-ui §1C1, e.g. `taxjson-fill-crypto: warning:`).
    # `validation:` — merge2 --validate's report header ("validation: N
    # error(s) …" + indented TX lines); FUZZ #M found hard ERRORs living
    # only in the raw .diag because this regex dropped the header.
    r"^(?:[\w./-]+:\s+)?(?:ok|warning|note|error|validation):",
    re.IGNORECASE)


def collect_diagnostics(cache: Path, account: str) -> str:
    """Concatenate persisted stderr diagnostics across an account's
    pipeline stages, keeping ok/warning/note/error lines — bare or
    prog-prefixed (`taxjson-fill-crypto: warning: …`) — plus their
    indented continuation lines."""
    out: List[str] = []
    # Sibling-prefix guard (mirrors cmd_fees_sum): the glob for account
    # 'margin' also matches 'margin_us_*.diag', so account margin's
    # DIAGNOSTICS banner absorbed the other account's errors and sent
    # the user auditing the wrong book. cache is always <root>/work, so
    # the config is one level up; on any load problem fall back to the
    # unfiltered glob (a false extra line beats a crash here).
    siblings: List[str] = []
    try:
        accounts_cfg = (load_config(cache.parent).get("accounts") or {})
        siblings = [a for a in accounts_cfg
                    if a != account and a.startswith(f"{account}_")]
    except (Exception, SystemExit):
        pass
    for diag in sorted(cache.glob(f"{account}_*.diag")):
        if any(diag.name.startswith(f"{s}_") for s in siblings):
            continue
        kept_prev = False
        for line in diag.read_text(errors="replace").splitlines():
            is_marker = bool(_DIAG_MARKER_RE.match(line.strip()))
            is_cont = kept_prev and line[:1].isspace() and bool(line.strip())
            if is_marker or is_cont:
                out.append(line.rstrip())
                kept_prev = True
            else:
                kept_prev = False
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
    with path.open("rb") as f:
        try:
            cfg = tomllib.load(f)
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
            return cfg
        except Exception as e:
            _die(f"{path} is not valid TOML: {e}")


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


_ESTIMATE_KEYS = ("other_income", "other_losses")


def _estimate_inputs(root: Path, args) -> Tuple[float, float]:
    """(other_income, other_losses) for the tax estimate: CLI flags
    win, else the [estimate] table, else zero. Shared by cmd_summary
    and cmd_instalments — `taxjson instalments` shells out to the
    estimate, and without a common resolution the two commands
    reported different net tax owing for anyone with employment
    income (5.5x on the auditor's fixture)."""
    cfg = _soft_config(root).get("estimate") or {}
    oi = getattr(args, "other_income", None)
    ol = getattr(args, "other_losses", None)
    if oi is None:
        oi = cfg.get("other_income")
    if ol is None:
        ol = cfg.get("other_losses")
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


_SETTINGS_KEYS = ("year", "country", "base_currency", "tax_date",
                  "source_currencies", "cross_asset", "province",
                  "fx_cash_gains", "option_premium_timing",
                  "option_grant_timing_since", "option_buyback_loss_superficial")
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
    country = settings.get("country")
    if country is not None and _normalize_country(str(country)) not in _COUNTRY_CANON.values():
        _die(f"[settings] country must be canada|ca|usa|us, "
                 f"got {country!r}")
    tax_date = settings.get("tax_date")
    if tax_date is not None and tax_date not in ("settle", "trade"):
        _die(f"[settings] tax_date must be settle|trade, "
                 f"got {tax_date!r}")
    _opt = settings.get("option_premium_timing")
    if _opt is not None and str(_opt).strip().lower() not in ("grant", "close"):
        _die(f"[settings] option_premium_timing must be \"grant\" or "
             f"\"close\" (got {_opt!r}).")
    _since = settings.get("option_grant_timing_since")
    if _since is not None and not (isinstance(_since, int)
                                   and not isinstance(_since, bool)
                                   and 1990 <= _since <= 2100):
        _die(f"[settings] option_grant_timing_since must be a tax year "
             f"(got {_since!r}).")
    _bb = settings.get("option_buyback_loss_superficial")
    if _bb is not None and not isinstance(_bb, bool):
        _die(f"[settings] option_buyback_loss_superficial must be true/false (got {_bb!r}).")
    ca_flag = settings.get("cross_asset")
    if ca_flag is not None and not isinstance(ca_flag, bool):
        _die(f"[settings] cross_asset must be true/false, "
                 f"got {ca_flag!r}")
    src = settings.get("source_currencies")
    if src is not None and (not isinstance(src, list)
                            or not all(isinstance(c, str) for c in src)):
        _die(f"[settings] source_currencies must be a list of "
                 f"currency codes, got {src!r}")

    for table, allowed in (("instalments",
                           ("basis", "prior_year_net_tax",
                            "second_prior_net_tax", "withheld",
                            "prescribed_rate", "prescribed_rates",
                            "paid")),
                          ("estimate", _ESTIMATE_KEYS)):
        for key in (cfg.get(table) or {}):
            if key not in allowed:
                warnings.append(f"unknown [{table}] key {key!r} is "
                                f"ignored{_suggest(key, allowed)}")
    accounts = cfg.get("accounts", {})
    for name, acfg in accounts.items():
        if not isinstance(acfg, dict):
            _die(f"[accounts.{name}] must be a table")
        for key in acfg:
            if key not in _ACCOUNT_KEYS:
                warnings.append(f"unknown [accounts.{name}] key {key!r} is "
                                f"ignored{_suggest(key, _ACCOUNT_KEYS)}")
        atype = acfg.get("type")
        if atype is None:
            warnings.append(f"[accounts.{name}] has no `type` — defaulting "
                            f"to \"sheltered\" (set type = \"taxable\" or "
                            f"\"sheltered\" explicitly)")
        elif atype not in _ACCOUNT_TYPES:
            _die(f"[accounts.{name}] type must be "
                     f"\"taxable\" or \"sheltered\", got {atype!r} — this "
                     f"account would otherwise be silently dropped from "
                     f"the run{_suggest(str(atype), _ACCOUNT_TYPES)}")
        for flag in ("crypto", "transfers"):
            if flag in acfg and not isinstance(acfg[flag], bool):
                _die(f"[accounts.{name}] {flag} must be "
                         f"true/false, got {acfg[flag]!r}")
        brok = acfg.get("brokerage")
        if brok is not None and str(brok) not in ("questrade",
                                                  "ibkr_flex"):
            warnings.append(
                f"[accounts.{name}] brokerage {brok!r} is not a fetch "
                f"source (questrade | ibkr_flex) — `taxjson fetch` "
                f"will refuse it"
                f"{_suggest(str(brok), ('questrade', 'ibkr_flex'))}")

    # Inputs dir with data but no [accounts.*] entry: today that folder is
    # silently ignored — the inverse of the configured-but-unpopulated
    # warning the run loop already prints.
    if inputs_dir is not None and inputs_dir.is_dir():
        for sub in sorted(inputs_dir.iterdir()):
            if not sub.is_dir() or sub.name in accounts:
                continue
            if input_files(sub, ".csv") or input_files(sub, ".tt"):
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
_COUNTRY_CANON = {"ca": "canada", "canada": "canada", "us": "usa", "usa": "usa"}


_US_EXPERIMENTAL_NOTE = (
    "NOTE: the US engine is EXPERIMENTAL — its rules are implemented and "
    "unit-tested but have not been validated against a real account. "
    "Treat the output as a draft, and consider contributing a redacted "
    "export (`taxjson redact`) so it can be.")


def _normalize_country(c: str) -> str:
    key = (c or "").strip().lower()
    return _COUNTRY_CANON.get(key, key)


def _country_has_corp_rules(country: str) -> bool:
    """Whether taxjson-corp-actions has election rules for this jurisdiction.
    Only Canada today; the corp-actions stage is skipped otherwise (running it
    would just fail argparse / have no rules to apply)."""
    from taxjson.lib.corp_actions import RULES_BY_COUNTRY
    return _normalize_country(country) in RULES_BY_COUNTRY


def detect_broker(csv_path: Path) -> Optional[str]:
    """Filename hint first (covers coinbase/kraken whose CSV shapes aren't
    distinctive enough for content detection), then content detection
    (ib/rbc_direct/questrade/webull). The hint wins because Kraken ledgers
    look superficially like an rbc_direct activity export."""
    lower = csv_path.name.lower()
    for hint, broker in _FILENAME_HINTS:
        # Underscore-style hints (cb_, kr_) match only at the START of
        # the filename — the documented convention ("rename to start
        # with cb_/kr_"). A substring match routed ibkr_statement.csv
        # (contains "kr_") to the Kraken parser, which emitted 0
        # transactions while the run exited 0 (REVIEW-2026-07-ui #3).
        # Word hints (coinbase, kraken) stay substring matches.
        if hint.endswith("_"):
            if lower.startswith(hint):
                return broker
        elif hint in lower:
            return broker
    from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
    return detect_brokerage(csv_path)


def input_files(dirpath: Path, suffix: str) -> List[Path]:
    """Input files by suffix, CASE-INSENSITIVE. Excel and several
    brokers export TRADES.CSV / START.TT; glob("*.csv") silently
    ignored them while the GUI import accepted them — a run that
    exits 0 with those trades missing (REVIEW-2026-07-ui #1)."""
    if not dirpath.is_dir():
        return []
    return sorted(p for p in dirpath.iterdir()
                  if p.is_file() and p.suffix.lower() == suffix)


def group_inputs(account_dir: Path) -> Dict[str, List[Path]]:
    out: Dict[str, List[Path]] = {}
    for csv in input_files(account_dir, ".csv"):
        broker = detect_broker(csv)
        if not broker:
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
        if t.get("action") not in ("BUYSELL", "ASSIGN", "TRANSFER",
                                   "SPLIT"):
            continue
        c, sym = t.get("currency"), t.get("symbol")
        if c and sym:
            curs.setdefault(_final(sym), set()).add(c)
    return sorted(sym for sym, cs in curs.items() if len(cs) > 1)


# ---------------------------------------------------------------- stages

def _rates_coverage_stale(rates_path: Path, today: Optional[date_cls] = None) -> bool:
    """Whether to_base.csv's DATA has aged out. Freshness is a property of
    the data, not the file's mtime: coverage ends at the generation date,
    so on a stable install (no package/config mtime bumps to invalidate
    the cache) the file would otherwise be reused forever and every trade
    after its last row would silently convert at the --default-rate.
    Stale when the newest rate row is more than 3 days old (tolerates
    weekends/short holidays without refetching daily)."""
    today = today or date_cls.today()
    try:
        last_line = ""
        with rates_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return True                      # sources configured, no rows
        last_date = datetime.strptime(last_line.split()[0], "%Y-%m-%d").date()
    except (OSError, ValueError, IndexError):
        return True                          # unreadable/malformed: refetch
    return (today - last_date).days > 3


def stage_currency_rates(settings: Dict[str, Any], cache: Path) -> Path:
    """Build to_base.csv by appending taxjson-to-base-curr output for each
    configured source currency. Caches across runs."""
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
    if not needs_rebuild(rates_path) and not _rates_coverage_stale(rates_path):
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
    tax_date = settings.get("tax_date") or (
        "trade" if _normalize_country(settings.get("country", "canada"))
        in ("us", "usa") else "settle")
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
        sys.exit(
            f"taxjson: account '{name}' has {flag} in taxjson.toml but "
            f"its inputs contain {', '.join(sorted(_mismatched))} files "
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
        + _map_entries)) + "\n"
    if (not src_manifest.exists()
            or src_manifest.read_text(encoding="utf-8") != src_txt):
        src_manifest.write_text(src_txt, encoding="utf-8")

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
            cmd = _cmd("taxjson-brokerage") + ["--account", name,
                                               "--brokerage", broker]
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
                cmd += ["--security-overrides", str(security_overrides)]
            cmd += [str(p) for p in csvs]
            run_to_file(cmd, out)
            # Surface per-file transaction counts (and any 0-tx
            # warnings) inline so the user can sanity-check at a
            # glance that each CSV contributed the expected number
            # of rows.
            echo_parse_stats(out)
        parsed.append(out)

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
            if force or needs_rebuild(out, *csvs, manifest_path,
                                      src_manifest):
                print(f"  corp-actions {broker}")
                cmd = _cmd("taxjson-corp-actions") + [
                    "--account-name", name,
                    "--country", country,
                    "--brokerage", broker,
                    "--manifest", str(manifest_path),
                ]
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
            corp_files.append(out)

    # 3. starting-position .tt files
    tt_jsons: List[Path] = []
    for tt in input_files(acct_dir, ".tt"):
        out = cache / f"{name}_{tt.stem}.json"
        if force or needs_rebuild(out, tt):
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
        | {f"{name}_{tt.stem}" for tt in input_files(acct_dir, ".tt")}
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
        if force or needs_rebuild(filled, mapped):
            print("  fill-crypto-prices")
            run_to_file(_cmd("taxjson-fill-crypto") + [str(mapped)], filled)
        if force or needs_rebuild(base_json, filled, rates):
            print(f"  convert-currency → {base_currency}")
            run_to_file(_cmd("taxjson-convert-currency") + [
                str(filled), "--to", base_currency, "--rates", str(rates),
            ], base_json)
        # The crypto path has no merge2 stage, so it never validates its
        # output. Run taxjson-validate explicitly and persist the report
        # as a .diag so crypto.sum carries an OK:/error line like the
        # merge2-validated equity accounts.
        validate_diag = cache / f"{name}_validate.diag"
        from taxjson.lib.dispatch import run_cmd as _run_cmd
        vres = _run_cmd(_cmd("taxjson-validate") + [str(base_json)],
                        capture_output=True)
        report = (vres.stdout or "") + (vres.stderr or "")
        if report.strip():
            validate_diag.write_text(report, encoding="utf-8")
        elif validate_diag.exists():
            validate_diag.unlink()
        # Quiet on success (the report is in the .diag → .sum); only
        # surface to the console if validation actually failed.
        if vres.returncode != 0:
            sys.stderr.buffer.write(report)
            if strict:
                sys.exit(f"taxjson run --strict: {name}: validation "
                         f"ERROR(s) in the crypto books — aborting.")
    else:
        cmd = _cmd("taxjson-merge2") + [
            "--sort", "--dedup", "--require-inputs",
            "--to", base_currency, "--rates", str(rates), "--validate",
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
            + ([dist_map] if _apply_dists else [])
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
                            # i.e. the SETTLED position under CRA
                            # timing; the project's tax_date decides.
                            "--date-basis", tax_date],
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
    # Warn-only option-as-replacement scan (numbers never change).
    if is_taxable and settings.get("cross_asset"):
        cmd.append("--cross-asset")
    cmd += option_timing_flags(settings)
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
        raw_deps = list(sources) + ([ticker_map] if ticker_map else [])
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
        else:
            raw_gains = cache / f"{name}_raw_gains.json"
            if force or needs_rebuild(raw_gains, raw_json):
                print("  raw gains")
                # Match the country to the rest of the pipeline. Without
                # this, taxjson-gains defaults to Canada and the US
                # raw-holdings inventory would aggregate FIFO lots as a
                # Canadian-style ACB blended pool — wrong total_cost per
                # symbol on the holdings.toml handoff.
                run_to_file(_cmd("taxjson-gains") + [
                    "--country", country,
                ] + option_timing_flags(settings) + [str(raw_json)],
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
                ], raw_base_json)
            raw_base_gains = cache / f"{name}_raw_base_gains.json"
            if force or needs_rebuild(raw_base_gains, raw_base_json):
                print("  raw base gains")
                run_to_file(_cmd("taxjson-gains") + [
                    "--country", country, str(raw_base_json),
                ], raw_base_gains, capture_diag=False)
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
    # failing sub-report can't leave a truncated .sum that `taxjson show`
    # then serves (same atomicity contract as run_to_file).
    sum_path = reports_dir / f"{name}.sum"
    sum_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"  → {sum_path}")
    sum_tmp = sum_path.with_name(sum_path.name + ".part")
    try:
        with sum_tmp.open("wb") as out:
            out.write(_diagnostics_banner(cache, name))
            out.write(run_capture(_cmd("taxjson-sum-gains") + [str(gains_json)]))
            out.write(run_capture(_cmd("taxjson-sum-income") + [
                "--year", str(year), str(base_json),
            ]))
            if not is_crypto:
                # The portfolio-snapshot report is equity-specific; crypto skips it.
                out.write(run_capture(_cmd("taxjson-export") + [
                    "--report", "--futures", str(gains_json),
                ]))
        sum_tmp.replace(sum_path)
    finally:
        sum_tmp.unlink(missing_ok=True)

    # Machine twin of the text reports (ADDITIVE — the .sum bytes above are
    # untouched): the structured aggregates `taxjson summary` and future
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
                base_transactions=_base_rows)))
    except Exception as e:                      # advisory artifact only
        print(f"taxjson: warning: could not write {name}_report.json: {e}",
              file=sys.stderr)

    return {"base": base_json, "gains": gains_json, "sum": sum_path}


def _json_dumps_report(payload) -> str:
    import json as _json
    return _json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _diagnostics_banner(cache: Path, account: str) -> bytes:
    """A DIAGNOSTICS section for the top of a .sum file, or empty bytes
    when the account's pipeline stages produced no warnings/notes."""
    diag = collect_diagnostics(cache, account)
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
        "--tax-date", settings.get("tax_date") or (
            "trade" if _normalize_country(settings.get("country", "canada"))
            in ("us", "usa") else "settle"),
        "--sheltered", str(sheltered_base),
        "--full-traces", str(wash_traces),
    ]
    if settings.get("cross_asset"):
        cmd.append("--cross-asset")
    cmd += option_timing_flags(settings)
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
            out.write(run_capture(_cmd("taxjson-sum-gains") + [str(wash_gains)]))
            out.write(run_capture(_cmd("taxjson-sum-income") + [
                "--year", str(settings["year"]), str(base_json),
            ]))
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
                base_transactions=_base_rows)))
    except Exception as e:                      # advisory artifact only
        print(f"taxjson: warning: could not write {name}_report.json: {e}",
              file=sys.stderr)
    print(f"  → {wash_sum}")


def stage_blended_wash_pass(names: List[str],
                            settings: Dict[str, Any], cache: Path,
                            reports_dir: Path,
                            sheltered_base: Optional[Path],
                            incomplete_history: Optional[Path] = None
                            ) -> None:
    """ONE combined gains run over every taxable equity account, split
    back into the per-account `<name>_gains_wash.json` artifacts.

    This is what makes the canonical (filed-from) numbers correct for
    multi-account books: Canada's ACB blends across all non-registered
    accounts (ITA s.47 — the engine's symbol-global pools do this
    naturally on a combined input) and US §1091 wash matching spans
    accounts while FIFO basis stays per account (--per-account-basis).
    Single-account projects produce identical numbers by construction.
    The per-account `<name>.sum` stays the isolated pre-blend baseline —
    comparing the pair shows exactly what blending changed."""
    print(f"==> blended taxable pass ({', '.join(names)})")
    # Dot-prefixed intermediates: pathlib globs DO match leading dots
    # (`*_base.json` matches `.blend_base.json`), so every discovery
    # site — resolve_gains_files and the radar/missing-history fallback
    # globs — must ALSO filter `startswith(".")` explicitly. A visible
    # name here (or a glob site without the dot filter) would be
    # discovered as a phantom account and every aggregate would
    # double-count.
    combined_base = cache / ".blend_base.json"
    run_to_file(_cmd("taxjson-merge") + [
        str(cache / f"{n}_base.json") for n in names], combined_base)
    combined_wash = cache / ".blend_gains_wash.json"
    wash_traces = cache / ".blend_gains_wash.traces"
    country = _normalize_country(settings["country"])
    cmd = _cmd("taxjson-gains") + [
        "--taxable",
        "--country", country,
        "--year", str(settings["year"]),
        "--tax-date", settings.get("tax_date") or (
            "trade" if _normalize_country(settings.get("country", "canada"))
            in ("us", "usa") else "settle"),
        "--full-traces", str(wash_traces),
    ]
    if _normalize_country(country) in ("us", "usa"):
        cmd.append("--per-account-basis")
    if sheltered_base is not None:
        cmd += ["--sheltered", str(sheltered_base)]
    if settings.get("cross_asset"):
        cmd.append("--cross-asset")
    cmd += option_timing_flags(settings)
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
        _mirror = cache / f"{name}_blend.diag"
        if _blend_diag.exists() and _blend_diag.stat().st_size:
            _mirror.write_text(_blend_diag.read_text(errors="replace"),
                               encoding="utf-8")
        else:
            _mirror.unlink(missing_ok=True)
    for name in names:
        wash_gains = cache / f"{name}_gains_wash.json"
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
        blended_inv = {r.get("symbol"): float(r.get("qty") or 0.0)
                       for r in (_json.loads(combined_wash.read_text(
                           encoding="utf-8")).get("inventory") or [])
                       if not r.get("account")}
        split_sums: Dict[str, float] = {}
        for name in names:
            for r in (_json.loads(
                    (cache / f"{name}_gains_wash.json").read_text(
                        encoding="utf-8")).get("inventory") or []):
                if r.get("blended_pool"):
                    split_sums[r.get("symbol")] = (
                        split_sums.get(r.get("symbol"), 0.0)
                        + float(r.get("qty") or 0.0))
        for sym, total in sorted(blended_inv.items()):
            got = split_sums.get(sym, 0.0)
            if abs(total - got) > 1e-4:
                print(f"taxjson: warning: blended {sym} holds "
                      f"{total:g} but the per-account split accounts "
                      f"for only {got:g} — the difference is likely "
                      f"phantom (phantoms.json) shares, which the "
                      f"split cannot attribute to an account. "
                      f"Per-account holdings under-report by the gap.",
                      file=sys.stderr)
    except (OSError, ValueError):
        pass


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
    entirely silent before; the run now names the overlapping symbols
    and the documented workaround. Detection only — the blended
    computation itself is the deferred fix."""
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
    country = _normalize_country(settings.get("country", "canada"))
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
                        ticker_map: Optional[Path] = None) -> None:
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
            stem = tb.stem.replace("_base", "")
            # --json-out: structured sidecar next to the .rpt; the web UI
            # reads it and computes countdowns at view time.
            run_to_file(_cmd("taxjson-wash-radar") + [
                "--taxable", str(tb),
                "--json-out", str(reports_dir / f"wash_radar_{stem}.json"),
                "--account", stem,
            ] + sheltered_arg, reports_dir / f"wash_radar_{stem}.rpt",
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
            ] + sheltered_arg, reports_dir / "wash_radar_COMBINED.rpt",
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
    run_to_file(_cmd("taxjson-fees") + [
        "--cache", str(cache),
        "--year", str(settings["year"]),
        "--to", settings["base_currency"], "--rates", str(rates),
    ], reports_dir / "fees.rpt")
    print(f"  → {reports_dir}/fees.rpt")


# ---------------------------------------------------------------- entry points

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
    if _normalize_country(str(settings.get("country", "canada"))) == "usa":
        print(_US_EXPERIMENTAL_NOTE, file=sys.stderr)

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
    for required in ("year", "country", "base_currency"):
        if required not in settings:
            _die(f"missing [settings] {required} in taxjson.toml")

    for msg in validate_config(cfg, root / "inputs"):
        print(f"taxjson: warning: taxjson.toml: {msg}", file=sys.stderr)

    inputs_dir = root / "inputs"
    cache = root / "work"
    reports_dir = root / "reports"
    # ticker.map — one keyword-prefixed symbol-rule file. GLOBAL renames
    # apply everywhere; TOBASE consolidations apply only in the main
    # (to-base) merge; JOURNAL pairs also net in the holdings export;
    # DELETE nukes a ticker. Each merge/export stage requests its subset.
    ticker_map = root / "ticker.map"
    ticker_map_arg = ticker_map if ticker_map.exists() else None
    # ticker_extraction_overrides.txt — description-keyed ticker
    # corrections for securities the currency->exchange suffix mislabels.
    sec_overrides = root / "ticker_extraction_overrides.txt"
    sec_overrides_arg = sec_overrides if sec_overrides.exists() else None
    # phantoms.json — optional project-wide list of (symbol, account) pairs
    # with missing pre-window history (from `find-missing-history
    # --gen-phantoms`). Auto-detected at the root like ticker.map; when present
    # it feeds every account's gains run via --incomplete-history.
    phantoms = root / "phantoms.json"
    phantoms_arg = phantoms if phantoms.exists() else None
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
        _crypto_wash_covered = _normalize_country(
            settings.get("country", "canada")) not in ("us", "usa")
        if not is_crypto:
            # Equity taxable accounts are handled by ONE blended pass
            # after this loop (Canada ACB blending / US cross-account
            # §1091 — the multi-account fix). Collected here.
            _blend_names.append(name)
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
                           reports_dir / f"{name}_wash.sum"):
                _stale.unlink(missing_ok=True)

    if _blend_names and not args.account and not pending_accounts:
        stage_blended_wash_pass(_blend_names, settings, cache,
                                reports_dir, sheltered_base,
                                incomplete_history=phantoms_arg)

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
        _crypto_wash_covered = _normalize_country(
            settings.get("country", "canada")) not in ("us", "usa")
        taxable_equity_base = [
            o["base"] for _, o, is_crypto in taxable_outputs
            if not is_crypto or _crypto_wash_covered]
        stage_cross_reports(all_gains, taxable_equity_base, sheltered_base, reports_dir,
                            ticker_map_arg)
        _warn_cross_taxable_overlap(
            [(n, o["base"]) for n, o, _ in taxable_outputs],
            settings)
    stage_fees(cache, settings, rates, reports_dir)

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
            for _rec in Manifest.load(_mp).records.values():
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
        try:
            cmd_sanity(argparse.Namespace(dir=str(root), items=[],
                                          tolerance=None, json=False))
        except SystemExit as _e:
            if _e.code:
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
{province_line}base_currency     = "{base_currency}"{base_pad}# report currency; foreign income converted at BoC/IRS rates
source_currencies = ["{source_currency}"]{source_pad}# currencies you hold besides base_currency (FX rates fetched)
tax_date          = "{tax_date}"{tax_pad}# settle | trade (default: settle for canada — CRA; trade for usa — IRS)

# cross_asset   = false               # true: WARN-ONLY, flag option-as-replacement wash triggers
#                                     #   (long call vs share loss / long put vs short loss); numbers never change
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
# option_grant_timing_since       = {year}      # contracts written before this year keep close timing — set it to
#                                             #   the first year you FILE under grant timing and keep it every year after
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

# Tax instalments (`taxjson instalments`, and a summary inside
# `taxjson estimate`). Uncomment and fill in YOUR figures.
#
# [instalments]
# basis                = "current_year"   # current_year | prior_year | cra_reminder
# withheld             = 0                # tax withheld at source this year
# # Last two years' net tax owing (line 48500 minus withholding, from
# # each Notice of Assessment). Supply BOTH even on current_year: CRA
# # assesses interest on the least of the methods your figures support,
# # and they decide whether instalments are owed at all. Leaving a 0
# # here reads as "I owed nothing" and suppresses both.
# prior_year_net_tax   = 55000
# second_prior_net_tax = 41000
# prescribed_rate      = 0.08             # CRA's overdue-tax rate; or a dated
# # schedule, since CRA resets it quarterly and charges each day at the
# # rate then in force:
# # prescribed_rates = [
# #   { from = "2026-01-01", rate = 0.08 },
# #   { from = "2026-07-01", rate = 0.09 },
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


def _print_elections(name: str, manifest_path: Path) -> int:
    from taxjson.lib.corp_actions import Manifest
    man = Manifest.load(manifest_path) if manifest_path.exists() else Manifest()
    if not man.records:
        print(f"  {name}: no elections recorded.")
        return 0
    print(f"  {name}  ({manifest_path}):")
    for eid, r in sorted(man.records.items()):
        print(f"    [{eid}] {r.election or '(none)'}")
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
    from taxjson.bin.taxjson_corp_actions import EXTRACTORS, _pending_doc
    try:
        grouped = group_inputs(acct_dir)
    except SystemExit:
        return None
    sink = _io.StringIO()
    for broker, csvs in grouped.items():
        extractor = EXTRACTORS.get(broker)
        if extractor is None:
            continue
        for csv_path in csvs:
            try:
                with contextlib.redirect_stderr(sink), \
                        contextlib.redirect_stdout(sink):
                    events = extractor(csv_path, name)
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
    country = _normalize_country(cfg.get("settings", {}).get("country", "canada"))
    cache = root / "work"
    inputs_dir = root / "inputs"

    if getattr(args, "pending", False):
        import json as _json
        agg_path = cache / "pending_elections.json"
        if not agg_path.exists():
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
            _print_elections(name, _manifest_path_for(inputs_dir / name, cache, name))
        print("\nRedo one: `taxjson elect <account> --redo` "
              "(add --event <id> for just one event).")
        return

    name = args.account
    if name not in accounts:
        _die(f"no [accounts.{name}] in taxjson.toml")
    acct_dir = inputs_dir / name
    manifest_path = _manifest_path_for(acct_dir, cache, name)

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
            print(f"warning: {event_id!r} matches no pending event and "
                  f"no saved election — saving anyway; check the id "
                  f"with `taxjson elect --pending` (after a "
                  f"`taxjson run --no-input`).", file=sys.stderr)
        man.set(ElectionRecord(event_id=event_id, summary=summary,
                               election=election,
                               notes="set via elect --set",
                               hints=hints))
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        man.save(manifest_path)
        print(f"Election saved: {event_id} = {election}"
              + (f" (hints: {hints})" if hints else "")
              + f" → {manifest_path}")
        return

    if not (args.redo or args.reset):
        print("Corporate-action elections:")
        _print_elections(name, manifest_path)
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


def _tx_period_cutoff(period: str, root: Optional[Path] = None):
    """Oldest date to include for a look-back window: 30d / 6w / 3m / 1y,
    `mtd` / `ytd` (calendar month/year to date), or `all` for no lower bound
    (full history). Days and weeks are exact; months and years use calendar
    arithmetic. With `root`, the year-shaped tokens every other PERIOD
    command accepts resolve too — a literal YYYY and `tax_year`/`ty` map
    to Jan 1 of that year (a lower bound only: the since-based commands
    plot/measure through today). Without `root` they stay rejected —
    the error message must then not advertise them."""
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
    if root is not None:
        if _YEAR_TOKEN_RE.fullmatch(tok):
            return date(int(tok), 1, 1)
        if tok in _TAX_YEAR_TOKENS:
            year = _soft_settings(root).get("year")
            if not year:
                _die("'tax_year' needs [settings] year in "
                         "taxjson.toml (or give an explicit window "
                         "like 1y).")
            return date(int(year), 1, 1)
    m = re.fullmatch(r"\s*(\d+)\s*([dwmy])\s*", (period or "").lower())
    if not m:
        year_forms = ", a year (2025), or tax_year" if root is not None \
            else ""
        _die(f"invalid time period {period!r} "
                 f"(use e.g. 30d, 6w, 3m, 1y, mtd, ytd, all"
                 f"{year_forms})")
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


def _period_keep(period: str, root: Path):
    """Resolve a period token to `(keep(date_str) -> bool, scope_label)`.
    `tax_year` (or `ty`) binds the window to the config tax year; a literal
    YYYY is that calendar year; `all` is the whole history; anything else is
    a look-back window (30d/6w/3m/1y)."""
    tok = (period or "").strip().lower()
    if _YEAR_TOKEN_RE.fullmatch(tok):
        return (lambda d: d.startswith(tok)), f"tax year {tok}"
    if tok in _TAX_YEAR_TOKENS:
        year = _soft_settings(root).get("year")
        if not year:
            _die("'tax_year' needs [settings] year in taxjson.toml "
                     "(or give an explicit window like 1y).")
        ys = str(year)
        return (lambda d: d.startswith(ys)), f"tax year {year}"
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
# exactly what `taxjson transactions` shows.
_NATIVE_TX_SUFFIXES = ("_raw.json", "_filled.json", "_sorted.json")


def _native_tx_file(cache: Path, account: str) -> Optional[Path]:
    for suf in _NATIVE_TX_SUFFIXES:
        p = cache / f"{account}{suf}"
        if p.exists():
            return p
    return None


def _discover_tx_accounts(cache: Path) -> List[str]:
    names = set()
    for suf in ("_raw.json", "_filled.json"):
        for p in cache.glob(f"*{suf}"):
            # pathlib's `*` matches leading dots — keep dot-prefixed
            # pipeline intermediates from masquerading as accounts.
            if not p.name.startswith("."):
                names.add(p.name[: -len(suf)])
    return sorted(names)


def _tx_display_line(tx: dict) -> Optional[str]:
    """Human-readable line for `taxjson transactions`. Money amounts (total,
    fee, dividend/tax/interest/adjust amount) are shown to 2 decimals; quantity
    and per-share price keep their significant digits (a 0.0375 dividend rate
    or a 0.25178314 crypto qty must not be rounded away). Mirrors the .tt field
    layout but is a DISPLAY formatter — distinct from tx_to_tt_line, which
    keeps full precision for round-trippable .tt output. Returns None for
    actions with no representation."""
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
    time = tx.get("time", "09:30:00")
    sym = tx.get("symbol", "")
    cur = tx.get("currency") or "CAD"
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

    if action in ("BUYSELL", "ASSIGN"):
        return f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} {money(abs(net))} {money(abs(fee))}"
    if action == "TRANSFER":
        return f"{action} {date} {time} {sym} {sig(qty)} {cur} {sig(price)} {money(abs(net))}"
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
                   help="Futures (F:/\\/ / prefix)")
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
    for acct in accounts:
        native = _native_tx_file(cache, acct)
        if native is None:
            print(f"note: no native transaction file for account {acct!r}; "
                  f"skipping.", file=sys.stderr)
            continue
        try:
            data = json.loads(native.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {native}: {e}", file=sys.stderr)
            continue
        for tx in data.get("transactions", []):
            if actions is not None and tx.get("action") not in actions:
                continue
            if symbol_filter is not None and not symbol_filter(tx.get("symbol") or ""):
                continue
            d = tx.get("date") or ""
            if not _ISO_DATE_RE.match(d):    # can't place it in the window
                bad_dates += 1
                continue
            if keep(d):
                rows.append((d, tx.get("time") or "", acct, tx))

    # Chronological, oldest → latest (date, then time), across all accounts.
    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    if getattr(args, "json", False):
        jb: Dict[str, Dict[str, float]] = {"buy": {}, "sell": {},
                                           "dividend": {}}
        for _d, _t, acct, tx in rows:
            cur = tx.get("currency") or "?"
            act = tx.get("action")
            if act in ("BUYSELL", "ASSIGN"):
                amt = abs(float(tx.get("net_amount") or 0.0))
                q = float(tx.get("quantity") or 0.0)
                bucket = "buy" if q > 0 else "sell" if q < 0 else None
                if bucket:
                    jb[bucket][cur] = jb[bucket].get(cur, 0.0) + amt
            elif act in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                amt = (float(tx.get("gross_amount") or 0.0)
                       or float(tx.get("net_amount") or 0.0))
                jb["dividend"][cur] = jb["dividend"].get(cur, 0.0) + amt
        _json_out({"rows": [dict(tx, account=acct)
                            for _d, _t, acct, tx in rows],
                   "totals": {k: v for k, v in jb.items() if v},
                   "bad_dates": bad_dates})
        return

    # A single named account prints pure taxtext (round-trippable); the
    # all-accounts view prefixes each line with the account so the merged
    # chronological list stays legible.
    prefix = len(accounts) != 1
    skipped = 0
    out_lines = []
    buys: Dict[str, float] = {}
    sells: Dict[str, float] = {}
    divs: Dict[str, float] = {}
    for _d, _t, acct, tx in rows:
        line = _tx_display_line(tx)
        if line is None:
            skipped += 1
            continue
        out_lines.append(f"{acct} {line}" if prefix else line)
        cur = tx.get("currency") or "?"
        act = tx.get("action")
        if act in ("BUYSELL", "ASSIGN"):
            amt = abs(float(tx.get("net_amount") or 0.0))
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
            divs[cur] = divs.get(cur, 0.0) + amt
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
               ("TOTAL DIVIDEND:", divs)) if d]
    if footer:
        print()
        for lbl, d in footer:
            print(f"{lbl:<15} {_fmt(d)}")

    if skipped:
        print(f"note: skipped {skipped} row(s) with no taxtext representation "
              f"(e.g. OPENING_BALANCE).", file=sys.stderr)
    if bad_dates:
        print(f"taxjson: warning: {bad_dates} row(s) had a missing/unparseable date and "
              f"were excluded from the window.", file=sys.stderr)


def cmd_transactions(args: argparse.Namespace) -> None:      # `events` view
    _run_tx_view(args, actions=None, label="events")


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
    rows: List[Dict[str, Any]] = []
    for p in sorted(cache.glob("*_transfers.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
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
                         "value": float(t.get("net_amount") or 0),
                         "currency": t.get("currency") or "",
                         "where": "sidecar"})
    cfg = _soft_config(root)
    for name in (cfg.get("accounts") or {}):
        p = cache / f"{name}_base.json"
        if not p.exists():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for t in (doc.get("transactions") if isinstance(doc, dict)
                  else doc) or []:
            if t.get("action") != "TRANSFER":
                continue
            rows.append({"date": t.get("date") or "",
                         "account": t.get("account") or name,
                         "symbol": t.get("symbol") or "",
                         "quantity": float(t.get("quantity") or 0),
                         "type": t.get("description") or "",
                         "value": float(t.get("net_amount") or 0),
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
    out_lines = ["DATE ACCOUNT SYMBOL QTY TYPE VALUE CUR WHERE"]
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
            fmt_money(r["value"]), r["currency"], r["where"]]))
    _print_report_table(out_lines)
    print(f"\n{len(rows)} transfer row(s).")


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


def _warn_gains_artifact_scope(files, period_token) -> None:
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
            y = str((_json.loads(p.read_text(encoding="utf-8"))
                     .get("summary") or {}).get("year") or "")
        except (OSError, ValueError):
            continue
        if y and y != "all":
            years.add(y)
    if not years:
        return
    in_scope = (tok in _TAX_YEAR_TOKENS or tok == ""
                or (tok.isdigit() and tok in years))
    if not in_scope:
        print(f"taxjson: warning: the computed gains artifacts cover "
              f"tax year {', '.join(sorted(years))} only — rows "
              f"outside it are NOT in this report (the requested "
              f"window '{period_token}' may exceed that; "
              f"`taxjson gains` reads the full-history native "
              f"books).", file=sys.stderr)


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
    leaps: set = set()
    qty_by_symbol: Dict[str, float] = {}
    for acct in accounts:
        native = _native_tx_file(cache, acct)
        if native is None:
            continue
        try:
            data = json.loads(native.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
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
            if tx.get("action") not in ("BUYSELL", "ASSIGN"):
                continue
            sym = tx.get("symbol") or ""
            if not is_option_symbol(sym):
                continue
            qty = float(tx.get("quantity") or 0.0)
            prev_bal = acct_bal.get(sym, 0.0)
            acct_bal[sym] = prev_bal + qty
            qty_by_symbol[sym] = qty_by_symbol.get(sym, 0.0) + qty
            if sym in leaps or tx.get("action") != "BUYSELL" or qty <= 0:
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
                leaps.add(sym)
    return {sym: qty_by_symbol.get(sym, 0.0) for sym in leaps}


def cmd_leaps(args: argparse.Namespace) -> None:             # `leaps` view
    """Closed LEAPS positions over the window: one row per engine
    disposition of a qualifying contract (long option buy placed >3
    months to expiry), with lot-matched base-currency gains."""
    root = Path(args.dir).resolve()
    keep, scope, account = _view_window(args, root)
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
    if getattr(args, "json", False):
        _json_out({"rows": [dict(e, account=acct) for acct, e in entries],
                   "total_gain": round(sum(float(e.get("gain") or 0)
                                           for _a, e in entries), 2),
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
    print(f"Amounts are the engine's allowed figures — lot-matched, "
          f"basis: {basis}. Partial closes of a contract "
          f"appear as they are realized; still-open contracts are absent.")


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
    for acct, path in resolved.items():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {path}: {e}", file=sys.stderr)
            continue
        found = True
        for e in data.get("transactions", []):
            sym = e.get("symbol") or ""
            if sym not in leaps:
                continue
            if e.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            if "gain" not in e or "qty" not in e or e.get("tainted"):
                continue
            d = e.get("date") or ""
            if not _ISO_DATE_RE.match(d) or not keep(d):
                continue
            entries.append((acct, e))
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
                   "total_gain": round(sum(r2["gain"]
                                           for r2 in agg.values()), 2),
                   "currency": base_cur, "basis": basis})
        return
    for sym, rec in sorted(agg.items(), key=sort_key):
        total += rec["gain"]
        out_lines.append(" ".join([
            sym, parse_option_expiry(sym) or "?", f"{rec['qty']:g}",
            money(rec["proceeds"]), money(rec["cost"]),
            money(rec["gain"])]))
    print(f"LEAPS REALIZED GAINS — {scope} ({base_cur}; long option buys "
          f"placed >3 months to expiry)")
    print()
    _print_report_table(out_lines)
    print(f"\nTOTAL REALIZED GAIN: {money(total)} {base_cur}")
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
    _warn_gains_artifact_scope(resolved,
                               getattr(args, "period", None))
    if not resolved:
        if account:
            sys.exit(f"taxjson ccd-sum: no gains for account {account!r} "
                     f"in {cache} (run `taxjson run` first, or check "
                     f"the name).")
        sys.exit(f"taxjson ccd-sum: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    basis = gains_basis_label(resolved)

    agg: Dict[str, Dict[str, float]] = {}
    tainted_skipped = 0
    for acct, f in resolved.items():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {f}: {e}",
                  file=sys.stderr)
            continue
        for t in data.get("transactions", []):
            sym = str(t.get("symbol") or "")
            if not is_option_symbol(sym):
                continue
            if parse_option_right(sym) != "C":
                continue
            d = str(t.get("date") or "")
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
            und = parse_option_underlying(sym) or sym
            rec = agg.setdefault(und, {"contracts": 0, "qty": 0.0,
                                       "proceeds": 0.0, "cost": 0.0,
                                       "gain": 0.0})
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
                   "total_gain": round(sum(r["gain"]
                                           for r in agg.values()), 2),
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
        total += rec["gain"]
        out_lines.append(" ".join([
            und, str(int(rec["contracts"])), f"{rec['qty']:g}",
            money(rec["proceeds"]), money(rec["cost"]),
            money(rec["gain"])]))
    print(f"COVERED-CALL GAINS — {scope} ({base_cur}; SHORT call legs "
          f"only, engine-allowed amounts, basis: {basis})")
    print()
    _print_report_table(out_lines)
    print(f"\nTOTAL COVERED-CALL GAIN: {money(total)} {base_cur}")
    print("PREMIUM = proceeds of the sold calls; BUYBACK = cost to "
          "close (0 for expiries); assignments' share gains are NOT "
          "here — they land in the stock's own rows.")


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
    _warn_gains_artifact_scope(resolved,
                               getattr(args, "period", None))
    if not resolved:
        sys.exit(f"taxjson winners: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    basis = gains_basis_label(resolved)
    _INCOME = {"DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST",
               "FEE", "DISALLOW", "ADJUST"}
    agg: Dict[str, Dict[str, float]] = {}
    tainted_skipped = 0
    for _acct, f in resolved.items():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {f}: {e}",
                  file=sys.stderr)
            continue
        for t in data.get("transactions", []):
            if t.get("action") in _INCOME:
                continue
            d = str(t.get("date") or "")
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
            und = parse_option_underlying(sym) or sym
            rec = agg.setdefault(und, {"closes": 0, "proceeds": 0.0,
                                       "cost": 0.0, "gain": 0.0})
            rec["closes"] += 1
            rec["proceeds"] += float(t.get("proceeds") or 0.0)
            rec["cost"] += float(t.get("cost") or 0.0)
            rec["gain"] += float(t.get("gain") or 0.0)
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
                   "total_gain": round(sum(r["gain"]
                                           for _t, r in ranked), 2),
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
    total = sum(r["gain"] for _t, r in ranked)
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
            keep, scope = (lambda d: d.startswith(ys)), f"tax year {year}"
        else:
            keep, scope = (lambda d: True), "all history"
    return keep, scope, account


def _tx_fee(tx: dict) -> float:
    """Total fee on a transaction. Parsers split this across `fee` and
    `commission` (Questrade uses the latter); sum both."""
    return float(tx.get("fee") or 0.0) + float(tx.get("commission") or 0.0)


def _collect_period_txs(args: argparse.Namespace, label: str, actions):
    """Read native per-account transactions over a window, for the period-aware
    summaries (`fees`/`divs-sum`/`trades-sum`). The lone positional is a period
    (30d/6w/…) when it looks like one, else an account name; with no period the
    scope defaults to the config tax year. Returns (rows, scope_label, bad,
    keep) — `keep(iso_date) -> bool` is the window predicate, for callers
    that must apply the SAME window to a second data source."""
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

    rows, bad = [], 0
    for acct in accounts:
        native = _native_tx_file(cache, acct)
        if native is None:
            continue
        try:
            data = json.loads(native.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {native}: {e}", file=sys.stderr)
            continue
        for tx in data.get("transactions", []):
            if actions is not None and tx.get("action") not in actions:
                continue
            d = tx.get("date") or ""
            if not _ISO_DATE_RE.match(d):
                bad += 1
                continue
            if not keep(d):
                continue
            rows.append((acct, tx))
    return rows, scope, bad, keep


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
            # the amount in net_amount, sign-preserved: negative = charged.
            # Flip so a charge counts as a positive fee (a rebate nets out).
            fee = -float(tx.get("net_amount") or 0.0)
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


def cmd_divs_sum(args: argparse.Namespace) -> None:
    """Dividend summary over a window (default: the tax year): total received
    per ticker, plus a per-currency grand total. `PERIOD` is 30d/6w/3m/1y/all;
    omit it for the tax year."""
    rows, scope, bad, _keep = _collect_period_txs(
        args, "divs-sum", actions={"DIVIDEND", "DIVIDEND_IN_LIEU"})

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], float] = {}
    totals: Dict[str, float] = {}
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        # Signed: reversal rows (negative) net against the original posting.
        amt = (float(tx.get("gross_amount") or 0.0)
               or float(tx.get("net_amount") or 0.0))
        key = (str(tx.get("symbol") or "?"), cur)
        agg[key] = agg.get(key, 0.0) + amt
        totals[cur] = totals.get(cur, 0.0) + amt
    _warn_bad_dates(bad)
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "dividend": round(amt, 2)}
                            for (sym, cur), amt in sorted(agg.items())],
                   "totals": {c: round(v, 2) for c, v in totals.items()},
                   "scope": scope})
        return
    if not agg:
        print(f"No dividends in {scope}.")
        return
    out_lines = ["SYMBOL CUR DIVIDEND"]
    for (sym, cur), amt in sorted(agg.items()):
        out_lines.append(" ".join([sym, cur, money(amt)]))
    print(f"DIVIDENDS — {scope}")
    print()
    _print_report_table(out_lines)
    tot = ", ".join(f"{money(v)} {c}" for c, v in sorted(totals.items()))
    print(f"\nTOTAL DIVIDEND: {tot}")


def cmd_dil_sum(args: argparse.Namespace) -> None:
    """Payment-in-lieu summary over a window (default: the tax year):
    DIVIDEND_IN_LIEU rows only — payments received while shares were lent
    out (or short) over the ex-date. Split out from `divs-sum` because
    the tax treatment differs: a payment in lieu is ordinary income, NOT
    an eligible dividend (no CA gross-up/credit; no US qualified rate)."""
    rows, scope, bad, _keep = _collect_period_txs(
        args, "dil-sum", actions={"DIVIDEND_IN_LIEU"})

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], Dict[str, float]] = {}
    totals: Dict[str, float] = {}
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        # Signed: reversal rows (negative) net against the original posting.
        amt = (float(tx.get("gross_amount") or 0.0)
               or float(tx.get("net_amount") or 0.0))
        key = (str(tx.get("symbol") or "?"), cur)
        rec = agg.setdefault(key, {"amount": 0.0, "rows": 0})
        rec["amount"] += amt
        rec["rows"] += 1
        totals[cur] = totals.get(cur, 0.0) + amt
    _warn_bad_dates(bad)
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "in_lieu": round(rec["amount"], 2),
                             "rows": int(rec["rows"])}
                            for (sym, cur), rec in sorted(agg.items())],
                   "totals": {c: round(v, 2) for c, v in totals.items()},
                   "scope": scope})
        return
    if not agg:
        print(f"No dividends in lieu in {scope}.")
        return
    out_lines = ["SYMBOL CUR IN_LIEU ROWS"]
    for (sym, cur), rec in sorted(agg.items()):
        out_lines.append(" ".join([sym, cur, money(rec["amount"]),
                                   str(int(rec["rows"]))]))
    print(f"DIVIDENDS IN LIEU — {scope}  (ordinary income: no dividend "
          f"gross-up/credit or qualified rate)")
    print()
    _print_report_table(out_lines)
    tot = ", ".join(f"{money(v)} {c}" for c, v in sorted(totals.items()))
    print(f"\nTOTAL DIVIDEND IN LIEU: {tot}")


def cmd_roc_sum(args: argparse.Namespace) -> None:
    """Return-of-capital summary over a window (default: the tax year):
    per ticker, the capital returned (= ACB reduced) with broker-classified
    vs manual row counts, plus per-currency totals. Reads ADJUST rows —
    broker-classified ROC carries type='roc'; manual .tt adjustments (e.g.
    T3 box 42 entries) count too."""
    rows, scope, bad, _keep = _collect_period_txs(args, "roc-sum",
                                           actions={"ADJUST"})

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], Dict[str, float]] = {}
    totals: Dict[str, float] = {}
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        # ADJUST net_amount is the ACB delta (negative = reduction).
        # Present as capital RETURNED, so a normal ROC posting is
        # positive; negative values are reversals / manual ACB increases.
        returned = -float(tx.get("net_amount") or 0.0)
        key = (str(tx.get("symbol") or "?"), cur)
        rec = agg.setdefault(key, {"returned": 0.0, "roc_rows": 0,
                                   "manual_rows": 0})
        rec["returned"] += returned
        if (tx.get("type") or "").lower() == "roc":
            rec["roc_rows"] += 1
        else:
            rec["manual_rows"] += 1
        totals[cur] = totals.get(cur, 0.0) + returned
    _warn_bad_dates(bad)
    if getattr(args, "json", False):
        _json_out({"rows": [{"symbol": sym, "currency": cur,
                             "capital_returned": round(rec["returned"], 2),
                             "roc_rows": int(rec["roc_rows"]),
                             "manual_rows": int(rec["manual_rows"])}
                            for (sym, cur), rec in sorted(agg.items())],
                   "totals": {c: round(v, 2) for c, v in totals.items()},
                   "scope": scope})
        return
    if not agg:
        print(f"No ACB adjustments in {scope}.")
        return
    out_lines = ["SYMBOL CUR CAPITAL_RETURNED ROC_ROWS MANUAL_ROWS"]
    for (sym, cur), rec in sorted(agg.items()):
        out_lines.append(" ".join([sym, cur, money(rec["returned"]),
                                   str(rec["roc_rows"]),
                                   str(rec["manual_rows"])]))
    print(f"RETURN OF CAPITAL / ACB ADJUSTMENTS — {scope}")
    print()
    _print_report_table(out_lines)
    tot = ", ".join(f"{money(v)} {c}" for c, v in sorted(totals.items()))
    print(f"\nTOTAL CAPITAL RETURNED (ACB reduced): {tot}")
    print("Positive = ACB reduced (capital returned). Negative rows are "
          "reversals or manual ACB increases. Enter fund ROC from your T3 "
          "box 42 as .tt ADJUST lines — see the README's ROC section.")


def cmd_trades_sum(args: argparse.Namespace) -> None:
    """Trade summary over a window (default: the tax year): per ticker, the
    buy/sell counts, value bought/sold, and fees, plus per-currency totals.
    `PERIOD` is 30d/6w/3m/1y/all; omit it for the tax year."""
    rows, scope, bad, _keep = _collect_period_txs(
        args, "trades-sum", actions={"BUYSELL", "ASSIGN"})

    money = fmt_money               # shared report-layer formatter

    agg: Dict[Tuple[str, str], Dict[str, float]] = {}
    tot_bought: Dict[str, float] = {}
    tot_sold: Dict[str, float] = {}
    tot_fees: Dict[str, float] = {}
    for acct, tx in rows:
        cur = tx.get("currency") or "?"
        q = float(tx.get("quantity") or 0.0)
        amt = abs(float(tx.get("net_amount") or 0.0))
        fee = _tx_fee(tx)
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
    if getattr(args, "json", False):
        _json_out({"rows": [dict(d, symbol=sym, currency=cur,
                                 buys=int(d["buys"]), sells=int(d["sells"]))
                            for (sym, cur), d in sorted(agg.items())],
                   "totals": {c: {"bought": round(tot_bought.get(c, 0.0), 2),
                                  "sold": round(tot_sold.get(c, 0.0), 2),
                                  "fees": round(tot_fees.get(c, 0.0), 2)}
                              for c in sorted(set(tot_bought) | set(tot_sold)
                                              | set(tot_fees))},
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


def _gain_display_line(g: dict) -> str:
    """A realized-gain (disposition) row for `taxjson gains`: money columns
    (proceeds/cost/gain) to 2 decimals, quantity keeps significant digits."""
    money = fmt_money               # shared report-layer formatter

    def sig(x):
        s = f"{float(x or 0):.8f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-", "-0") else s

    days = g.get("days_held")
    return " ".join([
        g.get("date", ""), g.get("symbol", ""), sig(g.get("qty")),
        g.get("currency") or "?", money(g.get("proceeds")),
        money(g.get("cost")), money(g.get("gain")),
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
            sys.exit(f"taxjson gains: no native gains for account "
                     f"{account!r} in {cache} (crypto has none; else run "
                     f"`taxjson run`, or check the name).")
    else:
        accounts = sorted(p.name[: -len(suffix)]
                          for p in cache.glob(f"*{suffix}")
                          if not p.name.startswith("."))
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
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {f}: {e}", file=sys.stderr)
            continue
        for g in data.get("transactions", []):
            if g.get("action") in _INCOME:
                continue
            if sym_filter is not None and not sym_filter(g.get("symbol") or ""):
                continue
            d = g.get("date") or ""
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


def _account_plan(name: str, acfg: Dict[str, Any]) -> str:
    """Registered-plan kind for scan checks: explicit `plan = "tfsa"` in
    taxjson.toml wins; else inferred from the account NAME (the init
    scaffold names folders tfsa/rrsp/...); else the bare type."""
    explicit = str(acfg.get("plan") or "").strip().lower()
    if explicit:
        return explicit
    low = name.lower()
    for p in _PLAN_NAMES:
        if p in low:
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
    country = _normalize_country(str(settings.get("country", "canada")))
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
            print(f"taxjson: warning: could not read {f.name}: {e}",
                  file=sys.stderr)

    # Dividend payers, per raw (pre-consolidation) symbol.
    div_syms: set = set()
    for name in accounts:
        f = cache / f"{name}_raw.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for t in data.get("transactions", []):
            if t.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                div_syms.add(str(t.get("symbol") or "").upper())

    # ticker.map consolidations (GLOBAL + TOBASE + JOURNAL) and the
    # user's declared-distinct pairs (CDRs etc. — see DISTINCT).
    renames: Dict[str, str] = {}
    distinct_pairs: set = set()
    map_file = root / "ticker.map"
    if map_file.exists():
        try:
            from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                        merge_renames)
            _tmap = load_map_file(map_file)
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

    def _see(sym: str) -> None:
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
        if "TO" in (seen_suffixes.get(rt) or set()):
            return True
        tgt = renames_u.get(sym_u, "")
        return tgt.endswith(".TO")

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
                pays = (sym_u in div_syms
                        or f"{rt}.TO" in div_syms)
                if not pays:
                    continue
                if _has_ca_twin(rt, sym_u):
                    findings.append((
                        "US-LISTING", name, sym,
                        f"Canadian issuer held via its US listing in a "
                        f"{plan} account while paying dividends — hold "
                        f"{rt}.TO instead for clean eligible-dividend "
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
    # occurs in any parsed source. ROOT-aware on purpose — a rule with
    # no stock rows can still be live through OPTION trades (the
    # underlying's root folds through it: BCE251121C00050000.US needs
    # `TOBASE BCE.US BCE.TO`), and a bare from-symbol (`D056068`)
    # matches with or without a currency suffix. A root-blind check
    # once pruned ten live rules from a real map and split every
    # affected option's identity class (2026-09-15).
    map_unused: list = []
    if renames:
        from taxjson.lib.core import (is_option_symbol,
                                      parse_option_underlying)
        _seen_syms: set = set()
        for _acct in accounts:
            for _p in _audit_source_files(cache, _acct):
                try:
                    _doc = json.loads(_p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for _t in (_doc.get("transactions", _doc)
                           if isinstance(_doc, dict) else _doc) or []:
                    _sym = str((_t or {}).get("symbol") or "").upper()
                    if _sym:
                        _seen_syms.add(_sym)
        _roots: set = set()
        for _sym in _seen_syms:
            _b = _sym
            if is_option_symbol(_sym):
                try:
                    _b = str(parse_option_underlying(_sym)).upper()
                except Exception:
                    pass
            _roots.add(_b)
            _roots.add(_b.rsplit(".", 1)[0])
        for _frm, _to in sorted(renames.items()):
            _fu = _frm.upper()
            if _fu in _roots or _fu.rsplit(".", 1)[0] == _fu and _fu in _roots:
                continue
            if "." not in _fu and any(r.rsplit(".", 1)[0] == _fu
                                      for r in _roots):
                continue
            map_unused.append(f"{_frm} -> {_to}")

    if getattr(args, "online", False):
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
                us = [s for s in syms
                      if _scan_symbol_root(s)[1] == "US"]
                ca = [s for s in syms
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
              f"option roots and suffix-less codes): "
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
    `taxjson show <account>`."""
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
            sys.exit(f"taxjson sum: no gains for account "
                     f"{args.account!r} in {cache} (run `taxjson run` "
                     f"first, or check the name).")
        sys.exit(f"taxjson sum: no gains files in {cache} "
                 f"(run `taxjson run` first).")
    basis = gains_basis_label(files)

    money = fmt_money               # shared report-layer formatter

    # --other-income/--other-losses turn on the marginal tax estimate,
    # which needs the config (country/account types) and only counts
    # TAXABLE accounts' income.
    want_estimate = (getattr(args, "other_income", None) is not None
                     or getattr(args, "other_losses", None) is not None
                     or getattr(args, "estimate", False))
    _oi, _ol = _estimate_inputs(root, args)
    _foreign_by_acct: Dict[str, float] = {}
    cfg = load_config(root) if (root / "taxjson.toml").exists() else {}
    taxable_accounts = {n for n, c in cfg.get("accounts", {}).items()
                        if c.get("type") == "taxable"}
    est = dict(realized=0.0, st=0.0, lt=0.0, div_ca=0.0,
               div_foreign=0.0, pil=0.0)
    if want_estimate and not cfg:
        sys.exit("taxjson sum: the tax estimate needs taxjson.toml "
                 "(country and account types).")
    if want_estimate:
        import math as _math
        for _flag in ("other_income", "other_losses"):
            _v = getattr(args, _flag, None)
            if _v is not None and (not _math.isfinite(_v) or _v < 0):
                # nan/inf rendered contradictory estimates with rc 0;
                # a NEGATIVE loss fabricated taxable gains — the
                # natural sign trap for "my carryover is -10,000"
                # (REVIEW #25/#42).
                sys.exit(f"taxjson sum: --{_flag.replace('_', '-')} "
                         f"must be a non-negative finite number "
                         f"(enter losses as a positive amount), "
                         f"got {_v!r}")

    header = ["ACCOUNT", "STOCK", "OPTION", "REALIZED", "DIVIDEND", "PIL",
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
                rep = json.loads(report_p.read_text(encoding="utf-8"))
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
            except (OSError, json.JSONDecodeError):
                res = None
        if res is None:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
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
                    est["div_ca" if is_ca_listed else "div_foreign"] += d
                    if not is_ca_listed:
                        _foreign_by_acct[acct] = (
                            _foreign_by_acct.get(acct, 0.0) + d)
                    est["pil"] += float(s.get("pil", 0) or 0)
        fees = sum(float(v) for v in (res.get("total_fees") or {}).values())
        tainted_included += int(res.get("tainted_count") or 0)
        tainted_routed += int(res.get("tainted_routed") or 0)
        acct_rows.append({"account": acct, "stock": round(cap, 2),
                          "option": round(opt, 2),
                          "realized": round(cap + opt, 2),
                          "dividend": round(div, 2),
                          "pil": round(pil, 2), "fees": round(fees, 2),
                          "total": round(cap + opt + div, 2),
                          "type": acct_types.get(acct, "")})

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

    # What the return's capital-gains entry asks for (Schedule 3 lines
    # 13199/13200; TurboTax's proceeds / ACB / outlays boxes): taxable
    # accounts only, on form-export's convention, so these can never
    # disagree with the export.
    from taxjson.bin.taxjson_form_export import (filing_totals,
                                                 load_dispositions)
    _settings = cfg.get("settings") or {}
    _fyear = year or _settings.get("year")
    _is_us = _normalize_country(str(_settings.get("country", "canada"))) \
        in ("us", "usa")
    _date_key = "date" if _is_us else "date_settle"
    filing_rows: List[Dict[str, Any]] = []
    for acct, p in files.items():
        if acct not in taxable_accounts:
            continue
        try:
            _ents, _ = load_dispositions([p], _fyear, _date_key)
        except (OSError, ValueError) as e:
            print(f"taxjson sum: warning: {acct}: could not read "
                  f"dispositions: {e}", file=sys.stderr)
            continue
        filing_rows.append({"account": acct, **filing_totals(_ents)})
    filing_total = {k: round(sum(r[k] for r in filing_rows), 2)
                    for k in ("proceeds", "acb", "outlays", "gain",
                              "denied")}
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
              f"these totals EXCLUDE them (see the account .sum's "
              f"MANUAL REPORTING section; report them by hand).",
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
                       "date_basis": _date_key},
            "sheltered_included": sheltered_included,
            "subtotals": {g.lower(): _sum_rows(rows)
                          for g, rows in group_defs}}
        if want_estimate:
            doc["estimate"] = _tax_estimate_result(
                cfg, est,
                other_income=_oi, other_losses=_ol,
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
          f"(REALIZED = STOCK + OPTION capital gain; "
          f"TOTAL = REALIZED + DIVIDEND)")
    if sheltered_included:
        print(f"NOTE: totals include sheltered account(s) "
              f"{', '.join(sheltered_included)} — not taxable events; "
              f"carryover/t1135/form-export exclude them.")
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

    if filing_rows:
        _form = ("Form 8949 / Schedule D" if _is_us
                 else "Schedule 3: line 13199 proceeds, 13200 gain")
        print()
        print(f"FOR THE RETURN — taxable accounts, {base} ({_form})")
        _fl = [" ".join(["ACCOUNT", "PROCEEDS", "COST(ACB)", "OUTLAYS",
                         "GAIN", "DENIED"])]
        for r in filing_rows:
            _fl.append(" ".join([r["account"], money(r["proceeds"]),
                                 money(r["acb"]), money(r["outlays"]),
                                 money(r["gain"]), money(r["denied"])]))
        _fl.append(" ".join(["RETURN", money(filing_total["proceeds"]),
                             money(filing_total["acb"]),
                             money(filing_total["outlays"]),
                             money(filing_total["gain"]),
                             money(filing_total["denied"])]))
        _print_report_table(_fl, rule_before_last=True)
        print("PROCEEDS − COST − OUTLAYS = GAIN. Short sales are shown as "
              "|amounts| and sell-side commissions as outlays, as on the "
              "form; COST includes the superficial losses DENIED, so the "
              "gain is the allowed one. Per-security rows: `taxjson "
              "form-export`.")

    if want_estimate:
        _print_tax_estimate(
            cfg, est, base,
            other_income=_oi, other_losses=_ol,
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
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            _die(f"[instalments] {key} must be a number, got {v!r}")
    # CRA resets the prescribed rate quarterly and charges each day at
    # the rate in force that day — so a dated schedule is accepted and
    # applied per day. A scalar prescribed_rate stays valid.
    if (cfg.get("prescribed_rate") is not None
            and cfg.get("prescribed_rates") is not None):
        _die("[instalments] set prescribed_rate OR prescribed_rates, "
             "not both — the dated schedule would silently win and "
             "the single rate be discarded.")
    if out.get("prescribed_rate", 0) > 1.0:
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
                r = float(row["rate"])
                if r > 1.0:
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
            a = float(a)
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
    if _normalize_country(str(_soft_settings(root).get("country",
                                                      "canada"))) \
            not in ("canada", "ca"):
        return None
    net = _net_tax_owing(r, float(icfg.get("withheld") or 0.0))
    rate = (icfg.get("prescribed_rates")
            if icfg.get("prescribed_rates")
            else float(icfg.get("prescribed_rate") or 0.0))
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
             "  prescribed_rate = 0.08      "
             "# CRA's quarterly overdue-tax rate\n"
             "  withheld = 0                "
             "# tax already withheld at source\n"
             "  paid = [{ date = \"2026-03-15\", amount = 15000 }]")
    settings = _soft_settings(root)
    year = settings.get("year")
    base = str(settings.get("base_currency", "CAD"))
    if _normalize_country(str(settings.get("country", "canada"))) \
            not in ("canada", "ca"):
        _die("instalments are modeled for canada only (US estimated "
             "taxes use a different regime — see KNOWN_ISSUES).")
    _oi, _ol = _estimate_inputs(root, args)
    _argv = [sys.executable, "-m", "taxjson.bin.taxjson_run",
             "-C", str(root), "estimate", "--json"]
    if _oi:
        _argv += ["--other-income", repr(_oi)]
    if _ol:
        _argv += ["--other-losses", repr(_ol)]
    res = _run(_argv, capture_output=True)
    if res.returncode != 0:
        _die(f"could not compute the estimate it builds on: "
             f"{(res.stderr or '').strip()[:400]}")
    r = (_json.loads(res.stdout) or {}).get("estimate") or {}
    if not r:
        _die("the estimate produced no result — run `taxjson run` "
             "first, and set [settings] province.")
    doc = _instalments_doc(root, r, year, icfg)
    if doc is None:
        _die("no `year` under [settings] in taxjson.toml — the "
             "instalment schedule is per tax year.")
    if getattr(args, "json", False):
        _json_out(doc)
        return
    print(INST.render(doc, base))
    if not doc.get("rate_configured"):
        print()
        print(_wrap_note(
            "NOTE: no [instalments] prescribed_rate set — interest "
            "shown as 0. CRA posts the overdue-tax rate quarterly; "
            "set it to price the shortfall."))


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
    for acct in taxable_accounts:
        p = Path(cache) / f"{acct}_base.json"
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        acct_total = 0.0
        acct_seen = False
        for t in data.get("transactions", []):
            if t.get("action") != "TAX":
                continue
            d = str(t.get("date") or "")
            if ystr and not d.startswith(ystr):
                continue
            acct_seen = True
            acct_total += float(t.get("net_amount") or 0.0)
        if acct_seen:
            seen = True
            total += acct_total
        else:
            total += (CA_FOREIGN_WITHHOLDING
                      * float(foreign_by_account.get(acct) or 0.0))
    return max(0.0, total) if seen else None


def _tax_estimate_result(cfg: Dict[str, Any], est: Dict[str, float], *,
                         other_income: float, other_losses: float,
                         province: Optional[str],
                         actual_withheld: Optional[float] = None
                         ) -> Dict[str, Any]:
    """Resolve country/province and run the estimator — shared by the
    text block and `sum --json` so the two can never disagree. For usa,
    un-termed gains are folded into ST (conservative) with a stderr
    note, and the ST input (post-fold, pre-loss) rides along as
    `st_input` for display."""
    from taxjson.lib.tax_estimate import estimate_canada, estimate_usa
    settings = cfg.get("settings", {})
    _est_year = settings.get("year")
    country = _normalize_country(str(settings.get("country", "canada")))
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
                                   actual_withheld=actual_withheld)
        except ValueError as e:
            _die(str(e))
    unterm = est["realized"] - est["st"] - est["lt"]
    st_in = est["st"]
    if abs(unterm) > 0.01:
        st_in += unterm
        print(f"taxjson sum: note: {fmt_money(unterm)} of gains carry no "
              f"ST/LT term — treated as SHORT-TERM (conservative); "
              f"re-run `taxjson run` to refresh.", file=sys.stderr)
    r = estimate_usa(st=st_in, lt=est["lt"], year=_est_year,
                     qualified_div=est["div_ca"] + est["div_foreign"],
                     pil=est["pil"], other_income=other_income,
                     other_losses=other_losses)
    r["st_input"] = round(st_in, 2)
    return r


def _print_tax_estimate(cfg: Dict[str, Any], est: Dict[str, float],
                        base_cur: str, *, other_income: float,
                        other_losses: float,
                        province: Optional[str],
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
                             actual_withheld=actual_withheld)
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
        ]
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
                  f"{money(tw['ti'])} = {money(other_income + est['pil'])}"
                  f" ordinary + {money(r['taxable_gain'])} taxable gains"
                  f" + {money(r['grossed_eligible'])} grossed dividends"
                  f" + {money(est['div_foreign'])} foreign")
            print(f"  {'FEDERAL':<36}{'BASE':>14}{'WITH':>14}")
            _print_trace_table(
                _trace_bracket_rows(CA_FED_BRACKETS, tb["ti"], tw["ti"]),
                money)
            _print_trace_table([
                (f"BPA credit ({CA_FED_BPA:,.0f} @ "
                 f"{CA_FED_BRACKETS[0][1] * 100:.0f}%)",
                 -tb["fed_bpa"], -tw["fed_bpa"]),
                (f"DTC 15.0198% x {money(r['grossed_eligible'])}",
                 -tb["fed_dtc"], -tw["fed_dtc"]),
                (f"FTC 15% x {money(est['div_foreign'])}",
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
                    -tb["prov_dtc"], -tw["prov_dtc"]),
                   (f"= {r['province']}", r["tax_base"]["provincial"],
                    r["tax_with"]["provincial"])], money)
            _print_trace_table(
                [("TOTAL", r["tax_base"]["total"],
                  r["tax_with"]["total"])], money)
            print(f"    => WITH - BASE = {money(r['estimated_tax'])} "
                  f"estimated tax on investment income")
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
        print(_wrap_note(
            "Assumes: Canadian-listed dividends are all ELIGIBLE; "
            "foreign withholding fully creditable; no BPA phase-out; "
            "interest income not included — see divs/fees views."))
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
        ]
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
        print("Assumes: single filer, standard deduction, all dividends "
              "QUALIFIED, no state tax; interest income not included.")


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
            notes.append(f"account {name}: holdings file(s) missing — "
                         f"not checked: "
                         f"{', '.join(Path(m).name for m in missing)}")
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
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {f}: {e}",
                  file=sys.stderr)
            continue
        year = year or (data.get("summary") or {}).get("year")
        for h in (data.get("inventory") or []):
            sym = str(h.get("symbol") or "")
            qty = float(h.get("qty", 0) or 0)
            if not sym or abs(qty) < 1e-12:
                continue
            if not getattr(args, "options", False) and is_option_symbol(sym):
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
    if getattr(args, "json", False):
        _json_out({"basis": basis, "year": year, "currency": base,
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
          f"{', '.join(sorted(files))}; tax year {year}, basis: {basis}  "
          f"(after ticker.map; option contracts "
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


def cmd_option_boundary(args: argparse.Namespace) -> None:
    """`taxjson option-boundary [--json]`: every written option in the
    taxable accounts whose write and close straddle a tax-year boundary
    (or that is still open at the project year's end) — where each
    amount lands under the timing in force (ITA s.49), and whether a
    filed year needs a T1-ADJ. A `filed/<year>.json` lock is what turns
    "if that year was filed" into a fact."""
    import json
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.option_boundary import straddling
    from taxjson.lib.pipeline import option_timing_from_settings
    root = Path(args.dir).resolve()
    cache = root / "work"
    cfg = load_config(root)
    settings = cfg.get("settings", {})
    year = int(settings.get("year") or 0)
    kw = option_timing_from_settings(settings)
    timing = kw.get("option_premium_timing", "close") if kw else "close"
    since = kw.get("option_grant_since") if kw else None
    filed_years = set()
    for f in (root / "filed").glob("*.json"):
        try:
            filed_years.add(int(f.stem))
        except ValueError:
            pass
    rows = []
    for name, acfg in sorted((cfg.get("accounts") or {}).items()):
        if not isinstance(acfg, dict) or acfg.get("type", "sheltered") != "taxable":
            continue
        base = cache / f"{name}_base.json"
        if not base.exists():
            print(f"taxjson option-boundary: warning: no {base.name} — run `taxjson run` first",
                  file=sys.stderr)
            continue
        doc = json.loads(base.read_text(encoding="utf-8"))
        txs = []
        for r in (doc.get("transactions", doc) if isinstance(doc, dict) else doc):
            try:
                txs.append(TaxTransaction(**{k: v for k, v in r.items()
                                             if k in TaxTransaction.__dataclass_fields__}))
            except TypeError:
                continue
        for r in straddling(txs, year, timing, since, filed_years):
            r["account"] = r["account"] or name
            rows.append(r)
    if getattr(args, "json", False):
        _json_out({"year": year, "timing": timing, "since": since,
                   "filed_years": sorted(filed_years), "rows": rows})
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
    amend = [r for r in rows if r["action"].startswith("T1-ADJ")]
    print()
    if amend:
        print(f"{len(amend)} item(s) require an amended return (T1-ADJ) — listed above with the year and amount.")
    else:
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
    country = _normalize_country(str(settings.get("country", "canada")))

    marks = [(args.done, "done"), (args.skip, "skipped"), (args.undo, None)]
    for step, mark in marks:
        if step:
            try:
                cl.set_override(root, year, step, mark, note=args.note or "")
            except KeyError:
                sys.exit(f"taxjson checklist: unknown step {step!r} "
                         f"(ids: {', '.join(s[0] for s in cl.STEPS)})")
            verb = {"done": "marked done", "skipped": "marked skipped",
                    None: "mark removed"}[mark]
            print(f"taxjson checklist: {step} {verb} "
                  f"(recorded in {cl.STATE_FILE}).")
    if args.reset:
        (root / cl.STATE_FILE).unlink(missing_ok=True)
        print(f"taxjson checklist: {cl.STATE_FILE} removed.")
    if (args.done or args.skip or args.undo or args.reset) and not args.walk \
            and not args.show:
        return

    ctx = cl.Ctx(root=root, cfg=cfg, year=year, today=_date.today(),
                 run_sub=cl.default_run_sub(root))
    only = [args.only] if args.only else None
    if only and args.only not in {s[0] for s in cl.STEPS}:
        sys.exit(f"taxjson checklist: unknown step {args.only!r}")
    if only and args.quick:
        print("taxjson checklist: --only names one step; ignoring --quick.",
              file=sys.stderr)
        args.quick = False

    if args.walk:
        if not sys.stdin.isatty():
            sys.exit("taxjson checklist --walk needs a terminal (use "
                     "`taxjson checklist` for the report, --done/--skip to "
                     "record steps).")
        _checklist_walk(ctx, cl, only, quick=args.quick)
        return

    results = cl.evaluate(ctx, only=only, quick=args.quick,
                          progress=cl.stderr_progress)
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
    then the user's decision is recorded."""
    meta = {s[0]: s for s in cl.STEPS}
    ids = [s[0] for s in cl.STEPS if not only or s[0] in only]
    print("Filing checklist walk. For each open step: [d]one  [s]kip  "
          "[r]e-check  [n]ext  [q]uit  (Enter = next)\n")
    seen = 0
    for sid in ids:
        r = cl.evaluate(ctx, only=[sid], quick=quick,
                        progress=cl.stderr_progress)[0]
        if r.passed:
            print(f"    {cl.SYMBOL[r.effective]} {sid}: {r.detail}"
                  + (f" (marked {r.override})" if r.override else ""))
            continue
        seen += 1
        _, stage, title, cmd, why = meta[sid]
        stage_name = dict(cl.STAGES)[stage]
        while True:
            print(f"\n--- [{stage}. {stage_name}]  {sid}")
            print(f"    {title}")
            print(f"    why:     {why}")
            print(f"    proves:  {cmd}")
            print(f"    found:   {cl.SYMBOL[r.effective]} {r.detail}")
            try:
                ans = input("    > ").strip().lower()
            except EOFError:
                print()
                return
            if ans in ("q", "quit"):
                return
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
                continue
            break                                   # next
    print(f"\n{seen} open step(s) visited. Summary "
          f"(`taxjson checklist` re-checks everything):")
    results = cl.evaluate(ctx, only=only, quick=True)
    print(cl.render(results, ctx.year, ctx.settings.get("country", "canada"),
                    quick=True))


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
    for acct, f in resolved.items():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {f}: {e}",
                  file=sys.stderr)
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
                # one showed as a 2x QTY_MISMATCH (real data: a repeated
                # rrsp2 made FNV 80-vs-40) — while the header printed
                # the deduplicated list. Count once, say so.
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
        try:
            _accts_cfg = load_config(root).get("accounts") or {}
        except SystemExit:
            _accts_cfg = {}
        config_groups, config_notes = _sanity_items_from_config(_accts_cfg,
                                                                root)
        if not config_groups:
            for n in config_notes:
                print(f"taxjson sanity: note: {n}", file=sys.stderr)
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
                try:
                    q = float(h.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    sys.exit(f"taxjson sanity: {path.name}: {sym or '?'}: "
                             f"quantity {h.get('quantity')!r} is not a number")
                if q != q or q in (float("inf"), float("-inf")):
                    sys.exit(f"taxjson sanity: {path.name}: {sym or '?'}: "
                             f"quantity is not finite")
                if not sym or abs(q) <= 1e-12:
                    continue
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
            "files": [{"file": str(p2), "file_account": lbl}
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
            extra = f"  (account {lbl})" if lbl not in p2.stem else ""
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
    if not all_rows:
        print("OK: tickers and quantities agree"
              + (" in every group." if multi else "."))
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
        # its base.json with the engine's --as-of cutoff. Full ACB
        # fidelity (incl. deferred wash) but PRE-WASH and PRE-ticker.map
        # (the cross-account pass only exists for full runs) — the
        # basis label says so.
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
        country = _normalize_country(str(settings.get("country",
                                                      "canada")))
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
        files = {}
        tmp_docs = {}
        for n in names:
            b = cache / f"{n}_base.json"
            if not b.exists():
                if args.account:
                    sys.exit(f"taxjson list: no {b.name} in {cache} "
                             f"(run `taxjson run` first).")
                # Plain `list` shows this account; vanishing from the
                # as-of view with rc 0 was a silent drop (REVIEW #35).
                print(f"taxjson list: warning: {n} skipped — no "
                      f"{b.name} in {cache} (run `taxjson run`); the "
                      f"as-of total excludes it.", file=sys.stderr)
                continue
            cmd = [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                   "--country", country, "--year", year,
                   "--as-of", as_of, "--no-wash"]
            if accounts_cfg.get(n, {}).get("type") == "taxable":
                cmd.append("--taxable")
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
        basis = f"as of {as_of} (pre-wash, pre-ticker.map)"
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
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"taxjson: warning: could not read {p}: {e}", file=sys.stderr)
                continue
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
            # positive — same convention as the holdings export.
            cps = cost / qty if qty else 0.0
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

    if getattr(args, "json", False):
        doc = {"rows": json_rows, "basis": basis, "year": year,
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
    print(f"{title} — {base}, as of tax year {year}, basis: {basis}  "
          f"(after ticker.map + base-currency conversion; COST is book cost)")
    print()
    _print_report_table(out_lines)
    print(f"\n{n_pos} position(s), total book cost {money(total_cost)} {base}")
    if total_deferred > 0.005:
        print(f"DEFERRED: {money(total_deferred)} {base} of the book "
              f"cost is denied superficial losses parked in these "
              f"positions (recovered when sold without a rebuy in the "
              f"window).")


def _soft_config(root: Path) -> Dict[str, Any]:
    """Whole taxjson.toml (soft-read; {} when absent or unreadable). The
    single home for the query wrappers' config reads — they must work
    from work/ files without a hard config dependency."""
    cfg_path = root / "taxjson.toml"
    if cfg_path.exists() and tomllib is not None:
        try:
            return tomllib.loads(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    return {}


def _soft_settings(root: Path) -> Dict[str, Any]:
    """[settings] table from taxjson.toml (soft-read; {} when absent)."""
    return _soft_config(root).get("settings", {}) or {}


def _base_currency(root: Path) -> str:
    """Base currency label from taxjson.toml (soft-read; 'CAD' default)."""
    return _soft_settings(root).get("base_currency", "CAD")


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
    files = list(resolved.items())

    money = fmt_money               # shared report-layer formatter
    from taxjson.lib.report_model import fmt_qty as qfmt

    header = ["ACCOUNT", "DATE", "SYMBOL", "QTY", "PROCEEDS", "COST",
              "GAIN", "DENIED", "ALLOWED"]
    rows = []
    year = None
    for acct, f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"taxjson: warning: could not read {f}: {e}", file=sys.stderr)
            continue
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
            _d2 = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
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
        out_lines.append(" ".join([
            acct, date or "-", str(t.get("symbol") or "?"),
            qfmt(float(t.get("qty") or 0)), money(float(t.get("proceeds") or 0)),
            money(float(t.get("cost") or 0)), money(econ), money(denied),
            money(allowed)]))
        total_denied += denied
        total_perm += perm

    base = _base_currency(root)
    print(f"WASH SALES — {base}, tax year {year}, basis: "
          f"{gains_basis_label(resolved)}  "
          f"(losses denied under the superficial-loss rule)")
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
          "from a repurchase in a registered account, which is lost for good.")


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
    base_currency = str(settings.get("base_currency", "CAD"))
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
    if missing:
        print(f"taxjson: warning: no base file for taxable account(s) "
              f"{', '.join(missing)} — run `taxjson run` first; the "
              f"threshold test below may be understated.", file=sys.stderr)
    if not argv:
        sys.exit(f"taxjson t1135: no taxable base files in {cache} "
                 f"(run `taxjson run` first).")

    argv += ["--year", str(year), "--base-currency", base_currency]
    t1135_map = root / "t1135.map"
    if t1135_map.exists():
        argv += ["--map", str(t1135_map)]
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
        "--country", _normalize_country(str(settings.get("country", "canada"))),
        "--base-currency", str(settings.get("base_currency", "CAD")),
    ]
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
    if args.json:
        argv.append("--json")
    raise SystemExit(taxjson_carryover.main(argv))


def _taxable_gains_argv(root: Path, cache: Path, *,
                        exclude_crypto: bool = False,
                        prog: str = "taxjson") -> List[str]:
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
    for name in sorted(taxable):
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            argv += ["--gains", str(gains)]
        else:
            print(f"taxjson: warning: no gains file for taxable account {name!r} — "
                  f"run `taxjson run` first.", file=sys.stderr)
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
    settings = load_config(root).get("settings", {})
    country = _normalize_country(settings.get("country", ""))
    form = args.form or ("8949" if country == "usa" else "schedule3")
    year = settings.get("year")

    gains_argv = _taxable_gains_argv(root, cache)
    # form-export takes gains files positionally.
    files = [gains_argv[i + 1] for i in range(0, len(gains_argv), 2)]
    argv = files + ["--form", form,
                    "--base-currency",
                    str(settings.get("base_currency", ""))]
    if year is not None:
        argv += ["--year", str(year)]
    if args.csv:
        argv += ["--csv", args.csv]
    if args.json:
        argv.append("--json")
    if form == "txf":
        argv += ["--box", args.box]
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
    for name in taxable:
        gains = resolve_gains_files(cache, name).get(name)
        if gains is not None:
            files.append(str(gains))
        else:
            print(f"taxjson: warning: no gains file for taxable account "
                  f"{name!r} — run `taxjson run` first.", file=sys.stderr)
    if not files:
        sys.exit(f"taxjson harvest: no taxable gains files in {cache} "
                 f"(run `taxjson run` first).")
    cmd = _cmd("taxjson-harvest") + files + [
        "--price-cache", str(cache / ".price_cache.json"),
        "--country", _normalize_country(str(settings.get("country",
                                                         "canada"))),
        "--base-currency", str(settings.get("base_currency", "CAD")),
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
        cmd += ["--radar", str(combined_sidecar)]
    else:
        for sidecar in per_acct_sidecars:
            cmd += ["--radar", str(sidecar)]
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
    # Staleness guard: after `run --account X` the wash file predates
    # the just-rebuilt plain gains (the blend pass was skipped).
    # `taxjson sum` merely notes this; close-year WRITES the filing
    # lock, so snapshotting stale numbers is a hard stop.
    stale = sorted(
        a for a, p in files.items()
        if p.name.endswith("_gains_wash.json")
        and p.with_name(p.name.replace("_gains_wash.json",
                                       "_gains.json")).exists()
        and _wash_preferred_gains(
            p.with_name(p.name.replace("_gains_wash.json",
                                       "_gains.json"))) != p)
    if stale:
        sys.exit(f"taxjson close-year: wash-adjusted gains for "
                 f"{', '.join(stale)} are STALER than the plain gains "
                 f"(a --account rerun skipped the cross-account wash "
                 f"pass) — run a full `taxjson run` first.")
    accounts = {}
    import json as _json
    for acct, pth in sorted(files.items()):
        doc = _json.loads(Path(pth).read_text(encoding="utf-8"))
        accounts[acct] = taxjson_filed.aggregates_from_gains(doc)
    basis = gains_basis_label(files)
    path = taxjson_filed.write_snapshot(
        root, year, _normalize_country(settings["country"]), basis,
        accounts, force=args.force)
    tot = _json.loads(path.read_text())["totals"]
    print(f"closed {year} ({basis}): realized {tot['realized']:,.2f}, "
          f"disallowed {tot['disallowed']:,.2f}, income "
          f"{tot['income']:,.2f} across {len(accounts)} account(s)")
    print(f"  -> {path}  (commit this with your records; "
          f"`taxjson check-filed` now guards it)")


def _check_filed_years(root: Path, cache: Path,
                       settings: Dict[str, Any], *,
                       strict: bool) -> int:
    """Drift check for every filed/<year>.json. Returns the number of
    drifting years; prints per-year OK/DRIFT lines."""
    from taxjson.bin import taxjson_filed
    import json as _json
    snaps = taxjson_filed.list_snapshots(root)
    drifting = 0
    # Which snapshot accounts are crypto (their books never blend);
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
    for year, path in snaps:
        snap = _json.loads(path.read_text(encoding="utf-8"))
        _snap_accts = list(snap.get("accounts", {}))
        _crypto = [a for a in _snap_accts
                   if (_acct_cfg.get(a) or {}).get("crypto")]
        _equity = [a for a in _snap_accts if a not in _crypto]
        recomputed = taxjson_filed.recompute_accounts(
            cache, _equity, _crypto, year, settings,
            snap.get("basis", ""), _filed_run_gains)
        lines = taxjson_filed.diff_snapshot(snap, recomputed)
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
            print(f"  filed {year}: OK (matches {path.name})")
    if drifting and strict:
        sys.exit(f"taxjson run --strict: {drifting} filed year(s) "
                 f"drifted — aborting.")
    return drifting


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
    if _check_filed_years(root, cache, settings, strict=False):
        raise SystemExit(1)


def cmd_reconcile_slips(args: argparse.Namespace) -> None:
    """`taxjson reconcile-slips SLIP.csv`: diff broker T5008/1099-B slips
    against the computed dispositions of all taxable accounts."""
    from taxjson.bin import taxjson_reconcile_slips

    root = Path(args.dir).resolve()
    cache = root / "work"
    settings = load_config(root).get("settings", {})
    year = settings.get("year")

    argv = [args.slip_csv] + _taxable_gains_argv(
        root, cache, exclude_crypto=True, prog="taxjson reconcile-slips")
    if year is not None:
        argv += ["--year", str(year)]
        # Same date convention stage_account feeds the gains engine:
        # IRS/1099-B scope by TRADE date, CRA/T5008 by SETTLEMENT; an
        # explicit tax_date config wins. Without this, a USA year-end
        # sale settling in January was on the 1099-B (and in
        # form-export) but missing from the computed side.
        tax_date = settings.get("tax_date") or (
            "trade" if _normalize_country(settings.get("country", "canada"))
            in ("us", "usa") else "settle")
        argv += ["--date-basis", tax_date]
    if args.tolerance is not None:
        argv += ["--tolerance", str(args.tolerance)]
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
        bases = [base]
    else:
        names = _taxable_equity_account_names(root)
        if names:
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
    if settings.get("country"):
        common += ["--country", _normalize_country(settings["country"])]
    if settings.get("tax_date"):
        common += ["--tax-date", settings["tax_date"]]
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

    rc = 0
    for base in bases:
        # Each trace block self-identifies its account, so no per-file header
        # is needed (and none is printed, avoiding a stdout-ordering glitch).
        from taxjson.lib.dispatch import run_cmd as _run_cmd
        proc = _run_cmd(_cmd("taxjson-explain") + common + [str(base)])
        rc = proc.returncode or rc
    raise SystemExit(rc)


def _taxable_equity_account_names(root: Path) -> List[str]:
    """Wash-checkable taxable account names from taxjson.toml (soft-read,
    like the sibling query commands — no hard exit when the config is
    absent). Crypto accounts are excluded only for US projects (§1091
    does not reach digital assets); Canada's superficial-loss rule
    covers any identical property, so Canadian crypto accounts are
    included — matching _wash_flags and the run pipeline's second pass.
    Empty list means 'unknown', so the caller falls back to globbing
    base files."""
    cfg = _soft_config(root)
    if not cfg:
        return []
    crypto_covered = _normalize_country(
        (cfg.get("settings") or {}).get("country", "canada")) \
        not in ("us", "usa")
    return [n for n, c in (cfg.get("accounts") or {}).items()
            if (c or {}).get("type") == "taxable"
            and (crypto_covered or not (c or {}).get("crypto"))]


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
        _acct_cfg = _soft_config(root).get("accounts") or {}
        if (_acct_cfg.get(args.account) or {}).get("type") == "sheltered":
            sys.exit(f"taxjson wash-radar: {args.account} is a "
                     f"sheltered account — the radar advises on "
                     f"TAXABLE loss sales (sheltered books are its "
                     f"context, not its subject).")
        bases = [base]
    else:
        bases = _radar_taxable_bases(root, cache, "taxjson wash-radar")

    cmd = _cmd("taxjson-wash-radar") + ["--taxable", *[str(b) for b in bases]]
    # Cross-account superficial-loss detection needs the pooled sheltered
    # history; pass it when the pipeline has built it.
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    else:
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
                         prog: str) -> List[Path]:
    """Taxable base files for a radar-style run. Prefer the config's
    wash-checkable taxable accounts (sheltered accounts are never
    radar'd; crypto is included only where the jurisdiction's wash
    rule covers it — see _taxable_equity_account_names). Fall back to
    globbing base files when there's no readable config."""
    names = _taxable_equity_account_names(root)
    if names:
        bases = [cache / f"{n}_base.json" for n in sorted(names)
                 if (cache / f"{n}_base.json").exists()]
    else:
        bases = [p for p in sorted(cache.glob("*_base.json"))
                 if not p.name.endswith("_raw_base.json")
                 and p.name != "sheltered_base.json"
                 and not p.name.startswith(".")]
    if not bases:
        sys.exit(f"{prog}: no taxable base files in {cache} "
                 f"(run `taxjson run` first).")
    return bases


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
        state_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        state_path = cache / ".watch_state.json"
    state = _watch.load_state(state_path)
    if state is None:
        _watch.save_state(state_path, cur_radar, harvest_now, as_of)
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
                                  float(getattr(args, "threshold",
                                                100.0) or 100.0))
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
    _watch.save_state(state_path, cur_radar, saved_harvest, as_of)

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
    # 0600 from the first byte: write_text inherits the umask, which
    # left the live credential world-readable between creation and the
    # post-rename chmod.
    fd = _os.open(str(tmp), _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC,
                  0o600)
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
                _die(f"Questrade auth failed: {e}")
            _questrade_token_write(tok_cache,
                                   qt_session["refresh_token"])
        try:
            positions = F.qt_positions(qt_session, number, http)
        except RuntimeError as e:
            _die(f"{a}: {e}")
        # Montreal options: learn Canadian-listed roots from the
        # account's own BOOKS too — a cash-secured put has no equity
        # leg in the live payload, so its live symbol was suffixed
        # .US while the books say .TO (phantom verify mismatch,
        # 2026-09 audit).
        _book_to_roots = set()
        try:
            import json as _json
            _bp = cache / f"{a}_base.json"
            if _bp.exists():
                for _r in (_json.loads(_bp.read_text(encoding="utf-8"))
                           .get("transactions") or []):
                    _sym = str(_r.get("symbol") or "")
                    if _sym.endswith(".TO"):
                        _root = _sym.rsplit(".", 1)[0]
                        from taxjson.lib.core import parse_option_underlying
                        _u = parse_option_underlying(_sym)
                        _book_to_roots.add((_u or _root).split(".")[0])
        except Exception:
            pass
        text = F.positions_to_holdings_toml(
            positions, a, number,
            _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            extra_to_roots=_book_to_roots)
        toml_path = cache / f"{a}_live_holdings.toml"
        tmp = toml_path.with_name(toml_path.name + ".part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(toml_path)
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
    for sib in sorted(acct_dir.glob("*.csv")):
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
        n = sum(1 for r in rows[1:] if r and _row_date_in_window(
            r[0], start_iso, end_iso))
        if n:
            hits.append((sib, n))
    return hits


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
    with path.open(encoding="utf-8", errors="replace") as f:
        rows = list(_csv.reader(f))
    keep = [rows[0]] + [r for r in rows[1:]
                        if not (r and _row_date_in_window(
                            r[0], start_iso, end_iso))]
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
        path.replace(bak)
        buf = _io.StringIO()
        _csv.writer(buf, lineterminator="\n").writerows(keep)
        path.write_text(buf.getvalue(), encoding="utf-8")
    return removed


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
                             f"{e}")
                # Persist the ROTATED token immediately — a later
                # failure must not lose it (the old one is now dead).
                _questrade_token_write(tok_cache,
                                       qt_session["refresh_token"])
            _fetch_year = (getattr(args, "year", None)
                           or cfg.get("settings", {}).get("year"))
            start, end = F.qt_window(getattr(args, "days", None),
                                     getattr(args, "from_date", None),
                                     year=_fetch_year)
            say(f"fetch {a}: questrade #{number} "
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
            acct_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            _tmp_out = out.with_name(out.name + ".part")
            _tmp_out.write_text(merged, encoding="utf-8")
            _tmp_out.replace(out)
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
                    cut = _qt_trim_file(sib, start.isoformat(),
                                        end.isoformat())
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
                    acct_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                    bad.write_text(text, encoding="utf-8")
                    saved = f" — saved to {bad.name}"
                sys.exit(f"taxjson fetch: {a}: the Flex download is "
                         f"not in the section,Header/Data CSV shape "
                         f"the IB parser reads{saved}. "
                         f"In the Flex query settings choose format "
                         f"CSV and enable 'include section code and "
                         f"line descriptor', then re-fetch.")
            results[a] = {"source": "ibkr_flex", "file": out.name,
                          "lines": len(text.splitlines())}
            if getattr(args, "dry_run", False):
                say(f"  would write {out.name} "
                    f"({len(text.splitlines())} lines)")
                continue
            acct_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Atomic like the Questrade path: a crash mid-write must
            # not leave a truncated statement for the next run.
            _part = out.with_name(out.name + ".part")
            _part.write_text(text, encoding="utf-8")
            _part.replace(out)
            say(f"  {out.name}: {len(text.splitlines())} lines "
                f"(overwritten — a Flex query re-covers its whole "
                f"configured period)")
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
    base = str(settings.get("base_currency", "CAD")).upper()
    year = settings.get("year")
    if not year:
        sys.exit("taxjson fx-cash: needs [settings] year in "
                 "taxjson.toml.")
    country = _normalize_country(str(settings.get("country", "canada")))
    txs: List[Dict[str, Any]] = []
    found = False
    for name, acfg in sorted((cfg.get("accounts") or {}).items()):
        if (acfg or {}).get("type") != "taxable":
            continue                    # s.39 reaches the person's
            # taxable holdings; registered accounts are exempt.
        f = _native_tx_file(cache, name)
        if f is None:
            continue
        try:
            doc = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"taxjson fx-cash: warning: could not read {f}: {e}",
                  file=sys.stderr)
            continue
        found = True
        for t in doc.get("transactions", []):
            t.setdefault("account", name)
            txs.append(t)
    if not found:
        sys.exit(f"taxjson fx-cash: no native transaction files in "
                 f"{cache} (run `taxjson run` first).")
    fx = load_fx_history(cache / "to_base.csv", base)
    ledger = FX.build_ledger(txs, base, fx, int(year))
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
                   "currency": base, "year": year})
        return
    print(FX.render_report(ledger, base, year, country, verdict))
    if getattr(args, "events", False) and ledger["events"]:
        print("\nDATE ACCOUNT CUR UNITS RATE GAIN SYMBOL")
        for e in ledger["events"]:
            print(f"{e['date']} {e['account']} {e['currency']} "
                  f"{e['units']:g} {e['rate']:g} {e['gain']:+,.2f} "
                  f"{e['symbol'] or '-'}")


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
    sheltered_base = cache / "sheltered_base.json"
    if sheltered_base.exists():
        cmd += ["--sheltered", str(sheltered_base)]
    else:
        _acfg = _soft_config(root).get("accounts") or {}
        if any((c or {}).get("type") == "sheltered"
               for c in _acfg.values()):
            print(f"{prog}: note: no sheltered_base.json in work/ — "
                  f"sheltered-account activity is invisible to the "
                  f"window checks; run a full `taxjson run` to build "
                  f"it.", file=sys.stderr)
    res = _run(cmd, capture_output=True)
    if res.returncode != 0:
        _die(f"radar failed: {(res.stderr or '').strip()[:400]}")
    radar = flatten_radar(_json.loads(res.stdout))

    from taxjson.lib.brokerages.schema import KNOWN_SUFFIXES
    # 'VN' too: Questrade spells TSX Venture that way, and a root
    # must fold the same either way.
    _EXCH = set(KNOWN_SUFFIXES) | {"VN"}

    # DISTINCT pairs from ticker.map: symbols the user declared SEPARATE
    # securities despite a shared root (a CDR vs its US underlying —
    # the engine pools them separately). The suffix strip below merged
    # them before any union ran, so `DISTINCT UNH.US UNH.TO` still
    # made buy-check UNH.TO UNSAFE after a UNH.US loss (2026-09 audit).
    # A declared member keeps its FULL symbol as its root, and no union
    # may ever join the two sides of a pair.
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
                  f"— mapped cross-listings with different roots will "
                  f"not match.", file=sys.stderr)
    _protected = set().union(*_distinct_pairs) if _distinct_pairs else set()

    def _root(t: str) -> str:
        t = t.strip().upper()
        # An option is a right to acquire the underlying — identical
        # property for s.40(2)(g)/§1091 purposes. Fold OCC symbols to
        # the underlying's root so `buy-check AAPL...C00150000` sees
        # AAPL's wash state instead of "no exposure" (2026-09 audit).
        from taxjson.lib.core import parse_option_underlying
        _u = parse_option_underlying(t)
        if _u:
            t = _u.upper()
        if t in _protected:
            return t
        base, _, ext = t.rpartition(".")
        return base if ext in _EXCH else t

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

    def canon(t: str) -> str:
        return _find(_root(t))

    _acct_cfg = _soft_config(root).get("accounts") or {}
    _taxable = {a for a, c in _acct_cfg.items()
                if (c or {}).get("type") == "taxable"}
    last_loss: Dict[str, Dict[str, Any]] = {}
    for _a, _p in resolve_gains_files(cache).items():
        if _taxable and _a not in _taxable:
            continue
        try:
            _doc = _json.loads(_p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for _t in _doc.get("transactions", []):
            if not _t.get("qty") or "gain" not in _t:
                continue
            _g = float(_t.get("raw_gain", _t.get("gain")) or 0.0)
            if _g >= 0:
                continue
            _c = canon(str(_t.get("symbol") or ""))
            # Settlement basis: the radar's ±30-day windows are
            # settle-based, so a trade-date age contradicted the
            # verdict for anything traded 31-32 days ago.
            _d = str(_t.get("date_settle") or _t.get("date") or "")
            _prev = last_loss.get(_c)
            if _prev is None or _d > _prev["date"]:
                last_loss[_c] = {"date": _d,
                                 "symbol": _t.get("symbol"),
                                 "gain": round(_g, 2),
                                 "account": _t.get("account") or _a}
    return radar, canon, last_loss


def _class_matches(radar: Dict[str, Dict[str, Any]], canon, want: str):
    """(class root, {ticker: row}, note) for one queried symbol. A
    BARE query (no exchange suffix) whose root is shared by members of
    a DISTINCT pair matches none of them by root — those members keep
    their full symbol as root (a share class like BRK.B keeps its dot
    the same way) — so it falls back to EVERY such member (worst
    verdict wins; the per-ticker lines name each) with a note asking
    for an explicit spelling. Never merges the members with each
    other: an explicit `UNH.TO` still sees only UNH.TO's class."""
    wroot = canon(want)
    matches = {t: r for t, r in radar.items() if canon(t) == wroot}
    note = None
    if not matches and "." not in want.strip():
        amb = {t: r for t, r in radar.items()
               if canon(t) != wroot
               and canon(t).rpartition(".")[0] == wroot}
        if amb:
            matches = amb
            note = (f"{want.strip().upper()}: ambiguous — "
                    f"{', '.join(sorted(amb))} are kept separate "
                    f"(DISTINCT in ticker.map, or a share class); "
                    f"this is the worst verdict across them. Query "
                    f"one explicitly.")
    return wroot, matches, note


def _last_loss_line(ll) -> Optional[str]:
    if not ll:
        return None
    from datetime import date as _date
    try:
        _ago = (_date.today() - _date.fromisoformat(ll["date"])).days
        _ago_s = f"{_ago} days ago"
        _inout = ("INSIDE the 30-day window" if _ago <= 30
                  else "outside the 30-day window")
    except ValueError:
        _ago_s, _inout = "?", "window position unknown"
    return (f"last loss sale this tax year: {ll['symbol']} "
            f"{ll['date']} ({_ago_s}, {ll['gain']:,.2f}) — {_inout}.")


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
    unsafe = 0
    results = []
    for want in args.symbol:
        wroot, matches, _note = _class_matches(radar, _canon, want)
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
                lines.append(
                    f"{t}: {cat} — a loss sold within the past 30 "
                    f"days; buying now cancels it (DEFERRED if bought "
                    f"taxable, PERMANENT if bought sheltered)."
                    + (f" Safe to buy from {_cd}." if _cd else
                       " Wait until 31 days after the LATEST in-window "
                       "loss sale (see `taxjson wash-radar`)."))
            elif cat in ("LOCKED", "EXITABLE", "CAUTION"):
                if verdict == "SAFE":
                    verdict = "SAFE*"
                lines.append(
                    f"{t}: {cat} — no recent loss sale, buying is "
                    f"safe TODAY, but it extends the wash window: a "
                    f"loss sale of this name before ~31 days from "
                    f"the buy would be superficial.")
        matches = {t: r for t, r in matches.items()
                   if (r.get("category") or "")}
        if len(lines) == bool(_note) and matches:
            cats = ", ".join(f"{t} ({r.get('category')})"
                             for t, r in sorted(matches.items()))
            lines.append(f"{cats}: no loss sale in the past 30 days — "
                         f"safe to buy. (Any buy starts a 30-day "
                         f"window: selling this name at a loss within "
                         f"31 days of it would be superficial.)")
        elif not lines:
            lines.append(f"{wroot}: no wash exposure on record — safe "
                         f"to buy. (Any buy starts a 30-day window: "
                         f"selling this name at a loss within 31 days "
                         f"of it would be superficial.)")
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

    if getattr(args, "json", False):
        _json_out({"results": results})
    else:
        for r in results:
            print(f"{r['symbol']}: {r['verdict']}")
            for ln in r["detail"]:
                print(f"  {ln}")
    if unsafe:
        raise SystemExit(1)


def cmd_sell_check(args: argparse.Namespace) -> None:
    """`taxjson sell-check SYMBOL...`: is selling this ticker AT A
    LOSS today safe from the superficial-loss / wash-sale rules?
    UNSAFE when a recent affiliated buy still held would deny the
    loss (LOCKED — permanently for the registered-matched portion);
    ACTION when a rescueable violation is already open (sell the FULL
    position before the deadline); SAFE* for conditional cases
    (full-exit-only, sheltered-holds forward caveat); SAFE otherwise
    — with the standard rule: no rebuy on EITHER side for 30 days
    after. Root-matched with ticker.map equivalences. Exit 1 when any
    queried symbol is UNSAFE. Whether the sale would BE a loss at
    today's price is `taxjson harvest`'s job."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    radar, _canon, _last_loss = _wash_class_context(
        root, cache, "taxjson sell-check")
    unsafe = 0
    results = []
    for want in args.symbol:
        wroot, matches, _note = _class_matches(radar, _canon, want)
        verdict, lines = "SAFE", ([_note] if _note else [])
        clears = None        # safe-to-sell-from date (worst = LATEST)
        act_by = None        # rescue deadline: last TRADE date (EARLIEST)
        for t, r in sorted(matches.items()):
            cat = r.get("category") or ""
            adv = r.get("advisory") or ""
            if cat == "LOCKED":
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
                _shl = float(r.get("sheltered_qty") or 0.0)
                if abs(_shl) > 0.01:
                    # A registered account holds it too. The
                    # registered-matched portion is denied for good
                    # ONLY if the sheltered side still holds at the
                    # window's end (s.40(2)(g)(ii) still-held test) —
                    # exiting BOTH sides before the deadline defeats
                    # it. Say that, instead of the old blanket
                    # "cannot be rescued" that contradicted the
                    # rescue advisory on the same line.
                    verdict = "UNSAFE"
                    lines.append(
                        f"{t}: {adv} A sheltered account also holds "
                        f"this: unless the SHELTERED shares are also "
                        f"sold by that trade date"
                        + (f" ({_cd})" if _cd else "")
                        + f", the registered-matched portion is "
                        f"permanently denied.")
                else:
                    if verdict != "UNSAFE":
                        verdict = "ACTION"
                    lines.append(f"{t}: {adv}")
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
                        f"{t}: held only in sheltered account(s) — "
                        f"nothing to sell at a loss (a registered "
                        f"disposition has no tax effect).")
                else:
                    lines.append(f"{t}: CLEAR — safe to sell at a loss "
                                 f"now; do not rebuy on EITHER side "
                                 f"(taxable or sheltered) for 30 days.")
        if len(lines) == bool(_note):
            lines.append(f"{wroot}: no tracked taxable position — "
                         f"nothing to sell (or run `taxjson run` to "
                         f"refresh the books).")
        _lll = _last_loss_line(_last_loss.get(wroot))
        if _lll:
            lines.append(_lll)
        if verdict == "UNSAFE":
            unsafe += 1
        results.append({"symbol": want.strip().upper(),
                        "verdict": verdict,
                        "clears_at": clears,
                        "act_by": act_by, "detail": lines,
                        "last_loss": _last_loss.get(wroot)})
    if getattr(args, "json", False):
        _json_out({"results": results})
    else:
        for r in results:
            print(f"{r['symbol']}: {r['verdict']}")
            for ln in r["detail"]:
                print(f"  {ln}")
    if unsafe:
        raise SystemExit(1)


# Pipeline artifacts in work/ that are NOT parsed-source files — the
# audit's source join must never read a derived book as provenance.
_AUDIT_DERIVED_SUFFIXES = (
    "_base.json", "_gains.json", "_gains_wash.json", "_raw.json",
    "_raw_base.json", "_raw_gains.json", "_raw_base_gains.json",
    "_merged.json", "_sorted.json", "_filled.json", "_report.json",
    "_pending_elections.json", "_validate.diag",
)


def _audit_source_files(cache: Path, name: str) -> List[Path]:
    """The parsed-source JSONs for one account: everything the pipeline
    wrote as `{name}_*.json` that is not a derived book. These are the
    per-brokerage parses, converted .tt files, and corp-action rows —
    the nominal-currency provenance the audit joins dispositions to."""
    out = []
    for p in sorted(cache.glob(f"{name}_*.json")):
        if any(p.name.endswith(sfx) for sfx in _AUDIT_DERIVED_SUFFIXES):
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
    country = _normalize_country(settings.get("country", "canada"))
    tax_date = settings.get("tax_date") or (
        "trade" if country in ("us", "usa") else "settle")
    base_currency = str(settings.get("base_currency") or "CAD").upper()
    year = None if getattr(args, "all_years", False) else (
        getattr(args, "year", None) or settings.get("year"))

    acfgs = _soft_config(root).get("accounts") or {}
    taxable = {n: (c or {}) for n, c in acfgs.items()
               if (c or {}).get("type") == "taxable"}
    if not taxable:
        _die("no taxable accounts in taxjson.toml — nothing to audit.")
    equity = sorted(n for n, c in taxable.items() if not c.get("crypto"))
    crypto = sorted(n for n, c in taxable.items() if c.get("crypto"))

    sheltered_base = cache / "sheltered_base.json"
    phantoms = root / "phantoms.json"
    rates = cache / "to_base.csv"
    tmap = root / "ticker.map"

    def common_flags() -> List[str]:
        fl = ["--country", country, "--tax-date", tax_date,
              "--base-currency", base_currency]
        if year:
            fl += ["--year", str(year)]
        if rates.exists():
            fl += ["--rates", str(rates)]
        if tmap.exists():
            fl += ["--map", str(tmap)]
        if phantoms.exists():
            fl += ["--incomplete-history", str(phantoms)]
        if settings.get("cross_asset"):
            fl.append("--cross-asset")
        fl += option_timing_flags(settings)
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
        return fl

    # (flags, cleanup_path|None) per engine computation, mirroring the
    # pipeline: ONE blended pass over the equity taxable accounts, then
    # each crypto account on its own books.
    invocations: List[Tuple[List[str], Optional[Path]]] = []

    eq_bases = [cache / f"{n}_base.json" for n in equity
                if (cache / f"{n}_base.json").exists()]
    missing = [n for n in equity
               if not (cache / f"{n}_base.json").exists()]
    if missing:
        print(f"taxjson audit: note: no books yet for "
              f"{', '.join(missing)} — run `taxjson run` to include "
              f"them.", file=sys.stderr)
    if eq_bases:
        cleanup: Optional[Path] = None
        if len(eq_bases) == 1:
            base_arg = eq_bases[0]
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
                       + [str(b) for b in eq_bases],
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
        if country in ("us", "usa"):
            fl.append("--per-account-basis")
        for n in equity:
            for src in _audit_source_files(cache, n):
                fl += ["--source", str(src)]
            chk = cache / f"{n}_gains_wash.json"
            if not chk.exists():
                chk = cache / f"{n}_gains.json"
            if chk.exists():
                fl += ["--check", str(chk)]
        invocations.append((fl, cleanup))

    for n in crypto:
        base = cache / f"{n}_base.json"
        if not base.exists():
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
        for src in _audit_source_files(cache, n):
            fl += ["--source", str(src)]
        chk = cache / f"{n}_gains_wash.json"
        if not chk.exists():
            chk = cache / f"{n}_gains.json"
        if chk.exists():
            fl += ["--check", str(chk)]
        invocations.append((fl, None))

    if not invocations:
        _die("no computed books in work/ — run `taxjson run` first.")

    rc = 0
    json_docs: List[Dict[str, Any]] = []
    try:
        for fl, _cl in invocations:
            cmd = _cmd("taxjson-audit") + fl
            if getattr(args, "json", False):
                res = _run(cmd + ["--json"], capture_output=True)
                if res.stderr:
                    sys.stderr.write(res.stderr)
                try:
                    json_docs.append(_json.loads(res.stdout))
                except ValueError:
                    _die(f"taxjson-audit produced no JSON "
                         f"(rc={res.returncode}).")
                rc = max(rc, res.returncode or 0)
            else:
                res = _run(cmd)
                rc = max(rc, res.returncode or 0)
    finally:
        for _fl, _cl in invocations:
            if _cl is not None:
                _cl.unlink(missing_ok=True)

    if getattr(args, "json", False):
        if len(json_docs) == 1:
            doc = json_docs[0]
        else:
            doc = {"base_currency": base_currency, "country": country,
                   "events": [e for d in json_docs
                              for e in d.get("events") or []],
                   "total_gain": round(sum(d.get("total_gain") or 0.0
                                           for d in json_docs), 2),
                   "total_disallowed": round(
                       sum(d.get("total_disallowed") or 0.0
                           for d in json_docs), 2),
                   "failed": any(d.get("failed") for d in json_docs)}
        _json_out(doc)
    if rc:
        raise SystemExit(rc)


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
        hint = ("`taxjson run` auto-detects it"
                if out.name == "phantoms.json" and out.parent == root
                else f"save it as {root / 'phantoms.json'} and `taxjson run` "
                     f"picks it up, or pass it to taxjson-gains "
                     f"--incomplete-history")
        print(f"\nWrote {len(rows)} phantom candidate(s) to {out} "
              f"({n_reg} in registered accounts — almost certainly phantom).\n"
              f"Review the file and remove any entries that are real short "
              f"positions. Then {hint}.", file=sys.stderr)
        return

    cmd = _cmd("taxjson-missing-history") + [str(f) for f in files]
    if year:
        cmd += ["--year", str(year)]
    if args.include_options:
        cmd += ["--include-options"]
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
        cmd += ["--to", settings.get("base_currency", "CAD"),
                "--rates", str(rates)]
    if args.by_account:
        cmd += ["--by-account"]
    if args.json:
        cmd += ["--json"]
    _exec_tool(cmd)


def cmd_serve(args: argparse.Namespace) -> None:
    # Lazy import so the [web] extra is only needed for this subcommand.
    from taxjson.web.server import serve
    raise SystemExit(serve(args.dir, host=args.host, port=args.port))


def cmd_init(args: argparse.Namespace) -> None:
    country = _normalize_country(args.country)
    if country not in _INIT_BY_COUNTRY:
        sys.exit(f"taxjson init: unknown country {args.country!r} "
                 f"(expected canada | ca | usa | us)")

    _year = getattr(args, "year", None)
    if _year is not None and not (1900 <= _year <= 2100):
        # 0/-5/20255 scaffolded projects whose every report was
        # silently all-zero (REVIEW #28).
        sys.exit(f"taxjson init: --year {_year} is not a plausible tax "
                 f"year (expected 1900..2100)")

    # The positional `path` (if given) overrides the global -C/--dir flag.
    target = getattr(args, "path", None) or args.dir
    root = Path(target).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / "taxjson.toml"
    if cfg.exists() and not args.force:
        _die(f"{cfg} already exists (use --force to overwrite)")

    written: List[str] = []

    # The config is (re)written — the guard above already enforces --force.
    config_text, account_names = _render_init_config(
        country, getattr(args, "year", None))
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
    print("\nNext:")
    print(f"  1. edit {cfg} — set the year, accounts, and source currencies")
    print("  2. drop broker CSV exports into inputs/<account>/")
    print(f"  3. run: taxjson run -C {root}")


def main() -> None:
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
        help="Like `events` but only DIVIDEND rows (native, taxtext)")
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
        help="Dividend total per ticker over a window (default: tax year)")
    p_dsum.add_argument("period", nargs="?", help=_PERIOD_HELP)
    p_dsum.add_argument("account", nargs="?", help="Account (default: all)")
    p_dsum.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_dsum.set_defaults(func=cmd_divs_sum)

    p_dilsum = sub.add_parser(
        "dil-sum",
        help="Payment-in-lieu total per symbol over a window (default: "
             "tax year) — ordinary income, split out from divs-sum")
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
                       help="Positions AS OF this date — books recomputed "
                            "with the engine's --as-of cutoff (full "
                            "ACB/deferred fidelity; pre-wash, "
                            "pre-ticker.map)")
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
        help="Strip account numbers and identity from broker exports "
             "(row shapes kept) so a real statement can be shared as a "
             "parser sample or bug report")
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
                       help="Report only; write nothing")
    p_red.set_defaults(func=cmd_redact)

    p_ob = sub.add_parser(
        "option-boundary",
        help="Written options that straddle a tax-year boundary: where the "
             "premium and any later amount land under ITA s.49, and whether "
             "a filed year needs a T1-ADJ")
    p_ob.add_argument("--json", action="store_true",
                      help="Emit JSON instead of text")
    p_ob.set_defaults(func=cmd_option_boundary)

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
    p_san.add_argument("--tolerance", type=float, default=1e-4,
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
    p_watch.add_argument("--threshold", type=float, default=100.0,
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
                              "fetch its whole window (Dec 15 of N-1 "
                              "through Jan 15 of N+1) into "
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
                              "from Dec 15 of the prior year; "
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
                              "(cross-check with `taxjson sanity` or "
                              "`taxjson verify`)")
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
             "recent affiliated buy would deny it (LOCKED); ACTION "
             "when a rescueable violation is open (sell the FULL "
             "position); SAFE*/SAFE otherwise with the applicable "
             "caveats. buy-check's sell-side twin; whether it IS a "
             "loss at today's price is `taxjson harvest`'s job. "
             "Exit 1 on unsafe")
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
    p_audit.add_argument("--year", type=int,
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
    p_forms.add_argument("--box", default="A", choices=["A", "B", "C"],
                         help="txf only: 8949 checkbox pairing (A/D "
                              "basis-reported default, B/E, C/F)")
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
                        help="4001 Gateway live, 7496 TWS live")
    p_harv.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    p_harv.add_argument("--verbose", "-v", action="store_true",
                        help="Per-tier price-chain diagnostics")
    p_harv.set_defaults(func=cmd_harvest)

    p_rec = sub.add_parser(
        "reconcile-slips",
        help="Diff broker T5008 / 1099-B slip CSVs against computed "
             "dispositions (exit 1 on any mismatch)")
    p_rec.add_argument("slip_csv", help="Slip CSV — headers matched "
                       "loosely (symbol/ticker, quantity/box 16, "
                       "proceeds/box 21, cost/box 20)")
    p_rec.add_argument("--tolerance", type=float, default=None,
                       help="Absolute per-symbol tolerance (default 1.00)")
    p_rec.add_argument("--json", action="store_true")
    p_rec.set_defaults(func=cmd_reconcile_slips)

    p_close = sub.add_parser(
        "close-year",
        help="Snapshot the current tax year's filing aggregates to "
             "filed/<year>.json — the filed-year lock that "
             "check-filed (and every full run) guards")
    p_close.add_argument("--year", type=int, default=None,
                         help="Must match [settings].year (guard)")
    p_close.add_argument("--force", action="store_true",
                         help="Replace an existing lock (re-filed/"
                              "amended years only)")
    p_close.set_defaults(func=cmd_close_year)

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
    p_fmh.add_argument("--year", type=int,
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
             "(default: tax year); same report `taxjson run` writes to "
             "reports/fees.rpt")
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
    p_serve.add_argument("--port", type=int, default=8765, help="Bind port")
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
    for seg in segments:
        args = p.parse_args(seg)
        _CURRENT_CMD = next((t for t in seg if t in commands), "")
        try:
            args.func(args)
        except SystemExit as e:
            # A failing command stops the chain and propagates its
            # code; an explicit success (sys.exit(0) / None) lets the
            # next command run.
            if e.code not in (None, 0):
                raise
        except subprocess.CalledProcessError as e:
            # A pipeline stage failed. The child's own stderr already
            # explained WHY (run_to_file echoes it) — re-raising the
            # CalledProcessError just buried that explanation under a
            # second traceback (2026-09 audit).
            sys.exit(f"taxjson: stage failed: "
                     f"{' '.join(str(c) for c in (e.cmd or [])[-3:])} "
                     f"(exit {e.returncode}) — see the error above.")
    return


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
