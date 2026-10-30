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


# What a present section must carry: the balance carried OUT of the
# year, which the next year's estimate reads (resolve_losses,
# _from_lock). A section with an opening and no closing passed as
# well-formed, and the next year read the absent closing as zero: a loss
# carry-forward silently reset (2026-10 audit B39).
_BLOCK_CLOSING = {"net_capital_loss": ("closing",),
                  "capital_loss": ("st_closing", "lt_closing"),
                  "minimum_tax": ("closing_by_year",)}
# The balances close-year writes as 0 or more (record_block; the T1A
# carry-back it takes out): a negative one is a hand edit. The next
# year read a negative closing as a loss that adds gains, and dropped a
# negative minimum-tax amount in silence (2026-10-25 audit year-end#3).
_BLOCK_BALANCES = {"net_capital_loss": ("closing", "carried_back"),
                   "capital_loss": ("st_closing", "lt_closing",
                                    "deduction_used"),
                   "minimum_tax": ("closing",)}
_BY_YEAR_KEYS = ("opening_by_year", "closing_by_year",
                 "recovered_by_year", "expired_by_year")


def _by_year_problem(where: str, d: Any) -> Optional[str]:
    """What is wrong with a lock's {year of origin: amount} table, or
    None: the checks [estimate] amt_carryover gets (amt_from_config) —
    a year key, one entry per year (`2024` and `02024` are one year:
    the second replaced the first in silence), a plausible year, an
    amount that is a number of 0 or more (2026-10-25 audit year-end#3;
    close-year writes str(year) keys and positive amounts)."""
    if not isinstance(d, dict):
        return f"{where} is not {{year: amount}}"
    spelled: Dict[int, str] = {}
    for k, a in d.items():
        ks = str(k).strip()
        if not ks.isdigit() or not _num(a):
            return f"{where} is not {{year: amount}}"
        y = int(ks)
        if y in spelled:
            return (f"{where} gives {y} twice ({spelled[y]!r} and "
                    f"{str(k)!r}) — one entry per year of origin")
        spelled[y] = str(k)
        if not _plausible_year(y):
            return f"{where}: {ks} is not a plausible year of origin"
        if float(a) < 0:
            return (f"{where}: {y} is {a!r}, a negative amount (close-year "
                    f"writes 0 or more)")
    return None


