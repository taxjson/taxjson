"""Look-alike funds (ticker.map `SIMILAR A B [C ...]`): a loss on one
with another bought inside the loss window (tax-logic CA-SL-20 /
US-WASH-26).

Two index funds on one index, or a fund and its Canadian wrapper, are
not one security: each keeps its own ACB pool (US: its own lots), and
the engines deny nothing between them — s.54 reaches identical property
only (IT-387R2), §1091 substantially identical securities, undefined
for funds of different issuers. Whether such a pair should be treated
as one is the user's call, so a SIMILAR line never changes a figure. It
asks for the pair to be WATCHED:

* `tjs run`: a loss on one member in a taxable account, in the tax
  year, with another member bought within 30 days before or after it
  (the rule's dates: settlement in Canada, trade in the US) in any of
  the project's accounts, taxable or registered, and in Canada still
  held at the end of day 30, is a Warning naming the pair, the dates
  and the line (`analyze`, from the last run's work/ files, nothing
  recomputed — lib/xlist_loss_radar's method, whose helpers this
  module reuses); the findings go to work/similar.state and the
  checklist's wash-reviewed step lists them as a manual decision;
* the planning tools: `buy-check` and `sell-check` add a caveat line
  (SAFE*, never UNSAFE) when a similar member has a loss sale or a buy
  inside the window, `rebuy` names the similar listings a buy of which
  is flagged, `wash-radar` lists each group's statuses.

Groups: the map's SIMILAR lines, lines sharing a symbol merged
(lib/ticker_map.similar_groups). A member the map renames (GLOBAL,
TOBASE) is read under its booked symbol.

The map is read leniently here (a line that cannot be parsed is
skipped), so a reader that decides on the groups refuses a map with
a problem line first, as `tjs run` does (taxjson_run
`_strict_ticker_map`: buy-check, sell-check, rebuy, wash-radar,
harvest); `open_findings` keeps the run's findings while the map has
one (`map_problems`), backlog TJ-0352.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from taxjson.lib import project_layout as _PL
from taxjson.lib import xlist_loss_radar as XR

STATE = "similar.state"
FORMAT = "similar/1"
# The findings the run's console lists one by one; the rest are counted.
SHOWN = 20


def rules(root: Path):
    """The ticker.map side rules of a project, None when there is no
    map or it cannot be read."""
    from taxjson.lib.ticker_map import read_side_rules
    p = _PL.ticker_map_path(Path(root))
    if not p.is_file():
        return None
    try:
        return read_side_rules(p)
    except Exception:                               # noqa: BLE001
        return None


def groups(root: Path) -> List[List[str]]:
    """The project's SIMILAR groups ([] when none)."""
    from taxjson.lib.ticker_map import similar_groups
    r = rules(root)
    return similar_groups(r) if r is not None else []


def index(groups_: Iterable[List[str]]) -> Dict[str, List[str]]:
    from taxjson.lib.ticker_map import similar_index
    return similar_index(groups_)


def booked_groups(root: Path) -> List[List[str]]:
    """The groups as the books spell their members: a member a GLOBAL
    or TOBASE line (ticker.map or tobase.map) moves is read under its
    booked symbol (`SIMILAR ZZSP.US ZZVO.US` with `TOBASE ZZVO.US
    ZZVO.TO`: ZZVO.TO). Two members booked as one symbol are one."""
    root = Path(root)
    renames, _distinct = XR._map_rules(root, root / "work")
    out = []
    for g in groups(root):
        booked: List[str] = []
        for s in g:
            b = renames.get(s, s)
            if b not in booked:
                booked.append(b)
        if len(booked) > 1:
            out.append(sorted(booked))
    return sorted(out)


@dataclass
class Finding:
    """A loss on `loss_symbol`, purchases of `other_symbol` inside its
    window: one pair, every loss and purchase of it."""
    loss_symbol: str
    other_symbol: str
    where: str                      # `ticker.map:N`
    line: str                       # the SIMILAR line as written
    losses: List[Dict[str, Any]] = field(default_factory=list)
    buys: List[Dict[str, Any]] = field(default_factory=list)

    def record(self) -> Dict[str, Any]:
        return {"loss_symbol": self.loss_symbol,
                "other_symbol": self.other_symbol, "where": self.where,
                "line": self.line, "losses": self.losses, "buys": self.buys}


