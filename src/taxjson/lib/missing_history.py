"""Missing-history reconciliation: sales whose purchase is not in the files.

When a user's data window doesn't reach back to when a position was opened
(the shares were bought before the broker files start), the engine sees
only the disposition and concludes the user is short. This module detects
those cases and lets the user list them in the project's
missing_history.json (formerly phantoms.json) so the engine can:

  - Insert a synthetic OPENING_BALANCE transaction at the data-window start
    to keep position math non-negative.
  - Tag the ACB pool as tainted while those shares (unknown cost) remain.
    Dispositions drawing from a tainted pool are excluded from the gains
    report and listed for manual reporting.
  - Re-clean the pool when total quantity hits zero, so post-drain buys
    establish a fresh, fully-known ACB.

This is the gains-side counterpart to the user's old TRANSFER-in approach,
without the unsafe leak of fabricated ACB into the gain calculation.

The module was called phantom_holdings (and the file phantoms.json) until
2026-10; taxjson.lib.phantom_holdings is a shim for this module, and the
old function and class names are aliases at the end of this file.
"""
from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from taxjson.lib.core import TaxTransaction, is_stock_dividend
from taxjson.lib.corporate_timeline import (SplitTimeline, event_sort_key,
                                             normalize_symbol_new)


# The project-root file listing (symbol, account) pairs whose purchase
# is not in the broker files. It was called phantoms.json until 2026-10:
# a project that still has only the old name keeps working (one NOTE per
# run asks to rename it); a project with BOTH names is refused — which
# one is current cannot be guessed. taxjson never renames or edits the
# user's file itself.
MISSING_HISTORY_FILE = "missing_history.json"
LEGACY_MISSING_HISTORY_FILE = "phantoms.json"
# Set once the rename NOTE was printed: child processes inherit it, so
# one `taxjson run` (and the commands it spawns) says it once.
_LEGACY_NOTE_ENV = "TAXJSON_MISSING_HISTORY_NOTED"


class MissingHistoryFileConflict(ValueError):
    """Both missing_history.json and the legacy phantoms.json exist."""


class MissingHistoryPairs(set):
    """The (symbol, account) pairs load_missing_history read, carrying
    the file's name as the user has it on disk (missing_history.json, or
    the legacy phantoms.json) so every note and warning names it, and
    each entry's recorded `quantity` (`quantities`: the opening it sets,
    exactly; an entry without one is sized from the rows through the
    tax year's end — synthesize_openings)."""
    source_name = MISSING_HISTORY_FILE

    def __init__(self, *args):
        super().__init__(*args)
        self.quantities: Dict[Tuple[str, str], float] = {}


# The entry key that records how many shares (units) an opening fills:
# written by `find-missing-history --write-missing-history`.
QUANTITY_KEY = "quantity"

# The tax year an opening is sized for: an entry without a `quantity`
# fills the deepest shortage of its rows dated up to Dec 31 of that year
# (synthesize_openings). `taxjson` sets it from the project's `year` for
# every command and stage (taxjson_run._normalize_settings); a gains
# stage's own --year sets it too. Unset (a standalone tool outside a
# project), every row counts.
ENV_SIZING_YEAR = "TAXJSON_MISSING_HISTORY_YEAR"


def sizing_until(year: Any = None) -> Optional[str]:
    """The last date ('YYYY-12-31') whose rows size a missing-history
    opening: `year`'s, else the ENV_SIZING_YEAR year's; None (every
    row) when neither names a year."""
    y = year if year not in (None, '') else os.environ.get(ENV_SIZING_YEAR)
    try:
        y = int(str(y).strip())
    except (TypeError, ValueError):
        return None
    return f"{y:04d}-12-31" if 1000 <= y <= 9999 else None


def _source_name(pairs) -> str:
    """The file name a pairs set came from (MISSING_HISTORY_FILE for a
    plain set)."""
    return getattr(pairs, "source_name", None) or MISSING_HISTORY_FILE


def legacy_rename_note(path: Path) -> None:
    """Print the rename NOTE for a legacy phantoms.json — once per run
    (an environment marker the run's child processes inherit)."""
    if os.environ.get(_LEGACY_NOTE_ENV):
        return
    os.environ[_LEGACY_NOTE_ENV] = "1"
    emit_line(f"NOTE: {path} uses the old name of {MISSING_HISTORY_FILE} — it "
              f"is still read, but please rename it: `mv "
              f"{LEGACY_MISSING_HISTORY_FILE} {MISSING_HISTORY_FILE}` in "
              f"{path.parent}", file=sys.stderr)


def missing_history_conflict(root) -> Optional[str]:
    """The refusal text when the project has BOTH file names, else None."""
    root = Path(root)
    new = root / MISSING_HISTORY_FILE
    old = root / LEGACY_MISSING_HISTORY_FILE
    if not ((new.exists() or new.is_symlink())
            and (old.exists() or old.is_symlink())):
        return None
    return (f"both {MISSING_HISTORY_FILE} and {LEGACY_MISSING_HISTORY_FILE} "
            f"are in {root} — {LEGACY_MISSING_HISTORY_FILE} is the old name "
            f"of {MISSING_HISTORY_FILE}, and which one is current cannot be "
            f"guessed. Keep one: merge any entries you need into "
            f"{MISSING_HISTORY_FILE} and delete (or move away) "
            f"{LEGACY_MISSING_HISTORY_FILE}.")


def missing_history_path(root, *, note: bool = True) -> Path:
    """Like project_missing_history_file, but always a path: the new
    name when neither file exists (callers test .exists() and some use
    its parent as the project root)."""
    p = project_missing_history_file(root, note=note)
    return p if p is not None else Path(root) / MISSING_HISTORY_FILE


def project_missing_history_file(root, *, note: bool = True
                                 ) -> Optional[Path]:
    """The project's missing-history file under `root`:
    missing_history.json, else the legacy phantoms.json (with the rename
    NOTE once per run unless note=False), else None. Raises
    MissingHistoryFileConflict when both names exist."""
    root = Path(root)
    why = missing_history_conflict(root)
    if why:
        raise MissingHistoryFileConflict(why)
    new = root / MISSING_HISTORY_FILE
    if new.exists():
        return new
    old = root / LEGACY_MISSING_HISTORY_FILE
    if old.exists():
        if note:
            legacy_rename_note(old)
        return old
    return None


# Registered-account labels, the fallback for an account whose type the
# caller does not know (standalone tools outside a project). Short
# positions are prohibited in these accounts, so a negative balance is
# almost certainly a missing purchase. Every plan name — Canadian and US alike —
# needs a non-letter on each side ('ROTH_IRA', 'IRA-2', 'Spousal RRSP',
# 'rrsp2'; not 'MIRAGE', 'sunlife', 'cliff-margin', 'response'): the
# Canadian names matched as substrings and a taxable 'sunlife' read as
# a LIF (audit A2-1097, the twin of R1-243). Canada-only labels used to
# be the whole list, so an IRA was never recognised (partition
# ENGINE-14).
REGISTERED_ACCOUNT_PATTERNS = (
    'LIRA', 'RRSP', 'RRIF', 'TFSA', 'RESP', 'LIF', 'FHSA', 'LRIF', 'PRPP', 'RDSP',
)
US_REGISTERED_ACCOUNT_PATTERNS = (
    'IRA', 'ROTH', '401K', '403B', '457B', 'HSA', 'SEP', '529',
)


def _label_has(upper: str, patterns) -> bool:
    return any(re.search(rf'(?<![A-Z]){p}(?![A-Z])', upper)
               for p in patterns)


def _registered_label(upper: str, country: Optional[str]) -> bool:
    from taxjson.lib.country import canonical_country
    c = canonical_country(country) if country else None
    if c in (None, 'canada') and _label_has(upper,
                                            REGISTERED_ACCOUNT_PATTERNS):
        return True
    if c in (None, 'usa'):
        return _label_has(upper, US_REGISTERED_ACCOUNT_PATTERNS)
    return False


# OCC option-symbol detection uses the canonical engine-side definition
# (anchored regex with optional futures prefix and market suffix) so this
# module stays in sync with the gain engine. Previously this module
# defined its own unanchored substring check, which could mis-classify
# any synthesized symbol whose mid-string digits happened to
# match `\d{6}[CP]\d+`.
from taxjson.lib.core import is_option_symbol  # noqa: F401 — re-exported


def is_registered_account(account: str, registered_accounts=None,
                          country: Optional[str] = None) -> bool:
    """Registered (sheltered) status. When the caller knows the configured
    accounts, pass `registered_accounts` — {account: True for type =
    "sheltered", False for taxable}: a KNOWN account's type is the
    answer, never its label (audit S076-08 — a taxable 'sunlife' matched
    'LIF', a sheltered 'retireA' matched nothing). An account it does not
    list (or no mapping) falls back to the label heuristic: that
    country's plan names, or both countries' when it is not known."""
    if not account:
        return False
    if registered_accounts is not None and account in registered_accounts:
        return bool(registered_accounts[account])
    return _registered_label(account.upper(), country)


def _project_doc_near(path) -> Dict[str, Any]:
    """The project's taxjson.toml, found beside a book file
    (<root>/work/<acct>_base.json) or one level up; {} when none.
    Raises InputReadError (an OSError: exit 2 through guard_main) when
    the file exists but is not UTF-8 or not valid TOML."""
    try:
        import tomllib
    except ImportError:                      # Python < 3.11
        try:
            import tomli as tomllib          # type: ignore
        except ImportError:
            return {}
    from taxjson.lib.cli_diag import InputReadError, read_text_utf8
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        # A symlink loop: the reader of the file itself reports it in
        # one line (re-audit A2-0791).
        return {}
    for d in (p.parent, p.parent.parent):
        cfg = d / 'taxjson.toml'
        if cfg.is_file():
            # A UTF-8 BOM (what Notepad saves) is dropped, as `taxjson
            # run` drops it (S038-04); a file that exists but does not
            # parse stops the command — reading it as "no project" fell
            # back to Canada's settle basis and label-guessed account
            # types in a US project (re-audit A2-0419 / A2-0424 /
            # A2-0429 / A2-0430 / A2-0438).
            text = read_text_utf8(cfg)
            try:
                return tomllib.loads(text.lstrip('\ufeff')) or {}
            except ValueError as e:
                raise InputReadError(
                    f"{cfg}: not valid TOML ({e}) — fix it (`taxjson "
                    f"run` reports the same file)") from None
    return {}


def account_types_near(path) -> Dict[str, bool]:
    """{account: is_sheltered} from the project's configured types, so
    the standalone detectors use `type` instead of the label (audit
    S076-08). {} outside a project."""
    accts = _project_doc_near(path).get('accounts') or {}
    return {str(n): (a.get('type') == 'sheltered')
            for n, a in accts.items() if isinstance(a, dict)
            and a.get('type') in ('taxable', 'sheltered')}


def tax_date_near(path) -> Optional[str]:
    """The project's tax_date basis ('settle' | 'trade') through the one
    resolver (lib/country.settings_tax_date: the explicit value, else
    the country default); a missing or unknown country in that project
    raises CountryError. None outside a project — the caller decides
    and says so (it used to read the raw TOML and map an unknown
    country to Canada's settle basis, partition INPUTS-08)."""
    doc = _project_doc_near(path)
    if not doc:
        return None
    from taxjson.lib.country import settings_tax_date
    return settings_tax_date(doc.get('settings') or {})


def _drop_duplicate_splits(txs):
    """Drop repeated SPLIT rows for the same corporate event within the same
    ACCOUNT (one account fed by two brokers carries the split once per broker,
    with distinct ids that --dedup can't collapse). Keyed per account because
    every walk in this module runs per (symbol, account). Delegates to
    SplitTimeline.dedupe — the one definition of split-event identity."""
    return SplitTimeline.dedupe(txs, per_account=True)


@dataclass
class MissingHistoryCandidate:
    """One (symbol, account) pair whose running position went negative."""
    symbol: str
    account: str
    currency: str
    first_negative_date: str
    peak_short: float        # most negative running position seen
    end_position: float      # position at end of data
    disposition_count: int   # how many dispositions in the negative state
    registered: bool         # account looks registered (LIRA/RRSP/TFSA/etc.)
    # Every sale that took the position short carries the broker's own
    # short-sale marker (RBC "... SHORT."): a real short, not missing
    # history (audit R1-8).
    broker_marked_short: bool = False
    # A sale that took the position short is one the broker itself
    # codes CLOSING (IB Trades code "C", no "O"): it sold a position
    # bought before the data — missing history for certain, also for an
    # option or a future, which are otherwise skipped by default (audit
    # S013-00). `broker_basis` is the broker's cost of what it closed
    # ("450.00 CAD"), when the export gives it.
    broker_says_closing: bool = False
    broker_basis: str = ''
    # How the broker marked the short ("SHORT." / "IB code O"), for the
    # report's wording.
    short_marker: str = ''


# The broker's short-sale marker on a sale's description: RBC writes
# "<name> SHORT. UNSOLICITED ..." (and "COVER SHORT." on the buy back).
_BROKER_SHORT_RE = re.compile(r'(?<![A-Z])SHORT\.(?=\s|$)')


def open_close_codes(tx) -> Tuple[str, ...]:
    """The broker's open/close marker on a row ('O', 'C' or both, in
    the broker's order; () when the export has none) — IB's Trades
    Code, carried as `open_close`."""
    raw = str(getattr(tx, 'open_close', '') or '')
    return tuple(c for c in re.split(r'[;,\s]+', raw.upper())
                 if c in ('O', 'C'))


def broker_short_marker(tx) -> str:
    """How the broker marks this sale as a short sale: 'SHORT.' (RBC's
    description) or 'IB code O' (an IB sale coded O or C;O — it opened
    a short); '' when it does not."""
    if _BROKER_SHORT_RE.search((tx.description or '').upper()):
        return 'SHORT.'
    if float(tx.quantity or 0) < 0 and 'O' in open_close_codes(tx):
        return 'IB code O'
    return ''


def unbacked_close(tx, prev: float, order_prev: Optional[float] = None
                   ) -> bool:
    """The broker codes this trade as (partly) CLOSING (IB code C) but
    the position the data holds cannot back the close: a sale coded C
    alone that sells more than is held (`prev`, the position before this
    row), a sale coded C;O with no long held when its ORDER began
    (`order_prev`: IB stamps the order's code on every fill of it, so
    the second fill of a "C;O" order that closed 1 and opened 1 carries
    C;O too) — and the mirror cases for a buy. What it closed was
    opened before the data: missing history, never a new short or a new
    long (audit S013-00)."""
    codes = open_close_codes(tx)
    if 'C' not in codes:
        return False
    if order_prev is None:
        order_prev = prev
    q = float(tx.quantity or 0)
    if q < 0:
        if 'O' in codes:
            return order_prev <= 1e-9
        return prev + q < -1e-9
    if q > 0:
        if 'O' in codes:
            return order_prev >= -1e-9
        return prev + q > 1e-9
    return False


class OrderStarts:
    """The position each broker ORDER began from, for unbacked_close:
    consecutive rows of one (symbol, account) with the same clock stamp,
    direction and open/close code are fills of one order."""

    def __init__(self):
        self._last: Dict[Any, Tuple[Any, float]] = {}

    def prev(self, key, tx, prev: float) -> float:
        q = float(tx.quantity or 0)
        sig = (tx.date, tx.time, q > 0, open_close_codes(tx))
        last = self._last.get(key)
        if last is not None and last[0] == sig:
            return last[1]
        self._last[key] = (sig, prev)
        return prev


def _is_marked_short(tx) -> bool:
    return bool(broker_short_marker(tx))


class JournalDays:
    """The days the missing-history walks read as a journal's
    (_walk_key: buys and in-legs before sales and out-legs): a set of
    (account, date, SYMBOL), each symbol as the walked books spell it
    and as the inputs do. `in` tests a symbol of any journal pair (the
    symbols a caller re-checks: taxjson_run). A day that holds no
    journal keeps the clock — a sale and a rebuy of one listing, a sale
    of one listing with no purchase and a buy of the other the same day
    (a ticker.map TOBASE line says the two are one security, not that
    the units were journaled) are missing history (second pre-release
    review, finding 1). Built by walk_journal_symbols; journal_targets
    gives the JOURNAL lines' symbols with no day."""

    def __init__(self, symbols: Iterable[str] = (),
                 days: Iterable[Tuple[str, str, str]] = ()):
        self.symbols: Set[str] = {str(s).upper() for s in symbols if s}
        self.days: Set[Tuple[str, str, str]] = set(days)
        # Whether parsed exports (or a run's join record) were read: the
        # days come from them (walk_journal_symbols). Books alone cannot
        # tell a journal's two listings apart (books_journal_days).
        self.inputs_read = False

    def add(self, account: str, day: str, *symbols: str) -> None:
        for s in symbols:
            if s:
                s = str(s).upper()
                self.symbols.add(s)
                self.days.add((str(account), str(day)[:10], s))

    def on(self, account: Any, day: Any, symbol: Any) -> bool:
        """Whether `symbol`'s rows of `account` on `day` are a journal's."""
        return (str(account), str(day or '')[:10],
                str(symbol or '').upper()) in self.days

    def __contains__(self, symbol: Any) -> bool:
        return str(symbol or '').upper() in self.symbols

    def __iter__(self):
        return iter(sorted(self.symbols))

    def __len__(self) -> int:
        return len(self.symbols)

    def __bool__(self) -> bool:
        return bool(self.symbols or self.days)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, JournalDays):
            return (self.symbols, self.days) == (other.symbols, other.days)
        if isinstance(other, (set, frozenset)):
            return self.symbols == {str(s).upper() for s in other}
        return NotImplemented

    __hash__ = None             # mutable, like a set

    def __or__(self, other: Any) -> 'JournalDays':
        out = JournalDays(self.symbols, self.days)
        out.inputs_read = self.inputs_read
        if isinstance(other, JournalDays):
            out.symbols |= other.symbols
            out.days |= other.days
            out.inputs_read |= other.inputs_read
        elif isinstance(other, (set, frozenset)):
            out.symbols |= {str(s).upper() for s in other}
        else:
            return NotImplemented
        return out

    __ror__ = __or__

    def __ior__(self, other: Any) -> 'JournalDays':
        res = self | other
        if res is NotImplemented:
            return NotImplemented
        self.symbols, self.days = res.symbols, res.days
        self.inputs_read = res.inputs_read
        return self

    def __repr__(self) -> str:
        return (f"JournalDays({sorted(self.symbols)!r}, "
                f"{sorted(self.days)!r})")


