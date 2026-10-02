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
   - usa/merger: `taxable_exchange`, `reorg_368`, `reorg_368_boot`
   - usa/spinoff: `taxable_distribution_301`, `tax_free_355`
   - both: `rename` (auto-elected)
   `IGNORE_ELECTION` is universal (skips emit for IB-noise rows). Every
   lookup canonicalises the country through lib/country (an alias such
   as "us" works; an unknown country raises instead of offering only
   `ignore`).

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
    # actual fractional (e.g. a 1-for-2 split-up of an odd lot).
    # False (default): unknown broker semantics — snap as before.
    fractional_delivery: bool = False
    # Currency `target_fmv` is denominated in, when it differs from
    # `target_currency`: a cross-listing chain keeps the value of the
    # ECONOMIC merger hop (RGLD.CAD in-leg, CAD) while the position
    # lands on the final hop's listing (RGLD.US, USD). '' = the target
    # currency.
    target_fmv_currency: str = ''
    # The broker account the event was read from (IB statement account,
    # Questrade 'Account #', RBC 'Account'); '' when the export does not
    # say. NOT part of the event id — one election covers the event in
    # every broker account of a taxjson account — but it tells an
    # overlapping statement of ONE broker account (same event, emit once)
    # from a second broker account holding the same security (its
    # shares must be booked too). See `combine_broker_copies`.
    broker_account: str = ''

    def __post_init__(self):
        # The emitters date a second leg one second AFTER the event
        # (`_bump_time`): an event at 23:59:59 (or with no time) put
        # both legs on one second, so a same-symbol exchange's new BUY
        # could pool before the SELL of the old shares (audit S074-02).
        # Leave room for the bump. Not part of the event id.
        self.time = _clamp_time(self.time, _LATEST_EVENT_TIME)
        if not self.event_id:
            self.event_id = self._compute_id()

    @property
    def ratio(self) -> float:
        return self.ratio_new / self.ratio_old if self.ratio_old else 0.0

    def summary(self) -> str:
        if self.action_type == 'unsupported':
            return (f"{self.date} UNSUPPORTED corporate action: "
                    f"{self.source_symbol} -> {self.target_symbol} — "
                    f"taxjson cannot book it; record it by hand in a .tt "
                    f"file, then elect `ignore`")
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

    def _hash(self, n: int, *, account_salted: bool = False) -> str:
        """Hash of the event's identifying fields. The taxjson account
        name is NOT one of them (R1-301): the manifest is stored per
        account, so the name added no uniqueness, and renaming
        [accounts.rrsp] to retireA orphaned every election. Ids made
        before that fix were salted with the account name —
        `account_salted=True` reproduces them for migration."""
        parts = (
            self._normalize_date(self.date),
            self.action_type, self.source_isin, self.target_isin,
            f"{self.ratio_new}-for-{self.ratio_old}",
        ) + ((self.account,) if account_salted else ())
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

    def _compute_id(self, *, account_salted: bool = False) -> str:
        """Human-legible id: `YYYYMMDD-src-tgt-hhhh`, e.g.
        `20251022-ssl-rgld-51d7`. The readable part carries date and
        symbols; the 4-hex suffix (hashed from the FULL identifying
        fields: ISINs, ratio — not the account name) keeps ids unique
        when the readable part collides. Older manifests used the bare
        12-hex hash, or this form salted with the account name —
        `Manifest.migrate_legacy` rekeys both automatically."""
        date = self._normalize_date(self.date).replace('-', '')
        return (f"{date}-{self._sym_root(self.source_symbol)}-"
                f"{self._sym_root(self.target_symbol)}-"
                f"{self._hash(4, account_salted=account_salted)}")

    def account_salted_event_id(self) -> str:
        """The id this event had before R1-301 (4-hex suffix salted
        with the taxjson account name) — the alias every manifest
        written by that scheme is keyed by."""
        return self._compute_id(account_salted=True)

    def legacy_event_id(self) -> str:
        """The pre-2026-07 opaque id (12-hex content hash, which was
        salted with the account name) — kept so existing manifests
        migrate instead of orphaning elections."""
        return self._hash(12, account_salted=True)

    def to_dict(self) -> dict:
        return asdict(self)


# --- IB extractor ----------------------------------------------------------


# Matches an IB "Merged(Acquisition)" Corporate Action description, e.g.:
#   SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (RGLD.CAD, ROYAL GOLD INC, US0000000002)
# The trailing parenthetical block also carries a target ticker (which may be
# the cross-listing intermediate like "RGLD.CAD") that we use to chain the
# subsequent .CAD→.US journal into a single logical event.
#
# Tickers may carry a class-share space ('BRK B') and ratios a decimal
# ('1.025 for 1'); both shapes used to fall through every regex and the
# merger was booked by nobody (2026-09 audit). Spaces become dots, the
# way the statement parser spells the same ticker.
_IB_TICKER = r'[A-Z0-9][A-Z0-9.]*(?:\s[A-Z0-9][A-Z0-9.]*)?'
_IB_NUM = r'\d+(?:\.\d+)?'
_IB_MERGER_RE = re.compile(
    rf'^\s*({_IB_TICKER})\(([A-Z0-9]+)\)\s+Merged\(Acquisition\)\s+WITH\s+'
    rf'([A-Z0-9]+)\s+({_IB_NUM})\s+for\s+({_IB_NUM})\s*'
    rf'\(({_IB_TICKER}),\s*[^,]+,\s*([A-Z0-9]+)\)',
    re.IGNORECASE,
)

# Multi-counterparty variant — a SPLIT-UP / separation, e.g. (synthetic):
#   XYZ(US0000000101) Merged(Acquisition) WITH XYZAV 1 for 2,
#   US0000000102 1 for 2 (XYZA, XYZ AEROSPACE, US0000000103)
# One old share is exchanged for shares of TWO (or more) successor
# companies. IB reports one out-leg (the old position) and one in-leg
# per successor, all under the same description. The single-target
# regex above can't match the comma-separated WITH clause, so these
# events were silently dropped — leaving phantom positions (the old
# symbol never disposed / the successors never acquired).
_IB_MULTI_MERGER_RE = re.compile(
    rf'^\s*({_IB_TICKER})\(([A-Z0-9]+)\)\s+Merged\(Acquisition\)\s+WITH\s+'
    rf'((?:[A-Z0-9.]+\s+{_IB_NUM}\s+for\s+{_IB_NUM})'
    rf'(?:\s*,\s*[A-Z0-9.]+\s+{_IB_NUM}\s+for\s+{_IB_NUM})+)\s*'
    rf'\(({_IB_TICKER}),\s*[^,]+,\s*([A-Z0-9]+)\)',
    re.IGNORECASE,
)
_IB_WITH_PAIR_RE = re.compile(
    rf'([A-Z0-9.]+)\s+({_IB_NUM})\s+for\s+({_IB_NUM})', re.IGNORECASE)

# A cash takeover: the shares are bought out for cash, no new shares.
#   TGT(US0000000555) Merged(Acquisition) FOR USD 30.00 PER SHARE (TGT, TARGET CO, US0000000555)
# A plain disposition at the cash amount — the statement parser books it
# as a sale (ib_extractor, Corporate Actions branch); no election.
_IB_CASH_MERGER_RE = re.compile(
    rf'^\s*({_IB_TICKER})\s*\(([^)]*)\)\s+Merged\([^)]*\)\s+FOR\s+'
    rf'([A-Z]{{3}})\s+([\d,]*\.?\d+)\s+PER\s+SHARE\b', re.IGNORECASE)

# Any other merger-shaped row (a stock + cash offer 'WITH <id> 1 for 2
# AND USD 5.00', an unfamiliar layout): neither booked by the parser nor
# understood here. It becomes an `unsupported` event that stops the run
# until the user books it by hand and marks it `ignore`.
_IB_ANY_MERGER_RE = re.compile(r'\bMerged\(', re.IGNORECASE)

# IB spin-off row (the new shares arrive with a Value, no cash):
#   PARNT(CA0000000777) Spinoff  1 for 5 (SPNCO, SPINCO CORP, CA0000000778)
_IB_SPINOFF_TARGET_RE = re.compile(
    r'\bSpinoff\b.*?\(([A-Z0-9][A-Z0-9 .]*?)\s*,(?:[^,]*,\s*([A-Z0-9]+)\s*\))?',
    re.IGNORECASE)
_IB_SPINOFF_PARENT_RE = re.compile(
    rf'^\s*({_IB_TICKER})\s*\(([A-Z0-9]*)\)\s+Spinoff\b', re.IGNORECASE)
_IB_SPINOFF_RATIO_RE = re.compile(
    rf'\bSpinoff\s+({_IB_NUM})\s+for\s+({_IB_NUM})', re.IGNORECASE)


def _ib_ticker(tok: str) -> str:
    return re.sub(r'\s+', '.', (tok or '').strip())


def ib_spinoff_parts(description: str) -> Optional[Dict[str, Any]]:
    """The parts of an IB Corporate Actions spin-off row, or None when
    `description` is not one. Shared with the statement parser, which
    leaves exactly these rows to taxjson-corp-actions (the spin-off is a
    tax election: a dividend in kind at FMV, or s.86.1) — so the two
    stages can never disagree on who books a row."""
    d = description or ''
    mt = _IB_SPINOFF_TARGET_RE.search(d)
    if not mt:
        return None
    mp = _IB_SPINOFF_PARENT_RE.match(d)
    mr = _IB_SPINOFF_RATIO_RE.search(d)
    return {
        'target': _ib_ticker(mt.group(1)),
        'target_isin': (mt.group(2) or '').strip(),
        'parent': _ib_ticker(mp.group(1)) if mp else '',
        'parent_isin': (mp.group(2) or '').strip() if mp else '',
        'ratio_new': float(mr.group(1)) if mr else 0.0,
        'ratio_old': float(mr.group(2)) if mr else 0.0,
    }


def ib_cash_merger(description: str) -> Optional[Tuple[str, str, float]]:
    """(ticker, currency, cash per share) when `description` is an IB
    cash takeover row, else None."""
    m = _IB_CASH_MERGER_RE.match(description or '')
    if not m:
        return None
    return (_ib_ticker(m.group(1)), m.group(3).upper(),
            _num_text(m.group(4), where=description[:60]))


def ib_merger_owned(description: str) -> bool:
    """True when taxjson-corp-actions takes responsibility for this IB
    Corporate Actions row: a stock merger it books after an election, or
    an unsupported merger shape it turns into a blocking event. Cash
    takeovers and tender journals are the statement parser's."""
    d = description or ''
    if ib_cash_merger(d) or _IB_TENDER_RE.match(d):
        return False
    return bool(_IB_ANY_MERGER_RE.search(d))

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


def _f(x: str, where: str = '') -> float:
    """A numeric cell; blank or '-' is 0. Anything else goes through the
    strict parser: a decimal comma ('1234,56') is REFUSED — stripping
    every comma read it 100x too large."""
    s = (x or '').strip()
    if s in ('', '-'):
        return 0.0
    from taxjson.lib.brokerages.base import parse_strict_number
    return parse_strict_number(s, where=where)


def _num_text(s: str, where: str = '') -> float:
    """A number captured from description text ('1,234.50'): thousands
    commas only; a decimal comma is refused."""
    from taxjson.lib.brokerages.base import check_comma_grouping
    check_comma_grouping(s, where=where)
    return float(s.replace(',', ''))


# The Corporate Actions columns read by name (S072-22).
_IB_CA_COLUMNS = ('Currency', 'Date/Time', 'Description', 'Quantity',
                  'Value')


