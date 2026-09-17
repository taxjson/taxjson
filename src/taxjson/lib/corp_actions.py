"""Corporate-action normalization + country-specific election rules.

Three layers:

1. Broker extractors (`parse_ib_corporate_actions`,
   `parse_questrade_corporate_actions`) read each broker's CSV and emit
   a normalized list of `CorporateAction` events. IB-side handling
   collapses cross-listing journals (multi-hop, with cycle guard),
   drops `Code=Ca` cancellations, and uses ISIN country prefixes for
   market suffixes. Questrade-side handles spinoff DIS-row sequences
   (warrant→rights conversions netting to one logical event).

2. `RULES_BY_COUNTRY[country][action_type]` maps `(event, election_choice)`
   to a list of taxjson transaction dicts. Currently implemented:
   - canada/merger: `taxable_disposition`, `rollover_s_85_1_5`
   - canada/spinoff: `taxable_deemed_dividend`, `rollover_s_86_1`
   `IGNORE_ELECTION` is universal (skips emit for IB-noise rows).

3. `Manifest` (JSON on disk, atomic save) records the user's election
   decision per `event_id`. Re-running with the same manifest is
   deterministic and produces the same taxjson rows; that file is the
   audit artifact for why a given event was treated as a rollover vs.
   a disposition vs. ignored. event_id normalizes the date before
   hashing so IB timezone-suffix drift doesn't orphan elections.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# --- Event schema ----------------------------------------------------------


@dataclass
class CorporateAction:
    """Normalized corporate-action event, broker-agnostic.

    The `event_id` is a stable hash of the identifying fields so re-running
    extraction produces the same id and the elections manifest stays valid
    across re-runs (the manifest is keyed by event_id).
    """
    date: str
    time: str
    action_type: str        # 'merger' | 'spinoff' | 'split' | 'name_change'
    source_symbol: str      # e.g. 'SSL.TO'
    source_isin: str
    target_symbol: str      # e.g. 'RGLD.US'
    target_isin: str
    ratio_new: float        # "1 for 16" → 1
    ratio_old: float        # "1 for 16" → 16
    qty_disposed: float
    qty_received: float
    fmv: float              # source-currency FMV of the disposition leg
    currency: str           # disposition currency (drives source suffix)
    target_currency: str    # acquisition currency (drives target suffix)
    account: str
    raw_descriptions: List[str] = field(default_factory=list)
    event_id: str = ""
    # Acquisition-side FMV in the target currency. Critical when the
    # merger crosses currencies (SSL.TO CAD → RGLD.US USD): the BUY
    # leg's new-basis is the USD value, not the source-side CAD figure.
    # Defaults to 0 for back-compat / unknown — rules that need it
    # should fall back to `fmv` rather than emit a zero-value row.
    target_fmv: float = 0.0
    # Cash-in-lieu the broker paid for the fractional target share a
    # merger ratio leaves over (RBC's `CIL` rows). Booked as a small
    # disposition of the fractional share; 0 when the ratio divided
    # cleanly or the broker reported no cash-in-lieu.
    cash_in_lieu: float = 0.0
    cash_in_lieu_currency: str = ""
    # True when the broker actually DELIVERS fractional shares (IB):
    # qty_received is the exact delivered quantity, so emitters must
    # not snap a real 0.5-share delivery to whole+cash-in-lieu — that
    # manufactured a phantom -0.5 short the moment the user sold their
    # actual fractional (Honeywell split-up, real data 2026-07).
    # False (default): unknown broker semantics — snap as before.
    fractional_delivery: bool = False

    def __post_init__(self):
        if not self.event_id:
            self.event_id = self._compute_id()

    @property
    def ratio(self) -> float:
        return self.ratio_new / self.ratio_old if self.ratio_old else 0.0

    def summary(self) -> str:
        return (
            f"{self.date} {self.action_type}: "
            f"{self.source_symbol} → {self.target_symbol} "
            f"({self.ratio_new}-for-{self.ratio_old}, ratio {self.ratio:.6g}, "
            f"FMV {self.fmv:.2f} {self.currency})"
        )

    @staticmethod
    def _normalize_date(s: str) -> str:
        """Reduce a date string to ISO `YYYY-MM-DD`. IB occasionally
        appends timezone hints (`"2025-10-22 EST"`, `"2025-10-22, 20:25"`)
        that would otherwise change the hashed `event_id` between runs
        and orphan the user's saved election in the manifest. Strip
        anything after the first 10 chars; if those 10 don't look like
        a date, fall through and let the hash use the raw string (a
        wrong-format input is a separate problem the caller should
        surface)."""
        if not s:
            return ''
        head = s[:10]
        if len(head) == 10 and head[4] == '-' and head[7] == '-':
            return head
        return s

    def _hash(self, n: int) -> str:
        parts = (
            self._normalize_date(self.date),
            self.action_type, self.source_isin, self.target_isin,
            f"{self.ratio_new}-for-{self.ratio_old}", self.account,
        )
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:n]

    @staticmethod
    def _sym_root(symbol: str) -> str:
        """Lowercased symbol with its market suffix and punctuation
        stripped: SSL.TO -> ssl, RGLD.CAD.TO -> rgldcad, BRK.B.US ->
        brkb. Used only for the readable part of the event id."""
        parts = (symbol or '').rsplit('.', 1)
        if len(parts) == 2 and parts[1].upper() in set(
                _CURRENCY_SUFFIX.values()):
            symbol = parts[0]
        root = re.sub(r'[^A-Za-z0-9]', '', symbol).lower()
        return root or 'x'

    def _compute_id(self) -> str:
        """Human-legible id: `YYYYMMDD-src-tgt-hhhh`, e.g.
        `20251022-ssl-rgld-51d7`. The readable part carries date and
        symbols; the 4-hex suffix (hashed from the FULL identifying
        fields: ISINs, ratio, account) keeps ids unique when the
        readable part collides. Pre-2026-07 manifests used the bare
        12-hex hash — `legacy_event_id()` + `Manifest.migrate_legacy`
        rekey them automatically."""
        date = self._normalize_date(self.date).replace('-', '')
        return (f"{date}-{self._sym_root(self.source_symbol)}-"
                f"{self._sym_root(self.target_symbol)}-{self._hash(4)}")

    def legacy_event_id(self) -> str:
        """The pre-2026-07 opaque id (12-hex content hash) — kept so
        existing manifests migrate instead of orphaning elections."""
        return self._hash(12)

    def to_dict(self) -> dict:
        return asdict(self)


# --- IB extractor ----------------------------------------------------------


# Matches an IB "Merged(Acquisition)" Corporate Action description, e.g.:
#   SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (RGLD.CAD, ROYAL GOLD INC, US0000000002)
# The trailing parenthetical block also carries a target ticker (which may be
# the cross-listing intermediate like "RGLD.CAD") that we use to chain the
# subsequent .CAD→.US journal into a single logical event.
_IB_MERGER_RE = re.compile(
    r'^([A-Z0-9.]+)\(([A-Z0-9]+)\)\s+Merged\(Acquisition\)\s+WITH\s+'
    r'([A-Z0-9]+)\s+(\d+)\s+for\s+(\d+)\s*'
    r'\(([A-Z0-9.]+),\s*[^,]+,\s*([A-Z0-9]+)\)',
    re.IGNORECASE,
)

# Multi-counterparty variant — a SPLIT-UP / separation, e.g. Honeywell:
#   HON(US4385161066) Merged(Acquisition) WITH HONAV 1 for 2,
#   US4385162056 1 for 2 (HONA, HONEYWELL AEROSPACE, US43849R1059)
# One old share is exchanged for shares of TWO (or more) successor
# companies. IB reports one out-leg (the old position) and one in-leg
# per successor, all under the same description. The single-target
# regex above can't match the comma-separated WITH clause, so these
# events were silently dropped — leaving phantom positions (the old
# symbol never disposed / the successors never acquired).
_IB_MULTI_MERGER_RE = re.compile(
    r'^([A-Z0-9.]+)\(([A-Z0-9]+)\)\s+Merged\(Acquisition\)\s+WITH\s+'
    r'((?:[A-Z0-9.]+\s+\d+\s+for\s+\d+)(?:\s*,\s*[A-Z0-9.]+\s+\d+\s+for\s+\d+)+)\s*'
    r'\(([A-Z0-9.]+),\s*[^,]+,\s*([A-Z0-9]+)\)',
    re.IGNORECASE,
)
_IB_WITH_PAIR_RE = re.compile(
    r'([A-Z0-9.]+)\s+(\d+)\s+for\s+(\d+)', re.IGNORECASE)

# Tender / voluntary-offer share journals. IB books a tendered position
# as a pair of zero-proceeds legs that move the shares onto a `.TEN`
# placeholder line, then — on allocation — either journals them back
# with a `Merged(Voluntary Offer Allocation)` pair or settles them:
#   AAUC(CA0193081049) Tendered to 12345678 1 FOR 1 (AAUC.TEN, ALLIED GOLD CORP - TENDER, CA0193081049)
#   AAUC.TEN(12345678) Merged(Voluntary Offer Allocation) WITH CA0193081049 1 for 1 (AAUC, ALLIED GOLD CORP, CA0193081049)
# Neither is a merger the election machinery should ask about: the
# zero-proceeds round trip is a no-op journal and a cash settlement is
# a plain disposition. Both are handled by the statement parser
# (ib_extractor's Corporate Actions branch, via `ib_tender_root`);
# recognized here so the merger regexes can never be widened onto them
# by accident and so `parse_ib_corporate_actions` skips them on purpose.
_IB_TENDER_RE = re.compile(
    r'^\s*([A-Z0-9][A-Z0-9\s.]*?)\s*\(([^)]*)\)\s+'
    r'(?:Tendered\s+to\b|Merged\(Voluntary\s+Offer\s+Allocation\))',
    re.IGNORECASE)


def ib_tender_root(description: str) -> Optional[str]:
    """The underlying ticker (`.TEN` placeholder suffix stripped) when
    `description` is an IB tender / voluntary-offer journal row, else
    None. Both the out-leg (`AAUC(...) Tendered to ...`) and the
    placeholder leg (`AAUC.TEN(...) Merged(Voluntary Offer ...)`)
    resolve to the same root so a statement parser can net them."""
    m = _IB_TENDER_RE.match(description or '')
    if not m:
        return None
    root = m.group(1).strip().replace(' ', '.')
    if root.upper().endswith('.TEN'):
        root = root[:-4]
    return root

_CURRENCY_SUFFIX = {'CAD': 'TO', 'USD': 'US', 'AUD': 'AX', 'GBP': 'L'}


def _f(x: str) -> float:
    return float((x or '0').replace(',', '')) if (x or '').strip() not in ('', '-') else 0.0


def parse_ib_corporate_actions(csv_path: Path, account: str = 'IB') -> List[CorporateAction]:
    """Extract merger events from an IB Activity Statement CSV.

    `account` is stamped onto every emitted CorporateAction so downstream
    tools key the right pool (Margin / RRSP / TFSA / LIRA). Defaults to
    'IB' to keep the historical behaviour for callers that pre-date the
    multi-account refactor.

    Handles two flavours of noise that show up in real statements:

    * `Code=Ca` rows are IB cancellations — we drop them so they don't
      double-count.
    * Cross-listing journals (a CAD-side merger entry immediately followed
      by a 1-for-1 CAD→US "Merged(Acquisition) WITH ..." that's really
      just IB moving the position from the .TO sub-account to the .US one)
      get collapsed into the original SSL→RGLD.US event.
    """
    rows: List[Dict[str, str]] = []
    with csv_path.open('r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header_map: Dict[str, int] = {}
        for raw_row in reader:
            if not raw_row or raw_row[0] != 'Corporate Actions':
                continue
            if raw_row[1] == 'Header':
                header_map = {c: i for i, c in enumerate(raw_row)}
                continue
            if raw_row[1] != 'Data':
                continue
            currency = raw_row[header_map.get('Currency', 3)]
            if currency in ('', 'Total', 'Total in CAD'):
                continue
            # Cancellation rows undo a previous entry — drop them, don't
            # let them slip through and double the position.
            code = raw_row[header_map.get('Code', len(raw_row) - 1)] if 'Code' in header_map else ''
            if 'Ca' in (code or '').split(';'):
                continue
            # Tender / voluntary-offer journals are NOT mergers (see
            # _IB_TENDER_RE): the statement parser nets the zero-
            # proceeds round trip and books a cash settlement as a
            # sale. Skipped explicitly so a widened merger regex can
            # never turn the `.TEN` placeholder into an election.
            if _IB_TENDER_RE.match(raw_row[header_map.get('Description', 6)]
                                   or ''):
                continue
            rows.append({
                'currency': currency,
                'date_time': raw_row[header_map.get('Date/Time', 5)],
                'description': raw_row[header_map.get('Description', 6)],
                'quantity': raw_row[header_map.get('Quantity', 7)],
                'value': raw_row[header_map.get('Value', 9)],
            })

    # Group rows that describe the same merger. IB emits two rows per
    # event but uses inconsistent target tickers in each leg's parenthetical
    # — the in-leg names the acquirer (e.g. RGLD.CAD), the out-leg re-names
    # the source itself (e.g. SSL). The one invariant across both legs is
    # the "WITH <ISIN>" — the merger counterparty. Key on that.
    grouped: Dict[tuple, Dict[str, Any]] = {}
    multi_grouped: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        desc = r['description'].strip()
        m = _IB_MERGER_RE.match(desc)
        if not m:
            mm = _IB_MULTI_MERGER_RE.match(desc)
            if mm:
                src_sym, src_isin, clause, leg_sym, leg_isin = mm.groups()
                mkey = (r['date_time'], src_isin, clause)
                mb = multi_grouped.setdefault(mkey, {
                    'date_time': r['date_time'],
                    'src_sym': src_sym, 'src_isin': src_isin,
                    'pairs': [(tok, float(rn), float(ro)) for tok, rn, ro
                              in _IB_WITH_PAIR_RE.findall(clause)],
                    'qty_out': 0.0, 'val_out': 0.0,
                    'currency': r['currency'],
                    'in_legs': [], 'descriptions': [],
                })
                mb['descriptions'].append(desc)
                qty, val = _f(r['quantity']), _f(r['value'])
                if qty < 0:
                    mb['qty_out'] += qty
                    mb['val_out'] += val
                    mb['currency'] = r['currency']
                else:
                    # Each in-leg's parenthetical names ITS successor.
                    mb['in_legs'].append({
                        'sym': leg_sym, 'isin': leg_isin, 'qty': qty,
                        'val': val, 'currency': r['currency']})
            continue
        src_sym, src_isin, with_isin, ratio_new, ratio_old, tgt_sym, tgt_isin = m.groups()
        # Cross-listing journals split their two legs across currencies
        # (CAD out, USD in), so currency can't be part of the key — the
        # in/out pair would never reunite. (src_isin, with_isin, date_time)
        # is enough: IB doesn't run two unrelated mergers with the same
        # source on the same timestamp.
        key = (r['date_time'], src_isin, with_isin)
        bucket = grouped.setdefault(key, {
            'date_time': r['date_time'],
            'descriptions': [],
            # OUT-leg currency = disposition currency (drives source suffix);
            # IN-leg currency = acquisition currency (drives target suffix).
            # We track both because cross-listing chains can land them in
            # different currencies (CAD out, USD in for the final-leg link).
            'currency': r['currency'],
            'tgt_currency': r['currency'],
            'src_sym': src_sym, 'src_isin': src_isin,
            'with_isin': with_isin,
            # Resolved when we see the in-leg (positive qty) — the in-leg's
            # parenthetical names the real acquirer; the out-leg's names
            # the source again, which we don't want as the target.
            'tgt_sym': None, 'tgt_isin': None,
            'ratio_new': float(ratio_new), 'ratio_old': float(ratio_old),
            'qty_out': 0.0, 'qty_in': 0.0,
            'val_out': 0.0, 'val_in': 0.0,
        })
        bucket['descriptions'].append(r['description'])
        qty = _f(r['quantity'])
        val = _f(r['value'])
        if qty < 0:
            bucket['qty_out'] += qty
            bucket['val_out'] += val
            # The out-leg's currency is the disposition currency.
            bucket['currency'] = r['currency']
        else:
            bucket['qty_in'] += qty
            bucket['val_in'] += val
            # The in-leg names the real target.
            bucket['tgt_sym'] = tgt_sym
            bucket['tgt_isin'] = tgt_isin
            bucket['tgt_currency'] = r['currency']

    events: List[CorporateAction] = []
    for bucket in grouped.values():
        if bucket['qty_out'] == 0 or bucket['qty_in'] == 0:
            # Half-an-event — IB sometimes splits across statements. Skip;
            # we can't safely emit without both sides.
            continue
        if not bucket['tgt_isin']:
            # No in-leg was processed (shouldn't happen given the qty_in
            # check above, but defensive).
            continue
        src_suffix = _CURRENCY_SUFFIX.get(bucket['currency'], bucket['currency'])
        src_symbol = _apply_suffix(bucket['src_sym'], src_suffix)
        tgt_suffix = _CURRENCY_SUFFIX.get(bucket['tgt_currency'], bucket['tgt_currency'])
        tgt_symbol = _apply_suffix(bucket['tgt_sym'], tgt_suffix)
        date_part, _, time_part = bucket['date_time'].partition(',')
        date = date_part.strip()
        time = (time_part.strip() or '20:25:00')
        ev = CorporateAction(
            date=date, time=time,
            action_type='merger',
            source_symbol=src_symbol, source_isin=bucket['src_isin'],
            target_symbol=tgt_symbol, target_isin=bucket['tgt_isin'],
            ratio_new=bucket['ratio_new'], ratio_old=bucket['ratio_old'],
            qty_disposed=abs(bucket['qty_out']),
            qty_received=abs(bucket['qty_in']),
            fmv=abs(bucket['val_out']),
            target_fmv=abs(bucket['val_in']),
            currency=bucket['currency'],
            target_currency=bucket['tgt_currency'],
            account=account,
            raw_descriptions=bucket['descriptions'],
            fractional_delivery=True,      # IB delivers real fractions
        )
        events.append(ev)

    # Split-ups: decompose into events the rules already know. The
    # continuing entity (in-leg whose ticker matches the source, else
    # whose ISIN appears in the WITH clause, else the first) is a
    # MERGER old→new; every other successor is a SPINOFF from the
    # continuing entity. That mirrors the tax shape (Canada: s. 85.1(5)
    # question on the exchange, s. 86.1 question on the distribution).
    for mb in multi_grouped.values():
        if mb['qty_out'] >= 0 or not mb['in_legs']:
            continue                    # need both sides to emit safely
        legs = mb['in_legs']
        cont = next((l for l in legs if l['sym'] == mb['src_sym']), None)
        if cont is None:
            toks = {p[0] for p in mb['pairs']}
            cont = next((l for l in legs if l['isin'] in toks), legs[0])
        used: set = set()

        def _pair_for(leg):
            for i, (tok, rn, ro) in enumerate(mb['pairs']):
                if i not in used and tok in (leg['isin'], leg['sym']):
                    used.add(i)
                    return rn, ro
            for i, (tok, rn, ro) in enumerate(mb['pairs']):
                if i not in used:
                    used.add(i)
                    return rn, ro
            return 1.0, 1.0

        src_suffix = _CURRENCY_SUFFIX.get(mb['currency'], mb['currency'])
        src_symbol = _apply_suffix(mb['src_sym'], src_suffix)
        cont_symbol = _apply_suffix(
            cont['sym'],
            _CURRENCY_SUFFIX.get(cont['currency'], cont['currency']))
        date_part, _, time_part = mb['date_time'].partition(',')
        date = date_part.strip()
        time = (time_part.strip() or '20:25:00')
        cn, co = _pair_for(cont)
        events.append(CorporateAction(
            date=date, time=time, action_type='merger',
            source_symbol=src_symbol, source_isin=mb['src_isin'],
            target_symbol=cont_symbol, target_isin=cont['isin'],
            ratio_new=cn, ratio_old=co,
            qty_disposed=abs(mb['qty_out']),
            qty_received=cont['qty'],
            fmv=abs(mb['val_out']), target_fmv=cont['val'],
            currency=mb['currency'], target_currency=cont['currency'],
            account=account, raw_descriptions=mb['descriptions'],
            fractional_delivery=True))
        for leg in legs:
            if leg is cont:
                continue
            rn, ro = _pair_for(leg)
            leg_symbol = _apply_suffix(
                leg['sym'],
                _CURRENCY_SUFFIX.get(leg['currency'], leg['currency']))
            events.append(CorporateAction(
                # +1s: the spinoff's rows must sort AFTER the merger's
                # rename so the parent pool exists to allocate from.
                date=date, time=_bump_time(time, 1),
                action_type='spinoff',
                source_symbol=cont_symbol, source_isin=cont['isin'],
                target_symbol=leg_symbol, target_isin=leg['isin'],
                ratio_new=rn, ratio_old=ro,
                qty_disposed=0.0, qty_received=leg['qty'],
                fmv=leg['val'], target_fmv=leg['val'],
                currency=leg['currency'], target_currency=leg['currency'],
                account=account, raw_descriptions=mb['descriptions'],
                fractional_delivery=True))

    return _collapse_cross_listing_chains(events)


# Elections whose tax deferral is only valid if the user FILES the
# election with their return — consumed by `taxjson run`'s end-of-run
# reminder and the `elect` listing (the option text says this at
# choose time, but a March election is forgotten by filing season).
FILING_REQUIRED_ELECTIONS: Dict[str, str] = {
    'rollover_s_85_1_5': "file the s. 85.1(5) election with your "
                         "return for the exchange year",
    'rollover_s_86_1': "file the s. 86.1 election with your return "
                       "(spinoff must be on CRA's eligibility list)",
    'reorg_368': "attach the Reg. §1.368-3 statement to your return",
    'reorg_368_boot': "attach the Reg. §1.368-3 statement to your "
                      "return",
    'tax_free_355': "attach the Reg. §1.355-5 statement to your "
                    "return",
}

# Universal election available alongside every country/event-type rule.
# Useful for IB's cross-listing replay-noise rows that look like real
# events but aren't (the position never economically changed). Letting
# the user mark them ignored in the manifest is more general than baking
# heuristic filters into the source.
IGNORE_ELECTION = (
    'ignore',
    "Skip this event — taxjson emits nothing, as if it never happened. "
    "ONLY for broker noise (IB duplicate rows, internal sub-account "
    "journals). WARNING: ignoring a REAL merger/spinoff leaves the old "
    "position alive in your books and the new shares with no cost "
    "basis — your taxes will be silently wrong.",
)


def _apply_suffix(symbol: str, suffix: str) -> str:
    """Append `.SUFFIX` unless the symbol already carries one of the
    *known market* suffixes. The old check `if '.' in symbol` was too
    broad — class-share tickers like `BRK.B`, `BRK.A`, `RDS.A` would
    never get a market suffix appended, fragmenting their pool from
    the .TO / .US-suffixed equivalents downstream tools expect."""
    known_suffixes = set(_CURRENCY_SUFFIX.values())  # {'TO','US','AX','L'}
    parts = symbol.rsplit('.', 1)
    if len(parts) == 2 and parts[1] in known_suffixes:
        return symbol
    return f"{symbol}.{suffix}"


def _collapse_cross_listing_chains(events: List[CorporateAction]) -> List[CorporateAction]:
    """Fold IB's cross-listing journals into the upstream merger.

    Pattern (real example):
        Event A: SSL.TO  → RGLD.CAD  (16-for-1, the actual merger)
        Event B: RGLD.CAD → RGLD     (1-for-1, IB's currency-side journal
                                       reported again as Merged(Acquisition))

    The user's economic position is `SSL.TO → RGLD at 16-for-1`. The
    .CAD intermediate is bookkeeping. We walk forward through every
    1-for-1 journal we can find rooted at the current target until
    exhausted — this handles deeper chains (A→B→C→D) without a separate
    pass and without dropping any leg's audit descriptions.

    Match condition for each hop: ratio is 1-for-1, source_symbol of
    the candidate equals current.target_symbol, and target_isin matches
    the upstream event's target_isin (same underlying security across
    the journal — only the currency-side wrapper differs).
    """
    # Index by source_symbol so we can find a chain rooted at any event's
    # target.
    by_src_sym: Dict[str, List[CorporateAction]] = {}
    for ev in events:
        by_src_sym.setdefault(ev.source_symbol, []).append(ev)

    out: List[CorporateAction] = []
    consumed: set = set()
    for ev in events:
        if ev.event_id in consumed:
            continue
        # Walk every applicable 1-for-1 hop downstream of ev's target.
        # Each successful hop bumps `current` forward; descriptions
        # accumulate so the manifest summary preserves the full audit
        # trail. Loop terminates when no further hop matches or when
        # we revisit a target symbol we've already passed through
        # (cycle guard — IB shouldn't ever emit one but a malformed
        # statement could loop A→B→A and the walk would otherwise
        # spin).
        current = ev
        descriptions = list(ev.raw_descriptions)
        visited_targets = {ev.source_symbol, ev.target_symbol}
        while True:
            downstream = by_src_sym.get(current.target_symbol, [])
            chain = next(
                (
                    d for d in downstream
                    if d.event_id != current.event_id
                    and d.event_id not in consumed
                    and d.ratio_new == 1 and d.ratio_old == 1
                    and d.target_isin == current.target_isin
                    and d.target_symbol not in visited_targets
                ),
                None,
            )
            if chain is None:
                break
            consumed.add(chain.event_id)
            descriptions.extend(chain.raw_descriptions)
            visited_targets.add(chain.target_symbol)
            current = chain

        if current is ev:
            out.append(ev)
        else:
            collapsed = CorporateAction(
                date=ev.date, time=ev.time,
                action_type=ev.action_type,
                source_symbol=ev.source_symbol, source_isin=ev.source_isin,
                target_symbol=current.target_symbol,
                target_isin=current.target_isin,
                ratio_new=ev.ratio_new, ratio_old=ev.ratio_old,
                qty_disposed=ev.qty_disposed, qty_received=ev.qty_received,
                fmv=ev.fmv, currency=ev.currency,
                # Inherit target currency AND target_fmv from the *final*
                # hop — that's the market the position ends up in, which
                # drives the suffix and the BUY-leg currency value on
                # the resulting taxjson rows.
                target_currency=current.target_currency,
                target_fmv=current.target_fmv,
                # The journal hops are bookkeeping — delivery semantics
                # and any cash-in-lieu belong to the economic merger and
                # must survive the collapse. Omitting them reverted
                # fractional_delivery to False and re-snapped a genuinely
                # delivered 7.5-share position to 7 + fictitious CIL,
                # regressing the phantom-short fix (dataclass docstring).
                fractional_delivery=(ev.fractional_delivery
                                     or current.fractional_delivery),
                cash_in_lieu=ev.cash_in_lieu or current.cash_in_lieu,
                cash_in_lieu_currency=(ev.cash_in_lieu_currency
                                       or current.cash_in_lieu_currency),
                account=ev.account,
                raw_descriptions=descriptions,
            )
            out.append(collapsed)

    return out


# --- Questrade extractor ---------------------------------------------------


# Questrade encodes corp actions in the "Action=DIS" rows (Activity Type
# = "Dividends"). The Description text carries the structural detail in
# free-form English. A spinoff often spans 2-3 rows that share a symbol:
#   1) "...SPINOFF ON 1000 SHS FROM SEC# J070589 DEFI DEVELOPMENT CORP..."
#      — initial warrant receipt (positive qty)
#   2) "...SPINOFF ... RELEASING AS RIGHTS DIST"
#      — warrant retired pending the rights distribution (negative qty)
#   3) "...PENDING RTS DIST ON 1000 SHS REC..."
#      — rights credited (positive qty)
# Row 3 doesn't mention SPINOFF, so a strict regex would miss it and the
# group would fail to net out correctly. Match either keyword and let
# the netting logic sort it out per-symbol.
_QT_SPINOFF_RE = re.compile(r'\b(SPINOFF|RTS\s+DIST|RIGHTS\s+DIST)\b', re.IGNORECASE)
_QT_PARENT_RE = re.compile(
    r'FROM\s+SEC#\s+(\S+)\s+(.+?)\s+REC\s', re.IGNORECASE,
)
_QT_ON_SHS_RE = re.compile(r'\bON\s+([\d.]+)\s+SHS\b', re.IGNORECASE)
_QT_REC_PAY_RE = re.compile(
    r'\bREC\s+(\S+)\s+PAY\s+(\S+)', re.IGNORECASE)


def _parse_qt_date(s: str) -> str:
    """Questrade date column is `YYYY-MM-DD 12:00:00 AM`; strip the
    time fluff and return ISO date."""
    s = (s or '').split(' ', 1)[0]
    return s


def parse_questrade_corporate_actions(
    csv_path: Path, account: str = 'Questrade',
) -> List[CorporateAction]:
    """Extract spinoff events from a Questrade activity CSV.

    Spinoff bookkeeping in Questrade often spans 2-3 DIS rows because
    they record warrants→rights conversions as intermediate moves. We
    net by (target_symbol, parent_code) so the three-row pattern
    `+100 / -100 / +100` collapses to a single `qty_received=100` event.
    """
    # The parser's own description normalizer: the DIS row names the
    # parent only by Questrade's internal SEC# code plus the company
    # name, and the ticker the parent position is BOOKED under lives on
    # the Trade/Transfer rows describing that same company. Imported
    # lazily — this module is broker-agnostic apart from the extractors.
    from taxjson.lib.brokerages.questrade import (_INTERNAL_CODE_RE,
                                                  _get_desc_key)
    with csv_path.open('r', encoding='utf-8-sig') as f:
        all_rows = list(csv.DictReader(f))

    # company-name key -> (ticker, currency). Trades first (highest-
    # fidelity symbol), transfers fill positions never traded here.
    name_to_symbol: Dict[str, tuple] = {}
    for activity in ('Trades', 'Transfers'):
        for row in all_rows:
            if (row.get('Activity Type') or '').strip() != activity:
                continue
            sym = (row.get('Symbol') or '').strip().lstrip('.')
            sym = re.sub(r'\.TO$', '', sym, flags=re.IGNORECASE)
            key = _get_desc_key(row.get('Description') or '')
            if key and sym and not _INTERNAL_CODE_RE.match(sym):
                name_to_symbol.setdefault(
                    key, (sym, (row.get('Currency') or 'USD').strip()))

    by_target: Dict[tuple, list] = defaultdict(list)
    for row in all_rows:
        action_code = (row.get('Action') or '').strip()
        description = row.get('Description') or ''
        if action_code != 'DIS':
            continue
        if not _QT_SPINOFF_RE.search(description):
            continue

        symbol = (row.get('Symbol') or '').strip()
        try:
            qty = float((row.get('Quantity') or '0').replace(',', ''))
        except ValueError:
            qty = 0.0
        currency = (row.get('Currency') or 'USD').strip()
        date = _parse_qt_date(row.get('Transaction Date', ''))

        # Group rows belonging to the same distribution CHAIN.
        # Keying on target symbol alone broke the real DFDVW case:
        # Questrade's placeholder row carries NO symbol (an event
        # with an EMPTY target — an invalid book row downstream),
        # while its reversal/repost pair carries the real symbol
        # and netted to zero (event skipped). All three rows share
        # the REC/PAY dates and the ON-N-SHS count — that is the
        # chain identity; symbol is only the fallback.
        m_rp = _QT_REC_PAY_RE.search(description)
        m_shs = _QT_ON_SHS_RE.search(description)
        if m_rp:
            key = ('chain', m_rp.group(1), m_rp.group(2),
                   m_shs.group(1) if m_shs else '')
        else:
            key = ('symbol', symbol)
        by_target[key].append({
            'date': date, 'symbol': symbol, 'qty': qty,
            'currency': currency, 'description': description,
        })

    events: List[CorporateAction] = []
    for _key, rows in by_target.items():
        net_qty = sum(r['qty'] for r in rows)
        if net_qty <= 0:
            # Net zero or negative means the position was distributed
            # out, not received — not a spinoff acquisition for the user.
            continue
        # The chain's target symbol: the first row that carries one
        # (placeholder rows don't). Suffix it by currency exactly like
        # the Questrade parser does (USD -> .US, CAD -> .TO) — a bare
        # target emitted book rows on 'DFDVW' while the trades carry
        # 'DFDVW.US', splitting one position across two symbols.
        symbol = next((r['symbol'] for r in rows if r['symbol']), '')
        if symbol and '.' not in symbol:
            _cur = (rows[0]['currency'] or 'USD').upper()
            symbol = f"{symbol}.{'TO' if _cur == 'CAD' else 'US'}"
        if not symbol:
            print(f"warning: Questrade spinoff chain on "
                  f"{min(r['date'] for r in rows)} has NO resolvable "
                  f"target symbol — SKIPPED (an empty-symbol share "
                  f"row would corrupt the books). Add the position "
                  f"manually via a .tt file if it is real: "
                  f"{rows[0]['description'][:90]}", file=sys.stderr)
            continue

        # Extract parent reference from whichever row carries it: the
        # internal SEC# code (kept as source_isin — part of the hashed
        # event id) and the company name, which resolves to the ticker
        # the parent position is booked under. The s. 86.1 ADJUST must
        # hit THAT pool: pointed at the bare code it lands on an empty
        # pool, the parent keeps its full ACB and the spun-off shares
        # carry the allocated basis a second time.
        parent_code = ''
        parent_name = ''
        for r in rows:
            m = _QT_PARENT_RE.search(r['description'])
            if m:
                parent_code = m.group(1).strip()
                parent_name = m.group(2).strip()
                break
        parent_symbol = ''
        hit = name_to_symbol.get(_get_desc_key(parent_name)) \
            if parent_name else None
        if hit:
            parent_symbol = (
                f"{hit[0]}.{'TO' if hit[1].upper() == 'CAD' else 'US'}")
        elif parent_code:
            print(f"warning: Questrade spinoff parent {parent_name or parent_code!r} "
                  f"(SEC# {parent_code}) is not traded or transferred "
                  f"in this statement, so its ticker is unknown — a "
                  f"rollover's parent-ACB reduction would land on an "
                  f"empty {parent_code!r} pool. Include the statement "
                  f"that bought or transferred the parent into this "
                  f"account.", file=sys.stderr)

        # Ratio denominator (the parent share count the user held).
        source_qty = 0.0
        for r in rows:
            m = _QT_ON_SHS_RE.search(r['description'])
            if m:
                source_qty = float(m.group(1))
                break

        event_date = min(r['date'] for r in rows)
        currency = rows[0]['currency']

        events.append(CorporateAction(
            date=event_date, time='09:30:00',
            action_type='spinoff',
            source_symbol=parent_symbol or parent_code or '(unknown parent)',
            source_isin=parent_code,
            target_symbol=symbol, target_isin=symbol,
            ratio_new=net_qty,
            ratio_old=source_qty or 1.0,
            qty_disposed=0.0,
            qty_received=net_qty,
            # Questrade doesn't report FMV on the DIS row. Users supplying
            # `taxable_deemed_dividend` need to provide FMV via the
            # election prompt's hint.
            fmv=0.0,
            currency=currency,
            target_currency=currency,
            account=account,
            raw_descriptions=[r['description'] for r in rows],
        ))

    return events


# --- RBC Direct extractor --------------------------------------------------


# RBC books a merger as two $0-value 'Reorganization' rows:
#   removal:  "MGR - HESS CORPORATION MERGER TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"
#   receipt:  "MGR - CHEVRON CORPORATION SHRS RECEIVED THRU MERGER"
_RBC_MERGER_TO_RE = re.compile(r'\bMERGER\s+TO\s+(.+?)(?:\s+[\d.]+\s+NEW|\s*$)', re.I)
_RBC_RATIO_RE = re.compile(r'([\d.]+)\s+NEW\s*=\s*([\d.]+)\s+OLD', re.I)
_RBC_OLDCO_RE = re.compile(r'^\s*MGR\s*[-:]?\s*(.+?)\s+MERGER\s+TO\b', re.I)
_RBC_RECVCO_RE = re.compile(
    r'^\s*MGR\s*[-:]?\s*(.+?)\s+(?:SHRS|SHARES)\s+RECEIVED', re.I)
_RBC_CO_SUFFIX_RE = re.compile(
    r'\b(CORPORATION|CORP|INCORPORATED|INC|LTD|LIMITED|COMPANY|CO|PLC|SA|NV|AG|'
    r'HOLDINGS|GROUP)\b', re.I)
_RBC_CA_DATE_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%B %d, %Y", "%m/%d/%Y")
# A temporary reorganization placeholder RBC assigns while a security is
# mid-merger (e.g. 'H015283' for HESS). It is NOT the ticker the position
# is actually held under — the removal leg must be resolved back to the
# real ticker (via the shared company name) before the rollover SPLIT /
# disposition can find the source lot.
_RBC_TEMP_SYMBOL_RE = re.compile(r'^[A-Z]\d{4,}$')


def is_rbc_merger_row(activity: str, description: str) -> bool:
    """True for an RBC merger removal/receipt row — the ones taxjson-corp-actions
    owns, so the brokerage parser must NOT also emit them as $0 trades. Option
    expiries/assignments are also 'Reorganization' rows but never match these
    merger phrases."""
    d = (description or '').upper()
    if 'Reorganization' not in (activity or '') and 'MGR' not in d:
        return False
    return ('MERGER TO' in d) or ('RECEIVED THRU MERGER' in d) \
        or ('SHRS RECEIVED' in d and 'MERGER' in d)


_RBC_CIL_WORD_RE = re.compile(r'\bCIL\b')


def is_rbc_cil_row(activity: str, description: str) -> bool:
    """True for an RBC cash-in-lieu-of-fractional-shares 'Reorganization'
    row (`CIL - ... CASH IN LIEU OF FRAC SHARES`, and its `ADDITIONAL CIL
    PAYMENT` follow-up). These are the cash settlement of the fractional
    share a merger ratio leaves over — taxjson-corp-actions folds them
    into the merger event, so the brokerage parser must skip them instead
    of emitting a bogus 0-quantity trade.

    Matching is deliberately strict: 'CIL' must be a WHOLE WORD (the old
    substring test matched FACILITIES/COUNCIL/CECIL — silently dropping
    every buy/sell/dividend row of e.g. MEDICAL FACILITIES CORP into a
    phantom cash-in-lieu bucket), and outside a Reorganization activity the
    row must actually say CASH IN LIEU."""
    d = (description or '').upper()
    is_reorg = 'Reorganization' in (activity or '')
    if 'CASH IN LIEU' in d:
        return True
    if not is_reorg:
        # A non-reorg row is only CIL when it explicitly says so (above).
        return False
    return bool(_RBC_CIL_WORD_RE.search(d)) and 'MERGER' not in d \
        and 'RECEIVED' not in d


def _rbc_is_real_ticker(symbol: str) -> bool:
    """Whether `symbol` is a genuine exchange ticker rather than a
    temporary reorg placeholder (`H015283`) or an empty/cash-row blank.
    A real ticker carries letters and doesn't match the letter+digits
    reorg-code shape."""
    s = (symbol or '').strip().upper()
    if not s or _RBC_TEMP_SYMBOL_RE.match(s):
        return False
    return any(c.isalpha() for c in s)


def _rbc_norm_company(name: str) -> str:
    return re.sub(r'[^A-Z0-9]', '',
                  _RBC_CO_SUFFIX_RE.sub('', (name or '').upper()))


def _rbc_ca_symbol(symbol: str, currency: str) -> str:
    """Match the brokerage parser's symbol shape: strip any market suffix,
    spaces→dots, append the currency-derived suffix (CVX/USD → CVX.US)."""
    sym = re.sub(r'\.(US|TO|AX|L)$', '', (symbol or '').strip().replace(' ', '.'),
                 flags=re.I)
    return f"{sym}.{_CURRENCY_SUFFIX.get((currency or '').upper(), 'US')}"


def _rbc_ca_date(s: str) -> str:
    s = (s or '').strip()
    for fmt in _RBC_CA_DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s[:10]


def parse_rbc_corporate_actions(
    csv_path: Path, account: str = 'RBC',
) -> List[CorporateAction]:
    """Extract merger events from an RBC Direct Investing activity CSV.

    RBC fragments a merger across two $0-value 'Reorganization' rows — the old
    shares removed (often under a temporary symbol) with a "<OLDCO> MERGER TO
    <NEWCO> <ratio> NEW = <n> OLD" description, and the acquirer's shares
    received with "<NEWCO> SHRS RECEIVED THRU MERGER". This pairs them into one
    `merger` CorporateAction so the election machinery (taxable vs s.85.1(5)
    rollover, fractional-share snap) can resolve it correctly.

    RBC reports no FMV and no ISIN; FMV comes from the election hint when the
    user picks a taxable disposition, and the symbols stand in for the ISINs in
    the event-id hash (stable + unique across re-runs)."""
    lines = csv_path.read_text(encoding='utf-8-sig').splitlines()
    start = 0
    for i, line in enumerate(lines):
        if 'Activity' in line and 'Date' in line:
            start = i
            break
    rows = list(csv.DictReader(lines[start:]))

    # Company name → the real ticker it trades under. RBC books a merger
    # removal under a temporary reorg placeholder (e.g. 'H015283' for
    # HESS); the same security's dividend / trade rows carry the real
    # ticker ('HES'). Learning this from the shared 'Symbol Description'
    # lets us resolve the removal back to the ticker the position is
    # actually held under, so the rollover SPLIT / disposition lands on
    # the real lot instead of an empty temp-symbol pool.
    name_to_symbol: Dict[str, str] = {}
    for row in rows:
        if not row:
            continue
        sym = (row.get('Symbol') or '').strip()
        name = _rbc_norm_company(row.get('Symbol Description') or '')
        if sym and name and _rbc_is_real_ticker(sym):
            name_to_symbol.setdefault(name, sym)

    # Cash-in-lieu of fractional shares, keyed by the acquirer company so
    # each bucket can be folded into its merger event as the fractional
    # disposition (see _canada_merger_taxable / _rollover).
    cil_by_company: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not row:
            continue
        if not is_rbc_cil_row(row.get('Activity') or '', row.get('Description') or ''):
            continue
        company = _rbc_norm_company(
            row.get('Symbol Description') or row.get('Symbol') or '')
        amount = abs(_f(row.get('Value') or row.get('Amount')))
        if not company or amount <= 0:
            continue
        bucket = cil_by_company.setdefault(company, {
            'amount': 0.0,
            'currency': (row.get('Currency') or 'USD').strip(),
            'descs': [],
        })
        bucket['amount'] += amount
        bucket['descs'].append((row.get('Description') or '').strip())

    removals, receipts = [], []
    for row in rows:
        if not row:
            continue
        activity = row.get('Activity') or ''
        desc = row.get('Description') or ''
        if not is_rbc_merger_row(activity, desc):
            continue
        rec = {
            'symbol': (row.get('Symbol') or '').strip(),
            'currency': (row.get('Currency') or 'USD').strip(),
            'date': _rbc_ca_date(row.get('Date') or ''),
            'qty': _f(row.get('Quantity')),
            'desc': desc.strip(),
        }
        if rec['qty'] < 0 and _RBC_MERGER_TO_RE.search(desc):
            removals.append(rec)
        elif rec['qty'] > 0 and re.search(r'RECEIVED', desc, re.I):
            receipts.append(rec)

    def _days_apart(d1: str, d2: str) -> int:
        try:
            return abs((datetime.strptime(d1, '%Y-%m-%d')
                        - datetime.strptime(d2, '%Y-%m-%d')).days)
        except ValueError:
            return 9999

    events: List[CorporateAction] = []
    used: set = set()
    for rem in removals:
        m = _RBC_MERGER_TO_RE.search(rem['desc'])
        target = _rbc_norm_company(m.group(1)) if m else ''
        # RBC books the removal and receipt of one reorg on DIFFERENT dates
        # for some events, so exact-date matching silently vanished both
        # legs (the raw rows were already claimed by is_rbc_merger_row).
        # Allow a ±7-day window, preferring the closest date.
        same = sorted(
            ((i, rc) for i, rc in enumerate(receipts)
             if i not in used and _days_apart(rc['date'], rem['date']) <= 7),
            key=lambda ir: _days_apart(ir[1]['date'], rem['date']))
        pick = None
        for i, rc in same:                                  # 1) company match
            rcm = _RBC_RECVCO_RE.search(rc['desc'])
            rcco = _rbc_norm_company(rcm.group(1) if rcm else rc['symbol'])
            if target and rcco and (target in rcco or rcco in target):
                pick = (i, rc)
                break
        if pick is None and len(same) == 1:                 # 2) lone fallback
            pick = same[0]
        if pick is None:
            # An unmatched removal means shares silently vanish from
            # inventory — say so instead of a bare continue.
            oldm = _RBC_OLDCO_RE.search(rem['desc'])
            print(
                f"warning: RBC merger removal on {rem['date']} "
                f"({(oldm.group(1).strip() if oldm else rem['symbol'])!r}, "
                f"qty {rem['qty']:g}) has NO matching share receipt within "
                f"7 days — the event was skipped and these shares will "
                f"disappear from inventory. Check the statement covers the "
                f"receipt row, or add the event manually.",
                file=sys.stderr,
            )
            continue
        i, rc = pick
        used.add(i)
        rr = _RBC_RATIO_RE.search(rem['desc'])
        ratio_new = float(rr.group(1)) if rr else 1.0
        ratio_old = float(rr.group(2)) if rr and float(rr.group(2)) else 1.0
        oldm = _RBC_OLDCO_RE.search(rem['desc'])
        rcm = _RBC_RECVCO_RE.search(rc['desc'])
        # Resolve a temporary reorg placeholder (H015283) back to the real
        # ticker via the removal's company name; warn + keep the placeholder
        # if nothing else in the statement trades under that company.
        src_raw = rem['symbol']
        if not _rbc_is_real_ticker(src_raw):
            oldco = _rbc_norm_company(oldm.group(1)) if oldm else ''
            resolved = name_to_symbol.get(oldco)
            if resolved:
                src_raw = resolved
            else:
                print(
                    f"warning: RBC merger removal for "
                    f"{(oldm.group(1).strip() if oldm else rem['symbol'])!r} is "
                    f"booked under temporary reorg symbol {rem['symbol']!r}, and "
                    f"no other row in the statement trades under that company — "
                    f"the rollover / disposition has no source lot to act on. If "
                    f"this position isn't in the imported history, add a manual "
                    f"opening lot or a ticker.map GLOBAL line for "
                    f"{_rbc_ca_symbol(rem['symbol'], rem['currency'])}.",
                    file=sys.stderr,
                )
        src = _rbc_ca_symbol(src_raw, rem['currency'])
        tgt = _rbc_ca_symbol(rc['symbol'], rc['currency'])
        recv_co = _rbc_norm_company(rcm.group(1) if rcm else rc['symbol'])
        cil = cil_by_company.get(target) or cil_by_company.get(recv_co) or {}
        events.append(CorporateAction(
            date=rem['date'], time='09:30:00', action_type='merger',
            source_symbol=src, source_isin=src,
            target_symbol=tgt, target_isin=tgt,
            ratio_new=ratio_new, ratio_old=ratio_old,
            qty_disposed=abs(rem['qty']), qty_received=rc['qty'],
            fmv=0.0, currency=rem['currency'], target_currency=rc['currency'],
            account=account,
            cash_in_lieu=cil.get('amount', 0.0),
            cash_in_lieu_currency=cil.get('currency', ''),
            raw_descriptions=[rem['desc'], rc['desc']] + cil.get('descs', []),
        ))
    events.sort(key=lambda e: (e.date, e.source_symbol))
    return events


# --- Election manifest -----------------------------------------------------


@dataclass
class ElectionRecord:
    event_id: str
    summary: str            # human-readable, for audit/sanity at read time
    election: str           # the choice key from the rule's OPTIONS list
    notes: str = ""
    # Option-specific extra inputs. E.g. spinoff `taxable_deemed_dividend`
    # carries `{'fmv_per_share': 0.5}`; `rollover_s_86_1` carries
    # `{'allocated_acb': 1234.5}`. Empty when the election needs no
    # additional input.
    hints: Dict[str, Any] = field(default_factory=dict)


class Manifest:
    """JSON-backed elections store.

    Layout on disk:
        {
          "elections": {
            "<event_id>": {
              "summary": "...",
              "election": "taxable_disposition",
              "notes": "...",
              "hints": {"fmv_per_share": 12.5}   # when the election
            }                                     # needed extra input
          }
        }
    """

    def __init__(self, records: Optional[Dict[str, ElectionRecord]] = None):
        self.records = records or {}

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls({})
        raw = path.read_text(encoding='utf-8').strip()
        if not raw:
            # Empty file behaves like a missing one — typical when a
            # previous run created the file before any election was saved.
            return cls({})
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Common cause: argparse picked up the wrong file (e.g. CSV
            # passed where the manifest belongs). Surface the actual path
            # so the user can fix the invocation instead of staring at a
            # raw Python traceback.
            raise ValueError(
                f"manifest at {path} is not valid JSON ({exc.msg} at line "
                f"{exc.lineno} col {exc.colno}). If this file used to be a "
                f"CSV or other format, point --manifest at the correct path "
                f"or delete the file to start fresh."
            ) from None
        if not isinstance(data, dict):
            raise ValueError(
                f"manifest at {path} must be a JSON object with an "
                f"'elections' key; got top-level {type(data).__name__}"
            )
        records = {}
        for eid, rec in (data.get('elections') or {}).items():
            records[eid] = ElectionRecord(
                event_id=eid,
                summary=rec.get('summary', ''),
                election=rec.get('election', ''),
                notes=rec.get('notes', ''),
                hints=dict(rec.get('hints') or {}),
            )
        return cls(records)

    def save(self, path: Path) -> None:
        payload = {
            'elections': {
                eid: {
                    'summary': r.summary,
                    'election': r.election,
                    'notes': r.notes,
                    # Only persist hints when set so the file stays clean
                    # for elections (like `ignore`) that have no extras.
                    **({'hints': r.hints} if r.hints else {}),
                }
                for eid, r in sorted(self.records.items())
            }
        }
        # Atomic write: serialize to a sibling tempfile, then rename
        # into place. A previous Ctrl-C mid-`write_text` would leave a
        # truncated JSON file that fails to reload on the next run,
        # losing every prior election. `os.replace` is atomic on POSIX
        # and on Windows (Python 3.3+).
        import os
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix='.tmp', dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            os.replace(tmp_path, path)
        except Exception:
            # Best-effort cleanup of the tempfile if the rename failed.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def migrate_legacy(self, events: List["CorporateAction"]) -> int:
        """Rekey records saved under the old opaque 12-hex ids to the
        current human-legible ids. Returns the number migrated (caller
        saves when > 0). Elections are the non-rebuildable user
        artifact — an id-scheme change must never orphan them."""
        migrated = 0
        for ev in events:
            if ev.event_id in self.records:
                continue
            legacy = ev.legacy_event_id()
            rec = self.records.pop(legacy, None)
            if rec is None:
                rec = self._pop_resymbolled(ev)
            if rec is None:
                continue
            rec.event_id = ev.event_id
            self.records[ev.event_id] = rec
            migrated += 1
        return migrated

    def _pop_resymbolled(self, ev: "CorporateAction") -> Optional[ElectionRecord]:
        """A record whose id differs from `ev`'s only in the readable
        symbol roots. The date prefix and the 4-hex suffix (hashed from
        ISINs, ratio and account) are the event's identity; the roots
        are display only, and an extractor that learns a better ticker
        (a broker-internal parent code resolved to the traded symbol)
        changes them. Requires exactly one candidate."""
        date, _src, _tgt, suffix = ev.event_id.split('-', 3) \
            if ev.event_id.count('-') == 3 else ('', '', '', '')
        if not date or not suffix:
            return None
        hits = [eid for eid in self.records
                if eid.count('-') == 3
                and eid.startswith(f"{date}-") and eid.endswith(f"-{suffix}")]
        if len(hits) != 1:
            return None
        return self.records.pop(hits[0])

    def get(self, event_id: str) -> Optional[ElectionRecord]:
        return self.records.get(event_id)

    def set(self, record: ElectionRecord) -> None:
        self.records[record.event_id] = record


# --- Country rules ---------------------------------------------------------


# Each rule entry: list of `(option_key, human_description)` tuples and an
# `apply(event, option_key, hints) -> List[dict]` function. `hints` is a
# pass-through dict for option-specific inputs (e.g. fractional-cash spot
# price); resolver tools can leave it empty for simple cases.
@dataclass
class RuleSpec:
    options: List[tuple]  # (key, description)
    apply: Callable[[CorporateAction, str, dict], List[dict]]
    # When set, the resolver applies this election WITHOUT prompting —
    # for event types with exactly one sane treatment (name changes).
    # A manifest record is still written (audit trail intact; `taxjson
    # elect --redo` can override it like any other election).
    auto_default: Optional[str] = None


def _snap_qty_to_whole_shares(
    qty_received: float, total_fmv: float
) -> Tuple[float, float, float]:
    """Snap a corp-action's `qty_received` DOWN to the whole-share
    count and reduce its total FMV proportionally — mimicking the
    broker's "cash-in-lieu for fractional shares" treatment that real
    statements ship for mergers / spinoffs whose ratio doesn't divide
    cleanly into the user's source holding. (We floor, not round: the
    user keeps N whole shares and gets cash for the leftover fraction.)

    Without this, an SSL.TO 1-for-16 merger applied to a holding that
    isn't a multiple of 16 would leave a phantom 0.00XX RGLD.US dust
    position in the inventory forever — the engine has no other path
    to nuke it (the user already DELETEs the broker's cash-in-lieu
    rows via `ticker.map`, but those rows are on the source side; the
    target-side residue persists).

    Per-share basis is preserved: `(whole / qty_abs) * total_fmv /
    whole == total_fmv / qty_abs`. The "missing" value
    (`frac * per_share_fmv`) is the implicit cash-in-lieu, which has
    no incremental gain/loss vs the merger date (cost basis == FMV).

    Returns `(whole_qty, adjusted_total_fmv, frac_qty)`. A caller
    sees `frac_qty == 0` when no snapping was needed. When the entire
    position is fractional (`whole_qty == 0`), the caller should
    typically skip emitting any BUY row — the user received only
    cash, not shares; the source disposition already captures it."""
    qty_abs = abs(qty_received)
    if qty_abs < 1e-9:
        return 0.0, total_fmv, 0.0
    # Treat a quantity within float-noise of a whole number as clean.
    # `qty_received` is computed (qty_disposed * ratio_new / ratio_old)
    # and rarely lands exactly on an integer, so guard BOTH sides of the
    # nearest whole — a bare `int()` floor would snap a 99.99999998 that
    # should be 100 down to 99 and emit a spurious 0.9999 cash-in-lieu.
    nearest = round(qty_abs)
    if abs(qty_abs - nearest) < 1e-6:
        return float(nearest), total_fmv, 0.0
    whole = float(int(qty_abs))
    frac = qty_abs - whole
    if whole == 0.0:
        # Entire position settles as cash-in-lieu.
        return 0.0, 0.0, frac
    return whole, (whole / qty_abs) * total_fmv, frac


# Residue smaller than this is broker dust (e.g. IB's 100.0026 that a
# later journal moves as 100) — always snapped. Anything bigger on a
# fractional-delivery broker is a REAL position the user can sell.
_FRACTIONAL_DUST = 0.01


def _snap_received(event: CorporateAction, qty_received: float,
                   total_fmv: float) -> Tuple[float, float, float]:
    """Broker-aware wrapper around _snap_qty_to_whole_shares: on a
    fractional-delivery broker (event.fractional_delivery), only dust
    is snapped — the delivered fraction stays in the book."""
    if getattr(event, 'fractional_delivery', False):
        import math
        qty_abs = abs(qty_received)
        frac = qty_abs - math.floor(qty_abs + 1e-9)
        if frac >= _FRACTIONAL_DUST:
            return qty_received, total_fmv, 0.0
    return _snap_qty_to_whole_shares(qty_received, total_fmv)


def _emit_taxable_exchange(event: CorporateAction, hints: dict,
                           *, description_base: str) -> List[dict]:
    """Country-neutral taxable exchange: sell source at FMV, buy target at
    FMV. Realizes a capital gain/loss against the source's existing pool.
    Canada wraps this as the no-election merger default; the US wraps it
    as the fully-taxable §1001 exchange — only the description differs.

    Cross-currency mergers (e.g. SSL.TO CAD → RGLD.US USD) need the BUY
    leg expressed in the target market's currency: the new lot's cost
    basis is the USD market value at acquisition, not the source-side
    CAD figure. We use `event.target_fmv` + `event.target_currency` for
    the BUY when both are available, falling back to the source-side
    figures for back-compat with older data that doesn't carry them.
    """
    # RBC books both merger legs at $0 (fmv=0): without a value the SELL
    # realizes a fake full-ACB loss, the BUY enters at $0 basis (double
    # taxation later), and the cash-in-lieu vanishes. When the broker gave
    # no FMV, value the consideration from the user's fmv_per_share hint
    # (per NEW share) and include cash-in-lieu in the proceeds. Broker-
    # reported FMVs (IB) take precedence — the hint is only a fallback.
    src_fmv = event.fmv
    tgt_fmv = event.target_fmv
    hint_ps = float((hints or {}).get('fmv_per_share') or 0.0)
    cil_amt = float(getattr(event, 'cash_in_lieu', 0.0) or 0.0)
    if tgt_fmv <= 0 and hint_ps > 0:
        tgt_fmv = hint_ps * event.qty_received
    if src_fmv <= 0:
        # Proceeds of the old shares = value of what was received for them
        # (new shares + cash-in-lieu).
        src_fmv = max(tgt_fmv, 0.0) + cil_amt
    if src_fmv <= 0:
        print(
            f"warning: taxable merger {event.source_symbol}→"
            f"{event.target_symbol} on {event.date} has NO fair market "
            f"value (broker booked $0 and no fmv_per_share hint was "
            f"given) — emitting zero-valued rows: the SELL realizes a "
            f"fake full-ACB loss and the BUY enters at $0 basis. Re-run "
            f"`taxjson elect --redo` and supply the FMV.",
            file=sys.stderr,
        )

    price_disposed = src_fmv / event.qty_disposed if event.qty_disposed else 0.0
    # Prefer the parser-reported target-side FMV when present (that's
    # the IN-leg value in its native currency); fall back to the source
    # figure only when target_fmv is missing.
    target_currency = event.target_currency or event.currency
    fmv_acquired = tgt_fmv if tgt_fmv > 0 else max(src_fmv - cil_amt, 0.0)

    # Snap fractional residue to broker-style cash-in-lieu (see helper).
    whole_qty, fmv_acquired, frac_qty = _snap_received(event,
        event.qty_received, fmv_acquired,
    )
    price_acquired = fmv_acquired / whole_qty if whole_qty else 0.0
    description = description_base
    if frac_qty > 0:
        description += f"; cash-in-lieu for {frac_qty:.6g} fractional share(s)"

    rows = [
        {
            'action': 'BUYSELL',
            'date': event.date, 'time': event.time, 'date_settle': event.date,
            'symbol': event.source_symbol,
            'quantity': -abs(event.qty_disposed),
            'currency': event.currency,
            'price': price_disposed,
            'net_amount': src_fmv,
            'fee': 0.0,
            'account': event.account,
            'description': description,
        },
    ]
    # Emit the target BUY only when at least one whole share was
    # received. A 100%-fractional merger (rare: tiny source holding
    # below the ratio's reciprocal) settles entirely as cash; the
    # source SELL already captures the disposition.
    if whole_qty > 0:
        rows.append({
            'action': 'BUYSELL',
            'date': event.date,
            # Offset by one second so the buy sorts after the sell (the
            # sort stage tie-breaks on time; if they collide the gain
            # engine could match the new lot against itself).
            'time': _bump_time(event.time, 1),
            'date_settle': event.date,
            'symbol': event.target_symbol,
            'quantity': whole_qty,
            'currency': target_currency,
            'price': price_acquired,
            'net_amount': fmv_acquired,
            'fee': 0.0,
            'account': event.account,
            'description': description,
        })
    return rows


def _canada_merger_taxable(event: CorporateAction, option: str, hints: dict) -> List[dict]:
    """Canada wrapper: no-election merger default (sell at FMV, buy at FMV)."""
    return _emit_taxable_exchange(
        event, hints,
        description_base=(
            f"Merger {event.source_symbol}→{event.target_symbol} "
            f"(taxable disposition; no CRA election filed)"
        ),
    )


def _emit_basis_carryover_rename(event: CorporateAction, hints: dict,
                                 *, statute_note: str,
                                 cil_note: str) -> List[dict]:
    """Country-neutral basis-carryover rename: target inherits source's
    cost basis, modeled as a SPLIT — the engine renames the pool and
    preserves total cost while scaling the share count (which also
    preserves lot acquisition dates, so US holding-period tacking falls
    out for free). Canada wraps this as the s. 85.1(5) rollover; the US
    as the §368(a) tax-free reorganization.

    Share count: we scale by the EMPIRICAL `qty_received / qty_disposed`
    rather than the nominal `ratio_new/ratio_old`. When a broker snaps a
    fractional entitlement to whole shares plus cash-in-lieu (RBC delivers
    15 whole CVX for a 1.025-for-1 merger on 15 HES, not 15.375), the
    nominal ratio would leave a phantom 0.375-share dust position the
    engine can never clear. The empirical factor lands the pool on exactly
    the shares the broker delivered.

    Cash-in-lieu: when the broker DID report cash for the fractional
    (`event.cash_in_lieu`), we instead scale by the nominal ratio so the
    fractional share exists transiently, then dispose it at the cash-in-
    lieu proceeds — a small boot gain, with the remaining whole-share
    basis apportioned by the ACB engine. Both legs share the merger date
    so the position nets to the broker's whole-share count.
    """
    disposed = event.qty_disposed
    entitlement = disposed * event.ratio
    frac = entitlement - event.qty_received
    cash_in_lieu = event.cash_in_lieu
    settle_frac = cash_in_lieu > 0 and frac > 1e-6

    if settle_frac:
        # Keep the fractional so the cash-in-lieu SELL can retire it.
        split_qty = event.ratio
    elif disposed:
        # Land on the broker's actual delivered share count (kills dust).
        split_qty = event.qty_received / disposed
    else:
        split_qty = event.ratio

    rows = [
        {
            'action': 'SPLIT',
            'date': event.date, 'time': event.time, 'date_settle': event.date,
            'symbol': event.source_symbol,
            'symbol_new': event.target_symbol,
            'quantity': split_qty,
            'currency': event.currency,
            'account': event.account,
            'description': (
                f"Merger {event.source_symbol}→{event.target_symbol} "
                f"({statute_note}; {event.qty_received:.6g} shares "
                f"received per {disposed:.6g} disposed)"
            ),
        }
    ]
    if settle_frac:
        cil_currency = (event.cash_in_lieu_currency
                        or event.target_currency or event.currency)
        rows.append({
            'action': 'BUYSELL',
            'date': event.date,
            # After the SPLIT so the fractional lot exists to sell against.
            'time': _bump_time(event.time, 1),
            'date_settle': event.date,
            'symbol': event.target_symbol,
            'quantity': -frac,
            'currency': cil_currency,
            'price': cash_in_lieu / frac,
            'net_amount': cash_in_lieu,
            'fee': 0.0,
            'account': event.account,
            'description': (
                f"Merger {event.source_symbol}→{event.target_symbol}: "
                f"cash-in-lieu for {frac:.6g} fractional share(s) "
                f"({cil_note})"
            ),
        })
    return rows


def _canada_merger_rollover(event: CorporateAction, option: str, hints: dict) -> List[dict]:
    """Canada wrapper: s. 85.1(5) cross-border share-for-share rollover.
    The user must actually file the election with their return; this is
    purely the accounting side."""
    return _emit_basis_carryover_rename(
        event, hints,
        statute_note="s. 85.1(5) rollover elected",
        cil_note="s. 85.1(5) rollover",
    )


def _bump_time(t: str, secs: int) -> str:
    """Add `secs` seconds to an HH:MM:SS string with wraparound clamping
    at 23:59:59. We only need 1-second bumps for sort tie-breaking, so
    over-engineering minute/hour overflow isn't worth it here."""
    try:
        h, m, s = (int(x) for x in t.split(':'))
    except (ValueError, AttributeError):
        return t
    total = h * 3600 + m * 60 + s + secs
    if total >= 86400:
        return "23:59:59"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