def _journal_line_symbols(tm) -> Set[str]:
    """Both symbols of every legacy JOURNAL line of a parsed ticker map.
    A TOBASE line is no journal (second pre-release review, finding 1):
    it says two listings are one security, not that units moved between
    them — `taxjson format-map` migrates a JOURNAL line to one, and the
    journal's days then come from its evidence (the broker's legs, a .tt
    JOURNAL line: walk_journal_symbols)."""
    out: Set[str] = set()
    for src, dst in (getattr(tm, 'journal', {}) or {}).items():
        out.add(str(dst).upper())
        out.add(str(src).upper())
    return out


def _journal_line_pairs(tm) -> Set[Tuple[str, str]]:
    """(FROM, TO) of every legacy JOURNAL line of a parsed ticker map."""
    return {(str(s).upper(), str(d).upper())
            for s, d in (getattr(tm, 'journal', {}) or {}).items()}


def _day_trades(cache: Path, accounts: Iterable[str]
                ) -> Dict[Tuple[str, str], Dict[str, List[float]]]:
    """(account, date) -> symbol -> [units bought, units sold] by the
    BUYSELL rows of the account's parsed exports and converted .tt files
    that day (the symbols as the inputs spell them, before any map: the
    two listings of a journal are still apart)."""
    from taxjson.lib import cross_listings as XL
    out: Dict[Tuple[str, str], Dict[str, List[float]]] = {}
    for acct in sorted(set(accounts)):
        files = [f for _b, f, side in XL._parsed_files(cache, acct)
                 if not side]
        files += sorted(cache.glob(f'{acct}_tt_*.json'))
        for f in files:
            if not f.is_file():
                continue
            try:
                doc = json.loads(f.read_text(encoding='utf-8'))
            except (OSError, ValueError, RecursionError):
                continue
            txs = doc.get('transactions') if isinstance(doc, dict) else None
            for t in txs if isinstance(txs, list) else []:
                if not isinstance(t, dict) or t.get('action') != 'BUYSELL':
                    continue
                try:
                    q = float(t.get('quantity') or 0.0)
                except (TypeError, ValueError):
                    continue
                if abs(q) < 1e-12:
                    continue
                tot = out.setdefault(
                    (acct, str(t.get('date') or '')[:10]), {}).setdefault(
                    str(t.get('symbol') or '').upper(), [0.0, 0.0])
                tot[0 if q > 0 else 1] += abs(q)
    return out


def _opposite_trades(day: Dict[str, List[float]], a: str, b: str) -> bool:
    """A journal's trades on one day: units of one listing bought and the
    same number of units of the OTHER listing sold (a Norbert's gambit's
    buy and sale, the broker's clock in any order). Never one listing's
    own sale and rebuy (a == b)."""
    if a == b:
        return False
    ta, tb = day.get(a), day.get(b)
    if not ta or not tb:
        return False

    return _same_units(ta[0], tb[1]) or _same_units(tb[0], ta[1])


def _same_units(x: float, y: float) -> bool:
    return x > 1e-9 and abs(x - y) <= max(1e-6, 1e-6 * max(x, y))


def _journal_trade_day(trades: Dict[Tuple[str, str], Dict[str, List[float]]],
                       account: str, legs: Iterable[str], frm: str, to: str,
                       units: float) -> Optional[str]:
    """The ONE day of `account` whose trades are this journal's own (a
    Norbert's gambit: `units` of the FROM listing bought, the same units
    of the TO listing sold — RBC dates the J~ legs the trades' settlement
    day): within cross_listings.PAIR_DAYS business days of the legs'
    dates `legs`, the nearest such day (a legs' own day first; a tie goes
    to the earlier: trades settle after they are made). None when no day
    holds those trades. A day of another quantity, or of the reverse
    direction, is not this journal's: a sale with no purchase near an
    unrelated journal stays missing history (third pre-release review,
    finding 2)."""
    from datetime import date as _date
    from taxjson.lib import cross_listings as XL

    def _d(s: str):
        try:
            return _date.fromisoformat(str(s)[:10])
        except ValueError:
            return None
    frm, to = str(frm).upper(), str(to).upper()
    legd = [x for x in (_d(s) for s in legs) if x is not None]
    if not legd or frm == to or units <= 1e-9:
        return None
    best: Optional[Tuple[int, str]] = None
    for (a, day), tr in trades.items():
        dd = _d(day)
        if a != account or dd is None:
            continue
        gap = min(XL.business_days(dd, x) for x in legd)
        if gap > XL.PAIR_DAYS:
            continue
        bought, sold = tr.get(frm), tr.get(to)
        if not bought or not sold or not _same_units(bought[0], units) \
                or not _same_units(sold[1], units):
            continue
        rank = (gap, day)
        if best is None or rank < best:
            best = rank
    return best[1] if best else None


def journal_targets(ticker_map) -> JournalDays:
    """The symbols ticker.map's legacy JOURNAL lines fold a listing INTO
    (and from): a Norbert's-gambit pair (sell SAMPLF.TO, buy SAMPLF.U.TO
    the same morning) is one symbol in the books. No day: the days of
    those lines come from the trades (walk_journal_symbols). Empty
    without a readable map (a missing map is not an error here: the
    caller decides). A TOBASE line is no journal (_journal_line_symbols)."""
    if not ticker_map:
        return JournalDays()
    from taxjson.bin.taxjson_ticker_map import load_map_file
    return JournalDays(_journal_line_symbols(load_map_file(Path(ticker_map))))


# RBC's reference on the two TFR legs of one journal between a
# security's lines ("TFR - ... TRANSFER TO U$ J~1" / "... FROM C$ J~1"),
# read only on a TFR row (any other text that happens to hold "J~" is no
# journal reference).
_JOURNAL_REF_RE = re.compile(r'^\s*TFR\b.*(?<!\S)J~(\w+)\b',
                             re.IGNORECASE)
# The broker whose J~ reference that is (lib/brokerages ids).
_JOURNAL_REF_BROKER = 'rbc_direct'
# The broker of a row whose file is not known (a row of the books):
# journal_leg_key reads its `journal_pair` only.
_NO_BROKER = '?'


def journal_leg_key(t: Any, broker: Optional[str] = None
                    ) -> Optional[Tuple[str, ...]]:
    """What pairs a TRANSFER row with the other leg of its journal,
    within one account: the row's `journal_pair` (a Questrade BRW
    currency journal, brokerages/questrade; a .tt JOURNAL line's legs,
    lib/dated_events), else RBC's J~ reference on a TFR row, with the
    leg's date — and only on an RBC row when `broker` (the parser id of
    the row's file) is known. None for any other row. `t` is a
    TaxTransaction or a parsed row (dict)."""
    get = t.get if isinstance(t, dict) else (
        lambda k, d=None: getattr(t, k, d))
    if str(get('action') or '').upper() != 'TRANSFER':
        return None
    pair = str(get('journal_pair') or '').strip()
    if pair:
        return ('pair', pair)
    if broker and broker != _JOURNAL_REF_BROKER:
        return None
    m = _JOURNAL_REF_RE.search(' '.join(str(get('description') or '')
                                        .split()))
    if m:
        return ('ref', str(get('date') or '')[:10], m.group(1).upper())
    return None


def _row_get(t: Any):
    return t.get if isinstance(t, dict) else (
        lambda f, d=None, _t=t: getattr(_t, f, d))


def ref_group_journal(legs: Iterable[Tuple[str, float, str]]
                      ) -> Optional[Tuple[str, str, float]]:
    """THE rule for the legs one broker reference groups in one account
    (journal_leg_key: a Questrade journal_pair, RBC's J~ reference, a
    .tt JOURNAL line's pair id), shared by cross_listings.analyze (step
    0b), transfer_in.own_journal_legs and _detected_journals (v0.24.1
    leftovers, 4). `legs` are (symbol, signed quantity, date): the group
    is ONE journal of (out symbol, in symbol, units) when its out-legs
    all name one listing, its in-legs all name one listing (the same one
    for a custody note), the two directions move the same units (a
    journal the broker split over several rows — 600 + 400 into the
    1000 out — is one journal) and every leg is dated within
    cross_listings.PAIR_DAYS business days of the others; None
    otherwise (one direction only, units that do not balance, a third
    listing: not a journal the reference proves)."""
    from taxjson.lib.cross_listings import PAIR_DAYS, business_days
    eps = 1e-6
    outs: List[Tuple[str, float, str]] = []
    ins: List[Tuple[str, float, str]] = []
    for sym, q, day in legs:
        try:
            q = float(q)
        except (TypeError, ValueError):
            return None
        if q < -eps:
            outs.append((str(sym).upper(), q, str(day or "")[:10]))
        elif q > eps:
            ins.append((str(sym).upper(), q, str(day or "")[:10]))
    if not outs or not ins:
        return None
    o_syms = {x[0] for x in outs}
    i_syms = {x[0] for x in ins}
    if len(o_syms) != 1 or len(i_syms) != 1:
        return None
    qo = -sum(x[1] for x in outs)
    qi = sum(x[1] for x in ins)
    if abs(qo - qi) > max(eps, 1e-6 * max(qo, qi)):
        return None
    days = sorted(x[2] for x in outs + ins)
    try:
        lo, hi = (date.fromisoformat(days[0]),
                  date.fromisoformat(days[-1]))
    except ValueError:
        return None
    if business_days(lo, hi) > PAIR_DAYS:
        return None
    return next(iter(o_syms)), next(iter(i_syms)), qi


def _detected_journals(rows: Iterable[Tuple[Any, ...]],
                       renames: Optional[Dict[str, str]] = None
                       ) -> List[Tuple[str, str, str, str, str, float]]:
    """(account, out-leg date, out symbol, in-leg date, in symbol, units) of each
    broker journal the rows show whose two legs land on ONE symbol once
    `renames` apply. `rows` are (account, row) or (account, row, broker):
    TaxTransactions or parsed rows; a journal is one out-leg and one
    in-leg of the same quantity that journal_leg_key pairs in one
    account (overlapping copies of a row count once). RBC's J~ reference
    is read only on a row whose broker is RBC (`broker`, the parser id
    of the row's file); a row of no known broker pairs by its
    `journal_pair` id alone (second pre-release review, finding 12)."""
    ren = {str(k).upper(): str(v).upper() for k, v in (renames or {}).items()}
    groups: Dict[Tuple[Any, ...], Dict[str, Tuple[str, float, str]]] = {}
    for item in rows:
        acct, t = item[0], item[1]
        broker = item[2] if len(item) > 2 else _NO_BROKER
        k = journal_leg_key(t, broker=broker)
        if k is None:
            continue
        get = _row_get(t)
        sym = str(get('symbol') or '').upper()
        try:
            q = float(get('quantity') or 0.0)
        except (TypeError, ValueError):
            continue
        if not sym or abs(q) < 1e-9:
            continue
        rid = str(get('id') or '') or f"{sym}|{q!r}|{get('date')}"
        groups.setdefault((str(acct),) + k, {})[rid] = (
            sym, q, str(get('date') or '')[:10])
    out: List[Tuple[str, str, str, str, str, float]] = []
    for k, legs in sorted(groups.items()):
        # One rule for a reference group (ref_group_journal): a journal
        # split over several rows is one journal, dated by its first
        # out- and in-leg.
        j = ref_group_journal(legs.values())
        if j is None:
            continue
        o_sym, i_sym, units = j
        a, b = ren.get(o_sym, o_sym), ren.get(i_sym, i_sym)
        if a == b:
            od = min(g[2] for g in legs.values() if g[1] < 0)
            idt = min(g[2] for g in legs.values() if g[1] > 0)
            out.append((k[0], od, o_sym, idt, i_sym, units))
    return out


def detected_journal_symbols(rows: Iterable[Tuple[Any, ...]],
                             renames: Optional[Dict[str, str]] = None
                             ) -> Set[str]:
    """The symbols of the broker journals the rows show (_detected_
    journals): a journal whose two legs land on ONE symbol once `renames`
    apply (the books' TOBASE / JOURNAL / GLOBAL renames, a join of the
    run — lib/cross_listings) is a Norbert's gambit inside one security:
    the symbol and both legs' symbols are returned."""
    ren = {str(k).upper(): str(v).upper() for k, v in (renames or {}).items()}
    out: Set[str] = set()
    for _a, _od, o, _id, i, _q in _detected_journals(rows, renames):
        out.update({ren.get(o, o), o, i})
    return out


def _book_journals(txs: Sequence[TaxTransaction],
                   journal_symbols: Any) -> Any:
    """`journal_symbols` plus the journals the walked rows themselves
    show (an account whose transfers stay in its books: the two legs on
    one symbol once the books' renames applied, paired by their
    `journal_pair` id — a row of the books names no broker, so RBC's J~
    reference is read from the parsed exports: walk_journal_symbols).
    A JournalDays (or None) gains the legs' own days; a caller's plain
    set of symbols (every day of them) gains their symbols."""
    found = _detected_journals((t.account, t) for t in txs
                               if t.action == 'TRANSFER')
    if journal_symbols is not None and not isinstance(journal_symbols,
                                                      JournalDays):
        return set(journal_symbols) | {s for j in found
                                       for s in (j[2], j[4])}
    out = JournalDays() | (journal_symbols or JournalDays())
    for acct, od, o, idt, i, _q in found:
        out.add(acct, od, o, i)
        out.add(acct, idt, o, i)
    return out


def walk_journal_symbols(cache, ticker_map=None) -> JournalDays:
    """The days the missing-history walks read as one security's journal
    (_walk_key: a day's buys and in-legs before its sales and out-legs),
    for the project whose work/ folder is `cache` — only the days that
    hold a journal (second pre-release review, finding 1):

    - the legs' own days of every journal the evidence shows: a join of
      the run (work/cross_listings.state: a cross-listing or currency
      journal joined, and a broker or .tt journal the user's map decided
      — "refused": "map"), and every broker journal the parsed exports
      show (a Questrade BRW pair's `journal_pair`, RBC's J~ reference on
      the two TFR legs of an RBC export, a .tt JOURNAL line's legs) whose
      two legs the books' renames fold onto one symbol;
    - for each such journal, ONE day in its account within
      cross_listings.PAIR_DAYS business days of the legs whose trades are
      the journal's own: its units of the FROM listing bought and of the
      TO listing sold (_journal_trade_day: an RBC gambit's buy of the CAD
      line and sale of the USD line, its J~ legs dated the settlement
      day). Opposite trades of another quantity or direction near the
      journal are not its own (third pre-release review, finding 2);
    - for a legacy ticker.map JOURNAL line (`ticker_map`, and the run's
      effective map, work/ticker.map.effective) — still accepted, no
      longer needed — each day of any account with such opposite trades
      on its two listings.

    A TOBASE line alone is no journal; a sale and a rebuy of one listing
    never is. Each day is recorded for the symbols as the inputs spell
    them and as the books do (the effective map's renames). Advisory: an
    unreadable piece is skipped."""
    from taxjson.bin.taxjson_ticker_map import load_map_file, merge_renames
    from taxjson.lib import cross_listings as XL
    from taxjson.lib.dated_events import SIDECAR_BROKER
    cache = Path(cache)
    out = JournalDays()
    renames: Dict[str, str] = {}
    accounts: Set[str] = set()
    try:
        accounts |= {str(a) for a in (_project_doc_near(
            cache / 'x_base.json').get('accounts') or {})}
    except Exception:                               # noqa: BLE001
        pass
    accounts |= {p.name[:-len('_base.json')] for p in cache.glob('*_base.json')
                 if not p.name.endswith('_raw_base.json')}
    eff = cache / XL.EFFECTIVE_MAP
    maps = [Path(ticker_map)] if ticker_map else []
    if eff.is_file():
        maps.append(eff)
    lines: Set[Tuple[str, str]] = set()
    for m in maps:
        if not m.is_file():
            continue
        try:
            tm = load_map_file(m)
            lines |= _journal_line_pairs(tm)
            # The effective map (read last) is the one the books were
            # merged with.
            renames = merge_renames(tm, True)
        except Exception:                           # noqa: BLE001
            continue
    trades = _day_trades(cache, accounts)
    out.inputs_read = bool(trades)
    # (account, out date, out symbol, in date, in symbol, units) of each
    # journal the evidence shows.
    dated: List[Tuple[str, str, str, str, str, float]] = []
    st = XL.read_state(cache / XL.STATE)
    recs = list(st.get('joined', [])) + [
        r for r in st.get('refused', [])
        if r.get('refused') == 'map' and (r.get('journal') or r.get('ref')
                                          or r.get('kind') == 'JOURNAL')]
    for r in recs:
        o, i = r.get('out'), r.get('in')
        if not isinstance(o, dict) or not isinstance(i, dict):
            continue
        os_, is_ = (str(o.get('symbol') or '').upper(),
                    str(i.get('symbol') or '').upper())
        od, idt = str(o.get('date') or '')[:10], str(i.get('date') or '')[:10]
        oa, ia = str(o.get('account') or ''), str(i.get('account') or '')
        if not (os_ and is_ and od and idt):
            continue
        try:
            units = abs(float(i.get('quantity') or o.get('quantity') or 0))
        except (TypeError, ValueError):
            units = 0.0
        if oa == ia:
            dated.append((oa, od, os_, idt, is_, units))
        else:
            # A move between two of your accounts: each leg's own day in
            # its own account (no gambit's trades span two accounts).
            out.add(oa, od, os_, renames.get(os_, os_))
            out.add(ia, idt, is_, renames.get(is_, is_))
    rows: List[Tuple[str, Any, str]] = []
    for acct in sorted(accounts):
        files = list(XL._parsed_files(cache, acct)) + [
            (SIDECAR_BROKER, cache / f'{acct}_{SIDECAR_BROKER}_transfers'
             f'.json', True)]
        for _b, f, side in files:
            if not f.is_file():
                continue
            try:
                doc = json.loads(f.read_text(encoding='utf-8'))
            except (OSError, ValueError, RecursionError):
                continue
            txs = doc.get('transactions') if isinstance(doc, dict) else None
            for t in txs if isinstance(txs, list) else []:
                if (isinstance(t, dict)
                        and journal_leg_key(t, broker=_b) is not None):
                    rows.append((acct, t, _b))
    dated += _detected_journals(rows, renames)
    out.inputs_read |= bool(recs or rows)
    # One journal the run joined and the exports show is read once.
    for acct, od, o, idt, i, units in sorted(set(dated)):
        syms = (o, i, renames.get(o, o), renames.get(i, i))
        out.add(acct, od, *syms)
        out.add(acct, idt, *syms)
        day = _journal_trade_day(trades, acct, (od, idt), o, i, units)
        if day:
            out.add(acct, day, *syms)
    for a_, b_ in sorted(lines):
        syms = (a_, b_, renames.get(a_, a_), renames.get(b_, b_))
        out.symbols.update(s for s in syms if s)
        for (acct, day), tr in trades.items():
            if _opposite_trades(tr, a_, b_):
                out.add(acct, day, *syms)
    return out