def _read_ib_corporate_actions(csv_path: Path) -> Dict[str, Any]:
    """The Corporate Actions rows of one IB statement, sorted into merger
    rows, spin-off rows (and their `Ca` cancellations) and merger-shaped
    rows nothing understands. Every row carries the statement's account
    (`stmt`) so rows read from another statement keep their own."""
    rows: List[Dict[str, str]] = []
    # Spin-off rows; `Ca` cancellations of ANY corporate-action row are
    # kept apart and matched against the originals of every statement
    # of the account (`_ib_apply_cancellations`): a cancelled event must
    # not be offered for election.
    spin_rows: List[Dict[str, Any]] = []
    cancels: List[Dict[str, Any]] = []
    # Merger-shaped rows neither regex understands (see _IB_ANY_MERGER_RE).
    odd_rows: List[Dict[str, Any]] = []
    # Financial Instrument Information listing venues, in the statement
    # parser's `fii` shape, so merger legs follow its listing rule.
    fii: Dict[tuple, Dict[str, str]] = {}
    statement_account = ''
    with Path(csv_path).open('r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header_map: Dict[str, int] = {}
        info_header: Dict[str, int] = {}
        fii_header: Dict[str, int] = {}
        for raw_row in reader:
            if (raw_row and raw_row[0] == 'Financial Instrument Information'
                    and len(raw_row) > 1):
                if raw_row[1] == 'Header':
                    fii_header = {c: i for i, c in enumerate(raw_row)}
                elif raw_row[1] == 'Data' and fii_header:
                    def _g(col):
                        i = fii_header.get(col)
                        return (raw_row[i] if i is not None
                                and i < len(raw_row) else '').strip()
                    cat = _g('Asset Category')
                    if cat in ('Stocks', 'Warrants'):
                        for t in _g('Symbol').split(','):
                            t = t.strip()
                            for k in (re.sub(r'\s+', ' ', t),
                                      re.sub(r'\s+', '.', t)):
                                if k:
                                    fii.setdefault(
                                        (cat, k), {'exch': _g('Listing Exch')})
                continue
            if (raw_row and raw_row[0] == 'Account Information'
                    and len(raw_row) > 1):
                if raw_row[1] == 'Header':
                    info_header = {c: i for i, c in enumerate(raw_row)}
                elif raw_row[1] == 'Data' and not statement_account:
                    fn = raw_row[info_header.get('Field Name', 2)] \
                        if info_header.get('Field Name', 2) < len(raw_row) else ''
                    fi = info_header.get('Field Value', 3)
                    if fn == 'Account' and fi < len(raw_row):
                        statement_account = (raw_row[fi].split() or [''])[0]
                continue
            if not raw_row or raw_row[0] != 'Corporate Actions':
                continue
            if raw_row[1] == 'Header':
                header_map = {c: i for i, c in enumerate(raw_row)}
                # Columns by NAME only: the fixed-position fallback fit
                # the single-account layout, and in the consolidated
                # (Account-column) layout it read Report Date as the
                # event date — or matched no merger at all (S072-22).
                missing = [c for c in _IB_CA_COLUMNS if c not in header_map]
                if missing:
                    from taxjson.lib.brokerages.base import \
                        BrokerageParseError
                    raise BrokerageParseError(
                        f"{Path(csv_path).name}: Corporate Actions header "
                        f"is missing column(s) {', '.join(missing)} — "
                        f"refusing to guess them by position")
                continue
            if raw_row[1] != 'Data':
                continue
            if not header_map:
                from taxjson.lib.brokerages.base import BrokerageParseError
                raise BrokerageParseError(
                    f"{Path(csv_path).name}: a Corporate Actions Data row "
                    f"comes before any Corporate Actions Header row — its "
                    f"columns are unknown (IB's two layouts differ); "
                    f"re-download the statement")

            def cell(col, default=None):
                i = header_map.get(col)
                return raw_row[i] if i is not None and i < len(raw_row) else ''
            currency = cell('Currency')
            if currency in ('', 'Total', 'Total in CAD'):
                continue
            desc = cell('Description') or ''
            row_account = cell('Account') if 'Account' in header_map \
                else ''
            # Cancellation rows undo a previous entry: kept apart and
            # matched to their original (same description, negated
            # quantity) across the account's statements. The Code cell
            # is tokenised the way the statement parser does ('Ca;P',
            # 'Ca,P', 'Ca P'): a ';'-only split read 'Ca,P' as a live
            # row (A2-0970).
            code = raw_row[header_map.get('Code', len(raw_row) - 1)] if 'Code' in header_map else ''
            is_cancel = 'Ca' in re.split(r'[;,\s]+', code or '')
            date_time = _ib_norm_date_time(cell('Date/Time'))
            if is_cancel:
                cancels.append({
                    'currency': currency, 'date_time': date_time,
                    'description': desc, 'qty': _f(cell('Quantity')),
                    'value': _f(cell('Value')), 'account': row_account})
                continue
            spin = ib_spinoff_parts(desc)
            if spin is not None:
                spin_rows.append({
                    'currency': currency, 'date_time': date_time,
                    'description': desc, 'qty': _f(cell('Quantity')),
                    'value': _f(cell('Value')), 'parts': spin,
                    'account': row_account})
                continue
            # Tender / voluntary-offer journals are NOT mergers (see
            # _IB_TENDER_RE): the statement parser nets the zero-
            # proceeds round trip and books a cash settlement as a
            # sale. Skipped explicitly so a widened merger regex can
            # never turn the `.TEN` placeholder into an election.
            if _IB_TENDER_RE.match(desc):
                continue
            # A cash takeover is a sale the statement parser books.
            if ib_cash_merger(desc):
                continue
            rec = {
                'currency': currency,
                'date_time': date_time,
                'description': desc,
                'quantity': cell('Quantity'),
                'value': cell('Value'),
                'account': row_account,
            }
            if (_IB_ANY_MERGER_RE.search(desc)
                    and not _IB_MERGER_RE.match(desc.strip())
                    and not _IB_MULTI_MERGER_RE.match(desc.strip())):
                odd_rows.append(rec)
                continue
            rows.append(rec)
    for r in rows + spin_rows + cancels + odd_rows:
        r['stmt'] = statement_account
    return {'rows': rows, 'spin_rows': spin_rows,
            'cancels': cancels, 'odd_rows': odd_rows, 'fii': fii,
            'statement_account': statement_account}


def _ib_leg_symbol(sym: str, currency: str, fii: Dict[tuple, Any],
                   tsx_unit: bool = False) -> str:
    """An IB corporate-action leg's symbol under the statement parser's
    listing rule (`_ib_listing_ext`): a USD unit of a TSX-listed fund
    (QZAA.U) is the Canadian listing X.U.TO, not a fictional `.US`
    security — the currency-only suffix left the real position open and
    disposed of a pool that never existed (A2-0209, A2-0219, the S010-06
    twin). `tsx_unit`: the other leg is such a unit, so a `.U` successor
    with no instrument row of its own is one too."""
    from taxjson.lib.brokerages.ib_extractor import _ib_listing_ext
    ext = _ib_listing_ext('Stocks', sym, currency, fii)
    if (ext != 'TO' and tsx_unit and re.search(r'[.\s]U$', sym or '')
            and (currency or '').upper() == 'USD'):
        ext = 'TO'
    return _apply_suffix(sym, ext)


def _ib_warn_currency_tags(events: List[CorporateAction]) -> None:
    """Say once per symbol when an event's leg is an IB currency/venue
    tagged line (RGLD.CAD): booked as a security of its own, apart from
    the plain listing. The statement parser's R1-59 warning never saw a
    symbol that appears only in a merger or spin-off (A2-0556)."""
    from taxjson.lib.brokerages.ib_extractor import _IB_CURRENCY_TAGS
    seen = set()
    for ev in events:
        for sym in (ev.source_symbol, ev.target_symbol):
            root = (sym or '').rsplit('.', 1)[0]
            if '.' not in root or root in seen:
                continue
            base, tag = root.rsplit('.', 1)
            if tag.upper() not in _IB_CURRENCY_TAGS:
                continue
            seen.add(root)
            print(f"warning: IB corporate action {ev.event_id or ev.date}: "
                  f"symbol {root!r} ends in the currency/venue tag .{tag} "
                  f"— booked as {sym}, a security of its own apart from "
                  f"{base}. If it is the same security, join it in "
                  f"ticker.map (e.g. `GLOBAL {sym} {base}."
                  f"{sym.rsplit('.', 1)[1]}`).", file=sys.stderr)


def _ib_norm_date_time(s: str) -> str:
    """IB's 'YYYY-MM-DD, H:MM:SS' with the hour zero-padded, so the
    raw strings sort and compare as times: '9:30:00' sorted after
    '20:25:00' and a merger took its later leg's time (A2-0984)."""
    m = re.match(r'^(\d{4}-\d{2}-\d{2})(\s*[,;]\s*)(\d{1,2})(:\d{2}.*)$',
                 (s or '').strip())
    if not m:
        return (s or '').strip()
    return f"{m.group(1)}{m.group(2)}{int(m.group(3)):02d}{m.group(4)}"


def _ib_row_ident(r: Dict[str, Any]) -> tuple:
    """One IB Corporate Actions row, as repeated by overlapping
    statements of the same broker account."""
    qty = r['qty'] if 'qty' in r else _f(r.get('quantity'))
    val = r['value'] if isinstance(r.get('value'), float) else _f(
        r.get('value'))
    return (r.get('stmt', ''), r.get('account', ''), r['currency'],
            r['date_time'], r['description'], qty, val)


def _ib_union(own_rows: List[Dict[str, Any]],
              other_rows: List[List[Dict[str, Any]]]
              ) -> List[Dict[str, Any]]:
    """The rows of every statement of the account, each marked `own`
    when this statement holds it. A row the statements repeat
    (overlapping downloads) counts once — the most copies any one
    statement holds."""
    own_ids = {_ib_row_ident(r) for r in own_rows}
    union: Dict[tuple, Tuple[Dict[str, Any], int]] = {}
    for src_rows in [own_rows] + list(other_rows):
        counts: Dict[tuple, int] = defaultdict(int)
        firsts: Dict[tuple, Dict[str, Any]] = {}
        for r in src_rows:
            i = _ib_row_ident(r)
            counts[i] += 1
            firsts.setdefault(i, r)
        for i, n in counts.items():
            if i not in union or union[i][1] < n:
                union[i] = (firsts[i], n)
    out = []
    for i, (r, n) in union.items():
        for _ in range(n):
            out.append(dict(r, own=i in own_ids))
    return out


def _ib_apply_cancellations(cancels: List[Dict[str, Any]],
                            *pools: List[Dict[str, Any]]) -> None:
    """Remove from `pools` (in place) the original of every `Ca` row:
    same description, negated quantity, same row account, dated on or
    before the cancellation (same date preferred, else the latest) —
    the statement parser's `_ca_undo` rule. The cancellation and its
    original may sit in different statements of the account. Only the
    spin-off branch used to pair them, within one statement: a
    cancelled merger, a merger shape taxjson cannot book, or a spin-off
    cancelled in the next statement was still offered for election
    (A2-0018/0019/0020/0068/0069/0212/0220/0971)."""
    def _qty(r):
        return r['qty'] if 'qty' in r else _f(r.get('quantity'))
    for ca in sorted(cancels, key=lambda c: c['date_time']):
        cands = [(pool, r) for pool in pools for r in pool
                 if r['description'] == ca['description']
                 and (r.get('account') or '') == (ca.get('account') or '')
                 and abs(_qty(r) + ca['qty']) < 1e-9
                 and r['date_time'][:10] <= ca['date_time'][:10]]
        if not cands:
            continue
        same = [c for c in cands
                if c[1]['date_time'][:10] == ca['date_time'][:10]]
        pool, r = max(same or cands, key=lambda c: c[1]['date_time'])
        pool.remove(r)


def _ib_dt_days(a: str, b: str) -> int:
    return _rbc_days(a[:10], b[:10])


def parse_ib_corporate_actions(csv_path: Path, account: str = 'IB',
                               context_files: Optional[List[Path]] = None,
                               ) -> List[CorporateAction]:
    """Extract merger events from an IB Activity Statement CSV.

    `account` is stamped onto every emitted CorporateAction so downstream
    tools key the right pool (Margin / RRSP / TFSA / LIRA). Defaults to
    'IB' to keep the historical behaviour for callers that pre-date the
    multi-account refactor.

    `context_files` — every statement of the account (this one
    included). A merger whose out-leg and in-leg sit in two statements
    (a year-end event split across the yearly downloads) is paired
    across them; it used to be dropped from both with no warning (audit
    S020-00). An event is emitted by each statement holding one of its
    legs; `combine_broker_copies` keeps it once.

    Handles two flavours of noise that show up in real statements:

    * `Code=Ca` rows are IB cancellations — each removes its original
      (same description, negated quantity) in whichever statement of
      the account holds it, so a cancelled event is never offered.
    * Cross-listing journals (a CAD-side merger entry immediately followed
      by a 1-for-1 CAD→US "Merged(Acquisition) WITH ..." that's really
      just IB moving the position from the .TO sub-account to the .US one)
      get collapsed into the original SSL→RGLD.US event.
    """
    own = _read_ib_corporate_actions(csv_path)
    statement_account = own['statement_account']
    others: List[Dict[str, Any]] = []
    for other in context_files or []:
        if Path(other).resolve() == Path(csv_path).resolve():
            continue
        try:
            others.append(_read_ib_corporate_actions(Path(other)))
        except (OSError, csv.Error, UnicodeError, ValueError):
            continue

    # Rows of EVERY statement of the account (a merger's legs, a spin-off
    # and its cancellation can sit in two yearly downloads); a row the
    # statements repeat counts once. Each `Ca` row then removes its
    # original wherever it sits, and only events this statement holds a
    # row of are emitted (`combine_broker_copies` keeps one copy).
    def _all(key):
        return _ib_union(own[key], [o[key] for o in others])
    rows, spin_rows, odd_rows = _all('rows'), _all('spin_rows'), \
        _all('odd_rows')
    fii: Dict[tuple, Any] = {}
    for src in [own] + others:
        for k, v in src.get('fii', {}).items():
            fii.setdefault(k, v)
    _ib_apply_cancellations(_all('cancels'), rows, spin_rows, odd_rows)

    def _acct(recs) -> str:
        per_row = sorted({r.get('account') or '' for r in recs} - {''})
        if per_row:
            return ','.join(per_row)
        stmts = sorted({r.get('stmt') or '' for r in recs} - {''})
        return ','.join(stmts) if stmts else statement_account

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
                src_sym, leg_sym = _ib_ticker(src_sym), _ib_ticker(leg_sym)
                mkey = (r['date_time'], src_isin, clause)
                mb = multi_grouped.setdefault(mkey, {
                    'date_time': r['date_time'],
                    'src_sym': src_sym, 'src_isin': src_isin, 'recs': [],
                    'pairs': [(tok, float(rn), float(ro)) for tok, rn, ro
                              in _IB_WITH_PAIR_RE.findall(clause)],
                    'qty_out': 0.0, 'val_out': 0.0,
                    'currency': r['currency'],
                    'in_legs': [], 'descriptions': [],
                })
                mb['descriptions'].append(desc)
                mb['recs'].append(r)
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
        src_sym, tgt_sym = _ib_ticker(src_sym), _ib_ticker(tgt_sym)
        # Cross-listing journals split their two legs across currencies
        # (CAD out, USD in), so currency can't be part of the key — the
        # in/out pair would never reunite. (src_isin, with_isin, date_time)
        # is enough: IB doesn't run two unrelated mergers with the same
        # source on the same timestamp.
        key = (r['date_time'], src_isin, with_isin)
        bucket = grouped.setdefault(key, {
            'date_time': r['date_time'],
            'src_sym': src_sym, 'src_isin': src_isin,
            'with_isin': with_isin,
            'ratio_new': float(ratio_new), 'ratio_old': float(ratio_old),
            'legs': [],
        })
        bucket['legs'].append({
            'rec': r, 'qty': _f(r['quantity']), 'val': _f(r['value']),
            'sym': tgt_sym, 'isin': tgt_isin, 'currency': r['currency'],
        })

    def _side(b) -> str:
        outs = any(l['qty'] < 0 for l in b['legs'])
        ins = any(l['qty'] > 0 for l in b['legs'])
        return 'full' if outs and ins else ('out' if outs else 'in')

    # Legs of one merger stamped on different Date/Times (or reported in
    # two statements a day apart) formed two half-events that were both
    # skipped silently (audit S072-23): an out-only half pairs with the
    # nearest in-only half of the same source and counterparty within a
    # week.
    buckets = list(grouped.values())
    for b in [b for b in buckets if _side(b) == 'out']:
        cands = [c for c in buckets
                 if c is not b and not c.get('merged') and _side(c) == 'in'
                 and (c['src_isin'], c['with_isin'])
                 == (b['src_isin'], b['with_isin'])
                 and _ib_dt_days(c['date_time'], b['date_time']) <= 7]
        if not cands:
            continue
        c = min(cands, key=lambda c: (_ib_dt_days(c['date_time'],
                                                  b['date_time']),
                                      c['date_time']))
        c['merged'] = True
        b['legs'].extend(c['legs'])
        b['date_time'] = min(b['date_time'], c['date_time'])
    buckets = [b for b in buckets if not b.get('merged')]

    events: List[CorporateAction] = []
    blocked: List[Tuple[str, List[Dict[str, Any]]]] = []
    for bucket in buckets:
        recs = [l['rec'] for l in bucket['legs']]
        if not any(r.get('own') for r in recs):
            continue                # another statement's event
        side = _side(bucket)
        if side != 'full':
            print(f"warning: IB merger leg(s) "
                  f"{bucket['legs'][0]['rec']['description'][:100]!r} on "
                  f"{bucket['date_time'][:10]} have no matching "
                  f"{'in' if side == 'out' else 'out'}-leg in any statement "
                  f"of this account — the merger cannot be booked. Add the "
                  f"statement holding the other leg (IB sometimes reports "
                  f"it in the next period), or book the exchange by hand "
                  f"and mark the event `ignore`.", file=sys.stderr)
            blocked.append(('half', recs))
            continue
        src_isin, with_isin = bucket['src_isin'], bucket['with_isin']
        # A merger of a SHORT position inverts the legs: the positive leg
        # names the source (the short being removed), the negative one
        # the acquirer. Read as long it became OLDC -> OLDC with phantom
        # positions on both tickers (audit S072-24). Refused loudly.
        if src_isin != with_isin and any(
                (l['qty'] > 0 and l['isin'] == src_isin)
                or (l['qty'] < 0 and l['isin'] == with_isin)
                for l in bucket['legs']):
            print(f"warning: IB merger "
                  f"{bucket['legs'][0]['rec']['description'][:100]!r} on "
                  f"{bucket['date_time'][:10]} is a merger of a SHORT "
                  f"position — taxjson cannot book it. Record the cover of "
                  f"the old short and the new short by hand in a .tt file, "
                  f"then mark the event `ignore`.", file=sys.stderr)
            blocked.append(('short', recs))
            continue
        # The same merger held in TWO listings of the source (TSX and
        # NYSE lines of one issuer): IB reports each listing's legs in
        # its own currency. Summed into one event, the other listing's
        # shares were never converted (audit S020-07) — one event per
        # listing. A cross-listing journal (CAD out, USD in) is one
        # listing and stays one event.
        out_curs = sorted({l['currency'] for l in bucket['legs']
                           if l['qty'] < 0})
        in_curs = {l['currency'] for l in bucket['legs'] if l['qty'] > 0}
        if len(out_curs) > 1 and in_curs <= set(out_curs):
            parts = [(cur, [l for l in bucket['legs']
                            if l['currency'] == cur]) for cur in out_curs]
        else:
            parts = [('', bucket['legs'])]
        for listing_cur, legs in parts:
            outs = [l for l in legs if l['qty'] < 0]
            ins = [l for l in legs if l['qty'] > 0]
            if not outs or not ins:
                blocked.append(('half', [l['rec'] for l in legs]))
                continue
            cur = outs[-1]['currency']
            tgt = ins[-1]
            src_symbol = _ib_leg_symbol(bucket['src_sym'], cur, fii)
            tgt_symbol = _ib_leg_symbol(
                tgt['sym'], tgt['currency'], fii,
                tsx_unit=(src_symbol.endswith('.U.TO')
                          and tgt['currency'] == cur))
            date_part, _, time_part = bucket['date_time'].partition(',')
            events.append(CorporateAction(
                date=date_part.strip(),
                time=(time_part.strip() or '20:25:00'),
                action_type='merger',
                source_symbol=src_symbol,
                # One id per listing when a merger is split by listing.
                source_isin=(f"{src_isin}@{listing_cur}" if listing_cur
                             else src_isin),
                target_symbol=tgt_symbol, target_isin=tgt['isin'],
                ratio_new=bucket['ratio_new'], ratio_old=bucket['ratio_old'],
                qty_disposed=abs(sum(l['qty'] for l in outs)),
                qty_received=abs(sum(l['qty'] for l in ins)),
                fmv=abs(sum(l['val'] for l in outs)),
                target_fmv=abs(sum(l['val'] for l in ins)),
                currency=cur,
                target_currency=tgt['currency'],
                account=account,
                raw_descriptions=[l['rec']['description'] for l in legs],
                fractional_delivery=True,      # IB delivers real fractions
                broker_account=_acct([l['rec'] for l in legs]),
            ))

    # Split-ups: decompose into events the rules already know. The
    # continuing entity (in-leg whose ticker matches the source, else
    # whose ISIN appears in the WITH clause, else the first) is a
    # MERGER old→new; every other successor is a SPINOFF from the
    # continuing entity. That mirrors the tax shape (Canada: s. 85.1(5)
    # question on the exchange, s. 86.1 question on the distribution).
    for mb in multi_grouped.values():
        if not any(r.get('own') for r in mb['recs']):
            continue                    # another statement's event
        if mb['qty_out'] >= 0 or not mb['in_legs']:
            # Need both sides to emit safely — and say so: a silent skip
            # left the old shares alive and the successors unbooked.
            print(f"warning: IB split-up {mb['descriptions'][0][:100]!r} "
                  f"on {mb['date_time'][:10]} has no matching "
                  f"{'out' if mb['qty_out'] >= 0 else 'in'}-leg in any "
                  f"statement of this account — it cannot be booked. Add "
                  f"the statement holding the other leg(s), or book it by "
                  f"hand and mark the event `ignore`.", file=sys.stderr)
            blocked.append(('half', mb['recs']))
            continue
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

        src_symbol = _ib_leg_symbol(mb['src_sym'], mb['currency'], fii)
        unit = src_symbol.endswith('.U.TO')
        cont_symbol = _ib_leg_symbol(
            cont['sym'], cont['currency'], fii,
            tsx_unit=unit and cont['currency'] == mb['currency'])
        date_part, _, time_part = mb['date_time'].partition(',')
        date = date_part.strip()
        # One second below the event clamp: the spin-off legs below sit
        # one second after this merger (S074-02).
        time = _clamp_time(time_part.strip() or '20:25:00', '23:59:57')
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
            fractional_delivery=True, broker_account=_acct(mb['recs'])))
        for leg in legs:
            if leg is cont:
                continue
            rn, ro = _pair_for(leg)
            leg_symbol = _ib_leg_symbol(
                leg['sym'], leg['currency'], fii,
                tsx_unit=unit and leg['currency'] == mb['currency'])
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
                fractional_delivery=True, broker_account=_acct(mb['recs'])))

    events.extend(_ib_spinoff_events(spin_rows, account, _acct, fii))
    events.extend(_ib_unsupported_events(
        [r for r in odd_rows if r.get('own')], account, _acct))
    # Half / short mergers: a blocking `unsupported` event (the run stops
    # until the user books it by hand and marks it `ignore`) — never a
    # silent skip that leaves the old shares alive.
    events.extend(_ib_unsupported_events(
        [r for _why, recs in blocked for r in recs], account, _acct,
        quiet=True))
    events = _collapse_cross_listing_chains(events)
    _ib_warn_currency_tags(events)
    return events


parse_ib_corporate_actions.accepts_context = True


def _ib_ext(currency: str) -> str:
    """The statement parser's currency -> market suffix (so an event's
    symbols are the ones its trades are booked under)."""
    from taxjson.lib.brokerages.ib_extractor import _ib_currency_ext
    return _ib_currency_ext(currency)