CANADA_MERGER = RuleSpec(
    options=[
        (
            'taxable_disposition',
            "Report the merger as a sale — the treatment that applies "
            "if you file nothing with CRA. Realizes your capital "
            "gain/loss on the old shares THIS year; the new shares "
            "start at FMV cost. No paperwork.",
        ),
        (
            'rollover_s_85_1_5',
            "Defer the gain (s. 85.1(5) cross-border share-for-share "
            "rollover): the new shares inherit your old cost basis, so "
            "no gain this year — you pay when you sell them. You MUST "
            "file the s. 85.1(5) election with your return for the "
            "exchange year (CRA can deny it, e.g. if you received "
            "cash beyond a fractional-share payout).",
        ),
    ],
    apply=lambda ev, opt, hints: (
        _canada_merger_rollover(ev, opt, hints)
        if opt == 'rollover_s_85_1_5'
        else _canada_merger_taxable(ev, opt, hints)
    ),
)


def _emit_boot_exchange(event: CorporateAction, hints: dict) -> List[dict]:
    """§368(a) reorganization with cash boot (§356(a)(1)): gain is
    recognized to the LESSER of the realized gain or the boot received;
    LOSSES ARE NOT RECOGNIZED (§356(c)). New-share basis per §358(a) =
    old basis − boot + gain recognized.

    Modeled as an engineered SELL/BUY pair so the ENGINE books exactly
    the recognized gain against its own pool:
        SELL source at proceeds = basis + recognized_gain
        BUY  target at cost     = proceeds − boot   (== the §358 basis)
    This needs the user's total pre-merger basis (`source_basis_total`
    hint — read it off `taxjson list` / holdings.toml; it must match the
    engine's pool or the booked gain drifts by the difference). The
    target-side FMV (broker-reported or the `fmv_per_share` hint) is
    used only to compute the realized gain for the min(gain, boot) cap.

    Known limitation, documented: unlike the all-stock §368 path (SPLIT
    rename), the SELL/BUY model RESETS the holding-period start —
    §1223(1) tacking is not preserved. Fractional shares snap to whole
    with proportional basis, like the taxable path."""
    boot = float((hints or {}).get('cash_boot') or 0.0)
    basis = float((hints or {}).get('source_basis_total') or 0.0)
    tgt_fmv = event.target_fmv
    hint_ps = float((hints or {}).get('fmv_per_share') or 0.0)
    if tgt_fmv <= 0 and hint_ps > 0:
        tgt_fmv = hint_ps * event.qty_received

    if boot <= 0:
        print(
            f"warning: boot merger {event.source_symbol}→"
            f"{event.target_symbol} on {event.date} has cash_boot=0 — "
            f"this election degenerates to a basis carryover but RESETS "
            f"holding dates. Elect reorg_368 instead (`taxjson elect "
            f"--redo`).",
            file=sys.stderr,
        )
    if basis <= 0:
        print(
            f"warning: boot merger {event.source_symbol}→"
            f"{event.target_symbol} on {event.date} has no "
            f"source_basis_total hint — recognized gain defaults to the "
            f"full boot and the new basis to $0−boot+gain. Re-run "
            f"`taxjson elect --redo` with your pre-merger basis.",
            file=sys.stderr,
        )

    realized = (max(tgt_fmv, 0.0) + boot) - basis
    recognized = max(0.0, min(realized, boot))   # §356: capped at boot; no losses
    proceeds = basis + recognized
    new_basis = proceeds - boot                  # == basis − boot + recognized

    whole_qty, new_basis, frac_qty = _snap_received(event,
        event.qty_received, new_basis,
    )
    description = (
        f"Merger {event.source_symbol}→{event.target_symbol} "
        f"(§368(a) reorg with §356 cash boot {boot:.2f}; gain recognized "
        f"{recognized:.2f}; §358 basis carried)"
    )
    if frac_qty > 0:
        description += f"; cash-in-lieu for {frac_qty:.6g} fractional share(s)"

    rows = [{
        'action': 'BUYSELL',
        'date': event.date, 'time': event.time, 'date_settle': event.date,
        'symbol': event.source_symbol,
        'quantity': -abs(event.qty_disposed),
        'currency': event.currency,
        'price': (proceeds / event.qty_disposed) if event.qty_disposed else 0.0,
        'net_amount': proceeds,
        'fee': 0.0,
        'account': event.account,
        'description': description + ' (engineered proceeds = basis + recognized gain)',
    }]
    if whole_qty > 0:
        rows.append({
            'action': 'BUYSELL',
            'date': event.date,
            'time': _bump_time(event.time, 1),
            'date_settle': event.date,
            'symbol': event.target_symbol,
            'quantity': whole_qty,
            'currency': event.target_currency or event.currency,
            'price': new_basis / whole_qty,
            'net_amount': new_basis,
            'fee': 0.0,
            'account': event.account,
            'description': description,
        })
    return rows