def undeclared_journal_days(cache, ticker_map=None,
                            journal: Optional[JournalDays] = None
                            ) -> List[Tuple[str, str, str, str, float]]:
    """(account, date, listing bought, listing sold, units) of each day
    with opposite trades of the same quantity on the two listings of a
    ticker.map TOBASE line that holds no journal (`journal`:
    walk_journal_symbols): the day reads in clock order, so a sale
    stamped before the buy is a short. If the units were journaled
    between the two listings (a Norbert's gambit), a .tt line
    `JOURNAL <date> <bought> <sold> <units>` declares it — the run's
    hint (second pre-release review, finding 1)."""
    if not ticker_map or not Path(ticker_map).is_file():
        return []
    from taxjson.bin.taxjson_ticker_map import load_map_file
    cache = Path(cache)
    try:
        tm = load_map_file(Path(ticker_map))
    except Exception:                               # noqa: BLE001
        return []
    pairs = {(str(a).upper(), str(b).upper())
             for a, b in (getattr(tm, 'tobase', {}) or {}).items()}
    if not pairs:
        return []
    if journal is None:
        journal = walk_journal_symbols(cache, ticker_map)
    accounts = {p.name[:-len('_base.json')] for p in cache.glob('*_base.json')
                if not p.name.endswith('_raw_base.json')}
    out: List[Tuple[str, str, str, str, float]] = []
    for (acct, day), tr in sorted(_day_trades(cache, accounts).items()):
        for a, b in sorted(pairs):
            if not _opposite_trades(tr, a, b) or journal.on(acct, day, a) \
                    or journal.on(acct, day, b):
                continue
            bought, sold = (a, b) if tr[a][0] > 1e-9 and abs(
                tr[a][0] - tr[b][1]) <= max(1e-6, 1e-6 * tr[a][0]) \
                else (b, a)
            out.append((acct, day, bought, sold, tr[sold][1]))
    return out


def books_journal_days(txs: Iterable[TaxTransaction],
                       symbols: Iterable[str]) -> JournalDays:
    """The journal days of books read with no parsed exports beside them
    (taxjson-missing-history on base files outside a project): a legacy
    ticker.map JOURNAL line's symbol (`symbols`, journal_targets) on each
    day one account's books hold a buy and a sale of it of the same
    quantity. The books' rows carry the folded symbol, so its two
    listings cannot be told apart there: the user's JOURNAL line is taken
    at its word on those days only. In a project the parsed exports
    decide (walk_journal_symbols)."""
    syms = {str(s).upper() for s in symbols}
    tot: Dict[Tuple[str, str, str], List[float]] = {}
    for t in txs:
        sym = str(t.symbol or '').upper()
        if t.action != 'BUYSELL' or sym not in syms:
            continue
        q = float(t.quantity or 0.0)
        if abs(q) < 1e-12:
            continue
        tot.setdefault((str(t.account), str(t.date or '')[:10], sym),
                       [0.0, 0.0])[0 if q > 0 else 1] += abs(q)
    out = JournalDays(syms)
    for (acct, day, sym), (b, s) in tot.items():
        if b > 1e-9 and abs(b - s) <= max(1e-6, 1e-6 * max(b, s)):
            out.add(acct, day, sym)
    return out


def _journal_day(journal_symbols: Any, t: Any) -> bool:
    """Whether `t` is on a journal's day (JournalDays.on); a caller's
    plain set of symbols reads every day of them as one."""
    if isinstance(journal_symbols, JournalDays):
        return journal_symbols.on(t.account, t.date, t.symbol)
    return str(t.symbol or '').upper() in journal_symbols


def _walk_key(t, journal_symbols: Any = None) -> Tuple:
    """The missing-history walks' order. A journal's trades and transfer
    legs of one day read buys and in-legs first whatever their clock:
    RBC stamps a day's rows with its row ORDINAL (09:30:00 + k s,
    newest-first export), so the Norbert's-gambit sale of SAMPLF.TO
    sorted ahead of the same morning's SAMPLF.U.TO buy and read as a
    one-day short — reported as missing history, and the file generator
    wrote an entry that pulled the sale off Schedule 3 (audit A2-0309 /
    A2-0636); the journal's own legs, dated the trades' settlement day,
    read out-leg first the same way. `journal_symbols` come from
    walk_journal_symbols (JournalDays: only the days that hold a
    journal). Every other day keeps the clock: a same-day sale and
    rebuy of shares bought before the data IS missing history."""
    k = event_sort_key(t, profile='missing_history_walk')
    if (journal_symbols and t.action in ('BUYSELL', 'TRANSFER')
            and _journal_day(journal_symbols, t)):
        k = (k[0], k[1], '', k[3])
    return k


def detect_missing_history(
    transactions: Iterable[TaxTransaction],
    *,
    include_options: bool = False,
    include_broker_shorts: bool = False,
    registered_accounts=None,
    country: Optional[str] = None,
    journal_symbols: Optional[Set[str]] = None,
) -> List[MissingHistoryCandidate]:
    """Walk transactions per (symbol, account, currency) and return one
    MissingHistoryCandidate per pair whose running position ever went negative.

    Only BUYSELL / ASSIGN / SPLIT / OPENING_BALANCE affect position. Cash-
    flow events (DIVIDEND, INTEREST, FEE, etc.) are ignored, matching the
    main engine's pool-update rules.

    Option symbols (OCC format like SAMPLG250620C00150000) are skipped by
    default — negative option positions are normal (sell-to-open) and
    rarely indicate truncated history. Futures (`F:`-prefixed) are
    skipped likewise — a short future is an ordinary opening position.
    Pass include_options=True to include both anyway.

    A pair whose every short-opening sale carries the broker's own
    short-sale marker (RBC "SHORT.", or IB's Trades code O — `C;O` on
    a sale that closed a long and opened a short) is a REAL short: it
    is left out unless include_broker_shorts=True (then flagged
    broker_marked_short) — a missing-history entry for it removed a real
    loss and left invented shares (audit R1-8, S058-02).

    A pair where a sale the broker codes CLOSING (IB code C, no O)
    takes the position short is missing history for certain: it is
    reported even for an option or a future (broker_says_closing) —
    otherwise the sale of a long option bought before the data reads
    as a write (audit S013-00).

    `journal_symbols` (journal_targets of the project's ticker.map):
    their same-day trades read buys first (_walk_key).
    """
    # state[(symbol, account, currency)] -> running, peak_short, first_neg, count
    state: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    # Same-date rows with equal clock times go buys before sells (the
    # walks' convention — the engines keep the export's row order, see
    # corporate_timeline._walk_rest): a Norbert's-gambit pair — sell SAMPLF.TO, buy
    # SAMPLF.U.TO the same morning, folded to one symbol by the ticker map —
    # otherwise read as an N-share short with a missing purchase.
    transactions = list(transactions)
    journal_symbols = _book_journals(transactions, journal_symbols)
    sorted_txs = _drop_duplicate_splits(sorted(
        transactions,
        key=lambda t: (_walk_key(t, journal_symbols),
                       0 if float(t.quantity or 0) > 0 else 1),
    ))
    orders = OrderStarts()

    for tx in sorted_txs:
        # TRANSFER also moves position and is now consumed by the engine,
        # so include it in the running-position walk — otherwise a
        # TRANSFER-in + sell pair would falsely register as missing history.
        if tx.action not in ('BUYSELL', 'ASSIGN', 'SPLIT', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        # Options and futures are walked too, but reported only with
        # include_options — or when the broker says a sale that took
        # them short was a CLOSING one (below). Futures (IB `F:`
        # prefix): a short is an ordinary opening position (IB codes it
        # `O`), exactly like an option sell-to-open.
        derivative = (is_option_symbol(tx.symbol)
                      or (tx.symbol or '').startswith(('F:', '/', '\\')))
        # One pool per (symbol, account) — NOT per currency: the engine
        # pools identical property regardless of the leg's native
        # currency (base conversion happens before pooling), so a CAD
        # sell against a USD buy of the same mapped symbol is one pool.
        key = (tx.symbol, tx.account, '')

        def _new_state(_deriv=derivative):
            return {
                'running': 0.0,
                'peak_short': 0.0,
                'first_negative_date': None,
                'disposition_count': 0,
                'currencies': set(),
                'marked': False,
                'unmarked': False,
                'closing': False,
                'basis': '',
                'markers': set(),
                'derivative': _deriv,
            }
        s = state.get(key)
        if s is None:
            s = state[key] = _new_state()
        if tx.currency:
            s['currencies'].add(tx.currency)

        if tx.action == 'SPLIT':
            factor = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol, tx.symbol_new)
            if new_sym:
                # SPLIT-RENAME (s.85.1(5) rollover merger, ticker change):
                # move the scaled pool onto the new ticker so its shares
                # aren't seen as appearing from nowhere. Without this, a
                # later sale of the renamed position reads as a
                # short with a missing purchase (the acquirer never had a BUY in this data).
                tgt = state.get((new_sym, tx.account, ''))
                if tgt is None:
                    tgt = state[(new_sym, tx.account, '')] = _new_state(
                        is_option_symbol(new_sym)
                        or new_sym.startswith(('F:', '/', '\\')))
                tgt['running'] += s['running'] * factor
                s['running'] = 0.0
            else:
                s['running'] *= factor
            continue

        prev = s['running']
        order_prev = orders.prev(key, tx, prev)
        s['running'] += tx.quantity

        # A disposition while running is negative is one of the events
        # consuming shares bought before the data. Catches both "ran negative on this
        # disposition" and "was already negative when this disposition fired."
        if tx.quantity < 0 and (prev < 0 or s['running'] < 0):
            s['disposition_count'] += 1
            if s['running'] < -1e-9:
                # A sale that OPENS (or extends) the short side — also
                # one that crosses zero (IB `C;O`: closed the long, the
                # rest opened a short in the same fill).
                if unbacked_close(tx, prev, order_prev):
                    # The broker says this sale CLOSED a position the
                    # data never bought (audit S013-00) — even with an
                    # O beside it (C;O on a sale with no long held).
                    s['closing'] = True
                    s['unmarked'] = True
                    s['basis'] = s['basis'] or str(
                        getattr(tx, 'broker_basis', '') or '')
                else:
                    _mk = broker_short_marker(tx)
                    if _mk:
                        s['marked'] = True
                        s['markers'].add(_mk)
                    else:
                        s['unmarked'] = True

        if s['running'] < s['peak_short']:
            s['peak_short'] = s['running']
            if s['first_negative_date'] is None:
                s['first_negative_date'] = tx.date

    out: List[MissingHistoryCandidate] = []
    for (symbol, account, currency), s in state.items():
        if s['peak_short'] >= -1e-6:
            continue
        closing = bool(s.get('closing'))
        if s.get('derivative') and not include_options and not closing:
            continue
        marked = bool(s.get('marked')) and not s.get('unmarked')
        if marked and not include_broker_shorts:
            continue
        out.append(MissingHistoryCandidate(
            symbol=symbol,
            account=account,
            currency='/'.join(sorted(s.get('currencies') or [])) or currency,
            first_negative_date=s['first_negative_date'] or '',
            peak_short=s['peak_short'],
            end_position=s['running'],
            disposition_count=s['disposition_count'],
            registered=is_registered_account(account, registered_accounts,
                                             country),
            broker_marked_short=marked,
            broker_says_closing=closing,
            broker_basis=s.get('basis') or '',
            short_marker=' / '.join(sorted(s.get('markers') or ())),
        ))
    out.sort(key=lambda c: (c.symbol, c.account))
    return out


@dataclass
class UnbackedCover:
    """A buy the broker marks as COVERING a short (RBC "COVER SHORT.",
    IB Trades code C on a buy) while the data holds no short to cover:
    the short was opened before the data. Covering it is the
    disposition, so that sale's gain or loss is missing — and the
    engine books the buy as a new long (audit A2-0306, IB twin
    A2-0175)."""
    symbol: str
    account: str
    date: str                 # the cover's date on the caller's basis
    unbacked_qty: float       # bought to cover beyond any short held
    marker: str               # 'COVER SHORT.' / 'IB code C'
    proceeds: float           # the cover's cost (|net_amount|)


def _derivative_symbol(sym: str) -> bool:
    return (is_option_symbol(sym)
            or (sym or '').startswith(('F:', '/', '\\')))


def detect_unbacked_covers(
    transactions: Iterable[TaxTransaction],
    *,
    include_options: bool = False,
    date_basis: str = 'settle',
    journal_symbols: Optional[Set[str]] = None,
) -> List[UnbackedCover]:
    """Broker-marked covers the data's own short cannot back — the
    mirror of a sale that goes short. Walked like detect_missing_history (per
    (symbol, account), renames and splits followed, same-moment buys
    first). Options and futures only with include_options: a buy coded
    C of a contract written before the data is option-boundary's case.
    Same-moment rows read SELLS first here (the opposite of the
    short-side walk, for the same reason): a short and its cover
    stamped alike is no evidence that the short predates the data."""
    run: Dict[Tuple[str, str], float] = {}
    orders = OrderStarts()
    out: List[UnbackedCover] = []
    transactions = list(transactions)
    journal_symbols = _book_journals(transactions, journal_symbols)

    def _key(t):
        k = _walk_key(t, journal_symbols)
        return k[:3] + ((3 - k[3]) if k[3] in (1, 2) else k[3],)
    for tx in _drop_duplicate_splits(sorted(transactions, key=_key)):
        key = (tx.symbol, tx.account)
        if tx.action == 'SPLIT':
            ratio = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol,
                                           getattr(tx, 'symbol_new', ''))
            if new_sym:
                nk = (new_sym, tx.account)
                run[nk] = run.get(nk, 0.0) + run.pop(key, 0.0) * ratio
            elif key in run:
                run[key] *= ratio
            continue
        if tx.action not in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                             'TRANSFER'):
            continue
        prev = run.get(key, 0.0)
        q = float(tx.quantity or 0.0)
        order_prev = orders.prev(key, tx, prev)
        run[key] = prev + q
        if q <= 1e-9 or tx.action != 'BUYSELL':
            continue
        if _derivative_symbol(tx.symbol or '') and not include_options:
            continue
        if _BROKER_SHORT_RE.search((tx.description or '').upper()):
            marker = 'COVER SHORT.'
            unbacked = q - max(0.0, -prev)
        elif unbacked_close(tx, prev, order_prev):
            marker = 'IB code C'
            unbacked = q - max(0.0, -prev)
            if 'O' in open_close_codes(tx):
                # C;O with no short when the order began: the whole
                # closing part is unbacked; the broker does not say
                # how much of the fill closed — the fill is reported.
                unbacked = q
        else:
            continue
        if unbacked <= 1e-9:
            continue
        out.append(UnbackedCover(
            symbol=tx.symbol, account=tx.account,
            date=_basis_date(tx, date_basis),
            unbacked_qty=unbacked, marker=marker,
            proceeds=round(abs(float(tx.net_amount or 0.0)), 2)))
    out.sort(key=lambda c: (c.symbol, c.account, c.date))
    return out


@dataclass
class StaleMissingHistoryEntry:
    """A missing_history.json entry today's detection would NOT propose: the
    broker marks the sales that took it short as short sales (a real
    short), or it is an option / future whose short side the broker
    never coded CLOSING (a written contract). Applying it moves a real
    short's (or a write's) gain off the totals into manual reporting
    and leaves invented units (audit A2-0308 / A2-0310 / A2-0311 /
    A2-0637 / A2-0638 / A2-0639, R1-8)."""
    symbol: str
    account: str
    reason: str               # 'broker-short' | 'derivative' | 'complete'
    marker: str = ''          # how the broker marks the short
    file_name: str = MISSING_HISTORY_FILE   # the file that lists it