def _ib_spinoff_events(spin_rows, account, acct_of, fii=None
                       ) -> List[CorporateAction]:
    """IB `Spinoff` rows -> spin-off events. The statement parser used
    to book every one as a dividend at IB's Value with no election
    (s.86.1 never offered, and a Canadian butterfly spin-off taxed as
    income). IB's Value is carried as the broker FMV, which the
    `taxable_deemed_dividend` default uses. `spin_rows` are the live
    rows of every statement of the account (cancellations already
    removed); a group this statement holds no row of is another
    statement's event."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in spin_rows:
        if r['qty'] <= 0:
            continue
        groups.setdefault((r['date_time'], r['description'],
                           r['currency']), []).append(r)
    events = []
    for (date_time, desc, currency), recs in groups.items():
        if not any(r.get('own', True) for r in recs):
            continue
        parts = recs[0]['parts']
        qty = sum(r['qty'] for r in recs)
        val = abs(sum(r['value'] for r in recs))
        date_part, _, time_part = date_time.partition(',')
        parent = (_ib_leg_symbol(parts['parent'], currency, fii or {})
                  if parts['parent'] else '(unknown parent)')
        target = _ib_leg_symbol(parts['target'], currency, fii or {},
                                tsx_unit=parent.endswith('.U.TO'))
        if not parts['parent']:
            print(f"warning: IB spin-off row names no parent ticker "
                  f"({desc[:90]!r}) — a s.86.1 rollover's parent-ACB "
                  f"reduction has nowhere to land.", file=sys.stderr)
        events.append(CorporateAction(
            date=date_part.strip(), time=(time_part.strip() or '20:25:00'),
            action_type='spinoff',
            source_symbol=parent,
            source_isin=parts['parent_isin'] or parts['parent'],
            target_symbol=target,
            target_isin=parts['target_isin'] or parts['target'],
            ratio_new=parts['ratio_new'] or qty,
            ratio_old=parts['ratio_old'] or 1.0,
            qty_disposed=0.0, qty_received=qty,
            fmv=val, target_fmv=val,
            currency=currency, target_currency=currency,
            account=account, raw_descriptions=[desc],
            fractional_delivery=True, broker_account=acct_of(recs)))
    return events


def _ib_unsupported_events(odd_rows, account, acct_of, quiet: bool = False
                           ) -> List[CorporateAction]:
    """Merger-shaped IB rows nothing can book (a stock + cash offer, an
    unfamiliar layout). They used to vanish into a .sum NOTE with the
    old shares left in inventory and the new ones never arriving. Each
    becomes an `unsupported` event: the run stops until the user books
    the exchange by hand (a .tt file) and marks the event `ignore`."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in odd_rows:
        m = re.match(r'^\s*([A-Z0-9][A-Z0-9 .]*?)\s*\(([^)]*)\)',
                     r['description'])
        key = (r['date_time'], m.group(2) if m else r['description'])
        groups.setdefault(key, []).append(r)
    events = []
    for (date_time, ident), recs in groups.items():
        outs = [r for r in recs if _f(r['quantity']) < 0]
        ins = [r for r in recs if _f(r['quantity']) > 0]
        head = (outs or recs)[0]
        m = re.match(r'^\s*([A-Z0-9][A-Z0-9 .]*?)\s*\(', head['description'])
        src = _ib_ticker(m.group(1)) if m else '?'
        cur = head['currency']
        tgt = '?'
        if ins:
            mt = re.search(r'\(([A-Z0-9][A-Z0-9 .]*?),[^()]*\)\s*$',
                           ins[0]['description'])
            if mt:
                tgt = f"{_ib_ticker(mt.group(1))}.{_ib_ext(ins[0]['currency'])}"
        date_part, _, time_part = date_time.partition(',')
        if not quiet:
            print(f"warning: IB corporate action taxjson cannot book: "
                  f"{head['description'][:120]!r} on {date_part.strip()} "
                  f"— neither the old shares' disposal nor the new "
                  f"position is booked. Record the exchange by hand in a "
                  f".tt file, then mark the event `ignore`.",
                  file=sys.stderr)
        events.append(CorporateAction(
            date=date_part.strip(), time=(time_part.strip() or '20:25:00'),
            action_type='unsupported',
            source_symbol=f"{src}.{_ib_ext(cur)}", source_isin=ident,
            target_symbol=tgt, target_isin=tgt,
            ratio_new=0.0, ratio_old=0.0,
            qty_disposed=abs(sum(_f(r['quantity']) for r in outs)),
            qty_received=sum(_f(r['quantity']) for r in ins),
            fmv=abs(sum(_f(r['value']) for r in outs)),
            target_fmv=abs(sum(_f(r['value']) for r in ins)),
            currency=cur, target_currency=(ins[0]['currency'] if ins
                                           else cur),
            account=account,
            raw_descriptions=[r['description'] for r in recs],
            broker_account=acct_of(recs)))
    return events


# Elections whose tax deferral is only valid if the user FILES the
# election with their return — consumed by `taxjson run`'s end-of-run
# reminder and the `elect` listing (the option text says this at
# choose time, but a March election is forgotten by filing season).
#
# NOT the s.85.1 share-for-share rollover: it applies automatically
# unless the vendor reports the gain in its return (s.85.1(1)(a) and
# (5)); there is no election form (audit R1-138).
FILING_REQUIRED_ELECTIONS: Dict[str, str] = {
    'rollover_s_86_1': "file the s. 86.1 election with your return "
                       "(spinoff must be on CRA's eligibility list)",
    # No US entry: §354/§355 non-recognition applies by law when the
    # transaction qualifies, and the Reg. §1.368-3 / §1.355-5 statement
    # is due only from a significant holder — omitting it does not undo
    # the deferral. The reminder told every holder "the deferral is only
    # valid with the paperwork" (audit S073-00, the US mirror of R1-138);
    # the option text says who files.
}

# Who attaches the US reorganization statement (Reg. §1.368-3(c),
# §1.355-5(c)): a holder of at least 5% of a publicly traded company's
# stock, 1% of a non-public one, or securities with a basis of $1M or
# more.
_US_SIGNIFICANT_HOLDER = (
    "Only a significant holder (at least 5% of a public company's "
    "stock, 1% of a private one, or a basis of $1 million or more) "
    "attaches the Reg. §{reg} statement to the return; others file "
    "nothing.")

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

    # Walk chain ROOTS first (events whose source is no other event's
    # target), then the rest; output keeps the input order. Walking in
    # row order let a journal hop listed before its merger (IB groups
    # Corporate Actions by currency) be emitted standalone AND folded
    # into the merger (audit S074-03).
    targets = {ev.target_symbol for ev in events}
    walk = sorted(range(len(events)),
                  key=lambda i: (events[i].source_symbol in targets, i))
    placed: Dict[int, CorporateAction] = {}
    consumed: set = set()
    for idx in walk:
        ev = events[idx]
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
            placed[idx] = ev
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
                # The final hop names the market the position ends up
                # in (suffix, BUY-row currency); the VALUE stays the
                # economic merger's own in-leg, in that leg's currency.
                # Taking the final hop's value dated a later journal's
                # figure (once a row IB then cancelled and rebooked)
                # back to the merger and valued the exchange twice
                # (2026-09 audit: SSL->RGLD, 1,774 CAD apart).
                target_currency=current.target_currency,
                target_fmv=(ev.target_fmv if ev.target_fmv > 0
                            else current.target_fmv),
                target_fmv_currency=(
                    (ev.target_fmv_currency or ev.target_currency)
                    if ev.target_fmv > 0
                    else (current.target_fmv_currency
                          or current.target_currency)),
                broker_account=ev.broker_account,
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
            placed[idx] = collapsed

    return [placed[i] for i in sorted(placed)
            if placed[i].event_id not in consumed
            or placed[i] is not events[i]]


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
# Thousands commas are part of the number: '[\d.]+' stopped at the comma,
# missed 'ON 1,500 SHS' and showed a 150-for-1 ratio (audit S073-02).
_QT_ON_SHS_RE = re.compile(
    r'\bON\s+(\d{1,3}(?:,\d{3})+(?:\.\d+)?|[\d.]+)\s+SHS\b', re.IGNORECASE)
_QT_REC_PAY_RE = re.compile(
    r'\bREC\s+(\S+)\s+PAY\s+(\S+)', re.IGNORECASE)


def _parse_qt_date(s: str) -> str:
    """Questrade date column is `YYYY-MM-DD 12:00:00 AM`; strip the
    time fluff and return ISO date."""
    s = (s or '').split(' ', 1)[0]
    return s


def parse_questrade_corporate_actions(
    csv_path: Path, account: str = 'Questrade',
    context_files: Optional[List[Path]] = None,
    renames: Optional[Dict[str, str]] = None,
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
    from taxjson.lib.brokerages.questrade import (_FX_SETTLED_RE,
                                                  _INTERNAL_CODE_RE,
                                                  QuestradeBrokerage,
                                                  _get_desc_key)
    import io
    # The parser's own symbol shape (market suffix; a dotted class share
    # or warrant keeps its dot, a Venture .VN is the canonical .TO line)
    # — the corp rows must land on the pools the trades are booked under
    # (audits R1-141, S074-07).
    _suffix = QuestradeBrokerage().apply_currency_suffix

    def _read(path: Path) -> List[Dict[str, str]]:
        # Decoded and header-normalized exactly like the parser: a
        # padded header (', ' between cells) or a UTF-16 export used to
        # read as no rows here while the parser accepted it — the
        # spin-off's election was silently never asked (audit S020-02).
        raw = Path(path).read_bytes()
        text = (raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff')
                else raw.decode('utf-8-sig'))
        reader = csv.DictReader(io.StringIO(text, newline=''))
        reader.fieldnames = [h.strip() if h else h
                             for h in (reader.fieldnames or [])]
        return list(reader)
    all_rows = _read(csv_path)
    # The parent is usually bought in an EARLIER export (last year's)
    # than the one holding the DIS row: its ticker is looked up in every
    # export of the account, this one first (2026-09 audit — a per-file
    # lookup sent the s.86.1 ACB reduction to the SEC# code).
    lookup_rows = list(all_rows)
    others: List[List[Dict[str, str]]] = []
    for other in context_files or []:
        if Path(other).resolve() == Path(csv_path).resolve():
            continue
        try:
            rows_o = _read(other)
        except (OSError, csv.Error, UnicodeError):
            continue
        others.append(rows_o)
        lookup_rows.extend(rows_o)

    def _listing(row) -> str:
        # The parser's listing rule: a CAD row that says EXCHANGE RATE
        # is a US security bought from the CAD side (questrade.py).
        cur = (row.get('Currency') or 'USD').strip().upper()
        if cur == 'CAD' and _FX_SETTLED_RE.search(row.get('Description')
                                                  or ''):
            cur = 'USD'
        return cur

    # company-name key -> {(ticker, listing currency)}; ticker ->
    # {listing currencies}; (ticker, listing) -> [(date, qty)] from the
    # Trades/Transfers rows of every export. A key with several listings
    # (an interlisted company) is resolved by the one HELD on the
    # spin-off date, never by the first row in file order (audits
    # S073-05, S074-08).
    name_to_symbol: Dict[str, set] = {}
    symbol_listing: Dict[str, set] = {}
    moves: Dict[tuple, list] = defaultdict(list)
    for row in lookup_rows:
        if (row.get('Activity Type') or '').strip() not in ('Trades',
                                                            'Transfers'):
            continue
        sym = (row.get('Symbol') or '').strip().lstrip('.')
        sym = re.sub(r'\.TO$', '', sym, flags=re.IGNORECASE)
        if not sym or _INTERNAL_CODE_RE.match(sym):
            continue
        lst = _listing(row)
        symbol_listing.setdefault(sym.upper(), set()).add(lst)
        key = _get_desc_key(row.get('Description') or '')
        if key:
            name_to_symbol.setdefault(key, set()).add((sym, lst))
        # The parser's strict number (a decimal comma is refused, never
        # read 100x too large — the S072-17 sibling): this lookup sizes
        # the parent held on the spin-off date.
        from taxjson.lib.brokerages.base import parse_strict_number
        q = parse_strict_number(row.get('Quantity'), field='Quantity',
                                where=f"Questrade {row.get('Symbol') or ''} "
                                      f"row of {row.get('Transaction Date')}",
                                allow_blank=True, blank=0.0)
        moves[(sym, lst)].append(
            (_parse_qt_date(row.get('Transaction Date', '')), q))

    def _held_on(cand, date: str) -> float:
        return sum(q for d, q in moves.get(cand, ()) if d <= date)

    # DIS rows of EVERY export of the account: a chain whose placeholder
    # posts in December and its release/repost in January straddles two
    # exports, and each half alone netted to nothing or had no target
    # symbol (audit S073-09). Rows the exports repeat (an overlapping
    # re-download) count once — the most copies any one export holds.
    def _ident(row) -> tuple:
        return tuple(sorted((k, (v or '').strip()) for k, v in row.items()
                            if k is not None and isinstance(v, str)))
    own_ids = {_ident(r) for r in all_rows}
    union: Dict[tuple, Tuple[Dict[str, str], int]] = {}
    for rows_f in [all_rows] + others:
        counts: Dict[tuple, int] = defaultdict(int)
        firsts: Dict[tuple, Dict[str, str]] = {}
        for r in rows_f:
            if (r.get('Action') or '').strip() != 'DIS':
                continue
            i = _ident(r)
            counts[i] += 1
            firsts.setdefault(i, r)
        for i, n in counts.items():
            if i not in union or union[i][1] < n:
                union[i] = (firsts[i], n)
    dis_rows = [(r, i) for i, (r, n) in union.items() for _ in range(n)]

    by_target: Dict[tuple, list] = defaultdict(list)
    for row, ident in dis_rows:
        action_code = (row.get('Action') or '').strip()
        description = row.get('Description') or ''
        if action_code != 'DIS':
            continue
        if not _QT_SPINOFF_RE.search(description):
            continue

        symbol = (row.get('Symbol') or '').strip()
        from taxjson.lib.brokerages.base import parse_strict_number
        qty = parse_strict_number(row.get('Quantity'), field='Quantity',
                                  where=f"{Path(csv_path).name} DIS row "
                                        f"{description[:50]!r}",
                                  allow_blank=True, blank=0.0)
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
                   m_shs.group(1).replace(',', '') if m_shs else '')
        else:
            key = ('symbol', symbol)
        by_target[key].append({
            'date': date, 'symbol': symbol, 'qty': qty,
            'currency': currency, 'description': description,
            'account': (row.get('Account #') or '').strip(),
            'own': ident in own_ids,
        })

    events: List[CorporateAction] = []
    for _key, rows in by_target.items():
        if not any(r['own'] for r in rows):
            continue                    # another export's chain
        rows.sort(key=lambda r: (r['date'], -r['qty']))
        net_qty = sum(r['qty'] for r in rows)
        if net_qty < -1e-9:
            # A chain that REMOVES units (rights/warrants lapsed or
            # taken back as DIS legs) is not a spinoff acquisition —
            # and nothing else books it: the parser counts these legs
            # as corporate-action rows for this stage. Say so instead of
            # dropping it (audit S062-11): the units stay in inventory
            # and their ACB is never claimed.
            _syms = sorted({r['symbol'] for r in rows if r['symbol']})
            print(f"warning: UNBOOKED: Questrade DIS corporate-action "
                  f"chain on {min(r['date'] for r in rows)} "
                  f"({', '.join(_syms) or 'no symbol'}) nets "
                  f"{net_qty:g} units — a removal, not a spinoff; NOT "
                  f"booked. If the units lapsed or were taken back, book "
                  f"the disposition (a $0 sale) in a .tt file: "
                  f"{rows[0]['description'][:90]}", file=sys.stderr)
            continue
        if net_qty <= 0:
            # Net zero: a posting and its reversal — nothing received.
            continue
        # The chain's target symbol: the first row that carries one
        # (placeholder rows don't). Suffix it by currency exactly like
        # the Questrade parser does (USD -> .US, CAD -> .TO) — a bare
        # target emitted book rows on 'DFDVW' while the trades carry
        # 'DFDVW.US', splitting one position across two symbols.
        symbol = next((r['symbol'] for r in rows if r['symbol']), '')
        internal = bool(symbol and _INTERNAL_CODE_RE.match(symbol.upper()))
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
        parent_listing = ''
        event_date = min(r['date'] for r in rows)
        cands = sorted(name_to_symbol.get(_get_desc_key(parent_name))
                       or ()) if parent_name else []
        if len(cands) > 1:
            held = [c for c in cands if _held_on(c, event_date) > 1e-9]
            if len(held) == 1:
                cands = held
            else:
                print(f"warning: Questrade spinoff parent "
                      f"{parent_name!r} (SEC# {parent_code}) trades under "
                      f"several listings in this account ("
                      f"{', '.join(_suffix(sy, c) for sy, c in cands)}) "
                      f"and the one held on {event_date} is not clear — "
                      f"not guessed; a rollover's parent-ACB reduction "
                      f"would land on the SEC# code. Fold the listings "
                      f"with a ticker.map rule.", file=sys.stderr)
        hit = cands[0] if len(cands) == 1 else None
        if hit:
            parent_listing = hit[1].upper()
            parent_symbol = _suffix(hit[0], parent_listing)
        elif parent_code and not cands and _renamed(parent_code, renames):
            # The project's ticker.map names the parent (the closing-year
            # case: the parent sits in the start .tt, which carries no
            # company name to match).
            from taxjson.bin.taxjson_ticker_map import map_symbol
            parent_symbol = map_symbol(parent_code, renames)
        elif parent_code and not cands:
            print(f"warning: Questrade spinoff parent {parent_name or parent_code!r} "
                  f"(SEC# {parent_code}) is not traded or transferred "
                  f"in any export of this account, so its ticker is "
                  f"unknown — a rollover's parent-ACB reduction would "
                  f"land on an empty {parent_code!r} pool. Add the "
                  f"export that bought or transferred the parent into "
                  f"this account, or — when the parent is held in a "
                  f"start .tt (a closing-year project) — name it in "
                  f"ticker.map:  GLOBAL {parent_code} <PARENT>.TO (or "
                  f".US).", file=sys.stderr)

        # The target's listing, the way the parser books its later
        # trades: its own trades' listing when it trades anywhere in the
        # account's exports; else, a CAD DIS row under a US-listed
        # parent (bought from the CAD side, EXCHANGE RATE rows) is the
        # US listing too; else the DIS row's currency.
        bare = re.sub(r'\.TO$', '', symbol.lstrip('.'), flags=re.IGNORECASE)
        _cur = (rows[0]['currency'] or 'USD').upper()
        listings = symbol_listing.get(bare.upper()) or set()
        if len(listings) == 1:
            listing = next(iter(listings))
        else:
            listing = ('USD' if _cur == 'CAD' and parent_listing == 'USD'
                       else _cur)
        if symbol.upper().endswith('.TO'):
            listing = 'CAD' if len(listings) != 1 else listing
        # The parser's shape: ABC.WS -> ABC.WS.US (the old "no dot yet"
        # test left a dotted target bare), NEWCO.VN -> NEWCO.TO.
        symbol = _suffix(bare, listing)
        if internal and not _renamed(symbol, renames):
            # A manual web export writes the distributed warrant/right
            # under Questrade's internal code (D056068) while its later
            # sale carries the real ticker (DFDVW): booked as-is the
            # spinoff is a phantom long and the sale an open short, and
            # in a taxable account the sale drops out of the year's
            # gains (audit R1-3). Nothing in the export links the two.
            # Named as the books carry it, and quiet once ticker.map
            # renames it — the RBC twin's S072-03 rule (A2-0966).
            ext = symbol.rsplit('.', 1)[-1]
            print(f"warning: Questrade spinoff chain on "
                  f"{event_date} is booked under Questrade's INTERNAL "
                  f"code {symbol}, not a ticker "
                  f"({rows[0]['description'][:70]}). Its later trades "
                  f"use the real ticker, so map the code with a "
                  f"ticker.map line:  GLOBAL {symbol} <TICKER>.{ext} — "
                  f"otherwise the position splits in two.",
                  file=sys.stderr)

        # Ratio denominator (the parent share count the user held).
        source_qty = 0.0
        for r in rows:
            m = _QT_ON_SHS_RE.search(r['description'])
            if m:
                source_qty = _num_text(m.group(1), where=r['description'])
                break

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
            broker_account=','.join(sorted({r['account'] for r in rows}
                                           - {''})),
        ))

    return events