USA_MERGER = RuleSpec(
    options=[
        (
            'taxable_exchange',
            "Default. Fully taxable exchange (§1001): old shares sold at "
            "FMV, new shares acquired at FMV. Use when the transaction "
            "doesn't qualify as a §368(a) reorganization or you aren't "
            "claiming tax-free treatment.",
        ),
        (
            'reorg_368',
            "§368(a) tax-free reorganization (all-stock): no gain "
            "recognized; basis carries to the new shares (§358) and the "
            "holding period tacks (§1223(1)).",
        ),
        (
            'reorg_368_boot',
            "§368(a) reorganization with CASH BOOT (§356): gain recognized "
            "to the lesser of your realized gain or the cash received; "
            "losses are NOT recognized. New basis = old basis − boot + "
            "gain recognized (§358(a)). You supply the boot and your "
            "total pre-merger basis. Holding dates reset in this model.",
        ),
    ],
    apply=lambda ev, opt, hints: (
        _emit_basis_carryover_rename(
            ev, hints,
            statute_note="§368(a) reorg; §358 basis carryover, "
                         "§1223(1) holding period tacks",
            cil_note="§368(a) reorg")
        if opt == 'reorg_368'
        else _emit_boot_exchange(ev, hints)
        if opt == 'reorg_368_boot'
        else _emit_taxable_exchange(
            ev, hints,
            description_base=(
                f"Merger {ev.source_symbol}→{ev.target_symbol} "
                f"(taxable §1001 exchange; election=none)"
            ))
    ),
)


