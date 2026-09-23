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
    proceeds = st_gain = lt_gain = 0.0
    dispositions = tainted = 0
    for e in doc.get("transactions", []):
        if account is not None and e.get("account") != account:
            continue
        action = e.get("action") or ""
        if action in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
            income += float(e.get("dividend") or 0.0)
            income += float(e.get("pil") or 0.0)
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
    return {
        "realized": round(realized, 2),
        "disallowed": round(disallowed, 2),
        "dispositions": dispositions,
        "income": round(income, 2),
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
                   force: bool) -> Path:
    path = snapshot_path(root, year)
    if path.exists() and not force:
        sys.exit(f"taxjson close-year: {path} already exists — the "
                 f"lock protects a filed year. Re-run with --force to "
                 f"replace it (only if you re-filed/amended).")
    path.parent.mkdir(parents=True, exist_ok=True)
    totals = {
        k: round(sum(a.get(k, 0) for a in accounts.values()), 2)
        if k in ("realized", "disallowed", "income", "proceeds",
                 "st_gain", "lt_gain")
        else sum(a.get(k, 0) for a in accounts.values())
        for k in ("realized", "disallowed", "dispositions", "income",
                  "tainted", "proceeds", "st_gain", "lt_gain")
    }
    doc = {
        "schema_version": 1,
        "year": int(year),
        "country": country,
        "basis": basis,
        "closed_at": datetime.now().isoformat(timespec="seconds"),
        "accounts": accounts,
        "totals": totals,
    }
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return path


def recompute_accounts(cache: Path, equity_accounts: List[str],
                       crypto_accounts: List[str], year: int,
                       settings: Dict[str, Any], basis: str,
                       run_gains_cmd) -> Dict[str, Optional[Dict[str, Any]]]:
    """Recompute a filed year's aggregates the way the pipeline
    computed them at close time: equity accounts via ONE blended
    combined run (Canada s.47 ACB blending; US --per-account-basis) —
    recomputing each in isolation falsely drifted every multi-account
    book — and crypto accounts per-account (their books never blend)."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    # US crypto runs --taxable --no-wash in the pipeline (§1091 does
    # not reach digital assets) — the recompute must match or every US
    # crypto account with a wash-window loss drifts on every check.
    crypto_no_wash = (str(settings.get("country", "")).strip().lower()
                      in ("us", "usa"))
    for a in crypto_accounts:
        out[a] = recompute_year(cache, a, year, settings, basis,
                                run_gains_cmd,
                                no_wash=crypto_no_wash)
    present = [(a, cache / f"{a}_base.json") for a in equity_accounts]
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
    country = str(settings["country"]).strip().lower()
    country = {"ca": "canada", "us": "usa"}.get(country, country)
    tax_date = settings.get("tax_date") or (
        "trade" if str(country).lower() in ("us", "usa") else "settle")
    cmd = ["--country", str(country), "--year", str(year),
           "--tax-date", tax_date, "--taxable"]
    if str(country).strip().lower() in ("us", "usa"):
        cmd.append("--per-account-basis")
    sheltered = cache / "sheltered_base.json"
    if basis == "wash-adjusted" and sheltered.exists():
        cmd += ["--sheltered", str(sheltered)]
    if settings.get("cross_asset"):
        cmd.append("--cross-asset")
    cmd += option_timing_flags(settings)
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
                   no_wash: bool = False) -> Optional[Dict[str, Any]]:
    """Aggregates for `account`/`year` recomputed from the CURRENT base
    book via the gains engine. `run_gains_cmd(cmd_argv, out_path)`
    executes the CLI (injected so the wrapper supplies its dispatch).
    Returns None (with a warning) when the base book is missing."""
    base = cache / f"{account}_base.json"
    if not base.exists():
        cli_diag.warn(PROG, f"{account}: no {base.name} in {cache} — "
                            f"cannot recompute; run `taxjson run` first.")
        return None
    country = settings["country"]
    tax_date = settings.get("tax_date") or (
        "trade" if str(country).lower() in ("us", "usa") else "settle")
    cmd = ["--country", str(country), "--year", str(year),
           "--tax-date", tax_date, "--taxable"]
    if no_wash:
        cmd.append("--no-wash")
    sheltered = cache / "sheltered_base.json"
    if basis == "wash-adjusted" and sheltered.exists():
        cmd += ["--sheltered", str(sheltered)]
    if settings.get("cross_asset"):
        cmd.append("--cross-asset")
    cmd += option_timing_flags(settings)
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
                  recomputed: Dict[str, Optional[Dict[str, Any]]]
                  ) -> List[str]:
    """Human-readable drift lines ([] == clean)."""
    lines: List[str] = []
    for acct, filed in sorted(snapshot.get("accounts", {}).items()):
        cur = recomputed.get(acct)
        if cur is None:
            lines.append(f"{acct}: could not recompute (missing book)")
            continue
        for key in ("realized", "disallowed", "income", "proceeds",
                    "st_gain", "lt_gain"):
            if key not in filed:
                continue        # pre-upgrade lock: field not recorded
            if abs(float(filed[key]) - float(cur[key])) > _TOL:
                lines.append(
                    f"{acct}: {key} filed {filed[key]:,.2f} -> now "
                    f"{cur[key]:,.2f} (drift "
                    f"{cur[key] - filed[key]:+,.2f})")
        for key in ("dispositions", "tainted"):
            if int(filed[key]) != int(cur[key]):
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
