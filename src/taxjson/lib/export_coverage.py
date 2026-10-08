"""Per-broker export coverage: a broker whose exports stop while it still
holds positions (`taxjson run` Warning, `taxjson checklist`
export-coverage, `taxjson quick-start` inputs).

A data check, not a tax rule (both countries). For each account of the
project (crypto accounts excepted: a coin is held for years with no row)
and each broker whose exports feed it, the date the exports END:

* an IB Activity Statement's Period ("Statement,Data,Period"): the
  latest period end among the account's IB statements;
* an RBC activity export's "Activity Export as of" date
  (rbc_direct.rbc_export_as_of);
* a Webull export's "Date Range: ... - <end>" preamble; a Webull trading
  summary that names only its "Year / Année" cannot reach past Dec 31 of
  that year;
* otherwise the date of its last row (Questrade, a generic mapping): the
  export itself carries no end date.

The exports are short when that end is before the CUTOFF — Dec 31 of a
finished tax year, or today in the year still running — and the broker
still holds positions in the account at the end (its own rows: the
account's books attributed by their source file, its transfer legs and
corporate-action rows; a long position, or a written option, whose
contract has not expired by the end) that the account's later rows of
any source dated inside the gap (after the end, up to the cutoff) — a .tt line closing it, another broker's sale, a
transfer-out — do not close. A broker whose positions are all closed is
never listed: nothing after its end can be missing; when .tt lines
closed them, the run says so as an Info ("... were closed by .tt lines —
no export needed"), not a Warning. Slack: an
explicit end (statement, as-of, range) may trail today by GRACE_DAYS in
the running year, and the last days of a finished year may be days
without trading (a weekend, Dec 25); an end read from the last row
alone is short only when more than LAST_ROW_SLACK days of the year
follow it with no row (an account can be quiet for weeks) — or when
the export's year ends before the tax year starts.

The run says each one as a Warning ("<broker> exports for <account> end
<date> with open positions (...); download the rest of <year>"); the
checklist's export-coverage step needs attention until the export is
added or the step is marked done (`taxjson checklist --done
export-coverage`: e.g. a broker whose last row really is its last
activity), and the run then says a note instead.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

STEP = "export-coverage"
# In the running year, an export whose explicit end is this many days
# before today is still current (a statement downloaded last week).
GRACE_DAYS = 14
# An end read from the last row only: this many days of the year with
# no row after it are an ordinary quiet spell, not a short export.
LAST_ROW_SLACK = 30
# The positions a message names; the rest are counted.
SHOWN = 4
_EPS = 1e-9
_POSITION_ACTIONS = ("BUYSELL", "ASSIGN", "TRANSFER", "OPENING_BALANCE",
                     "SPLIT")
_WEBULL_RANGE_RE = re.compile(
    r"Date\s+Range\s*:?\s*,*\s*\"?([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})\s*-\s*"
    r"([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})", re.IGNORECASE)
_WEBULL_YEAR_RE = re.compile(r"^\s*\"?Year\b[^,\n]*,[,\s\"]*(\d{4})\b",
                             re.IGNORECASE | re.MULTILINE)
# How the end was found: an explicit end, or the last row only.
EXPLICIT = ("statement", "as-of", "range", "year")


@dataclass
class Gap:
    """One account's exports from one broker that stop early while the
    broker still holds positions there."""
    account: str
    broker: str                       # parser id (ib, rbc_direct, ...)
    end: str                          # ISO date the exports end
    how: str                          # statement | as-of | range | year | last-row
    cutoff: str                       # the date they should reach
    current_year: bool
    # Still open after every later row of the account (any source).
    positions: List[Tuple[str, float]] = field(default_factory=list)
    # Open at the export's end but closed by the account's later .tt
    # lines (a hand-entered close, expiry or transfer-out): nothing is
    # missing for them.
    closed_by_tt: List[str] = field(default_factory=list)

    @property
    def info(self) -> bool:
        """Every position open at the end was closed by later .tt lines:
        an Info, not a short export."""
        return not self.positions

    def record(self) -> Dict[str, Any]:
        return {"account": self.account, "broker": self.broker,
                "broker_name": broker_name(self.broker), "end": self.end,
                "how": self.how, "cutoff": self.cutoff,
                "current_year": self.current_year,
                "positions": [{"symbol": s, "quantity": q}
                              for s, q in self.positions],
                "closed_by_tt": list(self.closed_by_tt)}


def broker_name(broker: str) -> str:
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    b = str(broker or "")
    if b.startswith("generic-"):
        return b[len("generic-"):]
    return DISPLAY_NAMES.get(b, b or "broker")


def _d(s: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def _load(p: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) else None


def _rows(doc: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = (doc or {}).get("transactions")
    return [r for r in rows if isinstance(r, dict)] \
        if isinstance(rows, list) else []


def _head_text(path: Path, n: int = 16384) -> str:
    from taxjson.lib.brokerages.base import decode_broker_text
    try:
        with open(path, "rb") as fh:
            raw = fh.read(n)
        return decode_broker_text(raw, path.name)
    except Exception:                                   # noqa: BLE001
        return ""


def ib_statement_end(path: Path) -> Optional[date]:
    """The end of an IB Activity Statement's Period, read from the
    file's first lines ("Statement,Data,Period,..."), else None."""
    from taxjson.lib.brokerages.ib_extractor import _ib_period
    text = _head_text(path)
    if not text.lstrip().startswith("Statement,Header"):
        return None
    try:
        for row in csv.reader(text.splitlines()[:40]):
            if (len(row) >= 4 and row[0] == "Statement"
                    and row[1] == "Data" and row[2] == "Period"):
                span = _ib_period(row[3])
                return span[1] if span else None
    except csv.Error:
        return None
    return None


