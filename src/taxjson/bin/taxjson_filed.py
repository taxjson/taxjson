#!/usr/bin/env python3
"""Filed-year lock: snapshot a filed tax year and detect drift.

The pipeline recomputes full history on every run — correct, but it
means a code upgrade, a parser fix, or an input edit can silently change
an ALREADY-FILED year's numbers. This module gives that a lock:

  tjs close-year          snapshot the current tax year's per-account
                              filing aggregates to filed/<year>.json
                              (commit it with your records)
  tjs check-filed         recompute every filed year from the CURRENT
                              books and report any drift vs the snapshots

A full `tjs run` auto-checks at the end (warn-only; fatal under
--strict), so "did my filed 2025 numbers just move?" becomes a report
line instead of archaeology. Drift is not automatically wrong — a
legitimate fix can move a filed year — but it always deserves eyes, and
usually an amended return or a refreshed snapshot (`close-year
--force`).

Compared aggregates per taxable account (engine-recomputed, matching
the filing basis recorded at close time): realized total (non-tainted),
total disallowed, disposition count, dividend and PIL income, tainted
count, the engine's net proceeds, ST/LT gain, and the form lines the
export puts on the return (Schedule 3 line codes, or Form 8949 part
totals). NOT locked: interest, foreign tax withheld and the FX gain on
foreign cash (line 15300 / §988) — check-filed says so with every OK.

Reading a lock, its problems and the aggregates it holds is
lib/filed_locks.py (read_lock, lock_damage, aggregates_from_gains, ...);
this module writes a lock, recomputes a year and reports the drift.
"""

from taxjson.project.settings import Settings, as_settings
from taxjson.lib.pipeline import income_dating_flags, option_timing_flags
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib import channels as _channels
from taxjson.lib.tx_load import finite_num
from taxjson.lib import cli_diag
from taxjson.model import money as M
from taxjson.lib.filed_locks import (
    PROG,
    _canonical_country,
    aggregates_from_gains,
    locked_year_flags,
    project_locks,
)


# A figure a cent or less from the lock's is no drift, in whole cents
# (`M.cents_apart`): float error never trips it (TJ-3119).
_TOL_CENTS = 1


# What every OK line says the lock does not cover (S032-11); the RETURN
# row is checked for its shape only (backlog TJ-0274).
NOT_LOCKED = ("interest, foreign tax withheld and the FX gain on foreign "
              "cash are not locked; the RETURN row (return_row) is not "
              "recomputed")


# A 2024 lock closed before the 2024 form's Period 1 codes were modelled
# (A2-0166) put every 2024 disposition on the Period 2 codes: compared
# with today's split, Period 1 folds onto its Period 2 code.
_PERIOD1_TO_2 = {"10689": "13199", "10690": "13200",
                 "10693": "15199", "10694": "15300"}


def _fold_old_2024(fl_filed: Dict[str, Any], fl_cur: Dict[str, Any]
                   ) -> Optional[Dict[str, float]]:
    """`fl_cur` with its Period 1 codes folded onto the Period 2 ones
    when `fl_filed` is an old-shape 2024 lock (no Period 1 code) and
    `fl_cur` has one; else None."""
    if any(k in fl_filed for k in _PERIOD1_TO_2) or not any(
            k in fl_cur for k in _PERIOD1_TO_2):
        return None
    out: Dict[str, float] = {}
    for k, v in fl_cur.items():
        k2 = _PERIOD1_TO_2.get(k, k)
        out[k2] = M.shown(out.get(k2, 0.0) + float(v or 0.0), 2)
    return out


def snapshot_path(root: Path, year) -> Path:
    return Path(root) / "filed" / f"{year}.json"


