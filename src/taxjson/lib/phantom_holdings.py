"""Phantom-holdings reconciliation.

When a user's data window doesn't reach back to when a position was opened,
the engine sees only the disposition and concludes the user is short. This
module detects those cases and lets the user mark them as incomplete-
history so the engine can:

  - Insert a synthetic OPENING_BALANCE transaction at the data-window start
    to keep position math non-negative.
  - Tag the ACB pool as tainted while phantom shares remain. Dispositions
    drawing from a tainted pool are excluded from the gains report.
  - Re-clean the pool when total quantity hits zero, so post-drain buys
    establish a fresh, fully-known ACB.

This is the gains-side counterpart to the user's old TRANSFER-in approach,
without the unsafe leak of fabricated ACB into the gain calculation.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import (SplitTimeline, event_sort_key,
                                             normalize_symbol_new)


# Registered-account labels, the fallback for an account whose type the
# caller does not know (standalone tools outside a project). Short
# positions are prohibited in these accounts, so a negative balance is
# almost certainly phantom. Every plan name — Canadian and US alike —
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
# any synthesized phantom symbol whose mid-string digits happened to
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
    p = Path(path).resolve()
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
class PhantomCandidate:
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


def journal_targets(ticker_map) -> Set[str]:
    """The symbols ticker.map's JOURNAL lines fold a listing INTO (and
    from): a Norbert's-gambit pair (sell DLR.TO, buy DLR.U.TO the same
    morning) is one symbol in the books. Empty without a readable map
    (a missing map is not an error here: the caller decides)."""
    if not ticker_map:
        return set()
    from taxjson.bin.taxjson_ticker_map import load_map_file
    tm = load_map_file(Path(ticker_map))
    out: Set[str] = set()
    for src, dst in (getattr(tm, 'journal', {}) or {}).items():
        out.add(str(dst).upper())
        out.add(str(src).upper())
    return out


def _walk_key(t, journal_symbols: Optional[Set[str]] = None) -> Tuple:
    """The phantom walks' order. A JOURNAL-folded symbol's trades of one
    day read buys first whatever their clock: RBC stamps a day's rows
    with its row ORDINAL (09:30:00 + k s, newest-first export), so the
    Norbert's-gambit sale of DLR.TO sorted ahead of the same morning's
    DLR.U.TO buy and read as a one-day phantom short — reported as
    missing history, and --gen-phantoms wrote an entry that pulled the
    sale off Schedule 3 (audit A2-0309 / A2-0636). Other symbols keep
    the clock: a same-day sale and rebuy of shares bought before the
    data IS missing history."""
    k = event_sort_key(t, profile='phantom_walk')
    if (journal_symbols and t.action == 'BUYSELL'
            and str(t.symbol or '').upper() in journal_symbols):
        k = (k[0], k[1], '', k[3])
    return k


def detect_phantoms(
    transactions: Iterable[TaxTransaction],
    *,
    include_options: bool = False,
    include_broker_shorts: bool = False,
    registered_accounts=None,
    country: Optional[str] = None,
    journal_symbols: Optional[Set[str]] = None,
) -> List[PhantomCandidate]:
    """Walk transactions per (symbol, account, currency) and return one
    PhantomCandidate per pair whose running position ever went negative.

    Only BUYSELL / ASSIGN / SPLIT / OPENING_BALANCE affect position. Cash-
    flow events (DIVIDEND, INTEREST, FEE, etc.) are ignored, matching the
    main engine's pool-update rules.

    Option symbols (OCC format like AAPL250620C00150000) are skipped by
    default — negative option positions are normal (sell-to-open) and
    rarely indicate truncated history. Futures (`F:`-prefixed) are
    skipped likewise — a short future is an ordinary opening position.
    Pass include_options=True to include both anyway.

    A pair whose every short-opening sale carries the broker's own
    short-sale marker (RBC "SHORT.", or IB's Trades code O — `C;O` on
    a sale that closed a long and opened a short) is a REAL short: it
    is left out unless include_broker_shorts=True (then flagged
    broker_marked_short) — a phantom for it removed a real loss and
    left phantom shares (audit R1-8, S058-02).

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
    # corporate_timeline._walk_rest): a Norbert's-gambit pair — sell DLR.TO, buy
    # DLR.U.TO the same morning, folded to one symbol by the ticker map —
    # otherwise read as an N-share phantom short.
    sorted_txs = _drop_duplicate_splits(sorted(
        transactions,
        key=lambda t: (_walk_key(t, journal_symbols),
                       0 if float(t.quantity or 0) > 0 else 1),
    ))
    orders = OrderStarts()

    for tx in sorted_txs:
        # TRANSFER also moves position and is now consumed by the engine,
        # so include it in the running-position walk — otherwise a
        # TRANSFER-in + sell pair would falsely register as a phantom.
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
                # later sale of the renamed position reads as a phantom
                # short (the acquirer never had a BUY in this data).
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
        # consuming phantom shares. Catches both "ran negative on this
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

    out: List[PhantomCandidate] = []
    for (symbol, account, currency), s in state.items():
        if s['peak_short'] >= -1e-6:
            continue
        closing = bool(s.get('closing'))
        if s.get('derivative') and not include_options and not closing:
            continue
        marked = bool(s.get('marked')) and not s.get('unmarked')
        if marked and not include_broker_shorts:
            continue
        out.append(PhantomCandidate(
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
    mirror of a sale that goes short. Walked like detect_phantoms (per
    (symbol, account), renames and splits followed, same-moment buys
    first). Options and futures only with include_options: a buy coded
    C of a contract written before the data is option-boundary's case.
    Same-moment rows read SELLS first here (the opposite of the
    short-side walk, for the same reason): a short and its cover
    stamped alike is no evidence that the short predates the data."""
    run: Dict[Tuple[str, str], float] = {}
    orders = OrderStarts()
    out: List[UnbackedCover] = []

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
class StalePhantomEntry:
    """A phantoms.json entry today's detection would NOT propose: the
    broker marks the sales that took it short as short sales (a real
    short), or it is an option / future whose short side the broker
    never coded CLOSING (a written contract). Applying it moves a real
    short's (or a write's) gain off the totals into manual reporting
    and leaves phantom units (audit A2-0308 / A2-0310 / A2-0311 /
    A2-0637 / A2-0638 / A2-0639, R1-8)."""
    symbol: str
    account: str
    reason: str               # 'broker-short' | 'derivative'
    marker: str = ''          # how the broker marks the short


def stale_phantom_entries(
    transactions: Iterable[TaxTransaction],
    phantoms: Set[Tuple[str, str]],
) -> List[StalePhantomEntry]:
    """The listed pairs that are a broker-marked real short or a
    derivative the broker did not code CLOSING (the two cases
    detect_phantoms leaves out by default)."""
    if not phantoms:
        return []
    listed = {(str(s).upper(), str(a)) for s, a in phantoms}
    txs = list(transactions)
    cands = detect_phantoms(txs, include_options=True,
                            include_broker_shorts=True)
    out: List[StalePhantomEntry] = []
    for c in cands:
        pair = (str(c.symbol).upper(), c.account)
        if pair not in listed:
            continue
        if c.broker_marked_short:
            out.append(StalePhantomEntry(c.symbol, c.account,
                                         'broker-short',
                                         c.short_marker or 'SHORT.'))
        elif (_derivative_symbol(c.symbol or '')
              and not c.broker_says_closing):
            out.append(StalePhantomEntry(c.symbol, c.account,
                                         'derivative'))
    return out


def stale_entry_message(e: StalePhantomEntry) -> str:
    """One ATTENTION line for a stale entry (run, gains and every other
    phantoms.json applier print it; find-missing-history lists it)."""
    if e.reason == 'broker-short':
        how = ("codes the sale O (opening)" if e.marker == 'IB code O'
               else f"marks the sales {e.marker}")
        return (f"phantoms.json lists {e.symbol} / {e.account}, but the "
                f"broker {how} — a REAL short, not missing history. The "
                f"entry moves the short's gain or loss off the totals "
                f"into manual reporting and leaves phantom shares; "
                f"remove it from phantoms.json.")
    return (f"phantoms.json lists {e.symbol} / {e.account}, an option or "
            f"future the broker never coded CLOSING — its short side reads "
            f"as a WRITE (sell-to-open), not missing history. The entry "
            f"moves the premium's gain off the totals and leaves a phantom "
            f"long contract; remove it unless the contract was bought "
            f"before the data.")


@dataclass
class MissingHistoryRow:
    """A phantom candidate enriched with whether — and how much — it bears on
    a specific tax year."""
    candidate: PhantomCandidate
    affects_year: bool          # has an in-year disposition drawing from short
    in_year_dispositions: int   # count of those in-year phantom-state sells
    in_year_proceeds: float     # their summed proceeds (dollar-impact gauge)
    last_in_year_date: str


def _basis_date(tx, date_basis: str) -> str:
    if date_basis == 'settle':
        return tx.date_settle or tx.date or ''
    return tx.date or ''


def assess_tax_year_relevance(
    transactions: Iterable[TaxTransaction],
    candidates: List[PhantomCandidate],
    year: Any = None,
    *,
    date_basis: str = 'settle',
    journal_symbols: Optional[Set[str]] = None,
) -> List[MissingHistoryRow]:
    """For each phantom candidate, decide whether its missing history actually
    bears on tax year `year`.

    A candidate "affects" the year when, in that year, a row draws on the
    SHORT/phantom state:
      - a disposition (qty < 0) with the running position negative on
        either side of it — its cost basis is the missing history; or
      - a BUY that covers a short carried in (the engine books the cover
        as a short-close gain or loss in the year — audit S021-01 /
        S076-07; this is also the rule `--suggest-phantoms` uses).
    A clean sale AFTER the pool has drained back through zero (basis fully
    known) does NOT count.

    The walk follows rename-SPLITs (shares move to the new symbol, as in
    detect_phantoms — audit S075-13) and orders same-moment rows buys
    first (the phantom_walk profile — S075-12). The YEAR of a row is its
    date on `date_basis` ('settle' — the CRA default and the engine's
    year — or 'trade'), so a Dec-31 trade settling in January belongs to
    January's year (S075-16).

    `year` may be int or str (matched against the date prefix); None means "no
    year scope" — every candidate is reported as relevant, with its phantom-
    disposition totals across all years. Returns one row per candidate, in the
    candidates' order."""
    year_str = str(year) if year is not None else None
    run: Dict[Tuple[str, str], float] = {}
    stats: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for tx in _drop_duplicate_splits(
            sorted(transactions,
                   key=lambda t: _walk_key(t, journal_symbols))):
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
        if tx.action not in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        prev = run.get(key, 0.0)
        cur = prev + tx.quantity
        run[key] = cur
        draws = ((tx.quantity < 0 and (prev < -1e-9 or cur < -1e-9))
                 or (tx.quantity > 0 and prev < -1e-9
                     and tx.action in ('BUYSELL', 'ASSIGN')))
        if not draws:
            continue
        d = _basis_date(tx, date_basis)
        if year_str is not None and not d.startswith(year_str):
            continue
        st = stats.setdefault(key, {'n': 0, 'proceeds': 0.0, 'last': ''})
        st['n'] += 1
        st['proceeds'] += abs(getattr(tx, 'net_amount', 0.0) or 0.0)
        if d > st['last']:
            st['last'] = d

    out: List[MissingHistoryRow] = []
    for c in candidates:
        st = stats.get((c.symbol, c.account),
                       {'n': 0, 'proceeds': 0.0, 'last': ''})
        out.append(MissingHistoryRow(
            candidate=c,
            affects_year=(year_str is None or st['n'] > 0),
            in_year_dispositions=st['n'],
            in_year_proceeds=round(st['proceeds'], 2),
            last_in_year_date=st['last'],
        ))
    return out


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


def detect_zero_basis_acquisitions(
    transactions: Iterable[TaxTransaction],
    year: Any = None,
    *,
    include_options: bool = False,
    date_basis: str = 'settle',
) -> List[ZeroBasisRow]:
    """Flag (symbol, account) pairs that ACQUIRED shares at ~$0 cost — almost
    always a broker corporate-action row (a merger/spinoff "shares received"
    line booked with value 0) the pipeline couldn't assign a basis to — and
    then DISPOSED of them, so the missing basis silently inflates the realized
    gain.

    These never go negative (received +N, sold −N nets to zero), so the
    negative-holdings detector (`detect_phantoms`) can't see them — this is its
    complement.

    A disposition only counts while the pool actually holds $0-basis shares:
    the contamination is tracked from the $0 acquisition until the pool drains
    back through zero (mirroring ACB averaging), so a clean sale before the
    corp action — or after a full drain and fresh buy — is not flagged.

    `year` (int/str/None): when set, `affects_year` is True only if such a
    disposition falls in that year (its date on `date_basis` — the
    engine's year, audit S075-16); None reports every flagged pair.
    Rename-SPLITs carry the pool (and its $0 contamination) to the new
    symbol, as detect_phantoms does (audit S075-19).
    """
    year_str = str(year) if year is not None else None
    state: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    # Same dedupe as the module's other two walks (detect_phantoms,
    # assess_tax_year_relevance): per-broker duplicate SPLIT rows would
    # double-apply the ratio to `running`, so the drain-to-zero check
    # never cleared and clean later buys stayed flagged contaminated.
    for tx in _drop_duplicate_splits(sorted(
            transactions,
            key=lambda t: event_sort_key(t, profile='phantom_walk'))):
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
                })
                t['running'] += old['running'] * ratio
                t['zero_qty'] += old['zero_qty'] * ratio
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
            'in_year_proc': 0.0,
        })
        qty = float(tx.quantity or 0.0)
        cost = abs(float(tx.net_amount or 0.0))
        price = abs(float(tx.price or 0.0))
        if qty > 1e-9:                                   # acquisition
            if cost < 1e-6 and price < 1e-6:             # ...at ~$0 cost
                s['active'] = True
                s['zero_qty'] += qty
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
        if s['zero_qty'] <= 0 or s['any_disp'] == 0:
            continue                                      # no $0 basis hit a sale
        out.append(ZeroBasisRow(
            symbol=symbol, account=account, currency=currency,
            zero_cost_qty=round(s['zero_qty'], 4),
            acquisition_date=s['acq_date'], description=s['desc'][:80],
            looks_corp_action=s['corp'],
            affects_year=(year_str is None or s['in_year'] > 0),
            in_year_dispositions=s['in_year'],
            in_year_proceeds=round(s['in_year_proc'], 2),
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


def report_phantom_log(logs: List[List[Dict[str, Any]]],
                       accounts: Set[str]) -> None:
    """One stderr line per phantoms.json entry that did nothing in these
    books (whose account they belong to): a spelling mismatch (no rows),
    or a stale entry whose rows never go short — the note used to live
    only in the gains JSON, so a typo silently booked the phantom sale
    (audit S076-05). Entries of accounts not in these books are another
    stage's business and stay quiet."""
    by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for log in logs:
        for e in log or []:
            by_pair.setdefault((e.get('symbol', ''), e.get('account', '')),
                               []).append(e)
    for (symbol, account), es in sorted(by_pair.items()):
        if account not in accounts or any(e.get('inserted') for e in es):
            continue
        notes = [str(e.get('note') or '') for e in es]
        if any(n.startswith('no opening needed') for n in notes):
            print(f"note: phantoms.json lists {symbol} / {account}, but "
                  f"its rows never go short — no opening was needed; "
                  f"remove the entry if its history is complete.",
                  file=sys.stderr)
        elif notes and all(n.startswith('no rows') for n in notes):
            print(f"warning: phantoms.json lists {symbol} / {account}, but "
                  f"no row in the data has that symbol and account — "
                  f"nothing was applied. Check the spelling.",
                  file=sys.stderr)


def format_suggestions(candidates: List[PhantomCandidate]) -> str:
    """Write the candidate JSON to a string. Underscore-prefixed fields are
    notes for human review; the loader ignores them."""
    entries = []
    for c in candidates:
        note = (
            "Registered account — short positions prohibited; almost certainly phantom"
            if c.registered else
            "Margin/cash account — could be real short or phantom history"
        )
        if c.broker_says_closing:
            note = ("The broker codes the sale CLOSING (IB code C): it "
                    "sold a position bought before the data — missing "
                    "history, not a short or a written option"
                    + (f" (IB Basis {c.broker_basis})"
                       if c.broker_basis else ""))
        entries.append({
            "symbol": c.symbol,
            "account": c.account,
            "_note": note,
            "_first_negative": c.first_negative_date,
            # Full precision (audit S074-22: a 3e-05 BTC short read
            # -0.0); 10 dp only trims float noise.
            "_peak_short": round(c.peak_short, 10),
            "_end_position": round(c.end_position, 10),
            "_disposition_count": c.disposition_count,
        })
    return json.dumps(entries, indent=2) + "\n"


def load_phantoms(path: Path) -> Set[Tuple[str, str]]:
    """Load phantoms.json. Returns a set of (symbol, account) pairs.
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
        raise ValueError(f"{path}: expected a JSON array of phantom entries")
    out: Set[Tuple[str, str]] = set()
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
        out.add((str(symbol).strip().upper(), str(account).strip()))
    return out


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
                    f"({loss.get('date')}) has {len(nearby)} tainted disposition(s) within "
                    f"±{window_days} days ({LOSS_RULE[canonical_country(country)][1]} "
                    f"dates). {LOSS_RULE[canonical_country(country)][0][0].upper()}"
                    f"{LOSS_RULE[canonical_country(country)][0][1:]} may apply; "
                    f"verify manually."
                ),
            })
    return out


def synthesize_openings(
    transactions: List[TaxTransaction],
    phantoms: Set[Tuple[str, str]],
    *, warn: bool = False,
    flag_stale: bool = True,
) -> Tuple[List[TaxTransaction], List[Dict[str, Any]]]:
    """For each (symbol, account) in phantoms, compute the minimum running
    position over the data and prepend an OPENING_BALANCE transaction with
    quantity = abs(min). Returns (new_tx_list, applied) where `applied` is
    a per-entry log of what was inserted (or skipped, when the data didn't
    actually need an opening balance — useful for surfacing mis-classified
    entries the user can prune).

    No-op for phantoms that don't go negative in the data: the log entry
    notes this so the user knows the phantoms.json entry was redundant.

    An applied entry that today's detection would not propose — a
    broker-marked real short, or an option / future the broker never
    coded CLOSING (stale_phantom_entries) — is still applied (it is the
    user's explicit record), but its log entry carries `stale` and,
    with `flag_stale` (the default), an ATTENTION line goes to stderr:
    this is the one applier every caller shares (run's gains stages,
    t1135, wash-radar, option-boundary, apply-distributions), so each
    of them says so (audit A2-0308 / A2-0310 / A2-0637, R1-M006).
    """
    if not phantoms:
        return list(transactions), []

    # Compute min running position per LISTED pair — same walk as
    # detect_phantoms but restricted to listed pairs (expanded to their
    # rename chains), and tracking the earliest activity so the synthetic
    # opening lands before any real transaction touches the pool.
    sorted_txs = _drop_duplicate_splits(
        sorted(transactions,
               key=lambda t: event_sort_key(t, profile='phantom_walk')))

    # --- Rename-chain expansion. detect_phantoms migrates the running
    # balance across SPLIT-renames and reports candidates under the NEW
    # ticker, so phantoms.json lists (NEW, account) — but the deficit's
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
    # rename chain listed — which --suggest-phantoms itself emits), the
    # chain belongs to the most-downstream listed pair: one opening,
    # sized on the whole chain. Iterating the set let PYTHONHASHSEED pick
    # the owner, and the wrong one sized TWO openings (audit S021-04).
    chains: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}
    for pair in sorted(phantoms):
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
    for pair in sorted(phantoms):
        owners = [q for q in sorted(phantoms)
                  if q != pair and pair in chains[q]]
        if owners:
            # The downstream-most owner: the one no other owner contains.
            folded_into[pair] = next(
                (q for q in owners
                 if not any(q in chains[o] for o in owners if o != q)),
                owners[0])
    member_to_pair: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for pair in sorted(phantoms):
        if pair in folded_into:
            continue
        for m in sorted(chains[pair]):
            member_to_pair.setdefault(m, pair)
    phantoms = {p for p in phantoms if p not in folded_into}
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
    min_running: Dict[Tuple[str, str], float] = {p: 0.0 for p in phantoms}
    running: Dict[Tuple[str, str], float] = {p: 0.0 for p in phantoms}
    split_factor: Dict[Tuple[str, str], float] = {p: 1.0 for p in phantoms}
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
        # TRANSFER moves engine position too (detect_phantoms counts it, and
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
        if running[pair] < min_running[pair]:
            min_running[pair] = running[pair]
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
                             'check the spelling in phantoms.json')
            # Quiet by default: every account's stage is handed the
            # whole project file, so another account's entry has no rows
            # here by design. pipeline.prepare_books reports the
            # project-level result once (report_phantom_log).
            if warn:
                print(f"warning: phantoms.json lists {symbol} / {account}, "
                      f"but no row in the data has that symbol and "
                      f"account — nothing was applied. Check the "
                      f"spelling.", file=sys.stderr)
            applied.append(entry)
            continue
        if min_pos >= -1e-6:
            # Listed in phantoms.json but the data is actually complete.
            # Surface this so the user can prune the file.
            entry['note'] = 'no opening needed — data does not go negative for this pair'
            applied.append(entry)
            continue

        opening_qty = abs(min_pos)
        # Anchor on the CHAIN's earliest symbol and ONE DAY BEFORE its first
        # activity. The earliest symbol lets the engine replay the opening
        # through any rename. The opening anchors ON that first-activity
        # date: every sort that sees an OPENING_BALANCE now has an explicit
        # OB-first rung (engine profiles' priority/phase ladders, the
        # phantom walks' WalkPriority — all via event_sort_key), so a
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
            description=f'PHANTOM opening — pre-data-window history (qty={opening_qty})',
        ))
        entry['opening_qty'] = opening_qty
        entry['inserted'] = True
        entry['anchor_date'] = anchor_date
        entry['anchor_symbol'] = anchor_symbol
        applied.append(entry)

    inserted = {(e['symbol'], e['account']) for e in applied
                if e.get('inserted')}
    if inserted:
        stale = stale_phantom_entries(
            [t for t in sorted_txs if (t.symbol, t.account) in member_to_pair],
            inserted)
        by_pair = {(e.symbol.upper(), e.account): e for e in stale}
        for entry in applied:
            st = by_pair.get((entry['symbol'], entry['account']))
            if st is None or not entry.get('inserted'):
                continue
            entry['stale'] = st.reason
            if flag_stale:
                print(f"warning: ATTENTION: {stale_entry_message(st)}",
                      file=sys.stderr)
    return out, applied
