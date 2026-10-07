"""A loss on one listing of a security, a purchase of another listing of
it inside the loss window: a possible superficial loss (Canada, ITA s.54)
or wash sale (US, §1091) the books cannot see (tax-logic CA-XLIST-05 /
US-XLIST-04; QA F3).

Two listings of one company's same shares are identical property (a TSX
line and its NYSE line: CA-XLIST-01 / US-XLIST-01), but taxjson joins
them only on evidence — a ticker.map TOBASE line, a .tt JOURNAL line, or
a transfer journal it pairs itself — never on the names alone. Sold at a
loss on one listing and bought on the other within 30 days, an unjoined
pair keeps the loss allowed, though the rule would deny it if they are
one security.

This module finds those cases after the gains are computed, from the
run's work files (nothing is recomputed):

* a loss: a share disposition at a loss in a taxable equity account,
  in the tax year (the gains file `taxjson sum` reads,
  report_model.resolve_gains_files);
* another listing of the same root (cross_listings.listing_root:
  ZZX.TO, ZZX.U.TO and ZZX.US share ZZX) whose security names in the
  exports are EQUAL once normalised (cross_listings._names_verdict over
  symbol_codes.exact_name, the test a journal join applies) — names
  that differ, or a listing with no name in the exports (a .tt-only
  book), are never flagged;
* a purchase of that listing in ANY of the project's accounts —
  taxable or registered, the rule's own scope — within 30 days before
  or after the loss, counted on the rule's dates (settlement in Canada,
  trade in the US: missing_history.loss_window_date); in Canada the
  listing must also still be held at the end of day 30 (CA-SL-02 — the
  US rule has no such test);
* no ticker.map DISTINCT line for the pair (the user ruled them apart).
  A TOBASE (or GLOBAL) line, or the run's own join, makes them one
  symbol in the books, so the engine already applies the rule.

Each pair is a `Warning:` on the run's console naming the two lines of
ticker.map that answer it (`TOBASE FROM TO` if they are one security,
`DISTINCT A B` if not), and `run --strict` stops until one is in the map.
`taxjson ticker-map --suggest` offers the TOBASE line (not a conditional
hint: the equal names, the shared root and the trades are the evidence
that the books hold both symbols), and `taxjson scan` lists the pair as
XLIST-LOSS. Nothing is denied here: the user's line decides.

The findings are written to work/xlist_loss_radar.state (JSON) by the
run; `open_findings` reads them back with the map's current answers
taken out.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

STATE = "xlist_loss_radar.state"
FORMAT = "xlist_loss_radar/1"
# The rule's window, days before and after the loss.
WINDOW = 30
_EPS = 1e-9
# Rows that move a listing's position (the still-held test).
_POSITION_ACTIONS = ("BUYSELL", "ASSIGN", "TRANSFER", "OPENING_BALANCE")


@dataclass
class Finding:
    """A loss on `loss_symbol` and purchases of `other_symbol` inside its
    window: one pair, every loss and purchase of it."""
    loss_symbol: str
    other_symbol: str
    name: str                       # the shared name, as an export wrote it
    tobase: str                     # `TOBASE FROM TO`
    distinct: str                   # `DISTINCT A B`
    losses: List[Dict[str, Any]] = field(default_factory=list)
    buys: List[Dict[str, Any]] = field(default_factory=list)

    def record(self) -> Dict[str, Any]:
        return {"loss_symbol": self.loss_symbol,
                "other_symbol": self.other_symbol, "name": self.name,
                "tobase": self.tobase, "distinct": self.distinct,
                "losses": self.losses, "buys": self.buys}


def _d(s: Any) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def _rows(path: Path) -> List[Dict[str, Any]]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    rows = doc.get("transactions") if isinstance(doc, dict) else doc
    return [r for r in rows if isinstance(r, dict)] \
        if isinstance(rows, list) else []


def _qty(r: Dict[str, Any], key: str = "quantity") -> float:
    try:
        return float(r.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _share_listing(sym: str) -> bool:
    from taxjson.lib.core import is_option_symbol
    from taxjson.lib.symbol_codes import _plain_listing
    return bool(sym) and _plain_listing(sym) and not is_option_symbol(sym)


def _final_names(cache: Path, accounts: List[str], renames: Dict[str, str]
                 ) -> Tuple[Dict[str, Set[Tuple[str, ...]]],
                            Dict[Tuple[str, ...], str]]:
    """{symbol as the books carry it: its exports' normalised names},
    and each name as written (cross_listings.gather reads the parsed
    exports, before the map: a renamed symbol's names go to its
    target)."""
    from taxjson.lib import cross_listings as XL
    _legs, names, shown = XL.gather(cache, accounts)
    out: Dict[str, Set[Tuple[str, ...]]] = {}
    for sym, ns in names.items():
        out.setdefault(renames.get(sym, sym), set()).update(ns)
    return out, shown


def _map_rules(root: Path, cache: Path
               ) -> Tuple[Dict[str, str], Set[frozenset]]:
    """(the base-stage renames of the map the run applied — the
    effective map, else ticker.map —, the DISTINCT pairs of ticker.map)."""
    from taxjson.lib import cross_listings as XL
    renames: Dict[str, str] = {}
    distinct: Set[frozenset] = set()
    try:
        from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                    merge_renames)
    except ImportError:                             # pragma: no cover
        return renames, distinct
    eff = cache / XL.EFFECTIVE_MAP
    tm = root / "ticker.map"
    for p, what in ((eff if eff.is_file() else tm, "renames"),
                    (tm, "distinct")):
        if not p.is_file():
            continue
        try:
            tmap = load_map_file(p)
        except Exception:                           # noqa: BLE001
            continue
        if what == "renames":
            try:
                renames = {k.upper(): v.upper() for k, v in
                           merge_renames(tmap, to_base=True).items()}
            except Exception:                       # noqa: BLE001
                renames = {}
        else:
            distinct = {frozenset(str(s).upper() for s in pair)
                        for pair in getattr(tmap, "distinct", ()) or ()}
    return renames, distinct


def analyze(root: Path, cfg: Dict[str, Any]) -> List[Finding]:
    """The project's possible superficial losses (wash sales) across
    listings (see the module docstring), from the last run's work/."""
    from taxjson.lib import cross_listings as XL
    from taxjson.lib.country import (home_currency, is_usa,
                                     settings_country, settings_tax_date)
    from taxjson.lib.first_run import book_files
    from taxjson.lib.missing_history import loss_window_date
    from taxjson.lib.report_model import resolve_gains_files
    root = Path(root)
    cache = root / "work"
    settings = cfg.get("settings") or {}
    accounts = cfg.get("accounts") or {}
    country = settings_country(settings)
    year = str(settings.get("year") or "")
    basis = settings_tax_date(settings)
    base_cur = str(settings.get("base_currency")
                   or home_currency(country)).upper()
    equity = {str(n): a for n, a in accounts.items()
              if isinstance(a, dict) and not a.get("crypto")
              and a.get("type") in ("taxable", "sheltered")}
    taxable = sorted(n for n, a in equity.items()
                     if a.get("type") == "taxable")
    if not taxable or not cache.is_dir():
        return []
    renames, distinct = _map_rules(root, cache)

    # The tax year's share losses in the taxable accounts.
    losses: List[Tuple[str, Dict[str, Any]]] = []
    try:
        gains = resolve_gains_files(cache)
    except Exception:                               # noqa: BLE001
        gains = {}
    for acct in taxable:
        f = gains.get(acct)
        if f is None:
            continue
        for r in _rows(f):
            sym = str(r.get("symbol") or "").upper()
            if (str(r.get("account") or acct) != acct
                    or r.get("grant") or r.get("is_option")
                    or r.get("direction") not in (None, "", "LONG")
                    or not _share_listing(sym)):
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

    # Every account's rows of the share listings (purchases and the
    # position walk), as the books carry them (the map applied).
    rows_by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for p in book_files(cache):
        acct = p.name[:-len("_base.json")]
        if acct not in equity:
            continue
        for r in _rows(p):
            sym = str(r.get("symbol") or "").upper()
            if not _share_listing(sym):
                continue
            if str(r.get("action") or "") not in _POSITION_ACTIONS:
                continue
            r = dict(r)
            r.setdefault("account", acct)
            rows_by_sym.setdefault(sym, []).append(r)
    by_root: Dict[str, Set[str]] = {}
    for sym in rows_by_sym:
        by_root.setdefault(XL.listing_root(sym), set()).add(sym)

    names: Optional[Dict[str, Set[Tuple[str, ...]]]] = None
    shown: Dict[Tuple[str, ...], str] = {}
    found: Dict[Tuple[str, str], Finding] = {}
    for acct, loss in losses:
        a = str(loss.get("symbol") or "").upper()
        others = sorted(by_root.get(XL.listing_root(a), set()) - {a})
        if not others:
            continue
        ld = _d(loss_window_date(loss, country))
        if ld is None:
            continue
        lo, hi = ld - timedelta(days=WINDOW), ld + timedelta(days=WINDOW)
        for b in others:
            if frozenset((a, b)) in distinct:
                continue
            buys = []
            for r in rows_by_sym.get(b, []):
                if (str(r.get("action")) not in ("BUYSELL", "ASSIGN")
                        or _qty(r) <= _EPS):
                    continue
                bd = _d(loss_window_date(r, country))
                if bd is not None and lo <= bd <= hi:
                    buys.append(r)
            if not buys:
                continue
            if not is_usa(country) and _held_at(rows_by_sym.get(b, []), hi,
                                             country) <= _EPS:
                continue            # sold out by day 30 (CA-SL-02)
            if names is None:
                names, shown = _final_names(cache, sorted(equity), renames)
            if XL._names_verdict(names.get(a, set()), names.get(b, set()),
                                 shown) != "":
                continue
            key = (a, b)
            f = found.get(key)
            if f is None:
                common = sorted(names.get(a, set()) & names.get(b, set()))
                nm = shown.get(common[0], " ".join(common[0])) \
                    if common else ""
                frm, to = XL.tobase_direction(a, b, base_cur)
                f = found[key] = Finding(a, b, nm, f"TOBASE {frm} {to}",
                                         f"DISTINCT {a} {b}")
            item = {"account": acct, "date": str(loss.get("date") or ""),
                    "date_settle": str(loss.get("date_settle") or ""),
                    "quantity": abs(_qty(loss, "qty"))}
            if item not in f.losses:
                f.losses.append(item)
            for r in buys:
                ra = str(r.get("account") or "")
                bi = {"account": ra, "date": str(r.get("date") or ""),
                      "date_settle": str(r.get("date_settle") or ""),
                      "quantity": _qty(r),
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


def _held_at(rows: List[Dict[str, Any]], day: _date, country: str) -> float:
    """Units of a listing held across the accounts at the end of `day`
    (rows dated on the rule's basis; a split scales the position)."""
    from taxjson.lib.missing_history import loss_window_date
    pos = 0.0
    for r in rows:
        rd = _d(loss_window_date(r, country))
        if rd is None or rd > day:
            continue
        pos += _qty(r)
    return pos


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


def answered(f: Dict[str, Any], root: Path) -> Optional[str]:
    """Why ticker.map now answers a finding (a DISTINCT line, a rule
    joining the two), else None — the map may have changed since the
    run wrote the state."""
    from taxjson.lib import ticker_map_suggest as TS
    st = TS.map_state(Path(root) / "ticker.map")
    return TS.already(TS.Suggestion(str(f.get("tobase") or ""), "", ""), st)


def open_findings(root: Path) -> List[Dict[str, Any]]:
    """The last run's findings ticker.map does not answer yet."""
    return [f for f in read_state(Path(root) / "work")
            if not answered(f, root)]


def _kind(country: str, n: int = 1) -> str:
    from taxjson.lib.country import is_usa
    one, many = (("wash sale", "wash sales") if is_usa(country) else
                 ("superficial loss", "superficial losses"))
    return one if n == 1 else many


def _when(items: List[Dict[str, Any]]) -> str:
    out = []
    for x in items[:4]:
        tag = ", registered" if x.get("registered") else ""
        out.append(f"{x.get('date')} ({x.get('account')}{tag})")
    if len(items) > 4:
        out.append(f"+{len(items) - 4} more")
    return ", ".join(out)


def message(f: Dict[str, Any], country: str) -> Tuple[str, List[str]]:
    """(headline, details) of the run's console Warning for a finding."""
    from taxjson.lib.country import is_usa
    a, b = f["loss_symbol"], f["other_symbol"]
    if is_usa(country):
        rule = ("are substantially identical, §1091), add this line to "
                "ticker.map and the loss is disallowed as a wash sale:")
    else:
        rule = ("are identical property, s.54), add this line to "
                "ticker.map and the loss is denied as a superficial loss:")
    return (f"possible {_kind(country)} across listings: {a} sold at a "
            f"loss, {b} bought within 30 days",
            [f"Sold at a loss: {a} {_when(f.get('losses') or [])}. "
             f"Bought: {b} {_when(f.get('buys') or [])}. Both listings "
             f"are named {f.get('name')!r}.",
             f"The books keep {a} and {b} apart, so the loss is allowed. "
             f"If they are one security (two listings of one company's "
             f"shares {rule}",
             str(f.get("tobase")),
             "If they are not, add:",
             str(f.get("distinct")),
             "`run --strict` stops until ticker.map has one of the two."])


def scan_text(f: Dict[str, Any], country: str) -> str:
    """A finding as one `taxjson scan` line (XLIST-LOSS)."""
    return (f"loss on {f['loss_symbol']} ({_when(f.get('losses') or [])}) "
            f"and {f['other_symbol']} bought within 30 days "
            f"({_when(f.get('buys') or [])}), both named "
            f"{f.get('name')!r}: a possible {_kind(country)} the books "
            f"cannot see. Add `{f.get('tobase')}` to ticker.map if they "
            f"are one security, `{f.get('distinct')}` if not.")


def suggestion_reason(f: Dict[str, Any], country: str) -> str:
    """The reason `taxjson ticker-map --suggest` gives the TOBASE line."""
    return (f"a loss on {f['loss_symbol']} and {f['other_symbol']} bought "
            f"within 30 days, both named {f.get('name')!r}: a possible "
            f"{_kind(country)} across listings — add it if they are one "
            f"security (`{f.get('distinct')}` if not)")