USA_SPINOFF = RuleSpec(
    options=[
        (
            'taxable_distribution_301',
            "Default. §301 distribution: the spun-off shares are taxable "
            "income at FMV on receipt (a dividend to the extent of "
            "earnings & profits — see your 1099-DIV); cost basis of the "
            "new position = FMV. You supply the per-share FMV.",
        ),
        (
            'tax_free_355',
            "§355 tax-free spinoff: no current income; basis is allocated "
            "between parent and spin-co in proportion to relative FMV "
            "(§358(b)-(c)) — the company's Form 8937 publishes the "
            "allocation. You supply the dollar basis moved to the spin-co.",
        ),
    ],
    apply=lambda ev, opt, hints: (
        _emit_allocated_basis_spinoff(
            ev, hints,
            description_base=(
                f"Spinoff {ev.source_symbol}→{ev.target_symbol} "
                f"(§355 tax-free; §358(b) basis allocated from parent)"
            ))
        if opt == 'tax_free_355'
        else _emit_distribution(
            ev, hints,
            description_base=(
                f"Spinoff {ev.source_symbol}→{ev.target_symbol} "
                f"(§301 taxable distribution at FMV; election=none)"
            ))
    ),
)


def _emit_distribution(event: CorporateAction, hints: dict,
                       *, description_base: str) -> List[dict]:
    """Country-neutral taxable distribution: the received shares are
    income at FMV on the receipt date, and the new position's cost basis
    is that FMV. Canada wraps this as the deemed-dividend spinoff
    default; the US as a §301 distribution. The user supplies
    FMV-per-share via the `fmv_per_share` hint; if absent we emit
    zero-value rows so the pipeline runs and the user can patch the
    numbers later (better than aborting and blocking everything else)."""
    fmv_per_share = float(hints.get('fmv_per_share') or 0.0)
    total_fmv = fmv_per_share * event.qty_received
    # Snap fractional residue to broker-style cash-in-lieu. The
    # DIVIDEND row keeps the full pre-snap income (the user owes tax
    # on what was actually distributed, including the fractional);
    # only the BUY's qty and cost basis snap to whole shares.
    whole_qty, adjusted_fmv, frac_qty = _snap_received(event,
        event.qty_received, total_fmv,
    )
    description = description_base
    if frac_qty > 0:
        description += f"; cash-in-lieu for {frac_qty:.6g} fractional share(s)"
    rows = [
        {
            'action': 'DIVIDEND',
            'date': event.date, 'time': event.time, 'date_settle': event.date,
            'symbol': event.target_symbol,
            'quantity': 0.0, 'currency': event.currency,
            'net_amount': total_fmv, 'gross_amount': total_fmv,
            'type': 'dividend', 'account': event.account,
            'description': description,
        },
    ]
    if whole_qty > 0:
        rows.append({
            'action': 'BUYSELL',
            'date': event.date,
            # Offset by one second so the buy sorts after the dividend
            # and the FMV cost basis is in place before any later sale.
            'time': _bump_time(event.time, 1),
            'date_settle': event.date,
            'symbol': event.target_symbol,
            'quantity': whole_qty, 'currency': event.currency,
            'price': fmv_per_share, 'net_amount': adjusted_fmv, 'fee': 0.0,
            'account': event.account, 'description': description,
        })
    return rows


