#!/usr/bin/env python3
"""Filed-year lock: snapshot a filed tax year and detect drift.

The pipeline recomputes full history on every run — correct, but it
means a code upgrade, a parser fix, or an input edit can silently change
an ALREADY-FILED year's numbers. This module gives that a lock:

  taxjson close-year          snapshot the current tax year's per-account
                              filing aggregates to filed/<year>.json
                              (commit it with your records)
  taxjson check-filed         recompute every filed year from the CURRENT
                              books and report any drift vs the snapshots

A full `taxjson run` auto-checks at the end (warn-only; fatal under
--strict), so "did my filed 2025 numbers just move?" becomes a report
line instead of archaeology. Drift is not automatically wrong — a
legitimate fix can move a filed year — but it always deserves eyes, and
usually an amended return or a refreshed snapshot (`close-year
--force`).

Compared aggregates per taxable account (engine-recomputed, matching
the filing basis recorded at close time): realized total (non-tainted),
total disallowed, disposition count, dividend+PIL income, tainted count.
"""

from taxjson.lib.pipeline import option_timing_flags
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib import cli_diag

PROG = "taxjson-filed"
_TOL = 0.01


def aggregates_from_gains(doc: Dict[str, Any],
                          account: Optional[str] = None) -> Dict[str, Any]:
    """The filing-relevant aggregate set from one gains document —
    optionally restricted to one account's entries (used when the
    document is a blended combined run)."""
    realized = disallowed = income = 0.0
    dividend = pil = 0.0
    proceeds = st_gain = lt_gain = 0.0
    dispositions = tainted = 0
    for e in doc.get("transactions", []):
        if account is not None and e.get("account") != account:
            continue
        action = e.get("action") or ""
        if action in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            dividend += float(e.get("dividend") or 0.0)
            pil += float(e.get("pil") or 0.0)
            continue
        if "gain" not in e or "qty" not in e:
            continue
        if e.get("tainted"):
            tainted += 1
            continue
        realized += float(e.get("gain") or 0.0)
        disallowed += float(e.get("disallowed_amount")
                            or e.get("disallowed") or 0.0)
        dispositions += 1
        proceeds += float(e.get("proceeds") or 0.0)
        term = str(e.get("term") or "")
        if term == "SHORT_TERM":
            st_gain += float(e.get("gain") or 0.0)
        elif term == "LONG_TERM":
            lt_gain += float(e.get("gain") or 0.0)
    # Phantom-basis dispositions: the pipeline MOVES them to
    # manual_reporting_required (its 'tainted' key popped), so the
    # 'tainted' test above only sees hand-run files. Count both.
    for e in doc.get("manual_reporting_required") or []:
        if account is not None and e.get("account") != account:
            continue
        tainted += 1
    income = dividend + pil
    return {
        "realized": round(realized, 2),
        "disallowed": round(disallowed, 2),
        "dispositions": dispositions,
        "income": round(income, 2),
        # A dividend and a payment in lieu go on different lines (a PIL
        # is not a taxable dividend: no gross-up, no dividend tax
        # credit), so a reclassification between them moves the filed
        # return even when their sum does not (S032-06).
        "dividend": round(dividend, 2),
        "pil": round(pil, 2),
        "tainted": tainted,
        # Round-five audit: gain-total-preserving drift still moves
        # FILED figures — Schedule 3 line 13199 / 8949 (d) move with
        # proceeds, and a US ST<->LT term flip changes tax owed with
        # realized unchanged. Snapshot them so check-filed sees both.
        "proceeds": round(proceeds, 2),
        "st_gain": round(st_gain, 2),
        "lt_gain": round(lt_gain, 2),
    }


def snapshot_path(root: Path, year) -> Path:
    return Path(root) / "filed" / f"{year}.json"


