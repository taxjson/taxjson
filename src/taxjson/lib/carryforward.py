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
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

from taxjson.project.accounts import estimate_of, typed_estimate
from taxjson.project.settings import as_settings, settings_of
from taxjson.lib.tx_load import finite_num
from taxjson.lib.project_tables import _plausible_year
from taxjson.model import money as M

# The old project-root files; only `tjs migrate` reads them now
# (load_amt_file) — the inputs are [estimate] amt_carryover and
# [carryover] claimed in taxjson.toml.
AMT_FILE = "amt_carryover.txt"
CLAIMED_FILE = "claimed_losses.txt"
AMT_KEY = "[estimate] amt_carryover"
CLAIMED_KEY = "[carryover] claimed"
BLOCK_VERSION = 1

# A grouped amount's lead group has no leading zero; `$` allowed (the
# claimed_losses.txt rule, carryover_inputs._AMOUNT_RE).
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


def load_amt_file(path: Path) -> Dict[int, float]:
    """The old amt_carryover.txt (read by `tjs migrate` only):
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
    spelled: Dict[int, str] = {}
    for k, v in value.items():
        try:
            y = int(str(k).strip())
        except ValueError:
            raise CarryInputError(f"{where}: {k!r} is not a year") from None
        if not _plausible_year(y):
            raise CarryInputError(f"{where}: {y} is not a plausible year "
                                  f"of origin")
        if y in spelled:
            # `2023` and `02023` are two TOML keys but one year of
            # origin: the second silently replaced the first (2026-10
            # audit A39); [carryover] claimed refuses the same.
            raise CarryInputError(
                f"{where}: {y} is given twice ({spelled[y]!r} and "
                f"{str(k)!r}) — one entry per year of origin; keep the "
                f"amount the notice of assessment / T691 shows")
        spelled[y] = str(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)) or float(v) < 0:
            raise CarryInputError(f"{where}: {y} must be a non-negative "
                                  f"number, got {v!r}")
        out[y] = float(v)
    return out


# ------------------------------------------------------------- the locks

def prior_lock(root: Path, settings: Mapping[str, Any]
               ) -> Optional[Tuple[int, Path, str]]:
    """(year, path, label) of the latest close-year lock BEFORE the
    project year: this project's filed/<year>.json or the lock
    [settings] prior_year_record names. None when there is none."""
    from taxjson.lib import filed_locks
    try:
        py = int(as_settings(settings).year)
    except (TypeError, ValueError):
        return None
    try:
        locks = filed_locks.project_locks(root, settings)
    except ValueError:
        return None
    best = None
    for y, p, _w in locks:
        if y < py and (best is None or y > best[0]):
            best = (y, p)
    if best is None:
        return None
    return best[0], best[1], filed_locks.lock_label(root, best[1])


def lock_block(path: Path, country: Optional[str] = None,
               loss: bool = False) -> Optional[Dict[str, Any]]:
    """The `carryforwards` block of the previous year's lock (None when
    the lock has none — written before close-year recorded them). The
    lock passes the one check every reader applies
    (filed_locks.read_lock: readable, not damaged — its block
    included — and, `country` given, closed under this project's
    country); else CarryInputError naming the lock and the fix. `loss`:
    the loss the lock carried is read, so a lock whose filed
    dispositions hold another year's rows is refused too (backlog
    TJ-0347)."""
    from taxjson.lib import filed_locks
    try:
        doc = filed_locks.read_lock(
            Path(path), None if country is None else {"country": country},
            prior=True, carried=loss)
    except filed_locks.LockProblem as e:
        raise CarryInputError(str(e)) from None
    return doc.get("carryforwards")


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
    est = typed_estimate(est_cfg)
    if est.has("amt_carryover"):
        return {"by_year": amt_from_config(est.amt_carryover),
                "source": AMT_KEY, "notes": []}
    # The minimum tax carryover is Canada's (s.120.2): a lock closed
    # under US law carries none, and reading it as "no earlier record"
    # dropped the carryover (2026-10-25 audit year-end#1).
    return _from_lock(root, settings, "minimum_tax", "canada")


def _from_lock(root: Path, settings: Dict[str, Any], section: str,
               country: str) -> Dict[str, Any]:
    """The `section` of the latest lock before the project year, which
    must pass the one lock check (lock_block) under `country`: a lock
    closed under the other country's law is refused, never read as
    carrying nothing (2026-10-25 audit year-end#1)."""
    settings = as_settings(settings)
    out: Dict[str, Any] = {"by_year": None, "source": None, "notes": [],
                           "block": None}
    pl = prior_lock(root, settings)
    # The lock prior_year_record names, deleted after its year was
    # closed, is a lock that cannot be read, never "no record" (backlog
    # TJ-0272).
    from taxjson.lib.filed_locks import missing_prior_lock
    mp = missing_prior_lock(root, settings)
    try:
        _py = int(settings.year)
    except (TypeError, ValueError):
        _py = None
    if mp is not None and (_py is None or mp[0] < _py) \
            and (pl is None or mp[0] > pl[0]):
        raise CarryInputError(mp[2])
    if pl is None:
        return out
    ly, lp, label = pl
    # The loss it carried stops on a lock that counted another year's
    # filed sales (backlog TJ-0347); its minimum tax carryover is the
    # estimate's, not the filed dispositions'.
    block = lock_block(lp, country, loss=section != "minimum_tax")
    sec = (block or {}).get(section)
    if not isinstance(sec, dict):
        return out
    out["block"] = sec
    out["source"] = f"{label} (the {ly} close-year record)"
    out["lock_year"] = ly
    try:
        py = int(settings.year)
    except (TypeError, ValueError):
        py = None
    # A lock taken before its own year ended (`close-year --force` on an
    # open year) is a snapshot: its balances are provisional, said as
    # every other lock consumer says it (issue #55).
    from taxjson.lib.filed_locks import partial_year_note
    try:
        _doc = json.loads(Path(lp).read_text(encoding="utf-8-sig"))
        _pn = partial_year_note(_doc, ly)
    except (OSError, ValueError):
        _doc, _pn = None, None
    if _pn:
        out["partial_lock"] = ly
        out["notes"].append(
            f"The carried balances are provisional: {_pn} ({label}).")
    if section == "capital_loss" and isinstance(_doc, dict):
        # A US lock closed before the §1256 net was split 60/40 carries
        # it all short-term (US-FUT-03; backlog TJ-0611).
        from taxjson.lib.carryover_inputs import us_lock_1256
        try:
            _s, _unsplit = us_lock_1256(_doc)
        except (TypeError, ValueError, AttributeError):
            _s, _unsplit = 0.0, False
        if _unsplit:
            out["notes"].append(
                f"The {ly} record ({label}) was closed before taxjson "
                f"split §1256 P/L as Form 6781 does: its carryover counts "
                f"its {M.shown(_s, 2):,.2f} of §1256 P/L as short-term, where Form "
                f"6781 makes 60% of it long-term. Close {ly} again with "
                f"--force in its project.")
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
    r = _from_lock(Path(root), settings, sec, country)
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
    from the year's estimate result `r` (the same computation `tjs
    estimate` and `tjs amt` print) and the input sources it used."""
    out: Dict[str, Any] = {"version": BLOCK_VERSION,
                           "computed_by": "tjs estimate"}
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
                # Of it, the share off basic provincial tax (the
                # provincial form's line); the rest is surtax saved.
                "recovered_provincial_credit": float(
                    c.get("recovered_provincial_credit") or 0.0),
                # The surtax it saves; the rest of the provincial drop is
                # the foreign tax credit the lower tax limits. Federal:
                # the nominal line 40427 credit above, and the federal
                # tax drop (backlog TJ-0653).
                "recovered_provincial_surtax": float(
                    c.get("recovered_provincial_surtax") or 0.0),
                "recovered_federal_tax": float(
                    c.get("recovered_federal_tax")
                    if c.get("recovered_federal_tax") is not None
                    else c.get("recovered_federal") or 0.0),
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
            "lt_closing": float(r.get("lt_carryover_after") or 0.0),
            # The Schedule D line 21 deduction counted as used (the next
            # year's Capital Loss Carryover Worksheet line 4): the
            # year's [carryover] claimed entry, else what the estimate's
            # taxable income absorbs (backlog TJ-0464). handoff compares
            # a later claimed entry with it.
            "deduction_used": float(r.get("offset_used_for_carryover")
                                    or 0.0),
            "deduction_available": float(r.get("ordinary_offset") or 0.0),
            "deduction_from": (CLAIMED_KEY
                               if r.get("offset_used_from") == "claimed"
                               else "the estimate's taxable income"),
            "other_income": float(sources.get("other_income_value")
                                  or 0.0),
            "other_income_entered": bool(
                sources.get("other_income_entered")),
            # The year's §1256 P/L and the parts the balances above took
            # it in, as Form 6781 lines 8 and 9 split it: 40% short-term,
            # 60% long-term (US-FUT-03; backlog TJ-0466). A lock without
            # the parts was closed before the split and carries the P/L
            # as short-term (taxjson_carryover.lock_figure, TJ-0611).
            "section_1256_gain": float(r.get("section_1256_gain")
                                       or 0.0),
            "section_1256_short_term": float(
                r.get("section_1256_short_term") or 0.0),
            "section_1256_long_term": float(
                r.get("section_1256_long_term") or 0.0)}
    return out


# ------------------------------------------------------------ hand-off

def _close(a: float, b: float) -> bool:
    """Within a cent, in whole cents (`M.cents_apart`): a figure cut a
    cent the other way from the record's is no mismatch (TJ-3132)."""
    return M.cents_apart(a, b) <= 1


class LedgerCarryBack(NamedTuple):
    """What the carryover ledger says of this project's T1A carry-back, the
    one value handoff's comparison reads (backlog TJ-1448): `amounts` ({year:
    the part of this project's loss a claim under it carries back}), `unknown`
    (why the ledger could not be used) and `incomplete` (the years whose nets
    it left sales out of, as text)."""
    amounts: Mapping[int, float] = MappingProxyType({})
    unknown: Optional[str] = None
    incomplete: str = ""


class CarryComparison(NamedTuple):
    """`handoff_issues`' result: the differences, and the decision about the
    claim under the record year, made once where it is compared. `unchecked`:
    the note when that comparison could not run. `caveat`: the note when it
    ran on a ledger that left sales out. `split_note`: the note when the record
    itself was locked on one (backlog TJ-1461)."""
    issues: List[Dict[str, Any]]
    unchecked: Optional[str] = None
    caveat: Optional[str] = None
    split_note: Optional[str] = None


def handoff_issues(root: Path, cfg: Dict[str, Any],
                   record: Dict[str, Any],
                   carry_back: Optional[LedgerCarryBack] = None
                   ) -> CarryComparison:
    """What this (next) project's carry-forward inputs say vs what the
    closed year's record carried forward: [estimate] other_losses /
    long_term_losses, [carryover] claimed's entry for the record year,
    [estimate] amt_carryover (tax-logic CA-CARRY-05,
    US-CARRY-03). An input that is not set is not a
    mismatch (the estimate then reads the record). `carry_back`: the
    part of this project's loss a claim under the record year carries
    back (T1A) — the record, closed before it, applied none of it
    (backlog TJ-0078). When the ledger could not tell, that claim is not
    compared at all (backlog TJ-1426)."""
    from taxjson.lib.country import CountryError, settings_country
    block = record.get("carryforwards")
    if not isinstance(block, dict):
        return CarryComparison([])
    root = Path(root)
    carry_back = carry_back or LedgerCarryBack()
    settings = settings_of(cfg)
    try:
        country = settings_country(settings)
    except CountryError:
        return CarryComparison([])
    est = estimate_of(cfg)
    ry = int(record["year"])
    out: List[Dict[str, Any]] = []
    unchecked = caveat = split_note = None

    def item(what, here, there, why):
        out.append({"what": what, "here": here, "record": there,
                    "why": why})

    if country == "canada":
        ncl = block.get("net_capital_loss") or {}
        locked_on = ncl.get("carry_back_incomplete")
        if locked_on:
            from taxjson.lib.carry_back import left_out_text
            years = (left_out_text(locked_on)
                     if isinstance(locked_on, dict) else "")
            split_note = (
                f"The {ry} record split that year's loss on a carryover "
                f"ledger that left out sales with an unknown cost"
                + (f" ({years})" if years else "")
                + f". Once they have a cost, re-close {ry} with --force: the "
                  f"part carried back may change.")
        if "closing" in ncl and isinstance(est.other_losses,
                                           (int, float)) \
                and not isinstance(est.other_losses, bool) \
                and not _close(est.other_losses, ncl["closing"]):
            item("[estimate] other_losses", float(est.other_losses),
                 float(ncl["closing"]),
                 f"the {ry} record carried {M.shown(ncl['closing'], 2):,.2f} of net "
                 f"capital losses (100%) into {ry + 1}; this project "
                 f"enters {M.shown(float(est.other_losses), 2):,.2f}. Fix "
                 f"whichever is wrong (your notice of assessment "
                 f"decides; re-close {ry} with --force if the record "
                 f"is).")
        if "applied" in ncl:
            from taxjson.lib.project_tables import claimed_losses
            claimed = claimed_losses(cfg)[0]
            back = float(carry_back.amounts.get(ry) or 0.0)
            if ry in claimed and carry_back.unknown:
                unchecked = (
                    f"{CLAIMED_KEY} {ry} was not compared with the {ry} "
                    f"record: only the carryover ledger knows the part "
                    f"carried back from this project's loss, and it could "
                    f"not be used. Reason: {carry_back.unknown}. `tjs "
                    f"carryover` says more.")
            elif ry in claimed and carry_back.incomplete:
                caveat = (
                    f"{CLAIMED_KEY} {ry} was compared using a carryover "
                    f"ledger that leaves out sales with an unknown cost "
                    f"({carry_back.incomplete}), so the "
                    f"part carried back from this project's loss may change "
                    f"(`tjs carryover`).")
            if ry in claimed and not carry_back.unknown \
                    and not _close(M.of(claimed[ry]) - M.of(back), ncl["applied"]):
                item(f"{CLAIMED_KEY} {ry}", claimed[ry],
                     float(ncl["applied"]),
                     f"{CLAIMED_KEY} records {M.shown(claimed[ry], 2):,.2f} applied "
                     f"on the {ry} return"
                     + (f" ({M.shown(back, 2):,.2f} of it carried back from this "
                        f"project's loss, T1A)" if back > 0.005 else "")
                     + f"; the {ry} record applied "
                     f"{M.shown(float(ncl['applied']), 2):,.2f}.")
        mt = block.get("minimum_tax")
        given: Dict[str, Any] = {"source": None}
        if isinstance(mt, dict) and est.has("amt_carryover"):
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
                             f"{M.shown(want.get(y, 0.0), 2):,.2f} of {y} minimum tax "
                             f"into {ry + 1}; {given['source']} says "
                             f"{M.shown(have.get(y, 0.0), 2):,.2f}. The notice of "
                             f"assessment / T691 decides: fix the input, "
                             f"or re-close {ry} (its estimate inputs — "
                             f"[estimate] other_income — may differ from "
                             f"the return).")
    else:
        cl_ = block.get("capital_loss") or {}
        for key, k2, word in (("other_losses", "st_closing", "short"),
                              ("long_term_losses", "lt_closing", "long")):
            v = getattr(est, key)
            if k2 in cl_ and isinstance(v, (int, float)) \
                    and not isinstance(v, bool) \
                    and not _close(v, cl_[k2]):
                item(f"[estimate] {key}", float(v), float(cl_[k2]),
                     f"the {ry} record carried {M.shown(float(cl_[k2]), 2):,.2f} of "
                     f"{word}-term capital loss into {ry + 1}; this "
                     f"project enters {M.shown(float(v), 2):,.2f} (Schedule D "
                     f"Capital Loss Carryover Worksheet decides).")
        # The closed year's [carryover] claimed entry is the deduction
        # its return took; the record counted another one as used, so
        # the carryover it carried out is off by the difference (backlog
        # TJ-0464: a record closed with no other income carried the
        # whole loss).
        from taxjson.lib.project_tables import claimed_losses
        claimed = claimed_losses(cfg)[0]
        used = us_deduction_used(record)
        if ry in claimed and used is not None \
                and not _close(min(claimed[ry], used[1]), used[0]):
            item(f"{CLAIMED_KEY} {ry}", claimed[ry], used[0],
                 f"{CLAIMED_KEY} records {M.shown(claimed[ry], 2):,.2f} deducted "
                 f"against ordinary income on the {ry} return (Schedule "
                 f"D line 21); the {ry} record counted {M.shown(used[0], 2):,.2f} "
                 f"as used, so the {M.shown(float(cl_.get('st_closing') or 0), 2):,.2f}"
                 f" short-term and "
                 f"{M.shown(float(cl_.get('lt_closing') or 0), 2):,.2f} long-term "
                 f"it carried into {ry + 1} are off by the difference. "
                 f"Enter it in the {ry} project's {CLAIMED_KEY} and close "
                 f"{ry} again there with --force, or fix the entry here "
                 f"(the Capital Loss Carryover Worksheet decides).")
    return CarryComparison(out, unchecked, caveat, split_note)


def us_deduction_used(record: Dict[str, Any]
                      ) -> Optional[Tuple[float, float]]:
    """(the $3,000 deduction a US close-year record counted as used, the
    most the year's net loss allowed): what it records
    (`deduction_used`), or for a record written before it did, what its
    balance implies — the year's net loss (its filed Form 8949 gains
    less the carryover it took in) less the carryover it carried out.
    None when the record has no capital_loss block or no usable
    figures."""
    from taxjson.lib.tax_estimate import US_ORDINARY_LOSS_CAP
    cl_ = (record.get("carryforwards") or {}).get("capital_loss")
    if not isinstance(cl_, dict):
        return None
    try:
        from taxjson.lib.carryover_inputs import us_lock_gains
        st, lt, _src = us_lock_gains(record)
        loss = max(0.0, -((st - float(cl_.get("st_opening") or 0.0))
                          + (lt - float(cl_.get("lt_opening") or 0.0))))
        cap = min(US_ORDINARY_LOSS_CAP, loss)
        if finite_num(cl_.get("deduction_used")):
            return float(cl_["deduction_used"]), cap
        out = (float(cl_.get("st_closing") or 0.0)
               + float(cl_.get("lt_closing") or 0.0))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return max(0.0, min(cap, loss - out)), cap
