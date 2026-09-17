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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import (SplitTimeline, event_sort_key,
                                             normalize_symbol_new)


# Canadian registered-account labels. Short positions are prohibited by
# CRA in these accounts, so a negative balance is almost certainly phantom.
REGISTERED_ACCOUNT_PATTERNS = (
    'LIRA', 'RRSP', 'RRIF', 'TFSA', 'RESP', 'LIF', 'FHSA', 'LRIF', 'PRPP', 'RDSP',
)


# OCC option-symbol detection uses the canonical engine-side definition
# (anchored regex with optional futures prefix and market suffix) so this
# module stays in sync with the gain engine. Previously this module
# defined its own unanchored substring check, which could mis-classify
# any synthesized phantom symbol whose mid-string digits happened to
# match `\d{6}[CP]\d+`.
from taxjson.lib.core import is_option_symbol  # noqa: F401 — re-exported


def is_registered_account(account: str) -> bool:
    if not account:
        return False
    upper = account.upper()
    return any(p in upper for p in REGISTERED_ACCOUNT_PATTERNS)


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


def detect_phantoms(
    transactions: Iterable[TaxTransaction],
    *,
    include_options: bool = False,
) -> List[PhantomCandidate]:
    """Walk transactions per (symbol, account, currency) and return one
    PhantomCandidate per pair whose running position ever went negative.

    Only BUYSELL / ASSIGN / SPLIT / OPENING_BALANCE affect position. Cash-
    flow events (DIVIDEND, INTEREST, FEE, etc.) are ignored, matching the
    main engine's pool-update rules.

    Option symbols (OCC format like AAPL250620C00150000) are skipped by
    default — negative option positions are normal (sell-to-open) and
    rarely indicate truncated history. Pass include_options=True to
    include them anyway.
    """
    # state[(symbol, account, currency)] -> running, peak_short, first_neg, count
    state: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    sorted_txs = _drop_duplicate_splits(sorted(
        transactions,
        key=lambda t: event_sort_key(t, profile='phantom_walk'),
    ))

    for tx in sorted_txs:
        # TRANSFER also moves position and is now consumed by the engine,
        # so include it in the running-position walk — otherwise a
        # TRANSFER-in + sell pair would falsely register as a phantom.
        if tx.action not in ('BUYSELL', 'ASSIGN', 'SPLIT', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        if not include_options and is_option_symbol(tx.symbol):
            continue
        key = (tx.symbol, tx.account, tx.currency or '')
        s = state.setdefault(key, {
            'running': 0.0,
            'peak_short': 0.0,
            'first_negative_date': None,
            'disposition_count': 0,
        })

        if tx.action == 'SPLIT':
            factor = float(tx.quantity or 0.0)
            new_sym = normalize_symbol_new(tx.symbol, tx.symbol_new)
            if new_sym:
                # SPLIT-RENAME (s.85.1(5) rollover merger, ticker change):
                # move the scaled pool onto the new ticker so its shares
                # aren't seen as appearing from nowhere. Without this, a
                # later sale of the renamed position reads as a phantom
                # short (the acquirer never had a BUY in this data).
                tgt = state.setdefault((new_sym, tx.account, tx.currency or ''), {
                    'running': 0.0,
                    'peak_short': 0.0,
                    'first_negative_date': None,
                    'disposition_count': 0,
                })
                tgt['running'] += s['running'] * factor
                s['running'] = 0.0
            else:
                s['running'] *= factor
            continue

        prev = s['running']
        s['running'] += tx.quantity

        # A disposition while running is negative is one of the events
        # consuming phantom shares. Catches both "ran negative on this
        # disposition" and "was already negative when this disposition fired."
        if tx.quantity < 0 and (prev < 0 or s['running'] < 0):
            s['disposition_count'] += 1

        if s['running'] < s['peak_short']:
            s['peak_short'] = s['running']
            if s['first_negative_date'] is None:
                s['first_negative_date'] = tx.date

    out: List[PhantomCandidate] = []
    for (symbol, account, currency), s in state.items():
        if s['peak_short'] >= -1e-6:
            continue
        out.append(PhantomCandidate(
            symbol=symbol,
            account=account,
            currency=currency,
            first_negative_date=s['first_negative_date'] or '',
            peak_short=s['peak_short'],
            end_position=s['running'],
            disposition_count=s['disposition_count'],
            registered=is_registered_account(account),
        ))
    out.sort(key=lambda c: (c.symbol, c.account))
    return out


@dataclass
class MissingHistoryRow:
    """A phantom candidate enriched with whether — and how much — it bears on
    a specific tax year."""
    candidate: PhantomCandidate
    affects_year: bool          # has an in-year disposition drawing from short
    in_year_dispositions: int   # count of those in-year phantom-state sells
    in_year_proceeds: float     # their summed proceeds (dollar-impact gauge)
    last_in_year_date: str


def assess_tax_year_relevance(
    transactions: Iterable[TaxTransaction],
    candidates: List[PhantomCandidate],
    year: Any = None,
) -> List[MissingHistoryRow]:
    """For each phantom candidate, decide whether its missing history actually
    bears on tax year `year`.

    A candidate "affects" the year when it has a disposition (BUYSELL/ASSIGN
    with qty < 0) dated in that year that draws from the SHORT/phantom state —
    i.e. the running position is at or below zero across the sale, so the
    sale's cost basis is the missing history. A clean sale AFTER the pool has
    drained back through zero (basis fully known) does NOT count, so a symbol
    whose truncation is entirely in prior years isn't flagged for this year.

    `year` may be int or str (matched against the date prefix); None means "no
    year scope" — every candidate is reported as relevant, with its phantom-
    disposition totals across all years. Returns one row per candidate, in the
    candidates' order."""
    year_str = str(year) if year is not None else None
    pairs = {(c.symbol, c.account) for c in candidates}
    run: Dict[Tuple[str, str], float] = {p: 0.0 for p in pairs}
    stats: Dict[Tuple[str, str], Dict[str, Any]] = {
        p: {'n': 0, 'proceeds': 0.0, 'last': ''} for p in pairs}

    for tx in _drop_duplicate_splits(
            sorted(transactions,
                   key=lambda t: event_sort_key(t, profile='phantom_walk'))):
        key = (tx.symbol, tx.account)
        if key not in pairs:
            continue
        if tx.action == 'SPLIT':
            run[key] *= tx.quantity
            continue
        if tx.action not in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER'):
            continue
        prev = run[key]
        run[key] += tx.quantity
        # A sale drawing from the short/phantom state (mirrors detect_phantoms'
        # disposition_count): qty<0 with the position negative on either side.
        phantom_sale = tx.quantity < 0 and (prev < 0 or run[key] < 0)
        if not phantom_sale:
            continue
        if year_str is not None and not (tx.date or '').startswith(year_str):
            continue
        s = stats[key]
        s['n'] += 1
        s['proceeds'] += abs(getattr(tx, 'net_amount', 0.0) or 0.0)
        if (tx.date or '') > s['last']:
            s['last'] = tx.date or ''

    out: List[MissingHistoryRow] = []
    for c in candidates:
        s = stats[(c.symbol, c.account)]
        out.append(MissingHistoryRow(
            candidate=c,
            affects_year=(year_str is None or s['n'] > 0),
            in_year_dispositions=s['n'],
            in_year_proceeds=round(s['proceeds'], 2),
            last_in_year_date=s['last'],
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
    disposition falls in that year; None reports every flagged pair.
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
        if tx.action not in ('BUYSELL', 'SPLIT'):
            continue
        key = (tx.symbol, tx.account, tx.currency or '')
        s = state.setdefault(key, {
            'running': 0.0, 'active': False, 'zero_qty': 0.0, 'acq_date': '',
            'desc': '', 'corp': False, 'any_disp': 0, 'in_year': 0,
            'in_year_proc': 0.0,
        })
        if tx.action == 'SPLIT':
            s['running'] *= float(tx.quantity or 0.0)
            continue
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
                if year_str is None or (tx.date or '').startswith(year_str):
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


_MERGER_TO_RE = re.compile(r'\bMERGER\s+TO\s+(.+?)(?:\s+[\d.]+\s+NEW|\s*$)', re.I)
_RATIO_RE = re.compile(r'([\d.]+)\s+NEW\s*=\s*([\d.]+)\s+OLD', re.I)
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
        same = [(i, rc) for i, rc in enumerate(receipts)
                if i not in used and rc.account == rem.account
                and rc.date == rem.date]
        pick = None
        for i, rc in same:                                  # 1) name match
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
        rr = _RATIO_RE.search(rem.description or '')
        ratio = (float(rr.group(1)) / float(rr.group(2))
                 if rr and float(rr.group(2)) else 0.0)
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
        entries.append({
            "symbol": c.symbol,
            "account": c.account,
            "_note": note,
            "_first_negative": c.first_negative_date,
            "_peak_short": round(c.peak_short, 4),
            "_end_position": round(c.end_position, 4),
            "_disposition_count": c.disposition_count,
        })
    return json.dumps(entries, indent=2) + "\n"


def load_phantoms(path: Path) -> Set[Tuple[str, str]]:
    """Load phantoms.json. Returns a set of (symbol, account) pairs.
    Underscore-prefixed metadata fields are ignored."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
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
        out.add((symbol, account))
    return out


def detect_superficial_loss_warnings(
    clean_losses: List[Dict[str, Any]],
    all_tainted: List[Dict[str, Any]],
    *,
    window_days: int = 30,
) -> List[Dict[str, Any]]:
    """Find clean losses with tainted dispositions on the same symbol
    within ±window_days. The tainted leg has unknown ACB so the
    superficial-loss adjustment (CRA ITA 54, IRS §1091) can't be
    computed automatically. Each warning identifies the affected
    clean loss and the tainted disposition(s) within the window so
    the user can resolve it manually.

    `clean_losses` should already be filtered to losses (gain < 0) in
    the tax year of interest. `all_tainted` should include tainted
    dispositions across ALL years — a Dec 28 tainted disposition can
    affect a Jan 5 in-year loss the next year, and vice versa.
    """
    from datetime import datetime as _dt
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
            # Settle-basis (matches the engine's CRA window).
            loss_dt = _dt.strptime(
                loss.get('date_settle') or loss.get('date', ''), '%Y-%m-%d')
        except ValueError:
            continue
        nearby: List[Dict[str, Any]] = []
        for t in candidates:
            try:
                t_dt = _dt.strptime(
                    t.get('date_settle') or t.get('date', ''), '%Y-%m-%d')
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
                    f"±{window_days} days. The superficial-loss rule may apply; verify manually."
                ),
            })
    return out


def synthesize_openings(
    transactions: List[TaxTransaction],
    phantoms: Set[Tuple[str, str]],
) -> Tuple[List[TaxTransaction], List[Dict[str, Any]]]:
    """For each (symbol, account) in phantoms, compute the minimum running
    position over the data and prepend an OPENING_BALANCE transaction with
    quantity = abs(min). Returns (new_tx_list, applied) where `applied` is
    a per-entry log of what was inserted (or skipped, when the data didn't
    actually need an opening balance — useful for surfacing mis-classified
    entries the user can prune).

    No-op for phantoms that don't go negative in the data: the log entry
    notes this so the user knows the phantoms.json entry was redundant.
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

    member_to_pair: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for pair in phantoms:
        stack = [pair]
        seen_members = set()
        while stack:
            m = stack.pop()
            if m in seen_members:
                continue                       # cycle guard
            seen_members.add(m)
            member_to_pair.setdefault(m, pair)
            stack.extend(reverse_renames.get(m, []))

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
    for (symbol, account), min_pos in min_running.items():
        entry: Dict[str, Any] = {
            'symbol': symbol,
            'account': account,
            'opening_qty': 0.0,
            'inserted': False,
        }
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
    return out, applied