def _canada_spinoff_deemed_dividend(event: CorporateAction, option: str, hints: dict) -> List[dict]:
    """Canada wrapper: default CRA treatment — foreign dividend at FMV."""
    return _emit_distribution(
        event, hints,
        description_base=(
            f"Spinoff {event.source_symbol}→{event.target_symbol} "
            f"(deemed dividend at FMV; election=none)"
        ),
    )


def _emit_allocated_basis_spinoff(event: CorporateAction, hints: dict,
                                  *, description_base: str) -> List[dict]:
    """Country-neutral basis-allocated spinoff: part of the parent's cost
    basis moves to the spun-off position (via the `allocated_acb` hint);
    no current-year tax. Canada wraps this as the s. 86.1 rollover; the
    US as the §355 tax-free spinoff (basis allocation per §358(b))."""
    allocated_acb = float(hints.get('allocated_acb') or 0.0)
    # Snap fractional residue to broker-style cash-in-lieu. Unlike the
    # taxable / deemed-dividend paths — where the target is acquired at
    # FRESH FMV, so the dropped fraction is genuine zero-gain cash — a
    # rollover CARRIES basis from the parent. Basis must be conserved:
    # the parent's ACB reduction (below) is the basis that actually
    # transferred to the whole-share position (`adjusted_acb`), NOT the
    # full allocated_acb. Reducing by the full amount while the new
    # position only receives `adjusted_acb` would silently vaporize the
    # fractional basis (frac/qty * allocated_acb). Keeping it in the
    # parent defers it rather than losing it.
    whole_qty, adjusted_acb, frac_qty = _snap_received(event,
        event.qty_received, allocated_acb,
    )
    price = adjusted_acb / whole_qty if whole_qty else 0.0
    description = description_base
    if frac_qty > 0:
        description += f"; cash-in-lieu for {frac_qty:.6g} fractional share(s)"
    rows = []
    if whole_qty > 0:
        rows.append({
            'action': 'BUYSELL',
            'date': event.date, 'time': event.time, 'date_settle': event.date,
            'symbol': event.target_symbol,
            'quantity': whole_qty, 'currency': event.currency,
            'price': price, 'net_amount': adjusted_acb, 'fee': 0.0,
            'account': event.account, 'description': description,
        })
    if adjusted_acb > 0 and event.source_symbol and event.source_symbol != '(unknown parent)':
        # Mirror the allocation by reducing the parent ACB. ADJUST rows
        # are how the engine handles non-cash cost-basis tweaks. We
        # reduce by `adjusted_acb` (what moved to the whole-share lot),
        # so total basis is conserved across the parent + spinoff pools.
        rows.append({
            'action': 'ADJUST',
            'date': event.date, 'time': event.time, 'date_settle': event.date,
            'symbol': event.source_symbol, 'currency': event.currency,
            'net_amount': -adjusted_acb,
            'account': event.account,
            'description': description + ' (parent ACB reduction)',
        })
    return rows