parse_questrade_corporate_actions.accepts_context = True
parse_questrade_corporate_actions.accepts_renames = True


# --- RBC Direct extractor --------------------------------------------------


# RBC books every reorganization as a removal row (negative Quantity, often
# under a TEMPORARY code like 'H015283') plus a receipt row (positive
# Quantity, the listed ticker), both $0 'Reorganization' rows coded:
#   MGR  merger / exchange     "MGR - HESS CORPORATION MERGER TO CHEVRON
#                               CORPORATION 1.025 NEW = 1 OLD"
#                              "MGR - CHEVRON CORPORATION SHRS RECEIVED THRU
#                               MERGER"
#                              "MGR - BLACKROCK INC TO BLACKROCK INC COMMON
#                               STOCK 1 FOR 1", "MGR - " (blank: Arista 4:1)
#   NAC  name change           "NAC - ... NAME CHANGE TO ..." / "... RESULT OF
#                               NAME CHANGE"
#   REV  reverse split         "REV - ... REV SPLIT TO ...; 1 FOR 10" / "...
#                               RESULT OF REVERSE SPLIT" (+ "REVERSE ENTRY"
#                               corrections that cancel a leg)
#   MER  reorganization w/ ROC "MER - THOMSON REUTERS CORP COM NEW DEFAULT: ROC
#                               OF C$6.1585 + .963957 NEW SHS PER 1 OLD"
#   XCH  option adjustment     "XCH - CALL .TOU 03/21/25 64 ... ADJ FOR
#                               SPECIAL CASH DIV" (old code out, new code in)
# `pair_rbc_reorganizations` pairs each removal with its receipt; only true
# mergers ("MERGER TO") need a tax election and become CorporateActions here.
# The brokerage parser books every other pair itself as ONE SPLIT.
RBC_REORG_CODES = frozenset({'MGR', 'NAC', 'REV', 'MER', 'XCH'})
# A number in RBC's free text: '1,000' groups thousands (a '[\d.]+'
# group stopped at the comma and read '1 NEW = 1,000 OLD' as 1-for-1 —
# then the cash-in-lieu sold almost the whole position; audit S073-14).
#
# Captured WHOLE (no digit, comma or point on either side) and checked
# by `_num_text`: a decimal comma ('0,5', '1,5') is refused. The capture
# used to start after the comma and read '0,5 NEW = 1 OLD' as 5-for-1
# (A2-0972, A2-0976).
_RBC_NUM = r'(?<![\w.,])(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?![\d,])'
_RBC_MERGER_TO_RE = re.compile(
    rf'\bMERGER\s+TO\s+(.+?)(?:\s+{_RBC_NUM}\s+NEW|\s*$)', re.I)
_RBC_RATIO_RE = re.compile(
    rf'({_RBC_NUM})\s+NEW\s*=\s*({_RBC_NUM})\s+OLD', re.I)


def rbc_ratio_parts(desc: str) -> Optional[Tuple[float, float]]:
    """(new, old) of an RBC "<N> NEW = <M> OLD" phrase, thousands
    commas understood; None when absent."""
    m = _RBC_RATIO_RE.search(desc or '')
    if not m:
        return None
    return (_num_text(m.group(1), where=desc[:60]),
            _num_text(m.group(2), where=desc[:60]))
_RBC_OLDCO_RE = re.compile(r'^\s*MGR\s*[-:]?\s*(.+?)\s+MERGER\s+TO\b', re.I)
_RBC_RECVCO_RE = re.compile(
    r'^\s*MGR\s*[-:]?\s*(.+?)\s+(?:SHRS|SHARES)\s+RECEIVED', re.I)
_RBC_CO_SUFFIX_RE = re.compile(
    r'\b(CORPORATION|CORP|INCORPORATED|INC|LTD|LIMITED|COMPANY|CO|PLC|SA|NV|AG|'
    r'HOLDINGS|GROUP)\b', re.I)
# A temporary reorganization placeholder RBC assigns while a security is
# mid-reorganization (e.g. 'H015283' for HESS, 'C049527' for CANOPY). It is
# NOT the ticker the position is actually held under.
_RBC_TEMP_SYMBOL_RE = re.compile(r'^[A-Z]\d{4,}$')
# RBC's internal 7-character option code ('8DZQFW4', '9PKLPN0').
_RBC_OPTION_CODE_RE = re.compile(r'^[89][A-Z0-9]{6}$')
_RBC_LEG_OPTION_RE = re.compile(
    r'\b(CALL|PUT)\s+\.?([A-Z0-9.]+?)\s+(\d{1,2}/\d{1,2}/\d{2})\s+'
    r'(\d[\d,]*(?:\.\d+)?|\.\d+)(?![\d,])')  # "5,025": S063-15; whole
_RBC_CODE_PREFIX_RE = re.compile(r'^\s*[A-Z]{2,4}\s*-\s*')
_RBC_TO_RE = re.compile(
    r'\b(?:NAME\s+(?:CHANGE|CHG)\s+TO|REV(?:ERSE)?\s+SPLIT\s+TO|MERGER\s+TO|'
    r'XCH\s+TO|TO)\s+(.+?)(?:\s*;|\s+\d+(?:\.\d+)?\s+FOR\s+\d|'
    r'\s+[\d.]+\s+NEW\s*=|$)')
_RBC_RECEIPT_TAIL_RE = re.compile(
    r'\s+(?:AS\s+OF\s+\d|RESULT\s+OF\b|SHRS\s+RECEIVED|SHARES\s+RECEIVED)')
# RBC's own return-of-capital phrase ("DEFAULT: ROC OF C$6.1585"), not a
# bare ROC token: a company named "ROC OIL CORP" turned un-understood
# merger boot into a return of capital (audit S071-24).
# Only the consideration clause with an amount ('ROC OF C$6.1585'): the
# words in an issuer name ('ROC OF CANADA HOLDINGS') or a negation ('NO
# ROC OF C$ PAID', 'RETURN OF CAPITAL NOT APPLICABLE') turned the leg's
# cash into a silent ACB reduction (A2-0559); those keep the loud
# not-understood path.
_RBC_ROC_RE = re.compile(
    r'(?<!\bNO\s)(?<!\bNOT\s)\b(?:ROC|RETURN\s+OF\s+CAPITAL)\s+OF\s+'
    r'(?:[A-Z]{1,3})?\$\s*\.?\d', re.I)
_RBC_NAME_STOP = frozenset((
    'CORPORATION CORP INCORPORATED INC LTD LIMITED COMPANY CO PLC SA NV AG '
    'HOLDINGS HOLDING GROUP THE COM COMMON STOCK SHARES SHARE SHS SH NEW NO '
    'PAR CL CLASS SUB SUBORD SUBORDINATE VTG VOTING EXCHANGEABLE EXCHANGBLE '
    'UNIT UNITS TR TRUST ETF ORD DEFAULT OF AND').split())


def rbc_is_temp_symbol(symbol: str) -> bool:
    """RBC temporary reorganization placeholder ('H015283')."""
    return bool(_RBC_TEMP_SYMBOL_RE.match((symbol or '').strip().upper()))


def rbc_is_option_code(symbol: str) -> bool:
    """RBC's internal 7-character option code ('8DZQFW4')."""
    return bool(_RBC_OPTION_CODE_RE.match((symbol or '').strip().upper()))


def _rbc_is_real_ticker(symbol: str) -> bool:
    """Whether `symbol` is a genuine exchange ticker rather than a
    temporary reorg placeholder (`H015283`) or an empty/cash-row blank."""
    s = (symbol or '').strip().upper()
    if not s or _RBC_TEMP_SYMBOL_RE.match(s):
        return False
    return any(c.isalpha() for c in s)


def _rbc_norm_company(name: str) -> str:
    return re.sub(r'[^A-Z0-9]', '',
                  _RBC_CO_SUFFIX_RE.sub('', (name or '').upper()))


def rbc_norm_company(name: str) -> str:
    """Exact-match key for an RBC security name (legal-form words and
    punctuation removed; '**FORTUNA' and 'FORTUNA' are one key)."""
    return _rbc_norm_company((name or '').lstrip('*'))


def rbc_rights_key(text: str) -> str:
    """Identity of a rights/warrants issue across its rows: RBC books the
    distribution under a real symbol (CSU.RT) and the expiry under a
    temporary code, both described "RTS <ISSUER> EXP mm/dd/yyyy"."""
    m = re.search(r'\b(RTS|WTS)\s+(.+?)\s+EXP\s+(\d\d/\d\d/\d{4})',
                  (text or '').upper())
    if not m:
        return ''
    return f"{m.group(1)}|{_rbc_norm_company(m.group(2))}|{m.group(3)}"


def _rbc_name_tokens(name: str) -> List[str]:
    toks = re.findall(r'[A-Z0-9]+', (name or '').upper())
    out = []
    for t in toks:
        if len(t) > 1 and t not in _RBC_NAME_STOP and t not in out:
            out.append(t)
    return out


def _rbc_tok_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)):
        return True           # "ETHEREU M" (RBC's own typo) vs ETHEREUM
    if min(len(a), len(b)) >= 6:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85
    return False              # INSFRASTRUCTURE vs INFRASTRUCTURE ↑


def rbc_name_similarity(a: str, b: str) -> float:
    """Share of the shorter name's significant tokens found in the other
    (0..1). Legal-form and share-class words don't count."""
    ta, tb = _rbc_name_tokens(a), _rbc_name_tokens(b)
    if not ta or not tb:
        return 0.0
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hit = sum(1 for x in small if any(_rbc_tok_eq(x, y) for y in big))
    return hit / len(small)


def _rbc_body(desc: str) -> str:
    return _RBC_CODE_PREFIX_RE.sub('', (desc or '').upper()).strip()


def _rbc_removal_names(leg) -> Tuple[str, str]:
    """(old company, new company) of a removal leg."""
    body = _rbc_body(leg.desc)
    m = _RBC_TO_RE.search(body)
    new = m.group(1).strip() if m else ''
    old = leg.symdesc or (body[:m.start()] if m
                          else re.split(r'\s+(?:DEFAULT:|AS\s+OF\s)', body)[0])
    return old.strip().lstrip('*'), new.lstrip('*')


def _rbc_receipt_name(leg) -> str:
    if leg.symdesc:
        return leg.symdesc.lstrip('*')
    return _RBC_RECEIPT_TAIL_RE.split(_rbc_body(leg.desc))[0].lstrip('*')


def _rbc_stated_ratio(desc: str) -> Optional[float]:
    """New shares per old share stated in a removal's description:
    "1.025 NEW = 1 OLD", ".963957 NEW SHS PER 1 OLD", "; 1 FOR 10"."""
    d = (desc or '').upper()
    n = _RBC_NUM
    # '\b...(?![\d,])': '1 FOR 1,000' must read 1000, never stop at
    # the comma and read 1-for-1 (audit S073-19).
    # `_RBC_NUM` is anchored on both sides in every pattern (the
    # boundary fix used to reach only the third one: A2-0972).
    for pat in (rf'({n})\s+NEW\s*=\s*({n})\s+OLD',
                rf'({n})\s+NEW\s+SH(?:S|ARES?)?\s+PER\s+({n})\s+OLD',
                rf'({n})\s+FOR\s+({n})'):
        m = re.search(pat, d)
        if m:
            new = _num_text(m.group(1), where=d[:60])
            old = _num_text(m.group(2), where=d[:60])
            if new > 0 and old > 0:
                return new / old
    return None


def _rbc_leg_option(leg):
    for text in (leg.desc, leg.symdesc):
        m = _RBC_LEG_OPTION_RE.search((text or '').upper())
        if m:
            right, base, exp, strike = m.groups()
            # '6,4' is refused, not read as strike 6 (A2-0972).
            _num_text(strike, where=(text or '')[:60])
            return right, base, exp, strike.replace(',', '')
    return None


def _rbc_leg_is_option(leg) -> bool:
    return rbc_is_option_code(leg.symbol) or bool(_rbc_leg_option(leg))


def _rbc_days(d1: str, d2: str) -> int:
    try:
        return abs((datetime.strptime(d1, '%Y-%m-%d')
                    - datetime.strptime(d2, '%Y-%m-%d')).days)
    except ValueError:
        return 9999


@dataclass
class RbcReorgEvent:
    """One paired RBC reorganization.

    kind: 'merger' (needs a tax election — taxjson-corp-actions),
          'reorg' (name change / split / 1-for-1 exchange / MER: the
          parser books ONE SPLIT), 'option_adjust' (XCH on an option:
          the same contract continues), 'reversal' (a leg and its
          REVERSE ENTRY correction: nets to nothing)."""
    kind: str
    removal: Any
    receipt: Any
    cil: List[Any] = field(default_factory=list)
    ratio: Optional[float] = None          # stated new-per-old
    roc_amount: float = 0.0                # MER "ROC OF C$x" cash (Value)

    @property
    def date(self) -> str:
        return self.removal.date


@dataclass
class RbcReorgPairing:
    events: List[RbcReorgEvent]
    unmatched: List[Any]                   # reorg legs with no partner
    unmatched_cil: List[Any]               # CIL rows with no event


def _rbc_same_account(a, b) -> bool:
    """Legs of one reorganization sit in ONE broker account: an export
    covering two RBC accounts that both hold the security must not pair
    one account's removal with the other's receipt. Unknown ('' — no
    Account column) matches anything."""
    x, y = _rbc_account(a), _rbc_account(b)
    return not x or not y or x == y


def _rbc_account(row) -> str:
    """The row's RBC account, normalized like rbc_direct._norm_account
    (digits only, so '12345678' and '1234-5678' are one account)."""
    s = (getattr(row, 'account', '') or '').strip()
    return re.sub(r'\D', '', s) or s.upper()


def _rbc_stock_score(rem, rc) -> Tuple[float, float]:
    """(score, name similarity) of pairing removal `rem` with receipt `rc`."""
    old, new = _rbc_removal_names(rem)
    rname = _rbc_receipt_name(rc)
    name = max(rbc_name_similarity(rname, new) if new else 0.0,
               rbc_name_similarity(rname, old))
    ratio = _rbc_stated_ratio(rem.desc)
    qty_ok = ratio is None or abs(abs(rem.qty) * ratio - rc.qty) < 1.0
    score = (2.0 * name + (1.0 if qty_ok else 0.0)
             + (0.5 if rem.date == rc.date else 0.0)
             + (0.25 if rc.code == rem.code else 0.0)
             + (0.25 if rc.currency == rem.currency else 0.0))
    return score, name


def _rbc_option_score(rem, rc) -> float:
    a, b = _rbc_leg_option(rem), _rbc_leg_option(rc)
    if not a or not b:
        return 0.0
    if a[0] != b[0] or a[2] != b[2]:          # right, expiry
        return 0.0
    root = lambda s: re.sub(r'\d+$', '', re.sub(r'[^A-Z0-9]', '', s))
    if root(a[1]) != root(b[1]) and rbc_name_similarity(
            _rbc_body(rem.desc), _rbc_body(rc.desc)) < 0.5:
        return 0.0
    return (1.0 + (1.0 if abs(abs(rem.qty) - rc.qty) < 1e-9 else 0.0)
            + (0.5 if rem.date == rc.date else 0.0))