def stale_missing_history_entries(
    transactions: Iterable[TaxTransaction],
    pairs: Optional[Set[Tuple[str, str]]] = None,
    *, phantoms: Optional[Set[Tuple[str, str]]] = None,
    file_name: Optional[str] = None,
    complete: bool = False,
) -> List[StaleMissingHistoryEntry]:
    """The listed pairs that are a broker-marked real short or a
    derivative the broker did not code CLOSING (the two cases
    detect_missing_history leaves out by default) — and, with
    `complete=True`, the listed pairs whose rows never go short any more
    (reason 'complete': the purchase is now in the books, so the entry
    does nothing). `phantoms=`: the old keyword for `pairs`."""
    if pairs is None:
        pairs = phantoms
    if not pairs:
        return []
    name = file_name or _source_name(pairs)
    listed = {(str(s).upper(), str(a)) for s, a in pairs}
    txs = list(transactions)
    cands = detect_missing_history(txs, include_options=True,
                                   include_broker_shorts=True)
    out: List[StaleMissingHistoryEntry] = []
    for c in cands:
        pair = (str(c.symbol).upper(), c.account)
        if pair not in listed:
            continue
        if c.broker_marked_short:
            out.append(StaleMissingHistoryEntry(c.symbol, c.account,
                                                'broker-short',
                                                c.short_marker or 'SHORT.',
                                                file_name=name))
        elif (_derivative_symbol(c.symbol or '')
              and not c.broker_says_closing):
            out.append(StaleMissingHistoryEntry(c.symbol, c.account,
                                                'derivative',
                                                file_name=name))
    if complete:
        # Listed, has rows, never goes short: the history is complete.
        # (A pair with no rows at all is a spelling question, or another
        # account's books — not reported here.) Rename chains: the walk
        # reports a short under the NEW symbol, so an entry naming either
        # end of a chain that goes short is not complete.
        short = {(str(c.symbol).upper(), c.account) for c in cands}
        chain: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}
        for t in txs:
            if t.action == 'SPLIT':
                new_sym = normalize_symbol_new(t.symbol,
                                               getattr(t, 'symbol_new', ''))
                if new_sym:
                    a = (str(t.symbol).upper(), t.account)
                    b = (str(new_sym).upper(), t.account)
                    chain.setdefault(a, set()).add(b)
                    chain.setdefault(b, set()).add(a)

        def _linked(p):
            seen, stack = set(), [p]
            while stack:
                q = stack.pop()
                if q in seen:
                    continue
                seen.add(q)
                stack.extend(chain.get(q, ()))
            return seen
        have = {(str(t.symbol).upper(), t.account) for t in txs
                if t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER')}
        for pair in sorted(listed):
            if pair not in have or _linked(pair) & short:
                continue
            out.append(StaleMissingHistoryEntry(pair[0], pair[1],
                                                'complete',
                                                file_name=name))
    return out


def complete_entry_message(symbol: str, account: str,
                           file_name: str = MISSING_HISTORY_FILE, *,
                           more: Sequence[Tuple[str, str]] = ()) -> str:
    """A missing-history entry whose position never goes short: the
    purchase is in the books now (an older export or a .tt line was
    added), so the entry does nothing. `more`: further such entries,
    said in the same line."""
    if not more:
        return (f"{file_name} lists {symbol} / {account}, but its rows "
                f"never go short any more — the purchase is in the books "
                f"now (an older export or a .tt line), so the entry does "
                f"nothing. Remove it from {file_name}.")
    pairs = [(symbol, account)] + list(more)
    shown = ", ".join(f"{s} / {a}" for s, a in pairs[:6]) + (
        f" +{len(pairs) - 6} more" if len(pairs) > 6 else "")
    return (f"{file_name} lists {len(pairs)} entries whose rows never go "
            f"short any more ({shown}) — their purchases are in the books "
            f"now (an older export or a .tt line), so the entries do "
            f"nothing. Remove them from {file_name} (`taxjson "
            f"find-missing-history` lists them under STALE).")


def stale_entry_message(e: StaleMissingHistoryEntry) -> str:
    """One ATTENTION line for a stale entry (run, gains and every other
    missing-history applier print it; find-missing-history lists it)."""
    if e.reason == 'complete':
        return complete_entry_message(e.symbol, e.account, e.file_name)
    if e.reason == 'broker-short':
        how = ("codes the sale O (opening)" if e.marker == 'IB code O'
               else f"marks the sales {e.marker}")
        return (f"{e.file_name} lists {e.symbol} / {e.account}, but the "
                f"broker {how} — a REAL short, not missing history. The "
                f"entry moves the short's gain or loss off the totals "
                f"into manual reporting and invents shares that were "
                f"never bought; remove it from {e.file_name}.")
    return (f"{e.file_name} lists {e.symbol} / {e.account}, an option or "
            f"future the broker never coded CLOSING — its short side reads "
            f"as a WRITE (sell-to-open), not missing history. The entry "
            f"moves the premium's gain off the totals and invents a "
            f"long contract; remove it unless the contract was bought "
            f"before the data.")


@dataclass
class MissingHistoryRow:
    """A missing-history candidate enriched with whether — and how much — it bears on
    a specific tax year."""
    candidate: MissingHistoryCandidate
    affects_year: bool          # has an in-year disposition drawing from short
    # count of those in-year SALES drawing on it (a cover is no sale)
    in_year_dispositions: int
    in_year_proceeds: float     # their summed proceeds (dollar-impact gauge)
    last_in_year_date: str
    # Any row of the pair (a trade, a transfer, income) dated in the year,
    # whether or not it draws on the missing basis — `taxjson run` lists
    # such a pair on its console (year_listed).
    in_year_activity: bool = False
    # The position is still short in the books at the year's start (its
    # missing purchase changes no gain of the year unless a row of the
    # year draws on it).
    short_at_year_start: bool = False
    # Canada: other taxable accounts with a row of the symbol dated in
    # the year — one ACB pool across them (s.47, pool_activity), so this
    # pair's short moves their gain.
    pooled_with: Tuple[str, ...] = ()
    # The in-year sales that drew on the short: (row id, units sold
    # beyond the position) — what lib/first_run.engine_booking looks up
    # in the gains files.
    in_year_short_sales: Tuple[Tuple[str, float], ...] = ()

    @property
    def year_listed(self) -> bool:
        """The pair bears on the year: a row of the year draws on the
        missing basis (affects_year), touches the pair at all, or (Canada)
        trades the symbol in another taxable account of its ACB pool.
        What `taxjson run` lists one by one; the rest are the 'NOT
        relevant' pairs it counts in one line."""
        return bool(self.affects_year or self.in_year_activity
                    or self.pooled_with)


def _basis_date(tx, date_basis: str) -> str:
    if date_basis == 'settle':
        return tx.date_settle or tx.date or ''
    return tx.date or ''


def assess_tax_year_relevance(
    transactions: Iterable[TaxTransaction],
    candidates: List[MissingHistoryCandidate],
    year: Any = None,
    *,
    date_basis: str = 'settle',
    journal_symbols: Optional[Set[str]] = None,
    pool: Optional[Dict[Tuple[str, str], Set[str]]] = None,
) -> List[MissingHistoryRow]:
    """For each missing-history candidate, decide whether its missing history actually
    bears on tax year `year`.

    A candidate "affects" the year when, in that year, a row draws on the
    SHORT (missing-purchase) state:
      - a disposition (qty < 0) with the running position negative on
        either side of it — its cost basis is the missing history; or
      - a BUY that covers a short carried in (the engine books the cover
        as a short-close gain or loss in the year — audit S021-01 /
        S076-07; this is also the rule `--suggest-missing-history` uses).
    A clean sale AFTER the pool has drained back through zero (basis fully
    known) does NOT count.

    The walk follows rename-SPLITs (shares move to the new symbol, as in
    detect_missing_history — audit S075-13); a short carried into the new
    symbol keeps its pair: the new symbol's rows of the year (a cover, a
    sale, any activity) count for the old pair too, down a chain of
    renames (pre-release review M5). It orders same-moment rows buys
    first (the missing_history_walk profile — S075-12). The YEAR of a row is its
    date on `date_basis` ('settle' — the CRA default and the engine's
    year — or 'trade'), so a Dec-31 trade settling in January belongs to
    January's year (S075-16).

    Each row also says whether ANY row of the pair falls in the year
    (in_year_activity), whether the books hold the pair short at the
    year's start (short_at_year_start), and — `pool`, from pooled_with —
    which other accounts of its cost pool trade the symbol in the year:
    MissingHistoryRow.year_listed, the one test `taxjson run`'s console
    and find-missing-history share.

    `year` may be int or str (matched against the date prefix); None means "no
    year scope" — every candidate is reported as relevant, with the totals of
    its sales drawing on the missing history across all years. Returns one row per candidate, in the
    candidates' order."""
    year_str = str(year) if year is not None else None
    year_start = f"{year_str}-01-01" if year_str is not None else None
    run: Dict[Tuple[str, str], float] = {}
    stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
    # Any row of the pair dated in the year, and the running position
    # just before the year (in_year_activity / short_at_year_start).
    active: Set[Tuple[str, str]] = set()
    before: Dict[Tuple[str, str], float] = {}
    # A short carried through a rename (a SPLIT with symbol_new — a
    # broker's, a .tt line's or a dated ticker.map RENAME's): the new
    # symbol's rows draw on the OLD pair's missing history, so its
    # activity and draws count for the old pair too (pre-release review
    # M5). key -> the predecessor pairs whose short it carries.
    origins: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}

    def _with_origins(k: Tuple[str, str]) -> Set[Tuple[str, str]]:
        return {k} | origins.get(k, set())

    transactions = list(transactions)
    journal_symbols = _book_journals(transactions, journal_symbols)
    for tx in _drop_duplicate_splits(
            sorted(transactions,
                   key=lambda t: _walk_key(t, journal_symbols))):
        key = (tx.symbol, tx.account)
        d = _basis_date(tx, date_basis)
        if tx.action == 'SPLIT':
            ratio = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol,
                                           getattr(tx, 'symbol_new', ''))
            if new_sym:
                nk = (new_sym, tx.account)
                moved = run.pop(key, 0.0) * ratio
                if moved < -1e-9 and nk != key:
                    origins.setdefault(nk, set()).update(
                        _with_origins(key))
                run[nk] = run.get(nk, 0.0) + moved
                if year_start is not None and d < year_start:
                    before[nk] = run[nk]
                    before[key] = 0.0
            elif key in run:
                run[key] *= ratio
                if year_start is not None and d < year_start:
                    before[key] = run[key]
            continue
        if year_str is not None and d.startswith(year_str):
            active.update(_with_origins(key))
        if tx.action not in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        prev = run.get(key, 0.0)
        cur = prev + tx.quantity
        run[key] = cur
        if year_start is not None and d < year_start:
            before[key] = cur
        draws = ((tx.quantity < 0 and (prev < -1e-9 or cur < -1e-9))
                 or (tx.quantity > 0 and prev < -1e-9
                     and tx.action in ('BUYSELL', 'ASSIGN')))
        if not draws:
            continue
        if year_str is not None and not d.startswith(year_str):
            continue
        for k in _with_origins(key):
            st = stats.setdefault(k, {'n': 0, 'proceeds': 0.0, 'last': '',
                                      'sales': [], 'draws': 0})
            st['draws'] += 1
            # A cover (a purchase) bears on the year but is no sale: its
            # amount is a cost, never proceeds (QA F4: a short and its
            # cover read as 2 sales, the cover's cost added to proceeds).
            if tx.quantity < 0:
                st['n'] += 1
                st['proceeds'] += abs(getattr(tx, 'net_amount', 0.0)
                                      or 0.0)
            if d > st['last']:
                st['last'] = d
            if tx.quantity < 0 and cur < -1e-9:
                st['sales'].append((str(tx.id or ''),
                                    min(-tx.quantity, -cur)))

    def _carriers(ck: Tuple[str, str]) -> List[Tuple[str, str]]:
        """The pair and every later pair carrying its short."""
        return [ck] + [k for k, o in origins.items() if ck in o]

    out: List[MissingHistoryRow] = []
    for c in candidates:
        st = stats.get((c.symbol, c.account),
                       {'n': 0, 'proceeds': 0.0, 'last': '', 'sales': [],
                        'draws': 0})
        out.append(MissingHistoryRow(
            candidate=c,
            affects_year=(year_str is None or st['draws'] > 0),
            in_year_dispositions=st['n'],
            in_year_proceeds=round(st['proceeds'], 2),
            last_in_year_date=st['last'],
            in_year_activity=(year_str is None
                              or (c.symbol, c.account) in active),
            short_at_year_start=(year_str is not None and any(
                before.get(k, 0.0) < -1e-9
                for k in _carriers((c.symbol, c.account)))),
            pooled_with=tuple(sorted(
                (pool or {}).get((c.symbol, c.account), ()))),
            in_year_short_sales=tuple(st['sales']),
        ))
    return out


def pool_activity(transactions: Iterable[TaxTransaction], year: Any, *,
                  pooled_accounts: Iterable[str],
                  date_basis: str = 'settle'
                  ) -> Dict[str, Set[str]]:
    """{symbol: {account}} — the accounts of one cost pool
    (`pooled_accounts`) with a row of the symbol dated in `year`. In a
    Canadian project every taxable account's identical shares are one
    ACB pool (s.47; lib/country.basis_pooled_across_accounts): a short
    one account's missing purchase leaves in that pool moves another
    account's gain of the year. {} without a year."""
    if year is None:
        return {}
    ys = str(year)
    pooled = set(pooled_accounts)
    out: Dict[str, Set[str]] = {}
    for tx in transactions:
        if tx.account in pooled and _basis_date(tx, date_basis) \
                .startswith(ys) and tx.action != 'SPLIT':
            out.setdefault(tx.symbol, set()).add(tx.account)
    return out


def pooled_with(candidates: Iterable[MissingHistoryCandidate],
                activity: Dict[str, Set[str]],
                pooled_accounts: Iterable[str],
                successors: Optional[Dict[Tuple[str, str], Set[str]]]
                = None) -> Dict[Tuple[str, str], Set[str]]:
    """{(symbol, account): other accounts of its pool active in the
    year} for the candidates in a pooled account (pool_activity); a
    candidate renamed since (`successors`, rename_successors) counts its
    new symbols' activity too."""
    pooled = set(pooled_accounts)
    out: Dict[Tuple[str, str], Set[str]] = {}
    for c in candidates:
        if c.account not in pooled:
            continue
        others: Set[str] = set()
        for sym in {c.symbol} | set((successors or {}).get(
                (c.symbol, c.account), ())):
            others |= activity.get(sym, set())
        others -= {c.account}
        if others:
            out[(c.symbol, c.account)] = others
    return out


def rename_successors(transactions: Iterable[TaxTransaction]
                      ) -> Dict[Tuple[str, str], Set[str]]:
    """{(symbol, account): every symbol it was renamed to, directly or
    down a chain} from the books' rename SPLIT rows (symbol_new: a
    broker's event, a .tt line, a dated ticker.map RENAME). An undated
    ticker.map rename has already rewritten the rows to the new symbol."""
    direct: Dict[Tuple[str, str], Set[str]] = {}
    for t in transactions:
        if t.action != 'SPLIT':
            continue
        new = normalize_symbol_new(t.symbol, getattr(t, 'symbol_new', ''))
        if new and new != t.symbol:
            direct.setdefault((t.symbol, t.account), set()).add(new)
    out: Dict[Tuple[str, str], Set[str]] = {}
    for (sym, acct) in direct:
        seen: Set[str] = set()
        todo = list(direct[(sym, acct)])
        while todo:
            n = todo.pop()
            if n in seen or n == sym:
                continue
            seen.add(n)
            todo.extend(direct.get((n, acct), ()))
        out[(sym, acct)] = seen
    return out



def year_pool(transactions: Iterable[TaxTransaction],
              candidates: Iterable[MissingHistoryCandidate], year: Any, *,
              country: Optional[str], registered: Optional[Dict[str, bool]],
              date_basis: str = 'settle'
              ) -> Dict[Tuple[str, str], Set[str]]:
    """The `pool=` of assess_tax_year_relevance for a project: in a
    country whose cost basis pools identical property across taxable
    accounts (Canada, s.47 — lib/country.basis_pooled_across_accounts),
    the other taxable accounts that trade each candidate's symbol in the
    year. {} for the US (basis per account), outside a project (no
    country or no account types) and without a year."""
    from taxjson.lib.country import basis_pooled_across_accounts
    if (year is None or not country or not registered
            or not basis_pooled_across_accounts(country)):
        return {}
    txs = list(transactions)
    accounts = {t.account for t in txs if t.account}
    pooled = {a for a in accounts if registered.get(a) is False}
    return pooled_with(candidates,
                       pool_activity(txs, year, pooled_accounts=pooled,
                                     date_basis=date_basis), pooled,
                       successors=rename_successors(txs))