def _canada_spinoff_rollover_s_86_1(event: CorporateAction, option: str, hints: dict) -> List[dict]:
    """Canada wrapper: s. 86.1 foreign-spinoff rollover. Only valid if
    the spinoff is on CRA's eligibility list (Income Tax Folio S4-F8-C1)
    AND the user files the election with their return."""
    return _emit_allocated_basis_spinoff(
        event, hints,
        description_base=(
            f"Spinoff {event.source_symbol}→{event.target_symbol} "
            f"(s. 86.1 rollover elected; ACB allocated from parent)"
        ),
    )


CANADA_SPINOFF = RuleSpec(
    options=[
        (
            'taxable_deemed_dividend',
            "Report the received shares as a foreign dividend at FMV — "
            "the treatment that applies if you file nothing with CRA. "
            "Taxable income THIS year; the new shares start at FMV "
            "cost. You supply the per-share FMV (broker statement or "
            "the closing price on the distribution date).",
        ),
        (
            'rollover_s_86_1',
            "Defer the income (s. 86.1 foreign-spinoff rollover): part "
            "of the parent's cost basis moves to the spun-off shares, "
            "no tax this year. Only valid if the spinoff is on CRA's "
            "s. 86.1 eligibility list AND you file the election with "
            "your return. You supply the ACB to allocate (parent ACB — "
            "see `taxjson list` — times the allocation percentage the "
            "company publishes).",
        ),
    ],
    apply=lambda ev, opt, hints: (
        _canada_spinoff_rollover_s_86_1(ev, opt, hints)
        if opt == 'rollover_s_86_1'
        else _canada_spinoff_deemed_dividend(ev, opt, hints)
    ),
)