def _rbc_rank_option_pairs(removals, receipts, used: set
                           ) -> List['RbcReorgEvent']:
    """Option-adjust events for groups of n >= 2 removals and n receipts
    of one series family (account, right, root, expiry, date) where
    every removal/receipt pair is a candidate (`_rbc_option_score` > 0)
    and the rank-matched pairs carry equal quantities: matched in strike
    order. Marks the legs in `used`."""
    def _fam(leg):
        o = _rbc_leg_option(leg)
        if not o:
            return None
        root = re.sub(r'\d+$', '', re.sub(r'[^A-Z0-9]', '', o[1]))
        return (_rbc_account(leg), o[0], root, o[2], leg.date)

    def _strike(leg):
        try:
            return float(_rbc_leg_option(leg)[3])
        except (TypeError, ValueError, IndexError):
            return None
    groups: Dict[tuple, Tuple[list, list]] = {}
    for r in removals:
        if id(r) not in used and _rbc_leg_is_option(r) and _fam(r):
            groups.setdefault(_fam(r), ([], []))[0].append(r)
    for r in receipts:
        if id(r) not in used and _rbc_leg_is_option(r) and _fam(r) in groups:
            groups[_fam(r)][1].append(r)
    out = []
    for rems, rcs in groups.values():
        if len(rems) < 2 or len(rems) != len(rcs):
            continue
        if any(_strike(x) is None for x in rems + rcs):
            continue
        if any(_rbc_option_score(a, b) <= 0 for a in rems for b in rcs):
            continue
        rems = sorted(rems, key=lambda x: (_strike(x), x.symbol))
        rcs = sorted(rcs, key=lambda x: (_strike(x), x.symbol))
        if len({_strike(x) for x in rems}) != len(rems) or len(
                {_strike(x) for x in rcs}) != len(rcs):
            continue
        if any(abs(abs(a.qty) - b.qty) > 1e-9 for a, b in zip(rems, rcs)):
            continue
        for a, b in zip(rems, rcs):
            used.update((id(a), id(b)))
            out.append(RbcReorgEvent('option_adjust', a, b))
    return out


def _rbc_strike_gap(rem, rc) -> float:
    """|strike change| between a removal and a receipt option leg. A
    special-dividend XCH adjusts every strike a little (64 -> 63.50,
    70 -> 69.50): two same-right, same-expiry contracts adjusted the
    same day tie on every other score, and the first receipt in file
    order used to win — swapping ACB between the contracts (audit
    S071-19). The closest strike is the same contract."""
    a, b = _rbc_leg_option(rem), _rbc_leg_option(rc)
    try:
        return abs(float(a[3]) - float(b[3]))
    except (TypeError, ValueError, IndexError):
        return float('inf')


# An RBC merger phrase on a leg whose sign says the account was SHORT:
# the "MERGER TO" (removal) phrase on a positive quantity, or the
# "SHRS RECEIVED THRU MERGER" (receipt) phrase on a negative one.
def _rbc_short_merger_leg(leg) -> bool:
    d = (leg.desc or '').upper()
    if 'REVERSE ENTRY' in d:
        return False
    if leg.qty > 0 and _RBC_MERGER_TO_RE.search(d):
        return True
    return leg.qty < 0 and bool(re.search(r'\b(?:SHRS|SHARES)\s+RECEIVED\b',
                                          d))


def _rbc_cross_issuer(rem, rc) -> bool:
    """An RBC MGR exchange INTO ANOTHER COMPANY worded without "MERGER
    TO" ("MAPLE ENERGY CORP XCH TO OVERSEAS ENERGY INC; 1 FOR 5" + a
    "SHRS RECEIVED THRU MERGER" receipt). Booked as a basis-carrying
    rename it decided a rollover with no election (audit S072-00). Same-
    issuer exchanges (Celestica's share-class collapse, BlackRock's
    holdco, Brookfield's new corp) name the same company on both sides
    and stay parser-booked."""
    if (rem.code or '').upper() != 'MGR':
        return False
    if not re.search(r'\bMERGER\b', ((rem.desc or '') + ' '
                                       + (rc.desc or '')).upper()):
        return False
    old, new = _rbc_removal_names(rem)
    if not new:
        return False
    return max(rbc_name_similarity(old, new),
               rbc_name_similarity(_rbc_receipt_name(rc), old)) < 0.8


def pair_rbc_reorganizations(rows) -> RbcReorgPairing:
    """Pair RBC reorganization legs (rows classified 'reorg') into events
    and fold cash-in-lieu rows ('cil') into them. `rows` are
    rbc_direct.RbcRow objects (read_rbc_rows). Nothing is dropped: every
    leg either joins an event or is returned in `unmatched`."""
    legs = [r for r in rows if getattr(r, 'cls', '') == 'reorg']
    chrono = lambda r: (r.date, getattr(r, 'k', 0), -r.order)
    used: set = set()
    events: List[RbcReorgEvent] = []

    # 1) A booked leg and its "REVERSE ENTRY" correction cancel out.
    for leg in sorted(legs, key=chrono):
        if id(leg) in used or 'REVERSE ENTRY' not in leg.desc.upper():
            continue
        cands = [m for m in legs
                 if m is not leg and id(m) not in used
                 and m.symbol == leg.symbol and abs(m.qty + leg.qty) < 1e-9
                 and _rbc_days(m.date, leg.date) <= 7
                 and _rbc_same_account(m, leg)]
        if not cands:
            continue
        m = min(cands, key=lambda m: _rbc_days(m.date, leg.date))
        used.update((id(m), id(leg)))
        neg, pos = (leg, m) if leg.qty < 0 else (m, leg)
        events.append(RbcReorgEvent('reversal', neg, pos))

    # 2) Each removal with its best receipt within ±7 days.
    # A merger on a SHORT position inverts the legs' signs; paired as if
    # long, the SPLIT renamed the NEW ticker into the temporary code
    # (audit S071-22). Such legs stay unmatched — the loud path.
    short = {id(r) for r in legs if _rbc_short_merger_leg(r)}
    removals = sorted((r for r in legs if id(r) not in used
                       and id(r) not in short and r.qty < 0), key=chrono)
    receipts = [r for r in legs if id(r) not in used
                and id(r) not in short and r.qty > 0]
    # Option series adjusted together (a special-dividend XCH moves
    # every strike of one right and expiry by the same amount): pair
    # removals to receipts by strike RANK. The greedy closest-strike
    # pick below swapped two series' ACB whenever the adjustment exceeded
    # half the strike spacing, depending only on row order (A2-0222).
    for event in _rbc_rank_option_pairs(removals, receipts, used):
        events.append(event)
    for rem in removals:
        if id(rem) in used:
            continue
        is_opt = _rbc_leg_is_option(rem)
        window = [rc for rc in receipts
                  if id(rc) not in used
                  and _rbc_days(rc.date, rem.date) <= 7
                  and _rbc_leg_is_option(rc) == is_opt
                  and _rbc_same_account(rc, rem)]
        pick = None
        if is_opt:
            scored = [(_rbc_option_score(rem, rc), rc) for rc in window]
            scored = [s for s in scored if s[0] > 0]
            if scored:
                pick = max(scored, key=lambda s: (
                    s[0], -_rbc_strike_gap(rem, s[1]),
                    -_rbc_days(s[1].date, rem.date)))[1]
        else:
            scored = [(_rbc_stock_score(rem, rc), rc) for rc in window]
            # More than half the significant name tokens: 'ALPHA
            # RESOURCES' vs 'ALPHA GOLD' (0.5) is two companies, and a
            # 0.5 pair moved one company's pool into the other's.
            named = [s for s in scored if s[0][1] > 0.5]
            if named:
                pick = max(named, key=lambda s: (
                    s[0][0], -_rbc_days(s[1].date, rem.date)))[1]
            elif len(window) == 1:
                # A lone candidate with an unrecognisable name: accept only
                # when a STATED ratio explains its quantity. Accepting any
                # lone receipt in the window paired a removal whose own
                # receipt posts in the next export with an unrelated
                # company's reorganization (audit S071-20).
                ratio = _rbc_stated_ratio(rem.desc)
                rc = window[0]
                if ratio is not None \
                        and abs(abs(rem.qty) * ratio - rc.qty) < 1.0:
                    pick = rc
        if pick is None:
            continue
        used.update((id(rem), id(pick)))
        if is_opt:
            events.append(RbcReorgEvent('option_adjust', rem, pick))
            continue
        kind = ('merger' if _RBC_MERGER_TO_RE.search(rem.desc)
                or _rbc_cross_issuer(rem, pick) else 'reorg')
        roc = (rem.value if rem.value > 0.005 and _RBC_ROC_RE.search(rem.desc)
               else 0.0)
        events.append(RbcReorgEvent(kind, rem, pick,
                                    ratio=_rbc_stated_ratio(rem.desc),
                                    roc_amount=roc))
    unmatched = [r for r in legs if id(r) not in used]

    # 3) Cash in lieu of the fractional share, into its event: same
    #    ticker as the receipt or the removal, or the same company by
    #    its FULL name (the old ticker, a temporary code), paid within 45
    #    days. Half the name tokens in common (ALPHA GOLD vs ALPHA
    #    RESOURCES) folded another security's cash into the wrong
    #    disposition and silenced its own "NOT booked" warning (audit
    #    S072-01).
    unmatched_cil = []
    stock_events = [e for e in events if e.kind in ('merger', 'reorg')]
    for c in (r for r in rows if getattr(r, 'cls', '') == 'cil'):
        best = None
        for ev in stock_events:
            if not _rbc_same_account(c, ev.receipt):
                continue
            try:
                lag = (datetime.strptime(c.date, '%Y-%m-%d')
                       - datetime.strptime(ev.date, '%Y-%m-%d')).days
            except ValueError:
                continue
            if not -3 <= lag <= 45:
                continue
            if c.symbol and c.symbol in (ev.receipt.symbol,
                                         ev.removal.symbol):
                s = 2.0
            else:
                cname = c.symdesc or _rbc_body(c.desc)
                old, new = _rbc_removal_names(ev.removal)
                s = max(rbc_name_similarity(cname, _rbc_receipt_name(ev.receipt)),
                        rbc_name_similarity(cname, new) if new else 0.0,
                        rbc_name_similarity(cname, old) if old else 0.0)
                if s < 1.0:
                    continue
            key = (s, -abs(lag))
            if best is None or key > best[0]:
                best = (key, ev)
        if best is None:
            unmatched_cil.append(c)
        else:
            best[1].cil.append(c)
    return RbcReorgPairing(events, unmatched, unmatched_cil)


_RBC_SUFFIXER = None


def _rbc_ca_symbol(symbol: str, currency: str) -> str:
    """The brokerage parser's own symbol for an RBC row: its
    `apply_currency_suffix`, with the canonical-listing rules (FTN.PRA ->
    FTN.PR.A.TO, a Venture / CSE suffix -> ROOT.TO, an unmapped currency
    as the parser spells it). A private copy of the old suffix rule booked
    a preferred share's merger on FTN.PRA.TO while its trades were on
    FTN.PR.A.TO (A2-0223, the S014-07 twin)."""
    global _RBC_SUFFIXER
    if _RBC_SUFFIXER is None:
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        _RBC_SUFFIXER = RbcBrokerage()
    return _RBC_SUFFIXER.apply_currency_suffix(
        (symbol or '').strip(), (currency or '').upper() or 'USD')


_RBC_SPINOFF_RE = re.compile(
    r'\bSPIN\s?OFF\s+ON\s+([\d,]*\.?\d+)\s+SH(?:S|ARES?)?\s+FROM\s+SEC#\s*'
    r'(\S+)\s+(.+?)(?:\s+REC\s+\d\d/|\s*$)', re.I)


