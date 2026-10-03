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
total disallowed, disposition count, dividend and PIL income, tainted
count, the engine's net proceeds, ST/LT gain, and the form lines the
export puts on the return (Schedule 3 line codes, or Form 8949 part
totals). NOT locked: interest, foreign tax withheld and the FX gain on
foreign cash (line 15300 / §988) — check-filed says so with every OK.
"""

from taxjson.lib.pipeline import income_dating_flags, option_timing_flags
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib import cli_diag

PROG = "taxjson-filed"
_TOL = 0.01


# What every OK line says the lock does not cover (S032-11).
NOT_LOCKED = ("interest, foreign tax withheld and the FX gain on foreign "
              "cash are not locked")


def form_lines(entries: List[Dict[str, Any]], *, crypto: bool = False,
               year: Optional[int] = None,
               date_key: str = "date_settle") -> Dict[str, float]:
    """The amounts the export puts on the return for these disposition
    entries: Schedule 3 {proceeds/gain line code: amount} (Canadian
    gains; the 2024 form's Period 1 codes 10689/10690 and 10693/10694
    apart, A2-0166), or Form 8949 part totals {"I_proceeds": ..} (US
    gains carry a term), with, from 2025, the digital-asset boxes' own
    totals {"I_da_proceeds": ..} (boxes G-L, A2-0482). The lock records
    them so a change that moves amounts BETWEEN lines (13199 vs 15199 vs
    15200), or changes how proceeds are presented (outlays, short
    sales), is drift even when the total gain is unchanged (R1-205,
    R1-281)."""
    import contextlib
    import io
    from taxjson.bin import taxjson_form_export as FE
    if not entries:
        return {}
    marked = FE.mark_crypto(entries) if crypto else entries
    with contextlib.redirect_stderr(io.StringIO()):
        if any(e.get("term") in ("SHORT_TERM", "LONG_TERM")
               for e in entries):
            rep = FE.build_8949(marked, year)
            out = {f"{part}_{k}": float(v)
                   for part in ("I", "II")
                   for k, v in (rep.get(f"part_{part}_totals") or {}).items()
                   if rep.get(f"part_{part}")}
            for g in rep.get("groups") or []:
                if g.get("digital_asset"):
                    for k, v in g["totals"].items():
                        out[f"{g['part']}_da_{k}"] = float(v)
            if rep.get("section_1256"):
                # §1256 contracts are off Form 8949 (Form 6781 by hand,
                # US-FUT-02): their net is locked on its own line.
                out["6781_gain"] = float(
                    rep["section_1256_totals"]["gain"])
            return out
        rep = FE.build_schedule3(marked, year, date_key)
    return {k.split("_", 1)[1]: float(v)
            for k, v in (rep.get("totals") or {}).items()
            if k not in ("proceeds_all", "gain_all")}


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
        out[k2] = round(out.get(k2, 0.0) + float(v or 0.0), 2)
    return out


def aggregates_from_gains(doc: Dict[str, Any],
                          account: Optional[str] = None, *,
                          crypto: bool = False,
                          year: Optional[int] = None,
                          rounded: bool = True) -> Dict[str, Any]:
    """The filing-relevant aggregate set from one gains document —
    optionally restricted to one account's entries (used when the
    document is a blended combined run). `crypto`: the account's
    dispositions go on the crypto-asset line. `rounded=False` returns
    the money totals unrounded (close-year sums the accounts and rounds
    ONCE — S031-20)."""
    if not isinstance(doc, dict):
        raise ValueError("not a gains document (expected a JSON object)")
    # The date the file was year-scoped on also places a 2024
    # disposition in Period 1 or 2 of the 2024 Schedule 3 (A2-0166).
    _basis = str(((doc.get("summary") or {}) if isinstance(
        doc.get("summary"), dict) else {}).get("tax_date_basis") or "")
    date_key = "date" if _basis == "trade" else "date_settle"
    entries: List[Dict[str, Any]] = []
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
        entries.append(e)
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
    # Unknown-cost dispositions: the pipeline MOVES them to
    # manual_reporting_required (its 'tainted' key popped), so the
    # 'tainted' test above only sees hand-run files. Count both.
    for e in doc.get("manual_reporting_required") or []:
        if account is not None and e.get("account") != account:
            continue
        tainted += 1
    income = dividend + pil
    if not rounded:
        return {"realized": realized, "disallowed": disallowed,
                "income": income, "dividend": dividend, "pil": pil,
                "proceeds": proceeds, "st_gain": st_gain,
                "lt_gain": lt_gain}
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
        # The ENGINE's net proceeds (sell-side costs netted, a short
        # cover's negated cost) — not a return line; the return's lines
        # are in form_lines (R1-281). A US ST<->LT term flip changes tax
        # owed with realized unchanged, so ST/LT are locked too.
        "proceeds": round(proceeds, 2),
        "st_gain": round(st_gain, 2),
        "lt_gain": round(lt_gain, 2),
        "form_lines": {k: round(v, 2) for k, v in
                       form_lines(entries, crypto=crypto, year=year,
                                  date_key=date_key).items()},
    }


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
        sys.exit(f"taxjson close-year: {path} already exists — the "
                 f"lock protects a filed year. Re-run with --force to "
                 f"replace it (only if you re-filed/amended).")
    _money = ("realized", "disallowed", "income", "dividend", "pil",
              "proceeds", "st_gain", "lt_gain")
    src = raw if raw else accounts
    totals = {
        k: round(sum(a.get(k, 0) for a in src.values()), 2)
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        # filed/ is a file, or the project is read-only: one line, not a
        # traceback (S031-19). Nothing was written.
        try:
            tmp.unlink()
        except OSError:
            pass
        sys.exit(f"taxjson close-year: cannot write {path}: {e} — "
                 f"nothing was written.")
    return path


def lock_country_problem(lock: Dict[str, Any], settings: Dict[str, Any],
                         label: str) -> Optional[str]:
    """A refusal message when a filed-year lock (or a prior-year
    record) was closed under the OTHER country than this project's, else
    None. Every lock close-year writes records its country; recomputing
    a US-filed year under Canadian rules (or the reverse) reported every
    difference between the two laws as drift and advised amending or
    refreshing the lock — which would overwrite the filed record with
    the other country's numbers (partition COMMANDS-08). A lock without
    a country (hand-written, pre-country) is not judged."""
    from taxjson.lib.country import (CountryError, canonical_country,
                                     display_name, settings_country)
    rc = lock.get("country") if isinstance(lock, dict) else None
    if rc in (None, ""):
        return None
    here = settings_country(settings)
    try:
        rcc = canonical_country(rc, what=f"{label} country")
    except CountryError as e:
        return f"{e} — the lock cannot be checked against this project"
    if rcc == here:
        return None
    return (f"{label} was closed under country = \"{rcc}\" "
            f"({display_name(rcc)} law) but this project is country = "
            f"\"{here}\" ({display_name(here)}): a return filed under one "
            f"country's rules cannot be checked against the other's, so "
            f"it is not recomputed. Set [settings] country = \"{rcc}\" to "
            f"check it; do NOT refresh it with `close-year --force` (that "
            f"would overwrite the filed {display_name(rcc)} record).")


def partial_year_note(lock: Dict[str, Any], year) -> Optional[str]:
    """When the lock was taken on or before Dec 31 of its own year
    (`close-year --force` on an open year): a sentence saying it is a
    snapshot, not a filed return — every lock consumer says so instead
    of certifying it (S045-23, A2-0679, A2-1164). None otherwise, and
    for a lock without a readable closed_at."""
    if not isinstance(lock, dict):
        return None
    at = str(lock.get("closed_at") or "")[:10]
    try:
        y = int(lock.get("year") or year)
    except (TypeError, ValueError):
        return None
    if len(at) != 10 or at > f"{y}-12-31":
        return None
    return (f"the {y} lock was taken on {at}, before the year ended "
            f"(close-year --force) — a snapshot, not a filed return; "
            f"close the year again after filing")


def lock_settings(lock: Dict[str, Any], settings: Dict[str, Any]
                  ) -> Dict[str, Any]:
    """The settings a lock is recomputed under: this project's, with the
    date basis the lock RECORDED (close-year writes `date_basis`) — a
    year filed on settlement dates is checked on settlement dates even
    after the project switched tax_date."""
    rb = lock.get("date_basis") if isinstance(lock, dict) else None
    if rb in ("settle", "trade"):
        return dict(settings, tax_date=rb)
    return settings


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
                                      option_timing=option_timing,
                                      crypto=True))
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
                       crypto: bool = False
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
    cmd += income_dating_flags(dict(settings, country=country))
    cmd += locked_year_flags(cache.parent, settings)
    # missing_history.json (or its old name phantoms.json) lives at the
    # PROJECT ROOT (cache is <root>/work) — looking in work/ made
    # close-year snapshot WITH the missing-history openings and
    # check-filed recompute WITHOUT them: a guaranteed false DRIFT on
    # every such project (2026-09 audit).
    from taxjson.lib.missing_history import missing_history_path
    mh_file = missing_history_path(cache.parent)
    if mh_file.exists():
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
    cmd += income_dating_flags(dict(settings, country=country))
    cmd += locked_year_flags(cache.parent, settings)
    # missing_history.json (or its old name) lives at the PROJECT ROOT
    # (see _recompute_blended above).
    from taxjson.lib.missing_history import missing_history_path
    mh_file = missing_history_path(cache.parent)
    if mh_file.exists():
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
            if abs(float(filed[key]) - float(cur[key])) > _TOL:
                lines.append(
                    f"{acct}: {key} filed {filed[key]:,.2f} -> now "
                    f"{cur[key]:,.2f} (drift "
                    f"{cur[key] - filed[key]:+,.2f})")
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
                        f"see `taxjson form-export` for the split)")
            if not any("_da_" in k for k in fl_filed):
                # Locked before the 8949 digital-asset boxes (A2-0482):
                # the part totals still compare every row.
                fl_cur = {k: v for k, v in fl_cur.items()
                          if "_da_" not in k}
            for code in sorted(set(fl_filed) | set(fl_cur)):
                a = float(fl_filed.get(code) or 0.0)
                b = float(fl_cur.get(code) or 0.0)
                if abs(a - b) > _TOL:
                    lines.append(f"{acct}: form line {code} filed "
                                 f"{a:,.2f} -> now {b:,.2f} (drift "
                                 f"{b - a:+,.2f})")
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


class PriorRecordError(ValueError):
    """[settings] prior_year_record is not a path string."""


def prior_record_setting(root: Path, settings: Dict[str, Any]
                         ) -> Optional[Path]:
    """The lock [settings] prior_year_record names (per-year project
    layout: "../2025/filed/2025.json"), resolved against the project
    root; None when the key is unset. A non-string value raises
    PriorRecordError with the same text `taxjson run` refuses it with —
    handoff read 5 as the path <root>/5 (A2-1135)."""
    configured = (settings or {}).get("prior_year_record")
    if configured is None or configured == "":
        return None
    if not isinstance(configured, str):
        raise PriorRecordError(
            f"[settings] prior_year_record must be a path string "
            f"(got {configured!r}).")
    p = Path(configured).expanduser()
    return p if p.is_absolute() else (Path(root) / p)


def _lock_year(path: Path) -> Optional[int]:
    """The tax year a lock file records: its "year" key, else its
    <year>.json name. None when neither says."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("year") is not None:
            return int(doc["year"])
    except (OSError, ValueError, TypeError):
        pass
    try:
        return int(Path(path).stem)
    except ValueError:
        return None


