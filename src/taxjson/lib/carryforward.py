"""Carry-forwards from one tax year to the next: the net capital loss
(Canada), the short-/long-term capital loss carryover (USA) and the
Canadian minimum tax (AMT) carryover (ITA s.120.2).

Where each year's balances come from, in order (explicit input always
wins; tax-logic CA-CARRY-02 / CA-AMT-08 / US-CARRY-02):

1. what the user entered — `--other-losses` / `--long-term-losses`,
   `[estimate] other_losses` / `long_term_losses`; for the minimum tax
   `[estimate] amt_carryover = { 2023 = 1200.50 }` (by year of origin,
   as on the notice of assessment / T691);
2. else the latest close-year lock BEFORE the project year
   (filed/<year>.json, or the lock [settings] prior_year_record names)
   whose `carryforwards` block close-year wrote from the same estimate
   computation (`record_block`);
3. else nothing (the estimate says so).

The arithmetic lives in lib/tax_estimate (one source of truth); this
module only reads inputs and locks, and builds / checks the lock block.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The old project-root files; only `taxjson migrate` reads them now
# (load_amt_file) — the inputs are [estimate] amt_carryover and
# [carryover] claimed in taxjson.toml.
AMT_FILE = "amt_carryover.txt"
CLAIMED_FILE = "claimed_losses.txt"
AMT_KEY = "[estimate] amt_carryover"
CLAIMED_KEY = "[carryover] claimed"
BLOCK_VERSION = 1

# A grouped amount's lead group has no leading zero; `$` allowed (the
# claimed_losses.txt rule, taxjson_carryover._AMOUNT_RE).
_AMOUNT_RE = re.compile(
    r"\$?([1-9]\d{0,2}(?:,\d{3})+|\d+)(\.\d+)?|\$?\.\d+")


class CarryInputError(ValueError):
    """An [estimate] amt_carryover / old amt_carryover.txt / lock block
    that cannot be read. One line naming the input (and line)."""


def _amount(text: str) -> float:
    if _AMOUNT_RE.fullmatch(text) is None:
        raise ValueError(text)
    v = float(text.lstrip("$").replace(",", ""))
    if not math.isfinite(v) or v < 0:
        raise ValueError(text)
    return v


def _plausible_year(y: int) -> bool:
    from datetime import date
    return 1900 <= y <= date.today().year + 1


def load_amt_file(path: Path) -> Dict[int, float]:
    """The old amt_carryover.txt (read by `taxjson migrate` only):
    `YEAR AMOUNT` lines (`#` comments) -> {year of origin: amount}.
    Loud: a line that cannot be read, a year twice or an implausible
    year raises CarryInputError naming file:line — a dropped line would
    understate the credit with no word."""
    from taxjson.lib.cli_diag import read_text_utf8
    p = Path(path)
    if p.is_dir() or (p.is_symlink() and not p.exists()):
        raise CarryInputError(f"{p.name} is not a readable file (a "
                              f"directory or a broken link) — fix or "
                              f"remove it")
    try:
        text = read_text_utf8(p)
    except (OSError, ValueError) as e:
        raise CarryInputError(f"cannot read {p.name}: {e}") from None
    out: Dict[int, float] = {}
    for n, line in enumerate(text.splitlines(), 1):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        parts = body.split()
        try:
            if len(parts) != 2:
                raise ValueError
            y, a = int(parts[0]), _amount(parts[1])
        except ValueError:
            raise CarryInputError(
                f"{p.name}:{n}: expected `YEAR AMOUNT` (the year the "
                f"minimum tax was paid, and the carryover still "
                f"unapplied, e.g. `2023 1,200.50`), got {body!r}") \
                from None
        if not _plausible_year(y):
            raise CarryInputError(f"{p.name}:{n}: {y} is not a plausible "
                                  f"year of origin")
        if y in out:
            raise CarryInputError(f"{p.name}:{n}: {y} appears twice — one "
                                  f"line per year of origin")
        out[y] = a
    return out


def amt_from_config(value: Any) -> Dict[int, float]:
    """`[estimate] amt_carryover` -> {year: amount}. Only a table by year
    of origin: a bare total has no year, and the 7-year limit needs
    one."""
    where = "[estimate] amt_carryover"
    if not isinstance(value, dict):
        raise CarryInputError(
            f"{where} must be a table by year of origin, e.g. "
            f"`amt_carryover = {{ 2023 = 1200.50, 2024 = 800 }}` (the "
            f"7-year limit needs each amount's year), got {value!r}")
    out: Dict[int, float] = {}
    for k, v in value.items():
        try:
            y = int(str(k).strip())
        except ValueError:
            raise CarryInputError(f"{where}: {k!r} is not a year") from None
        if not _plausible_year(y):
            raise CarryInputError(f"{where}: {y} is not a plausible year "
                                  f"of origin")
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)) or float(v) < 0:
            raise CarryInputError(f"{where}: {y} must be a non-negative "
                                  f"number, got {v!r}")
        out[y] = float(v)
    return out


# ------------------------------------------------------------- the locks

def prior_lock(root: Path, settings: Dict[str, Any]
               ) -> Optional[Tuple[int, Path, str]]:
    """(year, path, label) of the latest close-year lock BEFORE the
    project year: this project's filed/<year>.json or the lock
    [settings] prior_year_record names. None when there is none."""
    from taxjson.bin import taxjson_filed
    try:
        py = int((settings or {}).get("year"))
    except (TypeError, ValueError):
        return None
    try:
        locks = taxjson_filed.project_locks(root, settings)
    except ValueError:
        return None
    best = None
    for y, p, _w in locks:
        if y < py and (best is None or y > best[0]):
            best = (y, p)
    if best is None:
        return None
    return best[0], best[1], taxjson_filed.lock_label(root, best[1])


def _num(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)))


def block_problem(block: Any) -> Optional[str]:
    """None when a lock's `carryforwards` block has the shape close-year
    writes; else what is wrong (handoff's validate_record and the
    readers here use it)."""
    if not isinstance(block, dict):
        return "carryforwards is not an object"
    for sec, keys in (("net_capital_loss", ("opening", "created",
                                            "applied", "closing")),
                      ("capital_loss", ("st_opening", "lt_opening",
                                        "st_closing", "lt_closing")),
                      ("minimum_tax", ("created", "closing",
                                       "recovered_federal"))):
        s = block.get(sec)
        if s is None:
            continue
        if not isinstance(s, dict):
            return f"carryforwards.{sec} is not an object"
        for k in keys:
            if k in s and not _num(s[k]):
                return f"carryforwards.{sec}.{k} is not a number"
        for k in ("opening_by_year", "closing_by_year",
                  "recovered_by_year", "expired_by_year"):
            d = s.get(k)
            if d is None:
                continue
            if not isinstance(d, dict) or not all(
                    str(y).isdigit() and _num(a) for y, a in d.items()):
                return f"carryforwards.{sec}.{k} is not {{year: amount}}"
    return None


def lock_block(path: Path) -> Optional[Dict[str, Any]]:
    """The `carryforwards` block of a lock (None when the lock has
    none — written before close-year recorded them). A lock that
    cannot be read, or a damaged block, raises CarryInputError."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise CarryInputError(f"cannot read the close-year lock {path}: "
                              f"{e}") from None
    if not isinstance(doc, dict):
        raise CarryInputError(f"{path} is not a close-year lock")
    block = doc.get("carryforwards")
    if block is None:
        return None
    prob = block_problem(block)
    if prob:
        raise CarryInputError(f"{path}: {prob} — restore the lock from "
                              f"git or re-close that year")
    return block