def parse_rbc_corporate_actions(
    csv_path: Path, account: str = 'RBC',
    context_files: Optional[List[Path]] = None,
    renames: Optional[Dict[str, str]] = None,
) -> List[CorporateAction]:
    """Extract election events from an RBC Direct Investing activity CSV:
    mergers ("<OLDCO> MERGER TO <NEWCO> <ratio> NEW = <n> OLD" removal +
    "<NEWCO> SHRS RECEIVED THRU MERGER" receipt, paired by
    `pair_rbc_reorganizations`) and spin-offs ("DIS - <NEWCO> SPINOFF ON
    <n> SHS FROM SEC# <code> <PARENT>"). Name changes, splits and 1-for-1
    exchanges need no election; the brokerage parser books those.

    RBC reports no FMV and no ISIN; FMV comes from the election hint, and
    the symbols stand in for the ISINs in the event-id hash.

    `renames` — the project's ticker.map renames (`taxjson run` passes
    them): a spin-off booked under a temporary code that the map already
    renames needs no "map it" warning."""
    from taxjson.lib.brokerages.rbc_direct import read_rbc_rows
    rows = read_rbc_rows(Path(csv_path)).rows

    # Company name → the real ticker it trades under (a merger removal is
    # booked under a temporary code; the same security's dividend / trade
    # rows carry the real ticker under the same 'Symbol Description').
    # Every export of the account is searched (this one first): the
    # position was often bought in an earlier year's export.
    lookup_rows = list(rows)
    other_files: List[list] = []
    for other in context_files or []:
        if Path(other).resolve() == Path(csv_path).resolve():
            continue
        try:
            other_files.append(read_rbc_rows(Path(other)).rows)
        except Exception:
            continue
        lookup_rows.extend(other_files[-1])
    # name -> {symbol: {listing currencies}}. Only SHARE lines count (an
    # option's code and description name the issuer too, and an s.86.1
    # ADJUST pointed at the option's empty pool became a phantom gain —
    # audit S019-06). A symbol's listing is the currency its TRADES
    # settle in (the parser suffixes trades by row currency); rows that
    # never trade (dividends) fall back to their own currency. Keeping
    # every listing, not the first seen, lets an interlisted company be
    # told apart by the removal's currency — or refused as ambiguous —
    # instead of by file order (audits S019-05, S074-09).
    name_listings: Dict[str, Dict[str, set]] = {}
    trade_cur: Dict[str, set] = {}
    other_cur: Dict[str, set] = {}
    share_rows = []
    for r in lookup_rows:
        if not (r.symbol and _rbc_is_real_ticker(r.symbol)) \
                or _rbc_leg_is_option(r) \
                or getattr(r, 'cls', '') in ('reorg', 'spinoff', 'cil'):
            continue
        share_rows.append(r)
        cur = (r.currency or '').upper()
        (trade_cur if getattr(r, 'cls', '') in ('trade', 'transfer')
         else other_cur).setdefault(r.symbol, set()).add(cur)
        name = _rbc_norm_company(r.symdesc)
        if name:
            name_listings.setdefault(name, {}).setdefault(r.symbol, set())

    def _listings(sym: str) -> set:
        return trade_cur.get(sym) or other_cur.get(sym) or set()

    def _position(sym: str, cur: str, date: str) -> float:
        """Shares of one listing held at the end of `date` (trades and
        transfers of that symbol settled in that currency)."""
        return sum(x.qty for x in share_rows
                   if x.symbol == sym and (x.currency or '').upper() == cur
                   and getattr(x, 'cls', '') in ('trade', 'transfer')
                   and x.date <= date)

    def _resolve(syms, prefer_cur: str = '', what: str = '',
                 on_date: str = '') -> Optional[str]:
        """One (symbol, listing) from candidate share symbols, as the
        parser's suffixed symbol; None (with a warning) when several
        listings remain after preferring `prefer_cur`, then the one
        listing held on `on_date` — a listing traded only before (sold
        out of) is not the parent (A2-0210, the Questrade twin's
        S073-05/S074-08 rule)."""
        opts = sorted({(sym, c) for sym in syms for c in _listings(sym)})
        if not opts:
            return None
        if len(opts) > 1 and prefer_cur:
            pick = [o for o in opts if o[1] == prefer_cur.upper()]
            if len(pick) == 1:
                opts = pick
        if len(opts) > 1 and on_date:
            held = [o for o in opts if _position(*o, on_date) > 1e-9]
            if len(held) == 1:
                opts = held
        if len(opts) > 1:
            shown = ', '.join(_rbc_ca_symbol(sy, c) for sy, c in opts)
            print(f"warning: RBC {what}: the company trades under several "
                  f"listings in this account ({shown}) — not guessed. "
                  f"Map the one that holds the shares with a ticker.map "
                  f"GLOBAL line.", file=sys.stderr)
            return None
        return _rbc_ca_symbol(*opts[0])

    pairing = pair_rbc_reorganizations(rows)
    merger_pairs = [ev for ev in pairing.events if ev.kind == 'merger']
    unmatched = list(pairing.unmatched)
    # Legs of one merger split across two statements (a December removal,
    # a January receipt): pair this file's leftover legs with the other
    # statements' leftovers, as IB and Questrade do. Only a removal held
    # in THIS file emits the event; a row an overlapping export repeats
    # is not a second leg (A2-0214).
    if other_files and unmatched:
        def _key(x):
            return (x.date, x.symbol, round(x.qty, 9), x.desc, x.currency,
                    x.account)
        own_keys = {_key(x) for x in rows}
        pool = [x for x in unmatched]
        pool += [x for x in rows if getattr(x, 'cls', '') == 'cil']
        for f_rows in other_files:
            other = pair_rbc_reorganizations(f_rows)
            pool += [x for x in other.unmatched if _key(x) not in own_keys]
            pool += [x for x in f_rows if getattr(x, 'cls', '') == 'cil'
                     and _key(x) not in own_keys]
        cross = pair_rbc_reorganizations(pool)
        own_left = {id(x) for x in unmatched}
        for ev in cross.events:
            if id(ev.removal) in own_left or id(ev.receipt) in own_left:
                own_left.discard(id(ev.removal))
                own_left.discard(id(ev.receipt))
                if ev.kind == 'merger' and any(
                        ev.removal is x for x in unmatched):
                    merger_pairs.append(ev)
        unmatched = [x for x in unmatched if id(x) in own_left]
    events: List[CorporateAction] = []
    for leg in unmatched:
        if _rbc_short_merger_leg(leg):
            print(
                f"warning: RBC merger leg on {leg.date} ({leg.symbol}, qty "
                f"{leg.qty:g}) is a merger of a SHORT position — taxjson "
                f"cannot book it (the legs' signs are inverted). NOTHING "
                f"was booked: record the cover of the old short and the "
                f"new short by hand in a .tt file. {leg.desc[:90]!r}",
                file=sys.stderr)
            continue
        if leg.qty < 0 and _RBC_MERGER_TO_RE.search(leg.desc):
            oldm = _RBC_OLDCO_RE.search(leg.desc)
            print(
                f"warning: RBC merger removal on {leg.date} "
                f"({(oldm.group(1).strip() if oldm else leg.symbol)!r}, "
                f"qty {leg.qty:g}) has NO matching share receipt within "
                f"7 days in any statement of this account — taxjson "
                f"cannot book it. Add the statement holding the receipt "
                f"row, or record the exchange by hand in a .tt file and "
                f"mark the event `ignore`.",
                file=sys.stderr,
            )
            # Blocking, as on IB: the run stops until the user acts,
            # instead of continuing with the old shares neither removed
            # nor exchanged (A2-0214).
            src = _rbc_ca_symbol(leg.symbol, leg.currency)
            events.append(CorporateAction(
                date=leg.date, time='09:30:00', action_type='unsupported',
                source_symbol=src, source_isin=f"{src}@{leg.date}",
                target_symbol='?', target_isin='?',
                ratio_new=0.0, ratio_old=0.0,
                qty_disposed=abs(leg.qty), qty_received=0.0, fmv=0.0,
                currency=leg.currency or 'USD',
                target_currency=leg.currency or 'USD', account=account,
                raw_descriptions=[leg.desc],
                broker_account=_rbc_account(leg)))
    for ev in merger_pairs:
        rem, rc = ev.removal, ev.receipt
        rr = rbc_ratio_parts(rem.desc)
        if rr and rr[0] > 0 and rr[1] > 0:
            ratio_new, ratio_old = rr
        elif ev.ratio:
            # "XCH TO <other company>; 1 FOR 5" (no NEW = OLD phrase).
            ratio_new, ratio_old = ev.ratio, 1.0
        else:
            ratio_new, ratio_old = 1.0, 1.0
        oldm = _RBC_OLDCO_RE.search(rem.desc)
        src_raw = rem.symbol
        src = _rbc_ca_symbol(src_raw, rem.currency)
        if not _rbc_is_real_ticker(src_raw):
            oldco = _rbc_norm_company(oldm.group(1) if oldm
                                      else _rbc_removal_names(rem)[0])
            syms = (name_listings.get(oldco)
                    or name_listings.get(_rbc_norm_company(rem.symdesc)))
            resolved = _resolve(syms or {}, rem.currency,
                                f"merger removal {rem.symbol} on "
                                f"{rem.date}", rem.date) if syms else None
            if resolved:
                src = resolved
            elif not syms:
                print(
                    f"warning: RBC merger removal for "
                    f"{(oldm.group(1).strip() if oldm else rem.symbol)!r} is "
                    f"booked under temporary reorg symbol {rem.symbol!r}, and "
                    f"no other row in the statement trades under that company — "
                    f"the rollover / disposition has no source lot to act on. If "
                    f"this position isn't in the imported history, add a manual "
                    f"opening lot or a ticker.map GLOBAL line for "
                    f"{_rbc_ca_symbol(rem.symbol, rem.currency)}.",
                    file=sys.stderr,
                )
        tgt = _rbc_ca_symbol(rc.symbol, rc.currency)
        # SIGNED: a reversal CIL row cancels its posting (audit S063-19).
        cil_amount = max(0.0, sum(c.value for c in ev.cil))
        events.append(CorporateAction(
            date=rem.date, time='09:30:00', action_type='merger',
            source_symbol=src, source_isin=src,
            target_symbol=tgt, target_isin=tgt,
            ratio_new=ratio_new, ratio_old=ratio_old,
            qty_disposed=abs(rem.qty), qty_received=rc.qty,
            fmv=0.0, currency=rem.currency or 'USD',
            target_currency=rc.currency or 'USD',
            account=account,
            cash_in_lieu=cil_amount,
            cash_in_lieu_currency=(ev.cil[0].currency if ev.cil else ''),
            raw_descriptions=[rem.desc, rc.desc] + [c.desc for c in ev.cil],
            broker_account=_rbc_account(rem),
        ))

    # Spin-offs: a tax election (s.86.1 or an FMV dividend in kind).
    # A "REVERSE ENTRY" row cancels its posting (same symbol and
    # account, negated quantity, on or before it; the nearest), in
    # whichever statement of the account holds it — as the reorg legs'
    # step 1 does. A reversed-and-rebooked spin-off was offered twice and
    # the reversal read as a short parent (A2-0213).
    def _spin_key(x):
        return (x.date, x.symbol, round(x.qty, 9), x.desc, x.account)
    spin_all = [x for x in rows if getattr(x, 'cls', '') == 'spinoff']
    _own_spin = {_spin_key(x) for x in spin_all}
    spin_all += [x for f_rows in other_files for x in f_rows
                 if getattr(x, 'cls', '') == 'spinoff'
                 and _spin_key(x) not in _own_spin]
    reversed_ids: set = set()
    for rev in sorted((x for x in spin_all if x.qty < 0
                       and 'REVERSE ENTRY' in (x.desc or '').upper()),
                      key=lambda x: x.date):
        cands = [x for x in spin_all
                 if x.qty > 0 and id(x) not in reversed_ids
                 and x.symbol == rev.symbol
                 and abs(x.qty + rev.qty) < 1e-9 and x.date <= rev.date
                 and _rbc_same_account(x, rev)]
        if cands:
            orig = max(cands, key=lambda x: x.date)
            reversed_ids.update((id(orig), id(rev)))
    for r in rows:
        if getattr(r, 'cls', '') != 'spinoff' or id(r) in reversed_ids:
            continue
        if r.qty < 0:
            # Spun-off shares DEBITED: the account was short the parent
            # and owes them. Snapped to a long buy it booked a negative
            # dividend and a phantom long (audit S072-04).
            print(f"warning: RBC spin-off on {r.date} DEBITS {abs(r.qty):g} "
                  f"{r.symbol} — the parent was held SHORT. taxjson cannot "
                  f"book a spin-off on a short position: NOTHING was "
                  f"booked; record the owed shares by hand in a .tt file. "
                  f"{r.desc[:90]!r}", file=sys.stderr)
            continue
        m = _RBC_SPINOFF_RE.search(r.desc)
        parent_qty = _num_text(m.group(1), where=r.label()) if m else 0.0
        parent_code = m.group(2).strip() if m else ''
        parent_name = m.group(3).strip().lstrip('*') if m else ''
        what = f"spin-off parent {parent_name or parent_code!r} on {r.date}"
        syms = (name_listings.get(_rbc_norm_company(parent_name))
                if parent_name else None)
        if not syms and parent_name:
            # Fuzzy fallback (RBC appends 'COMMON STOCK' and the like, so
            # exact names rarely hit). BOTH names must be mostly covered
            # by the other: scoring only the shorter one let 'BROOKFIELD
            # CORP' match 'BROOKFIELD RENEWABLE CORP' (audit S072-02).
            def _cov(a, b):
                ta, tb = _rbc_name_tokens(a), _rbc_name_tokens(b)
                if not ta or not tb:
                    return 0.0
                return sum(1 for x in ta
                           if any(_rbc_tok_eq(x, y) for y in tb)) / len(ta)
            scored = {}
            for x in share_rows:
                if not x.symdesc or x.symbol == r.symbol:
                    continue
                sc = min(_cov(parent_name, x.symdesc),
                         _cov(x.symdesc, parent_name))
                if sc >= 0.6:
                    scored[x.symbol] = max(sc, scored.get(x.symbol, 0.0))
            if scored:
                top = max(scored.values())
                syms = {sy for sy, sc in scored.items() if sc == top}
        parent = _resolve(syms, '', what, r.date) if syms else None
        if not parent and not syms:
            print(f"warning: RBC spin-off parent {parent_name or parent_code!r} "
                  f"(SEC# {parent_code}) is not traded in this statement, so "
                  f"its ticker is unknown — a s.86.1 rollover's parent-ACB "
                  f"reduction would land on an empty pool. Include the "
                  f"statement that bought the parent.", file=sys.stderr)
        # The parent's OWN listing, never the spun-off row's currency: a
        # TSX parent whose spin-off arrives in USD was PARENT.US, and the
        # s.86.1 ACB reduction became a phantom gain (audit S019-05).
        src = parent or (parent_code or '(unknown parent)')
        tgt = _rbc_ca_symbol(r.symbol, r.currency)
        if rbc_is_temp_symbol(r.symbol) and not _renamed(tgt, renames):
            # Named as the books carry it (C135859.TO): the bare code in
            # a GLOBAL line matched nothing, and the warning used to stay
            # after the line was added (audit S072-03).
            print(f"warning: RBC spin-off on {r.date} is booked under the "
                  f"temporary code {tgt} ({r.symdesc or r.desc[:60]!r}) "
                  f"— once the listed ticker is known, add to ticker.map:  "
                  f"GLOBAL {tgt} <TICKER>.{tgt.rsplit('.', 1)[-1]}",
                  file=sys.stderr)
        events.append(CorporateAction(
            date=r.date, time='09:30:00', action_type='spinoff',
            source_symbol=src, source_isin=parent_code or src,
            target_symbol=tgt, target_isin=tgt,
            ratio_new=r.qty, ratio_old=parent_qty or 1.0,
            qty_disposed=0.0, qty_received=r.qty,
            fmv=0.0, currency=r.currency or 'USD',
            target_currency=r.currency or 'USD',
            account=account, raw_descriptions=[r.desc],
            broker_account=_rbc_account(r),
        ))
    events.sort(key=lambda e: (e.date, e.source_symbol))
    return events


parse_rbc_corporate_actions.accepts_context = True
parse_rbc_corporate_actions.accepts_renames = True


def _renamed(symbol: str, renames: Optional[Dict[str, str]]) -> bool:
    """Whether the project's ticker.map renames `symbol`."""
    if not renames:
        return False
    from taxjson.bin.taxjson_ticker_map import map_symbol
    return map_symbol(symbol, renames) != symbol



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


def hint_value_problem(key: str, value: Any) -> Optional[str]:
    """Why an election hint value is unusable, or None. Every hint (an
    FMV per share, an allocated ACB, cash boot, a source basis) is a
    non-negative amount: a negative one created cost basis from nothing
    or booked negative dividend income, and nan/inf was saved and only
    failed the next run (S039-00)."""
    import math
    if isinstance(value, bool):
        return f"{key}={value!r} is not a number"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return f"{key}={value!r} is not a number"
    if not math.isfinite(f):
        return f"{key}={value!r} is not a finite number"
    if f < 0:
        return (f"{key}={value!r} is negative — every election hint is "
                f"an amount (enter it as a positive figure)")
    return None


class ManifestError(ValueError):
    """An elections manifest taxjson cannot read: not UTF-8, not JSON,
    or not the documented shape. Every command that reads one prints it
    as a one-line error (the file is hand-edited and committed, so a
    merge conflict or a typo reaches this — audits S072-05, S072-16)."""


def election_keys(country: str) -> set:
    """Every election key the country's rules know (plus `ignore`)."""
    keys = {IGNORE_ELECTION[0]}
    for rule in RULES_BY_COUNTRY[_canon(country)].values():
        keys.update(k for k, _ in rule.options)
    return keys


_SUMMARY_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2}) (\w+): (\S+) \u2192 (\S+) '
    r'\(([-\d.e+]+)-for-([-\d.e+]+)')


def _summary_identifies(summary: str, ev: "CorporateAction",
                        roots: bool = True) -> bool:
    """Whether a saved election's `summary` (the event's `summary()` when
    it was elected) describes `ev`: same date, action type and ratio,
    and — with `roots` — the same symbol roots."""
    m = _SUMMARY_RE.match((summary or '').strip())
    if not m:
        return False
    date, kind, src, tgt, rn, ro = m.groups()
    try:
        same_ratio = (abs(float(rn) - float(ev.ratio_new)) < 1e-9
                      and abs(float(ro) - float(ev.ratio_old)) < 1e-9)
    except ValueError:
        return False
    if (date != ev._normalize_date(ev.date) or kind != ev.action_type
            or not same_ratio):
        return False
    if not roots:
        return True
    root = CorporateAction._sym_root
    return (root(src), root(tgt)) == (root(ev.source_symbol),
                                      root(ev.target_symbol))


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
        self.migration_notes: List[str] = []

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls({})
        try:
            raw = path.read_text(encoding='utf-8').strip()
        except UnicodeDecodeError as exc:
            raise ManifestError(
                f"manifest at {path} is not UTF-8 text (byte "
                f"{exc.start}: {exc.reason}) — re-save it as UTF-8"
            ) from None
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
            raise ManifestError(
                f"manifest at {path} is not valid JSON ({exc.msg} at line "
                f"{exc.lineno} col {exc.colno}). If this file used to be a "
                f"CSV or other format, point --manifest at the correct path "
                f"or delete the file to start fresh."
            ) from None
        if not isinstance(data, dict):
            raise ManifestError(
                f"manifest at {path} must be a JSON object with an "
                f"'elections' key; got top-level {type(data).__name__}"
            )
        elections = data.get('elections') or {}
        if not isinstance(elections, dict):
            raise ManifestError(
                f"manifest at {path}: 'elections' must be a JSON object "
                f"keyed by event id; got {type(elections).__name__}")
        records = {}
        for eid, rec in elections.items():
            # A bare string (or list) record raised AttributeError from
            # deep inside the corp-actions stage (audit S072-16).
            if not isinstance(rec, dict):
                raise ManifestError(
                    f"manifest at {path}: election {eid} must be a JSON "
                    f"object like {{\"election\": \"...\"}}; got "
                    f"{type(rec).__name__} {rec!r:.40}")
            if not isinstance(rec.get('election', ''), str):
                raise ManifestError(
                    f"manifest at {path}: election {eid}: 'election' must "
                    f"be a string; got {rec.get('election')!r:.40}")
            if not isinstance(rec.get('hints') or {}, dict):
                raise ManifestError(
                    f"manifest at {path}: election {eid}: 'hints' must be "
                    f"a JSON object; got {rec.get('hints')!r:.40}")
            for hk, hv in (rec.get('hints') or {}).items():
                prob = hint_value_problem(hk, hv)
                if prob:
                    raise ManifestError(f"manifest at {path}: election "
                                        f"{eid}: hint {prob}")
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
        """Rekey records saved under an older id scheme to the current
        ids. Returns the number migrated (caller saves when > 0).
        Elections are the non-rebuildable user artifact — an id-scheme
        change, or renaming the account, must never orphan them; and a
        migration must never hand one event's election to another.

        A record whose id is the CURRENT id of an event of this run is
        that event's own and is never moved (A2-0064, A2-0557). For an
        event whose current id has no record, the aliases tried, in
        order: the pre-2026-07 opaque 12-hex hash; the account-salted
        id of the R1-301 scheme change (`account_salted_event_id`,
        computed with the CURRENT account name); a record that differs
        only in the readable symbol roots; and last, for a manifest
        written under ANOTHER account name, the one record with the
        event's date and symbol roots that no event of this run claims
        and whose saved summary names the same action type and ratio
        (A2-0975, A2-0168). An alias several events of this run share
        (ISIN-less events of one date, type and ratio hash alike) is
        resolved only by the record's saved summary (A2-0217, A2-0978).
        Every adoption, and every record that could belong to more than
        one event, is described in `self.migration_notes` (the caller
        prints them)."""
        self.migration_notes: List[str] = []
        migrated = 0
        current = {ev.event_id for ev in events}
        by_id = {}
        for ev in events:
            by_id.setdefault(ev.event_id, ev)
        uniq = list(by_id.values())
        owners: Dict[str, List["CorporateAction"]] = defaultdict(list)
        for ev in uniq:
            for alias in {ev.legacy_event_id(), ev.account_salted_event_id()}:
                if alias != ev.event_id:
                    owners[alias].append(ev)
        listed: set = set()

        def _orphan(eid: str, cands: List["CorporateAction"]) -> None:
            if eid in listed:
                return
            listed.add(eid)
            rec = self.records[eid]
            self.migration_notes.append(
                f"election {eid} ({rec.election}"
                f"{'; ' + rec.summary if rec.summary else ''}) was saved "
                f"under an older id that fits more than one event of this "
                f"run ({', '.join(e.event_id for e in cands)}) — not "
                f"guessed. Set it again with `taxjson elect <account> "
                f"--set <event id>={rec.election}` (the hints are in the "
                f"manifest), then delete the old entry.")

        def _adopt(eid: str, ev: "CorporateAction", note: str = '') -> None:
            nonlocal migrated
            rec = self.records.pop(eid)
            rec.event_id = ev.event_id
            self.records[ev.event_id] = rec
            migrated += 1
            if note:
                self.migration_notes.append(
                    f"election {eid} ({rec.election}) {note}; carried "
                    f"over to {ev.event_id}")

        unclaimed = []
        for ev in uniq:
            if ev.event_id in self.records:
                continue
            done = False
            for alias in (ev.legacy_event_id(), ev.account_salted_event_id()):
                if (alias == ev.event_id or alias in current
                        or alias not in self.records):
                    continue
                cands = owners[alias]
                if len(cands) == 1:
                    _adopt(alias, ev)
                    done = True
                    break
                named = [e for e in cands
                         if _summary_identifies(self.records[alias].summary,
                                                e)]
                if named == [ev]:
                    _adopt(alias, ev, "was saved under an id several "
                           "events share; its summary names this one")
                    done = True
                    break
                if not named:
                    _orphan(alias, cands)
            if done:
                continue
            hit = self._pop_resymbolled(ev, uniq, current)
            if hit is not None:
                eid, note = hit
                _adopt(eid, ev, note)
                continue
            unclaimed.append(ev)
        for ev in unclaimed:
            if ev.event_id in self.records:
                continue
            prefix = ev.event_id.rsplit('-', 1)[0] + '-'
            if sum(1 for e in uniq if e.event_id.startswith(prefix)) != 1:
                continue
            hits = [eid for eid in self.records
                    if eid.startswith(prefix) and eid not in current
                    and eid.count('-') == ev.event_id.count('-')]
            if len(hits) != 1:
                continue
            rec = self.records[hits[0]]
            if not _summary_identifies(rec.summary, ev, roots=False):
                self.migration_notes.append(
                    f"election {hits[0]} ({rec.election}) has this "
                    f"run's {ev.event_id} date and symbols but "
                    + ("no saved summary" if not rec.summary else
                       f"a different action type or ratio ({rec.summary})")
                    + " — not carried over. If it is the same event, set "
                    f"it again with `taxjson elect <account> --set "
                    f"{ev.event_id}={rec.election}`.")
                continue
            _adopt(hits[0], ev,
                   "was saved under another id (the account was renamed, "
                   "or an older id scheme); its date, symbols, type and "
                   "ratio match")
        return migrated

    def _pop_resymbolled(self, ev: "CorporateAction",
                         events: Optional[List["CorporateAction"]] = None,
                         current: Optional[set] = None
                         ) -> Optional[Tuple[str, str]]:
        """(record id, note) of a record whose id differs from `ev`'s only
        in the readable symbol roots, or None. The date prefix and the
        4-hex suffix (hashed from ISINs and ratio — or, in the pre-R1-301
        scheme, also the account name) are the event's identity; the
        roots are display only, and an extractor that learns a better
        ticker (a broker-internal parent code resolved to the traded
        symbol) changes them. Requires exactly one candidate record, no
        candidate that is the current id of an event of this run (that
        record is its own event's — A2-0557), and no other event of this
        run with the same date and suffix: ISIN-less events of one date
        and ratio share the suffix, and the record moved to whichever
        came first (A2-0064). The record is NOT removed here."""
        def _parts(e):
            if e.event_id.count('-') != 3:
                return '', set()
            date, _s, _t, suffix = e.event_id.split('-', 3)
            return date, {suffix,
                          e.account_salted_event_id().rsplit('-', 1)[-1]}
        date, suffixes = _parts(ev)
        if not date:
            return None
        current = current if current is not None else {ev.event_id}
        hits = [eid for eid in self.records
                if eid.count('-') == 3
                and eid.startswith(f"{date}-")
                and eid.rsplit('-', 1)[-1] in suffixes]
        if len(hits) != 1 or hits[0] in current:
            return None
        sfx = hits[0].rsplit('-', 1)[-1]
        rivals = [e for e in (events or [])
                  if e.event_id != ev.event_id
                  and _parts(e)[0] == date and sfx in _parts(e)[1]]
        if rivals:
            rec = self.records[hits[0]]
            named = [e for e in rivals + [ev]
                     if _summary_identifies(rec.summary, e)]
            if named != [ev]:
                return None
        old = '-'.join(hits[0].split('-')[1:3])
        new = '-'.join(ev.event_id.split('-')[1:3])
        return hits[0], (f"was saved when the event's symbols read "
                         f"{old} (now {new})")

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