def write_snapshot(root: Path, year, country: str, basis: str,
                   accounts: Dict[str, Dict[str, Any]], *,
                   force: bool,
                   option_timing: Optional[Dict[str, Any]] = None,
                   extra: Optional[Dict[str, Any]] = None) -> Path:
    """Write filed/<year>.json. `option_timing` records the written-option
    premium timing the return used (Canada), so a later project's
    `option-boundary` can tell a year filed under grant timing from one
    filed under close timing."""
    path = snapshot_path(root, year)
    if path.exists() and not force:
        sys.exit(f"taxjson close-year: {path} already exists — the "
                 f"lock protects a filed year. Re-run with --force to "
                 f"replace it (only if you re-filed/amended).")
    path.parent.mkdir(parents=True, exist_ok=True)
    totals = {
        k: round(sum(a.get(k, 0) for a in accounts.values()), 2)
        if k in ("realized", "disallowed", "income", "dividend", "pil",
                 "proceeds", "st_gain", "lt_gain")
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
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return path


def _canonical_country(settings: Dict[str, Any]) -> str:
    """lib/country.settings_country: missing / unknown raises."""
    from taxjson.lib.country import settings_country
    return settings_country(settings)


def _tax_date(settings: Dict[str, Any], country: str) -> str:
    """[settings] tax_date as the gains engine spells it. An explicit
    value that is not settle/trade (any case) is refused by name — it
    reached the engine raw and died with an argparse usage line that
    the checklist then reported as drift (S031-21)."""
    raw = settings.get("tax_date")
    if raw in (None, ""):
        from taxjson.lib.country import default_tax_date
        return default_tax_date(country)
    td = str(raw).strip().lower()
    if td not in ("settle", "trade"):
        raise ValueError(f"[settings] tax_date must be settle|trade, "
                         f"got {raw!r}")
    return td


def _lock_timing_flags(settings: Dict[str, Any], year: int,
                       option_timing: Optional[Dict[str, Any]]
                       ) -> List[str]:
    """The written-option timing flags for recomputing locked `year`:
    the timing its lock RECORDED when it has one, else the current
    settings. Recomputing a year filed on grant timing since 2025 with a
    later project's default (since = that project's year) moved the
    filed year's gains and advised amending a correct return (R1-188)."""
    if isinstance(option_timing, dict) \
            and option_timing.get("option_premium_timing"):
        s = dict(settings)
        s["option_premium_timing"] = option_timing["option_premium_timing"]
        since = option_timing.get("option_grant_since")
        s["option_grant_timing_since"] = since if since is not None \
            else year
        if "option_buyback_loss_superficial" in option_timing:
            s["option_buyback_loss_superficial"] = bool(
                option_timing["option_buyback_loss_superficial"])
        return option_timing_flags(s)
    return option_timing_flags(settings)


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
    if not crypto_no_wash and len(crypto_accounts) >= 2:
        out.update(_recompute_blended(cache, crypto_accounts, year,
                                      settings, basis, run_gains_cmd,
                                      per_account_basis=False,
                                      option_timing=option_timing))
    else:
        for a in crypto_accounts:
            out[a] = recompute_year(cache, a, year, settings, basis,
                                    run_gains_cmd,
                                    no_wash=crypto_no_wash,
                                    option_timing=option_timing)
    out.update(_recompute_blended(cache, equity_accounts, year, settings,
                                  basis, run_gains_cmd,
                                  option_timing=option_timing))
    return out


def _recompute_blended(cache: Path, accounts: List[str], year: int,
                       settings: Dict[str, Any], basis: str,
                       run_gains_cmd, *,
                       per_account_basis: Optional[bool] = None,
                       option_timing: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Optional[Dict[str, Any]]]:
    """ONE combined gains run over `accounts`' base books, split back
    into per-account aggregates (the pipeline's blended pass)."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    present = [(a, cache / f"{a}_base.json") for a in accounts]
    for a, b in present:
        if not b.exists():
            cli_diag.warn(PROG, f"{a}: no {b.name} in {cache} — cannot "
                                f"recompute; run `taxjson run` first.")
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
    sheltered = cache / "sheltered_base.json"
    if basis == "wash-adjusted" and sheltered.exists():
        cmd += ["--sheltered", str(sheltered)]
    cmd += _lock_timing_flags(settings, year, option_timing)
    # phantoms.json lives at the PROJECT ROOT (cache is
    # <root>/work) — looking in work/ made close-year snapshot WITH
    # phantom openings and check-filed recompute WITHOUT them: a
    # guaranteed false DRIFT on every phantom project (2026-09 audit).
    phantoms = cache.parent / "phantoms.json"
    if phantoms.exists():
        cmd += ["--incomplete-history", str(phantoms)]
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "combined_base.json"
        src.write_text(json.dumps(combined), encoding="utf-8")
        cmd.append(str(src))
        outp = Path(td) / "recomputed_gains.json"
        run_gains_cmd(cmd, outp)
        doc = json.loads(outp.read_text(encoding="utf-8"))
    for a, _b in present:
        out[a] = aggregates_from_gains(doc, account=a)
    return out


def recompute_year(cache: Path, account: str, year: int,
                   settings: Dict[str, Any], basis: str,
                   run_gains_cmd, *,
                   no_wash: bool = False,
                   option_timing: Optional[Dict[str, Any]] = None
                   ) -> Optional[Dict[str, Any]]:
    """Aggregates for `account`/`year` recomputed from the CURRENT base
    book via the gains engine. `run_gains_cmd(cmd_argv, out_path)`
    executes the CLI (injected so the wrapper supplies its dispatch).
    Returns None (with a warning) when the base book is missing."""
    base = cache / f"{account}_base.json"
    if not base.exists():
        cli_diag.warn(PROG, f"{account}: no {base.name} in {cache} — "
                            f"cannot recompute; run `taxjson run` first.")
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
    # phantoms.json lives at the PROJECT ROOT (cache is
    # <root>/work) — looking in work/ made close-year snapshot WITH
    # phantom openings and check-filed recompute WITHOUT them: a
    # guaranteed false DRIFT on every phantom project (2026-09 audit).
    phantoms = cache.parent / "phantoms.json"
    if phantoms.exists():
        cmd += ["--incomplete-history", str(phantoms)]
    cmd.append(str(base))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "recomputed_gains.json"
        run_gains_cmd(cmd, out)
        doc = json.loads(out.read_text(encoding="utf-8"))
    return aggregates_from_gains(doc)


def diff_snapshot(snapshot: Dict[str, Any],
                  recomputed: Dict[str, Optional[Dict[str, Any]]],
                  unconfigured: Optional[set] = None
                  ) -> List[str]:
    """Human-readable drift lines ([] == clean).

    Covers BOTH directions: a locked account the books no longer have
    (missing book), and an account in `recomputed` that the lock does
    not list but that has activity in the filed year (added after
    close-year, renamed, or left out) — its dispositions were never on
    the locked totals, so check-filed must not say OK."""
    lines: List[str] = []
    locked = snapshot.get("accounts", {})
    for acct, cur in sorted(recomputed.items()):
        if acct in locked or cur is None:
            continue
        active = (int(cur.get("dispositions") or 0)
                  or int(cur.get("tainted") or 0)
                  or any(abs(float(cur.get(k) or 0.0)) > _TOL
                         for k in ("realized", "disallowed", "income",
                                   "proceeds")))
        if active:
            lines.append(
                f"{acct}: in the books but not in the filed lock — "
                f"{int(cur.get('dispositions') or 0)} disposition(s), "
                f"realized {float(cur.get('realized') or 0.0):,.2f}, "
                f"income {float(cur.get('income') or 0.0):,.2f} "
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
        for key in ("realized", "disallowed", "income", "dividend",
                    "pil", "proceeds", "st_gain", "lt_gain"):
            if key not in filed or key not in cur:
                continue        # pre-upgrade lock: field not recorded
            if abs(float(filed[key]) - float(cur[key])) > _TOL:
                lines.append(
                    f"{acct}: {key} filed {filed[key]:,.2f} -> now "
                    f"{cur[key]:,.2f} (drift "
                    f"{cur[key] - filed[key]:+,.2f})")
        for key in ("dispositions", "tainted"):
            if key == "tainted" and not snapshot.get(
                    "tainted_counts_manual"):
                continue        # pre-fix lock: its count missed them
            if key not in filed:
                continue        # not recorded (hand-written lock)
            if int(filed[key]) != int(cur.get(key) or 0):
                lines.append(f"{acct}: {key} filed {filed[key]} -> now "
                             f"{cur[key]}")
    return lines


def list_snapshots(root: Path) -> List[Tuple[int, Path]]:
    d = Path(root) / "filed"
    out = []
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                out.append((int(p.stem), p))
            except ValueError:
                cli_diag.warn(PROG, f"ignoring non-year file {p.name}")
    return out