def write_snapshot(root: Path, year, country: str, basis: str,
                   accounts: Dict[str, Dict[str, Any]], *,
                   force: bool,
                   option_timing: Optional[Dict[str, Any]] = None,
                   extra: Optional[Dict[str, Any]] = None,
                   raw: Optional[Dict[str, Dict[str, float]]] = None
                   ) -> Path:
    """Write filed/<year>.json. `option_timing` records the written-option
    premium timing the return used (Canada), so a later project's
    `option-boundary` can tell a year filed under grant timing from one
    filed under close timing. `raw`: each account's UNROUNDED money
    aggregates (aggregates_from_gains(rounded=False)) — the totals are
    their sum rounded once, as wash-sales and the engine round it, not a
    sum of per-account cents (S031-20)."""
    path = snapshot_path(root, year)
    if path.exists() and not force:
        from taxjson.lib.out import fail
        fail(f"filed/{path.name} already exists — the lock protects a "
             f"filed year", prog="tjs close-year",
             details=["Re-run with --force to replace it (only if you "
                      "re-filed/amended)."])
    _money = ("realized", "disallowed", "income", "dividend", "pil",
              "proceeds", "st_gain", "lt_gain")
    src = raw if raw else accounts
    totals = {
        k: M.shown(sum(a.get(k, 0) for a in src.values()), 2)
        if k in _money
        else sum(a.get(k, 0) for a in accounts.values())
        for k in ("realized", "disallowed", "dispositions", "income",
                  "dividend", "pil", "tainted", "proceeds", "st_gain",
                  "lt_gain")
    }
    doc = {
        "schema_version": 1,
        "year": int(year),
        "country": country,
        "basis": basis,
        "closed_at": datetime.now().isoformat(timespec="seconds"),
        # Which taxjson computed the figures (backlog TJ-0341):
        # check-filed names it when a later run drifts from them.
        "taxjson": _channels.running_build(),
        "accounts": accounts,
        "totals": totals,
        # 'tainted' includes manual_reporting_required rows; locks
        # written before this flag counted 0 for pipeline files, so
        # check-filed compares 'tainted' only when it is set.
        "tainted_counts_manual": True,
    }
    if option_timing:
        doc["option_timing"] = dict(option_timing)
    if extra:
        doc.update(extra)
        doc["schema_version"] = 2
    from taxjson.lib.safe_write import write_atomic
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    except OSError as e:
        # filed/ is a file, or the project is read-only: one line, not a
        # traceback (S031-19), exit 2: an output it cannot write
        # (TJ-4896). Nothing was written.
        from taxjson.lib.out import fail
        fail(f"cannot write filed/{path.name}: {e}",
             prog="tjs close-year", details=["Nothing was written."],
             code=2)
    return path


def lock_settings(lock: Dict[str, Any], settings: Any) -> Settings:
    """The settings a lock is recomputed under: this project's, with the
    date basis the lock RECORDED (close-year writes `date_basis`) — a
    year filed on settlement dates is checked on settlement dates even
    after the project switched tax_date."""
    rb = lock.get("date_basis") if isinstance(lock, dict) else None
    if rb in ("settle", "trade"):
        return as_settings(settings).replace(tax_date=rb)
    return as_settings(settings)


def _tax_date(settings: Dict[str, Any], country: str) -> str:
    """[settings] tax_date as the gains engine spells it. An explicit
    value that is not settle/trade (any case) is refused by name — it
    reached the engine raw and died with an argparse usage line that
    the checklist then reported as drift (S031-21)."""
    settings = as_settings(settings)
    raw = settings.tax_date
    if raw in (None, ""):
        from taxjson.lib.country import default_tax_date
        return default_tax_date(country)
    td = str(raw).strip().lower()
    if td not in ("settle", "trade"):
        raise ValueError(f"[settings] tax_date must be settle|trade, "
                         f"got {raw!r}")
    return td


def _lock_timing_flags(settings: Any, year: int,
                       option_timing: Optional[Dict[str, Any]]
                       ) -> List[str]:
    """The written-option timing flags for recomputing locked `year`:
    the timing its lock RECORDED when it has one, else the current
    settings. Recomputing a year filed on grant timing since 2025 with a
    later project's default (since = that project's year) moved the
    filed year's gains and advised amending a correct return (R1-188)."""
    if isinstance(option_timing, dict) \
            and option_timing.get("option_premium_timing"):
        since = option_timing.get("option_grant_since")
        changes = {"option_premium_timing": option_timing["option_premium_timing"],
                   "option_grant_timing_since": since if since is not None
                   else year}
        if "option_buyback_loss_superficial" in option_timing:
            changes["option_buyback_loss_superficial"] = bool(
                option_timing["option_buyback_loss_superficial"])
        return option_timing_flags(as_settings(settings).replace(**changes))
    return option_timing_flags(settings)