def analyze(root: Path, cfg: Dict[str, Any]) -> List[Finding]:
    """The project's losses with a similar fund bought in the window
    (see the module docstring), from the last run's work/."""
    from taxjson.lib.country import is_usa, settings_country, settings_tax_date
    from taxjson.lib.first_run import book_files
    from taxjson.lib.missing_history import loss_window_date
    from taxjson.lib.report_model import resolve_gains_files
    from taxjson.lib.ticker_map import similar_groups, similar_where
    root = Path(root)
    cache = root / "work"
    side = rules(root)
    if side is None or not side.similar or not cache.is_dir():
        return []
    settings = cfg.get("settings") or {}
    accounts = cfg.get("accounts") or {}
    country = settings_country(settings)
    year = str(settings.get("year") or "")
    basis = settings_tax_date(settings)
    equity = {str(n): a for n, a in accounts.items()
              if isinstance(a, dict) and not a.get("crypto")
              and a.get("type") in ("taxable", "sheltered")}
    taxable = sorted(n for n, a in equity.items()
                     if a.get("type") == "taxable")
    if not taxable:
        return []
    # The groups as the books spell their members (the map's renames
    # applied), each booked symbol remembering how the line wrote it.
    renames, _distinct = XR._map_rules(root, cache)
    written: Dict[str, str] = {}
    others: Dict[str, List[str]] = {}
    for g in similar_groups(side):
        booked = []
        for s in g:
            b = renames.get(s, s)
            written.setdefault(b, s)
            if b not in booked:
                booked.append(b)
        for b in booked:
            others.setdefault(b, [])
            others[b] += [x for x in booked if x != b and x not in others[b]]
    if not any(others.values()):
        return []

    losses: List[Tuple[str, Dict[str, Any]]] = []
    try:
        gains = resolve_gains_files(cache)
    except Exception:                               # noqa: BLE001
        gains = {}
    for acct in taxable:
        f = gains.get(acct)
        if f is None:
            continue
        for r in XR._rows(f):
            sym = str(r.get("symbol") or "").upper()
            if (sym not in others or str(r.get("account") or acct) != acct
                    or r.get("grant") or r.get("is_option")
                    or r.get("direction") not in (None, "", "LONG")
                    or not XR._share_listing(sym)):
                continue
            try:
                gain = float(r.get("gain") or 0.0)
            except (TypeError, ValueError):
                continue
            if gain > -0.005:
                continue
            day = str((r.get("date_settle") if basis == "settle" else None)
                      or r.get("date") or "")
            if year and not day.startswith(year):
                continue
            losses.append((acct, r))
    if not losses:
        return []

    # Every listing's position rows: the still-held test follows a
    # dated rename of a replacement to its new listing (backlog TJ-0936).
    rows_by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for p in book_files(cache):
        acct = p.name[:-len("_base.json")]
        if acct not in equity:
            continue
        for r in XR._rows(p):
            sym = str(r.get("symbol") or "").upper()
            if str(r.get("action") or "") not in XR._POSITION_ACTIONS:
                continue
            r = dict(r)
            r.setdefault("account", acct)
            rows_by_sym.setdefault(sym, []).append(r)

    found: Dict[Tuple[str, str], Finding] = {}
    for acct, loss in losses:
        a = str(loss.get("symbol") or "").upper()
        ld = XR._d(loss_window_date(loss, country))
        if ld is None:
            continue
        lo = ld - timedelta(days=XR.WINDOW)
        hi = ld + timedelta(days=XR.WINDOW)
        for b in others.get(a, []):
            buys = []
            for r in rows_by_sym.get(b, []):
                if (str(r.get("action")) not in ("BUYSELL", "ASSIGN")
                        or XR._qty(r) <= XR._EPS):
                    continue
                bd = XR._d(loss_window_date(r, country))
                if bd is not None and lo <= bd <= hi:
                    buys.append(r)
            if not buys:
                continue
            if not is_usa(country) and XR.held_following(
                    rows_by_sym, b, hi, country) <= XR._EPS:
                continue            # sold out by day 30 (CA-SL-02)
            f = found.get((a, b))
            if f is None:
                where, line = similar_where(side, written.get(a, a),
                                            written.get(b, b))
                f = found[(a, b)] = Finding(a, b, where, line)
            item = {"account": acct, "date": str(loss.get("date") or ""),
                    "date_settle": str(loss.get("date_settle") or ""),
                    "quantity": abs(XR._qty(loss, "qty")),
                    "gain": round(float(loss.get("gain") or 0.0), 2)}
            if item not in f.losses:
                f.losses.append(item)
            for r in buys:
                ra = str(r.get("account") or "")
                bi = {"account": ra, "date": str(r.get("date") or ""),
                      "date_settle": str(r.get("date_settle") or ""),
                      "quantity": XR._qty(r),
                      "registered": (equity.get(ra) or {}).get("type")
                      == "sheltered"}
                if bi not in f.buys:
                    f.buys.append(bi)
    out = sorted(found.values(), key=lambda f: (f.loss_symbol,
                                                f.other_symbol))
    for f in out:
        f.losses.sort(key=lambda x: (x["date"], x["account"]))
        f.buys.sort(key=lambda x: (x["date"], x["account"]))
    return out