def _emit_rename(event: CorporateAction, hints: dict) -> List[dict]:
    """Pure ticker/name change: SPLIT with ratio 1.0 and symbol_new — the
    engines' alias machinery renames the pool, carrying cost basis, lot
    acquisition dates, and wash-sale identity for free. No tax event in
    either jurisdiction."""
    return [{
        'action': 'SPLIT',
        'date': event.date, 'time': event.time, 'date_settle': event.date,
        'symbol': event.source_symbol,
        'symbol_new': event.target_symbol,
        'quantity': 1.0,
        'currency': event.currency,
        'account': event.account,
        'description': (
            f"Name/ticker change {event.source_symbol}→"
            f"{event.target_symbol} (no disposition; basis, acquisition "
            f"dates, and wash-sale identity carried)"
        ),
    }]


NAME_CHANGE = RuleSpec(
    options=[
        (
            'rename',
            "Pure ticker/name change — no disposition, no tax event. The "
            "pool is renamed; cost basis, acquisition dates, and wash-sale "
            "identity carry over unchanged.",
        ),
    ],
    apply=lambda ev, opt, hints: _emit_rename(ev, hints),
    auto_default='rename',
)


# Hints prompts surface in the interactive resolver when an option needs
# extra input (FMV, allocated ACB, etc). Keys are (election_key,) and
# values are list of (hint_key, prompt_text). The resolver formats the
# numeric input and stores it on the manifest record's `hints` dict.
HINTS_BY_ELECTION: Dict[str, List[tuple]] = {
    # Each hint is (key, prompt) or (key, prompt, needed(event) -> bool).
    # The optional predicate suppresses the prompt when the event already
    # carries the number (e.g. IB reports merger FMVs; RBC books $0 rows).
    'taxable_disposition': [
        ('fmv_per_share',
         "FMV per NEW share on the merger effective date (values the share "
         "consideration: the old shares' proceeds and the new shares' cost "
         "basis). The broker booked this event at $0, so without it the SELL "
         "realizes a fake full-ACB loss and the BUY enters at $0 basis. "
         "Enter 0 to defer — rows will be zero-valued.",
         lambda ev: (getattr(ev, 'fmv', 0.0) or 0.0) <= 0
         and (getattr(ev, 'target_fmv', 0.0) or 0.0) <= 0),
    ],
    'taxable_deemed_dividend': [
        ('fmv_per_share',
         "FMV per share of the spunoff position on the receipt date "
         "(used to compute the deemed-dividend amount and cost basis). "
         "Enter 0 to defer this number — the pipeline will run but the "
         "rows will be zero-valued."),
    ],
    'rollover_s_86_1': [
        ('allocated_acb',
         "ACB amount (in source currency) allocated from the parent to "
         "the spunoff position. CRA's published spinoff record usually "
         "gives the allocation percentage; multiply by your parent ACB."),
    ],
    # --- US elections -----------------------------------------------------
    'taxable_exchange': [
        ('fmv_per_share',
         "FMV per NEW share on the merger effective date (values the share "
         "consideration: the old shares' proceeds and the new shares' cost "
         "basis). The broker booked this event at $0, so without it the SELL "
         "realizes a fake full-basis loss and the BUY enters at $0 basis. "
         "Enter 0 to defer — rows will be zero-valued.",
         lambda ev: (getattr(ev, 'fmv', 0.0) or 0.0) <= 0
         and (getattr(ev, 'target_fmv', 0.0) or 0.0) <= 0),
    ],
    'reorg_368_boot': [
        ('cash_boot',
         "Total CASH (boot) you received in the exchange, in the target "
         "currency."),
        ('source_basis_total',
         "Your TOTAL cost basis in the old shares immediately before the "
         "merger (see `taxjson list` / holdings.toml). The engine books "
         "gain = engineered proceeds − its own pool basis, so this must "
         "match your books or the recognized gain drifts by the "
         "difference."),
        ('fmv_per_share',
         "FMV per NEW share on the effective date — used only to compute "
         "your realized gain for the min(gain, boot) cap.",
         lambda ev: (getattr(ev, 'target_fmv', 0.0) or 0.0) <= 0),
    ],
    'taxable_distribution_301': [
        ('fmv_per_share',
         "FMV per share of the spun-off position on the receipt date "
         "(sets the taxable amount and the new cost basis). Enter 0 to "
         "defer this number — the pipeline will run but the rows will be "
         "zero-valued."),
    ],
    'tax_free_355': [
        ('allocated_acb',
         "Basis (dollar amount) allocated from the parent to the spun-off "
         "position per §358(b) — the company's Form 8937 publishes the "
         "allocation percentage; multiply by your parent basis."),
    ],
}