def classify_year_shorts(transactions: Iterable[TaxTransaction], year: Any,
                         *, country: Optional[str],
                         registered: Optional[Dict[str, bool]],
                         date_basis: str = 'settle',
                         journal_symbols: Optional[Set[str]] = None
                         ) -> Dict[Tuple[str, str], MissingHistoryRow]:
    """{(symbol, account): MissingHistoryRow} for every pair that goes
    short in a project's books (options and broker-marked shorts
    included), judged against `year` the way find-missing-history
    judges it (assess_tax_year_relevance with the project's pool):
    `taxjson run` lists a pair whose row is `year_listed` and counts the
    rest in one line; `--write-missing-history --outside-year` writes
    only the rest."""
    txs = list(transactions)
    cands = detect_missing_history(txs, include_options=True,
                                   include_broker_shorts=True,
                                   registered_accounts=registered or None,
                                   country=country,
                                   journal_symbols=journal_symbols)
    pool = year_pool(txs, cands, year, country=country,
                     registered=registered, date_basis=date_basis)
    return {(r.candidate.symbol, r.candidate.account): r
            for r in assess_tax_year_relevance(
                txs, cands, year, date_basis=date_basis,
                journal_symbols=journal_symbols, pool=pool)}


def missing_history_suspect(c: MissingHistoryCandidate) -> bool:
    """A pair that goes short because a sale had nothing to close — a
    purchase missing from the files, not a short: no broker short-sale
    marker, and a share or coin (an option or a future sold to open is
    an ordinary short) unless the broker coded the sale CLOSING (IB code
    C) or the account is registered. The pairs find-missing-history
    reports as TRUNCATED HISTORY, `taxjson run` warns about and `taxjson
    list` marks `missing history?`."""
    if c.broker_marked_short:
        return False
    if c.broker_says_closing or c.registered:
        return True
    return not _derivative_symbol(c.symbol)


def missing_history_suspects(transactions: Iterable[TaxTransaction], *,
                             registered: Optional[Dict[str, bool]] = None,
                             country: Optional[str] = None,
                             journal_symbols: Optional[Set[str]] = None
                             ) -> Set[Tuple[str, str]]:
    """{(symbol, account)} of every missing_history_suspect pair in the
    books."""
    return {(c.symbol, c.account) for c in detect_missing_history(
                transactions, include_options=True,
                include_broker_shorts=True,
                registered_accounts=registered or None, country=country,
                journal_symbols=journal_symbols)
            if missing_history_suspect(c)}

# Description keywords that mark a broker row as a corporate action — used
# only to annotate WHY a $0-cost acquisition happened, not to gate detection.
_CORP_ACTION_RE = re.compile(
    r'\b(MGR|MERGER|REORG|SPIN[\s\-]?OFF|SPINOFF|ARRANGEMENT|RECEIVED|'
    r'CONVERSION|EXCHANGE|REDEMPTION|STOCK\s+DIV)\b', re.IGNORECASE)


@dataclass
class ZeroBasisRow:
    """A (symbol, account) that acquired shares at ~$0 cost (typically an
    unhandled corporate action) and later sold them, so the $0 basis inflates
    the realized gain."""
    symbol: str
    account: str
    currency: str
    zero_cost_qty: float        # shares acquired at ~$0 cost
    acquisition_date: str       # first such acquisition
    description: str            # representative row text (corp-action hint)
    looks_corp_action: bool     # description matched a corp-action keyword
    affects_year: bool          # has a disposition-while-contaminated in `year`
    in_year_dispositions: int
    in_year_proceeds: float
    # Shares of the pool still held at the end of the data while it
    # holds $0-cost shares (include_held): their cost is understated
    # now, and their sale will overstate a gain.
    still_held_qty: float = 0.0
    sold: bool = True           # some $0-cost shares were sold
    # Every $0 acquisition of the pool is a spin-off whose election
    # DECLARES the $0 (corp_actions.declares_zero_value): answered, an
    # Info, not missing cost. `event_ids`: the corp events of its $0
    # acquisitions.
    declared: bool = False
    event_ids: Tuple[str, ...] = ()


def detect_zero_basis_acquisitions(
    transactions: Iterable[TaxTransaction],
    year: Any = None,
    *,
    include_options: bool = False,
    date_basis: str = 'settle',
    include_held: bool = False,
    stock_dividends_spread: bool = False,
    declared_events: Iterable[Tuple[str, str]] = (),
) -> List[ZeroBasisRow]:
    """Flag (symbol, account) pairs that ACQUIRED shares at ~$0 cost — almost
    always a broker corporate-action row (a merger/spinoff "shares received"
    line booked with value 0) the pipeline couldn't assign a basis to — and
    then DISPOSED of them, so the missing basis silently inflates the realized
    gain.

    These never go negative (received +N, sold −N nets to zero), so the
    negative-holdings detector (`detect_missing_history`) can't see them — this is its
    complement.

    A disposition only counts while the pool actually holds $0-basis shares:
    the contamination is tracked from the $0 acquisition until the pool drains
    back through zero (mirroring ACB averaging), so a clean sale before the
    corp action — or after a full drain and fresh buy — is not flagged.

    `year` (int/str/None): when set, `affects_year` is True only if such a
    disposition falls in that year (its date on `date_basis` — the
    engine's year, audit S075-16); None reports every flagged pair.
    Rename-SPLITs carry the pool (and its $0 contamination) to the new
    symbol, as detect_missing_history does (audit S075-19).

    `include_held`: also report pairs whose pool still holds shares at
    the end of the data while it carries $0-cost shares, sold or not
    (`still_held_qty`; affects_year stays about in-year sales) — the
    positions whose cost is understated today.

    `stock_dividends_spread` (a US project, lib/country
    .stock_dividend_zero_cost): a stock dividend's shares share the old
    shares' basis, so they are never $0-cost here.

    `declared_events` ({(account, corp event id)}, corp_actions
    .declared_zero_value_events): spin-offs whose election declares the
    $0 value. A pool whose every $0 acquisition is one of them is
    reported with `declared` set — the user's answer, listed apart.
    """
    year_str = str(year) if year is not None else None
    declared = set(declared_events)
    transactions = list(transactions)
    # A positive ADJUST on the same (symbol, account) from 31 days before
    # to 7 days after a $0 acquisition is its cost — the documented fix
    # for a stock dividend (a .tt ADJUST or [[distributions]]), and the
    # window the gains stage's stock-dividend ATTENTION uses. Such shares
    # are not $0-basis.
    cost_adjusts: Dict[Tuple[str, str], List[str]] = {}
    for tx in transactions:
        if tx.action == 'ADJUST' and float(tx.net_amount or 0.0) > 0:
            cost_adjusts.setdefault((tx.symbol, tx.account), []).append(
                str(tx.date)[:10])

    def _cost_added(tx) -> bool:
        dates = cost_adjusts.get((tx.symbol, tx.account))
        if not dates:
            return False
        try:
            d0 = date.fromisoformat(str(tx.date)[:10])
        except ValueError:
            return False
        lo = (d0 - timedelta(days=31)).isoformat()
        hi = (d0 + timedelta(days=7)).isoformat()
        return any(lo <= d <= hi for d in dates)

    state: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    # Same dedupe as the module's other two walks (detect_missing_history,
    # assess_tax_year_relevance): per-broker duplicate SPLIT rows would
    # double-apply the ratio to `running`, so the drain-to-zero check
    # never cleared and clean later buys stayed flagged contaminated.
    for tx in _drop_duplicate_splits(sorted(
            transactions,
            key=lambda t: event_sort_key(t, profile='missing_history_walk'))):
        if not include_options and is_option_symbol(tx.symbol):
            continue
        # An ASSIGN stock leg (Questrade, Webull) moves the pool like a
        # trade — the module's other two walks count it; skipping it
        # here missed a $0-basis spin-off sold by assignment and flagged
        # a later clean sale instead (audit A2-0307). The option leg of
        # an assignment closes the contract at no value: never a "$0
        # acquisition".
        if tx.action == 'ASSIGN' and is_option_symbol(tx.symbol):
            continue
        if tx.action not in ('BUYSELL', 'ASSIGN', 'SPLIT'):
            continue
        if tx.action == 'SPLIT':
            # Every currency-slice of this (symbol, account) pool — a
            # SPLIT row's own currency field is not the trades' key.
            ratio = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol,
                                           getattr(tx, 'symbol_new', ''))
            for key in [k for k in state
                        if k[0] == tx.symbol and k[1] == tx.account]:
                old = state[key]
                if not new_sym:
                    old['running'] *= ratio
                    continue
                t = state.setdefault((new_sym, key[1], key[2]), {
                    'running': 0.0, 'active': False, 'zero_qty': 0.0,
                    'acq_date': '', 'desc': '', 'corp': False,
                    'any_disp': 0, 'in_year': 0, 'in_year_proc': 0.0,
                    'undeclared': 0.0, 'events': [],
                })
                t['running'] += old['running'] * ratio
                t['zero_qty'] += old['zero_qty'] * ratio
                t['undeclared'] += old['undeclared'] * ratio
                t['events'] += [e for e in old['events']
                                if e not in t['events']]
                t['active'] = t['active'] or old['active']
                t['acq_date'] = t['acq_date'] or old['acq_date']
                if old['corp'] and not t['corp']:
                    t['desc'], t['corp'] = old['desc'], True
                elif not t['desc']:
                    t['desc'] = old['desc']
                # The old line keeps its own disposition record; its
                # shares (and contamination) now live on the new line.
                old['running'] = 0.0
                old['active'] = False
            continue
        key = (tx.symbol, tx.account, tx.currency or '')
        s = state.setdefault(key, {
            'running': 0.0, 'active': False, 'zero_qty': 0.0, 'acq_date': '',
            'desc': '', 'corp': False, 'any_disp': 0, 'in_year': 0,
            'in_year_proc': 0.0, 'undeclared': 0.0, 'events': [],
        })
        qty = float(tx.quantity or 0.0)
        cost = abs(float(tx.net_amount or 0.0))
        price = abs(float(tx.price or 0.0))
        if qty > 1e-9:                                   # acquisition
            if (cost < 1e-6 and price < 1e-6             # ...at ~$0 cost
                    and not _cost_added(tx)
                    and not (stock_dividends_spread
                             and is_stock_dividend(tx))):
                s['active'] = True
                s['zero_qty'] += qty
                eid = getattr(tx, 'corp_event_id', '') or ''
                if eid and eid not in s['events']:
                    s['events'].append(eid)
                if not eid or (tx.account, eid) not in declared:
                    s['undeclared'] += qty
                if not s['acq_date']:
                    s['acq_date'] = tx.date
                desc = tx.description or ''
                if _CORP_ACTION_RE.search(desc) and not s['corp']:
                    s['desc'], s['corp'] = desc, True
                elif not s['desc']:
                    s['desc'] = desc
            s['running'] += qty
        elif qty < -1e-9:                                # disposition
            if s['active']:
                s['any_disp'] += 1
                if year_str is None or _basis_date(
                        tx, date_basis).startswith(year_str):
                    s['in_year'] += 1
                    s['in_year_proc'] += cost
            s['running'] += qty
            if s['running'] <= 1e-6:                      # pool drained — clean
                s['active'] = False

    out: List[ZeroBasisRow] = []
    for (symbol, account, currency), s in state.items():
        held = (s['running'] if include_held and s['active']
                and s['running'] > 1e-6 else 0.0)
        if s['zero_qty'] <= 0 or (s['any_disp'] == 0 and not held):
            continue                                      # no $0 basis hit a sale
        out.append(ZeroBasisRow(
            symbol=symbol, account=account, currency=currency,
            zero_cost_qty=round(s['zero_qty'], 4),
            acquisition_date=s['acq_date'], description=s['desc'][:80],
            looks_corp_action=s['corp'],
            affects_year=(s['any_disp'] > 0
                          and (year_str is None or s['in_year'] > 0)),
            in_year_dispositions=s['in_year'],
            in_year_proceeds=round(s['in_year_proc'], 2),
            still_held_qty=round(held, 4),
            sold=s['any_disp'] > 0,
            declared=s['undeclared'] <= 1e-9,
            event_ids=tuple(s['events']),
        ))
    out.sort(key=lambda r: (r.symbol, r.account))
    return out


# Thousands commas in the ratio ('1 NEW = 1,000 OLD'): the corp-actions
# extractor's own number pattern (audit S073-14).
from taxjson.lib.corp_actions import _RBC_NUM
_MERGER_TO_RE = re.compile(
    rf'\bMERGER\s+TO\s+(.+?)(?:\s+{_RBC_NUM}\s+NEW|\s*$)', re.I)
_RATIO_RE = re.compile(
    rf'({_RBC_NUM})\s+NEW\s*=\s*({_RBC_NUM})\s+OLD', re.I)
_OLDCO_RE = re.compile(r'^\s*(?:MGR|MERGER)\s*[-:]?\s*(.+?)\s+MERGER\s+TO\b', re.I)
_RECVCO_RE = re.compile(
    r'^\s*(?:MGR|MERGER)\s*[-:]?\s*(.+?)\s+(?:SHRS|SHARES)\s+RECEIVED', re.I)
_CO_SUFFIX_RE = re.compile(
    r'\b(CORPORATION|CORP|INCORPORATED|INC|LTD|LIMITED|COMPANY|CO|PLC|SA|NV|AG|'
    r'HOLDINGS|GROUP)\b', re.I)


def _norm_company(name: str) -> str:
    """Normalize a company name for fuzzy matching: drop common suffixes and
    non-alphanumerics so 'CHEVRON CORPORATION' == 'CHEVRON'."""
    return re.sub(r'[^A-Z0-9]', '', _CO_SUFFIX_RE.sub('', (name or '').upper()))


@dataclass
class MergerLink:
    """A reconstructed merger: an old symbol removed and a new symbol received,
    both as $0-value broker corp-action rows on the same date/account."""
    date: str
    account: str
    old_symbol: str
    old_qty: float
    old_company: str
    new_symbol: str
    new_qty: float
    new_company: str
    ratio: float            # new shares per old, if parseable (else 0.0)


MERGER_LINK_DAYS = 7


def _day_gap(a: str, b: str) -> int:
    try:
        from datetime import datetime
        return abs((datetime.strptime(str(a)[:10], '%Y-%m-%d')
                    - datetime.strptime(str(b)[:10], '%Y-%m-%d')).days)
    except ValueError:
        return 10 ** 6


def detect_corp_action_links(
    transactions: Iterable[TaxTransaction],
    *,
    include_options: bool = False,
) -> List[MergerLink]:
    """Pair a broker's split-across-symbols merger rows into single events.

    Brokers (RBC here) book a merger as TWO $0-value rows: the old shares
    removed under a temporary symbol with a "<OLDCO> MERGER TO <NEWCO>"
    description, and the new shares received under the acquirer's symbol with
    "<NEWCO> SHRS RECEIVED THRU MERGER". The pipeline treats these as an
    unrelated short and a $0-basis buy. This reconstructs the link so the two
    can be reported as one event - and makes clear that what's missing is the
    OLD position's ACB (often its purchase predates the data window).

    Pairs within the same (account, date): first by matching the removal's
    'MERGER TO <NEWCO>' against the receipt's company, then by a lone
    removal/receipt fallback."""
    removals, receipts = [], []
    for tx in transactions:
        if not include_options and is_option_symbol(tx.symbol):
            continue
        if tx.action != 'BUYSELL' or abs(float(tx.net_amount or 0.0)) >= 1e-6:
            continue
        desc = tx.description or ''
        if not _CORP_ACTION_RE.search(desc):
            continue
        qty = float(tx.quantity or 0.0)
        if qty < -1e-9 and re.search(r'MERGER\s+TO', desc, re.I):
            removals.append(tx)
        elif qty > 1e-9 and re.search(r'RECEIVED', desc, re.I):
            receipts.append(tx)

    links: List[MergerLink] = []
    used: set = set()
    for rem in removals:
        m = _MERGER_TO_RE.search(rem.description or '')
        target = _norm_company(m.group(1)) if m else ''
        # A receipt can post a few days after the removal (audit
        # S075-23): a NAME-matched receipt within MERGER_LINK_DAYS links,
        # nearest first (the RBC reorganization pairing allows ±7 days);
        # the lone fallback stays same-date.
        near = sorted(
            ((i, rc) for i, rc in enumerate(receipts)
             if i not in used and rc.account == rem.account
             and _day_gap(rc.date, rem.date) <= MERGER_LINK_DAYS),
            key=lambda e: _day_gap(e[1].date, rem.date))
        same = [(i, rc) for i, rc in near if rc.date == rem.date]
        pick = None
        for i, rc in near:                                  # 1) name match
            rcm = _RECVCO_RE.search(rc.description or '')
            rcco = _norm_company(rcm.group(1)) if rcm else _norm_company(rc.symbol)
            if target and rcco and (target in rcco or rcco in target):
                pick = (i, rc)
                break
        if pick is None and len(same) == 1:                 # 2) lone fallback
            pick = same[0]
        if pick is None:
            continue
        i, rc = pick
        used.add(i)
        # The corp-actions extractor's own reader (thousands commas
        # only): '0,125 NEW = 1 OLD' is a decimal comma it refuses —
        # the hint used to drop the comma and show ratio 125 (audit
        # A2-1096). An unreadable ratio is left out of the hint.
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.corp_actions import rbc_ratio_parts
        try:
            _parts = rbc_ratio_parts(rem.description or '')
        except (BrokerageParseError, ValueError):
            _parts = None
        _rn, _ro = _parts if _parts else (0.0, 0.0)
        ratio = _rn / _ro if _ro else 0.0
        oldm = _OLDCO_RE.search(rem.description or '')
        rcm = _RECVCO_RE.search(rc.description or '')
        links.append(MergerLink(
            date=rem.date, account=rem.account,
            old_symbol=rem.symbol, old_qty=abs(float(rem.quantity or 0.0)),
            old_company=(oldm.group(1).strip() if oldm else ''),
            new_symbol=rc.symbol, new_qty=float(rc.quantity or 0.0),
            new_company=(rcm.group(1).strip() if rcm else ''),
            ratio=ratio))
    links.sort(key=lambda l: (l.date, l.old_symbol))
    return links