# Reserved key resolve_event puts on the (copied) hints dict: a currency
# converter `fx(amount, from_cur, to_cur, date) -> Optional[float]` built
# from the project's rates file. Never persisted in the manifest.
FX_HINT = '__fx__'


def _convert(hints: dict, amount: float, from_cur: str, to_cur: str,
             date: str) -> Optional[float]:
    """`amount` in `from_cur` expressed in `to_cur` at the `date` rate,
    or None when no converter / rate is available."""
    from_cur, to_cur = (from_cur or '').upper(), (to_cur or '').upper()
    if not amount or not from_cur or not to_cur or from_cur == to_cur:
        return amount
    fx = (hints or {}).get(FX_HINT)
    if fx is None:
        return None
    try:
        return fx(amount, from_cur, to_cur, date)
    except Exception:
        return None


def _consideration(event: CorporateAction, hints: dict
                   ) -> Tuple[float, str, str]:
    """(value, currency, source) of the NEW SHARES received in an
    exchange — the one fair value both legs use (s.69 / the general
    rule: the old shares' proceeds are the FMV of what was received,
    and that same FMV is the new shares' cost). Preference: the broker's
    IN-leg value (what was received), the user's fmv_per_share hint,
    then the broker's OUT-leg value less any cash-in-lieu."""
    hint_ps = float((hints or {}).get('fmv_per_share') or 0.0)
    if event.target_fmv > 0:
        return (event.target_fmv,
                event.target_fmv_currency or event.target_currency
                or event.currency, 'broker in-leg value')
    if hint_ps > 0:
        return (hint_ps * event.qty_received,
                event.target_currency or event.currency,
                'fmv_per_share hint')
    if event.fmv > 0:
        cil = float(getattr(event, 'cash_in_lieu', 0.0) or 0.0)
        if cil:
            cil = _convert(hints, cil, event.cash_in_lieu_currency
                           or event.target_currency or event.currency,
                           event.currency, event.date) or 0.0
        return (max(event.fmv - cil, 0.0), event.currency,
                'broker out-leg value')
    return (0.0, event.target_currency or event.currency, 'none')


def _emit_taxable_exchange(event: CorporateAction, hints: dict,
                           *, description_base: str) -> List[dict]:
    """Country-neutral taxable exchange: sell source at FMV, buy target at
    FMV. Realizes a capital gain/loss against the source's existing pool.
    Canada wraps this as the no-election merger default; the US wraps it
    as the fully-taxable §1001 exchange — only the description differs.

    ONE valuation (see `_consideration`): proceeds of the old shares =
    value of the new shares + cash-in-lieu; cost of the new shares =
    that same value. The broker's OUT-leg and IN-leg values used to feed
    the two legs separately, leaving their difference as a permanent
    phantom gain or loss; and a hint-based figure was booked as SOURCE-
    currency proceeds and TARGET-currency cost at once. Each leg is
    expressed in its own listing's currency, converted at the event-date
    rate (the project's rates file) — so both legs carry the same
    base-currency amount. When no rate is available the leg is booked
    in the consideration's currency instead (the conversion stage values
    it; a native-currency view of that pool is then mixed) with a
    warning.
    """
    value, value_cur, _src = _consideration(event, hints)
    cil_amt = float(getattr(event, 'cash_in_lieu', 0.0) or 0.0)
    cil_cur = (event.cash_in_lieu_currency or event.target_currency
               or event.currency)
    src_cur = event.currency
    target_currency = event.target_currency or event.currency

    def _in(amount, from_cur, to_cur, what):
        """(amount, currency) for a row wanting `to_cur`."""
        conv = _convert(hints, amount, from_cur, to_cur, event.date)
        if conv is None:
            print(f"warning: {what} of the {event.source_symbol}→"
                  f"{event.target_symbol} exchange on {event.date}: no "
                  f"{from_cur}->{to_cur} rate available, so the row is "
                  f"booked in {from_cur} (the conversion stage values "
                  f"it at that date's rate).", file=sys.stderr)
            return amount, from_cur
        return conv, to_cur

    # Keyed on the SHARE consideration: cash-in-lieu of a fraction is
    # not a value for the whole exchange, and gating on it let the
    # documented "0 to defer" book a near-full-ACB fake loss silently
    # whenever RBC paid cash in lieu (audits S020-06, S074-00).
    if value <= 0:
        # Name the real cause: a saved fmv_per_share of 0 is not "no
        # hint given" (A2-0974); the remedy is the run-level line's.
        why = ("the saved election values the new shares at 0 "
               "(fmv_per_share=0)" if 'fmv_per_share' in (hints or {})
               else "the broker booked $0 and no fmv_per_share hint was "
                    "given")
        print(
            f"warning: taxable merger {event.source_symbol}→"
            f"{event.target_symbol} on {event.date} has NO fair market "
            f"value for the new shares ({why}) — emitting zero-valued "
            f"rows: the SELL realizes a fake loss (proceeds are only the "
            f"cash-in-lieu, if any) and the BUY enters at $0 basis. Set "
            f"its value: taxjson elect {event.account} --set "
            f"{event.event_id}=<election> --hint fmv_per_share=<value>",
            file=sys.stderr,
        )

    # Proceeds in the source listing's currency: shares + cash-in-lieu.
    proceeds, proceeds_cur = _in(value, value_cur, src_cur,
                                 'the proceeds')
    if cil_amt:
        if proceeds_cur == src_cur:
            c, c_cur = _in(cil_amt, cil_cur, src_cur, 'the cash-in-lieu')
        else:
            c, c_cur = _in(cil_amt, cil_cur, proceeds_cur,
                           'the cash-in-lieu')
        if c_cur == proceeds_cur:
            proceeds += c
        else:                     # cannot sum across currencies: keep loud
            print(f"warning: cash-in-lieu {cil_amt:g} {cil_cur} of "
                  f"{event.source_symbol} on {event.date} could not be "
                  f"added to the proceeds (no rate) — add it by hand.",
                  file=sys.stderr)
    cost, cost_cur = _in(value, value_cur, target_currency,
                         'the new shares\' cost')

    price_disposed = proceeds / event.qty_disposed if event.qty_disposed else 0.0

    # Snap fractional residue to broker-style cash-in-lieu (see helper).
    whole_qty, fmv_acquired, frac_qty = _snap_received(event,
        event.qty_received, cost,
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
            'currency': proceeds_cur,
            'price': price_disposed,
            'net_amount': proceeds,
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
            'currency': cost_cur,
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
            f"(taxable disposition; gain reported — no s. 85.1 rollover)"
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
    """Canada wrapper: the s. 85.1 share-for-share rollover (s. 85.1(1)
    Canadian purchaser, s. 85.1(5) foreign-for-foreign). Automatic when
    it applies — no election is filed; the vendor opts out by reporting
    the gain. The key keeps its historical name for saved manifests."""
    return _emit_basis_carryover_rename(
        event, hints,
        statute_note="s. 85.1 rollover (automatic; no gain reported)",
        cil_note="s. 85.1 rollover",
    )


# The latest clock time an event may carry: its emitters add one second
# to it (a merger's spin-off leg is itself one second after the merger,
# so a merger source time is held to one second earlier still).
_LATEST_EVENT_TIME = '23:59:58'


def _clamp_time(t: str, latest: str) -> str:
    """`t` as HH:MM:SS, no later than `latest`; a blank time is the
    start of the day. An unparseable time is returned unchanged."""
    if not (t or '').strip():
        return '00:00:00'
    try:
        h, m, sec = (int(x) for x in t.split(':'))
    except (ValueError, AttributeError):
        return t
    norm = f"{h:02d}:{m:02d}:{sec:02d}"
    return min(norm, latest)


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
            "Report the merger as a sale: realizes your capital gain/loss "
            "on the old shares THIS year; the new shares start at FMV "
            "cost. Required when s. 85.1 does not apply — e.g. a Canadian "
            "company acquired by a FOREIGN purchaser for its shares, or "
            "consideration other than the purchaser's shares beyond a "
            "fractional-share payout. Where s. 85.1 does apply, reporting "
            "the gain (or loss) in your return is how you opt out of the "
            "rollover.",
        ),
        (
            'rollover_s_85_1_5',
            "Defer the gain (s. 85.1 share-for-share exchange): the new "
            "shares inherit your old cost basis, so no gain this year — "
            "you pay when you sell them. The rollover is AUTOMATIC when "
            "it applies — there is no election form; you simply do not "
            "report a gain for the exchange. It applies to a Canadian "
            "purchaser issuing its own shares (s. 85.1(1)) or to one "
            "foreign corporation's shares exchanged for another foreign "
            "corporation's shares (s. 85.1(5)); NOT to a Canadian target "
            "acquired by a foreign purchaser, and not when you received "
            "other consideration beyond a fractional-share payout.",
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

    if tgt_fmv <= 0 and event.qty_received > 0:
        # Without the new shares' value the realized gain is understated
        # (boot - basis), so the §356 gain is capped too low and the
        # §358 basis understated by the same amount (audit S073-22).
        print(
            f"warning: boot merger {event.source_symbol}→"
            f"{event.target_symbol} on {event.date} has no value for the "
            f"new shares (fmv_per_share=0 and none reported) — the "
            f"realized gain counts only the cash, so the recognized gain "
            f"and the new basis are understated. Re-run `taxjson elect "
            f"--redo` with the FMV per new share.",
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
            "Fully taxable exchange (§1001): old shares sold at FMV, new "
            "shares acquired at FMV. Only when the merger does NOT qualify "
            "as a §368(a) reorganization (the company's Form 8937 says "
            "which). A qualifying reorganization is tax-free by law "
            "(§354), not by choice: picking this for one books a gain or "
            "loss the Code does not recognize.",
        ),
        (
            'reorg_368',
            "§368(a) tax-free reorganization (all-stock): no gain "
            "recognized; basis carries to the new shares (§358) and the "
            "holding period tacks (§1223(1)). "
            + _US_SIGNIFICANT_HOLDER.format(reg="1.368-3"),
        ),
        (
            'reorg_368_boot',
            "§368(a) reorganization with CASH BOOT (§356): gain recognized "
            "to the lesser of your realized gain or the cash received; "
            "losses are NOT recognized. New basis = old basis − boot + "
            "gain recognized (§358(a)). You supply the boot and your "
            "total pre-merger basis. Holding dates reset in this model. "
            + _US_SIGNIFICANT_HOLDER.format(reg="1.368-3"),
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
                f"(taxable §1001 exchange; election={opt})"
            ))
    ),
)


USA_SPINOFF = RuleSpec(
    options=[
        (
            'taxable_distribution_301',
            "§301 distribution: the spun-off shares are taxable income at "
            "FMV on receipt (a dividend to the extent of earnings & "
            "profits — see your 1099-DIV); cost basis of the new position "
            "= FMV. You supply the per-share FMV. Only when the spin-off "
            "does NOT qualify under §355 (the company's Form 8937 says "
            "which).",
        ),
        (
            'tax_free_355',
            "§355 tax-free spinoff (applies by law when the spin-off "
            "qualifies): no current income; basis is allocated between "
            "parent and spin-co in proportion to relative FMV "
            "(§358(b)-(c)) — the company's Form 8937 publishes the "
            "allocation. You supply the dollar basis moved to the "
            "spin-co. "
            + _US_SIGNIFICANT_HOLDER.format(reg="1.355-5"),
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
                f"(§301 taxable distribution at FMV; election={opt})"
            ))
    ),
)


def spinoff_broker_value(event: CorporateAction) -> Tuple[float, str]:
    """(total value, currency) the broker reported for the spun-off
    shares (IB's Value), or (0, '') when it reported none (RBC and
    Questrade book spin-offs at $0)."""
    if (event.target_fmv or 0) > 0:
        return (event.target_fmv, event.target_fmv_currency
                or event.target_currency or event.currency)
    if (event.fmv or 0) > 0:
        return event.fmv, event.currency
    return 0.0, ''


def _emit_distribution(event: CorporateAction, hints: dict,
                       *, description_base: str) -> List[dict]:
    """Country-neutral taxable distribution: the received shares are
    income at FMV on the receipt date, and the new position's cost basis
    is that FMV. Canada wraps this as the deemed-dividend spinoff
    default; the US as a §301 distribution.

    The value: the user's `fmv_per_share` hint when it is positive,
    else the broker's own value for the new shares (IB reports one —
    it used to be ignored), else 0. A zero-valued taxable spin-off is
    booked so the pipeline runs, and `taxjson run` warns about it on
    every run until a value is supplied."""
    fmv_per_share = float(hints.get('fmv_per_share') or 0.0)
    currency = event.currency
    if fmv_per_share > 0:
        total_fmv = fmv_per_share * event.qty_received
    else:
        total_fmv, bcur = spinoff_broker_value(event)
        if total_fmv > 0 and bcur and bcur != currency:
            conv = _convert(hints, total_fmv, bcur, currency, event.date)
            if conv is None:
                currency = bcur           # book in the value's currency
            else:
                total_fmv = conv
        fmv_per_share = (total_fmv / event.qty_received
                         if event.qty_received else 0.0)
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
    if total_fmv <= 0:
        description += "; ZERO VALUE — no FMV given"
    rows = [
        {
            'action': 'DIVIDEND',
            'date': event.date, 'time': event.time, 'date_settle': event.date,
            'symbol': event.target_symbol,
            'quantity': 0.0, 'currency': currency,
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
            'quantity': whole_qty, 'currency': currency,
            'price': fmv_per_share, 'net_amount': adjusted_fmv, 'fee': 0.0,
            'account': event.account, 'description': description,
        })
    return rows


def zero_value_merger_rows(rows: List[dict]) -> List[dict]:
    """New-share BUY rows of taxable merger elections booked at $0 —
    the deferred-FMV state (with or without cash-in-lieu) `taxjson run`
    keeps loud on every run, like a $0 spin-off."""
    return [r for r in rows
            if r.get('action') == 'BUYSELL'
            and float(r.get('quantity') or 0.0) > 0
            and r.get('corp_election') in ('taxable_disposition',
                                           'taxable_exchange')
            and abs(float(r.get('net_amount') or 0.0)) < 0.005]


# The basis-allocating spin-off elections and the hint each one takes.
ALLOCATED_BASIS_HINT = {'rollover_s_86_1': 'allocated_acb_cad',
                        'tax_free_355': 'allocated_acb'}


def zero_basis_rollover_rows(rows: List[dict]) -> List[dict]:
    """Spun-off share BUY rows of an s.86.1 / §355 election booked with
    $0 allocated basis: the parent keeps its whole cost and the gain
    moves to the spin-off's sale (audits S073-21, S074-04). `taxjson
    run` keeps it loud on every run, like a $0 spin-off."""
    return [r for r in rows
            if r.get('action') == 'BUYSELL'
            and float(r.get('quantity') or 0.0) > 0
            and r.get('corp_election') in ALLOCATED_BASIS_HINT
            and abs(float(r.get('net_amount') or 0.0)) < 0.005]


def zero_value_spinoff_rows(rows: List[dict]) -> List[dict]:
    """DIVIDEND rows of taxable spin-off elections booked at $0 — the
    deferred-FMV state `taxjson run` keeps loud on every run."""
    return [r for r in rows
            if r.get('action') == 'DIVIDEND'
            and r.get('corp_election') in ('taxable_deemed_dividend',
                                           'taxable_distribution_301')
            and abs(float(r.get('net_amount') or 0.0)) < 0.005]


def _canada_spinoff_deemed_dividend(event: CorporateAction, option: str, hints: dict) -> List[dict]:
    """Canada wrapper: default CRA treatment — foreign dividend at FMV."""
    return _emit_distribution(
        event, hints,
        description_base=(
            f"Spinoff {event.source_symbol}→{event.target_symbol} "
            f"(deemed dividend at FMV; election={option})"
        ),
    )