RULES_BY_COUNTRY: Dict[str, Dict[str, RuleSpec]] = {
    'canada': {
        'merger': CANADA_MERGER,
        'spinoff': CANADA_SPINOFF,
        'name_change': NAME_CHANGE,
    },
    'usa': {
        'merger': USA_MERGER,
        'spinoff': USA_SPINOFF,
        'name_change': NAME_CHANGE,
    },
}


def apply_auto_defaults(events: List[CorporateAction], manifest: "Manifest",
                        country: str) -> List[CorporateAction]:
    """Write auto-default elections (RuleSpec.auto_default) for any
    unresolved event whose rule declares one. Returns the events that
    were auto-elected. The manifest record is a full audit-trail entry —
    `taxjson elect --redo` overrides it like any hand-made election."""
    applied: List[CorporateAction] = []
    for ev in events:
        if manifest.get(ev.event_id) is not None:
            continue
        rule = RULES_BY_COUNTRY.get(country, {}).get(ev.action_type)
        if rule is None or not rule.auto_default:
            continue
        manifest.set(ElectionRecord(
            event_id=ev.event_id,
            summary=ev.summary(),
            election=rule.auto_default,
            notes='auto-elected (single sane treatment; no decision required)',
        ))
        applied.append(ev)
    return applied


def resolve_event(
    event: CorporateAction,
    election_key: str,
    country: str = 'canada',
    hints: Optional[dict] = None,
) -> List[dict]:
    """Apply a country's rule to a single event with a given election choice.

    `election_key='ignore'` is universal — returns an empty row list,
    skipping the country/event-type rule entirely. Otherwise raises
    KeyError on unknown keys so callers can't silently emit wrong rows.
    """
    if election_key == IGNORE_ELECTION[0]:
        return []
    rule = RULES_BY_COUNTRY[country][event.action_type]
    valid = {key for key, _ in rule.options}
    if election_key not in valid:
        raise KeyError(
            f"unknown election '{election_key}' for {country}/{event.action_type}; "
            f"valid: {sorted(valid | {IGNORE_ELECTION[0]})}"
        )
    rows = rule.apply(event, election_key, hints or {})
    # Traceability: every emitted row names the event (and choice) it
    # came from, so reports can join a gains row back to the manifest
    # record instead of grepping prose descriptions.
    for r in rows:
        r.setdefault('corp_event_id', event.event_id)
        r.setdefault('corp_election', election_key)
    return rows


def options_for(country: str, action_type: str) -> List[tuple]:
    """Return the full list of (key, description) tuples available for a
    given country/event-type, with the universal `ignore` appended. The
    interactive prompter uses this so each event always offers ignore
    alongside the country-specific tax treatments."""
    rule = RULES_BY_COUNTRY.get(country, {}).get(action_type)
    base = list(rule.options) if rule else []
    return base + [IGNORE_ELECTION]