def _has_lot_moves(cache: Path, accounts: List[str]) -> bool:
    """Whether these accounts' base books carry own-account move legs
    (a US project's crypto accounts are then blended, as the run does —
    US-CRYPTO-05)."""
    from taxjson.lib.row_types import LOT_MOVE_TYPE
    for a in accounts:
        try:
            doc = json.loads((cache / f"{a}_base.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(isinstance(t, dict) and t.get("type") == LOT_MOVE_TYPE
               for t in (doc.get("transactions") or [])):
            return True
    return False


def recompute_accounts(cache: Path, equity_accounts: List[str],
                       crypto_accounts: List[str], year: int,
                       settings: Dict[str, Any], basis: str,
                       run_gains_cmd, *,
                       option_timing: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Optional[Dict[str, Any]]]:
    """Recompute a filed year's aggregates the way the pipeline
    computed them at close time: equity accounts via ONE blended
    combined run (Canada s.47 ACB blending; US --per-account-basis) —
    recomputing each in isolation falsely drifted every multi-account
    book — and crypto accounts per-account, except a Canadian project's
    two or more crypto accounts, which the pipeline blends too."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    # US crypto runs --taxable --no-wash in the pipeline (§1091 does
    # not reach digital assets) — the recompute must match or every US
    # crypto account with a wash-window loss drifts on every check.
    crypto_no_wash = _canonical_country(settings) == "usa"
    if len(crypto_accounts) >= 2 and (not crypto_no_wash
                                      or _has_lot_moves(cache,
                                                        crypto_accounts)):
        # Canada: s.47 averaging across the exchanges; USA (only with a
        # move of coins between them): FIFO per account with no
        # wash-sale rule, blended so the move carries its lots
        # (US-CRYPTO-05) — the pipeline's blended crypto pass.
        out.update(_recompute_blended(cache, crypto_accounts, year,
                                      settings, basis, run_gains_cmd,
                                      per_account_basis=crypto_no_wash,
                                      option_timing=option_timing,
                                      crypto=True,
                                      no_wash=crypto_no_wash))
    else:
        for a in crypto_accounts:
            out[a] = recompute_year(cache, a, year, settings, basis,
                                    run_gains_cmd,
                                    no_wash=crypto_no_wash,
                                    option_timing=option_timing,
                                    crypto=True)
    out.update(_recompute_blended(cache, equity_accounts, year, settings,
                                  basis, run_gains_cmd,
                                  option_timing=option_timing))
    return out


def _recompute_blended(cache: Path, accounts: List[str], year: int,
                       settings: Dict[str, Any], basis: str,
                       run_gains_cmd, *,
                       per_account_basis: Optional[bool] = None,
                       option_timing: Optional[Dict[str, Any]] = None,
                       crypto: bool = False,
                       no_wash: bool = False
                       ) -> Dict[str, Optional[Dict[str, Any]]]:
    """ONE combined gains run over `accounts`' base books, split back
    into per-account aggregates (the pipeline's blended pass)."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    present = [(a, cache / f"{a}_base.json") for a in accounts]
    for a, b in present:
        if not b.exists():
            cli_diag.warn(PROG, f"{a}: no work/{b.name} — cannot "
                                f"recompute",
                          details=["Run `tjs run` first."])
            out[a] = None
    present = [(a, b) for a, b in present if b.exists()]
    if not present:
        return out
    combined = {"transactions": []}
    for _a, b in present:
        combined["transactions"].extend(
            json.loads(b.read_text(encoding="utf-8"))
            .get("transactions", []))
    country = _canonical_country(settings)
    tax_date = _tax_date(settings, country)
    cmd = ["--country", str(country), "--year", str(year),
           "--tax-date", tax_date, "--taxable"]
    if per_account_basis is None:
        per_account_basis = country == "usa"
    if per_account_basis:
        cmd.append("--per-account-basis")
    if no_wash:
        cmd.append("--no-wash")
    sheltered = cache / "sheltered_base.json"
    if basis == "wash-adjusted" and sheltered.exists() and not no_wash:
        cmd += ["--sheltered", str(sheltered)]
    cmd += _lock_timing_flags(settings, year, option_timing)
    cmd += income_dating_flags(as_settings(settings).replace(country=country))
    cmd += locked_year_flags(cache.parent, settings)
    # The filing positions the books took (.tt ALLOWLOSS lines).
    from taxjson.lib.loss_overrides import flags as _lo_flags
    cmd += _lo_flags(cache)
    # The missing-history .tt lines are read through the PROJECT
    # ROOT (cache is <root>/work) — looking in work/ made
    # close-year snapshot WITH the missing-history openings and
    # check-filed recompute WITHOUT them: a guaranteed false DRIFT on
    # every such project (2026-09 audit).
    from taxjson.lib.missing_history import missing_history_arg
    mh_file = missing_history_arg(cache.parent)
    if mh_file is not None:
        cmd += ["--incomplete-history", str(mh_file)]
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "combined_base.json"
        src.write_text(json.dumps(combined), encoding="utf-8")
        cmd.append(str(src))
        outp = Path(td) / "recomputed_gains.json"
        run_gains_cmd(cmd, outp)
        doc = json.loads(outp.read_text(encoding="utf-8"))
    for a, _b in present:
        out[a] = aggregates_from_gains(doc, account=a, crypto=crypto,
                                       year=int(year))
    return out


def recompute_year(cache: Path, account: str, year: int,
                   settings: Dict[str, Any], basis: str,
                   run_gains_cmd, *,
                   no_wash: bool = False,
                   option_timing: Optional[Dict[str, Any]] = None,
                   crypto: bool = False
                   ) -> Optional[Dict[str, Any]]:
    """Aggregates for `account`/`year` recomputed from the CURRENT base
    book via the gains engine. `run_gains_cmd(cmd_argv, out_path)`
    executes the CLI (injected so the wrapper supplies its dispatch).
    Returns None (with a warning) when the base book is missing."""
    base = cache / f"{account}_base.json"
    if not base.exists():
        cli_diag.warn(PROG, f"{account}: no work/{base.name} — cannot "
                            f"recompute",
                      details=["Run `tjs run` first."])
        return None
    # Canonical spelling: "Canada"/"CA" passed raw died in the engine's
    # argparse and disabled the drift guard (S031-24).
    country = _canonical_country(settings)
    tax_date = _tax_date(settings, country)
    cmd = ["--country", str(country), "--year", str(year),
           "--tax-date", tax_date, "--taxable"]
    if no_wash:
        cmd.append("--no-wash")
    sheltered = cache / "sheltered_base.json"
    if basis == "wash-adjusted" and sheltered.exists():
        cmd += ["--sheltered", str(sheltered)]
    cmd += _lock_timing_flags(settings, year, option_timing)
    cmd += income_dating_flags(as_settings(settings).replace(country=country))
    cmd += locked_year_flags(cache.parent, settings)
    # The filing positions the books took (.tt ALLOWLOSS lines).
    from taxjson.lib.loss_overrides import flags as _lo_flags
    cmd += _lo_flags(cache)
    # The missing-history .tt lines are read through the PROJECT ROOT
    # (see _recompute_blended above).
    from taxjson.lib.missing_history import missing_history_arg
    mh_file = missing_history_arg(cache.parent)
    if mh_file is not None:
        cmd += ["--incomplete-history", str(mh_file)]
    cmd.append(str(base))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "recomputed_gains.json"
        run_gains_cmd(cmd, out)
        doc = json.loads(out.read_text(encoding="utf-8"))
    return aggregates_from_gains(doc, crypto=crypto, year=int(year))


# The per-account figures a lock records (any one makes the entry
# comparable; a pre-upgrade lock may lack the later ones).
_LOCKED_KEYS = ("realized", "disallowed", "dispositions", "income",
                "dividend", "pil", "proceeds", "st_gain", "lt_gain",
                "tainted", "form_lines")


def diff_snapshot(snapshot: Dict[str, Any],
                  recomputed: Dict[str, Optional[Dict[str, Any]]],
                  unconfigured: Optional[set] = None,
                  notes: Optional[List[str]] = None
                  ) -> List[str]:
    """Human-readable drift lines ([] == clean).

    Covers BOTH directions: a locked account the books no longer have
    (missing book), and an account in `recomputed` that the lock does
    not list but that has activity in the filed year (added after
    close-year, renamed, or left out) — its dispositions were never on
    the locked totals, so check-filed must not say OK.

    `notes` (a list) collects what is compared differently and is not
    drift: a 2024 lock closed before the Period 1 line codes compared
    on the Period 2 codes, digital-asset box totals a pre-2025-split
    lock did not record."""
    lines: List[str] = []
    locked = snapshot.get("accounts", {})
    for acct, cur in sorted(recomputed.items()):
        if acct in locked or cur is None:
            continue
        active = (int(cur.get("dispositions") or 0)
                  or int(cur.get("tainted") or 0)
                  or any(M.cents_apart(cur.get(k) or 0.0, 0) > _TOL_CENTS
                         for k in ("realized", "disallowed", "income",
                                   "proceeds")))
        if active:
            lines.append(
                f"{acct}: in the books but not in the filed lock — "
                f"{int(cur.get('dispositions') or 0)} disposition(s), "
                f"realized {M.shown(float(cur.get('realized') or 0.0), 2):,.2f}, "
                f"income {M.shown(float(cur.get('income') or 0.0), 2):,.2f} "
                f"(added or renamed after close-year?)")
    for acct, filed in sorted(locked.items()):
        cur = recomputed.get(acct)
        if unconfigured and acct in unconfigured:
            lines.append(f"{acct}: in the filed lock but no longer a "
                         f"taxable account in taxjson.toml (renamed or "
                         f"removed?) — not recomputed from its old "
                         f"work/{acct}_base.json; restore the name or "
                         f"re-close the year")
            continue
        if cur is None:
            lines.append(f"{acct}: in the filed lock but could not "
                         f"recompute (missing book — account renamed "
                         f"or removed?)")
            continue
        # An entry that records NONE of the locked totals compared
        # nothing, and a form_lines that is not a table compared no
        # line: both said "OK (matches)" over real drift (A2-0347,
        # A2-0668). A damaged lock is reported, never a match.
        if not any(k in filed for k in _LOCKED_KEYS):
            raise ValueError(
                f"accounts.{acct} records none of the locked totals "
                f"({', '.join(_LOCKED_KEYS[:3])}, ...) — the lock is "
                f"damaged")
        if "form_lines" in filed and not isinstance(
                filed.get("form_lines"), dict):
            raise ValueError(
                f"accounts.{acct}.form_lines is "
                f"{type(filed.get('form_lines')).__name__}, not a table "
                f"of line amounts — the lock is damaged")
        for key in ("realized", "disallowed", "income", "dividend",
                    "pil", "proceeds", "st_gain", "lt_gain"):
            if key not in filed or key not in cur:
                continue        # pre-upgrade lock: field not recorded
            if not finite_num(filed[key]):
                # A NaN compares equal to nothing and drifts from
                # nothing: said OK over any figure (2026-10 audit B38).
                raise ValueError(
                    f"accounts.{acct}.{key} is {filed[key]!r}, not a "
                    f"finite number — the lock is damaged")
            if M.cents_apart(filed[key], cur[key]) > _TOL_CENTS:
                lines.append(
                    f"{acct}: {key} filed {M.shown(filed[key], 2):,.2f} -> now "
                    f"{M.shown(cur[key], 2):,.2f} (drift "
                    f"{M.shown(cur[key] - filed[key], 2):+,.2f})")
        fl_filed = filed.get("form_lines")
        fl_cur = cur.get("form_lines")
        if isinstance(fl_filed, dict) and isinstance(fl_cur, dict):
            _folded = _fold_old_2024(fl_filed, fl_cur)
            if _folded is not None:
                fl_cur = _folded
                if notes is not None:
                    notes.append(
                        f"{acct}: the lock puts every 2024 disposition on "
                        f"the Period 2 lines (13199/13200, 15199/15300); "
                        f"the 2024 Schedule 3 puts January 1 - June 24 on "
                        f"the Period 1 lines (10689/10690, 10693/10694) — "
                        f"compared on the Period 2 lines (same dollars; "
                        f"see `tjs form-export` for the split)")
            if not any("_da_" in k for k in fl_filed):
                # Locked before the 8949 digital-asset boxes (A2-0482):
                # the part totals still compare every row.
                fl_cur = {k: v for k, v in fl_cur.items()
                          if "_da_" not in k}
            for code in sorted(set(fl_filed) | set(fl_cur)):
                if code in fl_filed and not finite_num(fl_filed[code]):
                    raise ValueError(
                        f"accounts.{acct}.form_lines.{code} is "
                        f"{fl_filed[code]!r}, not a finite number — the "
                        f"lock is damaged")
                a = float(fl_filed.get(code) or 0.0)
                b = float(fl_cur.get(code) or 0.0)
                if M.cents_apart(a, b) > _TOL_CENTS:
                    lines.append(f"{acct}: form line {code} filed "
                                 f"{M.shown(a, 2):,.2f} -> now {M.shown(b, 2):,.2f} (drift "
                                 f"{M.shown(b - a, 2):+,.2f})")
        for key in ("dispositions", "tainted"):
            if key == "tainted" and not snapshot.get(
                    "tainted_counts_manual"):
                continue        # pre-fix lock: its count missed them
            if key not in filed:
                continue        # not recorded (hand-written lock)
            if not finite_num(filed[key]):
                raise ValueError(
                    f"accounts.{acct}.{key} is {filed[key]!r}, not a "
                    f"finite number — the lock is damaged")
            if int(filed[key]) != int(cur.get(key) or 0):
                lines.append(f"{acct}: {key} filed {filed[key]} -> now "
                             f"{cur[key]}")
    return lines


def lock_for_year(root: Path, settings: Dict[str, Any], year: int
                  ) -> Optional[Tuple[Path, str]]:
    """(path, where) of the lock for `year` (project_locks), or None."""
    for y, p, w in project_locks(root, settings):
        if y == int(year):
            return p, w
    return None