def _emit_allocated_basis_spinoff(event: CorporateAction, hints: dict,
                                  *, description_base: str,
                                  allocated_acb: Optional[float] = None,
                                  alloc_cur: Optional[str] = None
                                  ) -> List[dict]:
    """Country-neutral basis-allocated spinoff: part of the parent's cost
    basis moves to the spun-off position; no current-year tax. Canada
    wraps this as the s. 86.1 rollover; the US as the §355 tax-free
    spinoff (basis allocation per §358(b)).

    The amount is the `allocated_acb` hint, in the event's currency,
    unless the country's wrapper passes `allocated_acb` / `alloc_cur`
    itself: Canada's s.86.1(3) `allocated_acb_cad` (booked in CAD) is
    read ONLY by the Canada wrapper — the shared emitter never sees a
    Canadian hint, so the US §355 rule cannot book one (partition
    ENGINE-08)."""
    if allocated_acb is None:
        allocated_acb = float((hints or {}).get('allocated_acb') or 0.0)
    alloc_cur = alloc_cur or event.currency
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
    if abs(allocated_acb) < 0.005 and event.qty_received > 0:
        # A spin-off with value is never allocated $0 (s.86.1(2) /
        # §358(b) apportion by relative FMV): the parent kept its whole
        # cost and the spun-off shares booked at $0, with no word
        # (audits S073-21, S074-04). Loud; `taxjson run` repeats it.
        print(f"warning: spin-off {event.source_symbol}→"
              f"{event.target_symbol} on {event.date}: the basis-allocating "
              f"election carries an allocated cost of 0 — the spun-off "
              f"shares get $0 cost and the parent keeps all of it, which "
              f"moves gain from the parent's sale to the spin-off's. "
              f"Enter the allocated amount (parent cost x the spin-off's "
              f"share of the combined FMV).", file=sys.stderr)
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
            'quantity': whole_qty, 'currency': alloc_cur,
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
            'symbol': event.source_symbol, 'currency': alloc_cur,
            'net_amount': -adjusted_acb,
            'account': event.account,
            'description': description + ' (parent ACB reduction)',
        })
    return rows


def _canada_spinoff_rollover_s_86_1(event: CorporateAction, option: str, hints: dict) -> List[dict]:
    """Canada wrapper: s. 86.1 foreign-spinoff rollover. Only valid for
    an "eligible distribution" under ITA s. 86.1(2) — among other
    conditions the distributing corporation must provide the required
    information to the Minister; CRA publishes the foreign spin-offs it
    has accepted on canada.ca — AND when the user elects in writing with
    their return."""
    if 'allocated_acb_cad' not in hints and (event.currency or 'CAD'
                                             ).upper() != 'CAD':
        print(f"warning: s.86.1 spin-off {event.source_symbol}→"
              f"{event.target_symbol} on {event.date}: the election "
              f"carries the legacy `allocated_acb` in {event.currency}, "
              f"converted at the spin-off date's rate. s.86.1(3) splits "
              f"the parent's CAD cost amount: re-elect with "
              f"`--hint allocated_acb_cad=<CAD amount>` (parent ACB in "
              f"CAD x the spin-off's share of the combined FMV).",
              file=sys.stderr)
    # s.86.1(3): the parent's CAD cost amount x the spin-off's share of
    # the combined FMV, booked in CAD so the pools get exactly that
    # figure. The legacy `allocated_acb` is in the event's currency and
    # converts at the spin-off date's rate — which moves the FX drift
    # since purchase between the pools (audits S019-09, S072-15).
    cad = 'allocated_acb_cad' in (hints or {})
    rows = _emit_allocated_basis_spinoff(
        event, hints,
        description_base=(
            f"Spinoff {event.source_symbol}→{event.target_symbol} "
            f"(s. 86.1 rollover elected; ACB allocated from parent)"
        ),
        allocated_acb=(float(hints.get('allocated_acb_cad') or 0.0)
                       if cad else None),
        alloc_cur='CAD' if cad else None,
    )
    if cad:
        rows = _cad_allocation_in_listing_currency(event, hints, rows)
    return rows


def _cad_allocation_in_listing_currency(event: CorporateAction,
                                        hints: dict,
                                        rows: List[dict]) -> List[dict]:
    """Express the s.86.1(3) CAD allocation rows in each leg's listing
    currency at the spin-off date's rate (the parent ADJUST in the
    event's currency, the spun-off BUYSELL in the target's). The
    conversion stage values a row at that same date's rate from the
    same rates file, so the converted books get back exactly the CAD
    figure the user entered, while the native-currency (raw) view of a
    USD pool stays in one currency. Booking the rows in CAD on a USD
    listing made the raw holdings pass abort `taxjson run` (A2-0002,
    a regression of S072-15) or skip the holdings refresh with a false
    'rollover rename' message (A2-0215, A2-0967). With no rate the rows
    stay in CAD and the run skips only the native view, loudly."""
    out = []
    for r in rows:
        cur = ((event.target_currency or event.currency)
               if r['action'] == 'BUYSELL' else event.currency)
        cur = (cur or 'CAD').upper()
        if cur == 'CAD' or (r.get('currency') or '').upper() != 'CAD':
            out.append(r)
            continue
        cad_amt = float(r.get('net_amount') or 0.0)
        conv = _convert(hints, cad_amt, 'CAD', cur, event.date)
        if conv is None:
            print(f"warning: s.86.1 spin-off {event.source_symbol}→"
                  f"{event.target_symbol} on {event.date}: no CAD->{cur} "
                  f"rate available, so the {r['symbol']} row is booked in "
                  f"CAD (the tax books are right; the native-currency "
                  f"holdings view of that {cur} pool is skipped).",
                  file=sys.stderr)
            return rows
        r = dict(r, currency=cur, net_amount=conv)
        qty = float(r.get('quantity') or 0.0)
        if r['action'] == 'BUYSELL' and qty:
            r['price'] = conv / qty
        r['description'] = (f"{r.get('description', '')} "
                            f"[{abs(cad_amt):.2f} CAD allocated, booked "
                            f"in {cur} at the {event.date} rate]")
        out.append(r)
    return out


CANADA_SPINOFF = RuleSpec(
    options=[
        (
            'taxable_deemed_dividend',
            "Report the received shares as a dividend at FMV — the "
            "treatment that applies if you file nothing with CRA. "
            "Taxable income THIS year; the new shares start at FMV "
            "cost. The broker's own value is used when it reported one "
            "(IB); otherwise you supply the per-share FMV (broker "
            "statement or the closing price on the distribution date).",
        ),
        (
            'rollover_s_86_1',
            "Defer the income (s. 86.1 foreign-spinoff rollover): part "
            "of the parent's cost basis moves to the spun-off shares, "
            "no tax this year. Only valid if the spinoff is on CRA's "
            "s. 86.1 eligibility list AND you file the election with "
            "your return. You supply the CAD cost to allocate: the "
            "parent's ACB in CAD immediately before the distribution "
            "(`taxjson list` shows it) times the spin-off's share of the "
            "combined fair market value right after it (s. 86.1(3)). A "
            "company's Form 8937 percentage is a US figure and can "
            "differ. A Canadian parent's tax-deferred "
            "spin-off (a butterfly reorganization) is booked the same "
            "way: pick this and enter the allocated ACB.",
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
         "The broker reported no value for this spin-off. Enter 0 to "
         "defer this number — the rows will be zero-valued and every "
         "`taxjson run` warns until you set it.",
         lambda ev: spinoff_broker_value(ev)[0] <= 0),
    ],
    'rollover_s_86_1': [
        ('allocated_acb_cad',
         "Cost (in CAD) moved from the parent to the spun-off shares, per "
         "s. 86.1(3): the parent's ACB in CAD immediately before the "
         "distribution (`taxjson list`) x FMV of the spun-off shares / "
         "(FMV of the parent + FMV of the spun-off shares) right after "
         "it. Booked in CAD — never converted at the spin-off date."),
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
         "(sets the taxable amount and the new cost basis). The broker "
         "reported no value for this spin-off. Enter 0 to defer this "
         "number — the rows will be zero-valued and every `taxjson run` "
         "warns until you set it.",
         lambda ev: spinoff_broker_value(ev)[0] <= 0),
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


def _canon(country: str) -> str:
    """lib/country.canonical_country: 'us' -> 'usa'; unknown raises."""
    from taxjson.lib.country import canonical_country
    return canonical_country(country)


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
        rule = RULES_BY_COUNTRY[_canon(country)].get(ev.action_type)
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


# Hint keys older manifests carry that the emitters still honour.
LEGACY_HINT_KEYS: Dict[str, Dict[str, str]] = {
    # key -> the current key it stands in for
    'rollover_s_86_1': {'allocated_acb': 'allocated_acb_cad'},
}


def check_hints(event: CorporateAction, election_key: str,
                hints: Optional[dict]) -> None:
    """Refuse a saved election whose hints the emitters would not read —
    an unknown key (`fmv` for `fmv_per_share`, `allocated_ACB`) — and
    warn about a missing needed one. Every consumer reads `hints.get(key) or 0`, so a
    misspelled key in a hand-edited manifest silently booked a $0
    dividend, a $0-cost lot or a rollover moving no cost — while `taxjson
    elect --hint` refuses the same key (audit S072-07)."""
    import difflib
    specs = HINTS_BY_ELECTION.get(election_key, [])
    legacy = LEGACY_HINT_KEYS.get(election_key, {})
    allowed = {h[0] for h in specs} | set(legacy)
    given = {k for k in (hints or {}) if k != FX_HINT}
    unknown = sorted(given - allowed)
    if unknown:
        tips = []
        for k in unknown:
            near = difflib.get_close_matches(k, sorted(allowed), n=1,
                                             cutoff=0.5) \
                or difflib.get_close_matches(k.lower(), sorted(allowed),
                                             n=1, cutoff=0.5)
            tips.append(f"{k!r}" + (f" (did you mean {near[0]!r}?)"
                                    if near else ""))
        raise ValueError(
            f"election {election_key!r} for event {event.event_id} has "
            f"unknown hint(s) {', '.join(tips)} — it takes: "
            f"{', '.join(sorted(h[0] for h in specs)) or 'none'}. Fix the "
            f"manifest or re-elect with `taxjson elect --set`.")
    have = given | {legacy[k] for k in given if k in legacy}
    missing = [h[0] for h in specs
               if h[0] not in have and (len(h) < 3 or h[2](event))]
    if missing:
        # Loud, not fatal: a missing value books the documented $0
        # "deferred" rows, which `taxjson run` keeps warning about.
        print(f"warning: election {election_key!r} for event "
              f"{event.event_id} has no {', '.join(missing)} — the rows "
              f"are booked at $0 until you set it: `taxjson elect --set "
              f"{event.event_id}={election_key} "
              + ' '.join(f'--hint {k}=<value>' for k in missing) + "`.",
              file=sys.stderr)


def resolve_event(
    event: CorporateAction,
    election_key: str,
    country: str = 'canada',
    hints: Optional[dict] = None,
    fx: Optional[Callable[[float, str, str, str], Optional[float]]] = None,
) -> List[dict]:
    """Apply a country's rule to a single event with a given election choice.

    `fx(amount, from_cur, to_cur, date)` converts between currencies at
    the event date (see `rates_converter`); needed only for exchanges
    whose legs are listed in different currencies.

    `election_key='ignore'` is universal — returns an empty row list,
    skipping the country/event-type rule entirely. Otherwise raises
    KeyError on unknown keys so callers can't silently emit wrong rows.
    """
    if election_key == IGNORE_ELECTION[0]:
        return []
    country = _canon(country)
    rule = RULES_BY_COUNTRY[country][event.action_type]
    valid = {key for key, _ in rule.options}
    if election_key not in valid:
        raise KeyError(
            f"unknown election '{election_key}' for {country}/{event.action_type}; "
            f"valid: {sorted(valid | {IGNORE_ELECTION[0]})}"
        )
    check_hints(event, election_key, hints)
    hints = dict(hints or {})
    if fx is not None:
        hints[FX_HINT] = fx
    rows = rule.apply(event, election_key, hints)
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
    rule = RULES_BY_COUNTRY[_canon(country)].get(action_type)
    base = list(rule.options) if rule else []
    return base + [IGNORE_ELECTION]


# --- Currency conversion for cross-currency exchanges -----------------------


def rates_converter(rates_path: Optional[Path], base_currency: str
                    ) -> Optional[Callable[[float, str, str, str],
                                           Optional[float]]]:
    """A converter `fx(amount, from_cur, to_cur, date)` over the
    project's rates file (the one the conversion stage uses: one
    `<src> -> base` series per source currency). Cross rates go through
    the base currency; a date with no rate takes the latest one within
    the previous 6 days (the conversion stage's own lookback). Returns
    None when no rates file is given."""
    if not rates_path:
        return None
    from datetime import datetime as _dt, timedelta as _td
    from taxjson.bin.taxjson_convert_currency import load_exchange_rates
    base = (base_currency or '').upper()
    history = load_exchange_rates(Path(rates_path), base or None)

    def _to_base(cur: str, date: str) -> Optional[float]:
        if cur == base:
            return 1.0
        series = history.get(cur) or {}
        try:
            d = _dt.strptime(date[:10], '%Y-%m-%d')
        except ValueError:
            return None
        for i in range(6):
            r = series.get((d - _td(days=i)).strftime('%Y-%m-%d'))
            if r is not None:
                return float(r)
        return None

    def fx(amount: float, from_cur: str, to_cur: str,
           date: str) -> Optional[float]:
        a, b = _to_base(from_cur.upper(), date), _to_base(to_cur.upper(),
                                                           date)
        if a is None or b is None or not b:
            return None
        return amount * a / b
    return fx


# --- One event held in several broker accounts ------------------------------


def _same_quantities(a: CorporateAction, b: CorporateAction) -> bool:
    return (abs(a.qty_disposed - b.qty_disposed) < 1e-9
            and abs(a.qty_received - b.qty_received) < 1e-9)


def combine_broker_copies(events: List[CorporateAction], *,
                          stream=None) -> List[CorporateAction]:
    """One corporate event per event id, covering every broker account.

    The event id is the corporate event (date, type, securities, ratio,
    taxjson account) — deliberately not the broker account, so ONE
    election covers it everywhere. But its quantities are per broker
    account: copies of an id are therefore
      * an OVERLAPPING statement of one broker account (the same broker
        account, or no account known and identical quantities) — the
        same holding reported twice: kept once;
      * a SECOND broker account holding the security (a different
        broker account, or no account known and different quantities) —
        its shares, values and cash-in-lieu are ADDED, so the emitted
        rows cover the whole taxjson account.
    Emitting each id once used to drop the second broker account's
    disposition (or deemed dividend) silently and leave its old shares
    in inventory (2026-09 audit). Copies in different currencies cannot
    be summed and stay separate events (same id, same election)."""
    import dataclasses
    order: List[str] = []
    groups: Dict[str, List[CorporateAction]] = {}
    for ev in events:
        if ev.event_id not in groups:
            order.append(ev.event_id)
        groups.setdefault(ev.event_id, []).append(ev)
    out: List[CorporateAction] = []
    for eid in order:
        holdings: List[CorporateAction] = []     # one per broker account
        seen: List[CorporateAction] = []         # every copy placed
        # Copies that name their broker account first, then the ones
        # that do not: matching an unnamed copy against whichever copy
        # happened to come first made the result depend on input order
        # (A2-0977).
        copies = ([ev for ev in groups[eid] if ev.broker_account]
                  + [ev for ev in groups[eid] if not ev.broker_account])
        for ev in copies:
            if ev.broker_account:
                dup = next((h for h in holdings
                            if h.broker_account == ev.broker_account),
                           None)
                if dup is not None and not _same_quantities(ev, dup):
                    # One broker account's statements disagree on the
                    # event (a correction one of them lacks): keep the
                    # larger copy, whatever the order, and say so.
                    keep = max((dup, ev), key=lambda e: (
                        e.qty_disposed, e.qty_received, e.fmv,
                        e.target_fmv))
                    print(f"warning: corp-action {eid}: two statements "
                          f"of broker account "
                          f"{_mask_account(ev.broker_account)} disagree "
                          f"on its quantities ({dup.qty_disposed:g}->"
                          f"{dup.qty_received:g} vs {ev.qty_disposed:g}->"
                          f"{ev.qty_received:g}); kept "
                          f"{keep.qty_disposed:g}->{keep.qty_received:g}. "
                          f"Check which statement is current.",
                          file=stream or sys.stderr)
                    holdings[holdings.index(dup)] = keep
            else:
                dup = next((h for h in seen if _same_quantities(ev, h)),
                           None)
            seen.append(ev)
            if dup is None:
                holdings.append(ev)
        if len(holdings) == 1:
            out.append(holdings[0])
            continue
        merged: List[CorporateAction] = []
        for h in holdings:
            tgt = next((m for m in merged
                        if m.currency == h.currency
                        and m.target_currency == h.target_currency
                        and (m.target_fmv_currency or m.target_currency)
                        == (h.target_fmv_currency or h.target_currency)
                        and (m.cash_in_lieu_currency or '')
                        in ('', h.cash_in_lieu_currency or '')), None)
            if tgt is None:
                merged.append(h)
                continue
            merged[merged.index(tgt)] = dataclasses.replace(
                tgt,
                qty_disposed=tgt.qty_disposed + h.qty_disposed,
                qty_received=tgt.qty_received + h.qty_received,
                fmv=tgt.fmv + h.fmv,
                target_fmv=tgt.target_fmv + h.target_fmv,
                cash_in_lieu=tgt.cash_in_lieu + h.cash_in_lieu,
                cash_in_lieu_currency=(tgt.cash_in_lieu_currency
                                       or h.cash_in_lieu_currency),
                raw_descriptions=(list(tgt.raw_descriptions)
                                  + list(h.raw_descriptions)),
                broker_account='+'.join(
                    x for x in (tgt.broker_account, h.broker_account) if x),
                event_id=tgt.event_id)
        accts = ', '.join(_mask_account(h.broker_account) or '(unnamed)'
                          for h in holdings)
        print(f"note: corp-action {eid} is held in {len(holdings)} broker "
              f"accounts ({accts}) of taxjson account "
              f"{holdings[0].account!r}; their quantities are combined "
              f"(one election covers all of them).",
              file=stream or sys.stderr)
        out.extend(merged)
    return out


def _mask_account(acct: str) -> str:
    """Broker account ids are private: first 2 characters + ***."""
    return '+'.join((a[:2] + '***') if a else '' for a in
                    (acct or '').split('+')) if acct else ''