def block_problem(block: Any) -> Optional[str]:
    """None when a lock's `carryforwards` block has the shape close-year
    writes; else what is wrong (taxjson_filed.lock_damage — the one
    check every reader of a lock applies — and handoff use it). A
    present section without its closing balance (_BLOCK_CLOSING) is a
    problem, never a zero; so is a negative balance (_BLOCK_BALANCES), a
    by-year table with a year twice or a negative amount
    (_by_year_problem), and a minimum tax closing that is not the sum
    of its closing_by_year."""
    if not isinstance(block, dict):
        return "carryforwards is not an object"
    for sec, keys in (("net_capital_loss", ("opening", "created",
                                            "applied", "closing",
                                            "carried_back")),
                      ("capital_loss", ("st_opening", "lt_opening",
                                        "st_closing", "lt_closing",
                                        "deduction_used",
                                        "deduction_available",
                                        "section_1256_gain",
                                        "section_1256_short_term",
                                        "section_1256_long_term")),
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
        for k in _BLOCK_CLOSING[sec]:
            if k not in s:
                return (f"carryforwards.{sec} has no {k} — the balance "
                        f"carried out of the year is missing (a truncated "
                        f"block)")
        for k in _BLOCK_BALANCES[sec]:
            if k in s and float(s[k]) < 0:
                return (f"carryforwards.{sec}.{k} is {s[k]!r}, a negative "
                        f"balance (close-year writes 0 or more)")
        for k in _BY_YEAR_KEYS:
            if s.get(k) is None:
                continue
            prob = _by_year_problem(f"carryforwards.{sec}.{k}", s[k])
            if prob:
                return prob
        if sec == "minimum_tax" and "closing" in s:
            # close-year writes closing as the rounded sum of
            # closing_by_year (tax_estimate): an amount edited in one
            # and not the other moved the carryover the next year
            # recovers with no word.
            parts = s["closing_by_year"]
            total = sum(float(a) for a in parts.values())
            if abs(total - float(s["closing"])) > 0.01:
                return (f"carryforwards.minimum_tax.closing is "
                        f"{float(s['closing']):,.2f} but its "
                        f"closing_by_year adds up to {total:,.2f}")
    return None


def lock_block(path: Path, country: Optional[str] = None,
               loss: bool = False) -> Optional[Dict[str, Any]]:
    """The `carryforwards` block of the previous year's lock (None when
    the lock has none — written before close-year recorded them). The
    lock passes the one check every reader applies
    (taxjson_filed.read_lock: readable, not damaged — its block
    included — and, `country` given, closed under this project's
    country); else CarryInputError naming the lock and the fix. `loss`:
    the loss the lock carried is read, so a lock whose filed
    dispositions hold another year's rows is refused too (backlog
    TJ-0347)."""
    from taxjson.bin import taxjson_filed
    try:
        doc = taxjson_filed.read_lock(
            Path(path), None if country is None else {"country": country},
            prior=True, carried=loss)
    except taxjson_filed.LockProblem as e:
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
    if "amt_carryover" in (est_cfg or {}):
        return {"by_year": amt_from_config(est_cfg["amt_carryover"]),
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
    out: Dict[str, Any] = {"by_year": None, "source": None, "notes": [],
                           "block": None}
    pl = prior_lock(root, settings)
    # The lock prior_year_record names, deleted after its year was
    # closed, is a lock that cannot be read, never "no record" (backlog
    # TJ-0272).
    from taxjson.bin.taxjson_filed import missing_prior_lock
    mp = missing_prior_lock(root, settings)
    try:
        _py = int(settings.get("year"))
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
        py = int(settings.get("year"))
    except (TypeError, ValueError):
        py = None
    # A lock taken before its own year ended (`close-year --force` on an
    # open year) is a snapshot: its balances are provisional, said as
    # every other lock consumer says it (issue #55).
    from taxjson.bin.taxjson_filed import partial_year_note
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
        from taxjson.bin.taxjson_carryover import us_lock_1256
        try:
            _s, _unsplit = us_lock_1256(_doc)
        except (TypeError, ValueError, AttributeError):
            _s, _unsplit = 0.0, False
        if _unsplit:
            out["notes"].append(
                f"The {ly} record ({label}) was closed before taxjson "
                f"split §1256 P/L as Form 6781 does: its carryover counts "
                f"its {_s:,.2f} of §1256 P/L as short-term, where Form "
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
    return abs(float(a) - float(b)) <= 0.01


def handoff_issues(root: Path, cfg: Dict[str, Any],
                   record: Dict[str, Any],
                   carried_back_to: Optional[Dict[int, float]] = None
                   ) -> List[Dict[str, Any]]:
    """What this (next) project's carry-forward inputs say vs what the
    closed year's record carried forward: [estimate] other_losses /
    long_term_losses, [carryover] claimed's entry for the record year,
    [estimate] amt_carryover (tax-logic CA-CARRY-05,
    US-CARRY-03). An input that is not set is not a
    mismatch (the estimate then reads the record). `carried_back_to`
    ({year: amount}, the carryover ledger's): the part of this
    project's loss a claim under the record year carries back (T1A) —
    the record, closed before it, applied none of it (backlog
    TJ-0078)."""
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
            back = float((carried_back_to or {}).get(ry) or 0.0)
            if ry in claimed and not _close(claimed[ry] - back,
                                            ncl["applied"]):
                item(f"{CLAIMED_KEY} {ry}", claimed[ry],
                     float(ncl["applied"]),
                     f"{CLAIMED_KEY} records {claimed[ry]:,.2f} applied "
                     f"on the {ry} return"
                     + (f" ({back:,.2f} of it carried back from this "
                        f"project's loss, T1A)" if back > 0.005 else "")
                     + f"; the {ry} record applied "
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
                 f"{CLAIMED_KEY} records {claimed[ry]:,.2f} deducted "
                 f"against ordinary income on the {ry} return (Schedule "
                 f"D line 21); the {ry} record counted {used[0]:,.2f} "
                 f"as used, so the {float(cl_.get('st_closing') or 0):,.2f}"
                 f" short-term and "
                 f"{float(cl_.get('lt_closing') or 0):,.2f} long-term "
                 f"it carried into {ry + 1} are off by the difference. "
                 f"Enter it in the {ry} project's {CLAIMED_KEY} and close "
                 f"{ry} again there with --force, or fix the entry here "
                 f"(the Capital Loss Carryover Worksheet decides).")
    return out


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
        from taxjson.bin.taxjson_carryover import us_lock_gains
        st, lt, _src = us_lock_gains(record)
        loss = max(0.0, -((st - float(cl_.get("st_opening") or 0.0))
                          + (lt - float(cl_.get("lt_opening") or 0.0))))
        cap = min(US_ORDINARY_LOSS_CAP, loss)
        if _num(cl_.get("deduction_used")):
            return float(cl_["deduction_used"]), cap
        out = (float(cl_.get("st_closing") or 0.0)
               + float(cl_.get("lt_closing") or 0.0))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return max(0.0, min(cap, loss - out)), cap