def state_text(findings: Iterable[Finding], country: str) -> str:
    return json.dumps({"format": FORMAT, "country": country,
                       "findings": [f.record() for f in findings]},
                      indent=2, sort_keys=True) + "\n"


def read_state(cache: Path) -> List[Dict[str, Any]]:
    """The findings the last run wrote ([] when none or unreadable)."""
    try:
        doc = json.loads((Path(cache) / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return []
    return [f for f in doc.get("findings") or [] if isinstance(f, dict)
            and f.get("loss_symbol") and f.get("other_symbol")]


def map_problems(root: Path) -> List[str]:
    """The project's ticker.map lines that cannot be parsed (or why the
    map cannot be read), [] for none or no map: a group read past such
    a line may have lost a member (backlog TJ-0352)."""
    p = _PL.ticker_map_path(Path(root))
    if not p.is_file():
        return []
    try:
        from taxjson.bin.taxjson_ticker_map import map_file_problems
        return list(map_file_problems(p))
    except Exception as e:                          # noqa: BLE001
        return [f"{p.name} cannot be read ({e})"]


def open_findings(root: Path) -> List[Dict[str, Any]]:
    """The last run's findings whose pair ticker.map still declares
    similar (a line deleted since the run drops its findings). While
    the map has a problem line, every finding the run made: the groups
    cannot be read from it (backlog TJ-0352)."""
    root = Path(root)
    if map_problems(root):
        return read_state(root / "work")
    idx = index(booked_groups(root))
    return [f for f in read_state(root / "work")
            if str(f.get("other_symbol")) in idx.get(
                str(f.get("loss_symbol")), [])]


def _test(country: str) -> str:
    from taxjson.lib.country import is_usa
    return ("substantially identical securities (§1091)" if is_usa(country)
            else "identical property (s.54)")


def message(f: Dict[str, Any], country: str) -> Tuple[str, List[str]]:
    """(headline, details) of the run's console Warning for a finding."""
    from taxjson.lib.country import is_usa
    a, b = f["loss_symbol"], f["other_symbol"]
    kind = XR._kind(country)
    law = ("§1091 reaches substantially identical securities, which the "
           "IRS has not defined for funds of different issuers"
           if is_usa(country) else
           "s.54 reaches identical property, which CRA does not read as "
           "another issuer's fund (IT-387R2)")
    return (f"similar fund in a loss's window: {a} sold at a loss, {b} "
            f"bought within 30 days",
            [f"Sold at a loss: {a} {XR._when(f.get('losses') or [])}. "
             f"Bought: {b} {XR._when(f.get('buys') or [])}. "
             f"{f.get('where') or 'ticker.map'} declares them alike: "
             f"`{f.get('line') or 'SIMILAR ' + a + ' ' + b}`.",
             f"The books keep them two securities: the loss is allowed "
             f"and nothing is denied. {law}; whether to treat this one "
             f"as a {kind} is your call (said on every run while the "
             f"line stands)."])


def more_message(n: int, country: str) -> Tuple[str, List[str]]:
    return (f"{n} more loss(es) with a similar fund bought in the window, "
            f"not listed here",
            ["`tjs run --details` lists every one; the checklist's "
             "wash-reviewed step counts them."])


def checklist_note(findings: List[Dict[str, Any]]) -> str:
    """The wash-reviewed step's note for the open findings ("" when
    none)."""
    if not findings:
        return ""
    pairs = [f"{f['loss_symbol']}/{f['other_symbol']}" for f in findings]
    return (f"{len(pairs)} loss(es) with a similar fund bought in the "
            f"window (ticker.map SIMILAR): {', '.join(pairs[:4])}"
            f"{' ...' if len(pairs) > 4 else ''} — decide each by hand "
            f"(`tjs run --details` lists them)")


def caveat(t: str, cat: str, q: str, mode: str, country: str,
           sheltered_buy: Optional[Dict[str, Any]] = None,
           bought: Optional[Dict[str, Any]] = None) -> str:
    """buy-check's / sell-check's caveat line for a radar row `t` (a
    member similar to the queried `q`) in category `cat`.
    `sheltered_buy` ({account, date}, sell only): `t` is held only in a
    registered account / IRA that bought it inside the window.
    `bought` ({account, date}, sell only): the purchase inside the
    window, named when the category does not say it (a VIOLATION's
    replacement — backlog TJ-0449)."""
    if mode == "buy":
        return (f"{t}: {cat} — a loss sold within the past 30 days; "
                f"ticker.map calls it similar to {q} (SIMILAR). The books "
                f"keep them two securities, so buying {q} denies nothing; "
                f"it would if you treat them as {_test(country)}.")
    if sheltered_buy:
        from taxjson.lib.country import is_usa
        kind = "an IRA" if is_usa(country) else "a registered account"
        return (f"{t}: bought within the past 30 days in "
                f"{kind} ('{sheltered_buy.get('account') or 'unknown'}', "
                f"{sheltered_buy.get('date') or '?'}); ticker.map calls it "
                f"similar to {q} (SIMILAR). The books keep them two "
                f"securities, so a loss on {q} stays allowed; treated as "
                f"{_test(country)}, it would be denied for good (bought in "
                f"{kind}).")
    when = (f" ('{bought.get('account') or 'unknown'}', "
            f"{bought.get('date') or '?'})" if bought else "")
    return (f"{t}: {cat} — bought within the past 30 days{when}; "
            f"ticker.map calls it similar to {q} (SIMILAR). The books keep "
            f"them two securities, so a loss on {q} stays allowed; it "
            f"would not if you treat them as {_test(country)}.")


# The radar categories that say a loss was sold in the window, and the
# ones that say a buy was made in it (bin/taxjson_wash_radar).
LOSS_CATEGORIES = ("BLOCKED", "COOLING", "VIOLATION", "WASHED")
BUY_CATEGORIES = ("LOCKED", "EXITABLE", "CAUTION")


def radar_lines(groups_: Iterable[List[str]], status: Dict[str, str]
                ) -> List[str]:
    """`wash-radar`'s one line per SIMILAR group that has a member with
    an advisory: each member with its status (`—` for none)."""
    out = []
    for g in groups_:
        if not any(status.get(s) for s in g):
            continue
        out.append(", ".join(f"{s} {status.get(s) or '—'}" for s in g))
    return out


__all__ = ["STATE", "groups", "booked_groups", "index", "analyze",
           "read_state",
           "open_findings", "message", "more_message", "checklist_note",
           "caveat", "radar_lines", "LOSS_CATEGORIES", "BUY_CATEGORIES"]