def report_missing_history_log(logs: List[List[Dict[str, Any]]],
                               accounts: Set[str],
                               file_name: str = MISSING_HISTORY_FILE
                               ) -> None:
    """One stderr line per missing-history file entry that did nothing in these
    books (whose account they belong to): a spelling mismatch (no rows),
    or a stale entry whose rows never go short — the note used to live
    only in the gains JSON, so a typo silently booked the sale as short
    (audit S076-05). Entries of accounts not in these books are another
    stage's business and stay quiet."""
    by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for log in logs:
        for e in log or []:
            by_pair.setdefault((e.get('symbol', ''), e.get('account', '')),
                               []).append(e)
    complete: List[Tuple[str, str]] = []
    for (symbol, account), es in sorted(by_pair.items()):
        if account not in accounts or any(e.get('inserted') for e in es):
            continue
        notes = [str(e.get('note') or '') for e in es]
        if any(n.startswith('no opening needed') for n in notes):
            complete.append((symbol, account))
        elif notes and all(n.startswith('no rows') for n in notes):
            emit_line(f"warning: {file_name} lists {symbol} / {account}, but "
                      f"no row in the data has that symbol and account — "
                      f"nothing was applied. Check the spelling.",
                      file=sys.stderr)
    if complete:
        # ATTENTION (the run echoes it): the usual cause is the fix
        # itself — the purchase was added (an older export, a .tt line)
        # and the entry is now stale (new-user study). One line.
        emit_line("warning: ATTENTION: " + complete_entry_message(
            *complete[0], file_name, more=complete[1:]), file=sys.stderr)


def format_suggestions(candidates: List[MissingHistoryCandidate],
                       quantities: Optional[Dict[Tuple[str, str], float]]
                       = None, sized_through: Optional[str] = None) -> str:
    """Write the candidate JSON to a string. Underscore-prefixed fields are
    notes for human review; the loader ignores them. `quantities`: the
    units each entry's opening fills, as the run sizes it
    (synthesize_openings: the rows through `sized_through`, the tax
    year's end), recorded as `quantity` with `_sized_through`."""
    entries = []
    for c in candidates:
        note = (
            "Registered account — short positions prohibited; almost "
            "certainly a purchase missing from your files"
            if c.registered else
            "Margin/cash account — could be a real short or a purchase "
            "missing from your files"
        )
        if c.broker_says_closing:
            note = ("The broker codes the sale CLOSING (IB code C): it "
                    "sold a position bought before the data — missing "
                    "history, not a short or a written option"
                    + (f" (IB Basis {c.broker_basis})"
                       if c.broker_basis else ""))
        _q = (quantities or {}).get((c.symbol, c.account))
        entries.append({
            "symbol": c.symbol,
            "account": c.account,
            **({QUANTITY_KEY: round(_q, 10)} if _q else {}),
            **({"_sized_through": sized_through}
               if _q and sized_through else {}),
            "_note": note,
            "_first_negative": c.first_negative_date,
            # Full precision (audit S074-22: a 3e-05 BTC short read
            # -0.0); 10 dp only trims float noise.
            "_peak_short": round(c.peak_short, 10),
            "_end_position": round(c.end_position, 10),
            "_disposition_count": c.disposition_count,
        })
    return json.dumps(entries, indent=2) + "\n"


def load_missing_history(path: Path) -> MissingHistoryPairs:
    """Load a missing-history file (missing_history.json, or the legacy
    phantoms.json). Returns a set of (symbol, account) pairs that knows
    the file's name (MissingHistoryPairs.source_name).
    Underscore-prefixed metadata fields are ignored. A leading BOM
    (an editor's UTF-8 save) is dropped, as for every other user-edited
    file (re-audit A2-1453)."""
    with open(path, 'r', encoding='utf-8-sig') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            # Name the file: it is hand-edited, and the bare decoder
            # message gave no hint which input was bad (audit S076-01).
            raise json.JSONDecodeError(f"{path}: {e.msg}", e.doc,
                                       e.pos) from None
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of missing-history "
                         f"entries")
    out = MissingHistoryPairs()
    out.source_name = Path(path).name or MISSING_HISTORY_FILE
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}[{i}]: expected an object")
        symbol = entry.get('symbol')
        account = entry.get('account')
        if not symbol or not account:
            raise ValueError(
                f"{path}[{i}]: 'symbol' and 'account' are both required"
            )
        # Book symbols are upper-case: a hand-typed 'xyz.to' used to
        # match nothing and read as "data does not go negative" (audit
        # S075-24 / S076-00).
        pair = (str(symbol).strip().upper(), str(account).strip())
        out.add(pair)
        q = entry.get(QUANTITY_KEY)
        if q is not None:
            if isinstance(q, bool) or not isinstance(q, (int, float)) \
                    or not q > 0 or q != q or q == float("inf"):
                raise ValueError(f"{path}[{i}]: {QUANTITY_KEY!r} must be "
                                 f"a positive number (got {q!r})")
            out.quantities[pair] = float(q)
    return out


def window_sized_entries(logs: Iterable[Iterable[Dict[str, Any]]]
                         ) -> List[Dict[str, Any]]:
    """The applied-log entries (synthesize_openings) whose size the tax
    year's end decided: no recorded `quantity`, and the rows after the
    year end go shorter than the rows up to it (sizing over every row
    would have opened more). One per (symbol, account)."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for log in logs:
        for e in log or ():
            if (isinstance(e, dict) and e.get('sized_through')
                    and e.get('recorded_quantity') is None
                    and float(e.get('opening_all_rows') or 0.0)
                    > float(e.get('opening_qty') or 0.0) + 1e-6):
                out.setdefault((e.get('symbol', ''), e.get('account', '')),
                               e)
    return [out[k] for k in sorted(out)]


def short_again_message(entry: Dict[str, Any],
                        file_name: str = MISSING_HISTORY_FILE) -> str:
    """One ATTENTION line for a listed position that goes short again
    once its opening is used up (synthesize_openings `short_again`)."""
    sym, acct = entry.get('symbol'), entry.get('account')
    sa = entry['short_again']
    q = float(entry.get('opening_qty') or 0.0)
    until = entry.get('sized_through')
    head = (f"{file_name} lists {sym} / {acct}: the position goes short "
            f"again on {sa['date']} ({sa['qty']:g} units)")
    if entry.get('recorded_quantity') is not None:
        return (f"{head}, after the {q:g} units its `quantity` records are "
                f"used up — those sales have no purchase and no opening. "
                f"Raise `quantity` to the units held before the data, or "
                f"add the missing purchase.")
    if until and sa['date'] > until:
        opened = (f"its opening ({q:g} units, sized from the rows through "
                  f"{until}) is used up" if q > 0 else
                  f"no opening (the rows through {until} never go short)")
        return (f"{head}, after {opened}: a short or a gap of a later "
                f"year, which does not change {until[:4]}. That year's "
                f"project sizes its own opening; if it is a real short, "
                f"nothing to do.")
    return (f"{head}, after its opening of {q:g} units is used up — "
            f"those sales have no purchase and no opening.")


# The replacement rule a manual warning names, and the date its ±30-day
# window runs on: the SAME basis the country's engine uses (partition
# ENGINE-04/05, INPUTS-11) — Canada's s.54 window on settlement dates,
# the US §1091 window on trade dates.
LOSS_RULE = {"canada": ("the superficial-loss rule (ITA s.54)", "settle"),
             "usa": ("the wash-sale rule (§1091)", "trade")}
LOSS_CHECK_LABEL = {"canada": "superficial-loss check (manual)",
                    "usa": "wash-sale check (manual)"}


def loss_window_date(row: Dict[str, Any], country: str) -> str:
    """The date a row's ±30-day loss window is measured on for
    `country` (settle date in Canada, trade date in the US)."""
    from taxjson.lib.country import canonical_country
    basis = LOSS_RULE[canonical_country(country)][1]
    if basis == "settle":
        return str(row.get('date_settle') or row.get('date') or '')
    return str(row.get('date') or '')


def detect_superficial_loss_warnings(
    clean_losses: List[Dict[str, Any]],
    all_tainted: List[Dict[str, Any]],
    *,
    country: str,
    window_days: int = 30,
) -> List[Dict[str, Any]]:
    """Find clean losses with tainted dispositions on the same symbol
    within ±window_days, measured on the country's own window dates
    (``loss_window_date``). The tainted leg has unknown cost so the
    country's replacement rule (ITA s.54 in Canada, §1091 in the US)
    can't be applied automatically. Each warning identifies the affected
    clean loss and the tainted disposition(s) within the window so
    the user can resolve it manually.

    `clean_losses` should already be filtered to losses (gain < 0) in
    the tax year of interest. `all_tainted` should include tainted
    dispositions across ALL years — a Dec 28 tainted disposition can
    affect a Jan 5 in-year loss the next year, and vice versa.
    """
    from datetime import datetime as _dt
    from taxjson.lib.country import canonical_country
    out: List[Dict[str, Any]] = []
    if not clean_losses or not all_tainted:
        return out
    # Index tainted by symbol for O(1) lookup per loss.
    tainted_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for t in all_tainted:
        tainted_by_symbol.setdefault(t.get('symbol', ''), []).append(t)

    for loss in clean_losses:
        sym = loss.get('symbol', '')
        candidates = tainted_by_symbol.get(sym)
        if not candidates:
            continue
        try:
            loss_dt = _dt.strptime(loss_window_date(loss, country),
                                   '%Y-%m-%d')
        except ValueError:
            continue
        nearby: List[Dict[str, Any]] = []
        for t in candidates:
            try:
                t_dt = _dt.strptime(loss_window_date(t, country),
                                    '%Y-%m-%d')
            except ValueError:
                continue
            if abs((t_dt - loss_dt).days) <= window_days:
                nearby.append({
                    'date': t.get('date'),
                    'qty': t.get('qty'),
                    'proceeds': t.get('proceeds'),
                    'account': t.get('account'),
                    'days_offset': (t_dt - loss_dt).days,
                })
        if nearby:
            out.append({
                'loss_date': loss.get('date'),
                'symbol': sym,
                'account': loss.get('account'),
                'loss_amount': abs(loss.get('gain', 0.0)),
                'tainted_dispositions': nearby,
                'message': (
                    f"Clean loss of {abs(loss.get('gain', 0.0)):.2f} on {loss.get('symbol')} "
                    f"({loss.get('date')}) has {len(nearby)} unknown-cost disposition(s) within "
                    f"±{window_days} days ({LOSS_RULE[canonical_country(country)][1]} "
                    f"dates). {LOSS_RULE[canonical_country(country)][0][0].upper()}"
                    f"{LOSS_RULE[canonical_country(country)][0][1:]} may apply; "
                    f"verify manually."
                ),
            })
    return out


def _short_again(entry: Dict[str, Any],
                 path: List[Tuple[str, float, float]],
                 opening_qty: float) -> None:
    """Mark `entry` with the first row where the position, its opening
    of `opening_qty` (opening-date units) included, goes short again:
    {'date', 'qty'} in that row's units."""
    for d, run, factor in path:
        if run + opening_qty < -1e-6 * max(1.0, opening_qty):
            entry['short_again'] = {
                'date': d, 'qty': round(abs(run + opening_qty) * factor,
                                        10)}
            return


def synthesize_openings(
    transactions: List[TaxTransaction],
    pairs: Optional[Set[Tuple[str, str]]] = None,
    *, warn: bool = False,
    flag_stale: bool = True,
    phantoms: Optional[Set[Tuple[str, str]]] = None,
    until: Optional[str] = None,
) -> Tuple[List[TaxTransaction], List[Dict[str, Any]]]:
    """For each (symbol, account) in pairs, compute the minimum running
    position over the rows dated up to `until` (the tax year's end,
    'YYYY-12-31'; default sizing_until(): the project's year, or every
    row outside a project) and prepend an OPENING_BALANCE transaction
    with quantity = abs(min) — or exactly the entry's recorded
    `quantity` (pairs.quantities), raising or lowering it. Rows after
    the year end never size it: with exports shared by every year, a
    later year's short or gap would otherwise grow an earlier year's
    opening (tax-logic CA-ACB-11 / US-BASIS-04). Returns (new_tx_list,
    applied) where `applied` is a per-entry log of what was inserted (or
    skipped, when the data didn't actually need an opening balance —
    useful for surfacing mis-classified entries the user can prune).

    A listed position that goes short again once its opening is used up
    (a later year's short, a recorded quantity below the shortage) is
    logged as `short_again` and, with `flag_stale`, said as an ATTENTION
    line (short_again_message).

    No-op for pairs that don't go negative in the data: the log entry
    notes this so the user knows the missing-history entry was redundant.

    An applied entry that today's detection would not propose — a
    broker-marked real short, or an option / future the broker never
    coded CLOSING (stale_missing_history_entries) — is still applied (it is the
    user's explicit record), but its log entry carries `stale` and,
    with `flag_stale` (the default), an ATTENTION line goes to stderr:
    this is the one applier every caller shares (run's gains stages,
    t1135, wash-radar, option-boundary, apply-distributions), so each
    of them says so (audit A2-0308 / A2-0310 / A2-0637, R1-M006).

    `phantoms=` is the old keyword for `pairs` (kept for callers that
    name it).
    """
    if pairs is None:
        pairs = phantoms
    if not pairs:
        return list(transactions), []
    label = _source_name(pairs)
    pairs_in = pairs
    if until is None:
        until = sizing_until()

    # Compute min running position per LISTED pair — same walk as
    # detect_missing_history but restricted to listed pairs (expanded to their
    # rename chains), and tracking the earliest activity so the synthetic
    # opening lands before any real transaction touches the pool.
    sorted_txs = _drop_duplicate_splits(
        sorted(transactions,
               key=lambda t: event_sort_key(t, profile='missing_history_walk')))

    # --- Rename-chain expansion. detect_missing_history migrates the running
    # balance across SPLIT-renames and reports candidates under the NEW
    # ticker, so missing_history.json lists (NEW, account) — but the deficit's
    # history (and the correct anchor for the opening) may live on the OLD
    # ticker. Walking only the listed symbol skipped the pre-rename rows
    # entirely, oversizing the opening (taint never cleared). Expand each
    # listed pair to cover every ancestor in its per-account rename chain,
    # and anchor the opening on the CHAIN's earliest symbol/date so the
    # engine replays it through the rename.
    reverse_renames: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for tx in sorted_txs:
        if tx.action == 'SPLIT':
            new_sym = normalize_symbol_new(tx.symbol,
                                           getattr(tx, 'symbol_new', ''))
            if new_sym:
                reverse_renames.setdefault((new_sym, tx.account), []).append(
                    (tx.symbol, tx.account))

    # Each listed pair's chain: itself plus every ancestor symbol. When
    # a listed pair is an ANCESTOR of another listed pair (both ends of a
    # rename chain listed — which --suggest-missing-history itself emits), the
    # chain belongs to the most-downstream listed pair: one opening,
    # sized on the whole chain. Iterating the set let PYTHONHASHSEED pick
    # the owner, and the wrong one sized TWO openings (audit S021-04).
    chains: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}
    for pair in sorted(pairs):
        stack = [pair]
        seen_members: Set[Tuple[str, str]] = set()
        while stack:
            m = stack.pop()
            if m in seen_members:
                continue                       # cycle guard
            seen_members.add(m)
            stack.extend(reverse_renames.get(m, []))
        chains[pair] = seen_members
    folded_into: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for pair in sorted(pairs):
        owners = [q for q in sorted(pairs)
                  if q != pair and pair in chains[q]]
        if owners:
            # The downstream-most owner: the one no other owner contains.
            folded_into[pair] = next(
                (q for q in owners
                 if not any(q in chains[o] for o in owners if o != q)),
                owners[0])
    member_to_pair: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for pair in sorted(pairs):
        if pair in folded_into:
            continue
        for m in sorted(chains[pair]):
            member_to_pair.setdefault(m, pair)
    pairs = {p for p in pairs if p not in folded_into}
    seen_rows: Set[Tuple[str, str]] = set()

    # Per LISTED pair state. The deficit is tracked in OPENING-DATE units:
    # the synthetic OPENING_BALANCE is inserted before everything else and
    # gets re-multiplied by every SPLIT (and rename ratio) on engine replay,
    # so its quantity must be denominated in pre-split units. In opening-
    # date units a split is a pure unit change — it only updates the factor
    # and can never deepen or shrink the deficit by itself. (Mixed-unit
    # accounting made a forward split size the opening 2x too big: the pool
    # never drained, the taint never cleared, and later dispositions were
    # silently dropped from gains.)
    # min_running: the deepest shortage of the rows dated up to `until`
    # (the tax year's end) — what the opening fills. min_all: over every
    # row (what it would be without the year end; said when it differs,
    # window_sized_entries). path: each row's running position, to find
    # where the position goes short again once the opening is used up.
    min_running: Dict[Tuple[str, str], float] = {p: 0.0 for p in pairs}
    min_all: Dict[Tuple[str, str], float] = {p: 0.0 for p in pairs}
    running: Dict[Tuple[str, str], float] = {p: 0.0 for p in pairs}
    path: Dict[Tuple[str, str], List[Tuple[str, float, float]]] = {
        p: [] for p in pairs}
    split_factor: Dict[Tuple[str, str], float] = {p: 1.0 for p in pairs}
    # pair -> (anchor_symbol, anchor_date): first activity anywhere in the
    # pair's chain. The opening must carry the chain's EARLIEST symbol.
    anchor: Dict[Tuple[str, str], Tuple[str, str]] = {}
    currency: Dict[Tuple[str, str], str] = {}

    for tx in sorted_txs:
        key = (tx.symbol, tx.account)
        pair = member_to_pair.get(key)
        if pair is None:
            continue
        seen_rows.add(pair)
        # TRANSFER moves engine position too (detect_missing_history counts it, and
        # the sheltered CLI path rewrites remaining TRANSFERs to BUYSELL
        # before the engine) — excluding it sized openings wrong exactly on
        # registered accounts, the tool's primary use case.
        if tx.action not in ('BUYSELL', 'ASSIGN', 'TRANSFER', 'SPLIT'):
            continue
        if pair not in anchor:
            anchor[pair] = (tx.symbol, tx.date)
        if tx.action == 'SPLIT':
            # Both plain splits and rename ratios re-denominate the chain.
            ratio = tx.quantity
            if ratio and abs(ratio) > 1e-12:
                split_factor[pair] *= ratio
            continue
        running[pair] += tx.quantity / split_factor[pair]
        if running[pair] < min_all[pair]:
            min_all[pair] = running[pair]
        if (until is None or str(tx.date)[:10] <= until) \
                and running[pair] < min_running[pair]:
            min_running[pair] = running[pair]
        path[pair].append((str(tx.date)[:10], running[pair],
                           split_factor[pair]))
        if tx.currency and pair not in currency:
            currency[pair] = tx.currency

    out = list(transactions)
    applied: List[Dict[str, Any]] = []
    for (symbol, account), owner in sorted(folded_into.items()):
        applied.append({'symbol': symbol, 'account': account,
                        'opening_qty': 0.0, 'inserted': False,
                        'note': f'same rename chain as {owner[0]} — its '
                                f'one opening covers this pair'})
    for (symbol, account), min_pos in sorted(min_running.items()):
        entry: Dict[str, Any] = {
            'symbol': symbol,
            'account': account,
            'opening_qty': 0.0,
            'inserted': False,
        }
        if (symbol, account) not in seen_rows:
            # Nothing in the data carries this (symbol, account): a
            # spelling or account-name mismatch, not "complete data"
            # (audit S075-24 / S076-00).
            entry['note'] = ('no rows for this symbol/account in the data — '
                             f'check the spelling in {label}')
            # Quiet by default: every account's stage is handed the
            # whole project file, so another account's entry has no rows
            # here by design. pipeline.prepare_books reports the
            # project-level result once (report_missing_history_log).
            if warn:
                emit_line(f"warning: {label} lists {symbol} / {account}, "
                          f"but no row in the data has that symbol and "
                          f"account — nothing was applied. Check the "
                          f"spelling.", file=sys.stderr)
            applied.append(entry)
            continue
        _rec = (getattr(pairs_in, "quantities", None) or {}).get(
            (symbol, account))
        if _rec is None and min_pos >= -1e-6:
            if min_all[(symbol, account)] < -1e-6:
                # Short only after the year end: no opening for this
                # year (a later year's project sizes its own).
                entry['sized_through'] = until
                entry['opening_all_rows'] = abs(min_all[(symbol, account)])
                entry['note'] = (f'not short through {until} — no opening '
                                 f'for this year')
                _short_again(entry, path[(symbol, account)], 0.0)
                applied.append(entry)
                continue
            # Listed in the file but the data is actually complete.
            # Surface this so the user can prune the file.
            entry['note'] = 'no opening needed — data does not go negative for this pair'
            applied.append(entry)
            continue

        opening_qty = abs(min(min_pos, 0.0))
        entry['sized_through'] = until
        entry['opening_all_rows'] = abs(min(min_all[(symbol, account)], 0.0))
        if _rec is not None:
            # The quantity the entry records IS the opening: the units
            # held before the data, as the user states them (raising or
            # lowering what the rows show).
            opening_qty = _rec
            entry['recorded_quantity'] = _rec
        # Anchor on the CHAIN's earliest symbol and ONE DAY BEFORE its first
        # activity. The earliest symbol lets the engine replay the opening
        # through any rename. The opening anchors ON that first-activity
        # date: every sort that sees an OPENING_BALANCE now has an explicit
        # OB-first rung (engine profiles' priority/phase ladders, the
        # missing-history walks' WalkPriority — all via event_sort_key), so a
        # same-date 00:00:00 SPLIT can no longer sort ahead of the
        # pre-split-sized opening. (The old workaround fabricated a date
        # one day earlier to win bare (date, time) sorts that no longer
        # exist.) Fallback '1970-01-01' is defensive: the anchor is always
        # populated for any pair that produced a negative position.
        anchor_symbol, anchor_date = anchor.get((symbol, account),
                                                (symbol, '1970-01-01'))
        out.append(TaxTransaction(
            action='OPENING_BALANCE',
            date=anchor_date,
            time='00:00:00',
            symbol=anchor_symbol,
            quantity=opening_qty,
            currency=currency.get((symbol, account), ''),
            price=0.0,
            net_amount=0.0,
            account=account,
            description=(f'MISSING-HISTORY opening — bought before the data, '
                         f'cost unknown (qty={opening_qty})'),
        ))
        entry['opening_qty'] = opening_qty
        entry['inserted'] = True
        entry['anchor_date'] = anchor_date
        entry['anchor_symbol'] = anchor_symbol
        _short_again(entry, path[(symbol, account)], opening_qty)
        applied.append(entry)

    if flag_stale:
        for entry in applied:
            if entry.get('short_again'):
                emit_line("warning: ATTENTION: "
                          + short_again_message(entry, label),
                          file=sys.stderr)

    inserted = {(e['symbol'], e['account']) for e in applied
                if e.get('inserted')}
    if inserted:
        stale = stale_missing_history_entries(
            [t for t in sorted_txs if (t.symbol, t.account) in member_to_pair],
            inserted, file_name=label)
        by_pair = {(e.symbol.upper(), e.account): e for e in stale}
        for entry in applied:
            st = by_pair.get((entry['symbol'], entry['account']))
            if st is None or not entry.get('inserted'):
                continue
            entry['stale'] = st.reason
            if flag_stale:
                emit_line(f"warning: ATTENTION: {stale_entry_message(st)}",
                          file=sys.stderr)
    return out, applied