def _month_day_year(s: str) -> Optional[date]:
    s = " ".join(s.replace(",", " ").replace(".", " ").split())
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def webull_export_end(path: Path) -> Tuple[Optional[date], str]:
    """(end, how) from a Webull export's preamble: the "Date Range"'s
    end ("range"), or Dec 31 of the "Year / Année" a trading summary
    names ("year" — an upper bound: it holds nothing later), else
    (None, "")."""
    text = "\n".join(_head_text(path, 4096).splitlines()[:12])
    m = _WEBULL_RANGE_RE.search(text)
    if m:
        end = _month_day_year(m.group(2))
        if end:
            return end, "range"
    m = _WEBULL_YEAR_RE.search(text)
    if m:
        return date(int(m.group(1)), 12, 31), "year"
    return None, ""


def _explicit_end(broker: str, paths: Iterable[Path]
                  ) -> Tuple[Optional[date], str]:
    """The latest explicit end among one broker's files, and how it was
    read ("" when none of them carries one)."""
    best: Optional[date] = None
    how = ""
    for p in paths:
        if broker == "ib":
            e, h = ib_statement_end(p), "statement"
        elif broker == "rbc_direct":
            from taxjson.lib.brokerages.rbc_direct import rbc_export_as_of
            e, h = _d(rbc_export_as_of(p)), "as-of"
        elif broker == "webull":
            e, h = webull_export_end(p)
        else:
            e, h = None, ""
        if e is not None and (best is None or e > best):
            best, how = e, h
    return best, how


def _quiet_days(a: date, b: date) -> bool:
    """Every day after `a` up to `b` is a weekend day or Dec 25 / Jan 1:
    no trading, so an end on `a` reaches `b`."""
    d = a + timedelta(days=1)
    while d <= b:
        if d.weekday() < 5 and (d.month, d.day) not in ((1, 1), (12, 25)):
            return False
        d += timedelta(days=1)
    return True