def project_locks(root: Path, settings: Dict[str, Any]
                  ) -> List[Tuple[int, Path, str]]:
    """Every filed-year lock this project answers to, as (year, path,
    where): the project's own filed/<year>.json ("local"), plus the lock
    [settings] prior_year_record names ("prior_year_record") when no
    local lock covers its year. In the documented per-year layout last
    year's lock lives only in the previous project; readers that looked
    only under <project>/filed/ (option-boundary, audit --year,
    carryover, the grant-since hint) said "no filed-year locks" and gave
    advice the lock contradicted (A2-0036, A2-0335, A2-0338, A2-0664).
    A configured record that does not exist is left out (handoff and the
    checklist report it); a non-string setting raises
    PriorRecordError."""
    out: List[Tuple[int, Path, str]] = [
        (y, p, "local") for y, p in list_snapshots(root)]
    pr = prior_record_setting(root, settings)
    if pr is not None and pr.is_file():
        try:
            same = any(pr.resolve() == p.resolve() for _y, p, _w in out)
        except OSError:
            same = False
        y = _lock_year(pr)
        if y is None:
            try:
                y = int((settings or {}).get("year")) - 1
            except (TypeError, ValueError):
                y = None
        if (y is not None and not same
                and y not in {yy for yy, _p, _w in out}):
            out.append((y, pr, "prior_year_record"))
    out.sort(key=lambda t: t[0])
    return out


def locked_year_flags(root: Path, settings: Dict[str, Any]) -> List[str]:
    """`--locked-year Y` for every filed-year lock of a US project (the
    gains engine then books a wash-sale basis add that reaches a sale
    in a filed year in the loss's year instead, US-WASH-22). Canada
    has no such flag ([] — lib/country.FLAG_COUNTRY)."""
    if _canonical_country(settings) != "usa":
        return []
    try:
        locks = project_locks(root, settings)
    except ValueError:
        return []
    out: List[str] = []
    for y, _p, _w in locks:
        out += ["--locked-year", str(y)]
    return out


def lock_for_year(root: Path, settings: Dict[str, Any], year: int
                  ) -> Optional[Tuple[Path, str]]:
    """(path, where) of the lock for `year` (project_locks), or None."""
    for y, p, w in project_locks(root, settings):
        if y == int(year):
            return p, w
    return None


def lock_label(root: Path, path: Path) -> str:
    """A short name for a lock in messages: filed/<year>.json for the
    project's own, else the path as configured."""
    try:
        return str(Path(path).relative_to(Path(root)))
    except ValueError:
        return str(path)


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