def _by_year(d: Any) -> Dict[int, float]:
    return {int(y): float(a) for y, a in (d or {}).items()}


# ------------------------------------------------------------ resolving

def resolve_amt(root: Path, settings: Dict[str, Any],
                est_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """{"by_year": {origin: amount} | None, "source": str | None,
    "notes": [str]} — the minimum tax carryover available to the
    project year (Canada): [estimate] amt_carryover, else the prior
    year's lock. Raises CarryInputError on unreadable input."""
    root = Path(root)
    if "amt_carryover" in (est_cfg or {}):
        return {"by_year": amt_from_config(est_cfg["amt_carryover"]),
                "source": AMT_KEY, "notes": []}
    return _from_lock(root, settings, "minimum_tax")


def _from_lock(root: Path, settings: Dict[str, Any], section: str
               ) -> Dict[str, Any]:
    out: Dict[str, Any] = {"by_year": None, "source": None, "notes": [],
                           "block": None}
    pl = prior_lock(root, settings)
    if pl is None:
        return out
    ly, lp, label = pl
    block = lock_block(lp)
    sec = (block or {}).get(section)
    if not isinstance(sec, dict):
        return out
    out["block"] = sec
    out["source"] = f"{label} (the {ly} close-year record)"
    out["lock_year"] = ly
    try:
        py = int(settings.get("year"))
    except (TypeError, ValueError):
        py = None
    # A lock taken before its own year ended (`close-year --force` on an
    # open year) is a snapshot: its balances are provisional, said as
    # every other lock consumer says it (issue #55).
    from taxjson.bin.taxjson_filed import partial_year_note
    try:
        _pn = partial_year_note(
            json.loads(Path(lp).read_text(encoding="utf-8-sig")), ly)
    except (OSError, ValueError):
        _pn = None
    if _pn:
        out["partial_lock"] = ly
        out["notes"].append(
            f"The carried balances are provisional: {_pn} ({label}).")
    if py is not None and ly < py - 1:
        out["notes"].append(
            f"The latest close-year record is {ly}'s ({label}); "
            f"{py - 1} has none, so the balance carried from it misses "
            f"whatever {py - 1} used or added — enter the figures from "
            f"your notice of assessment.")
    if section == "minimum_tax":
        out["by_year"] = _by_year(sec.get("closing_by_year"))
    return out


def resolve_losses(root: Path, settings: Dict[str, Any], country: str,
                   explicit: bool) -> Dict[str, Any]:
    """The loss carryover the estimate uses when the user set none
    (`explicit` False): Canada {"other_losses"}, USA {"other_losses"
    (short-term), "lt_losses"} from the latest close-year lock before
    the project year; {} when explicit or no lock carries one. Always
    with "source" / "notes"."""
    if explicit:
        return {"source": None, "notes": []}
    sec = "net_capital_loss" if country == "canada" else "capital_loss"
    r = _from_lock(Path(root), settings, sec)
    s = r.get("block")
    if not isinstance(s, dict):
        return {"source": None, "notes": []}
    extra = ({"partial_lock": r["partial_lock"]}
             if r.get("partial_lock") else {})
    if country == "canada":
        return {"other_losses": float(s.get("closing") or 0.0),
                "source": r["source"], "notes": r["notes"], **extra}
    return {"other_losses": float(s.get("st_closing") or 0.0),
            "lt_losses": float(s.get("lt_closing") or 0.0),
            "source": r["source"], "notes": r["notes"], **extra}


# ---------------------------------------------------- the lock's block

def record_block(country: str, r: Dict[str, Any],
                 sources: Dict[str, Any]) -> Dict[str, Any]:
    """The `carryforwards` block close-year writes into filed/<year>.json,
    from the year's estimate result `r` (the same computation `taxjson
    estimate` and `taxjson amt` print) and the input sources it used."""
    out: Dict[str, Any] = {"version": BLOCK_VERSION,
                           "computed_by": "taxjson estimate"}
    if country == "canada":
        out["net_capital_loss"] = {
            "opening": float(r.get("losses_opening") or 0.0),
            "opening_source": sources.get("other_losses") or "none",
            "created": float(r.get("losses_created") or 0.0),
            "applied": float(r.get("losses_applied") or 0.0),
            "closing": float(r.get("losses_unused") or 0.0)}
        c = ((r.get("amt") or {}).get("carryover"))
        if isinstance(c, dict):
            out["minimum_tax"] = {
                "opening_by_year": dict(c.get("available_by_year") or {}),
                "opening_source": c.get("source") or "none",
                "expired_by_year": dict(c.get("expired_by_year") or {}),
                "recovered_by_year": dict(c.get("recovered_by_year")
                                          or {}),
                "recovered_federal": float(c.get("recovered_federal")
                                           or 0.0),
                "recovered_provincial": float(
                    c.get("recovered_provincial") or 0.0),
                "created": float(c.get("created") or 0.0),
                "closing_by_year": dict(c.get("closing_by_year") or {}),
                "closing": float(c.get("closing") or 0.0),
                "province": r.get("province"),
                "other_income": float(sources.get("other_income_value")
                                      or 0.0),
                "other_income_entered": bool(
                    sources.get("other_income_entered"))}
    else:
        out["capital_loss"] = {
            "st_opening": float(r.get("carryover_short_term") or 0.0),
            "lt_opening": float(r.get("carryover_long_term") or 0.0),
            "opening_source": sources.get("other_losses") or "none",
            "st_closing": float(r.get("st_carryover_after") or 0.0),
            "lt_closing": float(r.get("lt_carryover_after") or 0.0)}
    return out


# ------------------------------------------------------------ hand-off

def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 0.01


def handoff_issues(root: Path, cfg: Dict[str, Any],
                   record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What this (next) project's carry-forward inputs say vs what the
    closed year's record carried forward: [estimate] other_losses /
    long_term_losses, [carryover] claimed's entry for the record year,
    [estimate] amt_carryover (tax-logic CA-CARRY-05,
    US-CARRY-03). An input that is not set is not a
    mismatch (the estimate then reads the record)."""
    from taxjson.lib.country import CountryError, settings_country
    block = record.get("carryforwards")
    if not isinstance(block, dict):
        return []
    root = Path(root)
    settings = cfg.get("settings") or {}
    try:
        country = settings_country(settings)
    except CountryError:
        return []
    est = cfg.get("estimate") or {}
    ry = int(record["year"])
    out: List[Dict[str, Any]] = []

    def item(what, here, there, why):
        out.append({"what": what, "here": here, "record": there,
                    "why": why})

    if country == "canada":
        ncl = block.get("net_capital_loss") or {}
        if "closing" in ncl and isinstance(est.get("other_losses"),
                                           (int, float)) \
                and not isinstance(est.get("other_losses"), bool) \
                and not _close(est["other_losses"], ncl["closing"]):
            item("[estimate] other_losses", float(est["other_losses"]),
                 float(ncl["closing"]),
                 f"the {ry} record carried {ncl['closing']:,.2f} of net "
                 f"capital losses (100%) into {ry + 1}; this project "
                 f"enters {float(est['other_losses']):,.2f}. Fix "
                 f"whichever is wrong (your notice of assessment "
                 f"decides; re-close {ry} with --force if the record "
                 f"is).")
        if "applied" in ncl:
            from taxjson.lib.project_tables import claimed_losses
            claimed = claimed_losses(cfg)[0]
            if ry in claimed and not _close(claimed[ry], ncl["applied"]):
                item(f"{CLAIMED_KEY} {ry}", claimed[ry],
                     float(ncl["applied"]),
                     f"{CLAIMED_KEY} records {claimed[ry]:,.2f} applied "
                     f"on the {ry} return; the {ry} record applied "
                     f"{float(ncl['applied']):,.2f}.")
        mt = block.get("minimum_tax")
        given: Dict[str, Any] = {"source": None}
        if isinstance(mt, dict) and "amt_carryover" in est:
            try:
                given = resolve_amt(root, {"year": ry + 1}, est)
            except CarryInputError as e:
                item("minimum tax carryover input", str(e), "", str(e))
            if given.get("source") == AMT_KEY:
                from taxjson.lib.tax_estimate import CA_AMT_CARRY_YEARS
                cutoff = ry + 1 - CA_AMT_CARRY_YEARS
                want = {int(y): float(a) for y, a in
                        (mt.get("closing_by_year") or {}).items()
                        if int(y) >= cutoff and float(a) > 0.005}
                have = {y: a for y, a in (given["by_year"] or {}).items()
                        if y >= cutoff and a > 0.005}
                for y in sorted(set(want) | set(have)):
                    if not _close(want.get(y, 0.0), have.get(y, 0.0)):
                        item(f"{given['source']} {y}", have.get(y, 0.0),
                             want.get(y, 0.0),
                             f"the {ry} record carried "
                             f"{want.get(y, 0.0):,.2f} of {y} minimum tax "
                             f"into {ry + 1}; {given['source']} says "
                             f"{have.get(y, 0.0):,.2f}. The notice of "
                             f"assessment / T691 decides: fix the input, "
                             f"or re-close {ry} (its estimate inputs — "
                             f"[estimate] other_income — may differ from "
                             f"the return).")
    else:
        cl_ = block.get("capital_loss") or {}
        for key, k2, word in (("other_losses", "st_closing", "short"),
                              ("long_term_losses", "lt_closing", "long")):
            v = est.get(key)
            if k2 in cl_ and isinstance(v, (int, float)) \
                    and not isinstance(v, bool) \
                    and not _close(v, cl_[k2]):
                item(f"[estimate] {key}", float(v), float(cl_[k2]),
                     f"the {ry} record carried {float(cl_[k2]):,.2f} of "
                     f"{word}-term capital loss into {ry + 1}; this "
                     f"project enters {float(v):,.2f} (Schedule D "
                     f"Capital Loss Carryover Worksheet decides).")
    return out