def _expired_by(sym: str, day: date) -> bool:
    from taxjson.lib.core import is_option_symbol, parse_option_expiry
    if not is_option_symbol(sym):
        return False
    exp = _d(parse_option_expiry(sym))
    return exp is not None and exp <= day


def open_positions(rows: Iterable[Dict[str, Any]], day: date
                   ) -> List[Tuple[str, float]]:
    """[(symbol, quantity)] a broker's rows leave open at the end of
    `day` (trade dates; a SPLIT row scales the symbol's units by its
    ratio, or moves them to its new symbol): long positions and written
    options, an option whose contract expired by `day` left out (the
    run's expired-options check covers a missing expiry row). A short
    share position is left out: with no purchase in the data it is
    missing history (find-missing-history), not a sign of a short
    export."""
    from taxjson.lib.core import is_option_symbol
    dated = []
    for r in rows:
        if str(r.get("action") or "") not in _POSITION_ACTIONS:
            continue
        rd = _d(r.get("date"))
        if rd is not None and rd <= day:
            dated.append((rd, str(r.get("time") or ""), r))
    dated.sort(key=lambda x: (x[0], x[1]))
    pos: Dict[str, float] = {}
    for _rd, _t, r in dated:
        sym = str(r.get("symbol") or "").upper()
        if not sym:
            continue
        try:
            q = float(r.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        if str(r.get("action")) == "SPLIT":
            new = str(r.get("symbol_new") or "").upper()
            held = pos.pop(sym, 0.0)
            if new and new != sym:
                # A rename keeps the units; a split's ratio scales them.
                pos[new] = pos.get(new, 0.0) + held * (q if q > _EPS
                                                       else 1.0)
            else:
                pos[sym] = held * (q if q > _EPS else 1.0)
            continue
        pos[sym] = pos.get(sym, 0.0) + q
    out = []
    for sym, q in sorted(pos.items()):
        if abs(q) <= 1e-6:
            continue
        opt = is_option_symbol(sym)
        if q < 0 and not opt:
            continue
        if opt and _expired_by(sym, day):
            continue
        out.append((sym, q))
    return out


def _broker_files(cache: Path, acct: str) -> List[Tuple[str, Path]]:
    """(broker, parsed file) of an account's parsed exports in work/."""
    from taxjson.lib.brokerages.detect import DISPLAY_NAMES
    out = []
    for b in sorted(DISPLAY_NAMES):
        if b in ("coinbase", "kraken", "generic"):
            continue
        p = cache / f"{acct}_{b}.json"
        if p.is_file():
            out.append((b, p))
    for p in sorted(cache.glob(f"{acct}_generic-*.json")):
        stem = p.name[len(acct) + 1:-len(".json")]
        if stem.endswith(("_transfers", "_corp")):
            continue
        out.append((stem, p))
    return out


def _row_key(r: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (str(r.get("action") or ""), str(r.get("date") or "")[:10],
            str(r.get("symbol") or "").upper(),
            f"{float(r.get('quantity') or 0.0):.6f}")


def account_gaps(root: Path, acct: str, year: int, today: date
                 ) -> List[Gap]:
    """The short exports of one account (module docstring)."""
    cache = root / "work"
    base = _load(cache / f"{acct}_base.json")
    if base is None:
        return []
    year_end = date(year, 12, 31)
    current = today <= year_end
    cutoff = today if current else year_end
    year_start = date(year, 1, 1)
    files = _broker_files(cache, acct)
    if not files:
        return []
    by_source: Dict[str, str] = {}
    inputs: Dict[str, List[Path]] = {}
    corp_keys: Dict[Tuple[str, str, str, str], str] = {}
    sidecars: Dict[str, List[Dict[str, Any]]] = {}
    for b, p in files:
        md = (_load(p) or {}).get("metadata") or {}
        names = [Path(str(f)).name for f in (md.get("input_files") or [])
                 if f]
        for n in names:
            by_source.setdefault(n, b)
        folder = root / "inputs" / acct
        inputs[b] = [folder / n for n in names if (folder / n).is_file()]
        for r in _rows(_load(cache / f"{acct}_{b}_corp.json")):
            corp_keys.setdefault(_row_key(r), b)
        side = _load(cache / f"{acct}_{b}_transfers.json")
        smd = (side or {}).get("metadata") or {}
        if isinstance(smd, dict) and smd.get("kind") == "transfer_sidecar":
            sidecars[b] = [r for r in _rows(side)
                           if str(r.get("action")) == "TRANSFER"]
    rows_by: Dict[str, List[Dict[str, Any]]] = {}
    last: Dict[str, date] = {}
    for r in _rows(base):
        src = Path(str(r.get("source") or "")).name
        b = by_source.get(src) if src else corp_keys.get(_row_key(r))
        if b is None:
            continue
        rows_by.setdefault(b, []).append(r)
        rd = _d(r.get("date"))
        if rd is not None and (b not in last or rd > last[b]):
            last[b] = rd
    # Every position-moving row of the account, any source (a .tt line,
    # another broker, a transfer leg): what happened after an export's end.
    all_rows = [r for r in _rows(base)] + [r for v in sidecars.values()
                                           for r in v]
    gaps: List[Gap] = []
    for b, _p in files:
        rows = rows_by.get(b, []) + sidecars.get(b, [])
        for r in sidecars.get(b, []):
            rd = _d(r.get("date"))
            if rd is not None and (b not in last or rd > last[b]):
                last[b] = rd
        if not rows or b not in last:
            continue
        end, how = _explicit_end(b, inputs.get(b, []))
        if how == "year":
            # A trading summary's year bounds it; its last row dates it.
            if end >= year_start:
                end, how = last[b], "last-row"
        elif end is not None:
            end = max(end, last[b])
        else:
            end, how = last[b], "last-row"
        if how == "last-row":
            short = end < cutoff - timedelta(days=LAST_ROW_SLACK)
        elif current:
            short = end < cutoff - timedelta(days=GRACE_DAYS)
        else:
            short = end < cutoff and not _quiet_days(end, cutoff)
        if not short:
            continue
        held = open_positions(rows, end)
        if not held:
            continue
        # Only rows inside the gap the missing export would cover (after
        # its end, up to the cutoff) close a position: a .tt close dated
        # next year says nothing about this year's missing months.
        later = [r for r in all_rows
                 if end < (_d(r.get("date")) or end) <= cutoff
                 and str(r.get("action") or "") in _POSITION_ACTIONS]
        still, by_tt, by_other = closed_later(held, later)
        if not still and not by_tt:
            continue                    # closed by another broker's rows
        gaps.append(Gap(acct, b, end.isoformat(), how, cutoff.isoformat(),
                        current, still, by_tt))
    return gaps


def _is_tt(r: Dict[str, Any]) -> bool:
    return Path(str(r.get("source") or "")).suffix.lower() == ".tt"


def closed_later(held: List[Tuple[str, float]],
                 later: List[Dict[str, Any]]
                 ) -> Tuple[List[Tuple[str, float]], List[str], List[str]]:
    """(still open, closed by .tt lines, closed by other rows) for the
    positions a broker held at its export's end, walked through the
    account's later rows of any source in date order (a .tt BUYSELL or
    expiry, another broker's sale, a transfer-out). A position counts as
    closed once the later rows bring it to zero or past it; it is
    closed by .tt when the row that closed it is a .tt line."""
    rows = sorted(later, key=lambda r: (str(r.get("date") or ""),
                                       str(r.get("time") or "")))
    still: List[Tuple[str, float]] = []
    by_tt: List[str] = []
    by_other: List[str] = []
    for sym, q in held:
        left = q
        closer = None
        for r in rows:
            if str(r.get("symbol") or "").upper() != sym \
                    or str(r.get("action")) == "SPLIT":
                continue
            try:
                left += float(r.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(left) <= 1e-6 or (left > 0) != (q > 0):
                closer = r
                break
        if closer is None:
            still.append((sym, left if abs(left) > 1e-6 else q))
        elif _is_tt(closer):
            by_tt.append(sym)
        else:
            by_other.append(sym)
    return still, by_tt, by_other


def find_gaps(root: Path, cfg: Dict[str, Any],
              today: Optional[date] = None) -> List[Gap]:
    """Every account's short exports (crypto accounts excepted). [] when
    the project has no year or no books yet."""
    root = Path(root)
    settings = (cfg or {}).get("settings") or {}
    year = settings.get("year")
    if not isinstance(year, int) or isinstance(year, bool):
        return []
    today = today or date.today()
    out: List[Gap] = []
    for name, a in sorted(((cfg or {}).get("accounts") or {}).items()):
        if not isinstance(a, dict) or a.get("crypto"):
            continue
        try:
            out += account_gaps(root, str(name), year, today)
        except Exception:                               # noqa: BLE001
            continue                    # advisory: never a crash
    return out


def _held_text(g: Gap) -> str:
    shown = [f"{s} {q:g}" for s, q in g.positions[:SHOWN]]
    more = len(g.positions) - SHOWN
    return ", ".join(shown) + (f" +{more} more" if more > 0 else "")


def _how_text(g: Gap) -> str:
    return {"statement": f"The last statement's period ends {g.end}.",
            "as-of": f"The latest export is as of {g.end}.",
            "range": f"The export's date range ends {g.end}.",
            "year": f"The trading summary covers {g.end[:4]} only.",
            "last-row": f"The last row is dated {g.end}; the export "
                        f"itself names no end date."}.get(g.how, "")


def info_message(g: Gap) -> str:
    """The run's Info line for a gap whose positions later .tt lines
    closed."""
    syms = ", ".join(g.closed_by_tt[:SHOWN]) + (
        f" +{len(g.closed_by_tt) - SHOWN} more"
        if len(g.closed_by_tt) > SHOWN else "")
    return (f"{broker_name(g.broker)} exports for {g.account} end {g.end}; "
            f"the positions open at the export end ({syms}) were closed by "
            f".tt lines — no export needed")


def message(g: Gap, year: int) -> Tuple[str, List[str]]:
    """(headline, details) of the run's Warning for one gap."""
    head = (f"{broker_name(g.broker)} exports for {g.account} end {g.end} "
            f"with open positions ({_held_text(g)}); download the rest of "
            f"{year}")
    details = [
        f"{_how_text(g)} Sales, expiries, assignments and income after "
        f"{g.end} are not in the books"
        + (f" (the year so far runs to {g.cutoff})." if g.current_year
           else ".")]
    if g.closed_by_tt:
        details.append(f"Closed by later .tt lines, not listed: "
                       f"{', '.join(g.closed_by_tt[:SHOWN])}"
                       + (" ..." if len(g.closed_by_tt) > SHOWN else "")
                       + ".")
    if g.how == "last-row":
        details.append(
            f"If {broker_name(g.broker)} really had no activity for "
            f"{g.account} after {g.end}, confirm it with `taxjson "
            f"checklist --done {STEP}`.")
    return head, details


def detail(gaps: List[Gap]) -> str:
    """One line for the checklist / quick-start (the gaps that still
    hold positions: an Info gap is not listed)."""
    gaps = [g for g in gaps if not g.info]
    parts = [f"{broker_name(g.broker)} exports for {g.account} end "
             f"{g.end} with open positions ({_held_text(g)})"
             for g in gaps[:3]]
    more = len(gaps) - 3
    quiet = all(g.how == "last-row" for g in gaps)
    return ("; ".join(parts) + (f"; +{more} more" if more > 0 else "")
            + " — download the rest of the year"
            + (" (or, if there was no later activity, `taxjson checklist "
               f"--done {STEP}`)" if quiet else ""))