# --------------------------------------------------------------------
# Purchase drafts (`find-missing-history --write-purchases`).
#
# A broker sometimes states the cost of what a sale with no purchase in
# the files closed: IB's Trades `Basis` on a sale coded C (closing), with
# the lots it closed when the statement lists Closed Lots; the "TRANSFER
# BOOK VALUE" a Questrade or RBC transfer-in states. That figure is
# EVIDENCE: the run never books it. These helpers draft `.tt` purchase
# lines from it into a file the run does not read (DRAFT_NAME, suffix
# .tt.txt); the user reviews each line, fills in what the broker does not
# say, and renames the file to .tt — the kept line is then the user's
# assertion (tax-logic CA-ACB-15 / US-BASIS-08).

# The draft file: `.txt` is not an input suffix (`taxjson run` reads
# only *.csv and *.tt directly in inputs/<account>/), so an unreviewed
# draft can never be booked; renaming it to `<name>.tt` makes it input.
DRAFT_NAME = "purchases_draft.tt.txt"
# Placeholders the .tt reader refuses ("not a valid YYYY-MM-DD date";
# a non-number total): a line still holding one stops the run, so a
# file renamed before it was edited cannot book a guess.
DATE_PLACEHOLDER = "YYYY-MM-DD"
COST_PLACEHOLDER = "COST"
_DRAFT_TIME = "09:30:00"
# A transfer-in row that states the delivering dealer's book value of
# what was delivered: Questrade's "... TRANSFER BOOK VALUE <amount>"
# (lib/brokerages/questrade.py reads it as the row's net amount), RBC's
# "... ACCOUNT TRANSFER BOOK VALUE <amount> FROM ACCOUNT ...".
_TRANSFER_BOOK_VALUE_RE = re.compile(
    r'TRANSFER\s+BOOK\s+VALUE\s+([\d,]+(?:\.\d+)?)', re.IGNORECASE)
# A move between two of the user's own accounts posts its two legs a few
# days apart at most (taxjson_run._OWN_MOVE_DAYS).
_OWN_MOVE_DAYS = 10
# The `type` of a books row that acquires transferred-in shares at the
# broker's stated book value on their arrival date: such a transfer is
# already costed by the run, so it is not drafted again.
from taxjson.lib.core import TRANSFER_BOOK_VALUE_TYPE  # noqa: E402


@dataclass
class PurchaseDraft:
    """One drafted `.tt` BUYSELL purchase. `date` None = the
    DATE_PLACEHOLDER (the broker does not say when it was bought);
    `cost` None = the COST_PLACEHOLDER (the broker's figure covers more
    than the missing units)."""
    account: str
    symbol: str
    quantity: float
    currency: str
    cost: Optional[float]
    date: Optional[str]
    multiplier: float = 1.0
    source: str = ''          # 'ib-lot' | 'ib-basis' | 'transfer'
    sale_date: str = ''       # the sale it backs ('' for a transfer-in)
    comments: Tuple[str, ...] = ()
    warn: bool = False        # a CHECK the user must not skip

    def tt_line(self) -> str:
        """The BUYSELL line (fee 0: a broker's basis already holds the
        commission)."""
        qty = f"{self.quantity:.10g}"
        if self.cost is None:
            price = total = COST_PLACEHOLDER
        else:
            unit = self.quantity * (self.multiplier or 1.0)
            price = f"{(self.cost / unit if unit else 0.0):.6f}"
            total = f"{self.cost:.2f}"
        size = ''
        if is_option_symbol(self.symbol) and self.multiplier \
                and abs(self.multiplier - 100.0) > 1e-9:
            size = f"  x{self.multiplier:g}"
        return (f"BUYSELL  {self.date or DATE_PLACEHOLDER}  {_DRAFT_TIME}  "
                f"{self.symbol}  {qty}  {self.currency}  {price}  {total}  "
                f"0{size}")


@dataclass
class DraftGap:
    """A sale with no purchase in the files (or a transfer-in) that no
    draft line was written for, and why."""
    account: str
    symbol: str
    date: str
    quantity: float
    reason: str


def _mask_id(row_id: Any) -> str:
    """A row id shortened the way every id is shown: 2 chars + ***."""
    s = str(row_id or '')
    return f"{s[:2]}***" if s else '?'


def parse_broker_basis(text: str) -> Optional[Tuple[float, str]]:
    """'1,234.56 USD' -> (1234.56, 'USD'); None when it does not read."""
    m = re.match(r'^\s*([\d,]+(?:\.\d+)?)\s+([A-Za-z]{3})\s*$',
                 str(text or ''))
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', '')), m.group(2).upper()
    except ValueError:
        return None


def parse_broker_lots(text: str) -> Optional[List[Tuple[str, float, float]]]:
    """broker_lots evidence -> [(open date, qty, cost)]; None when there
    is none or a lot did not read (the export's lots are then not used)."""
    text = str(text or '').strip()
    if not text or text == 'invalid':
        return None
    out = []
    for part in text.split(';'):
        bits = part.split()
        if len(bits) != 3:
            return None
        try:
            d = date.fromisoformat(bits[0]).isoformat()
            q, c = float(bits[1]), float(bits[2])
        except ValueError:
            return None
        if q <= 0 or c < 0:
            return None
        out.append((d, q, c))
    return out or None


def _drafting_caveats(country: str, currency: str, *, lots: bool,
                      is_transfer: bool = False) -> List[str]:
    """The per-line notes that differ by country (CA-ACB-15 /
    US-BASIS-08)."""
    from taxjson.lib.country import home_currency, is_canada
    out: List[str] = []
    if is_canada(country):
        if is_transfer:
            out.append("check: the delivering broker's book value is its "
                       "average cost, which is your ACB only if this was "
                       "your only position in it (s.47 averages every "
                       "identical share in all your taxable accounts).")
        else:
            out.append("check: IB's Basis is the cost of the lots IB "
                       "closed (FIFO), not your ACB: the ACB averages "
                       "every identical share in all your taxable "
                       "accounts (s.47). If you held more of it bought "
                       "before the data (still held, or at another "
                       "broker), add those purchases too.")
        if currency and currency != home_currency(country):
            out.append(f"check: the run converts this {currency} cost "
                       f"to CAD at the Bank of Canada rate of the line's "
                       f"date, so the date must be the real purchase "
                       f"date.")
        out.append("check: a purchase within 30 days (settlement dates) "
                   "of a loss sale of the same stock is a replacement "
                   "for the superficial-loss rule.")
    else:
        if lots:
            out.append("check: IB's lot date and cost are this lot's basis "
                       "and the start of its holding period; if IB "
                       "adjusted the lot for a wash sale, enter the "
                       "original cost (taxjson applies §1091 itself).")
        elif is_transfer:
            out.append("check: the book value may sum several lots: write "
                       "one line per lot with its own purchase date and "
                       "cost — the date decides short- or long-term.")
        else:
            out.append("check: IB's Basis may sum several lots (FIFO): "
                       "write one line per lot with its own purchase date "
                       "and cost — the date decides short- or long-term.")
    return out


def _splits_after(events: List[Tuple[str, float, str]],
                  when: Optional[str]) -> Tuple[float, str]:
    """(unit factor, symbol in force) at `when` (None: before the data)
    from a pair's split/rename history [(date, ratio, old symbol or '')],
    walking back from the sale: every split or rename dated after
    `when` re-denominates the units and renames back."""
    factor = 1.0
    symbol = ''
    for d, ratio, old in reversed(events):
        if when is not None and d <= when:
            break
        if ratio and abs(ratio) > 1e-12:
            factor *= ratio
        if old:
            symbol = old
    return factor, symbol


def draft_purchases(
    transactions: Iterable[TaxTransaction],
    *,
    country: str,
    year: Any = None,
    date_basis: str = 'settle',
    transfer_rows: Iterable[Dict[str, Any]] = (),
    registered_accounts=None,
    journal_symbols: Optional[Set[str]] = None,
    listed_pairs: Optional[Set[Tuple[str, str]]] = None,
    rename_sources: Optional[Dict[str, List[str]]] = None,
    accounts: Optional[Set[str]] = None,
    symbol_key=None,
) -> Tuple[List[PurchaseDraft], List[DraftGap]]:
    """Draft `.tt` purchase lines from the broker's own cost evidence.

    1. An IB sale coded C (closing) that sells units the data never
       bought, with IB's Basis: one line per lot when IB lists the lots
       it closed (ClosedLot rows: open date, quantity, cost); else one
       line with the purchase date left as DATE_PLACEHOLDER. When the
       data held some of the units sold, IB's Basis covers those too:
       the missing units get IB's lots dated before the account's first
       row (when they add up), else a COST_PLACEHOLDER with the
       arithmetic in the comment.
    2. A transfer-in that states the delivering broker's book value
       (Questrade's or RBC's "TRANSFER BOOK VALUE"; `transfer_rows` are the
       transfer sidecars' rows, the run leaves them out of a taxable
       account's books): one line with the date placeholder — the
       transfer date is not the purchase date.

    A pair is drafted whole (every one of its uncovered sales) when any
    of its sales falls in `year` (on `date_basis`), or always when
    `year` is None: a draft for one sale of a pair would otherwise be
    consumed by an earlier one. Sheltered accounts are not drafted (no
    gain is computed there). Quantities are in the units in force on
    the purchase date (a split in the data after it is undone). Returns
    (drafts, gaps); gaps are the sales and transfer-ins left undrafted,
    with the reason. `accounts`: draft only these (every account's rows
    still count for the Canadian cross-account check). `symbol_key`: a
    transfer row's symbol as the books spell it (ticker.map's renames —
    the sidecars keep the broker's spelling, which the draft line
    uses); identity by default."""
    from taxjson.lib.country import canonical_country, is_canada
    country = canonical_country(country)
    year_str = str(year) if year is not None else None
    listed = {(str(s).upper(), str(a)) for s, a in (listed_pairs or ())}
    txs = list(transactions)
    first_day: Dict[str, str] = {}
    for t in txs:
        d = str(t.date or '')[:10]
        if d and (t.account not in first_day or d < first_day[t.account]):
            first_day[t.account] = d
    journal_symbols = _book_journals(txs, journal_symbols)
    sorted_txs = _drop_duplicate_splits(sorted(
        txs, key=lambda t: (_walk_key(t, journal_symbols),
                            0 if float(t.quantity or 0) > 0 else 1)))

    def _new():
        return {'qty': 0.0, 'cost': 0.0, 'events': [], 'sales': [],
                'in_year': False, 'tt_qty': 0.0, 'low': 0.0,
                'bought': False}
    state: Dict[Tuple[str, str], Dict[str, Any]] = {}
    orders = OrderStarts()
    for tx in sorted_txs:
        key = (tx.symbol, tx.account)
        if tx.action == 'SPLIT':
            ratio = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol,
                                           getattr(tx, 'symbol_new', ''))
            s = state.setdefault(key, _new())
            if new_sym:
                t = state.setdefault((new_sym, tx.account), _new())
                t['qty'] += s['qty'] * ratio
                t['cost'] += s['cost']
                t['events'] = (s['events'] + [(str(tx.date)[:10], ratio,
                                               tx.symbol)])
                t['low'] = min(t['low'], s['low'] * ratio)
                t['bought'] = t['bought'] or s['bought']
                s['qty'] = s['cost'] = 0.0
            else:
                s['qty'] *= ratio
                s['events'].append((str(tx.date)[:10], ratio, ''))
            continue
        if tx.action not in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                             'TRANSFER'):
            continue
        s = state.setdefault(key, _new())
        prev = s['qty']
        q = float(tx.quantity or 0.0)
        order_prev = orders.prev(key, tx, prev)
        if q > 0:
            if str(getattr(tx, 'type', '') or '') == \
                    TRANSFER_BOOK_VALUE_TYPE:
                s['booked_bv'] = s.get('booked_bv', 0.0) + q
            s['qty'] = prev + q
            s['bought'] = True
            if prev >= 0:
                s['cost'] += abs(float(tx.net_amount or 0.0))
            else:
                s['cost'] = (abs(float(tx.net_amount or 0.0))
                             * max(0.0, prev + q) / q)
            if (tx.action == 'BUYSELL'
                    and str(getattr(tx, 'source', '') or '')
                    .lower().endswith('.tt')):
                s['tt_qty'] += q
            continue
        if q >= 0:
            continue
        held = max(prev, 0.0)
        held_cost = s['cost'] if held > 1e-12 else 0.0
        s['qty'] = prev + q
        s['low'] = min(s['low'], s['qty'])
        if prev > 1e-12:
            s['cost'] = (s['cost'] * max(0.0, prev + q) / prev
                         if prev + q > 1e-12 else 0.0)
        unbacked = abs(q) - held
        if unbacked <= 1e-9:
            continue
        closing = unbacked_close(tx, prev, order_prev)
        if not closing and (broker_short_marker(tx)
                            or _derivative_symbol(tx.symbol or '')):
            # A short the broker declares (RBC SHORT., IB code O) or an
            # option / future sold to open: a real position, not a
            # missing purchase (R1-8, S013-00).
            continue
        if year_str is None or _basis_date(tx, date_basis).startswith(
                year_str):
            s['in_year'] = True
        s['sales'].append({
            'tx': tx, 'unbacked': unbacked, 'held': held,
            'held_cost': held_cost, 'events': list(s['events']),
        })

    drafts: List[PurchaseDraft] = []
    gaps: List[DraftGap] = []
    # Canada pools identical shares across taxable accounts (s.47): the
    # other taxable accounts that trade each symbol.
    traded_in: Dict[str, Set[str]] = {}
    for t in txs:
        if (t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER')
                and not is_registered_account(t.account,
                                              registered_accounts,
                                              country)):
            traded_in.setdefault(t.symbol, set()).add(t.account)
    for (symbol, account), s in sorted(state.items()):
        if not s['sales'] or not s['in_year']:
            continue
        if accounts is not None and account not in accounts:
            continue
        if is_registered_account(account, registered_accounts, country):
            for sale in s['sales']:
                gaps.append(DraftGap(account, symbol,
                                     str(sale['tx'].date), sale['unbacked'],
                                     'sheltered account (no gain is '
                                     'computed there)'))
            continue
        pair_drafts: List[PurchaseDraft] = []
        pair_gaps: List[DraftGap] = []
        for sale in s['sales']:
            d, g = _draft_sale(sale, symbol, account, country,
                               first_day.get(account, ''),
                               rename_sources or {})
            pair_drafts += d
            pair_gaps += g
        if pair_drafts and pair_gaps:
            # The pair stays short until every gap is filled: say so on
            # its drafts.
            short = sum(g.quantity for g in pair_gaps)
            for dr in pair_drafts:
                dr.comments += (f"CHECK: {short:g} more unit(s) of "
                                f"{symbol} were sold with no purchase in "
                                f"your files and no broker cost — their "
                                f"purchase is not drafted; without it the "
                                f"sales still draw on missing history.",)
                dr.warn = True
        others = sorted(traded_in.get(symbol, set()) - {account})
        if pair_drafts and others and is_canada(country):
            for dr in pair_drafts:
                dr.comments += (f"CHECK: you also hold or trade {symbol} "
                                f"in {', '.join(others)}: the ACB pools "
                                f"those shares with these (s.47), so "
                                f"IB's lot cost is not this sale's ACB — "
                                f"those accounts' history must be "
                                f"complete too.",)
                dr.warn = True
        if pair_drafts and (str(symbol).upper(), account) in listed:
            for dr in pair_drafts:
                dr.comments += ("once these lines are in a .tt file, "
                                "remove " + f"{symbol} / {account} from "
                                "missing_history.json.",)
        drafts += pair_drafts
        gaps += pair_gaps

    t_drafts, t_gaps = _draft_transfers(
        [r for r in transfer_rows
         if accounts is None or float(r.get('quantity') or 0) < 0
         or r.get('account') in accounts],
        state, country, registered_accounts, symbol_key or (lambda x: x))
    # A sale whose units a drafted transfer-in delivered is fixed by
    # that line: not listed as undrafted.
    delivered: Dict[Tuple[str, str], float] = {}
    for dr in t_drafts:
        k = ((symbol_key or (lambda x: x))(dr.symbol), dr.account)
        delivered[k] = delivered.get(k, 0.0) + dr.quantity
        if (str(k[0]).upper(), dr.account) in listed:
            dr.comments += ("once this line is in a .tt file, remove "
                            f"{k[0]} / {dr.account} from "
                            "missing_history.json.",)
    kept: List[DraftGap] = []
    for g in gaps:
        left = delivered.get((g.symbol, g.account), 0.0)
        if g.reason.startswith('no broker cost') and left > 1e-9:
            used = min(left, g.quantity)
            delivered[(g.symbol, g.account)] = left - used
            if used >= g.quantity - 1e-9:
                continue
            g.quantity -= used
            g.reason += (" (the rest of this sale is the drafted "
                         "transfer-in's)")
        kept.append(g)
    return drafts + t_drafts, kept + t_gaps


def _draft_sale(sale: Dict[str, Any], symbol: str, account: str,
                country: str, first_day: str,
                rename_sources: Dict[str, List[str]]
                ) -> Tuple[List[PurchaseDraft], List[DraftGap]]:
    """The draft lines (or the gap) for one sale with unbacked units."""
    tx = sale['tx']
    u = sale['unbacked']
    sale_date = str(tx.date or '')[:10]
    gap = DraftGap(account, symbol, sale_date, u, '')
    if (symbol or '').startswith(('F:', '/', '\\')):
        gap.reason = ("a future: IB's Basis on a futures close is not a "
                      "purchase cost")
        return [], [gap]
    basis = parse_broker_basis(getattr(tx, 'broker_basis', '') or '')
    if basis is None:
        gap.reason = ("no broker cost on the sale (only an IB sale coded "
                      "C carries one, in a statement with the Basis "
                      "column)")
        return [], [gap]
    amount, cur = basis
    mult = float(getattr(tx, 'multiplier', 0.0) or 0.0) or (
        100.0 if is_option_symbol(symbol) else 1.0)
    sold = abs(float(tx.quantity or 0.0))
    src = (f"IB sale {sale_date} of {sold:g} {symbol} (code C, row "
           f"{_mask_id(tx.id)}"
           + (f", {tx.source}" if getattr(tx, 'source', '') else '')
           + ")")
    ev = f"IB Basis {amount:,.2f} {cur} for the {sold:g} sold"
    lots = parse_broker_lots(getattr(tx, 'broker_lots', '') or '')
    lot_note = ''
    if lots is not None and abs(sum(q for _, q, _ in lots) - sold) > 1e-6:
        lot_note = ("IB's Closed Lots do not add up to the sale's "
                    "quantity — not used")
        lots = None
    if lots is not None and sale['held'] > 1e-12:
        # Only the lots bought before the data are missing.
        pre = [lt for lt in lots if first_day and lt[0] < first_day]
        if abs(sum(q for _, q, _ in pre) - u) <= 1e-6:
            lots = pre
        else:
            lot_note = ("IB's Closed Lots dated before your data do not "
                        "add up to the units missing — not used")
            lots = None
    # The line goes under the broker's own spelling when ticker.map
    # renames it into the books' symbol (the run maps the .tt line the
    # same way; the holdings hand-off keys on the broker's listing —
    # S049-01): the rename source whose root is IB's raw symbol.
    rename_note = ''
    line_symbol = symbol
    srcs = rename_sources.get(symbol) or []
    raw = str(getattr(tx, 'description', '') or '').strip().upper() \
        .replace(' ', '.')
    mine = [x for x in srcs if x.upper().rsplit('.', 1)[0] == raw]
    if len(mine) == 1:
        line_symbol = mine[0]
        rename_note = (f"{line_symbol} is the broker's symbol: ticker.map "
                       f"maps it to {symbol}, as it maps the sale.")
    elif srcs:
        rename_note = (f"check: ticker.map renames {', '.join(srcs)} to "
                       f"{symbol} — enter the purchase under the "
                       f"broker's symbol and currency, as the account "
                       f"labels it.")
    out: List[PurchaseDraft] = []
    if lots is not None:
        for lot_date, lq, lc in lots:
            factor, old = _splits_after(sale['events'], lot_date)
            comments = [src, f"{ev}; this lot: opened {lot_date}, "
                             f"{lq:g} units, cost {lc:,.2f} {cur} "
                             f"(IB Closed Lots)"]
            if abs(factor - 1.0) > 1e-12 or old:
                comments.append(
                    f"quantity in the units of {lot_date}: a split or "
                    f"rename in your data after it turns {lq / factor:g} "
                    f"{old or symbol} into {lq:g} {symbol}.")
            comments += _drafting_caveats(country, cur, lots=True)
            if rename_note:
                comments.append(rename_note)
            out.append(PurchaseDraft(
                account=account, symbol=old or line_symbol,
                quantity=lq / factor, currency=cur, cost=lc,
                date=lot_date, multiplier=mult, source='ib-lot',
                sale_date=sale_date, comments=tuple(comments)))
        return out, []
    factor, old = _splits_after(sale['events'], None)
    comments = [src, ev + (" (no lot detail in the export)"
                           if not lot_note else f" ({lot_note})")]
    warn = False
    if sale['held'] > 1e-12:
        rest = amount - sale['held_cost']
        comments.append(
            f"CHECK: your data held {sale['held']:g} of the units sold "
            f"(cost {sale['held_cost']:,.2f} in the books' currency) and "
            f"IB's Basis covers all {sold:g}: the {u:g} missing units' "
            f"cost is not known. If this sale closed your whole position "
            f"and the books' currency is {cur}, it is {amount:,.2f} - "
            f"{sale['held_cost']:,.2f} = {rest:,.2f}; else use the "
            f"purchase confirmation.")
        cost = None
        warn = True
    else:
        cost = amount
    comments.append("fill in: the purchase date (the broker does not say "
                    "when these units were bought).")
    if abs(factor - 1.0) > 1e-12 or old:
        comments.append(
            f"quantity in the units before your data: a split or rename "
            f"in your data turns {u / factor:g} {old or symbol} into "
            f"{u:g} {symbol}.")
    comments += _drafting_caveats(country, cur, lots=False)
    if rename_note:
        comments.append(rename_note)
    out.append(PurchaseDraft(
        account=account, symbol=old or line_symbol, quantity=u / factor,
        currency=cur, cost=cost, date=None, multiplier=mult,
        source='ib-basis', sale_date=sale_date, comments=tuple(comments),
        warn=warn))
    return out, []


def _draft_transfers(rows: List[Dict[str, Any]],
                     state: Dict[Tuple[str, str], Dict[str, Any]],
                     country: str, registered_accounts, key
                     ) -> Tuple[List[PurchaseDraft], List[DraftGap]]:
    """Drafts from transfer-ins that state the delivering broker's book
    value (rows of the transfer sidecars)."""
    drafts: List[PurchaseDraft] = []
    gaps: List[DraftGap] = []
    outs = [r for r in rows if float(r.get('quantity') or 0) < 0]
    tt_left: Dict[Tuple[str, str], float] = {
        k: s.get('tt_qty', 0.0) for k, s in state.items()}
    bv_left: Dict[Tuple[str, str], float] = {
        k: s.get('booked_bv', 0.0) for k, s in state.items()}
    for r in sorted(rows, key=lambda r: (str(r.get('date') or ''),
                                         str(r.get('symbol') or ''))):
        q = float(r.get('quantity') or 0)
        if q <= 1e-12 or r.get('action', 'TRANSFER') != 'TRANSFER':
            continue
        m = _TRANSFER_BOOK_VALUE_RE.search(str(r.get('description') or ''))
        if not m:
            continue
        symbol = str(r.get('symbol') or '')
        account = str(r.get('account') or '')
        d = str(r.get('date') or '')[:10]
        gap = DraftGap(account, symbol, d, q, '')
        if is_registered_account(account, registered_accounts, country):
            gap.reason = 'sheltered account (no gain is computed there)'
            gaps.append(gap)
            continue
        own = next((o for o in outs
                    if o.get('account') != account
                    and o.get('symbol') == symbol
                    and abs(abs(float(o.get('quantity') or 0)) - q) < 1e-9
                    and _day_gap(o.get('date') or '', d) <= _OWN_MOVE_DAYS),
                   None)
        if own is not None:
            gap.reason = (f"a move from your account {own.get('account')}"
                          f" — its cost carries over, it is not a "
                          f"purchase")
            gaps.append(gap)
            continue
        book_key = (key(symbol), account)
        if tt_left.get(book_key, 0.0) >= q - 1e-9:
            tt_left[book_key] -= q
            gap.reason = "your .tt purchase lines already cover it"
            gaps.append(gap)
            continue
        if bv_left.get(book_key, 0.0) >= q - 1e-9:
            bv_left[book_key] -= q
            gap.reason = ("the run already books it at the stated book "
                          "value")
            gaps.append(gap)
            continue
        st = state.get(book_key) or {}
        if st.get('bought') and st.get('low', 0.0) >= -1e-9:
            # Your files acquire it and no sale of it goes short: the
            # shares are in the books already (another export, a .tt
            # line under another quantity) — a draft would count them
            # twice. Shares still held but missing show in `sanity`.
            gap.reason = ("your files already acquire it and no sale "
                          "goes short — a line would count it twice "
                          "(`taxjson sanity` shows a position still "
                          "short of the broker's)")
            gaps.append(gap)
            continue
        try:
            amount = float(m.group(1).replace(',', ''))
        except ValueError:
            continue
        cur = str(r.get('currency') or '').upper()
        mult = float(r.get('multiplier') or 0.0) or (
            100.0 if is_option_symbol(symbol) else 1.0)
        comments = [f"transfer-in {d} of {q:g} {symbol} (row "
                    f"{_mask_id(r.get('id'))}"
                    + (f", {r.get('source')}" if r.get('source') else '')
                    + ")",
                    f"the delivering broker's book value "
                    f"{amount:,.2f} {cur}, as printed on the row",
                    "fill in: the ORIGINAL purchase date at the other "
                    "broker (the transfer date is not a purchase date)."]
        warn = False
        if st.get('bought') and -st.get('low', 0.0) < q - 1e-6:
            comments.append(
                f"CHECK: your files already acquire some {symbol} and "
                f"its sales go at most {-st.get('low', 0.0):g} units "
                f"short: part of these {q:g} may be in your files "
                f"already — keep only the units that are not.")
            warn = True
        comments += _drafting_caveats(country, cur, lots=False,
                                      is_transfer=True)
        drafts.append(PurchaseDraft(
            account=account, symbol=symbol, quantity=q, currency=cur,
            cost=amount, date=None, multiplier=mult, source='transfer',
            comments=tuple(comments), warn=warn))
    return drafts, gaps


def format_purchase_drafts(drafts: List[PurchaseDraft],
                           gaps: List[DraftGap], *, country: str,
                           account: str, final_name: str = "purchases.tt"
                           ) -> str:
    """The draft file's text for one account: a header that says how to
    use it, then each line under its `#` notes."""
    from taxjson.lib.country import is_canada
    ca = is_canada(country)
    head = [
        f"# {DRAFT_NAME} — DRAFT purchase lines for account {account},",
        "# written by `taxjson find-missing-history --write-purchases`.",
        "#",
        "# taxjson does NOT read this file (its name ends in .txt). For "
        "each line:",
        "#   1. check it against your records (statements, trade "
        "confirmations);",
        f"#   2. replace every {DATE_PLACEHOLDER} with the real purchase "
        f"date and every {COST_PLACEHOLDER} with the real cost (total = "
        "qty x price + commission; price = total / qty);",
        "#   3. delete any line you cannot vouch for.",
        f"# Then rename the file to {final_name} (any name ending in .tt) "
        "in this folder and run `taxjson run`.",
        f"# A line still holding {DATE_PLACEHOLDER} or {COST_PLACEHOLDER} "
        "is refused by the run.",
        "#",
        "# The broker's figure is evidence; once you keep a line, its "
        "cost is YOUR statement of what you paid "
        + ("(tax-logic CA-ACB-15)." if ca else "(tax-logic US-BASIS-08)."),
    ]
    if ca:
        head += [
            "# Canada: your ACB is the average cost of every identical "
            "share you held in all taxable accounts — a broker's lot cost "
            "or book value is a start, not always your ACB. A non-CAD "
            "line is converted at the Bank of Canada rate of its date.",
        ]
    else:
        head += [
            "# United States: each lot keeps its own basis and purchase "
            "date; the date decides short- or long-term.",
        ]
    body: List[str] = []
    for dr in drafts:
        body.append("")
        for c in dr.comments:
            body.append(f"# {c}")
        body.append(dr.tt_line())
    tail: List[str] = []
    if gaps:
        tail += ["", "# Not drafted (fix these another way — "
                     "docs/getting-started.md, step 5b):"]
        for g in gaps:
            tail.append(f"#   {g.symbol} {g.date} {g.quantity:g} units: "
                        f"{g.reason}")
    return "\n".join(head + body + tail) + "\n"


# The names this module had as lib/phantom_holdings (before 2026-10),
# kept so external code importing them keeps working.
PhantomCandidate = MissingHistoryCandidate
StalePhantomEntry = StaleMissingHistoryEntry
detect_phantoms = detect_missing_history
stale_phantom_entries = stale_missing_history_entries
report_phantom_log = report_missing_history_log
load_phantoms = load_missing_history
